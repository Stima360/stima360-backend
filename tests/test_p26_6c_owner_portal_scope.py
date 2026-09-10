"""P26-6C OWNER Portal - a grant may only be followed when it is coherent.

The portal is account-scoped and stays that way. Its identity is the session
cookie, not an operator context, and it is not being turned into one: an owner
is not an operator and must not acquire one's surface.

What changes is narrower. Every portal read reaches its rows by following
`owner_property_access` - the portal's whole authorisation model is "this
account holds an active grant on this property". A grant written before
`create_access` started checking can link an account of one agency to a
property of another, and the portal would follow it faithfully: the queries
were not at fault, the grant was.

So every portal query that joins that table now also requires the grant's two
roots - the account's contact and the property - to name the SAME agency:

    JOIN owner_accounts oa_g ON oa_g.id=x.owner_account_id
    JOIN contacts ct_g ON ct_g.id=oa_g.contact_id
    JOIN properties p_g ON p_g.id=x.property_id
    ... AND ct_g.agency_id=p_g.agency_id

There is no agency parameter. Nothing a client sends reaches this, and there is
nothing to widen: the rule compares two columns of the database against each
other. Incoherent rows are hidden, never repaired - repointing or deleting them
is a data decision, not something a portal read should do.

WHAT THIS FILE PROVES

That every grant-following query carries the constraint, that the portal
refuses the property behind an incoherent grant, and that the portal's own
authentication is untouched. It does not run PostgreSQL.
"""
from __future__ import annotations

import ast
import inspect
import re
from contextlib import contextmanager
from pathlib import Path

import pytest

from core.exceptions import NotFoundError
from owner import repository


ROOT = Path(__file__).resolve().parents[1]

AGENCY_A = 901
AGENCY_B = 902

ACCOUNT_A = 1
PROPERTY_A = 101          # agency A, granted to ACCOUNT_A coherently
PROPERTY_B = 102          # agency B, granted to ACCOUNT_A by a legacy row


# Every portal entry point that follows a grant, with the arguments it takes.
GRANT_FOLLOWERS = {
    "require_property": lambda p: repository.require_property(ACCOUNT_A, p),
    "portal_properties": lambda p: repository.portal_properties(ACCOUNT_A),
    "publication": lambda p: repository.publication(ACCOUNT_A, 1),
    "portal_shared_documents": lambda p: repository.portal_shared_documents(ACCOUNT_A, p),
    "portal_shared_document": lambda p: repository.portal_shared_document(ACCOUNT_A, 1),
    "portal_visit_feedback_detail": lambda p: repository.portal_visit_feedback_detail(ACCOUNT_A, 1),
    "portal_notifications": lambda p: repository.portal_notifications(ACCOUNT_A),
    "mark_notification_read": lambda p: repository.mark_notification_read(ACCOUNT_A, 1),
    "create_feedback": lambda p: repository.create_feedback(
        ACCOUNT_A, p, {"feedback_type": "general_message", "subject": "s", "message": "m"}
    ),
}


# ---------------------------------------------------------------------------
# A model that follows grants only when their two roots agree.
# ---------------------------------------------------------------------------

_ROOTS_AGREE = re.compile(r"ct_g\.agency_id\s*=\s*p_g\.agency_id", re.IGNORECASE)

# property -> the agency of the property side of ACCOUNT_A's grant on it.
# The account side is always agency A: that is the account under test.
GRANT_PROPERTY_AGENCY = {PROPERTY_A: AGENCY_A, PROPERTY_B: AGENCY_B}


# One wide row, so a DTO builder never fails on a missing key instead of on
# the isolation assertion the test is making.
_ROW = {"id": 1, "contact_id": 11, "agency_id": AGENCY_A,
                       "property_id": None, "status": "published", "title": "t",
                       "notification_type": "publication_published", "body": "b",
                       "created_at": None, "read_at": None, "acknowledged_at": None,
                       "target_type": "owner_publication", "target_id": 1,
                       "address": "a", "city": "c", "access_role": "owner",
                       "is_primary": True, "public_title": "t",
                       "public_document_type": "ape", "version_number": 1,
                       "published_at": None, "expires_at": None,
                       "acknowledgement_required": False, "source_status": "available",
                       "source_title": "t", "source_metadata": {}, "storage_key": "k",
                       "category": "price", "public_summary": "ok", "sentiment": None,
                       "subject": "s", "message": "m", "feedback_type": "general_message",
                       "submitted_at": None, "handled_at": None, "public_response": None,
                       "availability_from": None, "availability_to": None,
                       "summary": None, "publication_type": "general_update"}


class Recorder:
    def __init__(self):
        self.statements: list[tuple[str, tuple]] = []

    def touching_grants(self) -> list[str]:
        return [s for s, _ in self.statements
                if re.search(r"\bowner_property_access\b", s, re.I)]


