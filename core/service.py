"""Application services for CORE CRM."""

from __future__ import annotations

from datetime import datetime, timezone

from operator_auth import permissions

from . import repository
from .exceptions import PermissionDenied
from .normalization import normalize_email, normalize_phone

ASSIGNMENT_DENIED_MESSAGE = (
    "Questa operazione richiede un ruolo di amministrazione dell'agenzia."
)


def _require_assignment_permission(ctx) -> None:
    """The single role gate for assignment, checked before any lookup.

    Uses the shared predicate rather than restating the role list, so the
    router, the service and the repository cannot drift apart. Checked first so
    that an agent's refusal is identical whether or not the record exists, and
    whichever agency it belongs to.
    """
    if not permissions.may_assign_records(ctx.role, ctx.is_platform_admin):
        raise PermissionDenied(ASSIGNMENT_DENIED_MESSAGE)


def _dump(model, *, exclude_unset: bool = False) -> dict:
    # Compatible with both Pydantic v1 and v2.
    if hasattr(model, "model_dump"):
        return model.model_dump(exclude_unset=exclude_unset)
    return model.dict(exclude_unset=exclude_unset)


def create_contact(ctx, payload):
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
    return repository.create_contact(ctx, data)


def list_contacts(ctx, limit, offset, search, status):
    return repository.list_contacts(ctx, limit, offset, search, status)


def get_contact(ctx, contact_id):
    return repository.get_contact(ctx, contact_id)


def update_contact(ctx, contact_id, payload):
    data = _dump(payload, exclude_unset=True)
    if "email" in data:
        data["email_normalized"] = normalize_email(data.get("email"))
    if "phone" in data:
        data["phone_normalized"] = normalize_phone(data.get("phone"))

    # Keep display_name coherent when person/company identity fields change.
    identity_fields = {"contact_type", "first_name", "last_name", "company_name"}
    if identity_fields.intersection(data) and "display_name" not in data:
        current = repository.get_contact(ctx, contact_id)
        merged = {**current, **data}
        if merged.get("contact_type") == "company":
            data["display_name"] = merged.get("company_name")
        else:
            data["display_name"] = " ".join(
                part for part in (merged.get("first_name"), merged.get("last_name")) if part
            ) or merged.get("display_name")
    return repository.update_contact(ctx, contact_id, data)


def add_contact_role(ctx, contact_id, payload):
    return repository.add_contact_role(ctx, contact_id, _dump(payload))


def delete_contact_role(ctx, contact_id, role):
    repository.delete_contact_role(ctx, contact_id, role)


def set_contact_assignment(ctx, contact_id, payload):
    """Assign or clear a contact's agent. Permission first, then the record."""
    _require_assignment_permission(ctx)
    return repository.set_contact_assignment(ctx, contact_id, payload.assigned_agent_id)


def set_lead_assignment(ctx, lead_id, payload):
    """Assign or clear a lead's agent. Permission first, then the record."""
    _require_assignment_permission(ctx)
    return repository.set_lead_assignment(ctx, lead_id, payload.assigned_agent_id)


def create_lead(ctx, payload):
    return repository.create_lead(ctx, _dump(payload))


def list_leads(ctx, limit, offset, contact_id, pipeline, stage, status):
    return repository.list_leads(ctx, limit, offset, contact_id, pipeline, stage, status)


def get_lead(ctx, lead_id):
    return repository.get_lead(ctx, lead_id)


def update_lead(ctx, lead_id, payload):
    data = _dump(payload, exclude_unset=True)
    if data.get("status") == "closed" and "closed_at" not in data:
        data["closed_at"] = datetime.now(timezone.utc)
    elif data.get("status") in {"open", "paused"} and "closed_at" not in data:
        data["closed_at"] = None
    return repository.update_lead(ctx, lead_id, data)


def link_stima(ctx, lead_id, stima_id, payload):
    return repository.link_stima(ctx, lead_id, stima_id, payload.relation_type)


def bridge_public_stima(
    stima_id,
    *,
    first_name,
    last_name,
    email,
    phone,
    marketing_consent,
    marketing_consent_at,
    system_ctx,
):
    """Shape one public estimation into CORE contact and lead data.

    `system_ctx` is the `SystemAgencyContext` the public writer already
    resolved and stamped on the stima. It is threaded straight through to the
    repository, unread and unmodified: this layer decides what the records
    look like, never which agency they belong to (P26-2B2B-R1).
    """

    def clean(value):
        if value is None:
            return None
        value = str(value).strip()
        return value or None

    first_name = clean(first_name)
    last_name = clean(last_name)
    email = clean(email)
    phone = clean(phone)
    display_name = " ".join(part for part in (first_name, last_name) if part) or None
    contact_data = {
        "contact_type": "person",
        "first_name": first_name,
        "last_name": last_name,
        "company_name": None,
        "display_name": display_name,
        "email": email,
        "email_normalized": normalize_email(email),
        "phone": phone,
        "phone_normalized": normalize_phone(phone),
        "secondary_phone": None,
        "source": "public_stima",
        "status": "active",
        "marketing_consent": bool(marketing_consent),
        "marketing_consent_at": marketing_consent_at,
        "notes": None,
    }
    lead_data = {
        "source": "public_stima",
        "pipeline": "sell",
        "stage": "new",
        "priority": "normal",
        "status": "open",
        "assigned_to": None,
        "estimated_value": None,
        "next_action_at": None,
        "lost_reason": None,
        "notes": None,
    }
    return repository.bridge_public_stima(
        int(stima_id),
        contact_data,
        lead_data,
        "related",
        system_ctx=system_ctx,
    )


def unlink_stima(ctx, lead_id, stima_id):
    repository.unlink_stima(ctx, lead_id, stima_id)


def create_activity(ctx, payload):
    return repository.create_activity(ctx, _dump(payload))


def list_activities(ctx, limit, offset, contact_id, lead_id, stima_id):
    return repository.list_activities(ctx, limit, offset, contact_id, lead_id, stima_id)


def create_task(ctx, payload):
    data = _dump(payload)
    if data.get("status") == "completed" and data.get("completed_at") is None:
        data["completed_at"] = datetime.now(timezone.utc)
    return repository.create_task(ctx, data)


def list_tasks(ctx, limit, offset, contact_id, lead_id, stima_id, status):
    return repository.list_tasks(ctx, limit, offset, contact_id, lead_id, stima_id, status)


def update_task(ctx, task_id, payload):
    data = _dump(payload, exclude_unset=True)
    if data.get("status") == "completed" and "completed_at" not in data:
        data["completed_at"] = datetime.now(timezone.utc)
    elif data.get("status") in {"open", "in_progress", "cancelled"} and "completed_at" not in data:
        data["completed_at"] = None
    return repository.update_task(ctx, task_id, data)


def delete_activity(ctx, activity_id):
    repository.delete_activity(ctx, activity_id)


def delete_task(ctx, task_id):
    repository.delete_task(ctx, task_id)
