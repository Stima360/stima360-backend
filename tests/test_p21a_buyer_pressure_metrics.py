from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import threading

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from admin_security import require_admin
from integration_p2_support import import_project_module
from property_watch import buyer_pressure, repository, service
from property_watch import router as property_watch_router
from property_watch.exceptions import ValidationError, WatchNotFoundError


def baseline(**changes):
    value = {
        "comune": "Alba Adriatica",
        "microzona": "Nord",
        "tipologia": "Appartamento",
        "mq": Decimal("90"),
        "price_exact": Decimal("180000"),
    }
    value.update(changes)
    return value


def buy(
    score=80,
    *,
    request_id=1,
    activity=None,
    budget_target=None,
    budget_max=None,
    budget_min=None,
    hard_fail_count=0,
    compatibility_status="compatible",
):
    return {
        "id": request_id,
        "status": "active",
        "archived_at": None,
        "surface_min": Decimal("50"),
        "budget_target": budget_target,
        "budget_max": budget_max,
        "budget_min": budget_min,
        "locations": [],
        "typologies": [],
        "features": [],
        "last_activity_at": activity,
        "_result": {
            "score_total": score,
            "hard_fail_count": hard_fail_count,
            "compatibility_status": compatibility_status,
            "algorithm_version": "match-0.1",
        },
    }


def test_ephemeral_property_uses_only_approved_baseline_fields():
    assert buyer_pressure.build_ephemeral_property(baseline(extra="not-used")) == {
        "city": "Alba Adriatica",
        "microzone": "Nord",
        "property_type": "Appartamento",
        "surface_sqm": Decimal("90"),
        "asking_price": Decimal("180000"),
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("comune", " "),
        ("microzona", None),
        ("tipologia", ""),
        ("mq", True),
        ("mq", Decimal("NaN")),
        ("price_exact", 0),
    ],
)
def test_ephemeral_property_rejects_unusable_baselines(field, value):
    assert buyer_pressure.build_ephemeral_property(baseline(**{field: value})) is None


def test_canonical_metrics_are_strict_and_stable():
    metrics = buyer_pressure.empty_metrics()
    metrics["average_match_score"] = Decimal("80.000")
    metrics["maximum_match_score"] = 80
    metrics["average_budget"] = Decimal("245000.005")
    canonical = buyer_pressure.canonicalize_metrics(metrics)
    assert canonical["average_budget"] == Decimal("245000.01")
    assert buyer_pressure.metrics_digest(metrics) == buyer_pressure.metrics_digest(
        dict(reversed(list(metrics.items())))
    )
    with pytest.raises(ValueError):
        buyer_pressure.canonicalize_metrics({**metrics, "buyer_ids": [1]})


def test_canonical_metrics_normalize_negative_zero_and_equal_digests():
    negative_zero = buyer_pressure.empty_metrics()
    negative_zero.update(
        {
            "average_match_score": Decimal("-0.00"),
            "maximum_match_score": Decimal("-0.000"),
            "average_budget": Decimal("-0.00"),
        }
    )
    positive_zero = buyer_pressure.empty_metrics()
    positive_zero.update(
        {
            "average_match_score": Decimal("0.00"),
            "maximum_match_score": Decimal("0.00"),
            "average_budget": Decimal("0.00"),
        }
    )
    assert buyer_pressure.canonicalize_metrics(negative_zero) == positive_zero
    assert buyer_pressure.metrics_digest(negative_zero) == buyer_pressure.metrics_digest(
        positive_zero
    )


@pytest.mark.parametrize("compatibility_status", ["", " ", "unknown", "COMPATIBLE"])
def test_invalid_match_compatibility_status_fails_entire_calculation(
    monkeypatch, compatibility_status
):
    monkeypatch.setattr(
        buyer_pressure, "calculate_match", lambda request, _prop: request["_result"]
    )
    with pytest.raises(ValueError):
        buyer_pressure.calculate_buyer_pressure_metrics(
            [
                buy(
                    80,
                    activity=datetime(2026, 9, 3, tzinfo=timezone.utc),
                    compatibility_status=compatibility_status,
                )
            ],
            baseline(),
            datetime(2026, 9, 3, tzinfo=timezone.utc),
        )


@pytest.mark.parametrize(
    ("compatibility_status", "compatible_buyers"),
    [("compatible", 1), ("exception", 1), ("incompatible", 0)],
)
def test_match_compatibility_status_controls_eligible_count(
    monkeypatch, compatibility_status, compatible_buyers
):
    monkeypatch.setattr(
        buyer_pressure, "calculate_match", lambda request, _prop: request["_result"]
    )
    metrics = buyer_pressure.calculate_buyer_pressure_metrics(
        [
            buy(
                80,
                activity=datetime(2026, 9, 3, tzinfo=timezone.utc),
                compatibility_status=compatibility_status,
            )
        ],
        baseline(),
        datetime(2026, 9, 3, tzinfo=timezone.utc),
    )
    assert metrics["compatible_buyers"] == compatible_buyers


