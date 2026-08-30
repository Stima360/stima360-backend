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

from core.exceptions import NotFoundError, ValidationError


ROOT = Path(__file__).resolve().parents[1]
JS_PATH = ROOT / "static/property_admin/assets/app.js"
POST_VISIT_ACTIONS = ("visited", "interested", "discarded", "offer_candidate")


class PropertyMutationCursor:
    def __init__(self):
        self.queries = []
        self.result = None

    def execute(self, query, params=()):
        normalized = " ".join(query.split())
        self.queries.append((normalized, tuple(params)))
        if normalized.lower().startswith("update property_visits set"):
            self.result = {
                "id": params[-1],
                "property_id": 21,
                "status": "completed",
                "outcome": "visita effettuata",
                "feedback": "spazi adeguati",
                "rating": 4,
            }
            return
        raise AssertionError(f"Query non prevista: {normalized}")

    def fetchone(self):
        return copy.deepcopy(self.result)


@contextmanager
def property_mutation_cursor(cursor, commit=False):
    assert commit is True
    yield object(), cursor


def test_property_completion_and_feedback_remain_property_only(monkeypatch):
    from property import repository

    cursor = PropertyMutationCursor()
    monkeypatch.setattr(
        repository,
        "core_cursor",
        lambda commit=False: property_mutation_cursor(cursor, commit),
    )

    result = repository.update_visit(
        101,
        {
            "status": "completed",
            "outcome": "visita effettuata",
            "feedback": "spazi adeguati",
            "rating": 4,
        },
    )

    assert result["status"] == "completed"
    sql = " ".join(query for query, _ in cursor.queries).lower()
    for forbidden in ("buy_request_interactions", "buy_request_history", "update matches"):
        assert forbidden not in sql


class PropertyDetailCursor:
    def __init__(self):
        self.result = None
        self.rows = []

    def execute(self, query, params=()):
        normalized = " ".join(query.split())
        lowered = normalized.lower()
        self.result = None
        self.rows = []
        if lowered.startswith("select * from properties"):
            self.result = {
                "id": 21,
                "title": "Casa centro",
                "city": "Teramo",
                "surface_sqm": 90,
                "asking_price": 190000,
                "classification": "A",
            }
            return
        if "from property_contacts" in lowered or "from property_leads" in lowered:
            return
        if "from property_documents" in lowered or "from property_photos" in lowered:
            return
        if "from property_visits v" in lowered and "buy_request_interactions" in lowered:
            self.rows = [
                {
                    "id": 101,
                    "property_id": 21,
                    "status": "completed",
                    "scheduled_at": datetime(2026, 8, 30, 8, 15, tzinfo=timezone.utc),
                    "buy_request_id": 7,
                    "match_id": 11,
                    "last_commercial_interaction_id": 401,
                    "last_commercial_interaction_type": "interested",
                }
            ]
            return
        if "from property_visits" in lowered:
            self.rows = [
                {
                    "id": 101,
                    "property_id": 21,
                    "status": "completed",
                    "scheduled_at": datetime(2026, 8, 30, 8, 15, tzinfo=timezone.utc),
                }
            ]
            return
        if "from property_price_history" in lowered or "from property_status_history" in lowered:
            return
        raise AssertionError(f"Query non prevista: {normalized}")

    def fetchone(self):
        return copy.deepcopy(self.result)

    def fetchall(self):
        return copy.deepcopy(self.rows)


@contextmanager
def property_detail_cursor(cursor, commit=False):
    assert commit is False
    yield object(), cursor


def test_property_detail_exposes_p2_link_and_latest_commercial_outcome(monkeypatch):
    from property import repository

    cursor = PropertyDetailCursor()
    monkeypatch.setattr(
        repository,
        "core_cursor",
        lambda commit=False: property_detail_cursor(cursor, commit),
    )

    detail = repository.get_property(21)
    visit = detail["visits"][0]

    assert visit["id"] == 101
    assert visit["buy_request_id"] == 7
    assert visit["match_id"] == 11
    assert visit["last_commercial_interaction_id"] == 401
    assert visit["last_commercial_interaction_type"] == "interested"


