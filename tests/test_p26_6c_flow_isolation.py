"""P26-6C pass 2 - FLOW agency isolation.

FLOW is the automation engine: it scans six modules for candidates, evaluates
platform rules against them, and executes live actions. Before this slice its
entire HTTP surface was global, so one Basic-authenticated caller could scan,
simulate and *execute automation against* every agency's leads, properties, buy
requests, matches, visits and owner requests. That is not a read leak; it is a
write into another tenant's CRM.

WHAT MAKES FLOW DIFFERENT FROM EVERY EARLIER SLICE

Its tenancy is polymorphic. `entity_type` and `entity_id` are plain columns
with no foreign key, across six entity types in four modules, so a FLOW row
cannot be scoped by a join the way a property or a buy request can. Three
tables therefore earn a physical `agency_id` (052) and two deliberately do not:

    flow_events        polymorphic reference, nothing to derive from
    flow_executions    event_id nullable + SET NULL, rule_id tenant-free
    flow_suppressions  rule_id tenant-free, polymorphic entity

    flow_action_records  execution_id NOT NULL CASCADE -> derives
    flow_rules           the platform-global rule registry -> no tenant

THE RULE SQL LIVES ONCE

P26-6B added scoped scanners for the two rules NBA consumed, alongside the
global ones. Extending that to twelve would have meant two copies of every
business filter, and two copies of a rule are two rules that will disagree -
the WHERE clause here IS the automation's behaviour. `_SCANS` holds one
definition per rule and both scanners build from it, with the tenant predicate
composed on. Test 4 is what holds that together.

Coverage map:

     1-2    route inventory, and every tenant route scoped
     3-5    scan: bounded, every rule, R004/R005 regression
     6-7    load entity and simulate: foreign entities refused
     8-12   events: stamped, unforgeable, foreign entity, idempotency, listing
    13-15   executions and retry
    16-17   suppressions
    18      unbound platform admin
    19-20   cron and the server-only orchestrator
    21-34   the migrations
"""
from __future__ import annotations

import ast
import inspect
import re
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from operator_auth.context import OperatorContext
from operator_auth.exceptions import PlatformAdminAgencyRequired

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"

AGENCY_DEPENDENCY = "legacy_basic_agency_context"

A, B = 71, 82          # two agencies; deliberately not 1

COLUMNS = "052_p26_flow_agency_columns"
BACKFILL = "053_p26_flow_agency_backfill"
ENFORCE = "054_p26_flow_agency_enforce"

# The three tables 052 gives a physical column, and the two it must not.
ROOT_OWNED = ("flow_events", "flow_executions", "flow_suppressions")
DERIVED = ("flow_action_records", "flow_rules")


def ctx(agency_id=A) -> OperatorContext:
    return OperatorContext(
        user_id=None, agency_id=agency_id, role="agency_owner",
        is_platform_admin=False, session_id=None, auth_channel="legacy_basic",
    )


def unbound() -> OperatorContext:
    return OperatorContext(
        user_id=9, agency_id=None, role="platform_admin",
        is_platform_admin=True, session_id=1, auth_channel="operator_session",
    )


# ---------------------------------------------------------------------------
# An agency-aware cursor: it honours the predicate the SQL carries, so a query
# that forgot one SEES the foreign row and its test fails - rather than passing
# because a canned response happened to be empty.
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
            "id": 1, "agency_id": self.owner, "rule_id": 1, "event_id": None,
            "entity_type": "lead", "entity_id": 1, "status": "failed",
            "retry_count": 0, "retry_of_execution_id": None,
            "rule_code": "FLOW-R001", "n": 0, "count": 0,
            "contact_id": 1, "lead_id": 1, "feedback_type": "general_message",
            "property_id": 1, "linked_activity_id": None,
        }

    def fetchone(self):
        return self.current

    def fetchall(self):
        return [self.current] if self.current else []

    def sql_of(self, needle):
        return [s for s in self.statements if needle.lower() in s[0].lower()]


@contextmanager
def install(module, cursor, attribute="core_cursor"):
    original = getattr(module, attribute)

    @contextmanager
    def fake(*_a, **_k):
        yield None, cursor

    setattr(module, attribute, fake)
    try:
        yield cursor
    finally:
        setattr(module, attribute, original)


