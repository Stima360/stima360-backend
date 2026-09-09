"""P26-6B - INTELLIGENCE agency isolation (P20-P24).

    P20 Property Watch -> P21 Buyer Pressure -> P22 Invisible Sale
    P23 Next Best Action -> P24 Database Revival

Offline. The application tests drive the real repositories against an
agency-aware fake cursor; the migration tests read SQL text.

OWNERSHIP, VERIFIED AGAINST THE REAL SCHEMA

Two tables get a physical `agency_id`, and each earns it the same way the P26-6A
logs did - not by preference, but because the schema lets a row outlive the
parent it would otherwise derive from:

    property_watches   stima_id is NULLABLE, ON DELETE SET NULL (022)
    next_best_actions  contact_id / lead_id / stima_id all nullable,
                       ON DELETE SET NULL, and the subject is polymorphic (024)

Everything else derives, because every one of them has a mandatory parent:

    property_watch_observations    watch_id NOT NULL
    invisible_sale_opportunities   watch_id NOT NULL
    invisible_sale_candidates      opportunity_id + buy_request_id, both NOT NULL
    invisible_sale_events          opportunity_id NOT NULL, candidate_id optional
    seller_revival_suppressions    contact_id NOT NULL, ON DELETE CASCADE

TWO INVARIANTS THAT ARE NOT ABOUT TENANCY

`invisible_sale_candidates` has two mandatory parents that can disagree, so the
opportunity's agency and the buy request's agency must match - a cross-agency
candidate is a tenancy breach.

`invisible_sale_events.candidate_id` is stricter than that: when it is set, the
candidate must belong to *exactly* the event's opportunity. Same-agency is not
enough - an event citing a sibling opportunity's candidate would attribute one
opportunity's decision to another, inside a single tenant.

THE QUIET LEAKS

Three of the five gaps in this slice return no foreign row at all, and would
pass any test that only checked what came back:

    internal supply    counts agency B's properties into agency A's zone metric
    buyer pressure     enumerates every active buy request in the database
    NBA refresh        prunes by key, so agency A's refresh deletes agency B's
                       rows

Those are asserted on the SQL that is issued, not on the rows returned.

Coverage map:

    1-6     structure
    7-15    property watch
    16-18   internal signals
    19-23   buyer pressure
    24-36   invisible sale
    37-50   next best action and its signal collectors
    51-64   database revival
    65-86   migrations 046 / 047 / 048
"""
from __future__ import annotations

import ast
import inspect
import re
from contextlib import contextmanager
from pathlib import Path

import pytest

from operator_auth.context import OperatorContext
from operator_auth.exceptions import PlatformAdminAgencyRequired

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"

AGENCY_DEPENDENCY = "legacy_basic_agency_context"

A, B = 71, 82          # two agencies; deliberately not 1

COLUMNS = "046_p26_intelligence_agency_columns"
BACKFILL = "047_p26_intelligence_agency_backfill"
ENFORCE = "048_p26_intelligence_agency_enforce"

# The only two tables this slice gives a physical column.
OWNED_TABLES = ("property_watches", "next_best_actions")
# Everything that must stay derived.
DERIVED_TABLES = (
    "property_watch_observations",
    "invisible_sale_opportunities",
    "invisible_sale_candidates",
    "invisible_sale_events",
    "seller_revival_suppressions",
)

EXPECTED_NBA_ROUTES = 3


def ctx(agency_id=A) -> OperatorContext:
    return OperatorContext(
        user_id=None,
        agency_id=agency_id,
        role="agency_owner" if agency_id is not None else "platform_admin",
        is_platform_admin=agency_id is None,
        session_id=None,
        auth_channel="legacy_basic",
    )


def unbound() -> OperatorContext:
    return OperatorContext(
        user_id=9, agency_id=None, role="platform_admin",
        is_platform_admin=True, session_id=1, auth_channel="operator_session",
    )


# ---------------------------------------------------------------------------
# An agency-aware fake cursor. It honours the predicate the SQL carries, so a
# query that forgot one SEES the foreign row and its test fails - rather than
# passing because a canned response happened to be empty.
# ---------------------------------------------------------------------------

class ScopeCursor:
    def __init__(self, *, owner=B, rows=None):
        self.owner = owner
        self.rows = list(rows or [])
        self.statements: list[tuple[str, object]] = []
        self.current = None

    def execute(self, sql, params=None):
        flat = " ".join(str(sql).split())
        self.statements.append((flat, params))
        values = list(params.values()) if isinstance(params, dict) else list(params or [])
        scoped = bool(re.search(r"agency_id\s*=\s*%s", flat, re.IGNORECASE))
        asked = [v for v in values if v in (A, B)]

        if self.rows:
            self.current = self.rows.pop(0)
            return
        if scoped and self.owner not in asked:
            self.current = None
            return
        self.current = self._payload()

    def _payload(self):
        return {
            "id": 1, "agency_id": self.owner, "watch_id": 1, "stima_id": 1,
            "contact_id": 1, "lead_id": 1, "buy_request_id": 1,
            "opportunity_id": 1, "candidate_id": 1, "subject_type": "lead",
            "subject_id": 1, "status": "active", "n": 0, "count": 0,
            "comune": "Alba", "microzona": "Centro", "payload": {},
            "observation_type": "watch_started", "idempotency_key": "k",
            "collection_time": None, "supply_count": 0, "eligible": False,
        }

    def fetchone(self):
        return self.current

    def fetchall(self):
        return [self.current] if self.current else []

    def sql_of(self, needle: str) -> list[tuple[str, object]]:
        return [s for s in self.statements if needle.lower() in s[0].lower()]


@contextmanager
def _install(module, attribute, cursor):
    original = getattr(module, attribute)

    @contextmanager
    def fake(*_a, **_k):
        yield None, cursor

    setattr(module, attribute, fake)
    try:
        yield cursor
    finally:
        setattr(module, attribute, original)


def _run(module, attribute, fn, *args, owner=B, rows=None, **kwargs):
    cur = ScopeCursor(owner=owner, rows=rows)
    with _install(module, attribute, cur):
        try:
            return fn(*args, **kwargs), cur
        except Exception as exc:  # noqa: BLE001 - type asserted by callers
            return exc, cur


def _executable_source(function) -> str:
    """Source with the docstring removed.

    These rules ban tokens like `LIMIT 1` and `MIN(`, and the functions'
    docstrings explain why. Without stripping, the explanation fails the rule it
    explains - a trap this project has hit repeatedly.
    """
    tree = ast.parse(inspect.getsource(function).lstrip())
    node = tree.body[0]
    if (
        node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    ):
        node.body = node.body[1:]
    return ast.unparse(node)


def _sql_literals(function) -> list[str]:
    """Every SQL string a function issues, with f-string parts kept.

    Reads the AST rather than the raw text so a fragment interpolated from a
    module constant is still seen as part of the statement - several earlier
    probes in this project reported a leak that was really an unresolved
    `{_SCOPE}` placeholder.
    """
    module = inspect.getmodule(function)
    source = _executable_source(function)
    for name in dir(module):
        if name.startswith("_") and name.isupper() is False:
            continue
        value = getattr(module, name, None)
        if isinstance(value, str) and ("SELECT" in value or "JOIN" in value or "agency_id" in value):
            source = source.replace("{" + name + "}", value)
    out = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            flat = " ".join(node.value.split())
            if re.match(r"^(SELECT|INSERT|UPDATE|DELETE|WITH)\b", flat, re.IGNORECASE):
                out.append(flat)
        elif isinstance(node, ast.JoinedStr):
            flat = " ".join(ast.unparse(node).split())
            if re.search(r"\b(SELECT|INSERT|UPDATE|DELETE|WITH)\b", flat, re.IGNORECASE):
                out.append(flat)
    return out


