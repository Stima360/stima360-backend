"""P26-5 - i cinque frontend amministrativi legacy, ESEGUITI.

CHE COSA E' CAMBIATO

CORE, PROPERTY, BUY, MATCH e FLOW autenticavano con HTTP Basic: `/api/admin/check`
verificava la coppia, poi username e password restavano in una variabile e ogni
richiesta li rimetteva in un header `Authorization`. Cinque copie della stessa
routine, e quando P26-3 ha spostato la OS Shell sulla sessione operatore questi
sono rimasti indietro senza che nulla lo segnalasse.

Adesso i cinque condividono `static/shared/operator-session.js`: la password
parte una volta verso `/api/operator-auth/login`, il token vive in un cookie
HttpOnly che il codice di pagina non puo' leggere, e nessuno di questi file
costruisce piu' un header di autorizzazione.

PERCHE' ESEGUIRLI E NON LEGGERLI

Un controllo statico sa dire che la stringa "Authorization" non compare. Non sa
dire se la password finisce in un altro posto, se un 401 innesca un ciclo, o se
il modulo tocca `localStorage` per una via indiretta. Qui i file girano davvero
in node, dentro un contesto `vm` con un DOM minimo e `fetch` scriptato, e le
trappole SOLLEVANO se qualcuno tocca uno storage del browser.

I cinque `app.js` sono script classici, non moduli: vengono eseguiti con
`vm.runInContext` esattamente come li serve il browser, preceduti dallo script
condiviso, senza riscrivere una riga.

Le prove che questo file NON fa: non e' un browser, quindi non dice nulla sul
rendering; e non tocca la rete, quindi non dice nulla su un cookie reale che
attraversa un edge. Quella parte e' della certificazione live.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
SHARED = STATIC / "shared" / "operator-session.js"

# I cinque, col percorso del proprio app.js e la funzione che il boot chiama.
FRONTENDS = {
    "core_admin": STATIC / "core_admin" / "assets" / "app.js",
    "property_admin": STATIC / "property_admin" / "assets" / "app.js",
    "buy_admin": STATIC / "buy_admin" / "assets" / "app.js",
    "match_admin": STATIC / "match_admin" / "assets" / "app.js",
    "flow_admin": STATIC / "flow_admin" / "assets" / "app.js",
}

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(
    NODE is None,
    reason=(
        "node non e' disponibile: le prove di runtime dei frontend legacy non "
        "sono state eseguite. Va riportato come BLOCKED, mai come PASS."
    ),
)


HARNESS = r"""
'use strict';
// Un 401 dentro una catena che nessuno attende produce una unhandledRejection.
// E' il normale percorso d'errore di questi frontend, non un difetto: qui
// verrebbe scambiata per un crash del driver e nasconderebbe l'esito vero.
process.on('unhandledRejection', () => {});
const vm = require('vm');
const fs = require('fs');

