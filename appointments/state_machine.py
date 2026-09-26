"""A30-2 - la macchina a stati dell'Agenda. Funzioni PURE: niente database.

    requested --schedule--> scheduled --confirm--> confirmed
        |                       |  |  |  |              |  |  |  |
        +--cancel--> cancelled  |  |  |  +--cancel------+  |  |  +--> cancelled
                                |  |  +-----no_show--------+  |  +--> no_show
                                |  +--------complete----------+-----> completed
                                +-----------reschedule-------------> rescheduled
                                            (+ riga NUOVA 'scheduled')

Terminali (Q6): completed, cancelled, no_show, rescheduled. Non si esce mai.

Guardie di tempo (D11 rev. 2; codici 422 propri da A30-8 D6):
  * complete: solo da `start_at` in poi, con start_at <= completed_at <= adesso
    (completed_at dichiarato dall'operatore o adesso: Q3);
  * no_show: solo da `end_at` in poi.

Riassegnazione (D5 rev. 2): STESSA riga, stato invariato; solo owner/admin.
"""
from __future__ import annotations

from datetime import datetime

from . import errors

OPEN_STATUSES = ("requested", "scheduled", "confirmed")
TERMINAL_STATUSES = ("completed", "cancelled", "no_show", "rescheduled")

#: Da quali stati parte ogni azione.
TRANSITIONS = {
    "schedule": ("requested",),
    "confirm": ("scheduled", "confirmed"),        # su 'confirmed' e' idempotente
    "reschedule": ("scheduled", "confirmed"),
    "reassign": ("requested", "scheduled", "confirmed"),
    "cancel": ("requested", "scheduled", "confirmed"),
    "complete": ("scheduled", "confirmed"),
    "no_show": ("scheduled", "confirmed"),
    "patch": ("requested", "scheduled", "confirmed"),
}

ACTIONS = tuple(TRANSITIONS)

#: Azioni riservate a chi puo' assegnare (matrice P26-1: owner, admin,
#: platform admin in acting).
MANAGER_ONLY_ACTIONS = ("reassign",)

_ETICHETTE = {
    "requested": "Richiesta", "scheduled": "Fissato", "confirmed": "Confermato",
    "completed": "Completato", "cancelled": "Annullato", "no_show": "Assente",
    "rescheduled": "Spostato",
}


def status_label(status: str) -> str:
    return _ETICHETTE.get(status, status)


def check_transition(action: str, status: str) -> None:
    """Rifiuta un'azione non ammessa dallo stato attuale."""
    if action not in TRANSITIONS:
        raise ValueError(f"azione sconosciuta: {action}")
    if status not in TRANSITIONS[action]:
        raise errors.InvalidTransition(
            f"Azione non piu' possibile: lo stato e' '{status_label(status)}'",
            status=status, action=action)


def check_time_guard(action: str, *, start_at: datetime, end_at: datetime,
                     now: datetime) -> None:
    # A30-8 D6: "troppo presto" non e' una transizione vietata dallo stato
    # (quella resta 409 INVALID_TRANSITION) ma un dato non ancora valido:
    # 422 con un codice proprio e `available_from`.
    if action == "complete" and now < start_at:
        raise errors.CompleteTooEarly(
            "Un appuntamento si completa solo dal suo inizio in poi",
            action=action, available_from=start_at)
    if action == "no_show" and now < end_at:
        raise errors.NoShowTooEarly(
            "L'assenza si registra solo dopo la fine dell'appuntamento",
            action=action, available_from=end_at)


def resolve_completed_at(*, start_at: datetime, declared: datetime | None,
                         db_now: datetime) -> datetime:
    """Il momento REALE dello svolgimento (Q3, gate A30-2 correzione A).

    - non dichiarato: `db_now`, il NOW() del DATABASE letto nella stessa
      transazione della mutazione e della proiezione (quindi mai successivo
      al `completed_recorded_at` che LMC-15 scrive con lo stesso NOW());
    - dichiarato: conservato ESATTAMENTE, purche'
      `start_at <= completed_at <= db_now`.
    Nessuna tolleranza, nessuna correzione silenziosa: un orario futuro o
    anteriore all'inizio e' un errore applicativo. Mai `start_at` d'ufficio.
    """
    if declared is None:
        return db_now
    if declared < start_at:
        raise errors.CompletedAtInvalid(
            "L'orario di svolgimento non puo' precedere l'inizio dell'appuntamento")
    if declared > db_now:
        raise errors.CompletedAtInvalid("L'orario di svolgimento non puo' essere nel futuro")
    return declared


def allowed_actions(row: dict, *, is_manager: bool, is_own: bool,
                    now: datetime) -> list[str]:
    """Le azioni che la UI puo' mostrare, calcolate dal server.

    Un agent agisce solo sui propri appuntamenti; owner/admin su tutti. Le
    azioni con una guardia di tempo non ancora soddisfatta NON sono incluse:
    la UI le mostra disabilitate con `available_from`.
    """
    if not (is_manager or is_own):
        return []
    status = row["status"]
    esito = []
    for azione, stati in TRANSITIONS.items():
        if status not in stati:
            continue
        if azione in MANAGER_ONLY_ACTIONS and not is_manager:
            continue
        if azione == "confirm" and status == "confirmed":
            continue
        if azione == "complete" and now < row["start_at"]:
            continue
        if azione == "no_show" and now < row["end_at"]:
            continue
        esito.append(azione)
    return esito
