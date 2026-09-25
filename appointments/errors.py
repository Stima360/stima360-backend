"""A30-2 - gli errori dell'Agenda, ognuno con il suo `code`.

Ogni classe estende l'eccezione di dominio che il resto del CRM gia' conosce
(`core.exceptions`), cosi' un chiamante che intercetta `ConflictError` o
`NotFoundError` continua a funzionare. In piu' porta un `code` stabile, che
il router mette nella risposta accanto a `detail`: la UI traduce il codice,
non il testo.
"""
from __future__ import annotations

from core.exceptions import ConflictError, NotFoundError, PermissionDenied, ValidationError

# --- codici (contratto con la UI A30-4) -----------------------------------
APPOINTMENT_CONFLICT = "APPOINTMENT_CONFLICT"
INVALID_TRANSITION = "INVALID_TRANSITION"
VERSION_CONFLICT = "VERSION_CONFLICT"
IDEMPOTENCY_KEY_REUSED = "IDEMPOTENCY_KEY_REUSED"
PROJECTION_CONFLICT = "PROJECTION_CONFLICT"
AGENT_REQUIRED = "AGENT_REQUIRED"
AGENT_NOT_ACTIVE = "AGENT_NOT_ACTIVE"
REASON_REQUIRED = "REASON_REQUIRED"
COMPLETED_AT_INVALID = "COMPLETED_AT_INVALID"
TIMEZONE_REQUIRED = "TIMEZONE_REQUIRED"
RANGE_TOO_LARGE = "RANGE_TOO_LARGE"
LINK_MISMATCH = "LINK_MISMATCH"
INSPECTION_PROJECTION_NOT_ACTIVE = "INSPECTION_PROJECTION_NOT_ACTIVE"
VALIDATION_ERROR = "VALIDATION_ERROR"
NOT_FOUND = "NOT_FOUND"
FORBIDDEN_ROLE = "FORBIDDEN_ROLE"
SESSION_REQUIRED = "SESSION_REQUIRED"
PLATFORM_ADMIN_AGENCY_REQUIRED = "PLATFORM_ADMIN_AGENCY_REQUIRED"


class _ConCodice:
    code = None

    def __init__(self, message, **extra):
        super().__init__(message)
        self.extra = extra


# --- 404 ------------------------------------------------------------------
class AppointmentNotFound(_ConCodice, NotFoundError):
    """Inesistente, di un'altra agenzia o non visibile: la stessa risposta."""
    code = NOT_FOUND


# --- 403 ------------------------------------------------------------------
class ForbiddenRole(_ConCodice, PermissionDenied):
    code = FORBIDDEN_ROLE


class SessionRequired(_ConCodice, PermissionDenied):
    code = SESSION_REQUIRED


# --- 409 ------------------------------------------------------------------
class AppointmentConflict(_ConCodice, ConflictError):
    """L'agente e' gia' occupato. `conflicts` elenca cosa lo occupa,
    `alternatives` i primi orari liberi dello stesso agente."""
    code = APPOINTMENT_CONFLICT

    def __init__(self, message, conflicts=None, alternatives=None):
        super().__init__(message)
        self.conflicts = list(conflicts or [])
        self.alternatives = list(alternatives or [])


class InvalidTransition(_ConCodice, ConflictError):
    code = INVALID_TRANSITION


class VersionConflict(_ConCodice, ConflictError):
    code = VERSION_CONFLICT


class IdempotencyKeyReused(_ConCodice, ConflictError):
    code = IDEMPOTENCY_KEY_REUSED


class ProjectionConflict(_ConCodice, ConflictError):
    code = PROJECTION_CONFLICT


# --- 422 ------------------------------------------------------------------
class AgentRequired(_ConCodice, ValidationError):
    code = AGENT_REQUIRED


class AgentNotActive(_ConCodice, ValidationError):
    code = AGENT_NOT_ACTIVE


class ReasonRequired(_ConCodice, ValidationError):
    code = REASON_REQUIRED


class CompletedAtInvalid(_ConCodice, ValidationError):
    code = COMPLETED_AT_INVALID


class RangeTooLarge(_ConCodice, ValidationError):
    code = RANGE_TOO_LARGE


class LinkMismatch(_ConCodice, ValidationError):
    code = LINK_MISMATCH


class InspectionProjectionNotActive(_ConCodice, ValidationError):
    """Un sopralluogo legato a una stima si fissa solo con la proiezione
    verso `stima_inspections` attiva (A30-2P)."""
    code = INSPECTION_PROJECTION_NOT_ACTIVE
