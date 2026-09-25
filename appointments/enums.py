"""A30-1 - i valori ammessi. Specchio dei CHECK della migration 072.

Un test confronta queste tuple con il testo della migration: se una delle
due cambia da sola, il test lo dice.
"""
from __future__ import annotations

APPOINTMENT_TYPES = (
    "call", "video_call", "seller_meeting", "inspection", "buyer_visit",
    "valuation_presentation", "mandate_signing", "proposal",
    "preliminary_contract", "notary", "technical", "other",
)

#: Etichette dell'interfaccia italiana. Sopralluogo proprietario (inspection)
#: e visita acquirente (buyer_visit) sono due cose diverse.
APPOINTMENT_TYPE_LABELS_IT = {
    "call": "Telefonata",
    "video_call": "Videochiamata",
    "seller_meeting": "Appuntamento proprietario",
    "inspection": "Sopralluogo",
    "buyer_visit": "Visita acquirente",
    "valuation_presentation": "Presentazione valutazione",
    "mandate_signing": "Firma incarico",
    "proposal": "Proposta",
    "preliminary_contract": "Preliminare",
    "notary": "Rogito",
    "technical": "Tecnico",
    "other": "Altro",
}

APPOINTMENT_STATUSES = (
    "requested", "scheduled", "confirmed", "completed",
    "cancelled", "no_show", "rescheduled",
)

#: Gli stati che occupano l'agenda dell'agente (decisione Q-A6). Stesso
#: elenco della clausola WHERE del vincolo EXCLUDE.
BLOCKING_STATUSES = ("scheduled", "confirmed", "completed", "no_show")

#: Gli stati con cui il service accetta di CREARE un appuntamento.
CREATABLE_STATUSES = ("requested", "scheduled", "confirmed")

#: Gli stati da cui si puo' spostare un appuntamento.
RESCHEDULABLE_STATUSES = ("scheduled", "confirmed")

APPOINTMENT_SOURCES = (
    "crm_manual", "legacy_stime_dettagliate", "stima_inspections_backfill",
    "booking_link", "system", "a30_test",
    # A30-2P, migration 073: i sopralluoghi nati dalle rotte LMC-15 attraverso
    # l'Agenda. Solo `inspection`, sempre collegati a `stima_inspections`.
    "lmc15_facade",
)

#: Le fonti STORICHE: solo i loro record chiusi (completed, no_show) possono
#: non avere un agente, perche' la fonte non lo dice e non lo si inferisce (Q4).
HISTORICAL_SOURCES = ("legacy_stime_dettagliate", "stima_inspections_backfill")

#: Il marcatore dei dati di prova (Q7): cancellabili solo da
#: `a30_test_purge(run_id)`, rifiutati su PROD.
TEST_SOURCE = "a30_test"

#: Durate standard in minuti (decisione Q-A5 / Q5 del GATE A30-1). Sono un
#: DEFAULT proposto all'operatore, non un vincolo: ogni appuntamento porta i
#: propri orari. La configurazione per agenzia/agente e' A30-11; fino ad
#: allora si cambia qui. `buyer_visit`: 60 minuti.
DEFAULT_DURATION_MINUTES = {
    "call": 15,
    "video_call": 30,
    "inspection": 60,
    "buyer_visit": 60,
}
FALLBACK_DURATION_MINUTES = 60


def default_duration_minutes(appointment_type: str) -> int:
    return DEFAULT_DURATION_MINUTES.get(appointment_type, FALLBACK_DURATION_MINUTES)


GOOGLE_SYNC_STATUSES = ("not_synced", "pending", "synced", "error", "disabled")

DEFAULT_TIMEZONE = "Europe/Rome"
