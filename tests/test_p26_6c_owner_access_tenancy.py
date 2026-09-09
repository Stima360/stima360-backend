"""P26-6C OWNER - an access grant may not cross agencies.

`owner_property_access` is the table an administrator uses to *create* a link
between OWNER's two tenancy roots: the account reaches an agency through
`owner_accounts -> contacts`, the property carries its own. Other OWNER tables
touch both roots too - `owner_publication_reads`, `owner_document_reads`,
`owner_feedback`, `owner_notifications` - but they record something that
happened to a link that already exists. This one establishes it, and it is what
`portal_properties` and `require_property` then treat as authorisation.

Until this patch nothing required the two sides to agree:

    INSERT INTO owner_property_access (owner_account_id, property_id) ...
    -- account of agency A, property of agency B: accepted

WHAT THESE TESTS ARE, AND ARE NOT

They are behavioural: they call `create_access` and assert what it does,
against a small in-memory model of two agencies installed over
`owner.repository.core_cursor`.

The model is deliberately strict rather than permissive. An earlier version
answered by table name alone, which made it a poor witness: it returned
`agency_id` for `SELECT id FROM properties`, and it followed
`owner_accounts.contact_id` even when the query joined `ON ct.id = oa.id`. Both
are queries that cannot answer the tenancy question, and both went unnoticed.
The model now checks a narrow contract for each of the two reads - projection,
join predicate, selection key, parameters - and refuses anything else. It is
not an SQL interpreter and does not try to be: it knows exactly two SELECT
shapes, one INSERT into the grant table and one into the audit log.

They are NOT a PostgreSQL proof. No real transaction, lock or constraint is
exercised. Test 19 proves that the reads and the INSERT are issued on one
connection inside one still-open transaction; it does not prove what PostgreSQL
does with that. Test 20 proves which rows the executed statements ask to lock;
it does not observe a lock being taken or held. Concurrency and the live
behaviour remain uncertified.
"""
from __future__ import annotations

import re
from contextlib import contextmanager
from pathlib import Path

import pytest

from core.exceptions import NotFoundError
from owner import repository


ROOT = Path(__file__).resolve().parents[1]

AGENCY_A = 901
AGENCY_B = 902

CONTACT_A = 11
CONTACT_B = 12
CONTACT_NO_AGENCY = 13      # exists, but carries no agency
CONTACT_MISSING = 19        # referenced by an account, absent from contacts

ACCOUNT_A = 1
ACCOUNT_B = 2
ACCOUNT_NO_AGENCY = 3
ACCOUNT_DANGLING = 4        # its contact row is not there
ACCOUNT_MISSING = 9

PROPERTY_A = 101
PROPERTY_B = 102
PROPERTY_NO_AGENCY = 103
PROPERTY_MISSING = 109


class ModelViolation(AssertionError):
    """The code under test issued SQL this model refuses to answer."""


# ---------------------------------------------------------------------------
# Locking clause, split off before anything else is parsed.
#
# `FOR SHARE`, `FOR SHARE OF a, b`, `FOR UPDATE`, `FOR KEY SHARE`, ... The rest
# of the statement is matched against the query contracts; the clause itself is
# what test 20 reads.
# ---------------------------------------------------------------------------

_LOCK = re.compile(
    r"\s+for\s+(?P<mode>update|share|key\s+share|no\s+key\s+update)"
    r"(?:\s+of\s+(?P<aliases>[\w\s,]+?))?\s*$",
    re.IGNORECASE,
)


def split_lock(flat: str) -> tuple[str, str | None, tuple[str, ...]]:
    """(statement without its locking clause, mode, explicitly named aliases)."""
    found = _LOCK.search(flat)
    if not found:
        return flat, None, ()
    mode = " ".join(found.group("mode").split()).lower()
    named = found.group("aliases") or ""
    aliases = tuple(part.strip() for part in named.split(",") if part.strip())
    return flat[: found.start()], mode, aliases


