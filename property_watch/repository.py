"""Raw SQL persistence for append-only Property Watch observations."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
from typing import Any

from psycopg2.extras import Json

from match.enums import ACTIVE_PROPERTY_STATUSES

from .buyer_pressure import canonicalize_metrics, metrics_digest
from .database import property_watch_cursor
from .exceptions import StimaNotFoundError


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
    *,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
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


def get_buyer_pressure_inputs(stima_id: int) -> dict[str, Any] | None:
    """Read a coherent, privacy-minimized BUY snapshot for one active watch."""
    with property_watch_cursor() as (_, cur):
        cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        cur.execute("SELECT transaction_timestamp() AS collection_time")
        collection_time = cur.fetchone()["collection_time"]
        cur.execute(
            """
                SELECT w.id AS watch_id, o.id AS baseline_observation_id,
                       o.payload AS baseline_payload
                FROM property_watches w
                LEFT JOIN LATERAL (
                    SELECT id, payload
                    FROM property_watch_observations
                    WHERE watch_id = w.id AND observation_type = 'watch_started'
                    ORDER BY observed_at ASC, id ASC
                    LIMIT 1
                ) o ON TRUE
                WHERE w.stima_id = %s AND w.status = 'active'
            """,
            (stima_id,),
        )
        context = _row(cur.fetchone())
        if context is None:
            return None
        cur.execute(
            """
                SELECT b.id, b.status, b.archived_at, b.budget_min, b.budget_target,
                       b.budget_max, b.budget_flexibility_percent, b.surface_min,
                       b.surface_target, b.surface_max, b.rooms_min, b.bedrooms_min,
                       b.bathrooms_min, b.created_at, b.updated_at,
                       GREATEST(b.created_at, b.updated_at,
                           COALESCE(MAX(i.occurred_at), b.created_at)) AS last_activity_at
                FROM buy_requests b
                LEFT JOIN buy_request_interactions i ON i.buy_request_id = b.id
                WHERE b.status = 'active' AND b.archived_at IS NULL
                GROUP BY b.id
                ORDER BY b.id ASC
            """
        )
        buyers = [dict(row) for row in cur.fetchall()]
        for buy in buyers:
            buy["locations"] = []
            buy["typologies"] = []
            buy["features"] = []
        request_ids = [buy["id"] for buy in buyers]
        if request_ids:
            child_specs = (
                    (
                        "locations",
                        "buy_request_locations",
                        "microzone, municipality, province, priority, is_required, is_excluded",
                    ),
                    (
                        "typologies",
                        "buy_request_typologies",
                        "property_type, requirement_level, priority",
                    ),
                    (
                        "features",
                        "buy_request_features",
                        "feature_code, requirement_level, value_type, value_boolean, "
                        "value_min, value_target, value_max, value_text, weight_override",
                    ),
            )
            buyers_by_id = {buy["id"]: buy for buy in buyers}
            for key, table, columns in child_specs:
                cur.execute(
                    f"""
                        SELECT buy_request_id, {columns}
                        FROM {table}
                        WHERE buy_request_id = ANY(%s)
                        ORDER BY buy_request_id ASC, id ASC
                    """,
                    (request_ids,),
                )
                for row in cur.fetchall():
                    item = dict(row)
                    buyers_by_id[item.pop("buy_request_id")][key].append(item)
        return {
            "watch_id": context["watch_id"],
            "baseline_observation_id": context["baseline_observation_id"],
            "baseline_payload": context.get("baseline_payload"),
            "collection_time": collection_time,
            "buyers": buyers,
        }


def _get_earliest_watch_started_observation(
    cur: Any, watch_id: int
) -> dict[str, Any] | None:
    cur.execute(
        """
            SELECT *
            FROM property_watch_observations
            WHERE watch_id = %s AND observation_type = 'watch_started'
            ORDER BY observed_at ASC, id ASC
            LIMIT 1
        """,
        (watch_id,),
    )
    return _row(cur.fetchone())


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
                WHERE id = %s AND stima_id = %s AND status = 'active'
                FOR UPDATE
            """,
            (watch_id, stima_id),
        )
        if cur.fetchone() is None:
            return None
        baseline = _get_earliest_watch_started_observation(cur, watch_id)
        if baseline is None or baseline["id"] != baseline_observation_id:
            return {
                "status": "baseline_unavailable",
                "watch_id": watch_id,
                "observation": None,
            }
        latest = get_latest_relevant_observation(
            cur, watch_id, ("buyer_pressure_snapshot", "buyer_pressure_changed")
        )
        if latest is not None and latest["observed_at"] > observed_at:
            return {"status": "superseded", "watch_id": watch_id, "observation": None}
        if latest is None:
            observation_type = "buyer_pressure_snapshot"
            predecessor_id = baseline_observation_id
        else:
            if canonicalize_metrics(latest.get("payload")) == canonical:
                return {"status": "unchanged", "watch_id": watch_id, "observation": None}
            observation_type = "buyer_pressure_changed"
            predecessor_id = latest["id"]
        idempotency_key = (
            f"property_watch:{observation_type}:watch:{watch_id}:"
            f"after:{predecessor_id}:metrics:{digest}:v1"
        )
        observation = _insert_observation_with_cursor(
            cur,
            watch_id,
            observation_type,
            "internal",
            canonical,
            idempotency_key,
            observed_at=observed_at,
        )
        return {"status": "written", "watch_id": watch_id, "observation": observation}


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


