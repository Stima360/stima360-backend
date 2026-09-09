"""P26-6C OWNER Admin - the account, access and token writes are agency-bound.

WHAT THIS SLICE COVERS

    POST /api/owner/admin/accounts
    POST /api/owner/admin/accounts/{id}/disable
    POST /api/owner/admin/accounts/{id}/enable
    POST /api/owner/admin/access
    POST /api/owner/admin/access/{id}/revoke
    POST /api/owner/admin/accounts/{id}/tokens

Six writes. The reads were the previous slice; these are the operations that
create an owner identity, grant it a property, and mint the token it logs in
with - so an unscoped one is worse than an unscoped listing: it does not leak
another agency's data, it changes it.

`create_access` already refused a grant whose two roots disagree. That is not
the same rule as the one added here: a grant of B to a property of B is
perfectly coherent and must still be refused when the caller is A.

WHAT THIS MODEL PROVES, AND WHAT IT DOES NOT

Rows carry an agency and statements are answered through it, so a write that
reaches the wrong row is visible. Every INSERT and UPDATE is recorded, and so
is the audit log, because "refused" has to mean nothing happened - not even a
success audit.

Connection and transaction identity are recorded too: a tenant check that runs
in its own transaction and an INSERT that runs in another is a check that
proves nothing about the row being written.

Nothing here runs PostgreSQL. `FOR SHARE` / `FOR UPDATE` are read from the
statements, never observed. Rows already stored on TEST are untouched by any of
this.
"""
from __future__ import annotations

import re
from contextlib import contextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.exceptions import NotFoundError
from operator_auth.context import OperatorContext
from operator_auth.dependencies import legacy_basic_agency_context
from owner import repository
from owner.router_admin import router as admin_router


AGENCY_A = 901
AGENCY_B = 902
ALL_AGENCIES = (AGENCY_A, AGENCY_B)

CONTACT_A, CONTACT_B = 11, 12
CONTACT_UNCLAIMED_A = 13        # agency A, has no owner account yet
ACCOUNT_A, ACCOUNT_B = 1, 2
PROPERTY_A, PROPERTY_B = 101, 102
GRANT_A, GRANT_B, GRANT_LEGACY = 5001, 5002, 5003

MISSING_CONTACT = 88
MISSING_ACCOUNT = 89
MISSING_GRANT = 5099

AUTH = ("giorgio", "test-secret")


# ---------------------------------------------------------------------------
# Statement reading, shared with the read slice's approach.
# ---------------------------------------------------------------------------

_TENANT = re.compile(r"agency_id\s*=\s*%s", re.IGNORECASE)


def alias_map(sql: str) -> dict[str, str]:
    names: dict[str, str] = {}
    for table, alias in re.findall(r"\b(?:from|join)\s+(\w+)(?:\s+(\w+))?", sql, re.IGNORECASE):
        table = table.lower()
        names[table] = table
        alias = (alias or "").lower()
        if alias and alias not in {"on", "where", "join", "order", "limit", "group", "set"}:
            names[alias] = table
    return names


def subject_table(sql: str) -> str | None:
    lowered = sql.lower()
    for verb in ("insert into", "update"):
        if lowered.startswith(verb):
            found = re.match(rf"{verb}\s+(\w+)", lowered)
            return found.group(1) if found else None
    names = alias_map(sql)
    projection = re.match(r"^\s*select\s+(?:distinct\s+)?(.+?)\s+from\b", sql, re.IGNORECASE | re.DOTALL)
    if projection:
        first = projection.group(1).split(",")[0].strip()
        if "." in first:
            qualifier = first.split(".")[0].strip().lower()
            if qualifier in names:
                return names[qualifier]
    found = re.search(r"\bfrom\s+(\w+)", sql, re.IGNORECASE)
    return found.group(1).lower() if found else None


# ---------------------------------------------------------------------------
# The two-agency world.
# ---------------------------------------------------------------------------

