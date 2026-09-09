"""Raw SQL repository for the P17 Seller Intelligence module.

No validation lives here. This module trusts its caller (service.py) to
have already enforced the "at least one reference" rule; the repository's
only job is to persist rows and apply the idempotency contract.
"""

from __future__ import annotations

from typing import Any

from psycopg2.extras import Json

from .database import si_cursor
from .exceptions import ValidationError


def _row(row):
    return dict(row) if row else None


def insert_event(data: dict[str, Any]) -> dict[str, Any]:
    """Insert one seller_timeline_events row.

    Idempotency contract: if ``idempotency_key`` is not None and a row with
    the same key already exists, no new row is written and the existing row
    is returned instead (deterministic, never raises). If ``idempotency_key``
    is None, a new row is always written - manual/free-form events are never
    deduplicated.
    """
    # P26-6A: this is the system write path - the public estimation funnel calls
    # it through service.safe_record_event and has no operator, so there is no
    # scope to receive. The agency is derived from the references themselves:
    # exactly one distinct agency, or a refusal. That is the same rule migration
    # 044 uses to backfill history, deliberately shared so the write path and
    # the backfill cannot disagree about what an event's agency is.
    #
    # The signature and return contract are unchanged - several P17 tests and
    # database_revival call this directly.
    with si_cursor(commit=True) as (_, cur):
        agency_id = derive_agency_id(cur, data)
        return _insert_event_with_agency(cur, data, agency_id)


