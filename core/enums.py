"""Allowed values used by the CORE CRM module."""

CONTACT_TYPES = {"person", "company"}
CONTACT_STATUSES = {"active", "inactive", "archived"}
CONTACT_ROLES = {
    "owner",
    "seller",
    "buyer",
    "prospect",
    "referrer",
    "agency",
    "professional",
    "other",
}
LEAD_PIPELINES = {"sell", "buy", "general"}
LEAD_STAGES = {"new", "contacted", "qualified", "appointment", "proposal", "won", "lost"}
PRIORITIES = {"low", "normal", "high", "urgent"}
LEAD_STATUSES = {"open", "paused", "closed"}
LEAD_STIMA_RELATIONS = {"origin", "related", "follow_up"}
ACTIVITY_TYPES = {"note", "call", "email", "whatsapp", "meeting", "valuation", "status_change", "system"}
ACTIVITY_DIRECTIONS = {"in", "out", "internal"}
TASK_STATUSES = {"open", "in_progress", "completed", "cancelled"}
