from pathlib import Path
from datetime import timedelta
import pytest
from pydantic import ValidationError
from owner.security import *
from owner.schemas import *
R=Path(__file__).parents[1]
class Resp:
 def set_cookie(self,*a,**k):self.k=k
 def delete_cookie(self,*a,**k):self.k=k
def test_secret_hash():
 s=generate_secret();assert s!=hash_secret(s) and len(hash_secret(s))==64
def test_cookie_secure():
 r=Resp();set_cookie(r,'x');assert r.k['httponly'] and r.k['secure'] and r.k['samesite']=='lax'
def test_no_browser_storage():
 s=(R/'static/owner_portal/assets/app.js').read_text();assert 'localStorage' not in s and 'sessionStorage' not in s
def test_session_idle():
 n=utcnow();assert valid_session({'revoked_at':None,'expires_at':n+timedelta(hours=1),'last_seen_at':n});assert not valid_session({'revoked_at':None,'expires_at':n+timedelta(hours=1),'last_seen_at':n-timedelta(minutes=61)},n)
def test_feedback_limit():
 with pytest.raises(ValidationError):FeedbackCreate(feedback_type='general_message',subject='x',message='x'*5001)
def test_closed_role():
 with pytest.raises(ValidationError):AccessCreate(owner_account_id=1,property_id=1,access_role='admin')
def test_token_min():
 with pytest.raises(ValidationError):TokenConsume(token='short')
def test_migration_tables():
 s=(R/'migrations/009_owner_01.sql').read_text().lower()
 for t in ['owner_accounts','owner_property_access','owner_access_tokens','owner_sessions','owner_publications','owner_publication_reads','owner_feedback','owner_audit_log']:assert f'create table if not exists {t}' in s
def test_migration_isolated():
 s=(R/'migrations/009_owner_01.sql').read_text().lower();assert 'alter table contacts' not in s and 'alter table properties' not in s
def test_no_owner_docs():assert 'owner_documents' not in (R/'migrations/009_owner_01.sql').read_text()
def test_no_sensitive_property_data():
 s=(R/'owner/repository.py').read_text();assert 'classification' not in s and 'minimum_price' not in s and 'internal_notes' not in s and 'storage_path' not in s
def test_immutability_guard():
 s=(R/'owner/repository.py').read_text();assert "r['status']!='draft'" in s and 'immutabile' in s
def test_version_links():
 s=(R/'migrations/009_owner_01.sql').read_text();assert 'supersedes_publication_id' in s and 'superseded_by_publication_id' in s
def test_reads_version_specific():assert 'UNIQUE(publication_id,owner_account_id)' in (R/'migrations/009_owner_01.sql').read_text()
def test_timeline_published_only():assert "status='published'" in (R/'owner/repository.py').read_text()
def test_uniform_404():assert "HTTPException(404,'Risorsa non trovata')" in (R/'owner/router_portal.py').read_text()
def test_no_document_api():
 s=(R/'owner/router_admin.py').read_text()+(R/'owner/router_portal.py').read_text();assert '/documents' not in s
def test_ui_present():assert (R/'static/owner_admin/index.html').exists() and (R/'static/owner_portal/index.html').exists()
def test_main_excluded():assert not (R/'main.py').exists()
def test_rollback_isolated():
 s=(R/'migrations/009_owner_01_down.sql').read_text().lower();assert 'drop table if exists contacts' not in s and 'drop table if exists properties' not in s
def test_cumulative_instruction():assert 'Non sostituire main.py' in (R/'INTEGRAZIONE_MAIN_CUMULATIVA.txt').read_text()
def test_production_unchanged_doc():assert 'produzione invariata' in (R/'README_OWNER_0.1.txt').read_text().lower()
