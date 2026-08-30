from match.refresh import changed_fields, requires_review


def test_refresh_diff_only_reports_changes():
    before = {"score_total": 70, "match_class": "good", "compatibility_status": "compatible", "hard_fail_count": 0, "warning_count": 1}
    after = {"score_total": 85, "match_class": "strong", "compatibility_status": "compatible", "hard_fail_count": 0, "warning_count": 1}
    diff = changed_fields(before, after)
    assert set(diff) == {"score_total", "match_class"}
    assert diff["score_total"] == {"before": 70, "after": 85}
    assert requires_review(before, after) is True


def test_refresh_diff_empty_when_unchanged():
    value = {"score_total": 80, "match_class": "strong", "compatibility_status": "compatible", "hard_fail_count": 0, "warning_count": 0}
    assert changed_fields(value, dict(value)) == {}
    assert requires_review(value, dict(value)) is False
