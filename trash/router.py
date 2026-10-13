"""CESTINO-GENERALE-1 (CG-1) - la superficie tenant del Cestino unico
(`/api/trash`), in SOLA LETTURA.

    GET /api/trash                          elenco unificato (type, q, reason,
                                            deleted_by, date_from, date_to,
                                            limit, offset)
    GET /api/trash/counts                   contatori per categoria + richieste
                                            edifici in attesa
    GET /api/trash/building-requests        richieste di eliminazione edifici
                                            dell'agenzia (status)
    GET /api/trash/{type}/{id}/relations    anteprima dei collegamenti

Scope: `require_operator` su ogni rotta (sessione; l'agenzia e' quella della
sessione, mai un parametro). Spostare, ripristinare e cancellare restano le
rotte esistenti di ogni categoria e quelle del Super Admin.

MONTAGGIO: questo router NON e' montato finche' non viene autorizzata la
modifica di `main.py` (file protetto). Le prove lo montano su un'app di prova.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from core.exceptions import ConflictError, NotFoundError, PermissionDenied, ValidationError
from operator_auth.context import OperatorContext
from operator_auth.dependencies import require_operator
from operator_auth.exceptions import PlatformAdminAgencyRequired

from . import service

router = APIRouter(prefix="/api/trash", tags=["trash"])

AGENCY_REQUIRED_MESSAGE = "Il Cestino dell'agenzia richiede un'agenzia: il Super Admin usa la vista globale"


def tradurre(fn, *args, **kwargs):
    """`{detail, code, ...}` come le altre superfici del Cestino."""
    try:
        esito = fn(*args, **kwargs)
    except PlatformAdminAgencyRequired:
        return JSONResponse(status_code=403, content={"detail": AGENCY_REQUIRED_MESSAGE, "code": "AGENCY_REQUIRED"})
    except (NotFoundError, ConflictError, ValidationError, PermissionDenied) as exc:
        stato = {NotFoundError: 404, ConflictError: 409, ValidationError: 400, PermissionDenied: 403}
        http = next(v for c, v in stato.items() if isinstance(exc, c))
        corpo = dict(getattr(exc, "extra", None) or {})
        corpo.update({"detail": str(exc) if http != 404 else "Elemento non trovato nel Cestino",
                      "code": getattr(exc, "code", None) or
                      {404: "NOT_FOUND", 409: "CONFLICT", 400: "VALIDATION_ERROR", 403: "FORBIDDEN"}[http]})
        return JSONResponse(status_code=http, content=jsonable_encoder(corpo))
    return JSONResponse(status_code=200, content=jsonable_encoder(esito))


@router.get("")
def list_trash(
    type: str | None = Query(None, max_length=80),
    q: str | None = Query(None, max_length=200),
    reason: str | None = Query(None, max_length=30),
    deleted_by: int | None = Query(None, ge=1),
    date_from: str | None = Query(None, max_length=10),
    date_to: str | None = Query(None, max_length=10),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: OperatorContext = Depends(require_operator),
):
    return tradurre(service.list_items, ctx, scope="agency", kind=type, q=q, reason=reason, deleted_by=deleted_by,
                    date_from=date_from, date_to=date_to, limit=limit, offset=offset)


@router.get("/counts")
def trash_counts(
    q: str | None = Query(None, max_length=200),
    reason: str | None = Query(None, max_length=30),
    deleted_by: int | None = Query(None, ge=1),
    date_from: str | None = Query(None, max_length=10),
    date_to: str | None = Query(None, max_length=10),
    ctx: OperatorContext = Depends(require_operator),
):
    return tradurre(service.counts, ctx, scope="agency", q=q, reason=reason, deleted_by=deleted_by,
                    date_from=date_from, date_to=date_to)


@router.get("/building-requests")
def building_requests(
    status: str | None = Query("pending", max_length=12),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: OperatorContext = Depends(require_operator),
):
    return tradurre(service.building_requests, ctx, status=status, limit=limit, offset=offset)


@router.get("/{kind}/{entity_id}/relations")
def trash_relations(kind: str, entity_id: int, ctx: OperatorContext = Depends(require_operator)):
    return tradurre(service.relations, ctx, kind, entity_id, scope="agency")
