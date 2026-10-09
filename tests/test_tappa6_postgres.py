"""Tappa 6, solo PostgreSQL locale usa-e-getta, schema e trigger reali."""
from pathlib import Path
import pytest
from tests.test_censimento_3_backend_postgres import completo
from operator_auth.context import OperatorContext
ROOT=Path(__file__).resolve().parents[1]

def test_t14_v2_draft_idempotent_preserves_v1(completo):
    from communication.journey_catalog import ensure_stima_lead_v1, ensure_stima_lead_v2
    c=completo
    ctx=OperatorContext(user_id=c['ids']['owner_a'],agency_id=1,role='agency_owner',is_platform_admin=False,session_id=None,auth_channel='operator_session')
    conn=c['psycopg2'].connect(c['dsn'],cursor_factory=c['psycopg2'].extras.RealDictCursor)
    try:
        with conn.cursor() as cur:
            v1=ensure_stima_lead_v1(ctx,cur=cur)
            from communication import journey_service
            journey_service.activate_journey(ctx,v1['journey']['id'],cur=cur)
            journey_service.retire_journey(ctx,v1['journey']['id'],cur=cur)
            v1=ensure_stima_lead_v1(ctx,cur=cur)
            assert v1['journey']['status']=='retired'
            v2=ensure_stima_lead_v2(ctx,cur=cur)
            again=ensure_stima_lead_v2(ctx,cur=cur)
            assert v2['journey']['status']=='draft'
            assert len(v2['journey']['steps'])==10
            assert again['created'] is False
            assert ensure_stima_lead_v1(ctx,cur=cur)['journey']==v1['journey']
    finally:
        conn.rollback();conn.close()


def test_t14_down_up_preserves_schema(completo):
    conn=completo['psycopg2'].connect(completo['dsn'])
    try:
        with conn.cursor() as cur:
            cur.execute((ROOT/'migrations/097_tappa6_request_followup_down.sql').read_text().replace('BEGIN;', '').replace('COMMIT;', ''))
            # Backfill must use a CRM ledger, exclude baseline and leave unknown
            # pre-existing registrations NULL rather than invent migration time.
            cur.execute("INSERT INTO stime(agency_id,comune,data) VALUES(1,'Backfill',NOW()-interval '90 days') RETURNING id")
            known = cur.fetchone()[0]
            cur.execute("INSERT INTO stime(agency_id,comune,data) VALUES(1,'Unknown',NOW()-interval '90 days') RETURNING id")
            unknown = cur.fetchone()[0]
            cur.execute("INSERT INTO stime(agency_id,comune,data) VALUES(1,'Historical',NOW()-interval '90 days') RETURNING id")
            historical = cur.fetchone()[0]
            origin = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)
            for sid in (known, historical):
                cur.execute("INSERT INTO site_import_records(source_table,source_id,crm_id,agency_id,created_at) VALUES('stime',%s,%s,1,%s)", (sid, sid, origin))
            cur.execute("INSERT INTO site_import_baselines(source,ids) VALUES('t6_backfill',%s)",
                        (Json({'stime': [historical], 'stime_dettagliate': []}),))
            cur.execute((ROOT/'migrations/097_tappa6_request_followup.sql').read_text())
            cur.execute("SELECT id,crm_registered_at FROM stime WHERE id IN (%s,%s,%s)", (known, unknown, historical))
            stamps = dict(cur.fetchall())
            assert stamps == {known: origin, unknown: None, historical: None}
            cur.execute("SELECT to_regclass('communication_pdf_ready_events')")
            assert cur.fetchone()[0] is None
            cur.execute("SELECT column_default FROM information_schema.columns WHERE table_name='stime' AND column_name='crm_registered_at'")
            assert 'clock_timestamp' in cur.fetchone()[0]
    finally:
        conn.rollback();conn.close()

from datetime import datetime, timedelta, timezone
import hashlib, uuid
from psycopg2.extras import RealDictCursor, Json

