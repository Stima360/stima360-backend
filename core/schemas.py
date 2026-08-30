"""Pydantic request/response schemas for the STIMA360 CORE API."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, root_validator

from .enums import (
    ACTIVITY_DIRECTIONS,
    ACTIVITY_TYPES,
    CONTACT_ROLES,
    CONTACT_STATUSES,
    CONTACT_TYPES,
    LEAD_PIPELINES,
    LEAD_STAGES,
    LEAD_STATUSES,
    LEAD_STIMA_RELATIONS,
    PRIORITIES,
    TASK_STATUSES,
)


class CoreModel(BaseModel):
    class Config:
        extra = "forbid"


class ContactCreate(CoreModel):
    contact_type: str = "person"
    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    company_name: str | None = Field(default=None, max_length=200)
    display_name: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=50)
    secondary_phone: str | None = Field(default=None, max_length=50)
    source: str | None = Field(default=None, max_length=100)
    status: str = "active"
    marketing_consent: bool | None = None
    marketing_consent_at: datetime | None = None
    notes: str | None = None

    @root_validator(skip_on_failure=True)
    def validate_contact(cls, values):
        contact_type = values.get("contact_type")
        if contact_type not in CONTACT_TYPES:
            raise ValueError(f"contact_type must be one of {sorted(CONTACT_TYPES)}")
        if values.get("status") not in CONTACT_STATUSES:
            raise ValueError(f"status must be one of {sorted(CONTACT_STATUSES)}")
        if contact_type == "company" and not values.get("company_name"):
            raise ValueError("company_name is required for company contacts")
        if contact_type == "person" and not any(
            [values.get("first_name"), values.get("last_name"), values.get("display_name")]
        ):
            raise ValueError("a person contact requires first_name, last_name or display_name")
        if values.get("marketing_consent") and values.get("marketing_consent_at") is None:
            values["marketing_consent_at"] = datetime.utcnow()
        return values


class ContactUpdate(CoreModel):
    contact_type: str | None = None
    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    company_name: str | None = Field(default=None, max_length=200)
    display_name: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=50)
    secondary_phone: str | None = Field(default=None, max_length=50)
    source: str | None = Field(default=None, max_length=100)
    status: str | None = None
    marketing_consent: bool | None = None
    marketing_consent_at: datetime | None = None
    notes: str | None = None
    archived_at: datetime | None = None

    @root_validator(skip_on_failure=True)
    def validate_values(cls, values):
        if values.get("contact_type") is not None and values["contact_type"] not in CONTACT_TYPES:
            raise ValueError(f"contact_type must be one of {sorted(CONTACT_TYPES)}")
        if values.get("status") is not None and values["status"] not in CONTACT_STATUSES:
            raise ValueError(f"status must be one of {sorted(CONTACT_STATUSES)}")
        return values


class ContactRoleCreate(CoreModel):
    role: str
    is_primary: bool = False
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @root_validator(skip_on_failure=True)
    def validate_role(cls, values):
        if values.get("role") not in CONTACT_ROLES:
            raise ValueError(f"role must be one of {sorted(CONTACT_ROLES)}")
        if values.get("valid_from") and values.get("valid_to") and values["valid_to"] < values["valid_from"]:
            raise ValueError("valid_to cannot precede valid_from")
        return values


class LeadCreate(CoreModel):
    contact_id: int
    source: str | None = Field(default=None, max_length=100)
    pipeline: str = "general"
    stage: str = "new"
    priority: str = "normal"
    status: str = "open"
    assigned_to: str | None = Field(default=None, max_length=200)
    estimated_value: Decimal | None = None
    next_action_at: datetime | None = None
    lost_reason: str | None = None
    notes: str | None = None

    @root_validator(skip_on_failure=True)
    def validate_enums(cls, values):
        checks = (
            ("pipeline", LEAD_PIPELINES),
            ("stage", LEAD_STAGES),
            ("priority", PRIORITIES),
            ("status", LEAD_STATUSES),
        )
        for field, allowed in checks:
            if values.get(field) not in allowed:
                raise ValueError(f"{field} must be one of {sorted(allowed)}")
        return values


class LeadUpdate(CoreModel):
    source: str | None = Field(default=None, max_length=100)
    pipeline: str | None = None
    stage: str | None = None
    priority: str | None = None
    status: str | None = None
    assigned_to: str | None = Field(default=None, max_length=200)
    estimated_value: Decimal | None = None
    next_action_at: datetime | None = None
    lost_reason: str | None = None
    notes: str | None = None
    closed_at: datetime | None = None

    @root_validator(skip_on_failure=True)
    def validate_enums(cls, values):
        checks = (
            ("pipeline", LEAD_PIPELINES),
            ("stage", LEAD_STAGES),
            ("priority", PRIORITIES),
            ("status", LEAD_STATUSES),
        )
        for field, allowed in checks:
            if values.get(field) is not None and values[field] not in allowed:
                raise ValueError(f"{field} must be one of {sorted(allowed)}")
        return values


class LeadStimaLinkCreate(CoreModel):
    relation_type: str = "related"

    @root_validator(skip_on_failure=True)
    def validate_relation(cls, values):
        if values.get("relation_type") not in LEAD_STIMA_RELATIONS:
            raise ValueError(f"relation_type must be one of {sorted(LEAD_STIMA_RELATIONS)}")
        return values


class ActivityCreate(CoreModel):
    contact_id: int | None = None
    lead_id: int | None = None
    stima_id: int | None = None
    activity_type: str
    direction: str | None = None
    channel: str | None = Field(default=None, max_length=50)
    subject: str | None = Field(default=None, max_length=200)
    description: str | None = None
    outcome: str | None = Field(default=None, max_length=100)
    occurred_at: datetime | None = None
    created_by: str | None = Field(default=None, max_length=200)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @root_validator(skip_on_failure=True)
    def validate_activity(cls, values):
        if not any(values.get(field) is not None for field in ("contact_id", "lead_id", "stima_id")):
            raise ValueError("at least one of contact_id, lead_id or stima_id is required")
        if values.get("activity_type") not in ACTIVITY_TYPES:
            raise ValueError(f"activity_type must be one of {sorted(ACTIVITY_TYPES)}")
        if values.get("direction") is not None and values["direction"] not in ACTIVITY_DIRECTIONS:
            raise ValueError(f"direction must be one of {sorted(ACTIVITY_DIRECTIONS)}")
        return values


class TaskCreate(CoreModel):
    contact_id: int | None = None
    lead_id: int | None = None
    stima_id: int | None = None
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    task_type: str | None = Field(default=None, max_length=50)
    priority: str = "normal"
    status: str = "open"
    due_at: datetime | None = None
    completed_at: datetime | None = None
    assigned_to: str | None = Field(default=None, max_length=200)
    created_by: str | None = Field(default=None, max_length=200)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @root_validator(skip_on_failure=True)
    def validate_task(cls, values):
        if not any(values.get(field) is not None for field in ("contact_id", "lead_id", "stima_id")):
            raise ValueError("at least one of contact_id, lead_id or stima_id is required")
        if values.get("priority") not in PRIORITIES:
            raise ValueError(f"priority must be one of {sorted(PRIORITIES)}")
        if values.get("status") not in TASK_STATUSES:
            raise ValueError(f"status must be one of {sorted(TASK_STATUSES)}")
        return values


class TaskUpdate(CoreModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    task_type: str | None = Field(default=None, max_length=50)
    priority: str | None = None
    status: str | None = None
    due_at: datetime | None = None
    completed_at: datetime | None = None
    assigned_to: str | None = Field(default=None, max_length=200)
    created_by: str | None = Field(default=None, max_length=200)
    metadata: dict[str, Any] | None = None

    @root_validator(skip_on_failure=True)
    def validate_task(cls, values):
        if values.get("priority") is not None and values["priority"] not in PRIORITIES:
            raise ValueError(f"priority must be one of {sorted(PRIORITIES)}")
        if values.get("status") is not None and values["status"] not in TASK_STATUSES:
            raise ValueError(f"status must be one of {sorted(TASK_STATUSES)}")
        return values
