from __future__ import annotations

import ast
import inspect
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest

from flow import adapters, engine, repository, service
from flow.rules.registry import RULES, OWNER_RULES, ALL_RULES
from owner import repository as owner_repo
import integration_owner_request as bridge

OWNER_MAPPING = {
    "FLOW-R008": ("contact_request", "Contattare proprietario", "high", 4),
    "FLOW-R009": ("correction_request", "Verificare richiesta di correzione", "high", 24),
    "FLOW-R010": ("strategy_feedback", "Rivedere strategia con proprietario", "high", 24),
    "FLOW-R011": ("price_review", "Valutare revisione prezzo", "high", 24),
    "FLOW-R012": ("document_question", "Rispondere a richiesta documentale", "normal", 24),
}


def owner_entity(request_type="contact_request"):
    return {
        "entity_type": "owner_feedback",
        "entity_id": 101,
        "owner_request_type": request_type,
        "property_id": 34,
        "contact_id": 77,
        "linked_activity_id": 501,
    }


def test_registry_preserves_r001_r007_and_adds_five_owner_rules():
    assert list(RULES) == [f"FLOW-R00{i}" for i in range(1, 8)]
    assert set(OWNER_RULES) == set(OWNER_MAPPING)
    assert len(ALL_RULES) == 12
    assert not (set(RULES) & set(OWNER_RULES))
    for code, rule in OWNER_RULES.items():
        assert rule.event_type == "owner.request_submitted"
        assert rule.entity_type == "owner_feedback"
        assert rule.version == 1
        assert rule.action_type == "create_core_task"
        assert rule.idempotency_scope == "event"
        assert rule.default_parameters["cooldown_minutes"] == 0
    for rule in RULES.values():
        assert rule.idempotency_scope == "cooldown"


@pytest.mark.parametrize("code,data", OWNER_MAPPING.items())
def test_owner_rules_match_exact_request_type_and_build_task(code, data):
    request_type, title, priority, due_hours = data
    entity = owner_entity(request_type)
    entity["event_payload"] = {"owner_request_type": request_type}
    params = OWNER_RULES[code].validate_parameters({})
    matched, _ = engine.evaluate(code, entity, params)
    assert matched is True
    action = engine.build_action(code, entity, params)
    assert action == {
        "action_type": "create_core_task",
        "title": title,
        "description": "Richiesta ricevuta dal portale proprietario.",
        "priority": priority,
        "due_hours": due_hours,
        "contact_id": 77,
        "lead_id": None,
        "assigned_to": None,
    }


@pytest.mark.parametrize("request_type", ["general_message", "availability_update", "unknown_type"])
def test_non_candidate_owner_types_match_no_owner_rule(request_type):
    entity = owner_entity(request_type)
    entity["event_payload"] = {"owner_request_type": request_type}
    assert all(
        engine.evaluate(code, entity, rule.validate_parameters({}))[0] is False
        for code, rule in OWNER_RULES.items()
    )


def test_live_event_payload_takes_precedence_over_adapter_fallback():
    entity = owner_entity("general_message")
    entity["event_payload"] = {"owner_request_type": "contact_request"}
    matched, _ = engine.evaluate("FLOW-R008", entity, OWNER_RULES["FLOW-R008"].validate_parameters({}))
    assert matched is True


def test_simulation_without_live_payload_uses_owner_adapter_context(monkeypatch):
    captured = {}
    monkeypatch.setattr(service.repository, "get_rule_row", lambda code: {
        "parameters": dict(OWNER_RULES[code].default_parameters)
    })
    monkeypatch.setattr(service, "load_entity", lambda et, eid: owner_entity("contact_request"))
    monkeypatch.setattr(service.repository, "record_simulation", lambda code, et, eid, matched, reasons, action, requested_by=None, error=None: captured.update(
        matched=matched, action=action, error=error
    ) or {"execution_mode": "simulation", "status": "matched" if matched else "not_matched"})

    class Payload:
        entity_type = "owner_feedback"
        entity_id = 101
        requested_by = None
        def model_dump(self, exclude_unset=False):
            return {"entity_type": self.entity_type, "entity_id": self.entity_id, "requested_by": self.requested_by}

    result = service.simulate("FLOW-R008", Payload())
    assert result["execution_mode"] == "simulation"
    assert captured["matched"] is True
    assert captured["action"]["title"] == "Contattare proprietario"


