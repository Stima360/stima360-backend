from __future__ import annotations
from psycopg2 import errors
from psycopg2.extras import Json
from core.database import core_cursor
from core.exceptions import ConflictError, NotFoundError

def row(x): return dict(x) if x else None

def ensure(cur, table, id_, label):
    cur.execute(f"SELECT 1 FROM {table} WHERE id=%s",(id_,))
    if not cur.fetchone(): raise NotFoundError(f"{label} {id_} not found")

def create_property(data):
    data={**data,'metadata':Json(data.get('metadata') or {})}
    cols=list(data); vals=[data[x] for x in cols]
    with core_cursor(commit=True) as (_,cur):
        try:
            cur.execute(f"INSERT INTO properties ({','.join(cols)}) VALUES ({','.join(['%s']*len(cols))}) RETURNING *",vals)
        except errors.UniqueViolation as exc: raise ConflictError('property code already exists') from exc
        created=row(cur.fetchone())
        if created.get('asking_price') is not None:
            cur.execute("INSERT INTO property_price_history(property_id,new_price,change_reason) VALUES(%s,%s,%s)",(created['id'],created['asking_price'],'initial price'))
        if created.get('commercial_status'):
            cur.execute("INSERT INTO property_status_history(property_id,field_name,new_value,note) VALUES(%s,'commercial_status',%s,%s)",(created['id'],created['commercial_status'],'initial status'))
        if created.get('classification'):
            cur.execute("INSERT INTO property_status_history(property_id,field_name,new_value,note) VALUES(%s,'classification',%s,%s)",(created['id'],created['classification'],'initial classification'))
        return created

def list_properties(limit,offset,search,status,classification,city,contact_id,lead_id,assigned_to,mandate_expiring,missing_documents):
    filters=[]; params=[]; joins=[]
    if search: filters.append("(p.title ILIKE %s OR p.code ILIKE %s OR p.address ILIKE %s OR p.city ILIKE %s)"); params += [f'%{search}%']*4
    if status: filters.append('p.commercial_status=%s'); params.append(status)
    if classification: filters.append('p.classification=%s'); params.append(classification)
    if city: filters.append('p.city ILIKE %s'); params.append(f'%{city}%')
    if assigned_to: filters.append('p.assigned_to ILIKE %s'); params.append(f'%{assigned_to}%')
    if contact_id: joins.append('JOIN property_contacts pc_filter ON pc_filter.property_id=p.id'); filters.append('pc_filter.contact_id=%s'); params.append(contact_id)
    if lead_id: joins.append('JOIN property_leads pl_filter ON pl_filter.property_id=p.id'); filters.append('pl_filter.lead_id=%s'); params.append(lead_id)
    if mandate_expiring: filters.append("p.mandate_end IS NOT NULL AND p.mandate_end BETWEEN CURRENT_DATE AND CURRENT_DATE + INTERVAL '30 days'")
    if missing_documents: filters.append("EXISTS (SELECT 1 FROM property_documents pd WHERE pd.property_id=p.id AND pd.status IN ('missing','requested','expired','rejected'))")
    where=' WHERE '+' AND '.join(filters) if filters else ''
    params += [limit,offset]
    with core_cursor() as (_,cur):
        cur.execute(f"""
            SELECT DISTINCT p.*,
              (SELECT COUNT(*) FROM property_documents pd WHERE pd.property_id=p.id AND pd.status IN ('missing','requested','expired','rejected')) AS document_issues,
              (SELECT COUNT(*) FROM property_visits pv WHERE pv.property_id=p.id AND pv.status IN ('scheduled','confirmed') AND pv.scheduled_at >= NOW()) AS upcoming_visits
            FROM properties p {' '.join(joins)}{where}
            ORDER BY p.updated_at DESC,p.id DESC LIMIT %s OFFSET %s
        """,params)
        return [dict(x) for x in cur.fetchall()]

