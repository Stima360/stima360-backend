"""P26-3 - the OS Shell moves from legacy Basic to the operator session.

WHAT CHANGED, IN ONE PARAGRAPH

The Shell used to keep the operator's username and password in a module
variable and rebuild `Authorization: Basic` on every request. It now posts them
once to `/api/operator-auth/login`, keeps nothing, and lets the browser carry
an HttpOnly cookie the page cannot read. The routers it calls moved from
`require_admin` to `require_authenticated_operator`, which verifies the SAME
legacy credential through the same `admin_security.require_admin` and, in
addition, admits the cookie. Nothing that authenticated before stops
authenticating; what is added is a channel, and what is removed is a password
living in the browser for a whole working day.

THE FOUR DEPENDENCIES, AND WHY THERE ARE FOUR

    require_operator                 CORE's mount. Authenticates AND yields the
                                     scope its routes read, so one callable
                                     serves both and FastAPI caches it once.

    require_authenticated_operator   every other mount. Admits and returns
                                     None. It exists because mounting those
                                     routers on `require_operator` would have
                                     resolved the Default Agency twice per
                                     legacy request - once for the mount, once
                                     for the route's own scope dependency,
                                     which are two different callables and
                                     therefore two cache entries.

    legacy_basic_agency_context      the scope, on roughly a hundred and fifty
                                     routes. P26-3 changed what it does - a
                                     live session wins over the shared
                                     credential - and deliberately not its
                                     name, which is retired in P26-5 with the
                                     channel it describes.

    basic_only_agency_context        the same scope, for OWNER Admin, whose
                                     mount admits HTTP Basic and nothing else.
                                     It is what the one above was before P26-3.
                                     Tests 30-35 are why it exists: a surface
                                     admitted by one credential must not be
                                     scoped by another.

WHAT THIS FILE DOES NOT PROVE

No PostgreSQL, so nothing here observes a real session row expiring or being
revoked; those are modelled at the boundary `optional_session` reads. And no
browser: the frontend assertions read the shipped JavaScript, they do not run
it.
"""
from __future__ import annotations

import ast
import inspect
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from operator_auth import dependencies as deps
from operator_auth.context import OperatorContext


ROOT = Path(__file__).resolve().parents[1]
SHELL = ROOT / "static" / "os_shell"
AUTH_JS = SHELL / "assets" / "core" / "auth.js"
API_CLIENT_JS = SHELL / "assets" / "core" / "api-client.js"
MAIN_JS = SHELL / "assets" / "main.js"
INDEX_HTML = SHELL / "index.html"

DEFAULT_AGENCY_ID = 1
AGENCY_A = 7
AGENCY_B = 8

BASIC = ("giorgio", "test-secret")
COOKIE = "stima360_operator_session"


def _session(agency_id=AGENCY_A, user_id=3, role="agency_owner"):
    return {
        "context": OperatorContext(
            user_id=user_id, agency_id=agency_id, role=role,
            is_platform_admin=False, session_id=99,
            auth_channel="operator_session",
        ),
        "agency_name": "Agenzia A",
        "expires_at": datetime(2030, 1, 1, tzinfo=timezone.utc),
    }


@pytest.fixture
def wired(monkeypatch):
    """A scoped route mounted exactly as main.py mounts them, without a database.

    One route, taking the shared scope dependency, behind the admission-only
    mount. That is the shape every OS Shell router now has, so what this proves
    about one of them it proves about the arrangement.
    """
    monkeypatch.setenv("ADMIN_USER", BASIC[0])
    monkeypatch.setenv("ADMIN_PASS", BASIC[1])

    state = {"session": None, "default_agency_lookups": 0, "ctx": None}

    monkeypatch.setattr(deps.service, "session_from_token",
                        lambda token: state["session"])

    class _Cursor:
        def execute(self, sql, params=None):
            state["default_agency_lookups"] += 1

        def fetchone(self):
            return {"id": DEFAULT_AGENCY_ID}

    @contextmanager
    def _cursor(*args, **kwargs):
        yield (None, _Cursor())

    monkeypatch.setattr(deps, "operator_cursor", _cursor)

    from fastapi import APIRouter

    router = APIRouter(prefix="/api/probe")

    @router.get("/scope")
    def scope(ctx: OperatorContext = Depends(deps.legacy_basic_agency_context)):
        state["ctx"] = ctx
        return {
            "agency_id": ctx.agency_id,
            "user_id": ctx.user_id,
            "auth_channel": ctx.auth_channel,
            "is_platform_admin": ctx.is_platform_admin,
        }

    app = FastAPI()
    app.include_router(router, dependencies=[Depends(deps.require_authenticated_operator)])
    return TestClient(app, raise_server_exceptions=False), state