CONTACTS = {
    CONTACT_A: AGENCY_A,
    CONTACT_B: AGENCY_B,
    CONTACT_UNCLAIMED_A: AGENCY_A,
}
ACCOUNTS = {ACCOUNT_A: CONTACT_A, ACCOUNT_B: CONTACT_B}
PROPERTIES = {PROPERTY_A: AGENCY_A, PROPERTY_B: AGENCY_B}
# grant -> (account root agency, property root agency)
GRANTS = {
    GRANT_A: (AGENCY_A, AGENCY_A),
    GRANT_B: (AGENCY_B, AGENCY_B),
    GRANT_LEGACY: (AGENCY_A, AGENCY_B),     # written before create_access checked
}


class Statement:
    __slots__ = ("sql", "params", "connection", "transaction")

    def __init__(self, sql, params, connection, transaction):
        self.sql = sql
        self.params = params
        self.connection = connection
        self.transaction = transaction

    @property
    def verb(self) -> str:
        return self.sql.split(None, 1)[0].lower()

    @property
    def table(self) -> str | None:
        return subject_table(self.sql)


class World:
    def __init__(self):
        self.statements: list[Statement] = []
        self.audit: list[dict] = []
        self.writes: list[Statement] = []
        self.commits = 0
        self.rollbacks = 0
        self._connections = 0
        self._transactions = 0

    def of(self, table: str) -> list[Statement]:
        return [s for s in self.statements if s.table == table]

    def success_audit(self) -> list[dict]:
        return [row for row in self.audit if row.get("result") == "success"]


class Connection:
    def __init__(self, world: World):
        self.world = world
        world._connections += 1
        self.id = world._connections
        self.transaction: int | None = None

    def begin_if_needed(self) -> int:
        if self.transaction is None:
            self.world._transactions += 1
            self.transaction = self.world._transactions
        return self.transaction

    def commit(self):
        self.world.commits += 1
        self.transaction = None

    def rollback(self):
        self.world.rollbacks += 1
        self.transaction = None


class Cursor:
    def __init__(self, world: World, connection: Connection):
        self.world = world
        self.connection = connection
        self._rows: list[dict] = []
        self._next_id = 7000

    def execute(self, sql, params=None):
        flat = " ".join(str(sql).split())
        values = list(params or ())
        statement = Statement(flat, tuple(values), self.connection.id,
                              self.connection.begin_if_needed())
        self.world.statements.append(statement)
        lowered = flat.lower()

        if lowered.startswith("insert into owner_audit_log"):
            keys = ("owner_account_id", "property_id", "action", "entity_type",
                    "entity_id", "result", "metadata")
            self.world.audit.append(dict(zip(keys, values + [None] * len(keys))))
            self._rows = []
            return

        if statement.verb in {"insert", "update"}:
            self.world.writes.append(statement)
            self._rows = [self._written(statement, values)]
            return

        self._rows = self._read(flat, values)

    # -- reads --------------------------------------------------------------

    def _read(self, flat: str, values: list) -> list[dict]:
        # Dispatch on the FROM, not on the projection: the account-tenant read
        # projects the contact's agency but is keyed by the account.
        lowered = flat.lower()
        pinned = bool(_TENANT.search(flat))
        asked = {v for v in values if v in ALL_AGENCIES}

        if re.search(r"\bfrom\s+owner_accounts\b", lowered):
            table = "owner_accounts"
        elif re.search(r"\bfrom\s+owner_property_access\b", lowered):
            table = "owner_property_access"
        elif re.search(r"\bfrom\s+contacts\b", lowered):
            table = "contacts"
        elif re.search(r"\bfrom\s+properties\b", lowered):
            table = "properties"
        else:
            table = subject_table(flat)

        if table == "contacts":
            contact = next((v for v in values if v in CONTACTS), None)
            if contact is None:
                return []
            agency = CONTACTS[contact]
            if pinned and agency not in asked:
                return []
            return [{"id": contact, "agency_id": agency}]

        if table == "owner_accounts":
            account = next((v for v in values if v in ACCOUNTS), None)
            if account is None:
                return []
            agency = CONTACTS[ACCOUNTS[account]]
            if pinned and agency not in asked:
                return []
            return [{"id": account, "contact_id": ACCOUNTS[account], "agency_id": agency}]

        if table == "properties":
            prop = next((v for v in values if v in PROPERTIES), None)
            if prop is None:
                return []
            agency = PROPERTIES[prop]
            if pinned and agency not in asked:
                return []
            return [{"id": prop, "agency_id": agency}]

        if table == "owner_property_access":
            grant = next((v for v in values if v in GRANTS), None)
            if grant is None:
                return []
            account_agency, property_agency = GRANTS[grant]
            joined = alias_map(flat).values()
            if pinned:
                if {"contacts", "owner_accounts"} & set(joined) and account_agency not in asked:
                    return []
                if "properties" in joined and property_agency not in asked:
                    return []
            return [{"id": grant, "owner_account_id": ACCOUNT_A, "property_id": PROPERTY_A}]

        raise AssertionError(f"the model was asked something it does not know: {flat}")

    # -- writes -------------------------------------------------------------

    def _written(self, statement: Statement, values: list) -> dict:
        self._next_id += 1
        row = {"id": self._next_id, "owner_account_id": None, "property_id": None,
               "expires_at": "2030-01-01T00:00:00Z", "status": "active"}
        if statement.table == "owner_property_access":
            row["owner_account_id"] = values[0] if values else None
            row["property_id"] = values[1] if len(values) > 1 else None
        if statement.table in {"owner_accounts", "owner_access_tokens"}:
            account = next((v for v in values if v in ACCOUNTS), None)
            row["owner_account_id"] = account if account is not None else self._next_id
        return row

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def close(self):
        pass