class OutcomeDatabase:
    def __init__(self, *, fail_after_interaction=False):
        self.state = {
            "buy_requests": {7: {"id": 7}, 8: {"id": 8}},
            "matches": {
                11: {
                    "id": 11,
                    "buy_request_id": 7,
                    "property_id": 21,
                    "commercial_status": "visit_scheduled",
                },
                12: {
                    "id": 12,
                    "buy_request_id": 8,
                    "property_id": 22,
                    "commercial_status": "visit_scheduled",
                },
            },
            "property_visits": {
                101: {"id": 101, "property_id": 21, "status": "completed"},
                102: {"id": 102, "property_id": 21, "status": "completed"},
                103: {"id": 103, "property_id": 21, "status": "completed"},
                201: {"id": 201, "property_id": 22, "status": "completed"},
            },
            "interactions": [
                {
                    "id": 301,
                    "buy_request_id": 7,
                    "match_id": 11,
                    "property_id": 21,
                    "property_visit_id": 101,
                    "interaction_type": "visit_scheduled",
                },
                {
                    "id": 302,
                    "buy_request_id": 7,
                    "match_id": 11,
                    "property_id": 21,
                    "property_visit_id": 102,
                    "interaction_type": "visit_scheduled",
                },
                {
                    "id": 303,
                    "buy_request_id": 8,
                    "match_id": 12,
                    "property_id": 22,
                    "property_visit_id": 201,
                    "interaction_type": "visit_scheduled",
                },
            ],
            "history": [],
        }
        self.fail_after_interaction = fail_after_interaction
        self.transactions = 0
        self.commits = 0
        self.rollbacks = 0
        self.queries = []

    @contextmanager
    def cursor(self, commit=False):
        assert commit is True, "l'esito collegato deve usare core_cursor(commit=True)"
        self.transactions += 1
        staged = copy.deepcopy(self.state)
        cursor = OutcomeCursor(self, staged)
        try:
            yield object(), cursor
        except Exception:
            self.rollbacks += 1
            raise
        else:
            self.state = staged
            self.commits += 1


class OutcomeCursor:
    def __init__(self, database, staged):
        self.database = database
        self.state = staged
        self.result = None

    def execute(self, query, params=()):
        normalized = " ".join(query.split())
        lowered = normalized.lower()
        self.database.queries.append((normalized, tuple(params)))
        self.result = None

        if lowered.startswith("select id from buy_requests"):
            self.result = copy.deepcopy(self.state["buy_requests"].get(params[0]))
            return
        if lowered.startswith("select property_id,buy_request_id from matches"):
            self.result = copy.deepcopy(self.state["matches"].get(params[0]))
            return
        if lowered.startswith("select id from properties"):
            property_id = params[0]
            if any(item["property_id"] == property_id for item in self.state["matches"].values()):
                self.result = {"id": property_id}
            return
        if lowered.startswith("select property_id from property_visits"):
            self.result = copy.deepcopy(self.state["property_visits"].get(params[0]))
            return
        if "from buy_request_interactions" in lowered and "interaction_type='visit_scheduled'" in lowered:
            visit_id, request_id, match_id, property_id = params
            self.result = next(
                (
                    copy.deepcopy(item)
                    for item in reversed(self.state["interactions"])
                    if item.get("property_visit_id") == visit_id
                    and item["buy_request_id"] == request_id
                    and item.get("match_id") == match_id
                    and item.get("property_id") == property_id
                    and item["interaction_type"] == "visit_scheduled"
                ),
                None,
            )
            return
        if lowered.startswith("insert into buy_request_interactions"):
            item = self._inserted_row(query, params)
            item["id"] = max(existing["id"] for existing in self.state["interactions"]) + 1
            self.state["interactions"].append(item)
            self.result = copy.deepcopy(item)
            return
        if lowered.startswith("update matches set commercial_status"):
            if self.database.fail_after_interaction:
                raise RuntimeError("match update failed")
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


def linked_outcome(action="visited", visit_id=101, **overrides):
    data = {
        "match_id": 11,
        "property_visit_id": visit_id,
        "interaction_type": action,
        "reason_code": "buyer_decision" if action == "discarded" else None,
        "notes": f"Esito {action}",
        "created_by": "property-admin",
    }
    data.update(overrides)
    return data


@pytest.mark.parametrize(
    "action,match_status,history_event",
    [
        ("visited", "visited", "visited"),
        ("interested", "interested", "match_interested"),
        ("discarded", "rejected", "match_discarded"),
        ("offer_candidate", "interested", "offer_candidate"),
    ],
)
def test_linked_outcomes_preserve_existing_match_and_history_mapping(
    monkeypatch, action, match_status, history_event
):
    from buy import repository

    database = OutcomeDatabase()
    monkeypatch.setattr(repository, "core_cursor", database.cursor)

    result = repository.add_interaction(7, linked_outcome(action))

    assert result["property_visit_id"] == 101
    assert result["property_id"] == 21
    assert database.state["matches"][11]["commercial_status"] == match_status
    assert database.state["history"][-1]["event_type"] == history_event


