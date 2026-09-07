"""Raw SQL repository for the STIMA360 CORE module.

Every function that touches `contacts`, `leads`, `activities` or `tasks` takes
an `AgencyScope` as its first positional parameter and builds its statement
through `core/scope.py`. Omitting it is a `TypeError` at call time, so the
mistake fails closed and loudly rather than silently returning another
agency's rows.

The table name and its predicate are emitted by the same expression - see
`scoped_source` and `scoped_predicate` - so a statement cannot name a scoped
table without carrying its scope. The SQL below stays plain and greppable:
`INSERT INTO contacts` really is written as `INSERT INTO contacts`, because the
guard is the security property proved in tests/test_p26_1_core_isolation.py,
not a ban on table literals.

Three public functions deliberately do not take an *operator* scope:

* `bridge_public_stima` takes a `SystemAgencyContext` instead - it serves an
  unauthenticated caller, so there is no operator scope to receive. The public
  writer resolves that context once and passes it here, so the estimation and
  the CORE records it produces share a single ownership decision (P26-2B2B-R1).
  It is the single member of `SYSTEM_CONTEXT_FUNCTIONS`.
* `create_activity_with_cursor` and `create_task_with_cursor` accept an
  *optional* `ctx`. Three P17-P25-certified modules call them with
  `(cur, data)` inside their own transaction and are explicitly unmodified in
  P26-1 (design spec R-4); migration 030's `core_agency_integrity()` trigger
  derives and validates their `agency_id` from the row's own references.
  Forcing a mandatory `ctx` here would break them for no security gain. The
  three are named in tests/test_p26_1_core_isolation.py, which is where the
  exception is pinned - naming them here would couple this module to them.
"""

from __future__ import annotations

from typing import Any

from psycopg2 import errors
from psycopg2.extras import Json

from operator_auth.repository import membership_exists

from .database import core_cursor
from .exceptions import ConflictError, NotFoundError, ValidationError
from operator_auth.context import SystemAgencyContext

from .scope import (
    ProgrammingError,
    scoped_predicate,
    scoped_source,
)

# Columns that record where a row came from. They are written from the scope,
# never from a payload, so a caller cannot place a record in another agency or
# attribute it to another operator.
SERVER_OWNED_COLUMNS = ("agency_id", "created_by_user_id")

# The only origin `bridge_public_stima` will act under. Declared here rather
# than imported so core/scope.py keeps the origin literal it was certified with;
# a test pins this to what the factory actually produces, so the two cannot
# drift apart.
PUBLIC_STIMA_ORIGIN = "public_stima"


def _row(row):
    return dict(row) if row else None


def _reject_server_owned(data: dict[str, Any]) -> None:
    """Refuse a payload that tries to set its own provenance.

    The CORE request schemas do not declare these fields, so this cannot be
    reached over HTTP. It is the repository-level restatement of the same rule:
    agency authority comes from the scope, and from nowhere else.
    """
    for column in SERVER_OWNED_COLUMNS:
        if column in data:
            raise ProgrammingError(
                f"{column!r} is derived from the agency scope and must not be supplied"
            )


def _stamp(ctx, data: dict[str, Any]) -> dict[str, Any]:
    """Return `data` with the provenance columns taken from the scope."""
    _reject_server_owned(data)
    return {
        **data,
        "agency_id": ctx.require_agency(),
        "created_by_user_id": ctx.user_id,
    }


def _ensure_exists(cur, table: str, entity_id: int, label: str) -> None:
    """Existence check for a table that carries no agency.

    Used only for `stime`, which belongs to the public estimation domain and
    has no agency column. Scoped tables go through `_ensure_exists_scoped`.
    """
    # table is selected only from internal constants, never user input.
    cur.execute(f"SELECT 1 FROM {table} WHERE id = %s", (entity_id,))
    if cur.fetchone() is None:
        raise NotFoundError(f"{label} {entity_id} not found")


def _ensure_exists_scoped(ctx, cur, table: str, entity_id: int, label: str) -> None:
    """Existence check confined to the caller's agency.

    A record in another agency is reported exactly as a record that does not
    exist (design spec D-6), so this cannot be used to probe another agency's
    primary keys.
    """
    source, params = scoped_source(ctx, table, "s")
    cur.execute(f"SELECT 1 FROM {source} AND s.id = %s", params + [entity_id])
    if cur.fetchone() is None:
        raise NotFoundError(f"{label} {entity_id} not found")