def test_thresholds_recency_and_budget_aggregation(monkeypatch):
    now = datetime(2026, 9, 3, tzinfo=timezone.utc)
    monkeypatch.setattr(buyer_pressure, "calculate_match", lambda request, _prop: request["_result"])
    metrics = buyer_pressure.calculate_buyer_pressure_metrics(
        [
            buy(54.99, request_id=1, activity=now),
            buy(55, request_id=2, activity=now - timedelta(days=30), budget_target=Decimal("200000")),
            buy(79.99, request_id=3, activity=now - timedelta(days=30, microseconds=1), budget_max=Decimal("240000")),
            buy(80, request_id=4, activity=now, budget_min=Decimal("280000")),
            buy(100, request_id=5, activity=now, hard_fail_count=1),
        ],
        baseline(),
        now,
    )
    assert metrics == {
        "evaluated_buyers": 5,
        "compatible_buyers": 3,
        "highly_compatible_buyers": 1,
        "recent_compatible_buyers_30d": 2,
        "average_match_score": Decimal("71.66"),
        "maximum_match_score": Decimal("80.00"),
        "average_budget": Decimal("240000.00"),
        "algorithm_version": "match-0.1",
    }


def test_calculation_filters_inactive_archived_and_not_ready_buys(monkeypatch):
    now = datetime(2026, 9, 3, tzinfo=timezone.utc)
    calls = []
    monkeypatch.setattr(
        buyer_pressure,
        "calculate_match",
        lambda request, _prop: calls.append(request["id"]) or request["_result"],
    )
    active = buy(request_id=1, activity=now)
    paused = buy(request_id=2, activity=now)
    paused["status"] = "paused"
    archived = buy(request_id=3, activity=now)
    archived["archived_at"] = now
    without_criteria = buy(request_id=4, activity=now)
    without_criteria["surface_min"] = None
    metrics = buyer_pressure.calculate_buyer_pressure_metrics(
        [active, paused, archived, without_criteria], baseline(), now
    )
    assert calls == [1]
    assert metrics["evaluated_buyers"] == 1

    zero = buyer_pressure.calculate_buyer_pressure_metrics(
        [paused, archived, without_criteria], baseline(), now
    )
    assert zero == buyer_pressure.empty_metrics()


def test_incompatible_and_future_compatible_activity_are_handled(monkeypatch):
    now = datetime(2026, 9, 3, tzinfo=timezone.utc)
    monkeypatch.setattr(
        buyer_pressure, "calculate_match", lambda request, _prop: request["_result"]
    )
    metrics = buyer_pressure.calculate_buyer_pressure_metrics(
        [
            buy(
                100,
                request_id=1,
                activity=now + timedelta(days=1),
                compatibility_status="compatible",
            ),
            buy(
                100,
                request_id=2,
                activity=now,
                compatibility_status="incompatible",
            ),
        ],
        baseline(),
        now,
    )
    assert metrics["compatible_buyers"] == 1
    assert metrics["recent_compatible_buyers_30d"] == 1


def test_invalid_match_result_fails_entire_calculation(monkeypatch):
    monkeypatch.setattr(buyer_pressure, "calculate_match", lambda *_: {"score_total": Decimal("NaN")})
    with pytest.raises((KeyError, ValueError)):
        buyer_pressure.calculate_buyer_pressure_metrics(
            [buy(activity=datetime.now(timezone.utc))], baseline(), datetime.now(timezone.utc)
        )


def test_strict_safe_and_batch_collectors_keep_per_watch_boundary(monkeypatch):
    inputs = {
        "watch_id": 3,
        "baseline_observation_id": 10,
        "baseline_payload": baseline(),
        "collection_time": datetime(2026, 9, 3, tzinfo=timezone.utc),
        "buyers": [],
    }
    monkeypatch.setattr(repository, "get_buyer_pressure_inputs", lambda _id: inputs)
    monkeypatch.setattr(
        repository,
        "store_buyer_pressure_metrics",
        lambda **_kwargs: {"status": "written", "watch_id": 3, "observation": {"id": 11}},
    )
    assert service.collect_buyer_pressure_for_stima(501)["status"] == "written"

    calls = []
    monkeypatch.setattr(repository, "list_active_watch_stima_ids", lambda: [7, 11])
    def safe(stima_id):
        calls.append(stima_id)
        if stima_id == 7:
            raise service.WatchNotFoundError("gone")
        return {"status": "written", "watch_id": 3, "observation": None}
    monkeypatch.setattr(service, "safe_collect_buyer_pressure_for_stima", safe)
    assert service.collect_buyer_pressure_for_active_watches() == {
        "processed": 2, "written": 1, "unchanged": 0, "unavailable": 0,
        "superseded": 0, "failed": 1,
        "outcomes": [
            {"stima_id": 7, "status": "failed", "watch_id": None, "observation": None},
            {"stima_id": 11, "status": "written", "watch_id": 3, "observation": None},
        ],
    }
    assert calls == [7, 11]


