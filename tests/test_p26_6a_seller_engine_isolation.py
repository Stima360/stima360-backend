"""P26-6A - SELLER ENGINE agency isolation (P17 timeline, P18 follow-up, P19 intent).

Offline. The application tests drive the real repositories against an
agency-aware fake cursor; the migration tests read SQL text.

THE CHAIN

    SELLER TIMELINE -> AUTOMATED FOLLOW-UP -> TASKS -> SELLER INTENT

OWNERSHIP, AND WHY IT DIFFERS FROM EVERY EARLIER SLICE

`seller_timeline_events` and `followup_actions` get a *physical* `agency_id`,
which P26-2 through P26-5 deliberately refused for every child table. The
difference is in the schema: all four references on a timeline event
(contact_id, lead_id, stima_id, property_id) and all four on a follow-up action
are NULLABLE and ON DELETE SET NULL. Deleting a contact does not delete the
event - it blanks the reference. A historical row can therefore outlive every
parent it derived from, and a derived-only design would let it become a row with
no tenant at all. These are logs, and a log that forgets whose it is cannot be
scoped later.

`seller_intent` is calculated on demand and stores nothing, so it gets no column:
its tenancy is whatever the caller's lead is.

TWO WRITE PATHS, ONE RULE

The timeline has an operator path (`POST /api/seller-intelligence/events`) and a
system path (`main.py`'s public STIMA funnel, which has no operator at all).
Both must stamp an agency, and neither may take it from a client:

    operator -> ctx.require_agency(), and every reference must be in it
    system   -> derived from the references themselves, exactly one distinct
                agency or a hard failure

The second rule is the same one migration 044 uses to backfill history. Having
one rule serving both is deliberate: the write path and the backfill cannot
disagree about what an event's agency is.

Coverage map:

    1-6    structure: route inventory, scoping, schemas, actor
    7-16   timeline create and list
    17-25  follow-up scan, task creation, actions, background boundary
    26-32  seller intent
    33-51  migrations 043 / 044 / 045
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

A, B = 51, 62          # two agencies; deliberately not 1

COLUMNS = "043_p26_seller_engine_agency_columns"
BACKFILL = "044_p26_seller_engine_agency_backfill"
ENFORCE = "045_p26_seller_engine_agency_enforce"

OWNED_TABLES = ("seller_timeline_events", "followup_actions")
REFERENCE_TABLES = ("contacts", "leads", "stime", "properties", "tasks")

EXPECTED_ROUTES = {
    "seller_intelligence": 2,
    "followup": 1,
    "seller_intent": 1,
}


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
# An agency-aware fake cursor: it honours the predicate the SQL carries, so a
# query that forgot it SEES the foreign row and the test fails, rather than
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
        low = flat.lower()
        self.statements.append((flat, params))
        values = list(params.values()) if isinstance(params, dict) else list(params or [])
        scoped = "agency_id = %s" in low or "agency_id=%s" in low.replace(" ", "")
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
            "id": 1, "agency_id": self.owner, "contact_id": 1, "lead_id": 1,
            "stima_id": 1, "property_id": 1, "task_id": 1, "status": "pending",
            "event_type": "stima_richiesta", "occurred_at": None,
            "idempotency_key": "k", "_created": True, "priority": "normal",
            "metadata": {}, "lead_status": "open", "lead_stage": "new",
            "has_stima_completata": False, "has_p18_followup_in_progress": False,
            "has_p18_followup_overdue": False, "latest_seller_origin_event_at": None,
        }

    def fetchone(self):
        return self.current

    def fetchall(self):
        return [self.current] if self.current else []


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



def _executable_source(function) -> str:
    """Source with the docstring removed.

    These rules ban tokens like `LIMIT 1` and `MIN(`, and the functions'
    docstrings explain why those are banned. Without stripping, the explanation
    fails the rule it explains - a trap this project has hit repeatedly.
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


def _scoped_sql(module, names: tuple[str, ...]) -> str:
    """The SQL of the *scoped* functions only.

    The legacy ctx-less functions remain in these modules on purpose - the public
    funnel and several historical tests call them - and they are deliberately not
    agency-filtered. A probe reading the whole module finds those first and
    reports a leak that is really the compatibility surface working as designed.
    """
    return "\n".join(
        inspect.getsource(getattr(module, name))
        for name in names
        if hasattr(module, name)
    )


