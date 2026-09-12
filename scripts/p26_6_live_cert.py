#!/usr/bin/env python3
"""P26-6 - matrice ostile A/B, live, su TEST soltanto.

    python scripts/p26_6_live_cert.py --approved-commit <SHA>

CHE COSA PROVA, E PERCHE' NON BASTA IL RESTO

Tutto il lavoro di P26 fino a qui dimostra che il CODICE e' scritto per isolare
le agenzie: ogni query porta un predicato, ogni dipendenza deriva il tenant dal
server, nessun client puo' chiedere un'agenzia diversa dalla propria. Sono
prove statiche e offline, e sono la difesa quotidiana.

Non dicono se l'isolamento REGGE. Un predicato puo' essere corretto e la
gerarchia dei dati incoerente; una JOIN puo' essere scopata e la successiva no;
una riga puo' esistere con due radici che si contraddicono. Queste cose si
vedono solo interrogando un database vero con due operatori veri di due agenzie
diverse, e provando a rubare.

E' quello che fa questo script: due identita' reali, un'agenzia ciascuna, e per
ogni dominio di tenant la stessa domanda in entrambe le direzioni - A vede A?
B vede B? A vede B? B vede A? A modifica B? B modifica A? - piu' i modi
obliqui: l'ID diretto di un'altra agenzia, le liste, le ricerche, i lookup, i
percorsi che attraversano una relazione.

CHE COSA NON PROVA

Non e' un test di concorrenza: le richieste sono sequenziali. Non e' un test di
carico. E non certifica il codice sorgente - quello lo fanno i prover offline -
ma il comportamento di un'istanza in esecuzione su dati reali.

TRE PRINCIPALI, NON UNO

La matrice si crea due identita' `agency_admin`, una per agenzia. Non bastano
per due superfici, e per ragioni opposte:

* OWNER Admin esige `agency_owner`. migrations/027 ne ammette UNO SOLO attivo
  per agenzia, quindi la matrice non puo' crearsene: apre due sessioni
  temporanee agganciate ai titolari che gia' esistono - nessuna password letta
  o cambiata, nessuna membership toccata - e le cancella per ID alla fine.
* Il portale proprietari autentica un principale diverso, con un cookie suo. La
  matrice si costruisce un proprietario per agenzia attraverso l'API di OWNER
  Admin e interroga il portale come farebbe lui.

Interrogare una di queste due con l'identita' sbagliata produce un rifiuto che
SEMBRA isolamento e non lo e': e' esattamente l'errore che questa revisione
corregge.

CHE COSA SCRIVE, E CHE COSA NON SCRIVE

Scrive solo fixture proprie: contatto, immobile, richiesta d'acquisto, e da
questi la catena commerciale (match, proposta, vendita) piu' un conto
proprietario con la sua concessione. Non modifica una sola riga preesistente.

Le stime fanno eccezione al modo, non alla regola: non si creano da questa API
- il funnel pubblico risolve l'agenzia lato server - quindi vengono LETTE, e
l'isolamento si osserva su quelle senza scriverci.

REGOLE DI CONDOTTA

* Rifiuta qualunque database che non sia `stima360_db_test`, e verifica la
  connessione reale oltre alla variabile.
* Ogni run ha un `run_id`; le fixture nascono con quel marchio e il cleanup
  cancella per ID, mai per prefisso: due certificazioni avviate insieme non si
  distruggono a vicenda.
* Un residuo trovato all'avvio ferma il run senza creare e senza cancellare
  nulla: da qui non si distingue un resto di ieri da un run in corso adesso.
* Cleanup in `finally`, nell'ordine imposto dalle chiavi esterne. Qualunque
  cleanup incompleto e' FAIL.
* Qualunque prova non eseguita e' BLOCKED, mai PASS. Una lista vuota non e' una
  prova di isolamento, e un dominio senza dati risulta non provato.
* Uscita 0 solo con tutte le prove PASS.
* Nessuna password, cookie, token o DSN stampato. L'unica risposta che puo'
  contenere un token e' quella che lo emette, per contratto, ed e' registrata
  come tale: lo stesso token altrove resta una fuga.
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import re
import secrets
import ssl
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REQUIRED_DB_NAME = "stima360_db_test"
APPROVED_BRANCH = "core-0.1-test"

# operator_auth/enums.py
COOKIE_NAME = "stima360_operator_session"
DEFAULT_AGENCY_SLUG = "stima360"
# scripts/p26_seed_agencies_test.py
AGENCY_B_SLUG = "agenzia-b-test"

# migrations/027: un solo agency_owner attivo per agenzia, quindi le identita'
# di certificazione non possono esserlo. `agency_admin` e' la soglia piu' alta
# disponibile senza collidere, e vede tutti i record dell'agenzia (matrice dei
# permessi: "See all agency records" YES per agency_admin) - che e' cio' che
# serve a una prova di isolamento: se anche il ruolo piu' capace non vede
# l'altra agenzia, nessuno la vede.
CERT_ROLE = "agency_admin"
CERT_PREFIX = "p26-6-cert-"
CERT_DOMAIN = "@certification.invalid"
CERT_EMAIL_LIKE = CERT_PREFIX + "%" + CERT_DOMAIN

# property_watch/repository.py - `ensure_watch_with_baseline_scoped`, cioe' la
# funzione dietro `POST /api/property-watch/stime/{id}/initialize`, scrive UNA
# osservazione insieme al watch, nella stessa transazione. Tipo, sorgente e
# chiave sono costanti nel repository, e la chiave si DERIVA dallo stima_id:
#
#     INSERT INTO property_watch_observations (
#         watch_id, observation_type, source, payload, idempotency_key
#     ) VALUES (%s, 'watch_started', 'internal', %s, %s)
#
# Sono qui, e non dentro Cert, perche' il test di regressione li confronta con
# il sorgente del repository: se un giorno la chiave cambia forma, il confronto
# fallisce invece di lasciare il cleanup a cercare righe che non riconosce piu'.
WATCH_BASELINE_TYPE = "watch_started"
WATCH_BASELINE_SOURCE = "internal"
WATCH_BASELINE_KEY = "property_watch:watch_started:stima:{stima_id}:v1"

# operator_auth/dependencies.py: la soglia di OWNER Admin. `agency_admin` non
# la raggiunge, e questo e' il motivo per cui la matrice non puo' provare quella
# superficie con le proprie identita' - vedi OwnerSessions.
OWNER_ADMIN_MIN_ROLE = "agency_owner"

# owner/enums.py - il portale proprietari autentica un principale DIVERSO, con
# un cookie suo. Tenerli distinti non e' pedanteria: un jar che portasse
# entrambi renderebbe impossibile sapere quale identita' ha risposto.
OWNER_COOKIE_NAME = "stima360_owner_session"

LOGIN = "/api/operator-auth/login"
LOGOUT = "/api/operator-auth/logout"
ME = "/api/operator-auth/me"
PUBLIC = "/api/public/contatore_oggi"

# OWNER Admin - owner/router_admin.py
OWNER_ACCOUNTS = "/api/owner/admin/accounts"
OWNER_ACCESS = "/api/owner/admin/access"
# OWNER Portal - owner/router_portal.py
PORTAL_LOGIN = "/api/owner/portal/auth/token"
PORTAL_LOGOUT = "/api/owner/portal/auth/logout"
PORTAL_PROPERTIES = "/api/owner/portal/properties"

NOT_AUTHENTICATED = "Non autorizzato"

PASS, FAIL, BLOCKED = "PASS", "FAIL", "BLOCKED"


# Uno stato "l'altra agenzia non esiste per te" e' accettabile in tre forme.
# 200 non lo e' mai; 500 nemmeno, perche' un errore interno puo' nascondere una
# query che ha comunque toccato la riga.
NEUTRAL_REFUSALS = (403, 404)


class GuardFailure(Exception):
    """Una precondizione che vieta di procedere del tutto."""


class CheckFailed(Exception):
    """Una prova fallita. Interrompe il run; il cleanup avviene comunque."""


# ---------------------------------------------------------------------------
# L'INVENTARIO DEI DOMINI - il cuore della matrice
# ---------------------------------------------------------------------------
#
# Ogni voce descrive un dominio di tenant e come interrogarlo. Il prover
# offline (`tests/test_p26_6_live_cert_script.py`) confronta questa struttura
# con l'inventario delle route ricavato dal codice: un dominio montato e non
# elencato qui fa fallire la suite, cosi' la completezza non dipende dalla
# memoria di chi scrive.
#
# `fixture` dice come nascere una risorsa di quel dominio per conto
# dell'operatore che chiama - ed e' l'unico modo in cui questo script scrive:
# mai su dati preesistenti.


class Domain:
    """Un dominio di tenant, e come metterlo alla prova nelle due direzioni."""

    def __init__(self, name, prefix, *, listing=None, search=None,
                 fixture=None, depends_on=None, detail=None, update=None,
                 delete=None, cross_links=(), chain=None, derive=None,
                 principal="operator", certifier=None, api_delete=True,
                 table=None, marker_column=None, note=""):
        self.name = name
        self.prefix = prefix
        self.listing = listing            # GET che elenca: deve mostrare solo le proprie
        self.search = search              # GET con ricerca libera: idem
        self.fixture = fixture            # (path, payload) per creare una risorsa propria
        self.depends_on = depends_on      # dominio da cui prendere un id per il payload
        self.detail = detail              # "/api/x/{id}" - ID diretto dell'altra agenzia
        self.update = update              # (metodo, "/api/x/{id}", payload) - scrittura
        self.delete = delete              # ("POST"|"DELETE", "/api/x/{id}") - distruttiva
        self.cross_links = cross_links    # percorsi che attraversano una relazione

        # LA DELETE ESISTE DAVVERO?
        #
        # Il cleanup dava per scontato che ogni dominio con un `detail` avesse
        # anche una DELETE sullo stesso percorso. CORE no: il router ha
        # POST/GET/PATCH sui contatti e nessuna cancellazione. Ogni run
        # riceveva 405 e lasciava due contatti sul TEST.
        #
        # Il prover offline confronta questo valore con il router reale, cosi'
        # che non sia una dichiarazione da tenere aggiornata a mano.
        #
        # ATTENZIONE AL SIGNIFICATO: "la DELETE RIMUOVE FISICAMENTE la riga".
        # Non "la route DELETE esiste". PROPERTY e BUY ce l'hanno, e i loro
        # handler si chiamano `archive_property` e `archive_request`: fanno
        # UPDATE ... SET archived_at=NOW(). Il run 22d007af7916 ha ricevuto
        # 200, ha creduto al codice di stato e ha lasciato quattro righe sul
        # TEST senza segnalarle.
        self.api_delete = api_delete

        # Dove vive la riga, e in quale colonna il marcatore: servono al
        # cleanup SQL dei domini che l'API non sa cancellare davvero.
        self.table = table
        self.marker_column = marker_column

        # UNA RISORSA CHE NASCE DA PIU' CHIAMATE.
        #
        # `fixture` copre il caso semplice: una POST, un id. MATCH, PROPOSAL e
        # SALE non stanno in quella forma - il primo si calcola da due risorse
        # esistenti, gli altri due nascono da cio' che li precede - e per un
        # intero giro di revisione questo li ha fatti dichiarare "non
        # applicabili". Non lo sono: la stessa API produce gli id a monte, e
        # `chain` e' il nome dei passi che li producono.
        self.chain = chain

        # UNA RISORSA CHE NON SI CREA E SI TROVA.
        #
        # Le stime nascono dal funnel pubblico, che risolve l'agenzia lato
        # server: da questa API non se ne crea una per l'agenzia B. Cio' che si
        # puo' fare e' LEGGERE quali gia' esistono, per agenzia, e provare
        # l'isolamento su quelle - senza scrivere una riga su dati preesistenti.
        self.derive = derive

        # Chi interroga questa superficie: "operator" (le due identita' della
        # matrice), "agency_owner" (OWNER Admin, che ha una soglia piu' alta) o
        # "owner" (il portale, che autentica un principale del tutto diverso).
        self.principal = principal

        # Il nome della funzione `certify_<...>` che prova questo dominio,
        # quando le sei domande generiche non bastano. Il prover offline
        # verifica che la funzione esista davvero: una stringa che non risolve
        # sarebbe copertura scritta e mai eseguita.
        self.certifier = certifier
        self.note = note


DOMAINS = (
    Domain(
        "CORE", "/api/core",
        listing="/api/core/contacts?limit=50",
        search="/api/core/contacts?search={marker}&limit=50",
        fixture=("/api/core/contacts", {"display_name": "{marker}", "status": "active"}),
        detail="/api/core/contacts/{id}",
        update=("PATCH", "/api/core/contacts/{id}", {"status": "archived"}),
        cross_links=("/api/core/leads?contact_id={id}&limit=50",),
        # core/router.py non ha `@router.delete("/contacts/{contact_id}")`:
        # esiste solo la DELETE di un RUOLO. I contatti di questo run si
        # rimuovono quindi via SQL, per id verificati anche su marcatore e
        # agenzia - vedi cleanup_orphan_fixtures.
        api_delete=False,
        table="contacts", marker_column="display_name",
        note="contatti, lead, attivita' e task: la radice di tutto il resto",
    ),
    Domain(
        "PROPERTY", "/api/property",
        listing="/api/property/properties?limit=50",
        search="/api/property/properties?search={marker}&limit=50",
        # `commercial_status` attivo non e' un dettaglio estetico:
        # match/readiness.py esige uno stato in ACTIVE_PROPERTY_STATUSES perche'
        # l'immobile sia eleggibile al calcolo, e senza calcolo non esistono
        # MATCH, PROPOSAL e SALE da provare.
        fixture=("/api/property/properties",
                 {"title": "{marker}", "commercial_status": "active"}),
        detail="/api/property/properties/{id}",
        update=("PATCH", "/api/property/properties/{id}", {"title": "{marker}-mod"}),
        # `cross_links=("/api/property/properties/{id}/visits?limit=20",)` era
        # sbagliato: quella GET NON ESISTE. Il router ha `POST
        # /properties/{id}/visits` e `GET /visits` (senza filtro per immobile),
        # quindi la sonda riceveva 405 - e un 405 non e' isolamento, e' una
        # route inventata. La matrice lo contava come prova superata.
        #
        # La relazione vera che si puo' attraversare da un immobile e' il
        # legame con un contatto, ed e' una SCRITTURA: vedi certify_property.
        certifier="property",
        # `DELETE /properties/{id}` esiste e risponde 200, ma l'handler si
        # chiama `archive_property` e fa UPDATE ... commercial_status='archived',
        # archived_at=NOW(). La riga resta.
        api_delete=False,
        table="properties", marker_column="title",
    ),
    Domain(
        "BUY", "/api/buy",
        listing="/api/buy/requests?limit=50",
        search="/api/buy/requests?search={marker}&limit=50",
        # La richiesta d'acquisto nasce da un contatto: la fixture prende l'id
        # dal contatto che lo stesso operatore ha appena creato in CORE. E' la
        # catena vera, non un id inventato - e rende la sonda sull'ID diretto
        # una prova forte invece che un 404 ambiguo.
        # `status` attivo e un criterio effettivo (`budget_target`) sono cio'
        # che match/readiness.py esige perche' la richiesta sia calcolabile.
        # Senza, il MATCH non nasce e con lui non nascono PROPOSAL e SALE.
        fixture=("/api/buy/requests",
                 {"contact_id": "{core_id}", "title": "{marker}",
                  "status": "active", "budget_target": 250000}),
        depends_on="CORE",
        detail="/api/buy/requests/{id}",
        update=("PATCH", "/api/buy/requests/{id}", {"title": "{marker}-mod"}),
        # Come PROPERTY: l'handler e' `archive_request`, UPDATE status='archived'.
        api_delete=False,
        table="buy_requests", marker_column="title",
    ),
    # I TRE DOMINI DELLA CATENA COMMERCIALE.
    #
    # Per un intero giro di revisione sono stati "non applicabili": PROPOSAL
    # richiede un `match_id`, SALE un `proposal_id`, e MATCH non ha una POST di
    # creazione. Il ragionamento era vero nei fatti e sbagliato nella
    # conclusione - "richiede un id a monte" non e' un ostacolo finche' e' LA
    # STESSA API a produrlo:
    #
    #   POST /api/match/calculate                 buy + property propri -> match
    #   POST /api/proposals                       match -> proposta
    #   POST /api/proposals/{id}/transition       draft -> submitted -> accepted
    #   POST /api/sales                           proposta accettata -> vendita
    #
    # Sei passi, tutti con risorse che questo run possiede. `chain` li descrive
    # e `certify_chain` li esegue. Un passo che fallisce non produce un PASS
    # piu' debole: rende BLOCKED tutto cio' che ne dipende, con lo stato HTTP
    # scritto nel report.
    Domain(
        "MATCH", "/api/match",
        listing="/api/match/matches?limit=50",
        chain="match",
        detail="/api/match/matches/{id}",
        update=("PATCH", "/api/match/matches/{id}", {"priority": "urgent"}),
        certifier="chain",
        note="calcolato da una richiesta e un immobile propri: la lista "
             "proietta buy_title e property_title, che portano il marcatore",
    ),
    Domain(
        "PROPOSAL", "/api/proposals",
        listing="/api/proposals?limit=50",
        chain="proposal",
        depends_on="MATCH",
        detail="/api/proposals/{id}",
        update=("PATCH", "/api/proposals/{id}", {"notes": "{marker}-mod"}),
        certifier="chain",
        note="nasce dal match di questo run; il marcatore vive in `notes`",
    ),
    Domain(
        "SALE", "/api/sales",
        listing="/api/sales?limit=50",
        chain="sale",
        depends_on="PROPOSAL",
        detail="/api/sales/{id}",
        update=("PATCH", "/api/sales/{id}", {"notes": "{marker}-mod"}),
        certifier="chain",
        note="nasce dalla proposta accettata di questo run",
    ),
    Domain(
        "CRM", "/api/crm",
        # Contact 360 prende l'id di un CONTATTO: quello lo possediamo, quindi
        # qui la sonda sull'ID diretto e' forte.
        detail="/api/crm/contacts/{id}/360",
        depends_on="CORE",
        note="Contact 360 attraversa contatti, lead, immobili e proposte: e' il "
             "punto in cui una JOIN non scopata si vedrebbe per prima",
    ),
    Domain(
        "SELLER_INTELLIGENCE", "/api/seller-intelligence",
        listing="/api/seller-intelligence/timeline?limit=50",
        # Una lista non vuota, da sola, non prova che B non veda A: prova
        # solo che c'e' qualcosa. Serve una risorsa RICONOSCIBILE dell'altra
        # agenzia. L'evento nasce sul contatto proprio, con il marcatore in
        # `event_type` (testo libero, max 50); il timeline si filtra per
        # contatto, quindi la sonda ostile e' "il timeline del contatto di B",
        # e la risposta corretta e' non vederne l'evento.
        fixture=("/api/seller-intelligence/events",
                 {"contact_id": "{core_id}", "event_type": "{marker}",
                  "event_source": "p26-6-cert", "payload": {}}),
        depends_on="CORE",
        cross_links=("/api/seller-intelligence/timeline?contact_id={id}&limit=50",),
        # Nessuna DELETE via API: la riga ha agency_id e va nel cleanup per
        # terna id+marcatore+agenzia.
        api_delete=False,
        table="seller_timeline_events", marker_column="event_type",
    ),
    Domain(
        "FOLLOWUP", "/api/followup",
        # `listing="/api/followup/pending?limit=50"` NON ESISTEVA. Il router
        # monta una sola route, `POST /scan-temporal`: l'assenza di GET rende
        # non provabili le LETTURE, non il dominio. La scansione e' una
        # scrittura scopata per agenzia, e una scrittura si prova cosi': ognuno
        # la lancia, e si osserva che tocchi solo le proprie righe.
        certifier="followup",
        note="POST /scan-temporal: escalation delle attivita' stale, provata con "
             "fixture proprie e guardia read-only sui dati preesistenti",
    ),
    Domain(
        "SELLER_INTENT", "/api/seller-intent",
        # Lo score si chiede per lead_id, e questo run non crea lead. La sonda
        # forte sarebbe l'ID diretto; qui resta il percorso che attraversa il
        # contatto posseduto dall'altra agenzia, che e' comunque una domanda
        # ostile vera: "dammi i lead di un contatto che non e' tuo".
        cross_links=("/api/core/leads?contact_id={id}&limit=50",),
        depends_on="CORE",
        note="lo score si chiede per lead_id: coperto dal percorso che passa "
             "per un contatto dell'altra agenzia",
    ),
    Domain(
        "PROPERTY_WATCH", "/api/property-watch",
        # Prima diceva `covered_by="LEGACY_ADMIN"`: una copertura per etichetta.
        # LEGACY_ADMIN elenca le stime attraverso `/api/admin/stime`, che e' una
        # route diversa, con una query diversa, in un file diverso. Provare
        # quella non dice niente su `/api/property-watch/stime/{id}`.
        #
        # Le stime pero' non si creano da questa API - il funnel pubblico
        # risolve l'agenzia lato server - quindi la fixture si DERIVA: si
        # legge, in sola lettura, quale stima ciascuna agenzia gia' possiede, e
        # si prova l'isolamento su quelle. Se un'agenzia non ne ha, la prova e'
        # BLOCKED e non PASS.
        derive="stime",
        detail="/api/property-watch/stime/{id}",
        certifier="property_watch",
        note="stime derivate in sola lettura: nessun dato preesistente viene "
             "creato o modificato da questo run",
    ),
    Domain(
        "NEXT_BEST_ACTION", "/api/next-best-action",
        # La lista si popola solo con `POST /refresh`, che ricalcola E POTA le
        # azioni dell'intero tenant: su un'agenzia condivisa toccherebbe righe
        # altrui. Provabile sulle agenzie dedicate, dove ogni riga e' del run.
        certifier="batch_only",
        note="materializzato da un refresh che percorre il tenant: provato "
             "sulle agenzie dedicate",
    ),
    Domain(
        "FLOW", "/api/flow",
        listing="/api/flow/executions?limit=50",
        # IL MARCATORE NON PUO' COMPARIRE IN UN'ESECUZIONE, e per un giro
        # intero questo ha reso la riga BLOCKED senza che si capisse perche'.
        #
        # `flow_executions` proietta `rule_code`, `entity_type`, `entity_id`,
        # `status` e gli snapshot dei parametri: nessuno di questi campi
        # contiene testo scelto da chi crea la risorsa. Le sei domande
        # generiche cercano `cert.marker(other)` nel corpo, e qui quella
        # ricerca sarebbe vera SEMPRE - anche a isolamento rotto. Un PASS
        # ottenuto cosi' e' peggio del BLOCKED che sostituirebbe.
        #
        # `certify_flow` fa le stesse domande con gli ID delle esecuzioni, che
        # in questo dominio sono il riconoscimento giusto e piu' severo di una
        # sottostringa.
        certifier="flow",
        note="l'esecuzione nasce da un evento su una risorsa del run; il "
             "riconoscimento e' per id, perche' un'esecuzione non porta testo "
             "scelto da chi la crea",
    ),
    Domain(
        "OWNER_ADMIN", "/api/owner/admin",
        # LA SOGLIA NON E' LA PROVA.
        #
        # Prima qui c'era solo un 403: le identita' della matrice sono
        # `agency_admin`, OWNER Admin esige `agency_owner`, quindi il rifiuto
        # arrivava prima di ogni domanda sullo scope. Un 403 su entrambe le
        # agenzie e' compatibile con qualunque cosa accada dietro la soglia.
        #
        # 027 ammette UN SOLO agency_owner attivo per agenzia, quindi la
        # matrice non puo' crearsene uno: usa gli owner TEST che gia' esistono e
        # apre per loro due sole SESSIONI temporanee - nessuna password toccata,
        # nessuna membership toccata, nessun utente creato. Vedi OwnerSessions.
        listing=OWNER_ACCOUNTS,
        principal=OWNER_ADMIN_MIN_ROLE,
        certifier="owner_admin",
        note="interrogato da due sessioni agency_owner temporanee agganciate "
             "agli owner reali di TEST; il 403 di agency_admin resta come "
             "prova della soglia di ruolo",
    ),
    Domain(
        "OWNER_PORTAL", "/api/owner/portal",
        # Assente dalla matrice fino a questa revisione, con la motivazione che
        # "non e' una superficie da operatore". E' vero e non c'entra: e' una
        # superficie di TENANT - un proprietario vede immobili che appartengono
        # a un'agenzia - e il principale diverso e' un motivo per provarla in
        # modo diverso, non per non provarla.
        principal="owner",
        depends_on="OWNER_ADMIN",
        detail="/api/owner/portal/properties/{id}",
        certifier="owner_portal",
        note="il proprietario autentica con un token monouso emesso da OWNER "
             "Admin e un cookie proprio; vede solo gli immobili concessi",
    ),
    Domain(
        "LEGACY_ADMIN", "/api/admin",
        listing="/api/admin/stime?day=oggi",
        note="le sette route /api/admin che toccano dati di tenant, migrate "
             "alla sessione da P26-5",
    ),
)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

class Report:
    """Il verdetto, e le righe che lo giustificano."""

    def __init__(self, stream=None) -> None:
        self.rows: list[tuple[str, str, str]] = []
        self._stream = stream if stream is not None else sys.stdout

    def record(self, kind: str, ident: str, text: str) -> None:
        self.rows.append((kind, ident, text))
        print(f"{kind:<8} {ident:<22} {text}", file=self._stream, flush=True)

    def check(self, ident: str, condition: bool, text: str) -> None:
        if condition:
            self.record(PASS, ident, text)
            return
        self.record(FAIL, ident, text)
        raise CheckFailed(ident)

    def note(self, ident: str, text: str) -> None:
        self.record(PASS, ident, text)

    def fail(self, ident: str, text: str) -> None:
        """Fallimento che NON interrompe. Il cleanup deve proseguire su tutte
        le risorse anche quando una gli e' sfuggita."""
        self.record(FAIL, ident, text)

    def blocked(self, ident: str, text: str) -> None:
        self.record(BLOCKED, ident, text)

    def count(self, kind: str) -> int:
        return sum(1 for k, _, _ in self.rows if k == kind)

    @property
    def exit_code(self) -> int:
        """1 se qualcosa e' fallito, 2 se qualcosa e' rimasto BLOCKED, 0 solo
        se ogni prova e' stata eseguita ed e' passata.

        Il FAIL precede il BLOCKED: un run con entrambi e' fallito, non
        incompleto.
        """
        if self.count(FAIL):
            return 1
        if self.count(BLOCKED):
            return 2
        return 0

    @property
    def verdict(self) -> str:
        return {
            1: "FAIL",
            2: "INCOMPLETO - prove BLOCKED, la matrice non e' chiusa",
            0: "PASS - matrice ostile A/B superata su TEST",
        }[self.exit_code]

    def summary(self) -> None:
        print("=" * 78, file=self._stream)
        print(f"PASS {self.count(PASS)}   FAIL {self.count(FAIL)}   "
              f"BLOCKED {self.count(BLOCKED)}", file=self._stream)
        print(f"ESITO: {self.verdict}", file=self._stream)
        if self.exit_code == 0:
            print("", file=self._stream)
            print("GATE-MA1 resta APERTO finche' questo esito non e' registrato",
                  file=self._stream)
            print("e LIVE_HOSTILE_MATRIX_PASSED non e' impostato a mano.",
                  file=self._stream)
        print("=" * 78, file=self._stream, flush=True)


# ---------------------------------------------------------------------------
# Guardia sul database
# ---------------------------------------------------------------------------

def assert_certification_database(name: str | None) -> str:
    """Ritorna `name` solo se e' il database di certificazione.

    Nessun marcatore, nessuna euristica: un solo nome ammesso. Uno script che
    crea identita' e fixture e poi le cancella non deve poter scegliere il
    bersaglio sbagliato per somiglianza.
    """
    candidate = (name or "").strip()
    if not candidate:
        raise GuardFailure("BLOCCATO: DB_NAME non e' impostato.")
    if candidate != REQUIRED_DB_NAME:
        raise GuardFailure(
            f"BLOCCATO: {candidate!r} non e' {REQUIRED_DB_NAME!r}. La matrice "
            "ostile scrive fixture e nomina un solo database."
        )
    return candidate


class Database:
    """Sottile involucro su `operator_auth.database.operator_cursor`.

    Esiste per rendere iniettabile la sorgente dei cursori: le prove su questo
    file esercitano guardie e cleanup senza un PostgreSQL.
    """

    def __init__(self, cursor_factory) -> None:
        self._cursor_factory = cursor_factory

    @classmethod
    def connect(cls) -> "Database":
        from operator_auth.database import operator_cursor

        return cls(operator_cursor)

    @contextmanager
    def read(self):
        with self._cursor_factory() as (_, cur):
            yield cur

    @contextmanager
    def write(self):
        with self._cursor_factory(commit=True) as (_, cur):
            yield cur

    def current_database(self) -> str:
        with self.read() as cur:
            cur.execute("SELECT current_database() AS name")
            return cur.fetchone()["name"]


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

class Response:
    __slots__ = ("status", "headers", "body")

    def __init__(self, status, headers, body):
        self.status, self.headers, self.body = status, headers, body

    def json(self):
        try:
            return json.loads(self.body.decode("utf-8"))
        except Exception:
            return None

    def items(self) -> list:
        parsed = self.json()
        if isinstance(parsed, dict):
            for key in ("items", "results", "data"):
                if isinstance(parsed.get(key), list):
                    return parsed[key]
        return parsed if isinstance(parsed, list) else []

    def text(self) -> str:
        return self.body.decode("utf-8", "replace")


#: Quanto corpo si riporta. Un motivo di validazione di FastAPI sta in poche
#: centinaia di byte; il tetto esiste perche' una pagina di errore HTML non
#: allaghi il report, non per nascondere qualcosa.
CORPO_MAX = 600


#: Cio' che nel corpo di una risposta non puo' finire in un report, con la
#: sostituzione che ne prende il posto. L'ordine CONTA: gli URL per primi,
#: cosi' che una firma nella query sparisca prima che la regola sulle stringhe
#: opache abbia occasione di guardarla.
#:
#: Ogni marcatore dice CHE COSA e' stato tolto. Una redazione muta - o peggio,
#: un troncamento silenzioso - riprodurrebbe il difetto che `_corpo` esiste per
#: chiudere: un report che non permette di ricostruire cosa e' successo.
_REDAZIONI = (
    # Un URL: restano schema e host, che dicono DOVE senza dire altro. Una URL
    # firmata porta la firma nella query, e una chiave di storage nel percorso:
    # nessuno dei due serve a diagnosticare uno stato HTTP.
    (re.compile(r"(https?://[^/\s\"'<>]+)[^\s\"'<>]*"), r"\1[percorso rimosso]"),
    # Valori di campi che portano un segreto per definizione.
    (re.compile(r'("(?:[\w-]*(?:token|secret|password|signature|authorization'
                r'|cookie|session|storage_key|presigned)[\w-]*)"\s*:\s*)"[^"]*"',
                re.I), r'\1"[segreto rimosso]"'),
    # Valori di campi personali. I nomi sono quelli che questo dominio usa
    # davvero - `stime`, `contacts`, `owner_accounts` - non un elenco generico.
    (re.compile(r'("(?:email|telefono|phone|nome|cognome|name|surname'
                r'|indirizzo|address|via|civico)"\s*:\s*)"[^"]*"', re.I),
     r'\1"[dato personale rimosso]"'),
    # Un indirizzo email ovunque si trovi, anche in mezzo a una frase.
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[email rimossa]"),
    # Una stringa opaca lunga: token, chiave, hash. Un messaggio di
    # validazione ha spazi e non finisce mai qui.
    (re.compile(r"\b[A-Za-z0-9_\-]{32,}\b"), "[stringa opaca rimossa]"),
)


def _redigi(testo: str) -> str:
    """Toglie dal corpo cio' che un report non deve portare.

    NON E' UN TRONCAMENTO PRUDENZIALE. Le regole sono mirate: tolgono firme,
    token, chiavi e dati personali e lasciano intatto il resto - cioe' proprio
    la parte diagnostica, che e' il motivo per cui il corpo viene stampato.
    "Il documento deve essere disponibile in storage privato" passa per intero;
    una URL firmata no.

    Non pretende di essere una garanzia formale su qualunque corpo un server
    possa produrre: e' una difesa per categoria, e il tetto `CORPO_MAX` resta
    il secondo argine.
    """
    for schema, sostituto in _REDAZIONI:
        testo = schema.sub(sostituto, testo)
    return testo


