from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from contextlib import contextmanager
import copy
import threading

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parent.parent
MIGRATION = ROOT / "migrations" / "023_invisible_sale.sql"
DOWN_MIGRATION = ROOT / "migrations" / "023_invisible_sale_down.sql"


def test_p22_migration_has_required_constraints():
    sql = MIGRATION.read_text()
    assert "UNIQUE (watch_id)" in sql
    assert "UNIQUE (opportunity_id, buy_request_id)" in sql
    assert "UNIQUE (idempotency_key)" in sql
    assert "CHECK (revision >= 0)" in sql
    assert "CHECK (decision_version >= 0)" in sql


def test_p22_down_drops_only_p22_tables_in_reverse_order():
    sql = DOWN_MIGRATION.read_text()
    assert sql.index("invisible_sale_events") < sql.index(
        "invisible_sale_candidates"
    ) < sql.index("invisible_sale_opportunities")


def test_baseline_builds_exact_ephemeral_property():
    from property_watch.invisible_sale import build_ephemeral_property

    assert build_ephemeral_property(
        {
            "comune": " Milano ",
            "microzona": " Centro ",
            "tipologia": " Appartamento ",
            "mq": "80",
            "price_exact": "320000",
        }
    ) == {
        "city": "Milano",
        "microzone": "Centro",
        "property_type": "Appartamento",
        "surface_sqm": Decimal("80"),
        "asking_price": Decimal("320000"),
    }


@pytest.mark.parametrize("value", [None, "", " ", 0, -1, "NaN", "Infinity"])
def test_baseline_rejects_invalid_required_values(value):
    from property_watch.invisible_sale import build_ephemeral_property

    baseline = {
        "comune": "Milano",
        "microzona": "Centro",
        "tipologia": "Appartamento",
        "mq": 80,
        "price_exact": 320000,
    }
    baseline["mq"] = value
    assert build_ephemeral_property(baseline) is None


def _buy():
    return {
        "id": 7,
        "status": "active",
        "archived_at": None,
        "budget_target": "300000",
        "last_activity_at": datetime(2026, 9, 4, tzinfo=timezone.utc),
    }


def _engine(score, hard_fails=0, compatibility="compatible"):
    return {
        "score_total": score,
        "hard_fail_count": hard_fails,
        "compatibility_status": compatibility,
        "algorithm_version": "match-0.1",
        "criteria": [
            {
                "criterion_group": "location",
                "is_blocking": False,
                "result": "matched",
                "score": "90",
            }
        ],
    }


@pytest.mark.parametrize(
    ("score", "hard_fails", "compatibility", "included"),
    [
        ("79.99", 0, "compatible", False),
        ("80.00", 0, "compatible", True),
        ("99.00", 1, "incompatible", False),
        ("87.50", 0, "exception", True),
    ],
)
def test_candidate_threshold(score, hard_fails, compatibility, included, monkeypatch):
    from property_watch import invisible_sale

    monkeypatch.setattr(
        invisible_sale.match_engine, "calculate", lambda *_: _engine(score, hard_fails, compatibility)
    )
    result = invisible_sale.calculate_candidates(
        [_buy()],
        {"city": "Milano", "microzone": "Centro", "property_type": "Appartamento",
         "surface_sqm": Decimal("80"), "asking_price": Decimal("320000")},
    )
    assert bool(result) is included


def test_candidate_is_minimized_ordered_and_canonical(monkeypatch):
    from property_watch import invisible_sale

    first = _buy()
    first["id"] = 8
    first["last_activity_at"] = datetime(2026, 9, 3, tzinfo=timezone.utc)
    second = _buy()
    second["id"] = 3
    monkeypatch.setattr(invisible_sale.match_engine, "calculate", lambda buy, _: _engine("90" if buy["id"] == 3 else "80"))
    candidates = invisible_sale.calculate_candidates([first, second], {"city": "M", "microzone": "C", "property_type": "A", "surface_sqm": Decimal(1), "asking_price": Decimal(1)})
    assert [item["buy_request_id"] for item in candidates] == [3, 8]
    assert set(candidates[0]) == {
        "buy_request_id", "score_total", "compatibility_status", "reason_codes",
        "last_activity_at", "budget_reference", "match_algorithm_version", "candidate_digest",
    }
    assert invisible_sale.candidate_set_digest(candidates) == invisible_sale.candidate_set_digest(candidates)


