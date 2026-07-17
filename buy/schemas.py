from __future__ import annotations
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from pydantic import BaseModel, Field, root_validator
from .enums import *

INTERACTION_TYPES = {"proposed","discarded","interested","visit_requested","visit_scheduled","visited","offer_candidate","other"}
REJECTION_REASONS = {"price_too_high","wrong_location","too_small","too_large","missing_elevator","wrong_floor","poor_condition","no_parking","no_outdoor_space","already_seen","not_available","buyer_decision","agent_decision","other"}

class BuyModel(BaseModel):
    class Config: extra = "forbid"

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
    next_action_at: datetime | None = None
    next_action_note: str | None = None
    finance_review_at: datetime | None = None
    finance_notes: str | None = None
    notes: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @root_validator(skip_on_failure=True)
    def validate_request(cls, values):
        if values.get("status") not in BUY_STATUSES: raise ValueError("invalid status")
        if values.get("priority") not in BUY_PRIORITIES: raise ValueError("invalid priority")
        if values.get("urgency") not in BUY_URGENCIES: raise ValueError("invalid urgency")
        if values.get("finance_status") not in FINANCE_STATUSES: raise ValueError("invalid finance_status")
        for a,b in (("budget_min","budget_target"),("budget_target","budget_max"),("surface_min","surface_target"),("surface_target","surface_max")):
            if values.get(a) is not None and values.get(b) is not None and values[a] > values[b]: raise ValueError(f"{a} cannot exceed {b}")
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
    next_action_at: datetime | None = None
    next_action_note: str | None = None
    finance_review_at: datetime | None = None
    finance_notes: str | None = None
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
    location_type: str; region: str | None = Field(None,max_length=120); province: str | None = Field(None,max_length=120); municipality: str | None = Field(None,max_length=120); microzone: str | None = Field(None,max_length=150)
    priority: int = Field(1, ge=1, le=10); radius_km: Decimal | None = Field(None, ge=0); is_required: bool = False; is_excluded: bool = False
    @root_validator(skip_on_failure=True)
    def validate_location(cls,v):
        if v.get('location_type') not in LOCATION_TYPES: raise ValueError('invalid location_type')
        if v.get('is_required') and v.get('is_excluded'): raise ValueError('location cannot be both required and excluded')
        if not any(v.get(x) for x in ('region','province','municipality','microzone')): raise ValueError('at least one location value is required')
        return v

class TypologyCreate(BuyModel):
    property_type: str = Field(...,min_length=1,max_length=80); requirement_level: str = 'preferred'; priority: int = Field(1,ge=1,le=10)
    @root_validator(skip_on_failure=True)
    def validate_typology(cls,v):
        if v.get('requirement_level') not in REQUIREMENT_LEVELS: raise ValueError('invalid requirement_level')
        return v

class FeatureCreate(BuyModel):
    feature_code: str = Field(...,min_length=1,max_length=100); requirement_level: str = 'preferred'; value_type: str = 'boolean'; value_boolean: bool | None = None
    value_min: Decimal | None = None; value_target: Decimal | None = None; value_max: Decimal | None = None; value_text: str | None = None; weight_override: Decimal | None = Field(None,ge=0,le=100)
    @root_validator(skip_on_failure=True)
    def validate_feature(cls,v):
        if v.get('requirement_level') not in REQUIREMENT_LEVELS: raise ValueError('invalid requirement_level')
        if v.get('value_type') not in FEATURE_VALUE_TYPES: raise ValueError('invalid value_type')
        if v.get('value_min') is not None and v.get('value_max') is not None and v['value_min'] > v['value_max']: raise ValueError('value_min cannot exceed value_max')
        return v

class InteractionCreate(BuyModel):
    match_id: int | None = None
    property_id: int | None = None
    property_visit_id: int | None = None
    interaction_type: str
    reason_code: str | None = Field(None,max_length=50)
    notes: str | None = None
    occurred_at: datetime | None = None
    created_by: str | None = Field(None,max_length=200)
    @root_validator(skip_on_failure=True)
    def validate_interaction(cls,v):
        if v.get('interaction_type') not in INTERACTION_TYPES: raise ValueError('invalid interaction_type')
        if not v.get('match_id') and not v.get('property_id'): raise ValueError('match_id or property_id is required')
        reason=v.get('reason_code')
        if reason is not None and reason not in REJECTION_REASONS: raise ValueError('invalid reason_code')
        if v.get('interaction_type') == 'discarded' and not reason: raise ValueError('reason_code is required for discarded interactions')
        return v

class InteractionUpdate(BuyModel):
    interaction_type: str | None = None; reason_code: str | None = None; notes: str | None = None; occurred_at: datetime | None = None; property_visit_id: int | None = None
    @root_validator(skip_on_failure=True)
    def validate_interaction(cls,v):
        if v.get('interaction_type') is not None and v['interaction_type'] not in INTERACTION_TYPES: raise ValueError('invalid interaction_type')
        reason=v.get('reason_code')
        if reason is not None and reason not in REJECTION_REASONS: raise ValueError('invalid reason_code')
        if v.get('interaction_type') == 'discarded' and not reason: raise ValueError('reason_code is required for discarded interactions')
        return v

class MatchDecision(BuyModel):
    action: str
    reason_code: str | None = None
    notes: str | None = None
    occurred_at: datetime | None = None
    created_by: str | None = None
    @root_validator(skip_on_failure=True)
    def validate_action(cls,v):
        if v.get('action') not in INTERACTION_TYPES - {'other'}: raise ValueError('invalid action')
        reason=v.get('reason_code')
        if reason is not None and reason not in REJECTION_REASONS: raise ValueError('invalid reason_code')
        if v.get('action') == 'discarded' and not reason: raise ValueError('reason_code is required when discarding a match')
        return v

class BuyTaskCreate(BuyModel):
    title: str = Field(...,min_length=1,max_length=200)
    description: str | None = None
    task_type: str | None = Field('buy_follow_up',max_length=50)
    priority: str = 'normal'
    due_at: datetime | None = None
    assigned_to: str | None = Field(None,max_length=200)
    created_by: str | None = Field(None,max_length=200)
    @root_validator(skip_on_failure=True)
    def validate_task(cls,v):
        if v.get('priority') not in BUY_PRIORITIES: raise ValueError('invalid priority')
        return v

class HistoryNoteCreate(BuyModel):
    description: str = Field(...,min_length=1)
    created_by: str | None = Field(None,max_length=200)