@pytest.fixture
def world(monkeypatch):
    w = World()

    @contextmanager
    def fake_core_cursor(*, commit: bool = False):
        connection = Connection(w)
        cursor = Cursor(w, connection)
        try:
            yield connection, cursor
            if commit:
                connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()

    monkeypatch.setattr(repository, "core_cursor", fake_core_cursor)
    return w


def context(agency_id):
    return OperatorContext(
        user_id=None, agency_id=agency_id, role="agency_owner",
        is_platform_admin=agency_id is None, session_id=None,
        auth_channel="legacy_basic",
    )


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "giorgio")
    monkeypatch.setenv("ADMIN_PASS", "test-secret")
    app = FastAPI()
    app.include_router(admin_router)

    def use(agency_id):
        app.dependency_overrides[legacy_basic_agency_context] = lambda: context(agency_id)
        return TestClient(app)

    return use


# Every write under test, as (name, allowed-for-A call, refused-for-A call).
ALLOWED = {
    "create_account": lambda a: repository.create_account(a, {"contact_id": CONTACT_UNCLAIMED_A}),
    "set_account": lambda a: repository.set_account(a, ACCOUNT_A, "disabled"),
    "create_access": lambda a: repository.create_access(a, {"owner_account_id": ACCOUNT_A, "property_id": PROPERTY_A}),
    "revoke_access": lambda a: repository.revoke_access(a, GRANT_A),
    "create_token": lambda a: repository.create_token(a, ACCOUNT_A),
}

REFUSED = {
    "create_account: contact of B":
        lambda: repository.create_account(AGENCY_A, {"contact_id": CONTACT_B}),
    "create_account: contact absent":
        lambda: repository.create_account(AGENCY_A, {"contact_id": MISSING_CONTACT}),
    "set_account: account of B":
        lambda: repository.set_account(AGENCY_A, ACCOUNT_B, "disabled"),
    "set_account: account absent":
        lambda: repository.set_account(AGENCY_A, MISSING_ACCOUNT, "disabled"),
    "create_access: property of B":
        lambda: repository.create_access(AGENCY_A, {"owner_account_id": ACCOUNT_A, "property_id": PROPERTY_B}),
    "create_access: coherent pair of B":
        lambda: repository.create_access(AGENCY_A, {"owner_account_id": ACCOUNT_B, "property_id": PROPERTY_B}),
    "revoke_access: grant of B":
        lambda: repository.revoke_access(AGENCY_A, GRANT_B),
    "revoke_access: incoherent legacy grant":
        lambda: repository.revoke_access(AGENCY_A, GRANT_LEGACY),
    "revoke_access: grant absent":
        lambda: repository.revoke_access(AGENCY_A, MISSING_GRANT),
    "create_token: account of B":
        lambda: repository.create_token(AGENCY_A, ACCOUNT_B),
    "create_token: account absent":
        lambda: repository.create_token(AGENCY_A, MISSING_ACCOUNT),
}