def test_persistence_has_revisioned_stale_audit_and_lock_order():
    source = (ROOT / "property_watch/invisible_sale_repository.py").read_text()
    assert "invisible_sale:stale:candidate:" in source
    decisions = source[source.index("def set_candidate_review_status"):]
    assert decisions.index("FROM property_watches WHERE") < decisions.index(
        "FROM invisible_sale_opportunities WHERE"
    ) < decisions.index("FROM invisible_sale_candidates")


def test_snapshot_uses_maximum_activity_and_close_locks_watch_first():
    source = (ROOT / "property_watch/invisible_sale_repository.py").read_text()
    assert "GREATEST(b.created_at, b.updated_at" in source
    close = source[source.index("def close_invisible_sale_for_stima"):]
    assert close.index("FROM property_watches WHERE") < close.index(
        "FROM invisible_sale_opportunities WHERE"
    )


def test_first_refresh_emits_candidate_discovery_audit_keys():
    source = (ROOT / "property_watch/invisible_sale_repository.py").read_text()
    assert "invisible_sale:discovered:candidate:" in source


class _FakeInvisibleSaleDatabase:
    """A stateful transaction double for repository behavioral contracts."""

    def __init__(self):
        self.lock = threading.RLock()
        self.watch_id = 11
        self.opportunity = None
        self.candidates = {}
        self.events = []
        self.executed = []
        self.effective_writes = []
        self.fail_on = None
        self.close_update_entered = None
        self.release_close_update = None

    @contextmanager
    def cursor(self, *, commit=False):
        with self.lock:
            snapshot = copy.deepcopy(
                (self.opportunity, self.candidates, self.events, self.effective_writes)
            )
            cursor = _FakeInvisibleSaleCursor(self)
            try:
                yield _FakeInvisibleSaleConnection(self), cursor
                if commit:
                    cursor.connection.commit()
            except Exception:
                (
                    self.opportunity,
                    self.candidates,
                    self.events,
                    self.effective_writes,
                ) = snapshot
                cursor.connection.rollback()
                raise


class _FakeInvisibleSaleConnection:
    def __init__(self, database):
        self.database = database
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class _FakeInvisibleSaleCursor:
    def __init__(self, database):
        self.database = database
        self.connection = _FakeInvisibleSaleConnection(database)
        self.result = []

    def execute(self, sql, params=None):
        compact = " ".join(sql.split())
        lowered = compact.lower()
        self.database.executed.append((compact, params))
        if self.database.fail_on and self.database.fail_on in compact:
            raise RuntimeError("forced SQL failure")

        if lowered.startswith("select id from property_watches"):
            self.result = [{"id": self.database.watch_id}]
        elif lowered.startswith("insert into invisible_sale_opportunities"):
            if self.database.opportunity is None:
                watch_id, digest, algorithm_version, evaluated_at = params
                self.database.opportunity = {
                    "id": 31,
                    "watch_id": watch_id,
                    "status": "empty",
                    "candidate_digest": digest,
                    "current_candidate_count": 0,
                    "algorithm_version": algorithm_version,
                    "revision": 0,
                    "last_evaluated_at": evaluated_at,
                }
                self.database.effective_writes.append("create_opportunity")
            self.result = []
        elif lowered.startswith("select * from invisible_sale_opportunities"):
            self.result = [] if self.database.opportunity is None else [copy.deepcopy(self.database.opportunity)]
        elif lowered.startswith("select * from invisible_sale_candidates"):
            if "where opportunity_id=%s and buy_request_id=%s" in lowered:
                candidate = self.database.candidates.get(params[1])
                self.result = [] if candidate is None else [copy.deepcopy(candidate)]
            else:
                self.result = [
                    copy.deepcopy(row)
                    for _, row in sorted(self.database.candidates.items())
                ]
        elif lowered.startswith("update invisible_sale_candidates set status = 'stale'"):
            row = next(row for row in self.database.candidates.values() if row["id"] == params[1])
            if row["status"] != "stale":
                row["status"] = "stale"
                self.database.effective_writes.append("stale")
            self.result = []
        elif lowered.startswith("insert into invisible_sale_candidates"):
            buy_id = params[1]
            prior = self.database.candidates.get(buy_id)
            values = {
                "opportunity_id": params[0],
                "buy_request_id": buy_id,
                "score_total": params[2],
                "compatibility_status": params[3],
                "reason_codes": params[4],
                "last_activity_at": params[5],
                "budget_reference": params[6],
                "match_algorithm_version": params[7],
                "candidate_digest": params[8],
                "status": params[9],
            }
            if prior is None:
                values.update(id=100 + buy_id, decision_version=0)
                self.database.candidates[buy_id] = values
                self.database.effective_writes.append("create_candidate")
            elif any(prior[key] != value for key, value in values.items()):
                prior.update(values)
                self.database.effective_writes.append("update_candidate")
            self.result = []
        elif lowered.startswith("select id from invisible_sale_candidates"):
            self.result = [{"id": self.database.candidates[params[1]]["id"]}]
        elif lowered.startswith("update invisible_sale_opportunities set status=%s"):
            status, digest, count, version, revision, evaluated_at, _, _ = params
            self.database.opportunity.update(
                status=status,
                candidate_digest=digest,
                current_candidate_count=count,
                algorithm_version=version,
                revision=revision,
                last_evaluated_at=evaluated_at,
            )
            self.database.effective_writes.append("refresh_opportunity")
            self.result = []
        elif lowered.startswith("update invisible_sale_opportunities set status='closed'"):
            if self.database.close_update_entered is not None:
                self.database.close_update_entered.set()
                assert self.database.release_close_update.wait(timeout=1)
            self.database.opportunity["status"] = "closed"
            self.database.effective_writes.append("close_opportunity")
            self.result = []
        elif lowered.startswith("update invisible_sale_candidates set status=%s"):
            status, version, candidate_id = params
            row = next(row for row in self.database.candidates.values() if row["id"] == candidate_id)
            row.update(status=status, decision_version=version)
            self.database.effective_writes.append("review_candidate")
            self.result = []
        elif lowered.startswith("insert into invisible_sale_events"):
            key = params[-2]
            if not any(event["idempotency_key"] == key for event in self.database.events):
                self.database.events.append({"idempotency_key": key, "event_type": params[2] if "candidate_id" in lowered else params[1]})
                self.database.effective_writes.append("event")
            self.result = []
        else:
            raise AssertionError(f"unexpected SQL: {compact}")

    def fetchone(self):
        return self.result[0] if self.result else None

    def fetchall(self):
        return self.result