# ---------------------------------------------------------------------------
# 1-4 - the cookie authenticates, and it wins
# ---------------------------------------------------------------------------

def test_1_a_live_session_cookie_authenticates_and_carries_its_own_agency(wired):
    client, state = wired
    state["session"] = _session(agency_id=AGENCY_A)

    response = client.get("/api/probe/scope", cookies={COOKIE: "live"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["agency_id"] == AGENCY_A
    assert body["user_id"] == 3
    assert body["auth_channel"] == "operator_session"


def test_2_the_session_wins_over_basic_when_both_are_present(wired):
    """An operator who also sends Basic must not be silently downgraded to the
    shared credential's Default-Agency scope."""
    client, state = wired
    state["session"] = _session(agency_id=AGENCY_A)

    response = client.get("/api/probe/scope", cookies={COOKIE: "live"}, auth=BASIC)
    assert response.status_code == 200
    assert response.json()["agency_id"] == AGENCY_A
    assert response.json()["auth_channel"] == "operator_session"
    assert state["default_agency_lookups"] == 0, (
        "the Default Agency was resolved even though a session was present"
    )


def test_3_the_legacy_basic_channel_still_works_unchanged(wired):
    """P26-5 confines or removes it. Until then it must not have moved."""
    client, state = wired
    state["session"] = None

    response = client.get("/api/probe/scope", auth=BASIC)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["agency_id"] == DEFAULT_AGENCY_ID
    assert body["auth_channel"] == "legacy_basic"
    assert body["user_id"] is None
    assert body["is_platform_admin"] is False


def test_4_the_default_agency_is_resolved_once_per_legacy_request(wired):
    """The reason `require_authenticated_operator` exists.

    Mounting these routers on `require_operator` would have made the mount and
    the route's scope dependency two separate cache entries, each resolving the
    Default Agency. One lookup, not two.
    """
    client, state = wired
    state["session"] = None

    client.get("/api/probe/scope", auth=BASIC)
    assert state["default_agency_lookups"] == 1, state["default_agency_lookups"]


# ---------------------------------------------------------------------------
# 5-8 - the cookie that is not good enough
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cookies", [
    pytest.param({}, id="no cookie"),
    pytest.param({COOKIE: "expired-or-revoked"}, id="cookie the server refuses"),
])
def test_5_a_request_without_a_usable_session_and_without_basic_is_401(wired, cookies):
    """`optional_session` returns None for absent, unknown, revoked, expired
    and idle-timed-out alike - the boundary this models - and none of them may
    fall through to anything but a refusal."""
    client, state = wired
    state["session"] = None

    response = client.get("/api/probe/scope", cookies=cookies)
    assert response.status_code == 401, response.text


def test_6_a_dead_cookie_does_not_fall_through_to_valid_basic(wired):
    """A refused session must mean refused, in the request that carried it.

    Revocation has to survive the fact that the same browser may still hold
    ADMIN_USER and ADMIN_PASS. If it did not, logging an operator out - or
    disabling their account, or suspending their membership - would leave them
    working through the shared credential, on the very request whose cookie the
    server had just rejected.

    What distinguishes the two cases is the presence of a cookie, not the
    reason it failed: `optional_session` still returns a bare None for absent,
    unknown, revoked, expired, idle, disabled and de-membered alike, so nothing
    here can be used as an oracle for which of those it was.
    """
    client, state = wired
    state["session"] = None

    response = client.get("/api/probe/scope", cookies={COOKIE: "stale"}, auth=BASIC)
    assert response.status_code == 401
    assert response.json()["detail"] == "Non autorizzato"


