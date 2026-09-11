"""P26-4 - la OS Shell, ESEGUITA.

P26-3 ha certificato i due moduli di autenticazione: `auth.js` e
`api-client.js`. Sono provati davvero, in node, da
`tests/test_p26_3_shell_runtime.py`, e questo file non li ripete.

P26-4 riguarda la Shell attorno a quei moduli - bootstrap, login, ripristino,
navigazione, caricamento delle view, logout - e in particolare il CICLO DI VITA
DEL DOM attraverso un cambio di sessione. E' la' che P26-3 non poteva guardare:
due moduli che non toccano il documento non possono dire nulla su cosa resta
sullo schermo quando una sessione finisce.

LE DUE LACUNE CHE QUESTO FILE HA APERTO IN ROSSO

1. La superficie applicativa sopravviveva al logout. `onAuthChange` commutava
   `hidden` e nient'altro: i contatti, gli immobili e gli abbinamenti
   dell'agenzia appena uscita restavano nel DOM, insieme ai risultati della
   ricerca globale. Nascosti, non rimossi - a un `hidden = false` di distanza,
   o a un devtools aperto. Su una postazione condivisa, e a maggior ragione
   quando il prossimo a entrare appartiene a un'altra agenzia, non e'
   accettabile.

2. Una view in volo scriveva nel DOM dopo la fine della sessione. Le view sono
   asincrone: `container.innerHTML = ...` avviene DOPO l'await. Se nel
   frattempo arriva un 401, o l'utente esce, la risposta della vecchia sessione
   si dipingeva comunque - e con un nuovo login di un'altra agenzia si
   dipingeva DENTRO la sessione nuova. Un dato dell'agenzia A sullo schermo di
   un operatore dell'agenzia B, senza che nessuna richiesta del backend sia
   stata sbagliata.

COME VIENE ESEGUITO

Node (gia' presente) e nessuna dipendenza nuova. L'albero `assets/` viene
copiato in una directory temporanea con un `package.json` che dichiara
`{"type": "module"}`: cosi' node accetta i `.js` come ES module senza che si
riscriva un solo specificatore, e cio' che gira e' il file spedito.

Il DOM e' uno stub scritto qui: piccolo, ma vero abbastanza da far girare
`main.js`, il router e la ricerca globale. Non e' un browser, e le prove che
riguardano il rendering visivo non sono qui - ma il ciclo di vita del DOM, che
e' cio' che P26-4 deve dimostrare, e' osservabile per intero.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "static" / "os_shell" / "assets"

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(
    NODE is None,
    reason=(
        "node non e' disponibile: le prove di runtime della Shell non sono state "
        "eseguite. Questo va riportato come BLOCKED, mai come PASS."
    ),
)


def _stage(tmp_path: Path) -> Path:
    """Copia l'albero spedito e lo dichiara ESM, senza toccarne una riga.

    `{"type": "module"}` in un package.json della directory temporanea e' cio'
    che permette a node di caricare i `.js` come ES module. Non e' una
    dipendenza: e' un manifest di quattro parole in una cartella usa e getta, e
    soprattutto evita di riscrivere gli import - i moduli eseguiti sono
    byte-identici a quelli serviti.
    """
    staged = tmp_path / "assets"
    shutil.copytree(ASSETS, staged)
    (tmp_path / "package.json").write_text('{"type": "module"}', encoding="utf-8")
    return staged


# ---------------------------------------------------------------------------
# Il DOM
# ---------------------------------------------------------------------------

DOM = r"""
// Uno stub di DOM: abbastanza per far girare main.js, il router e i componenti.
// Ogni nodo tiene i figli veri, cosi' "cosa e' rimasto sullo schermo" e' una
// domanda con una risposta osservabile e non una supposizione.

// Un parser HTML minimo. Non e' un browser: riconosce tag, attributi e testo,
// che e' quanto usano i template della Shell. Serve perche' le view scrivono
// `innerHTML` e poi interrogano il risultato con querySelector - senza nodi
// veri quelle view non girerebbero, e P26-4 deve eseguirle, non evitarle.
const VOID_TAGS = new Set(['input', 'img', 'br', 'hr', 'meta', 'link']);

function parseHTML(html) {
  const roots = [];
  const stack = [];
  const push = (node) => {
    const parent = stack[stack.length - 1];
    if (parent) parent.appendChild(node); else roots.push(node);
  };
  const pattern = /<\/?([a-zA-Z][a-zA-Z0-9]*)((?:\s+[^<>]*?)?)\/?>|([^<]+)/g;
  let match;
  while ((match = pattern.exec(html)) !== null) {
    const [raw, tag, attributes, text] = match;
    if (text !== undefined) {
      const trimmed = text.replace(/\s+/g, ' ');
      if (trimmed.trim()) {
        const node = new El('#text');
        node._text = trimmed;
        push(node);
      }
      continue;
    }
    if (raw.startsWith('</')) { stack.pop(); continue; }
    const node = new El(tag);
    for (const [, name, value] of (attributes || '').matchAll(/([A-Za-z-]+)=["']([^"']*)["']/g)) {
      if (name === 'class') node.className = value;
      else if (name === 'id') node.id = value;
      else if (name.startsWith('data-')) {
        node.dataset[name.slice(5).replace(/-([a-z])/g, (_m, c) => c.toUpperCase())] = value;
      } else node.setAttribute(name, value);
    }
    push(node);
    if (!raw.endsWith('/>') && !VOID_TAGS.has(tag.toLowerCase())) stack.push(node);
  }
  return roots;
}

