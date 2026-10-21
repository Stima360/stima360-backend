"""STIMA VOICE V8-0 - un'agenzia ATTIVA riceve la Voce in revisione (PostgreSQL).

  01  `provision_voice_settings`: crea la riga accesa in `review`; MAI sovrascrive una
      riga esistente (un'agenzia spenta di proposito resta spenta)
  02  senza la tabella (098 non applicata) non fa nulla
  03  la creazione di un'agenzia attiva e la riattivazione passano dal service
      della piattaforma e chiamano il provisioning nella stessa transazione;
      un'agenzia creata sospesa non lo riceve (testo del service)
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_voice_2_resolver_postgres import DSN, db  # noqa: F401
from tests.test_voice_4_executor_postgres import q  # noqa: F401

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL per V8-0")
ROOT = Path(__file__).resolve().parents[1]


def test_01_crea_in_review_e_non_sovrascrive(db):
    from platform_admin import agencies_repository as repo
    nuova = q(db, "INSERT INTO agencies (id, name, slug, status) VALUES (900, 'Nuova V8', 'nuova-v8', 'active') RETURNING id")[0][0]
    with db["conn"].cursor() as cur:
        assert repo.provision_voice_settings(cur, nuova) is True
        assert repo.provision_voice_settings(cur, nuova) is False             # gia' presente: niente
    assert q(db, "SELECT enabled, mode FROM voice_agency_settings WHERE agency_id = %s", (nuova,)) == [[True, "review"]]
    q(db, "UPDATE voice_agency_settings SET enabled = FALSE WHERE agency_id = %s", (nuova,))
    with db["conn"].cursor() as cur:
        assert repo.provision_voice_settings(cur, nuova) is False
    assert q(db, "SELECT enabled FROM voice_agency_settings WHERE agency_id = %s", (nuova,)) == [[False]]
    q(db, "DELETE FROM voice_agency_settings WHERE agency_id = %s", (nuova,))


def test_02_senza_tabella_non_fa_nulla(db):
    from platform_admin import agencies_repository as repo

    class Finto:
        def __init__(self): self.sql = []
        def execute(self, sql, params=None): self.sql.append(sql)
        def fetchone(self): return (False,)
    cur = Finto()
    assert repo.provision_voice_settings(cur, 1) is False and len(cur.sql) == 1


def test_03_il_service_della_piattaforma_lo_chiama():
    testo = (ROOT / "platform_admin" / "agencies_service.py").read_text(encoding="utf-8")
    crea = testo[testo.index("def create_agency"):testo.index("def update_agency")]
    assert 'if row["status"] == AGENCY_STATUS_ACTIVE:\n            agencies_repository.provision_voice_settings(cur, row["id"])' in crea
    assert crea.index("provision_voice_settings") < crea.index("audit_then_commit(")          # prima del commit
    aggiorna = testo[testo.index("def update_agency"):testo.index("def _guard_status_change")]
    assert "agencies_repository.provision_voice_settings(cur, agency_id)" in aggiorna
    assert aggiorna.index("provision_voice_settings") < aggiorna.index("audit_then_commit(")