@pytest.fixture
def world(completo, monkeypatch):
    from tests.test_p29_3c_orchestrator_postgres import modulo
    modules=modulo.__wrapped__(completo,monkeypatch)
    c=completo
    conn=c['psycopg2'].connect(c['dsn'],cursor_factory=RealDictCursor)
    conn.autocommit=True
    now=datetime(2026,10,9,10,tzinfo=timezone.utc)
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return now if tz else now.replace(tzinfo=None)
    from communication import repository as ledger
    monkeypatch.setattr(ledger,'datetime',Clock)
    monkeypatch.setattr(modules['tick'],'utcnow',lambda:now)
    monkeypatch.setattr(modules['repo'],'utcnow',lambda:now)
    def q(sql,p=None):
        with conn.cursor() as cur:
            cur.execute(sql,p)
            return [dict(x) for x in cur.fetchall()] if cur.description else None
    ctx=OperatorContext(user_id=c['ids']['owner_a'],agency_id=1,role='agency_owner',is_platform_admin=False,session_id=None,auth_channel='operator_session')
    from communication.journey_catalog import ensure_stima_lead_v2
    j=ensure_stima_lead_v2(ctx)['journey']
    if j['status']=='draft':modules['journeys'].activate_journey(ctx,j['id'])
    def contact(consent=True):
        name=uuid.uuid4().hex[:10]
        ct=q("INSERT INTO contacts(agency_id,first_name,display_name,email,email_normalized) VALUES(1,'Anna',%s,%s,%s) RETURNING id",(name,name+'@example.invalid',name+'@example.invalid'))[0]['id']
        if consent:
            q("INSERT INTO consent_events(agency_id,contact_id,purpose,decision,decided_at,source,actor_type) VALUES(1,%s,'marketing','granted',NOW(),'crm','subject')",(ct,))
            q("UPDATE contacts SET marketing_consent=TRUE,marketing_consent_at=NOW(),marketing_consent_source='crm' WHERE id=%s",(ct,))
        return ct
    def request(contact_id=None,age=2,baseline=False,pdf_status=None):
        ct=contact_id or contact()
        lead=q("INSERT INTO leads(agency_id,contact_id) VALUES(1,%s) RETURNING id",(ct,))[0]['id']
        st=q("INSERT INTO stime(agency_id,comune,crm_registered_at,data) VALUES(1,'Alba',%s,%s) RETURNING id",(now-timedelta(days=age),now-timedelta(days=100)))[0]['id']
        q("INSERT INTO lead_stime(lead_id,stima_id,relation_type) VALUES(%s,%s,'related')",(lead,st))
        if baseline:
            q("INSERT INTO site_import_baselines(source,ids) VALUES(%s,%s)",('test_'+uuid.uuid4().hex[:10],Json({'stime':[st]+list(range(1000000,1000095)),'stime_dettagliate':list(range(2000000,2000008))})))
        if pdf_status is not None:
            q("INSERT INTO stima_pdf_artifacts(stima_id,agency_id,status,render_payload) VALUES(%s,1,%s,'{}')",(st,pdf_status))
        return dict(contact=ct,lead=lead,stima=st)
    def enroll(p):
        result=modules['tick'].tick(ctx,now=now)
        assert result['errors']==0,result
        return q("SELECT * FROM communication_enrollments WHERE stima_id=%s",(p['stima'],))
    def messages(e):return q("SELECT * FROM communication_messages WHERE enrollment_id=%s ORDER BY id",(e['id'],))
    def claim():return modules['comm'].claim_due(ctx,provider='null',channel='email',limit=50)
    state=dict(c=c,conn=conn,q=q,ctx=ctx,now=now,request=request,contact=contact,enroll=enroll,messages=messages,claim=claim,**modules)
    yield state
    # End each synthetic case, retaining its audit until disposable DB teardown.
    q("UPDATE communication_enrollments SET status='stopped',stop_reason='contact_inactive',stopped_at=NOW(),next_step_no=NULL,next_action_at=NULL,next_action_kind=NULL,awaiting_since=NULL,paused_at=NULL,paused_source=NULL,paused_by_operator_user_id=NULL WHERE agency_id=1 AND status IN ('active','paused')")
    conn.close()


def test_t1_request_plus_two_not_before(world):
    w=world;p=w['request'](age=1);e=w['enroll'](p)[0]
    assert e['next_action_at']>=e['trigger_sent_at']+timedelta(days=2)
    assert w['messages'](e)==[]
    assert w['enroll'](p)[0]['id']==e['id']


