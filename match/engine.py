from __future__ import annotations
from decimal import Decimal
from .enums import ALGORITHM_VERSION

WEIGHTS = {
    "location": 20.0,
    "budget": 20.0,
    "typology": 10.0,
    "dimensions": 10.0,
    "rooms": 10.0,
    "features": 20.0,
    "condition": 10.0,
}


def _num(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError, Decimal.InvalidOperation):
        return None


def _norm(value):
    return str(value or "").strip().lower()


def _criterion(code, group, criterion_type, requested, actual, score, result, explanation, blocking=False):
    return {
        "criterion_code": code,
        "criterion_group": group,
        "criterion_type": criterion_type,
        "requested_value": requested,
        "property_value": actual,
        "weight": WEIGHTS.get(group, 0),
        "result": result,
        "score": round(max(0, min(100, score)), 2),
        "penalty": 0,
        "is_blocking": blocking,
        "explanation": explanation,
    }


def _property_feature(prop, code):
    direct = {
        "elevator": prop.get("elevator"),
        "surface_sqm": prop.get("surface_sqm") or prop.get("commercial_surface_sqm"),
        "rooms": prop.get("rooms"),
        "bedrooms": prop.get("bedrooms"),
        "bathrooms": prop.get("bathrooms"),
        "asking_price": prop.get("asking_price"),
        "condition": prop.get("condition"),
        "energy_class": prop.get("energy_class"),
    }
    if code in direct:
        return direct[code]
    metadata = prop.get("metadata") or {}
    return metadata.get(code)


def _location_score(request, prop):
    locations = request.get("locations") or []
    city = _norm(prop.get("city"))
    province = _norm(prop.get("province"))
    microzone = _norm(prop.get("microzone"))
    required = [x for x in locations if x.get("is_required") and not x.get("is_excluded")]
    excluded = [x for x in locations if x.get("is_excluded")]

    def matches(loc):
        if loc.get("microzone") and _norm(loc["microzone"]) == microzone:
            return 100
        if loc.get("municipality") and _norm(loc["municipality"]) == city:
            return 85
        if loc.get("province") and _norm(loc["province"]) == province:
            return 65
        return 0

    if any(matches(x) for x in excluded):
        return _criterion("location", "location", "hard", excluded, {"city": city, "province": province, "microzone": microzone}, 0, "not_matched", "Località esplicitamente esclusa", True)
    if not locations:
        return _criterion("location", "location", "informational", None, {"city": city, "province": province, "microzone": microzone}, 100, "not_applicable", "Nessun vincolo geografico impostato")
    best = max((matches(x) * min(1.0, max(0.1, float(x.get("priority") or 1) / 10)) for x in locations if not x.get("is_excluded")), default=0)
    if required and not any(matches(x) for x in required):
        return _criterion("location", "location", "hard", required, {"city": city, "province": province, "microzone": microzone}, 0, "not_matched", "Nessuna località obbligatoria è rispettata", True)
    score = min(100, best if best else 35)
    return _criterion("location", "location", "preference", locations, {"city": city, "province": province, "microzone": microzone}, score, "matched" if score >= 80 else "partially_matched", "Compatibilità geografica calcolata sulle località indicate")


def _budget_score(request, prop):
    price = _num(prop.get("asking_price"))
    target = _num(request.get("budget_target"))
    maximum = _num(request.get("budget_max"))
    flex = _num(request.get("budget_flexibility_percent")) or 0
    if price is None:
        return _criterion("budget", "budget", "soft", {"target": target, "max": maximum}, None, 40, "not_available", "Prezzo immobile non disponibile")
    if maximum is None and target is None:
        return _criterion("budget", "budget", "informational", None, price, 100, "not_applicable", "Nessun budget impostato")
    if target is not None and price <= target:
        score = 100
    elif maximum is not None and price <= maximum:
        base = target or maximum
        score = 100 - 20 * ((price - base) / max(1, maximum - base)) if maximum > base else 85
    else:
        limit = (maximum or target) * (1 + flex / 100)
        if price <= limit:
            score = 60
        else:
            return _criterion("budget", "budget", "hard", {"target": target, "max": maximum, "flex": flex}, price, 0, "not_matched", "Prezzo oltre il budget massimo e la flessibilità", True)
    return _criterion("budget", "budget", "soft", {"target": target, "max": maximum, "flex": flex}, price, score, "matched" if score >= 80 else "partially_matched", "Prezzo confrontato con budget target, massimo e flessibilità")


def _typology_score(request, prop):
    typologies = request.get("typologies") or []
    actual = _norm(prop.get("property_type"))
    if not typologies:
        return _criterion("typology", "typology", "informational", None, actual, 100, "not_applicable", "Nessuna tipologia impostata")
    by_level = {level: {_norm(x.get("property_type")) for x in typologies if x.get("requirement_level") == level} for level in ("required", "preferred", "optional", "excluded")}
    if actual in by_level["excluded"]:
        return _criterion("typology", "typology", "hard", typologies, actual, 0, "not_matched", "Tipologia esplicitamente esclusa", True)
    if by_level["required"] and actual not in by_level["required"]:
        return _criterion("typology", "typology", "hard", typologies, actual, 0, "not_matched", "Tipologia obbligatoria non rispettata", True)
    score = 100 if actual in by_level["required"] or actual in by_level["preferred"] else 70 if actual in by_level["optional"] else 45
    return _criterion("typology", "typology", "preference", typologies, actual, score, "matched" if score >= 80 else "partially_matched", "Tipologia confrontata con livelli di preferenza")


