"""CESTINO-RICHIESTE-1 (FASE H) - Cestino e Ripristino delle RICHIESTE ACQUIRENTE,
su PostgreSQL VERO.

Schema completo dal runner (migration 092 compresa), rotte VERE (buy, match,
proposte, vendite, core, CRM) con il contesto operatore sostituito per
ruolo. Nessuna cancellazione fisica, nessuna cascata, nessuna chiusura: ogni
prova controlla che le righe collegate restino come sono.

  01  richiesta semplice (attiva, con un abbinamento): controllo con gli
      effetti, spostamento (motivo + nota, registro), stato invariato, fuori
      da elenco / ricerca / scheda del contatto / dashboard, scheda e flusso
      ancora leggibili, ogni scrittura rifiutata (servizio e database), il
      contatto e la sua altra richiesta intatti; ripristino sullo stesso id
      con i dati, l'altra richiesta aperta segnalata come possibile doppione;
  02  blocchi: proposta in corso, vendita pendente, visita futura, task
      aperto - ciascuno con il suo codice e il collegamento; nulla chiuso;
      l'abbinamento da solo NON blocca; la guardia nel database ferma anche
      una UPDATE diretta;
  03  storico, permessi e agenzie: un agent si ferma davanti allo storico
      (403 HISTORY_REQUIRES_ADMIN), il titolare no; l'agent ripristina e
      vede solo i suoi; un'altra agenzia: 404 e Cestino vuoto;
  04  esclusione operativa: abbinamenti (elenco, calcolo per immobile), FLOW
      R004/R005, «Oggi» (una NBA gia' calcolata sparisce), proposte e vendite
      nuove rifiutate; il ripristino non riapre nulla e non invia nulla;
  05  contatto nel Cestino: la richiesta nel Cestino non blocca il Cestino del
      contatto; il ripristino della richiesta si ferma con il collegamento al
      contatto; ripristinato il contatto, la richiesta torna;
  06  concorrenza: due spostamenti -> uno; un task nasce mentre lo spostamento
      aspetta (lo blocca); lo spostamento e' in corso mentre nasce un task
      (rifiutato) o una proposta scritta a mano (la guardia) - mai entrambi;
  99  codice su un database SENZA la 092: 503 leggibile, elenco e scrittura
      come prima; la down si ferma con richieste nel Cestino o eventi; up.
"""
from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.test_censimento_3_backend_postgres import (  # noqa: F401
    DSN, _in_attesa_di_lock, _in_parallelo, _q, completo,
)

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

MIGRAZIONI = Path(__file__).resolve().parents[1] / "migrations"
SU = MIGRAZIONI / "092_cestino_richieste_1_buy_request_trash.sql"
GIU = MIGRAZIONI / "092_cestino_richieste_1_buy_request_trash_down.sql"
IMMOBILE = 18          # fixture: agenzia 1, bozza senza incarico
ALTRO_IMMOBILE = 5     # fixture: agenzia 1


@pytest.fixture(scope="module")
def k(completo):
    import importlib

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from buy.router import router as acquirenti
    from core.router import router as core
    from crm.router import router as crm
    from match.router import router as abbinamenti
    from operator_auth.context import OperatorContext
    from operator_auth.dependencies import (legacy_basic_agency_context, require_authenticated_operator,
                                            require_operator)
    from proposal.router import router as proposte
    from sale.router import router as vendite

    c = completo
    mp = pytest.MonkeyPatch()
    for modulo in ("communication.database", "consent.database", "next_best_action.database",
                   "seller_intelligence.database"):
        mp.setattr(importlib.import_module(modulo), "get_connection",
                   lambda: importlib.import_module("core.database").get_connection())
    ids = dict(c["ids"])
    with c["conn"].cursor() as cur:
        def operatore(email, nome, agenzia, ruolo):
            cur.execute("INSERT INTO operator_users (email, email_normalized, password_hash, first_name) "
                        "VALUES (%s, %s, 'pbkdf2_sha256$1$x$y', %s) RETURNING id", (email, email, nome))
            uid = cur.fetchone()[0]
            cur.execute("INSERT INTO agency_memberships (agency_id, operator_user_id, role, status) "
                        "VALUES (%s, %s, %s, 'active')", (agenzia, uid, ruolo))
            return uid
        ids["agent2_a"] = operatore("b2.a@x.test", "Bruno", 1, "agent")
    ruoli = {"owner_a": (ids["owner_a"], 1, "agency_owner"), "agent_a": (ids["agent_a"], 1, "agent"),
             "agent2_a": (ids["agent2_a"], 1, "agent"), "owner_b": (ids["owner_b"], 2, "agency_owner")}
    stato = {"chi": "owner_a"}

    def contesto():
        uid, agenzia, ruolo = ruoli[stato["chi"]]
        return OperatorContext(user_id=uid, agency_id=agenzia, role=ruolo, is_platform_admin=False,
                               session_id=None, auth_channel="operator_session")

    app = FastAPI()
    for r in (core, crm, acquirenti, abbinamenti, proposte, vendite):
        app.include_router(r)
    for dipendenza in (legacy_basic_agency_context, require_operator, require_authenticated_operator):
        app.dependency_overrides[dipendenza] = contesto
    from operator_auth.dependencies import audit_actor
    app.dependency_overrides[audit_actor] = lambda: "test"
    client = TestClient(app)

    class _Come:
        def __init__(self, chi):
            self.chi = chi

        def __getattr__(self, metodo):
            def invia(*a, **kw):
                stato["chi"] = self.chi
                return getattr(client, metodo)(*a, **kw)
            return invia

    def ctx(chi="owner_a"):
        stato["chi"] = chi
        return contesto()

    yield {**c, "ids": ids, "api": lambda chi="owner_a": _Come(chi), "ctx": ctx}
    mp.undo()


