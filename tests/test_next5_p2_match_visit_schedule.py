from __future__ import annotations

import copy
import json
import os
import re
import subprocess
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from core.exceptions import ValidationError


ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "static/buy_admin/index.html"
JS_PATH = ROOT / "static/buy_admin/assets/app.js"


def _decision(**values):
    from buy.schemas import MatchDecision

    return MatchDecision(**values)


def _schedule(repository, request_id, match_id, data):
    schedule = getattr(repository, "schedule_match_visit", None)
    assert callable(schedule), "buy.repository.schedule_match_visit mancante"
    return schedule(request_id, match_id, data)


def test_visit_scheduled_requires_scheduled_at_but_other_decisions_do_not():
    with pytest.raises(PydanticValidationError, match="scheduled_at"):
        _decision(action="visit_scheduled")

    for action in (
        "proposed",
        "interested",
        "visit_requested",
        "visited",
        "offer_candidate",
    ):
        assert _decision(action=action).action == action
    assert _decision(action="discarded", reason_code="buyer_decision").action == "discarded"


def test_scheduled_at_preserves_the_explicit_instant():
    value = _decision(
        action="visit_scheduled",
        scheduled_at="2026-08-30T10:15:00+02:00",
    ).scheduled_at

    assert value is not None
    assert value.astimezone(timezone.utc) == datetime(2026, 8, 30, 8, 15, tzinfo=timezone.utc)


class TransactionDatabase:
    def __init__(self, *, lead_id=41, fail_after_visit=False):
        self.state = {
            "buy_requests": {
                7: {"id": 7, "contact_id": 31, "lead_id": lead_id},
                8: {"id": 8, "contact_id": 32, "lead_id": None},
            },
            "matches": {
                11: {
                    "id": 11,
                    "buy_request_id": 7,
                    "property_id": 21,
                    "commercial_status": "interested",
                },
                12: {
                    "id": 12,
                    "buy_request_id": 8,
                    "property_id": 22,
                    "commercial_status": "interested",
                },
            },
            "property_visits": [],
            "interactions": [],
            "history": [],
        }
        self.fail_after_visit = fail_after_visit
        self.transactions = 0
        self.commits = 0
        self.rollbacks = 0
        self.queries = []

    @contextmanager
    def cursor(self, commit=False):
        assert commit is True, "il workflow visita deve usare core_cursor(commit=True)"
        self.transactions += 1
        staged = copy.deepcopy(self.state)
        cursor = TransactionCursor(self, staged)
        try:
            yield object(), cursor
        except Exception:
            self.rollbacks += 1
            raise
        else:
            self.state = staged
            self.commits += 1


class TransactionCursor:
    def __init__(self, database, staged):
        self.database = database
        self.state = staged
        self.result = None

    def execute(self, query, params=()):
        normalized = " ".join(query.split())
        lowered = normalized.lower()
        self.database.queries.append((normalized, tuple(params)))
        self.result = None

        if lowered.startswith("select contact_id,lead_id from buy_requests"):
            self.result = copy.deepcopy(self.state["buy_requests"].get(params[0]))
            return
        if lowered.startswith("select property_id,buy_request_id from matches"):
            self.result = copy.deepcopy(self.state["matches"].get(params[0]))
            return
        if "from buy_request_interactions i" in lowered and "join property_visits v" in lowered:
            request_id, match_id, scheduled_at = params
            visits = {item["id"]: item for item in self.state["property_visits"]}
            matches = [
                item
                for item in self.state["interactions"]
                if item["buy_request_id"] == request_id
                and item["match_id"] == match_id
                and item["interaction_type"] == "visit_scheduled"
                and item.get("property_visit_id") in visits
                and visits[item["property_visit_id"]]["scheduled_at"] == scheduled_at
            ]
            self.result = copy.deepcopy(matches[-1]) if matches else None
            return
        if lowered.startswith("insert into property_visits"):
            item = self._inserted_row(query, params)
            item["id"] = len(self.state["property_visits"]) + 101
            self.state["property_visits"].append(item)
            self.result = copy.deepcopy(item)
            return
        if lowered.startswith("insert into buy_request_interactions"):
            if self.database.fail_after_visit:
                raise RuntimeError("interaction insert failed")
            item = self._inserted_row(query, params)
            item["id"] = len(self.state["interactions"]) + 201
            self.state["interactions"].append(item)
            self.result = copy.deepcopy(item)
            return
        if lowered.startswith("update matches set commercial_status"):
            status, match_id = params
            self.state["matches"][match_id]["commercial_status"] = status
            return
        if lowered.startswith("insert into buy_request_history"):
            fields = (
                "buy_request_id",
                "event_type",
                "match_id",
                "property_id",
                "task_id",
                "reason_code",
                "description",
                "old_value",
                "new_value",
                "created_by",
            )
            self.state["history"].append(dict(zip(fields, params)))
            return
        raise AssertionError(f"Query non prevista: {normalized}")

    def fetchone(self):
        return copy.deepcopy(self.result)

    @staticmethod
    def _inserted_row(query, params):
        match = re.search(r"INSERT\s+INTO\s+\w+\s*\(([^)]+)\)", query, re.IGNORECASE)
        assert match, query
        columns = [column.strip() for column in match.group(1).split(",")]
        return dict(zip(columns, params))


