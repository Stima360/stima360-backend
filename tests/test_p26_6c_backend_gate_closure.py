"""P26-6C - CRM 360 agency isolation, and the last of GATE-MA1.

CRM is the final entry in the GATE-MA1 residual, and it is a different shape
from every module P26-2..6B migrated. Those owned their own tables. CRM owns
none: it is an aggregator that reads eight subsystems and returns one document.
Its isolation is therefore entirely a question of what it *passes*, not of what
it queries - there is no CRM table to give an `agency_id` to, and no migration
that could fix a missing argument here.

WHAT WAS ACTUALLY WRONG

The route already carried `legacy_basic_agency_context`, and four of the eight
reads already took the ctx. The other four did not:

    properties       list_properties(500, 0, ...)          no ctx -> no filter
    buy requests     buy.service.list_requests(...)         the legacy surface
    matches          match.service.list_matches(...)        the legacy surface
    visits           list_visits_by_contact(contact_id)     no ctx -> no filter

So agency A's Contact 360 could return agency B's properties, buy requests,
matches and visits. The contact, its leads, activities and tasks were already
A's - which is what made the document look right.

MATCHES ARE THE COMPOUND CASE

The match loop enumerates by `buy_request_id`. Scoping the match read alone
would not be enough if the request ids came from an unscoped buy read: the
match query would faithfully return matches for a foreign request. Both had to
move together, and test 16 asserts the pairing rather than either half.

TWO LAYERS, AS EVERYWHERE ELSE IN P26

    A  structural - the ctx object the router resolved is the object every
       subsystem receives, asserted by identity (`is`), not by equality;
       and the scoped surfaces are the ones imported.

    B  behavioural - the real service runs against an agency-aware cursor that
       honours the predicate the SQL carries. A read that forgot one SEES the
       foreign row, so the assertion fails on data rather than on text.

Coverage map:

     1-2    route inventory and dependency
     3-11   the ctx reaches all eight subsystems, unchanged
    12-13   foreign contact: 404, and nothing else is read
    14-18   no agency B row reaches agency A's document
    19-21   unbound admin, forged agency, server-side Default Agency
    22-25   the legacy surfaces are gone and the ctx really travels
"""
from __future__ import annotations

import ast
import inspect
import re
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from operator_auth.context import OperatorContext
from operator_auth.exceptions import PlatformAdminAgencyRequired

ROOT = Path(__file__).resolve().parents[1]

AGENCY_DEPENDENCY = "legacy_basic_agency_context"

A, B = 61, 94          # two agencies; deliberately neither 1 nor adjacent

CONTACT_A = 700
CONTACT_B = 900

# Every module CRM reads through. Each binds `core_cursor` into its own
# namespace at import time, so the seam is per-module rather than one shared
# patch - installing on `core.database` alone would leave four live cursors.
CURSOR_MODULES = (
    "core.repository",
    "property.repository",
    "buy.repository",
    "match.repository",
)


def ctx(agency_id=A) -> OperatorContext:
    return OperatorContext(
        user_id=None,
        agency_id=agency_id,
        role="agency_owner",
        is_platform_admin=False,
        session_id=None,
        auth_channel="legacy_basic",
    )


def unbound() -> OperatorContext:
    return OperatorContext(
        user_id=9, agency_id=None, role="platform_admin",
        is_platform_admin=True, session_id=1, auth_channel="operator_session",
    )


# ---------------------------------------------------------------------------
# Layer B's cursor.
#
# Every table holds one row for agency A and one for agency B, and the cursor
# answers each statement with whichever the statement's own predicate admits:
#
#   names a tenant, asks for A  ->  A's row
#   names a tenant, asks for B  ->  B's row
#   names no tenant             ->  BOTH, which is what an unscoped read
#                                   against a real multi-tenant table returns
#
# An earlier draft had the cursor own only agency B, so a scoped read returned
# nothing. That looked stricter and was weaker: the buy read came back empty,
# the match loop never ran, and the MATCH assertion passed without MATCH ever
# being queried - it survived a mutation that replaced the pair predicate with
# `TRUE AND TRUE`. Returning A's row keeps the assembly moving so that every
# read downstream of it is actually reached.
# ---------------------------------------------------------------------------

class TenantCursor:
    def __init__(self, *, seed=None):
        self.seed = list(seed or [])
        self.statements: list[tuple[str, object]] = []
        self.current: list[dict] = []

    def execute(self, sql, params=None):
        flat = " ".join(str(sql).split())
        self.statements.append((flat, params))

        if self.seed:
            self.current = self.seed.pop(0)
            return

        values = list(params.values()) if isinstance(params, dict) else list(params or [])
        names_a_tenant = bool(re.search(r"agency_id\s*=\s*%s", flat, re.IGNORECASE))
        asked = [v for v in values if v in (A, B)]

        if not names_a_tenant:
            self.current = [self._row(A), self._row(B)]
        else:
            self.current = [self._row(agency) for agency in (A, B) if agency in asked]

    @staticmethod
    def _row(agency: int) -> dict:
        """One row of whatever was asked for, tagged with its agency.

        Deliberately wide: these tests read four repositories with four row
        shapes, and a row missing a key would fail as a KeyError rather than as
        the isolation assertion the test is making.
        """
        marker = CONTACT_A if agency == A else CONTACT_B
        label = "AGENCY A" if agency == A else "AGENCY B"
        return {
            "id": marker, "agency_id": agency,
            "contact_id": marker, "lead_id": marker,
            "buy_request_id": marker, "property_id": marker,
            "match_id": marker, "visit_id": marker,
            "display_name": label, "title": label, "contact_name": label,
            "property_title": label, "code": label,
            "contact_type": "person", "status": "active", "roles": [],
            "n": 0, "count": 0, "total": 0,
        }

    def fetchone(self):
        return self.current[0] if self.current else None

    def fetchall(self):
        return list(self.current)

    def sql_of(self, needle: str) -> list[tuple[str, object]]:
        return [s for s in self.statements if needle.lower() in s[0].lower()]


@contextmanager
def install_cursor(cursor, modules=CURSOR_MODULES):
    import importlib

    @contextmanager
    def fake(*_a, **_k):
        yield None, cursor

    originals = []
    for name in modules:
        module = importlib.import_module(name)
        originals.append((module, getattr(module, "core_cursor")))
        module.core_cursor = fake
    try:
        yield cursor
    finally:
        for module, original in originals:
            module.core_cursor = original


class Recorder:
    """Captures the ctx each subsystem was handed, by identity."""

    def __init__(self, result=None):
        self.calls: list[tuple] = []
        self.result = [] if result is None else result

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.result

    @property
    def ctx(self):
        assert self.calls, "never called"
        return self.calls[0][0][0] if self.calls[0][0] else self.calls[0][1].get("ctx")


def _crm_source() -> str:
    return (ROOT / "crm" / "service.py").read_text(encoding="utf-8")