class CursorContext:
    def __init__(self, cursor):
        self.cursor = cursor

    def __enter__(self):
        return None, self.cursor

    def __exit__(self, _exc_type, _exc_value, _traceback):
        return False


def test_buyer_pressure_inputs_are_read_only_ordered_and_privacy_minimized(
    monkeypatch,
):
    now = datetime(2026, 9, 3, tzinfo=timezone.utc)

    class Cursor:
        def __init__(self):
            self.executions = []
            self.one_rows = [
                {"collection_time": now},
                {
                    "watch_id": 3,
                    "baseline_observation_id": 10,
                    "baseline_payload": baseline(),
                },
            ]
            self.all_rows = [
                [
                    {
                        "id": 7,
                        "status": "active",
                        "archived_at": None,
                        "created_at": now - timedelta(days=31),
                        "updated_at": now - timedelta(days=2),
                        "last_activity_at": now - timedelta(days=1),
                    }
                ],
                [
                    {
                        "buy_request_id": 7,
                        "microzone": "Nord",
                        "municipality": "Alba Adriatica",
                        "province": "Teramo",
                        "priority": 10,
                        "is_required": True,
                        "is_excluded": False,
                    }
                ],
                [],
                [],
            ]

        def execute(self, query, params=None):
            self.executions.append((query, params))

        def fetchone(self):
            return self.one_rows.pop(0)

        def fetchall(self):
            return self.all_rows.pop(0)

    cursor = Cursor()
    monkeypatch.setattr(
        repository, "property_watch_cursor", lambda **_kwargs: CursorContext(cursor)
    )

    inputs = repository.get_buyer_pressure_inputs(501)

    assert inputs["watch_id"] == 3
    assert inputs["baseline_observation_id"] == 10
    assert inputs["collection_time"] == now
    assert inputs["buyers"][0]["locations"] == [
        {
            "microzone": "Nord",
            "municipality": "Alba Adriatica",
            "province": "Teramo",
            "priority": 10,
            "is_required": True,
            "is_excluded": False,
        }
    ]
    queries = "\n".join(query for query, _ in cursor.executions).lower()
    assert "set transaction isolation level repeatable read, read only" in queries
    assert "b.status = 'active'" in queries
    assert "b.archived_at is null" in queries
    assert "max(i.occurred_at)" in queries
    assert "order by b.id asc" in queries
    assert queries.count("order by buy_request_id asc, id asc") == 3
    assert not any(
        token in queries
        for token in (
            "contact_id",
            "lead_id",
            "display_name",
            "email",
            "phone",
            "finance_notes",
            "b.notes",
            "b.metadata",
            "i.notes",
            "i.created_by",
            "insert ",
            "update ",
            "delete ",
        )
    )


@pytest.mark.parametrize(
    ("context", "parent_rows", "expected"),
    [
        (None, [], None),
        (
            {"watch_id": 3, "baseline_observation_id": None, "baseline_payload": None},
            [],
            {"watch_id": 3, "baseline_observation_id": None, "baseline_payload": None},
        ),
    ],
)
def test_buyer_pressure_inputs_handle_missing_watch_baseline_and_zero_buys(
    monkeypatch, context, parent_rows, expected
):
    now = datetime(2026, 9, 3, tzinfo=timezone.utc)

    class Cursor:
        def __init__(self):
            self.executions = []
            self.one_rows = [{"collection_time": now}, context]
            self.all_rows = [parent_rows]

        def execute(self, query, params=None):
            self.executions.append((query, params))

        def fetchone(self):
            return self.one_rows.pop(0)

        def fetchall(self):
            return self.all_rows.pop(0)

    cursor = Cursor()
    monkeypatch.setattr(
        repository, "property_watch_cursor", lambda **_kwargs: CursorContext(cursor)
    )
    result = repository.get_buyer_pressure_inputs(501)
    if expected is None:
        assert result is None
    else:
        assert result == {**expected, "collection_time": now, "buyers": []}
    assert all(
        "buy_request_locations" not in query
        and "buy_request_typologies" not in query
        and "buy_request_features" not in query
        for query, _ in cursor.executions
    )