# ---------------------------------------------------------------------------
# aiuti
# ---------------------------------------------------------------------------

def _contatto(k, chi="owner_a", **campi):
    r = k["api"](chi).post("/api/core/contacts", json={"display_name": f"Acquirente {uuid.uuid4().hex[:8]}", **campi})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _richiesta(k, cid, chi="owner_a", **campi):
    corpo = {"contact_id": cid, "title": f"Trilocale {uuid.uuid4().hex[:6]}", "status": "active",
             "budget_target": 200000, **campi}
    r = k["api"](chi).post("/api/buy/requests", json=corpo)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _abbinamento(k, rid, pid=IMMOBILE):
    return _q(k, "INSERT INTO matches (buy_request_id, property_id, compatibility_status, score_total, match_class, "
                 "algorithm_version) VALUES (%s, %s, 'compatible', 80, 'strong', 'test') RETURNING id",
              (rid, pid))[0][0]


def _proposta(k, mid, status="submitted"):
    return _q(k, "INSERT INTO property_proposals (match_id, amount, expires_at, idempotency_key, created_by, status) "
                 "VALUES (%s, 100000, NOW() + INTERVAL '30 days', %s, 'test', %s) RETURNING id",
              (mid, str(uuid.uuid4()), status))[0][0]


def _check(k, rid, chi="owner_a"):
    return k["api"](chi).get(f"/api/buy/requests/{rid}/deletion-check")


def _sposta(k, rid, chi="owner_a", reason="duplicate", note=None):
    corpo = {"reason_code": reason}
    if note is not None:
        corpo["note"] = note
    return k["api"](chi).post(f"/api/buy/requests/{rid}/trash", json=corpo)


def _ripristina(k, rid, chi="owner_a"):
    return k["api"](chi).post(f"/api/buy/requests/{rid}/restore")


def _codice(r, atteso):
    assert r.status_code == atteso, r.text
    return r.json()


def _codici(r):
    return [b["code"] for b in r.json()["blockers"]]


def _elenco(k, chi="owner_a", **params):
    r = k["api"](chi).get("/api/buy/requests", params={"limit": 200, **params})
    assert r.status_code == 200, r.text
    return {x["id"] for x in r.json()["items"]}


def _cestino(k, chi="owner_a"):
    r = k["api"](chi).get("/api/buy/trash/requests", params={"limit": 200})
    assert r.status_code == 200, r.text
    return {x["id"]: x for x in r.json()["items"]}


def _nel_cestino(k, rid):
    return _q(k, "SELECT deleted_at IS NOT NULL FROM buy_requests WHERE id = %s", (rid,))[0][0]


def _impronta(k, rid):
    return _q(k, "SELECT row_to_json(x)::jsonb - 'updated_at' - 'deleted_at' - 'deleted_by_user_id' - 'deleted_reason' "
                 "FROM buy_requests x WHERE id = %s", (rid,))[0][0]


def _errore(k, sql, params=None):
    psycopg2 = k["psycopg2"]
    try:
        _q(k, sql, params)
    except psycopg2.Error as exc:
        with k["conn"].cursor() as cur:
            cur.execute("ROLLBACK")
        return str(exc).splitlines()[0]
    return None


