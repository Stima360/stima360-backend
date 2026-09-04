"""P24 - repository.py tests against a fake cursor.

Same convention as tests/test_next_best_action_repository.py: a FakeCursor
that captures issued SQL/params and returns canned results, so the
Python-side query construction and result handling are exercised for
real, without a live Postgres connection.
"""

from __future__ import annotations

from database_revival import repository


class FakeCursor:
    def __init__(self, fetchone_result=None, fetchall_result=None, rowcount=0):
        self.queries: list[tuple[str, object]] = []
        self._fetchone_result = fetchone_result
        self._fetchall_result = fetchall_result or []
        self.rowcount = rowcount

    def execute(self, query, params=None):
        sql = " ".join(str(query).split())
        self.queries.append((sql, params))

    def fetchone(self):
        return self._fetchone_result

    def fetchall(self):
        return list(self._fetchall_result)


def _last_sql(cur: FakeCursor) -> str:
    return cur.queries[-1][0]


def _last_params(cur: FakeCursor):
    return cur.queries[-1][1]


def test_acquire_daily_batch_lock_issues_pg_advisory_xact_lock_with_fixed_scope():
    cur = FakeCursor()
    repository.acquire_daily_batch_lock(cur)
    sql = _last_sql(cur).lower()
    params = _last_params(cur)
    assert "pg_advisory_xact_lock(hashtextextended(%s,0))" in sql.replace(" ", "")
    assert params == ("database_revival:daily_batch",) or params == ["database_revival:daily_batch"]


def test_count_batch_today_filters_created_at_current_date():
    cur = FakeCursor(fetchone_result={"n": 5})
    result = repository.count_batch_today(cur)
    sql = _last_sql(cur)
    assert "created_at::date = CURRENT_DATE" in sql
    assert result == 5


def test_count_batch_today_returns_zero_when_no_row():
    cur = FakeCursor(fetchone_result=None)
    result = repository.count_batch_today(cur)
    assert result == 0


def test_get_cooldown_contact_ids_filters_expires_at_greater_than_now():
    cur = FakeCursor(fetchall_result=[{"contact_id": 1}, {"contact_id": 2}])
    result = repository.get_cooldown_contact_ids(cur)
    sql = _last_sql(cur)
    assert "expires_at > NOW()" in sql
    assert result == {1, 2}


def test_get_cooldown_contact_ids_returns_empty_set_when_none():
    cur = FakeCursor(fetchall_result=[])
    result = repository.get_cooldown_contact_ids(cur)
    assert result == set()


def test_upsert_batch_row_uses_conditional_do_update_not_do_nothing():
    cur = FakeCursor(rowcount=1)
    repository.upsert_batch_row(cur, contact_id=3, lead_id=14)
    sql = _last_sql(cur)
    assert "ON CONFLICT (contact_id) DO UPDATE SET" in sql
    assert "DO NOTHING" not in sql
    assert "WHERE seller_revival_suppressions.expires_at <= NOW()" in sql


def test_upsert_batch_row_sets_created_at_and_expires_at_90_days():
    cur = FakeCursor(rowcount=1)
    repository.upsert_batch_row(cur, contact_id=3, lead_id=14)
    sql = _last_sql(cur)
    assert "NOW() + INTERVAL '90 days'" in sql


def test_upsert_batch_row_binds_contact_id_and_lead_id():
    cur = FakeCursor(rowcount=1)
    repository.upsert_batch_row(cur, contact_id=3, lead_id=14)
    params = _last_params(cur)
    assert params["contact_id"] == 3
    assert params["lead_id"] == 14


def test_upsert_batch_row_returns_true_when_rowcount_positive():
    cur = FakeCursor(rowcount=1)
    assert repository.upsert_batch_row(cur, contact_id=3, lead_id=14) is True


def test_upsert_batch_row_returns_false_when_rowcount_zero():
    """rowcount == 0 means the ON CONFLICT DO UPDATE's WHERE guard blocked
    the write (an active, non-expired row already exists) - defensive
    path, expected to be rare given the advisory lock, but must be
    reported truthfully rather than assumed successful."""
    cur = FakeCursor(rowcount=0)
    assert repository.upsert_batch_row(cur, contact_id=3, lead_id=14) is False


def test_list_batch_today_filters_created_at_current_date():
    canned = [{"contact_id": 3, "lead_id": 14}]
    cur = FakeCursor(fetchall_result=canned)
    result = repository.list_batch_today(cur)
    sql = _last_sql(cur)
    assert "created_at::date = CURRENT_DATE" in sql
    assert result == canned
