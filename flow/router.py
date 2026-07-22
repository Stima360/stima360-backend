from fastapi import APIRouter, HTTPException, Query, Response
from core.exceptions import NotFoundError, ConflictError, ValidationError
from . import service
from .schemas import *
from .enums import SCAN_DEFAULT_LIMIT, SCAN_MAX_LIMIT

router=APIRouter(prefix="/api/flow",tags=["flow"])

def tr(fn,*a,**kw):
    try: return fn(*a,**kw)
    except NotFoundError as e: raise HTTPException(404,str(e))
    except ConflictError as e: raise HTTPException(409,str(e))
    except ValidationError as e: raise HTTPException(400,str(e))
    except ValueError as e: raise HTTPException(422,str(e))

@router.post("/sync-rules")
def sync_rules(): return {"items":tr(service.sync_rules)}
@router.get("/rules")
def rules(): return {"items":tr(service.list_rules)}
@router.get("/rules/{code}")
def rule(code:str): return tr(service.get_rule_row,code)
@router.patch("/rules/{code}/parameters")
def parameters(code:str,payload:RuleParametersUpdate): return tr(service.update_parameters,code,payload)
@router.post("/rules/{code}/reset-parameters")
def reset(code:str): return tr(service.reset_parameters,code)
@router.post("/rules/{code}/simulate")
def simulate(code:str,payload:SimulationRequest): return tr(service.simulate,code,payload)
@router.post("/rules/{code}/activate")
def activate(code:str,payload:ActivationRequest): return tr(service.activate,code,payload)
@router.post("/rules/{code}/deactivate")
def deactivate(code:str): return tr(service.deactivate,code)

@router.post("/events",status_code=201)
def event(payload:EventCreate): return tr(service.process_event,payload)
@router.get("/events")
def events(limit:int=Query(100,ge=1,le=500),offset:int=Query(0,ge=0),status:str|None=None): return {"items":tr(service.list_events,limit,offset,status)}

@router.post("/evaluate")
def evaluate(payload:EvaluateRequest): return tr(service.evaluate,payload)
@router.post("/simulate")
def simulate_generic(payload:EvaluateRequest):
    data=payload.model_copy(update={'mode':'simulation'}) if hasattr(payload,'model_copy') else payload.copy(update={'mode':'simulation'})
    return tr(service.evaluate,data)
@router.post("/scan")
def scan(payload:ScanRequest): return tr(service.scan,payload)

@router.get("/executions")
def executions(limit:int=Query(100,ge=1,le=500),offset:int=Query(0,ge=0),status:str|None=None): return {"items":tr(service.list_executions,limit,offset,status)}
@router.get("/executions/{execution_id}")
def execution(execution_id:int): return tr(service.get_execution,execution_id)
@router.post("/executions/{execution_id}/retry")
def retry(execution_id:int,payload:RetryRequest): return tr(service.retry,execution_id,payload)

@router.post("/suppressions",status_code=201)
def suppression(payload:SuppressionCreate): return tr(service.add_suppression,payload)
@router.get("/suppressions")
def suppressions(): return {"items":tr(service.list_suppressions)}
@router.delete("/suppressions/{suppression_id}",status_code=204)
def delete_suppression(suppression_id:int): tr(service.delete_suppression,suppression_id); return Response(status_code=204)

@router.get("/dashboard")
def dashboard(): return tr(service.dashboard)
@router.get("/dashboard/errors")
def errors(limit:int=Query(100,ge=1,le=500)): return {"items":tr(service.list_executions,limit,0,"failed")}
@router.get("/dashboard/recent")
def recent(limit:int=Query(100,ge=1,le=500)): return {"items":tr(service.list_executions,limit,0,None)}
