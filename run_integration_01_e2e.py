#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import secrets
import traceback
from typing import Any

import requests
from psycopg2.extras import RealDictCursor, Json

from integration_p2_support import (
    IntegrationStop,
    RunManifest,
    api_request,
    db_connect,
    fetch_row,
    postcheck,
    require_test_environment,
    rows_for_fk,
    teardown,
    validate_openapi_routes,
)

RULE_CODE="FLOW-R001"

FORBIDDEN_OWNER_FIELDS = {
    "classification", "classificazione", "a/b/c", "match_score", "buyer_budget",
    "minimum_price", "internal_notes", "commercial_notes", "storage_path",
    "document_path", "buy_request_id", "buyer_contact_id", "financial_status",
    "flow_execution_id", "commercial_score", "match_total", "score_total",
}
FORBIDDEN_OWNER_VALUE_MARKERS = {
    "NON PUBBLICARE", "segreto", "minimum_price", "internal_notes",
    "buyer_budget", "match_score", "flow_execution_id",
}
SQL_DETAIL_MARKERS = ("psycopg", "postgres", "sqlstate", "constraint", "duplicate key", "syntax error", "traceback")


def flatten_payload(value: Any, *, keys: set[str] | None = None, values: list[str] | None = None):
    keys = keys if keys is not None else set()
    values = values if values is not None else []
    if isinstance(value, dict):
        for key, item in value.items():
            keys.add(str(key).lower())
            flatten_payload(item, keys=keys, values=values)
    elif isinstance(value, list):
        for item in value:
            flatten_payload(item, keys=keys, values=values)
    elif value is not None:
        values.append(str(value))
    return keys, values


def assert_owner_privacy(payload: Any) -> None:
    keys, values = flatten_payload(payload)
    bad_keys = sorted(keys & FORBIDDEN_OWNER_FIELDS)
    joined_values = "\n".join(values).lower()
    bad_values = sorted(marker for marker in FORBIDDEN_OWNER_VALUE_MARKERS if marker.lower() in joined_values)
    if bad_keys or bad_values:
        raise AssertionError(f"OWNER privacy violata: keys={bad_keys}, value_markers={bad_values}")


def assert_error_response(response: requests.Response, expected: tuple[int, ...], label: str) -> None:
    if response.status_code in (500, 502):
        raise IntegrationStop(f"{label}: HTTP inatteso {response.status_code}: {response.text[:500]}")
    if response.status_code not in expected:
        raise AssertionError(f"{label}: HTTP {response.status_code}, atteso {expected}: {response.text[:500]}")
    text = response.text.lower()
    leaked = [marker for marker in SQL_DETAIL_MARKERS if marker in text]
    if leaked:
        raise AssertionError(f"{label}: dettagli tecnici/SQL esposti: {leaked}; body={response.text[:500]}")


def need_id(payload: dict[str, Any], label: str) -> int:
    value=payload.get("id")
    if value is None:
        raise IntegrationStop(f"Risposta senza id per {label}: {payload}")
    return int(value)


def register_children(manifest: RunManifest, table: str, column: str, value: int, scenario: str):
    manifest.register_many(table, rows_for_fk(table,column,value), scenario)


