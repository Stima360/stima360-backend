"""P26-5 - PROPOSAL and SALE agency isolation.

Offline. The application tests drive the real repositories against an
agency-aware fake cursor; the migration tests read SQL text.

THE CHAIN

    BUY[A] + PROPERTY[A] -> MATCH -> PROPOSAL -> SALE

Neither a proposal nor a sale has an agency of its own. A proposal names one
match, and a match is already certified by P26-4 to join a buy request and a
property in the same agency; a sale names a property, a buy request and a
proposal, which must all describe that same chain.

So there are two distinct invariants here, and conflating them is the easiest
way to get this wrong:

    CROSS-AGENCY          a sale in agency A citing agency B's property
    SAME-AGENCY WRONG PAIR a sale in agency A citing agency A's *other*
                          property - one that belongs to a different match

The second is not a tenancy breach and no agency comparison would catch it, but
it corrupts the chain just as badly: the sale would complete, mark the wrong
property sold and satisfy the wrong buy request. Both are asserted below.

WHICH TABLES NEED A DB GUARD

    property_proposals    match_id only              -> NO trigger
    property_sales        property + buy + proposal  -> guard
    property_sale_sellers sale_id + contact_id       -> guard

`property_proposals` is excluded for the same structural reason P26-2E excluded
match_feedback: exactly one foreign key, to a parent already guarded. A row that
names one other row cannot straddle two agencies, so a trigger could never fire
while costing a write on every insert.

IDEMPOTENCY

`idempotency_key` is UNIQUE globally on both tables. That is left alone - it is
a technical collision domain, and making it per-agency is not possible without a
physical agency_id, which this slice deliberately does not add. What must not
happen is *leakage*: agency A replaying a key that belongs to agency B's record
must never receive that record. A refuses with a conflict; it learns only that
the key is taken, which is unavoidable for a globally unique key and is not the
record.
"""
from __future__ import annotations

import ast
import inspect
import re
from contextlib import contextmanager
from pathlib import Path

import pytest

from core.exceptions import ConflictError, NotFoundError
from operator_auth.context import OperatorContext
from operator_auth.exceptions import PlatformAdminAgencyRequired
from proposal import repository as proposal_repository
from sale import repository as sale_repository

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"

VERSION = "042_p26_sales_proposals_agency_enforce"
UP_PATH = MIGRATIONS / f"{VERSION}.sql"
DOWN_PATH = MIGRATIONS / f"{VERSION}_down.sql"

AGENCY_DEPENDENCY = "legacy_basic_agency_context"
PROPOSAL_ROUTES = 5
SALE_ROUTES = 6

A, B = 31, 42          # two agencies; deliberately not 1

GUARDED_TABLES = ("property_sales", "property_sale_sellers")
SINGLE_PARENT_TABLES = ("property_proposals",)
ALL_TABLES = GUARDED_TABLES + SINGLE_PARENT_TABLES


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
# An agency-aware fake cursor.
#
# It honours the agency predicate the SQL actually carries: a query that forgot
# it SEES the foreign row, so the test fails instead of passing because a canned
# response happened to be empty.
# ---------------------------------------------------------------------------

class ScopeCursor:
    def __init__(self, *, owner=B, rows=None):
        self.owner = owner       # the agency every stored row belongs to
        self.rows = list(rows or [])
        self.statements: list[tuple[str, object]] = []
        self.current = None

    def execute(self, sql, params=None):
        flat = " ".join(str(sql).split())
        low = flat.lower()
        self.statements.append((flat, params))
        p = list(params or [])
        scoped = "agency_id=%s" in low.replace(" ", "")
        asked = [x for x in p if x in (A, B)]

        if self.rows:
            self.current = self.rows.pop(0)
            return

        if low.startswith(("insert", "update", "delete")):
            if scoped and self.owner not in asked:
                self.current = None
                return
            self.current = self._payload()
            return

        # A read that carries no agency predicate returns the row whatever the
        # caller's scope: that is the leak the assertions are looking for.
        if scoped and self.owner not in asked:
            self.current = None
            return
        self.current = self._payload()

    def _payload(self):
        return {
            "id": 1, "match_id": 1, "proposal_id": 1,
            "buy_request_id": 1, "property_id": 1, "contact_id": 1,
            "status": "draft", "amount": 100, "sale_price": 100,
            "commercial_status": "available", "agency_id": self.owner,
            "expires_at": None, "notes": None, "database_now": None,
            "ownership_share": None, "role": "owner",
        }

    def fetchone(self):
        return self.current

    def fetchall(self):
        return [self.current] if self.current else []


