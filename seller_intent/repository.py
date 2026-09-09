from __future__ import annotations

from typing import Any

from .database import seller_intent_cursor


def _row(row):
    return dict(row) if row else None


def get_lead_intent_inputs(lead_id: int) -> dict[str, Any] | None:
    with seller_intent_cursor() as (_, cur):
        cur.execute(
            """
            WITH lead_ctx AS (
                SELECT l.id, l.status, l.stage
                FROM leads l
                WHERE l.id = %s
            ),
            linked_stime AS (
                SELECT ls.stima_id
                FROM lead_stime ls
                WHERE ls.lead_id = %s
            )
            SELECT
                lc.id AS lead_id,
                lc.status AS lead_status,
                lc.stage AS lead_stage,
                EXISTS (
                    SELECT 1
                    FROM seller_timeline_events ste
                    WHERE ste.event_type = 'stima_completata'
                      AND (
                           ste.lead_id = lc.id
                           OR ste.stima_id IN (SELECT stima_id FROM linked_stime)
                      )
                ) AS has_stima_completata,
                EXISTS (
                    SELECT 1
                    FROM tasks t
                    WHERE (
                           t.lead_id = lc.id
                           OR t.stima_id IN (SELECT stima_id FROM linked_stime)
                          )
                      AND t.status = 'in_progress'
                      AND t.task_type = 'automated_followup'
                      AND t.title = 'Contattare proprietario'
                      AND COALESCE(t.metadata->>'source', '') = 'followup'
                      AND COALESCE(t.metadata->>'rule_code', '') = 'FOLLOWUP_STIMA_RICHIESTA'
                ) AS has_p18_followup_in_progress,
                EXISTS (
                    SELECT 1
                    FROM tasks t
                    WHERE (
                           t.lead_id = lc.id
                           OR t.stima_id IN (SELECT stima_id FROM linked_stime)
                          )
                      AND t.status = 'open'
                      AND t.due_at IS NOT NULL
                      AND t.due_at <= NOW() - INTERVAL '24 hours'
                      AND t.task_type = 'automated_followup'
                      AND t.title = 'Contattare proprietario'
                      AND COALESCE(t.metadata->>'source', '') = 'followup'
                      AND COALESCE(t.metadata->>'rule_code', '') = 'FOLLOWUP_STIMA_RICHIESTA'
                ) AS has_p18_followup_overdue,
                (
                    SELECT MAX(ste.occurred_at)
                    FROM seller_timeline_events ste
                    WHERE ste.event_type IN ('stima_richiesta', 'stima_completata')
                      AND (
                           ste.lead_id = lc.id
                           OR ste.stima_id IN (SELECT stima_id FROM linked_stime)
                      )
                ) AS latest_seller_origin_event_at
            FROM lead_ctx lc
            """,
            (lead_id, lead_id),
        )
        return _row(cur.fetchone())


def get_lead_intent_inputs_scoped(ctx, lead_id: int) -> dict[str, Any] | None:
    """P26-6A. The same score, computed only from the caller's own data.

    Scoping the lead alone would not be enough. Every EXISTS below reaches its
    rows through two paths - `ste.lead_id = lc.id` OR `ste.stima_id IN (linked
    stime)` - and the second one leaves the lead behind. Without a tenant
    predicate on that branch, another agency's timeline event or task could
    contribute to this score: no row would be returned, but the number would be
    wrong and would silently describe someone else's activity.
    """
    agency_id = ctx.require_agency()
    with seller_intent_cursor() as (_, cur):
        cur.execute(
            """
            WITH lead_ctx AS (
                SELECT l.id, l.status, l.stage
                FROM leads l
                WHERE l.id = %s AND l.agency_id = %s
            ),
            linked_stime AS (
                SELECT ls.stima_id
                FROM lead_stime ls
                JOIN leads l ON l.id = ls.lead_id
                JOIN stime s ON s.id = ls.stima_id
                WHERE ls.lead_id = %s
                  AND l.agency_id = %s
                  AND s.agency_id = %s
            )
            SELECT
                lc.id AS lead_id,
                lc.status AS lead_status,
                lc.stage AS lead_stage,
                EXISTS (
                    SELECT 1
                    FROM seller_timeline_events ste
                    WHERE ste.event_type = 'stima_completata'
                      AND ste.agency_id = %s
                      AND (
                           ste.lead_id = lc.id
                           OR ste.stima_id IN (SELECT stima_id FROM linked_stime)
                      )
                ) AS has_stima_completata,
                EXISTS (
                    SELECT 1
                    FROM tasks t
                    WHERE t.agency_id = %s
                      AND (
                           t.lead_id = lc.id
                           OR t.stima_id IN (SELECT stima_id FROM linked_stime)
                          )
                      AND t.status = 'in_progress'
                      AND t.task_type = 'automated_followup'
                      AND t.title = 'Contattare proprietario'
                      AND COALESCE(t.metadata->>'source', '') = 'followup'
                      AND COALESCE(t.metadata->>'rule_code', '') = 'FOLLOWUP_STIMA_RICHIESTA'
                ) AS has_p18_followup_in_progress,
                EXISTS (
                    SELECT 1
                    FROM tasks t
                    WHERE t.agency_id = %s
                      AND (
                           t.lead_id = lc.id
                           OR t.stima_id IN (SELECT stima_id FROM linked_stime)
                          )
                      AND t.status = 'open'
                      AND t.due_at IS NOT NULL
                      AND t.due_at <= NOW() - INTERVAL '24 hours'
                      AND t.task_type = 'automated_followup'
                      AND t.title = 'Contattare proprietario'
                      AND COALESCE(t.metadata->>'source', '') = 'followup'
                      AND COALESCE(t.metadata->>'rule_code', '') = 'FOLLOWUP_STIMA_RICHIESTA'
                ) AS has_p18_followup_overdue,
                (
                    SELECT MAX(ste.occurred_at)
                    FROM seller_timeline_events ste
                    WHERE ste.event_type IN ('stima_richiesta', 'stima_completata')
                      AND ste.agency_id = %s
                      AND (
                           ste.lead_id = lc.id
                           OR ste.stima_id IN (SELECT stima_id FROM linked_stime)
                      )
                ) AS latest_seller_origin_event_at
            FROM lead_ctx lc
            """,
            (
                lead_id, agency_id,
                lead_id, agency_id, agency_id,
                agency_id, agency_id, agency_id, agency_id,
            ),
        )
        return _row(cur.fetchone())