def test_6b_basic_without_a_cookie_is_untouched(wired):
    """The other half of test_6, and the reason it costs nothing.

    P26-1 refused to fail a dead cookie because it feared locking out a
    legitimate Basic client. A Basic client sends no cookie, so it is not
    affected - and this is that claim as an executed assertion rather than an
    argument. The six legacy admin pages and every script on that channel keep
    working until P26-5 removes it.
    """
    client, state = wired
    state["session"] = None

    response = client.get("/api/probe/scope", auth=BASIC)
    assert response.status_code == 200
    assert response.json()["auth_channel"] == "legacy_basic"


def test_6c_an_empty_cookie_is_not_a_presented_session(wired):
    """A cookie header sent with an empty value is no cookie at all.

    Worth pinning: had the check been `COOKIE in request.cookies` rather than a
    truth test on the value, a client that had just been logged out - the
    server clears the cookie by setting it empty - could have been refused
    Basic as well, on a cookie the server itself blanked.
    """
    client, state = wired
    state["session"] = None

    response = client.get("/api/probe/scope", cookies={COOKIE: ""}, auth=BASIC)
    assert response.status_code == 200
    assert response.json()["auth_channel"] == "legacy_basic"


def test_7_wrong_basic_and_no_session_is_401(wired):
    client, state = wired
    state["session"] = None

    response = client.get("/api/probe/scope", auth=("giorgio", "wrong"))
    assert response.status_code == 401


def test_8_an_unconfigured_server_still_answers_503_not_401(wired, monkeypatch):
    """P26-1 answered 503 when ADMIN_USER/ADMIN_PASS were absent, because that
    is an operational fault rather than a failed login. Both dependencies keep
    that answer - a route that said 503 must not start saying 401."""
    client, state = wired
    state["session"] = None
    monkeypatch.delenv("ADMIN_USER", raising=False)
    monkeypatch.delenv("ADMIN_PASS", raising=False)

    response = client.get("/api/probe/scope")
    assert response.status_code == 503, response.text


# ---------------------------------------------------------------------------
# 9-11 - the client cannot choose an agency
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("params", [
    {"agency_id": AGENCY_B},
    {"agency": AGENCY_B},
    {"agency_id": DEFAULT_AGENCY_ID},
])
def test_9_a_session_for_agency_a_cannot_ask_for_agency_b(wired, params):
    client, state = wired
    state["session"] = _session(agency_id=AGENCY_A)

    response = client.get("/api/probe/scope", params=params, cookies={COOKIE: "live"})
    assert response.status_code == 200
    assert response.json()["agency_id"] == AGENCY_A, params


def test_10_a_header_cannot_select_an_agency_either(wired):
    client, state = wired
    state["session"] = _session(agency_id=AGENCY_A)

    response = client.get(
        "/api/probe/scope",
        cookies={COOKIE: "live"},
        headers={"X-Agency-Id": str(AGENCY_B), "X-Agency": str(AGENCY_B)},
    )
    assert response.status_code == 200
    assert response.json()["agency_id"] == AGENCY_A


def test_11_neither_dependency_reads_anything_from_the_request(wired):
    for function in (deps.legacy_basic_agency_context,
                     deps.require_authenticated_operator,
                     deps.require_operator,
                     deps._scope_from_session_or_basic):
        source = inspect.getsource(function)
        body = source.split('"""')[-1]
        assert not re.search(r"request\.(query_params|headers|json|cookies)", body), function
        assert "agency_id=" not in body.replace("agency_id=None", ""), function


# ---------------------------------------------------------------------------
# 12-15 - the OS Shell no longer holds a credential
# ---------------------------------------------------------------------------

def test_12_the_shell_builds_no_authorization_header_anywhere():
    """The whole point. A page that cannot build the header cannot leak it."""
    for path in sorted(SHELL.rglob("*.js")) + [INDEX_HTML]:
        text = path.read_text(encoding="utf-8")
        code = "\n".join(
            line for line in text.splitlines() if not line.strip().startswith("//")
        )
        assert "btoa(" not in code, path
        assert "Authorization" not in code, path
        assert "Basic " not in code, path