def collect_internal_supply_change(
    watch_id: int,
    baseline_payload: dict[str, Any],
    *,
    cur: Any | None = None,
    agency_id: int,
) -> dict[str, Any]:
    """Collect one append-only internal supply transition under a watch lock.

    P26-6B: `agency_id` is keyword-only and required. It bounds the competing
    supply count to the watch's own tenant - see count_internal_supply_for_agency
    for why that count is the leak worth naming.
    """
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
            return collect_internal_supply_change(
                watch_id,
                baseline_payload,
                cur=transaction_cursor,
                agency_id=agency_id,
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

    latest_supply = get_latest_relevant_observation(
        cur,
        watch_id,
        ("internal_supply_changed", "internal_supply_snapshot"),
    )
    # P26-6B: the supply count is bounded to the watch's own agency.
    #
    # `agency_id` is required, not optional-with-a-global-default. An optional
    # parameter would have left the system path - the public estimation funnel,
    # which has no operator - still counting every tenant's listings into this
    # watch's metric, and that path is precisely the one nobody would think to
    # check. The caller already holds the watch row (046/047/048 make its
    # agency_id NOT NULL), so supplying it costs nothing.
    current_count = count_internal_supply_for_agency(cur, comune, microzona, agency_id)
    if latest_supply is None:
        baseline = get_latest_relevant_observation(cur, watch_id, ("watch_started",))
        if baseline is None:
            return {
                "status": "baseline_unavailable",
                "watch_id": watch_id,
                "observation": None,
            }
        observation_type = "internal_supply_snapshot"
        payload = {
            "current_count": current_count,
            "comune": comune,
            "microzona": microzona,
        }
        idempotency_key = (
            "property_watch:internal_supply_snapshot:"
            f"watch:{watch_id}:after:{baseline['id']}:count:{current_count}:v1"
        )
    else:
        latest_payload = latest_supply.get("payload")
        previous_count = (
            latest_payload.get("current_count")
            if isinstance(latest_payload, dict)
            else None
        )
        if (
            not isinstance(previous_count, int)
            or isinstance(previous_count, bool)
            or previous_count < 0
        ):
            return {
                "status": "baseline_unavailable",
                "watch_id": watch_id,
                "observation": None,
            }
        if current_count == previous_count:
            return {"status": "unchanged", "watch_id": watch_id, "observation": None}
        observation_type = "internal_supply_changed"
        payload = {
            "previous_count": previous_count,
            "current_count": current_count,
            "delta": current_count - previous_count,
            "comune": comune,
            "microzona": microzona,
        }
        idempotency_key = (
            "property_watch:internal_supply_changed:"
            f"watch:{watch_id}:after:{latest_supply['id']}:count:{current_count}:v1"
        )

    observation = _insert_observation_with_cursor(
        cur,
        watch_id,
        observation_type,
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


# ---------------------------------------------------------------------------
# P26-6B agency scoping.
#
# `property_watches` carries a physical agency_id (migration 046) because
# stima_id is nullable and ON DELETE SET NULL: a watch outlives the estimation
# it was opened for, and a derived-only design would leave it with no tenant.
#
# The quiet leak in this module is `count_internal_supply`. It returns a number,
# not a row, so nothing foreign is ever handed back - and yet counting agency
# B's listings into agency A's zone metric tells A exactly how much competing
# inventory B holds. The scoped version takes the watch's agency and constrains
# the properties it counts.
#
# The legacy functions above are untouched: the public estimation funnel reaches
# `safe_ensure_watch_for_stima` with no operator at all.
# ---------------------------------------------------------------------------

def _agency(ctx) -> int:
    return ctx.require_agency()


def ensure_watch_with_baseline_scoped(
    ctx, stima_id: int, baseline: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    """Open (or re-open) a watch for a stima the caller owns.

    The stima is resolved in scope first, so a foreign one is not found rather
    than watched. Both idempotent re-reads carry the agency too: `stima_id` and
    `idempotency_key` are globally unique, so an unscoped retry lookup would
    return another tenant's row on a key collision.
    """
    agency_id = _agency(ctx)
    idempotency_key = f"property_watch:watch_started:stima:{stima_id}:v1"
    with property_watch_cursor(commit=True) as (_, cur):
        cur.execute(
            "SELECT id FROM stime WHERE id = %s AND agency_id = %s",
            (stima_id, agency_id),
        )
        if cur.fetchone() is None:
            raise StimaNotFoundError(f"stima {stima_id} not found")

        cur.execute(
            """
            INSERT INTO property_watches (stima_id, status, agency_id)
            VALUES (%s, 'active', %s)
            ON CONFLICT (stima_id) WHERE stima_id IS NOT NULL
            DO NOTHING
            RETURNING *
            """,
            (stima_id, agency_id),
        )
        watch = _row(cur.fetchone())
        if watch is None:
            cur.execute(
                "SELECT * FROM property_watches WHERE stima_id = %s AND agency_id = %s",
                (stima_id, agency_id),
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
            # Reached through the watch, which is already known to be this
            # agency's: an observation has no tenant of its own, so its parent
            # is what makes this lookup safe.
            cur.execute(
                """SELECT o.* FROM property_watch_observations o
                   JOIN property_watches w ON w.id = o.watch_id
                   WHERE o.idempotency_key = %s AND w.agency_id = %s""",
                (idempotency_key, agency_id),
            )
            observation = _row(cur.fetchone())
        if observation is None:
            raise RuntimeError(f"property watch baseline conflict for stima_id={stima_id}")
        return {"watch": watch, "baseline": observation}


def get_watch_for_stima_scoped(ctx, stima_id: int) -> dict[str, Any] | None:
    agency_id = _agency(ctx)
    with property_watch_cursor() as (_, cur):
        cur.execute(
            "SELECT * FROM property_watches WHERE stima_id = %s AND agency_id = %s",
            (stima_id, agency_id),
        )
        return _row(cur.fetchone())


def get_collection_context_for_update_scoped(
    cur: Any, stima_id: int, agency_id: int
) -> dict[str, dict[str, Any] | None] | None:
    """As the ctx-less version, bounded to one agency.

    Takes a cursor and a resolved agency rather than a context: it runs inside a
    caller's transaction, which is the same shape core.repository's `_with_cursor`
    helpers use.
    """
    cur.execute(
        """
        SELECT *
        FROM property_watches
        WHERE stima_id = %s
          AND agency_id = %s
          AND status = 'active'
        FOR UPDATE
        """,
        (stima_id, agency_id),
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
        ORDER BY id ASC
        LIMIT 1
        """,
        (watch["id"],),
    )
    return {"watch": watch, "baseline": _row(cur.fetchone())}


def list_active_watch_stima_ids_for_agency(agency_id: int) -> list[int]:
    """The active-watch sweep, for one agency.

    Takes an agency id rather than a context because the background orchestrator
    calls it once per tenant, where there is no operator to carry a scope.
    """
    with property_watch_cursor() as (_, cur):
        cur.execute(
            """
            SELECT stima_id
            FROM property_watches
            WHERE status = 'active'
              AND stima_id IS NOT NULL
              AND agency_id = %s
            ORDER BY id ASC
            """,
            (agency_id,),
        )
        return [row["stima_id"] for row in cur.fetchall()]


def list_active_agency_ids() -> list[int]:
    """The tenants a server-only batch iterates, one bounded cycle each."""
    with property_watch_cursor() as (_, cur):
        cur.execute("SELECT id FROM agencies WHERE status = 'active' ORDER BY id")
        return [row["id"] for row in cur.fetchall()]


def count_internal_supply_for_agency(
    cur: Any, comune: str, microzona: str, agency_id: int
) -> int:
    """Competing listings in this zone, counted inside one agency.

    The unscoped version returns a plain integer, so no foreign row ever
    reaches the caller - which is exactly what makes it easy to miss. A count
    that includes another tenant's listings hands agency A a measurement of
    agency B's inventory, and it lands in A's stored metrics as if it were its
    own market.
    """
    cur.execute(
        """
        SELECT COUNT(*) AS supply_count
        FROM properties
        WHERE city = %s
          AND microzone = %s
          AND agency_id = %s
          AND archived_at IS NULL
          AND commercial_status IN (%s, %s, %s, %s)
        """,
        (comune, microzona, agency_id, *ACTIVE_PROPERTY_STATUSES),
    )
    row = _row(cur.fetchone())
    return int(row["supply_count"]) if row is not None else 0


def list_observations_scoped(ctx, watch_id: int) -> list[dict[str, Any]]:
    """Observations, reachable only through a watch this agency owns."""
    agency_id = _agency(ctx)
    with property_watch_cursor() as (_, cur):
        cur.execute(
            """
            SELECT o.* FROM property_watch_observations o
            JOIN property_watches w ON w.id = o.watch_id
            WHERE o.watch_id = %s AND w.agency_id = %s
            ORDER BY o.observed_at DESC, o.id DESC
            """,
            (watch_id, agency_id),
        )
        return [dict(row) for row in cur.fetchall()]


def get_buyer_pressure_inputs_for_agency(stima_id: int, agency_id: int) -> dict[str, Any] | None:
    """The BUY snapshot for one active watch, inside one agency.

    This is the second quiet leak of the slice. The ctx-less version above
    enumerates every active buy request in the database and feeds them all into
    the pressure model; nothing foreign is returned to a caller, but agency A's
    demand metrics, digest and stored observations would all be computed from
    agency B's buyers.

    Only the root query needed a predicate. The three child tables are reached
    through `request_ids`, which now comes from an already-scoped set - so they
    inherit the boundary rather than needing one of their own, and adding a
    redundant filter there would suggest they were a separate risk.
    """
    with property_watch_cursor() as (_, cur):
        cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        cur.execute("SELECT transaction_timestamp() AS collection_time")
        collection_time = cur.fetchone()["collection_time"]
        cur.execute(
            """
                SELECT w.id AS watch_id, o.id AS baseline_observation_id,
                       o.payload AS baseline_payload
                FROM property_watches w
                LEFT JOIN LATERAL (
                    SELECT id, payload
                    FROM property_watch_observations
                    WHERE watch_id = w.id AND observation_type = 'watch_started'
                    ORDER BY observed_at ASC, id ASC
                    LIMIT 1
                ) o ON TRUE
                WHERE w.stima_id = %s AND w.status = 'active' AND w.agency_id = %s
            """,
            (stima_id, agency_id),
        )
        context = _row(cur.fetchone())
        if context is None:
            return None
        cur.execute(
            """
                SELECT b.id, b.status, b.archived_at, b.budget_min, b.budget_target,
                       b.budget_max, b.budget_flexibility_percent, b.surface_min,
                       b.surface_target, b.surface_max, b.rooms_min, b.bedrooms_min,
                       b.bathrooms_min, b.created_at, b.updated_at,
                       GREATEST(b.created_at, b.updated_at,
                           COALESCE(MAX(i.occurred_at), b.created_at)) AS last_activity_at
                FROM buy_requests b
                LEFT JOIN buy_request_interactions i ON i.buy_request_id = b.id
                WHERE b.status = 'active' AND b.archived_at IS NULL
                  AND b.agency_id = %s
                GROUP BY b.id
                ORDER BY b.id ASC
            """,
            (agency_id,),
        )
        buyers = [dict(row) for row in cur.fetchall()]
        for buy in buyers:
            buy["locations"] = []
            buy["typologies"] = []
            buy["features"] = []
        request_ids = [buy["id"] for buy in buyers]
        if request_ids:
            child_specs = (
                    (
                        "locations",
                        "buy_request_locations",
                        "microzone, municipality, province, priority, is_required, is_excluded",
                    ),
                    (
                        "typologies",
                        "buy_request_typologies",
                        "property_type, requirement_level, priority",
                    ),
                    (
                        "features",
                        "buy_request_features",
                        "feature_code, requirement_level, value_type, value_boolean, "
                        "value_min, value_target, value_max, value_text, weight_override",
                    ),
            )
            buyers_by_id = {buy["id"]: buy for buy in buyers}
            for key, table, columns in child_specs:
                cur.execute(
                    f"""
                        SELECT buy_request_id, {columns}
                        FROM {table}
                        WHERE buy_request_id = ANY(%s)
                        ORDER BY buy_request_id ASC, id ASC
                    """,
                    (request_ids,),
                )
                for row in cur.fetchall():
                    item = dict(row)
                    buyers_by_id[item.pop("buy_request_id")][key].append(item)
        return {
            "stima_id": stima_id,
            "watch_id": context["watch_id"],
            "baseline_observation_id": context["baseline_observation_id"],
            "baseline_payload": context.get("baseline_payload"),
            "collection_time": collection_time,
            "buyers": buyers,
        }


def get_stima_baseline_data_scoped(ctx, stima_id: int) -> dict[str, Any] | None:
    """The baseline attributes, for a stima this agency owns."""
    agency_id = _agency(ctx)
    with property_watch_cursor() as (_, cur):
        cur.execute(
            """
            SELECT id, comune, microzona, tipologia, mq, prezzo_mq_base
            FROM stime
            WHERE id = %s AND agency_id = %s
            """,
            (stima_id, agency_id),
        )
        return _row(cur.fetchone())


def get_stima_completed_valuation_scoped(ctx, stima_id: int) -> dict[str, Any] | None:
    """The P17 completed-valuation payload, inside this agency.

    Both the stima and the timeline event carry the tenant since P26-2B and
    P26-6A, and both are named: the ordering plus LIMIT 1 picks the latest of a
    single stima's own events, and the set it picks from must be this agency's.
    """
    agency_id = _agency(ctx)
    with property_watch_cursor() as (_, cur):
        cur.execute(
            """
            SELECT e.payload
            FROM seller_timeline_events e
            JOIN stime s ON s.id = e.stima_id
            WHERE e.stima_id = %s
              AND e.event_type = 'stima_completata'
              AND e.agency_id = %s
              AND s.agency_id = %s
            ORDER BY e.occurred_at DESC, e.id DESC
            LIMIT 1
            """,
            (stima_id, agency_id, agency_id),
        )
        row = cur.fetchone()
        if not row:
            return None
        payload = row["payload"]
        return dict(payload) if isinstance(payload, dict) else None
