from __future__ import annotations

import ast
import hashlib
import inspect
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from owner import repository as repo
from owner.dependencies import current_owner
from owner.router_portal import router as portal_router
from owner.schemas import NotificationPreferencesUpdate, OwnerNotificationDTO

ROOT = Path(__file__).resolve().parents[1]
UP = ROOT / "migrations/011_owner_02_p5.sql"
DOWN = ROOT / "migrations/011_owner_02_p5_down.sql"


def _compact(value: str) -> str:
    return " ".join(value.split())


def test_p5_migration_is_additive_test_only_and_creates_only_notification_tables():
    sql = UP.read_text(encoding="utf-8")
    compact = _compact(sql)
    assert "current_database() <> 'stima360_db_test'" in sql
    assert "current_schema() <> 'public'" in sql
    assert "CREATE TABLE owner_notifications" in sql
    assert "CREATE TABLE owner_notification_preferences" in sql
    assert compact.count("CREATE TABLE ") == 2
    for forbidden in ("ALTER TABLE", "DROP TABLE", "TRUNCATE", "DELETE FROM", "UPDATE properties", "UPDATE owner_"):
        assert forbidden not in sql


def test_p5_migration_requires_p1_p4_baseline_and_absent_targets():
    sql = UP.read_text(encoding="utf-8")
    for table in (
        "owner_accounts",
        "owner_property_access",
        "owner_publications",
        "owner_feedback",
        "owner_shared_documents",
        "owner_visit_feedback_publications",
        "owner_audit_log",
    ):
        assert f"public.{table}" in sql
    assert "to_regclass('public.owner_notifications') IS NOT NULL" in sql
    assert "to_regclass('public.owner_notification_preferences') IS NOT NULL" in sql


def test_p5_schema_has_race_safe_idempotency_and_retention():
    sql = UP.read_text(encoding="utf-8")
    assert "UNIQUE (idempotency_key)" in sql
    assert "VARCHAR(255) NOT NULL" in sql
    assert "INTERVAL '365 days'" in sql
    assert "read_at IS NULL OR read_at >= created_at" in sql
    assert "expires_at > created_at" in sql


def test_p5_schema_is_in_app_only():
    sql = (UP.read_text(encoding="utf-8") + "\n" + (ROOT / "owner/schemas.py").read_text(encoding="utf-8")).lower()
    assert "in_app_enabled" in sql
    for forbidden in ("email_enabled", "whatsapp_enabled", "push_enabled", "smtp", "phone_number"):
        assert forbidden not in sql


def test_p5_rollback_is_test_only_and_refuses_nonempty_tables():
    sql = DOWN.read_text(encoding="utf-8")
    assert "current_database() <> 'stima360_db_test'" in sql
    assert "current_schema() <> 'public'" in sql
    assert "notifications_count <> 0 OR preferences_count <> 0" in sql
    assert "DROP TABLE owner_notification_preferences" in sql
    assert "DROP TABLE owner_notifications" in sql
    for forbidden in ("owner_publications", "owner_shared_documents", "owner_visit_feedback_publications"):
        assert f"DROP TABLE {forbidden}" not in sql


def test_previous_owner_migration_hashes_are_unchanged():
    assert hashlib.sha256((ROOT / "migrations/010_owner_02_p1.sql").read_bytes()).hexdigest() == "46f21b5f073607b178fe6d257e37d95cb04bdc6210da73539bc4c9a23e57e5a6"
    assert hashlib.sha256((ROOT / "migrations/010_owner_02_p1_down.sql").read_bytes()).hexdigest() == "66d8fdb3012d914a79e8763bcd29e44de4db4e3b3a050c0ae267b27afcb4327a"


def test_owner_notification_dto_is_exact_whitelist():
    assert set(OwnerNotificationDTO.model_fields) == {
        "id", "type", "title", "body", "created_at", "read_at", "target_type", "target_id"
    }
    row = {
        "id": 9,
        "notification_type": "shared_document_published",
        "title": "APE",
        "body": "Documento disponibile",
        "created_at": "now",
        "read_at": None,
        "target_type": "owner_shared_document",
        "target_id": 3,
        "owner_account_id": 7,
        "property_id": 11,
        "idempotency_key": "secret",
        "expires_at": "later",
        "metadata": {"internal": True},
    }
    assert set(repo._public_notification(row)) == {
        "id", "type", "title", "body", "created_at", "read_at", "target_type", "target_id"
    }


