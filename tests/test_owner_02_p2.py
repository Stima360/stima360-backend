from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from owner.dependencies import current_owner
from owner.router_admin import router as admin_router
from owner.router_portal import router as portal_router
from owner.schemas import FeedbackCreate, SharedDocumentCreate, VisitFeedbackCreate
from owner import repository as repo
from core.exceptions import ConflictError

R = Path(__file__).parents[1]


def test_p1_migration_untouched_hash_shape():
    text = (R / "migrations/010_owner_02_p1.sql").read_text()
    assert "CREATE TABLE owner_shared_documents" in text
    assert "CREATE TABLE owner_document_reads" in text
    assert "CREATE TABLE owner_visit_feedback_publications" in text


def test_p2_routes_declared():
    app = FastAPI()
    app.include_router(admin_router)
    app.include_router(portal_router)
    paths = set(app.openapi()["paths"])
    expected = {
        "/api/owner/admin/documents",
        "/api/owner/admin/documents/{i}/publish",
        "/api/owner/admin/documents/{i}/revoke",
        "/api/owner/admin/documents/{i}/supersede",
        "/api/owner/admin/visit-feedback",
        "/api/owner/admin/visit-feedback/{i}/publish",
        "/api/owner/admin/feedback/{i}",
        "/api/owner/portal/properties/{p}/documents",
        "/api/owner/portal/documents/{i}",
        "/api/owner/portal/documents/{i}/acknowledge",
        "/api/owner/portal/properties/{p}/visit-feedback",
    }
    assert expected <= paths


def test_availability_validation():
    now = datetime.now(timezone.utc)
    with pytest.raises(ValidationError):
        FeedbackCreate(
            feedback_type="availability_update", subject="Disponibilità", message="Test",
            availability_from=now, availability_to=now - timedelta(hours=1)
        )
    with pytest.raises(ValidationError):
        FeedbackCreate(feedback_type="availability_update", subject="Disponibilità", message="Test")


def test_new_feedback_types_supported():
    for kind in ("price_review", "availability_update", "document_question"):
        kwargs = {}
        if kind == "availability_update":
            kwargs["availability_from"] = datetime.now(timezone.utc)
        assert FeedbackCreate(feedback_type=kind, subject="Oggetto", message="Messaggio", **kwargs).feedback_type == kind


def test_shared_document_schema_limits():
    with pytest.raises(ValidationError):
        SharedDocumentCreate(property_document_id=1, public_title="", public_document_type="ape")


def test_visit_feedback_schema_limits():
    with pytest.raises(ValidationError):
        VisitFeedbackCreate(property_visit_id=1, category="other", public_summary="x")


def test_portal_http_isolation_and_reads(monkeypatch):
    app = FastAPI()
    app.include_router(portal_router)
    app.dependency_overrides[current_owner] = lambda: {"owner_account_id": 7, "expires_at": "2099-01-01T00:00:00Z"}
    monkeypatch.setattr(repo, "portal_shared_documents", lambda account, prop: [{"id": 3}] if (account, prop) == (7, 11) else [])
    monkeypatch.setattr(repo, "portal_shared_document", lambda account, doc: {"id": doc, "property_id": 11, "url": "https://example.test/doc"} if (account, doc) == (7, 3) else (_ for _ in ()).throw(Exception()))
    monkeypatch.setattr(repo, "read_shared_document", lambda account, doc, ack=False: {"shared_document_id": doc, "owner_account_id": account, "acknowledged_at": "now" if ack else None})
    c = TestClient(app)
    assert c.get("/api/owner/portal/properties/11/documents").json()["items"] == [{"id": 3}]
    assert c.get("/api/owner/portal/documents/3").status_code == 200
    assert c.post("/api/owner/portal/documents/3/acknowledge").json()["acknowledged_at"] == "now"
    assert c.get("/api/owner/portal/documents/99").status_code == 404


def test_admin_http_document_lifecycle(monkeypatch):
    app = FastAPI()
    app.include_router(admin_router)
    monkeypatch.setattr(repo, "create_shared_document", lambda d: {"id": 1, **d, "status": "draft"})
    monkeypatch.setattr(repo, "publish_shared_document", lambda i: {"id": i, "status": "published"})
    c = TestClient(app)
    payload = {"property_document_id": 9, "public_title": "APE", "public_document_type": "ape"}
    assert c.post("/api/owner/admin/documents", json=payload).status_code == 201
    assert c.post("/api/owner/admin/documents/1/publish").json()["status"] == "published"


def test_immutable_document_guard(monkeypatch):
    monkeypatch.setattr(repo, "get_shared_document", lambda i: {"id": i, "status": "published"})
    with pytest.raises(ConflictError):
        repo.update_shared_document(1, {"public_title": "nuovo"})


def test_sensitive_source_fields_not_selected_for_portal():
    source = (R / "owner/repository.py").read_text()
    portal_query = source[source.index("def portal_shared_document"):source.index("def read_shared_document")]
    assert "storage_key" not in portal_query
    assert "metadata" not in portal_query
    assert "notes" not in portal_query


def test_audit_actions_for_p2():
    source = (R / "owner/repository.py").read_text()
    for action in (
        "shared_document_created", "shared_document_published", "shared_document_revoked",
        "shared_document_viewed", "shared_document_acknowledged", "visit_feedback_published",
        "feedback_status_updated",
    ):
        assert action in source
