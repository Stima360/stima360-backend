from pathlib import Path
import re
import pytest

ROOT = Path(__file__).resolve().parent.parent
FRONTENDS = ("core", "property", "buy", "match")


def read_html(fe: str) -> str:
    return (ROOT / f"static/{fe}_admin/index.html").read_text(encoding="utf-8")


def read_js(fe: str) -> str:
    return (ROOT / f"static/{fe}_admin/assets/app.js").read_text(encoding="utf-8")


@pytest.mark.parametrize("fe", FRONTENDS)
def test_frontend_has_login_and_hidden_app(fe):
    html = read_html(fe)
    assert 'id="login-view"' in html
    assert 'id="app-view"' in html
    assert re.search(r'id="app-view"[^>]*\bhidden\b', html)
    assert 'id="login-form"' in html
    assert 'id="admin-username"' in html
    assert 'id="admin-password"' in html
    assert 'type="password"' in html
    assert 'id="logout-btn"' in html


@pytest.mark.parametrize("fe", FRONTENDS)
def test_frontend_credentials_are_memory_only(fe):
    js = read_js(fe)
    assert "credentials" in js
    assert "localStorage" not in js
    assert "sessionStorage" not in js
    assert "document.cookie" not in js
    assert "indexedDB" not in js


@pytest.mark.parametrize("fe", FRONTENDS)
def test_frontend_basic_authorization_and_401_flow(fe):
    js = read_js(fe)
    assert "encodeBasic" in js
    assert "TextEncoder" in js
    assert "btoa(" in js
    assert "Authorization" in js
    assert re.search(r"status\s*===\s*401", js)
    assert "logout" in js


@pytest.mark.parametrize("fe", FRONTENDS)
def test_frontend_uses_existing_admin_check_contract(fe):
    js = read_js(fe)
    assert "/api/admin/check" in js
    assert re.search(r"user\s*:\s*username", js)
    assert re.search(r"password\s*:\s*password", js)


def test_backend_still_unprotected_in_p2():
    main_py = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "from admin_security import require_admin" not in main_py
    assert "Depends(require_admin)" not in main_py