def test_13_the_shell_stores_no_credential_in_the_browser():
    """localStorage, sessionStorage, IndexedDB and document.cookie - none of
    them, and not only for the password: the session token is HttpOnly
    precisely so that no page script can put it anywhere."""
    for path in sorted(SHELL.rglob("*.js")) + [INDEX_HTML]:
        code = "\n".join(
            line for line in path.read_text(encoding="utf-8").splitlines()
            if not line.strip().startswith("//")
        )
        for forbidden in ("localStorage", "sessionStorage", "indexedDB", "document.cookie"):
            assert forbidden not in code, f"{path.name} uses {forbidden}"


def test_14_the_password_never_leaves_the_login_call():
    """It is read from the form, passed to `login`, and goes out of scope.

    Asserted on the module that would have to hold it: no assignment of a
    password to anything that outlives the call, and no `credentials` module
    state of the kind the Basic client kept.
    """
    source = AUTH_JS.read_text(encoding="utf-8")
    # Strip both comment styles: the JSDoc blocks explain the rule and would
    # otherwise be read as if they broke it.
    code = "\n".join(
        line for line in source.splitlines()
        if not line.strip().startswith(("//", "*", "/*"))
    )
    assert "let credentials" not in code
    assert "getCredentials" not in code
    # `password` appears exactly where it must: the parameter and the body it
    # is serialised into. Nowhere else.
    password_lines = [line.strip() for line in code.splitlines() if "password" in line]
    assert password_lines == [
        "export async function login(email, password) {",
        "body: JSON.stringify({ email, password }),",
    ], password_lines


def _code(path):
    """The file with its comments removed.

    Every assertion about what the Shell DOES has to read this rather than the
    raw text: these files explain themselves at length, and a comment saying
    `credentials: 'include'` would otherwise satisfy a test about sending the
    cookie while the fetch below it did not.
    """
    return "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith(("//", "*", "/*"))
    )


def test_15_every_shell_request_sends_the_cookie():
    """Without `credentials: 'include'` the browser sends no cookie and accepts
    no Set-Cookie, and the whole transition silently does nothing."""
    client = _code(API_CLIENT_JS)
    assert "credentials: 'include'" in client
    # One fetch in the client, and it is the one that carries the option.
    assert client.count("fetch(") == 1, client.count("fetch(")
    fetch_call = client[client.index("fetch("):]
    assert "credentials: 'include'" in fetch_call[:fetch_call.index(")") + 1], fetch_call[:200]

    auth = _code(AUTH_JS)
    assert "credentials: 'include'" in auth
    assert auth.count("fetch(") == 1, "every auth call must go through one fetch"
    # In auth.js the option is applied by a wrapper, so assert the wrapper is
    # what the single fetch actually receives.
    assert "withCookie(options)" in auth, auth


# ---------------------------------------------------------------------------
# 16-19 - login, restore, logout, and no 401 loop
# ---------------------------------------------------------------------------

def test_16_the_shell_logs_in_through_operator_auth():
    auth = AUTH_JS.read_text(encoding="utf-8")
    assert "/api/operator-auth/login" in auth
    assert "/api/admin/check" not in auth, "the legacy check endpoint is gone"


def test_17_the_shell_restores_its_session_from_me():
    """The cookie is HttpOnly, so only the server can say whether a session is
    live. Booting straight to the login screen and asking /me is the only
    correct answer - and it is what makes a refresh keep the session."""
    auth = AUTH_JS.read_text(encoding="utf-8")
    assert "/api/operator-auth/me" in auth
    assert "export async function restore()" in auth

    main = MAIN_JS.read_text(encoding="utf-8")
    assert "restore()" in main
    assert "loginView.hidden = false;" in main, "boot must start from the login view"


def test_18_logout_revokes_on_the_server():
    """Clearing local state alone would leave a live cookie behind."""
    auth = AUTH_JS.read_text(encoding="utf-8")
    assert "/api/operator-auth/logout" in auth
    logout = auth[auth.index("export async function logout()"):]
    assert "method: 'POST'" in logout
    assert "session = null" in logout