def _routes():
    """Route handlers, and those declaring the agency dependency."""
    tree = ast.parse((ROOT / "flow" / "router.py").read_text(encoding="utf-8"))
    routes, scoped = [], []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not any(
            isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
            and d.func.attr in ("get", "post", "patch", "put", "delete")
            and isinstance(d.func.value, ast.Name) and d.func.value.id == "router"
            for d in node.decorator_list
        ):
            continue
        routes.append(node.name)
        for default in list(node.args.defaults) + list(node.args.kw_defaults):
            if (
                isinstance(default, ast.Call)
                and isinstance(default.func, ast.Name)
                and default.func.id == "Depends"
                and any(isinstance(a, ast.Name) and a.id == AGENCY_DEPENDENCY
                        for a in default.args)
            ):
                scoped.append(node.name)
    return routes, scoped


# ---------------------------------------------------------------------------
# 1-2 - the HTTP surface
# ---------------------------------------------------------------------------

def test_1_flow_route_inventory():
    """Read from the router, not asserted from a brief."""
    from flow import router as flow_router

    routes, _ = _routes()
    assert len(routes) == 23, sorted(routes)
    # Seven of them administer the platform-global rule registry.
    assert len(flow_router.PLATFORM_CONFIG_ROUTES) == 7


def test_2_every_tenant_route_is_scoped_and_only_the_registry_is_not():
    """The exemption is the registry, and it is named rather than assumed.

    `flow_rules` is one catalogue for the whole platform - the same template
    for every agency - so those seven routes have no tenant to carry. Every
    other route touches tenant data and must declare the dependency.
    """
    from flow import router as flow_router

    routes, scoped = _routes()
    unscoped = sorted(set(routes) - set(scoped))
    assert unscoped == sorted(flow_router.PLATFORM_CONFIG_ROUTES), unscoped


# ---------------------------------------------------------------------------
# 3-5 - scan
# ---------------------------------------------------------------------------

def test_3_the_scan_is_bounded_and_never_falls_back_to_the_global_one():
    from flow import adapters

    cur = ScopeCursor(owner=B)
    with install(adapters, cur):
        found = adapters.scan_candidates_for_agency(A, "FLOW-R001", {}, 10)
    assert found == [], found
    statement = cur.statements[0][0]
    assert re.search(r"agency_id\s*=\s*%s", statement, re.IGNORECASE), statement
    assert A in list(cur.statements[0][1]), cur.statements[0][1]


@pytest.mark.parametrize("code", [f"FLOW-R{n:03d}" for n in range(1, 13)])
def test_4_every_rule_has_a_tenant_bounded_scan(code):
    """All twelve, from the same definition the global scan uses.

    The five OWNER rules are event-driven and have no population to sweep;
    they answer with no candidates, which is a different thing from an
    unknown rule and is asserted as such in test 5.
    """
    from flow import adapters

    parameters = {
        "days_before_expiry": 15, "overdue_hours": 0,
        "minimum_score": 80, "feedback_wait_hours": 24,
    }
    spec = adapters._SCANS[code]
    if spec is None:
        cur = ScopeCursor(owner=B)
        with install(adapters, cur):
            assert adapters.scan_candidates_for_agency(A, code, parameters, 10) == []
        assert cur.statements == [], "an event-driven rule must not query"
        return

    sql, params = adapters._scan_sql(spec, agency_id=A, parameters=parameters, limit=10)
    assert re.search(r"agency_id\s*=\s*%s", sql, re.IGNORECASE), sql
    assert sql.count("%s") == len(params), (sql, params)
    assert A in params, params
    # Filtered in SQL, never selected wholesale and narrowed afterwards.
    assert " WHERE " in sql and " LIMIT %s" in sql, sql