_SOURCE = re.compile(r"\b(?:from|join)\s+(?P<table>\w+)(?:\s+(?P<alias>\w+))?", re.IGNORECASE)
_NOT_AN_ALIAS = {"on", "where", "for", "join", "inner", "left", "group", "order"}


def alias_map(flat: str) -> dict[str, str]:
    """Every way a source in this statement can be named -> its table.

    `FROM owner_accounts oa JOIN contacts ct` yields
    {'owner_accounts': 'owner_accounts', 'oa': 'owner_accounts',
     'contacts': 'contacts', 'ct': 'contacts'}.
    """
    names: dict[str, str] = {}
    for match in _SOURCE.finditer(flat):
        table = match.group("table").lower()
        names[table] = table
        alias = (match.group("alias") or "").lower()
        if alias and alias not in _NOT_AN_ALIAS:
            names[alias] = table
    return names


def locked_tables(flat: str) -> set[str]:
    """Which tables this statement asks to lock. Order of aliases is irrelevant."""
    body, mode, aliases = split_lock(flat)
    if mode is None:
        return set()
    names = alias_map(body)
    if not aliases:
        # `FOR SHARE` with no OF locks every source in the FROM clause.
        return set(names.values())
    return {names[a.lower()] for a in aliases if a.lower() in names}


# ---------------------------------------------------------------------------
# The two read contracts.
#
# Narrow on purpose: each says what the query must project, how it must join,
# and what it must select by. A query that fails one of these cannot answer the
# question `create_access` is asking, whatever it returns.
# ---------------------------------------------------------------------------

_ACCOUNT_READ = re.compile(
    r"^select\s+(?P<projection>.+?)"
    r"\s+from\s+owner_accounts\s+(?P<oa>\w+)"
    r"\s+join\s+contacts\s+(?P<ct>\w+)"
    r"\s+on\s+(?P<on>.+?)"
    r"\s+where\s+(?P<where>.+?)$",
    re.IGNORECASE,
)

_PROPERTY_READ = re.compile(
    r"^select\s+(?P<projection>.+?)"
    r"\s+from\s+properties"
    r"\s+where\s+(?P<where>.+?)$",
    re.IGNORECASE,
)


def _equates(clause: str, left: str, right: str) -> bool:
    """`left = right` or `right = left`, whitespace-insensitive."""
    normalised = re.sub(r"\s*=\s*", "=", clause.strip().lower())
    return normalised in {f"{left.lower()}={right.lower()}", f"{right.lower()}={left.lower()}"}


def parse_account_read(flat: str, params) -> int:
    """Validate the account read against its contract; return the account id."""
    body, _, _ = split_lock(flat)
    match = _ACCOUNT_READ.match(body.strip())
    if not match:
        raise ModelViolation(
            "the account read must be "
            "`SELECT <contacts alias>.agency_id FROM owner_accounts <a> "
            "JOIN contacts <c> ON ... WHERE ...`; got: " + flat
        )
    oa, ct = match.group("oa").lower(), match.group("ct").lower()

    projection = match.group("projection").lower()
    if f"{ct}.agency_id" not in projection:
        raise ModelViolation(
            f"the account read must project {ct}.agency_id - the contact's agency is "
            f"the whole point of the join; got projection: {match.group('projection')}"
        )
    if not _equates(match.group("on"), f"{ct}.id", f"{oa}.contact_id"):
        raise ModelViolation(
            f"the account read must join ON {ct}.id = {oa}.contact_id; "
            f"got: {match.group('on')}"
        )
    if not _equates(match.group("where"), f"{oa}.id", "%s"):
        raise ModelViolation(
            f"the account read must select by {oa}.id = %s; got: {match.group('where')}"
        )
    values = list(params or ())
    if len(values) != 1:
        raise ModelViolation(f"the account read takes exactly one parameter; got {values}")
    return values[0]