def get_property(property_id):
    with core_cursor() as (_,cur):
        cur.execute('SELECT * FROM properties WHERE id=%s',(property_id,)); p=row(cur.fetchone())
        if not p: raise NotFoundError(f'property {property_id} not found')
        cur.execute('SELECT pc.*,c.display_name,c.email,c.phone FROM property_contacts pc JOIN contacts c ON c.id=pc.contact_id WHERE pc.property_id=%s ORDER BY pc.is_primary DESC,pc.id',(property_id,)); p['contacts']=[dict(x) for x in cur.fetchall()]
        cur.execute('SELECT pl.*,l.pipeline,l.stage,l.status,l.contact_id FROM property_leads pl JOIN leads l ON l.id=pl.lead_id WHERE pl.property_id=%s ORDER BY pl.id',(property_id,)); p['leads']=[dict(x) for x in cur.fetchall()]
        for table,key in [('property_documents','documents'),('property_photos','photos'),('property_visits','visits')]:
            order='sort_order,id' if table=='property_photos' else ('scheduled_at DESC,id DESC' if table=='property_visits' else 'created_at DESC,id DESC')
            cur.execute(f'SELECT * FROM {table} WHERE property_id=%s ORDER BY {order}',(property_id,)); p[key]=[dict(x) for x in cur.fetchall()]
        cur.execute('SELECT * FROM property_price_history WHERE property_id=%s ORDER BY created_at DESC,id DESC',(property_id,)); p['price_history']=[dict(x) for x in cur.fetchall()]
        cur.execute('SELECT * FROM property_status_history WHERE property_id=%s ORDER BY created_at DESC,id DESC',(property_id,)); p['status_history']=[dict(x) for x in cur.fetchall()]
        p['document_issues']=sum(1 for x in p['documents'] if x['status'] in {'missing','requested','expired','rejected'})
        p['readiness_score']=readiness_score(p)
        return p

def readiness_score(p):
    checks=[bool(p.get('title')),bool(p.get('city')),bool(p.get('surface_sqm')),bool(p.get('asking_price')),bool(p.get('contacts')),bool(p.get('photos'))]
    docs=p.get('documents') or []
    checks.append(bool(docs) and not any(x['status'] in {'missing','requested','expired','rejected'} for x in docs))
    checks.append(bool(p.get('classification')))
    return round(sum(checks)/len(checks)*100)

def update_property(property_id,data):
    if not data:return get_property(property_id)
    change_reason=data.pop('change_reason',None); changed_by=data.pop('changed_by',None); history_note=data.pop('history_note',None)
    if 'metadata' in data:data['metadata']=Json(data.get('metadata') or {})
    with core_cursor(commit=True) as (_,cur):
        cur.execute('SELECT asking_price,commercial_status,classification FROM properties WHERE id=%s FOR UPDATE',(property_id,)); old=cur.fetchone()
        if not old: raise NotFoundError(f'property {property_id} not found')
        old=dict(old)
        cur.execute(f"UPDATE properties SET {','.join(f'{k}=%s' for k in data)},updated_at=NOW() WHERE id=%s RETURNING *",list(data.values())+[property_id]); r=row(cur.fetchone())
        if 'asking_price' in data and data['asking_price'] != old.get('asking_price'):
            cur.execute("INSERT INTO property_price_history(property_id,old_price,new_price,change_reason,changed_by) VALUES(%s,%s,%s,%s,%s)",(property_id,old.get('asking_price'),data['asking_price'],change_reason,changed_by))
        for field in ('commercial_status','classification'):
            if field in data and data[field] != old.get(field):
                cur.execute("INSERT INTO property_status_history(property_id,field_name,old_value,new_value,note,changed_by) VALUES(%s,%s,%s,%s,%s,%s)",(property_id,field,old.get(field),data[field],history_note,changed_by))
        return r

def archive_property(property_id):
    return update_property(property_id,{'commercial_status':'archived','archived_at':__import__('datetime').datetime.now(__import__('datetime').timezone.utc),'history_note':'archived from admin'})

def add_contact(property_id,data):
    with core_cursor(commit=True) as (_,cur):
        ensure(cur,'properties',property_id,'property'); ensure(cur,'contacts',data['contact_id'],'contact')
        if data.get('is_primary'): cur.execute("UPDATE property_contacts SET is_primary=FALSE WHERE property_id=%s AND role=%s",(property_id,data['role']))
        try:
            cur.execute("INSERT INTO property_contacts(property_id,contact_id,role,is_primary,ownership_share,notes) VALUES(%s,%s,%s,%s,%s,%s) RETURNING *",(property_id,data['contact_id'],data['role'],data.get('is_primary',False),data.get('ownership_share'),data.get('notes')))
        except errors.UniqueViolation as exc: raise ConflictError('contact already linked with this role') from exc
        return row(cur.fetchone())

