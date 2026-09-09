from __future__ import annotations

import inspect
from contextlib import contextmanager
from pathlib import Path

import pytest

from buy import repository, router, service
from core.exceptions import NotFoundError
from core.scope import ProgrammingError
from operator_auth.context import OperatorContext
from operator_auth.exceptions import PlatformAdminAgencyRequired

A = 10
B = 20


def ctx(agency_id=A):
    return OperatorContext(
        user_id=1 if agency_id is not None else 999,
        agency_id=agency_id,
        role="agency_owner" if agency_id is not None else "platform_admin",
        is_platform_admin=agency_id is None,
        session_id=1,
        auth_channel="legacy_basic" if agency_id is not None else "operator_session",
    )


class Cursor:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.current = None
        self.statements = []

    def execute(self, sql, params=None):
        self.statements.append((" ".join(str(sql).split()), params))
        self.current = self.responses.pop(0) if self.responses else None

    def fetchone(self):
        if isinstance(self.current, list):
            return self.current[0] if self.current else None
        return self.current

    def fetchall(self):
        return self.current if isinstance(self.current, list) else []


def install(monkeypatch, responses=None):
    cur = Cursor(responses)

    @contextmanager
    def fake_cursor(*_a, **_k):
        yield None, cur

    monkeypatch.setattr(repository, "core_cursor", fake_cursor)
    return cur


def test_all_buy_routes_resolve_legacy_agency_context():
    source = inspect.getsource(router)
    assert source.count("Depends(legacy_basic_agency_context)") == 23


def test_legacy_public_signatures_remain_compatible():
    assert list(inspect.signature(repository.schedule_match_visit).parameters) == [
        "request_id", "match_id", "data"
    ]
    assert list(inspect.signature(service.match_decision).parameters) == [
        "i", "match_id", "p"
    ]
    assert inspect.signature(repository.history).parameters["agency_id"].default is None


def test_create_scoped_rejects_client_agency(monkeypatch):
    install(monkeypatch)
    with pytest.raises(ProgrammingError):
        repository.create_request_scoped(
            ctx(), {"contact_id": 1, "title": "x", "agency_id": B}
        )


def test_unbound_platform_admin_fails_closed(monkeypatch):
    install(monkeypatch)
    with pytest.raises(PlatformAdminAgencyRequired):
        repository.list_requests_scoped(
            ctx(None), 50, 0, None, None, None, None, None, None, None
        )


def test_list_scoped_is_agency_scoped(monkeypatch):
    cur = install(monkeypatch, [[]])
    repository.list_requests_scoped(
        ctx(), 50, 0, None, None, None, None, None, None, None
    )
    sql, params = cur.statements[0]
    assert "b.agency_id=%s" in sql
    assert A in params


def test_get_scoped_foreign_buy_is_not_found(monkeypatch):
    install(monkeypatch, [None])
    with pytest.raises(NotFoundError):
        repository.get_request_scoped(ctx(), 999)


def test_child_delete_scoped_joins_parent(monkeypatch):
    cur = install(monkeypatch, [None])
    with pytest.raises(NotFoundError):
        repository.delete_child_scoped(
            ctx(), "buy_request_locations", 999, "location"
        )
    sql, params = cur.statements[0]
    assert "USING buy_requests b" in sql
    assert "b.agency_id=%s" in sql
    assert A in params


def test_match_listing_scoped_requires_property_same_agency(monkeypatch):
    cur = install(monkeypatch, [{"id": 1, "agency_id": A}, []])
    repository.list_matches_scoped(ctx(), 1)
    sql, params = cur.statements[-1]
    assert "p.agency_id=%s" in sql
    assert A in params


def test_router_keeps_frozen_match_decision_decorator():
    source = inspect.getsource(router)
    assert "@router.post('/requests/{request_id}/matches/{match_id}/decision',status_code=201)" in source


def test_backfill_contract_is_deterministic():
    root = Path(__file__).resolve().parents[1]
    m38 = (root / "migrations/038_p26_buy_agency_backfill.sql").read_text()
    lowered = m38.lower()
    assert "set agency_id=c.agency_id" in lowered
    assert "default agency" not in lowered
    assert "limit 1" not in lowered
    assert "min(" not in lowered
    assert "max(" not in lowered
    assert "coalesce(" not in lowered


def test_enforcement_has_exact_triggers_and_history_match_guard():
    root = Path(__file__).resolve().parents[1]
    m39 = (root / "migrations/039_p26_buy_agency_enforce.sql").read_text()
    triggers = (
        "trg_buy_requests_agency_integrity",
        "trg_buy_interactions_agency_integrity",
        "trg_buy_task_links_agency_integrity",
        "trg_buy_history_agency_integrity",
    )
    for name in triggers:
        assert name in m39
    assert m39.count("CREATE TRIGGER") == 4
    assert "h.match_id IS NOT NULL" in m39
    assert "m.buy_request_id <> h.buy_request_id" in m39
    assert "NEW.match_id IS NOT NULL" in m39
    assert "ALTER TABLE buy_requests ALTER COLUMN agency_id SET NOT NULL" in m39
