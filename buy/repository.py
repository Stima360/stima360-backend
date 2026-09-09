from __future__ import annotations
from decimal import Decimal
from datetime import date, datetime
from psycopg2 import errors
from psycopg2.extras import Json
from core.database import core_cursor
from core.exceptions import NotFoundError, ConflictError, ValidationError

RELEVANT_FIELDS={'budget_min','budget_target','budget_max','budget_flexibility_percent','includes_agency_fees','includes_renovation','finance_status','mortgage_required','mortgage_preapproved','available_cash','maximum_monthly_payment','property_to_sell_first','surface_min','surface_target','surface_max','rooms_min','bedrooms_min','bathrooms_min','status','urgency','target_purchase_date'}
FINANCE_FIELDS={'finance_status','mortgage_required','mortgage_preapproved','available_cash','maximum_monthly_payment','property_to_sell_first','finance_review_at','finance_notes'}
NEXT_ACTION_FIELDS={'next_action_at','next_action_note'}
MATCH_STATUS={'proposed':'suggested','discarded':'rejected','interested':'interested','visit_requested':'visit_requested','visit_scheduled':'visit_scheduled','visited':'visited','offer_candidate':'interested'}
HISTORY_EVENT={'proposed':'match_proposed','discarded':'match_discarded','interested':'match_interested','visit_requested':'visit_requested','visit_scheduled':'visit_scheduled','visited':'visited','offer_candidate':'offer_candidate','other':'note'}

def _jsonable(value):
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value

def row(value): return dict(value) if value else None

def ensure(cur,table,item_id,label):
    cur.execute(f'SELECT id FROM {table} WHERE id=%s',(item_id,))
    if not cur.fetchone(): raise NotFoundError(f'{label} {item_id} not found')

