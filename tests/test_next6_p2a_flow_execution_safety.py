from __future__ import annotations

import copy
import json
import subprocess
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from core import repository as core_repository
from flow import repository as flow_repository
from flow import service as flow_service
from integration_p2_support import import_main_app


ROOT = Path(__file__).resolve().parents[1]
FLOW_HTML = ROOT / "static/flow_admin/index.html"
FLOW_JS = ROOT / "static/flow_admin/assets/app.js"


def _json_value(value):
    return copy.deepcopy(getattr(value, "adapted", value))


def _rule_row(code="FLOW-R001"):
    parameters = {
        "inactivity_hours": 24,
        "task_priority": "high",
        "cooldown_minutes": 1440,
    }
    if code == "FLOW-R004":
        parameters = {
            "overdue_hours": 0,
            "task_priority": "high",
            "cooldown_minutes": 1440,
        }
    return {
        "id": 1 if code == "FLOW-R001" else 4,
        "code": code,
        "is_active": True,
        "parameters": parameters,
        "event_type": "core.lead_created" if code == "FLOW-R001" else "buy.next_action_due",
        "entity_type": "lead" if code == "FLOW-R001" else "buy_request",
    }


def _entity(entity_id=10):
    return {
        "id": entity_id,
        "entity_type": "lead",
        "entity_id": entity_id,
        "status": "open",
        "activity_count": 0,
        "open_task_count": 0,
    }


def _action():
    return {
        "action_type": "create_core_task",
        "title": "Primo contatto lead",
        "description": "Follow-up FLOW",
        "priority": "high",
        "due_hours": 4,
        "contact_id": None,
        "lead_id": 10,
        "assigned_to": None,
    }


class FlowCursor:
    def __init__(self, state, *, fail_finalize=False):
        self.state = state
        self.fail_finalize = fail_finalize
        self.current = None
        self.rowcount = -1
        self.savepoint = None
        self.sql = []

    def _execution(self, execution_id):
        return next(item for item in self.state["executions"] if item["id"] == execution_id)

    def execute(self, query, params=()):
        normalized = " ".join(str(query).split())
        lowered = normalized.lower()
        self.sql.append(normalized)
        self.current = None
        self.rowcount = -1
        params = params or ()

        if lowered.startswith("select * from flow_rules"):
            self.current = copy.deepcopy(_rule_row(params[0]))
            return
        if "select 1 from flow_suppressions" in lowered:
            return
        if lowered.startswith("insert into flow_executions"):
            execution = {
                "id": len(self.state["executions"]) + 1,
                "status": params[4],
                "entity_type": params[2],
                "entity_id": params[3],
                "actions_result": {},
                "error_message": None,
            }
            self.state["executions"].append(execution)
            self.current = copy.deepcopy(execution)
            return
        if lowered.startswith("savepoint "):
            self.savepoint = copy.deepcopy(self.state)
            return
        if lowered.startswith("rollback to savepoint "):
            assert self.savepoint is not None
            self.state.clear()
            self.state.update(copy.deepcopy(self.savepoint))
            return
        if lowered.startswith("release savepoint "):
            self.savepoint = None
            return
        if lowered.startswith("insert into flow_action_records"):
            idempotency_key = params[2]
            existing = next(
                (item for item in self.state["actions"] if item["idempotency_key"] == idempotency_key),
                None,
            )
            if existing:
                return
            action = {
                "id": len(self.state["actions"]) + 1,
                "execution_id": params[0],
                "action_type": params[1],
                "idempotency_key": idempotency_key,
                "status": "failed" if "'failed'" in lowered else "pending",
                "target_entity_id": None,
            }
            self.state["actions"].append(action)
            self.current = copy.deepcopy(action)
            return
        if lowered.startswith("select * from flow_action_records"):
            self.current = copy.deepcopy(
                next(
                    (item for item in self.state["actions"] if item["idempotency_key"] == params[0]),
                    None,
                )
            )
            return
        if "select id from tasks where metadata->>'idempotency_key'" in lowered:
            task = next(
                (item for item in self.state["tasks"] if item["metadata"].get("idempotency_key") == params[0]),
                None,
            )
            self.current = {"id": task["id"]} if task else None
            return
        if lowered.startswith("select 1 from leads"):
            self.current = {"exists": 1}
            return
        if lowered.startswith("select 1 from contacts") or lowered.startswith("select 1 from stime"):
            self.current = {"exists": 1}
            return
        if lowered.startswith("insert into tasks"):
            task = {
                **params,
                "id": len(self.state["tasks"]) + 100,
                "metadata": _json_value(params["metadata"]),
            }
            self.state["tasks"].append(task)
            self.current = copy.deepcopy(task)
            return
        if lowered.startswith("update flow_action_records set execution_id") and "target_entity_type='task'" in lowered:
            if self.fail_finalize:
                self.fail_finalize = False
                raise RuntimeError("FLOW finalize failed")
            action = next(item for item in self.state["actions"] if item["id"] == params[2])
            action.update(
                {
                    "execution_id": params[0],
                    "target_entity_id": params[1],
                    "status": "completed",
                }
            )
            self.rowcount = 1
            return
        if lowered.startswith("update flow_action_records"):
            action = next(
                (item for item in self.state["actions"] if item["idempotency_key"] == params[2]),
                None,
            )
            if action and action["status"] != "completed":
                action.update({"execution_id": params[0], "status": "failed"})
                self.rowcount = 1
            else:
                self.rowcount = 0
            return
        if lowered.startswith("update flow_executions"):
            execution_id = params[-1]
            execution = self._execution(execution_id)
            if "status='executed'" in lowered:
                execution.update(status="executed", actions_result=_json_value(params[0]), error_message=None)
            elif "status='skipped'" in lowered:
                execution.update(status="skipped", actions_result=_json_value(params[0]))
            elif "status='not_matched'" in lowered:
                execution.update(status="not_matched")
            elif "status='failed'" in lowered:
                execution.update(status="failed", error_message=params[0])
            self.current = copy.deepcopy(execution)
            return
        raise AssertionError(f"SQL non gestito dal test: {normalized}")

    def fetchone(self):
        return self.current