# ---------------------------------------------------------------------------
# 1-3 - what must work
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(ALLOWED))
def test_1_an_operation_inside_the_callers_agency_succeeds(world, name):
    ALLOWED[name](AGENCY_A)
    assert world.writes, f"{name} wrote nothing"
    assert world.rollbacks == 0


@pytest.mark.parametrize("name", sorted(ALLOWED))
def test_2_the_same_operation_works_for_agency_b(world, name):
    """The mirror, so the rule is not "agency A is special"."""
    if name == "create_account":
        repository.create_account(AGENCY_B, {"contact_id": CONTACT_B})
    elif name == "set_account":
        repository.set_account(AGENCY_B, ACCOUNT_B, "disabled")
    elif name == "create_access":
        repository.create_access(AGENCY_B, {"owner_account_id": ACCOUNT_B, "property_id": PROPERTY_B})
    elif name == "revoke_access":
        repository.revoke_access(AGENCY_B, GRANT_B)
    else:
        repository.create_token(AGENCY_B, ACCOUNT_B)
    assert world.writes
    assert world.rollbacks == 0


@pytest.mark.parametrize("name", sorted(ALLOWED))
def test_3_a_successful_operation_audits_success(world, name):
    ALLOWED[name](AGENCY_A)
    if name == "set_account":
        # set_account has never written an audit row; this patch does not add
        # one, and inventing one here would be a behaviour change smuggled into
        # a scoping slice.
        assert world.audit == []
    else:
        assert world.success_audit(), (name, world.audit)


# ---------------------------------------------------------------------------
# 4-6 - what must be refused, and what a refusal leaves behind
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(REFUSED))
def test_4_an_operation_outside_the_callers_agency_is_refused(world, name):
    with pytest.raises(NotFoundError) as excinfo:
        REFUSED[name]()
    assert str(excinfo.value) == "Risorsa non trovata", name


@pytest.mark.parametrize("name", sorted(REFUSED))
def test_5_a_refusal_writes_nothing_and_audits_nothing(world, name):
    with pytest.raises(NotFoundError):
        REFUSED[name]()
    assert world.writes == [], (name, [s.sql for s in world.writes])
    assert world.audit == [], (name, world.audit)
    assert world.commits == 0, name
    assert world.rollbacks == 1, name


def test_6_a_coherent_pair_of_another_agency_is_still_refused(world):
    """The rule this slice adds, distinct from the one create_access had.

    Account B and property B agree with each other - `create_access` has
    refused disagreeing pairs since e84a1d4 and would accept this one. What
    refuses it now is that neither belongs to the caller.
    """
    with pytest.raises(NotFoundError):
        repository.create_access(AGENCY_A, {"owner_account_id": ACCOUNT_B, "property_id": PROPERTY_B})
    assert world.writes == []


# ---------------------------------------------------------------------------
# 7-9 - how the check is made
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(ALLOWED))
def test_7_the_check_and_the_write_share_one_open_transaction(world, name):
    """A tenant check in its own transaction proves nothing about the row the
    next transaction writes."""
    ALLOWED[name](AGENCY_A)
    reads = [s for s in world.statements if s.verb == "select"]
    writes = world.writes
    assert reads, name
    assert writes, name
    assert len({s.connection for s in reads + writes}) == 1, name
    assert len({s.transaction for s in reads + writes}) == 1, name
    assert reads[0].transaction is not None


