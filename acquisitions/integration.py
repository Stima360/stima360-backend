"""CRM-OPS-3 - gli hook che `appointments/service.py` chiama nella SUA
transazione, accanto a quelli delle visite acquirente (A31-2).

Solo per un appuntamento `seller_meeting` che e' l'appuntamento di
un'acquisizione (`acquisitions.appointment_id`). Tutti gli altri: nessun
effetto, nessuna lettura oltre il tipo.

  RESCHEDULE (riga nuova)  -> `appointment_id` passa al successore, evento
                              `appointment_rescheduled`. L'acquisizione non
                              cambia stato: l'Agenda resta la sola fonte di
                              data, ora, durata, agente e stato.
  COMPLETE                 -> `appointment_set` -> `inspection_done` (mai
                              indietro, mai da altri stati), evento
                              `appointment_completed`
  CANCEL / NO_SHOW         -> solo l'evento: l'acquisizione resta aperta e
                              puo' ricevere un nuovo appuntamento
  SCHEDULE / CONFIRM       -> nulla
  REASSIGN                 -> nulla: l'agente dell'acquisizione e' un dato
                              suo (visibilita'), l'agente dell'appuntamento
                              e' dell'Agenda
  PATCH property           -> vietato (409) sull'appuntamento di
                              un'acquisizione, prima di qualunque scrittura

Ordine dei lock: la riga dell'appuntamento (gia' bloccata dall'Agenda) ->
gli agenti (se l'azione li blocca) -> l'acquisizione. Nessun ciclo: chi
scrive un'acquisizione non attende mai una riga dell'Agenda dopo averla
bloccata (vedi repository.py).

Nessuna connessione, nessun commit; se qualcosa fallisce l'eccezione risale
e la mutazione dell'Agenda si annulla tutta.

Su un database senza la 081 (`acquisitions` assente) gli hook non fanno
nulla: l'Agenda resta quella di prima, finche' la migration non c'e'.
"""
from __future__ import annotations

from . import errors, repository
from .enums import AGENDA_COMPLETED_ADVANCES, APPOINTMENT_TYPE

_EVENTO_PER_STATO = {
    "completed": "appointment_completed",
    "cancelled": "appointment_cancelled",
    "no_show": "appointment_no_show",
}


def _pertinente(cur, row) -> bool:
    """Solo un `seller_meeting`, e solo se la 081 e' applicata: per ogni altro
    tipo nemmeno la domanda al catalogo."""
    return (row is not None and row.get("appointment_type") == APPOINTMENT_TYPE
            and repository.installed(cur))


def on_status(cur, agency_id: int, row: dict, *, actor_user_id) -> None:
    """Dopo SCHEDULE, CONFIRM, CANCEL, COMPLETE, NO_SHOW (riga gia' scritta)."""
    evento = _EVENTO_PER_STATO.get(row["status"])
    if evento is None or not _pertinente(cur, row):
        return
    acq = repository.lock_by_appointment(cur, agency_id, row["id"])
    if acq is None:
        return
    prima = acq["status"]
    dopo = prima
    if row["status"] == "completed":
        dopo = AGENDA_COMPLETED_ADVANCES.get(prima, prima)
        if dopo != prima:
            repository.update_acquisition(cur, acq["id"], {"status": dopo})
    repository.record_event(
        cur, agency_id=agency_id, acquisition_id=acq["id"], event_type=evento,
        from_status=prima, to_status=dopo, actor_user_id=actor_user_id,
        changes={"appointment_id": row["id"], "appointment_status": row["status"]})


def on_reschedule(cur, agency_id: int, old_row: dict, new_row: dict, *, actor_user_id) -> None:
    """La riga vecchia e' `rescheduled`, la nuova la sostituisce: la STESSA
    acquisizione la segue, nella stessa transazione. Nessun ciclo: qui non
    si chiama mai l'Agenda."""
    if not _pertinente(cur, old_row):
        return
    acq = repository.lock_by_appointment(cur, agency_id, old_row["id"])
    if acq is None:
        return
    repository.update_acquisition(cur, acq["id"], {"appointment_id": new_row["id"]})
    repository.record_event(
        cur, agency_id=agency_id, acquisition_id=acq["id"],
        event_type="appointment_rescheduled", from_status=acq["status"],
        to_status=acq["status"], actor_user_id=actor_user_id,
        changes={"old_appointment_id": old_row["id"], "new_appointment_id": new_row["id"],
                 "start_at": new_row["start_at"], "assigned_user_id": new_row["assigned_user_id"]})


def before_patch(cur, agency_id: int, row: dict, changes: dict) -> None:
    """L'immobile dell'appuntamento di un'acquisizione non si cambia ne' si
    toglie dall'Agenda. Chiamato PRIMA dell'UPDATE."""
    if "property_id" not in changes or changes["property_id"] == row["property_id"]:
        return
    if not _pertinente(cur, row):
        return
    if repository.find_by_appointment(cur, agency_id, row["id"]) is not None:
        raise errors.AppointmentLinkedToAcquisition(
            "Questo appuntamento appartiene a un'acquisizione: l'immobile non si puo' cambiare")
