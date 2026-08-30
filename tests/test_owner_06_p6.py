from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from typing import get_args

import pytest
from html.parser import HTMLParser
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from pydantic import ValidationError

from owner.schemas import FeedbackCreate, NotificationPreferencesUpdate, OwnerNotificationDTO


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
  removeAttribute(name) {{ delete this.attributes[name]; }}
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
  'publication-detail-summary','publication-detail-body','acknowledge-status','acknowledge-button',
  'visit-feedback-loading','visit-feedback-empty','visit-feedback-error','visit-feedback-error-message',
  'visit-feedback-retry','visit-feedback-content','visit-feedback-list','visit-feedback-pagination',
  'visit-feedback-load-more','visit-feedback-pagination-status','visit-feedback-detail-loading',
  'visit-feedback-detail-empty','visit-feedback-detail-error','visit-feedback-detail-error-message',
  'visit-feedback-detail-retry','visit-feedback-detail-content','visit-feedback-detail-title',
  'visit-feedback-detail-meta','visit-feedback-detail-summary',
  'documents-loading','documents-empty','documents-error','documents-error-message',
  'documents-retry','documents-content','documents-list','document-detail-loading',
  'document-detail-empty','document-detail-error','document-detail-error-message',
  'document-detail-retry','document-detail-content','document-detail-title','document-detail-meta',
  'document-download-status','document-download-link','document-acknowledge-status',
  'document-acknowledge-button','request-form','request-type','request-subject','request-message',
  'request-availability-fields','request-availability-from','request-availability-to','request-submit',
  'request-form-status','requests-loading','requests-empty','requests-error','requests-error-message',
  'requests-retry','requests-content','requests-list',
  'notifications-unread-only','notifications-loading','notifications-empty','notifications-empty-message',
  'notifications-error','notifications-error-message','notifications-retry','notifications-content',
  'notifications-list','notifications-pagination','notifications-load-more','notifications-pagination-status',
  'notification-preferences-loading','notification-preferences-error','notification-preferences-error-message',
  'notification-preferences-retry','notification-preferences-form','preference-in-app','preference-publication',
  'preference-visit-feedback','preference-document','preference-request-update',
  'notification-preferences-save','notification-preferences-status'
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
ids['visit-feedback-loading'].hidden = true;
ids['visit-feedback-empty'].hidden = true;
ids['visit-feedback-error'].hidden = true;
ids['visit-feedback-content'].hidden = true;
ids['visit-feedback-pagination'].hidden = true;
ids['visit-feedback-detail-loading'].hidden = true;
ids['visit-feedback-detail-empty'].hidden = false;
ids['visit-feedback-detail-error'].hidden = true;
ids['visit-feedback-detail-content'].hidden = true;
ids['documents-loading'].hidden = true;
ids['documents-empty'].hidden = true;
ids['documents-error'].hidden = true;
ids['documents-content'].hidden = true;
ids['document-detail-loading'].hidden = true;
ids['document-detail-empty'].hidden = false;
ids['document-detail-error'].hidden = true;
ids['document-detail-content'].hidden = true;
ids['document-download-link'].hidden = true;
ids['document-acknowledge-button'].hidden = true;
ids['timeline-retry'].tagName = 'BUTTON';
ids['publication-detail-retry'].tagName = 'BUTTON';
ids['acknowledge-button'].tagName = 'BUTTON';
ids['visit-feedback-retry'].tagName = 'BUTTON';
ids['visit-feedback-load-more'].tagName = 'BUTTON';
ids['visit-feedback-detail-retry'].tagName = 'BUTTON';
ids['documents-retry'].tagName = 'BUTTON';
ids['document-detail-retry'].tagName = 'BUTTON';
ids['document-download-link'].tagName = 'A';
ids['document-acknowledge-button'].tagName = 'BUTTON';
ids['request-form'].tagName = 'FORM';
ids['request-type'].tagName = 'SELECT';
ids['request-subject'].tagName = 'INPUT';
ids['request-message'].tagName = 'TEXTAREA';
ids['request-availability-fields'].tagName = 'FIELDSET';
ids['request-availability-from'].tagName = 'INPUT';
ids['request-availability-to'].tagName = 'INPUT';
ids['request-submit'].tagName = 'BUTTON';
ids['requests-retry'].tagName = 'BUTTON';
ids['request-availability-fields'].hidden = true;
ids['requests-loading'].hidden = true;
ids['requests-empty'].hidden = true;
ids['requests-error'].hidden = true;
ids['requests-content'].hidden = true;
ids['notifications-unread-only'].tagName = 'INPUT';
ids['notifications-unread-only'].checked = false;
ids['notifications-loading'].hidden = true;
ids['notifications-empty'].hidden = true;
ids['notifications-error'].hidden = true;
ids['notifications-content'].hidden = true;
ids['notifications-pagination'].hidden = true;
ids['notifications-retry'].tagName = 'BUTTON';
ids['notifications-load-more'].tagName = 'BUTTON';
ids['notification-preferences-loading'].hidden = true;
ids['notification-preferences-error'].hidden = true;
ids['notification-preferences-form'].hidden = true;
ids['notification-preferences-retry'].tagName = 'BUTTON';
ids['notification-preferences-form'].tagName = 'FORM';
for (const id of ['preference-in-app','preference-publication','preference-visit-feedback','preference-document','preference-request-update']) {{
  ids[id].tagName = 'INPUT';
  ids[id].checked = false;
}}
ids['notification-preferences-save'].tagName = 'BUTTON';

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
  const queue = routeQueues.get(url);
  if (!queue || queue.length === 0) {{
    if (url.includes('/api/owner/portal/notifications?')) {{
      return fakeResponse({{ status: 200, body: {{ items: [], limit: 50, offset: 0, has_more: false }} }});
    }}
    if (url.endsWith('/api/owner/portal/notification-preferences') && (options.method || 'GET') === 'GET') {{
      return fakeResponse({{ status: 200, body: {{
        in_app_enabled: true, publication_enabled: true, visit_feedback_enabled: true,
        document_enabled: true, request_update_enabled: true
      }} }});
    }}
  }}
  calls.push({{ url, method: options.method || 'GET', body: options.body, headers: options.headers }});
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

    dashboard_start = source.index("async function loadDashboard()")
    dashboard_end = source.index("async function authenticateWithToken", dashboard_start)
    dashboard_block = source[dashboard_start:dashboard_end]
    property_start = source.index("async function selectProperty(id)")
    property_end = source.index("function preferredPropertyId", property_start)
    property_block = source[property_start:property_end]
    for forbidden in ("/notifications", "/notification-preferences"):
        assert forbidden not in dashboard_block
        assert forbidden not in property_block


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
            "/api/owner/portal/properties/11/documents": [
                {"status": 200, "body": {"items": []}}
            ],
            "/api/owner/portal/properties/11/visit-feedback?limit=50&offset=0": [
                {"status": 200, "body": {"items": [], "limit": 50, "offset": 0}}
            ],
            "/api/owner/portal/properties/11/feedback": [
                {"status": 200, "body": {"items": []}}
            ],
        },
        """
assert(calls.map((call) => call.url).join('|').endsWith('/session|/api/owner/portal/dashboard|/api/owner/portal/properties/11|/api/owner/portal/properties/11/timeline|/api/owner/portal/properties/11/documents|/api/owner/portal/properties/11/visit-feedback?limit=50&offset=0|/api/owner/portal/properties/11/feedback'), 'single: wrong request order');
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
assert(calls.some((call) => call.url.endsWith('/properties/1')), 'multi: selected detail API missing');
assert(calls.some((call) => call.url.endsWith('/properties/1/timeline')), 'multi: selected timeline API missing');
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


def test_p6_4_uses_only_authorized_timeline_publication_apis_and_no_cross_feature_calls():
    source = APP_JS.read_text(encoding="utf-8")
    assert "`/properties/${encodeURIComponent(String(propertyAtStart))}/timeline`" in source
    assert "`/publications/${encodeURIComponent(String(id))}`" in source
    assert "`/publications/${encodeURIComponent(String(id))}/acknowledge`" in source

    start = source.index("async function loadTimeline")
    end = source.index("async function loadVisitFeedback", start)
    timeline_block = source[start:end]
    for forbidden in ("/feedback", "/notifications", "/notification-preferences"):
        assert forbidden not in timeline_block


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
            "/api/owner/portal/properties/16/visit-feedback?limit=50&offset=0": [
                {"status": 200, "body": {"items": [], "limit": 50, "offset": 0}}
            ],
        },
        """
assert(ids['app-view'].hidden === false, 'timeline 404: app should remain visible');
assert(ids['timeline-error'].hidden === false, 'timeline 404: error state missing');
assert(ids['timeline-error-message'].textContent === 'Contenuto non disponibile o accesso non più valido.', 'timeline 404: wrong neutral message');
const timelineCallIndex = calls.findIndex((call) => call.url.endsWith('/properties/16/timeline'));
assert(timelineCallIndex >= 0, 'timeline 404: timeline request missing');
assert(calls.slice(timelineCallIndex + 1).some((call) => call.url.endsWith('/session')), 'timeline 404: session probe missing');
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


def _feedback_item(i: int, *, summary: str | None = None) -> dict:
    return {
        "visit_feedback_publication_id": i,
        "category_code": "general",
        "category_label": f"Feedback {i}",
        "public_summary": summary or f"Sintesi pubblica {i}",
        "sentiment": "neutral",
        "sentiment_label": "Neutro",
        "version_number": 1,
        "published_at": "2026-08-12T12:00:00Z",
        "is_current_version": True,
    }


def test_p6_5_markup_has_accessible_loading_empty_error_pagination_list_and_detail_states():
    parser = _html_parser()
    required = {
        "visit-feedback-section",
        "visit-feedback-title",
        "visit-feedback-loading",
        "visit-feedback-empty",
        "visit-feedback-error",
        "visit-feedback-error-message",
        "visit-feedback-retry",
        "visit-feedback-content",
        "visit-feedback-list",
        "visit-feedback-pagination",
        "visit-feedback-load-more",
        "visit-feedback-pagination-status",
        "visit-feedback-detail-section",
        "visit-feedback-detail-loading",
        "visit-feedback-detail-empty",
        "visit-feedback-detail-error",
        "visit-feedback-detail-error-message",
        "visit-feedback-detail-retry",
        "visit-feedback-detail-content",
        "visit-feedback-detail-title",
        "visit-feedback-detail-meta",
        "visit-feedback-detail-summary",
    }
    assert required <= parser.ids
    assert "Feedback visite pubblicati" in parser.aria_labels
    assert "Informazioni feedback visita" in parser.aria_labels
    assert "polite" in parser.live_regions
    assert "alert" in parser.roles

    html = INDEX.read_text(encoding="utf-8")
    assert "Feedback anonimizzati" in html
    assert "senza dati identificativi dei visitatori" in html

    css = APP_CSS.read_text(encoding="utf-8")
    assert ".visit-feedback-layout" in css
    assert ".visit-feedback-card" in css
    assert ".visit-feedback-detail" in css
    assert "@media (min-width: 760px)" in css
    assert "overflow-wrap: anywhere" in css
    assert "white-space: pre-wrap" in css


def test_p6_5_uses_only_authorized_visit_feedback_apis_and_no_p6_7_plus():
    source = APP_JS.read_text(encoding="utf-8")
    assert "`/properties/${encodeURIComponent(String(propertyAtStart))}/visit-feedback?limit=${limit}&offset=${offset}`" in source
    assert "`/visit-feedback/${encodeURIComponent(String(id))}`" in source
    assert "const limit = 50;" in source

    assert "/properties/${propertyAtStart}/feedback" not in source


