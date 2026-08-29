from __future__ import annotations

import hashlib
import hmac

import pytest
from fastapi.testclient import TestClient

from integration_p2_support import import_project_module


ADMIN_USER = "release-admin"
ADMIN_PASS = "release-secret"
APP_SECRET = "meta-app-secret"

LEGACY_ADMIN_ROUTES = (
    ("GET", "/api/admin/whatsapp/messages", None),
    ("GET", "/api/admin/stime", None),
    ("GET", "/api/admin/stime_pro", None),
    ("POST", "/api/admin/whatsapp/reply", {"to": "3331234567", "text": "Test"}),
    ("POST", "/api/admin/stime/delete", {"ids": [1]}),
    ("POST", "/api/admin/stime_dettagliate/delete", {"ids": [1]}),
    ("POST", "/api/admin/stime/1/update", {"lead_status": "contacted"}),
)


class FakeCursor:
    def __init__(self):
        self.description = []
        self.executions = []
        self.closed = False

    def execute(self, query, params=None):
        self.executions.append((query, params))

    def fetchall(self):
        return []

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self):
        self.cursor_instance = FakeCursor()
        self.commit_count = 0
        self.closed = False

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.commit_count += 1

    def close(self):
        self.closed = True


@pytest.fixture(scope="module")
def main_module():
    return import_project_module("main")


@pytest.fixture(scope="module")
def client(main_module):
    return TestClient(main_module.app, raise_server_exceptions=False)


@pytest.fixture
def admin_env(monkeypatch):
    monkeypatch.setenv("ADMIN_USER", ADMIN_USER)
    monkeypatch.setenv("ADMIN_PASS", ADMIN_PASS)


def _request(client, method, path, payload, **kwargs):
    return client.request(method, path, json=payload, **kwargs)


@pytest.mark.parametrize("method,path,payload", LEGACY_ADMIN_ROUTES)
def test_legacy_admin_routes_reject_anonymous_before_business_logic(
    client,
    main_module,
    admin_env,
    monkeypatch,
    method,
    path,
    payload,
):
    calls = {"database": 0, "whatsapp": 0}

    def forbidden_database():
        calls["database"] += 1
        raise AssertionError("database accessed before admin authentication")

    def forbidden_whatsapp(*_args, **_kwargs):
        calls["whatsapp"] += 1
        raise AssertionError("WhatsApp called before admin authentication")

    monkeypatch.setattr(main_module, "get_connection", forbidden_database)
    monkeypatch.setattr(main_module, "invia_whatsapp_text", forbidden_whatsapp)

    response = _request(client, method, path, payload)

    assert response.status_code == 401
    assert response.json() == {"detail": "Non autorizzato"}
    assert response.headers["WWW-Authenticate"] == 'Basic realm="STIMA360 Admin"'
    assert calls == {"database": 0, "whatsapp": 0}


@pytest.mark.parametrize("method,path,payload", LEGACY_ADMIN_ROUTES)
def test_legacy_admin_routes_are_reachable_with_valid_credentials(
    client,
    main_module,
    admin_env,
    monkeypatch,
    method,
    path,
    payload,
):
    connection = FakeConnection()
    monkeypatch.setattr(main_module, "get_connection", lambda: connection)
    monkeypatch.setattr(
        main_module,
        "invia_whatsapp_text",
        lambda *_args, **_kwargs: type("MetaResponse", (), {"status_code": 200, "text": "ok"})(),
    )

    response = _request(
        client,
        method,
        path,
        payload,
        auth=(ADMIN_USER, ADMIN_PASS),
    )

    assert response.status_code == 200


def test_all_seven_legacy_admin_operations_publish_the_existing_security_gate(main_module):
    paths = main_module.app.openapi()["paths"]
    for method, path, _payload in LEGACY_ADMIN_ROUTES:
        openapi_path = path.replace("/1/", "/{stima_id}/")
        assert paths[openapi_path][method.lower()].get("security")


def _signature(raw_body: bytes, secret: str = APP_SECRET) -> str:
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _signed_post(client, raw_body: bytes, signature: str | None):
    headers = {"Content-Type": "application/json"}
    if signature is not None:
        headers["X-Hub-Signature-256"] = signature
    return client.post("/webhook/whatsapp", content=raw_body, headers=headers)


VALID_MESSAGE = (
    b'{ "entry": [{"changes": [{"value": {"messages": ['
    b'{"from": "393331234567", "type": "text", "text": {"body": "Ciao"}}'
    b']}}]}] }'
)


def test_whatsapp_valid_signature_accepts_raw_body_and_preserves_incoming_flow(
    client,
    main_module,
    monkeypatch,
):
    monkeypatch.setenv("WHATSAPP_APP_SECRET", APP_SECRET)
    connection = FakeConnection()
    monkeypatch.setattr(main_module, "get_connection", lambda: connection)

    response = _signed_post(client, VALID_MESSAGE, _signature(VALID_MESSAGE))

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert connection.commit_count == 1
    assert len(connection.cursor_instance.executions) == 1
    _query, params = connection.cursor_instance.executions[0]
    assert params == ("393331234567", "text", "Ciao")


@pytest.mark.parametrize(
    "signature",
    (None, "sha256=wrong", "sha1=" + "0" * 40),
)
def test_whatsapp_missing_or_invalid_signature_is_rejected_before_database(
    client,
    main_module,
    monkeypatch,
    signature,
):
    monkeypatch.setenv("WHATSAPP_APP_SECRET", APP_SECRET)
    database_calls = 0

    def forbidden_database():
        nonlocal database_calls
        database_calls += 1
        raise AssertionError("database accessed before webhook signature verification")

    monkeypatch.setattr(main_module, "get_connection", forbidden_database)

    response = _signed_post(client, VALID_MESSAGE, signature)

    assert response.status_code == 403
    assert database_calls == 0


def test_whatsapp_tampered_raw_body_is_rejected_before_database(
    client,
    main_module,
    monkeypatch,
):
    monkeypatch.setenv("WHATSAPP_APP_SECRET", APP_SECRET)
    database_calls = 0

    def forbidden_database():
        nonlocal database_calls
        database_calls += 1
        raise AssertionError("database accessed before webhook signature verification")

    monkeypatch.setattr(main_module, "get_connection", forbidden_database)
    tampered = VALID_MESSAGE.replace(b"Ciao", b"Alterato")

    response = _signed_post(client, tampered, _signature(VALID_MESSAGE))

    assert response.status_code == 403
    assert database_calls == 0


def test_whatsapp_missing_app_secret_fails_closed_before_database(
    client,
    main_module,
    monkeypatch,
):
    monkeypatch.delenv("WHATSAPP_APP_SECRET", raising=False)
    database_calls = 0

    def forbidden_database():
        nonlocal database_calls
        database_calls += 1
        raise AssertionError("database accessed without webhook app secret")

    monkeypatch.setattr(main_module, "get_connection", forbidden_database)

    response = _signed_post(client, VALID_MESSAGE, _signature(VALID_MESSAGE))

    assert response.status_code == 503
    assert database_calls == 0


def test_whatsapp_get_verification_contract_is_preserved(client, monkeypatch):
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "verify-token")

    response = client.get(
        "/webhook/whatsapp",
        params={
            "hub_mode": "subscribe",
            "hub_challenge": "123456",
            "hub_verify_token": "verify-token",
        },
    )

    assert response.status_code == 200
    assert response.json() == 123456
