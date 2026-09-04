"""P24 - read-only eligibility predicate for Seller Database Revival.

Both functions below accept an already-open cursor (same convention as
core.repository.create_task_with_cursor and next_best_action.signals
.resolve_stima_contact_lead): callers own the transaction/connection, this
module only issues read-only SELECTs.

Frozen business rules implemented here (see the P24 Design Closure report
in this conversation for the full audit trail):

  - subject: leads.status = 'paused' AND leads.pipeline = 'sell'
    (bridge_public_stima hardcodes pipeline='sell'; contact_roles is never
    auto-populated by it, so it is NOT used here - see GAP 1);
  - leads.stage != 'won';
  - contacts.marketing_consent IS TRUE (FALSE/NULL excluded);
  - contacts.status != 'archived';
  - dormancy: last_activity_at <= NOW() - INTERVAL '180 days', where
    last_activity_at = GREATEST over activities.occurred_at,
    seller_timeline_events.occurred_at and tasks.completed_at (status=
    'completed'), falling back to leads.created_at when none exist.
    leads.updated_at is NEVER used (it is touched by purely technical
    UPDATEs - see core.repository.update_lead) - see GAP 4/5;
  - sold-property exclusion via leads -> property_leads -> properties
    (NOT property_contacts.contact_id, which could match an unrelated
    property of the same contact) - see GAP 2;
  - active commercial pipeline exclusion: properties.commercial_status IN
    ('mandate','active','reserved','under_offer'), OR an active
    property_sales row (status='pending') OR an open property_proposals
    row (status IN ('draft','submitted')) for the SAME linked property -
    see GAP 3;
  - no open/in_progress CORE task and no pending followup_actions row
    linked to the contact or lead;
  - leads.next_action_at IS NULL OR already in the past;
  - one row per contact (GROUP/PARTITION by contact_id, tie-break
    last_activity_at ASC then lead_id ASC) - see GAP 5/6.

No relational integration test is possible in this environment (no
Postgres reachable - see the disclosed limitation in
tests/test_database_revival_eligibility.py's module docstring); the SQL
below is exercised at the text/parameter level only.
"""

from __future__ import annotations

from typing import Any

# Shared WHERE-body reused verbatim by both functions below, so the batch
# selection and the single-row live re-validation can never diverge on the
# actual eligibility predicate - only the framing SELECT differs (a ranked
# list with LIMIT vs. a single-row EXISTS).
_ELIGIBILITY_PREDICATE_SQL = """
    l.status = 'paused'
    AND l.pipeline = 'sell'
    AND l.stage != 'won'
    AND c.marketing_consent IS TRUE
    AND c.status != 'archived'
    AND (l.next_action_at IS NULL OR l.next_action_at <= NOW())
    AND last_activity_at(l.id, l.contact_id, l.created_at) <= NOW() - INTERVAL '180 days'
    AND NOT EXISTS (
        SELECT 1 FROM property_leads pl
        JOIN properties p ON p.id = pl.property_id
        WHERE pl.lead_id = l.id AND p.commercial_status = 'sold'
    )
    AND NOT EXISTS (
        SELECT 1 FROM property_leads pl
        JOIN properties p ON p.id = pl.property_id
        WHERE pl.lead_id = l.id
          AND p.commercial_status IN ('mandate', 'active', 'reserved', 'under_offer')
    )
    AND NOT EXISTS (
        SELECT 1 FROM property_leads pl
        JOIN property_sales ps ON ps.property_id = pl.property_id
        WHERE pl.lead_id = l.id AND ps.status = 'pending'
    )
    AND NOT EXISTS (
        SELECT 1 FROM property_leads pl
        JOIN matches m ON m.property_id = pl.property_id
        JOIN property_proposals pp ON pp.match_id = m.id
        WHERE pl.lead_id = l.id AND pp.status IN ('draft', 'submitted')
    )
    AND NOT EXISTS (
        SELECT 1 FROM tasks t
        WHERE (t.lead_id = l.id OR t.contact_id = l.contact_id)
          AND t.status IN ('open', 'in_progress')
    )
    AND NOT EXISTS (
        SELECT 1 FROM followup_actions fa
        WHERE (fa.lead_id = l.id OR fa.contact_id = l.contact_id)
          AND fa.status = 'pending'
    )
"""

