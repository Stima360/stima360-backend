"""P26-2C - static checks for migration 036_p26_property_agency_enforce.

Offline, stdlib only. Nothing here opens a database or applies a migration:
every assertion is made against the SQL text and the runner's own validation,
the same discipline as tests/test_p26_2c_migration_035.py.

035 filled properties.agency_id for all historical rows and left the column
nullable. 036 closes the enforcement layer:

    034  nullable structure           (applied)
    035  controlled backfill          (applied)
    036  NOT NULL + integrity         <- this file

WHAT 036 DOES:

1. Prechecks (HARD FAIL if count > 0):
   - properties with agency_id IS NULL
   - property_contacts with property agency != contact agency
   - property_leads with property agency != lead agency
   - property_visits with contact agency mismatch
   - property_visits with lead agency mismatch

2. ALTER TABLE properties ALTER COLUMN agency_id SET NOT NULL

3. CREATE OR REPLACE FUNCTION property_agency_integrity()
   - PL/pgSQL; uses TG_TABLE_NAME and NEW; no hardcoded agency_id
   - raises RAISE EXCEPTION on mismatch

4. Triggers BEFORE INSERT OR UPDATE on exactly three child tables:
   - property_contacts  -> trg_property_contacts_agency_integrity
   - property_leads     -> trg_property_leads_agency_integrity
   - property_visits    -> trg_property_visits_agency_integrity

5. No agency_id column added to any child table.

DOWN file drops triggers, function, and the NOT NULL constraint. No data
modification, no zeroing of agency_id.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from test_p26_1_migration_rules import (
    RUNNER,
    _load,
    _strip_sql_comments,
    assert_p26_migration_rules,
)

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"

VERSION = "036_p26_property_agency_enforce"
UP_PATH = MIGRATIONS / f"{VERSION}.sql"
DOWN_PATH = MIGRATIONS / f"{VERSION}_down.sql"

# The three child tables that get triggers in this migration.
TRIGGER_TABLES = (
    "property_contacts",
    "property_leads",
    "property_visits",
)

TRIGGER_NAMES = (
    "trg_property_contacts_agency_integrity",
    "trg_property_leads_agency_integrity",
    "trg_property_visits_agency_integrity",
)


@pytest.fixture(scope="module")
def runner_module():
    return _load(RUNNER, "p26_migrate_036")


def _up() -> str:
    return _strip_sql_comments(UP_PATH.read_text(encoding="utf-8"))


def _down() -> str:
    return _strip_sql_comments(DOWN_PATH.read_text(encoding="utf-8"))


def _squash(sql: str) -> str:
    return " ".join(sql.split())


def _strip_sql_strings(sql: str) -> str:
    return re.sub(r"'(?:[^']|'')*'", " ", sql)


# ---------------------------------------------------------------------------
# 1. Existence and shared rules
# ---------------------------------------------------------------------------

def test_036_files_exist():
    assert UP_PATH.exists(), f"missing {UP_PATH}"
    assert DOWN_PATH.exists(), f"missing {DOWN_PATH}"


def test_036_obeys_shared_rules_as_reversible():
    assert_p26_migration_rules(VERSION, expect_reversible=True)


def test_036_up_has_no_manual_begin_or_commit():
    up = UP_PATH.read_text(encoding="utf-8")
    assert not re.search(r"^\s*BEGIN\s*;", up, re.IGNORECASE | re.MULTILINE)
    assert not re.search(r"^\s*COMMIT\s*;", up, re.IGNORECASE | re.MULTILINE)


def test_036_down_brackets_its_own_transaction():
    down = DOWN_PATH.read_text(encoding="utf-8")
    assert re.search(r"^\s*BEGIN\s*;", down, re.IGNORECASE | re.MULTILINE)
    assert re.search(r"^\s*COMMIT\s*;", down, re.IGNORECASE | re.MULTILINE)


def test_runner_discovers_contiguous_026_to_036(runner_module):
    present = sorted(m.number for m in runner_module.discover_migrations(MIGRATIONS))
    assert present[:11] == list(range(26, 37)), present
    for later in present[11:]:
        assert later > 36, present


def test_runner_validates_036_without_complaint(runner_module):
    migration = next(
        m for m in runner_module.discover_migrations(MIGRATIONS) if m.number == 36
    )
    violations = runner_module.validate_migration(migration)
    assert violations == [], violations


def test_036_is_transactional(runner_module):
    migration = next(
        m for m in runner_module.discover_migrations(MIGRATIONS) if m.number == 36
    )
    assert not migration.non_transactional


# ---------------------------------------------------------------------------
# 2. NOT NULL enforcement present
# ---------------------------------------------------------------------------

def test_set_not_null_on_properties_agency_id():
    up = _squash(_strip_sql_strings(_up())).upper()
    assert "ALTER TABLE PROPERTIES ALTER COLUMN AGENCY_ID SET NOT NULL" in up or \
           re.search(
               r"ALTER\s+TABLE\s+PROPERTIES\s+ALTER\s+COLUMN\s+AGENCY_ID\s+SET\s+NOT\s+NULL",
               up
           )


def test_no_backfill_update_in_036():
    up = _squash(_strip_sql_strings(_up())).upper()
    # UPDATE must not appear at statement level (prechecks only SELECT/COUNT)
    # A bare UPDATE properties ... SET agency_id would be a backfill — forbidden.
    assert not re.search(r"\bUPDATE\s+PROPERTIES\b", up), \
        "036 must not backfill data; that is 035's job"


def test_no_hardcoded_agency_id_in_036():
    up = _squash(_up())
    # No numeric literal assigned to agency_id (e.g. agency_id = 1)
    assert not re.search(r"\bagency_id\s*[:=]+\s*\d+\b", up), \
        "hardcoded agency_id numeric literal found in 036"


# ---------------------------------------------------------------------------
# 3. Prechecks present
# ---------------------------------------------------------------------------

def test_precheck_null_agency_on_properties():
    up = _squash(_strip_sql_strings(_up())).lower()
    assert re.search(r"from\s+properties\s+where\s+agency_id\s+is\s+null", up), \
        "precheck for NULL agency_id on properties missing"
    assert re.search(r"raise\s+exception", up), "no RAISE EXCEPTION in precheck"


def test_precheck_property_contacts_mismatch():
    up = _squash(_strip_sql_strings(_up())).lower()
    assert "property_contacts" in up
    # Cross-join to properties and contacts, mismatch comparison
    assert re.search(r"p\.agency_id\s*<>\s*c\.agency_id", up), \
        "property_contacts mismatch precheck missing"


def test_precheck_property_leads_mismatch():
    up = _squash(_strip_sql_strings(_up())).lower()
    assert "property_leads" in up
    assert re.search(r"p\.agency_id\s*<>\s*l\.agency_id", up), \
        "property_leads mismatch precheck missing"


def test_precheck_property_visits_contact_mismatch():
    up = _squash(_strip_sql_strings(_up())).lower()
    assert "property_visits" in up
    # Visits precheck for contact mismatch
    assert re.search(
        r"property_visits.*?p\.agency_id\s*<>\s*c\.agency_id"
        r"|p\.agency_id\s*<>\s*c\.agency_id.*?property_visits",
        up, re.DOTALL
    ), "property_visits contact mismatch precheck missing"


def test_precheck_property_visits_lead_mismatch():
    up = _squash(_strip_sql_strings(_up())).lower()
    # Visits precheck for lead mismatch
    assert re.search(
        r"property_visits.*?p\.agency_id\s*<>\s*l\.agency_id"
        r"|p\.agency_id\s*<>\s*l\.agency_id.*?property_visits",
        up, re.DOTALL
    ), "property_visits lead mismatch precheck missing"


# ---------------------------------------------------------------------------
# 4. Function property_agency_integrity()
# ---------------------------------------------------------------------------

def test_function_property_agency_integrity_present():
    up = _squash(_strip_sql_strings(_up())).lower()
    assert re.search(r"function\s+property_agency_integrity\s*\(\s*\)", up), \
        "function property_agency_integrity() not found in 036"


def test_function_uses_tg_table_name():
    up = _squash(_up()).lower()
    assert "tg_table_name" in up, "function must branch on TG_TABLE_NAME"


def test_function_uses_new():
    up = _squash(_up()).lower()
    assert "new." in up or "new " in up, "function must reference NEW"


def test_function_raises_exception_on_mismatch():
    up = _squash(_strip_sql_strings(_up())).lower()
    assert re.search(r"raise\s+exception", up), \
        "function must RAISE EXCEPTION on agency mismatch"


def test_function_does_not_hardcode_agency():
    up = _squash(_up())
    assert not re.search(r"\bagency_id\s*[:=]+\s*\d+\b", up), \
        "hardcoded agency_id numeric literal found in trigger function"


# ---------------------------------------------------------------------------
# 5. Triggers on exactly 3 child tables
# ---------------------------------------------------------------------------

def test_triggers_on_exactly_three_child_tables():
    up = _squash(_up()).lower()
    trigger_count = sum(
        1 for t in TRIGGER_TABLES
        if t.lower() in up and re.search(
            rf"create\s+trigger\s+\S+.*?on\s+{re.escape(t)}\b", up, re.DOTALL
        )
    )
    assert trigger_count == 3, \
        f"expected triggers on exactly 3 tables, found on {trigger_count}"


def test_trigger_names_present():
    up = _squash(_up()).lower()
    for name in TRIGGER_NAMES:
        assert name.lower() in up, f"trigger name {name!r} not found in 036"


def test_triggers_are_before_insert_or_update():
    up = _squash(_strip_sql_strings(_up())).upper()
    assert re.search(r"BEFORE\s+INSERT\s+OR\s+UPDATE", up), \
        "triggers must be BEFORE INSERT OR UPDATE"


def test_no_agency_id_column_added_to_child_tables():
    up = _squash(_strip_sql_strings(_up())).upper()
    for table in TRIGGER_TABLES:
        assert not re.search(
            rf"ALTER\s+TABLE\s+{table.upper()}\s+ADD\s+COLUMN.*?AGENCY_ID",
            up
        ), f"036 must not add agency_id to child table {table}"


def test_036_does_not_reference_p20():
    """036 is PROPERTY enforcement. P20 (property_watches, property_watch_observations) is out of scope."""
    up = _up().lower()
    down = _down().lower()
    for table in ("property_watches", "property_watch_observations"):
        assert table not in up, f"036 UP references {table}"
        assert table not in down, f"036 DOWN references {table}"


# ---------------------------------------------------------------------------
# 6. DOWN migration assertions
# ---------------------------------------------------------------------------

def test_down_drops_all_three_triggers():
    down = _squash(_strip_sql_strings(_down())).lower()
    for name in TRIGGER_NAMES:
        assert re.search(
            rf"drop\s+trigger\s+if\s+exists\s+{re.escape(name.lower())}",
            down
        ), f"DOWN does not drop trigger {name!r}"


def test_down_drops_function():
    down = _squash(_strip_sql_strings(_down())).lower()
    assert re.search(r"drop\s+function\s+if\s+exists\s+property_agency_integrity\s*\(\s*\)", down), \
        "DOWN must DROP FUNCTION IF EXISTS property_agency_integrity()"


def test_down_drops_not_null():
    down = _squash(_strip_sql_strings(_down())).upper()
    assert re.search(
        r"ALTER\s+TABLE\s+PROPERTIES\s+ALTER\s+COLUMN\s+AGENCY_ID\s+DROP\s+NOT\s+NULL",
        down
    ), "DOWN must DROP NOT NULL on properties.agency_id"


def test_down_does_not_zero_agency_id():
    down = _squash(_strip_sql_strings(_down())).lower()
    assert not re.search(r"update\s+properties\s+set\s+agency_id", down), \
        "DOWN must not modify agency_id data"


# ---------------------------------------------------------------------------
# 7. Layer B - Mutation probe suite
# ---------------------------------------------------------------------------

def _all_probe_failures(raw_sql: str) -> list[str]:
    up = _strip_sql_comments(raw_sql)
    flat = _squash(_strip_sql_strings(up)).lower()
    raw_flat = _squash(up).lower()
    failures: list[str] = []

    # SET NOT NULL must be present
    if not re.search(
        r"alter\s+table\s+properties\s+alter\s+column\s+agency_id\s+set\s+not\s+null",
        flat
    ):
        failures.append("SET NOT NULL on properties.agency_id missing")

    # No backfill UPDATE
    if re.search(r"\bupdate\s+properties\b", flat):
        failures.append("backfill UPDATE on properties present")

    # No hardcoded agency numeric literal
    if re.search(r"\bagency_id\s*[:=]+\s*\d+\b", raw_flat):
        failures.append("hardcoded agency_id numeric literal found")

    # Precheck: NULL agency
    if not re.search(r"from\s+properties\s+where\s+agency_id\s+is\s+null", flat):
        failures.append("precheck for NULL agency_id on properties missing")

    # Precheck: property_contacts mismatch
    if "property_contacts" not in flat or \
            not re.search(r"p\.agency_id\s*<>\s*c\.agency_id", flat):
        failures.append("property_contacts mismatch precheck missing")

    # Precheck: property_leads mismatch
    if "property_leads" not in flat or \
            not re.search(r"p\.agency_id\s*<>\s*l\.agency_id", flat):
        failures.append("property_leads mismatch precheck missing")

    # Precheck: property_visits (contact)
    if not re.search(
        r"property_visits.*?p\.agency_id\s*<>\s*c\.agency_id"
        r"|p\.agency_id\s*<>\s*c\.agency_id.*?property_visits",
        flat, re.DOTALL
    ):
        failures.append("property_visits contact mismatch precheck missing")

    # Precheck: property_visits (lead)
    if not re.search(
        r"property_visits.*?p\.agency_id\s*<>\s*l\.agency_id"
        r"|p\.agency_id\s*<>\s*l\.agency_id.*?property_visits",
        flat, re.DOTALL
    ):
        failures.append("property_visits lead mismatch precheck missing")

    # Function present
    if not re.search(r"function\s+property_agency_integrity\s*\(\s*\)", flat):
        failures.append("function property_agency_integrity() missing")

    # TG_TABLE_NAME used
    if "tg_table_name" not in raw_flat:
        failures.append("TG_TABLE_NAME not used in function")

    # RAISE EXCEPTION
    if not re.search(r"raise\s+exception", flat):
        failures.append("RAISE EXCEPTION not present in function")

    # Triggers on all 3 tables
    for table in TRIGGER_TABLES:
        if not re.search(
            rf"create\s+trigger\s+\S+.*?on\s+{re.escape(table)}\b", flat, re.DOTALL
        ):
            failures.append(f"trigger on {table} missing")

    # BEFORE INSERT OR UPDATE
    if not re.search(r"before\s+insert\s+or\s+update", flat):
        failures.append("BEFORE INSERT OR UPDATE not present")

    # No agency_id column added to child tables
    for table in TRIGGER_TABLES:
        if re.search(
            rf"alter\s+table\s+{re.escape(table)}\s+add\s+column.*?agency_id",
            flat
        ):
            failures.append(f"agency_id column added to child table {table}")

    return failures


MUTANTS = {
    "SET NOT NULL removed": lambda s: re.sub(
        r"ALTER TABLE properties\s+ALTER COLUMN agency_id SET NOT NULL\s*;",
        "-- SET NOT NULL removed",
        s,
        flags=re.IGNORECASE,
    ),
    "backfill UPDATE introduced": lambda s: (
        s + "\nUPDATE properties SET agency_id = agency_id WHERE agency_id IS NOT NULL;\n"
    ),
    "hardcoded agency_id": lambda s: (
        s + "\n-- test mutation\nDO $$ BEGIN PERFORM 1 WHERE agency_id = 1; END $$;\n"
    ),
    "null precheck removed": lambda s: re.sub(
        r"WHERE\s+agency_id\s+IS\s+NULL",
        "WHERE false",
        s,
        count=1,
        flags=re.IGNORECASE,
    ),
    "property_contacts precheck removed": lambda s: s.replace(
        "p.agency_id <> c.agency_id", "false"
    ),
    "property_leads precheck removed": lambda s: s.replace(
        "p.agency_id <> l.agency_id", "false"
    ),
    "function missing": lambda s: re.sub(
        r"CREATE OR REPLACE FUNCTION property_agency_integrity.*?LANGUAGE plpgsql\s*;",
        "-- function removed",
        s,
        flags=re.IGNORECASE | re.DOTALL,
    ),
    "RAISE EXCEPTION removed": lambda s: re.sub(
        r"RAISE EXCEPTION",
        "-- RAISE EXCEPTION",
        s,
        flags=re.IGNORECASE,
    ),
    "trigger on property_contacts removed": lambda s: re.sub(
        r"DO \$do\$\s*BEGIN\s*IF NOT EXISTS.*?trg_property_contacts_agency_integrity.*?END\s*\$do\$\s*;",
        "-- trigger removed",
        s,
        flags=re.IGNORECASE | re.DOTALL,
    ),
    "BEFORE INSERT OR UPDATE changed": lambda s: s.replace(
        "BEFORE INSERT OR UPDATE", "AFTER INSERT OR UPDATE"
    ),
}


def test_real_migration_passes_all_probes():
    assert _all_probe_failures(UP_PATH.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_every_mutant_is_caught(name):
    original = UP_PATH.read_text(encoding="utf-8")
    mutant = MUTANTS[name](original)
    assert mutant != original, f"mutation {name!r} did not change SQL"
    assert _all_probe_failures(mutant), f"mutation {name!r} went uncaught"
