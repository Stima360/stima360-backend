"""BLOCCO-5A - le scritture dell'Agenda per la prenotazione online di una VISITA.

Il solo punto che crea una visita (`buyer_visit`) da un link pubblico
d'immobile (`property_visit_booking_links`, migrazione 120). Vive qui, nel
modulo dell'Agenda, perche' l'Agenda resta l'unica a scrivere `appointments`:
stessi lock d'agente, stessa regola degli orari pubblici, stesso controllo dei
conflitti, stesso vincolo EXCLUDE, stessa proiezione sull'immobile
(`buyer_visits.integration`) e stessa marcatura Google di ogni altra scrittura.

Differenze rispetto ai link generici (`create_public_booking_appointment`, che
continua a NON prenotare visite):

    * la visita e' collegata all'immobile del link, che deve avere un
      incarico REALE e IN CORSO (`core.property_mandate`, stato `active`:
      un incarico scaduto, concluso o ritirato non si prenota online);
    * l'autorizzazione del proprietario e' quella registrata sul link:
      `preauthorized` -> visita `scheduled` (confermata); `per_visit` -> visita
      `requested`, MAI confermata da qui: la conferma e' un'azione
      dell'operatore in Agenda dopo l'assenso del proprietario;
    * una richiesta in attesa occupa comunque l'orario per gli altri
      prenotanti dello stesso agente (nessuna doppia richiesta sullo stesso
      orario), anche se il vincolo EXCLUDE ignora le `requested`;
    * il tempo di spostamento del link e' il buffer prima e dopo la visita.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.property_mandate import mandate_origin, mandate_state, signed_link_mandate
from operator_auth.context import SystemAgencyContext

from . import availability, errors, repository, working_hours
from . import working_hours_repository as _wh_repository
from .enums import VISIT_TYPE
from .service import _GCAL_PRESENT, _contatto_pubblico_coerente, _gcal, _in_transazione, _visite, cancel_locked

SOURCE = "booking_link"
PUBLIC_MANDATE_STATE = "active"
VISIT_STATUS = {"preauthorized": "scheduled", "per_visit": "requested"}


def _require_ctx(ctx) -> int:
    if type(ctx) is not SystemAgencyContext or ctx.origin != "public_booking":
        raise TypeError("la prenotazione di una visita richiede un SystemAgencyContext(origin='public_booking')")
    return ctx.require_agency()


def mandate_open(cur, agency_id: int, property_id: int) -> bool:
    """L'immobile ha un incarico reale, fuori dal Cestino, IN CORSO."""
    immobile = repository.sale_mandate_property(cur, agency_id, property_id)
    if immobile is None or immobile["deleted_at"] is not None:
        return False
    immobile["signed_mandate"] = signed_link_mandate(cur, property_id)
    return mandate_origin(immobile) is not None and mandate_state(immobile) == PUBLIC_MANDATE_STATE


def _requested_busy(cur, *, assigned_user_id, date_from, date_to, exclude_appointment_id=None):
    cur.execute(
        "SELECT lower(blocked_range) AS b_start, upper(blocked_range) AS b_end FROM appointments "
        "WHERE assigned_user_id = %s AND status = 'requested' AND appointment_type = %s AND source = %s "
        "AND blocked_range && tstzrange(%s, %s, '[)') AND (%s::bigint IS NULL OR id <> %s::bigint) ORDER BY 1",
        (assigned_user_id, VISIT_TYPE, SOURCE, date_from, date_to, exclude_appointment_id, exclude_appointment_id))
    return [(r["b_start"], r["b_end"]) for r in cur.fetchall()]


def bookable_slots(cur, link: dict, date_from: datetime, date_to: datetime, *, now: datetime,
                   exclude_appointment_id=None) -> list[dict]:
    """Gli orari PRENOTABILI del link nella finestra: dentro l'orario pubblico
    dell'agente (senza orario configurato: nessuno), dopo il preavviso, entro
    l'orizzonte, liberi da appuntamenti e richieste di visita, col tempo di
    spostamento prima e dopo. Solo inizio e fine: nient'altro esce."""
    agency_id, user_id = link["agency_id"], link["assigned_user_id"]
    earliest = now + timedelta(minutes=int(link["min_notice_minutes"]))
    latest = now + timedelta(days=int(link["horizon_days"]))
    start, end = max(date_from, earliest), min(date_to, latest)
    if end <= start or not repository.active_membership(cur, agency_id, user_id):
        return []
    ingressi = _wh_repository.effective_inputs(
        cur, agency_id, user_id,
        (start.astimezone(working_hours.ROMA) - timedelta(days=1)).date(),
        (end.astimezone(working_hours.ROMA) + timedelta(days=1)).date())
    if not ingressi["has_weekly_config"]:
        return []
    effettiva = working_hours.effective_windows(start, end, **ingressi)
    occupato = (repository.busy_intervals(cur, assigned_user_id=user_id, date_from=start - timedelta(days=1),
                                          date_to=end + timedelta(days=1),
                                          exclude_appointment_id=exclude_appointment_id)
                + _requested_busy(cur, assigned_user_id=user_id, date_from=start - timedelta(days=1),
                                  date_to=end + timedelta(days=1), exclude_appointment_id=exclude_appointment_id))
    travel = int(link["travel_minutes"])
    tutti = availability.slots(start, end, duration=int(link["duration_minutes"]), step=int(link["step_minutes"]),
                               busy=occupato, buffer_before=travel, buffer_after=travel)
    return [{"start_at": s["start_at"], "end_at": s["end_at"]} for s in tutti
            if s["available"] and working_hours.is_within(s["start_at"], s["end_at"], effettiva)]


