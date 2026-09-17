"""P29-2.4 - il corpo della richiesta di dispatch, e nient'altro.

UN CAMPO SOLO, E `extra = "forbid"`

Il payload porta `limit`. Non porta `agency_id`, e non e' una dimenticanza: lo
scope del dispatcher viene dalla sessione autenticata, e un `agency_id` nel
corpo sarebbe la strada per dispacciare i messaggi di un'altra agenzia.

`extra = "forbid"` fa si' che un campo di troppo non venga IGNORATO ma
RIFIUTATO con 422, nominandolo. E' la stessa tecnica di P29-1.3, dove serviva a
impedire che un consenso viaggiasse di soppiatto dentro un aggiornamento di
contatto: un campo ignorato in silenzio e' un campo che qualcuno crede di aver
mandato.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from .enums import CHANNELS


class DispatchRequest(BaseModel):
    """Quale canale, e quanti messaggi provare in questo giro.

    `channel` non ha default: un giro di dispatch riguarda un canale solo, e
    con N canali servono N cron - esattamente come con N agenzie servono N
    cron. Un default qui sceglierebbe al posto di chi chiama.
    """

    channel: str
    limit: int = Field(default=10, ge=1, le=50)

    @field_validator("channel")
    @classmethod
    def _canale_noto(cls, valore: str) -> str:
        if valore not in CHANNELS:
            raise ValueError(f"channel must be one of {', '.join(sorted(CHANNELS))}")
        return valore

    class Config:
        extra = "forbid"
