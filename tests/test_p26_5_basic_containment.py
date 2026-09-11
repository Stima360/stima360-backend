"""P26-5 - l'inventario del canale HTTP Basic residuo.

PERCHE' UN INVENTARIO, E NON UNA FRASE IN UN COMMENTO

P26-5 dichiara che nessuna route di tenant accetta piu' HTTP Basic. E' la
condizione che rende una seconda agenzia reale una possibilita' tecnica invece
che un rischio, quindi non puo' essere affidata alla memoria di chi legge il
diff: va calcolata dall'applicazione servita, a ogni esecuzione della suite.

Questo file e' quella prova. Non cerca stringhe nel sorgente - una ricerca
testuale non distingue una dipendenza viva da un commento che la nomina, e
P26-5 e' piena di commenti che nominano apposta cio' che il codice non fa piu'.
Interroga il documento OpenAPI dell'app reale, che e' generato dalle dipendenze
effettivamente dichiarate.

LE TRE COSE CHE PROVA

1. L'applicazione servita dichiara UN SOLO schema di sicurezza, ed e' il cookie
   di sessione. E il modulo di autenticazione non ne tiene uno Basic vivo e
   inutilizzato: l'OpenAPI da solo non lo direbbe, perche' FastAPI registra
   soltanto gli schemi usati - vedi il test 2b.
2. Ogni superficie che non dichiara sicurezza e' pubblica per progetto, oppure
   e' `/api/admin/check` - l'unica residua, e il perche' e' scritto qui sotto.
3. `whatsapp.py` definisce una SECONDA applicazione FastAPI con route protette
   da HTTP Basic. Non e' servita da nessuno, e questo file lo fissa: "non
   montata" e' esattamente il genere di cosa che diventa "montata" senza che
   nessuno se ne accorga.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def spec():
    import main

    return main.app.openapi()


# ---------------------------------------------------------------------------
# L'INVENTARIO CONGELATO
# ---------------------------------------------------------------------------
#
# Ogni operazione dell'app servita che NON dichiara uno schema di sicurezza,
# con la ragione per cui puo' restare cosi'. Un'aggiunta a questa lista e' una
# decisione da rivedere, non una riga da scrivere: il test sotto confronta
# l'elenco calcolato con questo, e fallisce in entrambe le direzioni.

PUBLIC_BY_DESIGN = {
    # Il funnel pubblico: e' il prodotto, non un'amministrazione. Scrive
    # attraverso `SystemAgencyContext`, che risolve l'agenzia Default lato
    # server e non accetta nulla dal client.
    ("POST", "/api/salva_stima"),
    ("POST", "/api/salva_stima_dettagliata"),
    ("POST", "/api/stima_base"),
    ("GET", "/api/prefill"),
    ("GET", "/api/public/contatore_oggi"),
    ("GET", "/api/successi"),
    # SEO e crawler.
    ("GET", "/api/seo/data"),
    ("GET", "/robots.txt"),
    ("GET", "/sitemap.xml"),
    # Il webhook WhatsApp: autenticato dalla firma di Meta, non da una
    # credenziale - `verify_whatsapp_signature`. Uno schema di sicurezza
    # HTTP non descriverebbe quel meccanismo.
    ("GET", "/webhook/whatsapp"),
    ("POST", "/webhook/whatsapp"),
    # Login e logout devono restare raggiungibili senza sessione: attaccare
    # una dipendenza di autenticazione qui renderebbe impossibile entrare.
    ("POST", "/api/operator-auth/login"),
    ("POST", "/api/operator-auth/logout"),
}

# L'UNICO INGRESSO BASIC RESIDUO.
#
# Non dichiara uno schema di sicurezza perche' non usa una dipendenza: prende
# la coppia nel corpo della richiesta e la confronta con due variabili
# d'ambiente. Non apre una connessione, non nomina una tabella, non risolve
# un'agenzia e non restituisce alcun dato - non c'e' tenant da isolare perche'
# non c'e' tenant.
#
# Dopo P26-5 nessun frontend la chiama: era il primo passo del login Basic dei
# cinque pannelli amministrativi, che adesso passano da
# /api/operator-auth/login. Resta raggiungibile perche' rimuovere un endpoint
# e' una decisione di prodotto, non un effetto collaterale di una migrazione.
LEGACY_CREDENTIAL_CHECK = ("POST", "/api/admin/check")

# Il portale proprietari. Autentica un principale DIVERSO - il proprietario,
# con `current_owner` e il proprio cookie - e quella dipendenza non e' un
# `SecurityBase`, quindi non produce una dichiarazione OpenAPI.
#
# E' una condizione preesistente a P26-5 e non una sua conseguenza: il portale
# non e' mai stato su HTTP Basic. Elencata qui perche' un'assenza di `security`
# non spiegata e' indistinguibile da una dimenticanza.
OWNER_PORTAL_PREFIX = "/api/owner/portal"


def _operations(spec):
    return [
        (method.upper(), path, operation.get("security"))
        for path, item in spec["paths"].items()
        for method, operation in item.items()
        if isinstance(operation, dict)
    ]


# ---------------------------------------------------------------------------
# 1 - l'app servita dichiara un solo schema, ed e' il cookie
# ---------------------------------------------------------------------------

def test_1_the_served_application_declares_only_the_session_cookie(spec):
    """Un solo schema di sicurezza nell'app servita, ed e' il cookie."""
    schemes = spec.get("components", {}).get("securitySchemes", {})
    assert set(schemes) == {"APIKeyCookie"}, schemes

    cookie = schemes["APIKeyCookie"]
    assert cookie["type"] == "apiKey"
    assert cookie["in"] == "cookie"
    # Il nome reale, non un segnaposto: uno schema che nominasse un cookie
    # diverso da quello che il server emette descriverebbe un'API immaginaria.
    from operator_auth.enums import COOKIE_NAME

    assert cookie["name"] == COOKIE_NAME


def test_2_no_operation_declares_a_basic_scheme(spec):
    basic = [
        (method, path) for method, path, security in _operations(spec)
        if security and any("Basic" in str(key) for entry in security for key in entry)
    ]
    assert basic == [], basic


def test_2b_the_auth_module_holds_no_unused_basic_scheme():
    """L'OpenAPI non basta a provarlo, e questa e' la differenza.

    FastAPI registra in `securitySchemes` solo gli schemi effettivamente usati
    come dipendenza: un `HTTPBasic()` dichiarato e mai riferito non comparirebbe
    nel documento, e i test 1 e 2 lo lascerebbero passare - come infatti
    facevano, finche' una mutazione non l'ha mostrato.

    Uno schema Basic vivo dentro il modulo di autenticazione non e' innocuo:
    e' un oggetto pronto all'uso a una riga di distanza dal riaprire il canale,
    e chi lo trova ragionevolmente pensa che serva a qualcosa. Questo controllo
    guarda il sorgente perche' e' li' che quel residuo vive.

    `HTTPBasicCredentials` resta importato: e' il TIPO che
    `_verify_legacy_credentials` riceve, e quella funzione serve ancora
    `/api/admin/check`. Un tipo non e' una porta.
    """
    import ast

    source = (ROOT / "operator_auth" / "dependencies.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    instantiations = [
        ast.unparse(node) for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "HTTPBasic"
    ]
    assert instantiations == [], (
        f"schema Basic vivo in operator_auth/dependencies.py: {instantiations}"
    )
    assert "HTTPBasic(" not in source


def test_3_the_cookie_is_declared_on_the_protected_surface_not_merely_defined(spec):
    """Uno schema definito e mai riferito sarebbe decorazione.

    La prova che conta e' che le route protette lo DICHIARINO: e' quella
    dichiarazione che P26-5 rischiava di perdere togliendo `HTTPBasic`, e che
    `APIKeyCookie` restituisce.
    """
    operations = _operations(spec)
    with_cookie = [
        (method, path) for method, path, security in operations
        if security and any("APIKeyCookie" in str(key)
                            for entry in security for key in entry)
    ]
    # Non un numero congelato - cambierebbe a ogni route aggiunta - ma la
    # proprieta': la stragrande maggioranza della superficie e' protetta, e
    # tutto cio' che non lo e' e' enumerato nel test successivo.
    assert len(with_cookie) > 150, len(with_cookie)
    assert len(with_cookie) == len(operations) - len(_unsecured(spec))


def _unsecured(spec):
    return sorted((method, path) for method, path, security in _operations(spec)
                  if not security)


# ---------------------------------------------------------------------------
# 4 - ogni superficie senza sicurezza e' enumerata e giustificata
# ---------------------------------------------------------------------------

def test_4_every_unsecured_operation_is_in_the_frozen_inventory(spec):
    """Il confronto e' nelle DUE direzioni.

    Una route nuova senza sicurezza fa fallire; e una voce dell'inventario che
    non corrisponde piu' a nulla fa fallire ugualmente, perche' un inventario
    che elenca superfici inesistenti smette di essere un inventario.
    """
    unsecured = set(_unsecured(spec))
    portal = {(m, p) for m, p in unsecured if p.startswith(OWNER_PORTAL_PREFIX)}
    accounted = PUBLIC_BY_DESIGN | {LEGACY_CREDENTIAL_CHECK} | portal

    unexplained = unsecured - accounted
    assert unexplained == set(), (
        f"superfici senza sicurezza e senza giustificazione: {sorted(unexplained)}"
    )

    stale = accounted - unsecured - portal
    assert stale == set(), (
        f"l'inventario elenca superfici che non esistono piu': {sorted(stale)}"
    )


def test_5_admin_check_is_the_only_residual_basic_credential_endpoint(spec):
    """L'affermazione centrale di P26-5, calcolata invece che dichiarata.

    "Unico" significa: fra tutte le operazioni dell'app servita, una sola
    confronta ADMIN_USER/ADMIN_PASS. Provato leggendo quali handler chiamano
    quelle variabili, non cercando la parola "Basic" nel sorgente.
    """
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    tree = __import__("ast").parse(source)
    ast = __import__("ast")

    reading_admin_env = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = ast.get_source_segment(source, node) or ""
        if 'os.getenv("ADMIN_USER")' in body or 'os.getenv("ADMIN_PASS")' in body:
            reading_admin_env.append(node.name)

    assert reading_admin_env == ["admin_check"], reading_admin_env

    # E la route esiste ancora, altrimenti l'assenza sarebbe vera per il motivo
    # sbagliato.
    assert LEGACY_CREDENTIAL_CHECK in set(_unsecured(spec))


# ---------------------------------------------------------------------------
# 6 - nessuna route di tenant accetta Basic
# ---------------------------------------------------------------------------

TENANT_PREFIXES = (
    "/api/core", "/api/property", "/api/buy", "/api/match", "/api/proposals",
    "/api/sales", "/api/crm", "/api/seller-intelligence", "/api/followup",
    "/api/seller-intent", "/api/property-watch", "/api/next-best-action",
    "/api/flow", "/api/owner/admin", "/api/admin/stime", "/api/admin/whatsapp",
)


def test_6_every_tenant_route_declares_the_cookie_and_only_the_cookie(spec):
    """La prova che P26-5 doveva ottenere, sull'app reale.

    Ogni operazione sotto un prefisso di tenant dichiara `APIKeyCookie`, e
    nessuna dichiara altro. Una route di tenant senza sicurezza sarebbe un buco;
    una con uno schema Basic sarebbe il canale che rientra.
    """
    offenders = []
    for method, path, security in _operations(spec):
        if not any(path.startswith(prefix) for prefix in TENANT_PREFIXES):
            continue
        keys = {key for entry in (security or []) for key in entry}
        if keys != {"APIKeyCookie"}:
            offenders.append((method, path, sorted(keys)))
    assert offenders == [], offenders


def test_7_the_tenant_prefix_list_matches_what_is_actually_mounted():
    """L'elenco sopra non e' scritto a mano e basta: viene confrontato con i
    router realmente montati, cosi' aggiungerne uno e dimenticarlo qui fa
    fallire invece di ridurre in silenzio la copertura di questo file."""
    main_source = (ROOT / "main.py").read_text(encoding="utf-8")
    mounted = set(re.findall(r"app\.include_router\((\w+)", main_source))

    for path in sorted(ROOT.glob("*/router.py")):
        module = path.parent.name
        text = path.read_text(encoding="utf-8")
        # Entrambi gli stili di virgoletta: `buy` e `property` usano gli
        # apici singoli, e un pattern che accettasse solo le doppie li
        # renderebbe invisibili a questo inventario.
        found = re.search(r"""APIRouter\(\s*prefix=["']([^"']+)["']""", text)
        if not found:
            continue
        prefix = found.group(1)
        if prefix == "/api/operator-auth":
            continue                      # l'autenticazione, non un tenant
        if f"{module}_router" not in mounted and module != "flow":
            continue
        assert prefix in TENANT_PREFIXES, (
            f"{module} e' montato su {prefix} e non compare in TENANT_PREFIXES"
        )


# ---------------------------------------------------------------------------
# 8 - OWNER Admin: sessione, ruolo, e agenzia dalla sessione
# ---------------------------------------------------------------------------

def test_8_owner_admin_requires_a_session_and_the_owner_role():
    from fastapi import HTTPException

    from operator_auth.dependencies import (
        OWNER_ADMIN_MIN_ROLE, require_owner_admin_context,
    )

    assert OWNER_ADMIN_MIN_ROLE == "agency_owner"

    # Nessuna sessione: 401.
    with pytest.raises(HTTPException) as anonymous:
        require_owner_admin_context(None)
    assert anonymous.value.status_code == 401

    # Ruolo insufficiente: 403, non 401. Autenticato si', autorizzato no.
    for role in ("agent", "agency_admin"):
        with pytest.raises(HTTPException) as refused:
            require_owner_admin_context(_session(role))
        assert refused.value.status_code == 403, role

    # Titolare e platform admin: ammessi, con l'agenzia DELLA SESSIONE.
    for session in (_session("agency_owner"), _session("agent", platform_admin=True)):
        context = require_owner_admin_context(session)
        assert context.agency_id == 4242
        assert context.auth_channel == "operator_session"


def _session(role, platform_admin=False, agency_id=4242):
    from datetime import datetime, timezone

    from operator_auth.context import OperatorContext
    from operator_auth.dependencies import AuthenticatedSession

    return AuthenticatedSession(
        context=OperatorContext(
            user_id=1, agency_id=agency_id, role=role,
            is_platform_admin=platform_admin, session_id=1,
            auth_channel="operator_session",
        ),
        agency_name="Agenzia",
        expires_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
    )


def test_9_owner_admin_takes_its_agency_from_the_session_not_from_a_default():
    """Il guadagno di P26-5 su questa superficie.

    Prima l'agenzia era sempre la Default, risolta dal segreto condiviso: OWNER
    Admin non poteva servire una seconda agenzia nemmeno in linea di principio.
    """
    from operator_auth.dependencies import require_owner_admin_context

    for agency_id in (7, 8):
        context = require_owner_admin_context(
            _session("agency_owner", agency_id=agency_id))
        assert context.agency_id == agency_id

    # E non c'e' alcuna risoluzione della Default Agency in mezzo: la funzione
    # non la nomina. Se lo facesse, due agenzie riceverebbero lo stesso scope.
    import inspect

    source = inspect.getsource(require_owner_admin_context)
    body = source[source.index('"""', source.index('"""') + 3) + 3:]
    assert "_default_agency_context" not in body


# ---------------------------------------------------------------------------
# 10 - la seconda applicazione FastAPI, e il fatto che non sia servita
# ---------------------------------------------------------------------------

def test_10_the_whatsapp_module_defines_a_second_app_that_is_never_served():
    """`whatsapp.py` HA route protette da HTTP Basic. Non sono raggiungibili.

    Il modulo definisce un proprio `app = FastAPI()` con un `HTTPBasic()` e due
    route `/api/admin/...`. Sono un residuo storico: l'applicazione servita e'
    `main:app`, e nessuno importa o monta quella di `whatsapp.py` - `main.py`
    non lo importa affatto e ha le proprie funzioni WhatsApp.

    Questo test non ripulisce quel modulo: rimuoverlo e' una decisione di
    prodotto fuori dal perimetro di P26-5. Fissa la sola cosa che conta per la
    sicurezza - che quella superficie resti non servita - perche' "non montata"
    e' esattamente il genere di condizione che diventa "montata" senza che
    nessuno se ne accorga, e riaprirebbe un canale Basic su dati di tenant.
    """
    whatsapp = (ROOT / "whatsapp.py").read_text(encoding="utf-8")
    # La premessa: quel modulo ha davvero un'app e davvero usa Basic. Se un
    # domani non fosse piu' vero, questo test deve essere riletto, non passare
    # per inerzia.
    assert "app = FastAPI()" in whatsapp
    assert "HTTPBasic()" in whatsapp

    main_source = (ROOT / "main.py").read_text(encoding="utf-8")
    for forbidden in ("import whatsapp", "from whatsapp import",
                      "whatsapp.app", "whatsapp:app"):
        assert forbidden not in main_source, (
            f"main.py raggiunge il modulo whatsapp ({forbidden}): la sua "
            "superficie Basic potrebbe essere diventata servita"
        )

    # E l'app servita non espone le due route di quel modulo con Basic: le
    # omonime in main.py sono passate alla sessione (test 6).
    import main

    paths = main.app.openapi()["paths"]
    assert "/api/admin/stime" in paths
    security = paths["/api/admin/stime"]["get"].get("security")
    assert security and all("APIKeyCookie" in str(entry) for entry in security)


# ---------------------------------------------------------------------------
# 11 - LA REGRESSIONE DEL DEPLOY FALLITO
#
# `tests/test_integration_http_errors_readonly.py::test_invalid_payload_nonpersistent`
# spediva HTTP Basic a POST /api/owner/admin/accounts con un corpo vuoto e
# pretendeva 422. Su Render ha ricevuto 401, ed e' stato l'unico rosso di tutta
# la suite.
#
# Il backend aveva ragione. Dopo P26-5 quella superficie e' dietro
# `require_owner_admin_context`, che e' una dipendenza di router: FastAPI la
# risolve PRIMA di convalidare il corpo, quindi un chiamante senza sessione
# riceve 401 e la validazione non viene mai raggiunta. L'header Basic non viene
# ignorato per distrazione - non esiste piu' nulla che lo legga.
#
# E' anche l'ordine giusto: chi non e' autenticato non deve imparare nulla
# sulla forma del payload. Un 422 a un anonimo racconterebbe quali campi
# esistono e quali sono obbligatori.
#
# Questi tre test esistono perche' quel rosso e' arrivato da Render invece che
# dalla suite locale: i test di integrazione saltano senza PostgreSQL TEST, e
# questa verifica non dipende da nessuno dei due.
# ---------------------------------------------------------------------------

BASIC_HEADER = "Basic " + __import__("base64").b64encode(b"utente:segreto").decode()


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    import main

    return TestClient(main.app)


def test_11_basic_credentials_do_not_open_owner_admin(client):
    """L'header Basic non apre nulla, e non cambia nulla.

    La prova che conta e' la TERZA riga: con un payload valido lo stato e'
    identico. Se il 401 dipendesse dal corpo, vorrebbe dire che la validazione
    viene comunque eseguita prima del rifiuto - e allora il confine non sarebbe
    dove crediamo.
    """
    basic = client.post("/api/owner/admin/accounts", json={},
                        headers={"Authorization": BASIC_HEADER})
    anonymous = client.post("/api/owner/admin/accounts", json={})
    valid_body = client.post("/api/owner/admin/accounts", json={"contact_id": 1},
                             headers={"Authorization": BASIC_HEADER})

    assert basic.status_code == 401, basic.text
    assert anonymous.status_code == 401, anonymous.text
    assert valid_body.status_code == 401, (
        "con un corpo valido la risposta cambia: il rifiuto NON precede la "
        "validazione del payload"
    )
    # Presentare una credenziale Basic non e' meglio che non presentarne alcuna.
    assert basic.status_code == anonymous.status_code
    # E il rifiuto non nomina il canale morto: un 'WWW-Authenticate: Basic'
    # inviterebbe un client a ritentare con le credenziali condivise.
    assert "basic" not in basic.headers.get("www-authenticate", "").lower()


def test_11b_the_refused_request_writes_nothing(client, monkeypatch):
    """Punto 4: la richiesta rifiutata non scrive.

    Non dedotto dallo status - un 401 potrebbe in teoria arrivare dopo un
    effetto - ma osservato: il repository viene sostituito, e se qualcuno lo
    chiamasse il test fallirebbe.

    IL PAYLOAD E' VALIDO, E NON E' UN DETTAGLIO

    Con un corpo vuoto questa prova sarebbe vacua. Se la guardia
    di autenticazione sparisse, la richiesta verrebbe comunque respinta dalla
    validazione - `contact_id` e' obbligatorio - e il repository non sarebbe
    chiamato lo stesso: `calls == []` resterebbe vero per la ragione sbagliata.

    Con `contact_id` valorizzato non c'e' piu' nulla, a valle
    dell'autenticazione, che possa fermare la scrittura. Se il repository non
    viene invocato e' perche' la richiesta e' stata respinta al confine, che e'
    esattamente cio' che questo test afferma.
    """
    import owner.repository

    calls = []
    monkeypatch.setattr(owner.repository, "create_account",
                        lambda *a, **k: calls.append(a) or {"id": 1})

    response = client.post("/api/owner/admin/accounts", json={"contact_id": 1},
                           headers={"Authorization": BASIC_HEADER})

    assert response.status_code == 401
    assert calls == [], f"il repository e' stato invocato da una richiesta rifiutata: {calls}"


def test_11c_an_authorised_session_does_reach_payload_validation(client, monkeypatch):
    """E il 422 non e' sparito: e' dietro la sessione, dove deve stare.

    Senza questo, la correzione del test di integrazione sarebbe indistinguibile
    dall'aver smesso di controllare la validazione del payload.
    """
    from operator_session_helpers import operator_session

    with operator_session(monkeypatch, client, agency_id=1, role="agency_owner"):
        invalid = client.post("/api/owner/admin/accounts", json={})
        assert invalid.status_code == 422, invalid.text
        # E l'errore non rivela nulla del database.
        body = invalid.text.lower()
        for marker in ("psycopg", "postgres", "sqlstate", "traceback"):
            assert marker not in body

    # Un ruolo insufficiente non e' 401 ne' 422: e' 403. La distinzione e'
    # l'intera ragione per cui require_owner_admin_context esiste.
    with operator_session(monkeypatch, client, agency_id=1, role="agent"):
        assert client.post("/api/owner/admin/accounts", json={}).status_code == 403
