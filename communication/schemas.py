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

from datetime import datetime

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


class ConsentGrantRequest(BaseModel):
    """Fase B: un consenso marketing raccolto dall'operatore, con la prova.

    `decided_at` e' la data della RACCOLTA (con fuso), non quella del click.
    `evidence_type`/`evidence_ref` dicono dove sta la prova; `notice_id` cita
    l'informativa presentata. Nessun campo del contatto, nessun attore: il
    contatto e' nel path e l'attore e' la sessione.
    """
    decided_at: datetime
    #: 109: i canali autorizzati, nominati uno per uno. Nessun default e
    #: nessun "tutti": un consenso senza canale non esiste.
    channels: list[str] = Field(min_length=1, max_length=4)
    evidence_type: str = Field(min_length=1, max_length=40)
    evidence_ref: str = Field(min_length=1, max_length=100)
    notice_id: int = Field(gt=0)
    note: str | None = Field(default=None, max_length=2000)

    model_config = {"extra": "forbid"}


class ConsentRefusalRequest(BaseModel):
    """Fase B: un rifiuto o una revoca espressi al personale.

    `scope` e' l'ambito dichiarato dall'operatore: `agency` (questa agenzia)
    o `platform` (tutto STIMA360). `note` racconta come e' stato espresso.
    """
    decided_at: datetime
    #: 109: i canali revocati, nominati uno per uno (anche tutti e quattro).
    channels: list[str] = Field(min_length=1, max_length=4)
    scope: str = "agency"
    note: str = Field(min_length=1, max_length=2000)
    evidence_type: str | None = Field(default=None, max_length=40)
    evidence_ref: str | None = Field(default=None, max_length=100)

    model_config = {"extra": "forbid"}