def _wire_store(monkeypatch, *, baseline_id=10, latest=None):
    writes = []

    class Cursor:
        def execute(self, _query, _params=None):
            pass

        def fetchone(self):
            return {"id": 3}

    monkeypatch.setattr(
        repository, "property_watch_cursor", lambda **_kwargs: CursorContext(Cursor())
    )
    monkeypatch.setattr(
        repository,
        "_get_earliest_watch_started_observation",
        lambda _cur, _watch_id: {"id": baseline_id},
    )
    state = {"latest": latest}
    monkeypatch.setattr(
        repository,
        "get_latest_relevant_observation",
        lambda _cur, _watch_id, _types: state["latest"],
    )

    def insert(_cur, watch_id, observation_type, source, payload, key, *, observed_at):
        observation = {
            "id": 11 + len(writes),
            "watch_id": watch_id,
            "observation_type": observation_type,
            "source": source,
            "payload": payload,
            "idempotency_key": key,
            "observed_at": observed_at,
        }
        writes.append(observation)
        state["latest"] = observation
        return observation

    monkeypatch.setattr(repository, "_insert_observation_with_cursor", insert)
    return writes, state


def test_store_buyer_pressure_is_append_only_and_canonical(monkeypatch):
    now = datetime(2026, 9, 3, tzinfo=timezone.utc)
    writes, _state = _wire_store(monkeypatch)
    zero = {
        **buyer_pressure.empty_metrics(),
        "compatible_buyers": 1,
        "average_match_score": Decimal("55.00"),
        "maximum_match_score": Decimal("55.00"),
        "average_budget": Decimal("0.00"),
    }

    first = repository.store_buyer_pressure_metrics(
        stima_id=501,
        watch_id=3,
        baseline_observation_id=10,
        metrics=zero,
        observed_at=now,
    )
    assert first["status"] == "written"
    assert first["observation"]["observation_type"] == "buyer_pressure_snapshot"
    assert first["observation"]["source"] == "internal"
    assert set(first["observation"]["payload"]) == set(buyer_pressure.METRIC_KEYS)
    assert ":after:10:metrics:" in first["observation"]["idempotency_key"]

    equivalent = {**zero, "average_budget": Decimal("-0.00")}
    assert repository.store_buyer_pressure_metrics(
        stima_id=501,
        watch_id=3,
        baseline_observation_id=10,
        metrics=equivalent,
        observed_at=now,
    ) == {"status": "unchanged", "watch_id": 3, "observation": None}
    changed = {**zero, "evaluated_buyers": 1}
    second = repository.store_buyer_pressure_metrics(
        stima_id=501,
        watch_id=3,
        baseline_observation_id=10,
        metrics=changed,
        observed_at=now,
    )
    assert second["observation"]["observation_type"] == "buyer_pressure_changed"
    assert ":after:11:metrics:" in second["observation"]["idempotency_key"]
    assert len(writes) == 2


def test_store_buyer_pressure_rechecks_baseline_and_supersedes_stale_work(
    monkeypatch,
):
    now = datetime(2026, 9, 3, tzinfo=timezone.utc)
    writes, _state = _wire_store(monkeypatch, baseline_id=12)
    assert repository.store_buyer_pressure_metrics(
        stima_id=501,
        watch_id=3,
        baseline_observation_id=10,
        metrics=buyer_pressure.empty_metrics(),
        observed_at=now,
    ) == {"status": "baseline_unavailable", "watch_id": 3, "observation": None}
    assert writes == []

    latest = {
        "id": 11,
        "payload": buyer_pressure.empty_metrics(),
        "observed_at": now + timedelta(microseconds=1),
    }
    writes, _state = _wire_store(monkeypatch, latest=latest)
    assert repository.store_buyer_pressure_metrics(
        stima_id=501,
        watch_id=3,
        baseline_observation_id=10,
        metrics=buyer_pressure.empty_metrics(),
        observed_at=now,
    ) == {"status": "superseded", "watch_id": 3, "observation": None}
    assert writes == []


def test_store_buyer_pressure_does_not_insert_when_locked_watch_disappears(
    monkeypatch,
):
    writes = []

    class Cursor:
        def execute(self, _query, _params=None):
            pass

        def fetchone(self):
            return None

    monkeypatch.setattr(
        repository, "property_watch_cursor", lambda **_kwargs: CursorContext(Cursor())
    )
    monkeypatch.setattr(
        repository,
        "_insert_observation_with_cursor",
        lambda *_args, **_kwargs: writes.append("unexpected"),
    )
    assert repository.store_buyer_pressure_metrics(
        stima_id=501,
        watch_id=3,
        baseline_observation_id=10,
        metrics=buyer_pressure.empty_metrics(),
        observed_at=datetime(2026, 9, 3, tzinfo=timezone.utc),
    ) is None
    assert writes == []


