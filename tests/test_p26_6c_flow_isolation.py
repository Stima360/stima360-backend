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
import os
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


# ===========================================================================
# 35-37 - THE NAME-SHADOWING REGRESSION
#
# 053 shipped a defect that passed every test in this file and every one of the
# hundred-odd FLOW tests, and only surfaced against real data on TEST. It is
# worth stating exactly, because the shape recurs.
#
# `p26_6c_flow_entity_agency` returns SETOF *bigint* - a base type, not a
# composite - so the FROM item it produces exposes exactly one column, and
# PostgreSQL names that column after the FUNCTION. The function body's own
# `agency_id` is not visible to the caller.
#
# So `SELECT agency_id FROM p26_6c_flow_entity_agency(...)` does not read the
# function's output. The unqualified name finds nothing in the subquery's range
# table, resolution walks outward, and it binds to the OUTER relation's
# `agency_id` - the column being written. That is a legal correlated reference,
# so it parses, plans and runs silently, and the UPDATE assigns the column to
# itself: NULL.
#
# It was invisible because 052 creates that outer column immediately before.
# Without it, the statement would have failed at parse time.
#
# These three tests encode the rule structurally rather than by matching one
# bad string, so a differently-spelled reintroduction still fails.
# ===========================================================================

FLOW_HELPER = "p26_6c_flow_entity_agency"


def _call_sites(sql_text):
    """Every call of the helper in a FROM clause, with what surrounds it.

    Returns (target_list, alias_clause) per site: the target list of the SELECT
    the call belongs to, and whatever follows the closing parenthesis - which
    is where an explicit `AS name(agency_id)` would appear.
    """
    flat = " ".join(re.sub(r"--[^\n]*", "", sql_text).split())
    sites = []
    for match in re.finditer(rf"FROM\s+{FLOW_HELPER}\s*\(", flat, re.IGNORECASE):
        start = match.end() - 1
        depth = 0
        end = None
        for index in range(start, len(flat)):
            if flat[index] == "(":
                depth += 1
            elif flat[index] == ")":
                depth -= 1
                if depth == 0:
                    end = index
                    break
        assert end is not None, "unbalanced parentheses at a helper call"
        select = flat.rfind("SELECT", 0, match.start())
        target_list = flat[select + len("SELECT"):match.start()].strip()
        sites.append((target_list, flat[end + 1:end + 48]))
    return sites


def test_35_every_helper_call_that_projects_a_column_names_it_explicitly():
    """The rule, applied to all of 053 rather than to one known-bad line.

    Two forms are admissible and nothing else:

      * the call projects no column at all - `SELECT 1 FROM helper(...)`, which
        the three "unresolved" guards use. It cannot capture an outer name
        because it never writes one.

      * the call carries `AS <alias>(agency_id)` and every reference to the
        column is qualified with that alias.
    """
    sites = _call_sites((MIGRATIONS / f"{BACKFILL}.sql").read_text(encoding="utf-8"))
    assert len(sites) >= 7, f"expected every pass to call the helper, found {len(sites)}"

    offenders = []
    for target_list, after in sites:
        projects_column = re.search(r"\bagency_id\b", target_list) is not None
        alias = re.match(r"\s*AS\s+(\w+)\s*\(\s*agency_id\s*\)", after, re.IGNORECASE)
        if not projects_column:
            continue
        if alias is None:
            offenders.append(("no alias", target_list))
            continue
        # Projected, aliased - now the reference itself must be qualified.
        if not re.search(rf"\b{alias.group(1)}\.agency_id\b", target_list):
            offenders.append(("unqualified reference", target_list))
    assert not offenders, offenders


def test_36_the_unqualified_projection_pattern_is_absent():
    """The literal shape that shipped, spelled out.

    Test 35 is the structural rule; this one names the exact statement that
    reached TEST, so the diff that reintroduces it fails against something a
    reader recognises immediately.
    """
    flat = " ".join(
        re.sub(r"--[^\n]*", "", (MIGRATIONS / f"{BACKFILL}.sql").read_text(encoding="utf-8")).split()
    )
    assert not re.search(
        rf"SELECT\s+agency_id\s+FROM\s+{FLOW_HELPER}", flat, re.IGNORECASE
    ), "an unqualified projection of the helper's output has come back"
    # And the same shape one level down, inside a UNION branch or a derived table.
    assert not re.search(
        rf"SELECT\s+DISTINCT\s+agency_id\s+FROM\s+\(", flat, re.IGNORECASE
    ), "a derived table projects an unqualified agency_id"