class El {
  constructor(tag) {
    this.tagName = String(tag || 'div').toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.attributes = {};
    this.dataset = {};
    this.style = {};
    this.hidden = false;
    this.disabled = false;
    this._text = '';
    this._html = '';
    this.listeners = {};
    this.value = '';
    this.id = '';
    this.classList = {
      _set: new Set(),
      add: (c) => this.classList._set.add(c),
      remove: (c) => this.classList._set.delete(c),
      toggle: (c, on) => { on ? this.classList._set.add(c) : this.classList._set.delete(c); },
      contains: (c) => this.classList._set.has(c),
    };
  }
  get parentElement() { return this.parentNode; }
  get childNodes() { return this.children; }
  contains(node) { return node === this || this._descendants().includes(node); }
  set className(value) { this.classList._set = new Set(String(value).split(/\s+/)); }
  get className() { return [...this.classList._set].join(' '); }
  set textContent(value) { this._text = String(value ?? ''); this._html = ''; this.children = []; }
  get textContent() {
    if (this._text) return this._text;
    return this.children.map((c) => c.textContent).join('');
  }
  set innerHTML(value) {
    this._html = String(value ?? '');
    this._text = '';
    this.children = [];
    for (const node of parseHTML(this._html)) this.appendChild(node);
  }
  get innerHTML() { return this._html || this.children.map((c) => c.outerHTML).join(''); }
  get outerHTML() { return '<' + this.tagName.toLowerCase() + '>' + this.innerHTML; }
  appendChild(child) { child.parentNode = this; this.children.push(child); return child; }
  append(...nodes) { for (const n of nodes) this.appendChild(n); }
  prepend(...nodes) { for (const n of nodes.reverse()) { n.parentNode = this; this.children.unshift(n); } }
  replaceChildren(...nodes) { this.children = []; this._html = ''; this._text = ''; this.append(...nodes); }
  remove() {
    if (!this.parentNode) return;
    this.parentNode.children = this.parentNode.children.filter((c) => c !== this);
    this.parentNode = null;
  }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  removeEventListener(type, fn) {
    this.listeners[type] = (this.listeners[type] || []).filter((f) => f !== fn);
  }
  dispatch(type, event = {}) {
    for (const fn of this.listeners[type] || []) fn({ preventDefault() {}, target: this, ...event });
  }
  _descendants() {
    return this.children.flatMap((c) => [c, ...c._descendants()]);
  }
  matches(selector) {
    return String(selector).split(',').some((part) => {
      const one = part.trim();
      if (!one) return false;
      const tag = /^[a-zA-Z][a-zA-Z0-9]*/.exec(one);
      if (tag && this.tagName !== tag[0].toUpperCase()) return false;
      for (const [, cls] of one.matchAll(/\.([A-Za-z0-9_-]+)/g)) {
        if (!this.classList.contains(cls)) return false;
      }
      for (const [, wanted] of one.matchAll(/#([A-Za-z0-9_-]+)/g)) {
        if (this.id !== wanted) return false;
      }
      for (const [, name, , value] of one.matchAll(/\[([A-Za-z-]+)(=["']?([^\]"']*)["']?)?\]/g)) {
        const key = name.startsWith('data-')
          ? name.slice(5).replace(/-([a-z])/g, (_m, c) => c.toUpperCase())
          : null;
        const actual = key !== null ? this.dataset[key] : this.getAttribute(name);
        if (actual === undefined || actual === null) return false;
        if (value !== undefined && String(actual) !== value) return false;
      }
      return true;
    });
  }
  querySelectorAll(selector) {
    return this._descendants().filter((e) => e.matches(selector));
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  closest(selector) {
    let node = this;
    while (node) { if (node.matches && node.matches(selector)) return node; node = node.parentNode; }
    return null;
  }
  reset() { this.value = ''; for (const e of this._descendants()) e.value = ''; }
  focus() {}
  // Il testo che un utente vedrebbe: contenuto proprio piu' quello dei figli.
  visibleText() {
    if (this.tagName === '#TEXT') return this._text;
    return this._text + this.children.map((c) => c.visibleText()).join('');
  }
}

const byId = {};
for (const id of ['login-view', 'app-view', 'login-form', 'login-error', 'logout-btn',
                  'page-title', 'content', 'nav', 'env-badge',
                  'login-email', 'login-password']) {
  byId[id] = new El('div');
  byId[id].id = id;
}
// content vive dentro un contenitore: mountGlobalSearch fa prepend sul parent.
const main = new El('main');
main.appendChild(byId['content']);
byId['login-form'].appendChild(byId['login-email']);
byId['login-form'].appendChild(byId['login-password']);
const submit = new El('button');
submit.setAttribute('type', 'submit');
submit.classList.add('submit');
byId['login-form'].appendChild(submit);

globalThis.document = {
  getElementById: (id) => byId[id] || null,
  createElement: (tag) => new El(tag),
  addEventListener() {},
  body: new El('body'),
};
// main.js cerca il bottone di submit con querySelector('button[type="submit"]').
byId['login-form'].querySelector = (selector) =>
  selector.includes('submit') ? submit : null;

globalThis.window = {
  location: { hash: '', hostname: 'stima360-backend-test.onrender.com' },
  addEventListener(type, fn) { (this._l ||= {})[type] = fn; },
  _fire(type) { const fn = (this._l || {})[type]; if (fn) return fn(); },
};

// Trappole: nessun modulo della Shell puo' toccare uno storage del browser.
function trap(name) {
  const boom = () => { throw new Error('FORBIDDEN: la Shell ha toccato ' + name); };
  return new Proxy({}, { get: boom, set: boom, has: boom, deleteProperty: boom });
}
globalThis.localStorage = trap('localStorage');
globalThis.sessionStorage = trap('sessionStorage');
globalThis.indexedDB = trap('indexedDB');
Object.defineProperty(globalThis.document, 'cookie', {
  get() { throw new Error('FORBIDDEN: la Shell ha letto document.cookie'); },
});

globalThis.__dom = { byId, main, submit, El };
"""

FETCH = """
// `fetch` scriptato e registrato. Ogni voce e' consumata una volta; se il
// codice chiede piu' di quanto lo scenario prevede, la richiesta in eccesso
// viene comunque registrata e risolta con un 500, cosi' un retry non
// desiderato si vede invece di far esplodere il run in modo ambiguo.
const calls = [];
let scripted = [];

function reply(spec) {
  return {
    status: spec.status,
    ok: spec.status >= 200 && spec.status < 300,
    async json() {
      if (spec.body === undefined) throw new Error('nessun corpo');
      return spec.body;
    },
  };
}

globalThis.fetch = async (url, options = {}) => {
  calls.push({ url, options });
  const next = scripted.shift();
  if (!next) return reply({ status: 500, body: { detail: 'non previsto' } });
  if (next.throw) throw new Error('rete non raggiungibile');
  return reply(next);
};

globalThis.__script = (entries) => { scripted = entries.slice(); };
globalThis.__calls = calls;
globalThis.__pending = () => scripted.length;

// Un tick abbastanza lungo da far completare le catene di promesse dei moduli.
globalThis.__settle = async (rounds = 12) => {
  for (let i = 0; i < rounds; i += 1) await Promise.resolve();
  await new Promise((resolve) => setTimeout(resolve, 0));
  for (let i = 0; i < rounds; i += 1) await Promise.resolve();
};
"""


def run_shell(staged: Path, scenario: str, script_before_boot: str = "[]") -> dict:
    """Esegue main.js dentro lo stub e restituisce quanto lo scenario riporta."""
    driver = staged.parent / "driver.mjs"
    driver.write_text(
        DOM
        + FETCH
        + f"\n__script({script_before_boot});\n"
        + f"const CLIENT = '{(staged / 'core' / 'api-client.js').as_posix()}';\n"
        + f"const ROUTER = '{(staged / 'core' / 'router.js').as_posix()}';\n"
        + f"await import('{(staged / 'main.js').as_posix()}');\n"
        + "await __settle();\n"
        + scenario
        + "\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [NODE, str(driver)], capture_output=True, text=True, timeout=60, cwd=staged.parent
    )
    if result.returncode != 0:
        raise AssertionError(
            f"il driver node e' fallito ({result.returncode}):\n"
            f"{result.stderr[-3000:]}\n--- stdout ---\n{result.stdout[-2000:]}"
        )
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def staged(tmp_path_factory) -> Path:
    return _stage(tmp_path_factory.mktemp("shell"))


# Una sessione viva e la risposta di /me che la descrive.
ME = {
    "user_id": 3,
    "agency_id": 7,
    "agency_name": "Agenzia A",
    "role": "agent",
    "is_platform_admin": False,
    "expires_at": "2030-01-01T00:00:00Z",
}
ME_OTHER = {**ME, "user_id": 9, "agency_id": 8, "agency_name": "Agenzia B"}

# Il boot chiama restore() -> GET /me. Uno scenario "gia' autenticato" parte da qui.
BOOTED = json.dumps([{"status": 200, "body": ME}])
ANONYMOUS = json.dumps([{"status": 401, "body": {"detail": "Non autorizzato"}}])


REPORT = """
console.log(JSON.stringify({
  content: __dom.byId['content'].visibleText(),
  contentChildren: __dom.byId['content'].children.length,
  title: __dom.byId['page-title'].textContent,
  loginHidden: __dom.byId['login-view'].hidden,
  appHidden: __dom.byId['app-view'].hidden,
  loginError: __dom.byId['login-error'].textContent,
  urls: __calls.map((c) => c.url),
  methods: __calls.map((c) => (c.options.method || 'GET')),
  credentials: __calls.map((c) => c.options.credentials || null),
  headers: __calls.map((c) => JSON.stringify(c.options.headers || {})),
  bodies: __calls.map((c) => c.options.body || null),
  searchPanel: __dom.main.children.filter((c) => c !== __dom.byId['content'])
                    .map((c) => c.visibleText()).join('|'),
  searchInput: (__dom.main.children.filter((c) => c !== __dom.byId['content'])[0] || {})
                    .querySelectorAll ? '' : '',
}));
"""


# ---------------------------------------------------------------------------
# Bootstrap, login, restore, logout
# ---------------------------------------------------------------------------

def test_1_the_shell_boots_by_asking_me(staged):
    """Il cookie e' HttpOnly: la sola cosa che sa dire se c'e' una sessione e'
    il server, e la Shell glielo chiede all'avvio."""
    out = run_shell(staged, REPORT, ANONYMOUS)
    assert out["urls"] == ["/api/operator-auth/me"]
    assert out["methods"] == ["GET"]
    assert out["credentials"] == ["include"]


def test_2_an_anonymous_boot_shows_the_login_and_no_error(staged):
    """Un 401 all'avvio significa "non c'e' sessione", non "e' andato storto"."""
    out = run_shell(staged, REPORT, ANONYMOUS)
    assert out["loginHidden"] is False and out["appHidden"] is True
    assert out["loginError"] == ""


def test_3_a_live_cookie_restores_the_session_without_a_new_login(staged):
    """Il guadagno visibile di P26-3, provato qui sulla Shell: dopo un refresh
    l'operatore e' ancora dentro."""
    out = run_shell(staged, REPORT, BOOTED)
    assert out["appHidden"] is False and out["loginHidden"] is True


def test_4_login_posts_to_operator_auth_and_then_asks_me(staged):
    scenario = """
__script([
  { status: 204 },
  { status: 200, body: ME_LIVE },
  { status: 200, body: { items: [] } },
  { status: 200, body: { items: [] } },
  { status: 200, body: { items: [] } },
  { status: 200, body: { items: [] } },
]);
__dom.byId['login-email'].value = 'operatore@example.test';
__dom.byId['login-password'].value = 'una-password';
__dom.byId['login-form'].dispatch('submit');
await __settle(40);
""".replace("ME_LIVE", json.dumps(ME)) + REPORT
    out = run_shell(staged, scenario, ANONYMOUS)
    assert out["urls"][1] == "/api/operator-auth/login"
    assert out["methods"][1] == "POST"
    assert out["urls"][2] == "/api/operator-auth/me"
    assert out["appHidden"] is False
    body = json.loads(out["bodies"][1])
    assert body == {"email": "operatore@example.test", "password": "una-password"}


def test_5_rejected_login_shows_a_message_and_stays_on_the_login(staged):
    scenario = """
__script([{ status: 401, body: { detail: 'Credenziali non valide.' } }]);
__dom.byId['login-email'].value = 'x@y.test';
__dom.byId['login-password'].value = 'sbagliata';
__dom.byId['login-form'].dispatch('submit');
await __settle(20);
""" + REPORT
    out = run_shell(staged, scenario, ANONYMOUS)
    assert out["appHidden"] is True and out["loginHidden"] is False
    assert "Credenziali" in out["loginError"]
    # Nessun secondo tentativo: login rifiutata, non riprovata.
    assert out["urls"].count("/api/operator-auth/login") == 1


def test_6_the_password_is_never_placed_in_a_header_or_a_url(staged):
    scenario = """
__script([{ status: 204 }, { status: 200, body: ME_LIVE },
          { status: 200, body: { items: [] } }, { status: 200, body: { items: [] } },
          { status: 200, body: { items: [] } }, { status: 200, body: { items: [] } }]);
__dom.byId['login-email'].value = 'a@b.test';
__dom.byId['login-password'].value = 'segretissima';
__dom.byId['login-form'].dispatch('submit');
await __settle(40);
""".replace("ME_LIVE", json.dumps(ME)) + REPORT
    out = run_shell(staged, scenario, ANONYMOUS)
    for url in out["urls"]:
        assert "segretissima" not in url
    for header in out["headers"]:
        assert "segretissima" not in header
    # Nel corpo della sola login, e in nessun altro.
    with_password = [b for b in out["bodies"] if b and "segretissima" in b]
    assert len(with_password) == 1


def test_7_logout_revokes_server_side(staged):
    scenario = """
__script([{ status: 204 }]);
__dom.byId['logout-btn'].dispatch('click');
await __settle(20);
""" + REPORT
    out = run_shell(staged, scenario, BOOTED)
    assert "/api/operator-auth/logout" in out["urls"]
    assert out["methods"][out["urls"].index("/api/operator-auth/logout")] == "POST"
    assert out["appHidden"] is True and out["loginHidden"] is False


# ---------------------------------------------------------------------------
# LE DUE LACUNE - queste erano RED
# ---------------------------------------------------------------------------

def test_8_logout_wipes_the_application_surface(staged):
    """RED prima della correzione.

    `onAuthChange` commutava `hidden` e nient'altro. I dati dell'agenzia appena
    uscita restavano nel DOM: nascosti, non rimossi.
    """
    scenario = """
// Una sessione viva, una view renderizzata, poi logout.
__script([{ status: 200, body: { items: [{ id: 1, display_name: 'CONTATTO-DI-A' }] } },
          { status: 200, body: { items: [] } }]);
window.location.hash = '#/contatti';
await window._fire('hashchange');
await __settle(30);
const before = __dom.byId['content'].visibleText();
__script([{ status: 204 }]);
__dom.byId['logout-btn'].dispatch('click');
await __settle(30);
console.log(JSON.stringify({
  before,
  content: __dom.byId['content'].visibleText(),
  contentChildren: __dom.byId['content'].children.length,
  title: __dom.byId['page-title'].textContent,
}));
"""
    out = run_shell(staged, scenario, BOOTED)
    assert "CONTATTO-DI-A" in out["before"], "lo scenario non ha renderizzato nulla"
    assert "CONTATTO-DI-A" not in out["content"], (
        "dopo il logout i dati dell'agenzia precedente sono ancora nel DOM"
    )
    assert out["contentChildren"] == 0
    assert out["content"] == ""


def test_9_session_expiry_wipes_the_application_surface(staged):
    """Stessa regola per la via involontaria: un 401 che azzera la sessione
    deve lasciare lo schermo pulito quanto un logout."""
    scenario = """
__script([{ status: 200, body: { items: [{ id: 1, display_name: 'CONTATTO-DI-A' }] } },
          { status: 200, body: { items: [] } }]);
window.location.hash = '#/contatti';
await window._fire('hashchange');
await __settle(30);
const before = __dom.byId['content'].visibleText();
// La sessione muore sotto i piedi: una richiesta di sfondo torna 401, e la
// Shell resta sulla rotta corrente. Nessuna navigazione a ripulire il DOM per
// conto suo - il DOM deve essere ripulito perche' la sessione e' finita.
__script([{ status: 401, body: { detail: 'Non autorizzato' } }]);
const { apiGet } = await import(CLIENT);
await apiGet('/api/core/tasks?limit=1').catch(() => {});
await __settle(30);
console.log(JSON.stringify({
  before,
  content: __dom.byId['content'].visibleText(),
  contentChildren: __dom.byId['content'].children.length,
  loginHidden: __dom.byId['login-view'].hidden,
  appHidden: __dom.byId['app-view'].hidden,
}));
"""
    out = run_shell(staged, scenario, BOOTED)
    assert "CONTATTO-DI-A" in out["before"], "lo scenario non ha renderizzato nulla"
    assert out["appHidden"] is True and out["loginHidden"] is False
    assert "CONTATTO-DI-A" not in out["content"], (
        "dopo la scadenza della sessione i dati precedenti sono ancora nel DOM"
    )
    assert out["content"] == "" and out["contentChildren"] == 0, (
        "la superficie non e' stata svuotata: e' solo cambiato cosa c'e' sopra"
    )


def test_10_a_render_in_flight_does_not_paint_after_the_session_ends(staged):
    """RED prima della correzione, ed e' la lacuna piu' seria delle due.

    Le view sono asincrone: scrivono DOPO l'await. Se la sessione finisce nel
    frattempo, la risposta della vecchia sessione si dipingeva comunque.

    Lo scenario usa la vista "Oggi" perche' e' costruita su `allSettled`:
    scrive SEMPRE dopo l'await, anche se una chiamata fallisce. Con una view
    che si interrompe da sola l'assenza del dato non proverebbe nulla, ed e'
    esattamente cosi' che una prima versione di questo test lasciava
    sopravvivere ogni mutazione.
    """
    scenario = """
let release;
let delivered = false;
const held = new Promise((resolve) => { release = resolve; });
const realFetch = globalThis.fetch;
let first = true;
globalThis.fetch = async (url, options) => {
  if (first && url.startsWith('/api/core/tasks')) {
    first = false;
    __calls.push({ url, options });
    await held;
    delivered = true;
    return { status: 200, ok: true, async json() {
      return { items: [{ id: 1, title: 'TASK-IN-VOLO-DI-A', status: 'open',
                         priority: 'high', due_at: '2030-01-01T10:00:00Z' }] };
    } };
  }
  return realFetch(url, options);
};
__script([{ status: 200, body: { items: [] } }, { status: 200, body: { items: [] } },
          { status: 200, body: { items: [] } }, { status: 200, body: { items: [] } }]);
window.location.hash = '#/oggi';
window._fire('hashchange');
await __settle(6);

// Logout mentre la view e' ancora in attesa.
__script([{ status: 204 }]);
__dom.byId['logout-btn'].dispatch('click');
await __settle(25);

// Ora la risposta della vecchia sessione arriva.
release();
await __settle(60);

console.log(JSON.stringify({
  content: __dom.byId['content'].visibleText(),
  contentChildren: __dom.byId['content'].children.length,
  appHidden: __dom.byId['app-view'].hidden,
  delivered,
}));
"""
    out = run_shell(staged, scenario, BOOTED)
    assert out["delivered"], (
        "la risposta trattenuta non e' stata consegnata: la corsa non e' stata "
        "esercitata e un PASS non direbbe nulla"
    )
    assert out["appHidden"] is True
    assert "TASK-IN-VOLO-DI-A" not in out["content"], (
        "una risposta della sessione conclusa e' stata dipinta nel DOM"
    )
    assert out["content"] == "" and out["contentChildren"] == 0


def test_10b_a_late_failure_does_not_paint_an_error_into_a_logged_out_shell(staged):
    """L'altra meta' della guardia, e sopravviveva a una mutazione finche' non
    e' esistito questo test.

    Se la view non riesce, `renderCurrentRoute` scrive una casella d'errore. E'
    giusto mentre la sessione e' viva; su una Shell tornata al login e' testo
    di una sessione finita che ricompare su uno schermo che dovrebbe essere
    vuoto - e, se nel frattempo e' entrato un altro operatore, il messaggio puo'
    nominare una risorsa che non ha titolo di conoscere.
    """
    scenario = """
let release;
let delivered = false;
const held = new Promise((resolve, reject) => { release = reject; });
const realFetch = globalThis.fetch;
let first = true;
globalThis.fetch = async (url, options) => {
  if (first && url.startsWith('/api/core/contacts')) {
    first = false;
    __calls.push({ url, options });
    try { await held; } catch (e) { delivered = true; throw e; }
  }
  return realFetch(url, options);
};
window.location.hash = '#/contatti';
window._fire('hashchange');
await __settle(6);

__script([{ status: 204 }]);
__dom.byId['logout-btn'].dispatch('click');
await __settle(25);

release(new Error('IMMOBILE-RISERVATO-DI-A non accessibile'));
await __settle(60);

console.log(JSON.stringify({
  content: __dom.byId['content'].visibleText(),
  contentChildren: __dom.byId['content'].children.length,
  appHidden: __dom.byId['app-view'].hidden,
  delivered,
}));
"""
    out = run_shell(staged, scenario, BOOTED)
    assert out["delivered"], "il fallimento tardivo non e' stato consegnato"
    assert out["appHidden"] is True
    assert "IMMOBILE-RISERVATO-DI-A" not in out["content"], (
        "un errore della sessione conclusa e' comparso sulla Shell disconnessa"
    )
    assert out["content"] == "" and out["contentChildren"] == 0


def test_10c_the_routers_error_path_is_guarded_too(staged):
    """La guardia sul ramo d'errore, provata dove vive.

    Ogni view spedita cattura i propri errori, quindi la casella d'errore di
    `renderCurrentRoute` e' una difesa per la view che se ne dimentica: dalle
    rotte reali non ci si arriva, e attraverso di esse la regola resterebbe
    non provata - una mutazione che toglie la guardia sopravviveva proprio
    cosi'. Qui il router viene caricato da solo, con una rotta che fallisce
    dopo un await, e si osserva cosa scrive quando l'epoca e' cambiata nel
    frattempo.
    """
    scenario = """
const router = await import(ROUTER);
const box = document.createElement('div');
let epoch = 1;
router.initRouter(box, { epoch: () => epoch });

let release;
const held = new Promise((_resolve, reject) => { release = reject; });
router.registerRoute('probe', async (container) => {
  await held;                       // fallisce piu' tardi
  container.innerHTML = 'mai';
});

window.location.hash = '#/probe';
const rendering = router.renderCurrentRoute();
await __settle(5);

epoch += 1;                          // la sessione cambia mentre la view attende
release(new Error('DETTAGLIO-RISERVATO-DI-A'));
await rendering;
await __settle(20);

const guarded = box.visibleText();

// E la stessa rotta, senza cambio di epoca, DEVE invece mostrare l'errore:
// altrimenti "non stampa nulla" sarebbe vero per il motivo sbagliato.
let release2;
const held2 = new Promise((_resolve, reject) => { release2 = reject; });
router.registerRoute('probe', async () => { await held2; });
const rendering2 = router.renderCurrentRoute();
await __settle(5);
release2(new Error('ERRORE-VISIBILE'));
await rendering2;
await __settle(20);

console.log(JSON.stringify({ guarded, unguarded: box.visibleText() }));
"""
    out = run_shell(staged, scenario, ANONYMOUS)
    assert out["unguarded"] and "ERRORE-VISIBILE" in out["unguarded"], (
        "con l'epoca invariata la casella d'errore deve comparire: senza questo "
        "il controllo sopra non distingue la guardia da un router muto"
    )
    assert "DETTAGLIO-RISERVATO-DI-A" not in out["guarded"]
    assert out["guarded"] == "", (
        "il router ha scritto qualcosa dopo un cambio di sessione"
    )


def test_11_a_render_in_flight_does_not_paint_into_the_next_operators_session(staged):
    """Il caso peggiore, e il motivo per cui la guardia non puo' essere
    "sei autenticato?": qui la Shell E' di nuovo autenticata quando la vecchia
    risposta arriva, con un operatore di un'ALTRA agenzia. Solo un contatore
    che avanza a ogni transizione distingue le due sessioni."""
    scenario = """
let release;
let delivered = false;
const held = new Promise((resolve) => { release = resolve; });
const realFetch = globalThis.fetch;
let first = true;
globalThis.fetch = async (url, options) => {
  if (first && url.startsWith('/api/core/tasks')) {
    first = false;
    __calls.push({ url, options });
    await held;
    delivered = true;
    return { status: 200, ok: true, async json() {
      return { items: [{ id: 1, title: 'TASK-DI-AGENZIA-A', status: 'open',
                         priority: 'high', due_at: '2030-01-01T10:00:00Z' }] };
    } };
  }
  return realFetch(url, options);
};
__script([{ status: 200, body: { items: [] } }, { status: 200, body: { items: [] } },
          { status: 200, body: { items: [] } }, { status: 200, body: { items: [] } }]);
window.location.hash = '#/oggi';
window._fire('hashchange');
await __settle(6);

__script([{ status: 204 }]);
__dom.byId['logout-btn'].dispatch('click');
await __settle(25);

// Entra un operatore dell'agenzia B. Il boot della sua sessione ridisegna.
__script([{ status: 204 }, { status: 200, body: ME_OTHER_LIVE },
          { status: 200, body: { items: [] } }, { status: 200, body: { items: [] } },
          { status: 200, body: { items: [] } }, { status: 200, body: { items: [] } }]);
__dom.byId['login-email'].value = 'b@example.test';
__dom.byId['login-password'].value = 'pw';
__dom.byId['login-form'].dispatch('submit');
await __settle(40);
const authenticated = __dom.byId['app-view'].hidden === false;

release();
await __settle(60);

console.log(JSON.stringify({
  content: __dom.byId['content'].visibleText(),
  appHidden: __dom.byId['app-view'].hidden,
  authenticated,
  delivered,
}));
""".replace("ME_OTHER_LIVE", json.dumps(ME_OTHER))
    out = run_shell(staged, scenario, BOOTED)
    assert out["authenticated"], "il secondo operatore doveva risultare autenticato"
    assert out["delivered"], "la corsa non e' stata esercitata"
    assert out["appHidden"] is False
    assert "TASK-DI-AGENZIA-A" not in out["content"], (
        "un dato dell'agenzia A e' comparso nella sessione di un operatore "
        "dell'agenzia B"
    )


def test_12_logout_clears_the_global_search_panel(staged):
    """La ricerca globale vive fuori dal container delle view: il router non la
    vede, e query e risultati sopravvivevano al logout.

    La precondizione e' asserita: se il pannello non avesse mai mostrato nulla,
    "dopo il logout non c'e' nulla" sarebbe vero e insignificante.
    """
    scenario = """
// Il boot rende gia' la vista "Oggi", che emette quattro richieste proprie:
// vanno lasciate finire, altrimenti consumano lo script destinato alla ricerca
// e il pannello mostra "non disponibile" invece dei risultati.
await __settle(60);
await new Promise((r) => setTimeout(r, 20));
await __settle(60);

const panel = __dom.main.children.find((c) => c !== __dom.byId['content']);
const input = panel.querySelector('input');
if (!input) { console.log(JSON.stringify({ error: 'input non trovato' })); process.exit(0); }
// Sei risposte: searchGlobal ne emette tre di base piu' quelle dei lead, e
// una sola mancante fa comparire "ricerca non disponibile" al posto dei
// risultati - cioe' una precondizione fallita travestita da esito.
__script([
  { status: 200, body: { items: [{ id: 1, display_name: 'ROSSI-DI-AGENZIA-A',
                                   email: 'r@a.test', status: 'active' }] } },
  { status: 200, body: { items: [] } },
  { status: 200, body: { items: [] } },
  { status: 200, body: { items: [] } },
  { status: 200, body: { items: [] } },
  { status: 200, body: { items: [] } },
]);
input.value = 'rossi';
input.dispatch('input');
await new Promise((r) => setTimeout(r, 500));
await __settle(40);
const before = panel.visibleText();
const inputBefore = input.value;

__script([{ status: 204 }]);
__dom.byId['logout-btn'].dispatch('click');
await __settle(30);

console.log(JSON.stringify({
  before, inputBefore,
  after: panel.visibleText(),
  inputAfter: input.value,
}));
"""
    out = run_shell(staged, scenario, BOOTED)
    assert out.get("error") is None, out.get("error")
    assert "ROSSI-DI-AGENZIA-A" in out["before"], (
        "il pannello non ha mai mostrato un risultato: la prova sarebbe vuota"
    )
    assert out["inputBefore"] == "rossi"
    assert out["inputAfter"] == "", "la query digitata e' sopravvissuta al logout"
    assert "ROSSI-DI-AGENZIA-A" not in out["after"], (
        "i risultati di ricerca dell'agenzia precedente sono ancora nel pannello"
    )


# ---------------------------------------------------------------------------
# Le regole gia' certificate in P26-3, riverificate a livello di Shell
# ---------------------------------------------------------------------------

def test_13_no_request_from_the_shell_carries_an_authorization_header(staged):
    scenario = """
__script([{ status: 200, body: { items: [] } }, { status: 200, body: { items: [] } }]);
window.location.hash = '#/contatti';
await window._fire('hashchange');
await __settle(30);
""" + REPORT
    out = run_shell(staged, scenario, BOOTED)
    for header in out["headers"]:
        assert "authorization" not in header.lower()
        assert "Basic" not in header


def test_14_every_request_the_shell_makes_sends_the_cookie(staged):
    scenario = """
__script([{ status: 200, body: { items: [] } }, { status: 200, body: { items: [] } }]);
window.location.hash = '#/contatti';
await window._fire('hashchange');
await __settle(30);
""" + REPORT
    out = run_shell(staged, scenario, BOOTED)
    assert out["credentials"], "nessuna richiesta registrata"
    assert set(out["credentials"]) == {"include"}


def test_15_no_request_names_an_agency(staged):
    """L'agenzia la decide il server dalla sessione. Il browser non la conosce
    e non la puo' chiedere."""
    scenario = """
__script([{ status: 200, body: { items: [] } }, { status: 200, body: { items: [] } }]);
window.location.hash = '#/contatti';
await window._fire('hashchange');
await __settle(30);
""" + REPORT
    out = run_shell(staged, scenario, BOOTED)
    for url in out["urls"]:
        assert "agency" not in url.lower()
    for body in out["bodies"]:
        if body:
            assert "agency" not in body.lower()


def test_16_navigation_works_after_a_restore(staged):
    """Il boot ripristina la sessione e la navigazione funziona subito, senza
    un login intermedio."""
    scenario = """
__script([{ status: 200, body: { items: [{ id: 4, title: 'IMMOBILE-X' }] } },
          { status: 200, body: { items: [] } }, { status: 200, body: { items: [] } }]);
window.location.hash = '#/immobili';
await window._fire('hashchange');
await __settle(40);
""" + REPORT
    out = run_shell(staged, scenario, BOOTED)
    assert out["appHidden"] is False
    assert out["title"] == "Immobili"
    assert any(u.startswith("/api/property") for u in out["urls"])


def test_17_a_401_does_not_start_a_login_loop(staged):
    """Un 401 riporta al login e si ferma li'. Non ritenta, non rifa' la login,
    non richiama /me: e' cosi' che questi client entrano in ciclo."""
    scenario = """
__script([{ status: 401, body: { detail: 'Non autorizzato' } }]);
window.location.hash = '#/contatti';
await window._fire('hashchange');
await __settle(60);
""" + REPORT
    out = run_shell(staged, scenario, BOOTED)
    after_boot = out["urls"][1:]
    assert all(not u.startswith("/api/operator-auth/login") for u in after_boot)
    # /me viene chiesto una volta sola, al boot.
    assert out["urls"].count("/api/operator-auth/me") == 1
    assert out["appHidden"] is True


def test_18_the_harness_can_fail(staged):
    """Se lo stub non potesse fallire, ogni PASS qui sopra non direbbe nulla."""
    scenario = """
try {
  localStorage.setItem('x', 'y');
  console.log(JSON.stringify({ trapped: false }));
} catch (error) {
  console.log(JSON.stringify({ trapped: String(error.message) }));
}
"""
    out = run_shell(staged, scenario, ANONYMOUS)
    assert out["trapped"] and "localStorage" in out["trapped"]
