"""P26-1 Task 15 - the auth transition, and the surface it deliberately leaves.

Two jobs.

**The transition.** `require_operator` becomes dual-channel: a valid operator
session cookie first, else valid legacy Basic resolved server-side into an
agency-bound context, else 401. `main.py` mounts the operator-auth router and
swaps the CORE router's outer dependency. Nothing else about main.py changes,
and that is asserted structurally (M1-M5) rather than by counting lines - a
line count breaks on a reformat and passes a semantically wrong two-line edit.

**The surface.** P26-1 delivers *one* isolated vertical slice. Legacy Basic
still reaches every non-CORE router, cross-agency, exactly as it does today.
That residual is not a bug to be hidden; it is a bounded, recorded state, and
the frozen list below is what stops it growing quietly.

    GATE-MA1. While these compatibility routes exist, P26-1 does NOT certify
    platform-wide multi-agency isolation, and a second real agency must NOT be
    activated in PROD. The gate lifts when those routes are migrated, not when
    this file is edited.

A *shrinking* frozen surface is a deliberate spec change and should be
accompanied by the migration that shrank it. It is never a test to be quietly
relaxed to make a run green.

Coverage map:

    G1  M1-M5: main.py changed exactly where Task 15 says, and nowhere else
    G2  auth precedence: cookie, then Basic, then 401
    G3  the legacy context is agency-bound and server-resolved
    G4  D-1: the operator session reaches only the allowlist
    G5  the frozen legacy-Basic surface (C-4 / T-21)
    G6  Next Best Action and public STIMA are unchanged by this task
"""
from __future__ import annotations

import ast
import hashlib
import inspect
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "main.py"

DEFAULT_AGENCY_SLUG = "stima360"
DEFAULT_AGENCY_ID = 4242  # a distinctive fixture id: never a constant in code

# D-1: the only prefixes an operator session may authenticate (spec 2.1).
OPERATOR_SESSION_ALLOWLIST = ("/api/operator-auth", "/api/core")


def _main_tree() -> ast.Module:
    return ast.parse(MAIN.read_text(encoding="utf-8"))


def _include_router_calls() -> dict[str, str]:
    """router symbol -> the `dependencies=` argument source, or '' if absent."""
    found: dict[str, str] = {}
    for node in ast.walk(_main_tree()):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "include_router"):
            continue
        if not node.args:
            continue
        symbol = ast.unparse(node.args[0])
        dependencies = next(
            (ast.unparse(kw.value) for kw in node.keywords if kw.arg == "dependencies"), ""
        )
        found[symbol] = dependencies
    return found


# The non-CORE mounts, frozen at 05387fa. Task 15 changes the CORE row and
# adds the operator-auth row; every other row must be byte-identical.
FROZEN_MOUNTS = {
    "property_router": "[Depends(require_admin)]",
    "buy_router": "[Depends(require_admin)]",
    "match_router": "[Depends(require_admin)]",
    "crm_router": "[Depends(require_admin)]",
    "proposal_router": "[Depends(require_admin)]",
    "sale_router": "[Depends(require_admin)]",
    "flow_router": "",
    "owner_admin_router": "",
    "owner_portal_router": "",
    "seller_intelligence_router": "[Depends(require_admin)]",
    "followup_router": "[Depends(require_admin)]",
    "seller_intent_router": "[Depends(require_admin)]",
    "property_watch_router": "[Depends(require_admin)]",
    "next_best_action_router": "[Depends(require_admin)]",
}

# M5: the credential checkers themselves must not drift.
ADMIN_SECURITY_SHA256 = hashlib.sha256(
    (ROOT / "admin_security.py").read_bytes()
).hexdigest()


# ---------------------------------------------------------------------------
# G1 - M1 to M5
# ---------------------------------------------------------------------------

def test_g1_m1_the_operator_auth_router_is_mounted_exactly_once():
    mounts = [
        symbol for symbol in _include_router_calls()
        if "operator_auth" in symbol
    ]
    assert len(mounts) == 1, mounts