@contextmanager
def _install(module, cursor):
    original = module.core_cursor

    @contextmanager
    def fake(*_a, **_k):
        yield None, cursor

    module.core_cursor = fake
    try:
        yield cursor
    finally:
        module.core_cursor = original


def _rendered_source(function) -> str:
    """The function's source with its module's SQL fragments substituted in.

    The repositories compose their statements from module constants
    (`_CHAIN_JOIN`, `_CHAIN_SCOPE`, `_SALE_JOIN`, `_SALE_SCOPE`), which is
    exactly where the agency predicate lives. A probe reading the raw source
    sees `{_CHAIN_SCOPE}` and concludes the query is unscoped - so it resolves
    them first and matches against the SQL that is actually issued.
    """
    module = inspect.getmodule(function)
    source = inspect.getsource(function)
    for name in ("_CHAIN_JOIN", "_CHAIN_SCOPE", "_SALE_JOIN", "_SALE_SCOPE"):
        value = getattr(module, name, None)
        if isinstance(value, str):
            source = source.replace("{" + name + "}", value)
    return source


def _assert_idempotency_lookups_are_scoped(function, table: str) -> None:
    """Every SELECT that resolves an idempotency key must carry the scope.

    Found by locating the key predicate and reading back to the SELECT that
    contains it, rather than by matching a fixed statement shape - the previous
    version of this probe looked for `SELECT * FROM <table>` and silently
    matched nothing once the query became `SELECT ps.* FROM <table> ps`.
    """
    source = _rendered_source(function)
    statements = re.findall(
        rf"SELECT[^;]*?FROM {table}\b[^;]*?idempotency_key=%s[^\"]*", source
    )
    assert statements, f"no idempotency lookup found on {table}"
    for statement in statements:
        flat = " ".join(statement.split()).replace(" ", "")
        assert "agency_id=%s" in flat, statement


def _blocked(module, fn, *args, owner=B, rows=None, **kwargs):
    cur = ScopeCursor(owner=owner, rows=rows)
    with _install(module, cur):
        try:
            fn(*args, **kwargs)
            return None, cur
        except Exception as exc:  # noqa: BLE001 - type asserted by callers
            return exc, cur


# ---------------------------------------------------------------------------
# Route surface
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


def test_1_proposal_route_count():
    routes, _ = _routes("proposal")
    assert len(routes) == PROPOSAL_ROUTES, sorted(routes)


def test_2_every_proposal_route_is_scoped():
    routes, scoped = _routes("proposal")
    assert not sorted(set(routes) - set(scoped)), sorted(set(routes) - set(scoped))


def test_17_sale_route_count():
    routes, _ = _routes("sale")
    assert len(routes) == SALE_ROUTES, sorted(routes)


def test_18_every_sale_route_is_scoped():
    routes, scoped = _routes("sale")
    assert not sorted(set(routes) - set(scoped)), sorted(set(routes) - set(scoped))


@pytest.mark.parametrize("package", ("proposal", "sale"))
def test_3_19_no_schema_exposes_a_client_owned_agency(package):
    import importlib

    from pydantic import BaseModel

    schemas = importlib.import_module(f"{package}.schemas")
    models = [
        (n, o) for n, o in vars(schemas).items()
        if isinstance(o, type) and issubclass(o, BaseModel) and o is not BaseModel
    ]
    assert models, f"no {package} schemas found"
    for name, model in models:
        assert "agency_id" not in getattr(model, "model_fields", {}), name


# ---------------------------------------------------------------------------
# PROPOSAL scoping
# ---------------------------------------------------------------------------

