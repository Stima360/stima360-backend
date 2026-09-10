#!/usr/bin/env python3
"""P26-3 - certificazione live della transizione Client/Auth, su TEST soltanto.

    python scripts/p26_3_live_cert.py

Prova, contro l'applicazione realmente in esecuzione e via HTTP, che la OS
Shell puo' autenticarsi con la sessione operatore, che il canale Basic legacy
continua a funzionare fino a P26-5, e che le due cose non si contaminano.

PERCHE' ESISTE QUESTO FILE
--------------------------
Le prove di P26-3 nella suite sono offline: montano l'applicazione in un
TestClient e sostituiscono la sessione. Sono la difesa quotidiana, e non
possono dire nulla su un cookie `Secure` che attraversa un edge reale, su un
`Set-Cookie` emesso da uvicorn, o su una revoca che deve sopravvivere a un
round-trip col database. Questo script chiude quel divario, e vive in
`scripts/` perche' e' una certificazione da eseguire a mano, non una prova che
gira a ogni push.

COSA TOCCA
----------
Dati di dominio: nulla. Le sole scritture stanno sulle tabelle di
autenticazione - `operator_users`, `agency_memberships`, `operator_sessions` -
e riguardano due operatori temporanei creati qui e cancellati nel `finally`.
Le agenzie vengono lette e mai modificate, create o disattivate.

I due operatori esistono perche' le password del seed P26-1 non sono nel
runtime: sono variabili d'ambiente che servivano al seeding e non vi sono
rimaste. Senza un'identita' vera la sessione operatore non e' provabile, e una
prova dichiarata BLOCKED e' comunque una prova non fatta. Vengono quindi creati
sul momento, con password casuali generate in memoria, mai stampate e mai
scritte su disco, e le loro righe spariscono a fine run.

CLEANUP
-------
Il cleanup e' esso stesso una prova. Ogni fallimento - una revoca non
confermata da un 204, una DELETE che non completa - e' un FAIL che porta
l'uscita a 1. Un cleanup incompleto non puo' in nessun caso produrre PASS: e'
l'unico modo perche' "certificato" significhi anche "il database e' come l'ho
trovato".

Cancella per id, e solo gli id che ha creato lui. Ogni run porta un `run_id`
casuale che finisce nell'email per rendere leggibile la provenienza di una
riga, ma non e' quello il criterio di cancellazione: due certificazioni
avviate insieme condividono il prefisso, e una DELETE per prefisso farebbe
sparire gli operatori dell'altra mentre li sta usando. Se all'avvio trova
righe di certificazione gia' presenti si ferma senza creare e senza
cancellare nulla, perche' da qui non c'e' modo di distinguere il residuo di
ieri da un run in corso adesso.
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

# ---------------------------------------------------------------------------
# Costanti, tutte ricavate dal codice applicativo e non indovinate
# ---------------------------------------------------------------------------

# La certificazione scrive righe di autenticazione: nomina un database esatto e
# rifiuta tutto il resto, incluso un altro database TEST legittimo. Piu' stretto
# di `scripts/p26_migrate.py`, che serve piu' target e accetta il marcatore.
REQUIRED_DB_NAME = "stima360_db_test"
APPROVED_BRANCH = "core-0.1-test"

# operator_auth/enums.py
COOKIE_NAME = "stima360_operator_session"
SESSION_MAX_HOURS = 12
DEFAULT_AGENCY_SLUG = "stima360"

# scripts/p26_seed_agencies_test.py
AGENCY_B_SLUG = "agenzia-b-test"

# migrations/027: `uq_agency_memberships_single_owner` ammette un solo
# agency_owner attivo per agenzia, quindi un operatore di certificazione non
# puo' esserlo. 'agent' e' anche il ruolo di minor privilegio, e le route in
# esame non leggono il ruolo: leggono l'agenzia.
CERT_ROLE = "agent"
CERT_PREFIX = "p26-3-cert-"
CERT_DOMAIN = "@certification.invalid"      # RFC 2606: dominio non risolvibile
CERT_EMAIL_LIKE = CERT_PREFIX + "%" + CERT_DOMAIN

# operator_auth/router.py
LOGIN = "/api/operator-auth/login"
LOGOUT = "/api/operator-auth/logout"
ME = "/api/operator-auth/me"

# Route read-only.
#   OS_ROUTE   proposal_router, montato in main.py con
#              require_authenticated_operator; l'handler list_proposals prende
#              lo scope da legacy_basic_agency_context.
#   CORE_ROUTE core_router, montato con require_operator.
#   PUBLIC     rotta pubblica, solo per la raggiungibilita'.
OS_ROUTE = "/api/proposals?limit=1"
CORE_ROUTE = "/api/core/contacts?limit=1"
PUBLIC = "/api/public/contatore_oggi"

# operator_auth/exceptions.py e operator_auth/dependencies.py
LOGIN_FAILED_DETAIL = "Credenziali non valide."
NOT_AUTHENTICATED_DETAIL = "Non autorizzato"

ME_FIELDS = frozenset(
    {"user_id", "agency_id", "agency_name", "role", "is_platform_admin", "expires_at"}
)

PASS, FAIL, BLOCKED = "PASS", "FAIL", "BLOCKED"


class GuardFailure(Exception):
    """Una precondizione che vieta di procedere del tutto."""


class CheckFailed(Exception):
    """Una prova fallita. Interrompe il run; il cleanup avviene comunque."""


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

class Report:
    """Il verdetto, e le righe che lo giustificano.

    Un oggetto e non variabili di modulo: due run nello stesso processo - che
    e' esattamente cosa fa la suite - devono partire da zero, e il verdetto
    deve essere una funzione delle righe raccolte e di nient'altro.
    """

    def __init__(self, stream=None) -> None:
        self.rows: list[tuple[str, str, str]] = []
        self._stream = stream if stream is not None else sys.stdout

    def record(self, kind: str, ident: str, text: str) -> None:
        self.rows.append((kind, ident, text))
        print(f"{kind:<8} {ident:<11} {text}", file=self._stream, flush=True)

    def check(self, ident: str, condition: bool, text: str) -> None:
        """Registra l'esito e, se negativo, interrompe il run."""
        if condition:
            self.record(PASS, ident, text)
            return
        self.record(FAIL, ident, text)
        raise CheckFailed(ident)

    def note(self, ident: str, text: str) -> None:
        self.record(PASS, ident, text)

    def fail(self, ident: str, text: str) -> None:
        """Un fallimento che NON interrompe. Usato dal cleanup, che deve
        proseguire su tutte le risorse anche quando una gli e' sfuggita."""
        self.record(FAIL, ident, text)

    def blocked(self, ident: str, text: str) -> None:
        self.record(BLOCKED, ident, text)

    def count(self, kind: str) -> int:
        return sum(1 for k, _, _ in self.rows if k == kind)

    @property
    def exit_code(self) -> int:
        """1 se qualcosa e' fallito, 2 se qualcosa e' rimasto BLOCKED, 0 solo
        se ogni prova e' stata eseguita ed e' passata.

        Il FAIL viene prima del BLOCKED di proposito: un run con entrambi e' un
        run fallito, non un run incompleto.
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
            2: "INCOMPLETO - prove BLOCKED, la certificazione non e' chiusa",
            0: "PASS - P26-3 certificato live su TEST",
        }[self.exit_code]

    def summary(self) -> None:
        print("=" * 74, file=self._stream)
        print(
            f"PASS {self.count(PASS)}   FAIL {self.count(FAIL)}   "
            f"BLOCKED {self.count(BLOCKED)}",
            file=self._stream,
        )
        print(f"ESITO: {self.verdict}", file=self._stream)
        print("=" * 74, file=self._stream, flush=True)


# ---------------------------------------------------------------------------
# Guardia sul database
# ---------------------------------------------------------------------------

def assert_certification_database(name: str | None) -> str:
    """Ritorna `name` solo se e' il database di certificazione.

    Nessun marcatore, nessuna euristica, nessuna lista di esclusione: un solo
    nome ammesso. Uno script che crea utenti e li cancella non deve poter
    scegliere il bersaglio sbagliato per somiglianza.
    """
    candidate = (name or "").strip()
    if not candidate:
        raise GuardFailure("BLOCCATO: DB_NAME non e' impostato.")
    if candidate != REQUIRED_DB_NAME:
        raise GuardFailure(
            f"BLOCCATO: {candidate!r} non e' {REQUIRED_DB_NAME!r}. "
            "Questa certificazione scrive righe di autenticazione e nomina un "
            "solo database."
        )
    return candidate


# ---------------------------------------------------------------------------
# Seam sul database
# ---------------------------------------------------------------------------

class Database:
    """Sottile involucro su `operator_auth.database.operator_cursor`.

    Esiste per una ragione sola: rendere iniettabile la sorgente dei cursori,
    cosi' che le prove su questo file possano esercitare cleanup e guardie
    senza un PostgreSQL. La logica sta nei chiamanti, non qui.
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
# Seam HTTP
# ---------------------------------------------------------------------------

