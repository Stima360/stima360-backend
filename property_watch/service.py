"""Application service for idempotent, append-only Property Watch events."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from . import repository
from .exceptions import StimaNotFoundError, ValidationError, WatchNotFoundError


logger = logging.getLogger(__name__)


def _validate_stima_id(stima_id: int) -> None:
    if not isinstance(stima_id, int) or isinstance(stima_id, bool) or stima_id < 1:
        raise ValidationError("stima_id must be a positive integer")


def _log_unavailable(stima_id: int, collector: str, outcome: dict[str, Any]) -> None:
    if outcome["status"] in {"baseline_unavailable", "source_unavailable"}:
        logger.warning(
            "property_watch_internal_signal_unavailable stima_id=%s collector=%s outcome=%s",
            stima_id,
            collector,
            outcome["status"],
        )


def ensure_watch_for_stima(stima_id: int) -> dict[str, dict[str, Any]]:
    _validate_stima_id(stima_id)

    stima = repository.get_stima_baseline_data(stima_id)
    if stima is None:
        raise StimaNotFoundError(f"stima {stima_id} not found")

    completed = repository.get_stima_completed_valuation(stima_id)
    if completed is None:
        raise ValidationError(
            f"completed valuation not found for stima {stima_id}"
        )

    baseline = {
        key: stima[key]
        for key in ("comune", "microzona", "tipologia", "mq", "prezzo_mq_base")
        if stima.get(key) is not None
    }
    baseline.update(
        {
            key: completed[key]
            for key in ("price_exact", "eur_mq_finale", "base_mq")
            if completed.get(key) is not None
        }
    )

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


def collect_microzone_market_signal_for_stima(stima_id: int) -> dict[str, Any]:
    _validate_stima_id(stima_id)
    with repository.property_watch_cursor(commit=True) as (_, cur):
        context = repository.get_collection_context_for_update(cur, stima_id)
        if context is None:
            raise WatchNotFoundError(f"active property watch for stima {stima_id} not found")
        baseline = context["baseline"]
        outcome = repository.collect_microzone_price_change(
            context["watch"]["id"],
            baseline["payload"] if baseline is not None else {},
            cur=cur,
        )
    _log_unavailable(stima_id, "microzone", outcome)
    return outcome


def collect_internal_supply_signal_for_stima(stima_id: int) -> dict[str, Any]:
    _validate_stima_id(stima_id)
    with repository.property_watch_cursor(commit=True) as (_, cur):
        context = repository.get_collection_context_for_update(cur, stima_id)
        if context is None:
            raise WatchNotFoundError(
                f"active property watch for stima {stima_id} not found"
            )
        baseline = context["baseline"]
        outcome = repository.collect_internal_supply_change(
            context["watch"]["id"],
            baseline["payload"] if baseline is not None else {},
            cur=cur,
        )
    _log_unavailable(stima_id, "internal_supply", outcome)
    return outcome


def _combined_outcomes(
    microzone: dict[str, Any], internal_supply: dict[str, Any]
) -> dict[str, Any]:
    return {
        "watch_id": microzone.get("watch_id") or internal_supply.get("watch_id"),
        "microzone": microzone,
        "internal_supply": internal_supply,
    }


def collect_internal_signals_for_stima(stima_id: int) -> dict[str, Any]:
    microzone = collect_microzone_market_signal_for_stima(stima_id)
    internal_supply = collect_internal_supply_signal_for_stima(stima_id)
    return _combined_outcomes(microzone, internal_supply)


def _failed_collector_outcome() -> dict[str, Any]:
    return {"status": "failed", "watch_id": None, "observation": None}


def _failed_watch_outcome() -> dict[str, Any]:
    return _combined_outcomes(_failed_collector_outcome(), _failed_collector_outcome())


def _log_collector_failure(stima_id: int, collector: str, exc: Exception) -> None:
    logger.error(
        "property_watch_collector_failed stima_id=%s collector=%s error_type=%s",
        stima_id,
        collector,
        type(exc).__name__,
    )


def safe_collect_internal_signals_for_stima(stima_id: int) -> dict[str, Any]:
    try:
        microzone = collect_microzone_market_signal_for_stima(stima_id)
    except (ValidationError, WatchNotFoundError):
        raise
    except Exception as exc:  # noqa: BLE001 - collector-level fault isolation
        _log_collector_failure(stima_id, "microzone", exc)
        microzone = _failed_collector_outcome()

    try:
        internal_supply = collect_internal_supply_signal_for_stima(stima_id)
    except (ValidationError, WatchNotFoundError):
        raise
    except Exception as exc:  # noqa: BLE001 - collector-level fault isolation
        _log_collector_failure(stima_id, "internal_supply", exc)
        internal_supply = _failed_collector_outcome()

    return _combined_outcomes(microzone, internal_supply)


def _summarize_collector_statuses(outcomes: list[dict[str, Any]]) -> dict[str, int]:
    summary = {"written": 0, "unchanged": 0, "unavailable": 0, "failed": 0}
    for outcome in outcomes:
        for collector in ("microzone", "internal_supply"):
            status = outcome[collector]["status"]
            if status in {"baseline_unavailable", "source_unavailable"}:
                summary["unavailable"] += 1
            elif status in summary:
                summary[status] += 1
            else:
                summary["failed"] += 1
    return summary


def collect_internal_signals_for_active_watches() -> dict[str, Any]:
    outcomes = []
    for stima_id in repository.list_active_watch_stima_ids():
        try:
            outcome = safe_collect_internal_signals_for_stima(stima_id)
        except (ValidationError, WatchNotFoundError) as exc:
            logger.error(
                "property_watch_active_batch_item_failed stima_id=%s error_type=%s",
                stima_id,
                type(exc).__name__,
            )
            outcome = _failed_watch_outcome()
        outcomes.append({"stima_id": stima_id, **outcome})
    return {
        "processed": len(outcomes),
        **_summarize_collector_statuses(outcomes),
        "outcomes": outcomes,
    }


def _latest_observation_of_type(
    observations: list[dict[str, Any]], observation_types: set[str]
) -> dict[str, Any] | None:
    return next(
        (
            observation
            for observation in reversed(observations)
            if observation["observation_type"] in observation_types
        ),
        None,
    )


def get_current_watch_state(stima_id: int) -> dict[str, Any]:
    watch = get_watch_for_stima(stima_id)
    observations = repository.list_observations(watch["id"])
    baseline = next(
        (item for item in observations if item["observation_type"] == "watch_started"),
        None,
    )
    baseline_payload = baseline.get("payload", {}) if baseline is not None else {}
    baseline_payload = baseline_payload if isinstance(baseline_payload, dict) else {}

    microzone_changes = [
        item
        for item in observations
        if item["observation_type"] == "microzone_price_changed"
    ]
    latest_microzone_change = _latest_observation_of_type(
        observations,
        {"microzone_price_changed"},
    )
    latest_microzone_payload = (
        latest_microzone_change.get("payload", {})
        if latest_microzone_change is not None
        else {}
    )
    latest_microzone_payload = (
        latest_microzone_payload
        if isinstance(latest_microzone_payload, dict)
        else {}
    )
    microzone_reference = (
        {
            "prezzo_mq_base": baseline_payload.get("prezzo_mq_base"),
            "current": latest_microzone_payload.get(
                "current",
                baseline_payload.get("prezzo_mq_base"),
            ),
            "latest_change": latest_microzone_change,
            "observed_at": (
                latest_microzone_change.get("observed_at")
                if latest_microzone_change is not None
                else None
            ),
            "observation_count": len(microzone_changes),
        }
        if baseline is not None
        else None
    )

    supply_observations = [
        item
        for item in observations
        if item["observation_type"]
        in {"internal_supply_snapshot", "internal_supply_changed"}
    ]
    latest_supply_observation = _latest_observation_of_type(
        observations,
        {"internal_supply_snapshot", "internal_supply_changed"},
    )
    latest_supply_payload = (
        latest_supply_observation.get("payload", {})
        if latest_supply_observation is not None
        else {}
    )
    latest_supply_payload = (
        latest_supply_payload if isinstance(latest_supply_payload, dict) else {}
    )
    internal_supply = (
        {
            "current_count": latest_supply_payload.get("current_count"),
            "latest_observation": latest_supply_observation,
            "observed_at": latest_supply_observation.get("observed_at"),
            "observation_count": len(supply_observations),
        }
        if latest_supply_observation is not None
        else None
    )
    return {
        "watch": watch,
        "baseline": baseline,
        "microzone_reference": microzone_reference,
        "internal_supply": internal_supply,
        "observation_count": len(observations),
        "observations": observations,
        "computed_at": datetime.now(timezone.utc),
    }
