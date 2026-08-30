import os
import secrets

import pytest
import requests
from requests.auth import HTTPBasicAuth
from integration_p2_support import require_test_environment

SQL_DETAIL_MARKERS=("psycopg","postgres","sqlstate","duplicate key","syntax error","traceback")

@pytest.fixture(scope="module")
def base():
    return require_test_environment(require_http=True).backend

def assert_no_server_or_sql_leak(response):
    assert response.status_code not in {500,502}
    body=response.text.lower()
    assert not any(marker in body for marker in SQL_DETAIL_MARKERS)

@pytest.mark.parametrize("path",[
    "/api/owner/portal/properties/999999999",
    "/api/owner/portal/publications/999999999",
    "/api/flow/rules/FLOW-NOT-EXISTING",
])
def test_nonexistent_never_500_or_sql_leak(base,path):
    response=requests.get(base+path,timeout=20)
    assert_no_server_or_sql_leak(response)

def test_formally_invalid_owner_token_payload(base):
    response=requests.post(base+"/api/owner/portal/auth/token",json={"token":"too-short"},timeout=20)
    assert response.status_code in {400,422}
    assert_no_server_or_sql_leak(response)

def test_formally_valid_but_nonexistent_owner_token(base):
    # Token URL-safe, 48 byte di entropia, lunghezza compatibile con min=32/max=512.
    token=secrets.token_urlsafe(48)
    assert 32 <= len(token) <= 512
    response=requests.post(base+"/api/owner/portal/auth/token",json={"token":token},timeout=20)
    assert response.status_code==404
    assert_no_server_or_sql_leak(response)

def _owner_admin_auth():
    user = os.getenv("ADMIN_USER")
    password = os.getenv("ADMIN_PASS")
    assert user and password, "ADMIN_USER/ADMIN_PASS richiesti per il test admin autenticato"
    return HTTPBasicAuth(user, password)

def test_invalid_payload_nonpersistent(base):
    response=requests.post(
        base+"/api/owner/admin/accounts",
        json={},
        auth=_owner_admin_auth(),
        timeout=20,
    )
    assert response.status_code == 422
    assert_no_server_or_sql_leak(response)