def test_5_create_against_a_foreign_match_fails_closed():
    exc, cur = _blocked(
        proposal_repository, proposal_repository.create_proposal_scoped,
        ctx(A), {"match_id": 9, "amount": 1, "expires_at": None,
                 "idempotency_key": "k"}, "actor",
    )
    assert isinstance(exc, NotFoundError), exc
    assert not [s for s in cur.statements if s[0].lower().startswith("insert")], (
        "a proposal was written for a foreign match"
    )


def test_4_16_create_against_an_own_match_reaches_the_scoped_relation():
    """Positive control: the matrix must not simply block everything."""
    exc, cur = _blocked(
        proposal_repository, proposal_repository.create_proposal_scoped,
        ctx(A), {"match_id": 9, "amount": 1, "expires_at": None,
                 "idempotency_key": "k"}, "actor",
        owner=A,
    )
    assert not isinstance(exc, NotFoundError), exc
    relation = [s for s in cur.statements if "from matches" in s[0].lower()]
    assert relation, cur.statements
    assert "agency_id" in relation[0][0].lower(), relation[0][0]


def test_8_get_a_foreign_proposal_is_not_found():
    exc, _ = _blocked(proposal_repository, proposal_repository.get_proposal_scoped, ctx(A), 1)
    assert isinstance(exc, NotFoundError), exc


def test_7_12_list_is_scoped_and_filters_do_not_bypass_it():
    for filters in ({}, {"match_id": 1}, {"buy_request_id": 1},
                    {"property_id": 1}, {"contact_id": 1}):
        _, cur = _blocked(
            proposal_repository, proposal_repository.list_proposals_scoped,
            ctx(A), **filters,
        )
        reads = [s for s in cur.statements if "from property_proposals" in s[0].lower()]
        assert reads, (filters, cur.statements)
        for sql, params in reads:
            assert "agency_id" in sql.lower(), (filters, sql)
            assert A in list(params or []), (filters, params)


def test_9_update_a_foreign_proposal_is_not_found():
    exc, cur = _blocked(
        proposal_repository, proposal_repository.update_proposal_scoped,
        ctx(A), 1, {"notes": "x"}, "actor",
    )
    assert isinstance(exc, NotFoundError), exc
    assert not [s for s in cur.statements if s[0].lower().startswith("update")]


def test_10_transition_of_a_foreign_proposal_is_not_found():
    exc, cur = _blocked(
        proposal_repository, proposal_repository.transition_proposal_scoped,
        ctx(A), 1, "submitted", "actor",
    )
    assert isinstance(exc, NotFoundError), exc
    assert not [s for s in cur.statements if s[0].lower().startswith("update")]


def test_11_accepted_uniqueness_does_not_look_across_agencies():
    """A's transition must not be blocked by B's accepted proposal.

    An unscoped uniqueness probe would let one tenant's data cause a visible
    conflict in another - an inference channel, even though no row is returned.
    """
    source = _rendered_source(proposal_repository.transition_proposal_scoped)
    probe = re.search(
        r"SELECT pp\.id FROM property_proposals pp(.*?)LIMIT 1", source, re.DOTALL
    )
    assert probe, source
    assert "agency_id=%s" in " ".join(probe.group(0).split()).replace(" ", ""), probe.group(0)


def test_6_a_foreign_idempotency_key_does_not_leak_the_record():
    """A replaying B's key gets a conflict, never B's proposal."""
    _assert_idempotency_lookups_are_scoped(
        proposal_repository.create_proposal_scoped, "property_proposals"
    )


def test_13_the_history_side_effect_carries_the_agency():
    """buy.repository.history validates its references when given an agency.

    Passing it keeps P26-3's trigger 039 meaningful for rows this module writes,
    instead of relying on the ctx-less legacy path.
    """
    source = inspect.getsource(proposal_repository)
    for function in ("create_proposal_scoped", "update_proposal_scoped",
                     "transition_proposal_scoped"):
        body = inspect.getsource(getattr(proposal_repository, function))
        assert "agency_id=agency_id" in body.replace(" ", ""), function


# ---------------------------------------------------------------------------
# SALE scoping
# ---------------------------------------------------------------------------

