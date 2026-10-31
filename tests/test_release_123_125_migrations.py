"""Lo script di rilascio 123-125 (Render One-Off Job) resta allineato ai file.

La prova contro un database e' quella del runbook (database locale costruito
come il TEST fino alla 118); qui solo cio' che non richiede un database.
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import release_123_125_migrations as rel  # noqa: E402


def _sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_embedded_checksums_match_the_repository():
    assert _sha(rel.RUNNER) == rel.RUNNER_SHA
    for v, s in rel.PRE.items():
        assert _sha(ROOT / "migrations" / f"{v}.sql") == s, v
    for v, (up, down) in rel.NEW.items():
        assert _sha(ROOT / "migrations" / f"{v}.sql") == up, v
        assert _sha(ROOT / "migrations" / f"{v}_down.sql") == down, v
    g = rel.Gate()
    rel.files_gate(g)
    assert g.problems == []


def test_placeholder_or_wrong_database_stops_before_connecting(monkeypatch):
    for host, name in (("INCOLLA_QUI_L_HOST_TEST_VERIFICATO", "stima360_db_test"), ("h", "stima360_db")):
        env = {"DB_HOST": "h", "DB_NAME": name, "PATH": "/usr/bin:/bin"}
        out = subprocess.run([sys.executable, str(ROOT / "scripts" / "release_123_125_migrations.py"), "check",
                              "--expected-host", host], env=env, capture_output=True, text=True)
        assert out.returncode == 1 and "nessuna connessione aperta" in out.stdout, out.stdout


def test_the_script_has_no_schema_sql_and_uses_the_official_runner():
    code = (ROOT / "scripts" / "release_123_125_migrations.py").read_text(encoding="utf-8")
    for forbidden in ("CREATE TABLE", "ALTER TABLE", "DROP TABLE", "INSERT INTO", "UPDATE schema_migrations"):
        assert forbidden not in code, forbidden
    assert 'runner("apply", "--operator", OPERATOR)' in code and "set_session(readonly=True)" in code
