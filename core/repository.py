"""Raw SQL repository for the STIMA360 CORE module."""

from __future__ import annotations

from typing import Any

from psycopg2 import errors
from psycopg2.extras import Json

from .database import core_cursor
from .exceptions import ConflictError, NotFoundError


def _row(row):
    return dict(row) if row else None


def _ensure_exists(cur, table: str, entity_id: int, label: str) -> None:
    # table is selected only from internal constants, never user input.
    cur.execute(f"SELECT 1 FROM {table} WHERE id = %s", (entity_id,))
    if cur.fetchone() is None:
        raise NotFoundError(f"{label} {entity_id} not found")


def create_contact(data: dict[str, Any]) -> dict[str, Any]:
    with core_cursor(commit=True) as (_, cur):
        cur.execute(
            """
            INSERT INTO contacts (
                contact_type, first_name, last_name, company_name, display_name,
                email, email_normalized, phone, phone_normalized, secondary_phone,
                source, status, marketing_consent, marketing_consent_at, notes
            ) VALUES (
                %(contact_type)s, %(first_name)s, %(last_name)s, %(company_name)s, %(display_name)s,
                %(email)s, %(email_normalized)s, %(phone)s, %(phone_normalized)s, %(secondary_phone)s,
                %(source)s, %(status)s, %(marketing_consent)s, %(marketing_consent_at)s, %(notes)s
            )
            RETURNING *
            """,
            data,
        )
        return _row(cur.fetchone())


def list_contacts(limit: int, offset: int, search: str | None, status: str | None) -> list[dict[str, Any]]:
    where = []
    params: list[Any] = []
    if status:
        where.append("status = %s")
        params.append(status)
    if search:
        where.append(
            """(
                display_name ILIKE %s OR first_name ILIKE %s OR last_name ILIKE %s OR
                company_name ILIKE %s OR email_normalized ILIKE %s OR phone_normalized ILIKE %s
            )"""
        )
        term = f"%{search.strip()}%"
        params.extend([term] * 6)
    clause = " WHERE " + " AND ".join(where) if where else ""
    params.extend([limit, offset])
    with core_cursor() as (_, cur):
        cur.execute(f"SELECT * FROM contacts{clause} ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s", params)
        return [dict(row) for row in cur.fetchall()]


def get_contact(contact_id: int) -> dict[str, Any]:
    with core_cursor() as (_, cur):
        cur.execute("SELECT * FROM contacts WHERE id = %s", (contact_id,))
        contact = _row(cur.fetchone())
        if not contact:
            raise NotFoundError(f"contact {contact_id} not found")
        cur.execute("SELECT * FROM contact_roles WHERE contact_id = %s ORDER BY created_at, id", (contact_id,))
        contact["roles"] = [dict(row) for row in cur.fetchall()]
        return contact


def update_contact(contact_id: int, data: dict[str, Any]) -> dict[str, Any]:
    if not data:
        return get_contact(contact_id)
    assignments = [f"{key} = %s" for key in data]
    params = list(data.values()) + [contact_id]
    with core_cursor(commit=True) as (_, cur):
        cur.execute(
            f"UPDATE contacts SET {', '.join(assignments)}, updated_at = NOW() WHERE id = %s RETURNING *",
            params,
        )
        row = cur.fetchone()
        if not row:
            raise NotFoundError(f"contact {contact_id} not found")
        return _row(row)


def add_contact_role(contact_id: int, data: dict[str, Any]) -> dict[str, Any]:
    with core_cursor(commit=True) as (_, cur):
        _ensure_exists(cur, "contacts", contact_id, "contact")
        try:
            role_data = {**data, "contact_id": contact_id, "metadata": Json(data.get("metadata") or {})}
            cur.execute(
                """
                INSERT INTO contact_roles (contact_id, role, is_primary, valid_from, valid_to, metadata)
                VALUES (%(contact_id)s, %(role)s, %(is_primary)s, %(valid_from)s, %(valid_to)s, %(metadata)s)
                RETURNING *
                """,
                role_data,
            )
        except errors.UniqueViolation as exc:
            raise ConflictError(f"role {data['role']} already assigned to contact {contact_id}") from exc
        return _row(cur.fetchone())


