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
  'property-detail-retry','property-detail-content','property-detail-title','property-summary',
  'timeline-loading','timeline-empty','timeline-error','timeline-error-message','timeline-retry',
  'timeline-content','timeline-list','publication-detail-loading','publication-detail-empty',
  'publication-detail-error','publication-detail-error-message','publication-detail-retry',
  'publication-detail-content','publication-detail-title','publication-detail-meta',
  'publication-detail-summary','publication-detail-body','acknowledge-status','acknowledge-button'
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
ids['timeline-loading'].hidden = true;
ids['timeline-empty'].hidden = true;
ids['timeline-error'].hidden = true;
ids['timeline-content'].hidden = true;
ids['publication-detail-loading'].hidden = true;
ids['publication-detail-empty'].hidden = false;
ids['publication-detail-error'].hidden = true;
ids['publication-detail-content'].hidden = true;
ids['acknowledge-button'].hidden = true;
ids['timeline-retry'].tagName = 'BUTTON';
ids['publication-detail-retry'].tagName = 'BUTTON';
ids['acknowledge-button'].tagName = 'BUTTON';

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
  if (spec.delay_ms) await new Promise((resolve) => setTimeout(resolve, spec.delay_ms));
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


def test_p6_3_dashboard_and_property_detail_data_apis_remain_present():
    source = APP_JS.read_text(encoding="utf-8")
    assert "apiRequest('/dashboard')" in source
    assert "apiRequest(`/properties/${encodeURIComponent(String(id))}`)" in source

    for forbidden in (
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
            "/api/owner/portal/properties/11/timeline": [
                {"status": 200, "body": {"items": []}}
            ],
        },
        """
assert(calls.map((call) => call.url).join('|').endsWith('/session|/api/owner/portal/dashboard|/api/owner/portal/properties/11|/api/owner/portal/properties/11/timeline'), 'single: wrong request order');
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
            "/api/owner/portal/properties/2/timeline": [
                {"status": 200, "body": {"items": []}}
            ],
            "/api/owner/portal/properties/1": [
                {"status": 200, "body": {"property": {"title": "Casa Uno", "city": "Tortoreto", "access_role": "owner", "is_primary": False}}}
            ],
            "/api/owner/portal/properties/1/timeline": [
                {"status": 200, "body": {"items": []}}
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
assert(calls[calls.length - 2].url.endsWith('/properties/1'), 'multi: selected detail API missing');
assert(calls[calls.length - 1].url.endsWith('/properties/1/timeline'), 'multi: selected timeline API missing');
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


def test_p6_4_timeline_markup_has_accessible_loading_empty_error_list_detail_and_ack_states():
    parser = _html_parser()
    required = {
        "timeline-section",
        "timeline-title",
        "timeline-loading",
        "timeline-empty",
        "timeline-error",
        "timeline-error-message",
        "timeline-retry",
        "timeline-content",
        "timeline-list",
        "publication-detail-section",
        "publication-detail-loading",
        "publication-detail-empty",
        "publication-detail-error",
        "publication-detail-error-message",
        "publication-detail-retry",
        "publication-detail-content",
        "publication-detail-title",
        "publication-detail-meta",
        "publication-detail-summary",
        "publication-detail-body",
        "acknowledge-status",
        "acknowledge-button",
    }
    assert required <= parser.ids
    assert "Aggiornamenti pubblicati" in parser.aria_labels
    assert "Informazioni pubblicazione" in parser.aria_labels
    assert "polite" in parser.live_regions
    assert "alert" in parser.roles

    css = APP_CSS.read_text(encoding="utf-8")
    assert ".timeline-layout" in css
    assert ".timeline-card" in css
    assert ".publication-detail" in css
    assert "@media (min-width: 760px)" in css
    assert "overflow-wrap: anywhere" in css
    assert "white-space: pre-wrap" in css


def test_p6_4_uses_only_authorized_timeline_publication_apis_and_no_p6_5_plus():
    source = APP_JS.read_text(encoding="utf-8")
    assert "`/properties/${encodeURIComponent(String(propertyAtStart))}/timeline`" in source
    assert "`/publications/${encodeURIComponent(String(id))}`" in source
    assert "`/publications/${encodeURIComponent(String(id))}/acknowledge`" in source

    for forbidden in (
        "/documents",
        "/visit-feedback",
        "/feedback",
        "/notifications",
        "/notification-preferences",
    ):
        assert forbidden not in source


def test_p6_4_safe_dom_whitelist_has_no_generic_json_or_internal_fields():
    source = APP_JS.read_text(encoding="utf-8")
    assert "JSON.stringify(payload" not in source
    assert "Object.entries(payload" not in source
    assert "Object.keys(payload" not in source
    assert "publicationDetailBody.textContent" in source
    assert "publicationDetailSummary.textContent" in source
    assert "publicationDetailTitle.textContent" in source

    for forbidden in (
        "owner_account_id",
        "activity_id",
        "contact_id",
        "lead_id",
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


def test_p6_4_zero_publications_runtime_shows_empty_after_property_selection():
    _run_node_scenario(
        {
            "/api/owner/portal/session": [{"status": 200, "body": {"authenticated": True}}],
            "/api/owner/portal/dashboard": [
                {"status": 200, "body": {"property_count": 1, "properties": [{"id": 10, "title": "Casa"}]}}
            ],
            "/api/owner/portal/properties/10": [
                {"status": 200, "body": {"property": {"title": "Casa"}}}
            ],
            "/api/owner/portal/properties/10/timeline": [
                {"status": 200, "body": {"items": []}}
            ],
        },
        """
