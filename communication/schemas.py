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

from pydantic import BaseModel, Field


class DispatchRequest(BaseModel):
    """Quanti messaggi provare in questo giro."""

    limit: int = Field(default=10, ge=1, le=50)

    class Config:
        extra = "forbid"