def parse_property_read(flat: str, params) -> int:
    """Validate the property read against its contract; return the property id."""
    body, _, _ = split_lock(flat)
    match = _PROPERTY_READ.match(body.strip())
    if not match:
        raise ModelViolation(
            "the property read must be `SELECT agency_id FROM properties WHERE ...`; "
            "got: " + flat
        )
    if "agency_id" not in match.group("projection").lower():
        raise ModelViolation(
            "the property read must project agency_id; got projection: "
            + match.group("projection")
        )
    if not _equates(match.group("where"), "id", "%s"):
        raise ModelViolation(
            f"the property read must select by id = %s; got: {match.group('where')}"
        )
    values = list(params or ())
    if len(values) != 1:
        raise ModelViolation(f"the property read takes exactly one parameter; got {values}")
    return values[0]


# ---------------------------------------------------------------------------
# A two-agency world, with connection and transaction identity.
# ---------------------------------------------------------------------------

class Statement:
    __slots__ = ("sql", "params", "connection", "transaction", "position")

    def __init__(self, sql, params, connection, transaction, position):
        self.sql = sql
        self.params = params
        self.connection = connection
        self.transaction = transaction
        # Where this statement sits in the world's event stream, so that
        # "did anything end the transaction between these two statements"
        # is answerable rather than approximated.
        self.position = position

    @property
    def verb(self) -> str:
        return self.sql.split(None, 1)[0].lower()


class World:
    def __init__(self):
        self.contacts = {
            CONTACT_A: {"id": CONTACT_A, "agency_id": AGENCY_A},
            CONTACT_B: {"id": CONTACT_B, "agency_id": AGENCY_B},
            CONTACT_NO_AGENCY: {"id": CONTACT_NO_AGENCY, "agency_id": None},
        }
        self.accounts = {
            ACCOUNT_A: {"id": ACCOUNT_A, "contact_id": CONTACT_A},
            ACCOUNT_B: {"id": ACCOUNT_B, "contact_id": CONTACT_B},
            ACCOUNT_NO_AGENCY: {"id": ACCOUNT_NO_AGENCY, "contact_id": CONTACT_NO_AGENCY},
            ACCOUNT_DANGLING: {"id": ACCOUNT_DANGLING, "contact_id": CONTACT_MISSING},
        }
        self.properties = {
            PROPERTY_A: {"id": PROPERTY_A, "agency_id": AGENCY_A},
            PROPERTY_B: {"id": PROPERTY_B, "agency_id": AGENCY_B},
            PROPERTY_NO_AGENCY: {"id": PROPERTY_NO_AGENCY, "agency_id": None},
        }
        self.statements: list[Statement] = []
        self.events: list[tuple[str, int, int | None]] = []
        self.grants: list[dict] = []
        self.audit: list[dict] = []
        self._connections = 0
        self._transactions = 0
        self._next_grant_id = 5000

    # -- identity -----------------------------------------------------------

    def take_connection_id(self) -> int:
        self._connections += 1
        return self._connections

    def take_transaction_id(self) -> int:
        self._transactions += 1
        return self._transactions

    def log(self, event: str, connection_id: int, transaction_id: int | None) -> None:
        self.events.append((event, connection_id, transaction_id))

    # -- what the assertions ask about --------------------------------------

    def granted(self) -> list[dict]:
        return list(self.grants)

    def success_audit(self) -> list[dict]:
        return [row for row in self.audit if row.get("result") == "success"]

    def find(self, table: str) -> list[Statement]:
        return [s for s in self.statements if re.search(rf"\b{table}\b", s.sql, re.I)]

    def count(self, event: str) -> int:
        return sum(1 for name, _, _ in self.events if name == event)


class Connection:
    def __init__(self, world: World):
        self.world = world
        self.id = world.take_connection_id()
        self.transaction: int | None = None
        world.log("open", self.id, None)

    def begin_if_needed(self) -> int:
        """psycopg2 opens a transaction lazily, on the first statement."""
        if self.transaction is None:
            self.transaction = self.world.take_transaction_id()
            self.world.log("begin", self.id, self.transaction)
        return self.transaction

    def commit(self):
        self.world.log("commit", self.id, self.transaction)
        self.transaction = None

    def rollback(self):
        self.world.log("rollback", self.id, self.transaction)
        self.transaction = None

    def close(self):
        self.world.log("close", self.id, self.transaction)


