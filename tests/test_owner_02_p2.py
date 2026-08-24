from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from owner.dependencies import current_owner
from owner.router_admin import require_owner_admin, router as admin_router
from owner.router_portal import router as portal_router
from owner.schemas import FeedbackCreate, FeedbackPublic, SharedDocumentCreate, VisitFeedbackCreate
from owner import repository as repo
from core.exceptions import ConflictError, NotFoundError

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
    app.dependency_overrides[require_owner_admin] = lambda: "test-admin"
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


# P6.7 blocking backend hardening --------------------------------------------
FEEDBACK_PUBLIC_KEYS = {
    "feedback_type",
    "subject",
    "message",
    "status",
    "submitted_at",
    "availability_from",
    "availability_to",
    "handled_at",
    "public_response",
}


def _public_feedback_row(**overrides):
    row = {
        "feedback_type": "general_message",
        "subject": "Oggetto",
        "message": "Messaggio",
        "status": "new",
        "submitted_at": datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc),
        "availability_from": None,
        "availability_to": None,
        "handled_at": None,
        "public_response": None,
    }
    row.update(overrides)
    return row


class _FeedbackCursor:
    def __init__(self, *, rows=None, one_row=None):
        self.rows = list(rows or [])
        self.one_row = one_row
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((" ".join(str(query).split()), params))

    def fetchone(self):
        row, self.one_row = self.one_row, None
        return row

    def fetchall(self):
        return list(self.rows)


def test_feedback_public_schema_is_exact_whitelist():
    assert set(FeedbackPublic.model_fields) == FEEDBACK_PUBLIC_KEYS
    assert "id" not in FeedbackPublic.model_fields


def test_feedback_portal_repository_revalidates_canonical_property_access():
    source = (R / "owner/repository.py").read_text()
    start = source.index("def list_feedback")
    end = source.index("def dashboard", start)
    block = source[start:end]
    assert "require_property(a,p)" in block
    assert "SELECT * FROM owner_feedback WHERE owner_account_id" not in block
    assert "SELECT feedback_type,subject,message,status,submitted_at" in block

    require_source = source[source.index("def require_property"):source.index("def portal_properties")]
    assert "x.owner_account_id=%s" in require_source
    assert "x.property_id=%s" in require_source
    assert "x.access_status='active'" in require_source
    assert "x.revoked_at IS NULL" in require_source
    assert "x.valid_until IS NULL OR x.valid_until>NOW()" in require_source

    session_source = source[source.index("def get_session"):source.index("def revoke_session")]
    assert "a.status account_status" in session_source
    assert "r['account_status']!='active'" in session_source


def test_feedback_list_valid_access_uses_public_projection(monkeypatch):
    checks = []
    monkeypatch.setattr(repo, "require_property", lambda account, prop: checks.append((account, prop)) or {"property_id": prop})
    cursor = _FeedbackCursor(rows=[dict(_public_feedback_row(), owner_account_id=7, internal_notes="secret")])

    @contextmanager
    def fake_cursor(*, commit=False):
        assert commit is False
        yield object(), cursor

    monkeypatch.setattr(repo, "core_cursor", fake_cursor)
    result = repo.list_feedback(7, 11)
    assert checks == [(7, 11)]
    assert result == [_public_feedback_row()]
    query, params = cursor.executed[0]
    assert params == (7, 11)
    assert "SELECT *" not in query.upper()
    assert "owner_account_id" not in query.split("FROM owner_feedback", 1)[0]
    assert "internal_notes" not in query


@pytest.mark.parametrize("reason", ["revoked", "expired", "different-account"])
def test_feedback_list_invalid_access_is_uniform_404(monkeypatch, reason):
    app = FastAPI()
    app.include_router(portal_router)
    app.dependency_overrides[current_owner] = lambda: {
        "owner_account_id": 8 if reason == "different-account" else 7,
        "expires_at": "2099-01-01T00:00:00Z",
    }

    def denied(account, prop):
        raise NotFoundError("Risorsa non trovata")

    monkeypatch.setattr(repo, "require_property", denied)
    client = TestClient(app)
    response = client.get("/api/owner/portal/properties/11/feedback")
    assert response.status_code == 404
    assert response.json() == {"detail": "Risorsa non trovata"}


