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
import secrets
import ssl
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path

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
                 principal="operator", certifier=None, note=""):
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
        cross_links=("/api/property/properties/{id}/visits?limit=20",),
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
    ),
    Domain("FOLLOWUP", "/api/followup", listing="/api/followup/pending?limit=50"),
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
    Domain("NEXT_BEST_ACTION", "/api/next-best-action", listing="/api/next-best-action?limit=50"),
    Domain("FLOW", "/api/flow", listing="/api/flow/executions?limit=50"),
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
        for agency, method, path in reversed(self.fixtures):
            jar = jars.get(agency)
            if jar is None:
                self.report.fail("CLEAN-FIXTURE",
                                 f"nessuna sessione per {agency}: {path} resta")
                continue
            response = http.request(method, path, jar=jar)
            if response.status in (200, 204, 404):
                continue
            self.report.fail(
                "CLEAN-FIXTURE",
                f"{method} {path} -> {response.status}: la fixture POTREBBE restare",
            )

    def _delete_scoped(self, label: str, statements: tuple, residue: tuple) -> None:
        """Cancella per ID e verifica che non resti nulla.

        `statements` e' una sequenza di `(sql, ids)` in ordine di dipendenza;
        `residue` la stessa cosa in forma di conteggio. Nessuno dei due accetta
        un LIKE o un prefisso: il criterio e' sempre e solo l'elenco degli id
        che questo run ha creato.
        """
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

    def cleanup_owner_fixtures(self) -> None:
        """Conti proprietario, concessioni, token e sessioni del portale.

        Anche questo prima delle fixture HTTP: `owner_accounts.contact_id` e'
        ON DELETE RESTRICT, quindi finche' il conto esiste il contatto non si
        cancella.
        """
        ids = self.created_owner_account_ids
        self._delete_scoped(
            "CLEAN-OWNER",
            (
                ("DELETE FROM owner_sessions WHERE owner_account_id IN %s", ids),
                ("DELETE FROM owner_access_tokens WHERE owner_account_id IN %s", ids),
                ("DELETE FROM owner_publication_reads WHERE owner_account_id IN %s", ids),
                ("DELETE FROM owner_feedback WHERE owner_account_id IN %s", ids),
                ("DELETE FROM owner_property_access WHERE owner_account_id IN %s", ids),
                # L'audit log referenzia il conto con ON DELETE SET NULL: la
                # traccia di cio' che e' successo resta, ed e' giusto che resti.
                ("DELETE FROM owner_accounts WHERE id IN %s", ids),
            ),
            (
                ("SELECT COUNT(*) AS n FROM owner_accounts WHERE id IN %s", ids),
                ("SELECT COUNT(*) AS n FROM owner_property_access "
                 "WHERE owner_account_id IN %s", ids),
                ("SELECT COUNT(*) AS n FROM owner_sessions WHERE owner_account_id IN %s", ids),
            ),
        )

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
        ("audit OWNER senza entrambe le radici", """
            SELECT COUNT(*) AS n FROM owner_audit_log
             WHERE owner_account_id IS NULL AND property_id IS NULL
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
    if total == 0:
        report.note("CENSUS", "nessuna incoerenza rilevata dal censimento")


# ---------------------------------------------------------------------------
# La matrice
# ---------------------------------------------------------------------------

def _fill(template: str, **values) -> str:
    for key, value in values.items():
        template = template.replace("{" + key + "}", str(value))
    return template


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


def build_owner_fixtures(report, http, cert, owner_jars, owned, context) -> None:
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
        # 3. I documenti: la superficie da cui esce un file, quindi quella dove
        #    una fuga non sarebbe un dato ma un allegato.
        response = http.request(
            "GET", _fill(domain.detail, id=target) + "/documents", jar=portal_jars[label])
        report.check(
            f"OWNER_PORTAL-documenti-{label}-{other}",
            response.status in NEUTRAL_REFUSALS,
            f"il proprietario di {label} chiede i documenti dell'immobile "
            f"{target} di {other} -> {response.status}",
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
            agencies=None, database=None) -> None:
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
        "stime": {}, "disclosures": [],
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
            if domain.detail:
                cert.fixtures.append(
                    (label, "DELETE", _fill(domain.detail, id=identifier)))
            report.note(f"fixture-{domain.name}-{label}",
                        f"{domain.name}: risorsa di {label} creata (id {identifier})")

    context["owned_properties"] = {label: owned[label].get("PROPERTY")
                                   for label in ("A", "B")}

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

    build_owner_fixtures(report, http, cert, context["owner_jars"], owned, context)

    if database is not None:
        context["stime"] = derive_stime(database, agencies or {})

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


def certify_generic(report, http, cert, domain, jars, owned) -> None:
    """Le sei domande, piu' i modi obliqui, su un dominio."""
    name = domain.name

    # 1-2: ciascuno vede la propria lista, e ci trova la propria fixture.
    if domain.listing:
        for label in ("A", "B"):
            response = http.request("GET", domain.listing, jar=jars[label])
            report.check(f"{name}-list-{label}", response.status == 200,
                         f"{label} legge {domain.listing.split('?')[0]} -> "
                         f"{response.status}")

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
        http_factory=HttpProbe) -> int:
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
                          database=database) or {}
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
        cert.cleanup_chain_fixtures()
        cert.cleanup_owner_fixtures()
        cert.cleanup_http_fixtures(http, jars)
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
    return run(report, database, dict(os.environ), arguments.approved_commit)


if __name__ == "__main__":                          # pragma: no cover
    raise SystemExit(main())
