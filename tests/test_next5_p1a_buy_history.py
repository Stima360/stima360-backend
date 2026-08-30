from datetime import date, datetime, timezone
from decimal import Decimal

from psycopg2.extensions import adapt
from psycopg2.extras import Json

from buy.repository import history


class JsonAdaptingCursor:
    def __init__(self):
        self.adapted_json = []

    def execute(self, _query, params):
        json_params = [param for param in params if isinstance(param, Json)]
        self.adapted_json = [adapt(param).getquoted() for param in json_params]


def test_buy_history_serializes_decimal_date_and_datetime_values():
    cursor = JsonAdaptingCursor()

    history(
        cursor,
        request_id=5,
        event_type="request_updated",
        old_value={
            "budget_target": Decimal("180000.00"),
            "context": {"reviewed_on": date(2026, 8, 28)},
        },
        new_value={
            "budget_target": Decimal("190000.00"),
            "reviews": [datetime(2026, 8, 28, 10, 30, tzinfo=timezone.utc)],
        },
    )

    assert len(cursor.adapted_json) == 2
    assert b"180000.0" in cursor.adapted_json[0]
    assert b"2026-08-28" in cursor.adapted_json[0]
    assert b"190000.0" in cursor.adapted_json[1]
    assert b"2026-08-28T10:30:00+00:00" in cursor.adapted_json[1]