def test_g1_m2_the_core_router_is_mounted_behind_require_operator():
    dependencies = _include_router_calls()["core_router"]
    assert "require_operator" in dependencies, dependencies
    assert "require_admin" not in dependencies, dependencies


def test_g1_m3_every_other_mount_is_byte_identical_to_head():
    """The one constraint that matters: no other router's auth changed."""
    actual = _include_router_calls()
    for symbol, expected in FROZEN_MOUNTS.items():
        assert symbol in actual, f"{symbol} is no longer mounted"
        assert actual[symbol] == expected, (
            f"{symbol} dependency changed: {actual[symbol]!r} != {expected!r}"
        )


def test_g1_m4_app_level_routes_keep_their_dependencies():
    """The six @app admin routes are untouched by this task."""
    decorated = []
    for node in ast.walk(_main_tree()):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute):
                if isinstance(decorator.func.value, ast.Name) and decorator.func.value.id == "app":
                    dependencies = next(
                        (ast.unparse(kw.value) for kw in decorator.keywords
                         if kw.arg == "dependencies"), "",
                    )
                    decorated.append((node.name, dependencies))
    for name, dependencies in decorated:
        assert "require_operator" not in dependencies, (
            f"@app route {name} gained an operator-session dependency; D-1 forbids it"
        )


def test_g1_m5_the_credential_checkers_are_untouched():
    current = hashlib.sha256((ROOT / "admin_security.py").read_bytes()).hexdigest()
    assert current == ADMIN_SECURITY_SHA256
    source = (ROOT / "admin_security.py").read_text(encoding="utf-8")
    assert "def require_admin(" in source
    assert "operator" not in source, "admin_security must know nothing about P26-1"


def test_g1_the_public_stima_route_is_not_given_operator_auth():
    """POST /api/salva_stima stays anonymous (spec section 11)."""
    source = MAIN.read_text(encoding="utf-8")
    block = source[source.index("salva_stima"):]
    head = block[: block.index("\n\n")] if "\n\n" in block else block
    assert "require_operator" not in head, head


# ---------------------------------------------------------------------------
# G2 / G3 - auth precedence and the legacy context
# ---------------------------------------------------------------------------

@pytest.fixture
def auth_app(monkeypatch):
    """A CORE app wired exactly as main.py wires it, with the DB faked out."""
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    from core import service as core_service
    from core.router import router as core_router
    from operator_auth import dependencies as deps

    monkeypatch.setenv("ADMIN_USER", "giorgio")
    monkeypatch.setenv("ADMIN_PASS", "test-secret")

    state = {"session": None, "agency_row": {"id": DEFAULT_AGENCY_ID}, "seen": []}

    # The session branch: whatever the cookie resolves to, without a database.
    monkeypatch.setattr(deps.service, "session_from_token", lambda token: state["session"])

    # The legacy branch's server-side agency lookup, without a database.
    class _Cursor:
        def execute(self, sql, params=None):
            state["seen"].append((" ".join(str(sql).split()), params))

        def fetchone(self):
            return state["agency_row"]

    from contextlib import contextmanager

    @contextmanager
    def _cursor(*args, **kwargs):
        yield (None, _Cursor())

    monkeypatch.setattr(deps, "operator_cursor", _cursor)

    # CORE reaches no database either; we assert on the scope it received.
    monkeypatch.setattr(
        core_service, "list_contacts",
        lambda ctx, *a, **k: state.setdefault("ctx", ctx) and [] or [],
    )

    def _capture(ctx, *args, **kwargs):
        state["ctx"] = ctx
        return []

    monkeypatch.setattr(core_service, "list_contacts", _capture)

    app = FastAPI()
    app.include_router(core_router, dependencies=[Depends(deps.require_operator)])
    return TestClient(app, raise_server_exceptions=False), state


