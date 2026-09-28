"""A30-12 - il servizio del booking pubblico: CRUD operatore dei link (D2),
e le tre rotte pubbliche (metadata, slot, submit).

PERMESSI OPERATORE (D2): owner/admin (`ctx.may_assign_records`) gestiscono i
link di QUALUNQUE agente attivo dell'agenzia; un `agent` gestisce SOLO il
proprio. Stessa forma di `appointments.working_hours_service._controlla_target`.

PRIVACY PUBBLICA: ogni funzione `public_*` restituisce SOLO l'elenco
esplicito di campi che l'API pubblica ammette (mai un `dict(riga)` grezzo)
e traduce QUALUNQUE causa di rifiuto - token inesistente, disabilitato,
scaduto, revocato - nella STESSA eccezione `LinkNotBookable`: il chiamante
pubblico non deve mai poter distinguere questi casi (D privacy).
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone

from core.database import core_cursor
from core.exceptions import NotFoundError, ValidationError, PermissionDenied
from core.normalization import normalize_email, normalize_phone
from operator_auth.context import SystemAgencyContext

from appointments import availability as _availability
from appointments import errors as _appt_errors
from appointments import repository as _appt_repository
from appointments import working_hours as _working_hours
from appointments import working_hours_repository as _wh_repository
from appointments.enums import APPOINTMENT_TYPE_LABELS_IT
from appointments.service import create_public_booking_appointment

from . import repository, security

PUBLIC_BOOKING_ORIGIN = "public_booking"

#: D9: default conservativi, configurabili via env. Una finestra fissa di 60
#: secondi per ciascun budget: semplice, DB-backed, nessuna dipendenza
#: nuova.
RATE_LIMIT_WINDOW_SECONDS = 60
DEFAULT_RATE_LIMITS = {
    "get_link": 120,
    "get_ip": 30,
    "post_link": 30,
    "post_ip": 10,
}


def _limit(scope: str) -> int:
    env_var = f"PUBLIC_BOOKING_RATE_LIMIT_{scope.upper()}"
    valore = os.environ.get(env_var)
    if valore is not None:
        try:
            return int(valore)
        except ValueError:
            pass
    return DEFAULT_RATE_LIMITS[scope]


def _window_start(now: datetime) -> datetime:
    epoch = int(now.timestamp())
    finestra = epoch - (epoch % RATE_LIMIT_WINDOW_SECONDS)
    return datetime.fromtimestamp(finestra, tz=timezone.utc)


class RateLimited(Exception):
    """D9: superamento di un budget GET o POST. Il router traduce SEMPRE
    questa eccezione nella stessa risposta pubblica neutra."""


class LinkNotBookable(Exception):
    """D privacy: token inesistente, disabilitato, scaduto o revocato -
    UNA sola eccezione per tutti e quattro i casi, cosi' il router non puo'
    accidentalmente far trapelare quale fosse."""


class SubmissionPayloadMismatch(Exception):
    """D7: stesso submission_token, payload diverso - rifiuto
    deterministico, distinto da `LinkNotBookable` (qui non c'e' nulla da
    nascondere sull'ESISTENZA del link: il client ha gia' la pagina di
    booking aperta)."""


# ---------------------------------------------------------------------------
# permessi (D2) - stessa forma di working_hours_service._controlla_target
# ---------------------------------------------------------------------------

def _attore(ctx) -> int:
    user_id = getattr(ctx, "user_id", None)
    if user_id is None:
        raise PermissionDenied("La gestione dei link di booking richiede una sessione operatore")
    return int(user_id)


def _puo_gestire_tutti(ctx) -> bool:
    return bool(getattr(ctx, "may_assign_records", False))


def _controlla_target(ctx, cur, agency_id, target_user_id):
    if not _puo_gestire_tutti(ctx) and int(target_user_id) != _attore(ctx):
        raise PermissionDenied("Un agente puo' gestire solo i propri link di booking")
    if not _appt_repository.active_membership(cur, agency_id, target_user_id):
        raise _appt_errors.AgentNotActive(
            "L'agente indicato non e' un membro attivo di questa agenzia")


def _controlla_proprietario_link(ctx, cur, agency_id, link):
    if link is None or link["agency_id"] != agency_id:
        raise NotFoundError("Link di booking non trovato")
    if not _puo_gestire_tutti(ctx) and int(link["assigned_user_id"]) != _attore(ctx):
        raise NotFoundError("Link di booking non trovato")


# ---------------------------------------------------------------------------
# API operatore - CRUD (D2, D6)
# ---------------------------------------------------------------------------

def create_link(ctx, body) -> dict:
    agency_id = ctx.require_agency()
    actor = _attore(ctx)
    with core_cursor(commit=True) as (_, cur):
        _controlla_target(ctx, cur, agency_id, body.assigned_user_id)
        raw_token = security.generate_token()
        riga = repository.create_link(
            cur, agency_id=agency_id, assigned_user_id=body.assigned_user_id,
            token_hash=security.hash_token(raw_token), label=body.label,
            appointment_type=body.appointment_type, duration_minutes=body.duration_minutes,
            buffer_before_minutes=body.buffer_before_minutes,
            buffer_after_minutes=body.buffer_after_minutes,
            created_by_user_id=actor, expires_at=body.expires_at)
    return {**_link_publica_operatore(riga), "token": raw_token}


def list_links(ctx) -> list[dict]:
    agency_id = ctx.require_agency()
    with core_cursor() as (_, cur):
        target = None if _puo_gestire_tutti(ctx) else _attore(ctx)
        righe = repository.list_links(cur, agency_id=agency_id, assigned_user_id=target)
    return [_link_publica_operatore(r) for r in righe]


def patch_link(ctx, link_id: int, body) -> dict:
    agency_id = ctx.require_agency()
    with core_cursor(commit=True) as (_, cur):
        link = repository.get_link(cur, agency_id=agency_id, link_id=link_id)
        _controlla_proprietario_link(ctx, cur, agency_id, link)
        cambi = {}
        for campo in ("label", "appointment_type", "duration_minutes",
                      "buffer_before_minutes", "buffer_after_minutes", "expires_at"):
            valore = getattr(body, campo, None)
            if valore is not None:
                cambi[campo] = valore
        riga = repository.update_link_fields(cur, agency_id=agency_id, link_id=link_id,
                                             changes=cambi)
    return _link_publica_operatore(riga)


def rotate_link(ctx, link_id: int) -> dict:
    agency_id = ctx.require_agency()
    with core_cursor(commit=True) as (_, cur):
        link = repository.get_link(cur, agency_id=agency_id, link_id=link_id)
        _controlla_proprietario_link(ctx, cur, agency_id, link)
        raw_token = security.generate_token()
        riga = repository.rotate_token(cur, agency_id=agency_id, link_id=link_id,
                                       new_token_hash=security.hash_token(raw_token))
    return {**_link_publica_operatore(riga), "token": raw_token}


def disable_link(ctx, link_id: int) -> dict:
    agency_id = ctx.require_agency()
    with core_cursor(commit=True) as (_, cur):
        link = repository.get_link(cur, agency_id=agency_id, link_id=link_id)
        _controlla_proprietario_link(ctx, cur, agency_id, link)
        riga = repository.set_status(cur, agency_id=agency_id, link_id=link_id, status="disabled")
    return _link_publica_operatore(riga)


#: D6: il raw token esce SOLO da create_link/rotate_link (sopra), mai da qui.
def _link_publica_operatore(riga: dict) -> dict:
    return {
        "id": riga["id"], "agency_id": riga["agency_id"],
        "assigned_user_id": riga["assigned_user_id"], "label": riga["label"],
        "status": riga["status"], "appointment_type": riga["appointment_type"],
        "duration_minutes": riga["duration_minutes"],
        "buffer_before_minutes": riga["buffer_before_minutes"],
        "buffer_after_minutes": riga["buffer_after_minutes"],
        "created_at": riga["created_at"], "updated_at": riga["updated_at"],
        "expires_at": riga["expires_at"], "revoked_at": riga["revoked_at"],
    }


# ---------------------------------------------------------------------------
# API pubblica - risoluzione del token e rate limit (D9, D privacy)
# ---------------------------------------------------------------------------

def _resolve_bookable_link(token: str) -> dict:
    """D privacy: token inesistente, disabilitato, scaduto o revocato sono
    UN solo esito per il chiamante pubblico - `LinkNotBookable`, sempre la
    stessa eccezione, mai un messaggio che riveli quale dei quattro."""
    token_hash = security.hash_token(token)
    with core_cursor() as (_, cur):
        link = repository.find_link_by_token_hash(cur, token_hash)
    if link is None:
        raise LinkNotBookable("token")
    if link["status"] != "active":
        raise LinkNotBookable("status")
    if link["expires_at"] is not None and link["expires_at"] <= datetime.now(timezone.utc):
        raise LinkNotBookable("expired")
    if link["revoked_at"] is not None:
        raise LinkNotBookable("revoked")
    return link


def _check_rate_limit(*, link_id: int, scope: str, client_ip_hash: str | None) -> None:
    """D9: budget separati GET/POST, e per-link / per-(link+IP). Chiamata
    due volte per un GET (get_link, get_ip) e due volte per un POST
    (post_link, post_ip) - ciascuna nella propria finestra fissa."""
    ora = datetime.now(timezone.utc)
    finestra = _window_start(ora)
    ip_hash = client_ip_hash if scope.endswith("_ip") else ""
    with core_cursor(commit=True) as (_, cur):
        ammesso = repository.increment_and_check(
            cur, link_id=link_id, scope=scope, window_start=finestra,
            limit=_limit(scope), client_ip_hash=ip_hash)
    if not ammesso:
        raise RateLimited(scope)


def _gate_get(link_id: int, client_ip_hash: str) -> None:
    _check_rate_limit(link_id=link_id, scope="get_link", client_ip_hash=None)
    _check_rate_limit(link_id=link_id, scope="get_ip", client_ip_hash=client_ip_hash)


def _gate_post(link_id: int, client_ip_hash: str) -> None:
    _check_rate_limit(link_id=link_id, scope="post_link", client_ip_hash=None)
    _check_rate_limit(link_id=link_id, scope="post_ip", client_ip_hash=client_ip_hash)


# ---------------------------------------------------------------------------
# GET /api/public/booking/{token} - metadata, NESSUNA scrittura (D privacy)
# ---------------------------------------------------------------------------

def get_public_link_info(token: str, *, client_ip: str) -> dict:
    client_ip_hash = security.hash_ip(client_ip)
    link = _resolve_bookable_link(token)
    _gate_get(link["id"], client_ip_hash)
    with core_cursor() as (_, cur):
        nomi = repository.public_names(cur, agency_id=link["agency_id"],
                                       user_id=link["assigned_user_id"])
    # D7: generato QUI, SENZA scrivere nulla - solo al submit finisce hashed
    # in public_booking_submissions.
    submission_token = security.generate_token()
    return {
        "agency_name": nomi["agency_name"],
        "agent_name": nomi["agent_name"],
        "appointment_type": link["appointment_type"],
        "appointment_type_label": APPOINTMENT_TYPE_LABELS_IT.get(
            link["appointment_type"], link["appointment_type"]),
        "duration_minutes": link["duration_minutes"],
        "submission_token": submission_token,
    }


# ---------------------------------------------------------------------------
# GET /api/public/booking/{token}/slots - SOLO slot prenotabili (D3, D10)
# ---------------------------------------------------------------------------

def get_public_slots(token: str, *, date_from: datetime, date_to: datetime,
                     client_ip: str) -> dict:
    client_ip_hash = security.hash_ip(client_ip)
    link = _resolve_bookable_link(token)
    _gate_get(link["id"], client_ip_hash)
    if date_to <= date_from:
        raise ValidationError("La finestra deve terminare dopo il suo inizio")
    assigned_user_id = link["assigned_user_id"]
    duration = link["duration_minutes"]
    before = link["buffer_before_minutes"]
    after = link["buffer_after_minutes"]
    with core_cursor() as (_, cur):
        if not _appt_repository.active_membership(cur, link["agency_id"], assigned_user_id):
            return {"slots": []}
        # Il range per `effective_inputs` va ALLARGATO di un giorno per lato
        # (stessa ragione di `appointments.service._finestra_effettiva`):
        # `exception_date`/`closure_date` sono colonne DATE confrontate con
        # `BETWEEN` - una finestra che non attraversa la mezzanotte
        # altrimenti perderebbe silenziosamente un'eccezione/chiusura dello
        # stesso giorno. Il calcolo della finestra effettiva sotto resta
        # invece basato sulla finestra esatta richiesta (`date_from`/`date_to`).
        ingressi = _wh_repository.effective_inputs(
            cur, link["agency_id"], assigned_user_id,
            (date_from.astimezone(_working_hours.ROMA) - timedelta(days=1)).date(),
            (date_to.astimezone(_working_hours.ROMA) + timedelta(days=1)).date())
        # D10 HARD: nessun orario configurato = zero slot pubblici. Questo e'
        # l'INVERSO del ramo permissivo del CRM SOFT (`is_within` con
        # `effective=None` -> sempre vero): qui vince l'esatto opposto.
        if not ingressi["has_weekly_config"]:
            return {"slots": []}
        effettiva = _working_hours.effective_windows(date_from, date_to, **ingressi)
        occupato = _appt_repository.busy_intervals(
            cur, assigned_user_id=assigned_user_id, date_from=date_from, date_to=date_to)
    try:
        tutti = _availability.slots(date_from, date_to, duration=duration, step=15,
                                    busy=occupato, buffer_before=before, buffer_after=after)
    except ValueError as exc:
        if "finestra" in str(exc):
            raise _appt_errors.RangeTooLarge(
                "La finestra e' al massimo di 7 giorni") from exc
        raise ValidationError(str(exc)) from exc
    prenotabili = [
        {"start_at": s["start_at"], "end_at": s["end_at"]}
        for s in tutti
        if s["available"] and _working_hours.is_within(s["start_at"], s["end_at"], effettiva)
    ]
    return {"slots": prenotabili}


# ---------------------------------------------------------------------------
# POST /api/public/booking/{token}/submit (D1, D4-D8, D10)
# ---------------------------------------------------------------------------

def _payload_fingerprint(*, start_at: datetime, name: str, phone_normalized: str | None,
                         email_normalized: str | None) -> str:
    canonico = json.dumps({
        "start_at": start_at.astimezone(timezone.utc).isoformat(),
        "name": name.strip(),
        "phone": phone_normalized,
        "email": email_normalized,
    }, sort_keys=True)
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


def submit_booking(token: str, body, *, client_ip: str) -> dict:
    client_ip_hash = security.hash_ip(client_ip)
    link = _resolve_bookable_link(token)
    _gate_post(link["id"], client_ip_hash)

    try:
        start_at = datetime.fromisoformat(body.start_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError("start_at: data e ora non valide") from exc
    if start_at.tzinfo is None:
        raise ValidationError("start_at: l'orario deve indicare il fuso")

    name = body.name.strip()
    phone = body.phone.strip()
    email = (body.email or "").strip() or None
    if not name or not phone:
        raise ValidationError("Nome e telefono sono obbligatori")
    phone_normalized = normalize_phone(phone)
    email_normalized = normalize_email(email)

    submission_hash = security.hash_token(body.submission_token)
    fingerprint = _payload_fingerprint(
        start_at=start_at, name=name, phone_normalized=phone_normalized,
        email_normalized=email_normalized)

    with core_cursor(commit=True) as (_, cur):
        submission = repository.create_pending_submission(
            cur, link_id=link["id"], submission_hash=submission_hash,
            client_ip_hash=client_ip_hash, payload_fingerprint=fingerprint)

    if submission["payload_fingerprint"] != fingerprint:
        raise SubmissionPayloadMismatch(submission_hash)
    if submission["status"] == "succeeded" and submission["appointment_id"] is not None:
        with core_cursor() as (_, cur):
            riga = _appt_repository.get_appointment(cur, link["agency_id"],
                                                     submission["appointment_id"])
        return _prenotazione_pubblica(riga)

    ctx = SystemAgencyContext(agency_id=link["agency_id"], origin=PUBLIC_BOOKING_ORIGIN)
    contact_data = {
        "contact_type": "person", "first_name": name, "last_name": None,
        "company_name": None, "display_name": name,
        "email": email, "email_normalized": email_normalized,
        "phone": phone, "phone_normalized": phone_normalized,
        "secondary_phone": None, "source": "booking_link", "status": "active",
        "notes": None,
    }
    try:
        riga = create_public_booking_appointment(
            ctx, link=link, submission_hash=submission_hash, start_at=start_at,
            contact_data=contact_data)
    except Exception:
        with core_cursor(commit=True) as (_, cur):
            repository.mark_submission_failed(cur, submission_hash)
        raise
    return _prenotazione_pubblica(riga)


def _prenotazione_pubblica(riga: dict) -> dict:
    """D privacy: SOLO cio' che il client ha bisogno di sapere per
    confermare la propria prenotazione - nessun id interno diverso da
    quanto serve a mostrare "confermato", nessun dato di agenzia/agente/CRM."""
    return {
        "status": riga["status"],
        "start_at": riga["start_at"],
        "end_at": riga["end_at"],
        "appointment_type": riga["appointment_type"],
    }
