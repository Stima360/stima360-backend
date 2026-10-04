from fastapi import APIRouter,Depends,HTTPException,Query,Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from core.exceptions import NotFoundError,ConflictError,ValidationError,PermissionDenied
from operator_auth.context import OperatorContext
from operator_auth.dependencies import legacy_basic_agency_context
from operator_auth.exceptions import PlatformAdminAgencyRequired
from . import census, interactions, mandates, service
from .schemas import *
router=APIRouter(prefix='/api/property',tags=['property'])
def tr(fn,*a,**k):
    try:return fn(*a,**k)
    # CENSIMENTO-1 Fase 3: un campo della 083 inviato dove la 083 non c'e' (POST/PATCH
    # generici su un database senza il modulo) -> 503 leggibile, prima di ogni altro caso
    except census.CensusNotInstalled as e:raise HTTPException(503,str(e))
    except NotFoundError as e:raise HTTPException(404,str(e))
    except ConflictError as e:raise HTTPException(409,str(e))
    except ValidationError as e:raise HTTPException(400,str(e))
    except PlatformAdminAgencyRequired as e:raise HTTPException(403,str(e))
    except PermissionDenied as e:raise HTTPException(403,str(e))
@router.get('/dashboard')
def get_dashboard(ctx:OperatorContext=Depends(legacy_basic_agency_context)):return tr(service.dashboard,ctx)
@router.get('/alerts')
def get_alerts(ctx:OperatorContext=Depends(legacy_basic_agency_context)):return tr(service.alerts,ctx)
# P26-2C: All PROPERTY routes are tenant-scoped.
# `legacy_basic_agency_context` resolves the Default Agency by slug server-side
# and declares `require_admin` itself, so the caller supplies a credential and
# never an agency. Resolved once here and passed down unchanged - the service
# and the repository resolve nothing.
# CRM-OPS-2: cataloghi del form Immobili (territorio, classi energetiche,
# tipologie) e agenti assegnabili - questi ultimi solo a chi puo' assegnare.
@router.get('/form-options')
def get_form_options(ctx:OperatorContext=Depends(legacy_basic_agency_context)):return tr(service.form_options,ctx)
@router.post('/properties',status_code=201)
def create_property(p:PropertyCreate,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return tr(service.create_property,ctx,p)
@router.get('/properties')
def list_properties(limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0),search:str|None=None,status:str|None=None,classification:str|None=None,city:str|None=None,contact_id:int|None=None,lead_id:int|None=None,assigned_to:str|None=None,mandate_expiring:bool=False,missing_documents:bool=False,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return {'items':tr(service.list_properties,ctx,limit,offset,search,status,classification,city,contact_id,lead_id,assigned_to,mandate_expiring,missing_documents)}
@router.get('/properties/{property_id}')
def get_property(property_id:int,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return tr(service.get_property,ctx,property_id)
@router.patch('/properties/{property_id}')
# CENSIMENTO-1 Fase 4: la PATCH generica risponde con `{detail, code}` come le rotte del
# censimento (`trc`), cosi' la guardia §7 porta `code: CENSUS_LOCKED` e non solo il testo.
# Risposte di successo invariate; gli altri errori guadagnano solo un `code` additivo.
def update_property(property_id:int,p:PropertyUpdate,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return trc(service.update_property,ctx,property_id,p)
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
# CRM-OPS-4: INCARICHI (vista in sola lettura su properties + acquisitions:
# nessuna entita' nuova, nessuna creazione da qui) e lo STORICO INTERAZIONI
# dell'immobile (righe di `activities` con property_id, migration 082),
# lo stesso per la scheda Immobile e per la scheda Incarico.
@router.get('/mandates')
def list_mandates(search:str|None=None,agent_id:int|None=None,city:str|None=None,mandate_type:str|None=None,commercial_status:str|None=None,expiry:str|None=None,sort:str='expiry',limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0),ctx:OperatorContext=Depends(legacy_basic_agency_context)):return tr(mandates.list_mandates,ctx,search=search,agent_id=agent_id,city=city,mandate_type=mandate_type,commercial_status=commercial_status,expiry=expiry,sort=sort,limit=limit,offset=offset)
@router.get('/mandates/{property_id}')
def get_mandate(property_id:int,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return tr(mandates.get_mandate,ctx,property_id)
@router.get('/properties/{property_id}/interactions')
def list_interactions(property_id:int,limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0),ctx:OperatorContext=Depends(legacy_basic_agency_context)):return tr(interactions.list_interactions,ctx,property_id,limit=limit,offset=offset)
@router.post('/properties/{property_id}/interactions',status_code=201)
def create_interaction(property_id:int,p:InteractionCreate,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return tr(interactions.create_interaction,ctx,property_id,p)
# CENSIMENTO-1 Fase 3: EDIFICI, UNITA' DI CENSIMENTO, PERTINENZE, ACCESSORI,
# PRESA IN CARICO e ANNULLA (property/census.py), dietro lo stesso
# legacy_basic_agency_context. Gli errori portano `code` accanto a `detail`
# (stesso idioma delle Acquisizioni): la UI distingue un avviso superabile
# (SIMILAR_FOUND, con i candidati) da un blocco (CADASTRAL_DUPLICATE,
# CENSUS_LOCKED, IDEMPOTENCY_KEY_REUSED, UNDO_NOT_POSSIBLE).
# Codice deployato prima della 083 (stessa chiusura leggibile di CRM-OPS-3 RC-1):
# ogni operazione del censimento verifica la 083 sul catalogo PRIMA di leggere
# righe (property/census.py::_assicura_083) e risponde 503 CENSUS_NOT_INSTALLED.
CENSUS_NOT_INSTALLED=census.CENSUS_NOT_INSTALLED_MESSAGE
def trc(fn,*a,status=200,**k):
    try:esito=fn(*a,**k)
    except census.CensusNotInstalled as e:return JSONResponse(status_code=503,content={'detail':str(e),'code':e.code})
    except (NotFoundError,ConflictError,ValidationError,PermissionDenied,PlatformAdminAgencyRequired) as e:
        stato={NotFoundError:404,ConflictError:409,ValidationError:400,PermissionDenied:403,PlatformAdminAgencyRequired:403}
        http=next(v for c,v in stato.items() if isinstance(e,c))
        corpo=dict(getattr(e,'extra',None) or {});corpo.update({'detail':str(e),'code':getattr(e,'code',None) or {404:'NOT_FOUND',409:'CONFLICT',400:'VALIDATION_ERROR',403:'FORBIDDEN'}[http]})
        return JSONResponse(status_code=http,content=jsonable_encoder(corpo))
    if esito is None:return Response(status_code=204)
    return JSONResponse(status_code=status,content=jsonable_encoder(esito))
@router.get('/buildings')
def list_buildings(search:str|None=None,city:str|None=None,limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0),ctx:OperatorContext=Depends(legacy_basic_agency_context)):return trc(census.list_buildings,ctx,search=search,city=city,limit=limit,offset=offset)
@router.post('/buildings',status_code=201)
def create_building(p:BuildingCreate,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return trc(census.create_building,ctx,p,status=201)
@router.get('/buildings/{building_id}')
def get_building(building_id:int,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return trc(census.get_building,ctx,building_id)
@router.patch('/buildings/{building_id}')
def update_building(building_id:int,p:BuildingUpdate,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return trc(census.update_building,ctx,building_id,p)
@router.post('/census/units',status_code=201)
def create_census_unit(p:CensusUnitCreate,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return trc(census.create_unit,ctx,p,status=201)
@router.get('/properties/{property_id}/census')
def get_property_census(property_id:int,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return trc(census.get_census,ctx,property_id)
@router.post('/properties/{property_id}/pertinenze/link')
def link_pertinenza(property_id:int,p:PertinenzaLink,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return trc(census.link_pertinenza,ctx,property_id,p)
@router.post('/properties/{property_id}/pertinenze/{pertinenza_id}/unlink')
def unlink_pertinenza(property_id:int,pertinenza_id:int,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return trc(census.unlink_pertinenza,ctx,property_id,pertinenza_id)
@router.post('/properties/{property_id}/accessories',status_code=201)
def create_accessory(property_id:int,p:AccessoryCreate,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return trc(census.create_accessory,ctx,property_id,p,status=201)
@router.patch('/properties/{property_id}/accessories/{accessory_id}')
def update_accessory(property_id:int,accessory_id:int,p:AccessoryUpdate,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return trc(census.update_accessory,ctx,property_id,accessory_id,p)
@router.delete('/properties/{property_id}/accessories/{accessory_id}',status_code=204)
def delete_accessory(property_id:int,accessory_id:int,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return trc(census.delete_accessory,ctx,property_id,accessory_id)
@router.post('/properties/{property_id}/accessories/{accessory_id}/resolve')
def resolve_accessory(property_id:int,accessory_id:int,p:AccessoryResolve,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return trc(census.resolve_accessory,ctx,property_id,accessory_id,p)
@router.post('/properties/{property_id}/take-in-charge')
def take_in_charge(property_id:int,p:TakeInCharge,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return trc(census.take_in_charge,ctx,property_id,p)
@router.post('/properties/{property_id}/undo-create')
def undo_create(property_id:int,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return trc(census.undo_create,ctx,property_id)