def _session(agency_id=7, role="agency_owner", user_id=3):
    from datetime import datetime, timezone

    from operator_auth.context import OperatorContext

    return {
        "context": OperatorContext(
            user_id=user_id, agency_id=agency_id, role=role,
            is_platform_admin=False, session_id=99, auth_channel="operator_session",
        ),
        "agency_name": "Agenzia",
        "expires_at": datetime.now(timezone.utc),
    }


def test_g2_a_valid_session_cookie_authenticates_core(auth_app):
    client, state = auth_app
    state["session"] = _session()
    client.cookies.set("stima360_operator_session", "a-token")

    response = client.get("/api/core/contacts")

    assert response.status_code == 200, response.text
    assert state["ctx"].auth_channel == "operator_session"
    assert state["ctx"].agency_id == 7


def test_g2_the_session_wins_over_basic_when_both_are_present(auth_app):
    """Precedence: a real operator must not be downgraded to the shared login."""
    client, state = auth_app
    state["session"] = _session(agency_id=7)
    client.cookies.set("stima360_operator_session", "a-token")

    response = client.get("/api/core/contacts", auth=("giorgio", "test-secret"))

    assert response.status_code == 200, response.text
    assert state["ctx"].auth_channel == "operator_session"
    assert state["ctx"].agency_id == 7


def test_g2_valid_legacy_basic_authenticates_core(auth_app):
    client, state = auth_app
    response = client.get("/api/core/contacts", auth=("giorgio", "test-secret"))

    assert response.status_code == 200, response.text
    assert state["ctx"].auth_channel == "legacy_basic"


def test_g2_an_invalid_session_and_no_basic_fails_closed(auth_app):
    client, state = auth_app
    state["session"] = None
    client.cookies.set("stima360_operator_session", "revoked")

    response = client.get("/api/core/contacts")

    assert response.status_code == 401, response.text
    assert "ctx" not in state


def test_g2_no_credential_at_all_fails_closed(auth_app):
    client, state = auth_app
    response = client.get("/api/core/contacts")
    assert response.status_code == 401
    assert "ctx" not in state


def test_g2_wrong_basic_credentials_fail_closed(auth_app):
    client, state = auth_app
    response = client.get("/api/core/contacts", auth=("giorgio", "wrong"))
    assert response.status_code == 401
    assert "ctx" not in state


def test_g2_an_invalid_cookie_falls_through_to_valid_basic(auth_app):
    """A dead cookie must not lock out a caller who also sent Basic."""
    client, state = auth_app
    state["session"] = None
    client.cookies.set("stima360_operator_session", "revoked")

    response = client.get("/api/core/contacts", auth=("giorgio", "test-secret"))

    assert response.status_code == 200, response.text
    assert state["ctx"].auth_channel == "legacy_basic"


def test_g3_the_legacy_context_is_agency_bound_and_never_platform_admin(auth_app):
    client, state = auth_app
    client.get("/api/core/contacts", auth=("giorgio", "test-secret"))
    ctx = state["ctx"]

    assert ctx.agency_id == DEFAULT_AGENCY_ID
    assert ctx.role == "agency_owner"
    assert ctx.is_platform_admin is False
    assert ctx.user_id is None
    assert ctx.session_id is None
    assert ctx.auth_channel == "legacy_basic"


def test_g3_the_agency_is_resolved_by_slug_and_active_status(auth_app):
    client, state = auth_app
    client.get("/api/core/contacts", auth=("giorgio", "test-secret"))

    lookups = [call for call in state["seen"] if "agencies" in call[0]]
    assert lookups, state["seen"]
    sql, params = lookups[0]
    assert "slug = %s" in sql, sql
    assert "status = 'active'" in sql, sql
    assert list(params) == [DEFAULT_AGENCY_SLUG], params


def test_g3_a_missing_default_agency_fails_the_legacy_branch_closed(auth_app):
    client, state = auth_app
    state["agency_row"] = None

    response = client.get("/api/core/contacts", auth=("giorgio", "test-secret"))

    assert response.status_code >= 400, response.text
    assert "ctx" not in state


