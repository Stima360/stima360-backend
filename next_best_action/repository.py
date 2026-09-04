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


def _subject_label_from_contact(
    contact_type: str | None,
    display_name: str | None,
    first_name: str | None,
    last_name: str | None,
    company_name: str | None,
) -> str | None:
    """Same 3-tier fallback already used elsewhere for a contact's display
    label (core/service.py::create_contact, static/os_shell/assets/
    components/contact-picker.js::contactLabel): display_name first, then
    company_name for a company contact, then first_name+last_name for a
    person. Returns None (never a synthetic string) when nothing usable is
    available - the caller (OGGI, oggi.js::nbaSubjectLabel) already owns the
    "{Type} #{id}" fallback formatting and must keep doing so."""
    if display_name:
        return display_name
    if contact_type == "company":
        return company_name or None
    parts = [p for p in (first_name, last_name) if p]
    return " ".join(parts) if parts else None


def _row(row: Any) -> dict[str, Any] | None:
    """P25.7 (Gap C fix): enriches the raw next_best_actions row with an
    additive `subject_label` field, computed from the contact columns the
    dynamic LEFT JOIN in list_current/get_current adds to the SELECT below
    (prefixed `_contact_` so they never collide with a real
    next_best_actions column). This is a pure read-model enrichment - no
    migration, no new table, no change to next_best_action/engine.py's
    ranking/precedence/eligibility (frozen P23 business logic, untouched).
    """
    if not row:
        return None
    data = dict(row)
    contact_type = data.pop("_contact_type", None)
    display_name = data.pop("_contact_display_name", None)
    first_name = data.pop("_contact_first_name", None)
    last_name = data.pop("_contact_last_name", None)
    company_name = data.pop("_contact_company_name", None)
    data["subject_label"] = _subject_label_from_contact(
        contact_type, display_name, first_name, last_name, company_name
    )
    return data


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


# P25.7 (Gap C fix): dynamic LEFT JOIN on contacts at read time - no
# migration, no denormalized column added to next_best_actions. A single
# query per call (no N+1: the join happens once, not per row). contact_id
# is already a real column on next_best_actions (populated by
# signals.py/service.py for lead/buy_request/stima/match subjects where
# available - see replace_current_actions above); when it's NULL or the
# contact has no usable name, the LEFT JOIN columns simply come back NULL
# and _row()/_subject_label_from_contact() produce subject_label=None,
# which the frontend already knows how to fall back on.
_SELECT_WITH_CONTACT_LABEL = """
    SELECT nba.*,
           c.contact_type AS _contact_type,
           c.display_name AS _contact_display_name,
           c.first_name AS _contact_first_name,
           c.last_name AS _contact_last_name,
           c.company_name AS _contact_company_name
    FROM next_best_actions nba
    LEFT JOIN contacts c ON c.id = nba.contact_id
"""


def list_current(limit: int) -> list[dict[str, Any]]:
    """Ranking-globale read (section 9 / 5.B): orders the already-decided,
    one-per-subject NBAs for the OGGI view. This is a DIFFERENT concern
    from engine.select_winner - here every row is already a chosen winner,
    this only orders them for display."""
    with next_best_action_cursor() as (_, cur):
        cur.execute(_SELECT_WITH_CONTACT_LABEL)
        rows = [_row(row) for row in cur.fetchall()]
    rows.sort(key=lambda r: (rank_for_display(r["priority"]), -r["generated_at"].timestamp(), r["subject_id"]))
    return rows[:limit]


def get_current(subject_type: str, subject_id: int) -> dict[str, Any] | None:
    with next_best_action_cursor() as (_, cur):
        cur.execute(
            f"{_SELECT_WITH_CONTACT_LABEL} WHERE nba.subject_type = %s AND nba.subject_id = %s",
            (subject_type, subject_id),
        )
        return _row(cur.fetchone())
