"""P26-2C2C - static checks for migration 035_p26_property_agency_backfill.

Offline, stdlib only. Nothing here opens a database or applies a migration:
every assertion is made against the SQL text and the runner's own validation,
the same discipline as tests/test_p26_2b_migration_032.py and
tests/test_p26_1_migration_029.py.

034 added `properties.agency_id` as nullable. 035 fills historical rows and
nothing else:

    034  nullable structure           (applied)
    035  controlled backfill          <- this file
    ---  NOT NULL / integrity         (a later block)

PROVENANCE:
Candidate agencies are computed strictly from:
    1. property_contacts -> contacts.agency_id
    2. property_leads    -> leads.agency_id

RULES:
- Zero candidates: conservative fallback to Default Agency (slug 'stima360', status 'active'),
  PERMITTED ONLY IF no non-default agency existed at or before properties.created_at.
  (agencies.created_at <= properties.created_at).
- Exactly one candidate: that is the property's agency.
- More than one candidate: HARD FAIL of the migration.
- Prefilled properties: if already populated, must match candidate provenance;
  any contradiction causes HARD FAIL.
- No MIN, MAX, LIMIT 1, COALESCE or hardcoded IDs to select tenancy.
- Post-checks verify:
    1. row count unchanged
    2. properties.agency_id IS NULL = 0
    3. provenance conflicts = 0
    4. no property carries an agency different from certain provenance
    5. no arbitrary tenancy decisions
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

VERSION = "035_p26_property_agency_backfill"
UP_PATH = MIGRATIONS / f"{VERSION}.sql"
DOWN_PATH = MIGRATIONS / f"{VERSION}_down.sql"

# Tables involved in provenance and backfill
PROVENANCE_TABLES = (
    "properties",
    "property_contacts",
    "contacts",
    "property_leads",
    "leads",
    "agencies",
)


@pytest.fixture(scope="module")
def runner_module():
    return _load(RUNNER, "p26_migrate_035")


def _up() -> str:
    return _strip_sql_comments(UP_PATH.read_text(encoding="utf-8"))


def _down() -> str:
    return _strip_sql_comments(DOWN_PATH.read_text(encoding="utf-8"))


def _squash(sql: str) -> str:
    return " ".join(sql.split())


def _strip_sql_strings(sql: str) -> str:
    return re.sub(r"'(?:[^']|'')*'", " ", sql)


def _statements(sql: str) -> list[str]:
    out: list[str] = []
    depth_marker = None
    buffer: list[str] = []
    i = 0
    while i < len(sql):
        if depth_marker is None:
            match = re.match(r"\$[A-Za-z_]*\$", sql[i:])
            if match:
                depth_marker = match.group(0)
                buffer.append(depth_marker)
                i += len(depth_marker)
                continue
            if sql[i] == ";":
                out.append("".join(buffer))
                buffer = []
                i += 1
                continue
        else:
            if sql.startswith(depth_marker, i):
                buffer.append(depth_marker)
                i += len(depth_marker)
                depth_marker = None
                continue
        buffer.append(sql[i])
        i += 1
    if "".join(buffer).strip():
        out.append("".join(buffer))
    return [s.strip() for s in out if s.strip()]


# ---------------------------------------------------------------------------
# 1. Existence and Shared Rules
# ---------------------------------------------------------------------------

def test_035_files_exist():
    assert UP_PATH.exists(), f"missing {UP_PATH}"
    assert DOWN_PATH.exists(), f"missing {DOWN_PATH}"


def test_035_obeys_shared_rules_as_irreversible():
    assert_p26_migration_rules(VERSION, expect_reversible=False)


def test_035_up_has_no_manual_begin_or_commit():
    up = UP_PATH.read_text(encoding="utf-8")
    assert not re.search(r"^\s*BEGIN\s*;", up, re.IGNORECASE | re.MULTILINE)
    assert not re.search(r"^\s*COMMIT\s*;", up, re.IGNORECASE | re.MULTILINE)


def test_035_down_brackets_its_own_transaction():
    down = DOWN_PATH.read_text(encoding="utf-8")
    assert re.search(r"^\s*BEGIN\s*;", down, re.IGNORECASE | re.MULTILINE)
    assert re.search(r"^\s*COMMIT\s*;", down, re.IGNORECASE | re.MULTILINE)


def test_runner_discovers_contiguous_026_to_035(runner_module):
    present = sorted(m.number for m in runner_module.discover_migrations(MIGRATIONS))
    assert present[:10] == list(range(26, 36)), present
    for later in present[10:]:
        assert later > 35, present


def test_runner_validates_035_without_complaint(runner_module):
    migration = next(
        m for m in runner_module.discover_migrations(MIGRATIONS) if m.number == 35
    )
    violations = runner_module.validate_migration(migration)
    assert violations == [], violations


def test_035_is_transactional(runner_module):
    migration = next(
        m for m in runner_module.discover_migrations(MIGRATIONS) if m.number == 35
    )
    assert not migration.non_transactional


# ---------------------------------------------------------------------------
# 2. Data-Only: No Schema or DDL Changes
# ---------------------------------------------------------------------------

def test_no_set_not_null():
    up = _squash(_strip_sql_strings(_up())).upper()
    assert "SET NOT NULL" not in up, "035 must leave properties.agency_id nullable"


def test_no_ddl_triggers_or_functions():
    up = _squash(_strip_sql_strings(_up())).upper()
    for forbidden in (
        "ALTER TABLE",
        "CREATE TABLE",
        "CREATE TRIGGER",
        "CREATE FUNCTION",
        "CREATE OR REPLACE FUNCTION",
        "CREATE INDEX",
        "DROP TABLE",
        "DROP COLUMN",
        "TRUNCATE",
        "DELETE FROM",
        "INSERT INTO",
    ):
        assert forbidden not in up, f"035 contains forbidden DDL/statement: {forbidden}"


def test_035_does_not_reference_p20():
    """035 is PROPERTY backfill. P20 (property_watches, property_watch_observations) is out of scope."""
    up = _up().lower()
    down = _down().lower()
    for table in ("property_watches", "property_watch_observations"):
        assert table not in up, f"035 UP references {table}"
        assert table not in down, f"035 DOWN references {table}"


# ---------------------------------------------------------------------------
# 3. Default Agency & Fallback Rules
# ---------------------------------------------------------------------------

def test_no_hardcoded_agency_id_in_tenancy_decision():
    up = _squash(_up())
    # No agency_id = 1 or agency_id := 1
    assert not re.search(r"\bagency_id\s*[:=]+\s*1\b", up), "hardcoded agency_id = 1 found"


def test_default_agency_resolved_via_slug_and_status():
    up = _squash(_up())
    assert "slug = 'stima360'" in up or "slug = ''stima360''" in up
    assert "status = 'active'" in up or "status = ''active''" in up
    # Must count default agencies and fail if <> 1
    assert re.search(r"slug\s*=\s*'stima360'\s+and\s+status\s*=\s*'active'", up, re.IGNORECASE)


def test_fallback_uses_properties_and_agencies_created_at():
    up = _squash(_up()).lower()
    assert "properties.created_at" in up or "p.created_at" in up
    assert "agencies.created_at" in up or "a.created_at" in up
    assert re.search(r"a\.created_at\s*<=\s*p\.created_at", up)


def test_fallback_checks_non_default_agencies():
    up = _squash(_up()).lower()
    # Checks that no non-default agency existed prior to property creation
    assert re.search(r"a\.(?:slug|id)\s*<>\s*(?:'stima360'|v_default_agency_id)", up)


# ---------------------------------------------------------------------------
# 4. Provenance Derivation & Guards
# ---------------------------------------------------------------------------

def test_provenance_consults_property_contacts_and_contacts():
    up = _squash(_up()).lower()
    assert "property_contacts" in up
    assert re.search(r"join\s+contacts\b", up)
    assert re.search(r"c\.agency_id", up)


def test_provenance_consults_property_leads_and_leads():
    up = _squash(_up()).lower()
    assert "property_leads" in up
    assert re.search(r"join\s+leads\b", up)
    assert re.search(r"l\.agency_id", up)


def test_hard_fail_on_multi_candidate_agencies():
    up = _squash(_strip_sql_strings(_up())).lower()
    assert re.search(r"count\s*\(\s*distinct\s+agency_id\s*\)\s*>\s*1", up)


def test_hard_fail_on_contradictory_prefilled_property():
    up = _squash(_strip_sql_strings(_up())).lower()
    assert re.search(r"p\.agency_id\s*<>\s*candidates\.agency_id", up) or \
           re.search(r"p\.agency_id\s*!=\s*candidates\.agency_id", up)


def test_updates_only_target_null_agency():
    up = _strip_sql_strings(_up())
    updates = [s for s in _statements(up) if s.lstrip().lower().startswith("update")]
    assert len(updates) == 2, f"expected exactly 2 updates, found {len(updates)}"
    for u in updates:
        squashed = _squash(u).lower()
        assert "where" in squashed
        assert "agency_id is null" in squashed


def test_no_arbitrary_selection_heuristics():
    up = _squash(_strip_sql_strings(_up())).upper()
    for heuristic in ("MIN(", "MAX(", "LIMIT 1", "COALESCE("):
        assert heuristic not in up, f"forbidden heuristic found in 035: {heuristic}"


# ---------------------------------------------------------------------------
# 5. Post-Checks
# ---------------------------------------------------------------------------

def test_post_check_null_agency_is_zero():
    up = _squash(_strip_sql_strings(_up())).lower()
    assert re.search(r"from\s+properties\s+where\s+agency_id\s+is\s+null", up)
    assert "v_null_agency_count <> 0" in up


def test_post_check_row_count_invariance():
    up = _squash(_up()).lower()
    assert "properties_before" in up
    assert "v_before <> v_after" in up


def test_post_check_provenance_coherence():
    up = _squash(_strip_sql_strings(_up())).lower()
    assert re.search(r"p\.agency_id\s*<>\s*candidates\.agency_id", up)


# ---------------------------------------------------------------------------
# 6. Down Migration Assertions
# ---------------------------------------------------------------------------

def test_down_does_not_set_agency_id_null():
    down = _squash(_strip_sql_strings(_down())).lower()
    assert not re.search(r"update\s+properties\s+set\s+agency_id\s*=\s*null", down)


def test_down_refuses_and_mentions_restore_from_backup():
    down = _squash(_down()).lower()
    assert "raise exception" in down
    assert "restore required from pre-035 database backup" in down


# ---------------------------------------------------------------------------
# 7. Layer B - Mutation Table Probes
# ---------------------------------------------------------------------------

def _all_probe_failures(raw_sql: str) -> list[str]:
    up = _strip_sql_comments(raw_sql)
    raw_flat = _squash(up).lower()
    squashed = _squash(_strip_sql_strings(up))
    flat = squashed.lower()
    failures: list[str] = []

    if "set not null" in flat:
        failures.append("SET NOT NULL introduced")

    if re.search(r"alter table|create table|create trigger|create function|create index", flat):
        failures.append("DDL/trigger/function introduced")

    if re.search(r"\bagency_id\s*[:=]+\s*1\b", flat):
        failures.append("hardcoded agency_id = 1")

    if "slug = 'stima360'" not in raw_flat and "slug = ''stima360''" not in raw_flat:
        failures.append("Default Agency slug missing")

    if "properties_before" not in raw_flat or "v_before <> v_after" not in flat:
        failures.append("row count check missing")

    if not re.search(r"count\s*\(\s*distinct\s+agency_id\s*\)\s*>\s*1", flat):
        failures.append("ambiguous provenance check missing")

    if not re.search(r"p\.agency_id\s+is\s+not\s+null\s+and\s+p\.agency_id\s*<>\s*candidates\.agency_id", flat):
        failures.append("prefilled contradiction check missing")

    if "a.created_at <= p.created_at" not in flat and "agencies.created_at <= properties.created_at" not in flat:
        failures.append("fallback timestamp check missing")

    if any(h in squashed.upper() for h in ("MIN(", "MAX(", "LIMIT 1", "COALESCE(")):
        failures.append("heuristic tenant selection used")

    if "v_null_agency_count <> 0" not in flat:
        failures.append("null agency post-check missing")

    return failures


MUTANTS = {
    "SET NOT NULL introduced":
        lambda s: s + "\nALTER TABLE properties ALTER COLUMN agency_id SET NOT NULL;\n",
    "hardcoded agency 1":
        lambda s: s.replace("slug = 'stima360'", "slug = 'stima360' --\nUPDATE properties SET agency_id = 1;"),
    "Default Agency slug removed":
        lambda s: s.replace("slug = 'stima360'", "id = 1"),
    "ambiguity check removed":
        lambda s: s.replace("HAVING count(DISTINCT agency_id) > 1", "HAVING false"),
    "prefilled conflict check removed":
        lambda s: s.replace(
            "p.agency_id IS NOT NULL\n       AND p.agency_id <> candidates.agency_id;",
            "false;",
        ),
    "fallback timestamp check removed":
        lambda s: s.replace("AND a.created_at <= p.created_at", "-- timestamp check removed"),
    "heuristic MIN introduced":
        lambda s: s.replace("prov.agency_id", "MIN(prov.agency_id)"),
    "row count check removed":
        lambda s: s.replace("v_before <> v_after", "false"),
    "null agency post-check removed":
        lambda s: s.replace("v_null_agency_count <> 0", "false"),
}


def test_real_migration_passes_all_probes():
    assert _all_probe_failures(UP_PATH.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_every_mutant_is_caught(name):
    original = UP_PATH.read_text(encoding="utf-8")
    mutant = MUTANTS[name](original)
    assert mutant != original, f"mutation {name!r} did not change SQL"
    assert _all_probe_failures(mutant), f"mutation {name!r} went uncaught"
