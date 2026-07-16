from __future__ import annotations
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from pydantic import BaseModel, Field, root_validator
from .enums import *

class BuyModel(BaseModel):
    class Config:
        extra = "forbid"

class BuyRequestCreate(BuyModel):
    contact_id: int
    lead_id: int | None = None
    title: str = Field(..., min_length=1, max_length=200)
    status: str = "draft"
    priority: str = "normal"
    urgency: str = "flexible"
    assigned_to: str | None = Field(None, max_length=200)
    search_start_date: date | None = None
    target_purchase_date: date | None = None
    budget_min: Decimal | None = Field(None, ge=0)
    budget_target: Decimal | None = Field(None, ge=0)
    budget_max: Decimal | None = Field(None, ge=0)
    budget_flexibility_percent: Decimal = Field(0, ge=0, le=100)
    includes_agency_fees: bool = False
    includes_renovation: bool = False
    finance_status: str = "unknown"
    mortgage_required: bool | None = None
    mortgage_preapproved: bool | None = None
    available_cash: Decimal | None = Field(None, ge=0)
    maximum_monthly_payment: Decimal | None = Field(None, ge=0)
    property_to_sell_first: bool = False
    surface_min: Decimal | None = Field(None, ge=0)
    surface_target: Decimal | None = Field(None, ge=0)
    surface_max: Decimal | None = Field(None, ge=0)
    rooms_min: int | None = Field(None, ge=0)
    bedrooms_min: int | None = Field(None, ge=0)
    bathrooms_min: int | None = Field(None, ge=0)
    notes: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @root_validator(skip_on_failure=True)
    def validate_request(cls, values):
        if values.get("status") not in BUY_STATUSES: raise ValueError("invalid status")
        if values.get("priority") not in BUY_PRIORITIES: raise ValueError("invalid priority")
        if values.get("urgency") not in BUY_URGENCIES: raise ValueError("invalid urgency")
        if values.get("finance_status") not in FINANCE_STATUSES: raise ValueError("invalid finance_status")
        for a,b in (("budget_min","budget_target"),("budget_target","budget_max"),("surface_min","surface_target"),("surface_target","surface_max")):
            if values.get(a) is not None and values.get(b) is not None and values[a] > values[b]:
                raise ValueError(f"{a} cannot exceed {b}")
        return values

class BuyRequestUpdate(BuyModel):
    lead_id: int | None = None
    title: str | None = Field(None, min_length=1, max_length=200)
    status: str | None = None
    priority: str | None = None
    urgency: str | None = None
    assigned_to: str | None = Field(None, max_length=200)
    search_start_date: date | None = None
    target_purchase_date: date | None = None
    budget_min: Decimal | None = Field(None, ge=0)
    budget_target: Decimal | None = Field(None, ge=0)
    budget_max: Decimal | None = Field(None, ge=0)
    budget_flexibility_percent: Decimal | None = Field(None, ge=0, le=100)
    includes_agency_fees: bool | None = None
    includes_renovation: bool | None = None
    finance_status: str | None = None
    mortgage_required: bool | None = None
    mortgage_preapproved: bool | None = None
    available_cash: Decimal | None = Field(None, ge=0)
    maximum_monthly_payment: Decimal | None = Field(None, ge=0)
    property_to_sell_first: bool | None = None
    surface_min: Decimal | None = Field(None, ge=0)
    surface_target: Decimal | None = Field(None, ge=0)
    surface_max: Decimal | None = Field(None, ge=0)
    rooms_min: int | None = Field(None, ge=0)
    bedrooms_min: int | None = Field(None, ge=0)
    bathrooms_min: int | None = Field(None, ge=0)
    notes: str | None = None
    metadata: dict[str, Any] | None = None
    archived_at: datetime | None = None

    @root_validator(skip_on_failure=True)
    def validate_update(cls, values):
        if values.get("status") is not None and values["status"] not in BUY_STATUSES: raise ValueError("invalid status")
        if values.get("priority") is not None and values["priority"] not in BUY_PRIORITIES: raise ValueError("invalid priority")
        if values.get("urgency") is not None and values["urgency"] not in BUY_URGENCIES: raise ValueError("invalid urgency")
        if values.get("finance_status") is not None and values["finance_status"] not in FINANCE_STATUSES: raise ValueError("invalid finance_status")
        return values

class LocationCreate(BuyModel):
    location_type: str
    region: str | None = Field(None,max_length=120)
    province: str | None = Field(None,max_length=120)
    municipality: str | None = Field(None,max_length=120)
    microzone: str | None = Field(None,max_length=150)
    priority: int = Field(1, ge=1, le=10)
    radius_km: Decimal | None = Field(None, ge=0)
    is_required: bool = False
    is_excluded: bool = False
    @root_validator(skip_on_failure=True)
    def validate_location(cls,v):
        if v.get('location_type') not in LOCATION_TYPES: raise ValueError('invalid location_type')
        if v.get('is_required') and v.get('is_excluded'): raise ValueError('location cannot be both required and excluded')
        if not any(v.get(x) for x in ('region','province','municipality','microzone')): raise ValueError('at least one location value is required')
        return v

class TypologyCreate(BuyModel):
    property_type: str = Field(...,min_length=1,max_length=80)
    requirement_level: str = 'preferred'
    priority: int = Field(1,ge=1,le=10)
    @root_validator(skip_on_failure=True)
    def validate_typology(cls,v):
        if v.get('requirement_level') not in REQUIREMENT_LEVELS: raise ValueError('invalid requirement_level')
        return v

class FeatureCreate(BuyModel):
    feature_code: str = Field(...,min_length=1,max_length=100)
    requirement_level: str = 'preferred'
    value_type: str = 'boolean'
    value_boolean: bool | None = None
    value_min: Decimal | None = None
    value_target: Decimal | None = None
    value_max: Decimal | None = None
    value_text: str | None = None
    weight_override: Decimal | None = Field(None,ge=0,le=100)
    @root_validator(skip_on_failure=True)
    def validate_feature(cls,v):
        if v.get('requirement_level') not in REQUIREMENT_LEVELS: raise ValueError('invalid requirement_level')
        if v.get('value_type') not in FEATURE_VALUE_TYPES: raise ValueError('invalid value_type')
        if v.get('value_min') is not None and v.get('value_max') is not None and v['value_min'] > v['value_max']: raise ValueError('value_min cannot exceed value_max')
        return v