def test_linked_outcome_uses_one_transaction_and_locks_buy_match_and_visit(monkeypatch):
    from buy import repository

    database = OutcomeDatabase()
    monkeypatch.setattr(repository, "core_cursor", database.cursor)

    repository.add_interaction(7, linked_outcome())

    assert database.transactions == database.commits == 1
    assert database.rollbacks == 0
    locks = [query.lower() for query, _ in database.queries if "for update" in query.lower()]
    assert any("buy_requests" in query for query in locks)
    assert any("matches" in query for query in locks)
    assert any("property_visits" in query for query in locks)


@pytest.mark.parametrize(
    "request_id,data,error_type,error_message",
    [
        (7, linked_outcome(visit_id=201), ValidationError, "property"),
        (7, linked_outcome(match_id=12, visit_id=201), ValidationError, "match does not belong"),
        (7, linked_outcome(visit_id=103), ValidationError, "linked"),
        (7, linked_outcome(visit_id=999), NotFoundError, "visit"),
    ],
)
def test_incoherent_buy_match_property_visit_combinations_are_rejected_without_writes(
    monkeypatch, request_id, data, error_type, error_message
):
    from buy import repository

    database = OutcomeDatabase()
    initial = copy.deepcopy(database.state)
    monkeypatch.setattr(repository, "core_cursor", database.cursor)

    with pytest.raises(error_type, match=error_message):
        repository.add_interaction(request_id, data)

    assert database.commits == 0
    assert database.rollbacks == 1
    assert database.state == initial


def test_property_id_is_derived_from_match_not_trusted_from_client(monkeypatch):
    from buy import repository

    database = OutcomeDatabase()
    monkeypatch.setattr(repository, "core_cursor", database.cursor)

    result = repository.add_interaction(7, linked_outcome(property_id=999))

    assert result["property_id"] == 21
    assert database.state["interactions"][-1]["property_id"] == 21


def test_failure_after_interaction_insert_rolls_back_interaction_match_and_history(monkeypatch):
    from buy import repository

    database = OutcomeDatabase(fail_after_interaction=True)
    initial = copy.deepcopy(database.state)
    monkeypatch.setattr(repository, "core_cursor", database.cursor)

    with pytest.raises(RuntimeError, match="match update failed"):
        repository.add_interaction(7, linked_outcome())

    assert database.commits == 0
    assert database.rollbacks == 1
    assert database.state == initial


def test_multiple_visits_on_same_match_are_distinguished_by_property_visit_id(monkeypatch):
    from buy import repository

    database = OutcomeDatabase()
    monkeypatch.setattr(repository, "core_cursor", database.cursor)

    repository.add_interaction(7, linked_outcome("visited", visit_id=101))
    result = repository.add_interaction(7, linked_outcome("interested", visit_id=102))

    assert result["property_visit_id"] == 102
    assert [item["property_visit_id"] for item in database.state["interactions"][-2:]] == [101, 102]


def test_interaction_without_property_visit_id_keeps_previous_behavior(monkeypatch):
    from buy import repository

    database = OutcomeDatabase()
    monkeypatch.setattr(repository, "core_cursor", database.cursor)

    result = repository.add_interaction(
        7,
        {
            "property_id": 21,
            "interaction_type": "other",
            "notes": "Nota non collegata a visita",
        },
    )

    assert result["property_id"] == 21
    assert "property_visit_id" not in result
    assert database.state["history"][-1]["event_type"] == "note"
    assert not any("property_visits" in query.lower() for query, _ in database.queries)


def test_linked_post_visit_interaction_satisfies_existing_flow_r007_semantics(monkeypatch):
    from buy import repository
    from flow.engine import evaluate

    database = OutcomeDatabase()
    monkeypatch.setattr(repository, "core_cursor", database.cursor)
    repository.add_interaction(7, linked_outcome("visited", visit_id=101))

    feedback_count = sum(
        item.get("property_visit_id") == 101 and item["interaction_type"] in POST_VISIT_ACTIONS
        for item in database.state["interactions"]
    )
    matched, _ = evaluate(
        "FLOW-R007",
        {
            "status": "completed",
            "updated_at": datetime(2000, 1, 1, tzinfo=timezone.utc),
            "feedback_count": feedback_count,
        },
        {"feedback_wait_hours": 24},
    )
    assert matched is False

    property_feedback_only, _ = evaluate(
        "FLOW-R007",
        {
            "status": "completed",
            "updated_at": datetime(2000, 1, 1, tzinfo=timezone.utc),
            "feedback_count": 0,
            "feedback": "testo PROPERTY",
            "outcome": "positivo",
            "rating": 5,
        },
        {"feedback_wait_hours": 24},
    )
    assert property_feedback_only is True


