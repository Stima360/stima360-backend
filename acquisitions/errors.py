"""CRM-OPS-3 - gli errori delle Acquisizioni, ognuno con il suo `code`.

Stessa forma degli errori dell'Agenda (appointments/errors.py): estendono le
eccezioni di dominio di `core.exceptions`, cosi' chi intercetta
`ConflictError` o `NotFoundError` continua a funzionare, e portano un `code`
stabile che il router mette accanto a `detail`.
"""
from __future__ import annotations

from core.exceptions import ConflictError, NotFoundError, PermissionDenied, ValidationError

NOT_FOUND = "NOT_FOUND"
FORBIDDEN_ROLE = "FORBIDDEN_ROLE"
AGENT_REQUIRED = "AGENT_REQUIRED"
AGENT_NOT_ACTIVE = "AGENT_NOT_ACTIVE"
PROPERTY_WITHOUT_OWNER = "PROPERTY_WITHOUT_OWNER"
OWNER_NOT_LINKED = "OWNER_NOT_LINKED"
OPEN_ACQUISITION_EXISTS = "OPEN_ACQUISITION_EXISTS"
INVALID_TRANSITION = "INVALID_TRANSITION"
LOST_REASON_REQUIRED = "LOST_REASON_REQUIRED"
VERSION_CONFLICT = "VERSION_CONFLICT"
APPOINTMENT_STILL_OPEN = "APPOINTMENT_STILL_OPEN"
MANDATE_NOT_ALLOWED = "MANDATE_NOT_ALLOWED"
MANDATE_ALREADY_EXISTS = "MANDATE_ALREADY_EXISTS"
APPOINTMENT_LINKED_TO_ACQUISITION = "APPOINTMENT_LINKED_TO_ACQUISITION"
VALIDATION_ERROR = "VALIDATION_ERROR"


class _ConCodice:
    code = None

    def __init__(self, message, **extra):
        super().__init__(message)
        self.extra = extra


class AcquisitionNotFound(_ConCodice, NotFoundError):
    """Inesistente, di un'altra agenzia o non visibile: la stessa risposta."""
    code = NOT_FOUND


class ForbiddenRole(_ConCodice, PermissionDenied):
    code = FORBIDDEN_ROLE


class AgentRequired(_ConCodice, ValidationError):
    code = AGENT_REQUIRED


class AgentNotActive(_ConCodice, ValidationError):
    code = AGENT_NOT_ACTIVE


class PropertyWithoutOwner(_ConCodice, ValidationError):
    code = PROPERTY_WITHOUT_OWNER


class OwnerNotLinked(_ConCodice, ValidationError):
    code = OWNER_NOT_LINKED


class OpenAcquisitionExists(_ConCodice, ConflictError):
    code = OPEN_ACQUISITION_EXISTS


class InvalidTransition(_ConCodice, ConflictError):
    code = INVALID_TRANSITION


class LostReasonRequired(_ConCodice, ValidationError):
    code = LOST_REASON_REQUIRED


class VersionConflict(_ConCodice, ConflictError):
    code = VERSION_CONFLICT


class AppointmentStillOpen(_ConCodice, ConflictError):
    code = APPOINTMENT_STILL_OPEN


class MandateNotAllowed(_ConCodice, ConflictError):
    code = MANDATE_NOT_ALLOWED


class MandateAlreadyExists(_ConCodice, ConflictError):
    code = MANDATE_ALREADY_EXISTS


class AppointmentLinkedToAcquisition(_ConCodice, ConflictError):
    """Dall'Agenda: l'immobile di un appuntamento d'acquisizione non cambia."""
    code = APPOINTMENT_LINKED_TO_ACQUISITION


class InvalidData(_ConCodice, ValidationError):
    code = VALIDATION_ERROR
