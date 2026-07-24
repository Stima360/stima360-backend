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
 with core_cursor(commit=True) as(_,c):c.execute("INSERT INTO owner_publications(property_id,publication_type,title,summary,body,status,version_number) VALUES(%s,%s,%s,%s,%s,'draft',1) RETURNING *",(d['property_id'],d['publication_type'],d['title'],d.get('summary'),d['body']));r=one(c)
 audit('publication_created',prop=r['property_id'],etype='owner_publication',eid=r['id']);return r
def get_publication(i):
 with core_cursor() as(_,c):c.execute('SELECT * FROM owner_publications WHERE id=%s',(i,));return one(c)
def list_publications():
 with core_cursor() as(_,c):c.execute('SELECT * FROM owner_publications ORDER BY created_at DESC');return[dict(x) for x in c.fetchall()]
def update_publication(i,d):
 r=get_publication(i)
 if r['status']!='draft':raise ConflictError('Una pubblicazione pubblicata o archiviata è immutabile')
 f=[];v=[]
 for k in('publication_type','title','summary','body'):
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
  c.execute("INSERT INTO owner_publications(property_id,publication_type,title,summary,body,status,version_number,supersedes_publication_id) VALUES(%s,%s,%s,%s,%s,'draft',%s,%s) RETURNING *",(old['property_id'],d['publication_type'],d['title'],d.get('summary'),d['body'],old['version_number']+1,old['id']));new=one(c);c.execute('UPDATE owner_publications SET superseded_by_publication_id=%s WHERE id=%s',(new['id'],old['id']))
 audit('publication_version_created',prop=old['property_id'],etype='owner_publication',eid=new['id'],meta={'previous':old['id']});return new
def timeline(a,p):
 require_property(a,p)
 with core_cursor() as(_,c):c.execute("SELECT id,property_id,publication_type,title,summary,body,published_at,version_number FROM owner_publications WHERE property_id=%s AND status='published' ORDER BY published_at DESC",(p,));return[dict(x) for x in c.fetchall()]
def publication(a,i):
 with core_cursor() as(_,c):c.execute("SELECT p.* FROM owner_publications p JOIN owner_property_access x ON x.property_id=p.property_id WHERE p.id=%s AND p.status='published' AND x.owner_account_id=%s AND x.access_status='active' AND x.revoked_at IS NULL",(i,a));return one(c)
def read(a,i,ack=False):
 p=publication(a,i)
 with core_cursor(commit=True) as(_,c):c.execute("INSERT INTO owner_publication_reads(publication_id,owner_account_id,view_count,acknowledged_at) VALUES(%s,%s,1,CASE WHEN %s THEN NOW() END) ON CONFLICT(publication_id,owner_account_id) DO UPDATE SET last_viewed_at=NOW(),view_count=owner_publication_reads.view_count+1,acknowledged_at=CASE WHEN %s THEN COALESCE(owner_publication_reads.acknowledged_at,NOW()) ELSE owner_publication_reads.acknowledged_at END RETURNING *",(i,a,ack,ack));r=one(c)
 audit('publication_acknowledged' if ack else 'publication_viewed',a,p['property_id'],'owner_publication',i);return r
def create_feedback(a,p,d):
 require_property(a,p)
 with core_cursor(commit=True) as(_,c):c.execute("INSERT INTO owner_feedback(owner_account_id,property_id,feedback_type,subject,message,status,submitted_at) VALUES(%s,%s,%s,%s,%s,'new',NOW()) RETURNING *",(a,p,d['feedback_type'],d['subject'],d['message']));r=one(c)
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
