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


# ---------------------------------------------------------------------------
# P27-3 - OPERATORI, TITOLARI E RUOLI
# ---------------------------------------------------------------------------

TARGET_TYPE_OPERATOR = "operator"

# La membership e' un oggetto suo, non un attributo della persona: e' la riga
# che nasce quando un'identita' gia' esistente entra in un'agenzia, ed e'
# l'unica cosa creata su quel percorso. Registrarla come `operator` renderebbe
# la riga di audit incoerente con se stessa - l'azione direbbe
# "membership.create" e il target indicherebbe un oggetto preesistente che
# nessuno ha creato.
TARGET_TYPE_MEMBERSHIP = "agency_membership"

# DUE AZIONI PER IL POST, NON UNA CON UN METADATO CHE LA CORREGGE.
#
# `POST /agencies/{id}/operators` fa due cose diverse a seconda che l'email sia
# nuova o gia' nota, e l'azione deve dire QUALE delle due e' accaduta. Una sola
# azione `platform.operator.create` con `operator_created=false` nei metadata
# significherebbe che meta' delle righe del registro affermano una cosa falsa
# nel campo che si legge per primo, e che vanno rilette insieme a un metadato
# per capire cosa dicono davvero. In una tabella append-only non c'e' un
# secondo momento in cui correggerla.
ACTION_OPERATOR_CREATE = "platform.operator.create"
ACTION_MEMBERSHIP_CREATE = "platform.membership.create"
ACTION_OPERATOR_UPDATE = "platform.operator.update"
ACTION_MEMBERSHIP_UPDATE = "platform.membership.update"
ACTION_OWNER_TRANSFER = "platform.owner.transfer"

# I tre ruoli di membership, IDENTICI al CHECK `agency_memberships_role_chk`
# della 027. `platform_admin` non c'e' e non deve esserci: e' un flag su
# operator_users, non un ruolo di agenzia, e un platform admin non tiene alcuna
# riga di membership per esserlo (P27-1 D4 permette che ne tenga una, ma in
# quanto operatore di quell'agenzia, non in quanto platform admin).
ROLE_AGENCY_OWNER = "agency_owner"
ROLE_AGENCY_ADMIN = "agency_admin"
ROLE_AGENT = "agent"
MEMBERSHIP_ROLES = (ROLE_AGENCY_OWNER, ROLE_AGENCY_ADMIN, ROLE_AGENT)

# Gli stati di membership, IDENTICI a `agency_memberships_status_chk`.
#   active    membership utilizzabile, compatibilmente con utente e agenzia
#   suspended accesso tenant bloccato, relazione conservata
#   revoked   relazione revocata storicamente - mai una DELETE
MEMBERSHIP_ACTIVE = "active"
MEMBERSHIP_SUSPENDED = "suspended"
MEMBERSHIP_REVOKED = "revoked"
MEMBERSHIP_STATUSES = (MEMBERSHIP_ACTIVE, MEMBERSHIP_SUSPENDED, MEMBERSHIP_REVOKED)

# Gli stati dell'operatore, IDENTICI a `operator_users_status_chk`.
# Nessuno stato nuovo: `invited` esiste nello schema dalla 027 e resta
# selezionabile, ma senza un flusso di invito approvato significa soltanto
# "creato e non ancora utilizzabile" - vedi OPERATOR_DEFAULT_STATUS.
OPERATOR_INVITED = "invited"
OPERATOR_ACTIVE = "active"
OPERATOR_DISABLED = "disabled"
OPERATOR_STATUSES = (OPERATOR_INVITED, OPERATOR_ACTIVE, OPERATOR_DISABLED)

# Lo stato con cui nasce un operatore creato dalla superficie Platform.
#
# `active` e non `invited`, e la ragione e' che non esiste un flusso di invito:
# nessuna email, nessun token di primo accesso, nessuna pagina di scelta
# password. Un operatore creato `invited` sarebbe quindi un conto che nessuno
# puo' completare, e il platform admin dovrebbe attivarlo con una seconda
# chiamata - attrito senza contropartita.
#
# `invited` resta comunque scegliibile esplicitamente, per chi vuole preparare
# un conto e attivarlo dopo. E' una scelta, non il comportamento predefinito.
OPERATOR_DEFAULT_STATUS = OPERATOR_ACTIVE

# Le lunghezze delle colonne in 027.
OPERATOR_EMAIL_MAX = 320
OPERATOR_NAME_MAX = 100

# LA PASSWORD NON HA REGOLE DI FORMA IN P27-3, E NON NE HA NESSUNA ALTROVE.
#
# Una prima stesura imponeva qui una lunghezza minima e una massima. Sono state
# tolte: `operator_auth` non ne ha - `LoginRequest.password` e' un `str` senza
# vincoli, `hash_password` accetta qualunque stringa, e il solo CHECK della 027
# riguarda il formato dell'HASH, non della password. Un limite introdotto qui
# sarebbe quindi stata la politica password dell'intero prodotto, decisa da
# dentro una fase che non se ne occupa e applicata alla sola superficie
# Platform.
#
# Il gap resta aperto ed e' dichiarato come rischio: non esiste oggi nessuna
# regola condivisa, quindi nemmeno la stringa vuota e' rifiutata da qualcosa.
# Va deciso insieme al flusso di invito, prima che si aprano affiliati veri.
#
# Cio' che P27-3 garantisce comunque, e che non e' una politica: nessun
# plaintext persistito, hashing esclusivamente con
# `operator_auth.security.hash_password`, e password mai in audit, log o
# risposta.

