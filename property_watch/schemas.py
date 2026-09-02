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
    observation_type: str
    source: str
    payload: dict[str, Any]
    idempotency_key: str
    observed_at: datetime
    created_at: datetime


class PropertyWatchState(PropertyWatchModel):
    watch: dict[str, Any]
    baseline: PropertyWatchObservation | None
    observation_count: int
    observations: list[PropertyWatchObservation]
    computed_at: datetime