class _RecordingCursor:
    def __init__(self, rows=None):
        self.executed = []
        self.rows = list(rows or [])

    def execute(self, query, params=None):
        self.executed.append((_compact(str(query)), params))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def fetchall(self):
        rows = list(self.rows)
        self.rows.clear()
        return rows


def test_notification_event_uses_db_unique_on_conflict_and_complete_access_predicate():
    cursor = _RecordingCursor()
    repo._emit_notification_event(
        cursor,
        property_id=11,
        notification_type="shared_document_published",
        preference_column="document_enabled",
        title="APE",
        body="Documento disponibile",
        target_type="owner_shared_document",
        target_id=8,
        owner_account_id=None,
    )
    source = "\n".join(query for query, _ in cursor.executed)
    assert "ON CONFLICT (idempotency_key) DO NOTHING" in source
    assert "owner-p5:v1:" in source
    assert "x.access_status='active'" in source
    assert "x.revoked_at IS NULL" in source
    assert "x.valid_until IS NULL OR x.valid_until>NOW()" in source
    assert "oa.status<>'disabled'" in source
    assert "COALESCE(np.in_app_enabled, TRUE)" in source
    assert "COALESCE(np.document_enabled, TRUE)" in source


def test_notification_event_audits_created_and_preference_suppressed_without_payload_body():
    cursor = _RecordingCursor()
    repo._emit_notification_event(
        cursor,
        property_id=11,
        notification_type="publication_published",
        preference_column="publication_enabled",
        title="Aggiornamento",
        body="Contenuto pubblico che non deve finire nell'audit",
        target_type="owner_publication",
        target_id=5,
    )
    source = "\n".join(query for query, _ in cursor.executed)
    assert "notification_created" in source
    assert "notification_suppressed" in source
    assert "preference_disabled" in source
    assert "Contenuto pubblico" not in source


@pytest.mark.parametrize(
    ("notification_type", "preference", "target_type"),
    [
        ("publication_published", "publication_enabled", "owner_publication"),
        ("visit_feedback_published", "visit_feedback_enabled", "owner_visit_feedback"),
        ("shared_document_published", "document_enabled", "owner_shared_document"),
        ("request_handled", "request_update_enabled", "owner_feedback"),
    ],
)
def test_four_notification_event_contracts_are_allowed(notification_type, preference, target_type):
    cursor = _RecordingCursor()
    repo._emit_notification_event(
        cursor,
        property_id=11,
        notification_type=notification_type,
        preference_column=preference,
        title="Titolo",
        body="Corpo",
        target_type=target_type,
        target_id=1,
        owner_account_id=7,
    )
    assert len(cursor.executed) == 2


def test_p2_p3_p4_hooks_are_transactional_and_flow_free():
    for function in (repo.publish, repo.publish_visit_feedback, repo.publish_shared_document, repo.update_feedback_status):
        source = inspect.getsource(function)
        assert "core_cursor(commit=True)" in source
        assert "_emit_notification_event" in source
        assert "flow" not in source.lower()
    assert "publication_published" in inspect.getsource(repo.publish)
    assert "visit_feedback_published" in inspect.getsource(repo.publish_visit_feedback)
    assert "shared_document_published" in inspect.getsource(repo.publish_shared_document)
    assert "request_handled" in inspect.getsource(repo.update_feedback_status)


def test_request_handled_only_emits_on_first_handling_transition():
    source = inspect.getsource(repo.update_feedback_status)
    assert "first_handling=handled and old.get('handled_at') is None" in source
    assert "if first_handling:" in source


def test_portal_notification_queries_revalidate_access_and_expiry():
    for function in (repo.portal_notifications, repo.mark_notification_read):
        source = inspect.getsource(function)
        assert "owner_property_access" in source
        assert "access_status='active'" in source
        assert "revoked_at IS NULL" in source
        assert "valid_until IS NULL OR x.valid_until>NOW()" in source
        assert "expires_at>NOW()" in source


