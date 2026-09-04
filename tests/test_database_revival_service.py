"""P24 - service.py tests: ensure_today_batch / safe_ensure_today_batch
(daily batch + cooldown + concurrency, Task 3) and collect_today_signals
(signal adapter, Task 4).

Monkeypatches the module's own imported names (repository.*,
eligibility.*), same technique as tests/test_next_best_action_signals.py,
so no real DB/cursor machinery is needed - the fake cursor context manager
below only has to satisfy the `with ... as (_, cur):` shape.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from database_revival import service


class _NullConn:
    pass


@contextmanager
def _fake_cursor_factory(cur_obj):
    @contextmanager
    def _ctx(*, commit: bool = False):
        yield _NullConn(), cur_obj

    yield _ctx


class DummyCursor:
    """Only needs to exist as an opaque object passed through to the
    monkeypatched repository/eligibility functions below - its identity,
    not its behaviour, is what the ordering/argument assertions check."""


def _patch_cursor(monkeypatch, cur_obj=None):
    cur_obj = cur_obj if cur_obj is not None else DummyCursor()

    @contextmanager
    def _ctx(*, commit: bool = False):
        yield _NullConn(), cur_obj

    monkeypatch.setattr(service, "database_revival_cursor", _ctx)
    return cur_obj


# --- ensure_today_batch: ordering + cap ------------------------------------


def test_ensure_today_batch_acquires_lock_before_count(monkeypatch):
    calls = []
    cur = _patch_cursor(monkeypatch)
    monkeypatch.setattr(service.repository, "acquire_daily_batch_lock", lambda c: calls.append("lock"))
    monkeypatch.setattr(service.repository, "count_batch_today", lambda c: calls.append("count") or 0)
    monkeypatch.setattr(service.repository, "get_cooldown_contact_ids", lambda c: calls.append("cooldown") or set())
    monkeypatch.setattr(service.eligibility, "find_eligible_candidates", lambda c, exclude_contact_ids, limit: calls.append("eligibility") or [])
    monkeypatch.setattr(service.repository, "upsert_batch_row", lambda c, **kw: True)

    service.ensure_today_batch()

    assert calls[0] == "lock"
    assert calls.index("lock") < calls.index("count")


def test_ensure_today_batch_first_selection_inserts_rows(monkeypatch):
    _patch_cursor(monkeypatch)
    monkeypatch.setattr(service.repository, "acquire_daily_batch_lock", lambda c: None)
    monkeypatch.setattr(service.repository, "count_batch_today", lambda c: 0)
    monkeypatch.setattr(service.repository, "get_cooldown_contact_ids", lambda c: set())
    monkeypatch.setattr(
        service.eligibility,
        "find_eligible_candidates",
        lambda c, exclude_contact_ids, limit: [
            {"contact_id": 1, "lead_id": 10, "last_activity_at": None},
            {"contact_id": 2, "lead_id": 20, "last_activity_at": None},
        ],
    )
    inserted = []
    monkeypatch.setattr(
        service.repository,
        "upsert_batch_row",
        lambda c, contact_id, lead_id: inserted.append((contact_id, lead_id)) or True,
    )

    result = service.ensure_today_batch()

    assert result["added"] == 2
    assert inserted == [(1, 10), (2, 20)]


def test_ensure_today_batch_second_call_same_day_does_not_duplicate(monkeypatch):
    """count_batch_today already reflects the first call's writes (it is
    read fresh at the start of the transaction, per the real repository
    behaviour), so with 20 already present, remaining_slots is 0 and no
    eligibility query or insert should even be attempted."""
    _patch_cursor(monkeypatch)
    monkeypatch.setattr(service.repository, "acquire_daily_batch_lock", lambda c: None)
    monkeypatch.setattr(service.repository, "count_batch_today", lambda c: 20)
    eligibility_called = []
    monkeypatch.setattr(
        service.eligibility,
        "find_eligible_candidates",
        lambda c, exclude_contact_ids, limit: eligibility_called.append(1) or [],
    )
    monkeypatch.setattr(service.repository, "get_cooldown_contact_ids", lambda c: set())
    monkeypatch.setattr(service.repository, "upsert_batch_row", lambda c, **kw: True)

    result = service.ensure_today_batch()

    assert result["added"] == 0
    assert eligibility_called == []


def test_ensure_today_batch_partial_top_up_when_slots_remain(monkeypatch):
    _patch_cursor(monkeypatch)
    monkeypatch.setattr(service.repository, "acquire_daily_batch_lock", lambda c: None)
    monkeypatch.setattr(service.repository, "count_batch_today", lambda c: 15)
    captured_limit = {}

    def _fake_find(c, exclude_contact_ids, limit):
        captured_limit["limit"] = limit
        return [{"contact_id": 1, "lead_id": 10, "last_activity_at": None}]

    monkeypatch.setattr(service.eligibility, "find_eligible_candidates", _fake_find)
    monkeypatch.setattr(service.repository, "get_cooldown_contact_ids", lambda c: set())
    monkeypatch.setattr(service.repository, "upsert_batch_row", lambda c, **kw: True)

    result = service.ensure_today_batch()

    assert captured_limit["limit"] == 5  # 20 - 15
    assert result["added"] == 1
    assert result["batch_size_today"] == 16


def test_ensure_today_batch_never_exceeds_20_per_day(monkeypatch):
    _patch_cursor(monkeypatch)
    monkeypatch.setattr(service.repository, "acquire_daily_batch_lock", lambda c: None)
    monkeypatch.setattr(service.repository, "count_batch_today", lambda c: 19)
    monkeypatch.setattr(
        service.eligibility,
        "find_eligible_candidates",
        lambda c, exclude_contact_ids, limit: [
            {"contact_id": i, "lead_id": i * 10, "last_activity_at": None} for i in range(1, limit + 1)
        ],
    )
    monkeypatch.setattr(service.repository, "get_cooldown_contact_ids", lambda c: set())
    monkeypatch.setattr(service.repository, "upsert_batch_row", lambda c, **kw: True)

    result = service.ensure_today_batch()

    assert result["added"] == 1
    assert result["batch_size_today"] == 20


def test_ensure_today_batch_with_19_existing_adds_at_most_1(monkeypatch):
    _patch_cursor(monkeypatch)
    monkeypatch.setattr(service.repository, "acquire_daily_batch_lock", lambda c: None)
    monkeypatch.setattr(service.repository, "count_batch_today", lambda c: 19)
    captured_limit = {}

    def _fake_find(c, exclude_contact_ids, limit):
        captured_limit["limit"] = limit
        return []

    monkeypatch.setattr(service.eligibility, "find_eligible_candidates", _fake_find)
    monkeypatch.setattr(service.repository, "get_cooldown_contact_ids", lambda c: set())
    monkeypatch.setattr(service.repository, "upsert_batch_row", lambda c, **kw: True)

    service.ensure_today_batch()

    assert captured_limit["limit"] == 1


def test_ensure_today_batch_excluded_cooldown_contacts_do_not_consume_slots(monkeypatch):
    _patch_cursor(monkeypatch)
    monkeypatch.setattr(service.repository, "acquire_daily_batch_lock", lambda c: None)
    monkeypatch.setattr(service.repository, "count_batch_today", lambda c: 0)
    monkeypatch.setattr(service.repository, "get_cooldown_contact_ids", lambda c: {99, 100})
    captured_exclude = {}

    def _fake_find(c, exclude_contact_ids, limit):
        captured_exclude["exclude"] = exclude_contact_ids
        return []

    monkeypatch.setattr(service.eligibility, "find_eligible_candidates", _fake_find)
    monkeypatch.setattr(service.repository, "upsert_batch_row", lambda c, **kw: True)

    service.ensure_today_batch()

    assert captured_exclude["exclude"] == {99, 100}


def test_ensure_today_batch_reuses_expired_row_updates_lead_id(monkeypatch):
    """upsert_batch_row is the sole write primitive (Task 3, repository
    layer) - this test verifies ensure_today_batch passes the FRESH
    lead_id from this cycle's selection through to it, so a contact whose
    cooldown just expired gets its lead_id updated on reuse."""
    _patch_cursor(monkeypatch)
    monkeypatch.setattr(service.repository, "acquire_daily_batch_lock", lambda c: None)
    monkeypatch.setattr(service.repository, "count_batch_today", lambda c: 0)
    monkeypatch.setattr(service.repository, "get_cooldown_contact_ids", lambda c: set())
    monkeypatch.setattr(
        service.eligibility,
        "find_eligible_candidates",
        lambda c, exclude_contact_ids, limit: [{"contact_id": 3, "lead_id": 99, "last_activity_at": None}],
    )
    captured = {}
    monkeypatch.setattr(
        service.repository,
        "upsert_batch_row",
        lambda c, contact_id, lead_id: captured.update(contact_id=contact_id, lead_id=lead_id) or True,
    )

    service.ensure_today_batch()

    assert captured == {"contact_id": 3, "lead_id": 99}


def test_ensure_today_batch_counts_upsert_returning_false_as_not_added(monkeypatch):
    """Defensive path: if upsert_batch_row's WHERE guard blocks the write
    (rowcount 0 - an active row unexpectedly already exists), it must not
    be counted as 'added', consistent with what actually happened in the
    database rather than what was attempted."""
    _patch_cursor(monkeypatch)
    monkeypatch.setattr(service.repository, "acquire_daily_batch_lock", lambda c: None)
    monkeypatch.setattr(service.repository, "count_batch_today", lambda c: 0)
    monkeypatch.setattr(service.repository, "get_cooldown_contact_ids", lambda c: set())
    monkeypatch.setattr(
        service.eligibility,
        "find_eligible_candidates",
        lambda c, exclude_contact_ids, limit: [{"contact_id": 3, "lead_id": 99, "last_activity_at": None}],
    )
    monkeypatch.setattr(service.repository, "upsert_batch_row", lambda c, **kw: False)

    result = service.ensure_today_batch()

    assert result["added"] == 0


# --- safe_ensure_today_batch: non-blocking wrapper -------------------------


def test_safe_ensure_today_batch_returns_result_on_success(monkeypatch):
    monkeypatch.setattr(service, "ensure_today_batch", lambda now=None: {"added": 2, "batch_size_today": 2})
    assert service.safe_ensure_today_batch() == {"added": 2, "batch_size_today": 2}


def test_safe_ensure_today_batch_returns_none_and_logs_on_exception(monkeypatch):
    def _boom(now=None):
        raise RuntimeError("db down")

    monkeypatch.setattr(service, "ensure_today_batch", _boom)
    logged = []
    monkeypatch.setattr(service.logger, "error", lambda *a, **kw: logged.append((a, kw)))

    result = service.safe_ensure_today_batch()

    assert result is None
    assert len(logged) == 1


# --- collect_today_signals: read-only signal adapter (Task 4) -------------


def _batch_row(contact_id=3, lead_id=14, created_at=None):
    return {
        "contact_id": contact_id,
        "lead_id": lead_id,
        "created_at": created_at or datetime(2026, 1, 1, tzinfo=timezone.utc),
        "expires_at": datetime(2026, 4, 1, tzinfo=timezone.utc),
    }


def test_collect_today_signals_returns_candidate_for_each_still_eligible_batch_row(monkeypatch):
    _patch_cursor(monkeypatch)
    monkeypatch.setattr(service.repository, "list_batch_today", lambda c: [_batch_row()])
    monkeypatch.setattr(service.eligibility, "is_still_eligible", lambda c, contact_id, lead_id: True)

    candidates = service.collect_today_signals()

    assert len(candidates) == 1
    assert candidates[0]["subject_type"] == "lead"
    assert candidates[0]["subject_id"] == 14
    assert candidates[0]["contact_id"] == 3
    assert candidates[0]["lead_id"] == 14
    assert candidates[0]["source_signal"] == "database_revival"
    assert candidates[0]["priority"] == "normal"
    assert candidates[0]["cta_route"] == "contatti"
    assert candidates[0]["cta_params"] == [3]


def test_collect_today_signals_skips_row_when_not_still_eligible(monkeypatch):
    _patch_cursor(monkeypatch)
    monkeypatch.setattr(service.repository, "list_batch_today", lambda c: [_batch_row()])
    monkeypatch.setattr(service.eligibility, "is_still_eligible", lambda c, contact_id, lead_id: False)

    candidates = service.collect_today_signals()

    assert candidates == []


def test_collect_today_signals_does_not_call_write_primitives(monkeypatch):
    """collect_today_signals must be purely read-only: it never calls
    upsert_batch_row or acquire_daily_batch_lock - those belong exclusively
    to ensure_today_batch (Task 3)."""
    _patch_cursor(monkeypatch)
    monkeypatch.setattr(service.repository, "list_batch_today", lambda c: [_batch_row()])
    monkeypatch.setattr(service.eligibility, "is_still_eligible", lambda c, contact_id, lead_id: True)

    def _fail(*a, **kw):
        raise AssertionError("collect_today_signals must not write")

    monkeypatch.setattr(service.repository, "upsert_batch_row", _fail)
    monkeypatch.setattr(service.repository, "acquire_daily_batch_lock", _fail)

    service.collect_today_signals()


def test_collect_today_signals_skips_row_with_null_lead_id(monkeypatch):
    """lead_id is ON DELETE SET NULL - if the lead was deleted after the
    contact entered the batch, there is nothing left to propose (no
    subject_id to attach the NBA to), and eligibility cannot even be
    re-evaluated without a lead_id."""
    _patch_cursor(monkeypatch)
    monkeypatch.setattr(service.repository, "list_batch_today", lambda c: [_batch_row(lead_id=None)])
    called = []
    monkeypatch.setattr(
        service.eligibility, "is_still_eligible", lambda c, contact_id, lead_id: called.append(1) or True
    )

    candidates = service.collect_today_signals()

    assert candidates == []
    assert called == []


def test_collect_today_signals_multiple_rows_mixed_eligibility(monkeypatch):
    _patch_cursor(monkeypatch)
    monkeypatch.setattr(
        service.repository,
        "list_batch_today",
        lambda c: [_batch_row(contact_id=1, lead_id=10), _batch_row(contact_id=2, lead_id=20)],
    )
    monkeypatch.setattr(
        service.eligibility,
        "is_still_eligible",
        lambda c, contact_id, lead_id: contact_id == 1,
    )

    candidates = service.collect_today_signals()

    assert len(candidates) == 1
    assert candidates[0]["contact_id"] == 1
