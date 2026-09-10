"""P26-6C OWNER Admin - the six read surfaces are bound to one agency.

WHAT THIS SLICE COVERS

    GET /api/owner/admin/accounts
    GET /api/owner/admin/access
    GET /api/owner/admin/lookups/contacts
    GET /api/owner/admin/lookups/accounts/{a}/properties
    GET /api/owner/admin/lookups/accounts/{a}/properties/{p}/documents
    GET /api/owner/admin/lookups/accounts/{a}/properties/{p}/visits

Six reads, no writes. `lookup_contacts` was the worst read in the module - free
text over every agency's contacts, by name and by email - and `list_access`
exposed every grant on the platform.

The agency is resolved server-side by `basic_only_agency_context`, the same
compatibility dependency CRM and FLOW already use: the Default Agency, from its
slug, agency-bound, `is_platform_admin=False`. The mount stays on
`require_owner_admin`, untouched, because FLOW mounts on it too.

HOW THE MODEL WORKS, AND WHAT IT PROVES

Rows carry an agency. A statement is answered only if every entity named in its
parameters belongs to an agency the statement also names - which is what the
join to the tenant does in the real query. A statement that names NO tenant is
answered with both agencies' rows, so an unscoped read leaks visibly and fails
here rather than in production.

`owner_property_access` rows carry two agencies, one per root. Whether each is
required is decided by what the statement actually joins, so filtering only one
root is a detectable mistake - and the fixture contains a legacy grant whose two
roots disagree, which must be invisible to BOTH agencies.

WHAT IT DOES NOT PROVE

Nothing here runs PostgreSQL. There is no proof about real joins, real
collation of ILIKE, or the rows that exist on TEST today. Incoherent grants
already stored are hidden by these queries, not repaired.
"""
from __future__ import annotations

import re
from contextlib import contextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.exceptions import NotFoundError
from operator_auth.context import OperatorContext
from operator_auth.dependencies import basic_only_agency_context
from owner import admin_lookup_repository as lookups
from owner import repository
from owner.router_admin import router as admin_router


AGENCY_A = 901
AGENCY_B = 902

CONTACT_A, CONTACT_B = 11, 12
ACCOUNT_A, ACCOUNT_B = 1, 2
PROPERTY_A, PROPERTY_B = 101, 102
PROPERTY_A_UNLINKED = 103   # agency A, but CONTACT_A is not its owner
GRANT_A, GRANT_B, GRANT_LEGACY = 5001, 5002, 5003
DOCUMENT_A, DOCUMENT_B = 201, 202
VISIT_A, VISIT_B = 301, 302

ALL_AGENCIES = (AGENCY_A, AGENCY_B)


# ---------------------------------------------------------------------------
# Reading the tenant predicate out of a statement.
# ---------------------------------------------------------------------------

_TENANT = re.compile(r"agency_id\s*=\s*%s", re.IGNORECASE)


def names_a_tenant(sql: str) -> bool:
    return bool(_TENANT.search(sql))