def _assert_all_sql_scoped(function, *, tables: tuple[str, ...]) -> None:
    """Every statement touching one of these tables must carry a tenant predicate."""
    for statement in _sql_literals(function):
        if not any(re.search(rf"\b{t}\b", statement, re.IGNORECASE) for t in tables):
            continue
        assert re.search(r"agency_id\s*=\s*%s", statement, re.IGNORECASE), (
            f"{function.__qualname__} issues an unscoped statement: {statement[:180]}"
        )


# ---------------------------------------------------------------------------
# 1-6 - structure
# ---------------------------------------------------------------------------

def _routes(package: str) -> tuple[list[str], list[str]]:
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


def test_1_property_watch_route_inventory():
    """The count is read from the router, not asserted from the brief."""
    routes, _ = _routes("property_watch")
    assert len(routes) == 12, sorted(routes)


def test_2_every_property_watch_route_is_scoped():
    routes, scoped = _routes("property_watch")
    missing = sorted(set(routes) - set(scoped))
    assert not missing, f"routes without {AGENCY_DEPENDENCY}: {missing}"


def test_3_nba_route_inventory():
    routes, _ = _routes("next_best_action")
    assert len(routes) == EXPECTED_NBA_ROUTES, sorted(routes)


def test_4_every_nba_route_is_scoped():
    routes, scoped = _routes("next_best_action")
    missing = sorted(set(routes) - set(scoped))
    assert not missing, f"routes without {AGENCY_DEPENDENCY}: {missing}"


@pytest.mark.parametrize("package", ("property_watch", "next_best_action"))
def test_4b_both_intelligence_routers_answer_the_unbound_admin_the_same_way(package):
    """403, in both, or one authorization outcome depends on which router was hit.

    Neither can reach this state over legacy Basic, where the dependency
    resolves the Default Agency server-side. It is asserted because these are
    the routes an operator-session mount reaches first, and because a 500 for
    a legitimate refusal is a different bug in each router.
    """
    source = (ROOT / package / "router.py").read_text(encoding="utf-8")
    assert "PlatformAdminAgencyRequired" in source, package
    assert "status_code=403" in source, package
    import importlib

    from pydantic import BaseModel

    found = False
    for name in (f"{package}.schemas", f"{package}.router"):
        try:
            module = importlib.import_module(name)
        except ModuleNotFoundError:
            continue
        for attribute, obj in vars(module).items():
            if isinstance(obj, type) and issubclass(obj, BaseModel) and obj is not BaseModel:
                found = True
                assert "agency_id" not in getattr(obj, "model_fields", {}), (name, attribute)
    assert found, f"no request models found for {package}"


# Every entry is a `ctx`-taking seam: the ctx is the only thing that can carry
# an agency, so it is the only kind of function that can be asked to fail
# closed. `_for_agency` helpers take an int that a caller already resolved, and
# the `database_revival.repository` helpers take a live cursor - neither is
# reachable by an unbound admin without passing through one of these first.
UNBOUND_SURFACES = [
    ("property_watch.repository", "get_watch_for_stima_scoped", (1,)),
    ("property_watch.repository", "ensure_watch_with_baseline_scoped", (1, {})),
    ("property_watch.invisible_sale_repository", "list_active_watch_refs_scoped", ()),
    ("property_watch.invisible_sale_repository", "get_invisible_sale_for_stima_scoped", (1,)),
    ("next_best_action.repository", "list_current_scoped", (10,)),
    ("next_best_action.repository", "get_current_scoped", ("lead", 1)),
    ("next_best_action.repository", "replace_current_actions_scoped", ([],)),
    ("database_revival.service", "ensure_today_batch_scoped", ()),
    ("database_revival.service", "collect_today_signals_scoped", ()),
]


@pytest.mark.parametrize("module_name,function,args", UNBOUND_SURFACES,
                         ids=[f"{m.split('.')[-2]}.{f}" for m, f, _ in UNBOUND_SURFACES])
def test_6_unbound_platform_admin_fails_closed(module_name, function, args):
    """A platform admin holding no membership cannot reach tenant data.

    The refusal must precede the query: `require_agency()` runs before any
    cursor opens, so nothing is read and then discarded.
    """
    import importlib

    module = importlib.import_module(module_name)
    attribute = next(
        a for a in ("property_watch_cursor", "next_best_action_cursor",
                    "database_revival_cursor")
        if hasattr(module, a)
    )
    fn = getattr(module, function)
    result, cur = _run(module, attribute, fn, unbound(), *args)
    assert isinstance(result, PlatformAdminAgencyRequired), (function, result)
    assert cur.statements == [], f"{function} queried before refusing"


# ---------------------------------------------------------------------------
# 7-15 - PROPERTY WATCH
#
# The watch is the root of P20-P22: everything downstream is reached through
# one, so a watch resolved out of scope would carry the breach into the
# observations, the pressure snapshot and the invisible-sale opportunity
# without any of those needing a mistake of their own.
# ---------------------------------------------------------------------------

def test_7_ensure_watch_resolves_the_stima_in_scope_before_writing():
    """A foreign stima is not found rather than watched, and nothing is written."""
    from property_watch import repository as pw_repository

    result, cur = _run(
        pw_repository, "property_watch_cursor",
        pw_repository.ensure_watch_with_baseline_scoped, ctx(A), 1, {},
        owner=B,
    )
    assert isinstance(result, pw_repository.StimaNotFoundError), result
    assert len(cur.statements) == 1, [s[0][:80] for s in cur.statements]
    first = cur.statements[0][0]
    assert re.search(r"\bFROM stime\b", first, re.IGNORECASE), first
    assert re.search(r"agency_id\s*=\s*%s", first, re.IGNORECASE), first
    assert not cur.sql_of("INSERT INTO property_watches")


def test_8_ensure_watch_stamps_the_agency_on_the_inserted_watch():
    """Asserted on the value bound, not on the column name appearing.

    A column list is text: `agency_id_x` contains `agency_id`, and an earlier
    version of this test passed against exactly that mutation. What has to be
    true is that the scope's agency reaches the INSERT.
    """
    from property_watch import repository as pw_repository

    _, cur = _run(
        pw_repository, "property_watch_cursor",
        pw_repository.ensure_watch_with_baseline_scoped, ctx(A), 1, {},
        owner=A,
    )
    inserts = cur.sql_of("INSERT INTO property_watches")
    assert len(inserts) == 1, [s[0][:80] for s in cur.statements]
    statement, params = inserts[0]
    assert re.search(r"\bagency_id\b", statement), statement
    assert A in list(params), params


def test_9_both_idempotent_rereads_carry_the_agency():
    """`stima_id` and `idempotency_key` are globally unique.

    An unscoped retry lookup on either would hand back another tenant's row on
    a key collision, which is the failure a DO NOTHING makes reachable.
    """
    from property_watch import repository as pw_repository

    rereads = [
        s for s in _sql_literals(pw_repository.ensure_watch_with_baseline_scoped)
        if s.upper().startswith("SELECT")
        and ("property_watches" in s.lower() or "property_watch_observations" in s.lower())
    ]
    assert len(rereads) == 2, rereads
    for statement in rereads:
        assert re.search(r"agency_id\s*=\s*%s", statement, re.IGNORECASE), statement


def test_10_get_watch_for_stima_does_not_see_a_foreign_watch():
    from property_watch import repository as pw_repository

    result, cur = _run(
        pw_repository, "property_watch_cursor",
        pw_repository.get_watch_for_stima_scoped, ctx(A), 1, owner=B,
    )
    assert result is None, result
    assert cur.sql_of("property_watches"), "no watch query issued"
    _assert_all_sql_scoped(
        pw_repository.get_watch_for_stima_scoped, tables=("property_watches",)
    )


