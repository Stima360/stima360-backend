from fastapi import APIRouter,Depends,HTTPException,Query,Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from core.exceptions import NotFoundError,ConflictError,ValidationError,PermissionDenied
from core.property_trash import PropertyInTrash, trash_409  # DELETE-ARCH 2B2
from core.contact_trash import ContactInTrash  # CESTINO-CONTATTI-1: stessa forma {detail, code}
from core.building_trash import BuildingInTrash  # CESTINO-EDIFICI-1: stessa forma {detail, code}
from operator_auth.context import OperatorContext
from operator_auth.dependencies import legacy_basic_agency_context
from operator_auth.exceptions import PlatformAdminAgencyRequired
from . import building_deletion, building_lifecycle, building_position, census, composition, interactions, mandates, service, site_sync
from .schemas import *
router=APIRouter(prefix='/api/property',tags=['property'])
def tr(fn,*a,**k):
    try:return fn(*a,**k)
    # CENSIMENTO-1 Fase 3: un campo della 083 inviato dove la 083 non c'e' (POST/PATCH
    # generici su un database senza il modulo) -> 503 leggibile, prima di ogni altro caso
    except census.CensusNotInstalled as e:raise HTTPException(503,str(e))
    except NotFoundError as e:raise HTTPException(404,str(e))
    except (PropertyInTrash, ContactInTrash, BuildingInTrash) as e:return trash_409(e)      # DELETE-ARCH 2B2: {detail, code}
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
# CENSIMENTO-1 Fase 5: `record_kind` = crm (default: le schede operative, per
# il tab Commerciale, i selettori operativi e property_admin) | census (le
# unita' censite, per il tab Censimento) | all (ricerca globale, «Collega
# esistente», viste per relazione). Valore diverso: 422.
@router.get('/properties')
def list_properties(limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0),search:str|None=None,status:str|None=None,classification:str|None=None,city:str|None=None,contact_id:int|None=None,lead_id:int|None=None,assigned_to:str|None=None,mandate_expiring:bool=False,missing_documents:bool=False,record_kind:str=Query('crm',pattern='^(crm|census|all)$'),include_archived:bool=False,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return {'items':tr(service.list_properties,ctx,limit,offset,search,status,classification,city,contact_id,lead_id,assigned_to,mandate_expiring,missing_documents,record_kind=record_kind,include_archived=include_archived)}
@router.get('/properties/{property_id}')
def get_property(property_id:int,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return tr(service.get_property,ctx,property_id)
@router.patch('/properties/{property_id}')
# CENSIMENTO-1 Fase 4: la PATCH generica risponde con `{detail, code}` come le rotte del
# censimento (`trc`), cosi' la guardia §7 porta `code: CENSUS_LOCKED` e non solo il testo.
# Risposte di successo invariate; gli altri errori guadagnano solo un `code` additivo.
def update_property(property_id:int,p:PropertyUpdate,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return trc(_update_property_guarded,ctx,property_id,p)
# CENSIMENTO-2 Fase A (A3, D2, D3): la PATCH generica non invalida in silenzio una verifica
# catastale documentata (409 VERIFIED_DATA_LOCKED) e non cambia l'interno senza storico
# (409 INTERNAL_NUMBER_TRACKED): controllo e scrittura del service sotto lo stesso lock della scheda.
def _update_property_guarded(ctx,property_id,p):
    return composition.update_property_guarded(ctx,property_id,p,lambda:service.update_property(ctx,property_id,p))
# DELETE-ARCH Fase 0: Archivia/Riattiva sono azioni esplicite (D11). La DELETE
# storica resta come archivio per i client esistenti, deprecata: nessun
# caller nuovo la usa e la UI e' passata a /archive. Errori con `code`
# (ARCHIVE_BLOCKED + blockers, NOT_ASSIGNED, ...), come il censimento.
@router.post('/properties/{property_id}/archive')
def archive_property_explicit(property_id:int,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return trc(service.archive_property,ctx,property_id)
@router.post('/properties/{property_id}/unarchive')
def unarchive_property(property_id:int,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return trc(service.unarchive_property,ctx,property_id)
# DELETE-ARCH Fase 2B1: Cestino Immobili. Azioni esplicite, errori con `code`
# (TRASH_BLOCKED + blockers, ALREADY_DELETED, NOT_DELETED, RESTORE_CONFLICT +
# conflicts, NOT_ASSIGNED, NOT_DELETED_BY_YOU). La DELETE storica qui sotto
# NON cambia significato: resta l'archiviazione deprecata.
@router.get('/properties/{property_id}/deletion-check')
def property_deletion_check(property_id:int,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return trc(service.deletion_check,ctx,property_id)
@router.post('/properties/{property_id}/trash')
def trash_property(property_id:int,p:PropertyTrash,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return trc(service.trash_property,ctx,property_id,p)
@router.post('/properties/{property_id}/restore')
def restore_property(property_id:int,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return trc(service.restore_property,ctx,property_id)
# DELETE-ARCH Fase 2B3: elenco del Cestino (sola lettura, agency-scoped; agent: solo i propri).
@router.get('/trash')
def list_trash(limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0),ctx:OperatorContext=Depends(legacy_basic_agency_context)):return trc(service.list_trash,ctx,limit,offset)
@router.delete('/properties/{property_id}',deprecated=True)
def archive_property(property_id:int,ctx:OperatorContext=Depends(legacy_basic_agency_context)):
    esito=trc(service.archive_property,ctx,property_id)
    esito.headers['Deprecation']='true';esito.headers['Link']=f'</api/property/properties/{property_id}/archive>; rel="successor-version"'
    return esito
@router.post('/properties/{property_id}/contacts',status_code=201)
def add_contact(property_id:int,p:PropertyContactCreate,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return tr(service.add_contact,ctx,property_id,p)
@router.delete('/properties/{property_id}/contacts/{contact_id}/{role}',status_code=204)
def delete_contact(property_id:int,contact_id:int,role:str,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return trc(service.delete_contact,ctx,property_id,contact_id,role)
@router.post('/properties/{property_id}/leads',status_code=201)
def add_lead(property_id:int,p:PropertyLeadCreate,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return tr(service.add_lead,ctx,property_id,p)
@router.delete('/properties/{property_id}/leads/{lead_id}',status_code=204)
def delete_lead(property_id:int,lead_id:int,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return trc(service.delete_lead,ctx,property_id,lead_id)
@router.post('/properties/{property_id}/documents',status_code=201)
def add_document(property_id:int,p:DocumentCreate,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return tr(service.add_document,ctx,property_id,p)
@router.patch('/documents/{document_id}')
def update_document(document_id:int,p:DocumentUpdate,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return tr(service.update_document,ctx,document_id,p)
@router.delete('/documents/{document_id}',status_code=204)
def delete_document(document_id:int,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return trc(service.delete_document,ctx,document_id)
@router.post('/properties/{property_id}/photos',status_code=201)
def add_photo(property_id:int,p:PhotoCreate,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return tr(service.add_photo,ctx,property_id,p)
@router.patch('/photos/{photo_id}')
def update_photo(photo_id:int,p:PhotoUpdate,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return tr(service.update_photo,ctx,photo_id,p)
@router.delete('/photos/{photo_id}',status_code=204)
def delete_photo(photo_id:int,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return trc(service.delete_photo,ctx,photo_id)
@router.get('/visits')
def list_visits(limit:int=Query(100,ge=1,le=500),offset:int=Query(0,ge=0),status:str|None=None,from_date:str|None=None,to_date:str|None=None,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return {'items':tr(service.list_visits,ctx,limit,offset,status,from_date,to_date)}
@router.post('/properties/{property_id}/visits',status_code=201)
def add_visit(property_id:int,p:VisitCreate,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return tr(service.add_visit,ctx,property_id,p)
@router.patch('/visits/{visit_id}')
def update_visit(visit_id:int,p:VisitUpdate,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return tr(service.update_visit,ctx,visit_id,p)
@router.delete('/visits/{visit_id}',status_code=204)
def delete_visit(visit_id:int,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return trc(service.delete_visit,ctx,visit_id)
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
    # CENSIMENTO-2: i vincoli e le colonne della migration 100 arrivano anche dalla
    # PATCH generica (provenienza dalla scheda dell'unita'): risposta leggibile, mai 500.
    except composition.DB_ERRORS as e:
        tradotto=composition.translate_db_error(e)
        if tradotto is None:raise
        http,code,detail=tradotto
        return JSONResponse(status_code=http,content={'detail':detail,'code':code})
    except (NotFoundError,ConflictError,ValidationError,PermissionDenied,PlatformAdminAgencyRequired) as e:
        stato={NotFoundError:404,ConflictError:409,ValidationError:400,PermissionDenied:403,PlatformAdminAgencyRequired:403}
        http=next(v for c,v in stato.items() if isinstance(e,c))
        corpo=dict(getattr(e,'extra',None) or {});corpo.update({'detail':str(e),'code':getattr(e,'code',None) or {404:'NOT_FOUND',409:'CONFLICT',400:'VALIDATION_ERROR',403:'FORBIDDEN'}[http]})
        return JSONResponse(status_code=http,content=jsonable_encoder(corpo))
    if esito is None:return Response(status_code=204)
    return JSONResponse(status_code=status,content=jsonable_encoder(esito))
@router.get('/buildings')
def list_buildings(search:str|None=None,city:str|None=None,microzone:str|None=None,sort:str=Query('recent',pattern='^(recent|address)$'),limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0),ctx:OperatorContext=Depends(legacy_basic_agency_context)):return trc(census.list_buildings,ctx,search=search,city=city,microzone=microzone,sort=sort,limit=limit,offset=offset)
@router.post('/buildings',status_code=201)
def create_building(p:BuildingCreate,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return trc(census.create_building,ctx,p,status=201)
# CESTINO-EDIFICI-1: il Cestino degli edifici (palazzine contenitore), stesso
# impianto del Cestino Immobili; stessa traduzione `trc` ({detail, code, ...}).
@router.get('/buildings/{building_id}/deletion-check')
def building_deletion_check(building_id:int,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return trc(building_lifecycle.deletion_check,ctx,building_id)
# ELIMINAZIONE-EDIFICIO-1: nessun operatore sposta da solo un edificio nel Cestino ne' lo
# ripristina. Si RICHIEDE l'eliminazione (sotto); approvazione, ripristino e cancellazione
# definitiva sono del Super Admin, sulla superficie Platform (platform_admin/router.py).
@router.post('/buildings/{building_id}/trash')
def trash_building(building_id:int,p:PropertyTrash,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return trc(building_deletion.direct_trash_refused,ctx,building_id)
@router.post('/buildings/{building_id}/restore')
def restore_building(building_id:int,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return trc(building_deletion.direct_restore_refused,ctx,building_id)
@router.get('/buildings/{building_id}/deletion-request')
def building_deletion_request_status(building_id:int,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return trc(building_deletion.request_status,ctx,building_id)
@router.post('/buildings/{building_id}/deletion-request',status_code=201)
def create_building_deletion_request(building_id:int,p:BuildingDeletionRequestCreate,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return trc(building_deletion.create_request,ctx,building_id,p.reason_code,p.note,status=201)
@router.post('/building-deletion-requests/{request_id}/withdraw')
def withdraw_building_deletion_request(request_id:int,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return trc(building_deletion.withdraw_request,ctx,request_id)
@router.get('/trash/building-groups')
def list_building_trash_groups(limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0),ctx:OperatorContext=Depends(legacy_basic_agency_context)):return trc(building_deletion.list_trash_groups,ctx,limit=limit,offset=offset)
@router.get('/trash/buildings')
def list_building_trash(limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0),ctx:OperatorContext=Depends(legacy_basic_agency_context)):return trc(building_lifecycle.list_trash,ctx,limit=limit,offset=offset)
# STIMA360 TERRITORIO T4: la posizione VERIFICATA dell'edificio. Lettura dello stato e conferma
# esplicita (coordinate + verifica, operatore e data dal server); stesso gate e stessa traduzione.
@router.get('/buildings/{building_id}/position')
def get_building_position(building_id:int,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return trc(building_position.position_info,ctx,building_id)
@router.post('/buildings/{building_id}/position')
def confirm_building_position(building_id:int,p:PositionConfirm,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return trc(building_position.confirm_position,ctx,building_id,p)
@router.get('/buildings/{building_id}')
def get_building(building_id:int,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return trc(lambda *a:building_deletion.scheda_tenant(census.get_building(*a)),ctx,building_id)
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
# CENSIMENTO-2: composizione dell'edificio, generazione delle unita' mancanti
# (anteprima/conferma, una transazione), annullamento del lotto, modifica di
# gruppo (property/composition.py). Stesso contesto e stessa traduzione `trc`.
@router.get('/buildings/{building_id}/composition')
def get_building_composition(building_id:int,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return trc(composition.get_composition,ctx,building_id)
@router.put('/buildings/{building_id}/composition')
def set_building_composition(building_id:int,p:BuildingComposition,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return trc(composition.set_composition,ctx,building_id,p)
@router.post('/buildings/{building_id}/units/generate')
def generate_building_units(building_id:int,p:UnitsGenerate,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return trc(composition.generate_units,ctx,building_id,p)
@router.post('/buildings/{building_id}/units/generate/{batch_key}/undo')
def undo_building_units_batch(building_id:int,batch_key:str,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return trc(composition.undo_batch,ctx,building_id,batch_key)
# CENSIMENTO-2 Fase A (A2/A3): «Dati catastali e superficie» della singola scheda in
# UNA transazione con lo storico (identita' catastale, superficie, categoria,
# provenienze, fonte della verifica, interno e sua conferma).
@router.patch('/properties/{property_id}/census-data')
def update_property_census_data(property_id:int,p:UnitCensusData,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return trc(composition.update_unit_data,ctx,property_id,p)
@router.post('/buildings/{building_id}/units/bulk-update')
def bulk_update_building_units(building_id:int,p:UnitsBulkUpdate,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return trc(composition.bulk_update,ctx,building_id,p)
# CATALOGO-CANONICO-1: provenienza dal sito stima360.it (property/site_sync.py).
# Lettura con la visibilita' della scheda; scritture con l'accesso di gestione
# dell'immobile (lifecycle.require_manage), nella stessa agenzia.
@router.get('/properties/{property_id}/site-sources')
def list_site_sources(property_id:int,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return trc(site_sync.list_sources,ctx,property_id)
@router.post('/properties/{property_id}/site-sources/{source_id}/conflicts')
def resolve_site_conflict(property_id:int,source_id:int,p:SiteConflictResolve,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return trc(_resolve_site_conflict_guarded,ctx,property_id,source_id,p)
# CENSIMENTO-2 Fase A (A3, D2): «Applica» del sito non scavalca una superficie verificata (stesso lock).
def _resolve_site_conflict_guarded(ctx,property_id,source_id,p):
    return composition.resolve_site_conflict_guarded(ctx,property_id,source_id,p,lambda:site_sync.resolve_conflict(ctx,property_id,source_id,p))
@router.post('/properties/{property_id}/site-sources/{source_id}/duplicates/{other_id}/dismiss')
def dismiss_site_duplicate(property_id:int,source_id:int,other_id:int,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return trc(site_sync.dismiss_duplicate,ctx,property_id,source_id,other_id)
@router.post('/properties/{property_id}/site-sources/{source_id}/relink')
def relink_site_source(property_id:int,source_id:int,p:SiteRelink,ctx:OperatorContext=Depends(legacy_basic_agency_context)):return trc(site_sync.relink,ctx,property_id,source_id,p)