def test_p6_5_safe_dom_and_privacy_whitelist_has_no_generic_json_or_private_crm_fields():
    source = APP_JS.read_text(encoding="utf-8")
    assert "JSON.stringify(payload" not in source
    assert "Object.entries(payload" not in source
    assert "Object.keys(payload" not in source
    assert "visit_feedback_publication_id" in source
    assert "category_label" in source
    assert "public_summary" in source
    assert "sentiment_label" in source
    assert "published_at" in source
    assert "version_number" in source

    for forbidden in (
        "visitor_id",
        "contact_id",
        "lead_id",
        "activity_id",
        "visitor_name",
        "visitor_surname",
        "internal_notes",
        "flow_payload",
        "match_id",
        "buy_id",
    ):
        assert forbidden not in source.lower()

    for forbidden_api in (
        ".innerHTML",
        ".outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "eval(",
        "new Function",
    ):
        assert forbidden_api not in source


def test_p6_5_feedback_is_requested_only_after_authenticated_property_selection():
    _run_node_scenario(
        {
            "/api/owner/portal/session": [{"status": 200, "body": {"authenticated": True}}],
            "/api/owner/portal/dashboard": [
                {"status": 200, "body": {"property_count": 1, "properties": [{"id": 31, "title": "Casa"}]}}
            ],
            "/api/owner/portal/properties/31": [{"status": 200, "body": {"property": {"title": "Casa"}}}],
            "/api/owner/portal/properties/31/timeline": [{"status": 200, "body": {"items": []}}],
            "/api/owner/portal/properties/31/visit-feedback?limit=50&offset=0": [
                {"status": 200, "body": {"items": [], "limit": 50, "offset": 0}}
            ],
        },
        """
const feedbackIndex = calls.findIndex((call) => call.url.includes('/visit-feedback?limit=50&offset=0'));
const propertyIndex = calls.findIndex((call) => call.url.endsWith('/properties/31'));
const timelineIndex = calls.findIndex((call) => call.url.endsWith('/properties/31/timeline'));
assert(feedbackIndex > propertyIndex, 'feedback order: list requested before property selection/detail');
assert(feedbackIndex > timelineIndex, 'feedback order: P6.4 flow unexpectedly reordered');
assert(calls[0].url.endsWith('/session'), 'feedback order: auth session must be first');
""",
    )


def test_p6_5_zero_feedback_runtime_shows_empty_state():
    _run_node_scenario(
        {
            "/api/owner/portal/session": [{"status": 200, "body": {"authenticated": True}}],
            "/api/owner/portal/dashboard": [{"status": 200, "body": {"property_count": 1, "properties": [{"id": 32, "title": "Casa"}]}}],
            "/api/owner/portal/properties/32": [{"status": 200, "body": {"property": {"title": "Casa"}}}],
            "/api/owner/portal/properties/32/timeline": [{"status": 200, "body": {"items": []}}],
            "/api/owner/portal/properties/32/visit-feedback?limit=50&offset=0": [
                {"status": 200, "body": {"items": [], "limit": 50, "offset": 0}}
            ],
        },
        """
assert(ids['visit-feedback-empty'].hidden === false, 'feedback zero: empty state missing');
assert(ids['visit-feedback-content'].hidden === true, 'feedback zero: content should remain hidden');
assert(ids['visit-feedback-list'].children.length === 0, 'feedback zero: unexpected cards');
assert(ids['visit-feedback-pagination'].hidden === true, 'feedback zero: pagination should be hidden');
""",
    )


def test_p6_5_one_and_multiple_feedback_preserve_backend_order_without_auto_open():
    items = [_feedback_item(72), _feedback_item(71), _feedback_item(70)]
    _run_node_scenario(
        {
            "/api/owner/portal/session": [{"status": 200, "body": {"authenticated": True}}],
            "/api/owner/portal/dashboard": [{"status": 200, "body": {"property_count": 1, "properties": [{"id": 33, "title": "Casa"}]}}],
            "/api/owner/portal/properties/33": [{"status": 200, "body": {"property": {"title": "Casa"}}}],
            "/api/owner/portal/properties/33/timeline": [{"status": 200, "body": {"items": []}}],
            "/api/owner/portal/properties/33/visit-feedback?limit=50&offset=0": [
                {"status": 200, "body": {"items": items, "limit": 50, "offset": 0}}
            ],
        },
        """
assert(ids['visit-feedback-content'].hidden === false, 'feedback multiple: content missing');
assert(ids['visit-feedback-list'].children.length === 3, 'feedback multiple: card count mismatch');
const first = ids['visit-feedback-list'].children[0].children[0];
const second = ids['visit-feedback-list'].children[1].children[0];
const third = ids['visit-feedback-list'].children[2].children[0];
assert(first.dataset.visitFeedbackId === '72', 'feedback multiple: backend order changed at first item');
assert(second.dataset.visitFeedbackId === '71', 'feedback multiple: backend order changed at second item');
assert(third.dataset.visitFeedbackId === '70', 'feedback multiple: backend order changed at third item');
assert(ids['visit-feedback-detail-content'].hidden === true, 'feedback multiple: detail auto-opened unexpectedly');
assert(ids['visit-feedback-detail-empty'].hidden === false, 'feedback multiple: empty detail prompt missing');
""",
    )


def test_p6_5_detail_uses_public_whitelist_and_renders_xss_payload_as_text():
    malicious = "<script>alert(1)</script><img src=x onerror=alert(2)>"
    item = _feedback_item(80, summary=malicious)
    detail = dict(item)
    detail.update(
        {
            "visitor": "NON MOSTRARE",
            "visitor_id": 999,
            "contact_id": 888,
            "lead_id": 777,
            "activity_id": 666,
            "internal_notes": "SEGRETO",
            "BUY": {"secret": True},
            "MATCH": {"secret": True},
            "FLOW": {"secret": True},
        }
    )
    _run_node_scenario(
        {
            "/api/owner/portal/session": [{"status": 200, "body": {"authenticated": True}}],
            "/api/owner/portal/dashboard": [{"status": 200, "body": {"property_count": 1, "properties": [{"id": 34, "title": "Casa"}]}}],
            "/api/owner/portal/properties/34": [{"status": 200, "body": {"property": {"title": "Casa"}}}],
            "/api/owner/portal/properties/34/timeline": [{"status": 200, "body": {"items": []}}],
            "/api/owner/portal/properties/34/visit-feedback?limit=50&offset=0": [
                {"status": 200, "body": {"items": [item], "limit": 50, "offset": 0}}
            ],
            "/api/owner/portal/visit-feedback/80": [
                {"status": 200, "body": {"visit_feedback": detail}}
            ],
        },
        f"""
await ids['visit-feedback-list'].children[0].children[0].trigger('click');
await flush();
assert(ids['visit-feedback-detail-content'].hidden === false, 'feedback detail: content missing');
assert(ids['visit-feedback-detail-summary'].textContent === {json.dumps(malicious)}, 'feedback detail: XSS text changed or executed');
const rendered = [
  ids['visit-feedback-detail-title'].textContent,
  allText(ids['visit-feedback-detail-meta']),
  ids['visit-feedback-detail-summary'].textContent,
].join(' ');
assert(rendered.includes('<script>alert(1)</script>'), 'feedback detail: malicious text should remain literal text');
for (const secret of ['NON MOSTRARE','SEGRETO','999','888','777','666']) {{
  assert(!rendered.includes(secret), `feedback detail privacy: leaked ${{secret}}`);
}}
assert(!rendered.includes('BUY') && !rendered.includes('MATCH') && !rendered.includes('FLOW'), 'feedback detail privacy: internal module leaked');
""",
    )


def test_p6_5_pagination_load_more_keeps_existing_items_avoids_duplicate_requests_and_detects_end():
    first_page = [_feedback_item(i) for i in range(1, 51)]
    second_page = [_feedback_item(51), _feedback_item(52)]
    _run_node_scenario(
        {
            "/api/owner/portal/session": [{"status": 200, "body": {"authenticated": True}}],
            "/api/owner/portal/dashboard": [{"status": 200, "body": {"property_count": 1, "properties": [{"id": 35, "title": "Casa"}]}}],
            "/api/owner/portal/properties/35": [{"status": 200, "body": {"property": {"title": "Casa"}}}],
            "/api/owner/portal/properties/35/timeline": [{"status": 200, "body": {"items": []}}],
            "/api/owner/portal/properties/35/visit-feedback?limit=50&offset=0": [
                {"status": 200, "body": {"items": first_page, "limit": 50, "offset": 0}}
            ],
            "/api/owner/portal/properties/35/visit-feedback?limit=50&offset=50": [
                {"status": 200, "delay_ms": 20, "body": {"items": second_page, "limit": 50, "offset": 50}}
            ],
        },
        """
assert(ids['visit-feedback-list'].children.length === 50, 'feedback pagination: first page missing');
assert(ids['visit-feedback-pagination'].hidden === false, 'feedback pagination: load more should be visible');
void ids['visit-feedback-load-more'].trigger('click');
void ids['visit-feedback-load-more'].trigger('click');
await new Promise((resolve) => setTimeout(resolve, 40));
await flush();
assert(ids['visit-feedback-list'].children.length === 52, 'feedback pagination: old items lost or second page missing');
const page2Calls = calls.filter((call) => call.url.includes('visit-feedback?limit=50&offset=50'));
assert(page2Calls.length === 1, 'feedback pagination: duplicate page request');
assert(ids['visit-feedback-pagination'].hidden === true, 'feedback pagination: end of list not detected');
""",
    )


def test_p6_5_pagination_filters_duplicate_ids_without_losing_offset_progress():
    first_page = [_feedback_item(i) for i in range(1, 51)]
    second_page = [_feedback_item(50), _feedback_item(51)]
    _run_node_scenario(
        {
            "/api/owner/portal/session": [{"status": 200, "body": {"authenticated": True}}],
            "/api/owner/portal/dashboard": [{"status": 200, "body": {"property_count": 1, "properties": [{"id": 36, "title": "Casa"}]}}],
            "/api/owner/portal/properties/36": [{"status": 200, "body": {"property": {"title": "Casa"}}}],
            "/api/owner/portal/properties/36/timeline": [{"status": 200, "body": {"items": []}}],
            "/api/owner/portal/properties/36/visit-feedback?limit=50&offset=0": [{"status": 200, "body": {"items": first_page}}],
            "/api/owner/portal/properties/36/visit-feedback?limit=50&offset=50": [{"status": 200, "body": {"items": second_page}}],
        },
        """
await ids['visit-feedback-load-more'].trigger('click');
await flush();
assert(ids['visit-feedback-list'].children.length === 51, 'feedback dedupe: duplicate id rendered');
const idsRendered = ids['visit-feedback-list'].children.map((node) => node.children[0].dataset.visitFeedbackId);
assert(new Set(idsRendered).size === 51, 'feedback dedupe: rendered IDs are not unique');
""",
    )


def test_p6_5_property_switch_resets_feedback_pagination_detail_and_ignores_stale_response():
    first_page = [_feedback_item(i) for i in range(1, 51)]
    _run_node_scenario(
        {
            "/api/owner/portal/session": [{"status": 200, "body": {"authenticated": True}}],
            "/api/owner/portal/dashboard": [
                {"status": 200, "body": {"property_count": 2, "properties": [{"id": 40, "title": "Casa Uno", "is_primary": True}, {"id": 41, "title": "Casa Due"}]}}
            ],
            "/api/owner/portal/properties/40": [{"status": 200, "body": {"property": {"title": "Casa Uno"}}}],
            "/api/owner/portal/properties/40/timeline": [{"status": 200, "body": {"items": []}}],
            "/api/owner/portal/properties/40/visit-feedback?limit=50&offset=0": [
                {"status": 200, "delay_ms": 60, "body": {"items": first_page}}
            ],
            "/api/owner/portal/properties/41": [{"status": 200, "body": {"property": {"title": "Casa Due"}}}],
            "/api/owner/portal/properties/41/timeline": [{"status": 200, "body": {"items": []}}],
            "/api/owner/portal/properties/41/visit-feedback?limit=50&offset=0": [
                {"status": 200, "body": {"items": [{"visit_feedback_publication_id": 900, "category_label": "NUOVO FEEDBACK", "public_summary": "Nuova property"}]}}
            ],
        },
        """
await new Promise((resolve) => setTimeout(resolve, 10));
const secondButton = ids['property-list'].children[1].children[0];
await secondButton.trigger('click');
await new Promise((resolve) => setTimeout(resolve, 80));
await flush();
const feedbackText = allText(ids['visit-feedback-list']);
assert(feedbackText.includes('NUOVO FEEDBACK'), 'feedback stale: new property feedback missing');
assert(!feedbackText.includes('Feedback 1'), 'feedback stale: old property response leaked');
assert(ids['visit-feedback-list'].children.length === 1, 'feedback stale: list was not reset');
assert(ids['visit-feedback-pagination'].hidden === true, 'feedback stale: pagination was not reset');
assert(ids['visit-feedback-detail-content'].hidden === true, 'feedback stale: old detail not cleared');
assert(ids['visit-feedback-detail-empty'].hidden === false, 'feedback stale: empty detail state missing');
""",
    )


def test_p6_5_list_404_with_valid_session_is_neutral_content_error():
    _run_node_scenario(
        {
            "/api/owner/portal/session": [
                {"status": 200, "body": {"authenticated": True}},
                {"status": 200, "body": {"authenticated": True}},
            ],
            "/api/owner/portal/dashboard": [{"status": 200, "body": {"property_count": 1, "properties": [{"id": 42, "title": "Casa"}]}}],
            "/api/owner/portal/properties/42": [{"status": 200, "body": {"property": {"title": "Casa"}}}],
            "/api/owner/portal/properties/42/timeline": [{"status": 200, "body": {"items": []}}],
            "/api/owner/portal/properties/42/visit-feedback?limit=50&offset=0": [{"status": 404, "body": {"detail": "Risorsa non trovata"}}],
        },
        """
assert(ids['app-view'].hidden === false, 'feedback list 404: app should remain visible');
assert(ids['visit-feedback-error'].hidden === false, 'feedback list 404: error state missing');
assert(ids['visit-feedback-error-message'].textContent === 'Contenuto non disponibile o accesso non più valido.', 'feedback list 404: wrong neutral message');
assert(calls.filter((call) => call.url.endsWith('/session')).length >= 2, 'feedback list 404: session probe missing');
""",
    )


def test_p6_5_session_loss_during_list_returns_to_login_and_stops():
    _run_node_scenario(
        {
            "/api/owner/portal/session": [
                {"status": 200, "body": {"authenticated": True}},
                {"status": 404, "body": {"detail": "Risorsa non trovata"}},
            ],
            "/api/owner/portal/dashboard": [{"status": 200, "body": {"property_count": 1, "properties": [{"id": 43, "title": "Casa"}]}}],
            "/api/owner/portal/properties/43": [{"status": 200, "body": {"property": {"title": "Casa"}}}],
            "/api/owner/portal/properties/43/timeline": [{"status": 200, "body": {"items": []}}],
            "/api/owner/portal/properties/43/visit-feedback?limit=50&offset=0": [{"status": 404, "body": {"detail": "Risorsa non trovata"}}],
        },
        """
assert(ids['login-view'].hidden === false, 'feedback list auth loss: login not shown');
assert(ids['app-view'].hidden === true, 'feedback list auth loss: app still visible');
assert(ids['auth-message'].textContent === 'Sessione non disponibile o scaduta.', 'feedback list auth loss: neutral message missing');
assert(calls.filter((call) => call.url.endsWith('/session')).length >= 2, 'feedback list auth loss: expected session probe');
""",
    )


def test_p6_5_detail_404_with_valid_session_is_neutral_content_error():
    item = _feedback_item(91)
    _run_node_scenario(
        {
            "/api/owner/portal/session": [
                {"status": 200, "body": {"authenticated": True}},
                {"status": 200, "body": {"authenticated": True}},
            ],
            "/api/owner/portal/dashboard": [{"status": 200, "body": {"property_count": 1, "properties": [{"id": 44, "title": "Casa"}]}}],
            "/api/owner/portal/properties/44": [{"status": 200, "body": {"property": {"title": "Casa"}}}],
            "/api/owner/portal/properties/44/timeline": [{"status": 200, "body": {"items": []}}],
            "/api/owner/portal/properties/44/visit-feedback?limit=50&offset=0": [{"status": 200, "body": {"items": [item]}}],
            "/api/owner/portal/visit-feedback/91": [{"status": 404, "body": {"detail": "Risorsa non trovata"}}],
        },
        """
await ids['visit-feedback-list'].children[0].children[0].trigger('click');
await flush();
assert(ids['app-view'].hidden === false, 'feedback detail 404: app should remain');
assert(ids['visit-feedback-detail-error'].hidden === false, 'feedback detail 404: error missing');
assert(ids['visit-feedback-detail-error-message'].textContent === 'Contenuto non disponibile o accesso non più valido.', 'feedback detail 404: wrong message');
assert(calls[calls.length - 1].url.endsWith('/session'), 'feedback detail 404: session probe missing');
""",
    )


def test_p6_5_session_loss_during_detail_returns_to_login():
    item = _feedback_item(92)
    _run_node_scenario(
        {
            "/api/owner/portal/session": [
                {"status": 200, "body": {"authenticated": True}},
                {"status": 404, "body": {"detail": "Risorsa non trovata"}},
            ],
            "/api/owner/portal/dashboard": [{"status": 200, "body": {"property_count": 1, "properties": [{"id": 45, "title": "Casa"}]}}],
            "/api/owner/portal/properties/45": [{"status": 200, "body": {"property": {"title": "Casa"}}}],
            "/api/owner/portal/properties/45/timeline": [{"status": 200, "body": {"items": []}}],
            "/api/owner/portal/properties/45/visit-feedback?limit=50&offset=0": [{"status": 200, "body": {"items": [item]}}],
            "/api/owner/portal/visit-feedback/92": [{"status": 404, "body": {"detail": "Risorsa non trovata"}}],
        },
        """
await ids['visit-feedback-list'].children[0].children[0].trigger('click');
await flush();
assert(ids['login-view'].hidden === false, 'feedback detail auth loss: login not shown');
assert(ids['app-view'].hidden === true, 'feedback detail auth loss: app still visible');
assert(ids['auth-message'].textContent === 'Sessione non disponibile o scaduta.', 'feedback detail auth loss: neutral message missing');
""",
    )


def test_p6_5_list_handles_422_429_5xx_and_network_without_raw_payloads():
    cases = [
        ({"status": 422, "body": {"secret": "raw-422"}}, "Impossibile caricare i feedback con i dati disponibili."),
        ({"status": 429, "body": {"secret": "raw-429"}}, "Troppe richieste. Riprova tra poco."),
        ({"status": 500, "body": {"secret": "raw-500"}}, "Servizio temporaneamente non disponibile."),
        ({"network_error": True}, "Connessione non disponibile. Controlla la rete e riprova."),
    ]
    for response, expected in cases:
        _run_node_scenario(
            {
                "/api/owner/portal/session": [{"status": 200, "body": {"authenticated": True}}],
                "/api/owner/portal/dashboard": [{"status": 200, "body": {"property_count": 1, "properties": [{"id": 46, "title": "Casa"}]}}],
                "/api/owner/portal/properties/46": [{"status": 200, "body": {"property": {"title": "Casa"}}}],
                "/api/owner/portal/properties/46/timeline": [{"status": 200, "body": {"items": []}}],
                "/api/owner/portal/properties/46/visit-feedback?limit=50&offset=0": [response],
            },
            f"""
assert(ids['visit-feedback-error'].hidden === false, 'feedback list error: state missing');
assert(ids['visit-feedback-error-message'].textContent === {json.dumps(expected)}, 'feedback list error: wrong safe message');
assert(!ids['visit-feedback-error-message'].textContent.includes('raw-'), 'feedback list error: raw payload leaked');
""",
        )


def test_p6_5_detail_handles_422_429_5xx_and_network_without_raw_payloads():
    cases = [
        ({"status": 422, "body": {"secret": "raw-422"}}, "Impossibile caricare il contenuto del feedback."),
        ({"status": 429, "body": {"secret": "raw-429"}}, "Troppe richieste. Riprova tra poco."),
        ({"status": 500, "body": {"secret": "raw-500"}}, "Servizio temporaneamente non disponibile."),
        ({"network_error": True}, "Connessione non disponibile. Controlla la rete e riprova."),
    ]
    for response, expected in cases:
        item = _feedback_item(93)
        _run_node_scenario(
            {
                "/api/owner/portal/session": [{"status": 200, "body": {"authenticated": True}}],
                "/api/owner/portal/dashboard": [{"status": 200, "body": {"property_count": 1, "properties": [{"id": 47, "title": "Casa"}]}}],
                "/api/owner/portal/properties/47": [{"status": 200, "body": {"property": {"title": "Casa"}}}],
                "/api/owner/portal/properties/47/timeline": [{"status": 200, "body": {"items": []}}],
                "/api/owner/portal/properties/47/visit-feedback?limit=50&offset=0": [{"status": 200, "body": {"items": [item]}}],
                "/api/owner/portal/visit-feedback/93": [response],
            },
            f"""
await ids['visit-feedback-list'].children[0].children[0].trigger('click');
await flush();
assert(ids['visit-feedback-detail-error'].hidden === false, 'feedback detail error: state missing');
assert(ids['visit-feedback-detail-error-message'].textContent === {json.dumps(expected)}, 'feedback detail error: wrong safe message');
assert(!ids['visit-feedback-detail-error-message'].textContent.includes('raw-'), 'feedback detail error: raw payload leaked');
""",
        )


def test_p6_5_security_accessibility_and_memory_only_contract():
    source = APP_JS.read_text(encoding="utf-8")
    html = INDEX.read_text(encoding="utf-8")
    assert "visitFeedbackGeneration" in source
    assert "visitFeedbackDetailGeneration" in source
    assert "visitFeedbackLoadInFlight" in source
    assert "aria-pressed" in source
    assert "aria-controls" in source
    assert 'aria-label="Feedback visite pubblicati"' in html
    assert 'aria-label="Informazioni feedback visita"' in html
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


def test_p6_5_property_switch_resets_existing_pagination_offset_and_cards():
    first_page = [_feedback_item(i) for i in range(1, 51)]
    _run_node_scenario(
        {
            "/api/owner/portal/session": [{"status": 200, "body": {"authenticated": True}}],
            "/api/owner/portal/dashboard": [
                {"status": 200, "body": {"property_count": 2, "properties": [{"id": 48, "title": "Casa Uno", "is_primary": True}, {"id": 49, "title": "Casa Due"}]}}
            ],
            "/api/owner/portal/properties/48": [{"status": 200, "body": {"property": {"title": "Casa Uno"}}}],
            "/api/owner/portal/properties/48/timeline": [{"status": 200, "body": {"items": []}}],
            "/api/owner/portal/properties/48/visit-feedback?limit=50&offset=0": [{"status": 200, "body": {"items": first_page}}],
            "/api/owner/portal/properties/49": [{"status": 200, "body": {"property": {"title": "Casa Due"}}}],
            "/api/owner/portal/properties/49/timeline": [{"status": 200, "body": {"items": []}}],
            "/api/owner/portal/properties/49/visit-feedback?limit=50&offset=0": [
                {"status": 200, "body": {"items": [{"visit_feedback_publication_id": 990, "category_label": "Seconda casa", "public_summary": "Solo seconda casa"}]}}
            ],
        },
        """
assert(ids['visit-feedback-list'].children.length === 50, 'feedback reset: first property page not loaded');
assert(ids['visit-feedback-pagination'].hidden === false, 'feedback reset: first property pagination should be visible');
await ids['property-list'].children[1].children[0].trigger('click');
await flush();
assert(ids['visit-feedback-list'].children.length === 1, 'feedback reset: old cards survived property switch');
assert(ids['visit-feedback-list'].children[0].children[0].dataset.visitFeedbackId === '990', 'feedback reset: wrong second-property item');
assert(ids['visit-feedback-pagination'].hidden === true, 'feedback reset: pagination did not reset');
const secondPropertyFeedbackCalls = calls.filter((call) => call.url.includes('/properties/49/visit-feedback?limit=50&offset=0'));
assert(secondPropertyFeedbackCalls.length === 1, 'feedback reset: new property did not restart at offset zero exactly once');
assert(!calls.some((call) => call.url.includes('/properties/49/visit-feedback?limit=50&offset=50')), 'feedback reset: offset leaked across properties');
""",
    )


def test_p6_5_feedback_transport_is_read_only():
    source = APP_JS.read_text(encoding="utf-8")
    start = source.index("async function loadVisitFeedback")
    end = source.index("async function loadDocuments", start)
    feedback_block = source[start:end]
    assert "method: 'POST'" not in feedback_block
    assert "method: 'PUT'" not in feedback_block
    assert "method: 'PATCH'" not in feedback_block
    assert "method: 'DELETE'" not in feedback_block


def _document_item(
    i: int,
    *,
    title: str | None = None,
    acknowledged_at: str | None = None,
    acknowledgement_required: bool = True,
    download_available: bool = True,
) -> dict:
    return {
        "id": i,
        "public_title": title or f"Documento {i}",
        "public_document_type": "ape",
        "public_document_type_label": "APE",
        "version_number": 1,
        "published_at": "2026-08-12T10:00:00Z",
        "expires_at": "2027-08-12T10:00:00Z",
        "acknowledgement_required": acknowledgement_required,
        "first_viewed_at": None,
        "last_viewed_at": None,
        "view_count": 0,
        "acknowledged_at": acknowledged_at,
        "mime_type": "application/pdf",
        "size_bytes": 1536,
        "download_filename": f"documento-{i}.pdf",
        "download_available": download_available,
    }


def _p6_6_base_routes(property_id: int, documents: list[dict]) -> dict[str, list[dict]]:
    return {
        "/api/owner/portal/session": [{"status": 200, "body": {"authenticated": True}}],
        "/api/owner/portal/dashboard": [
            {"status": 200, "body": {"property_count": 1, "properties": [{"id": property_id, "title": "Casa"}]}}
        ],
        f"/api/owner/portal/properties/{property_id}": [
            {"status": 200, "body": {"property": {"title": "Casa"}}}
        ],
        f"/api/owner/portal/properties/{property_id}/timeline": [
            {"status": 200, "body": {"items": []}}
        ],
        f"/api/owner/portal/properties/{property_id}/documents": [
            {"status": 200, "body": {"items": documents}}
        ],
        f"/api/owner/portal/properties/{property_id}/visit-feedback?limit=50&offset=0": [
            {"status": 200, "body": {"items": []}}
        ],
    }


def test_p6_6_precheck_backend_public_document_dto_fields_are_exactly_known():
    repository_source = (ROOT / "owner" / "repository.py").read_text(encoding="utf-8")
    start = repository_source.index("def _public_shared_document(row):")
    end = repository_source.index("\ndef _authorized_shared_document_source", start)
    block = repository_source[start:end]

    expected = {
        "id",
        "public_title",
        "public_document_type",
        "public_document_type_label",
        "version_number",
        "published_at",
        "expires_at",
        "acknowledgement_required",
        "first_viewed_at",
        "last_viewed_at",
        "view_count",
        "acknowledged_at",
        "mime_type",
        "size_bytes",
        "download_filename",
        "download_available",
    }
    import re

    actual = set(re.findall(r'^\s*"([a-z0-9_]+)"\s*:', block, flags=re.MULTILINE))
    assert actual == expected

    router_source = (ROOT / "owner" / "router_portal.py").read_text(encoding="utf-8")
    assert '@router.get("/properties/{p}/documents")' in router_source
    assert '@router.get("/documents/{i}")' in router_source
    assert '@router.get("/documents/{i}/download")' in router_source
    assert '@router.post("/documents/{i}/acknowledge")' in router_source


def test_p6_6_markup_has_accessible_loading_empty_error_list_detail_download_and_ack_states():
    parser = _html_parser()
    required = {
        "documents-section",
        "documents-title",
        "documents-loading",
        "documents-empty",
        "documents-error",
        "documents-error-message",
        "documents-retry",
        "documents-content",
        "documents-list",
        "document-detail-section",
        "document-detail-loading",
        "document-detail-empty",
        "document-detail-error",
        "document-detail-error-message",
        "document-detail-retry",
        "document-detail-content",
        "document-detail-title",
        "document-detail-meta",
        "document-download-status",
        "document-download-link",
        "document-acknowledge-status",
        "document-acknowledge-button",
    }
    assert required <= parser.ids
    assert "Documenti condivisi disponibili" in parser.aria_labels
    assert "Informazioni documento" in parser.aria_labels
    assert "polite" in parser.live_regions
    assert "alert" in parser.roles

    css = APP_CSS.read_text(encoding="utf-8")
    assert ".documents-section" in css
    assert ".documents-layout" in css
    assert ".document-card" in css
    assert "@media (min-width: 760px)" in css


def test_p6_6_uses_only_authorized_document_apis_and_no_cross_feature_calls():
    source = APP_JS.read_text(encoding="utf-8")
    assert "apiRequest(`/properties/${encodeURIComponent(String(propertyAtStart))}/documents`)" in source
    assert "apiRequest(`/documents/${encodeURIComponent(String(id))}`)" in source
    assert "`${API_BASE}/documents/${encodeURIComponent(String(id))}/download`" in source
    assert "apiRequest(`/documents/${encodeURIComponent(String(id))}/acknowledge`, { method: 'POST' })" in source

    start = source.index("async function loadDocuments")
    end = source.index("async function loadRequests", start)
    document_block = source[start:end]
    assert "/feedback" not in document_block
    assert "/notifications" not in document_block
    assert "/notification-preferences" not in document_block


def test_p6_6_safe_dom_privacy_and_native_download_contract():
    source = APP_JS.read_text(encoding="utf-8")
    assert "JSON.stringify(payload" not in source
    assert "Object.entries(payload" not in source
    assert "Object.keys(payload" not in source
    assert "documentDetailTitle.textContent" in source
    assert "addDocumentMeta('Tipo'" in source
    assert "addDocumentMeta('File'" in source

    for forbidden in (
        ".innerHTML",
        ".outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "eval(",
        "new Function",
        "localStorage",
        "sessionStorage",
        "document.cookie",
        "FileReader",
        ".blob(",
        ".arrayBuffer(",
        "base64",
    ):
        assert forbidden not in source

    for forbidden in (
        "storage_key",
        "storage_locator",
        "bucket",
        "r2_endpoint",
        "owner_account_id",
        "contact_id",
        "lead_id",
        "visitor_id",
        "flow_payload",
        "match_id",
        "buy_id",
    ):
        assert forbidden not in source.lower()

    # The binary is not fetched into JS memory: the UI builds a same-origin native link.
    assert "documentDownloadLink.setAttribute('href'" in source
    assert "documentDownloadLink.setAttribute('target', '_blank')" in source
    assert "documentDownloadLink.setAttribute('rel', 'noopener')" in source
    assert "apiRequest(`/documents/${encodeURIComponent(String(id))}/download`)" not in source


def test_p6_6_documents_are_requested_only_after_authenticated_property_selection():
    routes = _p6_6_base_routes(61, [])
    _run_node_scenario(
        routes,
        """
const urls = calls.map((call) => call.url);
const sessionIndex = urls.findIndex((url) => url.endsWith('/session'));
const dashboardIndex = urls.findIndex((url) => url.endsWith('/dashboard'));
const propertyIndex = urls.findIndex((url) => url.endsWith('/properties/61'));
const documentsIndex = urls.findIndex((url) => url.endsWith('/properties/61/documents'));
assert(sessionIndex === 0, 'documents order: session must be first');
assert(dashboardIndex > sessionIndex, 'documents order: dashboard must follow session');
assert(propertyIndex > dashboardIndex, 'documents order: property must follow dashboard');
assert(documentsIndex > propertyIndex, 'documents order: list must follow selected property');
assert(ids['documents-empty'].hidden === false, 'documents order: empty state should be visible');
""",
    )


def test_p6_6_zero_documents_runtime_shows_empty_state():
    _run_node_scenario(
        _p6_6_base_routes(62, []),
        """
assert(ids['documents-empty'].hidden === false, 'zero documents: empty state missing');
assert(ids['documents-content'].hidden === true, 'zero documents: content should stay hidden');
assert(ids['documents-list'].children.length === 0, 'zero documents: list should be empty');
assert(!calls.some((call) => call.url.includes('/documents/') && !call.url.endsWith('/properties/62/documents')), 'zero documents: detail must not auto-open');
""",
    )


def test_p6_6_one_and_multiple_documents_preserve_backend_order_without_auto_open():
    items = [
        _document_item(72, title="Secondo dal backend"),
        _document_item(71, title="Primo dal backend", acknowledgement_required=False),
    ]
    _run_node_scenario(
        _p6_6_base_routes(63, items),
        """
assert(ids['documents-content'].hidden === false, 'documents list: content missing');
assert(ids['documents-list'].children.length === 2, 'documents list: item count mismatch');
assert(allText(ids['documents-list'].children[0]).includes('Secondo dal backend'), 'documents list: backend order changed');
assert(allText(ids['documents-list'].children[1]).includes('Primo dal backend'), 'documents list: backend order changed');
assert(ids['document-detail-empty'].hidden === false, 'documents list: detail must await selection');
assert(!calls.some((call) => call.url.endsWith('/documents/72')), 'documents list: detail auto-loaded');
""",
    )


def test_p6_6_detail_uses_exact_public_whitelist_renders_xss_as_text_and_builds_native_download():
    item = _document_item(73, title="<script>alert(1)</script>.pdf")
    routes = _p6_6_base_routes(64, [item])
    routes["/api/owner/portal/documents/73"] = [
        {
            "status": 200,
            "body": {
                "document": {
                    **item,
                    "public_title": "<img src=x onerror=alert(2)>",
                    "download_filename": "<script>alert(3)</script>.pdf",
                    "owner_account_id": 999,
                    "property_id": 64,
                    "storage_key": "PRIVATE-LOCATOR",
                    "bucket": "PRIVATE-BUCKET",
                    "endpoint": "PRIVATE-ENDPOINT",
                    "sha256": "PRIVATE-HASH",
                    "metadata": {"internal": True},
                },
                "read": {"view_count": 1, "owner_account_id": 999},
            },
        }
    ]
    _run_node_scenario(
        routes,
        """
const card = ids['documents-list'].children[0].children[0];
assert(allText(card).includes('<script>alert(1)</script>.pdf'), 'document xss: list title should remain literal text');
await card.trigger('click');
await flush();
assert(ids['document-detail-content'].hidden === false, 'document detail: content missing');
assert(ids['document-detail-title'].textContent === '<img src=x onerror=alert(2)>', 'document xss: title not literal');
const meta = allText(ids['document-detail-meta']);
assert(meta.includes('<script>alert(3)</script>.pdf'), 'document xss: filename not literal');
assert(meta.includes('application/pdf'), 'document detail: MIME missing');
assert(meta.includes('1.5 KB'), 'document detail: size missing');
assert(!meta.includes('PRIVATE-LOCATOR'), 'document privacy: internal locator leaked');
assert(!meta.includes('PRIVATE-BUCKET'), 'document privacy: private bucket leaked');
assert(!meta.includes('PRIVATE-ENDPOINT'), 'document privacy: private endpoint leaked');
assert(!meta.includes('PRIVATE-HASH'), 'document privacy: private hash leaked');
assert(ids['document-download-link'].getAttribute('href') === '/api/owner/portal/documents/73/download', 'document download: wrong endpoint');
assert(ids['document-download-link'].getAttribute('download') === '<script>alert(3)</script>.pdf', 'document download: public filename not used');
assert(ids['document-download-link'].getAttribute('target') === '_blank', 'document download: native isolated target missing');
assert(ids['document-download-link'].getAttribute('rel') === 'noopener', 'document download: noopener missing');
assert(!calls.some((call) => call.url.endsWith('/documents/73/download')), 'document download: binary must not be buffered by JS');
assert(!calls.some((call) => call.url.endsWith('/documents/73/acknowledge')), 'document detail view must not acknowledge');
""",
    )


def test_p6_6_download_unavailable_hides_native_link_and_never_constructs_external_url():
    item = _document_item(74, download_available=False)
    routes = _p6_6_base_routes(65, [item])
    routes["/api/owner/portal/documents/74"] = [
        {"status": 200, "body": {"document": item, "read": {"view_count": 1}}}
    ]
    _run_node_scenario(
        routes,
        """
await ids['documents-list'].children[0].children[0].trigger('click');
await flush();
assert(ids['document-download-link'].hidden === true, 'document unavailable: download link should be hidden');
assert(ids['document-download-link'].getAttribute('href') === '#', 'document unavailable: unsafe href should not exist');
assert(ids['document-download-status'].textContent === 'Download non disponibile per questo documento.', 'document unavailable: neutral status missing');
""",
    )


def test_p6_6_persistent_acknowledged_state_from_dto_disables_action_without_post():
    acknowledged = "2026-08-12T15:00:00Z"
    item = _document_item(75, acknowledged_at=acknowledged)
    routes = _p6_6_base_routes(66, [item])
    routes["/api/owner/portal/documents/75"] = [
        {"status": 200, "body": {"document": item, "read": {"acknowledged_at": acknowledged}}}
    ]
    _run_node_scenario(
        routes,
        """
assert(allText(ids['documents-list']).includes('Presa visione confermata'), 'persistent ack: list state missing');
await ids['documents-list'].children[0].children[0].trigger('click');
await flush();
assert(ids['document-acknowledge-button'].hidden === false, 'persistent ack: required action should remain visible as state');
assert(ids['document-acknowledge-button'].disabled === true, 'persistent ack: button should be disabled');
assert(ids['document-acknowledge-button'].textContent === 'Presa visione confermata', 'persistent ack: success label missing');
assert(ids['document-acknowledge-status'].textContent.includes('Presa visione confermata'), 'persistent ack: status missing');
assert(!calls.some((call) => call.url.endsWith('/documents/75/acknowledge')), 'persistent ack: unexpected POST');
""",
    )


def test_p6_6_acknowledge_is_explicit_double_click_safe_updates_only_document_ui():
    item = _document_item(76)
    routes = _p6_6_base_routes(67, [item])
    routes["/api/owner/portal/documents/76"] = [
        {"status": 200, "body": {"document": item, "read": {"view_count": 1}}}
    ]
    routes["/api/owner/portal/documents/76/acknowledge"] = [
        {"status": 200, "delay_ms": 20, "body": {"acknowledged_at": "2026-08-12T16:00:00Z"}}
    ]
    _run_node_scenario(
        routes,
        """
await ids['documents-list'].children[0].children[0].trigger('click');
await flush();
assert(ids['document-acknowledge-status'].textContent.includes('non equivale'), 'document ack: view/ack distinction missing');
assert(calls.filter((call) => call.url.endsWith('/documents/76/acknowledge')).length === 0, 'document ack: detail auto-acknowledged');
const beforeDashboardCalls = calls.filter((call) => call.url.endsWith('/dashboard')).length;
await Promise.all([ids['document-acknowledge-button'].trigger('click'), ids['document-acknowledge-button'].trigger('click')]);
await new Promise((resolve) => setTimeout(resolve, 30));
await flush();
const ackCalls = calls.filter((call) => call.url.endsWith('/documents/76/acknowledge'));
assert(ackCalls.length === 1, 'document ack: double submit was not blocked');
assert(ackCalls[0].method === 'POST', 'document ack: wrong method');
assert(ids['document-acknowledge-button'].disabled === true, 'document ack: button should be disabled after success');
assert(ids['document-acknowledge-button'].textContent === 'Presa visione confermata', 'document ack: success label missing');
assert(allText(ids['documents-list']).includes('Presa visione confermata'), 'document ack: list state not updated');
assert(calls.filter((call) => call.url.endsWith('/dashboard')).length === beforeDashboardCalls, 'document ack: dashboard was unnecessarily reloaded');
""",
    )


def test_p6_6_view_and_native_download_do_not_acknowledge():
    item = _document_item(77)
    routes = _p6_6_base_routes(68, [item])
    routes["/api/owner/portal/documents/77"] = [
        {"status": 200, "body": {"document": item, "read": {"view_count": 1}}}
    ]
    _run_node_scenario(
        routes,
        """
await ids['documents-list'].children[0].children[0].trigger('click');
await flush();
await ids['document-download-link'].trigger('click');
await flush();
assert(ids['document-download-status'].textContent.includes('Download richiesto tramite il portale autenticato'), 'document download: status missing');
assert(calls.filter((call) => call.url.endsWith('/documents/77')).length === 1, 'document view: detail GET missing');
assert(calls.filter((call) => call.url.endsWith('/documents/77/acknowledge')).length === 0, 'document view/download must not acknowledge');
assert(calls.filter((call) => call.url.endsWith('/documents/77/download')).length === 0, 'document download should remain browser-native, not JS fetch');
""",
    )


def test_p6_6_property_switch_resets_documents_and_ignores_stale_list_response():
    routes = {
        "/api/owner/portal/session": [{"status": 200, "body": {"authenticated": True}}],
        "/api/owner/portal/dashboard": [
            {
                "status": 200,
                "body": {
                    "property_count": 2,
                    "properties": [
                        {"id": 69, "title": "Casa Uno", "is_primary": True},
                        {"id": 70, "title": "Casa Due"},
                    ],
                },
            }
        ],
        "/api/owner/portal/properties/69": [{"status": 200, "body": {"property": {"title": "Casa Uno"}}}],
        "/api/owner/portal/properties/69/timeline": [{"status": 200, "body": {"items": []}}],
        "/api/owner/portal/properties/69/documents": [
            {"status": 200, "delay_ms": 60, "body": {"items": [_document_item(901, title="VECCHIO DOCUMENTO")]}}
        ],
        "/api/owner/portal/properties/70": [{"status": 200, "body": {"property": {"title": "Casa Due"}}}],
        "/api/owner/portal/properties/70/timeline": [{"status": 200, "body": {"items": []}}],
        "/api/owner/portal/properties/70/documents": [
            {"status": 200, "body": {"items": [_document_item(902, title="NUOVO DOCUMENTO")]}}
        ],
        "/api/owner/portal/properties/70/visit-feedback?limit=50&offset=0": [{"status": 200, "body": {"items": []}}],
    }
    _run_node_scenario(
        routes,
        """
await new Promise((resolve) => setTimeout(resolve, 10));
await ids['property-list'].children[1].children[0].trigger('click');
await new Promise((resolve) => setTimeout(resolve, 80));
await flush();
const text = allText(ids['documents-list']);
assert(text.includes('NUOVO DOCUMENTO'), 'document stale: new property document missing');
assert(!text.includes('VECCHIO DOCUMENTO'), 'document stale: old property response leaked');
assert(ids['documents-list'].children.length === 1, 'document stale: list was not reset');
assert(ids['document-detail-content'].hidden === true, 'document stale: old detail survived property switch');
assert(ids['document-detail-empty'].hidden === false, 'document stale: detail empty state missing');
""",
    )


def test_p6_6_documents_list_404_with_valid_session_is_neutral_and_session_loss_logs_out():
    valid_routes = _p6_6_base_routes(71, [])
    valid_routes["/api/owner/portal/session"] = [
        {"status": 200, "body": {"authenticated": True}},
        {"status": 200, "body": {"authenticated": True}},
    ]
    valid_routes["/api/owner/portal/properties/71/documents"] = [
        {"status": 404, "body": {"detail": "Risorsa non trovata"}}
    ]
    _run_node_scenario(
        valid_routes,
        """
assert(ids['app-view'].hidden === false, 'documents 404: app should remain');
assert(ids['documents-error'].hidden === false, 'documents 404: error state missing');
assert(ids['documents-error-message'].textContent === 'Contenuto non disponibile o accesso non più valido.', 'documents 404: neutral message mismatch');
const listIndex = calls.findIndex((call) => call.url.endsWith('/properties/71/documents'));
assert(calls.slice(listIndex + 1).some((call) => call.url.endsWith('/session')), 'documents 404: session probe missing');
""",
    )

    lost_routes = _p6_6_base_routes(72, [])
    lost_routes["/api/owner/portal/session"] = [
        {"status": 200, "body": {"authenticated": True}},
        {"status": 404, "body": {"detail": "Risorsa non trovata"}},
    ]
    lost_routes["/api/owner/portal/properties/72/documents"] = [
        {"status": 404, "body": {"detail": "Risorsa non trovata"}}
    ]
    _run_node_scenario(
        lost_routes,
        """
assert(ids['login-view'].hidden === false, 'documents auth loss: login not shown');
assert(ids['app-view'].hidden === true, 'documents auth loss: app still visible');
assert(ids['auth-message'].textContent === 'Sessione non disponibile o scaduta.', 'documents auth loss: neutral session message missing');
assert(calls[calls.length - 1].url.endsWith('/session'), 'documents auth loss: requests continued after session probe');
""",
    )


def test_p6_6_document_detail_404_with_valid_session_and_session_loss_are_handled():
    item = _document_item(78)
    valid_routes = _p6_6_base_routes(73, [item])
    valid_routes["/api/owner/portal/session"] = [
        {"status": 200, "body": {"authenticated": True}},
        {"status": 200, "body": {"authenticated": True}},
    ]
    valid_routes["/api/owner/portal/documents/78"] = [
        {"status": 404, "body": {"detail": "Risorsa non trovata"}}
    ]
    _run_node_scenario(
        valid_routes,
        """
await ids['documents-list'].children[0].children[0].trigger('click');
await flush();
assert(ids['document-detail-error'].hidden === false, 'document detail 404: error state missing');
assert(ids['document-detail-error-message'].textContent === 'Documento non disponibile o accesso non più valido.', 'document detail 404: neutral message mismatch');
assert(calls[calls.length - 1].url.endsWith('/session'), 'document detail 404: session probe missing');
""",
    )

    lost_routes = _p6_6_base_routes(74, [item])
    lost_routes["/api/owner/portal/session"] = [
        {"status": 200, "body": {"authenticated": True}},
        {"status": 404, "body": {"detail": "Risorsa non trovata"}},
    ]
    lost_routes["/api/owner/portal/documents/78"] = [
        {"status": 404, "body": {"detail": "Risorsa non trovata"}}
    ]
    _run_node_scenario(
        lost_routes,
        """
await ids['documents-list'].children[0].children[0].trigger('click');
await flush();
assert(ids['login-view'].hidden === false, 'document detail auth loss: login not shown');
assert(ids['app-view'].hidden === true, 'document detail auth loss: app still visible');
""",
    )


def test_p6_6_acknowledge_404_and_session_loss_are_neutral_and_fail_closed():
    item = _document_item(79)
    valid_routes = _p6_6_base_routes(75, [item])
    valid_routes["/api/owner/portal/session"] = [
        {"status": 200, "body": {"authenticated": True}},
        {"status": 200, "body": {"authenticated": True}},
    ]
    valid_routes["/api/owner/portal/documents/79"] = [
        {"status": 200, "body": {"document": item, "read": {"view_count": 1}}}
    ]
    valid_routes["/api/owner/portal/documents/79/acknowledge"] = [
        {"status": 404, "body": {"detail": "Risorsa non trovata"}}
    ]
    _run_node_scenario(
        valid_routes,
        """
await ids['documents-list'].children[0].children[0].trigger('click');
await flush();
await ids['document-acknowledge-button'].trigger('click');
await flush();
assert(ids['document-detail-error'].hidden === false, 'document ack 404: detail error missing');
assert(ids['document-detail-error-message'].textContent === 'Documento non disponibile o accesso non più valido.', 'document ack 404: neutral message mismatch');
assert(calls[calls.length - 1].url.endsWith('/session'), 'document ack 404: session probe missing');
""",
    )

    lost_routes = _p6_6_base_routes(76, [item])
    lost_routes["/api/owner/portal/session"] = [
        {"status": 200, "body": {"authenticated": True}},
        {"status": 404, "body": {"detail": "Risorsa non trovata"}},
    ]
    lost_routes["/api/owner/portal/documents/79"] = [
        {"status": 200, "body": {"document": item, "read": {"view_count": 1}}}
    ]
    lost_routes["/api/owner/portal/documents/79/acknowledge"] = [
        {"status": 404, "body": {"detail": "Risorsa non trovata"}}
    ]
    _run_node_scenario(
        lost_routes,
        """
await ids['documents-list'].children[0].children[0].trigger('click');
await flush();
await ids['document-acknowledge-button'].trigger('click');
await flush();
assert(ids['login-view'].hidden === false, 'document ack auth loss: login not shown');
assert(ids['app-view'].hidden === true, 'document ack auth loss: app still visible');
""",
    )


def test_p6_6_list_detail_and_ack_handle_422_429_5xx_network_without_raw_payloads():
    list_cases = [
        ({"status": 422, "body": {"secret": "raw-422"}}, "Impossibile caricare i documenti con i dati disponibili."),
        ({"status": 429, "body": {"secret": "raw-429"}}, "Troppe richieste. Riprova tra poco."),
        ({"status": 500, "body": {"secret": "raw-500"}}, "Servizio temporaneamente non disponibile."),
        ({"network_error": True}, "Connessione non disponibile. Controlla la rete e riprova."),
    ]
    for response, expected in list_cases:
        routes = _p6_6_base_routes(80, [])
        routes["/api/owner/portal/properties/80/documents"] = [response]
        _run_node_scenario(
            routes,
            f"""
assert(ids['documents-error'].hidden === false, 'documents list error: state missing');
assert(ids['documents-error-message'].textContent === {json.dumps(expected)}, 'documents list error: wrong safe message');
assert(!ids['documents-error-message'].textContent.includes('raw-'), 'documents list error: raw payload leaked');
""",
        )

    detail_cases = [
        ({"status": 422, "body": {"secret": "raw-422"}}, "Impossibile caricare il contenuto del documento."),
        ({"status": 429, "body": {"secret": "raw-429"}}, "Troppe richieste. Riprova tra poco."),
        ({"status": 500, "body": {"secret": "raw-500"}}, "Servizio temporaneamente non disponibile."),
        ({"network_error": True}, "Connessione non disponibile. Controlla la rete e riprova."),
    ]
    for response, expected in detail_cases:
        item = _document_item(81)
        routes = _p6_6_base_routes(81, [item])
        routes["/api/owner/portal/documents/81"] = [response]
        _run_node_scenario(
            routes,
            f"""
await ids['documents-list'].children[0].children[0].trigger('click');
await flush();
assert(ids['document-detail-error'].hidden === false, 'document detail error: state missing');
assert(ids['document-detail-error-message'].textContent === {json.dumps(expected)}, 'document detail error: wrong safe message');
assert(!ids['document-detail-error-message'].textContent.includes('raw-'), 'document detail error: raw payload leaked');
""",
        )

    item = _document_item(82)
    routes = _p6_6_base_routes(82, [item])
    routes["/api/owner/portal/documents/82"] = [
        {"status": 200, "body": {"document": item, "read": {"view_count": 1}}}
    ]
    routes["/api/owner/portal/documents/82/acknowledge"] = [
        {"status": 500, "body": {"secret": "raw-ack"}}
    ]
    _run_node_scenario(
        routes,
        """
await ids['documents-list'].children[0].children[0].trigger('click');
await flush();
await ids['document-acknowledge-button'].trigger('click');
await flush();
assert(ids['document-acknowledge-status'].textContent === 'Servizio temporaneamente non disponibile.', 'document ack error: safe message mismatch');
assert(!ids['document-acknowledge-status'].textContent.includes('raw-ack'), 'document ack error: raw payload leaked');
assert(ids['document-acknowledge-button'].disabled === false, 'document ack error: retry should remain possible');
""",
    )


def test_p6_6_download_404_contract_is_backend_only_native_and_app_state_is_not_rewritten():
    """Native downloads intentionally do not fetch/buffer the binary in app.js.

    A stale authorization race can therefore return 404 from the download endpoint itself;
    the frontend keeps a same-origin isolated native link and never exposes an alternate locator.
    """
    item = _document_item(83)
    routes = _p6_6_base_routes(83, [item])
    routes["/api/owner/portal/documents/83"] = [
        {"status": 200, "body": {"document": item, "read": {"view_count": 1}}}
    ]
    _run_node_scenario(
        routes,
        """
await ids['documents-list'].children[0].children[0].trigger('click');
await flush();
const href = ids['document-download-link'].getAttribute('href');
assert(href === '/api/owner/portal/documents/83/download', 'download 404 contract: only backend endpoint is allowed');
assert(ids['document-download-link'].getAttribute('target') === '_blank', 'download 404 contract: app should not be replaced by an error response');
assert(!calls.some((call) => call.url.endsWith('/documents/83/download')), 'download 404 contract: JS must not fetch/buffer binary');
""",
    )


def test_p6_6_document_transport_scope_and_accessibility_remain_memory_only():
    source = APP_JS.read_text(encoding="utf-8")
    html = INDEX.read_text(encoding="utf-8")
    assert "documentGeneration" in source
    assert "documentDetailGeneration" in source
    assert "documentAcknowledgeInFlight" in source
    assert "dataset.documentId" in source
    assert "aria-pressed" in source
    assert "aria-controls" in source
    assert 'aria-label="Documenti condivisi disponibili"' in html
    assert 'aria-label="Informazioni documento"' in html
    assert "localStorage" not in source
    assert "sessionStorage" not in source
    assert "document.cookie" not in source

    # No P6.7+ features were introduced.
    assert "apiRequest('/notifications" not in source


# OWNER 0.2 P6.7 - Richieste proprietario ----------------------------------

REQUEST_TYPES = {
    "contact_request",
    "correction_request",
    "general_message",
    "strategy_feedback",
    "price_review",
    "availability_update",
    "document_question",
}
REQUEST_STATUSES = {"new", "in_review", "handled", "closed"}


def _request_item(
    i: int,
    *,
    feedback_type: str = "general_message",
    subject: str | None = None,
    message: str | None = None,
    status: str = "new",
    public_response: str | None = None,
) -> dict:
    return {
        "id": i,
        "owner_account_id": 999,
        "property_id": 999,
        "feedback_type": feedback_type,
        "subject": subject or f"Richiesta {i}",
        "message": message or f"Messaggio {i}",
        "status": status,
        "submitted_at": "2026-08-13T10:00:00Z",
        "availability_from": None,
        "availability_to": None,
        "handled_at": "2026-08-13T11:00:00Z" if status in {"handled", "closed"} else None,
        "handled_by": "INTERNAL ADMIN",
        "linked_activity_id": 777,
        "public_response": public_response,
        "created_at": "2026-08-13T10:00:00Z",
        "updated_at": "2026-08-13T11:00:00Z",
        "internal_notes": "PRIVATE",
        "BUY": "PRIVATE BUY",
        "MATCH": "PRIVATE MATCH",
        "FLOW": "PRIVATE FLOW",
    }


def _p6_7_base_routes(property_id: int, requests: list[dict]) -> dict[str, list[dict]]:
    return {
        "/api/owner/portal/session": [{"status": 200, "body": {"authenticated": True}}],
        "/api/owner/portal/dashboard": [
            {"status": 200, "body": {"property_count": 1, "properties": [{"id": property_id, "title": "Casa"}]}}
        ],
        f"/api/owner/portal/properties/{property_id}": [
            {"status": 200, "body": {"property": {"title": "Casa"}}}
        ],
        f"/api/owner/portal/properties/{property_id}/timeline": [{"status": 200, "body": {"items": []}}],
        f"/api/owner/portal/properties/{property_id}/documents": [{"status": 200, "body": {"items": []}}],
        f"/api/owner/portal/properties/{property_id}/visit-feedback?limit=50&offset=0": [
            {"status": 200, "body": {"items": []}}
        ],
        f"/api/owner/portal/properties/{property_id}/feedback": [{"status": 200, "body": {"items": requests}}],
    }


def test_p6_7_precheck_real_backend_feedback_contract_and_routes():
    assert set(get_args(FeedbackCreate.model_fields["feedback_type"].annotation)) == REQUEST_TYPES

    valid = FeedbackCreate(feedback_type="general_message", subject="x" * 150, message="m" * 5000)
    assert valid.subject == "x" * 150
    assert valid.message == "m" * 5000
    with pytest.raises(ValidationError):
        FeedbackCreate(feedback_type="general_message", subject="x" * 151, message="ok")
    with pytest.raises(ValidationError):
        FeedbackCreate(feedback_type="general_message", subject="ok", message="m" * 5001)
    with pytest.raises(ValidationError):
        FeedbackCreate(feedback_type="availability_update", subject="Disponibilità", message="Test")
    now = datetime.now(timezone.utc)
    with pytest.raises(ValidationError):
        FeedbackCreate(
            feedback_type="availability_update",
            subject="Disponibilità",
            message="Test",
            availability_from=now,
            availability_to=now - timedelta(minutes=1),
        )

    router = (ROOT / "owner" / "router_portal.py").read_text(encoding="utf-8")
    assert '@router.post("/properties/{p}/feedback", status_code=201, response_model=FeedbackPublic)' in router
    assert '@router.get("/properties/{p}/feedback", response_model=FeedbackListResponse)' in router
    repository = (ROOT / "owner" / "repository.py").read_text(encoding="utf-8")
    create_start = repository.index("def create_feedback(a,p,d):")
    create_end = repository.index("def dashboard():", create_start)
    feedback_block = repository[create_start:create_end]
    assert "d['feedback_type']" in feedback_block
    assert "d['subject']" in feedback_block
    assert "d['message']" in feedback_block
    assert "d.get('availability_from')" in feedback_block
    assert "d.get('availability_to')" in feedback_block
    assert "ORDER BY submitted_at DESC" in feedback_block
    assert "SELECT feedback_type,subject,message,status,submitted_at" in feedback_block
    assert "SELECT * FROM owner_feedback WHERE owner_account_id" not in feedback_block

    migration = (ROOT / "migrations" / "009_owner_01.sql").read_text(encoding="utf-8")
    migration_p1 = (ROOT / "migrations" / "010_owner_02_p1.sql").read_text(encoding="utf-8")
    for status in REQUEST_STATUSES:
        assert f"'{status}'" in migration
    assert "ADD COLUMN public_response TEXT" in migration_p1
    assert "ADD COLUMN availability_from TIMESTAMPTZ" in migration_p1
    assert "ADD COLUMN availability_to TIMESTAMPTZ" in migration_p1


def test_p6_7_markup_has_real_enum_form_accessibility_and_history_states():
    parser = _html_parser()
    required = {
        "requests-section",
        "requests-title",
        "request-form",
        "request-type",
        "request-subject",
        "request-message",
        "request-availability-fields",
        "request-availability-from",
        "request-availability-to",
        "request-submit",
        "request-form-status",
        "requests-loading",
        "requests-empty",
        "requests-error",
        "requests-error-message",
        "requests-retry",
        "requests-content",
        "requests-list",
    }
    assert required <= parser.ids
    assert {"request-type", "request-subject", "request-message", "request-availability-from", "request-availability-to"} <= parser.labels_for
    assert "Storico richieste proprietario" in parser.aria_labels
    html = INDEX.read_text(encoding="utf-8")
    for value in REQUEST_TYPES:
        assert f'value="{value}"' in html
    assert 'maxlength="150"' in html
    assert 'maxlength="5000"' in html
    css = APP_CSS.read_text(encoding="utf-8")
    assert ".requests-section" in css
    assert ".requests-layout" in css
    assert ".request-card" in css
    assert "@media (min-width: 860px)" in css


def test_p6_7_frontend_whitelist_is_explicit_and_has_no_generic_json_or_private_rendering():
    source = APP_JS.read_text(encoding="utf-8")
    start = source.index("function requestPublicView(item)")
    end = source.index("function appendRequestMeta", start)
    block = source[start:end]
    expected = {
        "feedback_type",
        "subject",
        "message",
        "status",
        "submitted_at",
        "availability_from",
        "availability_to",
        "handled_at",
        "public_response",
    }
    import re
    actual = set(re.findall(r"^\s*([a-z_]+):", block, flags=re.MULTILINE))
    assert actual == expected
    assert "JSON.stringify(payload" not in source
    assert "Object.entries(payload" not in source
    assert "Object.keys(payload" not in source
    for forbidden in (
        ".innerHTML",
        ".outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "eval(",
        "new Function",
        "localStorage",
        "sessionStorage",
        "document.cookie",
    ):
        assert forbidden not in source


def test_p6_7_history_loads_only_after_property_and_preserves_backend_order():
    items = [
        _request_item(2, subject="Seconda dal backend", status="handled", public_response="Risposta due"),
        _request_item(1, subject="Prima dal backend", status="new"),
    ]
    _run_node_scenario(
        _p6_7_base_routes(90, items),
        """
const urls = calls.map((call) => call.url);
const propertyIndex = urls.findIndex((url) => url.endsWith('/properties/90'));
const requestsIndex = urls.findIndex((url) => url.endsWith('/properties/90/feedback'));
assert(requestsIndex > propertyIndex, 'requests order: history must follow selected property');
assert(ids['requests-content'].hidden === false, 'requests history: content missing');
assert(ids['requests-list'].children.length === 2, 'requests history: item count mismatch');
assert(allText(ids['requests-list'].children[0]).includes('Seconda dal backend'), 'requests history: backend order changed');
assert(allText(ids['requests-list'].children[1]).includes('Prima dal backend'), 'requests history: backend order changed');
assert(allText(ids['requests-list'].children[0]).includes('Gestita'), 'requests status: handled label missing');
assert(allText(ids['requests-list'].children[0]).includes('Risposta due'), 'requests public response missing');
""",
    )


def test_p6_7_zero_requests_shows_empty_state_and_one_request_renders():
    _run_node_scenario(
        _p6_7_base_routes(91, []),
        """
assert(ids['requests-empty'].hidden === false, 'requests empty: state missing');
assert(ids['requests-content'].hidden === true, 'requests empty: content should be hidden');
assert(ids['requests-list'].children.length === 0, 'requests empty: list should be empty');
""",
    )
    _run_node_scenario(
        _p6_7_base_routes(92, [_request_item(1, subject="Una richiesta")]),
        """
assert(ids['requests-content'].hidden === false, 'one request: content missing');
assert(ids['requests-list'].children.length === 1, 'one request: card missing');
assert(allText(ids['requests-list']).includes('Una richiesta'), 'one request: subject missing');
""",
    )


def test_p6_7_history_privacy_and_xss_use_public_fields_as_text_only():
    item = _request_item(
        3,
        subject="<script>alert(1)</script>",
        message="<img src=x onerror=alert(2)>",
        status="closed",
        public_response="<script>alert(3)</script>",
    )
    _run_node_scenario(
        _p6_7_base_routes(93, [item]),
        """
const text = allText(ids['requests-list']);
assert(text.includes('<script>alert(1)</script>'), 'requests xss: subject should remain literal text');
assert(text.includes('<img src=x onerror=alert(2)>'), 'requests xss: message should remain literal text');
assert(text.includes('<script>alert(3)</script>'), 'requests xss: public response should remain literal text');
for (const forbidden of ['PRIVATE', 'INTERNAL ADMIN', 'PRIVATE BUY', 'PRIVATE MATCH', 'PRIVATE FLOW', '999', '777']) {
  assert(!text.includes(forbidden), `requests privacy: leaked ${forbidden}`);
}
""",
    )


def test_p6_7_client_validation_required_lengths_enum_and_availability_rules():
    routes = _p6_7_base_routes(94, [])
    _run_node_scenario(
        routes,
        """
ids['request-type'].value = '';
ids['request-subject'].value = 'Oggetto';
ids['request-message'].value = 'Messaggio';
await ids['request-form'].trigger('submit');
assert(ids['request-form-status'].textContent.includes('tipo di richiesta'), 'validation: enum required message missing');
assert(document.activeElement === ids['request-type'], 'validation: enum focus missing');
assert(calls.filter((call) => call.method === 'POST' && call.url.endsWith('/feedback')).length === 0, 'validation: invalid enum submitted');

ids['request-type'].value = 'general_message';
ids['request-subject'].value = '   ';
await ids['request-form'].trigger('submit');
assert(ids['request-form-status'].textContent.includes('oggetto'), 'validation: blank subject missing');
assert(document.activeElement === ids['request-subject'], 'validation: subject focus missing');

ids['request-subject'].value = 'x'.repeat(151);
await ids['request-form'].trigger('submit');
assert(ids['request-form-status'].textContent.includes('150'), 'validation: subject max missing');

ids['request-subject'].value = 'Oggetto';
ids['request-message'].value = '   ';
await ids['request-form'].trigger('submit');
assert(ids['request-form-status'].textContent.includes('messaggio'), 'validation: blank message missing');

ids['request-message'].value = 'm'.repeat(5001);
await ids['request-form'].trigger('submit');
assert(ids['request-form-status'].textContent.includes('5.000'), 'validation: message max missing');

ids['request-message'].value = 'Messaggio';
ids['request-type'].value = 'availability_update';
await ids['request-type'].trigger('change');
assert(ids['request-availability-fields'].hidden === false, 'availability: fields should be visible');
await ids['request-form'].trigger('submit');
assert(ids['request-form-status'].textContent.includes('almeno una'), 'availability: at least one bound missing');
ids['request-availability-from'].value = '2026-08-14T12:00';
ids['request-availability-to'].value = '2026-08-14T11:00';
await ids['request-form'].trigger('submit');
assert(ids['request-form-status'].textContent.includes('successiva'), 'availability: order validation missing');
assert(calls.filter((call) => call.method === 'POST' && call.url.endsWith('/feedback')).length === 0, 'validation: invalid availability submitted');
""",
    )


def test_p6_7_submit_uses_exact_backend_payload_double_submit_guard_and_refreshes_history():
    routes = _p6_7_base_routes(95, [])
    routes["/api/owner/portal/properties/95/feedback"] = [
        {"status": 200, "body": {"items": []}},
        {"status": 201, "delay_ms": 20, "body": _request_item(10)},
        {"status": 200, "body": {"items": [_request_item(10, subject="Nuova richiesta")] }},
    ]
    _run_node_scenario(
        routes,
        """
ids['request-type'].value = 'availability_update';
await ids['request-type'].trigger('change');
ids['request-subject'].value = '  Nuova richiesta  ';
ids['request-message'].value = '  Possiamo sentirci domani?  ';
ids['request-availability-from'].value = '2026-08-14T09:00';
ids['request-availability-to'].value = '2026-08-14T11:00';
const first = ids['request-form'].trigger('submit');
await new Promise((resolve) => setTimeout(resolve, 5));
assert(ids['request-submit'].disabled === true, 'submit: loading button not disabled');
assert(ids['request-submit'].textContent === 'Invio in corso…', 'submit: loading label missing');
const second = ids['request-form'].trigger('submit');
await Promise.all([first, second]);
await flush();
const posts = calls.filter((call) => call.method === 'POST' && call.url.endsWith('/properties/95/feedback'));
assert(posts.length === 1, 'submit: double submit not blocked');
const payload = JSON.parse(posts[0].body);
assert(payload.feedback_type === 'availability_update', 'submit payload: feedback_type mismatch');
assert(payload.subject === 'Nuova richiesta', 'submit payload: subject should be trimmed');
assert(payload.message === 'Possiamo sentirci domani?', 'submit payload: message should be trimmed');
assert(typeof payload.availability_from === 'string' && payload.availability_from.includes('2026-08-14'), 'submit payload: availability_from missing');
assert(typeof payload.availability_to === 'string' && payload.availability_to.includes('2026-08-14'), 'submit payload: availability_to missing');
assert(Object.keys(payload).sort().join(',') === 'availability_from,availability_to,feedback_type,message,subject', 'submit payload: unexpected fields');
assert(ids['request-form-status'].textContent === 'Richiesta inviata correttamente.', 'submit: success confirmation missing');
assert(ids['request-type'].value === '', 'submit: type not reset');
assert(ids['request-subject'].value === '', 'submit: subject not reset');
assert(ids['request-message'].value === '', 'submit: message not reset');
assert(allText(ids['requests-list']).includes('Nuova richiesta'), 'submit: history not refreshed');
const gets = calls.filter((call) => call.method === 'GET' && call.url.endsWith('/properties/95/feedback'));
assert(gets.length === 2, 'submit: history should be fetched once initially and once after success');
""",
    )


def test_p6_7_non_availability_request_omits_availability_fields():
    routes = _p6_7_base_routes(96, [])
    routes["/api/owner/portal/properties/96/feedback"] = [
        {"status": 200, "body": {"items": []}},
        {"status": 201, "body": _request_item(11)},
        {"status": 200, "body": {"items": [_request_item(11)]}},
    ]
    _run_node_scenario(
        routes,
        """
ids['request-type'].value = 'general_message';
ids['request-subject'].value = 'Oggetto';
ids['request-message'].value = 'Messaggio';
ids['request-availability-from'].value = '2026-08-14T09:00';
ids['request-availability-to'].value = '2026-08-14T11:00';
await ids['request-type'].trigger('change');
assert(ids['request-availability-fields'].hidden === true, 'non-availability: availability controls should be hidden');
await ids['request-form'].trigger('submit');
await flush();
const post = calls.find((call) => call.method === 'POST' && call.url.endsWith('/properties/96/feedback'));
const payload = JSON.parse(post.body);
assert(!Object.prototype.hasOwnProperty.call(payload, 'availability_from'), 'non-availability: from leaked into payload');
assert(!Object.prototype.hasOwnProperty.call(payload, 'availability_to'), 'non-availability: to leaked into payload');
""",
    )


def test_p6_7_get_404_is_neutral_with_valid_session_and_auth_loss_logs_out():
    valid = _p6_7_base_routes(97, [])
    valid["/api/owner/portal/session"] = [
        {"status": 200, "body": {"authenticated": True}},
        {"status": 200, "body": {"authenticated": True}},
    ]
    valid["/api/owner/portal/properties/97/feedback"] = [{"status": 404, "body": {"detail": "PRIVATE"}}]
    _run_node_scenario(
        valid,
        """
assert(ids['requests-error'].hidden === false, 'GET 404: error state missing');
assert(ids['requests-error-message'].textContent === 'Contenuto non disponibile o accesso non più valido.', 'GET 404: neutral message mismatch');
assert(ids['app-view'].hidden === false, 'GET 404: valid session should remain authenticated');
assert(!ids['requests-error-message'].textContent.includes('PRIVATE'), 'GET 404: raw payload leaked');
""",
    )

    expired = _p6_7_base_routes(98, [])
    expired["/api/owner/portal/properties/98/feedback"] = [{"status": 403, "body": {"detail": "forbidden"}}]
    _run_node_scenario(
        expired,
        """
assert(ids['login-view'].hidden === false, 'GET auth loss: login should be visible');
assert(ids['app-view'].hidden === true, 'GET auth loss: app should be hidden');
assert(ids['auth-message'].textContent === 'Sessione non disponibile o scaduta.', 'GET auth loss: neutral session message missing');
""",
    )


def test_p6_7_post_422_429_5xx_network_and_404_are_safe_and_retryable():
    cases = [
        (422, "Controlla i campi della richiesta e riprova."),
        (429, "Troppe richieste. Riprova tra poco."),
        (500, "Servizio temporaneamente non disponibile."),
    ]
    for offset, (status, expected) in enumerate(cases):
        pid = 100 + offset
        routes = _p6_7_base_routes(pid, [])
        routes[f"/api/owner/portal/properties/{pid}/feedback"] = [
            {"status": 200, "body": {"items": []}},
            {"status": status, "body": {"secret": "RAW SECRET"}},
        ]
        _run_node_scenario(
            routes,
            f"""
ids['request-type'].value = 'general_message';
ids['request-subject'].value = 'Oggetto';
ids['request-message'].value = 'Messaggio';
await ids['request-form'].trigger('submit');
await flush();
assert(ids['request-form-status'].textContent === {json.dumps(expected)}, 'POST {status}: safe error mismatch');
assert(!ids['request-form-status'].textContent.includes('RAW SECRET'), 'POST {status}: raw payload leaked');
assert(ids['request-submit'].disabled === false, 'POST {status}: retry should be enabled');
""",
        )

    pid = 103
    network = _p6_7_base_routes(pid, [])
    network[f"/api/owner/portal/properties/{pid}/feedback"] = [
        {"status": 200, "body": {"items": []}},
        {"network_error": True},
    ]
    _run_node_scenario(
        network,
        """
ids['request-type'].value = 'general_message';
ids['request-subject'].value = 'Oggetto';
ids['request-message'].value = 'Messaggio';
await ids['request-form'].trigger('submit');
await flush();
assert(ids['request-form-status'].textContent === 'Connessione non disponibile. Controlla la rete e riprova.', 'POST network: safe error mismatch');
assert(ids['request-submit'].disabled === false, 'POST network: retry should be enabled');
""",
    )

    pid = 104
    not_found = _p6_7_base_routes(pid, [])
    not_found["/api/owner/portal/session"] = [
        {"status": 200, "body": {"authenticated": True}},
        {"status": 200, "body": {"authenticated": True}},
    ]
    not_found[f"/api/owner/portal/properties/{pid}/feedback"] = [
        {"status": 200, "body": {"items": []}},
        {"status": 404, "body": {"detail": "PRIVATE"}},
    ]
    _run_node_scenario(
        not_found,
        """
ids['request-type'].value = 'general_message';
ids['request-subject'].value = 'Oggetto';
ids['request-message'].value = 'Messaggio';
await ids['request-form'].trigger('submit');
await flush();
assert(ids['request-form-status'].textContent === 'Contenuto non disponibile o accesso non più valido.', 'POST 404: neutral message mismatch');
assert(ids['app-view'].hidden === false, 'POST 404: valid session should remain logged in');
""",
    )


def test_p6_7_session_loss_during_post_resets_app_state():
    routes = _p6_7_base_routes(105, [])
    routes["/api/owner/portal/properties/105/feedback"] = [
        {"status": 200, "body": {"items": []}},
        {"status": 401, "body": {"detail": "expired"}},
    ]
    _run_node_scenario(
        routes,
        """
ids['request-type'].value = 'general_message';
ids['request-subject'].value = 'Oggetto';
ids['request-message'].value = 'Messaggio';
await ids['request-form'].trigger('submit');
await flush();
assert(ids['login-view'].hidden === false, 'POST session loss: login missing');
assert(ids['app-view'].hidden === true, 'POST session loss: app still visible');
assert(ids['requests-list'].children.length === 0, 'POST session loss: request state not cleared');
""",
    )


def test_p6_7_property_switch_resets_form_and_ignores_stale_get_response():
    routes = {
        "/api/owner/portal/session": [{"status": 200, "body": {"authenticated": True}}],
        "/api/owner/portal/dashboard": [{
            "status": 200,
            "body": {"property_count": 2, "properties": [{"id": 106, "title": "Casa Uno", "is_primary": True}, {"id": 107, "title": "Casa Due"}]},
        }],
        "/api/owner/portal/properties/106": [{"status": 200, "body": {"property": {"title": "Casa Uno"}}}],
        "/api/owner/portal/properties/106/timeline": [{"status": 200, "body": {"items": []}}],
        "/api/owner/portal/properties/106/documents": [{"status": 200, "body": {"items": []}}],
        "/api/owner/portal/properties/106/visit-feedback?limit=50&offset=0": [{"status": 200, "body": {"items": []}}],
        "/api/owner/portal/properties/106/feedback": [{"status": 200, "delay_ms": 60, "body": {"items": [_request_item(1, subject="VECCHIA RICHIESTA")]}}],
        "/api/owner/portal/properties/107": [{"status": 200, "body": {"property": {"title": "Casa Due"}}}],
        "/api/owner/portal/properties/107/timeline": [{"status": 200, "body": {"items": []}}],
        "/api/owner/portal/properties/107/documents": [{"status": 200, "body": {"items": []}}],
        "/api/owner/portal/properties/107/visit-feedback?limit=50&offset=0": [{"status": 200, "body": {"items": []}}],
        "/api/owner/portal/properties/107/feedback": [{"status": 200, "body": {"items": [_request_item(2, subject="NUOVA RICHIESTA")]}}],
    }
    _run_node_scenario(
        routes,
        """
await new Promise((resolve) => setTimeout(resolve, 10));
ids['request-type'].value = 'general_message';
ids['request-subject'].value = 'Bozza vecchia';
ids['request-message'].value = 'Da cancellare al cambio immobile';
await ids['property-list'].children[1].children[0].trigger('click');
await new Promise((resolve) => setTimeout(resolve, 80));
await flush();
const text = allText(ids['requests-list']);
assert(text.includes('NUOVA RICHIESTA'), 'request stale GET: new property history missing');
assert(!text.includes('VECCHIA RICHIESTA'), 'request stale GET: old property history leaked');
assert(ids['request-type'].value === '', 'request property switch: type form not reset');
assert(ids['request-subject'].value === '', 'request property switch: subject form not reset');
assert(ids['request-message'].value === '', 'request property switch: message form not reset');
""",
    )


def test_p6_7_post_response_after_property_switch_is_ignored_and_does_not_refresh_old_or_new_ui():
    routes = {
        "/api/owner/portal/session": [{"status": 200, "body": {"authenticated": True}}],
        "/api/owner/portal/dashboard": [{
            "status": 200,
            "body": {"property_count": 2, "properties": [{"id": 108, "title": "Casa Uno", "is_primary": True}, {"id": 109, "title": "Casa Due"}]},
        }],
        "/api/owner/portal/properties/108": [{"status": 200, "body": {"property": {"title": "Casa Uno"}}}],
        "/api/owner/portal/properties/108/timeline": [{"status": 200, "body": {"items": []}}],
        "/api/owner/portal/properties/108/documents": [{"status": 200, "body": {"items": []}}],
        "/api/owner/portal/properties/108/visit-feedback?limit=50&offset=0": [{"status": 200, "body": {"items": []}}],
        "/api/owner/portal/properties/108/feedback": [
            {"status": 200, "body": {"items": []}},
            {"status": 201, "delay_ms": 60, "body": _request_item(10, subject="POST VECCHIA")},
        ],
        "/api/owner/portal/properties/109": [{"status": 200, "body": {"property": {"title": "Casa Due"}}}],
        "/api/owner/portal/properties/109/timeline": [{"status": 200, "body": {"items": []}}],
        "/api/owner/portal/properties/109/documents": [{"status": 200, "body": {"items": []}}],
        "/api/owner/portal/properties/109/visit-feedback?limit=50&offset=0": [{"status": 200, "body": {"items": []}}],
        "/api/owner/portal/properties/109/feedback": [{"status": 200, "body": {"items": [_request_item(11, subject="STORICO NUOVO")]}}],
    }
    _run_node_scenario(
        routes,
        """
ids['request-type'].value = 'general_message';
ids['request-subject'].value = 'Invio vecchio';
ids['request-message'].value = 'Messaggio';
const submitPromise = ids['request-form'].trigger('submit');
await new Promise((resolve) => setTimeout(resolve, 10));
await ids['property-list'].children[1].children[0].trigger('click');
await submitPromise;
await new Promise((resolve) => setTimeout(resolve, 70));
await flush();
const text = allText(ids['requests-list']);
assert(text.includes('STORICO NUOVO'), 'request stale POST: new property history missing');
assert(!text.includes('POST VECCHIA'), 'request stale POST: old response leaked');
assert(ids['request-form-status'].textContent === '', 'request stale POST: old success/error contaminated new property form');
assert(calls.filter((call) => call.method === 'GET' && call.url.endsWith('/properties/108/feedback')).length === 1, 'request stale POST: old history was refreshed after property switch');
assert(calls.filter((call) => call.method === 'GET' && call.url.endsWith('/properties/109/feedback')).length === 1, 'request stale POST: new history should load exactly once');
""",
    )


def test_p6_7_get_422_429_5xx_and_network_errors_are_neutral():
    cases = [
        (110, {"status": 422, "body": {"secret": "RAW"}}, "Impossibile caricare le richieste con i dati disponibili."),
        (111, {"status": 429, "body": {"secret": "RAW"}}, "Troppe richieste. Riprova tra poco."),
        (112, {"status": 500, "body": {"secret": "RAW"}}, "Servizio temporaneamente non disponibile."),
        (113, {"network_error": True}, "Connessione non disponibile. Controlla la rete e riprova."),
    ]
    for pid, error_spec, expected in cases:
        routes = _p6_7_base_routes(pid, [])
        routes[f"/api/owner/portal/properties/{pid}/feedback"] = [error_spec]
        _run_node_scenario(
            routes,
            f"""
assert(ids['requests-error'].hidden === false, 'GET request error: error state missing');
assert(ids['requests-error-message'].textContent === {json.dumps(expected)}, 'GET request error: safe message mismatch');
assert(!ids['requests-error-message'].textContent.includes('RAW'), 'GET request error: raw payload leaked');
""",
        )


def test_p6_7_scope_only_feedback_api_no_p6_8_and_accessibility_memory_only():
    source = APP_JS.read_text(encoding="utf-8")
    html = INDEX.read_text(encoding="utf-8")
    assert "apiRequest(`/properties/${encodeURIComponent(String(propertyAtStart))}/feedback`)" in source
    assert "apiRequest(`/properties/${encodeURIComponent(String(propertyAtStart))}/feedback`, {" in source
    assert "method: 'POST'" in source[source.index("async function submitRequest"):source.index("async function selectProperty")]
    assert "requestGeneration" in source
    assert "requestSubmitInFlight" in source
    assert "/notifications" in source
    assert "/notification-preferences" in source
    assert "localStorage" not in source
    assert "sessionStorage" not in source
    assert "document.cookie" not in source
    assert 'aria-label="Storico richieste proprietario"' in html
    assert 'role="status"' in html
    assert 'aria-live="polite"' in html


# OWNER 0.2 P6.8 — notifiche e preferenze ---------------------------------

def _notification_item(
    notification_id: int,
    *,
    notification_type: str = "publication_published",
    title: str = "Nuovo aggiornamento",
    body: str = "È disponibile un nuovo aggiornamento.",
    read_at: str | None = None,
    created_at: str = "2026-08-13T12:00:00+00:00",
    target_type: str = "owner_publication",
    target_id: int = 501,
) -> dict:
    return {
        "id": notification_id,
        "type": notification_type,
        "title": title,
        "body": body,
        "created_at": created_at,
        "read_at": read_at,
        "target_type": target_type,
        "target_id": target_id,
        # deliberately private/unsupported fields; the UI must ignore them
        "owner_account_id": 999,
        "property_id": 777,
        "idempotency_key": "PRIVATE-IDEMPOTENCY",
        "metadata": {"FLOW": "PRIVATE FLOW"},
        "contact_id": 555,
        "lead_id": 444,
    }


def _notification_preferences(**overrides) -> dict:
    values = {
        "in_app_enabled": True,
        "publication_enabled": True,
        "visit_feedback_enabled": True,
        "document_enabled": True,
        "request_update_enabled": True,
    }
    values.update(overrides)
    return values


def _p6_8_base_routes(items=None, *, has_more=False, preferences=None) -> dict[str, list[dict]]:
    if items is None:
        items = []
    if preferences is None:
        preferences = _notification_preferences()
    return {
        "/api/owner/portal/session": [{"status": 200, "body": {"authenticated": True}}],
        "/api/owner/portal/dashboard": [{"status": 200, "body": {"property_count": 0, "properties": []}}],
        "/api/owner/portal/notifications?limit=50&offset=0&unread_only=false": [{
            "status": 200,
            "body": {"items": items, "limit": 50, "offset": 0, "has_more": has_more},
        }],
        "/api/owner/portal/notification-preferences": [{"status": 200, "body": preferences}],
    }


def test_p6_8_preflight_backend_contract_matches_frozen_api_and_dtos():
    router_source = (ROOT / "owner" / "router_portal.py").read_text(encoding="utf-8")
    repository_source = (ROOT / "owner" / "repository.py").read_text(encoding="utf-8")

    assert '@router.get("/notifications")' in router_source
    assert 'limit: int = Query(50, ge=1, le=100)' in router_source
    assert 'offset: int = Query(0, ge=0)' in router_source
    assert 'unread_only: bool = False' in router_source
    assert '"has_more": len(rows) > limit' in router_source
    assert '@router.post("/notifications/{i}/read")' in router_source
    assert '@router.get("/notification-preferences")' in router_source
    assert '@router.put("/notification-preferences")' in router_source

    assert set(OwnerNotificationDTO.model_fields) == {
        "id", "type", "title", "body", "created_at", "read_at", "target_type", "target_id"
    }
    assert set(NotificationPreferencesUpdate.model_fields) == {
        "in_app_enabled",
        "publication_enabled",
        "visit_feedback_enabled",
        "document_enabled",
        "request_update_enabled",
    }
    for notification_type in (
        "publication_published",
        "visit_feedback_published",
        "shared_document_published",
        "request_handled",
    ):
        assert notification_type in repository_source


def test_p6_8_markup_contains_real_notifications_preferences_filter_and_states():
    parser = _html_parser()
    required = {
        "notifications-section",
        "notifications-title",
        "notifications-unread-only",
        "notifications-loading",
        "notifications-empty",
        "notifications-error",
        "notifications-error-message",
        "notifications-retry",
        "notifications-content",
        "notifications-list",
        "notifications-pagination",
        "notifications-load-more",
        "notification-preferences-section",
        "notification-preferences-form",
        "notification-preferences-loading",
        "notification-preferences-error",
        "notification-preferences-retry",
        "preference-in-app",
        "preference-publication",
        "preference-visit-feedback",
        "preference-document",
        "preference-request-update",
        "notification-preferences-save",
        "notification-preferences-status",
    }
    assert required <= parser.ids
    assert "notifications-unread-only" in parser.labels_for
    for field_id in (
        "preference-in-app",
        "preference-publication",
        "preference-visit-feedback",
        "preference-document",
        "preference-request-update",
    ):
        assert field_id in parser.labels_for
    html = INDEX.read_text(encoding="utf-8")
    assert "Solo non lette" in html
    assert "Preferenze notifiche" in html
    css = APP_CSS.read_text(encoding="utf-8")
    for selector in (
        ".notifications-section",
        ".notifications-list",
        ".notification-card",
        ".notification-filter",
        ".notification-preferences-section",
        ".notification-preferences-form",
        ".preference-toggle",
    ):
        assert selector in css


def test_p6_8_source_contains_real_functions_exact_endpoints_and_no_future_scope():
    source = APP_JS.read_text(encoding="utf-8")
    assert "async function loadNotifications" in source
    assert "return `/notifications?${params.toString()}`" in source
    assert "`/notifications/${encodeURIComponent(String(id))}/read`" in source
    assert "async function loadNotificationPreferences" in source
    assert "apiRequest('/notification-preferences')" in source
    assert "apiRequest('/notification-preferences', {" in source
    assert "method: 'PUT'" in source[source.index("async function saveNotificationPreferences"):source.index("function startP68DataLoads")]
    assert "notificationGeneration" in source
    assert "notificationPreferencesGeneration" in source
    assert "notificationReadInFlight" in source
    assert "notificationPreferencesSaving" in source
    for forbidden in ("/owner/admin", "/api/owner/admin", "/flow/", "/buy/", "/match/"):
        assert forbidden not in source.lower()


def test_p6_8_notifications_load_after_auth_zero_one_multiple_and_preserve_backend_order():
    _run_node_scenario(
        _p6_8_base_routes([]),
        """
assert(ids['notifications-empty'].hidden === false, 'notifications zero: empty state missing');
assert(ids['notifications-content'].hidden === true, 'notifications zero: content should be hidden');
""",
    )

    _run_node_scenario(
        _p6_8_base_routes([_notification_item(1, title="Una notifica")]),
        """
assert(ids['notifications-content'].hidden === false, 'notifications one: content missing');
assert(ids['notifications-list'].children.length === 1, 'notifications one: item missing');
assert(allText(ids['notifications-list']).includes('Una notifica'), 'notifications one: title missing');
""",
    )

    items = [
        _notification_item(2, title="Seconda backend", notification_type="shared_document_published"),
        _notification_item(1, title="Prima backend", notification_type="visit_feedback_published"),
    ]
    _run_node_scenario(
        _p6_8_base_routes(items),
        """
const text0 = allText(ids['notifications-list'].children[0]);
const text1 = allText(ids['notifications-list'].children[1]);
assert(text0.includes('Seconda backend'), 'notifications order: first backend item changed');
assert(text1.includes('Prima backend'), 'notifications order: second backend item changed');
assert(text0.includes('Nuovo documento'), 'notification type label: document mapping missing');
assert(text1.includes('Nuovo feedback visita'), 'notification type label: visit mapping missing');
const notificationCall = calls.find((call) => call.url.includes('/notifications?'));
assert(notificationCall, 'notifications must load after authenticated bootstrap');
""",
    )


def test_p6_8_notification_whitelist_privacy_and_xss_are_text_only():
    item = _notification_item(
        3,
        title="<script>alert(1)</script>",
        body="<img src=x onerror=alert(2)>",
        target_id=123456,
    )
    _run_node_scenario(
        _p6_8_base_routes([item]),
        """
const text = allText(ids['notifications-list']);
assert(text.includes('<script>alert(1)</script>'), 'notification xss: title must remain text');
assert(text.includes('<img src=x onerror=alert(2)>'), 'notification xss: body must remain text');
for (const forbidden of ['PRIVATE-IDEMPOTENCY','PRIVATE FLOW','999','777','555','444','123456','owner_publication']) {
  assert(!text.includes(forbidden), `notification privacy: leaked ${forbidden}`);
}
""",
    )
    source = APP_JS.read_text(encoding="utf-8")
    render_start = source.index("function renderNotificationCard")
    render_end = source.index("function renderNotifications", render_start)
    block = source[render_start:render_end]
    for private_name in (
        "owner_account_id", "property_id", "idempotency_key", "contact_id", "lead_id", "metadata", "target_id"
    ):
        assert private_name not in block


def test_p6_8_read_is_explicit_idempotent_ui_and_already_read_never_posts():
    unread = _notification_item(10, title="Da leggere")
    routes = _p6_8_base_routes([unread])
    routes["/api/owner/portal/notifications/10/read"] = [{
        "status": 200,
        "delay_ms": 20,
        "body": _notification_item(10, title="Da leggere", read_at="2026-08-13T13:00:00+00:00"),
    }]
    _run_node_scenario(
        routes,
        """
assert(calls.filter((call) => call.url.endsWith('/notifications/10/read')).length === 0, 'render must not mark notification read');
const card = ids['notifications-list'].children[0];
const button = card.children[4].children[0];
assert(button.textContent === 'Segna come letta', 'unread action label missing');
const first = button.trigger('click');
await new Promise((resolve) => setTimeout(resolve, 5));
const second = button.trigger('click');
await Promise.all([first, second]);
await flush();
assert(calls.filter((call) => call.method === 'POST' && call.url.endsWith('/notifications/10/read')).length === 1, 'read double click not guarded');
assert(allText(ids['notifications-list']).includes('Letta'), 'read success did not update only notification state');
""",
    )

    already = _notification_item(11, title="Già letta", read_at="2026-08-13T13:00:00+00:00")
    _run_node_scenario(
        _p6_8_base_routes([already]),
        """
const button = ids['notifications-list'].children[0].children[4].children[0];
assert(button.disabled === true, 'already read: button must be disabled');
assert(button.textContent === 'Letta', 'already read: state label missing');
await button.trigger('click');
assert(calls.filter((call) => call.url.endsWith('/notifications/11/read')).length === 0, 'already read: must not POST');
""",
    )


def test_p6_8_pagination_uses_backend_has_more_offset_and_double_request_guard():
    first = [_notification_item(i, title=f"Prima pagina {i}") for i in (1, 2)]
    second = [_notification_item(3, title="Seconda pagina")]
    routes = _p6_8_base_routes(first, has_more=True)
    routes["/api/owner/portal/notifications?limit=50&offset=50&unread_only=false"] = [{
        "status": 200,
        "delay_ms": 20,
        "body": {"items": second, "limit": 50, "offset": 50, "has_more": False},
    }]
    _run_node_scenario(
        routes,
        """
assert(ids['notifications-pagination'].hidden === false, 'pagination: has_more true not honored');
const firstClick = ids['notifications-load-more'].trigger('click');
await new Promise((resolve) => setTimeout(resolve, 5));
const secondClick = ids['notifications-load-more'].trigger('click');
await Promise.all([firstClick, secondClick]);
await flush();
assert(calls.filter((call) => call.url.includes('offset=50&unread_only=false')).length === 1, 'pagination: duplicate load-more request');
assert(ids['notifications-list'].children.length === 3, 'pagination: existing items were lost');
assert(allText(ids['notifications-list'].children[2]).includes('Seconda pagina'), 'pagination: appended item missing');
assert(ids['notifications-pagination'].hidden === true, 'pagination: backend has_more false not honored');
""",
    )


def test_p6_8_unread_filter_resets_offset_uses_real_parameter_and_ignores_stale_response():
    routes = _p6_8_base_routes([], has_more=False)
    routes["/api/owner/portal/notifications?limit=50&offset=0&unread_only=false"] = [{
        "status": 200,
        "delay_ms": 60,
        "body": {"items": [_notification_item(1, title="VECCHIA")], "limit": 50, "offset": 0, "has_more": True},
    }]
    routes["/api/owner/portal/notifications?limit=50&offset=0&unread_only=true"] = [{
        "status": 200,
        "body": {"items": [_notification_item(2, title="NUOVA NON LETTA")], "limit": 50, "offset": 0, "has_more": False},
    }]
    _run_node_scenario(
        routes,
        """
await new Promise((resolve) => setTimeout(resolve, 10));
ids['notifications-unread-only'].checked = true;
await ids['notifications-unread-only'].trigger('change');
await new Promise((resolve) => setTimeout(resolve, 80));
await flush();
const text = allText(ids['notifications-list']);
assert(text.includes('NUOVA NON LETTA'), 'filter stale: new filtered result missing');
assert(!text.includes('VECCHIA'), 'filter stale: old response leaked');
const filtered = calls.filter((call) => call.url.includes('/notifications?') && call.url.includes('unread_only=true'));
assert(filtered.length === 1, 'filter: true request missing/duplicated');
assert(filtered[0].url.includes('offset=0'), 'filter: offset was not reset');
""",
    )


def test_p6_8_read_404_neutral_with_valid_session_and_session_loss_are_safe():
    routes = _p6_8_base_routes([_notification_item(20)])
    routes["/api/owner/portal/session"] = [
        {"status": 200, "body": {"authenticated": True}},
        {"status": 200, "body": {"authenticated": True}},
    ]
    routes["/api/owner/portal/notifications/20/read"] = [{"status": 404, "body": {"detail": "PRIVATE"}}]
    _run_node_scenario(
        routes,
        """
const button = ids['notifications-list'].children[0].children[4].children[0];
await button.trigger('click');
await flush();
assert(ids['app-view'].hidden === false, 'read 404: valid session should remain logged in');
assert(allText(ids['notifications-list']).includes('Contenuto non disponibile o accesso non più valido.'), 'read 404: neutral message missing');
assert(!allText(ids['notifications-list']).includes('PRIVATE'), 'read 404: raw backend leaked');
""",
    )

    expired = _p6_8_base_routes([_notification_item(21)])
    expired["/api/owner/portal/notifications/21/read"] = [{"status": 401, "body": {"detail": "expired"}}]
    _run_node_scenario(
        expired,
        """
await ids['notifications-list'].children[0].children[4].children[0].trigger('click');
await flush();
assert(ids['login-view'].hidden === false, 'read auth loss: login missing');
assert(ids['app-view'].hidden === true, 'read auth loss: app still visible');
assert(ids['notifications-list'].children.length === 0, 'read auth loss: notifications state not reset');
""",
    )


def test_p6_8_notification_list_422_429_5xx_network_and_404_are_recoverable_neutral():
    cases = [
        ({"status": 422, "body": {"secret": "RAW"}}, "Impossibile caricare le notifiche con i dati disponibili."),
        ({"status": 429, "body": {"secret": "RAW"}}, "Troppe richieste. Riprova tra poco."),
        ({"status": 500, "body": {"secret": "RAW"}}, "Servizio temporaneamente non disponibile."),
        ({"network_error": True}, "Connessione non disponibile. Controlla la rete e riprova."),
    ]
    for spec, expected in cases:
        routes = _p6_8_base_routes([])
        routes["/api/owner/portal/notifications?limit=50&offset=0&unread_only=false"] = [spec]
        _run_node_scenario(
            routes,
            f"""
assert(ids['notifications-error'].hidden === false, 'notification list error: state missing');
assert(ids['notifications-error-message'].textContent === {json.dumps(expected)}, 'notification list error: wrong neutral message');
assert(!ids['notifications-error-message'].textContent.includes('RAW'), 'notification list error: raw payload leaked');
""",
        )

    routes = _p6_8_base_routes([])
    routes["/api/owner/portal/session"] = [
        {"status": 200, "body": {"authenticated": True}},
        {"status": 200, "body": {"authenticated": True}},
    ]
    routes["/api/owner/portal/notifications?limit=50&offset=0&unread_only=false"] = [
        {"status": 404, "body": {"detail": "PRIVATE"}}
    ]
    _run_node_scenario(
        routes,
        """
assert(ids['notifications-error'].hidden === false, 'notifications 404: error state missing');
assert(ids['notifications-error-message'].textContent === 'Contenuto non disponibile o accesso non più valido.', 'notifications 404: neutral message mismatch');
assert(ids['app-view'].hidden === false, 'notifications 404: valid session should remain logged in');
""",
    )


def test_p6_8_preferences_get_renders_exact_five_booleans_and_labels():
    prefs = _notification_preferences(
        in_app_enabled=True,
        publication_enabled=False,
        visit_feedback_enabled=True,
        document_enabled=False,
        request_update_enabled=True,
    )
    _run_node_scenario(
        _p6_8_base_routes([], preferences=prefs),
        """
assert(ids['notification-preferences-form'].hidden === false, 'preferences GET: form missing');
assert(ids['preference-in-app'].checked === true, 'preferences GET: in_app mismatch');
assert(ids['preference-publication'].checked === false, 'preferences GET: publication mismatch');
assert(ids['preference-visit-feedback'].checked === true, 'preferences GET: visit mismatch');
assert(ids['preference-document'].checked === false, 'preferences GET: document mismatch');
assert(ids['preference-request-update'].checked === true, 'preferences GET: request mismatch');
const getCall = calls.find((call) => call.method === 'GET' && call.url.endsWith('/notification-preferences'));
assert(getCall, 'preferences GET: initial endpoint not called');
""",
    )


def test_p6_8_preferences_put_exact_five_booleans_double_submit_guard_and_success():
    routes = _p6_8_base_routes([])
    routes["/api/owner/portal/notification-preferences"] = [
        {"status": 200, "body": _notification_preferences()},
        {"status": 200, "delay_ms": 20, "body": _notification_preferences(
            in_app_enabled=True,
            publication_enabled=False,
            visit_feedback_enabled=False,
            document_enabled=True,
            request_update_enabled=False,
        )},
    ]
    _run_node_scenario(
        routes,
        """
ids['preference-in-app'].checked = true;
ids['preference-publication'].checked = false;
ids['preference-visit-feedback'].checked = false;
ids['preference-document'].checked = true;
ids['preference-request-update'].checked = false;
const first = ids['notification-preferences-form'].trigger('submit');
await new Promise((resolve) => setTimeout(resolve, 5));
assert(ids['notification-preferences-save'].disabled === true, 'preferences PUT: saving button not disabled');
assert(ids['notification-preferences-save'].textContent === 'Salvataggio…', 'preferences PUT: saving label missing');
const second = ids['notification-preferences-form'].trigger('submit');
await Promise.all([first, second]);
await flush();
const puts = calls.filter((call) => call.method === 'PUT' && call.url.endsWith('/notification-preferences'));
assert(puts.length === 1, 'preferences PUT: double submit not blocked');
const payload = JSON.parse(puts[0].body);
assert(Object.keys(payload).sort().join(',') === 'document_enabled,in_app_enabled,publication_enabled,request_update_enabled,visit_feedback_enabled', 'preferences PUT: extra/missing fields');
for (const value of Object.values(payload)) assert(typeof value === 'boolean', 'preferences PUT: non-boolean value');
assert(payload.in_app_enabled === true && payload.publication_enabled === false && payload.visit_feedback_enabled === false && payload.document_enabled === true && payload.request_update_enabled === false, 'preferences PUT: boolean values mismatch');
assert(ids['notification-preferences-status'].textContent === 'Preferenze salvate.', 'preferences PUT: success state missing');
assert(ids['notification-preferences-save'].disabled === false, 'preferences PUT: button not restored');
""",
    )


def test_p6_8_preferences_put_error_is_recoverable_and_session_loss_logs_out():
    routes = _p6_8_base_routes([])
    routes["/api/owner/portal/notification-preferences"] = [
        {"status": 200, "body": _notification_preferences()},
        {"status": 429, "body": {"secret": "RAW"}},
    ]
    _run_node_scenario(
        routes,
        """
await ids['notification-preferences-form'].trigger('submit');
await flush();
assert(ids['notification-preferences-status'].textContent === 'Troppe richieste. Riprova tra poco.', 'preferences PUT error: neutral message mismatch');
assert(!ids['notification-preferences-status'].textContent.includes('RAW'), 'preferences PUT error: raw payload leaked');
assert(ids['notification-preferences-save'].disabled === false, 'preferences PUT error: retry not enabled');
assert(ids['notification-preferences-form'].hidden === false, 'preferences PUT error: form not recoverable');
""",
    )

    expired = _p6_8_base_routes([])
    expired["/api/owner/portal/notification-preferences"] = [
        {"status": 200, "body": _notification_preferences()},
        {"status": 401, "body": {"detail": "expired"}},
    ]
    _run_node_scenario(
        expired,
        """
await ids['notification-preferences-form'].trigger('submit');
await flush();
assert(ids['login-view'].hidden === false, 'preferences PUT auth loss: login missing');
assert(ids['app-view'].hidden === true, 'preferences PUT auth loss: app still visible');
""",
    )


def test_p6_8_preferences_stale_get_is_ignored_after_newer_retry():
    routes = _p6_8_base_routes([])
    routes["/api/owner/portal/notification-preferences"] = [
        {"status": 200, "delay_ms": 60, "body": _notification_preferences(publication_enabled=False)},
        {"status": 200, "body": _notification_preferences(publication_enabled=True)},
    ]
    _run_node_scenario(
        routes,
        """
await new Promise((resolve) => setTimeout(resolve, 10));
await ids['notification-preferences-retry'].trigger('click');
await new Promise((resolve) => setTimeout(resolve, 80));
await flush();
assert(ids['preference-publication'].checked === true, 'preferences stale: older GET overwrote newer response');
""",
    )


def test_p6_8_logout_invalidates_pending_notification_response():
    routes = _p6_8_base_routes([])
    routes["/api/owner/portal/notifications?limit=50&offset=0&unread_only=false"] = [{
        "status": 200,
        "delay_ms": 60,
        "body": {"items": [_notification_item(40, title="NON DEVE COMPARIRE")], "limit": 50, "offset": 0, "has_more": False},
    }]
    routes["/api/owner/portal/auth/logout"] = [{"status": 204, "body": None}]
    _run_node_scenario(
        routes,
        """
await new Promise((resolve) => setTimeout(resolve, 10));
await ids['logout-button'].trigger('click');
await new Promise((resolve) => setTimeout(resolve, 80));
await flush();
assert(ids['login-view'].hidden === false, 'logout stale: login missing');
assert(ids['notifications-list'].children.length === 0, 'logout stale: pending notifications repopulated UI');
assert(!allText(ids['notifications-list']).includes('NON DEVE COMPARIRE'), 'logout stale: old notification leaked');
""",
    )


def test_p6_8_security_no_generic_json_browser_storage_or_forbidden_dom_and_no_private_fields():
    source = APP_JS.read_text(encoding="utf-8")
    for forbidden in (
        ".innerHTML", ".outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function",
        "localStorage", "sessionStorage", "document.cookie",
    ):
        assert forbidden not in source
    assert "JSON.stringify(preferencesBody)" in source
    assert "JSON.stringify(payload" not in source
    render_start = source.index("function renderNotificationCard")
    render_end = source.index("function renderNotifications", render_start)
    render_block = source[render_start:render_end]
    for private_name in (
        "owner_account_id", "property_id", "idempotency_key", "contact_id", "lead_id", "activity_id",
        "BUY", "MATCH", "FLOW", "target_id",
    ):
        assert private_name not in render_block
    pagination_start = source.index("async function loadNotifications")
    pagination_end = source.index("function notificationCardById", pagination_start)
    assert ".sort(" not in source[pagination_start:pagination_end]


def test_p6_8_anti_regression_frontend_and_tests_all_contain_real_p68_features():
    html = INDEX.read_text(encoding="utf-8")
    js = APP_JS.read_text(encoding="utf-8")
    css = APP_CSS.read_text(encoding="utf-8")
    tests = Path(__file__).read_text(encoding="utf-8")

    checks = {
        "Notifiche": (
            "notifications-section" in html,
            "loadNotifications" in js and "/notifications" in js,
            ".notifications-section" in css and ".notification-card" in css,
            "test_p6_8_notifications_load_after_auth_zero_one_multiple_and_preserve_backend_order" in tests,
        ),
        "Filtro non lette": (
            "notifications-unread-only" in html,
            "notificationUnreadOnly" in js and "unread_only" in js,
            ".notification-filter" in css,
            "test_p6_8_unread_filter_resets_offset_uses_real_parameter_and_ignores_stale_response" in tests,
        ),
        "Segna come letta": (
            "notifications-list" in html,
            "markNotificationRead" in js and "/read`" in js,
            ".notification-read-button" in css or ".notification-actions" in css,
            "test_p6_8_read_is_explicit_idempotent_ui_and_already_read_never_posts" in tests,
        ),
        "Paginazione": (
            "notifications-load-more" in html,
            "notificationHasMore" in js and "has_more" in js,
            ".notifications-pagination" in css,
            "test_p6_8_pagination_uses_backend_has_more_offset_and_double_request_guard" in tests,
        ),
        "Preferenze": (
            "notification-preferences-form" in html,
            "loadNotificationPreferences" in js and "saveNotificationPreferences" in js,
            ".notification-preferences-form" in css and ".preference-toggle" in css,
            "test_p6_8_preferences_put_exact_five_booleans_double_submit_guard_and_success" in tests,
        ),
    }
    assert all(all(parts) for parts in checks.values()), checks


def test_p6_8_preferences_404_uses_session_probe_and_neutral_error():
    routes = _p6_8_base_routes([])
    routes["/api/owner/portal/session"] = [
        {"status": 200, "body": {"authenticated": True}},
        {"status": 200, "body": {"authenticated": True}},
    ]
    routes["/api/owner/portal/notification-preferences"] = [
        {"status": 404, "body": {"detail": "PRIVATE"}},
    ]
    _run_node_scenario(
        routes,
        """
assert(ids['notification-preferences-error'].hidden === false, 'preferences 404: error state missing');
assert(ids['notification-preferences-error-message'].textContent === 'Contenuto non disponibile o accesso non più valido.', 'preferences 404: neutral message mismatch');
assert(!ids['notification-preferences-error-message'].textContent.includes('PRIVATE'), 'preferences 404: raw payload leaked');
assert(ids['app-view'].hidden === false, 'preferences 404: valid session should remain logged in');
""",
    )
