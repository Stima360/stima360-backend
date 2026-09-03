"""Pure P21-A Buyer Pressure calculation over Property Watch inputs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
from typing import Any

from match.engine import calculate as calculate_match
from match.enums import ALGORITHM_VERSION
from match.readiness import buy_readiness


METRIC_KEYS = (
    "evaluated_buyers",
    "compatible_buyers",
    "highly_compatible_buyers",
    "recent_compatible_buyers_30d",
    "average_match_score",
    "maximum_match_score",
    "average_budget",
    "algorithm_version",
)
COUNT_KEYS = METRIC_KEYS[:4]
DECIMAL_KEYS = METRIC_KEYS[4:7]
TWO_PLACES = Decimal("0.01")


def _finite_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def build_ephemeral_property(
    baseline_payload: dict[str, Any],
) -> dict[str, Any] | None:
    if not isinstance(baseline_payload, dict):
        return None
    strings = {
        "city": baseline_payload.get("comune"),
        "microzone": baseline_payload.get("microzona"),
        "property_type": baseline_payload.get("tipologia"),
    }
    if any(not isinstance(value, str) or not value.strip() for value in strings.values()):
        return None
    surface = _finite_decimal(baseline_payload.get("mq"))
    price = _finite_decimal(baseline_payload.get("price_exact"))
    if surface is None or surface <= 0 or price is None or price <= 0:
        return None
    return {
        **strings,
        "surface_sqm": surface,
        "asking_price": price,
    }


def empty_metrics() -> dict[str, Any]:
    return {
        "evaluated_buyers": 0,
        "compatible_buyers": 0,
        "highly_compatible_buyers": 0,
        "recent_compatible_buyers_30d": 0,
        "average_match_score": None,
        "maximum_match_score": None,
        "average_budget": None,
        "algorithm_version": ALGORITHM_VERSION,
    }


def canonicalize_metrics(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != set(METRIC_KEYS):
        raise ValueError("buyer pressure metrics have an invalid key set")
    canonical: dict[str, Any] = {}
    for key in COUNT_KEYS:
        value = payload[key]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"{key} must be a non-negative integer")
        canonical[key] = value
    for key in DECIMAL_KEYS:
        value = payload[key]
        if value is None:
            canonical[key] = None
            continue
        decimal_value = _finite_decimal(value)
        if (
            decimal_value is None
            or (decimal_value < 0 and not decimal_value.is_zero())
        ):
            raise ValueError(f"{key} must be finite and non-negative")
        normalized = decimal_value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        canonical[key] = Decimal("0.00") if normalized.is_zero() else normalized
    version = payload["algorithm_version"]
    if not isinstance(version, str) or not version:
        raise ValueError("algorithm_version is required")
    canonical["algorithm_version"] = version
    return canonical


def metrics_digest(payload: dict[str, Any]) -> str:
    canonical = canonicalize_metrics(payload)
    serializable = {
        key: format(value, ".2f") if isinstance(value, Decimal) else value
        for key, value in canonical.items()
    }
    raw = json.dumps(
        serializable,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _mean(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    return (sum(values, Decimal("0")) / Decimal(len(values))).quantize(
        TWO_PLACES, rounding=ROUND_HALF_UP
    )


def _last_activity_at(buy: dict[str, Any]) -> datetime:
    value = buy.get("last_activity_at")
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("last_activity_at must be timezone-aware")
    return value.astimezone(timezone.utc)


def _selected_budget(buy: dict[str, Any]) -> Decimal | None:
    for key in ("budget_target", "budget_max", "budget_min"):
        if buy.get(key) is not None:
            value = _finite_decimal(buy[key])
            if value is None or value < 0:
                raise ValueError(f"{key} must be finite and non-negative")
            return value
    return None


def calculate_buyer_pressure_metrics(
    buy_requests: list[dict[str, Any]],
    baseline_payload: dict[str, Any],
    collection_time: datetime,
) -> dict[str, Any] | None:
    prop = build_ephemeral_property(baseline_payload)
    if prop is None:
        return None
    if not isinstance(collection_time, datetime) or collection_time.tzinfo is None:
        raise ValueError("collection_time must be timezone-aware")
    cutoff = collection_time.astimezone(timezone.utc) - timedelta(days=30)
    evaluated: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for buy in buy_requests:
        if buy_readiness(buy)["can_match"]:
            result = calculate_match(buy, prop)
            if not isinstance(result, dict):
                raise ValueError("invalid MATCH result")
            evaluated.append((buy, result))

    compatible: list[tuple[dict[str, Any], Decimal]] = []
    for buy, result in evaluated:
        score = _finite_decimal(result.get("score_total"))
        hard_fail_count = result.get("hard_fail_count")
        compatibility = result.get("compatibility_status")
        if (
            score is None
            or score < 0
            or score > 100
            or not isinstance(hard_fail_count, int)
            or isinstance(hard_fail_count, bool)
            or hard_fail_count < 0
            or compatibility not in {"compatible", "exception", "incompatible"}
            or result.get("algorithm_version") != ALGORITHM_VERSION
        ):
            raise ValueError("invalid MATCH result")
        if (
            hard_fail_count == 0
            and compatibility != "incompatible"
            and score >= Decimal("55")
        ):
            compatible.append((buy, score))

    scores = [score for _, score in compatible]
    budgets = [
        budget
        for buy, _ in compatible
        if (budget := _selected_budget(buy)) is not None
    ]
    return canonicalize_metrics(
        {
            "evaluated_buyers": len(evaluated),
            "compatible_buyers": len(compatible),
            "highly_compatible_buyers": sum(
                score >= Decimal("80") for _, score in compatible
            ),
            "recent_compatible_buyers_30d": sum(
                _last_activity_at(buy) >= cutoff for buy, _ in compatible
            ),
            "average_match_score": _mean(scores),
            "maximum_match_score": (
                max(scores).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
                if scores
                else None
            ),
            "average_budget": _mean(budgets),
            "algorithm_version": ALGORITHM_VERSION,
        }
    )
