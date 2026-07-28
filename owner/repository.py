from datetime import timedelta
from psycopg2.extras import Json
from core.database import core_cursor
from core.exceptions import NotFoundError,ConflictError
from .security import generate_secret,hash_secret,utcnow,valid_session
NF='Risorsa non trovata'
def one(cur):
 r=cur.fetchone()
 if not r: raise NotFoundError(NF)
 return dict(r)
def audit(action,account=None,prop=None,etype=None,eid=None,result='success',meta=None):
 with core_cursor(commit=True) as(_,c):c.execute("INSERT INTO owner_audit_log(owner_account_id,property_id,action,entity_type,entity_id,result,metadata) VALUES(%s,%s,%s,%s,%s,%s,%s)",(account,prop,action,etype,str(eid) if eid else None,result,Json(meta or {})))
def create_account(d):
 with core_cursor(commit=True) as(_,c):
  c.execute('SELECT 1 FROM contacts WHERE id=%s',(d['contact_id'],))
  if not c.fetchone():raise NotFoundError(NF)
  c.execute("INSERT INTO owner_accounts(contact_id,status,preferred_language) VALUES(%s,'invited',%s) RETURNING *",(d['contact_id'],d.get('preferred_language','it')));r=one(c)
 audit('account_created',r['id'],etype='owner_account',eid=r['id']);return r
def list_accounts():
 with core_cursor() as(_,c):c.execute('SELECT oa.*,c.display_name,c.email FROM owner_accounts oa JOIN contacts c ON c.id=oa.contact_id ORDER BY oa.created_at DESC');return[dict(x) for x in c.fetchall()]
def get_account(i):
 with core_cursor() as(_,c):c.execute('SELECT * FROM owner_accounts WHERE id=%s',(i,));return one(c)
def set_account(i,status):
 get_account(i)
 with core_cursor(commit=True) as(_,c):c.execute("UPDATE owner_accounts SET status=%s,disabled_at=CASE WHEN %s='disabled' THEN NOW() ELSE NULL END,updated_at=NOW() WHERE id=%s RETURNING *",(status,status,i));return one(c)
def create_access(d):
 with core_cursor(commit=True) as(_,c):
  c.execute('SELECT 1 FROM owner_accounts WHERE id=%s',(d['owner_account_id'],));a=c.fetchone();c.execute('SELECT 1 FROM properties WHERE id=%s',(d['property_id'],));p=c.fetchone()
  if not a or not p:raise NotFoundError(NF)
  c.execute("INSERT INTO owner_property_access(owner_account_id,property_id,access_role,access_status,is_primary,valid_from,valid_until) VALUES(%s,%s,%s,'active',%s,NOW(),%s) RETURNING *",(d['owner_account_id'],d['property_id'],d.get('access_role','owner'),d.get('is_primary',False),d.get('valid_until')));r=one(c)
 audit('access_granted',r['owner_account_id'],r['property_id'],'owner_access',r['id']);return r
def list_access():
 with core_cursor() as(_,c):c.execute('SELECT * FROM owner_property_access ORDER BY created_at DESC');return[dict(x) for x in c.fetchall()]
def revoke_access(i):
 with core_cursor(commit=True) as(_,c):c.execute("UPDATE owner_property_access SET access_status='revoked',revoked_at=NOW(),updated_at=NOW() WHERE id=%s RETURNING *",(i,));r=one(c)
 audit('access_revoked',r['owner_account_id'],r['property_id'],'owner_access',i);return r
def create_token(i,typ='login',minutes=30,by=None):
 get_account(i);raw=generate_secret()
 with core_cursor(commit=True) as(_,c):c.execute("INSERT INTO owner_access_tokens(owner_account_id,token_hash,token_type,expires_at,created_by) VALUES(%s,%s,%s,%s,%s) RETURNING *",(i,hash_secret(raw),typ,utcnow()+timedelta(minutes=minutes),by));r=one(c)
 audit('token_created',i,etype='owner_token',eid=r['id']);return r,raw