def create_contact(ctx, data: dict[str, Any]) -> dict[str, Any]:
    # Resolved before the cursor opens: an unbound platform admin is refused
    # without a statement ever reaching the database.
    prepared = _stamp(ctx, data)
    with core_cursor(commit=True) as (_, cur):
        cur.execute(
            """
            INSERT INTO contacts (
                contact_type, first_name, last_name, company_name, display_name,
                email, email_normalized, phone, phone_normalized, secondary_phone,
                source, status, marketing_consent, marketing_consent_at, notes,
                agency_id, created_by_user_id
            ) VALUES (
                %(contact_type)s, %(first_name)s, %(last_name)s, %(company_name)s, %(display_name)s,
                %(email)s, %(email_normalized)s, %(phone)s, %(phone_normalized)s, %(secondary_phone)s,
                %(source)s, %(status)s, %(marketing_consent)s, %(marketing_consent_at)s, %(notes)s,
                %(agency_id)s, %(created_by_user_id)s
            )
            RETURNING *
            """,
            prepared,
        )
        return _row(cur.fetchone())


def list_contacts(ctx, limit: int, offset: int, search: str | None, status: str | None) -> list[dict[str, Any]]:
    source, params = scoped_source(ctx, "contacts", "c")
    where = []
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
    clause = " AND " + " AND ".join(where) if where else ""
    params.extend([limit, offset])
    with core_cursor() as (_, cur):
        cur.execute(f"SELECT * FROM {source}{clause} ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s", params)
        return [dict(row) for row in cur.fetchall()]


def get_contact(ctx, contact_id: int) -> dict[str, Any]:
    source, params = scoped_source(ctx, "contacts", "c")
    with core_cursor() as (_, cur):
        cur.execute(f"SELECT * FROM {source} AND c.id = %s", params + [contact_id])
        contact = _row(cur.fetchone())
        if not contact:
            raise NotFoundError(f"contact {contact_id} not found")
        # Reached only once the parent has resolved inside the scope, so the
        # child table needs no predicate of its own.
        cur.execute("SELECT * FROM contact_roles WHERE contact_id = %s ORDER BY created_at, id", (contact_id,))
        contact["roles"] = [dict(row) for row in cur.fetchall()]
        return contact


def update_contact(ctx, contact_id: int, data: dict[str, Any]) -> dict[str, Any]:
    _reject_server_owned(data)
    if not data:
        return get_contact(ctx, contact_id)
    predicate, scope_params = scoped_predicate(ctx, "contacts", "c")
    assignments = [f"{key} = %s" for key in data]
    params = list(data.values()) + [contact_id] + scope_params
    with core_cursor(commit=True) as (_, cur):
        cur.execute(
            f"UPDATE contacts c SET {', '.join(assignments)}, updated_at = NOW() "
            f"WHERE c.id = %s AND {predicate} RETURNING *",
            params,
        )
        row = cur.fetchone()
        if not row:
            raise NotFoundError(f"contact {contact_id} not found")
        return _row(row)


def add_contact_role(ctx, contact_id: int, data: dict[str, Any]) -> dict[str, Any]:
    with core_cursor(commit=True) as (_, cur):
        _ensure_exists_scoped(ctx, cur, "contacts", contact_id, "contact")
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


def delete_contact_role(ctx, contact_id: int, role: str) -> None:
    # One statement, joined to the scoped parent: a role cannot be removed by
    # guessing the id of a contact in another agency, and a foreign contact
    # produces the same "not found" as an absent one.
    predicate, scope_params = scoped_predicate(ctx, "contacts", "c")
    with core_cursor(commit=True) as (_, cur):
        cur.execute(
            "DELETE FROM contact_roles cr USING contacts c "
            f"WHERE cr.contact_id = c.id AND cr.contact_id = %s AND cr.role = %s AND {predicate}",
            [contact_id, role] + scope_params,
        )
        if cur.rowcount == 0:
            raise NotFoundError(f"role {role} not found for contact {contact_id}")


