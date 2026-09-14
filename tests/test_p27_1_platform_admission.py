"""P27-1 - l'ammissione alla superficie Platform.

Cosa prova questo file, in una riga: che `/api/platform` e' una porta diversa
da quelle del tenant, che si apre solo per `is_platform_admin`, e che ogni
apertura e ogni rifiuto lasciano una riga in `platform_audit_log`.

Mappa:

    A1-A3   chi NON entra, e con quale codice
    A4-A5   chi entra, incluso il caso D4 (platform admin CON membership)
    A6      l'ammissione e' registrata UNA volta, non due
    A7-A9   cosa succede quando l'audit non e' scrivibile
    A10     cosa finisce in metadata, e cosa no
    A11-A13 il contratto di risposta e il montaggio
    A14-A16 le proprieta' strutturali che tengono separate le due superfici

Nessun database: la sessione e il writer di audit sono sostituiti, e cio' che
si asserisce e' la decisione presa dal codice di produzione.
"""
from __future__ import annotations

import ast
import inspect
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from operator_auth.context import OperatorContext
from operator_auth.dependencies import AuthenticatedSession
from platform_admin import audit as platform_audit
from platform_admin import dependencies as platform_deps
from platform_admin.enums import (
    ACTION_ADMISSION,
    RESULT_DENIED,
    RESULT_SUCCESS,
    ROUTER_PREFIX,
)
from platform_admin.exceptions import PlatformAuditUnavailable
from platform_admin.router import router as platform_router

ROOT = Path(__file__).resolve().parents[1]

PLATFORM_USER = 77
AGENCY = 4242
EXPIRES = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)


def _ctx(**overrides) -> OperatorContext:
    base = dict(
        user_id=PLATFORM_USER,
        agency_id=None,
        role=None,
        is_platform_admin=True,
        session_id=1,
        auth_channel="operator_session",
    )
    base.update(overrides)
    return OperatorContext(**base)


def _platform_admin_unbound() -> OperatorContext:
    return _ctx()


def _platform_admin_bound() -> OperatorContext:
    """D4: la stessa identita' e' platform admin E titolare di un'agenzia."""
    return _ctx(agency_id=AGENCY, role="agency_owner")


def _agency_owner() -> OperatorContext:
    return _ctx(agency_id=AGENCY, role="agency_owner", is_platform_admin=False)


def _agent() -> OperatorContext:
    return _ctx(agency_id=AGENCY, role="agent", is_platform_admin=False)


@pytest.fixture
def platform_app(monkeypatch):
    """L'app montata come `main.py` la monta, senza database.

    `state["session"]` decide chi sta chiamando; `state["audit"]` raccoglie
    ogni chiamata al writer; `state["audit_fails"]` lo fa fallire.
    """
    state = {"session": None, "audit": [], "audit_fails": False}

    def _resolve(_token):
        context = state["session"]
        if context is None:
            return None
        return {
            "context": context,
            "agency_name": "Agenzia di prova",
            "expires_at": EXPIRES,
        }

    # Sostituita la risoluzione della sessione, non la dipendenza: cosi' la
    # precedenza, il fail-closed e la cache per richiesta restano quelli veri.
    from operator_auth import dependencies as operator_deps

    monkeypatch.setattr(operator_deps.service, "session_from_token", _resolve)

    def _record(**kwargs):
        state["audit"].append(kwargs)
        if state["audit_fails"]:
            raise PlatformAuditUnavailable("indisponibile")
        return len(state["audit"])

    monkeypatch.setattr(platform_deps.audit, "record", _record)

    app = FastAPI()
    app.include_router(
        platform_router,
        dependencies=[Depends(platform_deps.require_platform_admin)],
    )
    client = TestClient(app, raise_server_exceptions=False)
    client.cookies.set("stima360_operator_session", "un-token")
    return client, state


# ---------------------------------------------------------------------------
# A1-A3 - chi non entra
# ---------------------------------------------------------------------------

def test_a1_no_session_is_401_and_writes_no_audit_row(platform_app):
    """401, e nessuna riga.

    Non e' una dimenticanza: registrare una richiesta anonima darebbe a
    chiunque, senza autenticarsi, un modo per scrivere in una tabella
    append-only. Vedi il docstring di platform_admin/dependencies.py.
    """
    client, state = platform_app
    client.cookies.clear()
    response = client.get(f"{ROUTER_PREFIX}/me")
    assert response.status_code == 401, response.text
    assert state["audit"] == [], state["audit"]


