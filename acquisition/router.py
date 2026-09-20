"""LMC-15 - la superficie operatore del ponte di acquisizione.

INTERNA. Nessuna di queste rotte esiste sul portale del proprietario: sono
fatti che registra l'agenzia, non il cliente.

Lo scope arriva da `require_operator` su OGNI rotta, scritto per esteso e non
attraverso un alias di modulo: P26-4 dimostro' che un alias fa leggere le
rotte come non scopate al prover AST che certifica queste superfici.

Nessuna rotta accetta `agency_id` ne' un `*_operator_user_id`: il tenant viene
da `ctx.require_agency()` e l'attore da `ctx.user_id`.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from core.exceptions import ConflictError, NotFoundError, PermissionDenied, ValidationError
from operator_auth.context import OperatorContext
from operator_auth.dependencies import require_operator
from operator_auth.exceptions import PlatformAdminAgencyRequired

from . import service
from .schemas import (
    AcquisitionLinkCreate,
    AcquisitionRevoke,
    InspectionCancel,
    InspectionComplete,
    InspectionSchedule,
    MandateRecord,
)

router = APIRouter(prefix="/api/acquisition", tags=["acquisition"])

NOT_FOUND_MESSAGE = "Risorsa non trovata"


def _x(funzione, *args):
    try:
        return funzione(*args)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=NOT_FOUND_MESSAGE) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except PlatformAdminAgencyRequired as exc:
        # Un platform admin che non ha dichiarato in quale agenzia sta
        # lavorando: 403, e la query non viene raggiunta.
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/stime/{stima_id}/links", status_code=201)
def create_link(stima_id: int, payload: AcquisitionLinkCreate,
                ctx: OperatorContext = Depends(require_operator)):
    return _x(service.link_property_to_stima, ctx, stima_id, payload)


@router.post("/links/{acquisition_id}/mandate")
def record_mandate(acquisition_id: int, payload: MandateRecord,
                   ctx: OperatorContext = Depends(require_operator)):
    return _x(service.record_mandate, ctx, acquisition_id, payload)


@router.post("/links/{acquisition_id}/revoke")
def revoke_link(acquisition_id: int, payload: AcquisitionRevoke,
                ctx: OperatorContext = Depends(require_operator)):
    return _x(service.revoke_link, ctx, acquisition_id, payload)


@router.post("/stime/{stima_id}/inspections", status_code=201)
def schedule_inspection(stima_id: int, payload: InspectionSchedule,
                        ctx: OperatorContext = Depends(require_operator)):
    return _x(service.schedule_inspection, ctx, stima_id, payload)


@router.post("/stime/{stima_id}/inspections/completed", status_code=201)
def record_completed_inspection(stima_id: int, payload: InspectionComplete,
                                ctx: OperatorContext = Depends(require_operator)):
    return _x(service.record_completed_inspection, ctx, stima_id, payload)


@router.post("/inspections/{inspection_id}/complete")
def complete_inspection(inspection_id: int, payload: InspectionComplete,
                        ctx: OperatorContext = Depends(require_operator)):
    return _x(service.complete_inspection, ctx, inspection_id, payload)


@router.post("/inspections/{inspection_id}/cancel")
def cancel_inspection(inspection_id: int, payload: InspectionCancel,
                      ctx: OperatorContext = Depends(require_operator)):
    return _x(service.cancel_inspection, ctx, inspection_id, payload)