class Response:
    __slots__ = ("status", "headers", "body")

    def __init__(self, status: int, headers, body: bytes) -> None:
        self.status = status
        self.headers = headers
        self.body = body

    def json(self):
        try:
            return json.loads(self.body.decode("utf-8"))
        except Exception:
            return None

    def detail(self):
        parsed = self.json()
        return parsed.get("detail") if isinstance(parsed, dict) else None

    def set_cookies(self, name: str) -> list[str]:
        return [
            value
            for key, value in self.headers.items()
            if key.lower() == "set-cookie" and value.startswith(name + "=")
        ]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Ogni stato va osservato esattamente: nessun redirect seguito."""

    def redirect_request(self, *args, **kwargs):
        return None


class HttpProbe:
    """Client HTTP minimo, con cookie jar e con gli header mai stampati.

    Solo stdlib. L'immagine esegue gia' questa applicazione Python, quindi non
    si introduce nulla; e un client che non e' un browser rende esplicito quale
    cookie viene inviato in ogni singola richiesta.
    """

    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.exchanges: list[tuple[str, int, dict, bytes]] = []

    # -- cookie jar --------------------------------------------------------

    def new_jar(self) -> http.cookiejar.CookieJar:
        return http.cookiejar.CookieJar()

    def put_cookie(self, jar, value: str) -> bool:
        """Mette `value` nel jar e VERIFICA che venga davvero emesso.

        Un cookie costruito a mano che il jar poi rifiuta di inviare
        produrrebbe due bugie in una: un 200 sul canale Basic che sembra un bug
        del server, e un logout con 204 che sembra una revoca riuscita senza
        aver revocato nulla. Provato contro `add_cookie_header` prima di
        fidarsene.
        """
        host = urllib.parse.urlparse(self.base).hostname or ""
        jar.set_cookie(
            http.cookiejar.Cookie(
                version=0,
                name=COOKIE_NAME,
                value=value,
                port=None,
                port_specified=False,
                domain=host,
                domain_specified=False,
                domain_initial_dot=False,
                path="/",
                path_specified=True,
                secure=True,
                expires=None,
                discard=False,
                comment=None,
                comment_url=None,
                rest={"HttpOnly": None},
                rfc2109=False,
            )
        )
        probe = urllib.request.Request(self.base + "/probe-cookie-emission")
        jar.add_cookie_header(probe)
        return (probe.get_header("Cookie") or "").startswith(COOKIE_NAME + "=")

    @staticmethod
    def token_in(jar) -> str | None:
        for cookie in jar:
            if cookie.name == COOKIE_NAME and cookie.value:
                return cookie.value
        return None

    # -- richieste ---------------------------------------------------------

    def _opener(self, jar):
        handlers = [
            _NoRedirect(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        ]
        if jar is not None:
            handlers.append(urllib.request.HTTPCookieProcessor(jar))
        return urllib.request.build_opener(*handlers)

    def request(self, method, path, *, jar=None, basic=None, payload=None) -> Response:
        request = urllib.request.Request(self.base + path, method=method)
        if payload is not None:
            request.data = json.dumps(payload).encode("utf-8")
            request.add_header("Content-Type", "application/json")
        if basic is not None:
            import base64

            raw = f"{basic[0]}:{basic[1]}".encode("utf-8")
            token = base64.b64encode(raw).decode("ascii")
            request.add_header("Authorization", "Basic " + token)

        try:
            with self._opener(jar).open(request, timeout=30) as response:
                result = Response(response.status, response.info(), response.read())
        except urllib.error.HTTPError as exc:
            result = Response(exc.code, exc.headers, exc.read())

        self.exchanges.append(
            (
                f"{method} {path.split('?')[0]}",
                result.status,
                dict(result.headers.items()),
                result.body,
            )
        )
        return result


# ---------------------------------------------------------------------------
# Operatori temporanei
# ---------------------------------------------------------------------------

class TemporaryOperators:
    """Crea e distrugge le identita' di certificazione.

    Le password vivono in `self.secrets` per essere cercate nelle risposte, e
    non escono mai da li': non vengono stampate, non finiscono in un file e non
    compaiono in nessun messaggio di questo modulo.
    """

    def __init__(self, database: Database, report: Report) -> None:
        self.db = database
        self.report = report
        # Identifica QUESTO run. Compare nell'email per rendere leggibile a chi
        # guarda il database da dove viene una riga, e non viene mai usato come
        # criterio di cancellazione: il cleanup lavora sugli id, che sono
        # l'unica cosa che questo processo sa di aver creato lui.
        self.run_id = secrets.token_hex(6)
        self.created_ids: list[int] = []
        self.secrets: list[str] = []

    # -- creazione ---------------------------------------------------------

    def leftovers(self) -> int:
        with self.db.read() as cur:
            cur.execute(
                "SELECT COUNT(*) AS n FROM operator_users "
                "WHERE email_normalized LIKE %s",
                (CERT_EMAIL_LIKE,),
            )
            return int(cur.fetchone()["n"])

    def create(self, agency: dict) -> dict:
        """Un operatore attivo nell'agenzia data, con password casuale."""
        from core.normalization import normalize_email
        from operator_auth.security import hash_password

        email = f"{CERT_PREFIX}{self.run_id}-{secrets.token_hex(5)}{CERT_DOMAIN}"
        normalized = normalize_email(email)
        password = secrets.token_urlsafe(32)
        self.secrets.append(password)

        with self.db.write() as cur:
            # Collision check esplicito. L'indice UNIQUE su email_normalized lo
            # garantirebbe comunque, ma un 23505 a meta' run e' un errore da
            # interpretare, non una verifica.
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
            # l'utente esiste gia' e il cleanup deve saperlo.
            self.created_ids.append(user_id)

            cur.execute(
                """
                INSERT INTO agency_memberships (
                    agency_id, operator_user_id, role, status
                ) VALUES (%s, %s, %s, 'active')
                """,
                (agency["id"], user_id, CERT_ROLE),
            )

        return {"id": user_id, "email": email, "password": password, "agency": agency}

    # -- distruzione -------------------------------------------------------

    def _residue(self, cur, ids: tuple[int, ...]) -> dict:
        """Quante righe restano PER GLI ID DI QUESTO RUN.

        Contato per id e non per prefisso dell'email. Un conteggio per prefisso
        includerebbe le righe di un run concorrente e trasformerebbe il lavoro
        di qualcun altro in un fallimento di questo - o, peggio, spingerebbe a
        cancellarle per far tornare il conto.
        """
        cur.execute(
            """
            SELECT (SELECT COUNT(*) FROM operator_users u
                     WHERE u.id IN %s)                              AS users,
                   (SELECT COUNT(*) FROM agency_memberships m
                     WHERE m.operator_user_id IN %s)                AS memberships,
                   (SELECT COUNT(*) FROM operator_sessions s
                     WHERE s.operator_user_id IN %s)                AS sessions
            """,
            (ids, ids, ids),
        )
        row = cur.fetchone()
        return {k: int(row[k]) for k in ("users", "memberships", "sessions")}

    def cleanup(self) -> None:
        """Cancella cio' che QUESTO run ha creato, e lo dimostra.

        Il criterio e' `created_ids` e nient'altro. Cancellare per prefisso
        dell'email sarebbe piu' comodo - ripulirebbe anche i resti di ieri - e
        sarebbe sbagliato: due certificazioni avviate insieme condividono il
        prefisso, e la seconda a finire porterebbe via gli operatori della
        prima mentre li sta ancora usando, facendola fallire con 401
        inspiegabili. Un processo cancella quello che ha creato lui.

        Le tre DELETE sono esplicite anche dove ON DELETE CASCADE basterebbe.
        L'ordine rende leggibile l'intenzione e attribuisce un fallimento
        parziale alla tabella giusta; il conteggio finale e' l'unica prova che
        conta davvero.
        """
        if not self.created_ids:
            self.report.note("CLEAN-DB", "nessun operatore creato: niente da rimuovere")
            return

        ids = tuple(self.created_ids)
        try:
            with self.db.write() as cur:
                cur.execute(
                    "DELETE FROM operator_sessions WHERE operator_user_id IN %s",
                    (ids,),
                )
                sessions = cur.rowcount
                cur.execute(
                    "DELETE FROM agency_memberships WHERE operator_user_id IN %s",
                    (ids,),
                )
                memberships = cur.rowcount
                cur.execute("DELETE FROM operator_users WHERE id IN %s", (ids,))
                users = cur.rowcount
                self.report.note(
                    "CLEAN-DB",
                    f"run {self.run_id}: rimossi {sessions} sessioni, "
                    f"{memberships} membership, {users} utenti temporanei",
                )

            with self.db.read() as cur:
                final = self._residue(cur, ids)
        except Exception as exc:
            self.report.fail(
                "CLEAN-DB",
                f"cleanup del database fallito ({type(exc).__name__}): "
                f"le {len(ids)} righe del run {self.run_id} sono POTENZIALMENTE "
                "PRESENTI",
            )
            return

        if sum(final.values()) == 0:
            self.report.note(
                "CLEAN-CHK",
                f"verifica sugli id del run {self.run_id}: 0 utenti, "
                "0 membership, 0 sessioni",
            )
        else:
            self.report.fail(
                "CLEAN-CHK",
                f"cleanup INCOMPLETO: restano {final['users']} utenti, "
                f"{final['memberships']} membership, {final['sessions']} sessioni "
                f"del run {self.run_id}",
            )


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------