def delete_contact_role(contact_id: int, role: str) -> None:
    with core_cursor(commit=True) as (_, cur):
        cur.execute("DELETE FROM contact_roles WHERE contact_id = %s AND role = %s", (contact_id, role))
        if cur.rowcount == 0:
            raise NotFoundError(f"role {role} not found for contact {contact_id}")


def create_lead(data: dict[str, Any]) -> dict[str, Any]:
    with core_cursor(commit=True) as (_, cur):
        _ensure_exists(cur, "contacts", data["contact_id"], "contact")
        cur.execute(
            """
            INSERT INTO leads (
                contact_id, source, pipeline, stage, priority, status, assigned_to,
                estimated_value, next_action_at, lost_reason, notes
            ) VALUES (
                %(contact_id)s, %(source)s, %(pipeline)s, %(stage)s, %(priority)s, %(status)s,
                %(assigned_to)s, %(estimated_value)s, %(next_action_at)s, %(lost_reason)s, %(notes)s
            ) RETURNING *
            """,
            data,
        )
        return _row(cur.fetchone())


def list_leads(limit: int, offset: int, contact_id: int | None, pipeline: str | None, stage: str | None, status: str | None):
    filters = []
    params: list[Any] = []
    for column, value in (("contact_id", contact_id), ("pipeline", pipeline), ("stage", stage), ("status", status)):
        if value is not None:
            filters.append(f"{column} = %s")
            params.append(value)
    clause = " WHERE " + " AND ".join(filters) if filters else ""
    params.extend([limit, offset])
    with core_cursor() as (_, cur):
        cur.execute(f"SELECT * FROM leads{clause} ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s", params)
        return [dict(row) for row in cur.fetchall()]


def get_lead(lead_id: int) -> dict[str, Any]:
    with core_cursor() as (_, cur):
        cur.execute("SELECT * FROM leads WHERE id = %s", (lead_id,))
        lead = _row(cur.fetchone())
        if not lead:
            raise NotFoundError(f"lead {lead_id} not found")
        cur.execute("SELECT * FROM lead_stime WHERE lead_id = %s ORDER BY created_at, id", (lead_id,))
        lead["estimations"] = [dict(row) for row in cur.fetchall()]
        return lead


def update_lead(lead_id: int, data: dict[str, Any]) -> dict[str, Any]:
    if not data:
        return get_lead(lead_id)
    assignments = [f"{key} = %s" for key in data]
    params = list(data.values()) + [lead_id]
    with core_cursor(commit=True) as (_, cur):
        cur.execute(f"UPDATE leads SET {', '.join(assignments)}, updated_at = NOW() WHERE id = %s RETURNING *", params)
        row = cur.fetchone()
        if not row:
            raise NotFoundError(f"lead {lead_id} not found")
        return _row(row)


def link_stima(lead_id: int, stima_id: int, relation_type: str) -> dict[str, Any]:
    with core_cursor(commit=True) as (_, cur):
        _ensure_exists(cur, "leads", lead_id, "lead")
        _ensure_exists(cur, "stime", stima_id, "stima")
        try:
            cur.execute(
                """
                INSERT INTO lead_stime (lead_id, stima_id, relation_type)
                VALUES (%s, %s, %s) RETURNING *
                """,
                (lead_id, stima_id, relation_type),
            )
        except errors.UniqueViolation as exc:
            raise ConflictError(f"stima {stima_id} is already linked to lead {lead_id}") from exc
        return _row(cur.fetchone())


def unlink_stima(lead_id: int, stima_id: int) -> None:
    with core_cursor(commit=True) as (_, cur):
        cur.execute("DELETE FROM lead_stime WHERE lead_id = %s AND stima_id = %s", (lead_id, stima_id))
        if cur.rowcount == 0:
            raise NotFoundError(f"link between lead {lead_id} and stima {stima_id} not found")