def _run(module, attribute, fn, *args, owner=B, rows=None, **kwargs):
    cur = ScopeCursor(owner=owner, rows=rows)
    with _install(module, attribute, cur):
        try:
            return fn(*args, **kwargs), cur
        except Exception as exc:  # noqa: BLE001 - type asserted by callers
            return exc, cur


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


@pytest.mark.parametrize("package,expected", sorted(EXPECTED_ROUTES.items()))
def test_1_2_3_route_inventory(package, expected):
    routes, _ = _routes(package)
    assert len(routes) == expected, sorted(routes)


@pytest.mark.parametrize("package", sorted(EXPECTED_ROUTES))
def test_4_every_tenant_sensitive_route_is_scoped(package):
    routes, scoped = _routes(package)
    missing = sorted(set(routes) - set(scoped))
    assert not missing, f"{package}: routes without {AGENCY_DEPENDENCY}: {missing}"


@pytest.mark.parametrize("package", sorted(EXPECTED_ROUTES))
def test_5_no_schema_accepts_a_client_agency(package):
    import importlib

    from pydantic import BaseModel

    modules = [f"{package}.schemas", f"{package}.router"]
    found = False
    for name in modules:
        try:
            module = importlib.import_module(name)
        except ModuleNotFoundError:
            continue
        for attribute, obj in vars(module).items():
            if isinstance(obj, type) and issubclass(obj, BaseModel) and obj is not BaseModel:
                found = True
                assert "agency_id" not in getattr(obj, "model_fields", {}), (name, attribute)
    assert found, f"no request models found for {package}"


def test_6_created_by_is_not_trusted_from_the_client():
    """The scan request used to carry `created_by`, so any caller could stamp
    an action with whatever actor string it liked. The actor is server-derived
    now, and the field must not reach the service."""
    source = (ROOT / "followup" / "router.py").read_text(encoding="utf-8")
    assert "created_by=payload.created_by" not in source, source
    assert "created_by: str | None" not in source, (
        "the scan request still declares a client-supplied actor"
    )


def test_6_the_timeline_route_does_not_forward_a_client_actor():
    source = (ROOT / "seller_intelligence" / "router.py").read_text(encoding="utf-8")
    assert "**payload.dict()" not in source and "**payload.model_dump()" not in source, (
        "the route splats the request model into the service, so any field the "
        "schema carries - created_by included - is trusted from the client"
    )


# ---------------------------------------------------------------------------
# 7-16 - timeline
# ---------------------------------------------------------------------------

def _si():
    from seller_intelligence import repository

    return repository


def test_7_an_own_event_is_created_with_the_context_agency():
    repository = _si()
    result, cur = _run(
        repository, "si_cursor", repository.insert_event_scoped,
        ctx(A), {"contact_id": 1, "event_type": "e", "payload": {}}, owner=A,
    )
    assert not isinstance(result, Exception), result
    inserts = [s for s in cur.statements if s[0].lower().startswith("insert into seller_timeline_events")]
    assert inserts, cur.statements
    sql, params = inserts[0]
    assert "agency_id" in sql.lower(), sql
    bound = list(params.values()) if isinstance(params, dict) else list(params)
    assert A in bound, bound


@pytest.mark.parametrize(
    "reference", ["contact_id", "lead_id", "stima_id", "property_id"]
)
def test_8_9_10_11_a_foreign_reference_is_refused(reference):
    from seller_intelligence.exceptions import ValidationError

    repository = _si()
    result, cur = _run(
        repository, "si_cursor", repository.insert_event_scoped,
        ctx(A), {reference: 99, "event_type": "e", "payload": {}}, owner=B,
    )
    assert isinstance(result, Exception), result
    assert not [s for s in cur.statements if s[0].lower().startswith("insert")], (
        f"an event was written referencing a foreign {reference}"
    )


def test_12_13_the_timeline_is_scoped_before_any_filter():
    repository = _si()
    _, cur = _run(repository, "si_cursor", repository.list_timeline_scoped, ctx(A))
    reads = [s for s in cur.statements if "seller_timeline_events" in s[0].lower()]
    assert reads, cur.statements
    for sql, params in reads:
        assert "agency_id" in sql.lower(), sql
        assert A in list(params or []), params


