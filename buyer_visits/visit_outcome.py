"""ROADMAP VISITE (fase B) - l'ESITO di una visita registrato dal consulente dalla
scheda dell'appuntamento in Agenda: interesse dell'acquirente e feedback.

DOVE VIVE. Nei campi che `property_visits` ha gia' (002) e che il PATCH legacy di
PROPERTY scrive da sempre: `outcome` (l'interesse, in italiano leggibile) e
`feedback` (le osservazioni). Nessuna tabella nuova, nessuna migrazione: la
scheda dell'immobile e la scheda del contatto leggono gia' questi campi.

COSA RESTA VERO DI D4 (A31-1). La PROIEZIONE (`projection`, `integration`) non
scrive mai `outcome`, `feedback`, `rating`: lo stato e la data seguono
l'Agenda, il resto e' dell'operatore. Questo modulo e' l'unico scrittore, oltre
al PATCH legacy, ed e' esplicito: lo chiama una azione dell'operatore sulla
visita, mai un hook automatico. Mai `rating`, mai stato e data.

NIENTE AUTOMATISMI. Una visita non diventa `completed` perche' l'orario e'
passato: qui non si cambia lo stato, lo cambia l'operatore con «Visita
effettuata», «Cliente assente» o l'annullamento. L'esito si registra solo dopo.

PRIVACY. Il feedback e' una nota INTERNA: non entra mai da solo nel report del
proprietario. Il testo che il proprietario puo' leggere lo scrive e lo approva
l'agente (`owner_report`, fase D).

Lavora SOLO sul cursore del chiamante (nessuna connessione, nessun commit).
"""
from __future__ import annotations

from typing import Any

from . import errors

#: I livelli di interesse: codice -> frase che finisce in `outcome`.
INTEREST_LEVELS: dict[str, str] = {
    "high": "Molto interessato",
    "medium": "Interessato",
    "undecided": "Indeciso",
    "none": "Non interessato",
}
_CODE_OF_LABEL = {label: code for code, label in INTEREST_LEVELS.items()}

FEEDBACK_MAX = 4000
#: Gli stati dell'appuntamento da cui si puo' registrare l'esito.
CLOSED_STATES = ("completed", "no_show", "cancelled")
#: L'interesse ha senso solo se la visita e' stata effettuata.
INTEREST_STATES = ("completed",)

UNSET = object()


def interest_code(outcome: Any) -> str | None:
    """Il codice di interesse di un `outcome`, o None se non e' una frase di interesse
    (un esito scritto a mano nel percorso legacy resta com'e')."""
    return _CODE_OF_LABEL.get((outcome or "").strip()) if isinstance(outcome, str) else None


def capabilities(status: str) -> dict[str, bool]:
    return {"can_record_feedback": status in CLOSED_STATES, "can_record_interest": status in INTEREST_STATES}


def _view(row: dict | None, status: str) -> dict | None:
    if row is None:
        return None
    code = interest_code(row["outcome"])
    return {"id": row["id"], "interest": code, "interest_label": INTEREST_LEVELS.get(code),
            "outcome": row["outcome"], "feedback": row["feedback"], "rating": row["rating"],
            **capabilities(status)}


_SELECT = ("SELECT v.id, v.outcome, v.feedback, v.rating FROM property_visits v "
           "JOIN properties p ON p.id = v.property_id WHERE v.appointment_id = %s AND p.agency_id = %s")


def summary(cur, agency_id: int, appointment_id: int, status: str) -> dict | None:
    """La visita collegata all'appuntamento (della stessa agenzia) con il suo esito, o None."""
    cur.execute(_SELECT, (appointment_id, agency_id))
    riga = cur.fetchone()
    return _view(dict(riga) if riga else None, status)


def record(cur, agency_id: int, row: dict, *, interest: Any = UNSET, feedback: Any = UNSET) -> dict:
    """Scrive interesse e/o feedback sulla visita collegata all'appuntamento `row`
    (gia' bloccato dal chiamante). Un campo non indicato non cambia; `None` lo svuota
    (l'interesse solo se `outcome` e' una frase di interesse, mai un esito legacy)."""
    if row.get("appointment_type") != "buyer_visit" or row.get("property_id") is None:
        raise errors.BuyerVisitOutcomeInvalid("L'esito della visita si registra su una visita collegata a un immobile")
    stato = row["status"]
    if stato not in CLOSED_STATES:
        raise errors.BuyerVisitOutcomeNotAllowed(
            "Registra prima la visita come effettuata, cliente assente o non effettuata: poi potrai "
            "aggiungere interesse e feedback")
    if interest is not UNSET and interest is not None:
        if interest not in INTEREST_LEVELS:
            raise errors.BuyerVisitOutcomeInvalid("Livello di interesse non valido")
        if stato not in INTEREST_STATES:
            raise errors.BuyerVisitOutcomeNotAllowed("L'interesse si registra solo per una visita effettuata")
    if feedback is not UNSET and feedback is not None and len(feedback) > FEEDBACK_MAX:
        raise errors.BuyerVisitOutcomeInvalid(f"Il feedback puo' avere al massimo {FEEDBACK_MAX} caratteri")
    cur.execute(_SELECT + " FOR UPDATE OF v", (row["id"], agency_id))
    attuale = cur.fetchone()
    if attuale is None:
        raise errors.BuyerVisitOutcomeInvalid("La visita collegata a questo appuntamento non esiste")
    attuale = dict(attuale)
    outcome, nota = attuale["outcome"], attuale["feedback"]
    if interest is not UNSET:
        if interest is not None:
            outcome = INTEREST_LEVELS[interest]
        elif interest_code(outcome) is not None:
            outcome = None
    if feedback is not UNSET:
        nota = feedback.strip() or None if feedback is not None else None
    if (outcome, nota) != (attuale["outcome"], attuale["feedback"]):
        cur.execute("UPDATE property_visits SET outcome = %s, feedback = %s, updated_at = NOW() "
                    "WHERE id = %s RETURNING id, outcome, feedback, rating", (outcome, nota, attuale["id"]))
        attuale = dict(cur.fetchone())
    return _view(attuale, stato)