@pytest.fixture
def p22_database(monkeypatch):
    from property_watch import invisible_sale_repository as repository

    database = _FakeInvisibleSaleDatabase()
    monkeypatch.setattr(repository, "property_watch_cursor", database.cursor)
    return database


def _candidate(buy_request_id=7, digest="a" * 64):
    return {
        "buy_request_id": buy_request_id,
        "score_total": "91.00",
        "compatibility_status": "compatible",
        "reason_codes": ["location"],
        "last_activity_at": "2026-09-04T09:00:00Z",
        "budget_reference": "300000.00",
        "match_algorithm_version": "match-0.1",
        "candidate_digest": digest,
    }


def _refresh(database, candidates, digest):
    from property_watch.invisible_sale_repository import persist_invisible_sale_refresh

    return persist_invisible_sale_refresh(
        database.watch_id,
        candidates,
        digest,
        datetime(2026, 9, 4, tzinfo=timezone.utc),
    )


def test_refresh_persists_ready_empty_and_unchanged_without_effective_writes(p22_database):
    assert _refresh(p22_database, [_candidate()], "a" * 64)["status"] == "written"
    assert p22_database.opportunity["status"] == "ready"
    assert p22_database.opportunity["revision"] == 1
    writes_after_first = list(p22_database.effective_writes)

    assert _refresh(p22_database, [_candidate()], "a" * 64)["status"] == "unchanged"
    assert p22_database.effective_writes == writes_after_first

    assert _refresh(p22_database, [], "b" * 64)["status"] == "written"
    assert p22_database.opportunity["status"] == "empty"
    assert p22_database.opportunity["current_candidate_count"] == 0
    assert p22_database.candidates[7]["status"] == "stale"


def test_refresh_preserves_decisions_reactivates_stale_and_stops_when_closed(p22_database):
    from property_watch.invisible_sale_repository import (
        close_invisible_sale_for_stima,
        set_candidate_review_status,
    )

    _refresh(p22_database, [_candidate()], "a" * 64)
    set_candidate_review_status(1, 7, "approved")
    _refresh(p22_database, [_candidate(digest="b" * 64)], "b" * 64)
    assert p22_database.candidates[7]["status"] == "approved"

    _refresh(p22_database, [], "c" * 64)
    assert p22_database.candidates[7]["status"] == "stale"
    _refresh(p22_database, [_candidate(digest="d" * 64)], "d" * 64)
    assert p22_database.candidates[7]["status"] == "pending_review"

    close_invisible_sale_for_stima(1)
    writes_before = list(p22_database.effective_writes)
    assert _refresh(p22_database, [_candidate(digest="e" * 64)], "e" * 64) == {
        "status": "closed",
        "watch_id": p22_database.watch_id,
    }
    assert p22_database.effective_writes == writes_before


