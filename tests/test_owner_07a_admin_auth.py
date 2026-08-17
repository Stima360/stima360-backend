from __future__ import annotations

import base64
import re

from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from owner import repository as repo
from owner.router_admin import router as admin_router
from owner.router_portal import router as portal_router


def _basic(user: str, password: str) -> dict[str, str]:
    encoded = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {encoded}"}


def _admin_app() -> FastAPI:
    app = FastAPI()
    app.include_router(admin_router)
    return app


def test_owner_admin_dashboard_denies_anonymous_before_repository(monkeypatch):
    called = False

    def fake_dashboard():
        nonlocal called
        called = True
        return {"ok": True}

    monkeypatch.setenv("ADMIN_USER", "giorgio")
    monkeypatch.setenv("ADMIN_PASS", "test-secret")
    monkeypatch.setattr(repo, "dashboard", fake_dashboard)

    response = TestClient(_admin_app()).get("/api/owner/admin/dashboard")

    assert response.status_code == 401
    assert response.json() == {"detail": "Non autorizzato"}
    assert response.headers["www-authenticate"] == 'Basic realm="STIMA360 OWNER Admin"'
    assert called is False


def test_owner_admin_other_route_denies_anonymous_before_repository(monkeypatch):
    called = False

    def fake_accounts():
        nonlocal called
        called = True
        return []

    monkeypatch.setenv("ADMIN_USER", "giorgio")
    monkeypatch.setenv("ADMIN_PASS", "test-secret")
    monkeypatch.setattr(repo, "list_accounts", fake_accounts)

    response = TestClient(_admin_app()).get("/api/owner/admin/accounts")

    assert response.status_code == 401
    assert called is False


def test_owner_admin_valid_credentials_allow_access(monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "giorgio")
    monkeypatch.setenv("ADMIN_PASS", "test-secret")
    monkeypatch.setattr(repo, "dashboard", lambda: {"active_accounts": 2})

    response = TestClient(_admin_app()).get(
        "/api/owner/admin/dashboard",
        headers=_basic("giorgio", "test-secret"),
    )

    assert response.status_code == 200
    assert response.json() == {"active_accounts": 2}


def test_owner_admin_wrong_credentials_are_denied(monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "giorgio")
    monkeypatch.setenv("ADMIN_PASS", "test-secret")
    monkeypatch.setattr(repo, "dashboard", lambda: {"should_not": "run"})

    client = TestClient(_admin_app())
    wrong_password = client.get(
        "/api/owner/admin/dashboard",
        headers=_basic("giorgio", "wrong"),
    )
    wrong_user = client.get(
        "/api/owner/admin/dashboard",
        headers=_basic("other", "test-secret"),
    )

    assert wrong_password.status_code == 401
    assert wrong_user.status_code == 401
    assert wrong_password.json() == {"detail": "Non autorizzato"}
    assert wrong_user.json() == {"detail": "Non autorizzato"}


def test_owner_admin_missing_server_credentials_fail_closed(monkeypatch):
    monkeypatch.delenv("ADMIN_USER", raising=False)
    monkeypatch.delenv("ADMIN_PASS", raising=False)

    response = TestClient(_admin_app()).get(
        "/api/owner/admin/dashboard",
        headers=_basic("anything", "anything"),
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Servizio amministrativo non disponibile"}


def _concrete_admin_path(path: str) -> str:
    # Anonymous auth must be rejected before path/body/query validation.  Any
    # syntactically valid placeholder value is sufficient for this proof.
    return re.sub(r"\{[^}]+\}", "1", path)


def test_every_owner_admin_api_route_has_server_side_auth_dependency(monkeypatch):
    """Behaviorally prove that all OWNER Admin routes are protected.

    Do not rely on FastAPI's private ``Dependant`` object layout: router-level
    dependencies have changed representation across FastAPI versions even when
    request-time security semantics are identical.
    """

    monkeypatch.setenv("ADMIN_USER", "giorgio")
    monkeypatch.setenv("ADMIN_PASS", "test-secret")

    app = _admin_app()
    client = TestClient(app)
    routes = [
        route
        for route in app.routes
        if isinstance(route, APIRoute) and route.path.startswith("/api/owner/admin")
    ]

    assert len(routes) == 38
    for route in routes:
        method = next(
            method
            for method in sorted(route.methods)
            if method not in {"HEAD", "OPTIONS"}
        )
        response = client.request(method, _concrete_admin_path(route.path))
        assert response.status_code == 401, f"Auth mancante: {method} {route.path}"
        assert response.json() == {"detail": "Non autorizzato"}
        assert response.headers.get("www-authenticate") == (
            'Basic realm="STIMA360 OWNER Admin"'
        )


def test_owner_portal_routes_do_not_inherit_admin_basic_auth(monkeypatch):
    """Prove portal routing remains independent from OWNER Admin HTTP Basic."""

    monkeypatch.setenv("ADMIN_USER", "giorgio")
    monkeypatch.setenv("ADMIN_PASS", "test-secret")

    app = FastAPI()
    app.include_router(admin_router)
    app.include_router(portal_router)
    client = TestClient(app)

    # This portal request is deliberately malformed.  Portal validation must
    # answer 422; if admin HTTP Basic leaked onto portal routes it would answer
    # 401 with the Basic challenge before body validation.
    response = client.post(
        "/api/owner/portal/auth/token",
        json={"token": "too-short"},
    )

    assert response.status_code == 422
    assert response.headers.get("www-authenticate") is None


def test_auth_hardening_uses_existing_env_credentials_without_browser_session_storage():
    source = __import__("pathlib").Path(__file__).parents[1].joinpath("owner/router_admin.py").read_text()
    assert 'os.getenv("ADMIN_USER")' in source
    assert 'os.getenv("ADMIN_PASS")' in source
    assert "secrets.compare_digest" in source
    assert "HTTPBasic(auto_error=False)" in source
    for forbidden in (
        "localStorage",
        "sessionStorage",
        "document.cookie",
        "set_cookie(",
        "storage_key",
    ):
        assert forbidden not in source
