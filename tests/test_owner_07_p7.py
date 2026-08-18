from __future__ import annotations

import json
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from typing import get_args

from owner.schemas import (
    AccessCreate,
    AccountCreate,
    FeedbackCreate,
    FeedbackStatus,
    PrivacyValidationRequest,
    PublicationCreate,
    PublicationUpdate,
    SharedDocumentCreate,
    SharedDocumentSupersede,
    SharedDocumentUpdate,
    VisitFeedbackCreate,
    VisitFeedbackSupersede,
    VisitFeedbackUpdate,
)


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
  add(...names) {{ for (const name of names) this.values.add(name); }}
  remove(...names) {{ for (const name of names) this.values.delete(name); }}
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
    this.files = [];
    this.className = '';
    this.children = [];
    this.parentNode = null;
    this.attributes = {{}};
    this.listeners = {{}};
    this.classList = new FakeClassList();
    this._textContent = '';
  }}
  set textContent(value) {{ this._textContent = String(value ?? ''); }}
  get textContent() {{ return this._textContent; }}
  append(...nodes) {{ for (const node of nodes) {{ node.parentNode = this; this.children.push(node); }} }}
  replaceChildren(...nodes) {{ for (const child of this.children) child.parentNode = null; this.children = []; for (const node of nodes) {{ node.parentNode = this; this.children.push(node); }} }}
  remove() {{ if (!this.parentNode) return; this.parentNode.children = this.parentNode.children.filter(child => child !== this); this.parentNode = null; }}
  click() {{ this.clicked = true; }}
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
    if (this.id === 'document-link-form') {{
      for (const id of ['document-property-document-id','document-owner-account-id','document-public-title','document-expires-at','document-created-by']) ids[id].value = '';
      ids['document-ack-required'].checked = false;
      ids['document-public-type'].value = '';
    }}
    if (this.id === 'document-upload-form') {{
      for (const id of ['document-upload-property-id','document-upload-document-type','document-upload-source-title','document-upload-public-title','document-upload-owner-account-id','document-upload-supersedes-id','document-upload-expires-at','document-upload-created-by']) ids[id].value = '';
      ids['document-upload-file'].files = [];
      ids['document-upload-ack-required'].checked = false;
      ids['document-upload-public-type'].value = '';
    }}
    if (this.id === 'visit-feedback-create-form') {{
      for (const id of ['visit-feedback-property-visit-id','visit-feedback-owner-account-id','visit-feedback-summary','visit-feedback-created-by']) ids[id].value = '';
      ids['visit-feedback-category'].value = ''; ids['visit-feedback-sentiment'].value = '';
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
  'requests-loading','requests-empty','requests-error','requests-error-message','requests-retry','requests-reload','requests-content',
  'nav-documents','nav-visit-feedback','section-documents','section-visit-feedback','document-storage-health-check','document-storage-health-status',
  'document-link-form','document-property-document-id','document-owner-account-id','document-public-title','document-public-type','document-expires-at','document-created-by','document-ack-required','document-link-submit','document-link-status',
  'document-upload-form','document-upload-file','document-upload-property-id','document-upload-document-type','document-upload-source-title','document-upload-public-title','document-upload-public-type','document-upload-owner-account-id','document-upload-supersedes-id','document-upload-expires-at','document-upload-created-by','document-upload-ack-required','document-upload-submit','document-upload-status',
  'document-detail-panel','document-detail-close','document-detail-status','document-detail-content','document-reads-panel','document-reads-close','document-reads-status','document-reads-content','documents-loading','documents-empty','documents-error','documents-error-message','documents-retry','documents-reload','documents-content',
  'visit-feedback-create-form','visit-feedback-property-visit-id','visit-feedback-owner-account-id','visit-feedback-category','visit-feedback-sentiment','visit-feedback-summary','visit-feedback-created-by','visit-feedback-privacy-check','visit-feedback-create-submit','visit-feedback-form-status','visit-feedback-privacy-issues','visit-feedback-detail-panel','visit-feedback-detail-close','visit-feedback-detail-status','visit-feedback-detail-content','visit-feedback-loading','visit-feedback-empty','visit-feedback-error','visit-feedback-error-message','visit-feedback-retry','visit-feedback-reload','visit-feedback-content'
];
const ids = {{}};
for (const id of requiredIds) ids[id] = new FakeElement('div', id);
for (const id of ['admin-login-form','account-create-form','access-create-form','publication-create-form','document-link-form','document-upload-form','visit-feedback-create-form']) ids[id].tagName = 'FORM';
for (const id of ['admin-username','admin-password','account-contact-id','account-language','access-owner-account-id','access-property-id','access-primary','access-valid-until','publication-property-id','publication-title','publication-ack-required','document-property-document-id','document-owner-account-id','document-public-title','document-expires-at','document-created-by','document-ack-required','document-upload-file','document-upload-property-id','document-upload-document-type','document-upload-source-title','document-upload-public-title','document-upload-owner-account-id','document-upload-supersedes-id','document-upload-expires-at','document-upload-created-by','document-upload-ack-required','visit-feedback-property-visit-id','visit-feedback-owner-account-id','visit-feedback-created-by']) ids[id].tagName = 'INPUT';
for (const id of ['publication-summary','publication-body','visit-feedback-summary']) ids[id].tagName = 'TEXTAREA';
for (const id of ['access-role','publication-type','document-public-type','document-upload-public-type','visit-feedback-category','visit-feedback-sentiment']) ids[id].tagName = 'SELECT';
ids['account-language'].value = 'it';
ids['access-role'].value = 'owner';
ids['publication-type'].value = 'general_update';
ids['document-public-type'].value = 'mandate';
ids['document-upload-public-type'].value = 'mandate';
ids['visit-feedback-category'].value = 'price';
ids['visit-feedback-sentiment'].value = '';
ids['admin-app'].hidden = true;
ids['section-accounts'].hidden = true;
ids['section-access'].hidden = true;
ids['section-publications'].hidden = true;
ids['section-requests'].hidden = true;
ids['section-documents'].hidden = true;
ids['section-visit-feedback'].hidden = true;
ids['document-detail-panel'].hidden = true;
ids['document-reads-panel'].hidden = true;
ids['visit-feedback-detail-panel'].hidden = true;
ids['visit-feedback-privacy-issues'].hidden = true;
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
for (const id of ['documents-loading','documents-empty','documents-error','documents-content','visit-feedback-loading','visit-feedback-empty','visit-feedback-error','visit-feedback-content']) ids[id].hidden = true;

const createdElements = [];
const document = {{
  activeElement: null,
  getElementById(id) {{ return ids[id]; }},
  createElement(tag) {{ const node = new FakeElement(tag); createdElements.push(node); return node; }},
}};
global.document = document;
global.window = global;
global.btoa = text => Buffer.from(text, 'binary').toString('base64');
class FakeFormData {{ constructor() {{ this.entriesList = []; }} append(name, value) {{ this.entriesList.push([name, value]); }} get(name) {{ const row = this.entriesList.find(pair => pair[0] === name); return row ? row[1] : null; }} }}
global.FormData = FakeFormData;
global.URL = {{ createObjectURL(blob) {{ return `blob:fake/${{blob && blob.size ? blob.size : 0}}`; }}, revokeObjectURL(_url) {{}} }};

const routeQueues = {route_json};
const calls = [];
const deferred = {{}};
function responseFrom(spec) {{
  const status = spec.status ?? 200;
  const headerMap = spec.headers || {{}};
  return {{
    status,
    ok: status >= 200 && status < 300,
    headers: {{ get(name) {{ const key = Object.keys(headerMap).find(k => k.toLowerCase() === String(name).toLowerCase()); return key ? headerMap[key] : null; }} }},
    async json() {{ return spec.body ?? {{}}; }},
    async blob() {{ return spec.blob ?? {{ size: spec.blob_size ?? 10 }}; }},
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


def test_p73_scope_does_not_call_future_or_cross_module_apis():
    source = APP_JS.read_text(encoding="utf-8")
    for forbidden in (
        "/audit", "/tokens",
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

# OWNER 0.2 P7.3 — Documenti condivisi + Feedback visite -------------------

def test_p73_real_backend_contract_documents_and_visit_feedback_is_frozen():
    from owner.schemas import (
        SharedDocumentStatus,
        SharedDocumentType,
        VisitFeedbackCategory,
        VisitFeedbackSentiment,
        VisitFeedbackStatus,
    )

    assert set(SharedDocumentCreate.model_fields) == {
        "property_document_id", "owner_account_id", "public_title", "public_document_type",
        "expires_at", "acknowledgement_required", "created_by",
    }
    assert set(SharedDocumentUpdate.model_fields) == {
        "public_title", "public_document_type", "expires_at", "acknowledgement_required",
    }
    assert set(SharedDocumentSupersede.model_fields) == {
        "property_document_id", "public_title", "public_document_type", "expires_at",
        "acknowledgement_required", "created_by",
    }
    assert set(get_args(SharedDocumentType)) == {
        "mandate", "floor_plan", "ape", "cadastral_extract", "photo_report", "activity_report", "information",
    }
    assert set(get_args(SharedDocumentStatus)) == {"draft", "published", "revoked", "archived"}

    assert set(PrivacyValidationRequest.model_fields) == {"public_summary"}
    assert set(VisitFeedbackCreate.model_fields) == {
        "property_visit_id", "owner_account_id", "category", "public_summary", "sentiment", "created_by",
    }
    assert set(VisitFeedbackUpdate.model_fields) == {"category", "public_summary", "sentiment"}
    assert set(VisitFeedbackSupersede.model_fields) == {"category", "public_summary", "sentiment", "created_by"}
    assert set(get_args(VisitFeedbackCategory)) == {"price", "state", "layout", "location", "accessories", "general"}
    assert set(get_args(VisitFeedbackSentiment)) == {"positive", "neutral", "negative", "mixed"}
    assert set(get_args(VisitFeedbackStatus)) == {"draft", "published", "archived"}

    router_source = (ROOT / "owner" / "router_admin.py").read_text(encoding="utf-8")
    for declaration in (
        '@router.get("/documents")',
        '@router.post("/documents", status_code=201)',
        '@router.post("/documents/upload", status_code=201)',
        '@router.get("/document-storage/health")',
        '@router.get("/documents/{i}")',
        '@router.patch("/documents/{i}")',
        '@router.post("/documents/{i}/publish")',
        '@router.post("/documents/{i}/revoke")',
        '@router.post("/documents/{i}/archive")',
        '@router.post("/documents/{i}/supersede", status_code=201)',
        '@router.get("/documents/{i}/reads")',
        '@router.get("/documents/{i}/download")',
        '@router.post("/visit-feedback/validate-privacy")',
        '@router.get("/visit-feedback")',
        '@router.get("/visit-feedback/{i}")',
        '@router.post("/visit-feedback", status_code=201)',
        '@router.patch("/visit-feedback/{i}")',
        '@router.post("/visit-feedback/{i}/publish")',
        '@router.post("/visit-feedback/{i}/archive")',
        '@router.post("/visit-feedback/{i}/supersede", status_code=201)',
    ):
        assert declaration in router_source


def test_p73_markup_navigation_document_and_visit_feedback_forms_are_real():
    parser = _html()
    for element_id in (
        "nav-documents", "section-documents", "document-storage-health-check", "document-link-form",
        "document-property-document-id", "document-public-title", "document-public-type", "document-link-submit",
        "document-upload-form", "document-upload-file", "document-upload-property-id", "document-upload-document-type",
        "document-upload-source-title", "document-upload-public-title", "document-upload-public-type", "document-upload-submit",
        "document-detail-panel", "document-reads-panel", "documents-loading", "documents-empty", "documents-error", "documents-content",
        "nav-visit-feedback", "section-visit-feedback", "visit-feedback-create-form", "visit-feedback-property-visit-id",
        "visit-feedback-category", "visit-feedback-sentiment", "visit-feedback-summary", "visit-feedback-privacy-check",
        "visit-feedback-create-submit", "visit-feedback-detail-panel", "visit-feedback-loading", "visit-feedback-empty",
        "visit-feedback-error", "visit-feedback-content",
    ):
        assert element_id in parser.ids

    assert parser.select_options["document-public-type"] == [
        "mandate", "floor_plan", "ape", "cadastral_extract", "photo_report", "activity_report", "information"
    ]
    assert parser.select_options["document-upload-public-type"] == parser.select_options["document-public-type"]
    assert parser.select_options["visit-feedback-category"] == ["price", "state", "layout", "location", "accessories", "general"]
    assert parser.select_options["visit-feedback-sentiment"] == ["", "positive", "neutral", "negative", "mixed"]
    for field_id in (
        "document-property-document-id", "document-owner-account-id", "document-public-title", "document-public-type",
        "document-expires-at", "document-created-by", "document-ack-required", "document-upload-file",
        "document-upload-property-id", "document-upload-document-type", "document-upload-source-title",
        "document-upload-public-title", "document-upload-public-type", "document-upload-owner-account-id",
        "document-upload-supersedes-id", "document-upload-expires-at", "document-upload-created-by",
        "document-upload-ack-required", "visit-feedback-property-visit-id", "visit-feedback-owner-account-id",
        "visit-feedback-category", "visit-feedback-sentiment", "visit-feedback-summary", "visit-feedback-created-by",
    ):
        assert field_id in parser.labels_for


def test_p73_security_has_safe_dom_no_storage_locators_and_no_future_admin_sections():
    source = APP_JS.read_text(encoding="utf-8")
    forbidden = (
        "innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function",
        "Object.keys", "Object.entries", "localStorage", "sessionStorage", "document.cookie", "/api/admin/check",
        "storage_key", "storage_locator", "r2_endpoint", "access_key", "secret_key", "presigned",
        "/audit", "/tokens", "/api/core", "/api/property", "/api/flow", "/api/buy", "/api/match",
    )
    for token in forbidden:
        assert token not in source
    assert "new FormData()" in source
    assert "FileReader" not in source
    assert "base64" not in source.lower()
    assert "URL.createObjectURL" in source
    assert "Authorization: encodeBasic" in source


def test_documents_list_detail_reads_whitelist_order_xss_and_state_gating():
    items = [
        {"id": 1, "property_document_id": 101, "property_id": 20, "owner_account_id": 5, "public_title": "<script>alert(1)</script>.pdf", "public_document_type": "ape", "version_number": 1, "status": "draft", "published_at": None, "expires_at": None, "acknowledgement_required": True, "superseded_by_shared_document_id": None, "created_at": "2026-08-17T10:00:00Z", "source_title": "APE", "source_document_type": "ape", "source_status": "available", "file_present": True, "storage_key": "PRIVATE_KEY", "bucket": "PRIVATE_BUCKET"},
        {"id": 2, "property_document_id": 102, "property_id": 20, "owner_account_id": None, "public_title": "Pubblicato", "public_document_type": "floor_plan", "version_number": 2, "status": "published", "published_at": "2026-08-17T11:00:00Z", "acknowledgement_required": False, "superseded_by_shared_document_id": None, "source_title": "Plan", "source_status": "available", "file_present": True},
        {"id": 3, "property_document_id": 103, "property_id": 20, "public_title": "Revocato", "public_document_type": "information", "version_number": 1, "status": "revoked", "source_status": "available", "file_present": True},
        {"id": 4, "property_document_id": 104, "property_id": 20, "public_title": "Archiviato", "public_document_type": "photo_report", "version_number": 1, "status": "archived", "source_status": "available", "file_present": False},
    ]
    out = _run_node(
        {
            "GET /api/owner/admin/dashboard": [{"status": 200, "body": {}}],
            "GET /api/owner/admin/documents?limit=100&offset=0": [{"status": 200, "body": {"items": items, "limit": 100, "offset": 0}}],
            "GET /api/owner/admin/document-storage/health": [{"status": 200, "body": {"configured": True, "available": True}}],
            "GET /api/owner/admin/documents/1": [{"status": 200, "body": {**items[0], "revoked_by": "PRIVATE_OPERATOR", "storage_key": "SHOULD_NOT_RENDER"}}],
            "GET /api/owner/admin/documents/2/reads": [{"status": 200, "body": {"items": [{"owner_account_id": 5, "first_viewed_at": "2026-08-17T12:00:00Z", "last_viewed_at": "2026-08-17T12:30:00Z", "view_count": 3, "acknowledged_at": "2026-08-17T12:31:00Z", "internal": "PRIVATE"}]}}],
        },
        r"""
ids['admin-username'].value='u'; ids['admin-password'].value='p'; await ids['admin-login-form'].trigger('submit'); await ids['nav-documents'].trigger('click');
assert(ids['documents-content'].children.length===4,'document count');
const all=flatten(ids['documents-content']);
assert(all.includes('<script>alert(1)</script>.pdf'),'xss document title must remain text');
assert(all.indexOf('<script>alert(1)</script>.pdf') < all.indexOf('Pubblicato'),'backend order changed');
assert(!all.includes('PRIVATE_KEY') && !all.includes('PRIVATE_BUCKET'),'storage locator rendered');
assert(ids['document-storage-health-status'].textContent==='Storage documentale disponibile.','health not rendered');
const draft=findByAttribute(ids['documents-content'],'data-document-id','1');
assert(findButton(draft,'Modifica') && findButton(draft,'Pubblica'),'draft actions missing');
assert(!findButton(draft,'Revoca') && !findButton(draft,'Archivia'),'draft illegal actions');
const published=findByAttribute(ids['documents-content'],'data-document-id','2');
assert(findButton(published,'Revoca') && findButton(published,'Archivia') && findButton(published,'Nuova versione'),'published actions missing');
assert(!findButton(published,'Modifica') && !findButton(published,'Pubblica'),'published illegal actions');
const revoked=findByAttribute(ids['documents-content'],'data-document-id','3');
assert(findButton(revoked,'Archivia') && !findButton(revoked,'Revoca'),'revoked state gating');
const archived=findByAttribute(ids['documents-content'],'data-document-id','4');
assert(!findButton(archived,'Modifica') && !findButton(archived,'Pubblica') && !findButton(archived,'Revoca') && !findButton(archived,'Archivia') && !findButton(archived,'Nuova versione'),'archived must be immutable');
await findButton(draft,'Dettaglio').trigger('click');
assert(ids['document-detail-panel'].hidden===false,'detail panel hidden');
assert(flatten(ids['document-detail-content']).includes('<script>alert(1)</script>.pdf'),'detail title missing');
assert(!flatten(ids['document-detail-content']).includes('SHOULD_NOT_RENDER') && !flatten(ids['document-detail-content']).includes('PRIVATE_OPERATOR'),'detail leaked private fields');
await findButton(published,'Letture').trigger('click');
assert(flatten(ids['document-reads-content']).includes('3'),'reads count missing');
assert(!flatten(ids['document-reads-content']).includes('PRIVATE'),'reads leaked raw field');
""",
    )
    assert "SCENARIO_PASS" in out


def test_document_link_create_exact_payload_double_submit_and_reload():
    out = _run_node(
        {
            "GET /api/owner/admin/dashboard": [{"status": 200, "body": {}}],
            "GET /api/owner/admin/documents?limit=100&offset=0": [
                {"status": 200, "body": {"items": []}},
                {"status": 200, "body": {"items": [{"id": 9, "property_document_id": 77, "property_id": 3, "public_title": "Titolo", "public_document_type": "ape", "status": "draft", "version_number": 1, "file_present": True}]}},
            ],
            "GET /api/owner/admin/document-storage/health": [{"status": 200, "body": {"configured": True, "available": True}}],
            "POST /api/owner/admin/documents": [{"status": 201, "body": {"id": 9}, "defer": "createDoc"}],
        },
        r"""
ids['admin-username'].value='u'; ids['admin-password'].value='p'; await ids['admin-login-form'].trigger('submit'); await ids['nav-documents'].trigger('click');
ids['document-property-document-id'].value='77'; ids['document-owner-account-id'].value='5'; ids['document-public-title'].value='Titolo'; ids['document-public-type'].value='ape'; ids['document-created-by'].value='Operatore'; ids['document-ack-required'].checked=true;
const first=ids['document-link-form'].trigger('submit'); await sleepTick(); const second=ids['document-link-form'].trigger('submit'); await sleepTick();
assert(countCalls('POST','/api/owner/admin/documents')===1,'link create double submit');
const payload=JSON.parse(calls.find(c=>c.method==='POST'&&c.url==='/api/owner/admin/documents').body);
assert(JSON.stringify(payload)===JSON.stringify({owner_account_id:5,public_title:'Titolo',public_document_type:'ape',expires_at:null,acknowledgement_required:true,created_by:'Operatore',property_document_id:77}),'link payload mismatch');
deferred.createDoc(); await first; await second;
assert(countCalls('GET','/api/owner/admin/documents?limit=100&offset=0')===2,'documents not reloaded');
assert(ids['document-link-status'].textContent==='Documento collegato come draft.','success status missing');
""",
    )
    assert "SCENARIO_PASS" in out


def test_document_upload_uses_multipart_no_base64_double_submit_and_exact_fields():
    out = _run_node(
        {
            "GET /api/owner/admin/dashboard": [{"status": 200, "body": {}}],
            "GET /api/owner/admin/documents?limit=100&offset=0": [
                {"status": 200, "body": {"items": []}}, {"status": 200, "body": {"items": []}},
            ],
            "GET /api/owner/admin/document-storage/health": [{"status": 200, "body": {"configured": True, "available": True}}],
            "POST /api/owner/admin/documents/upload": [{"status": 201, "body": {"id": 10}, "defer": "uploadDoc"}],
        },
        r"""
ids['admin-username'].value='u'; ids['admin-password'].value='p'; await ids['admin-login-form'].trigger('submit'); await ids['nav-documents'].trigger('click');
const fakeFile={name:'plan.pdf',type:'application/pdf',size:100}; ids['document-upload-file'].files=[fakeFile];
ids['document-upload-property-id'].value='3'; ids['document-upload-document-type'].value='planimetria'; ids['document-upload-source-title'].value='Planimetria sorgente'; ids['document-upload-public-title'].value='Planimetria'; ids['document-upload-public-type'].value='floor_plan'; ids['document-upload-owner-account-id'].value='5'; ids['document-upload-supersedes-id'].value='8'; ids['document-upload-created-by'].value='Admin'; ids['document-upload-ack-required'].checked=true;
const first=ids['document-upload-form'].trigger('submit'); await sleepTick(); const second=ids['document-upload-form'].trigger('submit'); await sleepTick();
assert(countCalls('POST','/api/owner/admin/documents/upload')===1,'upload double submit');
const call=calls.find(c=>c.method==='POST'&&c.url==='/api/owner/admin/documents/upload');
assert(call.body instanceof FakeFormData,'upload must use FormData');
assert(call.body.get('file')===fakeFile && call.body.get('property_id')==='3' && call.body.get('document_type')==='planimetria','multipart source fields mismatch');
assert(call.body.get('source_title')==='Planimetria sorgente' && call.body.get('public_title')==='Planimetria' && call.body.get('public_document_type')==='floor_plan','multipart public fields mismatch');
assert(call.body.get('owner_account_id')==='5' && call.body.get('supersedes_shared_document_id')==='8' && call.body.get('created_by')==='Admin' && call.body.get('acknowledgement_required')==='true','multipart optional fields mismatch');
assert(!('Content-Type' in call.headers),'frontend must not set multipart Content-Type boundary');
deferred.uploadDoc(); await first; await second;
assert(countCalls('GET','/api/owner/admin/documents?limit=100&offset=0')===2,'upload reload missing');
""",
    )
    assert "SCENARIO_PASS" in out


def test_document_draft_patch_double_submit_and_controlled_409():
    item = {"id": 1, "property_document_id": 101, "property_id": 20, "owner_account_id": None, "public_title": "Prima", "public_document_type": "information", "status": "draft", "version_number": 1, "acknowledgement_required": False, "file_present": True}
    out = _run_node(
        {
            "GET /api/owner/admin/dashboard": [{"status": 200, "body": {}}],
            "GET /api/owner/admin/documents?limit=100&offset=0": [{"status": 200, "body": {"items": [item]}}],
            "GET /api/owner/admin/document-storage/health": [{"status": 200, "body": {"configured": True, "available": True}}],
            "PATCH /api/owner/admin/documents/1": [{"status": 409, "body": {"detail": "RAW INTERNAL"}, "defer": "patchDoc"}],
        },
        r"""
ids['admin-username'].value='u'; ids['admin-password'].value='p'; await ids['admin-login-form'].trigger('submit'); await ids['nav-documents'].trigger('click');
const card=ids['documents-content'].children[0]; await findButton(card,'Modifica').trigger('click'); const form=findByAttribute(card,'data-document-editor','edit'); assert(form,'edit form missing');
findByAttribute(form,'data-field','public_title').value='Nuovo titolo'; findByAttribute(form,'data-field','public_document_type').value='ape'; findByAttribute(form,'data-field','acknowledgement_required').checked=true;
const first=form.trigger('submit'); await sleepTick(); const second=form.trigger('submit'); await sleepTick(); assert(countCalls('PATCH','/api/owner/admin/documents/1')===1,'patch double submit');
const payload=JSON.parse(calls.find(c=>c.method==='PATCH').body); assert(JSON.stringify(payload)===JSON.stringify({public_title:'Nuovo titolo',public_document_type:'ape',expires_at:null,acknowledgement_required:true}),'document PATCH payload mismatch');
deferred.patchDoc(); await first; await second; assert(flatten(card).includes('Operazione non più valida nello stato corrente.'),'409 controlled message missing'); assert(!flatten(card).includes('RAW INTERNAL'),'raw error exposed');
""",
    )
    assert "SCENARIO_PASS" in out


def test_document_publish_revoke_archive_supersede_and_download_backend_only():
    base_items = [
        {"id": 1, "property_document_id": 101, "property_id": 20, "public_title": "Draft", "public_document_type": "information", "status": "draft", "version_number": 1, "file_present": True},
        {"id": 2, "property_document_id": 102, "property_id": 20, "public_title": "Pub A", "public_document_type": "ape", "status": "published", "version_number": 1, "file_present": True, "superseded_by_shared_document_id": None},
        {"id": 3, "property_document_id": 103, "property_id": 20, "public_title": "Rev", "public_document_type": "mandate", "status": "revoked", "version_number": 1, "file_present": True},
        {"id": 4, "property_document_id": 104, "property_id": 20, "public_title": "Pub B", "public_document_type": "floor_plan", "status": "published", "version_number": 2, "file_present": True, "superseded_by_shared_document_id": None},
    ]
    out = _run_node(
        {
            "GET /api/owner/admin/dashboard": [{"status": 200, "body": {}}],
            "GET /api/owner/admin/documents?limit=100&offset=0": [{"status": 200, "body": {"items": base_items}} for _ in range(5)],
            "GET /api/owner/admin/document-storage/health": [{"status": 200, "body": {"configured": True, "available": True}}],
            "POST /api/owner/admin/documents/1/publish": [{"status": 200, "body": {"id": 1, "status": "published"}}],
            "POST /api/owner/admin/documents/2/revoke": [{"status": 200, "body": {"id": 2, "status": "revoked"}}],
            "POST /api/owner/admin/documents/3/archive": [{"status": 200, "body": {"id": 3, "status": "archived"}}],
            "POST /api/owner/admin/documents/4/supersede": [{"status": 201, "body": {"id": 5, "status": "draft"}}],
            "GET /api/owner/admin/documents/4/download": [{"status": 200, "headers": {"Content-Disposition": 'attachment; filename="scheda.pdf"'}, "blob_size": 123}],
        },
        r"""
ids['admin-username'].value='u'; ids['admin-password'].value='p'; await ids['admin-login-form'].trigger('submit'); await ids['nav-documents'].trigger('click');
let card=findByAttribute(ids['documents-content'],'data-document-id','1'); await findButton(card,'Pubblica').trigger('click'); await findButton(card,'Conferma').trigger('click'); assert(countCalls('POST','/api/owner/admin/documents/1/publish')===1,'publish endpoint');
card=findByAttribute(ids['documents-content'],'data-document-id','2'); await findButton(card,'Revoca').trigger('click'); const revoke=findByAttribute(card,'data-document-revoke','2'); findByAttribute(revoke,'data-field','actor').value='Giorgio'; findByAttribute(revoke,'data-field','reason').value='Sostituito'; await revoke.trigger('submit'); const revPayload=JSON.parse(calls.find(c=>c.url==='/api/owner/admin/documents/2/revoke').body); assert(revPayload.actor==='Giorgio'&&revPayload.reason==='Sostituito','revoke contract');
card=findByAttribute(ids['documents-content'],'data-document-id','3'); await findButton(card,'Archivia').trigger('click'); await findButton(card,'Conferma').trigger('click'); assert(countCalls('POST','/api/owner/admin/documents/3/archive')===1,'archive endpoint');
card=findByAttribute(ids['documents-content'],'data-document-id','4'); await findButton(card,'Nuova versione').trigger('click'); const sup=findByAttribute(card,'data-document-editor','supersede'); findByAttribute(sup,'data-field','public_title').value='Versione nuova'; findByAttribute(sup,'data-field','public_document_type').value='ape'; findByAttribute(sup,'data-field','property_document_id').value='105'; findByAttribute(sup,'data-field','created_by').value='Admin'; await sup.trigger('submit'); const supPayload=JSON.parse(calls.find(c=>c.url==='/api/owner/admin/documents/4/supersede').body); assert(supPayload.property_document_id===105&&supPayload.public_title==='Versione nuova'&&supPayload.public_document_type==='ape'&&supPayload.created_by==='Admin','supersede contract');
card=findByAttribute(ids['documents-content'],'data-document-id','4'); await findButton(card,'Download').trigger('click'); const downloadCall=calls.find(c=>c.url==='/api/owner/admin/documents/4/download'); assert(downloadCall.headers.Authorization.startsWith('Basic '),'download missing Basic auth'); const anchor=createdElements.find(n=>n.tagName==='A'&&n.clicked); assert(anchor&&anchor.attributes.href.startsWith('blob:fake/')&&anchor.attributes.download==='scheda.pdf','backend download not converted safely');
""",
    )
    assert "SCENARIO_PASS" in out


def test_documents_storage_503_and_stale_list_are_controlled():
    out = _run_node(
        {
            "GET /api/owner/admin/dashboard": [{"status": 200, "body": {}}, {"status": 200, "body": {"active_accounts": 2}}],
            "GET /api/owner/admin/documents?limit=100&offset=0": [{"status": 200, "body": {"items": [{"id": 77, "public_title": "OLD DOC", "status": "draft"}]}, "defer": "oldDocs"}],
            "GET /api/owner/admin/document-storage/health": [{"status": 503, "body": {"detail": {"message": "RAW STORAGE"}}}],
        },
        r"""
ids['admin-username'].value='u'; ids['admin-password'].value='p'; await ids['admin-login-form'].trigger('submit');
const old=ids['nav-documents'].trigger('click'); await sleepTick(); assert(ids['document-storage-health-status'].textContent==='Servizio amministrativo non disponibile.','503 health controlled'); assert(!ids['document-storage-health-status'].textContent.includes('RAW STORAGE'),'raw health error');
await ids['nav-dashboard'].trigger('click'); deferred.oldDocs(); await old; await sleepTick(); assert(!flatten(ids['documents-content']).includes('OLD DOC'),'stale documents rendered'); assert(ids['section-dashboard'].hidden===false,'dashboard should remain active');
""",
    )
    assert "SCENARIO_PASS" in out


def test_visit_feedback_list_detail_whitelist_xss_state_gating_and_domains_are_distinct():
    items = [
        {"id": 21, "property_visit_id": 301, "property_id": 40, "owner_account_id": 5, "category": "price", "public_summary": "<img src=x onerror=alert(2)>", "sentiment": "mixed", "status": "draft", "version_number": 1, "created_at": "2026-08-17T10:00:00Z", "created_by": "PRIVATE", "internal_notes": "PRIVATE_NOTE"},
        {"id": 22, "property_visit_id": 302, "property_id": 40, "owner_account_id": None, "category": "layout", "public_summary": "Buona distribuzione", "sentiment": "positive", "status": "published", "version_number": 2, "published_at": "2026-08-17T11:00:00Z", "superseded_by_feedback_publication_id": None},
        {"id": 23, "property_visit_id": 303, "property_id": 40, "category": "general", "public_summary": "Archiviato", "sentiment": None, "status": "archived", "version_number": 1},
    ]
    out = _run_node(
        {
            "GET /api/owner/admin/dashboard": [{"status": 200, "body": {}}],
            "GET /api/owner/admin/visit-feedback?limit=50&offset=0": [{"status": 200, "body": {"items": items, "limit": 50, "offset": 0}}],
            "GET /api/owner/admin/visit-feedback/21": [{"status": 200, "body": {**items[0], "linked_activity_id": 999, "raw_payload": "PRIVATE_RAW"}}],
        },
        r"""
ids['admin-username'].value='u'; ids['admin-password'].value='p'; await ids['admin-login-form'].trigger('submit'); await ids['nav-visit-feedback'].trigger('click');
assert(ids['visit-feedback-content'].children.length===3,'visit feedback count'); const all=flatten(ids['visit-feedback-content']); assert(all.includes('<img src=x onerror=alert(2)>'),'xss summary not text'); assert(all.indexOf('<img src=x onerror=alert(2)>')<all.indexOf('Buona distribuzione'),'backend order changed'); assert(!all.includes('PRIVATE_NOTE')&&!all.includes('PRIVATE'),'private raw fields rendered');
const draft=findByAttribute(ids['visit-feedback-content'],'data-visit-feedback-id','21'); assert(findButton(draft,'Modifica')&&findButton(draft,'Pubblica'),'draft actions missing'); assert(!findButton(draft,'Archivia')&&!findButton(draft,'Nuova versione'),'draft illegal actions');
const published=findByAttribute(ids['visit-feedback-content'],'data-visit-feedback-id','22'); assert(findButton(published,'Archivia')&&findButton(published,'Nuova versione'),'published actions missing'); assert(!findButton(published,'Modifica')&&!findButton(published,'Pubblica'),'published illegal actions');
const archived=findByAttribute(ids['visit-feedback-content'],'data-visit-feedback-id','23'); assert(!findButton(archived,'Modifica')&&!findButton(archived,'Pubblica')&&!findButton(archived,'Archivia')&&!findButton(archived,'Nuova versione'),'archived actions');
await findButton(draft,'Dettaglio').trigger('click'); assert(flatten(ids['visit-feedback-detail-content']).includes('<img src=x onerror=alert(2)>'),'detail missing'); assert(!flatten(ids['visit-feedback-detail-content']).includes('PRIVATE_RAW')&&!flatten(ids['visit-feedback-detail-content']).includes('999'),'detail leaked internal fields');
assert(countCalls('GET','/api/owner/admin/feedback')===0,'requests endpoint mixed with visit feedback');
""",
    )
    assert "SCENARIO_PASS" in out


def test_visit_feedback_privacy_validation_invalid_is_controlled_and_blocks_create():
    out = _run_node(
        {
            "GET /api/owner/admin/dashboard": [{"status": 200, "body": {}}],
            "GET /api/owner/admin/visit-feedback?limit=50&offset=0": [{"status": 200, "body": {"items": []}}],
            "POST /api/owner/admin/visit-feedback/validate-privacy": [{"status": 200, "body": {"valid": False, "issues": [{"code": "email", "message": "Indirizzi email non ammessi", "raw": "SECRET"}]}}],
        },
        r"""
ids['admin-username'].value='u'; ids['admin-password'].value='p'; await ids['admin-login-form'].trigger('submit'); await ids['nav-visit-feedback'].trigger('click'); ids['visit-feedback-summary'].value='cliente@example.com'; await ids['visit-feedback-privacy-check'].trigger('click');
assert(ids['visit-feedback-form-status'].textContent==='La sintesi non supera la privacy validation.','privacy invalid status'); const issues=flatten(ids['visit-feedback-privacy-issues']); assert(issues.includes('Indirizzi email non ammessi'),'controlled issue missing'); assert(!issues.includes('SECRET'),'raw issue fields rendered'); assert(countCalls('POST','/api/owner/admin/visit-feedback')===0,'privacy check created feedback');
""",
    )
    assert "SCENARIO_PASS" in out


def test_visit_feedback_create_requires_privacy_exact_payload_double_submit_and_reload():
    out = _run_node(
        {
            "GET /api/owner/admin/dashboard": [{"status": 200, "body": {}}],
            "GET /api/owner/admin/visit-feedback?limit=50&offset=0": [{"status": 200, "body": {"items": []}}, {"status": 200, "body": {"items": []}}],
            "POST /api/owner/admin/visit-feedback/validate-privacy": [{"status": 200, "body": {"valid": True, "issues": []}, "defer": "privacyCreate"}],
            "POST /api/owner/admin/visit-feedback": [{"status": 201, "body": {"id": 50, "status": "draft"}}],
        },
        r"""
ids['admin-username'].value='u'; ids['admin-password'].value='p'; await ids['admin-login-form'].trigger('submit'); await ids['nav-visit-feedback'].trigger('click'); ids['visit-feedback-property-visit-id'].value='301'; ids['visit-feedback-owner-account-id'].value='5'; ids['visit-feedback-category'].value='layout'; ids['visit-feedback-sentiment'].value='positive'; ids['visit-feedback-summary'].value='Gli spazi sono stati valutati positivamente'; ids['visit-feedback-created-by'].value='Admin';
const first=ids['visit-feedback-create-form'].trigger('submit'); await sleepTick(); const second=ids['visit-feedback-create-form'].trigger('submit'); await sleepTick(); assert(countCalls('POST','/api/owner/admin/visit-feedback/validate-privacy')===1,'privacy double submit'); assert(countCalls('POST','/api/owner/admin/visit-feedback')===0,'create before privacy'); deferred.privacyCreate(); await first; await second; assert(countCalls('POST','/api/owner/admin/visit-feedback')===1,'create count'); const payload=JSON.parse(calls.find(c=>c.url==='/api/owner/admin/visit-feedback').body); assert(JSON.stringify(payload)===JSON.stringify({property_visit_id:301,owner_account_id:5,category:'layout',public_summary:'Gli spazi sono stati valutati positivamente',sentiment:'positive',created_by:'Admin'}),'visit create contract mismatch'); assert(countCalls('GET','/api/owner/admin/visit-feedback?limit=50&offset=0')===2,'visit list reload missing');
""",
    )
    assert "SCENARIO_PASS" in out


def test_visit_feedback_edit_publish_archive_supersede_use_privacy_and_exact_endpoints():
    items = [
        {"id": 1, "property_visit_id": 301, "property_id": 40, "category": "price", "public_summary": "Sintesi draft", "sentiment": "neutral", "status": "draft", "version_number": 1},
        {"id": 2, "property_visit_id": 302, "property_id": 40, "category": "layout", "public_summary": "Sintesi pubblicata", "sentiment": "positive", "status": "published", "version_number": 1, "superseded_by_feedback_publication_id": None},
        {"id": 3, "property_visit_id": 303, "property_id": 40, "category": "general", "public_summary": "Da archiviare", "sentiment": None, "status": "published", "version_number": 1, "superseded_by_feedback_publication_id": 9},
    ]
    out = _run_node(
        {
            "GET /api/owner/admin/dashboard": [{"status": 200, "body": {}}],
            "GET /api/owner/admin/visit-feedback?limit=50&offset=0": [{"status": 200, "body": {"items": items}} for _ in range(5)],
            "POST /api/owner/admin/visit-feedback/validate-privacy": [{"status": 200, "body": {"valid": True, "issues": []}} for _ in range(3)],
            "PATCH /api/owner/admin/visit-feedback/1": [{"status": 200, "body": {"id": 1}}],
            "POST /api/owner/admin/visit-feedback/1/publish": [{"status": 200, "body": {"id": 1, "status": "published"}}],
            "POST /api/owner/admin/visit-feedback/3/archive": [{"status": 200, "body": {"id": 3, "status": "archived"}}],
            "POST /api/owner/admin/visit-feedback/2/supersede": [{"status": 201, "body": {"id": 4, "status": "draft"}}],
        },
        r"""
ids['admin-username'].value='u'; ids['admin-password'].value='p'; await ids['admin-login-form'].trigger('submit'); await ids['nav-visit-feedback'].trigger('click');
let card=findByAttribute(ids['visit-feedback-content'],'data-visit-feedback-id','1'); await findButton(card,'Modifica').trigger('click'); let form=findByAttribute(card,'data-visit-feedback-editor','edit'); findByAttribute(form,'data-field','category').value='state'; findByAttribute(form,'data-field','sentiment').value='mixed'; findByAttribute(form,'data-field','public_summary').value='Sintesi aggiornata e anonima'; await form.trigger('submit'); const patchPayload=JSON.parse(calls.find(c=>c.url==='/api/owner/admin/visit-feedback/1'&&c.method==='PATCH').body); assert(JSON.stringify(patchPayload)===JSON.stringify({category:'state',public_summary:'Sintesi aggiornata e anonima',sentiment:'mixed'}),'visit patch contract');
card=findByAttribute(ids['visit-feedback-content'],'data-visit-feedback-id','1'); await findButton(card,'Pubblica').trigger('click'); await findButton(card,'Conferma').trigger('click'); assert(countCalls('POST','/api/owner/admin/visit-feedback/1/publish')===1,'visit publish endpoint');
card=findByAttribute(ids['visit-feedback-content'],'data-visit-feedback-id','3'); await findButton(card,'Archivia').trigger('click'); await findButton(card,'Conferma').trigger('click'); assert(countCalls('POST','/api/owner/admin/visit-feedback/3/archive')===1,'visit archive endpoint');
card=findByAttribute(ids['visit-feedback-content'],'data-visit-feedback-id','2'); await findButton(card,'Nuova versione').trigger('click'); form=findByAttribute(card,'data-visit-feedback-editor','supersede'); findByAttribute(form,'data-field','category').value='general'; findByAttribute(form,'data-field','sentiment').value=''; findByAttribute(form,'data-field','public_summary').value='Nuova sintesi anonima'; findByAttribute(form,'data-field','created_by').value='Admin'; await form.trigger('submit'); const supPayload=JSON.parse(calls.find(c=>c.url==='/api/owner/admin/visit-feedback/2/supersede').body); assert(JSON.stringify(supPayload)===JSON.stringify({category:'general',public_summary:'Nuova sintesi anonima',sentiment:null,created_by:'Admin'}),'visit supersede contract');
assert(countCalls('POST','/api/owner/admin/visit-feedback/validate-privacy')===3,'privacy validation must precede edit publish supersede');
""",
    )
    assert "SCENARIO_PASS" in out


def test_visit_feedback_stale_response_and_401_are_safe():
    out = _run_node(
        {
            "GET /api/owner/admin/dashboard": [{"status": 200, "body": {}}, {"status": 200, "body": {"active_accounts": 7}}],
            "GET /api/owner/admin/visit-feedback?limit=50&offset=0": [
                {"status": 200, "body": {"items": [{"id": 9, "public_summary": "OLD VISIT", "status": "draft"}]}, "defer": "oldVisit"},
                {"status": 401, "body": {"detail": "RAW"}},
            ],
        },
        r"""
ids['admin-username'].value='u'; ids['admin-password'].value='p'; await ids['admin-login-form'].trigger('submit'); const old=ids['nav-visit-feedback'].trigger('click'); await sleepTick(); await ids['nav-dashboard'].trigger('click'); deferred.oldVisit(); await old; await sleepTick(); assert(!flatten(ids['visit-feedback-content']).includes('OLD VISIT'),'stale visit feedback rendered');
await ids['nav-visit-feedback'].trigger('click'); assert(ids['login-view'].hidden===false&&ids['admin-app'].hidden===true,'401 must logout admin'); assert(ids['admin-login-status'].textContent==='Credenziali non valide.','401 controlled message');
""",
    )
    assert "SCENARIO_PASS" in out


def test_p73_css_generations_error_mapping_and_anti_error_features_are_real():
    source = APP_JS.read_text(encoding="utf-8")
    css = APP_CSS.read_text(encoding="utf-8")
    for token in (
        "documentsGeneration", "documentDetailGeneration", "documentReadsGeneration",
        "visitFeedbackGeneration", "visitFeedbackDetailGeneration", "privacyGeneration",
        "loadDocuments", "loadDocumentDetail", "loadDocumentReads", "submitDocumentUpload",
        "loadVisitFeedback", "loadVisitFeedbackDetail", "validateVisitFeedbackPrivacy",
        "error.status === 413",
    ):
        assert token in source
    for selector in (
        ".admin-two-column", ".detail-panel", ".document-card", ".visit-feedback-card",
        ".privacy-issues", ".storage-health", ".detail-meta", ".grouped-actions",
    ):
        assert selector in css
    assert "@media (max-width: 760px)" in css
