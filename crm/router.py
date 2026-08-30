from fastapi import APIRouter, HTTPException

from core.exceptions import NotFoundError

from . import service
from .schemas import Contact360Response

router = APIRouter(prefix="/api/crm", tags=["crm"])


@router.get("/contacts/{contact_id}/360", response_model=Contact360Response)
def get_contact_360(contact_id: int):
    try:
        return service.get_contact_360(contact_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