def test_11_the_collection_lock_is_taken_on_a_watch_this_agency_owns():
    """FOR UPDATE on an unscoped row would lock, and then act on, a foreign watch."""
    from property_watch import repository as pw_repository

    cur = ScopeCursor(owner=B)
    assert pw_repository.get_collection_context_for_update_scoped(cur, 1, A) is None
    locking = cur.sql_of("FOR UPDATE")
    assert locking, "no FOR UPDATE issued"
    assert re.search(r"agency_id\s*=\s*%s", locking[0][0], re.IGNORECASE), locking[0][0]


def test_12_the_active_watch_sweep_is_bounded_to_one_agency():
    from property_watch import repository as pw_repository

    _assert_all_sql_scoped(
        pw_repository.list_active_watch_stima_ids_for_agency, tables=("property_watches",)
    )


def test_13_observations_are_reached_only_through_a_watch_this_agency_owns():
    """Observations carry no agency of their own, so the parent supplies it."""
    from property_watch import repository as pw_repository

    statements = _sql_literals(pw_repository.list_observations_scoped)
    assert len(statements) == 1, statements
    statement = statements[0].lower()
    assert "join property_watches" in statement, statement
    assert re.search(r"w\.agency_id\s*=\s*%s", statement), statement


def test_14_the_baseline_reads_are_bounded_to_this_agency():
    from property_watch import repository as pw_repository

    _assert_all_sql_scoped(
        pw_repository.get_stima_baseline_data_scoped, tables=("stime",)
    )
    _assert_all_sql_scoped(
        pw_repository.get_stima_completed_valuation_scoped,
        tables=("stime", "seller_timeline_events"),
    )
    # The LIMIT 1 picks the latest event of one stima. Both sides of the join
    # are named so that the set it picks from cannot include another tenant's.
    statement = _sql_literals(pw_repository.get_stima_completed_valuation_scoped)[0]
    assert len(re.findall(r"agency_id\s*=\s*%s", statement, re.IGNORECASE)) == 2, statement


def test_15_only_the_agency_roster_query_is_unscoped():
    """`list_active_agency_ids` reads `agencies`, which is not tenant data.

    It is the one query in this module without a tenant predicate, and it is
    what lets every other path have one: the batch orchestrators iterate it and
    call a bounded cycle per tenant.
    """
    from property_watch import repository as pw_repository

    statements = _sql_literals(pw_repository.list_active_agency_ids)
    assert len(statements) == 1, statements
    assert re.search(r"\bFROM agencies\b", statements[0], re.IGNORECASE), statements[0]


# ---------------------------------------------------------------------------
# 16-18 - INTERNAL SIGNALS AND INTERNAL SUPPLY (quiet leak #1)
# ---------------------------------------------------------------------------

def test_16_internal_supply_counts_only_this_agencys_listings():
    """The first quiet leak: a count returns no foreign row, only a wrong number."""
    from property_watch import repository as pw_repository

    cur = ScopeCursor(owner=B)
    assert pw_repository.count_internal_supply_for_agency(cur, "Alba", "Centro", A) == 0
    statement = cur.statements[0][0]
    assert re.search(r"\bFROM properties\b", statement, re.IGNORECASE), statement
    assert re.search(r"agency_id\s*=\s*%s", statement, re.IGNORECASE), statement
    assert A in list(cur.statements[0][1]), cur.statements[0][1]