def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], capture_output=True, text=True, timeout=30
        ).stdout.strip()
    except Exception:
        return ""


def preflight(report: Report, database: Database, env: dict, approved_commit: str) -> str:
    """Verifica database, commit, branch e app. Ritorna la base URL.

    Il database viene per primo, prima ancora del commit. E' la guardia che
    impedisce di scrivere righe nel posto sbagliato, e una guardia che puo'
    essere preceduta da un altro controllo e' una guardia che quel controllo
    puo' mascherare.
    """
    # DB_NAME e' una variabile; la connessione reale e' l'autorita'. Vengono
    # controllate entrambe, e devono concordare.
    assert_certification_database(env.get("DB_NAME"))
    report.note("0.1", f"DB_NAME = {REQUIRED_DB_NAME}")
    connected = database.current_database()
    report.check(
        "0.2",
        connected == REQUIRED_DB_NAME,
        f"la connessione reale e' su {connected!r}, non solo DB_NAME",
    )

    head = _git("rev-parse", "HEAD")
    report.check(
        "0.3",
        head == approved_commit,
        f"git rev-parse HEAD = {head or '(assente)'} atteso {approved_commit}",
    )

    render_commit = env.get("RENDER_GIT_COMMIT", "")
    if render_commit:
        report.check(
            "0.4",
            render_commit == head,
            f"RENDER_GIT_COMMIT = {render_commit} coincide con HEAD",
        )
    else:
        report.blocked("0.4", "RENDER_GIT_COMMIT assente: incrocio non eseguibile")

    branch = env.get("RENDER_GIT_BRANCH") or _git("branch", "--show-current")
    report.check(
        "0.5",
        branch == APPROVED_BRANCH,
        f"branch = {branch or '(assente)'} atteso {APPROVED_BRANCH}",
    )

    from operator_auth.security import hash_password

    # Non "e' importata", ma "e' davvero quella funzione": il CHECK di
    # migrations/027 impone password_hash LIKE 'pbkdf2_sha256$%', e un hash
    # costruito altrimenti sarebbe rifiutato dal database anziche' dalla login.
    report.check(
        "0.6",
        hash_password("prova-di-forma").startswith("pbkdf2_sha256$"),
        "hashing preso dal codice applicativo (operator_auth.security)",
    )

    base = (env.get("TEST_BASE_URL") or env.get("RENDER_EXTERNAL_URL") or "").rstrip("/")
    report.check("0.7", bool(base), "URL dell'app disponibile (RENDER_EXTERNAL_URL)")
    # Il cookie e' Secure: un cookie jar corretto NON lo invia su http://, e
    # tutte le prove di sessione darebbero 401 fuorvianti.
    scheme = urllib.parse.urlparse(base).scheme
    report.check(
        "0.8",
        scheme == "https",
        f"la base e' https ({scheme or 'nessuno'}): il cookie e' Secure e non "
        "viaggia in chiaro",
    )
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
        report.check(
            f"0.9-{slug}",
            bool(row) and row["status"] == "active",
            f"agenzia {slug!r} presente e attiva",
        )
    first, second = found[DEFAULT_AGENCY_SLUG], found[AGENCY_B_SLUG]
    report.check("0.10", first["id"] != second["id"], "le due agenzie TEST sono distinte")
    return first, second


