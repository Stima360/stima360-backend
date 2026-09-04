"""P24 - eligibility.py tests against a fake cursor.

No real Postgres is available in this environment (established repeatedly
across P17-P23: no network, no psycopg2-binary). Consistent with the
existing convention in this repo for SQL-heavy code (see
tests/test_next_best_action_repository.py's FakeCursor dispatching on
sql.startswith(...), and tests/test_followup_isolation.py's text-level
migration assertions), these tests verify:

  1. the SQL text issued by each function contains every clause required
     by the frozen P24 business rules (a real substring check against the
     actual query, not a description of it);
  2. parameters are bound correctly;
  3. the Python-side result shaping (what the function returns given a
     canned row set) is correct.

They do NOT execute the join against real relational data - that would
require a live Postgres instance, unavailable in this sandbox (same
class of limitation already flagged for the P23 live-test phase). This is
a deliberate, disclosed scope reduction from a full behavioural/integration
suite - see the deviation note in the final implementation report.
"""

from __future__ import annotations

from contextlib import contextmanager

from database_revival import eligibility


class FakeCursor:
    def __init__(self, canned_rows=None, canned_exists=None):
        self.queries: list[tuple[str, object]] = []
        self._canned_rows = canned_rows or []
        self._canned_exists = canned_exists
        self._result = []

    def execute(self, query, params=None):
        sql = " ".join(str(query).split())
        self.queries.append((sql, params))
        lowered = sql.lower()
        if "select exists" in lowered:
            self._result = [{"eligible": bool(self._canned_exists)}]
        else:
            self._result = list(self._canned_rows)

    def fetchall(self):
        return list(self._result)

    def fetchone(self):
        return self._result[0] if self._result else None


def _last_sql(cur: FakeCursor) -> str:
    return cur.queries[-1][0]


def _last_params(cur: FakeCursor):
    return cur.queries[-1][1]


# --- find_eligible_candidates: clause coverage -----------------------------


def test_find_eligible_candidates_filters_status_paused():
    cur = FakeCursor()
    eligibility.find_eligible_candidates(cur, exclude_contact_ids=set(), limit=20)
    assert "l.status = 'paused'" in _last_sql(cur)


def test_find_eligible_candidates_filters_pipeline_sell():
    cur = FakeCursor()
    eligibility.find_eligible_candidates(cur, exclude_contact_ids=set(), limit=20)
    assert "l.pipeline = 'sell'" in _last_sql(cur)


def test_find_eligible_candidates_excludes_stage_won():
    cur = FakeCursor()
    eligibility.find_eligible_candidates(cur, exclude_contact_ids=set(), limit=20)
    assert "l.stage != 'won'" in _last_sql(cur)


def test_find_eligible_candidates_requires_consent_true():
    cur = FakeCursor()
    eligibility.find_eligible_candidates(cur, exclude_contact_ids=set(), limit=20)
    assert "marketing_consent IS TRUE" in _last_sql(cur)


def test_find_eligible_candidates_excludes_archived_contact():
    cur = FakeCursor()
    eligibility.find_eligible_candidates(cur, exclude_contact_ids=set(), limit=20)
    assert "c.status != 'archived'" in _last_sql(cur)


def test_find_eligible_candidates_uses_180_day_dormancy_threshold():
    cur = FakeCursor()
    eligibility.find_eligible_candidates(cur, exclude_contact_ids=set(), limit=20)
    assert "INTERVAL '180 days'" in _last_sql(cur)


def test_find_eligible_candidates_excludes_sold_property_via_property_leads():
    cur = FakeCursor()
    eligibility.find_eligible_candidates(cur, exclude_contact_ids=set(), limit=20)
    sql = _last_sql(cur)
    assert "property_leads" in sql
    assert "commercial_status = 'sold'" in sql


def test_find_eligible_candidates_excludes_active_commercial_statuses():
    cur = FakeCursor()
    eligibility.find_eligible_candidates(cur, exclude_contact_ids=set(), limit=20)
    sql = _last_sql(cur)
    assert "'mandate'" in sql and "'active'" in sql and "'reserved'" in sql and "'under_offer'" in sql


def test_find_eligible_candidates_excludes_pending_property_sale():
    cur = FakeCursor()
    eligibility.find_eligible_candidates(cur, exclude_contact_ids=set(), limit=20)
    sql = _last_sql(cur)
    assert "property_sales" in sql
    assert "ps.status = 'pending'" in sql


def test_find_eligible_candidates_excludes_draft_or_submitted_proposal():
    cur = FakeCursor()
    eligibility.find_eligible_candidates(cur, exclude_contact_ids=set(), limit=20)
    sql = _last_sql(cur)
    assert "property_proposals" in sql
    assert "'draft'" in sql and "'submitted'" in sql


def test_find_eligible_candidates_excludes_open_or_in_progress_task():
    cur = FakeCursor()
    eligibility.find_eligible_candidates(cur, exclude_contact_ids=set(), limit=20)
    sql = _last_sql(cur)
    assert "'open'" in sql and "'in_progress'" in sql
    assert "tasks" in sql


def test_find_eligible_candidates_excludes_pending_followup_action():
    cur = FakeCursor()
    eligibility.find_eligible_candidates(cur, exclude_contact_ids=set(), limit=20)
    sql = _last_sql(cur)
    assert "followup_actions" in sql
    assert "fa.status = 'pending'" in sql


def test_find_eligible_candidates_admits_null_next_action():
    cur = FakeCursor()
    eligibility.find_eligible_candidates(cur, exclude_contact_ids=set(), limit=20)
    sql = _last_sql(cur)
    assert "next_action_at IS NULL" in sql


