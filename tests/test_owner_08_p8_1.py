from __future__ import annotations

import ast
import runpy
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from core.exceptions import NotFoundError
from owner import admin_lookup_repository as lookup_repo
from owner.admin_lookup_schemas import (
    ContactLookupDTO,
    DocumentLookupDTO,
    PropertyLookupDTO,
    VisitLookupDTO,
)
from owner.router_admin import router as admin_router


ROOT = Path(__file__).resolve().parents[1]
LOOKUP_REPO = ROOT / "owner" / "admin_lookup_repository.py"
LOOKUP_ROUTER = ROOT / "owner" / "router_admin_lookups.py"
ADMIN_ROUTER = ROOT / "owner" / "router_admin.py"
INDEX = ROOT / "static" / "owner_admin" / "index.html"
APP_JS = ROOT / "static" / "owner_admin" / "assets" / "app.js"


def _p7_runner():
    return runpy.run_path(str(ROOT / "tests" / "test_owner_07_p7.py"))["_run_node"]


class FakeCursor:
    def __init__(self, fetchone_rows=None, fetchall_rows=None):
        self.fetchone_rows = list(fetchone_rows or [])
        self.fetchall_rows = list(fetchall_rows or [])
        self.executed: list[tuple[str, tuple | None]] = []

    def execute(self, query, params=None):
        self.executed.append((" ".join(str(query).split()), params))

    def fetchone(self):
        return self.fetchone_rows.pop(0) if self.fetchone_rows else None

    def fetchall(self):
        return list(self.fetchall_rows)


def _fake_cursor(monkeypatch, cursor: FakeCursor):
    @contextmanager
    def cm(*, commit=False):
        assert commit is False
        yield object(), cursor

    monkeypatch.setattr(lookup_repo, "core_cursor", cm)


def test_p81_policy_and_lookup_dtos_are_exact_whitelists():
    assert lookup_repo.OWNER_ELIGIBLE_PROPERTY_CONTACT_ROLES == frozenset({"owner"})
    assert set(ContactLookupDTO.model_fields) == {"id", "display_name", "email"}
    assert set(PropertyLookupDTO.model_fields) == {"id", "code", "title", "address", "city"}
    assert set(DocumentLookupDTO.model_fields) == {"id", "title", "document_type", "status", "expires_at"}
    assert set(VisitLookupDTO.model_fields) == {"id", "scheduled_at", "status"}
    with pytest.raises(ValidationError):
        ContactLookupDTO(id=1, display_name="Mario", email=None, internal_notes="secret")
    with pytest.raises(ValidationError):
        DocumentLookupDTO(id=1, title="APE", document_type="ape", status="available", storage_key="secret")


def test_contact_lookup_selects_only_whitelist_and_never_crm_dump(monkeypatch):
    cursor = FakeCursor(fetchall_rows=[{"id": 7, "display_name": "Mario Rossi", "email": "mario@example.test"}])
    _fake_cursor(monkeypatch, cursor)
    result = lookup_repo.lookup_contacts("Mario", 25)
    assert result == [{"id": 7, "display_name": "Mario Rossi", "email": "mario@example.test"}]
    query, params = cursor.executed[0]
    assert "SELECT id,display_name,email FROM contacts" in query
    assert "SELECT *" not in query.upper()
    assert params == ("%Mario%", "%Mario%", 25)
    for forbidden in ("phone", "notes", "metadata", "lead", "activity", "task"):
        assert forbidden not in query.lower()


def test_property_lookup_is_account_bound_and_role_owner_only(monkeypatch):
    cursor = FakeCursor(
        fetchone_rows=[{"contact_id": 41}],
        fetchall_rows=[{"id": 9, "code": "P9", "title": "Casa", "address": "Via Roma", "city": "Teramo"}],
    )
    _fake_cursor(monkeypatch, cursor)
    result = lookup_repo.lookup_account_properties(12)
    assert result[0]["id"] == 9
    assert cursor.executed[0][1] == (12,)
    query, params = cursor.executed[1]
    assert "pc.contact_id=%s" in query
    assert "pc.role=%s" in query
    assert params == (41, "owner")
    assert "seller" not in query and "tenant" not in query and "professional" not in query and "other" not in query
    assert "SELECT DISTINCT p.id,p.code,p.title,p.address,p.city" in query
    assert "p.*" not in query