def _visita(k, rid, mid, pid=IMMOBILE, giorni=3, status="scheduled"):
    """Una visita registrata come la registra «Visita programmata» (proiezione
    + interazione `visit_scheduled`), senza passare dall'Agenda."""
    quando = datetime.now(timezone.utc) + timedelta(days=giorni)
    vid = _q(k, "INSERT INTO property_visits (property_id, scheduled_at, status) VALUES (%s, %s, %s) RETURNING id",
             (pid, quando, status))[0][0]
    _q(k, "INSERT INTO buy_request_interactions (buy_request_id, match_id, property_id, property_visit_id, "
          "interaction_type) VALUES (%s, %s, %s, %s, 'visit_scheduled')", (rid, mid, pid, vid))
    return vid


# ---------------------------------------------------------------------------

def test_01_richiesta_semplice_sposta_legge_ripristina(k):
    api = k["api"]()
    cid = _contatto(k)
    rid = _richiesta(k, cid, title="Trilocale al mare")
    altra = _richiesta(k, cid, title="Bilocale in collina")
    mid = _abbinamento(k, rid)
    prima = _impronta(k, rid)
    assert rid in _elenco(k) and rid in _elenco(k, search="Trilocale al mare")
    r = _codice(_check(k, rid), 200)
    assert r["can_trash"] is True and r["blockers"] == [] and r["history"] == []
    assert r["effects"] == {"status": "active", "status_label": "attiva", "matches": 1, "other_open_requests": 1,
                            "contact_id": cid, "contact_name": r["effects"]["contact_name"],
                            "communications_unchanged": True}
    assert _codice(_sposta(k, rid, reason="boh"), 400)["code"] == "INVALID_TRASH_REASON"
    assert _sposta(k, rid, note="x" * 501).status_code == 422
    riga = _codice(_sposta(k, rid, reason="created_by_mistake", note="  Inserita due volte  "), 200)
    assert riga["id"] == rid and riga["status"] == "active" and riga["deleted_reason"] == "created_by_mistake"
    ev = _q(k, "SELECT action, reason_code, note, actor_user_id, before_state->>'status' FROM record_lifecycle_events "
               "WHERE entity_type = 'buy_request' AND entity_id = %s ORDER BY id", (rid,))
    assert ev == [["trash", "created_by_mistake", "Inserita due volte", k["ids"]["owner_a"], "active"]]
    # fuori da elenco, ricerca, filtro per contatto, scheda del contatto, dashboard
    assert rid not in _elenco(k) and rid not in _elenco(k, search="Trilocale al mare")
    assert rid not in _elenco(k, contact_id=cid) and altra in _elenco(k, contact_id=cid)
    assert rid not in _elenco(k, status="active")
    r360 = _codice(api.get(f"/api/crm/contacts/{cid}/360"), 200)
    assert [b["id"] for b in r360["buy_requests"]] == [altra] and r360["matches"] == []
    assert rid not in [x["id"] for x in _codice(api.get("/api/buy/dashboard"), 200)["recent"]]
    # scheda, flusso, abbinamenti e task in sola lettura
    scheda = _codice(api.get(f"/api/buy/requests/{rid}"), 200)
    assert scheda["trash"]["deleted_reason"] == "created_by_mistake" and scheda["trash"]["deleted_by_name"] == "Olga"
    assert scheda["trash"]["deleted_note"] == "Inserita due volte" and scheda["trash"]["can_restore"] is True
    assert scheda["trash"]["contact_in_trash"] is False
    flusso = _codice(api.get(f"/api/buy/requests/{rid}/workflow"), 200)
    assert [m["id"] for m in flusso["matches"]] == [mid] and flusso["trash"]["deleted_reason"] == "created_by_mistake"
    # ogni scrittura: 409 BUY_REQUEST_IN_TRASH
    for r in (api.patch(f"/api/buy/requests/{rid}", json={"notes": "x"}),
              api.patch(f"/api/buy/requests/{rid}", json={"status": "closed"}),
              api.delete(f"/api/buy/requests/{rid}"),
              api.post(f"/api/buy/requests/{rid}/locations", json={"location_type": "municipality",
                                                                   "municipality": "Tortoreto"}),
              api.post(f"/api/buy/requests/{rid}/typologies", json={"property_type": "apartment"}),
              api.post(f"/api/buy/requests/{rid}/interactions", json={"match_id": mid, "interaction_type": "interested"}),
              api.post(f"/api/buy/requests/{rid}/matches/{mid}/decision", json={"action": "interested"}),
              api.post(f"/api/buy/requests/{rid}/tasks", json={"title": "Richiamare"}),
              api.post(f"/api/buy/requests/{rid}/history/notes", json={"description": "nota"}),
              api.post(f"/api/match/buy-requests/{rid}/calculate", json={}),
              api.post("/api/match/calculate", json={"buy_request_id": rid, "property_id": ALTRO_IMMOBILE})):
        assert r.status_code == 409 and r.json()["code"] == "BUY_REQUEST_IN_TRASH", r.text
    assert _codice(_sposta(k, rid), 409)["code"] == "ALREADY_DELETED"
    # nel database: righe nuove e modifiche rifiutate
    assert "BUY_REQUEST_IN_TRASH" in _errore(k, "INSERT INTO buy_request_locations (buy_request_id, location_type, "
                                                "municipality) VALUES (%s, 'municipality', 'Giulianova')", (rid,))
    assert "BUY_REQUEST_IN_TRASH" in _errore(k, "INSERT INTO buy_request_history (buy_request_id, event_type) "
                                                "VALUES (%s, 'note')", (rid,))
    assert "BUY_REQUEST_IN_TRASH" in _errore(k, "UPDATE buy_requests SET title = 'x' WHERE id = %s", (rid,))
    # il contatto e la sua altra richiesta funzionano come prima
    assert _q(k, "SELECT deleted_at FROM contacts WHERE id = %s", (cid,))[0][0] is None
    assert api.patch(f"/api/buy/requests/{altra}", json={"notes": "sempre operativa"}).status_code == 200
    assert _codice(api.get(f"/api/core/contacts/{cid}/deletion-check"), 200)["blockers"][0]["items"][0]["id"] == altra
    # Cestino: chi, quando, perche'
    voce = _cestino(k)[rid]
    assert (voce["title"], voce["contact_id"], voce["deleted_by_name"], voce["deleted_note"]) == (
        "Trilocale al mare", cid, "Olga", "Inserita due volte")
    # ripristino: stesso id, stesso stato, l'altra richiesta aperta segnalata, nulla unito
    r = _codice(_ripristina(k, rid), 200)
    assert r["id"] == rid and r["deleted_at"] is None and r["status"] == "active"
    assert [d["id"] for d in r["possible_duplicates"]] == [altra]
    assert _impronta(k, rid) == prima
    assert rid in _elenco(k) and rid not in _cestino(k)
    assert [x[0] for x in _q(k, "SELECT action FROM record_lifecycle_events WHERE entity_type = 'buy_request' "
                               "AND entity_id = %s ORDER BY id", (rid,))] == ["trash", "restore"]
    assert _q(k, "SELECT count(*) FROM matches WHERE id = %s AND archived_at IS NULL", (mid,))[0][0] == 1
    assert _codice(_ripristina(k, rid), 409)["code"] == "NOT_DELETED"
    assert api.patch(f"/api/buy/requests/{rid}", json={"notes": "di nuovo operativa"}).status_code == 200


