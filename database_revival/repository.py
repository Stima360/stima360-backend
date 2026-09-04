"""P24 - persistence primitives for seller_revival_suppressions.

All functions here accept an already-open cursor and are meant to run
inside the single transaction opened by
database_revival.service.ensure_today_batch (same cursor-sharing
convention as core.repository.create_task_with_cursor). No function here
opens its own connection.
"""

from __future__ import annotations

from typing import Any

DAILY_BATCH_LOCK_SCOPE = "database_revival:daily_batch"


def acquire_daily_batch_lock(cur) -> None:
    """First operation of ensure_today_batch's transaction: a fixed-scope,
    transaction-scoped advisory lock (auto-released on commit/rollback,
    same pattern already used by core.repository.bridge_public_stima and
    flow.repository) that serializes COUNT -> eligibility -> INSERT across
    concurrent refreshes, so the 20/day cap holds structurally."""
    cur.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        (DAILY_BATCH_LOCK_SCOPE,),
    )


def count_batch_today(cur) -> int:
    """Number of contacts already in today's revival batch (rows with
    created_at on the current Postgres-side calendar day)."""
    cur.execute(
        """
        SELECT COUNT(*) AS n
        FROM seller_revival_suppressions
        WHERE created_at::date = CURRENT_DATE
        """
    )
    row = cur.fetchone()
    return int(row["n"]) if row else 0


def get_cooldown_contact_ids(cur) -> set[int]:
    """Contact ids currently blocked from (re-)selection: an unexpired
    suppression row, whether created today (today's batch) or still
    cooling down from an earlier batch - both are excluded identically by
    the same expires_at > NOW() check."""
    cur.execute(
        """
        SELECT contact_id
        FROM seller_revival_suppressions
        WHERE expires_at > NOW()
        """
    )
    return {row["contact_id"] for row in cur.fetchall()}


def upsert_batch_row(cur, *, contact_id: int, lead_id: int | None) -> bool:
    """Conditional UPSERT: creates a new suppression row, or - only if the
    existing row for this contact has already expired - reuses it (new
    created_at/expires_at/lead_id). Never overwrites an active (non
    expired) cooldown row: the WHERE guard on DO UPDATE makes that case a
    no-op (rowcount 0), never a DO NOTHING dead end, so a contact is never
    permanently stuck in cooldown - see the P24 Design Closure report,
    "Expired suppression fix"."""
    cur.execute(
        """
        INSERT INTO seller_revival_suppressions (contact_id, lead_id, created_at, expires_at)
        VALUES (%(contact_id)s, %(lead_id)s, NOW(), NOW() + INTERVAL '90 days')
        ON CONFLICT (contact_id) DO UPDATE SET
            lead_id = EXCLUDED.lead_id,
            created_at = EXCLUDED.created_at,
            expires_at = EXCLUDED.expires_at
        WHERE seller_revival_suppressions.expires_at <= NOW()
        """,
        {"contact_id": contact_id, "lead_id": lead_id},
    )
    return bool(cur.rowcount)


def list_batch_today(cur) -> list[dict[str, Any]]:
    """Rows that make up today's revival batch (created_at on the current
    calendar day) - read by database_revival.service.collect_today_signals
    for live re-validation and NBA candidate emission."""
    cur.execute(
        """
        SELECT contact_id, lead_id, created_at, expires_at
        FROM seller_revival_suppressions
        WHERE created_at::date = CURRENT_DATE
        """
    )
    return list(cur.fetchall())
