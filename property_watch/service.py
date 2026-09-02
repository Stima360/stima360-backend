"""Application service for idempotent, append-only Property Watch events."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from . import repository
from .exceptions import StimaNotFoundError, ValidationError, WatchNotFoundError


logger = logging.getLogger(__name__)


def ensure_watch_for_stima(stima_id: int) -> dict[str, dict[str, Any]]:
    if not isinstance(stima_id, int) or isinstance(stima_id, bool) or stima_id < 1:
        raise ValidationError("stima_id must be a positive integer")

    stima = repository.get_stima_baseline_data(stima_id)
    if stima is None:
        raise StimaNotFoundError(f"stima {stima_id} not found")

    baseline = {
        key: stima[key]
        for key in ("comune", "microzona", "tipologia", "mq", "prezzo_mq_base")
        if stima.get(key) is not None
    }
    return repository.ensure_watch_with_baseline(stima_id, baseline)


def safe_ensure_watch_for_stima(stima_id: int) -> dict[str, dict[str, Any]] | None:
    """Fail open for the public valuation funnel; no P20 error may escape."""
    try:
        return ensure_watch_for_stima(stima_id)
    except Exception as exc:  # noqa: BLE001 - intentional public-flow isolation
        logger.error(
            "property_watch_initialization_failed stima_id=%s error_type=%s",
            stima_id,
            type(exc).__name__,
        )
        return None


def record_observation(
    *,
    watch_id: int,
    observation_type: str,
    source: str,
    payload: dict[str, Any],
    idempotency_key: str,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    if not isinstance(watch_id, int) or isinstance(watch_id, bool) or watch_id < 1:
        raise ValidationError("watch_id must be a positive integer")
    if not isinstance(observation_type, str) or not observation_type.strip():
        raise ValidationError("observation_type is required")
    if not isinstance(source, str) or not source.strip():
        raise ValidationError("source is required")
    if not isinstance(payload, dict):
        raise ValidationError("payload must be an object")
    if not isinstance(idempotency_key, str) or not idempotency_key.strip():
        raise ValidationError("idempotency_key is required")
    return repository.insert_observation(
        watch_id,
        observation_type,
        source,
        payload,
        idempotency_key,
        observed_at,
    )


def get_watch_for_stima(stima_id: int) -> dict[str, Any]:
    watch = repository.get_watch_for_stima(stima_id)
    if watch is None:
        raise WatchNotFoundError(f"property watch for stima {stima_id} not found")
    return watch


def get_current_watch_state(stima_id: int) -> dict[str, Any]:
    watch = get_watch_for_stima(stima_id)
    observations = repository.list_observations(watch["id"])
    baseline = next(
        (item for item in observations if item["observation_type"] == "watch_started"),
        None,
    )
    return {
        "watch": watch,
        "baseline": baseline,
        "observation_count": len(observations),
        "observations": observations,
        "computed_at": datetime.now(timezone.utc),
    }
