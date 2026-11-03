"""BLOCCO-6B - richieste di firma dei fogli visita: transizioni controllate.

    sent ──► simulated_completed   (solo adattatore simulato: NON e' una firma)
     │  ├──► signed                (solo un fornitore reale: oggi nessuno)
     │  ├──► declined
     │  ├──► expired               (scadenza superata: applicata alla lettura)
     │  └──► cancelled             (annullamento dell'agenzia)
     └ ogni stato diverso da `sent` e' finale.

Ogni transizione e' sotto lock di riga, condizionata allo stato atteso e
registrata in `signature_request_events`; il database rifiuta comunque
modifiche a una richiesta chiusa e la firma «signed» di una simulazione.
Idempotenza: la stessa chiave nella stessa agenzia restituisce la stessa
richiesta. Permessi: la visibilita' dell'Agenda sul foglio visita.
"""
from __future__ import annotations

import dataclasses
import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone

from psycopg2.extras import Json

from core.database import core_cursor
from visit_sheets import repository as sheets_repository
from visit_sheets import service as sheets_service

from . import providers
from .layout import locate_signature_boxes
from .pdf_text import PdfLayoutError

logger = logging.getLogger(__name__)
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SIGNER_LABELS = {"visitor": "Cliente", "agent": "Agente"}
SIGNER_STATUS_LABELS = {"pending": "Da firmare", "signed": "Ha firmato", "declined": "Ha rifiutato"}
#: Il livello dichiarato dal fornitore, in parole: «semplice» NON e' «avanzata» ne' «qualificata».
LEVEL_LABELS = {"ses": "Firma elettronica semplice", "aes": "Firma elettronica avanzata", "qes": "Firma elettronica qualificata"}

TERMINAL = ("signed", "simulated_completed", "declined", "expired", "cancelled")
STATUS_LABELS = {
    "sent": "In attesa del firmatario",
    "signed": "Firmata",
    "simulated_completed": "SIMULAZIONE completata — nessuna firma valida",
    "declined": "Rifiutata",
    "expired": "Scaduta",
    "cancelled": "Annullata",
}
SIMULATED_OUTCOMES = {"completed": "simulated_completed", "declined": "declined"}
COLUMNS = ("id, agency_id, visit_sheet_id, provider, provider_reference, signer_name, signer_contact, status, "
           "expires_at, idempotency_key, document_sha256, received_document_sha256, received_at, created_by_user_id, "
           "created_at, closed_at, "
           # BLOCCO-5 (125), letti senza rompere un database con la sola 122
           "COALESCE(to_jsonb(signature_requests)->>'provider_environment', "
           "CASE WHEN provider = 'simulated' THEN 'simulation' END) AS provider_environment, "
           "to_jsonb(signature_requests)->>'signature_level' AS signature_level, "
           "to_jsonb(signature_requests)->>'signed_at' AS signed_at, "
           "to_jsonb(signature_requests)->>'audit_trail_sha256' AS audit_trail_sha256")
ENVIRONMENT_LABELS = {"simulation": "Simulazione locale", "sandbox": "Ambiente di prova del fornitore",
                      "production": "Ambiente reale del fornitore"}


class SignatureError(Exception):
    status = 400

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class NotFound(SignatureError):
    status = 404


class Unavailable(SignatureError):
    status = 503


class Conflict(SignatureError):
    status = 409


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _installed(cur) -> None:
    cur.execute("SELECT to_regclass('public.signature_requests') IS NOT NULL AS presente")
    if not cur.fetchone()["presente"]:
        raise Unavailable("NOT_INSTALLED", "Le richieste di firma non sono installate (migrazione 122).")


def _view(row: dict) -> dict:
    out = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in row.items() if k != "idempotency_key"}
    out["status_label"] = STATUS_LABELS[row["status"]]
    out["simulation"] = row["provider"] == providers.SIMULATED
    env = row.get("provider_environment")
    out["environment_label"] = ENVIRONMENT_LABELS.get(env, env)
    # condizione TECNICA (non legale): firmata presso un fornitore reale, nel
    # suo ambiente reale. Una firma dell'ambiente di prova (sandbox) resta di
    # prova. La validita' legale dipende dal tipo di firma, dal fornitore e dal
    # documento: non la stabilisce questo campo (vedi BLOCCO-5_FIRMA_FORNITORI)
    out["valid_signature"] = (row["status"] == "signed" and row["provider"] != providers.SIMULATED
                              and env == "production")
    out["has_document"] = row.get("received_document_sha256") is not None
    out["signature_level_label"] = LEVEL_LABELS.get(row.get("signature_level") or "")
    out["has_audit_trail"] = row.get("audit_trail_sha256") is not None
    return out


