"""WHATSAPP COEXISTENCE - costanti del collegamento del numero dell'app WhatsApp
Business alla Cloud API con la procedura ufficiale di Meta (Embedded Signup)."""
from __future__ import annotations

#: Valori PUBBLICI della procedura (finiscono nel browser): l'App ID di Meta e
#: l'ID della configurazione di «Facebook Login for Business» creata per
#: l'onboarding dell'app WhatsApp Business.
APP_ID_ENV = "META_APP_ID"
CONFIG_ID_ENV = "META_COEXISTENCE_CONFIG_ID"
#: Segreto dell'app Meta (lo stesso che firma i webhook): serve a scambiare il
#: codice della procedura. Mai nel browser, mai nel database, mai nei log.
APP_SECRET_ENV = "WHATSAPP_APP_SECRET"
#: Facoltativi: indirizzo dei webhook del WABA collegato (override di Meta) e
#: token di verifica. Senza, il WABA usa l'indirizzo dell'app Meta.
CALLBACK_URL_ENV = "WHATSAPP_COEXISTENCE_CALLBACK_URL"
VERIFY_TOKEN_ENV = "WHATSAPP_VERIFY_TOKEN"

STATUS_REQUESTED = "requested"
STATUS_CONFIRMED = "meta_confirmed"
STATUS_FAILED = "failed"

STATUS_LABELS = {
    STATUS_REQUESTED: "Procedura completata, in attesa della conferma di Meta",
    STATUS_CONFIRMED: "Confermato da Meta",
    STATUS_FAILED: "Non completato",
}

SYNC_CONTACTS = "smb_app_state_sync"
SYNC_HISTORY = "history"
SYNC_KINDS = {"contacts": SYNC_CONTACTS, "history": SYNC_HISTORY}

REQUEST_TIMEOUT = (3.05, 15)

OWNER_ROLE = "agency_owner"

FORBIDDEN_MESSAGE = "Solo il titolare dell'agenzia può collegare un numero WhatsApp."
NOT_INSTALLED_MESSAGE = ("Il collegamento WhatsApp Coexistence non è ancora attivo su questo ambiente: "
                         "la migrazione 132 non è stata applicata.")
NOT_CONFIGURED_MESSAGE = "La procedura Meta non è configurata su questo ambiente."
NOT_FOUND_MESSAGE = "Collegamento non trovato."
PHONE_NOT_IN_WABA_MESSAGE = ("Il numero scelto non risulta nell'account WhatsApp Business (WABA) indicato da Meta: "
                             "ripeti la procedura scegliendo lo stesso account.")
PHONE_AMBIGUOUS_MESSAGE = ("L'account WhatsApp Business ha più numeri: indica quale collegare.")
CODE_EXCHANGE_FAILED_MESSAGE = ("Meta non ha accettato il codice della procedura (scaduto o già usato): "
                                "ripeti il collegamento dall'inizio.")
TOKEN_MISSING_MESSAGE = ("Manca il token di sistema per questo numero (WHATSAPP_TOKEN__<phone_number_id> "
                         "o WHATSAPP_TOKEN): senza, il CRM non può chiedere a Meta conferme o sincronizzazioni.")
NOT_ROUTED_MESSAGE = ("Prima di chiedere rubrica e storico il numero deve essere instradato a questa agenzia "
                      "(WHATSAPP_INBOUND_ROUTES sul server): arrivati prima, resterebbero invisibili.")