def test_37_the_backfill_would_not_be_a_no_op():
    """Each UPDATE must assign from the helper, not from the row it is writing.

    The defect made all three UPDATEs `SET agency_id = <its own value>`. The
    tell is that the assigned expression has to mention an alias that is not
    the table being updated.
    """
    flat = " ".join(
        re.sub(r"--[^\n]*", "", (MIGRATIONS / f"{BACKFILL}.sql").read_text(encoding="utf-8")).split()
    )
    updates = re.findall(
        r"UPDATE\s+(flow_\w+)\s+(\w+)\s+SET agency_id = \((.*?)\)\s+WHERE",
        flat, re.IGNORECASE,
    )
    assert len(updates) == 3, [u[0] for u in updates]
    for table, table_alias, expression in updates:
        assert FLOW_HELPER in expression, (table, expression[:120])
        # The value must come from the helper's own alias, never from the row.
        assert not re.search(rf"SELECT\s+{table_alias}\.agency_id\b", expression), (
            table, expression[:120],
        )
        assert re.search(r"SELECT\s+(DISTINCT\s+)?\w+\.agency_id\b", expression), (
            table, expression[:120],
        )


# ===========================================================================
# 38-40 - THE CRON RECOVERY CONTRACT
#
# The second defect this slice shipped, and it reached production: the
# scheduled job `stima360-flow-automation` failed every run with
#
#     phase=recovery status=failed duration_ms=384 reason=invalid_json
#
# `run_flow_p2b_cron.py::_post` validates a recovery response before reading
# it: a string `status`, and non-negative integers for `requested_limit`,
# `processed`, `ignored`, `failed` and `busy`. Anything else is rejected as
# `invalid_json` - which is what a *contractually* invalid but syntactically
# fine JSON body is.
#
# P26-6C's first `recover_received_events_for_agency` returned
# `{"processed": ..., "items": [...]}`. Valid JSON, wrong shape, and its
# `processed` counted attempts rather than successes.
#
# The cron is NOT relaxed to accept the poorer body. These tests pin the
# contract from the consumer's side, so the service has to satisfy it.
# ===========================================================================

CRON_RECOVERY_INTEGER_KEYS = ("requested_limit", "processed", "ignored", "failed", "busy")


def _cron_required_keys():
    """The keys the cron actually validates, read from the cron itself.

    Derived rather than restated: if `_post` starts requiring another counter,
    this test starts requiring it too, instead of quietly falling behind.
    """
    source = (ROOT / "run_flow_p2b_cron.py").read_text(encoding="utf-8")
    block = source[source.index("if phase=='recovery'") - 400:source.index("if phase=='recovery'")]
    required = re.search(r"\(([^)]*)\)\s*$", block.strip())
    assert required, block
    return tuple(re.findall(r"'(\w+)'", required.group(1)))


def test_38_the_cron_still_validates_the_full_recovery_contract():
    """The consumer is unchanged: this slice fixes the producer, not the check."""
    assert _cron_required_keys() == CRON_RECOVERY_INTEGER_KEYS, _cron_required_keys()
    source = (ROOT / "run_flow_p2b_cron.py").read_text(encoding="utf-8")
    assert "raise TechnicalError('invalid_json')" in source
    assert "isinstance(data.get('status'),str)" in source


@pytest.mark.parametrize("scoped", [False, True])
def test_39_both_recovery_entry_points_answer_the_cron_contract(monkeypatch, scoped):
    """Same shape from the ctx-less path and the per-agency one.

    Driven through the real service with the event stream faked, so the counts
    are the ones the classification actually produces - not a canned dict.
    """
    from flow import service as flow_service

    outcomes = {
        1: {"claim_status": "claimed", "event": {"status": "processed"}},
        2: {"claim_status": "ineligible", "event": {"status": "received"}},
        3: {"claim_status": "busy", "event": {"status": "received"}},
        4: {"claim_status": "claimed", "event": {"status": "failed"}},
    }
    monkeypatch.setattr(flow_service.repository, "list_received_owner_event_ids",
                        lambda limit: list(outcomes))
    monkeypatch.setattr(flow_service.repository, "list_received_owner_event_ids_for_agency",
                        lambda agency_id, limit: list(outcomes))
    monkeypatch.setattr(flow_service, "process_saved_event",
                        lambda event_id, received_only=False: outcomes[event_id])

    if scoped:
        result = flow_service.recover_received_events_for_agency(A, 10)
    else:
        result = flow_service.recover_received_events(10)

    assert isinstance(result.get("status"), str), result
    for key in CRON_RECOVERY_INTEGER_KEYS:
        assert type(result.get(key)) is int and result[key] >= 0, (key, result)
    # The counts are the classification's, not the loop's length.
    assert result["requested_limit"] == 10
    assert result["processed"] == 1 and result["ignored"] == 1
    assert result["busy"] == 1 and result["failed"] == 1
    assert result["status"] == "partial_failure", result["status"]
    assert len(result["items"]) == 4