def _signers_installed(cur) -> bool:
    cur.execute("SELECT to_regclass('public.signature_request_signers') IS NOT NULL AS presente")
    return bool(cur.fetchone()["presente"])


def _with_signers(cur, rows: list[dict]) -> list[dict]:
    """Le viste delle richieste con i firmatari (mai il collegamento di firma: quello ha una rotta sua)."""
    views = [_view(r) for r in rows]
    if not rows or not _signers_installed(cur):
        for v in views:
            v["signers"] = []
        return views
    cur.execute("SELECT request_id, role, name, email, status, signed_at, (signing_url IS NOT NULL) AS has_link "
                "FROM signature_request_signers WHERE request_id = ANY(%s) ORDER BY request_id, signer_order, id",
                ([r["id"] for r in rows],))
    by_request: dict[int, list] = {}
    for r in cur.fetchall():
        by_request.setdefault(r["request_id"], []).append({
            "role": r["role"], "role_label": SIGNER_LABELS[r["role"]], "name": r["name"], "email": r["email"],
            "status": r["status"], "status_label": SIGNER_STATUS_LABELS[r["status"]],
            "signed_at": r["signed_at"].isoformat() if r["signed_at"] else None, "has_link": r["has_link"]})
    for v in views:
        v["signers"] = by_request.get(v["id"], [])
    return views


def _visible_sheet(cur, ctx, sheet_id: int) -> dict:
    agency_id = ctx.require_agency()
    cur.execute("SELECT id, appointment_id, sheet_version, sha256, pdf_bytes FROM visit_sheets "
                "WHERE id = %s AND agency_id = %s", (sheet_id, agency_id))
    sheet = cur.fetchone()
    visit = sheets_repository.visit(cur, agency_id, sheet["appointment_id"]) if sheet else None
    if sheet is None or visit is None or not sheets_service._visible(ctx, visit):
        raise NotFound("SHEET_NOT_FOUND", "Foglio visita non trovato.")
    return {**dict(sheet), "assigned_user_id": visit.get("assigned_user_id")}


