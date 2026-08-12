from __future__ import annotations

import json
import subprocess
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
        self.aria_labels: set[str] = set()

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
        if values.get("aria-label"):
            self.aria_labels.add(values["aria-label"])


def _html_parser() -> _PortalHtmlParser:
    parser = _PortalHtmlParser()
    parser.feed(INDEX.read_text(encoding="utf-8"))
    return parser


def _run_node_scenario(routes: dict[str, list[dict]], assertions: str) -> str:
    """Execute the real app.js against a tiny deterministic DOM/fetch harness."""
    route_json = json.dumps(routes, ensure_ascii=False)
    app_path = json.dumps(str(APP_JS))
    script = f"""
const fs = require('fs');
const vm = require('vm');

function assert(value, message) {{
  if (!value) throw new Error(message || 'assertion failed');
}}

class FakeClassList {{
  constructor(owner) {{ this.owner = owner; this.values = new Set(); }}
  add(name) {{ this.values.add(name); }}
  remove(name) {{ this.values.delete(name); }}
  toggle(name, force) {{
    if (force === true) {{ this.values.add(name); return true; }}
    if (force === false) {{ this.values.delete(name); return false; }}
    if (this.values.has(name)) {{ this.values.delete(name); return false; }}
    this.values.add(name); return true;
  }}
  contains(name) {{ return this.values.has(name); }}
}}

class FakeElement {{
  constructor(tag = 'div', id = '') {{
    this.tagName = tag.toUpperCase();
    this.id = id;
    this.hidden = false;
    this.disabled = false;
    this.value = '';
    this.type = '';
    this.className = '';
    this.children = [];
    this.dataset = {{}};
    this.attributes = {{}};
    this.listeners = {{}};
    this.classList = new FakeClassList(this);
    this._textContent = '';
  }}
  set textContent(value) {{ this._textContent = String(value ?? ''); }}
  get textContent() {{ return this._textContent; }}
  append(...nodes) {{ this.children.push(...nodes); }}
  replaceChildren(...nodes) {{ this.children = [...nodes]; }}
  setAttribute(name, value) {{ this.attributes[name] = String(value); }}
  getAttribute(name) {{ return this.attributes[name]; }}
  addEventListener(type, listener) {{
    if (!this.listeners[type]) this.listeners[type] = [];
    this.listeners[type].push(listener);
  }}
  focus() {{ document.activeElement = this; }}
  async trigger(type) {{
    const event = {{ preventDefault() {{}} }};
    for (const listener of this.listeners[type] || []) {{
      await listener(event);
    }}
  }}
}}

const ids = {{}};
const requiredIds = [
  'loading-view','login-view','app-view','loading-message','login-form','token-input',
  'login-button','auth-message','app-message','logout-button','property-count',
  'dashboard-loading','shell-empty','dashboard-error','dashboard-error-message',
  'dashboard-retry','dashboard-content','property-list','property-detail-loading',
  'property-detail-empty','property-detail-error','property-detail-error-message',
  'property-detail-retry','property-detail-content','property-detail-title','property-summary'
];
for (const id of requiredIds) ids[id] = new FakeElement('div', id);
ids['login-form'].tagName = 'FORM';
ids['token-input'].tagName = 'INPUT';
ids['login-button'].tagName = 'BUTTON';
ids['logout-button'].tagName = 'BUTTON';
ids['dashboard-retry'].tagName = 'BUTTON';
ids['property-detail-retry'].tagName = 'BUTTON';
ids['login-view'].hidden = true;
ids['app-view'].hidden = true;
ids['shell-empty'].hidden = true;
ids['dashboard-error'].hidden = true;
ids['dashboard-content'].hidden = true;
ids['property-detail-loading'].hidden = true;
ids['property-detail-empty'].hidden = true;
ids['property-detail-error'].hidden = true;
ids['property-detail-content'].hidden = true;

global.document = {{
  activeElement: null,
  getElementById(id) {{ return ids[id]; }},
  createElement(tag) {{ return new FakeElement(tag); }},
}};

global.window = {{
  location: {{ href: 'https://test.local/owner/' }},
  history: {{
    replaceState(_state, _title, next) {{
      window.location.href = new URL(next, window.location.href).href;
    }}
  }}
}};

const routeQueues = new Map(Object.entries({route_json}));
const calls = [];
function fakeResponse(spec) {{
  const status = spec.status ?? 200;
  return {{
    status,
    ok: status >= 200 && status < 300,
    async json() {{ return spec.body ?? null; }}
  }};
}}

global.fetch = async function(url, options = {{}}) {{
  calls.push({{ url, method: options.method || 'GET' }});
  const queue = routeQueues.get(url);
  if (!queue || queue.length === 0) throw new Error(`No fake response for ${{url}}`);
  const spec = queue.shift();
  if (spec.network_error) throw new Error('network');
  return fakeResponse(spec);
}};

function allText(node) {{
  return [node.textContent, ...node.children.map(allText)].filter(Boolean).join(' ');
}}
async function flush(rounds = 30) {{
  for (let i = 0; i < rounds; i += 1) {{
    await new Promise((resolve) => setTimeout(resolve, 0));
  }}
}}

global.URL = URL;
vm.runInThisContext(fs.readFileSync({app_path}, 'utf8'), {{ filename: 'app.js' }});

(async () => {{
  await flush();
  {assertions}
  console.log('SCENARIO PASS');
}})().catch((error) => {{
  console.error(error.stack || error);
  process.exit(1);
}});
"""
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return completed.stdout


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
    assert "@media (min-width: 860px)" in css
    assert ":focus-visible" in css
    assert "prefers-reduced-motion" in css