def _set_assignment(
    ctx, table: str, alias: str, entity_id: int, label: str, assigned_agent_id: int | None
) -> dict[str, Any]:
    """Set or clear a record's assigned agent. The shared half of Task 12.

    The governing agency is derived from the *record*, never from `ctx` and
    never from the target operator. That single rule serves every role: for an
    agency owner or admin the record was fetched under their own scope, so the
    record's agency is necessarily theirs; for a platform admin holding no
    membership it is the record's agency that governs, which is the only
    coherent answer. `ctx.require_agency()` is deliberately not called on this
    path, so an unbound platform admin is never forced to infer a default.

    Order: resolve the record in scope (404), validate the target against the
    record's agency (400), then update by id *and* scope predicate. The final
    UPDATE repeats the predicate rather than trusting the earlier SELECT, so
    the write cannot outlive the read that authorised it.

    Migration 030's composite foreign key is the last structural gate. It is
    not the user-facing one: this returns a controlled ValidationError rather
    than letting an integrity error surface as a 500.
    """
    source, read_params = scoped_source(ctx, table, alias)
    predicate, scope_params = scoped_predicate(ctx, table, alias)

    with core_cursor(commit=True) as (_, cur):
        cur.execute(
            f"SELECT {alias}.id, {alias}.agency_id FROM {source} AND {alias}.id = %s",
            read_params + [entity_id],
        )
        record = _row(cur.fetchone())
        if record is None:
            raise NotFoundError(f"{label} {entity_id} not found")

        record_agency_id = record["agency_id"]

        if assigned_agent_id is not None and not membership_exists(
            cur, record_agency_id, assigned_agent_id
        ):
            # Names neither the operator nor the agency: a rejected assignment
            # must not become a way to probe who belongs where.
            raise ValidationError(
                "assigned_agent_id is not an active member of this record's agency"
            )

        cur.execute(
            f"UPDATE {table} {alias} SET assigned_agent_id = %s, updated_at = NOW() "
            f"WHERE {alias}.id = %s AND {predicate} RETURNING *",
            [assigned_agent_id, entity_id] + scope_params,
        )
        updated = cur.fetchone()
        if not updated:
            raise NotFoundError(f"{label} {entity_id} not found")
        return _row(updated)


def set_contact_assignment(ctx, contact_id: int, assigned_agent_id: int | None) -> dict[str, Any]:
    return _set_assignment(ctx, "contacts", "c", contact_id, "contact", assigned_agent_id)


def set_lead_assignment(ctx, lead_id: int, assigned_agent_id: int | None) -> dict[str, Any]:
    return _set_assignment(ctx, "leads", "l", lead_id, "lead", assigned_agent_id)


def create_lead(ctx, data: dict[str, Any]) -> dict[str, Any]:
    prepared = _stamp(ctx, data)
    with core_cursor(commit=True) as (_, cur):
        _ensure_exists_scoped(ctx, cur, "contacts", data["contact_id"], "contact")
        cur.execute(
            """
            INSERT INTO leads (
                contact_id, source, pipeline, stage, priority, status, assigned_to,
                estimated_value, next_action_at, lost_reason, notes,
                agency_id, created_by_user_id
            ) VALUES (
                %(contact_id)s, %(source)s, %(pipeline)s, %(stage)s, %(priority)s, %(status)s,
                %(assigned_to)s, %(estimated_value)s, %(next_action_at)s, %(lost_reason)s, %(notes)s,
                %(agency_id)s, %(created_by_user_id)s
            ) RETURNING *
            """,
            prepared,
        )
        return _row(cur.fetchone())


def list_leads(ctx, limit: int, offset: int, contact_id: int | None, pipeline: str | None, stage: str | None, status: str | None):
    source, params = scoped_source(ctx, "leads", "l")
    filters = []
    for column, value in (("contact_id", contact_id), ("pipeline", pipeline), ("stage", stage), ("status", status)):
        if value is not None:
            filters.append(f"{column} = %s")
            params.append(value)
    clause = " AND " + " AND ".join(filters) if filters else ""
    params.extend([limit, offset])
    with core_cursor() as (_, cur):
        cur.execute(f"SELECT * FROM {source}{clause} ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s", params)
        return [dict(row) for row in cur.fetchall()]


def get_lead(ctx, lead_id: int) -> dict[str, Any]:
    source, params = scoped_source(ctx, "leads", "l")
    with core_cursor() as (_, cur):
        cur.execute(f"SELECT * FROM {source} AND l.id = %s", params + [lead_id])
        lead = _row(cur.fetchone())
        if not lead:
            raise NotFoundError(f"lead {lead_id} not found")
        cur.execute("SELECT * FROM lead_stime WHERE lead_id = %s ORDER BY created_at, id", (lead_id,))
        lead["estimations"] = [dict(row) for row in cur.fetchall()]
        return lead