def test_19_a_401_returns_to_login_without_a_loop():
    """The API client must not answer a 401 by authenticating again - that is
    exactly how these clients end up in a login/401/login cycle. It clears the
    session and lets the view react."""
    client = API_CLIENT_JS.read_text(encoding="utf-8")
    assert "sessionExpired()" in client
    assert "login(" not in client
    assert "restore(" not in client

    auth = AUTH_JS.read_text(encoding="utf-8")
    expired = auth[auth.index("export function sessionExpired()"):]
    assert "fetch(" not in expired, "sessionExpired must not make a request"
    assert "if (session === null) return;" in expired, "must be idempotent"


# ---------------------------------------------------------------------------
# 20-23 - the widening of D-1, stated and bounded
# ---------------------------------------------------------------------------

OS_SHELL_ROUTERS = (
    "property_router", "buy_router", "match_router", "crm_router",
    "proposal_router", "sale_router", "seller_intelligence_router",
    "followup_router", "seller_intent_router", "property_watch_router",
    "next_best_action_router",
)


def _mounts():
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    found = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "include_router"
                and node.args):
            dependencies = ""
            for keyword in node.keywords:
                if keyword.arg == "dependencies":
                    dependencies = ast.unparse(keyword.value)
            found[ast.unparse(node.args[0])] = dependencies
    return found


@pytest.mark.parametrize("router", OS_SHELL_ROUTERS)
def test_20_every_router_the_shell_calls_admits_the_session(router):
    assert _mounts()[router] == "[Depends(require_authenticated_operator)]", router


def test_21_core_keeps_the_dependency_that_also_yields_its_scope():
    assert _mounts()["core_router"] == "[Depends(require_operator)]"


def test_22_the_owner_routers_are_not_part_of_the_widening():
    """The Shell does not call them; the portal authenticates owners rather
    than operators; and OWNER Admin keeps `require_owner_admin`, which is what
    P26-6C's admission proof reads."""
    mounts = _mounts()
    assert mounts["owner_admin_router"] == ""
    assert mounts["owner_portal_router"] == ""
    admin = (ROOT / "owner" / "router_admin.py").read_text(encoding="utf-8")
    assert "dependencies=[Depends(require_owner_admin)]" in admin


def test_23_flow_no_longer_borrows_owners_dependency():
    """It could not: `require_owner_admin` is HTTP Basic and nothing else, and
    the Shell calls /api/flow with a cookie."""
    flow = (ROOT / "flow" / "router.py").read_text(encoding="utf-8")
    assert "require_owner_admin" not in flow
    assert "dependencies=[Depends(require_authenticated_operator)]" in flow


# ---------------------------------------------------------------------------
# 24-26 - what must not have moved
# ---------------------------------------------------------------------------

def test_24_the_legacy_admin_frontends_still_use_basic():
    """They are P26-5's problem, not P26-3's. If this ever starts failing
    because they migrated too, that is fine - but it must be a decision."""
    still_basic = []
    for index in sorted(ROOT.glob("static/*_admin/assets/app.js")):
        text = index.read_text(encoding="utf-8")
        if "encodeBasic" in text or "Authorization" in text:
            still_basic.append(index.parent.parent.name)
    assert still_basic, "no legacy admin frontend uses Basic any more - is that intended?"


def test_25_the_credential_checker_itself_is_untouched():
    """`admin_security.require_admin` is the single definition of what the
    legacy credential is, and P26-3 does not redefine it - it reuses it."""
    source = (ROOT / "admin_security.py").read_text(encoding="utf-8")
    assert "def require_admin(" in source
    assert "operator" not in source
    assert "session" not in source

    for name in ("require_authenticated_operator", "_verify_legacy_credentials"):
        body = inspect.getsource(getattr(deps, name))
        assert "require_admin" in body, name