def test_strict_collection_never_persists_unavailable_or_partial_results(
    monkeypatch, caplog
):
    inputs = {
        "watch_id": 3,
        "baseline_observation_id": 10,
        "baseline_payload": baseline(microzona=None),
        "collection_time": datetime(2026, 9, 3, tzinfo=timezone.utc),
        "buyers": [],
    }
    monkeypatch.setattr(repository, "get_buyer_pressure_inputs", lambda _id: inputs)
    monkeypatch.setattr(
        repository,
        "store_buyer_pressure_metrics",
        lambda **_kwargs: pytest.fail("unavailable baseline must not persist"),
    )
    assert service.collect_buyer_pressure_for_stima(501) == {
        "status": "baseline_unavailable",
        "watch_id": 3,
        "observation": None,
    }
    monkeypatch.setattr(
        buyer_pressure,
        "calculate_buyer_pressure_metrics",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("private details")),
    )
    assert service.safe_collect_buyer_pressure_for_stima(501) == {
        "status": "failed",
        "watch_id": None,
        "observation": None,
    }
    record = caplog.records[-1]
    assert record.args == (501, "RuntimeError")
    assert "private details" not in record.getMessage()


def test_buyer_pressure_routes_are_protected_body_free_and_serialize(monkeypatch):
    paths = import_project_module("main").app.openapi()["paths"]
    for path in (
        "/api/property-watch/stime/{stima_id}/buyer-pressure/refresh",
        "/api/property-watch/buyer-pressure/refresh-active",
    ):
        assert set(paths[path]) == {"post"}
        assert paths[path]["post"].get("security")
        assert "requestBody" not in paths[path]["post"]

    observed_at = datetime(2026, 9, 3, tzinfo=timezone.utc)
    observation = {
        "id": 11,
        "watch_id": 3,
        "observation_type": "buyer_pressure_snapshot",
        "source": "internal",
        "payload": buyer_pressure.empty_metrics(),
        "idempotency_key": "property_watch:buyer_pressure_snapshot:watch:3:v1",
        "observed_at": observed_at,
        "created_at": observed_at,
    }
    monkeypatch.setattr(
        service,
        "safe_collect_buyer_pressure_for_stima",
        lambda stima_id: {
            "status": "written",
            "watch_id": 3,
            "observation": observation,
        },
    )
    app = FastAPI()
    app.include_router(
        property_watch_router.router, dependencies=[Depends(require_admin)]
    )
    monkeypatch.setenv("ADMIN_USER", "test-admin")
    monkeypatch.setenv("ADMIN_PASS", "test-password")
    response = TestClient(app).post(
        "/api/property-watch/stime/501/buyer-pressure/refresh",
        auth=("test-admin", "test-password"),
    )
    assert response.status_code == 200
    assert response.json()["observation"]["watch_id"] == 3


@pytest.mark.parametrize(
    ("error", "status"),
    [(WatchNotFoundError("missing"), 404), (ValidationError("invalid"), 400)],
)
def test_buyer_pressure_route_maps_expected_errors(monkeypatch, error, status):
    monkeypatch.setattr(
        service,
        "safe_collect_buyer_pressure_for_stima",
        lambda _id: (_ for _ in ()).throw(error),
    )
    with pytest.raises(Exception) as raised:
        property_watch_router.refresh_buyer_pressure(501)
    assert raised.value.status_code == status


def test_current_state_derives_metrics_without_collection_or_writes(monkeypatch):
    observed_at = datetime(2026, 9, 3, tzinfo=timezone.utc)
    pressure = {
        "id": 11,
        "watch_id": 3,
        "observation_type": "buyer_pressure_snapshot",
        "source": "internal",
        "payload": buyer_pressure.empty_metrics(),
        "idempotency_key": "property_watch:buyer_pressure_snapshot:watch:3:v1",
        "observed_at": observed_at,
        "created_at": observed_at,
    }
    monkeypatch.setattr(
        repository,
        "get_watch_for_stima",
        lambda _id: {"id": 3, "stima_id": 501, "status": "active"},
    )
    monkeypatch.setattr(repository, "list_observations", lambda _id: [pressure])
    for name in (
        "collect_buyer_pressure_for_stima",
        "safe_collect_buyer_pressure_for_stima",
        "collect_internal_signals_for_stima",
    ):
        monkeypatch.setattr(service, name, lambda *_args: pytest.fail("GET must not collect"))
    monkeypatch.setattr(
        repository,
        "store_buyer_pressure_metrics",
        lambda **_kwargs: pytest.fail("GET must not write"),
    )
    state = service.get_current_watch_state(501)
    assert state["buyer_pressure_metrics"] == {
        **buyer_pressure.empty_metrics(),
        "latest_observation": pressure,
        "observed_at": observed_at,
        "observation_count": 1,
    }


def test_current_state_returns_null_before_first_buyer_pressure_snapshot(monkeypatch):
    monkeypatch.setattr(
        repository,
        "get_watch_for_stima",
        lambda _id: {"id": 3, "stima_id": 501, "status": "active"},
    )
    monkeypatch.setattr(repository, "list_observations", lambda _id: [])
    assert service.get_current_watch_state(501)["buyer_pressure_metrics"] is None


