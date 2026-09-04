"""Persistence for the P23 next_best_actions materialized table.

This is a pure read model: every row here is a cache of a decision already
made by next_best_action/engine.py from signals already produced by
P17-P22. No business logic lives here - only UPSERT/prune and reads.
"""

from __future__ import annotations

from typing import Any

from psycopg2.extras import Json

from .database import next_best_action_cursor
from .engine import rank_for_display


def _row(row: Any) -> dict[str, Any] | None:
    return dict(row) if row else None


def replace_current_actions(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Atomically replace the materialized set with `rows` (the full,
    freshly recomputed set of current winners across all subjects).

    Idempotent: running this twice with the same `rows` content produces
    the same table content (UPSERT with no-op update when values match,
    same surviving row set). Any existing row whose (subject_type,
    subject_id) is not present in `rows` is deleted - this is how an
    obsolete NBA (its source signal no longer holds) gets invalidated,
    with no separate status/history column needed for V1.
    """
    created = 0
    updated = 0
    with next_best_action_cursor(commit=True) as (_, cur):
        desired_keys = {(r["subject_type"], r["subject_id"]) for r in rows}

        cur.execute("SELECT subject_type, subject_id FROM next_best_actions")
        existing_keys = {(r["subject_type"], r["subject_id"]) for r in cur.fetchall()}

        for r in rows:
            cur.execute(
                """
                INSERT INTO next_best_actions (
                    subject_type, subject_id, contact_id, lead_id, stima_id,
                    action_type, priority, reason, source_signal,
                    cta_route, cta_params, generated_at, valid_until
                ) VALUES (
                    %(subject_type)s, %(subject_id)s, %(contact_id)s, %(lead_id)s, %(stima_id)s,
                    %(action_type)s, %(priority)s, %(reason)s, %(source_signal)s,
                    %(cta_route)s, %(cta_params)s, %(generated_at)s, %(valid_until)s
                )
                ON CONFLICT (subject_type, subject_id) DO UPDATE SET
                    contact_id = EXCLUDED.contact_id,
                    lead_id = EXCLUDED.lead_id,
                    stima_id = EXCLUDED.stima_id,
                    action_type = EXCLUDED.action_type,
                    priority = EXCLUDED.priority,
                    reason = EXCLUDED.reason,
                    source_signal = EXCLUDED.source_signal,
                    cta_route = EXCLUDED.cta_route,
                    cta_params = EXCLUDED.cta_params,
                    generated_at = EXCLUDED.generated_at,
                    valid_until = EXCLUDED.valid_until,
                    updated_at = NOW()
                """,
                {
                    "subject_type": r["subject_type"],
                    "subject_id": r["subject_id"],
                    "contact_id": r.get("contact_id"),
                    "lead_id": r.get("lead_id"),
                    "stima_id": r.get("stima_id"),
                    "action_type": r["action_type"],
                    "priority": r["priority"],
                    "reason": r["reason"],
                    "source_signal": r["source_signal"],
                    "cta_route": r.get("cta_route"),
                    "cta_params": Json(r.get("cta_params") or []),
                    "generated_at": r["generated_at"],
                    "valid_until": r.get("valid_until"),
                },
            )
            if (r["subject_type"], r["subject_id"]) in existing_keys:
                updated += 1
            else:
                created += 1

        removed_keys = existing_keys - desired_keys
        removed = 0
        for subject_type, subject_id in removed_keys:
            cur.execute(
                "DELETE FROM next_best_actions WHERE subject_type = %s AND subject_id = %s",
                (subject_type, subject_id),
            )
            removed += 1

        return {"created": created, "updated": updated, "removed": removed}


def list_current(limit: int) -> list[dict[str, Any]]:
    """Ranking-globale read (section 9 / 5.B): orders the already-decided,
    one-per-subject NBAs for the OGGI view. This is a DIFFERENT concern
    from engine.select_winner - here every row is already a chosen winner,
    this only orders them for display."""
    with next_best_action_cursor() as (_, cur):
        cur.execute("SELECT * FROM next_best_actions")
        rows = [_row(row) for row in cur.fetchall()]
    rows.sort(key=lambda r: (rank_for_display(r["priority"]), -r["generated_at"].timestamp(), r["subject_id"]))
    return rows[:limit]


def get_current(subject_type: str, subject_id: int) -> dict[str, Any] | None:
    with next_best_action_cursor() as (_, cur):
        cur.execute(
            "SELECT * FROM next_best_actions WHERE subject_type = %s AND subject_id = %s",
            (subject_type, subject_id),
        )
        return _row(cur.fetchone())
