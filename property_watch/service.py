"""Application service for idempotent, append-only Property Watch events."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from . import buyer_pressure, buyer_pressure_score, repository
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
            agency_id=context["watch"]["agency_id"],
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


def _buyer_pressure_outcome_from_inputs(stima_id: int, inputs: dict[str, Any]) -> dict[str, Any]:
    """Metrics and storage from an already-read input snapshot.

    P26-6B: extracted so the scoped and ctx-less paths differ only in which
    buyers they are allowed to read, never in how pressure is computed or
    stored.
    """
    metrics = buyer_pressure.calculate_buyer_pressure_metrics(
        inputs["buyers"], inputs["baseline_payload"], inputs["collection_time"]
    )
    if metrics is None or inputs["baseline_observation_id"] is None:
        return {
            "status": "baseline_unavailable",
            "watch_id": inputs["watch_id"],
            "observation": None,
        }
    outcome = repository.store_buyer_pressure_metrics(
        stima_id=stima_id,
        watch_id=inputs["watch_id"],
        baseline_observation_id=inputs["baseline_observation_id"],
        metrics=metrics,
        observed_at=inputs["collection_time"],
    )
    if outcome is None:
        raise WatchNotFoundError(
            f"active property watch for stima {stima_id} not found"
        )
    return outcome


def collect_buyer_pressure_for_stima(stima_id: int) -> dict[str, Any]:
    _validate_stima_id(stima_id)
    inputs = repository.get_buyer_pressure_inputs(stima_id)
    if inputs is None:
        raise WatchNotFoundError(f"active property watch for stima {stima_id} not found")
    return _buyer_pressure_outcome_from_inputs(stima_id, inputs)


def safe_collect_buyer_pressure_for_stima(stima_id: int) -> dict[str, Any]:
    try:
        return collect_buyer_pressure_for_stima(stima_id)
    except (ValidationError, WatchNotFoundError):
        raise
    except Exception as exc:  # noqa: BLE001 - collector fault boundary
        logger.error(
            "property_watch_buyer_pressure_failed stima_id=%s error_type=%s",
            stima_id,
            type(exc).__name__,
        )
        return {"status": "failed", "watch_id": None, "observation": None}


def collect_buyer_pressure_for_active_watches() -> dict[str, Any]:
    outcomes = []
    for stima_id in repository.list_active_watch_stima_ids():
        try:
            outcome = safe_collect_buyer_pressure_for_stima(stima_id)
        except (ValidationError, WatchNotFoundError) as exc:
            logger.error(
                "property_watch_buyer_pressure_batch_item_failed "
                "stima_id=%s error_type=%s",
                stima_id,
                type(exc).__name__,
            )
            outcome = {"status": "failed", "watch_id": None, "observation": None}
        outcomes.append({"stima_id": stima_id, **outcome})
    totals = {
        "written": 0,
        "unchanged": 0,
        "unavailable": 0,
        "superseded": 0,
        "failed": 0,
    }
    for outcome in outcomes:
        status = outcome["status"]
        if status == "baseline_unavailable":
            totals["unavailable"] += 1
        elif status in totals:
            totals[status] += 1
        else:
            totals["failed"] += 1
    return {"processed": len(outcomes), **totals, "outcomes": outcomes}


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


def _watch_state_from_observations(watch: dict[str, Any], observations: list[dict[str, Any]]) -> dict[str, Any]:
    """The state view, built from an already-read watch and its observations.

    P26-6B: extracted so the scoped and ctx-less paths differ only in which
    rows they may read, never in how the view is assembled.
    """
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
    buyer_pressure_observations = [
        item
        for item in observations
        if item["observation_type"]
        in {"buyer_pressure_snapshot", "buyer_pressure_changed"}
    ]
    latest_buyer_pressure = _latest_observation_of_type(
        observations, {"buyer_pressure_snapshot", "buyer_pressure_changed"}
    )
    buyer_pressure_metrics = None
    buyer_pressure_insight = None
    if latest_buyer_pressure is not None:
        try:
            metrics = buyer_pressure.canonicalize_metrics(latest_buyer_pressure.get("payload"))
            observed_at = latest_buyer_pressure.get("observed_at")
            if observed_at is None:
                raise ValueError("buyer pressure observation timestamp is required")
            buyer_pressure_insight = buyer_pressure_score.derive_buyer_pressure_insight(
                metrics
            )
            buyer_pressure_metrics = {
                **metrics,
                "latest_observation": latest_buyer_pressure,
                "observed_at": observed_at,
                "observation_count": len(buyer_pressure_observations),
            }
        except ValueError as exc:
            logger.error(
                "property_watch_buyer_pressure_state_invalid "
                "stima_id=%s watch_id=%s error_type=%s",
                watch.get("stima_id"),
                watch.get("id"),
                type(exc).__name__,
            )
    return {
        "watch": watch,
        "baseline": baseline,
        "microzone_reference": microzone_reference,
        "internal_supply": internal_supply,
        "buyer_pressure_metrics": buyer_pressure_metrics,
        "buyer_pressure_insight": buyer_pressure_insight,
        "observation_count": len(observations),
        "observations": observations,
        "computed_at": datetime.now(timezone.utc),
    }


def get_current_watch_state(stima_id: int) -> dict[str, Any]:
    watch = get_watch_for_stima(stima_id)
    observations = repository.list_observations(watch["id"])
    return _watch_state_from_observations(watch, observations)


# ---------------------------------------------------------------------------
# P26-6B agency scoping.
#
# Three shapes, because there are three kinds of caller - the same three
# P26-6A settled on in followup.service:
#
#   *_scoped(ctx, ...)       the HTTP routes, which carry an operator context
#   *_for_agency(agency_id)  one bounded cycle of a server-only batch, where
#                            there is no operator to carry one
#   *_for_all_agencies()     the orchestrator, which iterates
#                            `list_active_agency_ids()` and runs the bounded
#                            cycle once per tenant
#
# The orchestrator is what makes the loop the source of the tenant predicate:
# no path added by P26-6B sweeps active watches globally and filters the
# results afterwards.
#
# It is a claim about the paths this block adds, not about the module. The
# ctx-less batches above still sweep every active watch, and they are
# untouched on purpose: the public estimation funnel reaches
# `safe_ensure_watch_for_stima` with no operator at all, and the historical
# tests call the ctx-less collectors directly.
# ---------------------------------------------------------------------------

class _AgencyScope:
    """A minimal scope for a server-only batch cycle.

    Deliberately not an OperatorContext: there is no operator behind a batch,
    and constructing one would claim an authenticated principal that does not
    exist. It exposes only `require_agency`, which is all the scoped functions
    ask of a scope.
    """

    __slots__ = ("_agency_id",)

    def __init__(self, agency_id: int) -> None:
        self._agency_id = agency_id

    def require_agency(self) -> int:
        return self._agency_id


def _baseline_for_stima_scoped(ctx, stima_id: int) -> dict[str, Any]:
    stima = repository.get_stima_baseline_data_scoped(ctx, stima_id)
    if stima is None:
        raise StimaNotFoundError(f"stima {stima_id} not found")
    completed = repository.get_stima_completed_valuation_scoped(ctx, stima_id)
    if completed is None:
        raise ValidationError(f"completed valuation not found for stima {stima_id}")
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
    return baseline


def ensure_watch_for_stima_scoped(ctx, stima_id: int) -> dict[str, dict[str, Any]]:
    _validate_stima_id(stima_id)
    baseline = _baseline_for_stima_scoped(ctx, stima_id)
    return repository.ensure_watch_with_baseline_scoped(ctx, stima_id, baseline)


def get_watch_for_stima_scoped(ctx, stima_id: int) -> dict[str, Any]:
    _validate_stima_id(stima_id)
    watch = repository.get_watch_for_stima_scoped(ctx, stima_id)
    if watch is None:
        raise WatchNotFoundError(f"property watch for stima {stima_id} not found")
    return watch


def collect_microzone_market_signal_for_stima_scoped(ctx, stima_id: int) -> dict[str, Any]:
    _validate_stima_id(stima_id)
    agency_id = ctx.require_agency()
    with repository.property_watch_cursor(commit=True) as (_, cur):
        context = repository.get_collection_context_for_update_scoped(cur, stima_id, agency_id)
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


def collect_internal_supply_signal_for_stima_scoped(ctx, stima_id: int) -> dict[str, Any]:
    _validate_stima_id(stima_id)
    agency_id = ctx.require_agency()
    with repository.property_watch_cursor(commit=True) as (_, cur):
        context = repository.get_collection_context_for_update_scoped(cur, stima_id, agency_id)
        if context is None:
            raise WatchNotFoundError(f"active property watch for stima {stima_id} not found")
        baseline = context["baseline"]
        outcome = repository.collect_internal_supply_change(
            context["watch"]["id"],
            baseline["payload"] if baseline is not None else {},
            cur=cur,
            agency_id=agency_id,
        )
    _log_unavailable(stima_id, "internal_supply", outcome)
    return outcome


def collect_internal_signals_for_stima_scoped(ctx, stima_id: int) -> dict[str, Any]:
    """Both collectors, with the per-collector fault boundary preserved.

    An earlier version of this ran both under one lock. That is tidier and
    wrong: the legacy contract is that a microzone failure must not stop the
    supply collector, and folding them into one transaction would have made one
    bad collector abort the other - a fault-isolation regression that no agency
    predicate would have revealed.
    """
    try:
        microzone = collect_microzone_market_signal_for_stima_scoped(ctx, stima_id)
    except (ValidationError, WatchNotFoundError):
        raise
    except Exception as exc:  # noqa: BLE001 - collector-level fault isolation
        _log_collector_failure(stima_id, "microzone", exc)
        microzone = _failed_collector_outcome()

    try:
        internal_supply = collect_internal_supply_signal_for_stima_scoped(ctx, stima_id)
    except (ValidationError, WatchNotFoundError):
        raise
    except Exception as exc:  # noqa: BLE001 - collector-level fault isolation
        _log_collector_failure(stima_id, "internal_supply", exc)
        internal_supply = _failed_collector_outcome()

    return _combined_outcomes(microzone, internal_supply)


def collect_internal_signals_for_active_watches_for_agency(agency_id: int) -> dict[str, Any]:
    """One bounded cycle of the internal-signals batch."""
    scope = _AgencyScope(agency_id)
    outcomes = []
    for stima_id in repository.list_active_watch_stima_ids_for_agency(agency_id):
        try:
            outcome = collect_internal_signals_for_stima_scoped(scope, stima_id)
        except (ValidationError, WatchNotFoundError) as exc:
            # Same message and arguments as the ctx-less batch: the log shape is
            # a contract other tooling reads, and the agency is already implied
            # by which cycle emitted it.
            logger.error(
                "property_watch_active_batch_item_failed stima_id=%s error_type=%s",
                stima_id,
                type(exc).__name__,
            )
            outcome = _failed_watch_outcome()
        outcomes.append({"stima_id": stima_id, **outcome})
    # The result shape is unchanged from the ctx-less batch: the caller asked
    # for one agency, so echoing it back adds nothing and would break every
    # existing consumer of this contract.
    return {
        "processed": len(outcomes),
        **_summarize_collector_statuses(outcomes),
        "outcomes": outcomes,
    }


def collect_internal_signals_for_active_watches_scoped(ctx) -> dict[str, Any]:
    return collect_internal_signals_for_active_watches_for_agency(ctx.require_agency())


def collect_buyer_pressure_for_stima_scoped(ctx, stima_id: int) -> dict[str, Any]:
    """Buyer pressure for one watch, computed from this agency's buyers only."""
    _validate_stima_id(stima_id)
    agency_id = ctx.require_agency()
    inputs = repository.get_buyer_pressure_inputs_for_agency(stima_id, agency_id)
    if inputs is None:
        raise WatchNotFoundError(f"active property watch for stima {stima_id} not found")
    return _buyer_pressure_outcome_from_inputs(stima_id, inputs)


