from __future__ import annotations
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID
from pydantic import BaseModel, Field, model_validator, root_validator
from .enums import *

class PropertyModel(BaseModel):
    class Config:
        extra = "forbid"

class PropertyCreate(PropertyModel):
    # CRM-OPS-2: ne' il codice ne' il titolo si chiedono piu' all'operatore.
    # Se mancano li genera il backend (property/service.py): IMM-<id> e una
    # descrizione sintetica dai dati. Restano accettati per i client storici.
    code: str | None = Field(None,max_length=50)
    title: str | None = Field(None,min_length=1,max_length=200)
    property_type: str = "apartment"
    commercial_status: str = "draft"
    classification: str | None = None
    region: str | None = Field(None,max_length=50)
    address: str | None = Field(None,max_length=250)
    civic_number: str | None = Field(None,max_length=30)
    city: str | None = Field(None,max_length=120)
    province: str | None = Field(None,max_length=10)
    postal_code: str | None = Field(None,max_length=20)
    microzone: str | None = Field(None,max_length=150)
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    surface_sqm: Decimal | None = Field(None,ge=0)
    commercial_surface_sqm: Decimal | None = Field(None,ge=0)
    rooms: int | None = Field(None,ge=0)
    bedrooms: int | None = Field(None,ge=0)
    bathrooms: int | None = Field(None,ge=0)
    floor: str | None = Field(None,max_length=50)
    total_floors: int | None = Field(None,ge=0)
    elevator: bool | None = None
    year_built: int | None = Field(None,ge=1000,le=2200)
    condition: str | None = Field(None,max_length=80)
    energy_class: str | None = Field(None,max_length=20)
    asking_price: Decimal | None = Field(None,ge=0)
    minimum_price: Decimal | None = Field(None,ge=0)
    mandate_type: str | None = Field(None,max_length=80)
    mandate_start: date | None = None
    mandate_end: date | None = None
    assigned_to: str | None = Field(None,max_length=200)
    # CRM-OPS-2: l'identificativo reale dell'agente. Chi puo' assegnare e a chi
    # lo decide il service (permessi + membership attiva della stessa agenzia).
    assigned_agent_id: int | None = Field(None,gt=0)
    source: str | None = Field(None,max_length=100)
    public_notes: str | None = None
    internal_notes: str | None = None
    metadata: dict[str,Any] = Field(default_factory=dict)
    # CENSIMENTO-1 Fase 3: dati catastali e posizione nella palazzina
    # (migration 083). `record_kind`, `building_id`, `parent_property_id`,
    # `address_inherited` e `client_request_id` NON sono campi di questo
    # schema: i collegamenti e la presa in carico passano dalle rotte
    # dedicate (property/census.py), mai da un POST/PATCH generico.
    staircase: str | None = Field(None,max_length=10)
    internal_number: str | None = Field(None,max_length=10)
    cadastral_municipality_code: str | None = Field(None,max_length=4)
    cadastral_section: str | None = Field(None,max_length=5)     # None = non conosciuta, '' = assente
    cadastral_sheet: str | None = Field(None,max_length=10)
    cadastral_parcel: str | None = Field(None,max_length=10)
    cadastral_subunit: str | None = Field(None,max_length=10)
    cadastral_category: str | None = Field(None,max_length=5)    # None = "Da verificare"
    # CATALOGO-CANONICO-1 (migration 087): i dati del sito stima360.it che non
    # avevano una colonna. NULL = non dichiarato; i valori di catalogo
    # (posizione e distanza dal mare) li giudica il service. Entrano
    # nell'INSERT solo se inviati (property/service.py::SITE_SCHEMA_FIELDS).
    sea_position: str | None = Field(None,max_length=30)
    sea_distance: str | None = Field(None,max_length=30)
    sea_band: str | None = Field(None,max_length=32)
    sea_barrier: bool | None = None
    sea_view: bool | None = None
    sea_view_detail: str | None = Field(None,max_length=200)
    heating: str | None = Field(None,max_length=120)
    air_conditioning: str | None = Field(None,max_length=120)
    air_conditioning_type: str | None = Field(None,max_length=120)
    exposure: str | None = Field(None,max_length=120)
    furnishing: str | None = Field(None,max_length=120)
    condo_fees: Decimal | None = Field(None,ge=0)
    other_features: str | None = None
    @root_validator(skip_on_failure=True)
    def validate_values(cls,v):
        if v.get('property_type') not in PROPERTY_TYPES: raise ValueError('invalid property_type')
        if v.get('commercial_status') not in PROPERTY_STATUSES: raise ValueError('invalid commercial_status')
        if v.get('classification') is not None and v['classification'] not in PROPERTY_CLASSES: raise ValueError('classification must be A, B or C')
        if v.get('mandate_start') and v.get('mandate_end') and v['mandate_end'] < v['mandate_start']: raise ValueError('mandate_end cannot precede mandate_start')
        # CRM-OPS-2: catalogo territoriale e classe energetica si applicano nel
        # service, dove si sa COSA e' stato inviato e cosa era gia' salvato:
        # i client storici (property_admin) non devono trovare blocchi nuovi.
        return v