class Cursor:
    def __init__(self, world: World, connection: Connection):
        self.world = world
        self.connection = connection
        self._rows: list[dict] = []

    def execute(self, sql, params=None):
        flat = " ".join(str(sql).split())
        low = flat.lower()
        transaction = self.connection.begin_if_needed()
        self.world.statements.append(
            Statement(flat, params, self.connection.id, transaction, len(self.world.events))
        )
        self.world.log("execute", self.connection.id, transaction)

        if low.startswith("insert into owner_property_access"):
            self._rows = [self._insert_grant(list(params or ()))]
            return
        if low.startswith("insert into owner_audit_log"):
            self.world.audit.append(self._audit_row(list(params or ())))
            self._rows = []
            return
        if re.search(r"\bowner_property_access\b", low):
            # Answered rather than refused, so that an application-level
            # duplicate pre-check fails in test 17 - which explains what is
            # wrong with it - instead of blowing up every test in the file.
            self._rows = []
            return
        if re.search(r"\bowner_accounts\b", low):
            self._rows = self._read_account(parse_account_read(flat, params))
            return
        if re.search(r"\bproperties\b", low):
            self._rows = self._read_property(parse_property_read(flat, params))
            return
        raise ModelViolation(f"the model was asked something it does not know: {flat}")

    # -- readers ------------------------------------------------------------

    def _read_account(self, account_id) -> list[dict]:
        account = self.world.accounts.get(account_id)
        if account is None:
            return []
        contact = self.world.contacts.get(account["contact_id"])
        if contact is None:
            return []          # inner join: a dangling contact yields no row
        return [{"agency_id": contact["agency_id"]}]

    def _read_property(self, property_id) -> list[dict]:
        prop = self.world.properties.get(property_id)
        return [{"agency_id": prop["agency_id"]}] if prop is not None else []

    # -- writers ------------------------------------------------------------

    def _insert_grant(self, values) -> dict:
        account_id, property_id, access_role, is_primary, valid_until = (
            values + [None] * 5
        )[:5]
        row = {
            "id": self._take_id(),
            "owner_account_id": account_id,
            "property_id": property_id,
            "access_role": access_role,
            "access_status": "active",
            "is_primary": is_primary,
            "valid_until": valid_until,
        }
        self.world.grants.append(row)
        return dict(row)

    def _audit_row(self, values) -> dict:
        keys = ("owner_account_id", "property_id", "action",
                "entity_type", "entity_id", "result", "metadata")
        return dict(zip(keys, values + [None] * len(keys)))

    def _take_id(self) -> int:
        self.world._next_grant_id += 1
        return self.world._next_grant_id

    # -- cursor protocol ----------------------------------------------------

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
            connection.close()

    monkeypatch.setattr(repository, "core_cursor", fake_core_cursor)
    return w


def grant(account_id, property_id, **extra):
    payload = {"owner_account_id": account_id, "property_id": property_id}
    payload.update(extra)
    return repository.create_access(payload)


# ---------------------------------------------------------------------------
# 1-2 - what must keep working
# ---------------------------------------------------------------------------

def test_1_a_grant_inside_agency_a_is_allowed(world):
    row = grant(ACCOUNT_A, PROPERTY_A)
    assert row["owner_account_id"] == ACCOUNT_A
    assert row["property_id"] == PROPERTY_A
    assert len(world.granted()) == 1
    # Two commits: the grant's own transaction, then `audit`, which opens its
    # own connection after the grant is durable. Test 19 pins that split.
    assert world.count("commit") == 2
    assert world.count("rollback") == 0


def test_2_a_grant_inside_agency_b_is_allowed(world):
    """The other direction, so the rule is not "agency A is special"."""
    row = grant(ACCOUNT_B, PROPERTY_B)
    assert row["owner_account_id"] == ACCOUNT_B
    assert row["property_id"] == PROPERTY_B
    assert len(world.granted()) == 1


# ---------------------------------------------------------------------------
# 3-4 - the regression this file exists for
# ---------------------------------------------------------------------------