def test_a2_an_agency_owner_is_403_and_is_recorded_as_denied(platform_app):
    client, state = platform_app
    state["session"] = _agency_owner()
    response = client.get(f"{ROUTER_PREFIX}/me")
    assert response.status_code == 403, response.text
    assert len(state["audit"]) == 1, state["audit"]
    entry = state["audit"][0]
    assert entry["result"] == RESULT_DENIED
    assert entry["action"] == ACTION_ADMISSION
    assert entry["actor"].user_id == PLATFORM_USER


def test_a3_an_agent_is_403_too(platform_app):
    client, state = platform_app
    state["session"] = _agent()
    response = client.get(f"{ROUTER_PREFIX}/me")
    assert response.status_code == 403, response.text
    assert state["audit"][0]["result"] == RESULT_DENIED


def test_a3_the_refusal_is_403_and_never_404(platform_app):
    """403 e non 404: l'endpoint esiste e il chiamante lo sa gia'.

    404 e' riservato a nascondere i record di un'altra agenzia - una regola di
    tenant. Qui non si nasconde niente, si rifiuta.
    """
    client, state = platform_app
    state["session"] = _agency_owner()
    assert client.get(f"{ROUTER_PREFIX}/me").status_code == 403


# ---------------------------------------------------------------------------
# A4-A5 - chi entra
# ---------------------------------------------------------------------------

def test_a4_an_unbound_platform_admin_is_admitted_and_recorded(platform_app):
    client, state = platform_app
    state["session"] = _platform_admin_unbound()
    response = client.get(f"{ROUTER_PREFIX}/me")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "user_id": PLATFORM_USER,
        "is_platform_admin": True,
        "agency_id": None,
        "session_expires_at": EXPIRES.isoformat().replace("+00:00", "Z"),
        # P28. Questo amministratore non sta operando dentro nessuna agenzia,
        # ed e' cosi' che lo dice: due `None`, non l'assenza dei campi.
        "acting_agency_id": None,
        "acting_entered_at": None,
    }
    assert len(state["audit"]) == 1
    assert state["audit"][0]["result"] == RESULT_SUCCESS


def test_a5_d4_a_platform_admin_holding_a_membership_is_admitted(platform_app):
    """Decisione D4: il flag basta, la membership non squalifica.

    La stessa identita' puo' essere platform admin qui e agency_owner nella
    superficie tenant. A separarle e' l'endpoint, non l'obbligo di avere due
    account - e non e' un allargamento, perche' D1 ha tolto dalla superficie
    tenant l'unico accesso globale implicito che esisteva.
    """
    client, state = platform_app
    state["session"] = _platform_admin_bound()
    response = client.get(f"{ROUTER_PREFIX}/me")
    assert response.status_code == 200, response.text
    assert response.json()["agency_id"] == AGENCY
    assert response.json()["is_platform_admin"] is True


# ---------------------------------------------------------------------------
# A6 - una riga per richiesta
# ---------------------------------------------------------------------------

def test_a6_the_admission_is_audited_once_not_once_per_declaration(platform_app):
    """La dipendenza e' dichiarata sul mount E nella route.

    FastAPI risolve una dipendenza una volta sola per richiesta (cache per
    callable), quindi le due dichiarazioni costano una risoluzione di sessione
    e UNA riga di audit. Se un giorno la cache non valesse piu' - una firma
    diversa fra le due dichiarazioni la disattiverebbe - questo test lo dice
    subito, invece di lasciare che il registro raddoppi in silenzio.
    """
    client, state = platform_app
    state["session"] = _platform_admin_unbound()
    assert client.get(f"{ROUTER_PREFIX}/me").status_code == 200
    assert len(state["audit"]) == 1, state["audit"]


# ---------------------------------------------------------------------------
# A7-A9 - quando l'audit non e' scrivibile
# ---------------------------------------------------------------------------

def test_a7_an_unwritable_audit_stops_an_admitted_request_with_503(platform_app):
    """Un atto amministrativo che non si riesce a registrare non avviene.

    La regola si stabilisce adesso, mentre l'unica operazione della superficie
    e' una lettura innocua, proprio perche' dopo P27-2 non sarebbe piu' il
    momento di discuterla.
    """
    client, state = platform_app
    state["session"] = _platform_admin_unbound()
    state["audit_fails"] = True
    response = client.get(f"{ROUTER_PREFIX}/me")
    assert response.status_code == 503, response.text