def test_17_the_supply_collector_requires_an_agency_it_cannot_default():
    """Keyword-only and with no default: forgetting it is a TypeError, not a leak."""
    from property_watch import repository as pw_repository

    signature = inspect.signature(pw_repository.collect_internal_supply_change)
    parameter = signature.parameters["agency_id"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, parameter.kind
    assert parameter.default is inspect.Parameter.empty, parameter.default


def test_18_the_scoped_supply_signal_passes_the_context_agency_down(monkeypatch):
    from property_watch import repository as pw_repository
    from property_watch import service as pw_service

    seen = {}

    def _context(cur, stima_id, agency_id):
        seen["context"] = agency_id
        return {"watch": {"id": 1}, "baseline": {"payload": {}}}

    def _count(watch_id, payload, *, cur, agency_id):
        seen["count"] = agency_id
        return {"status": "unchanged", "watch_id": watch_id, "observation": None}

    monkeypatch.setattr(
        pw_repository, "get_collection_context_for_update_scoped", _context
    )
    monkeypatch.setattr(pw_repository, "collect_internal_supply_change", _count)
    with _install(pw_repository, "property_watch_cursor", ScopeCursor(owner=A)):
        pw_service.collect_internal_supply_signal_for_stima_scoped(ctx(A), 1)
    assert seen == {"context": A, "count": A}


# ---------------------------------------------------------------------------
# 19-23 - BUYER PRESSURE (quiet leak #2)
# ---------------------------------------------------------------------------

def test_19_the_buyer_enumeration_is_bounded_to_this_agency():
    """The second quiet leak.

    Nothing foreign is returned to a caller: agency A's metrics, digest and
    stored observation are simply computed from agency B's buyers.
    """
    from property_watch import repository as pw_repository

    roots = [
        s for s in _sql_literals(pw_repository.get_buyer_pressure_inputs_for_agency)
        if re.search(r"\bFROM buy_requests\b", s, re.IGNORECASE)
    ]
    assert len(roots) == 1, roots
    assert re.search(r"b\.agency_id\s*=\s*%s", roots[0], re.IGNORECASE), roots[0]


def test_20_the_buyer_pressure_watch_lookup_is_scoped():
    from property_watch import repository as pw_repository

    watch_reads = [
        s for s in _sql_literals(pw_repository.get_buyer_pressure_inputs_for_agency)
        if re.search(r"\bFROM property_watches\b", s, re.IGNORECASE)
    ]
    assert len(watch_reads) == 1, watch_reads
    assert re.search(r"w\.agency_id\s*=\s*%s", watch_reads[0], re.IGNORECASE), watch_reads[0]


def test_21_the_buy_request_children_inherit_the_boundary():
    """They are read by `buy_request_id = ANY(...)` from an already-scoped set.

    A redundant predicate there would suggest they were a separate risk; the
    thing that must be asserted is that the id list they consume is the scoped
    one, so any child statement must be keyed on buy_request_id.
    """
    from property_watch import repository as pw_repository

    for statement in _sql_literals(pw_repository.get_buyer_pressure_inputs_for_agency):
        if not re.search(r"buy_request_(locations|typologies|features)|{table}", statement):
            continue
        assert re.search(r"buy_request_id\s*=\s*ANY", statement, re.IGNORECASE), statement


def test_22_buyer_pressure_refuses_a_watch_this_agency_does_not_own():
    from property_watch import service as pw_service

    with _install(pw_service.repository, "property_watch_cursor", ScopeCursor(owner=B)):
        with pytest.raises(pw_service.WatchNotFoundError):
            pw_service.collect_buyer_pressure_for_stima_scoped(ctx(A), 1)


def test_23_the_buyer_pressure_batch_keeps_its_per_watch_fault_boundary(monkeypatch):
    """One failing watch must not end the agency's cycle.

    The batch was rewritten for P26-6B; an inlined version would have kept the
    agency predicate and quietly dropped this boundary, which no scoping
    assertion would have caught.
    """
    from property_watch import repository as pw_repository
    from property_watch import service as pw_service

    monkeypatch.setattr(
        pw_repository, "list_active_watch_stima_ids_for_agency", lambda _a: [1, 2, 3]
    )
    monkeypatch.setattr(
        pw_repository, "get_buyer_pressure_inputs_for_agency",
        lambda stima_id, agency_id: (_ for _ in ()).throw(RuntimeError("boom"))
        if stima_id == 2
        else {"watch_id": stima_id, "baseline_observation_id": None,
              "baseline_payload": {}, "collection_time": None, "buyers": []},
    )
    result = pw_service.collect_buyer_pressure_for_active_watches_for_agency(A)
    assert result["processed"] == 3, result
    assert result["failed"] == 1, result
    assert [o["stima_id"] for o in result["outcomes"]] == [1, 2, 3]


# ---------------------------------------------------------------------------
# 24-36 - INVISIBLE SALE
# ---------------------------------------------------------------------------

def test_24_the_invisible_sale_baseline_read_is_scoped():
    from property_watch import invisible_sale_repository as is_repository

    _assert_all_sql_scoped(
        is_repository.get_watch_and_baseline_for_stima_scoped,
        tables=("property_watches",),
    )


def test_25_the_match_input_snapshot_is_bounded_to_this_agency():
    from property_watch import invisible_sale_repository as is_repository

    _assert_all_sql_scoped(
        is_repository.list_eligible_buy_snapshot_for_agency, tables=("buy_requests",)
    )


def test_26_the_active_watch_refs_sweep_is_bounded_to_this_agency():
    from property_watch import invisible_sale_repository as is_repository

    _assert_all_sql_scoped(
        is_repository.list_active_watch_refs_for_agency, tables=("property_watches",)
    )


def test_27_reading_an_invisible_sale_refuses_a_foreign_watch_before_reading_it():
    from property_watch import invisible_sale_repository as is_repository

    result, cur = _run(
        is_repository, "property_watch_cursor",
        is_repository.get_invisible_sale_for_stima_scoped, ctx(A), 1,
        owner=B,
    )
    assert isinstance(result, LookupError), result
    assert not cur.sql_of("invisible_sale_opportunities"), cur.statements


def test_28_every_review_path_locks_a_scoped_watch_first():
    """One tenant check, before any write, shared by all three review entries."""
    from property_watch import invisible_sale_repository as is_repository

    cur = ScopeCursor(owner=B)
    with pytest.raises(LookupError):
        is_repository._scoped_opportunity_for_update(cur, 1, A)
    assert len(cur.statements) == 1, cur.statements
    statement = cur.statements[0][0]
    assert "property_watches" in statement.lower(), statement
    assert re.search(r"agency_id\s*=\s*%s", statement, re.IGNORECASE), statement


def test_29_a_review_proves_the_buy_request_is_this_agencys_too():
    """A candidate row existing is not evidence that its buy request is ours.

    `invisible_sale_candidates` has two mandatory parents that can disagree,
    which is exactly the pairing 048's trigger refuses; the read side must not
    depend on the write-time trigger to be safe.
    """
    from property_watch import invisible_sale_repository as is_repository

    result, cur = _run(
        is_repository, "property_watch_cursor",
        is_repository.set_candidate_review_status_scoped, ctx(A), 1, 5, "approved",
        owner=B,
        # Watch and opportunity are handed over; the buy-request lookup that
        # follows is left to the cursor, so it refuses only if the statement
        # actually carries the predicate.
        rows=[{"id": 1}, {"id": 31, "status": "ready"}],
    )
    assert isinstance(result, LookupError), result
    buy_reads = cur.sql_of("FROM buy_requests")
    assert len(buy_reads) == 1, cur.statements
    assert re.search(r"agency_id\s*=\s*%s", buy_reads[0][0], re.IGNORECASE), buy_reads[0][0]
    assert not cur.sql_of("UPDATE invisible_sale_candidates"), cur.statements


def test_30_the_review_event_names_its_exact_opportunity_and_candidate():
    """048 is stricter than same-agency here: the candidate must belong to
    exactly the event's opportunity, or one opportunity's decision is
    attributed to another inside a single tenant."""
    from property_watch import invisible_sale_repository as is_repository

    inserts = [
        s for s in _sql_literals(is_repository.set_candidate_review_status_scoped)
        if s.upper().startswith("INSERT INTO INVISIBLE_SALE_EVENTS")
    ]
    assert len(inserts) == 1, inserts
    assert "opportunity_id" in inserts[0] and "candidate_id" in inserts[0], inserts[0]


def test_31_closing_an_invisible_sale_goes_through_a_scoped_watch():
    from property_watch import invisible_sale_repository as is_repository

    result, cur = _run(
        is_repository, "property_watch_cursor",
        is_repository.close_invisible_sale_for_stima_scoped, ctx(A), 1,
        owner=B,
    )
    assert isinstance(result, LookupError), result
    assert not cur.sql_of("UPDATE invisible_sale_opportunities"), cur.statements


# `persist_invisible_sale_refresh` is the one ctx-less repository call a scoped
# service is allowed to make, and it is exempt for a reason that is asserted
# rather than asserted-away in test_32b: every statement it issues is keyed on
# a `watch_id` or `opportunity_id` that the caller already resolved in scope,
# exactly like the buy-request child tables. An agency predicate there would
# imply it was a second, independent risk.
INHERITED_BOUNDARY_CALLS = {"persist_invisible_sale_refresh"}


def test_32_the_invisible_sale_service_calls_only_scoped_repository_seams():
    from property_watch import invisible_sale_service as is_service

    for name in (
        "get_invisible_sale_for_stima_scoped",
        "approve_invisible_sale_candidate_scoped",
        "reject_invisible_sale_candidate_scoped",
        "close_invisible_sale_scoped",
        "collect_invisible_sale_for_stima_scoped",
    ):
        source = _executable_source(getattr(is_service, name))
        for call in re.findall(r"repository\.(\w+)", source):
            if call in INHERITED_BOUNDARY_CALLS:
                continue
            assert call.endswith(("_scoped", "_for_agency")), (name, call)


def test_32b_the_one_exempt_write_is_keyed_on_an_already_scoped_identifier():
    """The exemption above has to earn itself, or it is a whitelist.

    Everything this function reads or writes is reached through the watch the
    scoped caller resolved, or through the opportunity that watch owns. If it
    ever grew a statement keyed on anything else, that statement would be a
    global one and this test fails.
    """
    from property_watch import invisible_sale_repository as is_repository

    statements = _sql_literals(is_repository.persist_invisible_sale_refresh)
    assert statements, "no statements found"
    for statement in statements:
        assert re.search(
            r"\b(watch_id|opportunity_id|candidate_id)\b\s*(=|IN)\s*%s|"
            r"\bVALUES\b|\bid\s*=\s*%s",
            statement, re.IGNORECASE,
        ), statement
        assert not re.search(
            r"\bFROM\s+(leads|contacts|stime|properties|buy_requests)\b",
            statement, re.IGNORECASE,
        ), statement


def test_33_the_invisible_sale_batch_keeps_its_per_watch_fault_boundary(monkeypatch):
    from property_watch import invisible_sale_repository as is_repository
    from property_watch import invisible_sale_service as is_service

    monkeypatch.setattr(
        is_repository, "list_active_watch_refs_for_agency",
        lambda _a: [{"watch_id": i, "stima_id": i} for i in (1, 2, 3)],
    )
    monkeypatch.setattr(
        is_service, "collect_invisible_sale_for_stima_scoped",
        lambda _scope, stima_id: (_ for _ in ()).throw(RuntimeError("boom"))
        if stima_id == 2
        else {"status": "unchanged", "watch_id": stima_id},
    )
    result = is_service.collect_invisible_sale_for_active_watches_for_agency(A)
    assert result["processed"] == 3, result
    assert [o["stima_id"] for o in result["outcomes"]] == [1, 2, 3]


def test_34_a_batch_cycle_carries_a_scope_that_is_not_a_forged_operator():
    """There is no operator behind a batch, so none is constructed.

    `_AgencyScope` exposes `require_agency` and nothing else - it cannot be
    mistaken for an authenticated principal, and it cannot answer a question
    about a user that has no answer.
    """
    from property_watch.service import _AgencyScope

    scope = _AgencyScope(A)
    assert scope.require_agency() == A
    assert not hasattr(scope, "__dict__"), "a batch scope must not be extensible"
    public = [n for n in dir(scope) if not n.startswith("_")]
    assert public == ["require_agency"], public
    assert not isinstance(scope, OperatorContext)


@pytest.mark.parametrize(
    "module_name,orchestrator,cycle,per_agency",
    [
        ("property_watch.service", "collect_internal_signals_for_all_agencies",
         "collect_internal_signals_for_active_watches_for_agency",
         {"processed": 2, "written": 1, "unchanged": 1, "unavailable": 0, "failed": 0}),
        ("property_watch.service", "collect_buyer_pressure_for_all_agencies",
         "collect_buyer_pressure_for_active_watches_for_agency",
         {"processed": 2, "written": 1, "unchanged": 1, "unavailable": 0,
          "superseded": 0, "failed": 0}),
        ("property_watch.invisible_sale_service", "collect_invisible_sale_for_all_agencies",
         "collect_invisible_sale_for_active_watches_for_agency",
         {"processed": 2, "totals": {"written": 1, "unchanged": 1,
                                     "baseline_unavailable": 0, "closed": 0, "failed": 0}}),
    ],
    ids=["internal_signals", "buyer_pressure", "invisible_sale"],
)
def test_34b_the_orchestrators_run_one_bounded_cycle_per_tenant(
    monkeypatch, module_name, orchestrator, cycle, per_agency
):
    """The loop is what supplies the predicate, so it has to be a loop.

    A single global pass that filtered afterwards would still return plausible
    totals, so this asserts which agencies were asked for - not only the sum.
    """
    import importlib

    module = importlib.import_module(module_name)
    asked = []
    monkeypatch.setattr(module.repository, "list_active_agency_ids", lambda: [A, B])
    monkeypatch.setattr(
        module, cycle, lambda agency_id: asked.append(agency_id) or dict(per_agency)
    )

    result = getattr(module, orchestrator)()

    assert asked == [A, B], asked
    assert result["agencies"] == 2, result
    assert result["processed"] == 4, result
    totals = result.get("totals", result)
    assert totals["written"] == 2, result
    assert len(result["runs"]) == 2, result


def test_35_every_invisible_sale_statement_on_a_tenant_root_is_scoped():
    from property_watch import invisible_sale_repository as is_repository

    for name in (
        "get_watch_and_baseline_for_stima_scoped",
        "list_eligible_buy_snapshot_for_agency",
        "list_active_watch_refs_for_agency",
        "get_invisible_sale_for_stima_scoped",
        "_scoped_opportunity_for_update",
        "set_candidate_review_status_scoped",
        "close_invisible_sale_for_stima_scoped",
    ):
        _assert_all_sql_scoped(
            getattr(is_repository, name), tables=("property_watches", "buy_requests")
        )


def test_36_no_scoped_function_falls_back_to_a_ctx_less_twin():
    """The failure mode this rules out is a scoped entry point that resolves an
    agency, then calls the global helper it was written to replace."""
    import importlib

    for module_name in (
        "property_watch.service",
        "property_watch.invisible_sale_service",
        "next_best_action.service",
        "database_revival.service",
    ):
        module = importlib.import_module(module_name)
        tree = ast.parse(inspect.getsource(module))
        scoped = {
            n.name for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef)
            and (n.name.endswith("_scoped") or n.name.endswith("_for_agency"))
        }
        unscoped_twins = {
            n[: -len("_scoped")] for n in scoped if n.endswith("_scoped")
        } | {n[: -len("_for_agency")] for n in scoped if n.endswith("_for_agency")}
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or node.name not in scoped:
                continue
            for call in ast.walk(node):
                if not isinstance(call, ast.Call):
                    continue
                name = (
                    call.func.attr if isinstance(call.func, ast.Attribute)
                    else call.func.id if isinstance(call.func, ast.Name)
                    else None
                )
                assert name not in unscoped_twins or name.endswith(
                    ("_scoped", "_for_agency")
                ), f"{module_name}.{node.name} calls its ctx-less twin {name}"