def test_4b_the_business_filter_is_written_once():
    """The global scan and the scoped one build from the same `_SCANS` entry.

    Asserted by reconstructing the global statement and requiring the scoped
    one to be that statement plus a tenant predicate: if someone reintroduced a
    second hand-written copy for either path, the two would stop lining up.
    """
    from flow import adapters

    parameters = {
        "days_before_expiry": 15, "overdue_hours": 0,
        "minimum_score": 80, "feedback_wait_hours": 24,
    }
    for code, spec in adapters._SCANS.items():
        if spec is None:
            continue
        plain, _ = adapters._scan_sql(spec, agency_id=None, parameters=parameters, limit=10)
        scoped, _ = adapters._scan_sql(spec, agency_id=A, parameters=parameters, limit=10)
        # The tenant predicate is the only thing the scoped form adds, and the
        # global form must not carry it.
        assert spec["tenant"] in scoped, code
        assert spec["tenant"] not in plain, code
        # Same business filter on both sides, modulo the alias the scoped
        # source introduces. Comparing the filters with aliases removed is what
        # catches a second, hand-written copy drifting away from this one.
        def strip(clause):
            without_tenant = clause.replace(spec["tenant"] + " AND ", "")
            return re.sub(r"\b[mvp]\.", "", without_tenant).strip()
        assert strip(plain.split(" WHERE ", 1)[1].split(" ORDER BY ")[0]) == \
               strip(scoped.split(" WHERE ", 1)[1].split(" ORDER BY ")[0]), code


def test_5_an_unknown_rule_is_refused_not_answered_with_nothing():
    """The global scan returns `[]` for anything it does not recognise.

    Inheriting that here would turn "I cannot scope this" into "there is
    nothing to do" - the quieter and more dangerous of the two.
    """
    from flow import adapters

    with pytest.raises(adapters.UnscopedRuleError):
        adapters.scan_candidates_for_agency(A, "FLOW-R999", {}, 10)


def test_5b_the_two_rules_p26_6b_scoped_are_still_bounded():
    """NBA's regression. R004 and R005 were scoped for Next Best Action before
    FLOW itself was migrated; the rewrite must not have loosened either."""
    from flow import adapters

    for code, params in (("FLOW-R004", {"overdue_hours": 0}),
                         ("FLOW-R005", {"minimum_score": 80})):
        sql, bound = adapters._scan_sql(
            adapters._SCANS[code], agency_id=A, parameters=params, limit=10
        )
        assert re.search(r"agency_id\s*=\s*%s", sql, re.IGNORECASE), (code, sql)
        assert A in bound, (code, bound)
    # A match has no agency column: both roots of the pair are named.
    match_sql, _ = adapters._scan_sql(
        adapters._SCANS["FLOW-R005"], agency_id=A,
        parameters={"minimum_score": 80}, limit=10,
    )
    assert "b.agency_id=%s AND p.agency_id=%s" in match_sql, match_sql


# ---------------------------------------------------------------------------
# 6-7 - load entity, simulate
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "entity_type", ["lead", "property", "buy_request", "match", "property_visit",
                    "owner_feedback"],
)
def test_6_a_foreign_entity_cannot_be_loaded(entity_type):
    """A guessed id from another agency is not found, exactly as an absent one.

    The caller learns the entity is not available to it, never that it exists
    somewhere else.
    """
    from core.exceptions import NotFoundError
    from flow import adapters

    cur = ScopeCursor(owner=B)
    with install(adapters, cur):
        with pytest.raises(NotFoundError):
            adapters.load_entity_for_agency(A, entity_type, 1)
    first = cur.statements[0][0]
    assert re.search(r"agency_id\s*=\s*%s", first, re.IGNORECASE), first


def test_6b_an_unknown_entity_type_is_refused_before_the_cursor_opens():
    from flow import adapters

    cur = ScopeCursor(owner=B)
    with install(adapters, cur):
        with pytest.raises(adapters.UnscopedRuleError):
            adapters.load_entity_for_agency(A, "invoice", 1)
    assert cur.statements == [], "a refusal must not have touched the database"


def test_7_simulate_cannot_load_a_foreign_entity(monkeypatch):
    """And it records the failed simulation against the caller's own agency."""
    from core.exceptions import NotFoundError
    from flow import service as flow_service

    monkeypatch.setattr(flow_service.repository, "get_rule_row",
                        lambda code, **kw: {"parameters": {}, "id": 1})
    monkeypatch.setattr(flow_service, "load_entity_for_agency",
                        lambda a, t, i: (_ for _ in ()).throw(NotFoundError("nope")))
    recorded = {}
    monkeypatch.setattr(flow_service.repository, "record_simulation",
                        lambda *a, agency_id=None, **k: recorded.setdefault("agency", agency_id))

    class Payload:
        def model_dump(self, exclude_unset=False):
            return {"entity_type": "lead", "entity_id": 1, "requested_by": None}

    with pytest.raises(NotFoundError):
        flow_service.simulate_for_agency(A, "FLOW-R001", Payload())
    assert recorded["agency"] == A


