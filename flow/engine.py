from __future__ import annotations
from datetime import datetime, timezone, timedelta


def evaluate(rule_code: str, entity: dict, p: dict) -> tuple[bool, list[str]]:
    now = datetime.now(timezone.utc)
    reasons=[]
    if rule_code == "FLOW-R001":
        created=entity.get('created_at'); old_enough=created is None or created <= now-timedelta(hours=p['inactivity_hours'])
        matched = entity.get("status")=="open" and old_enough and entity.get("activity_count",0)==0 and entity.get("open_task_count",0)==0
        reasons.append("lead aperto senza attività/task" if matched else "lead già lavorato o non aperto")
    elif rule_code == "FLOW-R002":
        end=entity.get("mandate_end"); days=(end - now.date()).days if end else 99999
        matched=entity.get("commercial_status") not in {"sold","withdrawn","archived"} and 0 <= days <= p["days_before_expiry"]
        reasons.append(f"giorni alla scadenza: {days}")
    elif rule_code == "FLOW-R003":
        n=int(entity.get("document_issue_count") or 0); matched=n>=p["minimum_issue_count"]; reasons.append(f"criticità documentali: {n}")
    elif rule_code == "FLOW-R004":
        due=entity.get("next_action_at"); matched=entity.get("status")=="active" and due is not None and due <= now-timedelta(hours=p["overdue_hours"]); reasons.append("prossima azione scaduta" if matched else "azione non scaduta")
    elif rule_code == "FLOW-R005":
        matched_at=entity.get('first_matched_at') or entity.get('created_at') or entity.get('last_calculated_at'); aged=matched_at is None or matched_at <= now-timedelta(days=p['maximum_days_without_proposal'])
        matched=entity.get("freshness_status")=="fresh" and float(entity.get("score_total") or 0)>=p["minimum_score"] and entity.get("commercial_status") in {"new","to_review"} and int(entity.get("proposed_count") or 0)==0 and aged
        reasons.append("match forte non proposto" if matched else "match non eleggibile")
    elif rule_code == "FLOW-R006":
        matched=bool(entity.get("review_required")); reasons.append("revisione richiesta" if matched else "revisione non richiesta")
    elif rule_code == "FLOW-R007":
        updated=entity.get("updated_at") or entity.get("scheduled_at"); matched=entity.get("status")=="completed" and int(entity.get("feedback_count") or 0)==0 and updated <= now-timedelta(hours=p["feedback_wait_hours"]); reasons.append("visita senza feedback" if matched else "feedback presente o soglia non raggiunta")
    else: raise ValueError(f"unsupported rule {rule_code}")
    return matched,reasons


def build_action(rule_code: str, entity: dict, p: dict) -> dict:
    labels={
        "FLOW-R001":"Primo contatto lead",
        "FLOW-R002":"Verificare rinnovo incarico",
        "FLOW-R003":"Risolvere criticità documentale",
        "FLOW-R004":"Ricontattare acquirente: azione scaduta",
        "FLOW-R005":"Revisionare match forte non proposto",
        "FLOW-R006":"Revisionare variazione MATCH",
        "FLOW-R007":"Raccogliere feedback dopo visita",
    }
    due_hours={"FLOW-R001":4,"FLOW-R002":24,"FLOW-R003":24,"FLOW-R004":4,"FLOW-R005":8,"FLOW-R006":8,"FLOW-R007":8}
    return {
        "action_type":"create_core_task",
        "title":labels[rule_code],
        "description":f"Task generato da {rule_code} per {entity['entity_type']} #{entity['entity_id']}",
        "priority":p["task_priority"],
        "due_hours":due_hours[rule_code],
        "contact_id":entity.get("contact_id"),
        "lead_id":entity.get("lead_id") if entity["entity_type"]!="lead" else entity["id"],
        "assigned_to":entity.get("assigned_to"),
    }
