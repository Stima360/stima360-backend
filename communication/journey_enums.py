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

#: I nomi delle ragioni, uno per costante. P29-3C li passa come PARAMETRI
#: alla query fresca degli stop: cosi' il SQL non contiene nessun letterale
#: da tenere allineato a mano con questa lista.
STOP_MANDATE_SIGNED = "mandate_signed"
STOP_ACQUISITION_LINKED = "acquisition_linked"
STOP_INSPECTION = "inspection"
STOP_CONSULTATION_REQUESTED = "consultation_requested"
STOP_LEAD_CLOSED = "lead_closed"
STOP_CONTACT_INACTIVE = "contact_inactive"
STOP_CONSENT_REVOKED = "consent_revoked"
STOP_CONSENT_NOT_GRANTED = "consent_not_granted"
STOP_CONSENT_INCONSISTENT = "consent_inconsistent"
STOP_EXPIRED_ON_RESUME = "expired_on_resume"
STOP_OPERATOR = "operator"

#: L'ORDINE E' LA PRIORITA' (P29-3A.1 §H): quando piu' ragioni sono vere
#: insieme vince la prima. Una tupla, non un insieme, e un test ne fissa
#: l'ordine. E' l'UNICA fonte dell'ordinamento: la query fresca di P29-3C
#: la riceve come array di parametri e ordina per la posizione in questa
#: tupla, invece di riscriverla in SQL.
STOP_PRIORITY = (
    STOP_MANDATE_SIGNED, STOP_ACQUISITION_LINKED, STOP_INSPECTION,
    STOP_CONSULTATION_REQUESTED, STOP_LEAD_CLOSED, STOP_CONTACT_INACTIVE,
    STOP_CONSENT_REVOKED, STOP_CONSENT_NOT_GRANTED, STOP_CONSENT_INCONSISTENT,
    STOP_EXPIRED_ON_RESUME, STOP_OPERATOR,
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