def test_refresh_creation_and_equivalent_concurrency_have_one_effective_refresh(p22_database):
    barrier = threading.Barrier(2)
    completed = threading.Event()
    outcomes = []

    def refresh():
        assert barrier.wait(timeout=1) in (0, 1)
        outcomes.append(_refresh(p22_database, [_candidate()], "a" * 64))
        if len(outcomes) == 2:
            completed.set()

    threads = [threading.Thread(target=refresh) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=1)
        assert not thread.is_alive()
    assert completed.wait(timeout=1)
    assert sorted(outcome["status"] for outcome in outcomes) == ["unchanged", "written"]
    assert p22_database.effective_writes.count("create_opportunity") == 1
    assert p22_database.effective_writes.count("refresh_opportunity") == 1
    opportunity_inserts = [
        sql for sql, _ in p22_database.executed
        if sql.startswith("INSERT INTO invisible_sale_opportunities")
    ]
    assert len(opportunity_inserts) == 2
    assert all("ON CONFLICT (watch_id) DO NOTHING" in sql for sql in opportunity_inserts)


def test_concurrent_close_prevents_a_waiting_refresh(p22_database):
    from property_watch.invisible_sale_repository import close_invisible_sale_for_stima

    _refresh(p22_database, [_candidate()], "a" * 64)
    p22_database.close_update_entered = threading.Event()
    p22_database.release_close_update = threading.Event()
    start = threading.Barrier(2)
    result = {}

    def close():
        assert start.wait(timeout=1) in (0, 1)
        result["close"] = close_invisible_sale_for_stima(1)

    def refresh():
        assert start.wait(timeout=1) in (0, 1)
        assert p22_database.close_update_entered.wait(timeout=1)
        result["refresh"] = _refresh(p22_database, [_candidate(digest="b" * 64)], "b" * 64)

    closer = threading.Thread(target=close)
    refresher = threading.Thread(target=refresh)
    closer.start()
    refresher.start()
    assert p22_database.close_update_entered.wait(timeout=1)
    p22_database.release_close_update.set()
    for thread in (closer, refresher):
        thread.join(timeout=1)
        assert not thread.is_alive()
    assert result == {
        "close": {"status": "closed"},
        "refresh": {"status": "closed", "watch_id": p22_database.watch_id},
    }
    assert p22_database.opportunity["revision"] == 1


def test_refresh_rolls_back_all_state_after_sql_error(p22_database):
    _refresh(p22_database, [_candidate()], "a" * 64)
    before = (
        p22_database.opportunity["revision"],
        p22_database.opportunity["candidate_digest"],
        p22_database.candidates[7]["status"],
        p22_database.candidates[7]["candidate_digest"],
        [event["idempotency_key"] for event in p22_database.events],
    )
    p22_database.fail_on = (
        "INSERT INTO invisible_sale_events (opportunity_id, event_type, opportunity_revision"
    )

    with pytest.raises(RuntimeError, match="forced SQL failure"):
        _refresh(p22_database, [_candidate(digest="b" * 64)], "b" * 64)

    assert (
        p22_database.opportunity["revision"],
        p22_database.opportunity["candidate_digest"],
        p22_database.candidates[7]["status"],
        p22_database.candidates[7]["candidate_digest"],
        [event["idempotency_key"] for event in p22_database.events],
    ) == before


def test_refresh_a_b_a_revisions_have_unique_audit_keys(p22_database):
    _refresh(p22_database, [_candidate(7)], "a" * 64)
    _refresh(p22_database, [_candidate(8, "b" * 64)], "b" * 64)
    _refresh(p22_database, [_candidate(7, "c" * 64)], "c" * 64)

    keys = [event["idempotency_key"] for event in p22_database.events]
    refresh_keys = [key for key in keys if ":refreshed:" in key]
    assert p22_database.opportunity["revision"] == 3
    assert len(refresh_keys) == len(set(refresh_keys)) == 3
    assert all(f":revision:{revision}:" in refresh_keys[revision - 1] for revision in (1, 2, 3))