# ---------------------------------------------------------------------------
# 37-50 - NEXT BEST ACTION (quiet leak #3, and the highest-risk path)
# ---------------------------------------------------------------------------

def test_37_the_nba_projection_scopes_the_rows_and_the_contact_label():
    """A LEFT JOIN on `c.id = nba.contact_id` alone would enrich a row with
    another tenant's contact name - the label is the one field here that comes
    from outside this table, so it is the one that can leak."""
    from next_best_action import repository as nba_repository

    projection = " ".join(nba_repository._SELECT_WITH_CONTACT_LABEL_SCOPED.split())
    assert re.search(r"c\.agency_id\s*=\s*%s", projection, re.IGNORECASE), projection
    assert re.search(r"nba\.agency_id\s*=\s*%s", projection, re.IGNORECASE), projection


def test_38_list_current_binds_the_agency_to_both_placeholders():
    from next_best_action import repository as nba_repository

    _, cur = _run(
        nba_repository, "next_best_action_cursor",
        nba_repository.list_current_scoped, ctx(A), 10, owner=A,
        rows=[None],
    )
    assert cur.statements[0][1] == (A, A), cur.statements[0][1]


def test_39_get_current_binds_the_agency_and_the_subject():
    from next_best_action import repository as nba_repository

    _, cur = _run(
        nba_repository, "next_best_action_cursor",
        nba_repository.get_current_scoped, ctx(A), "lead", 5, owner=A, rows=[None],
    )
    assert cur.statements[0][1] == (A, A, "lead", 5), cur.statements[0][1]


def test_40_the_existing_key_read_is_scoped_or_the_prune_is_global():
    from next_best_action import repository as nba_repository

    reads = [
        s for s in _sql_literals(nba_repository.replace_current_actions_scoped)
        if s.upper().startswith("SELECT SUBJECT_TYPE")
    ]
    assert len(reads) == 1, reads
    assert re.search(r"agency_id\s*=\s*%s", reads[0], re.IGNORECASE), reads[0]


def test_41_the_nba_insert_stamps_the_agency_from_the_scope():
    from next_best_action import repository as nba_repository

    inserts = [
        s for s in _sql_literals(nba_repository.replace_current_actions_scoped)
        if s.upper().startswith("INSERT INTO NEXT_BEST_ACTIONS")
    ]
    assert len(inserts) == 1, inserts
    assert "%(agency_id)s" in inserts[0], inserts[0]


def test_42_the_upsert_refuses_to_overwrite_another_agencys_row():
    """`(subject_type, subject_id)` is a GLOBAL unique key on this table.

    Without the WHERE, a conflict with agency B's row would silently rewrite it
    into agency A's action. In practice subjects are already partitioned by
    tenant and 048's trigger would refuse the mismatch, but the upsert must not
    depend on either of those to be safe.
    """
    from next_best_action import repository as nba_repository

    statement = next(
        s for s in _sql_literals(nba_repository.replace_current_actions_scoped)
        if "ON CONFLICT" in s.upper()
    )
    assert re.search(
        r"WHERE next_best_actions\.agency_id\s*=\s*%\(agency_id\)s",
        statement, re.IGNORECASE,
    ), statement


def test_43_the_prune_deletes_inside_one_agency_only():
    """The third quiet leak, and the reason this slice led with NBA.

    The ctx-less refresh reads every existing key and deletes the ones not
    among its winners. Run by agency A, that step deletes agency B's rows.
    """
    from next_best_action import repository as nba_repository

    deletes = [
        s for s in _sql_literals(nba_repository.replace_current_actions_scoped)
        if s.upper().startswith("DELETE FROM NEXT_BEST_ACTIONS")
    ]
    assert len(deletes) == 1, deletes
    assert re.search(r"agency_id\s*=\s*%s", deletes[0], re.IGNORECASE), deletes[0]


