from pathlib import Path


MIGRATION = Path("migrations/021_public_stima_sell_pipeline.sql")


def test_public_stima_sell_pipeline_backfill_migration_exists():
    assert MIGRATION.exists()


def test_public_stima_sell_pipeline_backfill_is_scoped_and_idempotent():
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    assert "update leads" in sql
    assert "pipeline = 'sell'" in sql
    assert "source = 'public_stima'" in sql
    assert "pipeline = 'general'" in sql