def consume_token(raw):
 with core_cursor(commit=True) as(_,c):
  c.execute("SELECT t.*,a.status account_status FROM owner_access_tokens t JOIN owner_accounts a ON a.id=t.owner_account_id WHERE token_hash=%s FOR UPDATE",(hash_secret(raw),));r=c.fetchone()
  if not r or r['used_at'] or r['revoked_at'] or r['expires_at']<=utcnow() or r['account_status']=='disabled':raise NotFoundError(NF)
  c.execute('UPDATE owner_access_tokens SET used_at=NOW() WHERE id=%s',(r['id'],));s=generate_secret();c.execute("INSERT INTO owner_sessions(owner_account_id,session_token_hash,last_seen_at,expires_at) VALUES(%s,%s,NOW(),%s) RETURNING *",(r['owner_account_id'],hash_secret(s),utcnow()+timedelta(hours=12)));sr=one(c);c.execute("UPDATE owner_accounts SET status='active',last_login_at=NOW() WHERE id=%s",(r['owner_account_id'],))
 audit('login_succeeded',r['owner_account_id'],etype='owner_session',eid=sr['id']);return sr,s
def get_session(raw):
 if not raw:raise NotFoundError(NF)
 with core_cursor(commit=True) as(_,c):
  c.execute("SELECT s.*,a.status account_status FROM owner_sessions s JOIN owner_accounts a ON a.id=s.owner_account_id WHERE session_token_hash=%s",(hash_secret(raw),));r=c.fetchone()
  if not r or r['account_status']!='active' or not valid_session(dict(r)):raise NotFoundError(NF)
  c.execute('UPDATE owner_sessions SET last_seen_at=NOW() WHERE id=%s RETURNING *',(r['id'],));return one(c)
def revoke_session(raw):
 if raw:
  with core_cursor(commit=True) as(_,c):c.execute('UPDATE owner_sessions SET revoked_at=COALESCE(revoked_at,NOW()) WHERE session_token_hash=%s',(hash_secret(raw),))
def require_property(a,p):
 with core_cursor() as(_,c):c.execute("SELECT x.*,p.title,p.address,p.city FROM owner_property_access x JOIN properties p ON p.id=x.property_id WHERE x.owner_account_id=%s AND x.property_id=%s AND x.access_status='active' AND x.revoked_at IS NULL AND (x.valid_until IS NULL OR x.valid_until>NOW())",(a,p));return one(c)
def portal_properties(a):
 with core_cursor() as(_,c):c.execute("SELECT p.id,p.title,p.address,p.city,x.access_role,x.is_primary FROM owner_property_access x JOIN properties p ON p.id=x.property_id WHERE x.owner_account_id=%s AND x.access_status='active' AND x.revoked_at IS NULL AND (x.valid_until IS NULL OR x.valid_until>NOW()) ORDER BY x.is_primary DESC",(a,));return[dict(x) for x in c.fetchall()]
def create_publication(d):
 with core_cursor(commit=True) as(_,c):c.execute("INSERT INTO owner_publications(property_id,publication_type,title,summary,body,status,version_number,acknowledgement_required) VALUES(%s,%s,%s,%s,%s,'draft',1,%s) RETURNING *",(d['property_id'],d['publication_type'],d['title'],d.get('summary'),d['body'],d.get('acknowledgement_required',False)));r=one(c)
 audit('publication_created',prop=r['property_id'],etype='owner_publication',eid=r['id']);return r
def get_publication(i):
 with core_cursor() as(_,c):c.execute('SELECT * FROM owner_publications WHERE id=%s',(i,));return one(c)
def list_publications():
 with core_cursor() as(_,c):c.execute('SELECT * FROM owner_publications ORDER BY created_at DESC');return[dict(x) for x in c.fetchall()]
def update_publication(i,d):
 r=get_publication(i)
 if r['status']!='draft':raise ConflictError('Una pubblicazione pubblicata o archiviata è immutabile')
 f=[];v=[]
 for k in('publication_type','title','summary','body','acknowledgement_required'):
  if d.get(k) is not None:f.append(k+'=%s');v.append(d[k])
 if not f:return r
 v.append(i)
 with core_cursor(commit=True) as(_,c):c.execute('UPDATE owner_publications SET '+','.join(f)+',updated_at=NOW() WHERE id=%s RETURNING *',v);return one(c)
def publish(i):
 r=get_publication(i)
 if r['status']!='draft':raise ConflictError('Solo draft pubblicabile')
 with core_cursor(commit=True) as(_,c):c.execute("UPDATE owner_publications SET status='published',published_at=NOW() WHERE id=%s RETURNING *",(i,));z=one(c)
 audit('publication_published',prop=z['property_id'],etype='owner_publication',eid=i);return z
