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


def test_3_the_legacy_basic_channel_is_gone(wired):
    """P26-5 L'HA RIMOSSO, ed e' questo test a dirlo.

    Il nome di prima era `..._still_works_unchanged`, e la docstring diceva
    "P26-5 lo confina o lo rimuove; fino ad allora non deve essersi mosso".
    P26-5 e' arrivata: i cinque frontend amministrativi sono passati alla
    sessione operatore, quindi non resta un solo client che abbia bisogno di
    quel canale su una route di tenant.

    Il test non sparisce, si gira. Provare che una credenziale NON apre e'
    almeno importante quanto provare che ne apre un'altra: senza, la sua
    inefficacia non sarebbe scritta da nessuna parte e potrebbe rientrare in
    silenzio.
    """
    client, state = wired
    state["session"] = None

    response = client.get("/api/probe/scope", auth=BASIC)
    assert response.status_code == 401, response.text
    assert response.json()["detail"] == "Non autorizzato"


def test_4_no_default_agency_is_resolved_for_a_refused_request(wired):
    """Il rifiuto arriva prima del database.

    Il test contava le risoluzioni della Default Agency per provare che
    `require_authenticated_operator` non ne facesse due. Adesso il conto giusto
    e' zero, per una ragione piu' forte: senza il canale Basic non esiste piu'
    una Default Agency da risolvere - l'agenzia arriva dalla sessione, gia'
    pronta - e un chiamante non autenticato non fa partire alcuna query.
    """
    client, state = wired
    state["session"] = None

    client.get("/api/probe/scope", auth=BASIC)
    assert state["default_agency_lookups"] == 0, state["default_agency_lookups"]


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


def test_6b_basic_without_a_cookie_is_refused_too(wired):
    """L'altra meta' del test 6, e P26-5 la gira insieme a lui.

    P26-1 non voleva far fallire un cookie morto per timore di chiudere fuori
    un client Basic legittimo, e la risposta era: un client Basic non manda
    cookie, quindi non e' toccato. Vero allora, irrilevante adesso - quel
    client non esiste piu' su nessuna route di tenant, e il canale e' chiuso in
    entrambe le forme.

    Restano provate le due cose che contano: che il Basic non apra, e che il
    messaggio di rifiuto sia sempre lo stesso qualunque sia la causa.
    """
    client, state = wired
    state["session"] = None

    response = client.get("/api/probe/scope", auth=BASIC)
    assert response.status_code == 401
    assert response.json()["detail"] == "Non autorizzato"


def test_6c_an_empty_cookie_is_refused_like_any_other_absent_session(wired):
    """Il cookie vuoto era un caso delicato finche' esisteva un fallback.

    Il server cancella il cookie impostandolo vuoto: se la presenza fosse stata
    controllata con `COOKIE in request.cookies` invece che sul valore, un
    utente appena disconnesso si sarebbe visto negare anche il Basic, per un
    cookie che il server stesso aveva svuotato. Senza fallback quella trappola
    non esiste piu', e resta solo la regola semplice: nessuna sessione, 401.
    """
    client, state = wired
    state["session"] = None

    response = client.get("/api/probe/scope", cookies={COOKIE: ""}, auth=BASIC)
    assert response.status_code == 401


def test_7_wrong_basic_and_no_session_is_401(wired):
    client, state = wired
    state["session"] = None

    response = client.get("/api/probe/scope", auth=("giorgio", "wrong"))
    assert response.status_code == 401


def test_8_the_admin_env_no_longer_changes_this_answer(wired, monkeypatch):
    """Il 503 se ne va da qui, e non e' una perdita: e' un cambio di indirizzo.

    Significava "il server non ha credenziali amministrative configurate", ed
    era la risposta giusta finche' questa superficie viveva su quelle
    variabili: un 401 avrebbe mandato l'operatore a cercare una password
    sbagliata invece di una configurazione mancante.

    Adesso questa superficie non le legge piu'. Toglierle non cambia nulla, e
    l'unica risposta possibile per chi non ha una sessione e' 401. Il 503
    sopravvive dove sopravvive il canale - `/api/admin/check` e
    `admin_security.require_admin` che la serve - ed e' provato la'.
    """
    client, state = wired
    state["session"] = None
    monkeypatch.delenv("ADMIN_USER", raising=False)
    monkeypatch.delenv("ADMIN_PASS", raising=False)

    assert client.get("/api/probe/scope").status_code == 401
    monkeypatch.setenv("ADMIN_USER", BASIC[0])
    monkeypatch.setenv("ADMIN_PASS", BASIC[1])
    assert client.get("/api/probe/scope").status_code == 401


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
                     deps.require_owner_admin_context,
                     deps._scope_from_session):
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


