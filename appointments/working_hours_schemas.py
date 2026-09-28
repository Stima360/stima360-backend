"""A30-11 - i corpi ammessi dalle rotte di orari/eccezioni/chiusure.

Stesso stile di `appointments/schemas.py`: `extra="forbid"`, nessun
`agency_id`/attore nel corpo (arrivano dal contesto), validazione qui e non
nella firma della rotta.
"""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator


class _Corpo(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _valida_minuti(start_minute: int, end_minute: int) -> None:
    if not (0 <= start_minute < 1440):
        raise ValueError("start_minute deve essere fra 0 e 1439")
    if not (1 <= end_minute <= 1440):
        raise ValueError("end_minute deve essere fra 1 e 1440")
    if end_minute <= start_minute:
        raise ValueError(
            "end_minute deve essere dopo start_minute: nessuna fascia attraversa la mezzanotte")


class WorkingHourSlot(_Corpo):
    day_of_week: int = Field(..., ge=1, le=7)
    start_minute: int
    end_minute: int

    @model_validator(mode="after")
    def _controlla(self):
        _valida_minuti(self.start_minute, self.end_minute)
        return self


class WorkingHoursReplaceBody(_Corpo):
    """PUT: l'intero set settimanale dell'agente, sostituito atomicamente."""
    slots: list[WorkingHourSlot] = Field(default_factory=list, max_length=100)


class AvailabilityExceptionCreate(_Corpo):
    exception_date: date
    start_minute: int = 0
    end_minute: int = 1440
    is_available: bool
    reason_code: str | None = Field(None, max_length=40)

    @model_validator(mode="after")
    def _controlla(self):
        _valida_minuti(self.start_minute, self.end_minute)
        return self


class AgencyClosureCreate(_Corpo):
    closure_date: date
    start_minute: int = 0
    end_minute: int = 1440
    reason_code: str | None = Field(None, max_length=40)

    @model_validator(mode="after")
    def _controlla(self):
        _valida_minuti(self.start_minute, self.end_minute)
        return self
