"""IMPORTAZIONE RUBRICA - `/api/contact-imports`.

Montato in main.py con `require_authenticated_operator`; lo scope viene da
`require_operator`. Nessuna rotta invia messaggi.

    POST /api/contact-imports/preview                 (multipart: file .vcf) analizza e salva l'anteprima
    POST /api/contact-imports/{id}/commit             «Importa tutti» con le decisioni sulle voci ambigue
    GET  /api/contact-imports/{id}                    stato e riepilogo
    POST /api/contact-imports/contacts/{id}/share     un contatto riservato diventa dell'agenzia
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Path, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from operator_auth.context import OperatorContext
from operator_auth.dependencies import require_operator
from operator_auth.exceptions import PlatformAdminAgencyRequired

from . import service

router = APIRouter(prefix="/api/contact-imports", tags=["contact-imports"])


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str = Field(..., pattern=r"^(merge|create|skip)$")
    contact_id: int | None = Field(None, gt=0)


class CommitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decisions: dict[str, Decision] = Field(default_factory=dict)


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except PlatformAdminAgencyRequired as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except service.ImportError_ as exc:
        raise HTTPException(status_code=exc.status, detail={"code": exc.code, "message": str(exc)}) from exc


@router.post("/preview")
async def preview(file: UploadFile = File(...), ctx: OperatorContext = Depends(require_operator)):
    data = await file.read()
    return _call(service.preview, ctx, file_name=file.filename, data=data)


@router.post("/{import_id}/commit")
def commit(body: CommitRequest, import_id: int = Path(..., ge=1), ctx: OperatorContext = Depends(require_operator)):
    decisions = {k: v.model_dump() for k, v in body.decisions.items()}
    return _call(service.commit, ctx, import_id, decisions)


@router.get("/{import_id}")
def get(import_id: int = Path(..., ge=1), ctx: OperatorContext = Depends(require_operator)):
    return _call(service.get, ctx, import_id)


@router.post("/contacts/{contact_id}/share")
def share(contact_id: int = Path(..., ge=1), ctx: OperatorContext = Depends(require_operator)):
    return _call(service.share_contact, ctx, contact_id)
