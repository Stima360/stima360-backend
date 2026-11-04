"""FIRMA OTP STIMA360 - il procedimento di sottoscrizione elettronica SEMPLICE del foglio visita.

    L'agente         crea la richiesta dal foglio (PDF gia' congelato e con impronta): nasce il collegamento personale del
                     cliente, parte l'email «Foglio visita da firmare». L'agente firma dal CRM, autenticato, riconfermando
                     la password e accettando la stessa dichiarazione.
    Il cliente       apre il collegamento: legge e scarica il PDF, accetta la dichiarazione, chiede il codice OTP (email),
                     lo inserisce. La conferma e' UNA sola: lock di riga sulla richiesta, sfida consumata, firmatario
                     segnato, prove scritte nella stessa transazione.
    Il CRM           quando tutti hanno firmato produce il documento sottoscritto (originale + attestazione) e il rapporto
                     delle prove, li archivia con le impronte e chiude la richiesta come `signed` (livello `ses`).

Ogni passo lascia una voce nel registro probatorio concatenato (`esign.evidence`). Codici e token non compaiono mai nei
log ne' nelle prove: solo impronte, esiti e identificativi. Le regole (scadenze, tentativi, limiti) stanno in
`otp_security.DEFAULTS` e sono configurabili per ambiente.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from communication.enums import REASON_SIGNATURE_LINK, REASON_SIGNATURE_OTP
from core.database import core_cursor
from operator_auth.context import SystemAgencyContext
from operator_auth.security import verify_password

from . import attest_pdf, consent, emails, evidence, otp_security, providers, report_pdf
from . import service as _s

logger = logging.getLogger(__name__)
ROMA = ZoneInfo("Europe/Rome")
PROVIDER = providers.OTP_PROVIDER
METHOD_VISITOR = "Collegamento personale via email + codice OTP via email"
METHOD_AGENT = "Sessione CRM autenticata + password riverificata"
STATE_LABELS = {
    "ready": "Pronto per firma", "sending": "Invio in corso", "sent": "Inviato", "awaiting_otp": "In attesa di OTP",
    "awaiting_agent": "In attesa della firma agente", "completed": "Completato", "expired": "Scaduto",
    "declined": "Rifiutato", "revoked": "Revocato", "error": "Errore di invio",
}
PUBLIC_STATES = ("ready", "otp_sent", "signed", "completed", "declined", "expired", "revoked", "link_expired", "link_revoked")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _procedure_code() -> str:
    return "S360-" + base64.b32encode(secrets.token_bytes(8)).decode().rstrip("=")


def _title(sheet: dict) -> str:
    return f"Foglio visita n. {sheet['appointment_id']} (versione {sheet['sheet_version']})"


def _mask(email: str) -> str:
    nome, _, dominio = email.partition("@")
    return f"{nome[:1]}***@{dominio}" if dominio else "***"


def _agency_name(cur, agency_id: int) -> str:
    cur.execute("SELECT COALESCE(NULLIF(btrim(settings->'report_profile'->>'display_name'), ''), name) AS n "
                "FROM agencies WHERE id = %s", (agency_id,))
    row = cur.fetchone()
    return row["n"] if row else "Agenzia"


def _identity(agency_id: int) -> dict:
    from agency_profile import service as agency_profile
    return agency_profile.report_identity(SystemAgencyContext(agency_id=agency_id, origin=emails.ORIGIN))


def _sheet_full(cur, sheet_id: int) -> dict:
    cur.execute("SELECT id, appointment_id, property_id, sheet_version, sha256, pdf_bytes, data_snapshot FROM visit_sheets "
                "WHERE id = %s", (sheet_id,))
    return dict(cur.fetchone())


def _agent_signer(cur, agency_id: int, user_id: int | None) -> dict:
    if user_id is None:
        raise _s.SignatureError("NO_AGENT", "La visita non ha un agente assegnato: l'agente firma insieme al cliente.")
    cur.execute("SELECT btrim(concat_ws(' ', first_name, last_name)) AS name, email FROM operator_users u "
                "JOIN agency_memberships m ON m.operator_user_id = u.id AND m.agency_id = %s AND m.status = 'active' "
                "WHERE u.id = %s AND u.status = 'active'", (agency_id, user_id))
    row = cur.fetchone()
    if row is None or not otp_security.is_valid_email(row["email"] or ""):
        raise _s.SignatureError("NO_AGENT", "L'agente assegnato non è attivo o non ha un indirizzo email valido.")
    return {"user_id": user_id, "name": row["name"] or row["email"], "email": row["email"].strip()}


def _signers(cur, request_id: int, *, lock: bool = False) -> list[dict]:
    cur.execute("SELECT id, request_id, role, name, email, status, signed_at, operator_user_id FROM signature_request_signers "
                "WHERE request_id = %s ORDER BY signer_order, id" + (" FOR UPDATE" if lock else ""), (request_id,))
    return [dict(r) for r in cur.fetchall()]


def _signer(signers: list[dict], role: str) -> dict | None:
    return next((s for s in signers if s["role"] == role), None)


def _ev(cur, row: dict, event_type: str, *, actor_kind: str, actor_ref: str | None = None, signer_id: int | None = None,
        details: dict | None = None) -> dict:
    return evidence.append(cur, request_id=row["id"], agency_id=row["agency_id"], event_type=event_type,
                           actor_kind=actor_kind, actor_ref=actor_ref, signer_id=signer_id, details=details)


def _context(ip: str | None, ua: str | None) -> dict:
    return {"ip_hash": otp_security.ip_hash(ip), "user_agent": otp_security.user_agent_short(ua)}


# ---------------------------------------------------------------------------------------------------------------------
# collegamenti e codici
# ---------------------------------------------------------------------------------------------------------------------

def _invalidate_open(cur, signer_id: int, kind: str, reason: str) -> list[int]:
    cur.execute("UPDATE signature_challenges SET invalidated_at = NOW(), invalidation_reason = %s WHERE signer_id = %s "
                "AND kind = %s AND consumed_at IS NULL AND invalidated_at IS NULL RETURNING id", (reason, signer_id, kind))
    return [r["id"] for r in cur.fetchall()]


def _issue_link(cur, row: dict, signer: dict, *, actor_kind: str, actor_ref: str | None, ip=None, ua=None) -> tuple[str, dict]:
    """Un collegamento nuovo per il firmatario; quelli precedenti si chiudono. Restituisce (token in chiaro, sfida)."""
    revoked = _invalidate_open(cur, signer["id"], "access", "superseded")
    for cid in revoked:
        _ev(cur, row, "link_revoked", actor_kind=actor_kind, actor_ref=actor_ref, signer_id=signer["id"],
            details={"challenge_id": cid, "reason": "superseded"})
    raw = otp_security.new_access_token()
    ctx = _context(ip, ua)
    cur.execute("INSERT INTO signature_challenges (request_id, signer_id, agency_id, kind, secret_hash, expires_at, "
                "max_attempts, requester_ip_hash, requester_user_agent) VALUES (%s, %s, %s, 'access', %s, %s, 1, %s, %s) "
                "RETURNING id, expires_at", (row["id"], signer["id"], row["agency_id"], otp_security.hash_access_token(raw),
                                             row["expires_at"], ctx["ip_hash"], ctx["user_agent"]))
    challenge = dict(cur.fetchone())
    _ev(cur, row, "link_issued", actor_kind=actor_kind, actor_ref=actor_ref, signer_id=signer["id"],
        details={"challenge_id": challenge["id"], "signer_role": signer["role"], "expires_at": row["expires_at"].isoformat()})
    return raw, challenge


def _send_link(cur, row: dict, sheet: dict, signer: dict, challenge: dict, raw_token: str, *, actor_kind, actor_ref) -> int:
    subject, body = emails.link_email(signer_name=signer["name"], agency_name=_agency_name(cur, row["agency_id"]),
                                      title=_title(sheet), url=emails.sign_url(raw_token), expires_at=row["expires_at"])
    contact_id = sheet.get("contact_id") or sheet_contact(cur, sheet)
    if contact_id is None:
        raise _s.SignatureError("NO_CONTACT", "La visita non ha un contatto nel CRM: l'email del collegamento non può essere registrata.")
    message_id = emails.enqueue(cur, agency_id=row["agency_id"], contact_id=contact_id,
                                property_id=sheet.get("property_id"), destination=signer["email"], subject=subject,
                                body_html=body, reason_code=REASON_SIGNATURE_LINK,
                                idempotency_key=f"{REASON_SIGNATURE_LINK}:{challenge['id']}",
                                metadata={"signature_request_id": row["id"], "challenge_id": challenge["id"],
                                          "procedure": row["provider_reference"]})
    cur.execute("UPDATE signature_challenges SET message_id = %s WHERE id = %s", (message_id, challenge["id"]))
    _ev(cur, row, "link_email_queued", actor_kind=actor_kind, actor_ref=actor_ref, signer_id=signer["id"],
        details={"challenge_id": challenge["id"], "message_id": message_id, "destination": signer["email"]})
    return message_id


def _link_challenges(cur, request_ids: list[int]) -> dict[int, dict]:
    """L'ultimo collegamento del cliente per richiesta, con lo stato dell'email."""
    cur.execute("SELECT DISTINCT ON (c.request_id) c.request_id, c.id, c.created_at, c.expires_at, c.consumed_at, "
                "c.invalidated_at, c.message_id, c.agency_id FROM signature_challenges c "
                "JOIN signature_request_signers s ON s.id = c.signer_id AND s.role = 'visitor' "
                "WHERE c.kind = 'access' AND c.request_id = ANY(%s) ORDER BY c.request_id, c.created_at DESC, c.id DESC",
                (request_ids,))
    return {r["request_id"]: dict(r) for r in cur.fetchall()}


def _otp_active(cur, request_ids: list[int]) -> dict[int, dict]:
    cur.execute("SELECT DISTINCT ON (request_id) request_id, id, expires_at, attempts, max_attempts FROM signature_challenges "
                "WHERE kind = 'otp' AND request_id = ANY(%s) AND consumed_at IS NULL AND invalidated_at IS NULL "
                "AND expires_at > NOW() ORDER BY request_id, created_at DESC", (request_ids,))
    return {r["request_id"]: dict(r) for r in cur.fetchall()}


def decorate(cur, views: list[dict]) -> None:
    """Lo stato operativo di ogni richiesta OTP, dai dati persistiti: mai da variabili del browser."""
    ids = [v["id"] for v in views]
    links = _link_challenges(cur, ids)
    otps = _otp_active(cur, ids)
    for v in views:
        v["procedure"] = v.get("provider_reference")
        visitor = next((s for s in v["signers"] if s["role"] == "visitor"), None)
        agent = next((s for s in v["signers"] if s["role"] == "agent"), None)
        link = links.get(v["id"])
        mail = emails.message_status(cur, link["agency_id"], link["message_id"]) if link else None
        v["link"] = None if link is None else {
            "issued_at": link["created_at"].isoformat(), "valid": link["invalidated_at"] is None and link["expires_at"] > _now(),
            "email": mail}
        otp = otps.get(v["id"])
        v["otp_pending"] = None if otp is None else {"expires_at": otp["expires_at"].isoformat(),
                                                     "attempts_left": otp["max_attempts"] - otp["attempts"]}
        v["can_agent_sign"] = bool(v["status"] == "sent" and agent and agent["status"] == "pending")
        v["can_resend"] = bool(v["status"] == "sent" and visitor and visitor["status"] == "pending")
        if v["status"] == "signed":
            state = "completed"
        elif v["status"] == "declined":
            state = "declined"
        elif v["status"] == "expired":
            state = "expired"
        elif v["status"] == "cancelled":
            state = "revoked"
        elif visitor and visitor["status"] == "signed":
            state = "awaiting_agent"
        elif otp is not None:
            state = "awaiting_otp"
        elif link is None:
            state = "ready"
        elif mail is None:
            state = "ready"
        elif mail["status"] in ("failed", "suppressed", "indeterminate", "cancelled"):
            state = "error"
        elif mail["status"] == "sent":
            state = "sent"
        else:
            state = "sending"
        v["state"], v["state_label"] = state, STATE_LABELS[state]
        v["level_notice"] = "Firma elettronica semplice (FES): non è una firma avanzata né qualificata."


# ---------------------------------------------------------------------------------------------------------------------
# l'agente: creazione, reinvio, firma dell'agente, prove
# ---------------------------------------------------------------------------------------------------------------------

def _require_ready(cur) -> None:
    _s._installed(cur)
    if not _s._signers_installed(cur) or not _s._env_installed(cur) or not _s._otp_installed(cur):
        raise _s.Unavailable("NOT_INSTALLED", "La firma OTP richiede le migrazioni 125, 128 e 129.")


def create(ctx, *, visit_sheet_id: int, signer_name: str, signer_email: str, expires_in_days: int, idempotency_key: str,
           include_agent: bool = True) -> dict:
    agency_id = ctx.require_agency()
    try:
        otp_security.require_configuration()
    except otp_security.NotConfigured as exc:
        raise _s.Unavailable("OTP_NOT_CONFIGURED", f"Firma OTP non configurata: {exc}") from exc
    signer_name = (signer_name or "").strip()
    signer_email = (signer_email or "").strip()
    if not signer_name:
        raise _s.SignatureError("SIGNER_NAME_REQUIRED", "Indica il nome del cliente che firma.")
    if not otp_security.is_valid_email(signer_email):
        raise _s.SignatureError("SIGNER_EMAIL_REQUIRED", "Serve l'indirizzo email del cliente: riceverà il collegamento e il codice.")
    with core_cursor(commit=True) as (_, cur):
        _require_ready(cur)
        existing = _s._by_key(cur, agency_id, idempotency_key)
        if existing is not None:
            return {"request": _s._with_signers(cur, [existing])[0], "created": False}
        sheet = _s._visible_sheet(cur, ctx, visit_sheet_id)
        cur.execute("SELECT id FROM visit_sheets WHERE id = %s FOR UPDATE", (sheet["id"],))     # una richiesta alla volta per foglio
        if _s._open_for_sheet(cur, agency_id, sheet["id"]):
            raise _s.Conflict("REQUEST_OPEN", "Per questo foglio c'è già una richiesta di firma aperta: revocala prima.")
        agent = _agent_signer(cur, agency_id, sheet["assigned_user_id"]) if include_agent else None
        if agent and agent["email"].lower() == signer_email.lower():
            raise _s.Conflict("SAME_SIGNER", "Cliente e agente devono avere indirizzi email diversi.")
        document = bytes(sheet["pdf_bytes"])
        if hashlib.sha256(document).hexdigest() != sheet["sha256"]:
            raise _s.SignatureError("SHEET_INTEGRITY", "Il foglio visita non corrisponde alla sua impronta.")
        expires_at = _now() + timedelta(days=expires_in_days)
        cur.execute("INSERT INTO signature_requests (agency_id, visit_sheet_id, provider, provider_reference, signer_name, "
                    "signer_contact, status, expires_at, idempotency_key, document_sha256, created_by_user_id, provider_environment) "
                    "VALUES (%s, %s, %s, %s, %s, %s, 'sent', %s, %s, %s, %s, 'production') "
                    f"ON CONFLICT (agency_id, idempotency_key) DO NOTHING RETURNING {_s.COLUMNS}",
                    (agency_id, sheet["id"], PROVIDER, _procedure_code(), signer_name, signer_email, expires_at,
                     idempotency_key, sheet["sha256"], ctx.user_id))
        row = cur.fetchone()
        if row is None:
            return {"request": _s._with_signers(cur, [_s._by_key(cur, agency_id, idempotency_key)])[0], "created": False}
        row = dict(row)
        _s._event(cur, row, None, "sent", f"operator:{ctx.user_id}", {"provider": PROVIDER, "environment": "production"})
        cur.execute("INSERT INTO signature_request_signers (request_id, agency_id, role, signer_order, name, email) "
                    "VALUES (%s, %s, 'visitor', 1, %s, %s) RETURNING id, role, name, email, status",
                    (row["id"], agency_id, signer_name, signer_email))
        visitor = dict(cur.fetchone())
        if agent:
            cur.execute("INSERT INTO signature_request_signers (request_id, agency_id, role, signer_order, name, email, "
                        "operator_user_id) VALUES (%s, %s, 'agent', 2, %s, %s, %s)",
                        (row["id"], agency_id, agent["name"], agent["email"], agent["user_id"]))
        actor = f"operator:{ctx.user_id}"
        _ev(cur, row, "request_created", actor_kind="operator", actor_ref=actor, details={
            "procedure": row["provider_reference"], "visit_sheet_id": sheet["id"], "sheet_version": sheet["sheet_version"],
            "appointment_id": sheet["appointment_id"], "property_id": sheet["property_id"],
            "document_sha256": sheet["sha256"], "consent_version": consent.VERSION,
            "expires_at": expires_at.isoformat(), "agent_included": bool(agent), "signature_level": "ses"})
        raw, challenge = _issue_link(cur, row, visitor, actor_kind="operator", actor_ref=actor)
        _send_link(cur, row, sheet, visitor, challenge, raw, actor_kind="operator", actor_ref=actor)
        view = _s._with_signers(cur, [row])[0]
    emails.dispatch_now(agency_id)
    with core_cursor() as (_, cur):                       # lo stato dell'email dopo il giro di invio
        view = _s._with_signers(cur, [row])[0]
    return {"request": view, "created": True}


def _visitor_open(cur, ctx, request_id: int) -> tuple[dict, list[dict], dict]:
    agency_id = ctx.require_agency()
    row = _s._lock(cur, agency_id, request_id)
    if row is None:
        raise _s.NotFound("REQUEST_NOT_FOUND", "Richiesta di firma non trovata.")
    _s._visible_sheet(cur, ctx, row["visit_sheet_id"])
    row = _s._expire_if_due(cur, row)
    if row["provider"] != PROVIDER:
        raise _s.Conflict("NOT_OTP", "Questa richiesta non è una firma OTP.")
    if row["status"] != "sent":
        raise _s.Conflict("REQUEST_CLOSED", f"La richiesta è già chiusa: {_s.STATUS_LABELS[row['status']]}.")
    signers = _signers(cur, row["id"], lock=True)
    return row, signers, _sheet_full(cur, row["visit_sheet_id"])


def issue_link(ctx, request_id: int, *, send: bool = True) -> dict:
    """«Reinvia link» (email) o «Copia link» (il collegamento, una volta sola, senza email). Il precedente si chiude."""
    agency_id = ctx.require_agency()
    with core_cursor(commit=True) as (_, cur):
        _require_ready(cur)
        row, signers, sheet = _visitor_open(cur, ctx, request_id)
        visitor = _signer(signers, "visitor")
        if visitor is None or visitor["status"] != "pending":
            raise _s.Conflict("VISITOR_DONE", "Il cliente ha già firmato o rifiutato.")
        cur.execute("SELECT count(*) AS n FROM signature_challenges WHERE request_id = %s AND kind = 'access' "
                    "AND created_at > NOW() - interval '1 hour'", (row["id"],))
        if cur.fetchone()["n"] >= otp_security.setting("ESIGN_LINK_MAX_PER_HOUR_REQUEST"):
            raise _s.TooMany("LINK_RATE_LIMITED", "Troppi collegamenti generati nell'ultima ora: riprova più tardi.")
        actor = f"operator:{ctx.user_id}"
        raw, challenge = _issue_link(cur, row, visitor, actor_kind="operator", actor_ref=actor)
        if send:
            _send_link(cur, row, sheet, visitor, challenge, raw, actor_kind="operator", actor_ref=actor)
        view = _s._with_signers(cur, [row])[0]
    if send:
        emails.dispatch_now(agency_id)
        with core_cursor() as (_, cur):
            view = _s._with_signers(cur, [row])[0]
        return {"request": view, "sent": True}
    return {"request": view, "sent": False, "url": emails.sign_url(raw)}


def sign_as_agent(ctx, request_id: int, *, password: str, consent_accepted: bool, consent_version: str,
                  ip: str | None = None, user_agent: str | None = None) -> dict:
    """La sottoscrizione dell'agente: SOLO l'agente assegnato, dalla sua sessione, con la password riverificata e la
    dichiarazione accettata. Identita', volonta' ed evidenze proprie: mai attribuita per effetto della firma del cliente."""
    agency_id = ctx.require_agency()
    if ctx.user_id is None:
        raise _s.SignatureError("SESSION_REQUIRED", "Serve una sessione operatore.")
    if consent_accepted is not True or consent_version != consent.VERSION:
        raise _s.SignatureError("CONSENT_REQUIRED", "Per firmare devi accettare la dichiarazione di sottoscrizione.")
    if not isinstance(password, str) or not password:
        raise _s.Forbidden("PASSWORD_INVALID", "Password non valida.")
    failure = None
    with core_cursor(commit=True) as (_, cur):
        _require_ready(cur)
        row, signers, sheet = _visitor_open(cur, ctx, request_id)
        agent = _signer(signers, "agent")
        if agent is None:
            raise _s.Conflict("NO_AGENT_SIGNER", "Questa richiesta non prevede la firma dell'agente.")
        if agent["operator_user_id"] != ctx.user_id:
            raise _s.Forbidden("NOT_THE_AGENT", "Solo l'agente assegnato alla visita può firmare come agente.")
        if agent["status"] != "pending":
            raise _s.Conflict("AGENT_DONE", "L'agente ha già firmato.")
        cur.execute("SELECT password_hash FROM operator_users WHERE id = %s AND status = 'active'", (ctx.user_id,))
        stored = cur.fetchone()
        actor = f"operator:{ctx.user_id}"
        if stored is None or not verify_password(password, stored["password_hash"]):
            _ev(cur, row, "agent_auth_failed", actor_kind="operator", actor_ref=actor, signer_id=agent["id"],
                details={**_context(ip, user_agent)})
            failure = _s.Forbidden("PASSWORD_INVALID", "Password non valida.")
        else:
            now = _now()
            _ev(cur, row, "agent_authenticated", actor_kind="operator", actor_ref=actor, signer_id=agent["id"],
                details={"method": "crm_session+password", **_context(ip, user_agent)})
            declaration = consent.text(title=_title(sheet), sha256=sheet["sha256"])
            _ev(cur, row, "consent_recorded", actor_kind="operator", actor_ref=actor, signer_id=agent["id"],
                details={"consent_version": consent.VERSION, "consent_sha256": consent.digest(declaration)})
            cur.execute("UPDATE signature_request_signers SET status = 'signed', signed_at = %s, updated_at = NOW() WHERE id = %s",
                        (now, agent["id"]))
            _ev(cur, row, "signer_signed", actor_kind="operator", actor_ref=actor, signer_id=agent["id"],
                details={"signer_role": "agent", "method": "crm_session+password", "signed_at": now.isoformat()})
            row = _complete_if_done(cur, row, sheet)
            view = _s._with_signers(cur, [row])[0]
    if failure is not None:
        raise failure
    return {"request": view}


def events(ctx, request_id: int) -> dict:
    with core_cursor() as (_, cur):
        _require_ready(cur)
        row = _s._request_of(ctx, cur, request_id)
        rows = evidence.entries(cur, row["id"])
        check = evidence.verify(cur, row["id"])
    return {"items": [evidence.public_view(r) for r in rows], "integrity": check}


def verify_integrity(ctx, request_id: int) -> dict:
    """Catena e sigilli delle prove, impronte dei documenti archiviati e del foglio originale."""
    with core_cursor() as (_, cur):
        _require_ready(cur)
        row = _s._request_of(ctx, cur, request_id)
        chain = evidence.verify(cur, row["id"])
        cur.execute("SELECT document_sha256, received_document, received_document_sha256, audit_trail, "
                    "audit_trail_sha256, visit_sheet_id FROM signature_requests WHERE id = %s", (row["id"],))
        r = cur.fetchone()
        cur.execute("SELECT pdf_bytes, sha256 FROM visit_sheets WHERE id = %s", (r["visit_sheet_id"],))
        sheet = cur.fetchone()
    checks = [{"name": "catena delle prove", "ok": chain["ok"], "detail": "; ".join(chain["problems"]) or f"{chain['count']} voci"}]
    sheet_ok = hashlib.sha256(bytes(sheet["pdf_bytes"])).hexdigest() == sheet["sha256"] == r["document_sha256"]
    checks.append({"name": "foglio originale", "ok": sheet_ok, "detail": r["document_sha256"]})
    if r["received_document"] is not None:
        ok = hashlib.sha256(bytes(r["received_document"])).hexdigest() == r["received_document_sha256"]
        checks.append({"name": "documento sottoscritto", "ok": ok, "detail": r["received_document_sha256"]})
    if r["audit_trail"] is not None:
        ok = hashlib.sha256(bytes(r["audit_trail"])).hexdigest() == r["audit_trail_sha256"]
        checks.append({"name": "rapporto delle prove", "ok": ok, "detail": r["audit_trail_sha256"]})
    return {"ok": all(c["ok"] for c in checks), "checks": checks, "chain_head": chain["head"]}


def on_expired(cur, row: dict) -> None:
    _ev(cur, row, "request_expired", actor_kind="system", details={"expires_at": row["expires_at"].isoformat()})


def on_cancelled(cur, row: dict, *, actor_user_id) -> None:
    _ev(cur, row, "request_cancelled", actor_kind="operator", actor_ref=f"operator:{actor_user_id}")


# ---------------------------------------------------------------------------------------------------------------------
# completamento: documento sottoscritto e rapporto delle prove
# ---------------------------------------------------------------------------------------------------------------------

def _report_data(cur, row: dict, sheet: dict, signers: list[dict], *, outcome: str, signed_sha256: str | None) -> dict:
    snap = sheet.get("data_snapshot") or {}
    prop, visit = snap.get("property") or {}, snap.get("visit") or {}
    rows = evidence.entries(cur, row["id"])
    declaration = consent.text(title=_title(sheet), sha256=sheet["sha256"])
    return {
        "procedure": row["provider_reference"], "request_id": row["id"], "generated_at": _now(),
        "property": " · ".join(x for x in (prop.get("code"), prop.get("address")) if x) or f"id {sheet['property_id']}",
        "visit": f"Appuntamento {sheet['appointment_id']} · {visit.get('date_text') or ''} {visit.get('time_text') or ''}".strip(),
        "sheet": f"{_title(sheet)} · foglio id {sheet['id']}", "outcome": outcome,
        "original_sha256": sheet["sha256"], "signed_sha256": signed_sha256,
        "signers": [{"role_label": _s.SIGNER_LABELS[s["role"]], "name": s["name"], "email": s["email"],
                     "method": METHOD_VISITOR if s["role"] == "visitor" else METHOD_AGENT,
                     "status_label": _s.SIGNER_STATUS_LABELS[s["status"]], "signed_at": s["signed_at"]} for s in signers],
        "consent": {"version": consent.VERSION, "text": declaration, "sha256": consent.digest(declaration)},
        "events": rows, "chain_head": rows[-1]["entry_hash"] if rows else None,
    }


def _complete_if_done(cur, row: dict, sheet: dict) -> dict:
    signers = _signers(cur, row["id"])
    if not signers or any(s["status"] != "signed" for s in signers):
        return row
    now = _now()
    _ev(cur, row, "signature_completed", actor_kind="system", details={"signed_at": now.isoformat(), "signature_level": "ses"})
    identity = _identity(row["agency_id"])
    info = {"procedure": row["provider_reference"], "title": _title(sheet), "original_sha256": sheet["sha256"],
            "signers": [{"role": s["role"], "role_label": _s.SIGNER_LABELS[s["role"]], "name": s["name"], "status": s["status"],
                         "signed_at": s["signed_at"], "procedure": row["provider_reference"],
                         "method": METHOD_VISITOR if s["role"] == "visitor" else METHOD_AGENT} for s in signers]}
    signed_pdf = attest_pdf.build(bytes(sheet["pdf_bytes"]), identity=identity, info=info)
    signed_sha = hashlib.sha256(signed_pdf).hexdigest()
    report = report_pdf.build(identity=identity, data=_report_data(cur, row, sheet, signers, outcome="Firmato da tutti i firmatari",
                                                                   signed_sha256=signed_sha))
    report_sha = hashlib.sha256(report).hexdigest()
    nuova = _s._transition(cur, row, "signed", "system:otp", document=signed_pdf,
                           details={"procedure": row["provider_reference"], "signature_level": "ses"},
                           extra={"signature_level": "ses", "signed_at": now, "audit_trail": report, "audit_trail_sha256": report_sha})
    _ev(cur, nuova, "documents_archived", actor_kind="system",
        details={"signed_document_sha256": signed_sha, "evidence_report_sha256": report_sha})
    return nuova


# ---------------------------------------------------------------------------------------------------------------------
# il cliente: pagina pubblica (token personale)
# ---------------------------------------------------------------------------------------------------------------------

def _resolve(cur, token: str, *, lock: bool) -> tuple[dict, dict] | None:
    """(sfida di accesso, richiesta) per il token, o None se il token non esiste. Non giudica validita' o scadenza."""
    if not otp_security.looks_like_access_token(token):
        return None
    cur.execute("SELECT id, request_id, signer_id, agency_id, expires_at, consumed_at, invalidated_at FROM signature_challenges "
                "WHERE kind = 'access' AND secret_hash = %s", (otp_security.hash_access_token(token),))
    challenge = cur.fetchone()
    if challenge is None:
        return None
    challenge = dict(challenge)
    if lock:
        row = _s._lock(cur, challenge["agency_id"], challenge["request_id"])
    else:
        cur.execute(f"SELECT {_s.COLUMNS} FROM signature_requests WHERE id = %s", (challenge["request_id"],))
        row = cur.fetchone()
        row = dict(row) if row else None
    if row is None:
        return None
    if lock:
        row = _s._expire_if_due(cur, row)
    return challenge, row