def test_a7_the_503_body_is_the_constant_message_and_leaks_no_internal_detail(
    platform_app,
):
    """Il corpo e' la costante, non il messaggio dell'eccezione.

    Il writer solleva con un testo suo (qui "indisponibile"), e quel testo in
    produzione contiene cio' che psycopg2 ha da dire: nomi di oggetti, valori,
    a volte un frammento di istruzione. Nessuno di questi deve raggiungere il
    chiamante.
    """
    from platform_admin.enums import AUDIT_UNAVAILABLE_MESSAGE

    client, state = platform_app
    state["session"] = _platform_admin_unbound()
    state["audit_fails"] = True
    response = client.get(f"{ROUTER_PREFIX}/me")

    assert response.status_code == 503
    assert response.json() == {"detail": AUDIT_UNAVAILABLE_MESSAGE}
    body = response.text
    assert "indisponibile" not in body, body
    assert "platform_audit_log" not in body, body
    assert "psycopg" not in body.lower(), body


def test_a8_an_unwritable_audit_does_not_turn_a_refusal_into_a_500(platform_app):
    """Il rifiuto resta un rifiuto.

    Non stava concedendo nulla, e trasformarlo in un 503 racconterebbe al
    chiamante respinto qualcosa sullo stato interno del server.
    """
    client, state = platform_app
    state["session"] = _agency_owner()
    state["audit_fails"] = True
    response = client.get(f"{ROUTER_PREFIX}/me")
    assert response.status_code == 403, response.text


def test_a9_an_unauthenticated_request_never_reaches_the_writer(platform_app):
    """Controllo negativo di A1: il writer non viene proprio chiamato.

    Asserire solo "nessuna riga" non distinguerebbe fra "non chiamato" e
    "chiamato e fallito".
    """
    client, state = platform_app
    client.cookies.clear()
    state["audit_fails"] = True
    assert client.get(f"{ROUTER_PREFIX}/me").status_code == 401
    assert state["audit"] == []


# ---------------------------------------------------------------------------
# A10 - cosa finisce in metadata
# ---------------------------------------------------------------------------

def test_a10_metadata_carries_the_path_and_method_and_not_the_query_string(
    platform_app,
):
    """La query string non entra nel registro.

    Puo' portare filtri, identificatori e - un giorno, per errore di qualcun
    altro - un token. Una tabella append-only e' il posto peggiore in cui far
    finire una stringa che nessuno ha guardato.
    """
    client, state = platform_app
    state["session"] = _platform_admin_unbound()
    client.get(f"{ROUTER_PREFIX}/me?token=segretissimo&filtro=x")
    metadata = state["audit"][0]["metadata"]
    assert metadata == {"path": f"{ROUTER_PREFIX}/me", "method": "GET"}
    assert "segretissimo" not in str(state["audit"])


# ---------------------------------------------------------------------------
# A11-A13 - contratto e montaggio
# ---------------------------------------------------------------------------

def test_a11_the_response_projects_six_fields_and_no_personal_datum():
    """I quattro di P27-1, piu' i due di P28. Nessun dato personale, ancora.

    P28 aggiunge SE si sta operando dentro un'agenzia e da quando. Non
    aggiunge il nome di quell'agenzia: la proiezione non ha mai portato un
    `agency_name`, e chi deve scriverlo sullo schermo lo prende da
    `/api/operator-auth/me`.
    """
    from platform_admin.schemas import PlatformMeResponse

    fields = set(PlatformMeResponse.model_fields)
    assert fields == {
        "user_id",
        "is_platform_admin",
        "agency_id",
        "session_expires_at",
        "acting_agency_id",
        "acting_entered_at",
    }, fields
    # Ne' l'email ne' il nome dell'agenzia: /me di operator-auth esclude gia'
    # deliberatamente l'email, e questa superficie non la reintroduce.
    assert "email" not in fields
    assert "agency_name" not in fields


def test_a12_main_mounts_the_platform_router_behind_require_platform_admin():
    """Letto dall'AST di main.py, non da una lista in questo file."""
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    mounts = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "include_router"
            and node.args
        ):
            symbol = ast.unparse(node.args[0])
            keywords = {kw.arg: ast.unparse(kw.value) for kw in node.keywords}
            mounts[symbol] = keywords.get("dependencies", "")
    assert "platform_router" in mounts, sorted(mounts)
    assert mounts["platform_router"] == "[Depends(require_platform_admin)]", mounts[
        "platform_router"
    ]


