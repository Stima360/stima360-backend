"""FastAPI router for the P17 Seller Intelligence module.

Not registered anywhere in P17-A. Mirrors core/router.py's convention: the
router itself carries no auth dependency - that is applied at mount time
(``app.include_router(seller_intelligence_router, dependencies=[Depends(require_admin)])``),
exactly like every other admin-protected domain router in this repository.
Wiring that mount into main.py is explicitly out of scope for P17-A (see
tests/test_seller_intelligence_router.py, which proves the router works
correctly when mounted with that same dependency on a throwaway FastAPI app,
without touching main.py at all).

Does not replace or alter /api/core/activities in any way.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from . import service
from .exceptions import ValidationError
from .schemas import SellerTimelineEventCreate

router = APIRouter(prefix="/api/seller-intelligence", tags=["seller-intelligence"])


def _translate(callable_, *args, **kwargs):
    try:
        return callable_(*args, **kwargs)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/events", status_code=201)
def create_event(payload: SellerTimelineEventCreate):
    return _translate(service.record_event, **payload.dict())


@router.get("/timeline")
def get_timeline(
    contact_id: int | None = None,
    lead_id: int | None = None,
    stima_id: int | None = None,
    property_id: int | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    items = _translate(
        service.list_timeline,
        contact_id=contact_id,
        lead_id=lead_id,
        stima_id=stima_id,
        property_id=property_id,
        limit=limit,
        offset=offset,
    )
    return {"items": items}
