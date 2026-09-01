"""Domain exceptions for the P18 Follow-up module."""


class FollowupError(Exception):
    """Base error for the Follow-up module."""


class ValidationError(FollowupError):
    """Raised for bad input: unknown rule_code, disabled rule, wrong
    trigger_type for the rule, or a missing reference required by the
    rule's own contract (e.g. the first rule requires stima_id)."""


class ConflictError(FollowupError):
    """Raised when a previous, not-yet-completed attempt for the same
    idempotency_key already exists (status 'pending' or 'failed').

    P18-B deliberately does not auto-retry this case: silently retrying
    could double-create a CORE task if the earlier attempt actually
    succeeded at creating the task but crashed before recording
    'completed' (see followup/repository.py). Raising here makes the
    ambiguous case loud - it reaches safe_run_followup() like any other
    failure, is logged, and never blocks the caller - rather than risking
    a silent duplicate task. A real retry/reconciliation policy is future
    work, not part of the P18-B foundation.
    """