def _corpo(risposta) -> str:
    """Il corpo della risposta - redatto, non riassunto - per il report.

    IL RUN 58aa0e189aaa NON HA POTUTO DIRE PERCHE'.

    `owner-fixture-A-documento` si e' fermato su "condivisione -> 422" e basta:
    lo stato senza il motivo. Il motivo c'era, nel corpo, e nessuno lo
    stampava - due run interi sono stati spesi a indovinarlo, e la prima
    ipotesi era sbagliata.

    Quindi il corpo si stampa. Le credenziali non passano di qui - viaggiano
    negli header, che questo client non stampa in nessun caso - ma il CORPO di
    una risposta del dominio documenti puo' portare una URL firmata, una
    chiave di storage o i dati di un contatto, e nessuna delle tre serve a
    capire perche' una richiesta e' stata rifiutata. `_redigi` le toglie e
    lascia il messaggio.
    """
    try:
        testo = " ".join(risposta.text().split())
    except Exception as exc:                       # pragma: no cover - difensivo
        return f" [corpo illeggibile: {type(exc).__name__}]"
    if not testo:
        return " [corpo vuoto]"
    testo = _redigi(testo)
    if len(testo) > CORPO_MAX:
        testo = testo[:CORPO_MAX] + f"... (+{len(testo) - CORPO_MAX} caratteri)"
    return f" [corpo: {testo}]"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Ogni stato va osservato esattamente: nessun redirect seguito."""

    def redirect_request(self, *args, **kwargs):
        return None


class HttpProbe:
    """Client HTTP minimo, con cookie jar e header mai stampati."""

    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.exchanges: list[tuple[str, int, bytes]] = []

    def new_jar(self):
        return http.cookiejar.CookieJar()

    def put_cookie(self, jar, value: str, name: str = COOKIE_NAME) -> bool:
        """Mette `value` nel jar e VERIFICA che venga emesso.

        Un cookie che il jar poi rifiuta di inviare produrrebbe un 401 letto
        come "isolamento funzionante" quando invece la richiesta non era
        nemmeno autenticata. Provato prima di fidarsene.

        `name` esiste perche' i principali sono due: l'operatore porta
        `stima360_operator_session`, il proprietario `stima360_owner_session`.
        Un jar che li mescolasse renderebbe impossibile sapere quale identita'
        ha risposto.
        """
        host = urllib.parse.urlparse(self.base).hostname or ""
        jar.set_cookie(http.cookiejar.Cookie(
            version=0, name=name, value=value, port=None,
            port_specified=False, domain=host, domain_specified=False,
            domain_initial_dot=False, path="/", path_specified=True, secure=True,
            expires=None, discard=False, comment=None, comment_url=None,
            rest={"HttpOnly": None}, rfc2109=False))
        probe = urllib.request.Request(self.base + "/probe-cookie-emission")
        jar.add_cookie_header(probe)
        return (probe.get_header("Cookie") or "").startswith(COOKIE_NAME + "=")

    @staticmethod
    def token_in(jar):
        for cookie in jar:
            if cookie.name == COOKIE_NAME and cookie.value:
                return cookie.value
        return None

    def _opener(self, jar):
        handlers = [_NoRedirect(),
                    urllib.request.HTTPSHandler(context=ssl.create_default_context())]
        if jar is not None:
            handlers.append(urllib.request.HTTPCookieProcessor(jar))
        return urllib.request.build_opener(*handlers)

    def upload(self, path, *, jar, campi: dict, nome_file: str, contenuto: bytes,
               tipo: str = "application/pdf") -> Response:
        """Una POST multipart, scritta a mano perche' non ci sono dipendenze.

        Serve a un solo scopo: caricare un documento che abbia davvero uno
        `storage_key`, cosi' che il download del portale abbia un positivo
        reale invece di restare BLOCKED per assenza di storage.
        """
        confine = "----p26-6-" + secrets.token_hex(8)
        parti = []
        for chiave, valore in campi.items():
            parti.append(
                f'--{confine}\r\nContent-Disposition: form-data; name="{chiave}"'
                f"\r\n\r\n{valore}\r\n".encode("utf-8"))
        parti.append(
            f'--{confine}\r\nContent-Disposition: form-data; name="file"; '
            f'filename="{nome_file}"\r\nContent-Type: {tipo}\r\n\r\n'.encode("utf-8")
            + contenuto + b"\r\n")
        parti.append(f"--{confine}--\r\n".encode("utf-8"))
        corpo = b"".join(parti)
        request = urllib.request.Request(self.base + path, method="POST", data=corpo)
        request.add_header("Content-Type", f"multipart/form-data; boundary={confine}")
        try:
            with self._opener(jar).open(request, timeout=60) as response:
                risultato = Response(response.status, response.info(), response.read())
        except urllib.error.HTTPError as exc:
            risultato = Response(exc.code, exc.headers, exc.read())
        self.exchanges.append((f"POST {path}", risultato.status, risultato.body))
        return risultato

    def request(self, method, path, *, jar=None, payload=None) -> Response:
        request = urllib.request.Request(self.base + path, method=method)
        if payload is not None:
            request.data = json.dumps(payload).encode("utf-8")
            request.add_header("Content-Type", "application/json")
        try:
            with self._opener(jar).open(request, timeout=30) as response:
                result = Response(response.status, response.info(), response.read())
        except urllib.error.HTTPError as exc:
            result = Response(exc.code, exc.headers, exc.read())
        self.exchanges.append((f"{method} {path.split('?')[0]}", result.status, result.body))
        return result


# ---------------------------------------------------------------------------
# Identita' e fixture temporanee
# ---------------------------------------------------------------------------

class OwnerSessions:
    """Due sessioni `agency_owner` temporanee, agganciate agli owner REALI.

    IL VINCOLO CHE RENDE NECESSARIA QUESTA CLASSE

    migrations/027 dichiara `uq_agency_memberships_single_owner`: un'agenzia ha
    esattamente un agency_owner attivo. La matrice non puo' quindi crearsi due
    identita' owner come fa con le proprie - collidono con quelle vere - e
    finche' ha interrogato OWNER Admin da `agency_admin` ha ricevuto 403 prima
    di poter chiedere qualunque cosa sullo scope.

    COSA FA, ED ESATTAMENTE QUANTO POCO

    Apre una riga in `operator_sessions` per l'owner che gia' esiste. Nient'altro:
    nessuna password letta o cambiata, nessuna membership creata o sospesa,
    nessun utente inserito. `operator_sessions` porta solo
    `(operator_user_id, token_hash, expires_at)` - l'agenzia e il ruolo li
    ricalcola `resolve_session` a ogni richiesta - quindi una sessione non
    conferisce niente che l'owner non abbia gia'.

    IL RISCHIO, E COME E' LIMITATO

    Il token vale quanto le credenziali di un titolare. Percio': generato con
    `secrets`, scadenza breve, mai stampato, aggiunto ai segreti che lo scanner
    di fughe cerca in ogni risposta, e cancellato per ID nel `finally`. Se anche
    una sola di quelle righe sopravvivesse, il run e' FAIL.
    """

    # Abbastanza per la matrice, poco abbastanza da non lasciare in giro una
    # sessione di titolare utile: la certificazione dura minuti, non ore.
    LIFETIME_MINUTES = 30

    def __init__(self, database: "Database", report: Report) -> None:
        self.db = database
        self.report = report
        self.created_session_ids: list[int] = []

    def find_owner(self, agency: dict) -> int | None:
        """L'agency_owner attivo dell'agenzia, o None.

        Le stesse condizioni che `_scope_is_usable` applica a ogni richiesta:
        una sessione aperta per un owner sospeso non risolverebbe, e la prova
        fallirebbe per una ragione che non ha nulla a che vedere con lo scope.
        """
        with self.db.read() as cur:
            cur.execute(
                """
                SELECT m.operator_user_id AS id
                  FROM agency_memberships m
                  JOIN operator_users u ON u.id = m.operator_user_id
                 WHERE m.agency_id = %s
                   AND m.role = %s
                   AND m.status = 'active'
                   AND u.status = 'active'
                """,
                (agency["id"], OWNER_ADMIN_MIN_ROLE),
            )
            row = cur.fetchone()
        return int(row["id"]) if row else None

    def open(self, user_id: int) -> str:
        """Una sessione per quell'utente. Ritorna il token grezzo.

        L'hash lo calcola la funzione applicativa vera: reimplementare SHA-256
        qui vorrebbe dire che una modifica al modo in cui il server hasha i
        token lascerebbe questo script convinto di funzionare.
        """
        from operator_auth.security import generate_session_token, hash_session_token

        raw = generate_session_token()
        with self.db.write() as cur:
            cur.execute(
                """
                INSERT INTO operator_sessions (operator_user_id, token_hash, expires_at)
                VALUES (%s, %s, NOW() + (%s || ' minutes')::interval)
                RETURNING id
                """,
                (user_id, hash_session_token(raw), str(self.LIFETIME_MINUTES)),
            )
            self.created_session_ids.append(int(cur.fetchone()["id"]))
        return raw

    def cleanup(self) -> None:
        """Cancella SOLO le sessioni aperte da questo run, e lo dimostra."""
        if not self.created_session_ids:
            self.report.note("CLEAN-OWNER-SESS",
                             "nessuna sessione owner aperta: niente da rimuovere")
            return
        ids = tuple(self.created_session_ids)
        try:
            with self.db.write() as cur:
                cur.execute("DELETE FROM operator_sessions WHERE id IN %s", (ids,))
                removed = cur.rowcount
            with self.db.read() as cur:
                cur.execute(
                    "SELECT COUNT(*) AS n FROM operator_sessions WHERE id IN %s", (ids,))
                left = int(cur.fetchone()["n"])
        except Exception as exc:
            self.report.fail(
                "CLEAN-OWNER-SESS",
                f"cancellazione delle sessioni owner fallita ({type(exc).__name__}): "
                f"{len(ids)} sessioni di titolare sono POTENZIALMENTE ANCORA VALIDE",
            )
            return
        if left:
            self.report.fail(
                "CLEAN-OWNER-SESS",
                f"restano {left} sessioni owner di questo run: vanno revocate a mano",
            )
        else:
            self.report.note(
                "CLEAN-OWNER-SESS",
                f"rimosse {removed} sessioni owner temporanee, 0 residue "
                "(nessun owner reale modificato)",
            )


class ChildFk(NamedTuple):
    """Una chiave esterna verso una riga del run, con l'azione DICHIARATA.

    L'azione non e' una nota descrittiva: decide che cosa e' possibile
    provare, e chiamarla con il nome sbagliato rende falso il report.

      CASCADE  - la figlia DEVE sparire con il genitore. Verificabile per id
                 del genitore, dopo la cancellazione: se c'e' ancora, il
                 CASCADE non e' avvenuto.
      RESTRICT - la figlia IMPEDISCE la cancellazione del genitore. Va contata
                 PRIMA, perche' dopo non ci sarebbe nulla da cancellare: una
                 riga qui dentro fa fallire l'intera transazione del cleanup.
                 Trovarla dopo significa che il genitore e' ancora al suo posto.
      SET NULL - la figlia SOPRAVVIVE al genitore con la colonna azzerata.
                 Cercarla per id del genitore, dopo, restituisce 0 qualunque
                 cosa sia rimasta - come una JOIN a una riga cancellata. Si
                 verifica solo finche' il genitore esiste, e il report dice
                 che quello 0 non e' una prova di rimozione.
    """

    table: str
    column: str
    parent: str
    on_delete: str

    def __str__(self) -> str:
        return f"{self.table}.{self.column} ({self.on_delete} -> {self.parent})"


class Certification:
    """Le due identita' della matrice, e tutto cio' che questo run ha creato.

    Le password vivono in `self.secrets` per essere cercate nelle risposte, e
    non escono mai da li': non vengono stampate e non finiscono in un file.
    """

    def __init__(self, database: Database, report: Report) -> None:
        self.db = database
        self.report = report
        # Identifica QUESTO run. Compare nell'email e nel marcatore delle
        # fixture per rendere leggibile la provenienza di una riga, e non e'
        # mai il criterio di cancellazione: il cleanup lavora sugli id.
        self.run_id = secrets.token_hex(6)
        self.created_user_ids: list[int] = []
        self.secrets: list[str] = []
        # {(dominio, agenzia): [(metodo di cancellazione, path)]}
        self.fixtures: list[tuple[str, str, str]] = []
        # Le righe che nessuna route sa cancellare. MATCH, PROPOSAL, SALE e i
        # conti proprietario non hanno una DELETE: l'API sa crearli e non sa
        # disfarli. Restano quindi gli id - solo quelli di questo run - e il
        # cleanup li rimuove in ordine di dipendenza, provando che non ne resti
        # nessuno.
        self.created_sale_ids: list[int] = []
        self.created_proposal_ids: list[int] = []
        self.created_match_ids: list[int] = []
        self.created_owner_account_ids: list[int] = []
        # {tabella: [(id, marcatore, colonna_marcatore)]} - le righe che l'API
        # non rimuove fisicamente, da cancellare via SQL in ordine di FK.
        self.created_rows: dict = {}
        self.created_agency_ids: list[int] = []
        # Effetti di cui conosciamo l'ID perche' l'API ce l'ha restituito
        # (documenti dell'immobile, documenti condivisi). Non hanno agency_id:
        # si cancellano per id, in ordine di FK, prima delle radici.
        self.created_effects: dict = {}
        # Il motivo per cui il cleanup DISTRUTTIVO non puo' procedere, o None.
        # Oggi lo scrive solo la pulizia del bucket: se un oggetto resta
        # nello storage, la sua chiave si legge SOLO da
        # `property_documents.storage_key`, e cancellare quelle righe
        # renderebbe falso il messaggio di recupero appena stampato.
        self.blocking_reason: str | None = None
        # `cleanup_dedicated_agencies` e' stata invocata. Serve a
        # `verify_no_residue`: gli effetti elencati in
        # DEDICATED_EFFECT_TABLES vivono dentro quelle agenzie e spariscono
        # con loro, quindi contarli PRIMA darebbe un residuo che non e' tale.
        self.dedicated_cleanup_done = False
        # {tabella genitore: (id)} fotografati PRIMA di qualunque
        # cancellazione, e quante figlie avevano allora. Dopo il cleanup il
        # genitore non c'e' piu': senza questa istantanea le figlie non
        # sarebbero piu' raggiungibili, e una JOIN al genitore cancellato
        # risponderebbe 0 qualunque cosa sia rimasta.
        self.child_parents: dict = {}
        self.children_before: dict = {}
        # {tabella effetto: (id)} delle righe del run, fotografate PRIMA delle
        # DELETE. L'ID e' l'unico riferimento che nessun ON DELETE tocca: dopo
        # un SET NULL la riga non risponde piu' al genitore, ma risponde
        # sempre al proprio id.
        self.effect_rows_before: dict = {}
        self.effect_snapshot_done = False
        # {tabella figlia: (id)} delle righe che il run ha creato SENZA mai
        # riceverne l'id - le scrive il backend dentro la stessa transazione
        # di una fixture. Oggi soltanto `property_watch_observations`, vedi
        # `_snapshot_owned_children`. Serve a tre posti: il perimetro
        # distruttivo, la guardia RESTRICT e la cancellazione per id.
        self.owned_child_ids: dict = {}
        # Le righe che la fixture PROPERTY_WATCH crea nelle agenzie CONDIVISE.
        # Li' non esiste una cancellazione per `agency_id`: l'unico criterio
        # ammesso e' l'id, e questi sono gli id.
        self.shared_stima_ids: list = []
        self.shared_event_ids: list = []
        self.shared_observation_ids: list = []
        # E le righe che la fixture FLOW crea nelle agenzie CONDIVISE, per la
        # stessa ragione: `created_effects["flow_events"]` contiene anche gli
        # eventi delle agenzie dedicate, che se ne vanno per `agency_id` e non
        # vanno cancellati per id - cancellarli entrambi allo stesso modo
        # nasconderebbe un buco in `DEDICATED_TABLES`.
        self.shared_flow_event_ids: list = []
        self.shared_flow_execution_ids: list = []

    def marker(self, agency: str) -> str:
        """Una stringa che compare solo nelle fixture di questo run.

        Serve alle ricerche: una lista puo' contenere dati preesistenti, e
        cercare il marcatore isola cio' che questo run ha creato da cio' che
        c'era prima - senza il quale "A non vede la risorsa di B" sarebbe vero
        anche su un database vuoto.
        """
        return f"P26-6-{self.run_id}-{agency}"

    def leftovers(self) -> int:
        with self.db.read() as cur:
            cur.execute(
                "SELECT COUNT(*) AS n FROM operator_users "
                "WHERE email_normalized LIKE %s",
                (CERT_EMAIL_LIKE,),
            )
            return int(cur.fetchone()["n"])

    def create_operator(self, agency: dict) -> dict:
        """Un operatore attivo nell'agenzia data, con password casuale."""
        from core.normalization import normalize_email
        from operator_auth.security import hash_password

        email = f"{CERT_PREFIX}{self.run_id}-{secrets.token_hex(4)}{CERT_DOMAIN}"
        normalized = normalize_email(email)
        password = secrets.token_urlsafe(32)
        self.secrets.append(password)

        with self.db.write() as cur:
            cur.execute(
                "SELECT 1 FROM operator_users WHERE email_normalized = %s",
                (normalized,),
            )
            if cur.fetchone() is not None:
                raise CheckFailed("collisione sull'email generata")

            cur.execute(
                """
                INSERT INTO operator_users (
                    email, email_normalized, password_hash, status, is_platform_admin
                ) VALUES (%s, %s, %s, 'active', FALSE)
                RETURNING id
                """,
                (email, normalized, hash_password(password)),
            )
            user_id = int(cur.fetchone()["id"])
            # Registrato PRIMA della membership: se il secondo INSERT fallisce,
            # l'utente esiste e il cleanup deve saperlo.
            self.created_user_ids.append(user_id)

            cur.execute(
                """
                INSERT INTO agency_memberships (
                    agency_id, operator_user_id, role, status
                ) VALUES (%s, %s, %s, 'active')
                """,
                (agency["id"], user_id, CERT_ROLE),
            )

        return {"id": user_id, "email": email, "password": password, "agency": agency}

    def _residue(self, cur, ids: tuple[int, ...]) -> dict:
        """Quante righe restano PER GLI ID DI QUESTO RUN.

        Contato per id e non per prefisso: un conteggio per prefisso
        includerebbe le righe di un run concorrente e trasformerebbe il lavoro
        di qualcun altro in un fallimento di questo.
        """
        cur.execute(
            """
            SELECT (SELECT COUNT(*) FROM operator_users u
                     WHERE u.id IN %s)                          AS users,
                   (SELECT COUNT(*) FROM agency_memberships m
                     WHERE m.operator_user_id IN %s)            AS memberships,
                   (SELECT COUNT(*) FROM operator_sessions s
                     WHERE s.operator_user_id IN %s)            AS sessions
            """,
            (ids, ids, ids),
        )
        row = cur.fetchone()
        return {k: int(row[k]) for k in ("users", "memberships", "sessions")}

    def cleanup_http_fixtures(self, http: HttpProbe, jars: dict) -> None:
        """Cancella le risorse di dominio create da questo run, via API.

        Via HTTP e non via SQL di proposito: la cancellazione passa dalle
        stesse regole di scope delle altre chiamate, quindi non puo' toccare
        nulla che non appartenga all'operatore che l'ha creata. Una DELETE
        diretta sul database sarebbe piu' comoda e aggirerebbe esattamente cio'
        che questo script esiste per provare.
        """
        motivo = self._destructive_db_blocked()
        if motivo:
            # Anche le DELETE via API sono distruttive: dall'altra parte c'e'
            # comunque un handler che scrive.
            self.report.fail("CLEAN-FIXTURE",
                             f"nessuna cancellazione eseguita: {motivo}")
            return
        for agency, method, path in reversed(self.fixtures):
            jar = jars.get(agency)
            if jar is None:
                self.report.fail("CLEAN-FIXTURE",
                                 f"nessuna sessione per {agency}: {path} resta")
                continue
            response = http.request(method, path, jar=jar)
            if response.status not in (200, 204, 404):
                self.report.fail(
                    "CLEAN-FIXTURE",
                    f"{method} {path} -> {response.status}: la fixture resta",
                )
                continue
            # Un 2xx NON dimostra la cancellazione fisica - su questa API due
            # route DELETE archiviano e basta - ma nemmeno una rilettura HTTP
            # lo dimostra: `GET /api/core/tasks/{id}` non esiste, quindi un
            # 404 li' non distingue "cancellata" da "rotta assente".
            # La verifica che conta e' sul database, e vale per ogni riga
            # creata da questo run: vedi `verify_no_residue`.

    def _delete_scoped(self, label: str, statements: tuple, residue: tuple) -> None:
        """Cancella per ID e verifica che non resti nulla.

        `statements` e' una sequenza di `(sql, ids)` in ordine di dipendenza;
        `residue` la stessa cosa in forma di conteggio. Nessuno dei due accetta
        un LIKE o un prefisso: il criterio e' sempre e solo l'elenco degli id
        che questo run ha creato.
        """
        motivo = self._destructive_db_blocked()
        if motivo:
            self.report.fail(label, f"nessuna cancellazione eseguita: {motivo}")
            return
        pending = [(sql, ids) for sql, ids in statements if ids]
        if not pending:
            self.report.note(label, "niente creato: niente da rimuovere")
            return
        try:
            with self.db.write() as cur:
                for sql, ids in pending:
                    cur.execute(sql, (tuple(ids),))
            with self.db.read() as cur:
                left = 0
                for sql, ids in residue:
                    if not ids:
                        continue
                    cur.execute(sql, (tuple(ids),))
                    left += int(cur.fetchone()["n"])
        except Exception as exc:
            self.report.fail(
                label,
                f"cleanup fallito ({type(exc).__name__}): le righe del run "
                f"{self.run_id} sono POTENZIALMENTE PRESENTI",
            )
            return
        if left:
            self.report.fail(label, f"cleanup INCOMPLETO: restano {left} righe di questo run")
        else:
            self.report.note(label, f"run {self.run_id}: 0 righe residue")

    def cleanup_chain_fixtures(self) -> None:
        """Vendite, proposte e match creati da questo run.

        PRIMA delle fixture HTTP, non dopo: `matches` referenzia
        `buy_requests` e `properties`, quindi cancellare l'immobile mentre il
        match esiste ancora fallirebbe - e il fallimento sarebbe del cleanup,
        non dell'isolamento.
        """
        # L'ordine e' quello delle FK, ed e' obbligato: 013 e 016 dichiarano
        # ON DELETE RESTRICT su `match_id` e `proposal_id`, quindi una vendita
        # tiene in vita la sua proposta e una proposta il suo match. Le righe a
        # valle - eventi, risultati per criterio, righe di esecuzione - hanno
        # invece CASCADE e se ne vanno da sole.
        self._delete_scoped(
            "CLEAN-CHAIN",
            (
                ("DELETE FROM property_sales WHERE id IN %s", self.created_sale_ids),
                ("DELETE FROM property_proposals WHERE id IN %s", self.created_proposal_ids),
                ("DELETE FROM matches WHERE id IN %s", self.created_match_ids),
            ),
            (
                ("SELECT COUNT(*) AS n FROM property_sales WHERE id IN %s", self.created_sale_ids),
                ("SELECT COUNT(*) AS n FROM property_proposals WHERE id IN %s",
                 self.created_proposal_ids),
                ("SELECT COUNT(*) AS n FROM matches WHERE id IN %s", self.created_match_ids),
            ),
        )

    def cleanup_shared_watch_fixtures(self) -> None:
        """Stima, evento, watch e osservazione creati nelle agenzie CONDIVISE.

        PERCHE' UN METODO A PARTE.

        Le stesse quattro righe, nelle agenzie dedicate, se ne vanno con
        `DELETE ... WHERE agency_id IN (...)`. Nelle condivise no: li' vivono
        accanto ai dati veri di TEST, e l'unico criterio ammesso e' l'id.

        L'ORDINE E' OBBLIGATO da un solo vincolo vero:
        `property_watch_observations.watch_id` e' ON DELETE RESTRICT, quindi
        l'osservazione che `initialize` ha scritto va tolta PRIMA del watch,
        altrimenti la cancellazione del watch non fallisce "in parte" -
        fallisce del tutto. `property_watches.stima_id` e
        `seller_timeline_events.stima_id` sono SET NULL: non impongono niente,
        e infatti la stima resta per ultima solo per leggibilita'.

        Le osservazioni si cancellano per gli id gia' RICONOSCIUTI nostri da
        `_snapshot_owned_children` - chiave derivata - non per `watch_id`: una
        riga scritta dalla scansione periodica sullo stesso watch non e'
        nostra e deve continuare a bloccare.
        """
        watch = tuple(self.created_effects.get("property_watches", ()))
        stime = tuple(self.shared_stima_ids)
        eventi = tuple(self.shared_event_ids)
        if not (watch or stime or eventi):
            return
        nostre = set(self.owned_child_ids.get("property_watch_observations", ()))
        osservazioni = tuple(sorted(nostre & set(self.shared_observation_ids)))
        self._delete_scoped(
            "CLEAN-CONDIVISE",
            (
                ("DELETE FROM property_watch_observations WHERE id IN %s", osservazioni),
                ("DELETE FROM property_watches WHERE id IN %s", watch),
                ("DELETE FROM seller_timeline_events WHERE id IN %s", eventi),
                ("DELETE FROM stime WHERE id IN %s", stime),
            ),
            (
                ("SELECT COUNT(*) AS n FROM property_watch_observations WHERE id IN %s",
                 osservazioni),
                ("SELECT COUNT(*) AS n FROM property_watches WHERE id IN %s", watch),
                ("SELECT COUNT(*) AS n FROM seller_timeline_events WHERE id IN %s", eventi),
                ("SELECT COUNT(*) AS n FROM stime WHERE id IN %s", stime),
            ),
        )

    def cleanup_shared_flow_fixtures(self) -> None:
        """Evento ed esecuzione FLOW creati nelle agenzie CONDIVISE.

        Stessa ragione di `cleanup_shared_watch_fixtures`: nelle agenzie
        dedicate queste righe se ne vanno con
        `DELETE ... WHERE agency_id IN (...)`, nelle condivise vivono accanto
        ai dati veri di TEST e l'unico criterio ammesso e' l'id.

        L'ORDINE E' OBBLIGATO, e non dal fallimento di una DELETE.
        `flow_executions.event_id` e' ON DELETE SET NULL (008_flow_01.sql):
        cancellare l'evento per primo NON fallirebbe, azzererebbe la colonna, e
        l'esecuzione resterebbe sul TEST come una riga che nessuno sa piu'
        attribuire a questo run. E' il danno silenzioso che il SET NULL produce
        ogni volta che lo si ignora - qui si evita cancellando la figlia
        finche' il legame e' ancora leggibile.

        `flow_action_records` non compare: sull'esecuzione e' CASCADE, e le sue
        righe - se mai ne nascessero - sono gia' fotografate per id da
        `_snapshot_owned_by_parent`, che e' l'unico modo di verificarne la
        rimozione dopo che il genitore e' sparito.
        """
        esecuzioni = tuple(self.shared_flow_execution_ids)
        eventi = tuple(self.shared_flow_event_ids)
        if not (esecuzioni or eventi):
            return
        self._delete_scoped(
            "CLEAN-FLOW",
            (
                ("DELETE FROM flow_executions WHERE id IN %s", esecuzioni),
                ("DELETE FROM flow_events WHERE id IN %s", eventi),
            ),
            (
                ("SELECT COUNT(*) AS n FROM flow_executions WHERE id IN %s", esecuzioni),
                ("SELECT COUNT(*) AS n FROM flow_events WHERE id IN %s", eventi),
            ),
        )

    def cleanup_owner_fixtures(self) -> None:
        """Conti proprietario, concessioni, token e sessioni del portale.

        Anche questo prima delle fixture HTTP: `owner_accounts.contact_id` e'
        ON DELETE RESTRICT, quindi finche' il conto esiste il contatto non si
        cancella.
        """
        ids = self.created_owner_account_ids
        # LE DUE TABELLE DEL PERCORSO DOCUMENTALE, PER ID E NON PER GENITORE.
        #
        # `owner_notifications` e `owner_document_reads` sono CASCADE verso
        # `owner_accounts`: la DELETE in fondo a questo elenco se le porterebbe
        # via da sola, in silenzio, senza che il predicato di appartenenza le
        # abbia mai viste. Vanno quindi rimosse PRIMA, e per gli id che
        # l'istantanea ha gia' riconosciuto come nostri - non per
        # `owner_account_id IN (...)`, che prenderebbe anche una riga che lega
        # il nostro conto a un documento di qualcun altro. Quella riga non e'
        # nostra: deve restare, e deve continuare a bloccare.
        notifiche = self.effect_rows_before.get("owner_notifications", ())
        letture = self.effect_rows_before.get("owner_document_reads", ())
        self._delete_scoped(
            "CLEAN-OWNER",
            (
                ("DELETE FROM owner_document_reads WHERE id IN %s", letture),
                ("DELETE FROM owner_notifications WHERE id IN %s", notifiche),
                ("DELETE FROM owner_sessions WHERE owner_account_id IN %s", ids),
                ("DELETE FROM owner_access_tokens WHERE owner_account_id IN %s", ids),
                ("DELETE FROM owner_publication_reads WHERE owner_account_id IN %s", ids),
                ("DELETE FROM owner_feedback WHERE owner_account_id IN %s", ids),
                ("DELETE FROM owner_property_access WHERE owner_account_id IN %s", ids),
                # L'AUDIT DEL RUN VA VIA, E IL PERCHE' E' CAMBIATO.
                #
                # `owner_audit_log.owner_account_id` e `.property_id` sono
                # entrambi ON DELETE SET NULL: cancellare conto e immobile
                # lascerebbe la riga con due colonne azzerate, cioe' una traccia
                # che non dice piu' di cosa parlava. Su dati veri conservarla
                # avrebbe senso; su un conto di certificazione e' rumore che
                # sopravvive a ogni run e che nessuno sapra' piu' attribuire.
                #
                # Il criterio resta l'id: il conto di questo run, oppure uno dei
                # suoi immobili. Mai un prefisso, mai un intervallo di date.
                ("DELETE FROM owner_audit_log WHERE owner_account_id IN %s", ids),
                ("DELETE FROM owner_accounts WHERE id IN %s", ids),
            ),
            (
                ("SELECT COUNT(*) AS n FROM owner_accounts WHERE id IN %s", ids),
                ("SELECT COUNT(*) AS n FROM owner_property_access "
                 "WHERE owner_account_id IN %s", ids),
                ("SELECT COUNT(*) AS n FROM owner_sessions WHERE owner_account_id IN %s", ids),
                ("SELECT COUNT(*) AS n FROM owner_audit_log WHERE owner_account_id IN %s", ids),
                # Per ID: dopo la cancellazione del conto, un conteggio per
                # `owner_account_id` risponderebbe 0 per costruzione - la
                # stessa vacuita' della JOIN a un genitore cancellato.
                ("SELECT COUNT(*) AS n FROM owner_document_reads WHERE id IN %s", letture),
                ("SELECT COUNT(*) AS n FROM owner_notifications WHERE id IN %s", notifiche),
            ),
        )

    # ------------------------------------------------------------------
    # AGENZIE DEDICATE
    #
    # Le uniche righe che questo run puo' creare in un'agenzia sua, e quindi
    # l'unico modo in cui la scansione FOLLOWUP - che non ammette una
    # restrizione agli id - puo' toccare soltanto cio' che ci appartiene.
    # ------------------------------------------------------------------

    #: In ordine di CANCELLAZIONE: figli prima dei genitori. Scritto a mano e
    #: non ricavato dal catalogo, deliberatamente. Il catalogo serve a
    #: SCOPRIRE una dipendenza che non prevedevamo, non ad autorizzarne la
    #: cancellazione: una cancellazione automatica su una tabella che nessuno
    #: aveva considerato e' esattamente il danno da cui ci si vuole difendere.
    DEDICATED_TABLES = (
        # `property_watch_observations` e `seller_revival_suppressions` non
        # hanno agency_id e non compaiono qui, ma per ragioni OPPOSTE, e
        # chiamarle entrambe "figlie CASCADE" - come faceva questo commento -
        # descriveva il comportamento di una sola delle due:
        #
        #   seller_revival_suppressions.contact_id  ON DELETE CASCADE
        #     se ne va con il contatto. La verifica finale lo conferma.
        #   property_watch_observations.watch_id    ON DELETE RESTRICT
        #     NON se ne va: impedisce la cancellazione del watch e fa cadere
        #     l'intera transazione di questo metodo. Le righe che `initialize`
        #     ha scritto per noi si cancellano per ID, sotto lock, prima del
        #     watch (vedi `_snapshot_owned_children`); tutte le altre restano
        #     ostacoli che `_blocking_children` conta PRIMA e che fanno
        #     fermare, non cancellare.
        #
        # Lo stesso vale per `invisible_sale_opportunities.watch_id`, anch'essa
        # RESTRICT. Le azioni vere sono dichiarate in CHILD_FOREIGN_KEYS e
        # verificate contro le migration.
        # `seller_timeline_events` per PRIMA: la fixture PROPERTY_WATCH vi
        # inserisce l'evento `stima_completata`, e la sua `agency_id` e'
        # RESTRICT verso `agencies`. Senza questa riga, `DELETE FROM agencies`
        # fallirebbe e l'agenzia temporanea resterebbe sul TEST.
        ("seller_timeline_events", "agency_id"),
        ("next_best_actions", "agency_id"),
        # `flow_suppressions` PRIMA delle esecuzioni, e trovata cercandola.
        #
        # Ha `agency_id` NOT NULL (054) e la sua FK verso `agencies` e' ON
        # DELETE RESTRICT (052): una sola riga di soppressione in un'agenzia
        # dedicata avrebbe fatto fallire `DELETE FROM agencies` e con essa
        # l'intera transazione di questo metodo. La scrive la valutazione delle
        # regole, cioe' lo stesso `POST /api/flow/events` che la fixture FLOW
        # chiama: nessun run l'ha ancora incontrata solo perche' nessuna regola
        # ha trovato corrispondenza nelle agenzie temporanee.
        ("flow_suppressions", "agency_id"),
        ("flow_executions", "agency_id"),
        ("flow_events", "agency_id"),
        ("property_watches", "agency_id"),
        ("stime", "agency_id"),
        ("followup_actions", "agency_id"),
        ("tasks", "agency_id"),
        # `leads` PRIMA di `contacts`: `leads.contact_id` e' RESTRICT, quindi
        # finche' il lead esiste il contatto non si cancella. Il lead lo crea
        # la fixture NEXT_BEST_ACTION, che senza di esso non avrebbe alcun
        # segnale da cui far nascere un'azione.
        ("leads", "agency_id"),
        ("contacts", "agency_id"),
        ("agency_memberships", "agency_id"),
    )

    def create_dedicated_agency(self, label: str) -> dict | None:
        """Un'agenzia temporanea con un operatore e nient'altro.

        Lo slug rispetta `agencies_slug_chk` e porta il run_id, cosi' che una
        riga trovata domani sia attribuibile. Non e' pero' un criterio di
        cancellazione: quello resta l'id.
        """
        from core.normalization import normalize_email
        from operator_auth.security import hash_password

        slug = f"p26-6-cert-{self.run_id}-{label.lower()}"
        email = f"{CERT_PREFIX}{self.run_id}-{label.lower()}-ded{CERT_DOMAIN}"
        password = secrets.token_urlsafe(32)
        self.secrets.append(password)
        try:
            with self.db.write() as cur:
                cur.execute(
                    "SELECT 1 FROM agencies WHERE slug = %s", (slug,))
                if cur.fetchone() is not None:
                    raise CheckFailed("collisione sullo slug dell'agenzia dedicata")
                cur.execute(
                    "INSERT INTO agencies (name, slug, status) "
                    "VALUES (%s, %s, 'active') RETURNING id",
                    (f"P26-6 certificazione {self.run_id} {label}", slug),
                )
                agency_id = int(cur.fetchone()["id"])
                self.created_agency_ids.append(agency_id)

                cur.execute(
                    """
                    INSERT INTO operator_users (
                        email, email_normalized, password_hash, status, is_platform_admin
                    ) VALUES (%s, %s, %s, 'active', FALSE) RETURNING id
                    """,
                    (email, normalize_email(email), hash_password(password)),
                )
                user_id = int(cur.fetchone()["id"])
                self.created_user_ids.append(user_id)
                cur.execute(
                    "INSERT INTO agency_memberships (agency_id, operator_user_id, "
                    "role, status) VALUES (%s, %s, %s, 'active')",
                    (agency_id, user_id, CERT_ROLE),
                )
        except CheckFailed:
            raise
        except Exception as exc:
            self.report.fail(
                f"DEDICATA-{label}",
                f"creazione non riuscita ({type(exc).__name__}): nessuna prova "
                "FOLLOWUP su agenzia dedicata",
            )
            return None
        self.report.note(
            f"DEDICATA-{label}",
            f"agenzia temporanea {agency_id} creata con un operatore proprio "
            f"(slug {slug})",
        )
        return {"id": agency_id, "slug": slug, "email": email, "password": password}

    def snapshot_before_cleanup(self) -> None:
        """Tutto cio' che dopo le DELETE non sarebbe piu' identificabile.

        Due fotografie, per due ragioni diverse ma della stessa forma: un
        riferimento che sparisce - il genitore cancellato, o la colonna
        azzerata da un SET NULL - rende la domanda successiva vuota invece che
        negativa.
        """
        # L'ORDINE NON E' CASUALE: `_snapshot_owned_children` parte dagli id
        # dei watch, che solo `_snapshot_child_parents` conosce.
        guasti = [m for m in (self._snapshot_child_parents(),
                              self._snapshot_owned_children(),
                              self._snapshot_effect_rows()) if m]
        if not guasti:
            return
        # FAIL-CLOSED. Una fotografia incompleta non si scopre dopo: dopo le
        # DELETE le righe che non sono state fotografate non sono piu'
        # identificabili, e la verifica finale direbbe "0 presenti" su una
        # domanda che non ha potuto fare. Segnalare e cancellare lo stesso
        # significherebbe distruggere cio' che non si sa piu' controllare.
        self.blocking_reason = (
            f"istantanea incompleta prima del cleanup: {'; '.join(guasti)}. "
            "Nessuna cancellazione viene eseguita: dopo, quelle righe non "
            "sarebbero piu' identificabili. RECUPERO: rieseguire il run quando "
            "il database e' interrogabile, oppure rimuovere a mano le righe "
            "degli id gia' riportati sopra."
        )
        self.report.fail("CLEAN-ISTANTANEA", self.blocking_reason)

    def _snapshot_effect_rows(self) -> None:
        """Gli ID delle righe del run nelle tabelle degli effetti.

        PERCHE' NON BASTA CERCARLE PER ID DEL GENITORE

        `owner_audit_log.property_id` e `.owner_account_id` sono ON DELETE SET
        NULL. Cancellato l'immobile, quella colonna diventa NULL: la riga
        sopravvive e non risponde piu' al suo genitore. Un conteggio
        `WHERE property_id IN (...)` eseguito dopo il cleanup restituisce
        quindi 0 per costruzione - la stessa vacuita' della JOIN a un genitore
        cancellato - e `CLEAN-VERIFICA` non poteva dire "0 presenti,
        verificato" mentre `CLEAN-FIGLIE` ammetteva di non poter verificare.

        Qui si prende l'ID della riga, che nessun ON DELETE modifica. Dopo il
        cleanup la si cerca per quello, e la risposta significa qualcosa.

        Il criterio di appartenenza e' lo stesso delle cancellazioni - ogni
        riferimento non nullo dentro il perimetro, almeno uno che ci punta -
        quindi questa istantanea non nomina mai una riga altrui.
        """
        self.effect_rows_before = {}
        # Si parte dalle RADICI e si cresce: `match_requirement_results`
        # appartiene al run perche' pende da un `match_runs` che appartiene al
        # run, e quello si scopre in questo stesso giro. L'ordine di
        # EFFECT_TABLES e' quello delle dipendenze proprio per questo.
        pieno = self._root_perimeter()
        if not pieno:
            # Niente da cui derivare l'appartenenza: nessun effetto puo'
            # essere nostro. E' una fotografia vuota, non una fallita.
            self.effect_snapshot_done = True
            return None
        try:
            with self.db.read() as cur:
                for table, columns in self.EFFECT_TABLES:
                    pred = self._effect_predicate(table, columns, pieno, "t")
                    if pred is None:
                        continue
                    frammento, params = pred
                    cur.execute(f"SELECT t.id FROM {table} t WHERE {frammento}", params)
                    ids = tuple(int(r["id"]) for r in cur.fetchall())
                    if ids:
                        self.effect_rows_before[table] = ids
                        # Da adesso questa tabella e' un genitore possibile per
                        # quelle che seguono.
                        pieno[table] = tuple(sorted(set(pieno.get(table, ())) | set(ids)))
        except Exception as exc:
            self.effect_rows_before = {}
            self.effect_snapshot_done = False
            self.report.fail(
                "CLEAN-EFFETTI",
                f"righe degli effetti non fotografabili ({type(exc).__name__}): "
                "dopo il cleanup le relazioni SET NULL non saranno piu' "
                "verificabili, e la verifica finale non potra' dirsi conclusa",
            )
            return f"righe degli effetti non fotografabili ({type(exc).__name__})"
        self.effect_snapshot_done = True
        return None

    def _snapshot_child_parents(self) -> None:
        """Fotografa i genitori PRIMA che qualcuno li cancelli.

        PERCHE' NON SI PUO' GUARDARE DOPO

        La verifica precedente contava le figlie con una JOIN al genitore:

            FROM property_watch_observations o
            JOIN property_watches w ON w.id = o.watch_id
            WHERE w.agency_id IN (...)

        Dopo il cleanup quel genitore non esiste piu', quindi la JOIN non ha
        righe e il conteggio e' 0 - qualunque cosa sia sopravvissuta. La
        verifica non falliva mai, e non perche' il CASCADE avesse funzionato:
        perche' non stava piu' guardando niente. Eseguirla PRIMA della
        cancellazione e' l'errore opposto e altrettanto vuoto: chiede se le
        figlie esistono ancora prima di aver chiesto loro di sparire.

        L'unica forma che prova qualcosa e' in due tempi: qui si prendono gli
        id dei genitori e quante figlie avevano; dopo il cleanup le figlie si
        cercano per quegli id, senza JOIN e senza dipendere da una riga
        cancellata.

        Va chiamata prima di OGNI cancellazione, comprese quelle via API: e'
        il motivo per cui sta in testa al `finally` e non accanto al cleanup
        delle agenzie.

        NON SOLO I GENITORI DELLE FIGLIE DICHIARATE. Nelle agenzie dedicate si
        cancella per `agency_id`, quindi di quelle righe non si registra mai un
        id: `tasks` e `flow_executions` sparivano senza che la guardia potesse
        chiedere chi le referenziasse, e hanno entrambe FK non-CASCADE
        entranti. Qui si fotografano anche quelle.
        """
        # SENZA AGENZIE DEDICATE C'E' COMUNQUE QUALCOSA DA FOTOGRAFARE.
        #
        # La fixture PROPERTY_WATCH crea un watch anche nelle agenzie
        # CONDIVISE, e la sua osservazione e' RESTRICT: uscire qui
        # lascerebbe quell'osservazione fuori dal riconoscimento e la
        # cancellazione del watch fallirebbe per intero.
        watch_condivisi_presenti = bool(self.created_effects.get("property_watches"))
        if not self.created_agency_ids and not watch_condivisi_presenti:
            return None
        agenzie = tuple(self.created_agency_ids) or (0,)
        genitori = ({fk.parent for fk in self.CHILD_FOREIGN_KEYS}
                    | set(self.DEDICATED_SNAPSHOT_TABLES))
        # I WATCH DELLE AGENZIE CONDIVISE non hanno un `agency_id` fra quelle
        # dedicate, quindi la query qui sotto non li troverebbe: senza questa
        # unione le loro osservazioni resterebbero fuori dal riconoscimento e
        # il preflight le dichiarerebbe estranee - lo stesso difetto del run
        # 58aa0e189aaa, un'agenzia piu' in la'.
        watch_condivisi = tuple(self.created_effects.get("property_watches", ()))
        try:
            with self.db.read() as cur:
                for genitore in sorted(genitori):
                    cur.execute(
                        f"SELECT id FROM {genitore} WHERE agency_id IN %s", (agenzie,))
                    trovati = {int(r["id"]) for r in cur.fetchall()}
                    if genitore == "property_watches":
                        trovati |= set(watch_condivisi)
                    self.child_parents[genitore] = tuple(sorted(trovati))
                for fk in self.CHILD_FOREIGN_KEYS:
                    ids = self.child_parents.get(fk.parent)
                    if not ids:
                        self.children_before[fk.table] = 0
                        continue
                    cur.execute(
                        f"SELECT COUNT(*) AS n FROM {fk.table} WHERE {fk.column} IN %s",
                        (ids,))
                    self.children_before[fk.table] = int(cur.fetchone()["n"])
        except Exception as exc:
            # Senza istantanea la verifica successiva sarebbe vuota: meglio
            # dirlo adesso che dopo, quando non sarebbe piu' distinguibile da
            # una rimozione riuscita.
            self.child_parents.clear()
            self.report.fail(
                "CLEAN-FIGLIE",
                f"genitori non fotografabili ({type(exc).__name__}): dopo la "
                "cancellazione non ci sara' modo di identificare le figlie, e "
                "la verifica non potra' dire nulla",
            )
            return f"genitori non fotografabili ({type(exc).__name__})"
        return None

    def _etichetta_figlia(self, tabella: str) -> str:
        """"tabella (AZIONE)" - il nome con l'ON DELETE dichiarato.

        Ogni riga di report che nomina una figlia deve portarne l'azione: "0
        dopo" significa cose diverse per un CASCADE e per una RESTRICT, e una
        relazione RESTRICT descritta come CASCADE manda l'operatore a cercare
        un guasto che non esiste. `test_86g` lo pretende sul testo.
        """
        azione = next((fk.on_delete for fk in self.CHILD_FOREIGN_KEYS
                       if fk.table == tabella), "azione non dichiarata")
        return f"{tabella} ({azione})"

    #: Figlie SENZA `agency_id` il cui genitore vive in un'agenzia dedicata.
    #:
    #: (tabella, colonna, genitore). Il genitore deve comparire in
    #: `DEDICATED_SNAPSHOT_TABLES`, altrimenti `child_parents` non ne conosce
    #: gli id e questa dichiarazione sarebbe muta: `_snapshot_owned_by_parent`
    #: lo pretende invece di fidarsi.
    #:
    #: `flow_action_records` E' IL CASO CHE HA FATTO NASCERE QUESTA STRUTTURA,
    #: e non e' stato trovato da un run fallito ma cercandolo. `POST
    #: /api/flow/events` non si limita a registrare l'evento: `process_event`
    #: chiama `process_saved_event`, che valuta le regole attive e puo'
    #: scrivere una esecuzione e i suoi record di azione. Quei record non hanno
    #: `agency_id` - la loro appartenenza e' la JOIN all'esecuzione, come dice
    #: 055 - quindi non entravano nel perimetro per nessuna via, e il giorno in
    #: cui una regola avesse trovato corrispondenza in un'agenzia dedicata il
    #: preflight li avrebbe dichiarati estranei bloccando tutto: la stessa
    #: forma di `property_watch_observations`, un run piu' avanti.
    OWNED_BY_PARENT = (
        ("flow_action_records", "execution_id", "flow_executions"),
    )

    #: I riferimenti che il CATALOGO NON VEDE.
    #:
    #: {tabella: (colonna del tipo, colonna dell'id, {valore: tabella})}
    #:
    #: `flow_action_records` ha una sola chiave esterna vera - `execution_id` -
    #: ma punta anche altrove, e senza vincolo: l'azione `create_core_task`
    #: scrive `target_entity_type='task'` e `target_entity_id=<id del task>`
    #: (flow/repository.py, UPDATE dopo l'esecuzione dell'azione). Nessuna FK
    #: lega quelle due colonne, quindi `_altre_chiavi_esterne` non le trova e
    #: una verifica basata sul solo catalogo direbbe "nessun altro
    #: riferimento" su una riga che ne ha uno.
    #:
    #: Un valore del tipo non elencato qui NON viene ignorato: BLOCCA. Una
    #: forma non prevista e' esattamente il caso in cui non si puo' decidere.
    RIFERIMENTI_LOGICI = {
        "flow_action_records": (
            "target_entity_type", "target_entity_id", {"task": "tasks"}),
    }

    def _riferimento_logico_estraneo(self, tabella: str, riga, perimetro) -> list:
        """Il riferimento che nessun vincolo dichiara, controllato lo stesso.

        `flow_action_records.target_entity_id` punta a un task, ma senza FK:
        il catalogo non lo vede e una verifica che si fermasse li' direbbe
        "nessun altro riferimento" su una riga che ne ha uno.

        Un tipo non elencato in RIFERIMENTI_LOGICI non viene ignorato: e'
        segnalato come estraneo, perche' "non so a cosa punti" e "punta dentro
        il perimetro" non sono la stessa cosa.
        """
        logico = self.RIFERIMENTI_LOGICI.get(tabella)
        if not logico:
            return []
        colonna_tipo, colonna_id, mappa = logico
        tipo, valore = riga[colonna_tipo], riga[colonna_id]
        if valore is None:
            return []
        genitore = mappa.get(tipo)
        if genitore is None:
            return [f"{colonna_tipo}={tipo!r} non classificato ({colonna_id}={valore})"]
        if int(valore) not in set(perimetro.get(genitore, ())):
            return [f"{colonna_id}={valore} ({genitore}) fuori perimetro"]
        return []

    def _altre_chiavi_esterne(self, cur, tabella: str, esclusa: str) -> list:
        """Le FK di `tabella` diverse da `esclusa`, con la relazione INTERA.

        [(colonna, tabella genitore)], con TRE forme rifiutate prima di
        arrivare qui.

        LA VERSIONE PRECEDENTE PROMETTEVA L'IDENTITA' E LA BUTTAVA VIA. Leggeva
        `confrelid::regclass::text` - che porta lo schema solo quando non e'
        nel search_path - e poi faceva `split(".")[-1]`: due tabelle omonime in
        schemi diversi diventavano la stessa, in silenzio.

        Qui lo schema arriva da `pg_namespace` e serve a RIFIUTARE, non a
        decorare: un genitore fuori da `public` solleva. Per questo la tupla
        restituita non lo porta - a valle sarebbe sempre "public" e un campo
        che nessuno legge e' un campo che nessuno puo' sbagliare.

        E IGNORAVA `confkey`. Confrontava il valore della colonna figlia con
        gli id del perimetro comunque, anche se la FK puntasse a un'altra
        colonna: su `REFERENCES t(codice)` avrebbe confrontato un codice con
        degli id e dichiarato "fuori perimetro" qualunque riga. Adesso la
        colonna riferita si legge, e:

          * FK COMPOSITA (piu' di una colonna): si BLOCCA. Il perimetro e' un
            insieme di id singoli e non sa rispondere a una chiave a due
            colonne; fingere di saperlo sarebbe la risposta peggiore.
          * FK VERSO UNA COLONNA DIVERSA DA `id`: si BLOCCA, per la stessa
            ragione.
          * GENITORE FUORI DA `public`: si BLOCCA. Il perimetro e' tutto in
            public, quindi su un altro schema non ha nulla da dire.

        Bloccare vuol dire sollevare: `_snapshot_owned_by_parent` traduce
        l'eccezione in un FAIL fail-closed, e nessuna DELETE parte.
        """
        cur.execute(
            """
            SELECT con.conname                       AS vincolo,
                   ns.nspname                        AS genitore_schema,
                   cl.relname                        AS genitore_tabella,
                   att.attname                       AS colonna,
                   patt.attname                      AS colonna_riferita,
                   array_length(con.conkey, 1)       AS quante_colonne
              FROM pg_constraint con
              JOIN pg_class cl ON cl.oid = con.confrelid
              JOIN pg_namespace ns ON ns.oid = cl.relnamespace
              CROSS JOIN LATERAL unnest(con.conkey, con.confkey) AS k(figlio, genitore)
              JOIN pg_attribute att ON att.attrelid = con.conrelid
                   AND att.attnum = k.figlio
              JOIN pg_attribute patt ON patt.attrelid = con.confrelid
                   AND patt.attnum = k.genitore
             WHERE con.contype = 'f'
               AND con.conrelid = ('public.' || %s)::regclass
            """,
            (tabella,),
        )
        fuori = []
        for riga in cur.fetchall():
            if riga["colonna"] == esclusa:
                if int(riga["quante_colonne"] or 1) != 1:
                    raise AssertionError(
                        f"{tabella}: il legame col genitore ({riga['vincolo']}) e' "
                        "una FK composita: l'appartenenza per id non e' definita")
                continue
            if int(riga["quante_colonne"] or 1) != 1:
                raise AssertionError(
                    f"{tabella}.{riga['colonna']} appartiene alla FK composita "
                    f"{riga['vincolo']}: forma non supportata, non si decide")
            if riga["colonna_riferita"] != "id":
                raise AssertionError(
                    f"{tabella}.{riga['colonna']} -> "
                    f"{riga['genitore_tabella']}.{riga['colonna_riferita']}: il "
                    "perimetro conosce solo gli id, forma non supportata")
            if riga["genitore_schema"] != "public":
                raise AssertionError(
                    f"{tabella}.{riga['colonna']} punta a "
                    f"{riga['genitore_schema']}.{riga['genitore_tabella']}: il "
                    "perimetro e' tutto in public, forma non supportata")
            fuori.append((riga["colonna"], riga["genitore_tabella"]))
        return fuori

    def _snapshot_owned_by_parent(self) -> None:
        """Le figlie dichiarate in OWNED_BY_PARENT, con TRE condizioni.

        "IL GENITORE STA IN UN'AGENZIA NOSTRA" NON BASTA, E LA VERSIONE
        PRECEDENTE DI QUESTO COMMENTO DICEVA CHE BASTAVA.

        Diceva: "non c'e' nessun altro che possa averla scritta". E' falso
        come affermazione generale. Un processo platform-wide - il cron FLOW,
        una ripresa degli eventi, un motore batch - puo' scrivere una figlia su
        una NOSTRA esecuzione mentre il run e' in corso: l'agenzia e' nostra,
        la riga no. Cancellarla sarebbe esattamente il danno da cui tutto
        questo meccanismo difende.

        Le condizioni sono tre, e sono tutte necessarie:

        1. IL GENITORE E' FOTOGRAFATO fra le tabelle delle agenzie dedicate.
           Senza, non c'e' nemmeno l'insieme di partenza.

        2. I PROCESSI PLATFORM-WIDE SONO SOSPESI. Non e' una verifica che
           questo script possa fare - lo dichiara `--with-dedicated-agencies`,
           la cui guida dice esattamente questo: "ATTESTA che l'operatore ha
           verificato e sospeso i processi platform-wide: lo script NON lo
           rileva e non puo' rilevarlo". E' una condizione OPERATIVA, e va
           scritta qui accanto al criterio che ne dipende.

        3. LA RIGA NON PUNTA ALTROVE. Se la figlia ha altre chiavi esterne
           oltre a quella dichiarata, ogni riferimento non nullo deve cadere
           nel perimetro: una riga che lega la nostra esecuzione a qualcosa di
           estraneo e' MISTA, e una relazione mista non diventa cancellabile
           per effetto di questa generalizzazione. Le altre FK si leggono dal
           catalogo, non da un elenco scritto a mano: una colonna aggiunta
           domani viene esaminata senza che nessuno se ne ricordi.
        """
        if not self.created_agency_ids:
            return None
        perimetro = self._perimeter()
        try:
            with self.db.read() as cur:
                for tabella, colonna, genitore in self.OWNED_BY_PARENT:
                    if genitore not in self.DEDICATED_SNAPSHOT_TABLES:
                        raise AssertionError(
                            f"{tabella}.{colonna} pende da {genitore}, che non e' "
                            "fotografato: l'appartenenza non sarebbe dimostrabile")
                    ids = self.child_parents.get(genitore)
                    if not ids:
                        continue
                    altre = self._altre_chiavi_esterne(cur, tabella, colonna)
                    logico = self.RIFERIMENTI_LOGICI.get(tabella)
                    lette = ["id"] + [c for c, _genitore in altre]
                    if logico:
                        lette += [logico[0], logico[1]]
                    cur.execute(
                        f"SELECT {', '.join(lette)} FROM {tabella} "
                        f"WHERE {colonna} IN %s",
                        (tuple(ids),))
                    nostre, miste = [], []
                    for riga in cur.fetchall():
                        estranei = [
                            f"{c}={riga[c]}" for c, genitore in altre
                            if riga[c] is not None
                            and int(riga[c]) not in set(perimetro.get(genitore, ()))
                        ]
                        estranei += self._riferimento_logico_estraneo(
                            tabella, riga, perimetro)
                        (miste if estranei else nostre).append(
                            (int(riga["id"]), estranei))
                    if nostre:
                        self.owned_child_ids[tabella] = tuple(sorted(
                            set(self.owned_child_ids.get(tabella, ()))
                            | {i for i, _e in nostre}))
                    if miste:
                        # NON entrano nel perimetro: restano estranee, e il
                        # preflight si fermera' su di loro come su qualunque
                        # altra dipendenza non nostra.
                        self.report.fail(
                            "CLEAN-FIGLIE-DERIVATE",
                            f"{tabella}: {len(miste)} righe pendono da un "
                            f"{genitore} nostro ma puntano ANCHE fuori dal "
                            f"perimetro ({miste[0][1]}). Restano dove sono: "
                            "una relazione mista non e' del run.")
                    self.report.note(
                        "CLEAN-FIGLIE-DERIVATE",
                        f"{tabella}: {len(nostre)} righe pendono da "
                        f"{len(ids)} {genitore} delle agenzie dedicate "
                        f"(altre FK esaminate: {len(altre)}; "
                        f"{len(miste)} miste escluse). Vale solo con i "
                        "processi platform-wide sospesi, come attesta "
                        "--with-dedicated-agencies")
        except Exception as exc:
            self.owned_child_ids = {}
            self.report.fail(
                "CLEAN-FIGLIE-DERIVATE",
                f"figlie derivate non fotografabili ({type(exc).__name__}): "
                "senza i loro id non si possono ne' cancellare ne' verificare, "
                "e un CASCADE del genitore le porterebbe via senza prova",
            )
            return f"figlie derivate non fotografabili ({type(exc).__name__})"
        return None

    def _snapshot_owned_children(self) -> None:
        """Le figlie che il RUN ha creato senza mai vederne l'id.

        IL RUN 58aa0e189aaa HA DIMOSTRATO CHE QUESTA CATEGORIA ESISTE.

        Il preflight si e' fermato su `property_watch_observations.watch_id=2`
        e ha bloccato l'intero cleanup. Non era una riga altrui: e' comparsa
        PROPRIO PERCHE' la fixture PROPERTY_WATCH ha ricominciato a funzionare.
        `ensure_watch_with_baseline_scoped` - cioe' `POST .../initialize` -
        scrive il watch E una osservazione `watch_started` nella stessa
        transazione, una per agenzia dedicata. Il commento che diceva "una riga
        qui dentro e' qualcosa che non abbiamo messo noi" descriveva un
        backend che non fa piu' quello che si pensava facesse.

        L'APPARTENENZA E' ESPLICITA, NON "FIGLIA DI UNA RIGA NOSTRA".

        Prendere tutte le osservazioni del nostro watch sarebbe comodo e
        sbagliato: la scansione periodica e il motore invisible-sale scrivono
        nella stessa tabella e sullo stesso watch, e quelle righe non sono del
        run. Il criterio e' la chiave di idempotenza che il repository DERIVA
        dallo stima_id del watch - `property_watch:watch_started:stima:N:v1` -
        insieme al tipo e alla sorgente. Una riga che non corrisponde resta
        ESTRANEA e continua a bloccare: la guardia non viene indebolita, viene
        resa capace di distinguere.

        DUE CRITERI, E LA DIFFERENZA E' NELLA PROVA CHE OFFRONO.

        * CHIAVE DERIVATA, per `property_watch_observations`. Il watch vive in
          un'agenzia dedicata, ma la sua tabella la scrivono anche la scansione
          periodica e il motore invisible-sale: "figlia di una riga nostra" non
          basterebbe, e il criterio e' la chiave che il repository deriva.

        * GENITORE IN UN'AGENZIA CHE ABBIAMO CREATO NOI, per le figlie
          dichiarate in `OWNED_BY_PARENT`. **Non basta da solo**, e una
          versione precedente di questa frase diceva il contrario: sosteneva
          che ogni riga pendente da una nostra fosse del run "per costruzione,
          non c'e' nessun altro che possa averla scritta". E' falso - un
          processo platform-wide scrive nelle nostre agenzie come in ogni
          altra. Le condizioni sono tre e stanno tutte in
          `_snapshot_owned_by_parent`: genitore fotografato, processi
          platform-wide sospesi (condizione OPERATIVA, attestata da
          `--with-dedicated-agencies` e non rilevabile da qui), e nessun altro
          riferimento della riga fuori dal perimetro - chiavi esterne del
          catalogo e riferimenti logici compresi.

        Fail-closed come le altre due istantanee: non poter leggere non e'
        "non ce n'erano".
        """
        self.owned_child_ids = {}
        guasto = self._snapshot_owned_by_parent()
        if guasto:
            return guasto
        watch_ids = self.child_parents.get("property_watches")
        if not watch_ids:
            return None
        try:
            with self.db.read() as cur:
                cur.execute(
                    "SELECT id, stima_id FROM property_watches WHERE id IN %s",
                    (tuple(watch_ids),))
                attese = {
                    WATCH_BASELINE_KEY.format(stima_id=int(r["stima_id"]))
                    for r in cur.fetchall() if r["stima_id"] is not None
                }
                cur.execute(
                    "SELECT id, idempotency_key, observation_type, source "
                    "  FROM property_watch_observations WHERE watch_id IN %s",
                    (tuple(watch_ids),))
                nostre, estranee = [], []
                for riga in cur.fetchall():
                    e_nostra = (
                        riga["idempotency_key"] in attese
                        and riga["observation_type"] == WATCH_BASELINE_TYPE
                        and riga["source"] == WATCH_BASELINE_SOURCE
                    )
                    (nostre if e_nostra else estranee).append(int(riga["id"]))
                if nostre:
                    self.owned_child_ids["property_watch_observations"] = tuple(
                        sorted(nostre))
                self.report.note(
                    "CLEAN-OSSERVAZIONI",
                    f"{self._etichetta_figlia('property_watch_observations')} sui "
                    f"{len(watch_ids)} watch del run: {len(nostre)} riconosciute "
                    f"come create da initialize (chiave derivata), "
                    f"{len(estranee)} estranee")
        except Exception as exc:
            self.owned_child_ids = {}
            self.report.fail(
                "CLEAN-OSSERVAZIONI",
                f"osservazioni del watch non fotografabili ({type(exc).__name__}): "
                "senza questa distinzione le righe create dal run e quelle "
                "altrui sarebbero indistinguibili, e cancellare le une "
                "significherebbe rischiare le altre",
            )
            return f"osservazioni del watch non fotografabili ({type(exc).__name__})"
        return None

    def _blocking_children(self, genitore: str) -> list:
        """Figlie ON DELETE RESTRICT di un genitore del run, contate per id.

        RESTRICT e' l'unica azione che va guardata PRIMA: una figlia CASCADE
        se ne andrebbe da sola, una SET NULL sopravvive per progetto, una
        RESTRICT invece non lascia cancellare il genitore e fa cadere l'intera
        transazione del cleanup - non una DELETE, tutte.

        Ritorna l'elenco "tabella=n" delle righe che impedirebbero la
        cancellazione, vuoto se non ce ne sono. Un errore di lettura NON e'
        "non ce ne sono": si riporta come ostacolo, perche' procedere alla
        cieca porterebbe esattamente al fallimento che si vuole evitare.
        """
        ids = self.child_parents.get(genitore)
        if not ids:
            return []
        bloccanti = []
        try:
            with self.db.read() as cur:
                for fk in self.CHILD_FOREIGN_KEYS:
                    if fk.parent != genitore or fk.on_delete != "RESTRICT":
                        continue
                    # LE NOSTRE NON BLOCCANO NOI. Vengono cancellate per id
                    # poche righe piu' sotto, nella stessa transazione e prima
                    # del genitore: contarle come ostacolo significherebbe
                    # fermare il cleanup a causa di cio' che il cleanup stesso
                    # sta per rimuovere. Tutto il resto blocca ancora.
                    nostre = self.owned_child_ids.get(fk.table)
                    if nostre:
                        cur.execute(
                            f"SELECT COUNT(*) AS n FROM {fk.table} "
                            f" WHERE {fk.column} IN %s AND NOT (id IN %s)",
                            (ids, nostre))
                    else:
                        cur.execute(
                            f"SELECT COUNT(*) AS n FROM {fk.table} WHERE {fk.column} IN %s",
                            (ids,))
                    n = int(cur.fetchone()["n"])
                    if n:
                        bloccanti.append(f"{fk.table}={n}")
        except Exception as exc:
            return [f"verifica non eseguibile ({type(exc).__name__})"]
        return bloccanti

    def cleanup_dedicated_agencies(self) -> None:
        """Le agenzie temporanee, e tutto cio' che il run vi ha messo dentro.

        Ordine esplicito, per id. Poi il catalogo, e solo per GUARDARE: se una
        tabella che non avevamo previsto referenzia ancora l'agenzia, la si
        nomina e si fallisce - non la si cancella. Cancellare cio' che non era
        stato considerato e' il modo in cui un cleanup diventa il danno.
        """
        # Invocata: da qui in avanti `verify_no_residue` ha il diritto di
        # pretendere che gli effetti delle agenzie dedicate siano spariti. Se
        # questa chiamata si rifiuta di cancellare - o fallisce - il residuo
        # che la verifica trovera' sara' vero, ed e' giusto che lo dica.
        self.dedicated_cleanup_done = True
        if not self.created_agency_ids:
            return
        motivo = self._destructive_db_blocked()
        if motivo:
            self.report.fail(
                "CLEAN-DEDICATA",
                f"agenzie temporanee {list(self.created_agency_ids)} NON rimosse: "
                f"{motivo}",
            )
            return
        ids = tuple(self.created_agency_ids)

        # LE FIGLIE RESTRICT DEL WATCH, PRIMA DI TOCCARE IL WATCH.
        #
        # `property_watch_observations.watch_id` e
        # `invisible_sale_opportunities.watch_id` sono ON DELETE RESTRICT:
        # finche' una riga esiste, `DELETE FROM property_watches` non fallisce
        # "in parte", fallisce del tutto e porta con se' l'intera transazione
        # di questo metodo.
        #
        # LA VERSIONE PRECEDENTE DI QUESTO COMMENTO ERA SMENTITA DAI FATTI.
        # Diceva: "il run crea il watch con `initialize` e non fa girare ne' la
        # scansione ne' il motore invisible-sale, quindi una riga qui dentro e'
        # qualcosa che non abbiamo messo noi". Il run 58aa0e189aaa lo ha
        # contraddetto: `initialize` scrive ANCHE l'osservazione di baseline,
        # nella stessa transazione del watch, e il preflight si e' fermato su
        # due righe create da noi bloccando l'intero cleanup.
        #
        # La distinzione ora la fa `_snapshot_owned_children`, per chiave
        # derivata. Le nostre vengono cancellate qui sotto; quelle di chiunque
        # altro - la scansione, il motore invisible-sale - continuano a
        # bloccare e si nominano, non si rimuovono.
        bloccanti = self._blocking_children("property_watches")
        if bloccanti:
            self.report.fail(
                "CLEAN-DEDICATA",
                f"figlie RESTRICT del watch presenti: {bloccanti}. Nessuna "
                f"cancellazione: le agenzie {list(ids)} restano, e quelle righe "
                "vanno esaminate prima. " + self.RECOVERY_HINT,
            )
            return
        try:
            with self.db.write() as cur:
                # LE FIGLIE RICONOSCIUTE, PER ID E SOTTO LOCK, PRIMA DEL
                # GENITORE.
                #
                # `property_watch_observations` non ha `agency_id`, quindi non
                # puo' stare in DEDICATED_TABLES: si cancella per gli id
                # fotografati, mai per `watch_id` - che porterebbe via anche
                # una riga scritta da una scansione fra l'istantanea e adesso.
                #
                # `FOR UPDATE NOWAIT` e' la differenza fra cancellare cio' che
                # si e' guardato e cancellare cio' che nel frattempo e'
                # cambiato: se un'altra transazione tiene una di quelle righe,
                # qui si solleva subito invece di attendere, e il ramo di
                # cattura lascia tutto dov'e'.
                for tabella, figlie in sorted(self.owned_child_ids.items()):
                    cur.execute(
                        f"SELECT id FROM {tabella} WHERE id IN %s FOR UPDATE NOWAIT",
                        (figlie,))
                    bloccate = {int(r["id"]) for r in cur.fetchall()}
                    mancanti = set(figlie) - bloccate
                    if mancanti:
                        # Sparite fra l'istantanea e adesso: non e' un errore
                        # da nascondere, ma nemmeno una ragione per fermarsi -
                        # le righe che restano si cancellano, e il report lo
                        # dice.
                        self.report.note(
                            "CLEAN-OSSERVAZIONI",
                            f"{self._etichetta_figlia(tabella)}: {len(mancanti)} "
                            "righe fotografate non sono piu' presenti al "
                            "momento del lock")
                    if bloccate:
                        cur.execute(
                            f"DELETE FROM {tabella} WHERE id IN %s",
                            (tuple(sorted(bloccate)),))
                for table, column in self.DEDICATED_TABLES:
                    cur.execute(
                        f"DELETE FROM {table} WHERE {column} IN %s", (ids,))
                cur.execute(
                    "DELETE FROM operator_users WHERE id IN %s",
                    (tuple(self.created_user_ids) or (0,),))
                cur.execute("DELETE FROM agencies WHERE id IN %s", (ids,))
        except Exception as exc:
            self.report.fail(
                "CLEAN-DEDICATA",
                f"cancellazione non riuscita ({type(exc).__name__}). Agenzie "
                f"{list(ids)} POTENZIALMENTE PRESENTI. "
                + self.RECOVERY_HINT,
            )
            return

        # Verifica, e ricerca di dipendenze impreviste.
        try:
            with self.db.read() as cur:
                cur.execute("SELECT COUNT(*) AS n FROM agencies WHERE id IN %s", (ids,))
                rimaste = int(cur.fetchone()["n"])
                inattese = []
                if rimaste:
                    cur.execute(
                        """
                        SELECT ns.nspname AS schema, cl.relname AS tabella,
                               att.attname AS colonna
                          FROM pg_constraint con
                          JOIN pg_class cl ON cl.oid = con.conrelid
                          JOIN pg_namespace ns ON ns.oid = cl.relnamespace
                          CROSS JOIN LATERAL unnest(con.conkey, con.confkey)
                               AS k(child_attnum, parent_attnum)
                          JOIN pg_attribute att ON att.attrelid = con.conrelid
                               AND att.attnum = k.child_attnum
                          JOIN pg_attribute patt ON patt.attrelid = con.confrelid
                               AND patt.attnum = k.parent_attnum
                         WHERE con.contype = 'f'
                           AND con.confrelid = 'public.agencies'::regclass
                           AND patt.attname = 'id'
                        """
                    )
                    for riga in cur.fetchall():
                        cur.execute(
                            f"SELECT COUNT(*) AS n FROM {riga['schema']}.{riga['tabella']} "
                            f"WHERE {riga['colonna']} IN %s", (ids,))
                        n = int(cur.fetchone()["n"])
                        if n:
                            inattese.append(
                                f"{riga['schema']}.{riga['tabella']}.{riga['colonna']}={n}")
        except Exception as exc:
            self.report.fail("CLEAN-DEDICATA",
                             f"verifica non eseguibile ({type(exc).__name__}): non si "
                             f"puo' affermare che le agenzie {list(ids)} siano sparite")
            return

        if rimaste:
            self.report.fail(
                "CLEAN-DEDICATA",
                f"{rimaste} agenzie temporanee ANCORA PRESENTI: {list(ids)}. "
                + (f"Dipendenze non previste dal cleanup: {inattese}. "
                   if inattese else "Nessuna dipendenza residua individuata. ")
                + self.RECOVERY_HINT,
            )
        else:
            self.report.note(
                "CLEAN-DEDICATA",
                f"agenzie temporanee {list(ids)} rimosse, 0 residue")

    #: Cosa fare a mano se il cleanup non riesce. Nessun dato personale: solo
    #: id numerici e nomi di tabella.
    RECOVERY_HINT = (
        "RECUPERO: le righe sono identificate dagli id agenzia sopra. "
        "Rimuovere nell'ordine followup_actions, tasks, contacts, "
        "agency_memberships, operator_users, poi agencies, filtrando SEMPRE per "
        "quegli id e mai per prefisso dello slug. Se una tabella non prevista "
        "compare fra le dipendenze, va esaminata prima di cancellare."
    )

    #: Cosa dire quando il bucket non e' stato ripulito. Nomina la colonna in
    #: cui la chiave si trova ancora - ed e' un'affermazione che resta vera
    #: solo perche' `_destructive_db_blocked` impedisce di cancellare quelle
    #: righe subito dopo.
    STORAGE_BLOCK_HINT = (
        "Il cleanup DISTRUTTIVO del database e' SOSPESO per questo motivo: "
        "property_documents conserva la storage_key degli oggetti rimasti, e "
        "cancellarla renderebbe l'oggetto irrecuperabile. RECUPERO: leggere "
        "storage_key per quegli id, rimuovere gli oggetti dal bucket, poi "
        "cancellare le righe. Le identita' e le sessioni del run sono state "
        "rimosse comunque: nessuna credenziale resta viva."
    )

    #: Effetti che vivono DENTRO le agenzie dedicate e se ne vanno con loro.
    #: Non hanno una cancellazione propria: `cleanup_dedicated_agencies` li
    #: rimuove per agency_id. Verificarli prima di quella chiamata segnalerebbe
    #: come residuo una riga che il passo successivo avrebbe portato via.
    DEDICATED_EFFECT_TABLES = ("flow_events", "stime", "seller_timeline_events")

    #: Le figlie delle righe create nelle agenzie dedicate, con l'azione VERA.
    #:
    #: Due di queste tre sono RESTRICT, non CASCADE: chiamarle "figli CASCADE"
    #: - come faceva la versione precedente - descriveva un comportamento che
    #: non hanno. Una figlia CASCADE se ne va con il genitore; una RESTRICT
    #: impedisce al genitore di andarsene, e la differenza e' fra un residuo da
    #: verificare e una transazione di cleanup che fallisce per intero.
    #:
    #: La colonna e' quella VERA: `property_watch_observations` si lega al
    #: watch con `watch_id`, non con `property_watch_id`, e una query sul nome
    #: sbagliato non risponde "0 figlie" ma solleva UndefinedColumn - un errore
    #: che il ramo di cattura tradurrebbe in "verifica non eseguibile".
    CHILD_FOREIGN_KEYS = (
        ChildFk("property_watch_observations", "watch_id", "property_watches", "RESTRICT"),
        ChildFk("invisible_sale_opportunities", "watch_id", "property_watches", "RESTRICT"),
        ChildFk("seller_revival_suppressions", "contact_id", "contacts", "CASCADE"),
    )

    #: Le figlie degli effetti delle API, con l'azione VERA. Anche qui la
    #: versione precedente le chiamava tutte CASCADE: `owner_audit_log` e i
    #: riferimenti secondari di `buy_request_history` sono SET NULL, e per loro
    #: il conteggio "per id del genitore" dopo la cancellazione e' vuoto per
    #: costruzione - la colonna a quel punto e' NULL.
    EFFECT_FOREIGN_KEYS = (
        ChildFk("property_status_history", "property_id", "properties", "CASCADE"),
        ChildFk("buy_request_history", "buy_request_id", "buy_requests", "CASCADE"),
        ChildFk("match_runs", "property_id", "properties", "CASCADE"),
        ChildFk("match_runs", "buy_request_id", "buy_requests", "CASCADE"),
        ChildFk("owner_audit_log", "property_id", "properties", "SET NULL"),
        # Il percorso documentale. Tutte e quattro CASCADE, ed e' il motivo
        # per cui non bastava lasciarle fuori dal perimetro e fidarsi: un
        # CASCADE non fallisce, porta via in silenzio: `DELETE FROM
        # owner_accounts` avrebbe cancellato notifiche e letture senza che
        # nessun predicato di appartenenza le avesse mai guardate.
        ChildFk("owner_notifications", "owner_account_id", "owner_accounts", "CASCADE"),
        ChildFk("owner_notifications", "property_id", "properties", "CASCADE"),
        ChildFk("owner_document_reads", "owner_account_id", "owner_accounts", "CASCADE"),
        ChildFk("owner_document_reads", "shared_document_id",
                "owner_shared_documents", "CASCADE"),
    )

    #: Gli id che il run traccia FUORI da `created_rows`, con la tabella su
    #: cui vanno verificati. `created_rows` raccoglie solo le fixture di
    #: dominio: un run puo' non averne nessuna - tutti i domini BLOCKED prima
    #: della creazione - e avere comunque prodotto documenti, match, proposte,
    #: vendite, conti proprietario e agenzie temporanee. Legare la pulizia e
    #: la verifica a `created_rows` significava, in quel caso, dichiarare
    #: "niente da rimuovere" e "niente da verificare" su un database sporco.
    TRACKED_ID_TABLES = (
        ("created_sale_ids", "property_sales"),
        ("created_proposal_ids", "property_proposals"),
        ("created_match_ids", "matches"),
        ("created_owner_account_ids", "owner_accounts"),
    )

    def _tracked_totals(self) -> dict:
        """{categoria: quante} per tutto cio' che il run ha creato, senza gli zeri."""
        totali = {tabella: len(getattr(self, attributo))
                  for attributo, tabella in self.TRACKED_ID_TABLES}
        totali["righe di dominio"] = sum(len(v) for v in self.created_rows.values())
        totali["effetti"] = sum(len(v) for v in self.created_effects.values())
        totali["agenzie dedicate"] = len(self.created_agency_ids)
        return {nome: n for nome, n in totali.items() if n}

    def _created_nothing(self) -> bool:
        """Vero solo se NESSUNA categoria ha id. Non "nessuna riga di dominio"."""
        return not self._tracked_totals()

    def registra_identita_owner(self) -> None:
        """Scrive nel report gli id OWNER che questo run ha creato.

        PERCHE' UNA RIGA DI REPORT E' UNA PROVA, E LA SUA ASSENZA UN BUCO

        I sei audit `560, 562, 563, 564, 566, 567` hanno `owner_account_id`
        NULL e nominano conti 4/5, token 6/7 e sessioni 6/7 che non esistono
        piu'. Il codice dimostra che quel NULL viene da un `ON DELETE SET NULL`
        - i tre soli autori di quelle righe passano sempre un conto - quindi il
        conto c'era ed e' stato cancellato.

        Chi lo avesse creato pero' NON si puo' dimostrare, e non per mancanza
        di dati sul TEST: perche' nessun run precedente ha lasciato scritti i
        propri id OWNER. Non esiste l'elenco con cui confrontare 4 e 5.

        Questa riga non serve a quei sei - e' troppo tardi - ma chiude il buco
        in avanti: il prossimo audit senza radice sara' attribuibile con un
        confronto invece che con un ragionamento. Gli id di conti, token e
        sessioni sono numeri, non segreti: nessun hash e nessun token grezzo
        passa di qui.
        """
        conti = self.created_owner_account_ids
        if not conti:
            self.report.note("OWNER-identita",
                             "nessun conto proprietario creato da questo run")
            return
        try:
            with self.db.read() as cur:
                cur.execute("SELECT id FROM owner_access_tokens "
                            " WHERE owner_account_id IN %s ORDER BY id",
                            (tuple(conti),))
                token = [int(r["id"]) for r in cur.fetchall()]
                cur.execute("SELECT id FROM owner_sessions "
                            " WHERE owner_account_id IN %s ORDER BY id",
                            (tuple(conti),))
                sessioni = [int(r["id"]) for r in cur.fetchall()]
        except Exception as exc:                       # pragma: no cover - difensivo
            self.report.fail(
                "OWNER-identita",
                f"id OWNER non leggibili ({type(exc).__name__}): un audit senza "
                "radice trovato in futuro non sara' attribuibile a questo run")
            return
        self.report.note(
            "OWNER-identita",
            f"conti {sorted(conti)}, token {token}, sessioni {sessioni}: "
            "registrati perche' un audit senza radice trovato domani sia "
            "confrontabile con questo run")

    def _destructive_db_blocked(self) -> str | None:
        """Il motivo per cui nessuna DELETE puo' partire, o None.

        Un solo punto di decisione, chiamato da ogni metodo che cancella: due
        guardie separate si mutano una alla volta, e la sopravvissuta
        nasconderebbe l'altra.
        """
        return self.blocking_reason

    #: Le tabelle da cui questo cleanup cancella righe. Non e' documentazione:
    #: e' il perimetro su cui deve girare la guardia delle dipendenze, ed e'
    #: l'insieme su cui la prova di completezza interroga lo schema. Ogni FK
    #: NON-CASCADE che punta a una di queste tabelle e' rilevante, perche' o
    #: impedisce la cancellazione (RESTRICT) o modifica in silenzio una riga
    #: che sopravvive (SET NULL) - e in entrambi i casi e' una riga che
    #: potrebbe non essere nostra.
    CLEANUP_PARENTS = (
        "contacts", "properties", "buy_requests", "tasks",
        "seller_timeline_events",
        "matches", "property_proposals", "property_sales",
        "owner_accounts", "property_documents", "owner_shared_documents",
        "property_watches", "stime", "flow_events", "flow_executions",
        "next_best_actions", "followup_actions",
        "agencies", "agency_memberships", "operator_users",
        # Anche le tabelle degli EFFETTI: le si cancella per predicato, quindi
        # sono genitori tanto quanto le altre. Lasciarle fuori significava che
        # nessuno aveva mai guardato le FK entranti - e `match_runs` ne ha tre
        # non-CASCADE (`match_refresh_history` e `matches.latest_run_id`), che
        # su una riga altrui sarebbero tre campi azzerati in silenzio.
        "property_status_history", "buy_request_history", "match_runs",
        "property_contacts", "owner_audit_log",
        # I figli delle fixture raccolti dopo il run 52f6d97b5214.
        "property_sale_sellers", "owner_property_access", "owner_access_tokens",
        "owner_sessions", "operator_sessions", "match_requirement_results",
        # La baseline che `initialize` scrive insieme al watch. E' RESTRICT
        # verso `property_watches`: finche' resta, la cancellazione del watch
        # non fallisce "in parte", fallisce del tutto.
        "property_watch_observations",
        # Il percorso documentale: notifica alla pubblicazione, lettura allo
        # scaricamento. Run 42e32975ccd6.
        "owner_notifications", "owner_document_reads",
        # Il lead che la fixture NEXT_BEST_ACTION crea per avere un segnale.
        "leads",
        # Trovate cercandole, non da un run fallito: la valutazione delle
        # regole FLOW puo' scrivere entrambe dentro un'agenzia dedicata.
        "flow_suppressions", "flow_action_records",
    )

    #: Le tabelle delle agenzie dedicate di cui servono gli ID PRIMA del
    #: cleanup. Li' si cancella per `agency_id`, quindi nessun id viene mai
    #: registrato: senza istantanea quelle righe non entrerebbero nel
    #: perimetro della guardia, e `tasks` e `flow_executions` - che hanno FK
    #: non-CASCADE entranti - sarebbero cancellate senza che nessuno abbia
    #: guardato chi le referenzia.
    DEDICATED_SNAPSHOT_TABLES = (
        "contacts", "property_watches", "tasks",
        "flow_executions", "flow_events", "stime",
        # `leads` si cancella per `agency_id` come le altre, quindi dei suoi
        # id non resta traccia: senza istantanea le sue otto figlie SET NULL
        # non verrebbero mai interrogate dalla guardia.
        "leads",
    )

    #: Il tipo pubblico dei documenti condivisi, e NON e' una preferenza.
    #:
    #: `owner/schemas.py` dichiara `SharedDocumentType` come un Literal chiuso:
    #: mandate, floor_plan, ape, cadastral_extract, photo_report,
    #: activity_report, information. "other" NON e' fra questi, ed e' il 422
    #: del run 52f6d97b5214 - la condivisione non veniva mai creata, quindi la
    #: lista documenti del portale restava vuota per entrambe le agenzie e il
    #: confronto "B non vede i documenti di A" non provava niente.
    #:
    #: Il tipo del documento dell'IMMOBILE e' un'altra cosa:
    #: `property/schemas.py` lo dichiara `str(min_length=1, max_length=80)`,
    #: cioe' testo libero, e "other" li' e' valido. Due campi con nomi simili e
    #: due contratti diversi: e' il motivo per cui lo stesso valore passava da
    #: una parte e veniva rifiutato dall'altra.
    OWNER_PUBLIC_DOCUMENT_TYPE = "information"
    PROPERTY_DOCUMENT_TYPE = "other"

    #: Il modulo di provenienza dell'evento FLOW. Obbligatorio in
    #: `flow/schemas.EventCreate`, e senza valore predefinito: ometterlo e' il
    #: 422 del run 38e341f68f8a.
    FLOW_SOURCE_MODULE = "core"

    # ------------------------------------------------------------------
    # L'ESECUZIONE FLOW: QUALE REGOLA, E PERCHE' PROPRIO QUELLA
    #
    # `/api/flow/executions` elenca `flow_executions`, e una riga li' dentro
    # nasce in un solo modo che non percorra l'intero tenant: `POST
    # /api/flow/events` con un evento il cui `event_type` e `entity_type`
    # corrispondono a una regola ATTIVA. Il servizio carica QUELL'entita' e
    # nessun'altra (`flow/service.py::_process_saved_event`), quindi la
    # scrittura resta confinata a una riga che questo run possiede.
    #
    # Su TEST sono attive R001, R004 e le cinque OWNER (R008-R012). Delle tre
    # famiglie:
    #
    #   R008-R012  esigono un'entita' `owner_feedback`, che questo run non
    #              crea e che porterebbe una tabella nuova nel perimetro
    #              distruttivo. Il costo non e' il codice: e' il perimetro.
    #   R001       esige un `lead`. Il run ne crea uno, ma solo nelle agenzie
    #              DEDICATE, e li' `/api/flow/executions` non e' la riga
    #              BLOCKED da chiudere.
    #   R004       esige una `buy_request`, ed e' esattamente la fixture che
    #              ogni operatore della matrice crea nella PROPRIA agenzia
    #              condivisa. Nessuna riga nuova, nessuna tabella nuova.
    #
    # LA CORRISPONDENZA NON E' L'OBIETTIVO, E VA DETTO CHIARO. `execute_live`
    # inserisce l'esecuzione PRIMA di sapere se la regola corrisponde, e la
    # chiude con `status='not_matched'` quando non corrisponde: la riga esiste
    # in entrambi i casi, ed e' la riga di cui si prova l'isolamento. La
    # fixture sceglie deliberatamente il ramo NON corrispondente, perche' e'
    # l'unico i cui effetti siano UNO: l'esecuzione. Il ramo corrispondente
    # aggiungerebbe un `flow_action_records` e un `tasks` in un'agenzia
    # CONDIVISA, dove nulla si cancella per `agency_id`.
    #
    # E la non corrispondenza e' DETERMINISTICA, non fortunata:
    # `flow/engine.py` per R004 esige `next_action_at IS NOT NULL`, la fixture
    # BUY non lo valorizza (`buy/schemas.py` lo dichiara opzionale e nessuna
    # chiamata della matrice lo scrive), e il parametro `overdue_hours` non
    # entra nemmeno nel confronto quando il valore e' NULL. Qualunque sia la
    # configurazione di questo TEST, l'esito e' lo stesso.
    #
    # Non e' pero' un'assunzione lasciata implicita: `certify_flow` LEGGE
    # `next_action_at` dal database prima di inviare l'evento e, se non fosse
    # NULL, non invia niente e riporta BLOCKED. Una fixture BUY cambiata in
    # futuro produce un BLOCKED spiegato, non una riga imprevista sul TEST.
    FLOW_EXECUTION_TRIGGER = "buy.next_action_due"
    FLOW_EXECUTION_ENTITY = "buy_request"
    FLOW_EXECUTION_RULE = "FLOW-R004"
    FLOW_EXECUTION_SOURCE_MODULE = "buy"

    #: Il tipo di evento che `property_watch` esige per poter inizializzare un
    #: watch. `_baseline_for_stima_scoped` legge la valutazione completata da
    #: `seller_timeline_events` e, se non la trova, solleva ValidationError -
    #: che il router traduce in 400. E' l'`initialize -> 400` del run
    #: 38e341f68f8a: la stima c'era, la sua valutazione no.
    STIMA_COMPLETATA_EVENT = "stima_completata"

    #: Gli effetti che si cancellano per id, in ordine di FK:
    #: (la baseline del watch e' definita a livello di modulo, vedi
    #: WATCH_BASELINE_KEY: la usa anche il test che la confronta con il
    #: repository reale.)
    #: `owner_shared_documents.property_document_id` e' RESTRICT verso
    #: `property_documents`, quindi la condivisione va rimossa per prima.
    #:
    #: GLI EFFETTI FLOW NON STANNO QUI, ed e' una decisione. Gli eventi delle
    #: agenzie DEDICATE sono registrati nella stessa chiave
    #: `created_effects["flow_events"]` di quelli condivisi, e una
    #: cancellazione per id li prenderebbe tutti: `DEDICATED_TABLES` potrebbe
    #: perdere `flow_events` senza che nessun test se ne accorga, perche' le
    #: righe sarebbero gia' sparite per un'altra strada. I condivisi si
    #: cancellano in `cleanup_shared_flow_fixtures`, sui loro id e solo su
    #: quelli - com'e' gia' per stime, eventi e watch condivisi.
    EFFECT_BY_ID_TABLES = ("owner_shared_documents", "property_documents")

    #: Tabelle che le NOSTRE operazioni popolano, e le colonne con cui puntano
    #: altrove. Una riga e' del run solo se OGNI riferimento non nullo cade nel
    #: perimetro: e' la condizione che distingue lo storico scritto dal nostro
    #: archive_property da una visita fissata da un operatore vero sullo stesso
    #: immobile. Nessuna di queste cancellazioni e' per prefisso o per data.
    EFFECT_TABLES = (
        ("property_documents", ("property_id",)),
        ("property_contacts", ("property_id", "contact_id")),
        ("property_status_history", ("property_id",)),
        ("buy_request_history", ("buy_request_id", "property_id", "match_id", "task_id")),
        ("match_runs", ("buy_request_id", "property_id")),
        # `match_requirement_results` DOPO `match_runs`, e non e' un refuso.
        # Questo elenco e' in ordine di DIPENDENZA - prima il genitore - e
        # serve all'istantanea, che deve conoscere gli id di `match_runs` per
        # poter riconoscere le sue figlie. Le CANCELLAZIONI lo percorrono al
        # contrario, perche' li' il vincolo e' opposto: la figlia per prima,
        # altrimenti il CASCADE del genitore se la porterebbe via senza
        # passare dal predicato di appartenenza.
        ("match_requirement_results", ("match_run_id",)),
        ("owner_audit_log", ("property_id", "owner_account_id")),
        # I FIGLI DELLE FIXTURE CHE IL RUN NON AVEVA MAI RACCOLTO.
        #
        # Il run 52f6d97b5214 si e' fermato qui: nessuna di queste tabelle
        # compariva nel perimetro, quindi la guardia delle dipendenze le
        # vedeva referenziare le nostre righe e le dichiarava ESTRANEE -
        # bloccando l'intero cleanup su righe che il run aveva creato lui
        # stesso, un passo prima.
        #
        # La risposta non e' esentare queste tabelle dal controllo: sarebbe
        # cieca esattamente dove serve vedere. La risposta e' raccoglierne le
        # righe per APPARTENENZA, con lo stesso predicato di tutte le altre -
        # ogni riferimento non nullo dentro il perimetro, almeno uno che ci
        # punti - cosi' che la riga del run entri nel perimetro e quella di
        # chiunque altro continui a bloccare.
        ("property_sale_sellers", ("sale_id", "contact_id")),
        ("followup_actions", ("contact_id", "lead_id", "stima_id", "task_id")),
        ("owner_property_access", ("owner_account_id", "property_id")),
        ("owner_access_tokens", ("owner_account_id",)),
        ("owner_sessions", ("owner_account_id",)),
        ("agency_memberships", ("agency_id", "operator_user_id")),
        ("operator_sessions", ("operator_user_id",)),
        # IL PERCORSO DOCUMENTALE SCRIVE DUE VOLTE PIU' DI QUANTO SEMBRI.
        #
        # Il run 42e32975ccd6 si e' fermato qui, e la forma dell'errore e'
        # ormai riconoscibile: la fixture documenti ha ricominciato a
        # funzionare - pubblicazione, elenco e scaricamento passano tutti - e
        # proprio per questo ha prodotto righe che nessuno raccoglieva.
        #
        #   publish_shared_document -> _emit_notification_event
        #       INSERT owner_notifications, uno per titolare con concessione
        #       attiva, piu' la riga di audit `notification_created`.
        #   prepare_shared_document_download -> read_shared_document
        #       UPSERT owner_document_reads, piu' l'audit
        #       `shared_document_viewed`.
        #
        # Nessuna delle due e' una figlia "nascosta": sono scritture dirette
        # del percorso che la matrice esercita apposta. Entrano qui con lo
        # stesso predicato di tutte le altre - ogni riferimento non nullo
        # dentro il perimetro, almeno uno che ci punti - quindi una lettura
        # che legasse il NOSTRO documento a un conto altrui resterebbe fuori e
        # continuerebbe a bloccare.
        ("owner_notifications", ("owner_account_id", "property_id")),
        ("owner_document_reads", ("owner_account_id", "shared_document_id")),
    )

    #: Le due tabelle in cui l'appartenenza si decide da UNA SOLA colonna.
    #:
    #: La regola generale - ogni riferimento non nullo dentro il perimetro -
    #: qui direbbe il falso. La membership dell'operatore A punta all'agenzia
    #: 1, che e' preesistente e non nostra, e verrebbe quindi dichiarata
    #: estranea: ma quella RIGA l'ha creata questo run, un istante dopo aver
    #: creato l'operatore.
    #:
    #: `operator_user_id` basta da solo perche' `operator_users` contiene
    #: SOLO identita' di questo run - sono create qui e cancellate qui, per
    #: id. Una membership di un operatore vero continua a non essere nostra,
    #: e continua a bloccare: cambia il criterio, non la severita'.
    EFFECT_OWNING_COLUMNS = {
        "agency_memberships": "operator_user_id",
        "operator_sessions": "operator_user_id",
    }

    def _perimeter(self) -> dict:
        """{tabella: ids} di tutto cio' che questo run possiede."""
        out = {t: tuple(i for i, _m, _c in e) for t, e in self.created_rows.items()}
        if self.created_match_ids:
            out["matches"] = tuple(self.created_match_ids)
        if self.created_owner_account_ids:
            out["owner_accounts"] = tuple(self.created_owner_account_ids)
        for table, ids in self.created_effects.items():
            if ids:
                # UNIONE, non sostituzione.
                #
                # `seller_timeline_events` compare in ENTRAMBI: e' la risorsa
                # del dominio SELLER_INTELLIGENCE (in `created_rows`) ed e'
                # anche l'evento `stima_completata` che la fixture
                # PROPERTY_WATCH inserisce (in `created_effects`).
                # Sovrascrivere faceva sparire dal perimetro gli id del
                # dominio, e con loro la protezione che il perimetro fornisce.
                out[table] = tuple(sorted(set(out.get(table, ())) | set(ids)))
        return out

    def _effect_predicate(self, table: str, columns: tuple, perimeter: dict, alias: str):
        """Il predicato di appartenenza per una tabella di effetti.

        Due criteri, e la differenza e' dichiarata in EFFECT_OWNING_COLUMNS:
        per quasi tutte vale la regola generale, per le due che pendono da
        `operator_users` basta quella colonna. Un solo posto in cui si decide,
        cosi' che l'istantanea, la cancellazione e la guardia non possano
        applicarne tre versioni diverse.
        """
        propria = self.EFFECT_OWNING_COLUMNS.get(table)
        if propria:
            ids = perimeter.get("operator_users")
            if not ids:
                return None
            return (f"{alias}.{propria} IN %s", [ids])
        return self._ownership_predicate(columns, perimeter, alias)

    def _root_perimeter(self) -> dict:
        """Le RADICI: cio' di cui il run conosce gli id senza cercarli.

        E' il perimetro da cui si DERIVA l'appartenenza degli effetti, e per
        questo non li contiene: sarebbe circolare.
        """
        fuori = {t: tuple(i) for t, i in self._perimeter().items()}

        def aggiungi(tabella, ids):
            if ids:
                fuori[tabella] = tuple(sorted(set(fuori.get(tabella, ())) | set(ids)))

        aggiungi("property_sales", self.created_sale_ids)
        aggiungi("property_proposals", self.created_proposal_ids)
        aggiungi("agencies", self.created_agency_ids)
        aggiungi("operator_users", self.created_user_ids)
        for genitore, ids in self.child_parents.items():
            aggiungi(genitore, ids)
        return fuori

    def _destructive_perimeter(self) -> dict:
        """{tabella: ids} di OGNI riga che questo run cancellera'.

        NON e' `_perimeter()`, e la differenza e' costata una guardia
        incompleta. `_perimeter()` serve al predicato di appartenenza: contiene
        le tabelle che gli EFFETTI referenziano, perche' e' quello che il
        predicato deve sapere. Ma il cleanup cancella anche vendite, proposte,
        identita' e agenzie temporanee, che non sono genitori di nessun
        effetto e quindi da quella struttura non compaiono - restando fuori dal
        controllo delle dipendenze soltanto perche' un'altra struttura, scritta
        per un altro scopo, non le nominava.

        `property_sales` e `property_proposals` hanno figlie RESTRICT
        (`property_sale_sellers`, e la vendita stessa verso la proposta); i
        contatti e i watch delle agenzie dedicate arrivano dall'istantanea,
        che e' l'unico posto in cui i loro id sono noti.
        """
        fuori = self._root_perimeter()

        def aggiungi(tabella, ids):
            if ids:
                fuori[tabella] = tuple(sorted(set(fuori.get(tabella, ())) | set(ids)))

        # Le righe degli effetti, che si cancellano per predicato: sono
        # genitori a loro volta. `match_runs` ha tre FK non-CASCADE entranti,
        # e finche' non compariva qui nessuno le aveva mai interrogate. Da qui
        # passano anche gli otto figli delle fixture che il run 52f6d97b5214
        # non raccoglieva - senza, la guardia li dichiarava estranei e
        # bloccava il cleanup su righe create dal run stesso.
        for tabella, ids in self.effect_rows_before.items():
            aggiungi(tabella, ids)
        # Le figlie che il backend ha scritto per conto nostro. Senza questa
        # riga il preflight del run 58aa0e189aaa dichiarava estranea
        # `property_watch_observations` - una riga creata dalla nostra stessa
        # `initialize` - e bloccava ogni cancellazione. Entrano per ID: sono
        # esattamente quelle riconosciute in `_snapshot_owned_children`, mai la
        # tabella intera.
        for tabella, ids in self.owned_child_ids.items():
            aggiungi(tabella, ids)
        return fuori

    def _ownership_predicate(self, columns: tuple, perimeter: dict, alias: str):
        """"Questa riga e' del run": ogni riferimento non nullo cade nel
        perimetro, e almeno uno ci punta davvero. Ritorna (frammento WHERE,
        parametri) o None se nessuna colonna puo' puntare dentro."""
        # colonna -> tabella genitore. Ogni colonna delle EFFECT_TABLES deve
        # comparire qui: una che mancasse solleverebbe KeyError invece di
        # essere ignorata, ed e' voluto - un riferimento non classificato non
        # puo' entrare in un predicato di appartenenza.
        genitore = {"property_id": "properties", "contact_id": "contacts",
                    "buy_request_id": "buy_requests", "match_id": "matches",
                    "task_id": "tasks", "owner_account_id": "owner_accounts",
                    "sale_id": "property_sales", "lead_id": "leads",
                    "stima_id": "stime", "agency_id": "agencies",
                    "operator_user_id": "operator_users",
                    "match_run_id": "match_runs",
                    "shared_document_id": "owner_shared_documents"}
        dentro, dentro_p, tutte, tutte_p = [], [], [], []
        for col in columns:
            ids = perimeter.get(genitore[col])
            if ids:
                dentro.append(f"{alias}.{col} IN %s"); dentro_p.append(ids)
                tutte.append(f"({alias}.{col} IS NULL OR {alias}.{col} IN %s)"); tutte_p.append(ids)
            else:
                tutte.append(f"{alias}.{col} IS NULL")
        if not dentro:
            return None
        return ("(" + " OR ".join(dentro) + ") AND " + " AND ".join(tutte),
                dentro_p + tutte_p)

    def preflight_dependencies(self) -> None:
        """La guardia PRIMA della prima DELETE, non a meta' strada.

        `_foreign_dependencies` viveva dentro `cleanup_orphan_fixtures`, che
        nel `finally` arriva TERZA: vendite, proposte, match, conti
        proprietario e il loro audit erano gia' stati cancellati da
        `cleanup_chain_fixtures` e `cleanup_owner_fixtures` quando qualcuno si
        chiedeva per la prima volta se qualcosa li referenziasse. Su quelle
        tabelle la guardia non arrivava mai in tempo: una dipendenza estranea
        si sarebbe manifestata come transazione caduta - o come una riga
        altrui azzerata da un SET NULL, che non fa cadere niente.

        Qui la si esegue per prima. Se trova qualcosa, scrive il motivo di
        blocco: da quel momento nessuna cancellazione parte, per lo stesso
        unico punto di decisione che usa il fallimento del bucket.

        `cleanup_orphan_fixtures` la ripete, e non e' ridondanza: fra questo
        istante e quello possono passare secondi in cui un operatore vero
        scrive una riga che punta alle nostre.
        """
        # UN BLOCCO GIA' DECISO NON SI CANCELLA QUI.
        #
        # Se l'istantanea e' fallita, il preflight non ha nulla da aggiungere
        # e soprattutto non deve stampare "nessuna dipendenza fuori
        # perimetro": su un perimetro incompleto quella frase sarebbe vera e
        # priva di significato, e letta di seguito al FAIL precedente
        # suonerebbe come una smentita.
        motivo = self._destructive_db_blocked()
        if motivo:
            self.report.fail(
                "CLEAN-PREFLIGHT",
                f"controllo non eseguito: {motivo}",
            )
            return
        estranee = self._foreign_dependencies()
        if not estranee:
            self.report.note(
                "CLEAN-PREFLIGHT",
                f"nessuna dipendenza fuori perimetro su "
                f"{len(self._destructive_perimeter())} tabelle da cancellare")
            return
        self.blocking_reason = (
            f"dipendenze fuori perimetro rilevate PRIMA di cancellare: "
            f"{estranee}. Nessuna DELETE viene eseguita: un CASCADE le "
            "porterebbe via, un SET NULL azzererebbe un campo, e sono "
            "entrambi danni a dati non nostri."
        )
        self.report.fail("CLEAN-PREFLIGHT", self.blocking_reason)

    def _foreign_dependencies(self) -> list:
        """Righe che puntano alle nostre e NON sono a loro volta del run.

        Ricavate dal catalogo per OID - `regclass` e non il nome, perche' una
        tabella omonima in un altro schema ha un OID diverso e confrontare
        stringhe la confonderebbe con la nostra.

        Non cancella nulla: elenca. Il catalogo serve a scoprire cio' che non
        avevamo previsto, non ad autorizzarne la rimozione.
        """
        # TUTTO cio' che si cancella, non solo le fixture di dominio.
        #
        # Fermarsi a `created_rows` lasciava senza guardia proprio le tabelle
        # con le FK piu' scomode: `property_documents` ha una figlia RESTRICT
        # (`owner_shared_documents`), `matches` ne ha una (`property_proposals`)
        # e `owner_accounts` una SET NULL (`owner_audit_log`). Cancellare la'
        # dentro senza guardare significava scoprire il problema dalla
        # transazione che cade, o non scoprirlo affatto.
        perimetro = self._destructive_perimeter()
        fuori = []
        non_dichiarate = [t for t in perimetro if t not in self.CLEANUP_PARENTS]
        if non_dichiarate:
            # Una tabella che il cleanup cancella ma che nessuno ha dichiarato
            # non e' coperta dalla prova di completezza sullo schema: le sue
            # FK non sono mai state esaminate.
            return [f"tabella cancellata ma non dichiarata in CLEANUP_PARENTS: "
                    f"{sorted(non_dichiarate)}"]
        if not perimetro:
            return []
        try:
            with self.db.read() as cur:
                for table, ids in perimetro.items():
                    cur.execute(
                        """
                        SELECT con.conrelid::regclass::text AS figlio,
                               att.attname AS colonna
                          FROM pg_constraint con
                          CROSS JOIN LATERAL unnest(con.conkey, con.confkey)
                               AS k(figlio, genitore)
                          JOIN pg_attribute att ON att.attrelid = con.conrelid
                               AND att.attnum = k.figlio
                          JOIN pg_attribute patt ON patt.attrelid = con.confrelid
                               AND patt.attnum = k.genitore
                         WHERE con.contype = 'f'
                           AND con.confrelid = ('public.' || %s)::regclass
                           AND patt.attname = 'id'
                        """,
                        (table,),
                    )
                    for riga in cur.fetchall():
                        figlio, colonna = riga["figlio"], riga["colonna"]
                        nudo = figlio.split(".")[-1]
                        esclusi = perimetro.get(nudo)
                        effetto = next((c for t, c in self.EFFECT_TABLES if t == nudo), None)
                        if effetto and not esclusi:
                            # Un effetto delle nostre API: sono estranee solo
                            # le righe che puntano FUORI dal perimetro.
                            pred = self._effect_predicate(
                                nudo, effetto, self._destructive_perimeter(), "t")
                            if pred is None:
                                cur.execute(f"SELECT COUNT(*) AS n FROM {figlio} t "
                                            f" WHERE t.{colonna} IN %s", (ids,))
                            else:
                                frammento, params_p = pred
                                cur.execute(
                                    f"SELECT COUNT(*) AS n FROM {figlio} t "
                                    f" WHERE t.{colonna} IN %s AND NOT ({frammento})",
                                    (ids, *params_p))
                            n = int(cur.fetchone()["n"])
                            if n:
                                fuori.append(f"{figlio}.{colonna}={n}")
                            continue
                        if esclusi:
                            cur.execute(
                                f"SELECT COUNT(*) AS n FROM {figlio} t "
                                f" WHERE t.{colonna} IN %s AND NOT (t.id IN %s)",
                                (ids, esclusi))
                        else:
                            cur.execute(
                                f"SELECT COUNT(*) AS n FROM {figlio} t "
                                f" WHERE t.{colonna} IN %s", (ids,))
                        n = int(cur.fetchone()["n"])
                        if n:
                            fuori.append(f"{figlio}.{colonna}={n}")
        except Exception as exc:
            # Non poter guardare non e' "non c'e' niente".
            return [f"verifica non eseguibile ({type(exc).__name__})"]
        return fuori

    def register_uploaded_document(self, shared_id: int, origin_http) -> None:
        """Lega l'oggetto caricato alla riga che ne conserva la chiave.

        `POST /owner/admin/documents/upload` crea DUE righe: il documento
        dell'immobile, che porta `storage_key`, e la condivisione che lo
        espone. La risposta restituisce l'id della condivisione, e oggi anche
        `property_document_id`.

        QUELL'ID NON E' LA FONTE. Il valore su cui si cancella arriva SEMPRE
        da `owner_shared_documents.property_document_id`, letto per
        `shared_id`: e' la colonna che la FK lega davvero all'oggetto, e' NOT
        NULL, ed e' l'unica che un DELETE dovrebbe poter seguire. Un campo
        della risposta e' un'affermazione del servizio su se stesso: se fosse
        sbagliato - un id di un altro documento, magari di un'altra agenzia -
        cancellare quello significherebbe rimuovere una riga che non e'
        nostra, e lasciare nel bucket l'oggetto che era nostro.

        Fidarsi di un id non nullo solo perche' e' non nullo era la versione
        precedente: leggeva il database solo quando il campo mancava, cioe'
        proprio nel caso in cui il campo non poteva mentire.

        Se i due divergono si dichiara il disallineamento e si procede SOLO
        con l'id del database. Se il database non e' interrogabile, o non
        restituisce l'origine, l'oggetto non e' piu' associabile ad alcun id
        noto - nessun censimento SQL lo vedrebbe mai, perche' non e' sul
        database - e il cleanup distruttivo si ferma, cosi' che le righe che
        lo localizzano restino leggibili.
        """
        motivo = "owner_shared_documents non ha restituito l'origine"
        try:
            with self.db.read() as cur:
                cur.execute(
                    "SELECT property_document_id FROM owner_shared_documents "
                    " WHERE id = %s", (int(shared_id),))
                riga = cur.fetchone()
        except Exception as exc:
            riga, motivo = None, f"lettura fallita ({type(exc).__name__})"
        canonico = riga.get("property_document_id") if riga else None

        if canonico is None:
            self.blocking_reason = (
                f"il documento caricato {shared_id} non ha un property_documents "
                f"associabile ({motivo}): l'oggetto nel bucket non e' "
                "localizzabile per id. " + self.STORAGE_BLOCK_HINT)
            self.report.fail("CLEAN-STORAGE-ORIGINE", self.blocking_reason)
            return

        # REGISTRATO SUBITO, prima di guardare la risposta.
        #
        # L'ordine non e' estetico. Se la validazione dell'id HTTP sollevasse
        # - `property_document_id: "abc"` e un `int()` che esplode - l'id
        # canonico, gia' noto e valido, non verrebbe mai registrato: il
        # cleanup del bucket non saprebbe piu' dove cercare la chiave, e
        # l'oggetto resterebbe nello store per un difetto nella diagnostica di
        # un altro difetto. Prima si mette al sicuro cio' che si sa; poi si
        # esamina cio' che qualcun altro afferma.
        canonico = int(canonico)
        self.created_effects.setdefault("property_documents", []).append(canonico)

        if origin_http is None:
            self.report.note(
                "CLEAN-STORAGE-ORIGINE",
                f"documento condiviso {shared_id}: origine {canonico} risolta "
                "dal database, la risposta non la portava")
            return
        try:
            dichiarato = int(origin_http)
        except (TypeError, ValueError):
            # Non e' un numero: non e' confrontabile e non e' cancellabile. Si
            # dice cosa e' arrivato - il tipo e la lunghezza, non il valore,
            # che viene da una risposta remota - e si prosegue sul canonico.
            self.report.fail(
                "CLEAN-STORAGE-ORIGINE",
                f"documento condiviso {shared_id}: la risposta dichiara un "
                f"property_document_id non numerico ({type(origin_http).__name__}, "
                f"{len(str(origin_http))} caratteri). Non confrontabile: il "
                f"cleanup prosegue sul valore del database, {canonico}.",
            )
            return
        if dichiarato != canonico:
            # Non si blocca, e non si cancella l'id della risposta: si
            # cancella quello vero e si dice che i due non coincidono. Un id
            # sbagliato nella risposta e' un difetto del backend, non del
            # database, e il cleanup ha gia' il valore giusto.
            self.report.fail(
                "CLEAN-STORAGE-ORIGINE",
                f"documento condiviso {shared_id}: la risposta dichiara "
                f"property_document_id={dichiarato}, il database dice "
                f"{canonico}. Si procede sul valore del database; l'id della "
                "risposta NON viene cancellato, perche' potrebbe essere di un "
                "documento che non appartiene a questo run.",
            )

    def cleanup_storage_objects(self) -> None:
        """Gli oggetti caricati nello store, prima delle righe che li localizzano.

        PERCHE' NON BASTA CANCELLARE LE RIGHE

        `storage.delete_object` esiste, ma nel codice applicativo e' invocata
        SOLO sul rollback di un caricamento fallito: nessuna route rimuove
        l'oggetto quando il documento viene revocato o archiviato. Cancellare
        `property_documents` lascerebbe quindi un file nel bucket che nessuno
        sa piu' a cosa apparteneva - un residuo che nessun censimento SQL
        vedrebbe mai, perche' non e' sul database.

        La chiave si legge dalla riga, per id: la risposta dell'API non la
        restituisce (`_admin_shared_document` la esclude di proposito), ed e'
        giusto cosi'. Si cancella solo la chiave delle righe create da questo
        run, e un fallimento e' FAIL con l'id del documento - mai con la
        chiave, che e' un localizzatore.
        """
        # IL BLOCCO GLOBALE VALE ANCHE QUI, e prima di ogni altra cosa.
        #
        # `delete_object` e' l'unica cancellazione di questo script che non
        # passa dal database, e per questo era rimasta fuori dalla guardia. Ma
        # e' anche la sola irreversibile: una riga cancellata per errore si
        # ritrova in un dump, un oggetto rimosso dal bucket no.
        #
        # Sotto blocco il resto del cleanup si ferma proprio per CONSERVARE le
        # righe - la `storage_key` che localizza il file, gli id che nessuno
        # ha potuto fotografare. Svuotare il bucket mentre si conservano le
        # righe che lo indicizzano e' l'immagine speculare del difetto per cui
        # questo blocco esiste: resterebbero i puntatori, e sparirebbe cio' a
        # cui puntano.
        motivo = self._destructive_db_blocked()
        if motivo:
            self.report.fail(
                "CLEAN-STORAGE",
                f"nessun oggetto rimosso dal bucket: {motivo}",
            )
            return
        ids = self.created_effects.get("property_documents")
        if not ids:
            return

        def irrecuperabile(motivo: str) -> None:
            """FAIL, e il cleanup distruttivo si ferma.

            Segnalare e proseguire era il difetto: `cleanup_orphan_fixtures`
            cancella `property_documents`, e con quelle righe se ne va
            `storage_key` - l'unico posto dove la chiave dell'oggetto rimasto
            e' scritta. Il messaggio "la chiave e' in
            property_documents.storage_key" diventerebbe falso un istante
            dopo averlo stampato, e l'oggetto resterebbe nel bucket senza che
            nessuno possa piu' dire a cosa apparteneva.
            """
            self.blocking_reason = motivo
            self.report.fail("CLEAN-STORAGE", motivo + " " + self.STORAGE_BLOCK_HINT)

        try:
            with self.db.read() as cur:
                cur.execute(
                    "SELECT id, storage_key FROM property_documents "
                    " WHERE id IN %s AND storage_key IS NOT NULL", (tuple(ids),))
                chiavi = [(int(r["id"]), r["storage_key"]) for r in cur.fetchall()]
        except Exception as exc:
            irrecuperabile(f"chiavi non leggibili ({type(exc).__name__}): non si "
                           "puo' affermare che il bucket sia pulito.")
            return
        if not chiavi:
            self.report.note("CLEAN-STORAGE",
                             "nessun oggetto caricato: niente da rimuovere dal bucket")
            return
        try:
            from owner.document_storage import get_document_storage

            storage = get_document_storage()
        except Exception as exc:
            irrecuperabile(
                f"storage non raggiungibile ({type(exc).__name__}): {len(chiavi)} "
                f"oggetti dei documenti {[i for i, _k in chiavi]} RESTANO nel bucket.")
            return
        falliti = []
        for identificativo, chiave in chiavi:
            try:
                storage.delete_object(chiave)
            except Exception as exc:
                falliti.append(f"documento {identificativo} ({type(exc).__name__})")
        if falliti:
            irrecuperabile(f"oggetti NON rimossi dal bucket: {falliti}.")
        else:
            self.report.note("CLEAN-STORAGE",
                             f"{len(chiavi)} oggetti rimossi dal bucket")

    def cleanup_orphan_fixtures(self, agencies: dict) -> None:
        """Le righe che l'API NON rimuove fisicamente.

        PERCHE' ESISTE, E COSA E' COSTATA LA SUA ASSENZA

        Il run 22d007af7916 ha lasciato sul TEST due contatti, due immobili e
        due richieste d'acquisto, e non li ha segnalati come residui. Tre cause
        sovrapposte:

          * `DELETE /api/core/contacts/{id}` non esiste -> 405, segnalato;
          * `DELETE /api/property/properties/{id}` e
            `DELETE /api/buy/requests/{id}` ESISTONO e rispondono 200, ma i
            loro handler si chiamano `archive_property` e `archive_request` e
            fanno UPDATE ... archived_at=NOW(). La riga resta;
          * il cleanup credeva al codice di stato e non guardava.

        Un 2xx non dimostra la cancellazione fisica. Qui si cancella per SQL,
        con ID + MARCATORE + AGENZIA, e si verifica.

        L'ordine e' quello inverso della dichiarazione dei domini: `DOMAINS`
        elenca CORE prima di BUY perche' la richiesta d'acquisto NASCE dal
        contatto, quindi al contrario si cancella prima il figlio - ed e' lo
        stesso criterio che `cleanup_http_fixtures` applica alle sue.
        """
        # PRIMA DI TUTTO, prima ancora di guardare se c'e' qualcosa da
        # cancellare: se un passo precedente ha lasciato un residuo che solo
        # queste righe permettono di ritrovare, qui non si cancella nulla.
        motivo = self._destructive_db_blocked()
        if motivo:
            self.report.fail(
                "CLEAN-ORFANE",
                f"nessuna cancellazione eseguita: {motivo}",
            )
            return
        # NON "se non ci sono righe di dominio": gli effetti si cancellano qui
        # dentro, e un run che avesse creato solo documenti - tutti i domini
        # BLOCKED, il portale no - usciva di qui dicendo "niente da rimuovere"
        # e lasciava le righe sul TEST.
        if not self.created_rows and not self.created_effects:
            self.report.note("CLEAN-ORFANE",
                             "nessuna riga di dominio e nessun effetto da rimuovere via SQL")
            return

        # marcatore -> agenzia: la corrispondenza che rende verificabili le terne.
        agency_of = {self.marker(label): agency["id"]
                     for label, agency in agencies.items()}
        # L'ordine: prima le tabelle che non appartengono a un dominio - sono
        # foglie create strada facendo, come `tasks`, figlio di `contacts` -
        # poi i domini in ordine inverso di dichiarazione, che e' l'ordine
        # inverso delle dipendenze.
        domini = [d.table for d in reversed(DOMAINS)
                  if d.table and d.table in self.created_rows]
        ordine = [t for t in self.created_rows if t not in domini] + domini
        # PRIMA DI CANCELLARE: nessuna dipendenza fuori perimetro.
        #
        # Il conteggio delle righe non la rileverebbe - le nostre possono
        # essere tutte al loro posto e una riga di qualcun altro puntarle lo
        # stesso. Se fra il censimento e questo istante e' comparsa una
        # dipendenza estranea, si ferma senza cancellare e senza modificarla:
        # un CASCADE la porterebbe via, un SET NULL le azzererebbe un campo, e
        # sono entrambi danni a dati non nostri.
        estranee = self._foreign_dependencies()
        if estranee:
            self.report.fail(
                "CLEAN-ORFANE",
                f"dipendenze fuori perimetro: {estranee}. Nessuna cancellazione "
                "e nessuna modifica: vanno esaminate prima.",
            )
            return

        rimosse, residui = {}, 0
        try:
            with self.db.write() as cur:
                # GLI EFFETTI PER PRIMI, e solo quelli di nostra proprieta'.
                #
                # Tre azioni diverse, e nessuna rende superflua questa DELETE:
                #
                #   property_contacts.contact_id        RESTRICT
                #     senza rimuoverlo, il contatto non si cancella affatto.
                #   property_status_history, match_runs,
                #   property_documents, buy_request_history.buy_request_id
                #                                       CASCADE
                #     se ne andrebbero da soli - ma "da soli" vuol dire senza
                #     il predicato di appartenenza, e quello e' cio' che separa
                #     il nostro storico da una riga altrui sullo stesso
                #     immobile.
                #   owner_audit_log, e i riferimenti secondari di
                #   buy_request_history                 SET NULL
                #     NON se ne andrebbero: sopravvivrebbero con la colonna
                #     azzerata, cioe' come righe che nessuno sa piu' attribuire.
                #     Qui e' l'unico posto in cui si possono ancora cancellare
                #     per appartenenza, perche' il riferimento esiste ancora.
                #
                # Cancellare per predicato, e poi verificare per id, e' la sola
                # forma in cui tutte e tre restano sotto controllo.
                pieno = self._destructive_perimeter()
                # Prima gli effetti noti per id, nell'ordine delle FK.
                for table in self.EFFECT_BY_ID_TABLES:
                    ids = self.created_effects.get(table)
                    if ids:
                        cur.execute(f"DELETE FROM {table} WHERE id IN %s", (tuple(ids),))
                        rimosse[table] = cur.rowcount
                for table, columns in reversed(self.EFFECT_TABLES):
                    ids = self.effect_rows_before.get(table)
                    if not ids:
                        # NIENTE DI NOSTRO QUI DENTRO: nessuna DELETE.
                        #
                        # Una cancellazione che non puo' trovare nulla resta
                        # una scrittura, e su `followup_actions` la garanzia
                        # e' che il run non ne faccia MAI quando non ha
                        # creato niente - non che le sue siano circoscritte.
                        # L'istantanea l'ha gia' stabilito: se non ha trovato
                        # righe, non c'e' motivo di toccare la tabella.
                        continue
                    pred = self._effect_predicate(table, columns, pieno, "t")
                    if pred is None:
                        continue
                    frammento, params_p = pred
                    # ID FOTOGRAFATI *E* PREDICATO. Gli id dicono quali righe
                    # avevamo riconosciuto; il predicato riverifica che lo
                    # siano ancora adesso. Se una di quelle righe e' cambiata
                    # nel frattempo, non viene cancellata.
                    cur.execute(
                        f"DELETE FROM {table} t WHERE t.id IN %s AND ({frammento})",
                        [tuple(ids), *params_p])
                    rimosse[table] = cur.rowcount
                for table in ordine:
                    entries = self.created_rows[table]
                    column = entries[0][2]
                    # La colonna del marcatore viene da una costante del
                    # modulo, mai da una risposta HTTP: non c'e' un percorso
                    # per cui un dato remoto finisca in questa query.
                    if column not in ("display_name", "title", "description", "event_type"):
                        raise ValueError(f"colonna marcatore non prevista: {column!r}")

                    # TERNE, non tre elenchi indipendenti. Con
                    # `id IN (...) AND marcatore IN (...) AND agency_id IN (...)`
                    # la riga di A passerebbe anche portando il marcatore di B:
                    # tre condizioni vere separatamente non dicono che i tre
                    # valori appartengano alla STESSA riga.
                    terne = [(i, m, agency_of[m]) for i, m, _c in entries
                             if m in agency_of]
                    if len(terne) != len(entries):
                        raise ValueError("marcatore senza agenzia corrispondente")
                    segnaposti = ",".join(["(%s,%s,%s)"] * len(terne))
                    cur.execute(
                        f"DELETE FROM {table} t "
                        f" USING (VALUES {segnaposti}) AS f(id, marcatore, agency_id) "
                        f" WHERE t.id = f.id AND t.{column} = f.marcatore "
                        f"   AND t.agency_id = f.agency_id",
                        [valore for terna in terne for valore in terna],
                    )
                    rimosse[table] = cur.rowcount
            with self.db.read() as cur:
                for table in ordine:
                    ids = tuple(i for i, _m, _c in self.created_rows[table])
                    cur.execute(
                        f"SELECT COUNT(*) AS n FROM {table} WHERE id IN %s", (ids,))
                    left = int(cur.fetchone()["n"])
                    if left:
                        residui += left
                        self.report.fail(
                            "CLEAN-ORFANE",
                            f"{table}: restano {left} righe di questo run "
                            f"(rimosse {rimosse.get(table, 0)})",
                        )
        except Exception as exc:
            self.report.fail(
                "CLEAN-ORFANE",
                f"cleanup fallito ({type(exc).__name__}): le righe del run "
                f"{self.run_id} sono POTENZIALMENTE PRESENTI sul TEST",
            )
            return

        if not residui:
            dettaglio = ", ".join(f"{t}={n}" for t, n in rimosse.items())
            self.report.note("CLEAN-ORFANE",
                             f"rimosse per id+marcatore+agenzia ({dettaglio}), 0 residui")

    def verify_no_residue(self) -> None:
        """L'ULTIMA PAROLA: nessuna riga creata da questo run e' rimasta.

        Non si fida di nessun codice di stato e non dipende dalla forma delle
        route. Interroga il database per gli id creati, qualunque sia stato il
        modo in cui si e' tentato di rimuoverli.

        E' la verifica che mancava al run 22d007af7916: quattro righe
        archiviate ma presenti, e un report che non le nominava.
        """
        # L'ORDINE, PRIMA DI CONTARE.
        #
        # `flow_events` e `stime` sono registrati fra gli effetti del run, ma
        # vivono DENTRO le agenzie dedicate e se ne vanno con loro. Contarli
        # prima che `cleanup_dedicated_agencies` sia passata li troverebbe -
        # tutti - e il report direbbe "righe ancora presenti" di righe che il
        # passo successivo avrebbe rimosso. Sui doppi non si vedrebbe, perche'
        # un doppio che risponde sempre 0 non materializza niente; sul TEST
        # sarebbe un FAIL che dice il falso.
        if self.created_agency_ids and not self.dedicated_cleanup_done:
            self.report.fail(
                "CLEAN-VERIFICA",
                "verifica invocata PRIMA di cleanup_dedicated_agencies: "
                + ", ".join(self.DEDICATED_EFFECT_TABLES)
                + " spariscono con le agenzie dedicate, e contarli adesso "
                "segnalerebbe come residuo cio' che il passo successivo "
                "rimuove. Il difetto e' nell'ordine delle chiamate, non sul TEST.",
            )
            return
        # UNA CONSEGUENZA NON E' UN SECONDO GUASTO.
        #
        # Se il cleanup e' stato BLOCCATO - dipendenze estranee, bucket non
        # ripulito, istantanea incompleta - le righe sono ancora la' PER
        # DECISIONE, non per un difetto della cancellazione. Elencarle come
        # "righe ANCORA PRESENTI" accanto agli altri FAIL produce dieci
        # fallimenti dove il problema e' uno, e sposta la diagnosi sul
        # sintomo: e' successo nel run 38e341f68f8a, dove sette FAIL su dieci
        # erano la stessa riga di preflight ripetuta.
        motivo = self._destructive_db_blocked()
        if motivo:
            self.report.fail(
                "CLEAN-VERIFICA",
                f"verifica non conclusiva: il cleanup era bloccato ({motivo}) "
                "e le righe del run sono ancora sul TEST PER DECISIONE, non "
                "per una cancellazione fallita. Il guasto da correggere e' "
                "quello segnalato sopra: questa riga ne e' la conseguenza.",
            )
            return
        if self._created_nothing():
            self.report.note("CLEAN-VERIFICA",
                             "il run non ha creato nulla: niente da verificare")
            return
        figlie = []
        try:
            with self.db.read() as cur:
                residui = []
                for table, entries in self.created_rows.items():
                    ids = tuple(i for i, _m, _c in entries)
                    cur.execute(
                        f"SELECT COUNT(*) AS n FROM {table} WHERE id IN %s", (ids,))
                    left = int(cur.fetchone()["n"])
                    if left:
                        residui.append(f"{table}={left}")

                # GLI EFFETTI DELLE API, NON SOLO LE RIGHE CHIESTE.
                #
                # Archiviare un immobile scrive in `property_status_history`,
                # archiviare una richiesta in `buy_request_history`, calcolare
                # un match scrive `match_runs` e i `match_requirement_results`.
                # Il run 22d007af7916 ne ha lasciati 32 sul TEST perche' il
                # genitore non era stato cancellato ma solo archiviato - e
                # nessuno li contava.
                #
                # Ciascuno con la sua azione DICHIARATA, perche' non tutte
                # sono CASCADE: per un SET NULL questo conteggio non dimostra
                # la rimozione della riga - se il genitore e' sparito la
                # colonna e' NULL e la riga non risponde piu' al suo id. Puo'
                # solo dimostrare il contrario, cioe' che il genitore c'e'
                # ancora, e il report lo dice con queste parole.
                genitori_id = {
                    "properties": tuple(i for i, _m, _c in
                                        self.created_rows.get("properties", [])) or (0,),
                    "buy_requests": tuple(i for i, _m, _c in
                                          self.created_rows.get("buy_requests", [])) or (0,),
                    # I genitori del percorso documentale. `(0,)` quando il run
                    # non ne ha creati: la query resta legale e conta zero,
                    # invece di sollevare e trasformare l'intera verifica in
                    # "non eseguibile" - che e' esattamente cosa succedeva
                    # quando questa mappa non conosceva un genitore dichiarato.
                    "owner_accounts": tuple(self.created_owner_account_ids) or (0,),
                    "owner_shared_documents": tuple(
                        self.created_effects.get("owner_shared_documents", ())) or (0,),
                }
                for fk in self.EFFECT_FOREIGN_KEYS:
                    tabella, colonna = fk.table, fk.column
                    if fk.parent not in genitori_id:
                        # Un genitore dichiarato che nessuno sa enumerare non
                        # si salta in silenzio: il report dice che quella
                        # relazione non e' stata verificata.
                        figlie.append(
                            f"{tabella}.{colonna} ({fk.on_delete}): genitore "
                            f"{fk.parent} non enumerabile, relazione non verificata")
                        continue
                    valori = genitori_id[fk.parent]
                    cur.execute(
                        f"SELECT COUNT(*) AS n FROM {tabella} WHERE {colonna} IN %s",
                        (valori,))
                    left = int(cur.fetchone()["n"])
                    if left:
                        residui.append(
                            f"{tabella}.{colonna}={left}"
                            + (" (SET NULL: il genitore e' ancora presente)"
                               if fk.on_delete == "SET NULL" else ""))
                    elif fk.on_delete == "SET NULL":
                        # Zero qui non e' "rimossa": e' "non piu' raggiungibile
                        # per id del genitore", perche' la colonna e' NULL. La
                        # prova sta nell'istantanea degli id, poco piu' sotto;
                        # questa riga dice soltanto quale delle due domande e'
                        # stata fatta.
                        figlie.append(
                            f"{tabella}.{colonna} (SET NULL): 0 per id del genitore, "
                            "che non prova la rimozione della riga"
                            + (f"; verificata per id ({len(self.effect_rows_before.get(tabella, ()))} "
                               "righe fotografate prima)"
                               if self.effect_snapshot_done
                               else "; NESSUNA istantanea degli id: non verificabile"))
                for table, ids in self.created_effects.items():
                    if not ids:
                        continue
                    cur.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE id IN %s",
                                (tuple(ids),))
                    left = int(cur.fetchone()["n"])
                    if left:
                        residui.append(f"{table}={left}")
                # LE RIGHE DEGLI EFFETTI, PER ID.
                #
                # E' l'unica domanda che un SET NULL non puo' svuotare: la
                # colonna verso il genitore sara' anche NULL, ma l'id della
                # riga e' rimasto quello di prima. Senza questa istantanea la
                # verifica finale diceva "0 presenti" mentre CLEAN-FIGLIE
                # ammetteva che quella relazione non era verificabile - due
                # affermazioni che non possono stare insieme.
                if self.created_rows or self.created_effects:
                    if not self.effect_snapshot_done:
                        residui.append(
                            "effetti non verificabili per id (nessuna istantanea: "
                            "vedi CLEAN-EFFETTI)")
                    for tabella, ids in self.effect_rows_before.items():
                        cur.execute(
                            f"SELECT COUNT(*) AS n FROM {tabella} WHERE id IN %s",
                            (ids,))
                        left = int(cur.fetchone()["n"])
                        if left:
                            residui.append(
                                f"{tabella}={left} di {len(ids)} righe fotografate "
                                "prima del cleanup (verificate per id)")

                # LE FIGLIE RICONOSCIUTE, PER ID.
                #
                # Il giro su CHILD_FOREIGN_KEYS piu' sotto le cerca per
                # `watch_id`, e dopo il cleanup quel watch non esiste piu':
                # risponderebbe 0 sia che siano state cancellate sia che siano
                # rimaste con il genitore. L'id invece non lo tocca nessun ON
                # DELETE, ed e' l'unica domanda la cui risposta significhi
                # qualcosa.
                if self.dedicated_cleanup_done:
                    # `nostre` e non `figlie`: quel nome e' gia' la lista dei
                    # messaggi su CHILD_FOREIGN_KEYS, poche righe piu' sotto.
                    for tabella, nostre in sorted(self.owned_child_ids.items()):
                        cur.execute(
                            f"SELECT COUNT(*) AS n FROM {tabella} WHERE id IN %s",
                            (nostre,))
                        left = int(cur.fetchone()["n"])
                        if left:
                            residui.append(
                                f"{self._etichetta_figlia(tabella)}={left} di "
                                f"{len(nostre)} righe create dal run "
                                "(verificate per id, non per genitore)")

                # GLI ID TRACCIATI FUORI DA `created_rows`.
                #
                # Vendite, proposte, match e conti proprietario li cancellano
                # `cleanup_chain_fixtures` e `cleanup_owner_fixtures`, che
                # verificano il proprio lavoro. Questa e' l'ultima parola e non
                # deve fidarsi di loro: se una di quelle verifiche fosse
                # sbagliata, nessun altro se ne accorgerebbe.
                for attributo, tabella in self.TRACKED_ID_TABLES:
                    ids = getattr(self, attributo)
                    if not ids:
                        continue
                    cur.execute(f"SELECT COUNT(*) AS n FROM {tabella} WHERE id IN %s",
                                (tuple(ids),))
                    left = int(cur.fetchone()["n"])
                    if left:
                        residui.append(f"{tabella}={left}")
                if self.created_agency_ids:
                    # LE FIGLIE DEI GENITORI CANCELLATI, per gli id presi
                    # PRIMA. Nessuna JOIN: il genitore non c'e' piu', e
                    # chiedere di lui risponderebbe 0 comunque sia andata.
                    if not self.child_parents:
                        residui.append(
                            "figlie non verificabili (nessuna istantanea dei "
                            "genitori: vedi CLEAN-FIGLIE)")
                    for fk in self.CHILD_FOREIGN_KEYS:
                        ids = self.child_parents.get(fk.parent)
                        prima = self.children_before.get(fk.table, 0)
                        if fk.on_delete not in ("CASCADE", "RESTRICT"):
                            # Nessuna azione oltre queste due e' verificabile
                            # qui: dirlo e' meglio che contare e tacere.
                            figlie.append(f"{fk.table} ({fk.on_delete}): non "
                                          "verificabile per id del genitore")
                            continue
                        if not ids:
                            figlie.append(
                                f"{fk.table}: nessun {fk.parent} del run, niente da "
                                "verificare")
                            continue
                        cur.execute(
                            f"SELECT COUNT(*) AS n FROM {fk.table} WHERE {fk.column} IN %s",
                            (ids,))
                        left = int(cur.fetchone()["n"])
                        if left:
                            residui.append(
                                f"{fk.table}={left}"
                                + (f" (RESTRICT: {fk.parent} non e' stata cancellata)"
                                   if fk.on_delete == "RESTRICT"
                                   else f" (CASCADE non avvenuto da {fk.parent})"))
                        figlie.append(
                            f"{fk.table} ({fk.on_delete}): {prima} prima, {left} dopo")
                if self.created_match_ids:
                    cur.execute(
                        "SELECT COUNT(*) AS n FROM match_requirement_results r "
                        " JOIN match_runs mr ON mr.id = r.match_run_id "
                        " WHERE mr.property_id IN %s OR mr.buy_request_id IN %s",
                        (genitori_id["properties"], genitori_id["buy_requests"]))
                    left = int(cur.fetchone()["n"])
                    if left:
                        residui.append(f"match_requirement_results={left}")
        except Exception as exc:
            self.report.fail("CLEAN-VERIFICA",
                             f"verifica dei residui non eseguibile ({type(exc).__name__}): "
                             "non si puo' affermare che il TEST sia pulito")
            return
        if figlie:
            # Il "prima" e' cio' che distingue una verifica da una formalita':
            # "0 prima, 0 dopo" non prova che la rimozione funzioni, e il
            # report deve permettere di vederlo invece di dire "pulito".
            # Ogni riga porta l'azione dichiarata, perche' "0 dopo" significa
            # cose diverse per un CASCADE, un RESTRICT e un SET NULL.
            self.report.note("CLEAN-FIGLIE", "; ".join(figlie))
        if residui:
            self.report.fail(
                "CLEAN-VERIFICA",
                f"righe di questo run ANCORA PRESENTI: {', '.join(residui)}. "
                "Un 2xx sulla DELETE non e' una prova di cancellazione.",
            )
        else:
            # Il conteggio nomina OGNI categoria: "12 righe create" quando il
            # run aveva creato anche match, conti e documenti diceva meno del
            # vero proprio nel punto in cui il report afferma di piu'.
            dettaglio = ", ".join(f"{nome}={n}" for nome, n in self._tracked_totals().items())
            self.report.note("CLEAN-VERIFICA",
                             f"creati {dettaglio}; 0 presenti: verificato sul database")

    def cleanup_database(self) -> None:
        """Cancella identita' e sessioni di questo run, e lo dimostra.

        Il criterio e' `created_user_ids` e nient'altro. Cancellare per
        prefisso sarebbe piu' comodo - ripulirebbe anche i resti di ieri - e
        sarebbe sbagliato: due certificazioni avviate insieme condividono il
        prefisso, e la seconda a finire porterebbe via le identita' della prima
        mentre le sta ancora usando.
        """
        if not self.created_user_ids:
            self.report.note("CLEAN-DB", "nessuna identita' creata: niente da rimuovere")
            return

        ids = tuple(self.created_user_ids)
        try:
            with self.db.write() as cur:
                cur.execute(
                    "DELETE FROM operator_sessions WHERE operator_user_id IN %s", (ids,))
                sessions = cur.rowcount
                cur.execute(
                    "DELETE FROM agency_memberships WHERE operator_user_id IN %s", (ids,))
                memberships = cur.rowcount
                cur.execute("DELETE FROM operator_users WHERE id IN %s", (ids,))
                users = cur.rowcount
                self.report.note(
                    "CLEAN-DB",
                    f"run {self.run_id}: rimossi {sessions} sessioni, "
                    f"{memberships} membership, {users} identita'",
                )
            with self.db.read() as cur:
                final = self._residue(cur, ids)
        except Exception as exc:
            self.report.fail(
                "CLEAN-DB",
                f"cleanup del database fallito ({type(exc).__name__}): le "
                f"{len(ids)} righe del run {self.run_id} sono POTENZIALMENTE PRESENTI",
            )
            return

        if sum(final.values()) == 0:
            self.report.note(
                "CLEAN-CHK",
                f"verifica sugli id del run {self.run_id}: 0 identita', "
                "0 membership, 0 sessioni",
            )
        else:
            self.report.fail(
                "CLEAN-CHK",
                f"cleanup INCOMPLETO: restano {final['users']} identita', "
                f"{final['memberships']} membership, {final['sessions']} sessioni",
            )


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------

