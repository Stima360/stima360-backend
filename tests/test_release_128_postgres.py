"""La verifica finale del rilascio 128 contro un database costruito dal runner (banco 2C): tutto registrato, niente
di pendente, tabella e trigger presenti. (Il `check` pre-apply e l'`apply` si provano sul TEST con il runbook.)"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_blocco_2c_integrated_postgres import CLUSTER_VERIFIED, DSN, _q, app, banco  # noqa: F401

pytestmark = [
    pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL locale"),
    pytest.mark.skipif(not CLUSTER_VERIFIED, reason="cluster usa-e-getta non dichiarato verificato"),
]
ROOT = Path(__file__).resolve().parents[1]


def test_verify_passes_on_a_database_built_by_the_runner(app):
    env = {k: v for k, v in os.environ.items()}
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "release_128_migration.py"), "verify",
                          "--expected-host", env["DB_HOST"]], env=env, capture_output=True, text=True, cwd=ROOT)
    assert out.returncode == 0, out.stdout[-2500:] + out.stderr[-500:]
    assert "VERIFICA FINALE SUPERATA: 128 applicata e registrata" in out.stdout
    assert _q(app, "SELECT to_regclass('public.signature_request_signers') IS NOT NULL") == [(True,)]