def _top_level_or_split(clause: str) -> list[str]:
    """Split on ` OR ` that is not inside parentheses."""
    parts, depth, start = [], 0, 0
    lowered = clause.lower()
    i = 0
    while i < len(clause):
        ch = clause[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and lowered.startswith(" or ", i):
            parts.append(clause[start:i])
            i += 4
            start = i
            continue
        i += 1
    parts.append(clause[start:])
    return parts


def tenant_predicate_is_unconditional(sql: str) -> bool:
    """Every top-level disjunct of the WHERE clause names the tenant.

    The trap this exists for is operator precedence:

        WHERE agency_id=%s AND display_name ILIKE %s OR email ILIKE %s

    reads as `(tenant AND name) OR email`, so a contact of another agency whose
    email matches is returned. The parameters still contain only the caller's
    agency, so a model that filters by parameters cannot see this - only the
    shape of the clause can.
    """
    match = re.search(r"\bwhere\b(?P<clause>.+?)(\border\s+by\b|\blimit\b|$)", sql, re.I | re.S)
    if not match:
        return False
    return all(_TENANT.search(part) for part in _top_level_or_split(match.group("clause")))


def alias_map(sql: str) -> dict[str, str]:
    """alias or table name -> table, for every source in FROM/JOIN."""
    names: dict[str, str] = {}
    for table, alias in re.findall(
        r"\b(?:from|join)\s+(\w+)(?:\s+(\w+))?", sql, re.IGNORECASE
    ):
        table = table.lower()
        names[table] = table
        alias = (alias or "").lower()
        if alias and alias not in {"on", "where", "join", "order", "limit", "group"}:
            names[alias] = table
    return names


def subject_table(sql: str) -> str | None:
    """The table whose columns the statement projects.

    Read from the projection when it is qualified - `SELECT p.id, ... FROM
    property_contacts pc JOIN properties p` returns properties, not the first
    table in the FROM - so the model answers with the row shape the caller will
    actually receive.
    """
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


def joined_tables(sql: str) -> set[str]:
    return set(alias_map(sql).values())


def constrained_tables(sql: str) -> set[str]:
    """The tables whose agency the statement actually pins.

    A qualified `ct.agency_id=%s` is attributed through the alias; an
    unqualified one to the nearest preceding FROM/JOIN, which is also what
    makes the `IN (SELECT id FROM properties WHERE agency_id=%s)` subquery
    count as pinning properties rather than the outer table.
    """
    names = alias_map(sql)
    sources = [
        (m.start(), m.group(1).lower())
        for m in re.finditer(r"\b(?:from|join)\s+(\w+)", sql, re.IGNORECASE)
    ]
    pinned: set[str] = set()
    for match in re.finditer(r"(?:(\w+)\.)?agency_id\s*=\s*%s", sql, re.IGNORECASE):
        qualifier = (match.group(1) or "").lower()
        if qualifier and qualifier in names:
            pinned.add(names[qualifier])
            continue
        preceding = [t for pos, t in sources if pos < match.start()]
        if preceding:
            pinned.add(preceding[-1])
    return pinned


# ---------------------------------------------------------------------------
# The two-agency world.
# ---------------------------------------------------------------------------

def _row(row_id, agency, **extra):
    return {"id": row_id, "_agencies": (agency,), **extra}


STORE: dict[str, list[dict]] = {
    "contacts": [
        _row(CONTACT_A, AGENCY_A, display_name="Anna Alfa", email="anna@a.test"),
        _row(CONTACT_B, AGENCY_B, display_name="Anna Beta", email="anna@b.test"),
    ],
    "owner_accounts": [
        _row(ACCOUNT_A, AGENCY_A, contact_id=CONTACT_A, status="active"),
        _row(ACCOUNT_B, AGENCY_B, contact_id=CONTACT_B, status="active"),
    ],
    "properties": [
        _row(PROPERTY_A, AGENCY_A, code="PA", title="Casa A", address="Via A", city="A"),
        _row(PROPERTY_B, AGENCY_B, code="PB", title="Casa B", address="Via B", city="B"),
        _row(PROPERTY_A_UNLINKED, AGENCY_A, code="PA2", title="Casa A2",
             address="Via A2", city="A"),
    ],
    "owner_property_access": [
        # id, account root agency, property root agency
        {"id": GRANT_A, "_account_agency": AGENCY_A, "_property_agency": AGENCY_A,
         "owner_account_id": ACCOUNT_A, "property_id": PROPERTY_A, "access_status": "active"},
        {"id": GRANT_B, "_account_agency": AGENCY_B, "_property_agency": AGENCY_B,
         "owner_account_id": ACCOUNT_B, "property_id": PROPERTY_B, "access_status": "active"},
        # The legacy grant `create_access` can no longer create. Its two roots
        # disagree, so it belongs to neither agency and must appear in neither
        # listing.
        {"id": GRANT_LEGACY, "_account_agency": AGENCY_A, "_property_agency": AGENCY_B,
         "owner_account_id": ACCOUNT_A, "property_id": PROPERTY_B, "access_status": "active"},
    ],
    "property_documents": [
        _row(DOCUMENT_A, AGENCY_A, title="APE A", document_type="ape",
             status="available", expires_at=None),
        _row(DOCUMENT_B, AGENCY_B, title="APE B", document_type="ape",
             status="available", expires_at=None),
    ],
    "property_visits": [
        _row(VISIT_A, AGENCY_A, scheduled_at="2026-01-01", status="done"),
        _row(VISIT_B, AGENCY_B, scheduled_at="2026-01-02", status="done"),
    ],
    "property_contacts": [
        {"id": 401, "_agencies": (AGENCY_A,), "contact_id": CONTACT_A,
         "property_id": PROPERTY_A, "role": "owner"},
        {"id": 402, "_agencies": (AGENCY_B,), "contact_id": CONTACT_B,
         "property_id": PROPERTY_B, "role": "owner"},
    ],
}

# id -> the agencies that entity belongs to, for every entity the model knows.
ENTITY_AGENCIES: dict[int, tuple[int, ...]] = {}
for _table, _rows in STORE.items():
    for _r in _rows:
        if "_agencies" in _r:
            ENTITY_AGENCIES[_r["id"]] = _r["_agencies"]
        else:
            ENTITY_AGENCIES[_r["id"]] = (_r["_account_agency"], _r["_property_agency"])


def _public(row: dict) -> dict:
    return {k: v for k, v in row.items() if not k.startswith("_")}


class Cursor:
    """Answers by tenant, the way the real joins would."""

    def __init__(self, recorder: "Recorder"):
        self.recorder = recorder
        self._rows: list[dict] = []

    def execute(self, sql, params=None):
        flat = " ".join(str(sql).split())
        values = list(params or ())
        self.recorder.statements.append((flat, tuple(values)))

        table = subject_table(flat)
        if table not in STORE:
            raise AssertionError(f"the model knows no table for: {flat}")

        asked_agencies = {v for v in values if v in ALL_AGENCIES}
        joined = joined_tables(flat)
        tenant_named = names_a_tenant(flat)

        # A statement that names no tenant sees the whole platform. That is the
        # defect this suite exists for, and it must be visible, not hidden.
        visible = asked_agencies if tenant_named else set(ALL_AGENCIES)

        # Every entity the statement names must reach an agency the statement
        # also names - otherwise the join to the tenant drops the row. This is
        # what refuses `account of A` + `property of B`.
        for value in values:
            if value in ENTITY_AGENCIES and tenant_named:
                if not set(ENTITY_AGENCIES[value]) & visible:
                    self._rows = []
                    return

        if self._owner_link_missing(flat, values):
            self._rows = []
            return

        # `SELECT p... FROM property_contacts pc JOIN properties p` returns
        # only the properties this contact owns, not every property of the
        # agency. Modelled so a listing that dropped the link would be visible.
        owned: set | None = None
        if table == "properties" and "property_contacts" in joined:
            contacts_named = {v for v in values if v in {r["id"] for r in STORE["contacts"]}}
            owned = {
                pc["property_id"]
                for pc in STORE["property_contacts"]
                if pc["contact_id"] in contacts_named and pc["role"] == "owner"
            }

        rows = []
        for row in STORE[table]:
            if owned is not None and row["id"] not in owned:
                continue
            if "_agencies" in row:
                if not set(row["_agencies"]) <= visible:
                    continue
            else:
                # Two roots. Each is required only if the statement joins it.
                if {"contacts", "owner_accounts"} & joined:
                    if row["_account_agency"] not in visible:
                        continue
                if "properties" in joined:
                    if row["_property_agency"] not in visible:
                        continue
                if not ({"contacts", "owner_accounts"} & joined) and "properties" not in joined:
                    pass          # unscoped: the row is visible, which is the leak
            asked_ids = {v for v in values if v in ENTITY_AGENCIES}
            if asked_ids and row["id"] in ENTITY_AGENCIES and row["id"] not in asked_ids:
                # An id-selective statement returns only what it asked for,
                # unless the ids named belong to other tables.
                if any(i in {r["id"] for r in STORE[table]} for i in asked_ids):
                    continue
            rows.append(_public(row))
        self._rows = rows

    def _owner_link_missing(self, flat: str, values: list) -> bool:
        """The eligibility guard also requires a property_contacts row.

        Modelled because it is the predicate that does most of the work: the
        role must be `owner`, and 036's trigger already keeps a
        property_contacts row inside one agency. Without this the guard would
        appear to be refused by the tenant predicates when the real query is
        refused by the link.
        """
        if subject_table(flat) != "owner_accounts" or "property_contacts" not in joined_tables(flat):
            return False
        accounts = {r["id"] for r in STORE["owner_accounts"]}
        properties = {r["id"] for r in STORE["properties"]}
        account_id = next((v for v in values if v in accounts), None)
        property_id = next((v for v in values if v in properties), None)
        account = next((r for r in STORE["owner_accounts"] if r["id"] == account_id), None)
        if account is None or property_id is None:
            return True
        return not any(
            pc["contact_id"] == account["contact_id"]
            and pc["property_id"] == property_id
            and pc["role"] == "owner"
            for pc in STORE["property_contacts"]
        )

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def close(self):
        pass


class Recorder:
    def __init__(self):
        self.statements: list[tuple[str, tuple]] = []

    def agencies_seen(self) -> set:
        return {v for _, params in self.statements for v in params if v in ALL_AGENCIES}

    def unscoped(self) -> list[str]:
        return [s for s, _ in self.statements if not names_a_tenant(s)]

    def not_unconditional(self) -> list[str]:
        return [s for s, _ in self.statements if not tenant_predicate_is_unconditional(s)]


@pytest.fixture
def db(monkeypatch):
    recorder = Recorder()

    @contextmanager
    def fake_core_cursor(*, commit: bool = False):
        cursor = Cursor(recorder)
        try:
            yield object(), cursor
        finally:
            cursor.close()

    monkeypatch.setattr(repository, "core_cursor", fake_core_cursor)
    monkeypatch.setattr(lookups, "core_cursor", fake_core_cursor)
    return recorder


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
        app.dependency_overrides[basic_only_agency_context] = lambda: context(agency_id)
        return TestClient(app)

    return use


AUTH = ("giorgio", "test-secret")


# The six calls under test, as (name, callable taking the agency).
READS = {
    "list_accounts": lambda a: repository.list_accounts(a),
    "list_access": lambda a: repository.list_access(a),
    "lookup_contacts": lambda a: lookups.lookup_contacts(a, "anna", 50),
    "lookup_account_properties": lambda a: lookups.lookup_account_properties(a, ACCOUNT_A if a == AGENCY_A else ACCOUNT_B),
    "lookup_property_documents": lambda a: lookups.lookup_property_documents(
        a, ACCOUNT_A if a == AGENCY_A else ACCOUNT_B, PROPERTY_A if a == AGENCY_A else PROPERTY_B),
    "lookup_property_visits": lambda a: lookups.lookup_property_visits(
        a, ACCOUNT_A if a == AGENCY_A else ACCOUNT_B, PROPERTY_A if a == AGENCY_A else PROPERTY_B),
}


# ---------------------------------------------------------------------------
# 1-4 - every statement is scoped, in both directions
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(READS))
@pytest.mark.parametrize("agency", ALL_AGENCIES)
def test_1_every_statement_names_the_callers_tenant(db, name, agency):
    READS[name](agency)
    assert db.statements, f"{name} issued no statement"
    assert db.unscoped() == [], f"{name} has a statement with no tenant predicate"
    assert db.agencies_seen() == {agency}, (name, db.agencies_seen())


