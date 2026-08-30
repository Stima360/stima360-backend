import os
import pytest
from integration_p2_support import require_manifest_result, require_test_environment

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_P2_E2E") != "1",
    reason="E2E non autorizzato",
)


def test_core_property_manifest_result():
    require_test_environment(require_http=True)
    require_manifest_result("core_property")