assert(calls[3].url.endsWith('/properties/10/timeline'), 'zero timeline: timeline must follow selected property');
assert(ids['timeline-empty'].hidden === false, 'zero timeline: empty state missing');
assert(ids['timeline-content'].hidden === true, 'zero timeline: content must stay hidden');
assert(ids['timeline-list'].children.length === 0, 'zero timeline: list should be empty');
""",
    )


def test_p6_4_multiple_publications_preserve_backend_order_and_do_not_auto_open_detail():
    _run_node_scenario(
        {
            "/api/owner/portal/session": [{"status": 200, "body": {"authenticated": True}}],
            "/api/owner/portal/dashboard": [
                {"status": 200, "body": {"property_count": 1, "properties": [{"id": 12, "title": "Casa"}]}}
            ],
            "/api/owner/portal/properties/12": [
                {"status": 200, "body": {"property": {"title": "Casa"}}}
            ],
            "/api/owner/portal/properties/12/timeline": [
                {
                    "status": 200,
                    "body": {
                        "items": [
                            {"id": 22, "title": "Secondo dal backend", "publication_type": "milestone", "published_at": "2026-08-12T10:00:00Z"},
                            {"id": 21, "title": "Primo dal backend", "publication_type": "general_update", "published_at": "2026-08-11T10:00:00Z"},
                        ]
                    },
                }
            ],
        },
        """
assert(ids['timeline-content'].hidden === false, 'multi timeline: content missing');
assert(ids['timeline-list'].children.length === 2, 'multi timeline: item count mismatch');
assert(allText(ids['timeline-list'].children[0]).includes('Secondo dal backend'), 'multi timeline: backend order changed');
assert(allText(ids['timeline-list'].children[1]).includes('Primo dal backend'), 'multi timeline: backend order changed');
assert(ids['publication-detail-empty'].hidden === false, 'multi timeline: detail should await explicit selection');
assert(!calls.some((call) => call.url.includes('/publications/')), 'multi timeline: detail must not auto-load');
""",
    )


def test_p6_4_open_detail_is_view_only_and_xss_payload_is_rendered_as_text():
    _run_node_scenario(
        {
            "/api/owner/portal/session": [{"status": 200, "body": {"authenticated": True}}],
            "/api/owner/portal/dashboard": [
                {"status": 200, "body": {"property_count": 1, "properties": [{"id": 13, "title": "Casa"}]}}
            ],
            "/api/owner/portal/properties/13": [
                {"status": 200, "body": {"property": {"title": "Casa"}}}
            ],
            "/api/owner/portal/properties/13/timeline": [
                {"status": 200, "body": {"items": [{"id": 31, "title": "<img src=x onerror=alert(1)>", "summary": "<b>lista</b>", "acknowledgement_required": True}]}}
            ],
            "/api/owner/portal/publications/31": [
                {
                    "status": 200,
                    "body": {
                        "id": 31,
                        "property_id": 13,
                        "title": "<script>alert('x')</script>",
                        "summary": "<b>riassunto</b>",
                        "body": "<img src=x onerror=alert(2)>",
                        "publication_type": "general_update",
                        "published_at": "2026-08-12T11:00:00Z",
                        "version_number": 1,
                        "acknowledgement_required": True,
                        "owner_account_id": 999,
                        "activity_id": 888,
                    },
                }
            ],
        },
        """