@pytest.mark.parametrize("name", sorted(READS))
def test_2_the_tenant_predicate_survives_an_or(db, name):
    """`WHERE agency_id=%s AND x OR y` is `(tenant AND x) OR y`.

    Parameter-level checks cannot see this - the parameters are still only the
    caller's agency - so the clause itself is read.
    """
    READS[name](AGENCY_A)
    assert db.not_unconditional() == [], db.not_unconditional()


def test_3_agency_a_sees_only_agency_a(db):
    assert [r["id"] for r in repository.list_accounts(AGENCY_A)] == [ACCOUNT_A]
    db.statements.clear()
    assert [r["id"] for r in lookups.lookup_contacts(AGENCY_A, "anna", 50)] == [CONTACT_A]
    db.statements.clear()
    assert [r["id"] for r in lookups.lookup_account_properties(AGENCY_A, ACCOUNT_A)] == [PROPERTY_A]
    db.statements.clear()
    assert [r["id"] for r in lookups.lookup_property_documents(AGENCY_A, ACCOUNT_A, PROPERTY_A)] == [DOCUMENT_A]
    db.statements.clear()
    assert [r["id"] for r in lookups.lookup_property_visits(AGENCY_A, ACCOUNT_A, PROPERTY_A)] == [VISIT_A]