@pytest.mark.parametrize(
    "filters",
    [{"contact_id": 1}, {"lead_id": 1}, {"stima_id": 1}, {"property_id": 1}],
)
def test_14_no_filter_bypasses_the_agency_scope(filters):
    repository = _si()
    _, cur = _run(repository, "si_cursor", repository.list_timeline_scoped, ctx(A), **filters)
    reads = [s for s in cur.statements if "seller_timeline_events" in s[0].lower()]
    assert reads, (filters, cur.statements)
    for sql, params in reads:
        assert "agency_id" in sql.lower(), (filters, sql)
        assert A in list(params or []), (filters, params)


def test_15_the_idempotency_lookup_is_scoped():
    """A replaying B's globally unique key must not receive B's event."""
    source = _scoped_sql(_si(), ("_insert_event_with_agency", "list_timeline_scoped"))
    lookups = [
        statement for statement in re.findall(
            r"SELECT[^;\"]*FROM seller_timeline_events[^\"]*", source
        )
        if "idempotency_key" in statement
    ]
    assert lookups, source
    for lookup in lookups:
        assert "agency_id" in lookup, lookup


def test_16_an_unbound_platform_admin_cannot_write_or_read_the_timeline():
    repository = _si()
    for fn, args in (
        (repository.insert_event_scoped, ({"contact_id": 1, "event_type": "e"},)),
        (repository.list_timeline_scoped, ()),
    ):
        result, cur = _run(repository, "si_cursor", fn, unbound(), *args)
        assert isinstance(result, PlatformAdminAgencyRequired), (fn, result)
        assert cur.statements == [], f"{fn.__name__} queried before refusing"


# ---------------------------------------------------------------------------
# The system write path - public STIMA has no operator.
# ---------------------------------------------------------------------------

def test_system_events_derive_their_agency_from_the_references():
    """`main.py` records timeline events for anonymous estimations. There is no
    operator, so the agency is derived from the references - exactly one
    distinct agency, or a refusal. It is never taken from the caller."""
    repository = _si()
    assert hasattr(repository, "derive_agency_id"), (
        "no server-side derivation exists for the system write path"
    )
    sources = {
        "contact_id": "contacts", "lead_id": "leads",
        "stima_id": "stime", "property_id": "properties",
    }
    assert dict(repository._REFERENCE_SOURCES) == sources, repository._REFERENCE_SOURCES
    body = _executable_source(repository.derive_agency_id)
    assert "_REFERENCE_SOURCES" in body, body


def test_system_events_refuse_an_ambiguous_or_absent_agency():
    repository = _si()
    body = _executable_source(repository.derive_agency_id)
    assert body.count("raise") >= 2, body
    for forbidden in ("MIN(", "LIMIT 1", "stima360", "COALESCE("):
        assert forbidden not in body, f"derivation uses {forbidden!r}"


def test_the_public_funnel_still_calls_the_never_raising_wrapper():
    """main.py must keep working unchanged: the funnel swallows failures."""
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "seller_intelligence_service.safe_record_event(" in main
    assert "followup_service.safe_run_followup(" in main


# ---------------------------------------------------------------------------
# 17-25 - follow-up
# ---------------------------------------------------------------------------

def _fu():
    from followup import repository

    return repository


def test_17_the_scan_selects_only_its_own_agency():
    repository = _fu()
    _, cur = _run(
        repository, "followup_cursor",
        repository.list_temporal_escalation_candidates_for_agency,
        A, limit=10, rule_code="R",
    )
    reads = [s for s in cur.statements if "from tasks" in s[0].lower()]
    assert reads, cur.statements
    for sql, params in reads:
        assert "agency_id" in sql.lower(), sql
        assert A in list(params or []), params


def test_17_the_scan_does_not_filter_in_python():
    """A global SELECT narrowed afterwards would still have read every tenant."""
    source = inspect.getsource(_fu().list_temporal_escalation_candidates_for_agency)
    assert "agency_id" in source, source
    assert "for row in" not in source.split("cur.execute")[0], source


def test_18_19_20_21_the_action_and_task_carry_the_scan_agency():
    source = _scoped_sql(
        _fu(), ("_insert_pending_action_for_agency", "execute_temporal_escalation_for_agency")
    )
    insert = re.search(r"INSERT INTO followup_actions[^\"]*", source)
    assert insert, source
    assert "agency_id" in insert.group(0), insert.group(0)
    for statement in re.findall(r"UPDATE (?:tasks|followup_actions)[^\"]*", source):
        assert "agency_id" in statement, statement