def list_timeline(
    *,
    contact_id: int | None = None,
    lead_id: int | None = None,
    stima_id: int | None = None,
    property_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    where = []
    params: list[Any] = []
    if contact_id is not None:
        where.append("contact_id = %s")
        params.append(contact_id)
    if lead_id is not None:
        where.append("lead_id = %s")
        params.append(lead_id)
    if stima_id is not None:
        where.append("stima_id = %s")
        params.append(stima_id)
    if property_id is not None:
        where.append("property_id = %s")
        params.append(property_id)

    clause = " WHERE " + " AND ".join(where) if where else ""
    params.extend([limit, offset])

    with si_cursor() as (_, cur):
        cur.execute(
            f"""
            SELECT * FROM seller_timeline_events{clause}
            ORDER BY occurred_at DESC, id DESC
            LIMIT %s OFFSET %s
            """,
            params,
        )
        return [dict(row) for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# P26-6A agency scoping.
#
# `seller_timeline_events` carries a physical agency_id (migration 043) because
# all four of its references are nullable and ON DELETE SET NULL: a historical
# row can outlive every parent it derived from, and a log that forgets whose it
# is cannot be scoped afterwards.
#
# Two write paths reach this module, and neither takes an agency from a client:
#
#   operator  POST /api/seller-intelligence/events -> insert_event_scoped,
#             which stamps ctx.require_agency() and refuses any reference that
#             is not in it.
#   system    main.py's public estimation funnel, which has no operator at all
#             -> derive_agency_id(), which resolves the agency from the
#             references themselves: exactly one distinct agency, or a refusal.
#
# The second rule is the one migration 044 uses to backfill history. Sharing it
# is deliberate: the write path and the backfill cannot disagree about what an
# event's agency is.
#
# The legacy `insert_event` / `list_timeline` above are untouched.
# ---------------------------------------------------------------------------

_REFERENCE_SOURCES = (
    ("contact_id", "contacts"),
    ("lead_id", "leads"),
    ("stima_id", "stime"),
    ("property_id", "properties"),
)


class AgencyDerivationError(Exception):
    """The references do not determine exactly one agency.

    A programmer/data error rather than a client error: it means either that a
    row has outlived all of its parents, or that it points at two tenants.
    """


def derive_agency_id(cur, data: dict[str, Any]) -> int:
    """The one agency every present reference agrees on, or a refusal.

    No Default Agency, no MIN/MAX/LIMIT 1, no COALESCE across candidates. Each
    of those would turn "undecidable" into "here is an answer", which for a
    tenancy is the one conversion that must never be made: a wrong owner does
    not fail, it produces a row that looks correct in the wrong tenant.
    """
    candidates: set[int] = set()
    for field, table in _REFERENCE_SOURCES:
        value = data.get(field)
        if value is None:
            continue
        cur.execute(f"SELECT agency_id FROM {table} WHERE id = %s", (value,))
        row = cur.fetchone()
        if row is None or row["agency_id"] is None:
            continue
        candidates.add(row["agency_id"])

    if not candidates:
        raise AgencyDerivationError(
            "no reference on this seller timeline event determines an agency"
        )
    if len(candidates) > 1:
        raise AgencyDerivationError(
            f"seller timeline event references {len(candidates)} agencies: "
            f"{sorted(candidates)}"
        )
    return candidates.pop()


def _assert_references_in_agency(cur, data: dict[str, Any], agency_id: int) -> None:
    """Every supplied reference must belong to the caller's agency."""
    for field, table in _REFERENCE_SOURCES:
        value = data.get(field)
        if value is None:
            continue
        cur.execute(
            f"SELECT id FROM {table} WHERE id = %s AND agency_id = %s",
            (value, agency_id),
        )
        if cur.fetchone() is None:
            raise ValidationError(f"{field} {value} not found")


def _insert_event_with_agency(cur, data: dict[str, Any], agency_id: int) -> dict[str, Any]:
    payload = {
        **data,
        "payload": Json(data.get("payload") or {}),
        "agency_id": agency_id,
    }
    payload.setdefault("event_source", None)
    payload.setdefault("occurred_at", None)
    payload.setdefault("idempotency_key", None)
    payload.setdefault("created_by", None)
    for field, _table in _REFERENCE_SOURCES:
        payload.setdefault(field, None)

    cur.execute(
        """
        INSERT INTO seller_timeline_events (
            agency_id, contact_id, lead_id, stima_id, property_id,
            event_type, event_source, occurred_at, payload,
            idempotency_key, created_by
        ) VALUES (
            %(agency_id)s, %(contact_id)s, %(lead_id)s, %(stima_id)s, %(property_id)s,
            %(event_type)s, %(event_source)s, %(occurred_at)s, %(payload)s,
            %(idempotency_key)s, %(created_by)s
        )
        ON CONFLICT (idempotency_key) WHERE idempotency_key IS NOT NULL
        DO NOTHING
        RETURNING *
        """,
        payload,
    )
    row = _row(cur.fetchone())
    if row is not None:
        return row

    # Conflict on the globally unique key. The lookup is agency-scoped, so a
    # replay of another tenant's key finds nothing here and raises instead of
    # returning that tenant's row: the caller learns the key is taken, which is
    # unavoidable for a global key, and never learns the record.
    cur.execute(
        """SELECT * FROM seller_timeline_events
           WHERE idempotency_key = %s AND agency_id = %s""",
        (data.get("idempotency_key"), agency_id),
    )
    existing = _row(cur.fetchone())
    if existing is None:
        raise ValidationError("idempotency key already used")
    return existing


def insert_event_scoped(ctx, data: dict[str, Any]) -> dict[str, Any]:
    agency_id = ctx.require_agency()
    with si_cursor(commit=True) as (_, cur):
        _assert_references_in_agency(cur, data, agency_id)
        return _insert_event_with_agency(cur, data, agency_id)


def list_timeline_scoped(
    ctx,
    *,
    contact_id: int | None = None,
    lead_id: int | None = None,
    stima_id: int | None = None,
    property_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    agency_id = ctx.require_agency()
    where = ["agency_id = %s"]
    params: list[Any] = [agency_id]
    for field, value in (
        ("contact_id", contact_id),
        ("lead_id", lead_id),
        ("stima_id", stima_id),
        ("property_id", property_id),
    ):
        if value is not None:
            where.append(f"{field} = %s")
            params.append(value)
    params.extend([limit, offset])

    with si_cursor() as (_, cur):
        cur.execute(
            f"""
            SELECT * FROM seller_timeline_events
            WHERE {' AND '.join(where)}
            ORDER BY occurred_at DESC, id DESC
            LIMIT %s OFFSET %s
            """,
            params,
        )
        return [dict(row) for row in cur.fetchall()]