def test_3_an_account_of_a_cannot_be_granted_a_property_of_b(world):
    """Accepted before this patch. This is the defect."""
    with pytest.raises(NotFoundError):
        grant(ACCOUNT_A, PROPERTY_B)
    assert world.granted() == [], "a cross-agency grant was written"


def test_4_an_account_of_b_cannot_be_granted_a_property_of_a(world):
    with pytest.raises(NotFoundError):
        grant(ACCOUNT_B, PROPERTY_A)
    assert world.granted() == []


# ---------------------------------------------------------------------------
# 5-7 - missing rows
# ---------------------------------------------------------------------------

def test_5_a_missing_account_is_refused(world):
    with pytest.raises(NotFoundError):
        grant(ACCOUNT_MISSING, PROPERTY_A)
    assert world.granted() == []


def test_6_a_missing_property_is_refused(world):
    with pytest.raises(NotFoundError):
        grant(ACCOUNT_A, PROPERTY_MISSING)
    assert world.granted() == []


def test_7_an_account_whose_contact_is_gone_is_refused(world):
    """The FK is ON DELETE RESTRICT, so this should be unreachable in a live
    database. It is asserted anyway: the tenant is read through that contact,
    and "cannot happen" is not the same as "is refused if it does"."""
    with pytest.raises(NotFoundError):
        grant(ACCOUNT_DANGLING, PROPERTY_A)
    assert world.granted() == []


# ---------------------------------------------------------------------------
# 8-10 - the agency cannot be determined
# ---------------------------------------------------------------------------

def test_8_an_account_without_an_agency_is_refused(world):
    with pytest.raises(NotFoundError):
        grant(ACCOUNT_NO_AGENCY, PROPERTY_A)
    assert world.granted() == []


def test_9_a_property_without_an_agency_is_refused(world):
    with pytest.raises(NotFoundError):
        grant(ACCOUNT_A, PROPERTY_NO_AGENCY)
    assert world.granted() == []


def test_10_two_unknown_agencies_are_not_a_match(world):
    """`NULL == NULL` is the trap.

    A comparison written as `account_agency != property_agency` returns False
    when both are None, which would accept the one case where nothing is known
    about either side. Both must be rejected on their own before they are ever
    compared.
    """
    with pytest.raises(NotFoundError):
        grant(ACCOUNT_NO_AGENCY, PROPERTY_NO_AGENCY)
    assert world.granted() == []


# ---------------------------------------------------------------------------
# 11-13 - what a refusal must leave behind
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "account_id,property_id",
    [
        (ACCOUNT_A, PROPERTY_B),
        (ACCOUNT_B, PROPERTY_A),
        (ACCOUNT_MISSING, PROPERTY_A),
        (ACCOUNT_A, PROPERTY_MISSING),
        (ACCOUNT_DANGLING, PROPERTY_A),
        (ACCOUNT_NO_AGENCY, PROPERTY_A),
        (ACCOUNT_A, PROPERTY_NO_AGENCY),
        (ACCOUNT_NO_AGENCY, PROPERTY_NO_AGENCY),
    ],
)
def test_11_a_refusal_writes_nothing_at_all(world, account_id, property_id):
    with pytest.raises(NotFoundError):
        grant(account_id, property_id)
    assert world.granted() == []
    assert world.audit == [], "a refused grant left an audit row"
    assert world.success_audit() == []
    assert world.count("commit") == 0, "a refused grant committed its transaction"
    assert world.count("rollback") == 1


def test_12_the_refusal_is_the_neutral_not_found_the_route_already_maps(world):
    """`owner/router_admin.py` turns NotFoundError into 404 'Risorsa non
    trovata'. Raising anything else would either leak that the property exists
    in another agency, or reach the client as a 500."""
    with pytest.raises(NotFoundError) as excinfo:
        grant(ACCOUNT_A, PROPERTY_B)
    assert str(excinfo.value) == "Risorsa non trovata"