def _validate_references(cur, data: dict[str, Any]) -> None:
    mapping = (("contact_id", "contacts", "contact"), ("lead_id", "leads", "lead"), ("stima_id", "stime", "stima"))
    for field, table, label in mapping:
        if data.get(field) is not None:
            _ensure_exists(cur, table, data[field], label)


def create_activity(data: dict[str, Any]) -> dict[str, Any]:
    with core_cursor(commit=True) as (_, cur):
        _validate_references(cur, data)
        data = {**data, "metadata": Json(data.get("metadata") or {})}
        cur.execute(
            """
            INSERT INTO activities (
                contact_id, lead_id, stima_id, activity_type, direction, channel,
                subject, description, outcome, occurred_at, created_by, metadata
            ) VALUES (
                %(contact_id)s, %(lead_id)s, %(stima_id)s, %(activity_type)s, %(direction)s,
                %(channel)s, %(subject)s, %(description)s, %(outcome)s,
                COALESCE(%(occurred_at)s, NOW()), %(created_by)s, %(metadata)s
            ) RETURNING *
            """,
            data,
        )
        return _row(cur.fetchone())


def list_activities(limit: int, offset: int, contact_id: int | None, lead_id: int | None, stima_id: int | None):
    filters = []
    params: list[Any] = []
    for column, value in (("contact_id", contact_id), ("lead_id", lead_id), ("stima_id", stima_id)):
        if value is not None:
            filters.append(f"{column} = %s")
            params.append(value)
    clause = " WHERE " + " AND ".join(filters) if filters else ""
    params.extend([limit, offset])
    with core_cursor() as (_, cur):
        cur.execute(f"SELECT * FROM activities{clause} ORDER BY occurred_at DESC, id DESC LIMIT %s OFFSET %s", params)
        return [dict(row) for row in cur.fetchall()]


def create_task(data: dict[str, Any]) -> dict[str, Any]:
    with core_cursor(commit=True) as (_, cur):
        _validate_references(cur, data)
        data = {**data, "metadata": Json(data.get("metadata") or {})}
        cur.execute(
            """
            INSERT INTO tasks (
                contact_id, lead_id, stima_id, title, description, task_type,
                priority, status, due_at, completed_at, assigned_to, created_by, metadata
            ) VALUES (
                %(contact_id)s, %(lead_id)s, %(stima_id)s, %(title)s, %(description)s,
                %(task_type)s, %(priority)s, %(status)s, %(due_at)s, %(completed_at)s,
                %(assigned_to)s, %(created_by)s, %(metadata)s
            ) RETURNING *
            """,
            data,
        )
        return _row(cur.fetchone())


def list_tasks(limit: int, offset: int, contact_id: int | None, lead_id: int | None, stima_id: int | None, status: str | None):
    filters = []
    params: list[Any] = []
    for column, value in (("contact_id", contact_id), ("lead_id", lead_id), ("stima_id", stima_id), ("status", status)):
        if value is not None:
            filters.append(f"{column} = %s")
            params.append(value)
    clause = " WHERE " + " AND ".join(filters) if filters else ""
    params.extend([limit, offset])
    with core_cursor() as (_, cur):
        cur.execute(f"SELECT * FROM tasks{clause} ORDER BY due_at NULLS LAST, created_at DESC, id DESC LIMIT %s OFFSET %s", params)
        return [dict(row) for row in cur.fetchall()]


def update_task(task_id: int, data: dict[str, Any]) -> dict[str, Any]:
    if not data:
        with core_cursor() as (_, cur):
            cur.execute("SELECT * FROM tasks WHERE id = %s", (task_id,))
            row = cur.fetchone()
            if not row:
                raise NotFoundError(f"task {task_id} not found")
            return _row(row)
    if "metadata" in data:
        data["metadata"] = Json(data.get("metadata") or {})
    assignments = [f"{key} = %s" for key in data]
    params = list(data.values()) + [task_id]
    with core_cursor(commit=True) as (_, cur):
        cur.execute(f"UPDATE tasks SET {', '.join(assignments)}, updated_at = NOW() WHERE id = %s RETURNING *", params)
        row = cur.fetchone()
        if not row:
            raise NotFoundError(f"task {task_id} not found")
        return _row(row)
