from fastapi import APIRouter,Depends,HTTPException,Query,Response
from core.exceptions import NotFoundError,ConflictError,ValidationError
from operator_auth.context import OperatorContext
from operator_auth.dependencies import legacy_basic_agency_context
from operator_auth.exceptions import PlatformAdminAgencyRequired
from . import service
from .schemas import *
router=APIRouter(prefix='/api/property',tags=['property'])
def tr(fn,*a,**k):
    try:return fn(*a,**k)
    except NotFoundError as e:raise HTTPException(404,str(e))
    except ConflictError as e:raise HTTPException(409,str(e))
    except ValidationError as e:raise HTTPException(400,str(e))
    except PlatformAdminAgencyRequired as e:raise HTTPException(403,str(e))
@router.get('/dashboard')
def get_dashboard(ctx:OperatorContext=Depends(legacy_basic_agency_context)):return tr(service.dashboard,ctx)
@router.get('/alerts')
def get_alerts(ctx:OperatorContext=Depends(legacy_basic_agency_context)):return tr(service.alerts,ctx)
# P26-2C: All PROPERTY routes are tenant-scoped.
# `legacy_basic_agency_context` resolves the Default Agency by slug server-side
# and declares `require_admin` itself, so the caller supplies a credential and
# never an agency. Resolved once here and passed down unchanged - the service
# and the repository resolve nothing.
@router.post('/properties',status_code=201)
def create_property(p:PropertyCreate,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return tr(service.create_property,ctx,p)
@router.get('/properties')
def list_properties(limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0),search:str|None=None,status:str|None=None,classification:str|None=None,city:str|None=None,contact_id:int|None=None,lead_id:int|None=None,assigned_to:str|None=None,mandate_expiring:bool=False,missing_documents:bool=False,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return {'items':tr(service.list_properties,ctx,limit,offset,search,status,classification,city,contact_id,lead_id,assigned_to,mandate_expiring,missing_documents)}
@router.get('/properties/{property_id}')
def get_property(property_id:int,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return tr(service.get_property,ctx,property_id)
@router.patch('/properties/{property_id}')
def update_property(property_id:int,p:PropertyUpdate,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return tr(service.update_property,ctx,property_id,p)
@router.delete('/properties/{property_id}')
def archive_property(property_id:int,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return tr(service.archive_property,ctx,property_id)
@router.post('/properties/{property_id}/contacts',status_code=201)
def add_contact(property_id:int,p:PropertyContactCreate,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return tr(service.add_contact,ctx,property_id,p)
@router.delete('/properties/{property_id}/contacts/{contact_id}/{role}',status_code=204)
def delete_contact(property_id:int,contact_id:int,role:str,ctx:OperatorContext=Depends(legacy_basic_agency_context)):tr(service.delete_contact,ctx,property_id,contact_id,role);return Response(status_code=204)
@router.post('/properties/{property_id}/leads',status_code=201)
def add_lead(property_id:int,p:PropertyLeadCreate,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return tr(service.add_lead,ctx,property_id,p)
@router.delete('/properties/{property_id}/leads/{lead_id}',status_code=204)
def delete_lead(property_id:int,lead_id:int,ctx:OperatorContext=Depends(legacy_basic_agency_context)):tr(service.delete_lead,ctx,property_id,lead_id);return Response(status_code=204)
@router.post('/properties/{property_id}/documents',status_code=201)
def add_document(property_id:int,p:DocumentCreate,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return tr(service.add_document,ctx,property_id,p)
@router.patch('/documents/{document_id}')
def update_document(document_id:int,p:DocumentUpdate,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return tr(service.update_document,ctx,document_id,p)
@router.delete('/documents/{document_id}',status_code=204)
def delete_document(document_id:int,ctx:OperatorContext=Depends(legacy_basic_agency_context)):tr(service.delete_document,ctx,document_id);return Response(status_code=204)
@router.post('/properties/{property_id}/photos',status_code=201)
def add_photo(property_id:int,p:PhotoCreate,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return tr(service.add_photo,ctx,property_id,p)
@router.patch('/photos/{photo_id}')
def update_photo(photo_id:int,p:PhotoUpdate,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return tr(service.update_photo,ctx,photo_id,p)
@router.delete('/photos/{photo_id}',status_code=204)
def delete_photo(photo_id:int,ctx:OperatorContext=Depends(legacy_basic_agency_context)):tr(service.delete_photo,ctx,photo_id);return Response(status_code=204)
@router.get('/visits')
def list_visits(limit:int=Query(100,ge=1,le=500),offset:int=Query(0,ge=0),status:str|None=None,from_date:str|None=None,to_date:str|None=None,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return {'items':tr(service.list_visits,ctx,limit,offset,status,from_date,to_date)}
@router.post('/properties/{property_id}/visits',status_code=201)
def add_visit(property_id:int,p:VisitCreate,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return tr(service.add_visit,ctx,property_id,p)
@router.patch('/visits/{visit_id}')
def update_visit(visit_id:int,p:VisitUpdate,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return tr(service.update_visit,ctx,visit_id,p)
@router.delete('/visits/{visit_id}',status_code=204)
def delete_visit(visit_id:int,ctx:OperatorContext=Depends(legacy_basic_agency_context)):tr(service.delete_visit,ctx,visit_id);return Response(status_code=204)