const card = ids['timeline-list'].children[0].children[0];
assert(allText(card).includes('<img src=x onerror=alert(1)>'), 'xss: list title should remain text');
await card.trigger('click');
await flush();
assert(ids['publication-detail-content'].hidden === false, 'view: detail missing');
assert(ids['publication-detail-title'].textContent === "<script>alert('x')</script>", 'xss: title not rendered as literal text');
assert(ids['publication-detail-summary'].textContent === '<b>riassunto</b>', 'xss: summary not rendered as literal text');
assert(ids['publication-detail-body'].textContent === '<img src=x onerror=alert(2)>', 'xss: body not rendered as literal text');
assert(!allText(ids['publication-detail-content']).includes('999'), 'view: owner account leaked');
assert(!allText(ids['publication-detail-content']).includes('888'), 'view: activity leaked');
assert(calls.filter((call) => call.url.endsWith('/publications/31')).length === 1, 'view: detail GET missing');
assert(calls.filter((call) => call.url.endsWith('/publications/31/acknowledge')).length === 0, 'view must not acknowledge');
assert(ids['acknowledge-button'].hidden === false, 'view: acknowledge action missing');
assert(ids['acknowledge-button'].disabled === false, 'view: acknowledge should be available');
""",
    )


def test_p6_4_acknowledge_is_explicit_double_click_safe_and_remains_acknowledged_on_reopen():
    _run_node_scenario(
        {
            "/api/owner/portal/session": [{"status": 200, "body": {"authenticated": True}}],
            "/api/owner/portal/dashboard": [
                {"status": 200, "body": {"property_count": 1, "properties": [{"id": 14, "title": "Casa"}]}}
            ],
            "/api/owner/portal/properties/14": [
                {"status": 200, "body": {"property": {"title": "Casa"}}}
            ],
            "/api/owner/portal/properties/14/timeline": [
                {"status": 200, "body": {"items": [{"id": 32, "title": "Aggiornamento", "acknowledgement_required": True}]}}
            ],
            "/api/owner/portal/publications/32": [
                {"status": 200, "body": {"id": 32, "title": "Aggiornamento", "body": "Testo", "acknowledgement_required": True}},
                {"status": 200, "body": {"id": 32, "title": "Aggiornamento", "body": "Testo", "acknowledgement_required": True}},
            ],
            "/api/owner/portal/publications/32/acknowledge": [
                {"status": 200, "delay_ms": 20, "body": {"acknowledged_at": "2026-08-12T12:00:00Z"}}
            ],
        },
        """
