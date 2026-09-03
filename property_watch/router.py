"""Admin-protected read and initialization routes for Property Watch."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from . import service
from .exceptions import StimaNotFoundError, ValidationError, WatchNotFoundError
from .schemas import (
    PropertyWatchBuyerPressureBatchRefresh,
    PropertyWatchBuyerPressureRefresh,
    PropertyWatchInternalSignalsBatchRefresh,
    PropertyWatchInternalSignalsRefresh,
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