def _routes(package: str) -> tuple[list[str], list[str]]:
    """Route handlers, and those declaring the agency dependency.

    The same decorator-to-parameter match GATE-MA1's own prover uses, restated
    here rather than imported: this file must be able to fail on its own.
    """
    tree = ast.parse((ROOT / package / "router.py").read_text(encoding="utf-8"))
    routes, scoped = [], []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not any(
            isinstance(d, ast.Call)
            and isinstance(d.func, ast.Attribute)
            and d.func.attr in ("get", "post", "patch", "put", "delete")
            and isinstance(d.func.value, ast.Name)
            and d.func.value.id == "router"
            for d in node.decorator_list
        ):
            continue
        routes.append(node.name)
        for default in list(node.args.defaults) + list(node.args.kw_defaults):
            if (
                isinstance(default, ast.Call)
                and isinstance(default.func, ast.Name)
                and default.func.id == "Depends"
                and any(
                    isinstance(a, ast.Name) and a.id == AGENCY_DEPENDENCY
                    for a in default.args
                )
            ):
                scoped.append(node.name)
    return routes, scoped


# ---------------------------------------------------------------------------
# 1-2 - the HTTP surface
# ---------------------------------------------------------------------------

def test_1_crm_route_inventory():
    routes, _ = _routes("crm")
    assert len(routes) == 1, sorted(routes)


def test_2_the_crm_route_declares_the_agency_dependency():
    routes, scoped = _routes("crm")
    assert set(routes) == set(scoped), sorted(set(routes) - set(scoped))


# ---------------------------------------------------------------------------
# 3-11 - one ctx, eight subsystems
#
# Asserted with `is`. Equality would pass for a context CRM rebuilt itself, and
# a context CRM builds is a context CRM could get wrong.
# ---------------------------------------------------------------------------

# Named as CRM imports them. BUY and MATCH are the unaliased `_scoped`
# spellings on purpose: the alias form the brief also permits would make this
# list read identically before and after the fix, which is precisely the
# ambiguity P26-6C set out to remove from the source.
SUBSYSTEMS = (
    ("get_contact", {"id": CONTACT_A, "roles": []}),
    ("list_leads", []),
    ("list_properties", []),
    # One request, because the match read is inside the loop over them: an
    # empty buy result would leave MATCH unexercised and this file would report
    # a context it never actually watched arrive.
    ("list_requests_scoped", [{"id": 500}]),
    ("list_matches_scoped", []),
    ("list_visits_by_contact", []),
    ("list_activities", []),
    ("list_tasks", []),
)


@pytest.fixture
def recorded(monkeypatch):
    """Every subsystem replaced by a recorder, so only the wiring is exercised."""
    from crm import service as crm_service

    recorders = {}
    for name, result in SUBSYSTEMS:
        recorder = Recorder(result)
        recorders[name] = recorder
        monkeypatch.setattr(crm_service, name, recorder)
    return recorders


def test_3_get_contact_360_takes_a_context():
    from crm import service as crm_service

    parameters = list(inspect.signature(crm_service.get_contact_360).parameters)
    assert parameters[0] == "ctx", parameters


@pytest.mark.parametrize("name", [n for n, _ in SUBSYSTEMS])
def test_4_to_11_every_subsystem_receives_the_routers_own_context(recorded, name):
    from crm import service as crm_service

    scope = ctx(A)
    crm_service.get_contact_360(scope, CONTACT_A)

    recorder = recorded[name]
    assert recorder.calls, f"{name} was never called"
    assert recorder.ctx is scope, (
        f"{name} received {recorder.ctx!r}, not the context the route resolved"
    )


# ---------------------------------------------------------------------------
# 12-13 - a contact this agency cannot see
# ---------------------------------------------------------------------------

def _crm_client(monkeypatch):
    """The real router, real Basic guard, and the DB-backed scope overridden.

    The dependency object is taken from the router module, so it is the same
    function object FastAPI resolved rather than a second import of the name.
    """
    from admin_security import require_admin
    from crm import router as crm_router_module

    monkeypatch.setenv("ADMIN_USER", "giorgio")
    monkeypatch.setenv("ADMIN_PASS", "test-secret")
    app = FastAPI()
    app.include_router(crm_router_module.router, dependencies=[Depends(require_admin)])
    app.dependency_overrides[
        crm_router_module.legacy_basic_agency_context
    ] = lambda: ctx(A)
    return TestClient(app, raise_server_exceptions=False)


def test_12_a_foreign_contact_is_a_404(monkeypatch):
    from core.exceptions import NotFoundError
    from crm import service as crm_service

    client = _crm_client(monkeypatch)

    def _refuse(_ctx, contact_id):
        raise NotFoundError(f"contact {contact_id} not found")

    monkeypatch.setattr(crm_service, "get_contact", _refuse)
    response = client.get(
        f"/api/crm/contacts/{CONTACT_B}/360", auth=("giorgio", "test-secret")
    )

    assert response.status_code == 404, response.status_code
    assert client.get(f"/api/crm/contacts/{CONTACT_B}/360").status_code == 401


def test_13_a_foreign_contact_starts_no_secondary_read(recorded):
    """The contact lookup is first, and it is the gate.

    Nothing downstream may run speculatively: a 404 that had already listed the
    properties would have read them out of scope to decide it could not.
    """
    from core.exceptions import NotFoundError
    from crm import service as crm_service

    def _refuse(_ctx, contact_id):
        raise NotFoundError(f"contact {contact_id} not found")

    crm_service.get_contact = _refuse
    try:
        with pytest.raises(NotFoundError):
            crm_service.get_contact_360(ctx(A), CONTACT_B)
    finally:
        crm_service.get_contact = recorded["get_contact"]

    for name in ("list_leads", "list_properties", "list_requests_scoped",
                 "list_matches_scoped", "list_visits_by_contact",
                 "list_activities", "list_tasks"):
        assert not recorded[name].calls, f"{name} ran for a contact we cannot see"


# ---------------------------------------------------------------------------
# 14-18 - no agency B row reaches agency A's document
#
# Driven through the real services against the tenant cursor. The seed supplies
# only the contact; everything after it is decided by the predicate the SQL
# carries.
# ---------------------------------------------------------------------------

def _assembled_360_against_agency_b():
    from crm import service as crm_service

    cursor = TenantCursor()
    with install_cursor(cursor):
        return crm_service.get_contact_360(ctx(A), CONTACT_A), cursor


