"""P22 collector and review service boundary."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from . import invisible_sale, invisible_sale_repository as repository

logger = logging.getLogger(__name__)


def _valid_stima_id(stima_id: int) -> None:
    if not isinstance(stima_id, int) or isinstance(stima_id, bool) or stima_id < 1:
        raise ValueError("stima_id must be a positive integer")


def collect_invisible_sale_for_stima(stima_id: int) -> dict[str, Any]:
    _valid_stima_id(stima_id)
    context = repository.get_watch_and_baseline_for_stima(stima_id)
    if context is None:
        raise LookupError("property watch not found")
    baseline = context["baseline"]
    property_candidate = invisible_sale.build_ephemeral_property(
        baseline.get("payload", {}) if baseline else {}
    )
    if property_candidate is None:
        return {"status": "baseline_unavailable", "watch_id": context["watch"]["id"]}
    candidates = invisible_sale.calculate_candidates(
        repository.list_eligible_buy_snapshot(), property_candidate
    )
    return repository.persist_invisible_sale_refresh(
        context["watch"]["id"], candidates, invisible_sale.candidate_set_digest(candidates),
        datetime.now(timezone.utc),
    )


def safe_collect_invisible_sale_for_stima(stima_id: int) -> dict[str, Any]:
    try:
        return collect_invisible_sale_for_stima(stima_id)
    except (ValueError, LookupError):
        raise
    except Exception as exc:
        logger.error("invisible_sale_collection_failed stima_id=%s error_type=%s", stima_id, type(exc).__name__)
        return {"status": "failed", "watch_id": None}


def collect_invisible_sale_for_active_watches() -> dict[str, Any]:
    outcomes = []
    for watch in repository.list_active_watch_refs():
        try:
            outcome = safe_collect_invisible_sale_for_stima(watch["stima_id"])
        except (ValueError, LookupError):
            outcome = {"status": "failed", "watch_id": watch["watch_id"]}
        outcomes.append({"stima_id": watch["stima_id"], **outcome})
    totals = {key: 0 for key in ("written", "unchanged", "baseline_unavailable", "closed", "failed")}
    for outcome in outcomes:
        totals[outcome["status"] if outcome["status"] in totals else "failed"] += 1
    return {"processed": len(outcomes), "outcomes": outcomes, "totals": totals}


def get_invisible_sale_for_stima(stima_id: int) -> dict[str, Any]:
    _valid_stima_id(stima_id)
    return repository.get_invisible_sale_for_stima(stima_id)


def approve_invisible_sale_candidate(stima_id: int, buy_request_id: int) -> dict[str, Any]:
    return repository.set_candidate_review_status(stima_id, buy_request_id, "approved")


def reject_invisible_sale_candidate(stima_id: int, buy_request_id: int) -> dict[str, Any]:
    return repository.set_candidate_review_status(stima_id, buy_request_id, "rejected")


def close_invisible_sale(stima_id: int) -> dict[str, Any]:
    return repository.close_invisible_sale_for_stima(stima_id)
