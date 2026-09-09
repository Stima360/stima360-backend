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


# ---------------------------------------------------------------------------
# P26-6B agency scoping.
#
# `seller_revival_suppressions` stays CHILD-DERIVED: contact_id is NOT NULL and
# ON DELETE CASCADE, so a suppression cannot outlive the contact that gives it a
# tenant. Every function below reaches it through that contact.
#
# Two things here are not row reads and would have been easy to leave global:
#
#   the daily cap   `count_batch_today` decides how many contacts may enter
#                   today's batch. Counted across tenants, agency B's revivals
#                   consume agency A's twenty slots - A simply gets fewer, with
#                   no error and nothing foreign ever returned.
#
#   the lock        one fixed advisory key serialised every agency's batch
#                   against every other's. Correctness was never at risk, but A
#                   and B blocked each other for no reason, and a lock whose
#                   scope is wider than the data it protects is the kind of
#                   thing that only shows up under load.
# ---------------------------------------------------------------------------

def daily_batch_lock_scope(agency_id: int) -> str:
    """The advisory key for one agency's batch.

    A fixed namespace plus the agency id, hashed by Postgres itself through
    `hashtextextended` - the same pattern core.repository.bridge_public_stima
    and flow.repository already use. Deliberately not Python's `hash()`: that is
    randomised per process by PYTHONHASHSEED, so two workers would compute
    different keys for the same agency and the lock would not serialise anything.
    """
    return f"{DAILY_BATCH_LOCK_SCOPE}:agency:{agency_id}"


def acquire_daily_batch_lock_for_agency(cur, agency_id: int) -> None:
    cur.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        (daily_batch_lock_scope(agency_id),),
    )


def count_batch_today_for_agency(cur, agency_id: int) -> int:
    """Today's batch size for one agency - the number the daily cap divides."""
    cur.execute(
        """
        SELECT COUNT(*) AS n
        FROM seller_revival_suppressions s
        JOIN contacts c ON c.id = s.contact_id
        WHERE s.created_at::date = CURRENT_DATE
          AND c.agency_id = %s
        """,
        (agency_id,),
    )
    row = cur.fetchone()
    return int(row["n"]) if row else 0


def get_cooldown_contact_ids_for_agency(cur, agency_id: int) -> set[int]:
    cur.execute(
        """
        SELECT s.contact_id
        FROM seller_revival_suppressions s
        JOIN contacts c ON c.id = s.contact_id
        WHERE s.expires_at > NOW()
          AND c.agency_id = %s
        """,
        (agency_id,),
    )
    return {row["contact_id"] for row in cur.fetchall()}


def upsert_batch_row_for_agency(
    cur, *, contact_id: int, lead_id: int | None, agency_id: int
) -> bool:
    """As the ctx-less upsert, with both references proved to be this agency's.

    `contact_id` is checked because the row's tenancy comes from it, and
    `lead_id` because 048's trigger refuses a suppression whose optional lead
    belongs to another agency - this is the first line, the trigger is the
    second.
    """
    cur.execute(
        "SELECT id FROM contacts WHERE id = %s AND agency_id = %s",
        (contact_id, agency_id),
    )
    if cur.fetchone() is None:
        return False
    if lead_id is not None:
        cur.execute(
            "SELECT id FROM leads WHERE id = %s AND agency_id = %s",
            (lead_id, agency_id),
        )
        if cur.fetchone() is None:
            return False
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


def list_batch_today_for_agency(cur, agency_id: int) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT s.contact_id, s.lead_id, s.created_at, s.expires_at
        FROM seller_revival_suppressions s
        JOIN contacts c ON c.id = s.contact_id
        WHERE s.created_at::date = CURRENT_DATE
          AND c.agency_id = %s
        """,
        (agency_id,),
    )
    return list(cur.fetchall())


def list_active_agency_ids(cur) -> list[int]:
    """The tenants a server-only batch iterates, one bounded cycle each."""
    cur.execute("SELECT id FROM agencies WHERE status = 'active' ORDER BY id")
    return [row["id"] for row in cur.fetchall()]
