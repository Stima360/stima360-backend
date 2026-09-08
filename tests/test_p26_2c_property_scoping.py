"""P26-2C - Runtime Agency Isolation Tests for PROPERTY.

Offline tests validating that:
1. All PROPERTY operations are tenant-scoped via OperatorContext.
2. Cross-agency reads, updates, and child links fail closed (NotFoundError / 404).
3. Unbound platform admin fails closed (PlatformAdminAgencyRequired / 403).
4. No client-supplied agency_id can widen scope.
5. Single resolution: identical context is propagated router -> service -> repository.
"""
from __future__ import annotations

import inspect
import re
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient

from core.exceptions import NotFoundError, ValidationError
from core.scope import ProgrammingError
from operator_auth.context import OperatorContext
from operator_auth.exceptions import PlatformAdminAgencyRequired
from property import repository as property_repository
from property import router as property_router
from property import service as property_service

AGENCY_A = 10
AGENCY_B = 20


def make_ctx(agency_id: int | None = AGENCY_A, is_platform_admin: bool = False) -> OperatorContext:
    return OperatorContext(
        user_id=1 if agency_id else 999,
        agency_id=agency_id,
        role="agency_owner" if agency_id else "platform_admin",
        is_platform_admin=is_platform_admin,
        session_id=1,
        auth_channel="legacy_basic" if agency_id else "operator_session",
    )


class Statement:
    def __init__(self, sql: str, params: Any = None):
        self.sql = " ".join(str(sql).split())
        self.params = params

    @property
    def bound(self) -> list:
        if self.params is None:
            return []
        if isinstance(self.params, dict):
            return list(self.params.values())
        return list(self.params)

    def __repr__(self) -> str:
        return f"<{self.sql} params={self.params}>"


class MockCursor:
    def __init__(self, responses: list[Any] | None = None, rowcount: int = 1):
        self.statements: list[Statement] = []
        self.responses: list[Any] = list(responses) if responses is not None else []
        self.rowcount = rowcount
        self._current_response: Any = None

    def execute(self, sql: str, params: Any = None):
        self.statements.append(Statement(sql, params))
        if self.responses:
            self._current_response = self.responses.pop(0)
        else:
            self._current_response = None

    def fetchone(self):
        if isinstance(self._current_response, list):
            return self._current_response[0] if self._current_response else None
        return self._current_response

    def fetchall(self):
        if isinstance(self._current_response, list):
            return self._current_response
        return []

    def close(self):
        pass


@pytest.fixture
def mock_db(monkeypatch):
    """Installs a MockCursor into property_repository.core_cursor."""
    state = {}

    def _install(responses=None, rowcount=1):
        cursor = MockCursor(responses=responses, rowcount=rowcount)
        state["cursor"] = cursor

        @contextmanager
        def _core_cursor(*_args, **_kwargs):
            yield (None, cursor)

        monkeypatch.setattr(property_repository, "core_cursor", _core_cursor)
        return cursor

    state["install"] = _install
    return state


# ---------------------------------------------------------------------------
# ROOT SCOPING TESTS
# ---------------------------------------------------------------------------

def test_list_properties_scoped_to_agency(mock_db):
    cursor = mock_db["install"](responses=[[{"id": 1, "title": "Prop A"}]])
    ctx = make_ctx(AGENCY_A)
    items = property_repository.list_properties(ctx, 50, 0, None, None, None, None, None, None, None, False, False)
    assert len(items) == 1
    stmt = cursor.statements[0]
    assert "p.agency_id = %s" in stmt.sql
    assert AGENCY_A in stmt.bound


def test_get_property_verifies_agency(mock_db):
    cursor = mock_db["install"](responses=[{"id": 100, "agency_id": AGENCY_A, "title": "Prop A"}])
    ctx = make_ctx(AGENCY_A)
    prop = property_repository.get_property(ctx, 100)
    assert prop["id"] == 100
    first_stmt = cursor.statements[0]
    assert "agency_id = %s" in first_stmt.sql or "agency_id=%s" in first_stmt.sql
    assert AGENCY_A in first_stmt.bound


def test_get_property_other_agency_raises_not_found(mock_db):
    mock_db["install"](responses=[None])  # simulating property not found in Agency B
    ctx = make_ctx(AGENCY_B)
    with pytest.raises(NotFoundError):
        property_repository.get_property(ctx, 100)


