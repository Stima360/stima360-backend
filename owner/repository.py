from datetime import date, timedelta
from psycopg2.extras import Json
from core.database import core_cursor
from core.repository import create_activity_with_cursor
from integration_owner_request import record_owner_request_event_with_cursor, process_saved_owner_request_event
from core.exceptions import NotFoundError, ConflictError, ValidationError
from .security import generate_secret,hash_secret,utcnow,valid_session
from .schemas import validate_visit_feedback_summary, visit_feedback_privacy_issues
NF='Risorsa non trovata'
def one(cur):
 r=cur.fetchone()
 if not r: raise NotFoundError(NF)
 return dict(r)
def audit(action,account=None,prop=None,etype=None,eid=None,result='success',meta=None):
 with core_cursor(commit=True) as(_,c):c.execute("INSERT INTO owner_audit_log(owner_account_id,property_id,action,entity_type,entity_id,result,metadata) VALUES(%s,%s,%s,%s,%s,%s,%s)",(account,prop,action,etype,str(eid) if eid else None,result,Json(meta or {})))
# P26-6C OWNER Admin: the derivation chain, in one place.
#
# An owner account reaches an agency through its contact - owner_accounts ->
# contacts -> agency_id - and every admin operation on an account has to walk
# it before touching the row. Written once so the three callers cannot drift
# into three slightly different predicates.
#
# The read is locked for the same reason create_access locks: the decision and
# the write that follows must be atomic with respect to the parents. FOR UPDATE
# on the account when the caller is about to modify it, FOR SHARE otherwise;
# the contact is only read, so it is shared in both cases. The lock ends at
# COMMIT and says nothing about a parent changed afterwards.
_ACCOUNT_TENANT="SELECT oa.id FROM owner_accounts oa JOIN contacts ct ON ct.id=oa.contact_id WHERE oa.id=%s AND ct.agency_id=%s "

def _require_account_in_agency(c,agency_id,owner_account_id,*,for_update=False):
 """Resolve the account inside `agency_id`, or refuse with the neutral 404.

 The tenant is a predicate rather than a value compared afterwards, so the
 statement itself carries the scope and an audit of what this module executes
 can see it.

 Absent, tenant-less and belonging-to-another-agency are one answer on
 purpose: telling the caller which of the three it was would confirm that an
 account it may not touch exists.
 """
 c.execute(_ACCOUNT_TENANT+("FOR UPDATE OF oa FOR SHARE OF ct" if for_update else "FOR SHARE OF oa,ct"),(owner_account_id,agency_id))
 if not c.fetchone():raise NotFoundError(NF)

_PROPERTY_TENANT="SELECT id FROM properties WHERE id=%s AND agency_id=%s "

def _require_property_in_agency(c,agency_id,property_id):
 """The property root, the other half of OWNER's tenancy.

 Publications, shared documents and visit feedback all reach an agency through
 a property rather than through a contact, so this is the counterpart of
 _require_account_in_agency for that side of the module.
 """
 c.execute(_PROPERTY_TENANT+"FOR SHARE",(property_id,agency_id))
 if not c.fetchone():raise NotFoundError(NF)

def _publication_in_agency(c,agency_id,publication_id,*,for_update=False):
 """The publication, resolved through its property. Raises the neutral 404."""
 c.execute(
     "SELECT pub.* FROM owner_publications pub JOIN properties p ON p.id=pub.property_id "
     "WHERE pub.id=%s AND p.agency_id=%s "
     +("FOR UPDATE OF pub FOR SHARE OF p" if for_update else "FOR SHARE OF pub,p"),
     (publication_id,agency_id),
 )
 return one(c)

def create_account(agency_id,d):
 # The contact must be one of this agency's. `d` is the request body and is
 # never consulted for the tenant.
 with core_cursor(commit=True) as(_,c):
  c.execute('SELECT id FROM contacts WHERE id=%s AND agency_id=%s FOR SHARE',(d['contact_id'],agency_id))
  if not c.fetchone():raise NotFoundError(NF)
  c.execute("INSERT INTO owner_accounts(contact_id,status,preferred_language) VALUES(%s,'invited',%s) RETURNING *",(d['contact_id'],d.get('preferred_language','it')));r=one(c)
 audit('account_created',r['id'],etype='owner_account',eid=r['id']);return r
def list_accounts(agency_id):
 # P26-6C OWNER Admin: the account's tenant is its contact's. The join to
 # contacts was already here for the display name; it now also carries the
 # predicate. `agency_id` is positional and has no default, so a caller that
 # forgets it raises a TypeError instead of listing the platform.
 with core_cursor() as(_,c):c.execute('SELECT oa.*,c.display_name,c.email FROM owner_accounts oa JOIN contacts c ON c.id=oa.contact_id WHERE c.agency_id=%s ORDER BY oa.created_at DESC',(agency_id,));return[dict(x) for x in c.fetchall()]
def get_account(i):
 with core_cursor() as(_,c):c.execute('SELECT * FROM owner_accounts WHERE id=%s',(i,));return one(c)
def set_account(agency_id,i,status):
 # The check moves inside the UPDATE's own transaction. It used to be a
 # separate `get_account(i)` on its own connection, which left a window in
 # which the row could change between the two - harmless while the check was
 # only "does it exist", not harmless now that it decides who may write.
 with core_cursor(commit=True) as(_,c):
  _require_account_in_agency(c,agency_id,i,for_update=True)
  c.execute("UPDATE owner_accounts SET status=%s,disabled_at=CASE WHEN %s='disabled' THEN NOW() ELSE NULL END,updated_at=NOW() WHERE id=%s RETURNING *",(status,status,i));return one(c)
def create_access(agency_id,d):
 # P26-6C OWNER: a grant may not join an account and a property of different
 # agencies. OWNER has two tenancy roots - the account reaches an agency
 # through its contact, the property carries its own - and nothing in the
 # schema requires them to agree. Several OWNER tables touch both roots
 # (owner_publication_reads, owner_document_reads, owner_feedback,
 # owner_notifications); owner_property_access is the one where an
 # administrator CREATES the link, and the one require_property and
 # portal_properties then read as authorisation. That is why the check is
 # here, in the layer that has the cursor, inside the same transaction as the
 # INSERT it guards.
 #
 # Both agencies come from the database. Nothing in `d` is consulted for them:
 # `d` is the request body.
 #
 # FOR SHARE, not FOR UPDATE: these rows are read, not modified, and two grants
 # created against the same property should not serialise against each other.
 # Nor FOR KEY SHARE - but not for the reason it might seem. On two of the
 # three rows a key-share lock would in fact be enough: owner_accounts.contact_id
 # is UNIQUE, and 030 gave contacts the composite key
 # contacts_agency_scope_unq UNIQUE (agency_id, id), so an UPDATE of either
 # column is a key update. properties.agency_id is the exception: 034 covers it
 # only with the plain, non-unique index idx_properties_agency_id, so there a
 # key-share lock would leave UPDATE properties SET agency_id=... unblocked.
 # FOR SHARE is the weakest level that holds uniformly across all three.
 #
 # The lock holds until COMMIT and not one moment longer: it makes the decision
 # and the INSERT atomic with respect to the parents, and says nothing about a
 # parent that changes agency afterwards. A grant already written stays behind,
 # and all three parents are mutable - owner_accounts.contact_id,
 # contacts.agency_id and properties.agency_id all accept an UPDATE today, none
 # of them carrying an immutability trigger. Closing that needs an agreement
 # trigger on this table plus those guards; none of it is in this patch.
 with core_cursor(commit=True) as(_,c):
  c.execute("SELECT ct.agency_id FROM owner_accounts oa JOIN contacts ct ON ct.id=oa.contact_id WHERE oa.id=%s FOR SHARE OF oa,ct",(d['owner_account_id'],));a=c.fetchone()
  c.execute('SELECT agency_id FROM properties WHERE id=%s FOR SHARE',(d['property_id'],));p=c.fetchone()
  if not a or not p:raise NotFoundError(NF)
  # Each side is rejected on its own BEFORE the two are compared: NULL is not a
  # value, and `None != None` is False, so a single equality test would accept
  # the one case where neither agency is known.
  account_agency=a['agency_id'];property_agency=p['agency_id']
  if account_agency is None or property_agency is None:raise NotFoundError(NF)
  # NotFoundError, which the route already maps to a neutral 404: telling the
  # caller that the property exists but belongs elsewhere is itself a leak.
  if account_agency!=property_agency:raise NotFoundError(NF)
  # And both must be the CALLER's agency. This is a second, separate rule: an
  # account of B granted a property of B is perfectly coherent and passes the
  # check above, but an administrator of A has no business creating it.
  if account_agency!=agency_id:raise NotFoundError(NF)
  c.execute("INSERT INTO owner_property_access(owner_account_id,property_id,access_role,access_status,is_primary,valid_from,valid_until) VALUES(%s,%s,%s,'active',%s,NOW(),%s) RETURNING *",(d['owner_account_id'],d['property_id'],d.get('access_role','owner'),d.get('is_primary',False),d.get('valid_until')));r=one(c)
 audit('access_granted',r['owner_account_id'],r['property_id'],'owner_access',r['id']);return r
def list_access(agency_id):
 # Both roots, not one. A grant reaches an agency through its account
 # (-> contact) and through its property, and rows written before the
 # cross-agency check in create_access can have the two disagree. Requiring
 # both means such a row belongs to neither listing: an admin of A must not
 # see a grant pointing at a property of B, and vice versa. This hides them;
 # it does not repair them.
 with core_cursor() as(_,c):
  c.execute(
      """SELECT x.*
           FROM owner_property_access x
           JOIN owner_accounts oa ON oa.id=x.owner_account_id
           JOIN contacts ct ON ct.id=oa.contact_id
           JOIN properties p ON p.id=x.property_id
          WHERE ct.agency_id=%s AND p.agency_id=%s
          ORDER BY x.created_at DESC""",
      (agency_id, agency_id),
  )
  return[dict(x) for x in c.fetchall()]
def revoke_access(agency_id,i):
 # Both roots, as in list_access: a grant reaches an agency through its
 # account (-> contact) and through its property. Requiring both means a
 # legacy grant whose two roots disagree cannot be revoked by either agency -
 # deliberate. Repointing or removing those rows is a data decision, not
 # something an admin of one side should do by accident.
 with core_cursor(commit=True) as(_,c):
  c.execute(
      """SELECT x.id
           FROM owner_property_access x
           JOIN owner_accounts oa ON oa.id=x.owner_account_id
           JOIN contacts ct ON ct.id=oa.contact_id
           JOIN properties p ON p.id=x.property_id
          WHERE x.id=%s AND ct.agency_id=%s AND p.agency_id=%s
            FOR UPDATE OF x FOR SHARE OF oa,ct,p""",
      (i,agency_id,agency_id),
  )
  if not c.fetchone():raise NotFoundError(NF)
  c.execute("UPDATE owner_property_access SET access_status='revoked',revoked_at=NOW(),updated_at=NOW() WHERE id=%s RETURNING *",(i,));r=one(c)
 audit('access_revoked',r['owner_account_id'],r['property_id'],'owner_access',i);return r
def create_token(agency_id,i,typ='login',minutes=30,by=None):
 # This mints the credential the owner logs in with, so it is the operation
 # where a missing tenant check is worst: it would hand an administrator of one
 # agency a working session for an owner of another. The check and the INSERT
 # share one transaction, as everywhere else in this slice.
 raw=generate_secret()
 with core_cursor(commit=True) as(_,c):
  _require_account_in_agency(c,agency_id,i)
  c.execute("INSERT INTO owner_access_tokens(owner_account_id,token_hash,token_type,expires_at,created_by) VALUES(%s,%s,%s,%s,%s) RETURNING *",(i,hash_secret(raw),typ,utcnow()+timedelta(minutes=minutes),by));r=one(c)
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
# P26-6C OWNER Portal: the portal follows a grant, so the grant must be
# coherent. Every portal query that joins owner_property_access adds these
# three joins and the predicate below, which require the grant's two roots -
# the account's contact and the property - to name the SAME agency.
#
# No agency parameter: the portal is account-scoped by design and its identity
# is the session, not an operator context. What this rule removes is the ability
# to follow a grant written before create_access started checking, one whose two
# roots disagree. Such rows are hidden, never repaired: repointing or deleting
# them is a data decision.
_COHERENT_GRANT = """
           JOIN owner_accounts oa_g ON oa_g.id=x.owner_account_id
           JOIN contacts ct_g ON ct_g.id=oa_g.contact_id
           JOIN properties p_g ON p_g.id=x.property_id"""
_GRANT_ROOTS_AGREE = " AND ct_g.agency_id=p_g.agency_id"


def require_property(a,p):
 with core_cursor() as(_,c):c.execute("SELECT x.*,p.title,p.address,p.city FROM owner_property_access x JOIN properties p ON p.id=x.property_id"+_COHERENT_GRANT+" WHERE x.owner_account_id=%s AND x.property_id=%s"+_GRANT_ROOTS_AGREE+" AND x.access_status='active' AND x.revoked_at IS NULL AND (x.valid_until IS NULL OR x.valid_until>NOW())",(a,p));return one(c)
def portal_properties(a):
 with core_cursor() as(_,c):c.execute("SELECT p.id,p.title,p.address,p.city,x.access_role,x.is_primary FROM owner_property_access x JOIN properties p ON p.id=x.property_id"+_COHERENT_GRANT+" WHERE x.owner_account_id=%s"+_GRANT_ROOTS_AGREE+" AND x.access_status='active' AND x.revoked_at IS NULL AND (x.valid_until IS NULL OR x.valid_until>NOW()) ORDER BY x.is_primary DESC",(a,));return[dict(x) for x in c.fetchall()]
def create_publication(agency_id,d):
 with core_cursor(commit=True) as(_,c):
  _require_property_in_agency(c,agency_id,d['property_id'])
  c.execute("INSERT INTO owner_publications(property_id,publication_type,title,summary,body,status,version_number,acknowledgement_required) VALUES(%s,%s,%s,%s,%s,'draft',1,%s) RETURNING *",(d['property_id'],d['publication_type'],d['title'],d.get('summary'),d['body'],d.get('acknowledgement_required',False)));r=one(c)
 audit('publication_created',prop=r['property_id'],etype='owner_publication',eid=r['id']);return r