def test_p6_2_session_bootstrap_uses_canonical_route_only():
    source = APP_JS.read_text(encoding="utf-8")
    assert "apiRequest('/session')" in source
    assert "/auth/session" not in source


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
    assert "document.createElement" in source


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


def test_p6_3_dashboard_markup_has_explicit_loading_empty_error_and_selector_states():
    parser = _html_parser()
    required = {
        "dashboard-section",
        "dashboard-loading",
        "shell-empty",
        "dashboard-error",
        "dashboard-error-message",
        "dashboard-retry",
        "dashboard-content",
        "property-count",
        "property-list",
        "property-detail-section",
        "property-detail-loading",
        "property-detail-empty",
        "property-detail-error",
        "property-detail-error-message",
        "property-detail-retry",
        "property-detail-content",
        "property-detail-title",
        "property-summary",
    }
    assert required <= parser.ids
    assert "Immobili accessibili" in parser.aria_labels
    assert "alert" in parser.roles


def test_p6_3_only_calls_dashboard_and_property_detail_data_apis():
    source = APP_JS.read_text(encoding="utf-8")
    assert "apiRequest('/dashboard')" in source
    assert "apiRequest(`/properties/${encodeURIComponent(String(id))}`)" in source

    for forbidden in (
        "/timeline",
        "/documents",
        "/visit-feedback",
        "/feedback",
        "/notifications",
        "/notification-preferences",
    ):
        assert forbidden not in source


def test_p6_3_does_not_render_generic_json_or_internal_fields():
    source = APP_JS.read_text(encoding="utf-8")
    assert "JSON.stringify({ token })" in source
    assert "JSON.stringify(payload" not in source
    assert "Object.entries(payload" not in source
    assert "Object.keys(payload" not in source

    for forbidden in (
        "contact_id",
        "lead_id",
        "email",
        "telefono",
        "phone",
        "storage_key",
        "storage_locator",
        "bucket",
        "r2_endpoint",
        "flow_payload",
        "visitor_id",
        "match_id",
        "buy_id",
    ):
        assert forbidden not in source.lower()


def test_p6_3_dashboard_is_loaded_only_after_authenticated_session():
    source = APP_JS.read_text(encoding="utf-8")
    auth_start = source.index("async function authenticateWithToken")
    bootstrap_start = source.index("async function bootstrap()")
    auth_block = source[auth_start:bootstrap_start]
    assert auth_block.index("const session = await loadSession();") < auth_block.index("await loadDashboard();")
    assert auth_block.index("enterAuthenticated(session);") < auth_block.index("await loadDashboard();")

    bootstrap_end = source.index("loginForm.addEventListener", bootstrap_start)
    bootstrap = source[bootstrap_start:bootstrap_end]
    no_token_path = bootstrap.rsplit("try {", 1)[-1]
    assert no_token_path.index("const session = await loadSession();") < no_token_path.index("await loadDashboard();")
    assert no_token_path.index("enterAuthenticated(session);") < no_token_path.index("await loadDashboard();")


def test_p6_3_zero_properties_runtime():
    _run_node_scenario(
        {
            "/api/owner/portal/session": [{"status": 200, "body": {"authenticated": True}}],
            "/api/owner/portal/dashboard": [
                {"status": 200, "body": {"properties": [], "property_count": 0}}
            ],
        },
        """
assert(calls.length === 2, 'zero: unexpected request count');
assert(calls[0].url.endsWith('/session'), 'zero: session must be first');
assert(calls[1].url.endsWith('/dashboard'), 'zero: dashboard must follow auth');
assert(ids['shell-empty'].hidden === false, 'zero: empty state not shown');
assert(ids['dashboard-content'].hidden === true, 'zero: content must stay hidden');
assert(ids['property-count'].textContent === '0 immobili', 'zero: count mismatch');
assert(!calls.some((call) => call.url.includes('/properties/')), 'zero: detail must not load');
""",
    )


