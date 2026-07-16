from __future__ import annotations
from psycopg2 import errors
from psycopg2.extras import Json
from core.database import core_cursor
from core.exceptions import NotFoundError, ConflictError, ValidationError

RELEVANT_FIELDS={'budget_min','budget_target','budget_max','budget_flexibility_percent','includes_agency_fees','includes_renovation','finance_status','mortgage_required','mortgage_preapproved','available_cash','maximum_monthly_payment','property_to_sell_first','surface_min','surface_target','surface_max','rooms_min','bedrooms_min','bathrooms_min','status','urgency','target_purchase_date'}
FINANCE_FIELDS={'finance_status','mortgage_required','mortgage_preapproved','available_cash','maximum_monthly_payment','property_to_sell_first','finance_review_at','finance_notes'}
NEXT_ACTION_FIELDS={'next_action_at','next_action_note'}
MATCH_STATUS={'proposed':'suggested','discarded':'rejected','interested':'interested','visit_requested':'visit_requested','visit_scheduled':'visit_requested','visited':'visited','offer_candidate':'interested'}
HISTORY_EVENT={'proposed':'match_proposed','discarded':'match_discarded','interested':'match_interested','visit_requested':'visit_requested','visit_scheduled':'visit_scheduled','visited':'visited','offer_candidate':'offer_candidate','other':'note'}

def row(value): return dict(value) if value else None

def ensure(cur,table,item_id,label):
    cur.execute(f'SELECT id FROM {table} WHERE id=%s',(item_id,))
    if not cur.fetchone(): raise NotFoundError(f'{label} {item_id} not found')

def history(cur, request_id, event_type, description=None, old_value=None, new_value=None, match_id=None, property_id=None, task_id=None, reason_code=None, created_by=None):
    cur.execute("""INSERT INTO buy_request_history(buy_request_id,event_type,match_id,property_id,task_id,reason_code,description,old_value,new_value,created_by)
    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",(request_id,event_type,match_id,property_id,task_id,reason_code,description,Json(old_value) if old_value is not None else None,Json(new_value) if new_value is not None else None,created_by))

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
        ensure(cur,'buy_requests',request_id,'buy request')
        match_id=data.get('match_id');property_id=data.get('property_id')
        if match_id:
            cur.execute('SELECT property_id,buy_request_id FROM matches WHERE id=%s',(match_id,));m=cur.fetchone()
            if not m:raise NotFoundError(f'match {match_id} not found')
            if m['buy_request_id']!=request_id:raise ValidationError('match does not belong to buy request')
            property_id=m['property_id'];data['property_id']=property_id
        elif property_id:ensure(cur,'properties',property_id,'property')
        if data.get('occurred_at') is None:data.pop('occurred_at',None)
        data['buy_request_id']=request_id;cols=list(data)
        cur.execute(f"INSERT INTO buy_request_interactions({','.join(cols)}) VALUES({','.join(['%s']*len(cols))}) RETURNING *",list(data.values()));result=row(cur.fetchone())
        action=result['interaction_type']
        if match_id and action in MATCH_STATUS:
            cur.execute('UPDATE matches SET commercial_status=%s,last_reviewed_at=NOW(),updated_at=NOW() WHERE id=%s',(MATCH_STATUS[action],match_id))
        history(cur,request_id,HISTORY_EVENT[action],result.get('notes') or action,match_id=match_id,property_id=property_id,reason_code=result.get('reason_code'),new_value={'interaction_type':action})
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
