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


class MemoryCollectionStore:
    def __init__(self, baseline, zone_value=None, supply_count=0):
        self.baseline = baseline
        self.zone_value = zone_value
        self.supply_count = supply_count
        self.observations = []
        self.by_key = {}
        self.next_id = 100

    def get_latest(self, _cursor, _watch_id, observation_types):
        candidates = [
            item
            for item in [self.baseline, *self.observations]
            if item["observation_type"] in observation_types
        ]
        return candidates[-1] if candidates else None

    def get_zone_value(self, _cursor, _comune, _microzona):
        return self.zone_value

    def count_internal_supply(self, _cursor, _comune, _microzona):
        return self.supply_count

    def insert(self, _cursor, watch_id, observation_type, source, payload, key):
        if key in self.by_key:
            return self.by_key[key]
        self.next_id += 1
        observation = {
            "id": self.next_id,
            "watch_id": watch_id,
            "observation_type": observation_type,
            "source": source,
            "payload": payload,
            "idempotency_key": key,
        }
        self.observations.append(observation)
        self.by_key[key] = observation
        return observation


def _wire_microzone_collector(monkeypatch, store):
    cursor = object()
    monkeypatch.setattr(
        repository,
        "property_watch_cursor",
        lambda **_kwargs: CursorContext(cursor),
    )
    monkeypatch.setattr(
        repository,
        "get_collection_context_for_update",
        lambda _cursor, _stima_id: {
            "watch": {"id": 3, "stima_id": 501, "status": "active"},
            "baseline": store.baseline,
        },
    )
    monkeypatch.setattr(repository, "get_latest_relevant_observation", store.get_latest)
    monkeypatch.setattr(repository, "get_zone_value", store.get_zone_value)
    monkeypatch.setattr(repository, "_insert_observation_with_cursor", store.insert)


def _wire_supply_collector(monkeypatch, store):
    cursor = object()
    monkeypatch.setattr(
        repository,
        "property_watch_cursor",
        lambda **_kwargs: CursorContext(cursor),
    )
    monkeypatch.setattr(
        repository,
        "get_collection_context_for_update",
        lambda _cursor, _stima_id: {
            "watch": {"id": 3, "stima_id": 501, "status": "active"},
            "baseline": store.baseline,
        },
    )
    monkeypatch.setattr(repository, "get_latest_relevant_observation", store.get_latest)
    monkeypatch.setattr(repository, "count_internal_supply", store.count_internal_supply)
    monkeypatch.setattr(repository, "_insert_observation_with_cursor", store.insert)


def _baseline(payload=None):
    return {
        "id": 10,
        "watch_id": 3,
        "observation_type": "watch_started",
        "source": "internal",
        "payload": payload
        or {
            "prezzo_mq_base": Decimal("1500.00"),
            "comune": "Alba Adriatica",
            "microzona": "Nord",
        },
    }


def test_microzone_collector_writes_only_when_exact_source_differs(monkeypatch):
    from property_watch import service

    store = MemoryCollectionStore(_baseline(), Decimal("1500.00"))
    _wire_microzone_collector(monkeypatch, store)

    unchanged = service.collect_microzone_market_signal_for_stima(501)

    assert unchanged["status"] == "unchanged"
    assert store.observations == []

    store.zone_value = Decimal("1600.00")
    written = service.collect_microzone_market_signal_for_stima(501)

    assert written["status"] == "written"
    assert written["watch_id"] == 3
    assert written["observation"]["payload"] == {
        "previous": Decimal("1500.00"),
        "current": Decimal("1600.00"),
        "delta": Decimal("100.00"),
        "delta_percent": Decimal("6.666666666666666666666666667"),
        "comune": "Alba Adriatica",
        "microzona": "Nord",
    }
    assert written["observation"]["idempotency_key"] == (
        "property_watch:microzone_price_changed:watch:3:after:10:current:1600:v1"
    )
    assert json.loads(repository._json_dumps(written["observation"]["payload"])) == {
        "previous": 1500.0,
        "current": 1600.0,
        "delta": 100.0,
        "delta_percent": 6.666666666666667,
        "comune": "Alba Adriatica",
        "microzona": "Nord",
    }


