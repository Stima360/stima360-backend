"""Raw SQL repository for the P17 Seller Intelligence module.

No validation lives here. This module trusts its caller (service.py) to
have already enforced the "at least one reference" rule; the repository's
only job is to persist rows and apply the idempotency contract.
"""

from __future__ import annotations

from typing import Any

from psycopg2.extras import Json

from .database import si_cursor


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
    payload = {**data, "payload": Json(data.get("payload") or {})}
    with si_cursor(commit=True) as (_, cur):
        cur.execute(
            """
            INSERT INTO seller_timeline_events (
                contact_id, lead_id, stima_id, property_id,
                event_type, event_source, occurred_at, payload,
                idempotency_key, created_by
            ) VALUES (
                %(contact_id)s, %(lead_id)s, %(stima_id)s, %(property_id)s,
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

        # Conflict on idempotency_key: no row was inserted. Return the
        # existing one deterministically, mirroring the fallback already
        # used by core.repository.bridge_public_stima for lead_stime.
        cur.execute(
            "SELECT * FROM seller_timeline_events WHERE idempotency_key = %s",
            (data.get("idempotency_key"),),
        )
        existing = _row(cur.fetchone())
        if existing is None:
            # Should not happen (ON CONFLICT implies a pre-existing row),
            # but never leave the caller with an unexplained None.
            raise RuntimeError(
                f"seller_timeline_events insert conflicted on idempotency_key="
                f"{data.get('idempotency_key')!r} but no existing row was found"
            )
        return existing


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