def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True,
                              timeout=30).stdout.strip()
    except Exception:
        return ""


def preflight(report: Report, database: Database, env: dict, approved_commit: str) -> str:
    """Database, commit, branch e app. Ritorna la base URL.

    Il database viene per primo, prima ancora del commit: e' la guardia che
    impedisce di scrivere nel posto sbagliato, e una guardia preceduta da un
    altro controllo e' una guardia che quel controllo puo' mascherare.
    """
    assert_certification_database(env.get("DB_NAME"))
    report.note("0.1", f"DB_NAME = {REQUIRED_DB_NAME}")
    connected = database.current_database()
    report.check("0.2", connected == REQUIRED_DB_NAME,
                 f"la connessione reale e' su {connected!r}, non solo DB_NAME")

    head = _git("rev-parse", "HEAD")
    report.check("0.3", head == approved_commit,
                 f"git rev-parse HEAD = {head or '(assente)'} atteso {approved_commit}")

    render_commit = env.get("RENDER_GIT_COMMIT", "")
    if render_commit:
        report.check("0.4", render_commit == head,
                     f"RENDER_GIT_COMMIT = {render_commit} coincide con HEAD")
    else:
        report.blocked("0.4", "RENDER_GIT_COMMIT assente: incrocio non eseguibile")

    branch = env.get("RENDER_GIT_BRANCH") or _git("branch", "--show-current")
    report.check("0.5", branch == APPROVED_BRANCH,
                 f"branch = {branch or '(assente)'} atteso {APPROVED_BRANCH}")

    base = (env.get("TEST_BASE_URL") or env.get("RENDER_EXTERNAL_URL") or "").rstrip("/")
    report.check("0.6", bool(base), "URL dell'app disponibile (RENDER_EXTERNAL_URL)")
    scheme = urllib.parse.urlparse(base).scheme
    # Il cookie e' Secure: un cookie jar corretto non lo invia su http://, e
    # ogni prova di isolamento leggerebbe 401 come "isolamento funzionante".
    report.check("0.7", scheme == "https",
                 f"la base e' https ({scheme or 'nessuno'}): il cookie e' Secure")
    return base


