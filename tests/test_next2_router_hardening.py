from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from integration_p2_support import import_main_app

ROOT = Path(__file__).resolve().parent.parent
PROTECTED_PREFIXES = (
    "/api/core",
    "/api/property",
    "/api/property-watch",
    "/api/buy",
    "/api/match",
    "/api/proposals",
)
REPRESENTATIVE_PATHS = (
    "/api/core/contacts",
    "/api/property/properties",
    "/api/property-watch/stime/1",
    "/api/buy/requests",
    "/api/match/matches",
    "/api/proposals",
)
# P26-1 Task 12 added the two assignment endpoints - PATCH
# /api/core/{contacts,leads}/{id}/assignment - so CORE goes 19 -> 21 and the
# certified total 106 -> 108. The count is updated only because the *security*
# property below still holds for every operation, including the two new ones:
# both are on the CORE router behind Depends(require_operator), and
# test_2 re-proves that no operation lacks a security gate.
EXPECTED_COUNTS = {
    "/api/core": 21,
    "/api/property": 21,
    "/api/property-watch": 12,
    "/api/buy": 23,
    "/api/match": 26,
    "/api/proposals": 5,
}
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head"}


@pytest.fixture(scope="module")
def app():
    # Use the repository's deterministic import helper.  The full integration
    # suite reloads project modules, so a collection-time ``from main import app``
    # is not a stable reference for structural assertions.
    return import_main_app()


@pytest.fixture(scope="module")
def client(app):
    return TestClient(app, raise_server_exceptions=False)


def openapi_operations(app, prefixes=PROTECTED_PREFIXES):
    operations = []
    for path, item in app.openapi()["paths"].items():
        if not any(path == prefix or path.startswith(f"{prefix}/") for prefix in prefixes):
            continue
        for method, operation in item.items():
            if method.lower() in HTTP_METHODS:
                operations.append((method.upper(), path, operation))
    return operations


def test_1_all_100_routes_are_still_mounted(app):
    operations = openapi_operations(app)
    for prefix, expected in EXPECTED_COUNTS.items():
        actual = sum(
            1
            for _, path, _ in operations
            if path == prefix or path.startswith(f"{prefix}/")
        )
        assert actual == expected
    assert len(operations) == 108


def test_2_every_certified_admin_route_has_security_gate(app):
    operations = openapi_operations(app)
    assert len(operations) == 108
    missing = [f"{method} {path}" for method, path, operation in operations if not operation.get("security")]
    assert missing == []


@pytest.mark.parametrize("path", REPRESENTATIVE_PATHS)
def test_3_anonymous_requests_are_rejected(client, path, monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "giorgio")
    monkeypatch.setenv("ADMIN_PASS", "test-secret")
    response = client.get(path)
    assert response.status_code == 401
    assert response.json() == {"detail": "Non autorizzato"}


@pytest.mark.parametrize("path", REPRESENTATIVE_PATHS)
def test_4_wrong_credentials_are_rejected(client, path, monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "giorgio")
    monkeypatch.setenv("ADMIN_PASS", "test-secret")
    response = client.get(path, auth=("wrong", "password"))
    assert response.status_code == 401


@pytest.mark.parametrize("path", REPRESENTATIVE_PATHS)
def test_5_the_admin_env_no_longer_decides_anything_here(client, path, monkeypatch):
    """P26-5 HA SPOSTATO LA PORTA, E QUESTO TEST DICE DOVE.

    Il 503 significava "il server non ha credenziali amministrative
    configurate": era la risposta giusta finche' queste route vivevano su
    ADMIN_USER/ADMIN_PASS, perche' senza quelle non c'era modo di distinguere
    un amministratore da chiunque altro, e un 401 avrebbe mandato l'operatore a
    cercare il problema sbagliato.

    Adesso queste route non leggono piu' quelle variabili. Toglierle non cambia
    nulla, e il Basic non apre - ne' giusto ne' sbagliato. E' esattamente il
    contenimento che P26-5 doveva ottenere, e senza questo test l'inefficacia
    di quella credenziale non sarebbe provata da nessuna parte.

    Il 503 sopravvive dove sopravvive il canale: `/api/admin/check`, l'unica
    route non-tenant rimasta, e `admin_security.require_admin` che la serve.
    """
    monkeypatch.delenv("ADMIN_USER", raising=False)
    monkeypatch.delenv("ADMIN_PASS", raising=False)
    assert client.get(path, auth=("giorgio", "test-secret")).status_code == 401

    # E con le variabili impostate la risposta e' identica: il Basic non e' una
    # credenziale valida su questa superficie, punto.
    monkeypatch.setenv("ADMIN_USER", "giorgio")
    monkeypatch.setenv("ADMIN_PASS", "test-secret")
    refused = client.get(path, auth=("giorgio", "test-secret"))
    assert refused.status_code == 401
    assert refused.json() == {"detail": "Non autorizzato"}


