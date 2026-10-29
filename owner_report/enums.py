"""REPORT-PROPRIETARIO-1 - costanti del report attivita' per il proprietario."""
from __future__ import annotations

TEMPLATE_VERSION = "report_proprietario_pdf_v1"
TITLE = "Report attività per il proprietario"

#: Senza un incarico, il periodo predefinito sono gli ultimi 90 giorni.
DEFAULT_PERIOD_DAYS = 90
MAX_PERIOD_DAYS = 731
AGENT_NOTES_MAX = 3000

COMMERCIAL_STATUS_LABELS = {
    "draft": "Bozza", "evaluation": "In valutazione", "mandate": "Mandato", "active": "Attivo",
    "reserved": "Riservato", "under_offer": "Sotto offerta", "sold": "Venduto", "withdrawn": "Ritirato",
    "archived": "Archiviato",
}
MANDATE_STATE_LABELS = {
    "active": "In corso", "expired": "Scaduto", "sold": "Venduto", "withdrawn": "Ritirato",
    "archived": "Archiviato",
}
#: Le proposte di acquisto contate nel report: mai le bozze interne.
PROPOSAL_STATUS_LABELS = {
    "submitted": "In attesa di risposta", "accepted": "Accettate", "rejected": "Rifiutate",
    "expired": "Scadute", "withdrawn": "Ritirate",
}

#: Come si contano le voci: stampato nel PDF, nessun dato ricavato in altro modo.
DEFINITIONS = (
    "Richieste ricevute: contatti collegati all'immobile come interessati all'acquisto, per data di collegamento.",
    "Visite effettuate: visite all'immobile segnate come svolte, per data di svolgimento.",
    "Proposte di acquisto: proposte registrate e inviate (mai le bozze), per data di registrazione.",
)

PROPERTY_NOT_FOUND_MESSAGE = "Immobile non trovato."
NOT_ALLOWED_MESSAGE = "Non puoi preparare il report di questo immobile."
PERIOD_INVALID_MESSAGE = "Il periodo non è valido: la data di inizio deve precedere quella di fine."
PERIOD_TOO_LONG_MESSAGE = f"Il periodo può coprire al massimo {MAX_PERIOD_DAYS} giorni."
PERIOD_FUTURE_MESSAGE = "Il periodo non può finire dopo oggi."
LOGO_UNREADABLE_MESSAGE = ("Il logo dell'agenzia non è leggibile: il PDF non è stato generato. "
                           "Ricarica il logo dalla pagina «Dati agenzia e logo».")
