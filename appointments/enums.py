"""A30-1 - i valori ammessi. Specchio dei CHECK della migration 072.

Un test confronta queste tuple con il testo della migration: se una delle
due cambia da sola, il test lo dice.
"""
from __future__ import annotations

APPOINTMENT_TYPES = (
    "call", "video_call", "seller_meeting", "inspection", "buyer_visit",
    "valuation_presentation", "mandate_signing", "proposal",
    "preliminary_contract", "notary", "technical", "other",
    # APPUNTAMENTI DI VENDITA (migration 106): sempre su un incarico, con il
    # cliente interessato all'acquisto e l'agente incaricato.
    "sale_appointment",
)

#: Etichette dell'interfaccia italiana (SEMPLIFICAZIONE-CRM-1: le parole di un
#: agente immobiliare). Sopralluogo proprietario (inspection) e visita
#: immobile (buyer_visit) sono due cose diverse. `sale_appointment` resta nel
#: catalogo per le righe esistenti ma non si offre piu' alla creazione
#: (`CREATE_HIDDEN_TYPES`): la visita immobile ne ha preso il ruolo.
APPOINTMENT_TYPE_LABELS_IT = {
    "call": "Telefonata",
    "video_call": "Videochiamata",
    "seller_meeting": "Appuntamento proprietario",
    "inspection": "Sopralluogo",
    "buyer_visit": "Visita immobile",
    "valuation_presentation": "Presentazione valutazione",
    "mandate_signing": "Firma incarico",
    "proposal": "Proposta d’acquisto",
    "preliminary_contract": "Preliminare di compravendita",
    "notary": "Rogito notarile",
    "technical": "Appuntamento tecnico",
    "other": "Altro",
    "sale_appointment": "Appuntamento di vendita",
}

#: SEMPLIFICAZIONE-CRM-1: la VISITA IMMOBILE e' `buyer_visit` (proiezione in
#: `property_visits`, promemoria, Google, voce: tutto invariato) e da ora si
#: fissa SOLO su un incarico reale, con il cliente acquirente e l'agente.
#: Stessa regola di `sale_appointment` (106), che resta valido per le righe
#: gia' scritte ma non si crea piu' dall'interfaccia.
VISIT_TYPE = "buyer_visit"
MANDATE_REQUIRED_TYPES = ("buyer_visit", "sale_appointment")
CREATE_HIDDEN_TYPES = ("sale_appointment",)

#: APPUNTAMENTI DI VENDITA (106). Distinto dalla visita acquirente: nessuna
#: proiezione in `property_visits`, nessuna prenotazione pubblica.
SALE_APPOINTMENT_TYPE = "sale_appointment"
#: Gli stati dell'incarico (`core.property_mandate.mandate_state`) su cui si
#: fissa un appuntamento di vendita: in corso o scaduto. Conclusi con la
#: vendita, ritirati e su immobile archiviato restano esclusi.
SALE_MANDATE_STATES = ("active", "expired")

APPOINTMENT_STATUSES = (
    "requested", "scheduled", "confirmed", "completed",
    "cancelled", "no_show", "rescheduled",
)

#: DELETE-ARCH Fase 1A (migration 084): la qualifica di un annullamento.
#: Lo stato resta `cancelled`; `mistake` = creato per errore, mai esistito:
#: fuori dall'Agenda normale e dalle liste, visibile solo col filtro esplicito.
#: NULL solo per gli annullamenti storici (prima della 084): ogni NUOVO
#: annullamento ha una qualifica, e senza scelta esplicita e' `agency`
#: (REVIEW 1 Fase 1A).
CANCELLED_KINDS = ("client", "agency", "mistake")
CANCELLED_KIND_LABELS_IT = {
    "client": "Annullato dal cliente",
    "agency": "Annullato dall'agenzia",
    "mistake": "Creato per errore",
}
MISTAKE_KIND = "mistake"
DEFAULT_CANCELLED_KIND = "agency"

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
