from __future__ import annotations

import ast
import base64
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from owner import repository as repo
from owner.router_admin import require_owner_admin, router as admin_router
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


def test_every_owner_admin_api_route_has_server_side_auth_dependency():
    """Verify router-wide auth and the 38 declarations without shared-router state.

    Route enumeration is intentionally source/AST based, matching the robust P4
    approach.  This test therefore does not re-include or traverse a shared
    ``APIRouter`` instance that another test may already have mutated.  Runtime
    enforcement is covered by the dedicated anonymous/authenticated tests above.
    """

    # 1. Live router configuration: the OWNER Admin router carries Basic auth
    # globally, at the router level, rather than relying on individual handlers.
    assert admin_router.prefix == "/api/owner/admin"
    assert any(
        getattr(dependency, "dependency", None) is require_owner_admin
        for dependency in admin_router.dependencies
    )

    # 2. Enumerate the declared route surface directly from source.  This is
    # deterministic across test order and avoids FastAPI ``route.dependant``
    # internals as well as re-including the shared router object.
    router_path = Path(__file__).parents[1] / "owner/router_admin.py"
    source = router_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(router_path))

    declared_prefix = None
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "router"
            for target in node.targets
        ):
            continue
        if not isinstance(node.value, ast.Call):
            continue
        if not (
            isinstance(node.value.func, ast.Name)
            and node.value.func.id == "APIRouter"
        ):
            continue
        for keyword in node.value.keywords:
            if (
                keyword.arg == "prefix"
                and isinstance(keyword.value, ast.Constant)
                and isinstance(keyword.value.value, str)
            ):
                declared_prefix = keyword.value.value
                break

    assert declared_prefix == admin_router.prefix == "/api/owner/admin"

    supported_methods = {"get", "post", "patch", "put", "delete"}
    declared_routes: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        for decorator in getattr(node, "decorator_list", ()):
            if not isinstance(decorator, ast.Call):
                continue
            func = decorator.func
            if not (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "router"
                and func.attr in supported_methods
                and decorator.args
                and isinstance(decorator.args[0], ast.Constant)
                and isinstance(decorator.args[0].value, str)
            ):
                continue
            full_path = declared_prefix + decorator.args[0].value
            declared_routes.append((func.attr.upper(), full_path))

    assert len(declared_routes) == 38
    assert len(declared_routes) == len(set(declared_routes))
    assert all(path.startswith("/api/owner/admin/") for _, path in declared_routes)


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
