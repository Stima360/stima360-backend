from __future__ import annotations
from . import repository
from .adapters import (
    load_entity,
    load_entity_for_agency,
    scan_candidates,
    scan_candidates_for_agency,
)
from .engine import evaluate as evaluate_rule, build_action
from .rules import get_rule


# ---------------------------------------------------------------------------
# P26-6C: FLOW runs per agency.
#
# Every function that reads or writes tenant data takes an `agency_id`. The
# orchestration is NOT duplicated: `_adapters` returns the scoped pair or the
# global one, and the body of each function is the code that was already there.
#
# An agency-less call remains possible - the historical tests make it - but it
# is reachable from no HTTP route, and every write it would perform requires a
# tenant it does not have, so it fails loudly instead of writing an unowned row.
# ---------------------------------------------------------------------------

def _adapters(agency_id):
    """(scan, load) bound to one agency, or the global pair when there is none."""
    if agency_id is None:
        return scan_candidates, load_entity
    return (
        lambda code, parameters, limit: scan_candidates_for_agency(
            agency_id, code, parameters, limit
        ),
        lambda entity_type, entity_id: load_entity_for_agency(
            agency_id, entity_type, entity_id
        ),
    )


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
def create_event(payload,*,agency_id=None): return repository.add_event(dump(payload),agency_id=agency_id)
def list_events(*args): return repository.list_events(*args)
def list_executions(*args): return repository.list_executions(*args)
def get_execution(i): return repository.get_execution(i)
def dashboard(): return repository.dashboard()
def add_suppression(payload,*,agency_id=None): return repository.add_suppression(dump(payload),agency_id=agency_id)
def list_suppressions(): return repository.list_suppressions()
def delete_suppression(i): return repository.delete_suppression(i)


def simulate(code, payload, *, agency_id=None):
    data=dump(payload); row=repository.get_rule_row(code); rule=get_rule(code)
    if data["entity_type"] != rule.entity_type: raise ValueError(f"rule {code} requires entity_type {rule.entity_type}")
    _scan,_load=_adapters(agency_id)
    try:
        entity=_load(data["entity_type"],data["entity_id"])
        parameters=rule.validate_parameters(dict(row["parameters"]))
        matched,reasons=evaluate_rule(code,entity,parameters)
        action=build_action(code,entity,parameters) if matched else None
        return repository.record_simulation(code,data["entity_type"],data["entity_id"],matched,reasons,action,data.get("requested_by"),agency_id=agency_id)
    except Exception as exc:
        repository.record_simulation(code,data["entity_type"],data["entity_id"],False,[],None,data.get("requested_by"),str(exc),agency_id=agency_id)
        raise


def evaluate(payload, *, agency_id=None):
    data=dump(payload); code=data["rule_code"]; row=repository.get_rule_row(code); rule=get_rule(code)
    if data["entity_type"] != rule.entity_type: raise ValueError(f"rule {code} requires entity_type {rule.entity_type}")
    _scan,_load=_adapters(agency_id)
    entity=_load(data["entity_type"],data["entity_id"]); p=rule.validate_parameters(dict(row["parameters"])); matched,reasons=evaluate_rule(code,entity,p); action=build_action(code,entity,p) if matched else None
    if data["mode"]=="simulation": return repository.record_simulation(code,data["entity_type"],data["entity_id"],matched,reasons,action,data.get("requested_by"),agency_id=agency_id)
    return repository.execute_live(code,entity,matched,reasons,action,data.get("requested_by"),agency_id=agency_id)


def _process_saved_event(saved):
    results=[]
    payload=dict(saved.get("payload") or {})
    # The event row carries the tenant it was raised for (052). Reading it from
    # the event rather than re-deriving it is what keeps an execution and its
    # event on one agency, which is the invariant 054's trigger enforces.
    agency_id=saved.get("agency_id")
    _scan,_load=_adapters(agency_id)
    for row in repository.list_rules():
        if row["is_active"] and row["event_type"]==saved["event_type"] and row["entity_type"]==saved["entity_type"]:
            rule=get_rule(row["code"]); entity=_load(saved["entity_type"],saved["entity_id"]); entity["event_payload"]=payload; p=rule.validate_parameters(dict(row["parameters"])); matched,reasons=evaluate_rule(row["code"],entity,p); action=build_action(row["code"],entity,p) if matched else None
            results.append(repository.execute_live(row["code"],entity,matched,reasons,action,event_id=saved["id"],agency_id=agency_id))
    status='failed' if any(x.get('status')=='failed' for x in results) else ('processed' if results else 'ignored')
    return {"event":repository.update_event_status(saved['id'],status),"executions":results}


