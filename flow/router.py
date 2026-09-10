"""HTTP adapter for FLOW.

P26-6C: every route that touches tenant data resolves an agency server-side and
works inside it. Before this the scan, evaluate, simulate, event, execution,
suppression and dashboard routes all reached the global surface, so one
Basic-authenticated caller could read, simulate and *execute live automation
against* every agency's leads, properties, buy requests, matches, visits and
owner requests.

TWO KINDS OF ROUTE, AND THE LINE BETWEEN THEM

The rule registry - `flow_rules` - is a platform-global template: one
catalogue, the same for every agency, with no tenant to scope by. Reading it,
editing its parameters, activating and deactivating it is platform
configuration, not tenant data. Those seven routes stay as they are, and they
are named in `PLATFORM_CONFIG_ROUTES` so that "unscoped" is a claim this module
makes deliberately rather than one it forgets to make.

Everything else - events, executions, retries, suppressions, the dashboard
counters, and every scan or simulation that reads a business entity - is tenant
data and takes `legacy_basic_agency_context`, written out inline on each route.

WHY INLINE AND NOT A MODULE-LEVEL ALIAS

P26-4 shipped `CTX = Depends(...)` in match/router.py and it made all 26 routes
read as unscoped to the AST prover that certifies these surfaces: the prover
matches `Depends(legacy_basic_agency_context)` in the parameter default, and an
alias is just a Name. Repetition here is what keeps the proof mechanical.

WHERE THE SCOPE COMES FROM

Until P26-3 this router borrowed OWNER Admin's HTTP Basic guard for its mount,
which meant there was no operator session to derive a scope from and every
route fell back to the Default Agency. The OS Shell now calls /api/flow with an
operator session cookie, so the mount takes `require_authenticated_operator`
instead: the same legacy credential, verified the same way, plus the cookie.

Each route still takes the shared scope dependency, which since P26-3 prefers a
live session and falls back to the Default Agency for legacy Basic. Either way
the agency is decided server-side - never from the request; no route here takes
one and no schema carries one.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from core.exceptions import NotFoundError, ConflictError, ValidationError
from operator_auth.context import OperatorContext
from operator_auth.dependencies import legacy_basic_agency_context
from operator_auth.exceptions import PlatformAdminAgencyRequired
from . import service
from .schemas import *
from .enums import SCAN_DEFAULT_LIMIT, SCAN_MAX_LIMIT
from operator_auth.dependencies import require_authenticated_operator

# P26-3: the mount is the operator one now.
#
# The OS Shell calls /api/flow/rules, /dashboard and /executions, and it now
# authenticates with the operator session cookie - which OWNER Admin's guard,
# being HTTP Basic and nothing else, cannot accept.
# `require_authenticated_operator` verifies the SAME legacy credential through
# the same admin_security.require_admin, so
# every existing Basic caller is unaffected; what it adds is the cookie.
#
# It also removes FLOW's import of an OWNER symbol. The coupling was historical
# - FLOW needed a self-authenticating mount and borrowed the nearest one - and
# it meant a change to OWNER Admin's authentication silently changed FLOW's.
router=APIRouter(prefix="/api/flow",tags=["flow"],dependencies=[Depends(require_authenticated_operator)])

# The rule registry is one catalogue for the whole platform. These handlers
# read and write it and touch no tenant row. The reconnaissance test asserts
# this tuple against the router's own AST, so it cannot silently grow.
PLATFORM_CONFIG_ROUTES = (
    "sync_rules", "rules", "rule", "parameters", "reset", "activate", "deactivate",
)

def tr(fn,*a,**kw):
    try: return fn(*a,**kw)
    except NotFoundError as e: raise HTTPException(404,str(e))
    except ConflictError as e: raise HTTPException(409,str(e))
    except ValidationError as e: raise HTTPException(400,str(e))
    # An unbound platform admin is refused, not served, and not a 500. It
    # cannot fire on this channel - the compatibility dependency always
    # resolves the Default Agency - but these are the routes an operator-session
    # mount would reach first, and the other intelligence routers answer the
    # identical condition the same way.
    except PlatformAdminAgencyRequired as e: raise HTTPException(403,str(e))
    except ValueError as e: raise HTTPException(422,str(e))

# ---------------------------------------------------------------------------
# Rule registry - platform-global template, no tenant.
# ---------------------------------------------------------------------------
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
@router.post("/rules/{code}/activate")
def activate(code:str,payload:ActivationRequest): return tr(service.activate,code,payload)
@router.post("/rules/{code}/deactivate")
def deactivate(code:str): return tr(service.deactivate,code)

# ---------------------------------------------------------------------------
# Tenant data. Every route below carries the agency context.
# ---------------------------------------------------------------------------
@router.post("/rules/{code}/simulate")
def simulate(code:str,payload:SimulationRequest,
             ctx:OperatorContext=Depends(legacy_basic_agency_context)):
    return tr(service.simulate_for_agency,ctx.require_agency(),code,payload)

@router.post("/events",status_code=201)
def event(payload:EventCreate,
          ctx:OperatorContext=Depends(legacy_basic_agency_context)):
    return tr(service.process_event_for_agency,ctx.require_agency(),payload)

@router.post("/events/recover")
def recover_events(payload:EventRecoveryRequest,
                   ctx:OperatorContext=Depends(legacy_basic_agency_context)):
    return tr(service.recover_received_events_for_agency,ctx.require_agency(),payload.limit)

@router.get("/events")
def events(limit:int=Query(100,ge=1,le=500),offset:int=Query(0,ge=0),status:str|None=None,
           ctx:OperatorContext=Depends(legacy_basic_agency_context)):
    return {"items":tr(service.list_events_for_agency,ctx.require_agency(),limit,offset,status)}

@router.post("/evaluate")
def evaluate(payload:EvaluateRequest,
             ctx:OperatorContext=Depends(legacy_basic_agency_context)):
    return tr(service.evaluate_for_agency,ctx.require_agency(),payload)

@router.post("/simulate")
def simulate_generic(payload:EvaluateRequest,
                     ctx:OperatorContext=Depends(legacy_basic_agency_context)):
    data=payload.model_copy(update={'mode':'simulation'}) if hasattr(payload,'model_copy') else payload.copy(update={'mode':'simulation'})
    return tr(service.evaluate_for_agency,ctx.require_agency(),data)

@router.post("/scan")
def scan(payload:ScanRequest,
         ctx:OperatorContext=Depends(legacy_basic_agency_context)):
    """One agency's scan. There is no HTTP route that scans the platform.

    A background run that needs every tenant calls
    `service.scan_for_all_agencies`, which loops and runs this same bounded
    cycle once per agency - see run_flow_p2b_cron.py.
    """
    return tr(service.scan_for_agency,ctx.require_agency(),payload)

@router.get("/executions")
def executions(limit:int=Query(100,ge=1,le=500),offset:int=Query(0,ge=0),status:str|None=None,
               ctx:OperatorContext=Depends(legacy_basic_agency_context)):
    return {"items":tr(service.list_executions_for_agency,ctx.require_agency(),limit,offset,status)}

@router.get("/executions/{execution_id}")
def execution(execution_id:int,
              ctx:OperatorContext=Depends(legacy_basic_agency_context)):
    return tr(service.get_execution_for_agency,ctx.require_agency(),execution_id)

@router.post("/executions/{execution_id}/retry")
def retry(execution_id:int,payload:RetryRequest,
          ctx:OperatorContext=Depends(legacy_basic_agency_context)):
    return tr(service.retry_for_agency,ctx.require_agency(),execution_id,payload)

@router.post("/suppressions",status_code=201)
def suppression(payload:SuppressionCreate,
                ctx:OperatorContext=Depends(legacy_basic_agency_context)):
    return tr(service.add_suppression_for_agency,ctx.require_agency(),payload)

@router.get("/suppressions")
def suppressions(ctx:OperatorContext=Depends(legacy_basic_agency_context)):
    return {"items":tr(service.list_suppressions_for_agency,ctx.require_agency())}

@router.delete("/suppressions/{suppression_id}",status_code=204)
def delete_suppression(suppression_id:int,
                       ctx:OperatorContext=Depends(legacy_basic_agency_context)):
    tr(service.delete_suppression_for_agency,ctx.require_agency(),suppression_id)
    return Response(status_code=204)

@router.get("/dashboard")
def dashboard(ctx:OperatorContext=Depends(legacy_basic_agency_context)):
    return tr(service.dashboard_for_agency,ctx.require_agency())

@router.get("/dashboard/errors")
def errors(limit:int=Query(100,ge=1,le=500),
           ctx:OperatorContext=Depends(legacy_basic_agency_context)):
    return {"items":tr(service.list_executions_for_agency,ctx.require_agency(),limit,0,"failed")}

@router.get("/dashboard/recent")
def recent(limit:int=Query(100,ge=1,le=500),
           ctx:OperatorContext=Depends(legacy_basic_agency_context)):
    return {"items":tr(service.list_executions_for_agency,ctx.require_agency(),limit,0,None)}