def test_p6_3_single_property_runtime_loads_detail_and_whitelists_summary():
    _run_node_scenario(
        {
            "/api/owner/portal/session": [{"status": 200, "body": {"authenticated": True}}],
            "/api/owner/portal/dashboard": [
                {
                    "status": 200,
                    "body": {
                        "property_count": 1,
                        "properties": [
                            {
                                "id": 11,
                                "title": "Casa Mare",
                                "address": "Via Adriatica 28",
                                "city": "Alba Adriatica",
                                "access_role": "owner",
                                "is_primary": True,
                            }
                        ],
                    },
                }
            ],
            "/api/owner/portal/properties/11": [
                {
                    "status": 200,
                    "body": {
                        "property": {
                            "property_id": 11,
                            "title": "Casa Mare",
                            "address": "Via Adriatica 28",
                            "city": "Alba Adriatica",
                            "access_role": "owner",
                            "is_primary": True,
                            "contact_id": 999,
                            "revoked_at": None,
                        },
                        "timeline": [{"body": "NON RENDERIZZARE"}],
                        "documents": [{"storage_key": "SEGRETO"}],
                        "visit_feedback": [{"visitor_id": 777}],
                    },
                }
            ],
        },
        """
assert(calls.map((call) => call.url).join('|').endsWith('/session|/api/owner/portal/dashboard|/api/owner/portal/properties/11'), 'single: wrong request order');
assert(ids['property-count'].textContent === '1 immobile', 'single: count mismatch');
assert(ids['dashboard-content'].hidden === false, 'single: dashboard content hidden');
assert(ids['property-list'].children.length === 1, 'single: card missing');
assert(ids['property-detail-content'].hidden === false, 'single: detail missing');
assert(ids['property-detail-title'].textContent === 'Casa Mare', 'single: title mismatch');
const summary = allText(ids['property-summary']);
assert(summary.includes('Via Adriatica 28'), 'single: address missing');
assert(summary.includes('Alba Adriatica'), 'single: city missing');
assert(summary.includes('Proprietario'), 'single: role missing');
assert(summary.includes('Immobile principale'), 'single: primary missing');
assert(!summary.includes('999'), 'single: contact leaked');
assert(!summary.includes('NON RENDERIZZARE'), 'single: timeline leaked');
assert(!summary.includes('SEGRETO'), 'single: storage leaked');
assert(!summary.includes('777'), 'single: visitor leaked');
""",
    )


def test_p6_3_multiple_properties_runtime_primary_selection_and_switch():
    _run_node_scenario(
        {
            "/api/owner/portal/session": [{"status": 200, "body": {"authenticated": True}}],
            "/api/owner/portal/dashboard": [
                {
                    "status": 200,
                    "body": {
                        "property_count": 2,
                        "properties": [
                            {"id": 1, "title": "Casa Uno", "city": "Tortoreto", "access_role": "owner", "is_primary": False},
                            {"id": 2, "title": "Casa Due", "city": "Alba Adriatica", "access_role": "co_owner", "is_primary": True},
                        ],
                    },
                }
            ],
            "/api/owner/portal/properties/2": [
                {"status": 200, "body": {"property": {"title": "Casa Due", "city": "Alba Adriatica", "access_role": "co_owner", "is_primary": True}}}
            ],
            "/api/owner/portal/properties/1": [
                {"status": 200, "body": {"property": {"title": "Casa Uno", "city": "Tortoreto", "access_role": "owner", "is_primary": False}}}
            ],
        },
        """
assert(ids['property-list'].children.length === 2, 'multi: cards missing');
const firstButton = ids['property-list'].children[0].children[0];
const secondButton = ids['property-list'].children[1].children[0];
assert(secondButton.getAttribute('aria-pressed') === 'true', 'multi: primary not selected');
assert(firstButton.getAttribute('aria-pressed') === 'false', 'multi: wrong initial selection');
assert(ids['property-detail-title'].textContent === 'Casa Due', 'multi: primary detail missing');
await firstButton.trigger('click');
await flush();
assert(firstButton.getAttribute('aria-pressed') === 'true', 'multi: switched card not selected');
assert(secondButton.getAttribute('aria-pressed') === 'false', 'multi: old card still selected');
assert(ids['property-detail-title'].textContent === 'Casa Uno', 'multi: switched detail missing');
assert(calls[calls.length - 1].url.endsWith('/properties/1'), 'multi: selected detail API missing');
""",
    )