def test_02_blocchi_con_collegamenti_nulla_si_chiude(k):
    api = k["api"]()

    def bloccata(rid, codice):
        r = _check(k, rid)
        assert r.status_code == 200 and r.json()["can_trash"] is False, r.text
        [b] = r.json()["blockers"]
        assert b["code"] == codice
        s = _codice(_sposta(k, rid), 409)
        assert s["code"] == "TRASH_BLOCKED" and [x["code"] for x in s["blockers"]] == [codice]
        assert not _nel_cestino(k, rid)
        return b

    cid = _contatto(k)
    # proposta inviata
    rid = _richiesta(k, cid)
    pp = _proposta(k, _abbinamento(k, rid))
    b = bloccata(rid, "PROPOSAL_OPEN")
    assert b["items"][0]["id"] == pp and b["items"][0]["href"] == f"#/acquirenti/{rid}"
    assert _q(k, "SELECT status FROM property_proposals WHERE id = %s", (pp,))[0][0] == "submitted"
    # vendita pendente (proposta accettata)
    rid2 = _richiesta(k, cid)
    mid2 = _abbinamento(k, rid2)
    pp2 = _proposta(k, mid2, "accepted")
    sale = _q(k, "INSERT INTO property_sales (property_id, buy_request_id, proposal_id, sale_price, idempotency_key, "
                 "created_by, status) VALUES (%s, %s, %s, 100000, %s, 'test', 'pending') RETURNING id",
              (IMMOBILE, rid2, pp2, str(uuid.uuid4())))[0][0]
    b = bloccata(rid2, "SALE_PENDING")
    assert b["items"][0]["id"] == sale and b["items"][0]["href"] == f"#/immobili/{IMMOBILE}"
    # visita futura in programma
    rid3 = _richiesta(k, cid)
    vid = _visita(k, rid3, _abbinamento(k, rid3))
    b = bloccata(rid3, "VISIT_SCHEDULED")
    assert b["items"][0]["id"] == vid and b["items"][0]["href"].startswith("#/agenda/giorno/")
    # task aperto collegato
    rid4 = _richiesta(k, cid)
    task = _codice(api.post(f"/api/buy/requests/{rid4}/tasks", json={"title": "Richiamare l'acquirente"}), 201)
    b = bloccata(rid4, "TASK_OPEN")
    assert b["items"][0]["id"] == task["id"] and b["link"]["href"] == "#/attivita"
    assert _q(k, "SELECT status FROM tasks WHERE id = %s", (task["id"],))[0][0] == "open"
    # la guardia nel database ferma anche una UPDATE diretta
    for x in (rid, rid2, rid3, rid4):
        assert "BUY_REQUEST_HAS_OPEN_PROCESSES" in _errore(
            k, "UPDATE buy_requests SET deleted_at = NOW(), deleted_reason = 'other' WHERE id = %s", (x,))
    # chiuso dal suo posto, il blocco cade (task completato -> storico: il titolare puo')
    _q(k, "UPDATE tasks SET status = 'completed', completed_at = NOW() WHERE id = %s", (task["id"],))
    r = _codice(_check(k, rid4), 200)
    assert r["can_trash"] is True and [h["code"] for h in r["history"]] == ["TASK_HISTORY"]
    # gli abbinamenti (anche forti, anche numerosi) da soli NON bloccano
    rid5 = _richiesta(k, cid)
    _abbinamento(k, rid5, IMMOBILE)
    _abbinamento(k, rid5, ALTRO_IMMOBILE)
    r = _codice(_check(k, rid5), 200)
    assert r["can_trash"] is True and r["effects"]["matches"] == 2
    assert _sposta(k, rid5).status_code == 200
    assert _q(k, "SELECT count(*) FROM matches WHERE buy_request_id = %s", (rid5,))[0][0] == 2   # nessuna cascata