class PropertyUpdate(PropertyModel):
    code: str | None = Field(None,max_length=50)
    title: str | None = Field(None,min_length=1,max_length=200)
    property_type: str | None = None
    commercial_status: str | None = None
    classification: str | None = None
    region: str | None = Field(None,max_length=50)
    address: str | None = Field(None,max_length=250)
    civic_number: str | None = Field(None,max_length=30)
    city: str | None = Field(None,max_length=120)
    province: str | None = Field(None,max_length=10)
    postal_code: str | None = Field(None,max_length=20)
    microzone: str | None = Field(None,max_length=150)
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    surface_sqm: Decimal | None = Field(None,ge=0)
    commercial_surface_sqm: Decimal | None = Field(None,ge=0)
    rooms: int | None = Field(None,ge=0)
    bedrooms: int | None = Field(None,ge=0)
    bathrooms: int | None = Field(None,ge=0)
    floor: str | None = Field(None,max_length=50)
    total_floors: int | None = Field(None,ge=0)
    elevator: bool | None = None
    year_built: int | None = Field(None,ge=1000,le=2200)
    condition: str | None = Field(None,max_length=80)
    energy_class: str | None = Field(None,max_length=20)
    asking_price: Decimal | None = Field(None,ge=0)
    minimum_price: Decimal | None = Field(None,ge=0)
    mandate_type: str | None = Field(None,max_length=80)
    mandate_start: date | None = None
    mandate_end: date | None = None
    assigned_to: str | None = Field(None,max_length=200)
    # CRM-OPS-2: l'identificativo reale dell'agente. Chi puo' assegnare e a chi
    # lo decide il service (permessi + membership attiva della stessa agenzia).
    assigned_agent_id: int | None = Field(None,gt=0)
    source: str | None = Field(None,max_length=100)
    public_notes: str | None = None
    internal_notes: str | None = None
    metadata: dict[str,Any] | None = None
    archived_at: datetime | None = None
    change_reason: str | None = Field(None,max_length=200)
    changed_by: str | None = Field(None,max_length=200)
    history_note: str | None = None
    # CENSIMENTO-1 Fase 3: vedi PropertyCreate. Un campo di indirizzo inviato
    # su un'unita' con indirizzo ereditato dalla palazzina la rende
    # personalizzata ("Ingresso diverso?"): lo decide il service.
    staircase: str | None = Field(None,max_length=10)
    internal_number: str | None = Field(None,max_length=10)
    cadastral_municipality_code: str | None = Field(None,max_length=4)
    cadastral_section: str | None = Field(None,max_length=5)
    cadastral_sheet: str | None = Field(None,max_length=10)
    cadastral_parcel: str | None = Field(None,max_length=10)
    cadastral_subunit: str | None = Field(None,max_length=10)
    cadastral_category: str | None = Field(None,max_length=5)
    # CATALOGO-CANONICO-1 (migration 087): i dati del sito stima360.it che non
    # avevano una colonna. NULL = non dichiarato; i valori di catalogo
    # (posizione e distanza dal mare) li giudica il service. Entrano
    # nell'INSERT solo se inviati (property/service.py::SITE_SCHEMA_FIELDS).
    sea_position: str | None = Field(None,max_length=30)
    sea_distance: str | None = Field(None,max_length=30)
    sea_band: str | None = Field(None,max_length=32)
    sea_barrier: bool | None = None
    sea_view: bool | None = None
    sea_view_detail: str | None = Field(None,max_length=200)
    heating: str | None = Field(None,max_length=120)
    air_conditioning: str | None = Field(None,max_length=120)
    air_conditioning_type: str | None = Field(None,max_length=120)
    exposure: str | None = Field(None,max_length=120)
    furnishing: str | None = Field(None,max_length=120)
    condo_fees: Decimal | None = Field(None,ge=0)
    other_features: str | None = None
    @root_validator(skip_on_failure=True)
    def validate_update(cls,v):
        if v.get('property_type') is not None and v['property_type'] not in PROPERTY_TYPES: raise ValueError('invalid property_type')
        if v.get('commercial_status') is not None and v['commercial_status'] not in PROPERTY_STATUSES: raise ValueError('invalid commercial_status')
        if v.get('classification') is not None and v['classification'] not in PROPERTY_CLASSES: raise ValueError('classification must be A, B or C')
        if v.get('mandate_start') and v.get('mandate_end') and v['mandate_end'] < v['mandate_start']: raise ValueError('mandate_end cannot precede mandate_start')
        # CRM-OPS-2: vedi PropertyCreate - le regole del catalogo sono nel service.
        return v