def test_t2_actual_sent_not_queued_and_t3_rotation(world):
    w=world;e=w['enroll'](w['request']())[0]
    assert len(w['messages'](e))==1
    assert w['enroll']({'stima':e['stima_id']})[0]['next_step_no']==1
    # Real claim/finalization, fake provider acceptance only; synthetic timestamp
    # on fixture fact then exact +10 prediction through the production ADVANCE.
    sent=w['claim']()
    own=next(x for x in sent if x['message']['enrollment_id']==e['id'])
    w['comm'].finalize_sent(w['ctx'],own['message']['id'],own['message']['claim_token'])
    actual=w['messages'](e)[0]['sent_at']
    w['tick'].tick(w['ctx'],now=w['now'])
    current=w['q']('SELECT * FROM communication_enrollments WHERE id=%s',(e['id'],))[0]
    from communication.send_window import next_allowed
    window={'days':[1,2,3,4,5,6],'from':'09:00','to':'19:00'}
    assert current['next_action_at']==next_allowed(actual+timedelta(days=10),window,'Europe/Rome')
    assert current['next_step_no']==2
    # Exercise real steps 2..10 with actual finalized sends; no advances from queued.
    for step in range(2,11):
        current=w['q']('SELECT * FROM communication_enrollments WHERE id=%s',(e['id'],))[0]
        future=current['next_action_at']
        w['tick'].tick(w['ctx'],now=future)
        queued=w['messages'](e)[-1]
        # claim tests deliberately make only this fixture due (not production).
        w['q']("UPDATE communication_messages SET scheduled_at=NOW()-interval '1 second' WHERE id=%s",(queued['id'],))
        own=next(x for x in w['claim']() if x['message']['id']==queued['id'])
        w['comm'].finalize_sent(w['ctx'],queued['id'],own['message']['claim_token'])
        w['tick'].tick(w['ctx'],now=w['now'])
    current=w['q']('SELECT * FROM communication_enrollments WHERE id=%s',(e['id'],))[0]
    assert (current['next_step_no'],current['run_no'],current['status'])==(1,2,'active')
    assert len(w['messages'](e))==10
    w['tick'].tick(w['ctx'],now=w['now'])
    assert len(w['messages'](e))==10
    w['tick'].tick(w['ctx'],now=current['next_action_at'])
    again=w['messages'](e)
    assert len(again)==11 and again[-1]['step_no']==1 and again[-1]['run_no']==2
    w['tick'].tick(w['ctx'],now=current['next_action_at'])
    assert len(w['messages'](e))==11


def test_t5_old_registration_t6_baseline_pdf_retry_irrelevant(world):
    w=world
    old=w['request'](age=8);baseline=w['request'](baseline=True)
    snapshot=w['q']('SELECT source,ids,initialized_at FROM site_import_baselines ORDER BY source')
    assert any(len(row['ids']['stime'])==96 and len(row['ids']['stime_dettagliate'])==8 for row in snapshot)
    assert w['enroll'](old)==[] and w['enroll'](baseline)==[]
    fresh=w['request'](pdf_status='pending');enrollment=w['enroll'](fresh)[0]
    registered=w['q']('SELECT crm_registered_at FROM stime WHERE id=%s',(fresh['stima'],))[0]
    sha=hashlib.sha256(b'%PDF-test').hexdigest()
    w['q']("UPDATE stima_pdf_artifacts SET status='ready',pdf_bytes=%s,sha256=%s WHERE stima_id=%s",(b'%PDF-test',sha,fresh['stima']))
    w['q']("UPDATE stima_pdf_artifacts SET status='ready',updated_at=NOW() WHERE stima_id=%s",(fresh['stima'],))
    assert w['q']('SELECT crm_registered_at FROM stime WHERE id=%s',(fresh['stima'],))[0]==registered
    assert w['enroll'](fresh)[0]['id']==enrollment['id']
    assert len(w['messages'](enrollment))==1
    assert w['q']('SELECT source,ids,initialized_at FROM site_import_baselines ORDER BY source')==snapshot


def test_t9_no_consent_fails_closed(world):
    w=world;p=w['request'](contact_id=w['contact'](False));e=w['enroll'](p)[0]
    assert e['status']=='stopped' and e['stop_reason']=='consent_not_granted'
    assert w['messages'](e)==[]


