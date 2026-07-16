from fastapi import APIRouter, HTTPException, Query, Response
from core.exceptions import NotFoundError, ConflictError, ValidationError
from . import service
from .schemas import *

router = APIRouter(prefix="/api/match", tags=["match"])

def tr(fn, *args, **kwargs):
    try: return fn(*args, **kwargs)
    except NotFoundError as exc: raise HTTPException(404, str(exc))
    except ConflictError as exc: raise HTTPException(409, str(exc))
    except ValidationError as exc: raise HTTPException(400, str(exc))
    except ValueError as exc: raise HTTPException(422, str(exc))

@router.get("/dashboard")
def dashboard(): return tr(service.dashboard)

@router.post("/calculate", status_code=201)
def calculate_pair(payload: SingleMatchRequest): return tr(service.calculate_pair, payload)

@router.post("/buy-requests/{request_id}/calculate", status_code=201)
def calculate_for_buy(request_id: int, payload: BatchMatchRequest): return tr(service.calculate_for_buy, request_id, payload)

@router.post("/properties/{property_id}/calculate", status_code=201)
def calculate_for_property(property_id: int, payload: BatchMatchRequest): return tr(service.calculate_for_property, property_id, payload)

@router.get("/matches")
def list_matches(limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0), buy_request_id: int | None = None, property_id: int | None = None, match_class: str | None = None, commercial_status: str | None = None, compatible_only: bool = False):
    return {"items": tr(service.list_matches, limit, offset, buy_request_id, property_id, match_class, commercial_status, compatible_only)}

@router.get("/matches/{match_id}")
def get_match(match_id: int): return tr(service.get_match, match_id)

@router.patch("/matches/{match_id}")
def update_match(match_id: int, payload: MatchUpdate): return tr(service.update_match, match_id, payload)

@router.post("/matches/{match_id}/override")
def set_override(match_id: int, payload: OverrideRequest): return tr(service.set_override, match_id, payload)

@router.delete("/matches/{match_id}/override")
def clear_override(match_id: int): return tr(service.clear_override, match_id)

@router.post("/exclusions", status_code=201)
def add_exclusion(payload: ExclusionCreate): return tr(service.add_exclusion, payload)

@router.get("/exclusions")
def list_exclusions(): return {"items": tr(service.list_exclusions)}

@router.delete("/exclusions/{exclusion_id}", status_code=204)
def delete_exclusion(exclusion_id: int): tr(service.delete_exclusion, exclusion_id); return Response(status_code=204)