def _mentions_agency_b(value) -> bool:
    if isinstance(value, dict):
        return any(_mentions_agency_b(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(_mentions_agency_b(v) for v in value)
    return value in (B, CONTACT_B, "AGENCY B")


@pytest.mark.parametrize(
    "section", ["properties", "buy_requests", "matches", "visits", "activities", "tasks"]
)
def test_14_to_18_no_agency_b_row_appears_in_agency_as_360(section):
    document, _cursor = _assembled_360_against_agency_b()
    assert not _mentions_agency_b(document[section]), document[section]


def test_18b_every_root_read_of_the_360_names_a_tenant():
    """Said on the statements, not only on the rows.

    Two of these reads are counts and joins whose foreign rows would not be
    returned to the caller at all - the quiet-leak shape P26-6B named - so the
    predicate has to be asserted where it is written.
    """
    _document, cursor = _assembled_360_against_agency_b()
    roots = ("FROM properties", "FROM buy_requests", "FROM property_visits",
             "FROM matches")
    for root in roots:
        statements = cursor.sql_of(root)
        if not statements:
            continue
        for flat, params in statements:
            assert re.search(r"agency_id\s*=\s*%s", flat, re.IGNORECASE), flat
            values = list(params.values()) if isinstance(params, dict) else list(params or [])
            assert A in values, (flat[:160], params)


# ---------------------------------------------------------------------------
# 19-21 - who may ask, and with what
# ---------------------------------------------------------------------------

def test_19_an_unbound_platform_admin_is_refused_before_any_read():
    from crm import service as crm_service

    cursor = TenantCursor()
    with install_cursor(cursor):
        with pytest.raises(PlatformAdminAgencyRequired):
            crm_service.get_contact_360(unbound(), CONTACT_A)
    assert cursor.statements == [], cursor.statements[:2]


def test_20_no_client_supplied_agency_can_reach_the_service():
    """The route's only inputs are the path id and the dependency.

    An `agency_id` query parameter or body field would be a client-chosen
    tenant, which is the one thing the C2 compatibility context exists to
    prevent.
    """
    tree = ast.parse((ROOT / "crm" / "router.py").read_text(encoding="utf-8"))
    handlers = [
        n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(
            isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
            and isinstance(d.func.value, ast.Name) and d.func.value.id == "router"
            for d in n.decorator_list
        )
    ]
    assert handlers
    for handler in handlers:
        names = [a.arg for a in handler.args.args + handler.args.kwonlyargs]
        assert "agency_id" not in names, (handler.name, names)

    from crm import schemas as crm_schemas
    from pydantic import BaseModel

    for attribute, obj in vars(crm_schemas).items():
        if isinstance(obj, type) and issubclass(obj, BaseModel) and obj is not BaseModel:
            assert "agency_id" not in getattr(obj, "model_fields", {}), attribute


def test_21_the_legacy_basic_scope_is_resolved_server_side():
    """The dependency reads the Default Agency from its slug, in the database.

    Nothing about the request selects it, and the resulting context is
    agency-bound rather than platform-wide.
    """
    from operator_auth import dependencies

    # P26-3 split the choice from the construction: `_default_agency_context`
    # builds the legacy scope, `legacy_basic_agency_context` picks between it
    # and a live session. Both halves are asserted.
    builder = inspect.getsource(dependencies._default_agency_context)
    assert "resolve_default_agency_id" in builder, builder
    assert "is_platform_admin=False" in builder.replace(" ", ""), builder
    assert not re.search(r"request\.(query_params|headers|json)", builder), builder

    chooser = inspect.getsource(dependencies.legacy_basic_agency_context)
    assert not re.search(r"request\.(query_params|headers|json)", chooser), chooser
    assert "agency_id" not in chooser.split('"""')[-1], chooser


# ---------------------------------------------------------------------------
# 22-25 - the legacy surfaces are gone, and the ctx really travels
# ---------------------------------------------------------------------------

def test_22_crm_no_longer_imports_the_legacy_buy_surface():
    source = _crm_source()
    assert not re.search(r"from buy\.service import .*\blist_requests\b(?!_scoped)", source), source
    assert "list_requests_scoped" in source, source


def test_23_crm_no_longer_imports_the_legacy_match_surface():
    source = _crm_source()
    assert not re.search(r"from match\.service import .*\blist_matches\b(?!_scoped)", source), source
    assert "list_matches_scoped" in source, source


def test_24_the_property_call_really_carries_the_context():
    """PROPERTY keeps a compatibility overload: `list_properties(*args)` accepts
    the ctx or does without it, and silently drops the tenant filter when it is
    absent. A name that reads as scoped is therefore not evidence here - the
    argument has to be seen arriving."""
    from crm import service as crm_service

    recorder = Recorder([])
    original = crm_service.list_properties
    crm_service.list_properties = recorder
    try:
        scope = ctx(A)
        crm_service.get_contact_360.__wrapped__ if False else None
        with install_cursor(TenantCursor()):
            try:
                crm_service.get_contact_360(scope, CONTACT_A)
            except Exception:  # noqa: BLE001 - only the property call is asserted
                pass
    finally:
        crm_service.list_properties = original

    assert recorder.calls, "list_properties was never called"
    args, _kwargs = recorder.calls[0]
    assert args and args[0] is scope, args[:1]
    assert len(args) == 12, f"the 11 legacy positional filters must survive: {len(args)}"


def test_25b_the_visit_overload_is_the_hazard_this_test_exists_for():
    """`list_visits_by_contact(contact_id)` is still a legal call.

    PROPERTY branches on argument count, so the one-argument form reads every
    agency's visits and raises nothing. That is why test 25 asserts arrival
    rather than absence of the legacy name - there is no legacy name to
    forbid, only an argument that has to be there.
    """
    from property import repository as property_repository

    source = inspect.getsource(property_repository.list_visits_by_contact)
    assert "if len(args) == 1:" in source, source
    assert "agency_id = None" in source, source


def test_25_the_visit_call_really_carries_the_context():
    """Same overload hazard: `list_visits_by_contact(contact_id)` is a valid
    call that reads every agency's visits."""
    from crm import service as crm_service

    recorder = Recorder([])
    original = crm_service.list_visits_by_contact
    crm_service.list_visits_by_contact = recorder
    try:
        scope = ctx(A)
        with install_cursor(TenantCursor()):
            try:
                crm_service.get_contact_360(scope, CONTACT_A)
            except Exception:  # noqa: BLE001 - only the visit call is asserted
                pass
    finally:
        crm_service.list_visits_by_contact = original

    assert recorder.calls, "list_visits_by_contact was never called"
    args, _kwargs = recorder.calls[0]
    assert args[0] is scope, args[:1]
    assert args[1] == CONTACT_A, args


# ---------------------------------------------------------------------------
# 26-30 - THE FINAL BACKEND RECONNAISSANCE
#
# CRM was the last entry in G5's residual, but G5 measures one thing: routers
# mounted behind Basic that read a CORE table. It does not see routers that
# authenticate themselves, and it does not see `@app` routes declared in
# main.py at all. Closing GATE-MA1 needs the wider claim, so this section makes
# the wider claim testable.
#
# Every tenant-sensitive surface in the backend is classified:
#
#   A  operator/session scoped        core_router, via require_operator
#   B  legacy Basic but agency-scoped property, buy, match, crm, proposal,
#                                     sale, seller_intelligence, followup,
#                                     seller_intent, property_watch,
#                                     next_best_action
#   C  server/system, tenant derived  the public estimation funnel, whose rows
#                                     get their agency from 030/033 triggers,
#                                     and the *_for_all_agencies orchestrators
#   D  platform-global, legitimate    operator_auth, and the public
#                                     `contatore_oggi` marketing counter
#   E  unsafe / unscoped              enumerated below - and NOT empty
#
# E is frozen rather than described. A surface that leaves it is a deliberate
# spec change; one that appears is a regression, and this test is what says so.
# ---------------------------------------------------------------------------

# Basic-authenticated `@app` routes in main.py that read or write a tenant
# table with no agency predicate. All three tables involved already carry the
# boundary - `stime.agency_id` is NOT NULL since 033, and `stime_dettagliate`
# derives through `stima_id` - so every one of these is fixable in code. None
# of them needs a migration, and none is fixed here: P26-6C's mandate was CRM.
# Empty since P26-6C phase 1. All six took the compatibility context and their
# statements now name a tenant; tests 31-45 are the proof, and test 26 below
# recomputes this set from main.py's AST so it cannot be emptied by editing it.
FROZEN_UNSCOPED_APP_ROUTES: set[str] = set()

# Routers that authenticate themselves and are therefore invisible to G5.
# Each needs more than a predicate, which is why neither is touched here.
# FLOW left this set in P26-6C pass 2. All sixteen of its tenant routes now
# take the agency context, all twelve rules have a bounded scan built from the
# same definition as the global one, and 052-054 gave flow_events,
# flow_executions and flow_suppressions a physical agency_id with per-table
# triggers. The proof is tests/test_p26_6c_flow_isolation.py, and it is
# recomputed from the router's AST rather than granted by this list.
# OWNER left this set in P26-6C's OWNER pass. Emptying it is not a deletion: the two
# routers are admitted on two DIFFERENT criteria, and both are recomputed from
# the source by tests 46-50 rather than granted here.
#
#   owner_admin_router  - every route that reaches a tenant table takes
#                         `legacy_basic_agency_context`, and every repository
#                         function it calls takes `agency_id` as its first
#                         parameter with no default.
#   owner_portal_router - account-scoped by design and deliberately NOT given
#                         an operator context. Every route derives its account
#                         from `current_owner`, no route accepts an account or
#                         an agency from the client, and every query that
#                         follows `owner_property_access` requires the grant's
#                         two roots to name the same agency.
#
# The set is empty. What keeps it empty is tests 46-50; what keeps it honest is
# test 30, which now refuses to call GATE-MA1 closed on this evidence alone.
FROZEN_UNSCOPED_SELF_AUTH_ROUTERS: set[str] = set()

TENANT_TABLES = (
    "activities", "buy_requests", "contacts", "followup_actions", "leads",
    "next_best_actions", "properties", "property_watches",
    "seller_timeline_events", "stime", "tasks", "stime_dettagliate",
)

_EXECUTE = re.compile(
    r'cur\.execute\(\s*(?:f)?("""(?:.|\n)*?"""|"[^"]*"|\'[^\']*\')'
)


def _app_routes_with_tenant_sql():
    """Every `@app` route in main.py, with the tenant statements it issues."""
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    found = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        route = None
        dependencies = ""
        for decorator in node.decorator_list:
            call = decorator.func if isinstance(decorator, ast.Call) else decorator
            if (
                isinstance(call, ast.Attribute)
                and call.attr in ("get", "post", "put", "patch", "delete")
                and isinstance(call.value, ast.Name)
                and call.value.id == "app"
            ):
                route = call.attr
                if isinstance(decorator, ast.Call):
                    for keyword in decorator.keywords:
                        if keyword.arg == "dependencies":
                            dependencies = ast.unparse(keyword.value)
        if route is None:
            continue
        body = ast.get_source_segment(source, node) or ""
        unscoped = []
        for raw in _EXECUTE.findall(body):
            flat = " ".join(raw.strip('"\'').split())
            if not any(re.search(rf"\b{t}\b", flat, re.I) for t in TENANT_TABLES):
                continue
            if not re.search(r"agency_id", flat, re.I):
                unscoped.append(flat)
        if unscoped:
            found[node.name] = (dependencies, unscoped)
    return found


def test_26_the_unscoped_app_routes_in_main_are_exactly_the_known_ones():
    """Recomputed from main.py's AST every run, never from this list.

    The set is frozen so that it can only shrink deliberately. A seventh route
    reading `stime` without a predicate fails here rather than being absorbed.
    """
    found = _app_routes_with_tenant_sql()
    basic = {
        name for name, (dependencies, _) in found.items()
        if "require_admin" in dependencies
    }
    assert basic == FROZEN_UNSCOPED_APP_ROUTES, (
        f"the unscoped Basic app-route surface changed.\n"
        f"added:   {sorted(basic - FROZEN_UNSCOPED_APP_ROUTES)}\n"
        f"removed: {sorted(FROZEN_UNSCOPED_APP_ROUTES - basic)}"
    )


def test_27_the_public_funnel_routes_are_not_counted_as_a_tenant_surface():
    """They have no operator, and their rows get an agency from the triggers.

    Named explicitly so that "unscoped statement in main.py" is never read as
    "unsafe": the funnel is category C, and conflating it with the admin routes
    above would make the frozen set meaningless.
    """
    found = _app_routes_with_tenant_sql()
    public = {
        name for name, (dependencies, _) in found.items()
        if "require_admin" not in dependencies
    }
    assert public == {
        "salva_stima",         # INSERT carries agency_id; the two UPDATEs that
                               # follow are keyed on the id it returned
        "prefill",             # keyed on the single-use token
        "api_contatore_oggi",  # a platform-wide public count, category D
    }, sorted(public)
    # `salva_stima_dettagliata` used to be on this list. It has left it, not by
    # being reclassified but by changing: since 049 the detail row carries its
    # own agency, so the writer stamps one and the statement names a tenant.
    assert "salva_stima_dettagliata" not in public


def test_28_no_certified_router_regressed_out_of_full_scoping():
    """The eleven category-B routers, re-proved from their own ASTs.

    G5 asserts this for the ones that read CORE. Repeated here over all of
    them, because a router that stopped reading CORE would silently leave G5's
    measurement while still serving tenant data.
    """
    expected = {
        "property": 21, "buy": 23, "match": 26, "crm": 1, "proposal": 5,
        "sale": 6, "seller_intelligence": 2, "followup": 1, "seller_intent": 1,
        "property_watch": 12, "next_best_action": 3,
    }
    for package, count in expected.items():
        routes, scoped = _routes(package)
        assert len(routes) == count, (package, len(routes), sorted(routes))
        assert set(routes) == set(scoped), (
            package, sorted(set(routes) - set(scoped))
        )


def test_29_the_self_authenticating_routers_are_the_known_two_families():
    """FLOW and OWNER mount without `require_admin` and guard themselves.

    That is why G5 cannot see them, and why naming them here is the only thing
    that keeps them from being forgotten. Both are blockers, not oversights:
    see the comments on FROZEN_UNSCOPED_SELF_AUTH_ROUTERS.
    """
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    unguarded = set()
    # FLOW mounts itself behind `require_owner_admin`, so G5 still cannot see
    # it - but it is no longer unsafe, so it is excluded here by name rather
    # than by being absent.
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "include_router"
        ):
            continue
        symbol = ast.unparse(node.args[0]) if node.args else "?"
        dependencies = ""
        for keyword in node.keywords:
            if keyword.arg == "dependencies":
                dependencies = ast.unparse(keyword.value)
        if not any(
            guard in dependencies
            for guard in ("require_admin", "require_operator", "require_authenticated_operator")
        ):
            unguarded.add(symbol)

    # operator_auth mounts bare because it *is* the authentication surface; the
    # two OWNER routers guard themselves, the admin one with HTTP Basic and the
    # portal with its own cookie. All three are still invisible to G5 - that has
    # not changed - but none is unsafe any more, so they are excluded by name
    # and the residual set is empty.
    #
    # FLOW stays in this list, and for the same structural reason as before:
    # its guard is declared on its own APIRouter rather than on the mount, so
    # main.py includes it bare and G5 cannot see it. What P26-3 changed is
    # WHICH guard - it used to borrow OWNER Admin's Basic-only one and now
    # declares `require_authenticated_operator`, which also admits the operator
    # session the OS Shell carries. Same invisibility to G5, different and no
    # longer borrowed authentication.
    self_authenticating = {
        "operator_auth_router", "flow_router",
        "owner_admin_router", "owner_portal_router",
    }
    assert unguarded - self_authenticating == FROZEN_UNSCOPED_SELF_AUTH_ROUTERS, (
        sorted(unguarded - self_authenticating)
    )
    # And the four are actually the ones mounting unguarded: if one of them
    # started going through require_admin, this list would be stale.
    assert unguarded == self_authenticating, sorted(unguarded)


def test_30_category_e_is_empty_and_that_is_necessary_but_not_sufficient():
    """The gate's own rule, asserted rather than remembered.

    Category E is now empty: every tenant surface this suite can see derives
    its agency server-side. That is the NECESSARY condition for GATE-MA1, and
    it is the only one a static suite can establish.

    It is not sufficient, and this test says so in the one place someone will
    look. What is still missing is evidence no test in this repository can
    produce:

      * the platform-wide hostile A/B matrix on TEST, with two real agencies
        and two real operators - that is P26-6 - covering read, write,
        direct-id and relationship attempts across every domain;
      * the census of rows already stored: grants, feedback and notifications
        whose two roots disagree are now HIDDEN by every query, not repaired,
        and nobody has counted them;
      * batch and worker isolation; the OS Shell moving to the operator session
        (P26-4); and legacy Basic being confined or removed (P26-5).

    So: category E empty, GATE-MA1 still OPEN until the P26-6 matrix runs.
    Anyone who wants to close it has to change this test, and changing it means
    saying which of the three above has been done.
    """
    residual = FROZEN_UNSCOPED_APP_ROUTES | FROZEN_UNSCOPED_SELF_AUTH_ROUTERS
    assert residual == set(), (
        "category E is populated again: " + str(sorted(residual))
    )

    # The rule, stated as code so it cannot be misremembered: an empty residual
    # is what makes the gate *closable*, not what closes it.
    gate_may_close = not residual and LIVE_HOSTILE_MATRIX_PASSED
    assert not gate_may_close, (
        "GATE-MA1 would be closable - flip LIVE_HOSTILE_MATRIX_PASSED only "
        "when the P26-6 live A/B run on TEST has actually passed"
    )


# The one thing this repository cannot prove about itself. It is a constant
# rather than a comment so that test 30 can compute the gate's verdict instead
# of describing it, and so that flipping it is a visible, reviewable diff.
LIVE_HOSTILE_MATRIX_PASSED = False


# ===========================================================================
# PHASE 1 - THE SIX @app ADMIN ROUTES (tests 31-45)
#
# These are not routers, so GATE-MA1's G5 has never measured them and the
# certified per-module isolation suites do not reach them either. They open a
# raw psycopg2 connection through `database.get_connection` and write their own
# SQL, which is why the module-level fakes used everywhere else in P26 do not
# apply: the seam here is the connection, not a repository cursor.
#
# Three of the six mutate. `admin_update_stima` and the two delete routes took
# an id list straight from the request body and applied it with no tenant
# predicate at all, so agency A could rewrite or destroy agency B's rows by
# guessing an integer. Those are asserted on effect, not only on the statement.
# ===========================================================================

STIMA_A = 11
STIMA_B = 22
DETAIL_A = 111
DETAIL_B = 222


class MainCursor:
    """A raw-DBAPI double for main.py: tuples, `.description`, `.rowcount`.

    main.py reads `cur.description` to name its columns and returns tuples, so
    this cannot be the dict-row cursor the repositories use. It carries the
    same predicate-honouring rule as `TenantCursor`: a statement that names a
    tenant sees only that tenant's rows, one that does not sees both.
    """

    ROWS = {
        "stime": [
            {"id": STIMA_A, "agency_id": A, "nome": "AGENCY A", "cognome": "A",
             "email": "a@example.test", "telefono": "1", "data": None,
             "comune": "A", "microzona": "A", "via": "A", "civico": "1",
             "tipologia": "A", "mq": 1, "piano": "1", "locali": 1, "bagni": 1,
             "pertinenze": "", "ascensore": "", "consenso_marketing": False,
             "lead_status": "new", "note_internal": "", "data_dettaglio": None,
             "stima_id": STIMA_A, "from_number": "391", "text": "A",
             "direction": "in", "received_at": None},
            {"id": STIMA_B, "agency_id": B, "nome": "AGENCY B", "cognome": "B",
             "email": "b@example.test", "telefono": "2", "data": None,
             "comune": "B", "microzona": "B", "via": "B", "civico": "2",
             "tipologia": "B", "mq": 2, "piano": "2", "locali": 2, "bagni": 2,
             "pertinenze": "", "ascensore": "", "consenso_marketing": False,
             "lead_status": "new", "note_internal": "", "data_dettaglio": None,
             "stima_id": STIMA_B, "from_number": "392", "text": "B",
             "direction": "in", "received_at": None},
        ],
        "stime_dettagliate": [
            {"id": DETAIL_A, "agency_id": A, "stima_id": STIMA_A, "data": None,
             "nome": "AGENCY A"},
            {"id": DETAIL_B, "agency_id": B, "stima_id": STIMA_B, "data": None,
             "nome": "AGENCY B"},
        ],
    }

    def __init__(self):
        self.statements: list[tuple[str, object]] = []
        self.description = None
        self.rowcount = 0
        self._rows: list[dict] = []

    def execute(self, sql, params=None):
        flat = " ".join(str(sql).split())
        self.statements.append((flat, params))
        values = list(params or ())
        flattened = []
        for value in values:
            flattened.extend(value if isinstance(value, (list, tuple)) else [value])

        table = "stime_dettagliate" if "stime_dettagliate" in flat.lower() else "stime"
        rows = list(self.ROWS[table])

        if re.search(r"agency_id\s*=\s*%s", flat, re.IGNORECASE):
            asked = [v for v in flattened if v in (A, B)]
            rows = [r for r in rows if r["agency_id"] in asked]
        ids = [v for v in flattened if v in (STIMA_A, STIMA_B, DETAIL_A, DETAIL_B)]
        if ids:
            rows = [r for r in rows if r["id"] in ids]

        self._rows = rows
        self.rowcount = len(rows)
        self.description = [(key,) for key in (rows[0] if rows else {"id": None})]

    def fetchall(self):
        return [tuple(row.values()) for row in self._rows]

    def fetchone(self):
        return tuple(self._rows[0].values()) if self._rows else None

    def close(self):
        pass

    def touched_ids(self, verb: str) -> list[int]:
        """Which row ids a DELETE/UPDATE would actually have reached."""
        reached = []
        for flat, params in self.statements:
            if not flat.upper().startswith(verb):
                continue
            values = []
            for value in (params or ()):
                values.extend(value if isinstance(value, (list, tuple)) else [value])
            table = "stime_dettagliate" if "stime_dettagliate" in flat.lower() else "stime"
            asked = [v for v in values if v in (A, B)]
            candidates = self.ROWS[table]
            if re.search(r"agency_id\s*=\s*%s", flat, re.IGNORECASE) or "JOIN stime" in flat:
                candidates = [r for r in candidates if not asked or r["agency_id"] in asked]
            wanted = [v for v in values if v in (STIMA_A, STIMA_B, DETAIL_A, DETAIL_B)]
            reached += [r["id"] for r in candidates if not wanted or r["id"] in wanted]
        return reached


class MainConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.commits = 0

    def cursor(self, *a, **k):
        return self._cursor

    def commit(self):
        self.commits += 1

    def close(self):
        pass


@contextmanager
def install_main_connection(cursor):
    import main as main_module

    original = main_module.get_connection
    main_module.get_connection = lambda *a, **k: MainConnection(cursor)
    try:
        yield cursor
    finally:
        main_module.get_connection = original


def _main_client(monkeypatch):
    """main.py's real app, with only the agency dependency overridden."""
    import main as main_module

    monkeypatch.setenv("ADMIN_USER", "giorgio")
    monkeypatch.setenv("ADMIN_PASS", "test-secret")
    main_module.app.dependency_overrides[
        main_module.legacy_basic_agency_context
    ] = lambda: ctx(A)
    client = TestClient(main_module.app, raise_server_exceptions=False)
    yield_client = client
    return yield_client


ADMIN_ROUTE_HANDLERS = (
    "admin_lista_stime",
    "admin_lista_stime_pro",
    "admin_update_stima",
    "admin_delete_stime",
    "admin_delete_stime_dettagliate",
    "admin_whatsapp_messages",
)


def test_31_all_six_admin_routes_declare_the_agency_dependency():
    """Declared as a parameter, not in `dependencies=`.

    D-1 forbids these routes gaining an operator-session dependency, and G5
    asserts that separately; the compatibility context is a different thing and
    is what they need. Reading the signature rather than the decorator keeps
    those two facts from being confused.
    """
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    found = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name not in ADMIN_ROUTE_HANDLERS:
            continue
        declared = False
        for default in list(node.args.defaults) + list(node.args.kw_defaults):
            if (
                isinstance(default, ast.Call)
                and isinstance(default.func, ast.Name)
                and default.func.id == "Depends"
                and any(
                    isinstance(a, ast.Name) and a.id == AGENCY_DEPENDENCY
                    for a in default.args
                )
            ):
                declared = True
        found[node.name] = declared
    assert set(found) == set(ADMIN_ROUTE_HANDLERS), sorted(found)
    missing = sorted(name for name, ok in found.items() if not ok)
    assert not missing, f"admin routes without {AGENCY_DEPENDENCY}: {missing}"


def test_32_every_admin_statement_names_a_tenant_or_reaches_one_by_join():
    """`stime` carries the predicate itself; `stime_dettagliate` may instead
    reach it through its parent, which is the only other admissible form."""
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name not in ADMIN_ROUTE_HANDLERS:
            continue
        body = ast.get_source_segment(source, node) or ""
        for raw in _EXECUTE.findall(body):
            flat = " ".join(raw.strip('"\'').split())
            if not any(re.search(rf"\b{t}\b", flat, re.I) for t in TENANT_TABLES):
                continue
            assert re.search(r"agency_id", flat, re.I), (node.name, flat[:160])


def test_33_the_stime_list_shows_only_this_agency(monkeypatch):
    client = _main_client(monkeypatch)
    cursor = MainCursor()
    with install_main_connection(cursor):
        response = client.get("/api/admin/stime", auth=("giorgio", "test-secret"))
    assert response.status_code == 200, response.text
    body = response.text
    assert "AGENCY A" in body, body[:200]
    assert "AGENCY B" not in body, body[:400]


def test_34_the_detailed_stima_list_shows_only_this_agency(monkeypatch):
    client = _main_client(monkeypatch)
    cursor = MainCursor()
    with install_main_connection(cursor):
        response = client.get("/api/admin/stime_pro", auth=("giorgio", "test-secret"))
    assert response.status_code == 200, response.text
    assert "AGENCY B" not in response.text, response.text[:400]


def test_35_an_update_cannot_reach_another_agencys_stima(monkeypatch):
    """Asserted on which rows the UPDATE would have matched, not on the reply.

    The route answers `{"ok": True}` either way - it never reported a row
    count - so a test that only read the response body would have passed
    against the unscoped statement it was written to catch.
    """
    client = _main_client(monkeypatch)
    cursor = MainCursor()
    with install_main_connection(cursor):
        response = client.post(
            f"/api/admin/stime/{STIMA_B}/update",
            json={"lead_status": "hijacked"},
            auth=("giorgio", "test-secret"),
        )
    assert response.status_code in (200, 404), response.text
    assert STIMA_B not in cursor.touched_ids("UPDATE"), cursor.statements


def test_36_a_delete_cannot_reach_another_agencys_stima(monkeypatch):
    client = _main_client(monkeypatch)
    cursor = MainCursor()
    with install_main_connection(cursor):
        response = client.post(
            "/api/admin/stime/delete",
            json={"ids": [STIMA_A, STIMA_B]},
            auth=("giorgio", "test-secret"),
        )
    assert response.status_code == 200, response.text
    reached = cursor.touched_ids("DELETE")
    assert STIMA_B not in reached, cursor.statements
    assert DETAIL_B not in reached, cursor.statements


def test_37_a_detail_delete_cannot_bypass_the_parent_scope(monkeypatch):
    """`stime_dettagliate` is deleted by its own id, so the tenant has to come
    from somewhere else: either its own column or a join to the parent stima.
    Either way agency B's detail row must not be reachable by guessing 222."""
    client = _main_client(monkeypatch)
    cursor = MainCursor()
    with install_main_connection(cursor):
        response = client.post(
            "/api/admin/stime_dettagliate/delete",
            json={"ids": [DETAIL_A, DETAIL_B]},
            auth=("giorgio", "test-secret"),
        )
    assert response.status_code == 200, response.text
    assert DETAIL_B not in cursor.touched_ids("DELETE"), cursor.statements


def test_38_whatsapp_never_names_another_agencys_lead(monkeypatch):
    client = _main_client(monkeypatch)
    cursor = MainCursor()
    with install_main_connection(cursor):
        response = client.get(
            "/api/admin/whatsapp/messages", auth=("giorgio", "test-secret")
        )
    assert response.status_code == 200, response.text
    assert "AGENCY B" not in response.text, response.text[:400]


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("get", "/api/admin/stime", None),
        ("get", "/api/admin/stime_pro", None),
        ("get", "/api/admin/whatsapp/messages", None),
        ("post", f"/api/admin/stime/{STIMA_A}/update", {"lead_status": "x"}),
        ("post", "/api/admin/stime/delete", {"ids": [STIMA_A]}),
        ("post", "/api/admin/stime_dettagliate/delete", {"ids": [DETAIL_A]}),
    ],
)
def test_39_to_44_the_admin_routes_stay_behind_basic(monkeypatch, method, path, body):
    """The auth model does not change in this slice: still Basic, still 401."""
    client = _main_client(monkeypatch)
    response = getattr(client, method)(path, **({"json": body} if body else {}))
    assert response.status_code == 401, (path, response.status_code)


