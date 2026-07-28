"""Targeted OWNER 0.2 P2 HTTP E2E runner (isolated, no real TEST data writes)."""
import subprocess
import sys

cmd = [sys.executable, "-m", "pytest", "--noconftest", "-q", "tests/test_owner_02_p2.py"]
raise SystemExit(subprocess.call(cmd))