def test_03_storico_permessi_e_agenzie(k):
    cid = _contatto(k)
    # storico reale: un'interazione registrata
    rid = _richiesta(k, cid)
    mid = _abbinamento(k, rid)
    assert k["api"]().post(f"/api/buy/requests/{rid}/interactions",
                           json={"match_id": mid, "interaction_type": "interested"}).status_code == 201
    r = _codice(_check(k, rid, "agent_a"), 200)
    assert r["can_trash"] is False and _codici(_check(k, rid, "agent_a")) == ["HISTORY_REQUIRES_ADMIN"]
    assert [h["code"] for h in r["history"]] == ["BUYER_INTERACTION_HISTORY"]
    assert _codice(_sposta(k, rid, "agent_a"), 403)["code"] == "HISTORY_REQUIRES_ADMIN"
    # una richiesta conclusa e' storico
    chiusa = _richiesta(k, cid, status="closed")
    assert [h["code"] for h in _codice(_check(k, chiusa, "agent_a"), 200)["history"]] == ["REQUEST_CONCLUDED"]
    # il titolare procede; lo storico resta
    r = _codice(_check(k, rid), 200)
    assert r["can_trash"] is True and r["history"][0]["code"] == "BUYER_INTERACTION_HISTORY"
    assert _sposta(k, rid).status_code == 200
    assert _q(k, "SELECT count(*) FROM buy_request_interactions WHERE buy_request_id = %s", (rid,))[0][0] == 1
    # un agent sposta una richiesta senza storico (nessuno scope per agente sulle richieste)
    sua = _richiesta(k, cid, "agent_a")
    del_collega = _richiesta(k, cid, "agent2_a")
    assert _codice(_sposta(k, sua, "agent_a"), 200)["deleted_by_user_id"] == k["ids"]["agent_a"]
    assert _sposta(k, del_collega, "agent2_a").status_code == 200
    # elenco: l'agent i suoi, il titolare tutti
    assert set(_cestino(k, "agent_a")) == {sua}
    assert {rid, sua, del_collega} <= set(_cestino(k))
    # ripristino: l'agent solo i suoi; la scheda lo dice
    assert _codice(_ripristina(k, del_collega, "agent_a"), 403)["code"] == "NOT_DELETED_BY_YOU"
    assert k["api"]("agent_a").get(f"/api/buy/requests/{del_collega}").json()["trash"]["can_restore"] is False
    assert _ripristina(k, sua, "agent_a").status_code == 200
    assert _ripristina(k, del_collega).status_code == 200
    # altra agenzia: 404 indistinguibile, Cestino separato
    assert _check(k, rid, "owner_b").status_code == 404
    assert _sposta(k, sua, "owner_b").status_code == 404 and _ripristina(k, rid, "owner_b").status_code == 404
    assert k["api"]("owner_b").get(f"/api/buy/requests/{rid}").status_code == 404
    assert _cestino(k, "owner_b") == {}