const card = ids['timeline-list'].children[0].children[0];
await card.trigger('click');
await flush();
assert(ids['acknowledge-status'].textContent.includes('non equivale'), 'ack: view/ack distinction missing');
await Promise.all([ids['acknowledge-button'].trigger('click'), ids['acknowledge-button'].trigger('click')]);
await new Promise((resolve) => setTimeout(resolve, 30));
await flush();
const ackCalls = calls.filter((call) => call.url.endsWith('/publications/32/acknowledge'));
assert(ackCalls.length === 1, 'ack: double submit was not blocked');
assert(ackCalls[0].method === 'POST', 'ack: wrong method');
assert(ids['acknowledge-button'].disabled === true, 'ack: action must be disabled after success');
assert(ids['acknowledge-button'].textContent === 'Presa visione confermata', 'ack: success state missing');
await card.trigger('click');
await flush();
assert(ids['acknowledge-button'].disabled === true, 'ack: acknowledged state lost on reopen');
assert(ids['acknowledge-button'].textContent === 'Presa visione confermata', 'ack: reopen status mismatch');
assert(calls.filter((call) => call.url.endsWith('/publications/32/acknowledge')).length === 1, 'ack: reopening caused duplicate acknowledge');
""",
    )


def test_p6_4_publication_without_ack_requirement_has_no_action():
    _run_node_scenario(
        {
            "/api/owner/portal/session": [{"status": 200, "body": {"authenticated": True}}],
            "/api/owner/portal/dashboard": [
                {"status": 200, "body": {"property_count": 1, "properties": [{"id": 15, "title": "Casa"}]}}
            ],
            "/api/owner/portal/properties/15": [{"status": 200, "body": {"property": {"title": "Casa"}}}],
            "/api/owner/portal/properties/15/timeline": [
                {"status": 200, "body": {"items": [{"id": 33, "title": "Info", "acknowledgement_required": False}]}}
            ],
            "/api/owner/portal/publications/33": [
                {"status": 200, "body": {"id": 33, "title": "Info", "body": "Testo", "acknowledgement_required": False}}
            ],
        },
        """
await ids['timeline-list'].children[0].children[0].trigger('click');
await flush();
assert(ids['acknowledge-button'].hidden === true, 'no-ack: button should be hidden');
assert(ids['acknowledge-status'].textContent.includes('Nessuna presa visione richiesta'), 'no-ack: neutral status missing');
assert(!calls.some((call) => call.url.includes('/acknowledge')), 'no-ack: unexpected acknowledge request');
""",
    )


def test_p6_4_property_switch_ignores_stale_timeline_response_and_clears_old_detail():
    _run_node_scenario(
        {
            "/api/owner/portal/session": [{"status": 200, "body": {"authenticated": True}}],
            "/api/owner/portal/dashboard": [
                {"status": 200, "body": {"property_count": 2, "properties": [{"id": 1, "title": "Casa Uno", "is_primary": True}, {"id": 2, "title": "Casa Due"}]}}
            ],
            "/api/owner/portal/properties/1": [{"status": 200, "body": {"property": {"title": "Casa Uno"}}}],
            "/api/owner/portal/properties/1/timeline": [
                {"status": 200, "delay_ms": 60, "body": {"items": [{"id": 101, "title": "VECCHIO IMMOBILE"}]}}
            ],
            "/api/owner/portal/properties/2": [{"status": 200, "body": {"property": {"title": "Casa Due"}}}],
            "/api/owner/portal/properties/2/timeline": [
                {"status": 200, "body": {"items": [{"id": 202, "title": "NUOVO IMMOBILE"}]}}
            ],
        },
        """
await new Promise((resolve) => setTimeout(resolve, 10));
const secondButton = ids['property-list'].children[1].children[0];
await secondButton.trigger('click');
await new Promise((resolve) => setTimeout(resolve, 80));
await flush();
const timelineText = allText(ids['timeline-list']);
assert(ids['property-detail-title'].textContent === 'Casa Due', 'stale: selected property detail mismatch');
assert(timelineText.includes('NUOVO IMMOBILE'), 'stale: new timeline missing');
assert(!timelineText.includes('VECCHIO IMMOBILE'), 'stale: old timeline leaked after switch');
assert(ids['publication-detail-content'].hidden === true, 'stale: old detail must not remain');
assert(ids['publication-detail-empty'].hidden === false, 'stale: publication detail should reset');
""",
    )


def test_p6_4_timeline_404_with_valid_session_is_neutral_content_error():
    _run_node_scenario(
        {
            "/api/owner/portal/session": [
                {"status": 200, "body": {"authenticated": True}},
                {"status": 200, "body": {"authenticated": True}},
            ],
            "/api/owner/portal/dashboard": [
                {"status": 200, "body": {"property_count": 1, "properties": [{"id": 16, "title": "Casa"}]}}
            ],
            "/api/owner/portal/properties/16": [{"status": 200, "body": {"property": {"title": "Casa"}}}],
            "/api/owner/portal/properties/16/timeline": [{"status": 404, "body": {"detail": "Risorsa non trovata"}}],
        },
        """
