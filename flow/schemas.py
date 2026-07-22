from typing import Any, Literal
from pydantic import BaseModel, Field, validator
from .enums import SCAN_DEFAULT_LIMIT, SCAN_MAX_LIMIT

class RuleParametersUpdate(BaseModel):
    parameters: dict[str, Any]
    updated_by: str | None = None

class SimulationRequest(BaseModel):
    entity_type: str
    entity_id: int = Field(gt=0)
    requested_by: str | None = None

class EventCreate(BaseModel):
    event_type: str
    entity_type: str
    entity_id: int = Field(gt=0)
    source_module: Literal["core", "property", "buy", "match", "flow"]
    payload: dict[str, Any] = Field(default_factory=dict)
    deduplication_key: str | None = None

class EvaluateRequest(BaseModel):
    rule_code: str
    entity_type: str
    entity_id: int = Field(gt=0)
    mode: Literal["simulation", "live"] = "simulation"
    requested_by: str | None = None

class ScanRequest(BaseModel):
    rule_codes: list[str] | None = None
    limit: int = SCAN_DEFAULT_LIMIT
    simulation: bool = True
    requested_by: str | None = None

    @validator("limit")
    def valid_limit(cls, value):
        if value < 1 or value > SCAN_MAX_LIMIT:
            raise ValueError(f"limit must be between 1 and {SCAN_MAX_LIMIT}")
        return value

class SuppressionCreate(BaseModel):
    rule_code: str
    entity_type: str
    entity_id: int = Field(gt=0)
    reason: str
    expires_at: str | None = None
    created_by: str | None = None

class ActivationRequest(BaseModel):
    activated_by: str | None = None

class RetryRequest(BaseModel):
    requested_by: str | None = None
