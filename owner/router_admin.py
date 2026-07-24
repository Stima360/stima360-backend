from fastapi import APIRouter,HTTPException
from core.exceptions import NotFoundError,ConflictError
from .schemas import *
from . import repository as r
router=APIRouter(prefix='/api/owner/admin',tags=['owner-admin'])
def x(f,*a):
 try:return f(*a)
 except NotFoundError:raise HTTPException(404,'Risorsa non trovata')
 except ConflictError as e:raise HTTPException(409,str(e))
@router.get('/dashboard')
def dash():return x(r.dashboard)
@router.get('/accounts')
def accounts():return{'items':x(r.list_accounts)}
@router.post('/accounts',status_code=201)
def account(p:AccountCreate):return x(r.create_account,p.model_dump())
@router.post('/accounts/{i}/disable')
def disable(i:int):return x(r.set_account,i,'disabled')
@router.post('/accounts/{i}/enable')
def enable(i:int):return x(r.set_account,i,'active')
@router.get('/access')
def access():return{'items':x(r.list_access)}
@router.post('/access',status_code=201)
def access_create(p:AccessCreate):return x(r.create_access,p.model_dump())
@router.post('/access/{i}/revoke')
def revoke(i:int):return x(r.revoke_access,i)
@router.post('/accounts/{i}/tokens')
def token(i:int,p:TokenCreate):
 row,raw=x(r.create_token,i,p.token_type,p.expires_minutes,p.created_by);return{'token_id':row['id'],'expires_at':row['expires_at'],'token':raw,'one_time_display':True}
@router.get('/publications')
def pubs():return{'items':x(r.list_publications)}
@router.post('/publications',status_code=201)
def pub(p:PublicationCreate):return x(r.create_publication,p.model_dump())
@router.patch('/publications/{i}')
def edit(i:int,p:PublicationUpdate):return x(r.update_publication,i,p.model_dump(exclude_unset=True))
@router.post('/publications/{i}/publish')
def publish(i:int):return x(r.publish,i)
@router.post('/publications/{i}/archive')
def archive(i:int):return x(r.archive,i)
@router.post('/publications/{i}/supersede',status_code=201)
def supersede(i:int,p:PublicationCreate):return x(r.supersede,i,p.model_dump())
@router.get('/feedback')
def feedback():return{'items':x(r.list_feedback)}
@router.get('/audit')
def audit():return{'items':x(r.audits)}
