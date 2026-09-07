"""HTTP adapter for the CRM 360 view.

`get_contact_360` reads four CORE tables through `core.service`, and since
P26-1 every one of those reads requires an agency scope. This router is where
that scope enters.

It is mounted behind legacy Basic (`main.py`), and P26-1's D-1 allowlist admits
operator sessions only on `/api/operator-auth/*` and `/api/core/*` - so there is
no session here to derive a scope from. The scope therefore comes from the same
C2 compatibility dependency Next Best Action uses: the Default Agency, resolved
server-side from its slug, presented as an agency-*bound* `OperatorContext`.

Never a `SystemAgencyContext` - that type is for server-originated work with no
principal, and a CRM request has a human behind it. Never an agency from the
request. Never a widened or platform scope.

CRM remains a legacy-Basic compatibility surface under GATE-MA1: it is
Default-Agency-bound on this channel, and its non-CORE reads (properties, buy
requests, matches, visits) stay unscoped until those modules are migrated.
"""

from fastapi import APIRouter, Depends, HTTPException

from core.exceptions import NotFoundError
from operator_auth.context import OperatorContext
from operator_auth.dependencies import legacy_basic_agency_context

from . import service
from .schemas import Contact360Response

router = APIRouter(prefix="/api/crm", tags=["crm"])


@router.get("/contacts/{contact_id}/360", response_model=Contact360Response)
def get_contact_360(
    contact_id: int,
    ctx: OperatorContext = Depends(legacy_basic_agency_context),
):
    try:
        return service.get_contact_360(ctx, contact_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
