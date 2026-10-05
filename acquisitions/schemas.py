"""CRM-OPS-3 - i corpi ammessi dalla API delle Acquisizioni.

Come l'Agenda (appointments/schemas.py): `extra="forbid"`, nessun
`agency_id`, nessun attore, nessuno stato `acquired` scrivibile. Il tenant
viene da `ctx.require_agency()`, l'attore da `ctx.user_id`.

I catalogo (stati, motivi, tempistiche, fonti) li controlla il service
contro `enums.py`, cosi' l'errore ha un `code` di dominio e il testo resta
in italiano.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

_PREZZO = dict(ge=0, max_digits=14, decimal_places=2)


class _Corpo(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _ConVersione(_Corpo):
    version: StrictInt = Field(..., ge=1)


class AcquisitionAppointment(_Corpo):
    """L'appuntamento dell'acquisizione, come lo raccoglie il dialog
    condiviso dell'Agenda. Tipo (`seller_meeting`), contatto (il referente)
    e immobile li mette il service: qui solo cio' che l'operatore sceglie.

    Il fuso, la durata massima, l'agente obbligatorio e la chiave
    d'idempotenza li valida `AppointmentCreate` dell'Agenda: una sola
    regola, nessuna copia."""
    start_at: datetime
    #: Facoltativo: senza, la durata di default dell'Agenda (60').
    end_at: datetime | None = None
    #: Facoltativo per un `agent` (e' se stesso), obbligatorio per chi assegna.
    assigned_user_id: StrictInt | None = None
    buffer_before_minutes: int = Field(0, ge=0, le=240)
    buffer_after_minutes: int = Field(0, ge=0, le=240)
    location_text: str | None = Field(None, max_length=500)
    #: Le note OPERATIVE dell'appuntamento (appointments.notes). Mai copiate
    #: in `acquisitions.notes`.
    notes: str | None = Field(None, max_length=5000)
    #: UUID v4: la stessa richiesta ripetuta restituisce la stessa
    #: acquisizione (idempotenza dell'Agenda, A30-2 §6).
    client_request_id: str


class AcquisitionCreate(_Corpo):
    property_id: StrictInt
    owner_contact_id: StrictInt
    lead_id: StrictInt | None = None
    asking_price: Decimal | None = Field(None, **_PREZZO)
    valuation_price: Decimal | None = Field(None, **_PREZZO)
    sale_timing: str | None = None
    source: str | None = Field(None, max_length=100)
    #: Le note COMMERCIALI (acquisitions.notes).
    notes: str | None = Field(None, max_length=5000)
    appointment: AcquisitionAppointment


#: I campi che un PATCH puo' toccare. Mai stato, immobile, appuntamento,
#: motivo di perdita, date di chiusura.
PATCHABLE_FIELDS = (
    "owner_contact_id", "assigned_agent_id", "lead_id", "asking_price",
    "valuation_price", "sale_timing", "source", "notes",
)


class AcquisitionPatch(_ConVersione):
    owner_contact_id: StrictInt | None = None
    assigned_agent_id: StrictInt | None = None
    lead_id: StrictInt | None = None
    asking_price: Decimal | None = Field(None, **_PREZZO)
    valuation_price: Decimal | None = Field(None, **_PREZZO)
    sale_timing: str | None = None
    source: str | None = Field(None, max_length=100)
    notes: str | None = Field(None, max_length=5000)

    def changes(self) -> dict:
        """Solo i campi INVIATI (anche null, per svuotare)."""
        return {c: getattr(self, c) for c in PATCHABLE_FIELDS if c in self.model_fields_set}


class StatusBody(_ConVersione):
    status: str


class LostBody(_ConVersione):
    lost_reason: str | None = None
    lost_notes: str | None = Field(None, max_length=5000)
    #: DELETE-ARCH Fase 1A: annulla anche l'appuntamento ancora aperto, nella
    #: stessa transazione. Facoltativo: assente = come prima (resta aperto).
    cancel_appointment: bool = False
    #: Chi lo annulla: 'client' o 'agency' (default). Mai 'mistake': una
    #: perdita reale non e' un errore.
    appointment_cancelled_kind: str = "agency"


class MistakeBody(_ConVersione):
    """DELETE-ARCH Fase 1A: «Segna come creata per errore»."""
    notes: str | None = Field(None, max_length=5000)


class NewAppointmentBody(_ConVersione):
    appointment: AcquisitionAppointment


class MandateBody(_ConVersione):
    """I dati dell'incarico, gli stessi campi di oggi su `properties`."""
    mandate_type: str = Field(..., min_length=1, max_length=80)
    mandate_start: date
    mandate_end: date | None = None
    #: Facoltativo: il prezzo concordato diventa `properties.asking_price`
    #: (con il suo storico prezzi).
    agreed_price: Decimal | None = Field(None, **_PREZZO)

    @model_validator(mode="after")
    def _date(self):
        if not self.mandate_type.strip():
            raise ValueError("mandate_type: indica il tipo di incarico")
        if self.mandate_end is not None and self.mandate_end < self.mandate_start:
            raise ValueError("la scadenza dell'incarico non puo' precedere l'inizio")
        return self