def test_26_the_owner_portal_cookie_is_still_a_different_one():
    """Two principals, two cookies. A shared name would let one overwrite the
    other's session."""
    from operator_auth.enums import COOKIE_NAME as OPERATOR_COOKIE
    from owner.enums import COOKIE_NAME as OWNER_COOKIE

    assert OPERATOR_COOKIE != OWNER_COOKIE
    assert OPERATOR_COOKIE == COOKIE


# ---------------------------------------------------------------------------
# 27-28 - the mount authenticates on its own
# ---------------------------------------------------------------------------

@pytest.fixture
def bare_mount(monkeypatch):
    """A route with NO scope dependency, behind the admission-only mount.

    Every other test in this file exercises a scoped route, where the route's
    own dependency also authenticates - so removing the mount's guard would go
    unnoticed. Those routers do contain routes that take no context, and for
    them the mount is the only thing standing between an anonymous caller and
    the handler.
    """
    monkeypatch.setenv("ADMIN_USER", BASIC[0])
    monkeypatch.setenv("ADMIN_PASS", BASIC[1])
    state = {"session": None, "reached": 0}

    monkeypatch.setattr(deps.service, "session_from_token", lambda token: state["session"])

    from fastapi import APIRouter

    router = APIRouter(prefix="/api/bare")

    @router.get("/ping")
    def ping():
        state["reached"] += 1
        return {"ok": True}

    app = FastAPI()
    app.include_router(router, dependencies=[Depends(deps.require_authenticated_operator)])
    return TestClient(app, raise_server_exceptions=False), state


def test_27_the_mount_alone_refuses_an_anonymous_caller(bare_mount):
    client, state = bare_mount
    response = client.get("/api/bare/ping")
    assert response.status_code == 401, response.text
    assert state["reached"] == 0, "the handler ran without a credential"


def test_28_the_mount_alone_admits_both_channels(bare_mount):
    client, state = bare_mount

    assert client.get("/api/bare/ping", auth=BASIC).status_code == 200
    state["session"] = _session(agency_id=AGENCY_A)
    assert client.get("/api/bare/ping", cookies={COOKIE: "live"}).status_code == 200
    assert state["reached"] == 2


def test_29_the_mount_alone_refuses_a_dead_cookie_that_also_sent_basic(bare_mount):
    """Admission, on its own, must not launder a revoked session into Basic.

    Test 6 proves this for the scope dependency, and would pass even if the
    mount had lost the rule, because that route also declares the scope. This
    route declares nothing: the mount is the only thing deciding, so the
    assertion is about the mount and not about what happens to sit behind it.
    """
    client, state = bare_mount
    state["session"] = None

    response = client.get("/api/bare/ping", cookies={COOKIE: "revoked"}, auth=BASIC)

    assert response.status_code == 401, response.text
    assert state["reached"] == 0, (
        "a revoked session was admitted through the shared credential"
    )

    # And without the dead cookie the same caller is still admitted.
    client.cookies.clear()
    assert client.get("/api/bare/ping", auth=BASIC).status_code == 200
    assert state["reached"] == 1


@pytest.fixture
def unmounted(monkeypatch, wired):
    """A route that declares the scope dependency and sits behind NO mount guard.

    This is the shape of the six `@app` routes in main.py: there is no
    router-level admission in front of them, so `legacy_basic_agency_context`
    is the whole of their authentication. Everything else in this file mounts
    the admission dependency too, which is right for those routers and hides
    exactly the mutation below - the scope dependency's own guard can be
    removed and every other test still passes, because the mount refuses first.
    """
    _, state = wired

    app = FastAPI()

    @app.get("/api/unmounted/scope")
    def scope(ctx: OperatorContext = Depends(deps.legacy_basic_agency_context)):
        state["ctx"] = ctx
        return {"agency_id": ctx.agency_id, "auth_channel": ctx.auth_channel}

    return TestClient(app, raise_server_exceptions=False), state


def test_29b_the_scope_dependency_refuses_a_dead_cookie_on_its_own(unmounted):
    """No mount to fall back on: this is the guard being tested by itself."""
    client, state = unmounted
    state["session"] = None
    state["ctx"] = None

    response = client.get("/api/unmounted/scope", cookies={COOKIE: "revoked"}, auth=BASIC)

    assert response.status_code == 401, response.text
    assert state["ctx"] is None, (
        "a revoked session was scoped through the shared credential on a route "
        "with no mount-level admission in front of it"
    )

    client.cookies.clear()
    again = client.get("/api/unmounted/scope", auth=BASIC)
    assert again.status_code == 200, again.text
    assert again.json()["auth_channel"] == "legacy_basic"


