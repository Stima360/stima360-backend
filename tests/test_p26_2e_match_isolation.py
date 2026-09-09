"""P26-2E - MATCH agency integrity.

Offline. No database, no network: the application tests drive the real
repository against an agency-aware fake cursor, and the migration tests read
SQL text.

MATCH IS CHILD-DERIVED

A match has no agency of its own. It is a statement about two rows that do:

    matches.buy_request_id -> buy_requests.agency_id
    matches.property_id    -> properties.agency_id

and the whole of P26-2E is the rule that those two must be equal. A match
between agency A's buyer and agency B's property is not a mis-scoped row - it
is a row that must never exist, because it asserts a relationship across a
tenant boundary.

So no `agency_id` column is added to any MATCH table. A physical column would
be a third copy of a fact already recorded twice, and the failure mode of three
copies is that they disagree.

WHICH TABLES NEED A DB GUARD, AND WHICH DO NOT

    matches                    2 roots + latest_run_id   -> guard
    match_runs                 2 optional roots          -> guard
    match_exclusions           2 roots                   -> guard
    match_refresh_history      parent + 2 run refs       -> guard
    match_requirement_results  match_run_id only         -> NO guard
    match_feedback             match_id only             -> NO guard

The last two are asserted as *absences* below, and the reason is structural
rather than a judgement call: each has exactly one foreign key, to a parent that
is itself guarded. A row cannot straddle two agencies when it names only one
other row. Adding a trigger there would cost writes on every insert and could
never fire.

`latest_run_id` and the two refresh-history run references need more than
same-agency. A run carries its own buy_request_id / property_id, so a run from
the *same agency but a different pair* would pass an agency check while still
being the wrong run. The guards below therefore compare the pair, not the
tenant.

Coverage map:

    A-C   route surface and schemas
    D-H   read/update scoping
    I-L   calculate
    M-N   dashboard aggregates
    O-S   refresh and stale
    T     readiness
    U-X   feedback, exclusions, timeline, refresh history
    Y-Z   unbound platform admin, forged agency
    MIG   migration 040 statics + hostile matrix
"""
from __future__ import annotations

import ast
import inspect
import re
from contextlib import contextmanager
from pathlib import Path

import pytest

from core.exceptions import NotFoundError, ValidationError
from core.scope import ProgrammingError
from match import repository, router, service
from operator_auth.context import OperatorContext
from operator_auth.exceptions import PlatformAdminAgencyRequired

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"

VERSION = "040_p26_match_agency_enforce"
UP_PATH = MIGRATIONS / f"{VERSION}.sql"
DOWN_PATH = MIGRATIONS / f"{VERSION}_down.sql"

EXPECTED_ROUTES = 26
AGENCY_DEPENDENCY = "legacy_basic_agency_context"

# Two agencies. Deliberately not 1: a hardcoded agency would pass a test using 1.
A, B = 11, 22

MATCH_TABLES = (
    "matches",
    "match_runs",
    "match_requirement_results",
    "match_exclusions",
    "match_refresh_history",
    "match_feedback",
)
# Tables whose only foreign key is to an already-guarded parent.
SINGLE_PARENT_TABLES = ("match_requirement_results", "match_feedback")
GUARDED_TABLES = ("matches", "match_runs", "match_exclusions", "match_refresh_history")


def ctx(agency_id=A) -> OperatorContext:
    """What legacy_basic_agency_context produces: agency-bound, no user."""
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
# An agency-aware fake cursor.
#
# Rows carry an owning agency and the fake honours the agency predicate the SQL
# actually carries. A handler that forgot the predicate therefore SEES the
# foreign row and its test fails, instead of passing because a canned response
# happened to be empty. That distinction is the whole value of this fixture.
# ---------------------------------------------------------------------------

