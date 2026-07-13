"""Application services for CORE CRM."""

from __future__ import annotations

from datetime import datetime, timezone

from . import repository
from .normalization import normalize_email, normalize_phone


def _dump(model, *, exclude_unset: bool = False) -> dict:
    # Compatible with both Pydantic v1 and v2.
    if hasattr(model, "model_dump"):
        return model.model_dump(exclude_unset=exclude_unset)
    return model.dict(exclude_unset=exclude_unset)


def create_contact(payload):
    data = _dump(payload)
    data["email_normalized"] = normalize_email(data.get("email"))
    data["phone_normalized"] = normalize_phone(data.get("phone"))
    if not data.get("display_name"):
        if data.get("contact_type") == "company":
            data["display_name"] = data.get("company_name")
        else:
            data["display_name"] = " ".join(
                part for part in (data.get("first_name"), data.get("last_name")) if part
            ) or None
    return repository.create_contact(data)


def list_contacts(limit, offset, search, status):
    return repository.list_contacts(limit, offset, search, status)


def get_contact(contact_id):
    return repository.get_contact(contact_id)


def update_contact(contact_id, payload):
    data = _dump(payload, exclude_unset=True)
    if "email" in data:
        data["email_normalized"] = normalize_email(data.get("email"))
    if "phone" in data:
        data["phone_normalized"] = normalize_phone(data.get("phone"))
    return repository.update_contact(contact_id, data)


def add_contact_role(contact_id, payload):
    return repository.add_contact_role(contact_id, _dump(payload))


def delete_contact_role(contact_id, role):
    repository.delete_contact_role(contact_id, role)


def create_lead(payload):
    return repository.create_lead(_dump(payload))


def list_leads(limit, offset, contact_id, pipeline, stage, status):
    return repository.list_leads(limit, offset, contact_id, pipeline, stage, status)


def get_lead(lead_id):
    return repository.get_lead(lead_id)


def update_lead(lead_id, payload):
    data = _dump(payload, exclude_unset=True)
    if data.get("status") == "closed" and "closed_at" not in data:
        data["closed_at"] = datetime.now(timezone.utc)
    return repository.update_lead(lead_id, data)


def link_stima(lead_id, stima_id, payload):
    return repository.link_stima(lead_id, stima_id, payload.relation_type)


def unlink_stima(lead_id, stima_id):
    repository.unlink_stima(lead_id, stima_id)


def create_activity(payload):
    return repository.create_activity(_dump(payload))


def list_activities(limit, offset, contact_id, lead_id, stima_id):
    return repository.list_activities(limit, offset, contact_id, lead_id, stima_id)


def create_task(payload):
    data = _dump(payload)
    if data.get("status") == "completed" and data.get("completed_at") is None:
        data["completed_at"] = datetime.now(timezone.utc)
    return repository.create_task(data)


def list_tasks(limit, offset, contact_id, lead_id, stima_id, status):
    return repository.list_tasks(limit, offset, contact_id, lead_id, stima_id, status)


def update_task(task_id, payload):
    data = _dump(payload, exclude_unset=True)
    if data.get("status") == "completed" and "completed_at" not in data:
        data["completed_at"] = datetime.now(timezone.utc)
    return repository.update_task(task_id, data)
