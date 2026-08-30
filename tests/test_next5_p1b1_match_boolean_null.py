import pytest

from match.engine import calculate


def calculate_elevator_feature(actual, wanted):
    request = {
        "budget_target": 200000,
        "budget_max": 220000,
        "budget_flexibility_percent": 5,
        "surface_min": 70,
        "surface_target": 80,
        "rooms_min": 3,
        "bedrooms_min": 2,
        "bathrooms_min": 1,
        "locations": [
            {
                "municipality": "Tortoreto",
                "priority": 10,
                "is_required": True,
                "is_excluded": False,
            }
        ],
        "typologies": [
            {"property_type": "apartment", "requirement_level": "required"}
        ],
        "features": [
            {
                "feature_code": "elevator",
                "requirement_level": "required",
                "value_type": "boolean",
                "value_boolean": wanted,
            }
        ],
    }
    prop = {
        "city": "Tortoreto",
        "property_type": "apartment",
        "asking_price": 200000,
        "surface_sqm": 80,
        "rooms": 3,
        "bedrooms": 2,
        "bathrooms": 1,
        "elevator": actual,
        "condition": "good",
        "metadata": {},
    }

    result = calculate(request, prop)
    criterion = next(
        item for item in result["criteria"] if item["criterion_code"] == "elevator"
    )
    return result, criterion


@pytest.mark.parametrize(
    "actual,wanted,matched",
    [
        (True, True, True),
        (False, False, True),
        (True, False, False),
        (False, True, False),
        (None, False, False),
        (None, True, False),
    ],
)
def test_required_boolean_feature_keeps_null_distinct_from_false(
    actual, wanted, matched
):
    result, criterion = calculate_elevator_feature(actual, wanted)

    assert criterion["property_value"] is actual
    assert criterion["result"] == ("matched" if matched else "not_matched")
    assert criterion["score"] == (100 if matched else 0)
    assert criterion["is_blocking"] is (not matched)
    assert result["compatibility_status"] == (
        "compatible" if matched else "incompatible"
    )
