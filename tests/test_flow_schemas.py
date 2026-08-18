import hashlib
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError
from flow.schemas import ScanRequest,EventCreate

R = Path(__file__).parents[1]
EXPECTED_EVENT_SOURCES = {"core", "property", "buy", "match", "flow", "owner"}


def test_scan_default_limit(): assert ScanRequest().limit==50
def test_scan_max_limit(): assert ScanRequest(limit=200).limit==200
def test_scan_over_limit_rejected():
    with pytest.raises(ValidationError): ScanRequest(limit=201)


def test_event_source_allowlist_is_exact():
    annotation = EventCreate.model_fields["source_module"].annotation
    assert set(get_args(annotation)) == EXPECTED_EVENT_SOURCES


@pytest.mark.parametrize("source_module", sorted(EXPECTED_EVENT_SOURCES))
def test_event_source_allowlist_accepts_known_modules(source_module):
    event = EventCreate(event_type="x", entity_type="lead", entity_id=1, source_module=source_module)
    assert event.source_module == source_module


def test_event_source_allowlist_rejects_unknown_module():
    with pytest.raises(ValidationError):
        EventCreate(event_type="x", entity_type="lead", entity_id=1, source_module="email")


def test_p8_3a_migration_replaces_only_flow_event_source_module_check():
    sql = (R / "migrations/012_flow_owner_source.sql").read_text(encoding="utf-8")
    compact = " ".join(sql.split())
    assert "BEGIN;" in sql and "COMMIT;" in sql
    assert compact.count("ALTER TABLE flow_events") == 2
    assert "DROP CONSTRAINT flow_events_source_module_check" in compact
    assert "ADD CONSTRAINT flow_events_source_module_check" in compact
    assert "CHECK (source_module IN ('core','property','buy','match','flow','owner'))" in compact
    for forbidden in (
        "CREATE TABLE", "DROP TABLE", "TRUNCATE", "INSERT INTO", "UPDATE ",
        "DELETE FROM", "ALTER COLUMN", "ADD COLUMN", "DROP COLUMN",
    ):
        assert forbidden not in sql


def test_flow_01_baseline_migration_is_untouched():
    baseline = (R / "migrations/008_flow_01.sql").read_bytes()
    assert hashlib.sha256(baseline).hexdigest() == "942938d6f09095560b68950b040d5025bf4f9268496ae31fd5e96022ba1c2334"
