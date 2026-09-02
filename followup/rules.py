"""Deterministic rule definitions for the P18 Follow-up module.

Rules are plain data, not a framework: a dataclass plus a dict registry,
same shape FLOW already uses successfully for its own predefined rules
(see flow/rules/registry.py) but deliberately without FLOW's versioning,
simulation-gate or cooldown machinery - P18-B does not need it yet, and
adding it now would be speculative complexity nobody asked for. New rules
are added later (P18-D, P19, P23) by adding another FollowupRule to
ALL_RULES; nothing else in this module needs to change shape for that.

No AI, no dynamic/DB-authored rules: every rule is a Python constant here,
reviewed and deployed like any other code change, per principle 5 in the
P18 design ("Regole deterministiche").
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FollowupRule:
    rule_code: str
    trigger_type: str  # 'event' | 'time' (only 'event' is used in P18-B)
    event_type: str | None  # the seller_timeline_events.event_type this
    # rule reacts to, for trigger_type == 'event'; None for time-based
    # rules (P18-D), which instead scan CRM state directly.
    action: str  # 'create_core_task' is the only action P18-B implements.
    title: str
    task_type: str
    priority: str  # must be one of CORE's tasks_priority_chk values:
    # 'low' | 'normal' | 'high' | 'urgent' (see migrations/001_core_
    # contacts_leads.sql) - not re-validated here, CORE's own SQL CHECK is
    # the source of truth and will reject an invalid value.
    due_hours: int
    enabled: bool


# due_hours=24 for the first rule matches FLOW's own default for the exact
# same task ("Contattare proprietario", see flow/rules/registry.py
# FLOW-R008 and flow/repository.py's create_core_task default of 24h) -
# reused rather than invented, so the same task title carries the same
# implied urgency regardless of which module created it.
ALL_RULES: dict[str, FollowupRule] = {
    "FOLLOWUP_STIMA_RICHIESTA": FollowupRule(
        rule_code="FOLLOWUP_STIMA_RICHIESTA",
        trigger_type="event",
        event_type="stima_richiesta",
        action="create_core_task",
        title="Contattare proprietario",
        task_type="automated_followup",
        priority="normal",
        due_hours=24,
        enabled=True,
    ),
    "FOLLOWUP_TASK_STALE_ESCALATE_V1": FollowupRule(
        rule_code="FOLLOWUP_TASK_STALE_ESCALATE_V1",
        trigger_type="time",
        event_type=None,
        action="escalate_core_task_priority",
        title="Contattare proprietario",
        task_type="automated_followup",
        priority="high",
        due_hours=0,
        enabled=True,
    ),
}


def get_rule(rule_code: str) -> FollowupRule:
    try:
        return ALL_RULES[rule_code]
    except KeyError:
        raise KeyError(f"unknown followup rule {rule_code!r}") from None