class Cursor:
    def __init__(self, recorder: Recorder, followed_property: int):
        self.recorder = recorder
        self.followed = followed_property
        self._rows: list[dict] = []

    def execute(self, sql, params=None):
        flat = " ".join(str(sql).split())
        self.recorder.statements.append((flat, tuple(params or ())))

        if not re.search(r"\bowner_property_access\b", flat, re.I):
            # Anything not following a grant answers with a harmless row.
            self._rows = [dict(_ROW, property_id=self.followed)]
            return

        # A grant is followed only when the statement requires its two roots to
        # agree AND they actually do. Modelled this way round so that removing
        # the constraint from a query makes the incoherent grant visible here.
        constrained = bool(_ROOTS_AGREE.search(flat))
        coherent = GRANT_PROPERTY_AGENCY.get(self.followed) == AGENCY_A
        if constrained and not coherent:
            self._rows = []
            return
        self._rows = [dict(_ROW, property_id=self.followed)]

    @property
    def rowcount(self):
        return len(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def close(self):
        pass


@pytest.fixture
def portal(monkeypatch):
    """Installs the model; `use(property_id)` chooses which grant is followed."""
    recorder = Recorder()
    state = {"property": PROPERTY_A}

    @contextmanager
    def fake_core_cursor(*, commit: bool = False):
        cursor = Cursor(recorder, state["property"])
        try:
            yield object(), cursor
        finally:
            cursor.close()

    monkeypatch.setattr(repository, "core_cursor", fake_core_cursor)

    def use(property_id):
        state["property"] = property_id
        recorder.statements.clear()
        return recorder

    recorder.use = use
    return recorder


# ---------------------------------------------------------------------------
# 1-3 - the constraint is present, and it is the same one everywhere
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(GRANT_FOLLOWERS))
def test_1_every_grant_following_query_requires_the_two_roots_to_agree(portal, name):
    portal.use(PROPERTY_A)
    try:
        GRANT_FOLLOWERS[name](PROPERTY_A)
    except NotFoundError:
        pass
    grants = portal.touching_grants()
    assert grants, f"{name} followed no grant"
    for statement in grants:
        assert _ROOTS_AGREE.search(statement), (name, statement)
        assert "join contacts ct_g" in statement.lower(), (name, statement)
        assert "join properties p_g" in statement.lower(), (name, statement)


def test_2_the_constraint_is_written_once_and_reused():
    """One fragment, so nine queries cannot drift into nine dialects."""
    source = (ROOT / "owner" / "repository.py").read_text(encoding="utf-8")
    assert "_COHERENT_GRANT = " in source
    assert "_GRANT_ROOTS_AGREE = " in source
    assert source.count("ct_g.agency_id=p_g.agency_id") >= 2


def test_3_the_constraint_takes_no_parameter(portal):
    """It compares two columns. There is nothing for a client to widen."""
    portal.use(PROPERTY_A)
    repository.portal_properties(ACCOUNT_A)
    statement, params = portal.statements[0]
    assert _ROOTS_AGREE.search(statement), statement
    assert params == (ACCOUNT_A,), params
    assert "%s" not in statement.split("ct_g.agency_id")[1][:40], statement


# ---------------------------------------------------------------------------
# 4-5 - what the constraint actually refuses
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(GRANT_FOLLOWERS))
def test_4_a_property_behind_an_incoherent_grant_is_not_reachable(portal, name):
    """The account is agency A's; the grant points at a property of agency B.

    `create_access` can no longer create such a row, but rows already stored
    can. The portal must not follow one.
    """
    portal.use(PROPERTY_B)
    result = None
    try:
        result = GRANT_FOLLOWERS[name](PROPERTY_B)
    except NotFoundError:
        return
    assert result in (None, [], {}), (name, result)


@pytest.mark.parametrize("name", sorted(GRANT_FOLLOWERS))
def test_5_a_coherent_grant_is_still_followed(portal, name):
    """The other direction, so the rule is not "the portal refuses everything"."""
    portal.use(PROPERTY_A)
    result = GRANT_FOLLOWERS[name](PROPERTY_A)
    assert result not in (None, [],), (name, result)


# ---------------------------------------------------------------------------
# 6-8 - the portal keeps its own authentication and its own shape
# ---------------------------------------------------------------------------

def test_6_the_portal_router_has_no_operator_context():
    """An owner is not an operator. The portal must not acquire that surface."""
    source = (ROOT / "owner" / "router_portal.py").read_text(encoding="utf-8")
    assert "legacy_basic_agency_context" not in source
    assert "basic_only_agency_context" not in source
    assert "OperatorContext" not in source
    assert "require_owner_admin" not in source
    assert "current_owner" in source


def test_7_every_portal_route_still_derives_the_account_from_the_session():
    """`current_owner` reads the cookie; no route may take an account, or an
    agency, from the client instead."""
    tree = ast.parse((ROOT / "owner" / "router_portal.py").read_text(encoding="utf-8"))
    routed, session_bound = set(), set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not any(isinstance(d, ast.Call) and getattr(d.func, "attr", None)
                   in {"get", "post", "patch", "put", "delete"}
                   for d in node.decorator_list):
            continue
        routed.add(node.name)
        for default in node.args.defaults:
            if (isinstance(default, ast.Call)
                    and getattr(default.func, "id", None) == "Depends"
                    and default.args
                    and getattr(default.args[0], "id", None) == "current_owner"):
                session_bound.add(node.name)
        for argument in node.args.args:
            assert argument.arg not in {"agency_id", "owner_account_id", "account_id"}, (
                f"{node.name} takes {argument.arg} from the client"
            )

    # The two that legitimately have no session yet: consuming a token, and
    # logging out. Everything else is session-bound.
    assert routed - session_bound == {"login", "logout"}, sorted(routed - session_bound)


def test_8_the_session_and_token_lookups_are_unchanged():
    """They are keyed by a hash and carry no tenant by construction - the
    account they resolve to IS the identity. Asserted so a later edit does not
    quietly add a client-supplied filter to them."""
    for name in ("get_session", "consume_token", "revoke_session"):
        source = inspect.getsource(getattr(repository, name))
        assert "hash_secret(raw)" in source, name
        assert "agency_id" not in source, name
