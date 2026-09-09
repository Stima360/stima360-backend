"""FastAPI router for additive P18-D temporal follow-up scans."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from operator_auth.context import OperatorContext
from operator_auth.dependencies import legacy_basic_agency_context
from operator_auth.exceptions import PlatformAdminAgencyRequired

from . import service
from .exceptions import ValidationError

router = APIRouter(prefix="/api/followup", tags=["followup"])


class TemporalScanRequest(BaseModel):
    """P26-6A: `created_by` is gone.

    It used to let any caller stamp a follow-up action with an arbitrary actor
    string, which is an identity the server has no reason to accept from a
    request body. The actor is now server-defined
    (`followup.service.SYSTEM_ACTOR`), and `extra="forbid"` makes a client still
    sending the old field fail loudly rather than have it silently ignored.
    """

    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=100, ge=1, le=500)


@router.post("/scan-temporal")
def scan_temporal(
    payload: TemporalScanRequest,
    ctx: OperatorContext = Depends(legacy_basic_agency_context),
):
    try:
        return service.run_temporal_escalation_scan_scoped(ctx, limit=payload.limit)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PlatformAdminAgencyRequired as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception:
        raise HTTPException(
            status_code=500,
            detail="followup temporal scan failed",
        ) from None