def get_publication(agency_id,i):
 with core_cursor() as(_,c):return _publication_in_agency(c,agency_id,i)
def list_publications(agency_id):
 with core_cursor() as(_,c):
  c.execute(
      """SELECT pub.* FROM owner_publications pub
           JOIN properties p ON p.id=pub.property_id
          WHERE p.agency_id=%s
          ORDER BY pub.created_at DESC""",
      (agency_id,),
  )
  return[dict(x) for x in c.fetchall()]
def update_publication(agency_id,i,d):
 # The check and the UPDATE share one transaction: it used to resolve the row
 # on one connection and write on another.
 with core_cursor(commit=True) as(_,c):
  r=_publication_in_agency(c,agency_id,i,for_update=True)
  if r['status']!='draft':raise ConflictError('Una pubblicazione pubblicata o archiviata è immutabile')
  f=[];v=[]
  for k in('publication_type','title','summary','body','acknowledgement_required'):
   if d.get(k) is not None:f.append(k+'=%s');v.append(d[k])
  if not f:return r
  v.append(i)
  c.execute('UPDATE owner_publications SET '+','.join(f)+',updated_at=NOW() WHERE id=%s RETURNING *',v);return one(c)
def publish(agency_id,i):
 with core_cursor(commit=True) as(_,c):
  r=_publication_in_agency(c,agency_id,i,for_update=True)
  if r['status']!='draft':raise ConflictError('Solo draft pubblicabile')
  c.execute("UPDATE owner_publications SET status='published',published_at=NOW() WHERE id=%s RETURNING *",(i,));z=one(c)
  _emit_notification_event(
      c,
      property_id=z['property_id'],
      notification_type='publication_published',
      preference_column='publication_enabled',
      title=z['title'],
      body='È disponibile un nuovo aggiornamento sul tuo immobile.',
      target_type='owner_publication',
      target_id=z['id'],
  )
  _audit_with_cursor(c,'publication_published',prop=z['property_id'],etype='owner_publication',eid=i)
 return z
def archive(agency_id,i):
 with core_cursor(commit=True) as(_,c):
  r=_publication_in_agency(c,agency_id,i,for_update=True)
  if r['status']!='published':raise ConflictError('Solo published archiviabile')
  c.execute("UPDATE owner_publications SET status='archived',archived_at=NOW() WHERE id=%s RETURNING *",(i,));z=one(c)
 audit('publication_archived',prop=z['property_id'],etype='owner_publication',eid=i);return z
def supersede(agency_id,i,d):
 with core_cursor(commit=True) as(_,c):
  old=_publication_in_agency(c,agency_id,i,for_update=True)
  if old['status']!='published':raise ConflictError('Solo published sostituibile')
  c.execute("INSERT INTO owner_publications(property_id,publication_type,title,summary,body,status,version_number,supersedes_publication_id,acknowledgement_required) VALUES(%s,%s,%s,%s,%s,'draft',%s,%s,%s) RETURNING *",(old['property_id'],d['publication_type'],d['title'],d.get('summary'),d['body'],old['version_number']+1,old['id'],d.get('acknowledgement_required',False)));new=one(c);c.execute('UPDATE owner_publications SET superseded_by_publication_id=%s WHERE id=%s',(new['id'],old['id']))
 audit('publication_version_created',prop=old['property_id'],etype='owner_publication',eid=new['id'],meta={'previous':old['id']});return new
def timeline(a,p):
 require_property(a,p)
 with core_cursor() as(_,c):c.execute("SELECT id,property_id,publication_type,title,summary,body,published_at,version_number,acknowledgement_required FROM owner_publications WHERE property_id=%s AND status='published' ORDER BY published_at DESC",(p,));return[dict(x) for x in c.fetchall()]
def publication(a,i):
 with core_cursor() as(_,c):c.execute("SELECT p.* FROM owner_publications p JOIN owner_property_access x ON x.property_id=p.property_id"+_COHERENT_GRANT+" WHERE p.id=%s AND p.status='published'"+_GRANT_ROOTS_AGREE+" AND x.owner_account_id=%s AND x.access_status='active' AND x.revoked_at IS NULL",(i,a));return one(c)
def read(a,i,ack=False):
 p=publication(a,i)
 with core_cursor(commit=True) as(_,c):c.execute("INSERT INTO owner_publication_reads(publication_id,owner_account_id,view_count,acknowledged_at) VALUES(%s,%s,1,CASE WHEN %s THEN NOW() END) ON CONFLICT(publication_id,owner_account_id) DO UPDATE SET last_viewed_at=NOW(),view_count=owner_publication_reads.view_count+1,acknowledged_at=CASE WHEN %s THEN COALESCE(owner_publication_reads.acknowledged_at,NOW()) ELSE owner_publication_reads.acknowledged_at END RETURNING *",(i,a,ack,ack));r=one(c)
 audit('publication_acknowledged' if ack else 'publication_viewed',a,p['property_id'],'owner_publication',i);return r
FEEDBACK_PUBLIC_FIELDS = (
    "feedback_type",
    "subject",
    "message",
    "status",
    "submitted_at",
    "availability_from",
    "availability_to",
    "handled_at",
    "public_response",
)


def _public_feedback(row):
 return {key: row.get(key) for key in FEEDBACK_PUBLIC_FIELDS}


def create_feedback(a,p,d):
 with core_cursor(commit=True) as(_,c):
  c.execute(
      """SELECT oa.contact_id
           FROM owner_accounts oa
           JOIN owner_property_access x ON x.owner_account_id=oa.id
           JOIN contacts ct_g ON ct_g.id=oa.contact_id
           JOIN properties p_g ON p_g.id=x.property_id
          WHERE oa.id=%s AND oa.status='active' AND x.property_id=%s
            AND ct_g.agency_id=p_g.agency_id
            AND x.access_status='active' AND x.revoked_at IS NULL
            AND (x.valid_until IS NULL OR x.valid_until>NOW())
          FOR UPDATE OF oa,x FOR SHARE OF ct_g,p_g""",
      (a,p),
  )
  access=c.fetchone()
  if not access:raise NotFoundError(NF)
  contact_id=access['contact_id']
  # P26-6C: the owner account's tenant, read on the same cursor and inside the
  # same transaction as the row it will stamp. This is the OWNER chain the
  # architecture settles - owner_account -> contact -> agency - resolved here
  # because this is the only layer that knows it. It is a read, not a
  # migration: no OWNER table or route changes in this slice.
  c.execute("SELECT agency_id FROM contacts WHERE id=%s",(contact_id,))
  owner_agency=one(c)['agency_id']
  c.execute(
      """INSERT INTO owner_feedback(
             owner_account_id,property_id,feedback_type,subject,message,status,submitted_at,
             availability_from,availability_to
         ) VALUES(%s,%s,%s,%s,%s,'new',NOW(),%s,%s)
         RETURNING id,feedback_type,subject,message,status,submitted_at,
                   availability_from,availability_to,handled_at,public_response""",
      (a,p,d['feedback_type'],d['subject'],d['message'],d.get('availability_from'),d.get('availability_to')),
  )
  r=one(c)
  activity=create_activity_with_cursor(c,{
      'contact_id':contact_id,
      'lead_id':None,
      'stima_id':None,
      'activity_type':'note',
      'direction':'in',
      'channel':'owner_portal',
      'subject':r['subject'],
      'description':r['message'],
      'outcome':None,
      'occurred_at':r['submitted_at'],
      'created_by':None,
      'metadata':{
          'source_module':'owner',
          'owner_feedback_id':r['id'],
          'owner_request_type':r['feedback_type'],
          'property_id':p,
      },
  })
  c.execute(
      """UPDATE owner_feedback
            SET linked_activity_id=%s,updated_at=NOW()
          WHERE id=%s AND linked_activity_id IS NULL""",
      (activity['id'],r['id']),
  )
  if c.rowcount != 1:raise ConflictError('Collegamento activity OWNER non riuscito')
  flow_event=record_owner_request_event_with_cursor(c,{
      'source_module':'owner',
      'event_type':'owner.request_submitted',
      'entity_type':'owner_feedback',
      'entity_id':r['id'],
      'deduplication_key':f"owner:feedback:{r['id']}:submitted",
      'payload':{
          'owner_request_type':r['feedback_type'],
          'property_id':p,
          'contact_id':contact_id,
          'linked_activity_id':activity['id'],
      },
      'occurred_at':r['submitted_at'],
  },agency_id=owner_agency)
  _audit_with_cursor(c,'feedback_submitted',a,p,'owner_feedback',r['id'])
 result=_public_feedback(r)
 process_saved_owner_request_event(flow_event['id'])
 return result

def admin_list_feedback(agency_id):
 """The OWNER Admin listing. Both roots, as everywhere a row touches both."""
 with core_cursor() as(_,c):
  c.execute(
      """SELECT f.* FROM owner_feedback f
           JOIN owner_accounts oa ON oa.id=f.owner_account_id
           JOIN contacts ct ON ct.id=oa.contact_id
           JOIN properties p ON p.id=f.property_id
          WHERE ct.agency_id=%s AND p.agency_id=%s
          ORDER BY f.submitted_at DESC""",
      (agency_id,agency_id),
  )
  return[dict(x) for x in c.fetchall()]

def list_feedback(a,p):
 """The portal path. Both arguments are required now: the admin branch that
 used to live here read every agency's feedback when called with none, and it
 has moved to admin_list_feedback with a tenant of its own."""
 if True:
  # Portal path: revalidate the canonical account-property grant on every read.
  # current_owner separately guarantees that a disabled account has no valid session.
  require_property(a,p)
  with core_cursor() as(_,c):
   c.execute(
       """SELECT feedback_type,subject,message,status,submitted_at,availability_from,
                 availability_to,handled_at,public_response
            FROM owner_feedback
           WHERE owner_account_id=%s AND property_id=%s
           ORDER BY submitted_at DESC""",
       (a,p),
   )
   return [_public_feedback(dict(x)) for x in c.fetchall()]
def dashboard(agency_id):
 # Four counters, four scoped subqueries. The two that touch both roots -
 # grants and feedback - require both, so a legacy row whose roots disagree is
 # counted by neither agency, the same answer list_access gives.
 with core_cursor() as(_,c):
  c.execute(
      """SELECT
           (SELECT COUNT(*) FROM owner_accounts oa
              JOIN contacts ct ON ct.id=oa.contact_id
             WHERE oa.status='active' AND ct.agency_id=%s) active_accounts,
           (SELECT COUNT(*) FROM owner_property_access x
              JOIN owner_accounts oa ON oa.id=x.owner_account_id
              JOIN contacts ct ON ct.id=oa.contact_id
              JOIN properties p ON p.id=x.property_id
             WHERE x.access_status='active'
               AND ct.agency_id=%s AND p.agency_id=%s) active_access,
           (SELECT COUNT(*) FROM owner_publications pub
              JOIN properties p ON p.id=pub.property_id
             WHERE pub.status='published' AND p.agency_id=%s) published,
           (SELECT COUNT(*) FROM owner_feedback f
              JOIN owner_accounts oa ON oa.id=f.owner_account_id
              JOIN contacts ct ON ct.id=oa.contact_id
              JOIN properties p ON p.id=f.property_id
             WHERE f.status='new'
               AND ct.agency_id=%s AND p.agency_id=%s) new_feedback""",
      (agency_id,)*6,
  )
  return dict(c.fetchone())
def audits(agency_id):
 # owner_audit_log is the one OWNER table with no guaranteed tenant: both its
 # parents are nullable ON DELETE SET NULL, so a row can outlive them both.
 # A row is this agency's when EITHER surviving parent says so; a row that has
 # lost both is in no agency's view. That is a deliberate consequence of the
 # schema and is why the audit view is not a complete history.
 with core_cursor() as(_,c):
  c.execute(
      """SELECT al.*
           FROM owner_audit_log al
           LEFT JOIN owner_accounts oa ON oa.id=al.owner_account_id
           LEFT JOIN contacts ct ON ct.id=oa.contact_id
           LEFT JOIN properties p ON p.id=al.property_id
          WHERE ct.agency_id=%s OR p.agency_id=%s
          ORDER BY al.created_at DESC
          LIMIT 200""",
      (agency_id,agency_id),
  )
  return[dict(x) for x in c.fetchall()]

# OWNER 0.2 P2/P4 ------------------------------------------------------------
SHARED_DOCUMENT_TYPE_LABELS = {
    "mandate": "Incarico",
    "floor_plan": "Planimetria",
    "ape": "Attestato energetico",
    "cadastral_extract": "Documento catastale",
    "photo_report": "Report fotografico",
    "activity_report": "Report attività",
    "information": "Documento informativo",
}


def _property_for_document(c, agency_id, document_id, *, for_update=False):
    """The source PROPERTY document, inside the caller's agency.

    P26-6C: property_documents is CHILD-DERIVED - it carries no agency_id of
    its own - so the tenant comes from its property, as a join rather than a
    value compared afterwards.
    """
    suffix = " FOR UPDATE OF pd" if for_update else ""
    c.execute(
        """SELECT pd.id,pd.property_id,pd.document_type,pd.title,pd.url,pd.storage_key,
                  pd.status,pd.expires_at,pd.metadata,pd.created_at,pd.updated_at
           FROM property_documents pd
           JOIN properties p ON p.id=pd.property_id
           WHERE pd.id=%s AND p.agency_id=%s""" + suffix,
        (document_id, agency_id),
    )
    return one(c)


def _property_for_visit(c, agency_id, visit_id):
    """The PROPERTY visit, inside the caller's agency. Same derivation as
    _property_for_document: property_visits is CHILD-DERIVED too."""
    c.execute(
        """SELECT pv.id,pv.property_id,pv.scheduled_at,pv.status
           FROM property_visits pv
           JOIN properties p ON p.id=pv.property_id
           WHERE pv.id=%s AND p.agency_id=%s""",
        (visit_id, agency_id),
    )
    return one(c)


