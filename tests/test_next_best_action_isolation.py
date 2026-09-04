"""P23 — service orchestration tests: anti-duplication (section 4 / 12.D)
and module isolation (no P17-P22 module imports next_best_action).
"""

from __future__ import annotations

from datetime import datetime, timezone

from next_best_action import service


def _candidate(**overrides):
    base = {
        "subject_type": "lead",
        "subject_id": 14,
        "contact_id": 3,
        "lead_id": 14,
        "stima_id": None,
        "source_signal": "followup_overdue",
        "signal_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "action_type": "contact_overdue_followup",
        "priority": "urgent",
        "reason": "Follow-up scaduto",
        "cta_route": "contatti",
        "cta_params": [3],
    }
    base.update(overrides)
    return base


def test_refresh_suppresses_nba_when_open_task_already_exists(monkeypatch):
    monkeypatch.setattr(service, "collect_all_signals", lambda limit: [_candidate()])
    monkeypatch.setattr(
        service.core_repository,
        "list_tasks",
        lambda **kwargs: [{"status": "open"}] if kwargs.get("contact_id") == 3 else [],
    )
    captured = {}
    monkeypatch.setattr(
        service.nba_repository,
        "replace_current_actions",
        lambda rows: captured.setdefault("rows", rows) or {"created": 0, "updated": 0, "removed": 0},
    )

    result = service.refresh()

    assert captured["rows"] == []
    assert result["suppressed_duplicates"] == 1
    assert result["total_active"] == 0


def test_refresh_keeps_nba_when_no_open_task_exists(monkeypatch):
    monkeypatch.setattr(service, "collect_all_signals", lambda limit: [_candidate()])
    monkeypatch.setattr(service.core_repository, "list_tasks", lambda **kwargs: [])
    captured = {}

    def _fake_replace(rows):
        captured["rows"] = rows
        return {"created": 1, "updated": 0, "removed": 0}

    monkeypatch.setattr(service.nba_repository, "replace_current_actions", _fake_replace)

    result = service.refresh()

    assert len(captured["rows"]) == 1
    assert captured["rows"][0]["subject_id"] == 14
    assert result["suppressed_duplicates"] == 0
    assert result["total_active"] == 1


def test_refresh_groups_multiple_candidates_per_subject_into_single_winner(monkeypatch):
    candidates = [
        _candidate(source_signal="match_strong_unproposed", cta_route="abbinamenti"),
        _candidate(source_signal="seller_intent_hot"),
    ]
    monkeypatch.setattr(service, "collect_all_signals", lambda limit: candidates)
    monkeypatch.setattr(service.core_repository, "list_tasks", lambda **kwargs: [])
    captured = {}

    def _fake_replace(rows):
        captured["rows"] = rows
        return {"created": 1, "updated": 0, "removed": 0}

    monkeypatch.setattr(service.nba_repository, "replace_current_actions", _fake_replace)

    result = service.refresh()

    assert len(captured["rows"]) == 1
    assert captured["rows"][0]["source_signal"] == "seller_intent_hot"
    assert result["evaluated_subjects"] == 1


def test_anti_duplication_checks_contact_lead_and_stima_ids_independently(monkeypatch):
    seen_filters = []

    def _fake_list_tasks(**kwargs):
        seen_filters.append({k: v for k, v in kwargs.items() if v is not None and k != "limit" and k != "offset" and k != "status"})
        return []

    monkeypatch.setattr(service.core_repository, "list_tasks", _fake_list_tasks)
    candidate = _candidate(lead_id=14, stima_id=None)

    service._has_open_equivalent_task(candidate)

    assert {"contact_id": 3} in seen_filters
    assert {"lead_id": 14} in seen_filters