def test_40_the_agency_filter_is_in_the_event_query_not_in_the_counting(monkeypatch):
    """Restoring the contract must not have restored the global sweep.

    The tenant bound is the id query: nothing outside this agency is claimed,
    so nothing outside it can be processed, counted or reported.
    """
    from flow import service as flow_service

    asked = {}
    monkeypatch.setattr(
        flow_service.repository, "list_received_owner_event_ids_for_agency",
        lambda agency_id, limit: asked.setdefault("agency", agency_id) and [] or [],
    )
    monkeypatch.setattr(
        flow_service.repository, "list_received_owner_event_ids",
        lambda limit: pytest.fail("the scoped path used the global event query"),
    )
    result = flow_service.recover_received_events_for_agency(A, 5)
    assert asked["agency"] == A
    assert result["status"] == "completed" and result["processed"] == 0
    assert result["requested_limit"] == 5


def test_40b_the_two_recovery_paths_share_one_definition():
    """The shape is defined once, so it cannot drift again.

    The first version of the scoped function was a second, independent
    implementation - which is exactly how it came to return a different
    dictionary from the one the cron reads.
    """
    from flow import service as flow_service

    for name in ("recover_received_events", "recover_received_events_for_agency"):
        source = inspect.getsource(getattr(flow_service, name))
        assert "_recover_events(" in source, name
        assert "'status'" not in source and '"status"' not in source, (
            f"{name} builds its own response shape instead of using _recover_events"
        )


# ===========================================================================
# 41-46 - MIGRATION 055: THE TENANT OF A FLOW ROW IS IMMUTABLE
#
# 054 validated the CHILD side of every link at write time and nothing
# validated the PARENT side. A BEFORE trigger on flow_executions fires when a
# flow_executions row is written, not when the event it points at is rewritten,
# so
#
#     UPDATE flow_events SET agency_id = <other agency> WHERE id = <parent>;
#
# was accepted and left the executions attached to it in a different tenant. A
# live hostile probe on TEST found it: the child-side check passed with
# SQLSTATE P0001, the parent-side change went through.
#
# These are structural assertions on the migration text. They are NOT the
# proof - the proof is tests/test_p26_6c_flow_immutability_pg.py, which runs
# the statements against a real PostgreSQL. What these do is stop the guard
# being removed or weakened by an edit, in a run where no database is
# reachable.
# ===========================================================================

IMMUTABILITY = "055_p26_flow_agency_immutability"


def test_41_055_is_the_next_migration_and_does_not_touch_the_applied_ones():
    """Forward-only: 052/053/054 are applied, so 055 replaces, never edits."""
    versions = sorted(
        p.stem for p in MIGRATIONS.glob("0*.sql") if not p.stem.endswith("_down")
    )
    assert versions[-1] == IMMUTABILITY, versions[-3:]
    body = _sql(IMMUTABILITY)
    # It may replace function bodies; it may not alter the columns, the NOT
    # NULL, or the triggers 054 installed.
    for banned in ("ADD COLUMN", "DROP COLUMN", "SET NOT NULL", "DROP NOT NULL",
                   "ALTER TABLE flow_events", "ALTER TABLE flow_executions",
                   "ALTER TABLE flow_suppressions"):
        assert banned not in body, banned
    assert "DELETE FROM schema_migrations" not in body, "an UP must not edit the ledger"


@pytest.mark.parametrize("function", [
    "flow_event_agency_integrity",
    "flow_execution_agency_integrity",
    "flow_suppression_agency_integrity",
])
def test_42_each_root_guard_refuses_a_tenant_change_on_update(function):
    body = _sql(IMMUTABILITY)
    start = body.index(f"FUNCTION {function}()")
    end = body.index("$fn$;", start)
    definition = body[start:end]
    assert "TG_OP = 'UPDATE'" in definition, function
    assert "NEW.agency_id IS DISTINCT FROM OLD.agency_id" in definition, function
    assert "is immutable" in definition, function


def test_43_the_comparison_is_is_distinct_from_not_plain_inequality():
    """`<>` is NULL-propagating and, worse here, an UPDATE that rewrites the
    same agency would not be a change at all - the runtime's own
    `UPDATE flow_executions SET status='executed' ...` rewrites the row through
    this trigger on every live execution."""
    body = _sql(IMMUTABILITY)
    # Counted inside the three function bodies only: the migration's own
    # verification block mentions the same text when it reads pg_proc back,
    # and that occurrence is not a guard.
    guarded = 0
    for function in ("flow_event_agency_integrity", "flow_execution_agency_integrity",
                     "flow_suppression_agency_integrity"):
        start = body.index(f"FUNCTION {function}()")
        guarded += body[start:body.index("$fn$;", start)].count(
            "IS DISTINCT FROM OLD.agency_id"
        )
    assert guarded == 3, guarded
    assert "NEW.agency_id <> OLD.agency_id" not in body