@pytest.mark.parametrize("kind", ["documents", "visits"])
def test_child_lookup_rechecks_account_property_owner_eligibility_and_blocks_cross_property(monkeypatch, kind):
    denied = FakeCursor(fetchone_rows=[])
    _fake_cursor(monkeypatch, denied)
    fn = lookup_repo.lookup_property_documents if kind == "documents" else lookup_repo.lookup_property_visits
    with pytest.raises(NotFoundError):
        fn(12, 99)
    guard_query, guard_params = denied.executed[0]
    assert "oa.id=%s" in guard_query and "pc.property_id=%s" in guard_query and "pc.role=%s" in guard_query
    assert guard_params == (12, 99, "owner")
    assert len(denied.executed) == 1, "cross-property denial must happen before source data lookup"


def test_document_and_visit_queries_are_minimal_and_have_no_internal_fields(monkeypatch):
    doc_cursor = FakeCursor(fetchone_rows=[{"ok": 1}], fetchall_rows=[{"id": 3, "title": "APE", "document_type": "ape", "status": "available", "expires_at": None}])
    _fake_cursor(monkeypatch, doc_cursor)
    assert lookup_repo.lookup_property_documents(5, 7)[0]["id"] == 3
    doc_query = doc_cursor.executed[1][0]
    assert "SELECT id,title,document_type,status,expires_at FROM property_documents" in doc_query
    for forbidden in ("storage_key", "metadata", "notes", " url", "SELECT *"):
        assert forbidden.lower() not in doc_query.lower()

    visit_cursor = FakeCursor(fetchone_rows=[{"ok": 1}], fetchall_rows=[{"id": 4, "scheduled_at": "2026-08-18T10:00:00Z", "status": "completed"}])
    _fake_cursor(monkeypatch, visit_cursor)
    assert lookup_repo.lookup_property_visits(5, 7)[0]["id"] == 4
    visit_query = visit_cursor.executed[1][0]
    assert "SELECT id,scheduled_at,status FROM property_visits" in visit_query
    for forbidden in ("contact_id", "lead_id", "outcome", "feedback", "rating", "assigned_to", "created_by", "SELECT *"):
        assert forbidden.lower() not in visit_query.lower()


def test_lookup_backend_is_strictly_read_only_and_has_no_core_flow_side_effects():
    source = LOOKUP_REPO.read_text(encoding="utf-8") + LOOKUP_ROUTER.read_text(encoding="utf-8")
    upper = source.upper()
    for mutation in ("INSERT INTO", "UPDATE ", "DELETE FROM"):
        assert mutation not in upper
    for forbidden in ("activities", "tasks", "flow_events", "create_activity", "create_task", "/api/core", "/api/property"):
        assert forbidden not in source


def test_four_lookup_routes_are_mounted_under_owner_admin_and_inherit_basic_auth(monkeypatch):
    source = ADMIN_ROUTER.read_text(encoding="utf-8")
    assert "router_admin_lookups" in source
    assert "router.include_router(lookup_router)" in source

    app = FastAPI()
    app.include_router(admin_router)
    monkeypatch.setenv("ADMIN_USER", "giorgio")
    monkeypatch.setenv("ADMIN_PASS", "test-secret")
    called = False

    def fake_contacts(*_args):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(lookup_repo, "lookup_contacts", fake_contacts)
    response = TestClient(app).get("/api/owner/admin/lookups/contacts")
    assert response.status_code == 401
    assert response.json() == {"detail": "Non autorizzato"}
    assert called is False

    paths = set(app.openapi()["paths"])
    assert {
        "/api/owner/admin/lookups/contacts",
        "/api/owner/admin/lookups/accounts/{owner_account_id}/properties",
        "/api/owner/admin/lookups/accounts/{owner_account_id}/properties/{property_id}/documents",
        "/api/owner/admin/lookups/accounts/{owner_account_id}/properties/{property_id}/visits",
    } <= paths


