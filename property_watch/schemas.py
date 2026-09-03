"""Pydantic schemas for the read-only Property Watch admin API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


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


class PropertyWatchState(PropertyWatchModel):
    watch: dict[str, Any]
    baseline: PropertyWatchObservation | None
    microzone_reference: MicrozoneReference | None
    internal_supply: InternalSupply | None
    observation_count: int
    observations: list[PropertyWatchObservation]
    computed_at: datetime
