#!/usr/bin/env python3
import os, subprocess, sys
from integration_p2_support import require_test_environment
RUNTIME_PATTERNS=["tests/test_core*.py","tests/test_property*.py","tests/test_buy*.py","tests/test_match*.py","tests/test_flow*.py","tests/test_owner*.py"]
def main():
 require_test_environment(require_http=True,require_branch=True)
 raise SystemExit(subprocess.call([sys.executable,"-m","pytest","-q","--continue-on-collection-errors",*RUNTIME_PATTERNS],env={**os.environ,"RUN_INTEGRATION_P2_E2E":"0"}))
if __name__=="__main__":main()