def test_t10_paused_31_days_requires_operator(world):
    w=world;e=w['enroll'](w['request'](age=1))[0]
    w['journeys'].pause_enrollment(w['ctx'],e['id'])
    w['q']("UPDATE communication_enrollments SET paused_at=NOW()-interval '31 days' WHERE id=%s",(e['id'],))
    from operator_auth.context import SystemAgencyContext
    with pytest.raises(Exception):w['journeys'].resume_enrollment(SystemAgencyContext(agency_id=1,origin='test'),e['id'])
    w['tick'].tick(w['ctx'],now=w['now']+timedelta(days=31))
    assert w['q']('SELECT status FROM communication_enrollments WHERE id=%s',(e['id'],))[0]['status']=='paused'
    resumed=w['journeys'].resume_enrollment(w['ctx'],e['id'],now=w['now'])
    assert resumed['status']=='active' and resumed['next_action_at']>=w['now']+timedelta(days=10)


def _appointment(w,p,status='requested',from_id=None):
    return w['q']("""INSERT INTO appointments(agency_id,contact_id,lead_id,stima_id,appointment_type,status,start_at,end_at,source,
        cancelled_at,rescheduled_at,rescheduled_from_id) VALUES(1,%s,%s,%s,'seller_meeting',%s,%s,%s,'system',%s,%s,%s) RETURNING id""",
        (p['contact'],p['lead'],p['stima'],status,w['now']+timedelta(days=3),w['now']+timedelta(days=3,hours=1),
         w['now'] if status=='cancelled' else None,w['now'] if status=='rescheduled' else None,from_id))[0]['id']


def _stop(w,p,kind):
    q=w['q'];uid=w['c']['ids']['owner_a']
    if kind in ('acquisition','mandate'):
        prop=q("INSERT INTO properties(agency_id,title) VALUES(1,'Synthetic property') RETURNING id")[0]['id']
        q("""INSERT INTO stima_acquisitions(stima_id,stima_id_snapshot,property_id,link_status,linked_by_operator_user_id,
              mandate_signed_at,mandate_recorded_at,mandate_recorded_by_operator_user_id) VALUES(%s,%s,%s,'active',%s,%s,%s,%s)""",
          (p['stima'],p['stima'],prop,uid,w['now'] if kind=='mandate' else None,w['now'] if kind=='mandate' else None,uid if kind=='mandate' else None))
    elif kind=='inspection':
        q("INSERT INTO stima_inspections(stima_id,stima_id_snapshot,status,scheduled_for,created_by_operator_user_id) VALUES(%s,%s,'scheduled',%s,%s)",(p['stima'],p['stima'],w['now'],uid))
    elif kind=='consultation':
        q("INSERT INTO seller_timeline_events(agency_id,contact_id,stima_id,event_type,event_source,idempotency_key) VALUES(1,%s,%s,'owner_consultation_requested','owner_portal',%s)",(p['contact'],p['stima'],uuid.uuid4().hex))
    elif kind=='closed':q("UPDATE leads SET status='closed' WHERE id=%s",(p['lead'],))
    elif kind=='inactive':q("UPDATE contacts SET status='inactive' WHERE id=%s",(p['contact'],))
    elif kind=='revoked':
        q("INSERT INTO consent_events(agency_id,contact_id,purpose,decision,decided_at,source,actor_type) VALUES(1,%s,'marketing','revoked',NOW(),'crm','subject')",(p['contact'],))
        q("UPDATE contacts SET marketing_consent=FALSE,marketing_revoked_at=NOW() WHERE id=%s",(p['contact'],))
    elif kind=='appointment':_appointment(w,p)


