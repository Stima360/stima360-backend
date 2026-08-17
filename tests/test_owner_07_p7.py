from __future__ import annotations

import json
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from typing import get_args

from owner.schemas import AccessCreate, AccountCreate, FeedbackCreate, FeedbackStatus, PublicationCreate, PublicationUpdate


ROOT = Path(__file__).resolve().parents[1]
ADMIN = ROOT / "static" / "owner_admin"
INDEX = ADMIN / "index.html"
APP_JS = ADMIN / "assets" / "app.js"
APP_CSS = ADMIN / "assets" / "app.css"
REPOSITORY = ROOT / "owner" / "repository.py"


class _AdminHtmlParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids: set[str] = set()
        self.labels_for: set[str] = set()
        self.inputs: dict[str, dict[str, str | None]] = {}
        self.select_options: dict[str, list[str]] = {}
        self._select_id: str | None = None
        self.meta: list[dict[str, str | None]] = []
        self.scripts: list[str | None] = []
        self.links: list[str | None] = []
        self.aria_live: set[str] = set()

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        element_id = values.get("id")
        if element_id:
            self.ids.add(element_id)
        if tag == "label" and values.get("for"):
            self.labels_for.add(values["for"])
        if tag == "input" and element_id:
            self.inputs[element_id] = values
        if tag == "select":
            self._select_id = element_id
            if element_id:
                self.select_options[element_id] = []
        if tag == "option" and self._select_id and values.get("value") is not None:
            self.select_options[self._select_id].append(values["value"])
        if tag == "meta":
            self.meta.append(values)
        if tag == "script":
            self.scripts.append(values.get("src"))
        if tag == "link":
            self.links.append(values.get("href"))
        if values.get("aria-live"):
            self.aria_live.add(values["aria-live"])

    def handle_endtag(self, tag):
        if tag == "select":
            self._select_id = None


def _html() -> _AdminHtmlParser:
    parser = _AdminHtmlParser()
    parser.feed(INDEX.read_text(encoding="utf-8"))
    return parser


