from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path

import pytest

from integration_p2_support import import_project_module
from property_watch import repository, service
from property_watch.exceptions import StimaNotFoundError


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "022_property_watch.sql"


def test_migration_is_additive_and_contains_database_idempotency_guarantees():
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    assert "begin;" in sql and "commit;" in sql
    assert "create table if not exists property_watches" in sql
    assert "create table if not exists property_watch_observations" in sql
    assert "unique index if not exists idx_property_watches_stima_id" in sql
    assert "unique index if not exists idx_property_watch_observations_idempotency_key" in sql
    assert "alter table stime" not in sql
    assert "alter table leads" not in sql
    assert "alter table contacts" not in sql
    assert "alter table properties" not in sql
    assert "alter table tasks" not in sql
    assert " drop " not in f" {sql} "


def test_service_initializes_one_watch_and_one_private_data_free_baseline(monkeypatch):
    stima = {
        "id": 501,
        "comune": "Alba Adriatica",
        "microzona": "Centro",
        "tipologia": "Appartamento",
        "mq": 90,
        "prezzo_mq_base": 1500,
    }
    calls = []

    def ensure(stima_id, baseline):
        calls.append((stima_id, baseline))
        return {
            "watch": {"id": 10, "stima_id": stima_id, "status": "active"},
            "baseline": {
                "observation_type": "watch_started",
                "source": "internal",
                "payload": baseline,
                "idempotency_key": f"property_watch:watch_started:stima:{stima_id}:v1",
            },
        }

    monkeypatch.setattr(repository, "get_stima_baseline_data", lambda stima_id: stima if stima_id == 501 else None)
    monkeypatch.setattr(
        repository,
        "get_stima_completed_valuation",
        lambda stima_id: {
            "price_exact": 210000,
            "eur_mq_finale": 2333.33,
            "base_mq": 1500,
        } if stima_id == 501 else None,
    )
    monkeypatch.setattr(repository, "ensure_watch_with_baseline", ensure)

    first = service.ensure_watch_for_stima(501)
    second = service.ensure_watch_for_stima(501)

    assert first["watch"]["id"] == second["watch"]["id"] == 10
    assert len(calls) == 2
    baseline = calls[0][1]
    assert baseline == {
        "comune": "Alba Adriatica",
        "microzona": "Centro",
        "tipologia": "Appartamento",
        "mq": 90,
        "prezzo_mq_base": 1500,
        "price_exact": 210000,
        "eur_mq_finale": 2333.33,
        "base_mq": 1500,
    }
    assert not {"email", "telefono", "nome", "cognome", "note"} & baseline.keys()


def test_service_rejects_nonexistent_stima_without_writing(monkeypatch):
    monkeypatch.setattr(repository, "get_stima_baseline_data", lambda _stima_id: None)
    monkeypatch.setattr(
        repository,
        "ensure_watch_with_baseline",
        lambda *_args: pytest.fail("a missing stima must not create a watch"),
    )

    with pytest.raises(StimaNotFoundError, match="501"):
        service.ensure_watch_for_stima(501)


def test_watch_baseline_preserves_completed_valuation(monkeypatch):
    stima = {
        "id": 501,
        "comune": "Alba Adriatica",
        "microzona": "Centro",
        "tipologia": "Appartamento",
        "mq": 90,
        "prezzo_mq_base": 1500,
    }
    completed = {
        "price_exact": 210000,
        "eur_mq_finale": 2333.33,
        "base_mq": 1500,
    }
    captured = {}

    monkeypatch.setattr(
        repository,
        "get_stima_baseline_data",
        lambda _stima_id: stima,
    )
    monkeypatch.setattr(
        repository,
        "get_stima_completed_valuation",
        lambda _stima_id: completed,
        raising=False,
    )

    def ensure(stima_id, baseline):
        captured["baseline"] = baseline
        return {
            "watch": {"id": 10, "stima_id": stima_id, "status": "active"},
            "baseline": {"payload": baseline},
        }

    monkeypatch.setattr(repository, "ensure_watch_with_baseline", ensure)

    service.ensure_watch_for_stima(501)

    assert captured["baseline"]["price_exact"] == 210000
    assert captured["baseline"]["eur_mq_finale"] == 2333.33
    assert captured["baseline"]["base_mq"] == 1500


