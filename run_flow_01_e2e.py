#!/usr/bin/env python3
"""Isolated live E2E for FLOW 0.1. Run only in stima360-backend-test Shell."""
import os, uuid, json
from urllib.parse import urlparse
import requests, psycopg2
from psycopg2.extras import RealDictCursor, Json

PREFIX="E2E_FLOW01_"
RUN=PREFIX+uuid.uuid4().hex[:10]
BASE=os.getenv("FLOW_E2E_BASE_URL","https://stima360-backend-test.onrender.com").rstrip('/')
created={"contact":None,"lead":None,"execution":None}
rule_backup=None

class Fail(Exception): pass
def check(cond,msg):
    if not cond: raise Fail(msg)
    print("  OK ",msg)
def api(method,path,body=None,expected=(200,201,204)):
    r=requests.request(method,BASE+path,json=body,timeout=45)
    if r.status_code not in expected: raise Fail(f"{method} {path}: HTTP {r.status_code} {r.text[:500]}")
    return None if r.status_code==204 else r.json()

def conn():
    name=os.environ["DB_NAME"]
    if "test" not in name.lower(): raise SystemExit("BLOCCATO: DB_NAME non è test")
    return psycopg2.connect(host=os.environ["DB_HOST"],port=os.getenv("DB_PORT","5432"),dbname=name,user=os.environ["DB_USER"],password=os.environ["DB_PASSWORD"])

def setup():
    global rule_backup
    c=conn(); cur=c.cursor(cursor_factory=RealDictCursor)
    cur.execute("INSERT INTO contacts(contact_type,display_name,status,source) VALUES('person',%s,'active','flow_e2e') RETURNING id",(RUN,)); created['contact']=cur.fetchone()['id']
    cur.execute("INSERT INTO leads(contact_id,source,pipeline,stage,priority,status,notes,created_at,updated_at) VALUES(%s,'flow_e2e','general','new','normal','open',%s,NOW()-INTERVAL '48 hours',NOW()-INTERVAL '48 hours') RETURNING id",(created['contact'],RUN)); created['lead']=cur.fetchone()['id']
    c.commit(); cur.close(); c.close()
    api('POST','/api/flow/sync-rules')
    c=conn(); cur=c.cursor(cursor_factory=RealDictCursor); cur.execute("SELECT * FROM flow_rules WHERE code='FLOW-R001'"); rule_backup=dict(cur.fetchone()); cur.close(); c.close()

def cleanup():
    try:
        c=conn(); cur=c.cursor()
        cur.execute("DELETE FROM tasks WHERE metadata->>'source'='flow' AND metadata->>'flow_rule_code'='FLOW-R001' AND lead_id=%s",(created['lead'],))
        cur.execute("DELETE FROM flow_executions WHERE entity_type='lead' AND entity_id=%s",(created['lead'],))
        cur.execute("DELETE FROM flow_events WHERE entity_type='lead' AND entity_id=%s",(created['lead'],))
        if rule_backup:
            cur.execute("""UPDATE flow_rules SET code_version=%s,name=%s,description=%s,event_type=%s,entity_type=%s,is_active=%s,priority=%s,cooldown_minutes=%s,parameters=%s,default_parameters=%s,allowed_parameters=%s,last_simulation_at=%s,last_simulation_status=%s,last_simulation_execution_id=%s,last_simulation_parameters_hash=%s,last_simulation_rule_version=%s,activated_at=%s,activated_by=%s,updated_at=%s,archived_at=%s WHERE code='FLOW-R001'""",
            (rule_backup['code_version'],rule_backup['name'],rule_backup['description'],rule_backup['event_type'],rule_backup['entity_type'],rule_backup['is_active'],rule_backup['priority'],rule_backup['cooldown_minutes'],Json(rule_backup['parameters']),Json(rule_backup['default_parameters']),Json(rule_backup['allowed_parameters']),rule_backup['last_simulation_at'],rule_backup['last_simulation_status'],rule_backup['last_simulation_execution_id'],rule_backup['last_simulation_parameters_hash'],rule_backup['last_simulation_rule_version'],rule_backup['activated_at'],rule_backup['activated_by'],rule_backup['updated_at'],rule_backup['archived_at']))
        if created['lead']: cur.execute("DELETE FROM leads WHERE id=%s AND notes=%s",(created['lead'],RUN))
        if created['contact']: cur.execute("DELETE FROM contacts WHERE id=%s AND display_name=%s",(created['contact'],RUN))
        c.commit(); cur.close(); c.close(); print("Pulizia dati FLOW E2E: completata")
    except Exception as e: print("ATTENZIONE pulizia:",e)

