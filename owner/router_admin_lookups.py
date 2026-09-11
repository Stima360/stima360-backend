"""P8.1 read-only lookup routes mounted under the authenticated OWNER Admin router.

P26-6C: all four are agency-bound. The tenant comes from
`require_owner_admin_context` - the Default Agency, resolved server-side from
its slug - and is passed as the first argument of every repository call. No
route here accepts an agency, and none of the path or query parameters can
influence which one is used.

The mount stays on `require_owner_admin` in `router_admin.py`. P26-3 moved
FLOW off that dependency and onto the operator session; OWNER Admin
deliberately did not follow - see `require_owner_admin_context` for why - so
admission and scope on this surface are the same credential, HTTP Basic.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from core.exceptions import NotFoundError
from operator_auth.context import OperatorContext
from operator_auth.dependencies import require_owner_admin_context
from operator_auth.exceptions import PlatformAdminAgencyRequired
from . import admin_lookup_repository as r
from .admin_lookup_schemas import (
    ContactLookupResponse,
    DocumentLookupResponse,
    PropertyLookupResponse,
    VisitLookupResponse,
)


router = APIRouter(prefix="/lookups", tags=["owner-admin-lookups"])


def agency_of(ctx: OperatorContext) -> int:
    """The caller's agency, or a refusal. See `router_admin.agency_of`.

    Duplicated rather than imported: `router_admin` imports this module, so an
    import in the other direction would be a cycle.
    """
    try:
        return ctx.require_agency()
    except PlatformAdminAgencyRequired as exc:
        raise HTTPException(403, str(exc)) from exc


def _read(fn, *args):
    try:
        return fn(*args)
    except NotFoundError:
        raise HTTPException(404, "Risorsa non trovata")


@router.get("/contacts", response_model=ContactLookupResponse)
def contacts(
    search: str | None = Query(None, max_length=200),
    limit: int = Query(50, ge=1, le=100),
    ctx: OperatorContext = Depends(require_owner_admin_context),
):
    return {"items": _read(r.lookup_contacts, agency_of(ctx), search, limit)}


@router.get("/accounts/{owner_account_id}/properties", response_model=PropertyLookupResponse)
def account_properties(
    owner_account_id: int,
    ctx: OperatorContext = Depends(require_owner_admin_context),
):
    return {"items": _read(r.lookup_account_properties, agency_of(ctx), owner_account_id)}


@router.get("/accounts/{owner_account_id}/properties/{property_id}/documents", response_model=DocumentLookupResponse)
def property_documents(
    owner_account_id: int,
    property_id: int,
    ctx: OperatorContext = Depends(require_owner_admin_context),
):
    return {
        "items": _read(
            r.lookup_property_documents, agency_of(ctx), owner_account_id, property_id
        )
    }


@router.get("/accounts/{owner_account_id}/properties/{property_id}/visits", response_model=VisitLookupResponse)
def property_visits(
    owner_account_id: int,
    property_id: int,
    ctx: OperatorContext = Depends(require_owner_admin_context),
):
    return {
        "items": _read(
            r.lookup_property_visits, agency_of(ctx), owner_account_id, property_id
        )
    }