class FlowDatabase:
    def __init__(self, *, fail_finalize=False):
        self.state = {"executions": [], "actions": [], "tasks": []}
        self.fail_finalize = fail_finalize
        self.calls = []
        self.commits = 0
        self.rollbacks = 0
        self.cursors = []

    @contextmanager
    def cursor(self, commit=False):
        self.calls.append(commit)
        staged = copy.deepcopy(self.state)
        cursor = FlowCursor(staged, fail_finalize=self.fail_finalize)
        self.cursors.append(cursor)
        try:
            yield object(), cursor
        except Exception:
            self.rollbacks += 1
            raise
        else:
            if commit:
                self.state = staged
                self.commits += 1


def _run_live(monkeypatch, database, public_create_task):
    monkeypatch.setattr(flow_repository, "core_cursor", database.cursor)
    monkeypatch.setattr(flow_repository, "get_rule_row", lambda code: _rule_row(code))
    monkeypatch.setattr(flow_repository, "is_suppressed", lambda *args: False)
    monkeypatch.setattr(core_repository, "create_task", public_create_task)
    return flow_repository.execute_live(
        "FLOW-R001",
        _entity(),
        True,
        ["lead aperto senza attività/task"],
        _action(),
        requested_by="test",
    )


def test_core_task_cursor_helper_does_not_open_a_transaction(monkeypatch):
    helper = getattr(core_repository, "create_task_with_cursor", None)
    assert helper is not None, "create_task_with_cursor mancante"
    database = FlowDatabase()
    cursor = FlowCursor(database.state)

    def forbidden_cursor(*args, **kwargs):
        raise AssertionError("l'helper cursor-aware non deve aprire core_cursor")

    monkeypatch.setattr(core_repository, "core_cursor", forbidden_cursor)
    result = helper(
        cursor,
        {
            "contact_id": None,
            "lead_id": 10,
            "stima_id": None,
            "title": "Task",
            "description": "Descrizione",
            "task_type": "flow_follow_up",
            "priority": "high",
            "status": "open",
            "due_at": None,
            "completed_at": None,
            "assigned_to": None,
            "created_by": "FLOW",
            "metadata": {"source": "flow"},
        },
    )
    assert result["id"] == 100
    assert database.state["tasks"][0]["metadata"] == {"source": "flow"}