def _validate_target_account(c, agency_id, account_id, property_id):
    """The grant that lets this document or feedback be aimed at one owner.

    P26-6C: both roots. The account reaches an agency through its contact and
    the property carries its own, so a grant written before create_access
    started checking - one whose two roots disagree - can no longer be used to
    target an owner of another agency.
    """
    if account_id is None:
        return
    c.execute(
        """SELECT 1
           FROM owner_property_access x
           JOIN owner_accounts oa ON oa.id=x.owner_account_id
           JOIN contacts ct ON ct.id=oa.contact_id
           JOIN properties p ON p.id=x.property_id
           WHERE x.owner_account_id=%s AND x.property_id=%s
             AND ct.agency_id=%s AND p.agency_id=%s
             AND x.access_status='active' AND x.revoked_at IS NULL
             AND (x.valid_until IS NULL OR x.valid_until>NOW())""",
        (account_id, property_id, agency_id, agency_id),
    )
    if not c.fetchone():
        raise NotFoundError(NF)


def _shared_document_with_source(c, agency_id, item_id, *, for_update=False):
    """The shared document with its source, inside the caller's agency.

    owner_shared_documents.owner_account_id is nullable, so the account is not
    a dependable root here: the tenant comes through the source document to its
    property. Every admin path to a shared document goes through this helper,
    including the download, so the predicate lives in one place.
    """
    suffix = " FOR UPDATE OF sd" if for_update else ""
    c.execute(
        """SELECT sd.*,pd.property_id,pd.title AS source_title,
                  pd.document_type AS source_document_type,pd.status AS source_status,
                  pd.expires_at AS source_expires_at,pd.storage_key,pd.url,
                  pd.metadata AS source_metadata
           FROM owner_shared_documents sd
           JOIN property_documents pd ON pd.id=sd.property_document_id
           JOIN properties p ON p.id=pd.property_id
           WHERE sd.id=%s AND p.agency_id=%s""" + suffix,
        (item_id, agency_id),
    )
    return one(c)