def update_lead(ctx, lead_id: int, data: dict[str, Any]) -> dict[str, Any]:
    _reject_server_owned(data)
    if not data:
        return get_lead(ctx, lead_id)
    predicate, scope_params = scoped_predicate(ctx, "leads", "l")
    assignments = [f"{key} = %s" for key in data]
    params = list(data.values()) + [lead_id] + scope_params
    with core_cursor(commit=True) as (_, cur):
        cur.execute(
            f"UPDATE leads l SET {', '.join(assignments)}, updated_at = NOW() "
            f"WHERE l.id = %s AND {predicate} RETURNING *",
            params,
        )
        row = cur.fetchone()
        if not row:
            raise NotFoundError(f"lead {lead_id} not found")
        return _row(row)


def link_stima(ctx, lead_id: int, stima_id: int, relation_type: str) -> dict[str, Any]:
    with core_cursor(commit=True) as (_, cur):
        _ensure_exists_scoped(ctx, cur, "leads", lead_id, "lead")
        # stime carries no agency: it is the public estimation, reached here
        # only after the lead has resolved inside the caller's scope.
        _ensure_exists(cur, "stime", stima_id, "stima")

        cur.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0)) AS locked",
            (f"core:public_stima:{stima_id}",),
        )

        cur.execute(
            """
            SELECT lead_id
            FROM lead_stime
            WHERE stima_id = %s
            ORDER BY id
            LIMIT 1
            FOR UPDATE
            """,
            (stima_id,),
        )
        existing_link = _row(cur.fetchone())

        if existing_link:
            if existing_link["lead_id"] == lead_id:
                raise ConflictError(
                    f"stima {stima_id} is already linked to lead {lead_id}"
                )

            raise ConflictError(
                f"stima {stima_id} is already linked to lead {existing_link['lead_id']}"
            )

        try:
            cur.execute(
                """
                INSERT INTO lead_stime (lead_id, stima_id, relation_type)
                VALUES (%s, %s, %s) RETURNING *
                """,
                (lead_id, stima_id, relation_type),
            )
        except errors.UniqueViolation as exc:
            raise ConflictError(
                f"stima {stima_id} is already linked to lead {lead_id}"
            ) from exc

        return _row(cur.fetchone())