def test_22_the_owner_routers_keep_their_own_admission():
    """P26-3 li tenne fuori dall'allargamento di D-1 perche' ammettere
    qualunque sessione a OWNER Admin sarebbe stata un'escalation. P26-5 non ha
    cambiato quella conclusione: l'ha resa esplicita con un ruolo.

    OWNER Admin non e' montata su `require_authenticated_operator` - che
    verifica solo che il chiamante sia autenticato - ma su
    `require_owner_admin_context`, che in piu' impone `agency_owner`. Il
    portale resta un principale diverso, con `current_owner`.
    """
    mounts = _mounts()
    assert mounts["owner_admin_router"] == ""
    assert mounts["owner_portal_router"] == ""
    admin = (ROOT / "owner" / "router_admin.py").read_text(encoding="utf-8")
    assert "dependencies=[Depends(require_owner_admin_context)]" in admin
    assert "require_authenticated_operator" not in admin


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


def test_28_the_mount_alone_admits_the_session_and_only_the_session(bare_mount):
    """Erano due canali, adesso e' uno.

    Il test si chiamava `..._admits_both_channels` e provava che il mount
    ammettesse tanto il Basic quanto il cookie. P26-5 chiude il primo: il
    Basic, da solo, non ammette piu' nulla, e la sessione resta l'unica porta.
    """
    client, state = bare_mount

    assert client.get("/api/bare/ping", auth=BASIC).status_code == 401
    assert state["reached"] == 0

    state["session"] = _session(agency_id=AGENCY_A)
    assert client.get("/api/bare/ping", cookies={COOKIE: "live"}).status_code == 200
    assert state["reached"] == 1


def test_29_a_dead_cookie_plus_basic_is_refused_by_the_mount(bare_mount):
    """La regola della revisione P26-3 sopravvive a P26-5, in forma piu' forte.

    Allora il rischio era che un cookie RIFIUTATO ricadesse sul Basic della
    stessa richiesta: revocare una sessione non avrebbe revocato l'accesso, se
    il browser ricordava anche ADMIN_USER e ADMIN_PASS. Serviva una regola
    apposta per distinguere "nessun cookie" da "cookie rifiutato".

    Adesso non serve piu' distinguerli, perche' non c'e' un secondo canale su
    cui ricadere. La prova resta, ed e' proprio quella che accorgerebbe di un
    ritorno del fallback.
    """
    client, state = bare_mount
    state["session"] = None

    response = client.get("/api/bare/ping", cookies={COOKIE: "revoked"}, auth=BASIC)

    assert response.status_code == 401, response.text
    assert state["reached"] == 0, "una sessione revocata e' passata col Basic"

    # E senza cookie il Basic non apre lo stesso: non e' il cookie morto a
    # chiudere la porta, e' che quella porta non esiste piu'.
    client.cookies.clear()
    assert client.get("/api/bare/ping", auth=BASIC).status_code == 401
    assert state["reached"] == 0


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


def test_29b_the_scope_dependency_refuses_basic_on_its_own(unmounted):
    """Le sei route `@app` di main.py non hanno guardia di mount: la
    dipendenza di scope e' tutta l'autenticazione che hanno.

    E' il posto dove un fallback dimenticato sarebbe piu' pericoloso e meno
    visibile, quindi la regola va provata qui e non solo dietro un mount che la
    coprirebbe comunque.
    """
    client, state = unmounted
    state["session"] = None
    state["ctx"] = None

    # Cookie rifiutato piu' Basic valido: rifiutato.
    assert client.get("/api/unmounted/scope", cookies={COOKIE: "revoked"},
                      auth=BASIC).status_code == 401
    assert state["ctx"] is None

    # Solo Basic: rifiutato lo stesso.
    client.cookies.clear()
    assert client.get("/api/unmounted/scope", auth=BASIC).status_code == 401
    assert state["ctx"] is None


def test_29c_a_live_session_still_scopes_an_unmounted_route(unmounted):
    """The guard refuses refused cookies, not cookies."""
    client, state = unmounted
    state["session"] = _session(agency_id=AGENCY_A)

    response = client.get("/api/unmounted/scope", cookies={COOKIE: "live"})

    assert response.status_code == 200, response.text
    assert response.json() == {"agency_id": AGENCY_A, "auth_channel": "operator_session"}


# ---------------------------------------------------------------------------
# 30-35 - OWNER Admin: una superficie, una sessione, un ruolo
# ---------------------------------------------------------------------------
#
# P26-3 lascio' OWNER Admin fuori dall'allargamento di D-1, e aveva ragione:
# `require_authenticated_operator` chiede se il chiamante e' autenticato, non
# cosa gli e' permesso, e ammettere qualunque sessione avrebbe messo la gestione
# dei conti proprietario, l'emissione dei loro token di accesso e i loro
# documenti alla portata di una sessione con ruolo agent.
#
# La conclusione non cambia con P26-5, cambia il modo di ottenerla. Restare
# Basic-only era un modo indiretto di dire "serve un privilegio piu' alto", e
# aveva il difetto di legare il privilegio a un segreto condiviso invece che a
# una persona. Adesso il privilegio e' detto: sessione, piu' ruolo
# `agency_owner` (o platform admin). Un agent riceve 403 - autenticato, non
# autorizzato - e l'agenzia arriva dalla sessione, quindi OWNER Admin diventa
# multi-agenzia insieme a tutto il resto.

OWNER_ADMIN_PROBE = "/api/owner/admin/lookups/contacts"


