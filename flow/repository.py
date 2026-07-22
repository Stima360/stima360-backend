from __future__ import annotations
from datetime import datetime, timezone, timedelta
from psycopg2.extras import Json
from core.database import core_cursor
from core.exceptions import NotFoundError, ConflictError, ValidationError
from core import repository as core_repository
from .rules import RULES, get_rule
from .enums import MAX_RETRY


def _dict(row): return dict(row) if row else None

def sync_rules():
    items=[]
    with core_cursor(commit=True) as (_,cur):
        for code,rule in RULES.items():
            cur.execute("SELECT * FROM flow_rules WHERE code=%s",(code,)); existing=cur.fetchone()
            defaults=rule.validate_parameters({})
            allowed=rule.allowed_parameters
            if not existing:
                cur.execute("""INSERT INTO flow_rules(code,code_version,name,description,event_type,entity_type,is_active,priority,cooldown_minutes,parameters,default_parameters,allowed_parameters,last_simulation_status)
                    VALUES(%s,%s,%s,%s,%s,%s,FALSE,%s,%s,%s,%s,%s,'never_run') RETURNING *""",
                    (code,rule.version,rule.name,rule.description,rule.event_type,rule.entity_type,defaults.get('task_priority','normal'),defaults.get('cooldown_minutes',0),Json(defaults),Json(defaults),Json(allowed)))
                items.append(dict(cur.fetchone())); continue
            current=dict(existing); parameters=dict(current.get('parameters') or {})
            merged=rule.validate_parameters({k:v for k,v in parameters.items() if k in rule.allowed_parameters})
            version_changed=current['code_version']!=rule.version
            cur.execute("""UPDATE flow_rules SET code_version=%s,name=%s,description=%s,event_type=%s,entity_type=%s,
                default_parameters=%s,allowed_parameters=%s,parameters=%s,
                last_simulation_status=CASE WHEN %s THEN 'outdated' ELSE last_simulation_status END,
                updated_at=NOW() WHERE code=%s RETURNING *""",
                (rule.version,rule.name,rule.description,rule.event_type,rule.entity_type,Json(defaults),Json(allowed),Json(merged),version_changed,code))
            items.append(dict(cur.fetchone()))
    return items


def list_rules():
    sync_rules()
    with core_cursor() as (_,cur): cur.execute("SELECT * FROM flow_rules WHERE archived_at IS NULL ORDER BY code"); return [dict(x) for x in cur.fetchall()]

def get_rule_row(code):
    sync_rules()
    with core_cursor() as (_,cur):
        cur.execute("SELECT * FROM flow_rules WHERE code=%s AND archived_at IS NULL",(code,)); row=cur.fetchone()
        if not row: raise NotFoundError(f"flow rule {code} not found")
        return dict(row)

def update_parameters(code, parameters, updated_by=None):
    row=get_rule_row(code); rule=get_rule(code); valid=rule.validate_parameters(parameters)
    h=rule.parameters_hash(valid)
    current_h=rule.parameters_hash(dict(row.get('parameters') or {}))
    with core_cursor(commit=True) as (_,cur):
        cur.execute("""UPDATE flow_rules SET parameters=%s,priority=%s,cooldown_minutes=%s,
            last_simulation_status=CASE WHEN %s<>%s THEN 'outdated' ELSE last_simulation_status END,
            updated_at=NOW() WHERE code=%s RETURNING *""",
            (Json(valid),valid.get('task_priority','normal'),valid.get('cooldown_minutes',0),h,current_h,code))
        return dict(cur.fetchone())

def reset_parameters(code): return update_parameters(code,get_rule(code).default_parameters)