# ---------------------------------------------------------------------------
# Le prove
# ---------------------------------------------------------------------------

def certify(report: Report, http: HttpProbe, operators: TemporaryOperators,
            agency_a: dict, agency_b: dict, basic_ok, basic_wrong) -> None:
    """Tutte le prove HTTP. Solleva CheckFailed alla prima che fallisce."""
    have_basic = basic_ok is not None

    operator_a = operators.create(agency_a)
    report.note("0.11", f"operatore temporaneo creato in {agency_a['slug']!r} "
                        f"(ruolo {CERT_ROLE})")
    operator_b = operators.create(agency_b)
    report.note("0.12", f"operatore temporaneo creato in {agency_b['slug']!r} "
                        f"(ruolo {CERT_ROLE})")

    # -- 1: login valido e cookie HttpOnly ---------------------------------
    jar_a = http.new_jar()
    response = http.request(
        "POST", LOGIN, jar=jar_a,
        payload={"email": operator_a["email"], "password": operator_a["password"]},
    )
    report.check("1.1", response.status == 204,
                 f"POST {LOGIN} con credenziali valide -> {response.status} (atteso 204)")
    report.check("1.2", response.body == b"",
                 "il corpo della login e' vuoto: il token sta solo nel Set-Cookie")
    lines = response.set_cookies(COOKIE_NAME)
    report.check("1.3", len(lines) == 1,
                 f"un solo Set-Cookie per {COOKIE_NAME} ({len(lines)})")
    attrs = lines[0].lower()
    report.check("1.4", "httponly" in attrs, "il cookie di sessione e' HttpOnly")
    report.check("1.5", "secure" in attrs, "il cookie di sessione e' Secure")
    report.check("1.6", "samesite=lax" in attrs, "il cookie di sessione e' SameSite=Lax")
    report.check("1.7", "path=/" in attrs, "il cookie di sessione ha Path=/")
    report.check("1.8", f"max-age={SESSION_MAX_HOURS * 3600}" in attrs,
                 f"il cookie scade a {SESSION_MAX_HOURS}h "
                 f"(Max-Age={SESSION_MAX_HOURS * 3600})")
    token_a = http.token_in(jar_a)
    report.check("1.9", bool(token_a), "il cookie jar ha acquisito la sessione")
    operators.secrets.append(token_a)

    response = http.request(
        "POST", LOGIN,
        payload={"email": operator_a["email"],
                 "password": operator_a["password"] + "-wrong"},
    )
    report.check("1.10",
                 response.status == 401 and response.detail() == LOGIN_FAILED_DETAIL,
                 f"password errata -> {response.status} col messaggio unico di "
                 "fallimento")

    # -- 2: /me con cookie -------------------------------------------------
    response = http.request("GET", ME, jar=jar_a)
    report.check("2.1", response.status == 200,
                 f"GET {ME} col solo cookie -> {response.status} (atteso 200)")
    me_a = response.json()
    report.check("2.2", isinstance(me_a, dict) and set(me_a) == set(ME_FIELDS),
                 "/me proietta esattamente i campi di MeResponse "
                 "(nessun email, nessun session_id)")
    report.check("2.3", me_a["user_id"] == operator_a["id"],
                 "/me identifica proprio l'operatore che ha fatto login")
    report.check("2.4",
                 me_a["agency_id"] == agency_a["id"]
                 and me_a["agency_name"] == agency_a["name"],
                 f"/me riporta l'agenzia della membership ({agency_a['slug']})")
    report.check("2.5",
                 me_a["role"] == CERT_ROLE and me_a["is_platform_admin"] is False,
                 f"ruolo {CERT_ROLE!r}, non platform admin")

    # -- 3: una route OS non-CORE accetta la sola sessione -----------------
    response = http.request("GET", OS_ROUTE, jar=jar_a)
    report.check("3.1", response.status == 200,
                 f"GET {OS_ROUTE.split('?')[0]} con la sola sessione -> "
                 f"{response.status} (atteso 200)")
    response = http.request("GET", CORE_ROUTE, jar=jar_a)
    report.check("3.2", response.status == 200,
                 f"GET {CORE_ROUTE.split('?')[0]} con la sola sessione -> "
                 f"{response.status} (atteso 200)")

    # -- 4: Basic senza cookie continua a funzionare -----------------------
    if not have_basic:
        report.blocked("4", "Basic senza cookie: ADMIN_USER/ADMIN_PASS non disponibili")
    else:
        response = http.request("GET", OS_ROUTE, basic=basic_ok)
        report.check("4.1", response.status == 200,
                     f"GET {OS_ROUTE.split('?')[0]} col solo Basic -> "
                     f"{response.status} (atteso 200)")
        response = http.request("GET", CORE_ROUTE, basic=basic_ok)
        report.check("4.2", response.status == 200,
                     f"GET {CORE_ROUTE.split('?')[0]} col solo Basic -> "
                     f"{response.status} (atteso 200)")
        response = http.request("GET", OS_ROUTE, basic=basic_wrong)
        report.check("4.3", response.status == 401,
                     f"Basic errato senza cookie -> {response.status} (atteso 401)")

    # -- 5: la sessione prevale sul Basic ----------------------------------
    response = http.request("GET", OS_ROUTE, jar=jar_a, basic=basic_wrong)
    report.check("5.1", response.status == 200,
                 f"sessione valida + Basic errato -> {response.status}: la sessione "
                 "vince (atteso 200)")
    response = http.request("GET", ME, jar=jar_a, basic=basic_wrong)
    report.check("5.2", response.status == 200 and response.json() == me_a,
                 "/me con sessione + Basic errato descrive lo stesso operatore")

    if not have_basic:
        report.blocked("5.3", "precedenza per identita': Basic non disponibile")
    else:
        jar_b = http.new_jar()
        response = http.request(
            "POST", LOGIN, jar=jar_b,
            payload={"email": operator_b["email"], "password": operator_b["password"]},
        )
        report.check("5.3", response.status == 204,
                     f"login dell'operatore B -> {response.status} (atteso 204)")
        token_b = http.token_in(jar_b)
        if token_b:
            operators.secrets.append(token_b)
        response = http.request("GET", ME, jar=jar_b, basic=basic_ok)
        me_b = response.json() or {}
        report.check("5.4",
                     response.status == 200 and me_b.get("agency_id") == agency_b["id"],
                     f"sessione {agency_b['slug']} + Basic VALIDO: lo scope resta "
                     f"{agency_b['name']!r} e non l'agenzia del Basic "
                     f"(letto: {me_b.get('agency_name')!r})")
        report.check("5.5", me_b.get("agency_id") != me_a["agency_id"],
                     "le due sessioni appartengono ad agenzie diverse: la precedenza "
                     "e' provata per identita', non per assenza di 401")

    # -- 6: cookie invalido + Basic valido -> 401, nessun fallback ---------
    if not have_basic:
        report.blocked("6", "assenza di fallback: Basic non disponibile")
    else:
        jar_dead = http.new_jar()
        if not http.put_cookie(jar_dead, "not-a-real-session-token"):
            report.blocked("6", "il cookie jar non emette il cookie costruito: "
                                "risultato non interpretabile")
        else:
            report.note("6.0", "il cookie invalido viene effettivamente inviato")
            response = http.request("GET", OS_ROUTE, jar=jar_dead, basic=basic_ok)
            report.check("6.1", response.status == 401,
                         f"cookie invalido + Basic VALIDO su route OS -> "
                         f"{response.status} (atteso 401: nessun fallback silenzioso)")
            report.check("6.2", response.detail() == NOT_AUTHENTICATED_DETAIL,
                         "il rifiuto usa il messaggio unico, senza dire quale "
                         "credenziale ha fallito")
            response = http.request("GET", CORE_ROUTE, jar=jar_dead, basic=basic_ok)
            report.check("6.3", response.status == 401,
                         f"cookie invalido + Basic VALIDO su CORE -> "
                         f"{response.status} (atteso 401)")
            response = http.request("GET", ME, jar=jar_dead, basic=basic_ok)
            report.check("6.4", response.status == 401,
                         f"cookie invalido + Basic VALIDO su /me -> {response.status}")

    # -- 7-8: logout revoca, e il cookie revocato non risorge --------------
    response = http.request("POST", LOGOUT, jar=jar_a)
    report.check("7.1", response.status == 204,
                 f"POST {LOGOUT} -> {response.status} (atteso 204)")
    cleared = response.set_cookies(COOKIE_NAME)
    report.check("7.2", bool(cleared),
                 "il logout emette un Set-Cookie di cancellazione")
    wiped = cleared[0].lower()
    report.check(
        "7.3",
        f'{COOKIE_NAME}=""' in cleared[0]
        or f"{COOKIE_NAME}=;" in cleared[0]
        or "max-age=0" in wiped
        or "expires=thu, 01 jan 1970" in wiped,
        "il Set-Cookie di logout cancella davvero il valore",
    )

    revoked = http.new_jar()
    if not http.put_cookie(revoked, token_a):
        report.blocked("8", "cookie revocato non inviabile")
    else:
        response = http.request("GET", ME, jar=revoked)
        report.check("8.1", response.status == 401,
                     f"/me col cookie revocato -> {response.status} (atteso 401)")
        response = http.request("GET", OS_ROUTE, jar=revoked)
        report.check("8.2", response.status == 401,
                     f"route OS col cookie revocato -> {response.status} (atteso 401)")
        response = http.request("GET", CORE_ROUTE, jar=revoked)
        report.check("8.3", response.status == 401,
                     f"CORE col cookie revocato -> {response.status} (atteso 401)")
        if not have_basic:
            report.blocked("8.4", "revoca + Basic: ADMIN_PASS non disponibile")
        else:
            response = http.request("GET", OS_ROUTE, jar=revoked, basic=basic_ok)
            report.check("8.4", response.status == 401,
                         f"cookie REVOCATO + Basic VALIDO -> {response.status} "
                         "(atteso 401: la revoca non si aggira col Basic)")

    response = http.request("POST", LOGOUT, jar=http.new_jar())
    report.check("7.4", response.status == 204,
                 f"logout senza cookie -> {response.status}: non e' un oracolo sui "
                 "token")