def test_frontend_replaces_the_four_manual_id_touchpoints_and_keeps_safe_dom():
    html = INDEX.read_text(encoding="utf-8")
    js = APP_JS.read_text(encoding="utf-8")
    for element_id in (
        "account-contact-id",
        "access-owner-account-id",
        "access-property-id",
        "document-property-document-id",
        "visit-feedback-property-visit-id",
    ):
        assert f'<select id="{element_id}"' in html
        assert f'<input id="{element_id}"' not in html
    assert '<select id="document-property-id"' in html
    assert '<select id="visit-feedback-property-id"' in html
    assert "/lookups/contacts" in js
    assert "/lookups/accounts/${encodeURIComponent(accountId)}/properties" in js
    assert "/documents`" in js and "/visits`" in js
    for forbidden in ("localStorage", "sessionStorage", "document.cookie", "innerHTML", "Object.keys", "Object.entries", "/api/core", "/api/property"):
        assert forbidden not in js + html
    for forbidden in ("storage_key", "source_metadata", "linked_activity_id", "lead_id", "assigned_to"):
        assert forbidden not in js


def test_frontend_contact_lookup_uses_real_contact_id_and_xss_is_text_only():
    run_node = _p7_runner()
    out = run_node(
        {
            "GET /api/owner/admin/dashboard": [{"status": 200, "body": {"active_accounts": 0, "active_access": 0, "published": 0, "new_feedback": 0}}],
            "GET /api/owner/admin/accounts": [
                {"status": 200, "body": {"items": []}},
                {"status": 200, "body": {"items": []}},
            ],
            "GET /api/owner/admin/lookups/contacts?search=Mario&limit=50": [{"status": 200, "body": {"items": [{"id": 77, "display_name": "<img src=x onerror=alert(1)>", "email": "mario@example.test", "internal_notes": "NEVER"}]}}],
            "POST /api/owner/admin/accounts": [{"status": 201, "body": {"id": 5}}],
        },
        r"""
ids['admin-username'].value='u'; ids['admin-password'].value='p'; await ids['admin-login-form'].trigger('submit');
await ids['nav-accounts'].trigger('click');
ids['account-contact-search'].value='Mario'; await ids['account-contact-search-button'].trigger('click');
assert(ids['account-contact-id'].disabled===false,'contact select should enable');
const labels=flatten(ids['account-contact-id']);
assert(labels.includes('<img src=x onerror=alert(1)>'),'XSS text should remain literal text');
assert(!labels.includes('NEVER'),'unwhitelisted contact field rendered');
ids['account-contact-id'].value='77'; ids['account-language'].value='it'; await ids['account-create-form'].trigger('submit');
const post=calls.find(c=>c.method==='POST'&&c.url==='/api/owner/admin/accounts');
assert(JSON.parse(post.body).contact_id===77,'real contact_id not submitted');
""",
    )
    assert "SCENARIO_PASS" in out


def test_frontend_account_property_stale_guard_and_401_logout():
    run_node = _p7_runner()
    out = run_node(
        {
            "GET /api/owner/admin/dashboard": [{"status": 200, "body": {"active_accounts": 0, "active_access": 0, "published": 0, "new_feedback": 0}}],
            "GET /api/owner/admin/access": [{"status": 200, "body": {"items": []}}],
            "GET /api/owner/admin/lookups/accounts/10/properties": [{"status": 200, "defer": "oldProps", "body": {"items": [{"id": 99, "title": "Vecchio"}]}}],
            "GET /api/owner/admin/lookups/accounts/11/properties": [{"status": 200, "body": {"items": [{"id": 22, "title": "Nuovo"}]}}],
        },
        r"""
ids['admin-username'].value='u'; ids['admin-password'].value='p'; await ids['admin-login-form'].trigger('submit'); await ids['nav-access'].trigger('click');
ids['access-owner-account-id'].value='10'; const oldPromise=ids['access-owner-account-id'].trigger('change'); await sleepTick();
ids['access-owner-account-id'].value='11'; await ids['access-owner-account-id'].trigger('change');
assert(flatten(ids['access-property-id']).includes('Nuovo'),'new account properties missing');
deferred.oldProps(); await oldPromise; await sleepTick();
assert(!flatten(ids['access-property-id']).includes('Vecchio'),'stale property response populated UI');
""",
    )
    assert "SCENARIO_PASS" in out

    out401 = run_node(
        {
            "GET /api/owner/admin/dashboard": [{"status": 200, "body": {"active_accounts": 0, "active_access": 0, "published": 0, "new_feedback": 0}}],
            "GET /api/owner/admin/access": [{"status": 200, "body": {"items": []}}],
            "GET /api/owner/admin/lookups/accounts/10/properties": [{"status": 401, "body": {"detail": "RAW"}}],
        },
        r"""
ids['admin-username'].value='u'; ids['admin-password'].value='p'; await ids['admin-login-form'].trigger('submit'); await ids['nav-access'].trigger('click');
ids['access-owner-account-id'].value='10'; await ids['access-owner-account-id'].trigger('change');
assert(ids['admin-app'].hidden===true && ids['login-view'].hidden===false,'lookup 401 must logout locally');
assert(!ids['admin-login-status'].textContent.includes('RAW'),'raw backend detail leaked');
""",
    )
    assert "SCENARIO_PASS" in out401


