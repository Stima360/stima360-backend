"""P26-2B2B - the public STIMA writer becomes tenant-aware.

Offline. No database: `main.get_connection` is replaced by a recording fake,
and every assertion is made against the `(sql, params)` pairs the endpoint
actually issued. Same discipline as tests/test_public_stima_core_crm_bridge.py,
whose harness this borrows.

`031` gave `stime` a nullable `agency_id`. This block makes the one anonymous
writer fill it. The column stays nullable, so the ten historical rows are
untouched and no migration is needed - but from here on a *new* estimation
without an owner is a bug, not a legacy artefact.

The single rule: the agency comes from
`core.scope.system_context_for_public_stima(cur)`, the server-side factory
already certified in P26-1, resolved from slug 'stima360' inside the same
transaction as the INSERT. The caller is anonymous and has no say. An
`agency_id` in the request body is not "validated and rejected" - it is never
read at all, which is a stronger property and the one asserted below.

Coverage map:

    B1  a new estimation is stamped with the server-resolved agency
    B2  a forged agency in the payload is ignored, and never even read
    B3  no active Default Agency -> fail closed, no ownerless row
    B4  the stima's agency is the same agency the CORE bridge uses
    B5  historical rows are untouched - no backfill hides in this block
    B6  the public contract and the STIMA -> CORE bridge still work
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_AGENCY_SLUG = "stima360"
# Deliberately not 1: a hardcoded id would pass a test that used 1.
RESOLVED_AGENCY_ID = 77
FORGED_AGENCY_ID = 999
NEW_STIMA_ID = 501


class RecordingCursor:
    """A psycopg2-ish cursor that records statements and replays rows.

    `dict_rows` mirrors RealDictCursor. The distinction matters here and is not
    incidental: `get_connection()` hands out tuple cursors, while
    `resolve_default_agency_id` reads `row["id"]`. A writer that passed the
    default cursor to the factory would raise TypeError on the first real
    request, so the fake reproduces both shapes rather than smoothing them over.
    """

    def __init__(self, connection, *, dict_rows: bool):
        self.connection = connection
        self.dict_rows = dict_rows
        self._row = None

    def execute(self, query, params=None):
        squashed = " ".join(str(query).split())
        self.connection.executions.append((squashed, params))
        lowered = squashed.lower()

        if "from agencies" in lowered:
            if self.connection.agency_row is None:
                self._row = None
            elif self.dict_rows:
                self._row = {"id": self.connection.agency_row}
            else:
                self._row = (self.connection.agency_row,)
        elif "insert into stime" in lowered:
            self._row = (NEW_STIMA_ID,)
        else:
            self._row = None

    def fetchone(self):
        return self._row

    def fetchall(self):
        return [self._row] if self._row is not None else []

    def close(self):
        pass


class RecordingConnection:
    def __init__(self, agency_row=RESOLVED_AGENCY_ID):
        self.executions: list[tuple[str, object]] = []
        self.agency_row = agency_row
        self.commits = 0
        self.rollbacks = 0

    def cursor(self, **kwargs):
        return RecordingCursor(self, dict_rows="cursor_factory" in kwargs)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass

    # -- reading the recording ---------------------------------------------
    def statements(self, needle: str) -> list[tuple[str, object]]:
        return [
            call for call in self.executions if needle.lower() in call[0].lower()
        ]

    @property
    def stima_insert(self) -> tuple[str, object] | None:
        found = self.statements("INSERT INTO stime")
        return found[0] if found else None


class JsonRequest:
    headers = {"content-type": "application/json"}

    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


BASE_PAYLOAD = {
    "comune": "Alba Adriatica",
    "microzona": "Centro",
    "mq": 90,
    "nome": "Mario",
    "cognome": "Rossi",
    "email": "mario@example.com",
    "telefono": "+39 333 123 4567",
    "prezzo_mq_base": 1500,
}


@pytest.fixture
def public_stima(monkeypatch):
    """Drive POST /api/salva_stima against a recording connection."""
    from integration_p2_support import import_project_module

    main_module = import_project_module("main")
    state = {"connection": None, "bridge_calls": [], "bridge_agency": None}

    def _run(payload_overrides=None, *, agency_row=RESOLVED_AGENCY_ID):
        connection = RecordingConnection(agency_row=agency_row)
        state["connection"] = connection
        monkeypatch.setattr(main_module, "get_connection", lambda: connection)

        monkeypatch.setattr(
            main_module, "compute_from_payload",
            lambda _payload: {
                "price_exact": 180000, "eur_mq_finale": 2000,
                "valore_pertinenze": 5000, "base_mq": 1500,
            },
        )
        monkeypatch.setattr(
            main_module, "genera_pdf_stima",
            lambda payload, nome_file: "reports/stima_501.pdf",
        )
        monkeypatch.setattr(main_module, "invia_mail", lambda *args: None)
        monkeypatch.setattr(main_module, "invia_whatsapp", lambda *args: None)

        def _bridge(stima_id, **kwargs):
            # The real bridge resolves its own SystemAgencyContext from the
            # same slug. Recording what it would have resolved lets B4 compare
            # the two without a database.
            state["bridge_calls"].append((stima_id, kwargs))
            state["bridge_agency"] = connection.agency_row
            return {
                "status": "linked", "stima_id": stima_id,
                "contact_id": 11, "lead_id": 22,
                "contact_created": True, "lead_created": True,
            }

        monkeypatch.setattr(main_module.core_service, "bridge_public_stima", _bridge)
        monkeypatch.setattr(
            main_module.seller_intelligence_service, "safe_record_event",
            lambda **kwargs: None,
        )
        monkeypatch.setattr(
            main_module.followup_service, "safe_run_followup",
            lambda **kwargs: None,
        )

        payload = {**BASE_PAYLOAD, **(payload_overrides or {})}
        return asyncio.run(main_module.salva_stima(JsonRequest(payload)))

    return type("Harness", (), {
        "run": staticmethod(_run),
        "state": state,
        "connection": property(lambda self: state["connection"]),
    })()


def _bound(call) -> list:
    _, params = call
    if params is None:
        return []
    if isinstance(params, dict):
        return list(params.values())
    return list(params)


# ---------------------------------------------------------------------------
# B1 - a new estimation carries its agency
# ---------------------------------------------------------------------------

def test_b1_the_agency_is_resolved_from_the_slug_before_the_insert(public_stima):
    public_stima.run()
    connection = public_stima.state["connection"]

    lookups = connection.statements("FROM agencies")
    assert lookups, "the writer never resolved an agency"
    sql, params = lookups[0]
    assert "slug = %s" in sql, sql
    assert "status = 'active'" in sql, sql
    assert list(params) == [DEFAULT_AGENCY_SLUG], params

    insert = connection.stima_insert
    assert insert is not None, "no stima was inserted"
    order = [call[0] for call in connection.executions]
    assert order.index(lookups[0][0]) < order.index(insert[0]), (
        "the agency was resolved after the row was already written"
    )


def test_b1_the_insert_names_and_binds_agency_id(public_stima):
    public_stima.run()
    sql, params = public_stima.state["connection"].stima_insert

    assert re.search(r"INSERT INTO stime\b[^)]*\bagency_id\b", sql, re.IGNORECASE), sql
    assert RESOLVED_AGENCY_ID in _bound((sql, params)), params


def test_b1_the_resolution_shares_the_insert_transaction(public_stima):
    """One connection, so the lookup and the INSERT commit together.

    Resolving on a second connection would leave a window in which the agency
    was read, deleted, and the row written anyway.
    """
    public_stima.run()
    connection = public_stima.state["connection"]
    assert connection.statements("FROM agencies"), "no lookup recorded"
    assert connection.stima_insert is not None
    # Both statements are on the same recording connection by construction:
    # get_connection() returns this single object.
    assert connection.commits >= 1


# ---------------------------------------------------------------------------
# B2 - the client has no say
# ---------------------------------------------------------------------------

def test_b2_a_forged_agency_id_in_the_body_is_ignored(public_stima):
    public_stima.run({"agency_id": FORGED_AGENCY_ID})
    sql, params = public_stima.state["connection"].stima_insert

    bound = _bound((sql, params))
    assert FORGED_AGENCY_ID not in bound, f"the forged agency was written: {bound}"
    assert RESOLVED_AGENCY_ID in bound, bound


@pytest.mark.parametrize(
    "forged",
    [
        {"agency_id": FORGED_AGENCY_ID},
        {"agency": FORGED_AGENCY_ID},
        {"agency_slug": "agenzia-b-test"},
        {"tenant_id": FORGED_AGENCY_ID},
    ],
)
def test_b2_no_client_agency_selector_of_any_name_is_honoured(public_stima, forged):
    public_stima.run(forged)
    sql, params = public_stima.state["connection"].stima_insert
    bound = _bound((sql, params))
    assert RESOLVED_AGENCY_ID in bound
    for value in forged.values():
        assert value not in bound, f"{forged} reached the INSERT"


AGENCY_KEYS = ("agency_id", "agency", "agency_slug", "tenant_id", "tenant")


def _handler_source() -> str:
    import ast

    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    handler = next(
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "salva_stima"
    )
    return ast.unparse(handler)


def test_b2_the_writer_never_reads_an_agency_key_from_client_data():
    """Structural: stronger than "it is rejected" - it is never consulted.

    An agency key can only reach the handler as a subscript (`data['agency_id']`)
    or a lookup call (`raw.get('agency_id')`). Both are string constants in
    those positions, so the check walks for exactly that rather than grepping
    the whole function - which would also match the column name inside the
    INSERT, where it legitimately belongs.
    """
    import ast

    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    handler = next(
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "salva_stima"
    )

    read_keys: list[str] = []
    for node in ast.walk(handler):
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            if isinstance(node.slice.value, str):
                read_keys.append(node.slice.value)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in {"get", "pop", "setdefault"}:
                for arg in node.args[:1]:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        read_keys.append(arg.value)

    leaked = [key for key in read_keys if key.lower() in AGENCY_KEYS]
    assert not leaked, f"salva_stima reads {leaked} from client data"


def test_b2_the_agency_is_never_chosen_arbitrarily():
    """No hardcoded id, and no "pick whichever agency exists" lookup.

    Scoped to agency SQL on purpose. The handler contains an unrelated
    `LIMIT 1` in the prezzo_mq_base lookup, which is legitimate and predates
    this block; banning the token outright would either fail on innocent code
    or have to be dropped. What must never happen is an *agency* chosen by
    convenience rather than resolved by slug.
    """
    import ast

    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    handler = next(
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "salva_stima"
    )

    for node in ast.walk(handler):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        sql = " ".join(node.value.split()).lower()
        if "agenc" not in sql:
            continue
        for forbidden in ("limit 1", "min(id)", "max(id)", "order by id"):
            assert forbidden not in sql, (
                f"agency SQL in salva_stima selects arbitrarily: {forbidden!r} in {sql!r}"
            )

    rendered = ast.unparse(handler)
    assert not re.search(r"agency_id\s*=\s*\d", rendered), (
        "salva_stima assigns a literal agency_id"
    )

    # The value bound to the INSERT must come from the resolved context and
    # nowhere else. Swapping `system_ctx.require_agency()` for a bare `1` in
    # the parameter tuple defeats every rule above - it reads nothing from the
    # client, contains no agency SQL, and matches no `agency_id = <digit>`
    # pattern - so the link to the factory is asserted directly.
    assert "system_ctx.require_agency()" in rendered, (
        "the agency bound to the INSERT is not the one the factory resolved"
    )


# ---------------------------------------------------------------------------
# B3 - fail closed
# ---------------------------------------------------------------------------

def test_b3_no_default_agency_means_no_stima_is_written(public_stima):
    """The row must not be created without an owner.

    A nullable column makes an ownerless INSERT technically legal, which is
    exactly why this has to be enforced by the writer rather than by the schema
    in this block.
    """
    with pytest.raises(Exception):
        public_stima.run(agency_row=None)

    connection = public_stima.state["connection"]
    assert connection.stima_insert is None, (
        "a stima was written without an agency"
    )


def test_b3_the_failure_is_not_silently_absorbed(public_stima):
    with pytest.raises(Exception) as failure:
        public_stima.run(agency_row=None)
    assert failure.value is not None


# ---------------------------------------------------------------------------
# B4 - the stima and the CORE bridge agree
# ---------------------------------------------------------------------------

def test_b4_the_stima_agency_matches_the_bridge_agency(public_stima):
    public_stima.run()
    sql, params = public_stima.state["connection"].stima_insert

    stamped = [v for v in _bound((sql, params)) if v == RESOLVED_AGENCY_ID]
    assert stamped, "no agency stamped on the stima"
    assert public_stima.state["bridge_agency"] == RESOLVED_AGENCY_ID, (
        "the bridge resolved a different agency from the writer"
    )


def test_b4_the_writer_owns_the_only_resolution():
    """R1: one factory call per request, in the writer, and nowhere downstream.

    This replaces an earlier version of this test that asserted the *bridge*
    called the factory too. That was the bug: calling the same factory twice is
    two independent decisions, not one shared one. The property that actually
    matters is that the resolution happens once, in the writer, and travels.
    """
    import inspect

    from core import repository

    bridge = inspect.getsource(repository.bridge_public_stima)
    assert "system_context_for_public_stima" not in bridge, (
        "the bridge still resolves a context of its own"
    )

    writer = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "system_context_for_public_stima" in writer, (
        "the writer does not use the certified factory"
    )
    assert writer.count("system_context_for_public_stima(") == 1, (
        "the writer resolves the Default Agency more than once"
    )


def test_b4_the_bridge_refuses_a_context_it_did_not_get_from_the_factory():
    """Receiving a scope is not trusting one.

    Without this the signature would merely relocate the authority: anything
    shaped like a context, from anywhere, would set the agency for every CORE
    row of a public estimation.
    """
    from dataclasses import dataclass

    from core import repository
    from core.scope import ProgrammingError

    @dataclass
    class LookAlike:
        agency_id: int = 999
        origin: str = "public_stima"
        role = None
        user_id = None
        is_platform_admin = False

        def require_agency(self):
            return self.agency_id

    for hostile in (LookAlike(), None, 1, "public_stima"):
        with pytest.raises(ProgrammingError):
            repository.bridge_public_stima(
                501, {}, {}, "related", system_ctx=hostile
            )


def test_b4_a_context_from_another_flow_cannot_even_be_built():
    """The type is closed to this one flow, so the bridge's origin check is
    defence in depth rather than the only line.

    Worth pinning: it is what makes threading the context safe. If
    SystemAgencyContext ever became a general-purpose system scope, passing one
    across a module boundary would start carrying real risk, and this test is
    where that change gets noticed.
    """
    from operator_auth.context import SystemAgencyContext

    with pytest.raises(ValueError):
        SystemAgencyContext(agency_id=RESOLVED_AGENCY_ID, origin="something_else")


def test_b4_the_accepted_origin_is_the_one_the_factory_produces():
    """Pins the repository's constant to the factory's literal.

    The two live in different modules on purpose - core/scope.py keeps the
    origin literal it was certified with - so this is what stops them drifting.
    """
    from core import repository
    from core.scope import system_context_for_public_stima

    class _Cur:
        def execute(self, *_a, **_k):
            pass

        def fetchone(self):
            return {"id": RESOLVED_AGENCY_ID}

    produced = system_context_for_public_stima(_Cur())
    assert produced.origin == repository.PUBLIC_STIMA_ORIGIN


def test_b4_the_bridge_still_receives_the_new_stima_id(public_stima):
    public_stima.run()
    calls = public_stima.state["bridge_calls"]
    assert len(calls) == 1, calls
    assert calls[0][0] == NEW_STIMA_ID


# ---------------------------------------------------------------------------
# B5 - nothing historical moves
# ---------------------------------------------------------------------------

def test_b5_the_flow_performs_no_backfill(public_stima):
    """The ten legacy NULL rows belong to a later block."""
    public_stima.run()
    for sql, _ in public_stima.state["connection"].executions:
        upper = sql.upper()
        if "UPDATE STIME" in upper:
            assert "WHERE ID = %S" in upper or "WHERE ID=%S" in upper, (
                f"an unscoped UPDATE on stime: {sql}"
            )
        assert "SET AGENCY_ID" not in upper, f"a backfill hid in the flow: {sql}"


def test_b5_the_historical_backfill_is_a_migration_not_runtime_code():
    """Retargeted when 032 arrived.

    This asserted that 031 was the newest migration - a scope check for the
    writer block, which correctly fired the moment the backfill block landed.
    What it was really protecting is that the runtime writer never rewrites
    historical rows: the ten legacy NULLs are moved by a reviewed, transactional
    migration, or not at all.

    The behavioural half is `test_b5_the_flow_performs_no_backfill` above; this
    is the structural half.
    """
    writer = (ROOT / "main.py").read_text(encoding="utf-8")
    assert not re.search(r"UPDATE\s+stime\s+SET\s+agency_id", writer, re.IGNORECASE), (
        "main.py rewrites the agency of existing estimations"
    )

    backfill = ROOT / "migrations" / "032_p26_stima_agency_backfill.sql"
    assert backfill.exists(), "the historical backfill is not a migration"
    assert (
        ROOT / "migrations" / "032_p26_stima_agency_backfill_down.sql"
    ).exists(), "the backfill migration ships no down file"


# ---------------------------------------------------------------------------
# B6 - the public contract is unchanged
# ---------------------------------------------------------------------------

def test_b6_the_endpoint_still_returns_its_contract(public_stima):
    result = public_stima.run()
    assert isinstance(result, dict), result
    assert result.get("id") == NEW_STIMA_ID or result.get("stima_id") == NEW_STIMA_ID, result


def test_b6_the_bridge_and_downstream_still_run(public_stima):
    public_stima.run()
    assert public_stima.state["bridge_calls"], "the CORE bridge did not run"


# ---------------------------------------------------------------------------
# B7 (R1) - one ownership decision per request
#
# B4 proved the writer and the bridge use the same *factory*. That is not the
# same as using the same *answer*. Before R1 each resolved independently: the
# writer on its own connection, the bridge on a second one it opened after the
# first had committed and closed. Two lookups, two transactions, and nothing
# tying the second to the first.
#
# The window is small but real - the Default Agency can be renamed, deactivated
# or replaced between them - and the consequence is the one thing multi-tenancy
# cannot tolerate: a stima in one agency whose contact and lead are in another,
# with no constraint anywhere to catch it.
#
# So these tests hold the property directly: the ownership of a public
# estimation is decided once, and every row the request produces carries that
# one decision. The fake below makes the second resolution return a *different*
# agency, which turns a re-resolution from a theoretical race into a visible,
# deterministic failure.
# ---------------------------------------------------------------------------

DRIFTED_AGENCY_ID = 88


class DriftingBridgeCursor:
    """The bridge's own cursor - and its `agencies` table has moved on.

    Any lookup this cursor answers returns DRIFTED_AGENCY_ID, never
    RESOLVED_AGENCY_ID. A bridge that resolves its own context therefore writes
    88 while the stima carries 77. A bridge that uses the context it was handed
    never queries agencies here at all.
    """

    def __init__(self):
        self.calls: list[tuple[str, object]] = []
        self.inserted: dict[str, list[dict]] = {
            "contacts": [], "leads": [], "lead_stime": []
        }
        self._rows: list = []
        self._next_id = 600

    def execute(self, sql, params=None):
        squashed = " ".join(str(sql).split())
        self.calls.append((squashed, params))
        lowered = squashed.lower()

        if "from agencies" in lowered:
            self._rows = [{"id": DRIFTED_AGENCY_ID}]
        elif "pg_advisory_xact_lock" in lowered:
            self._rows = [{"locked": True}]
        elif "from lead_stime ls" in lowered:
            self._rows = []
        elif "from contacts" in lowered:
            self._rows = []
        elif "insert into contacts" in lowered:
            self._next_id += 1
            row = {**params, "id": self._next_id}
            self.inserted["contacts"].append(row)
            self._rows = [row]
        elif "insert into leads" in lowered:
            self._next_id += 1
            row = {**params, "id": self._next_id}
            self.inserted["leads"].append(row)
            self._rows = [row]
        elif "insert into lead_stime" in lowered:
            self.inserted["lead_stime"].append(
                {"lead_id": params[0], "stima_id": params[1]}
            )
            self._rows = [{"lead_id": params[0], "stima_id": params[1]}]
        else:  # pragma: no cover - a new statement should be noticed loudly
            raise AssertionError(f"unexpected bridge SQL: {squashed}")

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def statements(self, needle: str) -> list[tuple[str, object]]:
        return [c for c in self.calls if needle.lower() in c[0].lower()]


@pytest.fixture
def public_stima_real_bridge(monkeypatch):
    """As `public_stima`, but the real CORE bridge runs - not a stub.

    B4's fixture replaces `bridge_public_stima` to observe the call. Here the
    genuine service and repository run end to end, so the context that reaches
    the CORE inserts is the one the production code actually chose.
    """
    from contextlib import contextmanager

    from integration_p2_support import import_project_module

    main_module = import_project_module("main")
    # The repository object main actually calls through, not a second import of
    # the same name - `import_project_module` controls its own import graph, and
    # patching the wrong instance would leave the real bridge running against a
    # real cursor and quietly prove nothing.
    core_repository = main_module.core_service.repository
    state = {}

    def _run():
        connection = RecordingConnection(agency_row=RESOLVED_AGENCY_ID)
        bridge_cursor = DriftingBridgeCursor()
        state["connection"] = connection
        state["bridge_cursor"] = bridge_cursor

        @contextmanager
        def _core_cursor(*_args, **_kwargs):
            yield (None, bridge_cursor)

        monkeypatch.setattr(core_repository, "core_cursor", _core_cursor)
        monkeypatch.setattr(main_module, "get_connection", lambda: connection)
        monkeypatch.setattr(
            main_module, "compute_from_payload",
            lambda _payload: {
                "price_exact": 180000, "eur_mq_finale": 2000,
                "valore_pertinenze": 5000, "base_mq": 1500,
            },
        )
        monkeypatch.setattr(
            main_module, "genera_pdf_stima",
            lambda payload, nome_file: "reports/stima_501.pdf",
        )
        monkeypatch.setattr(main_module, "invia_mail", lambda *args: None)
        monkeypatch.setattr(main_module, "invia_whatsapp", lambda *args: None)
        monkeypatch.setattr(
            main_module.seller_intelligence_service, "safe_record_event",
            lambda **kwargs: None,
        )
        monkeypatch.setattr(
            main_module.followup_service, "safe_run_followup",
            lambda **kwargs: None,
        )

        return asyncio.run(main_module.salva_stima(JsonRequest(dict(BASE_PAYLOAD))))

    return type("Harness", (), {"run": staticmethod(_run), "state": state})()


def test_b7_the_contact_lands_in_the_agency_the_writer_resolved(
    public_stima_real_bridge,
):
    public_stima_real_bridge.run()
    contacts = public_stima_real_bridge.state["bridge_cursor"].inserted["contacts"]

    assert contacts, "the bridge inserted no contact"
    agencies = {row["agency_id"] for row in contacts}
    assert agencies == {RESOLVED_AGENCY_ID}, (
        f"the contact was written to {agencies}, not the agency the stima carries "
        f"({RESOLVED_AGENCY_ID}) - the bridge resolved its own context"
    )


def test_b7_the_lead_lands_in_the_agency_the_writer_resolved(
    public_stima_real_bridge,
):
    public_stima_real_bridge.run()
    leads = public_stima_real_bridge.state["bridge_cursor"].inserted["leads"]

    assert leads, "the bridge inserted no lead"
    agencies = {row["agency_id"] for row in leads}
    assert agencies == {RESOLVED_AGENCY_ID}, (
        f"the lead was written to {agencies}, not {RESOLVED_AGENCY_ID}"
    )


def test_b7_stime_contact_and_lead_all_carry_one_agency(public_stima_real_bridge):
    """The property in full: stime.agency_id == contact.agency_id == lead.agency_id."""
    public_stima_real_bridge.run()
    connection = public_stima_real_bridge.state["connection"]
    cursor = public_stima_real_bridge.state["bridge_cursor"]

    stima_agency = [
        value for value in _bound(connection.stima_insert)
        if value in (RESOLVED_AGENCY_ID, DRIFTED_AGENCY_ID)
    ]
    contact_agency = [row["agency_id"] for row in cursor.inserted["contacts"]]
    lead_agency = [row["agency_id"] for row in cursor.inserted["leads"]]

    assert stima_agency, "the stima carries no agency"
    assert set(stima_agency) | set(contact_agency) | set(lead_agency) == {
        RESOLVED_AGENCY_ID
    }, (
        f"one request produced more than one agency: stime={stima_agency} "
        f"contact={contact_agency} lead={lead_agency}"
    )


def test_b7_the_bridge_does_not_resolve_an_agency_of_its_own(
    public_stima_real_bridge,
):
    """The direct statement of the fix: one resolution per request.

    Asserted on the bridge's own cursor, so it holds regardless of what the
    lookup would have returned - it is the second lookup itself that is gone,
    not merely its disagreement.
    """
    public_stima_real_bridge.run()
    lookups = public_stima_real_bridge.state["bridge_cursor"].statements("FROM agencies")

    assert not lookups, (
        f"the bridge resolved the Default Agency a second time: {lookups}"
    )


def test_b7_the_writer_resolves_the_agency_exactly_once(public_stima_real_bridge):
    public_stima_real_bridge.run()
    lookups = public_stima_real_bridge.state["connection"].statements("FROM agencies")
    assert len(lookups) == 1, (
        f"the request resolved its ownership {len(lookups)} times: {lookups}"
    )
