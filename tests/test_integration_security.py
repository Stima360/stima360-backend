import ast
from pathlib import Path
import os
import pytest
from integration_p2_support import require_manifest_result


def test_owner_frontend_has_no_browser_token_storage():
    text="\n".join(
        p.read_text(encoding="utf-8",errors="ignore")
        for p in Path("static/owner_portal").rglob("*") if p.is_file()
    )
    assert "localStorage" not in text
    assert "sessionStorage" not in text


def _constant_value(node):
    return node.value if isinstance(node,ast.Constant) else None


def test_cookie_flags_static_contract_semantic():
    tree=ast.parse(Path("owner/security.py").read_text(encoding="utf-8"))
    calls=[]
    for node in ast.walk(tree):
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr in {"set_cookie","delete_cookie"}:
            calls.append((node.func.attr,{kw.arg:_constant_value(kw.value) for kw in node.keywords if kw.arg}))
    assert calls,"Nessuna configurazione cookie trovata"
    set_calls=[kwargs for name,kwargs in calls if name=="set_cookie"]
    assert set_calls,"response.set_cookie non trovato"
    for kwargs in set_calls:
        assert kwargs.get("httponly") is True
        assert kwargs.get("secure") is True
        assert str(kwargs.get("samesite","")).lower()=="lax"


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_P2_E2E")!="1",reason="runtime HTTPS nell'orchestratore")
def test_cookie_runtime_manifest_result():
    require_manifest_result("owner")


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_P2_E2E")!="1",reason="verifica DB token nell'orchestratore")
def test_owner_token_hash_manifest_result():
    require_manifest_result("owner_token_storage")