assert(ids['app-view'].hidden === false, 'timeline 404: app should remain visible');
assert(ids['timeline-error'].hidden === false, 'timeline 404: error state missing');
assert(ids['timeline-error-message'].textContent === 'Contenuto non disponibile o accesso non più valido.', 'timeline 404: wrong neutral message');
assert(calls[calls.length - 1].url.endsWith('/session'), 'timeline 404: session probe missing');
""",
    )


def test_p6_4_session_loss_during_timeline_returns_to_login_and_stops():
    _run_node_scenario(
        {
            "/api/owner/portal/session": [
                {"status": 200, "body": {"authenticated": True}},
                {"status": 404, "body": {"detail": "Risorsa non trovata"}},
            ],
            "/api/owner/portal/dashboard": [
                {"status": 200, "body": {"property_count": 1, "properties": [{"id": 17, "title": "Casa"}]}}
            ],
            "/api/owner/portal/properties/17": [{"status": 200, "body": {"property": {"title": "Casa"}}}],
            "/api/owner/portal/properties/17/timeline": [{"status": 404, "body": {"detail": "Risorsa non trovata"}}],
        },
        """
assert(ids['login-view'].hidden === false, 'timeline auth loss: login not shown');
assert(ids['app-view'].hidden === true, 'timeline auth loss: app still visible');
assert(ids['auth-message'].textContent === 'Sessione non disponibile o scaduta.', 'timeline auth loss: neutral message missing');
assert(calls.length === 5, 'timeline auth loss: requests continued after probe');
""",
    )


def test_p6_4_publication_404_with_valid_session_is_neutral_content_error():
    _run_node_scenario(
        {
            "/api/owner/portal/session": [
                {"status": 200, "body": {"authenticated": True}},
                {"status": 200, "body": {"authenticated": True}},
            ],
            "/api/owner/portal/dashboard": [{"status": 200, "body": {"property_count": 1, "properties": [{"id": 18, "title": "Casa"}]}}],
            "/api/owner/portal/properties/18": [{"status": 200, "body": {"property": {"title": "Casa"}}}],
            "/api/owner/portal/properties/18/timeline": [{"status": 200, "body": {"items": [{"id": 41, "title": "Aggiornamento"}]}}],
            "/api/owner/portal/publications/41": [{"status": 404, "body": {"detail": "Risorsa non trovata"}}],
        },
        """
await ids['timeline-list'].children[0].children[0].trigger('click');
await flush();
assert(ids['app-view'].hidden === false, 'publication 404: app should remain');
assert(ids['publication-detail-error'].hidden === false, 'publication 404: error missing');
assert(ids['publication-detail-error-message'].textContent === 'Contenuto non disponibile o accesso non più valido.', 'publication 404: wrong message');
assert(calls[calls.length - 1].url.endsWith('/session'), 'publication 404: session probe missing');
""",
    )


def test_p6_4_session_loss_during_publication_detail_returns_to_login():
    _run_node_scenario(
        {
            "/api/owner/portal/session": [
                {"status": 200, "body": {"authenticated": True}},
                {"status": 404, "body": {"detail": "Risorsa non trovata"}},
            ],
            "/api/owner/portal/dashboard": [{"status": 200, "body": {"property_count": 1, "properties": [{"id": 19, "title": "Casa"}]}}],
            "/api/owner/portal/properties/19": [{"status": 200, "body": {"property": {"title": "Casa"}}}],
            "/api/owner/portal/properties/19/timeline": [{"status": 200, "body": {"items": [{"id": 42, "title": "Aggiornamento"}]}}],
            "/api/owner/portal/publications/42": [{"status": 404, "body": {"detail": "Risorsa non trovata"}}],
        },
        """