class PropertyContactCreate(PropertyModel):
    contact_id: int
    role: str = 'owner'
    is_primary: bool = False
    ownership_share: Decimal | None = Field(None,ge=0,le=100)
    notes: str | None = None
    @root_validator(skip_on_failure=True)
    def validate_role(cls,v):
        if v.get('role') not in PROPERTY_CONTACT_ROLES: raise ValueError('invalid role')
        return v

class PropertyLeadCreate(PropertyModel):
    lead_id: int
    relation_type: str = 'origin'
    @root_validator(skip_on_failure=True)
    def validate_relation(cls,v):
        if v.get('relation_type') not in PROPERTY_LEAD_RELATIONS: raise ValueError('invalid relation_type')
        return v

class DocumentCreate(PropertyModel):
    document_type: str = Field(...,min_length=1,max_length=80)
    title: str = Field(...,min_length=1,max_length=200)
    url: str | None = None
    storage_key: str | None = None
    status: str = 'available'
    expires_at: date | None = None
    notes: str | None = None
    metadata: dict[str,Any] = Field(default_factory=dict)
    @root_validator(skip_on_failure=True)
    def validate_doc(cls,v):
        if v.get('status') not in DOCUMENT_STATUSES: raise ValueError('invalid document status')
        if not v.get('url') and not v.get('storage_key') and v.get('status') not in {'missing','requested'}: raise ValueError('url or storage_key required')
        return v

class DocumentUpdate(PropertyModel):
    document_type: str | None = Field(None,min_length=1,max_length=80)
    title: str | None = Field(None,min_length=1,max_length=200)
    url: str | None = None
    storage_key: str | None = None
    status: str | None = None
    expires_at: date | None = None
    notes: str | None = None
    metadata: dict[str,Any] | None = None
    @root_validator(skip_on_failure=True)
    def validate_doc(cls,v):
        if v.get('status') is not None and v['status'] not in DOCUMENT_STATUSES: raise ValueError('invalid document status')
        return v

class PhotoCreate(PropertyModel):
    url: str = Field(...,min_length=1)
    title: str | None = Field(None,max_length=200)
    sort_order: int = Field(0,ge=0)
    is_cover: bool = False
    metadata: dict[str,Any] = Field(default_factory=dict)