def test_review_cycles_version_events_and_repeated_close_are_idempotent(p22_database):
    from property_watch.invisible_sale_repository import (
        close_invisible_sale_for_stima,
        set_candidate_review_status,
    )

    _refresh(p22_database, [_candidate()], "a" * 64)
    for target, version in (("approved", 1), ("rejected", 2), ("approved", 3)):
        assert set_candidate_review_status(1, 7, target)["status"] == target
        assert p22_database.candidates[7]["decision_version"] == version
    writes_before_same_decision = list(p22_database.effective_writes)
    assert set_candidate_review_status(1, 7, "approved") == {"status": "approved", "buy_request_id": 7}
    assert p22_database.effective_writes == writes_before_same_decision

    decision_keys = [event["idempotency_key"] for event in p22_database.events if ":decision:" in event["idempotency_key"]]
    assert len(decision_keys) == len(set(decision_keys)) == 3
    assert {key.split(":decision:")[1].split(":")[0] for key in decision_keys} == {"1", "2", "3"}
    assert close_invisible_sale_for_stima(1) == {"status": "closed"}
    writes_before_reclose = list(p22_database.effective_writes)
    assert close_invisible_sale_for_stima(1) == {"status": "closed"}
    assert p22_database.effective_writes == writes_before_reclose


def _p22_client(monkeypatch):
    from admin_security import require_admin
    from property_watch.router import router

    monkeypatch.setenv("ADMIN_USER", "giorgio")
    monkeypatch.setenv("ADMIN_PASS", "test-secret")
    app = FastAPI()
    app.include_router(router, dependencies=[Depends(require_admin)])
    return app, TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/property-watch/stime/1/invisible-sale"),
        ("post", "/api/property-watch/stime/1/invisible-sale/refresh"),
        ("post", "/api/property-watch/invisible-sale/refresh-active"),
        ("post", "/api/property-watch/stime/1/invisible-sale/candidates/7/approve"),
        ("post", "/api/property-watch/stime/1/invisible-sale/candidates/7/reject"),
        ("post", "/api/property-watch/stime/1/invisible-sale/close"),
    ],
)
def test_p22_routes_require_admin_authentication(monkeypatch, method, path):
    _, client = _p22_client(monkeypatch)
    assert getattr(client, method)(path).status_code == 401


def test_p22_posts_have_no_request_bodies_and_ignore_extraneous_json(monkeypatch):
    import property_watch.router as router_module

    app, client = _p22_client(monkeypatch)
    seen = []
    monkeypatch.setattr(
        router_module.invisible_sale_service,
        "safe_collect_invisible_sale_for_stima",
        lambda stima_id: seen.append(stima_id) or {"status": "unchanged", "watch_id": 11},
    )

    response = client.post(
        "/api/property-watch/stime/17/invisible-sale/refresh",
        auth=("giorgio", "test-secret"),
        json={"stima_id": 999, "status": "closed", "buy_request_id": 999},
    )
    assert response.status_code == 200
    assert seen == [17]
    operations = app.openapi()["paths"]
    p22_posts = [
        operation
        for path, methods in operations.items() if "/invisible-sale" in path
        for method, operation in methods.items() if method == "post"
    ]
    assert p22_posts and all("requestBody" not in operation for operation in p22_posts)


def test_p22_api_enforces_idor_and_stale_or_closed_review_conflicts(monkeypatch, p22_database):
    from property_watch.invisible_sale_repository import close_invisible_sale_for_stima

    _, client = _p22_client(monkeypatch)
    _refresh(p22_database, [_candidate()], "a" * 64)

    assert client.post(
        "/api/property-watch/stime/1/invisible-sale/candidates/99/approve",
        auth=("giorgio", "test-secret"),
    ).status_code == 404

    _refresh(p22_database, [], "b" * 64)
    assert client.post(
        "/api/property-watch/stime/1/invisible-sale/candidates/7/reject",
        auth=("giorgio", "test-secret"),
    ).status_code == 409
    _refresh(p22_database, [_candidate(digest="c" * 64)], "c" * 64)
    close_invisible_sale_for_stima(1)
    assert client.post(
        "/api/property-watch/stime/1/invisible-sale/candidates/7/approve",
        auth=("giorgio", "test-secret"),
    ).status_code == 409


@pytest.mark.parametrize("status", ["not_collected", "ready", "empty", "closed"])
def test_p22_get_is_read_only_for_every_visible_state(monkeypatch, status):
    import property_watch.router as router_module

    _, client = _p22_client(monkeypatch)
    calls = []
    state = {"status": status, "current_candidate_count": 0, "candidates": []}
    monkeypatch.setattr(
        router_module.invisible_sale_service,
        "get_invisible_sale_for_stima",
        lambda stima_id: calls.append(stima_id) or state,
    )
    response = client.get("/api/property-watch/stime/12/invisible-sale", auth=("giorgio", "test-secret"))
    assert response.status_code == 200
    assert response.json() == state
    assert calls == [12]