@pytest.mark.parametrize("path", REPRESENTATIVE_PATHS)
def test_6_a_live_session_passes_the_auth_gate(client, path, monkeypatch):
    """La controprova del test 5: qualcosa deve pur aprire, altrimenti "il
    Basic non apre" sarebbe vero anche su un'API rotta.

    L'identita' e' dichiarata - agenzia e ruolo - invece che implicita come lo
    era in `auth=("giorgio", "test-secret")`, dove entrambe erano decise dal
    server e sempre le stesse.
    """
    from tests.operator_session_helpers import operator_session

    with operator_session(monkeypatch, client, agency_id=1, role="agency_owner"):
        assert client.get(path).status_code not in (401, 403, 503)


@pytest.mark.parametrize("path", REPRESENTATIVE_PATHS)
def test_7_the_unauthorized_response_no_longer_offers_a_basic_challenge(client, path):
    """`WWW-Authenticate: Basic` diceva al browser di aprire il prompt.

    Su una superficie che il Basic non lo accetta piu' sarebbe un invito a
    inserire una credenziale che non apre nulla - e per un umano davanti a un
    prompt di sistema e' peggio di nessun invito. Il messaggio di rifiuto resta
    identico: uno solo per ogni causa.
    """
    response = client.get(path)
    assert response.status_code == 401
    assert response.json() == {"detail": "Non autorizzato"}
    assert response.headers.get("WWW-Authenticate") is None


def test_8_public_stima_and_legacy_admin_check_are_not_put_behind_new_gate(client, monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "giorgio")
    monkeypatch.setenv("ADMIN_PASS", "test-secret")

    stima = client.post("/api/stima_base", json={})
    assert stima.status_code == 400
    assert stima.json() == {"detail": "Dati mancanti"}

    legacy = client.post(
        "/api/admin/check",
        json={"user": "giorgio", "password": "wrong"},
    )
    assert legacy.status_code == 401
    assert "WWW-Authenticate" not in legacy.headers


def test_9_owner_and_flow_do_not_receive_new_neutral_dependency():
    # The contract we own in NEXT.2 is the wiring in main.py.  OWNER and FLOW
    # keep their pre-existing auth mechanisms; they must not be wired to the new
    # neutral require_admin dependency.
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "app.include_router(flow_router, dependencies=[Depends(require_admin)])" not in source
    assert "app.include_router(owner_admin_router, dependencies=[Depends(require_admin)])" not in source
    assert "app.include_router(owner_portal_router, dependencies=[Depends(require_admin)])" not in source


def test_10_domain_routers_remain_decoupled_from_owner():
    # P26-3 added flow: it used to borrow `require_owner_admin` for its mount
    # and now takes `require_authenticated_operator`, so it is decoupled too.
    for module in ("core", "property", "buy", "match", "proposal", "flow"):
        source = (ROOT / module / "router.py").read_text(encoding="utf-8")
        assert "from owner" not in source
        assert "import owner" not in source

    main_source = (ROOT / "main.py").read_text(encoding="utf-8")
    # P26-5: `main.py` non importa piu' `require_admin` perche' non lo usa piu'.
    #
    # La protezione che questo test esiste per garantire non cambia - nessuna
    # di queste superfici e' raggiungibile senza una credenziale - ma la
    # credenziale e' la sessione operatore, e l'import era diventato codice
    # morto. Un import inutilizzato dentro il modulo che monta l'applicazione
    # non e' innocuo: e' una porta pronta a una riga di distanza.
    assert "from admin_security import require_admin" not in main_source
    # P26-1 Task 15: core_router moved from require_admin to require_operator.
    # The protection this test exists to guarantee is unchanged - none of these
    # is reachable without a credential - but the dependency now accepts BOTH
    # approved channels: an operator session cookie, or the same legacy
    # ADMIN_USER/ADMIN_PASS Basic credential as before, verified through the
    # same `admin_security.require_admin`.
    #
    # P26-3 extended that from CORE to the other four. The OS Shell logs in
    # with the cookie and calls all of them, so leaving them on require_admin
    # would have meant a Shell that authenticates and is then refused
    # everywhere but CORE. CORE keeps `require_operator`, which also yields the
    # scope its routes read; the others take the admission-only dependency and
    # get their scope from each route.
    assert (
        "app.include_router(core_router, dependencies=[Depends(require_operator)])"
        in main_source
    )
    for router_name in ("property_router", "buy_router", "match_router", "proposal_router"):
        expected = (
            f"app.include_router({router_name}, "
            "dependencies=[Depends(require_authenticated_operator)])"
        )
        assert expected in main_source