class PhotoUpdate(PropertyModel):
    url: str | None = Field(None,min_length=1)
    title: str | None = Field(None,max_length=200)
    sort_order: int | None = Field(None,ge=0)
    is_cover: bool | None = None
    metadata: dict[str,Any] | None = None

class VisitCreate(PropertyModel):
    contact_id: int | None = None
    lead_id: int | None = None
    scheduled_at: datetime
    status: str = 'scheduled'
    outcome: str | None = Field(None,max_length=80)
    feedback: str | None = None
    rating: int | None = Field(None,ge=1,le=5)
    assigned_to: str | None = Field(None,max_length=200)
    created_by: str | None = Field(None,max_length=200)
    # A31-3: una visita futura da svolgere nasce nell'Agenda. L'agente si
    # indica esplicitamente (owner/admin, A31-4 in UI); un agent e' se stesso.
    assigned_user_id: int | None = Field(None, gt=0)
    client_request_id: str | None = Field(None, max_length=64)
    @root_validator(skip_on_failure=True)
    def validate_status(cls,v):
        if v.get('status') not in VISIT_STATUSES: raise ValueError('invalid visit status')
        return v

class VisitUpdate(PropertyModel):
    contact_id: int | None = None
    lead_id: int | None = None
    scheduled_at: datetime | None = None
    status: str | None = None
    outcome: str | None = Field(None,max_length=80)
    feedback: str | None = None
    rating: int | None = Field(None,ge=1,le=5)
    assigned_to: str | None = Field(None,max_length=200)
    created_by: str | None = Field(None,max_length=200)
    @root_validator(skip_on_failure=True)
    def validate_status(cls,v):
        if v.get('status') is not None and v['status'] not in VISIT_STATUSES: raise ValueError('invalid visit status')
        return v


# CRM-OPS-4: un'interazione con il proprietario (telefonata, incontro, nota).
# Data, ora e autore NON sono campi: li mette il server. `extra=forbid`
# (PropertyModel) rifiuta anche agency_id, occurred_at, created_by_user_id.
class InteractionCreate(PropertyModel):
    interaction_type: str = Field(..., min_length=1, max_length=30)
    note: str = Field(..., min_length=1, max_length=5000)
    contact_id: int | None = Field(None, ge=1)
    # VENDITORI-1: dall'area Venditori l'interazione porta anche il lead SELL
    # dell'opportunita' (contesto 'seller'): la STESSA riga di activities.
    lead_id: int | None = Field(None, ge=1)
    context: str = Field("property", pattern="^(property|mandate|seller)$")


# ---------------------------------------------------------------------------
# CENSIMENTO-1 Fase 3 - edifici, unita' di censimento, pertinenze, accessori.
# Contratti del progetto CENSIMENTO-0 (§0 p.5-6, §2, §4, §6, §7). Tutti
# `extra=forbid`: `agency_id`, `record_kind`, `address_inherited`,
# `client_request_fingerprint` e ogni altro campo deciso dal server sono
# rifiutati con 422 prima del service.
# ---------------------------------------------------------------------------

BUILDING_TYPES = ("condominio", "villa", "rustico", "capannone", "commerciale", "misto", "altro")
BUILDING_CENSUS_STATUSES = ("verified", "partial", "estimated")
UNITS_DECLARED_SOURCES = ("survey", "cadastre", "owner", "unknown")
# CATALOGO-CANONICO-1 (migration 087): + i tipi del sito. Il "garage" del sito e' il `box`.
ACCESSORY_KINDS = ("cantina", "soffitta", "posto_auto", "giardino", "terrazzo", "box", "deposito", "altro",
                   "taverna", "balcone", "piscina", "posto_moto", "posto_bici")
ACCESSORY_STATUSES = ("included", "unknown")

