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

import hashlib
from datetime import datetime, timedelta, timezone

from psycopg2.extras import Json

from core.database import core_cursor
from visit_sheets import repository as sheets_repository
from visit_sheets import service as sheets_service

from . import providers

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
    # una firma VALIDA solo da un fornitore reale, nel suo ambiente reale: una
    # firma dell'ambiente di prova (sandbox) resta di prova
    out["valid_signature"] = (row["status"] == "signed" and row["provider"] != providers.SIMULATED
                              and env == "production")
    out["has_document"] = row.get("received_document_sha256") is not None
    return out


def _visible_sheet(cur, ctx, sheet_id: int) -> dict:
    agency_id = ctx.require_agency()
    cur.execute("SELECT id, appointment_id, sha256, pdf_bytes FROM visit_sheets WHERE id = %s AND agency_id = %s",
                (sheet_id, agency_id))
    sheet = cur.fetchone()
    visit = sheets_repository.visit(cur, agency_id, sheet["appointment_id"]) if sheet else None
    if sheet is None or visit is None or not sheets_service._visible(ctx, visit):
        raise NotFound("SHEET_NOT_FOUND", "Foglio visita non trovato.")
    return dict(sheet)


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
    return dict(nuova)


def _lock(cur, agency_id: int, request_id: int) -> dict | None:
    cur.execute(f"SELECT {COLUMNS} FROM signature_requests WHERE id = %s AND agency_id = %s FOR UPDATE",
                (request_id, agency_id))
    row = cur.fetchone()
    return dict(row) if row else None


def _expire_if_due(cur, row: dict) -> dict:
    if row["status"] == "sent" and row["expires_at"] <= _now():
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


def create(ctx, *, visit_sheet_id: int, signer_name: str, signer_contact: str, expires_in_days: int,
           idempotency_key: str) -> dict:
    """BLOCCO-5: il fornitore si chiama FUORI da ogni transazione (una chiamata
    di rete non tiene aperto un lock) e con la chiave di idempotenza: se il
    nostro INSERT poi fallisce, lo stesso tentativo ritrova la stessa richiesta
    remota invece di crearne un'altra."""
    agency_id = ctx.require_agency()
    if ctx.user_id is None:
        raise SignatureError("SESSION_REQUIRED", "Serve una sessione operatore.")
    try:
        provider = providers.current()
    except providers.ProviderNotConfigured as exc:
        raise Unavailable("PROVIDER_NOT_CONFIGURED", "Nessun servizio di firma configurato.") from exc
    with core_cursor() as (_, cur):
        _installed(cur)
        existing = _by_key(cur, agency_id, idempotency_key)
        if existing is not None:
            return {"request": _view(existing), "created": False}
        sheet = _visible_sheet(cur, ctx, visit_sheet_id)
        env_ready = _env_installed(cur)
    if provider.environment != "simulation" and not env_ready:
        raise Unavailable("NOT_INSTALLED", "Un fornitore reale richiede la migrazione 125.")
    document = bytes(sheet["pdf_bytes"])
    if hashlib.sha256(document).hexdigest() != sheet["sha256"]:
        raise SignatureError("SHEET_INTEGRITY", "Il foglio visita non corrisponde alla sua impronta.")
    expires_at = _now() + timedelta(days=expires_in_days)
    accepted = provider.create_request(document=document, document_sha256=sheet["sha256"],
                                       signer_name=signer_name, signer_contact=signer_contact,
                                       expires_at=expires_at, idempotency_key=idempotency_key)
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
            return {"request": _view(_by_key(cur, agency_id, idempotency_key)), "created": False}
        row = dict(row)
        _event(cur, row, None, "sent", f"operator:{ctx.user_id}",
               {"provider": provider.name, "environment": provider.environment})
    return {"request": _view(row), "created": True}


def list_for_sheet(ctx, visit_sheet_id: int) -> dict:
    agency_id = ctx.require_agency()
    with core_cursor(commit=True) as (_, cur):
        _installed(cur)
        _visible_sheet(cur, ctx, visit_sheet_id)
        cur.execute(f"SELECT {COLUMNS} FROM signature_requests WHERE agency_id = %s AND visit_sheet_id = %s "
                    "ORDER BY created_at DESC, id DESC FOR UPDATE", (agency_id, visit_sheet_id))
        rows = [_expire_if_due(cur, dict(r)) for r in cur.fetchall()]
    return {"items": [_view(r) for r in rows]}


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
        return _view(work(cur, row))


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

    def work(cur, locked):
        return _transition(cur, locked, "cancelled", f"operator:{ctx.user_id}")
    return _act(ctx, request_id, work)


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
# BLOCCO-5: eventi del fornitore (webhook), indipendenti dal fornitore
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


def handle_provider_event(provider_name: str, headers: dict, raw_body: bytes) -> dict:
    """Un evento del fornitore: autenticita' verificata dall'adattatore sul
    corpo grezzo, una sola applicazione per id evento, stato RILETTO dal
    fornitore (il contenuto dell'evento non decide mai da solo), transizione
    sotto lock. Il documento firmato e l'audit trail si scaricano e si
    conservano con la loro impronta. Mai per l'adattatore simulato."""
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
        cur.execute(f"SELECT {COLUMNS} FROM signature_requests WHERE provider = %s AND provider_reference = %s",
                    (provider_name, event.reference))
        found = cur.fetchone()
    if found is None:
        with core_cursor(commit=True) as (_, cur):
            fresh = _record_event(cur, provider_name, event, raw_body, request=None, outcome="unknown_request")
        return {"outcome": "unknown_request" if fresh else "duplicate"}
    found = dict(found)
    remote = provider.fetch_status(found["provider_reference"])               # fuori transazione
    target = PROVIDER_TO_STATUS.get(remote.status)
    document = audit = None
    if target == "signed":
        document = provider.fetch_document(found["provider_reference"], document_sha256=found["document_sha256"],
                                           signer_name=found["signer_name"]).content
        trail = provider.fetch_audit_trail(found["provider_reference"])
        audit = trail.content if trail is not None else None
    with core_cursor(commit=True) as (_, cur):
        row = _lock(cur, found["agency_id"], found["id"])
        if target is None or row["status"] != "sent":
            outcome = "no_change" if target is None else "status_mismatch"
            fresh = _record_event(cur, provider_name, event, raw_body, request=row, outcome=outcome,
                                  detail=f"provider={remote.status} local={row['status']}")
            return {"outcome": outcome if fresh else "duplicate"}
        if not _record_event(cur, provider_name, event, raw_body, request=row, outcome="applied",
                             detail=f"provider={remote.status}"):
            return {"outcome": "duplicate"}
        if target == "signed":
            if remote.signed_at is None or remote.signature_level not in ("ses", "aes", "qes"):
                raise Conflict("SIGNED_WITHOUT_EVIDENCE", "Il fornitore non ha indicato data e livello della firma.")
            new = _transition(cur, row, "signed", f"provider:{provider_name}", document=document,
                              details={"event_id": event.event_id, "environment": row.get("provider_environment")},
                              extra={"signature_level": remote.signature_level, "signed_at": remote.signed_at,
                                     "audit_trail": audit,
                                     "audit_trail_sha256": hashlib.sha256(audit).hexdigest() if audit else None})
        else:
            new = _transition(cur, row, target, f"provider:{provider_name}", details={"event_id": event.event_id})
    return {"outcome": "applied", "status": new["status"]}


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
    return {"data": data, "filename": f"rapporto-firma-richiesta-{request_id}.pdf"}
