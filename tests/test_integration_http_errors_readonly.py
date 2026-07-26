import pytest
import requests
from integration_p2_support import require_test_environment

SQL_DETAIL_MARKERS = (
    "psycopg", "postgres", "sqlstate", "duplicate key", "syntax error", "traceback"
)


@pytest.fixture(scope="module")
def base():
    return require_test_environment(require_http=True).backend


def assert_no_server_or_sql_leak(response):
    assert response.status_code not in {500, 502}
    body = response.text.lower()
    assert not any(marker in body for marker in SQL_DETAIL_MARKERS)


@pytest.mark.parametrize(
    "path",
    [
        "/api/owner/portal/properties/999999999",
        "/api/owner/portal/publications/999999999",
        "/api/flow/rules/FLOW-NOT-EXISTING",
    ],
)
def test_nonexistent_never_500_or_sql_leak(base, path):
    response = requests.get(base + path, timeout=20)
    assert_no_server_or_sql_leak(response)


def test_invalid_owner_token(base):
    response = requests.post(
        base + "/api/owner/portal/auth/token",
        json={"token": "definitely-invalid-token"},
        timeout=20,
    )
    assert response.status_code == 404
    assert_no_server_or_sql_leak(response)


def test_invalid_payload_nonpersistent(base):
    response = requests.post(base + "/api/owner/admin/accounts", json={}, timeout=20)
    assert response.status_code in {400, 404, 422}
    assert_no_server_or_sql_leak(response)