def bridge_public_stima(
    stima_id: int,
    contact_data: dict[str, Any],
    lead_data: dict[str, Any],
    relation_type: str,
    *,
    system_ctx: SystemAgencyContext,
) -> dict[str, Any]:
    """Atomically reconcile one public stima with its dedicated CORE lead.

    The single member of `SYSTEM_CONTEXT_FUNCTIONS`: it runs under a
    `SystemAgencyContext` rather than an operator's scope, because its caller is
    an unauthenticated public estimation.

    P26-2B2B-R1: that context is *received*, not built here. The writer resolves
    the Default Agency once, on its own connection, and stamps `stime.agency_id`
    with it; the same object then travels here so the contact and the lead land
    in the agency the estimation already belongs to. Resolving a second time
    would be a second, independent decision - two lookups in two transactions,
    which can disagree if the Default Agency changes between them, and nothing
    in the schema would catch a stima whose lead lives elsewhere.

    Receiving a scope is not the same as trusting one. `system_ctx` is admitted
    only if it is a `SystemAgencyContext` carrying this flow's origin, so no
    caller can substitute an operator's scope, a look-alike object, or a
    context built for some other system flow. The guard runs before the cursor
    opens: nothing unscoped executes, and nothing executes at all under a scope
    this function did not accept.
    """
    if type(system_ctx) is not SystemAgencyContext:
        raise ProgrammingError(
            "bridge_public_stima requires a SystemAgencyContext, "
            f"received {type(system_ctx).__name__}"
        )
    if system_ctx.origin != PUBLIC_STIMA_ORIGIN:
        raise ProgrammingError(
            f"bridge_public_stima refuses a context with origin {system_ctx.origin!r}"
        )
    system_ctx.require_agency()

    def result(status, contact_id=None, lead_id=None, *, reason=None, contact_created=False, lead_created=False):
        value = {
            "status": status,
            "stima_id": stima_id,
            "contact_id": contact_id,
            "lead_id": lead_id,
            "contact_created": contact_created,
            "lead_created": lead_created,
        }
        if reason is not None:
            value["reason"] = reason
        return value

    with core_cursor(commit=True) as (_, cur):
        ctx = system_ctx
        lead_predicate, lead_scope = scoped_predicate(ctx, "leads", "l")
        contact_source, contact_scope = scoped_source(ctx, "contacts", "c")

        cur.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0)) AS locked",
            (f"core:public_stima:{stima_id}",),
        )
        cur.execute(
            f"""SELECT ls.lead_id,l.contact_id
            FROM lead_stime ls JOIN leads l ON l.id=ls.lead_id
            WHERE ls.stima_id=%s AND {lead_predicate}
            ORDER BY ls.id LIMIT 1 FOR UPDATE OF ls,l""",
            [stima_id] + lead_scope,
        )
        existing_link = _row(cur.fetchone())
        if existing_link:
            return result(
                "already_linked",
                existing_link["contact_id"],
                existing_link["lead_id"],
            )

        # The lock key carries the agency: two agencies may legitimately hold
        # the same email, and a shared key would serialise their unrelated
        # estimations against each other (approved plan, Task 13).
        identity_scopes = []
        if contact_data.get("email_normalized"):
            identity_scopes.append(
                f"core:contact:{ctx.agency_id}:email:{contact_data['email_normalized']}"
            )
        if contact_data.get("phone_normalized"):
            identity_scopes.append(
                f"core:contact:{ctx.agency_id}:phone:{contact_data['phone_normalized']}"
            )
        for scope in sorted(identity_scopes):
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0)) AS locked",
                (scope,),
            )

        email_matches = []
        if contact_data.get("email_normalized"):
            cur.execute(
                f"SELECT * FROM {contact_source} AND c.email_normalized=%s ORDER BY c.id FOR UPDATE",
                contact_scope + [contact_data["email_normalized"]],
            )
            email_matches = [dict(item) for item in cur.fetchall()]

        phone_matches = []
        if contact_data.get("phone_normalized"):
            cur.execute(
                f"SELECT * FROM {contact_source} AND c.phone_normalized=%s ORDER BY c.id FOR UPDATE",
                contact_scope + [contact_data["phone_normalized"]],
            )
            phone_matches = [dict(item) for item in cur.fetchall()]

        if len(email_matches) > 1 or len(phone_matches) > 1:
            return result("conflict", reason="ambiguous_identity")

        email_contact = email_matches[0] if email_matches else None
        phone_contact = phone_matches[0] if phone_matches else None
        if email_contact and phone_contact and email_contact["id"] != phone_contact["id"]:
            return result("conflict", reason="identity_conflict")

        contact = email_contact or phone_contact
        contact_created = False
        if contact and (contact.get("status") == "archived" or contact.get("archived_at") is not None):
            return result("skipped", contact["id"], reason="archived_contact")

        if contact is None:
            if not contact_data.get("display_name"):
                return result("skipped", reason="insufficient_contact_identity")
            cur.execute(
                """INSERT INTO contacts(
                    contact_type,first_name,last_name,company_name,display_name,
                    email,email_normalized,phone,phone_normalized,secondary_phone,
                    source,status,marketing_consent,marketing_consent_at,notes,
                    agency_id,created_by_user_id
                ) VALUES(
                    %(contact_type)s,%(first_name)s,%(last_name)s,%(company_name)s,%(display_name)s,
                    %(email)s,%(email_normalized)s,%(phone)s,%(phone_normalized)s,%(secondary_phone)s,
                    %(source)s,%(status)s,%(marketing_consent)s,%(marketing_consent_at)s,%(notes)s,
                    %(agency_id)s,%(created_by_user_id)s
                ) RETURNING *""",
                _stamp(ctx, contact_data),
            )
            contact = _row(cur.fetchone())
            contact_created = True

        prepared_lead = _stamp(ctx, {**lead_data, "contact_id": contact["id"]})
        cur.execute(
            """INSERT INTO leads(
                contact_id,source,pipeline,stage,priority,status,assigned_to,
                estimated_value,next_action_at,lost_reason,notes,
                agency_id,created_by_user_id
            ) VALUES(
                %(contact_id)s,%(source)s,%(pipeline)s,%(stage)s,%(priority)s,%(status)s,%(assigned_to)s,
                %(estimated_value)s,%(next_action_at)s,%(lost_reason)s,%(notes)s,
                %(agency_id)s,%(created_by_user_id)s
            ) RETURNING *""",
            prepared_lead,
        )
        lead = _row(cur.fetchone())
        cur.execute(
            """INSERT INTO lead_stime(lead_id,stima_id,relation_type)
            VALUES(%s,%s,%s) ON CONFLICT(lead_id,stima_id) DO NOTHING RETURNING *""",
            (lead["id"], stima_id, relation_type),
        )
        link = _row(cur.fetchone())
        if not link:
            cur.execute(
                f"""SELECT ls.lead_id,l.contact_id
                FROM lead_stime ls JOIN leads l ON l.id=ls.lead_id
                WHERE ls.stima_id=%s AND {lead_predicate}
                ORDER BY ls.id LIMIT 1 FOR UPDATE OF ls,l""",
                [stima_id] + lead_scope,
            )
            existing_link = _row(cur.fetchone())
            if existing_link:
                return result(
                    "already_linked",
                    existing_link["contact_id"],
                    existing_link["lead_id"],
                )
            raise ConflictError(f"stima {stima_id} could not be linked")

        return result(
            "linked",
            contact["id"],
            lead["id"],
            contact_created=contact_created,
            lead_created=True,
        )


