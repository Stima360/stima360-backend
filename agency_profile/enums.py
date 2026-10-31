"""BLOCCO-1B - costanti dei dati agenzia e del logo."""
from __future__ import annotations

#: La chiave di `agencies.settings` che contiene i dati per i report. Accanto
#: a `timezone` e `locale` (P27-4), che restano del contratto della Platform:
#: ogni scrittura qui sostituisce SOLO questa chiave e conserva le altre.
SETTINGS_KEY = "report_profile"

#: I campi dei dati, nell'ordine dell'interfaccia. Solo `display_name` e'
#: richiesto in questa fase; gli altri restano facoltativi e assenti finche'
#: nessuno li scrive. Non e' una verifica degli obblighi legali dei documenti.
PROFILE_FIELDS = (
    "display_name",
    "legal_name",
    "vat_number",
    "tax_code",
    "legal_address",
    "phone",
    "email",
    "website",
)

MAX_LENGTHS = {
    "display_name": 120,
    "legal_name": 200,
    "vat_number": 32,
    "tax_code": 32,
    "legal_address": 300,
    "phone": 40,
    "email": 254,
    "website": 300,
}

# --- logo -------------------------------------------------------------------

CONTENT_TYPE_PNG = "image/png"
CONTENT_TYPE_JPEG = "image/jpeg"
LOGO_CONTENT_TYPES = (CONTENT_TYPE_PNG, CONTENT_TYPE_JPEG)

#: 2 MB per file, letti al massimo fin qui: oltre si risponde 413 senza
#: tenere in memoria il resto del corpo.
LOGO_MAX_BYTES = 2 * 1024 * 1024
#: Il limite delle dimensioni DICHIARATE dall'immagine, controllato prima di
#: decomprimere qualunque cosa: un file piccolo non puo' annunciare
#: un'immagine enorme.
LOGO_MAX_SIDE = 4096
LOGO_MAX_PIXELS = 16_777_216
#: Il limite dei byte PRODOTTI dalla normalizzazione (e del vincolo della 115):
#: una ricodifica non puo' gonfiare il logo oltre cio' che il database accetta.
LOGO_MAX_STORED_BYTES = 2 * 1024 * 1024

# --- messaggi (italiano, mostrati all'operatore) -----------------------------

FORBIDDEN_MESSAGE = "Solo il titolare o un amministratore dell'agenzia puo' modificare questi dati."
AGENCY_NOT_FOUND_MESSAGE = "Agenzia non trovata."
PROFILE_CORRUPTED_MESSAGE = "I dati salvati dell'agenzia non sono leggibili: contattare l'assistenza."
LOGO_NOT_FOUND_MESSAGE = "Nessun logo caricato."
LOGO_UNAVAILABLE_MESSAGE = ("Il logo non e' ancora attivo su questo ambiente: "
                            "la migrazione 115 non e' stata applicata.")
LOGO_TOO_LARGE_MESSAGE = "Il logo supera 2 MB."
LOGO_EMPTY_MESSAGE = "Nessun file ricevuto."
LOGO_UNSUPPORTED_MESSAGE = "Formato non ammesso: caricare un file PNG o JPEG."
LOGO_CORRUPTED_MESSAGE = "Il file non e' un'immagine PNG o JPEG integra."
LOGO_OUTPUT_TOO_LARGE_MESSAGE = ("Il logo, una volta normalizzato, supera 2 MB: "
                                 "caricare un'immagine con meno pixel.")
LOGO_ANIMATED_MESSAGE = "Immagini animate o con piu' fotogrammi non ammesse: caricare un logo statico."
LOGO_DIMENSIONS_MESSAGE = (
    f"L'immagine e' troppo grande: al massimo {LOGO_MAX_SIDE} pixel per lato."
)