@pytest.mark.parametrize('phase',['enroll','due','dispatch'])
@pytest.mark.parametrize('kind',['mandate','acquisition','inspection','consultation','closed','inactive','revoked','appointment'])
def test_t7_t8_t9_contact_wide_all_gates(world,phase,kind):
    w=world;p=w['request'](age=2 if phase=='dispatch' else 1)
    if phase!='enroll':e=w['enroll'](p)[0]
    if phase=='dispatch':
        claim=next(x for x in w['claim']() if x['message']['enrollment_id']==e['id'])
    other=w['request'](contact_id=p['contact'],age=1)
    _stop(w,other,kind)
    if phase=='dispatch':
        from communication import dispatcher
        class Tripwire:
            def send(self,message):raise AssertionError('provider forbidden for stopped contact')
        assert dispatcher._dispatch_request_journey(w['ctx'],claim['message'],claim['message']['claim_token'],Tripwire(),now=w['now'])=='suppressed'
        assert w['messages'](e)[0]['status']=='suppressed'
    else:
        w['tick'].tick(w['ctx'],now=w['now']+timedelta(days=1))
        e=w['q']('SELECT * FROM communication_enrollments WHERE stima_id=%s',(p['stima'],))[0]
        assert w['messages'](e)==[]
    assert w['q']('SELECT status FROM communication_enrollments WHERE id=%s',(e['id'],))[0]['status']=='stopped'


def test_t7_cancelled_and_rescheduled_predecessor_do_not_block(world):
    w=world;p=w['request']();_appointment(w,p,'cancelled');old=_appointment(w,p,'rescheduled')
    e=w['enroll'](p)[0]
    assert e['status']=='active' and len(w['messages'](e))==1
    _appointment(w,p,'requested',old)
    w['tick'].tick(w['ctx'],now=w['now'])
    assert w['messages'](e)[0]['status']=='cancelled'


def test_t11_new_valuation_only_without_blocks(world):
    w=world;p=w['request'](age=1);e=w['enroll'](p)[0]
    w['journeys'].stop_enrollment(w['ctx'],e['id'])
    second=w['request'](contact_id=p['contact'],age=1)
    assert w['enroll'](second)[0]['status']=='active'
    w['journeys'].pause_automations(w['ctx'],p['contact'])
    third=w['request'](contact_id=p['contact'],age=1)
    assert w['enroll'](third)==[]


def test_t12_indeterminate_never_resends(world):
    w=world;e=w['enroll'](w['request']())[0]
    claim=next(x for x in w['claim']() if x['message']['enrollment_id']==e['id'])
    from communication import dispatcher
    from communication.providers import null
    from communication.providers.base import ProviderResult, OUTCOME_UNKNOWN
    class Unknown:
        CAPABILITIES=null.CAPABILITIES
        calls=0
        @classmethod
        def send(cls,message):cls.calls+=1;return ProviderResult(outcome=OUTCOME_UNKNOWN)
    assert dispatcher._dispatch_request_journey(w['ctx'],claim['message'],claim['message']['claim_token'],Unknown,now=w['now'])=='indeterminate'
    w['tick'].tick(w['ctx'],now=w['now']+timedelta(days=100))
    assert len(w['messages'](e))==1 and w['messages'](e)[0]['status']=='indeterminate'
    assert w['claim']()==[] and Unknown.calls==1


def test_t5_late_first_send_stops_and_t14_registration_immutable(world):
    w=world;p=w['request'](age=1);e=w['enroll'](p)[0]
    w['tick'].tick(w['ctx'],now=w['now']+timedelta(days=8))
    after=w['q']('SELECT * FROM communication_enrollments WHERE id=%s',(e['id'],))[0]
    assert after['status']=='stopped' and after['stop_reason']=='request_too_old'
    assert w['messages'](e)==[]
    with pytest.raises(Exception):w['q']("UPDATE stime SET crm_registered_at=NOW() WHERE id=%s",(p['stima'],))
    conn=w['conn'];conn.autocommit=False
    try:
        with conn.cursor() as cur:
            with pytest.raises(Exception):cur.execute((ROOT/'migrations/097_tappa6_request_followup_down.sql').read_text().replace('BEGIN;','').replace('COMMIT;',''))
        conn.rollback()
    finally:conn.autocommit=True
    assert w['q']('SELECT * FROM communication_enrollments WHERE id=%s',(e['id'],))