def test_a13_the_platform_prefix_is_its_own_and_is_not_a_core_subpath():
    """P27-2: IL PERNO SUL NUMERO DI ROUTE SI E' SPOSTATO, NON E' STATO TOLTO.

    Questo test asseriva `paths == {"/api/platform/me"}`. Era vero quando P27-1
    era tutta la superficie, e ha smesso di esserlo con le quattro route di
    P27-2 - come smettera' di esserlo a ogni fase successiva. Un perno sulla
    DIMENSIONE della superficie, dentro il file della prima fase, genera un
    fallimento a ogni fase che la allarga: rumore, non sorveglianza.

    Cio' che questo file possiede resta asserito qui: il prefisso e' suo, non
    e' un sotto-percorso di CORE, e `/me` c'e'. L'elenco esaustivo di cosa la
    superficie espone appartiene alla fase che ce lo mette, ed e'
    tests/test_p27_2_agencies.py::test_c8_the_real_application_exposes_exactly_the_four_p27_2_routes.
    """
    assert platform_router.prefix == "/api/platform"
    assert not platform_router.prefix.startswith("/api/core")

    paths = {route.path for route in platform_router.routes}
    assert "/api/platform/me" in paths, paths
    # Nessuna route di questo router puo' finire fuori dal proprio prefisso:
    # e' questa la separazione fra le due superfici, e vale per una route come
    # per venti.
    for path in paths:
        assert path.startswith("/api/platform/"), path


# ---------------------------------------------------------------------------
# A14-A16 - le proprieta' strutturali che tengono separate le due superfici
# ---------------------------------------------------------------------------

TENANT_DEPENDENCIES = (
    "require_operator",
    "require_authenticated_operator",
    "legacy_basic_agency_context",
    "require_owner_admin_context",
)


