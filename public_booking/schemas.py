"""A30-12 - i corpi validati QUI (stessa disciplina di `appointments/router.py`:
`_corpo`/`model_validate`, mai la firma della rotta), sia per l'API
operatore sia per l'API pubblica.

D4: dati cliente V1 - nome e telefono obbligatori, email facoltativa,
NESSUNA nota libera. Limiti di lunghezza conservativi, applicati qui cosi'
il database non e' l'unica difesa.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .enums import APPOINTMENT_TYPES

# D4: limiti di lunghezza - conservativi, non arbitrari: abbastanza per un
# nome/indirizzo reale, troppo poco per un payload di attacco.
NAME_MAX_LENGTH = 120
PHONE_MAX_LENGTH = 32
EMAIL_MAX_LENGTH = 254
LABEL_MAX_LENGTH = 200


# ---------------------------------------------------------------------------
# API operatore - gestione dei link (CRUD)
# ---------------------------------------------------------------------------

class BookingLinkCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assigned_user_id: int
    label: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    appointment_type: str
    duration_minutes: int = Field(gt=0, le=1440)
    buffer_before_minutes: int = Field(default=0, ge=0, le=1440)
    buffer_after_minutes: int = Field(default=0, ge=0, le=1440)
    expires_at: str | None = None

    @field_validator("appointment_type")
    @classmethod
    def _tipo_ammesso(cls, v):
        if v not in APPOINTMENT_TYPES:
            raise ValueError(f"appointment_type non valido: {v!r}")
        return v


class BookingLinkPatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    appointment_type: str | None = None
    duration_minutes: int | None = Field(default=None, gt=0, le=1440)
    buffer_before_minutes: int | None = Field(default=None, ge=0, le=1440)
    buffer_after_minutes: int | None = Field(default=None, ge=0, le=1440)
    expires_at: str | None = None

    @field_validator("appointment_type")
    @classmethod
    def _tipo_ammesso(cls, v):
        if v is not None and v not in APPOINTMENT_TYPES:
            raise ValueError(f"appointment_type non valido: {v!r}")
        return v


# ---------------------------------------------------------------------------
# API pubblica
# ---------------------------------------------------------------------------

class PublicBookingSubmitBody(BaseModel):
    """D3: nessun `duration`/`user_id`/`agency_id`/`buffer` qui - arrivano
    SOLO dal link. D4: nessuna nota libera."""
    model_config = ConfigDict(extra="forbid")

    submission_token: str = Field(min_length=1, max_length=200)
    start_at: str
    name: str = Field(min_length=1, max_length=NAME_MAX_LENGTH)
    phone: str = Field(min_length=1, max_length=PHONE_MAX_LENGTH)
    email: str | None = Field(default=None, max_length=EMAIL_MAX_LENGTH)
