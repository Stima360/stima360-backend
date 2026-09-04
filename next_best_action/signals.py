"""Read-only signal adapters for P23.

Each function below enumerates candidate subjects and returns 0+ candidate
NBA dicts (see engine.py for the exact shape expected), reading EXCLUSIVELY
through the public service/repository functions already exposed by
P17-P22 (seller_intent, core, flow, property_watch). No P17-P22 module is
modified or reimplemented here; no SQL is issued against another domain's
tables, with one documented exception (see resolve_stima_contact_lead
below).

Every function accepts a `limit` to keep V1 bounded and cheap: this module
is meant to run synchronously on refresh (see next_best_action/service.py),
not as a background job, so it deliberately never scans an entire table
without a cap.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core import repository as core_repository
from flow import adapters as flow_adapters
from flow import engine as flow_engine
from property_watch import invisible_sale_repository
from property_watch import invisible_sale_service
from seller_intent.exceptions import NotFoundError as SellerIntentNotFoundError
from seller_intent.service import get_seller_intent_score

from .database import next_best_action_cursor

DEFAULT_LIMIT = 200

# Reused verbatim from flow/rules/registry.py FLOW-R004 defaults - not a
# new business rule, the same "next action overdue" threshold FLOW already
# uses for buy_requests.
FLOW_R004_PARAMS = {"overdue_hours": 0}

# Reused verbatim from flow/rules/registry.py FLOW-R005 defaults - not a
# new business rule, the same "strong match not yet proposed" threshold
# FLOW already uses.
FLOW_R005_PARAMS = {"minimum_score": 80, "maximum_days_without_proposal": 2}


def _is_overdue(due_at: datetime | None, now: datetime) -> bool:
    """Shared, minimal "is this timestamp overdue" predicate.

    Same shape as the comparison flow/engine.py already does for
    FLOW-R004 (`due <= now - overdue_hours`, with overdue_hours=0 in V1
    here too), just factored out so collect_lead_signals below does not
    duplicate the datetime-safety handling inline. Tolerates a
    timezone-naive value (defensive only - TIMESTAMPTZ columns are
    returned tz-aware by psycopg2 in practice) by assuming UTC, so this
    never raises on a naive/aware comparison mismatch.
    """
    if due_at is None:
        return False
    if due_at.tzinfo is None:
        due_at = due_at.replace(tzinfo=timezone.utc)
    return due_at < now


def collect_lead_signals(limit: int = DEFAULT_LIMIT) -> list[dict[str, Any]]:
    """Signals #1 (follow-up scaduto), #2 (next_action scaduto, lead-side)
    and #3 (seller intent molto caldo).

    #1 and #3 come from a single call to seller_intent.service
    .get_seller_intent_score(lead_id=...) per open lead - it already
    computes has_followup_overdue (via seller_intent/repository.py's join
    against P18's tasks) and the intent band in one pass, so calling it
    once per lead (not twice) avoids a redundant query.

    #2 (lead-side next_action_overdue) reuses the SAME list_leads(...)
    call below - core.repository.list_leads already returns
    leads.next_action_at on every row, so no second query is issued.
    Unlike the buy_request branch (collect_next_action_signals), no FLOW
    rule exists for lead.next_action_at (FLOW-R004 is scoped to
    buy_request only - see flow/rules/registry.py), so the identical
    "is this due timestamp in the past" predicate FLOW-R004 already
    implements is applied directly here via _is_overdue above, without
    inventing a new business rule: it is the same frozen precedence
    category #2, just completed for its other existing subject.

    Subject: (subject_type="lead", subject_id=lead_id). A single lead can
    yield up to three candidates here (all signals can be true at once);
    engine.py picks the single winner between them using the frozen
    precedence.
    """
    leads = core_repository.list_leads(
        limit=limit, offset=0, contact_id=None, pipeline=None, stage=None, status="open"
    )
    now = datetime.now(timezone.utc)
    candidates: list[dict[str, Any]] = []
    for lead in leads:
        lead_id = lead["id"]
        try:
            score = get_seller_intent_score(lead_id=lead_id)
        except SellerIntentNotFoundError:
            # Lead vanished between the list and the score call (race) -
            # not a data error worth surfacing, just skip this subject.
            continue
        contact_id = lead.get("contact_id")
        cta_route = "contatti" if contact_id is not None else None
        cta_params = [contact_id] if contact_id is not None else []
        computed_at = score.get("computed_at")

        has_followup_overdue = any(
            flag.get("code") == "followup_overdue" for flag in score.get("operational_flags", [])
        )
        if has_followup_overdue:
            candidates.append(
                {
                    "subject_type": "lead",
                    "subject_id": lead_id,
                    "contact_id": contact_id,
                    "lead_id": lead_id,
                    "stima_id": None,
                    "source_signal": "followup_overdue",
                    "signal_at": computed_at,
                    "action_type": "contact_overdue_followup",
                    "priority": "urgent",
                    "reason": "Follow-up scaduto: contattare il venditore",
                    "cta_route": cta_route,
                    "cta_params": cta_params,
                }
            )

        next_action_at = lead.get("next_action_at")
        if _is_overdue(next_action_at, now):
            candidates.append(
                {
                    "subject_type": "lead",
                    "subject_id": lead_id,
                    "contact_id": contact_id,
                    "lead_id": lead_id,
                    "stima_id": None,
                    "source_signal": "next_action_overdue",
                    "signal_at": next_action_at,
                    "action_type": "contact_overdue_next_action",
                    "priority": "high",
                    "reason": "Prossima azione pianificata gia' scaduta",
                    "cta_route": cta_route,
                    "cta_params": cta_params,
                }
            )

        if score.get("band") == "molto_caldo":
            candidates.append(
                {
                    "subject_type": "lead",
                    "subject_id": lead_id,
                    "contact_id": contact_id,
                    "lead_id": lead_id,
                    "stima_id": None,
                    "source_signal": "seller_intent_hot",
                    "signal_at": computed_at,
                    "action_type": "contact_hot_seller",
                    "priority": "high",
                    "reason": "Seller intent molto alto: contattare il venditore",
                    "cta_route": cta_route,
                    "cta_params": cta_params,
                }
            )
    return candidates


def collect_next_action_signals(limit: int = DEFAULT_LIMIT) -> list[dict[str, Any]]:
    """Signal #2 (next_action scaduto).

    Reuses the exact existing FLOW-R004 rule (flow/adapters.py::
    scan_candidates + flow/engine.py::evaluate), read-only, instead of
    reimplementing the "next_action_at overdue" condition.

    Scoping note (documented limit, see final report): FLOW-R004 only
    scans buy_requests.next_action_at. leads.next_action_at exists as a
    column but no public scan function covers it, so V1 does not include
    lead-side next_action as a separate signal - the lead subject is
    already covered by collect_lead_signals above for its own two signals.

    Subject: (subject_type="buy_request", subject_id=buy_request_id).
    """
    pairs = flow_adapters.scan_candidates("FLOW-R004", FLOW_R004_PARAMS, limit)
    candidates: list[dict[str, Any]] = []
    for entity_type, entity_id in pairs:
        entity = flow_adapters.load_entity(entity_type, entity_id)
        matched, _reasons = flow_engine.evaluate("FLOW-R004", entity, FLOW_R004_PARAMS)
        if not matched:
            continue
        candidates.append(
            {
                "subject_type": "buy_request",
                "subject_id": entity_id,
                "contact_id": entity.get("contact_id"),
                "lead_id": entity.get("lead_id"),
                "stima_id": None,
                "source_signal": "next_action_overdue",
                "signal_at": entity.get("next_action_at"),
                "action_type": "contact_overdue_next_action",
                "priority": "high",
                "reason": "Prossima azione pianificata gia' scaduta",
                "cta_route": "acquirenti",
                "cta_params": [entity_id],
            }
        )
    return candidates


def resolve_stima_contact_lead(stima_id: int) -> tuple[int | None, int | None]:
    """Resolve (contact_id, lead_id) for a stima_id via CORE's own
    lead_stime linking table.

    DOCUMENTED EXCEPTION to "no direct SQL on other domains' tables": no
    public repository/service function in any module exposes this exact
    lookup (stima_id -> linked lead's contact_id). This performs the same
    read-only join seller_intent/repository.py::get_lead_intent_inputs
    already relies on (lead_stime + leads), scoped to a single, minimal,
    read-only SELECT. It is needed only to build the invisible-sale CTA
    (there is no dedicated "stima" view in the OS - the CTA must resolve
    to the linked contact's own view, see static/os_shell/assets/views/
    contatto-dettaglio.js). If no lead is linked, both values are None and
    the caller must render the NBA without a CTA rather than fail.
    """
    with next_best_action_cursor() as (_, cur):
        cur.execute(
            """
            SELECT l.id AS lead_id, l.contact_id AS contact_id
            FROM lead_stime ls
            JOIN leads l ON l.id = ls.lead_id
            WHERE ls.stima_id = %s
            ORDER BY ls.created_at, ls.id
            LIMIT 1
            """,
            (stima_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None, None
        return row["contact_id"], row["lead_id"]


def collect_invisible_sale_signals(limit: int = DEFAULT_LIMIT) -> list[dict[str, Any]]:
    """Signal #4 (vendita invisibile ready).

    Enumerates active Property Watches via property_watch.
    invisible_sale_repository.list_active_watch_refs() (public), then reads
    each opportunity via property_watch.invisible_sale_service.
    get_invisible_sale_for_stima() (public) - never touches
    invisible_sale_* tables directly.

    Subject: (subject_type="stima", subject_id=stima_id).
    """
    watch_refs = invisible_sale_repository.list_active_watch_refs()[:limit]
    candidates: list[dict[str, Any]] = []
    for ref in watch_refs:
        stima_id = ref["stima_id"]
        state = invisible_sale_service.get_invisible_sale_for_stima(stima_id)
        if state.get("status") != "ready":
            continue
        pending = [c for c in state.get("candidates", []) if c.get("status") == "pending_review"]
        if not pending:
            continue
        contact_id, lead_id = resolve_stima_contact_lead(stima_id)
        cta_route = "contatti" if contact_id is not None else None
        cta_params = [contact_id] if contact_id is not None else []
        last_activity_values = [c.get("last_activity_at") for c in pending if c.get("last_activity_at")]
        signal_at: datetime | None = max(last_activity_values) if last_activity_values else None
        candidates.append(
            {
                "subject_type": "stima",
                "subject_id": stima_id,
                "contact_id": contact_id,
                "lead_id": lead_id,
                "stima_id": stima_id,
                "source_signal": "invisible_sale_ready",
                "signal_at": signal_at,
                "action_type": "review_invisible_sale",
                "priority": "high",
                "reason": f"Vendita invisibile pronta: {len(pending)} candidato/i da valutare",
                "cta_route": cta_route,
                "cta_params": cta_params,
            }
        )
    return candidates


def collect_match_signals(limit: int = DEFAULT_LIMIT) -> list[dict[str, Any]]:
    """Signal #5 (match forte non proposto).

    Reuses the exact existing FLOW-R005 rule (flow/adapters.py::
    scan_candidates + flow/engine.py::evaluate), read-only, instead of
    reimplementing the "strong fresh match not yet proposed" condition.

    Subject: (subject_type="match", subject_id=match_id).
    """
    pairs = flow_adapters.scan_candidates("FLOW-R005", FLOW_R005_PARAMS, limit)
    candidates: list[dict[str, Any]] = []
    for entity_type, entity_id in pairs:
        entity = flow_adapters.load_entity(entity_type, entity_id)
        matched, _reasons = flow_engine.evaluate("FLOW-R005", entity, FLOW_R005_PARAMS)
        if not matched:
            continue
        signal_at = (
            entity.get("first_matched_at")
            or entity.get("created_at")
            or entity.get("last_calculated_at")
        )
        candidates.append(
            {
                "subject_type": "match",
                "subject_id": entity_id,
                "contact_id": entity.get("contact_id"),
                "lead_id": entity.get("lead_id"),
                "stima_id": None,
                "source_signal": "match_strong_unproposed",
                "signal_at": signal_at,
                "action_type": "propose_strong_match",
                "priority": "normal",
                "reason": "Match forte non ancora proposto all'acquirente",
                "cta_route": "abbinamenti",
                "cta_params": [entity_id],
            }
        )
    return candidates


def collect_all_signals(limit: int = DEFAULT_LIMIT) -> list[dict[str, Any]]:
    """Collect all five V1 signals in one call, grouped by nothing - the
    caller (service.py) is responsible for grouping by (subject_type,
    subject_id) before handing each group to engine.select_winner."""
    return (
        collect_lead_signals(limit)
        + collect_next_action_signals(limit)
        + collect_invisible_sale_signals(limit)
        + collect_match_signals(limit)
    )
