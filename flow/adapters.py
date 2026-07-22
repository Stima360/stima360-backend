from __future__ import annotations
from datetime import datetime, timezone
from core.database import core_cursor
from core.exceptions import NotFoundError


def _one(cur, sql, params, label):
    cur.execute(sql, params)
    row = cur.fetchone()
    if not row: raise NotFoundError(label)
    return dict(row)


def load_entity(entity_type: str, entity_id: int) -> dict:
    with core_cursor() as (_, cur):
        if entity_type == "lead":
            x = _one(cur, "SELECT * FROM leads WHERE id=%s", (entity_id,), f"lead {entity_id} not found")
            cur.execute("SELECT COUNT(*) AS n FROM activities WHERE lead_id=%s", (entity_id,)); x["activity_count"] = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM tasks WHERE lead_id=%s AND status IN ('open','in_progress')", (entity_id,)); x["open_task_count"] = cur.fetchone()["n"]
            x["entity_type"]="lead"; x["entity_id"]=entity_id; return x
        if entity_type == "property":
            x = _one(cur, "SELECT * FROM properties WHERE id=%s AND archived_at IS NULL", (entity_id,), f"property {entity_id} not found")
            cur.execute("SELECT COUNT(*) AS n FROM property_documents WHERE property_id=%s AND (status IN ('missing','requested','expired','rejected') OR (expires_at IS NOT NULL AND expires_at<CURRENT_DATE))", (entity_id,)); x["document_issue_count"] = cur.fetchone()["n"]
            cur.execute("SELECT contact_id FROM property_contacts WHERE property_id=%s ORDER BY is_primary DESC,id LIMIT 1", (entity_id,)); r=cur.fetchone(); x["contact_id"] = r["contact_id"] if r else None
            cur.execute("SELECT lead_id FROM property_leads WHERE property_id=%s ORDER BY id LIMIT 1", (entity_id,)); r=cur.fetchone(); x["lead_id"] = r["lead_id"] if r else None
            x["entity_type"]="property"; x["entity_id"]=entity_id; return x
        if entity_type == "buy_request":
            x = _one(cur, "SELECT * FROM buy_requests WHERE id=%s AND archived_at IS NULL", (entity_id,), f"buy request {entity_id} not found")
            x["entity_type"]="buy_request"; x["entity_id"]=entity_id; return x
        if entity_type == "match":
            x = _one(cur, """SELECT m.*,b.contact_id,b.lead_id,b.title AS buy_title,p.title AS property_title
                FROM matches m JOIN buy_requests b ON b.id=m.buy_request_id JOIN properties p ON p.id=m.property_id
                WHERE m.id=%s AND m.archived_at IS NULL""", (entity_id,), f"match {entity_id} not found")
            cur.execute("SELECT COUNT(*) AS n FROM buy_request_interactions WHERE match_id=%s AND interaction_type='proposed'", (entity_id,)); x["proposed_count"] = cur.fetchone()["n"]
            x["entity_type"]="match"; x["entity_id"]=entity_id; return x
        if entity_type == "property_visit":
            x = _one(cur, """SELECT v.*,p.title AS property_title FROM property_visits v JOIN properties p ON p.id=v.property_id WHERE v.id=%s""", (entity_id,), f"visit {entity_id} not found")
            cur.execute("SELECT COUNT(*) AS n FROM buy_request_interactions WHERE property_visit_id=%s AND interaction_type IN ('visited','interested','discarded','offer_candidate')", (entity_id,)); x["feedback_count"] = cur.fetchone()["n"]
            x["entity_type"]="property_visit"; x["entity_id"]=entity_id; return x
    raise ValueError(f"unsupported entity_type {entity_type}")


def scan_candidates(rule_code: str, parameters: dict, limit: int) -> list[tuple[str,int]]:
    with core_cursor() as (_, cur):
        if rule_code == "FLOW-R001":
            cur.execute("SELECT id FROM leads WHERE status='open' ORDER BY created_at LIMIT %s", (limit,)); return [("lead",r["id"]) for r in cur.fetchall()]
        if rule_code == "FLOW-R002":
            cur.execute("SELECT id FROM properties WHERE archived_at IS NULL AND commercial_status NOT IN ('sold','withdrawn','archived') AND mandate_end IS NOT NULL AND mandate_end<=CURRENT_DATE+(%s||' days')::interval ORDER BY mandate_end LIMIT %s", (parameters["days_before_expiry"],limit)); return [("property",r["id"]) for r in cur.fetchall()]
        if rule_code == "FLOW-R003":
            cur.execute("SELECT DISTINCT p.id FROM properties p JOIN property_documents d ON d.property_id=p.id WHERE p.archived_at IS NULL AND (d.status IN ('missing','requested','expired','rejected') OR (d.expires_at IS NOT NULL AND d.expires_at<CURRENT_DATE)) ORDER BY p.id LIMIT %s", (limit,)); return [("property",r["id"]) for r in cur.fetchall()]
        if rule_code == "FLOW-R004":
            cur.execute("SELECT id FROM buy_requests WHERE status='active' AND archived_at IS NULL AND next_action_at IS NOT NULL AND next_action_at<=NOW()-(%s||' hours')::interval ORDER BY next_action_at LIMIT %s", (parameters["overdue_hours"],limit)); return [("buy_request",r["id"]) for r in cur.fetchall()]
        if rule_code == "FLOW-R005":
            cur.execute("SELECT id FROM matches WHERE archived_at IS NULL AND freshness_status='fresh' AND score_total>=%s AND commercial_status IN ('new','to_review') ORDER BY score_total DESC LIMIT %s", (parameters["minimum_score"],limit)); return [("match",r["id"]) for r in cur.fetchall()]
        if rule_code == "FLOW-R006":
            cur.execute("SELECT id FROM matches WHERE archived_at IS NULL AND review_required=TRUE ORDER BY updated_at LIMIT %s", (limit,)); return [("match",r["id"]) for r in cur.fetchall()]
        if rule_code == "FLOW-R007":
            cur.execute("SELECT id FROM property_visits WHERE status='completed' AND updated_at<=NOW()-(%s||' hours')::interval ORDER BY updated_at LIMIT %s", (parameters["feedback_wait_hours"],limit)); return [("property_visit",r["id"]) for r in cur.fetchall()]
    return []