def test_44_a_row_the_upsert_refuses_is_reported_not_counted_as_success():
    """The WHERE makes DO UPDATE a no-op, so RETURNING yields nothing.

    Folding that into `created` or `updated` would report a write that never
    happened; it is counted separately instead.
    """
    from next_best_action import repository as nba_repository

    row = {
        "subject_type": "lead", "subject_id": 5, "action_type": "call",
        "priority": "high", "reason": "r", "source_signal": "s",
        "generated_at": None,
    }
    cur = ScopeCursor(owner=A, rows=[None, None])  # no existing keys, refused upsert
    with _install(nba_repository, "next_best_action_cursor", cur):
        result = nba_repository.replace_current_actions_scoped(ctx(A), [row])
    assert result == {"created": 0, "updated": 0, "removed": 0, "skipped_foreign": 1}, result


def test_45_refresh_threads_the_context_through_every_stage():
    from next_best_action import service as nba_service

    source = _executable_source(nba_service.refresh)
    for call in (
        "safe_ensure_today_batch_scoped(ctx)",
        "collect_all_signals_scoped(ctx",
        "replace_current_actions_scoped(ctx",
    ):
        assert call in source.replace(" ", "").replace(
            "safe_ensure_today_batch_scoped(ctx)", "safe_ensure_today_batch_scoped(ctx)"
        ) or call in source, call


def test_46_the_stima_link_lookup_scopes_both_sides_of_its_join():
    """The LIMIT 1 is kept - it picks the earliest link of a single stima - but
    the set it chooses from is bounded, or it could return another tenant's lead
    for a stima this caller cannot see."""
    from next_best_action import signals as nba_signals

    statement = _sql_literals(nba_signals.resolve_stima_contact_lead_scoped)[0]
    assert re.search(r"l\.agency_id\s*=\s*%s", statement, re.IGNORECASE), statement
    assert re.search(r"s\.agency_id\s*=\s*%s", statement, re.IGNORECASE), statement


def test_47_lead_signals_read_the_seller_intent_score_in_scope():
    """The lead list was already scoped; the per-lead score was not.

    `get_seller_intent_score` reads timeline events, tasks and linked stime
    through OR branches that leave the lead behind, so another tenant's
    activity could raise this lead's intent band.
    """
    from next_best_action import signals as nba_signals

    source = _executable_source(nba_signals.collect_lead_signals_scoped)
    assert "get_seller_intent_score_scoped(ctx" in source.replace(" ", ""), source
    assert not re.search(r"\bget_seller_intent_score\(", source), source


def test_48_the_flow_backed_signals_scan_and_load_inside_one_agency():
    from next_best_action import signals as nba_signals

    for name in ("collect_next_action_signals_scoped", "collect_match_signals_scoped"):
        source = _executable_source(getattr(nba_signals, name))
        assert "scan_candidates_for_agency" in source, name
        assert "load_entity_for_agency" in source, name
        assert not re.search(r"flow_adapters\.scan_candidates\(", source), name
        assert not re.search(r"flow_adapters\.load_entity\(", source), name


def test_49_flow_refuses_an_unscoped_rule_rather_than_scanning_globally():
    """The dangerous fallback would be to fall through to the global scan for
    any rule without a scoped implementation."""
    from flow import adapters as flow_adapters

    # P26-6B scoped exactly the two rules NBA consumes and refused the rest.
    # P26-6C scoped all twelve, so FLOW-R001 and `lead` are no longer refusals -
    # they are implemented. What this test protects is unchanged: a rule or
    # entity type with no scoped implementation is REFUSED, never quietly
    # served by the global surface. Both sets are derived from the runtime, so
    # a rule added to the registry without a scan still fails here.
    assert set(flow_adapters.AGENCY_SCOPED_RULES) == set(flow_adapters._SCANS)
    assert "FLOW-R004" in flow_adapters.AGENCY_SCOPED_RULES
    assert "FLOW-R005" in flow_adapters.AGENCY_SCOPED_RULES
    with pytest.raises(flow_adapters.UnscopedRuleError):
        flow_adapters.scan_candidates_for_agency(A, "FLOW-R999", {}, 10)
    with pytest.raises(flow_adapters.UnscopedRuleError):
        flow_adapters.load_entity_for_agency(A, "invoice", 1)