def history(cur, request_id, event_type, description=None, old_value=None, new_value=None, match_id=None, property_id=None, task_id=None, reason_code=None, created_by=None):
    cur.execute("""INSERT INTO buy_request_history(buy_request_id,event_type,match_id,property_id,task_id,reason_code,description,old_value,new_value,created_by)
    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",(request_id,event_type,match_id,property_id,task_id,reason_code,description,Json(_jsonable(old_value)) if old_value is not None else None,Json(_jsonable(new_value)) if new_value is not None else None,created_by))

def create_request(data):
    data=dict(data); data['metadata']=Json(data.get('metadata') or {})
    with core_cursor(commit=True) as (_,cur):
        ensure(cur,'contacts',data['contact_id'],'contact')
        if data.get('lead_id') is not None: ensure(cur,'leads',data['lead_id'],'lead')
        cols=list(data); cur.execute(f"INSERT INTO buy_requests({','.join(cols)}) VALUES({','.join(['%s']*len(cols))}) RETURNING *",list(data.values())); result=row(cur.fetchone())
        history(cur,result['id'],'request_created','Richiesta BUY creata',new_value={'status':result['status'],'title':result['title']})
        return result

def list_requests(limit,offset,search,status,priority,urgency,contact_id,lead_id,assigned_to):
    filters=['b.archived_at IS NULL']; params=[]
    if search: filters.append("(b.title ILIKE %s OR c.display_name ILIKE %s OR c.email ILIKE %s OR c.phone ILIKE %s)"); params += [f'%{search}%']*4
    if status: filters.append('b.status=%s'); params.append(status)
    if priority: filters.append('b.priority=%s'); params.append(priority)
    if urgency: filters.append('b.urgency=%s'); params.append(urgency)
    if contact_id: filters.append('b.contact_id=%s'); params.append(contact_id)
    if lead_id: filters.append('b.lead_id=%s'); params.append(lead_id)
    if assigned_to: filters.append('b.assigned_to=%s'); params.append(assigned_to)
    params += [limit,offset]
    with core_cursor() as (_,cur):
        cur.execute(f"""SELECT b.*,c.display_name AS contact_name,c.email AS contact_email,c.phone AS contact_phone,l.pipeline AS lead_pipeline,l.stage AS lead_stage,
        (SELECT COUNT(*) FROM buy_request_locations x WHERE x.buy_request_id=b.id) locations_count,
        (SELECT COUNT(*) FROM buy_request_typologies x WHERE x.buy_request_id=b.id) typologies_count,
        (SELECT COUNT(*) FROM buy_request_features x WHERE x.buy_request_id=b.id) features_count,
        (SELECT COUNT(*) FROM matches m WHERE m.buy_request_id=b.id AND m.archived_at IS NULL) matches_count,
        (SELECT COUNT(*) FROM matches m WHERE m.buy_request_id=b.id AND m.archived_at IS NULL AND m.match_class IN ('excellent','strong')) strong_matches_count,
        (SELECT COUNT(*) FROM buy_request_task_links lnk JOIN tasks t ON t.id=lnk.task_id WHERE lnk.buy_request_id=b.id AND t.status IN ('open','in_progress')) open_tasks_count
        FROM buy_requests b JOIN contacts c ON c.id=b.contact_id LEFT JOIN leads l ON l.id=b.lead_id
        WHERE {' AND '.join(filters)} ORDER BY CASE WHEN b.next_action_at IS NOT NULL AND b.next_action_at<NOW() THEN 0 ELSE 1 END,b.next_action_at NULLS LAST,b.updated_at DESC LIMIT %s OFFSET %s""",params)
        return [dict(x) for x in cur.fetchall()]

def get_request(request_id):
    with core_cursor() as (_,cur):
        cur.execute("""SELECT b.*,c.display_name contact_name,c.email contact_email,c.phone contact_phone,l.pipeline lead_pipeline,l.stage lead_stage
        FROM buy_requests b JOIN contacts c ON c.id=b.contact_id LEFT JOIN leads l ON l.id=b.lead_id WHERE b.id=%s""",(request_id,)); result=cur.fetchone()
        if not result: raise NotFoundError(f'buy request {request_id} not found')
        data=dict(result)
        for key,table in [('locations','buy_request_locations'),('typologies','buy_request_typologies'),('features','buy_request_features')]:
            cur.execute(f'SELECT * FROM {table} WHERE buy_request_id=%s ORDER BY id',(request_id,)); data[key]=[dict(x) for x in cur.fetchall()]
        return data

def update_request(request_id,data):
    data=dict(data)
    if not data:return get_request(request_id)
    if 'metadata' in data:data['metadata']=Json(data.get('metadata') or {})
    with core_cursor(commit=True) as (_,cur):
        cur.execute('SELECT * FROM buy_requests WHERE id=%s',(request_id,)); old=cur.fetchone()
        if not old: raise NotFoundError(f'buy request {request_id} not found')
        old=dict(old)
        if data.get('lead_id') is not None:ensure(cur,'leads',data['lead_id'],'lead')
        relevant=bool(RELEVANT_FIELDS.intersection(data)); assignments=[f'{k}=%s' for k in data]
        if relevant:assignments.append('match_relevant_updated_at=NOW()')
        assignments.append('updated_at=NOW()')
        cur.execute(f"UPDATE buy_requests SET {','.join(assignments)} WHERE id=%s RETURNING *",list(data.values())+[request_id]); result=row(cur.fetchone())
        changed={k:{'old':old.get(k),'new':result.get(k)} for k in data if old.get(k)!=result.get(k)}
        if changed:
            event='status_changed' if 'status' in changed else 'finance_updated' if FINANCE_FIELDS.intersection(changed) else 'next_action_updated' if NEXT_ACTION_FIELDS.intersection(changed) else 'request_updated'
            history(cur,request_id,event,'Richiesta BUY aggiornata',old_value={k:v['old'] for k,v in changed.items()},new_value={k:v['new'] for k,v in changed.items()})
        return result

def archive_request(request_id):
    with core_cursor(commit=True) as (_,cur):
        cur.execute("UPDATE buy_requests SET status='archived',archived_at=NOW(),updated_at=NOW(),match_relevant_updated_at=NOW() WHERE id=%s RETURNING *",(request_id,)); result=cur.fetchone()
        if not result:raise NotFoundError(f'buy request {request_id} not found')
        history(cur,request_id,'status_changed','Richiesta archiviata',new_value={'status':'archived'})
        return row(result)

def add_child(table,request_id,data):
    data=dict(data)
    with core_cursor(commit=True) as (_,cur):
        ensure(cur,'buy_requests',request_id,'buy request');data['buy_request_id']=request_id;cols=list(data)
        try:cur.execute(f"INSERT INTO {table}({','.join(cols)}) VALUES({','.join(['%s']*len(cols))}) RETURNING *",list(data.values()))
        except errors.UniqueViolation as exc:raise ConflictError('criterion already exists for this request') from exc
        result=row(cur.fetchone());cur.execute('UPDATE buy_requests SET match_relevant_updated_at=NOW(),updated_at=NOW() WHERE id=%s',(request_id,));return result

def delete_child(table,item_id,label):
    with core_cursor(commit=True) as (_,cur):
        cur.execute(f'DELETE FROM {table} WHERE id=%s RETURNING buy_request_id',(item_id,)); result=cur.fetchone()
        if not result:raise NotFoundError(f'{label} {item_id} not found')
        cur.execute('UPDATE buy_requests SET match_relevant_updated_at=NOW(),updated_at=NOW() WHERE id=%s',(result['buy_request_id'],))

def normalized(request_id):
    data=get_request(request_id)
    return {'buy_request_id':data['id'],'status':data['status'],'contact_id':data['contact_id'],'lead_id':data.get('lead_id'),'budget':{k:data.get(k) for k in ('budget_min','budget_target','budget_max','budget_flexibility_percent','includes_agency_fees','includes_renovation')},'finance':{k:data.get(k) for k in ('finance_status','mortgage_required','mortgage_preapproved','available_cash','maximum_monthly_payment','property_to_sell_first')},'dimensions':{k:data.get(k) for k in ('surface_min','surface_target','surface_max','rooms_min','bedrooms_min','bathrooms_min')},'locations':data['locations'],'typologies':data['typologies'],'features':data['features'],'match_relevant_updated_at':data['match_relevant_updated_at']}

def list_matches(request_id):
    with core_cursor() as (_,cur):
        ensure(cur,'buy_requests',request_id,'buy request')
        cur.execute("""SELECT m.*,COALESCE(m.manual_score,m.score_total) effective_score,p.title property_title,p.code property_code,p.city,p.microzone,p.asking_price,p.classification,p.commercial_status property_status,
        (SELECT i.interaction_type FROM buy_request_interactions i WHERE i.buy_request_id=m.buy_request_id AND i.match_id=m.id ORDER BY i.occurred_at DESC,i.id DESC LIMIT 1) last_interaction,
        (SELECT i.reason_code FROM buy_request_interactions i WHERE i.buy_request_id=m.buy_request_id AND i.match_id=m.id ORDER BY i.occurred_at DESC,i.id DESC LIMIT 1) last_reason
        FROM matches m JOIN properties p ON p.id=m.property_id WHERE m.buy_request_id=%s AND m.archived_at IS NULL ORDER BY effective_score DESC,m.updated_at DESC""",(request_id,))
        return [dict(x) for x in cur.fetchall()]

def add_interaction(request_id,data):
    data=dict(data)
    with core_cursor(commit=True) as (_,cur):
        cur.execute('SELECT id FROM buy_requests WHERE id=%s FOR UPDATE',(request_id,));buy=cur.fetchone()
        if not buy:raise NotFoundError(f'buy request {request_id} not found')
        match_id=data.get('match_id');property_id=data.get('property_id');property_visit_id=data.get('property_visit_id')
        if match_id:
            cur.execute('SELECT property_id,buy_request_id FROM matches WHERE id=%s FOR UPDATE',(match_id,));m=cur.fetchone()
            if not m:raise NotFoundError(f'match {match_id} not found')
            if m['buy_request_id']!=request_id:raise ValidationError('match does not belong to buy request')
            property_id=m['property_id'];data['property_id']=property_id
        elif property_id:ensure(cur,'properties',property_id,'property')
        if property_visit_id is not None:
            if not match_id:raise ValidationError('match_id is required when property_visit_id is provided')
            cur.execute('SELECT property_id FROM property_visits WHERE id=%s FOR UPDATE',(property_visit_id,));visit=cur.fetchone()
            if not visit:raise NotFoundError(f'property visit {property_visit_id} not found')
            if visit['property_id']!=property_id:raise ValidationError('property visit does not belong to match property')
            cur.execute("""SELECT id FROM buy_request_interactions
            WHERE property_visit_id=%s AND buy_request_id=%s AND match_id=%s AND property_id=%s
            AND interaction_type='visit_scheduled' ORDER BY occurred_at DESC,id DESC LIMIT 1""",(property_visit_id,request_id,match_id,property_id));linked=cur.fetchone()
            if not linked:raise ValidationError('property visit is not linked to buy request and match')
        if data.get('occurred_at') is None:data.pop('occurred_at',None)
        data['buy_request_id']=request_id;cols=list(data)
        cur.execute(f"INSERT INTO buy_request_interactions({','.join(cols)}) VALUES({','.join(['%s']*len(cols))}) RETURNING *",list(data.values()));result=row(cur.fetchone())
        action=result['interaction_type']
        if match_id and action in MATCH_STATUS:
            cur.execute('UPDATE matches SET commercial_status=%s,last_reviewed_at=NOW(),updated_at=NOW() WHERE id=%s',(MATCH_STATUS[action],match_id))
        history(cur,request_id,HISTORY_EVENT[action],result.get('notes') or action,match_id=match_id,property_id=property_id,reason_code=result.get('reason_code'),new_value={'interaction_type':action})
        return result

def schedule_match_visit(request_id,match_id,data):
    data=dict(data);scheduled_at=data.pop('scheduled_at',None)
    if scheduled_at is None:raise ValidationError('scheduled_at is required when scheduling a visit')
    with core_cursor(commit=True) as (_,cur):
        cur.execute('SELECT contact_id,lead_id FROM buy_requests WHERE id=%s FOR UPDATE',(request_id,));buy=cur.fetchone()
        if not buy:raise NotFoundError(f'buy request {request_id} not found')
        cur.execute('SELECT property_id,buy_request_id FROM matches WHERE id=%s FOR UPDATE',(match_id,));match=cur.fetchone()
        if not match:raise NotFoundError(f'match {match_id} not found')
        if match['buy_request_id']!=request_id:raise ValidationError('match does not belong to buy request')
        property_id=match['property_id']
        cur.execute("""SELECT i.* FROM buy_request_interactions i JOIN property_visits v ON v.id=i.property_visit_id
        WHERE i.buy_request_id=%s AND i.match_id=%s AND i.interaction_type='visit_scheduled' AND v.scheduled_at=%s
        ORDER BY i.id DESC LIMIT 1""",(request_id,match_id,scheduled_at));existing=cur.fetchone()
        if existing:return row(existing)
        cur.execute("""INSERT INTO property_visits(property_id,contact_id,lead_id,scheduled_at,status,created_by)
        VALUES(%s,%s,%s,%s,%s,%s) RETURNING *""",(property_id,buy['contact_id'],buy['lead_id'],scheduled_at,'scheduled',data.get('created_by')));visit=row(cur.fetchone())
        interaction={'buy_request_id':request_id,'match_id':match_id,'property_id':property_id,'property_visit_id':visit['id'],'interaction_type':'visit_scheduled','reason_code':data.get('reason_code'),'notes':data.get('notes'),'occurred_at':data.get('occurred_at'),'created_by':data.get('created_by')}
        if interaction['occurred_at'] is None:interaction.pop('occurred_at')
        cols=list(interaction)
        cur.execute(f"INSERT INTO buy_request_interactions({','.join(cols)}) VALUES({','.join(['%s']*len(cols))}) RETURNING *",list(interaction.values()));result=row(cur.fetchone())
        cur.execute('UPDATE matches SET commercial_status=%s,last_reviewed_at=NOW(),updated_at=NOW() WHERE id=%s',(MATCH_STATUS['visit_scheduled'],match_id))
        history(cur,request_id,HISTORY_EVENT['visit_scheduled'],result.get('notes') or 'visit_scheduled',match_id=match_id,property_id=property_id,reason_code=result.get('reason_code'),new_value={'interaction_type':'visit_scheduled'})
        return result

def update_interaction(interaction_id,data):
    data=dict(data)
    if not data:raise ValidationError('no fields to update')
    with core_cursor(commit=True) as (_,cur):
        cur.execute(f"UPDATE buy_request_interactions SET {','.join(f'{k}=%s' for k in data)},updated_at=NOW() WHERE id=%s RETURNING *",list(data.values())+[interaction_id]);r=cur.fetchone()
        if not r:raise NotFoundError(f'interaction {interaction_id} not found')
        return dict(r)

def delete_interaction(interaction_id):
    with core_cursor(commit=True) as (_,cur):
        cur.execute('DELETE FROM buy_request_interactions WHERE id=%s RETURNING buy_request_id',(interaction_id,));r=cur.fetchone()
        if not r:raise NotFoundError(f'interaction {interaction_id} not found')

def create_task(request_id,data):
    data=dict(data)
    with core_cursor(commit=True) as (_,cur):
        cur.execute('SELECT contact_id,lead_id FROM buy_requests WHERE id=%s',(request_id,));b=cur.fetchone()
        if not b:raise NotFoundError(f'buy request {request_id} not found')
        cur.execute("""INSERT INTO tasks(contact_id,lead_id,title,description,task_type,priority,status,due_at,assigned_to,created_by,metadata)
        VALUES(%s,%s,%s,%s,%s,%s,'open',%s,%s,%s,%s) RETURNING *""",(b['contact_id'],b['lead_id'],data['title'],data.get('description'),data.get('task_type'),data.get('priority','normal'),data.get('due_at'),data.get('assigned_to'),data.get('created_by'),Json({'buy_request_id':request_id})))
        task=dict(cur.fetchone());cur.execute('INSERT INTO buy_request_task_links(buy_request_id,task_id) VALUES(%s,%s) RETURNING id',(request_id,task['id']));link_id=cur.fetchone()['id'];task['link_id']=link_id
        history(cur,request_id,'task_created',task['title'],task_id=task['id'],new_value={'due_at':str(task.get('due_at')) if task.get('due_at') else None,'priority':task['priority']})
        return task

def list_tasks(request_id):
    with core_cursor() as (_,cur):
        cur.execute("""SELECT l.id link_id,t.* FROM buy_request_task_links l JOIN tasks t ON t.id=l.task_id WHERE l.buy_request_id=%s ORDER BY CASE t.status WHEN 'open' THEN 1 WHEN 'in_progress' THEN 2 ELSE 3 END,t.due_at NULLS LAST,t.id DESC""",(request_id,));return [dict(x) for x in cur.fetchall()]

def unlink_task(link_id):
    with core_cursor(commit=True) as (_,cur):
        cur.execute('DELETE FROM buy_request_task_links WHERE id=%s RETURNING buy_request_id,task_id',(link_id,));r=cur.fetchone()
        if not r:raise NotFoundError(f'task link {link_id} not found')
        history(cur,r['buy_request_id'],'task_unlinked','Task scollegato',task_id=r['task_id'])

def add_note(request_id,description,created_by=None):
    with core_cursor(commit=True) as (_,cur):
        ensure(cur,'buy_requests',request_id,'buy request');history(cur,request_id,'note',description,created_by=created_by);return {'ok':True}

def workflow(request_id):
    data=get_request(request_id)
    with core_cursor() as (_,cur):
        cur.execute("""SELECT i.*,p.title property_title,p.code property_code FROM buy_request_interactions i LEFT JOIN properties p ON p.id=i.property_id WHERE i.buy_request_id=%s ORDER BY i.occurred_at DESC,i.id DESC""",(request_id,));data['interactions']=[dict(x) for x in cur.fetchall()]
        cur.execute("""SELECT h.*,p.title property_title,t.title task_title FROM buy_request_history h LEFT JOIN properties p ON p.id=h.property_id LEFT JOIN tasks t ON t.id=h.task_id WHERE h.buy_request_id=%s ORDER BY h.created_at DESC,h.id DESC LIMIT 200""",(request_id,));data['history']=[dict(x) for x in cur.fetchall()]
    data['matches']=list_matches(request_id);data['tasks']=list_tasks(request_id);return data

def dashboard():
    with core_cursor() as (_,cur):
        cur.execute("""SELECT COUNT(*) FILTER(WHERE archived_at IS NULL) total,COUNT(*) FILTER(WHERE status='active' AND archived_at IS NULL) active,COUNT(*) FILTER(WHERE status='draft' AND archived_at IS NULL) draft,
        COUNT(*) FILTER(WHERE priority IN ('high','urgent') AND status='active' AND archived_at IS NULL) priority,
        COUNT(*) FILTER(WHERE next_action_at IS NOT NULL AND next_action_at<NOW() AND status='active' AND archived_at IS NULL) overdue_actions,
        COUNT(*) FILTER(WHERE next_action_at::date=CURRENT_DATE AND status='active' AND archived_at IS NULL) actions_today,
        COALESCE(SUM(budget_target) FILTER(WHERE status='active' AND archived_at IS NULL),0) active_target_budget FROM buy_requests""");kpi=dict(cur.fetchone())
        cur.execute("SELECT interaction_type,COUNT(*) count FROM buy_request_interactions GROUP BY interaction_type");kpi['interaction_counts']={x['interaction_type']:x['count'] for x in cur.fetchall()}
        cur.execute("""SELECT b.id,b.title,b.status,b.priority,b.urgency,b.budget_target,b.next_action_at,b.next_action_note,c.display_name contact_name FROM buy_requests b JOIN contacts c ON c.id=b.contact_id WHERE b.archived_at IS NULL ORDER BY CASE WHEN b.next_action_at IS NOT NULL AND b.next_action_at<NOW() THEN 0 ELSE 1 END,b.next_action_at NULLS LAST,b.updated_at DESC LIMIT 12""");kpi['recent']=[dict(x) for x in cur.fetchall()];return kpi


# P26-2D scoped HTTP runtime surface

from core.repository import create_task_with_cursor as core_create_task_with_cursor
from core.scope import ProgrammingError


def _agency(ctx) -> int:
    return ctx.require_agency()


def _reject_server_owned(data):
    if "agency_id" in data:
        raise ProgrammingError(
            "'agency_id' is derived from the agency scope and must not be supplied"
        )


def _ensure_agency_row(cur, table, item_id, agency_id, label):
    cur.execute(
        f"SELECT id FROM {table} WHERE id=%s AND agency_id=%s",
        (item_id, agency_id),
    )
    if not cur.fetchone():
        raise NotFoundError(f"{label} {item_id} not found")


def _ensure_buy(cur, request_id, agency_id, *, for_update=False):
    suffix = " FOR UPDATE" if for_update else ""
    cur.execute(
        f"SELECT * FROM buy_requests WHERE id=%s AND agency_id=%s{suffix}",
        (request_id, agency_id),
    )
    result = cur.fetchone()
    if not result:
        raise NotFoundError(f"buy request {request_id} not found")
    return dict(result)


def _ensure_match(cur, request_id, match_id, agency_id, *, for_update=False):
    suffix = " FOR UPDATE OF m" if for_update else ""
    cur.execute(
        f"""
        SELECT m.*, p.agency_id AS property_agency_id
        FROM matches m
        JOIN properties p ON p.id=m.property_id
        WHERE m.id=%s
          AND m.buy_request_id=%s
          AND p.agency_id=%s
        {suffix}
        """,
        (match_id, request_id, agency_id),
    )
    result = cur.fetchone()
    if not result:
        raise NotFoundError(f"match {match_id} not found")
    return dict(result)


_legacy_history = history


def history(
    cur,
    request_id,
    event_type,
    description=None,
    old_value=None,
    new_value=None,
    match_id=None,
    property_id=None,
    task_id=None,
    reason_code=None,
    created_by=None,
    *,
    agency_id=None,
):
    """Backward-compatible BUY history writer.

    Legacy Proposal/Sale callers keep the historical signature. P26-2D scoped
    BUY callers pass agency_id explicitly so references are checked before the
    insert. Migration 039 supplies the DB-level invariant for all callers.
    """
    if agency_id is not None:
        _ensure_buy(cur, request_id, agency_id)
        match_property_id = None
        if match_id is not None:
            match = _ensure_match(cur, request_id, match_id, agency_id)
            match_property_id = match["property_id"]
        if property_id is not None:
            _ensure_agency_row(cur, "properties", property_id, agency_id, "property")
            if match_property_id is not None and property_id != match_property_id:
                raise ValidationError("history property does not match match property")
        if task_id is not None:
            _ensure_agency_row(cur, "tasks", task_id, agency_id, "task")
    return _legacy_history(
        cur,
        request_id,
        event_type,
        description=description,
        old_value=old_value,
        new_value=new_value,
        match_id=match_id,
        property_id=property_id,
        task_id=task_id,
        reason_code=reason_code,
        created_by=created_by,
    )


def create_request_scoped(ctx, data):
    agency_id = _agency(ctx)
    data = dict(data)
    _reject_server_owned(data)
    data["metadata"] = Json(data.get("metadata") or {})
    data["agency_id"] = agency_id
    with core_cursor(commit=True) as (_, cur):
        _ensure_agency_row(cur, "contacts", data["contact_id"], agency_id, "contact")
        if data.get("lead_id") is not None:
            _ensure_agency_row(cur, "leads", data["lead_id"], agency_id, "lead")
        cols = list(data)
        cur.execute(
            f"INSERT INTO buy_requests({','.join(cols)}) "
            f"VALUES({','.join(['%s'] * len(cols))}) RETURNING *",
            list(data.values()),
        )
        result = row(cur.fetchone())
        history(
            cur,
            result["id"],
            "request_created",
            "Richiesta BUY creata",
            new_value={"status": result["status"], "title": result["title"]},
            agency_id=agency_id,
        )
        return result


def list_requests_scoped(
    ctx, limit, offset, search, status, priority, urgency,
    contact_id, lead_id, assigned_to
):
    agency_id = _agency(ctx)
    filters = ["b.archived_at IS NULL", "b.agency_id=%s"]
    params = [agency_id]
    if search:
        filters.append(
            "(b.title ILIKE %s OR c.display_name ILIKE %s OR "
            "c.email ILIKE %s OR c.phone ILIKE %s)"
        )
        params += [f"%{search}%"] * 4
    if status:
        filters.append("b.status=%s"); params.append(status)
    if priority:
        filters.append("b.priority=%s"); params.append(priority)
    if urgency:
        filters.append("b.urgency=%s"); params.append(urgency)
    if contact_id:
        filters.append("b.contact_id=%s"); params.append(contact_id)
    if lead_id:
        filters.append("b.lead_id=%s"); params.append(lead_id)
    if assigned_to:
        filters.append("b.assigned_to=%s"); params.append(assigned_to)
    params += [limit, offset]
    with core_cursor() as (_, cur):
        cur.execute(
            f"""
            SELECT b.*,c.display_name AS contact_name,c.email AS contact_email,
                   c.phone AS contact_phone,l.pipeline AS lead_pipeline,l.stage AS lead_stage,
              (SELECT COUNT(*) FROM buy_request_locations x WHERE x.buy_request_id=b.id) locations_count,
              (SELECT COUNT(*) FROM buy_request_typologies x WHERE x.buy_request_id=b.id) typologies_count,
              (SELECT COUNT(*) FROM buy_request_features x WHERE x.buy_request_id=b.id) features_count,
              (SELECT COUNT(*) FROM matches m
                JOIN properties mp ON mp.id=m.property_id
               WHERE m.buy_request_id=b.id AND m.archived_at IS NULL
                 AND mp.agency_id=b.agency_id) matches_count,
              (SELECT COUNT(*) FROM matches m
                JOIN properties mp ON mp.id=m.property_id
               WHERE m.buy_request_id=b.id AND m.archived_at IS NULL
                 AND mp.agency_id=b.agency_id
                 AND m.match_class IN ('excellent','strong')) strong_matches_count,
              (SELECT COUNT(*) FROM buy_request_task_links lnk
                JOIN tasks t ON t.id=lnk.task_id
               WHERE lnk.buy_request_id=b.id
                 AND t.agency_id=b.agency_id
                 AND t.status IN ('open','in_progress')) open_tasks_count
            FROM buy_requests b
            JOIN contacts c ON c.id=b.contact_id AND c.agency_id=b.agency_id
            LEFT JOIN leads l ON l.id=b.lead_id AND l.agency_id=b.agency_id
            WHERE {' AND '.join(filters)}
            ORDER BY CASE WHEN b.next_action_at IS NOT NULL AND b.next_action_at<NOW()
                          THEN 0 ELSE 1 END,
                     b.next_action_at NULLS LAST,b.updated_at DESC
            LIMIT %s OFFSET %s
            """,
            params,
        )
        return [dict(x) for x in cur.fetchall()]


def get_request_scoped(ctx, request_id):
    agency_id = _agency(ctx)
    with core_cursor() as (_, cur):
        cur.execute(
            """
            SELECT b.*,c.display_name contact_name,c.email contact_email,
                   c.phone contact_phone,l.pipeline lead_pipeline,l.stage lead_stage
            FROM buy_requests b
            JOIN contacts c ON c.id=b.contact_id AND c.agency_id=b.agency_id
            LEFT JOIN leads l ON l.id=b.lead_id AND l.agency_id=b.agency_id
            WHERE b.id=%s AND b.agency_id=%s
            """,
            (request_id, agency_id),
        )
        result = cur.fetchone()
        if not result:
            raise NotFoundError(f"buy request {request_id} not found")
        data = dict(result)
        for key, table in (
            ("locations", "buy_request_locations"),
            ("typologies", "buy_request_typologies"),
            ("features", "buy_request_features"),
        ):
            cur.execute(
                f"SELECT * FROM {table} WHERE buy_request_id=%s ORDER BY id",
                (request_id,),
            )
            data[key] = [dict(x) for x in cur.fetchall()]
        return data


def update_request_scoped(ctx, request_id, data):
    agency_id = _agency(ctx)
    data = dict(data)
    _reject_server_owned(data)
    if not data:
        return get_request_scoped(ctx, request_id)
    if "metadata" in data:
        data["metadata"] = Json(data.get("metadata") or {})
    with core_cursor(commit=True) as (_, cur):
        old = _ensure_buy(cur, request_id, agency_id, for_update=True)
        if data.get("lead_id") is not None:
            _ensure_agency_row(cur, "leads", data["lead_id"], agency_id, "lead")
        relevant = bool(RELEVANT_FIELDS.intersection(data))
        assignments = [f"{k}=%s" for k in data]
        if relevant:
            assignments.append("match_relevant_updated_at=NOW()")
        assignments.append("updated_at=NOW()")
        cur.execute(
            f"UPDATE buy_requests SET {','.join(assignments)} "
            "WHERE id=%s AND agency_id=%s RETURNING *",
            list(data.values()) + [request_id, agency_id],
        )
        result = row(cur.fetchone())
        if not result:
            raise NotFoundError(f"buy request {request_id} not found")
        changed = {
            k: {"old": old.get(k), "new": result.get(k)}
            for k in data if old.get(k) != result.get(k)
        }
        if changed:
            event = (
                "status_changed" if "status" in changed else
                "finance_updated" if FINANCE_FIELDS.intersection(changed) else
                "next_action_updated" if NEXT_ACTION_FIELDS.intersection(changed) else
                "request_updated"
            )
            history(
                cur, request_id, event, "Richiesta BUY aggiornata",
                old_value={k: v["old"] for k, v in changed.items()},
                new_value={k: v["new"] for k, v in changed.items()},
                agency_id=agency_id,
            )
        return result


def archive_request_scoped(ctx, request_id):
    agency_id = _agency(ctx)
    with core_cursor(commit=True) as (_, cur):
        cur.execute(
            """
            UPDATE buy_requests
               SET status='archived',archived_at=NOW(),updated_at=NOW(),
                   match_relevant_updated_at=NOW()
             WHERE id=%s AND agency_id=%s RETURNING *
            """,
            (request_id, agency_id),
        )
        result = cur.fetchone()
        if not result:
            raise NotFoundError(f"buy request {request_id} not found")
        history(
            cur, request_id, "status_changed", "Richiesta archiviata",
            new_value={"status": "archived"}, agency_id=agency_id
        )
        return row(result)


def add_child_scoped(ctx, table, request_id, data):
    agency_id = _agency(ctx)
    data = dict(data)
    with core_cursor(commit=True) as (_, cur):
        _ensure_buy(cur, request_id, agency_id)
        data["buy_request_id"] = request_id
        cols = list(data)
        try:
            cur.execute(
                f"INSERT INTO {table}({','.join(cols)}) "
                f"VALUES({','.join(['%s'] * len(cols))}) RETURNING *",
                list(data.values()),
            )
        except errors.UniqueViolation as exc:
            raise ConflictError("criterion already exists for this request") from exc
        result = row(cur.fetchone())
        cur.execute(
            "UPDATE buy_requests SET match_relevant_updated_at=NOW(),updated_at=NOW() "
            "WHERE id=%s AND agency_id=%s",
            (request_id, agency_id),
        )
        return result


def delete_child_scoped(ctx, table, item_id, label):
    agency_id = _agency(ctx)
    with core_cursor(commit=True) as (_, cur):
        cur.execute(
            f"""
            DELETE FROM {table} c
            USING buy_requests b
            WHERE c.buy_request_id=b.id
              AND c.id=%s
              AND b.agency_id=%s
            RETURNING c.buy_request_id
            """,
            (item_id, agency_id),
        )
        result = cur.fetchone()
        if not result:
            raise NotFoundError(f"{label} {item_id} not found")
        cur.execute(
            "UPDATE buy_requests SET match_relevant_updated_at=NOW(),updated_at=NOW() "
            "WHERE id=%s AND agency_id=%s",
            (result["buy_request_id"], agency_id),
        )


def normalized_scoped(ctx, request_id):
    data = get_request_scoped(ctx, request_id)
    return {
        "buy_request_id": data["id"],
        "status": data["status"],
        "contact_id": data["contact_id"],
        "lead_id": data.get("lead_id"),
        "budget": {k: data.get(k) for k in (
            "budget_min","budget_target","budget_max","budget_flexibility_percent",
            "includes_agency_fees","includes_renovation"
        )},
        "finance": {k: data.get(k) for k in (
            "finance_status","mortgage_required","mortgage_preapproved",
            "available_cash","maximum_monthly_payment","property_to_sell_first"
        )},
        "dimensions": {k: data.get(k) for k in (
            "surface_min","surface_target","surface_max","rooms_min",
            "bedrooms_min","bathrooms_min"
        )},
        "locations": data["locations"],
        "typologies": data["typologies"],
        "features": data["features"],
        "match_relevant_updated_at": data["match_relevant_updated_at"],
    }


def list_matches_scoped(ctx, request_id):
    agency_id = _agency(ctx)
    with core_cursor() as (_, cur):
        _ensure_buy(cur, request_id, agency_id)
        cur.execute(
            """
            SELECT m.*,COALESCE(m.manual_score,m.score_total) effective_score,
                   p.title property_title,p.code property_code,p.city,p.microzone,
                   p.asking_price,p.classification,p.commercial_status property_status,
              (SELECT i.interaction_type
                 FROM buy_request_interactions i
                WHERE i.buy_request_id=m.buy_request_id AND i.match_id=m.id
                ORDER BY i.occurred_at DESC,i.id DESC LIMIT 1) last_interaction,
              (SELECT i.reason_code
                 FROM buy_request_interactions i
                WHERE i.buy_request_id=m.buy_request_id AND i.match_id=m.id
                ORDER BY i.occurred_at DESC,i.id DESC LIMIT 1) last_reason
            FROM matches m
            JOIN properties p ON p.id=m.property_id
            WHERE m.buy_request_id=%s
              AND p.agency_id=%s
              AND m.archived_at IS NULL
            ORDER BY effective_score DESC,m.updated_at DESC
            """,
            (request_id, agency_id),
        )
        return [dict(x) for x in cur.fetchall()]


def add_interaction_scoped(ctx, request_id, data):
    agency_id = _agency(ctx)
    data = dict(data)
    with core_cursor(commit=True) as (_, cur):
        _ensure_buy(cur, request_id, agency_id, for_update=True)
        match_id = data.get("match_id")
        property_id = data.get("property_id")
        property_visit_id = data.get("property_visit_id")

        if match_id:
            match = _ensure_match(cur, request_id, match_id, agency_id, for_update=True)
            property_id = match["property_id"]
            data["property_id"] = property_id
        elif property_id:
            _ensure_agency_row(cur, "properties", property_id, agency_id, "property")

        if property_visit_id is not None:
            if not match_id:
                raise ValidationError("match_id is required when property_visit_id is provided")
            cur.execute(
                """
                SELECT v.property_id
                FROM property_visits v
                JOIN properties p ON p.id=v.property_id
                WHERE v.id=%s AND p.agency_id=%s
                FOR UPDATE OF v
                """,
                (property_visit_id, agency_id),
            )
            visit = cur.fetchone()
            if not visit:
                raise NotFoundError(f"property visit {property_visit_id} not found")
            if visit["property_id"] != property_id:
                raise ValidationError("property visit does not belong to match property")
            cur.execute(
                """
                SELECT id FROM buy_request_interactions
                WHERE property_visit_id=%s AND buy_request_id=%s
                  AND match_id=%s AND property_id=%s
                  AND interaction_type='visit_scheduled'
                ORDER BY occurred_at DESC,id DESC LIMIT 1
                """,
                (property_visit_id, request_id, match_id, property_id),
            )
            if not cur.fetchone():
                raise ValidationError("property visit is not linked to buy request and match")

        if data.get("occurred_at") is None:
            data.pop("occurred_at", None)
        data["buy_request_id"] = request_id
        cols = list(data)
        cur.execute(
            f"INSERT INTO buy_request_interactions({','.join(cols)}) "
            f"VALUES({','.join(['%s'] * len(cols))}) RETURNING *",
            list(data.values()),
        )
        result = row(cur.fetchone())
        action = result["interaction_type"]
        if match_id and action in MATCH_STATUS:
            cur.execute(
                """
                UPDATE matches
                   SET commercial_status=%s,last_reviewed_at=NOW(),updated_at=NOW()
                 WHERE id=%s AND buy_request_id=%s
                """,
                (MATCH_STATUS[action], match_id, request_id),
            )
        history(
            cur, request_id, HISTORY_EVENT[action], result.get("notes") or action,
            match_id=match_id, property_id=property_id,
            reason_code=result.get("reason_code"),
            new_value={"interaction_type": action}, agency_id=agency_id
        )
        return result


def schedule_match_visit_scoped(ctx, request_id, match_id, data):
    agency_id = _agency(ctx)
    data = dict(data)
    scheduled_at = data.pop("scheduled_at", None)
    if scheduled_at is None:
        raise ValidationError("scheduled_at is required when scheduling a visit")
    with core_cursor(commit=True) as (_, cur):
        buy = _ensure_buy(cur, request_id, agency_id, for_update=True)
        match = _ensure_match(cur, request_id, match_id, agency_id, for_update=True)
        property_id = match["property_id"]
        cur.execute(
            """
            SELECT i.* FROM buy_request_interactions i
            JOIN property_visits v ON v.id=i.property_visit_id
            JOIN properties p ON p.id=v.property_id
            WHERE i.buy_request_id=%s
              AND i.match_id=%s
              AND i.interaction_type='visit_scheduled'
              AND v.scheduled_at=%s
              AND p.agency_id=%s
            ORDER BY i.id DESC LIMIT 1
            """,
            (request_id, match_id, scheduled_at, agency_id),
        )
        existing = cur.fetchone()
        if existing:
            return row(existing)

        cur.execute(
            """
            INSERT INTO property_visits(
                property_id,contact_id,lead_id,scheduled_at,status,created_by
            )
            VALUES(%s,%s,%s,%s,%s,%s) RETURNING *
            """,
            (
                property_id, buy["contact_id"], buy["lead_id"], scheduled_at,
                "scheduled", data.get("created_by")
            ),
        )
        visit = row(cur.fetchone())
        interaction = {
            "buy_request_id": request_id,
            "match_id": match_id,
            "property_id": property_id,
            "property_visit_id": visit["id"],
            "interaction_type": "visit_scheduled",
            "reason_code": data.get("reason_code"),
            "notes": data.get("notes"),
            "occurred_at": data.get("occurred_at"),
            "created_by": data.get("created_by"),
        }
        if interaction["occurred_at"] is None:
            interaction.pop("occurred_at")
        cols = list(interaction)
        cur.execute(
            f"INSERT INTO buy_request_interactions({','.join(cols)}) "
            f"VALUES({','.join(['%s'] * len(cols))}) RETURNING *",
            list(interaction.values()),
        )
        result = row(cur.fetchone())
        cur.execute(
            """
            UPDATE matches
               SET commercial_status=%s,last_reviewed_at=NOW(),updated_at=NOW()
             WHERE id=%s AND buy_request_id=%s
            """,
            (MATCH_STATUS["visit_scheduled"], match_id, request_id),
        )
        history(
            cur, request_id, HISTORY_EVENT["visit_scheduled"],
            result.get("notes") or "visit_scheduled",
            match_id=match_id, property_id=property_id,
            reason_code=result.get("reason_code"),
            new_value={"interaction_type": "visit_scheduled"}, agency_id=agency_id
        )
        return result


def update_interaction_scoped(ctx, interaction_id, data):
    agency_id = _agency(ctx)
    data = dict(data)
    if not data:
        raise ValidationError("no fields to update")
    with core_cursor(commit=True) as (_, cur):
        cur.execute(
            """
            SELECT i.*,b.agency_id
            FROM buy_request_interactions i
            JOIN buy_requests b ON b.id=i.buy_request_id
            WHERE i.id=%s AND b.agency_id=%s
            FOR UPDATE OF i
            """,
            (interaction_id, agency_id),
        )
        current = cur.fetchone()
        if not current:
            raise NotFoundError(f"interaction {interaction_id} not found")
        current = dict(current)

        if data.get("property_visit_id") is not None:
            if current.get("match_id") is None or current.get("property_id") is None:
                raise ValidationError(
                    "interaction requires match_id/property_id before linking a visit"
                )
            cur.execute(
                """
                SELECT v.property_id
                FROM property_visits v
                JOIN properties p ON p.id=v.property_id
                WHERE v.id=%s AND p.agency_id=%s
                """,
                (data["property_visit_id"], agency_id),
            )
            visit = cur.fetchone()
            if not visit:
                raise NotFoundError(f"property visit {data['property_visit_id']} not found")
            if visit["property_id"] != current["property_id"]:
                raise ValidationError("property visit does not belong to interaction property")

        cur.execute(
            f"UPDATE buy_request_interactions SET "
            f"{','.join(f'{k}=%s' for k in data)},updated_at=NOW() "
            "WHERE id=%s RETURNING *",
            list(data.values()) + [interaction_id],
        )
        result = cur.fetchone()
        if not result:
            raise NotFoundError(f"interaction {interaction_id} not found")
        return dict(result)


def delete_interaction_scoped(ctx, interaction_id):
    agency_id = _agency(ctx)
    with core_cursor(commit=True) as (_, cur):
        cur.execute(
            """
            DELETE FROM buy_request_interactions i
            USING buy_requests b
            WHERE i.buy_request_id=b.id
              AND i.id=%s
              AND b.agency_id=%s
            RETURNING i.buy_request_id
            """,
            (interaction_id, agency_id),
        )
        if not cur.fetchone():
            raise NotFoundError(f"interaction {interaction_id} not found")


def create_task_scoped(ctx, request_id, data):
    agency_id = _agency(ctx)
    data = dict(data)
    with core_cursor(commit=True) as (_, cur):
        buy = _ensure_buy(cur, request_id, agency_id)
        task_data = {
            "contact_id": buy["contact_id"],
            "lead_id": buy["lead_id"],
            "stima_id": None,
            "title": data["title"],
            "description": data.get("description"),
            "task_type": data.get("task_type"),
            "priority": data.get("priority", "normal"),
            "status": "open",
            "due_at": data.get("due_at"),
            "completed_at": None,
            "assigned_to": data.get("assigned_to"),
            "created_by": data.get("created_by"),
            "metadata": {"buy_request_id": request_id},
        }
        task = core_create_task_with_cursor(cur, task_data, ctx=ctx)
        cur.execute(
            """
            INSERT INTO buy_request_task_links(buy_request_id,task_id)
            VALUES(%s,%s) RETURNING id
            """,
            (request_id, task["id"]),
        )
        link_id = cur.fetchone()["id"]
        task["link_id"] = link_id
        history(
            cur, request_id, "task_created", task["title"],
            task_id=task["id"],
            new_value={
                "due_at": str(task.get("due_at")) if task.get("due_at") else None,
                "priority": task["priority"],
            },
            agency_id=agency_id,
        )
        return task


def list_tasks_scoped(ctx, request_id):
    agency_id = _agency(ctx)
    with core_cursor() as (_, cur):
        _ensure_buy(cur, request_id, agency_id)
        cur.execute(
            """
            SELECT l.id link_id,t.*
            FROM buy_request_task_links l
            JOIN tasks t ON t.id=l.task_id
            JOIN buy_requests b ON b.id=l.buy_request_id
            WHERE l.buy_request_id=%s
              AND b.agency_id=%s
              AND t.agency_id=%s
            ORDER BY CASE t.status WHEN 'open' THEN 1
                                   WHEN 'in_progress' THEN 2 ELSE 3 END,
                     t.due_at NULLS LAST,t.id DESC
            """,
            (request_id, agency_id, agency_id),
        )
        return [dict(x) for x in cur.fetchall()]


def unlink_task_scoped(ctx, link_id):
    agency_id = _agency(ctx)
    with core_cursor(commit=True) as (_, cur):
        cur.execute(
            """
            DELETE FROM buy_request_task_links l
            USING buy_requests b, tasks t
            WHERE l.buy_request_id=b.id
              AND l.task_id=t.id
              AND l.id=%s
              AND b.agency_id=%s
              AND t.agency_id=%s
            RETURNING l.buy_request_id,l.task_id
            """,
            (link_id, agency_id, agency_id),
        )
        result = cur.fetchone()
        if not result:
            raise NotFoundError(f"task link {link_id} not found")
        history(
            cur, result["buy_request_id"], "task_unlinked", "Task scollegato",
            task_id=result["task_id"], agency_id=agency_id
        )


def add_note_scoped(ctx, request_id, description, created_by=None):
    agency_id = _agency(ctx)
    with core_cursor(commit=True) as (_, cur):
        _ensure_buy(cur, request_id, agency_id)
        history(
            cur, request_id, "note", description,
            created_by=created_by, agency_id=agency_id
        )
        return {"ok": True}


def workflow_scoped(ctx, request_id):
    agency_id = _agency(ctx)
    data = get_request_scoped(ctx, request_id)
    with core_cursor() as (_, cur):
        cur.execute(
            """
            SELECT i.*,p.title property_title,p.code property_code
            FROM buy_request_interactions i
            LEFT JOIN properties p
              ON p.id=i.property_id AND p.agency_id=%s
            WHERE i.buy_request_id=%s
              AND (i.property_id IS NULL OR p.id IS NOT NULL)
            ORDER BY i.occurred_at DESC,i.id DESC
            """,
            (agency_id, request_id),
        )
        data["interactions"] = [dict(x) for x in cur.fetchall()]
        cur.execute(
            """
            SELECT h.*,p.title property_title,t.title task_title
            FROM buy_request_history h
            LEFT JOIN properties p
              ON p.id=h.property_id AND p.agency_id=%s
            LEFT JOIN tasks t
              ON t.id=h.task_id AND t.agency_id=%s
            WHERE h.buy_request_id=%s
              AND (h.property_id IS NULL OR p.id IS NOT NULL)
              AND (h.task_id IS NULL OR t.id IS NOT NULL)
            ORDER BY h.created_at DESC,h.id DESC LIMIT 200
            """,
            (agency_id, agency_id, request_id),
        )
        data["history"] = [dict(x) for x in cur.fetchall()]
    data["matches"] = list_matches_scoped(ctx, request_id)
    data["tasks"] = list_tasks_scoped(ctx, request_id)
    return data


def dashboard_scoped(ctx):
    agency_id = _agency(ctx)
    with core_cursor() as (_, cur):
        cur.execute(
            """
            SELECT COUNT(*) FILTER(WHERE archived_at IS NULL) total,
                   COUNT(*) FILTER(WHERE status='active' AND archived_at IS NULL) active,
                   COUNT(*) FILTER(WHERE status='draft' AND archived_at IS NULL) draft,
                   COUNT(*) FILTER(WHERE priority IN ('high','urgent')
                                    AND status='active' AND archived_at IS NULL) priority,
                   COUNT(*) FILTER(WHERE next_action_at IS NOT NULL
                                    AND next_action_at<NOW()
                                    AND status='active' AND archived_at IS NULL) overdue_actions,
                   COUNT(*) FILTER(WHERE next_action_at::date=CURRENT_DATE
                                    AND status='active' AND archived_at IS NULL) actions_today,
                   COALESCE(SUM(budget_target) FILTER(
                       WHERE status='active' AND archived_at IS NULL
                   ),0) active_target_budget
            FROM buy_requests
            WHERE agency_id=%s
            """,
            (agency_id,),
        )
        kpi = dict(cur.fetchone())
        cur.execute(
            """
            SELECT i.interaction_type,COUNT(*) count
            FROM buy_request_interactions i
            JOIN buy_requests b ON b.id=i.buy_request_id
            WHERE b.agency_id=%s
            GROUP BY i.interaction_type
            """,
            (agency_id,),
        )
        kpi["interaction_counts"] = {
            x["interaction_type"]: x["count"] for x in cur.fetchall()
        }
        cur.execute(
            """
            SELECT b.id,b.title,b.status,b.priority,b.urgency,b.budget_target,
                   b.next_action_at,b.next_action_note,c.display_name contact_name
            FROM buy_requests b
            JOIN contacts c ON c.id=b.contact_id AND c.agency_id=b.agency_id
            WHERE b.archived_at IS NULL AND b.agency_id=%s
            ORDER BY CASE WHEN b.next_action_at IS NOT NULL AND b.next_action_at<NOW()
                          THEN 0 ELSE 1 END,
                     b.next_action_at NULLS LAST,b.updated_at DESC LIMIT 12
            """,
            (agency_id,),
        )
        kpi["recent"] = [dict(x) for x in cur.fetchall()]
        return kpi
