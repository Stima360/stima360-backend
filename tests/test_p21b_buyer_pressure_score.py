from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError as PydanticValidationError

from property_watch import buyer_pressure, buyer_pressure_score, repository, service
from property_watch.schemas import PropertyWatchState


DISCLAIMER = (
    "Indicatore interno basato su richieste BUY attive e criteri MATCH; "
    "non garantisce la vendita né l’interesse per lo specifico immobile."
)


def metrics(**overrides):
    value = {
        "evaluated_buyers": 18,
        "compatible_buyers": 13,
        "highly_compatible_buyers": 5,
        "recent_compatible_buyers_30d": 7,
        "average_match_score": Decimal("72.35"),
        "maximum_match_score": Decimal("91.40"),
        "average_budget": Decimal("245000.00"),
        "algorithm_version": "match-0.1",
    }
    value.update(overrides)
    return value


def test_derive_buyer_pressure_insight_returns_exact_87_point_example():
    assert buyer_pressure_score.derive_buyer_pressure_insight(metrics()) == {
        "score_version": "buyer-pressure-score-1.0",
        "score": 87,
        "band": "high",
        "band_label": "Domanda alta",
        "headline": "DOMANDA ALTA — 87/100",
        "message": (
            "Nel database STIMA360 è presente una domanda elevata di acquirenti "
            "compatibili per immobili con caratteristiche simili."
        ),
        "disclaimer": DISCLAIMER,
        "factors": [
            {
                "code": "compatible_volume",
                "label": "Buyer compatibili",
                "points": 30,
                "max_points": 30,
            },
            {
                "code": "highly_compatible_volume",
                "label": "Buyer altamente compatibili",
                "points": 25,
                "max_points": 25,
            },
            {
                "code": "recent_compatible_activity",
                "label": "Buyer compatibili attivi negli ultimi 30 giorni",
                "points": 18,
                "max_points": 20,
            },
            {
                "code": "average_match_quality",
                "label": "Qualità media dei match compatibili",
                "points": 6,
                "max_points": 15,
            },
            {
                "code": "maximum_match_quality",
                "label": "Migliore match compatibile",
                "points": 8,
                "max_points": 10,
            },
        ],
    }


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            metrics(
                evaluated_buyers=0,
                compatible_buyers=0,
                highly_compatible_buyers=0,
                recent_compatible_buyers_30d=0,
                average_match_score=None,
                maximum_match_score=None,
                average_budget=None,
            ),
            (
                0,
                "none",
                "Nessuna domanda rilevata",
                "NESSUNA DOMANDA RILEVATA — 0/100",
                "Al momento non risultano richieste BUY compatibili con i dati disponibili.",
            ),
        ),
        (
            metrics(
                evaluated_buyers=1,
                compatible_buyers=1,
                highly_compatible_buyers=0,
                recent_compatible_buyers_30d=0,
                average_match_score=Decimal("55"),
                maximum_match_score=Decimal("55"),
            ),
            (
                3,
                "low",
                "Domanda bassa",
                "DOMANDA BASSA — 3/100",
                "Nel database STIMA360 risultano alcune compatibilità, ma la "
                "pressione della domanda è ancora limitata.",
            ),
        ),
        (
            metrics(
                evaluated_buyers=10,
                compatible_buyers=10,
                highly_compatible_buyers=0,
                recent_compatible_buyers_30d=0,
                average_match_score=Decimal("55"),
                maximum_match_score=Decimal("55"),
            ),
            (
                30,
                "low",
                "Domanda bassa",
                "DOMANDA BASSA — 30/100",
                "Nel database STIMA360 risultano alcune compatibilità, ma la "
                "pressione della domanda è ancora limitata.",
            ),
        ),
        (
            metrics(
                evaluated_buyers=10,
                compatible_buyers=10,
                highly_compatible_buyers=1,
                recent_compatible_buyers_30d=0,
                average_match_score=Decimal("55"),
                maximum_match_score=Decimal("55"),
            ),
            (
                35,
                "medium",
                "Domanda media",
                "DOMANDA MEDIA — 35/100",
                "Nel database STIMA360 è presente una domanda concreta per "
                "immobili con caratteristiche simili.",
            ),
        ),
        (
            metrics(
                evaluated_buyers=10,
                compatible_buyers=10,
                highly_compatible_buyers=5,
                recent_compatible_buyers_30d=4,
                average_match_score=Decimal("55"),
                maximum_match_score=Decimal("55"),
            ),
            (
                65,
                "high",
                "Domanda alta",
                "DOMANDA ALTA — 65/100",
                "Nel database STIMA360 è presente una domanda elevata di "
                "acquirenti compatibili per immobili con caratteristiche simili.",
            ),
        ),
        (
            metrics(
                evaluated_buyers=20,
                compatible_buyers=20,
                highly_compatible_buyers=10,
                recent_compatible_buyers_30d=20,
                average_match_score=Decimal("100"),
                maximum_match_score=Decimal("100"),
            ),
            (
                100,
                "high",
                "Domanda alta",
                "DOMANDA ALTA — 100/100",
                "Nel database STIMA360 è presente una domanda elevata di "
                "acquirenti compatibili per immobili con caratteristiche simili.",
            ),
        ),
    ],
)
def test_score_bands_and_approved_copy(source, expected):
    insight = buyer_pressure_score.derive_buyer_pressure_insight(source)

    assert (
        insight["score"],
        insight["band"],
        insight["band_label"],
        insight["headline"],
        insight["message"],
    ) == expected
    assert insight["disclaimer"] == DISCLAIMER


