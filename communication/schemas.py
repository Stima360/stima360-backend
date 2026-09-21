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

from .enums import CHANNELS, COMMUNICATION_TYPES


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


class JourneyTickRequest(BaseModel):
    """Quanto lavoro al massimo in un giro. Nient'altro.

    Nessun `agency_id`, per la stessa ragione del dispatch: lo scope viene
    dalla sessione. Nessun `journey_id` e nessun `contact_id`: un giro non si
    pilota dall'esterno verso un bersaglio scelto - fa cio' che e' dovuto, e
    cio' che e' dovuto lo dicono i dati.
    """

    limit: int = Field(default=500, ge=1, le=2000)

    model_config = {"extra": "forbid"}


class ManualMessageRequest(BaseModel):
    """Un messaggio scritto da una persona: oggetto, testo, e il tipo.

    NON porta il destinatario e NON porta l'agenzia: il primo si risolve dal
    contatto nello scope, la seconda dalla sessione. Un indirizzo accettato
    dal client renderebbe questa rotta un modo per mandare posta a chiunque
    con il mittente dell'agenzia.

    `communication_type` e' esplicito e non indovinato. Il default e'
    `marketing`, che e' il caso piu' restrittivo: passa dal gate del
    consenso. Chi scrive una comunicazione di servizio lo dichiara.
    """

    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=20000)
    communication_type: str = "marketing"

    model_config = {"extra": "forbid"}

    @field_validator("communication_type")
    @classmethod
    def _tipo_noto(cls, valore: str) -> str:
        if valore not in COMMUNICATION_TYPES:
            raise ValueError(f"communication_type must be one of {sorted(COMMUNICATION_TYPES)}")
        return valore