def test_21_create_from_a_foreign_proposal_fails_closed():
    exc, cur = _blocked(
        sale_repository, sale_repository.create_sale_scoped,
        ctx(A), {"proposal_id": 1, "idempotency_key": "k"}, "actor",
    )
    assert isinstance(exc, NotFoundError), exc
    assert not [s for s in cur.statements if s[0].lower().startswith("insert")]


def test_23_get_a_foreign_sale_is_not_found():
    exc, _ = _blocked(sale_repository, sale_repository.get_sale_scoped, ctx(A), 1)
    assert isinstance(exc, NotFoundError), exc


def test_24_list_is_scoped():
    for filters in ({}, {"status": "pending"}, {"property_id": 1},
                    {"buy_request_id": 1}):
        _, cur = _blocked(sale_repository, sale_repository.list_sales_scoped,
                          ctx(A), **filters)
        reads = [s for s in cur.statements if "from property_sales" in s[0].lower()]
        assert reads, (filters, cur.statements)
        for sql, params in reads:
            assert "agency_id" in sql.lower(), (filters, sql)
            assert A in list(params or []), (filters, params)


def test_25_update_a_foreign_sale_is_not_found():
    exc, cur = _blocked(sale_repository, sale_repository.update_sale_scoped,
                        ctx(A), 1, {"notes": "x"})
    assert isinstance(exc, NotFoundError), exc
    assert not [s for s in cur.statements if s[0].lower().startswith("update")]


def test_26_cancel_a_foreign_sale_is_not_found():
    exc, cur = _blocked(sale_repository, sale_repository.cancel_sale_scoped,
                        ctx(A), 1, "actor")
    assert isinstance(exc, NotFoundError), exc
    assert not [s for s in cur.statements if s[0].lower().startswith("update")]


def test_27_complete_a_foreign_sale_fails_before_any_write():
    """The most dangerous path: completing writes to the sale, the property,
    the property history and the buy request. None of it may happen."""
    exc, cur = _blocked(sale_repository, sale_repository.complete_sale_scoped,
                        ctx(A), 1, "actor")
    assert isinstance(exc, NotFoundError), exc
    writes = [s for s in cur.statements
              if s[0].lower().startswith(("update", "insert"))]
    assert not writes, f"complete_sale wrote before the scope check: {writes}"


def test_28_29_30_complete_resolves_every_root_inside_the_scope():
    """sale, property, buy request and proposal are each re-read in scope."""
    source = _rendered_source(sale_repository.complete_sale_scoped)
    flat = " ".join(source.split())
    for table in ("property_sales", "properties", "buy_requests", "property_proposals"):
        probe = re.search(rf"FROM {table}\b(.*?)FOR UPDATE", flat)
        assert probe, f"complete_sale does not lock {table}: {flat[:400]}"
        assert "agency_id=%s" in probe.group(0).replace(" ", ""), (table, probe.group(0))


def test_31_the_relation_chain_mismatch_guard_survives():
    """Same-agency wrong pair: a sale citing agency A's *other* property.

    No agency comparison catches this, and the scoped reads alone would not
    either - both rows are legitimately the caller's. The chain equality check
    is what refuses it.
    """
    source = _rendered_source(sale_repository.complete_sale_scoped)
    assert "relation chain mismatch" in source, source
    assert 'match_row["property_id"] != sale["property_id"]' in source
    assert 'match_row["buy_request_id"] != sale["buy_request_id"]' in source


def test_32_33_the_seller_snapshot_reads_only_same_agency_contacts():
    source = _rendered_source(sale_repository.create_sale_scoped)
    probe = re.search(r"FROM property_contacts(.*?)ORDER BY", source, re.DOTALL)
    assert probe, source
    flat = " ".join(probe.group(0).split()).replace(" ", "")
    assert "agency_id=%s" in flat, probe.group(0)


def test_22_a_foreign_sale_idempotency_key_does_not_leak_the_record():
    _assert_idempotency_lookups_are_scoped(
        sale_repository.create_sale_scoped, "property_sales"
    )


