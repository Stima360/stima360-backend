from __future__ import annotations

from decimal import Decimal
import json

from property_watch import repository


class CursorContext:
    def __init__(self, cursor):
        self.cursor = cursor

    def __enter__(self):
        return None, self.cursor

    def __exit__(self, _exc_type, _exc_value, _traceback):
        return False


def test_collection_repository_primitives_use_exact_internal_sources(monkeypatch):
    class Cursor:
        def __init__(self):
            self.executions = []

        def execute(self, query, params=None):
            self.executions.append((query, params))

        def fetchone(self):
            return {"prezzo_mq_base": Decimal("1500.00")}

    cursor = Cursor()

    zone_value = repository.get_zone_value(cursor, "Alba Adriatica", "Nord")

    assert zone_value == Decimal("1500.00")
    query, params = cursor.executions[0]
    assert "FROM zone_valori" in query
    assert "comune = %s" in query and "microzona = %s" in query
    assert params == ("Alba Adriatica", "Nord")
    assert "LOWER" not in query and "JOIN" not in query


def test_count_internal_supply_uses_exact_allowlist_and_archive_filter():
    class Cursor:
        def __init__(self):
            self.query = ""
            self.params = None

        def execute(self, query, params=None):
            self.query = query
            self.params = params

        def fetchone(self):
            return {"supply_count": 0}

    cursor = Cursor()

    assert repository.count_internal_supply(cursor, "Alba Adriatica", "Nord") == 0
    assert "FROM properties" in cursor.query
    assert "city = %s" in cursor.query and "microzone = %s" in cursor.query
    assert "archived_at IS NULL" in cursor.query
    assert "commercial_status IN" in cursor.query
    assert cursor.params[:2] == ("Alba Adriatica", "Nord")
    assert set(cursor.params[2:]) == {
        "mandate",
        "active",
        "reserved",
        "under_offer",
    }
    assert "JOIN" not in cursor.query


def test_active_watch_stima_ids_are_deterministic_and_exclude_null_or_inactive(monkeypatch):
    class Cursor:
        def __init__(self):
            self.query = ""

        def execute(self, query, _params=None):
            self.query = query

        def fetchall(self):
            return [{"stima_id": 9}, {"stima_id": 21}]

    cursor = Cursor()
    monkeypatch.setattr(
        repository,
        "property_watch_cursor",
        lambda **_kwargs: CursorContext(cursor),
    )

    assert repository.list_active_watch_stima_ids() == [9, 21]
    assert "status = 'active'" in cursor.query
    assert "stima_id IS NOT NULL" in cursor.query
    assert "ORDER BY id ASC" in cursor.query


def test_latest_relevant_observation_is_ordered_by_timestamp_then_id():
    class Cursor:
        def __init__(self):
            self.query = ""
            self.params = None

        def execute(self, query, params=None):
            self.query = query
            self.params = params

        def fetchone(self):
            return {"id": 8, "observation_type": "microzone_price_changed"}

    cursor = Cursor()

    observation = repository.get_latest_relevant_observation(
        cursor,
        3,
        ("watch_started", "microzone_price_changed"),
    )

    assert observation == {"id": 8, "observation_type": "microzone_price_changed"}
    assert cursor.params == (3, ["watch_started", "microzone_price_changed"])
    assert "observation_type = ANY(%s)" in cursor.query
    assert "ORDER BY observed_at DESC, id DESC" in cursor.query


def test_collection_context_locks_active_watch_and_reads_watch_started():
    class Cursor:
        def __init__(self):
            self.executions = []
            self.rows = [
                {"id": 3, "stima_id": 501, "status": "active"},
                {
                    "id": 4,
                    "watch_id": 3,
                    "observation_type": "watch_started",
                    "payload": {"comune": "Alba Adriatica"},
                },
            ]

        def execute(self, query, params=None):
            self.executions.append((query, params))

        def fetchone(self):
            return self.rows.pop(0)

    cursor = Cursor()

    context = repository.get_collection_context_for_update(cursor, 501)

    assert context["watch"]["id"] == 3
    assert context["baseline"]["id"] == 4
    watch_query, watch_params = cursor.executions[0]
    assert "status = 'active'" in watch_query
    assert "FOR UPDATE" in watch_query
    assert watch_params == (501,)
    baseline_query, baseline_params = cursor.executions[1]
    assert "observation_type = 'watch_started'" in baseline_query
    assert baseline_params == (3,)


def test_insert_observation_serializes_decimal_payload_as_json_numeric(monkeypatch):
    captured = {}

    class Cursor:
        def execute(self, query, params=None):
            if "INSERT INTO property_watch_observations" in query:
                captured["payload"] = params[3]

        def fetchone(self):
            return {"id": 4, "watch_id": 3}

    monkeypatch.setattr(
        repository,
        "property_watch_cursor",
        lambda **_kwargs: CursorContext(Cursor()),
    )

    repository.insert_observation(
        3,
        "microzone_price_changed",
        "internal",
        {"current": Decimal("1500.00")},
        "property_watch:test:v1",
    )

    payload = captured["payload"]
    assert b"1500.0" in payload.getquoted()
    assert json.loads(payload.dumps(payload.adapted)) == {"current": 1500.0}