def test_44_054s_checks_are_carried_forward_verbatim():
    """055 adds; it must not quietly drop what 054 established.

    Compared against 054's own text rather than a list retyped here, so a
    future edit to either file has to keep them in step.
    """
    enforce, immutable = _sql(ENFORCE), _sql(IMMUTABILITY)
    for fragment in (
        "flow_events.agency_id cannot be NULL",
        "flow_executions.agency_id cannot be NULL",
        "flow_suppressions.agency_id cannot be NULL",
        "does not match event",
        "does not match retried execution",
        "does not resolve to an event with an agency",
        "does not resolve to an execution with an agency",
    ):
        assert fragment in enforce, ("054 no longer says this", fragment)
        assert fragment in immutable, ("055 dropped one of 054's checks", fragment)


def test_45_the_derived_table_gets_agreement_not_immutability():
    """`flow_action_records.execution_id` must stay mutable.

    `execute_live`'s recovery path re-points an existing action record at the
    new execution. Freezing the column would break recovery, so the guard is
    that a re-point stays inside one agency.
    """
    body = _sql(IMMUTABILITY)
    assert "FUNCTION flow_action_record_agency_integrity()" in body
    assert "NEW.execution_id IS DISTINCT FROM OLD.execution_id" in body
    assert "would move record" in body
    # Only UPDATE: an INSERT names its execution for the first time.
    assert re.search(
        r"CREATE TRIGGER trg_flow_action_record_agency_integrity\s+BEFORE UPDATE ON flow_action_records",
        body,
    ), body
    assert "BEFORE INSERT OR UPDATE ON flow_action_records" not in body
    # And the runtime's re-point is still expressed.
    runtime = (ROOT / "flow" / "repository.py").read_text(encoding="utf-8")
    assert "UPDATE flow_action_records SET execution_id=%s" in runtime, (
        "the recovery re-point this guard is shaped around has gone"
    )


def test_46_the_down_restores_054_and_removes_only_055s_own_objects():
    down = _down(IMMUTABILITY)
    # Puts the three 054 bodies back...
    for function in ("flow_event_agency_integrity", "flow_execution_agency_integrity",
                     "flow_suppression_agency_integrity"):
        assert f"CREATE OR REPLACE FUNCTION {function}()" in down, function
    assert "IS DISTINCT FROM OLD.agency_id" not in down, "the down still enforces 055"
    # ...drops only what 055 added...
    assert "DROP FUNCTION IF EXISTS flow_action_record_agency_integrity()" in down
    assert "trg_flow_action_record_agency_integrity" in down
    # ...and touches nothing from 052/053/054.
    for banned in ("DROP COLUMN", "DROP TRIGGER IF EXISTS trg_flow_event_agency_integrity",
                   "DROP TRIGGER IF EXISTS trg_flow_execution_agency_integrity",
                   "DROP TRIGGER IF EXISTS trg_flow_suppression_agency_integrity",
                   "DROP NOT NULL"):
        assert banned not in down, banned
    assert down.count("DELETE FROM schema_migrations") == 1


def test_47_the_real_postgresql_proof_exists_and_is_honest_about_skipping():
    """The structural tests above are not the proof, and must not be read as it.

    This asserts the live suite exists, refuses to run outside a test database,
    uses negative ids, rolls back, and - critically - skips with a reason that
    says a skip is not a pass.
    """
    live = (ROOT / "tests" / "test_p26_6c_flow_immutability_pg.py").read_text(encoding="utf-8")
    assert "P26_PG_DSN" in live
    assert "never as PASS" in live, "a skip must not read as a pass"
    assert "SAVEPOINT" in live and "ROLLBACK TO SAVEPOINT" in live
    # The database is named exactly, not matched as a substring - test 55
    # carries the full argument for why.
    assert "current_database()" in live
    assert 'REQUIRED_DATABASE = "stima360_db_test"' in live
    assert "conn.rollback()" in live
    # Asserted on executable content: the module docstring says the words
    # "nextval" and "setval" while explaining that it uses neither.
    executable = live.split('"""', 2)[-1]
    assert "nextval" not in executable and "setval" not in executable
    # Negative fixture ids only.
    for name in ("AGENCY_A", "EVENT", "EXEC_PARENT", "SUPPRESSION", "ACTION"):
        assert re.search(rf"^{name} = -\d+", live, re.MULTILINE), name
    # And it attributes every refusal to the guard under test.
    assert "pgcode == \"P0001\"" in live