def test_t13_agencies_1_35_isolated(world):
    w=world
    from communication.journey_catalog import ensure_stima_lead_v2
    from communication.exceptions import NotFoundError
    w['q']("INSERT INTO agencies(id,name,slug) VALUES(35,'STIMA360 Giulianova','synthetic-giulianova')")
    uid=w['q']("INSERT INTO operator_users(email,email_normalized,password_hash,first_name) VALUES('tappa6-35@example.invalid','tappa6-35@example.invalid','pbkdf2_sha256$1$x$y','Anna') RETURNING id")[0]['id']
    w['q']("INSERT INTO agency_memberships(agency_id,operator_user_id,role,status) VALUES(35,%s,'agency_owner','active')",(uid,))
    ctx=OperatorContext(user_id=uid,agency_id=35,role='agency_owner',is_platform_admin=False,session_id=None,auth_channel='operator_session')
    j=ensure_stima_lead_v2(ctx)['journey']
    assert j['status']=='draft' and len(j['steps'])==10
    e=w['enroll'](w['request']())[0]
    with w['conn'].cursor() as cur:
        with pytest.raises(NotFoundError):w['repo'].select_enrollment(cur,ctx,e['id'])
    result=w['tick'].tick(ctx,now=w['now'])
    assert result['enrolled_active']==0 and result['queued']==0
    conn=w['conn'];conn.autocommit=False
    try:
        with conn.cursor() as cur:
            from communication import journey_repository as repo
            repo.insert_enrollment(cur,ctx,journey_id=j['id'],contact_id=e['contact_id'],lead_id=e['lead_id'],stima_id=e['stima_id'],trigger_message_id=None,status='active',next_step_no=1,next_action_at=w['now'],next_action_kind='enqueue',stop_reason=None,actor_type='system',actor_user_id=None,idempotency_key='cross-agency-attempt')
    except Exception:conn.rollback()
    else:pytest.fail('cross agency request accepted')
    finally:conn.autocommit=True