_CADASTRAL_UNIT_FIELDS = ("cadastral_municipality_code", "cadastral_section", "cadastral_sheet",
                          "cadastral_parcel", "cadastral_subunit", "cadastral_category")


def _mai_null(model, campi):
    """PATCH: campo OMESSO = invariato; `null` ESPLICITO = azzera - ma solo
    dove la 083 lo ammette. Su una colonna NOT NULL un `null` esplicito e'
    un errore di validazione (422), non un 500 dal database."""
    for campo in campi:
        if campo in model.model_fields_set and getattr(model, campo) is None:
            raise ValueError(f"{campo} non puo' essere null")
    return model


class BuildingCreate(PropertyModel):
    client_request_id: UUID | None = None
    confirm_similar: bool = False          # S8: "Salva comunque" sugli avvisi non bloccanti
    building_type: str = "condominio"
    name: str | None = Field(None, max_length=120)
    region: str | None = Field(None, max_length=50)
    province: str | None = Field(None, max_length=10)
    city: str | None = Field(None, max_length=120)
    microzone: str | None = Field(None, max_length=150)
    address: str | None = Field(None, max_length=250)
    civic_number: str | None = Field(None, max_length=30)
    postal_code: str | None = Field(None, max_length=20)
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    cadastral_municipality_code: str | None = Field(None, max_length=4)
    cadastral_section: str | None = Field(None, max_length=5)
    cadastral_sheet: str | None = Field(None, max_length=10)
    cadastral_parcel: str | None = Field(None, max_length=10)
    floors_above_ground: int | None = Field(None, ge=0)
    year_built: int | None = Field(None, ge=1000, le=2200)
    elevator: bool | None = None
    units_declared: int | None = Field(None, ge=0)
    units_declared_source: str | None = None
    census_status: str = "partial"
    notes: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @root_validator(skip_on_failure=True)
    def validate_building(cls, v):
        if v.get("building_type") not in BUILDING_TYPES: raise ValueError("invalid building_type")
        if v.get("census_status") not in BUILDING_CENSUS_STATUSES: raise ValueError("invalid census_status")
        if v.get("units_declared_source") is not None and v["units_declared_source"] not in UNITS_DECLARED_SOURCES:
            raise ValueError("invalid units_declared_source")
        return v


class BuildingUpdate(PropertyModel):
    building_type: str | None = None
    name: str | None = Field(None, max_length=120)
    region: str | None = Field(None, max_length=50)
    province: str | None = Field(None, max_length=10)
    city: str | None = Field(None, max_length=120)
    microzone: str | None = Field(None, max_length=150)
    address: str | None = Field(None, max_length=250)
    civic_number: str | None = Field(None, max_length=30)
    postal_code: str | None = Field(None, max_length=20)
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    cadastral_municipality_code: str | None = Field(None, max_length=4)
    cadastral_section: str | None = Field(None, max_length=5)
    cadastral_sheet: str | None = Field(None, max_length=10)
    cadastral_parcel: str | None = Field(None, max_length=10)
    floors_above_ground: int | None = Field(None, ge=0)
    year_built: int | None = Field(None, ge=1000, le=2200)
    elevator: bool | None = None
    units_declared: int | None = Field(None, ge=0)
    units_declared_source: str | None = None
    census_status: str | None = None
    notes: str | None = None
    metadata: dict[str, Any] | None = None

    @root_validator(skip_on_failure=True)
    def validate_building(cls, v):
        if v.get("building_type") is not None and v["building_type"] not in BUILDING_TYPES:
            raise ValueError("invalid building_type")
        if v.get("census_status") is not None and v["census_status"] not in BUILDING_CENSUS_STATUSES:
            raise ValueError("invalid census_status")
        if v.get("units_declared_source") is not None and v["units_declared_source"] not in UNITS_DECLARED_SOURCES:
            raise ValueError("invalid units_declared_source")
        return v

    @model_validator(mode="after")
    def no_null_on_not_null_columns(self):
        # NOT NULL nella 083: building_type, census_status, metadata
        return _mai_null(self, ("building_type", "census_status", "metadata"))