def _check_slot(cur, link: dict, start_at: datetime, *, exclude_appointment_id=None) -> datetime:
    """Il controllo del SERVER, sotto il lock dell'agente: l'orario scelto deve
    essere uno degli orari prenotabili calcolati adesso. Restituisce la fine."""
    now = repository.db_now(cur)
    window_start = start_at - timedelta(hours=1)
    window_end = start_at + timedelta(minutes=int(link["duration_minutes"])) + timedelta(hours=1)
    ammessi = bookable_slots(cur, link, window_start, window_end, now=now,
                             exclude_appointment_id=exclude_appointment_id)
    for slot in ammessi:
        if slot["start_at"] == start_at:
            return slot["end_at"]
    raise errors.PublicSlotUnavailable("Questo orario non è più disponibile: scegline un altro")


def create_visit(ctx, *, link: dict, start_at: datetime, contact_data: dict, source_record_id: str,
                 replaces_appointment_id: int | None = None, cancel_reason: str | None = None,
                 after_insert=None) -> dict:
    """Crea la visita (o la riprogramma: annulla quella vecchia e crea la nuova
    nella STESSA transazione). Idempotente su `source_record_id`.
    `after_insert(cur, riga)` scrive, nella stessa transazione, cio' che il
    chiamante lega alla visita (la riga di `property_visit_bookings`)."""
    agency_id = _require_ctx(ctx)
    user_id = link["assigned_user_id"]
    travel = int(link["travel_minutes"])
    status = VISIT_STATUS[link["owner_authorization"]]

    def _lavoro(cur):
        esistente = repository.find_by_source_key(cur, SOURCE, source_record_id)
        if esistente is not None:
            return repository.get_appointment(cur, agency_id, esistente["id"])
        vecchia = None
        if replaces_appointment_id is not None:
            # ordine dei lock: riga dell'appuntamento -> agente (come `_su_riga`)
            vecchia = repository.lock_appointment(cur, agency_id, replaces_appointment_id)
            if vecchia is None or vecchia["status"] not in ("requested", "scheduled", "confirmed"):
                raise errors.PublicSlotUnavailable("Questa visita non si può più riprogrammare")
        if not repository.active_membership(cur, agency_id, user_id):
            raise errors.PublicSlotUnavailable("Questo collegamento non è più associato a un agente attivo")
        if not mandate_open(cur, agency_id, link["property_id"]):
            raise errors.PublicSlotUnavailable("Le visite di questo immobile non sono prenotabili online")
        repository.lock_agents(cur, [user_id])
        end_at = _check_slot(cur, link, start_at, exclude_appointment_id=replaces_appointment_id)
        if repository.find_conflicts(cur, assigned_user_id=user_id, start_at=start_at, end_at=end_at,
                                     buffer_before_minutes=travel, buffer_after_minutes=travel,
                                     exclude_appointment_id=replaces_appointment_id):
            raise errors.PublicSlotUnavailable("Questo orario non è più disponibile: scegline un altro")
        if vecchia is not None:
            cancel_locked(cur, agency_id, None, vecchia, reason=cancel_reason or "Riprogrammata dal cliente",
                          kind="client")
            contact_id = vecchia["contact_id"]
        else:
            contact_id, _creato = _contatto_pubblico_coerente(cur, agency_id, contact_data)
        valori = {
            "agency_id": agency_id, "assigned_user_id": user_id, "appointment_type": VISIT_TYPE,
            "status": status, "start_at": start_at, "end_at": end_at,
            "buffer_before_minutes": travel, "buffer_after_minutes": travel,
            "contact_id": contact_id, "property_id": link["property_id"],
            "source": SOURCE, "source_record_id": source_record_id, "created_by_user_id": None,
            "rescheduled_from_id": replaces_appointment_id,
        }
        riga = repository.insert_appointment(cur, valori, actor_user_id=None)
        if riga is None:
            esistente = repository.find_by_source_key(cur, SOURCE, source_record_id)
            return repository.get_appointment(cur, agency_id, esistente["id"])
        # stessa visita in Agenda e sull'immobile (proiezione `property_visits`)
        _visite.on_create(cur, agency_id, riga, actor_user_id=None)
        if riga["status"] in _GCAL_PRESENT:
            _gcal.on_appointment_mutation(cur, agency_id, riga["id"])
        if after_insert is not None:
            after_insert(cur, riga)
        return riga

    return _in_transazione(_lavoro)


def cancel_visit(ctx, *, appointment_id: int, reason: str) -> dict | None:
    """Annullamento chiesto dal cliente col suo collegamento di gestione."""
    agency_id = _require_ctx(ctx)

    def _lavoro(cur):
        riga = repository.lock_appointment(cur, agency_id, appointment_id)
        if riga is None or riga["status"] not in ("requested", "scheduled", "confirmed"):
            return None
        return cancel_locked(cur, agency_id, None, riga, reason=reason, kind="client")

    return _in_transazione(_lavoro)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
