"""Pure P21-B Buyer Pressure insight derivation."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from .buyer_pressure import canonicalize_metrics


SCORE_VERSION = "buyer-pressure-score-1.0"
DISCLAIMER = (
    "Indicatore interno basato su richieste BUY attive e criteri MATCH; "
    "non garantisce la vendita né l’interesse per lo specifico immobile."
)
_ONE = Decimal("1")
_ZERO = Decimal("0")
_FIFTY_FIVE = Decimal("55")
_ONE_HUNDRED = Decimal("100")

_BANDS = (
    (
        0,
        0,
        "none",
        "Nessuna domanda rilevata",
        "NESSUNA DOMANDA RILEVATA — 0/100",
        "Al momento non risultano richieste BUY compatibili con i dati disponibili.",
    ),
    (
        1,
        34,
        "low",
        "Domanda bassa",
        "DOMANDA BASSA — {score}/100",
        "Nel database STIMA360 risultano alcune compatibilità, ma la pressione "
        "della domanda è ancora limitata.",
    ),
    (
        35,
        64,
        "medium",
        "Domanda media",
        "DOMANDA MEDIA — {score}/100",
        "Nel database STIMA360 è presente una domanda concreta per immobili con "
        "caratteristiche simili.",
    ),
    (
        65,
        100,
        "high",
        "Domanda alta",
        "DOMANDA ALTA — {score}/100",
        "Nel database STIMA360 è presente una domanda elevata di acquirenti "
        "compatibili per immobili con caratteristiche simili.",
    ),
)


def _round_points(value: Decimal) -> int:
    return int(value.quantize(_ONE, rounding=ROUND_HALF_UP))


def _clamp(value: Decimal, lower: Decimal, upper: Decimal) -> Decimal:
    return max(lower, min(upper, value))


def _validate_relationships(metrics: dict[str, Any]) -> None:
    evaluated = metrics["evaluated_buyers"]
    compatible = metrics["compatible_buyers"]
    highly_compatible = metrics["highly_compatible_buyers"]
    recent_compatible = metrics["recent_compatible_buyers_30d"]
    average_score = metrics["average_match_score"]
    maximum_score = metrics["maximum_match_score"]
    average_budget = metrics["average_budget"]

    if not 0 <= highly_compatible <= compatible <= evaluated:
        raise ValueError("buyer pressure compatibility counts are inconsistent")
    if not 0 <= recent_compatible <= compatible:
        raise ValueError("buyer pressure recent count is inconsistent")
    if compatible == 0:
        if any(value is not None for value in (average_score, maximum_score, average_budget)):
            raise ValueError("zero compatible buyers require null aggregates")
        return
    if average_score is None or maximum_score is None:
        raise ValueError("compatible buyers require match score aggregates")
    if not _FIFTY_FIVE <= average_score <= _ONE_HUNDRED:
        raise ValueError("average match score is outside the compatible range")
    if not _FIFTY_FIVE <= maximum_score <= _ONE_HUNDRED:
        raise ValueError("maximum match score is outside the compatible range")
    if maximum_score < average_score:
        raise ValueError("maximum match score cannot be lower than the average")


def _quality_points(score: Decimal | None, maximum: int) -> int:
    if score is None:
        return 0
    ratio = _clamp((score - _FIFTY_FIVE) / Decimal("45"), _ZERO, _ONE)
    return _round_points(ratio * Decimal(maximum))


def _factor(code: str, label: str, points: int, max_points: int) -> dict[str, Any]:
    return {
        "code": code,
        "label": label,
        "points": points,
        "max_points": max_points,
    }


def _band_for(score: int) -> tuple[str, str, str, str]:
    for minimum, maximum, code, label, headline, message in _BANDS:
        if minimum <= score <= maximum:
            return code, label, headline.format(score=score), message
    raise ValueError("buyer pressure score is outside the supported range")


def derive_buyer_pressure_insight(metrics: dict) -> dict:
    """Return the deterministic P21-B score projection for canonical P21-A metrics."""
    canonical = canonicalize_metrics(metrics)
    _validate_relationships(canonical)

    if canonical["compatible_buyers"] == 0:
        component_points = (0, 0, 0, 0, 0)
    else:
        component_points = (
            _round_points(
                Decimal(min(canonical["compatible_buyers"], 10)) / Decimal("10") * Decimal("30")
            ),
            _round_points(
                Decimal(min(canonical["highly_compatible_buyers"], 5)) / Decimal("5") * Decimal("25")
            ),
            _round_points(
                Decimal(min(canonical["recent_compatible_buyers_30d"], 8)) / Decimal("8") * Decimal("20")
            ),
            _quality_points(canonical["average_match_score"], 15),
            _quality_points(canonical["maximum_match_score"], 10),
        )

    factors = [
        _factor("compatible_volume", "Buyer compatibili", component_points[0], 30),
        _factor(
            "highly_compatible_volume",
            "Buyer altamente compatibili",
            component_points[1],
            25,
        ),
        _factor(
            "recent_compatible_activity",
            "Buyer compatibili attivi negli ultimi 30 giorni",
            component_points[2],
            20,
        ),
        _factor(
            "average_match_quality",
            "Qualità media dei match compatibili",
            component_points[3],
            15,
        ),
        _factor(
            "maximum_match_quality",
            "Migliore match compatibile",
            component_points[4],
            10,
        ),
    ]
    score = max(0, min(100, sum(component_points)))
    band, band_label, headline, message = _band_for(score)
    return {
        "score_version": SCORE_VERSION,
        "score": score,
        "band": band,
        "band_label": band_label,
        "headline": headline,
        "message": message,
        "disclaimer": DISCLAIMER,
        "factors": factors,
    }