await ids['timeline-list'].children[0].children[0].trigger('click');
await flush();
assert(ids['login-view'].hidden === false, 'publication auth loss: login not shown');
assert(ids['app-view'].hidden === true, 'publication auth loss: app still visible');
assert(ids['auth-message'].textContent === 'Sessione non disponibile o scaduta.', 'publication auth loss: neutral message missing');
""",
    )


def test_p6_4_session_loss_during_acknowledge_returns_to_login():
    _run_node_scenario(
        {
            "/api/owner/portal/session": [
                {"status": 200, "body": {"authenticated": True}},
                {"status": 404, "body": {"detail": "Risorsa non trovata"}},
            ],
            "/api/owner/portal/dashboard": [{"status": 200, "body": {"property_count": 1, "properties": [{"id": 20, "title": "Casa"}]}}],
            "/api/owner/portal/properties/20": [{"status": 200, "body": {"property": {"title": "Casa"}}}],
            "/api/owner/portal/properties/20/timeline": [{"status": 200, "body": {"items": [{"id": 43, "title": "Aggiornamento", "acknowledgement_required": True}]}}],
            "/api/owner/portal/publications/43": [{"status": 200, "body": {"id": 43, "title": "Aggiornamento", "body": "Testo", "acknowledgement_required": True}}],
            "/api/owner/portal/publications/43/acknowledge": [{"status": 404, "body": {"detail": "Risorsa non trovata"}}],
        },
        """
await ids['timeline-list'].children[0].children[0].trigger('click');
await flush();
await ids['acknowledge-button'].trigger('click');
await flush();
assert(ids['login-view'].hidden === false, 'ack auth loss: login not shown');
assert(ids['app-view'].hidden === true, 'ack auth loss: app still visible');
assert(ids['auth-message'].textContent === 'Sessione non disponibile o scaduta.', 'ack auth loss: neutral message missing');
""",
    )


def test_p6_4_timeline_handles_422_429_5xx_and_network_without_raw_payloads():
    cases = [
        ({"status": 422, "body": {"secret": "raw-422"}}, "Impossibile caricare gli aggiornamenti con i dati disponibili."),
        ({"status": 429, "body": {"secret": "raw-429"}}, "Troppe richieste. Riprova tra poco."),
        ({"status": 500, "body": {"secret": "raw-500"}}, "Servizio temporaneamente non disponibile."),
        ({"network_error": True}, "Connessione non disponibile. Controlla la rete e riprova."),
    ]
    for timeline_response, expected in cases:
        _run_node_scenario(
            {
                "/api/owner/portal/session": [{"status": 200, "body": {"authenticated": True}}],
                "/api/owner/portal/dashboard": [{"status": 200, "body": {"property_count": 1, "properties": [{"id": 21, "title": "Casa"}]}}],
                "/api/owner/portal/properties/21": [{"status": 200, "body": {"property": {"title": "Casa"}}}],
                "/api/owner/portal/properties/21/timeline": [timeline_response],
            },
            f"""
assert(ids['timeline-error'].hidden === false, 'error contract: error state missing');
assert(ids['timeline-error-message'].textContent === {json.dumps(expected)}, 'error contract: wrong safe message');
assert(!ids['timeline-error-message'].textContent.includes('raw-'), 'error contract: raw payload leaked');
""",
        )


def test_p6_4_security_and_accessibility_contract_remains_memory_only():
    source = APP_JS.read_text(encoding="utf-8")
    html = INDEX.read_text(encoding="utf-8")
    assert "timelineGeneration" in source
    assert "publicationGeneration" in source
    assert "acknowledgeInFlight" in source
    assert "aria-pressed" in source
    assert "aria-controls" in source
    assert 'role="list"' in html
    assert 'aria-label="Aggiornamenti pubblicati"' in html
    assert "localStorage" not in source
    assert "sessionStorage" not in source
    assert "document.cookie" not in source
    for forbidden in (
        ".innerHTML",
        ".outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "eval(",
        "new Function",
    ):
        assert forbidden not in source