def delete_contact(property_id,contact_id,role):
    with core_cursor(commit=True) as (_,cur):
        cur.execute('DELETE FROM property_contacts WHERE property_id=%s AND contact_id=%s AND role=%s',(property_id,contact_id,role))
        if not cur.rowcount: raise NotFoundError('property contact link not found')

def add_lead(property_id,data):
    with core_cursor(commit=True) as (_,cur):
        ensure(cur,'properties',property_id,'property');ensure(cur,'leads',data['lead_id'],'lead')
        try:cur.execute('INSERT INTO property_leads(property_id,lead_id,relation_type) VALUES(%s,%s,%s) RETURNING *',(property_id,data['lead_id'],data['relation_type']))
        except errors.UniqueViolation as exc: raise ConflictError('lead already linked') from exc
        return row(cur.fetchone())

def delete_lead(property_id,lead_id):
    with core_cursor(commit=True) as (_,cur):
        cur.execute('DELETE FROM property_leads WHERE property_id=%s AND lead_id=%s',(property_id,lead_id))
        if not cur.rowcount: raise NotFoundError('property lead link not found')

def create_child(table,property_id,data):
    with core_cursor(commit=True) as (_,cur):
        ensure(cur,'properties',property_id,'property')
        if table in {'property_documents','property_photos'} and 'metadata' in data:data['metadata']=Json(data.get('metadata') or {})
        if table=='property_photos' and data.get('is_cover'):cur.execute('UPDATE property_photos SET is_cover=FALSE WHERE property_id=%s',(property_id,))
        data={**data,'property_id':property_id}; cols=list(data)
        cur.execute(f"INSERT INTO {table}({','.join(cols)}) VALUES({','.join(['%s']*len(cols))}) RETURNING *",list(data.values()))
        return row(cur.fetchone())

def update_child(table,item_id,data,label):
    if not data:
        with core_cursor() as (_,cur):cur.execute(f'SELECT * FROM {table} WHERE id=%s',(item_id,));r=cur.fetchone()
        if not r:raise NotFoundError(f'{label} {item_id} not found')
        return row(r)
    with core_cursor(commit=True) as (_,cur):
        if table in {'property_documents','property_photos'} and 'metadata' in data:data['metadata']=Json(data.get('metadata') or {})
        if table=='property_photos' and data.get('is_cover'):
            cur.execute('SELECT property_id FROM property_photos WHERE id=%s',(item_id,)); pr=cur.fetchone()
            if not pr:raise NotFoundError(f'{label} {item_id} not found')
            cur.execute('UPDATE property_photos SET is_cover=FALSE WHERE property_id=%s',(pr['property_id'],))
        extra=',updated_at=NOW()' if table=='property_documents' else ''
        cur.execute(f"UPDATE {table} SET {','.join(f'{k}=%s' for k in data)}{extra} WHERE id=%s RETURNING *",list(data.values())+[item_id]);r=cur.fetchone()
        if not r:raise NotFoundError(f'{label} {item_id} not found')
        return row(r)

def list_visits(limit,offset,status,from_date,to_date):
    filters=[];params=[]
    if status: filters.append('v.status=%s');params.append(status)
    if from_date: filters.append('v.scheduled_at >= %s');params.append(from_date)
    if to_date: filters.append('v.scheduled_at <= %s');params.append(to_date)
    where=' WHERE '+' AND '.join(filters) if filters else ''
    params += [limit,offset]
    with core_cursor() as (_,cur):
        cur.execute(f"SELECT v.*,p.title AS property_title,p.code AS property_code,c.display_name AS contact_name FROM property_visits v JOIN properties p ON p.id=v.property_id LEFT JOIN contacts c ON c.id=v.contact_id{where} ORDER BY v.scheduled_at DESC,v.id DESC LIMIT %s OFFSET %s",params)
        return [dict(x) for x in cur.fetchall()]

def update_visit(visit_id,data):
    if not data:
        with core_cursor() as (_,cur):cur.execute('SELECT * FROM property_visits WHERE id=%s',(visit_id,));r=cur.fetchone()
        if not r:raise NotFoundError(f'visit {visit_id} not found')
        return row(r)
    with core_cursor(commit=True) as (_,cur):
        cur.execute(f"UPDATE property_visits SET {','.join(f'{k}=%s' for k in data)},updated_at=NOW() WHERE id=%s RETURNING *",list(data.values())+[visit_id]);r=cur.fetchone()
        if not r:raise NotFoundError(f'visit {visit_id} not found')
        return row(r)