def _admin_shared_document(row):
    """Explicit admin DTO; storage locators and raw metadata are intentionally absent."""
    result = {
        "id": row["id"],
        "property_document_id": row["property_document_id"],
        "property_id": row["property_id"],
        "owner_account_id": row.get("owner_account_id"),
        "public_title": row["public_title"],
        "public_document_type": row["public_document_type"],
        "version_number": row["version_number"],
        "status": row["status"],
        "published_at": row.get("published_at"),
        "expires_at": row.get("expires_at"),
        "acknowledgement_required": row["acknowledgement_required"],
        "supersedes_shared_document_id": row.get("supersedes_shared_document_id"),
        "superseded_by_shared_document_id": row.get("superseded_by_shared_document_id"),
        "revoked_at": row.get("revoked_at"),
        "archived_at": row.get("archived_at"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "created_by": row.get("created_by"),
        "revoked_by": row.get("revoked_by"),
        "source_title": row.get("source_title"),
        "source_document_type": row.get("source_document_type"),
        "source_status": row.get("source_status"),
        "source_expires_at": row.get("source_expires_at"),
        "file_present": bool(row.get("storage_key")),
    }
    return result


def _source_file_contract(row):
    data = row.get("source_metadata") or row.get("metadata") or {}
    if not isinstance(data, dict):
        data = {}
    return {
        "storage_key": row.get("storage_key"),
        "mime_type": str(data.get("mime_detected") or "").lower(),
        "size_bytes": int(data.get("size_bytes") or 0),
        "sha256": str(data.get("sha256") or ""),
        "download_filename": str(data.get("sanitized_filename") or row.get("source_title") or "documento"),
        "storage_provider": str(data.get("storage_provider") or ""),
    }


def _validate_source_contract(row, storage, *, verify_provider=True):
    from .document_storage import ALLOWED_MIME_TYPES, DEFAULT_MAX_BYTES, StorageMetadataMismatch

    if row.get("source_status") != "available" and row.get("status") != "available":
        raise ValidationError("Documento PROPERTY non disponibile")
    contract = _source_file_contract(row)
    if not contract["storage_key"]:
        raise ValidationError("Documento privo di storage privato")
    if contract["mime_type"] not in ALLOWED_MIME_TYPES:
        raise ValidationError("MIME del documento sorgente non ammesso")
    if contract["size_bytes"] < 1 or contract["size_bytes"] > DEFAULT_MAX_BYTES:
        raise ValidationError("Dimensione documento sorgente non valida")
    if len(contract["sha256"]) != 64:
        raise ValidationError("Checksum documento sorgente non valido")
    source_expiry = (
        row.get("source_expires_at")
        if row.get("source_status") == "available"
        else row.get("expires_at")
    )
    if source_expiry is not None and source_expiry < date.today():
        raise ValidationError("Documento PROPERTY scaduto")
    if not storage.is_configured():
        from .document_storage import StorageNotConfigured

        raise StorageNotConfigured("Storage documentale non configurato")
    if verify_provider:
        remote = storage.head_object(contract["storage_key"])
        if remote.size_bytes != contract["size_bytes"]:
            raise StorageMetadataMismatch("Dimensione storage non coerente")
        if remote.content_type != contract["mime_type"]:
            raise StorageMetadataMismatch("MIME storage non coerente")
        if remote.sha256 and remote.sha256 != contract["sha256"]:
            raise StorageMetadataMismatch("Checksum storage non coerente")
    return contract


def create_shared_document(agency_id, d):
    with core_cursor(commit=True) as (_, c):
        src = _property_for_document(c, agency_id, d["property_document_id"])
        if src["status"] != "available" or not src.get("storage_key"):
            raise ValidationError("Il documento deve essere disponibile in storage privato")
        _validate_target_account(c, agency_id, d.get("owner_account_id"), src["property_id"])
        c.execute(
            """INSERT INTO owner_shared_documents(
                   property_document_id,owner_account_id,public_title,public_document_type,
                   expires_at,acknowledgement_required,created_by
               ) VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
            (
                d["property_document_id"],
                d.get("owner_account_id"),
                d["public_title"],
                d["public_document_type"],
                d.get("expires_at"),
                d.get("acknowledgement_required", False),
                d.get("created_by"),
            ),
        )
        result = one(c)
        _audit_with_cursor(
            c,
            "shared_document_created",
            d.get("owner_account_id"),
            src["property_id"],
            "owner_shared_document",
            result["id"],
            meta={"source": "linked_existing"},
        )
    result.update(property_id=src["property_id"], source_title=src["title"], source_status=src["status"])
    return _admin_shared_document(result)


def create_uploaded_shared_document(agency_id, d, staged, storage=None):
    """Upload a private file, then atomically create PROPERTY and OWNER records."""
    from .document_storage import (
        DocumentStorageError,
        get_document_storage,
        storage_metadata_for_database,
    )

    storage = storage or get_document_storage()
    if not storage.is_configured():
        from .document_storage import StorageNotConfigured

        raise StorageNotConfigured("Storage documentale non configurato")
    key = storage.generate_key()
    uploaded = True  # the provider may create a partial object before raising
    try:
        storage.put_object(
            staged.fileobj,
            key=key,
            content_type=staged.mime_detected,
            size_bytes=staged.size_bytes,
            sha256=staged.sha256,
        )
        metadata = storage_metadata_for_database(staged, provider=storage.provider_name)
        with core_cursor(commit=True) as (_, c):
            c.execute("SELECT id FROM properties WHERE id=%s", (d["property_id"],))
            if not c.fetchone():
                raise NotFoundError(NF)

            previous_id = d.get("supersedes_shared_document_id")
            target_account = d.get("owner_account_id")
            version_number = 1
            if previous_id is not None:
                previous = _shared_document_with_source(c, agency_id, previous_id, for_update=True)
                if previous["status"] != "published":
                    raise ConflictError("Solo un documento published può essere sostituito")
                if previous.get("superseded_by_shared_document_id") is not None:
                    raise ConflictError("Solo la versione corrente può essere sostituita")
                if previous["property_id"] != d["property_id"]:
                    raise ConflictError("La versione precedente appartiene a un altro immobile")
                if target_account is not None and target_account != previous.get("owner_account_id"):
                    raise ConflictError("Il destinatario non può cambiare nella catena versioni")
                target_account = previous.get("owner_account_id")
                version_number = int(previous["version_number"]) + 1
                c.execute(
                    """SELECT 1 FROM owner_shared_documents
                       WHERE supersedes_shared_document_id=%s
                         AND status IN ('draft','published') LIMIT 1""",
                    (previous_id,),
                )
                if c.fetchone():
                    raise ConflictError("Esiste già una versione successiva attiva")

            _validate_target_account(c, agency_id, target_account, d["property_id"])
            c.execute(
                """INSERT INTO property_documents(
                       property_id,document_type,title,storage_key,status,metadata
                   ) VALUES(%s,%s,%s,%s,'available',%s) RETURNING *""",
                (
                    d["property_id"],
                    d["document_type"],
                    d["source_title"],
                    key,
                    Json(metadata),
                ),
            )
            source = one(c)
            c.execute(
                """INSERT INTO owner_shared_documents(
                       property_document_id,owner_account_id,public_title,public_document_type,
                       version_number,expires_at,acknowledgement_required,
                       supersedes_shared_document_id,created_by
                   ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (
                    source["id"],
                    target_account,
                    d["public_title"],
                    d["public_document_type"],
                    version_number,
                    d.get("expires_at"),
                    d.get("acknowledgement_required", False),
                    previous_id,
                    d.get("created_by"),
                ),
            )
            result = one(c)
            _audit_with_cursor(
                c,
                "shared_document_uploaded",
                target_account,
                d["property_id"],
                "owner_shared_document",
                result["id"],
                meta={
                    "size_bytes": staged.size_bytes,
                    "mime_type": staged.mime_detected,
                    "source": "upload_admin",
                    "supersedes": previous_id,
                },
            )
            _audit_with_cursor(
                c,
                "shared_document_version_created" if previous_id is not None else "shared_document_created",
                target_account,
                d["property_id"],
                "owner_shared_document",
                result["id"],
                meta={
                    "source": "upload_admin",
                    "previous": previous_id,
                    "version_number": version_number,
                },
            )
        result.update(
            property_id=d["property_id"],
            source_title=d["source_title"],
            source_document_type=d["document_type"],
            source_status="available",
            storage_key=key,
        )
        return _admin_shared_document(result)
    except Exception as exc:
        if uploaded:
            try:
                storage.delete_object(key)
            except Exception as cleanup_exc:
                try:
                    audit(
                        "shared_document_cleanup_failed",
                        account=d.get("owner_account_id"),
                        prop=d.get("property_id"),
                        etype="owner_shared_document",
                        result="error",
                        meta={
                            "error_code": getattr(cleanup_exc, "error_code", "storage_error"),
                            "stage": "db_compensation",
                        },
                    )
                except Exception:
                    pass
        if isinstance(exc, DocumentStorageError):
            raise
        raise


def list_shared_documents(
    agency_id,
    property_id=None,
    status=None,
    owner_account_id=None,
    document_type=None,
    limit=100,
    offset=0,
):
    # The tenant is the first filter and is not optional: the caller-supplied
    # ones narrow within it and can never widen past it.
    filters = ["p.agency_id=%s"]
    values = [agency_id]
    if property_id is not None:
        filters.append("pd.property_id=%s")
        values.append(property_id)
    if status is not None:
        filters.append("sd.status=%s")
        values.append(status)
    if owner_account_id is not None:
        filters.append("sd.owner_account_id=%s")
        values.append(owner_account_id)
    if document_type is not None:
        filters.append("sd.public_document_type=%s")
        values.append(document_type)
    where = " WHERE " + " AND ".join(filters)
    values.extend((limit, offset))
    with core_cursor() as (_, c):
        c.execute(
            """SELECT sd.*,pd.property_id,pd.title AS source_title,
                      pd.document_type AS source_document_type,pd.status AS source_status,
                      pd.expires_at AS source_expires_at,pd.storage_key
               FROM owner_shared_documents sd
               JOIN property_documents pd ON pd.id=sd.property_document_id
               JOIN properties p ON p.id=pd.property_id"""
            + where
            + " ORDER BY sd.created_at DESC LIMIT %s OFFSET %s",
            values,
        )
        return [_admin_shared_document(dict(row)) for row in c.fetchall()]


def get_shared_document(agency_id, i):
    with core_cursor() as (_, c):
        return _admin_shared_document(_shared_document_with_source(c, agency_id, i))


def update_shared_document(agency_id, i, d):
    old = get_shared_document(agency_id, i)
    if old["status"] != "draft":
        raise ConflictError("Un documento pubblicato, revocato o archiviato è immutabile")
    fields = []
    values = []
    for key in ("public_title", "public_document_type", "expires_at", "acknowledgement_required"):
        if key in d:
            fields.append(key + "=%s")
            values.append(d[key])
    if not fields:
        return old
    values.append(i)
    with core_cursor(commit=True) as (_, c):
        c.execute(
            "UPDATE owner_shared_documents SET "
            + ",".join(fields)
            + ",updated_at=NOW() WHERE id=%s AND status='draft' RETURNING *",
            values,
        )
        result = one(c)
        _audit_with_cursor(
            c,
            "shared_document_updated",
            old.get("owner_account_id"),
            old["property_id"],
            "owner_shared_document",
            i,
        )
    result.update(old)
    result.update({key: value for key, value in d.items() if key in result})
    return result


def publish_shared_document(agency_id, i, storage=None):
    from .document_storage import get_document_storage

    storage = storage or get_document_storage()
    with core_cursor() as (_, c):
        preflight = _shared_document_with_source(c, agency_id, i)
    if preflight["status"] != "draft":
        raise ConflictError("Solo draft pubblicabile")
    _validate_source_contract(preflight, storage, verify_provider=True)

    with core_cursor(commit=True) as (_, c):
        current = _shared_document_with_source(c, agency_id, i, for_update=True)
        if current["status"] != "draft":
            raise ConflictError("Solo draft pubblicabile")
        _validate_target_account(c, agency_id, current.get("owner_account_id"), current["property_id"])
        if current.get("expires_at") is not None:
            c.execute("SELECT (%s > NOW()) AS valid", (current["expires_at"],))
            if not c.fetchone()["valid"]:
                raise ValidationError("Scadenza condivisione non valida")
        previous_id = current.get("supersedes_shared_document_id")
        if previous_id is not None:
            c.execute("SELECT * FROM owner_shared_documents WHERE id=%s FOR UPDATE", (previous_id,))
            previous = one(c)
            if previous["status"] != "published":
                raise ConflictError("La versione precedente non è pubblicata")
            if previous.get("superseded_by_shared_document_id") not in (None, i):
                raise ConflictError("La versione precedente è già stata sostituita")
            if previous.get("owner_account_id") != current.get("owner_account_id"):
                raise ConflictError("Catena versioni non coerente")
            previous_source = _property_for_document(c, agency_id, previous["property_document_id"])
            if previous_source["property_id"] != current["property_id"]:
                raise ConflictError("Catena versioni non coerente")
        c.execute(
            """UPDATE owner_shared_documents
               SET status='published',published_at=NOW(),updated_at=NOW()
               WHERE id=%s RETURNING *""",
            (i,),
        )
        result = one(c)
        if previous_id is not None:
            c.execute(
                """UPDATE owner_shared_documents
                   SET superseded_by_shared_document_id=%s,updated_at=NOW()
                   WHERE id=%s""",
                (i, previous_id),
            )
        _emit_notification_event(
            c,
            property_id=current["property_id"],
            notification_type="shared_document_published",
            preference_column="document_enabled",
            title=current["public_title"],
            body="È disponibile un nuovo documento condiviso.",
            target_type="owner_shared_document",
            target_id=i,
            owner_account_id=current.get("owner_account_id"),
        )
        _audit_with_cursor(
            c,
            "shared_document_published",
            current.get("owner_account_id"),
            current["property_id"],
            "owner_shared_document",
            i,
            meta={
                "version_number": current["version_number"],
                "supersedes": previous_id,
            },
        )
    result.update(current)
    result["status"] = "published"
    return _admin_shared_document(result)


def revoke_shared_document(agency_id, i, actor=None, reason=None):
    old = get_shared_document(agency_id, i)
    if old["status"] != "published":
        raise ConflictError("Solo published revocabile")
    with core_cursor(commit=True) as (_, c):
        c.execute(
            """UPDATE owner_shared_documents
               SET status='revoked',revoked_at=NOW(),revoked_by=%s,updated_at=NOW()
               WHERE id=%s AND status='published' RETURNING *""",
            (actor, i),
        )
        result = one(c)
        _audit_with_cursor(
            c,
            "shared_document_revoked",
            old.get("owner_account_id"),
            old["property_id"],
            "owner_shared_document",
            i,
            meta={"reason": reason} if reason else None,
        )
    result.update(old)
    result["status"] = "revoked"
    return result


def archive_shared_document(agency_id, i):
    old = get_shared_document(agency_id, i)
    if old["status"] not in ("published", "revoked"):
        raise ConflictError("Solo published o revoked archiviabile")
    with core_cursor(commit=True) as (_, c):
        c.execute(
            """UPDATE owner_shared_documents
               SET status='archived',archived_at=NOW(),updated_at=NOW()
               WHERE id=%s AND status IN ('published','revoked') RETURNING *""",
            (i,),
        )
        result = one(c)
        _audit_with_cursor(
            c,
            "shared_document_archived",
            old.get("owner_account_id"),
            old["property_id"],
            "owner_shared_document",
            i,
            meta={"version_number": old["version_number"]},
        )
    result.update(old)
    result["status"] = "archived"
    return result


def supersede_shared_document(agency_id, i, d):
    with core_cursor(commit=True) as (_, c):
        old = _shared_document_with_source(c, agency_id, i, for_update=True)
        if old["status"] != "published":
            raise ConflictError("Solo published sostituibile")
        if old.get("superseded_by_shared_document_id") is not None:
            raise ConflictError("Solo la versione corrente può essere sostituita")
        c.execute(
            """SELECT 1 FROM owner_shared_documents
               WHERE supersedes_shared_document_id=%s AND status IN ('draft','published')
               LIMIT 1""",
            (i,),
        )
        if c.fetchone():
            raise ConflictError("Esiste già una versione successiva attiva")
        new_document_id = d.get("property_document_id") or old["property_document_id"]
        source = _property_for_document(c, agency_id, new_document_id)
        if source["property_id"] != old["property_id"]:
            raise ConflictError("Il nuovo documento appartiene a un altro immobile")
        if source["status"] != "available" or not source.get("storage_key"):
            raise ValidationError("Il nuovo documento non è disponibile in storage privato")
        _validate_target_account(c, agency_id, old.get("owner_account_id"), old["property_id"])
        c.execute(
            """SELECT COALESCE(MAX(version_number),0)+1 AS next_version
               FROM owner_shared_documents
               WHERE property_document_id=%s
                 AND owner_account_id IS NOT DISTINCT FROM %s""",
            (new_document_id, old.get("owner_account_id")),
        )
        next_version = max(old["version_number"] + 1, int(c.fetchone()["next_version"]))
        c.execute(
            """INSERT INTO owner_shared_documents(
                   property_document_id,owner_account_id,public_title,public_document_type,
                   version_number,status,expires_at,acknowledgement_required,
                   supersedes_shared_document_id,created_by
               ) VALUES(%s,%s,%s,%s,%s,'draft',%s,%s,%s,%s) RETURNING *""",
            (
                new_document_id,
                old.get("owner_account_id"),
                d["public_title"],
                d["public_document_type"],
                next_version,
                d.get("expires_at"),
                d.get("acknowledgement_required", False),
                i,
                d.get("created_by"),
            ),
        )
        result = one(c)
        _audit_with_cursor(
            c,
            "shared_document_version_created",
            old.get("owner_account_id"),
            old["property_id"],
            "owner_shared_document",
            result["id"],
            meta={"previous": i, "version_number": next_version},
        )
    result.update(
        property_id=old["property_id"],
        source_title=source["title"],
        source_document_type=source["document_type"],
        source_status=source["status"],
        storage_key=source["storage_key"],
    )
    return _admin_shared_document(result)


_PORTAL_SHARED_DOCUMENT_SELECT = """SELECT sd.id,sd.public_title,sd.public_document_type,
       sd.version_number,sd.published_at,sd.expires_at,sd.acknowledgement_required,
       pd.property_id,pd.status AS source_status,
       pd.metadata->>'mime_detected' AS mime_type,
       NULLIF(pd.metadata->>'size_bytes','')::BIGINT AS size_bytes,
       pd.metadata->>'sanitized_filename' AS download_filename,
       dr.first_viewed_at,dr.last_viewed_at,dr.view_count,dr.acknowledged_at
"""


def _public_shared_document(row):
    return {
        "id": row["id"],
        "public_title": row["public_title"],
        "public_document_type": row["public_document_type"],
        "public_document_type_label": SHARED_DOCUMENT_TYPE_LABELS.get(
            row["public_document_type"], row["public_document_type"]
        ),
        "version_number": row["version_number"],
        "published_at": row.get("published_at"),
        "expires_at": row.get("expires_at"),
        "acknowledgement_required": row["acknowledgement_required"],
        "first_viewed_at": row.get("first_viewed_at"),
        "last_viewed_at": row.get("last_viewed_at"),
        "view_count": row.get("view_count") or 0,
        "acknowledged_at": row.get("acknowledged_at"),
        "mime_type": row.get("mime_type"),
        "size_bytes": row.get("size_bytes"),
        "download_filename": row.get("download_filename"),
        "download_available": row.get("source_status") == "available",
    }


def _authorized_shared_document_source(account_id, item_id):
    """Internal locator lookup used only after complete portal authorization."""
    with core_cursor() as (_, c):
        c.execute(
            """SELECT sd.*,pd.property_id,pd.status AS source_status,
                      pd.storage_key,pd.metadata AS source_metadata,pd.title AS source_title
               FROM owner_shared_documents sd
               JOIN property_documents pd ON pd.id=sd.property_document_id
               JOIN owner_property_access x ON x.property_id=pd.property_id"""
            + _COHERENT_GRANT
            + """
               WHERE sd.id=%s AND sd.status='published'
                 AND sd.superseded_by_shared_document_id IS NULL
                 AND (sd.expires_at IS NULL OR sd.expires_at>NOW())
                 AND (sd.owner_account_id IS NULL OR sd.owner_account_id=%s)"""
            + _GRANT_ROOTS_AGREE
            + """
                 AND x.owner_account_id=%s AND x.access_status='active'
                 AND x.revoked_at IS NULL
                 AND (x.valid_until IS NULL OR x.valid_until>NOW())""",
            (item_id, account_id, account_id),
        )
        return one(c)


def portal_shared_documents(a, p):
    with core_cursor() as (_, c):
        c.execute(
            _PORTAL_SHARED_DOCUMENT_SELECT
            + """FROM owner_shared_documents sd
               JOIN property_documents pd ON pd.id=sd.property_document_id
               JOIN owner_property_access x
                 ON x.property_id=pd.property_id AND x.owner_account_id=%s"""
            + _COHERENT_GRANT
            + """
               LEFT JOIN owner_document_reads dr
                 ON dr.shared_document_id=sd.id AND dr.owner_account_id=%s
               WHERE pd.property_id=%s AND sd.status='published'
                 AND sd.superseded_by_shared_document_id IS NULL"""
            + _GRANT_ROOTS_AGREE
            + """
                 AND pd.status='available'
                 AND (sd.owner_account_id IS NULL OR sd.owner_account_id=%s)
                 AND (sd.expires_at IS NULL OR sd.expires_at>NOW())
                 AND x.access_status='active' AND x.revoked_at IS NULL
                 AND (x.valid_until IS NULL OR x.valid_until>NOW())
               ORDER BY sd.published_at DESC""",
            (a, a, p, a),
        )
        return [_public_shared_document(dict(row)) for row in c.fetchall()]


def portal_shared_document(a, i):
    with core_cursor() as (_, c):
        c.execute(
            _PORTAL_SHARED_DOCUMENT_SELECT
            + """FROM owner_shared_documents sd
               JOIN property_documents pd ON pd.id=sd.property_document_id
               JOIN owner_property_access x ON x.property_id=pd.property_id"""
            + _COHERENT_GRANT
            + """
               LEFT JOIN owner_document_reads dr
                 ON dr.shared_document_id=sd.id AND dr.owner_account_id=%s
               WHERE sd.id=%s AND sd.status='published'
                 AND sd.superseded_by_shared_document_id IS NULL
                 AND pd.status='available'"""
            + _GRANT_ROOTS_AGREE
            + """
                 AND (sd.expires_at IS NULL OR sd.expires_at>NOW())
                 AND (sd.owner_account_id IS NULL OR sd.owner_account_id=%s)
                 AND x.owner_account_id=%s AND x.access_status='active'
                 AND x.revoked_at IS NULL
                 AND (x.valid_until IS NULL OR x.valid_until>NOW())""",
            (a, i, a, a),
        )
        return _public_shared_document(one(c))


def read_shared_document(a, i, ack=False):
    portal_shared_document(a, i)
    source = _authorized_shared_document_source(a, i)
    with core_cursor(commit=True) as (_, c):
        if ack:
            c.execute(
                """INSERT INTO owner_document_reads(
                       shared_document_id,owner_account_id,view_count,acknowledged_at
                   ) VALUES(%s,%s,1,NOW())
                   ON CONFLICT(shared_document_id,owner_account_id) DO UPDATE
                   SET acknowledged_at=COALESCE(owner_document_reads.acknowledged_at,NOW())
                   RETURNING *""",
                (i, a),
            )
        else:
            c.execute(
                """INSERT INTO owner_document_reads(shared_document_id,owner_account_id,view_count)
                   VALUES(%s,%s,1)
                   ON CONFLICT(shared_document_id,owner_account_id) DO UPDATE
                   SET last_viewed_at=NOW(),view_count=owner_document_reads.view_count+1
                   RETURNING *""",
                (i, a),
            )
        result = one(c)
        _audit_with_cursor(
            c,
            "shared_document_acknowledged" if ack else "shared_document_viewed",
            a,
            source["property_id"],
            "owner_shared_document",
            i,
            meta={"source": "acknowledge" if ack else "detail"},
        )
    return result


def prepare_shared_document_download(a, i, storage=None):
    from .document_storage import get_document_storage

    storage = storage or get_document_storage()
    source = _authorized_shared_document_source(a, i)
    contract = _validate_source_contract(source, storage, verify_provider=True)
    opened = storage.open_stream(contract["storage_key"])
    if (
        opened.metadata.size_bytes != contract["size_bytes"]
        or opened.metadata.content_type != contract["mime_type"]
        or (opened.metadata.sha256 and opened.metadata.sha256 != contract["sha256"])
    ):
        opened.close()
        from .document_storage import StorageMetadataMismatch

        raise StorageMetadataMismatch("Oggetto storage non coerente")
    try:
        read_shared_document(a, i, False)
    except Exception:
        opened.close()
        raise
    return {
        "shared_document_id": i,
        "property_id": source["property_id"],
        "owner_account_id": a,
        "filename": contract["download_filename"],
        "mime_type": contract["mime_type"],
        "size_bytes": contract["size_bytes"],
        "opened": opened,
    }


def prepare_admin_shared_document_download(agency_id, i, storage=None):
    from .document_storage import get_document_storage

    storage = storage or get_document_storage()
    with core_cursor() as (_, c):
        source = _shared_document_with_source(c, agency_id, i)
    contract = _validate_source_contract(source, storage, verify_provider=True)
    opened = storage.open_stream(contract["storage_key"])
    if (
        opened.metadata.size_bytes != contract["size_bytes"]
        or opened.metadata.content_type != contract["mime_type"]
        or (opened.metadata.sha256 and opened.metadata.sha256 != contract["sha256"])
    ):
        opened.close()
        from .document_storage import StorageMetadataMismatch

        raise StorageMetadataMismatch("Oggetto storage non coerente")
    audit(
        "shared_document_admin_download_started",
        account=source.get("owner_account_id"),
        prop=source["property_id"],
        etype="owner_shared_document",
        eid=i,
    )
    return {
        "shared_document_id": i,
        "property_id": source["property_id"],
        "owner_account_id": source.get("owner_account_id"),
        "filename": contract["download_filename"],
        "mime_type": contract["mime_type"],
        "size_bytes": contract["size_bytes"],
        "opened": opened,
    }


def audit_shared_document_download(item, *, result="success", reason_code=None, scope="portal"):
    meta = {"scope": scope, "size_bytes": item.get("size_bytes")}
    if reason_code:
        meta["reason_code"] = reason_code
    try:
        audit(
            "shared_document_downloaded" if result == "success" else "shared_document_download_failed",
            account=item.get("owner_account_id"),
            prop=item.get("property_id"),
            etype="owner_shared_document",
            eid=item.get("shared_document_id"),
            result=result,
            meta=meta,
        )
    except Exception:
        pass


def audit_shared_document_access_denied(account, property_id=None, document_id=None, scope="portal", reason_code="not_found"):
    try:
        audit(
            "shared_document_access_denied",
            account=account,
            prop=property_id,
            etype="owner_shared_document",
            eid=document_id,
            result="denied",
            meta={"scope": scope, "reason_code": reason_code},
        )
    except Exception:
        pass


def shared_document_reads(agency_id, i):
    doc = get_shared_document(agency_id, i)
    with core_cursor() as (_, c):
        c.execute(
            """SELECT dr.owner_account_id,dr.first_viewed_at,dr.last_viewed_at,
                      dr.view_count,dr.acknowledged_at
               FROM owner_document_reads dr
               JOIN owner_property_access x
                 ON x.owner_account_id=dr.owner_account_id AND x.property_id=%s
               JOIN owner_accounts oa ON oa.id=dr.owner_account_id
               JOIN contacts ct ON ct.id=oa.contact_id
               JOIN properties p ON p.id=x.property_id
               WHERE dr.shared_document_id=%s
                 AND ct.agency_id=%s AND p.agency_id=%s
               ORDER BY dr.first_viewed_at""",
            (doc["property_id"], i, agency_id, agency_id),
        )
        return [dict(row) for row in c.fetchall()]


def document_storage_health(storage=None):
    from .document_storage import get_document_storage

    storage = storage or get_document_storage()
    return storage.healthcheck()


CATEGORY_LABELS = {
    "price": "Posizionamento economico",
    "state": "Stato e presentazione",
    "layout": "Distribuzione degli spazi",
    "location": "Posizione",
    "accessories": "Accessori e pertinenze",
    "general": "Osservazione generale",
}
SENTIMENT_LABELS = {
    "positive": "Positivo",
    "neutral": "Neutro",
    "negative": "Critico",
    "mixed": "Misto",
}


def _audit_with_cursor(c, action, account=None, prop=None, etype=None, eid=None, result="success", meta=None):
    c.execute(
        """INSERT INTO owner_audit_log(
               owner_account_id,property_id,action,entity_type,entity_id,result,metadata
           ) VALUES(%s,%s,%s,%s,%s,%s,%s)""",
        (account, prop, action, etype, str(eid) if eid is not None else None, result, Json(meta or {})),
    )


def _validated_public_summary(value):
    try:
        return validate_visit_feedback_summary(value)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc


def validate_visit_feedback_privacy(public_summary):
    issues = visit_feedback_privacy_issues(public_summary)
    return {"valid": not issues, "issues": issues}


def _visit_feedback_for_update(c, agency_id, i):
    """The visit-feedback row, inside the caller's agency.

    owner_visit_feedback_publications.owner_account_id is nullable, like the
    shared documents', so the tenant comes through the visit to its property.
    """
    c.execute(
        """SELECT vf.*,pv.property_id
           FROM owner_visit_feedback_publications vf
           JOIN property_visits pv ON pv.id=vf.property_visit_id
           JOIN properties p ON p.id=pv.property_id
           WHERE vf.id=%s AND p.agency_id=%s
           FOR UPDATE OF vf""",
        (i, agency_id),
    )
    return one(c)


def _public_visit_feedback(row):
    sentiment = row.get("sentiment")
    result = {
        "visit_feedback_publication_id": row["id"],
        "category_code": row["category"],
        "category_label": CATEGORY_LABELS[row["category"]],
        "public_summary": row["public_summary"],
        "version_number": row["version_number"],
        "published_at": row["published_at"],
        "is_current_version": True,
    }
    if sentiment is not None:
        result["sentiment"] = sentiment
        result["sentiment_label"] = SENTIMENT_LABELS[sentiment]
    return result


def create_visit_feedback_publication(agency_id, d):
    summary = _validated_public_summary(d["public_summary"])
    with core_cursor(commit=True) as (_, c):
        src = _property_for_visit(c, agency_id, d["property_visit_id"])
        _validate_target_account(c, agency_id, d.get("owner_account_id"), src["property_id"])
        c.execute(
            """INSERT INTO owner_visit_feedback_publications(
                   property_visit_id,owner_account_id,category,public_summary,sentiment,created_by
               ) VALUES(%s,%s,%s,%s,%s,%s) RETURNING *""",
            (
                d["property_visit_id"],
                d.get("owner_account_id"),
                d["category"],
                summary,
                d.get("sentiment"),
                d.get("created_by"),
            ),
        )
        result = one(c)
        _audit_with_cursor(
            c,
            "visit_feedback_created",
            d.get("owner_account_id"),
            src["property_id"],
            "owner_visit_feedback",
            result["id"],
        )
    result["property_id"] = src["property_id"]
    return result


def list_visit_feedback_publications(
    agency_id,
    property_visit_id=None,
    property_id=None,
    status=None,
    owner_account_id=None,
    category=None,
    limit=50,
    offset=0,
):
    clauses = ["p.agency_id=%s"]
    values = [agency_id]
    for expression, value in (
        ("vf.property_visit_id=%s", property_visit_id),
        ("pv.property_id=%s", property_id),
        ("vf.status=%s", status),
        ("vf.owner_account_id=%s", owner_account_id),
        ("vf.category=%s", category),
    ):
        if value is not None:
            clauses.append(expression)
            values.append(value)
    where = " WHERE " + " AND ".join(clauses)
    values.extend((limit, offset))
    with core_cursor() as (_, c):
        c.execute(
            """SELECT vf.*,pv.property_id
               FROM owner_visit_feedback_publications vf
               JOIN property_visits pv ON pv.id=vf.property_visit_id
               JOIN properties p ON p.id=pv.property_id"""
            + where
            + " ORDER BY vf.created_at DESC LIMIT %s OFFSET %s",
            values,
        )
        return [dict(row) for row in c.fetchall()]


def get_visit_feedback_publication(agency_id, i):
    with core_cursor() as (_, c):
        c.execute(
            """SELECT vf.*,pv.property_id
               FROM owner_visit_feedback_publications vf
               JOIN property_visits pv ON pv.id=vf.property_visit_id
               JOIN properties p ON p.id=pv.property_id
               WHERE vf.id=%s AND p.agency_id=%s""",
            (i, agency_id),
        )
        return one(c)


def update_visit_feedback_publication(agency_id, i, d):
    fields = []
    values = []
    if "public_summary" in d:
        d = dict(d)
        d["public_summary"] = _validated_public_summary(d["public_summary"])
    for key in ("category", "public_summary", "sentiment"):
        if key in d:
            fields.append(key + "=%s")
            values.append(d[key])
    with core_cursor(commit=True) as (_, c):
        old = _visit_feedback_for_update(c, agency_id, i)
        if old["status"] != "draft":
            raise ConflictError("Un feedback pubblicato o archiviato è immutabile")
        if not fields:
            return old
        values.append(i)
        c.execute(
            "UPDATE owner_visit_feedback_publications SET "
            + ",".join(fields)
            + ",updated_at=NOW() WHERE id=%s RETURNING *",
            values,
        )
        result = one(c)
        _audit_with_cursor(
            c,
            "visit_feedback_updated",
            old.get("owner_account_id"),
            old["property_id"],
            "owner_visit_feedback",
            i,
        )
    result["property_id"] = old["property_id"]
    return result


def publish_visit_feedback(agency_id, i):
    with core_cursor(commit=True) as (_, c):
        current = _visit_feedback_for_update(c, agency_id, i)
        if current["status"] != "draft":
            raise ConflictError("Solo draft pubblicabile")
        _validated_public_summary(current["public_summary"])
        _validate_target_account(c, agency_id, current.get("owner_account_id"), current["property_id"])

        previous_id = current.get("supersedes_feedback_publication_id")
        if previous_id is not None:
            c.execute(
                "SELECT * FROM owner_visit_feedback_publications WHERE id=%s FOR UPDATE",
                (previous_id,),
            )
            previous = one(c)
            if previous["status"] != "published":
                raise ConflictError("La versione precedente non è pubblicata")
            if previous.get("superseded_by_feedback_publication_id") not in (None, i):
                raise ConflictError("La versione precedente è già stata sostituita")
            if (
                previous["property_visit_id"] != current["property_visit_id"]
                or previous.get("owner_account_id") != current.get("owner_account_id")
            ):
                raise ConflictError("Catena versioni non coerente")

        c.execute(
            """UPDATE owner_visit_feedback_publications
               SET status='published',published_at=NOW(),updated_at=NOW()
               WHERE id=%s RETURNING *""",
            (i,),
        )
        result = one(c)
        if previous_id is not None:
            c.execute(
                """UPDATE owner_visit_feedback_publications
                   SET superseded_by_feedback_publication_id=%s,updated_at=NOW()
                   WHERE id=%s""",
                (i, previous_id),
            )
        _emit_notification_event(
            c,
            property_id=current["property_id"],
            notification_type="visit_feedback_published",
            preference_column="visit_feedback_enabled",
            title="Nuovo feedback visita",
            body=current["public_summary"],
            target_type="owner_visit_feedback",
            target_id=i,
            owner_account_id=current.get("owner_account_id"),
        )
        _audit_with_cursor(
            c,
            "visit_feedback_published",
            current.get("owner_account_id"),
            current["property_id"],
            "owner_visit_feedback",
            i,
            meta={"supersedes": previous_id} if previous_id is not None else None,
        )
    result["property_id"] = current["property_id"]
    return result


def archive_visit_feedback(agency_id, i):
    with core_cursor(commit=True) as (_, c):
        old = _visit_feedback_for_update(c, agency_id, i)
        if old["status"] != "published":
            raise ConflictError("Solo published archiviabile")
        c.execute(
            """UPDATE owner_visit_feedback_publications
               SET status='archived',archived_at=NOW(),updated_at=NOW()
               WHERE id=%s RETURNING *""",
            (i,),
        )
        result = one(c)
        _audit_with_cursor(
            c,
            "visit_feedback_archived",
            old.get("owner_account_id"),
            old["property_id"],
            "owner_visit_feedback",
            i,
        )
    result["property_id"] = old["property_id"]
    return result


def supersede_visit_feedback(agency_id, i, d):
    summary = _validated_public_summary(d["public_summary"])
    with core_cursor(commit=True) as (_, c):
        old = _visit_feedback_for_update(c, agency_id, i)
        if old["status"] != "published":
            raise ConflictError("Solo published sostituibile")
        if old.get("superseded_by_feedback_publication_id") is not None:
            raise ConflictError("Solo la versione corrente può essere sostituita")
        _validate_target_account(c, agency_id, old.get("owner_account_id"), old["property_id"])
        c.execute(
            """SELECT 1 FROM owner_visit_feedback_publications
               WHERE supersedes_feedback_publication_id=%s AND status IN ('draft','published')
               LIMIT 1""",
            (i,),
        )
        if c.fetchone():
            raise ConflictError("Esiste già una versione successiva attiva")
        c.execute(
            """SELECT COALESCE(MAX(version_number),0)+1 AS next_version
               FROM owner_visit_feedback_publications
               WHERE property_visit_id=%s
                 AND owner_account_id IS NOT DISTINCT FROM %s""",
            (old["property_visit_id"], old.get("owner_account_id")),
        )
        next_version = int(c.fetchone()["next_version"])
        c.execute(
            """INSERT INTO owner_visit_feedback_publications(
                   property_visit_id,owner_account_id,category,public_summary,sentiment,
                   version_number,supersedes_feedback_publication_id,created_by
               ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
            (
                old["property_visit_id"],
                old.get("owner_account_id"),
                d["category"],
                summary,
                d.get("sentiment"),
                next_version,
                i,
                d.get("created_by"),
            ),
        )
        result = one(c)
        _audit_with_cursor(
            c,
            "visit_feedback_version_created",
            old.get("owner_account_id"),
            old["property_id"],
            "owner_visit_feedback",
            result["id"],
            meta={"previous": i, "version_number": next_version},
        )
    result["property_id"] = old["property_id"]
    return result


def portal_visit_feedback(a, p, limit=50, offset=0):
    require_property(a, p)
    with core_cursor() as (_, c):
        c.execute(
            """SELECT vf.id,vf.category,vf.public_summary,vf.sentiment,
                      vf.version_number,vf.published_at
               FROM owner_visit_feedback_publications vf
               JOIN property_visits pv ON pv.id=vf.property_visit_id
               WHERE pv.property_id=%s
                 AND vf.status='published'
                 AND vf.superseded_by_feedback_publication_id IS NULL
                 AND (vf.owner_account_id IS NULL OR vf.owner_account_id=%s)
               ORDER BY vf.published_at DESC
               LIMIT %s OFFSET %s""",
            (p, a, limit, offset),
        )
        return [_public_visit_feedback(dict(row)) for row in c.fetchall()]


def portal_visit_feedback_detail(a, i):
    with core_cursor() as (_, c):
        c.execute(
            """SELECT vf.id,vf.category,vf.public_summary,vf.sentiment,
                      vf.version_number,vf.published_at
               FROM owner_visit_feedback_publications vf
               JOIN property_visits pv ON pv.id=vf.property_visit_id
               JOIN owner_property_access x ON x.property_id=pv.property_id"""
            + _COHERENT_GRANT
            + """
               WHERE vf.id=%s
                 AND vf.status='published'
                 AND vf.superseded_by_feedback_publication_id IS NULL
                 AND (vf.owner_account_id IS NULL OR vf.owner_account_id=%s)"""
            + _GRANT_ROOTS_AGREE
            + """
                 AND x.owner_account_id=%s
                 AND x.access_status='active'
                 AND x.revoked_at IS NULL
                 AND (x.valid_until IS NULL OR x.valid_until>NOW())""",
            (i, a, a),
        )
        return _public_visit_feedback(one(c))


def audit_visit_feedback_access_denied(account, property_id=None, publication_id=None, scope="portal"):
    try:
        audit(
            "visit_feedback_access_denied",
            account=account,
            prop=property_id,
            etype="owner_visit_feedback",
            eid=publication_id,
            result="denied",
            meta={"scope": scope},
        )
    except Exception:
        # The neutral 404 must never be replaced by an audit infrastructure error.
        pass


def update_feedback_status(agency_id,i,d):
 with core_cursor(commit=True) as(_,c):
  c.execute(
      """SELECT f.* FROM owner_feedback f
           JOIN owner_accounts oa ON oa.id=f.owner_account_id
           JOIN contacts ct ON ct.id=oa.contact_id
           JOIN properties p ON p.id=f.property_id
          WHERE f.id=%s AND ct.agency_id=%s AND p.agency_id=%s
            FOR UPDATE OF f FOR SHARE OF oa,ct,p""",
      (i,agency_id,agency_id),
  )
  old=one(c)
  handled=d['status'] in ('handled','closed')
  first_handling=handled and old.get('handled_at') is None
  c.execute("""UPDATE owner_feedback SET status=%s,handled_at=CASE WHEN %s THEN COALESCE(handled_at,NOW()) ELSE handled_at END,
               handled_by=COALESCE(%s,handled_by),public_response=COALESCE(%s,public_response),updated_at=NOW() WHERE id=%s RETURNING *""",(d['status'],handled,d.get('handled_by'),d.get('public_response'),i));r=one(c)
  if first_handling:
   _emit_notification_event(
       c,
       property_id=old['property_id'],
       notification_type='request_handled',
       preference_column='request_update_enabled',
       title='Aggiornamento sulla tua richiesta',
       body=r.get('public_response') or 'La tua richiesta è stata gestita.',
       target_type='owner_feedback',
       target_id=i,
       owner_account_id=old['owner_account_id'],
   )
  _audit_with_cursor(c,'feedback_status_updated',old['owner_account_id'],old['property_id'],'owner_feedback',i,meta={'status':d['status']})
 return r

# OWNER 0.2 P5 - in-app notifications ---------------------------------------
_NOTIFICATION_TYPES = {
    "publication_published",
    "visit_feedback_published",
    "shared_document_published",
    "request_handled",
}
_NOTIFICATION_TARGET_TYPES = {
    "owner_publication",
    "owner_visit_feedback",
    "owner_shared_document",
    "owner_feedback",
}
_NOTIFICATION_PREFERENCE_COLUMNS = {
    "publication_enabled",
    "visit_feedback_enabled",
    "document_enabled",
    "request_update_enabled",
}
_NOTIFICATION_RETENTION_DAYS = 365


def _emit_notification_event(
    c,
    *,
    property_id,
    notification_type,
    preference_column,
    title,
    body,
    target_type,
    target_id,
    owner_account_id=None,
):
    """Materialize one in-app notification per eligible owner, race-safe.

    This helper intentionally uses only SQL statements that do not require a
    result fetch. It is designed to run inside the same transaction as the
    source P2/P3/P4 event. The UNIQUE idempotency key is the final race guard.
    """
    if notification_type not in _NOTIFICATION_TYPES:
        raise ValidationError("Tipo notifica non ammesso")
    if target_type not in _NOTIFICATION_TARGET_TYPES:
        raise ValidationError("Target notifica non ammesso")
    if preference_column not in _NOTIFICATION_PREFERENCE_COLUMNS:
        raise ValidationError("Preferenza notifica non ammessa")
    title = str(title or "").strip()
    body = str(body or "").strip()
    if not title or len(title) > 200 or not body or len(body) > 5000:
        raise ValidationError("Snapshot notifica non valido")

    eligible_sql = f"""
        SELECT x.owner_account_id,
               COALESCE(np.in_app_enabled, TRUE) AS in_app_enabled,
               COALESCE(np.{preference_column}, TRUE) AS category_enabled
        FROM owner_property_access x
        JOIN owner_accounts oa ON oa.id=x.owner_account_id
        JOIN contacts ct ON ct.id=oa.contact_id
        JOIN properties p ON p.id=x.property_id
        LEFT JOIN owner_notification_preferences np
          ON np.owner_account_id=x.owner_account_id
        WHERE x.property_id=%s
          -- P26-6C: the grant's two roots must agree before anyone is told
          -- anything. A grant written before create_access checked can link an
          -- account of one agency to a property of another; without this join
          -- publishing on that property would notify the other agency's owner.
          -- The agency is not a parameter here: it is the property's own, so
          -- no caller can widen it.
          AND ct.agency_id=p.agency_id
          AND (%s::bigint IS NULL OR x.owner_account_id=%s)
          AND x.access_status='active'
          AND x.revoked_at IS NULL
          AND (x.valid_until IS NULL OR x.valid_until>NOW())
          AND oa.status<>'disabled'
    """

    c.execute(
        f"""WITH eligible AS ({eligible_sql}),
        inserted AS (
            INSERT INTO owner_notifications(
                owner_account_id,property_id,notification_type,title,body,
                target_type,target_id,idempotency_key,expires_at
            )
            SELECT e.owner_account_id,%s,%s,%s,%s,%s,%s,
                   CONCAT('owner-p5:v1:',%s,':',%s,':',%s,':',e.owner_account_id),
                   NOW() + (%s * INTERVAL '1 day')
            FROM eligible e
            WHERE e.in_app_enabled AND e.category_enabled
            ON CONFLICT (idempotency_key) DO NOTHING
            RETURNING id,owner_account_id,property_id
        )
        INSERT INTO owner_audit_log(
            owner_account_id,property_id,action,entity_type,entity_id,result,metadata
        )
        SELECT i.owner_account_id,i.property_id,'notification_created',
               'owner_notification',i.id::text,'success',
               jsonb_build_object('notification_type',%s,'target_type',%s,'target_id',%s)
        FROM inserted i""",
        (
            property_id, owner_account_id, owner_account_id,
            property_id, notification_type, title, body, target_type, target_id,
            notification_type, target_type, target_id, _NOTIFICATION_RETENTION_DAYS,
            notification_type, target_type, target_id,
        ),
    )

    c.execute(
        f"""WITH eligible AS ({eligible_sql})
        INSERT INTO owner_audit_log(
            owner_account_id,property_id,action,entity_type,entity_id,result,metadata
        )
        SELECT e.owner_account_id,%s,'notification_suppressed',%s,%s::text,'success',
               jsonb_build_object('notification_type',%s,'reason_code','preference_disabled')
        FROM eligible e
        WHERE NOT (e.in_app_enabled AND e.category_enabled)""",
        (
            property_id, owner_account_id, owner_account_id,
            property_id, target_type, target_id, notification_type,
        ),
    )


def _public_notification(row):
    """Explicit P0/P5 whitelist. Never expose account/property/idempotency fields."""
    return {
        "id": row["id"],
        "type": row["notification_type"],
        "title": row["title"],
        "body": row["body"],
        "created_at": row["created_at"],
        "read_at": row.get("read_at"),
        "target_type": row["target_type"],
        "target_id": row["target_id"],
    }


def portal_notifications(a, limit=50, offset=0, unread_only=False):
    filters = [
        "n.owner_account_id=%s",
        "n.expires_at>NOW()",
        # The grant's two roots must agree. This one goes in the filter list
        # rather than being appended to the SQL, because that is where this
        # query builds its WHERE.
        "ct_g.agency_id=p_g.agency_id",
        "x.access_status='active'",
        "x.revoked_at IS NULL",
        "(x.valid_until IS NULL OR x.valid_until>NOW())",
    ]
    values = [a]
    if unread_only:
        filters.append("n.read_at IS NULL")
    values.extend((limit, offset))
    with core_cursor() as (_, c):
        c.execute(
            """SELECT n.id,n.notification_type,n.title,n.body,n.created_at,n.read_at,
                      n.target_type,n.target_id
               FROM owner_notifications n
               JOIN owner_property_access x
                 ON x.owner_account_id=n.owner_account_id AND x.property_id=n.property_id"""
            + _COHERENT_GRANT
            + """
               WHERE """
            + " AND ".join(filters)
            + " ORDER BY n.created_at DESC,n.id DESC LIMIT %s OFFSET %s",
            values,
        )
        return [_public_notification(dict(row)) for row in c.fetchall()]


def mark_notification_read(a, i):
    with core_cursor(commit=True) as (_, c):
        c.execute(
            """UPDATE owner_notifications n
               SET read_at=COALESCE(n.read_at,NOW())
               FROM owner_property_access x
               JOIN owner_accounts oa_g ON oa_g.id=x.owner_account_id
               JOIN contacts ct_g ON ct_g.id=oa_g.contact_id
               JOIN properties p_g ON p_g.id=x.property_id
               WHERE n.id=%s AND n.owner_account_id=%s
                 AND ct_g.agency_id=p_g.agency_id
                 AND x.owner_account_id=n.owner_account_id
                 AND x.property_id=n.property_id
                 AND n.expires_at>NOW()
                 AND x.access_status='active'
                 AND x.revoked_at IS NULL
                 AND (x.valid_until IS NULL OR x.valid_until>NOW())
               RETURNING n.id,n.notification_type,n.title,n.body,n.created_at,n.read_at,
                         n.target_type,n.target_id,n.property_id""",
            (i, a),
        )
        row = one(c)
        _audit_with_cursor(
            c,
            "notification_read",
            a,
            row["property_id"],
            "owner_notification",
            row["id"],
            meta={"notification_type": row["notification_type"]},
        )
        return _public_notification(row)


def get_notification_preferences(a):
    with core_cursor() as (_, c):
        c.execute(
            """SELECT in_app_enabled,publication_enabled,visit_feedback_enabled,
                      document_enabled,request_update_enabled
               FROM owner_notification_preferences WHERE owner_account_id=%s""",
            (a,),
        )
        row = c.fetchone()
    if row is None:
        return {
            "in_app_enabled": True,
            "publication_enabled": True,
            "visit_feedback_enabled": True,
            "document_enabled": True,
            "request_update_enabled": True,
        }
    return dict(row)


def update_notification_preferences(a, d):
    fields = (
        "in_app_enabled",
        "publication_enabled",
        "visit_feedback_enabled",
        "document_enabled",
        "request_update_enabled",
    )
    values = [bool(d[name]) for name in fields]
    with core_cursor(commit=True) as (_, c):
        c.execute("SELECT 1 FROM owner_accounts WHERE id=%s AND status<>'disabled'", (a,))
        if not c.fetchone():
            raise NotFoundError(NF)
        c.execute(
            """INSERT INTO owner_notification_preferences(
                   owner_account_id,in_app_enabled,publication_enabled,visit_feedback_enabled,
                   document_enabled,request_update_enabled
               ) VALUES(%s,%s,%s,%s,%s,%s)
               ON CONFLICT(owner_account_id) DO UPDATE SET
                   in_app_enabled=EXCLUDED.in_app_enabled,
                   publication_enabled=EXCLUDED.publication_enabled,
                   visit_feedback_enabled=EXCLUDED.visit_feedback_enabled,
                   document_enabled=EXCLUDED.document_enabled,
                   request_update_enabled=EXCLUDED.request_update_enabled,
                   updated_at=NOW()
               RETURNING in_app_enabled,publication_enabled,visit_feedback_enabled,
                         document_enabled,request_update_enabled""",
            (a, *values),
        )
        row = one(c)
        _audit_with_cursor(
            c,
            "notification_preferences_updated",
            a,
            etype="owner_notification_preferences",
            eid=a,
            meta={"scope": "in_app"},
        )
        return row


def audit_notification_access_denied(a, notification_id, scope="read"):
    try:
        with core_cursor() as (_, c):
            c.execute("SELECT property_id FROM owner_notifications WHERE id=%s", (notification_id,))
            row = c.fetchone()
        audit(
            "notification_access_denied",
            account=a,
            prop=row["property_id"] if row else None,
            etype="owner_notification",
            eid=notification_id,
            result="denied",
            meta={"scope": scope, "reason_code": "not_found_or_not_authorized"},
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# LMC-1A - il provisioning PRE-INCARICO: contatto -> account -> accesso alla stima.
#
# Una transazione sola, sul modello di `create_access`: la tenancy si decide
# DENTRO la stessa transazione della scrittura, con FOR SHARE sui genitori, e
# non arriva mai da un parametro del chiamante se non come agenzia da
# CONFRONTARE - il servizio la riceve dal contesto di sistema della stima gia'
# scritta (`stime.agency_id`, fonte di verita' da P27-6) e questa funzione la
# verifica su ENTRAMBE le radici prima di toccare una riga.
#
# Idempotente per costruzione, non per controllo: `owner_accounts.contact_id`
# e' UNIQUE (009) e `owner_stima_access (owner_account_id, stima_id)` e'
# UNIQUE (066), quindi le due INSERT sono ON CONFLICT DO NOTHING e la riga
# esistente viene riletta. L'audit si scrive SOLO per cio' che questa
# esecuzione ha creato, sullo stesso cursore: un retry non produce ne' righe
# ne' audit.
#
# L'advisory lock sul contatto serializza due stime dello stesso contatto che
# arrivano insieme: senza, entrambe potrebbero vedere "nessun account" e una
# delle due perderebbe la INSERT sull'UNIQUE dopo aver deciso `account_created`.
# ---------------------------------------------------------------------------

def provision_stima_access(agency_id, *, contact_id, stima_id, granted_by):
 """Lega il contatto alla sua stima attraverso un account owner, in una transazione.

 Ritorna un dict con `status` in {'provisioned', 'already_provisioned',
 'account_disabled'}, `owner_account_id`, `access_id` (None se nessun grant),
 `account_created`, `access_created`.

 Solleva NotFoundError (il 404 neutro di OWNER) se contatto o stima non
 esistono, non hanno agenzia, o non stanno entrambi in `agency_id`. Nessuna
 riga viene scritta in quel caso: la transazione si chiude con il rollback.
 """
 with core_cursor(commit=True) as(_,c):
  c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0)) AS locked",(f"owner:provision:contact:{contact_id}",))
  c.execute("SELECT agency_id FROM contacts WHERE id=%s FOR SHARE",(contact_id,));ct=c.fetchone()
  c.execute("SELECT agency_id FROM stime WHERE id=%s FOR SHARE",(stima_id,));st=c.fetchone()
  if not ct or not st:raise NotFoundError(NF)
  if ct['agency_id'] is None or st['agency_id'] is None:raise NotFoundError(NF)
  if ct['agency_id']!=st['agency_id']:raise NotFoundError(NF)
  if ct['agency_id']!=agency_id:raise NotFoundError(NF)
  c.execute("INSERT INTO owner_accounts(contact_id,status,preferred_language) VALUES(%s,'invited','it') ON CONFLICT (contact_id) DO NOTHING RETURNING *",(contact_id,));account=c.fetchone()
  account_created=account is not None
  if account is None:
   c.execute("SELECT * FROM owner_accounts WHERE contact_id=%s FOR SHARE",(contact_id,));account=c.fetchone()
  if account is None:raise NotFoundError(NF)
  account=dict(account)
  if account_created:
   _audit_with_cursor(c,'account_created',account['id'],etype='owner_account',eid=account['id'],meta={'source':granted_by,'stima_id':stima_id})
  if account['status']=='disabled':
   return {'status':'account_disabled','owner_account_id':account['id'],'access_id':None,'account_created':account_created,'access_created':False}
  c.execute("INSERT INTO owner_stima_access(owner_account_id,stima_id,access_role,access_status,is_primary,valid_from,granted_by) VALUES(%s,%s,'owner','active',TRUE,NOW(),%s) ON CONFLICT (owner_account_id,stima_id) DO NOTHING RETURNING *",(account['id'],stima_id,granted_by));access=c.fetchone()
  access_created=access is not None
  if access is None:
   c.execute("SELECT * FROM owner_stima_access WHERE owner_account_id=%s AND stima_id=%s",(account['id'],stima_id));access=c.fetchone()
  if access is None:raise NotFoundError(NF)
  access=dict(access)
  if access_created:
   _audit_with_cursor(c,'stima_access_granted',account['id'],etype='owner_stima_access',eid=access['id'],meta={'stima_id':stima_id,'granted_by':granted_by,'access_role':access['access_role']})
  return {'status':'provisioned' if access_created else 'already_provisioned','owner_account_id':account['id'],'access_id':access['id'],'account_created':account_created,'access_created':access_created}


# ---------------------------------------------------------------------------
# LMC-1B - il lookup e il token del magic link.
#
# `audit_with_cursor` e' l'alias pubblico di `_audit_with_cursor`: il servizio
# di login compone la propria transazione e ha bisogno di scrivere l'audit su
# QUEL cursore, non su una connessione nuova.
#
# IL LOOKUP NON PRENDE UN'AGENZIA. Ne restituisce una per riga: quella del
# contatto che porta l'indirizzo. E' l'unico punto del flusso in cui il tenant
# nasce, e nasce da un dato del server - `contacts.email_normalized` - mai da
# ciò che il client ha scritto oltre all'indirizzo stesso.
#
# ELEGGIBILITA': un account non disabilitato il cui contatto ha quell'indirizzo
# e che possiede ALMENO UN accesso valido, di uno dei due mondi:
#
#     PRE-INCARICO   owner_stima_access  -> stime      (LMC-1A, migration 066)
#     POST-INCARICO  owner_property_access -> properties (Owner Portal legacy)
#
# Entrambi con lo stesso predicato di validita' che il portale gia' applica
# (attivo, non revocato, non scaduto) e con le DUE RADICI CHE CONCORDANO: il
# contatto dell'account e la stima (o l'immobile) devono nominare la stessa
# agenzia. E' la stessa regola di `_COHERENT_GRANT`/`_GRANT_ROOTS_AGREE` qui
# sopra, estesa al grant pre-incarico: un grant scritto fra due agenzie non
# rende nessuno eleggibile, e non viene riparato - viene ignorato.
# ---------------------------------------------------------------------------

audit_with_cursor = _audit_with_cursor

_GRANT_VALIDO = ("x.access_status='active' AND x.revoked_at IS NULL "
                 "AND (x.valid_until IS NULL OR x.valid_until>NOW())")

_LOGIN_CANDIDATES = f"""
SELECT oa.id AS owner_account_id, ct.id AS contact_id, ct.agency_id AS agency_id,
       ct.email AS email, ct.display_name AS display_name
  FROM owner_accounts oa
  JOIN contacts ct ON ct.id = oa.contact_id
 WHERE ct.email_normalized = %s
   AND oa.status <> 'disabled'
   AND ct.agency_id IS NOT NULL
   AND (
        EXISTS (SELECT 1 FROM owner_stima_access x
                  JOIN stime s ON s.id = x.stima_id
                 WHERE x.owner_account_id = oa.id
                   AND s.agency_id = ct.agency_id
                   AND {_GRANT_VALIDO})
     OR EXISTS (SELECT 1 FROM owner_property_access x
                  JOIN properties p ON p.id = x.property_id
                 WHERE x.owner_account_id = oa.id
                   AND p.agency_id = ct.agency_id
                   AND {_GRANT_VALIDO})
   )
 ORDER BY oa.id
"""


def find_login_candidates(email_normalized):
 """Gli account che possono ricevere un magic link per quell'indirizzo.

 Zero, uno o piu' di uno: la stessa persona puo' essere contatto di piu'
 agenzie, e ognuna e' un tenant a se'. Nessuna riga significa "nessuno", e il
 chiamante non deve distinguerlo da nient'altro verso l'esterno.
 """
 if not email_normalized:return[]
 with core_cursor() as(_,c):c.execute(_LOGIN_CANDIDATES,(email_normalized,));return[dict(x) for x in c.fetchall()]


def issue_login_token_with_cursor(c,*,owner_account_id,agency_id,minutes,created_by,
                                  max_recent=3,window_minutes=15):
 """Il token di login, sul cursore del chiamante. Non committa.

 Ritorna `(riga, raw)`, oppure `(None, None)` se l'account ha gia' raggiunto
 il tetto di token di login CREATI nella finestra. Il segreto in chiaro
 esiste solo qui e nel valore di ritorno: in tabella va lo sha256, come per
 ogni altro token owner.

 SI CONTANO LE RICHIESTE, NON I LINK ANCORA APERTI. Il conteggio non guarda
 `used_at` ne' `revoked_at`: una richiesta gia' fatta pesa per tutti e quindici
 i minuti, che l'abbiano usata o no. Escludere i token consumati avrebbe
 trasformato il limite in una porta girevole - apri il link, il posto si
 libera, ne chiedi un altro - e avrebbe reso il tetto inefficace proprio per
 chi ha accesso alla casella e sta ripetendo la richiesta. Solo il tempo
 libera un posto.

 Perche' sul cursore del chiamante: il token e il messaggio che lo porta
 devono vivere o cadere insieme. Un token committato e una email mai accodata
 e' una credenziale che nessuno ricevera' mai ma che occupa il rate limit di
 chi ci riprova.

 L'advisory lock sull'account serializza due richieste simultanee per lo
 stesso proprietario: senza, entrambe conterebbero due token e ne scriverebbero
 un terzo e un quarto.

 Il tenant si verifica qui come ovunque in OWNER: `_require_account_in_agency`
 risale l'account al suo contatto e rifiuta con il 404 neutro se non e' di
 `agency_id`.
 """
 c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0)) AS locked",(f"owner:login_token:account:{owner_account_id}",))
 _require_account_in_agency(c,agency_id,owner_account_id)
 c.execute("SELECT count(*) AS n FROM owner_access_tokens WHERE owner_account_id=%s AND token_type='login' AND created_at >= NOW() - make_interval(mins => %s)",(owner_account_id,window_minutes))
 if c.fetchone()['n']>=max_recent:return None,None
 raw=generate_secret()
 c.execute("INSERT INTO owner_access_tokens(owner_account_id,token_hash,token_type,expires_at,created_by) VALUES(%s,%s,'login',NOW()+make_interval(mins => %s),%s) RETURNING *",(owner_account_id,hash_secret(raw),minutes,created_by))
 return one(c),raw


# ---------------------------------------------------------------------------
# LMC-2 - le letture di "La Mia Casa". SOLO SELECT.
#
# IL PREDICATO DEL GRANT, IN UN POSTO SOLO. `_COHERENT_HOME_GRANT` e' per la
# stima cio' che `_COHERENT_GRANT`/`_GRANT_ROOTS_AGREE` sono per l'immobile: le
# due radici del grant - il contatto dell'account e la stima - devono nominare
# la STESSA agenzia, e il grant deve essere attivo, non revocato e non scaduto.
# Lista e dettaglio compongono la stessa stringa, quindi non possono avere due
# idee diverse di chi puo' vedere che cosa.
#
# NESSUNA AGENZIA COME PARAMETRO. L'identita' qui e' l'`owner_account_id` della
# sessione, come in tutto il portale; il tenant lo porta il grant. Le funzioni
# che leggono PROPERTY WATCH ricevono invece un'agenzia, ma e' quella che
# `home_agency_for_account` ha ricavato dal contatto - server-side, mai dal
# client.
#
# COSA NON ESCE DA QUI. `stime` contiene nome, cognome, email e telefono di chi
# ha chiesto la stima, piu' campi gestionali (`lead_status`, `note_internal`,
# `prezzo_mq_base`, `token`). Le SELECT sotto NOMINANO le colonne una per una e
# non usano `*`: l'elenco e' la lista di cio' che il proprietario puo' vedere,
# e aggiungere una colonna e' un atto visibile in diff.
# ---------------------------------------------------------------------------

#: Le colonne di `stime` che il portale proprietario puo' leggere. Nessun dato
#: personale, nessun campo gestionale, nessun prezzo di riferimento interno.
HOME_STIMA_COLUMNS = (
    "id", "comune", "microzona", "via", "civico", "tipologia", "mq", "piano",
    "locali", "bagni", "pertinenze", "ascensore", "anno", "stato",
    "vistamareyn", "distanzamare", "altrodescrizione", "data",
)

_HOME_GRANT_JOIN = """
      FROM owner_stima_access x
      JOIN owner_accounts oa ON oa.id = x.owner_account_id
      JOIN contacts ct ON ct.id = oa.contact_id
      JOIN stime s ON s.id = x.stima_id
"""

_HOME_GRANT_WHERE = """
     WHERE x.owner_account_id = %s
       AND ct.agency_id = s.agency_id
       AND x.access_status = 'active'
       AND x.revoked_at IS NULL
       AND (x.valid_until IS NULL OR x.valid_until > NOW())
"""


def _home_columns(alias="s"):
 return ", ".join(f"{alias}.{c}" for c in HOME_STIMA_COLUMNS)


def home_agency_for_account(owner_account_id):
 """L'agenzia dell'account, dalla catena account -> contatto -> agenzia.

 E' l'unico modo in cui un tenant entra nel read model di LMC-2, ed e' la
 stessa catena che P26-6C ha certificato per l'Owner Admin. `None` quando
 l'account non esiste o il contatto non ha agenzia: il chiamante lo tratta
 come "nessuna casa", mai come "tutte".
 """
 with core_cursor() as(_,c):
  c.execute("SELECT ct.agency_id FROM owner_accounts oa JOIN contacts ct ON ct.id=oa.contact_id WHERE oa.id=%s",(owner_account_id,));r=c.fetchone()
 return r['agency_id'] if r else None


def list_home_grants(owner_account_id):
 """Le stime accessibili all'account, con le colonne pubbliche. Zero o piu'."""
 with core_cursor() as(_,c):
  c.execute(f"SELECT {_home_columns()}, s.agency_id AS agency_id{_HOME_GRANT_JOIN}{_HOME_GRANT_WHERE} ORDER BY s.id",(owner_account_id,))
  return[dict(x) for x in c.fetchall()]


def get_home_grant(owner_account_id,stima_id):
 """Una stima, se quell'account puo' vederla. Altrimenti il 404 neutro.

 Assente, di un altro proprietario, di un'altra agenzia, con il grant revocato
 o scaduto: una risposta sola, di proposito. Distinguerle direbbe a chi prova
 un id quali stime esistono.
 """
 with core_cursor() as(_,c):
  c.execute(f"SELECT {_home_columns()}, s.agency_id AS agency_id{_HOME_GRANT_JOIN}{_HOME_GRANT_WHERE} AND x.stima_id = %s",(owner_account_id,stima_id))
  return one(c)


def home_completed_valuation(agency_id,stima_id):
 """Il payload dell'evento `stima_completata` di QUELLA stima, in QUELL'agenzia.

 E' il ripiego del valore iniziale quando il watch non c'e'. Il predicato
 porta l'agenzia perche' `stima_id` da solo non e' un tenant: un evento
 scritto per un'altra agenzia non puo' diventare il valore di questa casa.
 Il piu' recente, se per qualsiasi ragione ce ne fosse piu' di uno.
 """
 with core_cursor() as(_,c):
  c.execute("SELECT payload FROM seller_timeline_events WHERE stima_id=%s AND agency_id=%s AND event_type='stima_completata' ORDER BY occurred_at DESC, id DESC LIMIT 1",(stima_id,agency_id));r=c.fetchone()
 if not r:return None
 payload=r['payload']
 return dict(payload) if isinstance(payload,dict) else None


def contact_homes(agency_id,contact_id):
 """Le case PRE-INCARICO di un contatto, viste dal CRM.

 LMC-8. E' la stessa definizione che vede il proprietario nel suo portale,
 letta dall'altro lato: una casa e' "sua" quando il suo account owner ha un
 grant valido su quella stima. Non si passa da `lead_stime`, che lega i
 lead alle stime per ragioni commerciali e puo' collegarne di altri; si
 passa dal grant, che e' l'unico posto dove e' scritto chi puo' vederla.
 Cosi' la scheda contatto e il portale non possono raccontare due cose
 diverse sulla stessa casa.

 Il tenant e' nel predicato tre volte e non per abbondanza: l'agenzia del
 contatto, quella della stima e la coerenza fra le due. Un contatto puo'
 esistere in piu' agenzie con lo stesso indirizzo email - il CRM di A non
 deve vedere la casa che quella persona ha in B.

 `data` esce perche' serve all'ordinamento di ripiego quando non c'e'
 nessuna attivita' del proprietario.
 """
 with core_cursor() as(_,c):
  c.execute(f"""
    SELECT {_home_columns()}
      FROM owner_stima_access x
      JOIN owner_accounts oa ON oa.id = x.owner_account_id
      JOIN contacts ct ON ct.id = oa.contact_id
      JOIN stime s ON s.id = x.stima_id
     WHERE ct.id = %s
       AND ct.agency_id = %s
       AND s.agency_id = %s
       AND ct.agency_id = s.agency_id
       AND x.access_status = 'active'
       AND x.revoked_at IS NULL
       AND (x.valid_until IS NULL OR x.valid_until > NOW())
     ORDER BY s.id
  """,(contact_id,agency_id,agency_id))
  return[dict(x) for x in c.fetchall()]


def home_tracking_context(owner_account_id,stima_id):
 """Chi sta guardando, di chi e' la casa, e quale lead SELL la riguarda.

 LMC-7. Una query sola, e il grant e' il primo predicato: se l'accesso non
 e' valido non esce niente, quindi non c'e' nessun percorso in cui si
 registri un evento per una stima che quell'owner non puo' vedere.

 IL LEAD NON E' GARANTITO, E NON SI INDOVINA. `lead_stime` ha UNIQUE su
 (lead_id, stima_id), non su stima_id: la stessa stima puo' essere legata a
 piu' lead - il CRM puo' averne aperto un secondo, o averla collegata a una
 trattativa diversa. Quindi non si prende "il" lead, si prende quello che
 soddisfa tutte queste condizioni insieme:

   - pipeline 'sell', perche' un lead BUY non e' il venditore di questa casa;
   - stessa agenzia della stima, perche' `stima_id` da solo non e' un tenant;
   - stesso contatto dell'account proprietario, perche' attribuire il
     comportamento di questa persona al lead di un'altra e' peggio che non
     attribuirlo a nessuno;
   - fra i rimasti, prima il legame 'origin' (quello che il funnel scrive
     quando la stima nasce), poi il piu' vecchio per id.

 Se nessun lead supera i filtri il risultato e' `None`, e l'evento viene
 comunque registrato con `contact_id` e `stima_id`: Seller Intelligence
 chiede almeno un riferimento, non tutti. Un lead sbagliato sporcherebbe la
 timeline di vendita di qualcun altro; un lead assente si puo' ricostruire.
 """
 with core_cursor() as(_,c):
  c.execute(f"""
    SELECT ct.agency_id AS agency_id,
           ct.id        AS contact_id,
           (SELECT ls.lead_id
              FROM lead_stime ls
              JOIN leads l ON l.id = ls.lead_id
             WHERE ls.stima_id = x.stima_id
               AND l.agency_id = s.agency_id
               AND l.contact_id = ct.id
               AND l.pipeline = 'sell'
             ORDER BY (ls.relation_type = 'origin') DESC, ls.id ASC
             LIMIT 1) AS lead_id
      {_HOME_GRANT_JOIN}
      {_HOME_GRANT_WHERE}
       AND x.stima_id = %s
  """,(owner_account_id,stima_id));r=c.fetchone()
 return dict(r) if r else None


def home_buyer_pressure(agency_id,stima_id):
 """L'ultima rilevazione Buyer Pressure di QUESTA casa, gia' sfoltita.

 LMC-4. Una sola riga, e di quella riga solo i campi che servono: la SELECT
 estrae dal payload le chiavi una per una invece di prendere `payload` e
 scartare dopo. La differenza non e' stilistica - `average_budget` e' la
 media dei budget di chi risulta compatibile, e con un solo compatibile e'
 il budget di quella persona. Qui non viene selezionato, quindi non esce da
 PostgreSQL, quindi non c'e' nessun punto piu' a valle in cui possa
 sfuggire. Le altre chiavi servono davvero: `evaluated_buyers` e i due
 punteggi entrano nella derivazione della fascia (e non nella risposta).

 L'agenzia e' nel predicato insieme alla stima: `stima_id` da solo non e' un
 tenant, e la rilevazione del watch di un'altra agenzia non puo' diventare
 la domanda di questa casa. `None` quando non c'e' watch, o non c'e'
 rilevazione: il chiamante lo traduce in "non disponibile".

 Le due forme che il dominio scrive contano entrambe: `buyer_pressure_snapshot`
 la prima volta, `buyer_pressure_changed` a ogni variazione.
 """
 with core_cursor() as(_,c):
  c.execute("""
    SELECT o.observed_at AS observed_at,
           o.payload->'evaluated_buyers'             AS evaluated_buyers,
           o.payload->'compatible_buyers'            AS compatible_buyers,
           o.payload->'highly_compatible_buyers'     AS highly_compatible_buyers,
           o.payload->'recent_compatible_buyers_30d' AS recent_compatible_buyers_30d,
           o.payload->'average_match_score'          AS average_match_score,
           o.payload->'maximum_match_score'          AS maximum_match_score,
           o.payload->'algorithm_version'            AS algorithm_version
      FROM property_watch_observations o
      JOIN property_watches w ON w.id = o.watch_id
     WHERE w.stima_id = %s
       AND w.agency_id = %s
       AND o.observation_type IN ('buyer_pressure_snapshot','buyer_pressure_changed')
     ORDER BY o.observed_at DESC, o.id DESC
     LIMIT 1
  """,(stima_id,agency_id));r=c.fetchone()
 if not r:return None
 riga=dict(r)
 return {'metrics':{k:v for k,v in riga.items() if k!='observed_at'},
         'observed_at':riga['observed_at']}


def home_watch_summary(agency_id,stima_id):
 """Presenza e stato del watch, e i due estremi della sua storia.

 Non ritorna le osservazioni: in LMC-2 il proprietario vede QUANTA storia c'e'
 e da quando, non che cosa contiene - meta' di quei payload sono metriche
 interne (pressione degli acquirenti, inventario dei concorrenti) che
 appartengono alla fase LMC-4 e alla decisione di pubblicarle.
 """
 with core_cursor() as(_,c):
  c.execute("SELECT id, status FROM property_watches WHERE stima_id=%s AND agency_id=%s",(stima_id,agency_id));w=c.fetchone()
  if not w:return None
  # Tipo e data, MAI il payload: e' cio' che serve per dire quanta storia c'e'
  # e da quando, e non c'e' nessun modo di far uscire per sbaglio una metrica
  # interna da righe che non la portano.
  # LMC-3: il payload esce SOLO per `valuation_snapshot`, che e' un valore
  # dell'immobile e appartiene al proprietario. Per ogni altro tipo la colonna
  # non viene nemmeno selezionata: `buyer_pressure_snapshot` e
  # `internal_supply_snapshot` portano metriche interne, e cio' che non si
  # legge non si puo' far uscire per sbaglio.
  c.execute("SELECT observation_type, observed_at, CASE WHEN observation_type=%s THEN payload ELSE NULL END AS payload FROM property_watch_observations WHERE watch_id=%s ORDER BY observed_at ASC, id ASC",('valuation_snapshot',w['id']))
  osservazioni=[dict(x) for x in c.fetchall()]
  # La baseline e' l'unica osservazione di cui si legge il payload, e se ne
  # prende un campo solo (`price_exact`, lato servizio): e' il valore che il
  # motore calcolo' al momento della stima.
  c.execute("SELECT payload FROM property_watch_observations WHERE watch_id=%s AND observation_type='watch_started' ORDER BY observed_at ASC, id ASC LIMIT 1",(w['id'],));b=c.fetchone()
 baseline=b['payload'] if b else None
 return {'status':w['status'],'observations':osservazioni,'baseline_payload':dict(baseline) if isinstance(baseline,dict) else None}


# ---------------------------------------------------------------------------
# LMC-10 - GLI OVERRIDE DEL PROPRIETARIO.
#
# `stime` resta la fotografia del momento della valutazione e non viene mai
# riscritta da qui. Cio' che il proprietario corregge vive in
# `owner_home_overrides`, una riga per stima, e il profilo effettivo lo
# compone `home_profile.build_effective_home_profile` - non queste query, che
# si limitano a portare a galla le due righe.
#
# La tenancy e' quella di sempre: l'agenzia sta nel predicato accanto alla
# stima, mai da sola. `owner_home_overrides` non ha `agency_id` (la 068 dice
# perche'), quindi si passa sempre da `stime`.
# ---------------------------------------------------------------------------

#: Le colonne che il profilo effettivo legge dalla riga di override: la
#: whitelist piu' versione e data. Nominate una per una, come ovunque qui.
HOME_OVERRIDE_COLUMNS = (
 "stima_id","mq","piano","locali","bagni","ascensore","anno","stato",
 "pertinenze","mqgiardino","mqgarage","mqcantina","mqpostoauto","mqtaverna",
 "mqsoffitta","mqterrazzo","numbalconi","altrodescrizione","version","updated_at",
)


def _override_columns(alias="o"):
 return ", ".join(f"{alias}.{c}" for c in HOME_OVERRIDE_COLUMNS)


def home_override(agency_id,stima_id):
 """L'override di QUESTA casa, in QUESTA agenzia. `None` se non esiste.

 `None` non e' un errore: la stragrande maggioranza delle case non e' mai
 stata corretta, e il profilo effettivo coincide con l'originale.
 """
 with core_cursor() as(_,c):
  c.execute(f"SELECT {_override_columns()} FROM owner_home_overrides o "
            "JOIN stime s ON s.id=o.stima_id "
            "WHERE o.stima_id=%s AND s.agency_id=%s",(stima_id,agency_id))
  r=c.fetchone()
 return dict(r) if r else None


def home_overrides_for_account(owner_account_id):
 """Gli override delle case di questo account, per `stima_id`.

 Una query sola per tutta la lista: il riepilogo mostra `mq`, che e' un
 campo correggibile, e leggerlo casa per casa sarebbe una query per riga.
 Passa dallo stesso grant di `list_home_grants`, quindi una casa che non
 compare nella lista non compare nemmeno qui.
 """
 with core_cursor() as(_,c):
  c.execute(f"SELECT {_override_columns()}{_HOME_GRANT_JOIN}"
            "      JOIN owner_home_overrides o ON o.stima_id = x.stima_id"
            f"{_HOME_GRANT_WHERE}",(owner_account_id,))
  return {r['stima_id']:dict(r) for r in c.fetchall()}


def upsert_home_override(owner_account_id,stima_id,valori,expected_version):
 """Scrive l'override, se la versione attesa e' quella giusta.

 Ritorna la riga nuova, oppure `None` quando la versione non corrisponde -
 cioe' quando qualcun altro (l'altra scheda del browser, un comproprietario)
 ha scritto nel frattempo. `None` diventa un 409, mai una sovrascrittura
 silenziosa: chi ha aperto il form su dati vecchi deve ricaricarli.

 IL GRANT SI RIVERIFICA QUI, NELLA STESSA TRANSAZIONE DELLA SCRITTURA.
 Il servizio lo ha gia' controllato per rispondere 404, ma fra quel
 controllo e questa scrittura passa del tempo, e un grant revocato nel mezzo
 non deve poter scrivere. Vale la stessa regola di
 `insert_valuation_snapshot_scoped`: il tenant non si eredita da una lettura
 precedente, si rimette nel predicato. `FOR SHARE` sulla stima perche' il
 trigger della 068 rilegge `stime.agency_id` subito dopo.

 `expected_version = 0` significa "non esiste ancora": l'INSERT e' consentito
 solo allora, e `ON CONFLICT (stima_id) DO NOTHING` trasforma la seconda
 prima-modifica in un `None`, cioe' nello stesso 409 di una versione vecchia.
 """
 campi=[k for k in valori]
 ritorno=", ".join(HOME_OVERRIDE_COLUMNS)
 with core_cursor(commit=True) as(_,c):
  c.execute("""
    SELECT s.id
      FROM owner_stima_access x
      JOIN owner_accounts oa ON oa.id = x.owner_account_id
      JOIN contacts ct ON ct.id = oa.contact_id
      JOIN stime s ON s.id = x.stima_id
     WHERE x.owner_account_id = %s
       AND x.stima_id = %s
       AND ct.agency_id = s.agency_id
       AND x.access_status = 'active'
       AND x.revoked_at IS NULL
       AND (x.valid_until IS NULL OR x.valid_until > NOW())
     FOR SHARE OF s
  """,(owner_account_id,stima_id))
  if c.fetchone() is None:raise NotFoundError(NF)
  if expected_version==0:
   colonne=["stima_id","updated_by_owner_account_id",*campi]
   parametri=[stima_id,owner_account_id,*[valori[k] for k in campi]]
   c.execute(f"INSERT INTO owner_home_overrides ({','.join(colonne)}) "
             f"VALUES ({','.join(['%s']*len(colonne))}) "
             f"ON CONFLICT (stima_id) DO NOTHING RETURNING {ritorno}",parametri)
  else:
   assegnazioni=[f"{k}=%s" for k in campi]
   parametri=[valori[k] for k in campi]
   c.execute("UPDATE owner_home_overrides SET "+",".join([*assegnazioni,
             "version=version+1","updated_at=NOW()","updated_by_owner_account_id=%s"])+
             " WHERE stima_id=%s AND version=%s "
             f"RETURNING {ritorno}",[*parametri,owner_account_id,stima_id,expected_version])
  r=c.fetchone()
 return dict(r) if r else None