def scan_for_leaks(report: Report, http: HttpProbe, secrets_seen: list[str]) -> None:
    """Nessuna risposta deve contenere una password, un token o un cookie.

    Solo questi. ADMIN_USER e' un identificativo, non un segreto, e puo'
    comparire legittimamente dentro i dati di un'agenzia.
    """
    leaks = []
    for label, _status, headers, body in http.exchanges:
        haystack = body + b"\n" + "\n".join(
            f"{key}: {value}"
            for key, value in headers.items()
            if key.lower() != "set-cookie"
        ).encode("utf-8", "replace")
        for secret in secrets_seen:
            if secret and secret.encode("utf-8") in haystack:
                leaks.append(label)
    report.check("9.1", not leaks,
                 f"nessuna password, token o cookie in {len(http.exchanges)} risposte "
                 f"(corpi e header, escluso il Set-Cookie della login) {leaks or ''}")

    # Ristretto a operator-auth: su una route di dominio la parola "password"
    # puo' comparire dentro la nota di un contatto e produrre un FAIL falso.
    # Li' la prova esatta e' 9.1, che cerca i valori veri e non un vocabolario.
    vocabulary = ("password", "password_hash", "token_hash", "secret")
    hits = [
        f"{label}:{word}"
        for label, _status, _headers, body in http.exchanges
        if "/api/operator-auth" in label
        for word in vocabulary
        if word.encode() in body.lower()
    ]
    report.check("9.2", not hits,
                 f"nessuna risposta di operator-auth nomina un campo sensibile "
                 f"{hits or ''}")


