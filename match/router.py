from fastapi import APIRouter, Depends, HTTPException, Query, Response
from core.exceptions import NotFoundError, ConflictError, ValidationError
from operator_auth.context import OperatorContext
from operator_auth.dependencies import legacy_basic_agency_context
from operator_auth.exceptions import PlatformAdminAgencyRequired
from . import service
from .schemas import *
from .enums import REFRESH_STALE_DEFAULT_LIMIT, REFRESH_STALE_MAX_LIMIT

router = APIRouter(prefix="/api/match", tags=["match"])


def tr(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except NotFoundError as exc:
        raise HTTPException(404, str(exc))
    except ConflictError as exc:
        raise HTTPException(409, str(exc))
    except ValidationError as exc:
        raise HTTPException(400, str(exc))
    except PlatformAdminAgencyRequired as exc:
        raise HTTPException(403, str(exc))
    except ValueError as exc:
        raise HTTPException(422, str(exc))


# P26-2E: every handler below resolves its agency once, server-side, through
# legacy_basic_agency_context, and passes that same object down. MATCH is
# CHILD-DERIVED, so the scope is used to require that BOTH roots of a pair - the
# buy request and the property - sit in the caller's agency. The client supplies
# a Basic credential and never an agency.
#
# The dependency is written out on every route rather than aliased to a module
# constant: the certified detector in tests/test_p26_1_legacy_basic_surface.py
# matches a route decorator to a `Depends(legacy_basic_agency_context)` default
# in the signature, and an alias would make 26 scoped routes look unscoped to
# the rule that decides whether MATCH counts as a GATE-MA1 residual.


@router.get("/dashboard")
def dashboard(ctx: OperatorContext = Depends(legacy_basic_agency_context)):
    return tr(service.dashboard_scoped, ctx)


@router.get("/dashboard/stale")
def dashboard_stale(limit: int = Query(100, ge=1, le=500), ctx: OperatorContext = Depends(legacy_basic_agency_context)):
    return {"items": tr(service.dashboard_stale_scoped, ctx, limit)}


@router.get("/dashboard/errors")
def dashboard_errors(limit: int = Query(100, ge=1, le=500), ctx: OperatorContext = Depends(legacy_basic_agency_context)):
    return {"items": tr(service.dashboard_errors_scoped, ctx, limit)}


@router.get("/dashboard/review")
def dashboard_review(limit: int = Query(100, ge=1, le=500), ctx: OperatorContext = Depends(legacy_basic_agency_context)):
    return {"items": tr(service.dashboard_review_scoped, ctx, limit)}


@router.get("/readiness")
def readiness(
    buy_request_id: int | None = Query(None, gt=0),
    property_id: int | None = Query(None, gt=0),
    ctx: OperatorContext = Depends(legacy_basic_agency_context),
):
    return tr(service.get_readiness_scoped, ctx, buy_request_id, property_id)


@router.post("/calculate", status_code=201)
def calculate_pair(payload: SingleMatchRequest, ctx: OperatorContext = Depends(legacy_basic_agency_context)):
    return tr(service.calculate_pair_scoped, ctx, payload)


@router.post("/buy-requests/{request_id}/calculate", status_code=201)
def calculate_for_buy(request_id: int, payload: BatchMatchRequest, ctx: OperatorContext = Depends(legacy_basic_agency_context)):
    return tr(service.calculate_for_buy_scoped, ctx, request_id, payload)


@router.post("/properties/{property_id}/calculate", status_code=201)
def calculate_for_property(property_id: int, payload: BatchMatchRequest, ctx: OperatorContext = Depends(legacy_basic_agency_context)):
    return tr(service.calculate_for_property_scoped, ctx, property_id, payload)


@router.post("/detect-stale")
def detect_stale(payload: DetectStaleRequest, ctx: OperatorContext = Depends(legacy_basic_agency_context)):
    return tr(service.detect_stale_scoped, ctx, payload)


@router.post("/matches/{match_id}/refresh")
def refresh_match(match_id: int, payload: RefreshRequest, ctx: OperatorContext = Depends(legacy_basic_agency_context)):
    return tr(service.refresh_match_scoped, ctx, match_id, payload)


@router.post("/buy-requests/{request_id}/refresh")
def refresh_for_buy(request_id: int, payload: RefreshRequest, ctx: OperatorContext = Depends(legacy_basic_agency_context)):
    return tr(service.refresh_for_buy_scoped, ctx, request_id, payload)


@router.post("/properties/{property_id}/refresh")
def refresh_for_property(property_id: int, payload: RefreshRequest, ctx: OperatorContext = Depends(legacy_basic_agency_context)):
    return tr(service.refresh_for_property_scoped, ctx, property_id, payload)


@router.post("/refresh/stale")
def refresh_stale(
    payload: RefreshRequest,
    limit: int = Query(REFRESH_STALE_DEFAULT_LIMIT, ge=1, le=REFRESH_STALE_MAX_LIMIT),
    ctx: OperatorContext = Depends(legacy_basic_agency_context),
):
    return tr(service.refresh_stale_scoped, ctx, limit, payload)


@router.get("/matches")
def list_matches(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    buy_request_id: int | None = None,
    property_id: int | None = None,
    match_class: str | None = None,
    commercial_status: str | None = None,
    compatible_only: bool = False,
    freshness_status: str | None = None,
    review_required: bool | None = None,
    ctx: OperatorContext = Depends(legacy_basic_agency_context),
):
    return {
        "items": tr(
            service.list_matches_scoped,
            ctx,
            limit,
            offset,
            buy_request_id,
            property_id,
            match_class,
            commercial_status,
            compatible_only,
            freshness_status,
            review_required,
        )
    }


@router.get("/matches/{match_id}")
def get_match(match_id: int, ctx: OperatorContext = Depends(legacy_basic_agency_context)):
    return tr(service.get_match_scoped, ctx, match_id)


@router.patch("/matches/{match_id}")
def update_match(match_id: int, payload: MatchUpdate, ctx: OperatorContext = Depends(legacy_basic_agency_context)):
    return tr(service.update_match_scoped, ctx, match_id, payload)


@router.post("/matches/{match_id}/override")
def set_override(match_id: int, payload: OverrideRequest, ctx: OperatorContext = Depends(legacy_basic_agency_context)):
    return tr(service.set_override_scoped, ctx, match_id, payload)


@router.delete("/matches/{match_id}/override")
def clear_override(match_id: int, ctx: OperatorContext = Depends(legacy_basic_agency_context)):
    return tr(service.clear_override_scoped, ctx, match_id)


@router.get("/matches/{match_id}/refresh-history")
def refresh_history(match_id: int, ctx: OperatorContext = Depends(legacy_basic_agency_context)):
    return {"items": tr(service.refresh_history_scoped, ctx, match_id)}


@router.get("/matches/{match_id}/timeline")
def timeline(match_id: int, ctx: OperatorContext = Depends(legacy_basic_agency_context)):
    return tr(service.timeline_scoped, ctx, match_id)


@router.post("/matches/{match_id}/feedback", status_code=201)
def add_feedback(match_id: int, payload: FeedbackCreate, ctx: OperatorContext = Depends(legacy_basic_agency_context)):
    return tr(service.add_feedback_scoped, ctx, match_id, payload)


@router.get("/matches/{match_id}/feedback")
def list_feedback(match_id: int, ctx: OperatorContext = Depends(legacy_basic_agency_context)):
    return {"items": tr(service.list_feedback_scoped, ctx, match_id)}


@router.delete("/feedback/{feedback_id}", status_code=204)
def delete_feedback(feedback_id: int, ctx: OperatorContext = Depends(legacy_basic_agency_context)):
    tr(service.delete_feedback_scoped, ctx, feedback_id)
    return Response(status_code=204)


@router.post("/exclusions", status_code=201)
def add_exclusion(payload: ExclusionCreate, ctx: OperatorContext = Depends(legacy_basic_agency_context)):
    return tr(service.add_exclusion_scoped, ctx, payload)


@router.get("/exclusions")
def list_exclusions(ctx: OperatorContext = Depends(legacy_basic_agency_context)):
    return {"items": tr(service.list_exclusions_scoped, ctx)}


@router.delete("/exclusions/{exclusion_id}", status_code=204)
def delete_exclusion(exclusion_id: int, ctx: OperatorContext = Depends(legacy_basic_agency_context)):
    tr(service.delete_exclusion_scoped, ctx, exclusion_id)
    return Response(status_code=204)