def delete_child(table,item_id,label):
    with core_cursor(commit=True) as (_,cur):
        cur.execute(f'DELETE FROM {table} WHERE id=%s',(item_id,))
        if not cur.rowcount:raise NotFoundError(f'{label} {item_id} not found')

def dashboard():
    with core_cursor() as (_,cur):
        cur.execute("""
        SELECT
          COUNT(*) FILTER (WHERE archived_at IS NULL) AS total,
          COUNT(*) FILTER (WHERE commercial_status IN ('mandate','active','reserved','under_offer')) AS active,
          COUNT(*) FILTER (WHERE classification='A') AS class_a,
          COUNT(*) FILTER (WHERE classification='B') AS class_b,
          COUNT(*) FILTER (WHERE classification='C') AS class_c,
          COALESCE(SUM(asking_price) FILTER (WHERE commercial_status IN ('mandate','active','reserved','under_offer')),0) AS active_value,
          COUNT(*) FILTER (WHERE mandate_end BETWEEN CURRENT_DATE AND CURRENT_DATE + INTERVAL '30 days') AS expiring_mandates
        FROM properties
        """); kpi=dict(cur.fetchone())
        cur.execute("SELECT COUNT(*) AS count FROM property_documents WHERE status IN ('missing','requested','expired','rejected')"); kpi['document_issues']=cur.fetchone()['count']
        cur.execute("SELECT COUNT(*) AS count FROM property_visits WHERE status IN ('scheduled','confirmed') AND scheduled_at::date=CURRENT_DATE"); kpi['visits_today']=cur.fetchone()['count']
        cur.execute("SELECT COUNT(*) AS count FROM property_visits WHERE status IN ('scheduled','confirmed') AND scheduled_at > NOW()"); kpi['upcoming_visits']=cur.fetchone()['count']
        cur.execute("""SELECT p.id,p.code,p.title,p.commercial_status,p.classification,p.mandate_end,p.asking_price,
          (SELECT COUNT(*) FROM property_documents d WHERE d.property_id=p.id AND d.status IN ('missing','requested','expired','rejected')) AS document_issues
          FROM properties p WHERE p.archived_at IS NULL ORDER BY p.updated_at DESC LIMIT 8"""); kpi['recent_properties']=[dict(x) for x in cur.fetchall()]
        cur.execute("""SELECT v.*,p.title AS property_title FROM property_visits v JOIN properties p ON p.id=v.property_id
          WHERE v.status IN ('scheduled','confirmed') AND v.scheduled_at>=NOW() ORDER BY v.scheduled_at LIMIT 8"""); kpi['next_visits']=[dict(x) for x in cur.fetchall()]
        return kpi

def alerts():
    with core_cursor() as (_,cur):
        cur.execute("""
        SELECT 'mandate' AS alert_type,p.id AS property_id,p.title,p.code,p.mandate_end AS due_date,
               'Incarico in scadenza' AS message
        FROM properties p
        WHERE p.mandate_end BETWEEN CURRENT_DATE AND CURRENT_DATE + INTERVAL '30 days'
        UNION ALL
        SELECT 'document',p.id,p.title,p.code,d.expires_at,
               'Documento: '||d.title||' ('||d.status||')'
        FROM property_documents d JOIN properties p ON p.id=d.property_id
        WHERE d.status IN ('missing','requested','expired','rejected')
           OR (d.expires_at IS NOT NULL AND d.expires_at <= CURRENT_DATE + INTERVAL '30 days')
        UNION ALL
        SELECT 'visit',p.id,p.title,p.code,v.scheduled_at::date,
               'Visita '||v.status||' alle '||to_char(v.scheduled_at,'DD/MM/YYYY HH24:MI')
        FROM property_visits v JOIN properties p ON p.id=v.property_id
        WHERE v.status IN ('scheduled','confirmed') AND v.scheduled_at BETWEEN NOW() AND NOW()+INTERVAL '7 days'
        ORDER BY due_date NULLS LAST
        """)
        return {'items':[dict(x) for x in cur.fetchall()]}