def test_a14_no_platform_module_declares_a_tenant_dependency():
    """La superficie Platform non e' una route di tenant con piu' privilegi.

    Se un giorno una route di `/api/platform` dichiarasse una dipendenza di
    tenant, riceverebbe uno scope di agenzia - e la tentazione successiva,
    passarlo a un repository di tenant, sarebbe a una riga di distanza.
    """
    for path in sorted((ROOT / "platform_admin").glob("*.py")):
        source = path.read_text(encoding="utf-8")
        # Le dipendenze vanno cercate nel codice, non nei commenti: questo file
        # e i docstring del package le nominano di proposito.
        tree = ast.parse(source)
        used = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        } | {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        for forbidden in TENANT_DEPENDENCIES:
            assert forbidden not in used, f"{path.name} usa {forbidden}"


def test_a15_the_platform_package_imports_no_tenant_query_builder():
    """Nessun import diretto di cio' che costruisce una query di tenant.

    `core.scope` e `core.repository` sono il modo in cui si legge la tabella di
    un'agenzia. P27-1 non li importa da nessuna parte, e non perche' non
    servano ancora: perche' quando serviranno dovra' essere una decisione, non
    un import gia' presente.
    """
    forbidden_modules = {"core.scope", "core.repository", "core.service"}
    for path in sorted((ROOT / "platform_admin").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert node.module not in forbidden_modules, (
                    f"{path.name} importa {node.module}"
                )
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name not in forbidden_modules, (
                        f"{path.name} importa {alias.name}"
                    )


def test_a16_the_denial_row_names_the_real_operator_not_a_constant(platform_app):
    """Un audit trail che registra sempre lo stesso attore non e' un audit trail.

    E' la stessa ragione per cui P26-5 sostitui' lo username Basic con
    `operator:<user_id>`.
    """
    client, state = platform_app
    state["session"] = _agency_owner()
    client.get(f"{ROUTER_PREFIX}/me")
    actor = state["audit"][0]["actor"]
    assert actor.user_id == PLATFORM_USER
    assert platform_audit.actor_label(actor) == f"operator:{PLATFORM_USER}"


def test_a16_the_dependency_is_not_a_thin_alias_of_an_operator_dependency():
    """Controllo strutturale: la firma e il corpo sono suoi.

    Un `require_platform_admin = require_operator` passerebbe molti dei test
    sopra e vanificherebbe tutto il resto.
    """
    from operator_auth.dependencies import require_operator

    assert platform_deps.require_platform_admin is not require_operator
    source = inspect.getsource(platform_deps.require_platform_admin)
    assert "is_platform_admin" in source
    assert "403" in source and "401" in source and "503" in source


# ---------------------------------------------------------------------------
# A17 - la prova sull'applicazione VERA, non su una sua ricostruzione
# ---------------------------------------------------------------------------

@pytest.fixture
def real_app(monkeypatch):
    """`main.app` cosi' com'e', con la sessione e il writer sostituiti.

    Tutti i test qui sopra montano il router in un'app costruita da questo
    file. E' il modo giusto di isolare la dipendenza, ma non prova che
    `main.py` monti davvero quella dipendenza su quel router: un'app di prova
    montata bene e un `main.py` montato male sono compatibili con ogni singola
    asserzione precedente.

    `test_a12` copre la stessa cosa leggendo l'AST, ed e' la prova che regge
    anche se l'app non si riesce a costruire. Questa e' l'altra meta': la
    richiesta vera, sul mount vero, con il codice HTTP vero.
    """
    from operator_auth import dependencies as operator_deps

    state = {"session": None, "audit": []}

    def _resolve(_token):
        if state["session"] is None:
            return None
        return {
            "context": state["session"],
            "agency_name": "Agenzia di prova",
            "expires_at": EXPIRES,
        }

    monkeypatch.setattr(operator_deps.service, "session_from_token", _resolve)
    monkeypatch.setattr(
        platform_deps.audit,
        "record",
        lambda **kwargs: state["audit"].append(kwargs) or 1,
    )

    from main import app

    client = TestClient(app, raise_server_exceptions=False)
    client.cookies.set("stima360_operator_session", "un-token")
    return client, state


def test_a17_the_real_application_exposes_the_platform_surface():
    """P27-2: IL PERNO SUL NUMERO DI ROUTE SI E' SPOSTATO, NON E' STATO TOLTO.

    Questo test asseriva `paths == {"/api/platform/me"}`. Era vero quando P27-1
    era tutta la superficie, e ha smesso di esserlo con le quattro route di
    P27-2 - come smettera' di esserlo a ogni fase successiva. Un perno sulla
    DIMENSIONE della superficie, dentro il file della prima fase, genera un
    fallimento a ogni fase che la allarga: rumore, non sorveglianza.

    Cio' che questo file possiede resta asserito qui: il prefisso e' suo, non
    e' un sotto-percorso di CORE, e `/me` c'e'. L'elenco esaustivo di cosa la
    superficie espone appartiene alla fase che ce lo mette, ed e'
    tests/test_p27_2_agencies.py::test_c8_the_real_application_exposes_exactly_the_four_p27_2_routes.
    """
    from main import app

    paths = sorted(p for p in app.openapi()["paths"] if p.startswith("/api/platform"))
    assert "/api/platform/me" in paths, paths
    assert paths, "la superficie platform non e' montata"


def test_a17_the_real_application_refuses_an_agency_owner_with_403(real_app):
    client, state = real_app
    state["session"] = _agency_owner()
    response = client.get(f"{ROUTER_PREFIX}/me")
    assert response.status_code == 403, response.text
    assert state["audit"] and state["audit"][0]["result"] == RESULT_DENIED


def test_a17_the_real_application_admits_a_platform_admin(real_app):
    client, state = real_app
    state["session"] = _platform_admin_unbound()
    response = client.get(f"{ROUTER_PREFIX}/me")
    assert response.status_code == 200, response.text
    assert response.json()["is_platform_admin"] is True
    assert len(state["audit"]) == 1, state["audit"]


def test_a17_the_real_application_refuses_an_anonymous_caller_with_401(real_app):
    client, state = real_app
    client.cookies.clear()
    assert client.get(f"{ROUTER_PREFIX}/me").status_code == 401
    assert state["audit"] == []


def test_a17_a_platform_admin_reaches_no_tenant_data_through_core(real_app):
    """D1 e P27-1 insieme, sull'applicazione vera.

    La stessa sessione che apre `/api/platform/me` non apre `/api/core/contacts`:
    e' la frase del principio del progetto - la superficie Platform amministra
    la rete, la superficie tenant serve una agenzia - resa osservabile su una
    richiesta HTTP.

    403 e non 200 vuoto: se CORE rispondesse 200 con zero righe, la differenza
    fra "non ha dati" e "non ha accesso" andrebbe persa, ed e' quella la
    differenza che D1 stabilisce.
    """
    client, state = real_app
    state["session"] = _platform_admin_unbound()
    assert client.get(f"{ROUTER_PREFIX}/me").status_code == 200
    assert client.get("/api/core/contacts").status_code == 403