def test_20_36_create_from_an_own_proposal_reaches_the_scoped_lookups():
    """Positive control for SALE."""
    exc, cur = _blocked(
        sale_repository, sale_repository.create_sale_scoped,
        ctx(A), {"proposal_id": 1, "idempotency_key": "k"}, "actor", owner=A,
    )
    assert not isinstance(exc, NotFoundError), exc
    reads = [s for s in cur.statements if "from property_proposals" in s[0].lower()]
    assert reads and "agency_id" in reads[0][0].lower(), cur.statements


# ---------------------------------------------------------------------------
# Unbound platform admin, forged agency, legacy Basic
# ---------------------------------------------------------------------------

UNBOUND_CALLS = [
    (proposal_repository, "get_proposal_scoped", (1,)),
    (proposal_repository, "list_proposals_scoped", ()),
    (proposal_repository, "update_proposal_scoped", (1, {}, "a")),
    (proposal_repository, "transition_proposal_scoped", (1, "submitted", "a")),
    (sale_repository, "get_sale_scoped", (1,)),
    (sale_repository, "list_sales_scoped", ()),
    (sale_repository, "update_sale_scoped", (1, {})),
    (sale_repository, "complete_sale_scoped", (1, "a")),
    (sale_repository, "cancel_sale_scoped", (1, "a")),
]


@pytest.mark.parametrize("module,name,args", UNBOUND_CALLS,
                         ids=[f"{m.__name__}.{n}" for m, n, _ in UNBOUND_CALLS])
def test_14_34_an_unbound_platform_admin_fails_closed(module, name, args):
    exc, cur = _blocked(module, getattr(module, name), unbound(), *args)
    assert isinstance(exc, PlatformAdminAgencyRequired), (name, exc)
    assert cur.statements == [], f"{name} queried before refusing"


@pytest.mark.parametrize("module", (proposal_repository, sale_repository))
def test_forged_agency_is_never_read_from_caller_data(module):
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    keys = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            if isinstance(node.slice.value, str):
                keys.append(node.slice.value)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in {"get", "pop", "setdefault"} and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    keys.append(first.value)
    leaked = [k for k in keys if k.lower() in ("agency_id", "agency", "tenant_id")]
    assert not leaked, f"{module.__name__} reads {leaked} from caller data"


@pytest.mark.parametrize("module", (proposal_repository, sale_repository))
def test_15_35_no_hardcoded_or_default_agency(module):
    source = Path(module.__file__).read_text(encoding="utf-8")
    assert not re.search(r"agency_id\s*=\s*\d", source), source
    assert "stima360" not in source
    assert "resolve_default_agency_id" not in source
    assert AGENCY_DEPENDENCY not in source, "the repository builds its own context"


