"""Targeted OWNER 0.2 P3 HTTP E2E runner (isolated, no real TEST data writes)."""
import subprocess
import sys

cmd = [sys.executable, "-m", "pytest", "-q", "tests/test_owner_03_p3.py"]
raise SystemExit(subprocess.call(cmd))