def test_microzone_collector_uses_latest_change_and_predecessor_aware_history(monkeypatch):
    from property_watch import service

    store = MemoryCollectionStore(_baseline(), Decimal("1600"))
    _wire_microzone_collector(monkeypatch, store)

    first = service.collect_microzone_market_signal_for_stima(501)
    assert first["status"] == "written"

    assert service.collect_microzone_market_signal_for_stima(501)["status"] == "unchanged"
    store.zone_value = Decimal("1500")
    returned = service.collect_microzone_market_signal_for_stima(501)

    assert returned["status"] == "written"
    assert returned["observation"]["payload"]["previous"] == Decimal("1600")
    assert returned["observation"]["payload"]["delta"] == Decimal("-100")
    assert returned["observation"]["idempotency_key"] == (
        "property_watch:microzone_price_changed:watch:3:after:101:current:1500:v1"
    )
    assert len(store.observations) == 2


def test_microzone_collector_returns_existing_observation_on_idempotency_collision(
    monkeypatch,
):
    from property_watch import service

    store = MemoryCollectionStore(_baseline(), Decimal("1600"))
    _wire_microzone_collector(monkeypatch, store)

    def baseline_only(_cursor, _watch_id, observation_types):
        return store.baseline if observation_types == ("watch_started",) else None

    monkeypatch.setattr(repository, "get_latest_relevant_observation", baseline_only)

    first = service.collect_microzone_market_signal_for_stima(501)
    retry = service.collect_microzone_market_signal_for_stima(501)

    assert first["status"] == retry["status"] == "written"
    assert retry["observation"] == first["observation"]
    assert len(store.observations) == 1


def test_microzone_collector_handles_zero_previous_and_unavailable_inputs(monkeypatch):
    from property_watch import service

    store = MemoryCollectionStore(
        _baseline(
            {
                "prezzo_mq_base": Decimal("0"),
                "comune": "Alba Adriatica",
                "microzona": "Nord",
            }
        ),
        Decimal("100"),
    )
    _wire_microzone_collector(monkeypatch, store)

    written = service.collect_microzone_market_signal_for_stima(501)
    assert written["observation"]["payload"]["delta_percent"] is None

    for payload, source_value, expected in (
        (
            {
                "prezzo_mq_base": Decimal("NaN"),
                "comune": "Alba Adriatica",
                "microzona": "Nord",
            },
            Decimal("1600"),
            "baseline_unavailable",
        ),
        (
            {
                "prezzo_mq_base": Decimal("1500"),
                "comune": None,
                "microzona": "Nord",
            },
            Decimal("1600"),
            "baseline_unavailable",
        ),
        (
            {
                "prezzo_mq_base": Decimal("1500"),
                "comune": "Alba Adriatica",
                "microzona": "Nord",
            },
            None,
            "source_unavailable",
        ),
    ):
        unavailable_store = MemoryCollectionStore(_baseline(payload), source_value)
        _wire_microzone_collector(monkeypatch, unavailable_store)

        result = service.collect_microzone_market_signal_for_stima(501)

        assert result["status"] == expected
        assert unavailable_store.observations == []


def test_supply_count_policy_excludes_all_non_inventory_statuses_and_archived_rows():
    rows = [
        {
            "city": "Alba Adriatica",
            "microzone": "Nord",
            "commercial_status": status,
            "archived_at": None,
        }
        for status in (
            "draft",
            "evaluation",
            "mandate",
            "active",
            "reserved",
            "under_offer",
            "sold",
            "withdrawn",
            "archived",
        )
    ]
    rows.extend(
        [
            {
                "city": "Alba Adriatica",
                "microzone": "Nord",
                "commercial_status": "active",
                "archived_at": "2026-09-01T00:00:00Z",
            },
            {
                "city": "Other City",
                "microzone": "Nord",
                "commercial_status": "active",
                "archived_at": None,
            },
            {
                "city": "Alba Adriatica",
                "microzone": "Other Zone",
                "commercial_status": "active",
                "archived_at": None,
            },
        ]
    )

    class Cursor:
        def execute(self, query, params=None):
            self.query = query
            self.params = params

        def fetchone(self):
            city, microzone, *statuses = self.params
            return {
                "supply_count": sum(
                    row["city"] == city
                    and row["microzone"] == microzone
                    and row["archived_at"] is None
                    and row["commercial_status"] in statuses
                    for row in rows
                )
            }

    cursor = Cursor()

    assert repository.count_internal_supply(cursor, "Alba Adriatica", "Nord") == 4
    assert set(cursor.params[2:]) == {
        "mandate",
        "active",
        "reserved",
        "under_offer",
    }


