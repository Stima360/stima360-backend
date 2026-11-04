"""BLOCCO-6B / FIRMA OTP - richieste di firma dei fogli visita: viste, transizioni controllate, simulazione.

    sent ──► signed                (FIRMA OTP STIMA360: tutti i firmatari hanno confermato; FES)
     │  ├──► simulated_completed   (solo adattatore simulato: NON e' una firma)
     │  ├──► declined              (un firmatario ha rifiutato)
     │  ├──► expired               (scadenza superata: applicata alla lettura)
     │  └──► cancelled             (revoca dell'agenzia)
     └ ogni stato diverso da `sent` e' finale.

Ogni transizione e' sotto lock di riga, condizionata allo stato atteso e registrata in `signature_request_events`;
il database rifiuta comunque modifiche a una richiesta chiusa e la firma «signed» di una simulazione. Idempotenza: la
stessa chiave nella stessa agenzia restituisce la stessa richiesta. Permessi: la visibilita' dell'Agenda sul foglio.
Il procedimento OTP (collegamenti, codici, prove, PDF) sta in `esign.otp_service`; qui le parti comuni.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone

from psycopg2.extras import Json

from core.database import core_cursor
from visit_sheets import repository as sheets_repository
from visit_sheets import service as sheets_service

from . import providers

logger = logging.getLogger(__name__)

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
ENVIRONMENT_LABELS = {"simulation": "Simulazione locale", "sandbox": "Ambiente di prova", "production": "Ambiente reale"}
SIGNER_LABELS = {"visitor": "Cliente", "agent": "Agente"}
SIGNER_STATUS_LABELS = {"pending": "Da firmare", "signed": "Ha firmato", "declined": "Ha rifiutato"}
#: Il livello in parole: «semplice» NON e' «avanzata» ne' «qualificata».
LEVEL_LABELS = {"ses": "Firma elettronica semplice (FES)", "aes": "Firma elettronica avanzata",
                "qes": "Firma elettronica qualificata"}


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


class Forbidden(SignatureError):
    status = 403


class TooMany(SignatureError):
    status = 429


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _installed(cur) -> None:
    cur.execute("SELECT to_regclass('public.signature_requests') IS NOT NULL AS presente")
    if not cur.fetchone()["presente"]:
        raise Unavailable("NOT_INSTALLED", "Le richieste di firma non sono installate (migrazione 122).")


def _signers_installed(cur) -> bool:
    cur.execute("SELECT to_regclass('public.signature_request_signers') IS NOT NULL AS presente")
    return bool(cur.fetchone()["presente"])


def _otp_installed(cur) -> bool:
    cur.execute("SELECT to_regclass('public.signature_challenges') IS NOT NULL AS presente")
    return bool(cur.fetchone()["presente"])


def _env_installed(cur) -> bool:
    cur.execute("SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' "
                "AND table_name = 'signature_requests' AND column_name = 'provider_environment') AS presente")
    return bool(cur.fetchone()["presente"])


# ---------------------------------------------------------------------------------------------------------------------
# viste
# ---------------------------------------------------------------------------------------------------------------------

def _view(row: dict) -> dict:
    out = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in row.items() if k != "idempotency_key"}
    out["status_label"] = STATUS_LABELS[row["status"]]
    out["simulation"] = row["provider"] == providers.SIMULATED
    out["otp"] = row["provider"] == providers.OTP_PROVIDER
    env = row.get("provider_environment")
    out["environment_label"] = ENVIRONMENT_LABELS.get(env, env)
    # condizione TECNICA (non legale): firmata davvero, non in simulazione ne' in un ambiente di prova. Il LIVELLO
    # e' sempre dichiarato accanto: per la firma OTP e' «semplice».
    out["valid_signature"] = (row["status"] == "signed" and row["provider"] != providers.SIMULATED
                              and env == "production")
    out["has_document"] = row.get("received_document_sha256") is not None
    out["has_audit_trail"] = row.get("audit_trail_sha256") is not None
    out["signature_level_label"] = LEVEL_LABELS.get(row.get("signature_level") or "")
    return out


def _signers_of(cur, request_ids: list[int]) -> dict[int, list[dict]]:
    if not request_ids or not _signers_installed(cur):
        return {}
    cur.execute("SELECT id, request_id, role, name, email, status, signed_at, operator_user_id FROM signature_request_signers "
                "WHERE request_id = ANY(%s) ORDER BY request_id, signer_order, id", (request_ids,))
    out: dict[int, list[dict]] = {}
    for r in cur.fetchall():
        out.setdefault(r["request_id"], []).append({
            "id": r["id"], "role": r["role"], "role_label": SIGNER_LABELS[r["role"]], "name": r["name"], "email": r["email"],
            "status": r["status"], "status_label": SIGNER_STATUS_LABELS[r["status"]],
            "signed_at": r["signed_at"].isoformat() if r["signed_at"] else None,
            "operator_user_id": r["operator_user_id"]})
    return out


def _with_signers(cur, rows: list[dict]) -> list[dict]:
    """Le viste con i firmatari e, per la firma OTP, lo stato operativo derivato dai dati persistiti."""
    views = [_view(r) for r in rows]
    signers = _signers_of(cur, [r["id"] for r in rows])
    for v in views:
        v["signers"] = signers.get(v["id"], [])
    otp_rows = [v for v in views if v["otp"]]
    if otp_rows and _otp_installed(cur):
        from . import otp_service
        otp_service.decorate(cur, otp_rows)
    for v in views:
        v.setdefault("state", v["status"])
        v.setdefault("state_label", v["status_label"])
    return views


def _visible_sheet(cur, ctx, sheet_id: int) -> dict:
    agency_id = ctx.require_agency()
    cur.execute("SELECT id, appointment_id, property_id, sheet_version, sha256, pdf_bytes FROM visit_sheets "
                "WHERE id = %s AND agency_id = %s", (sheet_id, agency_id))
    sheet = cur.fetchone()
    visit = sheets_repository.visit(cur, agency_id, sheet["appointment_id"]) if sheet else None
    if sheet is None or visit is None or not sheets_service._visible(ctx, visit):
        raise NotFound("SHEET_NOT_FOUND", "Foglio visita non trovato.")
    return {**dict(sheet), "assigned_user_id": visit.get("assigned_user_id"), "contact_id": visit.get("contact_id"),
            "start_at": visit.get("start_at")}


# ---------------------------------------------------------------------------------------------------------------------
# transizioni
# ---------------------------------------------------------------------------------------------------------------------

def _event(cur, row: dict, from_status, to_status, actor: str, details: dict | None = None) -> None:
    cur.execute("INSERT INTO signature_request_events (request_id, agency_id, from_status, to_status, actor, details) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (row["id"], row["agency_id"], from_status, to_status, actor, Json(details or {})))


def _transition(cur, row: dict, to_status: str, actor: str, *, document: bytes | None = None,
                details: dict | None = None, extra: dict | None = None) -> dict:
    """`extra`: colonne della 125 da scrivere NELLA STESSA istruzione (una richiesta chiusa non si modifica piu', e
    `signed` vuole data e livello)."""
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
    if _otp_installed(cur):
        # richiesta chiusa: nessun codice resta aperto; i collegamenti personali si chiudono SOLO se la chiusura non e'
        # una firma (dopo la firma il cliente li usa ancora, in sola lettura, per vedere l'esito e scaricare il documento)
        kinds = ("otp",) if to_status == "signed" else ("otp", "access")
        cur.execute("UPDATE signature_challenges SET invalidated_at = NOW(), invalidation_reason = %s "
                    "WHERE request_id = %s AND kind = ANY(%s) AND consumed_at IS NULL AND invalidated_at IS NULL",
                    (f"request_{to_status}"[:40], row["id"], list(kinds)))
    return dict(nuova)


def _lock(cur, agency_id: int, request_id: int) -> dict | None:
    cur.execute(f"SELECT {COLUMNS} FROM signature_requests WHERE id = %s AND agency_id = %s FOR UPDATE",
                (request_id, agency_id))
    row = cur.fetchone()
    return dict(row) if row else None


def _expire_if_due(cur, row: dict) -> dict:
    """La scadenza si applica alla lettura, sotto il lock del chiamante. Per la firma OTP la scadenza chiude anche i
    collegamenti (le sfide) e lascia la sua traccia nel registro delle prove."""
    if row["status"] == "sent" and row["expires_at"] <= _now():
        nuova = _transition(cur, row, "expired", "system", details={"reason": "scadenza superata"})
        if row["provider"] == providers.OTP_PROVIDER and _otp_installed(cur):
            from . import otp_service
            otp_service.on_expired(cur, nuova)
        return nuova
    return row


def _by_key(cur, agency_id: int, idempotency_key: str) -> dict | None:
    cur.execute(f"SELECT {COLUMNS} FROM signature_requests WHERE agency_id = %s AND idempotency_key = %s",
                (agency_id, idempotency_key))
    row = cur.fetchone()
    return dict(row) if row else None


def _open_for_sheet(cur, agency_id: int, sheet_id: int) -> list:
    cur.execute(f"SELECT {COLUMNS} FROM signature_requests WHERE agency_id = %s AND visit_sheet_id = %s AND status = 'sent' "
                "FOR UPDATE", (agency_id, sheet_id))
    return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------------------------------------------------------------
# creazione
# ---------------------------------------------------------------------------------------------------------------------

def create(ctx, *, visit_sheet_id: int, signer_name: str, signer_contact: str, expires_in_days: int,
           idempotency_key: str, include_agent: bool = True) -> dict:
    """La richiesta di firma. Con `ESIGN_PROVIDER=stima360_otp` e' il procedimento OTP (`otp_service.create`);
    altrimenti la simulazione locale. Mai un fornitore esterno."""
    agency_id = ctx.require_agency()
    if ctx.user_id is None:
        raise SignatureError("SESSION_REQUIRED", "Serve una sessione operatore.")
    try:
        modo = providers.mode()
    except providers.ProviderNotConfigured as exc:
        raise Unavailable("PROVIDER_NOT_CONFIGURED", "Nessun servizio di firma configurato.") from exc
    if modo == providers.OTP_PROVIDER:
        from . import otp_service
        return otp_service.create(ctx, visit_sheet_id=visit_sheet_id, signer_name=signer_name, signer_email=signer_contact,
                                  expires_in_days=expires_in_days, idempotency_key=idempotency_key,
                                  include_agent=include_agent)
    provider = providers.current()
    with core_cursor(commit=True) as (_, cur):
        _installed(cur)
        existing = _by_key(cur, agency_id, idempotency_key)
        if existing is not None:
            return {"request": _with_signers(cur, [existing])[0], "created": False}
        sheet = _visible_sheet(cur, ctx, visit_sheet_id)
        env_ready = _env_installed(cur)
    document = bytes(sheet["pdf_bytes"])
    if hashlib.sha256(document).hexdigest() != sheet["sha256"]:
        raise SignatureError("SHEET_INTEGRITY", "Il foglio visita non corrisponde alla sua impronta.")
    expires_at = _now() + timedelta(days=expires_in_days)
    accepted = provider.create_request(document=document, document_sha256=sheet["sha256"], signer_name=signer_name,
                                       signer_contact=signer_contact, expires_at=expires_at, idempotency_key=idempotency_key)
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
        _event(cur, row, None, "sent", f"operator:{ctx.user_id}", {"provider": provider.name, "environment": provider.environment})
        view = _with_signers(cur, [row])[0]
    return {"request": view, "created": True}


# ---------------------------------------------------------------------------------------------------------------------
# letture e azioni dell'operatore
# ---------------------------------------------------------------------------------------------------------------------

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
    """Revoca: chiude la richiesta e ogni collegamento. Per la simulazione avvisa l'adattatore (nessuna rete)."""
    agency_id = ctx.require_agency()
    with core_cursor() as (_, cur):
        _installed(cur)
        cur.execute(f"SELECT {COLUMNS} FROM signature_requests WHERE id = %s AND agency_id = %s", (request_id, agency_id))
        row = cur.fetchone()
        if row is None:
            raise NotFound("REQUEST_NOT_FOUND", "Richiesta di firma non trovata.")
        _visible_sheet(cur, ctx, row["visit_sheet_id"])
    if row["status"] == "sent" and row["provider"] == providers.SIMULATED and row["provider_reference"]:
        providers.by_name(row["provider"]).cancel_request(row["provider_reference"])

    def work(cur, locked):
        nuova = _transition(cur, locked, "cancelled", f"operator:{ctx.user_id}")
        if locked["provider"] == providers.OTP_PROVIDER and _otp_installed(cur):
            from . import otp_service
            otp_service.on_cancelled(cur, nuova, actor_user_id=ctx.user_id)
        return nuova
    return _act(ctx, request_id, work)


