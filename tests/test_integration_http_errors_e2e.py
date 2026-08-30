import os
import pytest
from integration_p2_support import require_manifest_result, require_test_environment

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_P2_E2E") != "1",
    reason="E2E non autorizzato",
)


def test_stateful_http_matrix_manifest_result():
    require_test_environment(require_http=True)
    result = require_manifest_result("http_stateful")
    for marker in (
        "duplicate=409",
        "incompatible_state=409",
        "immutable=409",
        "retry4=409",
        "invalid_payload=",
    ):
        assert marker in result.details