def safe_collect_buyer_pressure_for_stima_scoped(ctx, stima_id: int) -> dict[str, Any]:
    try:
        return collect_buyer_pressure_for_stima_scoped(ctx, stima_id)
    except (ValidationError, WatchNotFoundError, StimaNotFoundError):
        raise
    except Exception as exc:  # noqa: BLE001 - collector fault boundary
        logger.error(
            "property_watch_buyer_pressure_failed stima_id=%s error_type=%s",
            stima_id,
            type(exc).__name__,
        )
        return {"status": "failed", "watch_id": None, "observation": None}


def collect_buyer_pressure_for_active_watches_for_agency(agency_id: int) -> dict[str, Any]:
    """One bounded cycle of the buyer-pressure batch."""
    # Through the per-watch safe collector, not around it: one watch failing
    # must not end the cycle, which is the boundary the ctx-less batch keeps and
    # an inlined version would have quietly removed.
    scope = _AgencyScope(agency_id)
    outcomes = []
    for stima_id in repository.list_active_watch_stima_ids_for_agency(agency_id):
        try:
            outcome = safe_collect_buyer_pressure_for_stima_scoped(scope, stima_id)
        except (ValidationError, WatchNotFoundError) as exc:
            logger.error(
                "property_watch_buyer_pressure_batch_item_failed "
                "stima_id=%s error_type=%s",
                stima_id,
                type(exc).__name__,
            )
            outcome = {"status": "failed", "watch_id": None, "observation": None}
        outcomes.append({"stima_id": stima_id, **outcome})
    totals = {"written": 0, "unchanged": 0, "unavailable": 0, "superseded": 0, "failed": 0}
    for outcome in outcomes:
        status = outcome["status"]
        if status == "baseline_unavailable":
            totals["unavailable"] += 1
        elif status in totals:
            totals[status] += 1
        else:
            totals["failed"] += 1
    return {"processed": len(outcomes), **totals, "outcomes": outcomes}