def test_22_the_followup_idempotency_lookup_is_scoped():
    source = _scoped_sql(
        _fu(), ("_insert_pending_action_for_agency", "list_temporal_escalation_candidates_for_agency")
    )
    lookups = [
        statement for statement in re.findall(
            r"SELECT[^;\"]*FROM followup_actions[^\"]*", source
        )
        if "idempotency_key" in statement
    ]
    assert lookups, source
    for lookup in lookups:
        assert "agency_id" in lookup, lookup


def test_23_the_background_scan_has_an_explicit_per_agency_boundary():
    """A cron that swept the whole database would defeat every predicate above.

    The orchestrator may iterate agencies, but each cycle must be bounded.
    """
    from followup import service

    assert hasattr(service, "run_temporal_escalation_scan_for_agency"), (
        "no per-agency entry point exists for the background scan"
    )
    parameters = list(
        inspect.signature(service.run_temporal_escalation_scan_for_agency).parameters
    )
    assert parameters[0] == "agency_id", parameters


def test_23_any_orchestrator_iterates_agencies_one_at_a_time():
    from followup import service

    source = inspect.getsource(service)
    if "def run_temporal_escalation_scan_for_all_agencies" not in source:
        pytest.skip("no orchestrator in this slice")
    body = inspect.getsource(service.run_temporal_escalation_scan_for_all_agencies)
    assert "run_temporal_escalation_scan_for_agency" in body, body


def test_24_an_unbound_platform_admin_cannot_run_the_scan():
    from followup import service

    with pytest.raises(PlatformAdminAgencyRequired):
        service.run_temporal_escalation_scan_scoped(unbound(), limit=10)


def test_25_the_scan_route_takes_its_agency_from_the_legacy_basic_context():
    source = (ROOT / "followup" / "router.py").read_text(encoding="utf-8")
    assert AGENCY_DEPENDENCY in source, source
    assert "run_temporal_escalation_scan_scoped" in source, source


# ---------------------------------------------------------------------------
# 26-32 - seller intent
# ---------------------------------------------------------------------------

def _intent():
    from seller_intent import repository

    return repository


def test_26_an_own_lead_is_scored():
    repository = _intent()
    result, cur = _run(
        repository, "seller_intent_cursor",
        repository.get_lead_intent_inputs_scoped, ctx(A), 1, owner=A,
    )
    assert not isinstance(result, Exception), result
    assert cur.statements, "no query was issued"


def test_27_a_foreign_lead_is_not_found():
    from seller_intent.exceptions import NotFoundError

    from seller_intent import service

    result, _ = _run(
        _intent(), "seller_intent_cursor",
        service.get_seller_intent_score_scoped, ctx(A), lead_id=1, owner=B,
    )
    assert isinstance(result, NotFoundError), result


def test_28_29_30_31_every_subquery_stays_inside_the_agency():
    """The OR branches are the subtle part.

    `ste.lead_id = lc.id OR ste.stima_id IN (linked stime)` reaches rows through
    a second path that leaves the lead behind, so scoping the lead alone is not
    enough: another agency's event or task could contribute to this score. No row
    would be returned, but the number would silently describe someone else's
    activity.

    Checked per alias rather than by slicing after `FROM`: the queries contain
    `IN ('stima_richiesta', 'stima_completata')`, and a probe that stopped at the
    first closing parenthesis would truncate before ever reaching the predicate.
    """
    source = inspect.getsource(_intent().get_lead_intent_inputs_scoped)

    # Every table that can contribute a row must carry a tenant predicate on its
    # own alias.
    for alias, table in (
        ("l", "leads"),
        ("ste", "seller_timeline_events"),
        ("t", "tasks"),
        ("s", "stime"),
    ):
        assert re.search(rf"\b{table}\s+{alias}\b", source), f"{table} is never read"
        assert re.search(rf"\b{alias}\.agency_id\s*=\s*%s", source), (
            f"{table} (alias {alias}) is read without an agency predicate"
        )

    # lead_stime has no agency of its own: it is constrained through the two
    # rows it joins, so both must be scoped inside that CTE.
    cte = re.search(r"linked_stime AS \((.*?)\n            \)", source, re.DOTALL)
    assert cte, source
    assert "l.agency_id" in cte.group(1), cte.group(1)
    assert "s.agency_id" in cte.group(1), cte.group(1)


