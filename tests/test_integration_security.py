from pathlib import Path
import os
import pytest
from integration_p2_support import require_manifest_result


def test_owner_frontend_has_no_browser_token_storage():
    text = "\n".join(
        p.read_text(encoding="utf-8", errors="ignore")
        for p in Path("static/owner_portal").rglob("*")
        if p.is_file()
    )
    assert "localStorage" not in text
    assert "sessionStorage" not in text


def test_cookie_flags_static_contract():
    text = Path("owner/security.py").read_text(encoding="utf-8").replace(" ", "").lower()
    for token in ('httponly=true', 'secure=true', 'samesite="lax"'):
        assert token in text


@pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_P2_E2E") != "1",
    reason="runtime HTTPS nell'orchestratore",
)
def test_cookie_runtime_manifest_result():
    require_manifest_result("owner")


@pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_P2_E2E") != "1",
    reason="verifica DB token nell'orchestratore",
)
def test_owner_token_hash_manifest_result():
    require_manifest_result("owner_token_storage")
