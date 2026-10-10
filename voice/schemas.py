"""STIMA Voice - il contratto fra il modello linguistico e il CRM.

Il modello restituisce SOLO un `VoicePlanOutput`: un elenco di comandi, ognuno
con un `intent` preso da un elenco chiuso, piu' i dubbi che non sa sciogliere.
Non sceglie mai id, non decide il rischio, non nomina servizi.

Regole del contratto:
  * `extra="forbid"` ovunque: un campo in piu' e' un errore, non un'estensione;
  * i valori chiusi (tipo immobile, tipo edificio, tipo appuntamento, priorita',
    tipo interazione) sono gli enum del CRM, importati e non copiati;
  * le date NON sono ISO ma testo ("domani", "giovedi' alle 15"): le interpreta
    `voice.dates` in modo deterministico rispetto al momento della registrazione,
    perche' il calcolo delle date relative non si affida al modello;
  * i riferimenti ("Fabio", "il trilocale di via Roma") sono descrizioni, non
    id: li risolve il risolutore (Fase 2) con i permessi dell'agente;
  * un comando puo' riferirsi a un record creato da un comando precedente
    dello stesso vocale con `step` (indice 1-based).
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from appointments.enums import APPOINTMENT_TYPES
from core.enums import PRIORITIES
from core.normalization import normalize_email, normalize_phone
from property.enums import PROPERTY_TYPES
from property.interactions import INTERACTION_TYPES
from property.schemas import BUILDING_TYPES

#: Gli intenti della versione 1, e nient'altro. `unsupported` e' il modo in cui
#: il modello dichiara una richiesta fuori elenco: finisce bloccata, con la
#: spiegazione, invece di essere forzata in un intento sbagliato.
INTENTS = (
    "add_note", "add_task", "create_contact", "create_unit", "create_building",
    "link_owner", "activate_seller", "create_appointment", "unsupported",
)

RECORD_KINDS = ("crm", "census")

MAX_COMMANDS = 12
MAX_TEXT = 2000


def _sorted(values) -> list[str]:
    return sorted(values)


PropertyType = Literal[tuple(_sorted(PROPERTY_TYPES))]  # type: ignore[valid-type]
BuildingType = Literal[tuple(BUILDING_TYPES)]  # type: ignore[valid-type]
AppointmentType = Literal[tuple(APPOINTMENT_TYPES)]  # type: ignore[valid-type]
Priority = Literal[tuple(_sorted(PRIORITIES))]  # type: ignore[valid-type]
InteractionType = Literal[tuple(INTERACTION_TYPES)]  # type: ignore[valid-type]
RecordKind = Literal[tuple(RECORD_KINDS)]  # type: ignore[valid-type]


class VoiceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ---------------------------------------------------------------------------
# Riferimenti: descrizioni, mai id
# ---------------------------------------------------------------------------

class ContactRef(VoiceModel):
    """Chi: per nome, per recapito, o per `step` (un contatto creato prima
    nello stesso vocale)."""
    step: int | None = Field(None, ge=1, le=MAX_COMMANDS)
    first_name: str | None = Field(None, max_length=100)
    last_name: str | None = Field(None, max_length=100)
    company_name: str | None = Field(None, max_length=200)
    phone: str | None = Field(None, max_length=50)
    email: str | None = Field(None, max_length=320)

    @model_validator(mode="after")
    def _qualcosa(self):
        if self.step is None and not any(
            (self.first_name, self.last_name, self.company_name, self.phone, self.email)
        ):
            raise ValueError("contact reference needs a name, a contact detail or a step")
        return self

    @field_validator("email")
    @classmethod
    def _email(cls, v):
        return normalize_email(v)

    @field_validator("phone")
    @classmethod
    def _phone(cls, v):
        if v is None:
            return None
        digits = normalize_phone(v)
        if digits is None or len(digits) < 8:
            raise ValueError("phone too short")
        return v

    def describes_something(self) -> bool:
        return any((self.first_name, self.last_name, self.company_name, self.phone, self.email))


class PropertyRef(VoiceModel):
    """Quale immobile: per codice, per indirizzo, per `step`."""
    step: int | None = Field(None, ge=1, le=MAX_COMMANDS)
    code: str | None = Field(None, max_length=50)
    address: str | None = Field(None, max_length=250)
    civic_number: str | None = Field(None, max_length=30)
    city: str | None = Field(None, max_length=120)
    property_type: PropertyType | None = None

    @model_validator(mode="after")
    def _qualcosa(self):
        if self.step is None and not any((self.code, self.address, self.city)):
            raise ValueError("property reference needs a code, an address or a step")
        return self


class BuildingRef(VoiceModel):
    step: int | None = Field(None, ge=1, le=MAX_COMMANDS)
    name: str | None = Field(None, max_length=120)
    address: str | None = Field(None, max_length=250)
    civic_number: str | None = Field(None, max_length=30)
    city: str | None = Field(None, max_length=120)

    @model_validator(mode="after")
    def _qualcosa(self):
        if self.step is None and not any((self.name, self.address)):
            raise ValueError("building reference needs a name, an address or a step")
        return self


class WhenRef(VoiceModel):
    """Quando, come l'ha detto l'agente. `date_text` e `time_text` sono le
    parole del vocale ("domani", "giovedi' prossimo", "alle 15 e mezza"):
    le interpreta `voice.dates`. Il modello NON calcola date."""
    date_text: str = Field(..., min_length=1, max_length=80)
    time_text: str | None = Field(None, max_length=80)


# ---------------------------------------------------------------------------
# I comandi
# ---------------------------------------------------------------------------

class _Command(VoiceModel):
    #: Le parole del vocale da cui il comando e' stato dedotto: servono alla
    #: scheda di revisione e alle verifiche deterministiche (parole chiave).
    quote: str = Field(..., min_length=1, max_length=MAX_TEXT)


class AddNoteCommand(_Command):
    intent: Literal["add_note"]
    text: str = Field(..., min_length=1, max_length=5000)
    interaction_type: InteractionType = "note"
    contact: ContactRef | None = None
    property: PropertyRef | None = None

    @model_validator(mode="after")
    def _bersaglio(self):
        if self.contact is None and self.property is None:
            raise ValueError("a note needs a contact or a property")
        return self


class AddTaskCommand(_Command):
    intent: Literal["add_task"]
    title: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(None, max_length=5000)
    priority: Priority = "normal"
    due: WhenRef | None = None
    contact: ContactRef | None = None


class CreateContactCommand(_Command):
    intent: Literal["create_contact"]
    first_name: str | None = Field(None, max_length=100)
    last_name: str | None = Field(None, max_length=100)
    company_name: str | None = Field(None, max_length=200)
    phone: str | None = Field(None, max_length=50)
    email: str | None = Field(None, max_length=320)
    notes: str | None = Field(None, max_length=5000)

    @model_validator(mode="after")
    def _identita(self):
        if not any((self.first_name, self.last_name, self.company_name)):
            raise ValueError("a contact needs at least a name")
        return self

    @field_validator("email")
    @classmethod
    def _email(cls, v):
        return normalize_email(v)

    @field_validator("phone")
    @classmethod
    def _phone(cls, v):
        if v is None:
            return None
        digits = normalize_phone(v)
        if digits is None or len(digits) < 8:
            raise ValueError("phone too short")
        return v


class CreateUnitCommand(_Command):
    """Una scheda immobile: commerciale (`crm`) o di censimento (`census`).
    Il modello indica `record_kind` solo se il vocale lo dice; il
    pianificatore lo CONFERMA con le parole chiave del trascritto e, se non
    trova evidenza, lascia la scelta all'agente. Nessun campo obbligatorio
    oltre al tipo: e' la regola di `CensusUnitCreate`, che qui si rispetta."""
    intent: Literal["create_unit"]
    record_kind: RecordKind | None = None
    property_type: PropertyType = "apartment"
    address: str | None = Field(None, max_length=250)
    civic_number: str | None = Field(None, max_length=30)
    city: str | None = Field(None, max_length=120)
    floor: str | None = Field(None, max_length=20)
    internal_number: str | None = Field(None, max_length=10)
    rooms: int | None = Field(None, ge=0, le=99)
    bathrooms: int | None = Field(None, ge=0, le=20)
    surface_sqm: int | None = Field(None, ge=1, le=100000)
    building: BuildingRef | None = None
    owner: ContactRef | None = None
    notes: str | None = Field(None, max_length=5000)


