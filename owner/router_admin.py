from datetime import datetime
import secrets

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse

from core.exceptions import ConflictError, NotFoundError, ValidationError
from operator_auth.context import OperatorContext
from operator_auth.dependencies import require_owner_admin_context
from operator_auth.exceptions import PlatformAdminAgencyRequired
from .schemas import (
    AccessCreate,
    AccountCreate,
    FeedbackStatus,
    PrivacyValidationRequest,
    PublicationCreate,
    PublicationUpdate,
    RevokeRequest,
    SharedDocumentCreate,
    SharedDocumentStatus,
    SharedDocumentType,
    SharedDocumentSupersede,
    SharedDocumentUpdate,
    TokenCreate,
    VisitFeedbackCategory,
    VisitFeedbackCreate,
    VisitFeedbackStatus,
    VisitFeedbackSupersede,
    VisitFeedbackUpdate,
)
from . import repository as r
from .router_admin_lookups import router as lookup_router
from .document_storage import (
    DocumentFileValidationError,
    DocumentStorageError,
    iter_stream,
    safe_content_disposition,
    stage_upload,
    upload_limits_from_env,
)

# P26-5: OWNER Admin non ha piu' una guardia propria.
#
# `require_owner_admin` verificava ADMIN_USER/ADMIN_PASS a mano, in questo file,
# con una copia della logica di `admin_security.require_admin` e un realm
# diverso. Aveva senso finche' la superficie era Basic-only: adesso ammissione e
# scope vengono dalla stessa sessione, e tenere due controlli separati sarebbe
# di nuovo il rischio che P26-3 chiamo' "cookie + Basic".
#
# `require_owner_admin_context` decide entrambe le cose in un posto solo:
# autenticato o 401, ruolo sufficiente o 403, agenzia dalla sessione.
router = APIRouter(
    prefix="/api/owner/admin",
    tags=["owner-admin"],
    dependencies=[Depends(require_owner_admin_context)],
)
router.include_router(lookup_router)


def agency_of(ctx: OperatorContext) -> int:
    """The caller's agency, resolved server-side, or a refusal.

    `ctx.require_agency()` with its one failure mapped to 403 rather than
    escaping as a 500. It cannot fire on this channel - the compatibility
    dependency always resolves the Default Agency - but an unbound platform
    admin is refused rather than served, the same answer FLOW gives, and 403
    rather than 404 because the endpoint is not being hidden.

    Called before the repository, so a context with no agency never reaches a
    query.
    """
    try:
        return ctx.require_agency()
    except PlatformAdminAgencyRequired as exc:
        raise HTTPException(403, str(exc)) from exc


def x(f, *a, **kw):
    try:
        return f(*a, **kw)
    except NotFoundError:
        raise HTTPException(404, "Risorsa non trovata")
    except ConflictError as exc:
        raise HTTPException(409, str(exc))
    except ValidationError as exc:
        raise HTTPException(422, str(exc))
    except DocumentFileValidationError as exc:
        raise HTTPException(422, {"code": exc.code, "message": str(exc)})
    except DocumentStorageError as exc:
        raise HTTPException(503, {"code": exc.error_code, "message": "Storage documentale non disponibile"})


@router.get("/dashboard")
def dash(ctx: OperatorContext = Depends(require_owner_admin_context)):
    return x(r.dashboard, agency_of(ctx))


@router.get("/accounts")
def accounts(ctx: OperatorContext = Depends(require_owner_admin_context)):
    return {"items": x(r.list_accounts, agency_of(ctx))}


@router.post("/accounts", status_code=201)
def account(p: AccountCreate, ctx: OperatorContext = Depends(require_owner_admin_context)):
    return x(r.create_account, agency_of(ctx), p.model_dump())


@router.post("/accounts/{i}/disable")
def disable(i: int, ctx: OperatorContext = Depends(require_owner_admin_context)):
    return x(r.set_account, agency_of(ctx), i, "disabled")


@router.post("/accounts/{i}/enable")
def enable(i: int, ctx: OperatorContext = Depends(require_owner_admin_context)):
    return x(r.set_account, agency_of(ctx), i, "active")


@router.get("/access")
def access(ctx: OperatorContext = Depends(require_owner_admin_context)):
    return {"items": x(r.list_access, agency_of(ctx))}


@router.post("/access", status_code=201)
def access_create(p: AccessCreate, ctx: OperatorContext = Depends(require_owner_admin_context)):
    return x(r.create_access, agency_of(ctx), p.model_dump())


@router.post("/access/{i}/revoke")
def revoke(i: int, ctx: OperatorContext = Depends(require_owner_admin_context)):
    return x(r.revoke_access, agency_of(ctx), i)


