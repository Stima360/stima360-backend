from __future__ import annotations

import inspect
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError as PydanticValidationError

from core.exceptions import ConflictError
from owner import repository as repo
from owner.dependencies import current_owner
from owner.router_admin import router as admin_router
from owner.router_portal import router as portal_router
from owner.schemas import (
    VisitFeedbackCreate,
    VisitFeedbackSupersede,
    VisitFeedbackUpdate,
    visit_feedback_privacy_issues,
)

ROOT = Path(__file__).resolve().parents[1]
SAFE_SUMMARY = "Durante una recente visita è emersa una percezione di prezzo superiore alle aspettative."


def _portal_app(account_id: int = 7) -> FastAPI:
    app = FastAPI()
    app.include_router(portal_router)
    app.dependency_overrides[current_owner] = lambda: {
        "owner_account_id": account_id,
        "expires_at": "2099-01-01T00:00:00Z",
    }
    return app


def test_p3_routes_declared():
    app = FastAPI()
    app.include_router(admin_router)
    app.include_router(portal_router)
    paths = set(app.openapi()["paths"])
    assert {
        "/api/owner/admin/visit-feedback",
        "/api/owner/admin/visit-feedback/{i}",
        "/api/owner/admin/visit-feedback/{i}/publish",
        "/api/owner/admin/visit-feedback/{i}/archive",
        "/api/owner/admin/visit-feedback/{i}/supersede",
        "/api/owner/admin/visit-feedback/validate-privacy",
        "/api/owner/portal/properties/{p}/visit-feedback",
        "/api/owner/portal/visit-feedback/{i}",
    } <= paths


def test_privacy_validation_accepts_neutral_snapshot():
    assert visit_feedback_privacy_issues(SAFE_SUMMARY) == []
    payload = VisitFeedbackCreate(
        property_visit_id=1,
        category="price",
        public_summary=SAFE_SUMMARY,
        sentiment="neutral",
    )
    assert payload.public_summary == SAFE_SUMMARY


@pytest.mark.parametrize(
    ("text", "expected_code"),
    [
        ("Scrivere a mario.rossi@example.com", "email"),
        ("Chiamare il numero +39 333 123 4567", "phone"),
        ("Dettagli su https://example.com", "url"),
        ("<script>alert(1)</script>", "html_or_script"),
        ("Budget massimo 180.000 euro", "financial_amount"),
        ("MATCH 82/100 con ranking alto", "match_or_scoring"),
        ("Il signor Rossi ha espresso un dubbio", "personal_reference"),
        ("È emersa una condizione di disabilità", "sensitive_data"),
        ("Visita del 27/07/2026 alle 17:30", "precise_datetime"),
    ],
)
def test_privacy_validation_rejects_prohibited_content(text, expected_code):
    issues = visit_feedback_privacy_issues(text)
    assert expected_code in {issue["code"] for issue in issues}
    with pytest.raises(PydanticValidationError):
        VisitFeedbackCreate(
            property_visit_id=1,
            category="general",
            public_summary=text,
        )


def test_update_and_supersede_apply_same_privacy_rules():
    with pytest.raises(PydanticValidationError):
        VisitFeedbackUpdate(public_summary="Contatto 333 123 4567")
    with pytest.raises(PydanticValidationError):
        VisitFeedbackSupersede(
            category="general",
            public_summary="Score MATCH 90/100",
        )