def scenario_core_property(manifest: RunManifest, base: str):
    s="core_property"; tag=manifest.run_id
    contact,_=api_request(base,"POST","/api/core/contacts",json={"contact_type":"person","display_name":tag+" OWNER","email":tag.lower()+"@example.test","phone":"+393900000001","source":"integration_p2","notes":tag})
    cid=need_id(contact,"contact"); manifest.register_pk("contacts",cid,s)
    role,_=api_request(base,"POST",f"/api/core/contacts/{cid}/roles",json={"role":"owner","is_primary":True,"metadata":{"run_id":tag}})
    manifest.register_pk("contact_roles",need_id(role,"contact_role"),s)
    lead,_=api_request(base,"POST","/api/core/leads",json={"contact_id":cid,"source":"integration_p2","pipeline":"sell","stage":"new","priority":"normal","status":"open","notes":tag})
    lid=need_id(lead,"lead"); manifest.register_pk("leads",lid,s)
    prop,_=api_request(base,"POST","/api/property/properties",json={"code":tag+"-P1","title":tag+" Immobile","property_type":"apartment","commercial_status":"active","classification":"A","city":"Alba Adriatica","microzone":"Test Integration","surface_sqm":90,"rooms":3,"bedrooms":2,"bathrooms":1,"asking_price":200000,"minimum_price":180000,"source":"integration_p2","public_notes":"pubblico "+tag,"internal_notes":"segreto "+tag,"metadata":{"run_id":tag}})
    pid=need_id(prop,"property"); manifest.register_pk("properties",pid,s)
    register_children(manifest,"property_price_history","property_id",pid,s); register_children(manifest,"property_status_history","property_id",pid,s)
    pc,_=api_request(base,"POST",f"/api/property/properties/{pid}/contacts",json={"contact_id":cid,"role":"owner","is_primary":True,"ownership_share":100,"notes":tag})
    manifest.register_pk("property_contacts",need_id(pc,"property_contact"),s)
    pl,_=api_request(base,"POST",f"/api/property/properties/{pid}/leads",json={"lead_id":lid,"relation_type":"origin"})
    manifest.register_pk("property_leads",need_id(pl,"property_lead"),s)
    act,_=api_request(base,"POST","/api/core/activities",json={"contact_id":cid,"lead_id":lid,"activity_type":"note","direction":"internal","subject":tag,"description":tag,"created_by":"integration_p2","metadata":{"run_id":tag}})
    manifest.register_pk("activities",need_id(act,"activity"),s)
    task,_=api_request(base,"POST","/api/core/tasks",json={"contact_id":cid,"lead_id":lid,"title":tag+" task","description":tag,"task_type":"integration","priority":"normal","status":"open","created_by":"integration_p2","metadata":{"run_id":tag}})
    manifest.register_pk("tasks",need_id(task,"task"),s)
    manifest.result(s,"passed",f"contact={cid}, lead={lid}, property={pid}")
    return {"contact_id":cid,"lead_id":lid,"property_id":pid}


def scenario_buy_match(manifest: RunManifest, base: str):
    s="buy_match"; tag=manifest.run_id
    contact,_=api_request(base,"POST","/api/core/contacts",json={"contact_type":"person","display_name":tag+" BUYER","email":"buyer."+tag.lower()+"@example.test","source":"integration_p2","notes":tag})
    cid=need_id(contact,"buyer contact"); manifest.register_pk("contacts",cid,s)
    lead,_=api_request(base,"POST","/api/core/leads",json={"contact_id":cid,"source":"integration_p2","pipeline":"buy","stage":"qualified","priority":"normal","status":"open","notes":tag})
    lid=need_id(lead,"buyer lead"); manifest.register_pk("leads",lid,s)
    request,_=api_request(base,"POST","/api/buy/requests",json={"contact_id":cid,"lead_id":lid,"title":tag+" richiesta","status":"active","priority":"normal","urgency":"flexible","budget_min":150000,"budget_target":200000,"budget_max":250000,"surface_min":70,"surface_target":90,"surface_max":120,"rooms_min":3,"bedrooms_min":2,"bathrooms_min":1,"notes":tag,"metadata":{"run_id":tag}})
    rid=need_id(request,"buy request"); manifest.register_pk("buy_requests",rid,s); register_children(manifest,"buy_request_history","buy_request_id",rid,s)
    loc,_=api_request(base,"POST",f"/api/buy/requests/{rid}/locations",json={"location_type":"municipality","municipality":"Alba Adriatica","priority":10,"is_required":True,"is_excluded":False})
    manifest.register_pk("buy_request_locations",need_id(loc,"buy location"),s)
    typ,_=api_request(base,"POST",f"/api/buy/requests/{rid}/typologies",json={"property_type":"apartment","requirement_level":"required","priority":10})
    manifest.register_pk("buy_request_typologies",need_id(typ,"buy typology"),s)
    prop,_=api_request(base,"POST","/api/property/properties",json={"code":tag+"-MATCH","title":tag+" Match Property","property_type":"apartment","commercial_status":"active","city":"Alba Adriatica","surface_sqm":90,"rooms":3,"bedrooms":2,"bathrooms":1,"asking_price":200000,"source":"integration_p2","metadata":{"run_id":tag}})
    pid=need_id(prop,"match property"); manifest.register_pk("properties",pid,s)
    register_children(manifest,"property_price_history","property_id",pid,s); register_children(manifest,"property_status_history","property_id",pid,s)
    match,_=api_request(base,"POST","/api/match/calculate",json={"buy_request_id":rid,"property_id":pid,"created_by":tag})
    mid=need_id(match,"match"); manifest.register_pk("matches",mid,s)
    register_children(manifest,"match_runs","buy_request_id",rid,s)
    for run_pk in [x.pk for x in manifest.created_pks if x.table=="match_runs" and x.scenario==s]:
        register_children(manifest,"match_requirement_results","match_run_id",run_pk,s)
    api_request(base,"POST",f"/api/match/matches/{mid}/refresh",json={"created_by":tag,"trigger_reason":tag})
    register_children(manifest,"match_runs","buy_request_id",rid,s)
    for run_pk in [x.pk for x in manifest.created_pks if x.table=="match_runs" and x.scenario==s]:
        register_children(manifest,"match_requirement_results","match_run_id",run_pk,s)
    register_children(manifest,"match_refresh_history","match_id",mid,s)
    score=float(match.get("score_total",0));
    if not 0 <= score <= 100: raise AssertionError(f"score MATCH fuori range: {score}")
    manifest.result(s,"passed",f"buy_request={rid}, property={pid}, match={mid}, score={score}")