def record_simulation(code, entity_type, entity_id, matched, reasons, action, requested_by=None, error=None):
    row=get_rule_row(code); rule=get_rule(code); p=dict(row['parameters']); h=rule.parameters_hash(p)
    status='failed' if error else ('matched' if matched else 'not_matched')
    with core_cursor(commit=True) as (_,cur):
        cur.execute("""INSERT INTO flow_executions(rule_id,entity_type,entity_id,execution_mode,status,conditions_result,actions_result,rule_version,parameters_snapshot,parameters_hash,error_message,retry_count,max_retry,started_at,completed_at,created_at)
            VALUES(%s,%s,%s,'simulation',%s,%s,%s,%s,%s,%s,%s,0,%s,NOW(),NOW(),NOW()) RETURNING *""",
            (row['id'],entity_type,entity_id,status,Json({'matched':matched,'reasons':reasons}),Json({'planned_action':action}),rule.version,Json(p),h,error,MAX_RETRY))
        execution=dict(cur.fetchone())
        cur.execute("""UPDATE flow_rules SET last_simulation_at=NOW(),last_simulation_status=%s,last_simulation_execution_id=%s,
            last_simulation_parameters_hash=%s,last_simulation_rule_version=%s,updated_at=NOW() WHERE id=%s""",
            ('failed' if error else 'success',execution['id'],h,rule.version,row['id']))
        return execution

def activate(code, activated_by=None):
    row=get_rule_row(code); rule=get_rule(code); p=dict(row['parameters']); h=rule.parameters_hash(p)
    if row['last_simulation_status']!='success' or row.get('last_simulation_parameters_hash')!=h or row.get('last_simulation_rule_version')!=rule.version:
        raise ConflictError("La regola deve essere simulata con successo usando versione e parametri correnti prima dell'attivazione.")
    with core_cursor(commit=True) as (_,cur):
        cur.execute("UPDATE flow_rules SET is_active=TRUE,activated_at=NOW(),activated_by=%s,updated_at=NOW() WHERE id=%s RETURNING *",(activated_by,row['id'])); return dict(cur.fetchone())

def deactivate(code):
    row=get_rule_row(code)
    with core_cursor(commit=True) as (_,cur): cur.execute("UPDATE flow_rules SET is_active=FALSE,updated_at=NOW() WHERE id=%s RETURNING *",(row['id'],)); return dict(cur.fetchone())

def add_event(data):
    key=data.get('deduplication_key') or f"{data['event_type']}:{data['entity_type']}:{data['entity_id']}:{datetime.now(timezone.utc).strftime('%Y%m%d%H')}"
    with core_cursor(commit=True) as (_,cur):
        cur.execute("""INSERT INTO flow_events(event_type,entity_type,entity_id,source_module,payload,deduplication_key,status,occurred_at,received_at)
            VALUES(%s,%s,%s,%s,%s,%s,'received',NOW(),NOW()) ON CONFLICT(deduplication_key) DO UPDATE SET received_at=flow_events.received_at RETURNING *""",
            (data['event_type'],data['entity_type'],data['entity_id'],data['source_module'],Json(data.get('payload') or {}),key)); return dict(cur.fetchone())

def update_event_status(event_id, status, error_message=None):
    with core_cursor(commit=True) as (_,cur):
        cur.execute("UPDATE flow_events SET status=%s,error_message=%s WHERE id=%s RETURNING *",(status,error_message,event_id)); return dict(cur.fetchone())

def list_events(limit=100,offset=0,status=None):
    with core_cursor() as (_,cur):
        if status: cur.execute("SELECT * FROM flow_events WHERE status=%s ORDER BY received_at DESC LIMIT %s OFFSET %s",(status,limit,offset))
        else: cur.execute("SELECT * FROM flow_events ORDER BY received_at DESC LIMIT %s OFFSET %s",(limit,offset))
        return [dict(x) for x in cur.fetchall()]

def is_suppressed(rule_id,entity_type,entity_id):
    with core_cursor() as (_,cur):
        cur.execute("SELECT 1 FROM flow_suppressions WHERE rule_id=%s AND entity_type=%s AND entity_id=%s AND (expires_at IS NULL OR expires_at>NOW())",(rule_id,entity_type,entity_id)); return bool(cur.fetchone())

