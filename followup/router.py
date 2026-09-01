"""FastAPI router for additive P18-D temporal follow-up scans."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import service
from .exceptions import ValidationError

router = APIRouter(prefix="/api/followup", tags=["followup"])


class TemporalScanRequest(BaseModel):
    limit: int = Field(default=100, ge=1, le=500)
    created_by: str | None = Field(default=None, max_length=200)


@router.post("/scan-temporal")
def scan_temporal(payload: TemporalScanRequest):
    try:
        return service.run_temporal_escalation_scan(
            limit=payload.limit,
            created_by=payload.created_by,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        raise HTTPException(
            status_code=500,
            detail="followup temporal scan failed",
        ) from None