// --- DOM minimo -----------------------------------------------------------
class El {
  constructor(tag) {
    this.tagName = String(tag || 'div').toUpperCase();
    this.children = []; this.parentNode = null; this.attributes = {};
    this.dataset = {}; this.style = {}; this.hidden = false; this.disabled = false;
    this.value = ''; this.id = ''; this._text = ''; this._html = '';
    this.listeners = {};
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
  set className(v) { this.classList._set = new Set(String(v).split(/\s+/)); }
  get className() { return [...this.classList._set].join(' '); }
  set textContent(v) { this._text = String(v ?? ''); this.children = []; }
  get textContent() { return this._text || this.children.map((c) => c.textContent).join(''); }
  set innerHTML(v) { this._html = String(v ?? ''); }
  get innerHTML() { return this._html; }
  appendChild(c) { c.parentNode = this; this.children.push(c); return c; }
  append(...n) { for (const x of n) this.appendChild(x); }
  prepend(...n) { for (const x of n.reverse()) this.children.unshift(x); }
  replaceChildren(...n) { this.children = []; this._html = ''; this.append(...n); }
  remove() {}
  setAttribute(k, v) { this.attributes[k] = String(v); }
  getAttribute(k) { return this.attributes[k] ?? null; }
  addEventListener(t, fn) { (this.listeners[t] ||= []).push(fn); }
  removeEventListener() {}
  dispatch(t, e = {}) {
    for (const fn of this.listeners[t] || []) fn({ preventDefault() {}, target: this, ...e });
  }
  // Il DOM di questo harness serve all'autenticazione, non al rendering: una
  // query restituisce un nodo segnaposto stabile invece di null, cosi' il
  // codice di UI dei cinque frontend gira fino in fondo senza che si debba
  // riscrivere. Le prove qui non guardano cosa viene disegnato.
  querySelector(selector) {
    const key = String(selector);
    return (this._q ||= {})[key] || ((this._q[key] = new El('div')));
  }
  querySelectorAll() { return []; }
  closest() { return null; }
  contains() { return false; }
  reset() { this.value = ''; }
  focus() {} showModal() {} close() {}
}

const nodes = {};
function node(id) { return (nodes[id] ||= Object.assign(new El('div'), { id })); }

// --- fetch scriptato ------------------------------------------------------
const calls = [];
let scripted = [];
function reply(spec) {
  return {
    status: spec.status,
    ok: spec.status >= 200 && spec.status < 300,
    statusText: '',
    async json() { if (spec.body === undefined) throw new Error('nessun corpo'); return spec.body; },
    async text() { return JSON.stringify(spec.body ?? ''); },
  };
}

// --- trappole -------------------------------------------------------------
function trap(name) {
  const boom = () => { throw new Error('FORBIDDEN: il frontend ha toccato ' + name); };
  return new Proxy({}, { get: boom, set: boom, has: boom, deleteProperty: boom });
}

const sandbox = {
  console,
  setTimeout, clearTimeout, setInterval, clearInterval,
  TextEncoder, URLSearchParams, Promise, JSON, Math, Date, Number, String,
  Object, Array, Error, RegExp, Map, Set, isNaN, parseInt, parseFloat,
  encodeURIComponent, decodeURIComponent, FormData: class { constructor() {} },
  localStorage: trap('localStorage'),
  sessionStorage: trap('sessionStorage'),
  indexedDB: trap('indexedDB'),
  fetch: async (url, options = {}) => {
    calls.push({ url: String(url), options: options || {} });
    const next = scripted.shift();
    if (!next) return reply({ status: 500, body: { detail: 'non previsto' } });
    if (next.throw) throw new Error('rete non raggiungibile');
    return reply(next);
  },
  __calls: calls,
  // La proiezione che /me restituisce per l'operatore di prova. Costante del
  // sandbox e non una sostituzione di stringa fatta a mano in ogni scenario:
  // dimenticarne una produceva un ReferenceError travestito da fallimento.
  ME_LIVE: {
    user_id: 3, agency_id: 7, agency_name: 'Agenzia A', role: 'agent',
    is_platform_admin: false, expires_at: '2030-01-01T00:00:00Z',
  },
  __script: (entries) => { scripted = entries.slice(); },
  __node: node,
  __settle: async (rounds = 30) => {
    for (let i = 0; i < rounds; i += 1) await Promise.resolve();
    await new Promise((r) => setTimeout(r, 0));
    for (let i = 0; i < rounds; i += 1) await Promise.resolve();
  },
};

const document = {
  getElementById: (id) => node(id),
  querySelector: (sel) => node(String(sel).replace(/^[#.]/, '')),
  querySelectorAll: () => [],
  createElement: (tag) => new El(tag),
  addEventListener() {},
  body: new El('body'),
};
Object.defineProperty(document, 'cookie', {
  get() { throw new Error('FORBIDDEN: il frontend ha letto document.cookie'); },
});
sandbox.document = document;
sandbox.window = sandbox;
sandbox.location = { hash: '', search: '', hostname: 'test.local', pathname: '/' };
sandbox.window.location = sandbox.location;
sandbox.globalThis = sandbox;

vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sandbox, { filename: 'operator-session.js' });
// Il boot di ogni frontend parte al caricamento di app.js: le sue risposte
// devono essere gia' in coda, altrimenti la `/me` iniziale pesca il 500 di
// default e ogni scenario osserva uno stato che non e' quello che intendeva.
scripted = JSON.parse(process.argv[5] || '[]');
vm.runInContext(fs.readFileSync(process.argv[3], 'utf8'), sandbox, { filename: 'app.js' });
vm.runInContext(
  '(async () => {' + fs.readFileSync(process.argv[4], 'utf8') + '})()',
  sandbox,
  { filename: 'scenario.js' },
).then(() => {}, (error) => { console.error(error && error.stack); process.exit(1); });
"""


def run_app(app: str, scenario: str, tmp_path: Path, before_boot=None) -> dict:
    """Esegue lo script condiviso + l'app.js del frontend, poi lo scenario.

    `before_boot` sono le risposte gia' in coda quando app.js parte: il boot di
    ciascun frontend chiede `/me` immediatamente, e senza questo osserverebbe
    un 500 al posto della risposta che lo scenario intende dargli.
    """
    harness = tmp_path / "harness.cjs"
    harness.write_text(HARNESS, encoding="utf-8")
    script = tmp_path / "scenario.js"
    script.write_text(scenario, encoding="utf-8")
    result = subprocess.run(
        [NODE, str(harness), str(SHARED), str(FRONTENDS[app]), str(script),
         json.dumps(before_boot if before_boot is not None else [])],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"{app}: il driver node e' fallito ({result.returncode})\n"
            f"{result.stderr[-2500:]}\n--- stdout ---\n{result.stdout[-1500:]}"
        )
    lines = [l for l in result.stdout.strip().splitlines() if l.startswith("{")]
    assert lines, f"{app}: lo scenario non ha prodotto un risultato\n{result.stdout[-1500:]}"
    return json.loads(lines[-1])


ME = {
    "user_id": 3, "agency_id": 7, "agency_name": "Agenzia A", "role": "agent",
    "is_platform_admin": False, "expires_at": "2030-01-01T00:00:00Z",
}

# Risposte gia' in coda quando app.js parte.
ANONYMOUS = [{"status": 401, "body": {"detail": "Non autorizzato"}}]
LIVE_BOOT = [{"status": 200, "body": ME}] + [
    {"status": 200, "body": {"items": [], "next_visits": [], "recent_properties": []}}
    for _ in range(24)
]

REPORT = """
console.log(JSON.stringify({
  urls: __calls.map((c) => c.url),
  methods: __calls.map((c) => c.options.method || 'GET'),
  credentials: __calls.map((c) => c.options.credentials || null),
  headers: __calls.map((c) => JSON.stringify(c.options.headers || {})),
  bodies: __calls.map((c) => c.options.body || null),
  loginHidden: __node('login-view').hidden,
  appHidden: __node('app-view').hidden,
  status: __node('login-status').textContent,
}));
"""

EVERY = pytest.mark.parametrize("app", sorted(FRONTENDS))


# ---------------------------------------------------------------------------
# Bootstrap e ripristino
# ---------------------------------------------------------------------------

@EVERY
def test_1_the_boot_asks_me_and_sends_the_cookie(app, tmp_path):
    """Il cookie e' HttpOnly: solo il server sa se una sessione e' viva, e il
    frontend glielo chiede all'avvio invece di mostrare subito il login."""
    scenario = """
await __settle();
""" + REPORT
    out = run_app(app, scenario, tmp_path, before_boot=ANONYMOUS)
    assert out["urls"][0] == "/api/operator-auth/me"
    assert out["methods"][0] == "GET"
    assert out["credentials"][0] == "include"


@EVERY
def test_2_an_anonymous_boot_lands_on_the_login_without_an_error(app, tmp_path):
    scenario = """
await __settle();
""" + REPORT
    out = run_app(app, scenario, tmp_path, before_boot=ANONYMOUS)
    assert out["loginHidden"] is False and out["appHidden"] is True
    assert out["status"] == ""


@EVERY
def test_3_a_live_cookie_restores_the_session(app, tmp_path):
    """Il guadagno visibile della migrazione: dopo un refresh l'operatore e'
    ancora dentro. Con la password in memoria era impossibile."""
    scenario = """
await __settle(80);
""" + REPORT
    out = run_app(app, scenario, tmp_path, before_boot=LIVE_BOOT)
    assert out["appHidden"] is False and out["loginHidden"] is True


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

@EVERY
def test_4_login_posts_to_operator_auth_and_then_asks_me(app, tmp_path):
    scenario = """
await __settle();
__calls.length = 0;
__script([{ status: 204 }, { status: 200, body: ME_LIVE },
          { status: 200, body: { items: [] } }, { status: 200, body: { items: [] } },
          { status: 200, body: { items: [] } }, { status: 200, body: { items: [] } },
          { status: 200, body: { items: [] } }, { status: 200, body: { items: [] } }]);
__node('admin-username').value = 'operatore@example.test';
__node('admin-password').value = 'una-password';
__node('login-form').dispatch('submit');
await __settle(60);
""" + REPORT
    out = run_app(app, scenario, tmp_path, before_boot=LIVE_BOOT)
    assert out["urls"][0] == "/api/operator-auth/login"
    assert out["methods"][0] == "POST"
    assert out["urls"][1] == "/api/operator-auth/me"
    assert json.loads(out["bodies"][0]) == {
        "email": "operatore@example.test", "password": "una-password",
    }
    assert out["appHidden"] is False


@EVERY
def test_5_a_rejected_login_stays_on_the_login_and_does_not_retry(app, tmp_path):
    scenario = """
await __settle();
__calls.length = 0;
__script([{ status: 401, body: { detail: 'Credenziali non valide.' } }]);
__node('admin-username').value = 'x@y.test';
__node('admin-password').value = 'sbagliata';
__node('login-form').dispatch('submit');
await __settle(40);
""" + REPORT
    out = run_app(app, scenario, tmp_path, before_boot=ANONYMOUS)
    assert out["appHidden"] is True and out["loginHidden"] is False
    assert "Credenziali" in out["status"]
    assert out["urls"].count("/api/operator-auth/login") == 1, "ha ritentato la login"


@EVERY
def test_6_the_password_never_leaves_the_login_call(app, tmp_path):
    """Una volta sola, nel corpo, e in nessun header e in nessuna URL."""
    scenario = """
await __settle();
__calls.length = 0;
__script([{ status: 204 }, { status: 200, body: ME_LIVE },
          { status: 200, body: { items: [] } }, { status: 200, body: { items: [] } },
          { status: 200, body: { items: [] } }, { status: 200, body: { items: [] } },
          { status: 200, body: { items: [] } }, { status: 200, body: { items: [] } }]);
__node('admin-username').value = 'a@b.test';
__node('admin-password').value = 'PAROLADORDINE';
__node('login-form').dispatch('submit');
await __settle(60);
""" + REPORT
    out = run_app(app, scenario, tmp_path, before_boot=LIVE_BOOT)
    for url in out["urls"]:
        assert "PAROLADORDINE" not in url
    for header in out["headers"]:
        assert "PAROLADORDINE" not in header
    carrying = [b for b in out["bodies"] if b and "PAROLADORDINE" in b]
    assert len(carrying) == 1, f"la password compare in {len(carrying)} richieste"


# ---------------------------------------------------------------------------
# Il canale Basic e' sparito
# ---------------------------------------------------------------------------

@EVERY
def test_7_no_request_carries_an_authorization_header(app, tmp_path):
    scenario = """
await __settle(60);
""" + REPORT
    out = run_app(app, scenario, tmp_path, before_boot=LIVE_BOOT)
    assert out["headers"], "nessuna richiesta registrata"
    for header in out["headers"]:
        assert "authorization" not in header.lower()
        assert "Basic" not in header


@EVERY
def test_8_admin_check_is_never_called_again(app, tmp_path):
    """`/api/admin/check` era il primo passo del vecchio login. Nessuno dei
    cinque lo chiama piu': se ricomparisse, sarebbe il Basic che rientra."""
    scenario = """
await __settle(60);
__script([{ status: 204 }, { status: 200, body: ME_LIVE },
          { status: 200, body: { items: [] } }, { status: 200, body: { items: [] } },
          { status: 200, body: { items: [] } }, { status: 200, body: { items: [] } }]);
__node('admin-username').value = 'a@b.test';
__node('admin-password').value = 'pw';
__node('login-form').dispatch('submit');
await __settle(60);
""" + REPORT
    out = run_app(app, scenario, tmp_path, before_boot=LIVE_BOOT)
    assert all("/api/admin/check" not in url for url in out["urls"])


@EVERY
def test_9_every_request_sends_the_cookie(app, tmp_path):
    scenario = """
await __settle(60);
""" + REPORT
    out = run_app(app, scenario, tmp_path, before_boot=LIVE_BOOT)
    assert set(out["credentials"]) == {"include"}, out["credentials"]


@EVERY
def test_10_no_request_names_an_agency(app, tmp_path):
    """L'agenzia la decide il server dalla sessione: il browser non la conosce
    e non ha modo di chiederne un'altra."""
    scenario = """
await __settle(60);
""" + REPORT
    out = run_app(app, scenario, tmp_path, before_boot=LIVE_BOOT)
    for url in out["urls"]:
        assert "agency" not in url.lower()
    for body in out["bodies"]:
        if body:
            assert "agency" not in body.lower()


# ---------------------------------------------------------------------------
# Logout, 401, sessione revocata
# ---------------------------------------------------------------------------

@EVERY
def test_11_logout_revokes_server_side(app, tmp_path):
    """Azzerare lo stato locale lasciava un cookie valido sul server: adesso il
    logout lo revoca davvero."""
    scenario = """
await __settle(60);
__calls.length = 0;
__script([{ status: 204 }]);
__node('logout-btn').dispatch('click');
await __settle(40);
""" + REPORT
    out = run_app(app, scenario, tmp_path, before_boot=LIVE_BOOT)
    assert out["urls"] == ["/api/operator-auth/logout"], out["urls"]
    assert out["methods"] == ["POST"]
    assert out["appHidden"] is True and out["loginHidden"] is False


@EVERY
def test_12_a_401_returns_to_the_login_without_a_retry_or_a_loop(app, tmp_path):
    """Un 401 riporta al login e si ferma. Non ritenta, non rifa' la login e non
    richiede `/me`: e' cosi' che questi client entrano in ciclo."""
    scenario = """
await __settle(60);
__calls.length = 0;
// La sessione muore: ogni richiesta successiva torna 401.
__script([{ status: 401, body: { detail: 'Non autorizzato' } },
          { status: 401, body: { detail: 'Non autorizzato' } },
          { status: 401, body: { detail: 'Non autorizzato' } }]);
try { await REFRESH; } catch (_ignored) { /* il 401 rilancia, ed e' giusto */ }
await __settle(60);
""".replace("REFRESH", _refresh_call("APP")) + REPORT
    out = run_app(app, scenario.replace("APP", app), tmp_path)
    assert all("/api/operator-auth/login" not in u for u in out["urls"])
    assert all("/api/operator-auth/me" not in u for u in out["urls"])
    assert out["appHidden"] is True, "il 401 non ha riportato al login"


@EVERY
def test_13_no_frontend_touches_browser_storage(app, tmp_path):
    """Le trappole SOLLEVANO: se un frontend toccasse localStorage,
    sessionStorage, indexedDB o document.cookie, il driver morirebbe qui."""
    scenario = """
__script([{ status: 204 }, { status: 200, body: ME_LIVE },
          { status: 200, body: { items: [] } }, { status: 200, body: { items: [] } },
          { status: 200, body: { items: [] } }, { status: 200, body: { items: [] } },
          { status: 200, body: { items: [] } }, { status: 200, body: { items: [] } }]);
__node('admin-username').value = 'a@b.test';
__node('admin-password').value = 'pw';
__node('login-form').dispatch('submit');
await __settle(60);
__script([{ status: 204 }]);
__node('logout-btn').dispatch('click');
await __settle(40);
""" + REPORT
    out = run_app(app, scenario, tmp_path, before_boot=LIVE_BOOT)
    assert out["urls"], "lo scenario non ha esercitato nulla"


@EVERY
def test_14_no_password_survives_anywhere_reachable(app, tmp_path):
    """Cammina il contesto dopo la login e cerca la password in ogni valore
    raggiungibile. La vecchia versione la teneva in `credentials`."""
    scenario = """
__script([{ status: 204 }, { status: 200, body: ME_LIVE },
          { status: 200, body: { items: [] } }, { status: 200, body: { items: [] } },
          { status: 200, body: { items: [] } }, { status: 200, body: { items: [] } },
          { status: 200, body: { items: [] } }, { status: 200, body: { items: [] } }]);
__node('admin-username').value = 'a@b.test';
__node('admin-password').value = 'PAROLADORDINE';
__node('login-form').dispatch('submit');
await __settle(60);

const seen = new Set();
const found = [];
(function walk(value, path, depth) {
  if (depth > 4 || value === null || value === undefined) return;
  if (typeof value === 'string') {
    // `global.__calls` e' il registro di QUESTO harness: contiene per forza il
    // corpo della login. Cio' che si cerca qui e' la password nello stato
    // dell'applicazione, che e' dove il vecchio `credentials` la teneva.
    if (value.includes('PAROLADORDINE')
        && !path.startsWith('global.__calls')
        && !path.startsWith('global.__node')) found.push(path);
    return;
  }
  if (typeof value !== 'object' && typeof value !== 'function') return;
  if (seen.has(value)) return;
  seen.add(value);
  let keys = [];
  try { keys = Object.keys(value); } catch (_e) { return; }
  for (const key of keys) {
    let child;
    try { child = value[key]; } catch (_e) { continue; }
    walk(child, path + '.' + key, depth + 1);
  }
})(globalThis, 'global', 0);

console.log(JSON.stringify({ found, urls: __calls.map((c) => c.url) }));
"""
    out = run_app(app, scenario, tmp_path, before_boot=LIVE_BOOT)
    # Il campo del form e' l'unico posto ammesso: e' il DOM, non lo stato del
    # modulo, e viene svuotato dal reset del form al logout.
    leftovers = [p for p in out["found"] if "admin-password" not in p]
    assert leftovers == [], f"la password sopravvive in: {leftovers}"


@EVERY
def test_14b_a_401_clears_the_shared_session_state(app, tmp_path):
    """Non basta che la UI torni al login: lo STATO del modulo condiviso deve
    essere azzerato.

    Sono due cose diverse, e la differenza si vede solo mutando il codice: una
    versione che mostra il login ma tiene `session` popolata continuerebbe a
    rispondere `isAuthenticated() === true` a chiunque lo chieda, e il
    ripristino successivo salterebbe la verifica col server. Le prove che
    guardavano solo `app-view.hidden` lasciavano passare esattamente questo.
    """
    scenario = """
await __settle(60);
if (!OperatorSession.isAuthenticated()) { console.log(JSON.stringify({ error: 'boot non autenticato' })); }
__script([{ status: 401, body: { detail: 'Non autorizzato' } }]);
try { await OperatorSession.authFetch('/api/core/contacts'); } catch (_ignored) {}
await __settle(40);
console.log(JSON.stringify({
  authenticated: OperatorSession.isAuthenticated(),
  session: OperatorSession.getSession(),
}));
"""
    out = run_app(app, scenario, tmp_path, before_boot=LIVE_BOOT)
    assert out.get("error") is None, out.get("error")
    assert out["authenticated"] is False
    assert out["session"] is None


@EVERY
def test_14c_repeated_401s_notify_once(app, tmp_path):
    """`sessionExpired` e' idempotente. Senza, ogni richiesta in volo che torna
    401 ridisegna la schermata di login, e un pannello con sei chiamate
    parallele ne farebbe sei."""
    scenario = """
await __settle(60);
let notifications = 0;
OperatorSession.onAuthChange(() => { notifications += 1; });
__script([{ status: 401 }, { status: 401 }, { status: 401 }]);
for (let i = 0; i < 3; i += 1) {
  try { await OperatorSession.authFetch('/api/core/contacts'); } catch (_ignored) {}
}
await __settle(40);
console.log(JSON.stringify({ notifications }));
"""
    out = run_app(app, scenario, tmp_path, before_boot=LIVE_BOOT)
    assert out["notifications"] == 1, (
        f"tre 401 hanno prodotto {out['notifications']} notifiche"
    )


@EVERY
def test_14d_a_401_from_me_leaves_no_session(app, tmp_path):
    """Il ripristino con un cookie morto: `restore()` deve azzerare, non
    lasciare in piedi quello di prima."""
    scenario = """
await __settle(60);
const before = OperatorSession.isAuthenticated();
__script([{ status: 401, body: { detail: 'Non autorizzato' } }]);
const restored = await OperatorSession.restore();
await __settle(20);
console.log(JSON.stringify({
  before, restored, authenticated: OperatorSession.isAuthenticated(),
}));
"""
    out = run_app(app, scenario, tmp_path, before_boot=LIVE_BOOT)
    assert out["before"] is True, "lo scenario doveva partire autenticato"
    assert out["restored"] is None
    assert out["authenticated"] is False


@EVERY
def test_14e_requests_carry_exactly_the_expected_headers(app, tmp_path):
    """Enumerati, non solo "niente Authorization".

    Un divieto su un nome solo non vede l'header successivo che qualcuno
    aggiunge - e una credenziale puo' viaggiare sotto qualunque nome.
    """
    scenario = """
await __settle(80);
console.log(JSON.stringify({
  headers: __calls.map((c) => Object.keys(c.options.headers || {}).sort()),
  urls: __calls.map((c) => c.url),
}));
"""
    out = run_app(app, scenario, tmp_path, before_boot=LIVE_BOOT)
    assert out["headers"], "nessuna richiesta registrata"
    for names, url in zip(out["headers"], out["urls"]):
        assert names in ([], ["Content-Type"]), (
            f"{url} porta header inattesi: {names}"
        )


def test_15_the_harness_can_actually_fail(tmp_path):
    """Senza questo, i PASS qui sopra non direbbero nulla."""
    scenario = """
try {
  localStorage.setItem('x', 'y');
  console.log(JSON.stringify({ trapped: false }));
} catch (error) {
  console.log(JSON.stringify({ trapped: String(error.message) }));
}
"""
    out = run_app("core_admin", scenario, tmp_path)
    assert out["trapped"] and "localStorage" in out["trapped"]


def test_16_all_five_frontends_share_one_implementation():
    """Cinque copie di una regola di sicurezza sono cinque posti dove
    sbagliarla - ed e' esattamente cosi' che questi cinque sono rimasti
    indietro quando P26-3 ha spostato la OS Shell."""
    for app, path in FRONTENDS.items():
        source = path.read_text(encoding="utf-8")
        assert "OperatorSession." in source, app
        for forbidden in ("encodeBasic", "btoa(", "/api/admin/check"):
            assert forbidden not in source, f"{app} contiene ancora {forbidden!r}"

    for app in FRONTENDS:
        html = (STATIC / app / "index.html").read_text(encoding="utf-8")
        assert "/shared/operator-session.js" in html, app


def _refresh_call(app_placeholder: str) -> str:
    """La funzione che ogni frontend usa per ricaricare i propri dati."""
    return (
        "({"
        " core_admin: () => refresh(),"
        " property_admin: () => refresh(),"
        " buy_admin: () => dashboard(),"
        " match_admin: () => applyDeepLink(),"
        " flow_admin: () => dashboard(),"
        f"}})['{app_placeholder}']()"
    )