class CreateBuildingCommand(_Command):
    """Una palazzina come contenitore: le unita' dichiarate restano un numero
    (`units_declared`, come in `BuildingCreate`), MAI schede inventate."""
    intent: Literal["create_building"]
    building_type: BuildingType = "condominio"
    name: str | None = Field(None, max_length=120)
    address: str | None = Field(None, max_length=250)
    civic_number: str | None = Field(None, max_length=30)
    city: str | None = Field(None, max_length=120)
    units_declared: int | None = Field(None, ge=0, le=999)
    floors_above_ground: int | None = Field(None, ge=0, le=99)
    notes: str | None = Field(None, max_length=5000)


class LinkOwnerCommand(_Command):
    intent: Literal["link_owner"]
    contact: ContactRef
    property: PropertyRef


class ActivateSellerCommand(_Command):
    """«Vende»: il contatto vende quell'immobile (crm/sellers.activate)."""
    intent: Literal["activate_seller"]
    contact: ContactRef
    property: PropertyRef


class CreateAppointmentCommand(_Command):
    intent: Literal["create_appointment"]
    appointment_type: AppointmentType | None = None
    when: WhenRef
    duration_minutes: int | None = Field(None, ge=5, le=24 * 60)
    contact: ContactRef | None = None
    property: PropertyRef | None = None
    #: Nome dell'agente a cui affidarlo, SOLO se il vocale lo dice; chi parla
    #: non si nomina. La politica blocca chi non puo' assegnare.
    agent_name: str | None = Field(None, max_length=200)
    location_text: str | None = Field(None, max_length=500)
    notes: str | None = Field(None, max_length=5000)


