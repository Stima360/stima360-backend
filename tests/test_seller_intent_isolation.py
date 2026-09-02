from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = ROOT / "seller_intent"
MAIN_PY = ROOT / "main.py"


def test_module_files_exist():
    expected = (
        "__init__.py",
        "database.py",
        "exceptions.py",
        "repository.py",
        "scoring.py",
        "service.py",
        "schemas.py",
        "router.py",
    )
    for name in expected:
        assert (PACKAGE_DIR / name).exists(), f"seller_intent/{name} mancante"


def test_main_includes_seller_intent_router_with_admin_dependency():
    src = MAIN_PY.read_text(encoding="utf-8")
    assert "from seller_intent.router import router as seller_intent_router" in src
    assert "app.include_router(seller_intent_router, dependencies=[Depends(require_admin)])" in src


def test_repository_contains_no_write_statements():
    src = (PACKAGE_DIR / "repository.py").read_text(encoding="utf-8").lower()
    assert not re.search(r"\\binsert\\b", src)
    assert not re.search(r"\\bupdate\\b", src)
    assert not re.search(r"\\bdelete\\b", src)

