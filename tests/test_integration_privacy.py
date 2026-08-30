from pathlib import Path
import os
import pytest
from integration_p2_support import require_manifest_result


def test_portal_source_has_no_direct_buy_match_flow_imports():
    text = "\n".join(
        p.read_text(encoding="utf-8", errors="ignore")
        for p in Path("owner").glob("*.py")
    ).lower()
    for fragment in ("from buy", "import buy", "from match", "import match", "from flow", "import flow"):
        assert fragment not in text


@pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_P2_E2E") != "1",
    reason="privacy runtime nell'orchestratore",
)
def test_privacy_runtime_manifest_result():
    require_manifest_result("owner")
