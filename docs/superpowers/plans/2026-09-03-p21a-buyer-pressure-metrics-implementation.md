# P21-A Buyer Pressure Metrics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking. Do not dispatch subagents for this
> tightly coupled implementation.

**Goal:** Calculate and persist anonymous, raw BUY demand metrics for each
active Property Watch by applying the existing pure MATCH engine to the
watch's immutable valuation baseline.

**Architecture:** Property Watch owns collection, locking, append-only history,
manual routes, and read-model derivation. A new pure
`property_watch/buyer_pressure.py` module adapts the baseline and aggregates
MATCH results; repository code performs privacy-minimized BUY reads and one
atomic observation write without touching normal MATCH persistence.

**Tech Stack:** Python 3, FastAPI, Pydantic, psycopg2/PostgreSQL, Decimal,
pytest.

**Spec:**
`docs/superpowers/specs/2026-09-03-p21a-buyer-pressure-metrics-design.md`

## Global Constraints

- Start from `origin/core-0.1-test` at exactly
  `ec951b2be944b2f64d124a7d7a22c899424e0b3d`; stop on any mismatch.
- Read `AGENTS.md` and the complete spec before implementation.
- Invoke only `match.engine.calculate`; never call any `match.repository`
  calculation/write function.
- Do not create a PROPERTY row or write `matches`, `match_runs`,
  `match_requirement_results`, BUY, PROPERTY, interactions, contacts, leads, or
  `stime`.
- Persist only the approved eight aggregate keys in
  `property_watch_observations` with `source="internal"`.
- Do not modify `main.py`, migrations, BUY/MATCH production files, UI,
  schedulers, external sources, P22/P23, deployment, or PROD behavior.
- Do not add score, band, trend, recommendation, messaging, or P21-B behavior.
- Log only `stima_id`, `watch_id` when known, collector/status/error
  classification; never log exception messages, payloads, criteria, budgets,
  or PII.
- Use TDD: run each named test RED before its minimum implementation, then
  GREEN.
- The executing worker must not commit, push, create a PR, merge, or deploy.
  Giorgio performs the final commit and push manually after review.
- At every checkpoint run `git diff --check` and `git status --short`; no
  checkpoint contains a commit step.

## Planned file map

| Path | Action | Responsibility |
| --- | --- | --- |
| `property_watch/buyer_pressure.py` | Create | Pure baseline adaptation, MATCH execution, recency, metric canonicalization, and digest. |
| `property_watch/repository.py` | Modify | Coherent privacy-minimized input snapshot and atomic compare/insert. |
| `property_watch/service.py` | Modify | Strict/safe collector, batch isolation/totals, derived state. |
| `property_watch/router.py` | Modify | Two body-free ADMIN-inherited POST routes. |
| `property_watch/schemas.py` | Modify | P21-A outcome, batch, and derived-state models. |
| `tests/test_p21a_buyer_pressure_metrics.py` | Create | Focused pure/repository/service/API TDD coverage. |
| `tests/test_next2_router_hardening.py` | Modify only if exact inventory requires it | Mechanical Property Watch 4->6 and total 98->100 route-count update. |

---

### Task 1: Pure baseline adapter and canonical metric representation

**Files:**

- Create: `property_watch/buyer_pressure.py`
- Create: `tests/test_p21a_buyer_pressure_metrics.py`

**Interfaces:**

- Produces:
  `build_ephemeral_property(baseline_payload: dict[str, Any]) -> dict[str, Any] | None`
- Produces:
  `canonicalize_metrics(payload: dict[str, Any]) -> dict[str, Any]`
- Produces: `metrics_digest(payload: dict[str, Any]) -> str`
- Later tasks consume the exact five-field property dictionary and exact
  eight-field canonical metrics dictionary.

- [ ] **Step 1: Write failing adapter and canonicalization tests**

Create the focused test file with these imports and cases:

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

import pytest

from integration_p2_support import import_project_module
from property_watch import buyer_pressure, repository, service
from property_watch import router as property_watch_router
from property_watch.exceptions import ValidationError, WatchNotFoundError


def complete_baseline(**overrides):
    payload = {
        "comune": "Alba Adriatica",
        "microzona": "Nord",
        "tipologia": "Appartamento",
        "mq": Decimal("90"),
        "price_exact": Decimal("180000"),
        "prezzo_mq_base": Decimal("1500"),
        "base_mq": Decimal("1500"),
        "eur_mq_finale": Decimal("2000"),
    }
    payload.update(overrides)
    return payload


def test_ephemeral_property_uses_only_the_five_approved_baseline_fields():
    candidate = buyer_pressure.build_ephemeral_property(complete_baseline())

    assert candidate == {
        "city": "Alba Adriatica",
        "microzone": "Nord",
        "property_type": "Appartamento",
        "surface_sqm": Decimal("90"),
        "asking_price": Decimal("180000"),
    }
    assert not {
        "id", "province", "rooms", "address", "contact_id", "metadata",
        "prezzo_mq_base", "base_mq", "eur_mq_finale",
    } & candidate.keys()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("comune", ""),
        ("microzona", "   "),
        ("tipologia", None),
        ("mq", 0),
        ("mq", True),
        ("mq", Decimal("NaN")),
        ("price_exact", -1),
        ("price_exact", Decimal("Infinity")),
    ],
)
def test_ephemeral_property_rejects_unusable_required_baseline(field, value):
    assert buyer_pressure.build_ephemeral_property(
        complete_baseline(**{field: value})
    ) is None


