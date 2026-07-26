from pathlib import Path
import pytest
from integration_p2_support import db_connect, find_foreign_key_orphans

EXPECTED_TABLES = {
    "contacts", "contact_roles", "leads", "activities", "tasks", "properties",
    "flow_rules", "flow_events", "flow_executions", "flow_action_records",
    "owner_accounts", "owner_property_access", "owner_access_tokens", "owner_sessions",
    "owner_publications", "owner_publication_reads", "owner_feedback", "owner_audit_log",
}


@pytest.fixture
def conn():
    connection = db_connect(readonly=True)
    try:
        yield connection
    finally:
        connection.rollback()
        connection.close()


def test_expected_tables_exist(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
        actual = {r[0] for r in cur.fetchall()}
    assert EXPECTED_TABLES <= actual


def test_every_declared_foreign_key_has_zero_orphans():
    findings = find_foreign_key_orphans()
    assert findings, "Nessuna foreign key trovata: inventario non attendibile"
    broken = [x for x in findings if x["orphan_count"] != 0]
    assert broken == []


def test_migration_files_001_009_present():
    files = {
        p.name for p in Path("migrations").glob("*.sql")
        if not p.name.endswith("_down.sql")
    }
    for number in range(1, 10):
        assert any(name.startswith(f"{number:03d}_") for name in files)
