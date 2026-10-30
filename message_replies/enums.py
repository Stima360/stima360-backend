"""BLOCCO-4B - costanti delle risposte WhatsApp manuali."""
from __future__ import annotations

#: Modalita' d'invio. Solo `meta` invia davvero; qualsiasi altro valore (anche
#: l'assenza) lascia l'invio DISATTIVATO.
MODE_ENV = "WHATSAPP_OUTBOUND_MODE"
MODE_META = "meta"
TOKEN_ENV = "WHATSAPP_TOKEN"
#: Obbligatoria per attivare l'invio reale: nessuna versione implicita. Si
#: sceglie prima dell'attivazione sulla documentazione ufficiale Meta.
GRAPH_VERSION_ENV = "WHATSAPP_GRAPH_VERSION"
GRAPH_VERSION_PATTERN = r"^v[1-9][0-9]?\.[0-9]$"

#: Regola della piattaforma (finestra di assistenza clienti): un messaggio
#: libero, non template, si puo' inviare solo entro 24 ore dall'ultimo
#: messaggio ricevuto dal cliente. Il margine evita di partire a ridosso della
#: chiusura. Da riconfermare sulla documentazione ufficiale Meta.
WINDOW_HOURS = 24
WINDOW_SAFETY_MINUTES = 2
#: Errore Meta per un messaggio libero fuori finestra (re-engagement).
META_WINDOW_ERROR = 131047

TEXT_MAX = 4096
PROVIDER_LABEL = "meta_cloud_api"
REQUEST_TIMEOUT_SECONDS = 15

STATUS_LABELS = {
    "queued": "In coda", "sending": "In invio", "sent": "Accettato da WhatsApp",
    "failed": "Non inviato", "indeterminate": "Esito incerto: non ritentato",
    "suppressed": "Bloccato", "cancelled": "Annullato",
}
DELIVERY_LABELS = {"sent": "Inviato", "delivered": "Consegnato", "read": "Letto", "failed": "Non consegnato"}

OUTBOUND_DISABLED_MESSAGE = "L'invio WhatsApp è disattivato su questo ambiente: nessun messaggio può partire."
NOT_FOUND_MESSAGE = "Conversazione non trovata."
NO_CONTACT_MESSAGE = "Associa prima la conversazione a un contatto: la risposta resta registrata sulla sua scheda."
SENDER_NOT_CONFIGURED_MESSAGE = ("Il numero WhatsApp di questa conversazione non è configurato per la tua agenzia: "
                                 "la risposta non può partire.")
CREDENTIALS_MISSING_MESSAGE = "Le credenziali d'invio WhatsApp non sono configurate su questo ambiente."
WINDOW_CLOSED_MESSAGE = ("Sono passate più di 24 ore dall'ultimo messaggio del cliente: WhatsApp consente solo "
                         "messaggi modello approvati, non ancora disponibili qui.")
EMPTY_TEXT_MESSAGE = "Scrivi il testo della risposta."
CONFIRM_REQUIRED_MESSAGE = "Conferma l'invio prima di procedere."
GRAPH_VERSION_MISSING_MESSAGE = ("La versione dell'API WhatsApp non è configurata o non è valida "
                                 "(WHATSAPP_GRAPH_VERSION): l'invio resta bloccato.")
STOP_NOTICE = ("Il cliente ha scritto STOP: ha rinunciato alle comunicazioni promozionali su WhatsApp di questa "
               "agenzia. Non è un consenso a riceverne altre: la risposta è possibile solo perché ha scritto nelle "
               "ultime 24 ore, e deve riguardare soltanto la sua richiesta.")