# create_access is shaped differently on purpose - see test 8b.
BELONGS_TO_ME = sorted(set(ALLOWED) - {"create_access"})


@pytest.mark.parametrize("name", BELONGS_TO_ME)
def test_8_the_deciding_read_names_the_tenant_and_locks_its_rows(world, name):
    """"Does this row belong to me" is asked as a predicate, not as a value
    fetched and compared afterwards, so the statement itself carries the scope
    and an audit of what this module executes can see it."""
    ALLOWED[name](AGENCY_A)
    reads = [s for s in world.statements if s.verb == "select"]
    assert reads, name
    for statement in reads:
        assert _TENANT.search(statement.sql), (name, statement.sql)
        assert "for share" in statement.sql.lower() or "for update" in statement.sql.lower(), (
            name, statement.sql
        )
    assert {v for s in reads for v in s.params if v in ALL_AGENCIES} == {AGENCY_A}, name


def test_8b_create_access_keeps_two_separate_rules(world):
    """Its reads project the two agencies instead of pinning one.

    That is deliberate and is why it is excluded from test 8: the function has
    to compare the account's agency with the property's - a rule about the two
    roots agreeing with EACH OTHER, which a `WHERE ct.agency_id=%s` would make
    vacuous by forcing both to the caller's. Both rules are live: test 4 covers
    the disagreeing pair, test 6 the coherent pair of another agency.
    """
    repository.create_access(AGENCY_A, {"owner_account_id": ACCOUNT_A, "property_id": PROPERTY_A})
    reads = [s for s in world.statements if s.verb == "select"]
    assert len(reads) == 2, [s.sql for s in reads]
    for statement in reads:
        assert "agency_id" in statement.sql, statement.sql
        assert "for share" in statement.sql.lower(), statement.sql
        assert AGENCY_B not in statement.params
    source = repository.create_access.__doc__ or ""
    import inspect
    body = inspect.getsource(repository.create_access)
    assert "account_agency!=property_agency" in body, "the two-root rule is gone"
    assert "account_agency!=agency_id" in body, "the caller rule is gone"


def test_9_revoke_access_requires_both_roots(world):
    repository.revoke_access(AGENCY_A, GRANT_A)
    guard = [s for s in world.statements if s.verb == "select"][0]
    lowered = guard.sql.lower()
    assert "owner_accounts" in lowered and "contacts" in lowered, lowered
    assert "properties" in lowered, lowered
    assert lowered.count("agency_id=%s") == 2, lowered


# ---------------------------------------------------------------------------
# 10-13 - the HTTP surface
# ---------------------------------------------------------------------------

WRITE_ROUTES = [
    ("post", "/api/owner/admin/accounts", {"contact_id": CONTACT_UNCLAIMED_A}),
    ("post", f"/api/owner/admin/accounts/{ACCOUNT_A}/disable", None),
    ("post", f"/api/owner/admin/accounts/{ACCOUNT_A}/enable", None),
    ("post", "/api/owner/admin/access", {"owner_account_id": ACCOUNT_A, "property_id": PROPERTY_A}),
    ("post", f"/api/owner/admin/access/{GRANT_A}/revoke", None),
    ("post", f"/api/owner/admin/accounts/{ACCOUNT_A}/tokens", {"token_type": "login"}),
]

FOREIGN_ROUTES = [
    ("post", "/api/owner/admin/accounts", {"contact_id": CONTACT_B}),
    ("post", f"/api/owner/admin/accounts/{ACCOUNT_B}/disable", None),
    ("post", f"/api/owner/admin/accounts/{ACCOUNT_B}/enable", None),
    ("post", "/api/owner/admin/access", {"owner_account_id": ACCOUNT_B, "property_id": PROPERTY_B}),
    ("post", f"/api/owner/admin/access/{GRANT_B}/revoke", None),
    ("post", f"/api/owner/admin/accounts/{ACCOUNT_B}/tokens", {"token_type": "login"}),
]


