import pytest
from pydantic import ValidationError

from match.schemas import DetectStaleRequest, FeedbackCreate, MatchUpdate
from match.enums import REFRESH_STALE_DEFAULT_LIMIT, REFRESH_STALE_MAX_LIMIT


def test_detect_stale_accepts_single_scope():
    assert DetectStaleRequest(match_id=1).match_id == 1


def test_detect_stale_rejects_multiple_scopes():
    with pytest.raises(ValidationError):
        DetectStaleRequest(match_id=1, buy_request_id=2)


def test_negative_feedback_requires_reason():
    with pytest.raises(ValidationError):
        FeedbackCreate(source="buyer", feedback_type="negative")


def test_feedback_reason_is_controlled():
    with pytest.raises(ValidationError):
        FeedbackCreate(source="agent", feedback_type="negative", reason_code="unknown")


def test_positive_feedback_is_minimal():
    item = FeedbackCreate(source="agent", feedback_type="positive")
    assert item.reason_code is None


def test_match_update_accepts_review_flag():
    item = MatchUpdate(review_required=False)
    item.validate_values()
    assert item.review_required is False


def test_refresh_limits_are_documented():
    assert REFRESH_STALE_DEFAULT_LIMIT == 50
    assert REFRESH_STALE_MAX_LIMIT == 200