def test_29c_a_live_session_still_scopes_an_unmounted_route(unmounted):
    """The guard refuses refused cookies, not cookies."""
    client, state = unmounted
    state["session"] = _session(agency_id=AGENCY_A)

    response = client.get("/api/unmounted/scope", cookies={COOKIE: "live"})

    assert response.status_code == 200, response.text
    assert response.json() == {"agency_id": AGENCY_A, "auth_channel": "operator_session"}


# ---------------------------------------------------------------------------
# 30-35 - OWNER Admin: one surface, one channel
# ---------------------------------------------------------------------------
#
# P26-3 REVIEW. The Shell does not call OWNER Admin, so P26-3 had no reason to
# move it - and a good reason not to. `require_authenticated_operator` asks
# whether a caller is authenticated, not what they may do; admitting any
# operator session there would put owner-account management, login-token
# minting and owner documents inside reach of an agent-role session. That is a
# privilege decision, and it belongs to the phase that brings roles with it.
#
# But leaving the mount alone was not enough. OWNER Admin's routes took the
# shared scope dependency, and P26-3 made that one session-first - so a browser
# holding both a cookie and Basic would have been ADMITTED by Basic and SCOPED
# by the cookie. Not a widening and not a narrowing: two credentials deciding
# two halves of one request. `basic_only_agency_context` removes it.

OWNER_ADMIN_PROBE = "/api/owner/admin/lookups/contacts"


@pytest.fixture
def owner_admin(monkeypatch):
    """The REAL OWNER Admin router, mounted the way main.py mounts it.

    Not a probe. Every assertion below is about admission, which is decided
    before any handler runs, so no database is needed and none is reachable:
    the one cursor these paths could open is the Default-Agency lookup, and it
    is faked here exactly as in `wired`.
    """
    monkeypatch.setenv("ADMIN_USER", BASIC[0])
    monkeypatch.setenv("ADMIN_PASS", BASIC[1])

    state = {"session": None, "ctx": None, "agency_asked": None, "resolutions": 0}
    monkeypatch.setattr(deps.service, "session_from_token", lambda token: state["session"])

    real_default_context = deps._default_agency_context

    def _record(*args, **kwargs):
        state["resolutions"] += 1
        state["ctx"] = real_default_context(*args, **kwargs)
        return state["ctx"]

    monkeypatch.setattr(deps, "_default_agency_context", _record)

    class _Cursor:
        def execute(self, sql, params=None):
            pass

        def fetchone(self):
            return {"id": DEFAULT_AGENCY_ID}

    @contextmanager
    def _cursor(*args, **kwargs):
        yield (None, _Cursor())

    monkeypatch.setattr(deps, "operator_cursor", _cursor)

    # The probe route is a real one, and it runs. Only the repository call at
    # the end is faked, so the whole chain under test - mount, scope
    # dependency, `agency_of` - is the shipped code, and what it resolved is
    # read from the agency the query was asked for.
    from owner import router_admin_lookups as lookups

    def _lookup_contacts(agency_id, *args, **kwargs):
        state["agency_asked"] = agency_id
        return []

    monkeypatch.setattr(lookups.r, "lookup_contacts", _lookup_contacts)

    from owner.router_admin import router as owner_admin_router

    app = FastAPI()
    app.include_router(owner_admin_router)
    return TestClient(app, raise_server_exceptions=False), state


def test_30_an_operator_session_alone_does_not_admit_owner_admin(owner_admin):
    """The explicit proof that OWNER Admin did not join the D-1 widening."""
    client, state = owner_admin
    state["session"] = _session(agency_id=AGENCY_A)

    response = client.get(OWNER_ADMIN_PROBE, cookies={COOKIE: "a-live-token"})

    assert response.status_code == 401, response.text
    assert state["ctx"] is None, "the scope dependency ran on an unadmitted request"
    assert state["agency_asked"] is None, "an unadmitted request reached a query"
    assert response.headers["www-authenticate"] == 'Basic realm="STIMA360 OWNER Admin"'


