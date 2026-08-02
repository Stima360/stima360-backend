from fastapi import APIRouter, HTTPException, Query

from core.exceptions import ConflictError, NotFoundError, ValidationError
from .schemas import (
    AccessCreate,
    AccountCreate,
    FeedbackStatus,
    PrivacyValidationRequest,
    PublicationCreate,
    PublicationUpdate,
    RevokeRequest,
    SharedDocumentCreate,
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

router = APIRouter(prefix="/api/owner/admin", tags=["owner-admin"])


def x(f, *a):
    try:
        return f(*a)
    except NotFoundError:
        raise HTTPException(404, "Risorsa non trovata")
    except ConflictError as exc:
        raise HTTPException(409, str(exc))
    except ValidationError as exc:
        raise HTTPException(422, str(exc))


@router.get("/dashboard")
def dash():
    return x(r.dashboard)


@router.get("/accounts")
def accounts():
    return {"items": x(r.list_accounts)}


@router.post("/accounts", status_code=201)
def account(p: AccountCreate):
    return x(r.create_account, p.model_dump())


@router.post("/accounts/{i}/disable")
def disable(i: int):
    return x(r.set_account, i, "disabled")


@router.post("/accounts/{i}/enable")
def enable(i: int):
    return x(r.set_account, i, "active")


@router.get("/access")
def access():
    return {"items": x(r.list_access)}


@router.post("/access", status_code=201)
def access_create(p: AccessCreate):
    return x(r.create_access, p.model_dump())


@router.post("/access/{i}/revoke")
def revoke(i: int):
    return x(r.revoke_access, i)


@router.post("/accounts/{i}/tokens")
def token(i: int, p: TokenCreate):
    row, raw = x(r.create_token, i, p.token_type, p.expires_minutes, p.created_by)
    return {
        "token_id": row["id"],
        "expires_at": row["expires_at"],
        "token": raw,
        "one_time_display": True,
    }


@router.get("/publications")
def pubs():
    return {"items": x(r.list_publications)}


@router.post("/publications", status_code=201)
def pub(p: PublicationCreate):
    return x(r.create_publication, p.model_dump())


@router.patch("/publications/{i}")
def edit(i: int, p: PublicationUpdate):
    return x(r.update_publication, i, p.model_dump(exclude_unset=True))


@router.post("/publications/{i}/publish")
def publish(i: int):
    return x(r.publish, i)


@router.post("/publications/{i}/archive")
def archive(i: int):
    return x(r.archive, i)


@router.post("/publications/{i}/supersede", status_code=201)
def supersede(i: int, p: PublicationCreate):
    return x(r.supersede, i, p.model_dump())


@router.get("/feedback")
def feedback():
    return {"items": x(r.list_feedback)}


@router.patch("/feedback/{i}")
def feedback_status(i: int, p: FeedbackStatus):
    return x(r.update_feedback_status, i, p.model_dump(exclude_unset=True))


@router.get("/documents")
def documents():
    return {"items": x(r.list_shared_documents)}


@router.post("/documents", status_code=201)
def document_create(p: SharedDocumentCreate):
    return x(r.create_shared_document, p.model_dump())


@router.patch("/documents/{i}")
def document_update(i: int, p: SharedDocumentUpdate):
    return x(r.update_shared_document, i, p.model_dump(exclude_unset=True))


@router.post("/documents/{i}/publish")
def document_publish(i: int):
    return x(r.publish_shared_document, i)


@router.post("/documents/{i}/revoke")
def document_revoke(i: int, p: RevokeRequest):
    return x(r.revoke_shared_document, i, p.actor)


@router.post("/documents/{i}/archive")
def document_archive(i: int):
    return x(r.archive_shared_document, i)


@router.post("/documents/{i}/supersede", status_code=201)
def document_supersede(i: int, p: SharedDocumentSupersede):
    return x(r.supersede_shared_document, i, p.model_dump())


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
):
    items = x(
        r.list_visit_feedback_publications,
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
def visit_feedback_detail(i: int):
    return x(r.get_visit_feedback_publication, i)


@router.post("/visit-feedback", status_code=201)
def visit_feedback_create(p: VisitFeedbackCreate):
    return x(r.create_visit_feedback_publication, p.model_dump())


@router.patch("/visit-feedback/{i}")
def visit_feedback_update(i: int, p: VisitFeedbackUpdate):
    return x(r.update_visit_feedback_publication, i, p.model_dump(exclude_unset=True))


@router.post("/visit-feedback/{i}/publish")
def visit_feedback_publish(i: int):
    return x(r.publish_visit_feedback, i)


@router.post("/visit-feedback/{i}/archive")
def visit_feedback_archive(i: int):
    return x(r.archive_visit_feedback, i)


@router.post("/visit-feedback/{i}/supersede", status_code=201)
def visit_feedback_supersede(i: int, p: VisitFeedbackSupersede):
    return x(r.supersede_visit_feedback, i, p.model_dump())


@router.get("/audit")
def audit():
    return {"items": x(r.audits)}