def snapshot_rule(manifest: RunManifest):
    with db_connect(readonly=True) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM flow_rules WHERE code=%s",(RULE_CODE,)); row=cur.fetchone()
        conn.rollback()
    if not row: raise IntegrationStop(f"Regola {RULE_CODE} assente")
    manifest.snapshot("flow_rules",int(row["id"]),dict(row))
    return dict(row)


def scenario_flow_core(manifest: RunManifest, base: str):
    s="flow_core"; tag=manifest.run_id
    original=snapshot_rule(manifest)
    contact,_=api_request(base,"POST","/api/core/contacts",json={"contact_type":"person","display_name":tag+" FLOW","source":"integration_p2","notes":tag})
    cid=need_id(contact,"flow contact"); manifest.register_pk("contacts",cid,s)
    lead,_=api_request(base,"POST","/api/core/leads",json={"contact_id":cid,"source":"integration_p2","pipeline":"general","stage":"new","priority":"normal","status":"open","notes":tag})
    lid=need_id(lead,"flow lead"); manifest.register_pk("leads",lid,s)
    # Rende il lead inattivo da oltre 48h senza toccare record preesistenti.
    with db_connect(readonly=False) as conn:
        with conn.cursor() as cur: cur.execute("UPDATE leads SET created_at=NOW()-INTERVAL '48 hours',updated_at=NOW()-INTERVAL '48 hours' WHERE id=%s",(lid,))
        conn.commit()
    api_request(base,"POST",f"/api/flow/rules/{RULE_CODE}/deactivate")
    api_request(base,"PATCH",f"/api/flow/rules/{RULE_CODE}/parameters",json={"parameters":{"inactivity_hours":24,"task_priority":"high","cooldown_minutes":1440},"updated_by":tag})
    sim,_=api_request(base,"POST",f"/api/flow/rules/{RULE_CODE}/simulate",json={"entity_type":"lead","entity_id":lid,"requested_by":tag})
    manifest.register_pk("flow_executions",need_id(sim,"flow simulation"),s)
    if sim.get("execution_mode")!="simulation": raise AssertionError("FLOW simulation mode non rispettato")
    with db_connect(readonly=True) as conn:
        with conn.cursor() as cur: cur.execute("SELECT COUNT(*) FROM tasks WHERE lead_id=%s AND metadata->>'source'='flow'",(lid,)); count=cur.fetchone()[0]
        conn.rollback()
    if count!=0: raise AssertionError("La simulazione FLOW ha creato task")
    api_request(base,"POST",f"/api/flow/rules/{RULE_CODE}/activate",json={"activated_by":tag})
    event,_=api_request(base,"POST","/api/flow/events",json={"event_type":"core.lead_created","entity_type":"lead","entity_id":lid,"source_module":"core","payload":{"run_id":tag},"deduplication_key":tag+"-FLOW"})
    event_row=event.get("event") or {}
    eid=need_id(event_row,"flow event"); manifest.register_pk("flow_events",eid,s)
    executions=event.get("executions",[])
    for execution in executions: manifest.register_pk("flow_executions",execution.get("id"),s)
    register_children(manifest,"flow_executions","event_id",eid,s)
    for ex in [x.pk for x in manifest.created_pks if x.table=="flow_executions" and x.scenario==s]: register_children(manifest,"flow_action_records","execution_id",ex,s)
    register_children(manifest,"tasks","lead_id",lid,s)
    # Stato forzato creato nello stesso run per la matrice HTTP retry_count=3.
    with db_connect(readonly=False) as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO flow_executions(rule_id,entity_type,entity_id,execution_mode,status,conditions_result,actions_result,rule_version,parameters_snapshot,parameters_hash,error_message,retry_count,max_retry,started_at,completed_at,created_at) VALUES(%s,'lead',%s,'live','failed','{}','{}',%s,%s,%s,%s,3,3,NOW(),NOW(),NOW()) RETURNING id""",(original["id"],lid,original["code_version"],Json(original["parameters"]),"0"*64,tag))
            forced=int(cur.fetchone()[0])
        conn.commit()
    manifest.register_pk("flow_executions",forced,s)
    _,response=api_request(base,"POST",f"/api/flow/executions/{forced}/retry",expected=(409,),json={"requested_by":tag})
    manifest.result(s,"passed",f"lead={lid}, event={eid}, retry_http={response.status_code}")
    return {"lead_id": lid, "forced_execution_id": forced}


def scenario_owner(manifest: RunManifest, base: str):
    s="owner"; tag=manifest.run_id
    contact,_=api_request(base,"POST","/api/core/contacts",json={"contact_type":"person","display_name":tag+" PORTAL","email":"owner."+tag.lower()+"@example.test","source":"integration_p2","notes":tag})
    cid=need_id(contact,"owner contact"); manifest.register_pk("contacts",cid,s)
    prop,_=api_request(base,"POST","/api/property/properties",json={"code":tag+"-OWNER","title":tag+" Owner Property","property_type":"apartment","commercial_status":"active","classification":"A","city":"Alba Adriatica","asking_price":220000,"minimum_price":180000,"internal_notes":"NON PUBBLICARE "+tag,"metadata":{"run_id":tag}})
    pid=need_id(prop,"owner property"); manifest.register_pk("properties",pid,s)
    register_children(manifest,"property_price_history","property_id",pid,s); register_children(manifest,"property_status_history","property_id",pid,s)
    account,_=api_request(base,"POST","/api/owner/admin/accounts",json={"contact_id":cid})
    aid=need_id(account,"owner account"); manifest.register_pk("owner_accounts",aid,s); register_children(manifest,"owner_audit_log","owner_account_id",aid,s)
    access,_=api_request(base,"POST","/api/owner/admin/access",json={"owner_account_id":aid,"property_id":pid,"access_role":"owner","is_primary":True})
    access_id=need_id(access,"owner access"); manifest.register_pk("owner_property_access",access_id,s); register_children(manifest,"owner_audit_log","owner_account_id",aid,s)
    tok,_=api_request(base,"POST",f"/api/owner/admin/accounts/{aid}/tokens",json={"token_type":"login","expires_minutes":30,"created_by":tag})
    token_id=int(tok["token_id"]); raw_token=tok["token"]; manifest.register_pk("owner_access_tokens",token_id,s); register_children(manifest,"owner_audit_log","owner_account_id",aid,s)
    token_row = fetch_row("owner_access_tokens", token_id)
    if not token_row:
        raise AssertionError("Token OWNER non trovato nel database")
    stored_hash = token_row.get("token_hash")
    if not stored_hash or stored_hash == raw_token:
        raise AssertionError("Token OWNER non hashato correttamente")
    if stored_hash != hashlib.sha256(raw_token.encode()).hexdigest():
        raise AssertionError("Hash token OWNER non corrisponde al contratto SHA-256")
    if raw_token in json.dumps(token_row, default=str, ensure_ascii=False):
        raise AssertionError("Token grezzo OWNER recuperabile dal record database")
    manifest.result("owner_token_storage", "passed", f"token_id={token_id}, raw_absent=true, hash_present=true")
    session=requests.Session()
    _,login_response=api_request(base,"POST","/api/owner/portal/auth/token",expected=(204,),session=session,json={"token":raw_token})
    cookie=login_response.headers.get("set-cookie","").lower()
    for flag in ("httponly","secure","samesite=lax"):
        if flag not in cookie: raise AssertionError(f"Cookie runtime privo di {flag}")
    register_children(manifest,"owner_sessions","owner_account_id",aid,s); register_children(manifest,"owner_audit_log","owner_account_id",aid,s)
    # Replay del token deve essere indistinguibile: 404.
    api_request(base,"POST","/api/owner/portal/auth/token",expected=(404,),json={"token":raw_token})
    dash,_=api_request(base,"GET","/api/owner/portal/dashboard",session=session)
    assert_owner_privacy(dash)
    pub,_=api_request(base,"POST","/api/owner/admin/publications",json={"property_id":pid,"publication_type":"general_update","title":tag,"summary":"summary "+tag,"body":"body v1 "+tag})
    pub1=need_id(pub,"owner publication"); manifest.register_pk("owner_publications",pub1,s); register_children(manifest,"owner_audit_log","owner_account_id",aid,s); register_children(manifest,"owner_audit_log","property_id",pid,s)
    # Draft invisibile.
    api_request(base,"GET",f"/api/owner/portal/publications/{pub1}",expected=(404,),session=session)
    api_request(base,"POST",f"/api/owner/admin/publications/{pub1}/publish")
    register_children(manifest,"owner_audit_log","property_id",pid,s)
    published_payload,_=api_request(base,"GET",f"/api/owner/portal/publications/{pub1}",session=session)
    assert_owner_privacy(published_payload)
    register_children(manifest,"owner_publication_reads","publication_id",pub1,s)
    api_request(base,"POST",f"/api/owner/portal/publications/{pub1}/acknowledge",session=session)
    _,immutable=api_request(base,"PATCH",f"/api/owner/admin/publications/{pub1}",expected=(409,),json={"title":"tampered "+tag})
    pub2,_=api_request(base,"POST",f"/api/owner/admin/publications/{pub1}/supersede",json={"property_id":pid,"publication_type":"general_update","title":tag+" v2","summary":"summary v2","body":"body v2 "+tag})
    pub2_id=need_id(pub2,"owner publication v2"); manifest.register_pk("owner_publications",pub2_id,s); register_children(manifest,"owner_audit_log","property_id",pid,s)
    if int(pub2.get("version_number",0))!=2: raise AssertionError("Versionamento OWNER non incrementato")
    fb,_=api_request(base,"POST",f"/api/owner/portal/properties/{pid}/feedback",session=session,json={"feedback_type":"general_message","subject":tag,"message":"feedback "+tag})
    manifest.register_pk("owner_feedback",need_id(fb,"owner feedback"),s); register_children(manifest,"owner_audit_log","owner_account_id",aid,s); register_children(manifest,"owner_audit_log","property_id",pid,s)
    api_request(base,"POST",f"/api/owner/admin/access/{access_id}/revoke")
    register_children(manifest,"owner_audit_log","owner_account_id",aid,s); register_children(manifest,"owner_audit_log","property_id",pid,s)
    api_request(base,"GET",f"/api/owner/portal/properties/{pid}",expected=(404,),session=session)
    manifest.result(s,"passed",f"account={aid}, property={pid}, publications={pub1}/{pub2_id}, immutable={immutable.status_code}")
    return {"contact_id": cid, "account_id": aid, "access_id": access_id, "property_id": pid, "publication_id": pub1}


def scenario_http_stateful(manifest: RunManifest, base: str, flow_state: dict[str, int], owner_state: dict[str, int]) -> None:
    s = "http_stateful"
    tag = manifest.run_id
    checks: list[str] = []

    # Duplicato logico su entità creata nello stesso run.
    r = requests.post(base + "/api/owner/admin/accounts", json={"contact_id": owner_state["contact_id"]}, timeout=45)
    assert_error_response(r, (409,), "duplicato OWNER account")
    checks.append("duplicate=409")

    # Stato incompatibile: pubblicazione già published pubblicata nuovamente.
    r = requests.post(base + f"/api/owner/admin/publications/{owner_state["publication_id"]}/publish", timeout=45)
    assert_error_response(r, (409,), "stato incompatibile publish")
    checks.append("incompatible_state=409")

    # Published immutabile.
    r = requests.patch(
        base + f"/api/owner/admin/publications/{owner_state["publication_id"]}",
        json={"title": "HTTP MATRIX " + tag}, timeout=45,
    )
    assert_error_response(r, (409,), "published immutabile")
    checks.append("immutable=409")

    # Quarto retry FLOW bloccato.
    r = requests.post(
        base + f"/api/flow/executions/{flow_state["forced_execution_id"]}/retry",
        json={"requested_by": tag}, timeout=45,
    )
    assert_error_response(r, (409,), "quarto retry FLOW")
    checks.append("retry4=409")

    # Payload sicuramente invalido e non persistente.
    r = requests.post(base + "/api/owner/admin/accounts", json={}, timeout=45)
    assert_error_response(r, (400, 422), "payload invalido")
    checks.append(f"invalid_payload={r.status_code}")

    manifest.result(s, "passed", ", ".join(checks))


def main():
    env=require_test_environment(require_http=True,require_branch=True)
    if os.getenv("INTEGRATION_P2_E2E_AUTHORIZED")!="YES":
        raise SystemExit("BLOCCATO: impostare INTEGRATION_P2_E2E_AUTHORIZED=YES solo dopo approvazione esecutiva")
    validate_openapi_routes(env.backend)
    run_id="E2E_INT01_"+secrets.token_hex(6)
    manifest=RunManifest.start(run_id,env); manifest.write()
    failure=None
    try:
        scenario_core_property(manifest,env.backend)
        scenario_buy_match(manifest,env.backend)
        flow_state = scenario_flow_core(manifest,env.backend)
        owner_state = scenario_owner(manifest,env.backend)
        scenario_http_stateful(manifest, env.backend, flow_state, owner_state)
        manifest.status="tests_passed"
    except Exception as exc:
        failure=exc; manifest.status="failed"; manifest.result("orchestrator","failed",f"{type(exc).__name__}: {exc}")
    finally:
        try:
            teardown(manifest)
            manifest.result("teardown","passed","Tutte le PK registrate sono state rimosse e gli originali ripristinati")
        except Exception as exc:
            failure=failure or exc; manifest.status="teardown_failed"; manifest.result("teardown","failed",str(exc))
        try:
            result=postcheck(manifest)
            manifest.result("postcheck","passed",str(result))
        except Exception as exc:
            failure=failure or exc; manifest.status="postcheck_failed"; manifest.result("postcheck","failed",str(exc))
        manifest.finished_at=dt.datetime.now(dt.timezone.utc).isoformat()
        if failure is None: manifest.status="passed"
        manifest.write()
    if failure:
        raise SystemExit(f"INTEGRATION P2 E2E FALLITO: {failure}\nManifest: {manifest.path()}")
    print(f"INTEGRATION P2 E2E SUPERATO — run_id={run_id}\nManifest: {manifest.path()}")

if __name__=="__main__":
    main()
