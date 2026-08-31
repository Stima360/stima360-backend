from fastapi import APIRouter, Depends, HTTPException, Query

from admin_security import require_admin
from core.exceptions import ConflictError, NotFoundError, ValidationError

from . import service
from .schemas import SaleCreate, SaleUpdate


router = APIRouter(prefix="/api/sales", tags=["sales"])


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
def create_sale(payload: SaleCreate, actor: str = Depends(require_admin)):
    return translate(service.create_sale, payload, actor)


@router.get("")
def list_sales(
    status: str | None = Query(None),
    property_id: int | None = Query(None, gt=0),
    buy_request_id: int | None = Query(None, gt=0),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return {
        "items": translate(
            service.list_sales,
            status=status,
            property_id=property_id,
            buy_request_id=buy_request_id,
            limit=limit,
            offset=offset,
        )
    }


@router.get("/{sale_id}")
def get_sale(sale_id: int):
    return translate(service.get_sale, sale_id)


@router.patch("/{sale_id}")
def update_sale(sale_id: int, payload: SaleUpdate):
    return translate(service.update_sale, sale_id, payload)


@router.post("/{sale_id}/complete")
def complete_sale(sale_id: int, actor: str = Depends(require_admin)):
    return translate(service.complete_sale, sale_id, actor)


@router.post("/{sale_id}/cancel")
def cancel_sale(sale_id: int, actor: str = Depends(require_admin)):
    return translate(service.cancel_sale, sale_id, actor)
