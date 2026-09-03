"""Raw SQL persistence for append-only Property Watch observations."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
from typing import Any

from psycopg2.extras import Json

from match.enums import ACTIVE_PROPERTY_STATUSES

from .database import property_watch_cursor


def _row(row: Any) -> dict[str, Any] | None:
    return dict(row) if row else None


def _json_default(value: Any) -> float:
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _json_dumps(value: Any) -> str:
    return json.dumps(value, default=_json_default, allow_nan=False)


def get_stima_baseline_data(stima_id: int) -> dict[str, Any] | None:
    """Fetch only non-personal, durable valuation attributes for a baseline."""
    with property_watch_cursor() as (_, cur):
        cur.execute(
            """
            SELECT id, comune, microzona, tipologia, mq, prezzo_mq_base
            FROM stime
            WHERE id = %s
            """,
            (stima_id,),
        )
        return _row(cur.fetchone())


def get_stima_completed_valuation(stima_id: int) -> dict[str, Any] | None:
    """Return the persisted P17 completed valuation payload for this stima."""
    with property_watch_cursor() as (_, cur):
        cur.execute(
            """
            SELECT payload
            FROM seller_timeline_events
            WHERE stima_id = %s
              AND event_type = 'stima_completata'
            ORDER BY occurred_at DESC, id DESC
            LIMIT 1
            """,
            (stima_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        payload = row["payload"]
        return dict(payload) if isinstance(payload, dict) else None


def ensure_watch_with_baseline(
    stima_id: int, baseline: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    """Create the watch and its baseline atomically, returning existing rows on retry."""
    idempotency_key = f"property_watch:watch_started:stima:{stima_id}:v1"
    with property_watch_cursor(commit=True) as (_, cur):
        cur.execute(
            """
            INSERT INTO property_watches (stima_id, status)
            VALUES (%s, 'active')
            ON CONFLICT (stima_id) WHERE stima_id IS NOT NULL
            DO NOTHING
            RETURNING *
            """,
            (stima_id,),
        )
        watch = _row(cur.fetchone())
        if watch is None:
            cur.execute(
                "SELECT * FROM property_watches WHERE stima_id = %s",
                (stima_id,),
            )
            watch = _row(cur.fetchone())
        if watch is None:
            raise RuntimeError(f"property watch conflict for stima_id={stima_id}")

        cur.execute(
            """
            INSERT INTO property_watch_observations (
                watch_id, observation_type, source, payload, idempotency_key
            ) VALUES (%s, 'watch_started', 'internal', %s, %s)
            ON CONFLICT (idempotency_key) DO NOTHING
            RETURNING *
            """,
            (watch["id"], Json(baseline, dumps=_json_dumps), idempotency_key),
        )
        observation = _row(cur.fetchone())
        if observation is None:
            cur.execute(
                "SELECT * FROM property_watch_observations WHERE idempotency_key = %s",
                (idempotency_key,),
            )
            observation = _row(cur.fetchone())
        if observation is None:
            raise RuntimeError(f"property watch baseline conflict for stima_id={stima_id}")
        return {"watch": watch, "baseline": observation}


def get_watch_for_stima(stima_id: int) -> dict[str, Any] | None:
    with property_watch_cursor() as (_, cur):
        cur.execute(
            "SELECT * FROM property_watches WHERE stima_id = %s",
            (stima_id,),
        )
        return _row(cur.fetchone())


def get_collection_context_for_update(
    cur: Any, stima_id: int
) -> dict[str, dict[str, Any] | None] | None:
    """Lock an active watch and retrieve its immutable watch-started baseline."""
    cur.execute(
        """
        SELECT *
        FROM property_watches
        WHERE stima_id = %s
          AND status = 'active'
        FOR UPDATE
        """,
        (stima_id,),
    )
    watch = _row(cur.fetchone())
    if watch is None:
        return None

    cur.execute(
        """
        SELECT *
        FROM property_watch_observations
        WHERE watch_id = %s
          AND observation_type = 'watch_started'
        ORDER BY observed_at ASC, id ASC
        LIMIT 1
        """,
        (watch["id"],),
    )
    return {"watch": watch, "baseline": _row(cur.fetchone())}


def list_active_watch_stima_ids() -> list[int]:
    with property_watch_cursor() as (_, cur):
        cur.execute(
            """
            SELECT stima_id
            FROM property_watches
            WHERE status = 'active'
              AND stima_id IS NOT NULL
            ORDER BY id ASC
            """
        )
        return [row["stima_id"] for row in cur.fetchall()]


def get_zone_value(cur: Any, comune: str, microzona: str) -> Any | None:
    cur.execute(
        """
        SELECT prezzo_mq_base
        FROM zone_valori
        WHERE comune = %s
          AND microzona = %s
        LIMIT 1
        """,
        (comune, microzona),
    )
    row = _row(cur.fetchone())
    return None if row is None else row["prezzo_mq_base"]


def count_internal_supply(cur: Any, comune: str, microzona: str) -> int:
    cur.execute(
        """
        SELECT COUNT(*) AS supply_count
        FROM properties
        WHERE city = %s
          AND microzone = %s
          AND archived_at IS NULL
          AND commercial_status IN (%s, %s, %s, %s)
        """,
        (comune, microzona, *ACTIVE_PROPERTY_STATUSES),
    )
    row = _row(cur.fetchone())
    return int(row["supply_count"]) if row is not None else 0


def get_latest_relevant_observation(
    cur: Any, watch_id: int, observation_types: tuple[str, ...]
) -> dict[str, Any] | None:
    cur.execute(
        """
        SELECT *
        FROM property_watch_observations
        WHERE watch_id = %s
          AND observation_type = ANY(%s)
        ORDER BY observed_at DESC, id DESC
        LIMIT 1
        """,
        (watch_id, list(observation_types)),
    )
    return _row(cur.fetchone())


def _finite_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    try:
        decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return decimal_value if decimal_value.is_finite() else None


def _canonical_decimal(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _insert_observation_with_cursor(
    cur: Any,
    watch_id: int,
    observation_type: str,
    source: str,
    payload: dict[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    cur.execute(
        """
        INSERT INTO property_watch_observations (
            watch_id, observation_type, source, payload, idempotency_key
        ) VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (idempotency_key) DO NOTHING
        RETURNING *
        """,
        (
            watch_id,
            observation_type,
            source,
            Json(payload, dumps=_json_dumps),
            idempotency_key,
        ),
    )
    observation = _row(cur.fetchone())
    if observation is not None:
        return observation

    cur.execute(
        "SELECT * FROM property_watch_observations WHERE idempotency_key = %s",
        (idempotency_key,),
    )
    observation = _row(cur.fetchone())
    if observation is None:
        raise RuntimeError(
            "property watch observation conflict without an existing observation"
        )
    return observation


def collect_microzone_price_change(
    watch_id: int,
    baseline_payload: dict[str, Any],
    *,
    cur: Any | None = None,
) -> dict[str, Any]:
    """Collect one append-only microzone price transition under a watch lock."""
    if cur is None:
        with property_watch_cursor(commit=True) as (_, transaction_cursor):
            transaction_cursor.execute(
                """
                SELECT id
                FROM property_watches
                WHERE id = %s
                  AND status = 'active'
                FOR UPDATE
                """,
                (watch_id,),
            )
            if transaction_cursor.fetchone() is None:
                return {
                    "status": "baseline_unavailable",
                    "watch_id": watch_id,
                    "observation": None,
                }
            return collect_microzone_price_change(
                watch_id,
                baseline_payload,
                cur=transaction_cursor,
            )

    if not isinstance(baseline_payload, dict):
        return {
            "status": "baseline_unavailable",
            "watch_id": watch_id,
            "observation": None,
        }
    comune = baseline_payload.get("comune")
    microzona = baseline_payload.get("microzona")
    if (
        not isinstance(comune, str)
        or not comune
        or not isinstance(microzona, str)
        or not microzona
    ):
        return {
            "status": "baseline_unavailable",
            "watch_id": watch_id,
            "observation": None,
        }

    latest_change = get_latest_relevant_observation(
        cur,
        watch_id,
        ("microzone_price_changed",),
    )
    if latest_change is None:
        baseline = get_latest_relevant_observation(cur, watch_id, ("watch_started",))
        if baseline is None:
            return {
                "status": "baseline_unavailable",
                "watch_id": watch_id,
                "observation": None,
            }
        prior_observation_id = baseline["id"]
        previous = _finite_decimal(baseline_payload.get("prezzo_mq_base"))
    else:
        prior_observation_id = latest_change["id"]
        latest_payload = latest_change.get("payload")
        previous = _finite_decimal(
            latest_payload.get("current") if isinstance(latest_payload, dict) else None
        )
    if previous is None:
        return {
            "status": "baseline_unavailable",
            "watch_id": watch_id,
            "observation": None,
        }

    current = _finite_decimal(get_zone_value(cur, comune, microzona))
    if current is None:
        return {
            "status": "source_unavailable",
            "watch_id": watch_id,
            "observation": None,
        }
    if current == previous:
        return {"status": "unchanged", "watch_id": watch_id, "observation": None}

    delta = current - previous
    payload = {
        "previous": previous,
        "current": current,
        "delta": delta,
        "delta_percent": None if previous == 0 else (delta / previous) * Decimal("100"),
        "comune": comune,
        "microzona": microzona,
    }
    idempotency_key = (
        "property_watch:microzone_price_changed:"
        f"watch:{watch_id}:after:{prior_observation_id}:"
        f"current:{_canonical_decimal(current)}:v1"
    )
    observation = _insert_observation_with_cursor(
        cur,
        watch_id,
        "microzone_price_changed",
        "internal",
        payload,
        idempotency_key,
    )
    return {"status": "written", "watch_id": watch_id, "observation": observation}


def insert_observation(
    watch_id: int,
    observation_type: str,
    source: str,
    payload: dict[str, Any],
    idempotency_key: str,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    with property_watch_cursor(commit=True) as (_, cur):
        cur.execute(
            """
            INSERT INTO property_watch_observations (
                watch_id, observation_type, source, payload, idempotency_key, observed_at
            ) VALUES (%s, %s, %s, %s, %s, COALESCE(%s, NOW()))
            ON CONFLICT (idempotency_key) DO NOTHING
            RETURNING *
            """,
            (
                watch_id,
                observation_type,
                source,
                Json(payload, dumps=_json_dumps),
                idempotency_key,
                observed_at,
            ),
        )
        observation = _row(cur.fetchone())
        if observation is not None:
            return observation

        cur.execute(
            "SELECT * FROM property_watch_observations WHERE idempotency_key = %s",
            (idempotency_key,),
        )
        observation = _row(cur.fetchone())
        if observation is None:
            raise RuntimeError(
                "property watch observation conflict without an existing observation"
            )
        return observation


def list_observations(watch_id: int) -> list[dict[str, Any]]:
    with property_watch_cursor() as (_, cur):
        cur.execute(
            """
            SELECT * FROM property_watch_observations
            WHERE watch_id = %s
            ORDER BY observed_at ASC, id ASC
            """,
            (watch_id,),
        )
        return [dict(row) for row in cur.fetchall()]
