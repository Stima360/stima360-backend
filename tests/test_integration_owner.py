import os
import pytest
from integration_p2_support import require_manifest_result, require_test_environment

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_P2_E2E") != "1",
    reason="E2E non autorizzato",
)


def test_owner_manifest_result():
    require_test_environment(require_http=True)
    require_manifest_result("owner")


def test_owner_token_storage_manifest_result():
    require_manifest_result("owner_token_storage")