OPERATOR_NOT_FOUND_MESSAGE = "Operatore non trovato."
MEMBERSHIP_NOT_FOUND_MESSAGE = "Membership non trovata per questa agenzia."

# 409. Ogni messaggio nomina il conflitto e mai il vincolo di database che lo
# ha prodotto: il nome di un constraint dice a un chiamante come e' fatto lo
# schema.
EMAIL_TAKEN_MESSAGE = "Email gia' registrata per un altro operatore."
MEMBERSHIP_EXISTS_MESSAGE = (
    "Esiste gia' una membership fra questo operatore e questa agenzia: "
    "modificarne ruolo o stato invece di crearne una seconda."
)
ACTIVE_MEMBERSHIP_ELSEWHERE_MESSAGE = (
    "L'operatore ha gia' una membership attiva in un'altra agenzia."
)
OWNER_ALREADY_EXISTS_MESSAGE = "L'agenzia ha gia' un titolare attivo."
OWNER_ROLE_NEEDS_TRANSFER_MESSAGE = (
    "Il ruolo di titolare si assegna dall'endpoint di trasferimento titolare."
)
OWNER_MUST_BE_ACTIVE_MEMBER_MESSAGE = (
    "Il nuovo titolare deve avere una membership attiva in questa agenzia."
)
EMPTY_PATCH_MESSAGE = "Indicare almeno un campo da aggiornare."

# 409. Una password inviata insieme a un'email che identifica gia' una persona
# e' un payload di creazione credenziale in conflitto con un'identita' globale
# esistente. Rifiutata esplicitamente e non ignorata: ignorarla lascerebbe
# credere di aver impostato una credenziale che non e' stata toccata, e
# applicarla trasformerebbe questa route in un reimposta-password implicito.
PASSWORD_ON_EXISTING_IDENTITY_MESSAGE = (
    "L'email identifica un operatore gia' esistente: la sua credenziale non "
    "si imposta da qui. Ripetere la richiesta senza il campo."
)

# 422. Una persona nuova non puo' esistere senza credenziale: `password_hash`
# e' NOT NULL con un CHECK sul formato. Non e' un conflitto di stato - non c'e'
# nulla con cui confliggere - e' un dato mancante per il percorso che la
# richiesta ha imboccato.
PASSWORD_REQUIRED_MESSAGE = (
    "L'email identifica una persona nuova: indicare una password per crearla."
)


# ---------------------------------------------------------------------------
# P27-4 - CONFIGURAZIONE AGENZIA
#
# `agencies.settings` esiste dalla 027 come JSONB libero, e la 027 stessa lo
# dichiarava un contenitore in attesa: "P26-1 defines no key in it and reads it
# nowhere". L'audit di P27-4 lo conferma - nessun modulo legge una chiave la'
# dentro, e le uniche che comparivano erano valori di prova nei test di P27-2.
#
# P27-4 non aggiunge quindi una colonna: da' un CONTRATTO a quella che c'e'.
# ---------------------------------------------------------------------------

ACTION_AGENCY_CONFIGURATION_UPDATE = "platform.agency.configuration.update"

# I campi che la configurazione possiede. Tutto cio' che sta in `settings` e
# non e' qui dentro e' roba legacy: non viene esposta, non viene cancellata.
CONFIGURATION_FIELDS = ("timezone", "locale")

# Il fuso orario, come identificatore IANA. Validato con `zoneinfo`, che e'
# nella standard library: nessun elenco mantenuto a mano, che invecchierebbe a
# ogni revisione del database dei fusi.
CONFIGURATION_DEFAULT_TIMEZONE = "Europe/Rome"

# I locale ammessi. UNO SOLO OGGI, ed e' una scelta e non una svista: il
# prodotto e' italiano e nel repository non esiste evidenza di un secondo
# locale servito da qualcuno. Una tupla chiusa con un valore dice esattamente
# questo, e allargarla e' una riga - ma una riga che qualcuno deve decidere di
# scrivere, invece di un campo libero in cui finisce quel che capita.
CONFIGURATION_DEFAULT_LOCALE = "it-IT"
AGENCY_LOCALES = (CONFIGURATION_DEFAULT_LOCALE,)

# I valori applicativi quando la riga non dice nulla. Vivono qui e NON nel
# database: la 027 ha messo `DEFAULT '{}'` e ogni agenzia esistente ha quel
# valore, quindi applicare i default in lettura evita un backfill e lascia una
# sola sorgente di verita' per cosa significhi "non configurato".
CONFIGURATION_DEFAULTS = {
    "timezone": CONFIGURATION_DEFAULT_TIMEZONE,
    "locale": CONFIGURATION_DEFAULT_LOCALE,
}

CONFIGURATION_EMPTY_PATCH_MESSAGE = (
    "Indicare almeno un campo di configurazione da aggiornare."
)

# 500. Una chiave conosciuta e' presente nel JSONB con un valore che il
# contratto non accetta: la configurazione persistita e' corrotta, e non e' un
# errore del chiamante.
#
# Il messaggio non nomina il campo e non riporta il valore. Chi riceve questo
# 500 non ha fatto nulla di sbagliato e non puo' farci niente; chi puo' e' un
# amministratore che guardera' la riga, e a lui il valore lo dice il database,
# non una risposta HTTP.
CONFIGURATION_CORRUPTED_MESSAGE = (
    "Configurazione dell'agenzia non leggibile."
)