def test_frontend_document_and_visit_cascades_use_account_bound_paths_and_invalidate_stale_sources():
    source = APP_JS.read_text(encoding="utf-8")
    assert "documentSourceLookupGeneration" in source and "visitSourceLookupGeneration" in source
    assert "state.documentSourceLookupGeneration += 1" in source
    assert "state.visitSourceLookupGeneration += 1" in source
    assert "resetLookupSelect(el.documentPropertyDocumentId, 'Seleziona prima un immobile', true)" in source
    assert "resetLookupSelect(el.visitFeedbackPropertyVisitId, 'Seleziona prima un immobile', true)" in source
    assert "`/lookups/accounts/${encodeURIComponent(accountId)}/properties/${encodeURIComponent(propertyId)}/documents`" in source
    assert "`/lookups/accounts/${encodeURIComponent(accountId)}/properties/${encodeURIComponent(propertyId)}/visits`" in source


def test_frontend_lookup_404_422_and_network_errors_are_controlled_and_recoverable():
    run_node = _p7_runner()
    out = run_node(
        {
            "GET /api/owner/admin/dashboard": [{"status": 200, "body": {"active_accounts": 0, "active_access": 0, "published": 0, "new_feedback": 0}}],
            "GET /api/owner/admin/accounts": [
                {"status": 200, "body": {"items": []}},
                {"status": 200, "body": {"items": []}},
            ],
            "GET /api/owner/admin/lookups/contacts?search=Bad&limit=50": [
                {"status": 422, "body": {"detail": "RAW PYDANTIC"}},
                {"network_error": True},
            ],
        },
        r"""
ids['admin-username'].value='u'; ids['admin-password'].value='p'; await ids['admin-login-form'].trigger('submit');
await ids['nav-accounts'].trigger('click');
ids['account-contact-search'].value='Bad'; await ids['account-contact-search-button'].trigger('click');
assert(ids['account-contact-lookup-status'].textContent==='Controlla i dati inseriti e riprova.','lookup 422 message');
assert(!ids['account-contact-lookup-status'].textContent.includes('RAW PYDANTIC'),'raw 422 leaked');
assert(ids['account-contact-id'].disabled===true,'failed lookup must not enable stale selection');
await ids['account-contact-search-button'].trigger('click');
assert(ids['account-contact-lookup-status'].textContent==='Errore di connessione. Riprova.','lookup network message');
assert(ids['account-contact-id'].disabled===true,'network failure must remain recoverable without stale data');
""",
    )
    assert "SCENARIO_PASS" in out

    out404 = run_node(
        {
            "GET /api/owner/admin/dashboard": [{"status": 200, "body": {"active_accounts": 0, "active_access": 0, "published": 0, "new_feedback": 0}}],
            "GET /api/owner/admin/access": [{"status": 200, "body": {"items": []}}],
            "GET /api/owner/admin/lookups/accounts/10/properties": [{"status": 404, "body": {"detail": "RAW INTERNAL"}}],
        },
        r"""
ids['admin-username'].value='u'; ids['admin-password'].value='p'; await ids['admin-login-form'].trigger('submit'); await ids['nav-access'].trigger('click');
ids['access-owner-account-id'].value='10'; await ids['access-owner-account-id'].trigger('change');
assert(ids['access-property-lookup-status'].textContent==='Risorsa non disponibile.','lookup 404 message');
assert(!ids['access-property-lookup-status'].textContent.includes('RAW INTERNAL'),'raw 404 leaked');
assert(ids['access-property-id'].disabled===true,'404 must not expose a cross-contact property choice');
""",
    )
    assert "SCENARIO_PASS" in out404
