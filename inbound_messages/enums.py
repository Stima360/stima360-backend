"""BLOCCO-4A - costanti della ricezione WhatsApp."""
from __future__ import annotations

#: Configurazione server: `phone_number_id` del numero destinatario -> agenzia.
#: Formato `phone_number_id:agency_id`, separati da virgola. Un numero assente
#: o presente con due agenzie diverse NON e' instradato (mai un'agenzia di ripiego).
ROUTES_ENV = "WHATSAPP_INBOUND_ROUTES"

ROUTED = "routed"
UNROUTED = "unrouted"
REASON_UNKNOWN = "unknown_number"
REASON_AMBIGUOUS = "ambiguous_number"
REASON_AGENCY_NOT_FOUND = "agency_not_found"

MATCH_NONE = "none"
MATCH_MATCHED = "matched"
MATCH_AMBIGUOUS = "ambiguous"
MATCH_MANUAL = "manual"

TEXT_MAX = 10000

#: BLOCCO-4B: le sole parole che valgono come rinuncia (testo intero, senza
#: punteggiatura). Rinuncia al marketing su WhatsApp, non alle risposte di assistenza.
STOP_KEYWORDS = frozenset({"STOP"})
META_TEXT_MAX = 1000

MESSAGE_TYPE_LABELS = {
    "text": "Testo", "image": "Immagine", "video": "Video", "audio": "Audio", "document": "Documento",
    "sticker": "Sticker", "location": "Posizione", "contacts": "Contatto condiviso", "interactive": "Risposta a un menu",
    "button": "Risposta a un pulsante", "reaction": "Reazione", "order": "Ordine", "system": "Messaggio di sistema",
    "unsupported": "Tipo non supportato da WhatsApp", "request_welcome": "Primo contatto",
}

NOT_INSTALLED_MESSAGE = ("La ricezione WhatsApp non è ancora attiva su questo ambiente: "
                         "la migrazione 118 non è stata applicata.")
MESSAGE_NOT_FOUND_MESSAGE = "Messaggio non trovato."
LINK_FORBIDDEN_MESSAGE = "Solo il titolare o un amministratore dell'agenzia può associare i messaggi."
CONTACT_NOT_FOUND_MESSAGE = "Contatto non trovato in questa agenzia."
PROPERTY_NOT_FOUND_MESSAGE = "Immobile non trovato in questa agenzia."
