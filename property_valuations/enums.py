"""BLOCCO-2A - costanti delle valutazioni dell'agente."""
from __future__ import annotations

#: L'unico metodo di questa versione: i valori li inserisce l'agente.
METHOD = "valutazione_agente_v1"
METHOD_LABEL = "Valutazione dell'agente"

STATUS_DRAFT = "draft"
STATUS_APPROVED = "approved"
STATUS_SUPERSEDED = "superseded"

# --- elenchi chiusi dell'interfaccia (etichette in italiano) -----------------

SURFACE_KINDS = {
    "commerciale": "Superficie commerciale",
    "calpestabile": "Superficie calpestabile (netta)",
    "lorda": "Superficie lorda",
    "catastale": "Superficie catastale",
    "altro": "Altro (specificare nella nota)",
}
SURFACE_SOURCES = {
    "misurazione_agente": "Misurazione dell'agente",
    "planimetria": "Planimetria",
    "visura_catastale": "Visura catastale",
    "dichiarazione_proprietario": "Dichiarazione del proprietario",
    "scheda_immobile": "Dati della scheda immobile",
    "altro": "Altro (specificare nella nota)",
}
#: Solo canoni MENSILI di locazione: nessuna tariffa turistica giornaliera.
RENT_SEGMENTS = {
    "residenziale_ordinario": "Residenziale ordinario (canone libero)",
    "residenziale_concordato": "Residenziale a canone concordato",
    "residenziale_transitorio": "Residenziale transitorio",
    "studenti": "Studenti universitari",
    "uso_diverso": "Uso diverso dall'abitativo",
}
RENT_FURNISHING = {
    "arredato": "Arredato",
    "parzialmente_arredato": "Parzialmente arredato",
    "non_arredato": "Non arredato",
}
RENT_EXPENSES = {
    "escluse": "Spese escluse",
    "incluse": "Spese incluse",
    "parzialmente_incluse": "Spese parzialmente incluse",
}
COMPARABLE_KINDS = {"vendita": "Vendita", "locazione": "Locazione"}
COMPARABLE_PRICE_TYPES = {"richiesto": "Prezzo richiesto", "concluso": "Prezzo concluso"}
#: Il periodo a cui si riferisce l'importo di un comparabile di locazione.
COMPARABLE_RENT_PERIODS = {"mensile": "Mensile", "annuale": "Annuale"}

MAX_COMPARABLES = 30

# --- limiti dei testi ---------------------------------------------------------

LONG_TEXT = 4000
MEDIUM_TEXT = 1000
SHORT_TEXT = 300

# --- messaggi -----------------------------------------------------------------

PROPERTY_NOT_FOUND_MESSAGE = "Immobile non trovato."
VALUATION_NOT_FOUND_MESSAGE = "Valutazione non trovata."
NOT_ALLOWED_MESSAGE = "Non puoi gestire le valutazioni di questo immobile."
NOT_OWN_DRAFT_MESSAGE = "Un agente puo' modificare solo le bozze che ha creato."
APPROVE_FORBIDDEN_MESSAGE = "Solo il titolare o un amministratore dell'agenzia puo' approvare una valutazione."
ACTOR_REQUIRED_MESSAGE = "Serve la sessione di un operatore."
PROPERTY_IN_TRASH_MESSAGE = "L'immobile e' nel Cestino: nessuna modifica alle valutazioni."
DRAFT_EXISTS_MESSAGE = "Per questo immobile esiste gia' una bozza di valutazione: modificala o approvala."
NOT_DRAFT_MESSAGE = "Questa valutazione non e' una bozza: non si puo' modificare."
ALREADY_APPROVED_MESSAGE = "Questa valutazione e' gia' stata approvata."
VERSION_CONFLICT_MESSAGE = ("La valutazione e' stata modificata nel frattempo da un altro operatore: "
                            "ricarica e riprova.")
SOURCE_NOT_APPROVED_MESSAGE = "Una nuova versione si crea da una valutazione approvata o superata."
INCOMPLETE_MESSAGE = "La valutazione non e' completa: correggi i punti indicati prima di approvarla."
VALUATIONS_UNAVAILABLE_MESSAGE = ("Le valutazioni non sono ancora attive su questo ambiente: "
                                  "la migrazione 116 non e' stata applicata.")
