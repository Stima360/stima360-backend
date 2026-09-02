from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from admin_security import require_admin
from seller_intent.exceptions import NotFoundError
from seller_intent.router import router as seller_intent_router


def build_app():
    app = FastAPI()
    app.include_router(seller_intent_router, dependencies=[Depends(require_admin)])
    return app


def test_endpoint_requires_admin_auth(monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "giorgio")
    monkeypatch.setenv("ADMIN_PASS", "test-secret")
    client = TestClient(build_app(), raise_server_exceptions=False)
    response = client.get("/api/seller-intent/leads/14/score")
    assert response.status_code == 401


def test_endpoint_not_found(monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "giorgio")
    monkeypatch.setenv("ADMIN_PASS", "test-secret")
    import seller_intent.router as router_module

    def _missing(*, lead_id: int):
        raise NotFoundError(f"lead {lead_id} not found")

    monkeypatch.setattr(router_module, "get_seller_intent_score", _missing)
    client = TestClient(build_app(), raise_server_exceptions=False)

    response = client.get("/api/seller-intent/leads/999/score", auth=("giorgio", "test-secret"))
    assert response.status_code == 404


def test_endpoint_payload_contains_operational_flags(monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "giorgio")
    monkeypatch.setenv("ADMIN_PASS", "test-secret")
    import seller_intent.router as router_module

    def _ok(*, lead_id: int):
        return {
            "lead_id": lead_id,
            "score": 45,
            "band": "tiepido",
            "state": "active",
            "computed_at": datetime(2026, 9, 2, tzinfo=timezone.utc),
            "factors": [
                {"code": "stage_qualified", "label": "Lead in fase qualified", "points": 30},
                {"code": "stima_completata", "label": "Stima completata", "points": 10},
                {"code": "recent_activity_7d", "label": "Segnale seller-origin negli ultimi 7 giorni", "points": 15},
            ],
            "operational_flags": [
                {"code": "followup_in_progress", "label": "Follow-up in lavorazione"},
                {"code": "followup_overdue", "label": "Follow-up scaduto"},
            ],
        }

    monkeypatch.setattr(router_module, "get_seller_intent_score", _ok)
    client = TestClient(build_app(), raise_server_exceptions=False)
    response = client.get("/api/seller-intent/leads/14/score", auth=("giorgio", "test-secret"))
    assert response.status_code == 200
    data = response.json()
    assert data["score"] == 45
    assert len(data["operational_flags"]) == 2
