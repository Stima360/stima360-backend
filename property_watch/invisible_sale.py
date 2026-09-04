"""Pure P22 Invisible Sale candidate calculation."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
from typing import Any

from match import engine as match_engine
from match.readiness import buy_readiness


P22_ALGORITHM_VERSION = "invisible-sale-1.0"
SCORE_QUANTUM = Decimal("0.01")
REASON_CODE_ORDER = (
    "location", "budget", "typology", "dimensions", "rooms", "features", "condition",
)


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    try:
        value = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return value if value.is_finite() else None


def _canonical_decimal(value: Any, *, non_negative: bool = False) -> Decimal:
    result = _decimal(value)
    if result is None or (non_negative and result < 0):
        raise ValueError("invalid decimal")
    result = result.quantize(SCORE_QUANTUM, rounding=ROUND_HALF_UP)
    return Decimal("0.00") if result.is_zero() else result


def build_ephemeral_property(baseline: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(baseline, dict):
        return None
    text = {
        "city": baseline.get("comune"),
        "microzone": baseline.get("microzona"),
        "property_type": baseline.get("tipologia"),
    }
    if any(not isinstance(value, str) or not value.strip() for value in text.values()):
        return None
    surface = _decimal(baseline.get("mq"))
    price = _decimal(baseline.get("price_exact"))
    if surface is None or price is None or surface <= 0 or price <= 0:
        return None
    return {
        **{key: value.strip() for key, value in text.items()},
        "surface_sqm": surface,
        "asking_price": price,
    }


def _utc_timestamp(value: Any) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("last_activity_at must be timezone-aware")
    return value.astimezone(timezone.utc)


def _budget_reference(buy: dict[str, Any]) -> Decimal | None:
    for key in ("budget_target", "budget_max", "budget_min"):
        if buy.get(key) is not None:
            return _canonical_decimal(buy[key], non_negative=True)
    return None


def _reason_codes(criteria: Any) -> list[str]:
    if not isinstance(criteria, list) or not all(isinstance(item, dict) for item in criteria):
        raise ValueError("criteria must be a list of mappings")
    found = set()
    for item in criteria:
        group = item.get("criterion_group")
        if group not in REASON_CODE_ORDER or item.get("is_blocking") is True:
            continue
        if item.get("result") in {"not_applicable", "not_available"}:
            continue
        score = _decimal(item.get("score"))
        if score is not None and score >= Decimal("85.00"):
            found.add(group)
    return [code for code in REASON_CODE_ORDER if code in found]


def canonicalize_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(candidate, dict):
        raise ValueError("candidate must be a mapping")
    buy_id = candidate.get("buy_request_id")
    if not isinstance(buy_id, int) or isinstance(buy_id, bool) or buy_id < 1:
        raise ValueError("buy_request_id must be a positive integer")
    score = _canonical_decimal(candidate.get("score_total"), non_negative=True)
    if score > 100:
        raise ValueError("score_total must not exceed 100")
    compatibility = candidate.get("compatibility_status")
    if compatibility not in {"compatible", "exception", "incompatible"}:
        raise ValueError("invalid compatibility_status")
    reasons = candidate.get("reason_codes")
    if not isinstance(reasons, list) or any(reason not in REASON_CODE_ORDER for reason in reasons):
        raise ValueError("invalid reason_codes")
    activity = _utc_timestamp(candidate.get("last_activity_at"))
    version = candidate.get("match_algorithm_version")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("match_algorithm_version is required")
    budget = candidate.get("budget_reference")
    budget = None if budget is None else _canonical_decimal(budget, non_negative=True)
    return {
        "buy_request_id": buy_id,
        "score_total": format(score, ".2f"),
        "compatibility_status": compatibility,
        "reason_codes": [code for code in REASON_CODE_ORDER if code in reasons],
        "last_activity_at": activity.isoformat().replace("+00:00", "Z"),
        "budget_reference": None if budget is None else format(budget, ".2f"),
        "match_algorithm_version": version,
    }


def _sha256(value: dict[str, Any]) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(raw).hexdigest()


def candidate_digest(candidate: dict[str, Any]) -> str:
    return _sha256(canonicalize_candidate(candidate))


def candidate_set_digest(candidates: list[dict[str, Any]]) -> str:
    return _sha256({
        "algorithm_version": P22_ALGORITHM_VERSION,
        "candidates": [canonicalize_candidate(item) for item in candidates],
    })


def calculate_candidates(
    buys: list[dict[str, Any]], ephemeral_property: dict[str, Any]
) -> list[dict[str, Any]]:
    candidates = []
    for buy in buys:
        if not buy_readiness(buy)["can_match"]:
            continue
        result = match_engine.calculate(buy, ephemeral_property)
        if not isinstance(result, dict):
            raise ValueError("invalid MATCH result")
        hard_fails = result.get("hard_fail_count")
        score = _decimal(result.get("score_total"))
        compatibility = result.get("compatibility_status")
        version = result.get("algorithm_version")
        if (
            not isinstance(hard_fails, int) or isinstance(hard_fails, bool) or hard_fails < 0
            or score is None or score < 0 or score > 100
            or compatibility not in {"compatible", "exception", "incompatible"}
            or not isinstance(version, str) or not version.strip()
        ):
            raise ValueError("invalid MATCH result")
        reasons = _reason_codes(result.get("criteria"))
        score = _canonical_decimal(score, non_negative=True)
        if hard_fails or compatibility == "incompatible" or score < Decimal("80.00"):
            continue
        candidate = {
            "buy_request_id": buy.get("id"),
            "score_total": score,
            "compatibility_status": compatibility,
            "reason_codes": reasons,
            "last_activity_at": _utc_timestamp(buy.get("last_activity_at")),
            "budget_reference": _budget_reference(buy),
            "match_algorithm_version": version,
        }
        candidate["candidate_digest"] = candidate_digest(candidate)
        candidates.append(candidate)
    return sorted(
        candidates,
        key=lambda item: (-item["score_total"], -item["last_activity_at"].timestamp(), item["buy_request_id"]),
    )