def read_agencies(report: Report, database: Database) -> tuple[dict, dict]:
    """Le due agenzie TEST, lette e mai modificate."""
    with database.read() as cur:
        cur.execute(
            "SELECT id, slug, name, status FROM agencies WHERE slug IN (%s, %s)",
            (DEFAULT_AGENCY_SLUG, AGENCY_B_SLUG),
        )
        found = {row["slug"]: dict(row) for row in cur.fetchall()}

    for slug in (DEFAULT_AGENCY_SLUG, AGENCY_B_SLUG):
        row = found.get(slug)
        report.check(f"0.8-{slug}", bool(row) and row["status"] == "active",
                     f"agenzia {slug!r} presente e attiva")
    first, second = found[DEFAULT_AGENCY_SLUG], found[AGENCY_B_SLUG]
    report.check("0.9", first["id"] != second["id"], "le due agenzie sono distinte")
    return first, second


def incoherence_census(report: Report, database: Database) -> None:
    """Censimento READ-ONLY delle incoerenze gia' presenti nei dati.

    Non ripara nulla e non cancella nulla: conta. Una riga con due radici di
    agenzia discordi non e' un difetto del codice attuale - il codice attuale
    non la scriverebbe - ma un residuo di prima che le regole esistessero, ed e'
    esattamente il tipo di cosa che una matrice ostile puo' incontrare e che va
    classificata prima di dichiarare chiuso il gate.
    """
    queries = (
        ("grant OWNER con radici discordi", """
            SELECT COUNT(*) AS n
              FROM owner_property_access x
              JOIN owner_accounts oa ON oa.id = x.owner_account_id
              JOIN contacts ct ON ct.id = oa.contact_id
              JOIN properties p ON p.id = x.property_id
             WHERE ct.agency_id <> p.agency_id
        """),
        ("lead con agenzia diversa dal contatto", """
            SELECT COUNT(*) AS n
              FROM leads l JOIN contacts c ON c.id = l.contact_id
             WHERE l.agency_id <> c.agency_id
        """),
    )
    total = 0
    for label, sql in queries:
        try:
            with database.read() as cur:
                cur.execute(sql)
                count = int(cur.fetchone()["n"])
        except Exception as exc:
            report.blocked("CENSUS", f"{label}: non interrogabile ({type(exc).__name__})")
            continue
        total += count
        if count:
            report.fail("CENSUS", f"{label}: {count} righe incoerenti, non classificate")
        else:
            report.note("CENSUS", f"{label}: 0")
    total += _audit_without_roots(report, database)
    if total == 0:
        report.note("CENSUS", "nessuna incoerenza rilevata dal censimento")


