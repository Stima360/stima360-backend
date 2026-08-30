from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (ROOT / "migrations" / "007_match_02.sql").read_text(encoding="utf-8").lower()
ROLLBACK = (ROOT / "migrations" / "007_match_02_down.sql").read_text(encoding="utf-8").lower()


def test_migration_has_no_persistent_queue():
    assert "match_recalculation_queue" not in MIGRATION


def test_migration_does_not_alter_buy_property_or_core_tables():
    forbidden = (
        "alter table buy_requests",
        "alter table properties",
        "alter table contacts",
        "alter table leads",
        "alter table tasks",
        "alter table activities",
    )
    assert not any(value in MIGRATION for value in forbidden)


def test_migration_is_match_only_and_additive():
    assert "alter table matches" in MIGRATION
    assert "create table if not exists match_refresh_history" in MIGRATION
    assert "create table if not exists match_feedback" in MIGRATION
    assert "drop table" not in MIGRATION


def test_rollback_preserves_match_01_core_tables():
    assert "drop table if exists matches" not in ROLLBACK
    assert "drop table if exists match_runs" not in ROLLBACK
    assert "drop table if exists match_exclusions" not in ROLLBACK
    assert "drop table if exists match_requirement_results" not in ROLLBACK