def test_04_esclusione_operativa_e_ripristino_senza_effetti(k):
    from flow import adapters as flow_adapters
    from next_best_action import repository as nba

    api = k["api"]()
    cid = _contatto(k)
    rid = _richiesta(k, cid, next_action_at=(datetime.now(timezone.utc) - timedelta(days=3)).isoformat())
    mid = _abbinamento(k, rid)
    pp = _proposta(k, mid, "accepted")              # conclusa: si puo' spostare (titolare)
    _q(k, "UPDATE matches SET freshness_status = 'fresh', commercial_status = 'new', score_total = 95 WHERE id = %s",
       (mid,))
    _q(k, "INSERT INTO next_best_actions (agency_id, subject_type, subject_id, contact_id, action_type, priority, "
          "reason, source_signal, cta_route, cta_params, generated_at) VALUES (1, 'buy_request', %s, %s, "
          "'contact_overdue_next_action', 'high', 'x', 'next_action_overdue', 'acquirenti', '[]'::jsonb, NOW())",
       (rid, cid))
    assert ("buy_request", rid) in flow_adapters.scan_candidates_for_agency(1, "FLOW-R004", {"overdue_hours": 1}, 500)
    assert ("match", mid) in flow_adapters.scan_candidates_for_agency(1, "FLOW-R005", {"minimum_score": 50}, 500)
    assert rid in [x["subject_id"] for x in nba.list_current_scoped(k["ctx"](), 500)]
    assert mid in {m["id"] for m in api.get("/api/match/matches", params={"limit": 500}).json()["items"]}
    assert _sposta(k, rid).status_code == 200
    # abbinamenti, FLOW, «Oggi»
    assert mid not in {m["id"] for m in api.get("/api/match/matches", params={"limit": 500}).json()["items"]}
    assert api.get(f"/api/match/matches/{mid}").status_code == 404
    assert ("buy_request", rid) not in flow_adapters.scan_candidates_for_agency(1, "FLOW-R004", {"overdue_hours": 1}, 500)
    assert ("match", mid) not in flow_adapters.scan_candidates_for_agency(1, "FLOW-R005", {"minimum_score": 50}, 500)
    assert rid not in [x["subject_id"] for x in nba.list_current_scoped(k["ctx"](), 500)]
    # proposte e vendite nuove: rifiutate (servizio e database)
    r = api.post("/api/proposals", json={"match_id": mid, "amount": 150000,
                                         "expires_at": (datetime.now(timezone.utc) + timedelta(days=9)).isoformat(),
                                         "idempotency_key": str(uuid.uuid4())})
    assert r.status_code == 409 and r.json()["code"] == "BUY_REQUEST_IN_TRASH", r.text
    r = api.post("/api/sales", json={"proposal_id": pp, "idempotency_key": str(uuid.uuid4())})
    assert r.status_code == 409 and r.json()["code"] == "BUY_REQUEST_IN_TRASH", r.text
    assert "BUY_REQUEST_IN_TRASH" in _errore(
        k, "INSERT INTO property_proposals (match_id, amount, expires_at, idempotency_key, created_by, status) "
           "VALUES (%s, 1, NOW() + INTERVAL '3 days', %s, 'x', 'draft')", (mid, str(uuid.uuid4())))
    assert "BUY_REQUEST_IN_TRASH" in _errore(
        k, "INSERT INTO matches (buy_request_id, property_id, compatibility_status, score_total, match_class, "
           "algorithm_version) VALUES (%s, %s, 'compatible', 1, 'weak', 'x')", (rid, ALTRO_IMMOBILE))
    # il ripristino non invia, non riprogramma, non riapre: righe identiche
    prima = _q(k, "SELECT (SELECT count(*) FROM tasks WHERE contact_id = %s), "
                  "(SELECT count(*) FROM appointments WHERE contact_id = %s), "
                  "(SELECT count(*) FROM communication_messages WHERE contact_id = %s), "
                  "(SELECT status FROM property_proposals WHERE id = %s), "
                  "(SELECT status FROM buy_requests WHERE id = %s)", (cid, cid, cid, pp, rid))
    assert _ripristina(k, rid).status_code == 200
    dopo = _q(k, "SELECT (SELECT count(*) FROM tasks WHERE contact_id = %s), "
                 "(SELECT count(*) FROM appointments WHERE contact_id = %s), "
                 "(SELECT count(*) FROM communication_messages WHERE contact_id = %s), "
                 "(SELECT status FROM property_proposals WHERE id = %s), "
                 "(SELECT status FROM buy_requests WHERE id = %s)", (cid, cid, cid, pp, rid))
    assert dopo == prima
    # di nuovo nelle superfici operative
    assert ("buy_request", rid) in flow_adapters.scan_candidates_for_agency(1, "FLOW-R004", {"overdue_hours": 1}, 500)
    assert mid in {m["id"] for m in api.get("/api/match/matches", params={"limit": 500}).json()["items"]}


