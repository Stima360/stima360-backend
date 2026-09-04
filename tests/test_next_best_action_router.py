"""P23 — router tests (admin auth, list/detail/refresh wiring).

Follows the exact FastAPI TestClient + monkeypatch pattern used by
tests/test_seller_intent_router.py.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from admin_security import require_admin
from next_best_action.router import router as next_best_action_router


def build_app():
    app = FastAPI()
    app.include_router(next_best_action_router, dependencies=[Depends(require_admin)])
    return app


def _auth_env(monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "giorgio")
    monkeypatch.setenv("ADMIN_PASS", "test-secret")


def test_list_requires_admin_auth(monkeypatch):
    _auth_env(monkeypatch)
    client = TestClient(build_app(), raise_server_exceptions=False)
    response = client.get("/api/next-best-action")
    assert response.status_code == 401


def test_list_returns_items(monkeypatch):
    _auth_env(monkeypatch)
    import next_best_action.router as router_module

    def _fake_list(limit):
        return [
            {
                "id": 1,
                "subject_type": "lead",
                "subject_id": 14,
                "contact_id": 3,
                "lead_id": 14,
                "stima_id": None,
                "action_type": "contact_overdue_followup",
                "priority": "urgent",
                "reason": "Follow-up scaduto",
                "source_signal": "followup_overdue",
                "cta_route": "contatti",
                "cta_params": [3],
                "generated_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
                "valid_until": None,
            }
        ]

    monkeypatch.setattr(router_module.service, "list_next_best_actions", _fake_list)
    client = TestClient(build_app(), raise_server_exceptions=False)
    response = client.get("/api/next-best-action", auth=("giorgio", "test-secret"))

    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["subject_id"] == 14


def test_list_empty_is_not_an_error(monkeypatch):
    _auth_env(monkeypatch)
    import next_best_action.router as router_module

    monkeypatch.setattr(router_module.service, "list_next_best_actions", lambda limit: [])
    client = TestClient(build_app(), raise_server_exceptions=False)
    response = client.get("/api/next-best-action", auth=("giorgio", "test-secret"))

    assert response.status_code == 200
    assert response.json() == {"items": []}


def test_detail_not_found_returns_404(monkeypatch):
    _auth_env(monkeypatch)
    import next_best_action.router as router_module

    monkeypatch.setattr(router_module.service, "get_next_best_action", lambda subject_type, subject_id: None)
    client = TestClient(build_app(), raise_server_exceptions=False)
    response = client.get("/api/next-best-action/lead/999", auth=("giorgio", "test-secret"))

    assert response.status_code == 404


def test_refresh_calls_service_and_returns_counters(monkeypatch):
    _auth_env(monkeypatch)
    import next_best_action.router as router_module

    def _fake_refresh():
        return {
            "evaluated_subjects": 5,
            "created": 2,
            "updated": 1,
            "removed": 0,
            "suppressed_duplicates": 1,
            "total_active": 3,
        }

    monkeypatch.setattr(router_module.service, "refresh", _fake_refresh)
    client = TestClient(build_app(), raise_server_exceptions=False)
    response = client.post("/api/next-best-action/refresh", auth=("giorgio", "test-secret"))

    assert response.status_code == 200
    assert response.json()["created"] == 2
    assert response.json()["suppressed_duplicates"] == 1