def test_28_29_30_31_the_alias_probe_is_not_vacuous():
    """Negative control: an unscoped variant must fail the same rule."""
    unscoped = "FROM seller_timeline_events ste WHERE ste.event_type = 'x'"
    assert not re.search(r"\bste\.agency_id\s*=\s*%s", unscoped)


def test_32_an_unbound_platform_admin_cannot_score():
    from seller_intent import service

    result, cur = _run(
        _intent(), "seller_intent_cursor",
        service.get_seller_intent_score_scoped, unbound(), lead_id=1,
    )
    assert isinstance(result, PlatformAdminAgencyRequired), result
    assert cur.statements == []


def test_the_legacy_intent_entry_point_still_exists_for_nba():
    """next_best_action/signals.py imports and calls this by keyword."""
    from seller_intent.service import get_seller_intent_score

    parameters = list(inspect.signature(get_seller_intent_score).parameters)
    assert parameters[0] != "ctx", parameters


# ---------------------------------------------------------------------------
# 33-51 - migrations
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
def test_mig_shared_rules(version):
    from test_p26_1_migration_rules import assert_p26_migration_rules

    # 044 writes data it cannot identify afterwards, so its rollback refuses.
    assert_p26_migration_rules(version, expect_reversible=version != BACKFILL)


@pytest.mark.parametrize("version", (COLUMNS, BACKFILL, ENFORCE))
def test_mig_transaction_ownership(version):
    up = (MIGRATIONS / f"{version}.sql").read_text(encoding="utf-8")
    down = (MIGRATIONS / f"{version}_down.sql").read_text(encoding="utf-8")
    assert not re.search(r"^\s*BEGIN\s*;", up, re.IGNORECASE | re.MULTILINE)
    assert not re.search(r"^\s*COMMIT\s*;", up, re.IGNORECASE | re.MULTILINE)
    assert re.search(r"^\s*BEGIN\s*;", down, re.IGNORECASE | re.MULTILINE)
    assert re.search(r"^\s*COMMIT\s*;", down, re.IGNORECASE | re.MULTILINE)


@pytest.mark.parametrize("table", OWNED_TABLES)
def test_33_043_adds_a_nullable_column_with_a_foreign_key(table):
    up = " ".join(_strip_strings(_sql(COLUMNS)).split())
    assert re.search(
        rf"ALTER TABLE {table}\s+ADD COLUMN IF NOT EXISTS agency_id\s+BIGINT",
        up, re.IGNORECASE,
    ), up
    assert re.search(rf"{table}_agency_id_fk", up, re.IGNORECASE), up
    assert "REFERENCES agencies (id)" in up or "REFERENCES agencies(id)" in up, up


def test_33_043_adds_no_not_null_and_no_default():
    flat = _flat(COLUMNS)
    assert "set not null" not in flat, flat
    assert "set default" not in flat, flat
    add_columns = re.findall(r"add column if not exists agency_id bigint([^;]*)", flat)
    assert add_columns, flat
    for declaration in add_columns:
        assert "not null" not in declaration, declaration
        assert "default" not in declaration, declaration


@pytest.mark.parametrize("table", OWNED_TABLES)
def test_33_043_indexes_the_new_column(table):
    assert f"idx_{table}_agency_id" in _flat(COLUMNS), _flat(COLUMNS)


def test_34_the_backfill_uses_only_deterministic_derivation():
    flat = _flat(BACKFILL)
    for forbidden in ("limit 1", "min(", "max(", "coalesce(", "stima360", "slug"):
        assert forbidden not in flat, f"044 contains {forbidden!r}"
    assert not re.search(r"agency_id\s*=\s*\d", _strip_strings(_sql(BACKFILL)))
    assert not re.search(r"\bfrom\s+agencies\b", flat), flat


def test_34_the_backfill_counts_distinct_candidate_agencies():
    assert re.search(r"count\s*\(\s*distinct", _flat(BACKFILL)), _flat(BACKFILL)


@pytest.mark.parametrize("table", REFERENCE_TABLES)
def test_34_every_reference_is_a_derivation_source(table):
    assert table in _flat(BACKFILL), f"044 never derives from {table}"