def test_4_agency_b_sees_only_agency_b(db):
    """The mirror, so the rule is not "agency A is special"."""
    assert [r["id"] for r in repository.list_accounts(AGENCY_B)] == [ACCOUNT_B]
    db.statements.clear()
    assert [r["id"] for r in lookups.lookup_contacts(AGENCY_B, "anna", 50)] == [CONTACT_B]
    db.statements.clear()
    assert [r["id"] for r in lookups.lookup_property_documents(AGENCY_B, ACCOUNT_B, PROPERTY_B)] == [DOCUMENT_B]


# ---------------------------------------------------------------------------
# 5-7 - list_access filters BOTH roots
# ---------------------------------------------------------------------------

def test_5_list_access_returns_only_the_grant_of_this_agency(db):
    assert [r["id"] for r in repository.list_access(AGENCY_A)] == [GRANT_A]
    db.statements.clear()
    assert [r["id"] for r in repository.list_access(AGENCY_B)] == [GRANT_B]


def test_6_an_incoherent_legacy_grant_belongs_to_neither_agency(db):
    """`create_access` can no longer produce it; rows already stored can.

    Its account is of A and its property is of B, so requiring both roots hides
    it from both listings. Filtering only one root would show it to one of them
    - which is how a cross-agency property would become visible in an admin UI.
    """
    for agency in ALL_AGENCIES:
        db.statements.clear()
        assert GRANT_LEGACY not in [r["id"] for r in repository.list_access(agency)]