@pytest.mark.parametrize("package", ("proposal", "sale"))
def test_15_35_only_the_router_resolves_the_context(package):
    assert AGENCY_DEPENDENCY in (ROOT / package / "router.py").read_text(encoding="utf-8")
    assert AGENCY_DEPENDENCY not in (ROOT / package / "service.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Legacy compatibility
# ---------------------------------------------------------------------------

LEGACY = {
    proposal_repository: {
        "create_proposal": ["data", "created_by"],
        "get_proposal": ["proposal_id"],
        "update_proposal": ["proposal_id", "data", "created_by"],
        "transition_proposal": ["proposal_id", "target_status", "created_by"],
    },
    sale_repository: {
        "create_sale": ["data", "created_by"],
        "get_sale": ["sale_id"],
        "update_sale": ["sale_id", "data"],
        "complete_sale": ["sale_id", "actor"],
        "cancel_sale": ["sale_id", "actor"],
    },
}


@pytest.mark.parametrize(
    "module,name,params",
    [(m, n, p) for m, d in LEGACY.items() for n, p in d.items()],
    ids=[f"{m.__name__}.{n}" for m, d in LEGACY.items() for n in d],
)
def test_legacy_signatures_are_unchanged(module, name, params):
    """tests/test_next6_p1_proposals.py and tests/test_sale_01_p10.py call
    these directly. BUY and MATCH established the pattern: add `_scoped`
    functions rather than change contracts other callers depend on."""
    assert list(inspect.signature(getattr(module, name)).parameters) == params


@pytest.mark.parametrize("module", (proposal_repository, sale_repository))
def test_legacy_list_signature_is_keyword_only_and_unchanged(module):
    name = "list_proposals" if module is proposal_repository else "list_sales"
    parameters = inspect.signature(getattr(module, name)).parameters
    assert "ctx" not in parameters, name


# ---------------------------------------------------------------------------
# Migration 042
# ---------------------------------------------------------------------------

def _strip_comments(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


def _strip_strings(sql: str) -> str:
    return re.sub(r"'(?:[^']|'')*'", " ", sql)


def _up() -> str:
    return _strip_comments(UP_PATH.read_text(encoding="utf-8"))


def _down() -> str:
    return _strip_comments(DOWN_PATH.read_text(encoding="utf-8"))


def _flat_up() -> str:
    return " ".join(_strip_strings(_up()).split()).lower()


def test_mig_shared_rules_accept_042():
    from test_p26_1_migration_rules import assert_p26_migration_rules

    assert_p26_migration_rules(VERSION, expect_reversible=True)


def test_mig_transaction_ownership():
    up = UP_PATH.read_text(encoding="utf-8")
    down = DOWN_PATH.read_text(encoding="utf-8")
    assert not re.search(r"^\s*BEGIN\s*;", up, re.IGNORECASE | re.MULTILINE)
    assert not re.search(r"^\s*COMMIT\s*;", up, re.IGNORECASE | re.MULTILINE)
    assert re.search(r"^\s*BEGIN\s*;", down, re.IGNORECASE | re.MULTILINE)
    assert re.search(r"^\s*COMMIT\s*;", down, re.IGNORECASE | re.MULTILINE)


def test_mig_runner_discovers_026_to_042_and_validates_all():
    from test_p26_1_migration_rules import RUNNER, _load

    runner = _load(RUNNER, "p26_migrate_042")
    present = sorted(m.number for m in runner.discover_migrations(MIGRATIONS))
    assert present[:17] == list(range(26, 43)), present
    for migration in runner.discover_migrations(MIGRATIONS):
        assert runner.validate_migration(migration) == [], migration.version


def test_mig_040_and_041_are_untouched():
    """Both are applied on TEST; their checksums must stay immutable."""
    forty = (MIGRATIONS / "040_p26_match_agency_enforce.sql").read_text(encoding="utf-8")
    assert re.search(r"IF\s+TG_TABLE_NAME\s*=\s*'matches'\s+AND\s+NEW\.latest_run_id", forty)
    forty_one = (MIGRATIONS / "041_p26_match_trigger_record_fix.sql").read_text(encoding="utf-8")
    assert re.search(
        r"IF TG_TABLE_NAME = 'matches' THEN\s+IF NEW\.latest_run_id IS NOT NULL THEN",
        " ".join(forty_one.split()),
    )


@pytest.mark.parametrize("table", ALL_TABLES)
def test_mig_no_physical_agency_column_is_added(table):
    up = " ".join(_strip_strings(_up()).split())
    assert not re.search(rf"ALTER TABLE\s+{table}\s+ADD COLUMN", up, re.IGNORECASE), up


def test_mig_no_column_is_added_anywhere():
    assert "add column" not in _flat_up()


@pytest.mark.parametrize("table", GUARDED_TABLES)
def test_mig_each_multi_reference_table_gets_a_trigger(table):
    assert f"on {table}" in _flat_up(), f"{table} has no trigger"


@pytest.mark.parametrize("table", SINGLE_PARENT_TABLES)
def test_mig_single_parent_tables_get_no_trigger(table):
    """property_proposals names one row, and matches is already guarded by
    P26-4. A trigger could never fire and would cost every insert."""
    up = _flat_up()
    assert "create trigger" in up, "no triggers at all"
    assert f"on {table}" not in up, f"{table} is child-derived and needs no trigger"


def test_mig_the_trigger_functions_are_table_safe():
    """The 040 lesson: a condition that decides TG_TABLE_NAME must not read a
    field that only some of the guarded tables have.

    Enforced here by construction - separate functions per table - or by nested
    branching. Either satisfies this rule; a conjunction does not.
    """
    body = _strip_comments(UP_PATH.read_text(encoding="utf-8"))
    offenders = []
    for match in re.finditer(r"\b(?:ELSIF|IF)\b(.*?)\bTHEN\b", body, re.DOTALL | re.IGNORECASE):
        condition = " ".join(match.group(1).split())
        if "TG_TABLE_NAME" not in condition:
            continue
        if re.search(r"NEW\.\w+", condition):
            offenders.append(condition)
    assert not offenders, offenders


def test_mig_prechecks_hard_fail_and_never_repair():
    up = _up()
    assert up.count("RAISE EXCEPTION") >= 5, up.count("RAISE EXCEPTION")
    assert not re.search(r"^\s*UPDATE\s", up, re.IGNORECASE | re.MULTILINE)
    assert not re.search(r"^\s*DELETE\s+FROM", up, re.IGNORECASE | re.MULTILINE)
    assert "exception when" not in _flat_up()


@pytest.mark.parametrize(
    "precheck",
    ["property_sales", "property_sale_sellers", "property_proposals",
     "buy_requests", "properties", "matches", "contacts"],
)
def test_mig_every_root_is_consulted(precheck):
    assert precheck in _flat_up(), f"042 never consults {precheck}"


def test_mig_no_arbitrary_selection_or_default_agency():
    up = _flat_up()
    for forbidden in ("limit 1", "min(", "max(", "coalesce(", "stima360", "slug"):
        assert forbidden not in up, f"042 contains {forbidden!r}"
    assert not re.search(r"agency_id\s*=\s*\d", _strip_strings(_up()))


def test_mig_down_removes_exactly_what_up_creates():
    up, down = _flat_up(), " ".join(_strip_strings(_down()).split()).lower()
    triggers = set(re.findall(r"create trigger (\w+)", up))
    functions = set(re.findall(r"create or replace function (\w+)", up))
    assert triggers and functions
    for name in triggers:
        assert f"drop trigger if exists {name}" in down, name
    for name in functions:
        assert f"drop function if exists {name}" in down, name


def test_mig_down_changes_no_data_and_does_not_refuse():
    down = " ".join(_strip_strings(_down()).split()).lower()
    for forbidden in ("update ", "insert into", "delete from", "truncate", "cascade"):
        assert forbidden not in down, forbidden
    assert "raise exception" not in down


# ---------------------------------------------------------------------------
# Hostile DB matrix - static, against the guard functions.
#
# H1-H5 are cross-agency or chain breaks on INSERT; H6-H9 are the same breaks
# introduced by an UPDATE, which an insert-only guard would miss entirely.
# ---------------------------------------------------------------------------

HOSTILE = {
    "H1_sale_property_A_buy_B": ["buy_requests", "properties", "agency"],
    "H2_sale_property_wrong_pair": ["property_id"],
    "H3_sale_buy_wrong_pair": ["buy_request_id"],
    "H4_sale_proposal_foreign": ["proposal_id", "matches"],
    "H5_seller_contact_foreign": ["property_sale_sellers", "contacts"],
    "H6_update_sale_property": ["update of"],
    "H7_update_sale_buy": ["update of"],
    "H8_update_sale_proposal": ["update of"],
    "H9_update_seller_contact": ["update of"],
}


@pytest.mark.parametrize("name", sorted(HOSTILE))
def test_hostile_scenario_has_a_guard(name):
    up = _flat_up()
    for token in HOSTILE[name]:
        assert token in up, f"{name}: no guard mentioning {token!r}"


def test_hostile_updates_are_covered_not_only_inserts():
    """An insert-only trigger leaves the same breach one statement later."""
    up = _flat_up()
    for table in GUARDED_TABLES:
        block = up[up.index(f"on {table}") - 400: up.index(f"on {table}") + 60]
        assert "before insert or update of" in block, (table, block)


def test_hostile_same_agency_wrong_pair_is_guarded_in_the_db_too():
    """Not only in the application. The chain equality is asserted by the
    trigger, so a direct SQL write cannot break it either."""
    up = _flat_up()
    assert "m.property_id" in up or "match" in up, up
    assert "new.property_id" in up, up
    assert "new.buy_request_id" in up, up
