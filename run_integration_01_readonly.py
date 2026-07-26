#!/usr/bin/env python3
import os, subprocess, sys
from integration_p2_support import require_test_environment, validate_openapi_routes
READONLY_TESTS=[
"tests/test_integration_routes.py",
"tests/test_integration_schema.py",
"tests/test_integration_http_errors_readonly.py",
"tests/test_integration_security.py",
"tests/test_integration_privacy.py",
"tests/test_integration_regression.py",
]
def main():
 envctx=require_test_environment(require_http=True,require_branch=True); validate_openapi_routes(envctx.backend)
 env=os.environ.copy(); env["RUN_INTEGRATION_P2_E2E"]="0"
 print(f"PRECHECK OK: branch={envctx.branch} db={envctx.database} backend={envctx.backend} commit={envctx.commit}")
 raise SystemExit(subprocess.call([sys.executable,"-m","pytest","--noconftest","-q",*READONLY_TESTS],env=env))
if __name__=="__main__":main()