def test_repository_serializes_decimal_baseline_price_as_json_numeric(monkeypatch):
    captured = {}

    class Cursor:
        def __init__(self):
            self.rows = [
                {"id": 10, "stima_id": 501, "status": "active"},
                {"id": 11, "watch_id": 10, "observation_type": "watch_started"},
            ]

        def execute(self, query, params=None):
            if "INSERT INTO property_watch_observations" in query:
                captured["baseline_json"] = params[1]

        def fetchone(self):
            return self.rows.pop(0)

    class CursorContext:
        def __init__(self, cursor):
            self.cursor = cursor

        def __enter__(self):
            return None, self.cursor

        def __exit__(self, _exc_type, _exc_value, _traceback):
            return False

    monkeypatch.setattr(
        repository,
        "property_watch_cursor",
        lambda **_kwargs: CursorContext(Cursor()),
    )

    repository.ensure_watch_with_baseline(
        501,
        {"prezzo_mq_base": Decimal("1500.00")},
    )

    baseline_json = captured["baseline_json"]
    assert b"1500.0" in baseline_json.getquoted()
    assert json.loads(baseline_json.dumps(baseline_json.adapted)) == {
        "prezzo_mq_base": 1500.0
    }


def test_record_observation_uses_deterministic_database_deduplication(monkeypatch):
    calls = []

    def insert(watch_id, observation_type, source, payload, idempotency_key, observed_at):
        calls.append((watch_id, observation_type, source, payload, idempotency_key, observed_at))
        return {"id": 3, "watch_id": watch_id, "idempotency_key": idempotency_key}

    monkeypatch.setattr(repository, "insert_observation", insert)

    result = service.record_observation(
        watch_id=10,
        observation_type="comparable_added",
        source="internal",
        payload={"comparable_id": 4},
        idempotency_key="property_watch:comparable:4:v1",
    )

    assert result["id"] == 3
    assert calls == [
        (
            10,
            "comparable_added",
            "internal",
            {"comparable_id": 4},
            "property_watch:comparable:4:v1",
            None,
        )
    ]


def test_read_model_derives_simple_state_from_history(monkeypatch):
    baseline = {
        "id": 1,
        "observation_type": "watch_started",
        "source": "internal",
        "payload": {"comune": "Alba Adriatica"},
        "observed_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
    }
    latest = {
        "id": 2,
        "observation_type": "comparable_added",
        "source": "internal",
        "payload": {"comparable_id": 4},
        "observed_at": datetime(2026, 9, 2, tzinfo=timezone.utc),
    }
    monkeypatch.setattr(
        repository,
        "get_watch_for_stima",
        lambda stima_id: {"id": 10, "stima_id": stima_id, "status": "active", "created_at": baseline["observed_at"]},
    )
    monkeypatch.setattr(repository, "list_observations", lambda _watch_id: [baseline, latest])

    state = service.get_current_watch_state(501)

    assert state["watch"]["id"] == 10
    assert state["baseline"] == baseline
    assert state["observation_count"] == 2
    assert state["observations"] == [baseline, latest]
    assert "computed_at" in state
    assert "score" not in state and "trend" not in state and "band" not in state


def test_public_stima_starts_watch_only_after_a_successful_calculation(monkeypatch):
    main_module = import_project_module("main")
    calls = []

    monkeypatch.setattr(main_module.property_watch_service, "safe_ensure_watch_for_stima", lambda stima_id: calls.append(stima_id))
    monkeypatch.setattr(main_module, "compute_from_payload", lambda _payload: (_ for _ in ()).throw(RuntimeError("calculation failed")))

    class Request:
        headers = {"content-type": "application/json"}

        async def json(self):
            return {
                "comune": "Alba Adriatica",
                "microzona": "Centro",
                "mq": 90,
                "nome": "Mario",
                "email": "mario@example.com",
                "telefono": "+39 333 123 4567",
                "prezzo_mq_base": 1500,
            }

    class Cursor:
        def execute(self, _query, _params=None):
            pass

        def fetchone(self):
            return (501,)

        def close(self):
            pass

    class Connection:
        def cursor(self, **_kwargs):
            return Cursor()

        def commit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(main_module, "get_connection", Connection)
    monkeypatch.setattr(main_module.core_service, "bridge_public_stima", lambda *_args, **_kwargs: {"status": "skipped"})
    monkeypatch.setattr(main_module.seller_intelligence_service, "safe_record_event", lambda **_kwargs: None)
    monkeypatch.setattr(main_module.followup_service, "safe_run_followup", lambda **_kwargs: None)

    with pytest.raises(RuntimeError, match="calculation failed"):
        asyncio.run(main_module.salva_stima(Request()))
    assert calls == []


def test_property_watch_routes_are_registered_and_admin_protected():
    main_module = import_project_module("main")
    paths = main_module.app.openapi()["paths"]

    for path in (
        "/api/property-watch/stime/{stima_id}",
        "/api/property-watch/stime/{stima_id}/initialize",
    ):
        assert path in paths
        assert next(iter(paths[path].values())).get("security")