def test_feedback_get_response_is_public_whitelist_even_if_repository_has_extras(monkeypatch):
    app = FastAPI()
    app.include_router(portal_router)
    app.dependency_overrides[current_owner] = lambda: {"owner_account_id": 7, "expires_at": "2099-01-01T00:00:00Z"}
    row = dict(
        _public_feedback_row(public_response="Risposta pubblica", handled_at=datetime(2026, 8, 13, 13, 0, tzinfo=timezone.utc)),
        id=99,
        owner_account_id=7,
        property_id=11,
        handled_by="admin@example.test",
        linked_activity_id=123,
        contact_id=4,
        lead_id=5,
        activity_id=6,
        internal_notes="non pubblico",
        BUY={"secret": True},
        MATCH={"score": 90},
        FLOW={"rule": "internal"},
    )
    monkeypatch.setattr(repo, "list_feedback", lambda account, prop: [row])
    client = TestClient(app)
    response = client.get("/api/owner/portal/properties/11/feedback")
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert set(item) == FEEDBACK_PUBLIC_KEYS
    assert item["public_response"] == "Risposta pubblica"
    for forbidden in (
        "id", "owner_account_id", "property_id", "handled_by", "linked_activity_id",
        "contact_id", "lead_id", "activity_id", "internal_notes", "BUY", "MATCH", "FLOW",
    ):
        assert forbidden not in item


def test_feedback_post_response_is_public_whitelist_even_if_repository_has_extras(monkeypatch):
    app = FastAPI()
    app.include_router(portal_router)
    app.dependency_overrides[current_owner] = lambda: {"owner_account_id": 7, "expires_at": "2099-01-01T00:00:00Z"}
    row = dict(
        _public_feedback_row(),
        id=101,
        owner_account_id=7,
        property_id=11,
        handled_by="internal-handler",
        linked_activity_id=123,
        internal_notes="non pubblico",
    )
    monkeypatch.setattr(repo, "create_feedback", lambda account, prop, payload: row)
    client = TestClient(app)
    response = client.post(
        "/api/owner/portal/properties/11/feedback",
        json={"feedback_type": "general_message", "subject": "Oggetto", "message": "Messaggio"},
    )
    assert response.status_code == 201
    assert set(response.json()) == FEEDBACK_PUBLIC_KEYS
    assert "id" not in response.json()
    assert "owner_account_id" not in response.json()
    assert "property_id" not in response.json()
    assert "handled_by" not in response.json()
    assert "linked_activity_id" not in response.json()
    assert "internal_notes" not in response.json()


def test_create_feedback_repository_returns_only_public_fields_and_links_activity_atomically(monkeypatch):
    returned = dict(_public_feedback_row(), id=55, owner_account_id=7, property_id=11, internal_notes="secret")

    class CreateFeedbackCursor:
        def __init__(self):
            self.executed = []
            self.current = None
            self.rowcount = -1

        def execute(self, query, params=None):
            normalized = " ".join(str(query).split())
            self.executed.append((normalized, params))
            self.rowcount = -1
            if normalized.startswith("SELECT oa.contact_id"):
                self.current = {"contact_id": 44}
            elif "INSERT INTO owner_feedback" in normalized:
                self.current = returned
            elif normalized.startswith("UPDATE owner_feedback"):
                self.current = None
                self.rowcount = 1
            else:
                self.current = None

        def fetchone(self):
            return self.current

    cursor = CreateFeedbackCursor()
    activities = []
    flow_events = []

    @contextmanager
    def fake_cursor(*, commit=False):
        assert commit is True
        yield object(), cursor

    def fake_activity(cur, data):
        activities.append((cur, data))
        return {"id": 91}

    def fake_flow_event(cur, data):
        flow_events.append((cur, data))
        return {"id": 81}

    monkeypatch.setattr(repo, "core_cursor", fake_cursor)
    monkeypatch.setattr(repo, "create_activity_with_cursor", fake_activity)
    monkeypatch.setattr(repo, "record_owner_request_event_with_cursor", fake_flow_event)
    monkeypatch.setattr(repo, "process_saved_owner_request_event", lambda event_id: None)
    result = repo.create_feedback(
        7,
        11,
        {"feedback_type": "general_message", "subject": "Oggetto", "message": "Messaggio"},
    )
    assert set(result) == FEEDBACK_PUBLIC_KEYS
    assert result["subject"] == "Oggetto"
    assert len(activities) == 1
    assert activities[0][0] is cursor
    assert activities[0][1]["contact_id"] == 44
    assert activities[0][1]["lead_id"] is None
    assert activities[0][1]["stima_id"] is None
    assert len(flow_events) == 1
    assert flow_events[0][0] is cursor
    access_query, access_params = cursor.executed[0]
    assert "oa.status='active'" in access_query
    assert "FOR UPDATE OF oa,x" in access_query
    assert access_params == (7, 11)
    feedback_query = next(query for query, _ in cursor.executed if "INSERT INTO owner_feedback" in query)
    assert "RETURNING id,feedback_type,subject,message,status,submitted_at" in feedback_query
    assert "RETURNING *" not in feedback_query
    link_query = next(query for query, _ in cursor.executed if query.startswith("UPDATE owner_feedback"))
    assert "linked_activity_id IS NULL" in link_query
    assert any("INSERT INTO owner_audit_log" in query for query, _ in cursor.executed)
