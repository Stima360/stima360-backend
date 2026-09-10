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


def test_backend_is_protected_in_p3():
    main_py = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "from admin_security import require_admin" in main_py
    # P26-1 Task 15: core_router moved from require_admin to require_operator.
    # The protection this test exists to guarantee is unchanged - CORE is still
    # unreachable without a credential - but the dependency now accepts BOTH
    # approved channels: an operator session cookie, or the same legacy
    # ADMIN_USER/ADMIN_PASS Basic credential as before, resolved server-side
    # into a Default-Agency-bound scope (design spec D-2). The other four
    # routers below are deliberately untouched.
    assert "app.include_router(core_router, dependencies=[Depends(require_operator)])" in main_py
    # P26-6C added `legacy_basic_agency_context` to the same import. What this
    # assertion protects is that `require_operator` comes from the certified
    # dependency module, not that it is imported alone.
    assert re.search(
        r"^from operator_auth\.dependencies import [^\n]*\brequire_operator\b",
        main_py,
        re.MULTILINE,
    ), "require_operator is no longer imported from operator_auth.dependencies"
    for router_name in ("property_router", "buy_router", "match_router", "proposal_router"):
        assert f"app.include_router({router_name}, dependencies=[Depends(require_authenticated_operator)])" in main_py