def simulate_outcome(ctx, request_id: int, outcome: str) -> dict:
    """Solo per l'adattatore simulato: completa (con la ricevuta di simulazione) o rifiuta. Mai «signed»."""
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


def _request_of(ctx, cur, request_id: int) -> dict:
    agency_id = ctx.require_agency()
    cur.execute("SELECT id, visit_sheet_id, provider, provider_reference, status, received_document, "
                "received_document_sha256, to_jsonb(signature_requests)->>'audit_trail_sha256' AS audit_trail_sha256 "
                "FROM signature_requests WHERE id = %s AND agency_id = %s", (request_id, agency_id))
    row = cur.fetchone()
    if row is None:
        raise NotFound("REQUEST_NOT_FOUND", "Richiesta di firma non trovata.")
    _visible_sheet(cur, ctx, row["visit_sheet_id"])
    return dict(row)


def document(ctx, request_id: int) -> dict:
    with core_cursor() as (_, cur):
        _installed(cur)
        row = _request_of(ctx, cur, request_id)
    if row["received_document"] is None:
        raise NotFound("NO_DOCUMENT", "Nessun documento ricevuto per questa richiesta.")
    data = bytes(row["received_document"])
    if hashlib.sha256(data).hexdigest() != row["received_document_sha256"]:
        raise SignatureError("DOCUMENT_INTEGRITY", "Il documento ricevuto non corrisponde alla sua impronta.")
    if row["provider"] == providers.SIMULATED:
        name = f"SIMULAZIONE-non-firmato-richiesta-{request_id}.pdf"
    else:
        name = f"foglio-visita-sottoscritto-{row['provider_reference'] or request_id}.pdf"
    return {"data": data, "filename": name, "media_type": "application/pdf"}


def audit_trail(ctx, request_id: int) -> dict:
    """Il rapporto delle prove (firma OTP), verificato con la sua impronta."""
    with core_cursor() as (_, cur):
        _installed(cur)
        if not _env_installed(cur):
            raise NotFound("NO_AUDIT_TRAIL", "Nessun rapporto per questa richiesta.")
        row = _request_of(ctx, cur, request_id)
        cur.execute("SELECT audit_trail FROM signature_requests WHERE id = %s", (request_id,))
        trail = cur.fetchone()["audit_trail"]
    if trail is None:
        raise NotFound("NO_AUDIT_TRAIL", "Nessun rapporto per questa richiesta.")
    data = bytes(trail)
    if hashlib.sha256(data).hexdigest() != row["audit_trail_sha256"]:
        raise SignatureError("AUDIT_INTEGRITY", "Il rapporto non corrisponde alla sua impronta.")
    pdf = data.startswith(b"%PDF")
    return {"data": data, "media_type": "application/pdf" if pdf else "application/json",
            "filename": f"rapporto-prove-{row['provider_reference'] or request_id}.{'pdf' if pdf else 'json'}"}
