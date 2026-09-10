"""P26-6C OWNER Admin - dashboard, publications, feedback and audit.

BLOCK A of the remaining OWNER Admin surface:

    GET   /dashboard
    GET   /publications      POST /publications
    PATCH /publications/{i}  POST /publications/{i}/publish
    POST  /publications/{i}/archive
    POST  /publications/{i}/supersede
    GET   /feedback          PATCH /feedback/{i}
    GET   /audit

Publications reach an agency through their property; feedback through both
roots; the audit log through whichever parent survives, because both of its
parents are nullable ON DELETE SET NULL and a row can outlive them both.

TWO THINGS THIS BLOCK GETS RIGHT THAT ARE EASY TO GET WRONG

The audit view. A row with both parents gone belongs to no agency and appears
in no agency's view. That is a consequence of the schema, not a choice made
here, and it is why the admin audit is not a complete history.

Notifications. `_emit_notification_event` fans a publication out to every
account holding an active grant on the property. A grant written before
`create_access` started checking can link an account of one agency to a
property of another, so without a join requiring the two roots to agree,
publishing on a property of A would notify an owner of B. The agency is not a
parameter there: it is the property's own, so no caller can widen it.

WHAT THE MODEL PROVES

Rows carry an agency. A statement that names no tenant is answered with both
agencies' rows, so an unscoped read leaks visibly. Every write and every audit
row is recorded, because "refused" must mean nothing happened.

Nothing here runs PostgreSQL.
"""
from __future__ import annotations

import re
from contextlib import contextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.exceptions import ConflictError, NotFoundError
from operator_auth.context import OperatorContext
from operator_auth.dependencies import basic_only_agency_context
from owner import repository
from owner.router_admin import router as admin_router


AGENCY_A = 901
AGENCY_B = 902
ALL_AGENCIES = (AGENCY_A, AGENCY_B)
AUTH = ("giorgio", "test-secret")

CONTACT_A, CONTACT_B = 11, 12
ACCOUNT_A, ACCOUNT_B = 1, 2
PROPERTY_A, PROPERTY_B = 101, 102
PUB_A_DRAFT, PUB_A_PUBLISHED, PUB_B = 201, 202, 203
FEEDBACK_A, FEEDBACK_B, FEEDBACK_LEGACY = 301, 302, 303
AUDIT_BY_ACCOUNT_A, AUDIT_BY_PROPERTY_B, AUDIT_ORPHAN = 401, 402, 403
SOURCE_DOC_A, SOURCE_DOC_B = 501, 502          # property_documents
SHARED_A, SHARED_B = 601, 602                  # owner_shared_documents
VISIT_A, VISIT_B = 701, 702                    # property_visits
VF_A, VF_B = 801, 802                          # owner_visit_feedback_publications
READ_A, READ_CROSS = 901001, 901002            # owner_document_reads

MISSING = 999

_TENANT = re.compile(r"agency_id\s*=\s*%s", re.IGNORECASE)


