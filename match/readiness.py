from __future__ import annotations

from core.exceptions import ValidationError

from .enums import ACTIVE_PROPERTY_STATUSES


BUY_NOT_READY_REASON = "Nessun criterio MATCH effettivo impostato"


def _has_text(value):
    return value is not None and str(value).strip() != ""


def _has_effective_location(locations):
    return any(
        _has_text(location.get(field))
        for location in locations or []
        for field in ("microzone", "municipality", "province")
    )


def _has_effective_typology(typologies):
    return any(_has_text(item.get("property_type")) for item in typologies or [])


def _has_effective_feature(features):
    for feature in features or []:
        value_type = feature.get("value_type") or "boolean"
        if value_type == "boolean" and feature.get("value_boolean") is not None:
            return True
        if value_type in ("number", "range") and (
            feature.get("value_min") is not None
            or feature.get("value_max") is not None
        ):
            return True
        if value_type not in ("boolean", "number", "range") and _has_text(
            feature.get("value_text")
        ):
            return True
    return False


def _buy_has_effective_criteria(buy):
    scalar_fields = (
        "budget_target",
        "budget_max",
        "surface_min",
        "surface_target",
        "surface_max",
        "rooms_min",
        "bedrooms_min",
        "bathrooms_min",
    )
    return (
        any(buy.get(field) is not None for field in scalar_fields)
        or _has_effective_location(buy.get("locations"))
        or _has_effective_typology(buy.get("typologies"))
        or _has_effective_feature(buy.get("features"))
    )


def buy_readiness(buy):
    eligibility_reasons = []
    if buy.get("archived_at") is not None:
        eligibility_reasons.append("Richiesta BUY archiviata")
    if buy.get("status") != "active":
        eligibility_reasons.append("Stato BUY non attivo")

    ready = _buy_has_effective_criteria(buy)
    eligible = not eligibility_reasons
    return {
        "id": buy.get("id"),
        "eligible": eligible,
        "ready": ready,
        "can_match": eligible and ready,
        "reasons": [] if ready else [BUY_NOT_READY_REASON],
        "eligibility_reasons": eligibility_reasons,
    }


def property_readiness(prop):
    eligibility_reasons = []
    if prop.get("archived_at") is not None:
        eligibility_reasons.append("Immobile archiviato")
    if prop.get("commercial_status") not in ACTIVE_PROPERTY_STATUSES:
        eligibility_reasons.append("Stato PROPERTY non compatibile con MATCH")

    eligible = not eligibility_reasons
    return {
        "id": prop.get("id"),
        "eligible": eligible,
        "ready": True,
        "can_match": eligible,
        "reasons": [],
        "eligibility_reasons": eligibility_reasons,
    }


def match_readiness(buy=None, prop=None):
    sides = []
    buy_result = buy_readiness(buy) if buy is not None else None
    property_result = property_readiness(prop) if prop is not None else None
    if buy_result is not None:
        sides.append(buy_result)
    if property_result is not None:
        sides.append(property_result)
    if not sides:
        raise ValidationError("buy_request_id o property_id richiesto")
    return {
        "eligible": all(side["eligible"] for side in sides),
        "ready": all(side["ready"] for side in sides),
        "can_match": all(side["can_match"] for side in sides),
        "buy": buy_result,
        "property": property_result,
    }


def require_ready(buy=None, prop=None):
    result = match_readiness(buy, prop)
    reasons = [
        reason
        for side in (result["buy"], result["property"])
        if side is not None
        for reason in side["reasons"]
    ]
    if reasons:
        raise ValidationError("; ".join(reasons))
    return result