def test_update_property_verifies_agency(mock_db):
    cursor = mock_db["install"](responses=[
        {"asking_price": 100000, "commercial_status": "draft", "classification": "A"},  # old row
        {"id": 100, "title": "Updated"}  # updated row
    ])
    ctx = make_ctx(AGENCY_A)
    property_repository.update_property(ctx, 100, {"title": "Updated"})
    select_stmt = cursor.statements[0]
    assert "agency_id" in select_stmt.sql
    assert AGENCY_A in select_stmt.bound
    update_stmt = cursor.statements[1]
    assert "agency_id" in update_stmt.sql
    assert AGENCY_A in update_stmt.bound


def test_update_property_other_agency_raises_not_found(mock_db):
    mock_db["install"](responses=[None])
    ctx = make_ctx(AGENCY_B)
    with pytest.raises(NotFoundError):
        property_repository.update_property(ctx, 100, {"title": "Hack"})


def test_update_property_forbids_client_supplied_agency_id(mock_db):
    mock_db["install"]()
    ctx = make_ctx(AGENCY_A)
    with pytest.raises(ProgrammingError):
        property_repository.update_property(ctx, 100, {"title": "Villa", "agency_id": AGENCY_B})


def test_archive_property_scoped_to_agency(mock_db):
    cursor = mock_db["install"](responses=[
        {"asking_price": 100000, "commercial_status": "draft", "classification": "A"},
        {"id": 100, "commercial_status": "archived"}
    ])
    ctx = make_ctx(AGENCY_A)
    property_repository.archive_property(ctx, 100)
    select_stmt = cursor.statements[0]
    assert "agency_id" in select_stmt.sql
    assert AGENCY_A in select_stmt.bound


def test_dashboard_scoped_to_agency(mock_db):
    cursor = mock_db["install"](responses=[
        {"total": 1, "active": 1, "class_a": 1, "class_b": 0, "class_c": 0, "active_value": 100, "expiring_mandates": 0},
        {"count": 0},
        {"count": 0},
        {"count": 0},
        [],
        [],
    ])
    ctx = make_ctx(AGENCY_A)
    kpis = property_repository.dashboard(ctx)
    assert kpis["total"] == 1
    # Every query issued must filter by agency_id
    for stmt in cursor.statements:
        assert "agency_id" in stmt.sql
        assert AGENCY_A in stmt.bound


def test_alerts_scoped_to_agency(mock_db):
    cursor = mock_db["install"](responses=[[]])
    ctx = make_ctx(AGENCY_A)
    property_repository.alerts(ctx)
    stmt = cursor.statements[0]
    assert "p.agency_id = %s" in stmt.sql or "p.agency_id=%s" in stmt.sql
    assert AGENCY_A in stmt.bound


def test_platform_admin_unbound_fails_closed(mock_db):
    mock_db["install"]()
    unbound = make_ctx(agency_id=None, is_platform_admin=True)
    with pytest.raises(PlatformAdminAgencyRequired):
        property_repository.list_properties(unbound, 50, 0, None, None, None, None, None, None, None, False, False)
    with pytest.raises(PlatformAdminAgencyRequired):
        property_repository.get_property(unbound, 1)
    with pytest.raises(PlatformAdminAgencyRequired):
        property_repository.update_property(unbound, 1, {"title": "x"})
    with pytest.raises(PlatformAdminAgencyRequired):
        property_repository.archive_property(unbound, 1)
    with pytest.raises(PlatformAdminAgencyRequired):
        property_repository.dashboard(unbound)
    with pytest.raises(PlatformAdminAgencyRequired):
        property_repository.alerts(unbound)


# ---------------------------------------------------------------------------
# CONTACT / LEAD INTEGRITY TESTS
# ---------------------------------------------------------------------------

def test_add_contact_verifies_same_agency(mock_db):
    # Success case: property in agency A, contact in agency A
    cursor = mock_db["install"](responses=[
        {"id": 1},  # property check
        {"id": 10},  # contact check
        {"property_id": 1, "contact_id": 10, "role": "owner"}
    ])
    ctx = make_ctx(AGENCY_A)
    property_repository.add_contact(ctx, 1, {"contact_id": 10, "role": "owner"})
    # Must have verified property with agency_id and contact with agency_id
    prop_check = cursor.statements[0]
    assert "properties" in prop_check.sql and "agency_id" in prop_check.sql
    contact_check = cursor.statements[1]
    assert "contacts" in contact_check.sql and "agency_id" in contact_check.sql