def archive(i):
 r=get_publication(i)
 if r['status']!='published':raise ConflictError('Solo published archiviabile')
 with core_cursor(commit=True) as(_,c):c.execute("UPDATE owner_publications SET status='archived',archived_at=NOW() WHERE id=%s RETURNING *",(i,));z=one(c)
 audit('publication_archived',prop=z['property_id'],etype='owner_publication',eid=i);return z
def supersede(i,d):
 old=get_publication(i)
 if old['status']!='published':raise ConflictError('Solo published sostituibile')
 with core_cursor(commit=True) as(_,c):
  c.execute("INSERT INTO owner_publications(property_id,publication_type,title,summary,body,status,version_number,supersedes_publication_id,acknowledgement_required) VALUES(%s,%s,%s,%s,%s,'draft',%s,%s,%s) RETURNING *",(old['property_id'],d['publication_type'],d['title'],d.get('summary'),d['body'],old['version_number']+1,old['id'],d.get('acknowledgement_required',False)));new=one(c);c.execute('UPDATE owner_publications SET superseded_by_publication_id=%s WHERE id=%s',(new['id'],old['id']))
 audit('publication_version_created',prop=old['property_id'],etype='owner_publication',eid=new['id'],meta={'previous':old['id']});return new
def timeline(a,p):
 require_property(a,p)
 with core_cursor() as(_,c):c.execute("SELECT id,property_id,publication_type,title,summary,body,published_at,version_number,acknowledgement_required FROM owner_publications WHERE property_id=%s AND status='published' ORDER BY published_at DESC",(p,));return[dict(x) for x in c.fetchall()]
def publication(a,i):
 with core_cursor() as(_,c):c.execute("SELECT p.* FROM owner_publications p JOIN owner_property_access x ON x.property_id=p.property_id WHERE p.id=%s AND p.status='published' AND x.owner_account_id=%s AND x.access_status='active' AND x.revoked_at IS NULL",(i,a));return one(c)
def read(a,i,ack=False):
 p=publication(a,i)
 with core_cursor(commit=True) as(_,c):c.execute("INSERT INTO owner_publication_reads(publication_id,owner_account_id,view_count,acknowledged_at) VALUES(%s,%s,1,CASE WHEN %s THEN NOW() END) ON CONFLICT(publication_id,owner_account_id) DO UPDATE SET last_viewed_at=NOW(),view_count=owner_publication_reads.view_count+1,acknowledged_at=CASE WHEN %s THEN COALESCE(owner_publication_reads.acknowledged_at,NOW()) ELSE owner_publication_reads.acknowledged_at END RETURNING *",(i,a,ack,ack));r=one(c)
 audit('publication_acknowledged' if ack else 'publication_viewed',a,p['property_id'],'owner_publication',i);return r
def create_feedback(a,p,d):
 require_property(a,p)
 with core_cursor(commit=True) as(_,c):c.execute("INSERT INTO owner_feedback(owner_account_id,property_id,feedback_type,subject,message,status,submitted_at,availability_from,availability_to) VALUES(%s,%s,%s,%s,%s,'new',NOW(),%s,%s) RETURNING *",(a,p,d['feedback_type'],d['subject'],d['message'],d.get('availability_from'),d.get('availability_to')));r=one(c)
 audit('feedback_submitted',a,p,'owner_feedback',r['id']);return r
def list_feedback(a=None,p=None):
 with core_cursor() as(_,c):
  if a:c.execute('SELECT * FROM owner_feedback WHERE owner_account_id=%s AND property_id=%s ORDER BY submitted_at DESC',(a,p))
  else:c.execute('SELECT * FROM owner_feedback ORDER BY submitted_at DESC')
  return[dict(x) for x in c.fetchall()]
def dashboard():
 with core_cursor() as(_,c):c.execute("SELECT (SELECT COUNT(*) FROM owner_accounts WHERE status='active') active_accounts,(SELECT COUNT(*) FROM owner_property_access WHERE access_status='active') active_access,(SELECT COUNT(*) FROM owner_publications WHERE status='published') published,(SELECT COUNT(*) FROM owner_feedback WHERE status='new') new_feedback");return dict(c.fetchone())
def audits():
 with core_cursor() as(_,c):c.execute('SELECT * FROM owner_audit_log ORDER BY created_at DESC LIMIT 200');return[dict(x) for x in c.fetchall()]

# OWNER 0.2 P2 ---------------------------------------------------------------
def _property_for_document(c, document_id):
 c.execute("SELECT id,property_id,title,url,status,expires_at FROM property_documents WHERE id=%s",(document_id,))
 return one(c)

