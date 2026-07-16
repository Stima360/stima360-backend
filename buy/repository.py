from __future__ import annotations
from psycopg2 import errors
from psycopg2.extras import Json
from core.database import core_cursor
from core.exceptions import NotFoundError, ConflictError

RELEVANT_FIELDS={
 'budget_min','budget_target','budget_max','budget_flexibility_percent','includes_agency_fees','includes_renovation',
 'finance_status','mortgage_required','mortgage_preapproved','available_cash','maximum_monthly_payment','property_to_sell_first',
 'surface_min','surface_target','surface_max','rooms_min','bedrooms_min','bathrooms_min','status','urgency','target_purchase_date'
}

def row(value): return dict(value) if value else None

def ensure(cur,table,item_id,label):
    cur.execute(f'SELECT id FROM {table} WHERE id=%s',(item_id,))
    if not cur.fetchone(): raise NotFoundError(f'{label} {item_id} not found')

def create_request(data):
    data=dict(data); data['metadata']=Json(data.get('metadata') or {})
    with core_cursor(commit=True) as (_,cur):
        ensure(cur,'contacts',data['contact_id'],'contact')
        if data.get('lead_id') is not None: ensure(cur,'leads',data['lead_id'],'lead')
        cols=list(data)
        cur.execute(f"INSERT INTO buy_requests({','.join(cols)}) VALUES({','.join(['%s']*len(cols))}) RETURNING *",list(data.values()))
        return row(cur.fetchone())

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
        cur.execute(f"""SELECT b.*,c.display_name AS contact_name,c.email AS contact_email,c.phone AS contact_phone,
        l.pipeline AS lead_pipeline,l.stage AS lead_stage,
        (SELECT COUNT(*) FROM buy_request_locations x WHERE x.buy_request_id=b.id) AS locations_count,
        (SELECT COUNT(*) FROM buy_request_typologies x WHERE x.buy_request_id=b.id) AS typologies_count,
        (SELECT COUNT(*) FROM buy_request_features x WHERE x.buy_request_id=b.id) AS features_count
        FROM buy_requests b JOIN contacts c ON c.id=b.contact_id LEFT JOIN leads l ON l.id=b.lead_id
        WHERE {' AND '.join(filters)} ORDER BY b.updated_at DESC,b.id DESC LIMIT %s OFFSET %s""",params)
        return [dict(x) for x in cur.fetchall()]

def get_request(request_id):
    with core_cursor() as (_,cur):
        cur.execute("""SELECT b.*,c.display_name AS contact_name,c.email AS contact_email,c.phone AS contact_phone,
        l.pipeline AS lead_pipeline,l.stage AS lead_stage FROM buy_requests b JOIN contacts c ON c.id=b.contact_id
        LEFT JOIN leads l ON l.id=b.lead_id WHERE b.id=%s""",(request_id,)); result=cur.fetchone()
        if not result: raise NotFoundError(f'buy request {request_id} not found')
        data=dict(result)
        for key,table in [('locations','buy_request_locations'),('typologies','buy_request_typologies'),('features','buy_request_features')]:
            cur.execute(f'SELECT * FROM {table} WHERE buy_request_id=%s ORDER BY id',(request_id,)); data[key]=[dict(x) for x in cur.fetchall()]
        return data

def update_request(request_id,data):
    data=dict(data)
    if not data: return get_request(request_id)
    if 'metadata' in data: data['metadata']=Json(data.get('metadata') or {})
    with core_cursor(commit=True) as (_,cur):
        if data.get('lead_id') is not None: ensure(cur,'leads',data['lead_id'],'lead')
        relevant=bool(RELEVANT_FIELDS.intersection(data))
        assignments=[f'{k}=%s' for k in data]
        if relevant: assignments.append('match_relevant_updated_at=NOW()')
        assignments.append('updated_at=NOW()')
        cur.execute(f"UPDATE buy_requests SET {','.join(assignments)} WHERE id=%s RETURNING *",list(data.values())+[request_id]); result=cur.fetchone()
        if not result: raise NotFoundError(f'buy request {request_id} not found')
        return row(result)

