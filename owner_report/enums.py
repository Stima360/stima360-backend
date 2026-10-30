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

#: Visite future: solo quelle gia' fissate o confermate, in un intervallo esplicito.
UPCOMING_DAYS = 30
NOT_AVAILABLE = "Dato non disponibile"

#: Che cosa si conta e con quale data: stampato nel PDF e mostrato in anteprima.
DEFINITIONS = (
    "Contatti interessati registrati: contatti collegati all'immobile come interessati all'acquisto, "
    "attribuiti al periodo con la data del collegamento all'immobile (non con la data di creazione del contatto).",
    "Richieste ricevute: dato non disponibile. Il CRM non registra ancora le richieste in arrivo per immobile "
    "con data e provenienza; contatti, abbinamenti automatici e messaggi non vengono contati come richieste.",
    "Visite effettuate: visite all'immobile segnate come svolte in agenda, per data di svolgimento, più le "
    "visite registrate prima dell'agenda; una visita registrata due volte (scheda e agenda, stesso giorno) "
    "conta una sola volta.",
    f"Visite in programma: visite già fissate o confermate nei {UPCOMING_DAYS} giorni successivi alla "
    "generazione del documento; le richieste di visita non ancora fissate non sono contate.",
    "Proposte di acquisto: situazione attuale delle proposte inviate (mai le bozze); la data di invio non è "
    "registrata, quindi non sono attribuite al periodo.",
    "Prezzo richiesto: variazioni del prezzo di annuncio registrate nella scheda; non è una stima del valore "
    "di mercato.",
)

PROPERTY_NOT_FOUND_MESSAGE = "Immobile non trovato."
NOT_ALLOWED_MESSAGE = "Non puoi preparare il report di questo immobile."
PERIOD_INVALID_MESSAGE = "Il periodo non è valido: la data di inizio deve precedere quella di fine."
PERIOD_TOO_LONG_MESSAGE = f"Il periodo può coprire al massimo {MAX_PERIOD_DAYS} giorni."
PERIOD_FUTURE_MESSAGE = "Il periodo non può finire dopo oggi."
LOGO_UNREADABLE_MESSAGE = ("Il logo dell'agenzia non è leggibile: il PDF non è stato generato. "
                           "Ricarica il logo dalla pagina «Dati agenzia e logo».")