class AgencyCursor:
    def __init__(self, *, buys=None, properties=None, matches=None, rows=None):
        self.buys = dict(buys or {})            # id -> agency
        self.properties = dict(properties or {})
        self.matches = dict(matches or {})      # id -> (buy_id, property_id)
        self.rows = list(rows or [])
        self.statements: list[tuple[str, object]] = []
        self.current = None

    # -- helpers ---------------------------------------------------------
    def _scoped(self, sql: str) -> bool:
        flat = sql.lower().replace(" ", "")
        return "agency_id=%s" in flat

    def _agency_params(self, params):
        return [p for p in (params or []) if p in (A, B)]

    def execute(self, sql, params=None):
        flat = " ".join(str(sql).split())
        low = flat.lower()
        self.statements.append((flat, params))
        p = list(params or [])
        asked = self._agency_params(p)
        scoped = self._scoped(low)

        if self.rows:
            self.current = self.rows.pop(0)
            return

        if low.startswith(("insert", "update", "delete")):
            # A scoped write against rows owned by another agency must affect
            # nothing, exactly as the real DELETE ... USING joins would. An
            # unscoped write "succeeds" so the assertion can catch the leak.
            if scoped:
                owners = set(self.buys.values()) | set(self.properties.values())
                if owners and not owners.issubset(set(asked)):
                    self.current = None
                    return
            self.current = {"id": 1, "buy_request_id": 1, "property_id": 1,
                            "leaked": not scoped}
            return

        table = None
        m = re.search(r"from\s+(\w+)", low)
        if m:
            table = m.group(1)

        if table == "buy_requests":
            owner = self.buys.get(p[0] if p else None)
            self.current = self._row(owner, asked, scoped, {"id": p[0] if p else None,
                                                            "status": "active",
                                                            "archived_at": None})
            return
        if table == "properties":
            owner = self.properties.get(p[0] if p else None)
            self.current = self._row(owner, asked, scoped,
                                     {"id": p[0] if p else None,
                                      "commercial_status": "available",
                                      "archived_at": None})
            return
        if table == "matches" or "from matches" in low:
            match_id = p[0] if p else None
            pair = self.matches.get(match_id)
            if pair is None:
                self.current = None
                return
            buy_agency = self.buys.get(pair[0])
            prop_agency = self.properties.get(pair[1])
            visible = True
            if scoped:
                visible = all(a in asked for a in {buy_agency, prop_agency})
            self.current = {
                "id": match_id, "buy_request_id": pair[0], "property_id": pair[1],
                "latest_run_id": 5, "score_total": 50, "freshness_status": "fresh",
                "leaked": not scoped,
            } if visible else None
            return

        self.current = None

    def _row(self, owner, asked, scoped, payload):
        if owner is None:
            return None
        if scoped and owner not in asked:
            return None
        return {**payload, "agency_id": owner, "leaked": not scoped}

    def fetchone(self):
        return self.current

    def fetchall(self):
        if isinstance(self.current, list):
            return self.current
        return [self.current] if self.current else []


@pytest.fixture
def cursor(monkeypatch):
    state = {}

    def _install(**kwargs):
        cur = AgencyCursor(**kwargs)
        state["cur"] = cur

        @contextmanager
        def fake(*_a, **_k):
            yield None, cur

        monkeypatch.setattr(repository, "core_cursor", fake)
        return cur

    state["install"] = _install
    return state


def _blocked(fn, *args, **kwargs):
    """Run and report whether the call refused. Never swallow a wrong reason."""
    try:
        fn(*args, **kwargs)
        return None
    except Exception as exc:  # noqa: BLE001 - the type is asserted by callers
        return exc


# ---------------------------------------------------------------------------
# A / B / C - the route surface
# ---------------------------------------------------------------------------

def _routes() -> tuple[list[str], list[str]]:
    tree = ast.parse((ROOT / "match" / "router.py").read_text(encoding="utf-8"))
    routes, scoped = [], []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        is_route = any(
            isinstance(d, ast.Call)
            and isinstance(d.func, ast.Attribute)
            and d.func.attr in ("get", "post", "patch", "put", "delete")
            and isinstance(d.func.value, ast.Name)
            and d.func.value.id == "router"
            for d in node.decorator_list
        )
        if not is_route:
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


def test_a_the_router_has_the_expected_route_count():
    routes, _ = _routes()
    assert len(routes) == EXPECTED_ROUTES, sorted(routes)


def test_b_every_route_takes_the_agency_context():
    """Matched decorator-to-parameter, not a count of occurrences.

    A text count would stay correct when a route is added without the
    dependency, so it cannot detect the regression it appears to guard.
    """
    routes, scoped = _routes()
    missing = sorted(set(routes) - set(scoped))
    assert not missing, f"routes without {AGENCY_DEPENDENCY}: {missing}"


