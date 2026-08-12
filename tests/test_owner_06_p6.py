from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
PORTAL = ROOT / "static" / "owner_portal"
INDEX = PORTAL / "index.html"
APP_JS = PORTAL / "assets" / "app.js"
APP_CSS = PORTAL / "assets" / "app.css"


class _PortalHtmlParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids: set[str] = set()
        self.labels_for: set[str] = set()
        self.meta: list[dict[str, str | None]] = []
        self.scripts: list[str | None] = []
        self.links: list[str | None] = []
        self.roles: set[str] = set()
        self.live_regions: set[str] = set()

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        element_id = values.get("id")
        if element_id:
            self.ids.add(element_id)
        if tag == "label" and values.get("for"):
            self.labels_for.add(values["for"])
        if tag == "meta":
            self.meta.append(values)
        if tag == "script":
            self.scripts.append(values.get("src"))
        if tag == "link":
            self.links.append(values.get("href"))
        if values.get("role"):
            self.roles.add(values["role"])
        if values.get("aria-live"):
            self.live_regions.add(values["aria-live"])


def _html_parser() -> _PortalHtmlParser:
    parser = _PortalHtmlParser()
    parser.feed(INDEX.read_text(encoding="utf-8"))
    return parser


def test_p6_2_frontend_files_and_assets_are_present():
    assert INDEX.is_file()
    assert APP_JS.is_file()
    assert APP_CSS.is_file()

    parser = _html_parser()
    assert "/owner/assets/app.js" in parser.scripts
    assert "/owner/assets/app.css" in parser.links


def test_p6_2_static_portal_smoke_serves_index_and_assets():
    app = FastAPI()
    app.mount("/owner", StaticFiles(directory=str(PORTAL), html=True), name="owner-p6-smoke")
    client = TestClient(app)

    index = client.get("/owner/")
    js = client.get("/owner/assets/app.js")
    css = client.get("/owner/assets/app.css")

    assert index.status_code == 200
    assert "Area proprietario" in index.text
    assert js.status_code == 200
    assert "'/api/owner/portal'" in js.text
    assert css.status_code == 200
    assert ".portal-shell" in css.text


def test_p6_2_mobile_first_and_basic_accessibility_contract():
    html = INDEX.read_text(encoding="utf-8")
    parser = _html_parser()

    viewport = [item for item in parser.meta if item.get("name") == "viewport"]
    assert viewport
    assert "width=device-width" in (viewport[0].get("content") or "")
    assert '<html lang="it">' in html
    referrer = [item for item in parser.meta if item.get("name") == "referrer"]
    assert referrer and referrer[0].get("content") == "no-referrer"

    required_ids = {
        "loading-view",
        "login-view",
        "login-form",
        "token-input",
        "login-button",
        "auth-message",
        "app-view",
        "app-message",
        "shell-empty",
        "logout-button",
    }
    assert required_ids <= parser.ids
    assert "token-input" in parser.labels_for
    assert "status" in parser.roles
    assert "polite" in parser.live_regions

    css = APP_CSS.read_text(encoding="utf-8")
    assert "min-width: 320px" in css
    assert "@media (min-width: 640px)" in css
    assert ":focus-visible" in css
    assert "prefers-reduced-motion" in css


def test_p6_2_session_bootstrap_uses_canonical_route_only():
    source = APP_JS.read_text(encoding="utf-8")
    assert "apiRequest('/session')" in source
    assert "/auth/session" not in source


def test_p6_2_auth_scope_only_no_dashboard_or_feature_calls_yet():
    source = APP_JS.read_text(encoding="utf-8")
    assert "apiRequest('/auth/token'" in source
    assert "apiRequest('/auth/logout'" in source

    for forbidden in (
        "/dashboard",
        "/properties",
        "/timeline",
        "/documents",
        "/visit-feedback",
        "/feedback",
        "/notifications",
        "/notification-preferences",
    ):
        assert forbidden not in source


def test_p6_2_never_uses_browser_storage_or_javascript_cookie_access():
    source = APP_JS.read_text(encoding="utf-8")
    assert "localStorage" not in source
    assert "sessionStorage" not in source
    assert "document.cookie" not in source
    assert "credentials: 'include'" in source
    assert "cache: 'no-store'" in source


def test_p6_2_safe_dom_contract_has_no_html_injection_apis():
    source = APP_JS.read_text(encoding="utf-8")
    for forbidden in (
        ".innerHTML",
        ".outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "eval(",
        "new Function",
    ):
        assert forbidden not in source

    assert ".textContent" in source


def test_p6_2_url_token_is_removed_before_exchange_attempt():
    source = APP_JS.read_text(encoding="utf-8")
    assert "searchParams.delete('token')" in source
    assert "window.history.replaceState" in source

    bootstrap_start = source.index("async function bootstrap()")
    bootstrap_end = source.index("loginForm.addEventListener", bootstrap_start)
    bootstrap = source[bootstrap_start:bootstrap_end]
    remove_index = bootstrap.index("removeTokenFromUrl();")
    exchange_index = bootstrap.index("await authenticateWithToken(token);")
    assert remove_index < exchange_index
    assert "tokenInput.value = urlToken" not in source


def test_p6_2_loading_error_empty_and_session_states_are_explicit():
    source = APP_JS.read_text(encoding="utf-8")
    html = INDEX.read_text(encoding="utf-8")

    for state in ("showView('loading')", "showView('login')", "showView('app')"):
        assert state in source
    for status in ("401", "403", "404", "422", "429", "500"):
        assert status in source
    assert "Connessione non disponibile" in source
    assert 'id="shell-empty"' in html
    assert 'id="loading-view"' in html
    assert 'id="auth-message"' in html
    assert 'id="app-message"' in html


def test_p6_2_logout_does_not_fake_success_when_request_fails():
    source = APP_JS.read_text(encoding="utf-8")
    logout_start = source.index("logoutButton.addEventListener")
    logout = source[logout_start:]

    assert "await apiRequest('/auth/logout', { method: 'POST' });" in logout
    assert "enterLoggedOut();" in logout
    assert "showView('app');" in logout
    assert "setAppMessage(" in logout


def test_p6_2_has_no_framework_or_remote_frontend_dependency():
    html = INDEX.read_text(encoding="utf-8").lower()
    js = APP_JS.read_text(encoding="utf-8").lower()

    for forbidden in (
        "react",
        "vue",
        "angular",
        "svelte",
        "jquery",
        "tailwind",
        "bootstrap.min",
        "https://",
        "http://",
    ):
        assert forbidden not in html
        assert forbidden not in js
