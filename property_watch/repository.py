"""Raw SQL persistence for append-only Property Watch observations."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import json
from typing import Any

from psycopg2.extras import Json

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
                Json(payload),
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