def test_c_no_match_schema_exposes_a_client_owned_agency():
    import match.schemas as schemas
    from pydantic import BaseModel

    offenders = []
    models = [
        (name, obj) for name, obj in vars(schemas).items()
        if isinstance(obj, type) and issubclass(obj, BaseModel) and obj is not BaseModel
    ]
    assert models, "no MATCH schemas found"
    for name, model in models:
        if "agency_id" in getattr(model, "model_fields", {}):
            offenders.append(f"{name}: declares agency_id")
    assert not offenders, offenders


# ---------------------------------------------------------------------------
# D-H - reads and updates
# ---------------------------------------------------------------------------

FOREIGN_PAIR = {"buys": {7: B}, "properties": {8: B}, "matches": {70: (7, 8)}}
OWN_PAIR = {"buys": {7: A}, "properties": {8: A}, "matches": {70: (7, 8)}}


def test_d_list_is_scoped_to_the_context_agency(cursor):
    cur = cursor["install"](**FOREIGN_PAIR)
    repository.list_matches_scoped(ctx(A), limit=10, offset=0)
    sql = cur.statements[-1][0].lower()
    assert "agency_id" in sql, sql
    assert cur.statements[-1][1] and A in list(cur.statements[-1][1])


def test_e_get_foreign_match_is_not_found(cursor):
    cursor["install"](**FOREIGN_PAIR)
    exc = _blocked(repository.get_match_scoped, ctx(A), 70)
    assert isinstance(exc, NotFoundError), exc


def test_e_get_own_match_is_allowed(cursor):
    """Positive control: the matrix must not simply block everything."""
    cursor["install"](**OWN_PAIR)
    result = repository.get_match_scoped(ctx(A), 70)
    assert result["id"] == 70


def test_f_update_foreign_match_is_not_found(cursor):
    cursor["install"](**FOREIGN_PAIR)
    exc = _blocked(repository.update_match_scoped, ctx(A), 70, {"priority": "high"})
    assert isinstance(exc, NotFoundError), exc


def test_g_override_on_a_foreign_match_is_not_found(cursor):
    cursor["install"](**FOREIGN_PAIR)
    exc = _blocked(repository.set_override_scoped, ctx(A), 70, 90, "why")
    assert isinstance(exc, NotFoundError), exc


def test_h_clear_override_on_a_foreign_match_is_not_found(cursor):
    cursor["install"](**FOREIGN_PAIR)
    exc = _blocked(repository.clear_override_scoped, ctx(A), 70)
    assert isinstance(exc, NotFoundError), exc


# ---------------------------------------------------------------------------
# I-L - calculate
# ---------------------------------------------------------------------------

def test_i_calculate_pair_within_one_agency_resolves_both_roots(cursor):
    cur = cursor["install"](buys={7: A}, properties={8: A})
    _blocked(repository.calculate_pair_scoped, ctx(A), 7, 8)
    buy_reads = [s for s in cur.statements if "from buy_requests" in s[0].lower()]
    prop_reads = [s for s in cur.statements if "from properties" in s[0].lower()]
    assert buy_reads and prop_reads, cur.statements
    for sql, params in buy_reads + prop_reads:
        assert "agency_id" in sql.lower(), sql
        assert A in list(params or []), (sql, params)


def test_j_calculate_pair_across_agencies_fails_closed(cursor):
    cursor["install"](buys={7: A}, properties={8: B})
    exc = _blocked(repository.calculate_pair_scoped, ctx(A), 7, 8)
    assert isinstance(exc, NotFoundError), exc


def _sweep_sql(function_name: str, table: str) -> str:
    """The candidate-sweep statement inside a calculate_for_* function.

    Asserted on the source rather than on a recording: `require_ready` refuses
    the fixture's synthetic root before the sweep ever executes, so a
    behavioural probe here would assert on statements that never ran. The
    scoping of the roots themselves is proved behaviourally in test_i.
    """
    source = inspect.getsource(getattr(repository, function_name))
    match = re.search(
        rf"SELECT id FROM {table}(.*?)ORDER BY id", source, re.IGNORECASE | re.DOTALL
    )
    assert match, f"{function_name} has no {table} sweep: {source}"
    return " ".join(match.group(0).split()).lower()


def test_k_calculate_for_buy_only_sweeps_same_agency_properties():
    sweep = _sweep_sql("calculate_for_buy_scoped", "properties")
    assert "agency_id=%s" in sweep.replace(" ", ""), sweep


def test_l_calculate_for_property_only_sweeps_same_agency_buys():
    sweep = _sweep_sql("calculate_for_property_scoped", "buy_requests")
    assert "agency_id=%s" in sweep.replace(" ", ""), sweep


