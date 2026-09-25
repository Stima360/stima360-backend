"""A30-2P - la FACADE LMC-15 -> Agenda (design rev. 2, migration 073).

Le quattro rotte LMC-15 sui sopralluoghi (`/api/acquisition/...`) mantengono
il loro contratto HTTP - corpi, risposte `INSPECTION_COLUMNS`, stati e testi
d'errore, eventi di timeline `lmc15:v1:*`, visibilita' d'agenzia, nessuna
idempotenza - ma da qui passano per `appointments`, la fonte autorevole:
`stima_inspections` e' la sua proiezione, scritta nella STESSA transazione
dalle varianti LMC-15 `*_in` (Q1). Nessun secondo scrittore.

LE ECCEZIONI DI COMPATIBILITA' VIVONO SOLO QUI (mai come flag o bypass nelle
funzioni native dell'Agenda, che restano con D11 e le regole A30-2):

  * righe `source='lmc15_facade'` senza agente, `scheduled` o `completed`
    (le ammette solo la 073, solo per queste righe);
  * completamento prima di `start_at` e `completed_at` precedente a
    `start_at` (LMC-15 li accetta);
  * motivo di annullo facoltativo.

UNICA CORREZIONE DI COMPORTAMENTO (F2): un `completed_at` futuro, che in
LMC-15 finiva in una CheckViolation non gestita, e' un 422 leggibile
(`CompletedAtInvalid`, codice COMPLETED_AT_INVALID), prima di qualunque
scrittura.

Ordine dei lock, come nel service nativo: riga `appointments` -> stima -> riga
`stima_inspections` (le varianti LMC-15 prendono la stima da sole).
Nessun accesso a `stime_dettagliate`, `property_visits` o Google.
"""
from __future__ import annotations

from datetime import timedelta

from core.database import core_cursor
from core.exceptions import ConflictError, NotFoundError

from acquisition import repository as lmc15

from . import backfill, errors, repository
from .enums import default_duration_minutes

SOURCE = "lmc15_facade"
APPOINTMENT_TYPE = "inspection"
DURATA = timedelta(minutes=default_duration_minutes(APPOINTMENT_TYPE))
STATI_APERTI = ("requested", "scheduled", "confirmed")

#: Il testo LMC-15 per una risorsa assente o di un'altra agenzia (404).
NON_TROVATA = "Risorsa non trovata"


def source_key(inspection_id: int) -> str:
    return f"stima_inspections:{int(inspection_id)}"


def _in_transazione(lavoro):
    with core_cursor(commit=True) as (_, cur):
        return lavoro(cur)


def _completed_at_futuro(cur, completed_at) -> bool:
    """Confronto nel database, con la stessa interpretazione di LMC-15 (un
    istante senza fuso e' letto nel fuso della sessione): mai un TypeError
    fra un datetime senza fuso e uno con."""
    cur.execute("SELECT %s::timestamptz > NOW() AS futuro", (completed_at,))
    return bool(cur.fetchone()["futuro"])


def _rifiuta_futuro(cur, completed_at) -> None:
    if _completed_at_futuro(cur, completed_at):
        raise errors.CompletedAtInvalid(
            "COMPLETED_AT_INVALID: l'orario di svolgimento non puo' essere nel futuro")


def _nuova_riga(cur, ispezione: dict, *, status: str, start_at, actor_user_id: int,
                agency_id: int, completed_at=None) -> dict:
    valori = {
        "agency_id": agency_id,
        "assigned_user_id": None,
        "appointment_type": APPOINTMENT_TYPE,
        "status": status,
        "start_at": start_at,
        "end_at": start_at + DURATA,
        "buffer_before_minutes": 0,
        "buffer_after_minutes": 0,
        "stima_id": ispezione["stima_id"],
        "stima_inspection_id": ispezione["id"],
        "source": SOURCE,
        "source_record_id": source_key(ispezione["id"]),
        "created_by_user_id": actor_user_id,
    }
    if completed_at is not None:
        valori["completed_at"] = completed_at
    riga = repository.insert_appointment(cur, valori, actor_user_id=actor_user_id)
    if riga is None:                       # una riga LMC-15 appena nata: impossibile
        raise ConflictError("Sopralluogo gia' registrato nell'Agenda")
    return riga


# ---------------------------------------------------------------------------
# CREAZIONE
# ---------------------------------------------------------------------------

def schedule_inspection(agency_id: int, *, stima_id: int, scheduled_for, actor_user_id: int):
    """POST /stime/{id}/inspections: la riga LMC-15 PRIMA (la 073 pretende il
    collegamento gia' all'INSERT), poi l'appuntamento `scheduled` senza agente."""
    def lavoro(cur):
        ispezione = lmc15.create_inspection_in(
            cur, agency_id, stima_id=stima_id, scheduled_for=scheduled_for,
            actor_user_id=actor_user_id)
        _nuova_riga(cur, ispezione, status="scheduled", start_at=ispezione["scheduled_for"],
                    actor_user_id=actor_user_id, agency_id=agency_id)
        return ispezione
    return _in_transazione(lavoro)


def record_completed_inspection(agency_id: int, *, stima_id: int, completed_at,
                                actor_user_id: int):
    """POST /stime/{id}/inspections/completed: sopralluogo registrato a
    posteriori. F2 prima di qualunque scrittura; `start_at = completed_at`."""
    def lavoro(cur):
        _rifiuta_futuro(cur, completed_at)
        ispezione = lmc15.create_completed_inspection_in(
            cur, agency_id, stima_id=stima_id, completed_at=completed_at,
            actor_user_id=actor_user_id)
        _nuova_riga(cur, ispezione, status="completed", start_at=ispezione["completed_at"],
                    completed_at=ispezione["completed_at"], actor_user_id=actor_user_id,
                    agency_id=agency_id)
        return ispezione
    return _in_transazione(lavoro)


