"""Admin-protected read/refresh routes for P23 Next Best Action.

Minimal set (section 8, frozen): LIST, DETAIL, REFRESH. No manual
create/edit/delete - P23 only recommends, it does not perform actions.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from operator_auth.context import OperatorContext
from operator_auth.dependencies import legacy_basic_agency_context
from operator_auth.exceptions import PlatformAdminAgencyRequired

from . import service
from .schemas import (
    NextBestAction,
    NextBestActionListResponse,
    NextBestActionRefreshResult,
)

router = APIRouter(prefix="/api/next-best-action", tags=["next-best-action"])

DEFAULT_LIST_LIMIT = 50


def _agency_required(exc: PlatformAdminAgencyRequired) -> HTTPException:
    """An unbound platform admin is refused, not served, and not a 500.

    On the legacy Basic channel this cannot fire: `legacy_basic_agency_context`
    resolves the Default Agency server-side, so `require_agency()` always
    succeeds. It is here because the same three routes are what an
    operator-session mount would reach first, and because property_watch/router
    answers the identical condition with 403 - one authorization outcome should
    not depend on which intelligence router the caller happened to hit.
    """
    return HTTPException(status_code=403, detail=str(exc))


@router.get("", response_model=NextBestActionListResponse)
def list_next_best_actions(
    limit: int = DEFAULT_LIST_LIMIT,
    ctx: OperatorContext = Depends(legacy_basic_agency_context),
):
    try:
        items = service.list_next_best_actions_scoped(ctx, limit)
    except PlatformAdminAgencyRequired as exc:
        raise _agency_required(exc) from exc
    return {"items": items}


@router.post("/refresh", response_model=NextBestActionRefreshResult)
def refresh_next_best_actions(
    ctx: OperatorContext = Depends(legacy_basic_agency_context),
):
    """Recompute the current Next Best Actions inside the caller's agency.

    P23 reads CORE `leads` and `tasks`, so it needs an agency scope. This route
    is outside P26-1's D-1 operator-session allowlist and the OS Shell still
    authenticates it with Basic, so the scope comes from the C2 compatibility
    dependency: agency-bound, resolved server-side, never cross-agency.

    P26-6B: LIST and DETAIL below are scoped as well. They read only this
    module's own table, which is exactly why they now need a predicate -
    migration 046 gave `next_best_actions` a physical agency_id, so an unfiltered
    read returns every tenant's rows.
    """
    try:
        return service.refresh(ctx)
    except PlatformAdminAgencyRequired as exc:
        raise _agency_required(exc) from exc


@router.get("/{subject_type}/{subject_id}", response_model=NextBestAction)
def get_next_best_action(
    subject_type: str,
    subject_id: int,
    ctx: OperatorContext = Depends(legacy_basic_agency_context),
):
    try:
        item = service.get_next_best_action_scoped(ctx, subject_type, subject_id)
    except PlatformAdminAgencyRequired as exc:
        raise _agency_required(exc) from exc
    if item is None:
        raise HTTPException(status_code=404, detail="Nessuna Next Best Action per questo soggetto")
    return item
