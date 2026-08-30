import ast
import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from main import app

ROOT = Path(__file__).resolve().parent.parent
CLIENT = TestClient(app)
EXPECTED_KEYS = {
    "contact",
    "roles",
    "leads",
    "properties",
    "buy_requests",
    "matches",
    "visits",
    "activities",
    "tasks",
}


def _crm_service():
    return importlib.import_module("crm.service")


def _patch_empty_contact(monkeypatch, *, contact=None, roles=None):
    service = _crm_service()
    monkeypatch.setattr(service, "get_contact", lambda contact_id: {**(contact or {"id": contact_id, "display_name": "Mario Test"}), "roles": list(roles or [])})
    monkeypatch.setattr(service, "list_leads", lambda *args, **kwargs: [])
    monkeypatch.setattr(service, "list_properties", lambda *args, **kwargs: [])
    monkeypatch.setattr(service, "list_buy_requests", lambda *args, **kwargs: [])
    monkeypatch.setattr(service, "list_matches", lambda *args, **kwargs: [])
    monkeypatch.setattr(service, "list_visits_by_contact", lambda contact_id: [])
    monkeypatch.setattr(service, "list_activities", lambda *args, **kwargs: [])
    monkeypatch.setattr(service, "list_tasks", lambda *args, **kwargs: [])
    return service


def test_01_crm_endpoint_mounted():
    operations = {
        (path, method.lower())
        for path, item in app.openapi()["paths"].items()
        for method in item
        if method.lower() in {"get", "post", "put", "patch", "delete"}
    }
    assert ("/api/crm/contacts/{contact_id}/360", "get") in operations


def test_02_crm_anonymous_rejected(monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "giorgio")
    monkeypatch.setenv("ADMIN_PASS", "test-secret")
    response = CLIENT.get("/api/crm/contacts/999/360")
    assert response.status_code == 401
    assert response.headers.get("WWW-Authenticate") == 'Basic realm="STIMA360 Admin"'


def test_03_crm_wrong_credentials(monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "giorgio")
    monkeypatch.setenv("ADMIN_PASS", "test-secret")
    response = CLIENT.get("/api/crm/contacts/999/360", auth=("wrong", "password"))
    assert response.status_code == 401


def test_04_crm_missing_env_fails_closed(monkeypatch):
    monkeypatch.delenv("ADMIN_USER", raising=False)
    monkeypatch.delenv("ADMIN_PASS", raising=False)
    response = CLIENT.get("/api/crm/contacts/999/360", auth=("giorgio", "test-secret"))
    assert response.status_code == 503
    assert response.json() == {"detail": "Servizio amministrativo non disponibile"}


def test_05_crm_contact_not_found_returns_404(monkeypatch):
    from core.exceptions import NotFoundError
    crm_service = _crm_service()

    def missing(contact_id):
        raise NotFoundError(f"contact {contact_id} not found")

    monkeypatch.setattr(crm_service, "get_contact_360", missing)
    monkeypatch.setenv("ADMIN_USER", "giorgio")
    monkeypatch.setenv("ADMIN_PASS", "test-secret")
    response = CLIENT.get("/api/crm/contacts/999/360", auth=("giorgio", "test-secret"))
    assert response.status_code == 404
    assert response.json()["detail"] == "contact 999 not found"


def test_06_contact_only_core_returns_empty_relations(monkeypatch):
    service = _patch_empty_contact(monkeypatch)
    result = service.get_contact_360(1)
    assert result["contact"]["id"] == 1
    for key in EXPECTED_KEYS - {"contact"}:
        assert result[key] == []


def test_07_roles_are_split_from_contact(monkeypatch):
    roles = [{"contact_id": 1, "role": "owner"}, {"contact_id": 1, "role": "buyer"}]
    service = _patch_empty_contact(monkeypatch, roles=roles)
    result = service.get_contact_360(1)
    assert result["roles"] == roles
    assert "roles" not in result["contact"]


def test_08_leads_are_aggregated(monkeypatch):
    service = _patch_empty_contact(monkeypatch)
    monkeypatch.setattr(service, "list_leads", lambda *args, **kwargs: [{"id": 10, "contact_id": 1}])
    assert service.get_contact_360(1)["leads"] == [{"id": 10, "contact_id": 1}]


def test_09_properties_are_aggregated(monkeypatch):
    service = _patch_empty_contact(monkeypatch)
    monkeypatch.setattr(service, "list_properties", lambda *args, **kwargs: [{"id": 20, "title": "Casa"}])
    assert service.get_contact_360(1)["properties"] == [{"id": 20, "title": "Casa"}]


def test_10_buy_requests_are_aggregated(monkeypatch):
    service = _patch_empty_contact(monkeypatch)
    monkeypatch.setattr(service, "list_buy_requests", lambda *args, **kwargs: [{"id": 30, "contact_id": 1}])
    assert service.get_contact_360(1)["buy_requests"] == [{"id": 30, "contact_id": 1}]


def test_11_matches_are_aggregated_for_each_buy_request(monkeypatch):
    service = _patch_empty_contact(monkeypatch)
    monkeypatch.setattr(service, "list_buy_requests", lambda *args, **kwargs: [{"id": 30}, {"id": 31}])
    monkeypatch.setattr(
        service,
        "list_matches",
        lambda *args, **kwargs: [{"id": 100 + kwargs["buy_request_id"], "buy_request_id": kwargs["buy_request_id"]}],
    )
    matches = service.get_contact_360(1)["matches"]
    assert [item["buy_request_id"] for item in matches] == [30, 31]


def test_12_visits_are_aggregated(monkeypatch):
    service = _patch_empty_contact(monkeypatch)
    monkeypatch.setattr(service, "list_visits_by_contact", lambda contact_id: [{"id": 40, "contact_id": contact_id}])
    assert service.get_contact_360(1)["visits"] == [{"id": 40, "contact_id": 1}]


def test_13_activities_are_aggregated(monkeypatch):
    service = _patch_empty_contact(monkeypatch)
    monkeypatch.setattr(service, "list_activities", lambda *args, **kwargs: [{"id": 50, "contact_id": 1}])
    assert service.get_contact_360(1)["activities"] == [{"id": 50, "contact_id": 1}]


def test_14_tasks_are_aggregated(monkeypatch):
    service = _patch_empty_contact(monkeypatch)
    monkeypatch.setattr(service, "list_tasks", lambda *args, **kwargs: [{"id": 60, "contact_id": 1}])
    assert service.get_contact_360(1)["tasks"] == [{"id": 60, "contact_id": 1}]


def test_15_payload_has_exactly_nine_sections(monkeypatch):
    service = _patch_empty_contact(monkeypatch)
    assert set(service.get_contact_360(1)) == EXPECTED_KEYS


def test_16_crm_has_no_owner_imports():
    crm_path = ROOT / "crm"
    for py_file in crm_path.glob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(alias.name.split(".")[0] != "owner" for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] != "owner"


def test_17_no_inverse_domain_dependency_on_crm():
    for module in ("core", "property", "buy", "match"):
        for py_file in (ROOT / module).glob("*.py"):
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    assert all(alias.name.split(".")[0] != "crm" for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    assert node.module.split(".")[0] != "crm"
