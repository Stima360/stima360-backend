"""Admin-protected read and initialization routes for Property Watch."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from operator_auth.context import OperatorContext
from operator_auth.dependencies import legacy_basic_agency_context
from operator_auth.exceptions import PlatformAdminAgencyRequired

from . import service
from . import invisible_sale_service
from .exceptions import StimaNotFoundError, ValidationError, WatchNotFoundError
from .schemas import (
    PropertyWatchBuyerPressureBatchRefresh,
    PropertyWatchBuyerPressureRefresh,
    PropertyWatchInternalSignalsBatchRefresh,
    PropertyWatchInternalSignalsRefresh,
    InvisibleSaleBatchRefresh,
    InvisibleSaleOutcome,
    InvisibleSaleState,
)


router = APIRouter(prefix="/api/property-watch", tags=["property-watch"])

# P26-6B: every route resolves its agency once, server-side, and passes that
# same scope down. The two batch routes are the ones that mattered most - they
# used to sweep every active watch in the database - and are now one bounded
# cycle over the caller's own tenant.
#
# The dependency is written out on every route rather than hoisted into a
# module-level alias. P26-4 shipped exactly that alias in match/router.py and
# it made all 26 routes read as unscoped to the AST prover that certifies this
# surface, because the prover matches `Depends(legacy_basic_agency_context)` in
# the parameter default and an alias is just a Name. Repetition here is what
# keeps the proof mechanical.


@router.get("/stime/{stima_id}")
def get_watch_state(
    stima_id: int,
    ctx: OperatorContext = Depends(legacy_basic_agency_context),
):
    try:
        return service.get_current_watch_state_scoped(ctx, stima_id)
    except PlatformAdminAgencyRequired as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except WatchNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/stime/{stima_id}/initialize")
def initialize_watch(
    stima_id: int,
    ctx: OperatorContext = Depends(legacy_basic_agency_context),
):
    try:
        return service.ensure_watch_for_stima_scoped(ctx, stima_id)
    except PlatformAdminAgencyRequired as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (ValidationError, StimaNotFoundError) as exc:
        raise HTTPException(status_code=404 if isinstance(exc, StimaNotFoundError) else 400, detail=str(exc)) from exc


@router.post(
    "/stime/{stima_id}/internal-signals/refresh",
    response_model=PropertyWatchInternalSignalsRefresh,
)
def refresh_internal_signals(
    stima_id: int,
    ctx: OperatorContext = Depends(legacy_basic_agency_context),
):
    try:
        return service.collect_internal_signals_for_stima_scoped(ctx, stima_id)
    except PlatformAdminAgencyRequired as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except WatchNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/internal-signals/refresh-active",
    response_model=PropertyWatchInternalSignalsBatchRefresh,
)
def refresh_active_internal_signals(
    ctx: OperatorContext = Depends(legacy_basic_agency_context),
):
    return service.collect_internal_signals_for_active_watches_scoped(ctx)


@router.post(
    "/stime/{stima_id}/buyer-pressure/refresh",
    response_model=PropertyWatchBuyerPressureRefresh,
)
def refresh_buyer_pressure(
    stima_id: int,
    ctx: OperatorContext = Depends(legacy_basic_agency_context),
):
    try:
        return service.safe_collect_buyer_pressure_for_stima_scoped(ctx, stima_id)
    except PlatformAdminAgencyRequired as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except WatchNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/buyer-pressure/refresh-active",
    response_model=PropertyWatchBuyerPressureBatchRefresh,
)
def refresh_active_buyer_pressure(
    ctx: OperatorContext = Depends(legacy_basic_agency_context),
):
    return service.collect_buyer_pressure_for_active_watches_scoped(ctx)


def _p22_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, PlatformAdminAgencyRequired):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, LookupError):
        return HTTPException(status_code=404, detail="Invisible Sale non disponibile")
    if isinstance(exc, RuntimeError):
        return HTTPException(status_code=409, detail="Operazione Invisible Sale non consentita")
    return HTTPException(status_code=400, detail="Identificativo non valido")


@router.get("/stime/{stima_id}/invisible-sale", response_model=InvisibleSaleState)
def get_invisible_sale(
    stima_id: int,
    ctx: OperatorContext = Depends(legacy_basic_agency_context),
):
    try:
        return invisible_sale_service.get_invisible_sale_for_stima_scoped(ctx, stima_id)
    except (ValueError, LookupError, RuntimeError, PlatformAdminAgencyRequired) as exc:
        raise _p22_http_error(exc) from exc


@router.post("/stime/{stima_id}/invisible-sale/refresh", response_model=InvisibleSaleOutcome)
def refresh_invisible_sale(
    stima_id: int,
    ctx: OperatorContext = Depends(legacy_basic_agency_context),
):
    try:
        return invisible_sale_service.safe_collect_invisible_sale_for_stima_scoped(ctx, stima_id)
    except (ValueError, LookupError, RuntimeError, PlatformAdminAgencyRequired) as exc:
        raise _p22_http_error(exc) from exc


@router.post("/invisible-sale/refresh-active", response_model=InvisibleSaleBatchRefresh)
def refresh_active_invisible_sale(
    ctx: OperatorContext = Depends(legacy_basic_agency_context),
):
    return invisible_sale_service.collect_invisible_sale_for_active_watches_scoped(ctx)


@router.post("/stime/{stima_id}/invisible-sale/candidates/{buy_request_id}/approve")
def approve_invisible_sale(
    stima_id: int,
    buy_request_id: int,
    ctx: OperatorContext = Depends(legacy_basic_agency_context),
):
    try:
        return invisible_sale_service.approve_invisible_sale_candidate_scoped(ctx, stima_id, buy_request_id)
    except (ValueError, LookupError, RuntimeError, PlatformAdminAgencyRequired) as exc:
        raise _p22_http_error(exc) from exc


@router.post("/stime/{stima_id}/invisible-sale/candidates/{buy_request_id}/reject")
def reject_invisible_sale(
    stima_id: int,
    buy_request_id: int,
    ctx: OperatorContext = Depends(legacy_basic_agency_context),
):
    try:
        return invisible_sale_service.reject_invisible_sale_candidate_scoped(ctx, stima_id, buy_request_id)
    except (ValueError, LookupError, RuntimeError, PlatformAdminAgencyRequired) as exc:
        raise _p22_http_error(exc) from exc


@router.post("/stime/{stima_id}/invisible-sale/close")
def close_invisible_sale(
    stima_id: int,
    ctx: OperatorContext = Depends(legacy_basic_agency_context),
):
    try:
        return invisible_sale_service.close_invisible_sale_scoped(ctx, stima_id)
    except (ValueError, LookupError, RuntimeError, PlatformAdminAgencyRequired) as exc:
        raise _p22_http_error(exc) from exc