def test_execute_live_success_persists_execution_action_and_task_in_one_transaction(monkeypatch):
    database = FlowDatabase()

    def forbidden_public_create_task(data):
        raise RuntimeError("FLOW non deve usare create_task con transazione autonoma")

    result = _run_live(monkeypatch, database, forbidden_public_create_task)

    assert result["status"] == "executed"
    assert database.calls == [True]
    assert database.commits == 1
    assert database.rollbacks == 0
    assert [item["status"] for item in database.state["executions"]] == ["executed"]
    assert [item["status"] for item in database.state["actions"]] == ["completed"]
    assert len(database.state["tasks"]) == 1


def test_execute_live_finalize_error_rolls_back_task_and_action_but_records_failed_execution(monkeypatch):
    database = FlowDatabase(fail_finalize=True)

    def independently_committed_task(data):
        task = {**data, "id": 999, "metadata": copy.deepcopy(data["metadata"])}
        database.state["tasks"].append(task)
        return task

    result = _run_live(monkeypatch, database, independently_committed_task)

    assert result["status"] == "failed"
    assert result["error_message"] == "FLOW finalize failed"
    assert database.calls == [True]
    assert database.commits == 1
    assert database.state["tasks"] == []
    assert database.state["actions"] == []
    assert [item["status"] for item in database.state["executions"]] == ["failed"]


def test_execute_live_does_not_sync_rules_in_the_hot_path(monkeypatch):
    database = FlowDatabase()
    monkeypatch.setattr(flow_repository, "core_cursor", database.cursor)
    monkeypatch.setattr(
        flow_repository,
        "sync_rules",
        lambda: (_ for _ in ()).throw(AssertionError("sync_rules nel percorso execute_live")),
    )
    monkeypatch.setattr(core_repository, "create_task", lambda data: (_ for _ in ()).throw(AssertionError("public create_task")))

    result = flow_repository.execute_live("FLOW-R001", _entity(), True, ["matched"], _action())

    assert result["status"] == "executed"
    assert database.calls == [True]


def test_execute_live_completed_dedupe_returns_skipped_without_new_task(monkeypatch):
    database = FlowDatabase()
    database.state["actions"].append(
        {
            "id": 77,
            "execution_id": 70,
            "action_type": "create_core_task",
            "idempotency_key": "fixed-key",
            "status": "completed",
            "target_entity_id": 555,
        }
    )
    monkeypatch.setattr(flow_repository, "_idempotency_key", lambda *args: "fixed-key")

    result = _run_live(
        monkeypatch,
        database,
        lambda data: (_ for _ in ()).throw(AssertionError("duplicate must not create task")),
    )

    assert result["status"] == "skipped"
    assert _json_value(result["actions_result"]) == {
        "reason": "duplicate_or_cooldown",
        "task_id": 555,
    }
    assert database.state["tasks"] == []
    assert len(database.state["actions"]) == 1


def _scan_payload(*, rule_codes=None, limit=50, simulation=True):
    data = {
        "rule_codes": rule_codes,
        "limit": limit,
        "simulation": simulation,
        "requested_by": "test",
    }
    return SimpleNamespace(dict=lambda exclude_unset=False: copy.deepcopy(data))


