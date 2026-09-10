"""P26-3 - the OS Shell auth modules, EXECUTED.

Everything in tests/test_p26_3_client_auth_transition.py reads the shipped
JavaScript. This file runs it. The distinction matters: a static check proves
that `credentials: 'include'` appears in the source, and this one proves that
the request the browser would make actually carries it.

HOW, WITHOUT ADDING A DEPENDENCY

There is no package.json and no node_modules in this repository, and the one
JavaScript harness it already has (tests/test_owner_07_p7.py) drives a classic
script through node's `vm` module with hand-written DOM fakes. The OS Shell's
auth modules are ES modules, which `vm.runInNewContext` will not load, so this
uses node's own ESM loader instead: the two files are copied to a temporary
directory with an `.mjs` extension - the only edit is the import specifier that
follows the rename - and a driver imports them and asserts on a `fetch` stub.

Node is the runtime, not a library: nothing is installed.

WHAT IS RUN, AND WHAT IS NOT

Run: auth.js and api-client.js, in full - login, restore, logout,
sessionExpired, and all four request helpers.

Not run: main.js. It imports the router, the env badge and every view, and
needs a DOM; standing that up would be a second harness the size of the one in
P26-7. Its boot wiring - that `restore()` is called and that the login view is
what the page starts on - stays a static assertion in
test_p26_3_client_auth_transition.py, and is named there as one.

A missing node is reported as BLOCKED. It is never a pass.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "static" / "os_shell" / "assets" / "core"

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(
    NODE is None,
    reason=(
        "node is not on PATH - the OS Shell auth modules were not executed. "
        "Report this as BLOCKED, never as PASS."
    ),
)


def _stage(tmp_path: Path) -> Path:
    """Copy the two modules as .mjs so node's ESM loader accepts them.

    The only change is the specifier that has to follow the rename. Asserted
    rather than assumed: if api-client.js ever imports something else, this
    stops silently copying a module that no longer resolves.
    """
    auth = (CORE / "auth.js").read_text(encoding="utf-8")
    client = (CORE / "api-client.js").read_text(encoding="utf-8")

    assert client.count("from './auth.js'") == 1, "api-client's import shape changed"
    client = client.replace("from './auth.js'", "from './auth.mjs'")

    (tmp_path / "auth.mjs").write_text(auth, encoding="utf-8")
    (tmp_path / "api-client.mjs").write_text(client, encoding="utf-8")
    return tmp_path


PRELUDE = """
// --- the environment the modules are entitled to expect --------------------
//
// `fetch` is the only thing they touch. Everything a browser would also offer
// is installed as a TRAP: touching localStorage, sessionStorage, indexedDB or
// document.cookie throws, so "the modules store no credential" is enforced by
// the run rather than asserted about the text.
const calls = [];
let scripted = [];

function trap(name) {
  const boom = () => { throw new Error('FORBIDDEN: the module touched ' + name); };
  return new Proxy({}, { get: boom, set: boom, has: boom, deleteProperty: boom });
}
globalThis.localStorage = trap('localStorage');
globalThis.sessionStorage = trap('sessionStorage');
globalThis.indexedDB = trap('indexedDB');
globalThis.document = new Proxy({}, {
  get(_target, property) {
    if (property === 'cookie') throw new Error('FORBIDDEN: the module read document.cookie');
    return undefined;
  },
});

globalThis.fetch = async (url, options = {}) => {
  calls.push({ url, options: JSON.parse(JSON.stringify(options ?? {})) });
  if (scripted.length === 0) throw new Error('unscripted fetch to ' + url);
  const next = scripted.shift();
  if (next.networkError) throw new TypeError('network down');
  return {
    ok: next.status >= 200 && next.status < 300,
    status: next.status,
    json: async () => next.body ?? null,
  };
};

function script(...responses) { scripted = responses; calls.length = 0; }
function assert(value, message) { if (!value) throw new Error(message || 'assertion failed'); }
function last() { return calls[calls.length - 1]; }

