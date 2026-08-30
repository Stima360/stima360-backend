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


def _process_saved_event(saved):
    results=[]
    payload=dict(saved.get("payload") or {})
    for row in repository.list_rules():
        if row["is_active"] and row["event_type"]==saved["event_type"] and row["entity_type"]==saved["entity_type"]:
            rule=get_rule(row["code"]); entity=load_entity(saved["entity_type"],saved["entity_id"]); entity["event_payload"]=payload; p=rule.validate_parameters(dict(row["parameters"])); matched,reasons=evaluate_rule(row["code"],entity,p); action=build_action(row["code"],entity,p) if matched else None
            results.append(repository.execute_live(row["code"],entity,matched,reasons,action,event_id=saved["id"]))
    status='failed' if any(x.get('status')=='failed' for x in results) else ('processed' if results else 'ignored')
    return {"event":repository.update_event_status(saved['id'],status),"executions":results}


def process_event(event):
    data=dump(event); saved=repository.add_event(data)
    return process_saved_event(saved['id'])


def process_saved_event(event_id,received_only=False):
    with repository.claim_event_for_processing(event_id,received_only=received_only) as claim:
        if claim['claim_status']!='claimed':
            return {**claim,'executions':[]}
        saved=claim['event']
        try:
            return {'claim_status':'claimed',**_process_saved_event(saved)}
        except Exception as exc:
            repository.update_event_status(event_id,'failed',str(exc))
            raise


def recover_received_events(limit):
    items=[]
    counts={'processed':0,'ignored':0,'failed':0,'busy':0}
    for event_id in repository.list_received_owner_event_ids(limit):
        try:
            result=process_saved_event(event_id,received_only=True)
            claim_status=result.get('claim_status')
            event_status=(result.get('event') or {}).get('status')
            if claim_status=='busy':
                counts['busy']+=1; item_status='busy'
            elif claim_status=='ineligible':
                counts['ignored']+=1; item_status='ignored'
            elif event_status=='processed':
                counts['processed']+=1; item_status='processed'
            elif event_status=='ignored':
                counts['ignored']+=1; item_status='ignored'
            else:
                counts['failed']+=1; item_status='failed'
            items.append({'event_id':event_id,'status':item_status})
        except Exception as exc:
            counts['failed']+=1
            items.append({'event_id':event_id,'status':'failed','error_message':str(exc)})
    problems=counts['failed']+counts['busy']
    status='failed' if counts['failed'] and not (counts['processed']+counts['ignored']+counts['busy']) else ('partial_failure' if problems else 'completed')
    return {'status':status,'requested_limit':limit,**counts,'items':items}


def _scan_failure(code, stage, error, entity_type=None, entity_id=None, mode="simulation", requested_by=None):
    item={
        "status":"failed",
        "rule_code":code,
        "stage":stage,
        "entity_type":entity_type,
        "entity_id":entity_id,
        "error_message":str(error),
    }
    if entity_type is not None and entity_id is not None:
        try:
            saved=repository.record_failure(code,entity_type,entity_id,mode,str(error),requested_by)
            item={**saved,**item}
        except Exception as persistence_error:
            item["persistence_error"]=str(persistence_error)
    return item


def scan(payload):
    data=dump(payload); repository.sync_rules()
    codes=data.get("rule_codes")
    if not codes:
        codes=[r["code"] for r in repository.list_rules(synchronize=False) if r["is_active"] or data.get("simulation")]
    results=[]; plans=[]
    for code in codes:
        try:
            row=repository.get_rule_row(code,synchronize=False); rule=get_rule(code)
            p=rule.validate_parameters(dict(row["parameters"])); candidates=scan_candidates(code,p,data["limit"])
            plans.append({"code":code,"parameters":p,"candidates":list(candidates),"offset":0})
        except Exception as exc:
            results.append(_scan_failure(code,"adapter",exc,mode="simulation" if data["simulation"] else "live",requested_by=data.get("requested_by")))

    processed=0
    while processed<data["limit"]:
        progressed=False
        for plan in plans:
            if processed>=data["limit"]: break
            offset=plan["offset"]
            if offset>=len(plan["candidates"]): continue
            progressed=True; plan["offset"]+=1; processed+=1
            code=plan["code"]; p=plan["parameters"]
            entity_type,entity_id=plan["candidates"][offset]
            try:
                entity=load_entity(entity_type,entity_id)
            except Exception as exc:
                results.append(_scan_failure(code,"load",exc,entity_type,entity_id,"simulation" if data["simulation"] else "live",data.get("requested_by")))
                continue
            try:
                matched,reasons=evaluate_rule(code,entity,p); action=build_action(code,entity,p) if matched else None
            except Exception as exc:
                results.append(_scan_failure(code,"evaluate",exc,entity_type,entity_id,"simulation" if data["simulation"] else "live",data.get("requested_by")))
                continue
            try:
                if data["simulation"]: ex=repository.record_simulation(code,entity_type,entity_id,matched,reasons,action,data.get("requested_by"))
                else: ex=repository.execute_live(code,entity,matched,reasons,action,data.get("requested_by"))
                results.append({"rule_code":code,**dict(ex)})
            except Exception as exc:
                results.append(_scan_failure(code,"execute",exc,entity_type,entity_id,"simulation" if data["simulation"] else "live",data.get("requested_by")))
        if not progressed: break

    failures=sum(1 for item in results if item.get("status")=="failed")
    skips=sum(1 for item in results if item.get("status")=="skipped")
    successes=len(results)-failures-skips
    status="failed" if failures and not successes and not skips else ("partial_failure" if failures else "completed")
    return {
        "requested_limit":data["limit"],"processed":processed,"simulation":data["simulation"],"status":status,
        "successes":successes,"failures":failures,"skips":skips,"items":results,
    }


def retry(execution_id,payload):
    original=repository.increment_retry(execution_id); ex=repository.get_execution(execution_id); code=ex["rule_code"]
    entity=load_entity(ex["entity_type"],ex["entity_id"]); row=repository.get_rule_row(code); rule=get_rule(code); p=rule.validate_parameters(dict(row["parameters"])); matched,reasons=evaluate_rule(code,entity,p); action=build_action(code,entity,p) if matched else None
    return repository.execute_live(code,entity,matched,reasons,action,dump(payload).get("requested_by"),event_id=ex.get("event_id"),retry_of_execution_id=execution_id)
