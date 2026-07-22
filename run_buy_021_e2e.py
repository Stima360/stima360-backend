#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import traceback
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import psycopg2
from psycopg2.extras import RealDictCursor, Json
import requests

PREFIX = "E2E_BUY021_"
DEFAULT_BASE_URL = "https://stima360-backend-test.onrender.com"
TIMEOUT = 30


class TestFailure(RuntimeError):
    pass


def check(condition: bool, message: str) -> None:
    if not condition:
        raise TestFailure(message)
    print(f"  OK  {message}")


def as_num(value):
    if value is None:
        return None
    return float(value)


class Runner:
    def __init__(self) -> None:
        self.run_id = f"{PREFIX}{uuid.uuid4().hex[:10]}"
        self.base_url = os.getenv("E2E_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json", "X-E2E-Run": self.run_id})
        self.conn = None
        self.ids: dict[str, list[int] | int] = {
            "contacts": [], "leads": [], "properties": [], "visits": [],
            "buy_requests": [], "tasks": [], "matches": [], "interactions": []
        }

    def connect(self):
        required = ["DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD"]
        missing = [k for k in required if not os.getenv(k)]
        if missing:
            raise TestFailure("Variabili DB mancanti: " + ", ".join(missing))
        self.conn = psycopg2.connect(
            host=os.environ["DB_HOST"],
            port=os.getenv("DB_PORT", "5432"),
            dbname=os.environ["DB_NAME"],
            user=os.environ["DB_USER"],
            password=os.environ["DB_PASSWORD"],
            cursor_factory=RealDictCursor,
        )
        self.conn.autocommit = True

    def safety_guard(self):
        host = (urlparse(self.base_url).hostname or "").lower()
        check(host == "stima360-backend-test.onrender.com", f"endpoint test confermato: {host}")
        with self.conn.cursor() as cur:
            cur.execute("SELECT current_database() db, inet_server_addr()::text server_addr")
            row = cur.fetchone()
        db = str(row["db"]).lower()
        check("test" in db, f"database test confermato: {row['db']}")
        check("stima360-backend.onrender.com" not in self.base_url, "endpoint produzione escluso")

    def api(self, method, path, payload=None, expected=(200,)):
        url = self.base_url + path
        response = self.session.request(method, url, json=payload, timeout=TIMEOUT)
        if response.status_code not in expected:
            raise TestFailure(
                f"{method} {path}: atteso {expected}, ricevuto {response.status_code}: {response.text[:700]}"
            )
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except Exception as exc:
            raise TestFailure(f"{method} {path}: risposta non JSON: {response.text[:500]}") from exc

    def cleanup_stale(self):
        # Rimuove esclusivamente residui riconoscibili di precedenti esecuzioni di questa suite.
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM tasks WHERE title LIKE %s", (PREFIX + "%",))
            cur.execute("DELETE FROM buy_requests WHERE title LIKE %s", (PREFIX + "%",))
            cur.execute("DELETE FROM properties WHERE code LIKE %s", (PREFIX + "%",))
            cur.execute("DELETE FROM leads WHERE source LIKE %s", (PREFIX + "%",))
            cur.execute("DELETE FROM contacts WHERE source LIKE %s", (PREFIX + "%",))

    def cleanup_current(self):
        if not self.conn:
            return
        with self.conn.cursor() as cur:
            # Titoli/codici/source sono il confine di sicurezza: non vengono usati ID esterni.
            cur.execute("DELETE FROM tasks WHERE title LIKE %s", (self.run_id + "%",))
            cur.execute("DELETE FROM buy_requests WHERE title LIKE %s", (self.run_id + "%",))
            cur.execute("DELETE FROM properties WHERE code LIKE %s", (self.run_id + "%",))
            cur.execute("DELETE FROM leads WHERE source=%s", (self.run_id,))
            cur.execute("DELETE FROM contacts WHERE source=%s", (self.run_id,))

    def bootstrap(self):
        print("\n[1/10] Creazione dati test isolati")
        with self.conn.cursor() as cur:
            cur.execute("""
                INSERT INTO contacts(contact_type,first_name,last_name,display_name,email,email_normalized,phone,phone_normalized,source,status)
                VALUES('person','E2E','Buyer',%s,%s,%s,%s,%s,%s,'active') RETURNING id
            """, (self.run_id, f"{self.run_id.lower()}@example.test", f"{self.run_id.lower()}@example.test", "+390000000001", "390000000001", self.run_id))
            contact_id = cur.fetchone()["id"]
            self.ids["contacts"].append(contact_id)

            cur.execute("""
                INSERT INTO leads(contact_id,source,pipeline,stage,priority,status,notes)
                VALUES(%s,%s,'buy','qualified','high','open',%s) RETURNING id
            """, (contact_id, self.run_id, self.run_id))
            lead_id = cur.fetchone()["id"]
            self.ids["leads"].append(lead_id)

            props = [
                (self.run_id + "_P1", "L'appartamento E2E sul mare", "Tortoreto", "Lido Centro", 82, 3, 2, 1, True, 220000),
                (self.run_id + "_P2", "Immobile E2E controllo visita", "Alba Adriatica", "Villa Fiore", 70, 3, 2, 1, True, 195000),
            ]
            for code, title, city, microzone, sqm, rooms, beds, baths, elevator, price in props:
                cur.execute("""
                    INSERT INTO properties(code,title,property_type,commercial_status,classification,city,microzone,
                                           surface_sqm,rooms,bedrooms,bathrooms,elevator,condition,asking_price,metadata)
                    VALUES(%s,%s,'apartment','active','A',%s,%s,%s,%s,%s,%s,%s,'good',%s,%s) RETURNING id
                """, (code, title, city, microzone, sqm, rooms, beds, baths, elevator, price, Json({"e2e_run": self.run_id})))
                self.ids["properties"].append(cur.fetchone()["id"])

            rome = ZoneInfo("Europe/Rome")
            visit_time = datetime.now(rome) + timedelta(days=1)
            for prop_id in self.ids["properties"]:
                cur.execute("""
                    INSERT INTO property_visits(property_id,contact_id,lead_id,scheduled_at,status,created_by)
                    VALUES(%s,%s,%s,%s,'confirmed',%s) RETURNING id
                """, (prop_id, contact_id, lead_id, visit_time, self.run_id))
                self.ids["visits"].append(cur.fetchone()["id"])

        check(len(self.ids["properties"]) == 2, "creati due immobili esclusivamente E2E")
        check(len(self.ids["visits"]) == 2, "create due visite esclusivamente E2E")
        return contact_id, lead_id

    def test_ui_and_health(self):
        print("\n[2/10] UI e asset BUY 0.2.1")
        html = self.session.get(self.base_url + "/buy-admin/", timeout=TIMEOUT)
        check(html.status_code == 200, "UI /buy-admin/ raggiungibile")
        check("BUY 0.2" in html.text, "UI BUY corretta distribuita")
        js = self.session.get(self.base_url + "/buy-admin/assets/app.js", timeout=TIMEOUT)
        check(js.status_code == 200, "asset JavaScript raggiungibile")
        check("function openAction(matchId)" in js.text, "titoli immobili non interpolati nell'onclick")
        check("openAction(${m.id},'" not in js.text, "rimossa la variante vulnerabile agli apostrofi")
        self.api("GET", "/api/buy/dashboard")
        self.api("GET", "/api/match/dashboard")
        check(True, "API BUY e MATCH operative")

    def create_buy_request(self, contact_id, lead_id, suffix="MAIN"):
        payload = {
            "contact_id": contact_id,
            "lead_id": lead_id,
            "title": f"{self.run_id}_{suffix}",
            "status": "draft",
            "priority": "high",
            "urgency": "within_3_months",
            "budget_min": 180000,
            "budget_target": 215000,
            "budget_max": 230000,
            "budget_flexibility_percent": 5,
            "surface_min": 70,
            "surface_target": 80,
            "surface_max": 100,
            "rooms_min": 3,
            "bedrooms_min": 2,
            "bathrooms_min": 1,
            "metadata": {"e2e_run": self.run_id},
        }
        result = self.api("POST", "/api/buy/requests", payload, expected=(201,))
        self.ids["buy_requests"].append(result["id"])
        return result["id"]

    def test_criteria(self, request_id):
        print("\n[3/10] Località, tipologie, requisiti e storico criteri")
        loc = self.api("POST", f"/api/buy/requests/{request_id}/locations", {
            "location_type": "microzone", "municipality": "Tortoreto", "microzone": "Lido Centro",
            "priority": 10, "is_required": True, "is_excluded": False
        }, expected=(201,))
        typ = self.api("POST", f"/api/buy/requests/{request_id}/typologies", {
            "property_type": "apartment", "requirement_level": "required", "priority": 10
        }, expected=(201,))
        feat_required = self.api("POST", f"/api/buy/requests/{request_id}/features", {
            "feature_code": "elevator", "requirement_level": "required", "value_type": "boolean", "value_boolean": True
        }, expected=(201,))
        feat_pref = self.api("POST", f"/api/buy/requests/{request_id}/features", {
            "feature_code": "sea_view", "requirement_level": "preferred", "value_type": "boolean", "value_boolean": True
        }, expected=(201,))

        self.api("DELETE", f"/api/buy/features/{feat_pref['id']}", expected=(204,))
        feat_pref = self.api("POST", f"/api/buy/requests/{request_id}/features", {
            "feature_code": "balcony", "requirement_level": "preferred", "value_type": "boolean", "value_boolean": True
        }, expected=(201,))

        data = self.api("GET", f"/api/buy/requests/{request_id}/workflow")
        check(any(x["id"] == loc["id"] for x in data["locations"]), "località presente")
        check(any(x["id"] == typ["id"] for x in data["typologies"]), "tipologia presente")
        check(any(x["id"] == feat_required["id"] for x in data["features"]), "requisito obbligatorio presente")
        descriptions = [str(x.get("description") or "") for x in data["history"]]
        check(any("Criterio aggiunto" in x for x in descriptions), "storico aggiunta criteri presente")
        check(any("Criterio rimosso" in x for x in descriptions), "storico rimozione criteri presente")

        normalized = self.api("GET", f"/api/buy/requests/{request_id}/normalized")
        check(normalized["locations"] and normalized["typologies"] and normalized["features"], "vista normalizzata MATCH completa")

    def test_request_updates(self, request_id):
        print("\n[4/10] Stato, budget, finanza, prossima azione e fuso Europe/Rome")
        # Deve fallire perché il target supererebbe il massimo esistente.
        self.api("PATCH", f"/api/buy/requests/{request_id}", {"budget_target": 999999}, expected=(400, 422))
        check(True, "intervalli budget invalidi rifiutati")

        updated = self.api("PATCH", f"/api/buy/requests/{request_id}", {
            "budget_min": 185000, "budget_target": 218000, "budget_max": 235000
        })
        check(as_num(updated["budget_target"]) == 218000, "budget modificato")

        updated = self.api("PATCH", f"/api/buy/requests/{request_id}", {
            "finance_status": "mortgage_preapproved", "mortgage_required": True,
            "mortgage_preapproved": True, "available_cash": 65000,
            "maximum_monthly_payment": 950, "finance_notes": "Finanza E2E verificata",
            "finance_review_at": datetime.now(ZoneInfo("Europe/Rome")).isoformat()
        })
        check(updated["finance_status"] == "mortgage_preapproved", "stato finanziario modificato")

        rome = ZoneInfo("Europe/Rome")
        local_today = datetime.now(rome).replace(hour=23, minute=15, second=0, microsecond=0)
        updated = self.api("PATCH", f"/api/buy/requests/{request_id}", {
            "next_action_at": local_today.isoformat(), "next_action_note": "Richiamare l'acquirente E2E"
        })
        check("Richiamare" in updated["next_action_note"], "prossima azione modificata")

        updated = self.api("PATCH", f"/api/buy/requests/{request_id}", {"status": "active"})
        check(updated["status"] == "active", "richiesta portata in stato active")

        workflow = self.api("GET", f"/api/buy/requests/{request_id}/workflow")
        events = {x["event_type"] for x in workflow["history"]}
        check("finance_updated" in events, "storico finanziario presente")
        check("next_action_updated" in events, "storico prossima azione presente")
        check("status_changed" in events, "storico stato presente")

        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT ((next_action_at AT TIME ZONE 'Europe/Rome')::date =
                        (NOW() AT TIME ZONE 'Europe/Rome')::date) AS is_today
                FROM buy_requests WHERE id=%s
            """, (request_id,))
            check(cur.fetchone()["is_today"] is True, "data interpretata come oggi nel fuso Europe/Rome")

    def calculate_match(self, request_id):
        print("\n[5/10] MATCH reale e titolo con apostrofo")
        prop_id = self.ids["properties"][0]
        match = self.api("POST", "/api/match/calculate", {
            "buy_request_id": request_id, "property_id": prop_id, "created_by": self.run_id
        }, expected=(201,))
        self.ids["matches"].append(match["id"])
        check(0 <= as_num(match["score_total"]) <= 100, "punteggio MATCH 0-100")
        check(match["match_class"] in {"excellent","strong","good","possible","weak","poor","incompatible"}, "classe MATCH valida")

        workflow = self.api("GET", f"/api/buy/requests/{request_id}/workflow")
        found = next(x for x in workflow["matches"] if x["id"] == match["id"])
        check(found["property_title"] == "L'appartamento E2E sul mare", "titolo con apostrofo restituito correttamente")
        return match["id"]

    def test_interactions_and_match(self, request_id, match_id):
        print("\n[6/10] Proposto, scartato, visitato, rifiuti e coerenza MATCH")
        proposed = self.api("POST", f"/api/buy/requests/{request_id}/matches/{match_id}/decision", {
            "action": "proposed", "notes": "Proposta E2E", "created_by": self.run_id
        }, expected=(201,))
        self.ids["interactions"].append(proposed["id"])
        match = self.api("GET", f"/api/match/matches/{match_id}")
        check(match["commercial_status"] == "suggested", "proposto sincronizza MATCH a suggested")

        self.api("POST", f"/api/buy/requests/{request_id}/matches/{match_id}/decision", {
            "action": "discarded", "notes": "senza motivo"
        }, expected=(400, 422))
        check(True, "scarto senza motivo rifiutato")

        self.api("POST", f"/api/buy/requests/{request_id}/matches/{match_id}/decision", {
            "action": "discarded", "reason_code": "motivo_inventato"
        }, expected=(400, 422))
        check(True, "motivo di rifiuto non valido rifiutato")

        discarded = self.api("POST", f"/api/buy/requests/{request_id}/matches/{match_id}/decision", {
            "action": "discarded", "reason_code": "price_too_high", "notes": "Prezzo alto E2E"
        }, expected=(201,))
        self.ids["interactions"].append(discarded["id"])
        match = self.api("GET", f"/api/match/matches/{match_id}")
        check(match["commercial_status"] == "rejected", "scartato sincronizza MATCH a rejected")

        # Visita di un altro immobile: deve essere respinta.
        self.api("POST", f"/api/buy/requests/{request_id}/interactions", {
            "match_id": match_id, "property_visit_id": self.ids["visits"][1],
            "interaction_type": "visited", "notes": "visita sbagliata"
        }, expected=(400, 422))
        check(True, "visita appartenente a un altro immobile rifiutata")

        visited = self.api("POST", f"/api/buy/requests/{request_id}/interactions", {
            "match_id": match_id, "property_visit_id": self.ids["visits"][0],
            "interaction_type": "visited", "notes": "Visita E2E corretta", "created_by": self.run_id
        }, expected=(201,))
        self.ids["interactions"].append(visited["id"])
        match = self.api("GET", f"/api/match/matches/{match_id}")
        check(match["commercial_status"] == "visited", "visita corretta sincronizza MATCH a visited")

        updated = self.api("PATCH", f"/api/buy/interactions/{visited['id']}", {"notes": "Visita E2E aggiornata"})
        check(updated["notes"] == "Visita E2E aggiornata", "interazione modificata")

        self.api("DELETE", f"/api/buy/interactions/{visited['id']}", expected=(204,))
        match = self.api("GET", f"/api/match/matches/{match_id}")
        check(match["commercial_status"] == "rejected", "rimozione interazione ripristina l'ultimo stato MATCH")

        # Ricrea il visitato per verificare il workflow finale.
        visited2 = self.api("POST", f"/api/buy/requests/{request_id}/interactions", {
            "match_id": match_id, "property_visit_id": self.ids["visits"][0],
            "interaction_type": "visited", "notes": "Visita finale E2E", "created_by": self.run_id
        }, expected=(201,))
        self.ids["interactions"].append(visited2["id"])

        workflow = self.api("GET", f"/api/buy/requests/{request_id}/workflow")
        kinds = [x["interaction_type"] for x in workflow["interactions"]]
        check("proposed" in kinds and "discarded" in kinds and "visited" in kinds, "workflow contiene proposto, scartato e visitato")
        descriptions = [str(x.get("description") or "") for x in workflow["history"]]
        check(any("Interazione aggiornata" in x for x in descriptions), "storico modifica interazione presente")
        check(any("Interazione rimossa" in x for x in descriptions), "storico rimozione interazione presente")

    def test_tasks(self, request_id):
        print("\n[7/10] Collegamento ai task CORE")
        due = (datetime.now(ZoneInfo("Europe/Rome")) + timedelta(hours=2)).isoformat()
        task = self.api("POST", f"/api/buy/requests/{request_id}/tasks", {
            "title": f"{self.run_id}_TASK richiamare",
            "description": "Task E2E BUY",
            "priority": "urgent",
            "due_at": due,
            "created_by": self.run_id
        }, expected=(201,))
        self.ids["tasks"].append(task["id"])
        tasks = self.api("GET", f"/api/buy/requests/{request_id}/tasks")["items"]
        linked = next(x for x in tasks if x["id"] == task["id"])
        check(linked["contact_id"] == self.ids["contacts"][0], "task CORE collegato al contatto test")
        check(linked["lead_id"] == self.ids["leads"][0], "task CORE collegato al lead test")
        check(linked["link_id"] == task["link_id"], "link BUY-task creato")

        self.api("DELETE", f"/api/buy/task-links/{task['link_id']}", expected=(204,))
        tasks_after = self.api("GET", f"/api/buy/requests/{request_id}/tasks")["items"]
        check(not any(x["id"] == task["id"] for x in tasks_after), "task scollegato da BUY senza cancellare dati esterni")
        workflow = self.api("GET", f"/api/buy/requests/{request_id}/workflow")
        check(any(x["event_type"] == "task_unlinked" for x in workflow["history"]), "storico scollegamento task presente")

    def test_dashboard_kpi(self, contact_id, lead_id, main_request_id):
        print("\n[8/10] KPI e richieste archiviate")
        archived_id = self.create_buy_request(contact_id, lead_id, suffix="ARCHIVED")
        self.api("PATCH", f"/api/buy/requests/{archived_id}", {"status": "active"})
        # Crea un match reale sul secondo immobile e una interazione, poi archivia la richiesta.
        m = self.api("POST", "/api/match/calculate", {
            "buy_request_id": archived_id, "property_id": self.ids["properties"][1], "created_by": self.run_id
        }, expected=(201,))
        self.ids["matches"].append(m["id"])
        self.api("POST", f"/api/buy/requests/{archived_id}/matches/{m['id']}/decision", {
            "action": "proposed", "notes": "Da escludere KPI"
        }, expected=(201,))
        self.api("DELETE", f"/api/buy/requests/{archived_id}")

        dashboard = self.api("GET", "/api/buy/dashboard")
        with self.conn.cursor() as cur:
            cur.execute("""SELECT COUNT(*) FILTER(WHERE archived_at IS NULL) total,
                COUNT(*) FILTER(WHERE status='active' AND archived_at IS NULL) active,
                COUNT(*) FILTER(WHERE status='draft' AND archived_at IS NULL) draft,
                COUNT(*) FILTER(WHERE priority IN ('high','urgent') AND status='active' AND archived_at IS NULL) priority,
                COUNT(*) FILTER(WHERE next_action_at IS NOT NULL AND next_action_at<NOW() AND status='active' AND archived_at IS NULL) overdue_actions,
                COUNT(*) FILTER(WHERE (next_action_at AT TIME ZONE 'Europe/Rome')::date=(NOW() AT TIME ZONE 'Europe/Rome')::date AND status='active' AND archived_at IS NULL) actions_today,
                COALESCE(SUM(budget_target) FILTER(WHERE status='active' AND archived_at IS NULL),0) active_target_budget
                FROM buy_requests""")
            expected = cur.fetchone()
            cur.execute("""SELECT i.interaction_type,COUNT(*) count FROM buy_request_interactions i
                JOIN buy_requests b ON b.id=i.buy_request_id WHERE b.archived_at IS NULL GROUP BY i.interaction_type""")
            expected_interactions = {x["interaction_type"]: x["count"] for x in cur.fetchall()}

        for field in ("total","active","draft","priority","overdue_actions","actions_today"):
            check(int(dashboard[field]) == int(expected[field]), f"KPI {field} coerente con database test")
        check(as_num(dashboard["active_target_budget"]) == as_num(expected["active_target_budget"]), "KPI budget attivo coerente")
        check(dashboard.get("interaction_counts", {}) == expected_interactions, "KPI interazioni esclude richieste archiviate")
        check(int(dashboard["actions_today"]) >= 1, "azione odierna Europe/Rome conteggiata")

    def test_api_list_and_final_state(self, request_id):
        print("\n[9/10] Ricerca API e stato finale")
        items = self.api("GET", f"/api/buy/requests?search={self.run_id}")["items"]
        check(any(x["id"] == request_id for x in items), "ricerca BUY trova solo il dato riconoscibile")
        workflow = self.api("GET", f"/api/buy/requests/{request_id}/workflow")
        check(workflow["status"] == "active", "stato finale active")
        check(as_num(workflow["budget_target"]) == 218000, "budget finale corretto")
        check(workflow["finance_status"] == "mortgage_preapproved", "finanza finale corretta")
        check(any("L'appartamento" in (x.get("property_title") or "") for x in workflow["matches"]), "coerenza BUY-MATCH e apostrofo confermati")

    def verify_cleanup(self):
        print("\n[10/10] Pulizia selettiva")
        self.cleanup_current()
        with self.conn.cursor() as cur:
            checks = [
                ("contacts", "source=%s"),
                ("leads", "source=%s"),
                ("properties", "code LIKE %s"),
                ("buy_requests", "title LIKE %s"),
                ("tasks", "title LIKE %s"),
            ]
            for table, where in checks:
                value = self.run_id if "=%s" in where else self.run_id + "%"
                cur.execute(f"SELECT COUNT(*) count FROM {table} WHERE {where}", (value,))
                check(cur.fetchone()["count"] == 0, f"nessun residuo E2E in {table}")

    def run(self):
        self.connect()
        self.safety_guard()
        self.cleanup_stale()
        contact_id, lead_id = self.bootstrap()
        self.test_ui_and_health()
        request_id = self.create_buy_request(contact_id, lead_id)
        self.test_criteria(request_id)
        self.test_request_updates(request_id)
        match_id = self.calculate_match(request_id)
        self.test_interactions_and_match(request_id, match_id)
        self.test_tasks(request_id)
        self.test_dashboard_kpi(contact_id, lead_id, request_id)
        self.test_api_list_and_final_state(request_id)
        self.verify_cleanup()


def main():
    runner = Runner()
    print("=" * 72)
    print("STIMA360 — BUY 0.2.1 E2E TEST")
    print("Esecuzione esclusiva su backend-test e db-test")
    print("Run:", runner.run_id)
    print("=" * 72)
    try:
        runner.run()
    except Exception as exc:
        print("\nTEST FALLITO:", exc)
        traceback.print_exc()
        try:
            runner.cleanup_current()
            print("Pulizia dati creati dal test: completata")
        except Exception as cleanup_exc:
            print("ATTENZIONE: pulizia automatica fallita:", cleanup_exc)
        return 1
    finally:
        if runner.conn:
            runner.conn.close()
    print("\n" + "=" * 72)
    print("BUY 0.2.1 VALIDATO — TUTTI I TEST E2E SONO PASSATI")
    print("Produzione non toccata. Dati E2E rimossi.")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
