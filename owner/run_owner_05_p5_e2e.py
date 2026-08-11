"""Targeted OWNER 0.2 P5 in-app notification runner.

Isolated tests only: this runner never applies migrations and never targets the
live TEST database unless a future, separately authorized harness is added.
"""
import subprocess
import sys

cmd = [sys.executable, "-m", "pytest", "-q", "tests/test_owner_05_p5.py"]
raise SystemExit(subprocess.call(cmd))
