"""A30-1/A30-2 - i corpi ammessi dal service. Nessun identificativo di attore, mai.

`created_by_user_id` non c'e': l'attore viene dalla sessione (`ctx.user_id`).
`agency_id` non c'e': viene da `ctx.require_agency()`. `source` non c'e': il
service crea solo appuntamenti `crm_manual`; gli import avranno il loro
percorso (A30-6). `extra="forbid"` rende ogni tentativo un errore esplicito.
"""
from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator
from pydantic_core import PydanticCustomError

from .enums import APPOINTMENT_TYPES, CREATABLE_STATUSES


class _Corpo(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _istante(valore: datetime) -> datetime:
    # Un orario senza fuso e' ambiguo: non si indovina, si rifiuta. Il tipo
    # d'errore `timezone_required` diventa il codice TIMEZONE_REQUIRED (A30-2).
    if valore.tzinfo is None or valore.utcoffset() is None:
        raise PydanticCustomError(
            "timezone_required",
            "l'orario deve indicare il fuso (es. 2026-10-01T10:00:00+02:00)")
    return valore


#: UUID v4 in forma canonica minuscola: la chiave di idempotenza (A30-2 §6).
_UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


def _uuid4(valore):
    if valore is None:
        return None
    if not isinstance(valore, str) or not _UUID4.match(valore):
        raise PydanticCustomError(
            "client_request_id_invalid",
            "client_request_id deve essere un UUID v4 in forma canonica minuscola")
    return valore


class AppointmentCreate(_Corpo):
    appointment_type: str
    status: str = "scheduled"
    start_at: datetime
    end_at: datetime
    assigned_user_id: StrictInt | None = None
    buffer_before_minutes: int = Field(0, ge=0, le=240)
    buffer_after_minutes: int = Field(0, ge=0, le=240)
    stima_id: StrictInt | None = None
    contact_id: StrictInt | None = None
    lead_id: StrictInt | None = None
    property_id: StrictInt | None = None
    location_text: str | None = Field(None, max_length=500)
    notes: str | None = Field(None, max_length=5000)
    #: A30-2 §6: la chiave di idempotenza. Facoltativa per il service (gli
    #: scrittori interni non la usano), OBBLIGATORIA per la API
    #: (`AppointmentCreateBody`).
    client_request_id: str | None = None

    @field_validator("client_request_id")
    @classmethod
    def _chiave(cls, valore):
        return _uuid4(valore)

    @field_validator("appointment_type")
    @classmethod
    def _tipo(cls, valore):
        if valore not in APPOINTMENT_TYPES:
            raise ValueError(f"tipo non ammesso: {valore}")
        return valore

    @field_validator("status")
    @classmethod
    def _stato(cls, valore):
        if valore not in CREATABLE_STATUSES:
            raise ValueError(f"un appuntamento nasce solo come {', '.join(CREATABLE_STATUSES)}")
        return valore

    @field_validator("start_at", "end_at")
    @classmethod
    def _con_fuso(cls, valore):
        return _istante(valore)

    @model_validator(mode="after")
    def _intervallo(self):
        if self.end_at <= self.start_at:
            raise ValueError("end_at deve essere successivo a start_at")
        if (self.end_at - self.start_at).total_seconds() > 24 * 3600:
            raise ValueError("un appuntamento non puo' durare piu' di 24 ore")
        if self.status != "requested" and self.assigned_user_id is None:
            # D2: l'agente non si inferisce mai; senza agente si salva solo
            # come richiesta. Tipo d'errore -> codice AGENT_REQUIRED.
            raise PydanticCustomError(
                "agent_required",
                "un appuntamento fissato richiede un agente assegnato: "
                "senza agente si salva come richiesta")
        return self


class AppointmentReschedule(_Corpo):
    start_at: datetime
    end_at: datetime
    #: Facoltativo: se assente resta l'agente attuale.
    assigned_user_id: StrictInt | None = None

    @field_validator("start_at", "end_at")
    @classmethod
    def _con_fuso(cls, valore):
        return _istante(valore)

    @model_validator(mode="after")
    def _intervallo(self):
        if self.end_at <= self.start_at:
            raise ValueError("end_at deve essere successivo a start_at")
        if (self.end_at - self.start_at).total_seconds() > 24 * 3600:
            raise ValueError("un appuntamento non puo' durare piu' di 24 ore")
        return self


# ---------------------------------------------------------------------------
# A30-2 - i corpi della API. Ogni scrittura su una riga esistente porta
# `version` (lock ottimistico): chi ha letto una versione vecchia riceve
# VERSION_CONFLICT invece di sovrascrivere il lavoro di un altro.
# ---------------------------------------------------------------------------

class AppointmentCreateBody(AppointmentCreate):
    client_request_id: str


class _ConVersione(_Corpo):
    version: StrictInt = Field(..., ge=1)


def _intervallo_valido(start_at, end_at):
    if end_at <= start_at:
        raise ValueError("end_at deve essere successivo a start_at")
    if (end_at - start_at).total_seconds() > 24 * 3600:
        raise ValueError("un appuntamento non puo' durare piu' di 24 ore")


class ScheduleBody(_ConVersione):
    """FISSA SOPRALLUOGO / fissa una richiesta: requested -> scheduled.
    L'agente e' obbligatorio ed ESPLICITO (D2): se manca, AGENT_REQUIRED."""
    assigned_user_id: StrictInt | None = None
    start_at: datetime
    end_at: datetime

    @field_validator("start_at", "end_at")
    @classmethod
    def _con_fuso(cls, valore):
        return _istante(valore)

    @model_validator(mode="after")
    def _intervallo(self):
        _intervallo_valido(self.start_at, self.end_at)
        if self.assigned_user_id is None:
            raise PydanticCustomError(
                "agent_required", "per fissare l'appuntamento scegli un agente")
        return self


class RescheduleBody(AppointmentReschedule):
    version: StrictInt = Field(..., ge=1)


class ReassignBody(_ConVersione):
    assigned_user_id: StrictInt


class ConfirmBody(_ConVersione):
    pass


def _testo(valore):
    """Spazi ai bordi tolti; un testo vuoto e' assente."""
    if valore is None:
        return None
    valore = valore.strip()
    return valore or None


class FollowUpBody(_Corpo):
    """A30-8: il follow-up facoltativo di un esito. SOLO scadenza, titolo e
    nota: contatto, lead, stima, agenzia e assegnatario li deriva il SERVER
    dall'appuntamento gia' bloccato; `extra="forbid"` rifiuta ogni tentativo
    di mandarli (422)."""
    due_at: datetime
    title: str | None = Field(None, max_length=200)
    note: str | None = Field(None, max_length=2000)

    @field_validator("due_at")
    @classmethod
    def _con_fuso(cls, valore):
        return _istante(valore)

    @field_validator("title", "note")
    @classmethod
    def _pulito(cls, valore):
        return _testo(valore)


class CancelBody(_ConVersione):
    reason: str | None = Field(None, max_length=300)
    follow_up: FollowUpBody | None = None

    @field_validator("reason")
    @classmethod
    def _ragione(cls, valore):
        return _testo(valore)


class CompleteBody(_ConVersione):
    completed_at: datetime | None = None
    #: A30-8 D1: scritta SOLO nell'evento `status_changed` (changes.outcome_note).
    outcome_note: str | None = Field(None, max_length=1000)
    follow_up: FollowUpBody | None = None

    @field_validator("completed_at")
    @classmethod
    def _con_fuso(cls, valore):
        return None if valore is None else _istante(valore)

    @field_validator("outcome_note")
    @classmethod
    def _nota(cls, valore):
        return _testo(valore)


class NoShowBody(_ConVersione):
    outcome_note: str | None = Field(None, max_length=1000)
    follow_up: FollowUpBody | None = None

    @field_validator("outcome_note")
    @classmethod
    def _nota(cls, valore):
        return _testo(valore)


class PatchBody(_ConVersione):
    """Solo campi non temporali. Un campo assente non cambia; `null` lo svuota."""
    notes: str | None = Field(None, max_length=5000)
    location_text: str | None = Field(None, max_length=500)
    contact_id: StrictInt | None = None
    lead_id: StrictInt | None = None
    property_id: StrictInt | None = None

    def changes(self) -> dict:
        return {c: getattr(self, c) for c in self.model_fields_set if c != "version"}


class AvailabilityCheckBody(_Corpo):
    assigned_user_id: StrictInt
    start_at: datetime
    end_at: datetime
    buffer_before_minutes: int = Field(0, ge=0, le=240)
    buffer_after_minutes: int = Field(0, ge=0, le=240)
    exclude_appointment_id: StrictInt | None = None

    @field_validator("start_at", "end_at")
    @classmethod
    def _con_fuso(cls, valore):
        return _istante(valore)

    @model_validator(mode="after")
    def _intervallo(self):
        _intervallo_valido(self.start_at, self.end_at)
        return self