def process_event(event, *, agency_id=None):
    data=dump(event); saved=repository.add_event(data,agency_id=agency_id)
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


def _recover_events(event_ids, limit):
    """The recovery cycle, over whichever set of event ids it is given.

    Extracted in P26-6C so the ctx-less path and the per-agency one cannot
    drift: the RESPONSE SHAPE here is a contract, not an implementation detail.
    run_flow_p2b_cron.py::_post validates that a recovery response carries a
    string `status` and non-negative integer `requested_limit`, `processed`,
    `ignored`, `failed` and `busy`, and rejects anything else as `invalid_json`
    before it can even read the counts.

    The first version of `recover_received_events_for_agency` returned only
    `{processed, items}` - syntactically valid JSON that the cron could not
    accept - and the scheduled job failed with
    `phase=recovery status=failed reason=invalid_json`. Both entry points now
    build their answer here, so there is one shape and one place that defines it.
    """
    items=[]
    counts={'processed':0,'ignored':0,'failed':0,'busy':0}
    for event_id in event_ids:
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


def recover_received_events(limit):
    return _recover_events(repository.list_received_owner_event_ids(limit), limit)


def _scan_failure(code, stage, error, entity_type=None, entity_id=None, mode="simulation", requested_by=None, *, agency_id=None):
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
            saved=repository.record_failure(code,entity_type,entity_id,mode,str(error),requested_by,agency_id=agency_id)
            item={**saved,**item}
        except Exception as persistence_error:
            item["persistence_error"]=str(persistence_error)
    return item


def scan(payload, *, agency_id=None):
    data=dump(payload); repository.sync_rules()
    codes=data.get("rule_codes")
    if not codes:
        codes=[r["code"] for r in repository.list_rules(synchronize=False) if r["is_active"] or data.get("simulation")]
    _scan,_load=_adapters(agency_id)
    results=[]; plans=[]
    for code in codes:
        try:
            row=repository.get_rule_row(code,synchronize=False); rule=get_rule(code)
            p=rule.validate_parameters(dict(row["parameters"])); candidates=_scan(code,p,data["limit"])
            plans.append({"code":code,"parameters":p,"candidates":list(candidates),"offset":0})
        except Exception as exc:
            results.append(_scan_failure(code,"adapter",exc,mode="simulation" if data["simulation"] else "live",requested_by=data.get("requested_by"),agency_id=agency_id))

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
                entity=_load(entity_type,entity_id)
            except Exception as exc:
                results.append(_scan_failure(code,"load",exc,entity_type,entity_id,"simulation" if data["simulation"] else "live",data.get("requested_by"),agency_id=agency_id))
                continue
            try:
                matched,reasons=evaluate_rule(code,entity,p); action=build_action(code,entity,p) if matched else None
            except Exception as exc:
                results.append(_scan_failure(code,"evaluate",exc,entity_type,entity_id,"simulation" if data["simulation"] else "live",data.get("requested_by"),agency_id=agency_id))
                continue
            try:
                if data["simulation"]: ex=repository.record_simulation(code,entity_type,entity_id,matched,reasons,action,data.get("requested_by"),agency_id=agency_id)
                else: ex=repository.execute_live(code,entity,matched,reasons,action,data.get("requested_by"),agency_id=agency_id)
                results.append({"rule_code":code,**dict(ex)})
            except Exception as exc:
                results.append(_scan_failure(code,"execute",exc,entity_type,entity_id,"simulation" if data["simulation"] else "live",data.get("requested_by"),agency_id=agency_id))
        if not progressed: break

    failures=sum(1 for item in results if item.get("status")=="failed")
    skips=sum(1 for item in results if item.get("status")=="skipped")
    successes=len(results)-failures-skips
    status="failed" if failures and not successes and not skips else ("partial_failure" if failures else "completed")
    return {
        "requested_limit":data["limit"],"processed":processed,"simulation":data["simulation"],"status":status,
        "successes":successes,"failures":failures,"skips":skips,"items":results,
    }


