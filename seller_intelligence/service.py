"""Application layer for the P17 Seller Intelligence module.

This is where the "at least one reference" rule lives. It is deliberately
NOT a SQL CHECK constraint (see migrations/017_seller_intelligence_01.sql):
the table's FKs use ON DELETE SET NULL, and a CHECK requiring at least one
non-null reference would make that SET NULL fail (and therefore block) a
future CORE delete once it nulled out the last remaining reference on an
event row. Seller Intelligence must never be able to block an operation
that CORE would otherwise allow, so the rule is only checked at creation
time, here, in Python - never re-checked afterwards.

record_event() is written as a plain function taking explicit keyword
arguments (not a Pydantic model) on purpose: it is meant to be callable
both from seller_intelligence.router (after Pydantic validation) and,
directly from main.py (P17-B1), without requiring a FastAPI
request/response cycle.

safe_record_event() (added in P17-B1) is the never-raising wrapper main.py
actually calls from the public funnel. It exists so that the try/except
boundary protecting the public funnel lives here, once, instead of being
duplicated inline at every future call site in main.py (P17-B2 will add
more events reusing the same wrapper).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from . import repository
from .exceptions import ValidationError

logger = logging.getLogger(__name__)

_REFERENCE_FIELDS = ("contact_id", "lead_id", "stima_id", "property_id")


def _require_at_least_one_reference(values: dict[str, Any]) -> None:
    if not any(values.get(field) is not None for field in _REFERENCE_FIELDS):
        raise ValidationError(
            "at least one of contact_id, lead_id, stima_id or property_id is required"
        )


def record_event(
    *,
    contact_id: int | None = None,
    lead_id: int | None = None,
    stima_id: int | None = None,
    property_id: int | None = None,
    event_type: str,
    event_source: str | None = None,
    occurred_at: datetime | None = None,
    payload: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
    created_by: str | None = None,
) -> dict[str, Any]:
    values = {
        "contact_id": contact_id,
        "lead_id": lead_id,
        "stima_id": stima_id,
        "property_id": property_id,
    }
    _require_at_least_one_reference(values)

    if not event_type or not event_type.strip():
        raise ValidationError("event_type is required")

    data = {
        **values,
        "event_type": event_type,
        "event_source": event_source,
        "occurred_at": occurred_at or datetime.now(timezone.utc),
        "payload": payload or {},
        "idempotency_key": idempotency_key,
        "created_by": created_by,
    }
    return repository.insert_event(data)


def list_timeline(
    *,
    contact_id: int | None = None,
    lead_id: int | None = None,
    stima_id: int | None = None,
    property_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    return repository.list_timeline(
        contact_id=contact_id,
        lead_id=lead_id,
        stima_id=stima_id,
        property_id=property_id,
        limit=limit,
        offset=offset,
    )


def safe_record_event(**kwargs: Any) -> dict[str, Any] | None:
    """Never-raising wrapper around record_event().

    This is the ONLY function the public funnel (main.py) is meant to call.
    ANY exception raised while recording a Seller Intelligence event -
    ValidationError, a database/connection error, or anything else - is
    caught here, logged, and swallowed. It never re-raises and never
    returns anything the caller is expected to act on: the public funnel
    must behave identically whether this returns a row or None.

    Returns the created/existing row dict on success, None on any failure.
    """
    try:
        return record_event(**kwargs)
    except Exception as exc:  # noqa: BLE001 - intentional catch-all, see docstring
        logger.error(
            "seller_intelligence_event_failed event_type=%s stima_id=%s "
            "contact_id=%s lead_id=%s error_type=%s error=%s",
            kwargs.get("event_type"),
            kwargs.get("stima_id"),
            kwargs.get("contact_id"),
            kwargs.get("lead_id"),
            type(exc).__name__,
            exc,
        )
        return None
