"""P29-3 - gli insiemi chiusi delle journey. Rispecchiano i CHECK della 071."""
from __future__ import annotations

TRIGGER_STIMA_PDF_SENT = "stima_pdf_sent"
TRIGGER_TYPES = frozenset({TRIGGER_STIMA_PDF_SENT})

JOURNEY_DRAFT, JOURNEY_ACTIVE, JOURNEY_RETIRED = "draft", "active", "retired"
JOURNEY_STATUSES = frozenset({JOURNEY_DRAFT, JOURNEY_ACTIVE, JOURNEY_RETIRED})

ACTOR_SYSTEM, ACTOR_OPERATOR = "system", "operator"
ACTOR_TYPES = frozenset({ACTOR_SYSTEM, ACTOR_OPERATOR})

STEP_MODES = frozenset({"automatic", "assisted"})
DELAY_FROM = frozenset({"trigger", "previous_step_sent"})
STEP_REASON_CODES = ("m1", "m2", "m3", "m4", "m5")

ENR_ACTIVE, ENR_PAUSED, ENR_COMPLETED, ENR_STOPPED = "active", "paused", "completed", "stopped"
ENROLLMENT_STATUSES = frozenset({ENR_ACTIVE, ENR_PAUSED, ENR_COMPLETED, ENR_STOPPED})
OPEN_STATUSES = frozenset({ENR_ACTIVE, ENR_PAUSED})

KIND_ENQUEUE, KIND_AWAIT_OPERATOR = "enqueue", "await_operator"
ACTION_KINDS = frozenset({KIND_ENQUEUE, KIND_AWAIT_OPERATOR})

PAUSED_BY_ENROLLMENT, PAUSED_BY_CONTACT_CONTROL = "enrollment", "contact_control"

#: L'ORDINE E' LA PRIORITA' (P29-3A.1 §H): quando piu' ragioni sono vere
#: insieme vince la prima. Una tupla, non un insieme, e un test ne fissa
#: l'ordine.
STOP_PRIORITY = (
    "mandate_signed", "acquisition_linked", "inspection", "consultation_requested",
    "lead_closed", "contact_inactive",
    "consent_revoked", "consent_not_granted", "consent_inconsistent",
    "expired_on_resume", "operator",
)
STOP_REASONS = frozenset(STOP_PRIORITY)

#: Le tre ragioni del consenso, mappate dalla guardia (P29-1.5).
CONSENT_STOP_BY_GUARD_REASON = {
    "deny_revoked": "consent_revoked",
    "deny_never_given": "consent_not_granted",
    "deny_inconsistent_state": "consent_inconsistent",
}


def choose_stop_reason(reasons) -> str | None:
    """La ragione che vince fra quelle presenti, secondo `STOP_PRIORITY`."""
    presenti = set(reasons)
    for ragione in STOP_PRIORITY:
        if ragione in presenti:
            return ragione
    return None
