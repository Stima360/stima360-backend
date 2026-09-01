from __future__ import annotations

from followup import service


def test_temporal_scan_escalates_open_low_or_normal_candidates(monkeypatch):
    calls = []

    monkeypatch.setattr(
        service.repository,
        "list_temporal_escalation_candidates",
        lambda **kwargs: [
            {"id": 10, "contact_id": 1, "lead_id": 2, "stima_id": 3},
            {"id": 11, "contact_id": None, "lead_id": None, "stima_id": 4},
        ],
    )

    def _execute(**kwargs):
        calls.append(kwargs)
        return {"status": "completed", "task_id": kwargs["task_id"], "followup_action_id": kwargs["task_id"]}

    monkeypatch.setattr(service.repository, "execute_temporal_escalation", _execute)

    result = service.run_temporal_escalation_scan(limit=50)

    assert result["status"] == "completed"
    assert result["processed"] == 2
    assert result["escalated"] == 2
    assert result["failed"] == 0
    assert calls[0]["idempotency_key"] == (
        "followup:time:FOLLOWUP_TASK_STALE_ESCALATE_V1:task:10:v1"
    )


def test_temporal_scan_is_partial_failure_when_one_candidate_fails(monkeypatch):
    monkeypatch.setattr(
        service.repository,
        "list_temporal_escalation_candidates",
        lambda **kwargs: [{"id": 10}, {"id": 11}],
    )

    def _execute(**kwargs):
        if kwargs["task_id"] == 11:
            raise RuntimeError("boom")
        return {"status": "completed", "task_id": kwargs["task_id"], "followup_action_id": 1}

    monkeypatch.setattr(service.repository, "execute_temporal_escalation", _execute)

    result = service.run_temporal_escalation_scan(limit=10)

    assert result["status"] == "partial_failure"
    assert result["processed"] == 2
    assert result["escalated"] == 1
    assert result["failed"] == 1


def test_temporal_scan_validates_limit():
    try:
        service.run_temporal_escalation_scan(limit=0)
    except Exception as exc:
        assert "limit must be between 1 and 500" in str(exc)
    else:
        raise AssertionError("expected limit validation error")


def test_safe_temporal_scan_returns_failed_payload_on_total_error(monkeypatch):
    monkeypatch.setattr(
        service,
        "run_temporal_escalation_scan",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("total outage")),
    )
    result = service.safe_run_temporal_escalation_scan(limit=10)
    assert result["status"] == "failed"
    assert result["processed"] == 0