def test_find_eligible_candidates_last_activity_considers_activities():
    cur = FakeCursor()
    eligibility.find_eligible_candidates(cur, exclude_contact_ids=set(), limit=20)
    sql = _last_sql(cur)
    assert "activities" in sql
    assert "a.occurred_at" in sql


def test_find_eligible_candidates_last_activity_considers_seller_timeline_events():
    cur = FakeCursor()
    eligibility.find_eligible_candidates(cur, exclude_contact_ids=set(), limit=20)
    sql = _last_sql(cur)
    assert "seller_timeline_events" in sql
    assert "ste.occurred_at" in sql


def test_find_eligible_candidates_last_activity_considers_completed_tasks():
    cur = FakeCursor()
    eligibility.find_eligible_candidates(cur, exclude_contact_ids=set(), limit=20)
    sql = _last_sql(cur)
    assert "t.completed_at" in sql
    assert "'completed'" in sql


def test_find_eligible_candidates_last_activity_falls_back_to_created_at():
    cur = FakeCursor()
    eligibility.find_eligible_candidates(cur, exclude_contact_ids=set(), limit=20)
    sql = _last_sql(cur)
    assert "l.created_at" in sql


def test_find_eligible_candidates_never_references_leads_updated_at_for_dormancy():
    cur = FakeCursor()
    eligibility.find_eligible_candidates(cur, exclude_contact_ids=set(), limit=20)
    sql = _last_sql(cur).lower()
    assert "l.updated_at" not in sql


def test_find_eligible_candidates_orders_by_last_activity_then_lead_id():
    cur = FakeCursor()
    eligibility.find_eligible_candidates(cur, exclude_contact_ids=set(), limit=20)
    sql = _last_sql(cur)
    assert "ORDER BY last_activity_at ASC, lead_id ASC" in sql


def test_find_eligible_candidates_dedups_one_row_per_contact():
    cur = FakeCursor()
    eligibility.find_eligible_candidates(cur, exclude_contact_ids=set(), limit=20)
    sql = _last_sql(cur)
    assert "PARTITION BY contact_id" in sql
    assert "ORDER BY last_activity_at ASC, lead_id ASC" in sql  # tie-break inside the window too


def test_find_eligible_candidates_binds_limit_param():
    cur = FakeCursor()
    eligibility.find_eligible_candidates(cur, exclude_contact_ids=set(), limit=7)
    params = _last_params(cur)
    assert params["limit"] == 7


def test_find_eligible_candidates_binds_exclude_contact_ids_param():
    cur = FakeCursor()
    eligibility.find_eligible_candidates(cur, exclude_contact_ids={3, 9}, limit=20)
    params = _last_params(cur)
    assert set(params["exclude_contact_ids"]) == {3, 9}


def test_find_eligible_candidates_handles_empty_exclude_set():
    cur = FakeCursor()
    # must not raise (e.g. on an empty tuple/list passed to = ANY(%s))
    eligibility.find_eligible_candidates(cur, exclude_contact_ids=set(), limit=20)
    params = _last_params(cur)
    assert params["exclude_contact_ids"] == []


def test_find_eligible_candidates_returns_rows_from_cursor():
    canned = [
        {"contact_id": 3, "lead_id": 14, "last_activity_at": "2026-01-01"},
        {"contact_id": 5, "lead_id": 20, "last_activity_at": "2026-01-02"},
    ]
    cur = FakeCursor(canned_rows=canned)
    result = eligibility.find_eligible_candidates(cur, exclude_contact_ids=set(), limit=20)
    assert result == canned


def test_find_eligible_candidates_returns_empty_list_when_no_rows():
    cur = FakeCursor(canned_rows=[])
    result = eligibility.find_eligible_candidates(cur, exclude_contact_ids=set(), limit=20)
    assert result == []


# --- is_still_eligible: live re-validation, no cooldown clause -------------


def test_is_still_eligible_issues_exists_query_scoped_to_single_lead():
    cur = FakeCursor(canned_exists=True)
    eligibility.is_still_eligible(cur, contact_id=3, lead_id=14)
    sql = _last_sql(cur)
    params = _last_params(cur)
    assert "select exists" in sql.lower()
    assert params["lead_id"] == 14
    assert params["contact_id"] == 3


def test_is_still_eligible_reuses_same_predicate_clauses_as_batch_selection():
    cur = FakeCursor(canned_exists=True)
    eligibility.is_still_eligible(cur, contact_id=3, lead_id=14)
    sql = _last_sql(cur)
    for clause in (
        "l.status = 'paused'",
        "l.pipeline = 'sell'",
        "l.stage != 'won'",
        "marketing_consent IS TRUE",
        "c.status != 'archived'",
        "INTERVAL '180 days'",
        "commercial_status = 'sold'",
    ):
        assert clause in sql, f"is_still_eligible non riusa la clausola: {clause}"


def test_is_still_eligible_does_not_reference_seller_revival_suppressions():
    """The cooldown table must never gate this predicate against itself -
    it is applied only in ensure_today_batch's candidate selection, never
    in the live re-validation of an already-batched row."""
    cur = FakeCursor(canned_exists=True)
    eligibility.is_still_eligible(cur, contact_id=3, lead_id=14)
    sql = _last_sql(cur).lower()
    assert "seller_revival_suppressions" not in sql


def test_is_still_eligible_returns_true_when_cursor_says_exists():
    cur = FakeCursor(canned_exists=True)
    assert eligibility.is_still_eligible(cur, contact_id=3, lead_id=14) is True


def test_is_still_eligible_returns_false_when_cursor_says_not_exists():
    cur = FakeCursor(canned_exists=False)
    assert eligibility.is_still_eligible(cur, contact_id=3, lead_id=14) is False