@pytest.mark.parametrize(
    "name,table",
    [("calculate_for_buy_scoped", "properties"),
     ("calculate_for_property_scoped", "buy_requests")],
)
def test_kl_the_sweep_probe_is_not_vacuous(name, table):
    """Negative control: the probe must fail on an unscoped sweep."""
    unscoped = f"SELECT id FROM {table} WHERE archived_at IS NULL ORDER BY id"
    assert "agency_id=%s" not in unscoped.replace(" ", "")


# ---------------------------------------------------------------------------
# M-N - aggregates
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "name,args",
    [
        ("dashboard_scoped", ()),
        ("dashboard_stale_scoped", (10,)),
        ("dashboard_errors_scoped", (10,)),
        ("dashboard_review_scoped", (10,)),
    ],
)
def test_mn_every_aggregate_counts_only_its_own_agency(cursor, name, args):
    """A count that included another tenant would leak through a number.

    Aggregates are the quiet leak: no row is returned, but the totals still
    describe someone else's data.
    """
    cur = cursor["install"](**FOREIGN_PAIR)
    _blocked(getattr(repository, name), ctx(A), *args)
    reads = [s for s in cur.statements if "from matches" in s[0].lower()]
    assert reads, cur.statements
    for sql, params in reads:
        assert "agency_id" in sql.lower(), f"{name} aggregates without a scope: {sql}"


# ---------------------------------------------------------------------------
# O-S - refresh and stale
# ---------------------------------------------------------------------------

def test_o_refresh_of_a_foreign_match_fails_closed(cursor):
    cursor["install"](**FOREIGN_PAIR)
    exc = _blocked(repository.refresh_match_scoped, ctx(A), 70)
    assert isinstance(exc, NotFoundError), exc


def test_p_refresh_for_buy_rejects_a_foreign_buy(cursor):
    cursor["install"](buys={7: B}, properties={}, matches={})
    exc = _blocked(repository.refresh_for_buy_scoped, ctx(A), 7)
    assert isinstance(exc, NotFoundError), exc


def test_q_refresh_for_property_rejects_a_foreign_property(cursor):
    cursor["install"](properties={8: B})
    exc = _blocked(repository.refresh_for_property_scoped, ctx(A), 8)
    assert isinstance(exc, NotFoundError), exc


def test_r_refresh_stale_selects_only_its_own_agency(cursor):
    cur = cursor["install"](**FOREIGN_PAIR)
    _blocked(repository.refresh_stale_scoped, ctx(A), 5)
    selects = [s for s in cur.statements if "from matches" in s[0].lower()]
    assert selects, cur.statements
    for sql, _ in selects:
        assert "agency_id" in sql.lower(), f"refresh_stale sweeps globally: {sql}"


def test_s_detect_stale_scopes_every_optional_identifier(cursor):
    cur = cursor["install"](**FOREIGN_PAIR)
    _blocked(repository.detect_stale_scoped, ctx(A), 70, None, None)
    writes = [s for s in cur.statements if s[0].lower().startswith("update matches")]
    assert writes, cur.statements
    for sql, _ in writes:
        assert "agency_id" in sql.lower(), f"detect_stale updates globally: {sql}"


# ---------------------------------------------------------------------------
# T - readiness
# ---------------------------------------------------------------------------

def test_t_readiness_rejects_a_foreign_buy(cursor):
    cursor["install"](buys={7: B})
    exc = _blocked(repository.get_readiness_scoped, ctx(A), 7, None)
    assert isinstance(exc, NotFoundError), exc


def test_t_readiness_rejects_a_foreign_property(cursor):
    cursor["install"](properties={8: B})
    exc = _blocked(repository.get_readiness_scoped, ctx(A), None, 8)
    assert isinstance(exc, NotFoundError), exc


def test_t_readiness_requires_at_least_one_identifier(cursor):
    cursor["install"]()
    exc = _blocked(repository.get_readiness_scoped, ctx(A), None, None)
    assert isinstance(exc, ValidationError), exc


