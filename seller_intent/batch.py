"""VENDITORI-1 - seller intent for a PAGE of leads, in one statement.

A separate module on purpose: `seller_intent/repository.py` and `service.py`
stay byte-for-byte as they are (the LMC phases guard them), and this file only
ADDS a batch reader next to them. The scoring is not reimplemented: the rows
go through the same `_score_from_inputs` -> `scoring.compute_score` as the
single-lead path, and `tests/test_venditori_1_postgres.py::test_i03` proves the
scores are identical to `get_seller_intent_score_scoped`, lead by lead.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .service import _score_from_inputs


def get_lead_intent_inputs_scoped_many(cur, agency_id: int, lead_ids: list[int]) -> dict[int, dict[str, Any]]:
    """VENDITORI-1. The inputs of `get_lead_intent_inputs_scoped`, for a whole
    page of leads in ONE statement, on the caller's cursor.

    Same predicates, same tenant rules, row for row: `linked_stime` becomes a
    subquery correlated to each lead instead of a CTE for one lead, and every
    EXISTS keeps both paths (`lead_id` OR a linked stima) under `agency_id`.
    `tests/test_venditori_1_postgres.py::test_i03` proves the scores equal the
    single-lead ones. The single-lead function in repository.py is untouched.
    """
    if not lead_ids:
        return {}
    cur.execute(
        """
        WITH lead_ctx AS (
            SELECT l.id, l.status, l.stage
            FROM leads l
            WHERE l.id = ANY(%s) AND l.agency_id = %s
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
                       OR ste.stima_id IN (SELECT ls.stima_id FROM lead_stime ls JOIN stime s ON s.id = ls.stima_id
                                            WHERE ls.lead_id = lc.id AND s.agency_id = %s)
                  )
            ) AS has_stima_completata,
            EXISTS (
                SELECT 1
                FROM tasks t
                WHERE t.agency_id = %s
                  AND (
                       t.lead_id = lc.id
                       OR t.stima_id IN (SELECT ls.stima_id FROM lead_stime ls JOIN stime s ON s.id = ls.stima_id
                                          WHERE ls.lead_id = lc.id AND s.agency_id = %s)
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
                       OR t.stima_id IN (SELECT ls.stima_id FROM lead_stime ls JOIN stime s ON s.id = ls.stima_id
                                          WHERE ls.lead_id = lc.id AND s.agency_id = %s)
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
                       OR ste.stima_id IN (SELECT ls.stima_id FROM lead_stime ls JOIN stime s ON s.id = ls.stima_id
                                            WHERE ls.lead_id = lc.id AND s.agency_id = %s)
                  )
            ) AS latest_seller_origin_event_at
        FROM lead_ctx lc
        """,
        (list(lead_ids), agency_id, agency_id, agency_id, agency_id, agency_id, agency_id, agency_id,
         agency_id, agency_id),
    )
    return {int(r["lead_id"]): dict(r) for r in cur.fetchall()}


def get_seller_intent_scores_scoped_many(cur, ctx, lead_ids, *, now_utc: datetime | None = None) -> dict[int, dict]:
    """VENDITORI-1. The scoped score of a page of leads, one statement, on the
    caller's cursor; the same `_score_from_inputs` as the single-lead path."""
    inputs = get_lead_intent_inputs_scoped_many(cur, ctx.require_agency(), lead_ids)
    istante = now_utc or datetime.now(timezone.utc)
    return {lead_id: _score_from_inputs(row, now_utc=istante) for lead_id, row in inputs.items()}