def _public_state(cur, challenge: dict, row: dict) -> dict:
    signers = _signers(cur, row["id"])
    me = next(s for s in signers if s["id"] == challenge["signer_id"])
    sheet = _sheet_full(cur, row["visit_sheet_id"])
    snap = sheet.get("data_snapshot") or {}
    prop, visit = snap.get("property") or {}, snap.get("visit") or {}
    declaration = consent.text(title=_title(sheet), sha256=sheet["sha256"])
    link_ok = challenge["invalidated_at"] is None and challenge["expires_at"] > _now()
    otp = _otp_active(cur, [row["id"]]).get(row["id"])
    if row["status"] == "signed":
        state = "completed"
    elif row["status"] == "declined":
        state = "declined"
    elif row["status"] == "expired":
        state = "expired"
    elif row["status"] == "cancelled":
        state = "revoked"
    elif challenge["invalidated_at"] is not None:
        state = "link_revoked"
    elif not link_ok:
        state = "link_expired"
    elif me["status"] == "signed":
        state = "signed"
    elif me["status"] == "declined":
        state = "declined"
    elif otp is not None:
        state = "otp_sent"
    else:
        state = "ready"
    cur.execute("SELECT created_at FROM signature_challenges WHERE signer_id = %s AND kind = 'otp' ORDER BY created_at DESC LIMIT 1",
                (me["id"],))
    last = cur.fetchone()
    resend_after = None
    if last:
        resend_after = (last["created_at"] + timedelta(seconds=otp_security.setting("ESIGN_OTP_RESEND_SECONDS"))).isoformat()
    return {
        "state": state, "state_label": {"ready": "Da firmare", "otp_sent": "Codice inviato", "signed": "Firma registrata",
                                        "completed": "Documento sottoscritto da tutte le parti", "declined": "Firma rifiutata",
                                        "expired": "Richiesta scaduta", "revoked": "Richiesta revocata dall'agenzia",
                                        "link_expired": "Collegamento scaduto", "link_revoked": "Collegamento non più valido"}[state],
        "procedure": row["provider_reference"], "agency_name": _agency_name(cur, row["agency_id"]),
        "document": {"title": _title(sheet), "version": sheet["sheet_version"], "sha256": sheet["sha256"]},
        "property": {"reference": prop.get("code"), "address": prop.get("address"), "type_label": prop.get("type_label")},
        "visit": {"date_text": visit.get("date_text"), "time_text": visit.get("time_text"), "agent_name": visit.get("agent_name")},
        "signer": {"role": me["role"], "name": me["name"], "email_masked": _mask(me["email"])},
        "consent": {"version": consent.VERSION, "text": declaration, "sha256": consent.digest(declaration),
                    "privacy_note": consent.PRIVACY_NOTE},
        "otp": None if otp is None else {"expires_at": otp["expires_at"].isoformat(), "attempts_left": otp["max_attempts"] - otp["attempts"],
                                         "ttl_minutes": otp_security.setting("ESIGN_OTP_TTL_MINUTES")},
        "resend_after": resend_after, "expires_at": row["expires_at"].isoformat(),
        "can_request_otp": state in ("ready", "otp_sent"), "can_confirm": state == "otp_sent",
        "can_decline": state in ("ready", "otp_sent"), "can_download_signed": state == "completed",
        "level_notice": "Firma elettronica semplice (FES) con conferma tramite codice OTP ricevuto via email.",
    }


