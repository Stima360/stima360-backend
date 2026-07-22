from __future__ import annotations
from . import repository
from .adapters import load_entity, scan_candidates
from .engine import evaluate as evaluate_rule, build_action
from .rules import get_rule


def dump(model, exclude_unset=False):
    if hasattr(model, "model_dump"): return model.model_dump(exclude_unset=exclude_unset)
    return model.dict(exclude_unset=exclude_unset)


def sync_rules(): return repository.sync_rules()
def list_rules(): return repository.list_rules()
def get_rule_row(code): return repository.get_rule_row(code)
def update_parameters(code,payload): return repository.update_parameters(code,dump(payload)["parameters"],dump(payload).get("updated_by"))
def reset_parameters(code): return repository.reset_parameters(code)
def activate(code,payload): return repository.activate(code,dump(payload).get("activated_by"))
def deactivate(code): return repository.deactivate(code)
def create_event(payload): return repository.add_event(dump(payload))
def list_events(*args): return repository.list_events(*args)
def list_executions(*args): return repository.list_executions(*args)
def get_execution(i): return repository.get_execution(i)
def dashboard(): return repository.dashboard()
def add_suppression(payload): return repository.add_suppression(dump(payload))
def list_suppressions(): return repository.list_suppressions()
def delete_suppression(i): return repository.delete_suppression(i)


def simulate(code, payload):
    data=dump(payload); row=repository.get_rule_row(code); rule=get_rule(code)
    if data["entity_type"] != rule.entity_type: raise ValueError(f"rule {code} requires entity_type {rule.entity_type}")
    try:
        entity=load_entity(data["entity_type"],data["entity_id"])
        parameters=rule.validate_parameters(dict(row["parameters"]))
        matched,reasons=evaluate_rule(code,entity,parameters)
        action=build_action(code,entity,parameters) if matched else None
        return repository.record_simulation(code,data["entity_type"],data["entity_id"],matched,reasons,action,data.get("requested_by"))
    except Exception as exc:
        repository.record_simulation(code,data["entity_type"],data["entity_id"],False,[],None,data.get("requested_by"),str(exc))
        raise


def evaluate(payload):
    data=dump(payload); code=data["rule_code"]; row=repository.get_rule_row(code); rule=get_rule(code)
    if data["entity_type"] != rule.entity_type: raise ValueError(f"rule {code} requires entity_type {rule.entity_type}")
    entity=load_entity(data["entity_type"],data["entity_id"]); p=rule.validate_parameters(dict(row["parameters"])); matched,reasons=evaluate_rule(code,entity,p); action=build_action(code,entity,p) if matched else None
    if data["mode"]=="simulation": return repository.record_simulation(code,data["entity_type"],data["entity_id"],matched,reasons,action,data.get("requested_by"))
    return repository.execute_live(code,entity,matched,reasons,action,data.get("requested_by"))


def process_event(event):
    data=dump(event); saved=repository.add_event(data); results=[]
    for row in repository.list_rules():
        if row["is_active"] and row["event_type"]==data["event_type"] and row["entity_type"]==data["entity_type"]:
            rule=get_rule(row["code"]); entity=load_entity(data["entity_type"],data["entity_id"]); p=rule.validate_parameters(dict(row["parameters"])); matched,reasons=evaluate_rule(row["code"],entity,p); action=build_action(row["code"],entity,p) if matched else None
            results.append(repository.execute_live(row["code"],entity,matched,reasons,action,event_id=saved["id"]))
    repository.update_event_status(saved['id'], 'processed' if results else 'ignored')
    return {"event":repository.update_event_status(saved['id'], 'processed' if results else 'ignored'),"executions":results}


def scan(payload):
    data=dump(payload); codes=data.get("rule_codes") or [r["code"] for r in repository.list_rules() if r["is_active"] or data.get("simulation")]
    results=[]; remaining=data["limit"]
    for code in codes:
        if remaining<=0: break
        row=repository.get_rule_row(code); rule=get_rule(code); p=rule.validate_parameters(dict(row["parameters"])); candidates=scan_candidates(code,p,remaining)
        for entity_type,entity_id in candidates:
            entity=load_entity(entity_type,entity_id); matched,reasons=evaluate_rule(code,entity,p); action=build_action(code,entity,p) if matched else None
            if data["simulation"]: ex=repository.record_simulation(code,entity_type,entity_id,matched,reasons,action,data.get("requested_by"))
            else: ex=repository.execute_live(code,entity,matched,reasons,action,data.get("requested_by"))
            results.append(ex); remaining-=1
            if remaining<=0: break
    return {"requested_limit":data["limit"],"processed":len(results),"simulation":data["simulation"],"items":results}


def retry(execution_id,payload):
    original=repository.increment_retry(execution_id); ex=repository.get_execution(execution_id); code=ex["rule_code"]
    entity=load_entity(ex["entity_type"],ex["entity_id"]); row=repository.get_rule_row(code); rule=get_rule(code); p=rule.validate_parameters(dict(row["parameters"])); matched,reasons=evaluate_rule(code,entity,p); action=build_action(code,entity,p) if matched else None
    return repository.execute_live(code,entity,matched,reasons,action,dump(payload).get("requested_by"),retry_of_execution_id=execution_id)