def test_7_list_access_joins_both_roots(db):
    repository.list_access(AGENCY_A)
    sql = db.statements[0][0].lower()
    assert "owner_accounts" in sql and "contacts" in sql, sql
    assert "properties" in sql, sql
    assert sql.count("agency_id=%s") == 2, sql
    assert db.statements[0][1].count(AGENCY_A) == 2, db.statements[0][1]


# ---------------------------------------------------------------------------
# 8-10 - cross-agency entities are refused, neutrally
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("call", [
    lambda: lookups.lookup_account_properties(AGENCY_A, ACCOUNT_B),
    lambda: lookups.lookup_property_documents(AGENCY_A, ACCOUNT_B, PROPERTY_B),
    lambda: lookups.lookup_property_visits(AGENCY_A, ACCOUNT_B, PROPERTY_B),
    lambda: lookups.lookup_property_documents(AGENCY_A, ACCOUNT_A, PROPERTY_B),
    lambda: lookups.lookup_property_visits(AGENCY_A, ACCOUNT_A, PROPERTY_B),
])
def test_8_an_entity_of_another_agency_is_not_found(db, call):
    with pytest.raises(NotFoundError) as excinfo:
        call()
    assert str(excinfo.value) == "Risorsa non trovata"


def test_9_the_refusal_happens_before_the_source_data_is_read(db):
    """The eligibility guard runs first and nothing else follows it."""
    with pytest.raises(NotFoundError):
        lookups.lookup_property_documents(AGENCY_A, ACCOUNT_A, PROPERTY_B)
    assert len(db.statements) == 1, [s for s, _ in db.statements]
    assert "property_documents" not in db.statements[0][0].lower()


