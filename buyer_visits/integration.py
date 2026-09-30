"""A31-2 - gli hook che `appointments/service.py` chiama nella SUA transazione.

Contratto congelato da A31-1 (D1-D8), per un appuntamento
`appointment_type = 'buyer_visit'` con `property_id` valorizzato:

  CREATE requested                -> nessuna proiezione (D1)
  CREATE scheduled/confirmed      -> INSERT (se non esiste gia')
  SCHEDULE / CONFIRM              -> INSERT se assente, altrimenti lo stato
  RESCHEDULE (riga nuova)         -> la STESSA riga `property_visits` passa al
                                     successore: appointment_id, scheduled_at,
                                     stato; snapshot se cambia l'agente
  REASSIGN                        -> solo lo snapshot `assigned_to` (D3)
  CANCEL / COMPLETE / NO_SHOW     -> solo lo stato (+ updated_at, FLOW-R007)
  PATCH contact/lead              -> contact_id / lead_id
  PATCH property                  -> vietato se proiettata (D5, 409); aggiungere
                                     l'immobile a una visita fissata la proietta

L'identita' dell'agente resta `appointments.assigned_user_id`: `assigned_to`
e' SOLO un'etichetta di visualizzazione, non si rilegge mai.
"""
from __future__ import annotations

from appointments import repository as _appuntamenti

from . import errors, projection

TIPO = "buyer_visit"
#: Gli stati in cui una visita acquirente NASCE come proiezione.
STATI_DI_NASCITA = ("scheduled", "confirmed")


def is_projectable(row: dict) -> bool:
    return row.get("appointment_type") == TIPO and row.get("property_id") is not None


def _snapshot_agente(cur, agency_id: int, user_id):
    """D3: il nome come lo mostra l'Agenda (`appointments.repository.agent_name`,
    la stessa espressione dell'elenco agenti e dei follow-up A30-8)."""
    if user_id is None:
        return None
    return _appuntamenti.agent_name(cur, agency_id, user_id)


def _autore(actor_user_id):
    """Convenzione P26-5 per i campi di testo d'audit legacy: `operator:<id>`,
    mai l'email (`operator_auth.dependencies.audit_actor`)."""
    return None if actor_user_id is None else f"operator:{int(actor_user_id)}"


def _nasce(cur, agency_id: int, row: dict, *, actor_user_id):
    return projection.insert(
        cur, appointment=row, status=row["status"],
        assigned_to=_snapshot_agente(cur, agency_id, row["assigned_user_id"]),
        created_by=_autore(actor_user_id))


def _coerente(row: dict, visita: dict) -> None:
    if visita["property_id"] != row["property_id"]:
        raise errors.BuyerVisitProjectionInvalid(
            "La visita collegata non corrisponde piu' a questo appuntamento")


# ---------------------------------------------------------------------------
# HOOK
# ---------------------------------------------------------------------------

def on_create(cur, agency_id: int, row: dict, *, actor_user_id) -> None:
    """Dopo l'INSERT di un appuntamento (mai per una replica idempotente)."""
    if not is_projectable(row) or row["status"] not in STATI_DI_NASCITA:
        return
    if projection.for_appointment(cur, row["id"]) is None:
        _nasce(cur, agency_id, row, actor_user_id=actor_user_id)


def on_status(cur, agency_id: int, row: dict, *, actor_user_id) -> None:
    """Dopo SCHEDULE, CONFIRM, CANCEL, COMPLETE, NO_SHOW (riga gia' scritta)."""
    if not is_projectable(row):
        return
    visita = projection.for_appointment(cur, row["id"])
    if visita is None:
        if row["status"] in STATI_DI_NASCITA:
            _nasce(cur, agency_id, row, actor_user_id=actor_user_id)
        return
    _coerente(row, visita)
    cambi = {}
    if visita["status"] != row["status"]:
        cambi["status"] = row["status"]
    if visita["scheduled_at"] != row["start_at"]:
        cambi["scheduled_at"] = row["start_at"]
    if row["status"] in STATI_DI_NASCITA:
        # SCHEDULE fissa (o ri-fissa) anche l'agente: lo snapshot lo segue.
        nome = _snapshot_agente(cur, agency_id, row["assigned_user_id"])
        if visita["assigned_to"] != nome:
            cambi["assigned_to"] = nome
    if cambi:
        projection.update(cur, visita["id"], cambi)


def on_reschedule(cur, agency_id: int, old_row: dict, new_row: dict, *, actor_user_id) -> None:
    """La riga vecchia e' `rescheduled`, la nuova e' `scheduled` con
    `rescheduled_from_id = old_row.id`: la STESSA visita passa alla nuova (la
    guardia 078 lo ammette solo verso il successore diretto)."""
    if not is_projectable(old_row):
        return
    visita = projection.for_appointment(cur, old_row["id"])
    if visita is None:
        on_create(cur, agency_id, new_row, actor_user_id=actor_user_id)
        return
    _coerente(new_row, visita)
    cambi = {"appointment_id": new_row["id"], "scheduled_at": new_row["start_at"],
             "status": new_row["status"]}
    nome = _snapshot_agente(cur, agency_id, new_row["assigned_user_id"])
    if visita["assigned_to"] != nome:
        cambi["assigned_to"] = nome
    projection.update(cur, visita["id"], cambi)


def on_reassign(cur, agency_id: int, row: dict) -> None:
    """D3: solo lo snapshot di visualizzazione."""
    if not is_projectable(row):
        return
    visita = projection.for_appointment(cur, row["id"])
    if visita is None:
        return
    nome = _snapshot_agente(cur, agency_id, row["assigned_user_id"])
    if visita["assigned_to"] != nome:
        projection.update(cur, visita["id"], {"assigned_to": nome})


def before_patch(cur, agency_id: int, row: dict, changes: dict) -> None:
    """D5: l'immobile di una visita gia' proiettata non si cambia ne' si toglie.
    Chiamato PRIMA dell'UPDATE: niente viene scritto se rifiuta."""
    if not is_projectable(row) or "property_id" not in changes:
        return
    if changes["property_id"] == row["property_id"]:
        return
    if projection.for_appointment(cur, row["id"]) is not None:
        raise errors.BuyerVisitPropertyLocked(
            "L'immobile di una visita gia' fissata non si puo' cambiare: "
            "annulla la visita e creane una nuova")


def on_patch(cur, agency_id: int, old_row: dict, new_row: dict, *, actor_user_id) -> None:
    """Dopo l'UPDATE di un PATCH: allinea contatto e lead; un immobile appena
    aggiunto a una visita fissata la proietta."""
    if not (is_projectable(old_row) or is_projectable(new_row)):
        return
    visita = projection.for_appointment(cur, new_row["id"])
    if visita is None:
        if is_projectable(new_row) and new_row["status"] in STATI_DI_NASCITA:
            _nasce(cur, agency_id, new_row, actor_user_id=actor_user_id)
        return
    _coerente(new_row, visita)
    cambi = {c: new_row[c] for c in ("contact_id", "lead_id") if visita[c] != new_row[c]}
    if cambi:
        projection.update(cur, visita["id"], cambi)