def schedule_data(value="2026-08-30T08:15:00+00:00"):
    return {
        "scheduled_at": datetime.fromisoformat(value),
        "reason_code": None,
        "notes": "Visita con il cliente",
        "occurred_at": None,
        "created_by": "operatore",
    }


def test_visit_workflow_derives_links_and_writes_everything_once(monkeypatch):
    from buy import repository

    database = TransactionDatabase()
    monkeypatch.setattr(repository, "core_cursor", database.cursor)

    result = _schedule(repository, 7, 11, schedule_data())

    assert database.transactions == database.commits == 1
    assert database.rollbacks == 0
    assert len(database.state["property_visits"]) == 1
    visit = database.state["property_visits"][0]
    assert visit == {
        "property_id": 21,
        "contact_id": 31,
        "lead_id": 41,
        "scheduled_at": datetime(2026, 8, 30, 8, 15, tzinfo=timezone.utc),
        "status": "scheduled",
        "created_by": "operatore",
        "id": 101,
    }
    assert len(database.state["interactions"]) == 1
    interaction = database.state["interactions"][0]
    assert interaction["buy_request_id"] == 7
    assert interaction["match_id"] == 11
    assert interaction["property_id"] == 21
    assert interaction["property_visit_id"] == 101
    assert interaction["interaction_type"] == "visit_scheduled"
    assert result["property_visit_id"] == 101
    assert database.state["matches"][11]["commercial_status"] == "visit_scheduled"
    assert len(database.state["history"]) == 1
    assert database.state["history"][0]["event_type"] == "visit_scheduled"


def test_optional_lead_is_derived_from_buy_and_may_be_null(monkeypatch):
    from buy import repository

    database = TransactionDatabase(lead_id=None)
    monkeypatch.setattr(repository, "core_cursor", database.cursor)

    _schedule(repository, 7, 11, schedule_data())

    assert database.state["property_visits"][0]["contact_id"] == 31
    assert database.state["property_visits"][0]["lead_id"] is None


def test_match_must_belong_to_buy_and_property_cannot_come_from_payload(monkeypatch):
    from buy import repository

    database = TransactionDatabase()
    monkeypatch.setattr(repository, "core_cursor", database.cursor)
    incoming = {**schedule_data(), "property_id": 999, "contact_id": 998, "lead_id": 997}

    with pytest.raises(ValidationError, match="match does not belong"):
        _schedule(repository, 7, 12, incoming)
    assert database.state["property_visits"] == []

    result = _schedule(repository, 7, 11, incoming)
    visit = database.state["property_visits"][0]
    assert visit["property_id"] == 21
    assert visit["contact_id"] == 31
    assert visit["lead_id"] == 41
    assert result["property_id"] == 21


def test_failure_after_visit_insert_rolls_back_every_write(monkeypatch):
    from buy import repository

    database = TransactionDatabase(fail_after_visit=True)
    monkeypatch.setattr(repository, "core_cursor", database.cursor)

    with pytest.raises(RuntimeError, match="interaction insert failed"):
        _schedule(repository, 7, 11, schedule_data())

    assert database.commits == 0
    assert database.rollbacks == 1
    assert database.state["property_visits"] == []
    assert database.state["interactions"] == []
    assert database.state["history"] == []
    assert database.state["matches"][11]["commercial_status"] == "interested"