def test_10_the_eligibility_guard_pins_both_agencies(db):
    """Both predicates, asserted on the statement rather than through the model.

    Honest about why: 036 installs `trg_property_contacts_agency_integrity`, so
    a property_contacts row cannot already cross agencies - which means pinning
    the contact's agency implies the property's, and either predicate alone
    still refuses every case this suite can construct. They are defence in
    depth against that trigger being weakened, and the only way to keep them is
    to read the statement. A behavioural test would pass with one of them gone.
    """
    lookups.lookup_property_documents(AGENCY_A, ACCOUNT_A, PROPERTY_A)
    guard = db.statements[0][0].lower()
    assert "owner_accounts" in guard and "contacts" in guard, guard
    assert "properties" in guard and "property_contacts" in guard, guard
    assert "pc.role=%s" in guard, guard
    assert constrained_tables(guard) >= {"contacts", "properties"}, constrained_tables(guard)
    assert guard.count("agency_id=%s") == 2, guard


def test_10b_the_guard_still_requires_the_owner_link(db):
    """A property of the caller's own agency that this contact does not own."""
    with pytest.raises(NotFoundError):
        lookups.lookup_property_documents(AGENCY_A, ACCOUNT_A, PROPERTY_A_UNLINKED)
    assert len(db.statements) == 1


# ---------------------------------------------------------------------------
# 11-13 - the HTTP surface
# ---------------------------------------------------------------------------

def test_11_the_routes_return_only_the_callers_agency(db, client):
    a = client(AGENCY_A)
    assert [item["id"] for item in a.get("/api/owner/admin/accounts", auth=AUTH).json()["items"]] == [ACCOUNT_A]
    assert [item["id"] for item in a.get("/api/owner/admin/access", auth=AUTH).json()["items"]] == [GRANT_A]
    contacts = a.get("/api/owner/admin/lookups/contacts", params={"search": "anna"}, auth=AUTH)
    assert [item["id"] for item in contacts.json()["items"]] == [CONTACT_A]
    props = a.get(f"/api/owner/admin/lookups/accounts/{ACCOUNT_A}/properties", auth=AUTH)
    assert [item["id"] for item in props.json()["items"]] == [PROPERTY_A]
    assert db.agencies_seen() == {AGENCY_A}


def test_12_asking_for_another_agencys_account_over_http_is_a_404(db, client):
    a = client(AGENCY_A)
    for path in (
        f"/api/owner/admin/lookups/accounts/{ACCOUNT_B}/properties",
        f"/api/owner/admin/lookups/accounts/{ACCOUNT_B}/properties/{PROPERTY_B}/documents",
        f"/api/owner/admin/lookups/accounts/{ACCOUNT_A}/properties/{PROPERTY_B}/visits",
    ):
        response = a.get(path, auth=AUTH)
        assert response.status_code == 404, (path, response.status_code)
        assert response.json() == {"detail": "Risorsa non trovata"}


def test_13_no_client_parameter_can_change_the_agency(db, client):
    """`agency_id` in the query string, and an id from the other agency in the
    path, must both be inert: the tenant comes from the dependency."""
    a = client(AGENCY_A)
    response = a.get(
        "/api/owner/admin/accounts",
        params={"agency_id": AGENCY_B, "agency": AGENCY_B},
        auth=AUTH,
    )
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [ACCOUNT_A]
    assert AGENCY_B not in db.agencies_seen()
    assert all(AGENCY_B not in params for _, params in db.statements)


# ---------------------------------------------------------------------------
# 14-15 - a context with no agency
# ---------------------------------------------------------------------------

def test_14_a_context_without_an_agency_is_refused_before_any_query(db, client):
    unbound = client(None)
    for path in (
        "/api/owner/admin/accounts",
        "/api/owner/admin/access",
        "/api/owner/admin/lookups/contacts",
        f"/api/owner/admin/lookups/accounts/{ACCOUNT_A}/properties",
        f"/api/owner/admin/lookups/accounts/{ACCOUNT_A}/properties/{PROPERTY_A}/documents",
        f"/api/owner/admin/lookups/accounts/{ACCOUNT_A}/properties/{PROPERTY_A}/visits",
    ):
        response = unbound.get(path, auth=AUTH)
        assert response.status_code == 403, (path, response.status_code, response.text)
    assert db.statements == [], "an unbound context reached the database"