# ===========================================================================
# 48-50 - THE VERIFIER ITSELF
#
# Review found an inverted bit test in 055's precondition block and in the live
# suite's trigger assertion: `must fire BEFORE` was written as `tgtype & 2 = 0`
# and `assert not tgtype & 2`.
#
# BEFORE is a bit that is SET (pg_trigger.h: ROW=1, BEFORE=2, INSERT=4,
# DELETE=8, UPDATE=16, TRUNCATE=32, INSTEAD=64). There is no AFTER bit - AFTER
# is the absence of both BEFORE and INSTEAD. So the inverted test would have
# rejected the correct trigger and accepted an AFTER one, and an AFTER trigger
# cannot refuse a write at all: it would have certified a guard that does
# nothing.
#
# Nine mutations of 055 did not catch it, because every one of them mutated the
# thing being verified rather than the verifier. A wrong decoder makes all of
# its own assertions agree with it. These tests check the decoder against the
# values PostgreSQL actually produces, in both polarities.
# ===========================================================================

# The tgtype values for the shapes this slice installs, and for the ones it
# must reject. Computed from the documented flags rather than hardcoded, so the
# constants and the decoder cannot drift together.
TGTYPE_BEFORE_INSERT_UPDATE_ROW = 1 | 2 | 4 | 16          # 23 - the ROOT guards
TGTYPE_BEFORE_UPDATE_ROW = 1 | 2 | 16                     # 19 - the action-record guard
TGTYPE_AFTER_INSERT_UPDATE_ROW = 1 | 4 | 16               # 21 - must be rejected
TGTYPE_BEFORE_INSERT_UPDATE_STATEMENT = 2 | 4 | 16        # 22 - not FOR EACH ROW
TGTYPE_INSTEAD_OF_UPDATE_ROW = 1 | 64 | 16                # 81 - INSTEAD OF
# PostgreSQL's timing mask is BEFORE|INSTEAD and the two are mutually
# exclusive, so this value cannot occur in pg_trigger. It is here because the
# decoder should be conservative rather than rely on that: without the INSTEAD
# exclusion it would call this "BEFORE", and a mutation that removed the
# exclusion would otherwise go unnoticed.
TGTYPE_IMPOSSIBLE_BEFORE_AND_INSTEAD = 1 | 2 | 64 | 16    # 83


@pytest.mark.parametrize(
    "tgtype,row_level,before,insert,update",
    [
        (TGTYPE_BEFORE_INSERT_UPDATE_ROW, True, True, True, True),
        (TGTYPE_BEFORE_UPDATE_ROW, True, True, False, True),
        (TGTYPE_AFTER_INSERT_UPDATE_ROW, True, False, True, True),
        (TGTYPE_BEFORE_INSERT_UPDATE_STATEMENT, False, True, True, True),
        (TGTYPE_INSTEAD_OF_UPDATE_ROW, True, False, False, True),
        (TGTYPE_IMPOSSIBLE_BEFORE_AND_INSTEAD, True, False, False, True),
    ],
)
def test_48_the_tgtype_decoder_reads_the_real_flag_values(
    tgtype, row_level, before, insert, update
):
    """Imported from the live suite, so the decoder under test is the one it uses."""
    from tests import test_p26_6c_flow_immutability_pg as live

    assert live._is_row_level(tgtype) is row_level, tgtype
    assert live._is_before(tgtype) is before, tgtype
    assert live._covers_insert(tgtype) is insert, tgtype
    assert live._covers_update(tgtype) is update, tgtype


def test_49_the_decoder_accepts_before_and_rejects_after():
    """The regression for the inverted test, asserted in both directions."""
    from tests import test_p26_6c_flow_immutability_pg as live

    assert live._is_before(TGTYPE_BEFORE_INSERT_UPDATE_ROW) is True
    assert live._is_before(TGTYPE_BEFORE_UPDATE_ROW) is True
    assert live._is_before(TGTYPE_AFTER_INSERT_UPDATE_ROW) is False
    assert live._is_before(TGTYPE_INSTEAD_OF_UPDATE_ROW) is False
    assert live._is_before(TGTYPE_IMPOSSIBLE_BEFORE_AND_INSTEAD) is False
    # And no executable line in the live suite may go back to the inverted
    # spelling. Comment prose is excluded: the file explains the mistake, and
    # quoting it is not committing it again.
    source = (ROOT / "tests" / "test_p26_6c_flow_immutability_pg.py").read_text(encoding="utf-8")
    code = "\n".join(
        line for line in source.splitlines()
        if not line.lstrip().startswith("#")
    )
    assert "not tgtype & 2" not in code
    assert "(tgtype & 2) == 0" not in code


