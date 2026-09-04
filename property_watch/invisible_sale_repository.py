"""P22 persistence and read models, isolated from existing Property Watch flows."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from psycopg2.extras import Json

from .database import property_watch_cursor
from .invisible_sale import P22_ALGORITHM_VERSION


def _row(row: Any) -> dict[str, Any] | None:
    return dict(row) if row else None


def _public_candidate(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: row[key]
        for key in (
            "buy_request_id",
            "score_total",
            "compatibility_status",
            "reason_codes",
            "last_activity_at",
            "budget_reference",
            "match_algorithm_version",
            "status",
        )
    }


def get_watch_and_baseline_for_stima(stima_id: int) -> dict[str, Any] | None:
    with property_watch_cursor() as (_, cur):
        cur.execute(
            """SELECT * FROM property_watches WHERE stima_id = %s AND status = 'active'""",
            (stima_id,),
        )
        watch = _row(cur.fetchone())
        if watch is None:
            return None
        cur.execute(
            """SELECT * FROM property_watch_observations WHERE watch_id = %s
               AND observation_type = 'watch_started' ORDER BY observed_at, id LIMIT 1""",
            (watch["id"],),
        )
        return {"watch": watch, "baseline": _row(cur.fetchone())}


def list_eligible_buy_snapshot() -> list[dict[str, Any]]:
    """Read only MATCH inputs; deliberately never joins BUY contact/profile data."""
    with property_watch_cursor() as (_, cur):
        cur.execute(
            """SELECT b.*, GREATEST(b.created_at, b.updated_at, (
                    SELECT MAX(i.occurred_at) FROM buy_request_interactions i
                    WHERE i.buy_request_id = b.id
                )) AS last_activity_at
               FROM buy_requests b
               WHERE b.status = 'active' AND b.archived_at IS NULL
               ORDER BY b.id"""
        )
        buys = [_row(row) for row in cur.fetchall()]
        for buy in buys:
            for field, table in (
                ("locations", "buy_request_locations"),
                ("typologies", "buy_request_typologies"),
                ("features", "buy_request_features"),
            ):
                cur.execute(f"SELECT * FROM {table} WHERE buy_request_id = %s ORDER BY id", (buy["id"],))
                buy[field] = [_row(row) for row in cur.fetchall()]
        return buys


def list_active_watch_refs() -> list[dict[str, Any]]:
    with property_watch_cursor() as (_, cur):
        cur.execute(
            """SELECT id AS watch_id, stima_id FROM property_watches
               WHERE status = 'active' AND stima_id IS NOT NULL ORDER BY id"""
        )
        return [_row(row) for row in cur.fetchall()]


def get_invisible_sale_for_stima(stima_id: int) -> dict[str, Any]:
    with property_watch_cursor() as (_, cur):
        cur.execute("SELECT id FROM property_watches WHERE stima_id = %s", (stima_id,))
        watch = _row(cur.fetchone())
        if watch is None:
            raise LookupError("property watch not found")
        cur.execute("SELECT * FROM invisible_sale_opportunities WHERE watch_id = %s", (watch["id"],))
        opportunity = _row(cur.fetchone())
        if opportunity is None:
            return {"status": "not_collected", "current_candidate_count": 0, "candidates": []}
        cur.execute(
            """SELECT buy_request_id, score_total, compatibility_status, reason_codes,
                      last_activity_at, budget_reference, match_algorithm_version, status
               FROM invisible_sale_candidates WHERE opportunity_id = %s
               ORDER BY CASE WHEN status = 'stale' THEN 1 ELSE 0 END,
                        score_total DESC, last_activity_at DESC, buy_request_id ASC""",
            (opportunity["id"],),
        )
        return {
            "status": opportunity["status"],
            "current_candidate_count": opportunity["current_candidate_count"],
            "candidates": [_public_candidate(_row(row)) for row in cur.fetchall()],
        }


def persist_invisible_sale_refresh(
    watch_id: int, candidates: list[dict[str, Any]], digest: str, evaluated_at: datetime
) -> dict[str, Any]:
    """Atomically replace only calculated fields, preserving valid human decisions."""
    with property_watch_cursor(commit=True) as (_, cur):
        cur.execute("SELECT id FROM property_watches WHERE id = %s FOR UPDATE", (watch_id,))
        if cur.fetchone() is None:
            raise LookupError("property watch not found")
        cur.execute(
            """INSERT INTO invisible_sale_opportunities
               (watch_id, status, candidate_digest, current_candidate_count,
                algorithm_version, revision, last_evaluated_at)
               VALUES (%s, 'empty', %s, 0, %s, 0, %s)
               ON CONFLICT (watch_id) DO NOTHING""",
            (watch_id, digest, P22_ALGORITHM_VERSION, evaluated_at),
        )
        cur.execute("SELECT * FROM invisible_sale_opportunities WHERE watch_id = %s FOR UPDATE", (watch_id,))
        opportunity = _row(cur.fetchone())
        if opportunity["status"] == "closed":
            return {"status": "closed", "watch_id": watch_id}
        if opportunity["candidate_digest"] == digest and opportunity["revision"] > 0:
            return {"status": "unchanged", "watch_id": watch_id}
        revision = opportunity["revision"] + 1
        ids = [item["buy_request_id"] for item in candidates]
        cur.execute(
            "SELECT * FROM invisible_sale_candidates WHERE opportunity_id = %s ORDER BY buy_request_id FOR UPDATE",
            (opportunity["id"],),
        )
        existing = {row["buy_request_id"]: _row(row) for row in cur.fetchall()}
        for buy_id, row in existing.items():
            if buy_id not in ids and row["status"] != "stale":
                cur.execute(
                    "UPDATE invisible_sale_candidates SET status = 'stale', updated_at = %s WHERE id = %s",
                    (evaluated_at, row["id"]),
                )
                cur.execute(
                    """INSERT INTO invisible_sale_events
                       (opportunity_id, candidate_id, event_type, opportunity_revision,
                        idempotency_key, payload)
                       VALUES (%s, %s, 'stale', %s, %s, %s)
                       ON CONFLICT (idempotency_key) DO NOTHING""",
                    (
                        opportunity["id"], row["id"], revision,
                        f"invisible_sale:stale:candidate:{row['id']}:revision:{revision}:v1",
                        Json({"prior_status": row["status"], "revision": revision}),
                    ),
                )
        for item in candidates:
            prior = existing.get(item["buy_request_id"])
            status = prior["status"] if prior and prior["status"] in {"approved", "rejected"} else "pending_review"
            cur.execute(
                """INSERT INTO invisible_sale_candidates
                   (opportunity_id, buy_request_id, score_total, compatibility_status, reason_codes,
                    last_activity_at, budget_reference, match_algorithm_version, candidate_digest, status)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (opportunity_id, buy_request_id) DO UPDATE SET
                    score_total=EXCLUDED.score_total, compatibility_status=EXCLUDED.compatibility_status,
                    reason_codes=EXCLUDED.reason_codes, last_activity_at=EXCLUDED.last_activity_at,
                    budget_reference=EXCLUDED.budget_reference,
                    match_algorithm_version=EXCLUDED.match_algorithm_version,
                    candidate_digest=EXCLUDED.candidate_digest, status=EXCLUDED.status, updated_at=%s""",
                (opportunity["id"], item["buy_request_id"], item["score_total"],
                 item["compatibility_status"], Json(item["reason_codes"]), item["last_activity_at"],
                 item["budget_reference"], item["match_algorithm_version"], item["candidate_digest"],
                 status, evaluated_at),
            )
            if revision == 1:
                cur.execute(
                    """SELECT id FROM invisible_sale_candidates
                       WHERE opportunity_id=%s AND buy_request_id=%s""",
                    (opportunity["id"], item["buy_request_id"]),
                )
                candidate = _row(cur.fetchone())
                cur.execute(
                    """INSERT INTO invisible_sale_events
                       (opportunity_id, candidate_id, event_type, opportunity_revision,
                        idempotency_key, payload)
                       VALUES (%s, %s, 'discovered', %s, %s, %s)
                       ON CONFLICT (idempotency_key) DO NOTHING""",
                    (
                        opportunity["id"], candidate["id"], revision,
                        f"invisible_sale:discovered:candidate:{candidate['id']}:revision:{revision}:v1",
                        Json({"revision": revision}),
                    ),
                )
        status = "ready" if candidates else "empty"
        cur.execute(
            """UPDATE invisible_sale_opportunities SET status=%s, candidate_digest=%s,
               current_candidate_count=%s, algorithm_version=%s, revision=%s,
               last_evaluated_at=%s, updated_at=%s WHERE id=%s""",
            (status, digest, len(candidates), P22_ALGORITHM_VERSION, revision, evaluated_at, evaluated_at, opportunity["id"]),
        )
        cur.execute(
            """INSERT INTO invisible_sale_events (opportunity_id, event_type, opportunity_revision,
               idempotency_key, payload) VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (idempotency_key) DO NOTHING""",
            (opportunity["id"], "refreshed", revision,
             f"invisible_sale:refreshed:watch:{watch_id}:revision:{revision}:digest:{digest}:v1",
             Json({"candidate_count": len(candidates), "revision": revision})),
        )
        return {"status": "written", "watch_id": watch_id}


def set_candidate_review_status(
    stima_id: int, buy_request_id: int, target_status: Literal["approved", "rejected"]
) -> dict[str, Any]:
    with property_watch_cursor(commit=True) as (_, cur):
        cur.execute(
            "SELECT id FROM property_watches WHERE stima_id=%s FOR UPDATE",
            (stima_id,),
        )
        watch = _row(cur.fetchone())
        if watch is None:
            raise LookupError("property watch not found")
        cur.execute(
            "SELECT * FROM invisible_sale_opportunities WHERE watch_id=%s FOR UPDATE",
            (watch["id"],),
        )
        opportunity = _row(cur.fetchone())
        if opportunity is None:
            raise LookupError("opportunity not found")
        cur.execute(
            """SELECT * FROM invisible_sale_candidates
               WHERE opportunity_id=%s AND buy_request_id=%s FOR UPDATE""",
            (opportunity["id"], buy_request_id),
        )
        candidate = _row(cur.fetchone())
        if candidate is None:
            raise LookupError("candidate not found")
        if opportunity["status"] == "closed" or candidate["status"] == "stale":
            raise RuntimeError("candidate cannot be reviewed")
        if candidate["status"] == target_status:
            return {"status": target_status, "buy_request_id": buy_request_id}
        version = candidate["decision_version"] + 1
        cur.execute("UPDATE invisible_sale_candidates SET status=%s, decision_version=%s WHERE id=%s", (target_status, version, candidate["id"]))
        cur.execute(
            """INSERT INTO invisible_sale_events (opportunity_id,candidate_id,event_type,decision_version,idempotency_key,payload)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            (opportunity["id"], candidate["id"], target_status, version,
             f"invisible_sale:{target_status}:candidate:{candidate['id']}:decision:{version}:v1",
             Json({"status": target_status, "decision_version": version})),
        )
        return {"status": target_status, "buy_request_id": buy_request_id}


def close_invisible_sale_for_stima(stima_id: int) -> dict[str, Any]:
    with property_watch_cursor(commit=True) as (_, cur):
        cur.execute(
            "SELECT id FROM property_watches WHERE stima_id=%s FOR UPDATE",
            (stima_id,),
        )
        watch = _row(cur.fetchone())
        if watch is None:
            raise LookupError("property watch not found")
        cur.execute(
            "SELECT * FROM invisible_sale_opportunities WHERE watch_id=%s FOR UPDATE",
            (watch["id"],),
        )
        opportunity = _row(cur.fetchone())
        if opportunity is None:
            raise LookupError("opportunity not found")
        if opportunity["status"] == "closed":
            return {"status": "closed"}
        cur.execute("UPDATE invisible_sale_opportunities SET status='closed' WHERE id=%s", (opportunity["id"],))
        cur.execute(
            """INSERT INTO invisible_sale_events (opportunity_id,event_type,idempotency_key,payload)
               VALUES (%s,'closed',%s,%s)""",
            (opportunity["id"], f"invisible_sale:closed:opportunity:{opportunity['id']}:v1", Json({"status": "closed"})),
        )
        return {"status": "closed"}