# ---------------------------------------------------------------------------
# Orchestrazione
# ---------------------------------------------------------------------------

def run(report: Report, database: Database, env: dict, approved_commit: str,
        http_factory=HttpProbe) -> int:
    """Esegue la certificazione e ritorna l'exit code.

    Il cleanup sta in un `finally` che comprende la creazione: il primo INSERT
    e' il punto oltre il quale un'uscita non pulita lascerebbe righe nel
    database, e nulla fra quel punto e il `finally` puo' saltarlo.
    """
    try:
        base = preflight(report, database, env, approved_commit)
    except (CheckFailed, GuardFailure) as exc:
        if isinstance(exc, GuardFailure):
            report.fail("0.1", str(exc))
        report.summary()
        return report.exit_code

    http = http_factory(base)
    response = http.request("GET", PUBLIC)
    try:
        report.check("0.13", response.status == 200,
                     f"app raggiungibile via HTTP: GET {PUBLIC} -> {response.status}")
        agency_a, agency_b = read_agencies(report, database)
    except CheckFailed:
        report.summary()
        return report.exit_code

    admin_user = env.get("ADMIN_USER") or ""
    admin_pass = env.get("ADMIN_PASS") or ""
    have_basic = bool(admin_user and admin_pass)
    if have_basic:
        report.note("0.14", "ADMIN_USER/ADMIN_PASS presenti nel runtime")
    else:
        report.blocked("0.14", "ADMIN_USER/ADMIN_PASS assenti: canale Basic non "
                               "provabile")
    basic_ok = (admin_user, admin_pass) if have_basic else None
    basic_wrong = (admin_user or "admin", "questa-non-e-la-password-di-admin")

    operators = TemporaryOperators(database, report)
    # ADMIN_PASS e' una password, quindi un segreto. ADMIN_USER no.
    if admin_pass:
        operators.secrets.append(admin_pass)

    # Residui di un run precedente: si ferma qui.
    #
    # Rimuoverli automaticamente sarebbe la cosa comoda e la cosa sbagliata.
    # Questo processo non ha modo di sapere se quelle righe appartengono a un
    # run finito male ieri o a una certificazione avviata dieci secondi fa da
    # un'altra shell, e le due richiedono risposte opposte: una va ripulita,
    # l'altra sta lavorando. Nel dubbio non si tocca niente e non si crea
    # niente - un residuo va guardato da una persona, che sa quale dei due casi
    # e'. Il cleanup di questo run resta comunque circoscritto ai propri id,
    # quindi anche sbagliando qui non porterebbe via il lavoro altrui.
    leftovers = operators.leftovers()
    if leftovers:
        report.fail(
            "0.15",
            f"{leftovers} operatore/i di certificazione gia' presenti "
            f"({CERT_PREFIX}...{CERT_DOMAIN}). Potrebbero essere il residuo di un "
            "run non completato oppure una certificazione in corso da un'altra "
            "shell: nessun operatore creato e nessuna riga cancellata. Verificare "
            "a mano prima di rieseguire.",
        )
        report.summary()
        return report.exit_code
    report.note("0.15", "nessun residuo di certificazioni precedenti")

    try:
        certify(report, http, operators, agency_a, agency_b, basic_ok, basic_wrong)
        scan_for_leaks(report, http, operators.secrets)
    except CheckFailed:
        pass
    except Exception as exc:                       # pragma: no cover - difensivo
        report.fail("RUN", f"eccezione non gestita: {type(exc).__name__}")
    finally:
        operators.cleanup()

    report.summary()
    return report.exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Certificazione live P26-3 su Render TEST."
    )
    parser.add_argument(
        "--approved-commit",
        required=True,
        help="il commit rivisto e approvato, confrontato con git rev-parse HEAD",
    )
    arguments = parser.parse_args(argv)

    report = Report()
    print("=" * 74)
    print("P26-3 LIVE CERTIFICATION - Render TEST")
    print("=" * 74)
    try:
        database = Database.connect()
    except Exception as exc:
        report.fail("0.0", f"connessione al database non riuscita ({type(exc).__name__})")
        report.summary()
        return report.exit_code
    return run(report, database, dict(os.environ), arguments.approved_commit)


if __name__ == "__main__":                          # pragma: no cover
    raise SystemExit(main())