def unlink_stima(ctx, lead_id: int, stima_id: int) -> None:
    predicate, scope_params = scoped_predicate(ctx, "leads", "l")
    with core_cursor(commit=True) as (_, cur):
        cur.execute(
            "DELETE FROM lead_stime ls USING leads l "
            f"WHERE ls.lead_id = l.id AND ls.lead_id = %s AND ls.stima_id = %s AND {predicate}",
            [lead_id, stima_id] + scope_params,
        )
        if cur.rowcount == 0:
            raise NotFoundError(f"link between lead {lead_id} and stima {stima_id} not found")


def _validate_references(cur, data: dict[str, Any], ctx=None) -> None:
    """Check the row's references before it is written.

    With a scope, `contacts` and `leads` are checked inside it, so an activity
    or task cannot be attached to another agency's record. Without one - the
    R-4 path used by the three certified modules - the checks stay as they were
    and migration 030's trigger enforces coherence at the database.
    """
    mapping = (("contact_id", "contacts", "contact"), ("lead_id", "leads", "lead"), ("stima_id", "stime", "stima"))
    for field, table, label in mapping:
        if data.get(field) is not None:
            if ctx is not None and table != "stime":
                _ensure_exists_scoped(ctx, cur, table, data[field], label)
            else:
                _ensure_exists(cur, table, data[field], label)


def create_activity_with_cursor(cur, data: dict[str, Any], *, ctx=None) -> dict[str, Any]:
    """Create one CORE activity using an already-open database transaction.

    `ctx` is optional by design (R-4). Called with one, the row is stamped from
    the scope and the 030 trigger validates the stamp. Called without one - as
    the three certified modules do - the statement is byte for byte what it was
    before P26-1 and the trigger derives `agency_id` from the references.
    """
    _validate_references(cur, data, ctx=ctx)
    prepared = {**data, "metadata": Json(data.get("metadata") or {})}
    columns = ""
    values = ""
    if ctx is not None:
        prepared = {**_stamp(ctx, prepared)}
        columns = ", agency_id, created_by_user_id"
        values = ", %(agency_id)s, %(created_by_user_id)s"
    cur.execute(
        f"""
        INSERT INTO activities (
            contact_id, lead_id, stima_id, activity_type, direction, channel,
            subject, description, outcome, occurred_at, created_by, metadata{columns}
        ) VALUES (
            %(contact_id)s, %(lead_id)s, %(stima_id)s, %(activity_type)s, %(direction)s,
            %(channel)s, %(subject)s, %(description)s, %(outcome)s,
            COALESCE(%(occurred_at)s, NOW()), %(created_by)s, %(metadata)s{values}
        ) RETURNING *
        """,
        prepared,
    )
    return _row(cur.fetchone())


def create_activity(ctx, data: dict[str, Any]) -> dict[str, Any]:
    ctx.require_agency()
    with core_cursor(commit=True) as (_, cur):
        return create_activity_with_cursor(cur, data, ctx=ctx)


def list_activities(ctx, limit: int, offset: int, contact_id: int | None, lead_id: int | None, stima_id: int | None):
    source, params = scoped_source(ctx, "activities", "a")
    filters = []
    for column, value in (("contact_id", contact_id), ("lead_id", lead_id), ("stima_id", stima_id)):
        if value is not None:
            filters.append(f"{column} = %s")
            params.append(value)
    clause = " AND " + " AND ".join(filters) if filters else ""
    params.extend([limit, offset])
    with core_cursor() as (_, cur):
        cur.execute(f"SELECT * FROM {source}{clause} ORDER BY occurred_at DESC, id DESC LIMIT %s OFFSET %s", params)
        return [dict(row) for row in cur.fetchall()]