def test_50_the_migration_asserts_before_with_the_bit_set():
    """055's own precondition and verification blocks, in the same polarity.

    `& 2 = 0` means "the BEFORE bit is absent", which is the AFTER case and is
    what must RAISE. The file must therefore contain `(v_tgtype & 2) = 0` as
    the *failure* condition - and never `(v_tgtype & 2) <> 0` in that role,
    which is what shipped for review.
    """
    body = _sql(IMMUTABILITY)
    assert "(v_tgtype & 2) <> 0" not in body, "the inverted BEFORE test is back"
    checks = re.findall(r"IF \(v_tgtype & 2\) = 0[^;]*?THEN", body)
    assert len(checks) == 2, checks          # precondition block + verification block
    for check in checks:
        assert "& 64" in check, "INSTEAD OF is not excluded alongside BEFORE"
    # The migration's own commentary must describe the flags correctly too -
    # the wrong version said "bit 1 = BEFORE(0)/AFTER(1)". Read from the raw
    # file, since `_sql` strips comments.
    raw = (MIGRATIONS / f"{IMMUTABILITY}.sql").read_text(encoding="utf-8")
    assert "BEFORE(0)/AFTER(1)" not in raw
    assert "BEFORE is a bit that is SET" in raw


def test_51_the_catalogue_checks_resolve_functions_precisely():
    """`proname` alone would match an overload or another schema.

    055 certifies the objects it just wrote, so it has to name them exactly:
    schema `public`, zero arguments, returning `trigger`. And each trigger is
    tied to its table AND its function AND checked to be enabled - a guard that
    someone has disabled with ALTER TABLE ... DISABLE TRIGGER is a guard that
    is not running.
    """
    body = _sql(IMMUTABILITY)

    # Counted, not merely present. 055 has two blocks that resolve these
    # objects - the precondition block and the final verification block - and
    # an assertion that only required one occurrence let a mutation delete
    # either of them unnoticed.
    assert body.count("p.pronargs = 0") == 2, body.count("p.pronargs = 0")
    assert body.count("p.prorettype = 'pg_catalog.trigger'::regtype") == 2
    assert body.count("JOIN pg_proc p ON p.oid = t.tgfoid") == 2, (
        "both trigger lookups must join through tgfoid to the function"
    )
    assert body.count("n.nspname = 'public'") >= 4
    # Both blocks tie the trigger to its function and require it to be enabled.
    assert body.count("is not enabled (tgenabled=%)") == 2
    assert body.count("calls %, expected %") == 2
    # The action-record guard must be checked NOT to fire on INSERT.
    assert "must not fire on INSERT" in body


def test_52_the_live_suite_covers_the_cases_review_found_missing():
    """Presence, by name, of the five gaps the review named.

    A weak test - it reads the live suite rather than running it - but the live
    suite cannot run here, and "the case is absent" is exactly what review
    caught. This makes a silent removal fail.
    """
    source = (ROOT / "tests" / "test_p26_6c_flow_immutability_pg.py").read_text(encoding="utf-8")
    for name in (
        "test_immutability_holds_for_a_row_with_genuinely_no_children",
        "test_repointing_a_retry_across_agencies_is_refused",
        "test_inserting_a_null_agency_is_refused",
        "test_on_delete_set_null_blanks_the_child_link_and_keeps_its_agency",
        "test_on_delete_cascade_removes_the_action_records",
    ):
        assert f"def {name}(" in source, name
    # The childless case must use rows that are genuinely childless.
    assert "EVENT_ORPHAN" in source and "EXEC_ORPHAN" in source
    # And the sequential test must not claim to be a concurrency proof.
    assert "test_a_child_insert_cannot_race_a_parent_agency_change" not in source
    assert "SEQUENTIAL, in one transaction. This is NOT a concurrency test." in source


# ===========================================================================
# 53-55 - THE LIVE SUITE MUST BE CONSTRUCTIBLE WITHOUT POSTGRESQL
#
# Review found that `test_inserting_a_null_agency_is_refused` built its SQL
# with `values.format(rule=RULE)`. The flow_events row contains `'{}'::jsonb`,
# and `str.format` reads `{}` as a positional field, so the call raised
# IndexError before any statement reached the database.
#
# Nothing in this repository could have caught it: the live module skips
# without a DSN, so the broken expression was never evaluated here, and on a
# host WITH a database the test would have failed for a reason unrelated to
# isolation.
#
# These tests evaluate the statements the live suite will issue, here, with no
# database. They do not prove the isolation - only that the proof is runnable.
# ===========================================================================

def test_53_the_null_agency_inserts_are_constructible_and_parameterised():
    """Every case must build, and none may go through `.format`."""
    from tests import test_p26_6c_flow_immutability_pg as live

    cases = live.NULL_AGENCY_INSERTS
    assert len(cases) == 3, [c[0] for c in cases]
    assert [c[0] for c in cases] == [
        "flow_events", "flow_executions", "flow_suppressions",
    ], [c[0] for c in cases]

    for table, sql, params in cases:
        # It must be a real statement against the table it claims.
        assert f"INSERT INTO {table}" in sql, (table, sql[:80])
        # NULL is written literally; everything variable is bound.
        assert "agency_id" in sql and "NULL" in sql, table
        assert sql.count("%s") == len(params), (table, sql.count("%s"), len(params))
        # And the JSON literal that broke the first version survives untouched.
        if table == "flow_events":
            assert "'{}'::jsonb" in sql, sql

    # The regression itself: `.format` on any of these raises. Asserting the
    # failure mode keeps the reason for the parameters visible.
    with pytest.raises(IndexError):
        cases[0][1].format(rule=-9601)