def _keys_recursively(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key.lower()
            yield from _keys_recursively(item)
    elif isinstance(value, list):
        for item in value:
            yield from _keys_recursively(item)


def _values_recursively(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from _values_recursively(item)
    elif isinstance(value, list):
        for item in value:
            yield from _values_recursively(item)
    else:
        yield str(value)


def test_metrics_and_success_outcomes_expose_no_individual_buy_data(monkeypatch):
    now = datetime(2026, 9, 3, tzinfo=timezone.utc)
    monkeypatch.setattr(
        buyer_pressure, "calculate_match", lambda request, _prop: request["_result"]
    )
    metrics = buyer_pressure.calculate_buyer_pressure_metrics(
        [buy(activity=now, budget_target=Decimal("200000"))], baseline(), now
    )
    forbidden = {
        "buy_request_id",
        "buyer_id",
        "buy_ids",
        "contact_id",
        "lead_id",
        "display_name",
        "name",
        "email",
        "phone",
        "notes",
        "criteria",
        "locations",
        "typologies",
        "features",
        "individual_scores",
    }
    assert not forbidden & set(_keys_recursively(metrics))


def test_pure_calculation_calls_no_match_persistence(monkeypatch):
    from match import repository as match_repository

    for name in (
        "calculate_pair",
        "calculate_for_buy",
        "calculate_for_property",
        "refresh_match",
    ):
        if hasattr(match_repository, name):
            monkeypatch.setattr(
                match_repository,
                name,
                lambda *_args, **_kwargs: pytest.fail("MATCH persistence is forbidden"),
            )
    metrics = buyer_pressure.calculate_buyer_pressure_metrics(
        [
            buy(
                activity=datetime(2026, 9, 3, tzinfo=timezone.utc),
                budget_target=Decimal("200000"),
            )
        ],
        baseline(),
        datetime(2026, 9, 3, tzinfo=timezone.utc),
    )
    assert set(metrics) == set(buyer_pressure.METRIC_KEYS)


def test_concurrent_collectors_serialize_on_for_update_and_write_once(monkeypatch):
    now = datetime(2026, 9, 3, tzinfo=timezone.utc)
    lock = threading.Lock()
    first_locked = threading.Event()
    second_attempting_lock = threading.Event()
    allow_first_insert = threading.Event()
    executions = []
    observations = []
    latest = {"value": None}

    class LockingCursor:
        def __init__(self):
            self.locked = False
            self.last_query = ""

        def execute(self, query, _params=None):
            self.last_query = query
            executions.append(query)
            if "FOR UPDATE" in query:
                if first_locked.is_set():
                    second_attempting_lock.set()
                acquired = lock.acquire(timeout=2)
                if not acquired:
                    raise AssertionError("timed out acquiring test lock")
                self.locked = True
                if not first_locked.is_set():
                    first_locked.set()

        def fetchone(self):
            if "FROM property_watches" in self.last_query:
                return {"id": 3}
            return None

    class LockingContext:
        def __init__(self, cursor):
            self.cursor = cursor

        def __enter__(self):
            return None, self.cursor

        def __exit__(self, _exc_type, _exc_value, _traceback):
            if self.cursor.locked:
                lock.release()
            return False

    monkeypatch.setattr(
        repository,
        "property_watch_cursor",
        lambda **_kwargs: LockingContext(LockingCursor()),
    )
    monkeypatch.setattr(
        repository,
        "_get_earliest_watch_started_observation",
        lambda _cur, _watch_id: {"id": 10},
    )
    monkeypatch.setattr(
        repository,
        "get_latest_relevant_observation",
        lambda _cur, _watch_id, _types: latest["value"],
    )

    def insert(_cur, watch_id, observation_type, source, payload, key, *, observed_at):
        if not observations:
            allow_first_insert.wait(timeout=2)
        observation = {
            "id": 11,
            "watch_id": watch_id,
            "observation_type": observation_type,
            "source": source,
            "payload": payload,
            "idempotency_key": key,
            "observed_at": observed_at,
        }
        observations.append(observation)
        latest["value"] = observation
        return observation

    monkeypatch.setattr(repository, "_insert_observation_with_cursor", insert)
    results = []
    errors = []

    def collect():
        try:
            results.append(
                repository.store_buyer_pressure_metrics(
                    stima_id=501,
                    watch_id=3,
                    baseline_observation_id=10,
                    metrics=buyer_pressure.empty_metrics(),
                    observed_at=now,
                )
            )
        except Exception as exc:  # noqa: BLE001 - test captures worker faults
            errors.append(exc)

    first = threading.Thread(target=collect, daemon=True)
    second = threading.Thread(target=collect, daemon=True)
    try:
        first.start()
        assert first_locked.wait(timeout=2)
        second.start()
        assert second_attempting_lock.wait(timeout=2)
    finally:
        allow_first_insert.set()
        first.join(timeout=2)
        second.join(timeout=2)

    assert not first.is_alive() and not second.is_alive()
    assert errors == []
    assert sorted(result["status"] for result in results) == ["unchanged", "written"]
    assert len(observations) == 1
    assert sum("FOR UPDATE" in query for query in executions) == 2


def test_observation_insert_recovers_existing_row_after_on_conflict():
    conflicting_key = "property_watch:buyer_pressure_snapshot:watch:3:conflict"
    existing = {"id": 44, "watch_id": 3, "idempotency_key": conflicting_key}

    class Cursor:
        def __init__(self):
            self.executions = []

        def execute(self, query, params=None):
            self.executions.append((query, params))

        def fetchone(self):
            return None if len(self.executions) == 1 else existing

    cursor = Cursor()
    result = repository._insert_observation_with_cursor(
        cursor,
        3,
        "buyer_pressure_snapshot",
        "internal",
        buyer_pressure.empty_metrics(),
        conflicting_key,
        observed_at=datetime(2026, 9, 3, tzinfo=timezone.utc),
    )
    assert result == existing
    assert "ON CONFLICT (idempotency_key) DO NOTHING" in cursor.executions[0][0]
    fallback_query, fallback_params = cursor.executions[1]
    assert " ".join(fallback_query.split()) == (
        "SELECT * FROM property_watch_observations WHERE idempotency_key = %s"
    )
    assert fallback_params == (conflicting_key,)


def test_collector_sql_writes_only_property_watch_observations(monkeypatch):
    now = datetime(2026, 9, 3, tzinfo=timezone.utc)
    cursors = []

    class Cursor:
        def __init__(self, rows):
            self.rows = iter(rows)
            self.executions = []

        def execute(self, query, params=None):
            self.executions.append((query, params))

        def fetchone(self):
            return next(self.rows)

        def fetchall(self):
            if self is read_cursor:
                return [
                    {
                        "id": 777,
                        "status": "active",
                        "archived_at": None,
                        "budget_min": None,
                        "budget_target": Decimal("200000"),
                        "budget_max": None,
                        "budget_flexibility_percent": Decimal("0"),
                        "surface_min": Decimal("50"),
                        "surface_target": None,
                        "surface_max": None,
                        "rooms_min": None,
                        "bedrooms_min": None,
                        "bathrooms_min": None,
                        "created_at": now,
                        "updated_at": now,
                        "last_activity_at": now,
                    }
                ] if len(self.executions) == 4 else []
            return []

    read_cursor = Cursor(
        [
            {"collection_time": now},
            {
                "watch_id": 3,
                "baseline_observation_id": 10,
                "baseline_payload": baseline(),
            },
        ]
    )
    observation = {
        "id": 11,
        "watch_id": 3,
        "observation_type": "buyer_pressure_snapshot",
        "source": "internal",
        "payload": buyer_pressure.empty_metrics(),
        "idempotency_key": "property_watch:buyer_pressure_snapshot:watch:3:v1",
        "observed_at": now,
    }
    write_cursor = Cursor([{"id": 3}, {"id": 10}, None, observation])
    cursors.extend([read_cursor, write_cursor])
    monkeypatch.setattr(
        repository,
        "property_watch_cursor",
        lambda **_kwargs: CursorContext(cursors.pop(0)),
    )
    pure_calls = []
    monkeypatch.setattr(
        buyer_pressure,
        "calculate_match",
        lambda request, _prop: pure_calls.append(request["id"]) or {
            "score_total": Decimal("80"),
            "hard_fail_count": 0,
            "compatibility_status": "compatible",
            "algorithm_version": "match-0.1",
        },
    )
    from match import repository as match_repository
    for name in (
        "calculate_pair",
        "calculate_for_buy",
        "calculate_for_property",
        "refresh_match",
    ):
        if hasattr(match_repository, name):
            monkeypatch.setattr(
                match_repository,
                name,
                lambda *_args, **_kwargs: pytest.fail("MATCH persistence is forbidden"),
            )

    result = service.collect_buyer_pressure_for_stima(501)

    assert result == {"status": "written", "watch_id": 3, "observation": observation}
    assert pure_calls == [777]
    queries = [query.strip().lower() for cursor in (read_cursor, write_cursor) for query, _ in cursor.executions]
    assert any("for update" in query and "property_watches" in query for query in queries)
    writes = [
        query
        for query in queries
        if query.startswith(("insert", "update", "delete", "merge"))
    ]
    assert len(writes) == 1
    assert "insert into property_watch_observations" in writes[0]
    forbidden = (
        "matches",
        "match_runs",
        "match_requirement_results",
        "buy_requests",
        "buy_request_interactions",
        "properties",
        "contacts",
        "leads",
        "stime",
    )
    assert not any(
        query.startswith(("insert", "update", "delete", "merge"))
        and any(name in query for name in forbidden)
        for query in queries
    )


def test_aggregate_outputs_and_logs_expose_no_personal_buy_data(monkeypatch, caplog):
    now = datetime(2026, 9, 3, tzinfo=timezone.utc)
    inputs = {
        "watch_id": 3,
        "baseline_observation_id": 10,
        "baseline_payload": baseline(),
        "collection_time": now,
        "buyers": [
            {
                **buy(
                    request_id=777,
                    activity=now,
                    budget_target=Decimal("200000"),
                ),
                "contact_id": 888,
                "display_name": "Private Buyer",
                "email": "private@example.test",
                "phone": "123456",
                "notes": "private note",
            }
        ],
    }
    observations = []
    monkeypatch.setattr(repository, "get_buyer_pressure_inputs", lambda _id: inputs)
    monkeypatch.setattr(repository, "list_active_watch_stima_ids", lambda: [501])

    def store(**kwargs):
        observation = {
            "id": 11 + len(observations),
            "watch_id": kwargs["watch_id"],
            "observation_type": "buyer_pressure_snapshot",
            "source": "internal",
            "payload": kwargs["metrics"],
            "idempotency_key": "property_watch:buyer_pressure_snapshot:watch:3:v1",
            "observed_at": kwargs["observed_at"],
            "created_at": now,
        }
        observations.append(observation)
        return {"status": "written", "watch_id": 3, "observation": observation}

    monkeypatch.setattr(repository, "store_buyer_pressure_metrics", store)
    metrics = buyer_pressure.calculate_buyer_pressure_metrics(
        inputs["buyers"], inputs["baseline_payload"], now
    )
    single = service.collect_buyer_pressure_for_stima(501)
    batch = service.collect_buyer_pressure_for_active_watches()
    monkeypatch.setattr(
        repository,
        "get_watch_for_stima",
        lambda _id: {"id": 3, "stima_id": 501, "status": "active"},
    )
    monkeypatch.setattr(repository, "list_observations", lambda _id: observations)
    state = service.get_current_watch_state(501)
    monkeypatch.setattr(
        service,
        "collect_buyer_pressure_for_stima",
        lambda _id: (_ for _ in ()).throw(RuntimeError("email=private@example.test")),
    )
    failed = service.safe_collect_buyer_pressure_for_stima(501)
    forbidden = {
        "buy_request_id", "buyer_id", "buy_ids", "contact_id", "lead_id",
        "display_name", "name", "email", "phone", "notes", "criteria",
        "locations", "typologies", "features", "individual_scores",
    }
    sentinel_values = {
        "777", "888", "Private Buyer", "private@example.test", "123456", "private note"
    }
    for value in (metrics, *observations, single, batch, state, failed):
        assert not forbidden & set(_keys_recursively(value))
        assert not sentinel_values & set(_values_recursively(value))
    assert "private@example.test" not in caplog.records[-1].getMessage()
    assert caplog.records[-1].args == (501, "RuntimeError")


def test_get_current_state_never_calls_any_collector_or_write_boundary(monkeypatch):
    observed_at = datetime(2026, 9, 3, tzinfo=timezone.utc)
    pressure = {
        "id": 11,
        "watch_id": 3,
        "observation_type": "buyer_pressure_snapshot",
        "source": "internal",
        "payload": buyer_pressure.empty_metrics(),
        "idempotency_key": "property_watch:buyer_pressure_snapshot:watch:3:v1",
        "observed_at": observed_at,
        "created_at": observed_at,
    }
    monkeypatch.setattr(
        repository,
        "get_watch_for_stima",
        lambda _id: {"id": 3, "stima_id": 501, "status": "active"},
    )
    monkeypatch.setattr(repository, "list_observations", lambda _id: [pressure])
    for module, names in (
        (
            service,
            (
                "collect_buyer_pressure_for_stima",
                "safe_collect_buyer_pressure_for_stima",
                "collect_buyer_pressure_for_active_watches",
                "collect_internal_signals_for_stima",
                "collect_microzone_market_signal_for_stima",
                "collect_internal_supply_signal_for_stima",
            ),
        ),
        (
            repository,
            (
                "get_buyer_pressure_inputs",
                "store_buyer_pressure_metrics",
                "insert_observation",
                "_insert_observation_with_cursor",
                "collect_microzone_price_change",
                "collect_internal_supply_change",
            ),
        ),
    ):
        for name in names:
            monkeypatch.setattr(
                module,
                name,
                lambda *_args, **_kwargs: pytest.fail(f"GET invoked {name}"),
            )
    monkeypatch.setattr(
        buyer_pressure,
        "calculate_buyer_pressure_metrics",
        lambda *_args: pytest.fail("GET calculated buyer pressure"),
    )
    monkeypatch.setattr(
        buyer_pressure,
        "calculate_match",
        lambda *_args: pytest.fail("GET invoked MATCH"),
    )

    assert service.get_current_watch_state(501)["buyer_pressure_metrics"] == {
        **buyer_pressure.empty_metrics(),
        "latest_observation": pressure,
        "observed_at": observed_at,
        "observation_count": 1,
    }
