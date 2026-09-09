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

    source = inspect.getsource(dependencies.legacy_basic_agency_context)
    assert "resolve_default_agency_id" in source, source
    assert "is_platform_admin=False" in source.replace(" ", ""), source
    assert not re.search(r"request\.(query_params|headers|json)", source), source


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
FROZEN_UNSCOPED_SELF_AUTH_ROUTERS = {
    # OWNER has no tenant at all: not one owner_* table carries an agency_id,
    # and `lookup_contacts` reads every agency's contacts by name and email.
    # Scoping it requires deciding whether an owner account belongs to an
    # agency or to the platform, and then a migration to record the answer.
    # P26-6C pass 2 touched OWNER only where FLOW's event write needed a tenant
    # OWNER already knew - a bridge argument, not a migration.
    "owner_admin_router",
    "owner_portal_router",
}

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
        if "require_admin" not in dependencies and "require_operator" not in dependencies:
            unguarded.add(symbol)

    # operator_auth mounts bare because it *is* the authentication surface.
    assert unguarded - {"operator_auth_router", "flow_router"} == FROZEN_UNSCOPED_SELF_AUTH_ROUTERS, (
        sorted(unguarded - {"operator_auth_router", "flow_router"})
    )


def test_30_gate_ma1_cannot_be_declared_closed_while_category_e_is_populated():
    """The gate's own rule, asserted rather than remembered.

    G5's residual going empty is a statement about routers that read CORE over
    Basic. It is not the same claim as "no unscoped tenant surface remains",
    and this test exists so the two are never confused in either direction: if
    someone empties the two frozen sets above without doing the work, this
    fails; if they do the work, this is the test that has to be updated to say
    the gate may close.
    """
    residual = FROZEN_UNSCOPED_APP_ROUTES | FROZEN_UNSCOPED_SELF_AUTH_ROUTERS
    assert residual, "category E is empty - GATE-MA1 may now be re-evaluated"
    # Two OWNER routers remain. Pass 1 emptied FROZEN_UNSCOPED_APP_ROUTES and
    # pass 2 removed FLOW; these are what is left, and while they are here the
    # gate stays OPEN.
    assert residual == FROZEN_UNSCOPED_SELF_AUTH_ROUTERS, sorted(residual)
    assert residual == {"owner_admin_router", "owner_portal_router"}, sorted(residual)


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