def _idempotency_key(code,entity_type,entity_id,cooldown):
    if cooldown<=0: bucket=datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')
    else:
        seconds=cooldown*60; bucket=int(datetime.now(timezone.utc).timestamp()//seconds)
    return f"{code}:{entity_type}:{entity_id}:{bucket}"

def execute_live(code,entity,matched,reasons,action,requested_by=None,event_id=None,retry_of_execution_id=None):
    row=get_rule_row(code); rule=get_rule(code); p=dict(row['parameters']); h=rule.parameters_hash(p)
    if not row['is_active']: raise ConflictError("rule is not active")
    if is_suppressed(row['id'],entity['entity_type'],entity['entity_id']):
        matched=False; reasons=[*reasons,'soppressione attiva']
    idem=_idempotency_key(code,entity['entity_type'],entity['entity_id'],int(p.get('cooldown_minutes',0)))
    with core_cursor(commit=True) as (_,cur):
        cur.execute("""INSERT INTO flow_executions(event_id,rule_id,entity_type,entity_id,execution_mode,status,conditions_result,actions_result,rule_version,parameters_snapshot,parameters_hash,retry_count,max_retry,retry_of_execution_id,started_at,created_at)
            VALUES(%s,%s,%s,%s,'live',%s,%s,%s,%s,%s,%s,0,%s,%s,NOW(),NOW()) RETURNING *""",
            (event_id,row['id'],entity['entity_type'],entity['entity_id'],'matched' if matched else 'not_matched',Json({'matched':matched,'reasons':reasons}),Json({}),rule.version,Json(p),h,MAX_RETRY,retry_of_execution_id)); ex=dict(cur.fetchone())
    if not matched:
        with core_cursor(commit=True) as (_,cur): cur.execute("UPDATE flow_executions SET status='not_matched',completed_at=NOW() WHERE id=%s RETURNING *",(ex['id'],)); return dict(cur.fetchone())
    try:
        with core_cursor(commit=True) as (_,cur):
            cur.execute("""INSERT INTO flow_action_records(execution_id,action_type,target_module,target_entity_type,idempotency_key,payload,status,created_at)
                VALUES(%s,%s,'core','task',%s,%s,'pending',NOW()) ON CONFLICT(idempotency_key) DO NOTHING RETURNING *""",(ex['id'],action['action_type'],idem,Json(action)))
            ar=cur.fetchone()
        if not ar:
            with core_cursor(commit=True) as (_,cur): cur.execute("UPDATE flow_executions SET status='skipped',actions_result=%s,completed_at=NOW() WHERE id=%s RETURNING *",(Json({'reason':'duplicate_or_cooldown'}),ex['id'])); return dict(cur.fetchone())
        if action['action_type']=='create_core_task':
            if not action.get('contact_id') and not action.get('lead_id'):
                raise ValidationError("FLOW task requires contact_id or lead_id")
            task=core_repository.create_task({
                'contact_id':action.get('contact_id'),'lead_id':action.get('lead_id'),'stima_id':None,
                'title':action['title'],'description':action['description'],'task_type':'flow_follow_up','priority':action['priority'],'status':'open',
                'due_at':datetime.now(timezone.utc)+timedelta(hours=action.get('due_hours',24)),'completed_at':None,
                'assigned_to':action.get('assigned_to'),'created_by':requested_by or 'FLOW',
                'metadata':{'source':'flow','flow_rule_code':code,'flow_execution_id':ex['id'],'idempotency_key':idem}
            })
            result={'task_id':task['id']}
        else: result={'logged':True}
        with core_cursor(commit=True) as (_,cur):
            cur.execute("UPDATE flow_action_records SET target_entity_id=%s,status='completed' WHERE id=%s",(result.get('task_id'),ar['id']))
            cur.execute("UPDATE flow_executions SET status='executed',actions_result=%s,completed_at=NOW() WHERE id=%s RETURNING *",(Json(result),ex['id'])); return dict(cur.fetchone())
    except Exception as exc:
        with core_cursor(commit=True) as (_,cur):
            cur.execute("UPDATE flow_action_records SET status='failed',error_message=%s WHERE execution_id=%s AND status='pending'",(str(exc),ex['id']))
            cur.execute("UPDATE flow_executions SET status='failed',error_message=%s,completed_at=NOW() WHERE id=%s RETURNING *",(str(exc),ex['id'])); failed=dict(cur.fetchone())
        return failed

def get_execution(execution_id):
    with core_cursor() as (_,cur):
        cur.execute("SELECT e.*,r.code AS rule_code FROM flow_executions e JOIN flow_rules r ON r.id=e.rule_id WHERE e.id=%s",(execution_id,)); row=cur.fetchone()
        if not row: raise NotFoundError(f"flow execution {execution_id} not found")
        return dict(row)

def increment_retry(execution_id):
    ex=get_execution(execution_id)
    if ex['status']!='failed': raise ConflictError("only failed executions can be retried")
    with core_cursor() as (_,cur):
        cur.execute("SELECT 1 FROM flow_executions WHERE retry_of_execution_id=%s AND status IN ('executed','skipped') LIMIT 1",(execution_id,))
        if cur.fetchone(): raise ConflictError("execution already recovered by a successful retry")
    if ex['retry_count']>=MAX_RETRY: raise ConflictError("Limite massimo di 3 retry raggiunto. È richiesto un intervento amministrativo.")
    with core_cursor(commit=True) as (_,cur):
        cur.execute("UPDATE flow_executions SET retry_count=retry_count+1,last_retry_at=NOW() WHERE id=%s RETURNING *",(execution_id,)); return dict(cur.fetchone())

def list_executions(limit=100,offset=0,status=None):
    with core_cursor() as (_,cur):
        sql="SELECT e.*,r.code AS rule_code,r.name AS rule_name FROM flow_executions e JOIN flow_rules r ON r.id=e.rule_id"; params=[]
        if status: sql+=" WHERE e.status=%s"; params.append(status)
        sql+=" ORDER BY e.created_at DESC LIMIT %s OFFSET %s"; params += [limit,offset]; cur.execute(sql,params); return [dict(x) for x in cur.fetchall()]

def add_suppression(data):
    row=get_rule_row(data['rule_code'])
    with core_cursor(commit=True) as (_,cur):
        cur.execute("""INSERT INTO flow_suppressions(rule_id,entity_type,entity_id,reason,expires_at,created_by,created_at)
            VALUES(%s,%s,%s,%s,%s,%s,NOW()) RETURNING *""",(row['id'],data['entity_type'],data['entity_id'],data['reason'],data.get('expires_at'),data.get('created_by'))); return dict(cur.fetchone())

def list_suppressions():
    with core_cursor() as (_,cur): cur.execute("SELECT s.*,r.code AS rule_code FROM flow_suppressions s JOIN flow_rules r ON r.id=s.rule_id ORDER BY s.created_at DESC"); return [dict(x) for x in cur.fetchall()]
def delete_suppression(i):
    with core_cursor(commit=True) as (_,cur): cur.execute("DELETE FROM flow_suppressions WHERE id=%s RETURNING id",(i,));

def dashboard():
    with core_cursor() as (_,cur):
        cur.execute("""SELECT
          (SELECT COUNT(*) FROM flow_rules WHERE is_active) active_rules,
          (SELECT COUNT(*) FROM flow_events) total_events,
          (SELECT COUNT(*) FROM flow_executions WHERE status='executed') executed,
          (SELECT COUNT(*) FROM flow_executions WHERE status='failed') failed,
          (SELECT COUNT(*) FROM flow_executions WHERE status='skipped') skipped,
          (SELECT COUNT(*) FROM flow_action_records WHERE status='completed' AND action_type='create_core_task') tasks_created,
          (SELECT COUNT(*) FROM flow_suppressions WHERE expires_at IS NULL OR expires_at>NOW()) active_suppressions"""); return dict(cur.fetchone())