# ---------------------------------------------------------------------------
# CHIUSURA: lookup (o adozione) della riga appointments, poi LMC-15
# ---------------------------------------------------------------------------

def _ispezione_dell_agenzia(cur, agency_id: int, inspection_id: int):
    """La riga LMC-15, solo se la sua stima e' di questa agenzia. Un orfano
    (stima cancellata) o una riga altrui: None, cioe' il 404 di LMC-15."""
    cur.execute(
        """SELECT i.id, i.stima_id, i.status, i.scheduled_for, i.completed_at,
                  i.cancelled_at, i.cancelled_reason, s.agency_id
             FROM stima_inspections i
             JOIN stime s ON s.id = i.stima_id
            WHERE i.id = %s AND s.agency_id = %s""",
        (inspection_id, agency_id),
    )
    riga = cur.fetchone()
    return None if riga is None else dict(riga)


def _blocca_appuntamento(cur, agency_id: int, inspection_id: int):
    cur.execute(
        "SELECT * FROM appointments WHERE stima_inspection_id = %s AND agency_id = %s "
        "FOR UPDATE",
        (inspection_id, agency_id),
    )
    riga = cur.fetchone()
    return None if riga is None else dict(riga)


def _appuntamento_o_adozione(cur, agency_id: int, inspection_id: int):
    """La riga `appointments` collegata, bloccata. Se la riga LMC-15 non e'
    ancora rappresentata (nata prima della facade e dopo il backfill), la si
    ADOTTA qui, nella stessa transazione, con le regole e la chiave del
    backfill: due adozioni concorrenti si risolvono sull'indice unico, mai un
    doppione."""
    ispezione = _ispezione_dell_agenzia(cur, agency_id, inspection_id)
    if ispezione is None:
        raise NotFoundError(NON_TROVATA)
    riga = _blocca_appuntamento(cur, agency_id, inspection_id)
    if riga is None:
        piano = backfill.map_inspection(ispezione)
        repository.insert_appointment(cur, piano["values"], actor_user_id=None)
        riga = _blocca_appuntamento(cur, agency_id, inspection_id)
    if riga is None:                       # collegata in un'altra agenzia: impossibile
        raise NotFoundError(NON_TROVATA)
    # Con la riga `appointments` bloccata, si RILEGGE la riga LMC-15: una
    # transazione concorrente puo' averla chiusa mentre questa attendeva
    # (sull'indice unico dell'adozione o sul lock di riga). Lo stato letto
    # prima dell'attesa sarebbe vecchio, e il conflitto non avrebbe il testo
    # di LMC-15.
    ispezione = _ispezione_dell_agenzia(cur, agency_id, inspection_id)
    if ispezione is None:
        raise NotFoundError(NON_TROVATA)
    return ispezione, riga


def _deve_essere_aperto(ispezione: dict, riga: dict) -> None:
    if riga["status"] in STATI_APERTI:
        return
    if ispezione["status"] != "scheduled":
        # lo stesso testo di LMC-15 per una riga gia' chiusa
        raise ConflictError(
            f"Sopralluogo gia' {ispezione['status']}: nessuna transizione possibile")
    raise errors.ProjectionConflict(
        "Il sopralluogo risulta gia' chiuso nell'Agenda: ricarica",
        appointment_id=riga["id"])


def complete_inspection(agency_id: int, *, inspection_id: int, completed_at,
                        actor_user_id: int):
    """POST /inspections/{id}/complete. Niente D11, niente `completed_at >=
    start_at` (compatibilita' LMC-15); `completed_at <= NOW()` (F2)."""
    def lavoro(cur):
        ispezione, riga = _appuntamento_o_adozione(cur, agency_id, inspection_id)
        _rifiuta_futuro(cur, completed_at)
        _deve_essere_aperto(ispezione, riga)
        chiusa = lmc15.complete_inspection_in(
            cur, agency_id, inspection_id=inspection_id, completed_at=completed_at,
            actor_user_id=actor_user_id)
        repository.update_appointment(
            cur, riga["id"], {"status": "completed", "completed_at": chiusa["completed_at"]},
            actor_user_id=actor_user_id, event_type="status_changed",
            from_status=riga["status"], azione="lmc15_complete")
        return chiusa
    return _in_transazione(lavoro)


def cancel_inspection(agency_id: int, *, inspection_id: int, reason, actor_user_id: int):
    """POST /inspections/{id}/cancel. Motivo facoltativo (compatibilita'
    LMC-15). `cancelled_at` e' il NOW() della transazione, lo stesso istante
    che LMC-15 registra (Q10)."""
    def lavoro(cur):
        ispezione, riga = _appuntamento_o_adozione(cur, agency_id, inspection_id)
        _deve_essere_aperto(ispezione, riga)
        db_now = repository.db_now(cur)
        chiusa = lmc15.cancel_inspection_in(
            cur, agency_id, inspection_id=inspection_id, reason=reason,
            actor_user_id=actor_user_id)
        repository.update_appointment(
            cur, riga["id"], {"status": "cancelled", "cancelled_at": db_now,
                              "cancelled_reason": reason},
            actor_user_id=actor_user_id, event_type="status_changed",
            from_status=riga["status"], azione="lmc15_cancel")
        return chiusa
    return _in_transazione(lavoro)