def test_canonical_metrics_require_exact_keys_and_stable_numeric_forms():
    payload = {
        "evaluated_buyers": 2,
        "compatible_buyers": 1,
        "highly_compatible_buyers": 1,
        "recent_compatible_buyers_30d": 1,
        "average_match_score": Decimal("80.000"),
        "maximum_match_score": 80,
        "average_budget": Decimal("245000.005"),
        "algorithm_version": "match-0.1",
    }

    canonical = buyer_pressure.canonicalize_metrics(payload)

    assert canonical == {
        **{key: payload[key] for key in (
            "evaluated_buyers", "compatible_buyers",
            "highly_compatible_buyers", "recent_compatible_buyers_30d",
        )},
        "average_match_score": Decimal("80.00"),
        "maximum_match_score": Decimal("80.00"),
        "average_budget": Decimal("245000.01"),
        "algorithm_version": "match-0.1",
    }
    assert buyer_pressure.metrics_digest(payload) == buyer_pressure.metrics_digest(
        dict(reversed(list(payload.items())))
    )


def test_canonical_metrics_reject_extra_keys_booleans_and_non_finite_numbers():
    valid = {
        "evaluated_buyers": 0,
        "compatible_buyers": 0,
        "highly_compatible_buyers": 0,
        "recent_compatible_buyers_30d": 0,
        "average_match_score": None,
        "maximum_match_score": None,
        "average_budget": None,
        "algorithm_version": "match-0.1",
    }
    for changed in (
        {**valid, "buyer_ids": [1]},
        {**valid, "evaluated_buyers": True},
        {**valid, "average_budget": Decimal("NaN")},
    ):
        with pytest.raises(ValueError):
            buyer_pressure.canonicalize_metrics(changed)
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q \
  tests/test_p21a_buyer_pressure_metrics.py \
  -k 'ephemeral_property or canonical_metrics'