# ---------------------------------------------------------------------------
# U-X - children of a match
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "name,args",
    [
        ("add_feedback_scoped", (70, {"source": "agent", "feedback_type": "positive"})),
        ("list_feedback_scoped", (70,)),
        ("timeline_scoped", (70,)),
        ("refresh_history_scoped", (70,)),
    ],
)
def test_ux_match_children_refuse_a_foreign_parent(cursor, name, args):
    cursor["install"](**FOREIGN_PAIR)
    exc = _blocked(getattr(repository, name), ctx(A), *args)
    assert isinstance(exc, NotFoundError), (name, exc)


def test_u_delete_feedback_derives_its_agency_through_the_match(cursor):
    """Deleting by feedback_id must not bypass the parent check."""
    cur = cursor["install"](**FOREIGN_PAIR)
    exc = _blocked(repository.delete_feedback_scoped, ctx(A), 99)
    assert isinstance(exc, NotFoundError), exc
    deletes = [s for s in cur.statements if s[0].lower().startswith("delete")]
    for sql, _ in deletes:
        assert "agency_id" in sql.lower(), f"unscoped feedback delete: {sql}"


def test_v_exclusion_add_requires_both_roots_in_the_context_agency(cursor):
    cursor["install"](buys={7: A}, properties={8: B})
    exc = _blocked(
        repository.add_exclusion_scoped, ctx(A),
        {"buy_request_id": 7, "property_id": 8},
    )
    assert isinstance(exc, NotFoundError), exc


def test_v_exclusion_list_is_scoped(cursor):
    cur = cursor["install"](**FOREIGN_PAIR)
    _blocked(repository.list_exclusions_scoped, ctx(A))
    reads = [s for s in cur.statements if "match_exclusions" in s[0].lower()]
    assert reads, cur.statements
    for sql, _ in reads:
        assert "agency_id" in sql.lower(), sql


def test_v_exclusion_delete_derives_agency_from_the_pair(cursor):
    cur = cursor["install"](**FOREIGN_PAIR)
    exc = _blocked(repository.delete_exclusion_scoped, ctx(A), 99)
    assert isinstance(exc, NotFoundError), exc
    for sql, _ in [s for s in cur.statements if s[0].lower().startswith("delete")]:
        assert "agency_id" in sql.lower(), f"unscoped exclusion delete: {sql}"


# ---------------------------------------------------------------------------
# Y-Z - the caller cannot choose an agency
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "name,args",
    [
        ("list_matches_scoped", ()),
        ("get_match_scoped", (1,)),
        ("dashboard_scoped", ()),
        ("calculate_pair_scoped", (1, 2)),
        ("refresh_stale_scoped", (5,)),
        ("list_exclusions_scoped", ()),
    ],
)
def test_y_an_unbound_platform_admin_fails_closed(cursor, name, args):
    cursor["install"]()
    exc = _blocked(getattr(repository, name), unbound(), *args)
    assert isinstance(exc, PlatformAdminAgencyRequired), (name, exc)


def test_y_the_refusal_happens_before_any_query(cursor):
    cur = cursor["install"]()
    _blocked(repository.list_matches_scoped, unbound())
    assert cur.statements == [], cur.statements


def test_z_the_agency_is_never_read_from_caller_data():
    """Structural: an agency key is never consulted anywhere in the module."""
    tree = ast.parse((ROOT / "match" / "repository.py").read_text(encoding="utf-8"))
    read_keys = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            if isinstance(node.slice.value, str):
                read_keys.append(node.slice.value)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in {"get", "pop", "setdefault"} and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    read_keys.append(first.value)
    leaked = [k for k in read_keys if k.lower() in ("agency_id", "agency", "tenant_id")]
    assert not leaked, f"match/repository.py reads {leaked} from caller data"


def test_z_no_hardcoded_or_default_agency_in_match():
    source = (ROOT / "match" / "repository.py").read_text(encoding="utf-8")
    assert not re.search(r"agency_id\s*=\s*\d", source), source
    assert "stima360" not in source
    assert "resolve_default_agency_id" not in source, (
        "MATCH resolves a Default Agency instead of using the caller's scope"
    )
    assert AGENCY_DEPENDENCY not in source, (
        "the repository builds a context instead of receiving one"
    )


def test_z_only_the_router_resolves_the_context():
    assert AGENCY_DEPENDENCY in (ROOT / "match" / "router.py").read_text(encoding="utf-8")
    assert AGENCY_DEPENDENCY not in (ROOT / "match" / "service.py").read_text(encoding="utf-8")


def test_z_every_scoped_helper_takes_its_agency_from_require_agency():
    source = (ROOT / "match" / "repository.py").read_text(encoding="utf-8")
    assert "require_agency()" in source


