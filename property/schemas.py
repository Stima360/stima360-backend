from __future__ import annotations
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from pydantic import BaseModel, Field, root_validator
from .enums import *

class PropertyModel(BaseModel):
    class Config:
        extra = "forbid"

class PropertyCreate(PropertyModel):
    code: str | None = Field(None,max_length=50)
    title: str = Field(...,min_length=1,max_length=200)
    property_type: str = "apartment"
    commercial_status: str = "draft"
    classification: str | None = None
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
    source: str | None = Field(None,max_length=100)
    public_notes: str | None = None
    internal_notes: str | None = None
    metadata: dict[str,Any] = Field(default_factory=dict)
    @root_validator(skip_on_failure=True)
    def validate_values(cls,v):
        if v.get('property_type') not in PROPERTY_TYPES: raise ValueError('invalid property_type')
        if v.get('commercial_status') not in PROPERTY_STATUSES: raise ValueError('invalid commercial_status')
        if v.get('classification') is not None and v['classification'] not in PROPERTY_CLASSES: raise ValueError('classification must be A, B or C')
        if v.get('mandate_start') and v.get('mandate_end') and v['mandate_end'] < v['mandate_start']: raise ValueError('mandate_end cannot precede mandate_start')
        return v

class PropertyUpdate(PropertyModel):
    code: str | None = Field(None,max_length=50)
    title: str | None = Field(None,min_length=1,max_length=200)
    property_type: str | None = None
    commercial_status: str | None = None
    classification: str | None = None
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
    source: str | None = Field(None,max_length=100)
    public_notes: str | None = None
    internal_notes: str | None = None
    metadata: dict[str,Any] | None = None
    archived_at: datetime | None = None
    change_reason: str | None = Field(None,max_length=200)
    changed_by: str | None = Field(None,max_length=200)
    history_note: str | None = None
    @root_validator(skip_on_failure=True)
    def validate_update(cls,v):
        if v.get('property_type') is not None and v['property_type'] not in PROPERTY_TYPES: raise ValueError('invalid property_type')
        if v.get('commercial_status') is not None and v['commercial_status'] not in PROPERTY_STATUSES: raise ValueError('invalid commercial_status')
        if v.get('classification') is not None and v['classification'] not in PROPERTY_CLASSES: raise ValueError('classification must be A, B or C')
        if v.get('mandate_start') and v.get('mandate_end') and v['mandate_end'] < v['mandate_start']: raise ValueError('mandate_end cannot precede mandate_start')
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