def _audit_without_roots(report: Report, database: Database) -> int:
    """Gli audit senza radice: CLASSIFICATI, non solo contati.

    Entrambe le colonne NULL significa ORIGINE NON ATTRIBUITA, non "SET NULL
    gia' avvenuto": dalla riga da sola le due ipotesi non si distinguono, e
    chiamarle "incoerenti" attribuiva una causa che nessuno ha osservato.

    Cio' che la riga dice davvero e' `entity_type` ed `entity_id`: da li' si
    capisce di che cosa parlava e se sia collocabile per altra via. Il
    censimento le raggruppa per quello.

    Resta un FAIL. Una riga che nessuno sa attribuire a un tenant e' un
    ostacolo alla chiusura del gate, e classificarla non la risolve: la rende
    esaminabile. Non viene cancellata - non e' un residuo di questo run, e
    cancellare cio' che non si sa attribuire e' il danno peggiore fra i due.
    """
    try:
        with database.read() as cur:
            cur.execute(
                """
                SELECT COALESCE(entity_type, '(nessuno)') AS tipo,
                       COUNT(*) AS n,
                       COUNT(entity_id) AS con_entita
                  FROM owner_audit_log
                 WHERE owner_account_id IS NULL AND property_id IS NULL
                 GROUP BY 1
                 ORDER BY 2 DESC, 1
                """)
            righe = [dict(r) for r in cur.fetchall()]
    except Exception as exc:
        report.blocked("CENSUS", "audit OWNER senza radice: non interrogabile "
                                 f"({type(exc).__name__})")
        return 0

    totale = sum(int(r["n"]) for r in righe)
    if totale == 0:
        report.note("CENSUS", "audit OWNER senza radice: 0")
        return 0
    dettaglio = ", ".join(f"{r['tipo']}={r['n']} (con entity_id: {r['con_entita']})"
                          for r in righe)
    report.fail(
        "CENSUS",
        f"audit OWNER senza radice: {totale} righe con owner_account_id e "
        f"property_id entrambi NULL, classificate per entita': {dettaglio}. "
        "ORIGINE NON ATTRIBUITA, non SET NULL gia' avvenuto: dalla riga sola "
        "le due ipotesi non si distinguono. Non vengono cancellate; restano "
        "da collocare prima di chiudere il gate.",
    )
    return totale


# ---------------------------------------------------------------------------
# La matrice
# ---------------------------------------------------------------------------

def _fill(template: str, **values) -> str:
    for key, value in values.items():
        template = template.replace("{" + key + "}", str(value))
    return template


def build_shared_watch_fixtures(report, http, cert, jars, agencies) -> dict:
    """Una stima OSSERVABILE per ciascuna agenzia condivisa. {label: id}

    CHIUDE TRE BLOCKED DEL RUN 42e32975ccd6, e nessuno per caso.

    * `PROPERTY_WATCH-propria-B` e `ostile-A-B`: B non possiede alcuna stima su
      TEST. E UNA STIMA DA SOLA NON BASTEREBBE:
      `GET /api/property-watch/stime/{id}` chiama
      `get_watch_for_stima_scoped`, che solleva `WatchNotFoundError` - cioe'
      404 - quando il watch non esiste. Servono quindi tre cose, nell'ordine:
      la stima, la valutazione completata che `_baseline_for_stima_scoped`
      pretende (`seller_timeline_events` con `stima_completata` e il payload
      dei prezzi), e `POST .../initialize` che crea watch e osservazione.
      E' la stessa sequenza delle agenzie dedicate, che infatti passa.

    * `LEGACY_ADMIN-list-A/B-non-vede-*`: `/api/admin/stime?day=oggi` filtra
      `s.agency_id = <chiamante>` e `s.data >= oggi`. La stima nasce con
      `data DEFAULT CURRENT_TIMESTAMP` e porta il marcatore in `comune`, che
      la proiezione della route restituisce: da liste vuote - su cui il
      confronto col marcatore altrui non prova niente - si passa a liste che
      contengono la propria riga e non quella dell'altro.

    TRACCIAMENTO E CANCELLAZIONE NASCONO QUI, NON DOPO. Ogni id finisce in
    `created_effects` (perimetro, preflight, verifica) e negli elenchi
    `shared_*` che `cleanup_shared_watch_fixtures` cancella per id - le
    agenzie condivise non hanno una cancellazione per `agency_id`, e non
    devono averla. L'osservazione che `initialize` scrive viene riconosciuta
    da `_snapshot_owned_children` per chiave derivata.
    """
    creati = {}
    for label, agency in sorted(agencies.items()):
        try:
            with cert.db.write() as cur:
                cur.execute(
                    "INSERT INTO stime (comune, via, tipologia, mq, prezzo_mq_base, "
                    "                   agency_id) "
                    "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                    (cert.marker(label), cert.marker(label), "appartamento",
                     80, 2000, agency["id"]))
                stima = int(cur.fetchone()["id"])
            cert.created_effects.setdefault("stime", []).append(stima)
            cert.shared_stima_ids.append(stima)

            with cert.db.write() as cur:
                cur.execute(
                    "INSERT INTO seller_timeline_events "
                    "  (stima_id, event_type, event_source, payload, agency_id) "
                    "VALUES (%s, %s, %s, %s::jsonb, %s) RETURNING id",
                    (stima, cert.STIMA_COMPLETATA_EVENT, "p26-6-cert",
                     json.dumps({"price_exact": 160000, "eur_mq_finale": 2000,
                                 "base_mq": 2000}),
                     agency["id"]))
                evento = int(cur.fetchone()["id"])
            cert.created_effects.setdefault(
                "seller_timeline_events", []).append(evento)
            cert.shared_event_ids.append(evento)
        except Exception as exc:
            report.blocked(f"PROPERTY_WATCH-fixture-{label}",
                           f"stima condivisa non creabile ({type(exc).__name__})")
            continue

        risposta = http.request("POST",
                                f"/api/property-watch/stime/{stima}/initialize",
                                jar=jars[label])
        if risposta.status not in (200, 201):
            report.blocked(
                f"PROPERTY_WATCH-fixture-{label}",
                f"initialize -> {risposta.status}{_corpo(risposta)}: senza watch "
                "la lettura propria risponderebbe 404 e il confronto sarebbe vuoto")
            continue

        # L'ID DEL WATCH SI LEGGE DAL DATABASE, non dalla risposta: e' quello
        # su cui si cancella, e un campo della risposta e' un'affermazione del
        # servizio su se stesso. Stessa regola di `register_uploaded_document`.
        try:
            with cert.db.read() as cur:
                cur.execute(
                    "SELECT id FROM property_watches "
                    " WHERE stima_id = %s AND agency_id = %s",
                    (stima, agency["id"]))
                riga = cur.fetchone()
                watch = int(riga["id"]) if riga else None
                if watch is not None:
                    cur.execute(
                        "SELECT id FROM property_watch_observations WHERE watch_id = %s",
                        (watch,))
                    osservazioni = [int(r["id"]) for r in cur.fetchall()]
                else:
                    osservazioni = []
        except Exception as exc:
            report.fail(
                f"PROPERTY_WATCH-fixture-{label}",
                f"watch creato ma non rileggibile ({type(exc).__name__}): la riga "
                "esiste e non sarebbe piu' cancellabile per id")
            continue
        if watch is None:
            report.fail(f"PROPERTY_WATCH-fixture-{label}",
                        "initialize ha risposto 2xx ma nessun watch risulta sulla "
                        "stima: nulla da osservare e nulla da cancellare")
            continue

        cert.created_effects.setdefault("property_watches", []).append(watch)
        cert.shared_observation_ids.extend(osservazioni)
        creati[label] = stima
        report.note(f"PROPERTY_WATCH-fixture-{label}",
                    f"stima {stima} con valutazione e watch {watch} "
                    f"nell'agenzia di {label} ({len(osservazioni)} osservazioni)")
    return creati