def _patch_scan_basics(monkeypatch, *, candidates, load=None, evaluate=None, execute=None):
    rows = [_rule_row("FLOW-R001"), _rule_row("FLOW-R004")]
    sync = Mock(return_value=rows)
    monkeypatch.setattr(flow_service.repository, "sync_rules", sync)
    monkeypatch.setattr(flow_service.repository, "list_rules", Mock(return_value=rows))
    monkeypatch.setattr(
        flow_service.repository,
        "get_rule_row",
        Mock(side_effect=lambda code, **kwargs: copy.deepcopy(next(row for row in rows if row["code"] == code))),
    )
    monkeypatch.setattr(flow_service, "scan_candidates", Mock(side_effect=lambda code, parameters, limit: list(candidates(code))[:limit]))
    monkeypatch.setattr(
        flow_service,
        "load_entity",
        Mock(side_effect=load or (lambda entity_type, entity_id: {"id": entity_id, "entity_type": entity_type, "entity_id": entity_id})),
    )
    monkeypatch.setattr(flow_service, "evaluate_rule", Mock(side_effect=evaluate or (lambda code, entity, parameters: (True, ["matched"]))))
    monkeypatch.setattr(flow_service, "build_action", Mock(return_value={"action_type": "create_core_task"}))
    recorder = Mock(
        side_effect=execute
        or (
            lambda code, entity_type, entity_id, matched, reasons, action, requested_by: {
                "status": "matched" if matched else "not_matched",
                "rule_code": code,
                "entity_type": entity_type,
                "entity_id": entity_id,
            }
        )
    )
    monkeypatch.setattr(flow_service.repository, "record_simulation", recorder)
    failures = Mock(
        side_effect=lambda code, entity_type, entity_id, mode, error, requested_by=None: {
            "status": "failed",
            "rule_code": code,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "error_message": error,
        }
    )
    monkeypatch.setattr(flow_service.repository, "record_failure", failures, raising=False)
    return sync, recorder, failures


def test_scan_round_robin_limit_prevents_first_rule_monopoly_and_syncs_once(monkeypatch):
    sync, recorder, _ = _patch_scan_basics(
        monkeypatch,
        candidates=lambda code: (
            [("lead", 101), ("lead", 102), ("lead", 103)]
            if code == "FLOW-R001"
            else [("buy_request", 201)]
        ),
    )

    result = flow_service.scan(
        _scan_payload(rule_codes=["FLOW-R001", "FLOW-R004"], limit=2)
    )

    assert [item["entity_id"] for item in result["items"]] == [101, 201]
    assert result["processed"] == 2
    assert result["successes"] == 2
    assert result["failures"] == 0
    assert result["skips"] == 0
    assert result["status"] == "completed"
    sync.assert_called_once_with()
    assert recorder.call_count == 2


def test_candidate_error_is_recorded_and_later_candidates_same_rule_continue(monkeypatch):
    def load(entity_type, entity_id):
        if entity_id == 102:
            raise RuntimeError("candidate load failed")
        return {"id": entity_id, "entity_type": entity_type, "entity_id": entity_id}

    _, recorder, failures = _patch_scan_basics(
        monkeypatch,
        candidates=lambda code: [("lead", 101), ("lead", 102), ("lead", 103)] if code == "FLOW-R001" else [],
        load=load,
    )

    result = flow_service.scan(_scan_payload(rule_codes=["FLOW-R001"], limit=3))

    assert result["processed"] == 3
    assert result["successes"] == 2
    assert result["failures"] == 1
    assert result["status"] == "partial_failure"
    assert [call.args[2] for call in recorder.call_args_list] == [101, 103]
    failures.assert_called_once()
    failed = next(item for item in result["items"] if item["status"] == "failed")
    assert failed["entity_id"] == 102
    assert failed["stage"] == "load"
    assert failed["error_message"] == "candidate load failed"


@pytest.mark.parametrize("failing_stage", ["adapter", "load", "evaluate", "execute"])
def test_rule_failure_does_not_prevent_next_requested_rule(monkeypatch, failing_stage):
    def candidates(code):
        if code == "FLOW-R001" and failing_stage == "adapter":
            raise RuntimeError("adapter failed")
        return [("lead", 101)] if code == "FLOW-R001" else [("buy_request", 201)]

    def load(entity_type, entity_id):
        if entity_id == 101 and failing_stage == "load":
            raise RuntimeError("load failed")
        return {"id": entity_id, "entity_type": entity_type, "entity_id": entity_id}

    def evaluate(code, entity, parameters):
        if code == "FLOW-R001" and failing_stage == "evaluate":
            raise RuntimeError("evaluate failed")
        return True, ["matched"]

    def execute(code, entity_type, entity_id, matched, reasons, action, requested_by):
        if code == "FLOW-R001" and failing_stage == "execute":
            raise RuntimeError("execute failed")
        return {
            "status": "matched",
            "rule_code": code,
            "entity_type": entity_type,
            "entity_id": entity_id,
        }

    _, recorder, _ = _patch_scan_basics(
        monkeypatch,
        candidates=candidates,
        load=load,
        evaluate=evaluate,
        execute=execute,
    )

    result = flow_service.scan(
        _scan_payload(rule_codes=["FLOW-R001", "FLOW-R004"], limit=4)
    )

    assert any(item.get("rule_code") == "FLOW-R004" for item in result["items"])
    assert result["failures"] == 1
    assert result["status"] == "partial_failure"
    assert any(item.get("stage") == failing_stage for item in result["items"])
    assert any(call.args[0] == "FLOW-R004" for call in recorder.call_args_list)