def _property_for_visit(c, visit_id):
 c.execute("SELECT id,property_id,scheduled_at,status FROM property_visits WHERE id=%s",(visit_id,))
 return one(c)

def _validate_target_account(c, account_id, property_id):
 if account_id is None:return
 c.execute("SELECT 1 FROM owner_property_access WHERE owner_account_id=%s AND property_id=%s AND access_status='active' AND revoked_at IS NULL AND (valid_until IS NULL OR valid_until>NOW())",(account_id,property_id))
 if not c.fetchone():raise NotFoundError(NF)

def create_shared_document(d):
 with core_cursor(commit=True) as(_,c):
  src=_property_for_document(c,d['property_document_id'])
  _validate_target_account(c,d.get('owner_account_id'),src['property_id'])
  c.execute("""INSERT INTO owner_shared_documents(property_document_id,owner_account_id,public_title,public_document_type,expires_at,acknowledgement_required,created_by)
               VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING *""",(d['property_document_id'],d.get('owner_account_id'),d['public_title'],d['public_document_type'],d.get('expires_at'),d.get('acknowledgement_required',False),d.get('created_by')));r=one(c)
 audit('shared_document_created',d.get('owner_account_id'),src['property_id'],'owner_shared_document',r['id']);return r

def list_shared_documents():
 with core_cursor() as(_,c):
  c.execute("""SELECT sd.*,pd.property_id,pd.title source_title,pd.status source_status
               FROM owner_shared_documents sd JOIN property_documents pd ON pd.id=sd.property_document_id
               ORDER BY sd.created_at DESC""");return[dict(x) for x in c.fetchall()]

def get_shared_document(i):
 with core_cursor() as(_,c):
  c.execute("""SELECT sd.*,pd.property_id,pd.title source_title,pd.status source_status
               FROM owner_shared_documents sd JOIN property_documents pd ON pd.id=sd.property_document_id WHERE sd.id=%s""",(i,));return one(c)

def update_shared_document(i,d):
 old=get_shared_document(i)
 if old['status']!='draft':raise ConflictError('Un documento pubblicato, revocato o archiviato è immutabile')
 fields=[];vals=[]
 for k in ('public_title','public_document_type','expires_at','acknowledgement_required'):
  if k in d:fields.append(k+'=%s');vals.append(d[k])
 if not fields:return old
 vals.append(i)
 with core_cursor(commit=True) as(_,c):c.execute('UPDATE owner_shared_documents SET '+','.join(fields)+',updated_at=NOW() WHERE id=%s RETURNING *',vals);r=one(c)
 audit('shared_document_updated',old.get('owner_account_id'),old['property_id'],'owner_shared_document',i);return r

def publish_shared_document(i):
 old=get_shared_document(i)
 if old['status']!='draft':raise ConflictError('Solo draft pubblicabile')
 with core_cursor(commit=True) as(_,c):
  c.execute("UPDATE owner_shared_documents SET status='published',published_at=NOW(),updated_at=NOW() WHERE id=%s RETURNING *",(i,));r=one(c)
 audit('shared_document_published',old.get('owner_account_id'),old['property_id'],'owner_shared_document',i);return r

def revoke_shared_document(i,actor=None):
 old=get_shared_document(i)
 if old['status']!='published':raise ConflictError('Solo published revocabile')
 with core_cursor(commit=True) as(_,c):
  c.execute("UPDATE owner_shared_documents SET status='revoked',revoked_at=NOW(),revoked_by=%s,updated_at=NOW() WHERE id=%s RETURNING *",(actor,i));r=one(c)
 audit('shared_document_revoked',old.get('owner_account_id'),old['property_id'],'owner_shared_document',i);return r

def archive_shared_document(i):
 old=get_shared_document(i)
 if old['status'] not in ('published','revoked'):raise ConflictError('Solo published o revoked archiviabile')
 with core_cursor(commit=True) as(_,c):
  c.execute("UPDATE owner_shared_documents SET status='archived',archived_at=NOW(),updated_at=NOW() WHERE id=%s RETURNING *",(i,));r=one(c)
 audit('shared_document_archived',old.get('owner_account_id'),old['property_id'],'owner_shared_document',i);return r