def test_45_no_admin_route_accepts_a_client_supplied_agency():
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name not in ADMIN_ROUTE_HANDLERS:
            continue
        names = [a.arg for a in node.args.args + node.args.kwonlyargs]
        assert "agency_id" not in names, (node.name, names)
    from main import DeleteRequest, LeadUpdate

    for model in (DeleteRequest, LeadUpdate):
        assert "agency_id" not in model.model_fields, model


# ===========================================================================
# OWNER PASS - WHY THE TWO OWNER ROUTERS ARE ADMITTED (tests 46-50)
#
# Emptying FROZEN_UNSCOPED_SELF_AUTH_ROUTERS is a claim, and a set with nothing
# in it makes no claim at all. These five tests are the claim: they recompute,
# from the source, the property that admits each router - and they do it
# inter-procedurally, because the tenant predicate usually lives one call down,
# in a helper, or in a module constant spliced into the SQL.
#
# Two routers, two DIFFERENT criteria, deliberately not merged:
#
#   owner_admin_router   an operator surface. Every route that reaches a tenant
#                        table takes `legacy_basic_agency_context`, and every
#                        repository function it calls takes `agency_id` first,
#                        with no default, so forgetting the tenant is a
#                        TypeError rather than an unfiltered query.
#
#   owner_portal_router  an owner surface, and NOT an operator one. It has no
#                        agency context by design; its identity is the session
#                        cookie. What admits it is that no route accepts an
#                        account or an agency from the client, and that every
#                        query following `owner_property_access` requires the
#                        grant's two roots to name the same agency.
#
# Collapsing the two into one rule would be worse than leaving them apart: the
# portal would then have to grow an operator context it must not have.
# ===========================================================================

