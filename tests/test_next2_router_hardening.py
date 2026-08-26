from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from main import app

ROOT = Path(__file__).resolve().parent.parent
PROTECTED_PREFIXES = ("/api/core", "/api/property", "/api/buy", "/api/match")
REPRESENTATIVE_PATHS = (
    "/api/core/contacts",
    "/api/property/properties",
    "/api/buy/requests",
    "/api/match/matches",
)
EXPECTED_COUNTS = {
    "/api/core": 19,
    "/api/property": 21,
    "/api/buy": 23,
    "/api/match": 25,
}

client = TestClient(app, raise_server_exceptions=False)


def api_routes():
    # Avoid isinstance(APIRoute): the integration suite deliberately reloads
    # project modules, and route class identity is not a stable contract across
    # those import cycles.  Identify FastAPI operation routes by the attributes
    # required by the checks below; this excludes static Mount routes.
    return [
        route
        for route in app.routes
        if getattr(route, "path", None)
        and getattr(route, "methods", None)
        and hasattr(route, "dependant")
    ]


def protected_routes():
    return [
        route
        for route in api_routes()
        if route.path.startswith(PROTECTED_PREFIXES)
    ]


def dependency_calls(route):
    return [dependency.call for dependency in route.dependant.dependencies]


def test_1_all_88_routes_are_still_mounted():
    routes = api_routes()
    for prefix, expected in EXPECTED_COUNTS.items():
        assert len([route for route in routes if route.path.startswith(prefix)]) == expected
    assert len(protected_routes()) == 88


def test_2_every_certified_admin_route_has_neutral_admin_dependency():
    from admin_security import require_admin

    missing = [
        f"{','.join(sorted(route.methods))} {route.path}"
        for route in protected_routes()
        if require_admin not in dependency_calls(route)
    ]
    assert missing == []


@pytest.mark.parametrize("path", REPRESENTATIVE_PATHS)
def test_3_anonymous_requests_are_rejected(path, monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "giorgio")
    monkeypatch.setenv("ADMIN_PASS", "test-secret")
    response = client.get(path)
    assert response.status_code == 401
    assert response.json() == {"detail": "Non autorizzato"}


@pytest.mark.parametrize("path", REPRESENTATIVE_PATHS)
def test_4_wrong_credentials_are_rejected(path, monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "giorgio")
    monkeypatch.setenv("ADMIN_PASS", "test-secret")
    response = client.get(path, auth=("wrong", "password"))
    assert response.status_code == 401


@pytest.mark.parametrize("path", REPRESENTATIVE_PATHS)
def test_5_missing_admin_env_fails_closed(path, monkeypatch):
    monkeypatch.delenv("ADMIN_USER", raising=False)
    monkeypatch.delenv("ADMIN_PASS", raising=False)
    response = client.get(path, auth=("giorgio", "test-secret"))
    assert response.status_code == 503
    assert response.json() == {"detail": "Servizio amministrativo non disponibile"}


@pytest.mark.parametrize("path", REPRESENTATIVE_PATHS)
def test_6_correct_credentials_pass_the_auth_gate(path, monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "giorgio")
    monkeypatch.setenv("ADMIN_PASS", "test-secret")
    response = client.get(path, auth=("giorgio", "test-secret"))
    assert response.status_code not in (401, 503)


@pytest.mark.parametrize("path", REPRESENTATIVE_PATHS)
def test_7_unauthorized_response_has_basic_challenge(path, monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "giorgio")
    monkeypatch.setenv("ADMIN_PASS", "test-secret")
    response = client.get(path)
    assert response.status_code == 401
    assert response.headers.get("WWW-Authenticate") == 'Basic realm="STIMA360 Admin"'


def test_8_public_stima_and_legacy_admin_check_are_not_put_behind_new_gate(monkeypatch):
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
    from admin_security import require_admin

    unrelated = [
        route
        for route in api_routes()
        if route.path.startswith(("/api/owner", "/api/flow"))
    ]
    assert unrelated
    assert all(require_admin not in dependency_calls(route) for route in unrelated)


def test_10_domain_routers_remain_decoupled_from_owner():
    for module in ("core", "property", "buy", "match"):
        source = (ROOT / module / "router.py").read_text(encoding="utf-8")
        assert "from owner" not in source
        assert "import owner" not in source

    main_source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "from admin_security import require_admin" in main_source
