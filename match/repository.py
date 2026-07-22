from __future__ import annotations
from decimal import Decimal
from datetime import date, datetime
from time import monotonic

from psycopg2.extras import Json

from core.database import core_cursor
from core.exceptions import NotFoundError, ConflictError, ValidationError
from .engine import calculate
from .enums import ALGORITHM_VERSION, MODULE_VERSION, ACTIVE_PROPERTY_STATUSES
from .refresh import changed_fields as _refresh_changed_fields, requires_review


def _jsonable(value):
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _buy(cur, request_id):
    cur.execute("SELECT * FROM buy_requests WHERE id=%s AND archived_at IS NULL", (request_id,))
    row = cur.fetchone()
    if not row:
        raise NotFoundError(f"buy request {request_id} not found")
    data = dict(row)
    if data["status"] != "active":
        raise ValidationError("buy request must be active")
    for key, table in (
        ("locations", "buy_request_locations"),
        ("typologies", "buy_request_typologies"),
        ("features", "buy_request_features"),
    ):
        cur.execute(f"SELECT * FROM {table} WHERE buy_request_id=%s ORDER BY id", (request_id,))
        data[key] = [dict(x) for x in cur.fetchall()]
    return data


def _property(cur, property_id):
    cur.execute("SELECT * FROM properties WHERE id=%s AND archived_at IS NULL", (property_id,))
    row = cur.fetchone()
    if not row:
        raise NotFoundError(f"property {property_id} not found")
    data = dict(row)
    if data["commercial_status"] not in ACTIVE_PROPERTY_STATUSES:
        raise ValidationError("property is not commercially matchable")
    return data


def _is_excluded(cur, buy_request_id, property_id):
    cur.execute(
        """SELECT id FROM match_exclusions
           WHERE buy_request_id=%s AND property_id=%s
             AND (expires_at IS NULL OR expires_at>NOW())""",
        (buy_request_id, property_id),
    )
    return bool(cur.fetchone())


