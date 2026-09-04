"""Admin-protected read and initialization routes for Property Watch."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

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


@router.get("/stime/{stima_id}")
def get_watch_state(stima_id: int):
    try:
        return service.get_current_watch_state(stima_id)
    except WatchNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/stime/{stima_id}/initialize")
def initialize_watch(stima_id: int):
    try:
        return service.ensure_watch_for_stima(stima_id)
    except (ValidationError, StimaNotFoundError) as exc:
        raise HTTPException(status_code=404 if isinstance(exc, StimaNotFoundError) else 400, detail=str(exc)) from exc


@router.post(
    "/stime/{stima_id}/internal-signals/refresh",
    response_model=PropertyWatchInternalSignalsRefresh,
)
def refresh_internal_signals(stima_id: int):
    try:
        return service.safe_collect_internal_signals_for_stima(stima_id)
    except WatchNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/internal-signals/refresh-active",
    response_model=PropertyWatchInternalSignalsBatchRefresh,
)
def refresh_active_internal_signals():
    return service.collect_internal_signals_for_active_watches()


@router.post(
    "/stime/{stima_id}/buyer-pressure/refresh",
    response_model=PropertyWatchBuyerPressureRefresh,
)
def refresh_buyer_pressure(stima_id: int):
    try:
        return service.safe_collect_buyer_pressure_for_stima(stima_id)
    except WatchNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/buyer-pressure/refresh-active",
    response_model=PropertyWatchBuyerPressureBatchRefresh,
)
def refresh_active_buyer_pressure():
    return service.collect_buyer_pressure_for_active_watches()


def _p22_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, LookupError):
        return HTTPException(status_code=404, detail="Invisible Sale non disponibile")
    if isinstance(exc, RuntimeError):
        return HTTPException(status_code=409, detail="Operazione Invisible Sale non consentita")
    return HTTPException(status_code=400, detail="Identificativo non valido")


@router.get("/stime/{stima_id}/invisible-sale", response_model=InvisibleSaleState)
def get_invisible_sale(stima_id: int):
    try:
        return invisible_sale_service.get_invisible_sale_for_stima(stima_id)
    except (ValueError, LookupError, RuntimeError) as exc:
        raise _p22_http_error(exc) from exc


@router.post("/stime/{stima_id}/invisible-sale/refresh", response_model=InvisibleSaleOutcome)
def refresh_invisible_sale(stima_id: int):
    try:
        return invisible_sale_service.safe_collect_invisible_sale_for_stima(stima_id)
    except (ValueError, LookupError, RuntimeError) as exc:
        raise _p22_http_error(exc) from exc


@router.post("/invisible-sale/refresh-active", response_model=InvisibleSaleBatchRefresh)
def refresh_active_invisible_sale():
    return invisible_sale_service.collect_invisible_sale_for_active_watches()


@router.post("/stime/{stima_id}/invisible-sale/candidates/{buy_request_id}/approve")
def approve_invisible_sale(stima_id: int, buy_request_id: int):
    try:
        return invisible_sale_service.approve_invisible_sale_candidate(stima_id, buy_request_id)
    except (ValueError, LookupError, RuntimeError) as exc:
        raise _p22_http_error(exc) from exc


@router.post("/stime/{stima_id}/invisible-sale/candidates/{buy_request_id}/reject")
def reject_invisible_sale(stima_id: int, buy_request_id: int):
    try:
        return invisible_sale_service.reject_invisible_sale_candidate(stima_id, buy_request_id)
    except (ValueError, LookupError, RuntimeError) as exc:
        raise _p22_http_error(exc) from exc


@router.post("/stime/{stima_id}/invisible-sale/close")
def close_invisible_sale(stima_id: int):
    try:
        return invisible_sale_service.close_invisible_sale(stima_id)
    except (ValueError, LookupError, RuntimeError) as exc:
        raise _p22_http_error(exc) from exc