```

Expected: collection/import failure because
`property_watch.buyer_pressure` does not exist.

- [ ] **Step 3: Implement the pure adapter and canonicalization**

Create `property_watch/buyer_pressure.py` with these contracts:

```python
"""Pure P21-A Buyer Pressure calculation over Property Watch inputs."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
from typing import Any

from match.engine import calculate as calculate_match
from match.enums import ALGORITHM_VERSION
from match.readiness import buy_readiness


METRIC_KEYS = (
    "evaluated_buyers",
    "compatible_buyers",
    "highly_compatible_buyers",
    "recent_compatible_buyers_30d",
    "average_match_score",
    "maximum_match_score",
    "average_budget",
    "algorithm_version",
)
COUNT_KEYS = METRIC_KEYS[:4]
DECIMAL_KEYS = METRIC_KEYS[4:7]
TWO_PLACES = Decimal("0.01")


def _finite_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def build_ephemeral_property(
    baseline_payload: dict[str, Any],
) -> dict[str, Any] | None:
    if not isinstance(baseline_payload, dict):
        return None
    strings = {
        "city": baseline_payload.get("comune"),
        "microzone": baseline_payload.get("microzona"),
        "property_type": baseline_payload.get("tipologia"),
    }
    if any(not isinstance(value, str) or not value.strip() for value in strings.values()):
        return None
    surface = _finite_decimal(baseline_payload.get("mq"))
    price = _finite_decimal(baseline_payload.get("price_exact"))
    if surface is None or surface <= 0 or price is None or price <= 0:
        return None
    return {
        **strings,
        "surface_sqm": surface,
        "asking_price": price,
    }


def canonicalize_metrics(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != set(METRIC_KEYS):
        raise ValueError("buyer pressure metrics have an invalid key set")
    canonical: dict[str, Any] = {}
    for key in COUNT_KEYS:
        value = payload[key]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"{key} must be a non-negative integer")
        canonical[key] = value
    for key in DECIMAL_KEYS:
        value = payload[key]
        if value is None:
            canonical[key] = None
            continue
        decimal_value = _finite_decimal(value)
        if decimal_value is None or decimal_value < 0:
            raise ValueError(f"{key} must be finite and non-negative")
        canonical[key] = decimal_value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    version = payload["algorithm_version"]
    if not isinstance(version, str) or not version:
        raise ValueError("algorithm_version is required")
    canonical["algorithm_version"] = version
    return canonical


def metrics_digest(payload: dict[str, Any]) -> str:
    canonical = canonicalize_metrics(payload)
    serializable = {
        key: (
            format(value, ".2f") if isinstance(value, Decimal) else value
        )
        for key, value in canonical.items()
    }
    raw = json.dumps(
        serializable,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()
```

Keep the MATCH/readiness imports even before Task 2 so later code uses one
stable seam. Remove the currently unused `datetime`/`timezone` imports only if
Task 2 does not immediately follow in the same working session.

- [ ] **Step 4: Run the focused cases and verify GREEN**

Run the Step 2 command. Expected: all selected tests pass.

- [ ] **Step 5: Review checkpoint without commit**

Run:

```bash
git diff --check
git status --short
git diff -- property_watch/buyer_pressure.py \
  tests/test_p21a_buyer_pressure_metrics.py
```

Expected changed paths: exactly the new helper and focused test, plus the
already approved design/plan documents if they are present in this worktree.
Do not commit or push.

---

### Task 2: Pure MATCH aggregation, thresholds, recency, and budgets

**Files:**

- Modify: `property_watch/buyer_pressure.py`
- Modify: `tests/test_p21a_buyer_pressure_metrics.py`

**Interfaces:**

- Consumes: `build_ephemeral_property()` and `canonicalize_metrics()` from
  Task 1.
- Produces:
  `calculate_buyer_pressure_metrics(buy_requests: list[dict[str, Any]], baseline_payload: dict[str, Any], collection_time: datetime) -> dict[str, Any] | None`
- `None` means only `baseline_unavailable`; unexpected/malformed BUY or MATCH
  results raise and are handled by the service boundary in Task 5.

- [ ] **Step 1: Add failing aggregation tests**

Append tests that use a deterministic engine stub:

```python
def ready_buy(*, score, compatibility="compatible", hard_fails=0,
              last_activity_at=None, budget_target=None,
              budget_max=None, budget_min=None, request_id=1):
    return {
        "id": request_id,
        "status": "active",
        "archived_at": None,
        "budget_min": budget_min,
        "budget_target": budget_target,
        "budget_max": budget_max,
        "budget_flexibility_percent": Decimal("0"),
        "surface_min": Decimal("50"),
        "surface_target": None,
        "surface_max": None,
        "rooms_min": None,
        "bedrooms_min": None,
        "bathrooms_min": None,
        "locations": [],
        "typologies": [],
        "features": [],
        "last_activity_at": last_activity_at,
        "_match_result": {
            "score_total": score,
            "compatibility_status": compatibility,
            "hard_fail_count": hard_fails,
            "algorithm_version": "match-0.1",
        },
    }


def install_match_stub(monkeypatch):
    def calculate(request, prop):
        assert set(prop) == {
            "city", "microzone", "property_type", "surface_sqm", "asking_price"
        }
        return request["_match_result"]

    monkeypatch.setattr(buyer_pressure, "calculate_match", calculate)


def test_thresholds_hard_fails_recency_and_budget_fallback(monkeypatch):
    install_match_stub(monkeypatch)
    now = datetime(2026, 9, 3, 12, tzinfo=timezone.utc)
    buys = [
        ready_buy(score=54.99, last_activity_at=now, request_id=1),
        ready_buy(
            score=55,
            last_activity_at=now - timedelta(days=30),
            budget_target=Decimal("200000"),
            request_id=2,
        ),
        ready_buy(
            score=79.99,
            last_activity_at=now - timedelta(days=30, microseconds=1),
            budget_max=Decimal("240000"),
            request_id=3,
        ),
        ready_buy(
            score=80,
            last_activity_at=now,
            budget_min=Decimal("280000"),
            request_id=4,
        ),
        ready_buy(
            score=100,
            hard_fails=1,
            last_activity_at=now,
            budget_target=Decimal("999999"),
            request_id=5,
        ),
    ]

    metrics = buyer_pressure.calculate_buyer_pressure_metrics(
        buys, complete_baseline(), now
    )

    assert metrics == {
        "evaluated_buyers": 5,
        "compatible_buyers": 3,
        "highly_compatible_buyers": 1,
        "recent_compatible_buyers_30d": 2,
        "average_match_score": Decimal("71.66"),
        "maximum_match_score": Decimal("80.00"),
        "average_budget": Decimal("240000.00"),
        "algorithm_version": "match-0.1",
    }


def test_zero_ready_buyers_is_a_valid_zero_snapshot(monkeypatch):
    monkeypatch.setattr(
        buyer_pressure,
        "calculate_match",
        lambda *_args: pytest.fail("no BUY should be evaluated"),
    )
    metrics = buyer_pressure.calculate_buyer_pressure_metrics(
        [
            {"id": 1, "status": "paused", "archived_at": None,
             "locations": [], "typologies": [], "features": []},
            {"id": 2, "status": "active", "archived_at": None,
             "locations": [], "typologies": [], "features": []},
        ],
        complete_baseline(),
        datetime(2026, 9, 3, tzinfo=timezone.utc),
    )
    assert metrics == {
        "evaluated_buyers": 0,
        "compatible_buyers": 0,
        "highly_compatible_buyers": 0,
        "recent_compatible_buyers_30d": 0,
        "average_match_score": None,
        "maximum_match_score": None,
        "average_budget": None,
        "algorithm_version": "match-0.1",
    }


def test_malformed_match_result_fails_instead_of_writing_partial_metrics(
    monkeypatch,
):
    buy = ready_buy(
        score=80,
        last_activity_at=datetime(2026, 9, 3, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(
        buyer_pressure,
        "calculate_match",
        lambda *_args: {"score_total": Decimal("NaN")},
    )
    with pytest.raises((KeyError, ValueError)):
        buyer_pressure.calculate_buyer_pressure_metrics(
            [buy],
            complete_baseline(),
            datetime(2026, 9, 3, tzinfo=timezone.utc),
        )
```

Add a separate parameterized test proving `compatibility_status="incompatible"`
excludes scores 55, 80, and 100, and another proving a future
`last_activity_at` is recent.

- [ ] **Step 2: Run aggregation cases and verify RED**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q \
  tests/test_p21a_buyer_pressure_metrics.py \
  -k 'thresholds or zero_ready or malformed_match or incompatible or future'
```

Expected: FAIL because `calculate_buyer_pressure_metrics` is absent.

- [ ] **Step 3: Implement the deterministic aggregate**

Add to `buyer_pressure.py`:

```python
from datetime import timedelta


def _mean(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    return (sum(values, Decimal("0")) / Decimal(len(values))).quantize(
        TWO_PLACES, rounding=ROUND_HALF_UP
    )


def _last_activity_at(buy: dict[str, Any]) -> datetime:
    value = buy.get("last_activity_at")
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("last_activity_at must be timezone-aware")
    return value.astimezone(timezone.utc)


def _selected_budget(buy: dict[str, Any]) -> Decimal | None:
    for key in ("budget_target", "budget_max", "budget_min"):
        if buy.get(key) is not None:
            value = _finite_decimal(buy[key])
            if value is None or value < 0:
                raise ValueError(f"{key} must be finite and non-negative")
            return value
    return None


def calculate_buyer_pressure_metrics(
    buy_requests: list[dict[str, Any]],
    baseline_payload: dict[str, Any],
    collection_time: datetime,
) -> dict[str, Any] | None:
    prop = build_ephemeral_property(baseline_payload)
    if prop is None:
        return None
    if not isinstance(collection_time, datetime) or collection_time.tzinfo is None:
        raise ValueError("collection_time must be timezone-aware")
    cutoff = collection_time.astimezone(timezone.utc) - timedelta(days=30)
    evaluated = []
    for buy in buy_requests:
        if buy_readiness(buy)["can_match"]:
            evaluated.append((buy, calculate_match(buy, prop)))

    compatible: list[tuple[dict[str, Any], Decimal]] = []
    for buy, result in evaluated:
        score = _finite_decimal(result["score_total"])
        hard_fail_count = result["hard_fail_count"]
        compatibility = result["compatibility_status"]
        if score is None or not isinstance(hard_fail_count, int):
            raise ValueError("invalid MATCH result")
        if result["algorithm_version"] != ALGORITHM_VERSION:
            raise ValueError("unexpected MATCH algorithm version")
        if (
            hard_fail_count == 0
            and compatibility != "incompatible"
            and score >= Decimal("55")
        ):
            compatible.append((buy, score))

    scores = [score for _, score in compatible]
    budgets = [
        budget
        for buy, _ in compatible
        if (budget := _selected_budget(buy)) is not None
    ]
    raw = {
        "evaluated_buyers": len(evaluated),
        "compatible_buyers": len(compatible),
        "highly_compatible_buyers": sum(
            score >= Decimal("80") for _, score in compatible
        ),
        "recent_compatible_buyers_30d": sum(
            _last_activity_at(buy) >= cutoff for buy, _ in compatible
        ),
        "average_match_score": _mean(scores),
        "maximum_match_score": max(scores).quantize(
            TWO_PLACES, rounding=ROUND_HALF_UP
        ) if scores else None,
        "average_budget": _mean(budgets),
        "algorithm_version": ALGORITHM_VERSION,
    }
    return canonicalize_metrics(raw)
```

Do not retain `_match_result` or any BUY-level data in the returned dictionary.

- [ ] **Step 4: Run all pure helper tests and verify GREEN**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q \
  tests/test_p21a_buyer_pressure_metrics.py \
  -k 'ephemeral_property or canonical_metrics or thresholds or zero_ready or malformed_match or incompatible or future'
```

Expected: all selected tests pass.

- [ ] **Step 5: Review checkpoint without commit**

Run `git diff --check`, inspect the two Task 1/2 files, and recursively assert
in the test that the aggregate has exactly `set(METRIC_KEYS)`. Do not commit or
push.

---

### Task 3: Privacy-minimized coherent BUY input snapshot

**Files:**

- Modify: `property_watch/repository.py`
- Modify: `tests/test_p21a_buyer_pressure_metrics.py`

**Interfaces:**

- Produces:
  `get_buyer_pressure_inputs(stima_id: int) -> dict[str, Any] | None`
- Returned keys are exactly `watch_id`, `baseline_observation_id`,
  `baseline_payload`, `collection_time`, and `buyers`.
- `None` means no active watch with that `stima_id` at read time.

- [ ] **Step 1: Write failing repository snapshot tests**

Create a cursor/connection spy that records queries and returns, in order:

1. `collection_time`;
2. active watch plus baseline;
3. two active BUY parent rows;
4. location rows;
5. typology rows;
6. feature rows.

The parent rows must contain only the approved columns and
`last_activity_at`. Assert:

```python
result = repository.get_buyer_pressure_inputs(501)

assert result["watch_id"] == 3
assert result["baseline_observation_id"] == 10
assert [buy["id"] for buy in result["buyers"]] == [7, 11]
assert result["buyers"][0]["locations"] == [{
    "microzone": "Nord",
    "municipality": "Alba Adriatica",
    "province": "Teramo",
    "priority": 10,
    "is_required": True,
    "is_excluded": False,
}]
```

Join the captured SQL and assert it contains:

```python
assert "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY" in sql
assert "b.status = 'active'" in sql
assert "b.archived_at IS NULL" in sql
assert "ORDER BY b.id ASC" in sql
assert "MAX(i.occurred_at)" in sql
assert "ORDER BY buy_request_id ASC, id ASC" in sql
for forbidden in (
    "contact_id", "lead_id", "display_name", "email", "phone",
    "finance_notes", "b.notes", "b.metadata", "i.notes", "i.created_by",
):
    assert forbidden not in sql.lower()
```

Add cases for an absent/inactive watch returning `None`, a watch with no
baseline returning an input dictionary with null baseline fields, and zero
BUY rows skipping all child queries while returning `buyers=[]`.

- [ ] **Step 2: Run repository input tests and verify RED**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q \
  tests/test_p21a_buyer_pressure_metrics.py -k 'buyer_pressure_inputs'
```

Expected: FAIL because the repository function does not exist.

- [ ] **Step 3: Implement the read-only snapshot**

Add a repository function following this exact query order. Use
`property_watch_cursor()` without `commit=True`:

```python
def get_buyer_pressure_inputs(stima_id: int) -> dict[str, Any] | None:
    with property_watch_cursor() as (_, cur):
        cur.execute(
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
        )
        cur.execute("SELECT transaction_timestamp() AS collection_time")
        collection_time = cur.fetchone()["collection_time"]
        cur.execute(
            """
            SELECT w.id AS watch_id,
                   o.id AS baseline_observation_id,
                   o.payload AS baseline_payload
            FROM property_watches w
            LEFT JOIN LATERAL (
                SELECT id, payload
                FROM property_watch_observations
                WHERE watch_id = w.id
                  AND observation_type = 'watch_started'
                ORDER BY observed_at ASC, id ASC
                LIMIT 1
            ) o ON TRUE
            WHERE w.stima_id = %s
              AND w.status = 'active'
            """,
            (stima_id,),
        )
        context = _row(cur.fetchone())
        if context is None:
            return None
        cur.execute(
            """
            SELECT b.id, b.status, b.archived_at,
                   b.budget_min, b.budget_target, b.budget_max,
                   b.budget_flexibility_percent,
                   b.surface_min, b.surface_target, b.surface_max,
                   b.rooms_min, b.bedrooms_min, b.bathrooms_min,
                   b.created_at, b.updated_at,
                   GREATEST(
                       b.created_at,
                       b.updated_at,
                       COALESCE(MAX(i.occurred_at), b.created_at)
                   ) AS last_activity_at
            FROM buy_requests b
            LEFT JOIN buy_request_interactions i
              ON i.buy_request_id = b.id
            WHERE b.status = 'active'
              AND b.archived_at IS NULL
            GROUP BY b.id
            ORDER BY b.id ASC
            """
        )
        buyers = [dict(row) for row in cur.fetchall()]
        for buy in buyers:
            buy["locations"] = []
            buy["typologies"] = []
            buy["features"] = []
        ids = [buy["id"] for buy in buyers]
        if ids:
            child_specs = (
                (
                    "locations",
                    "buy_request_locations",
                    "microzone, municipality, province, priority, "
                    "is_required, is_excluded",
                ),
                (
                    "typologies",
                    "buy_request_typologies",
                    "property_type, requirement_level, priority",
                ),
                (
                    "features",
                    "buy_request_features",
                    "feature_code, requirement_level, value_type, "
                    "value_boolean, value_min, value_target, value_max, "
                    "value_text, weight_override",
                ),
            )
            by_id = {buy["id"]: buy for buy in buyers}
            for key, table, columns in child_specs:
                cur.execute(
                    f"""
                    SELECT buy_request_id, {columns}
                    FROM {table}
                    WHERE buy_request_id = ANY(%s)
                    ORDER BY buy_request_id ASC, id ASC
                    """,
                    (ids,),
                )
                for row in cur.fetchall():
                    item = dict(row)
                    request_id = item.pop("buy_request_id")
                    by_id[request_id][key].append(item)
        return {
            "watch_id": context["watch_id"],
            "baseline_observation_id": context["baseline_observation_id"],
            "baseline_payload": context.get("baseline_payload"),
            "collection_time": collection_time,
            "buyers": buyers,
        }
```

Do not reuse `buy.repository.get_request()` because it opens multiple
transactions and selects contact/title/notes fields.

- [ ] **Step 4: Run repository snapshot tests and verify GREEN**

Run the Step 2 command. Expected: all selected tests pass.

- [ ] **Step 5: Review checkpoint without commit**

Run `git diff --check`, `git status --short`, and inspect the repository diff.
Confirm the function has no `commit=True`, no broad `SELECT b.*`, and no
write statement. Do not commit or push.

---

### Task 4: Atomic append-only persistence and concurrency control

**Files:**

- Modify: `property_watch/repository.py`
- Modify: `tests/test_p21a_buyer_pressure_metrics.py`

**Interfaces:**

- Consumes: `canonicalize_metrics()` and `metrics_digest()` from the pure
  helper.
- Produces:
  `store_buyer_pressure_metrics(*, stima_id: int, watch_id: int, baseline_observation_id: int, metrics: dict[str, Any], observed_at: datetime) -> dict[str, Any] | None`
- `None` means the active watch disappeared and the service converts it to
  `WatchNotFoundError`.

- [ ] **Step 1: Write failing persistence/idempotency tests**

Use deterministic cursor spies or an in-memory repository harness to prove:

```python
first = repository.store_buyer_pressure_metrics(
    stima_id=501,
    watch_id=3,
    baseline_observation_id=10,
    metrics=zero_metrics,
    observed_at=collection_time,
)
assert first["status"] == "written"
assert first["observation"]["observation_type"] == "buyer_pressure_snapshot"
assert set(first["observation"]["payload"]) == set(buyer_pressure.METRIC_KEYS)

second = repository.store_buyer_pressure_metrics(...same arguments...)
assert second == {"status": "unchanged", "watch_id": 3, "observation": None}
```

Then change one count and assert `buyer_pressure_changed`, with a key matching:

```text
property_watch:buyer_pressure_changed:watch:3:after:11:metrics:<64 hex>:v1
```

Add controlled call-order tests for:

- inactive/missing locked watch -> `None`, no insert;
- baseline observation ID mismatch -> `baseline_unavailable`, no insert;
- latest observation newer than the supplied `observed_at` -> `superseded`, no
  insert;
- equivalent decimal payload -> `unchanged`;
- idempotency conflict -> existing observation returned;
- two equal concurrent attempts -> one insert and one `unchanged`, using lock
  order rather than `sleep`.

Assert the write transaction contains `FOR UPDATE`, reads latest relevant
types ordered by `observed_at DESC, id DESC`, and inserts only into
`property_watch_observations`.

- [ ] **Step 2: Run persistence cases and verify RED**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q \
  tests/test_p21a_buyer_pressure_metrics.py \
  -k 'store_buyer_pressure or buyer_pressure_concurrent or idempotency'
```

Expected: FAIL because the store function is absent.

- [ ] **Step 3: Extend the internal insert primitive without changing P20 calls**

Import from the pure helper at the top of `property_watch/repository.py`:

```python
from .buyer_pressure import canonicalize_metrics, metrics_digest
```

Add an optional keyword-only `observed_at` parameter to
`_insert_observation_with_cursor`, defaulting to `None`, and use:

```sql
INSERT INTO property_watch_observations (
    watch_id, observation_type, source, payload, idempotency_key, observed_at
) VALUES (%s, %s, %s, %s, %s, COALESCE(%s, NOW()))
ON CONFLICT (idempotency_key) DO NOTHING
RETURNING *
```

Append `observed_at` to the parameter tuple. Existing P20 calls omit the
keyword and preserve current behavior.

- [ ] **Step 4: Implement the atomic compare/insert**

Add:

```python
def store_buyer_pressure_metrics(
    *,
    stima_id: int,
    watch_id: int,
    baseline_observation_id: int,
    metrics: dict[str, Any],
    observed_at: datetime,
) -> dict[str, Any] | None:
    canonical = canonicalize_metrics(metrics)
    digest = metrics_digest(canonical)
    with property_watch_cursor(commit=True) as (_, cur):
        cur.execute(
            """
            SELECT id
            FROM property_watches
            WHERE id = %s
              AND stima_id = %s
              AND status = 'active'
            FOR UPDATE
            """,
            (watch_id, stima_id),
        )
        if cur.fetchone() is None:
            return None
        baseline = get_latest_relevant_observation(
            cur, watch_id, ("watch_started",)
        )
        if baseline is None or baseline["id"] != baseline_observation_id:
            return {
                "status": "baseline_unavailable",
                "watch_id": watch_id,
                "observation": None,
            }
        latest = get_latest_relevant_observation(
            cur,
            watch_id,
            ("buyer_pressure_snapshot", "buyer_pressure_changed"),
        )
        if latest is not None and latest["observed_at"] > observed_at:
            return {
                "status": "superseded",
                "watch_id": watch_id,
                "observation": None,
            }
        if latest is None:
            observation_type = "buyer_pressure_snapshot"
            predecessor_id = baseline_observation_id
        else:
            previous = canonicalize_metrics(latest.get("payload"))
            if previous == canonical:
                return {
                    "status": "unchanged",
                    "watch_id": watch_id,
                    "observation": None,
                }
            observation_type = "buyer_pressure_changed"
            predecessor_id = latest["id"]
        key = (
            f"property_watch:{observation_type}:watch:{watch_id}:"
            f"after:{predecessor_id}:metrics:{digest}:v1"
        )
        observation = _insert_observation_with_cursor(
            cur,
            watch_id,
            observation_type,
            "internal",
            canonical,
            key,
            observed_at=observed_at,
        )
        return {
            "status": "written",
            "watch_id": watch_id,
            "observation": observation,
        }
```

For the baseline lookup, ensure earliest semantics. If reusing
`get_latest_relevant_observation()` would select a later malformed duplicate,
add a dedicated earliest `watch_started` query instead of the call shown above;
the production contract is `ORDER BY observed_at ASC, id ASC LIMIT 1` and its
ID must equal `baseline_observation_id`.

- [ ] **Step 5: Run persistence and P20 repository tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q \
  tests/test_p21a_buyer_pressure_metrics.py \
  tests/test_p20b1_internal_signals.py \
  -k 'store_buyer_pressure or buyer_pressure_concurrent or idempotency or insert_observation or microzone_collector or supply_collector'
```

Expected: selected P21-A and existing P20 cases pass.

- [ ] **Step 6: Review checkpoint without commit**

Run Git checks. Inspect captured SQL to prove the only P21-A write target is
`property_watch_observations`. Do not commit or push.

---

### Task 5: Strict/safe collection and deterministic batch isolation

**Files:**

- Modify: `property_watch/service.py`
- Modify: `tests/test_p21a_buyer_pressure_metrics.py`

**Interfaces:**

- Consumes: repository input/store functions and
  `calculate_buyer_pressure_metrics()`.
- Produces:
  `collect_buyer_pressure_for_stima(stima_id: int) -> dict[str, Any]`
- Produces:
  `safe_collect_buyer_pressure_for_stima(stima_id: int) -> dict[str, Any]`
- Produces:
  `collect_buyer_pressure_for_active_watches() -> dict[str, Any]`

- [ ] **Step 1: Write failing strict and safe service tests**

Cover these exact outcomes:

```python
def test_strict_collector_writes_calculated_metrics(monkeypatch):
    inputs = {
        "watch_id": 3,
        "baseline_observation_id": 10,
        "baseline_payload": complete_baseline(),
        "collection_time": datetime(2026, 9, 3, tzinfo=timezone.utc),
        "buyers": [],
    }
    monkeypatch.setattr(repository, "get_buyer_pressure_inputs", lambda sid: inputs)
    monkeypatch.setattr(
        repository,
        "store_buyer_pressure_metrics",
        lambda **kwargs: {
            "status": "written", "watch_id": 3,
            "observation": {"id": 12, "watch_id": 3},
        },
    )

    result = service.collect_buyer_pressure_for_stima(501)

    assert result["status"] == "written"


def test_strict_collector_returns_baseline_unavailable_without_store(monkeypatch):
    inputs = {
        "watch_id": 3,
        "baseline_observation_id": 10,
        "baseline_payload": complete_baseline(microzona=None),
        "collection_time": datetime(2026, 9, 3, tzinfo=timezone.utc),
        "buyers": [],
    }
    monkeypatch.setattr(repository, "get_buyer_pressure_inputs", lambda sid: inputs)
    monkeypatch.setattr(
        repository,
        "store_buyer_pressure_metrics",
        lambda **kwargs: pytest.fail("unusable baseline must not write"),
    )
    assert service.collect_buyer_pressure_for_stima(501) == {
        "status": "baseline_unavailable",
        "watch_id": 3,
        "observation": None,
    }
```

Also assert:

- invalid `stima_id` raises `ValidationError`;
- absent inputs and a store-time disappearance raise `WatchNotFoundError`;
- safe collection re-raises those two expected classes;
- an unexpected exception returns `failed` and captured logs contain only
  `stima_id` plus `error_type`, with no exception text or payload.

- [ ] **Step 2: Write the RED batch regression**

Use active IDs `[7, 11]`. Make the safe collector raise
`WatchNotFoundError` for 7 and return `written` for 11. Assert calls are
`[7, 11]`, the batch does not raise, outcomes remain `[7, 11]`, and totals are:

```python
{
    "processed": 2,
    "written": 1,
    "unchanged": 0,
    "unavailable": 0,
    "superseded": 0,
    "failed": 1,
}
```

The failed item must be explicit:

```python
{
    "stima_id": 7,
    "status": "failed",
    "watch_id": None,
    "observation": None,
}
```

- [ ] **Step 3: Run service/batch tests and verify RED**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q \
  tests/test_p21a_buyer_pressure_metrics.py \
  -k 'strict_collector or safe_buyer_pressure or buyer_pressure_batch'
```

Expected: FAIL because service entry points are absent.

- [ ] **Step 4: Implement strict/safe service functions**

Import the helper module and add:

```python
from . import buyer_pressure


def collect_buyer_pressure_for_stima(stima_id: int) -> dict[str, Any]:
    _validate_stima_id(stima_id)
    inputs = repository.get_buyer_pressure_inputs(stima_id)
    if inputs is None:
        raise WatchNotFoundError(
            f"active property watch for stima {stima_id} not found"
        )
    metrics = buyer_pressure.calculate_buyer_pressure_metrics(
        inputs["buyers"],
        inputs["baseline_payload"],
        inputs["collection_time"],
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
```

- [ ] **Step 5: Implement deterministic batch totals**

Add a private summary over the single outcome per watch and:

```python
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
            outcome = {
                "status": "failed",
                "watch_id": None,
                "observation": None,
            }
        outcomes.append({"stima_id": stima_id, **outcome})
    totals = {
        "written": 0,
        "unchanged": 0,
        "unavailable": 0,
        "superseded": 0,
        "failed": 0,
    }
    for item in outcomes:
        status = item["status"]
        if status == "baseline_unavailable":
            totals["unavailable"] += 1
        elif status in totals:
            totals[status] += 1
        else:
            totals["failed"] += 1
    return {"processed": len(outcomes), **totals, "outcomes": outcomes}
```

Do not fold P21-A into the P20-B1 two-collector orchestration or totals; their
response contracts remain unchanged.

- [ ] **Step 6: Run service/batch and P20 isolation regressions**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q \
  tests/test_p21a_buyer_pressure_metrics.py \
  tests/test_p20b1_internal_signals.py \
  -k 'collector or batch or logs_no_payload'
```

Expected: selected P21-A and P20 tests pass.

- [ ] **Step 7: Review checkpoint without commit**

Inspect service logging arguments and outcome shapes, then run Git checks. Do
not commit or push.

---

### Task 6: Strict response schemas and protected body-free routes

**Files:**

- Modify: `property_watch/schemas.py`
- Modify: `property_watch/router.py`
- Modify: `tests/test_p21a_buyer_pressure_metrics.py`
- Modify: `tests/test_next2_router_hardening.py` only for exact inventory

**Interfaces:**

- Consumes the service functions from Task 5.
- Produces POST response models
  `PropertyWatchBuyerPressureRefresh` and
  `PropertyWatchBuyerPressureBatchRefresh`.

- [ ] **Step 1: Write failing OpenAPI, mapping, and HTTP serialization tests**

Assert the real imported `main` OpenAPI contains exactly POST for:

```text
/api/property-watch/stime/{stima_id}/buyer-pressure/refresh
/api/property-watch/buyer-pressure/refresh-active
```

For each operation assert `security` is present and `requestBody` absent.

Call router functions directly to prove:

- single refresh passes only the path `stima_id` to the safe service;
- `WatchNotFoundError` maps to 404;
- `ValidationError` maps to 400;
- batch returns the aggregate service result unchanged.

Use `FastAPI`, `Depends(require_admin)`, and `TestClient` as in the P20-B1
schema regression. Return a DB-shaped observation containing `watch_id` and
assert authenticated POST returns HTTP 200 and retains
`response.json()["observation"]["watch_id"]`.

- [ ] **Step 2: Run route/schema tests and verify RED**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q \
  tests/test_p21a_buyer_pressure_metrics.py \
  -k 'buyer_pressure_routes or buyer_pressure_http or buyer_pressure_error_mapping'
```

Expected: FAIL because the routes and response models do not exist.

- [ ] **Step 3: Add strict response schemas**

Append to `property_watch/schemas.py`:

```python
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
```

Reuse the hotfixed `PropertyWatchObservation` model containing `watch_id`.

- [ ] **Step 4: Add only the two routes**

Import the schemas and add to `property_watch/router.py`:

```python
@router.post(
    "/stime/{stima_id}/buyer-pressure/refresh",
    response_model=PropertyWatchBuyerPressureRefresh,
)
def refresh_buyer_pressure(stima_id: int):
    try:
        return service.safe_collect_buyer_pressure_for_stima(stima_id)
    except WatchNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/buyer-pressure/refresh-active",
    response_model=PropertyWatchBuyerPressureBatchRefresh,
)
def refresh_active_buyer_pressure():
    return service.collect_buyer_pressure_for_active_watches()
```

Do not add dependencies inside this router or edit `main.py`; the existing
router-wide dependency is authoritative.

- [ ] **Step 5: Update exact router inventory only if the focused test proves it**

In `tests/test_next2_router_hardening.py`, change only:

```python
"/api/property-watch": 4,
```

to:

```python
"/api/property-watch": 6,
```

and both exact total expectations from `98` to `100`. Do not alter any other
router exemption or protection expectation.

- [ ] **Step 6: Run focused HTTP/router tests and verify GREEN**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q \
  tests/test_p21a_buyer_pressure_metrics.py \
  tests/test_next2_router_hardening.py \
  tests/test_p20b1_internal_signals.py \
  tests/test_p20_property_watch.py
```

Expected: all tests pass; exact counts reflect only the two approved routes.

- [ ] **Step 7: Review checkpoint without commit**

Run Git checks and inspect the router diff for exactly two new decorators. Do
not commit or push.

---

### Task 7: Read-only Buyer Pressure current state

**Files:**

- Modify: `property_watch/service.py`
- Modify: `property_watch/schemas.py`
- Modify: `tests/test_p21a_buyer_pressure_metrics.py`

**Interfaces:**

- Extends `get_current_watch_state(stima_id)` with
  `buyer_pressure_metrics: dict[str, Any] | None`.
- Existing P20-A/P20-B1 fields and meanings remain unchanged.

- [ ] **Step 1: Write failing pure read-model tests**

Build ordered observations containing baseline, P20 internal signals, one
`buyer_pressure_snapshot`, and two `buyer_pressure_changed` rows. Assert the
latest payload is flattened into:

```python
assert state["buyer_pressure_metrics"] == {
    **latest_change["payload"],
    "latest_observation": latest_change,
    "observed_at": latest_change["observed_at"],
    "observation_count": 3,
}
```

With baseline/P20 observations but no Buyer Pressure event, assert the field
is `None`.

Monkeypatch these boundaries to `pytest.fail` if called during GET:

- `collect_buyer_pressure_for_stima`;
- `safe_collect_buyer_pressure_for_stima`;
- `repository.get_buyer_pressure_inputs`;
- `repository.store_buyer_pressure_metrics`;
- existing P20 collector/write functions.

Assert all existing P20 fields and total `observations`/
`observation_count` remain exact.

- [ ] **Step 2: Run read-model cases and verify RED**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q \
  tests/test_p21a_buyer_pressure_metrics.py -k 'buyer_pressure_current_state'
```

Expected: FAIL because the field is absent.

- [ ] **Step 3: Add pure derivation**

Inside `get_current_watch_state()`, after the current internal-supply
derivation, add:

```python
    buyer_pressure_observations = [
        item
        for item in observations
        if item["observation_type"]
        in {"buyer_pressure_snapshot", "buyer_pressure_changed"}
    ]
    latest_buyer_pressure = _latest_observation_of_type(
        observations,
        {"buyer_pressure_snapshot", "buyer_pressure_changed"},
    )
    buyer_pressure_metrics = None
    if latest_buyer_pressure is not None:
        metrics = buyer_pressure.canonicalize_metrics(
            latest_buyer_pressure.get("payload")
        )
        buyer_pressure_metrics = {
            **metrics,
            "latest_observation": latest_buyer_pressure,
            "observed_at": latest_buyer_pressure["observed_at"],
            "observation_count": len(buyer_pressure_observations),
        }
```

Add `"buyer_pressure_metrics": buyer_pressure_metrics` to the returned state.

- [ ] **Step 4: Extend the state schema without changing GET routing**

Add:

```python
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
```

Then add to `PropertyWatchState`:

```python
buyer_pressure_metrics: BuyerPressureMetricsState | None
```

Do not introduce writes or call the new collector from GET.

- [ ] **Step 5: Run P21-A and P20 read-model tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q \
  tests/test_p21a_buyer_pressure_metrics.py \
  tests/test_p20b1_internal_signals.py \
  tests/test_p20_property_watch.py \
  -k 'current_state or read_model or get_route'
```

Expected: selected P21-A and P20 tests pass.

- [ ] **Step 6: Review checkpoint without commit**

Inspect the state diff and prove no GET path invokes collection. Run Git
checks. Do not commit or push.

---

### Task 8: Privacy/write guardrails and full regression gate

**Files:**

- Modify: `tests/test_p21a_buyer_pressure_metrics.py`
- Modify: only already listed production/test files if a failing approved
  contract requires the smallest correction

**Interfaces:**

- Verifies the complete P21-A boundary; produces no new application interface.

- [ ] **Step 1: Add recursive privacy and SQL write-boundary tests**

Add a recursive key walker and assert no successful metrics, observation,
single response, batch response, current-state projection, or captured log
contains any case-insensitive forbidden key/token:

```python
FORBIDDEN = {
    "buy_request_id", "buyer_id", "buy_ids", "contact_id", "lead_id",
    "display_name", "name", "email", "phone", "notes", "criteria",
    "locations", "typologies", "features", "individual_scores",
}
```

For SQL captured while collecting one watch, classify every statement. Assert:

- all BUY/child/interaction statements start with `SELECT`;
- no statement references `match_runs` or `match_requirement_results`;
- no `INSERT`, `UPDATE`, or `DELETE` targets `matches`, BUY tables, PROPERTY,
  interactions, contacts, leads, or `stime`;
- the only insert target is `property_watch_observations`.

- [ ] **Step 2: Prove P21-A does not call MATCH persistence**

Monkeypatch these functions to fail if called:

```python
match.repository.calculate_pair
match.repository.calculate_for_buy
match.repository.calculate_for_property
match.repository.refresh_match
```

Run the strict collector with a valid in-memory input/store seam and assert it
completes through `buyer_pressure.calculate_match` only.

- [ ] **Step 3: Run the complete focused and adjacent regression gate**

Run in this order and record exact totals:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q \
  tests/test_p21a_buyer_pressure_metrics.py \
  tests/test_p20b1_internal_signals.py \
  tests/test_p20_property_watch.py

PYTHONDONTWRITEBYTECODE=1 python -m pytest -q \
  tests/test_match_engine.py \
  tests/test_next5_p1c_match_readiness.py \
  tests/test_next2_router_hardening.py

PYTHONDONTWRITEBYTECODE=1 python -m pytest -q
```

- [ ] **Step 4: Run static scope and whitespace checks**

Run:

```bash
git diff --check
git status --short
git diff --stat
git diff --name-only
rg -n "buyer_pressure|BuyerPressure" \
  property_watch tests/test_p21a_buyer_pressure_metrics.py
```

Expected application scope:

```text
property_watch/buyer_pressure.py
property_watch/repository.py
property_watch/service.py
property_watch/router.py
property_watch/schemas.py
```

Expected test scope:

```text
tests/test_p21a_buyer_pressure_metrics.py
tests/test_next2_router_hardening.py
```

The design and plan documents may also remain as approved additions. No other
file is allowed.

- [ ] **Step 5: Produce the uncommitted handoff report and stop**

Report:

- branch and HEAD;
- exact changed files;
- complete `git diff --stat`;
- focused, adjacent, and full-suite summaries with exit codes;
- `git diff --check` result;
- `git status --short`;
- confirmation that no normal MATCH/BUY/PROPERTY row is written;
- confirmation that GET is read-only and payload/logs contain no PII;
- remaining issues, if any.

Do not stage, commit, push, create a PR, merge, deploy, access TEST/PROD, or
clean unrelated files. Giorgio reviews and performs any eventual GitHub
Desktop commit/push manually.
