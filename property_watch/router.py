"""Admin-protected read and initialization routes for Property Watch."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from . import service
from .exceptions import StimaNotFoundError, ValidationError, WatchNotFoundError


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