def test_31_a_platform_admin_session_does_not_admit_owner_admin_either(owner_admin):
    """Not even the widest session. The mount does not read roles at all - it
    reads a credential - and that is the property, not a role check."""
    client, state = owner_admin
    state["session"] = _session(agency_id=AGENCY_A, role="platform_admin")

    assert client.get(OWNER_ADMIN_PROBE, cookies={COOKIE: "a-live-token"}).status_code == 401
    assert state["ctx"] is None


def test_32_basic_alone_still_admits_owner_admin(owner_admin):
    """Unchanged by P26-3, and the reason the surface stays Basic-only."""
    client, state = owner_admin

    response = client.get(OWNER_ADMIN_PROBE, auth=BASIC)

    assert response.status_code == 200, response.text
    assert state["ctx"] is not None, "Basic no longer admits OWNER Admin"
    assert state["ctx"].auth_channel == "legacy_basic"
    assert state["agency_asked"] == DEFAULT_AGENCY_ID
    # One request, one Default-Agency resolution. OWNER Admin's mount verifies
    # a credential and opens no cursor, so the scope dependency is the only
    # thing that resolves - which is the arrangement `require_authenticated_
    # operator` exists to preserve on the eleven routers that do have a guard.
    assert state["resolutions"] == 1, state["resolutions"]


def test_33_a_cookie_alongside_basic_does_not_change_the_scope(owner_admin):
    """THE HYBRID, STATED AS A TEST.

    Before this change the same request was admitted by Basic and scoped by the
    cookie: agency 7, from a credential the mount never even looked at. Now
    both halves are decided by the one credential the mount accepts.
    """
    client, state = owner_admin
    state["session"] = _session(agency_id=AGENCY_A)

    response = client.get(OWNER_ADMIN_PROBE, cookies={COOKIE: "a-live-token"}, auth=BASIC)

    assert response.status_code == 200, "Basic must still admit the request"
    assert state["agency_asked"] == DEFAULT_AGENCY_ID, (
        "the cookie decided which agency OWNER Admin queried, on a request "
        f"admitted by Basic (asked for {state['agency_asked']}, "
        f"the session's agency is {AGENCY_A})"
    )
    assert state["ctx"].auth_channel == "legacy_basic"
    assert state["ctx"].user_id is None and state["ctx"].session_id is None


def test_34_owner_admin_declares_only_the_basic_only_scope():
    """Structural, and it covers the routes tests 30-33 do not reach.

    Thirty routes and four lookups: an HTTP test per route would prove the same
    thing thirty-four times, and would still miss the thirty-fifth. The
    dependency each one names is the property.
    """
    for module in ("router_admin.py", "router_admin_lookups.py"):
        source = (ROOT / "owner" / module).read_text(encoding="utf-8")
        assert "basic_only_agency_context" in source, module
        assert "legacy_basic_agency_context" not in source, (
            f"owner/{module} takes the session-first scope while its mount "
            "accepts only Basic - that is the hybrid this section removed"
        )
        assert "require_authenticated_operator" not in source, module


def test_35_the_basic_only_scope_cannot_see_a_session_at_all():
    """Not "prefers Basic" - cannot do otherwise.

    A precedence that merely ordered the two channels would be one edit away
    from the hybrid returning. This dependency has no session parameter, so
    there is nothing for a cookie to win.
    """
    parameters = inspect.signature(deps.basic_only_agency_context).parameters
    assert list(parameters) == ["_credential"], parameters
    assert parameters["_credential"].default.dependency.__name__ == "require_admin"

    source = inspect.getsource(deps.basic_only_agency_context)
    body = source[source.index('"""', source.index('"""') + 3) + 3:]
    for forbidden in ("session", "cookie", "COOKIE_NAME", "optional_session", "request"):
        assert forbidden not in body, f"basic_only_agency_context mentions {forbidden!r}"