def test_same_match_and_instant_is_idempotent_under_match_lock(monkeypatch):
    from buy import repository

    database = TransactionDatabase()
    monkeypatch.setattr(repository, "core_cursor", database.cursor)
    data = schedule_data()

    first = _schedule(repository, 7, 11, data)
    second = _schedule(repository, 7, 11, data)

    assert second == first
    assert len(database.state["property_visits"]) == 1
    assert len(database.state["interactions"]) == 1
    assert len(database.state["history"]) == 1
    match_updates = [query for query, _ in database.queries if query.lower().startswith("update matches")]
    assert len(match_updates) == 1
    match_lock = next(query for query, _ in database.queries if "FROM matches" in query)
    assert "FOR UPDATE" in match_lock.upper()


def test_same_match_with_a_different_instant_creates_another_visit(monkeypatch):
    from buy import repository

    database = TransactionDatabase()
    monkeypatch.setattr(repository, "core_cursor", database.cursor)

    first = _schedule(repository, 7, 11, schedule_data())
    second = _schedule(repository, 7, 11, schedule_data("2026-09-01T14:00:00+00:00"))

    assert first["property_visit_id"] != second["property_visit_id"]
    assert len(database.state["property_visits"]) == 2
    assert len(database.state["interactions"]) == 2
    assert len(database.state["history"]) == 2


def test_service_routes_only_visit_scheduled_to_atomic_workflow(monkeypatch):
    from buy import repository, service

    scheduled_calls = []
    interaction_calls = []
    monkeypatch.setattr(
        repository,
        "schedule_match_visit",
        lambda request_id, match_id, data: scheduled_calls.append((request_id, match_id, data)) or {"id": 1},
        raising=False,
    )
    monkeypatch.setattr(
        repository,
        "add_interaction",
        lambda request_id, data: interaction_calls.append((request_id, data)) or {"id": 2},
    )

    service.match_decision(
        7,
        11,
        _decision(action="visit_scheduled", scheduled_at="2026-08-30T10:15:00+02:00"),
    )
    assert len(scheduled_calls) == 1
    assert scheduled_calls[0][0:2] == (7, 11)
    assert scheduled_calls[0][2]["scheduled_at"].utcoffset().total_seconds() == 7200
    assert interaction_calls == []

    for action in ("proposed", "interested", "visit_requested", "visited", "offer_candidate"):
        service.match_decision(7, 11, _decision(action=action))
    service.match_decision(7, 11, _decision(action="discarded", reason_code="buyer_decision"))
    assert [call[1]["interaction_type"] for call in interaction_calls] == [
        "proposed",
        "interested",
        "visit_requested",
        "visited",
        "offer_candidate",
        "discarded",
    ]
    assert all("scheduled_at" not in call[1] for call in interaction_calls)


def test_only_visit_scheduled_match_mapping_changes():
    from buy import repository

    assert repository.MATCH_STATUS == {
        "proposed": "suggested",
        "discarded": "rejected",
        "interested": "interested",
        "visit_requested": "visit_requested",
        "visit_scheduled": "visit_scheduled",
        "visited": "visited",
        "offer_candidate": "interested",
    }


def run_js(function_name, argument, *, timezone_name="Europe/Rome"):
    script = (
        f"const api=require({json.dumps(str(JS_PATH))});"
        f"const result=api[{json.dumps(function_name)}]({json.dumps(argument)});"
        "process.stdout.write(JSON.stringify(result));"
    )
    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        env={**os.environ, "TZ": timezone_name},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def run_action_visibility(actions):
    js = JS_PATH.read_text(encoding="utf-8")
    start = js.index("function updateActionScheduleField(){")
    end = js.index("\n}\n\nfunction bindUi", start) + 2
    function_source = js[start:end]
    script = f"""
{function_source}
const action={{value:'proposed'}};
const input={{value:'',disabled:true,required:false}};
const field={{hidden:true,style:{{display:'block'}}}};
const form={{querySelector(selector){{
  if(selector==='[name=action]')return action;
  if(selector==='[name=scheduled_at]')return input;
  throw new Error('selector inatteso: '+selector);
}}}};
function $(selector){{
  if(selector==='#actionForm')return form;
  if(selector==='#actionScheduledAtField')return field;
  throw new Error('selector inatteso: '+selector);
}}
const states=[];
for(const value of {json.dumps(actions)}){{
  action.value=value;
  if(value==='visit_scheduled')input.value='2026-08-30T10:15';
  updateActionScheduleField();
  states.push({{
    action:value,
    hidden:field.hidden,
    display:field.style.display,
    required:input.required,
    disabled:input.disabled,
    value:input.value,
  }});
}}
process.stdout.write(JSON.stringify(states));
"""
    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_action_datetime_is_hidden_at_author_style_level_before_javascript_runs():
    html = HTML_PATH.read_text(encoding="utf-8")
    field = re.search(r'<label[^>]+id="actionScheduledAtField"[^>]*>', html)

    assert field
    assert re.search(r"\bhidden\b", field.group(0))
    assert re.search(r'style="[^"]*display\s*:\s*none', field.group(0))


