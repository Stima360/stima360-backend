"""Schema-gap fix: migrations/018_stime_consenso_marketing.sql.

Static, stdlib-only checks (no DB connection). This migration closes a
pre-existing gap - main.py already writes stime.consenso_marketing /
stime.consenso_marketing_at on every INSERT, but no migration file ever
added those two columns to the legacy `stime` table (created outside
migrations/, see database.py::crea_tabella_stime, itself stale relative to
the live schema). The gap is unrelated to P17 Seller Intelligence; these
tests only certify the migration itself and that P17/salva_stima's
contract remain untouched by this fix.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UP = ROOT / "migrations" / "018_stime_consenso_marketing.sql"
DOWN = ROOT / "migrations" / "018_stime_consenso_marketing_down.sql"
MAIN_PY = ROOT / "main.py"


def test_migration_files_exist():
    assert UP.exists(), "migration up 018 mancante"
    assert DOWN.exists(), "migration down 018 mancante"


def test_migration_only_alters_stime_and_only_adds_the_two_columns():
    up = UP.read_text(encoding="utf-8")
    alter_targets = re.findall(r"ALTER TABLE\s+(\w+)", up, re.IGNORECASE)
    assert alter_targets, "la migration deve contenere almeno un ALTER TABLE"
    assert set(alter_targets) == {"stime"}, (
        f"la migration 018 deve toccare esclusivamente stime, trovato: {set(alter_targets)}"
    )
    # Nessuna CREATE/DROP TABLE: e' un'estensione additiva di una tabella
    # gia' esistente, non una nuova struttura.
    assert "CREATE TABLE" not in up.upper()
    assert "DROP TABLE" not in up.upper()


def test_migration_adds_exactly_consenso_marketing_and_consenso_marketing_at():
    up = UP.read_text(encoding="utf-8")
    assert re.search(
        r"ADD COLUMN IF NOT EXISTS\s+consenso_marketing\s+BOOLEAN\s*;",
        up,
        re.IGNORECASE,
    ), "colonna consenso_marketing (BOOLEAN) mancante o con tipo diverso"
    assert re.search(
        r"ADD COLUMN IF NOT EXISTS\s+consenso_marketing_at\s+TIMESTAMPTZ\s*;",
        up,
        re.IGNORECASE,
    ), "colonna consenso_marketing_at (TIMESTAMPTZ) mancante o con tipo diverso"
    # Nessun DEFAULT inventato: rispecchia esattamente contacts.marketing_consent
    # (migrations/001_core_contacts_leads.sql), che e' anch'essa senza DEFAULT.
    assert "DEFAULT" not in up.upper()


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


def test_down_migration_only_drops_the_two_new_columns_from_stime():
    down = DOWN.read_text(encoding="utf-8")
    alter_targets = re.findall(r"ALTER TABLE\s+(\w+)", down, re.IGNORECASE)
    assert set(alter_targets) == {"stime"}
    assert re.search(r"DROP COLUMN IF EXISTS\s+consenso_marketing\s*;", down, re.IGNORECASE)
    assert re.search(r"DROP COLUMN IF EXISTS\s+consenso_marketing_at\s*;", down, re.IGNORECASE)
    assert "DROP TABLE" not in down.upper()
    assert "TRUNCATE" not in down.upper()


def test_main_py_salva_stima_contract_is_unchanged_by_this_fix():
    # Questa fix non deve toccare main.py: il contratto di /api/salva_stima
    # (colonne scritte, ordine, semantica) resta esattamente quello gia'
    # presente prima di questa migration - la migration si limita a rendere
    # lo schema DB coerente con codice che esisteva gia'.
    main_source = MAIN_PY.read_text(encoding="utf-8")
    assert re.search(
        r"consenso_marketing\s*=\s*bool\(raw\.get\(\"consenso_marketing\",\s*False\)\)",
        main_source,
    ), "la semantica esistente di consenso_marketing in main.py e' cambiata inaspettatamente"
    assert "consenso_marketing, consenso_marketing_at" in main_source
    assert "s.consenso_marketing" in main_source


def test_no_other_migration_already_defines_these_columns():
    # Guardia anti-duplicazione: se una migration precedente avesse gia'
    # aggiunto queste colonne, la 018 sarebbe superflua/rischiosa da
    # aggiungere una seconda volta con semantica diversa.
    migrations_dir = ROOT / "migrations"
    for path in sorted(migrations_dir.glob("*.sql")):
        if path.name in {UP.name, DOWN.name}:
            continue
        text = path.read_text(encoding="utf-8")
        assert "consenso_marketing" not in text, (
            f"{path.name} referenzia gia' consenso_marketing - la 018 duplicherebbe una migration esistente"
        )