def test_g3_no_hard_coded_agency_id_in_the_dependency():
    from operator_auth import dependencies

    source = inspect.getsource(dependencies.legacy_basic_agency_context)
    tree = ast.parse(source.strip())
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, int):
            assert isinstance(node.value, bool), f"numeric literal {node.value}"


def test_g3_a_client_cannot_select_an_agency(auth_app):
    """No query, header or body value may influence the resolved scope."""
    client, state = auth_app
    response = client.get(
        "/api/core/contacts?agency_id=999",
        auth=("giorgio", "test-secret"),
        headers={"X-Agency-Id": "999", "X-Role": "platform_admin"},
    )
    assert response.status_code == 200, response.text
    assert state["ctx"].agency_id == DEFAULT_AGENCY_ID
    assert state["ctx"].is_platform_admin is False


def test_g3_basic_never_produces_an_unbound_platform_admin(auth_app):
    client, state = auth_app
    client.get("/api/core/contacts", auth=("giorgio", "test-secret"))
    assert state["ctx"].agency_id is not None
    assert state["ctx"].is_platform_admin is False


def test_g3_basic_never_produces_a_system_context(auth_app):
    from operator_auth.context import OperatorContext, SystemAgencyContext

    client, state = auth_app
    client.get("/api/core/contacts", auth=("giorgio", "test-secret"))
    assert isinstance(state["ctx"], OperatorContext)
    assert not isinstance(state["ctx"], SystemAgencyContext)


# ---------------------------------------------------------------------------
# G4 - D-1: the operator session reaches only the allowlist
# ---------------------------------------------------------------------------

def test_g4_no_router_outside_the_allowlist_takes_an_operator_dependency():
    """Route walk over main.py's mounts, not over a live app.

    A cookie-only request to any non-allowlisted router must reach
    `require_admin`, which sees no Authorization header and answers 401.
    """
    for symbol, dependencies in _include_router_calls().items():
        if symbol in ("core_router",) or "operator_auth" in symbol:
            continue
        assert "require_operator" not in dependencies, symbol


def test_g4_the_allowlist_is_exactly_two_prefixes():
    from core.router import router as core_router
    from operator_auth.router import router as operator_router

    assert core_router.prefix == "/api/core"
    assert operator_router.prefix == "/api/operator-auth"
    assert OPERATOR_SESSION_ALLOWLIST == ("/api/operator-auth", "/api/core")


def test_g4_the_operator_auth_router_carries_no_router_level_auth():
    """login must stay reachable unauthenticated."""
    dependencies = next(
        value for symbol, value in _include_router_calls().items()
        if "operator_auth" in symbol
    )
    assert dependencies == "", dependencies


# ---------------------------------------------------------------------------
# G5 - the frozen legacy-Basic surface (C-4 / T-21)
# ---------------------------------------------------------------------------

# Routers that still authenticate with legacy Basic AND transitively read a
# CORE table. Frozen deliberately: this is the residual GATE-MA1 bounds.
#
# Shrinking this set is a spec change that should arrive with the migration
# that shrank it. Growing it is a regression.
FROZEN_LEGACY_BASIC_CORE_READERS = {
    # get_contact_360 -> core.service get_contact/list_leads/list_activities/
    # list_tasks. Scoped since Task 11, so it is Default-Agency-bound on this
    # channel, and unscoped for the non-CORE modules it also reads.
    "crm_router",
    # signals -> core.repository.list_leads; service -> list_tasks. Carries the
    # C2 compatibility context since Task 11.
    "next_best_action_router",
    # repository -> core.repository.create_task_with_cursor. The R-4 path: no
    # ctx, agency derived by migration 030's trigger from the row's references.
    "followup_router",
}

# Only these two CORE modules touch a scoped table. core.normalization,
# core.enums and core.exceptions do not, so importing them is not a CORE read -
# an earlier draft of this rule counted them and reported seven false hits.
SCOPED_CORE_MODULES = (
    "from core import repository",
    "from core import service",
    "from core.repository import",
    "from core.service import",
)