@pytest.mark.parametrize("method,path,body", WRITE_ROUTES)
def test_10_the_write_routes_work_for_their_own_agency(world, client, method, path, body):
    response = getattr(client(AGENCY_A), method)(path, json=body, auth=AUTH)
    assert response.status_code in (200, 201), (path, response.status_code, response.text)


@pytest.mark.parametrize("method,path,body", FOREIGN_ROUTES)
def test_11_the_write_routes_refuse_another_agency_neutrally(world, client, method, path, body):
    response = getattr(client(AGENCY_A), method)(path, json=body, auth=AUTH)
    assert response.status_code == 404, (path, response.status_code, response.text)
    assert response.json() == {"detail": "Risorsa non trovata"}
    assert world.writes == [], [s.sql for s in world.writes]
    assert world.audit == []


@pytest.mark.parametrize("method,path,body", WRITE_ROUTES)
def test_12_no_client_parameter_can_change_the_agency(world, client, method, path, body):
    payload = dict(body or {})
    payload["agency_id"] = AGENCY_B
    response = getattr(client(AGENCY_A), method)(
        path, json=payload, params={"agency_id": AGENCY_B}, auth=AUTH
    )
    assert response.status_code in (200, 201, 422), (path, response.status_code)
    seen = {v for s in world.statements for v in s.params if v in ALL_AGENCIES}
    assert AGENCY_B not in seen, (path, seen)


@pytest.mark.parametrize("method,path,body", WRITE_ROUTES)
def test_13_a_context_without_an_agency_is_refused_before_any_query(world, client, method, path, body):
    response = getattr(client(None), method)(path, json=body, auth=AUTH)
    assert response.status_code == 403, (path, response.status_code, response.text)
    assert world.statements == [], "an unbound context reached the database"


# ---------------------------------------------------------------------------
# 14-16 - signatures, admission, and what must not have moved
# ---------------------------------------------------------------------------

def test_14_every_scoped_repository_function_takes_the_agency_first():
    import inspect

    for fn in (repository.create_account, repository.set_account,
               repository.create_access, repository.revoke_access,
               repository.create_token):
        parameters = list(inspect.signature(fn).parameters.values())
        assert parameters[0].name == "agency_id", (fn.__name__, parameters[0].name)
        assert parameters[0].default is inspect.Parameter.empty, fn.__name__


def test_15_the_six_write_routes_take_the_agency_context():
    """This slice's six. The full OWNER Admin set is pinned in
    tests/test_p26_6c_owner_admin_content.py, which grows block by block; here
    the concern is only that none of these six loses its context."""
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    scoped = set()
    for module in ("owner/router_admin.py", "owner/router_admin_lookups.py"):
        tree = ast.parse((root / module).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if any(
                    isinstance(default, ast.Call)
                    and getattr(default.func, "id", None) == "Depends"
                    and default.args
                    and getattr(default.args[0], "id", None) == "legacy_basic_agency_context"
                    for default in node.args.defaults
                ):
                    scoped.add(node.name)

    assert scoped >= {
        # reads
        "accounts", "access", "contacts", "account_properties",
        "property_documents", "property_visits",
        # writes
        "account", "disable", "enable", "access_create", "revoke", "token",
    }, sorted(scoped)


def test_16_the_portal_and_flow_are_untouched():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    portal = (root / "owner" / "router_portal.py").read_text(encoding="utf-8")
    assert "legacy_basic_agency_context" not in portal, (
        "the owner portal keeps its own authentication; it must not acquire an "
        "operator context"
    )
    assert "current_owner" in portal

    flow = (root / "flow" / "router.py").read_text(encoding="utf-8")
    assert "from owner.router_admin import require_owner_admin" in flow
    assert "dependencies=[Depends(require_owner_admin)]" in flow

    admin = (root / "owner" / "router_admin.py").read_text(encoding="utf-8")
    assert "dependencies=[Depends(require_owner_admin)]" in admin
    assert 'realm="STIMA360 OWNER Admin"' in admin
