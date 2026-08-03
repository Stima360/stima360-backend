from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse

from .schemas import FeedbackCreate, TokenConsume
from .dependencies import current_owner
from .security import clear_cookie, set_cookie
from .enums import COOKIE_NAME
from . import repository as r
from .document_storage import iter_stream, safe_content_disposition

router = APIRouter(prefix="/api/owner/portal", tags=["owner-portal"])


def nf(f, *a):
    try:
        return f(*a)
    except Exception:
        raise HTTPException(404,'Risorsa non trovata')


def visit_feedback_nf(
    f,
    *a,
    account: int,
    property_id: int | None = None,
    publication_id: int | None = None,
    scope: str,
):
    try:
        return f(*a)
    except Exception:
        r.audit_visit_feedback_access_denied(
            account,
            property_id=property_id,
            publication_id=publication_id,
            scope=scope,
        )
        raise HTTPException(404,'Risorsa non trovata')




def shared_document_nf(
    f,
    *a,
    account: int,
    property_id: int | None = None,
    document_id: int | None = None,
    scope: str,
):
    try:
        return f(*a)
    except Exception as exc:
        r.audit_shared_document_access_denied(
            account,
            property_id=property_id,
            document_id=document_id,
            scope=scope,
            reason_code=getattr(exc, "error_code", "not_found"),
        )
        raise HTTPException(404, 'Risorsa non trovata')

@router.post("/auth/token", status_code=204)
def login(p: TokenConsume, response: Response):
    _session, raw_token = nf(r.consume_token, p.token)
    set_cookie(response, raw_token)
    return None


@router.post("/auth/logout", status_code=204)
def logout(request: Request, response: Response):
    r.revoke_session(request.cookies.get(COOKIE_NAME))
    clear_cookie(response)
    return None


@router.get("/session")
def session(s=Depends(current_owner)):
    return {
        "authenticated": True,
        "owner_account_id": s["owner_account_id"],
        "expires_at": s["expires_at"],
    }


@router.get("/dashboard")
def dashboard(s=Depends(current_owner)):
    items = nf(r.portal_properties, s["owner_account_id"])
    return {"properties": items, "property_count": len(items)}


@router.get("/properties")
def properties(s=Depends(current_owner)):
    return {"items": nf(r.portal_properties, s["owner_account_id"])}


@router.get("/properties/{p}")
def prop(p: int, s=Depends(current_owner)):
    account = s["owner_account_id"]
    return {
        "property": nf(r.require_property, account, p),
        "timeline": nf(r.timeline, account, p),
        "documents": nf(r.portal_shared_documents, account, p),
        "visit_feedback": nf(r.portal_visit_feedback, account, p),
    }


@router.get("/properties/{p}/timeline")
def timeline(p: int, s=Depends(current_owner)):
    return {"items": nf(r.timeline, s["owner_account_id"], p)}


@router.get("/publications/{i}")
def publication(i: int, s=Depends(current_owner)):
    item = nf(r.publication, s["owner_account_id"], i)
    nf(r.read, s["owner_account_id"], i, False)
    return item


@router.post("/publications/{i}/acknowledge")
def ack(i: int, s=Depends(current_owner)):
    return nf(r.read, s["owner_account_id"], i, True)


@router.get("/properties/{p}/documents")
def documents(p: int, s=Depends(current_owner)):
    return {"items": nf(r.portal_shared_documents, s["owner_account_id"], p)}


@router.get("/documents/{i}")
def document(i: int, s=Depends(current_owner)):
    account = s["owner_account_id"]
    item = shared_document_nf(
        r.portal_shared_document,
        account,
        i,
        account=account,
        document_id=i,
        scope="detail",
    )
    receipt = shared_document_nf(
        r.read_shared_document,
        account,
        i,
        False,
        account=account,
        document_id=i,
        scope="view",
    )
    return {"document": item, "read": receipt}


@router.get("/documents/{i}/download")
def document_download(i: int, s=Depends(current_owner)):
    account = s["owner_account_id"]
    item = shared_document_nf(
        r.prepare_shared_document_download,
        account,
        i,
        account=account,
        document_id=i,
        scope="download",
    )
    headers = {
        "Content-Disposition": safe_content_disposition(item["filename"]),
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "sandbox",
        "Content-Length": str(item["size_bytes"]),
    }
    stream = iter_stream(
        item["opened"],
        on_complete=lambda: r.audit_shared_document_download(item, scope="portal"),
        on_error=lambda exc: r.audit_shared_document_download(
            item, result="error", reason_code="stream_failed", scope="portal"
        ),
    )
    return StreamingResponse(stream, media_type=item["mime_type"], headers=headers)


@router.post("/documents/{i}/acknowledge")
def document_ack(i: int, s=Depends(current_owner)):
    account = s["owner_account_id"]
    return shared_document_nf(
        r.read_shared_document,
        account,
        i,
        True,
        account=account,
        document_id=i,
        scope="acknowledge",
    )


@router.get("/properties/{p}/visit-feedback")
def visit_feedback(
    p: int,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    s=Depends(current_owner),
):
    account = s["owner_account_id"]
    items = visit_feedback_nf(
        r.portal_visit_feedback,
        account,
        p,
        limit,
        offset,
        account=account,
        property_id=p,
        scope="list",
    )
    return {"items": items, "limit": limit, "offset": offset}


@router.get("/visit-feedback/{i}")
def visit_feedback_detail(i: int, s=Depends(current_owner)):
    account = s["owner_account_id"]
    item = visit_feedback_nf(
        r.portal_visit_feedback_detail,
        account,
        i,
        account=account,
        publication_id=i,
        scope="detail",
    )
    return {"visit_feedback": item}


@router.post("/properties/{p}/feedback", status_code=201)
def feedback(p: int, d: FeedbackCreate, s=Depends(current_owner)):
    return nf(r.create_feedback, s["owner_account_id"], p, d.model_dump())


@router.get("/properties/{p}/feedback")
def feedback_list(p: int, s=Depends(current_owner)):
    nf(r.require_property, s["owner_account_id"], p)
    return {"items": nf(r.list_feedback, s["owner_account_id"], p)}
