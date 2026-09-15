"""The closed sets of the CONSENT domain.

Ogni insieme qui e' chiuso di proposito: allargarne uno e' una riga, ma una
riga che qualcuno deve decidere di scrivere, invece di un campo libero in cui
finisce quel che capita. Stesso principio di platform_admin/enums.py
(AGENCY_LOCALES) e di operator_auth/context.py (SYSTEM_CONTEXT_ORIGINS).

Dove un insieme e' anche un CHECK nel database, le due liste sono tenute
identiche e un test le confronta, cosi' un valore aggiunto da una parte sola
fallisce in suite invece di essere rifiutato dal database nel momento peggiore
(stessa disciplina di platform_admin/enums.AUDIT_RESULTS vs migration 057).
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# GLI SCOPI
#
# Due, e nessun terzo. Rispecchiano migrations/061 e 062.
#
# `privacy_terms` NON si chiama `privacy_consent`. Il nome dice cosa il funnel
# raccoglie davvero: l'accettazione del blocco Privacy Policy + Termini d'Uso +
# Istruzioni eliminazione dati, necessaria per usare il servizio. La semantica
# esatta e' determinata dal frontend di stima360.it, che vive fuori da questo
# repository: qui non viene inventata.
#
# `marketing` copre Email e WhatsApp INSIEME. Non esistono `marketing_email` e
# `marketing_whatsapp`, e il modello non deve rendere quella separazione
# nemmeno rappresentabile.
# ---------------------------------------------------------------------------
PURPOSE_PRIVACY_TERMS = "privacy_terms"
PURPOSE_MARKETING = "marketing"
PURPOSES = frozenset({PURPOSE_PRIVACY_TERMS, PURPOSE_MARKETING})

# ---------------------------------------------------------------------------
# LE DECISIONI
#
# Due, e nessuna terza. In particolare non esiste un valore per "non ha
# spuntato la casella": quello non e' una decisione, e' l'assenza di una.
# ---------------------------------------------------------------------------
DECISION_GRANTED = "granted"
DECISION_REVOKED = "revoked"
DECISIONS = frozenset({DECISION_GRANTED, DECISION_REVOKED})

# ---------------------------------------------------------------------------
# GLI STATI CORRENTI
#
# Tre, perche' `never_given` e `revoked` sono cose diverse. Entrambi bloccano
# un invio marketing, ma uno non ha mai detto si' e l'altro ha detto si' e poi
# no. La distinzione serve all'operatore che guarda la scheda e a chi legge
# l'audit; perderla sarebbe perdere l'unica informazione che spiega perche' il
# contatto e' dov'e'.
#
# `never_given` non e' mai un valore persistito: e' cio' che la derivazione
# restituisce quando non c'e' niente.
# ---------------------------------------------------------------------------
STATUS_GRANTED = "granted"
STATUS_REVOKED = "revoked"
STATUS_NEVER_GIVEN = "never_given"
STATUSES = frozenset({STATUS_GRANTED, STATUS_REVOKED, STATUS_NEVER_GIVEN})

# ---------------------------------------------------------------------------
# GLI ATTORI
#
# `subject`  - la persona stessa: form pubblico, link di disiscrizione, STOP
#              su WhatsApp.
# `operator` - un umano nel CRM. Sempre tracciato con la propria identita':
#              il database rifiuta un evento `operator` senza `actor_ref`
#              (consent_events_operator_ref_chk).
# `system`   - import, migrazione, processo. Ammesso per una concessione
#              dichiarata tale, MAI per una revoca: il database la rifiuta
#              (consent_events_revoked_actor_chk), perche' una revoca che
#              nessuno ha voluto e' una perdita di consenso che nessuno puo'
#              spiegare.
# ---------------------------------------------------------------------------
ACTOR_SUBJECT = "subject"
ACTOR_OPERATOR = "operator"
ACTOR_SYSTEM = "system"
ACTOR_TYPES = frozenset({ACTOR_SUBJECT, ACTOR_OPERATOR, ACTOR_SYSTEM})

# Gli attori che possono revocare. Ripetuto qui come insieme proprio perche' il
# servizio lo controlla PRIMA di scrivere: un errore del chiamante deve
# diventare un ValidationError leggibile, non una IntegrityError del driver.
REVOKING_ACTOR_TYPES = frozenset({ACTOR_SUBJECT, ACTOR_OPERATOR})

# ---------------------------------------------------------------------------
# LE PROVENIENZE
#
# Chiuso qui, LIBERO nel database (`consent_events_source_chk` impone solo che
# non sia vuoto). L'asimmetria e' voluta: un canale nuovo - un secondo form,
# un portale, un import una tantum - non deve richiedere una migration per
# essere registrato, ma deve richiedere una riga qui, cioe' una revisione.
#
# `legacy` non e' una provenienza che si scrive: e' cio' che l'API espone per i
# consensi raccolti prima che questo registro esistesse (12 contatti, censimento
# P29-1.0). Nessun evento viene creato per loro e nessun timestamp viene
# inventato.
# ---------------------------------------------------------------------------
# La DENOMINAZIONE APPLICATIVA del flusso, non l'hostname del sito.
#
# La prima stesura diceva "stima360.it". E' stata cambiata in P29-1.4 perche'
# un hostname dice DOVE stava la persona, non COSA ha fatto: lo stesso sito
# potrebbe un giorno ospitare un secondo form, e due consensi diversi
# porterebbero la stessa provenienza. `public_stima` e' invece il nome che
# l'origine STIMA -> CORE ha gia' in tutto il repository - `contacts.source`,
# `leads.source` e `SystemAgencyContext.origin` scrivono esattamente questo -
# quindi un lettore che incrocia le due tabelle trova la stessa parola.
#
# Nessuna migration: la colonna e' un VARCHAR libero (solo BTRIM <> '' la
# vincola) e nessun evento e' ancora stato scritto con il valore precedente.
SOURCE_PUBLIC_STIMA = "public_stima"
SOURCE_CRM = "crm"
SOURCE_UNSUBSCRIBE_LINK = "unsubscribe_link"
SOURCE_WHATSAPP_OPTOUT = "whatsapp_optout"
SOURCE_IMPORT = "import"
SOURCE_LEGACY = "legacy"

WRITABLE_SOURCES = frozenset({
    SOURCE_PUBLIC_STIMA,
    SOURCE_CRM,
    SOURCE_UNSUBSCRIBE_LINK,
    SOURCE_WHATSAPP_OPTOUT,
    SOURCE_IMPORT,
})

# ---------------------------------------------------------------------------
# I MOTIVI DELLA GUARDIA DI INVIO (P29-1.5)
#
# `can_send_marketing` non restituisce un booleano nudo: un "no" senza motivo
# non si puo' ne' spiegare a un operatore ne' diagnosticare sei mesi dopo. I
# valori sono stabili - finiranno in log e, un giorno, sulla riga di un
# messaggio non partito - quindi si aggiungono, non si rinominano.
#
# Due permessi e tre rifiuti, e nessun sesto caso: lo stato del consenso ha
# tre forme (granted, revoked, never_given), il permesso si distingue fra
# esplicito e legacy, e tutto cio' che non torna e' un rifiuto.
# ---------------------------------------------------------------------------
REASON_ALLOW_EXPLICIT_GRANT = "allow_explicit_grant"
REASON_ALLOW_LEGACY_GRANT = "allow_legacy_grant"
REASON_DENY_REVOKED = "deny_revoked"
REASON_DENY_NEVER_GIVEN = "deny_never_given"
REASON_DENY_INCONSISTENT_STATE = "deny_inconsistent_state"

SEND_DECISION_REASONS = frozenset({
    REASON_ALLOW_EXPLICIT_GRANT,
    REASON_ALLOW_LEGACY_GRANT,
    REASON_DENY_REVOKED,
    REASON_DENY_NEVER_GIVEN,
    REASON_DENY_INCONSISTENT_STATE,
})

ALLOWING_REASONS = frozenset({
    REASON_ALLOW_EXPLICIT_GRANT,
    REASON_ALLOW_LEGACY_GRANT,
})

# ---------------------------------------------------------------------------
# LE COLONNE DI PROIEZIONE, PER SCOPO
#
# Nomi di colonna, cioe' letterali di sviluppatore: non arrivano mai da una
# richiesta. Vivono in una mappa chiusa e non in una f-string costruita dal
# `purpose`, cosi' un valore inatteso non puo' diventare SQL - la stessa
# ragione per cui core/scope.py controlla `table` contro SCOPED_TABLES prima di
# interpolarlo.
#
# `granted_at` del marketing e' `marketing_consent_at`, che esiste dalla
# migration 001 e NON viene rinominata: P24 database_revival e tutto il codice
# esistente continuano a leggere le colonne che hanno sempre letto.
# ---------------------------------------------------------------------------
PROJECTION_COLUMNS = {
    PURPOSE_MARKETING: {
        "flag": "marketing_consent",
        "granted_at": "marketing_consent_at",
        "revoked_at": "marketing_revoked_at",
        "source": "marketing_consent_source",
        "notice_id": "marketing_consent_notice_id",
    },
    PURPOSE_PRIVACY_TERMS: {
        "flag": "privacy_terms_accepted",
        "granted_at": "privacy_terms_accepted_at",
        "revoked_at": "privacy_terms_revoked_at",
        "source": "privacy_terms_source",
        "notice_id": "privacy_terms_notice_id",
    },
}