def test_13_an_accepted_grant_still_audits_success(world):
    grant(ACCOUNT_A, PROPERTY_A)
    granted = [row for row in world.audit if row["action"] == "access_granted"]
    assert len(granted) == 1, world.audit
    assert granted[0]["result"] == "success"
    assert granted[0]["owner_account_id"] == ACCOUNT_A
    assert granted[0]["property_id"] == PROPERTY_A


# ---------------------------------------------------------------------------
# 14-17 - the behaviour around the check must not have moved
# ---------------------------------------------------------------------------

def test_14_the_role_is_still_carried_through(world):
    row = grant(ACCOUNT_A, PROPERTY_A, access_role="co_owner")
    assert row["access_role"] == "co_owner"


def test_15_the_role_still_defaults_to_owner(world):
    assert grant(ACCOUNT_A, PROPERTY_A)["access_role"] == "owner"


def test_16_the_expiry_and_primary_flag_are_still_carried_through(world):
    row = grant(ACCOUNT_A, PROPERTY_A, is_primary=True, valid_until="2030-01-01")
    assert row["is_primary"] is True
    assert row["valid_until"] == "2030-01-01"


def test_17_duplicates_are_still_left_to_the_unique_constraint(world):
    """`UNIQUE(owner_account_id, property_id)` is what refuses a second grant,
    and it does so from the database. This patch must not quietly add an
    application-level pre-check, which would change the error a caller sees.
    """
    grant(ACCOUNT_A, PROPERTY_A)
    reads = [s.sql for s in world.find("owner_property_access") if s.verb == "select"]
    assert reads == [], reads


# ---------------------------------------------------------------------------
# 18-20 - how the check is made, not only that it is made
# ---------------------------------------------------------------------------

def test_18_both_agencies_come_from_the_database(world):
    """Neither side may be taken from the caller's payload.

    The client-supplied dict reaches `create_access` directly from the request
    body, so an `agency_id` in it must have no effect whatsoever.
    """
    with pytest.raises(NotFoundError):
        grant(ACCOUNT_A, PROPERTY_B, agency_id=AGENCY_B)
    assert world.granted() == []

    row = grant(ACCOUNT_A, PROPERTY_A, agency_id=AGENCY_B)
    assert row["property_id"] == PROPERTY_A
    inserts = [s.sql for s in world.find("owner_property_access") if s.verb == "insert"]
    assert len(inserts) == 1
    assert "agency_id" not in inserts[0].lower(), inserts[0]


def test_19_the_two_reads_and_the_insert_are_one_open_transaction(world):
    """Counting commits proved nothing.

    The previous version asserted "two commits in total", which stays true when
    the reads are moved into their own `core_cursor(commit=False)` and the
    INSERT into a second one - the exact refactor that would silently undo the
    guarantee. What matters is identity: the same connection, the same
    transaction, and that transaction still open when the INSERT lands.
    """
    grant(ACCOUNT_A, PROPERTY_A)

    account_read = world.find("owner_accounts")[0]
    property_read = world.find("properties")[0]
    insert = [s for s in world.find("owner_property_access") if s.verb == "insert"][0]

    deciding = (account_read, property_read, insert)
    assert len({s.connection for s in deciding}) == 1, [s.connection for s in deciding]
    assert len({s.transaction for s in deciding}) == 1, [s.transaction for s in deciding]
    assert all(s.transaction is not None for s in deciding)

    # Nothing ended that transaction between the first read and the INSERT.
    assert account_read.position < property_read.position < insert.position
    between = world.events[account_read.position:insert.position]
    ended = [event for event in between if event[0] in {"commit", "rollback", "close"}]
    assert ended == [], f"the deciding transaction ended before the INSERT: {ended}"
    # And exactly one transaction was opened to carry all three.
    assert [e for e in between if e[0] == "begin"] == []

    # The audit is deliberately elsewhere: it is written once the grant is
    # durable, on its own connection.
    audit_insert = [s for s in world.find("owner_audit_log") if s.verb == "insert"][0]
    assert audit_insert.connection != insert.connection
    assert audit_insert.transaction != insert.transaction