def test_15_the_repository_functions_require_an_agency_positionally(db):
    """No default, and first: a caller that forgets the tenant gets a
    TypeError, not an unfiltered query."""
    import inspect

    for fn in (repository.list_accounts, repository.list_access,
               lookups.lookup_contacts, lookups.lookup_account_properties,
               lookups.lookup_property_documents, lookups.lookup_property_visits):
        parameters = list(inspect.signature(fn).parameters.values())
        assert parameters[0].name == "agency_id", (fn.__name__, parameters[0].name)
        assert parameters[0].default is inspect.Parameter.empty, fn.__name__

    guard = list(inspect.signature(lookups._ensure_owner_eligible_property).parameters.values())
    assert [p.name for p in guard[:2]] == ["cur", "agency_id"], [p.name for p in guard]
    assert guard[1].default is inspect.Parameter.empty


# ---------------------------------------------------------------------------
# 16-18 - what this patch must not have moved
# ---------------------------------------------------------------------------

def test_16_the_mount_still_authenticates_with_require_owner_admin():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "owner" / "router_admin.py").read_text(encoding="utf-8")
    assert "dependencies=[Depends(require_owner_admin)]" in source
    assert "def require_owner_admin(" in source
    assert 'realm="STIMA360 OWNER Admin"' in source


def test_17_flow_no_longer_borrows_owners_admin_dependency():
    """P26-3 decoupled them, and that is what this now asserts.

    FLOW borrowed `require_owner_admin` because it needed a self-authenticating
    mount and that was the nearest one. It meant a change to OWNER Admin's
    authentication silently changed FLOW's. P26-3 gave FLOW
    `require_authenticated_operator` - the same legacy credential, plus the
    operator session the OS Shell now carries - and the import is gone.
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "flow" / "router.py").read_text(encoding="utf-8")
    assert "require_owner_admin" not in source
    assert "dependencies=[Depends(require_authenticated_operator)]" in source

    # OWNER Admin's own mount is untouched by that move.
    admin = (Path(__file__).resolve().parents[1] / "owner" / "router_admin.py").read_text(encoding="utf-8")
    assert "dependencies=[Depends(require_owner_admin)]" in admin


def test_18_anonymous_is_still_refused_before_the_agency_is_resolved(monkeypatch, db):
    """The mount-level Basic check runs first, so an unauthenticated request
    never reaches `basic_only_agency_context` and never opens a cursor."""
    monkeypatch.setenv("ADMIN_USER", "giorgio")
    monkeypatch.setenv("ADMIN_PASS", "test-secret")
    app = FastAPI()
    app.include_router(admin_router)
    response = TestClient(app).get("/api/owner/admin/accounts")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == 'Basic realm="STIMA360 OWNER Admin"'
    assert db.statements == []


# ---------------------------------------------------------------------------
# 19 - the six routes declare the dependency, and only they
# ---------------------------------------------------------------------------

def test_19_the_six_read_routes_take_the_agency_context():
    """Admission by AST, so the list cannot drift from the code.

    This slice's six. The write slice adds six more and pins the full set in
    tests/test_p26_6c_owner_admin_writes.py::test_15; here the concern is only
    that none of these six loses its context.
    """
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    scoped = set()
    for module in ("owner/router_admin.py", "owner/router_admin_lookups.py"):
        tree = ast.parse((root / module).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                takes_ctx = any(
                    isinstance(default, ast.Call)
                    and getattr(default.func, "id", None) == "Depends"
                    and getattr(default.args[0], "id", None) == "basic_only_agency_context"
                    for default in node.args.defaults
                    if isinstance(default, ast.Call) and default.args
                )
                if takes_ctx:
                    scoped.add(node.name)

    assert scoped >= {
        "accounts", "access",                       # router_admin
        "contacts", "account_properties",           # lookups
        "property_documents", "property_visits",
    }, sorted(scoped)