def test_54_no_sql_in_the_live_suite_is_built_with_str_format():
    """The class of error, not just the one instance.

    `.format` on SQL that contains a JSON literal is a landmine, and there is
    no reason to use it here - psycopg2 binds parameters.
    """
    source = (ROOT / "tests" / "test_p26_6c_flow_immutability_pg.py").read_text(encoding="utf-8")
    code = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    assert ".format(" not in code, "SQL in the live suite is being built with str.format"


def test_55_the_live_suite_names_its_database_exactly():
    """The runner's marker rule is right for a runner; not for a writer.

    `scripts/p26_migrate.py` accepts any name carrying the TEST marker and
    refuses the known production names, because it has to serve more than one
    test database. This module writes fixture rows, so it names the single
    database it may write to and refuses every other - including another
    legitimate test database.
    """
    source = (ROOT / "tests" / "test_p26_6c_flow_immutability_pg.py").read_text(encoding="utf-8")
    code = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    assert 'REQUIRED_DATABASE = "stima360_db_test"' in code
    assert "database == REQUIRED_DATABASE" in code
    # The weaker substring form must not come back.
    assert '"test" in database.lower()' not in code

    # And the connection comes from the application's own DB_* set, so a Render
    # shell needs nothing exported and no DSN is assumed to exist.
    for variable in ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD"):
        assert variable in code, variable
    assert "DATABASE_URL" not in code, "the suite must not assume DATABASE_URL"


# ===========================================================================
# 56-58 - THE LIVE CERTIFICATION IS OPT-IN
#
# Gating the live module on database configuration alone was not enough: every
# application host carries `DB_NAME`, so an ordinary `pytest tests/` on Render
# would have armed it - opening a connection and writing fixture rows against
# whatever database happened to be configured. `P26_RUN_FLOW_LIVE_CERT=1` makes
# running it a decision rather than a side effect.
#
# Test 58 proves the important half without a database: with the DSN pointing
# at a port nothing listens on and the flag absent, the module must skip. If
# the driver's connect() were reached it would raise instead.
#
# NOTE the spelling. The H11 guard in tests/test_p26_db_entrypoints.py matches
# the literal `psycopg2` + `.connect` on the raw text of every *.py file, so
# writing that token even inside a comment registers this file as a database
# connection site. It opens none, so the name is not written in full here.
# ===========================================================================

UNREACHABLE_DSN = "postgresql://u:p@127.0.0.1:1/stima360_db_test"


@pytest.mark.parametrize(
    "flag,dsn,db_name,enabled",
    [
        ("1", UNREACHABLE_DSN, None, True),      # flag + DSN
        ("1", None, "stima360_db_test", True),   # flag + the Render DB_* shape
        (None, UNREACHABLE_DSN, None, False),    # configured, not asked for
        (None, None, "stima360_db_test", False), # the Render shape, not asked for
        ("0", UNREACHABLE_DSN, None, False),     # explicitly off
        ("1", None, None, False),                # asked for, nothing to connect to
    ],
)
def test_56_the_gate_needs_both_the_flag_and_a_database(
    monkeypatch, flag, dsn, db_name, enabled
):
    from tests import test_p26_6c_flow_immutability_pg as live

    for name, value in (("P26_RUN_FLOW_LIVE_CERT", flag),
                        ("P26_PG_DSN", dsn),
                        ("DB_NAME", db_name)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)

    is_enabled, reason = live._live_cert_gate()
    assert is_enabled is enabled, (flag, dsn, db_name, reason)
    if not enabled:
        assert "BLOCKED" in reason, reason


def test_57_the_fixture_refuses_to_run_with_the_gate_closed():
    """Belt and braces: the skipif is the gate, and the fixture re-checks it.

    A future edit that loosened `pytestmark` would still have to get past this.
    """
    source = (ROOT / "tests" / "test_p26_6c_flow_immutability_pg.py").read_text(encoding="utf-8")
    assert "pytestmark = pytest.mark.skipif(not LIVE_CERT_ENABLED" in source
    assert "the live certification fixture was reached with the gate closed" in source
    # And the connection parameters are not even resolved when it is closed.
    assert "CONNECT_KWARGS = _connect_kwargs() if LIVE_CERT_ENABLED else None" in source


