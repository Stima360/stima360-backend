"""REPORT-PROPRIETARIO-1 - `/api/owner-reports`: il report attivita' di un immobile.

Montato in main.py con `require_authenticated_operator`; ogni rotta prende lo
scope da `require_operator` (solo sessione). L'agenzia viene sempre dalla
sessione, il corpo e' chiuso (`extra=forbid`).

    GET  /api/owner-reports/{property_id}?date_from=&date_to=   anteprima (JSON)
    POST /api/owner-reports/{property_id}/pdf                   PDF da scaricare e condividere

Il PDF non viene salvato ne' inviato: lo scarica il consulente.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from agency_profile import service as agency_profile_service
from operator_auth.context import OperatorContext
from operator_auth.dependencies import require_operator
from operator_auth.exceptions import PlatformAdminAgencyRequired

from . import service
from .enums import AGENT_NOTES_MAX

router = APIRouter(prefix="/api/owner-reports", tags=["owner-reports"])


class PdfRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date_from: date | None = None
    date_to: date | None = None
    agent_notes: str | None = Field(None, max_length=AGENT_NOTES_MAX)


def _call(fn, *args):
    try:
        return fn(*args)
    except PlatformAdminAgencyRequired as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except service.ReportError as exc:
        raise HTTPException(status_code=exc.status, detail={"code": exc.code, "message": str(exc)}) from exc
    except agency_profile_service.ProfileCorrupted as exc:
        raise HTTPException(status_code=500, detail={"code": "AGENCY_PROFILE_CORRUPTED", "message": str(exc)}) from exc


@router.get("/{property_id}")
def preview(property_id: int, date_from: date | None = Query(None), date_to: date | None = Query(None),
            ctx: OperatorContext = Depends(require_operator)):
    return _call(service.build_report, ctx, property_id, date_from, date_to)


@router.post("/{property_id}/pdf")
def pdf(property_id: int, body: PdfRequest, ctx: OperatorContext = Depends(require_operator)):
    out = _call(service.render_pdf, ctx, property_id, body.date_from, body.date_to, body.agent_notes)
    return Response(content=out["data"], media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{out["filename"]}"',
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    })