def test_p6_3_dashboard_session_loss_returns_to_login_and_stops_data_requests():
    _run_node_scenario(
        {
            "/api/owner/portal/session": [{"status": 200, "body": {"authenticated": True}}],
            "/api/owner/portal/dashboard": [{"status": 404, "body": {"detail": "Risorsa non trovata"}}],
        },
        """
assert(ids['login-view'].hidden === false, 'dashboard auth loss: login not shown');
assert(ids['app-view'].hidden === true, 'dashboard auth loss: app still visible');
assert(ids['auth-message'].textContent === 'Sessione non disponibile o scaduta.', 'dashboard auth loss: neutral message missing');
assert(calls.length === 2, 'dashboard auth loss: data requests continued');
""",
    )


def test_p6_3_property_404_with_valid_session_is_neutral_resource_error():
    _run_node_scenario(
        {
            "/api/owner/portal/session": [
                {"status": 200, "body": {"authenticated": True}},
                {"status": 200, "body": {"authenticated": True}},
            ],
            "/api/owner/portal/dashboard": [
                {"status": 200, "body": {"property_count": 1, "properties": [{"id": 7, "title": "Casa"}]}}
            ],
            "/api/owner/portal/properties/7": [{"status": 404, "body": {"detail": "Risorsa non trovata"}}],
        },
        """
assert(ids['app-view'].hidden === false, 'property 404: app should remain visible');
assert(ids['login-view'].hidden === true, 'property 404: must not force login when session valid');
assert(ids['property-detail-error'].hidden === false, 'property 404: error state missing');
assert(ids['property-detail-error-message'].textContent === 'Immobile non disponibile o accesso non più valido.', 'property 404: wrong message');
assert(calls.length === 4, 'property 404: expected session confirmation probe');
assert(calls[3].url.endsWith('/session'), 'property 404: session probe missing');
""",
    )


def test_p6_3_property_session_loss_is_detected_via_session_probe_and_stops():
    _run_node_scenario(
        {
            "/api/owner/portal/session": [
                {"status": 200, "body": {"authenticated": True}},
                {"status": 404, "body": {"detail": "Risorsa non trovata"}},
            ],
            "/api/owner/portal/dashboard": [
                {"status": 200, "body": {"property_count": 1, "properties": [{"id": 9, "title": "Casa"}]}}
            ],
            "/api/owner/portal/properties/9": [{"status": 404, "body": {"detail": "Risorsa non trovata"}}],
        },
        """
assert(ids['login-view'].hidden === false, 'property auth loss: login not shown');
assert(ids['app-view'].hidden === true, 'property auth loss: app still visible');
assert(ids['auth-message'].textContent === 'Sessione non disponibile o scaduta.', 'property auth loss: neutral message missing');
assert(calls.length === 4, 'property auth loss: requests continued after session probe');
""",
    )


def test_p6_3_dashboard_server_error_uses_recoverable_error_state():
    _run_node_scenario(
        {
            "/api/owner/portal/session": [{"status": 200, "body": {"authenticated": True}}],
            "/api/owner/portal/dashboard": [{"status": 500, "body": {"internal": "do not show"}}],
        },
        """
assert(ids['app-view'].hidden === false, 'dashboard 500: app should remain');
assert(ids['dashboard-error'].hidden === false, 'dashboard 500: error state missing');
assert(ids['dashboard-error-message'].textContent === 'Servizio temporaneamente non disponibile.', 'dashboard 500: wrong safe message');
assert(!ids['dashboard-error-message'].textContent.includes('internal'), 'dashboard 500: raw payload leaked');
""",
    )


def test_p6_3_selection_is_memory_only_and_accessible():
    source = APP_JS.read_text(encoding="utf-8")
    html = INDEX.read_text(encoding="utf-8")

    assert "selectedPropertyId" in source
    assert "dataset.propertyId" in source
    assert "aria-pressed" in source
    assert "aria-controls" in source
    assert 'role="list"' in html
    assert 'aria-label="Immobili accessibili"' in html
    assert "localStorage" not in source
    assert "sessionStorage" not in source


def test_p6_3_error_contract_covers_422_429_5xx_and_network_without_raw_payloads():
    source = APP_JS.read_text(encoding="utf-8")
    for status in ("422", "429", "500"):
        assert status in source
    assert "Connessione non disponibile" in source
    assert "response.json()" in source
    assert "error.response" not in source
    assert "error.body" not in source
    assert "response.text()" not in source
    assert "console.log" not in source