def test_anti_duplication_suppresses_lead_next_action_overdue_when_task_open(monkeypatch):
    """Case 6 (FASE 5): the new lead-side next_action_overdue candidate
    must go through the exact same anti-duplication path as every other
    signal - no separate treatment."""
    candidate = _candidate(
        source_signal="next_action_overdue",
        action_type="contact_overdue_next_action",
        reason="Prossima azione pianificata gia' scaduta",
    )
    monkeypatch.setattr(service, "collect_all_signals", lambda limit: [candidate])
    monkeypatch.setattr(
        service.core_repository,
        "list_tasks",
        lambda **kwargs: [{"status": "open"}] if kwargs.get("contact_id") == 3 else [],
    )
    captured = {}

    def _fake_replace(rows):
        captured["rows"] = rows
        return {"created": 0, "updated": 0, "removed": 0}

    monkeypatch.setattr(service.nba_repository, "replace_current_actions", _fake_replace)

    result = service.refresh()

    assert captured["rows"] == []
    assert result["suppressed_duplicates"] == 1


def test_refresh_calls_safe_ensure_today_batch_before_collect_all_signals(monkeypatch):
    calls = []
    monkeypatch.setattr(
        service.database_revival_service, "safe_ensure_today_batch", lambda: calls.append("ensure")
    )
    monkeypatch.setattr(service, "collect_all_signals", lambda limit: calls.append("collect") or [])
    monkeypatch.setattr(service.core_repository, "list_tasks", lambda **kwargs: [])
    monkeypatch.setattr(service.nba_repository, "replace_current_actions", lambda rows: {"created": 0, "updated": 0, "removed": 0})

    service.refresh()

    assert calls == ["ensure", "collect"]


def test_refresh_has_no_redundant_exception_handling_around_ensure_call(monkeypatch):
    """refresh() itself adds no extra try/except around the P24 call - all
    non-blocking safety is the single responsibility of
    safe_ensure_today_batch()'s own try/except (already verified for real
    in test_database_revival_service.py::
    test_safe_ensure_today_batch_returns_none_and_logs_on_exception).
    Proven here by monkeypatching a raising stand-in in its place and
    confirming refresh() does NOT silently swallow it - i.e. refresh()
    trusts the wrapper completely instead of duplicating its safety net."""

    def _boom():
        raise RuntimeError("stand-in bypassing the real safe wrapper's own try/except")

    monkeypatch.setattr(service.database_revival_service, "safe_ensure_today_batch", _boom)
    monkeypatch.setattr(service, "collect_all_signals", lambda limit: [_candidate()])
    monkeypatch.setattr(service.core_repository, "list_tasks", lambda **kwargs: [])
    monkeypatch.setattr(
        service.nba_repository,
        "replace_current_actions",
        lambda rows: {"created": 1, "updated": 0, "removed": 0},
    )

    import pytest

    with pytest.raises(RuntimeError):
        service.refresh()


def test_refresh_result_unaffected_when_no_database_revival_candidates(monkeypatch):
    """collect_all_signals already includes database_revival (P24, Task
    4/5) - with zero candidates from it, refresh()'s result must be
    identical to the pre-P24 behaviour for the other five signals."""
    monkeypatch.setattr(service.database_revival_service, "safe_ensure_today_batch", lambda: None)
    monkeypatch.setattr(service, "collect_all_signals", lambda limit: [_candidate()])
    monkeypatch.setattr(service.core_repository, "list_tasks", lambda **kwargs: [])
    captured = {}

    def _fake_replace(rows):
        captured["rows"] = rows
        return {"created": 1, "updated": 0, "removed": 0}

    monkeypatch.setattr(service.nba_repository, "replace_current_actions", _fake_replace)

    result = service.refresh()

    assert len(captured["rows"]) == 1
    assert result["total_active"] == 1


def test_no_p17_p22_module_imports_next_best_action():
    """Dependency direction guard (section 5): none of the P17-P22
    modules next_best_action reads from may import it back."""
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    modules = ["seller_intent", "followup", "flow", "property_watch", "core", "match"]
    for module_name in modules:
        module_dir = root / module_name
        if not module_dir.is_dir():
            continue
        for py_file in module_dir.rglob("*.py"):
            tree = ast.parse(py_file.read_text(), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module] if node.module else []
                else:
                    continue
                for name in names:
                    assert not (name or "").startswith("next_best_action"), (
                        f"{py_file} imports next_best_action - forbidden dependency direction"
                    )