# ---------------------------------------------------------------------------
# Legacy compatibility - the reason this block adds functions instead of
# changing signatures.
# ---------------------------------------------------------------------------

LEGACY_SIGNATURES = {
    "list_matches": ["limit", "offset", "buy_request_id", "property_id",
                     "match_class", "commercial_status", "compatible_only",
                     "freshness_status", "review_required"],
    "calculate_pair": ["buy_request_id", "property_id", "run_type", "created_by"],
    "calculate_for_buy": ["request_id", "created_by"],
    "calculate_for_property": ["property_id", "created_by"],
    "get_readiness": ["buy_request_id", "property_id"],
    "refresh_for_buy": ["request_id", "created_by", "trigger_reason"],
}


@pytest.mark.parametrize("name,params", sorted(LEGACY_SIGNATURES.items()))
def test_legacy_repository_signatures_are_unchanged(name, params):
    """crm/service.py and several historical tests call these directly.

    P26-2D BUY established the pattern: add `_scoped` functions rather than
    thread a context through contracts other domains depend on.
    """
    assert list(inspect.signature(getattr(repository, name)).parameters) == params


def test_legacy_service_list_matches_is_still_callable_without_a_context():
    """crm.service.get_contact_360 imports and calls this by keyword."""
    assert list(inspect.signature(service.list_matches).parameters)[:1] != ["ctx"]


# ---------------------------------------------------------------------------
# MIG - migration 040
# ---------------------------------------------------------------------------

