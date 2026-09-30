"""A31-2 - gli errori della proiezione delle visite acquirente.

Estendono `core.exceptions.ConflictError` e portano un `code` stabile: il
router dell'Agenda (`appointments/router.py::_errore`) traduce gia' ogni
`ConflictError` con `code` in un 409 `{"detail", "code"}`, senza modifiche.
Mai dettagli del database nel messaggio.
"""
from __future__ import annotations

from core.exceptions import ConflictError

BUYER_VISIT_PROPERTY_LOCKED = "BUYER_VISIT_PROPERTY_LOCKED"
BUYER_VISIT_PROJECTION_INVALID = "BUYER_VISIT_PROJECTION_INVALID"
BUYER_VISIT_PROJECTION_INTEGRITY = "BUYER_VISIT_PROJECTION_INTEGRITY"


class _ConCodice(ConflictError):
    code = None

    def __init__(self, message, **extra):
        super().__init__(message)
        self.extra = extra


class BuyerVisitPropertyLocked(_ConCodice):
    """D5: l'immobile di una visita gia' proiettata non si cambia ne' si toglie."""
    code = BUYER_VISIT_PROPERTY_LOCKED


class BuyerVisitProjectionInvalid(_ConCodice):
    """La proiezione esistente non corrisponde piu' all'appuntamento (deriva)."""
    code = BUYER_VISIT_PROJECTION_INVALID


class BuyerVisitProjectionIntegrity(_ConCodice):
    """Il database ha rifiutato la scrittura della proiezione (guardia 078,
    FK, UNIQUE): la mutazione dell'Agenda si annulla tutta."""
    code = BUYER_VISIT_PROJECTION_INTEGRITY