def activate_without_simulation():
    api('POST','/api/flow/rules/FLOW-R001/deactivate')
    api('PATCH','/api/flow/rules/FLOW-R001/parameters',{'parameters':{'inactivity_hours':24,'task_priority':'high','cooldown_minutes':1440}})
    r=requests.post(BASE+'/api/flow/rules/FLOW-R001/activate',json={},timeout=30)
    check(r.status_code==409,"attivazione bloccata senza simulazione valida")

def simulation_and_live():
    sim=api('POST','/api/flow/rules/FLOW-R001/simulate',{'entity_type':'lead','entity_id':created['lead'],'requested_by':RUN})
    check(sim['execution_mode']=='simulation' and sim['status']=='matched',"simulazione positiva senza azioni reali")
    c=conn(); cur=c.cursor(); cur.execute("SELECT COUNT(*) FROM tasks WHERE lead_id=%s AND metadata->>'source'='flow'",(created['lead'],)); check(cur.fetchone()[0]==0,"simulazione non crea task CORE"); cur.close(); c.close()
    api('POST','/api/flow/rules/FLOW-R001/activate',{'activated_by':RUN})
    out=api('POST','/api/flow/events',{'event_type':'core.lead_created','entity_type':'lead','entity_id':created['lead'],'source_module':'core','payload':{'run':RUN},'deduplication_key':RUN})
    check(len(out['executions'])==1 and out['executions'][0]['status']=='executed',"evento live crea una sola esecuzione")
    created['execution']=out['executions'][0]['id']
    c=conn(); cur=c.cursor(); cur.execute("SELECT COUNT(*) FROM tasks WHERE lead_id=%s AND metadata->>'flow_rule_code'='FLOW-R001'",(created['lead'],)); check(cur.fetchone()[0]==1,"task CORE creato e riconoscibile"); cur.close(); c.close()
    out2=api('POST','/api/flow/events',{'event_type':'core.lead_created','entity_type':'lead','entity_id':created['lead'],'source_module':'core','payload':{'run':RUN},'deduplication_key':RUN})
    c=conn(); cur=c.cursor(); cur.execute("SELECT COUNT(*) FROM tasks WHERE lead_id=%s AND metadata->>'flow_rule_code'='FLOW-R001'",(created['lead'],)); check(cur.fetchone()[0]==1,"idempotenza: nessun secondo task"); cur.close(); c.close()

def invalidation():
    api('POST','/api/flow/rules/FLOW-R001/deactivate')
    r=api('PATCH','/api/flow/rules/FLOW-R001/parameters',{'parameters':{'inactivity_hours':25,'task_priority':'high','cooldown_minutes':1440}})
    check(r['last_simulation_status']=='outdated',"modifica parametri invalida la simulazione")
    rr=requests.post(BASE+'/api/flow/rules/FLOW-R001/activate',json={},timeout=30); check(rr.status_code==409,"riattivazione bloccata dopo modifica parametri")

def retry_limit():
    c=conn(); cur=c.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT id,parameters,code_version FROM flow_rules WHERE code='FLOW-R001'"); r=cur.fetchone()
    cur.execute("""INSERT INTO flow_executions(rule_id,entity_type,entity_id,execution_mode,status,conditions_result,actions_result,rule_version,parameters_snapshot,parameters_hash,error_message,retry_count,max_retry,started_at,completed_at,created_at) VALUES(%s,'lead',%s,'live','failed','{}','{}',%s,%s,%s,'forced E2E',3,3,NOW(),NOW(),NOW()) RETURNING id""",(r['id'],created['lead'],r['code_version'],Json(r['parameters']),'0'*64)); eid=cur.fetchone()['id']; c.commit(); cur.close(); c.close()
    rr=requests.post(BASE+f'/api/flow/executions/{eid}/retry',json={'requested_by':RUN},timeout=30)
    check(rr.status_code==409,"retry_limit: quarto tentativo bloccato dopo retry_count=3")

def main():
    print('='*72); print('STIMA360 — FLOW 0.1 E2E TEST',RUN); print('='*72)
    host=urlparse(BASE).hostname or ''
    if 'test' not in host.lower(): raise SystemExit('BLOCCATO: endpoint non test')
    check('test' in host.lower(),f'endpoint test confermato: {host}')
    check('test' in os.environ['DB_NAME'].lower(),f"database test confermato: {os.environ['DB_NAME']}")
    try:
        setup(); activate_without_simulation(); simulation_and_live(); invalidation(); retry_limit()
        print('\nFLOW 0.1 VALIDATO — TUTTI I TEST E2E SONO PASSATI')
        print('Produzione non toccata. Dati E2E rimossi al termine.')
    finally: cleanup()
if __name__=='__main__': main()
