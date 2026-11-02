"""REPORT-PROPRIETARIO-1 - `/api/owner-reports`: il report attivita' di un immobile.

Montato in main.py con `require_authenticated_operator`; ogni rotta prende lo
scope da `require_operator` (solo sessione). L'agenzia viene sempre dalla
sessione, il corpo e' chiuso (`extra=forbid`).

    GET  /api/owner-reports/{property_id}?date_from=&date_to=   anteprima (JSON)
    POST /api/owner-reports/{property_id}/pdf                   PDF da scaricare e condividere

Il PDF non viene salvato ne' inviato: lo scarica il consulente.

ROADMAP VISITE (fase D) - l'aggiornamento al proprietario su una visita, dalla scheda
dell'appuntamento in Agenda, con l'approvazione dell'agente (`visit_updates`):

    GET  /api/owner-reports/visits/{appointment_id}             stato, proposta di testo, bozza o testo pubblicato
    POST /api/owner-reports/visits/{appointment_id}/check       validazione della privacy, senza scrivere
    PUT  /api/owner-reports/visits/{appointment_id}             salva la BOZZA (mai visibile al proprietario)
    POST /api/owner-reports/visits/{appointment_id}/approve     l'agente approva e pubblica (conferma esplicita)
    POST /api/owner-reports/visits/{appointment_id}/withdraw    ritira dal portale un riscontro pubblicato
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from agency_profile import service as agency_profile_service
from operator_auth.context import OperatorContext
from operator_auth.dependencies import require_operator
from operator_auth.exceptions import PlatformAdminAgencyRequired

from . import service, visit_updates
from .enums import AGENT_NOTES_MAX

router = APIRouter(prefix="/api/owner-reports", tags=["owner-reports"])


class PdfRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date_from: date | None = None
    date_to: date | None = None
    agent_notes: str | None = Field(None, max_length=AGENT_NOTES_MAX)


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except PlatformAdminAgencyRequired as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except service.ReportError as exc:
        detail = {"code": exc.code, "message": str(exc)}
        if getattr(exc, "issues", None):
            detail["issues"] = exc.issues                     # solo codici e frasi, mai il testo inviato
        raise HTTPException(status_code=exc.status, detail=detail) from exc
    except agency_profile_service.ProfileCorrupted as exc:
        raise HTTPException(status_code=500, detail={"code": "AGENCY_PROFILE_CORRUPTED", "message": str(exc)}) from exc


class VisitUpdateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str = Field("general", max_length=40)
    sentiment: str | None = Field(None, max_length=20)
    public_summary: str = Field(..., max_length=visit_updates.SUMMARY_MAX)


class VisitCheckBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    public_summary: str = Field("", max_length=visit_updates.SUMMARY_MAX)


class VisitApproveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    publication_id: int = Field(..., ge=1)
    confirm: bool = False                                     # l'approvazione e' un gesto esplicito


class VisitWithdrawBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    publication_id: int = Field(..., ge=1)


@router.get("/visits/{appointment_id}")
def visit_update_state(appointment_id: int, ctx: OperatorContext = Depends(require_operator)):
    return _call(visit_updates.get_state, ctx, appointment_id)


@router.post("/visits/{appointment_id}/check")
def visit_update_check(appointment_id: int, body: VisitCheckBody, ctx: OperatorContext = Depends(require_operator)):
    return _call(visit_updates.check, ctx, appointment_id, body.public_summary)


@router.put("/visits/{appointment_id}")
def visit_update_save(appointment_id: int, body: VisitUpdateBody, ctx: OperatorContext = Depends(require_operator)):
    return _call(visit_updates.save_draft, ctx, appointment_id, category=body.category, sentiment=body.sentiment,
                 public_summary=body.public_summary)


@router.post("/visits/{appointment_id}/approve")
def visit_update_approve(appointment_id: int, body: VisitApproveBody,
                         ctx: OperatorContext = Depends(require_operator)):
    return _call(visit_updates.approve, ctx, appointment_id, body.publication_id, confirm=body.confirm)


@router.post("/visits/{appointment_id}/withdraw")
def visit_update_withdraw(appointment_id: int, body: VisitWithdrawBody,
                          ctx: OperatorContext = Depends(require_operator)):
    return _call(visit_updates.withdraw, ctx, appointment_id, body.publication_id)


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
