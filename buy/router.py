from fastapi import APIRouter,HTTPException,Query,Response
from core.exceptions import NotFoundError,ConflictError,ValidationError
from . import service
from .schemas import *
router=APIRouter(prefix='/api/buy',tags=['buy'])
def tr(fn,*a,**k):
    try:return fn(*a,**k)
    except NotFoundError as e:raise HTTPException(404,str(e))
    except ConflictError as e:raise HTTPException(409,str(e))
    except ValidationError as e:raise HTTPException(400,str(e))
@router.get('/dashboard')
def dashboard():return tr(service.dashboard)
@router.post('/requests',status_code=201)
def create_request(p:BuyRequestCreate):return tr(service.create_request,p)
@router.get('/requests')
def list_requests(limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0),search:str|None=None,status:str|None=None,priority:str|None=None,urgency:str|None=None,contact_id:int|None=None,lead_id:int|None=None,assigned_to:str|None=None):
    return {'items':tr(service.list_requests,limit,offset,search,status,priority,urgency,contact_id,lead_id,assigned_to)}
@router.get('/requests/{request_id}')
def get_request(request_id:int):return tr(service.get_request,request_id)
@router.patch('/requests/{request_id}')
def update_request(request_id:int,p:BuyRequestUpdate):return tr(service.update_request,request_id,p)
@router.delete('/requests/{request_id}')
def archive_request(request_id:int):return tr(service.archive_request,request_id)
@router.get('/requests/{request_id}/normalized')
def normalized(request_id:int):return tr(service.normalized,request_id)
@router.post('/requests/{request_id}/locations',status_code=201)
def add_location(request_id:int,p:LocationCreate):return tr(service.add_location,request_id,p)
@router.delete('/locations/{location_id}',status_code=204)
def delete_location(location_id:int):tr(service.delete_location,location_id);return Response(status_code=204)
@router.post('/requests/{request_id}/typologies',status_code=201)
def add_typology(request_id:int,p:TypologyCreate):return tr(service.add_typology,request_id,p)
@router.delete('/typologies/{typology_id}',status_code=204)
def delete_typology(typology_id:int):tr(service.delete_typology,typology_id);return Response(status_code=204)
@router.post('/requests/{request_id}/features',status_code=201)
def add_feature(request_id:int,p:FeatureCreate):return tr(service.add_feature,request_id,p)
@router.delete('/features/{feature_id}',status_code=204)
def delete_feature(feature_id:int):tr(service.delete_feature,feature_id);return Response(status_code=204)