def _event(cur, row: dict, from_status, to_status, actor: str, details: dict | None = None) -> None:
    cur.execute("INSERT INTO signature_request_events (request_id, agency_id, from_status, to_status, actor, details) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (row["id"], row["agency_id"], from_status, to_status, actor, Json(details or {})))


def _transition(cur, row: dict, to_status: str, actor: str, *, document: bytes | None = None,
                details: dict | None = None, extra: dict | None = None) -> dict:
    """`extra`: colonne della 125 da scrivere NELLA STESSA istruzione (una
    richiesta chiusa non si modifica piu', e `signed` vuole data e livello)."""
    if row["status"] != "sent":
        raise Conflict("REQUEST_CLOSED", f"La richiesta è già chiusa: {STATUS_LABELS[row['status']]}.")
    if to_status == "signed" and row["provider"] == providers.SIMULATED:
        raise Conflict("SIMULATION_CANNOT_SIGN", "Una richiesta simulata non può risultare firmata.")
    sha = hashlib.sha256(document).hexdigest() if document is not None else None
    extra = extra or {}
    sets = "".join(f", {k} = %s" for k in extra)
    cur.execute(f"UPDATE signature_requests SET status = %s, closed_at = NOW(), received_document = %s, "
                f"received_document_sha256 = %s, received_at = CASE WHEN %s::bytea IS NULL THEN NULL ELSE NOW() END"
                f"{sets} WHERE id = %s AND status = 'sent' RETURNING {COLUMNS}",
                (to_status, document, sha, document, *extra.values(), row["id"]))
    nuova = cur.fetchone()
    if nuova is None:
        raise Conflict("REQUEST_CLOSED", "La richiesta è stata chiusa nel frattempo.")
    _event(cur, row, row["status"], to_status, actor, details)
    if _signers_installed(cur):                       # richiesta chiusa: i collegamenti personali non servono piu'
        cur.execute("UPDATE signature_request_signers SET signing_url = NULL, updated_at = NOW() "
                    "WHERE request_id = %s AND signing_url IS NOT NULL", (row["id"],))
    return dict(nuova)


def _lock(cur, agency_id: int, request_id: int) -> dict | None:
    cur.execute(f"SELECT {COLUMNS} FROM signature_requests WHERE id = %s AND agency_id = %s FOR UPDATE",
                (request_id, agency_id))
    row = cur.fetchone()
    return dict(row) if row else None


def _expire_if_due(cur, row: dict) -> dict:
    """La scadenza si applica alla lettura SOLO per l'adattatore simulato. Per un fornitore reale la chiude la
    riconciliazione, dopo aver ANNULLATO la busta presso il fornitore: segnarla `expired` qui, senza revocarla, lascerebbe
    un collegamento di firma ancora valido su una richiesta che il CRM dice chiusa."""
    if row["status"] == "sent" and row["expires_at"] <= _now() and row["provider"] == providers.SIMULATED:
        return _transition(cur, row, "expired", "system", details={"reason": "scadenza superata"})
    return row


def _by_key(cur, agency_id: int, idempotency_key: str) -> dict | None:
    cur.execute(f"SELECT {COLUMNS} FROM signature_requests WHERE agency_id = %s AND idempotency_key = %s",
                (agency_id, idempotency_key))
    row = cur.fetchone()
    return dict(row) if row else None


def _env_installed(cur) -> bool:
    cur.execute("SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' "
                "AND table_name = 'signature_requests' AND column_name = 'provider_environment') AS presente")
    return bool(cur.fetchone()["presente"])


def _agent_signer(cur, agency_id: int, user_id: int | None) -> dict:
    if user_id is None:
        raise SignatureError("NO_AGENT", "La visita non ha un agente assegnato: l'agente firma insieme al cliente.")
    cur.execute("SELECT btrim(concat_ws(' ', first_name, last_name)) AS name, email FROM operator_users u "
                "JOIN agency_memberships m ON m.operator_user_id = u.id AND m.agency_id = %s WHERE u.id = %s",
                (agency_id, user_id))
    row = cur.fetchone()
    if row is None or not EMAIL.match(row["email"] or ""):
        raise SignatureError("NO_AGENT", "L'agente assegnato non ha un indirizzo email valido.")
    return {"user_id": user_id, "name": row["name"] or row["email"], "email": row["email"].strip()}


def _open_for_sheet(cur, agency_id: int, sheet_id: int) -> list:
    cur.execute(f"SELECT {COLUMNS} FROM signature_requests WHERE agency_id = %s AND visit_sheet_id = %s AND status = 'sent' "
                "FOR UPDATE", (agency_id, sheet_id))
    return cur.fetchall()


def create(ctx, *, visit_sheet_id: int, signer_name: str, signer_contact: str, expires_in_days: int,
           idempotency_key: str) -> dict:
    """BLOCCO-5: il fornitore si chiama FUORI da ogni transazione (una chiamata
    di rete non tiene aperto un lock) e con la chiave di idempotenza: se il
    nostro INSERT poi fallisce, lo stesso tentativo ritrova la stessa richiesta
    remota invece di crearne un'altra.

    DOCUMENSO: un fornitore con `supports_signers` firma in DUE: il cliente (nome e indirizzo qui indicati) e l'agente
    assegnato alla visita (dati dal server, mai dal browser), ciascuno col proprio collegamento personale e il proprio
    campo sul foglio. Il fornitore non scrive a nessuno: i collegamenti li mostra il CRM."""
    agency_id = ctx.require_agency()
    if ctx.user_id is None:
        raise SignatureError("SESSION_REQUIRED", "Serve una sessione operatore.")
    try:
        provider = providers.current()
    except providers.ProviderNotConfigured as exc:
        raise Unavailable("PROVIDER_NOT_CONFIGURED", "Nessun servizio di firma configurato.") from exc
    multi = bool(getattr(provider, "supports_signers", False))
    if multi and not EMAIL.match(signer_contact):
        raise SignatureError("SIGNER_EMAIL_REQUIRED", "Per la firma serve l'indirizzo email del cliente: serve solo a "
                             "identificarlo presso il servizio di firma, che non gli scrive.")
    with core_cursor(commit=True) as (_, cur):
        _installed(cur)
        existing = _by_key(cur, agency_id, idempotency_key)
        if existing is not None:
            return {"request": _with_signers(cur, [existing])[0], "created": False}
        sheet = _visible_sheet(cur, ctx, visit_sheet_id)
        env_ready = _env_installed(cur)
        if multi and not _signers_installed(cur):
            raise Unavailable("NOT_INSTALLED", "La firma con piu' firmatari richiede la migrazione 128.")
        agent = _agent_signer(cur, agency_id, sheet["assigned_user_id"]) if multi else None
        if multi:                                             # una sola busta aperta per foglio: niente doppioni
            if _open_for_sheet(cur, agency_id, sheet["id"]):
                raise Conflict("REQUEST_OPEN", "Per questo foglio c'è già una richiesta di firma aperta: annullala "
                               "prima di crearne un'altra.")
    if provider.environment != "simulation" and not env_ready:
        raise Unavailable("NOT_INSTALLED", "Un fornitore reale richiede la migrazione 125.")
    document = bytes(sheet["pdf_bytes"])
    if hashlib.sha256(document).hexdigest() != sheet["sha256"]:
        raise SignatureError("SHEET_INTEGRITY", "Il foglio visita non corrisponde alla sua impronta.")
    specs = layout = None
    extra: dict = {}
    if multi:
        if agent["email"].lower() == signer_contact.lower():
            raise Conflict("SAME_SIGNER", "Cliente e agente devono avere indirizzi email diversi.")
        try:
            layout = locate_signature_boxes(document)
        except PdfLayoutError as exc:
            raise SignatureError("LAYOUT_UNKNOWN", "Non trovo i riquadri di firma in questo foglio: generane una nuova "
                                 "versione dalla visita e riprova.") from exc
        specs = (providers.SignerSpec("visitor", signer_name, signer_contact, 1),
                 providers.SignerSpec("agent", agent["name"], agent["email"], 1))
        extra = {"signers": specs, "layout": layout,
                 "title": f"Foglio visita n. {sheet['appointment_id']} (versione {sheet['sheet_version']})"}
    expires_at = _now() + timedelta(days=expires_in_days)
    try:
        accepted = provider.create_request(document=document, document_sha256=sheet["sha256"],
                                           signer_name=signer_name, signer_contact=signer_contact,
                                           expires_at=expires_at, idempotency_key=idempotency_key, **extra)
    except providers.ProviderError as exc:
        logger.warning("esign_create_failed provider=%s code=%s status=%s", provider.name, exc.code, exc.status)
        raise Unavailable(exc.code, str(exc)) from exc
    columns = ("agency_id, visit_sheet_id, provider, provider_reference, signer_name, signer_contact, status, "
               "expires_at, idempotency_key, document_sha256, created_by_user_id")
    values = [agency_id, sheet["id"], provider.name, accepted.reference, signer_name, signer_contact, "sent",
              expires_at, idempotency_key, sheet["sha256"], ctx.user_id]
    if env_ready:
        columns += ", provider_environment"
        values.append(provider.environment)
    with core_cursor(commit=True) as (_, cur):
        cur.execute(f"INSERT INTO signature_requests ({columns}) VALUES ({', '.join(['%s'] * len(values))}) "
                    f"ON CONFLICT (agency_id, idempotency_key) DO NOTHING RETURNING {COLUMNS}", values)
        row = cur.fetchone()
        if row is None:
            return {"request": _with_signers(cur, [_by_key(cur, agency_id, idempotency_key)])[0], "created": False}
        row = dict(row)
        _event(cur, row, None, "sent", f"operator:{ctx.user_id}",
               {"provider": provider.name, "environment": provider.environment})
        if multi:
            by_role = {sp.role: sp for sp in specs}
            for sg in accepted.signers:
                spec = by_role[sg.role]
                cur.execute("INSERT INTO signature_request_signers (request_id, agency_id, role, signer_order, name, email, "
                            "operator_user_id, provider_recipient_id, signing_url) "
                            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                            (row["id"], agency_id, sg.role, spec.order, spec.name, spec.email,
                             agent["user_id"] if sg.role == "agent" else None, sg.recipient_id, sg.signing_url))
        view = _with_signers(cur, [row])[0]
    return {"request": view, "created": True}


def list_for_sheet(ctx, visit_sheet_id: int) -> dict:
    agency_id = ctx.require_agency()
    with core_cursor(commit=True) as (_, cur):
        _installed(cur)
        _visible_sheet(cur, ctx, visit_sheet_id)
        cur.execute(f"SELECT {COLUMNS} FROM signature_requests WHERE agency_id = %s AND visit_sheet_id = %s "
                    "ORDER BY created_at DESC, id DESC FOR UPDATE", (agency_id, visit_sheet_id))
        rows = [_expire_if_due(cur, dict(r)) for r in cur.fetchall()]
        return {"items": _with_signers(cur, rows)}


def _act(ctx, request_id: int, work) -> dict:
    agency_id = ctx.require_agency()
    with core_cursor(commit=True) as (_, cur):
        _installed(cur)
        row = _lock(cur, agency_id, request_id)
        if row is None:
            raise NotFound("REQUEST_NOT_FOUND", "Richiesta di firma non trovata.")
        _visible_sheet(cur, ctx, row["visit_sheet_id"])
        row = _expire_if_due(cur, row)
        if row["status"] != "sent":
            raise Conflict("REQUEST_CLOSED", f"La richiesta è già chiusa: {STATUS_LABELS[row['status']]}.")
        return _with_signers(cur, [work(cur, row)])[0]


def cancel(ctx, request_id: int) -> dict:
    """Annulla presso il fornitore (fuori transazione) e poi qui. Se il
    fornitore non conferma l'annullamento la richiesta resta com'e'."""
    agency_id = ctx.require_agency()
    with core_cursor() as (_, cur):
        _installed(cur)
        cur.execute(f"SELECT {COLUMNS} FROM signature_requests WHERE id = %s AND agency_id = %s",
                    (request_id, agency_id))
        row = cur.fetchone()
        if row is None:
            raise NotFound("REQUEST_NOT_FOUND", "Richiesta di firma non trovata.")
        _visible_sheet(cur, ctx, row["visit_sheet_id"])
    if row["status"] == "sent" and row["provider_reference"]:
        try:
            providers.by_name(row["provider"]).cancel_request(row["provider_reference"])
        except providers.ProviderNotConfigured as exc:
            raise Unavailable("PROVIDER_NOT_CONFIGURED", "Il servizio di firma di questa richiesta non è "
                              "configurato: annullala dal fornitore.") from exc
        except providers.ProviderError as exc:
            raise Unavailable(exc.code, "Il servizio di firma non ha confermato l'annullamento: la richiesta resta "
                              "aperta.") from exc

    def work(cur, locked):
        return _transition(cur, locked, "cancelled", f"operator:{ctx.user_id}")
    return _act(ctx, request_id, work)


def _open_request(ctx, request_id: int) -> dict:
    """Una richiesta APERTA, visibile a chi chiede, con i suoi firmatari. Per i collegamenti di firma."""
    agency_id = ctx.require_agency()
    with core_cursor(commit=True) as (_, cur):
        _installed(cur)
        row = _lock(cur, agency_id, request_id)
        if row is None:
            raise NotFound("REQUEST_NOT_FOUND", "Richiesta di firma non trovata.")
        _visible_sheet(cur, ctx, row["visit_sheet_id"])
        row = _expire_if_due(cur, row)
    if row["status"] != "sent":
        raise Conflict("REQUEST_CLOSED", f"La richiesta è già chiusa: {STATUS_LABELS[row['status']]}.")
    return row


def refresh(ctx, request_id: int) -> dict:
    """«Aggiorna stato» dall'operatore: rilegge dal fornitore e applica cio' che e' successo (come la riconciliazione, per
    UNA richiesta). Utile quando i webhook non arrivano. Mai per l'adattatore simulato."""
    row = _open_request(ctx, request_id)
    if row["provider"] == providers.SIMULATED:
        raise Conflict("NOT_A_PROVIDER", "Una richiesta simulata non ha uno stato presso un fornitore.")
    try:
        provider = providers.by_name(row["provider"])
    except providers.ProviderNotConfigured as exc:
        raise Unavailable("PROVIDER_NOT_CONFIGURED", "Il servizio di firma di questa richiesta non è configurato.") from exc
    outcome = _settle(provider, row, actor=f"operator:{ctx.user_id}:refresh")
    agency_id = ctx.require_agency()
    with core_cursor() as (_, cur):
        fresh = _lock_free(cur, agency_id, request_id)
        return {"outcome": outcome["outcome"], "request": _with_signers(cur, [fresh])[0]}


def _lock_free(cur, agency_id: int, request_id: int) -> dict:
    cur.execute(f"SELECT {COLUMNS} FROM signature_requests WHERE id = %s AND agency_id = %s", (request_id, agency_id))
    return dict(cur.fetchone())


def signing_links(ctx, request_id: int) -> dict:
    """I collegamenti di firma PERSONALI dei firmatari che devono ancora firmare. Sono segreti: chi li apre firma al
    posto di quel firmatario. Solo per la richiesta aperta, solo a chi vede il foglio; mai nei log."""
    row = _open_request(ctx, request_id)
    if row["expires_at"] <= _now():
        raise Conflict("REQUEST_EXPIRED", "La richiesta è scaduta: sarà chiusa dalla prossima riconciliazione. "
                       "Annullala per crearne un'altra.")
    with core_cursor() as (_, cur):
        if not _signers_installed(cur):
            return {"items": []}
        cur.execute("SELECT role, name, status, signing_url FROM signature_request_signers WHERE request_id = %s "
                    "ORDER BY signer_order, id", (row["id"],))
        return {"items": [{"role": r["role"], "role_label": SIGNER_LABELS[r["role"]], "name": r["name"],
                           "status": r["status"], "status_label": SIGNER_STATUS_LABELS[r["status"]],
                           "url": r["signing_url"] if r["status"] == "pending" else None} for r in cur.fetchall()]}


def signing_qr(ctx, request_id: int, role: str) -> bytes:
    """Il QR (SVG) del collegamento di un firmatario: il cliente lo inquadra col telefono. Stesse regole dei link."""
    import io

    import segno
    link = next((i for i in signing_links(ctx, request_id)["items"] if i["role"] == role and i["url"]), None)
    if link is None:
        raise NotFound("NO_LINK", "Nessun collegamento di firma disponibile per questo firmatario.")
    out = io.BytesIO()
    segno.make(link["url"], error="m").save(out, kind="svg", scale=6, border=2, xmldecl=False, svgns=True)
    return out.getvalue()


def simulate_outcome(ctx, request_id: int, outcome: str) -> dict:
    """Solo per l'adattatore simulato: completa (con la ricevuta di simulazione)
    o rifiuta. Mai «signed»."""
    to_status = SIMULATED_OUTCOMES[outcome]

    def work(cur, row):
        if row["provider"] != providers.SIMULATED:
            raise Conflict("NOT_A_SIMULATION", "L'esito si simula solo per le richieste simulate.")
        document = None
        if to_status == "simulated_completed":
            document = providers.SimulatedProvider().fetch_document(
                row["provider_reference"], document_sha256=row["document_sha256"], signer_name=row["signer_name"]).content
        return _transition(cur, row, to_status, f"simulation:operator:{ctx.user_id}", document=document,
                           details={"simulation": True})
    return _act(ctx, request_id, work)


def document(ctx, request_id: int) -> dict:
    agency_id = ctx.require_agency()
    with core_cursor() as (_, cur):
        _installed(cur)
        cur.execute("SELECT id, visit_sheet_id, provider, status, received_document, received_document_sha256 "
                    "FROM signature_requests WHERE id = %s AND agency_id = %s", (request_id, agency_id))
        row = cur.fetchone()
        if row is None:
            raise NotFound("REQUEST_NOT_FOUND", "Richiesta di firma non trovata.")
        _visible_sheet(cur, ctx, row["visit_sheet_id"])
    if row["received_document"] is None:
        raise NotFound("NO_DOCUMENT", "Nessun documento ricevuto per questa richiesta.")
    data = bytes(row["received_document"])
    if hashlib.sha256(data).hexdigest() != row["received_document_sha256"]:
        raise SignatureError("DOCUMENT_INTEGRITY", "Il documento ricevuto non corrisponde alla sua impronta.")
    prefix = "SIMULAZIONE-non-firmato" if row["provider"] == providers.SIMULATED else "documento-firmato"
    return {"data": data, "filename": f"{prefix}-richiesta-{request_id}.pdf"}


# ---------------------------------------------------------------------------
# BLOCCO-5: eventi del fornitore (webhook) e riconciliazione, indipendenti dal fornitore
# ---------------------------------------------------------------------------

#: Lo stato del fornitore -> il nostro (solo da `sent`).
PROVIDER_TO_STATUS = {"signed": "signed", "declined": "declined", "expired": "expired", "cancelled": "cancelled"}


def _record_event(cur, provider_name: str, event, raw_body: bytes, *, request: dict | None, outcome: str,
                  detail: str | None = None) -> bool:
    """Una riga per (fornitore, id evento); False se l'evento era gia' arrivato."""
    cur.execute("INSERT INTO signature_provider_events (provider, provider_event_id, event_type, provider_reference, "
                "request_id, agency_id, body_sha256, outcome, detail) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (provider, provider_event_id) DO NOTHING RETURNING id",
                (provider_name, event.event_id[:200], event.event_type[:80], event.reference,
                 (request or {}).get("id"), (request or {}).get("agency_id"), hashlib.sha256(raw_body).hexdigest(),
                 outcome, (detail or "")[:200] or None))
    return cur.fetchone() is not None


def _sync_signers(cur, request: dict, remote_signers) -> None:
    """Allinea lo stato personale di chi ha firmato o rifiutato (solo da `pending`, mai all'indietro)."""
    if not remote_signers or not _signers_installed(cur):
        return
    cur.execute("SELECT id, email, provider_recipient_id FROM signature_request_signers "
                "WHERE request_id = %s AND status = 'pending' FOR UPDATE", (request["id"],))
    for local in cur.fetchall():
        match = next((r for r in remote_signers
                      if (r.recipient_id and r.recipient_id == local["provider_recipient_id"])
                      or (r.email and r.email.lower() == local["email"].lower())), None)
        if match is None or match.status == "pending":
            continue
        if match.status == "signed" and match.signed_at is None:
            continue                                               # una firma senza data non si registra
        cur.execute("UPDATE signature_request_signers SET status = %s, signed_at = %s, updated_at = NOW() WHERE id = %s",
                    (match.status, match.signed_at if match.status == "signed" else None, local["id"]))


def _lookup(cur, provider_name: str, event) -> dict | None:
    """La richiesta a cui l'evento si riferisce: per riferimento del fornitore, altrimenti per l'identificativo che
    NOI avevamo dato alla busta (la chiave di idempotenza). Ambiguo -> nessuna."""
    if event.reference:
        cur.execute(f"SELECT {COLUMNS} FROM signature_requests WHERE provider = %s AND provider_reference = %s",
                    (provider_name, event.reference))
        row = cur.fetchone()
        return dict(row) if row else None
    if event.external_id:
        cur.execute(f"SELECT {COLUMNS} FROM signature_requests WHERE provider = %s AND idempotency_key = %s LIMIT 2",
                    (provider_name, event.external_id))
        rows = cur.fetchall()
        return dict(rows[0]) if len(rows) == 1 else None
    return None


def _settle(provider, found: dict, *, actor: str, event=None, raw_body: bytes | None = None) -> dict:
    """Rilegge lo stato dal fornitore (l'evento non decide mai da solo) e applica la transizione sotto lock.
    Il documento firmato e l'audit trail si scaricano e si conservano con la loro impronta."""
    try:
        remote = provider.fetch_status(found["provider_reference"])                  # fuori transazione
        target = PROVIDER_TO_STATUS.get(remote.status)
        document = audit = None
        if target == "signed":
            document = provider.fetch_document(found["provider_reference"], document_sha256=found["document_sha256"],
                                               signer_name=found["signer_name"]).content
            trail = provider.fetch_audit_trail(found["provider_reference"])
            audit = trail.content if trail is not None else None
    except providers.ProviderError as exc:
        logger.warning("esign_settle_failed provider=%s code=%s status=%s", provider.name, exc.code, exc.status)
        raise Unavailable(exc.code, str(exc)) from exc
    with core_cursor(commit=True) as (_, cur):
        row = _lock(cur, found["agency_id"], found["id"])
        if row["status"] == "sent":
            _sync_signers(cur, row, remote.signers)
        if target is None or row["status"] != "sent":
            outcome = "no_change" if target is None else "status_mismatch"
            if event is not None:
                fresh = _record_event(cur, provider.name, event, raw_body, request=row, outcome=outcome,
                                      detail=f"provider={remote.status} local={row['status']}")
                return {"outcome": outcome if fresh else "duplicate"}
            return {"outcome": outcome}
        if event is not None and not _record_event(cur, provider.name, event, raw_body, request=row, outcome="applied",
                                                   detail=f"provider={remote.status}"):
            return {"outcome": "duplicate"}
        details = {"event_id": event.event_id} if event is not None else {"reconciled": True}
        if target == "signed":
            if remote.signed_at is None or remote.signature_level not in ("ses", "aes", "qes"):
                raise Conflict("SIGNED_WITHOUT_EVIDENCE", "Il fornitore non ha indicato data e livello della firma.")
            new = _transition(cur, row, "signed", actor, document=document,
                              details={**details, "environment": row.get("provider_environment")},
                              extra={"signature_level": remote.signature_level, "signed_at": remote.signed_at,
                                     "audit_trail": audit,
                                     "audit_trail_sha256": hashlib.sha256(audit).hexdigest() if audit else None})
        else:
            new = _transition(cur, row, target, actor, details=details)
    return {"outcome": "applied", "status": new["status"]}


def handle_provider_event(provider_name: str, headers: dict, raw_body: bytes) -> dict:
    """Un evento del fornitore: autenticita' verificata dall'adattatore sul corpo grezzo (firma o segreto condiviso, a
    seconda del fornitore), una sola applicazione per id evento, stato RILETTO dal fornitore, transizione sotto lock.
    Mai per l'adattatore simulato."""
    if provider_name == providers.SIMULATED:
        raise NotFound("UNKNOWN_PROVIDER", "Fornitore sconosciuto.")
    try:
        provider = providers.by_name(provider_name)
    except providers.ProviderNotConfigured as exc:
        raise NotFound("UNKNOWN_PROVIDER", "Fornitore sconosciuto.") from exc
    event = provider.verify_event(headers, raw_body)           # InvalidEventSignature -> 401 dal router
    with core_cursor() as (_, cur):
        cur.execute("SELECT 1 FROM signature_provider_events WHERE provider = %s AND provider_event_id = %s",
                    (provider_name, event.event_id))
        if cur.fetchone() is not None:
            return {"outcome": "duplicate"}
        found = _lookup(cur, provider_name, event)
    if found is None:
        with core_cursor(commit=True) as (_, cur):
            fresh = _record_event(cur, provider_name, event, raw_body, request=None, outcome="unknown_request")
        return {"outcome": "unknown_request" if fresh else "duplicate"}
    event = dataclasses.replace(event, reference=found["provider_reference"])
    return _settle(provider, found, actor=f"provider:{provider_name}", event=event, raw_body=raw_body)


def reconcile(agency_id: int, *, limit: int = 50) -> dict:
    """RICONCILIAZIONE PERIODICA, per quando i webhook non arrivano (piano senza webhook, rete, riavvii): rilegge dal
    fornitore le richieste ancora aperte e applica cio' che e' successo. Una richiesta scaduta in locale viene prima
    ANNULLATA presso il fornitore, cosi' il collegamento di firma smette di funzionare. Idempotente: si puo' lanciare
    quando si vuole, anche in parallelo ai webhook (il lock di riga e la transizione condizionata evitano doppi effetti).
    Conta soltanto: nessun dato personale nell'esito."""
    counts = {"checked": 0, "applied": 0, "expired": 0, "unchanged": 0, "not_configured": 0, "errors": 0}
    with core_cursor() as (_, cur):
        _installed(cur)
        cur.execute(f"SELECT {COLUMNS} FROM signature_requests WHERE agency_id = %s AND status = 'sent' "
                    "AND provider <> %s AND provider_reference IS NOT NULL ORDER BY created_at, id LIMIT %s",
                    (agency_id, providers.SIMULATED, limit))
        rows = [dict(r) for r in cur.fetchall()]
    for row in rows:
        counts["checked"] += 1
        try:
            provider = providers.by_name(row["provider"])
        except providers.ProviderNotConfigured:
            counts["not_configured"] += 1
            continue
        try:
            if row["expires_at"] <= _now():
                provider.cancel_request(row["provider_reference"])
                with core_cursor(commit=True) as (_, cur):
                    locked = _lock(cur, agency_id, row["id"])
                    if locked["status"] == "sent":
                        _transition(cur, locked, "expired", f"reconcile:{provider.name}",
                                    details={"reason": "scadenza superata", "provider_cancelled": True})
                counts["expired"] += 1
                continue
            out = _settle(provider, row, actor=f"reconcile:{provider.name}")
        except (providers.ProviderError, SignatureError) as exc:
            logger.warning("esign_reconcile_failed provider=%s code=%s", row["provider"], getattr(exc, "code", "error"))
            counts["errors"] += 1
            continue
        counts["applied" if out["outcome"] == "applied" else "unchanged"] += 1
    return counts


def audit_trail(ctx, request_id: int) -> dict:
    """Il rapporto di completamento del fornitore, verificato con la sua impronta."""
    agency_id = ctx.require_agency()
    with core_cursor() as (_, cur):
        _installed(cur)
        if not _env_installed(cur):
            raise NotFound("NO_AUDIT_TRAIL", "Nessun rapporto del fornitore per questa richiesta.")
        cur.execute("SELECT id, visit_sheet_id, audit_trail, audit_trail_sha256 FROM signature_requests "
                    "WHERE id = %s AND agency_id = %s", (request_id, agency_id))
        row = cur.fetchone()
        if row is None:
            raise NotFound("REQUEST_NOT_FOUND", "Richiesta di firma non trovata.")
        _visible_sheet(cur, ctx, row["visit_sheet_id"])
    if row["audit_trail"] is None:
        raise NotFound("NO_AUDIT_TRAIL", "Nessun rapporto del fornitore per questa richiesta.")
    data = bytes(row["audit_trail"])
    if hashlib.sha256(data).hexdigest() != row["audit_trail_sha256"]:
        raise SignatureError("AUDIT_INTEGRITY", "Il rapporto del fornitore non corrisponde alla sua impronta.")
    pdf = data.startswith(b"%PDF")
    return {"data": data, "media_type": "application/pdf" if pdf else "application/json",
            "filename": f"rapporto-firma-richiesta-{request_id}.{'pdf' if pdf else 'json'}"}