class FrozenDatetime:
    value = datetime(2030, 1, 1, 12, 10, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls.value


def test_bucket_dedupe_same_entity_same_bucket_has_same_key(monkeypatch):
    monkeypatch.setattr(flow_repository, "datetime", FrozenDatetime)
    FrozenDatetime.value = datetime(2030, 1, 1, 12, 10, tzinfo=timezone.utc)
    first = flow_repository._idempotency_key("FLOW-R001", "lead", 10, 60)
    FrozenDatetime.value = datetime(2030, 1, 1, 12, 59, tzinfo=timezone.utc)
    second = flow_repository._idempotency_key("FLOW-R001", "lead", 10, 60)
    assert first == second


def test_bucket_dedupe_can_change_inside_cooldown_across_boundary(monkeypatch):
    monkeypatch.setattr(flow_repository, "datetime", FrozenDatetime)
    FrozenDatetime.value = datetime(2030, 1, 1, 12, 59, 59, 900000, tzinfo=timezone.utc)
    before = flow_repository._idempotency_key("FLOW-R001", "lead", 10, 60)
    FrozenDatetime.value = datetime(2030, 1, 1, 13, 0, 0, 100000, tzinfo=timezone.utc)
    after = flow_repository._idempotency_key("FLOW-R001", "lead", 10, 60)
    assert before != after


def test_bucket_size_follows_cooldown_minutes(monkeypatch):
    monkeypatch.setattr(flow_repository, "datetime", FrozenDatetime)
    FrozenDatetime.value = datetime(2030, 1, 1, 12, 59, tzinfo=timezone.utc)
    before = flow_repository._idempotency_key("FLOW-R001", "lead", 10, 120)
    FrozenDatetime.value = datetime(2030, 1, 1, 13, 1, tzinfo=timezone.utc)
    after = flow_repository._idempotency_key("FLOW-R001", "lead", 10, 120)
    assert before == after


def test_flow_admin_has_explicit_login_and_memory_only_credentials():
    html = FLOW_HTML.read_text(encoding="utf-8")
    js = FLOW_JS.read_text(encoding="utf-8")
    for marker in (
        'id="login-view"',
        'id="login-form"',
        'id="admin-username"',
        'id="admin-password"',
        'type="password"',
        'id="app-view"',
        'id="logout-btn"',
    ):
        assert marker in html
    assert "hidden" in html
    assert "/api/admin/check" in js
    assert "encodeBasic" in js
    assert "Authorization" in js
    assert "status===401" in js or "status === 401" in js
    assert "logout" in js
    assert "localStorage" not in js
    assert "sessionStorage" not in js
    assert "document.cookie" not in js
    assert "indexedDB" not in js


def test_flow_admin_runtime_adds_basic_auth_and_401_returns_to_login():
    source = FLOW_JS.read_text(encoding="utf-8")
    script = f"""
const vm=require('vm');
const {{TextEncoder}}=require('util');
function element(){{return {{hidden:false,value:'',textContent:'',innerHTML:'',style:{{}},onclick:null,onsubmit:null,addEventListener(){{}},reset(){{this.resetCalled=true}},showModal(){{}},close(){{}},querySelector(){{return element()}}}}}}
const nodes=new Map();
for(const id of ['login-view','app-view','login-form','admin-username','admin-password','login-status','logout-btn','sync','kpi','content','simModal','simForm','simCancel','paramsModal','paramsForm','paramsCancel','paramFields','toast'])nodes.set('#'+id,element());
nodes.get('#admin-username').value='admin';
nodes.get('#admin-password').value='secret';
const document={{querySelector(selector){{if(!nodes.has(selector))nodes.set(selector,element());return nodes.get(selector)}},querySelectorAll(){{return []}}}};
const calls=[];
let unauthorized=false;
async function fetch(url,options={{}}){{
  calls.push({{url,options}});
  if(url==='/api/admin/check')return {{ok:true,status:200,json:async()=>({{ok:true}}),text:async()=>''}};
  if(unauthorized)return {{ok:false,status:401,json:async()=>({{detail:'Non autorizzato'}}),text:async()=>'Non autorizzato'}};
  if(url.endsWith('/rules'))return {{ok:true,status:200,json:async()=>({{items:[]}}),text:async()=>''}};
  return {{ok:true,status:200,json:async()=>({{active_rules:0,total_events:0,executed:0,failed:0,skipped:0,tasks_created:0,active_suppressions:0}}),text:async()=>''}};
}}
const context={{document,fetch,TextEncoder,btoa:value=>Buffer.from(value,'binary').toString('base64'),setTimeout(){{}},console}};
context.window=context;
vm.createContext(context);
vm.runInContext({json.dumps(source + ";globalThis.__flowTest={login,req,hasCredentials:()=>credentials!==null};")},context);
(async()=>{{
  if(calls.length!==0)throw new Error('FLOW API called before login');
  await context.__flowTest.login({{preventDefault(){{}}}});
  const flowCalls=calls.filter(call=>call.url.startsWith('/api/flow'));
  if(flowCalls.length!==2)throw new Error('expected dashboard and rules after login');
  if(flowCalls.some(call=>!String(call.options.headers.Authorization||'').startsWith('Basic ')))throw new Error('missing Basic authorization');
  unauthorized=true;
  try{{await context.__flowTest.req('/api/flow/dashboard')}}catch(error){{}}
  if(context.__flowTest.hasCredentials())throw new Error('credentials retained after 401');
  if(nodes.get('#login-view').hidden!==false||nodes.get('#app-view').hidden!==true)throw new Error('401 did not restore login');
}})().catch(error=>{{console.error(error);process.exitCode=1}});
"""
    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


@pytest.fixture(scope="module")
def app():
    return import_main_app()


@pytest.fixture(scope="module")
def client(app):
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    "method,path,payload",
    (
        ("POST", "/api/flow/scan", {"simulation": True}),
        ("POST", "/api/flow/rules/FLOW-R001/simulate", {"entity_type": "lead", "entity_id": 1}),
        ("POST", "/api/flow/rules/FLOW-R001/activate", {}),
        ("POST", "/api/flow/rules/FLOW-R001/deactivate", None),
        ("GET", "/api/flow/executions", None),
        ("GET", "/api/flow/suppressions", None),
        ("POST", "/api/flow/executions/1/retry", {}),
        ("POST", "/api/flow/evaluate", {"rule_code": "FLOW-R001", "entity_type": "lead", "entity_id": 1, "mode": "live"}),
        ("POST", "/api/flow/events", {"event_type": "core.lead_created", "entity_type": "lead", "entity_id": 1, "source_module": "core"}),
    ),
)
def test_every_sensitive_flow_endpoint_remains_private(client, monkeypatch, method, path, payload):
    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.setenv("ADMIN_PASS", "secret")
    response = client.request(method, path, json=payload)
    assert response.status_code == 401
    assert response.json() == {"detail": "Non autorizzato"}


def test_flow_router_keeps_router_wide_owner_admin_auth(app):
    operations = []
    for path, item in app.openapi()["paths"].items():
        if not path.startswith("/api/flow"):
            continue
        for method, operation in item.items():
            if method.lower() in {"get", "post", "put", "patch", "delete"}:
                operations.append((method, path, operation))
    assert operations
    assert all(operation.get("security") for _, _, operation in operations)
