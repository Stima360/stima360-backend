import hashlib,secrets
from datetime import datetime,timezone,timedelta
from .enums import COOKIE_NAME,SESSION_MAX_HOURS,SESSION_IDLE_MINUTES
def generate_secret(): return secrets.token_urlsafe(32)
def hash_secret(v): return hashlib.sha256(v.encode()).hexdigest()
def utcnow(): return datetime.now(timezone.utc)
def valid_session(r,now=None):
 now=now or utcnow();return bool(r and not r.get('revoked_at') and r['expires_at']>now and r['last_seen_at']>now-timedelta(minutes=SESSION_IDLE_MINUTES))
def set_cookie(response,token): response.set_cookie(COOKIE_NAME,token,httponly=True,secure=True,samesite='lax',path='/',max_age=SESSION_MAX_HOURS*3600)
def clear_cookie(response): response.delete_cookie(COOKIE_NAME,path='/',httponly=True,secure=True,samesite='lax')