def test_p2_visit_scheduled_mapping_and_atomic_entrypoint_remain_unchanged():
    from buy import repository

    assert repository.MATCH_STATUS["visit_scheduled"] == "visit_scheduled"
    assert repository.HISTORY_EVENT["visit_scheduled"] == "visit_scheduled"
    assert callable(repository.schedule_match_visit)


def test_existing_buy_interaction_endpoint_remains_protected_by_next2_auth():
    from integration_p2_support import import_main_app

    app = import_main_app()
    operation = app.openapi()["paths"]["/api/buy/requests/{request_id}/interactions"]["post"]
    assert operation.get("security")


def node_prelude():
    return """
const element={
  value:'',hidden:false,innerHTML:'',textContent:'',disabled:false,
  classList:{toggle(){},add(){},remove(){}},
  addEventListener(){},reset(){},append(){},remove(){},
  querySelector(){return element},querySelectorAll(){return []}
};
global.document={
  querySelector(){return element},querySelectorAll(){return []},
  getElementById(){return element},createElement(){return Object.assign({},element)}
};
global.window={};
global.setTimeout=()=>0;
"""


def run_export(function_name, *arguments):
    script = (
        node_prelude()
        + f"const mod=require({json.dumps(str(JS_PATH))});"
        + f"const fn=mod[{json.dumps(function_name)}];"
        + "if(typeof fn!=='function')throw new Error('export missing');"
        + f"const result=fn(...{json.dumps(arguments)});"
        + "process.stdout.write(JSON.stringify(result));"
    )
    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        env={**os.environ, "TZ": "Europe/Rome"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def completed_visit(**overrides):
    visit = {
        "id": 101,
        "property_id": 21,
        "status": "completed",
        "buy_request_id": 7,
        "match_id": 11,
        "last_commercial_interaction_id": 401,
        "last_commercial_interaction_type": "interested",
    }
    visit.update(overrides)
    return visit


def test_frontend_builds_only_allowed_explicit_linked_outcomes_from_backend_ids():
    visit = completed_visit()
    for action in POST_VISIT_ACTIONS:
        values = {
            "action": action,
            "reason_code": "buyer_decision" if action == "discarded" else "",
            "notes": "Nota operatore",
            "buy_request_id": 999,
            "match_id": 998,
            "property_id": 997,
            "property_visit_id": 996,
        }
        request = run_export("buildVisitOutcomeRequest", visit, values)
        assert request == {
            "base": "/api/buy",
            "path": "/requests/7/interactions",
            "body": {
                "match_id": 11,
                "property_visit_id": 101,
                "interaction_type": action,
                "reason_code": "buyer_decision" if action == "discarded" else None,
                "notes": "Nota operatore",
                "created_by": "property-admin",
            },
        }


@pytest.mark.parametrize("action", ["proposed", "visit_requested", "visit_scheduled", "other"])
def test_frontend_rejects_actions_outside_post_visit_scope(action):
    script = (
        node_prelude()
        + f"const mod=require({json.dumps(str(JS_PATH))});"
        + "try{mod.buildVisitOutcomeRequest("
        + json.dumps(completed_visit())
        + ","
        + json.dumps({"action": action})
        + ");process.exitCode=2}catch(error){process.stdout.write(error.message)}"
    )
    result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout
    assert "Azione" in result.stdout


def test_frontend_requires_reason_for_discarded_and_positive_backend_ids():
    cases = [
        (completed_visit(), {"action": "discarded"}, "Motivo"),
        (completed_visit(id=0), {"action": "visited"}, "visita"),
        (completed_visit(buy_request_id="x"), {"action": "visited"}, "BUY"),
        (completed_visit(match_id=-1), {"action": "visited"}, "MATCH"),
    ]
    for visit, values, message in cases:
        script = (
            node_prelude()
            + f"const mod=require({json.dumps(str(JS_PATH))});"
            + f"try{{mod.buildVisitOutcomeRequest({json.dumps(visit)},{json.dumps(values)});process.exitCode=2}}"
            + "catch(error){process.stdout.write(error.message)}"
        )
        result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr or result.stdout
        assert message in result.stdout


def test_frontend_renders_safe_links_latest_outcome_and_contextual_action():
    markup = run_export(
        "renderVisitCommercialContext",
        completed_visit(last_commercial_interaction_type="<img src=x onerror=alert(1)>")
    )

    assert 'href="/buy-admin/?id=7"' in markup
    assert 'href="/match-admin/?id=11"' in markup
    assert 'target="_blank"' in markup
    assert 'rel="noopener noreferrer"' in markup
    assert "Registra esito BUY" in markup
    assert 'data-outcome-visit="101"' in markup
    assert "<img src=x onerror=alert(1)>" not in markup
    assert "&lt;img src=x onerror=alert(1)&gt;" in markup

    assert "Registra esito BUY" not in run_export(
        "renderVisitCommercialContext", completed_visit(status="scheduled")
    )
    assert "Registra esito BUY" not in run_export(
        "renderVisitCommercialContext", completed_visit(buy_request_id=None, match_id=None)
    )


def test_frontend_outcome_form_has_no_manual_relational_ids():
    markup = run_export("renderVisitOutcomeForm")
    action_select = re.search(r'<select id="voaction"[^>]*>(.*?)</select>', markup, re.DOTALL)
    assert action_select
    action_markup = action_select.group(1)

    for action in POST_VISIT_ACTIONS:
        assert f'value="{action}"' in action_markup
    for forbidden in ("proposed", "visit_requested", "visit_scheduled", "other"):
        assert f'value="{forbidden}"' not in action_markup
    assert "Note" in markup
    assert "Motivo" in markup
    assert not re.search(r'name="(?:buy_request_id|match_id|property_id|property_visit_id)"', markup)


def test_frontend_guards_double_submit_and_resets_guard_after_success_and_error():
    script = (
        node_prelude()
        + f"const mod=require({json.dumps(str(JS_PATH))});"
        + """
const visit={id:101,status:'completed',buy_request_id:7,match_id:11};
const values={action:'visited',reason_code:'',notes:''};
let release;
const gate=new Promise(resolve=>{release=resolve});
let calls=0;
const slow=async()=>{calls+=1;await gate;return {id:1}};
(async()=>{
  const first=mod.submitVisitOutcome(visit,values,slow);
  const duplicate=mod.submitVisitOutcome(visit,values,slow);
  await Promise.resolve();
  const during=calls;
  release();
  await Promise.all([first,duplicate]);
  await mod.submitVisitOutcome(visit,values,async()=>{calls+=1;return {id:2}});
  let failed=false;
  try{await mod.submitVisitOutcome(visit,values,async()=>{calls+=1;throw new Error('network')})}catch(_error){failed=true}
  await mod.submitVisitOutcome(visit,values,async()=>{calls+=1;return {id:3}});
  process.stdout.write(JSON.stringify({during,calls,failed}));
})().catch(error=>{console.error(error);process.exit(1)});
"""
    )
    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"during": 1, "calls": 4, "failed": True}


