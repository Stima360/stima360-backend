"""The mandatory agency predicate for every CORE query.

P26-1 rejects the obvious alternative - an optional ``agency_id=None`` filter
parameter on each repository function - because forgetting it fails *open*: the
query still runs, and it silently returns another agency's rows. The approach
adopted instead (design spec section 2.5) is that the table name and its scope
are emitted by the *same* expression:

    source, params = scoped_source(ctx, 'contacts', 'c')
    cur.execute(f'SELECT c.* FROM {source} AND c.status = %s', params + [status])

A caller therefore cannot name a scoped table without carrying its predicate,
and cannot forget the ``WHERE``: it is already there, so every additional
filter is appended with ``AND``.

Two context types reach this module, and both satisfy the ``AgencyScope``
protocol (spec section 8.1):

* ``OperatorContext``     - an authenticated human.
* ``SystemAgencyContext`` - the public estimation flow, which has no operator.
  It presents ``role=None``, ``user_id=None`` and ``is_platform_admin=False``,
  so it takes the plain single-agency branch with no special case here at all.

This module is pure scoping logic. It opens no connection, reads nothing from
the request, constructs no operator scope, and knows nothing about HTTP.
"""
from __future__ import annotations

from operator_auth.context import AgencyScope, SystemAgencyContext
from operator_auth.enums import DEFAULT_AGENCY_SLUG

from .exceptions import ConflictError

# The four CORE tables that carry an agency. Frozen: a set could be widened at
# runtime by any module that imported it.
SCOPED_TABLES = frozenset({
    "contacts",
    "leads",
    "activities",
    "tasks",
})

# The subset an agent is narrowed on. activities and tasks are deliberately
# absent: they have no assigned_agent_id in P26-1 (spec section 3.3), and their
# existing free-text assignment column is written by automation rather than by
# a person. An agent therefore sees those two for the whole agency. Stated as a
# decision rather than left as an omission; narrowing them belongs to P26-2.
AGENT_ASSIGNABLE = frozenset({
    "contacts",
    "leads",
})

# The one public function in core/repository.py allowed to build its own scope
# instead of receiving one (spec section 2.5.1 rule 4, branch b). Exactly one
# member: a second system-context writer cannot appear without editing this
# line, which is precisely the deliberate act that should be required.
SYSTEM_CONTEXT_FUNCTIONS = frozenset({
    "bridge_public_stima",
})


class ProgrammingError(Exception):
    """A scoped table was requested that this builder does not scope.

    Deliberately *not* a subclass of ``core.exceptions.CoreError``. The router
    translates NotFoundError, ConflictError and ValidationError into 4xx
    responses; a table name is a developer literal and never request input, so
    reaching this is a defect in this codebase. It must surface as a failure
    rather than be dressed up as a client mistake.

    Unrelated to psycopg2's exception of the same name: nothing here touches a
    driver.
    """


def scoped_predicate(ctx: AgencyScope, table: str, alias: str) -> tuple[str, list]:
    """Return ``('<predicate>', params)`` - never empty, never unscoped.

    The single place the agency rules are expressed. ``scoped_source`` wraps
    this; repository statements whose shape has no room for a FROM-clause
    source - ``UPDATE``, ``DELETE``, and the ``USING`` joins that guard child
    tables - compose the predicate directly. Both go through this function, so
    there is exactly one copy of the role logic in the codebase.

    Values travel as ``%s`` parameters. ``table`` and ``alias`` are developer
    literals, and ``table`` is checked against ``SCOPED_TABLES`` before it is
    interpolated.
    """
    if table not in SCOPED_TABLES:
        raise ProgrammingError(f"{table!r} is not a scoped CORE table")

    # The only cross-agency branch in P26-1, and the only bare TRUE. It needs
    # both conditions: the flag alone is not enough, because a platform admin
    # who holds a membership is acting inside that agency and is scoped like
    # anyone else. Compared with `is True` rather than for truthiness so a
    # non-boolean value cannot widen the query.
    if ctx.is_platform_admin is True and ctx.agency_id is None:
        return "TRUE", []

    # Everyone else: agency_owner, agency_admin, agent, a platform admin bound
    # to an agency, and SystemAgencyContext. require_agency() refuses an
    # unbound scope rather than returning an unfiltered predicate.
    parts = [f"{alias}.agency_id = %s"]
    params = [ctx.require_agency()]

    if ctx.role == "agent" and table in AGENT_ASSIGNABLE:
        parts.append(f"{alias}.assigned_agent_id = %s")
        params.append(ctx.user_id)

    return " AND ".join(parts), params


def scoped_source(ctx: AgencyScope, table: str, alias: str) -> tuple[str, list]:
    """Return ``('<table> <alias> WHERE <predicate>', params)`` - never without.

    One return statement, and the only place in the codebase that emits the
    ``WHERE`` keyword for a scoped table. A caller appending its own filters
    writes ``' AND ...'`` and can never be the one responsible for starting the
    clause, so a forgotten predicate is not an available mistake.
    """
    predicate, params = scoped_predicate(ctx, table, alias)
    return f"{table} {alias} WHERE {predicate}", params


def resolve_default_agency_id(cur) -> int:
    """Resolve the Default Agency's id, server-side, from its slug.

    The single lookup behind both P26-1 flows that need the Default Agency: the
    public-STIMA system context below, and the legacy-Basic compatibility
    context in operator_auth/dependencies.py. One copy means one place where
    the slug, the active-status filter and the fail-closed behaviour live.

    Raises ConflictError when no active Default Agency is present. That is the
    fail-closed answer: returning None would push an unscoped write downstream,
    and defaulting to an id would attribute records to whichever agency
    happened to hold it.
    """
    cur.execute(
        "SELECT id FROM agencies WHERE slug = %s AND status = 'active'",
        (DEFAULT_AGENCY_SLUG,),
    )
    row = cur.fetchone()
    if row is None:
        raise ConflictError(
            f"no active agency with slug {DEFAULT_AGENCY_SLUG!r}"
        )
    return row["id"]


def system_context_for_public_stima(cur) -> SystemAgencyContext:
    """The only factory for a ``SystemAgencyContext``.

    Takes an open cursor and nothing else. There is no ``agency_id``
    parameter, no slug parameter and no client selector anywhere in the
    signature, so the agency is a query result and can never originate in a
    request (spec section 15 item 52d).

    Runs inside the caller's transaction, so the agency it resolves is the one
    the caller's subsequent writes are checked against.

    Raises ConflictError when no active Default Agency is present - see
    resolve_default_agency_id, which owns that lookup.
    """
    return SystemAgencyContext(
        agency_id=resolve_default_agency_id(cur), origin="public_stima"
    )