def derive_stime(database: Database, agencies: dict) -> dict:
    """Una stima per agenzia, LETTA e mai creata.

    `stime.agency_id` e' fisico da 031, quindi la domanda "quale stima
    appartiene a chi" ha una risposta diretta. Serve perche' il funnel pubblico
    risolve l'agenzia lato server: da questa API non nasce una stima per
    l'agenzia B, e l'unico modo onesto di provare
    `/api/property-watch/stime/{id}` e' su cio' che esiste gia'.

    Sola lettura, deliberatamente: scrivere su una stima preesistente
    significherebbe che la certificazione ha modificato dati di produzione di
    TEST, ed e' proprio cio' che questo script promette di non fare.
    """
    found = {}
    with database.read() as cur:
        for label, agency in agencies.items():
            cur.execute(
                "SELECT id FROM stime WHERE agency_id = %s ORDER BY id DESC LIMIT 1",
                (agency["id"],),
            )
            row = cur.fetchone()
            if row:
                found[label] = int(row["id"])
    return found


def certify_property_watch(report, http, cert, domain, jars, owned, context) -> None:
    """L'isolamento delle stime, su stime che esistono davvero.

    LA COSTRUZIONE, E PERCHE' E' FATTA COSI'

    Un 404 su una stima altrui, da solo, non prova nulla: potrebbe voler dire
    "non e' tua" oppure "non esiste" oppure "non ha un watch". Le tre cose
    hanno lo stesso codice, ed e' giusto che l'abbiano - un 404 che
    distinguesse sarebbe esso stesso una fuga.

    Cio' che rende la prova decisiva e' il confronto: LO STESSO id risponde 200
    al suo proprietario e 403/404 all'altro. Percio' la prova ostile viene
    eseguita solo quando il proprietario ha appena dimostrato di poter leggere
    quella riga. Altrimenti: BLOCKED, con scritto perche'.
    """
    stime = context.get("stime", {})
    readable = {}

    for label in ("A", "B"):
        stima = stime.get(label)
        if stima is None:
            report.blocked(
                f"PROPERTY_WATCH-propria-{label}",
                f"nessuna stima appartiene all'agenzia {label} su questo TEST: "
                "senza una riga esistente non c'e' isolamento da osservare",
            )
            continue
        response = http.request("GET", _fill(domain.detail, id=stima), jar=jars[label])
        if response.status == 200:
            readable[label] = stima
            report.note(f"PROPERTY_WATCH-propria-{label}",
                        f"{label} legge il watch della propria stima {stima} -> 200")
        else:
            report.blocked(
                f"PROPERTY_WATCH-propria-{label}",
                f"la stima {stima} di {label} risponde {response.status}: "
                "probabilmente non ha un watch inizializzato. La prova ostile "
                "su questo id sarebbe ambigua e non viene eseguita",
            )

    for label, other in (("A", "B"), ("B", "A")):
        target = readable.get(other)
        if target is None:
            report.blocked(
                f"PROPERTY_WATCH-ostile-{label}-{other}",
                f"nessuna stima di {other} risulta leggibile dal suo proprietario: "
                "un rifiuto verso di essa non distinguerebbe l'isolamento "
                "dall'assenza del dato",
            )
            continue
        # Lettura ostile su un id che il suo proprietario legge davvero.
        response = http.request("GET", _fill(domain.detail, id=target), jar=jars[label])
        report.check(
            f"PROPERTY_WATCH-ostile-{label}-{other}",
            response.status in NEUTRAL_REFUSALS,
            f"{label} chiede il watch della stima {target} di {other} -> "
            f"{response.status}, mentre {other} sulla stessa riga ottiene 200",
        )
        # Scrittura ostile. `initialize` e' idempotente per il proprietario:
        # se rispondesse 200 a un estraneo avrebbe creato o toccato un watch
        # sulla stima di un'altra agenzia, che e' il danno peggiore di tutti.
        response = http.request(
            "POST", _fill(domain.detail, id=target) + "/initialize", jar=jars[label])
        report.check(
            f"PROPERTY_WATCH-ostile-write-{label}-{other}",
            response.status in NEUTRAL_REFUSALS,
            f"{label} tenta di inizializzare il watch della stima {target} di "
            f"{other} -> {response.status}",
        )


def build_owner_fixtures(report, http, cert, owner_jars, owned, context, jars=None) -> None:
    """Un proprietario per agenzia, con un immobile concesso e una sessione.

    LA CATENA, TUTTA ATTRAVERSO L'API DI OWNER ADMIN

        POST /accounts            contatto proprio      -> conto proprietario
        POST /access              conto + immobile propri -> concessione
        POST /accounts/{id}/tokens                      -> token monouso
        POST /portal/auth/token   token                 -> cookie del portale

    Nessun passo tocca dati preesistenti: il contatto e l'immobile sono le
    fixture che questo run ha gia' creato, e il conto nasce sul primo.

    Il token e' una credenziale a tutti gli effetti: entra nei segreti che lo
    scanner di fughe cerca in ogni risposta, e non viene mai stampato.
    """
    for label in ("A", "B"):
        if label not in owner_jars:
            continue
        contact = owned[label].get("CORE")
        prop = owned[label].get("PROPERTY")
        if contact is None or prop is None:
            report.blocked(f"owner-fixture-{label}",
                           "mancano il contatto o l'immobile di questo run: il "
                           "proprietario non e' costruibile")
            continue

        response = http.request("POST", OWNER_ACCOUNTS, jar=owner_jars[label],
                                payload={"contact_id": contact})
        account = (response.json() or {}).get("id")
        if response.status not in (200, 201) or account is None:
            report.blocked(f"owner-fixture-{label}",
                           f"POST {OWNER_ACCOUNTS} -> {response.status}: nessun "
                           "conto proprietario per questa agenzia")
            continue
        context["owner_accounts"][label] = account
        cert.created_owner_account_ids.append(int(account))

        response = http.request("POST", OWNER_ACCESS, jar=owner_jars[label], payload={
            "owner_account_id": account, "property_id": prop, "is_primary": True,
        })
        grant = (response.json() or {}).get("id")
        if response.status not in (200, 201):
            report.blocked(f"owner-fixture-{label}",
                           f"POST {OWNER_ACCESS} -> {response.status}: nessun "
                           "immobile concesso, il portale non avrebbe cosa mostrare")
            continue
        context["owner_grants"][label] = grant
        context["portal_properties"][label] = prop

        response = http.request("POST", f"{OWNER_ACCOUNTS}/{account}/tokens",
                                jar=owner_jars[label],
                                payload={"token_type": "login", "expires_minutes": 30})
        token = (response.json() or {}).get("token")
        if response.status not in (200, 201) or not token:
            report.blocked(f"owner-fixture-{label}",
                           f"emissione del token -> {response.status}: il portale "
                           "non e' raggiungibile per questa agenzia")
            continue
        cert.secrets.append(token)
        # Questa sola risposta ha il diritto di contenerlo: e' il contratto di
        # `one_time_display`. Registrata come tale, cosi' che lo scanner
        # continui a cercare quel token in ogni ALTRA risposta.
        context["disclosures"].append(
            (f"POST {OWNER_ACCOUNTS}/{account}/tokens", token))

        jar = http.new_jar()
        response = http.request("POST", PORTAL_LOGIN, jar=jar, payload={"token": token})
        if response.status not in (200, 204):
            report.blocked(f"owner-fixture-{label}",
                           f"POST {PORTAL_LOGIN} -> {response.status}: sessione "
                           "del portale non aperta")
            continue
        session_token = next(
            (c.value for c in jar if c.name == OWNER_COOKIE_NAME and c.value), None)
        if session_token:
            cert.secrets.append(session_token)
        context["portal_jars"][label] = jar
        report.note(f"owner-fixture-{label}",
                    f"proprietario di {label}: conto {account}, immobile {prop} "
                    "concesso, sessione del portale aperta")

        # IL DOCUMENTO DEL PORTALE NASCE DA UN CARICAMENTO, NON DA UN URL.
        #
        # IL 422 DEI RUN 52f6d97b5214 E 58aa0e189aaa, finalmente attribuito.
        #
        # La fixture precedente faceva tre passi: creava il documento
        # dell'immobile con un `url`, lo condivideva, lo pubblicava. Il secondo
        # passo rispondeva 422 e la diagnosi precedente - "manca
        # public_document_type" - era sbagliata: quel campo c'era, e infatti
        # correggerlo non ha cambiato nulla nel run successivo.
        #
        # La causa e' una REGOLA DI DOMINIO, non lo schema. In
        # `owner/repository.create_shared_document`:
        #
        #     if src["status"] != "available" or not src.get("storage_key"):
        #         raise ValidationError("Il documento deve essere disponibile
        #                                in storage privato")
        #
        # e il wrapper `x()` di `owner/router_admin.py` traduce ValidationError
        # in 422. Un documento nato da un `url` ha `storage_key` NULL -
        # `property/schemas.DocumentCreate` accetta l'uno O l'altro - quindi
        # non e' condivisibile, per progetto: il portale serve file che il
        # sistema custodisce, non link che qualcun altro puo' cambiare.
        #
        # Il backend ha ragione. La fixture no. Quindi il documento del portale
        # e' quello CARICATO, che nasce con la sua chiave, e il caricamento si
        # fa per primo perche' ora e' lui a reggere sia l'elenco sia lo
        # scaricamento.
        contenuto = ("%PDF-1.4 " + cert.marker(label)).encode("utf-8")
        risposta = http.upload(
            "/api/owner/admin/documents/upload", jar=owner_jars[label],
            campi={"property_id": prop,
                   "document_type": cert.PROPERTY_DOCUMENT_TYPE,
                   "source_title": cert.marker(label) + "-file",
                   "public_title": cert.marker(label) + "-file",
                   "public_document_type": cert.OWNER_PUBLIC_DOCUMENT_TYPE},
            nome_file=cert.marker(label) + ".pdf", contenuto=contenuto)
        caricato = (risposta.json() or {}).get("id")
        if risposta.status not in (200, 201) or caricato is None:
            report.blocked(
                f"owner-fixture-{label}-documento",
                f"caricamento -> {risposta.status}{_corpo(risposta)}. Se lo "
                "storage documenti non e' configurato su questo TEST "
                "(OWNER_DOCUMENT_STORAGE_ENABLED) il portale non avra' nulla "
                "da elencare ne' da scaricare",
            )
            continue
        cert.created_effects.setdefault("owner_shared_documents", []).append(
            int(caricato))
        # `property_document_id` arriva nella risposta; `storage_key` no -
        # `_admin_shared_document` lo esclude di proposito. L'id su cui si
        # cancella viene SEMPRE dal database: quello della risposta si passa
        # solo perche' venga confrontato.
        cert.register_uploaded_document(
            int(caricato), (risposta.json() or {}).get("property_document_id"))
        risposta = http.request(
            "POST", f"/api/owner/admin/documents/{caricato}/publish",
            jar=owner_jars[label])
        if risposta.status not in (200, 201):
            report.blocked(f"owner-fixture-{label}-documento",
                           f"pubblicazione -> {risposta.status}{_corpo(risposta)}")
            continue
        context["portal_documents"][label] = int(caricato)
        context["portal_downloadable"][label] = int(caricato)
        report.note(f"owner-fixture-{label}-documento",
                    f"documento {caricato} caricato e pubblicato per {label}: "
                    "elencabile e scaricabile")

        # E IL DOCUMENTO DA URL RESTA, MA COME PROVA DEL RIFIUTO.
        #
        # Non si butta via: e' una riga vera di `property_documents` che il
        # cleanup deve saper rimuovere, ed e' l'occasione per certificare la
        # regola invece di subirla. La richiesta si fa lo stesso, e il 422 -
        # con il suo motivo - diventa un PASS. Se un domani la condivisione di
        # un documento senza `storage_key` venisse ammessa, questa riga
        # diventerebbe rossa e qualcuno dovrebbe decidere, che e' esattamente
        # cio' che un BLOCKED permanente non faceva succedere.
        risposta = http.request(
            "POST", f"/api/property/properties/{prop}/documents",
            jar=jars[label],
            payload={"document_type": cert.PROPERTY_DOCUMENT_TYPE,
                     "title": cert.marker(label),
                     "url": "https://certification.invalid/" + cert.marker(label),
                     "status": "available"})
        doc = (risposta.json() or {}).get("id")
        if risposta.status not in (200, 201) or doc is None:
            report.blocked(f"owner-fixture-{label}-url-non-condivisibile",
                           f"documento dell'immobile -> {risposta.status}"
                           f"{_corpo(risposta)}")
            continue
        cert.created_effects.setdefault("property_documents", []).append(int(doc))
        risposta = http.request("POST", "/api/owner/admin/documents", jar=owner_jars[label],
                                payload={"property_document_id": doc,
                                         "public_title": cert.marker(label),
                                         "public_document_type":
                                             cert.OWNER_PUBLIC_DOCUMENT_TYPE})
        condiviso = (risposta.json() or {}).get("id")
        if risposta.status == 422 and condiviso is None:
            report.note(f"owner-fixture-{label}-url-non-condivisibile",
                        "un documento senza storage_key non e' condivisibile: "
                        f"422 come da regola{_corpo(risposta)}")
        elif condiviso is not None:
            # Condiviso davvero: la riga esiste e il cleanup deve saperlo,
            # qualunque cosa se ne pensi della regola.
            cert.created_effects.setdefault("owner_shared_documents", []).append(
                int(condiviso))
            report.fail(
                f"owner-fixture-{label}-url-non-condivisibile",
                f"la condivisione di un documento senza storage_key e' stata "
                f"ACCETTATA (id {condiviso}): "
                "`create_shared_document` dichiara di rifiutarla, e il portale "
                "finirebbe per esporre un link che il sistema non custodisce")
        else:
            report.fail(
                f"owner-fixture-{label}-url-non-condivisibile",
                f"rifiuto atteso 422, ottenuto {risposta.status}"
                f"{_corpo(risposta)}")


def build_chain(report, http, cert, jars, owned) -> None:
    """Costruisce MATCH -> PROPOSAL -> SALE per ciascuna agenzia.

    PERCHE' ESISTE

    Questi tre domini erano dichiarati "non applicabili" perche' la loro
    creazione richiede un id a monte. La conclusione era sbagliata: quell'id lo
    produce la stessa API, con risorse che questo run possiede gia'. Se non li
    si costruisce, restano solo lista e marcatore - e su un TEST poco popolato
    quelle liste sono vuote, quindi la matrice non prova niente su tre domini
    che maneggiano denaro.

    OGNI PASSO PUO' FALLIRE, E ALLORA DICE PERCHE'

    Nessun passo produce un PASS piu' debole: se il calcolo non e' possibile o
    la transizione e' rifiutata, i domini a valle restano BLOCKED con lo stato
    HTTP nel report. Un dominio non provato deve apparire come non provato.
    """
    import uuid
    from datetime import datetime, timedelta, timezone

    for label in ("A", "B"):
        buy = owned[label].get("BUY")
        prop = owned[label].get("PROPERTY")
        if buy is None or prop is None:
            report.blocked(f"chain-{label}",
                           "mancano la richiesta d'acquisto o l'immobile di questo "
                           "run: la catena commerciale non e' costruibile")
            continue

        # 1. La prontezza si CHIEDE, non si presume: se il motore rifiutera' il
        #    calcolo, il report deve dire quale criterio manca invece di
        #    mostrare un 400 nudo.
        readiness = http.request(
            "GET", f"/api/match/readiness?buy_request_id={buy}&property_id={prop}",
            jar=jars[label])
        state = readiness.json() or {}
        if not state.get("can_match"):
            reasons = []
            for side in ("buy", "property"):
                block = state.get(side) or {}
                reasons += list(block.get("reasons") or [])
                reasons += list(block.get("eligibility_reasons") or [])
            report.blocked(
                f"chain-{label}",
                f"la coppia richiesta {buy} / immobile {prop} non e' calcolabile "
                f"({readiness.status}): {'; '.join(reasons) or 'nessun motivo riportato'}",
            )
            continue

        # 2. Il match.
        response = http.request("POST", "/api/match/calculate", jar=jars[label],
                                payload={"buy_request_id": buy, "property_id": prop})
        match_id = (response.json() or {}).get("id")
        if response.status not in (200, 201) or match_id is None:
            report.blocked(f"chain-{label}",
                           f"POST /api/match/calculate -> {response.status}: senza "
                           "match non nascono proposta e vendita")
            continue
        owned[label]["MATCH"] = match_id
        cert.created_match_ids.append(int(match_id))
        report.note(f"chain-MATCH-{label}",
                    f"MATCH calcolato per {label} dalla propria coppia (id {match_id})")

        # 3. La proposta. Il marcatore vive in `notes`: e' l'unico campo libero
        #    dello schema, ed e' cio' che rende leggibile una lista.
        expires = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        response = http.request("POST", "/api/proposals", jar=jars[label], payload={
            "match_id": match_id,
            "amount": 250000,
            "expires_at": expires,
            "notes": cert.marker(label),
            "idempotency_key": str(uuid.uuid4()),
        })
        proposal_id = (response.json() or {}).get("id")
        if response.status not in (200, 201) or proposal_id is None:
            report.blocked(f"chain-{label}",
                           f"POST /api/proposals -> {response.status}: la vendita "
                           "non e' raggiungibile")
            continue
        owned[label]["PROPOSAL"] = proposal_id
        cert.created_proposal_ids.append(int(proposal_id))
        report.note(f"chain-PROPOSAL-{label}",
                    f"PROPOSAL creata da {label} sul proprio match (id {proposal_id})")

        # 4. draft -> submitted -> accepted. proposal/enums.py non ammette il
        #    salto: una vendita esige una proposta accettata, e una proposta si
        #    accetta solo dopo essere stata presentata.
        blocked = False
        for target in ("submitted", "accepted"):
            response = http.request(
                "POST", f"/api/proposals/{proposal_id}/transition", jar=jars[label],
                payload={"target_status": target})
            if response.status not in (200, 201):
                report.blocked(
                    f"chain-{label}",
                    f"transizione della proposta {proposal_id} verso {target!r} -> "
                    f"{response.status}: la vendita non e' costruibile",
                )
                blocked = True
                break
        if blocked:
            continue

        # 5. La vendita.
        response = http.request("POST", "/api/sales", jar=jars[label], payload={
            "proposal_id": proposal_id,
            "notes": cert.marker(label),
            "idempotency_key": str(uuid.uuid4()),
        })
        sale_id = (response.json() or {}).get("id")
        if response.status not in (200, 201) or sale_id is None:
            report.blocked(f"chain-{label}",
                           f"POST /api/sales -> {response.status}")
            continue
        owned[label]["SALE"] = sale_id
        cert.created_sale_ids.append(int(sale_id))
        report.note(f"chain-SALE-{label}",
                    f"SALE creata da {label} sulla propria proposta (id {sale_id})")


def link_property_owner(report, http, cert, jars, owned, context) -> None:
    """Collega il contatto all'immobile come proprietario. Relazione LECITA.

    DUE MOTIVI, ED E' LO STESSO PASSO

    1. E' la relazione che PROPERTY puo' davvero attraversare. Il router non ha
       nessuna GET che parta da un immobile e arrivi altrove - la sonda
       precedente ne invocava una inesistente e prendeva 405 - mentre questa
       POST esiste e crea un legame vero fra due tenant-owned rows.
    2. `create_sale_scoped` la ESIGE. Cerca in `property_contacts` un ruolo
       'owner' o 'seller' per quell'immobile, con contatto e immobile nella
       stessa agenzia, e senza nemmeno una riga rifiuta con 409. E' l'ultima
       delle cinque precondizioni della vendita, ed e' quella che il run live
       non soddisfaceva.

    Va eseguita PRIMA della catena: senza, POST /api/sales fallisce sempre.
    """
    for label in ("A", "B"):
        contact = owned[label].get("CORE")
        prop = owned[label].get("PROPERTY")
        if contact is None or prop is None:
            report.blocked(f"PROPERTY-relazione-{label}",
                           "mancano il contatto o l'immobile di questo run")
            continue
        response = http.request(
            "POST", f"/api/property/properties/{prop}/contacts", jar=jars[label],
            payload={"contact_id": contact, "role": "owner", "is_primary": True},
        )
        if response.status not in (200, 201):
            report.blocked(
                f"PROPERTY-relazione-{label}",
                f"POST /properties/{prop}/contacts -> {response.status}: senza il "
                "legame proprietario la vendita non e' creabile (409) e la "
                "relazione lecita non e' osservabile",
            )
            continue
        context["owner_links"][label] = (prop, contact)
        report.note(f"PROPERTY-relazione-{label}",
                    f"{label} collega il proprio contatto {contact} al proprio "
                    f"immobile {prop} come proprietario")


def certify_property(report, http, cert, domain, jars, owned, context) -> None:
    """Le sonde generiche, piu' la relazione nelle due direzioni ostili.

    La relazione lecita e' gia' stata creata da `link_property_owner`: qui si
    prova che la STESSA operazione, incrociata, sia rifiutata. Due incroci
    distinti, perche' sono due domande diverse:

      * il contatto e' mio, l'immobile e' dell'altro
      * l'immobile e' mio, il contatto e' dell'altro

    Un isolamento che controllasse solo l'immobile passerebbe il primo e
    fallirebbe il secondo, e viceversa.
    """
    certify_generic(report, http, cert, domain, jars, owned)

    for label, other in (("A", "B"), ("B", "A")):
        mine_contact = owned[label].get("CORE")
        mine_property = owned[label].get("PROPERTY")
        other_contact = owned[other].get("CORE")
        other_property = owned[other].get("PROPERTY")

        if context["owner_links"].get(label) is None:
            report.blocked(
                f"PROPERTY-relazione-ostile-{label}-{other}",
                f"la relazione lecita di {label} non e' stata creata: un rifiuto "
                "sull'incrocio non distinguerebbe l'isolamento dalla rotta assente",
            )
            continue

        if other_property is not None and mine_contact is not None:
            response = http.request(
                "POST", f"/api/property/properties/{other_property}/contacts",
                jar=jars[label],
                payload={"contact_id": mine_contact, "role": "owner"},
            )
            report.check(
                f"PROPERTY-relazione-ostile-{label}-{other}",
                response.status in NEUTRAL_REFUSALS,
                f"{label} tenta di collegare il proprio contatto all'immobile "
                f"{other_property} di {other} -> {response.status}",
            )
        if mine_property is not None and other_contact is not None:
            response = http.request(
                "POST", f"/api/property/properties/{mine_property}/contacts",
                jar=jars[label],
                payload={"contact_id": other_contact, "role": "owner"},
            )
            report.check(
                f"PROPERTY-relazione-estranea-{label}-{other}",
                response.status in NEUTRAL_REFUSALS,
                f"{label} tenta di collegare il contatto {other_contact} di "
                f"{other} al proprio immobile -> {response.status}",
            )


FOLLOWUP_SCAN = "/api/followup/scan-temporal"


def stale_followup_candidates(agency_id: int) -> list:
    """Le attivita' che la scansione SELEZIONEREBBE, col predicato vero.

    Chiama la funzione applicativa, non una copia della sua SQL: una copia
    divergerebbe in silenzio. E' una SELECT e nient'altro - il modo in cui
    questa matrice guarda FOLLOWUP senza toccarlo.
    """
    from followup import repository
    from followup.service import TEMPORAL_ESCALATION_RULE_CODE

    return repository.list_temporal_escalation_candidates_for_agency(
        agency_id, limit=500, rule_code=TEMPORAL_ESCALATION_RULE_CODE)


FOLLOWUP_TASK_TITLE = "Contattare proprietario"


def _error_shape(text) -> str:
    """La FORMA di un errore, non il suo testo.

    Un messaggio grezzo puo' contenere valori di riga - psycopg2 mette nel
    DETAIL la chiave che ha violato un vincolo - e ripulirlo da email e cifre
    non basta: resterebbero nomi, indirizzi, titoli. Qui si estrae solo cio'
    che serve a diagnosticare e che non puo' essere di una persona:

      * lo SQLSTATE, se c'e' (cinque caratteri alfanumerici, es. 23503);
      * il nome del vincolo violato, se nominato (identificatore dello schema);
      * il tipo di eccezione, se il testo lo porta in testa.

    Se non si riconosce nulla, si dichiara la sola lunghezza: "presente ma non
    classificato" e' un'informazione onesta, il testo integrale no.
    """
    if not text:
        return "(vuoto)"
    testo = str(text)
    pezzi = []
    sqlstate = re.search(r"\b(?:SQLSTATE|sqlstate)[ :=]*([0-9A-Z]{5})\b", testo)
    if not sqlstate:
        sqlstate = re.search(r"\((?:pgcode|code)=([0-9A-Z]{5})\)", testo)
    if sqlstate:
        pezzi.append(f"sqlstate={sqlstate.group(1)}")
    vincolo = re.search(r'(?:constraint|vincolo)\s+"?([a-z0-9_]+)"?', testo, re.I)
    if vincolo:
        pezzi.append(f"vincolo={vincolo.group(1)}")
    tipo = re.match(r"\s*([A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception|Violation))\b", testo)
    if tipo:
        pezzi.append(f"tipo={tipo.group(1)}")
    else:
        # LA CLASSE psycopg, OVUNQUE SIA NEL TESTO.
        #
        # `re.match` la trova solo se apre la stringa, e un 500 di FastAPI la
        # consegna dentro un JSON: `{"detail":"UndefinedColumn: ..."}`. Il run
        # 38e341f68f8a si e' chiuso con "non classificato (104 caratteri)" su
        # un messaggio che diceva esattamente qual era il guasto.
        #
        # L'elenco e' chiuso e fatto di NOMI DI CLASSE: non possono essere di
        # una persona, a differenza di un frammento di messaggio.
        classe = re.search(
            r"\b(UndefinedColumn|UndefinedTable|UndefinedFunction|UndefinedObject"
            r"|ForeignKeyViolation|UniqueViolation|NotNullViolation|CheckViolation"
            r"|InvalidTextRepresentation|DatatypeMismatch|SyntaxError"
            r"|InsufficientPrivilege|DeadlockDetected|SerializationFailure"
            r"|LockNotAvailable|QueryCanceled|OperationalError|ProgrammingError"
            r"|IntegrityError|DataError|InternalError)\b", testo)
        if classe:
            pezzi.append(f"tipo={classe.group(1)}")
    # L'oggetto nominato dall'errore: identificatore di schema, mai un valore.
    oggetto = re.search(r'column\s+"?([a-z0-9_.]+)"?\s+does not exist', testo, re.I)
    if not oggetto:
        oggetto = re.search(r'relation\s+"?([a-z0-9_.]+)"?\s+does not exist', testo, re.I)
    if oggetto:
        pezzi.append(f"oggetto={oggetto.group(1)}")
    return ", ".join(pezzi) if pezzi else f"non classificato ({len(testo)} caratteri)"


def certify_followup_dedicated(report, http, cert, jars, context) -> bool:
    """La scansione ESEGUITA, su due agenzie che contengono solo nostre righe.

    PERCHE' QUESTA E' SEPARAZIONE E LE ALTRE NO

    Il predicato della scansione e' `WHERE t.agency_id = %s` e non ammette una
    restrizione agli id. Su un'agenzia condivisa questo significa che ogni
    attivita' stale del tenant e' candidata, e nessun ordinamento, limite o
    controllo a posteriori lo cambia: quando ce ne accorgiamo, la riga altrui
    e' gia' modificata.

    Su un'agenzia CREATA DA QUESTO RUN non esiste alcuna riga che non sia
    nostra. Il predicato diventa la separazione, senza che il backend cambi di
    una virgola.

    COSA RESTA FUORI DALLE NOSTRE MANI

    Un'agenzia dev'essere attiva perche' una sessione risolva, e attiva
    significa visibile a ogni processo platform-wide. Che quei processi siano
    fermi lo attesta l'operatore con `--with-dedicated-agencies`: questo codice
    non lo rileva e non finge di rilevarlo.
    """
    from datetime import datetime, timedelta, timezone

    dedicate, task = {}, {}
    for label in ("C", "D"):
        agency = cert.create_dedicated_agency(label)
        if agency is None:
            return False
        jar = http.new_jar()
        risposta = http.request("POST", LOGIN, jar=jar, payload={
            "email": agency["email"], "password": agency["password"]})
        if risposta.status != 204:
            report.blocked(f"FOLLOWUP-dedicata-{label}",
                           f"login dell'operatore dedicato -> {risposta.status}")
            return False
        token = http.token_in(jar)
        if token:
            cert.secrets.append(token)
        dedicate[label] = {"agency": agency, "jar": jar, "contact": None}

    # Un contatto e un'attivita' stale per agenzia, creati via API dentro
    # l'agenzia dedicata: nient'altro esiste li' dentro.
    due = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    for label, dati in dedicate.items():
        jar = dati["jar"]
        risposta = http.request("POST", "/api/core/contacts", jar=jar, payload={
            "display_name": cert.marker(label), "status": "active"})
        contatto = (risposta.json() or {}).get("id")
        if risposta.status not in (200, 201) or contatto is None:
            report.blocked(f"FOLLOWUP-dedicata-{label}",
                           f"contatto non creato ({risposta.status})")
            return False
        # NON in `created_rows`: quello cancella per terna id+marcatore+agenzia
        # contro le due agenzie della matrice, e questo contatto sta in
        # un'agenzia dedicata. Lo rimuove `cleanup_dedicated_agencies`, che
        # cancella per agency_id - dove ogni riga e' del run per costruzione.

        risposta = http.request("POST", "/api/core/tasks", jar=jar, payload={
            "contact_id": contatto,
            "title": FOLLOWUP_TASK_TITLE,
            "description": cert.marker(label),
            "task_type": "automated_followup",
            "priority": "low",
            "status": "open",
            "due_at": due,
            "metadata": {"source": "followup",
                         "rule_code": "FOLLOWUP_STIMA_RICHIESTA",
                         "marker": cert.marker(label)},
        })
        identificativo = (risposta.json() or {}).get("id")
        if risposta.status not in (200, 201) or identificativo is None:
            report.blocked(f"FOLLOWUP-dedicata-{label}",
                           f"attivita' non creata ({risposta.status})")
            return False
        task[label] = int(identificativo)
        dedicate[label]["contact"] = contatto
        report.note(f"FOLLOWUP-dedicata-{label}",
                    f"agenzia {dati['agency']['id']}: contatto e attivita' "
                    f"{identificativo}, unica riga del tenant")

    def stato(label):
        listing = http.request(
            "GET", f"/api/core/tasks?limit=50", jar=dedicate[label]["jar"])
        riga = next((t for t in listing.items() if t.get("id") == task[label]), None)
        return (riga.get("status"), riga.get("priority")) if riga else None

    for label, altro in (("C", "D"), ("D", "C")):
        prima = stato(altro)
        risposta = http.request("POST", FOLLOWUP_SCAN, jar=dedicate[label]["jar"],
                                payload={"limit": 100})
        if risposta.status == 400:
            report.blocked(f"FOLLOWUP-scan-{label}",
                           f"scansione rifiutata (400): la regola potrebbe non "
                           "essere abilitata su questo TEST")
            return False
        corpo = risposta.json() or {}
        elaborati = [int(x["task_id"]) for x in corpo.get("items", [])
                     if x.get("task_id") is not None]

        report.check(f"FOLLOWUP-scan-{label}", risposta.status == 200,
                     f"{label} esegue la scansione -> {risposta.status}")
        # ESSERE NELL'ELENCO NON E' ESCALATION.
        #
        # `execute_temporal_escalation_for_agency` restituisce l'elemento anche
        # quando non tocca il task: se l'azione esisteva gia' (`_created`
        # False) o se l'UPDATE ha sollevato, l'elemento c'e' con uno stato
        # diverso da 'completed'. Il run 9b95b3ee215e ha lasciato la fixture C
        # open/low con la prova segnata PASS proprio per questo.
        #
        # Tre cose, tutte e tre: lo stato dell'elemento, lo stato del task
        # riletto, l'azione persistita.
        proprio = next((x for x in corpo.get("items", [])
                        if x.get("task_id") is not None and int(x["task_id"]) == task[label]),
                       None)
        report.check(
            f"FOLLOWUP-scan-{label}-elabora-la-propria",
            proprio is not None and proprio.get("status") == "completed",
            f"l'attivita' {task[label]} di {label} risulta "
            + ("completata" if proprio and proprio.get("status") == "completed"
               else f"'{(proprio or {}).get('status', 'assente')}'"
                    + (f" - errore: {_error_shape(proprio.get('error'))}"
                       if proprio and proprio.get("error") else "")),
        )
        dopo_propria = stato(label)
        report.check(
            f"FOLLOWUP-scan-{label}-task-escalato",
            dopo_propria == ("in_progress", "high"),
            f"l'attivita' {task[label]} di {label} e' {dopo_propria} "
            "(attesa in_progress/high)",
        )
        try:
            with cert.db.read() as cur:
                cur.execute(
                    "SELECT COUNT(*) AS n FROM followup_actions "
                    " WHERE task_id = %s AND agency_id = %s AND status = 'completed'",
                    (task[label], dedicate[label]["agency"]["id"]))
                persistite = int(cur.fetchone()["n"])
                # L'EVIDENZA, prima che il cleanup la porti via: se un'azione
                # e' 'failed', `error_message` dice perche' - ed e' l'unica
                # traccia sul database, perche' la risposta HTTP porta lo
                # stesso testo ma nessuno la conserva.
                cur.execute(
                    "SELECT status, error_message FROM followup_actions "
                    " WHERE idempotency_key LIKE %s AND agency_id = %s AND status <> 'completed'",
                    (f"followup:time:%:task:{task[label]}:v1", dedicate[label]["agency"]["id"]))
                for riga in cur.fetchall():
                    report.note(
                        f"FOLLOWUP-scan-{label}-evidenza",
                        f"azione '{riga['status']}' per l'attivita' {task[label]}, "
                        f"errore: {_error_shape(riga.get('error_message'))}",
                    )
        except Exception as exc:
            persistite = None
            report.blocked(f"FOLLOWUP-scan-{label}-azione-persistita",
                           f"azione non leggibile ({type(exc).__name__})")
        if persistite is not None:
            report.check(
                f"FOLLOWUP-scan-{label}-azione-persistita",
                persistite == 1,
                f"{persistite} azioni completate persistite per l'attivita' "
                f"{task[label]} (attesa 1)",
            )
        report.check(
            f"FOLLOWUP-scan-{label}-solo-la-propria",
            set(elaborati) <= {task[label]},
            f"la scansione di {label} ha elaborato {elaborati}: "
            + ("nient'altro che la propria"
               if set(elaborati) <= {task[label]}
               else f"ATTENZIONE, righe non del run: "
                    f"{sorted(set(elaborati) - {task[label]})}"),
        )
        dopo = stato(altro)
        report.check(
            f"FOLLOWUP-scan-{label}-{altro}-invariata",
            prima is not None and dopo == prima,
            f"l'attivita' {task[altro]} di {altro} e' {dopo} dopo la scansione "
            f"di {label}, com'era prima ({prima})",
        )

    report.note("FOLLOWUP-escalation",
                "escalation eseguita su agenzie dedicate: nessuna riga "
                "preesistente era candidata, per costruzione")

    # Le stesse due agenzie servono ai tre domini che hanno solo operazioni
    # batch: la' dentro ogni riga e' del run, quindi una passata su tutto il
    # tenant non puo' toccare nulla di altrui.
    certify_batch_domains(report, http, cert, dedicate, context)
    return True


