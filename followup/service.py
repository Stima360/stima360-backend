"""Application layer for the P18 Follow-up module.

run_followup() is the validated, exception-raising entry point. It is
callable directly (for tests, and later for the time-based scan endpoint
in P18-D) but is not what a public-facing flow should call - that is
safe_run_followup(), the never-raising wrapper, exactly mirroring
seller_intelligence.service.safe_record_event() in shape and guarantees.

P18-B ships both functions fully implemented and fully tested, but nothing
in main.py calls either of them yet (see followup/__init__.py). Wiring
run_followup() into the real /api/salva_stima flow via
safe_run_followup() is P18-C's job, not this one's.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from . import repository
from .exceptions import ValidationError
from .rules import get_rule

logger = logging.getLogger(__name__)


def _build_idempotency_key(*, event_type: str, stima_id: int) -> str:
    # Deterministic and rule-specific: same shape for every event-driven
    # rule (followup:<event_type>:<stima_id>), so the same stima_id can
    # never trigger the same rule's task twice, while a *different* rule
    # reacting to the same stima_id gets its own key and its own task.
    return f"followup:{event_type}:{stima_id}"


def run_followup(
    *,
    rule_code: str,
    trigger_type: str,
    contact_id: int | None = None,
    lead_id: int | None = None,
    stima_id: int | None = None,
    created_by: str | None = None,
) -> dict[str, Any]:
    """Validate inputs against the named rule, then execute it.

    Raises ValidationError for anything wrong with the call itself
    (unknown rule, disabled rule, trigger_type mismatch, missing reference
    required by the rule), and propagates whatever
    followup.repository.execute_followup_action raises for anything that
    goes wrong while actually creating the task (ConflictError,
    core.exceptions.NotFoundError, etc.). Never swallows - see
    safe_run_followup() for the non-blocking wrapper.
    """
    try:
        rule = get_rule(rule_code)
    except KeyError as exc:
        raise ValidationError(str(exc)) from exc

    if not rule.enabled:
        raise ValidationError(f"followup rule {rule_code!r} is not enabled")

    if trigger_type != rule.trigger_type:
        raise ValidationError(
            f"followup rule {rule_code!r} expects trigger_type="
            f"{rule.trigger_type!r}, got {trigger_type!r}"
        )

    if rule.trigger_type == "event":
        # P18-B's only rule is event-driven and keyed off stima_id - see
        # the P18-A audit, section 7 ("Per la prima regola: stima_id
        # obbligatorio"). stima_id being present also guarantees CORE's
        # own tasks_reference_chk (at least one of contact_id/lead_id/
        # stima_id) is satisfied even when contact_id and lead_id are both
        # None, e.g. because the CORE bridge failed upstream.
        if stima_id is None:
            raise ValidationError(
                f"followup rule {rule_code!r} requires stima_id"
            )
        idempotency_key = _build_idempotency_key(
            event_type=rule.event_type, stima_id=stima_id
        )
    else:
        raise ValidationError(
            f"followup rule {rule_code!r} has unsupported trigger_type "
            f"{rule.trigger_type!r} (only 'event' is implemented in P18-B)"
        )

    due_at = datetime.now(timezone.utc) + timedelta(hours=rule.due_hours)

    return repository.execute_followup_action(
        rule_code=rule.rule_code,
        trigger_type=rule.trigger_type,
        idempotency_key=idempotency_key,
        contact_id=contact_id,
        lead_id=lead_id,
        stima_id=stima_id,
        task_title=rule.title,
        task_description=None,
        task_type=rule.task_type,
        priority=rule.priority,
        due_at=due_at,
        created_by=created_by or "FOLLOWUP",
    )


def safe_run_followup(**kwargs: Any) -> dict[str, Any] | None:
    """Never-raising wrapper around run_followup().

    This is the ONLY function a public-facing flow (main.py, once wired in
    P18-C) is meant to call. ANY exception - ValidationError, ConflictError,
    a database/connection error, or anything else - is caught here, logged,
    and swallowed. It never re-raises and never returns anything the caller
    is expected to act on: the public funnel must behave identically
    whether this returns a result or None.

    Returns the repository result dict on success, None on any failure.
    """
    try:
        return run_followup(**kwargs)
    except Exception as exc:  # noqa: BLE001 - intentional catch-all, see docstring
        logger.error(
            "followup_engine_failed rule_code=%s trigger_type=%s stima_id=%s "
            "contact_id=%s lead_id=%s error_type=%s error=%s",
            kwargs.get("rule_code"),
            kwargs.get("trigger_type"),
            kwargs.get("stima_id"),
            kwargs.get("contact_id"),
            kwargs.get("lead_id"),
            type(exc).__name__,
            exc,
        )
        return None