def create_task_with_cursor(cur, data: dict[str, Any], *, ctx=None) -> dict[str, Any]:
    """Create one CORE task using an already-open transaction. See R-4 above."""
    _validate_references(cur, data, ctx=ctx)
    prepared = {**data, "metadata": Json(data.get("metadata") or {})}
    columns = ""
    values = ""
    if ctx is not None:
        prepared = {**_stamp(ctx, prepared)}
        columns = ", agency_id, created_by_user_id"
        values = ", %(agency_id)s, %(created_by_user_id)s"
    cur.execute(
        f"""
        INSERT INTO tasks (
            contact_id, lead_id, stima_id, title, description, task_type,
            priority, status, due_at, completed_at, assigned_to, created_by, metadata{columns}
        ) VALUES (
            %(contact_id)s, %(lead_id)s, %(stima_id)s, %(title)s, %(description)s,
            %(task_type)s, %(priority)s, %(status)s, %(due_at)s, %(completed_at)s,
            %(assigned_to)s, %(created_by)s, %(metadata)s{values}
        ) RETURNING *
        """,
        prepared,
    )
    return _row(cur.fetchone())


def create_task(ctx, data: dict[str, Any]) -> dict[str, Any]:
    ctx.require_agency()
    with core_cursor(commit=True) as (_, cur):
        return create_task_with_cursor(cur, data, ctx=ctx)


def list_tasks(ctx, limit: int, offset: int, contact_id: int | None, lead_id: int | None, stima_id: int | None, status: str | None):
    source, params = scoped_source(ctx, "tasks", "t")
    filters = []
    for column, value in (("contact_id", contact_id), ("lead_id", lead_id), ("stima_id", stima_id), ("status", status)):
        if value is not None:
            filters.append(f"{column} = %s")
            params.append(value)
    clause = " AND " + " AND ".join(filters) if filters else ""
    params.extend([limit, offset])
    with core_cursor() as (_, cur):
        cur.execute(f"SELECT * FROM {source}{clause} ORDER BY due_at NULLS LAST, created_at DESC, id DESC LIMIT %s OFFSET %s", params)
        return [dict(row) for row in cur.fetchall()]


def update_task(ctx, task_id: int, data: dict[str, Any]) -> dict[str, Any]:
    _reject_server_owned(data)
    if not data:
        source, params = scoped_source(ctx, "tasks", "t")
        with core_cursor() as (_, cur):
            cur.execute(f"SELECT * FROM {source} AND t.id = %s", params + [task_id])
            row = cur.fetchone()
            if not row:
                raise NotFoundError(f"task {task_id} not found")
            return _row(row)
    if "metadata" in data:
        data["metadata"] = Json(data.get("metadata") or {})
    predicate, scope_params = scoped_predicate(ctx, "tasks", "t")
    assignments = [f"{key} = %s" for key in data]
    params = list(data.values()) + [task_id] + scope_params
    with core_cursor(commit=True) as (_, cur):
        cur.execute(
            f"UPDATE tasks t SET {', '.join(assignments)}, updated_at = NOW() "
            f"WHERE t.id = %s AND {predicate} RETURNING *",
            params,
        )
        row = cur.fetchone()
        if not row:
            raise NotFoundError(f"task {task_id} not found")
        return _row(row)


def delete_activity(ctx, activity_id: int) -> None:
    predicate, scope_params = scoped_predicate(ctx, "activities", "a")
    with core_cursor(commit=True) as (_, cur):
        cur.execute(
            f"DELETE FROM activities a WHERE a.id = %s AND {predicate}",
            [activity_id] + scope_params,
        )
        if cur.rowcount == 0:
            raise NotFoundError(f"activity {activity_id} not found")


def delete_task(ctx, task_id: int) -> None:
    predicate, scope_params = scoped_predicate(ctx, "tasks", "t")
    with core_cursor(commit=True) as (_, cur):
        cur.execute(
            f"DELETE FROM tasks t WHERE t.id = %s AND {predicate}",
            [task_id] + scope_params,
        )
        if cur.rowcount == 0:
            raise NotFoundError(f"task {task_id} not found")