def certify_batch_domains(report, http, cert, dedicate, context) -> None:
    """FLOW, NEXT_BEST_ACTION e PROPERTY_WATCH sulle agenzie dedicate.

    Tutti e tre hanno lo stesso problema: la risorsa riconoscibile nasce da
    un'operazione che percorre l'intero tenant - `POST /flow/events` valuta le
    regole, `POST /next-best-action/refresh` ricalcola e POTA le azioni
    esistenti, la stima serve un watch. Su un'agenzia condivisa nessuna delle
    tre e' eseguibile senza toccare righe altrui; su un'agenzia del run tutte e
    tre lo sono, senza che il backend cambi.

    Una lista non vuota non basta: si esige che ciascuno veda la PROPRIA
    risorsa e non quella dell'altro, e le risorse portano il marcatore.
    """
    etichette = tuple(dedicate)

    # --- FLOW: un evento per agenzia, con il marcatore nel tipo ------------
    eventi = {}
    for label in etichette:
        # `source_module` E' OBBLIGATORIO, e mancava.
        #
        # `flow/schemas.EventCreate` lo dichiara
        # `Literal["core","property","buy","match","flow","owner"]` senza
        # valore predefinito: un payload che non lo porta prende 422 prima di
        # arrivare al servizio. E' il 422 di FLOW-fixture-C/D del run
        # 38e341f68f8a - nessun evento creato, quindi il confronto fra le due
        # liste non aveva niente da confrontare.
        #
        # "core" e non "flow": l'entita' e' un contatto, e il modulo dichiarato
        # e' quello da cui l'evento proviene, non quello che lo riceve.
        risposta = http.request("POST", "/api/flow/events", jar=dedicate[label]["jar"],
                                payload={"event_type": cert.marker(label),
                                         "entity_type": "contact",
                                         "entity_id": dedicate[label]["contact"],
                                         "source_module": cert.FLOW_SOURCE_MODULE,
                                         "payload": {},
                                         "deduplication_key": cert.marker(label)})
        identificativo = ((risposta.json() or {}).get("event") or {}).get("id") \
            or (risposta.json() or {}).get("id")
        if risposta.status not in (200, 201) or identificativo is None:
            report.blocked(f"FLOW-fixture-{label}",
                           f"POST /api/flow/events -> {risposta.status}")
            continue
        eventi[label] = int(identificativo)
        cert.created_effects.setdefault("flow_events", []).append(int(identificativo))
        report.note(f"FLOW-fixture-{label}",
                    f"evento {identificativo} nell'agenzia dedicata di {label}")

    for label, altro in (("C", "D"), ("D", "C")):
        if len(eventi) < 2:
            report.blocked(f"FLOW-list-{label}-non-vede-{altro}",
                           "manca un evento per agenzia: il confronto non prova nulla")
            continue
        risposta = http.request("GET", "/api/flow/events?limit=100",
                                jar=dedicate[label]["jar"])
        report.check(f"FLOW-list-{label}", risposta.status == 200,
                     f"{label} legge i propri eventi -> {risposta.status}")
        report.check(f"FLOW-list-{label}-vede-la-propria",
                     cert.marker(label) in risposta.text(),
                     f"la lista di {label} contiene il proprio evento {eventi[label]}")
        report.check(f"FLOW-list-{label}-non-vede-{altro}",
                     cert.marker(altro) not in risposta.text(),
                     f"la lista di {label} non contiene l'evento {eventi[altro]} di "
                     f"{altro} ({len(risposta.items())} elementi osservati)")

    # --- PROPERTY_WATCH: una stima per agenzia, creata dal run -------------
    #
    # Le stime nascono dal funnel pubblico, che risolve la Default: da li' non
    # se ne ottiene una per un'altra agenzia. Ma il funnel e' UNA strada, non
    # l'unica: la riga si puo' inserire direttamente nell'agenzia del run, che
    # e' la stessa cosa che facciamo per agenzie e operatori. Nessun invio,
    # nessun PDF, nessuna email - quelli stanno in `salva_stima`, non
    # nell'INSERT - e nessun dato preesistente toccato.
    stime = {}
    for label in etichette:
        try:
            with cert.db.write() as cur:
                cur.execute(
                    "INSERT INTO stime (comune, via, tipologia, mq, prezzo_mq_base, "
                    "                   agency_id) "
                    "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                    (cert.marker(label), cert.marker(label), "appartamento",
                     80, 2000, dedicate[label]["agency"]["id"]))
                stime[label] = int(cur.fetchone()["id"])
            cert.created_effects.setdefault("stime", []).append(stime[label])

            # LA STIMA DA SOLA NON BASTA, e il run 38e341f68f8a lo ha
            # dimostrato con un 400.
            #
            # `_baseline_for_stima_scoped` legge DUE cose: gli attributi della
            # stima e la valutazione completata, che sta in
            # `seller_timeline_events` con `event_type = 'stima_completata'`.
            # Senza la seconda solleva ValidationError, e il router la traduce
            # in 400: `initialize -> 400: nessun watch da osservare`.
            #
            # L'evento si inserisce con lo stesso criterio della stima: nessun
            # invio, nessun PDF, nessuna email - quelli stanno in
            # `salva_stima`, non in questo INSERT - e dentro l'agenzia del run,
            # dove ogni riga e' nostra per costruzione.
            with cert.db.write() as cur:
                cur.execute(
                    "INSERT INTO seller_timeline_events "
                    "  (stima_id, event_type, event_source, payload, agency_id) "
                    "VALUES (%s, %s, %s, %s::jsonb, %s) RETURNING id",
                    (stime[label], cert.STIMA_COMPLETATA_EVENT, "p26-6-cert",
                     json.dumps({"price_exact": 160000, "eur_mq_finale": 2000,
                                 "base_mq": 2000}),
                     dedicate[label]["agency"]["id"]))
                cert.created_effects.setdefault(
                    "seller_timeline_events", []).append(int(cur.fetchone()["id"]))
        except Exception as exc:
            report.blocked(f"PROPERTY_WATCH-fixture-{label}",
                           f"stima non creabile ({type(exc).__name__})")
            continue
        risposta = http.request("POST", f"/api/property-watch/stime/{stime[label]}/initialize",
                                jar=dedicate[label]["jar"])
        if risposta.status not in (200, 201):
            report.blocked(f"PROPERTY_WATCH-fixture-{label}",
                           f"initialize -> {risposta.status}: nessun watch da osservare")
            stime.pop(label, None)
            continue
        report.note(f"PROPERTY_WATCH-fixture-{label}",
                    f"stima {stime[label]} e watch nell'agenzia dedicata di {label}")

    for label, altro in (("C", "D"), ("D", "C")):
        if len(stime) < 2:
            report.blocked(f"PROPERTY_WATCH-ostile-{label}-{altro}",
                           "manca un watch per agenzia: il confronto non prova nulla")
            continue
        propria = http.request("GET", f"/api/property-watch/stime/{stime[label]}",
                               jar=dedicate[label]["jar"])
        report.check(f"PROPERTY_WATCH-propria-{label}", propria.status == 200,
                     f"{label} legge il watch della propria stima {stime[label]} "
                     f"-> {propria.status}")
        ostile = http.request("GET", f"/api/property-watch/stime/{stime[altro]}",
                              jar=dedicate[label]["jar"])
        report.check(f"PROPERTY_WATCH-ostile-{label}-{altro}",
                     ostile.status in NEUTRAL_REFUSALS,
                     f"{label} chiede il watch della stima {stime[altro]} di {altro} "
                     f"-> {ostile.status}, mentre {altro} sulla stessa riga ottiene 200")
        scrittura = http.request(
            "POST", f"/api/property-watch/stime/{stime[altro]}/initialize",
            jar=dedicate[label]["jar"])
        report.check(f"PROPERTY_WATCH-ostile-write-{label}-{altro}",
                     scrittura.status in NEUTRAL_REFUSALS,
                     f"{label} tenta di inizializzare il watch di {altro} "
                     f"-> {scrittura.status}")

    # --- NEXT_BEST_ACTION: refresh dentro l'agenzia propria ----------------
    #
    # DOPO la fixture PROPERTY_WATCH, e non e' un riordino estetico. Il
    # refresh raccoglie i segnali da P17-P22: in un'agenzia appena creata, con
    # un contatto e un'attivita', non c'e' NIENTE da cui nascere, e "0 azioni"
    # e' garantito prima ancora di chiamare la route - una prova che non puo'
    # fallire non e' una prova. La stima con la sua valutazione completata,
    # inserita qui sopra, e' un segnale P17 vero: se anche cosi' il refresh
    # non materializza nulla, il BLOCKED dice qualcosa sulle regole invece che
    # sull'ordine delle chiamate.
    #
    # E COSI' E' ANDATA: il run 42e32975ccd6 ha materializzato ZERO azioni in
    # entrambe le agenzie, con le stime e le valutazioni gia' al loro posto.
    # I segnali sono cinque (`collect_all_signals_scoped`) e nessuno di essi
    # nasce da una stima: quattro partono da un lead, da un match, da una
    # opportunita' invisible-sale o dal motore di revival, e il quinto da
    # FLOW-R004. In un'agenzia con un contatto, un'attivita' e una stima non
    # c'e' nulla che li soddisfi.
    #
    # Il piu' semplice e DETERMINISTICO e' `next_action_overdue`, in
    # `_lead_candidates_from_score`: basta un lead APERTO con `next_action_at`
    # nel passato. `LeadCreate` esige il solo `contact_id` e accetta
    # `next_action_at`, e il contatto dell'agenzia dedicata esiste gia'.
    # Nessuna regola applicativa viene toccata: si crea il dato che la regola
    # esistente prevede.
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    for label in etichette:
        contatto = dedicate[label].get("contact")
        if contatto is None:
            report.blocked(f"NEXT_BEST_ACTION-fixture-{label}",
                           "nessun contatto nell'agenzia dedicata: senza un lead "
                           "il refresh non ha segnali da raccogliere")
            continue
        scaduto = (_dt.now(_tz.utc) - _td(days=3)).isoformat()
        risposta = http.request(
            "POST", "/api/core/leads", jar=dedicate[label]["jar"],
            payload={"contact_id": contatto, "status": "open",
                     "next_action_at": scaduto, "notes": cert.marker(label)})
        lead = (risposta.json() or {}).get("id")
        if risposta.status not in (200, 201) or lead is None:
            report.blocked(
                f"NEXT_BEST_ACTION-fixture-{label}",
                f"lead non creabile -> {risposta.status}{_corpo(risposta)}: senza "
                "segnale il refresh non puo' materializzare nulla")
            continue
        # Tracciato PRIMA di qualunque altra cosa: `leads` si cancella per
        # `agency_id` insieme all'agenzia dedicata, ma il perimetro deve
        # conoscerlo comunque - il preflight guarda anche le sue figlie.
        cert.created_effects.setdefault("leads", []).append(int(lead))
        report.note(f"NEXT_BEST_ACTION-segnale-{label}",
                    f"lead {lead} aperto con azione scaduta nell'agenzia di {label}")

    azioni = {}
    for label in etichette:
        risposta = http.request("POST", "/api/next-best-action/refresh",
                                jar=dedicate[label]["jar"], payload={})
        if risposta.status != 200:
            report.blocked(f"NEXT_BEST_ACTION-fixture-{label}",
                           f"refresh -> {risposta.status}")
            continue
        lista = http.request("GET", "/api/next-best-action?limit=100",
                             jar=dedicate[label]["jar"])
        voci = lista.items()
        azioni[label] = {int(v["id"]) for v in voci if v.get("id") is not None}
        report.note(f"NEXT_BEST_ACTION-fixture-{label}",
                    f"{len(voci)} azioni materializzate nell'agenzia di {label}")

    if len(azioni) == 2 and (azioni["C"] or azioni["D"]):
        comuni = azioni["C"] & azioni["D"]
        report.check("NEXT_BEST_ACTION-disgiunte", not comuni,
                     f"le azioni di C ({len(azioni['C'])}) e D ({len(azioni['D'])}) "
                     f"non hanno id in comune")
        for label in etichette:
            if not azioni[label]:
                report.blocked(f"NEXT_BEST_ACTION-appartenenza-{label}",
                               "nessuna azione materializzata: niente di cui "
                               "verificare l'agenzia")
                continue
            try:
                with cert.db.read() as cur:
                    cur.execute("SELECT COUNT(*) AS n FROM next_best_actions "
                                " WHERE id IN %s AND agency_id <> %s",
                                (tuple(azioni[label]), dedicate[label]["agency"]["id"]))
                    estranee = int(cur.fetchone()["n"])
            except Exception as exc:
                report.blocked(f"NEXT_BEST_ACTION-appartenenza-{label}",
                               f"agenzia non leggibile ({type(exc).__name__})")
                continue
            report.check(f"NEXT_BEST_ACTION-appartenenza-{label}", estranee == 0,
                         f"tutte le {len(azioni[label])} azioni di {label} "
                         f"appartengono alla sua agenzia")
    else:
        # La lista vuota NON e' una prova di isolamento, e il messaggio deve
        # dire che cosa c'era davvero nell'agenzia quando il refresh non ha
        # prodotto niente: senza, il prossimo run ripete la stessa riga senza
        # sapere se manchi il segnale o la regola.
        report.blocked("NEXT_BEST_ACTION-disgiunte",
                       f"con {len(stime)} stime e altrettanti eventi "
                       f"'{cert.STIMA_COMPLETATA_EVENT}' nelle agenzie "
                       "dedicate, il refresh non ha materializzato nulla: "
                       "nessuna azione materializzata in nessuna delle due "
                       "agenzie dedicate: il confronto non proverebbe nulla")


def certify_batch_only(report, http, cert, domain, jars, owned, context) -> None:
    """Domini la cui risorsa nasce solo da un'operazione sull'intero tenant.

    Senza agenzie dedicate non c'e' modo di crearne una senza toccare righe di
    altri, e una lista vuota non prova nulla: BLOCKED, con il motivo. Con le
    agenzie dedicate la prova la fa `certify_batch_domains`.
    """
    if context.get("dedicated_agencies"):
        return
    report.blocked(
        f"{domain.name}-batch",
        "la risorsa nasce da un'operazione che percorre l'intero tenant: su "
        "un'agenzia condivisa la creerebbe toccando righe altrui. Eseguire con "
        "--with-dedicated-agencies",
    )


def certify_followup(report, http, cert, domain, jars, owned, context) -> None:
    """FOLLOWUP: si osserva la SELEZIONE, non si esegue l'escalation.

    PERCHE' LA SCANSIONE NON VIENE LANCIATA

    `POST /api/flow/... /scan-temporal` fa due cose: seleziona le attivita'
    stale dell'agenzia e le ESCALA - `tasks.priority` a 'high', `status` a
    'in_progress', piu' una riga in `followup_actions`. La seconda meta' e' una
    scrittura su righe che questo run non possiede.

    Tre difese sono state provate e scartate, e vale la pena dire perche':

      * contare le candidate prima (preflight): dice quante ce ne sono ADESSO,
        non quante ce ne saranno fra un istante. Il predicato e'
        `due_at <= NOW() - INTERVAL '24 hours'`, quindi una riga preesistente
        diventa eleggibile da sola, col passare del tempo;
      * un secondo conteggio: insegue lo stesso istante che non puo' fermare.
        Fra il controllo e l'uso c'e' sempre uno spazio;
      * fixture con scadenza remotissima e `limit=1`, cosi' che la nostra
        ordini per prima: un INSERIMENTO CONCORRENTE con una scadenza ancora
        piu' vecchia la scavalca. E accorgersene dopo non e' isolamento: la
        riga di qualcun altro e' gia' stata modificata.

    La separazione vera non e' costruibile da qui. La firma e'
    `list_temporal_escalation_candidates_for_agency(agency_id, *, limit,
    rule_code)` e il predicato non ha alcun aggancio per restringere a un
    elenco di id: non esiste modo, senza cambiare il backend, di impedire alla
    scansione di toccare cio' che c'era prima. E cambiare il backend per far
    passare una prova sarebbe adattare il sistema al suo test.

    COSA SI PUO' PROVARE, E SI PROVA

    L'isolamento fra agenzie vive per intero nella SELEZIONE: il predicato
    porta `t.agency_id = %s` e lo porta anche l'anti-join su
    `followup_actions`. Quella selezione e' una lettura pura, quindi la si
    esegue per A e per B sui dati reali e si verifica che i due insiemi siano
    disgiunti. Nessuna riga viene scritta.

    Cio' che resta non provato e' l'escalation - la meta' in scrittura - ed e'
    BLOCKED, con questo motivo. Non "non applicabile": non provata.
    """
    # Con la finestra attestata dall'operatore, l'escalation si prova davvero.
    if context.get("dedicated_agencies"):
        if certify_followup_dedicated(report, http, cert, jars, context):
            return

    agencies = context.get("agencies", {})
    candidate = {}

    for label, agency in agencies.items():
        try:
            righe = stale_followup_candidates(agency["id"])
        except Exception as exc:
            report.blocked(
                f"FOLLOWUP-selezione-{label}",
                f"candidate non interrogabili ({type(exc).__name__}): la "
                "selezione non e' osservabile su questo TEST",
            )
            return
        candidate[label] = {int(riga["id"]) for riga in righe}
        report.note(
            f"FOLLOWUP-selezione-{label}",
            f"la selezione di {label} propone {len(candidate[label])} attivita' "
            "(sola lettura, nessuna escalation eseguita)",
        )

    if len(candidate) < 2:
        report.blocked("FOLLOWUP-selezione-disgiunta",
                       "una sola agenzia osservata: il confronto non e' possibile")
        return

    # 1. DISGIUNZIONE: nessuna attivita' compare in entrambe le selezioni.
    #
    # Prima perche' non costa una lettura, e perche' il suo fallimento e' il
    # piu' leggibile. Su insiemi vuoti e' vera per costruzione e non prova
    # nulla - e da sola non basta comunque: vedi il punto 2.
    comune = candidate["A"] & candidate["B"]
    if not candidate["A"] and not candidate["B"]:
        report.blocked(
            "FOLLOWUP-selezione-disgiunta",
            "entrambe le selezioni sono vuote: la disgiunzione e' vera per "
            "costruzione e non dimostra l'isolamento",
        )
    else:
        report.check(
            "FOLLOWUP-selezione-disgiunta",
            not comune,
            f"le selezioni di A ({len(candidate['A'])}) e B ({len(candidate['B'])}) "
            f"non hanno attivita' in comune"
            + ("" if not comune else f": ATTENZIONE, condivise {sorted(comune)}"),
        )


    # 2. APPARTENENZA: ogni attivita' selezionata e' dell'agenzia richiesta.
    #
    # E' la prova che conta. La disgiunzione sopra e' piu' debole di quanto
    # sembri: due insiemi possono essere disgiunti e sbagliati ENTRAMBI - basta
    # che la selezione di A restituisca attivita' di una terza agenzia e quella
    # di B di una quarta. Nessun id in comune, e nessuna delle due appartiene a
    # chi l'ha chiesta.
    #
    # Qui si legge l'agenzia REALE di ogni riga selezionata e la si confronta
    # con quella richiesta. Su un database vero un'intersezione implica gia' un
    # difetto di appartenenza, ma il contrario non vale: questa prova copre
    # casi che la disgiunzione non vede.
    for label, agency in agencies.items():
        ids = candidate[label]
        if not ids:
            report.blocked(
                f"FOLLOWUP-appartenenza-{label}",
                f"la selezione di {label} e' vuota: non c'e' alcuna riga di cui "
                "verificare l'agenzia",
            )
            continue
        try:
            with cert.db.read() as cur:
                cur.execute(
                    "SELECT id, agency_id FROM tasks WHERE id IN %s", (tuple(ids),))
                proprietarie = {int(r["id"]): r["agency_id"] for r in cur.fetchall()}
        except Exception as exc:
            report.blocked(f"FOLLOWUP-appartenenza-{label}",
                           f"agenzia delle attivita' non leggibile ({type(exc).__name__})")
            continue
        estranee = {identifier: proprietarie.get(identifier)
                    for identifier in ids
                    if proprietarie.get(identifier) != agency["id"]}
        report.check(
            f"FOLLOWUP-appartenenza-{label}",
            not estranee,
            f"tutte le {len(ids)} attivita' selezionate per {label} appartengono "
            f"all'agenzia {agency['id']}"
            + ("" if not estranee
               else f": ATTENZIONE, estranee {estranee}"),
        )

    # E l'altra meta' della route resta non provata, con il motivo.
    report.blocked(
        "FOLLOWUP-escalation",
        "l'escalation non viene eseguita: modifica attivita' preesistenti e il "
        "predicato non ammette una restrizione agli id di questo run "
        "(list_temporal_escalation_candidates_for_agency accetta solo agency_id, "
        "limit e rule_code). Provarla richiederebbe un filtro lato backend, che "
        "e' una decisione di prodotto, non un adattamento per il test",
    )


def certify_chain(report, http, cert, domain, jars, owned, context) -> None:
    """MATCH, PROPOSAL e SALE: le stesse sei domande di ogni altro dominio.

    La catena e' gia' stata costruita da `build_chain`, quindi qui non c'e'
    niente di speciale da fare - ed e' il punto. Un dominio costruibile e' un
    dominio normale: lista, marcatore, ID diretto, scrittura ostile.
    """
    certify_generic(report, http, cert, domain, jars, owned)


def certify_owner_admin(report, http, cert, domain, jars, owned, context) -> None:
    """OWNER Admin, interrogato da chi ha davvero il ruolo per entrarci.

    Due prove distinte, e vanno tenute distinte:

    1. LA SOGLIA. Le identita' della matrice sono `agency_admin` e devono
       ricevere 403. E' isolamento in una dimensione diversa - il ruolo - ed e'
       cio' che la versione precedente provava. Resta, perche' e' vero.
    2. LO SCOPE. Con due sessioni `agency_owner` vere si torna alle domande di
       sempre: A vede la propria, B la propria, e nessuno dei due l'altra. E'
       la prova che prima non c'era, perche' il 403 arrivava troppo presto.
    """
    for label in ("A", "B"):
        response = http.request("GET", domain.listing, jar=jars[label])
        report.check(f"OWNER_ADMIN-soglia-{label}", response.status == 403,
                     f"{label} ({CERT_ROLE}) su OWNER Admin -> {response.status} "
                     "(atteso 403: la soglia e' agency_owner)")

    owner_jars = context.get("owner_jars", {})
    accounts = context.get("owner_accounts", {})

    for label in ("A", "B"):
        if label not in owner_jars:
            report.blocked(
                f"OWNER_ADMIN-scope-{label}",
                f"nessuna sessione agency_owner per l'agenzia {label}: la "
                "superficie resta provata solo sulla soglia di ruolo",
            )
            continue

        # Positiva: il titolare vede la propria lista, e ci trova il conto che
        # ha appena creato sul proprio contatto.
        response = http.request("GET", domain.listing, jar=owner_jars[label])
        report.check(
            f"OWNER_ADMIN-scope-{label}",
            response.status == 200,
            f"il titolare di {label} legge {domain.listing} -> {response.status}",
        )
        if accounts.get(label) is not None:
            report.check(
                f"OWNER_ADMIN-vede-la-propria-{label}",
                cert.marker(label) in response.text(),
                f"la lista del titolare di {label} contiene il conto creato su "
                "un contatto di questo run",
            )

    for label, other in (("A", "B"), ("B", "A")):
        if label not in owner_jars:
            continue
        # Ostile sulla lista.
        response = http.request("GET", domain.listing, jar=owner_jars[label])
        items = response.items()
        if accounts.get(other) is None and not items:
            report.blocked(
                f"OWNER_ADMIN-non-vede-{other}",
                f"la lista del titolare di {label} e' vuota e questo run non "
                f"possiede un conto di {other}: il confronto non proverebbe nulla",
            )
        else:
            report.check(
                f"OWNER_ADMIN-non-vede-{other}",
                cert.marker(other) not in response.text(),
                f"il titolare di {label} non vede il conto di {other} "
                f"({len(items)} elementi osservati)",
            )

        # Ostile sulla scrittura, e REVERSIBILE: `disable` ha il proprio
        # `enable`, quindi la prova non lascia dietro di se' un conto spento.
        target = accounts.get(other)
        if target is None:
            report.blocked(f"OWNER_ADMIN-write-{label}-{other}",
                           f"nessun conto di {other} creato da questo run")
            continue
        response = http.request(
            "POST", f"{OWNER_ACCOUNTS}/{target}/disable", jar=owner_jars[label])
        report.check(
            f"OWNER_ADMIN-write-{label}-{other}",
            response.status in NEUTRAL_REFUSALS,
            f"il titolare di {label} tenta di disabilitare il conto {target} di "
            f"{other} -> {response.status} (atteso uno di {NEUTRAL_REFUSALS})",
        )
        # E il conto dell'altro deve essere rimasto attivo: un rifiuto che
        # avesse comunque scritto sarebbe il caso peggiore.
        if other in owner_jars:
            check = http.request("GET", domain.listing, jar=owner_jars[other])
            body = check.json() or {}
            row = next((item for item in (body.get("items") or [])
                        if item.get("id") == target), None)
            report.check(
                f"OWNER_ADMIN-write-{label}-{other}-intatto",
                row is not None and row.get("status") != "disabled",
                f"il conto {target} di {other} non porta traccia del tentativo "
                f"di {label}",
            )

    # La scrittura reversibile sul PROPRIO conto: prova che la superficie sia
    # davvero operativa per il titolare, e non solo leggibile.
    for label in ("A", "B"):
        target = accounts.get(label)
        if label not in owner_jars or target is None:
            continue
        off = http.request("POST", f"{OWNER_ACCOUNTS}/{target}/disable",
                           jar=owner_jars[label])
        on = http.request("POST", f"{OWNER_ACCOUNTS}/{target}/enable",
                          jar=owner_jars[label])
        report.check(
            f"OWNER_ADMIN-write-propria-{label}",
            off.status == 200 and on.status == 200,
            f"il titolare di {label} disabilita e riabilita il proprio conto "
            f"{target} ({off.status}/{on.status}): scrittura reversibile, "
            "nessuno stato lasciato indietro",
        )


def certify_owner_portal(report, http, cert, domain, jars, owned, context) -> None:
    """Il portale proprietari: un principale diverso, lo stesso tenant.

    Il proprietario non e' un operatore e non ha un'agenzia propria: eredita
    quella del contatto a cui il suo conto e' agganciato. La domanda ostile e'
    percio' la piu' diretta di tutta la matrice - "fammi vedere l'immobile che
    appartiene all'altra agenzia" - e la risposta deve essere la stessa che
    riceverebbe un estraneo.
    """
    portal_jars = context.get("portal_jars", {})
    properties = context.get("portal_properties", {})

    for label in ("A", "B"):
        if label not in portal_jars:
            report.blocked(
                f"OWNER_PORTAL-propria-{label}",
                f"nessuna sessione portale per il proprietario di {label}: la "
                "superficie non e' stata interrogata",
            )
            continue
        response = http.request("GET", PORTAL_PROPERTIES, jar=portal_jars[label])
        report.check(
            f"OWNER_PORTAL-propria-{label}",
            response.status == 200 and cert.marker(label) in response.text(),
            f"il proprietario di {label} vede l'immobile concesso "
            f"({response.status}, {len(response.items())} elementi osservati)",
        )

    for label, other in (("A", "B"), ("B", "A")):
        if label not in portal_jars:
            continue
        # 1. La lista non deve contenere l'immobile dell'altra agenzia.
        response = http.request("GET", PORTAL_PROPERTIES, jar=portal_jars[label])
        if properties.get(other) is None and not response.items():
            report.blocked(
                f"OWNER_PORTAL-non-vede-{other}",
                f"la lista del proprietario di {label} e' vuota e questo run non "
                f"possiede un immobile concesso di {other}",
            )
        else:
            report.check(
                f"OWNER_PORTAL-non-vede-{other}",
                cert.marker(other) not in response.text(),
                f"il proprietario di {label} non vede l'immobile di {other} "
                f"({len(response.items())} elementi osservati)",
            )

        target = properties.get(other)
        if target is None:
            report.blocked(f"OWNER_PORTAL-detail-{label}-{other}",
                           f"nessun immobile di {other} concesso in questo run")
            continue
        # 2. L'ID diretto.
        response = http.request("GET", _fill(domain.detail, id=target),
                                jar=portal_jars[label])
        report.check(
            f"OWNER_PORTAL-detail-{label}-{other}",
            response.status in NEUTRAL_REFUSALS
            and cert.marker(other) not in response.text(),
            f"il proprietario di {label} chiede l'immobile {target} di {other} -> "
            f"{response.status}, senza vederne il contenuto",
        )
        # 3. I DOCUMENTI, CON IL LORO CONTRATTO.
        #
        # `portal_shared_documents` e' una SELECT filtrata per conto nella
        # JOIN: su un immobile non concesso risponde 200 con lista VUOTA - e
        # risponde cosi' anche per un id inesistente, quindi non e' un oracolo
        # di esistenza. Il run 9b95b3ee215e ha segnato quel 200 come FAIL
        # perche' la sonda pretendeva 403/404 dove il contratto dice "niente
        # da vedere". Un 200 e' accettabile SOLO vuoto e senza marcatore
        # altrui; ed e' una prova solo se il proprietario legittimo, sullo
        # stesso immobile, ne vede uno.
        documenti = context.get("portal_documents", {})
        mio = http.request("GET", _fill(domain.detail, id=properties.get(label)) + "/documents",
                           jar=portal_jars[label]) if properties.get(label) else None
        if documenti.get(label) is None or mio is None:
            report.blocked(f"OWNER_PORTAL-documenti-{label}",
                           "nessun documento pubblicato per questo run: la lista "
                           "vuota altrui non proverebbe nulla")
        else:
            report.check(
                f"OWNER_PORTAL-documenti-{label}",
                mio.status == 200 and cert.marker(label) in mio.text(),
                f"il proprietario di {label} vede il proprio documento "
                f"({mio.status}, {len(mio.items())} elementi)",
            )
            response = http.request(
                "GET", _fill(domain.detail, id=target) + "/documents", jar=portal_jars[label])
            vuota = response.status == 200 and response.items() == []
            report.check(
                f"OWNER_PORTAL-documenti-{label}-{other}",
                (response.status in NEUTRAL_REFUSALS or vuota)
                and cert.marker(other) not in response.text(),
                f"il proprietario di {label} chiede i documenti dell'immobile "
                f"{target} di {other} -> {response.status}"
                + (" con lista vuota" if vuota else ""),
            )
        # 3b. IL DOWNLOAD. `prepare_shared_document_download` apre lo storage.
        #
        #     FUORI DAL RAMO PRECEDENTE, E NON E' UN DETTAGLIO DI STILE. Stava
        #     dentro l'`else`: quando la lista non era provabile, questa riga
        #     non compariva affatto nel report - un dominio non provato che non
        #     si vedeva nemmeno. Adesso, se non c'e' nulla da scaricare, lo
        #     dice.
        #
        #     La prova ostile e' decisiva solo se quella positiva risponde 200;
        #     altrimenti e' BLOCKED, non PASS.
        scaricabili = context.get("portal_downloadable", {})
        proprio_doc = scaricabili.get(label)
        scarico = (http.request("GET",
                                f"/api/owner/portal/documents/{proprio_doc}/download",
                                jar=portal_jars[label])
                   if proprio_doc is not None else None)
        if proprio_doc is None or scarico.status != 200:
            report.blocked(
                f"OWNER_PORTAL-download-{label}",
                "nessun documento con oggetto nello storage per questo run"
                if proprio_doc is None else
                f"il proprietario legittimo riceve {scarico.status} sul proprio "
                f"documento {proprio_doc}: il rifiuto verso l'altro sarebbe ambiguo",
            )
        else:
            report.note(f"OWNER_PORTAL-download-{label}",
                        f"download del proprio documento {proprio_doc} -> 200")
            altrui = scaricabili.get(other)
            if altrui is not None:
                ostile = http.request(
                    "GET", f"/api/owner/portal/documents/{altrui}/download",
                    jar=portal_jars[label])
                report.check(
                    f"OWNER_PORTAL-download-{label}-{other}",
                    ostile.status in NEUTRAL_REFUSALS,
                    f"il proprietario di {label} scarica il documento {altrui} "
                    f"di {other} -> {ostile.status}",
                )

    # 4. IL GRANT INCOERENTE.
    #
    # Le tre prove sopra guardano la LETTURA. Questa guarda l'ORIGINE: un
    # titolare che concede al proprio proprietario un immobile dell'altra
    # agenzia costruirebbe una riga con due radici discordi - esattamente cio'
    # che il censimento delle incoerenze conta. Va rifiutato alla nascita.
    owner_jars = context.get("owner_jars", {})
    accounts = context.get("owner_accounts", {})
    foreign_properties = context.get("owned_properties", {})
    for label, other in (("A", "B"), ("B", "A")):
        account, foreign = accounts.get(label), foreign_properties.get(other)
        if label not in owner_jars or account is None or foreign is None:
            report.blocked(
                f"OWNER_PORTAL-grant-incoerente-{label}-{other}",
                "mancano un conto proprietario di questo run o un immobile "
                f"di {other}: la concessione incrociata non e' tentabile",
            )
            continue
        response = http.request("POST", OWNER_ACCESS, jar=owner_jars[label], payload={
            "owner_account_id": account,
            "property_id": foreign,
        })
        report.check(
            f"OWNER_PORTAL-grant-incoerente-{label}-{other}",
            response.status in NEUTRAL_REFUSALS,
            f"{label} tenta di concedere al proprio proprietario l'immobile "
            f"{foreign} di {other} -> {response.status}: una riga con due radici "
            "discordi non nasce",
        )