def _already(cur, row: dict, event_type: str, challenge_id: int) -> bool:
    cur.execute("SELECT 1 FROM signature_evidence WHERE request_id = %s AND event_type = %s AND (details->>'challenge_id')::bigint = %s "
                "LIMIT 1", (row["id"], event_type, challenge_id))
    return cur.fetchone() is not None


def public_state(token: str, *, ip: str | None = None, user_agent: str | None = None) -> dict:
    """Lo stato per la pagina. La prima lettura di un collegamento valido lascia la traccia «collegamento aperto»
    (e' la pagina che lo chiede: non e' prova che una persona l'abbia letto)."""
    with core_cursor(commit=True) as (_, cur):
        _require_ready(cur)
        found = _resolve(cur, token, lock=True)
        if found is None:
            raise _s.NotFound("LINK_UNKNOWN", "Collegamento non valido.")
        challenge, row = found
        state = _public_state(cur, challenge, row)
        if state["state"] in ("ready", "otp_sent") and not _already(cur, row, "link_opened", challenge["id"]):
            _ev(cur, row, "link_opened", actor_kind="signer", signer_id=challenge["signer_id"],
                details={"challenge_id": challenge["id"], **_context(ip, user_agent)})
    return state


def public_pdf(token: str, *, ip: str | None = None, user_agent: str | None = None) -> dict:
    with core_cursor(commit=True) as (_, cur):
        _require_ready(cur)
        found = _resolve(cur, token, lock=True)
        if found is None:
            raise _s.NotFound("LINK_UNKNOWN", "Collegamento non valido.")
        challenge, row = found
        if challenge["invalidated_at"] is not None or challenge["expires_at"] <= _now():
            raise _s.NotFound("LINK_UNKNOWN", "Collegamento non valido.")
        sheet = _sheet_full(cur, row["visit_sheet_id"])
        if not _already(cur, row, "document_viewed", challenge["id"]):
            _ev(cur, row, "document_viewed", actor_kind="signer", signer_id=challenge["signer_id"],
                details={"challenge_id": challenge["id"], "document_sha256": sheet["sha256"], **_context(ip, user_agent)})
    data = bytes(sheet["pdf_bytes"])
    if hashlib.sha256(data).hexdigest() != sheet["sha256"]:
        raise _s.SignatureError("SHEET_INTEGRITY", "Il documento non corrisponde alla sua impronta.")
    return {"data": data, "filename": f"foglio-visita-{sheet['appointment_id']}-v{sheet['sheet_version']}.pdf"}