def test_action_datetime_visibility_required_state_and_value_follow_action():
    states = run_action_visibility(
        [
            "proposed",
            "visit_scheduled",
            "proposed",
            "visit_scheduled",
            "discarded",
            "interested",
            "visit_requested",
            "visited",
            "offer_candidate",
        ]
    )

    initial = states[0]
    assert initial == {
        "action": "proposed",
        "hidden": True,
        "display": "none",
        "required": False,
        "disabled": True,
        "value": "",
    }

    for state in states:
        if state["action"] == "visit_scheduled":
            assert state["hidden"] is False
            assert state["display"] == "block"
            assert state["required"] is True
            assert state["disabled"] is False
        else:
            assert state["hidden"] is True
            assert state["display"] == "none"
            assert state["required"] is False
            assert state["disabled"] is True
            assert state["value"] == ""


def test_frontend_requires_datetime_only_for_scheduled_visit_and_sends_iso():
    html = HTML_PATH.read_text(encoding="utf-8")
    action_form = re.search(r'<form[^>]+id="actionForm"[^>]*>(.*?)</form>', html, re.DOTALL)
    assert action_form
    form = action_form.group(1)
    assert re.search(r'name="scheduled_at"[^>]+type="datetime-local"', form)
    assert not re.search(r'name="(?:property_id|contact_id|lead_id)"', form)

    scheduled = run_js(
        "buildMatchDecisionPayload",
        {"action": "visit_scheduled", "scheduled_at": "2026-08-30T10:15", "notes": "Nota"},
    )
    assert scheduled == {
        "action": "visit_scheduled",
        "scheduled_at": "2026-08-30T08:15:00.000Z",
        "notes": "Nota",
    }
    for action in (
        "proposed",
        "discarded",
        "interested",
        "visit_requested",
        "visited",
        "offer_candidate",
    ):
        values = {"action": action, "scheduled_at": "2026-08-30T10:15"}
        if action == "discarded":
            values["reason_code"] = "buyer_decision"
        ordinary = run_js("buildMatchDecisionPayload", values)
        assert "scheduled_at" not in ordinary


def test_frontend_guards_double_submit_and_preserves_safe_links():
    js = JS_PATH.read_text(encoding="utf-8")

    assert re.search(r"if\s*\(actionSubmitPending\)\s*return", js)
    assert re.search(r"actionSubmitPending\s*=\s*true", js)
    assert re.search(r"actionSubmitPending\s*=\s*false", js)
    assert re.search(r"\.disabled\s*=\s*true", js)
    assert re.search(r"finally\s*\{[^}]*\.disabled\s*=\s*false", js, re.DOTALL)
    assert "esc=" in js and "textContent" in js
    assert "/property-admin/?id=" in js
    assert "/match-admin/?id=" in js
    assert "positiveId" in js


def test_existing_consumers_read_the_new_property_visit_and_auth_is_unchanged():
    property_repository = (ROOT / "property/repository.py").read_text(encoding="utf-8")
    crm_service = (ROOT / "crm/service.py").read_text(encoding="utf-8")
    agenda = (ROOT / "static/core_admin/assets/app.js").read_text(encoding="utf-8")
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    router = (ROOT / "buy/router.py").read_text(encoding="utf-8")

    assert "FROM property_visits v" in property_repository
    assert "WHERE v.contact_id=%s" in property_repository
    assert "list_visits_by_contact(contact_id)" in crm_service
    assert "api('/api/property/visits?limit=500')" in agenda
    assert "app.include_router(buy_router, dependencies=[Depends(require_admin)])" in main
    assert "@router.post('/requests/{request_id}/matches/{match_id}/decision',status_code=201)" in router


def test_scope_does_not_modify_property_or_match_engine_contracts():
    migration = (ROOT / "migrations/002_property_01.sql").read_text(encoding="utf-8")
    property_schema = (ROOT / "property/schemas.py").read_text(encoding="utf-8")

    assert "scheduled_at TIMESTAMPTZ NOT NULL" in migration
    assert re.search(r"class VisitCreate\b[\s\S]*?scheduled_at:\s*datetime", property_schema)