def supersede_shared_document(i,d):
 old=get_shared_document(i)
 if old['status']!='published':raise ConflictError('Solo published sostituibile')
 with core_cursor(commit=True) as(_,c):
  c.execute("""INSERT INTO owner_shared_documents(property_document_id,owner_account_id,public_title,public_document_type,version_number,status,expires_at,acknowledgement_required,supersedes_shared_document_id,created_by)
               VALUES(%s,%s,%s,%s,%s,'draft',%s,%s,%s,%s) RETURNING *""",(old['property_document_id'],old.get('owner_account_id'),d['public_title'],d['public_document_type'],old['version_number']+1,d.get('expires_at'),d.get('acknowledgement_required',False),i,d.get('created_by')));new=one(c)
  c.execute('UPDATE owner_shared_documents SET superseded_by_shared_document_id=%s,updated_at=NOW() WHERE id=%s',(new['id'],i))
 audit('shared_document_version_created',old.get('owner_account_id'),old['property_id'],'owner_shared_document',new['id'],meta={'previous':i});return new

def portal_shared_documents(a,p):
 require_property(a,p)
 with core_cursor() as(_,c):
  c.execute("""SELECT sd.id,sd.public_title,sd.public_document_type,sd.version_number,sd.published_at,sd.expires_at,sd.acknowledgement_required,
                      dr.first_viewed_at,dr.last_viewed_at,dr.view_count,dr.acknowledged_at
               FROM owner_shared_documents sd
               JOIN property_documents pd ON pd.id=sd.property_document_id
               LEFT JOIN owner_document_reads dr ON dr.shared_document_id=sd.id AND dr.owner_account_id=%s
               WHERE pd.property_id=%s AND sd.status='published'
                 AND (sd.owner_account_id IS NULL OR sd.owner_account_id=%s)
                 AND (sd.expires_at IS NULL OR sd.expires_at>NOW())
               ORDER BY sd.published_at DESC""",(a,p,a));return[dict(x) for x in c.fetchall()]

def portal_shared_document(a,i):
 with core_cursor() as(_,c):
  c.execute("""SELECT sd.id,sd.public_title,sd.public_document_type,sd.version_number,sd.published_at,sd.expires_at,sd.acknowledgement_required,
                      pd.property_id,pd.url
               FROM owner_shared_documents sd
               JOIN property_documents pd ON pd.id=sd.property_document_id
               JOIN owner_property_access x ON x.property_id=pd.property_id
               WHERE sd.id=%s AND sd.status='published' AND (sd.expires_at IS NULL OR sd.expires_at>NOW())
                 AND (sd.owner_account_id IS NULL OR sd.owner_account_id=%s)
                 AND x.owner_account_id=%s AND x.access_status='active' AND x.revoked_at IS NULL
                 AND (x.valid_until IS NULL OR x.valid_until>NOW())""",(i,a,a));return one(c)

def read_shared_document(a,i,ack=False):
 d=portal_shared_document(a,i)
 with core_cursor(commit=True) as(_,c):
  c.execute("""INSERT INTO owner_document_reads(shared_document_id,owner_account_id,view_count,acknowledged_at)
               VALUES(%s,%s,1,CASE WHEN %s THEN NOW() END)
               ON CONFLICT(shared_document_id,owner_account_id) DO UPDATE SET last_viewed_at=NOW(),view_count=owner_document_reads.view_count+1,
               acknowledged_at=CASE WHEN %s THEN COALESCE(owner_document_reads.acknowledged_at,NOW()) ELSE owner_document_reads.acknowledged_at END RETURNING *""",(i,a,ack,ack));r=one(c)
 audit('shared_document_acknowledged' if ack else 'shared_document_viewed',a,d['property_id'],'owner_shared_document',i);return r

def create_visit_feedback_publication(d):
 with core_cursor(commit=True) as(_,c):
  src=_property_for_visit(c,d['property_visit_id'])
  _validate_target_account(c,d.get('owner_account_id'),src['property_id'])
  c.execute("""INSERT INTO owner_visit_feedback_publications(property_visit_id,owner_account_id,category,public_summary,sentiment,created_by)
               VALUES(%s,%s,%s,%s,%s,%s) RETURNING *""",(d['property_visit_id'],d.get('owner_account_id'),d['category'],d['public_summary'],d.get('sentiment'),d.get('created_by')));r=one(c)
 audit('visit_feedback_created',d.get('owner_account_id'),src['property_id'],'owner_visit_feedback',r['id']);return r

def list_visit_feedback_publications():
 with core_cursor() as(_,c):
  c.execute("""SELECT vf.*,pv.property_id,pv.scheduled_at FROM owner_visit_feedback_publications vf
               JOIN property_visits pv ON pv.id=vf.property_visit_id ORDER BY vf.created_at DESC""");return[dict(x) for x in c.fetchall()]