@pytest.fixture
def owner_admin(monkeypatch):
    """Il router OWNER Admin REALE, montato come lo monta main.py.

    Non un probe. Solo la chiamata finale al repository e' finta, cosi' la
    catena sotto esame - mount, dipendenza di ruolo, `agency_of` - e' il codice
    spedito, e l'agenzia che si osserva e' quella che la query ha ricevuto.
    """
    state = {"session": None, "agency_asked": None}
    monkeypatch.setattr(deps.service, "session_from_token", lambda token: state["session"])

    from owner import router_admin_lookups as lookups

    def _lookup_contacts(agency_id, *args, **kwargs):
        state["agency_asked"] = agency_id
        return []

    monkeypatch.setattr(lookups.r, "lookup_contacts", _lookup_contacts)

    from owner.router_admin import router as owner_admin_router

    app = FastAPI()
    app.include_router(owner_admin_router)
    return TestClient(app, raise_server_exceptions=False), state


def _live(agency_id=AGENCY_A, role="agency_owner", is_platform_admin=False):
    return {
        "context": OperatorContext(
            user_id=3, agency_id=agency_id, role=role,
            is_platform_admin=is_platform_admin, session_id=99,
            auth_channel="operator_session",
        ),
        "agency_name": "Agenzia",
        "expires_at": datetime(2030, 1, 1, tzinfo=timezone.utc),
    }


def test_30_an_anonymous_caller_is_refused_before_any_query(owner_admin):
    client, state = owner_admin

    response = client.get(OWNER_ADMIN_PROBE)

    assert response.status_code == 401, response.text
    assert state["agency_asked"] is None, "un anonimo ha raggiunto una query"


@pytest.mark.parametrize("role", ["agent", "agency_admin"])
def test_31_an_insufficient_role_gets_403_not_401(owner_admin, role):
    """403 perche' e' autenticato benissimo: non e' autorizzato.

    `agency_admin` e' incluso di proposito: la matrice gli da' "LIMITED" sui
    membri dell'agenzia, e i conti proprietario non sono membri.
    """
    client, state = owner_admin
    state["session"] = _live(role=role)

    response = client.get(OWNER_ADMIN_PROBE, cookies={COOKIE: "live"})

    assert response.status_code == 403, response.text
    assert state["agency_asked"] is None, "un ruolo insufficiente ha raggiunto una query"


def test_32_basic_no_longer_admits_owner_admin(owner_admin):
    """Prima si chiamava `..._still_admits_owner_admin`. P26-5 lo gira."""
    client, state = owner_admin

    assert client.get(OWNER_ADMIN_PROBE, auth=BASIC).status_code == 401
    assert state["agency_asked"] is None


def test_33_the_agency_comes_from_the_session_not_from_a_default(owner_admin):
    """IL GUADAGNO DI P26-5 SU QUESTA SUPERFICIE.

    Prima l'agenzia era sempre la Default, risolta dal segreto condiviso: OWNER
    Admin non poteva servire una seconda agenzia nemmeno in linea di principio.
    Adesso e' quella dell'operatore che ha fatto login, e la query lo dimostra.
    """
    client, state = owner_admin
    state["session"] = _live(agency_id=AGENCY_B, role="agency_owner")

    response = client.get(OWNER_ADMIN_PROBE, cookies={COOKIE: "live"})

    assert response.status_code == 200, response.text
    assert state["agency_asked"] == AGENCY_B, (
        f"la query ha chiesto l'agenzia {state['agency_asked']} invece di {AGENCY_B}"
    )


def test_34_owner_admin_declares_only_the_role_bearing_scope():
    """Strutturale, e copre le route che i test sopra non raggiungono.

    Trenta route piu' quattro lookup: una prova HTTP per ciascuna proverebbe
    trentaquattro volte la stessa cosa e mancherebbe comunque la
    trentacinquesima. La dipendenza che ognuna dichiara e' la proprieta'.
    """
    for module in ("router_admin.py", "router_admin_lookups.py"):
        source = (ROOT / "owner" / module).read_text(encoding="utf-8")
        assert "require_owner_admin_context" in source, module
        # Nessuna delle due dipendenze piu' deboli: la prima non guarda il
        # ruolo, la seconda e' lo scope generico degli altri router.
        assert "require_authenticated_operator" not in source, module
        assert "legacy_basic_agency_context" not in source, module


def test_35_the_owner_admin_scope_cannot_be_reached_without_a_role_check():
    """Non "preferisce" un ruolo: non puo' restituire un contesto senza averlo
    verificato. Una precedenza sarebbe a una riga di distanza dal cedere."""
    parameters = inspect.signature(deps.require_owner_admin_context).parameters
    assert list(parameters) == ["session"], parameters

    source = inspect.getsource(deps.require_owner_admin_context)
    body = source[source.index('"""', source.index('"""') + 3) + 3:]
    # Un solo `return context`, e sta dopo il controllo del ruolo.
    assert body.count("return context") == 1
    assert body.index("OWNER_ADMIN_MIN_ROLE") < body.index("return context")
    assert "403" in body or "OWNER_ADMIN_FORBIDDEN_MESSAGE" in body