const SESSION_A = { user_id: 3, agency_id: 7, agency_name: 'Agenzia A', role: 'agency_owner',
                    is_platform_admin: false, expires_at: '2030-01-01T00:00:00Z' };
"""


def _run(tmp_path: Path, body: str) -> str:
    driver = tmp_path / "driver.mjs"
    driver.write_text(
        PRELUDE
        + "\nimport * as auth from './auth.mjs';\n"
        + "import * as api from './api-client.mjs';\n"
        + textwrap.dedent(body),
        encoding="utf-8",
    )
    finished = subprocess.run(
        [NODE, str(driver)], capture_output=True, text=True, timeout=60, cwd=tmp_path
    )
    if finished.returncode != 0:
        raise AssertionError(
            "node exited " + str(finished.returncode) + "\n"
            + finished.stdout + "\n" + finished.stderr
        )
    return finished.stdout


@pytest.fixture
def shell(tmp_path):
    staged = _stage(tmp_path)

    def run(body):
        return _run(staged, body)

    return run


# ---------------------------------------------------------------------------
# 1-3 - login
# ---------------------------------------------------------------------------

def test_1_login_sends_the_password_only_in_the_body(shell):
    shell("""
    script({ status: 204 }, { status: 200, body: SESSION_A });
    await auth.login('mario@agenzia.test', 'sup3r-s3cret');

    const login = calls[0];
    assert(login.url === '/api/operator-auth/login', login.url);
    assert(login.options.method === 'POST', 'login must be a POST');

    // The password is in the body and NOWHERE else.
    const body = JSON.parse(login.options.body);
    assert(body.password === 'sup3r-s3cret', 'password missing from the body');
    assert(body.email === 'mario@agenzia.test', 'email missing from the body');

    const headers = JSON.stringify(login.options.headers ?? {});
    assert(!headers.includes('sup3r-s3cret'), 'password leaked into a header');
    assert(!headers.toLowerCase().includes('authorization'), 'an Authorization header was built');
    assert(!login.url.includes('sup3r-s3cret'), 'password leaked into the URL');
    """)


def test_2_login_sends_the_cookie_option_and_then_asks_me(shell):
    shell("""
    script({ status: 204 }, { status: 200, body: SESSION_A });
    const session = await auth.login('mario@agenzia.test', 'pw');

    assert(calls[0].options.credentials === 'include', 'login did not include credentials');
    assert(calls[1].url === '/api/operator-auth/me', 'login did not ask /me: ' + calls[1].url);
    assert(calls[1].options.credentials === 'include', '/me did not include credentials');

    // The identity comes from /me, never from the login response.
    assert(session.agency_id === 7, JSON.stringify(session));
    assert(auth.isAuthenticated() === true);
    assert(auth.getSession().user_id === 3);
    """)


def test_3_invalid_credentials_leave_no_session(shell):
    shell("""
    script({ status: 401 });
    let raised = null;
    try { await auth.login('mario@agenzia.test', 'wrong'); }
    catch (error) { raised = error; }

    assert(raised !== null, 'a 401 login resolved instead of throwing');
    assert(raised.message === 'Credenziali non valide.', raised.message);
    assert(auth.isAuthenticated() === false, 'a failed login left a session');
    assert(calls.length === 1, 'a failed login retried: ' + calls.length + ' calls');
    """)


# ---------------------------------------------------------------------------
# 4-5 - nothing is kept
# ---------------------------------------------------------------------------

def test_4_no_credential_survives_the_login_call(shell):
    """The storage traps are live for the whole run: reaching any of them
    throws inside node and this fails. What is left is the module's own
    exported surface, walked for the password."""
    shell("""
    script({ status: 204 }, { status: 200, body: SESSION_A });
    await auth.login('mario@agenzia.test', 'sup3r-s3cret');

    const exported = JSON.stringify(
      Object.fromEntries(
        Object.keys(auth)
          .filter((key) => typeof auth[key] !== 'function')
          .map((key) => [key, auth[key]])
      )
    );
    assert(!exported.includes('sup3r-s3cret'), 'the password is reachable: ' + exported);

    // And what IS kept is only the /me projection - no secret in it.
    const session = auth.getSession();
    assert(!('password' in session), 'the session carries a password');
    assert(!('token' in session), 'the session carries a token');
    assert(Object.keys(session).sort().join(',') ===
           'agency_id,agency_name,expires_at,is_platform_admin,role,user_id',
           Object.keys(session).sort().join(','));
    """)


def test_5_the_modules_never_touch_browser_storage(shell):
    """Enforced by the traps, and exercised across every entry point rather
    than asserted about one of them."""
    shell("""
    script({ status: 204 }, { status: 200, body: SESSION_A });
    await auth.login('a@b.test', 'pw');

    script({ status: 200, body: { items: [] } });
    await api.apiGet('/api/core/contacts');

    script({ status: 200, body: SESSION_A });
    await auth.restore();

    script({ status: 204 });
    await auth.logout();

    auth.sessionExpired();
    // Reaching any storage would have thrown before we got here.
    """)


# ---------------------------------------------------------------------------
# 6-7 - restore
# ---------------------------------------------------------------------------

def test_6_restore_reads_the_session_from_me(shell):
    shell("""
    script({ status: 200, body: SESSION_A });
    const session = await auth.restore();

    assert(calls.length === 1, 'restore made ' + calls.length + ' calls');
    assert(calls[0].url === '/api/operator-auth/me', calls[0].url);
    assert(calls[0].options.method === 'GET', calls[0].options.method);
    assert(calls[0].options.credentials === 'include');
    assert(session.agency_id === 7);
    assert(auth.isAuthenticated() === true);
    """)


def test_7_restore_treats_401_as_no_session_not_as_an_error(shell):
    """A cold browser has no cookie. That is the normal case, not a failure to
    show the user - and it must not retry."""
    shell("""
    script({ status: 401 });
    const session = await auth.restore();

    assert(session === null, 'a 401 /me produced a session');
    assert(auth.isAuthenticated() === false);
    assert(calls.length === 1, 'restore retried after a 401');
    """)


# ---------------------------------------------------------------------------
# 8-9 - logout
# ---------------------------------------------------------------------------

def test_8_logout_calls_the_backend_then_clears(shell):
    shell("""
    script({ status: 200, body: SESSION_A });
    await auth.restore();
    assert(auth.isAuthenticated() === true);

    script({ status: 204 });
    await auth.logout();

    assert(calls.length === 1, 'logout made ' + calls.length + ' calls');
    assert(calls[0].url === '/api/operator-auth/logout', calls[0].url);
    assert(calls[0].options.method === 'POST');
    assert(calls[0].options.credentials === 'include');
    assert(auth.isAuthenticated() === false, 'logout left a session behind');
    """)


def test_9_logout_clears_even_if_the_server_is_unreachable(shell):
    """Leaving the user in front of a UI that looks active because the network
    blipped is the wrong answer."""
    shell("""
    script({ status: 200, body: SESSION_A });
    await auth.restore();

    script({ networkError: true });
    await auth.logout();

    assert(auth.isAuthenticated() === false, 'a failed logout kept the session');
    """)


# ---------------------------------------------------------------------------
# 10-12 - the 401 path
# ---------------------------------------------------------------------------

def test_10_a_401_clears_the_session_and_does_not_retry(shell):
    shell("""
    script({ status: 200, body: SESSION_A });
    await auth.restore();
    assert(auth.isAuthenticated() === true);

    script({ status: 401 });
    let raised = null;
    try { await api.apiGet('/api/core/contacts'); }
    catch (error) { raised = error; }

    assert(raised !== null, 'a 401 resolved instead of throwing');
    assert(raised.status === 401, raised.status);
    assert(auth.isAuthenticated() === false, 'the 401 left the session in place');

    // ONE request. No refresh, no re-login, no second attempt.
    assert(calls.length === 1, 'the 401 was retried: ' + calls.length + ' calls');
    """)


def test_11_repeated_401s_do_not_compound(shell):
    """`sessionExpired` is idempotent, so a burst of parallel requests failing
    together clears once and notifies once."""
    shell("""
    script({ status: 200, body: SESSION_A });
    await auth.restore();

    let notifications = 0;
    auth.onAuthChange(() => { notifications += 1; });

    script({ status: 401 }, { status: 401 }, { status: 401 });
    for (const _ of [1, 2, 3]) {
      try { await api.apiGet('/api/core/contacts'); } catch (_error) { /* expected */ }
    }

    assert(calls.length === 3, 'requests were retried: ' + calls.length);
    assert(notifications === 1, 'sessionExpired fired ' + notifications + ' times');
    assert(auth.isAuthenticated() === false);
    """)


def test_12_the_401_handler_makes_no_request_of_its_own(shell):
    shell("""
    script({ status: 200, body: SESSION_A });
    await auth.restore();

    script({ status: 401 });
    try { await api.apiGet('/api/core/contacts'); } catch (_error) { /* expected */ }

    const afterTheFailure = calls.slice(1);
    assert(afterTheFailure.length === 0,
           'the 401 handler called ' + JSON.stringify(afterTheFailure.map((c) => c.url)));
    """)


# ---------------------------------------------------------------------------
# 13-15 - every request
# ---------------------------------------------------------------------------

def test_13_every_helper_sends_the_cookie(shell):
    shell("""
    const helpers = [
      ['apiGet',    () => api.apiGet('/api/core/contacts')],
      ['apiPost',   () => api.apiPost('/api/core/contacts', { display_name: 'x' })],
      ['apiPatch',  () => api.apiPatch('/api/core/contacts/1', { display_name: 'y' })],
      ['apiDelete', () => api.apiDelete('/api/core/contacts/1')],
    ];
    for (const [name, call] of helpers) {
      script({ status: 200, body: {} });
      await call();
      assert(last().options.credentials === 'include', name + ' did not include credentials');
    }
    """)


def test_14_no_request_carries_an_authorization_header(shell):
    shell("""
    script({ status: 204 }, { status: 200, body: SESSION_A });
    await auth.login('a@b.test', 'pw');
    script({ status: 200, body: {} });
    await api.apiGet('/api/property/properties');
    script({ status: 200, body: {} });
    await api.apiPost('/api/buy/requests', { contact_id: 1 });

    for (const call of calls) {
      const headers = Object.keys(call.options.headers ?? {}).map((k) => k.toLowerCase());
      assert(!headers.includes('authorization'),
             'Authorization sent to ' + call.url);
    }
    """)


def test_15_no_request_carries_an_agency_from_the_client(shell):
    """The agency is the server's decision. Nothing the Shell sends may name
    one - not in a URL, not in a body, not in a header."""
    shell("""
    script({ status: 204 }, { status: 200, body: SESSION_A });
    await auth.login('a@b.test', 'pw');

    const requests = [
      () => api.apiGet('/api/core/contacts'),
      () => api.apiPost('/api/core/contacts', { display_name: 'x', email: 'x@y.test' }),
      () => api.apiPatch('/api/property/properties/1', { title: 't' }),
      () => api.apiDelete('/api/buy/requests/1'),
    ];
    for (const request of requests) {
      script({ status: 200, body: {} });
      await request();
    }

    for (const call of calls) {
      const whole = call.url + ' ' + JSON.stringify(call.options.headers ?? {})
                              + ' ' + String(call.options.body ?? '');
      assert(!/agency/i.test(whole), 'a request named an agency: ' + whole);
    }
    """)


# ---------------------------------------------------------------------------
# 16 - the harness itself
# ---------------------------------------------------------------------------

def test_16_the_traps_and_the_stub_actually_fire(shell):
    """A harness that cannot fail proves nothing. Both halves are exercised:
    the storage trap throws, and an unscripted request throws."""
    shell("""
    let trapped = false;
    try { globalThis.localStorage.token = 'x'; } catch (error) { trapped = true; }
    assert(trapped, 'the localStorage trap did not fire');

    let unscripted = false;
    script();
    try { await api.apiGet('/api/core/contacts'); }
    catch (error) { unscripted = true; }
    assert(unscripted, 'an unscripted request was answered');
    """)
