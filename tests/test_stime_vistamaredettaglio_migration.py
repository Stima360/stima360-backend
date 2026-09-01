"""Schema-gap fix: migrations/019_stime_vistamaredettaglio.sql.

Static, stdlib-only checks (no DB connection). Closes a second pre-existing
gap on the legacy `stime` table (unrelated to P17 Seller Intelligence):
main.py already writes stime.vistamaredettaglio in the /api/salva_stima
flow, but no migration - and not even the stale legacy DDL in
database.py::crea_tabella_stime - ever created that column.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UP = ROOT / "migrations" / "019_stime_vistamaredettaglio.sql"
DOWN = ROOT / "migrations" / "019_stime_vistamaredettaglio_down.sql"
MAIN_PY = ROOT / "main.py"


def test_migration_files_exist():
    assert UP.exists(), "migration up 019 mancante"
    assert DOWN.exists(), "migration down 019 mancante"


def test_migration_only_alters_stime():
    up = UP.read_text(encoding="utf-8")
    alter_targets = re.findall(r"ALTER TABLE\s+(\w+)", up, re.IGNORECASE)
    assert alter_targets, "la migration deve contenere almeno un ALTER TABLE"
    assert set(alter_targets) == {"stime"}, (
        f"la migration 019 deve toccare esclusivamente stime, trovato: {set(alter_targets)}"
    )
    assert "CREATE TABLE" not in up.upper()
    assert "DROP TABLE" not in up.upper()


def test_migration_adds_exactly_vistamaredettaglio_varchar_50():
    up = UP.read_text(encoding="utf-8")
    assert re.search(
        r"ADD COLUMN IF NOT EXISTS\s+vistamaredettaglio\s+VARCHAR\(50\)\s*;",
        up,
        re.IGNORECASE,
    ), "colonna vistamaredettaglio (VARCHAR(50)) mancante o con tipo diverso"
    # Solo questa colonna: nessun'altra ADD COLUMN nella stessa migration.
    add_columns = re.findall(r"ADD COLUMN IF NOT EXISTS\s+(\w+)", up, re.IGNORECASE)
    assert add_columns == ["vistamaredettaglio"], (
        f"la migration 019 deve aggiungere esclusivamente vistamaredettaglio, trovato: {add_columns}"
    )


def test_migration_is_purely_additive_no_destructive_statements():
    up = UP.read_text(encoding="utf-8")
    for forbidden in ("DROP COLUMN", "DELETE ", "DELETE\n", "TRUNCATE", "DROP TABLE"):
        assert forbidden not in up.upper(), f"statement distruttivo trovato nella up migration: {forbidden.strip()}"


def test_migration_uses_if_not_exists_guard_for_idempotency():
    up = UP.read_text(encoding="utf-8")
    add_column_lines = [
        line for line in up.splitlines() if re.search(r"ADD COLUMN", line, re.IGNORECASE)
    ]
    assert add_column_lines, "nessuna riga ADD COLUMN trovata"
    for line in add_column_lines:
        assert "IF NOT EXISTS" in line.upper(), f"ADD COLUMN senza guard IF NOT EXISTS: {line}"


def test_down_migration_only_drops_vistamaredettaglio_from_stime():
    down = DOWN.read_text(encoding="utf-8")
    alter_targets = re.findall(r"ALTER TABLE\s+(\w+)", down, re.IGNORECASE)
    assert set(alter_targets) == {"stime"}
    assert re.search(r"DROP COLUMN IF EXISTS\s+vistamaredettaglio\s*;", down, re.IGNORECASE)
    drop_columns = re.findall(r"DROP COLUMN IF EXISTS\s+(\w+)", down, re.IGNORECASE)
    assert drop_columns == ["vistamaredettaglio"]
    assert "DROP TABLE" not in down.upper()
    assert "TRUNCATE" not in down.upper()


def test_main_py_salva_stima_vista_mare_block_is_unchanged_by_this_fix():
    # Questa fix non deve toccare main.py: il blocco vista-mare del secondo
    # UPDATE stime dentro /api/salva_stima resta esattamente quello gia'
    # presente prima di questa migration.
    main_source = MAIN_PY.read_text(encoding="utf-8")
    assert "vistamaredettaglio=%s," in main_source
    assert 'data["vistaMareDettaglio"],' in main_source
    assert '"vistaMareDettaglio": raw.get("vistaMareDettaglio") or "",' in main_source


def test_no_other_migration_already_defines_vistamaredettaglio():
    migrations_dir = ROOT / "migrations"
    for path in sorted(migrations_dir.glob("*.sql")):
        if path.name in {UP.name, DOWN.name}:
            continue
        text = path.read_text(encoding="utf-8")
        assert "vistamaredettaglio" not in text.lower(), (
            f"{path.name} referenzia gia' vistamaredettaglio - la 019 duplicherebbe una migration esistente"
        )
