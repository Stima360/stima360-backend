"""WHATSAPP DUE CASELLE - costanti delle caselle (migrazione 130)."""
from __future__ import annotations

ACCESS_AGENCY = "agency"          # come oggi: chiunque veda la casella WhatsApp dell'agenzia
ACCESS_RESTRICTED = "restricted"  # solo il titolare e gli operatori autorizzati
ACCESS_MODES = frozenset({ACCESS_AGENCY, ACCESS_RESTRICTED})

KIND_CLOUD_API = "cloud_api"
KIND_COEXISTENCE = "coexistence"
KINDS = frozenset({KIND_CLOUD_API, KIND_COEXISTENCE})

STATUS_ACTIVE = "active"
STATUS_DISABLED = "disabled"
STATUSES = frozenset({STATUS_ACTIVE, STATUS_DISABLED})

LABEL_MAX = 60

NOT_INSTALLED_MESSAGE = ("Le caselle WhatsApp non sono ancora attive su questo ambiente: "
                         "la migrazione 130 non è stata applicata.")
FORBIDDEN_MESSAGE = "Solo il titolare dell'agenzia può configurare le caselle WhatsApp."
NOT_ROUTED_MESSAGE = ("Questo numero non è instradato a questa agenzia nella configurazione del server "
                      "(WHATSAPP_INBOUND_ROUTES).")
MEMBER_NOT_FOUND_MESSAGE = "Uno degli operatori indicati non è un membro attivo dell'agenzia."
LABEL_TAKEN_MESSAGE = "Un'altra casella dell'agenzia ha già questa etichetta."
