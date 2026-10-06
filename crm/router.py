"""HTTP adapter for the CRM 360 view.

`get_contact_360` reads four CORE tables through `core.service`, and since
P26-1 every one of those reads requires an agency scope. This router is where
that scope enters.

It is mounted behind legacy Basic (`main.py`), and P26-1's D-1 allowlist admits
operator sessions only on `/api/operator-auth/*` and `/api/core/*` - so there is
no session here to derive a scope from. The scope therefore comes from the same
C2 compatibility dependency Next Best Action uses: the Default Agency, resolved
server-side from its slug, presented as an agency-*bound* `OperatorContext`.

Never a `SystemAgencyContext` - that type is for server-originated work with no
principal, and a CRM request has a human behind it. Never an agency from the
request. Never a widened or platform scope.

CRM remains a legacy-Basic compatibility surface under GATE-MA1: it is
Default-Agency-bound on this channel, and its non-CORE reads (properties, buy
requests, matches, visits) stay unscoped until those modules are migrated.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from core.exceptions import ConflictError, NotFoundError, PermissionDenied, ValidationError
from operator_auth.context import OperatorContext
from operator_auth.dependencies import legacy_basic_agency_context
from operator_auth.exceptions import PlatformAdminAgencyRequired

from . import sellers, service
from .schemas import Contact360Response, SellerActivate, SellerDeactivate

router = APIRouter(prefix="/api/crm", tags=["crm"])


@router.get("/contacts/{contact_id}/360", response_model=Contact360Response)
def get_contact_360(
    contact_id: int,
    ctx: OperatorContext = Depends(legacy_basic_agency_context),
):
    try:
        return service.get_contact_360(ctx, contact_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# VENDITORI-1: la worklist Venditori e l'interruttore «Vende» (crm/sellers.py).
# Stesso `legacy_basic_agency_context` della vista 360; gli errori portano un
# `code` che la UI legge (`{detail, code, ...}`), come il censimento.
# ---------------------------------------------------------------------------

def _venditori(fn, *a, **k):
    http = 200
    try:
        esito = fn(*a, **k)
    except sellers.SellerError as exc:
        return JSONResponse(status_code=409, content=jsonable_encoder(
            {**exc.extra, "detail": str(exc), "code": exc.code}))
    except NotFoundError as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc), "code": "NOT_FOUND"})
    except ValidationError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc), "code": "VALIDATION_ERROR"})
    except PermissionDenied as exc:
        return JSONResponse(status_code=403, content={"detail": str(exc), "code": "FORBIDDEN"})
    except PlatformAdminAgencyRequired as exc:
        # DELETE-ARCH Fase 1B: platform admin fuori acting -> 403 (prima 500).
        return JSONResponse(status_code=403, content={"detail": str(exc), "code": "AGENCY_REQUIRED"})
    except ConflictError as exc:
        # DELETE-ARCH 2B2: un conflitto con codice proprio (PROPERTY_IN_TRASH) lo porta.
        return JSONResponse(status_code=409, content={"detail": str(exc), "code": getattr(exc, "code", None) or "CONFLICT"})
    if isinstance(esito, tuple):
        esito, creato = esito
        http = 201 if creato else 200
    return JSONResponse(status_code=http, content=jsonable_encoder(esito))


@router.get("/sellers")
def list_sellers(
    view: str = Query("all", pattern="^(all|new|contacted|qualified|ready|overdue|no_action)$"),
    status: str = Query("active", pattern="^(active|paused|closed|all|mistakes)$"),
    agent_id: int | None = Query(None, ge=0),
    city: str | None = Query(None, max_length=120),
    search: str | None = Query(None, max_length=200),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: OperatorContext = Depends(legacy_basic_agency_context),
):
    return _venditori(sellers.list_sellers, ctx, view=view, status=status, agent_id=agent_id,
                      city=city, search=search, limit=limit, offset=offset)


@router.post("/sellers")
def activate_seller(payload: SellerActivate, ctx: OperatorContext = Depends(legacy_basic_agency_context)):
    if payload.lead_id is not None and payload.new_lead:
        return JSONResponse(status_code=422, content={"detail": "lead_id e new_lead si escludono", "code": "VALIDATION_ERROR"})
    return _venditori(sellers.activate, ctx, payload)


@router.post("/sellers/deactivate")
def deactivate_seller(payload: SellerDeactivate, ctx: OperatorContext = Depends(legacy_basic_agency_context)):
    return _venditori(sellers.deactivate, ctx, payload)
