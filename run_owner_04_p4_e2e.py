"""Targeted OWNER 0.2 P4 HTTP/storage E2E runner (isolated, no live R2 or DB writes)."""
import subprocess
import sys

cmd = [sys.executable, "-m", "pytest", "-q", "tests/test_owner_04_p4.py"]
raise SystemExit(subprocess.call(cmd))
