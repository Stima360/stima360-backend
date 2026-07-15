from __future__ import annotations
from typing import Any
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
        return row(cur.fetchone())

def list_properties(limit,offset,search,status,classification,city,contact_id,lead_id):
    filters=[]; params=[]; joins=[]
    if search: filters.append("(p.title ILIKE %s OR p.code ILIKE %s OR p.address ILIKE %s OR p.city ILIKE %s)"); params += [f'%{search}%']*4
    if status: filters.append('p.commercial_status=%s'); params.append(status)
    if classification: filters.append('p.classification=%s'); params.append(classification)
    if city: filters.append('p.city ILIKE %s'); params.append(f'%{city}%')
    if contact_id: joins.append('JOIN property_contacts pc_filter ON pc_filter.property_id=p.id'); filters.append('pc_filter.contact_id=%s'); params.append(contact_id)
    if lead_id: joins.append('JOIN property_leads pl_filter ON pl_filter.property_id=p.id'); filters.append('pl_filter.lead_id=%s'); params.append(lead_id)
    where=' WHERE '+' AND '.join(filters) if filters else ''
    params += [limit,offset]
    with core_cursor() as (_,cur):
        cur.execute(f"SELECT DISTINCT p.* FROM properties p {' '.join(joins)}{where} ORDER BY p.updated_at DESC,p.id DESC LIMIT %s OFFSET %s",params)
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
        return p

def update_property(property_id,data):
    if not data:return get_property(property_id)
    if 'metadata' in data:data['metadata']=Json(data.get('metadata') or {})
    with core_cursor(commit=True) as (_,cur):
        cur.execute(f"UPDATE properties SET {','.join(f'{k}=%s' for k in data)},updated_at=NOW() WHERE id=%s RETURNING *",list(data.values())+[property_id]); r=cur.fetchone()
        if not r: raise NotFoundError(f'property {property_id} not found')
        return row(r)

def archive_property(property_id):
    with core_cursor(commit=True) as (_,cur):
        cur.execute("UPDATE properties SET commercial_status='archived',archived_at=NOW(),updated_at=NOW() WHERE id=%s RETURNING *",(property_id,)); r=cur.fetchone()
        if not r: raise NotFoundError(f'property {property_id} not found')
        return row(r)

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

def list_visits(limit,offset,status,from_date,to_date):
    filters=[];params=[]
    if status: filters.append('v.status=%s');params.append(status)
    if from_date: filters.append('v.scheduled_at >= %s');params.append(from_date)
    if to_date: filters.append('v.scheduled_at <= %s');params.append(to_date)
    where=' WHERE '+' AND '.join(filters) if filters else ''
    params += [limit,offset]
    with core_cursor() as (_,cur):
        cur.execute(f"SELECT v.*,p.title AS property_title,p.code AS property_code FROM property_visits v JOIN properties p ON p.id=v.property_id{where} ORDER BY v.scheduled_at DESC,v.id DESC LIMIT %s OFFSET %s",params)
        return [dict(x) for x in cur.fetchall()]

def update_visit(visit_id,data):
    if not data:
        with core_cursor() as (_,cur):cur.execute('SELECT * FROM property_visits WHERE id=%s',(visit_id,));r=cur.fetchone();
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