def test_mark_read_is_idempotent_first_timestamp_wins(monkeypatch):
    cursor = _RecordingCursor([
        {
            "id": 4,
            "notification_type": "publication_published",
            "title": "Aggiornamento",
            "body": "Disponibile",
            "created_at": "before",
            "read_at": "first-read",
            "target_type": "owner_publication",
            "target_id": 2,
            "property_id": 11,
        }
    ])

    @contextmanager
    def fake_cursor(*, commit=False):
        assert commit is True
        yield object(), cursor

    monkeypatch.setattr(repo, "core_cursor", fake_cursor)
    result = repo.mark_notification_read(7, 4)
    assert result["read_at"] == "first-read"
    assert "COALESCE(n.read_at,NOW())" in cursor.executed[0][0]
    assert any("notification_read" in repr(params) for _, params in cursor.executed)


def test_preferences_default_true_without_implicit_write(monkeypatch):
    cursor = _RecordingCursor([])

    @contextmanager
    def fake_cursor(*, commit=False):
        assert commit is False
        yield object(), cursor

    monkeypatch.setattr(repo, "core_cursor", fake_cursor)
    assert repo.get_notification_preferences(7) == {
        "in_app_enabled": True,
        "publication_enabled": True,
        "visit_feedback_enabled": True,
        "document_enabled": True,
        "request_update_enabled": True,
    }
    assert len(cursor.executed) == 1
    assert cursor.executed[0][0].startswith("SELECT")


def test_preference_update_is_full_in_app_upsert_and_audited(monkeypatch):
    values = {
        "in_app_enabled": True,
        "publication_enabled": False,
        "visit_feedback_enabled": True,
        "document_enabled": False,
        "request_update_enabled": True,
    }
    cursor = _RecordingCursor([{"exists": 1}, dict(values)])

    @contextmanager
    def fake_cursor(*, commit=False):
        assert commit is True
        yield object(), cursor

    monkeypatch.setattr(repo, "core_cursor", fake_cursor)
    assert repo.update_notification_preferences(7, values) == values
    source = "\n".join(query for query, _ in cursor.executed)
    assert "ON CONFLICT(owner_account_id) DO UPDATE" in source
    assert any("notification_preferences_updated" in repr(params) for _, params in cursor.executed)
    assert "email" not in source.lower()
    assert "whatsapp" not in source.lower()


def test_notification_list_paginates_deterministically_and_supports_unread_filter(monkeypatch):
    rows = [
        {
            "id": 5,
            "notification_type": "publication_published",
            "title": "A",
            "body": "B",
            "created_at": "now",
            "read_at": None,
            "target_type": "owner_publication",
            "target_id": 2,
        }
    ]
    cursor = _RecordingCursor(rows)

    @contextmanager
    def fake_cursor(*, commit=False):
        assert commit is False
        yield object(), cursor

    monkeypatch.setattr(repo, "core_cursor", fake_cursor)
    result = repo.portal_notifications(7, 51, 0, True)
    assert result[0]["id"] == 5
    query, params = cursor.executed[0]
    assert "n.read_at IS NULL" in query
    assert "ORDER BY n.created_at DESC,n.id DESC LIMIT %s OFFSET %s" in query
    assert params[-2:] == [51, 0]


def test_p5_routes_declared_without_method_path_collisions_and_no_admin_p5_routes():
    def declared_route_pairs(relative_path: str):
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        tree = ast.parse(source, filename=relative_path)
        pairs = []
        prefix = None
        supported_methods = {"get", "post", "patch", "put", "delete"}

        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            if not any(isinstance(target, ast.Name) and target.id == "router" for target in node.targets):
                continue
            if not isinstance(node.value, ast.Call):
                continue
            if not isinstance(node.value.func, ast.Name) or node.value.func.id != "APIRouter":
                continue
            for keyword in node.value.keywords:
                if keyword.arg == "prefix" and isinstance(keyword.value, ast.Constant):
                    prefix = keyword.value.value
                    break

        assert isinstance(prefix, str), f"Prefix APIRouter non determinabile in {relative_path}"

        for node in ast.walk(tree):
            for decorator in getattr(node, "decorator_list", ()):
                if not isinstance(decorator, ast.Call):
                    continue
                func = decorator.func
                if not (
                    isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "router"
                    and func.attr in supported_methods
                    and decorator.args
                    and isinstance(decorator.args[0], ast.Constant)
                    and isinstance(decorator.args[0].value, str)
                ):
                    continue
                pairs.append((func.attr.upper(), prefix + decorator.args[0].value))
        return pairs

    admin_pairs = declared_route_pairs("owner/router_admin.py")
    portal_pairs = declared_route_pairs("owner/router_portal.py")
    pairs = admin_pairs + portal_pairs
    expected = {
        ("GET", "/api/owner/portal/notifications"),
        ("POST", "/api/owner/portal/notifications/{i}/read"),
        ("GET", "/api/owner/portal/notification-preferences"),
        ("PUT", "/api/owner/portal/notification-preferences"),
    }

    assert len(pairs) == len(set(pairs)), "Collisione method+path nelle route OWNER"
    assert expected <= set(pairs), "Mancano una o più route P5 attese"
    assert not any("notification" in path for _, path in admin_pairs), "P5 non deve aggiungere route admin"


