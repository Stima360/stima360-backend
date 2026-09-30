"""A31-2 - gli errori della proiezione delle visite acquirente.

Estendono `core.exceptions.ConflictError` e portano un `code` stabile: il
router dell'Agenda (`appointments/router.py::_errore`) traduce gia' ogni
`ConflictError` con `code` in un 409 `{"detail", "code"}`, senza modifiche.
Mai dettagli del database nel messaggio.
"""
from __future__ import annotations

from core.exceptions import ConflictError, ValidationError

BUYER_VISIT_PROPERTY_LOCKED = "BUYER_VISIT_PROPERTY_LOCKED"
BUYER_VISIT_PROJECTION_INVALID = "BUYER_VISIT_PROJECTION_INVALID"
BUYER_VISIT_PROJECTION_INTEGRITY = "BUYER_VISIT_PROJECTION_INTEGRITY"
# A31-3 - le facade BUY/PROPERTY (D5-D8)
BUYER_VISIT_MANAGED_BY_AGENDA = "BUYER_VISIT_MANAGED_BY_AGENDA"
BUYER_VISIT_LEGACY_REOPEN = "BUYER_VISIT_LEGACY_REOPEN"
BUYER_VISIT_SCHEDULE_VIA_AGENDA = "BUYER_VISIT_SCHEDULE_VIA_AGENDA"
BUYER_VISIT_INVALID = "BUYER_VISIT_INVALID"


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


# ---------------------------------------------------------------------------
# A31-3 - le facade BUY/PROPERTY. Stessa forma: `code` stabile, messaggio di
# dominio, mai un dettaglio del database. I router BUY e PROPERTY traducono
# gia' `ConflictError` in 409 e `ValidationError` in 400.
# ---------------------------------------------------------------------------

class BuyerVisitManagedByAgenda(_ConCodice):
    """D5-D7: una visita proiettata si programma, si sposta, si riassegna e si
    annulla dall'Agenda; il PATCH legacy scrive solo esito/feedback/voto."""
    code = BUYER_VISIT_MANAGED_BY_AGENDA


class BuyerVisitLegacyReopen(_ConCodice):
    """Regola congelata A31-1: una visita legacy non diventa una visita futura
    aperta (`scheduled`/`confirmed` nel futuro)."""
    code = BUYER_VISIT_LEGACY_REOPEN


class BuyerVisitScheduleViaAgenda(_ConCodice):
    """D8: l'interazione generica non crea `visit_scheduled`."""
    code = BUYER_VISIT_SCHEDULE_VIA_AGENDA


class BuyerVisitInvalid(ValidationError):
    """I dati di una visita non formano un appuntamento valido (fuso, durata)."""
    code = BUYER_VISIT_INVALID

    def __init__(self, message, **extra):
        super().__init__(message)
        self.extra = extra
