PROPOSAL_STATUSES = {
    "draft",
    "submitted",
    "accepted",
    "rejected",
    "expired",
    "withdrawn",
}

OPEN_PROPOSAL_STATUSES = {"draft", "submitted"}
TERMINAL_PROPOSAL_STATUSES = {"accepted", "rejected", "expired", "withdrawn"}

PROPOSAL_TRANSITIONS = {
    "draft": {"submitted", "withdrawn"},
    "submitted": {"accepted", "rejected", "expired", "withdrawn"},
    "accepted": set(),
    "rejected": set(),
    "expired": set(),
    "withdrawn": set(),
}
