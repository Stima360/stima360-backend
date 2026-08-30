import ast
import base64
import importlib.util
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parent.parent
SECURITY_PATH = ROOT / "admin_security.py"


def _load_security_module():
    assert SECURITY_PATH.exists(), "admin_security.py must exist"
    spec = importlib.util.spec_from_file_location("admin_security", SECURITY_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _basic_header(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def _make_client(require_admin):
    app = FastAPI()
    state = {"business_logic_executed": False}

    @app.get("/test-admin")
    def admin_endpoint(username: str = Depends(require_admin)):
        state["business_logic_executed"] = True
        return {"user": username}

    return TestClient(app), state


@pytest.fixture(autouse=True)
def admin_env(monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "giorgio")
    monkeypatch.setenv("ADMIN_PASS", "test-secret")


def test_1_anonymous_returns_401_with_basic_realm():
    security = _load_security_module()
    client, _ = _make_client(security.require_admin)

    response = client.get("/test-admin")

    assert response.status_code == 401
    assert response.json() == {"detail": "Non autorizzato"}
    assert response.headers.get("WWW-Authenticate") == 'Basic realm="STIMA360 Admin"'


def test_2_wrong_password_returns_401():
    security = _load_security_module()
    client, _ = _make_client(security.require_admin)

    response = client.get("/test-admin", headers=_basic_header("giorgio", "wrong"))

    assert response.status_code == 401
    assert response.json() == {"detail": "Non autorizzato"}
    assert response.headers.get("WWW-Authenticate") == 'Basic realm="STIMA360 Admin"'


def test_3_wrong_username_returns_401():
    security = _load_security_module()
    client, _ = _make_client(security.require_admin)

    response = client.get("/test-admin", headers=_basic_header("wrong", "test-secret"))

    assert response.status_code == 401
    assert response.json() == {"detail": "Non autorizzato"}
    assert response.headers.get("WWW-Authenticate") == 'Basic realm="STIMA360 Admin"'


def test_4_correct_credentials_reach_endpoint():
    security = _load_security_module()
    client, state = _make_client(security.require_admin)

    response = client.get("/test-admin", headers=_basic_header("giorgio", "test-secret"))

    assert response.status_code == 200
    assert response.json() == {"user": "giorgio"}
    assert state["business_logic_executed"] is True


def test_5_missing_env_fails_closed_with_503(monkeypatch):
    monkeypatch.delenv("ADMIN_USER", raising=False)
    monkeypatch.delenv("ADMIN_PASS", raising=False)
    security = _load_security_module()
    client, _ = _make_client(security.require_admin)

    response = client.get("/test-admin", headers=_basic_header("giorgio", "test-secret"))

    assert response.status_code == 503
    assert response.json() == {"detail": "Servizio amministrativo non disponibile"}


def test_6_business_logic_not_executed_without_valid_auth():
    security = _load_security_module()
    client, state = _make_client(security.require_admin)

    response = client.get("/test-admin", headers=_basic_header("giorgio", "wrong"))

    assert response.status_code == 401
    assert state["business_logic_executed"] is False


def test_7_admin_security_has_no_domain_imports():
    assert SECURITY_PATH.exists(), "admin_security.py must exist"
    source = SECURITY_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden_imports = {"owner", "core", "property", "buy", "match", "flow"}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in forbidden_imports
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] not in forbidden_imports