def _range_score(code, group, minimum, target, maximum, actual, hard_min=False):
    actual = _num(actual)
    minimum, target, maximum = _num(minimum), _num(target), _num(maximum)
    if actual is None:
        return _criterion(code, group, "soft", {"min": minimum, "target": target, "max": maximum}, None, 40, "not_available", f"{code}: dato immobile non disponibile")
    if minimum is not None and actual < minimum:
        if hard_min and actual < minimum * 0.9:
            return _criterion(code, group, "hard", {"min": minimum, "target": target, "max": maximum}, actual, 0, "not_matched", f"{code}: valore inferiore al minimo obbligatorio", True)
        score = max(20, 70 * actual / max(1, minimum))
    elif target is not None and actual < target:
        score = 70 + 30 * (actual - (minimum or 0)) / max(1, target - (minimum or 0))
    elif maximum is not None and actual > maximum:
        score = max(60, 100 - 20 * (actual - maximum) / max(1, maximum))
    else:
        score = 100
    return _criterion(code, group, "soft", {"min": minimum, "target": target, "max": maximum}, actual, score, "matched" if score >= 80 else "partially_matched", f"{code}: compatibilità numerica")


def _feature_score(feature, prop):
    code = feature.get("feature_code")
    level = feature.get("requirement_level") or "preferred"
    actual = _property_feature(prop, code)
    vtype = feature.get("value_type") or "boolean"
    wanted = feature.get("value_boolean") if vtype == "boolean" else feature.get("value_text")
    matched = False
    if vtype == "boolean":
        matched = bool(actual) == bool(wanted)
    elif vtype in ("number", "range"):
        number = _num(actual)
        low, high = _num(feature.get("value_min")), _num(feature.get("value_max"))
        matched = number is not None and (low is None or number >= low) and (high is None or number <= high)
    else:
        matched = _norm(actual) == _norm(wanted)
    if level == "excluded":
        blocking = matched
        return _criterion(code, "features", "hard", feature, actual, 0 if matched else 100, "not_matched" if matched else "matched", f"{code}: {'caratteristica esclusa presente' if matched else 'esclusione rispettata'}", blocking)
    if level == "required" and not matched:
        return _criterion(code, "features", "hard", feature, actual, 0, "not_matched", f"{code}: requisito obbligatorio non rispettato", True)
    score = 100 if matched else 55 if level == "optional" else 35
    return _criterion(code, "features", "preference", feature, actual, score, "matched" if matched else "not_matched", f"{code}: {'presente' if matched else 'non rispetta la preferenza'}")


def calculate(request, prop):
    results = [
        _location_score(request, prop),
        _budget_score(request, prop),
        _typology_score(request, prop),
        _range_score("surface", "dimensions", request.get("surface_min"), request.get("surface_target"), request.get("surface_max"), prop.get("surface_sqm") or prop.get("commercial_surface_sqm"), True),
        _range_score("rooms", "rooms", request.get("rooms_min"), request.get("rooms_min"), None, prop.get("rooms"), True),
        _range_score("bedrooms", "rooms", request.get("bedrooms_min"), request.get("bedrooms_min"), None, prop.get("bedrooms"), True),
        _range_score("bathrooms", "rooms", request.get("bathrooms_min"), request.get("bathrooms_min"), None, prop.get("bathrooms"), True),
    ]
    results.extend(_feature_score(feature, prop) for feature in request.get("features") or [])
    condition_score = 100 if prop.get("condition") else 60
    results.append(_criterion("condition", "condition", "preference", None, prop.get("condition"), condition_score, "matched" if condition_score == 100 else "not_available", "Stato immobile disponibile" if condition_score == 100 else "Stato immobile non indicato"))

    group_scores = {}
    for group in WEIGHTS:
        group_items = [x for x in results if x["criterion_group"] == group]
        group_scores[group] = round(sum(x["score"] for x in group_items) / len(group_items), 2) if group_items else 100.0
    hard_fails = [x for x in results if x["is_blocking"]]
    weighted = sum(group_scores[group] * weight for group, weight in WEIGHTS.items()) / sum(WEIGHTS.values())
    score_total = round(max(0, min(100, weighted)), 2)
    if hard_fails:
        compatibility = "incompatible"
        match_class = "incompatible"
    else:
        compatibility = "exception" if any(x["score"] < 50 for x in results) else "compatible"
        match_class = "excellent" if score_total >= 90 else "strong" if score_total >= 80 else "good" if score_total >= 70 else "possible" if score_total >= 55 else "weak" if score_total >= 40 else "poor"
    strengths = [x["explanation"] for x in results if x["score"] >= 85 and not x["is_blocking"]][:8]
    warnings = [x["explanation"] for x in results if 0 < x["score"] < 70 and not x["is_blocking"]][:8]
    blocking = [x["explanation"] for x in hard_fails]
    return {
        "algorithm_version": ALGORITHM_VERSION,
        "compatibility_status": compatibility,
        "score_total": score_total,
        "match_class": match_class,
        "hard_fail_count": len(hard_fails),
        "warning_count": len(warnings),
        "strengths": strengths,
        "warnings": warnings,
        "blocking_reasons": blocking,
        "group_scores": group_scores,
        "criteria": results,
    }
