"""Pydantic schemas for the read-only Property Watch admin API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class PropertyWatchModel(BaseModel):
    class Config:
        extra = "forbid"


class PropertyWatchObservation(PropertyWatchModel):
    id: int
    watch_id: int
    observation_type: str
    source: str
    payload: dict[str, Any]
    idempotency_key: str
    observed_at: datetime
    created_at: datetime


class MicrozoneReference(PropertyWatchModel):
    prezzo_mq_base: Any | None
    current: Any | None
    latest_change: PropertyWatchObservation | None
    observed_at: datetime | None
    observation_count: int


class InternalSupply(PropertyWatchModel):
    current_count: int | None
    latest_observation: PropertyWatchObservation
    observed_at: datetime
    observation_count: int


class PropertyWatchCollectorOutcome(PropertyWatchModel):
    status: str
    watch_id: int | None
    observation: PropertyWatchObservation | None


class PropertyWatchInternalSignalsRefresh(PropertyWatchModel):
    watch_id: int | None
    microzone: PropertyWatchCollectorOutcome
    internal_supply: PropertyWatchCollectorOutcome


class PropertyWatchInternalSignalsBatchOutcome(
    PropertyWatchInternalSignalsRefresh
):
    stima_id: int


class PropertyWatchInternalSignalsBatchRefresh(PropertyWatchModel):
    processed: int
    written: int
    unchanged: int
    unavailable: int
    failed: int
    outcomes: list[PropertyWatchInternalSignalsBatchOutcome]


class PropertyWatchBuyerPressureRefresh(PropertyWatchCollectorOutcome):
    pass


class PropertyWatchBuyerPressureBatchOutcome(PropertyWatchCollectorOutcome):
    stima_id: int


class PropertyWatchBuyerPressureBatchRefresh(PropertyWatchModel):
    processed: int
    written: int
    unchanged: int
    unavailable: int
    superseded: int
    failed: int
    outcomes: list[PropertyWatchBuyerPressureBatchOutcome]


class BuyerPressureMetricsState(PropertyWatchModel):
    evaluated_buyers: int
    compatible_buyers: int
    highly_compatible_buyers: int
    recent_compatible_buyers_30d: int
    average_match_score: float | None
    maximum_match_score: float | None
    average_budget: float | None
    algorithm_version: str
    latest_observation: PropertyWatchObservation
    observed_at: datetime
    observation_count: int


class BuyerPressureFactor(PropertyWatchModel):
    code: str
    label: str
    points: int = Field(ge=0)
    max_points: int = Field(gt=0)


class BuyerPressureInsight(PropertyWatchModel):
    score_version: str
    score: int = Field(ge=0, le=100)
    band: Literal["none", "low", "medium", "high"]
    band_label: str
    headline: str
    message: str
    disclaimer: str
    factors: list[BuyerPressureFactor]


class PropertyWatchState(PropertyWatchModel):
    watch: dict[str, Any]
    baseline: PropertyWatchObservation | None
    microzone_reference: MicrozoneReference | None
    internal_supply: InternalSupply | None
    buyer_pressure_metrics: BuyerPressureMetricsState | None
    buyer_pressure_insight: BuyerPressureInsight | None
    observation_count: int
    observations: list[PropertyWatchObservation]
    computed_at: datetime


class InvisibleSaleCandidate(PropertyWatchModel):
    buy_request_id: int
    score_total: float
    compatibility_status: Literal["compatible", "exception"]
    reason_codes: list[
        Literal["location", "budget", "typology", "dimensions", "rooms", "features", "condition"]
    ]
    last_activity_at: datetime
    budget_reference: float | None
    match_algorithm_version: str
    status: Literal["pending_review", "approved", "rejected", "stale"]


class InvisibleSaleState(PropertyWatchModel):
    status: Literal["not_collected", "ready", "empty", "closed"]
    current_candidate_count: int = Field(ge=0)
    candidates: list[InvisibleSaleCandidate]


class InvisibleSaleOutcome(PropertyWatchModel):
    status: Literal["written", "unchanged", "baseline_unavailable", "closed", "failed"]
    watch_id: int | None


class InvisibleSaleBatchOutcome(InvisibleSaleOutcome):
    stima_id: int


class InvisibleSaleBatchRefresh(PropertyWatchModel):
    processed: int = Field(ge=0)
    outcomes: list[InvisibleSaleBatchOutcome]
    totals: dict[str, int]
