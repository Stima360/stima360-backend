"""CESTINO-CONTATTI-1 (FASE F) - Cestino e Ripristino dei CONTATTI, su PostgreSQL VERO.

Schema completo dal runner (migration 090 compresa), rotte VERE (core, CRM,
buy, match, immobili, acquisizioni, comunicazioni, amministrazione OWNER) con
il contesto operatore sostituito per ruolo. Nessuna cancellazione fisica,
nessuna cascata: ogni prova controlla che le righe collegate restino.

  01  contatto semplice: controllo, spostamento (motivo + nota, registro
      append-only), scheda ancora leggibile, ripristino con lo stesso id;
  02  blocchi: lead aperta, richiesta d'acquisto, appuntamento futuro, task
      aperto, proprietario di immobile con incarico, acquisizione aperta,
      vendita in corso - ciascuno con il suo codice e il collegamento; nulla
      viene chiuso o scollegato; ricontrollo alla conferma (409 TRASH_BLOCKED);
  03  storico: un agent si ferma (403 HISTORY_REQUIRES_ADMIN), il titolare no;
      lo storico resta, e la scheda lo mostra;
  04  permessi e agenzie: agent solo sui contatti assegnati, ripristino solo
      dei suoi; un'altra agenzia non vede nulla (404, Cestino vuoto);
  05  congelamento: modifica, ruoli, assegnazione, lead, attivita',
      collegamenti nuovi (immobile, richiesta, appuntamento) -> 409
      CONTACT_IN_TRASH; un processo chiuso non si riapre;
  06  invisibilita' operativa: elenco/ricerca contatti, richieste, abbinamenti,
      Venditori, contatti dell'immobile, selettore OWNER, prenotazione pubblica;
      la scheda del contatto conserva lo storico;
  07  comunicazioni: automazioni sospese e messaggi in coda annullati nella
      stessa operazione; il ripristino NON li riattiva; nessun messaggio nuovo,
      nessuna ripresa delle automazioni mentre e' nel Cestino;
  08  portale proprietario: accesso attivo -> blocco con «Disattiva accesso»
      (solo per il titolare), la rotta esistente, poi il Cestino; il
      ripristino non lo riattiva e mentre e' nel Cestino non si riattiva;
  09  ripristino con possibili doppioni: segnalati, nulla unito o modificato;
  10  concorrenza: due spostamenti -> uno solo; spostamento contro un lead
      nuovo, nei due ordini -> mai entrambi;
  99  codice su un database SENZA la 090: 503 leggibile, elenco invariato; la
      down si ferma con contatti nel Cestino; up di nuovo.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.test_censimento_3_backend_postgres import (  # noqa: F401
    DSN, _in_attesa_di_lock, _in_parallelo, _q, completo,
)

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

MIGRAZIONI = Path(__file__).resolve().parents[1] / "migrations"
SU = MIGRAZIONI / "090_cestino_contatti_1_contact_trash.sql"
GIU = MIGRAZIONI / "090_cestino_contatti_1_contact_trash_down.sql"
CON_INCARICO = 5           # fixture: 1..12 con incarico storico
SENZA_INCARICO = 18        # fixture: bozza, nessun incarico


def _futuro(giorni=3, ora=10):
    base = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) + timedelta(days=giorni)
    return base.replace(hour=ora)


@pytest.fixture(scope="module")
def k(completo):
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient

    from acquisitions.router import router as acquisizioni
    from buy.router import router as acquirenti
    from communication.router import router as comunicazioni
    from core.router import router as core
    from crm.router import router as crm
    from match.router import router as abbinamenti
    from operator_auth.context import OperatorContext
    from operator_auth.dependencies import (legacy_basic_agency_context, require_authenticated_operator,
                                            require_operator, require_owner_admin_context)
    from owner.router_admin import router as owner_admin
    from property.router import router as immobili

    c = completo
    # i moduli che leggono con la connessione legacy (comunicazioni, consenso,
    # azioni consigliate, timeline) puntano anch'essi al database usa-e-getta
    import importlib
    mp = pytest.MonkeyPatch()
    for modulo in ("communication.database", "consent.database", "next_best_action.database",
                   "seller_intelligence.database"):
        # la connessione del modulo CORE, gia' puntata dalla fixture al database usa-e-getta
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
        ids["admin_a"] = operatore("ad.a@x.test", "Ada", 1, "agency_admin")
        ids["agent2_a"] = operatore("b.a@x.test", "Bruno", 1, "agent")
    ruoli = {"owner_a": (ids["owner_a"], 1, "agency_owner"), "admin_a": (ids["admin_a"], 1, "agency_admin"),
             "agent_a": (ids["agent_a"], 1, "agent"), "agent2_a": (ids["agent2_a"], 1, "agent"),
             "owner_b": (ids["owner_b"], 2, "agency_owner")}
    stato = {"chi": "owner_a"}

    def contesto():
        uid, agenzia, ruolo = ruoli[stato["chi"]]
        return OperatorContext(user_id=uid, agency_id=agenzia, role=ruolo, is_platform_admin=False,
                               session_id=None, auth_channel="operator_session")

    def contesto_owner_admin():
        ctx = contesto()
        if ctx.role != "agency_owner":
            raise HTTPException(status_code=403, detail="solo il titolare")
        return ctx

    app = FastAPI()
    for r in (core, crm, acquirenti, abbinamenti, immobili, acquisizioni, comunicazioni, owner_admin):
        app.include_router(r)
    for dipendenza in (legacy_basic_agency_context, require_operator, require_authenticated_operator):
        app.dependency_overrides[dipendenza] = contesto
    app.dependency_overrides[require_owner_admin_context] = contesto_owner_admin
    client = TestClient(app)

    class _Come:
        def __init__(self, chi):
            self.chi = chi

        def __getattr__(self, metodo):
            def invia(*a, **kw):
                stato["chi"] = self.chi
                return getattr(client, metodo)(*a, **kw)
            return invia

    yield {**c, "ids": ids, "api": lambda chi="owner_a": _Come(chi), "stato": stato, "ctx": contesto}
    mp.undo()


# ---------------------------------------------------------------------------
# aiuti
# ---------------------------------------------------------------------------

def _contatto(k, chi="owner_a", **campi):
    corpo = {"display_name": f"Prova {uuid.uuid4().hex[:8]}", **campi}
    r = k["api"](chi).post("/api/core/contacts", json=corpo)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _check(k, cid, chi="owner_a"):
    return k["api"](chi).get(f"/api/core/contacts/{cid}/deletion-check")


def _sposta(k, cid, chi="owner_a", reason="duplicate", note=None):
    corpo = {"reason_code": reason}
    if note is not None:
        corpo["note"] = note
    return k["api"](chi).post(f"/api/core/contacts/{cid}/trash", json=corpo)


def _ripristina(k, cid, chi="owner_a"):
    return k["api"](chi).post(f"/api/core/contacts/{cid}/restore")


def _codici(risposta):
    return [b["code"] for b in risposta.json()["blockers"]]


def _nel_cestino(k, cid):
    return _q(k, "SELECT deleted_at IS NOT NULL FROM contacts WHERE id = %s", (cid,))[0][0]


def _elenco(k, chi="owner_a", **params):
    r = k["api"](chi).get("/api/core/contacts", params={"limit": 200, **params})
    assert r.status_code == 200, r.text
    return {x["id"] for x in r.json()["items"]}


def _cestino(k, chi="owner_a"):
    r = k["api"](chi).get("/api/core/trash/contacts", params={"limit": 200})
    assert r.status_code == 200, r.text
    return {x["id"]: x for x in r.json()["items"]}


def _lead(k, cid, status="open", pipeline="general", lost_reason=None):
    return _q(k, "INSERT INTO leads (agency_id, contact_id, source, pipeline, stage, priority, status, lost_reason) "
                 "VALUES (1, %s, 'manual', %s, 'new', 'normal', %s, %s) RETURNING id",
              (cid, pipeline, status, lost_reason))[0][0]


def _richiesta(k, cid, status="active"):
    return _q(k, "INSERT INTO buy_requests (agency_id, contact_id, title, status) VALUES (1, %s, 'Trilocale', %s) "
                 "RETURNING id", (cid, status))[0][0]


def _catena(k, cid, pid, *, proposta="submitted", vendita=None):
    """richiesta -> abbinamento -> proposta (-> vendita)."""
    br = _richiesta(k, cid)
    mt = _q(k, "INSERT INTO matches (buy_request_id, property_id, compatibility_status, score_total, match_class, "
               "algorithm_version) VALUES (%s, %s, 'compatible', 80, 'good', 'test') RETURNING id", (br, pid))[0][0]
    pp = _q(k, "INSERT INTO property_proposals (match_id, amount, expires_at, idempotency_key, created_by, status) "
               "VALUES (%s, 100000, NOW() + INTERVAL '30 days', %s, 'test', %s) RETURNING id",
            (mt, str(uuid.uuid4()), proposta))[0][0]
    sale = None
    if vendita:
        sale = _q(k, "INSERT INTO property_sales (property_id, buy_request_id, proposal_id, sale_price, idempotency_key, "
                     "created_by, status) VALUES (%s, %s, %s, 100000, %s, 'test', %s) RETURNING id",
                  (pid, br, pp, str(uuid.uuid4()), vendita))[0][0]
    return {"br": br, "match": mt, "proposal": pp, "sale": sale}


def _messaggio(k, cid):
    """Un messaggio IN CODA per il contatto."""
    return _q(k, "INSERT INTO communication_messages (agency_id, contact_id, channel, direction, communication_type, "
                 "mode, reason_code, rendered_body, destination_snapshot, subject_snapshot, idempotency_key, actor_type) "
                 "VALUES (1, %s, 'email', 'outbound', 'service', 'manual', 'operator_manual', 'Testo', "
                 "'p@example.it', 'Oggetto', %s, 'system') RETURNING id", (cid, str(uuid.uuid4())))[0][0]


def _errore(k, sql, params=None):
    psycopg2 = k["psycopg2"]
    try:
        _q(k, sql, params)
    except psycopg2.Error as exc:
        # una down con BEGIN esplicito lascia la transazione abortita: si chiude
        with k["conn"].cursor() as cur:
            cur.execute("ROLLBACK")
        return str(exc).splitlines()[0]
    return None


# ---------------------------------------------------------------------------

def test_01_contatto_semplice_sposta_legge_ripristina(k):
    api = k["api"]()
    cid = _contatto(k, email="semplice@example.it", phone="333 1112223")
    assert cid in _elenco(k)
    r = _check(k, cid)
    assert r.status_code == 200 and r.json()["can_trash"] is True and r.json()["blockers"] == []
    assert r.json()["history"] == [] and r.json()["effects"]["queued_messages"] == 0
    # motivo fuori catalogo, nota troppo lunga: 400 senza modifiche
    assert _sposta(k, cid, reason="boh").json()["code"] == "INVALID_TRASH_REASON"
    assert _sposta(k, cid, note="x" * 501).status_code == 422
    assert not _nel_cestino(k, cid)
    r = _sposta(k, cid, reason="created_by_mistake", note="  Inserito due volte  ")
    assert r.status_code == 200, r.text
    assert r.json()["id"] == cid and r.json()["deleted_reason"] == "created_by_mistake"
    # registro append-only, nota ripulita
    ev = _q(k, "SELECT action, reason_code, note, actor_user_id, before_state->>'status' FROM record_lifecycle_events "
               "WHERE entity_type = 'contact' AND entity_id = %s ORDER BY id", (cid,))
    assert ev == [["trash", "created_by_mistake", "Inserito due volte", k["ids"]["owner_a"], "active"]]
    assert _errore(k, "UPDATE record_lifecycle_events SET note = 'x' WHERE entity_id = %s "
                      "AND entity_type = 'contact'", (cid,)) is not None
    # sparisce da elenco e ricerca; la scheda resta leggibile e dice chi/quando/perche'
    assert cid not in _elenco(k) and cid not in _elenco(k, search="semplice")
    assert cid not in _elenco(k, status="active")
    scheda = api.get(f"/api/core/contacts/{cid}").json()
    assert scheda["trash"]["deleted_reason"] == "created_by_mistake" and scheda["trash"]["deleted_by_name"] == "Olga"
    assert scheda["trash"]["deleted_note"] == "Inserito due volte" and scheda["trash"]["can_restore"] is True
    r360 = api.get(f"/api/crm/contacts/{cid}/360")
    assert r360.status_code == 200 and r360.json()["contact"]["trash"]["deleted_reason"] == "created_by_mistake"
    voce = _cestino(k)[cid]
    assert (voce["deleted_by_name"], voce["deleted_note"], voce["email"]) == ("Olga", "Inserito due volte",
                                                                              "semplice@example.it")
    # gia' nel Cestino
    assert _sposta(k, cid).json()["code"] == "ALREADY_DELETED"
    assert _codici(_check(k, cid)) == ["ALREADY_DELETED"]
    # ripristino: stesso id, di nuovo in elenco, campi deleted_* puliti, evento
    r = _ripristina(k, cid)
    assert r.status_code == 200 and r.json()["id"] == cid and r.json()["deleted_at"] is None
    assert r.json()["possible_duplicates"] == []
    assert cid in _elenco(k) and cid not in _cestino(k)
    assert _q(k, "SELECT deleted_by_user_id, deleted_reason FROM contacts WHERE id = %s", (cid,))[0] == [None, None]
    assert [x[0] for x in _q(k, "SELECT action FROM record_lifecycle_events WHERE entity_type = 'contact' "
                                "AND entity_id = %s ORDER BY id", (cid,))] == ["trash", "restore"]
    assert _ripristina(k, cid).json()["code"] == "NOT_DELETED"
    # nessuna cancellazione fisica: la riga c'e' sempre stata
    assert _q(k, "SELECT count(*) FROM contacts WHERE id = %s", (cid,))[0][0] == 1


def test_02_blocchi_con_collegamenti_nulla_si_chiude(k):
    uid = k["ids"]["owner_a"]
    cid = _contatto(k)
    lead = _lead(k, cid, pipeline="sell")
    br = _richiesta(k, cid)
    app = _q(k, "INSERT INTO appointments (agency_id, assigned_user_id, appointment_type, status, start_at, end_at, "
                "contact_id, source) VALUES (1, %s, 'call', 'scheduled', %s, %s, %s, 'crm_manual') RETURNING id",
             (uid, _futuro(4, 9), _futuro(4, 10), cid))[0][0]
    task = _q(k, "INSERT INTO tasks (agency_id, contact_id, title, status) VALUES (1, %s, 'Richiamare', 'open') "
                 "RETURNING id", (cid,))[0][0]
    _q(k, "INSERT INTO property_contacts (property_id, contact_id, role) VALUES (%s, %s, 'owner')", (CON_INCARICO, cid))
    r = _check(k, cid)
    assert r.status_code == 200 and r.json()["can_trash"] is False
    blocchi = {b["code"]: b for b in r.json()["blockers"]}
    assert set(blocchi) == {"LEAD_OPEN", "FUTURE_APPOINTMENT", "BUY_REQUEST_OPEN", "MANDATE_OWNER", "TASK_OPEN"}
    assert blocchi["LEAD_OPEN"]["link"]["href"] == "#/venditori"
    assert blocchi["LEAD_OPEN"]["items"][0]["id"] == lead and blocchi["LEAD_OPEN"]["items"][0]["href"] == "#/venditori"
    assert blocchi["BUY_REQUEST_OPEN"]["items"][0]["href"] == f"#/acquirenti/{br}"
    assert blocchi["FUTURE_APPOINTMENT"]["items"][0]["id"] == app
    assert blocchi["FUTURE_APPOINTMENT"]["items"][0]["href"].startswith("#/agenda/giorno/")
    assert blocchi["MANDATE_OWNER"]["items"][0]["href"] == f"#/immobili/{CON_INCARICO}"
    assert blocchi["TASK_OPEN"]["items"][0]["id"] == task and blocchi["TASK_OPEN"]["link"]["href"] == "#/attivita"
    for b in blocchi.values():
        assert b["label"] and all(i["label"] for i in b["items"])
    # la conferma ricontrolla: 409 con gli stessi blocchi, NULLA cambia
    r = _sposta(k, cid)
    assert r.status_code == 409 and r.json()["code"] == "TRASH_BLOCKED"
    assert {b["code"] for b in r.json()["blockers"]} == set(blocchi)
    assert not _nel_cestino(k, cid)
    assert _q(k, "SELECT status FROM leads WHERE id = %s", (lead,))[0][0] == "open"
    assert _q(k, "SELECT status FROM buy_requests WHERE id = %s", (br,))[0][0] == "active"
    assert _q(k, "SELECT status FROM appointments WHERE id = %s", (app,))[0][0] == "scheduled"
    assert _q(k, "SELECT count(*) FROM property_contacts WHERE contact_id = %s", (cid,))[0][0] == 1
    # acquisizione aperta e vendita in corso (su un altro contatto)
    altro = _contatto(k)
    _q(k, "INSERT INTO property_contacts (property_id, contact_id, role) VALUES (%s, %s, 'owner')", (SENZA_INCARICO, altro))
    incontro = _q(k, "INSERT INTO appointments (agency_id, assigned_user_id, appointment_type, status, start_at, end_at, "
                     "property_id, source) VALUES (1, %s, 'seller_meeting', 'scheduled', %s, %s, %s, 'crm_manual') "
                     "RETURNING id", (uid, _futuro(6, 9), _futuro(6, 10), SENZA_INCARICO))[0][0]
    acq = _q(k, "INSERT INTO acquisitions (agency_id, property_id, owner_contact_id, assigned_agent_id, appointment_id, "
                "created_by_user_id) VALUES (1, %s, %s, %s, %s, %s) RETURNING id",
             (SENZA_INCARICO, altro, uid, incontro, uid))[0][0]
    acquirente = _contatto(k)
    catena = _catena(k, acquirente, SENZA_INCARICO, proposta="accepted", vendita="pending")
    _q(k, "INSERT INTO property_sale_sellers (sale_id, contact_id, role) VALUES (%s, %s, 'owner')", (catena["sale"], altro))
    blocchi = {b["code"]: b for b in _check(k, altro).json()["blockers"]}
    assert {"ACQUISITION_OPEN", "SALE_PENDING"} <= set(blocchi)
    assert blocchi["ACQUISITION_OPEN"]["items"][0]["href"] == f"#/acquisizioni/{acq}"
    assert blocchi["SALE_PENDING"]["items"][0]["href"] == f"#/immobili/{SENZA_INCARICO}"
    # lato acquirente: la richiesta aperta e la vendita in corso; poi una proposta aperta
    assert {"BUY_REQUEST_OPEN", "SALE_PENDING"} <= set(_codici(_check(k, acquirente)))
    offerente = _contatto(k)
    proposta = _catena(k, offerente, SENZA_INCARICO, proposta="submitted")
    blocchi = {b["code"]: b for b in _check(k, offerente).json()["blockers"]}
    assert blocchi["PROPOSAL_OPEN"]["items"][0]["href"] == f"#/acquirenti/{proposta['br']}"
    # proprietario di un immobile SENZA incarico: nessun blocco per quello
    assert "MANDATE_OWNER" not in blocchi
    # chiusi i processi (a mano, dai loro posti), il Cestino passa
    _q(k, "UPDATE leads SET status = 'closed' WHERE id = %s", (lead,))
    _q(k, "UPDATE buy_requests SET status = 'closed' WHERE id = %s", (br,))
    _q(k, "UPDATE appointments SET status = 'cancelled', cancelled_at = NOW(), cancelled_kind = 'mistake' WHERE id = %s", (app,))
    _q(k, "UPDATE tasks SET status = 'cancelled' WHERE id = %s", (task,))
    _q(k, "DELETE FROM property_contacts WHERE contact_id = %s", (cid,))
    assert _check(k, cid).json()["can_trash"] is True
    assert _sposta(k, cid).status_code == 200


def test_03_storico_agent_si_ferma_titolare_no(k):
    cid = _contatto(k, "agent_a")                      # nasce assegnato all'agente
    r = k["api"]("agent_a").post("/api/core/activities", json={"contact_id": cid, "activity_type": "call",
                                                                "subject": "Chiamata"})
    assert r.status_code == 201, r.text
    _lead(k, cid, status="closed")
    r = _check(k, cid, "agent_a")
    assert r.json()["can_trash"] is False and _codici(r) == ["HISTORY_REQUIRES_ADMIN"]
    voci = {v["code"] for v in r.json()["blockers"][0]["items"]}
    assert voci == {"CONTACT_ACTIVITY", "LEAD_HISTORY"}
    r = _sposta(k, cid, "agent_a")
    assert r.status_code == 403 and r.json()["code"] == "HISTORY_REQUIRES_ADMIN"
    assert {v["code"] for v in r.json()["history"]} == voci and not _nel_cestino(k, cid)
    # il titolare: puo', e il controllo gli mostra lo storico che resta
    r = _check(k, cid)
    assert r.json()["can_trash"] is True and {v["code"] for v in r.json()["history"]} == voci
    assert _sposta(k, cid, "admin_a").status_code == 200
    # lo storico e' ancora li', leggibile dalla scheda
    scheda = k["api"]().get(f"/api/crm/contacts/{cid}/360").json()
    assert len(scheda["activities"]) == 1 and len(scheda["leads"]) == 1
    # un'attivita' segnata «per errore» e le attivita' di sistema non sono storico
    pulito = _contatto(k, "agent_a")
    k["api"]("agent_a").post("/api/core/activities", json={"contact_id": pulito, "activity_type": "system"})
    assert _check(k, pulito, "agent_a").json()["can_trash"] is True


def test_04_permessi_agenti_e_agenzie(k):
    mio = _contatto(k, "agent_a")
    altrui = _contatto(k, "agent2_a")
    # un agent non vede (404) il contatto di un collega: ne' controllo, ne' Cestino
    assert _check(k, altrui, "agent_a").status_code == 404
    assert _sposta(k, altrui, "agent_a").status_code == 404
    # un'altra agenzia: 404 indistinguibile, e il suo Cestino non mostra nulla di A
    assert _check(k, mio, "owner_b").status_code == 404
    assert _sposta(k, mio, "owner_b").status_code == 404
    assert _sposta(k, mio, "agent_a").status_code == 200
    assert _ripristina(k, mio, "owner_b").status_code == 404
    assert mio not in _cestino(k, "owner_b")
    # Cestino: l'agent vede cio' che ha spostato lui, il titolare tutto
    assert _sposta(k, altrui, "admin_a").status_code == 200
    assert mio in _cestino(k, "agent_a") and altrui not in _cestino(k, "agent_a")
    assert {mio, altrui} <= set(_cestino(k, "owner_a"))
    # ripristino: un agent non ripristina cio' che ha spostato un altro
    r = _ripristina(k, altrui, "agent2_a")
    assert r.status_code == 403 and r.json()["code"] == "NOT_DELETED_BY_YOU" and _nel_cestino(k, altrui)
    scheda = k["api"]("agent2_a").get(f"/api/core/contacts/{altrui}").json()
    assert scheda["trash"]["can_restore"] is False
    assert _ripristina(k, mio, "agent_a").status_code == 200
    assert _ripristina(k, altrui, "owner_a").status_code == 200


def test_05_congelato_nessun_collegamento_nuovo_nessuna_riapertura(k):
    api = k["api"]()
    cid = _contatto(k)
    lead = _lead(k, cid, status="closed")
    br = _richiesta(k, cid, status="closed")
    assert _sposta(k, cid, "owner_a").status_code == 200

    def rifiuto(r):
        assert r.status_code == 409, r.text
        corpo = r.json()
        assert corpo.get("code") == "CONTACT_IN_TRASH" or corpo["detail"].startswith("CONTACT_IN_TRASH"), corpo

    rifiuto(api.patch(f"/api/core/contacts/{cid}", json={"notes": "modifica"}))
    rifiuto(api.post(f"/api/core/contacts/{cid}/roles", json={"role": "buyer"}))
    rifiuto(api.patch(f"/api/core/contacts/{cid}/assignment", json={"assigned_agent_id": k["ids"]["agent_a"]}))
    rifiuto(api.post("/api/core/leads", json={"contact_id": cid}))
    rifiuto(api.post("/api/core/activities", json={"contact_id": cid, "activity_type": "call"}))
    rifiuto(api.post("/api/core/tasks", json={"contact_id": cid, "title": "Richiamare"}))
    rifiuto(api.post(f"/api/property/properties/{SENZA_INCARICO}/contacts", json={"contact_id": cid, "role": "owner"}))
    # nel database, qualunque strada
    for sql in ("INSERT INTO buy_requests (agency_id, contact_id, title) VALUES (1, %s, 'x')",
                "INSERT INTO property_visits (property_id, contact_id, scheduled_at) VALUES (18, %s, NOW())",
                "INSERT INTO owner_accounts (contact_id) VALUES (%s)"):
        assert "CONTACT_IN_TRASH" in _errore(k, sql, (cid,)), sql
    # un processo chiuso non si riapre; una modifica che non lo riapre passa
    assert "CONTACT_IN_TRASH" in _errore(k, "UPDATE leads SET status = 'open' WHERE id = %s", (lead,))
    assert "CONTACT_IN_TRASH" in _errore(k, "UPDATE buy_requests SET status = 'active' WHERE id = %s", (br,))
    assert _errore(k, "UPDATE leads SET notes = 'nota storica' WHERE id = %s", (lead,)) is None
    # il consenso (revoca da disiscrizione) resta possibile
    assert _errore(k, "UPDATE contacts SET marketing_consent = FALSE, marketing_revoked_at = NOW() WHERE id = %s",
                   (cid,)) is None
    # dopo il ripristino tutto torna come prima
    assert _ripristina(k, cid).status_code == 200
    assert api.patch(f"/api/core/contacts/{cid}", json={"notes": "ok"}).status_code == 200
    assert _errore(k, "UPDATE leads SET status = 'open' WHERE id = %s", (lead,)) is None


def test_06_invisibile_nelle_superfici_operative(k):
    api = k["api"]()
    cid = _contatto(k, email="doppio.cestino@example.it", phone="347 0000111")
    br = _richiesta(k, cid, status="closed")
    lead = _lead(k, cid, status="closed", pipeline="sell")
    _q(k, "INSERT INTO property_leads (property_id, lead_id, relation_type) VALUES (%s, %s, 'seller')",
       (SENZA_INCARICO, lead))
    _q(k, "INSERT INTO property_contacts (property_id, contact_id, role) VALUES (%s, %s, 'contact')", (SENZA_INCARICO, cid))

    def richieste():
        return {x["id"] for x in api.get("/api/buy/requests", params={"limit": 200}).json()["items"]}

    def venditori():
        r = api.get("/api/crm/sellers", params={"status": "closed", "limit": 200})
        assert r.status_code == 200, r.text
        return {x["lead_id"] for x in r.json()["items"]}

    def contatti_immobile():
        return {x["contact_id"] for x in api.get(f"/api/property/properties/{SENZA_INCARICO}").json()["contacts"]}

    def selettore_owner():
        r = api.get("/api/owner/admin/lookups/contacts", params={"search": "doppio.cestino"})
        assert r.status_code == 200, r.text
        return {x["id"] for x in r.json()["items"]}

    assert br in richieste() and lead in venditori() and cid in contatti_immobile() and cid in selettore_owner()
    assert _sposta(k, cid).status_code == 200
    assert cid not in _elenco(k, search="doppio.cestino") and cid not in _elenco(k, search="3470000111")
    assert br not in richieste() and lead not in venditori()
    assert cid not in contatti_immobile() and cid not in selettore_owner()
    # la scheda del contatto conserva lo storico (richiesta, lead, immobile)
    scheda = api.get(f"/api/crm/contacts/{cid}/360").json()
    assert [x["id"] for x in scheda["buy_requests"]] == [br] and [x["id"] for x in scheda["leads"]] == [lead]
    # il collegamento c'e' ancora, ed e' quello di prima
    assert _q(k, "SELECT count(*) FROM property_contacts WHERE contact_id = %s", (cid,))[0][0] == 1
    # prenotazione pubblica con la stessa email: un contatto NUOVO, mai quello nel Cestino
    from appointments.service import _contatto_pubblico_coerente
    from core.database import core_cursor
    with core_cursor(commit=True) as (_, cur):
        nuovo, creato = _contatto_pubblico_coerente(cur, 1, {
            "contact_type": "person", "first_name": "Pia", "last_name": None, "company_name": None,
            "display_name": "Pia", "email": "doppio.cestino@example.it", "email_normalized": "doppio.cestino@example.it",
            "phone": None, "phone_normalized": None, "secondary_phone": None, "source": "booking_link",
            "status": "active", "notes": None})
    assert creato is True and nuovo != cid
    # dopo il ripristino tutto torna
    assert _ripristina(k, cid).status_code == 200
    assert br in richieste() and lead in venditori() and cid in contatti_immobile()


def test_07_comunicazioni_sospese_e_annullate_restano_tali(k):
    api = k["api"]()
    cid = _contatto(k, email="comunica@example.it")
    m1, m2, m3 = _messaggio(k, cid), _messaggio(k, cid), _messaggio(k, cid)
    effetti = _check(k, cid).json()["effects"]
    assert effetti == {"queued_messages": 3, "automations_to_pause": True, "automations_already_paused": False}
    r = _sposta(k, cid)
    assert r.status_code == 200, r.text
    assert r.json()["communications"] == {"automations_paused": True, "automations_already_paused": False,
                                          "cancelled_messages": 3}
    righe = dict(_q(k, "SELECT id, status FROM communication_messages WHERE contact_id = %s", (cid,)))
    assert righe == {m1: "cancelled", m2: "cancelled", m3: "cancelled"}
    assert _q(k, "SELECT metadata->>'cancel_reason' FROM communication_messages WHERE id = %s", (m1,))[0][0] == "contact_trashed"
    assert _q(k, "SELECT paused, pause_reason FROM communication_automation_controls WHERE contact_id = %s",
              (cid,))[0] == [True, "contact_trashed"]
    ev = _q(k, "SELECT metadata->'communications'->>'cancelled_messages' FROM record_lifecycle_events "
               "WHERE entity_type = 'contact' AND entity_id = %s AND action = 'trash'", (cid,))
    assert ev == [["3"]]
    # nel Cestino: nessun messaggio nuovo, nessuna ripresa delle automazioni
    r = api.post(f"/api/communication/contacts/{cid}/messages", json={"subject": "Ciao", "body": "Testo",
                                                                     "communication_type": "service"})
    assert r.status_code == 409 and "CONTACT_IN_TRASH" in r.text
    r = api.post(f"/api/communication/contacts/{cid}/automation/resume")
    assert r.status_code == 409 and "CONTACT_IN_TRASH" in r.text
    # lo storico dei messaggi resta leggibile
    assert api.get(f"/api/communication/contacts/{cid}/messages").status_code == 200
    # il ripristino NON riattiva nulla
    assert _ripristina(k, cid).status_code == 200
    assert _q(k, "SELECT paused FROM communication_automation_controls WHERE contact_id = %s", (cid,))[0][0] is True
    assert {s for _, s in _q(k, "SELECT id, status FROM communication_messages WHERE contact_id = %s", (cid,))} == {"cancelled"}
    # la riattivazione e' un gesto esplicito, dopo il ripristino
    assert api.post(f"/api/communication/contacts/{cid}/automation/resume").status_code == 200
    # automazioni gia' sospese prima: restano come sono, e lo si dice
    altro = _contatto(k)
    assert api.post(f"/api/communication/contacts/{altro}/automation/pause").status_code == 200
    assert _check(k, altro).json()["effects"]["automations_already_paused"] is True
    assert _sposta(k, altro).json()["communications"]["automations_already_paused"] is True


def test_08_portale_proprietario_si_disattiva_prima(k):
    cid = _contatto(k)
    conto = _q(k, "INSERT INTO owner_accounts (contact_id, status) VALUES (%s, 'active') RETURNING id", (cid,))[0][0]
    r = _check(k, cid)
    blocco = next(b for b in r.json()["blockers"] if b["code"] == "OWNER_PORTAL_ACTIVE")
    assert blocco["action"] == {"kind": "owner_account_disable", "account_id": conto, "allowed": True,
                                "label": "Disattiva accesso",
                                "endpoint": f"/api/owner/admin/accounts/{conto}/disable"}
    # l'amministratore vede il blocco ma non l'azione (la superficie OWNER e' del titolare)
    blocco_admin = next(b for b in _check(k, cid, "admin_a").json()["blockers"] if b["code"] == "OWNER_PORTAL_ACTIVE")
    assert blocco_admin["action"]["allowed"] is False and "titolare" in blocco_admin["label"]
    assert _sposta(k, cid).json()["code"] == "TRASH_BLOCKED"
    assert _q(k, "SELECT status FROM owner_accounts WHERE id = %s", (conto,))[0][0] == "active"   # nessun automatismo
    # la rotta esistente, poi il Cestino
    assert k["api"]("admin_a").post(f"/api/owner/admin/accounts/{conto}/disable").status_code == 403
    assert k["api"]().post(f"/api/owner/admin/accounts/{conto}/disable").status_code == 200
    r = _check(k, cid)
    assert r.json()["can_trash"] is True
    assert [h["code"] for h in r.json()["history"]] == ["OWNER_PORTAL_HISTORY"]
    assert _sposta(k, cid).status_code == 200
    # nel Cestino non si riattiva; dopo il ripristino resta disattivato
    r = k["api"]().post(f"/api/owner/admin/accounts/{conto}/enable")
    assert r.status_code == 409 and r.json()["code"] == "CONTACT_IN_TRASH"
    assert _ripristina(k, cid).status_code == 200
    assert _q(k, "SELECT status FROM owner_accounts WHERE id = %s", (conto,))[0][0] == "disabled"


def test_09_ripristino_segnala_possibili_doppioni_senza_unire(k):
    vecchio = _contatto(k, email="Gemello@Example.it", phone="+39 333 9998887")
    assert _sposta(k, vecchio, reason="duplicate").status_code == 200
    nuovo = _contatto(k, email="gemello@example.it")
    telefono = _contatto(k, phone="3339998887")
    prima = _q(k, "SELECT id, md5(row_to_json(c)::text) FROM contacts c WHERE id IN (%s, %s) ORDER BY id",
               (nuovo, telefono))
    r = _ripristina(k, vecchio)
    assert r.status_code == 200 and r.json()["id"] == vecchio
    doppioni = {d["id"]: (d["same_email"], d["same_phone"]) for d in r.json()["possible_duplicates"]}
    assert doppioni == {nuovo: (True, False), telefono: (False, True)}
    assert _q(k, "SELECT id, md5(row_to_json(c)::text) FROM contacts c WHERE id IN (%s, %s) ORDER BY id",
              (nuovo, telefono)) == prima
    assert {vecchio, nuovo, telefono} <= _elenco(k)
    meta = _q(k, "SELECT metadata->'possible_duplicates' FROM record_lifecycle_events WHERE entity_type = 'contact' "
                 "AND entity_id = %s AND action = 'restore'", (vecchio,))[0][0]
    assert sorted(meta) == sorted([nuovo, telefono])


def test_10_concorrenza(k):
    from core import database as core_database
    # due spostamenti dello stesso contatto: uno solo
    cid = _contatto(k)
    esiti = _in_parallelo([lambda: _sposta(k, cid), lambda: _sposta(k, cid)])
    assert sorted(r.status_code for r in esiti) == [200, 409]
    assert [r.json()["code"] for r in esiti if r.status_code == 409] == ["ALREADY_DELETED"]
    assert _q(k, "SELECT count(*) FROM record_lifecycle_events WHERE entity_type = 'contact' AND entity_id = %s "
                 "AND action = 'trash'", (cid,))[0][0] == 1

    # un lead nasce mentre lo spostamento aspetta: lo spostamento lo vede e si ferma
    cid = _contatto(k)
    altra = core_database.get_connection()
    try:
        with altra.cursor() as cur:
            cur.execute("INSERT INTO leads (agency_id, contact_id, source, pipeline, stage, priority, status) "
                        "VALUES (1, %s, 'manual', 'general', 'new', 'normal', 'open')", (cid,))
        esito = {}

        def sposta():
            esito["r"] = _sposta(k, cid)
        import threading
        filo = threading.Thread(target=sposta)
        filo.start()
        assert _in_attesa_di_lock(k, "%FOR UPDATE OF c%")
        altra.commit()
        filo.join(30)
    finally:
        altra.close()
    assert esito["r"].status_code == 409 and esito["r"].json()["code"] == "TRASH_BLOCKED"
    assert not _nel_cestino(k, cid)

    # lo spostamento e' in corso mentre nasce un lead: il lead attende e viene rifiutato
    cid = _contatto(k)
    altra = core_database.get_connection()
    try:
        with altra.cursor() as cur:
            cur.execute("SELECT id FROM contacts WHERE id = %s FOR UPDATE", (cid,))
            cur.execute("UPDATE contacts SET deleted_at = NOW(), deleted_reason = 'duplicate' WHERE id = %s", (cid,))
        esito = {}

        def crea_lead():
            esito["r"] = k["api"]().post("/api/core/leads", json={"contact_id": cid})
        import threading
        filo = threading.Thread(target=crea_lead)
        filo.start()
        assert _in_attesa_di_lock(k, "%FOR KEY SHARE%")
        altra.commit()
        filo.join(30)
    finally:
        altra.close()
    assert esito["r"].status_code == 409 and "CONTACT_IN_TRASH" in esito["r"].json()["detail"]
    assert _q(k, "SELECT count(*) FROM leads WHERE contact_id = %s", (cid,))[0][0] == 0


def test_99_senza_la_090_e_la_down(k):
    api = k["api"]()
    cid = _contatto(k)
    assert _sposta(k, cid).status_code == 200
    giu = GIU.read_text(encoding="utf-8")
    # la down si ferma: un contatto e' nel Cestino; nulla cambia
    assert "contatti nel Cestino" in _errore(k, giu)
    assert _nel_cestino(k, cid)
    assert _ripristina(k, cid).status_code == 200
    # (database usa-e-getta: si ripristinano anche i contatti lasciati nel Cestino dalle prove precedenti)
    _q(k, "UPDATE contacts SET deleted_at = NULL, deleted_by_user_id = NULL, deleted_reason = NULL "
          "WHERE deleted_at IS NOT NULL")
    # e si ferma anche per gli eventi del registro (append-only)
    assert "eventi di contatti" in _errore(k, giu)
    # su un database usa-e-getta: si tolgono gli eventi aggirando la guardia append-only
    trigger = _q(k, "SELECT tgname FROM pg_trigger WHERE tgrelid = 'record_lifecycle_events'::regclass "
                    "AND NOT tgisinternal")
    for (t,) in trigger:
        _q(k, f"ALTER TABLE record_lifecycle_events DISABLE TRIGGER {t}")
    _q(k, "DELETE FROM record_lifecycle_events WHERE entity_type = 'contact'")
    for (t,) in trigger:
        _q(k, f"ALTER TABLE record_lifecycle_events ENABLE TRIGGER {t}")
    assert _errore(k, giu) is None
    assert _q(k, "SELECT count(*) FROM information_schema.columns WHERE table_name = 'contacts' "
                 "AND column_name LIKE 'deleted%%'")[0][0] == 0
    # codice nuovo, database senza la 090: elenco e scheda come prima, Cestino 503 leggibile
    assert cid in _elenco(k)
    assert api.get(f"/api/core/contacts/{cid}").status_code == 200
    r = _check(k, cid)
    assert r.status_code == 503 and r.json()["code"] == "TRASH_NOT_INSTALLED"
    assert api.get("/api/core/trash/contacts").json()["code"] == "TRASH_NOT_INSTALLED"
    assert api.post("/api/core/leads", json={"contact_id": cid}).status_code == 201
    assert api.patch(f"/api/core/contacts/{cid}", json={"notes": "senza 090"}).status_code == 200
    # up di nuovo (idempotente nella forma: la sonda finale passa)
    with k["conn"].cursor() as cur:
        cur.execute("BEGIN")
        cur.execute(SU.read_text(encoding="utf-8"))
        cur.execute("COMMIT")
    assert _q(k, "SELECT count(*) FROM pg_trigger WHERE tgname LIKE 'trg_%%_contact_trash_%%'")[0][0] == 14
    r = _check(k, cid)
    assert r.status_code == 200 and "LEAD_OPEN" in _codici(r)