def collect_buyer_pressure_for_active_watches_scoped(ctx) -> dict[str, Any]:
    return collect_buyer_pressure_for_active_watches_for_agency(ctx.require_agency())


# ---------------------------------------------------------------------------
# Server-only orchestrators, in the shape P26-6A settled on for
# followup.service.run_temporal_escalation_scan_for_all_agencies.
#
# They exist so a background job never has to run the sweep without a tenant
# predicate: the loop over agencies is what supplies it. Neither is wired to a
# scheduler - P23's frozen model has none - and neither is reachable over HTTP,
# where the route already carries a context.
# ---------------------------------------------------------------------------

def _for_all_agencies(cycle, totals_keys: tuple[str, ...]) -> dict[str, Any]:
    """Run one bounded cycle per active agency and sum the reported totals."""
    runs = [cycle(agency_id) for agency_id in repository.list_active_agency_ids()]
    summary = {
        "agencies": len(runs),
        "processed": sum(run["processed"] for run in runs),
    }
    for key in totals_keys:
        summary[key] = sum(run[key] for run in runs)
    summary["runs"] = runs
    return summary


def collect_internal_signals_for_all_agencies() -> dict[str, Any]:
    return _for_all_agencies(
        collect_internal_signals_for_active_watches_for_agency,
        ("written", "unchanged", "unavailable", "failed"),
    )


def collect_buyer_pressure_for_all_agencies() -> dict[str, Any]:
    return _for_all_agencies(
        collect_buyer_pressure_for_active_watches_for_agency,
        ("written", "unchanged", "unavailable", "superseded", "failed"),
    )


def get_current_watch_state_scoped(ctx, stima_id: int) -> dict[str, Any]:
    """The watch state view, reachable only through a watch this agency owns."""
    watch = get_watch_for_stima_scoped(ctx, stima_id)
    observations = repository.list_observations_scoped(ctx, watch["id"])
    return _watch_state_from_observations(watch, observations)