OWNER_REPOSITORY = ROOT / "owner" / "repository.py"
OWNER_LOOKUPS = ROOT / "owner" / "admin_lookup_repository.py"

OWNER_TENANT_TABLES = frozenset({
    "contacts", "leads", "activities", "tasks",
    "properties", "property_contacts", "property_leads",
    "property_documents", "property_visits",
    "owner_accounts", "owner_property_access", "owner_access_tokens",
    "owner_sessions", "owner_publications", "owner_publication_reads",
    "owner_feedback", "owner_audit_log", "owner_shared_documents",
    "owner_document_reads", "owner_visit_feedback_publications",
    "owner_notifications", "owner_notification_preferences",
})

_SQL_SOURCE = re.compile(r"(?:FROM|JOIN|INTO|UPDATE)\s+([a-z_]+)", re.IGNORECASE)
_TENANT_PARAMETER = re.compile(r"agency_id\s*=\s*%s", re.IGNORECASE)
_GRANT_ROOTS_AGREE = re.compile(r"ct_g\.agency_id\s*=\s*p_g\.agency_id", re.IGNORECASE)


def _module_index(path):
    """(function bodies by name, module-level constant text, ast tree)."""
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    bodies = {
        node.name: (ast.get_source_segment(text, node) or "")
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    constants = "\n".join(
        ast.get_source_segment(text, node) or ""
        for node in tree.body
        if isinstance(node, ast.Assign)
    )
    return bodies, constants, tree


def _reachable_source(name, bodies, constants, seen=None):
    """The function's body plus every module function it calls, transitively.

    Also splices in any module-level constant it names, because the OWNER
    portal builds three of its predicates from `_COHERENT_GRANT` and
    `_GRANT_ROOTS_AGREE` rather than writing them inline. Without this the
    analysis would report nine unscoped portal queries that are in fact
    constrained - which is exactly the mistake a first pass made.
    """
    seen = seen if seen is not None else set()
    if name in seen or name not in bodies:
        return ""
    seen.add(name)
    body = bodies[name]
    collected = [body]
    for called in set(re.findall(r"\b(_?[a-z][a-z0-9_]*)\s*\(", body)):
        if called in bodies and called != name:
            collected.append(_reachable_source(called, bodies, constants, seen))
    for constant in set(re.findall(r"\b(_[A-Z][A-Z0-9_]*)\b", body)):
        found = re.search(rf"^{constant}\s*=.*?(?=^\w|\Z)", constants, re.M | re.S)
        if found:
            collected.append(found.group(0))
    return "\n".join(collected)


def _tenant_tables_reached(source):
    return {t.lower() for t in _SQL_SOURCE.findall(source)} & OWNER_TENANT_TABLES


_AUDIT_SELECT = re.compile(r"SELECT(?:.|\n)*?FROM\s+owner_audit_log", re.IGNORECASE)


def _writes_only_the_audit_log(source):
    """True for a helper that appends to owner_audit_log and reads nothing.

    A derived property, not a name: the only tenant table it reaches is the
    audit log, and it never SELECTs from it. Such a helper cannot return
    another agency's data, and the account and property it records come from an
    item the route has already authorised - so requiring an agency it would
    only pass through would be ceremony, not a control.

    Written as a shape so it covers the four `audit_*_denied` siblings too, and
    so that widening it - a SELECT appearing, or a second tenant table - takes
    the exemption away by itself. Test 51 is the regression for that.
    """
    return (
        _tenant_tables_reached(source) == {"owner_audit_log"}
        and not _AUDIT_SELECT.search(source)
    )


def _routes_with_repository_calls(path):
    """{route name: (repository functions it calls, its own source)}."""
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    routes = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not any(
            isinstance(decorator, ast.Call)
            and getattr(decorator.func, "attr", None) in {"get", "post", "patch", "put", "delete"}
            for decorator in node.decorator_list
        ):
            continue
        body = ast.get_source_segment(text, node) or ""
        routes[node.name] = (set(re.findall(r"\br\.([a-z_]+)", body)), body)
    return routes


def _takes_agency_first(name, tree):
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            arguments = node.args.args
            if not arguments or arguments[0].arg != "agency_id":
                return False
            defaults = node.args.defaults
            # A default on the first parameter would mean it can be omitted.
            return len(defaults) < len(arguments)
    return True          # not a repository function; nothing to require


def test_46_every_owner_admin_route_that_reaches_a_tenant_table_takes_the_context():
    """Derived, not listed. The set of routes needing a context is computed
    from what their repository calls actually touch."""
    admin_bodies, admin_constants, admin_tree = _module_index(OWNER_REPOSITORY)
    lookup_bodies, lookup_constants, lookup_tree = _module_index(OWNER_LOOKUPS)

    unscoped = []
    for path, bodies, constants in (
        (ROOT / "owner" / "router_admin.py", admin_bodies, admin_constants),
        (ROOT / "owner" / "router_admin_lookups.py", lookup_bodies, lookup_constants),
    ):
        for route, (calls, route_source) in sorted(_routes_with_repository_calls(path).items()):
            reaches_tenant = any(
                _tenant_tables_reached(reachable)
                and not _writes_only_the_audit_log(reachable)
                for fn in calls
                for reachable in [_reachable_source(fn, bodies, constants)]
            )
            if not reaches_tenant:
                continue
            if "basic_only_agency_context" not in route_source:
                unscoped.append((route, sorted(calls)))
    assert unscoped == [], unscoped


def test_47_every_owner_admin_repository_function_requires_the_agency_first():
    """A default, or a tenant in second place, would let a caller omit it."""
    admin_bodies, admin_constants, admin_tree = _module_index(OWNER_REPOSITORY)
    lookup_bodies, lookup_constants, lookup_tree = _module_index(OWNER_LOOKUPS)

    offenders = []
    for path, bodies, constants, tree in (
        (ROOT / "owner" / "router_admin.py", admin_bodies, admin_constants, admin_tree),
        (ROOT / "owner" / "router_admin_lookups.py", lookup_bodies, lookup_constants, lookup_tree),
    ):
        for route, (calls, _) in sorted(_routes_with_repository_calls(path).items()):
            for fn in sorted(calls):
                if fn not in bodies:
                    continue
                reachable = _reachable_source(fn, bodies, constants)
                if not _tenant_tables_reached(reachable):
                    continue
                if _writes_only_the_audit_log(reachable):
                    continue
                if not _takes_agency_first(fn, tree):
                    offenders.append((route, fn))
    assert offenders == [], offenders


def test_48_the_owner_portal_takes_no_agency_and_no_account_from_the_client():
    """The portal's identity is the cookie. A route parameter named for an
    account or an agency would be a second, client-controlled identity."""
    text = (ROOT / "owner" / "router_portal.py").read_text(encoding="utf-8")
    assert "legacy_basic_agency_context" not in text
    assert "OperatorContext" not in text

    tree = ast.parse(text)
    routed, session_bound, offenders = set(), set(), []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not any(
            isinstance(decorator, ast.Call)
            and getattr(decorator.func, "attr", None) in {"get", "post", "patch", "put", "delete"}
            for decorator in node.decorator_list
        ):
            continue
        routed.add(node.name)
        for argument in node.args.args:
            if argument.arg in {"agency_id", "agency", "owner_account_id", "account_id", "account"}:
                offenders.append((node.name, argument.arg))
        for default in node.args.defaults:
            if (
                isinstance(default, ast.Call)
                and getattr(default.func, "id", None) == "Depends"
                and default.args
                and getattr(default.args[0], "id", None) == "current_owner"
            ):
                session_bound.add(node.name)

    assert offenders == [], offenders
    # Only the two routes that cannot have a session yet are exempt.
    assert routed - session_bound == {"login", "logout"}, sorted(routed - session_bound)


def test_49_every_portal_query_that_follows_a_grant_requires_the_two_roots_to_agree():
    """The portal's whole authorisation model is the grant, so a grant whose
    two roots disagree must not be followable. Computed through the helpers and
    the module constants, because that is where the predicate lives."""
    bodies, constants, _ = _module_index(OWNER_REPOSITORY)

    offenders = []
    for route, (calls, _) in sorted(_routes_with_repository_calls(ROOT / "owner" / "router_portal.py").items()):
        for fn in sorted(calls):
            if fn not in bodies:
                continue
            reachable = _reachable_source(fn, bodies, constants)
            if "owner_property_access" not in reachable:
                continue
            if not _GRANT_ROOTS_AGREE.search(reachable):
                offenders.append((route, fn))
    assert offenders == [], offenders


def test_50_the_two_routers_are_admitted_on_two_different_criteria():
    """Stated as an assertion so the distinction cannot quietly collapse.

    If the portal ever acquired an operator context it would stop being an
    owner surface; if the admin router ever lost one it would stop being an
    operator surface. Both are failures, and neither is caught by the other
    router's test.
    """
    admin = (ROOT / "owner" / "router_admin.py").read_text(encoding="utf-8")
    portal = (ROOT / "owner" / "router_portal.py").read_text(encoding="utf-8")

    assert "basic_only_agency_context" in admin
    assert "basic_only_agency_context" not in portal
    assert "current_owner" in portal
    assert "current_owner" not in admin

    # OWNER Admin's own mount is unchanged, and the portal's cookie is still
    # the owner's only credential.
    assert "dependencies=[Depends(require_owner_admin)]" in admin
    assert 'realm="STIMA360 OWNER Admin"' in admin

    # P26-3 review, and the reason `basic_only_agency_context` exists at all.
    # This mount admits HTTP Basic and nothing else. If its routes took the
    # session-first scope dependency, a caller holding both a cookie and Basic
    # would be ADMITTED by one credential and SCOPED by the other - the mount
    # would say "the shared admin secret" and the queries would say "whichever
    # agency the cookie belongs to". Not wider, not narrower: incoherent. One
    # surface, one channel, and this is the assertion that keeps it that way.
    lookups = (ROOT / "owner" / "router_admin_lookups.py").read_text(encoding="utf-8")
    for name, source in (("router_admin.py", admin), ("router_admin_lookups.py", lookups)):
        assert "legacy_basic_agency_context" not in source, (
            f"{name} took the session-first scope dependency while its mount "
            "still accepts only HTTP Basic - that is the cookie+Basic hybrid"
        )

    # P26-3 removed FLOW's borrowing of that dependency. Asserted here because
    # this test is where the coupling was recorded: a change to OWNER Admin's
    # authentication used to change FLOW's silently, and no longer can.
    flow = (ROOT / "flow" / "router.py").read_text(encoding="utf-8")
    assert "require_owner_admin" not in flow
    assert "dependencies=[Depends(require_authenticated_operator)]" in flow


def test_51_the_audit_write_exemption_is_a_shape_and_not_a_list():
    """It must apply to exactly the helpers that append and read nothing.

    Two directions. A helper that only writes the audit log is exempt - and the
    four that do are found by the shape, never named. A helper that reads any
    tenant table, including the audit log itself, is not: `audits` reads it and
    is agency-bound, and would be caught if it ever lost its predicate.
    """
    bodies, constants, tree = _module_index(OWNER_REPOSITORY)

    exempt = {
        name for name in bodies
        if _writes_only_the_audit_log(_reachable_source(name, bodies, constants))
    }
    # Every exempt helper writes the audit log and nothing else.
    assert exempt, "the exemption matches nothing - the shape is wrong"
    for name in exempt:
        reachable = _reachable_source(name, bodies, constants)
        assert "INSERT INTO owner_audit_log" in reachable, name
        assert _tenant_tables_reached(reachable) == {"owner_audit_log"}, name

    # The reader of the audit log is NOT exempt, and is agency-bound.
    assert "audits" not in exempt
    assert _takes_agency_first("audits", tree)
    audits_source = _reachable_source("audits", bodies, constants)
    assert _TENANT_PARAMETER.search(audits_source), audits_source

    # And no function that touches a second tenant table can slip in.
    for name in exempt:
        reachable = _reachable_source(name, bodies, constants)
        assert "owner_property_access" not in reachable, name
        assert "owner_shared_documents" not in reachable, name
