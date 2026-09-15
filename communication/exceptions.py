"""Domain exceptions for the COMMUNICATION module.

Stessa forma di consent/exceptions.py e core/exceptions.py: tipi propri, non
importati da un altro modulo, cosi' che un router futuro possa tradurli senza
che COMMUNICATION dipenda dalle scelte HTTP di CORE.
"""


class CommunicationError(Exception):
    """Base error for the COMMUNICATION module."""


class NotFoundError(CommunicationError):
    """Il contatto o il messaggio non esiste, oppure non esiste PER QUESTO SCOPE.

    I due casi sono deliberatamente indistinguibili dall'esterno: dire "esiste
    ma non e' tuo" direbbe a un'agenzia qualcosa dei messaggi di un'altra.
    Stessa scelta di consent.exceptions.NotFoundError e di
    core.exceptions.NotFoundError.
    """


class ValidationError(CommunicationError):
    """Un argomento non appartiene all'insieme chiuso che lo governa."""


class ConflictError(CommunicationError):
    """Lo stato persistito non permette di completare l'operazione.

    Il caso di P29-2.2: si e' chiesto di annullare un messaggio che non e' piu'
    in coda. Non e' un errore del chiamante ne' un difetto del codice - e' il
    mondo che e' andato avanti - e il chiamante deve poterlo distinguere da un
    messaggio che non esiste.
    """
