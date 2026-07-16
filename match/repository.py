from __future__ import annotations
from psycopg2.extras import Json
from decimal import Decimal
from datetime import date, datetime
from core.database import core_cursor
from core.exceptions import NotFoundError, ConflictError, ValidationError
from .engine import calculate
from .enums import ALGORITHM_VERSION, ACTIVE_PROPERTY_STATUSES


def _dict(row): return dict(row) if row else None

def _jsonable(value):
    if isinstance(value, dict): return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [_jsonable(v) for v in value]
    if isinstance(value, Decimal): return float(value)
    if isinstance(value, (date, datetime)): return value.isoformat()
    return value


def _buy(cur, request_id):
    cur.execute("SELECT * FROM buy_requests WHERE id=%s AND archived_at IS NULL", (request_id,))
    row = cur.fetchone()
    if not row: raise NotFoundError(f"buy request {request_id} not found")
    data = dict(row)
    if data["status"] != "active": raise ValidationError("buy request must be active")
    for key, table in (("locations", "buy_request_locations"), ("typologies", "buy_request_typologies"), ("features", "buy_request_features")):
        cur.execute(f"SELECT * FROM {table} WHERE buy_request_id=%s ORDER BY id", (request_id,))
        data[key] = [dict(x) for x in cur.fetchall()]
    return data


def _property(cur, property_id):
    cur.execute("SELECT * FROM properties WHERE id=%s AND archived_at IS NULL", (property_id,))
    row = cur.fetchone()
    if not row: raise NotFoundError(f"property {property_id} not found")
    data = dict(row)
    if data["commercial_status"] not in ACTIVE_PROPERTY_STATUSES:
        raise ValidationError("property is not commercially matchable")
    return data


def _is_excluded(cur, buy_request_id, property_id):
    cur.execute("""SELECT id FROM match_exclusions WHERE buy_request_id=%s AND property_id=%s
        AND (expires_at IS NULL OR expires_at>NOW())""", (buy_request_id, property_id))
    return bool(cur.fetchone())