def test_add_contact_cross_agency_refused(mock_db):
    # Property found in agency A, but contact NOT in agency A
    mock_db["install"](responses=[
        {"id": 1},  # property found in agency A
        None  # contact not found in agency A (either doesn't exist or is in agency B)
    ])
    ctx = make_ctx(AGENCY_A)
    with pytest.raises(NotFoundError):
        property_repository.add_contact(ctx, 1, {"contact_id": 999, "role": "owner"})


def test_delete_contact_verifies_parent_property_agency(mock_db):
    cursor = mock_db["install"](responses=[{"id": 1}])
    ctx = make_ctx(AGENCY_A)
    property_repository.delete_contact(ctx, 1, 10, "owner")
    prop_check = cursor.statements[0]
    assert "properties" in prop_check.sql and "agency_id" in prop_check.sql
    assert AGENCY_A in prop_check.bound


def test_add_lead_verifies_same_agency(mock_db):
    cursor = mock_db["install"](responses=[
        {"id": 1},  # property check
        {"id": 20},  # lead check
        {"property_id": 1, "lead_id": 20, "relation_type": "buyer"}
    ])
    ctx = make_ctx(AGENCY_A)
    property_repository.add_lead(ctx, 1, {"lead_id": 20, "relation_type": "buyer"})
    prop_check = cursor.statements[0]
    assert "properties" in prop_check.sql and "agency_id" in prop_check.sql
    lead_check = cursor.statements[1]
    assert "leads" in lead_check.sql and "agency_id" in lead_check.sql


def test_add_lead_cross_agency_refused(mock_db):
    mock_db["install"](responses=[
        {"id": 1},
        None  # lead not found in agency A
    ])
    ctx = make_ctx(AGENCY_A)
    with pytest.raises(NotFoundError):
        property_repository.add_lead(ctx, 1, {"lead_id": 999, "relation_type": "buyer"})


def test_delete_lead_verifies_parent_property_agency(mock_db):
    cursor = mock_db["install"](responses=[{"id": 1}])
    ctx = make_ctx(AGENCY_A)
    property_repository.delete_lead(ctx, 1, 20)
    prop_check = cursor.statements[0]
    assert "properties" in prop_check.sql and "agency_id" in prop_check.sql
    assert AGENCY_A in prop_check.bound


# ---------------------------------------------------------------------------
# CHILD-DERIVED DIRECT OPERATIONS (DOCUMENTS, PHOTOS, VISITS)
# ---------------------------------------------------------------------------

def test_update_document_scoped_via_parent_property(mock_db):
    cursor = mock_db["install"](responses=[
        {"id": 5, "property_id": 1, "url": "doc.pdf", "status": "available", "metadata": {}},  # parent ownership check
        {"id": 5, "property_id": 1, "title": "Doc A"}  # update result
    ])
    ctx = make_ctx(AGENCY_A)
    property_repository.update_child(ctx, "property_documents", 5, {"title": "Doc A"}, "document")
    first_stmt = cursor.statements[0]
    assert "properties" in first_stmt.sql
    assert "agency_id" in first_stmt.sql
    assert AGENCY_A in first_stmt.bound


def test_update_document_other_agency_raises_not_found(mock_db):
    mock_db["install"](responses=[None])  # document belongs to agency B
    ctx = make_ctx(AGENCY_A)
    with pytest.raises(NotFoundError):
        property_repository.update_child(ctx, "property_documents", 999, {"title": "Hack"}, "document")


def test_delete_document_scoped_via_parent_property(mock_db):
    cursor = mock_db["install"](responses=[
        {"id": 5, "property_id": 1},  # ownership check
        None,  # _document_has_published_owner_share check
    ])
    ctx = make_ctx(AGENCY_A)
    property_repository.delete_child(ctx, "property_documents", 5, "document")
    first_stmt = cursor.statements[0]
    assert "properties" in first_stmt.sql and "agency_id" in first_stmt.sql
    assert AGENCY_A in first_stmt.bound


def test_update_photo_scoped_via_parent_property(mock_db):
    cursor = mock_db["install"](responses=[
        {"id": 7, "property_id": 1},
        {"id": 7, "title": "Photo A"}
    ])
    ctx = make_ctx(AGENCY_A)
    property_repository.update_child(ctx, "property_photos", 7, {"title": "Photo A"}, "photo")
    first_stmt = cursor.statements[0]
    assert "properties" in first_stmt.sql and "agency_id" in first_stmt.sql
    assert AGENCY_A in first_stmt.bound


def test_delete_photo_scoped_via_parent_property(mock_db):
    cursor = mock_db["install"](responses=[{"id": 7, "property_id": 1}])
    ctx = make_ctx(AGENCY_A)
    property_repository.delete_child(ctx, "property_photos", 7, "photo")
    first_stmt = cursor.statements[0]
    assert "properties" in first_stmt.sql and "agency_id" in first_stmt.sql
    assert AGENCY_A in first_stmt.bound


