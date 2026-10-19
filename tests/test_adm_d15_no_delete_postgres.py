"""ADM D15 - la migration che vieta i DELETE fisici della Rete.

`migrations/105_adm_d15_no_delete_admin_entities.sql` e' ATTIVATA: il runner la
vede e la applica come ogni altra (la catena `completo` la contiene gia'). Qui
la si riprova su PostgreSQL usa-e-getta (schema completo, fixture `rete`)
DENTRO una transazione che alla fine si annulla.

  01  il runner la vede, con la sua down, e la valida senza errori
  02  dopo la migration: DELETE e TRUNCATE rifiutati su agenzie, account,
      membership, territori, assegnazioni, alias (23001), anche in replica;
      gli UPDATE di stato della Rete funzionano; i trigger del registro sono
      ENABLE ALWAYS; riapplicarla e' idempotente
  03  la down la toglie e riporta il registro allo stato di 057
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_c1_lettura_territoriale_postgres import rete  # noqa: F401
from tests.test_censimento_3_backend_postgres import DSN, _q, completo, mondo  # noqa: F401

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

ROOT = Path(__file__).resolve().parents[1]
MIGRAZIONI = ROOT / "migrations"
UP = MIGRAZIONI / "105_adm_d15_no_delete_admin_entities.sql"
DOWN = MIGRAZIONI / "105_adm_d15_no_delete_admin_entities_down.sql"
TABELLE = ("agencies", "operator_users", "agency_memberships", "network_territories",
           "agency_territory_assignments", "network_territory_aliases")


def test_01_il_runner_la_vede_con_la_sua_down():
    from scripts import p26_migrate
    trovate = [m for m in p26_migrate.discover_migrations() if m.number == 105]
    assert [m.path for m in trovate] == [UP]
    assert trovate[0].down_path == DOWN and not trovate[0].non_transactional
    assert p26_migrate.validate_migration(trovate[0]) == []
    assert not (MIGRAZIONI / "pending").exists()


@pytest.fixture
def transazione(rete):
    """Una connessione in transazione, annullata alla fine qualunque cosa accada."""
    m = rete
    conn = m["psycopg2"].connect(m["dsn"])
    conn.autocommit = False
    cur = conn.cursor()
    try:
        yield cur
    finally:
        conn.rollback()
        conn.close()


def _rifiutata(cur, sql, params=()):
    from psycopg2 import errors
    cur.execute("SAVEPOINT prova")
    try:
        cur.execute(sql, params)
    except errors.RestrictViolation as exc:
        cur.execute("ROLLBACK TO SAVEPOINT prova")
        return str(exc)
    cur.execute("ROLLBACK TO SAVEPOINT prova")
    return None


def _un_id(cur, tabella):
    cur.execute(f"SELECT id FROM {tabella} ORDER BY id LIMIT 1")
    riga = cur.fetchone()
    assert riga, f"la fixture deve avere almeno una riga in {tabella}"
    return riga[0]


def test_02_delete_e_truncate_rifiutati_update_ammessi(transazione):
    cur = transazione
    cur.execute(UP.read_text(encoding="utf-8"))
    cur.execute(UP.read_text(encoding="utf-8"))  # idempotente

    for tabella in TABELLE:
        rid = _un_id(cur, tabella)
        motivo = _rifiutata(cur, f"DELETE FROM {tabella} WHERE id = %s", (rid,))
        assert motivo and "non si cancella fisicamente" in motivo, (tabella, motivo)
        assert _rifiutata(cur, f"TRUNCATE {tabella} CASCADE"), tabella
    # anche con la replica di sessione (ENABLE ALWAYS)
    cur.execute("SET LOCAL session_replication_role = replica")
    assert _rifiutata(cur, "DELETE FROM agencies WHERE id = %s", (_un_id(cur, "agencies"),))
    cur.execute("SET LOCAL session_replication_role = origin")

    # le operazioni della Rete sono UPDATE e restano possibili
    agenzia = _un_id(cur, "agencies")
    cur.execute("UPDATE agencies SET status = status WHERE id = %s", (agenzia,))
    cur.execute("UPDATE operator_users SET status = status WHERE id = %s", (_un_id(cur, "operator_users"),))
    cur.execute("UPDATE agency_territory_assignments SET status = status WHERE id = %s",
                (_un_id(cur, "agency_territory_assignments"),))

    cur.execute("""SELECT tgname, tgenabled FROM pg_trigger
                    WHERE tgrelid = 'public.platform_audit_log'::regclass
                      AND tgname LIKE 'trg_platform_audit_log_%' ORDER BY tgname""")
    assert {n: e for n, e in cur.fetchall()} == {
        "trg_platform_audit_log_append_only": "A", "trg_platform_audit_log_no_truncate": "A"}


def test_03_la_down_la_toglie(transazione):
    cur = transazione
    cur.execute(UP.read_text(encoding="utf-8"))
    cur.execute(DOWN.read_text(encoding="utf-8"))
    cur.execute("SELECT count(*) FROM pg_trigger WHERE tgname IN ('trg_adm_no_delete', 'trg_adm_no_truncate')")
    assert cur.fetchone()[0] == 0
    cur.execute("""SELECT tgenabled FROM pg_trigger
                    WHERE tgrelid = 'public.platform_audit_log'::regclass
                      AND tgname = 'trg_platform_audit_log_append_only'""")
    assert cur.fetchone()[0] == "O"