class CensusUnitCreate(PropertyModel):
    """Una scheda di censimento (`record_kind = 'census'`): in palazzina
    (`building_id`), come pertinenza di un'unita' (`parent_property_id`),
    entrambe, o singola. Nessun campo obbligatorio oltre la tipologia."""
    client_request_id: UUID | None = None
    confirm_similar: bool = False
    building_id: int | None = Field(None, ge=1)
    parent_property_id: int | None = Field(None, ge=1)
    property_type: str = "apartment"
    whole_building: bool = False
    floor: str | None = Field(None, max_length=50)
    staircase: str | None = Field(None, max_length=10)
    internal_number: str | None = Field(None, max_length=10)
    surface_sqm: Decimal | None = Field(None, ge=0)
    commercial_surface_sqm: Decimal | None = Field(None, ge=0)
    rooms: int | None = Field(None, ge=0)
    bedrooms: int | None = Field(None, ge=0)
    bathrooms: int | None = Field(None, ge=0)
    cadastral_municipality_code: str | None = Field(None, max_length=4)
    cadastral_section: str | None = Field(None, max_length=5)
    cadastral_sheet: str | None = Field(None, max_length=10)
    cadastral_parcel: str | None = Field(None, max_length=10)
    cadastral_subunit: str | None = Field(None, max_length=10)
    cadastral_category: str | None = Field(None, max_length=5)
    # indirizzo PROPRIO ("Ingresso diverso?"): se assente e c'e' la palazzina,
    # l'indirizzo e' ereditato e materializzato dall'edificio
    region: str | None = Field(None, max_length=50)
    province: str | None = Field(None, max_length=10)
    city: str | None = Field(None, max_length=120)
    microzone: str | None = Field(None, max_length=150)
    address: str | None = Field(None, max_length=250)
    civic_number: str | None = Field(None, max_length=30)
    postal_code: str | None = Field(None, max_length=20)
    public_notes: str | None = None
    internal_notes: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    # CREAZIONE-GUIDATA-1: la stessa creazione (palazzina, indirizzo ereditato,
    # duplicati, idempotenza) anche per una scheda COMMERCIALE quando si parte
    # dall'elenco Commerciale. Default `census`: i client esistenti non cambiano.
    # L'assegnazione vale solo per la scheda commerciale, con le regole di
    # `POST /properties` (agente -> se stesso; titolare/admin -> agente attivo).
    record_kind: str = "census"
    assigned_agent_id: int | None = Field(None, gt=0)

    # PERTINENZE-1 (089): una pertinenza autonoma (con subalterno proprio),
    # collegata o no a un'unita' principale. `pertinenza_kind` con il catalogo
    # degli accessori (box e posto auto distinti); implica `is_pertinenza`.
    is_pertinenza: bool = False
    pertinenza_kind: str | None = None

    @root_validator(skip_on_failure=True)
    def validate_unit(cls, v):
        if v.get("pertinenza_kind") is not None and v["pertinenza_kind"] not in ACCESSORY_KINDS:
            raise ValueError("invalid pertinenza_kind")
        if v.get("property_type") not in PROPERTY_TYPES: raise ValueError("invalid property_type")
        if v.get("record_kind") not in ("census", "crm"): raise ValueError("invalid record_kind")
        if v.get("record_kind") == "census" and v.get("assigned_agent_id") is not None:
            raise ValueError("assigned_agent_id is only for commercial (crm) units")
        return v


class PertinenzaLink(PropertyModel):
    pertinenza_id: int = Field(..., ge=1)


class AccessoryCreate(PropertyModel):
    client_request_id: UUID | None = None
    kind: str
    cadastral_status: str = "included"      # "No" = included; "Non lo so" = unknown
    surface_sqm: Decimal | None = Field(None, ge=0)
    quantity: int | None = Field(None, ge=1)        # CATALOGO-CANONICO-1: NULL = non indicata
    notes: str | None = None

    @root_validator(skip_on_failure=True)
    def validate_accessory(cls, v):
        if v.get("kind") not in ACCESSORY_KINDS: raise ValueError("invalid kind")
        if v.get("cadastral_status") not in ACCESSORY_STATUSES: raise ValueError("invalid cadastral_status")
        return v


