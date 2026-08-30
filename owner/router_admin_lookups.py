"""P8.1 read-only lookup routes mounted under the authenticated OWNER Admin router."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from core.exceptions import NotFoundError
from . import admin_lookup_repository as r
from .admin_lookup_schemas import (
    ContactLookupResponse,
    DocumentLookupResponse,
    PropertyLookupResponse,
    VisitLookupResponse,
)


router = APIRouter(prefix="/lookups", tags=["owner-admin-lookups"])


def _read(fn, *args):
    try:
        return fn(*args)
    except NotFoundError:
        raise HTTPException(404, "Risorsa non trovata")


@router.get("/contacts", response_model=ContactLookupResponse)
def contacts(
    search: str | None = Query(None, max_length=200),
    limit: int = Query(50, ge=1, le=100),
):
    return {"items": _read(r.lookup_contacts, search, limit)}


@router.get("/accounts/{owner_account_id}/properties", response_model=PropertyLookupResponse)
def account_properties(owner_account_id: int):
    return {"items": _read(r.lookup_account_properties, owner_account_id)}


@router.get("/accounts/{owner_account_id}/properties/{property_id}/documents", response_model=DocumentLookupResponse)
def property_documents(owner_account_id: int, property_id: int):
    return {"items": _read(r.lookup_property_documents, owner_account_id, property_id)}


@router.get("/accounts/{owner_account_id}/properties/{property_id}/visits", response_model=VisitLookupResponse)
def property_visits(owner_account_id: int, property_id: int):
    return {"items": _read(r.lookup_property_visits, owner_account_id, property_id)}