def public_signed_document(token: str) -> dict:
    with core_cursor() as (_, cur):
        _require_ready(cur)
        found = _resolve(cur, token, lock=False)
        if found is None:
            raise _s.NotFound("LINK_UNKNOWN", "Collegamento non valido.")
        challenge, row = found
        if challenge["invalidated_at"] is not None or challenge["expires_at"] <= _now() or row["status"] != "signed":
            raise _s.NotFound("NO_DOCUMENT", "Documento non disponibile.")
        cur.execute("SELECT received_document, received_document_sha256 FROM signature_requests WHERE id = %s", (row["id"],))
        r = cur.fetchone()
    data = bytes(r["received_document"])
    if hashlib.sha256(data).hexdigest() != r["received_document_sha256"]:
        raise _s.SignatureError("DOCUMENT_INTEGRITY", "Il documento non corrisponde alla sua impronta.")
    return {"data": data, "filename": f"foglio-visita-sottoscritto-{row['provider_reference']}.pdf"}


def _open_for_signer(cur, token: str) -> tuple[dict, dict, dict, dict]:
    found = _resolve(cur, token, lock=True)
    if found is None:
        raise _s.NotFound("LINK_UNKNOWN", "Collegamento non valido.")
    challenge, row = found
    if challenge["invalidated_at"] is not None:
        raise _s.Conflict("LINK_REVOKED", "Questo collegamento non è più valido: chiedi all'agenzia un nuovo invio.")
    if challenge["expires_at"] <= _now() or row["status"] == "expired":
        raise _s.Conflict("LINK_EXPIRED", "Il collegamento è scaduto: chiedi all'agenzia un nuovo invio.")
    if row["status"] != "sent":
        raise _s.Conflict("REQUEST_CLOSED", f"La richiesta è chiusa: {_s.STATUS_LABELS[row['status']]}.")
    signers = _signers(cur, row["id"], lock=True)
    me = next(s for s in signers if s["id"] == challenge["signer_id"])
    if me["status"] != "pending":
        raise _s.Conflict("ALREADY_DONE", "La tua firma è già stata registrata.")
    return challenge, row, me, _sheet_full(cur, row["visit_sheet_id"])