def test_20_all_three_deciding_rows_are_share_locked(world):
    """Read from the statements actually executed, not from their text.

    "`FOR SHARE` appears" and "`contacts` appears" was the previous assertion,
    and both `FOR SHARE OF oa` and `FOR SHARE OF ct` satisfy it while leaving
    one of the two rows unlocked. The alias list is resolved against the FROM
    and JOIN clauses, so the order the aliases are written in is irrelevant.

    Why `FOR SHARE` and not something weaker: `owner_accounts.contact_id` is
    UNIQUE and `contacts` carries `contacts_agency_scope_unq UNIQUE
    (agency_id, id)` (030), so on those two a `FOR KEY SHARE` would in fact
    block the update this guards against. `properties.agency_id` is covered
    only by the plain, non-unique `idx_properties_agency_id` (034), so there it
    would not. `FOR SHARE` is the weakest level that covers all three
    uniformly, and it still lets two grants against the same property proceed
    together - only writers to those rows wait.

    What this does NOT show: that a lock is taken, held, or effective. That is
    a PostgreSQL claim and nothing here runs PostgreSQL. It also says nothing
    about after the commit - see the note in `create_access`.
    """
    grant(ACCOUNT_A, PROPERTY_A)

    account_read = world.find("owner_accounts")[0].sql
    property_read = world.find("properties")[0].sql

    _, account_mode, _ = split_lock(account_read)
    _, property_mode, _ = split_lock(property_read)
    assert account_mode == "share", (account_mode, account_read)
    assert property_mode == "share", (property_mode, property_read)

    assert locked_tables(account_read) >= {"owner_accounts", "contacts"}, account_read
    assert locked_tables(property_read) >= {"properties"}, property_read


# ---------------------------------------------------------------------------
# 21-24 - the model itself
#
# These assert that the contracts above reject the queries that an earlier,
# permissive model happily answered. Without them the model's strictness is
# itself untested, and a later loosening of it would go unnoticed.
# ---------------------------------------------------------------------------

def test_21_a_property_read_without_agency_id_is_refused_by_the_model():
    with pytest.raises(ModelViolation, match="must project agency_id"):
        parse_property_read("SELECT id FROM properties WHERE id=%s", (PROPERTY_A,))


def test_22_a_wrong_join_is_refused_by_the_model():
    with pytest.raises(ModelViolation, match="must join ON"):
        parse_account_read(
            "SELECT ct.agency_id FROM owner_accounts oa JOIN contacts ct "
            "ON ct.id=oa.id WHERE oa.id=%s",
            (ACCOUNT_A,),
        )


def test_23_the_pre_patch_account_read_is_refused_by_the_model():
    """`SELECT 1 FROM owner_accounts WHERE id=%s` - existence, no tenant."""
    with pytest.raises(ModelViolation, match="the account read must be"):
        parse_account_read("SELECT 1 FROM owner_accounts WHERE id=%s", (ACCOUNT_A,))


@pytest.mark.parametrize(
    "statement,expected",
    [
        ("SELECT a FROM owner_accounts oa JOIN contacts ct ON x FOR SHARE OF oa,ct",
         {"owner_accounts", "contacts"}),
        ("SELECT a FROM owner_accounts oa JOIN contacts ct ON x FOR SHARE OF ct,oa",
         {"owner_accounts", "contacts"}),
        ("SELECT a FROM owner_accounts oa JOIN contacts ct ON x FOR SHARE OF oa",
         {"owner_accounts"}),
        ("SELECT a FROM owner_accounts oa JOIN contacts ct ON x FOR SHARE OF ct",
         {"contacts"}),
        ("SELECT a FROM owner_accounts oa JOIN contacts ct ON x FOR SHARE",
         {"owner_accounts", "contacts"}),
        ("SELECT agency_id FROM properties WHERE id=%s FOR SHARE", {"properties"}),
        ("SELECT agency_id FROM properties WHERE id=%s", set()),
    ],
)
def test_24_the_lock_reader_resolves_aliases_in_either_order(statement, expected):
    assert locked_tables(statement) == expected