def calculate_pair(buy_request_id, property_id, run_type="single", created_by=None):
    with core_cursor(commit=True) as (_, cur):
        buy = _buy(cur, buy_request_id)
        prop = _property(cur, property_id)
        if _is_excluded(cur, buy_request_id, property_id):
            raise ConflictError("pair is excluded")
        result = calculate(buy, prop)
        cur.execute("""INSERT INTO match_runs(run_type,buy_request_id,property_id,algorithm_version,criteria_snapshot,property_snapshot,status,created_by,completed_at)
            VALUES(%s,%s,%s,%s,%s,%s,'completed',%s,NOW()) RETURNING id""",
            (run_type, buy_request_id, property_id, ALGORITHM_VERSION, Json(_jsonable(buy)), Json(_jsonable(prop)), created_by))
        run_id = cur.fetchone()["id"]
        g = result["group_scores"]
        cur.execute("""INSERT INTO matches(
            buy_request_id,property_id,latest_run_id,compatibility_status,score_total,
            score_location,score_budget,score_typology,score_dimensions,score_rooms,score_features,score_condition,
            match_class,hard_fail_count,warning_count,strengths,warnings,blocking_reasons,algorithm_version,last_calculated_at,updated_at)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),NOW())
            ON CONFLICT(buy_request_id,property_id) DO UPDATE SET
            latest_run_id=EXCLUDED.latest_run_id,compatibility_status=EXCLUDED.compatibility_status,score_total=EXCLUDED.score_total,
            score_location=EXCLUDED.score_location,score_budget=EXCLUDED.score_budget,score_typology=EXCLUDED.score_typology,
            score_dimensions=EXCLUDED.score_dimensions,score_rooms=EXCLUDED.score_rooms,score_features=EXCLUDED.score_features,
            score_condition=EXCLUDED.score_condition,match_class=EXCLUDED.match_class,hard_fail_count=EXCLUDED.hard_fail_count,
            warning_count=EXCLUDED.warning_count,strengths=EXCLUDED.strengths,warnings=EXCLUDED.warnings,
            blocking_reasons=EXCLUDED.blocking_reasons,algorithm_version=EXCLUDED.algorithm_version,last_calculated_at=NOW(),updated_at=NOW()
            RETURNING *""", (buy_request_id, property_id, run_id, result["compatibility_status"], result["score_total"],
            g["location"], g["budget"], g["typology"], g["dimensions"], g["rooms"], g["features"], g["condition"],
            result["match_class"], result["hard_fail_count"], result["warning_count"], Json(_jsonable(result["strengths"])), Json(_jsonable(result["warnings"])), Json(_jsonable(result["blocking_reasons"])), ALGORITHM_VERSION))
        match_row = dict(cur.fetchone())
        for criterion in result["criteria"]:
            cur.execute("""INSERT INTO match_requirement_results(match_run_id,criterion_code,criterion_group,criterion_type,requested_value,property_value,weight,result,score,penalty,is_blocking,explanation)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", (run_id, criterion["criterion_code"], criterion["criterion_group"], criterion["criterion_type"], Json(_jsonable(criterion["requested_value"])), Json(_jsonable(criterion["property_value"])), criterion["weight"], criterion["result"], criterion["score"], criterion["penalty"], criterion["is_blocking"], criterion["explanation"]))
        match_row["criteria"] = result["criteria"]
        return match_row


def calculate_for_buy(request_id, created_by=None):
    with core_cursor() as (_, cur):
        _buy(cur, request_id)
        cur.execute("SELECT id FROM properties WHERE archived_at IS NULL AND commercial_status=ANY(%s) ORDER BY id", (list(ACTIVE_PROPERTY_STATUSES),))
        ids = [x["id"] for x in cur.fetchall()]
    items, errors = [], []
    for property_id in ids:
        try: items.append(calculate_pair(request_id, property_id, "buy_to_all_properties", created_by))
        except ConflictError: continue
        except Exception as exc: errors.append({"property_id": property_id, "error": str(exc)})
    return {"items": items, "errors": errors, "count": len(items)}


def calculate_for_property(property_id, created_by=None):
    with core_cursor() as (_, cur):
        _property(cur, property_id)
        cur.execute("SELECT id FROM buy_requests WHERE status='active' AND archived_at IS NULL ORDER BY id")
        ids = [x["id"] for x in cur.fetchall()]
    items, errors = [], []
    for request_id in ids:
        try: items.append(calculate_pair(request_id, property_id, "property_to_all_buyers", created_by))
        except ConflictError: continue
        except Exception as exc: errors.append({"buy_request_id": request_id, "error": str(exc)})
    return {"items": items, "errors": errors, "count": len(items)}


def list_matches(limit=100, offset=0, buy_request_id=None, property_id=None, match_class=None, commercial_status=None, compatible_only=False):
    filters = ["m.archived_at IS NULL"]; params = []
    if buy_request_id: filters.append("m.buy_request_id=%s"); params.append(buy_request_id)
    if property_id: filters.append("m.property_id=%s"); params.append(property_id)
    if match_class: filters.append("m.match_class=%s"); params.append(match_class)
    if commercial_status: filters.append("m.commercial_status=%s"); params.append(commercial_status)
    if compatible_only: filters.append("m.compatibility_status<>'incompatible'")
    params += [limit, offset]
    with core_cursor() as (_, cur):
        cur.execute(f"""SELECT m.*,b.title AS buy_title,c.display_name AS buyer_name,p.title AS property_title,p.code AS property_code,
            p.city,p.microzone,p.asking_price,p.classification,
            COALESCE(m.manual_score,m.score_total) AS effective_score
            FROM matches m JOIN buy_requests b ON b.id=m.buy_request_id JOIN contacts c ON c.id=b.contact_id
            JOIN properties p ON p.id=m.property_id WHERE {' AND '.join(filters)}
            ORDER BY effective_score DESC,m.updated_at DESC LIMIT %s OFFSET %s""", params)
        return [dict(x) for x in cur.fetchall()]


def get_match(match_id):
    with core_cursor() as (_, cur):
        cur.execute("""SELECT m.*,b.title AS buy_title,c.display_name AS buyer_name,p.title AS property_title,p.code AS property_code,
            p.city,p.microzone,p.asking_price,p.classification,COALESCE(m.manual_score,m.score_total) AS effective_score
            FROM matches m JOIN buy_requests b ON b.id=m.buy_request_id JOIN contacts c ON c.id=b.contact_id
            JOIN properties p ON p.id=m.property_id WHERE m.id=%s""", (match_id,))
        row = cur.fetchone()
        if not row: raise NotFoundError(f"match {match_id} not found")
        data = dict(row)
        cur.execute("SELECT * FROM match_requirement_results WHERE match_run_id=%s ORDER BY criterion_group,id", (data["latest_run_id"],))
        data["criteria"] = [dict(x) for x in cur.fetchall()]
        return data


def update_match(match_id, data):
    data = dict(data)
    if not data: return get_match(match_id)
    with core_cursor(commit=True) as (_, cur):
        cur.execute(f"UPDATE matches SET {','.join(f'{k}=%s' for k in data)},last_reviewed_at=NOW(),updated_at=NOW() WHERE id=%s RETURNING *", list(data.values()) + [match_id])
        row = cur.fetchone()
        if not row: raise NotFoundError(f"match {match_id} not found")
        return dict(row)


def set_override(match_id, score, reason):
    return update_match(match_id, {"is_manual_override": True, "manual_score": score, "manual_reason": reason})


def clear_override(match_id):
    with core_cursor(commit=True) as (_, cur):
        cur.execute("UPDATE matches SET is_manual_override=FALSE,manual_score=NULL,manual_reason=NULL,updated_at=NOW() WHERE id=%s RETURNING *", (match_id,))
        row = cur.fetchone()
        if not row: raise NotFoundError(f"match {match_id} not found")
        return dict(row)


def add_exclusion(data):
    with core_cursor(commit=True) as (_, cur):
        _buy(cur, data["buy_request_id"]); _property(cur, data["property_id"])
        cur.execute("""INSERT INTO match_exclusions(buy_request_id,property_id,exclusion_type,reason,expires_at,created_by)
            VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(buy_request_id,property_id) DO UPDATE SET
            exclusion_type=EXCLUDED.exclusion_type,reason=EXCLUDED.reason,expires_at=EXCLUDED.expires_at,created_by=EXCLUDED.created_by
            RETURNING *""", (data["buy_request_id"], data["property_id"], data.get("exclusion_type", "agent_decision"), data.get("reason"), data.get("expires_at"), data.get("created_by")))
        exclusion = dict(cur.fetchone())
        cur.execute("UPDATE matches SET commercial_status='archived',archived_at=NOW(),updated_at=NOW() WHERE buy_request_id=%s AND property_id=%s", (data["buy_request_id"], data["property_id"]))
        return exclusion


def list_exclusions():
    with core_cursor() as (_, cur):
        cur.execute("""SELECT e.*,b.title AS buy_title,p.title AS property_title FROM match_exclusions e
            JOIN buy_requests b ON b.id=e.buy_request_id JOIN properties p ON p.id=e.property_id ORDER BY e.created_at DESC""")
        return [dict(x) for x in cur.fetchall()]


def delete_exclusion(exclusion_id):
    with core_cursor(commit=True) as (_, cur):
        cur.execute("DELETE FROM match_exclusions WHERE id=%s RETURNING buy_request_id,property_id", (exclusion_id,))
        row = cur.fetchone()
        if not row: raise NotFoundError(f"exclusion {exclusion_id} not found")
        cur.execute("UPDATE matches SET archived_at=NULL,commercial_status='to_review',updated_at=NOW() WHERE buy_request_id=%s AND property_id=%s", (row["buy_request_id"], row["property_id"]))


def dashboard():
    with core_cursor() as (_, cur):
        cur.execute("""SELECT COUNT(*) AS total,
            COUNT(*) FILTER(WHERE compatibility_status<>'incompatible') AS compatible,
            COUNT(*) FILTER(WHERE match_class IN ('excellent','strong')) AS strong,
            COUNT(*) FILTER(WHERE commercial_status='to_review' OR commercial_status='new') AS to_review,
            COUNT(*) FILTER(WHERE is_manual_override) AS overridden,
            COALESCE(AVG(score_total),0) AS average_score FROM matches WHERE archived_at IS NULL""")
        result = dict(cur.fetchone())
        cur.execute("""SELECT m.id,b.title AS buy_title,p.title AS property_title,m.score_total,m.manual_score,m.match_class,m.compatibility_status,m.commercial_status
            FROM matches m JOIN buy_requests b ON b.id=m.buy_request_id JOIN properties p ON p.id=m.property_id
            WHERE m.archived_at IS NULL ORDER BY COALESCE(m.manual_score,m.score_total) DESC LIMIT 10""")
        result["top_matches"] = [dict(x) for x in cur.fetchall()]
        result["algorithm_version"] = ALGORITHM_VERSION
        return result