def retry(execution_id,payload,*,agency_id=None):
    # The execution is proved to be this agency's BEFORE anything is
    # incremented or re-run: a foreign id is not found, so a retry cannot even
    # bump another tenant's retry_count, let alone re-execute its automation.
    if agency_id is None:
        original=repository.increment_retry(execution_id); ex=repository.get_execution(execution_id)
    else:
        original=repository.increment_retry_for_agency(execution_id,agency_id)
        ex=repository.get_execution_for_agency(execution_id,agency_id)
    code=ex["rule_code"]
    _scan,_load=_adapters(agency_id)
    entity=_load(ex["entity_type"],ex["entity_id"]); row=repository.get_rule_row(code); rule=get_rule(code); p=rule.validate_parameters(dict(row["parameters"])); matched,reasons=evaluate_rule(code,entity,p); action=build_action(code,entity,p) if matched else None
    # The retried execution's own agency, which the scoped reads above have
    # already proved equals the caller's. 054 refuses a retry that crosses.
    return repository.execute_live(code,entity,matched,reasons,action,dump(payload).get("requested_by"),event_id=ex.get("event_id"),retry_of_execution_id=execution_id,agency_id=ex.get("agency_id",agency_id))


# ---------------------------------------------------------------------------
# P26-6C: the three shapes, as in every slice since P26-6A.
#
#   *_for_agency(agency_id, ...)  one bounded cycle - what the HTTP routes call
#                                 after resolving their context
#   *_for_all_agencies(...)       the server-only orchestrator, which iterates
#                                 `list_active_agency_ids()` and runs the
#                                 bounded cycle once per tenant
#
# There is no global scan on any path a person can reach. The orchestrator is
# what supplies the tenant predicate to a background run, and it is a loop -
# never one pass filtered afterwards.
# ---------------------------------------------------------------------------

def scan_for_agency(agency_id, payload):
    return scan(payload, agency_id=agency_id)


def scan_for_all_agencies(payload):
    """One bounded scan per active agency, summed.

    Deliberately not a single global pass. Each cycle carries its own tenant
    predicate, so no statement reached from here ever runs without one, and one
    agency's failing rule cannot end another agency's scan.
    """
    runs = []
    for agency_id in repository.list_active_agency_ids():
        runs.append({"agency_id": agency_id, **scan(payload, agency_id=agency_id)})
    return {
        "agencies": len(runs),
        "requested_limit": dump(payload)["limit"],
        "processed": sum(r["processed"] for r in runs),
        "successes": sum(r["successes"] for r in runs),
        "failures": sum(r["failures"] for r in runs),
        "skips": sum(r["skips"] for r in runs),
        "runs": runs,
    }


def simulate_for_agency(agency_id, code, payload):
    return simulate(code, payload, agency_id=agency_id)


def evaluate_for_agency(agency_id, payload):
    return evaluate(payload, agency_id=agency_id)


def process_event_for_agency(agency_id, event):
    """The agency comes from the caller's context, never from the payload.

    `EventCreate` has no agency field and the schema test asserts it never
    grows one, so there is nothing here for a client to forge - the tenant is
    the one the server resolved.
    """
    return process_event(event, agency_id=agency_id)


def retry_for_agency(agency_id, execution_id, payload):
    return retry(execution_id, payload, agency_id=agency_id)


def list_events_for_agency(agency_id, limit=100, offset=0, status=None):
    return repository.list_events_for_agency(agency_id, limit, offset, status)


def get_event_for_agency(agency_id, event_id):
    return repository.get_event_for_agency(event_id, agency_id)


def list_executions_for_agency(agency_id, limit=100, offset=0, status=None):
    return repository.list_executions_for_agency(agency_id, limit, offset, status)


def get_execution_for_agency(agency_id, execution_id):
    return repository.get_execution_for_agency(execution_id, agency_id)


def dashboard_for_agency(agency_id):
    return repository.dashboard_for_agency(agency_id)


def add_suppression_for_agency(agency_id, payload):
    return add_suppression(payload, agency_id=agency_id)


def list_suppressions_for_agency(agency_id):
    return repository.list_suppressions_for_agency(agency_id)


def delete_suppression_for_agency(agency_id, suppression_id):
    return repository.delete_suppression_for_agency(suppression_id, agency_id)


def recover_received_events_for_agency(agency_id, limit):
    """Recovery, bounded to one agency's stuck events.

    Same response contract as the ctx-less twin - see `_recover_events` - and
    the only difference is which events are eligible to be recovered. The
    agency filter is in the id query, so nothing outside this tenant is even
    claimed, let alone processed.
    """
    return _recover_events(
        repository.list_received_owner_event_ids_for_agency(agency_id, limit), limit
    )