class AccessoryUpdate(PropertyModel):
    kind: str | None = None
    surface_sqm: Decimal | None = Field(None, ge=0)
    quantity: int | None = Field(None, ge=1)
    notes: str | None = None

    @root_validator(skip_on_failure=True)
    def validate_accessory(cls, v):
        if v.get("kind") is not None and v["kind"] not in ACCESSORY_KINDS: raise ValueError("invalid kind")
        return v

    @model_validator(mode="after")
    def no_null_on_not_null_columns(self):
        return _mai_null(self, ("kind",))          # NOT NULL nella 083


class AccessoryResolve(PropertyModel):
    """«Chiarisci»: `included` (e' compresa) oppure `separate` (e' separata):
    nasce la pertinenza collegata con tipo, mq e note travasati - o si collega
    un immobile gia' censito (`existing_property_id`) - e l'accessorio viene
    rimosso nella stessa transazione."""
    outcome: str = Field(..., pattern="^(included|separate)$")
    existing_property_id: int | None = Field(None, ge=1)
    property_type: str | None = None
    client_request_id: UUID | None = None
    cadastral_municipality_code: str | None = Field(None, max_length=4)
    cadastral_section: str | None = Field(None, max_length=5)
    cadastral_sheet: str | None = Field(None, max_length=10)
    cadastral_parcel: str | None = Field(None, max_length=10)
    cadastral_subunit: str | None = Field(None, max_length=10)
    cadastral_category: str | None = Field(None, max_length=5)

    @root_validator(skip_on_failure=True)
    def validate_resolve(cls, v):
        if v.get("property_type") is not None and v["property_type"] not in PROPERTY_TYPES:
            raise ValueError("invalid property_type")
        if v.get("outcome") == "included" and (v.get("existing_property_id") or v.get("property_type")):
            raise ValueError("included takes no property fields")
        # la chiave di idempotenza vive sulla riga CREATA: solo `separate` con
        # creazione la porta. Con `existing_property_id` o `included` nessuna
        # riga nuova la conserverebbe, e una chiave ignorata in silenzio
        # sarebbe un'idempotenza dichiarata e falsa: rifiutata.
        if v.get("client_request_id") is not None and (v.get("outcome") != "separate" or v.get("existing_property_id")):
            raise ValueError("client_request_id vale solo per `separate` con creazione di una pertinenza nuova")
        return v


class TakeInCharge(PropertyModel):
    include_pertinenze: bool = True


class PropertyTrash(PropertyModel):
    """DELETE-ARCH Fase 2B1: «Sposta nel Cestino». Il motivo e' validato sul
    catalogo dal service (400 INVALID_TRASH_REASON)."""
    reason_code: str = Field(..., max_length=30)
    note: str | None = Field(None, max_length=500)


# ---------------------------------------------------------------------------
# CATALOGO-CANONICO-1: provenienza dal sito (property/site_sync.py)
# ---------------------------------------------------------------------------

class SiteConflictResolve(PropertyModel):
    """«Applica» (scrive il valore del sito) o «Ignora» (lo lascia com'e')."""
    conflict_id: str = Field(..., min_length=1, max_length=40)
    action: str = Field(..., pattern="^(apply|ignore)$")


class SiteRelink(PropertyModel):
    """«Collega questa stima a un altro immobile»: per id o per codice IMM."""
    target_property_id: int | None = Field(None, ge=1)
    target_code: str | None = Field(None, min_length=1, max_length=50)

    @root_validator(skip_on_failure=True)
    def one_target(cls, v):
        if (v.get("target_property_id") is None) == (v.get("target_code") is None):
            raise ValueError("indica target_property_id oppure target_code")
        return v
