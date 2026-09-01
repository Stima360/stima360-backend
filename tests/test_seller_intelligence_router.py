"""P17-A router tests.

Deliberately does NOT import main.py or app.include_router the seller
intelligence router into the real application - that wiring is out of
scope for P17-A (see design review point 2). Instead this file mounts the
router on its own throwaway FastAPI() app, using the very same
admin_security.require_admin dependency the rest of the repository already
uses for every other admin-protected router, to prove the intended wiring
(prefix, auth, contract) works correctly in isolation before P17-B ever
touches main.py.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from admin_security import require_admin
from seller_intelligence.exceptions import ValidationError
from seller_intelligence.router import router as seller_intelligence_router


def build_app():
    app = FastAPI()
    app.include_router(seller_intelligence_router, dependencies=[Depends(require_admin)])
    return app


def test_router_is_mounted_under_its_own_prefix_and_does_not_touch_core():
    app = build_app()
    paths = app.openapi()["paths"]
    assert "/api/seller-intelligence/events" in paths
    assert "/api/seller-intelligence/timeline" in paths
    assert not any(path.startswith("/api/core") for path in paths), (
        "il router P17 non deve esporre o sostituire nulla sotto /api/core "
        "(in particolare non /api/core/activities)"
    )


def test_every_endpoint_requires_admin_auth(monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "giorgio")
    monkeypatch.setenv("ADMIN_PASS", "test-secret")
    app = build_app()
    client = TestClient(app, raise_server_exceptions=False)

    assert client.get("/api/seller-intelligence/timeline").status_code == 401
    assert client.post("/api/seller-intelligence/events", json={"event_type": "x", "contact_id": 1}).status_code == 401

    operation = app.openapi()["paths"]["/api/seller-intelligence/events"]["post"]
    assert operation.get("security")


def test_post_event_with_valid_admin_creds_delegates_to_service(monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "giorgio")
    monkeypatch.setenv("ADMIN_PASS", "test-secret")
    app = build_app()
    client = TestClient(app, raise_server_exceptions=False)

    captured = {}

    def fake_record_event(**kwargs):
        captured.update(kwargs)
        return {"id": 1, **kwargs}

    import seller_intelligence.router as router_module
    monkeypatch.setattr(router_module.service, "record_event", fake_record_event)

    response = client.post(
        "/api/seller-intelligence/events",
        json={"event_type": "nota_agente", "contact_id": 7, "payload": {"note": "richiamare"}},
        auth=("giorgio", "test-secret"),
    )

    assert response.status_code == 201
    assert captured["contact_id"] == 7
    assert captured["event_type"] == "nota_agente"
    assert captured["payload"] == {"note": "richiamare"}


def test_post_event_without_any_reference_is_rejected_at_the_schema_boundary(monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "giorgio")
    monkeypatch.setenv("ADMIN_PASS", "test-secret")
    app = build_app()
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/api/seller-intelligence/events",
        json={"event_type": "nota_agente"},
        auth=("giorgio", "test-secret"),
    )

    assert response.status_code == 422, "pydantic deve rifiutare la richiesta prima di chiamare il service"


def test_service_validation_error_translates_to_400(monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "giorgio")
    monkeypatch.setenv("ADMIN_PASS", "test-secret")
    app = build_app()
    client = TestClient(app, raise_server_exceptions=False)

    import seller_intelligence.router as router_module

    def raising_record_event(**kwargs):
        raise ValidationError("controlled validation failure")

    monkeypatch.setattr(router_module.service, "record_event", raising_record_event)

    response = client.post(
        "/api/seller-intelligence/events",
        json={"event_type": "nota_agente", "contact_id": 1},
        auth=("giorgio", "test-secret"),
    )

    assert response.status_code == 400
    assert "controlled validation failure" in response.json()["detail"]


def test_get_timeline_passes_filters_through_to_service(monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "giorgio")
    monkeypatch.setenv("ADMIN_PASS", "test-secret")
    app = build_app()
    client = TestClient(app, raise_server_exceptions=False)

    captured = {}

    import seller_intelligence.router as router_module

    def fake_list_timeline(**kwargs):
        captured.update(kwargs)
        return [{"id": 1, "event_type": "stima_richiesta"}]

    monkeypatch.setattr(router_module.service, "list_timeline", fake_list_timeline)

    response = client.get(
        "/api/seller-intelligence/timeline",
        params={"stima_id": 501, "limit": 10, "offset": 0},
        auth=("giorgio", "test-secret"),
    )

    assert response.status_code == 200
    assert response.json() == {"items": [{"id": 1, "event_type": "stima_richiesta"}]}
    assert captured["stima_id"] == 501
    assert captured["contact_id"] is None
    assert captured["limit"] == 10


def test_router_module_does_not_import_main():
    import ast
    from pathlib import Path

    router_path = Path(__file__).resolve().parents[1] / "seller_intelligence" / "router.py"
    tree = ast.parse(router_path.read_text(encoding="utf-8"))
    imported_modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)
    assert "main" not in imported_modules
