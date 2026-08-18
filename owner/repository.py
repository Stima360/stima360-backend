from datetime import date, timedelta
from psycopg2.extras import Json
from core.database import core_cursor
from core.repository import create_activity_with_cursor
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
 with core_cursor(commit=True) as(_,c):
  c.execute('SELECT * FROM owner_publications WHERE id=%s FOR UPDATE',(i,));r=one(c)
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
          WHERE oa.id=%s AND oa.status='active' AND x.property_id=%s
            AND x.access_status='active' AND x.revoked_at IS NULL
            AND (x.valid_until IS NULL OR x.valid_until>NOW())
          FOR UPDATE OF oa,x""",
      (a,p),
  )
  access=c.fetchone()
  if not access:raise NotFoundError(NF)
  contact_id=access['contact_id']
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
  _audit_with_cursor(c,'feedback_submitted',a,p,'owner_feedback',r['id'])
 return _public_feedback(r)

def list_feedback(a=None,p=None):
 if a is not None:
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
 with core_cursor() as(_,c):
  c.execute('SELECT * FROM owner_feedback ORDER BY submitted_at DESC')
  return[dict(x) for x in c.fetchall()]
def dashboard():
 with core_cursor() as(_,c):c.execute("SELECT (SELECT COUNT(*) FROM owner_accounts WHERE status='active') active_accounts,(SELECT COUNT(*) FROM owner_property_access WHERE access_status='active') active_access,(SELECT COUNT(*) FROM owner_publications WHERE status='published') published,(SELECT COUNT(*) FROM owner_feedback WHERE status='new') new_feedback");return dict(c.fetchone())
def audits():
 with core_cursor() as(_,c):c.execute('SELECT * FROM owner_audit_log ORDER BY created_at DESC LIMIT 200');return[dict(x) for x in c.fetchall()]

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


def _property_for_document(c, document_id, *, for_update=False):
    suffix = " FOR UPDATE" if for_update else ""
    c.execute(
        """SELECT id,property_id,document_type,title,url,storage_key,status,
                  expires_at,metadata,created_at,updated_at
           FROM property_documents WHERE id=%s""" + suffix,
        (document_id,),
    )
    return one(c)


def _property_for_visit(c, visit_id):
    c.execute(
        "SELECT id,property_id,scheduled_at,status FROM property_visits WHERE id=%s",
        (visit_id,),
    )
    return one(c)


def _validate_target_account(c, account_id, property_id):
    if account_id is None:
        return
    c.execute(
        """SELECT 1 FROM owner_property_access
           WHERE owner_account_id=%s AND property_id=%s
             AND access_status='active' AND revoked_at IS NULL
             AND (valid_until IS NULL OR valid_until>NOW())""",
        (account_id, property_id),
    )
    if not c.fetchone():
        raise NotFoundError(NF)


def _shared_document_with_source(c, item_id, *, for_update=False):
    suffix = " FOR UPDATE OF sd" if for_update else ""
    c.execute(
        """SELECT sd.*,pd.property_id,pd.title AS source_title,
                  pd.document_type AS source_document_type,pd.status AS source_status,
                  pd.expires_at AS source_expires_at,pd.storage_key,pd.url,
                  pd.metadata AS source_metadata
           FROM owner_shared_documents sd
           JOIN property_documents pd ON pd.id=sd.property_document_id
           WHERE sd.id=%s""" + suffix,
        (item_id,),
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


def create_shared_document(d):
    with core_cursor(commit=True) as (_, c):
        src = _property_for_document(c, d["property_document_id"])
        if src["status"] != "available" or not src.get("storage_key"):
            raise ValidationError("Il documento deve essere disponibile in storage privato")
        _validate_target_account(c, d.get("owner_account_id"), src["property_id"])
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


def create_uploaded_shared_document(d, staged, storage=None):
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
                previous = _shared_document_with_source(c, previous_id, for_update=True)
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

            _validate_target_account(c, target_account, d["property_id"])
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
    property_id=None,
    status=None,
    owner_account_id=None,
    document_type=None,
    limit=100,
    offset=0,
):
    filters = []
    values = []
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
    where = " WHERE " + " AND ".join(filters) if filters else ""
    values.extend((limit, offset))
    with core_cursor() as (_, c):
        c.execute(
            """SELECT sd.*,pd.property_id,pd.title AS source_title,
                      pd.document_type AS source_document_type,pd.status AS source_status,
                      pd.expires_at AS source_expires_at,pd.storage_key
               FROM owner_shared_documents sd
               JOIN property_documents pd ON pd.id=sd.property_document_id"""
            + where
            + " ORDER BY sd.created_at DESC LIMIT %s OFFSET %s",
            values,
        )
        return [_admin_shared_document(dict(row)) for row in c.fetchall()]


def get_shared_document(i):
    with core_cursor() as (_, c):
        return _admin_shared_document(_shared_document_with_source(c, i))


def update_shared_document(i, d):
    old = get_shared_document(i)
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


def publish_shared_document(i, storage=None):
    from .document_storage import get_document_storage

    storage = storage or get_document_storage()
    with core_cursor() as (_, c):
        preflight = _shared_document_with_source(c, i)
    if preflight["status"] != "draft":
        raise ConflictError("Solo draft pubblicabile")
    _validate_source_contract(preflight, storage, verify_provider=True)

    with core_cursor(commit=True) as (_, c):
        current = _shared_document_with_source(c, i, for_update=True)
        if current["status"] != "draft":
            raise ConflictError("Solo draft pubblicabile")
        _validate_target_account(c, current.get("owner_account_id"), current["property_id"])
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
            previous_source = _property_for_document(c, previous["property_document_id"])
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


def revoke_shared_document(i, actor=None, reason=None):
    old = get_shared_document(i)
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


def archive_shared_document(i):
    old = get_shared_document(i)
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


def supersede_shared_document(i, d):
    with core_cursor(commit=True) as (_, c):
        old = _shared_document_with_source(c, i, for_update=True)
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
        source = _property_for_document(c, new_document_id)
        if source["property_id"] != old["property_id"]:
            raise ConflictError("Il nuovo documento appartiene a un altro immobile")
        if source["status"] != "available" or not source.get("storage_key"):
            raise ValidationError("Il nuovo documento non è disponibile in storage privato")
        _validate_target_account(c, old.get("owner_account_id"), old["property_id"])
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
               JOIN owner_property_access x ON x.property_id=pd.property_id
               WHERE sd.id=%s AND sd.status='published'
                 AND sd.superseded_by_shared_document_id IS NULL
                 AND (sd.expires_at IS NULL OR sd.expires_at>NOW())
                 AND (sd.owner_account_id IS NULL OR sd.owner_account_id=%s)
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
                 ON x.property_id=pd.property_id AND x.owner_account_id=%s
               LEFT JOIN owner_document_reads dr
                 ON dr.shared_document_id=sd.id AND dr.owner_account_id=%s
               WHERE pd.property_id=%s AND sd.status='published'
                 AND sd.superseded_by_shared_document_id IS NULL
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
               JOIN owner_property_access x ON x.property_id=pd.property_id
               LEFT JOIN owner_document_reads dr
                 ON dr.shared_document_id=sd.id AND dr.owner_account_id=%s
               WHERE sd.id=%s AND sd.status='published'
                 AND sd.superseded_by_shared_document_id IS NULL
                 AND pd.status='available'
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


def prepare_admin_shared_document_download(i, storage=None):
    from .document_storage import get_document_storage

    storage = storage or get_document_storage()
    with core_cursor() as (_, c):
        source = _shared_document_with_source(c, i)
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


def shared_document_reads(i):
    doc = get_shared_document(i)
    with core_cursor() as (_, c):
        c.execute(
            """SELECT dr.owner_account_id,dr.first_viewed_at,dr.last_viewed_at,
                      dr.view_count,dr.acknowledged_at
               FROM owner_document_reads dr
               JOIN owner_property_access x
                 ON x.owner_account_id=dr.owner_account_id AND x.property_id=%s
               WHERE dr.shared_document_id=%s
               ORDER BY dr.first_viewed_at""",
            (doc["property_id"], i),
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


def _visit_feedback_for_update(c, i):
    c.execute(
        """SELECT vf.*,pv.property_id
           FROM owner_visit_feedback_publications vf
           JOIN property_visits pv ON pv.id=vf.property_visit_id
           WHERE vf.id=%s
           FOR UPDATE OF vf""",
        (i,),
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


def create_visit_feedback_publication(d):
    summary = _validated_public_summary(d["public_summary"])
    with core_cursor(commit=True) as (_, c):
        src = _property_for_visit(c, d["property_visit_id"])
        _validate_target_account(c, d.get("owner_account_id"), src["property_id"])
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
    property_visit_id=None,
    property_id=None,
    status=None,
    owner_account_id=None,
    category=None,
    limit=50,
    offset=0,
):
    clauses = []
    values = []
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
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    values.extend((limit, offset))
    with core_cursor() as (_, c):
        c.execute(
            """SELECT vf.*,pv.property_id
               FROM owner_visit_feedback_publications vf
               JOIN property_visits pv ON pv.id=vf.property_visit_id"""
            + where
            + " ORDER BY vf.created_at DESC LIMIT %s OFFSET %s",
            values,
        )
        return [dict(row) for row in c.fetchall()]


def get_visit_feedback_publication(i):
    with core_cursor() as (_, c):
        c.execute(
            """SELECT vf.*,pv.property_id
               FROM owner_visit_feedback_publications vf
               JOIN property_visits pv ON pv.id=vf.property_visit_id
               WHERE vf.id=%s""",
            (i,),
        )
        return one(c)


def update_visit_feedback_publication(i, d):
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
        old = _visit_feedback_for_update(c, i)
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


def publish_visit_feedback(i):
    with core_cursor(commit=True) as (_, c):
        current = _visit_feedback_for_update(c, i)
        if current["status"] != "draft":
            raise ConflictError("Solo draft pubblicabile")
        _validated_public_summary(current["public_summary"])
        _validate_target_account(c, current.get("owner_account_id"), current["property_id"])

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


def archive_visit_feedback(i):
    with core_cursor(commit=True) as (_, c):
        old = _visit_feedback_for_update(c, i)
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


def supersede_visit_feedback(i, d):
    summary = _validated_public_summary(d["public_summary"])
    with core_cursor(commit=True) as (_, c):
        old = _visit_feedback_for_update(c, i)
        if old["status"] != "published":
            raise ConflictError("Solo published sostituibile")
        if old.get("superseded_by_feedback_publication_id") is not None:
            raise ConflictError("Solo la versione corrente può essere sostituita")
        _validate_target_account(c, old.get("owner_account_id"), old["property_id"])
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
               JOIN owner_property_access x ON x.property_id=pv.property_id
               WHERE vf.id=%s
                 AND vf.status='published'
                 AND vf.superseded_by_feedback_publication_id IS NULL
                 AND (vf.owner_account_id IS NULL OR vf.owner_account_id=%s)
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


def update_feedback_status(i,d):
 with core_cursor(commit=True) as(_,c):
  c.execute('SELECT * FROM owner_feedback WHERE id=%s FOR UPDATE',(i,));old=one(c)
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
        LEFT JOIN owner_notification_preferences np
          ON np.owner_account_id=x.owner_account_id
        WHERE x.property_id=%s
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
                 ON x.owner_account_id=n.owner_account_id AND x.property_id=n.property_id
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
               WHERE n.id=%s AND n.owner_account_id=%s
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