class UnsupportedCommand(_Command):
    """Una richiesta fuori elenco (incarichi, acquisizioni, modifiche,
    cancellazioni, riassegnazioni...). Si blocca, si spiega, non si inventa."""
    intent: Literal["unsupported"]
    description: str = Field(..., min_length=1, max_length=500)


Command = Annotated[
    Union[
        AddNoteCommand, AddTaskCommand, CreateContactCommand, CreateUnitCommand,
        CreateBuildingCommand, LinkOwnerCommand, ActivateSellerCommand,
        CreateAppointmentCommand, UnsupportedCommand,
    ],
    Field(discriminator="intent"),
]


class VoicePlanOutput(VoiceModel):
    """Cio' che il modello restituisce, e basta."""
    commands: list[Command] = Field(default_factory=list, max_length=MAX_COMMANDS)
    #: Domande che il modello stesso non sa sciogliere dal vocale (es. "non
    #: ho capito il cognome"). Diventano richieste di chiarimento.
    clarifications: list[str] = Field(default_factory=list, max_length=MAX_COMMANDS)

    @model_validator(mode="after")
    def _riferimenti_a_passi(self):
        """Un `step` punta a un comando PRECEDENTE del tipo giusto."""
        for indice, comando in enumerate(self.commands, start=1):
            for nome, atteso in (("contact", "create_contact"), ("owner", "create_contact"),
                                 ("property", "create_unit"), ("building", "create_building")):
                rif = getattr(comando, nome, None)
                if rif is None or rif.step is None:
                    continue
                if rif.step >= indice:
                    raise ValueError(f"command {indice}: step {rif.step} must precede it")
                bersaglio = self.commands[rif.step - 1]
                if bersaglio.intent != atteso:
                    raise ValueError(f"command {indice}: step {rif.step} is not a {atteso}")
        return self


def _chiudi(schema: Any) -> None:
    """additionalProperties=false su OGNI oggetto: lo schema e' chiuso."""
    if isinstance(schema, dict):
        if schema.get("type") == "object" or "properties" in schema:
            schema.setdefault("additionalProperties", False)
        for valore in schema.values():
            _chiudi(valore)
    elif isinstance(schema, list):
        for valore in schema:
            _chiudi(valore)


def plan_json_schema() -> dict[str, Any]:
    """Lo schema da dare al fornitore (output strutturato). Chiuso ovunque."""
    schema = VoicePlanOutput.model_json_schema()
    _chiudi(schema)
    return schema


def parse_plan_output(raw: Any) -> VoicePlanOutput:
    """Convalida l'output grezzo del fornitore. Solleva `ValidationError`."""
    return VoicePlanOutput.model_validate(raw)
