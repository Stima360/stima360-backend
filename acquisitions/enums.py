"""CRM-OPS-3 - il catalogo delle Acquisizioni. Specchio dei CHECK della 081.

UNA sola fonte: il frontend li legge da `GET /api/acquisitions/options` e
non ne tiene una copia. Un test confronta queste tuple con il testo della
migration: se una delle due cambia da sola, il test lo dice.
"""
from __future__ import annotations

#: Gli stati, nell'ordine della pipeline. Un'acquisizione nasce SEMPRE con
#: il suo appuntamento in Agenda (`POST /api/acquisitions` lo pretende nel
#: corpo e `appointment_id` e' NOT NULL): non esistono stati "prima"
#: dell'appuntamento.
STATUSES = (
    "appointment_set", "inspection_done",
    "valuation_presented", "mandate_negotiation", "acquired", "lost",
)
STATUS_LABELS_IT = {
    "appointment_set": "Appuntamento fissato",
    "inspection_done": "Sopralluogo effettuato",
    "valuation_presented": "Valutazione presentata",
    "mandate_negotiation": "Trattativa incarico",
    "acquired": "Acquisita",
    "lost": "Persa",
}
TERMINAL_STATUSES = ("acquired", "lost")
OPEN_STATUSES = tuple(s for s in STATUSES if s not in TERMINAL_STATUSES)

#: Lo stato con cui nasce un'acquisizione: nasce SEMPRE con il suo
#: appuntamento in Agenda.
INITIAL_STATUS = "appointment_set"

#: Le transizioni che un operatore puo' chiedere a mano. Tutte in avanti.
#: Non ci sono:
#:   * `appointment_set -> inspection_done`: lo decide l'Agenda quando
#:     l'appuntamento viene segnato svolto (una sola fonte di verita');
#:   * `-> acquired`: solo `POST /{id}/mandate`, che genera l'incarico;
#:   * `-> lost`: solo `POST /{id}/lost`, che pretende il motivo;
#:   * un nuovo appuntamento (`POST /{id}/appointment`) sostituisce quello
#:     annullato o mancato e NON tocca lo stato commerciale.
MANUAL_TRANSITIONS = {
    "appointment_set": (),
    "inspection_done": ("valuation_presented", "mandate_negotiation"),
    "valuation_presented": ("mandate_negotiation",),
    "mandate_negotiation": (),
    "acquired": (),
    "lost": (),
}

#: Gli stati da cui si puo' generare l'incarico: dopo il sopralluogo.
MANDATE_FROM_STATUSES = ("inspection_done", "valuation_presented", "mandate_negotiation")

#: Lo stato verso cui l'Agenda fa avanzare l'acquisizione quando il SUO
#: appuntamento viene completato - solo da `appointment_set`, mai indietro.
AGENDA_COMPLETED_ADVANCES = {"appointment_set": "inspection_done"}

#: L'appuntamento attuale si puo' sostituire con uno nuovo solo quando non
#: e' piu' in corso.
REPLACEABLE_APPOINTMENT_STATUSES = ("cancelled", "no_show")

LOST_REASONS = (
    "other_agency", "commission", "price_disagreement", "owner_no_longer_selling",
    "unreachable", "property_or_documents_issue", "other",
)
LOST_REASON_LABELS_IT = {
    "other_agency": "Altra agenzia",
    "commission": "Provvigione",
    "price_disagreement": "Prezzo non condiviso",
    "owner_no_longer_selling": "Il proprietario non vende piu'",
    "unreachable": "Irreperibile",
    "property_or_documents_issue": "Documentazione / problemi immobile",
    "other": "Altro",
}

SALE_TIMINGS = (
    "immediate", "within_3_months", "within_6_months", "within_12_months",
    "over_12_months", "undecided",
)
SALE_TIMING_LABELS_IT = {
    "immediate": "Subito",
    "within_3_months": "Entro 3 mesi",
    "within_6_months": "Entro 6 mesi",
    "within_12_months": "Entro 12 mesi",
    "over_12_months": "Oltre 12 mesi",
    "undecided": "Da definire",
}

#: Da dove nasce l'acquisizione. Catalogo del service (la colonna e' testo
#: libero fino a 100 caratteri, senza CHECK: il catalogo puo' crescere senza
#: una migration), servito da `/options`.
SOURCES = (
    "seller_lead", "referral", "canvassing", "portal", "website",
    "past_client", "walk_in", "other",
)
SOURCE_LABELS_IT = {
    "seller_lead": "Lead venditore",
    "referral": "Segnalazione",
    "canvassing": "Acquisizione diretta / zona",
    "portal": "Portale immobiliare",
    "website": "Sito web",
    "past_client": "Cliente precedente",
    "walk_in": "Passaggio in agenzia",
    "other": "Altro",
}

#: I ruoli di `property_contacts` che possono essere il referente di
#: un'acquisizione (property/enums.py::PROPERTY_CONTACT_ROLES).
OWNER_ROLES = ("owner", "seller")

#: L'appuntamento dell'Agenda (appointments/enums.py): tipo esistente,
#: durata di default dell'Agenda (60').
APPOINTMENT_TYPE = "seller_meeting"
DEFAULT_DURATION_MINUTES = 60

EVENT_TYPES = (
    "created", "updated", "status_changed", "lost", "mandate_created",
    "appointment_rescheduled", "appointment_completed", "appointment_no_show",
    "appointment_cancelled", "appointment_replaced",
)
EVENT_LABELS_IT = {
    "created": "Acquisizione creata",
    "updated": "Dati modificati",
    "status_changed": "Stato cambiato",
    "lost": "Segnata come persa",
    "mandate_created": "Incarico generato",
    "appointment_rescheduled": "Appuntamento spostato",
    "appointment_completed": "Appuntamento svolto",
    "appointment_no_show": "Proprietario assente",
    "appointment_cancelled": "Appuntamento annullato",
    "appointment_replaced": "Nuovo appuntamento",
}

#: La regola, con le stesse parole ovunque: service delle Acquisizioni,
#: service Immobili (400 a property_admin e alla scheda) e interfaccia.
MANDATE_ONLY_FROM_ACQUISITION = "L’incarico può essere generato solo da un’acquisizione."