def _run_node(routes: dict[str, list[dict]], assertions: str) -> str:
    route_json = json.dumps(routes, ensure_ascii=False)
    app_path = json.dumps(str(APP_JS))
    script = f"""
const fs = require('fs');
const vm = require('vm');

function assert(value, message) {{ if (!value) throw new Error(message || 'assertion failed'); }}
function sleepTick() {{ return new Promise(resolve => setImmediate(resolve)); }}

class FakeClassList {{
  constructor() {{ this.values = new Set(); }}
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
  constructor(tag='div', id='') {{
    this.tagName = tag.toUpperCase();
    this.id = id;
    this.hidden = false;
    this.disabled = false;
    this.value = '';
    this.checked = false;
    this.type = '';
    this.className = '';
    this.children = [];
    this.attributes = {{}};
    this.listeners = {{}};
    this.classList = new FakeClassList();
    this._textContent = '';
  }}
  set textContent(value) {{ this._textContent = String(value ?? ''); }}
  get textContent() {{ return this._textContent; }}
  append(...nodes) {{ this.children.push(...nodes); }}
  replaceChildren(...nodes) {{ this.children = [...nodes]; }}
  setAttribute(name, value) {{ this.attributes[name] = String(value); }}
  removeAttribute(name) {{ delete this.attributes[name]; }}
  getAttribute(name) {{ return this.attributes[name]; }}
  addEventListener(type, listener) {{
    if (!this.listeners[type]) this.listeners[type] = [];
    this.listeners[type].push(listener);
  }}
  focus() {{ document.activeElement = this; }}
  reset() {{
    if (this.id === 'account-create-form') {{
      ids['account-contact-id'].value = '';
      ids['account-language'].value = '';
    }}
    if (this.id === 'access-create-form') {{
      ids['access-owner-account-id'].value = '';
      ids['access-property-id'].value = '';
      ids['access-role'].value = '';
      ids['access-primary'].checked = false;
      ids['access-valid-until'].value = '';
    }}
  }}
  async trigger(type) {{
    const event = {{ preventDefault() {{}} }};
    for (const listener of this.listeners[type] || []) await listener(event);
  }}
}}

const requiredIds = [
  'login-view','admin-app','admin-login-form','admin-username','admin-password','admin-login-submit','admin-login-status',
  'admin-logout','admin-global-status','section-title','nav-dashboard','nav-accounts','nav-access','nav-publications','nav-requests',
  'section-dashboard','section-accounts','section-access','section-publications','section-requests','dashboard-loading','dashboard-error','dashboard-error-message',
  'dashboard-retry','dashboard-reload','dashboard-content','account-create-form','account-contact-id','account-language',
  'account-create-submit','account-form-status','accounts-loading','accounts-empty','accounts-error','accounts-error-message',
  'accounts-retry','accounts-reload','accounts-content','access-create-form','access-owner-account-id','access-property-id',
  'access-role','access-primary','access-valid-until','access-create-submit','access-form-status','access-loading','access-empty',
  'access-error','access-error-message','access-retry','access-reload','access-content',
  'publication-create-form','publication-property-id','publication-type','publication-title','publication-summary','publication-body',
  'publication-ack-required','publication-create-submit','publication-form-status','publications-loading','publications-empty',
  'publications-error','publications-error-message','publications-retry','publications-reload','publications-content',
  'requests-loading','requests-empty','requests-error','requests-error-message','requests-retry','requests-reload','requests-content'
];
const ids = {{}};
for (const id of requiredIds) ids[id] = new FakeElement('div', id);
for (const id of ['admin-login-form','account-create-form','access-create-form','publication-create-form']) ids[id].tagName = 'FORM';
for (const id of ['admin-username','admin-password','account-contact-id','account-language','access-owner-account-id','access-property-id','access-primary','access-valid-until','publication-property-id','publication-title','publication-ack-required']) ids[id].tagName = 'INPUT';
for (const id of ['publication-summary','publication-body']) ids[id].tagName = 'TEXTAREA';
ids['access-role'].tagName = 'SELECT';
ids['publication-type'].tagName = 'SELECT';
ids['account-language'].value = 'it';
ids['access-role'].value = 'owner';
ids['publication-type'].value = 'general_update';
ids['admin-app'].hidden = true;
ids['section-accounts'].hidden = true;
ids['section-access'].hidden = true;
ids['section-publications'].hidden = true;
ids['section-requests'].hidden = true;
ids['dashboard-error'].hidden = true;
ids['dashboard-content'].hidden = true;
ids['accounts-loading'].hidden = true;
ids['accounts-empty'].hidden = true;
ids['accounts-error'].hidden = true;
ids['accounts-content'].hidden = true;
ids['access-loading'].hidden = true;
ids['access-empty'].hidden = true;
ids['access-error'].hidden = true;
ids['access-content'].hidden = true;
ids['publications-loading'].hidden = true;
ids['publications-empty'].hidden = true;
ids['publications-error'].hidden = true;
ids['publications-content'].hidden = true;
ids['requests-loading'].hidden = true;
ids['requests-empty'].hidden = true;
ids['requests-error'].hidden = true;
ids['requests-content'].hidden = true;

const document = {{
  activeElement: null,
  getElementById(id) {{ return ids[id]; }},
  createElement(tag) {{ return new FakeElement(tag); }},
}};
global.document = document;
global.window = global;
global.btoa = text => Buffer.from(text, 'binary').toString('base64');

const routeQueues = {route_json};
const calls = [];
const deferred = {{}};
function responseFrom(spec) {{
  const status = spec.status ?? 200;
  return {{
    status,
    ok: status >= 200 && status < 300,
    async json() {{ return spec.body ?? {{}}; }},
  }};
}}
global.fetch = async function(url, options={{}}) {{
  const method = options.method || 'GET';
  calls.push({{ url, method, headers: options.headers || {{}}, body: options.body }});
  const key = `${{method}} ${{url}}`;
  const queue = routeQueues[key] || [];
  if (!queue.length) throw new Error(`No fake response for ${{key}}`);
  const spec = queue.shift();
  if (spec.defer) {{
    return await new Promise(resolve => {{ deferred[spec.defer] = () => resolve(responseFrom(spec)); }});
  }}
  if (spec.network_error) throw new Error('offline');
  return responseFrom(spec);
}};
function countCalls(method, url) {{ return calls.filter(c => c.method === method && c.url === url).length; }}
function flatten(node) {{ return [node.textContent, ...node.children.flatMap(flatten)].join(' '); }}
function findButton(node, text) {{
  if (node.tagName === 'BUTTON' && node.textContent === text) return node;
  for (const child of node.children) {{ const found = findButton(child, text); if (found) return found; }}
  return null;
}}
function findByAttribute(node, name, value) {{
  if (node.attributes && node.attributes[name] === String(value)) return node;
  for (const child of node.children) {{ const found = findByAttribute(child, name, value); if (found) return found; }}
  return null;
}}

vm.runInThisContext(fs.readFileSync({app_path}, 'utf8'), {{ filename: {app_path} }});

(async () => {{
  {assertions}
}})().then(() => console.log('SCENARIO_PASS')).catch(error => {{ console.error(error.stack || error); process.exit(1); }});
"""
    proc = subprocess.run(["node", "-e", script], text=True, capture_output=True, timeout=30)
    if proc.returncode != 0:
        raise AssertionError(f"Node scenario failed\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return proc.stdout


def test_p71_files_markup_navigation_and_accessibility():
    parser = _html()
    assert INDEX.exists() and APP_JS.exists() and APP_CSS.exists()
    required = {
        "login-view", "admin-login-form", "admin-username", "admin-password", "admin-login-submit",
        "admin-logout", "nav-dashboard", "nav-accounts", "nav-access",
        "section-dashboard", "dashboard-loading", "dashboard-error", "dashboard-content",
        "section-accounts", "account-create-form", "account-contact-id", "account-language",
        "accounts-loading", "accounts-empty", "accounts-error", "accounts-content",
        "section-access", "access-create-form", "access-owner-account-id", "access-property-id",
        "access-role", "access-primary", "access-valid-until", "access-loading", "access-empty",
        "access-error", "access-content",
    }
    assert required <= parser.ids
    assert {
        "admin-username", "admin-password", "account-contact-id", "account-language",
        "access-owner-account-id", "access-property-id", "access-role", "access-primary", "access-valid-until",
    } <= parser.labels_for
    assert parser.inputs["admin-password"].get("type") == "password"
    assert "/owner-admin/assets/app.js" in parser.scripts
    assert "/owner-admin/assets/app.css" in parser.links
    assert any(meta.get("name") == "viewport" for meta in parser.meta)
    assert "polite" in parser.aria_live


def test_real_backend_contract_dashboard_accounts_and_access_is_frozen():
    assert set(AccountCreate.model_fields) == {"contact_id", "preferred_language"}
    assert AccountCreate.model_fields["preferred_language"].default == "it"
    assert set(AccessCreate.model_fields) == {
        "owner_account_id", "property_id", "access_role", "is_primary", "valid_until"
    }
    assert set(get_args(AccessCreate.model_fields["access_role"].annotation)) == {
        "owner", "co_owner", "delegate", "legal_representative"
    }
    repo_source = REPOSITORY.read_text(encoding="utf-8")
    for field in ("active_accounts", "active_access", "published", "new_feedback"):
        assert field in repo_source
    assert "SELECT oa.*,c.display_name,c.email" in repo_source
    assert "SELECT * FROM owner_property_access ORDER BY created_at DESC" in repo_source


def test_access_role_options_match_real_backend_enum_exactly():
    parser = _html()
    assert parser.select_options["access-role"] == [
        "owner", "co_owner", "delegate", "legal_representative"
    ]


def test_security_has_no_legacy_generic_renderer_or_browser_persistence():
    source = APP_JS.read_text(encoding="utf-8")
    html = INDEX.read_text(encoding="utf-8")
    combined = source + html
    for forbidden in (
        "innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function",
        "Object.keys", "Object.entries", "localStorage", "sessionStorage", "document.cookie", "indexedDB",
        "/api/admin/check",
    ):
        assert forbidden not in combined
    assert "response.text" not in source
    assert "createElement" in source and "textContent" in source and "setAttribute" in source


def test_p72_scope_does_not_call_future_or_cross_module_apis():
    source = APP_JS.read_text(encoding="utf-8")
    for forbidden in (
        "/documents", "/visit-feedback", "/audit", "/tokens",
        "/api/core", "/api/property", "/api/buy", "/api/match", "/api/flow",
        "/notifications", "/notification-preferences",
    ):
        assert forbidden not in source
    for expected in (
        "'/dashboard'", "'/accounts'", "'/access'", "'/publications'", "'/feedback'",
        "/accounts/${", "/access/${", "/publications/${", "/feedback/${",
    ):
        assert expected in source


def test_login_uses_basic_in_memory_clears_password_and_renders_whitelisted_dashboard():
    out = _run_node(
        {
            "GET /api/owner/admin/dashboard": [{
                "status": 200,
                "body": {
                    "active_accounts": 2, "active_access": 3, "published": 4, "new_feedback": 5,
                    "internal_secret": "NEVER_RENDER",
                },
            }],
        },
        r"""
ids['admin-username'].value = 'giorgio';
ids['admin-password'].value = 'secret';
await ids['admin-login-form'].trigger('submit');
assert(ids['login-view'].hidden === true, 'login must hide');
assert(ids['admin-app'].hidden === false, 'app must show');
assert(ids['admin-password'].value === '', 'password must be removed from DOM');
assert(calls.length === 1, 'login should use one dashboard request');
assert(calls[0].headers.Authorization === 'Basic ' + Buffer.from('giorgio:secret').toString('base64'), 'Basic header missing');
const dash = flatten(ids['dashboard-content']);
assert(dash.includes('Account attivi') && dash.includes('Accessi attivi') && dash.includes('Pubblicazioni online') && dash.includes('Richieste nuove'), 'real KPI labels missing');
assert(!dash.includes('NEVER_RENDER'), 'non-whitelisted dashboard field rendered');
assert(ids['dashboard-content'].children.length === 4, 'invented KPI count');
""",
    )
    assert "SCENARIO_PASS" in out


def test_login_401_and_503_are_controlled_without_raw_backend_payload():
    for status, expected in ((401, "Credenziali non valide."), (503, "Servizio amministrativo non disponibile.")):
        out = _run_node(
            {"GET /api/owner/admin/dashboard": [{"status": status, "body": {"detail": "RAW_BACKEND"}}]},
            f"""
ids['admin-username'].value = 'bad';
ids['admin-password'].value = 'bad';
await ids['admin-login-form'].trigger('submit');
assert(ids['admin-app'].hidden === true, 'app should stay hidden');
assert(ids['login-view'].hidden === false, 'login should stay visible');
assert(ids['admin-password'].value === '', 'password should clear');
assert(ids['admin-login-status'].textContent === {json.dumps(expected)}, 'controlled message mismatch');
assert(!ids['admin-login-status'].textContent.includes('RAW_BACKEND'), 'raw backend payload exposed');
""",
        )
        assert "SCENARIO_PASS" in out


def test_accounts_get_preserves_backend_order_whitelists_and_renders_xss_as_text():
    out = _run_node(
        {
            "GET /api/owner/admin/dashboard": [{"status": 200, "body": {}}],
            "GET /api/owner/admin/accounts": [{
                "status": 200,
                "body": {"items": [
                    {"id": 9, "display_name": "<script>alert(1)</script>", "email": "a@example.it", "status": "active", "preferred_language": "it", "contact_id": 999, "disabled_at": "PRIVATE"},
                    {"id": 8, "display_name": "Secondo", "email": "b@example.it", "status": "disabled", "preferred_language": "en", "last_login_at": "PRIVATE2"},
                ]},
            }],
        },
        r"""
ids['admin-username'].value = 'u'; ids['admin-password'].value = 'p';
await ids['admin-login-form'].trigger('submit');
await ids['nav-accounts'].trigger('click');
const text = flatten(ids['accounts-content']);
assert(text.includes('<script>alert(1)</script>'), 'XSS should remain text');
assert(text.indexOf('<script>alert(1)</script>') < text.indexOf('Secondo'), 'backend order changed');
assert(text.includes('a@example.it') && text.includes('Lingua'), 'public account fields missing');
assert(!text.includes('PRIVATE') && !text.includes('999'), 'private/non-whitelisted fields rendered');
assert(ids['accounts-content'].children.length === 2, 'account count mismatch');
""",
    )
    assert "SCENARIO_PASS" in out


def test_accounts_empty_state_and_session_401_returns_to_login():
    out = _run_node(
        {
            "GET /api/owner/admin/dashboard": [{"status": 200, "body": {}}],
            "GET /api/owner/admin/accounts": [{"status": 200, "body": {"items": []}}],
        },
        r"""
ids['admin-username'].value = 'u'; ids['admin-password'].value = 'p';
await ids['admin-login-form'].trigger('submit');
await ids['nav-accounts'].trigger('click');
assert(ids['accounts-empty'].hidden === false, 'empty state missing');
""",
    )
    assert "SCENARIO_PASS" in out

    out = _run_node(
        {
            "GET /api/owner/admin/dashboard": [{"status": 200, "body": {}}],
            "GET /api/owner/admin/accounts": [{"status": 401, "body": {"detail": "no"}}],
        },
        r"""
ids['admin-username'].value = 'u'; ids['admin-password'].value = 'p';
await ids['admin-login-form'].trigger('submit');
await ids['nav-accounts'].trigger('click');
assert(ids['login-view'].hidden === false && ids['admin-app'].hidden === true, '401 must logout locally');
assert(ids['admin-login-status'].textContent === 'Credenziali non valide.', '401 message');
""",
    )
    assert "SCENARIO_PASS" in out


def test_create_account_exact_payload_double_submit_guard_and_controlled_reload():
    out = _run_node(
        {
            "GET /api/owner/admin/dashboard": [{"status": 200, "body": {}}],
            "GET /api/owner/admin/accounts": [
                {"status": 200, "body": {"items": []}},
                {"status": 200, "body": {"items": [{"id": 21, "display_name": "Mario", "status": "invited", "preferred_language": "it"}]}},
            ],
            "POST /api/owner/admin/accounts": [{"status": 201, "body": {"id": 21}, "defer": "createAccount"}],
        },
        r"""
ids['admin-username'].value = 'u'; ids['admin-password'].value = 'p';
await ids['admin-login-form'].trigger('submit');
await ids['nav-accounts'].trigger('click');
ids['account-contact-id'].value = '77'; ids['account-language'].value = 'it';
const first = ids['account-create-form'].trigger('submit');
await sleepTick();
const second = ids['account-create-form'].trigger('submit');
await sleepTick();
assert(countCalls('POST', '/api/owner/admin/accounts') === 1, 'double submit not blocked');
const post = calls.find(c => c.method === 'POST' && c.url === '/api/owner/admin/accounts');
assert(JSON.stringify(JSON.parse(post.body)) === JSON.stringify({contact_id:77, preferred_language:'it'}), 'account payload mismatch');
deferred.createAccount();
await first; await second;
assert(countCalls('GET', '/api/owner/admin/accounts') === 2, 'accounts section should reload once after success');
assert(ids['account-form-status'].textContent === 'Account proprietario creato.', 'success message missing');
""",
    )
    assert "SCENARIO_PASS" in out


def test_disable_account_requires_inline_confirmation_and_enable_endpoint_exists():
    out = _run_node(
        {
            "GET /api/owner/admin/dashboard": [{"status": 200, "body": {}}],
            "GET /api/owner/admin/accounts": [
                {"status": 200, "body": {"items": [{"id": 3, "display_name": "Mario", "status": "active", "preferred_language": "it"}]}},
                {"status": 200, "body": {"items": [{"id": 3, "display_name": "Mario", "status": "disabled", "preferred_language": "it"}]}},
                {"status": 200, "body": {"items": [{"id": 3, "display_name": "Mario", "status": "active", "preferred_language": "it"}]}},
            ],
            "POST /api/owner/admin/accounts/3/disable": [{"status": 200, "body": {"id": 3, "status": "disabled"}}],
            "POST /api/owner/admin/accounts/3/enable": [{"status": 200, "body": {"id": 3, "status": "active"}}],
        },
        r"""
ids['admin-username'].value = 'u'; ids['admin-password'].value = 'p';
await ids['admin-login-form'].trigger('submit');
await ids['nav-accounts'].trigger('click');
const disable = findButton(ids['accounts-content'], 'Disabilita');
assert(disable, 'disable action missing');
await disable.trigger('click');
assert(countCalls('POST', '/api/owner/admin/accounts/3/disable') === 0, 'disable must require confirmation');
const confirm = findButton(ids['accounts-content'], 'Conferma');
assert(confirm, 'inline confirmation missing');
await confirm.trigger('click');
assert(countCalls('POST', '/api/owner/admin/accounts/3/disable') === 1, 'disable endpoint not called');
const enable = findButton(ids['accounts-content'], 'Abilita');
assert(enable, 'enable action missing after reload');
await enable.trigger('click');
assert(countCalls('POST', '/api/owner/admin/accounts/3/enable') === 1, 'enable endpoint not called');
""",
    )
    assert "SCENARIO_PASS" in out


def test_access_get_whitelist_create_payload_double_submit_and_revoke_confirmation():
    out = _run_node(
        {
            "GET /api/owner/admin/dashboard": [{"status": 200, "body": {}}],
            "GET /api/owner/admin/access": [
                {"status": 200, "body": {"items": [{"id": 4, "owner_account_id": 10, "property_id": 20, "access_role": "co_owner", "access_status": "active", "is_primary": True, "valid_until": "2030-01-01T10:00:00Z", "revoked_at": "PRIVATE"}]}},
                {"status": 200, "body": {"items": [{"id": 4, "owner_account_id": 10, "property_id": 20, "access_role": "co_owner", "access_status": "active", "is_primary": True}]}},
                {"status": 200, "body": {"items": [{"id": 4, "owner_account_id": 10, "property_id": 20, "access_role": "co_owner", "access_status": "revoked", "is_primary": True}]}},
            ],
            "POST /api/owner/admin/access": [{"status": 201, "body": {"id": 5}, "defer": "createAccess"}],
            "POST /api/owner/admin/access/4/revoke": [{"status": 200, "body": {"id": 4, "access_status": "revoked"}}],
        },
        r"""
ids['admin-username'].value = 'u'; ids['admin-password'].value = 'p';
await ids['admin-login-form'].trigger('submit');
await ids['nav-access'].trigger('click');
let text = flatten(ids['access-content']);
assert(text.includes('Comproprietario') && text.includes('ID account') && text.includes('ID immobile'), 'access whitelist fields missing');
assert(!text.includes('PRIVATE'), 'private access field rendered');
ids['access-owner-account-id'].value = '10'; ids['access-property-id'].value = '20'; ids['access-role'].value = 'delegate'; ids['access-primary'].checked = true; ids['access-valid-until'].value = '2030-05-06T12:30';
const first = ids['access-create-form'].trigger('submit'); await sleepTick();
const second = ids['access-create-form'].trigger('submit'); await sleepTick();
assert(countCalls('POST', '/api/owner/admin/access') === 1, 'access double submit not blocked');
const post = calls.find(c => c.method === 'POST' && c.url === '/api/owner/admin/access');
const payload = JSON.parse(post.body);
assert(payload.owner_account_id === 10 && payload.property_id === 20 && payload.access_role === 'delegate' && payload.is_primary === true, 'access payload core fields');
assert(typeof payload.valid_until === 'string' && payload.valid_until.includes('2030-05-06'), 'valid_until missing');
deferred.createAccess(); await first; await second;
const revoke = findButton(ids['access-content'], 'Revoca accesso');
assert(revoke, 'revoke action missing');
await revoke.trigger('click');
assert(countCalls('POST', '/api/owner/admin/access/4/revoke') === 0, 'revoke must require confirmation');
const confirm = findButton(ids['access-content'], 'Conferma');
await confirm.trigger('click');
assert(countCalls('POST', '/api/owner/admin/access/4/revoke') === 1, 'revoke endpoint not called');
""",
    )
    assert "SCENARIO_PASS" in out


def test_stale_accounts_response_cannot_overwrite_new_access_section():
    out = _run_node(
        {
            "GET /api/owner/admin/dashboard": [{"status": 200, "body": {}}],
            "GET /api/owner/admin/accounts": [{"status": 200, "body": {"items": [{"id": 99, "display_name": "OLD ACCOUNT", "status": "active"}]}, "defer": "oldAccounts"}],
            "GET /api/owner/admin/access": [{"status": 200, "body": {"items": [{"id": 7, "owner_account_id": 1, "property_id": 2, "access_role": "owner", "access_status": "active", "is_primary": False}]}}],
        },
        r"""
ids['admin-username'].value = 'u'; ids['admin-password'].value = 'p';
await ids['admin-login-form'].trigger('submit');
const oldRequest = ids['nav-accounts'].trigger('click');
await sleepTick();
await ids['nav-access'].trigger('click');
assert(ids['section-access'].hidden === false && ids['section-accounts'].hidden === true, 'access section should be active');
deferred.oldAccounts();
await oldRequest; await sleepTick();
assert(!flatten(ids['accounts-content']).includes('OLD ACCOUNT'), 'stale account response rendered');
assert(flatten(ids['access-content']).includes('Accesso #7'), 'current access response lost');
""",
    )
    assert "SCENARIO_PASS" in out


def test_logout_clears_memory_ui_and_invalidates_pending_response():
    out = _run_node(
        {
            "GET /api/owner/admin/dashboard": [{"status": 200, "body": {}}],
            "GET /api/owner/admin/accounts": [{"status": 200, "body": {"items": [{"id": 1, "display_name": "SHOULD NOT RENDER", "status": "active"}]}, "defer": "accounts"}],
        },
        r"""
ids['admin-username'].value = 'u'; ids['admin-password'].value = 'p';
await ids['admin-login-form'].trigger('submit');
const pending = ids['nav-accounts'].trigger('click'); await sleepTick();
await ids['admin-logout'].trigger('click');
assert(ids['login-view'].hidden === false && ids['admin-app'].hidden === true, 'logout view');
assert(ids['admin-username'].value === '' && ids['admin-password'].value === '', 'credential inputs not cleared');
deferred.accounts(); await pending; await sleepTick();
assert(!flatten(ids['accounts-content']).includes('SHOULD NOT RENDER'), 'pending response survived logout');
const before = calls.length;
await ids['nav-accounts'].trigger('click');
assert(calls.length === before, 'API request allowed after local logout');
""",
    )
    assert "SCENARIO_PASS" in out


def test_error_mapping_and_mutation_guards_are_present_for_required_statuses():
    source = APP_JS.read_text(encoding="utf-8")
    for status in (401, 403, 404, 409, 422, 429, 503, 500):
        assert f"error.status === {status}" in source or (status == 500 and "error.status >= 500" in source)
    assert "mutationsInFlight" in source
    assert "dashboardGeneration" in source
    assert "accountsGeneration" in source
    assert "accessGeneration" in source
    assert "sessionGeneration" in source
    assert "window.confirm" not in source


def test_responsive_css_and_all_p71_features_have_real_styles():
    css = APP_CSS.read_text(encoding="utf-8")
    for selector in (
        ".login-shell", ".app-shell", ".sidebar", ".admin-nav", ".kpi-grid", ".entity-list",
        ".entity-card", ".form-grid", ".inline-confirm", ".status-badge", ".error-state",
    ):
        assert selector in css
    assert "@media (max-width: 760px)" in css
    assert "@media (max-width: 520px)" in css


def test_admin_javascript_parses_with_node():
    result = subprocess.run(["node", "--check", str(APP_JS)], text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_p72_real_backend_contract_publications_and_requests_is_frozen():
    assert set(PublicationCreate.model_fields) == {
        "property_id", "publication_type", "title", "summary", "body", "acknowledgement_required"
    }
    assert set(get_args(PublicationCreate.model_fields["publication_type"].annotation)) == {
        "general_update", "marketing_update", "visit_update", "feedback_summary", "strategy_update", "milestone"
    }
    assert PublicationCreate.model_fields["title"].metadata[0].min_length == 1
    assert PublicationCreate.model_fields["title"].metadata[1].max_length == 200
    assert set(PublicationUpdate.model_fields) == {
        "publication_type", "title", "summary", "body", "acknowledgement_required"
    }
    assert set(get_args(FeedbackCreate.model_fields["feedback_type"].annotation)) == {
        "contact_request", "correction_request", "general_message", "strategy_feedback",
        "price_review", "availability_update", "document_question"
    }
    assert set(FeedbackStatus.model_fields) == {"status", "handled_by", "public_response"}
    assert set(get_args(FeedbackStatus.model_fields["status"].annotation)) == {
        "new", "in_review", "handled", "closed"
    }
    router = (ROOT / "owner" / "router_admin.py").read_text(encoding="utf-8")
    for declaration in (
        '@router.get("/publications")', '@router.post("/publications", status_code=201)',
        '@router.patch("/publications/{i}")', '@router.post("/publications/{i}/publish")',
        '@router.post("/publications/{i}/archive")', '@router.post("/publications/{i}/supersede", status_code=201)',
        '@router.get("/feedback")', '@router.patch("/feedback/{i}")',
    ):
        assert declaration in router
    repo = REPOSITORY.read_text(encoding="utf-8")
    assert "owner_publications ORDER BY created_at DESC" in repo
    assert "owner_feedback ORDER BY submitted_at DESC" in repo
    assert "Solo draft pubblicabile" in repo
    assert "Solo published archiviabile" in repo
    assert "Solo published sostituibile" in repo
    assert "pubblicata o archiviata è immutabile" in repo


def test_p72_markup_navigation_enums_and_accessibility():
    parser = _html()
    required = {
        "nav-publications", "nav-requests", "section-publications", "section-requests",
        "publication-create-form", "publication-property-id", "publication-type", "publication-title",
        "publication-summary", "publication-body", "publication-ack-required", "publication-create-submit",
        "publication-form-status", "publications-loading", "publications-empty", "publications-error",
        "publications-error-message", "publications-retry", "publications-reload", "publications-content",
        "requests-loading", "requests-empty", "requests-error", "requests-error-message", "requests-retry",
        "requests-reload", "requests-content",
    }
    assert required <= parser.ids
    assert {
        "publication-property-id", "publication-type", "publication-title", "publication-summary",
        "publication-body", "publication-ack-required",
    } <= parser.labels_for
    assert parser.select_options["publication-type"] == [
        "general_update", "marketing_update", "visit_update", "feedback_summary", "strategy_update", "milestone"
    ]


def test_publications_list_preserves_backend_order_whitelists_xss_and_state_gating():
    out = _run_node(
        {
            "GET /api/owner/admin/dashboard": [{"status": 200, "body": {}}],
            "GET /api/owner/admin/publications": [{"status": 200, "body": {"items": [
                {"id": 1, "property_id": 11, "publication_type": "general_update", "title": "<script>alert(1)</script>", "summary": "S1", "body": "B1", "status": "draft", "version_number": 1, "acknowledgement_required": True, "created_at": "2026-08-17T10:00:00Z", "published_by": "PRIVATE"},
                {"id": 2, "property_id": 12, "publication_type": "milestone", "title": "Pubblicata", "summary": "S2", "body": "B2", "status": "published", "version_number": 2, "published_at": "2026-08-17T11:00:00Z", "superseded_by_publication_id": 999},
                {"id": 3, "property_id": 13, "publication_type": "strategy_update", "title": "Archiviata", "body": "B3", "status": "archived", "version_number": 1, "archived_at": "2026-08-17T12:00:00Z", "internal_secret": "NEVER"},
            ]}}],
        },
        r"""
ids['admin-username'].value='u'; ids['admin-password'].value='p';
await ids['admin-login-form'].trigger('submit');
await ids['nav-publications'].trigger('click');
assert(ids['section-publications'].hidden === false, 'publications section not active');
assert(ids['publications-content'].children.length === 3, 'publication count');
const first = ids['publications-content'].children[0];
const second = ids['publications-content'].children[1];
const third = ids['publications-content'].children[2];
const text = flatten(ids['publications-content']);
assert(text.includes('<script>alert(1)</script>'), 'XSS must remain text');
assert(text.indexOf('<script>alert(1)</script>') < text.indexOf('Pubblicata') && text.indexOf('Pubblicata') < text.indexOf('Archiviata'), 'backend order changed');
assert(!text.includes('PRIVATE') && !text.includes('NEVER') && !text.includes('999'), 'private fields rendered');
assert(findButton(first, 'Modifica draft') && findButton(first, 'Pubblica'), 'draft actions missing');
assert(!findButton(first, 'Archivia') && !findButton(first, 'Nuova versione'), 'invalid draft actions');
assert(findButton(second, 'Archivia') && findButton(second, 'Nuova versione'), 'published actions missing');
assert(!findButton(second, 'Modifica draft') && !findButton(second, 'Pubblica'), 'published must be immutable');
assert(!findButton(third, 'Modifica draft') && !findButton(third, 'Pubblica') && !findButton(third, 'Archivia') && !findButton(third, 'Nuova versione'), 'archived must have no mutative actions');
""",
    )
    assert "SCENARIO_PASS" in out


def test_create_publication_exact_payload_double_submit_and_reload():
    out = _run_node(
        {
            "GET /api/owner/admin/dashboard": [{"status": 200, "body": {}}],
            "GET /api/owner/admin/publications": [
                {"status": 200, "body": {"items": []}},
                {"status": 200, "body": {"items": [{"id": 4, "property_id": 77, "publication_type": "marketing_update", "title": "Nuova", "body": "Testo", "status": "draft", "version_number": 1}]}},
            ],
            "POST /api/owner/admin/publications": [{"status": 201, "body": {"id": 4}, "defer": "createPub"}],
        },
        r"""
ids['admin-username'].value='u'; ids['admin-password'].value='p';
await ids['admin-login-form'].trigger('submit'); await ids['nav-publications'].trigger('click');
ids['publication-property-id'].value='77'; ids['publication-type'].value='marketing_update';
ids['publication-title'].value='Titolo'; ids['publication-summary'].value='Sintesi'; ids['publication-body'].value='Corpo'; ids['publication-ack-required'].checked=true;
const first=ids['publication-create-form'].trigger('submit'); await sleepTick();
const second=ids['publication-create-form'].trigger('submit'); await sleepTick();
assert(countCalls('POST','/api/owner/admin/publications')===1,'create publication double submit');
const post=calls.find(c=>c.method==='POST'&&c.url==='/api/owner/admin/publications');
const payload=JSON.parse(post.body);
assert(JSON.stringify(payload)===JSON.stringify({property_id:77,publication_type:'marketing_update',title:'Titolo',summary:'Sintesi',body:'Corpo',acknowledgement_required:true}),'publication create payload mismatch');
deferred.createPub(); await first; await second;
assert(countCalls('GET','/api/owner/admin/publications')===2,'publication section reload exactly once');
assert(ids['publication-form-status'].textContent==='Draft creato.','create success missing');
""",
    )
    assert "SCENARIO_PASS" in out


def test_edit_draft_patch_double_submit_and_controlled_409():
    out = _run_node(
        {
            "GET /api/owner/admin/dashboard": [{"status": 200, "body": {}}],
            "GET /api/owner/admin/publications": [{"status": 200, "body": {"items": [{"id": 5, "property_id": 20, "publication_type": "general_update", "title": "Old", "summary": "Old s", "body": "Old b", "status": "draft", "version_number": 1}]}}],
            "PATCH /api/owner/admin/publications/5": [{"status": 409, "body": {"detail": "RAW CONFLICT"}, "defer": "patchPub"}],
        },
        r"""
ids['admin-username'].value='u'; ids['admin-password'].value='p'; await ids['admin-login-form'].trigger('submit'); await ids['nav-publications'].trigger('click');
const card=ids['publications-content'].children[0]; await findButton(card,'Modifica draft').trigger('click');
const form=findByAttribute(card,'data-publication-editor','edit'); assert(form,'edit form missing');
findByAttribute(form,'data-field','publication_type').value='strategy_update';
findByAttribute(form,'data-field','title').value='New title';
findByAttribute(form,'data-field','summary').value='';
findByAttribute(form,'data-field','body').value='New body';
findByAttribute(form,'data-field','acknowledgement_required').checked=true;
const first=form.trigger('submit'); await sleepTick(); const second=form.trigger('submit'); await sleepTick();
assert(countCalls('PATCH','/api/owner/admin/publications/5')===1,'edit double submit not blocked');
const payload=JSON.parse(calls.find(c=>c.method==='PATCH').body);
assert(JSON.stringify(payload)===JSON.stringify({publication_type:'strategy_update',title:'New title',summary:'',body:'New body',acknowledgement_required:true}),'edit payload mismatch');
deferred.patchPub(); await first; await second;
const status=findByAttribute(form,'role','status');
assert(status.textContent==='Operazione non più valida nello stato corrente.','409 not controlled');
assert(!status.textContent.includes('RAW CONFLICT'),'raw error exposed');
""",
    )
    assert "SCENARIO_PASS" in out


def test_publish_and_archive_require_inline_confirmation_and_use_exact_endpoints():
    out = _run_node(
        {
            "GET /api/owner/admin/dashboard": [{"status": 200, "body": {}}],
            "GET /api/owner/admin/publications": [
                {"status": 200, "body": {"items": [{"id": 6, "property_id": 1, "publication_type": "general_update", "title": "Draft", "body": "B", "status": "draft", "version_number": 1}]}},
                {"status": 200, "body": {"items": [{"id": 6, "property_id": 1, "publication_type": "general_update", "title": "Draft", "body": "B", "status": "published", "version_number": 1}]}},
                {"status": 200, "body": {"items": [{"id": 6, "property_id": 1, "publication_type": "general_update", "title": "Draft", "body": "B", "status": "archived", "version_number": 1}]}},
            ],
            "POST /api/owner/admin/publications/6/publish": [{"status": 200, "body": {"id": 6, "status": "published"}}],
            "POST /api/owner/admin/publications/6/archive": [{"status": 200, "body": {"id": 6, "status": "archived"}}],
        },
        r"""
ids['admin-username'].value='u'; ids['admin-password'].value='p'; await ids['admin-login-form'].trigger('submit'); await ids['nav-publications'].trigger('click');
let card=ids['publications-content'].children[0]; await findButton(card,'Pubblica').trigger('click');
assert(countCalls('POST','/api/owner/admin/publications/6/publish')===0,'publish without confirm');
await findButton(card,'Conferma').trigger('click');
assert(countCalls('POST','/api/owner/admin/publications/6/publish')===1,'publish endpoint missing');
card=ids['publications-content'].children[0]; await findButton(card,'Archivia').trigger('click');
assert(countCalls('POST','/api/owner/admin/publications/6/archive')===0,'archive without confirm');
await findButton(card,'Conferma').trigger('click');
assert(countCalls('POST','/api/owner/admin/publications/6/archive')===1,'archive endpoint missing');
""",
    )
    assert "SCENARIO_PASS" in out


def test_supersede_uses_real_publicationcreate_contract_and_creates_new_version_request():
    out = _run_node(
        {
            "GET /api/owner/admin/dashboard": [{"status": 200, "body": {}}],
            "GET /api/owner/admin/publications": [
                {"status": 200, "body": {"items": [{"id": 7, "property_id": 44, "publication_type": "milestone", "title": "Published", "summary": "S", "body": "B", "status": "published", "version_number": 3}]}},
                {"status": 200, "body": {"items": [{"id": 8, "property_id": 44, "publication_type": "strategy_update", "title": "V4", "body": "Nuovo", "status": "draft", "version_number": 4}]}},
            ],
            "POST /api/owner/admin/publications/7/supersede": [{"status": 201, "body": {"id": 8}, "defer": "supPub"}],
        },
        r"""
ids['admin-username'].value='u'; ids['admin-password'].value='p'; await ids['admin-login-form'].trigger('submit'); await ids['nav-publications'].trigger('click');
const card=ids['publications-content'].children[0]; await findButton(card,'Nuova versione').trigger('click');
const form=findByAttribute(card,'data-publication-editor','supersede'); assert(form,'supersede editor missing');
findByAttribute(form,'data-field','publication_type').value='strategy_update'; findByAttribute(form,'data-field','title').value='V4'; findByAttribute(form,'data-field','summary').value=''; findByAttribute(form,'data-field','body').value='Nuovo';
const first=form.trigger('submit'); await sleepTick(); const second=form.trigger('submit'); await sleepTick();
assert(countCalls('POST','/api/owner/admin/publications/7/supersede')===1,'supersede double submit');
const payload=JSON.parse(calls.find(c=>c.url==='/api/owner/admin/publications/7/supersede').body);
assert(payload.property_id===44 && payload.publication_type==='strategy_update' && payload.title==='V4' && payload.body==='Nuovo','supersede PublicationCreate contract mismatch');
deferred.supPub(); await first; await second;
assert(countCalls('GET','/api/owner/admin/publications')===2,'supersede reload');
""",
    )
    assert "SCENARIO_PASS" in out


def test_requests_list_whitelist_labels_order_xss_and_patch_exact_contract():
    out = _run_node(
        {
            "GET /api/owner/admin/dashboard": [{"status": 200, "body": {}}],
            "GET /api/owner/admin/feedback": [
                {"status": 200, "body": {"items": [
                    {"id": 12, "feedback_type": "price_review", "subject": "<img src=x onerror=alert(2)>", "message": "Messaggio", "status": "new", "submitted_at": "2026-08-17T10:00:00Z", "availability_from": None, "availability_to": None, "handled_at": None, "public_response": None, "owner_account_id": 999, "internal_notes": "PRIVATE", "handled_by": "PRIVATE_HANDLER"},
                    {"id": 11, "feedback_type": "document_question", "subject": "Seconda", "message": "Domanda", "status": "handled", "submitted_at": "2026-08-16T10:00:00Z", "handled_at": "2026-08-17T11:00:00Z", "public_response": "Risposta"},
                ]}},
                {"status": 200, "body": {"items": [{"id": 12, "feedback_type": "price_review", "subject": "Prima", "message": "Messaggio", "status": "in_review", "submitted_at": "2026-08-17T10:00:00Z", "public_response": "Testo pubblico"}]}},
            ],
            "PATCH /api/owner/admin/feedback/12": [{"status": 200, "body": {"id": 12, "status": "in_review"}, "defer": "patchRequest"}],
        },
        r"""
ids['admin-username'].value='u'; ids['admin-password'].value='p'; await ids['admin-login-form'].trigger('submit'); await ids['nav-requests'].trigger('click');
assert(ids['requests-content'].children.length===2,'request count');
const text=flatten(ids['requests-content']);
assert(text.includes('<img src=x onerror=alert(2)>'),'request XSS should remain text');
assert(text.indexOf('<img src=x onerror=alert(2)>') < text.indexOf('Seconda'),'request order changed');
assert(text.includes('Revisione del prezzo') && text.includes('Domanda sui documenti'),'controlled type labels missing');
assert(text.includes('Nuova') && text.includes('Gestita') && text.includes('Risposta'),'status/public response missing');
assert(!text.includes('PRIVATE') && !text.includes('999'),'private request fields rendered');
const card=ids['requests-content'].children[0]; await findButton(card,'Gestisci richiesta').trigger('click');
const form=findByAttribute(card,'data-request-editor','12'); assert(form,'request editor missing');
findByAttribute(form,'data-field','request_status').value='in_review'; findByAttribute(form,'data-field','public_response').value='Testo pubblico';
const first=form.trigger('submit'); await sleepTick(); const second=form.trigger('submit'); await sleepTick();
assert(countCalls('PATCH','/api/owner/admin/feedback/12')===1,'request double submit');
const payload=JSON.parse(calls.find(c=>c.method==='PATCH'&&c.url==='/api/owner/admin/feedback/12').body);
assert(JSON.stringify(payload)===JSON.stringify({status:'in_review',public_response:'Testo pubblico'}),'request PATCH payload should contain only public handling fields');
deferred.patchRequest(); await first; await second;
assert(countCalls('GET','/api/owner/admin/feedback')===2,'request reload after patch');
""",
    )
    assert "SCENARIO_PASS" in out


def test_p72_stale_publications_and_requests_responses_do_not_overwrite_new_section():
    out = _run_node(
        {
            "GET /api/owner/admin/dashboard": [{"status": 200, "body": {}}],
            "GET /api/owner/admin/publications": [{"status": 200, "body": {"items": [{"id": 1, "title": "OLD PUB", "status": "draft"}]}, "defer": "oldPub"}],
            "GET /api/owner/admin/feedback": [{"status": 200, "body": {"items": [{"id": 2, "subject": "CURRENT REQUEST", "feedback_type": "general_message", "message": "M", "status": "new"}]}}],
        },
        r"""
ids['admin-username'].value='u'; ids['admin-password'].value='p'; await ids['admin-login-form'].trigger('submit');
const old=ids['nav-publications'].trigger('click'); await sleepTick(); await ids['nav-requests'].trigger('click');
assert(ids['section-requests'].hidden===false && ids['section-publications'].hidden===true,'requests must be active');
deferred.oldPub(); await old; await sleepTick();
assert(!flatten(ids['publications-content']).includes('OLD PUB'),'stale publication rendered');
assert(flatten(ids['requests-content']).includes('CURRENT REQUEST'),'current request missing');
""",
    )
    assert "SCENARIO_PASS" in out


def test_p72_401_during_request_patch_returns_login_and_no_manual_notification_api():
    out = _run_node(
        {
            "GET /api/owner/admin/dashboard": [{"status": 200, "body": {}}],
            "GET /api/owner/admin/feedback": [{"status": 200, "body": {"items": [{"id": 33, "feedback_type": "general_message", "subject": "S", "message": "M", "status": "new"}]}}],
            "PATCH /api/owner/admin/feedback/33": [{"status": 401, "body": {"detail": "RAW"}}],
        },
        r"""
ids['admin-username'].value='u'; ids['admin-password'].value='p'; await ids['admin-login-form'].trigger('submit'); await ids['nav-requests'].trigger('click');
const card=ids['requests-content'].children[0]; await findButton(card,'Gestisci richiesta').trigger('click');
const form=findByAttribute(card,'data-request-editor','33'); findByAttribute(form,'data-field','request_status').value='handled';
await form.trigger('submit');
assert(ids['login-view'].hidden===false && ids['admin-app'].hidden===true,'401 must return login');
assert(ids['admin-login-status'].textContent==='Credenziali non valide.','controlled 401 message');
""",
    )
    assert "SCENARIO_PASS" in out
    source = APP_JS.read_text(encoding="utf-8")
    assert "/notifications" not in source
    assert "request_handled" not in source



def test_p72_422_is_controlled_for_publication_create_without_raw_payload():
    out = _run_node(
        {
            "GET /api/owner/admin/dashboard": [{"status": 200, "body": {}}],
            "GET /api/owner/admin/publications": [{"status": 200, "body": {"items": []}}],
            "POST /api/owner/admin/publications": [{"status": 422, "body": {"detail": [{"msg": "RAW PYDANTIC"}]}}],
        },
        r"""
ids['admin-username'].value='u'; ids['admin-password'].value='p'; await ids['admin-login-form'].trigger('submit'); await ids['nav-publications'].trigger('click');
ids['publication-property-id'].value='1'; ids['publication-type'].value='general_update'; ids['publication-title'].value='Titolo'; ids['publication-body'].value='Corpo';
await ids['publication-create-form'].trigger('submit');
assert(ids['publication-form-status'].textContent==='Controlla i dati inseriti e riprova.','422 controlled message');
assert(!ids['publication-form-status'].textContent.includes('RAW PYDANTIC'),'raw pydantic exposed');
""",
    )
    assert "SCENARIO_PASS" in out


def test_stale_requests_response_cannot_overwrite_dashboard_after_section_change():
    out = _run_node(
        {
            "GET /api/owner/admin/dashboard": [
                {"status": 200, "body": {}},
                {"status": 200, "body": {"active_accounts": 5}},
            ],
            "GET /api/owner/admin/feedback": [{"status": 200, "body": {"items": [{"id": 91, "feedback_type": "general_message", "subject": "OLD REQUEST", "message": "M", "status": "new"}]}, "defer": "oldRequest"}],
        },
        r"""
ids['admin-username'].value='u'; ids['admin-password'].value='p'; await ids['admin-login-form'].trigger('submit');
const old=ids['nav-requests'].trigger('click'); await sleepTick(); await ids['nav-dashboard'].trigger('click');
deferred.oldRequest(); await old; await sleepTick();
assert(ids['section-dashboard'].hidden===false && ids['section-requests'].hidden===true,'dashboard should stay active');
assert(!flatten(ids['requests-content']).includes('OLD REQUEST'),'stale request rendered');
assert(flatten(ids['dashboard-content']).includes('5'),'dashboard data lost');
""",
    )
    assert "SCENARIO_PASS" in out


def test_p72_css_and_generations_cover_publications_and_requests():
    source = APP_JS.read_text(encoding="utf-8")
    css = APP_CSS.read_text(encoding="utf-8")
    assert "publicationsGeneration" in source and "requestsGeneration" in source
    for selector in (
        ".inline-editor", ".publication-card", ".request-card", ".public-response", ".entity-copy", ".editor-actions"
    ):
        assert selector in css
    assert "@media (max-width: 760px)" in css