def test_owner_adapter_whitelists_context_and_uses_canonical_account_contact(monkeypatch):
    class Cursor:
        def __init__(self): self.sql=[]; self.params=[]
        def execute(self, sql, params=None): self.sql.append(" ".join(str(sql).split())); self.params.append(params)
        def fetchone(self): return {"id":101,"feedback_type":"price_review","property_id":34,"linked_activity_id":501,"contact_id":77,"subject":"SECRET","message":"SECRET"}
    cursor=Cursor()
    @contextmanager
    def fake_cursor(commit=False):
        assert commit is False
        yield object(), cursor
    monkeypatch.setattr(adapters, "core_cursor", fake_cursor)
    entity=adapters.load_entity("owner_feedback",101)
    assert entity == {
        "entity_type":"owner_feedback","entity_id":101,"owner_request_type":"price_review",
        "property_id":34,"contact_id":77,"linked_activity_id":501,
    }
    sql=cursor.sql[0]
    assert "JOIN owner_accounts oa ON oa.id=f.owner_account_id" in sql
    assert "oa.contact_id" in sql
    assert "subject" not in sql and "message" not in sql


def test_process_saved_event_uses_existing_event_payload_and_same_event_id(monkeypatch):
    saved={"id":701,"event_type":"owner.request_submitted","entity_type":"owner_feedback","entity_id":101,"source_module":"owner","payload":{"owner_request_type":"contact_request","property_id":34,"contact_id":77,"linked_activity_id":501}}
    rows=[{"code":"FLOW-R008","is_active":True,"event_type":"owner.request_submitted","entity_type":"owner_feedback","parameters":dict(OWNER_RULES["FLOW-R008"].default_parameters)}]
    seen={}
    monkeypatch.setattr(service.repository,"get_event",lambda event_id: saved)
    monkeypatch.setattr(service.repository,"list_rules",lambda: rows)
    monkeypatch.setattr(service,"load_entity",lambda et,eid: owner_entity("general_message"))
    def fake_execute(code, entity, matched, reasons, action, requested_by=None, event_id=None, retry_of_execution_id=None):
        seen.update(code=code,entity=entity,matched=matched,action=action,event_id=event_id)
        return {"id":801,"status":"executed"}
    monkeypatch.setattr(service.repository,"execute_live",fake_execute)
    monkeypatch.setattr(service.repository,"update_event_status",lambda event_id,status,error_message=None:{**saved,"status":status,"error_message":error_message})
    result=service.process_saved_event(701)
    assert seen["event_id"] == 701
    assert seen["entity"]["event_payload"] == saved["payload"]
    assert seen["matched"] is True
    assert result["event"]["status"] == "processed"


def test_process_saved_event_does_not_insert_second_flow_event():
    src=inspect.getsource(service.process_saved_event)+inspect.getsource(service._process_saved_event)
    assert "add_event(" not in src
    assert "get_event(" in src


def test_process_saved_event_marks_failure(monkeypatch):
    saved={"id":701,"event_type":"owner.request_submitted","entity_type":"owner_feedback","entity_id":101,"payload":{}}
    statuses=[]
    monkeypatch.setattr(service.repository,"get_event",lambda event_id:saved)
    monkeypatch.setattr(service.repository,"list_rules",lambda:[{"code":"FLOW-R008","is_active":True,"event_type":"owner.request_submitted","entity_type":"owner_feedback","parameters":dict(OWNER_RULES["FLOW-R008"].default_parameters)}])
    monkeypatch.setattr(service,"load_entity",lambda *args: (_ for _ in ()).throw(RuntimeError("adapter failed")))
    monkeypatch.setattr(service.repository,"update_event_status",lambda event_id,status,error_message=None: statuses.append((event_id,status,error_message)) or {"id":event_id,"status":status})
    with pytest.raises(RuntimeError): service.process_saved_event(701)
    assert statuses[-1][0:2] == (701,"failed")


def test_bridge_swallows_post_commit_dispatch_failure(monkeypatch):
    monkeypatch.setattr(bridge,"_process_saved_event",lambda event_id: (_ for _ in ()).throw(RuntimeError("dispatch failed")))
    assert bridge.process_saved_owner_request_event(701) is None


def test_owner_dispatch_is_after_transaction_and_privacy_boundary(monkeypatch):
    src=inspect.getsource(owner_repo.create_feedback)
    assert src.index("with core_cursor(commit=True)") < src.index("record_owner_request_event_with_cursor") < src.index("_audit_with_cursor")
    assert src.index("_audit_with_cursor") < src.index("process_saved_owner_request_event")
    owner_dir=Path(owner_repo.__file__).resolve().parent
    violations=[]
    for path in owner_dir.rglob("*.py"):
        tree=ast.parse(path.read_text(),filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node,ast.Import):
                violations.extend((path.name,node.lineno,a.name) for a in node.names if a.name.split('.',1)[0] in {"flow","buy","match"})
            elif isinstance(node,ast.ImportFrom):
                mod=node.module or ""
                if mod.split('.',1)[0] in {"flow","buy","match"}: violations.append((path.name,node.lineno,mod))
    assert violations == []