def archive_request(request_id):
    with core_cursor(commit=True) as (_,cur):
        cur.execute("UPDATE buy_requests SET status='archived',archived_at=NOW(),updated_at=NOW(),match_relevant_updated_at=NOW() WHERE id=%s RETURNING *",(request_id,)); result=cur.fetchone()
        if not result: raise NotFoundError(f'buy request {request_id} not found')
        return row(result)

def add_child(table,request_id,data):
    data=dict(data)
    with core_cursor(commit=True) as (_,cur):
        ensure(cur,'buy_requests',request_id,'buy request')
        data['buy_request_id']=request_id; cols=list(data)
        try: cur.execute(f"INSERT INTO {table}({','.join(cols)}) VALUES({','.join(['%s']*len(cols))}) RETURNING *",list(data.values()))
        except errors.UniqueViolation as exc: raise ConflictError('criterion already exists for this request') from exc
        result=row(cur.fetchone())
        cur.execute('UPDATE buy_requests SET match_relevant_updated_at=NOW(),updated_at=NOW() WHERE id=%s',(request_id,))
        return result

def delete_child(table,item_id,label):
    with core_cursor(commit=True) as (_,cur):
        cur.execute(f'DELETE FROM {table} WHERE id=%s RETURNING buy_request_id',(item_id,)); result=cur.fetchone()
        if not result: raise NotFoundError(f'{label} {item_id} not found')
        cur.execute('UPDATE buy_requests SET match_relevant_updated_at=NOW(),updated_at=NOW() WHERE id=%s',(result['buy_request_id'],))

def normalized(request_id):
    data=get_request(request_id)
    return {
      'buy_request_id':data['id'],'status':data['status'],'contact_id':data['contact_id'],'lead_id':data.get('lead_id'),
      'budget':{k:data.get(k) for k in ('budget_min','budget_target','budget_max','budget_flexibility_percent','includes_agency_fees','includes_renovation')},
      'finance':{k:data.get(k) for k in ('finance_status','mortgage_required','mortgage_preapproved','available_cash','maximum_monthly_payment','property_to_sell_first')},
      'dimensions':{k:data.get(k) for k in ('surface_min','surface_target','surface_max','rooms_min','bedrooms_min','bathrooms_min')},
      'locations':data['locations'],'typologies':data['typologies'],'features':data['features'],
      'match_relevant_updated_at':data['match_relevant_updated_at']
    }

def dashboard():
    with core_cursor() as (_,cur):
        cur.execute("""SELECT COUNT(*) FILTER(WHERE archived_at IS NULL) total,
        COUNT(*) FILTER(WHERE status='active' AND archived_at IS NULL) active,
        COUNT(*) FILTER(WHERE status='draft' AND archived_at IS NULL) draft,
        COUNT(*) FILTER(WHERE priority IN ('high','urgent') AND status='active' AND archived_at IS NULL) priority,
        COUNT(*) FILTER(WHERE target_purchase_date IS NOT NULL AND target_purchase_date<=CURRENT_DATE+INTERVAL '30 days' AND status='active' AND archived_at IS NULL) expiring,
        COALESCE(SUM(budget_target) FILTER(WHERE status='active' AND archived_at IS NULL),0) active_target_budget
        FROM buy_requests"""); kpi=dict(cur.fetchone())
        cur.execute("""SELECT b.id,b.title,b.status,b.priority,b.urgency,b.budget_target,b.budget_max,b.target_purchase_date,c.display_name contact_name
        FROM buy_requests b JOIN contacts c ON c.id=b.contact_id WHERE b.archived_at IS NULL
        ORDER BY CASE b.priority WHEN 'urgent' THEN 1 WHEN 'high' THEN 2 WHEN 'normal' THEN 3 ELSE 4 END,b.updated_at DESC LIMIT 10""")
        kpi['recent']=[dict(x) for x in cur.fetchall()]
        return kpi