def public_request_otp(token: str, *, ip: str | None = None, user_agent: str | None = None) -> dict:
    """Il codice: generato, derivato con pepe e sale, mai conservato in chiaro; spedito all'email del firmatario."""
    try:
        otp_security.require_configuration()
    except otp_security.NotConfigured as exc:
        raise _s.Unavailable("OTP_NOT_CONFIGURED", "Firma OTP non configurata su questo servizio.") from exc
    with core_cursor(commit=True) as (_, cur):
        _require_ready(cur)
        challenge, row, me, sheet = _open_for_signer(cur, token)
        ctx = _context(ip, user_agent)
        cur.execute("SELECT created_at FROM signature_challenges WHERE signer_id = %s AND kind = 'otp' ORDER BY created_at DESC LIMIT 1",
                    (me["id"],))
        last = cur.fetchone()
        resend = otp_security.setting("ESIGN_OTP_RESEND_SECONDS")
        if last and last["created_at"] + timedelta(seconds=resend) > _now():
            raise _s.TooMany("OTP_TOO_SOON", f"Hai appena ricevuto un codice: attendi {resend} secondi prima di chiederne un altro.")
        cur.execute("SELECT count(*) AS n FROM signature_challenges WHERE signer_id = %s AND kind = 'otp' AND created_at > NOW() - interval '1 hour'",
                    (me["id"],))
        if cur.fetchone()["n"] >= otp_security.setting("ESIGN_OTP_MAX_PER_HOUR_SIGNER"):
            raise _s.TooMany("OTP_RATE_LIMITED", "Troppi codici richiesti nell'ultima ora: riprova più tardi.")
        if ctx["ip_hash"]:
            cur.execute("SELECT count(*) AS n FROM signature_challenges WHERE kind = 'otp' AND requester_ip_hash = %s "
                        "AND created_at > NOW() - interval '1 hour'", (ctx["ip_hash"],))
            if cur.fetchone()["n"] >= otp_security.setting("ESIGN_OTP_MAX_PER_HOUR_IP"):
                raise _s.TooMany("OTP_RATE_LIMITED", "Troppi codici richiesti da questa connessione: riprova più tardi.")
        for cid in _invalidate_open(cur, me["id"], "otp", "superseded"):
            _ev(cur, row, "otp_invalidated", actor_kind="system", signer_id=me["id"], details={"challenge_id": cid, "reason": "superseded"})
        code = otp_security.new_otp()
        salt = otp_security.new_salt()
        ttl = otp_security.setting("ESIGN_OTP_TTL_MINUTES")
        expires_at = _now() + timedelta(minutes=ttl)
        cur.execute("INSERT INTO signature_challenges (request_id, signer_id, agency_id, kind, secret_hash, expires_at, max_attempts, "
                    "requester_ip_hash, requester_user_agent) VALUES (%s, %s, %s, 'otp', %s, %s, %s, %s, %s) RETURNING id",
                    (row["id"], me["id"], row["agency_id"], salt + "$" + otp_security.hash_otp(code, challenge_salt=salt),
                     expires_at, otp_security.setting("ESIGN_OTP_MAX_ATTEMPTS"), ctx["ip_hash"], ctx["user_agent"]))
        otp_id = cur.fetchone()["id"]
        _ev(cur, row, "otp_requested", actor_kind="signer", signer_id=me["id"],
            details={"challenge_id": otp_id, "access_challenge_id": challenge["id"], "expires_at": expires_at.isoformat(), **ctx})
        subject, body = emails.otp_email(code=code, minutes=ttl, title=_title(sheet))
        message_id = emails.enqueue(cur, agency_id=row["agency_id"], contact_id=sheet_contact(cur, sheet), property_id=sheet["property_id"],
                                    destination=me["email"], subject=subject, body_html=body, reason_code=REASON_SIGNATURE_OTP,
                                    idempotency_key=f"{REASON_SIGNATURE_OTP}:{otp_id}",
                                    metadata={"signature_request_id": row["id"], "challenge_id": otp_id, "procedure": row["provider_reference"]})
        cur.execute("UPDATE signature_challenges SET message_id = %s WHERE id = %s", (message_id, otp_id))
        _ev(cur, row, "otp_email_queued", actor_kind="system", signer_id=me["id"],
            details={"challenge_id": otp_id, "message_id": message_id, "destination": me["email"]})
        del code                                               # mai oltre questa riga
        agency_id = row["agency_id"]
    emails.dispatch_now(agency_id)
    return public_state(token, ip=ip, user_agent=user_agent)