def _top_level_or_split(clause: str) -> list[str]:
    parts, depth, start, i = [], 0, 0, 0
    lowered = clause.lower()
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

    `WHERE tenant AND x OR y` is `(tenant AND x) OR y`, and the parameters look
    innocent either way - only the shape of the clause shows it. The audit
    query is a deliberate top-level OR and passes because BOTH sides name the
    tenant.
    """
    match = re.search(r"\bwhere\b(?P<clause>.+?)(\border\s+by\b|\blimit\b|\)\s*\w*\s*(,|$)|$)",
                      sql, re.I | re.S)
    if not match:
        return False
    return all(_TENANT.search(part) for part in _top_level_or_split(match.group("clause")))


def from_table(sql: str) -> str | None:
    lowered = sql.lower()
    for verb in ("insert into", "update"):
        if lowered.startswith(verb):
            found = re.match(rf"{verb}\s+(\w+)", lowered)
            return found.group(1) if found else None
    found = re.search(r"\bfrom\s+(\w+)", lowered)
    return found.group(1) if found else None


def joined(sql: str) -> set[str]:
    return {t.lower() for t in re.findall(r"\b(?:from|join)\s+(\w+)", sql, re.IGNORECASE)}


def alias_map(sql: str) -> dict[str, str]:
    names: dict[str, str] = {}
    for table, alias in re.findall(r"\b(?:from|join)\s+(\w+)(?:\s+(\w+))?", sql, re.IGNORECASE):
        table = table.lower()
        names[table] = table
        alias = (alias or "").lower()
        if alias and alias not in {"on", "where", "join", "order", "limit", "group", "set"}:
            names[alias] = table
    return names


def constrained_tables(sql: str) -> set[str]:
    """The tables whose agency the statement actually pins.

    Read from the statement rather than assumed, so dropping one of two root
    predicates changes the answer. A model that enforced "both roots" itself
    would hide exactly that mistake - and did, until this was added.
    """
    names = alias_map(sql)
    sources = [(m.start(), m.group(1).lower())
               for m in re.finditer(r"\b(?:from|join)\s+(\w+)", sql, re.IGNORECASE)]
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
# The two-agency world. `_agencies` is every agency a row must reach.
# ---------------------------------------------------------------------------

STORE: dict[str, list[dict]] = {
    "owner_publications": [
        {"id": PUB_A_DRAFT, "_agencies": (AGENCY_A,), "property_id": PROPERTY_A,
         "status": "draft", "version_number": 1, "title": "Bozza A"},
        {"id": PUB_A_PUBLISHED, "_agencies": (AGENCY_A,), "property_id": PROPERTY_A,
         "status": "published", "version_number": 1, "title": "Pubblicata A"},
        {"id": PUB_B, "_agencies": (AGENCY_B,), "property_id": PROPERTY_B,
         "status": "published", "version_number": 1, "title": "Pubblicata B"},
    ],
    "owner_feedback": [
        # Two roots, kept apart, so which of them a statement requires is read
        # from the statement and not decided by the model.
        {"id": FEEDBACK_A, "_account_agency": AGENCY_A, "_property_agency": AGENCY_A,
         "owner_account_id": ACCOUNT_A, "property_id": PROPERTY_A,
         "status": "new", "handled_at": None},
        {"id": FEEDBACK_B, "_account_agency": AGENCY_B, "_property_agency": AGENCY_B,
         "owner_account_id": ACCOUNT_B, "property_id": PROPERTY_B,
         "status": "new", "handled_at": None},
        # account of A, property of B: belongs to neither, but only a statement
        # that requires BOTH roots can tell.
        {"id": FEEDBACK_LEGACY, "_account_agency": AGENCY_A, "_property_agency": AGENCY_B,
         "owner_account_id": ACCOUNT_A, "property_id": PROPERTY_B,
         "status": "new", "handled_at": None},
    ],
    "owner_audit_log": [
        {"id": AUDIT_BY_ACCOUNT_A, "_any": (AGENCY_A,), "owner_account_id": ACCOUNT_A,
         "property_id": None, "action": "account_created"},
        {"id": AUDIT_BY_PROPERTY_B, "_any": (AGENCY_B,), "owner_account_id": None,
         "property_id": PROPERTY_B, "action": "publication_published"},
        # Both parents gone. In no agency's view, by construction.
        {"id": AUDIT_ORPHAN, "_any": (), "owner_account_id": None,
         "property_id": None, "action": "login_succeeded"},
    ],
    "properties": [
        {"id": PROPERTY_A, "_agencies": (AGENCY_A,)},
        {"id": PROPERTY_B, "_agencies": (AGENCY_B,)},
    ],
    # --- blocks B and C -----------------------------------------------------
    "property_documents": [
        {"id": SOURCE_DOC_A, "_agencies": (AGENCY_A,), "property_id": PROPERTY_A,
         "status": "available", "storage_key": "a/1", "url": None, "metadata": {},
         "document_type": "ape", "title": "APE A", "expires_at": None,
         "created_at": None, "updated_at": None},
        {"id": SOURCE_DOC_B, "_agencies": (AGENCY_B,), "property_id": PROPERTY_B,
         "status": "available", "storage_key": "b/1", "url": None, "metadata": {},
         "document_type": "ape", "title": "APE B", "expires_at": None,
         "created_at": None, "updated_at": None},
    ],
    "owner_shared_documents": [
        {"id": SHARED_A, "_agencies": (AGENCY_A,), "property_document_id": SOURCE_DOC_A,
         "property_id": PROPERTY_A, "owner_account_id": None, "public_title": "A",
         "public_document_type": "ape", "version_number": 1, "status": "draft",
         "published_at": None, "expires_at": None, "acknowledgement_required": False,
         "supersedes_shared_document_id": None, "superseded_by_shared_document_id": None,
         "revoked_at": None, "revoked_by": None, "created_at": None, "updated_at": None,
         "created_by": None, "archived_at": None, "source_title": "APE A",
         "source_document_type": "ape", "source_status": "available",
         "source_expires_at": None, "storage_key": "a/1", "url": None,
         "source_metadata": {}},
        {"id": SHARED_B, "_agencies": (AGENCY_B,), "property_document_id": SOURCE_DOC_B,
         "property_id": PROPERTY_B, "owner_account_id": None, "public_title": "B",
         "public_document_type": "ape", "version_number": 1, "status": "published",
         "published_at": None, "expires_at": None, "acknowledgement_required": False,
         "supersedes_shared_document_id": None, "superseded_by_shared_document_id": None,
         "revoked_at": None, "revoked_by": None, "created_at": None, "updated_at": None,
         "created_by": None, "archived_at": None, "source_title": "APE B",
         "source_document_type": "ape", "source_status": "available",
         "source_expires_at": None, "storage_key": "b/1", "url": None,
         "source_metadata": {}},
    ],
    "property_visits": [
        {"id": VISIT_A, "_agencies": (AGENCY_A,), "property_id": PROPERTY_A,
         "scheduled_at": None, "status": "done"},
        {"id": VISIT_B, "_agencies": (AGENCY_B,), "property_id": PROPERTY_B,
         "scheduled_at": None, "status": "done"},
    ],
    "owner_visit_feedback_publications": [
        {"id": VF_A, "_agencies": (AGENCY_A,), "property_visit_id": VISIT_A,
         "property_id": PROPERTY_A, "owner_account_id": None, "category": "price",
         "public_summary": "ok", "sentiment": None, "version_number": 1,
         "status": "draft", "published_at": None,
         "supersedes_feedback_publication_id": None,
         "superseded_by_feedback_publication_id": None, "created_at": None,
         "updated_at": None, "created_by": None, "archived_at": None},
        {"id": VF_B, "_agencies": (AGENCY_B,), "property_visit_id": VISIT_B,
         "property_id": PROPERTY_B, "owner_account_id": None, "category": "price",
         "public_summary": "ok", "sentiment": None, "version_number": 1,
         "status": "published", "published_at": None,
         "supersedes_feedback_publication_id": None,
         "superseded_by_feedback_publication_id": None, "created_at": None,
         "updated_at": None, "created_by": None, "archived_at": None},
    ],
    "owner_document_reads": [
        # A read of A's document by A's own owner.
        {"id": READ_A, "_account_agency": AGENCY_A, "_property_agency": AGENCY_A,
         "owner_account_id": ACCOUNT_A, "first_viewed_at": None, "last_viewed_at": None,
         "view_count": 1, "acknowledged_at": None},
        # A read of the SAME document recorded against an account of B - only
        # reachable through a legacy grant whose two roots disagree, and exactly
        # what the second root in the reads query is there to hide. The document
        # itself is A's, so the guard on the document cannot catch this one.
        {"id": READ_CROSS, "_account_agency": AGENCY_B, "_property_agency": AGENCY_A,
         "owner_account_id": ACCOUNT_B, "first_viewed_at": None, "last_viewed_at": None,
         "view_count": 1, "acknowledged_at": None},
    ],
}

ENTITY_AGENCIES = {
    PROPERTY_A: (AGENCY_A,), PROPERTY_B: (AGENCY_B,),
    ACCOUNT_A: (AGENCY_A,), ACCOUNT_B: (AGENCY_B,),
    CONTACT_A: (AGENCY_A,), CONTACT_B: (AGENCY_B,),
    PUB_A_DRAFT: (AGENCY_A,), PUB_A_PUBLISHED: (AGENCY_A,), PUB_B: (AGENCY_B,),
    FEEDBACK_A: (AGENCY_A,), FEEDBACK_B: (AGENCY_B,),
    SOURCE_DOC_A: (AGENCY_A,), SOURCE_DOC_B: (AGENCY_B,),
    SHARED_A: (AGENCY_A,), SHARED_B: (AGENCY_B,),
    VISIT_A: (AGENCY_A,), VISIT_B: (AGENCY_B,),
    VF_A: (AGENCY_A,), VF_B: (AGENCY_B,),
}


def _public(row: dict) -> dict:
    return {k: v for k, v in row.items() if not k.startswith("_")}


class Statement:
    __slots__ = ("sql", "params", "connection", "transaction")

    def __init__(self, sql, params, connection, transaction):
        self.sql, self.params = sql, params
        self.connection, self.transaction = connection, transaction

    @property
    def verb(self) -> str:
        return self.sql.split(None, 1)[0].lower()

    @property
    def table(self) -> str | None:
        return from_table(self.sql)


class World:
    def __init__(self):
        self.statements: list[Statement] = []
        self.writes: list[Statement] = []
        self.audit: list[dict] = []
        self.commits = self.rollbacks = 0
        self._connections = self._transactions = 0

    def selects(self) -> list[Statement]:
        return [s for s in self.statements if s.verb == "select"]

    def agencies_seen(self) -> set:
        return {v for s in self.statements for v in s.params if v in ALL_AGENCIES}

    def unscoped(self) -> list[str]:
        return [s.sql for s in self.selects() if not _TENANT.search(s.sql)]


class Connection:
    def __init__(self, world):
        self.world = world
        world._connections += 1
        self.id = world._connections
        self.transaction = None

    def begin_if_needed(self):
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
    def __init__(self, world, connection):
        self.world, self.connection = world, connection
        self._rows: list[dict] = []
        self._next = 9000

    def execute(self, sql, params=None):
        flat = " ".join(str(sql).split())
        values = list(params or ())
        self.world.statements.append(
            Statement(flat, tuple(values), self.connection.id, self.connection.begin_if_needed())
        )
        lowered = flat.lower()

        if lowered.startswith("insert into owner_audit_log"):
            keys = ("owner_account_id", "property_id", "action", "entity_type",
                    "entity_id", "result", "metadata")
            self.world.audit.append(dict(zip(keys, values + [None] * len(keys))))
            self._rows = []
            return
        if lowered.startswith(("insert", "update")) or " insert into owner_notifications" in lowered:
            self.world.writes.append(self.world.statements[-1])
            self._next += 1
            self._rows = [{"id": self._next, "property_id": PROPERTY_A, "status": "draft",
                           "version_number": 1, "owner_account_id": ACCOUNT_A,
                           "public_response": None, "title": "x"}]
            return
        if lowered.startswith("with eligible as"):
            # the notification fan-out; it writes and returns nothing
            self.world.writes.append(self.world.statements[-1])
            self._rows = []
            return

        self._rows = self._read(flat, values)

    def _read(self, flat, values):
        table = from_table(flat)
        pinned = bool(_TENANT.search(flat))
        asked = {v for v in values if v in ALL_AGENCIES}
        visible = asked if pinned else set(ALL_AGENCIES)

        if flat.lower().startswith("select (select count"):
            return [self._counters(visible)]

        if table not in STORE:
            raise AssertionError(f"the model was asked something it does not know: {flat}")

        # Every entity the statement names must reach an agency it also names.
        if pinned:
            for value in values:
                if value in ENTITY_AGENCIES and not set(ENTITY_AGENCIES[value]) & visible:
                    return []

        # Which rows OF THIS TABLE the statement asked for, independently of
        # ENTITY_AGENCIES: a row whose tenancy the model deliberately does not
        # summarise must still be selectable by id.
        own_ids = {r["id"] for r in STORE[table]}
        named = {v for v in values if v in own_ids}
        rows = []
        for row in STORE[table]:
            if named and row["id"] not in named:
                continue
            if "_any" in row:
                # the audit log: whichever parent survives decides
                if pinned and not set(row["_any"]) & visible:
                    continue
            elif "_account_agency" in row:
                # Two roots. Each is required only if the statement pins it.
                constrained = constrained_tables(flat)
                if "contacts" in constrained and row["_account_agency"] not in visible:
                    continue
                if "properties" in constrained and row["_property_agency"] not in visible:
                    continue
                if not constrained:
                    pass          # unscoped: the row shows, which is the leak
            elif pinned and not set(row["_agencies"]) <= visible:
                continue
            rows.append(_public(row))
        return rows

    def _counters(self, visible):
        publications = [r for r in STORE["owner_publications"]
                        if r["status"] == "published" and set(r["_agencies"]) <= visible]
        feedback = [r for r in STORE["owner_feedback"]
                    if r["status"] == "new"
                    and r["_account_agency"] in visible
                    and r["_property_agency"] in visible]
        return {"active_accounts": len(visible), "active_access": len(visible),
                "published": len(publications), "new_feedback": len(feedback)}

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def close(self):
        pass


@pytest.fixture
def db(monkeypatch):
    world = World()

    @contextmanager
    def fake_core_cursor(*, commit: bool = False):
        connection = Connection(world)
        cursor = Cursor(world, connection)
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
    return world


def context(agency_id):
    return OperatorContext(
        user_id=None, agency_id=agency_id, role="agency_owner",
        is_platform_admin=agency_id is None, session_id=None, auth_channel="legacy_basic",
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


READS = {
    "dashboard": lambda a: repository.dashboard(a),
    "audits": lambda a: repository.audits(a),
    "list_publications": lambda a: repository.list_publications(a),
    "admin_list_feedback": lambda a: repository.admin_list_feedback(a),
}


# ---------------------------------------------------------------------------
# 1-4 - the listings
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(READS))
@pytest.mark.parametrize("agency", ALL_AGENCIES)
def test_1_every_read_names_the_callers_tenant(db, name, agency):
    READS[name](agency)
    assert db.statements
    assert db.unscoped() == [], (name, db.unscoped())
    assert db.agencies_seen() == {agency}, (name, db.agencies_seen())


@pytest.mark.parametrize("name", sorted(READS))
def test_2_the_tenant_predicate_survives_an_or(db, name):
    READS[name](AGENCY_A)
    bad = [s.sql for s in db.selects() if not tenant_predicate_is_unconditional(s.sql)]
    assert bad == [], bad


def test_3_publications_and_feedback_show_only_this_agency(db):
    assert [r["id"] for r in repository.list_publications(AGENCY_A)] == [PUB_A_DRAFT, PUB_A_PUBLISHED]
    db.statements.clear()
    assert [r["id"] for r in repository.list_publications(AGENCY_B)] == [PUB_B]
    db.statements.clear()
    assert [r["id"] for r in repository.admin_list_feedback(AGENCY_A)] == [FEEDBACK_A]
    db.statements.clear()
    assert [r["id"] for r in repository.admin_list_feedback(AGENCY_B)] == [FEEDBACK_B]


def test_4_an_incoherent_feedback_row_belongs_to_neither_agency(db):
    """Its account is of A and its property is of B - the same shape as the
    legacy grants list_access hides."""
    for agency in ALL_AGENCIES:
        db.statements.clear()
        assert FEEDBACK_LEGACY not in [r["id"] for r in repository.admin_list_feedback(agency)]


def test_4b_the_feedback_statements_require_both_roots(db):
    """Read from the statements, because a legacy row is hidden by either root
    alone and a behavioural test cannot tell the two apart."""
    repository.admin_list_feedback(AGENCY_A)
    listing = db.statements[0].sql
    assert constrained_tables(listing) >= {"contacts", "properties"}, listing
    db.statements.clear()
    with pytest.raises(NotFoundError):
        repository.update_feedback_status(AGENCY_A, FEEDBACK_B, {"status": "handled"})
    guard = db.statements[0].sql
    assert constrained_tables(guard) >= {"contacts", "properties"}, guard


# ---------------------------------------------------------------------------
# 5-7 - the audit view
# ---------------------------------------------------------------------------

def test_5_the_audit_shows_rows_reachable_by_either_surviving_parent(db):
    assert [r["id"] for r in repository.audits(AGENCY_A)] == [AUDIT_BY_ACCOUNT_A]
    db.statements.clear()
    assert [r["id"] for r in repository.audits(AGENCY_B)] == [AUDIT_BY_PROPERTY_B]


def test_6_an_audit_row_with_both_parents_gone_is_in_no_agencys_view(db):
    """Not an oversight. owner_audit_log's two parents are both nullable ON
    DELETE SET NULL, so such a row has no tenant left to derive - and the admin
    audit is therefore not a complete history. Recorded here so the omission
    reads as a decision."""
    for agency in ALL_AGENCIES:
        db.statements.clear()
        assert AUDIT_ORPHAN not in [r["id"] for r in repository.audits(agency)]


def test_7_the_audit_query_names_the_tenant_on_both_sides_of_its_or(db):
    repository.audits(AGENCY_A)
    sql = db.statements[0].sql.lower()
    assert " or " in sql, sql
    assert sql.count("agency_id=%s") == 2, sql
    assert tenant_predicate_is_unconditional(sql), sql


# ---------------------------------------------------------------------------
# 8-10 - the dashboard
# ---------------------------------------------------------------------------

def test_8_the_dashboard_counters_are_per_agency(db):
    a = repository.dashboard(AGENCY_A)
    assert a["published"] == 1 and a["new_feedback"] == 1
    db.statements.clear()
    b = repository.dashboard(AGENCY_B)
    assert b["published"] == 1 and b["new_feedback"] == 1


def test_9_every_dashboard_counter_carries_a_predicate(db):
    repository.dashboard(AGENCY_A)
    sql = db.statements[0].sql.lower()
    assert sql.count("agency_id=%s") == 6, sql
    assert db.statements[0].params == (AGENCY_A,) * 6


def test_10_the_two_dashboard_counters_that_touch_both_roots_require_both(db):
    """Each counter is sliced out by its own alias and read on its own.

    The alias always closes its subquery, so `) <alias>` is the boundary -
    matching the bare word would find `status='published'` instead.
    """
    repository.dashboard(AGENCY_A)
    sql = db.statements[0].sql.lower()
    aliases = ["active_accounts", "active_access", "published", "new_feedback"]
    bounds = []
    for alias in aliases:
        found = re.search(rf"\)\s+{alias}\b", sql)
        assert found, (alias, sql)
        bounds.append(found.start())
    segments, start = {}, 0
    for alias, end in zip(aliases, bounds):
        segments[alias] = sql[start:end]
        start = end

    # The two that reach both roots require both.
    for alias in ("active_access", "new_feedback"):
        segment = segments[alias]
        assert "ct.agency_id=%s" in segment, (alias, segment)
        assert "p.agency_id=%s" in segment, (alias, segment)
    # And the two single-root counters name their own.
    assert "ct.agency_id=%s" in segments["active_accounts"]
    assert "p.agency_id=%s" in segments["published"]


# ---------------------------------------------------------------------------
# 11-14 - publications: detail and writes
# ---------------------------------------------------------------------------

def test_11_a_publication_of_another_agency_is_not_found(db):
    with pytest.raises(NotFoundError) as excinfo:
        repository.get_publication(AGENCY_A, PUB_B)
    assert str(excinfo.value) == "Risorsa non trovata"


@pytest.mark.parametrize("call", [
    lambda: repository.get_publication(AGENCY_A, PUB_B),
    lambda: repository.update_publication(AGENCY_A, PUB_B, {"title": "hijack"}),
    lambda: repository.publish(AGENCY_A, PUB_B),
    lambda: repository.archive(AGENCY_A, PUB_B),
    lambda: repository.supersede(AGENCY_A, PUB_B, {"publication_type": "general_update",
                                                   "title": "t", "body": "b"}),
    lambda: repository.create_publication(AGENCY_A, {"property_id": PROPERTY_B,
                                                     "publication_type": "general_update",
                                                     "title": "t", "body": "b"}),
    lambda: repository.update_feedback_status(AGENCY_A, FEEDBACK_B, {"status": "handled"}),
    lambda: repository.update_feedback_status(AGENCY_A, FEEDBACK_LEGACY, {"status": "handled"}),
])
def test_12_no_operation_reaches_another_agencys_row(db, call):
    with pytest.raises(NotFoundError):
        call()
    assert db.writes == [], [s.sql for s in db.writes]
    assert db.audit == []
    assert db.commits == 0
    assert db.rollbacks == 1


def test_13_the_operations_work_inside_the_callers_agency(db):
    repository.publish(AGENCY_A, PUB_A_DRAFT)
    assert db.writes, "publish wrote nothing"
    assert db.agencies_seen() == {AGENCY_A}


def test_14_the_check_and_the_write_share_one_open_transaction(db):
    repository.publish(AGENCY_A, PUB_A_DRAFT)
    deciding = db.selects()[0]
    written = db.writes[0]
    assert deciding.connection == written.connection
    assert deciding.transaction == written.transaction
    assert deciding.transaction is not None


def test_15_a_status_conflict_is_still_a_conflict_not_a_404(db):
    """Scoping must not have swallowed the lifecycle rules."""
    with pytest.raises(ConflictError):
        repository.publish(AGENCY_A, PUB_A_PUBLISHED)
    with pytest.raises(ConflictError):
        repository.archive(AGENCY_A, PUB_A_DRAFT)


# ---------------------------------------------------------------------------
# 16 - notifications never cross an agency
# ---------------------------------------------------------------------------

def test_16_the_notification_fan_out_requires_the_two_roots_to_agree(db):
    """A grant whose roots disagree must not turn a publication of A into a
    notification for an owner of B. The agency is the property's own, so it is
    a join and not a parameter - nothing a caller passes can widen it."""
    repository.publish(AGENCY_A, PUB_A_DRAFT)
    fan_out = [s for s in db.writes if "owner_notifications" in s.sql.lower()]
    assert fan_out, [s.sql[:60] for s in db.writes]
    sql = fan_out[0].sql.lower()
    assert "join contacts ct on ct.id=oa.contact_id" in sql, sql
    assert "join properties p on p.id=x.property_id" in sql, sql
    assert "ct.agency_id=p.agency_id" in sql, sql


# ---------------------------------------------------------------------------
# 17-19 - the HTTP surface
# ---------------------------------------------------------------------------

BLOCK_A_GETS = [
    "/api/owner/admin/dashboard",
    "/api/owner/admin/publications",
    "/api/owner/admin/feedback",
    "/api/owner/admin/audit",
]


@pytest.mark.parametrize("path", BLOCK_A_GETS)
def test_17_the_routes_answer_only_for_the_callers_agency(db, client, path):
    response = client(AGENCY_A).get(path, auth=AUTH)
    assert response.status_code == 200, (path, response.text)
    assert db.agencies_seen() == {AGENCY_A}


@pytest.mark.parametrize("path", BLOCK_A_GETS)
def test_18_a_context_without_an_agency_is_refused_before_any_query(db, client, path):
    response = client(None).get(path, auth=AUTH)
    assert response.status_code == 403, (path, response.status_code, response.text)
    assert db.statements == []


@pytest.mark.parametrize("path", BLOCK_A_GETS)
def test_19_no_client_parameter_can_change_the_agency(db, client, path):
    response = client(AGENCY_A).get(path, params={"agency_id": AGENCY_B}, auth=AUTH)
    assert response.status_code == 200
    assert AGENCY_B not in db.agencies_seen()


def test_20_a_publication_of_another_agency_is_a_404_over_http(db, client):
    a = client(AGENCY_A)
    assert a.post(f"/api/owner/admin/publications/{PUB_B}/publish", auth=AUTH).status_code == 404
    assert a.post(f"/api/owner/admin/publications/{PUB_B}/archive", auth=AUTH).status_code == 404
    assert db.writes == []


# ---------------------------------------------------------------------------
# 21 - the whole OWNER Admin surface, pinned
# ---------------------------------------------------------------------------

SCOPED_ROUTES = {
    # reads (fe91c4e)
    "accounts", "access", "contacts", "account_properties",
    "property_documents", "property_visits",
    # account / access / token writes (a9dc5f2)
    "account", "disable", "enable", "access_create", "revoke", "token",
    # block A: dashboard, publications, feedback, audit
    "dash", "pubs", "pub", "edit", "publish", "archive", "supersede",
    "feedback", "feedback_status", "audit",
    # block B: shared documents
    "documents", "document_create", "document_upload", "document_detail",
    "document_update", "document_publish", "document_revoke", "document_archive",
    "document_supersede", "document_reads", "document_download",
    # block C: visit feedback
    "visit_feedback", "visit_feedback_detail", "visit_feedback_create",
    "visit_feedback_update", "visit_feedback_publish", "visit_feedback_archive",
    "visit_feedback_supersede",
}

# Deliberately without a context, and why. Both are asserted to touch no
# tenant table, so "no context" is a property of the route and not an oversight.
NO_TENANT = {
    "visit_feedback_validate_privacy",   # validates a string, touches no table
    "document_storage_health",           # asks the storage backend, not the DB
}


def test_21_the_scoped_and_unscoped_owner_admin_routes_are_both_named():
    """Admission by AST. A forgotten route and an unannounced new one both fail.

    Everything not in either set is still to do and is listed in the failure,
    which is how the remaining blocks are tracked.
    """
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    scoped, all_routes = set(), set()
    for module in ("owner/router_admin.py", "owner/router_admin_lookups.py"):
        tree = ast.parse((root / module).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not any(isinstance(d, ast.Call) and getattr(d.func, "attr", None)
                       in {"get", "post", "patch", "put", "delete"}
                       for d in node.decorator_list):
                continue
            all_routes.add(node.name)
            if any(
                isinstance(default, ast.Call)
                and getattr(default.func, "id", None) == "Depends"
                and default.args
                and getattr(default.args[0], "id", None) == "basic_only_agency_context"
                for default in node.args.defaults
            ):
                scoped.add(node.name)

    assert scoped == SCOPED_ROUTES, {
        "missing": sorted(SCOPED_ROUTES - scoped),
        "unannounced": sorted(scoped - SCOPED_ROUTES),
    }
    assert NO_TENANT.isdisjoint(scoped), sorted(NO_TENANT & scoped)
    # Nothing left over: every OWNER Admin route is either agency-bound or
    # named in NO_TENANT with a reason.
    assert sorted(all_routes - scoped - NO_TENANT) == []


# ===========================================================================
# BLOCKS B AND C - shared documents and visit feedback
#
# Both tables have a NULLABLE owner_account_id, so the account is not a
# dependable root: the tenant comes through the source - a property_document
# or a property_visit - to its property. Four helpers carry that derivation
# and every admin path goes through one of them:
#
#     _property_for_document      _shared_document_with_source
#     _property_for_visit         _visit_feedback_for_update
#
# _validate_target_account is the fifth, and the only one that requires BOTH
# roots: it is what decides whether a document may be aimed at one owner, and a
# grant whose roots disagree must not be usable for that.
#
# The download matters most. It streams a private file, so a shared document of
# another agency must be refused before the storage key is ever read.
# ===========================================================================

DOCUMENT_OPERATIONS = {
    "get_shared_document": lambda a, i: repository.get_shared_document(a, i),
    "update_shared_document": lambda a, i: repository.update_shared_document(a, i, {"public_title": "x"}),
    "revoke_shared_document": lambda a, i: repository.revoke_shared_document(a, i, "actor", "reason"),
    "archive_shared_document": lambda a, i: repository.archive_shared_document(a, i),
    "supersede_shared_document": lambda a, i: repository.supersede_shared_document(
        a, i, {"property_document_id": SOURCE_DOC_B, "public_title": "x",
               "public_document_type": "ape"}),
    "shared_document_reads": lambda a, i: repository.shared_document_reads(a, i),
    "prepare_admin_shared_document_download": lambda a, i:
        repository.prepare_admin_shared_document_download(a, i, storage=_RefusingStorage()),
}

VISIT_FEEDBACK_OPERATIONS = {
    "get_visit_feedback_publication": lambda a, i: repository.get_visit_feedback_publication(a, i),
    "update_visit_feedback_publication": lambda a, i:
        repository.update_visit_feedback_publication(a, i, {"category": "price"}),
    "publish_visit_feedback": lambda a, i: repository.publish_visit_feedback(a, i),
    "archive_visit_feedback": lambda a, i: repository.archive_visit_feedback(a, i),
    "supersede_visit_feedback": lambda a, i: repository.supersede_visit_feedback(
        a, i, {"category": "price", "public_summary": "Riepilogo pubblico neutro."}),
}


class _RefusingStorage:
    """Any read of a storage key from a refused document is itself the failure."""

    def open_stream(self, *args, **kwargs):
        raise AssertionError("the storage was reached for a document of another agency")

    def healthcheck(self):
        return {"ok": True}


@pytest.mark.parametrize("name", sorted(DOCUMENT_OPERATIONS))
def test_22_no_document_operation_reaches_another_agency(db, name):
    with pytest.raises(NotFoundError) as excinfo:
        DOCUMENT_OPERATIONS[name](AGENCY_A, SHARED_B)
    assert str(excinfo.value) == "Risorsa non trovata", name
    assert db.writes == [], (name, [s.sql for s in db.writes])
    assert db.audit == [], (name, db.audit)


@pytest.mark.parametrize("name", sorted(VISIT_FEEDBACK_OPERATIONS))
def test_23_no_visit_feedback_operation_reaches_another_agency(db, name):
    with pytest.raises(NotFoundError) as excinfo:
        VISIT_FEEDBACK_OPERATIONS[name](AGENCY_A, VF_B)
    assert str(excinfo.value) == "Risorsa non trovata", name
    assert db.writes == [], (name, [s.sql for s in db.writes])
    assert db.audit == [], (name, db.audit)


@pytest.mark.parametrize("name", sorted(DOCUMENT_OPERATIONS) + sorted(VISIT_FEEDBACK_OPERATIONS))
def test_24_the_deciding_read_names_the_tenant(db, name):
    """The refusal must come from a predicate, not from a Python comparison
    after a global read."""
    operation = {**DOCUMENT_OPERATIONS, **VISIT_FEEDBACK_OPERATIONS}[name]
    target = SHARED_B if name in DOCUMENT_OPERATIONS else VF_B
    with pytest.raises(NotFoundError):
        operation(AGENCY_A, target)
    assert db.statements, name
    first = db.statements[0]
    assert _TENANT.search(first.sql), (name, first.sql)
    assert AGENCY_A in first.params and AGENCY_B not in first.params, (name, first.params)


def test_25_the_download_refuses_before_the_storage_key_is_used(db):
    """`_RefusingStorage` turns any storage access into a failure of its own, so
    a pass here means the refusal happened while still in the database."""
    with pytest.raises(NotFoundError):
        repository.prepare_admin_shared_document_download(
            AGENCY_A, SHARED_B, storage=_RefusingStorage()
        )


def test_26_the_document_and_visit_helpers_derive_through_properties(db):
    """One derivation, four helpers, read from the statements they issue."""
    for call, expected in (
        (lambda: repository.get_shared_document(AGENCY_A, SHARED_A), "owner_shared_documents"),
        (lambda: repository.get_visit_feedback_publication(AGENCY_A, VF_A),
         "owner_visit_feedback_publications"),
    ):
        db.statements.clear()
        call()
        sql = db.statements[0].sql.lower()
        assert expected in sql, sql
        assert "join properties p" in sql, sql
        assert "p.agency_id=%s" in sql, sql


def test_27_creating_from_a_source_of_another_agency_is_refused(db):
    for call in (
        lambda: repository.create_shared_document(AGENCY_A, {
            "property_document_id": SOURCE_DOC_B, "public_title": "x",
            "public_document_type": "ape"}),
        lambda: repository.create_visit_feedback_publication(AGENCY_A, {
            "property_visit_id": VISIT_B, "category": "price",
            "public_summary": "Riepilogo pubblico neutro."}),
    ):
        db.statements.clear()
        with pytest.raises(NotFoundError):
            call()
        assert db.writes == []


def test_28_the_listings_put_the_tenant_first_and_it_is_not_optional(db):
    repository.list_shared_documents(AGENCY_A)
    documents = db.statements[0]
    assert _TENANT.search(documents.sql), documents.sql
    assert documents.params[0] == AGENCY_A, documents.params
    db.statements.clear()
    repository.list_visit_feedback_publications(AGENCY_A)
    visits = db.statements[0]
    assert _TENANT.search(visits.sql), visits.sql
    assert visits.params[0] == AGENCY_A, visits.params


def test_29_the_target_account_check_requires_both_roots(db):
    """`_validate_target_account` is what aims a document at one owner. A grant
    whose two roots disagree must not be usable for that."""
    import inspect

    source = inspect.getsource(repository._validate_target_account)
    assert "ct.agency_id=%s" in source and "p.agency_id=%s" in source, source
    assert "JOIN contacts ct" in source and "JOIN properties p" in source, source
    parameters = list(inspect.signature(repository._validate_target_account).parameters)
    assert parameters[:2] == ["c", "agency_id"], parameters


@pytest.mark.parametrize("fn", [
    "list_shared_documents", "get_shared_document", "create_shared_document",
    "create_uploaded_shared_document", "update_shared_document",
    "publish_shared_document", "revoke_shared_document", "archive_shared_document",
    "supersede_shared_document", "shared_document_reads",
    "prepare_admin_shared_document_download",
    "list_visit_feedback_publications", "get_visit_feedback_publication",
    "create_visit_feedback_publication", "update_visit_feedback_publication",
    "publish_visit_feedback", "archive_visit_feedback", "supersede_visit_feedback",
])
def test_30_every_block_b_and_c_function_takes_the_agency_first(fn):
    import inspect

    parameters = list(inspect.signature(getattr(repository, fn)).parameters.values())
    assert parameters[0].name == "agency_id", (fn, parameters[0].name)
    assert parameters[0].default is inspect.Parameter.empty, fn


BLOCK_BC_ROUTES = [
    ("get", "/api/owner/admin/documents", None),
    ("get", f"/api/owner/admin/documents/{SHARED_B}", None),
    ("get", f"/api/owner/admin/documents/{SHARED_B}/reads", None),
    ("get", "/api/owner/admin/visit-feedback", None),
    ("get", f"/api/owner/admin/visit-feedback/{VF_B}", None),
]


@pytest.mark.parametrize("method,path,body", BLOCK_BC_ROUTES)
def test_31_a_context_without_an_agency_is_refused_before_any_query(db, client, method, path, body):
    response = getattr(client(None), method)(path, auth=AUTH)
    assert response.status_code == 403, (path, response.status_code, response.text)
    assert db.statements == []


def test_32_another_agencys_document_and_visit_feedback_are_404_over_http(db, client):
    a = client(AGENCY_A)
    for path in (f"/api/owner/admin/documents/{SHARED_B}",
                 f"/api/owner/admin/documents/{SHARED_B}/reads",
                 f"/api/owner/admin/visit-feedback/{VF_B}"):
        response = a.get(path, auth=AUTH)
        assert response.status_code == 404, (path, response.status_code, response.text)
        assert response.json() == {"detail": "Risorsa non trovata"}
    assert db.writes == []


def test_33_a_read_recorded_against_another_agencys_account_is_hidden(db):
    """The document is A's, so the guard on the document cannot catch this.

    Only the second root in the reads query can: a legacy grant whose two roots
    disagree is what makes such a row reachable at all, and hiding it is the
    same rule list_access applies to the grant itself.
    """
    rows = repository.shared_document_reads(AGENCY_A, SHARED_A)
    assert [row["owner_account_id"] for row in rows] == [ACCOUNT_A], rows

    reads_query = [s for s in db.statements if "owner_document_reads" in s.sql][0]
    assert constrained_tables(reads_query.sql) >= {"contacts", "properties"}, reads_query.sql


def test_34_the_download_route_passes_the_callers_agency(db, client, monkeypatch):
    """Read from what the route hands the repository, so a hardcoded agency in
    the route is visible. The storage is never involved."""
    seen = {}

    def fake_prepare(agency_id, item_id, storage=None):
        seen["agency_id"] = agency_id
        seen["item_id"] = item_id
        raise NotFoundError("Risorsa non trovata")

    monkeypatch.setattr(repository, "prepare_admin_shared_document_download", fake_prepare)
    response = client(AGENCY_B).get(f"/api/owner/admin/documents/{SHARED_A}/download", auth=AUTH)
    assert response.status_code == 404
    assert seen == {"agency_id": AGENCY_B, "item_id": SHARED_A}


@pytest.mark.parametrize("path,other", [
    (f"/api/owner/admin/documents/{SHARED_A}", AGENCY_B),
    (f"/api/owner/admin/documents/{SHARED_B}", AGENCY_A),
    (f"/api/owner/admin/visit-feedback/{VF_A}", AGENCY_B),
    (f"/api/owner/admin/visit-feedback/{VF_B}", AGENCY_A),
])
def test_35_every_detail_route_refuses_the_other_agency_in_both_directions(db, client, path, other):
    """Both directions, so a route that hardcoded one agency would pass in one
    of them and fail here in the other."""
    response = client(other).get(path, auth=AUTH)
    assert response.status_code == 404, (path, other, response.status_code)