def test_property_visit_status_only_patch_preserves_instant_and_rome_display():
    original = "2026-08-29T08:00:00.000Z"
    local = run_export("visitDateTimeLocal", original)
    payload = run_export(
        "buildVisitPayload",
        {
            "scheduled_at": local,
            "status": "completed",
            "contact_id": 31,
            "lead_id": 41,
            "outcome": None,
            "rating": 4,
            "feedback": "Visita completata",
        },
        original,
    )

    assert local == "2026-08-29T10:00"
    assert "scheduled_at" not in payload
    assert "10:00" in run_export("dt", original)

    from property.schemas import VisitUpdate

    backend_patch = VisitUpdate(status="completed").model_dump(exclude_unset=True)
    assert backend_patch == {"status": "completed"}


def test_property_visit_intentional_time_change_sends_single_utc_conversion():
    original = "2026-08-29T08:00:00.000Z"
    payload = run_export(
        "buildVisitPayload",
        {
            "scheduled_at": "2026-08-29T11:30",
            "status": "completed",
            "contact_id": 31,
            "lead_id": 41,
            "outcome": None,
            "rating": None,
            "feedback": None,
        },
        original,
    )

    assert payload["scheduled_at"] == "2026-08-29T09:30:00.000Z"
    assert run_export("visitDateTimeLocal", payload["scheduled_at"]) == "2026-08-29T11:30"
    assert "11:30" in run_export("dt", payload["scheduled_at"])
