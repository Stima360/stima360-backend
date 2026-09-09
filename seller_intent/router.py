from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from operator_auth.context import OperatorContext
from operator_auth.dependencies import legacy_basic_agency_context
from operator_auth.exceptions import PlatformAdminAgencyRequired

from .exceptions import NotFoundError
from .schemas import SellerIntentScoreResponse
from .service import get_seller_intent_score_scoped

router = APIRouter(prefix="/api/seller-intent", tags=["seller-intent"])


@router.get("/leads/{lead_id}/score", response_model=SellerIntentScoreResponse)
def get_lead_score(
    lead_id: int,
    ctx: OperatorContext = Depends(legacy_basic_agency_context),
):
    try:
        return get_seller_intent_score_scoped(ctx, lead_id=lead_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PlatformAdminAgencyRequired as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