def test_50_collect_all_signals_calls_six_scoped_collectors_and_nothing_else():
    from next_best_action import signals as nba_signals

    tree = ast.parse(_executable_source(nba_signals.collect_all_signals_scoped))
    called = [
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert called, ast.dump(tree)
    for name in called:
        assert name.endswith("_scoped"), name
        assert name != "collect_all_signals_scoped", "recursion, not delegation"
    # Five collectors, not six: signals #1, #2 and #3 all come out of the lead
    # collector, which reads one score per lead rather than three.
    assert len(called) == 5, called


# ---------------------------------------------------------------------------
# 51-64 - DATABASE REVIVAL
# ---------------------------------------------------------------------------

def test_51_the_daily_batch_lock_is_per_agency():
    """Correctness never depended on the lock being global; a single fixed key
    just made every tenant's batch queue behind every other's."""
    from database_revival import repository as dr_repository

    first = dr_repository.daily_batch_lock_scope(A)
    second = dr_repository.daily_batch_lock_scope(B)
    assert first != second
    assert str(A) in first and str(B) in second


def test_52_the_lock_key_is_hashed_by_postgres_not_by_python():
    """Python's `hash()` is randomised per process by PYTHONHASHSEED, so two
    workers would compute different keys and the lock would serialise nothing."""
    from database_revival import repository as dr_repository

    source = _executable_source(dr_repository.acquire_daily_batch_lock_for_agency)
    assert "hashtextextended" in source, source
    assert not re.search(r"\bhash\(", source), source
    assert not re.search(
        r"\bhash\(", _executable_source(dr_repository.daily_batch_lock_scope)
    )


def test_53_todays_batch_count_is_this_agencys():
    from database_revival import repository as dr_repository

    cur = ScopeCursor(owner=B)
    assert dr_repository.count_batch_today_for_agency(cur, A) == 0
    statement = cur.statements[0][0]
    assert "join contacts" in statement.lower(), statement
    assert re.search(r"c\.agency_id\s*=\s*%s", statement, re.IGNORECASE), statement


def test_54_the_cooldown_set_is_this_agencys():
    from database_revival import repository as dr_repository

    _assert_all_sql_scoped(
        dr_repository.get_cooldown_contact_ids_for_agency, tables=("contacts",)
    )


def test_55_a_batch_row_proves_its_contact_belongs_to_this_agency():
    """The row's tenancy comes from the contact, so that is the first check."""
    from database_revival import repository as dr_repository

    cur = ScopeCursor(owner=B)
    assert dr_repository.upsert_batch_row_for_agency(
        cur, contact_id=1, lead_id=2, agency_id=A
    ) is False
    assert len(cur.statements) == 1, cur.statements
    assert "from contacts" in cur.statements[0][0].lower(), cur.statements[0][0]
    assert not cur.sql_of("INSERT INTO seller_revival_suppressions")


def test_56_a_batch_row_proves_its_optional_lead_too():
    """048's trigger refuses a suppression whose optional lead is another
    agency's. This is the first line; the trigger is the second."""
    from database_revival import repository as dr_repository

    cur = ScopeCursor(owner=B, rows=[{"id": 1}])  # contact handed over; lead left to the cursor
    assert dr_repository.upsert_batch_row_for_agency(
        cur, contact_id=1, lead_id=2, agency_id=A
    ) is False
    assert len(cur.statements) == 2, cur.statements
    assert "from leads" in cur.statements[1][0].lower(), cur.statements[1][0]
    assert not cur.sql_of("INSERT INTO seller_revival_suppressions")


def test_57_both_membership_checks_carry_an_agency_predicate():
    from database_revival import repository as dr_repository

    for statement in _sql_literals(dr_repository.upsert_batch_row_for_agency):
        if not re.search(r"\bFROM (contacts|leads)\b", statement, re.IGNORECASE):
            continue
        assert re.search(r"agency_id\s*=\s*%s", statement, re.IGNORECASE), statement


def test_58_todays_batch_listing_is_this_agencys():
    from database_revival import repository as dr_repository

    _assert_all_sql_scoped(
        dr_repository.list_batch_today_for_agency, tables=("contacts",)
    )


def test_59_every_or_branch_of_the_eligibility_predicate_is_scoped():
    """The predicate is a disjunction over seven activity sources. One branch
    without a tenant predicate is enough for another agency's activity to keep
    this agency's lead out of - or into - the batch.
    """
    from database_revival import eligibility

    predicate = eligibility._predicate_sql_scoped()
    for table in (
        "tasks", "followup_actions", "activities", "seller_timeline_events",
        "lead_stime", "properties", "sales", "matches", "proposals",
    ):
        for fragment in re.findall(
            rf"FROM\s+{table}\s+\w+(.*?)(?=\)\s*[,)]|\Z)", predicate, re.S | re.I
        ):
            assert "agency_id = %(agency_id)s" in fragment, (table, fragment[:200])
    assert predicate.count("%(agency_id)s") >= 9, predicate.count("%(agency_id)s")


def test_60_candidate_selection_binds_the_agency():
    from database_revival import eligibility

    cur = ScopeCursor(owner=B)
    eligibility.find_eligible_candidates_for_agency(
        cur, agency_id=A, exclude_contact_ids=set(), limit=5
    )
    sql, params = cur.statements[0]
    assert params["agency_id"] == A, params
    assert "%(agency_id)s" in sql, sql[:200]


def test_61_live_revalidation_binds_the_agency():
    from database_revival import eligibility

    cur = ScopeCursor(owner=B, rows=[{"eligible": False}])
    assert eligibility.is_still_eligible_for_agency(
        cur, agency_id=A, contact_id=1, lead_id=2
    ) is False
    assert cur.statements[0][1]["agency_id"] == A, cur.statements[0][1]


def test_62_the_daily_cap_is_spent_per_agency():
    """A global cap would let the busiest tenant consume every other's slots."""
    from database_revival import service as dr_service

    source = _executable_source(dr_service.ensure_today_batch_for_agency)
    assert "DAILY_CAP - batch_count_today" in source, source
    assert "count_batch_today_for_agency(cur, agency_id)" in source, source


def test_63_the_orchestrator_loops_over_tenants_rather_than_filtering_after():
    """So that no statement in the module ever runs without a tenant predicate."""
    from database_revival import service as dr_service

    source = _executable_source(dr_service.ensure_today_batch_for_all_agencies)
    assert "list_active_agency_ids" in source, source
    assert "ensure_today_batch_for_agency(agency_id" in source.replace("\n", " "), source


def test_64_todays_signals_are_revalidated_inside_the_same_agency():
    from database_revival import service as dr_service

    source = _executable_source(dr_service.collect_today_signals_for_agency)
    assert "list_batch_today_for_agency(cur, agency_id)" in source, source
    assert "is_still_eligible_for_agency" in source, source
    assert "agency_id=agency_id" in source.replace(" ", ""), source


# ---------------------------------------------------------------------------
# 65-86 - migrations
# ---------------------------------------------------------------------------

def _strip_comments(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


def _strip_strings(sql: str) -> str:
    return re.sub(r"'(?:[^']|'')*'", " ", sql)


def _sql(version: str) -> str:
    return _strip_comments((MIGRATIONS / f"{version}.sql").read_text(encoding="utf-8"))


def _down(version: str) -> str:
    return _strip_comments((MIGRATIONS / f"{version}_down.sql").read_text(encoding="utf-8"))


def _flat(version: str) -> str:
    return " ".join(_strip_strings(_sql(version)).split()).lower()


@pytest.mark.parametrize("version", (COLUMNS, BACKFILL, ENFORCE))
def test_65_shared_migration_rules(version):
    from test_p26_1_migration_rules import assert_p26_migration_rules

    # 047 writes values it cannot identify afterwards, so its rollback refuses.
    assert_p26_migration_rules(version, expect_reversible=version != BACKFILL)


@pytest.mark.parametrize("version", (COLUMNS, BACKFILL, ENFORCE))
def test_65_transaction_ownership(version):
    up = (MIGRATIONS / f"{version}.sql").read_text(encoding="utf-8")
    down = (MIGRATIONS / f"{version}_down.sql").read_text(encoding="utf-8")
    assert not re.search(r"^\s*BEGIN\s*;", up, re.IGNORECASE | re.MULTILINE)
    assert not re.search(r"^\s*COMMIT\s*;", up, re.IGNORECASE | re.MULTILINE)
    assert re.search(r"^\s*BEGIN\s*;", down, re.IGNORECASE | re.MULTILINE)
    assert re.search(r"^\s*COMMIT\s*;", down, re.IGNORECASE | re.MULTILINE)


@pytest.mark.parametrize("table", OWNED_TABLES)
def test_65_046_adds_the_approved_columns(table):
    up = " ".join(_strip_strings(_sql(COLUMNS)).split())
    assert re.search(
        rf"ALTER TABLE {table}\s+ADD COLUMN IF NOT EXISTS agency_id\s+BIGINT",
        up, re.IGNORECASE,
    ), up
    assert re.search(rf"{table}_agency_id_fk", up, re.IGNORECASE), up
    assert "ON DELETE RESTRICT" in up, up


@pytest.mark.parametrize("table", DERIVED_TABLES)
def test_65_046_adds_no_column_to_a_derived_table(table):
    """Each of these has a mandatory parent. A physical column would be a
    second copy of a fact already recorded once, and two copies disagree."""
    up = " ".join(_strip_strings(_sql(COLUMNS)).split())
    assert not re.search(rf"ALTER TABLE\s+{table}\s+ADD COLUMN", up, re.IGNORECASE), up


def test_66_the_new_columns_are_nullable_with_no_default():
    flat = _flat(COLUMNS)
    assert "set not null" not in flat, flat
    assert "set default" not in flat, flat
    for declaration in re.findall(r"add column if not exists agency_id bigint([^;]*)", flat):
        assert "not null" not in declaration, declaration
        assert "default" not in declaration, declaration


def test_66_046_writes_no_data():
    up = _sql(COLUMNS)
    for forbidden in (r"^\s*UPDATE\s", r"^\s*INSERT\s+INTO", r"^\s*DELETE\s+FROM"):
        assert not re.search(forbidden, up, re.IGNORECASE | re.MULTILINE), forbidden


def test_67_the_watch_backfill_derives_from_the_stima_only():
    """A watch's provenance is its stima. There is no second source, so there
    is nothing to disambiguate - and nothing to fall back to."""
    flat = _flat(BACKFILL)
    assert "property_watches" in flat
    assert "stime" in flat
    assert re.search(r"update property_watches[^;]*agency_id is null", flat), flat


def test_68_a_watch_with_no_derivable_agency_is_a_hard_failure():
    """`stima_id` is ON DELETE SET NULL, so a watch can genuinely have no
    provenance left. That is refused, not invented."""
    up = _sql(BACKFILL)
    assert "property_watches" in up
    assert up.count("RAISE EXCEPTION") >= 4, up.count("RAISE EXCEPTION")


@pytest.mark.parametrize("subject", ("lead", "buy_request", "stima", "match"))
def test_69_the_nba_backfill_resolves_every_subject_type(subject):
    assert subject in _flat(BACKFILL), f"047 never resolves subject_type {subject!r}"


def test_69_the_nba_backfill_also_uses_the_direct_references():
    flat = _flat(BACKFILL)
    for table in ("contacts", "leads", "stime", "buy_requests", "matches"):
        assert table in flat, f"047 ignores {table} as a candidate source"


@pytest.mark.parametrize("case", ("no_source", "ambiguous"))
def test_70_71_undecidable_nba_rows_are_refused(case):
    up = _sql(BACKFILL)
    assert re.search(r"count\s*\(\s*distinct", up, re.IGNORECASE), up
    assert "exception when" not in _flat(BACKFILL), "047 swallows a failure"


def test_72_73_the_backfill_invents_nothing():
    flat = _flat(BACKFILL)
    for forbidden in ("limit 1", "min(", "max(", "coalesce(", "stima360", "slug"):
        assert forbidden not in flat, f"047 contains {forbidden!r}"
    assert not re.search(r"agency_id\s*=\s*\d", _strip_strings(_sql(BACKFILL)))
    assert not re.search(r"\bfrom\s+agencies\b", flat), flat


def test_67_69_the_backfill_never_overwrites_an_owned_row():
    for statement in re.findall(r"update\s+\w+[^;]*", _flat(BACKFILL)):
        assert "agency_id is null" in statement, statement


@pytest.mark.parametrize("table", OWNED_TABLES)
def test_74_048_sets_not_null(table):
    up = " ".join(_strip_strings(_sql(ENFORCE)).split())
    assert re.search(
        rf"ALTER TABLE {table}\s+ALTER COLUMN agency_id\s+SET NOT NULL",
        up, re.IGNORECASE,
    ), up


def test_74_048_prechecks_before_it_constrains():
    flat = _flat(ENFORCE)
    assert flat.index("raise exception") < flat.index("set not null"), (
        "048 constrains before it checks"
    )
    assert "exception when" not in flat


MIGRATION_FUNCTIONS = (
    "property_watch_agency_integrity",
    "next_best_action_agency_integrity",
    "invisible_sale_candidate_agency_integrity",
    "invisible_sale_event_parent_integrity",
)

TRIGGERED_TABLES = (
    "property_watches",
    "next_best_actions",
    "invisible_sale_candidates",
    "invisible_sale_events",
)


@pytest.mark.parametrize("function", MIGRATION_FUNCTIONS)
def test_75_82_048_creates_one_function_per_table(function):
    """Separate functions, not one shared across differing rowtypes.

    Migration 040 shipped a single function whose condition mixed the table
    test with a field only one table had; PL/pgSQL resolves a record field when
    it prepares the expression, so every write on the other tables failed. Four
    tables with four different shapes make one function per table the only form
    that cannot repeat it.
    """
    assert f"create or replace function {function}" in _flat(ENFORCE), _flat(ENFORCE)


@pytest.mark.parametrize("table", TRIGGERED_TABLES)
def test_75_82_each_trigger_fires_on_insert_and_update(table):
    flat = _flat(ENFORCE)
    assert f"on {table}" in flat, f"{table} has no trigger"
    position = flat.index(f"on {table}")
    block = flat[max(0, position - 400): position + 60]
    assert "before insert or update of" in block, (table, block)


def test_79_80_the_candidate_guard_compares_both_parents():
    flat = _flat(ENFORCE)
    assert "opportunity_id" in flat and "buy_request_id" in flat
    assert "property_watches" in flat, (
        "the candidate guard never reaches the watch its opportunity hangs off"
    )


def test_81_82_the_event_guard_requires_the_exact_parent():
    """Same-agency is not enough here.

    An event citing a sibling opportunity's candidate would attribute one
    opportunity's decision to another - inside a single tenant, where no agency
    comparison would notice.
    """
    up = _sql(ENFORCE)
    block = up[up.index("invisible_sale_event_parent_integrity"):]
    block = block[: block.index("$fn$ LANGUAGE plpgsql")]
    assert "NEW.candidate_id IS NOT NULL" in block, block
    # The candidate's own opportunity is read and compared to the event's.
    assert re.search(
        r"SELECT opportunity_id INTO (\w+)\s+FROM invisible_sale_candidates", block
    ), block
    assert re.search(r"\w+ <> NEW\.opportunity_id", block), block
    # And it must be a refusal, not a correction.
    assert block.count("RAISE EXCEPTION") >= 2, block


def test_83_the_suppression_lead_is_checked_against_its_contact():
    flat = _flat(ENFORCE)
    assert "seller_revival_suppressions" in flat, (
        "048 never validates the suppression's optional lead against its contact"
    )


def test_84_no_table_unsafe_generic_trigger_pattern():
    body = _sql(ENFORCE)
    offenders = []
    for match in re.finditer(r"\b(?:ELSIF|IF)\b(.*?)\bTHEN\b", body, re.DOTALL | re.IGNORECASE):
        condition = " ".join(match.group(1).split())
        if "TG_TABLE_NAME" in condition and re.search(r"NEW\.\w+", condition):
            offenders.append(condition)
    assert not offenders, offenders


def test_85_the_down_files_remove_only_p26_6b_objects():
    enforce_down = " ".join(_strip_strings(_down(ENFORCE)).split()).lower()
    for function in MIGRATION_FUNCTIONS:
        assert f"drop function if exists {function}" in enforce_down, function
    for table in OWNED_TABLES:
        assert f"alter table {table} alter column agency_id drop not null" in enforce_down, table
    for forbidden in ("insert into", "delete from", "truncate", "cascade", "drop column"):
        assert forbidden not in enforce_down, forbidden

    columns_down = " ".join(_strip_strings(_down(COLUMNS)).split()).lower()
    for table in OWNED_TABLES:
        assert f"alter table {table} drop column if exists agency_id" in columns_down, table
    assert "delete from" not in columns_down


def test_85_the_backfill_rollback_refuses():
    down = _down(BACKFILL).lower()
    assert "raise exception" in down, down
    assert "irreversible" in down, down
    assert not re.search(r"set\s+agency_id\s*=\s*null", down), down


def test_86_the_runner_discovers_026_to_048_and_validates_all():
    from test_p26_1_migration_rules import RUNNER, _load

    runner = _load(RUNNER, "p26_migrate_048")
    present = sorted(m.number for m in runner.discover_migrations(MIGRATIONS))
    assert present[:23] == list(range(26, 49)), present
    for migration in runner.discover_migrations(MIGRATIONS):
        assert runner.validate_migration(migration) == [], migration.version


def test_86_migrations_026_to_045_are_untouched():
    """All are applied on TEST; their checksums must stay immutable."""
    forty = (MIGRATIONS / "040_p26_match_agency_enforce.sql").read_text(encoding="utf-8")
    assert re.search(r"IF\s+TG_TABLE_NAME\s*=\s*'matches'\s+AND\s+NEW\.latest_run_id", forty)
    forty_five = (MIGRATIONS / "045_p26_seller_engine_agency_enforce.sql").read_text(encoding="utf-8")
    assert "seller_timeline_event_agency_integrity" in forty_five


def test_86_the_historical_034_comment_is_left_alone():
    """034 calls property_watches CHILD-DERIVED. That classification is now
    known to be wrong - stima_id is ON DELETE SET NULL - but 034 is applied and
    its checksum is immutable. The correction lives in 046, not in a rewrite of
    history."""
    thirty_four = (MIGRATIONS / "034_p26_property_agency_columns.sql").read_text(encoding="utf-8")
    assert "property_watches" in thirty_four
    assert "CHILD-DERIVED" in thirty_four
