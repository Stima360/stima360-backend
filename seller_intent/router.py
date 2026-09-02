from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .exceptions import NotFoundError
from .schemas import SellerIntentScoreResponse
from .service import get_seller_intent_score

router = APIRouter(prefix="/api/seller-intent", tags=["seller-intent"])


@router.get("/leads/{lead_id}/score", response_model=SellerIntentScoreResponse)
def get_lead_score(lead_id: int):
    try:
        return get_seller_intent_score(lead_id=lead_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