def test_58_without_the_flag_no_connection_is_opened(tmp_path):
    """Run the module for real, with a DSN that cannot possibly connect.

    Port 1 has nothing listening, so reaching the driver's connect() would
    raise OperationalError and the run would error. Every test skipping instead
    is the evidence that no connection was attempted.
    """
    import subprocess
    import sys

    environment = {
        **os.environ,
        "P26_PG_DSN": UNREACHABLE_DSN,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPYCACHEPREFIX": str(tmp_path),
    }
    environment.pop("P26_RUN_FLOW_LIVE_CERT", None)

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:randomly",
         "tests/test_p26_6c_flow_immutability_pg.py"],
        cwd=ROOT, env=environment, capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, result.stdout[-2000:]
    assert "skipped" in result.stdout, result.stdout[-2000:]
    for symptom in ("OperationalError", "could not connect", "Connection refused"):
        assert symptom not in result.stdout, (symptom, result.stdout[-2000:])


# ===========================================================================
# 59-60 - THE EXPECTED SQLSTATE MUST MATCH THE DECLARED ON DELETE MODE
#
# The live certification ran 28 passed / 1 failed on Render TEST. The failure
# was in the test, not in the schema:
#
#     DELETE FROM agencies WHERE id = -9101
#     -> psycopg2.errors.RestrictViolation: update or delete on table
#        "agencies" violates RESTRICT setting of foreign key constraint
#        "flow_events_agency_id_fk" on table "flow_events"
#
# while the test expected `ForeignKeyViolation`. PostgreSQL reports the two
# from different paths:
#
#     ON DELETE NO ACTION  -> 23503  "violates foreign key constraint"
#     ON DELETE RESTRICT   -> 23001  "violates RESTRICT setting of ..."
#
# 052 declares RESTRICT, and the same test asserts `confdeltype = 'r'` a few
# lines earlier - so it asserted one ON DELETE mode from the catalogue and
# expected the error class of the other. psycopg2 maps the two SQLSTATEs to
# SIBLING classes, so the expectation could not have passed against a
# correctly built schema; it was unfalsifiable in the wrong direction.
#
# Nothing here could have caught it before: the live module skips without a
# database, so the expectation was never evaluated. These two tests evaluate
# what can be evaluated offline - the exception hierarchy, and the agreement
# between the catalogue assertion and the expected class.
# ===========================================================================

def test_59_restrict_and_foreign_key_violations_are_sibling_classes():
    """The reason the wrong expectation could never have passed.

    If psycopg2 ever made one a subclass of the other, the assertion in the
    live suite would start passing for the wrong reason and this test is what
    would notice.
    """
    from psycopg2 import errors as pg_errors

    assert not issubclass(pg_errors.RestrictViolation, pg_errors.ForeignKeyViolation)
    assert not issubclass(pg_errors.ForeignKeyViolation, pg_errors.RestrictViolation)
    # Their only shared ancestor is the generic integrity error, which is
    # exactly why the live suite must not fall back to catching that instead.
    assert pg_errors.RestrictViolation.__bases__ == (pg_errors.IntegrityError,)
    assert pg_errors.ForeignKeyViolation.__bases__ == (pg_errors.IntegrityError,)
    # And the SQLSTATE mapping the diagnosis rests on.
    assert pg_errors.lookup("23001") is pg_errors.RestrictViolation
    assert pg_errors.lookup("23503") is pg_errors.ForeignKeyViolation


def test_60_the_live_suite_expects_the_class_its_catalogue_assertion_implies():
    """`confdeltype = 'r'` and `RestrictViolation` must be asserted together."""
    source = (ROOT / "tests" / "test_p26_6c_flow_immutability_pg.py").read_text(encoding="utf-8")
    code = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )

    # The catalogue side: all three agency FKs are RESTRICT.
    assert 'set(agency_fks.values()) == {"r"}' in code

    # The behavioural side must agree with it.
    assert "pytest.raises(pg_errors.RestrictViolation)" in code
    assert 'error.pgcode == "23001"' in code
    # The mismatched expectation must not come back, and the generic parent
    # must not be used to paper over the question.
    assert "ForeignKeyViolation" not in code, (
        "a RESTRICT constraint does not raise 23503"
    )
    assert "pg_errors.IntegrityError" not in code, (
        "catching the shared parent would accept either SQLSTATE"
    )

    # The refusal is attributed to a FLOW constraint by name, not merely to
    # 'some integrity error on agencies'.
    for name in ("flow_events_agency_id_fk", "flow_executions_agency_id_fk",
                 "flow_suppressions_agency_id_fk"):
        assert name in code, name

    # And the aborted statement is recovered from inside the test, so the
    # assertions after it run against a usable session.
    assert 'db.execute("SAVEPOINT restrict_probe")' in code
    assert 'db.execute("ROLLBACK TO SAVEPOINT restrict_probe")' in code
