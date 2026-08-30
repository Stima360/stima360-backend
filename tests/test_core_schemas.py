from pydantic import ValidationError

from core.normalization import normalize_email, normalize_phone
from core.schemas import ActivityCreate, ContactCreate, TaskCreate


def test_normalization_is_conservative():
    assert normalize_email("  Mario@Example.COM ") == "mario@example.com"
    assert normalize_phone("333 123 4567") == "393331234567"
    assert normalize_phone("+39 333 123 4567") == "393331234567"


def test_person_requires_identity():
    try:
        ContactCreate()
    except ValidationError:
        pass
    else:
        raise AssertionError("ContactCreate should reject a person without identity")


def test_activity_requires_one_reference():
    try:
        ActivityCreate(activity_type="note")
    except ValidationError:
        pass
    else:
        raise AssertionError("ActivityCreate should require a reference")


def test_task_requires_one_reference():
    try:
        TaskCreate(title="Call owner")
    except ValidationError:
        pass
    else:
        raise AssertionError("TaskCreate should require a reference")
