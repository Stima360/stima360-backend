"""BLOCCO-4C - `/api/reply-suggestions`: suggerimento di risposta a un messaggio ricevuto.

Montato in main.py con `require_authenticated_operator`; lo scope viene da
`require_operator`. Una sola rotta, GET: calcola e restituisce, non scrive
nulla e non invia nulla (anche in consultazione in sola lettura).

    GET /api/reply-suggestions/messages/{message_id}
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path

from operator_auth.context import OperatorContext
from operator_auth.dependencies import require_operator
from operator_auth.exceptions import PlatformAdminAgencyRequired

from . import service

router = APIRouter(prefix="/api/reply-suggestions", tags=["reply-suggestions"])


@router.get("/messages/{message_id}")
def suggestion(message_id: int = Path(..., ge=1), ctx: OperatorContext = Depends(require_operator)):
    try:
        return service.suggest(ctx, message_id)
    except PlatformAdminAgencyRequired as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except service.SuggestionError as exc:
        raise HTTPException(status_code=exc.status, detail={"code": exc.code, "message": str(exc)}) from exc