def test_05_contatto_nel_cestino(k):
    cid = _contatto(k)
    rid = _richiesta(k, cid)                   # attiva
    assert _sposta(k, rid).status_code == 200
    # una richiesta nel Cestino non e' un processo aperto del contatto
    r = _codice(k["api"]().get(f"/api/core/contacts/{cid}/deletion-check"), 200)
    assert "BUY_REQUEST_OPEN" not in [b["code"] for b in r["blockers"]], r
    assert k["api"]().post(f"/api/core/contacts/{cid}/trash", json={"reason_code": "duplicate"}).status_code == 200
    assert _codice(k["api"]().get(f"/api/buy/requests/{rid}"), 200)["trash"]["contact_in_trash"] is True
    voce = _cestino(k)[rid]
    assert voce["contact_in_trash"] is True
    # ripristino della richiesta: prima il contatto, con il collegamento
    r = _codice(_ripristina(k, rid), 409)
    assert r["code"] == "RESTORE_BLOCKED"
    [b] = r["blockers"]
    assert b["code"] == "CONTACT_IN_TRASH" and b["items"][0]["href"] == f"#/contatti/{cid}"
    assert b["link"]["href"] == "#/cestino/contatti"
    assert _nel_cestino(k, rid)
    assert k["api"]().post(f"/api/core/contacts/{cid}/restore").status_code == 200
    r = _codice(_ripristina(k, rid), 200)
    assert r["status"] == "active" and rid in _elenco(k)


def test_06_concorrenza(k):
    from core import database as core_database
    cid = _contatto(k)
    # due spostamenti della stessa richiesta: uno solo
    rid = _richiesta(k, cid)
    esiti = _in_parallelo([lambda: _sposta(k, rid), lambda: _sposta(k, rid)])
    assert sorted(r.status_code for r in esiti) == [200, 409]
    assert [r.json()["code"] for r in esiti if r.status_code == 409] == ["ALREADY_DELETED"]

    # un task nasce mentre lo spostamento aspetta: lo spostamento lo vede e si ferma
    rid = _richiesta(k, cid)
    altra = core_database.get_connection()
    try:
        with altra.cursor() as cur:
            cur.execute("INSERT INTO tasks (agency_id, contact_id, title, status, priority) "
                        "VALUES (1, %s, 'in corsa', 'open', 'normal') RETURNING id", (cid,))
            tid = cur.fetchone()[0]
            cur.execute("INSERT INTO buy_request_task_links (buy_request_id, task_id) VALUES (%s, %s)", (rid, tid))
        esito = {}
        filo = threading.Thread(target=lambda: esito.update(r=_sposta(k, rid)))
        filo.start()
        assert _in_attesa_di_lock(k, "%FOR UPDATE OF b%")
        altra.commit()
        filo.join(30)
    finally:
        altra.close()
    assert esito["r"].status_code == 409 and esito["r"].json()["code"] == "TRASH_BLOCKED"
    assert not _nel_cestino(k, rid)

    # lo spostamento e' in corso mentre nasce un task: il task aspetta ed e' rifiutato
    rid = _richiesta(k, cid)
    altra = core_database.get_connection()
    try:
        with altra.cursor() as cur:
            cur.execute("SELECT id FROM buy_requests WHERE id = %s FOR UPDATE", (rid,))
            cur.execute("UPDATE buy_requests SET deleted_at = NOW(), deleted_reason = 'duplicate' WHERE id = %s", (rid,))
        esito = {}
        filo = threading.Thread(target=lambda: esito.update(
            r=k["api"]().post(f"/api/buy/requests/{rid}/tasks", json={"title": "in ritardo"})))
        filo.start()
        assert _in_attesa_di_lock(k, "%FROM buy_requests WHERE id=% FOR SHARE%")
        altra.commit()
        filo.join(30)
    finally:
        altra.close()
    assert esito["r"].status_code == 409 and esito["r"].json()["code"] == "BUY_REQUEST_IN_TRASH"
    assert _q(k, "SELECT count(*) FROM buy_request_task_links WHERE buy_request_id = %s", (rid,))[0][0] == 0
    assert _q(k, "SELECT count(*) FROM tasks WHERE title = 'in ritardo'")[0][0] == 0      # niente a meta'

    # la stessa corsa scritta a mano nel database (una proposta): la guardia della 092
    rid = _richiesta(k, cid)
    mid = _abbinamento(k, rid)
    altra = core_database.get_connection()
    try:
        with altra.cursor() as cur:
            cur.execute("UPDATE buy_requests SET deleted_at = NOW(), deleted_reason = 'other' WHERE id = %s", (rid,))
        esito = {}

        def inserisci():
            terza = core_database.get_connection()        # connessione PROPRIA
            try:
                with terza.cursor() as cur:
                    cur.execute("INSERT INTO property_proposals (match_id, amount, expires_at, idempotency_key, "
                                "created_by, status) VALUES (%s, 1, NOW() + INTERVAL '3 days', %s, 'diretta', 'draft')",
                                (mid, str(uuid.uuid4())))
                terza.commit()
                esito["e"] = None
            except k["psycopg2"].Error as exc:
                esito["e"] = str(exc).splitlines()[0]
            finally:
                terza.close()
        filo = threading.Thread(target=inserisci)
        filo.start()
        assert _in_attesa_di_lock(k, "%INSERT INTO property_proposals%diretta%")
        altra.commit()
        filo.join(30)
    finally:
        altra.close()
    assert "BUY_REQUEST_IN_TRASH" in esito["e"]
    assert _q(k, "SELECT count(*) FROM property_proposals WHERE match_id = %s", (mid,))[0][0] == 0