def sheet_contact(cur, sheet: dict) -> int | None:
    cur.execute("SELECT contact_id FROM appointments WHERE id = %s", (sheet["appointment_id"],))
    r = cur.fetchone()
    return r["contact_id"] if r else None


def public_confirm(token: str, *, code: str, consent_accepted: bool, consent_version: str, ip: str | None = None,
                   user_agent: str | None = None) -> dict:
    """La conferma: dichiarazione prima del codice; tentativi contati e persistiti anche quando il codice e' sbagliato;
    una sola firma per firmatario (lock di riga, sfida consumata)."""
    if consent_accepted is not True or consent_version != consent.VERSION:
        raise _s.SignatureError("CONSENT_REQUIRED", "Per firmare devi accettare la dichiarazione di sottoscrizione.")
    if not otp_security.looks_like_otp(code):
        raise _s.SignatureError("OTP_FORMAT", "Il codice è di 6 cifre.")
    failure = None
    with core_cursor(commit=True) as (_, cur):
        _require_ready(cur)
        challenge, row, me, sheet = _open_for_signer(cur, token)
        ctx = _context(ip, user_agent)
        cur.execute("SELECT id, secret_hash, expires_at, attempts, max_attempts FROM signature_challenges WHERE signer_id = %s "
                    "AND kind = 'otp' AND consumed_at IS NULL AND invalidated_at IS NULL ORDER BY created_at DESC LIMIT 1 FOR UPDATE",
                    (me["id"],))
        otp = cur.fetchone()
        if otp is None:
            raise _s.Conflict("OTP_MISSING", "Richiedi prima un codice OTP.")
        if otp["expires_at"] <= _now():
            cur.execute("UPDATE signature_challenges SET invalidated_at = NOW(), invalidation_reason = 'expired' WHERE id = %s", (otp["id"],))
            _ev(cur, row, "otp_invalidated", actor_kind="system", signer_id=me["id"], details={"challenge_id": otp["id"], "reason": "expired"})
            failure = _s.Conflict("OTP_EXPIRED", "Il codice è scaduto: richiedine uno nuovo.")
        else:
            cur.execute("UPDATE signature_challenges SET attempts = attempts + 1, last_attempt_at = NOW() WHERE id = %s RETURNING attempts",
                        (otp["id"],))
            attempts = cur.fetchone()["attempts"]
            salt, _, stored = otp["secret_hash"].partition("$")
            if otp_security.verify_otp(code, challenge_salt=salt, stored=stored):
                now = _now()
                cur.execute("UPDATE signature_challenges SET consumed_at = %s WHERE id = %s", (now, otp["id"]))
                _ev(cur, row, "otp_verified", actor_kind="signer", signer_id=me["id"],
                    details={"challenge_id": otp["id"], "attempts": attempts, **ctx})
                declaration = consent.text(title=_title(sheet), sha256=sheet["sha256"])
                _ev(cur, row, "consent_recorded", actor_kind="signer", signer_id=me["id"],
                    details={"consent_version": consent.VERSION, "consent_sha256": consent.digest(declaration),
                             "document_sha256": sheet["sha256"], **ctx})
                cur.execute("UPDATE signature_request_signers SET status = 'signed', signed_at = %s, updated_at = NOW() WHERE id = %s",
                            (now, me["id"]))
                _ev(cur, row, "signer_signed", actor_kind="signer", signer_id=me["id"],
                    details={"signer_role": me["role"], "method": "email_otp", "signed_at": now.isoformat(), "otp_challenge_id": otp["id"]})
                _complete_if_done(cur, row, sheet)
            else:
                left = otp["max_attempts"] - attempts
                _ev(cur, row, "otp_failed", actor_kind="signer", signer_id=me["id"],
                    details={"challenge_id": otp["id"], "attempts": attempts, "attempts_left": left, **ctx})
                if left <= 0:
                    cur.execute("UPDATE signature_challenges SET invalidated_at = NOW(), invalidation_reason = 'too_many_attempts' WHERE id = %s",
                                (otp["id"],))
                    _ev(cur, row, "otp_invalidated", actor_kind="system", signer_id=me["id"],
                        details={"challenge_id": otp["id"], "reason": "too_many_attempts"})
                    failure = _s.Conflict("OTP_LOCKED", "Troppi tentativi: il codice non è più valido. Richiedine uno nuovo.")
                else:
                    failure = _s.SignatureError("OTP_INVALID", f"Codice errato. Tentativi rimasti: {left}.")
    if failure is not None:
        raise failure
    return public_state(token, ip=ip, user_agent=user_agent)


def public_decline(token: str, *, reason: str | None = None, ip: str | None = None, user_agent: str | None = None) -> dict:
    with core_cursor(commit=True) as (_, cur):
        _require_ready(cur)
        challenge, row, me, sheet = _open_for_signer(cur, token)
        motivo = " ".join((reason or "").split())[:200] or None
        cur.execute("UPDATE signature_request_signers SET status = 'declined', updated_at = NOW() WHERE id = %s", (me["id"],))
        _ev(cur, row, "signer_declined", actor_kind="signer", signer_id=me["id"],
            details={"signer_role": me["role"], "reason": motivo, **_context(ip, user_agent)})
        _s._transition(cur, row, "declined", f"signer:{me['role']}", details={"procedure": row["provider_reference"]})
    return public_state(token, ip=ip, user_agent=user_agent)
