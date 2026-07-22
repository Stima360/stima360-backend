from __future__ import annotations
from decimal import Decimal
from datetime import date, datetime


def jsonable(value):
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def changed_fields(previous, current):
    fields = {}
    for key in ("score_total", "match_class", "compatibility_status", "hard_fail_count", "warning_count"):
        before = jsonable(previous.get(key))
        after = jsonable(current.get(key))
        if before != after:
            fields[key] = {"before": before, "after": after}
    return fields


def requires_review(previous, current, threshold=10):
    delta = abs(float(current["score_total"]) - float(previous["score_total"]))
    return (
        delta >= threshold
        or previous.get("match_class") != current.get("match_class")
        or previous.get("compatibility_status") != current.get("compatibility_status")
        or previous.get("hard_fail_count") != current.get("hard_fail_count")
    )
