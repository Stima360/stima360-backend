"""BLOCCO-6A - `/api/visit-sheets`: fogli visita dalle visite in agenda.

Montato in main.py con `require_authenticated_operator`; scope da `require_operator`.

    GET  /api/visit-sheets?property_id=              visite dell'immobile e loro fogli
    GET  /api/visit-sheets/appointments/{id}         la visita di un appuntamento e i suoi fogli (Agenda)
    POST /api/visit-sheets/appointments/{id}         genera (o restituisce, se nulla e' cambiato)
    GET  /api/visit-sheets/{id}/pdf                  scarica il PDF salvato (stessi byte)
    GET  /api/visit-sheets/overview                  sezione «Fogli visita»: ultimo foglio di ogni visita,
                                                     stato della firma, ricerca e filtri (sola lettura)
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


@router.get("/overview")
def overview(q: str | None = Query(None, max_length=120), agent_id: int | None = Query(None, ge=1),
             date_from: str | None = Query(None, alias="from", max_length=40),
             date_to: str | None = Query(None, alias="to", max_length=40),
             state: str | None = Query(None, max_length=30), limit: int = Query(50, ge=1, le=200),
             offset: int = Query(0, ge=0), ctx: OperatorContext = Depends(require_operator)):
    """FOGLI VISITA: stessa visibilita' dei fogli (titolare/amministratori l'agenzia, agente le sue visite)."""
    from datetime import datetime

    from esign import summary

    def _data(valore):
        if not valore:
            return None
        try:
            return datetime.fromisoformat(valore)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail={"code": "BAD_DATE", "message": "Data non valida."}) from exc

    try:
        return summary.overview(ctx, q=q, agent_id=agent_id, date_from=_data(date_from), date_to=_data(date_to),
                                state=state, limit=limit, offset=offset)
    except PlatformAdminAgencyRequired as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/appointments/{appointment_id}")
def for_appointment(appointment_id: int = Path(..., ge=1), ctx: OperatorContext = Depends(require_operator)):
    """ROADMAP VISITE (fase C): i fogli di UNA visita, per la scheda dell'appuntamento."""
    return _call(service.list_for_appointment, ctx, appointment_id)


@router.post("/appointments/{appointment_id}")
def generate(appointment_id: int = Path(..., ge=1), ctx: OperatorContext = Depends(require_operator)):
    return _call(service.generate, ctx, appointment_id)


@router.get("/{sheet_id}/pdf")
def download(sheet_id: int = Path(..., ge=1), ctx: OperatorContext = Depends(require_operator)):
    out = _call(service.download, ctx, sheet_id)
    return Response(content=out["data"], media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{out["filename"]}"', "Cache-Control": "no-store",
        "X-Content-SHA256": out["sha256"]})
