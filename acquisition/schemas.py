"""LMC-15 - i corpi ammessi. Nessun identificativo di operatore, mai.

L'attore di ogni fatto viene dalla sessione (`ctx.user_id`), e per questo
nessuno di questi modelli ha un campo `*_operator_user_id`: non c'e' un posto
in cui il client possa scriverlo. `extra="forbid"` rende il tentativo un 422
invece di un campo ignorato in silenzio.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, StrictInt, StrictStr, field_validator


class _Corpo(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AcquisitionLinkCreate(_Corpo):
    """Il legame: quale immobile nasce dalla stima in path."""

    property_id: StrictInt


class MandateRecord(_Corpo):
    """L'incarico: la data REALE della firma, e un riferimento facoltativo.

    `mandate_recorded_at` non c'e': e' il momento in cui il server riceve
    questa richiesta, e lasciarlo dichiarare al client significherebbe poter
    raccontare di aver registrato ieri cio' che si registra oggi.
    """

    mandate_signed_at: datetime
    mandate_reference: StrictStr | None = None

    @field_validator("mandate_reference")
    @classmethod
    def _riferimento_non_vuoto(cls, valore):
        if valore is None:
            return None
        pulito = valore.strip()
        if not pulito:
            raise ValueError("mandate_reference non puo' essere vuoto")
        return pulito


class AcquisitionRevoke(_Corpo):
    """La revoca del LINK. La ragione e' obbligatoria: una revoca senza motivo
    e' una riga di audit che non spiega niente."""

    revoked_reason: StrictStr

    @field_validator("revoked_reason")
    @classmethod
    def _ragione_non_vuota(cls, valore):
        pulito = valore.strip()
        if not pulito:
            raise ValueError("revoked_reason e' obbligatoria")
        return pulito


class InspectionSchedule(_Corpo):
    scheduled_for: datetime


class InspectionComplete(_Corpo):
    """La chiusura di un sopralluogo, o la sua registrazione a posteriori.

    `completed_at` e' quando il sopralluogo E' AVVENUTO, non quando lo si
    scrive: sono due cose diverse e la tabella le tiene separate.
    """

    completed_at: datetime


class InspectionCancel(_Corpo):
    cancelled_reason: StrictStr | None = None

    @field_validator("cancelled_reason")
    @classmethod
    def _ragione_non_vuota(cls, valore):
        if valore is None:
            return None
        pulito = valore.strip()
        if not pulito:
            raise ValueError("cancelled_reason non puo' essere vuota")
        return pulito
