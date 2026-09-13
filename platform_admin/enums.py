"""Costanti della superficie Platform.

Valori, non regole: la regola sta nella dipendenza e nel writer, qui ci sono
solo i nomi che entrambi devono usare uguali.
"""
from __future__ import annotations

# Il prefisso del router. Dichiarato qui perche' anche l'audit lo usa - per
# riconoscere una richiesta di superficie platform - e due letterali che devono
# restare identici sono un letterale solo.
ROUTER_PREFIX = "/api/platform"

# Gli esiti registrabili. Corrispondono uno a uno al CHECK di
# platform_audit_log.result nella migration 057: se questa tupla e quel CHECK
# divergono, un esito valido per l'applicazione viene rifiutato dal database.
# tests/test_p27_1_migration_057.py li confronta.
RESULT_SUCCESS = "success"
RESULT_DENIED = "denied"
RESULT_ERROR = "error"
AUDIT_RESULTS = (RESULT_SUCCESS, RESULT_DENIED, RESULT_ERROR)

# L'azione registrata da `require_platform_admin`: l'ammissione alla superficie.
#
# In P27-1 l'ammissione E' l'operazione - non c'e' altro da fare su questa
# superficie - quindi una riga per richiesta e' esattamente la traccia giusta.
# Dalle azioni di P27-2 in poi ogni operazione aggiungera' la PROPRIA riga con
# il proprio nome e il proprio esito, e questa restera' quello che e': la
# registrazione di chi e' entrato.
ACTION_ADMISSION = "platform.admission"

# Lo spazio dei nomi delle azioni. Prefisso obbligatorio, cosi' una riga di
# questa tabella si riconosce dalla sua azione anche fuori contesto.
ACTION_NAMESPACE = "platform."

# L'attore quando non c'e' un operatore identificato. Non e' un caso previsto in
# P27-1 - la superficie non registra nulla per un chiamante non autenticato -
# ma il writer e' una funzione pubblica e deve avere una risposta per ogni
# input, non una NULL silenziosa.
ANONYMOUS_ACTOR = "anonymous"

# 403, non 404: l'endpoint esiste e il chiamante lo sa. Non stiamo nascondendo
# una risorsa, stiamo rifiutando un'operazione. La stessa scelta che CORE fa in
# `AGENCY_CONTEXT_REQUIRED_MESSAGE` e OWNER Admin in `require_owner_admin_context`.
FORBIDDEN_MESSAGE = "Superficie riservata all'amministrazione di piattaforma."

# 503. Un'operazione amministrativa che non si riesce a registrare non avviene:
# vedi `dependencies.require_platform_admin`.
AUDIT_UNAVAILABLE_MESSAGE = (
    "Audit di piattaforma non disponibile: operazione non eseguita."
)


# ---------------------------------------------------------------------------
# P27-2 - GESTIONE AGENZIE
# ---------------------------------------------------------------------------

# Il tipo di oggetto amministrato, come compare in platform_audit_log.target_type.
# Una costante e non il letterale "agency" sparso fra service e test: e' il
# valore con cui si ritrovano le righe di questo registro fra dieci mesi.
TARGET_TYPE_AGENCY = "agency"

ACTION_AGENCY_CREATE = "platform.agency.create"
ACTION_AGENCY_UPDATE = "platform.agency.update"

# Gli stati di un'agenzia, IDENTICI al CHECK `agencies_status_chk` della
# migration 027. Non e' una duplicazione da tollerare, e' una duplicazione da
# sorvegliare: tests/test_p27_2_agencies.py confronta questa tupla con quel
# CHECK, cosi' un quarto stato aggiunto da una parte sola fallisce qui invece
# che in produzione alla prima scrittura.
#
# La semantica e' gia' decisa da operator_auth, e P27-2 non la tocca: una
# sessione e' utilizzabile solo con membership_status='active' E
# agency_status='active'. Quindi `suspended` e `archived` rendono entrambi il
# tenant inutilizzabile, e la differenza fra i due e' amministrativa - un
# affiliato sospeso torna, uno archiviato no - non tecnica.
AGENCY_STATUS_ACTIVE = "active"
AGENCY_STATUS_SUSPENDED = "suspended"
AGENCY_STATUS_ARCHIVED = "archived"
AGENCY_STATUSES = (
    AGENCY_STATUS_ACTIVE,
    AGENCY_STATUS_SUSPENDED,
    AGENCY_STATUS_ARCHIVED,
)

# Lo slug, IDENTICO al CHECK `agencies_slug_chk` della 027. Stessa regola di
# sorveglianza degli stati: il test confronta questa stringa con quella nel
# file di migration.
#
# Validarlo qui non e' ridondante rispetto al CHECK. Il database risponderebbe
# con un errore di vincolo - cioe' un 500, o un messaggio che nomina oggetti
# interni - mentre la richiesta e' semplicemente malformata e merita un 422 che
# dice quale campo.
AGENCY_SLUG_PATTERN = r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$"

# Le lunghezze delle colonne in 027. Superarle e' un 422, non un troncamento
# silenzioso e non un errore del driver.
AGENCY_NAME_MAX = 200
AGENCY_SLUG_MAX = 100

# 404. Un'agenzia inesistente e una che non si puo' vedere non esistono su
# questa superficie: il platform admin vede tutta la rete, quindi qui il 404
# significa davvero "non c'e'" e non nasconde nulla a nessuno.
AGENCY_NOT_FOUND_MESSAGE = "Agenzia non trovata."

# 409. Il messaggio nomina il campo e NON riporta l'errore del database: un
# messaggio di psycopg2 porta il nome del vincolo, quello della tabella e il
# valore in conflitto.
AGENCY_SLUG_CONFLICT_MESSAGE = "Slug gia' assegnato a un'altra agenzia."

# 422. Una PATCH senza campi non e' un aggiornamento vuoto riuscito: e' una
# richiesta che non dice cosa fare, e rispondere 200 la farebbe sembrare
# applicata.
AGENCY_EMPTY_PATCH_MESSAGE = (
    "Indicare almeno un campo da aggiornare."
)
