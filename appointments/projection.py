"""A30-2P - la proiezione appointments -> stima_inspections (LMC-15). ACCESA.

Decisioni: Q1 (varianti LMC-15 sul cursore, senza commit), Q2 (un solo
scrittore logico: Agenda -> appointments -> stima_inspections), D3 (accesa
solo dopo il backfill, nella fase A30-2P).

`PROJECTION_ENABLED` e' True da A30-2P: un sopralluogo legato a una stima si
fissa, si sposta, si completa e si chiude dall'Agenda, e la riga LMC-15
corrispondente e' scritta nella STESSA transazione. L'interruttore resta come
arresto d'emergenza: a False il service torna a rifiutare quelle azioni
(`InspectionProjectionNotActive`) e `stima_inspections` resta scritta solo da
LMC-15 (i test lo provano spegnendolo SOLO dentro il test).

Finche' il router non e' montato in `main.py` e la facade LMC-15 non e'
attiva, nessuna rotta reale passa da qui: LMC-15 resta lo scrittore delle
richieste HTTP esistenti.

Ogni funzione lavora sul cursore dell'Agenda, DENTRO la sua transazione: se
la proiezione fallisce, anche l'appuntamento non e' scritto. Ordine dei lock:
riga appointments -> agenti -> stima -> riga stima_inspections (le varianti
LMC-15 prendono la stima da sole).

MAPPING
  schedule / create fissato   -> create_inspection_in (scheduled_for=start_at);
                                 se la richiesta e' GIA' collegata (backfill
                                 A30-2P) riusa la riga: reschedule_inspection_in
  reschedule (riga nuova)     -> reschedule_inspection_in sulla STESSA riga
                                 LMC-15; il collegamento passa alla riga nuova
  complete                    -> complete_inspection_in (completed_at reale, Q3)
  cancel                      -> cancel_inspection_in (ragione obbligatoria)
  no_show                     -> cancel_inspection_in, ragione 'no_show'
  reassign                    -> nessun effetto (LMC-15 non ha un agente)
"""
from __future__ import annotations

from core.exceptions import ConflictError

from acquisition import repository as lmc15

from . import errors, repository

#: L'interruttore. Acceso in A30-2P, dopo backfill e census (D3); False e'
#: l'arresto d'emergenza.
PROJECTION_ENABLED = True

NO_SHOW_REASON = "no_show"


def is_projectable(appointment_type: str, stima_id) -> bool:
    """Un sopralluogo legato a una stima: il solo caso che LMC-15 conosce."""
    return appointment_type == "inspection" and stima_id is not None


def require_active() -> None:
    if not PROJECTION_ENABLED:
        raise errors.InspectionProjectionNotActive(
            "Un sopralluogo legato a una stima si fissa, si sposta e si chiude "
            "dall'Agenda solo dopo l'attivazione della proiezione su "
            "stima_inspections (A30-2P). Oggi puo' restare una richiesta.")


def _conflitto(exc):
    return errors.ProjectionConflict(
        "Il sopralluogo collegato e' gia' stato aggiornato altrove: ricarica",
        detail_lmc15=str(exc))


def on_schedule(cur, agency_id: int, row: dict, *, actor_user_id: int) -> None:
    """Un sopralluogo diventa fissato (creazione fissata, o requested -> scheduled).

    A30-2P: una richiesta puo' essere GIA' collegata a una riga LMC-15 - e'
    il caso delle righe `scheduled` importate dal backfill, che nell'Agenda
    diventano `requested` perche' LMC-15 non conosce l'agente (Q4). Allora non
    si crea una seconda riga: si riusa la stessa, e se l'orario e' cambiato si
    sposta (`reschedule_inspection_in`). Se LMC-15 l'ha gia' chiusa, e' un
    conflitto leggibile, non una seconda verita'.
    """
    require_active()
    collegata = row.get("stima_inspection_id")
    try:
        if collegata is not None:
            lmc15.reschedule_inspection_in(cur, agency_id, inspection_id=collegata,
                                           scheduled_for=row["start_at"])
            return
        ispezione = lmc15.create_inspection_in(
            cur, agency_id, stima_id=row["stima_id"], scheduled_for=row["start_at"],
            actor_user_id=actor_user_id)
    except ConflictError as exc:
        raise _conflitto(exc) from exc
    repository.set_inspection_link(cur, row["id"], ispezione["id"])


def on_reschedule(cur, agency_id: int, old_row: dict, new_row: dict) -> None:
    require_active()
    ispezione = old_row["stima_inspection_id"]
    if ispezione is None:
        raise errors.ProjectionConflict("Il sopralluogo non ha una riga LMC-15 collegata")
    try:
        lmc15.reschedule_inspection_in(cur, agency_id, inspection_id=ispezione,
                                       scheduled_for=new_row["start_at"])
    except ConflictError as exc:
        raise _conflitto(exc) from exc
    # Il collegamento e' UNICO: prima si stacca dalla vecchia, poi si attacca
    # alla nuova.
    repository.set_inspection_link(cur, old_row["id"], None)
    repository.set_inspection_link(cur, new_row["id"], ispezione)


def _collegata(row: dict, azione: str) -> int:
    """La riga LMC-15 collegata. Senza collegamento non c'e' nulla da
    chiudere: e' un conflitto esplicito, mai una chiamata LMC-15 con id NULL."""
    ispezione = row.get("stima_inspection_id")
    if ispezione is None:
        raise errors.ProjectionConflict(
            f"Impossibile {azione} il sopralluogo su LMC-15: l'appuntamento "
            f"{row.get('id')} non ha una riga stima_inspections collegata",
            appointment_id=row.get("id"))
    return ispezione


def on_complete(cur, agency_id: int, row: dict, *, completed_at, actor_user_id: int) -> None:
    require_active()
    ispezione = _collegata(row, "completare")
    try:
        lmc15.complete_inspection_in(cur, agency_id, inspection_id=ispezione,
                                     completed_at=completed_at, actor_user_id=actor_user_id)
    except ConflictError as exc:
        raise _conflitto(exc) from exc


def on_cancel(cur, agency_id: int, row: dict, *, reason: str, actor_user_id: int) -> None:
    require_active()
    ispezione = _collegata(row, "annullare")
    try:
        lmc15.cancel_inspection_in(cur, agency_id, inspection_id=ispezione,
                                   reason=reason, actor_user_id=actor_user_id)
    except ConflictError as exc:
        raise _conflitto(exc) from exc


def on_no_show(cur, agency_id: int, row: dict, *, actor_user_id: int) -> None:
    """Eredita da `on_cancel` la guardia sul collegamento."""
    on_cancel(cur, agency_id, row, reason=NO_SHOW_REASON, actor_user_id=actor_user_id)