def certify(report, http, cert, operators, jars, owner_sessions=None,
            agencies=None, database=None, dedicated_agencies=False) -> None:
    """La matrice ostile, dominio per dominio, nelle due direzioni.

    TRE PRINCIPALI, NON UNO

    Le due identita' della matrice (`agency_admin`) coprono la gran parte delle
    superfici. Due non le raggiungono, e per ragioni opposte: OWNER Admin
    perche' chiede un ruolo piu' alto, il portale perche' autentica un
    proprietario e non un operatore. Interrogarle con l'identita' sbagliata
    produce un rifiuto che sembra isolamento e non lo e'.
    """
    a, b = operators["A"], operators["B"]

    # Tutto cio' che i certificatori dedicati si scambiano. Un dizionario e non
    # sei parametri: la lista dei principali e delle fixture derivate cresce, e
    # una firma che cresce con essa e' una firma che nessuno aggiorna.
    context = {
        "owner_jars": {}, "owner_accounts": {}, "owner_grants": {},
        "portal_jars": {}, "portal_properties": {}, "owned_properties": {},
        "stime": {}, "disclosures": [], "owner_links": {}, "portal_documents": {}, "portal_downloadable": {},
        "agencies": dict(agencies or {}),
        "dedicated_agencies": dedicated_agencies,
    }

    # -- le due sessioni sono vive e portano agenzie diverse -----------------
    for label, operator in (("A", a), ("B", b)):
        jar = jars[label]
        response = http.request("POST", LOGIN, jar=jar,
                                payload={"email": operator["email"],
                                         "password": operator["password"]})
        report.check(f"login-{label}", response.status == 204,
                     f"login dell'operatore {label} -> {response.status}")
        token = http.token_in(jar)
        if token:
            cert.secrets.append(token)
        me = http.request("GET", ME, jar=jar).json() or {}
        report.check(f"me-{label}",
                     me.get("agency_id") == operator["agency"]["id"],
                     f"/me per {label} riporta l'agenzia {operator['agency']['slug']}")

    report.check("identita-distinte",
                 a["agency"]["id"] != b["agency"]["id"],
                 "le due sessioni appartengono ad agenzie diverse: senza questo "
                 "ogni prova di isolamento sarebbe vacua")

    # -- fixture: ogni operatore crea le proprie ----------------------------
    owned: dict[str, dict[str, int]] = {"A": {}, "B": {}}
    # L'ordine conta: una fixture che dipende da un'altra deve trovarla gia'
    # creata. `DOMAINS` e' dichiarata nell'ordine della catena.
    for domain in DOMAINS:
        if not domain.fixture:
            continue
        path, template = domain.fixture
        for label in ("A", "B"):
            parent = owned[label].get(domain.depends_on) if domain.depends_on else None
            if domain.depends_on and parent is None:
                report.blocked(f"fixture-{domain.name}-{label}",
                               f"manca la fixture {domain.depends_on} da cui dipende")
                continue
            payload = json.loads(_fill(json.dumps(template),
                                       marker=cert.marker(label), core_id=parent))
            response = http.request("POST", path, jar=jars[label], payload=payload)
            if response.status not in (200, 201):
                report.blocked(
                    f"fixture-{domain.name}-{label}",
                    f"POST {path} -> {response.status}: il dominio non e' "
                    "provabile in scrittura in questo run",
                )
                continue
            created = response.json() or {}
            identifier = created.get("id")
            if identifier is None:
                report.blocked(f"fixture-{domain.name}-{label}",
                               "la risposta non porta un id")
                continue
            owned[label][domain.name] = identifier
            # Il cleanup via API si registra SOLO dove la DELETE esiste davvero.
            # Registrarla per CORE produceva 405 a ogni run - l'API sa creare un
            # contatto e non sa disfarlo - e il contatto restava sul TEST mentre
            # il report diceva soltanto "la fixture POTREBBE restare".
            if domain.detail and domain.api_delete:
                cert.fixtures.append(
                    (label, "DELETE", _fill(domain.detail, id=identifier)))
            # Tracciata SEMPRE, anche quando l'API dice di saperla cancellare:
            # e' l'unico modo di scoprire che non l'ha fatto.
            if domain.table:
                cert.created_rows.setdefault(domain.table, []).append(
                    (int(identifier), cert.marker(label), domain.marker_column))
            report.note(f"fixture-{domain.name}-{label}",
                        f"{domain.name}: risorsa di {label} creata (id {identifier})")

    context["owned_properties"] = {label: owned[label].get("PROPERTY")
                                   for label in ("A", "B")}

    # -- il legame proprietario: relazione lecita E precondizione della vendita
    link_property_owner(report, http, cert, jars, owned, context)

    # -- la catena commerciale: MATCH -> PROPOSAL -> SALE --------------------
    build_chain(report, http, cert, jars, owned)

    # -- il terzo e il quarto principale -------------------------------------
    #
    # Le sessioni di titolare esistono solo se gli owner reali sono stati
    # trovati. Se non lo sono, OWNER Admin e il portale restano BLOCKED: e' una
    # lacuna del TEST, non un isolamento provato.
    if owner_sessions is not None and agencies is not None:
        for label, agency in agencies.items():
            user_id = owner_sessions.find_owner(agency)
            if user_id is None:
                report.blocked(
                    f"owner-session-{label}",
                    f"nessun agency_owner attivo per {agency['slug']!r}: 027 ne "
                    "ammette uno solo e questo script non ne crea. OWNER Admin e "
                    "il portale non sono provabili su questa agenzia",
                )
                continue
            raw = owner_sessions.open(user_id)
            cert.secrets.append(raw)
            jar = http.new_jar()
            if not http.put_cookie(jar, raw):
                report.blocked(f"owner-session-{label}",
                               "il cookie della sessione di titolare non viene "
                               "emesso dal jar: ogni risposta sarebbe un 401")
                continue
            context["owner_jars"][label] = jar
            report.note(f"owner-session-{label}",
                        f"sessione {OWNER_ADMIN_MIN_ROLE} temporanea aperta per "
                        f"il titolare di {agency['slug']} (nessuna credenziale "
                        "letta o modificata)")

    build_owner_fixtures(report, http, cert, context["owner_jars"], owned, context, jars=jars)

    # LE STIME SI CREANO, E SOLO DOVE NON SI RIESCE SI DERIVANO.
    #
    # `derive_stime` legge cio' che l'agenzia gia' possiede: su TEST l'agenzia
    # B non possiede nulla, e da li' nascevano tre BLOCKED - la lettura
    # propria di B, il confronto ostile A/B e le due liste LEGACY_ADMIN vuote.
    # Una riga creata dal run e' migliore di una derivata anche quando la
    # derivata esiste: e' del run, si cancella per id, e porta il marcatore
    # che le prove di isolamento cercano. La derivazione resta come ripiego
    # per l'agenzia in cui la creazione non riesce.
    proprie = build_shared_watch_fixtures(report, http, cert, jars, agencies or {})
    if database is not None:
        derivate = derive_stime(database, agencies or {})
        context["stime"] = {**derivate, **proprie}
    else:
        context["stime"] = dict(proprie)

    # -- la matrice, dominio per dominio ------------------------------------
    for domain in DOMAINS:
        if domain.certifier:
            certifier = globals()[f"certify_{domain.certifier}"]
            certifier(report, http, cert, domain, jars, owned, context)
            continue
        certify_generic(report, http, cert, domain, jars, owned)

    # Restituito perche' il `finally` di `run` deve poter chiudere le sessioni
    # del portale: sono l'unica cosa creata qui che non vive ne' in `cert` ne'
    # in `jars`.
    return context


def certify_flow(report, http, cert, domain, jars, owned, context) -> None:
    """FLOW: un'esecuzione per agenzia condivisa, riconosciuta per ID.

    LA RIGA CHE QUESTA FUNZIONE CHIUDE

    `FLOW-list-B-non-vede-A`, BLOCKED in ogni run fino a `42e32975ccd6`. Il
    motivo scritto nel report era esatto: la matrice creava EVENTI e mai
    esecuzioni, quindi la lista di entrambe le agenzie era vuota e il
    confronto non provava niente. La causa vera stava un passo prima: un
    evento nasce con `event_type` qualunque, ma un'ESECUZIONE nasce solo se
    quell'`event_type` e quell'`entity_type` corrispondono a una regola
    ATTIVA, e il marcatore del run - che la fixture metteva nel tipo - impedisce
    per costruzione ogni corrispondenza.

    PERCHE' NON SI DELEGA A `certify_generic`

    Le sei domande generiche riconoscono una risorsa cercando
    `cert.marker(label)` nel corpo della lista. Un'esecuzione non porta testo
    scelto da chi la crea: `rule_code`, `entity_type`, `entity_id`, `status`,
    gli snapshot dei parametri. `marker(other) not in body` sarebbe vero
    SEMPRE, anche se la lista di B contenesse per intero le esecuzioni di A -
    cioe' un PASS che non puo' fallire, che e' la definizione di prova vacua.
    Qui le stesse domande si fanno con gli id, che l'esecuzione ha e che sono
    un riconoscimento piu' severo di una sottostringa.

    COSA PROVA, E COSA NO

    Prova che `/api/flow/executions` e `/api/flow/executions/{id}` rispondono
    solo per l'agenzia della sessione, su righe vere, create dal run, con
    l'appartenenza riletta dal database. NON prova nulla sul ramo in cui la
    regola corrisponde - `flow_action_records` e il task che ne seguono - che
    resta esercitato solo dai test offline. La ragione della scelta sta accanto
    a `FLOW_EXECUTION_TRIGGER`: il ramo corrispondente scriverebbe in
    un'agenzia condivisa due righe che nessun `DELETE ... WHERE agency_id`
    rimuove.
    """
    agenzie = context.get("agencies") or {}
    eventi: dict[str, int] = {}
    esecuzioni: dict[str, int] = {}

    # 1-2: la lista risponde, e un 5xx non e' un difetto di isolamento.
    for label in ("A", "B"):
        risposta = http.request("GET", domain.listing, jar=jars[label])
        dettaglio = ""
        if risposta.status >= 500:
            dettaglio = (f" [la route e' ROTTA, non isolata male: "
                         f"{_error_shape(risposta.text())}]")
        report.check(f"FLOW-list-{label}", risposta.status == 200,
                     f"{label} legge {domain.listing.split('?')[0]} -> "
                     f"{risposta.status}{dettaglio}")

    for label in ("A", "B"):
        richiesta = owned[label].get("BUY")
        if richiesta is None:
            report.blocked(
                f"FLOW-fixture-{label}",
                "manca la richiesta d'acquisto di questo run: un evento su "
                "un'entita' non nostra farebbe valutare una regola su una riga "
                "altrui, che e' esattamente cio' che la matrice non fa",
            )
            continue

        # LA PRECONDIZIONE SI LEGGE, NON SI SUPPONE.
        #
        # La fixture BUY non valorizza `next_action_at`, quindi R004 non
        # corrisponde e l'esecuzione si chiude `not_matched` senza altri
        # effetti. E' un'affermazione su un'ALTRA parte di questo file, che
        # qualcuno potrebbe cambiare per una ragione che non c'entra: qui la
        # si verifica sul dato reale, e se non regge non si invia l'evento.
        try:
            with cert.db.read() as cur:
                cur.execute(
                    "SELECT status, next_action_at FROM buy_requests WHERE id = %s",
                    (richiesta,))
                riga = cur.fetchone()
        except Exception as exc:                       # pragma: no cover - difensivo
            report.blocked(f"FLOW-fixture-{label}",
                           f"richiesta {richiesta} non leggibile ({type(exc).__name__})")
            continue
        if riga is None:
            report.blocked(f"FLOW-fixture-{label}",
                           f"la richiesta {richiesta} non e' sul database")
            continue
        if riga["next_action_at"] is not None:
            report.blocked(
                f"FLOW-fixture-{label}",
                f"la richiesta {richiesta} ha gia' una prossima azione: "
                f"{cert.FLOW_EXECUTION_RULE} corrisponderebbe e creerebbe un "
                "record d'azione e un task in un'agenzia CONDIVISA, dove nulla "
                "si cancella per agenzia. Nessun evento inviato.",
            )
            continue

        chiave = f"{cert.marker(label)}-esecuzione"
        risposta = http.request(
            "POST", "/api/flow/events", jar=jars[label],
            payload={"event_type": cert.FLOW_EXECUTION_TRIGGER,
                     "entity_type": cert.FLOW_EXECUTION_ENTITY,
                     "entity_id": richiesta,
                     "source_module": cert.FLOW_EXECUTION_SOURCE_MODULE,
                     "payload": {},
                     "deduplication_key": chiave})

        report.check(f"FLOW-post-{label}", risposta.status in (200, 201),
                     f"POST /api/flow/events per {label} -> {risposta.status}"
                     + _corpo(risposta))

        # L'ORIGINE CANONICA E' IL DATABASE, E SI LEGGE ANCHE SE L'HTTP HA
        # FALLITO.
        #
        # `process_event` salva l'evento PRIMA di valutare le regole: una
        # risposta 500 non significa "niente e' stato scritto". La chiave di
        # deduplicazione e' nostra - porta il marcatore del run - quindi la
        # riga si ritrova comunque, e tracciarla e' cio' che la rende
        # cancellabile. Il codice di stato si giudica dopo.
        try:
            with cert.db.read() as cur:
                cur.execute(
                    "SELECT id, agency_id FROM flow_events "
                    " WHERE deduplication_key = %s ORDER BY id", (chiave,))
                righe_evento = [dict(r) for r in cur.fetchall()]
        except Exception as exc:                       # pragma: no cover - difensivo
            report.blocked(f"FLOW-fixture-{label}",
                           f"evento non rileggibile ({type(exc).__name__})")
            continue
        for riga_evento in righe_evento:
            # DUE REGISTRI, DUE MESTIERI. `created_effects` e' il perimetro e
            # la verifica finale; `shared_flow_event_ids` e' l'elenco di cio'
            # che si cancella PER ID, che nelle agenzie dedicate sarebbe
            # sbagliato - la' se ne va con l'agenzia, ed e' quella
            # cancellazione che i test devono poter vedere fallire.
            cert.created_effects.setdefault(
                "flow_events", []).append(int(riga_evento["id"]))
            cert.shared_flow_event_ids.append(int(riga_evento["id"]))

        if len(righe_evento) != 1:
            report.blocked(
                f"FLOW-fixture-{label}",
                f"POST /api/flow/events -> {risposta.status}{_corpo(risposta)}: "
                f"{len(righe_evento)} eventi con la chiave di questo run invece "
                "di uno",
            )
            continue
        evento = int(righe_evento[0]["id"])
        eventi[label] = evento

        try:
            with cert.db.read() as cur:
                cur.execute(
                    "SELECT e.id, e.agency_id, e.status, r.code AS rule_code "
                    "  FROM flow_executions e "
                    "  JOIN flow_rules r ON r.id = e.rule_id "
                    " WHERE e.event_id = %s ORDER BY e.id", (evento,))
                righe_esecuzione = [dict(r) for r in cur.fetchall()]
        except Exception as exc:                       # pragma: no cover - difensivo
            report.blocked(f"FLOW-fixture-{label}",
                           f"esecuzioni non rileggibili ({type(exc).__name__})")
            continue
        for riga_esecuzione in righe_esecuzione:
            cert.created_effects.setdefault(
                "flow_executions", []).append(int(riga_esecuzione["id"]))
            cert.shared_flow_execution_ids.append(int(riga_esecuzione["id"]))

        if len(righe_esecuzione) != 1:
            report.blocked(
                f"FLOW-fixture-{label}",
                f"POST /api/flow/events -> {risposta.status}{_corpo(risposta)}: "
                f"{len(righe_esecuzione)} esecuzioni invece di una. Una sola "
                f"regola attiva ha trigger {cert.FLOW_EXECUTION_TRIGGER} su "
                f"{cert.FLOW_EXECUTION_ENTITY}: un numero diverso e' una "
                "configurazione di TEST diversa da quella censita",
            )
            continue
        esecuzione = righe_esecuzione[0]
        esecuzioni[label] = int(esecuzione["id"])
        report.note(f"FLOW-fixture-{label}",
                    f"esecuzione {esecuzione['id']} da {esecuzione['rule_code']} "
                    f"su {cert.FLOW_EXECUTION_ENTITY} {richiesta}, "
                    f"stato {esecuzione['status']}")

        atteso = agenzie.get(label, {}).get("id")
        report.check(f"FLOW-appartenenza-{label}",
                     atteso is not None and esecuzione["agency_id"] == atteso,
                     f"l'esecuzione {esecuzione['id']} di {label} porta "
                     f"l'agenzia {esecuzione['agency_id']}, attesa {atteso}")
        report.check(f"FLOW-stato-{label}",
                     esecuzione["status"] == "not_matched",
                     f"esecuzione {esecuzione['id']}: stato {esecuzione['status']}, "
                     "atteso not_matched")
        report.check(f"FLOW-regola-{label}",
                     esecuzione["rule_code"] == cert.FLOW_EXECUTION_RULE,
                     f"ha risposto {esecuzione['rule_code']}, atteso "
                     f"{cert.FLOW_EXECUTION_RULE}")

    _flow_effetti_imprevisti(report, cert, esecuzioni)

    # 3-4: ciascuno vede la propria esecuzione e non quella dell'altro.
    for label, altro in (("A", "B"), ("B", "A")):
        if len(esecuzioni) < 2:
            report.blocked(
                f"FLOW-list-{label}-non-vede-{altro}",
                "manca un'esecuzione per agenzia: una lista vuota, o con una "
                "sola riga, non e' una prova di isolamento",
            )
            continue
        risposta = http.request("GET", domain.listing, jar=jars[label])
        report.check(f"FLOW-list-fixture-{label}", risposta.status == 200,
                     f"lista con fixture di {label} -> {risposta.status}")
        visti = {int(v["id"]) for v in risposta.items() if v.get("id") is not None}
        report.check(f"FLOW-list-{label}-vede-la-propria",
                     esecuzioni[label] in visti,
                     f"la lista di {label} contiene l'esecuzione "
                     f"{esecuzioni[label]} che {label} ha fatto nascere")
        report.check(f"FLOW-list-{label}-non-vede-{altro}",
                     esecuzioni[altro] not in visti,
                     f"la lista di {label} NON contiene l'esecuzione "
                     f"{esecuzioni[altro]} di {altro} ({len(visti)} righe osservate)")

        proprio = http.request("GET", f"/api/flow/executions/{esecuzioni[label]}",
                               jar=jars[label])
        report.check(f"FLOW-dettaglio-proprio-{label}", proprio.status == 200,
                     f"{label} legge la propria esecuzione {esecuzioni[label]} "
                     f"-> {proprio.status}")

        # 5: l'id diretto dell'altra agenzia.
        diretto = http.request("GET", f"/api/flow/executions/{esecuzioni[altro]}",
                               jar=jars[label])
        report.check(f"FLOW-dettaglio-{label}-{altro}",
                     diretto.status in NEUTRAL_REFUSALS,
                     f"{label} chiede l'esecuzione {esecuzioni[altro]} di {altro} "
                     f"-> {diretto.status}, mentre {altro} sulla stessa riga la legge")

        # 6: la scrittura. Un retry riuscito RIESEGUE l'automazione di un altro
        # tenant, ed e' la sonda ostile che conta davvero su questa superficie.
        scrittura = http.request(
            "POST", f"/api/flow/executions/{esecuzioni[altro]}/retry",
            jar=jars[label], payload={})
        report.check(f"FLOW-retry-ostile-{label}-{altro}",
                     scrittura.status in NEUTRAL_REFUSALS,
                     f"{label} tenta il retry dell'esecuzione {esecuzioni[altro]} "
                     f"di {altro} -> {scrittura.status}")

    # Un retry riuscito avrebbe creato un'esecuzione nuova. La si cerca
    # comunque - per la colonna che la legherebbe alla nostra, non per il
    # codice di stato - cosi' che una riga scritta da una sonda respinta solo
    # in apparenza sia tracciata e non resti sul TEST.
    if esecuzioni:
        try:
            with cert.db.read() as cur:
                cur.execute(
                    "SELECT id FROM flow_executions "
                    " WHERE retry_of_execution_id IN %s ORDER BY id",
                    (tuple(sorted(esecuzioni.values())),))
                nate = [int(r["id"]) for r in cur.fetchall()]
        except Exception as exc:                       # pragma: no cover - difensivo
            report.fail("FLOW-retry-effetti",
                        f"esecuzioni derivate non leggibili ({type(exc).__name__}): "
                        "una riga nata da un retry potrebbe essere rimasta")
            return
        for identificativo in nate:
            cert.created_effects.setdefault(
                "flow_executions", []).append(identificativo)
            cert.shared_flow_execution_ids.append(identificativo)
        if nate:
            report.fail("FLOW-retry-effetti",
                        f"{len(nate)} esecuzioni nate da un retry: tracciate per "
                        "id, ma la sonda ostile doveva essere respinta")


def _flow_effetti_imprevisti(report, cert, esecuzioni: dict) -> None:
    """Il ramo corrispondente non doveva accadere: se e' accaduto, si nomina.

    `not_matched` non scrive ne' record d'azione ne' task. Se ce ne fossero,
    la precondizione letta poco sopra si sarebbe rivelata insufficiente: le
    righe stanno in tabelle che, in un'agenzia CONDIVISA, questo cleanup non
    percorre. Tracciarle non le cancella - `tasks` e `flow_action_records` non
    sono in `EFFECT_BY_ID_TABLES` - ma le fa comparire nella verifica finale
    per id, che e' la differenza fra un residuo segnalato e uno silenzioso.
    """
    if not esecuzioni:
        return
    ids = tuple(sorted(esecuzioni.values()))
    try:
        with cert.db.read() as cur:
            cur.execute("SELECT id FROM flow_action_records "
                        " WHERE execution_id IN %s ORDER BY id", (ids,))
            record = [int(r["id"]) for r in cur.fetchall()]
            cur.execute("SELECT id FROM tasks "
                        " WHERE (metadata ->> 'flow_execution_id')::bigint IN %s "
                        " ORDER BY id", (ids,))
            compiti = [int(r["id"]) for r in cur.fetchall()]
    except Exception as exc:                           # pragma: no cover - difensivo
        report.fail("FLOW-effetti",
                    f"effetti dell'esecuzione non leggibili ({type(exc).__name__}): "
                    "non si puo' affermare che non ne siano nati")
        return
    for identificativo in record:
        cert.created_effects.setdefault(
            "flow_action_records", []).append(identificativo)
    for identificativo in compiti:
        cert.created_effects.setdefault("tasks", []).append(identificativo)
    report.check(
        "FLOW-effetti", not record and not compiti,
        f"l'esecuzione non corrispondente non ha scritto altro "
        f"(record d'azione {len(record)}, task {len(compiti)})"
        + ("" if not (record or compiti) else
           ": righe TRACCIATE per id ma NON cancellabili in un'agenzia "
           "condivisa, vanno rimosse a mano"),
    )


def certify_generic(report, http, cert, domain, jars, owned) -> None:
    """Le sei domande, piu' i modi obliqui, su un dominio."""
    name = domain.name

    # 1-2: ciascuno vede la propria lista, e ci trova la propria fixture.
    if domain.listing:
        for label in ("A", "B"):
            response = http.request("GET", domain.listing, jar=jars[label])
            # UN 5xx NON E' UN FALLIMENTO DI ISOLAMENTO, ed e' inutile
            # riportarlo come se lo fosse. Il run 38e341f68f8a ha chiuso
            # LEGACY_ADMIN con `-> 500` e nient'altro: il run successivo
            # avrebbe rifatto la stessa domanda e ottenuto la stessa riga.
            # Qui il corpo viene ridotto alla sua FORMA - tipo di eccezione,
            # SQLSTATE, nome del vincolo, mai il messaggio grezzo - cosi' che
            # la diagnosi parta da qualcosa.
            dettaglio = ""
            if response.status >= 500:
                dettaglio = (f" [la route e' ROTTA, non isolata male: "
                             f"{_error_shape(response.text())}]")
            report.check(f"{name}-list-{label}", response.status == 200,
                         f"{label} legge {domain.listing.split('?')[0]} -> "
                         f"{response.status}{dettaglio}")

    # 3-4: nessuna lista mostra il marcatore dell'altra agenzia.
    #
    # UNA LISTA VUOTA NON PROVA NULLA, e questa e' la trappola piu' facile in
    # cui puo' cadere una matrice ostile. "Il marcatore di B non compare nella
    # lista di A" e' vero anche quando la lista di A e' vuota, e su un database
    # TEST poco popolato lo e' spesso: il dominio comparirebbe fra i PASS senza
    # che una sola riga sia stata confrontata.
    #
    # Il confronto vale in due casi, e in nessun altro:
    #
    #   * il run POSSIEDE una risorsa in questo dominio - allora si esige che
    #     la propria ci sia e quella altrui no, che e' la prova forte;
    #   * la lista non e' vuota - allora l'assenza del marcatore estraneo e' un
    #     fatto osservato su dati reali, anche se non creati da noi.
    #
    # Altrimenti: BLOCKED, col motivo scritto. Un dominio non provato deve
    # apparire come non provato.
    if domain.listing:
        for label, other in (("A", "B"), ("B", "A")):
            response = http.request("GET", domain.listing, jar=jars[label])
            body = response.text()
            mine = owned[label].get(name)
            items = response.items()

            if mine is not None:
                report.check(
                    f"{name}-list-{label}-vede-la-propria",
                    cert.marker(label) in body,
                    f"la lista di {label} contiene la risorsa che {label} ha creato",
                )
            elif not items:
                report.blocked(
                    f"{name}-list-{label}-non-vede-{other}",
                    f"la lista di {label} e' vuota e questo run non possiede una "
                    f"risorsa {name}: il confronto col marcatore non proverebbe "
                    "nulla",
                )
                continue

            report.check(
                f"{name}-list-{label}-non-vede-{other}",
                cert.marker(other) not in body,
                f"la lista di {label} NON contiene il marcatore di {other} "
                f"({len(items)} elementi osservati)",
            )

    # 5: la ricerca libera non e' una porta di servizio.
    if domain.search:
        for label, other in (("A", "B"), ("B", "A")):
            path = _fill(domain.search, marker=cert.marker(other))
            response = http.request("GET", path, jar=jars[label])
            report.check(
                f"{name}-search-{label}-non-trova-{other}",
                response.status == 200 and cert.marker(other) not in response.text(),
                f"{label} cerca il marcatore di {other} e non lo trova "
                f"({response.status})",
            )

    # 6: l'ID diretto dell'altra agenzia.
    if domain.detail:
        # Da quale dominio viene l'id da rubare: il proprio se questo run ne
        # possiede una fixture, altrimenti quello da cui dipende. BUY ha
        # entrambe le cose - dipende da CORE per NASCERE, ma poi ha un id suo -
        # e confondere i due farebbe provare l'isolamento del contatto invece
        # che quello della richiesta d'acquisto.
        source = name if (domain.fixture or domain.chain) else (domain.depends_on or name)
        for label, other in (("A", "B"), ("B", "A")):
            foreign = owned[other].get(source)
            if foreign is None:
                report.blocked(f"{name}-detail-{label}-{other}",
                               "nessuna fixture dell'altra agenzia in questo run")
                continue
            response = http.request("GET", _fill(domain.detail, id=foreign),
                                    jar=jars[label])
            report.check(
                f"{name}-detail-{label}-{other}",
                response.status in NEUTRAL_REFUSALS,
                f"{label} chiede l'id {foreign} di {other} -> {response.status} "
                f"(atteso uno di {NEUTRAL_REFUSALS})",
            )
            # E il rifiuto non deve confermare che la risorsa esiste.
            report.check(
                f"{name}-detail-{label}-{other}-neutro",
                cert.marker(other) not in response.text(),
                "il rifiuto non rivela il contenuto della risorsa estranea",
            )

    # 7: la scrittura sull'ID dell'altra agenzia.
    if domain.update:
        method, template, payload_template = domain.update
        # Da quale dominio viene l'id da rubare: il proprio se questo run ne
        # possiede una fixture, altrimenti quello da cui dipende. BUY ha
        # entrambe le cose - dipende da CORE per NASCERE, ma poi ha un id suo -
        # e confondere i due farebbe provare l'isolamento del contatto invece
        # che quello della richiesta d'acquisto.
        source = name if (domain.fixture or domain.chain) else (domain.depends_on or name)
        for label, other in (("A", "B"), ("B", "A")):
            foreign = owned[other].get(source)
            if foreign is None:
                report.blocked(f"{name}-write-{label}-{other}",
                               "nessuna fixture dell'altra agenzia in questo run")
                continue
            payload = json.loads(_fill(json.dumps(payload_template),
                                       marker=cert.marker(label)))
            response = http.request(method, _fill(template, id=foreign),
                                    jar=jars[label], payload=payload)
            report.check(
                f"{name}-write-{label}-{other}",
                response.status in NEUTRAL_REFUSALS,
                f"{label} tenta {method} sull'id {foreign} di {other} -> "
                f"{response.status} (atteso uno di {NEUTRAL_REFUSALS})",
            )
            # E la risorsa dell'altro deve essere intatta: un rifiuto che
            # avesse comunque scritto sarebbe il peggiore dei casi.
            check = http.request("GET", _fill(domain.detail, id=foreign),
                                 jar=jars[other])
            report.check(
                f"{name}-write-{label}-{other}-intatta",
                check.status == 200 and cert.marker(label) not in check.text(),
                f"la risorsa di {other} non porta traccia del tentativo di {label}",
            )

    # 8: i percorsi che attraversano una relazione.
    for template in domain.cross_links:
        # Da quale dominio viene l'id da rubare: il proprio se questo run ne
        # possiede una fixture, altrimenti quello da cui dipende. BUY ha
        # entrambe le cose - dipende da CORE per NASCERE, ma poi ha un id suo -
        # e confondere i due farebbe provare l'isolamento del contatto invece
        # che quello della richiesta d'acquisto.
        source = name if (domain.fixture or domain.chain) else (domain.depends_on or name)
        for label, other in (("A", "B"), ("B", "A")):
            foreign = owned[other].get(source)
            if foreign is None:
                continue
            response = http.request("GET", _fill(template, id=foreign), jar=jars[label])
            report.check(
                f"{name}-crosslink-{label}-{other}",
                response.status in NEUTRAL_REFUSALS
                or (response.status == 200 and cert.marker(other) not in response.text()),
                f"{label} segue una relazione verso {other} -> {response.status} "
                "senza vederne i dati",
            )


def scan_for_leaks(report: Report, http: HttpProbe, secrets_seen: list[str],
                   expected: tuple = ()) -> None:
    """Nessuna risposta deve contenere una password, un token o un cookie.

    UN'ECCEZIONE, E UNA SOLA

    `POST /api/owner/admin/accounts/{id}/tokens` restituisce il token in
    chiaro: e' il suo contratto - `one_time_display` - ed e' l'unico modo di
    consegnarlo a chi lo ha richiesto. Toglierlo dai segreti sorvegliati
    sarebbe la scorciatoia sbagliata: quel token resterebbe poi invisibile
    ovunque altro comparisse. Viene invece esentata LA SINGOLA risposta che lo
    ha emesso, e nient'altro.

    `expected` e' l'elenco di quelle coppie (percorso, segreto). Una fuga dello
    stesso token in una qualunque altra risposta resta una fuga.
    """
    permitted = set(expected)
    leaks = [
        f"{label} ({index})"
        for index, (label, _status, body) in enumerate(http.exchanges)
        for secret in secrets_seen
        if secret and secret.encode("utf-8") in body
        and (label, secret) not in permitted
    ]
    report.check("leak", not leaks,
                 f"nessuna password o token in {len(http.exchanges)} risposte "
                 f"{leaks or ''}")


# ---------------------------------------------------------------------------
# Orchestrazione
# ---------------------------------------------------------------------------

def run(report: Report, database: Database, env: dict, approved_commit: str,
        http_factory=HttpProbe, dedicated_agencies: bool = False) -> int:
    """Esegue la matrice e ritorna l'exit code."""
    try:
        base = preflight(report, database, env, approved_commit)
    except (CheckFailed, GuardFailure) as exc:
        if isinstance(exc, GuardFailure):
            report.fail("0.1", str(exc))
        report.summary()
        return report.exit_code

    http = http_factory(base)
    try:
        response = http.request("GET", PUBLIC)
        report.check("0.10", response.status == 200,
                     f"app raggiungibile: GET {PUBLIC} -> {response.status}")
        agency_a, agency_b = read_agencies(report, database)
    except CheckFailed:
        report.summary()
        return report.exit_code

    cert = Certification(database, report)

    # Residui di un run precedente: si ferma qui. Da questo processo non si
    # distingue il resto di ieri da una certificazione avviata adesso da
    # un'altra shell, e le due chiedono risposte opposte.
    leftovers = cert.leftovers()
    if leftovers:
        report.fail(
            "0.11",
            f"{leftovers} identita' di certificazione gia' presenti. Potrebbero "
            "essere il residuo di un run non completato oppure una "
            "certificazione in corso: nessuna identita' creata e nessuna riga "
            "cancellata. Verificare a mano prima di rieseguire.",
        )
        report.summary()
        return report.exit_code
    report.note("0.11", "nessun residuo di certificazioni precedenti")

    incoherence_census(report, database)

    jars = {"A": http.new_jar(), "B": http.new_jar()}
    agencies = {"A": agency_a, "B": agency_b}
    owner_sessions = OwnerSessions(database, report)
    context: dict = {}
    try:
        operators = {
            "A": cert.create_operator(agency_a),
            "B": cert.create_operator(agency_b),
        }
        report.note("0.12", f"due identita' create, ruolo {CERT_ROLE}, "
                            f"run {cert.run_id}")
        context = certify(report, http, cert, operators, jars,
                          owner_sessions=owner_sessions, agencies=agencies,
                          database=database,
                          dedicated_agencies=dedicated_agencies) or {}
        scan_for_leaks(report, http, cert.secrets,
                       tuple(context.get("disclosures", ())))
    except CheckFailed:
        pass
    except Exception as exc:                       # pragma: no cover - difensivo
        report.fail("RUN", f"eccezione non gestita: {type(exc).__name__}")
    finally:
        # L'ORDINE E' QUELLO DELLE CHIAVI ESTERNE, E NON E' NEGOZIABILE.
        #
        # `matches` e `property_sales` referenziano richieste e immobili;
        # `owner_accounts.contact_id` e' ON DELETE RESTRICT sul contatto. Se le
        # fixture HTTP se ne andassero per prime, ogni DELETE fallirebbe e il
        # run riporterebbe un cleanup incompleto - un FAIL vero, per una ragione
        # che non ha nulla a che vedere con l'isolamento.
        # PRIMA DI OGNI CANCELLAZIONE, comprese quelle via API: gli id dei
        # genitori le cui figlie andranno verificate dopo. Presa piu' tardi,
        # l'istantanea fotograferebbe un database gia' potato.
        # PRIMA dell'istantanea e di ogni cancellazione: token e sessioni si
        # leggono finche' il conto esiste. Dopo `cleanup_owner_fixtures` la
        # stessa query risponderebbe zero, e il report direbbe il falso.
        cert.registra_identita_owner()
        cert.snapshot_before_cleanup()
        # La guardia PRIMA della prima DELETE. Vendite, proposte e conti
        # proprietario se ne vanno nelle due chiamate qui sotto: chiedersi
        # dopo se qualcosa li referenziava sarebbe chiederselo quando non
        # esistono piu'.
        cert.preflight_dependencies()
        cert.cleanup_chain_fixtures()
        cert.cleanup_owner_fixtures()
        # Le righe create nelle agenzie CONDIVISE: si cancellano per id,
        # perche' li' non esiste - e non deve esistere - una cancellazione per
        # agenzia. Prima di `cleanup_orphan_fixtures`, che tocca i contatti e
        # gli immobili da cui quelle righe non dipendono, ma dopo il conto
        # proprietario, per tenere i passi nell'ordine in cui il report li
        # legge.
        cert.cleanup_shared_watch_fixtures()
        # Evento ed esecuzione FLOW, per la stessa ragione e con lo stesso
        # criterio. Prima di `cleanup_orphan_fixtures`: la richiesta d'acquisto
        # che l'esecuzione NOMINA se ne va di la', e benche' `entity_id` non
        # sia una chiave esterna - il catalogo non lo vedrebbe - lasciare
        # l'esecuzione dopo la sparizione della sua entita' significherebbe
        # tenere sul TEST una riga che punta a un id che non esiste piu'.
        cert.cleanup_shared_flow_fixtures()
        cert.cleanup_http_fixtures(http, jars)
        # I contatti per ULTIMI fra le fixture di dominio: `buy_requests` e
        # `property_contacts` li referenziano con RESTRICT, quindi finche' le
        # righe di sopra esistono il contatto non e' cancellabile.
        # Il bucket PRIMA delle righe: la chiave si legge da property_documents,
        # e cancellata quella riga la chiave non e' piu' recuperabile.
        cert.cleanup_storage_objects()
        cert.cleanup_orphan_fixtures(agencies)
        # Le agenzie dedicate PRIMA della verifica, e non e' una preferenza:
        # `flow_events` e `stime` sono effetti del run che stanno dentro
        # quelle agenzie, e verificarli prima li conterebbe tutti.
        # `verify_no_residue` non si fida dell'ordine scritto qui: se la
        # chiamata sotto finisse sopra, se ne accorge e fallisce dicendolo.
        cert.cleanup_dedicated_agencies()
        # Per ultima, e indipendente da come si e' cancellato: l'unica prova
        # che il TEST sia tornato com'era.
        cert.verify_no_residue()
        for jar in context.get("portal_jars", {}).values():
            http.request("POST", PORTAL_LOGOUT, jar=jar)
        for label, jar in jars.items():
            if http.token_in(jar):
                http.request("POST", LOGOUT, jar=jar)
        # Le sessioni di titolare per ultime fra le identita': finche' esistono,
        # i cleanup sopra possono ancora chiamare le route di OWNER Admin.
        owner_sessions.cleanup()
        cert.cleanup_database()

    report.summary()
    return report.exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Matrice ostile A/B live P26-6, su Render TEST."
    )
    parser.add_argument("--approved-commit", required=True,
                        help="il commit rivisto, confrontato con git rev-parse HEAD")
    parser.add_argument(
        "--with-dedicated-agencies", action="store_true",
        help=(
            "esegue la prova FOLLOWUP su due agenzie TEST temporanee. "
            "ATTESTA che l'operatore ha verificato e sospeso i processi "
            "platform-wide: lo script NON lo rileva e non puo' rilevarlo"),
    )
    arguments = parser.parse_args(argv)

    report = Report()
    print("=" * 78)
    print("P26-6 MATRICE OSTILE A/B - Render TEST")
    print("=" * 78)
    try:
        database = Database.connect()
    except Exception as exc:
        report.fail("0.0", f"connessione al database non riuscita ({type(exc).__name__})")
        report.summary()
        return report.exit_code
    return run(report, database, dict(os.environ), arguments.approved_commit,
               dedicated_agencies=arguments.with_dedicated_agencies)


if __name__ == "__main__":                          # pragma: no cover
    raise SystemExit(main())