# ---------------------------------------------------------------------------
# 8-12 - events
# ---------------------------------------------------------------------------

def test_8_an_event_is_stamped_with_the_callers_agency():
    from flow import repository as flow_repository

    cur = ScopeCursor(owner=A)
    with install(flow_repository, cur):
        flow_repository.add_event_with_cursor(cur, {
            "event_type": "x", "entity_type": "lead", "entity_id": 1,
            "source_module": "core", "payload": {},
            "deduplication_key": "k", "occurred_at": None,
        }, agency_id=A)
    statement, params = cur.statements[0]
    assert "INSERT INTO flow_events(agency_id" in statement, statement
    assert params[0] == A, params


def test_9_no_client_supplied_agency_can_reach_flow():
    """Neither a route parameter nor a request model carries one."""
    from pydantic import BaseModel

    from flow import schemas as flow_schemas

    tree = ast.parse((ROOT / "flow" / "router.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        names = [a.arg for a in node.args.args + node.args.kwonlyargs]
        assert "agency_id" not in names, (node.name, names)

    found = False
    for attribute, obj in vars(flow_schemas).items():
        if isinstance(obj, type) and issubclass(obj, BaseModel) and obj is not BaseModel:
            found = True
            assert "agency_id" not in getattr(obj, "model_fields", {}), attribute
    assert found, "no FLOW request models found"


def test_10_the_event_write_requires_a_tenant_it_cannot_default():
    """Keyword-only and with no default, on every FLOW write.

    Forgetting one is a TypeError here rather than a NULL that 054's NOT NULL
    rejects a layer down with a far worse message.
    """
    from flow import repository as flow_repository

    for name in ("add_event", "add_event_with_cursor", "execute_live",
                 "record_simulation", "record_failure", "add_suppression"):
        parameter = inspect.signature(getattr(flow_repository, name)).parameters["agency_id"]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, name
        assert parameter.default is inspect.Parameter.empty, name


def test_11_the_dedup_key_stays_global_and_leaks_nothing():
    """Its components already name a specific entity id, and entity ids come
    from tenant-owned tables on a single sequence - two agencies cannot collide
    on one. Widening it with the agency would weaken the dedup, not the leak,
    by letting the same occurrence through twice.

    What matters for isolation is that a read of events is scoped, which
    test 12 asserts."""
    from flow import repository as flow_repository

    source = inspect.getsource(flow_repository.add_event_with_cursor)
    assert "ON CONFLICT(deduplication_key)" in source, source
    assert "agency_id" in source, source


def test_12_listing_events_is_bounded_to_one_agency():
    from flow import repository as flow_repository

    cur = ScopeCursor(owner=B)
    with install(flow_repository, cur):
        assert flow_repository.list_events_for_agency(A) == []
        with pytest.raises(Exception):
            flow_repository.get_event_for_agency(1, A)
    for statement, params in cur.statements:
        assert re.search(r"agency_id\s*=\s*%s", statement, re.IGNORECASE), statement
        assert A in list(params), params


# ---------------------------------------------------------------------------
# 13-15 - executions and retry
# ---------------------------------------------------------------------------

def test_13_reading_an_execution_is_bounded():
    from core.exceptions import NotFoundError
    from flow import repository as flow_repository

    cur = ScopeCursor(owner=B)
    with install(flow_repository, cur):
        with pytest.raises(NotFoundError):
            flow_repository.get_execution_for_agency(1, A)
        assert flow_repository.list_executions_for_agency(A) == []
    for statement, _ in cur.statements:
        assert re.search(r"agency_id\s*=\s*%s", statement, re.IGNORECASE), statement


def test_14_retrying_a_foreign_execution_is_refused_before_anything_is_written():
    """The read comes first, so a foreign id cannot even bump another tenant's
    retry_count - let alone re-run its automation."""
    from core.exceptions import NotFoundError
    from flow import repository as flow_repository

    cur = ScopeCursor(owner=B)
    with install(flow_repository, cur):
        with pytest.raises(NotFoundError):
            flow_repository.increment_retry_for_agency(1, A)
    assert not cur.sql_of("UPDATE flow_executions"), cur.statements


def test_15_the_recovery_probe_is_scoped_too():
    """Without its predicate one agency's successful retry could make another
    agency's execution look already recovered and block a legitimate retry - a
    cross-tenant effect that returns no foreign row at all."""
    from flow import repository as flow_repository

    source = inspect.getsource(flow_repository.increment_retry_for_agency)

    # Each of the three statements is checked on its own. Asserting
    # "agency_id=%s appears somewhere in this function" was the first version
    # of this test, and it survived a mutation that removed the predicate from
    # the UPDATE - the SELECTs above it kept the string true.
    def statement_containing(needle):
        index = source.index(needle)
        start = source.rindex('"', 0, index)
        end = source.index('",', index)
        # widen to the whole quoted SQL, which may be split across adjacent
        # string literals
        return source[source.rindex("cur.execute(", 0, index):end]

    probe = statement_containing("retry_of_execution_id=%s")
    assert "agency_id=%s" in probe, probe

    update = statement_containing("UPDATE flow_executions SET retry_count")
    assert "agency_id=%s" in update, update
    assert "WHERE id=%s AND agency_id=%s" in update, update


# ---------------------------------------------------------------------------
# 16-17 - suppressions
# ---------------------------------------------------------------------------

def test_16_suppressions_are_read_written_and_deleted_in_one_agency():
    from core.exceptions import NotFoundError
    from flow import repository as flow_repository

    cur = ScopeCursor(owner=B)
    with install(flow_repository, cur):
        assert flow_repository.list_suppressions_for_agency(A) == []
        with pytest.raises(NotFoundError):
            flow_repository.delete_suppression_for_agency(1, A)
    for statement, params in cur.statements:
        assert re.search(r"agency_id\s*=\s*%s", statement, re.IGNORECASE), statement
        assert A in list(params), params


def test_17_agency_b_cannot_silence_a_rule_for_agency_a():
    """A suppression is a decision this agency made, not a fact about the
    entity. The unscoped probe silenced the rule for everyone while returning
    no foreign row - the quiet shape P26-6B named."""
    from flow import repository as flow_repository

    cur = ScopeCursor(owner=B)
    suppressed = flow_repository._is_suppressed_with_cursor_for_agency(
        cur, 1, "lead", 1, A
    )
    assert suppressed is False
    statement, params = cur.statements[0]
    assert "agency_id=%s" in statement, statement
    assert params[-1] == A, params


# ---------------------------------------------------------------------------
# 18 - unbound platform admin
# ---------------------------------------------------------------------------

def test_18_an_unbound_platform_admin_is_refused_before_any_tenant_query():
    """`require_agency()` runs in the route, before the service is called."""
    with pytest.raises(PlatformAdminAgencyRequired):
        unbound().require_agency()

    source = (ROOT / "flow" / "router.py").read_text(encoding="utf-8")
    assert "PlatformAdminAgencyRequired" in source
    assert "HTTPException(403" in source.replace(" ", "")


# ---------------------------------------------------------------------------
# 19-20 - cron and the orchestrator
# ---------------------------------------------------------------------------

def test_19_the_cron_no_longer_drives_a_global_scan():
    """`/api/flow/scan` is per-agency now, so the HTTP runner is one tenant's
    bounded cycle. The platform-wide sweep is the in-process orchestrator, and
    it is deliberately not an HTTP route."""
    source = (ROOT / "run_flow_p2b_cron.py").read_text(encoding="utf-8")
    assert "main_all_agencies" in source, source
    assert "scan_for_all_agencies" in source, source
    # The sweep must not be reachable over HTTP.
    routes, _ = _routes()
    assert not any("all_agencies" in name for name in routes), routes


def test_20_the_orchestrator_runs_one_bounded_cycle_per_tenant(monkeypatch):
    """The loop is what supplies the predicate, so it has to be a loop.

    A single global pass that filtered afterwards would still return plausible
    totals, so this asserts which agencies were asked for - not only the sum.
    """
    from flow import service as flow_service

    asked = []
    monkeypatch.setattr(flow_service.repository, "list_active_agency_ids", lambda: [A, B])
    monkeypatch.setattr(
        flow_service, "scan",
        lambda payload, *, agency_id=None: asked.append(agency_id) or {
            "processed": 1, "successes": 1, "failures": 0, "skips": 0,
        },
    )

    class Payload:
        def model_dump(self, exclude_unset=False):
            return {"limit": 10, "simulation": False}

    result = flow_service.scan_for_all_agencies(Payload())
    assert asked == [A, B], asked
    assert result["agencies"] == 2 and result["processed"] == 2, result
    assert len(result["runs"]) == 2


# ---------------------------------------------------------------------------
# 21-34 - the migrations
# ---------------------------------------------------------------------------

def _sql(version):
    return re.sub(r"--[^\n]*", "", (MIGRATIONS / f"{version}.sql").read_text(encoding="utf-8"))


def _down(version):
    return re.sub(r"--[^\n]*", "", (MIGRATIONS / f"{version}_down.sql").read_text(encoding="utf-8"))


def _strip_strings(sql):
    return re.sub(r"'(?:[^']|'')*'", " ", sql)


def test_21_only_the_three_root_owned_tables_get_a_column():
    body = _sql(COLUMNS)
    for table in ROOT_OWNED:
        assert re.search(rf"ALTER TABLE {table}\s+ADD COLUMN IF NOT EXISTS agency_id", body), table
    for table in DERIVED:
        assert not re.search(rf"ALTER TABLE {table}\s+ADD COLUMN IF NOT EXISTS agency_id", body), table
    # And the migration asserts the absence itself, rather than leaving it implicit.
    assert "must not carry a physical agency_id" in _sql(COLUMNS)


def test_22_the_column_is_nullable_with_no_default_in_052():
    raw = _sql(COLUMNS)
    body = _strip_strings(raw)
    assert "SET NOT NULL" not in body, "enforcement belongs to 054"
    assert not re.search(r"agency_id\s+BIGINT[^;]*DEFAULT", body, re.IGNORECASE), body
    # The FK is built through EXECUTE format(), so its clause lives inside a
    # string literal - asserted on the raw text rather than the stripped one.
    assert "ON DELETE RESTRICT" in raw
    assert "REFERENCES agencies (id)" in raw
    assert "must stay nullable; enforcement belongs to 054" in raw


def test_23_the_backfill_is_deterministic():
    raw = _sql(BACKFILL)
    body = _strip_strings(raw).upper()
    # None of these may pick an owner. `COALESCE` appears once, in a guard's
    # error-message aggregation, and never across agency candidates - which is
    # why the check is on the assignment text rather than the whole file.
    for banned in (" MIN(", " MAX(", "LIMIT 1"):
        assert banned not in body, banned
    assignments = re.findall(r"SET AGENCY_ID = [^;]+", body)
    assert assignments, "no assignment found"
    for assignment in assignments:
        for banned in ("COALESCE", "MIN(", "MAX(", "LIMIT 1"):
            assert banned not in assignment, (banned, assignment[:120])
    # Every entity type resolves by its own module's certified route. Checked
    # on the raw text: the resolution names its entity types as SQL literals,
    # which _strip_strings removes.
    for table in ("leads", "properties", "buy_requests", "matches",
                  "property_visits", "owner_feedback"):
        assert table in raw, table


def test_24_no_source_is_a_hard_fail():
    body = _sql(BACKFILL)
    assert body.count("RAISE EXCEPTION") >= 5, body.count("RAISE EXCEPTION")
    assert "no derivable agency" in body
    assert "Refusing rather than guessing an owner" in body


def test_25_ambiguity_is_a_hard_fail():
    """All three passes guard it, not just one.

    Counting the occurrences is what makes this bite: an earlier version
    asserted the clause appeared *somewhere*, and survived a mutation that
    disabled it in one pass while the other two kept the string true.
    """
    body = _sql(BACKFILL)
    assert body.count("COUNT(DISTINCT a.agency_id) <> 1") == 3, body.count(
        "COUNT(DISTINCT a.agency_id) <> 1"
    )
    assert body.count("Refusing rather than choosing") == 3
    # Executions resolve from two sources - their entity and their parent event
    # - so their guard must consider both, or a disagreement would go unseen.
    # Read from the raw file: `_sql` strips the comments that mark the passes.
    raw = (MIGRATIONS / f"{BACKFILL}.sql").read_text(encoding="utf-8")
    executions_guard = raw[raw.index("PASS 2"):raw.index("PASS 3")]
    assert "p26_6c_flow_entity_agency(x.entity_type, x.entity_id)" in executions_guard
    assert "FROM flow_events e" in executions_guard


def test_26_the_backfill_names_no_default_agency():
    body = _strip_strings(_sql(BACKFILL))
    assert "stima360" not in body.lower()
    assert "Default Agency" not in _sql(BACKFILL)
    # and no hardcoded id assignment
    assert not re.search(r"SET agency_id\s*=\s*\d+", body), body


def test_27_054_sets_not_null_on_all_three():
    body = _sql(ENFORCE)
    for table in ROOT_OWNED:
        assert re.search(rf"ALTER TABLE {table}\s+ALTER COLUMN agency_id SET NOT NULL", body), table


@pytest.mark.parametrize("table,trigger", [
    ("flow_events", "trg_flow_event_agency_integrity"),
    ("flow_executions", "trg_flow_execution_agency_integrity"),
    ("flow_suppressions", "trg_flow_suppression_agency_integrity"),
])
def test_28_to_30_each_table_has_its_own_trigger_on_insert_and_update(table, trigger):
    body = _sql(ENFORCE)
    assert re.search(
        rf"CREATE TRIGGER {trigger}\s+BEFORE INSERT OR UPDATE ON {table}\s+FOR EACH ROW",
        body,
    ), trigger


def test_31_a_cross_agency_execution_insert_is_refused():
    """The execution must agree with the event it came from."""
    body = _sql(ENFORCE)
    assert "does not match event" in body
    assert "SELECT agency_id INTO v_event_agency FROM flow_events WHERE id = NEW.event_id" in body


def test_32_a_cross_agency_retry_is_refused():
    body = _sql(ENFORCE)
    assert "does not match retried execution" in body
    assert "NEW.retry_of_execution_id" in body


def test_33_the_downs_remove_only_this_slices_objects():
    columns_down, enforce_down = _down(COLUMNS), _down(ENFORCE)
    for table in ROOT_OWNED:
        assert f"DROP COLUMN IF EXISTS agency_id" in columns_down
        assert table in columns_down
    # Nothing from 008 - the FLOW schema itself - may be dropped.
    for banned in ("DROP TABLE", "flow_action_records", "flow_rules"):
        assert banned not in columns_down, banned
        assert banned not in enforce_down, banned
    # The backfill refuses rather than blanking ownership.
    assert "irreversible" in _down(BACKFILL)
    # Each down removes its own ledger row and nothing else.
    assert columns_down.count("DELETE FROM schema_migrations") == 1
    assert enforce_down.count("DELETE FROM schema_migrations") == 1


def test_34_no_generic_rowtype_trigger_function():
    """Migration 040's lesson, applied.

    PL/pgSQL resolves a record field when it *prepares* the expression, not
    only when a preceding conjunct is true - so a shared
    `IF TG_TABLE_NAME = 'x' AND NEW.<field>` function fails on every table
    lacking that field. `flow_events` has no `event_id` and
    `flow_suppressions` has neither `event_id` nor `retry_of_execution_id`, so
    one shared function would have failed on two of the three tables.
    """
    body = _sql(ENFORCE)
    assert "TG_TABLE_NAME" not in body, "a shared multi-table trigger function reappeared"
    functions = re.findall(r"CREATE OR REPLACE FUNCTION (\w+)\(\)", body)
    assert sorted(functions) == [
        "flow_event_agency_integrity",
        "flow_execution_agency_integrity",
        "flow_suppression_agency_integrity",
    ], functions
    # Each function names only fields its own table has.
    execution_fn = body[body.index("flow_execution_agency_integrity()"):]
    assert "NEW.event_id" in execution_fn
    event_fn = body[body.index("flow_event_agency_integrity()"):body.index("flow_execution_agency_integrity()")]
    assert "NEW.event_id" not in event_fn, event_fn


def test_35_backfill_updates_do_not_reference_the_update_target_from_lateral():
    """PostgreSQL does not expose the UPDATE target alias inside FROM LATERAL.

    P26-6C live TEST caught this in migration 053. Correlated SET subqueries
    are valid; target-referencing FROM LATERAL updates are not.
    """
    body = _sql(BACKFILL)

    for table, alias in (
        ("flow_events", "e"),
        ("flow_executions", "x"),
        ("flow_suppressions", "s"),
    ):
        start = body.index(f"UPDATE {table} {alias}")
        end = body.index(";", start) + 1
        statement = body[start:end]

        assert "FROM LATERAL" not in statement, statement
        assert (
            f"p26_6c_flow_entity_agency({alias}.entity_type, {alias}.entity_id)"
            in statement
        ), statement