def get_visit_feedback_publication(i):
 with core_cursor() as(_,c):
  c.execute("""SELECT vf.*,pv.property_id,pv.scheduled_at FROM owner_visit_feedback_publications vf
               JOIN property_visits pv ON pv.id=vf.property_visit_id WHERE vf.id=%s""",(i,));return one(c)

def update_visit_feedback_publication(i,d):
 old=get_visit_feedback_publication(i)
 if old['status']!='draft':raise ConflictError('Un feedback pubblicato o archiviato è immutabile')
 fields=[];vals=[]
 for k in ('category','public_summary','sentiment'):
  if k in d:fields.append(k+'=%s');vals.append(d[k])
 if not fields:return old
 vals.append(i)
 with core_cursor(commit=True) as(_,c):c.execute('UPDATE owner_visit_feedback_publications SET '+','.join(fields)+',updated_at=NOW() WHERE id=%s RETURNING *',vals);r=one(c)
 audit('visit_feedback_updated',old.get('owner_account_id'),old['property_id'],'owner_visit_feedback',i);return r

def publish_visit_feedback(i):
 old=get_visit_feedback_publication(i)
 if old['status']!='draft':raise ConflictError('Solo draft pubblicabile')
 with core_cursor(commit=True) as(_,c):c.execute("UPDATE owner_visit_feedback_publications SET status='published',published_at=NOW(),updated_at=NOW() WHERE id=%s RETURNING *",(i,));r=one(c)
 audit('visit_feedback_published',old.get('owner_account_id'),old['property_id'],'owner_visit_feedback',i);return r

def archive_visit_feedback(i):
 old=get_visit_feedback_publication(i)
 if old['status']!='published':raise ConflictError('Solo published archiviabile')
 with core_cursor(commit=True) as(_,c):c.execute("UPDATE owner_visit_feedback_publications SET status='archived',archived_at=NOW(),updated_at=NOW() WHERE id=%s RETURNING *",(i,));r=one(c)
 audit('visit_feedback_archived',old.get('owner_account_id'),old['property_id'],'owner_visit_feedback',i);return r

def supersede_visit_feedback(i,d):
 old=get_visit_feedback_publication(i)
 if old['status']!='published':raise ConflictError('Solo published sostituibile')
 with core_cursor(commit=True) as(_,c):
  c.execute("""INSERT INTO owner_visit_feedback_publications(property_visit_id,owner_account_id,category,public_summary,sentiment,version_number,supersedes_feedback_publication_id,created_by)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",(old['property_visit_id'],old.get('owner_account_id'),d['category'],d['public_summary'],d.get('sentiment'),old['version_number']+1,i,d.get('created_by')));new=one(c)
  c.execute('UPDATE owner_visit_feedback_publications SET superseded_by_feedback_publication_id=%s,updated_at=NOW() WHERE id=%s',(new['id'],i))
 audit('visit_feedback_version_created',old.get('owner_account_id'),old['property_id'],'owner_visit_feedback',new['id'],meta={'previous':i});return new

def portal_visit_feedback(a,p):
 require_property(a,p)
 with core_cursor() as(_,c):
  c.execute("""SELECT vf.id,vf.category,vf.public_summary,vf.sentiment,vf.version_number,vf.published_at,pv.scheduled_at
               FROM owner_visit_feedback_publications vf JOIN property_visits pv ON pv.id=vf.property_visit_id
               WHERE pv.property_id=%s AND vf.status='published' AND (vf.owner_account_id IS NULL OR vf.owner_account_id=%s)
               ORDER BY vf.published_at DESC""",(p,a));return[dict(x) for x in c.fetchall()]

def update_feedback_status(i,d):
 with core_cursor(commit=True) as(_,c):
  c.execute('SELECT * FROM owner_feedback WHERE id=%s',(i,));old=one(c)
  handled=d['status'] in ('handled','closed')
  c.execute("""UPDATE owner_feedback SET status=%s,handled_at=CASE WHEN %s THEN COALESCE(handled_at,NOW()) ELSE handled_at END,
               handled_by=COALESCE(%s,handled_by),public_response=COALESCE(%s,public_response),updated_at=NOW() WHERE id=%s RETURNING *""",(d['status'],handled,d.get('handled_by'),d.get('public_response'),i));r=one(c)
 audit('feedback_status_updated',old['owner_account_id'],old['property_id'],'owner_feedback',i,meta={'status':d['status']});return r
