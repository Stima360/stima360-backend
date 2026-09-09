"""P24 - orchestration for Seller Database Revival.

ensure_today_batch() is the only WRITE entry point of this module: it
creates/tops-up today's revival batch (up to DAILY_CAP contacts) inside a
single locked transaction. collect_today_signals() (added by Task 4) is
the only READ entry point consumed by next_best_action/signals.py.

Neither function ever creates a CORE task, never sends a communication,
and never touches any table outside seller_revival_suppressions (write)
and the read-only tables eligibility.py queries.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from . import eligibility, repository
from .database import database_revival_cursor

logger = logging.getLogger(__name__)

DAILY_CAP = 20


def ensure_today_batch(now: datetime | None = None) -> dict[str, int]:
    """Idempotent, top-up daily batch selection (P24 Design Closure
    report, section "Definitive ensure_today_batch algorithm"):

      1. acquire the fixed-scope advisory lock (serializes concurrent
         refreshes - see repository.acquire_daily_batch_lock);
      2. count today's batch so far;
      3. compute remaining slots (DAILY_CAP - count); if none, no-op;
      4. exclude contacts currently in cooldown (active or already in
         today's batch - both share the same expires_at > NOW() check);
      5. select up to `remaining_slots` fresh eligible candidates, ordered
         by the frozen tie-break (eligibility.find_eligible_candidates);
      6. UPSERT one suppression row per candidate (reusing an expired row
         if present - repository.upsert_batch_row), counting only rows
         actually written (rowcount-based) as "added".

    Never raises for a "no eligible candidates" or "cap already reached"
    outcome - those are normal results, not errors. Real exceptions
    (connection failure, etc.) propagate to the caller; use
    safe_ensure_today_batch() from next_best_action/service.py::refresh().
    """
    with database_revival_cursor(commit=True) as (_, cur):
        repository.acquire_daily_batch_lock(cur)

        batch_count_today = repository.count_batch_today(cur)
        remaining_slots = DAILY_CAP - batch_count_today
        if remaining_slots <= 0:
            return {"added": 0, "batch_size_today": batch_count_today}

        exclude_contact_ids = repository.get_cooldown_contact_ids(cur)
        candidates = eligibility.find_eligible_candidates(
            cur, exclude_contact_ids=exclude_contact_ids, limit=remaining_slots
        )

        added = 0
        for candidate in candidates:
            written = repository.upsert_batch_row(
                cur,
                contact_id=candidate["contact_id"],
                lead_id=candidate.get("lead_id"),
            )
            if written:
                added += 1

        return {"added": added, "batch_size_today": batch_count_today + added}


def collect_today_signals(now: datetime | None = None) -> list[dict[str, Any]]:
    """Read-only signal adapter consumed by
    next_best_action.signals.collect_database_revival_signals. Never
    writes anything (no lock, no upsert) - it only reads today's batch
    (repository.list_batch_today) and live-revalidates each row against
    the full eligibility predicate minus the cooldown clause
    (eligibility.is_still_eligible), per the P24 Design Closure report's
    "Invalidation live" rule: a row that no longer passes disappears from
    OGGI immediately, but its suppression row is left untouched (the
    cooldown still applies).

    A row whose lead_id is NULL (the lead was deleted after the contact
    entered the batch - lead_id is ON DELETE SET NULL) is skipped without
    even calling is_still_eligible: there is no lead left to attach an NBA
    subject_id to.
    """
    with database_revival_cursor() as (_, cur):
        batch_rows = repository.list_batch_today(cur)
        candidates: list[dict[str, Any]] = []
        for row in batch_rows:
            contact_id = row["contact_id"]
            lead_id = row.get("lead_id")
            if lead_id is None:
                continue
            if not eligibility.is_still_eligible(cur, contact_id=contact_id, lead_id=lead_id):
                continue
            candidates.append(
                {
                    "subject_type": "lead",
                    "subject_id": lead_id,
                    "contact_id": contact_id,
                    "lead_id": lead_id,
                    "stima_id": None,
                    "source_signal": "database_revival",
                    "signal_at": row.get("created_at"),
                    "action_type": "contact_dormant_seller",
                    "priority": "normal",
                    "reason": "Seller dormiente riattivabile",
                    "cta_route": "contatti",
                    "cta_params": [contact_id],
                }
            )
        return candidates


def safe_ensure_today_batch(now: datetime | None = None) -> dict[str, int] | None:
    """Never-raising wrapper around ensure_today_batch(), same pattern as
    followup.service.safe_run_followup and
    seller_intelligence.service.safe_record_event. This is the ONLY
    function next_best_action/service.py::refresh() is allowed to call: an
    exception here must never break the other five P23 signals."""
    try:
        return ensure_today_batch(now=now)
    except Exception as exc:  # noqa: BLE001 - intentional catch-all, see docstring
        logger.error(
            "database_revival_ensure_today_batch_failed error_type=%s error=%s",
            type(exc).__name__,
            exc,
        )
        return None


# ---------------------------------------------------------------------------
# P26-6B agency scoping.
#
# DAILY_CAP is now twenty per agency, not twenty for STIMA360. Counted globally
# it was a shared resource with no owner: whichever tenant refreshed first that
# day consumed the slots, and the others silently got fewer revivals with no
# error and nothing to see. The cap only means anything if the count it compares
# against is the caller's own.
#
# The advisory lock is per-agency for the same reason in reverse: correctness
# never depended on it being global, but a single fixed key made every tenant's
# batch wait on every other's.
# ---------------------------------------------------------------------------

def ensure_today_batch_for_agency(agency_id: int, now: datetime | None = None) -> dict[str, int]:
    """One agency's daily batch: the same six steps, bounded to one tenant.

    The result carries `agency_id`, which the ctx-less twin does not. That is
    safe here and deliberately not done in the property-watch batches: this
    module has no router and its only caller ignores the return value, whereas
    those results are an asserted contract. The key exists because
    `ensure_today_batch_for_all_agencies` returns a list of runs, and a run
    that does not say whose it is cannot be read.
    """
    with database_revival_cursor(commit=True) as (_, cur):
        repository.acquire_daily_batch_lock_for_agency(cur, agency_id)

        batch_count_today = repository.count_batch_today_for_agency(cur, agency_id)
        remaining_slots = DAILY_CAP - batch_count_today
        if remaining_slots <= 0:
            return {
                "agency_id": agency_id,
                "added": 0,
                "batch_size_today": batch_count_today,
            }

        exclude_contact_ids = repository.get_cooldown_contact_ids_for_agency(cur, agency_id)
        candidates = eligibility.find_eligible_candidates_for_agency(
            cur,
            agency_id=agency_id,
            exclude_contact_ids=exclude_contact_ids,
            limit=remaining_slots,
        )

        added = 0
        for candidate in candidates:
            written = repository.upsert_batch_row_for_agency(
                cur,
                contact_id=candidate["contact_id"],
                lead_id=candidate.get("lead_id"),
                agency_id=agency_id,
            )
            if written:
                added += 1

        return {
            "agency_id": agency_id,
            "added": added,
            "batch_size_today": batch_count_today + added,
        }


def ensure_today_batch_scoped(ctx, now: datetime | None = None) -> dict[str, int]:
    return ensure_today_batch_for_agency(ctx.require_agency(), now=now)


def safe_ensure_today_batch_scoped(ctx, now: datetime | None = None) -> dict[str, int] | None:
    """Never-raising, like its ctx-less twin: a P24 failure must not stop the
    other five P23 signals from refreshing."""
    try:
        return ensure_today_batch_scoped(ctx, now=now)
    except Exception as exc:  # noqa: BLE001 - intentional catch-all
        logger.error(
            "database_revival_ensure_today_batch_failed error_type=%s error=%s",
            type(exc).__name__, exc,
        )
        return None


def ensure_today_batch_for_all_agencies(now: datetime | None = None) -> dict[str, Any]:
    """Server-only orchestrator: one bounded batch per active agency.

    Deliberately a loop over tenants rather than one global pass, so that no
    statement in this module ever runs without an agency predicate - and so
    that each agency gets its own twenty slots and its own lock.
    """
    with database_revival_cursor() as (_, cur):
        agency_ids = repository.list_active_agency_ids(cur)
    runs = [ensure_today_batch_for_agency(agency_id, now=now) for agency_id in agency_ids]
    return {
        "agencies": len(runs),
        "added": sum(run["added"] for run in runs),
        "runs": runs,
    }


def collect_today_signals_for_agency(agency_id: int, now: datetime | None = None) -> list[dict[str, Any]]:
    """Today's batch for one agency, live-revalidated inside that agency."""
    with database_revival_cursor() as (_, cur):
        batch_rows = repository.list_batch_today_for_agency(cur, agency_id)
        candidates: list[dict[str, Any]] = []
        for row in batch_rows:
            contact_id = row["contact_id"]
            lead_id = row.get("lead_id")
            if lead_id is None:
                continue
            if not eligibility.is_still_eligible_for_agency(
                cur, agency_id=agency_id, contact_id=contact_id, lead_id=lead_id
            ):
                continue
            candidates.append(
                {
                    "subject_type": "lead",
                    "subject_id": lead_id,
                    "contact_id": contact_id,
                    "lead_id": lead_id,
                    "stima_id": None,
                    "source_signal": "database_revival",
                    "signal_at": row.get("created_at"),
                    "action_type": "contact_dormant_seller",
                    "priority": "normal",
                    "reason": "Seller dormiente riattivabile",
                    "cta_route": "contatti",
                    "cta_params": [contact_id],
                }
            )
        return candidates


def collect_today_signals_scoped(ctx, now: datetime | None = None) -> list[dict[str, Any]]:
    return collect_today_signals_for_agency(ctx.require_agency(), now=now)