def calculate_pair(buy_request_id, property_id, run_type="single", created_by=None):
    """Calculate/upsert a pair while preserving manual and commercial fields."""
    with core_cursor(commit=True) as (_, cur):
        buy = _buy(cur, buy_request_id)
        prop = _property(cur, property_id)
        if _is_excluded(cur, buy_request_id, property_id):
            raise ConflictError("pair is excluded")

        result = calculate(buy, prop)
        cur.execute(
            """INSERT INTO match_runs(
                   run_type,buy_request_id,property_id,algorithm_version,
                   criteria_snapshot,property_snapshot,status,created_by,completed_at
               ) VALUES(%s,%s,%s,%s,%s,%s,'completed',%s,NOW()) RETURNING id""",
            (
                run_type,
                buy_request_id,
                property_id,
                ALGORITHM_VERSION,
                Json(_jsonable(buy)),
                Json(_jsonable(prop)),
                created_by,
            ),
        )
        run_id = cur.fetchone()["id"]
        g = result["group_scores"]

        cur.execute(
            """INSERT INTO matches(
                buy_request_id,property_id,latest_run_id,compatibility_status,score_total,
                score_location,score_budget,score_typology,score_dimensions,score_rooms,
                score_features,score_condition,match_class,hard_fail_count,warning_count,
                strengths,warnings,blocking_reasons,algorithm_version,last_calculated_at,
                freshness_status,stale_reason,stale_since,last_successful_run_at,
                last_failed_run_at,recalculation_error,buy_version_at_calculation,
                property_version_at_calculation,review_required,updated_at
            ) VALUES(
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),
                'fresh',NULL,NULL,NOW(),NULL,NULL,%s,%s,FALSE,NOW()
            )
            ON CONFLICT(buy_request_id,property_id) DO UPDATE SET
                latest_run_id=EXCLUDED.latest_run_id,
                compatibility_status=EXCLUDED.compatibility_status,
                score_total=EXCLUDED.score_total,
                score_location=EXCLUDED.score_location,
                score_budget=EXCLUDED.score_budget,
                score_typology=EXCLUDED.score_typology,
                score_dimensions=EXCLUDED.score_dimensions,
                score_rooms=EXCLUDED.score_rooms,
                score_features=EXCLUDED.score_features,
                score_condition=EXCLUDED.score_condition,
                match_class=EXCLUDED.match_class,
                hard_fail_count=EXCLUDED.hard_fail_count,
                warning_count=EXCLUDED.warning_count,
                strengths=EXCLUDED.strengths,
                warnings=EXCLUDED.warnings,
                blocking_reasons=EXCLUDED.blocking_reasons,
                algorithm_version=EXCLUDED.algorithm_version,
                last_calculated_at=NOW(),
                freshness_status='fresh',
                stale_reason=NULL,
                stale_since=NULL,
                last_successful_run_at=NOW(),
                last_failed_run_at=NULL,
                recalculation_error=NULL,
                buy_version_at_calculation=EXCLUDED.buy_version_at_calculation,
                property_version_at_calculation=EXCLUDED.property_version_at_calculation,
                updated_at=NOW()
            RETURNING *""",
            (
                buy_request_id,
                property_id,
                run_id,
                result["compatibility_status"],
                result["score_total"],
                g["location"],
                g["budget"],
                g["typology"],
                g["dimensions"],
                g["rooms"],
                g["features"],
                g["condition"],
                result["match_class"],
                result["hard_fail_count"],
                result["warning_count"],
                Json(_jsonable(result["strengths"])),
                Json(_jsonable(result["warnings"])),
                Json(_jsonable(result["blocking_reasons"])),
                ALGORITHM_VERSION,
                buy.get("updated_at"),
                prop.get("updated_at"),
            ),
        )
        match_row = dict(cur.fetchone())

        for criterion in result["criteria"]:
            cur.execute(
                """INSERT INTO match_requirement_results(
                    match_run_id,criterion_code,criterion_group,criterion_type,
                    requested_value,property_value,weight,result,score,penalty,
                    is_blocking,explanation
                ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    run_id,
                    criterion["criterion_code"],
                    criterion["criterion_group"],
                    criterion["criterion_type"],
                    Json(_jsonable(criterion["requested_value"])),
                    Json(_jsonable(criterion["property_value"])),
                    criterion["weight"],
                    criterion["result"],
                    criterion["score"],
                    criterion["penalty"],
                    criterion["is_blocking"],
                    criterion["explanation"],
                ),
            )
        match_row["criteria"] = result["criteria"]
        return match_row


def calculate_for_buy(request_id, created_by=None):
    with core_cursor() as (_, cur):
        _buy(cur, request_id)
        cur.execute(
            "SELECT id FROM properties WHERE archived_at IS NULL AND commercial_status=ANY(%s) ORDER BY id",
            (list(ACTIVE_PROPERTY_STATUSES),),
        )
        ids = [x["id"] for x in cur.fetchall()]
    items, errors = [], []
    for property_id in ids:
        try:
            items.append(calculate_pair(request_id, property_id, "buy_to_all_properties", created_by))
        except ConflictError:
            continue
        except Exception as exc:
            errors.append({"property_id": property_id, "error": str(exc)})
    return {"items": items, "errors": errors, "count": len(items)}


def calculate_for_property(property_id, created_by=None):
    with core_cursor() as (_, cur):
        _property(cur, property_id)
        cur.execute("SELECT id FROM buy_requests WHERE status='active' AND archived_at IS NULL ORDER BY id")
        ids = [x["id"] for x in cur.fetchall()]
    items, errors = [], []
    for request_id in ids:
        try:
            items.append(calculate_pair(request_id, property_id, "property_to_all_buyers", created_by))
        except ConflictError:
            continue
        except Exception as exc:
            errors.append({"buy_request_id": request_id, "error": str(exc)})
    return {"items": items, "errors": errors, "count": len(items)}


def list_matches(
    limit=100,
    offset=0,
    buy_request_id=None,
    property_id=None,
    match_class=None,
    commercial_status=None,
    compatible_only=False,
    freshness_status=None,
    review_required=None,
):
    filters = ["m.archived_at IS NULL"]
    params = []
    if buy_request_id:
        filters.append("m.buy_request_id=%s")
        params.append(buy_request_id)
    if property_id:
        filters.append("m.property_id=%s")
        params.append(property_id)
    if match_class:
        filters.append("m.match_class=%s")
        params.append(match_class)
    if commercial_status:
        filters.append("m.commercial_status=%s")
        params.append(commercial_status)
    if compatible_only:
        filters.append("m.compatibility_status<>'incompatible'")
    if freshness_status:
        filters.append("m.freshness_status=%s")
        params.append(freshness_status)
    if review_required is not None:
        filters.append("m.review_required=%s")
        params.append(review_required)
    params += [limit, offset]
    with core_cursor() as (_, cur):
        cur.execute(
            f"""SELECT m.*,b.title AS buy_title,c.display_name AS buyer_name,
                p.title AS property_title,p.code AS property_code,p.city,p.microzone,
                p.asking_price,p.classification,
                COALESCE(m.manual_score,m.score_total) AS effective_score
                FROM matches m
                JOIN buy_requests b ON b.id=m.buy_request_id
                JOIN contacts c ON c.id=b.contact_id
                JOIN properties p ON p.id=m.property_id
                WHERE {' AND '.join(filters)}
                ORDER BY effective_score DESC,m.updated_at DESC LIMIT %s OFFSET %s""",
            params,
        )
        return [dict(x) for x in cur.fetchall()]


def get_match(match_id):
    with core_cursor() as (_, cur):
        cur.execute(
            """SELECT m.*,b.title AS buy_title,c.display_name AS buyer_name,
                p.title AS property_title,p.code AS property_code,p.city,p.microzone,
                p.asking_price,p.classification,
                COALESCE(m.manual_score,m.score_total) AS effective_score
                FROM matches m
                JOIN buy_requests b ON b.id=m.buy_request_id
                JOIN contacts c ON c.id=b.contact_id
                JOIN properties p ON p.id=m.property_id
                WHERE m.id=%s""",
            (match_id,),
        )
        row = cur.fetchone()
        if not row:
            raise NotFoundError(f"match {match_id} not found")
        data = dict(row)
        cur.execute(
            "SELECT * FROM match_requirement_results WHERE match_run_id=%s ORDER BY criterion_group,id",
            (data["latest_run_id"],),
        )
        data["criteria"] = [dict(x) for x in cur.fetchall()]
        return data


def update_match(match_id, data):
    data = dict(data)
    if not data:
        return get_match(match_id)
    allowed = {"commercial_status", "priority", "assigned_to", "review_required"}
    unknown = set(data) - allowed
    if unknown:
        raise ValidationError(f"unsupported match fields: {', '.join(sorted(unknown))}")
    with core_cursor(commit=True) as (_, cur):
        cur.execute(
            f"UPDATE matches SET {','.join(f'{k}=%s' for k in data)},last_reviewed_at=NOW(),updated_at=NOW() WHERE id=%s RETURNING *",
            list(data.values()) + [match_id],
        )
        row = cur.fetchone()
        if not row:
            raise NotFoundError(f"match {match_id} not found")
        return dict(row)


def set_override(match_id, score, reason):
    with core_cursor(commit=True) as (_, cur):
        cur.execute(
            """UPDATE matches SET is_manual_override=TRUE,manual_score=%s,manual_reason=%s,
               last_reviewed_at=NOW(),updated_at=NOW() WHERE id=%s RETURNING *""",
            (score, reason, match_id),
        )
        row = cur.fetchone()
        if not row:
            raise NotFoundError(f"match {match_id} not found")
        return dict(row)


def clear_override(match_id):
    with core_cursor(commit=True) as (_, cur):
        cur.execute(
            """UPDATE matches SET is_manual_override=FALSE,manual_score=NULL,manual_reason=NULL,
               updated_at=NOW() WHERE id=%s RETURNING *""",
            (match_id,),
        )
        row = cur.fetchone()
        if not row:
            raise NotFoundError(f"match {match_id} not found")
        return dict(row)


def add_exclusion(data):
    with core_cursor(commit=True) as (_, cur):
        _buy(cur, data["buy_request_id"])
        _property(cur, data["property_id"])
        cur.execute(
            """INSERT INTO match_exclusions(
                buy_request_id,property_id,exclusion_type,reason,expires_at,created_by
            ) VALUES(%s,%s,%s,%s,%s,%s)
            ON CONFLICT(buy_request_id,property_id) DO UPDATE SET
                exclusion_type=EXCLUDED.exclusion_type,
                reason=EXCLUDED.reason,
                expires_at=EXCLUDED.expires_at,
                created_by=EXCLUDED.created_by
            RETURNING *""",
            (
                data["buy_request_id"],
                data["property_id"],
                data.get("exclusion_type", "agent_decision"),
                data.get("reason"),
                data.get("expires_at"),
                data.get("created_by"),
            ),
        )
        exclusion = dict(cur.fetchone())
        cur.execute(
            """UPDATE matches SET commercial_status='archived',archived_at=NOW(),
               freshness_status='excluded',stale_reason='pair excluded',updated_at=NOW()
               WHERE buy_request_id=%s AND property_id=%s""",
            (data["buy_request_id"], data["property_id"]),
        )
        return exclusion


def list_exclusions():
    with core_cursor() as (_, cur):
        cur.execute(
            """SELECT e.*,b.title AS buy_title,p.title AS property_title
               FROM match_exclusions e
               JOIN buy_requests b ON b.id=e.buy_request_id
               JOIN properties p ON p.id=e.property_id
               ORDER BY e.created_at DESC"""
        )
        return [dict(x) for x in cur.fetchall()]


def delete_exclusion(exclusion_id):
    with core_cursor(commit=True) as (_, cur):
        cur.execute(
            "DELETE FROM match_exclusions WHERE id=%s RETURNING buy_request_id,property_id",
            (exclusion_id,),
        )
        row = cur.fetchone()
        if not row:
            raise NotFoundError(f"exclusion {exclusion_id} not found")
        cur.execute(
            """UPDATE matches SET archived_at=NULL,commercial_status='to_review',
               freshness_status='stale',stale_reason='exclusion removed',stale_since=NOW(),
               review_required=TRUE,updated_at=NOW()
               WHERE buy_request_id=%s AND property_id=%s""",
            (row["buy_request_id"], row["property_id"]),
        )


def detect_stale(match_id=None, buy_request_id=None, property_id=None):
    filters = ["m.archived_at IS NULL", "m.freshness_status IN ('fresh','stale')"]
    params = []
    if match_id:
        filters.append("m.id=%s")
        params.append(match_id)
    if buy_request_id:
        filters.append("m.buy_request_id=%s")
        params.append(buy_request_id)
    if property_id:
        filters.append("m.property_id=%s")
        params.append(property_id)
    with core_cursor(commit=True) as (_, cur):
        cur.execute(
            f"""UPDATE matches m SET
                freshness_status='stale',
                stale_reason=CASE
                    WHEN (m.buy_version_at_calculation IS NULL OR b.updated_at>m.buy_version_at_calculation)
                     AND (m.property_version_at_calculation IS NULL OR p.updated_at>m.property_version_at_calculation)
                    THEN 'buy and property updated'
                    WHEN m.buy_version_at_calculation IS NULL OR b.updated_at>m.buy_version_at_calculation
                    THEN 'buy updated'
                    ELSE 'property updated'
                END,
                stale_since=COALESCE(m.stale_since,NOW()),
                updated_at=NOW()
                FROM buy_requests b,properties p
                WHERE b.id=m.buy_request_id AND p.id=m.property_id
                  AND {' AND '.join(filters)}
                  AND (
                    m.buy_version_at_calculation IS NULL OR b.updated_at>m.buy_version_at_calculation OR
                    m.property_version_at_calculation IS NULL OR p.updated_at>m.property_version_at_calculation
                  )
                RETURNING m.id,m.buy_request_id,m.property_id,m.stale_reason,m.stale_since""",
            params,
        )
        changed = [dict(x) for x in cur.fetchall()]
        return {"marked_stale": len(changed), "items": changed}


def refresh_match(match_id, created_by=None, trigger_source="manual", trigger_reason=None):
    previous = get_match(match_id)
    if previous.get("freshness_status") == "excluded":
        raise ConflictError("excluded match cannot be refreshed")

    with core_cursor(commit=True) as (_, cur):
        cur.execute(
            """UPDATE matches SET freshness_status='recalculating',recalculation_error=NULL,
               updated_at=NOW() WHERE id=%s""",
            (match_id,),
        )

    try:
        current = calculate_pair(
            previous["buy_request_id"],
            previous["property_id"],
            run_type="manual_refresh",
            created_by=created_by,
        )
        changed_fields = _refresh_changed_fields(previous, current)
        score_delta = abs(float(current["score_total"]) - float(previous["score_total"]))
        review_required = requires_review(previous, current)
        with core_cursor(commit=True) as (_, cur):
            cur.execute(
                """INSERT INTO match_refresh_history(
                    match_id,previous_run_id,new_run_id,previous_score,new_score,
                    previous_class,new_class,previous_compatibility_status,
                    new_compatibility_status,trigger_source,trigger_reason,changed_fields
                ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (
                    match_id,
                    previous.get("latest_run_id"),
                    current.get("latest_run_id"),
                    previous.get("score_total"),
                    current.get("score_total"),
                    previous.get("match_class"),
                    current.get("match_class"),
                    previous.get("compatibility_status"),
                    current.get("compatibility_status"),
                    trigger_source,
                    trigger_reason or previous.get("stale_reason") or "manual refresh",
                    Json(changed_fields),
                ),
            )
            history_id = cur.fetchone()["id"]
            cur.execute(
                """UPDATE matches SET freshness_status='fresh',review_required=%s,
                   last_successful_run_at=NOW(),last_failed_run_at=NULL,
                   recalculation_error=NULL,updated_at=NOW() WHERE id=%s RETURNING *""",
                (review_required, match_id),
            )
            result = dict(cur.fetchone())
        result["refresh_history_id"] = history_id
        result["changed_fields"] = changed_fields
        result["score_delta"] = round(score_delta, 2)
        return result
    except Exception as exc:
        with core_cursor(commit=True) as (_, cur):
            cur.execute(
                """UPDATE matches SET freshness_status='failed',last_failed_run_at=NOW(),
                   recalculation_error=%s,review_required=TRUE,updated_at=NOW() WHERE id=%s""",
                (str(exc)[:4000], match_id),
            )
        raise


def _refresh_ids(filters, params, trigger_source, created_by=None, trigger_reason=None):
    with core_cursor() as (_, cur):
        cur.execute(
            f"""SELECT m.id FROM matches m WHERE m.archived_at IS NULL
                AND m.freshness_status<>'excluded' AND {' AND '.join(filters)}
                ORDER BY m.id""",
            params,
        )
        ids = [x["id"] for x in cur.fetchall()]
    items, errors = [], []
    for match_id in ids:
        try:
            items.append(refresh_match(match_id, created_by, trigger_source, trigger_reason))
        except Exception as exc:
            errors.append({"match_id": match_id, "error": str(exc)})
    return {"requested": len(ids), "refreshed": len(items), "failed": len(errors), "items": items, "errors": errors}


def refresh_for_buy(request_id, created_by=None, trigger_reason=None):
    detect_stale(buy_request_id=request_id)
    return _refresh_ids(["m.buy_request_id=%s", "m.freshness_status IN ('stale','failed')"], [request_id], "buy", created_by, trigger_reason)


def refresh_for_property(property_id, created_by=None, trigger_reason=None):
    detect_stale(property_id=property_id)
    return _refresh_ids(["m.property_id=%s", "m.freshness_status IN ('stale','failed')"], [property_id], "property", created_by, trigger_reason)


def refresh_stale(limit=50, created_by=None, trigger_reason=None):
    started = monotonic()
    detect_stale()
    with core_cursor() as (_, cur):
        cur.execute(
            """SELECT id FROM matches WHERE archived_at IS NULL AND freshness_status='stale'
               ORDER BY stale_since ASC NULLS FIRST,id ASC LIMIT %s""",
            (limit,),
        )
        ids = [x["id"] for x in cur.fetchall()]
    items, errors = [], []
    for match_id in ids:
        try:
            items.append(refresh_match(match_id, created_by, "system", trigger_reason or "mass stale refresh"))
        except Exception as exc:
            errors.append({"match_id": match_id, "error": str(exc)})
    with core_cursor() as (_, cur):
        cur.execute("SELECT COUNT(*) AS count FROM matches WHERE archived_at IS NULL AND freshness_status='stale'")
        remaining = cur.fetchone()["count"]
    return {
        "limit": limit,
        "requested": len(ids),
        "processed": len(items) + len(errors),
        "refreshed": len(items),
        "failed": len(errors),
        "remaining_stale": remaining,
        "duration_seconds": round(monotonic() - started, 3),
        "items": items,
        "errors": errors,
    }


def refresh_history(match_id):
    get_match(match_id)
    with core_cursor() as (_, cur):
        cur.execute(
            "SELECT * FROM match_refresh_history WHERE match_id=%s ORDER BY created_at DESC,id DESC",
            (match_id,),
        )
        return [dict(x) for x in cur.fetchall()]


def timeline(match_id):
    match = get_match(match_id)
    with core_cursor() as (_, cur):
        cur.execute(
            """SELECT 'refresh' AS event_kind,id,created_at,trigger_source AS source,
                      trigger_reason AS description,changed_fields AS details
               FROM match_refresh_history WHERE match_id=%s
               UNION ALL
               SELECT 'feedback' AS event_kind,id,created_at,source,
                      COALESCE(notes,feedback_type) AS description,
                      jsonb_build_object('feedback_type',feedback_type,'reason_code',reason_code) AS details
               FROM match_feedback WHERE match_id=%s
               ORDER BY created_at DESC""",
            (match_id, match_id),
        )
        return {"match_id": match["id"], "items": [dict(x) for x in cur.fetchall()]}


def add_feedback(match_id, data):
    get_match(match_id)
    with core_cursor(commit=True) as (_, cur):
        cur.execute(
            """INSERT INTO match_feedback(match_id,source,feedback_type,reason_code,notes,created_by)
               VALUES(%s,%s,%s,%s,%s,%s) RETURNING *""",
            (
                match_id,
                data["source"],
                data["feedback_type"],
                data.get("reason_code"),
                data.get("notes"),
                data.get("created_by"),
            ),
        )
        return dict(cur.fetchone())


def list_feedback(match_id):
    get_match(match_id)
    with core_cursor() as (_, cur):
        cur.execute(
            "SELECT * FROM match_feedback WHERE match_id=%s ORDER BY created_at DESC,id DESC",
            (match_id,),
        )
        return [dict(x) for x in cur.fetchall()]


def delete_feedback(feedback_id):
    with core_cursor(commit=True) as (_, cur):
        cur.execute("DELETE FROM match_feedback WHERE id=%s RETURNING id", (feedback_id,))
        if not cur.fetchone():
            raise NotFoundError(f"feedback {feedback_id} not found")


def dashboard():
    detect_stale()
    with core_cursor() as (_, cur):
        cur.execute(
            """SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER(WHERE compatibility_status<>'incompatible') AS compatible,
                COUNT(*) FILTER(WHERE match_class IN ('excellent','strong')) AS strong,
                COUNT(*) FILTER(WHERE commercial_status IN ('to_review','new')) AS to_review,
                COUNT(*) FILTER(WHERE is_manual_override) AS overridden,
                COUNT(*) FILTER(WHERE freshness_status='fresh') AS fresh,
                COUNT(*) FILTER(WHERE freshness_status='stale') AS stale,
                COUNT(*) FILTER(WHERE freshness_status='failed') AS failed,
                COUNT(*) FILTER(WHERE review_required) AS review_required,
                COUNT(*) FILTER(WHERE match_class='excellent') AS excellent,
                COUNT(*) FILTER(WHERE match_class='incompatible') AS incompatible,
                COALESCE(AVG(score_total),0) AS average_score
                FROM matches WHERE archived_at IS NULL"""
        )
        result = dict(cur.fetchone())
        cur.execute(
            """SELECT m.id,b.title AS buy_title,p.title AS property_title,m.score_total,
                m.manual_score,m.match_class,m.compatibility_status,m.commercial_status,
                m.freshness_status,m.review_required
                FROM matches m
                JOIN buy_requests b ON b.id=m.buy_request_id
                JOIN properties p ON p.id=m.property_id
                WHERE m.archived_at IS NULL
                ORDER BY COALESCE(m.manual_score,m.score_total) DESC LIMIT 10"""
        )
        result["top_matches"] = [dict(x) for x in cur.fetchall()]
        cur.execute(
            """SELECT m.id,b.title AS buy_title,p.title AS property_title,m.stale_since,m.stale_reason
               FROM matches m JOIN buy_requests b ON b.id=m.buy_request_id
               JOIN properties p ON p.id=m.property_id
               WHERE m.archived_at IS NULL AND m.freshness_status='stale'
               ORDER BY m.stale_since ASC NULLS FIRST LIMIT 10"""
        )
        result["stale_items"] = [dict(x) for x in cur.fetchall()]
        cur.execute(
            """SELECT m.id,b.title AS buy_title,p.title AS property_title,m.last_failed_run_at,m.recalculation_error
               FROM matches m JOIN buy_requests b ON b.id=m.buy_request_id
               JOIN properties p ON p.id=m.property_id
               WHERE m.archived_at IS NULL AND m.freshness_status='failed'
               ORDER BY m.last_failed_run_at DESC NULLS LAST LIMIT 10"""
        )
        result["error_items"] = [dict(x) for x in cur.fetchall()]
        cur.execute(
            """SELECT COUNT(*) AS count FROM matches m
               WHERE m.archived_at IS NULL AND m.match_class IN ('excellent','strong')
                 AND m.commercial_status IN ('new','to_review','approved')"""
        )
        result["strong_not_proposed"] = cur.fetchone()["count"]
        cur.execute(
            """SELECT COUNT(DISTINCT mf.match_id) AS count FROM match_feedback mf
               JOIN matches m ON m.id=mf.match_id
               WHERE m.archived_at IS NULL AND mf.feedback_type='negative'"""
        )
        result["negative_feedback"] = cur.fetchone()["count"]
        result["algorithm_version"] = ALGORITHM_VERSION
        result["module_version"] = MODULE_VERSION
        return result


def dashboard_stale(limit=100):
    detect_stale()
    return list_matches(limit=limit, freshness_status="stale")


def dashboard_errors(limit=100):
    return list_matches(limit=limit, freshness_status="failed")


def dashboard_review(limit=100):
    return list_matches(limit=limit, review_required=True)