def test_privacy_endpoint_returns_codes_without_echoing_text():
    client = TestClient(FastAPI())
    client.app.include_router(admin_router)
    sensitive = "mario.rossi@example.com"
    response = client.post(
        "/api/owner/admin/visit-feedback/validate-privacy",
        json={"public_summary": sensitive},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert "email" in {item["code"] for item in body["issues"]}
    assert sensitive not in response.text


def test_public_dto_is_explicit_whitelist_and_snapshot_only():
    row = {
        "id": 9,
        "category": "layout",
        "public_summary": "È stata suggerita una distribuzione interna più ampia.",
        "sentiment": "neutral",
        "version_number": 2,
        "published_at": datetime(2026, 7, 28, tzinfo=timezone.utc),
        "property_visit_id": 100,
        "owner_account_id": 7,
        "contact_id": 55,
        "lead_id": 66,
        "feedback": "testo grezzo",
        "rating": 1,
        "created_by": "admin@example.com",
    }
    dto = repo._public_visit_feedback(row)
    assert set(dto) == {
        "visit_feedback_publication_id",
        "category_code",
        "category_label",
        "public_summary",
        "sentiment",
        "sentiment_label",
        "version_number",
        "published_at",
        "is_current_version",
    }
    assert dto["is_current_version"] is True
    assert "testo grezzo" not in repr(dto)


def test_public_dto_omits_absent_sentiment():
    dto = repo._public_visit_feedback(
        {
            "id": 10,
            "category": "general",
            "public_summary": SAFE_SUMMARY,
            "sentiment": None,
            "version_number": 1,
            "published_at": datetime(2026, 7, 28, tzinfo=timezone.utc),
        }
    )
    assert "sentiment" not in dto
    assert "sentiment_label" not in dto


def test_portal_queries_do_not_reconstruct_from_visit_buy_or_match():
    list_source = inspect.getsource(repo.portal_visit_feedback)
    detail_source = inspect.getsource(repo.portal_visit_feedback_detail)
    source = list_source + detail_source
    for forbidden in (
        "scheduled_at",
        "pv.feedback",
        "pv.outcome",
        "pv.rating",
        "contact_id",
        "lead_id",
        "buy_",
        "match_",
    ):
        assert forbidden not in source
    assert "superseded_by_feedback_publication_id IS NULL" in list_source
    assert "superseded_by_feedback_publication_id IS NULL" in detail_source


def test_snapshot_output_ignores_later_source_mutations():
    published = {
        "id": 3,
        "category": "state",
        "public_summary": "È stata suggerita una migliore presentazione degli ambienti.",
        "sentiment": "mixed",
        "version_number": 1,
        "published_at": datetime(2026, 7, 28, tzinfo=timezone.utc),
    }
    before = repo._public_visit_feedback(dict(published))
    mutated_source = dict(published)
    mutated_source.update(
        feedback="Nuovo feedback grezzo",
        outcome="rejected",
        rating=1,
        match_score=12,
        buyer_budget=999999,
    )
    after = repo._public_visit_feedback(mutated_source)
    assert before == after


def test_published_and_archived_records_are_immutable(monkeypatch):
    @contextmanager
    def fake_cursor(*, commit=False):
        yield object(), object()

    monkeypatch.setattr(repo, "core_cursor", fake_cursor)
    for status in ("published", "archived"):
        monkeypatch.setattr(
            repo,
            "_visit_feedback_for_update",
            lambda cursor, item_id, current=status: {
                "id": item_id,
                "status": current,
                "property_id": 11,
            },
        )
        with pytest.raises(ConflictError):
            repo.update_visit_feedback_publication(1, {"public_summary": SAFE_SUMMARY})


def test_supersede_draft_does_not_hide_previous_until_publish():
    supersede_source = inspect.getsource(repo.supersede_visit_feedback)
    publish_source = inspect.getsource(repo.publish_visit_feedback)
    assert "supersedes_feedback_publication_id" in supersede_source
    assert "UPDATE owner_visit_feedback_publications SET superseded_by_feedback_publication_id" not in supersede_source
    assert "SET superseded_by_feedback_publication_id=%s" in publish_source


class _QueueCursor:
    def __init__(self, rows):
        self.rows = list(rows)
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((" ".join(str(query).split()), params))

    def fetchone(self):
        if not self.rows:
            return None
        return self.rows.pop(0)


def test_publish_successor_links_previous_atomically(monkeypatch):
    current = {
        "id": 2,
        "status": "draft",
        "property_visit_id": 30,
        "property_id": 11,
        "owner_account_id": 7,
        "public_summary": SAFE_SUMMARY,
        "supersedes_feedback_publication_id": 1,
    }
    previous = {
        "id": 1,
        "status": "published",
        "property_visit_id": 30,
        "owner_account_id": 7,
        "superseded_by_feedback_publication_id": None,
    }
    published = dict(current, status="published", published_at="now")
    cursor = _QueueCursor([previous, published])

    @contextmanager
    def fake_cursor(*, commit=False):
        assert commit is True
        yield object(), cursor

    monkeypatch.setattr(repo, "core_cursor", fake_cursor)
    monkeypatch.setattr(repo, "_visit_feedback_for_update", lambda c, i: current)
    monkeypatch.setattr(repo, "_validate_target_account", lambda *args: None)
    result = repo.publish_visit_feedback(2)
    assert result["status"] == "published"
    assert any(
        "SET superseded_by_feedback_publication_id=%s" in query and params == (2, 1)
        for query, params in cursor.executed
    )
    assert any("visit_feedback_published" in repr(params) for _, params in cursor.executed)


def test_supersede_creates_new_draft_without_mutating_previous(monkeypatch):
    old = {
        "id": 1,
        "status": "published",
        "property_visit_id": 30,
        "property_id": 11,
        "owner_account_id": 7,
        "version_number": 1,
        "superseded_by_feedback_publication_id": None,
    }
    created = {
        "id": 2,
        "status": "draft",
        "property_visit_id": 30,
        "owner_account_id": 7,
        "category": "price",
        "public_summary": SAFE_SUMMARY,
        "version_number": 2,
        "supersedes_feedback_publication_id": 1,
    }
    cursor = _QueueCursor([None, {"next_version": 2}, created])

    @contextmanager
    def fake_cursor(*, commit=False):
        assert commit is True
        yield object(), cursor

    monkeypatch.setattr(repo, "core_cursor", fake_cursor)
    monkeypatch.setattr(repo, "_visit_feedback_for_update", lambda c, i: old)
    monkeypatch.setattr(repo, "_validate_target_account", lambda *args: None)
    result = repo.supersede_visit_feedback(
        1,
        {
            "category": "price",
            "public_summary": SAFE_SUMMARY,
            "sentiment": "neutral",
            "created_by": "operator",
        },
    )
    assert result["version_number"] == 2
    assert result["supersedes_feedback_publication_id"] == 1
    assert not any(
        query.startswith("UPDATE owner_visit_feedback_publications")
        for query, _ in cursor.executed
    )


def test_portal_list_and_detail_are_account_isolated(monkeypatch):
    app = _portal_app(7)
    audit_calls = []
    item = {
        "visit_feedback_publication_id": 4,
        "category_code": "general",
        "category_label": "Osservazione generale",
        "public_summary": SAFE_SUMMARY,
        "version_number": 1,
        "published_at": "2026-07-28T10:00:00Z",
        "is_current_version": True,
    }

    monkeypatch.setattr(
        repo,
        "portal_visit_feedback",
        lambda account, prop, limit=50, offset=0: [item]
        if (account, prop) == (7, 11)
        else (_ for _ in ()).throw(Exception()),
    )
    monkeypatch.setattr(
        repo,
        "portal_visit_feedback_detail",
        lambda account, publication_id: item
        if (account, publication_id) == (7, 4)
        else (_ for _ in ()).throw(Exception()),
    )
    monkeypatch.setattr(
        repo,
        "audit_visit_feedback_access_denied",
        lambda *args, **kwargs: audit_calls.append((args, kwargs)),
    )

    client = TestClient(app)
    assert client.get("/api/owner/portal/properties/11/visit-feedback").json()["items"] == [item]
    assert client.get("/api/owner/portal/visit-feedback/4").json()["visit_feedback"] == item
    denied = client.get("/api/owner/portal/visit-feedback/99")
    assert denied.status_code == 404
    assert denied.json() == {"detail": "Risorsa non trovata"}
    assert audit_calls and audit_calls[-1][1]["publication_id"] == 99


def test_admin_list_filters_and_detail(monkeypatch):
    app = FastAPI()
    app.include_router(admin_router)
    captured = {}

    def fake_list(*args):
        captured["args"] = args
        return [{"id": 1}]

    monkeypatch.setattr(repo, "list_visit_feedback_publications", fake_list)
    monkeypatch.setattr(repo, "get_visit_feedback_publication", lambda item_id: {"id": item_id})
    client = TestClient(app)
    response = client.get(
        "/api/owner/admin/visit-feedback",
        params={
            "property_visit_id": 5,
            "property_id": 11,
            "status": "published",
            "owner_account_id": 7,
            "category": "price",
            "limit": 20,
            "offset": 2,
        },
    )
    assert response.status_code == 200
    assert response.json()["items"] == [{"id": 1}]
    assert captured["args"] == (5, 11, "published", 7, "price", 20, 2)
    assert client.get("/api/owner/admin/visit-feedback/8").json() == {"id": 8}


def test_audit_actions_cover_p3_sensitive_operations():
    source = (ROOT / "owner/repository.py").read_text(encoding="utf-8")
    for action in (
        "visit_feedback_created",
        "visit_feedback_updated",
        "visit_feedback_published",
        "visit_feedback_archived",
        "visit_feedback_version_created",
        "visit_feedback_access_denied",
    ):
        assert action in source


def test_p3_requires_no_new_migration_and_preserves_p1():
    assert not (ROOT / "migrations/011_owner_02_p3.sql").exists()
    p1 = (ROOT / "migrations/010_owner_02_p1.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE owner_visit_feedback_publications" in p1
    assert "ON DELETE RESTRICT" in p1