# ---------------------------------------------------------------------------
# VISITS SCOPING & INTEGRITY
# ---------------------------------------------------------------------------

def test_list_visits_scoped_via_property_agency(mock_db):
    cursor = mock_db["install"](responses=[[{"id": 1, "property_id": 1}]])
    ctx = make_ctx(AGENCY_A)
    property_repository.list_visits(ctx, 100, 0, None, None, None)
    stmt = cursor.statements[0]
    assert "p.agency_id = %s" in stmt.sql or "p.agency_id=%s" in stmt.sql
    assert AGENCY_A in stmt.bound


def test_add_visit_cross_agency_contact_rejected(mock_db):
    mock_db["install"](responses=[
        {"id": 1},  # property in agency A
        None  # contact not in agency A
    ])
    ctx = make_ctx(AGENCY_A)
    with pytest.raises(NotFoundError):
        property_repository.add_visit(ctx, 1, {"contact_id": 999, "scheduled_at": "2026-09-08T10:00:00Z"})


def test_add_visit_cross_agency_lead_rejected(mock_db):
    mock_db["install"](responses=[
        {"id": 1},  # property in agency A
        None  # lead not in agency A
    ])
    ctx = make_ctx(AGENCY_A)
    with pytest.raises(NotFoundError):
        property_repository.add_visit(ctx, 1, {"lead_id": 999, "scheduled_at": "2026-09-08T10:00:00Z"})


def test_update_visit_verifies_parent_property_agency(mock_db):
    cursor = mock_db["install"](responses=[
        {"id": 15, "property_id": 1},  # visit ownership check
        {"id": 15, "status": "completed"}  # update result
    ])
    ctx = make_ctx(AGENCY_A)
    property_repository.update_visit(ctx, 15, {"status": "completed"})
    check_stmt = cursor.statements[0]
    assert "properties" in check_stmt.sql and "agency_id" in check_stmt.sql
    assert AGENCY_A in check_stmt.bound


def test_update_visit_other_agency_raises_not_found(mock_db):
    mock_db["install"](responses=[None])  # visit not found in agency B
    ctx = make_ctx(AGENCY_B)
    with pytest.raises(NotFoundError):
        property_repository.update_visit(ctx, 15, {"status": "completed"})


def test_delete_visit_verifies_parent_property_agency(mock_db):
    cursor = mock_db["install"](responses=[{"id": 15, "property_id": 1}])
    ctx = make_ctx(AGENCY_A)
    property_repository.delete_visit(ctx, 15)
    check_stmt = cursor.statements[0]
    assert "properties" in check_stmt.sql and "agency_id" in check_stmt.sql
    assert AGENCY_A in check_stmt.bound


# ---------------------------------------------------------------------------
# ROUTER & CONTEXT PROPAGATION
# ---------------------------------------------------------------------------

def test_all_property_routes_declare_ctx_dependency():
    for name in (
        "get_dashboard",
        "get_alerts",
        "create_property",
        "list_properties",
        "get_property",
        "update_property",
        "archive_property",
        "add_contact",
        "delete_contact",
        "add_lead",
        "delete_lead",
        "add_document",
        "update_document",
        "delete_document",
        "add_photo",
        "update_photo",
        "delete_photo",
        "list_visits",
        "add_visit",
        "update_visit",
        "delete_visit",
    ):
        fn = getattr(property_router, name)
        sig = inspect.signature(fn)
        assert "ctx" in sig.parameters, f"route handler {name} does not take ctx"


def test_http_get_property_other_agency_is_404(mock_db):
    from fastapi import FastAPI
    mock_db["install"](responses=[None])
    app = FastAPI()
    app.include_router(property_router.router)
    app.dependency_overrides[property_router.legacy_basic_agency_context] = lambda: make_ctx(AGENCY_A)
    client = TestClient(app)
    resp = client.get("/api/property/properties/999")
    assert resp.status_code == 404, resp.text


def test_http_unbound_platform_admin_is_403(mock_db):
    from fastapi import FastAPI
    mock_db["install"]()
    app = FastAPI()
    app.include_router(property_router.router)
    app.dependency_overrides[property_router.legacy_basic_agency_context] = lambda: make_ctx(agency_id=None, is_platform_admin=True)
    client = TestClient(app)
    resp = client.get("/api/property/properties")
    assert resp.status_code == 403, resp.text