def test_99_senza_la_092_e_la_down(k):
    api = k["api"]()
    cid = _contatto(k)
    rid = _richiesta(k, cid)
    assert _sposta(k, rid).status_code == 200
    giu = GIU.read_text(encoding="utf-8")
    assert "richieste acquirente nel Cestino" in _errore(k, giu)
    assert _nel_cestino(k, rid)
    # (database usa-e-getta: si ripristinano anche le richieste lasciate dalle prove precedenti)
    _q(k, "UPDATE buy_requests SET deleted_at = NULL, deleted_by_user_id = NULL, deleted_reason = NULL "
          "WHERE deleted_at IS NOT NULL")
    assert "eventi di richieste acquirente" in _errore(k, giu)
    trigger = _q(k, "SELECT tgname FROM pg_trigger WHERE tgrelid = 'record_lifecycle_events'::regclass "
                    "AND NOT tgisinternal")
    for (t,) in trigger:
        _q(k, f"ALTER TABLE record_lifecycle_events DISABLE TRIGGER {t}")
    _q(k, "DELETE FROM record_lifecycle_events WHERE entity_type = 'buy_request'")
    for (t,) in trigger:
        _q(k, f"ALTER TABLE record_lifecycle_events ENABLE TRIGGER {t}")
    assert _errore(k, giu) is None
    assert _q(k, "SELECT count(*) FROM information_schema.columns WHERE table_name = 'buy_requests' "
                 "AND column_name LIKE 'deleted%%'")[0][0] == 0
    assert _q(k, "SELECT count(*) FROM pg_trigger WHERE tgname LIKE 'trg_%%_buy_trash_guard'")[0][0] == 0
    # codice nuovo, database senza la 092: elenco, scheda e scritture come prima; Cestino 503 leggibile
    assert rid in _elenco(k)
    scheda = _codice(api.get(f"/api/buy/requests/{rid}"), 200)
    assert scheda["trash"] is None
    assert api.patch(f"/api/buy/requests/{rid}", json={"notes": "senza 092"}).status_code == 200
    assert api.post(f"/api/buy/requests/{rid}/tasks", json={"title": "senza 092"}).status_code == 201
    r = _check(k, rid)
    assert r.status_code == 503 and r.json()["code"] == "TRASH_NOT_INSTALLED"
    assert api.get("/api/buy/trash/requests").json()["code"] == "TRASH_NOT_INSTALLED"
    assert _codice(k["api"]().get(f"/api/core/contacts/{cid}/deletion-check"), 200)["blockers"]
    # up di nuovo (la sonda finale passa)
    with k["conn"].cursor() as cur:
        cur.execute("BEGIN")
        cur.execute(SU.read_text(encoding="utf-8"))
        cur.execute("COMMIT")
    assert _q(k, "SELECT count(*) FROM pg_trigger WHERE tgname LIKE 'trg_%%_buy_trash_guard'")[0][0] == 12
    assert _codici(_check(k, rid)) == ["TASK_OPEN"]