@router.post("/accounts/{i}/tokens")
def token(i: int, p: TokenCreate, ctx: OperatorContext = Depends(require_owner_admin_context)):
    row, raw = x(r.create_token, agency_of(ctx), i, p.token_type, p.expires_minutes, p.created_by)
    return {
        "token_id": row["id"],
        "expires_at": row["expires_at"],
        "token": raw,
        "one_time_display": True,
    }


@router.get("/publications")
def pubs(ctx: OperatorContext = Depends(require_owner_admin_context)):
    return {"items": x(r.list_publications, agency_of(ctx))}


@router.post("/publications", status_code=201)
def pub(p: PublicationCreate, ctx: OperatorContext = Depends(require_owner_admin_context)):
    return x(r.create_publication, agency_of(ctx), p.model_dump())


@router.patch("/publications/{i}")
def edit(i: int, p: PublicationUpdate, ctx: OperatorContext = Depends(require_owner_admin_context)):
    return x(r.update_publication, agency_of(ctx), i, p.model_dump(exclude_unset=True))


@router.post("/publications/{i}/publish")
def publish(i: int, ctx: OperatorContext = Depends(require_owner_admin_context)):
    return x(r.publish, agency_of(ctx), i)


@router.post("/publications/{i}/archive")
def archive(i: int, ctx: OperatorContext = Depends(require_owner_admin_context)):
    return x(r.archive, agency_of(ctx), i)


@router.post("/publications/{i}/supersede", status_code=201)
def supersede(i: int, p: PublicationCreate, ctx: OperatorContext = Depends(require_owner_admin_context)):
    return x(r.supersede, agency_of(ctx), i, p.model_dump())


@router.get("/feedback")
def feedback(ctx: OperatorContext = Depends(require_owner_admin_context)):
    return {"items": x(r.admin_list_feedback, agency_of(ctx))}


@router.patch("/feedback/{i}")
def feedback_status(i: int, p: FeedbackStatus, ctx: OperatorContext = Depends(require_owner_admin_context)):
    return x(r.update_feedback_status, agency_of(ctx), i, p.model_dump(exclude_unset=True))


@router.get("/documents")
def documents(
    property_id: int | None = None,
    status: SharedDocumentStatus | None = None,
    owner_account_id: int | None = None,
    document_type: SharedDocumentType | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    ctx: OperatorContext = Depends(require_owner_admin_context),
):
    items = x(
        r.list_shared_documents,
        agency_of(ctx),
        property_id,
        status,
        owner_account_id,
        document_type,
        limit,
        offset,
    )
    return {"items": items, "limit": limit, "offset": offset}


@router.post("/documents", status_code=201)
def document_create(p: SharedDocumentCreate, ctx: OperatorContext = Depends(require_owner_admin_context)):
    return x(r.create_shared_document, agency_of(ctx), p.model_dump())


@router.post("/documents/upload", status_code=201)
def document_upload(
    file: UploadFile = File(...),
    property_id: int = Form(...),
    document_type: str = Form(..., min_length=1, max_length=80),
    source_title: str = Form(..., min_length=1, max_length=200),
    public_title: str = Form(..., min_length=1, max_length=200),
    public_document_type: SharedDocumentType = Form(...),
    owner_account_id: int | None = Form(None),
    supersedes_shared_document_id: int | None = Form(None),
    expires_at: datetime | None = Form(None),
    acknowledgement_required: bool = Form(False),
    created_by: str | None = Form(None, max_length=200),
    ctx: OperatorContext = Depends(require_owner_admin_context),
):
    max_bytes, chunk_size = upload_limits_from_env()
    staged = x(
        stage_upload,
        file.file,
        filename=file.filename,
        declared_mime=file.content_type,
        max_bytes=max_bytes,
        chunk_size=chunk_size,
    )
    try:
        return x(
            r.create_uploaded_shared_document,
            agency_of(ctx),
            {
                "property_id": property_id,
                "document_type": document_type,
                "source_title": source_title,
                "public_title": public_title,
                "public_document_type": public_document_type,
                "owner_account_id": owner_account_id,
                "supersedes_shared_document_id": supersedes_shared_document_id,
                "expires_at": expires_at,
                "acknowledgement_required": acknowledgement_required,
                "created_by": created_by,
            },
            staged,
        )
    finally:
        staged.close()


@router.get("/document-storage/health")
def document_storage_health():
    return x(r.document_storage_health)


@router.get("/documents/{i}")
def document_detail(i: int, ctx: OperatorContext = Depends(require_owner_admin_context)):
    return x(r.get_shared_document, agency_of(ctx), i)


