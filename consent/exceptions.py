"""Domain exceptions for the CONSENT module.

Stessa forma di core/exceptions.py e followup/exceptions.py: tipi propri, non
importati da un altro modulo, cosi' che un router futuro possa tradurli senza
che CONSENT dipenda dalle scelte HTTP di CORE.
"""


class ConsentError(Exception):
    """Base error for the CONSENT module."""


class NotFoundError(ConsentError):
    """Il contatto non esiste, oppure non esiste PER QUESTO SCOPE.

    I due casi sono deliberatamente indistinguibili dall'esterno: dire "esiste
    ma non e' tuo" direbbe a un'agenzia qualcosa sui contatti di un'altra.
    Stessa scelta di core.exceptions.NotFoundError (design spec D-6).
    """


class ValidationError(ConsentError):
    """Un argomento non appartiene all'insieme chiuso che lo governa."""


class ConflictError(ConsentError):
    """Lo stato persistito non permette di completare l'operazione."""