def test_score_rounds_components_independently_and_caps_counts():
    source = metrics(
        evaluated_buyers=100,
        compatible_buyers=100,
        highly_compatible_buyers=100,
        recent_compatible_buyers_30d=100,
        average_match_score=Decimal("56.50"),
        maximum_match_score=Decimal("57.25"),
    )

    insight = buyer_pressure_score.derive_buyer_pressure_insight(source)

    assert [factor["points"] for factor in insight["factors"]] == [30, 25, 20, 1, 1]
    assert insight["score"] == 77
    assert sum(factor["points"] for factor in insight["factors"]) == insight["score"]
    assert all(set(factor) == {"code", "label", "points", "max_points"} for factor in insight["factors"])


@pytest.mark.parametrize(
    "source",
    [
        metrics(compatible_buyers=19),
        metrics(highly_compatible_buyers=14),
        metrics(recent_compatible_buyers_30d=14),
        metrics(
            compatible_buyers=0,
            highly_compatible_buyers=0,
            recent_compatible_buyers_30d=0,
            average_match_score=Decimal("55"),
            maximum_match_score=Decimal("55"),
        ),
        metrics(average_match_score=None),
        metrics(average_match_score=Decimal("54.99")),
        metrics(maximum_match_score=Decimal("101")),
        metrics(average_match_score=Decimal("90"), maximum_match_score=Decimal("89")),
        metrics(average_budget=Decimal("-1")),
    ],
)
def test_score_rejects_corrupt_p21a_metric_invariants(source):
    with pytest.raises(ValueError):
        buyer_pressure_score.derive_buyer_pressure_insight(source)


def test_score_reuses_p21a_canonicalization_before_derivation(monkeypatch):
    called = []
    original = buyer_pressure_score.canonicalize_metrics

    def canonicalize(payload):
        called.append(payload)
        return original(payload)

    monkeypatch.setattr(buyer_pressure_score, "canonicalize_metrics", canonicalize)

    buyer_pressure_score.derive_buyer_pressure_insight(metrics())

    assert called == [metrics()]


def _observation(payload, observed_at):
    return {
        "id": 11,
        "watch_id": 3,
        "observation_type": "buyer_pressure_snapshot",
        "source": "internal",
        "payload": payload,
        "idempotency_key": "property_watch:buyer_pressure_snapshot:watch:3:v1",
        "observed_at": observed_at,
        "created_at": observed_at,
    }


def test_current_state_derives_insight_without_collection_or_writes(monkeypatch):
    observed_at = datetime(2026, 9, 3, tzinfo=timezone.utc)
    observation = _observation(metrics(), observed_at)
    monkeypatch.setattr(
        repository,
        "get_watch_for_stima",
        lambda _stima_id: {"id": 3, "stima_id": 501, "status": "active"},
    )
    monkeypatch.setattr(repository, "list_observations", lambda _watch_id: [observation])
    for module, names in (
        (
            service,
            (
                "collect_buyer_pressure_for_stima",
                "safe_collect_buyer_pressure_for_stima",
                "collect_buyer_pressure_for_active_watches",
                "collect_internal_signals_for_stima",
            ),
        ),
        (
            repository,
            (
                "get_buyer_pressure_inputs",
                "store_buyer_pressure_metrics",
                "insert_observation",
            ),
        ),
    ):
        for name in names:
            monkeypatch.setattr(
                module,
                name,
                lambda *_args, **_kwargs: pytest.fail(f"GET invoked {name}"),
            )

    state = service.get_current_watch_state(501)

    assert state["buyer_pressure_metrics"]["observed_at"] == observed_at
    assert state["buyer_pressure_insight"]["score"] == 87
    assert state["buyer_pressure_insight"]["score_version"] == "buyer-pressure-score-1.0"


def test_current_state_hides_corrupt_metrics_and_logs_only_identifiers(monkeypatch, caplog):
    observed_at = datetime(2026, 9, 3, tzinfo=timezone.utc)
    invalid = metrics(compatible_buyers=19)
    observation = _observation(invalid, observed_at)
    monkeypatch.setattr(
        repository,
        "get_watch_for_stima",
        lambda _stima_id: {"id": 3, "stima_id": 501, "status": "active"},
    )
    monkeypatch.setattr(repository, "list_observations", lambda _watch_id: [observation])

    state = service.get_current_watch_state(501)

    assert state["buyer_pressure_metrics"] is None
    assert state["buyer_pressure_insight"] is None
    record = caplog.records[-1]
    assert record.args == (501, 3, "ValueError")
    assert str(invalid) not in record.getMessage()


def test_property_watch_state_accepts_only_strict_insight_shape():
    observed_at = datetime(2026, 9, 3, tzinfo=timezone.utc)
    observation = _observation(metrics(), observed_at)
    state = PropertyWatchState(
        watch={"id": 3},
        baseline=None,
        microzone_reference=None,
        internal_supply=None,
        buyer_pressure_metrics={
            **metrics(),
            "latest_observation": observation,
            "observed_at": observed_at,
            "observation_count": 1,
        },
        buyer_pressure_insight=buyer_pressure_score.derive_buyer_pressure_insight(metrics()),
        observation_count=1,
        observations=[observation],
        computed_at=observed_at,
    )

    assert state.buyer_pressure_insight.score == 87
    invalid_insight = buyer_pressure_score.derive_buyer_pressure_insight(metrics())
    invalid_insight["unexpected"] = True
    with pytest.raises(PydanticValidationError):
        PropertyWatchState(
            **{
                **state.dict(),
                "buyer_pressure_insight": invalid_insight,
            }
        )