@router.patch("/documents/{i}")
def document_update(i: int, p: SharedDocumentUpdate, ctx: OperatorContext = Depends(require_owner_admin_context)):
    return x(r.update_shared_document, agency_of(ctx), i, p.model_dump(exclude_unset=True))


@router.post("/documents/{i}/publish")
def document_publish(i: int, ctx: OperatorContext = Depends(require_owner_admin_context)):
    return x(r.publish_shared_document, agency_of(ctx), i)


@router.post("/documents/{i}/revoke")
def document_revoke(i: int, p: RevokeRequest, ctx: OperatorContext = Depends(require_owner_admin_context)):
    return x(r.revoke_shared_document, agency_of(ctx), i, p.actor, p.reason)


@router.post("/documents/{i}/archive")
def document_archive(i: int, ctx: OperatorContext = Depends(require_owner_admin_context)):
    return x(r.archive_shared_document, agency_of(ctx), i)


@router.post("/documents/{i}/supersede", status_code=201)
def document_supersede(i: int, p: SharedDocumentSupersede, ctx: OperatorContext = Depends(require_owner_admin_context)):
    return x(r.supersede_shared_document, agency_of(ctx), i, p.model_dump())


@router.get("/documents/{i}/reads")
def document_reads(i: int, ctx: OperatorContext = Depends(require_owner_admin_context)):
    return {"items": x(r.shared_document_reads, agency_of(ctx), i)}


@router.get("/documents/{i}/download")
def document_download(i: int, ctx: OperatorContext = Depends(require_owner_admin_context)):
    item = x(r.prepare_admin_shared_document_download, agency_of(ctx), i)
    headers = {
        "Content-Disposition": safe_content_disposition(item["filename"]),
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "sandbox",
        "Content-Length": str(item["size_bytes"]),
    }
    stream = iter_stream(
        item["opened"],
        on_complete=lambda: r.audit_shared_document_download(item, scope="admin"),
        on_error=lambda exc: r.audit_shared_document_download(
            item, result="error", reason_code="stream_failed", scope="admin"
        ),
    )
    return StreamingResponse(stream, media_type=item["mime_type"], headers=headers)


@router.post("/visit-feedback/validate-privacy")
def visit_feedback_validate_privacy(p: PrivacyValidationRequest):
    return x(r.validate_visit_feedback_privacy, p.public_summary)


@router.get("/visit-feedback")
def visit_feedback(
    property_visit_id: int | None = None,
    property_id: int | None = None,
    status: VisitFeedbackStatus | None = None,
    owner_account_id: int | None = None,
    category: VisitFeedbackCategory | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    ctx: OperatorContext = Depends(require_owner_admin_context),
):
    items = x(
        r.list_visit_feedback_publications,
        agency_of(ctx),
        property_visit_id,
        property_id,
        status,
        owner_account_id,
        category,
        limit,
        offset,
    )
    return {"items": items, "limit": limit, "offset": offset}


@router.get("/visit-feedback/{i}")
def visit_feedback_detail(i: int, ctx: OperatorContext = Depends(require_owner_admin_context)):
    return x(r.get_visit_feedback_publication, agency_of(ctx), i)


@router.post("/visit-feedback", status_code=201)
def visit_feedback_create(p: VisitFeedbackCreate, ctx: OperatorContext = Depends(require_owner_admin_context)):
    return x(r.create_visit_feedback_publication, agency_of(ctx), p.model_dump())


@router.patch("/visit-feedback/{i}")
def visit_feedback_update(i: int, p: VisitFeedbackUpdate, ctx: OperatorContext = Depends(require_owner_admin_context)):
    return x(r.update_visit_feedback_publication, agency_of(ctx), i, p.model_dump(exclude_unset=True))


@router.post("/visit-feedback/{i}/publish")
def visit_feedback_publish(i: int, ctx: OperatorContext = Depends(require_owner_admin_context)):
    return x(r.publish_visit_feedback, agency_of(ctx), i)


@router.post("/visit-feedback/{i}/archive")
def visit_feedback_archive(i: int, ctx: OperatorContext = Depends(require_owner_admin_context)):
    return x(r.archive_visit_feedback, agency_of(ctx), i)


@router.post("/visit-feedback/{i}/supersede", status_code=201)
def visit_feedback_supersede(i: int, p: VisitFeedbackSupersede, ctx: OperatorContext = Depends(require_owner_admin_context)):
    return x(r.supersede_visit_feedback, agency_of(ctx), i, p.model_dump())


@router.get("/audit")
def audit(ctx: OperatorContext = Depends(require_owner_admin_context)):
    return {"items": x(r.audits, agency_of(ctx))}