def test_event_bound_key_for_owner_and_legacy_key_for_r001(monkeypatch):
    captured=[]
    rows={
        "FLOW-R008":{"id":8,"is_active":True,"parameters":dict(OWNER_RULES["FLOW-R008"].default_parameters)},
        "FLOW-R001":{"id":1,"is_active":True,"parameters":dict(RULES["FLOW-R001"].default_parameters)},
    }
    monkeypatch.setattr(repository,"get_rule_row",lambda code:rows[code])
    monkeypatch.setattr(repository,"is_suppressed",lambda *args:False)
    monkeypatch.setattr(repository,"_idempotency_key",lambda *args:"LEGACY-KEY")
    class Cursor:
        def __init__(self): self.current=None
        def execute(self,sql,params=None):
            q=" ".join(str(sql).split())
            if "INSERT INTO flow_executions" in q: self.current={"id":900}
            elif "INSERT INTO flow_action_records" in q: captured.append(params[2]); self.current={"id":901}
            elif "UPDATE flow_action_records" in q: self.current=None
            elif "UPDATE flow_executions" in q: self.current={"id":900,"status":"executed"}
        def fetchone(self): return self.current
    @contextmanager
    def fake_cursor(commit=False): yield object(),Cursor()
    monkeypatch.setattr(repository,"core_cursor",fake_cursor)
    monkeypatch.setattr(repository.core_repository,"create_task",lambda data:{"id":1})
    action={"action_type":"create_core_task","title":"x","description":"x","priority":"high","due_hours":4,"contact_id":77,"lead_id":None,"assigned_to":None}
    repository.execute_live("FLOW-R008",owner_entity(),True,[],action,event_id=701)
    legacy_entity={"entity_type":"lead","entity_id":3,"id":3}
    repository.execute_live("FLOW-R001",legacy_entity,True,[],{**action,"contact_id":None,"lead_id":3},event_id=702)
    assert captured == ["FLOW-R008:event:701","LEGACY-KEY"]


def test_retry_preserves_event_id(monkeypatch):
    captured={}
    monkeypatch.setattr(service.repository,"increment_retry",lambda execution_id:{"id":execution_id})
    monkeypatch.setattr(service.repository,"get_execution",lambda execution_id:{"rule_code":"FLOW-R008","entity_type":"owner_feedback","entity_id":101,"event_id":701})
    monkeypatch.setattr(service,"load_entity",lambda et,eid:owner_entity("contact_request"))
    monkeypatch.setattr(service.repository,"get_rule_row",lambda code:{"parameters":dict(OWNER_RULES[code].default_parameters)})
    def fake_execute(*args,**kwargs): captured.update(kwargs); return {"status":"skipped"}
    monkeypatch.setattr(service.repository,"execute_live",fake_execute)
    class Payload:
        requested_by=None
        def model_dump(self,exclude_unset=False): return {"requested_by":None}
    service.retry(900,Payload())
    assert captured["event_id"] == 701
    assert captured["retry_of_execution_id"] == 900


def test_same_owner_rule_and_event_has_stable_key_by_source_contract():
    src=inspect.getsource(repository.execute_live)
    assert "rule.idempotency_scope=='event' and event_id is not None" in src
    assert 'f"{code}:event:{event_id}"' in src
    assert "_idempotency_key" in src


def test_sync_source_inserts_new_rules_inactive_never_run():
    src=inspect.getsource(repository.sync_rules)
    assert "ALL_RULES.items()" in src
    assert "FALSE" in src
    assert "'never_run'" in src


def test_no_owner_rule_for_general_message_or_availability_update():
    source=Path(OWNER_RULES["FLOW-R008"].__class__.__module__.replace('.', '/'))
    values={v[0] for v in OWNER_MAPPING.values()}
    assert "general_message" not in values
    assert "availability_update" not in values


def test_p8_2_and_p5_submit_contract_remain_separate():
    src=inspect.getsource(owner_repo.create_feedback)
    assert "create_activity_with_cursor" in src
    assert "_emit_notification_event" not in src
    assert "create_task" not in src
    handled=inspect.getsource(owner_repo.update_feedback_status)
    assert "request_handled" in handled
    assert "_emit_notification_event" in handled


def test_public_owner_feedback_dto_still_hides_internals():
    forbidden={"id","linked_activity_id","activity_id","contact_id","lead_id","stima_id","flow_event_id","deduplication_key","source_module","payload","rule","action","task"}
    assert forbidden.isdisjoint(owner_repo.FEEDBACK_PUBLIC_FIELDS)
