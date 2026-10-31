"""BLOCCO-6A - `/api/visit-sheets`: fogli visita dalle visite in agenda.

Montato in main.py con `require_authenticated_operator`; scope da `require_operator`.

    GET  /api/visit-sheets?property_id=              visite dell'immobile e loro fogli
    POST /api/visit-sheets/appointments/{id}         genera (o restituisce, se nulla e' cambiato)
    GET  /api/visit-sheets/{id}/pdf                  scarica il PDF salvato (stessi byte)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response

from operator_auth.context import OperatorContext
from operator_auth.dependencies import require_operator
from operator_auth.exceptions import PlatformAdminAgencyRequired

from . import service

router = APIRouter(prefix="/api/visit-sheets", tags=["visit-sheets"])


def _call(fn, *args):
    try:
        return fn(*args)
    except PlatformAdminAgencyRequired as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except service.VisitSheetError as exc:
        raise HTTPException(status_code=exc.status, detail={"code": exc.code, "message": str(exc)}) from exc


@router.get("")
def list_sheets(property_id: int = Query(..., ge=1), ctx: OperatorContext = Depends(require_operator)):
    return _call(service.list_for_property, ctx, property_id)


@router.post("/appointments/{appointment_id}")
def generate(appointment_id: int = Path(..., ge=1), ctx: OperatorContext = Depends(require_operator)):
    return _call(service.generate, ctx, appointment_id)


@router.get("/{sheet_id}/pdf")
def download(sheet_id: int = Path(..., ge=1), ctx: OperatorContext = Depends(require_operator)):
    out = _call(service.download, ctx, sheet_id)
    return Response(content=out["data"], media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{out["filename"]}"', "Cache-Control": "no-store",
        "X-Content-SHA256": out["sha256"]})