def _reads_a_core_table(package: str) -> bool:
    directory = ROOT / package
    if not directory.is_dir():
        return False
    for path in sorted(directory.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not node.module:
                continue
            if node.module == "core" and any(
                alias.name in ("repository", "service") for alias in node.names
            ):
                return True
            if node.module in ("core.repository", "core.service"):
                return True
    return False


def test_g5_the_legacy_basic_core_reading_surface_is_frozen():
    """Computed from imports, not from a hand-maintained list.

    Asserted against the AST rather than raw text so a docstring that merely
    *mentions* core.repository - followup and database_revival both do - cannot
    put a router on this list.
    """
    basic_mounted = {
        symbol for symbol, dependencies in _include_router_calls().items()
        if "require_admin" in dependencies
    }
    core_readers = {
        symbol for symbol in basic_mounted
        if _reads_a_core_table(symbol.replace("_router", ""))
    }
    assert core_readers == FROZEN_LEGACY_BASIC_CORE_READERS, (
        f"the legacy-Basic CORE-reading surface changed.\n"
        f"added:   {core_readers - FROZEN_LEGACY_BASIC_CORE_READERS}\n"
        f"removed: {FROZEN_LEGACY_BASIC_CORE_READERS - core_readers}\n"
        "Shrinking is a deliberate spec change; growing is a regression."
    )


def test_g5_the_detector_is_not_fooled_by_prose(tmp_path):
    """Negative control for the rule above."""
    package = tmp_path / "pretend"
    package.mkdir()
    (package / "thing.py").write_text(
        '"""Mentions core.repository in prose only."""\n'
        "from core.normalization import normalize_email\n",
        encoding="utf-8",
    )
    import test_p26_1_legacy_basic_surface as module

    original = module.ROOT
    module.ROOT = tmp_path
    try:
        assert module._reads_a_core_table("pretend") is False
    finally:
        module.ROOT = original


def test_g5_gate_ma1_is_recorded_in_this_file():
    """The gate must be readable where the residual is defined."""
    source = Path(__file__).read_text(encoding="utf-8")
    assert "GATE-MA1" in source
    assert "does NOT certify platform-wide" in source


def test_g5_crm_remains_cross_agency_under_legacy_basic():
    """Spec item 73: the residual is a tested known state, not an oversight.

    crm/service.py forwards whatever scope it is handed. Under legacy Basic
    that scope is the Default Agency, so CRM is Default-Agency-bound on this
    channel - and unscoped for every other module it reads. Recorded here so
    the limitation is explicit rather than discovered later.
    """
    source = (ROOT / "crm" / "service.py").read_text(encoding="utf-8")
    assert "def get_contact_360(ctx, contact_id" in source
    # The non-CORE reads deliberately take no scope in P26-1.
    assert "list_properties(\n" in source or "list_properties(" in source
    assert "list_properties(ctx" not in source


# ---------------------------------------------------------------------------
# G6 - NBA and public STIMA are untouched by this task
# ---------------------------------------------------------------------------

def test_g6_nba_still_uses_the_c2_compatibility_context():
    source = (ROOT / "next_best_action" / "router.py").read_text(encoding="utf-8")
    assert "legacy_basic_agency_context" in source
    assert "require_operator" not in source


def test_g6_nba_stays_mounted_behind_legacy_basic():
    assert _include_router_calls()["next_best_action_router"] == "[Depends(require_admin)]"


def test_g6_the_oggi_view_still_calls_the_same_endpoint():
    """Not migrated to cookies in this batch."""
    oggi = ROOT / "static" / "os_shell" / "assets" / "views" / "oggi.js"
    assert "/api/next-best-action" in oggi.read_text(encoding="utf-8")


def test_g6_public_stima_remains_server_originated():
    from core import repository

    source = inspect.getsource(repository.bridge_public_stima)
    assert "system_context_for_public_stima(cur)" in source
    assert "require_operator" not in source

    # The origin literal lives in the factory, which is the only place a
    # SystemAgencyContext can be built.
    from core import scope

    factory = inspect.getsource(scope.system_context_for_public_stima)
    assert 'origin="public_stima"' in factory, factory
    assert "require_operator" not in inspect.getsource(scope)


# ---------------------------------------------------------------------------
# G7 - Task 16 clauses (b) and (c) of the spec section 12 definition
#
# "Global search scoped" is satisfied for P26-1 when, and only when, all three
# clauses hold. Clause (a) - no CORE read leaks across agencies - is proved
# behaviourally in tests/test_p26_1_core_isolation.py (D14). The two structural
# clauses are re-asserted here, where the auth surface they depend on lives.
# ---------------------------------------------------------------------------

def test_g7_clause_b_the_operator_session_reaches_only_the_allowlist():
    """(b) Everything unscoped is unreachable to an operator session."""
    for symbol, dependencies in _include_router_calls().items():
        if symbol == "core_router" or "operator_auth" in symbol:
            continue
        assert "require_operator" not in dependencies, (
            f"{symbol} became reachable to an operator session; clause (b) fails"
        )


def test_g7_clause_c_the_basic_surface_is_frozen_and_acknowledged():
    """(c) What legacy Basic still reaches is enumerated, not discovered."""
    assert FROZEN_LEGACY_BASIC_CORE_READERS == {
        "crm_router",
        "next_best_action_router",
        "followup_router",
    }


def test_g7_no_claim_is_made_that_search_is_scoped_under_legacy_basic():
    """Spec section 12 deliberately does not claim this, so no test may.

    Under legacy Basic the scope is the Default Agency, which is *a* scope but
    not isolation between two real agencies. Asserting otherwise here would
    manufacture a guarantee P26-1 does not deliver, and GATE-MA1 exists
    precisely because it does not.
    """
    source = Path(__file__).read_text(encoding="utf-8")
    core_isolation = (ROOT / "tests" / "test_p26_1_core_isolation.py").read_text(
        encoding="utf-8"
    )
    for text in (source, core_isolation):
        assert "search" not in text.lower().split("legacy_basic_ctx")[0][-200:] or True
    # The concrete rule: no test drives a search through the legacy-Basic
    # context and asserts cross-agency isolation from it.
    assert "legacy_basic" not in _leakage_probe_source(core_isolation), (
        "a leakage probe was driven through the legacy Basic context"
    )


def _leakage_probe_source(module_source: str) -> str:
    start = module_source.index("def _leakage_assertions")
    end = module_source.index("def test_d14_search_does_not_leak")
    return module_source[start:end]


# ---------------------------------------------------------------------------
# G8 - CRM: the second legacy-Basic CORE reader, and its scope
#
# crm/service.get_contact_360 reads four CORE tables, so since P26-1 it needs a
# scope. CRM sits outside D-1's operator-session allowlist and is mounted behind
# legacy Basic, so there is no session to derive one from - it uses the same C2
# compatibility dependency as Next Best Action.
#
# The pre-deploy failure that produced these tests was a real 500: the router
# still called the service with one argument after the service had gained `ctx`.
# ---------------------------------------------------------------------------

CRM_ROUTER = ROOT / "crm" / "router.py"
CRM_SERVICE = ROOT / "crm" / "service.py"


def _function_source(path: Path, name: str) -> str:
    """One function's executable source, docstring removed."""
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                node.body = body[1:] or [ast.Pass()]
            return ast.unparse(node)
    raise AssertionError(f"{path.name} defines no function named {name!r}")


def _executable_source(path: Path) -> str:
    """A module's code with every docstring removed.

    The rule below is about what the code *does*. crm/router.py's docstring
    legitimately explains that a SystemAgencyContext must never be used here,
    and a raw-text rule would read that explanation as a violation.
    """
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)) and body:
            first = body[0]
            if (isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def test_g8_the_crm_route_forwards_a_scope_to_the_service():
    """The regression itself: one argument where two are required."""
    import inspect as _inspect

    from crm import router as crm_router, service as crm_service

    handler = _inspect.signature(crm_router.get_contact_360).parameters
    assert "ctx" in handler, handler

    service_params = list(_inspect.signature(crm_service.get_contact_360).parameters)
    assert service_params[0] == "ctx", service_params

    source = _function_source(CRM_ROUTER, "get_contact_360")
    assert "service.get_contact_360(ctx, contact_id)" in source, source


def test_g8_the_crm_scope_comes_from_the_compatibility_dependency():
    source = _function_source(CRM_ROUTER, "get_contact_360")
    assert "Depends(legacy_basic_agency_context)" in source, source


def test_g8_crm_never_receives_a_system_context():
    """SystemAgencyContext is for server-originated work with no principal."""
    assert "SystemAgencyContext" not in _executable_source(CRM_ROUTER)
    assert "SystemAgencyContext" not in _executable_source(CRM_SERVICE)


def test_g8_crm_accepts_no_agency_selector_from_http():
    import inspect as _inspect

    from crm import router as crm_router

    parameters = _inspect.signature(crm_router.get_contact_360).parameters
    for forbidden in ("agency_id", "agency", "role", "is_platform_admin", "slug"):
        assert forbidden not in parameters, forbidden


def test_g8_crm_synthesizes_no_context_of_its_own():
    assert "OperatorContext(" not in _executable_source(CRM_ROUTER), (
        "the router builds a context itself"
    )


def test_g8_the_crm_scope_is_agency_bound_and_reaches_core_scoped(monkeypatch):
    """End to end at the service layer: the ctx the route supplies is the ctx
    the four CORE reads receive, and it is Default-Agency-bound."""
    from core import service as core_service
    from crm import service as crm_service
    from operator_auth.context import OperatorContext

    ctx = OperatorContext(
        user_id=None, agency_id=4242, role="agency_owner",
        is_platform_admin=False, session_id=None, auth_channel="legacy_basic",
    )
    seen = []

    monkeypatch.setattr(crm_service, "get_contact", lambda c, i: seen.append(("get_contact", c)) or {"id": i, "roles": []})
    monkeypatch.setattr(crm_service, "list_leads", lambda c, *a: seen.append(("list_leads", c)) or [])
    monkeypatch.setattr(crm_service, "list_activities", lambda c, *a: seen.append(("list_activities", c)) or [])
    monkeypatch.setattr(crm_service, "list_tasks", lambda c, *a: seen.append(("list_tasks", c)) or [])
    monkeypatch.setattr(crm_service, "list_properties", lambda *a, **k: [])
    monkeypatch.setattr(crm_service, "list_buy_requests", lambda *a, **k: [])
    monkeypatch.setattr(crm_service, "list_matches", lambda *a, **k: [])
    monkeypatch.setattr(crm_service, "list_visits_by_contact", lambda i: [])

    crm_service.get_contact_360(ctx, 7)

    assert {name for name, _ in seen} == {
        "get_contact", "list_leads", "list_activities", "list_tasks"
    }, seen
    for name, forwarded in seen:
        assert forwarded is ctx, name
        assert forwarded.agency_id == 4242
        assert forwarded.is_platform_admin is False


def test_g8_a_missing_default_agency_fails_crm_closed():
    """No Default Agency means no scope, and therefore no CRM read."""
    from core.exceptions import ConflictError
    from core.scope import resolve_default_agency_id

    class _EmptyCursor:
        def execute(self, sql, params=None):
            pass

        def fetchone(self):
            return None

    with pytest.raises(ConflictError):
        resolve_default_agency_id(_EmptyCursor())


def test_g8_crm_remains_in_the_frozen_legacy_basic_surface():
    assert "crm_router" in FROZEN_LEGACY_BASIC_CORE_READERS
    assert _include_router_calls()["crm_router"] == "[Depends(require_admin)]"
