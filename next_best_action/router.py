"""Admin-protected read/refresh routes for P23 Next Best Action.

Minimal set (section 8, frozen): LIST, DETAIL, REFRESH. No manual
create/edit/delete - P23 only recommends, it does not perform actions.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from operator_auth.context import OperatorContext
from operator_auth.dependencies import legacy_basic_agency_context

from . import service
from .schemas import (
    NextBestAction,
    NextBestActionListResponse,
    NextBestActionRefreshResult,
)

router = APIRouter(prefix="/api/next-best-action", tags=["next-best-action"])

DEFAULT_LIST_LIMIT = 50


@router.get("", response_model=NextBestActionListResponse)
def list_next_best_actions(limit: int = DEFAULT_LIST_LIMIT):
    items = service.list_next_best_actions(limit)
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

    LIST and DETAIL below read only `next_best_actions`, this module's own
    table, and take no scope - adding one would claim a filtering they do not
    perform.
    """
    return service.refresh(ctx)


@router.get("/{subject_type}/{subject_id}", response_model=NextBestAction)
def get_next_best_action(subject_type: str, subject_id: int):
    item = service.get_next_best_action(subject_type, subject_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Nessuna Next Best Action per questo soggetto")
    return item