def test_t12_concurrent_ticks_one_enrollment_one_message(world):
    import threading
    w=world;p=w['request']();barrier=threading.Barrier(2);results=[];errors=[]
    def worker():
        try:
            barrier.wait(timeout=5)
            results.append(w['tick'].tick(w['ctx'],now=w['now']))
        except Exception as exc:errors.append(exc)
    threads=[threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:thread.start()
    for thread in threads:thread.join(timeout=10)
    assert not errors and all(not t.is_alive() for t in threads)
    enrollments=w['q']('SELECT * FROM communication_enrollments WHERE stima_id=%s',(p['stima'],))
    assert len(enrollments)==1 and len(w['messages'](enrollments[0]))==1
    assert sum(x['errors'] for x in results)==0


def test_t12_stop_writer_serialized_with_dispatch(world):
    import threading,time
    from communication import dispatcher
    from communication.providers import null
    from communication.providers.base import ProviderResult, OUTCOME_ACCEPTED
    w=world;p=w['request']();e=w['enroll'](p)[0]
    claim=next(x for x in w['claim']() if x['message']['enrollment_id']==e['id'])
    entered=threading.Event();release=threading.Event();writer_started=threading.Event()
    results=[];errors=[];pid=[]
    class WaitingProvider:
        CAPABILITIES=null.CAPABILITIES
        @staticmethod
        def send(message):
            entered.set();assert release.wait(5)
            return ProviderResult(outcome=OUTCOME_ACCEPTED)
    def sending():
        try:results.append(dispatcher._dispatch_request_journey(w['ctx'],claim['message'],claim['message']['claim_token'],WaitingProvider,now=w['now']))
        except Exception as exc:errors.append(exc)
    def writing():
        conn=w['c']['psycopg2'].connect(w['c']['dsn'])
        try:
            with conn.cursor() as cur:
                cur.execute('SELECT pg_backend_pid()');pid.append(cur.fetchone()[0]);writer_started.set()
                cur.execute("INSERT INTO appointments(agency_id,contact_id,appointment_type,status,start_at,end_at,source) VALUES(1,%s,'seller_meeting','requested',%s,%s,'system')",(p['contact'],w['now'],w['now']+timedelta(hours=1)))
            conn.commit()
        except Exception as exc:errors.append(exc)
        finally:conn.close()
    sender=threading.Thread(target=sending);writer=threading.Thread(target=writing)
    sender.start();assert entered.wait(5);writer.start();assert writer_started.wait(5)
    blocked=False;deadline=time.monotonic()+4
    while time.monotonic()<deadline:
        row=w['q']('SELECT cardinality(pg_blocking_pids(%s)) AS n',(pid[0],))[0]
        if row['n']>0:blocked=True;break
    release.set();sender.join(5);writer.join(5)
    assert blocked and not errors and results==['sent']
    assert not sender.is_alive() and not writer.is_alive()
    w['tick'].tick(w['ctx'],now=w['now'])
    assert w['q']('SELECT status FROM communication_enrollments WHERE id=%s',(e['id'],))[0]['status']=='stopped'


def test_t1_registration_and_enrollment_atomic_rollback(world):
    w=world;ct=w['contact']();conn=w['conn'];conn.autocommit=False
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO leads(agency_id,contact_id) VALUES(1,%s) RETURNING id",(ct,));lead=cur.fetchone()['id']
            cur.execute("INSERT INTO stime(agency_id,comune) VALUES(1,'Alba') RETURNING id,crm_registered_at");request=cur.fetchone()
            assert request['crm_registered_at'] is not None
            cur.execute("INSERT INTO lead_stime(lead_id,stima_id,relation_type) VALUES(%s,%s,'related')",(lead,request['id']))
            result=w['tick'].tick(w['ctx'],now=request['crm_registered_at'],cur=cur)
            assert result['errors']==0
            cur.execute('SELECT trigger_sent_at FROM communication_enrollments WHERE stima_id=%s',(request['id'],))
            assert cur.fetchone()['trigger_sent_at']==request['crm_registered_at']
        conn.rollback()
    finally:conn.autocommit=True
    assert not w['q']('SELECT * FROM stime WHERE id=%s',(request['id'],))
    assert not w['q']('SELECT * FROM communication_enrollments WHERE stima_id_snapshot=%s',(request['id'],))


def test_t4_claim_window_and_boundary_defers_without_provider(world,monkeypatch):
    w=world;e=w['enroll'](w['request']())[0]
    from communication import repository as ledger,dispatcher
    class Sunday(datetime):
        @classmethod
        def now(cls,tz=None):return datetime(2026,10,11,10,tzinfo=timezone.utc)
    monkeypatch.setattr(ledger,'datetime',Sunday)
    assert w['claim']()==[] and w['messages'](e)[0]['attempt_count']==0
    class Friday(datetime):
        @classmethod
        def now(cls,tz=None):return w['now']
    monkeypatch.setattr(ledger,'datetime',Friday)
    claim=next(x for x in w['claim']() if x['message']['enrollment_id']==e['id'])
    class Tripwire:
        def send(self,m):pytest.fail('provider invoked outside window')
    assert dispatcher._dispatch_request_journey(w['ctx'],claim['message'],claim['message']['claim_token'],Tripwire(),now=w['now'].replace(hour=17))=='suppressed'
    after=w['q']('SELECT * FROM communication_enrollments WHERE id=%s',(e['id'],))[0]
    assert after['status']=='active' and after['next_action_at']==datetime(2026,10,10,7,tzinfo=timezone.utc)
    w['tick'].tick(w['ctx'],now=after['next_action_at'])
    assert len(w['messages'](e))==2 and w['messages'](e)[1]['status']=='queued'


def test_t12_accepted_then_rollback_is_not_retried(world,monkeypatch):
    w=world;e=w['enroll'](w['request']())[0]
    from communication import dispatcher
    from communication.providers import null
    actual=dispatcher._dispatch_request_journey
    monkeypatch.setattr(dispatcher,'_dispatch_request_journey',lambda ctx,message,token,provider:actual(ctx,message,token,provider,now=w['now']))
    def broken(*args):raise RuntimeError('synthetic finalization failure')
    monkeypatch.setattr(dispatcher.integrations,'dopo_invio',broken)
    result=dispatcher.dispatch_batch(w['ctx'],channel='email',provider=null)
    assert result['lost']>=1
    assert w['messages'](e)[0]['status']=='sending'
    w['q']("UPDATE communication_messages SET claimed_at=NOW()-interval '16 minutes' WHERE enrollment_id=%s",(e['id'],))
    dispatcher.dispatch_batch(w['ctx'],channel='email',provider=null)
    assert w['messages'](e)[0]['status']=='indeterminate'
    assert w['claim']()==[] and len(w['messages'](e))==1


def test_t14_fk_detach_preserves_request_snapshot_and_provenance(world):
    w=world;p=w['request']();e=w['enroll'](p)[0]
    message=w['messages'](e)[0]
    with pytest.raises(Exception):w['q']("UPDATE communication_messages SET template_version=1 WHERE id=%s",(message['id'],))
    with pytest.raises(Exception):w['q']("UPDATE communication_messages SET tappa6_cycle=FALSE WHERE id=%s",(message['id'],))
    w['q']('DELETE FROM stime WHERE id=%s',(p['stima'],))
    saved=w['q']('SELECT * FROM communication_enrollments WHERE id=%s',(e['id'],))[0]
    assert saved['stima_id'] is None and saved['stima_id_snapshot']==p['stima']
    assert saved['trigger_sent_at']==e['trigger_sent_at']
    retained=w['messages'](e)[0]
    assert retained['stima_id'] is None and retained['tappa6_cycle'] is True


def test_t12_origin_writer_before_contact_fence_no_deadlock(world):
    import threading,time
    w=world;p=w['request']();pid=[];started=threading.Event();errors=[]
    locker=w['c']['psycopg2'].connect(w['c']['dsn'])
    with locker.cursor() as cur:cur.execute('SELECT id FROM stime WHERE id=%s FOR UPDATE',(p['stima'],))
    def worker():
        conn=w['c']['psycopg2'].connect(w['c']['dsn'],cursor_factory=RealDictCursor)
        try:
            with conn.cursor() as cur:
                cur.execute('SELECT pg_backend_pid() AS pid');pid.append(cur.fetchone()['pid']);started.set()
                result=w['tick'].tick(w['ctx'],now=w['now'],cur=cur)
                assert result['errors']==0,result
            conn.commit()
        except Exception as exc:errors.append(exc)
        finally:conn.close()
    thread=threading.Thread(target=worker);thread.start();assert started.wait(5)
    blocked=False;deadline=time.monotonic()+4
    while time.monotonic()<deadline:
        if w['q']('SELECT cardinality(pg_blocking_pids(%s)) AS n',(pid[0],))[0]['n']>0:
            blocked=True;break
    try:
        assert blocked
        with locker.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout='1500ms'")
            cur.execute("INSERT INTO stima_inspections(stima_id,stima_id_snapshot,status,scheduled_for,created_by_operator_user_id) VALUES(%s,%s,'scheduled',%s,%s)",(p['stima'],p['stima'],w['now'],w['c']['ids']['owner_a']))
        locker.commit()
    finally:locker.rollback();locker.close();thread.join(8)
    assert not errors and not thread.is_alive()
    e=w['q']('SELECT * FROM communication_enrollments WHERE stima_id=%s',(p['stima'],))[0]
    assert e['status']=='stopped' and e['stop_reason']=='inspection' and w['messages'](e)==[]


def test_rev2_request_without_pdf_is_scheduled_from_crm_registration(world):
    w=world;ct=w['contact']()
    lead=w['q']("INSERT INTO leads(agency_id,contact_id) VALUES(1,%s) RETURNING id",(ct,))[0]['id']
    st=w['q']("INSERT INTO stime(agency_id,comune,data) VALUES(1,'Alba',NOW()-interval '30 days') RETURNING *")[0]
    w['q']("INSERT INTO lead_stime(lead_id,stima_id,relation_type) VALUES(%s,%s,'related')",(lead,st['id']))
    now=w['q']('SELECT clock_timestamp() AS instant')[0]['instant']
    result=w['tick'].tick(w['ctx'],now=now)
    assert result['errors']==0
    enrollments=w['q']('SELECT * FROM communication_enrollments WHERE stima_id=%s',(st['id'],))
    assert len(enrollments)==1,'a registered request must not wait for a PDF'
    from communication.send_window import next_allowed
    from communication.journey_catalog import FINESTRA_LAVORATIVA
    assert enrollments[0]['next_action_at']==next_allowed(st['crm_registered_at']+timedelta(days=2),FINESTRA_LAVORATIVA,'Europe/Rome')
    assert enrollments[0]['trigger_sent_at']==st['crm_registered_at']
    assert w['messages'](enrollments[0])==[]
    assert not w['q']('SELECT * FROM stima_pdf_artifacts WHERE stima_id=%s',(st['id'],))
    assert not w['q']("SELECT * FROM communication_messages WHERE stima_id=%s AND reason_code='stima_pdf'",(st['id'],))
