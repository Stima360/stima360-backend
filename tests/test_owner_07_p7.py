from __future__ import annotations

import json
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from typing import get_args

from owner.schemas import AccessCreate, AccountCreate


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
  'admin-logout','admin-global-status','section-title','nav-dashboard','nav-accounts','nav-access',
  'section-dashboard','section-accounts','section-access','dashboard-loading','dashboard-error','dashboard-error-message',
  'dashboard-retry','dashboard-reload','dashboard-content','account-create-form','account-contact-id','account-language',
  'account-create-submit','account-form-status','accounts-loading','accounts-empty','accounts-error','accounts-error-message',
  'accounts-retry','accounts-reload','accounts-content','access-create-form','access-owner-account-id','access-property-id',
  'access-role','access-primary','access-valid-until','access-create-submit','access-form-status','access-loading','access-empty',
  'access-error','access-error-message','access-retry','access-reload','access-content'
];
const ids = {{}};
for (const id of requiredIds) ids[id] = new FakeElement('div', id);
for (const id of ['admin-login-form','account-create-form','access-create-form']) ids[id].tagName = 'FORM';
for (const id of ['admin-username','admin-password','account-contact-id','account-language','access-owner-account-id','access-property-id','access-primary','access-valid-until']) ids[id].tagName = 'INPUT';
ids['access-role'].tagName = 'SELECT';
ids['account-language'].value = 'it';
ids['access-role'].value = 'owner';
ids['admin-app'].hidden = true;
ids['section-accounts'].hidden = true;
ids['section-access'].hidden = true;
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


def test_p71_scope_does_not_call_future_or_cross_module_apis():
    source = APP_JS.read_text(encoding="utf-8")
    for forbidden in (
        "/publications", "/feedback", "/documents", "/visit-feedback", "/audit", "/tokens",
        "/api/core", "/api/property", "/api/buy", "/api/match", "/api/flow",
        "/notifications", "/notification-preferences",
    ):
        assert forbidden not in source
    for expected in (
        "'/dashboard'", "'/accounts'", "'/access'", "/accounts/${", "/access/${",
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
