"""FIRMA OTP - «Invia per firma»: UN gesto dell'operatore, fatto con i passi che esistono gia'.

Nessuna logica di firma nuova: questo modulo mette in fila funzioni esistenti e aggiunge solo i controlli che servono
perche' il gesto unico resti onesto.

1. Dati del cliente controllati PRIMA di tutto (stessi messaggi della creazione).
2. «Firmo anche io adesso»: la password dell'agente assegnato si verifica PRIMA di creare qualunque cosa; con una
   password sbagliata non parte nessuna email e non nasce nessuna richiesta. Nessuna firma automatica: serve la
   dichiarazione accettata e la password, ogni volta.
3. Mai due richieste aperte per la stessa visita, su nessuna versione del foglio (la scadenza si applica prima).
4. Il foglio: si genera se manca; si RIUSA se i dati non sono cambiati (la generazione e' idempotente). Se i dati
   sono cambiati e l'operatore stava guardando un'altra versione, ci si ferma: «controlla la nuova versione». Un
   documento gia' sottoposto a firma non si modifica mai: ogni versione e' una riga a se'.
5. La richiesta nasce con `otp_service.create` (collegamento personale, email, prove); poi, se chiesto, la firma
   dell'agente con `otp_service.sign_as_agent` (che riverifica password e dichiarazione e scrive le sue prove).
"""
from __future__ import annotations

from core.database import core_cursor
from operator_auth.security import verify_password
from visit_sheets import repository as sheets_repository
from visit_sheets import service as sheets_service

from . import consent, otp_security, otp_service, providers
from . import service as _s


def _da_foglio(exc: sheets_service.VisitSheetError) -> _s.SignatureError:
    """Un errore del foglio visita, con lo stesso codice e lo stesso stato HTTP."""
    errore = _s.SignatureError(exc.code, str(exc))
    errore.status = exc.status
    return errore


def _sheet_ids(cur, agency_id: int, appointment_id: int) -> list[int]:
    cur.execute("SELECT id FROM visit_sheets WHERE agency_id = %s AND appointment_id = %s ORDER BY sheet_version",
                (agency_id, appointment_id))
    return [r["id"] for r in cur.fetchall()]


def send_for_signature(ctx, *, appointment_id: int, known_sheet_id: int | None, signer_name: str, signer_email: str,
                       idempotency_key: str, agent_sign: dict | None = None, ip: str | None = None,
                       user_agent: str | None = None) -> dict:
    agency_id = ctx.require_agency()
    if providers.mode() != providers.OTP_PROVIDER:
        raise _s.Unavailable("OTP_NOT_ACTIVE", "La firma OTP non è attiva su questo servizio.")
    nome = (signer_name or "").strip()
    email = (signer_email or "").strip()
    if not nome:
        raise _s.SignatureError("SIGNER_NAME_REQUIRED", "Indica il nome del cliente che firma.")
    if not otp_security.is_valid_email(email):
        raise _s.SignatureError("SIGNER_EMAIL_REQUIRED", "Serve un indirizzo email valido del cliente.")

    with core_cursor() as (_, cur):
        visit = sheets_repository.visit(cur, agency_id, appointment_id)
        if visit is None or not sheets_service._visible(ctx, visit) or visit["appointment_type"] != "buyer_visit":
            raise _s.NotFound("VISIT_NOT_FOUND", "Visita non trovata.")
        if agent_sign is not None:
            if agent_sign.get("consent_accepted") is not True or agent_sign.get("consent_version") != consent.VERSION:
                raise _s.SignatureError("CONSENT_REQUIRED", "Per firmare devi accettare la dichiarazione di sottoscrizione.")
            if visit["assigned_user_id"] is None or visit["assigned_user_id"] != ctx.user_id:
                raise _s.Forbidden("NOT_THE_AGENT", "Solo l'agente assegnato alla visita può firmare come agente.")
            password = agent_sign.get("password")
            cur.execute("SELECT password_hash FROM operator_users WHERE id = %s AND status = 'active'", (ctx.user_id,))
            stored = cur.fetchone()
            if not isinstance(password, str) or not password or stored is None \
                    or not verify_password(password, stored["password_hash"]):
                raise _s.Forbidden("PASSWORD_INVALID", "Password non valida: non è stato inviato nulla.")
        sheet_ids = _sheet_ids(cur, agency_id, appointment_id)

    # la scadenza si applica alla lettura: prima di cercare richieste aperte, su ogni versione
    for sid in sheet_ids:
        _s.list_for_sheet(ctx, sid)
    with core_cursor() as (_, cur):
        cur.execute("SELECT r.id, r.idempotency_key FROM signature_requests r JOIN visit_sheets s ON s.id = r.visit_sheet_id "
                    "WHERE r.agency_id = %s AND s.appointment_id = %s AND r.status = 'sent'", (agency_id, appointment_id))
        aperte = [dict(r) for r in cur.fetchall()]
    if aperte and not any(str(r["idempotency_key"]) == str(idempotency_key) for r in aperte):
        raise _s.Conflict("REQUEST_OPEN", "Per questa visita c'è già una richiesta di firma in corso.")

    try:
        esito = sheets_service.generate(ctx, appointment_id)
    except sheets_service.VisitSheetError as exc:
        raise _da_foglio(exc) from exc
    sheet = esito["sheet"]
    if known_sheet_id is not None and sheet["id"] != known_sheet_id:
        raise _s.Conflict("SHEET_UPDATED", f"I dati della visita sono cambiati: è pronta la versione {sheet['version']} "
                          "del foglio. Controllala e premi di nuovo «Invia per firma».")

    creato = otp_service.create(ctx, visit_sheet_id=sheet["id"], signer_name=nome, signer_email=email,
                                expires_in_days=7, idempotency_key=idempotency_key, include_agent=True)
    richiesta = creato["request"]
    out = {"request": richiesta, "created": creato["created"], "sheet": sheet, "sheet_created": esito["created"],
           "agent_signed": False, "agent_error": None}
    if agent_sign is not None:
        try:
            firmata = otp_service.sign_as_agent(ctx, richiesta["id"], password=agent_sign["password"],
                                                consent_accepted=True, consent_version=agent_sign["consent_version"],
                                                ip=ip, user_agent=user_agent)
            out["request"], out["agent_signed"] = firmata["request"], True
        except _s.SignatureError as exc:
            if exc.code == "AGENT_DONE":                   # doppio clic: la firma c'e' gia'
                out["agent_signed"] = True
            else:
                out["agent_error"] = str(exc)
    return out