# `last_activity_at(...)` above is descriptive, not a real SQL function -
# it is expanded inline in both queries below via this LATERAL-free
# GREATEST/COALESCE expression, kept as one string so both queries use the
# identical computation (never duplicated ad hoc).
_LAST_ACTIVITY_EXPR_SQL = """
    GREATEST(
        COALESCE(
            (SELECT MAX(a.occurred_at) FROM activities a
             WHERE a.lead_id = l.id OR a.contact_id = l.contact_id),
            l.created_at
        ),
        COALESCE(
            (SELECT MAX(ste.occurred_at) FROM seller_timeline_events ste
             WHERE ste.lead_id = l.id
                OR ste.stima_id IN (SELECT stima_id FROM lead_stime WHERE lead_id = l.id)),
            l.created_at
        ),
        COALESCE(
            (SELECT MAX(t.completed_at) FROM tasks t
             WHERE (t.lead_id = l.id OR t.contact_id = l.contact_id) AND t.status = 'completed'),
            l.created_at
        )
    )
"""


def _predicate_sql() -> str:
    return _ELIGIBILITY_PREDICATE_SQL.replace(
        "last_activity_at(l.id, l.contact_id, l.created_at)", _LAST_ACTIVITY_EXPR_SQL
    )


def find_eligible_candidates(
    cur, exclude_contact_ids: set[int] | list[int], limit: int
) -> list[dict[str, Any]]:
    """Batch selection for ensure_today_batch: up to `limit` (contact_id,
    lead_id, last_activity_at) rows, one per contact, ordered by the
    frozen tie-break (oldest last_activity_at first, then lowest lead_id),
    excluding any contact_id already passed in `exclude_contact_ids`
    (contacts currently in cooldown or already in today's batch)."""
    # Two-step CTE on purpose: last_activity_at must be a materialized
    # column of `dormant` before it can be referenced inside `ranked`'s
    # window ORDER BY - a window definition cannot reference a sibling
    # alias computed in the very same SELECT list.
    query = f"""
        WITH dormant AS (
            SELECT
                l.contact_id AS contact_id,
                l.id AS lead_id,
                {_LAST_ACTIVITY_EXPR_SQL} AS last_activity_at
            FROM leads l
            JOIN contacts c ON c.id = l.contact_id
            WHERE
                {_predicate_sql()}
                AND l.contact_id != ALL(%(exclude_contact_ids)s)
        ),
        ranked AS (
            SELECT
                contact_id,
                lead_id,
                last_activity_at,
                ROW_NUMBER() OVER (
                    PARTITION BY contact_id
                    ORDER BY last_activity_at ASC, lead_id ASC
                ) AS rn
            FROM dormant
        )
        SELECT contact_id, lead_id, last_activity_at
        FROM ranked
        WHERE rn = 1
        ORDER BY last_activity_at ASC, lead_id ASC
        LIMIT %(limit)s
    """
    cur.execute(
        query,
        {
            "exclude_contact_ids": list(exclude_contact_ids),
            "limit": limit,
        },
    )
    return list(cur.fetchall())


def is_still_eligible(cur, *, contact_id: int, lead_id: int) -> bool:
    """Live re-validation used by database_revival.service.collect_today_
    signals for a single (contact_id, lead_id) pair already in today's
    batch. Reuses the exact same predicate as find_eligible_candidates,
    minus the cooldown/batch exclusion clause (which does not apply to a
    row checking itself) - see the module docstring / GAP 6."""
    query = f"""
        SELECT EXISTS (
            SELECT 1
            FROM leads l
            JOIN contacts c ON c.id = l.contact_id
            WHERE l.id = %(lead_id)s
              AND l.contact_id = %(contact_id)s
              AND {_predicate_sql()}
        ) AS eligible
    """
    cur.execute(query, {"lead_id": lead_id, "contact_id": contact_id})
    row = cur.fetchone()
    return bool(row["eligible"]) if row else False
