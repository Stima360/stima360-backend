from fastapi import APIRouter, Depends, HTTPException, Query

from admin_security import require_admin
from core.exceptions import ConflictError, NotFoundError, ValidationError

from . import service
from .schemas import ProposalCreate, ProposalTransition, ProposalUpdate


router = APIRouter(prefix="/api/proposals", tags=["proposals"])


def translate(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except NotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("", status_code=201)
def create_proposal(payload: ProposalCreate, actor: str = Depends(require_admin)):
    return translate(service.create_proposal, payload, actor)


@router.get("")
def list_proposals(
    match_id: int | None = Query(None, gt=0),
    buy_request_id: int | None = Query(None, gt=0),
    property_id: int | None = Query(None, gt=0),
    contact_id: int | None = Query(None, gt=0),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return {
        "items": translate(
            service.list_proposals,
            match_id=match_id,
            buy_request_id=buy_request_id,
            property_id=property_id,
            contact_id=contact_id,
            limit=limit,
            offset=offset,
        )
    }


@router.get("/{proposal_id}")
def get_proposal(proposal_id: int):
    return translate(service.get_proposal, proposal_id)


@router.patch("/{proposal_id}")
def update_proposal(
    proposal_id: int,
    payload: ProposalUpdate,
    actor: str = Depends(require_admin),
):
    return translate(service.update_proposal, proposal_id, payload, actor)


@router.post("/{proposal_id}/transition")
def transition_proposal(
    proposal_id: int,
    payload: ProposalTransition,
    actor: str = Depends(require_admin),
):
    return translate(service.transition_proposal, proposal_id, payload, actor)