def test_supply_collector_always_writes_first_snapshot_including_zero(monkeypatch):
    from property_watch import service

    store = MemoryCollectionStore(_baseline(), supply_count=0)
    _wire_supply_collector(monkeypatch, store)

    result = service.collect_internal_supply_signal_for_stima(501)

    assert result["status"] == "written"
    assert result["observation"]["observation_type"] == "internal_supply_snapshot"
    assert result["observation"]["source"] == "internal"
    assert result["observation"]["payload"] == {
        "current_count": 0,
        "comune": "Alba Adriatica",
        "microzona": "Nord",
    }
    assert result["observation"]["idempotency_key"] == (
        "property_watch:internal_supply_snapshot:watch:3:after:10:count:0:v1"
    )
    assert not {
        "property_id",
        "title",
        "address",
        "price",
        "owner",
        "contact",
        "lead",
        "buyer",
    } & result["observation"]["payload"].keys()


def test_supply_collector_appends_only_aggregate_count_changes(monkeypatch):
    from property_watch import service

    store = MemoryCollectionStore(_baseline(), supply_count=4)
    _wire_supply_collector(monkeypatch, store)

    snapshot = service.collect_internal_supply_signal_for_stima(501)
    assert snapshot["status"] == "written"
    assert service.collect_internal_supply_signal_for_stima(501)["status"] == "unchanged"

    store.supply_count = 6
    increased = service.collect_internal_supply_signal_for_stima(501)

    assert increased["status"] == "written"
    assert increased["observation"]["observation_type"] == "internal_supply_changed"
    assert increased["observation"]["payload"] == {
        "previous_count": 4,
        "current_count": 6,
        "delta": 2,
        "comune": "Alba Adriatica",
        "microzona": "Nord",
    }
    assert increased["observation"]["idempotency_key"] == (
        "property_watch:internal_supply_changed:watch:3:after:101:count:6:v1"
    )

    store.supply_count = 4
    returned = service.collect_internal_supply_signal_for_stima(501)
    assert returned["status"] == "written"
    assert returned["observation"]["idempotency_key"] == (
        "property_watch:internal_supply_changed:watch:3:after:102:count:4:v1"
    )
    assert len(store.observations) == 3


def test_supply_collector_returns_existing_snapshot_on_idempotency_collision(
    monkeypatch,
):
    from property_watch import service

    store = MemoryCollectionStore(_baseline(), supply_count=0)
    _wire_supply_collector(monkeypatch, store)

    def baseline_only(_cursor, _watch_id, observation_types):
        return store.baseline if observation_types == ("watch_started",) else None

    monkeypatch.setattr(repository, "get_latest_relevant_observation", baseline_only)

    first = service.collect_internal_supply_signal_for_stima(501)
    retry = service.collect_internal_supply_signal_for_stima(501)

    assert first["status"] == retry["status"] == "written"
    assert retry["observation"] == first["observation"]
    assert len(store.observations) == 1


def test_supply_collector_requires_baseline_locality_but_not_source_rows(monkeypatch):
    from property_watch import service

    store = MemoryCollectionStore(
        _baseline(
            {
                "prezzo_mq_base": Decimal("1500"),
                "comune": None,
                "microzona": "Nord",
            }
        ),
        supply_count=0,
    )
    _wire_supply_collector(monkeypatch, store)

    unavailable = service.collect_internal_supply_signal_for_stima(501)

    assert unavailable["status"] == "baseline_unavailable"
    assert store.observations == []