def test_p5_http_contract_isolated(monkeypatch):
    monkeypatch.setattr(
        repo,
        "portal_notifications",
        lambda account, limit, offset, unread: [
            {
                "id": 1,
                "type": "publication_published",
                "title": "Aggiornamento",
                "body": "Disponibile",
                "created_at": "now",
                "read_at": None,
                "target_type": "owner_publication",
                "target_id": 2,
            }
        ] if account == 7 else [],
    )
    monkeypatch.setattr(repo, "mark_notification_read", lambda account, item: {"id": item, "read_at": "now"})
    monkeypatch.setattr(repo, "get_notification_preferences", lambda account: {"in_app_enabled": True})
    monkeypatch.setattr(repo, "update_notification_preferences", lambda account, values: values)

    app = FastAPI()
    app.include_router(portal_router)
    app.dependency_overrides[current_owner] = lambda: {
        "owner_account_id": 7,
        "expires_at": "2099-01-01T00:00:00Z",
    }
    client = TestClient(app)
    response = client.get("/api/owner/portal/notifications?limit=50&offset=0&unread_only=true")
    assert response.status_code == 200
    assert response.json()["items"][0]["id"] == 1
    assert response.json()["has_more"] is False
    assert client.post("/api/owner/portal/notifications/1/read").status_code == 200
    assert client.get("/api/owner/portal/notification-preferences").status_code == 200
    payload = {
        "in_app_enabled": True,
        "publication_enabled": True,
        "visit_feedback_enabled": True,
        "document_enabled": False,
        "request_update_enabled": True,
    }
    assert client.put("/api/owner/portal/notification-preferences", json=payload).json() == payload


def test_notification_read_denial_is_uniform_404_and_audited(monkeypatch):
    calls = []
    monkeypatch.setattr(repo, "mark_notification_read", lambda *_: (_ for _ in ()).throw(RuntimeError("denied")))
    monkeypatch.setattr(repo, "audit_notification_access_denied", lambda *args, **kwargs: calls.append((args, kwargs)))
    app = FastAPI()
    app.include_router(portal_router)
    app.dependency_overrides[current_owner] = lambda: {
        "owner_account_id": 7,
        "expires_at": "2099-01-01T00:00:00Z",
    }
    response = TestClient(app).post("/api/owner/portal/notifications/999/read")
    assert response.status_code == 404
    assert response.json()["detail"] == "Risorsa non trovata"
    assert calls and calls[0][0][:2] == (7, 999)


def test_p5_has_no_flow_email_or_whatsapp_integration():
    parts = [
        inspect.getsource(repo._emit_notification_event),
        inspect.getsource(repo.portal_notifications),
        inspect.getsource(repo.mark_notification_read),
        inspect.getsource(repo.get_notification_preferences),
        inspect.getsource(repo.update_notification_preferences),
        (ROOT / "owner/router_portal.py").read_text(encoding="utf-8").split("# OWNER 0.2 P5", 1)[1],
    ]
    source = "\n".join(parts).lower()
    for forbidden in ("from flow", "import flow", "smtp", "whatsapp", "send_email", "invia_mail"):
        assert forbidden not in source


def test_runner_isolated_and_never_applies_migration():
    source = (ROOT / "run_owner_05_p5_e2e.py").read_text(encoding="utf-8")
    assert "tests/test_owner_05_p5.py" in source
    assert "psql" not in source
    assert "011_owner_02_p5.sql" not in source
