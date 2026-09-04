"""Admin-protected read/refresh routes for P23 Next Best Action.

Minimal set (section 8, frozen): LIST, DETAIL, REFRESH. No manual
create/edit/delete - P23 only recommends, it does not perform actions.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

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
def refresh_next_best_actions():
    return service.refresh()


@router.get("/{subject_type}/{subject_id}", response_model=NextBestAction)
def get_next_best_action(subject_type: str, subject_id: int):
    item = service.get_next_best_action(subject_type, subject_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Nessuna Next Best Action per questo soggetto")
    return item