@pytest.mark.parametrize(
    "case", ["ambiguous_timeline", "orphan_timeline", "ambiguous_followup", "orphan_followup"]
)
def test_35_36_37_38_the_backfill_hard_fails_on_undecidable_history(case):
    up = _sql(BACKFILL)
    assert up.count("RAISE EXCEPTION") >= 4, (
        f"only {up.count('RAISE EXCEPTION')} refusals for four undecidable cases"
    )
    assert "exception when" not in _flat(BACKFILL), "044 swallows a failure"


def test_34_the_backfill_only_touches_null_rows():
    updates = re.findall(r"update\s+(\w+)\s+[^;]*", _flat(BACKFILL))
    assert updates, _flat(BACKFILL)
    for statement in re.findall(r"update\s+\w+[^;]*", _flat(BACKFILL)):
        assert "agency_id is null" in statement, statement


@pytest.mark.parametrize("table", OWNED_TABLES)
def test_39_045_sets_not_null(table):
    up = " ".join(_strip_strings(_sql(ENFORCE)).split())
    assert re.search(
        rf"ALTER TABLE {table}\s+ALTER COLUMN agency_id\s+SET NOT NULL",
        up, re.IGNORECASE,
    ), up


def test_39_045_prechecks_before_it_constrains():
    up = _sql(ENFORCE)
    flat = _flat(ENFORCE)
    precheck = flat.index("raise exception")
    not_null = flat.index("set not null")
    assert precheck < not_null, "045 constrains before it checks"
    assert "exception when" not in flat


MIGRATION_FUNCTIONS = (
    "seller_timeline_event_agency_integrity",
    "followup_action_agency_integrity",
)


@pytest.mark.parametrize("function", MIGRATION_FUNCTIONS)
def test_40_41_42_43_045_creates_two_separate_functions(function):
    assert f"create or replace function {function}" in _flat(ENFORCE), _flat(ENFORCE)


@pytest.mark.parametrize("table", OWNED_TABLES)
def test_40_41_42_43_the_trigger_fires_on_insert_and_update(table):
    flat = _flat(ENFORCE)
    assert f"on {table}" in flat, f"{table} has no trigger"
    block = flat[max(0, flat.index(f"on {table}") - 400): flat.index(f"on {table}") + 60]
    assert "before insert or update of" in block, (table, block)


@pytest.mark.parametrize("table", REFERENCE_TABLES)
def test_44_48_every_reference_is_checked_against_the_row_agency(table):
    assert table in _flat(ENFORCE), f"045 never validates {table}"


def test_49_no_table_unsafe_generic_trigger_pattern():
    """The 040 lesson: a condition deciding TG_TABLE_NAME must not read a field
    only some guarded tables have. Two separate functions make this structural,
    but the rule is asserted anyway."""
    body = _sql(ENFORCE)
    offenders = []
    for match in re.finditer(r"\b(?:ELSIF|IF)\b(.*?)\bTHEN\b", body, re.DOTALL | re.IGNORECASE):
        condition = " ".join(match.group(1).split())
        if "TG_TABLE_NAME" in condition and re.search(r"NEW\.\w+", condition):
            offenders.append(condition)
    assert not offenders, offenders


def test_50_the_down_files_remove_only_p26_6a_objects():
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


def test_50_the_backfill_rollback_refuses():
    """044 writes values it cannot tell apart from later ones afterwards."""
    down = _down(BACKFILL).lower()
    assert "raise exception" in down, down
    assert "irreversible" in down, down
    assert not re.search(r"set\s+agency_id\s*=\s*null", down), down


def test_51_the_runner_discovers_026_to_045_and_validates_all():
    from test_p26_1_migration_rules import RUNNER, _load

    runner = _load(RUNNER, "p26_migrate_045")
    present = sorted(m.number for m in runner.discover_migrations(MIGRATIONS))
    assert present[:20] == list(range(26, 46)), present
    for migration in runner.discover_migrations(MIGRATIONS):
        assert runner.validate_migration(migration) == [], migration.version


def test_51_migrations_026_to_042_are_untouched():
    """All are applied on TEST; their checksums must stay immutable."""
    forty = (MIGRATIONS / "040_p26_match_agency_enforce.sql").read_text(encoding="utf-8")
    assert re.search(r"IF\s+TG_TABLE_NAME\s*=\s*'matches'\s+AND\s+NEW\.latest_run_id", forty)
    forty_two = (MIGRATIONS / "042_p26_sales_proposals_agency_enforce.sql").read_text(encoding="utf-8")
    assert "property_sale_chain_integrity" in forty_two
