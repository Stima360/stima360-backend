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
EXPECTED_COUNTS = {
    "/api/core": 19,
    "/api/property": 21,
    "/api/property-watch": 6,
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
    assert len(operations) == 100


def test_2_every_certified_admin_route_has_security_gate(app):
    operations = openapi_operations(app)
    assert len(operations) == 100
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
def test_5_missing_admin_env_fails_closed(client, path, monkeypatch):
    monkeypatch.delenv("ADMIN_USER", raising=False)
    monkeypatch.delenv("ADMIN_PASS", raising=False)
    response = client.get(path, auth=("giorgio", "test-secret"))
    assert response.status_code == 503
    assert response.json() == {"detail": "Servizio amministrativo non disponibile"}


@pytest.mark.parametrize("path", REPRESENTATIVE_PATHS)
def test_6_correct_credentials_pass_the_auth_gate(client, path, monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "giorgio")
    monkeypatch.setenv("ADMIN_PASS", "test-secret")
    response = client.get(path, auth=("giorgio", "test-secret"))
    assert response.status_code not in (401, 503)


@pytest.mark.parametrize("path", REPRESENTATIVE_PATHS)
def test_7_unauthorized_response_has_basic_challenge(client, path, monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "giorgio")
    monkeypatch.setenv("ADMIN_PASS", "test-secret")
    response = client.get(path)
    assert response.status_code == 401
    assert response.headers.get("WWW-Authenticate") == 'Basic realm="STIMA360 Admin"'


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
    for module in ("core", "property", "buy", "match", "proposal"):
        source = (ROOT / module / "router.py").read_text(encoding="utf-8")
        assert "from owner" not in source
        assert "import owner" not in source

    main_source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "from admin_security import require_admin" in main_source
    for router_name in ("core_router", "property_router", "buy_router", "match_router", "proposal_router"):
        expected = f"app.include_router({router_name}, dependencies=[Depends(require_admin)])"
        assert expected in main_source