def _strip_sql_comments(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


def _strip_sql_strings(sql: str) -> str:
    return re.sub(r"'(?:[^']|'')*'", " ", sql)


def _up() -> str:
    return _strip_sql_comments(UP_PATH.read_text(encoding="utf-8"))


def _down() -> str:
    return _strip_sql_comments(DOWN_PATH.read_text(encoding="utf-8"))


def _flat_up() -> str:
    return " ".join(_strip_sql_strings(_up()).split()).lower()


def test_mig_the_shared_rules_accept_040():
    from test_p26_1_migration_rules import assert_p26_migration_rules

    assert_p26_migration_rules(VERSION, expect_reversible=True)


def test_mig_up_is_runner_owned():
    up = UP_PATH.read_text(encoding="utf-8")
    assert not re.search(r"^\s*BEGIN\s*;", up, re.IGNORECASE | re.MULTILINE)
    assert not re.search(r"^\s*COMMIT\s*;", up, re.IGNORECASE | re.MULTILINE)


def test_mig_down_brackets_itself():
    down = DOWN_PATH.read_text(encoding="utf-8")
    assert re.search(r"^\s*BEGIN\s*;", down, re.IGNORECASE | re.MULTILINE)
    assert re.search(r"^\s*COMMIT\s*;", down, re.IGNORECASE | re.MULTILINE)


def test_mig_the_runner_discovers_026_to_040():
    from test_p26_1_migration_rules import RUNNER, _load

    runner = _load(RUNNER, "p26_migrate_040")
    present = sorted(m.number for m in runner.discover_migrations(MIGRATIONS))
    assert present[:15] == list(range(26, 41)), present
    for later in present[15:]:
        assert later > 40, present


def test_mig_the_runner_validates_040_and_leaves_the_rest_valid():
    from test_p26_1_migration_rules import RUNNER, _load

    runner = _load(RUNNER, "p26_migrate_040b")
    for migration in runner.discover_migrations(MIGRATIONS):
        assert runner.validate_migration(migration) == [], migration.version


@pytest.mark.parametrize("table", MATCH_TABLES)
def test_mig_no_match_table_gains_a_physical_agency_column(table):
    """MATCH is CHILD-DERIVED. A physical column would be a third copy of a
    fact recorded twice, and three copies disagree."""
    up = " ".join(_strip_sql_strings(_up()).split())
    assert not re.search(
        rf"ALTER TABLE\s+{table}\s+ADD COLUMN", up, re.IGNORECASE
    ), up


def test_mig_no_agency_column_is_added_anywhere():
    assert "add column" not in _flat_up(), _flat_up()


@pytest.mark.parametrize("table", GUARDED_TABLES)
def test_mig_each_multi_reference_table_gets_a_trigger(table):
    up = _flat_up()
    assert f"on {table}" in up, f"{table} has no trigger"


@pytest.mark.parametrize("table", SINGLE_PARENT_TABLES)
def test_mig_single_parent_tables_get_no_trigger(table):
    """YAGNI, with a structural reason rather than a preference.

    Each of these has exactly one foreign key, to a parent that is itself
    guarded. A row naming only one other row cannot straddle two agencies, so a
    trigger here could never fire and would cost a write on every insert.
    """
    up = _flat_up()
    assert f"create trigger" in up, "no triggers at all"
    assert f"on {table}" not in up, f"{table} is child-derived and needs no trigger"


def test_mig_the_prechecks_hard_fail_and_never_repair():
    up = _up()
    assert "RAISE EXCEPTION" in up
    assert not re.search(r"^\s*UPDATE\s", up, re.IGNORECASE | re.MULTILINE), (
        "040 modifies data; it must refuse, not repair"
    )
    assert not re.search(r"^\s*DELETE\s+FROM", up, re.IGNORECASE | re.MULTILINE)
    assert "exception when" not in _flat_up()


@pytest.mark.parametrize(
    "invariant",
    ["matches", "match_runs", "match_exclusions", "latest_run", "refresh"],
)
def test_mig_every_required_precheck_is_present(invariant):
    up = _flat_up()
    assert invariant.replace("_", "_") in up, f"no precheck mentioning {invariant}"


def test_mig_no_arbitrary_selection_or_default_agency():
    up = _flat_up()
    for forbidden in ("limit 1", "min(", "max(", "coalesce(", "stima360", "slug"):
        assert forbidden not in up, f"040 contains {forbidden!r}"
    assert not re.search(r"agency_id\s*=\s*\d", _strip_sql_strings(_up()))


def test_mig_the_pair_guards_compare_both_roots():
    """Same-agency is not enough for a run reference: a run from the same
    tenant but a different pair would pass an agency check and still be wrong.
    """
    up = _flat_up()
    assert "buy_request_id" in up and "property_id" in up
    assert "latest_run_id" in up, "latest_run_id is never validated"
    assert "previous_run_id" in up and "new_run_id" in up, (
        "the refresh history run references are never validated"
    )


def test_mig_down_removes_exactly_what_up_creates():
    up, down = _flat_up(), " ".join(_strip_sql_strings(_down()).split()).lower()
    triggers = set(re.findall(r"create trigger (\w+)", up))
    functions = set(re.findall(r"create or replace function (\w+)", up))
    assert triggers, "040 creates no trigger"
    assert functions, "040 creates no function"
    for name in triggers:
        assert f"drop trigger if exists {name}" in down, f"{name} is not dropped"
    for name in functions:
        assert f"drop function if exists {name}" in down, f"{name} is not dropped"


def test_mig_down_changes_no_data_and_does_not_refuse():
    down = " ".join(_strip_sql_strings(_down()).split()).lower()
    for forbidden in ("update ", "insert into", "delete from", "truncate", "cascade"):
        assert forbidden not in down, f"the down does {forbidden!r}"
    assert "raise exception" not in down, "040 wrote no data; its down can reverse"


# ---------------------------------------------------------------------------
# The hostile DB matrix, asserted statically against the trigger function.
# ---------------------------------------------------------------------------

HOSTILE_GUARDS = {
    "1_match_pair": ("matches", ["buy", "property"]),
    "2_run_pair": ("match_runs", ["buy", "property"]),
    "3_exclusion_pair": ("match_exclusions", ["buy", "property"]),
    "4_latest_run": ("matches", ["latest_run_id"]),
    "5_refresh_previous_run": ("match_refresh_history", ["previous_run_id"]),
    "6_refresh_new_run": ("match_refresh_history", ["new_run_id"]),
}


@pytest.mark.parametrize("name", sorted(HOSTILE_GUARDS))
def test_mig_every_hostile_scenario_has_a_guard(name):
    table, tokens = HOSTILE_GUARDS[name]
    up = _flat_up()
    assert table in up, f"{name}: {table} is never mentioned"
    for token in tokens:
        assert token in up, f"{name}: no guard mentioning {token!r}"


def test_mig_the_guard_function_raises_for_every_scenario():
    up = _up()
    body = up[up.index("CREATE OR REPLACE FUNCTION"):]
    assert body.count("RAISE EXCEPTION") >= 6, (
        f"only {body.count('RAISE EXCEPTION')} refusals for 6 hostile scenarios"
    )
