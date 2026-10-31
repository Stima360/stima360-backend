"""BLOCCO-2A - `/api/property-valuations`: valutazioni dell'agente.

Montato in main.py con `require_authenticated_operator`; ogni rotta prende lo
scope da `require_operator` (solo sessione). Nessuna rotta accetta
`agency_id`, `created_by` o `approved_by`: i corpi sono chiusi (`extra=forbid`).

    GET  /api/property-valuations?property_id=   versioni dell'immobile
    POST /api/property-valuations                 nuova bozza (anche senza incarico)
    GET  /api/property-valuations/{id}            una versione
    PUT  /api/property-valuations/{id}            modifica una bozza (row_version)
    POST /api/property-valuations/{id}/approve    approva (owner/admin)
    POST /api/property-valuations/{id}/new-version  bozza da una versione approvata
    POST /api/property-valuations/{id}/pdf        genera (una volta) il PDF della versione approvata
    GET  /api/property-valuations/{id}/pdf        scarica il PDF salvato (stessi byte, sempre)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from operator_auth.context import OperatorContext
from operator_auth.dependencies import require_operator
from operator_auth.exceptions import PlatformAdminAgencyRequired

from agency_profile import service as agency_profile_service

from . import pdf_service, service
from .schemas import ApproveRequest, CreateRequest, UpdateRequest

router = APIRouter(prefix="/api/property-valuations", tags=["property-valuations"])


def _call(fn, *args):
    try:
        return fn(*args)
    except PlatformAdminAgencyRequired as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except service.ValuationError as exc:
        raise HTTPException(status_code=exc.status,
                            detail={"code": exc.code, "message": str(exc), **exc.extra}) from exc
    except agency_profile_service.ProfileCorrupted as exc:
        # BLOCCO-2B: l'intestazione salvata non e' leggibile: nessun PDF parziale.
        raise HTTPException(status_code=500, detail={"code": "AGENCY_PROFILE_CORRUPTED", "message": str(exc)}) from exc


@router.get("")
def list_valuations(property_id: int = Query(..., gt=0), ctx: OperatorContext = Depends(require_operator)):
    return _call(service.list_valuations, ctx, property_id)


@router.post("", status_code=201)
def create_valuation(body: CreateRequest, ctx: OperatorContext = Depends(require_operator)):
    return _call(service.create_draft, ctx, body.property_id, body.content)


@router.get("/{valuation_id}")
def read_valuation(valuation_id: int, ctx: OperatorContext = Depends(require_operator)):
    return _call(service.get_valuation, ctx, valuation_id)


@router.put("/{valuation_id}")
def update_valuation(valuation_id: int, body: UpdateRequest, ctx: OperatorContext = Depends(require_operator)):
    return _call(service.update_draft, ctx, valuation_id, body.row_version, body.content)


@router.post("/{valuation_id}/approve")
def approve_valuation(valuation_id: int, body: ApproveRequest, ctx: OperatorContext = Depends(require_operator)):
    return _call(service.approve, ctx, valuation_id, body.row_version)


@router.post("/{valuation_id}/new-version", status_code=201)
def new_version(valuation_id: int, ctx: OperatorContext = Depends(require_operator)):
    return _call(service.create_version, ctx, valuation_id)


@router.post("/{valuation_id}/pdf")
def generate_pdf(valuation_id: int, response: Response, ctx: OperatorContext = Depends(require_operator)):
    result = _call(pdf_service.generate_pdf, ctx, valuation_id)
    response.status_code = 201 if result["created"] else 200
    return result


@router.get("/{valuation_id}/pdf")
def download_pdf(valuation_id: int, ctx: OperatorContext = Depends(require_operator)):
    pdf = _call(pdf_service.download_pdf, ctx, valuation_id)
    return Response(content=pdf["data"], media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{pdf["filename"]}"',
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "ETag": f'"{pdf["sha256"]}"',
    })
