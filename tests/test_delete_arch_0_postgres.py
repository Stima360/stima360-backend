"""DELETE-ARCH Fase 0 - sicurezza, permessi e archiviazione, sul database VERO.

Contratto REV 2 approvato per la Fase 0 (D10 agent su propri/assegnati, D19
auto-assegnazione, nessuna migration, nessun Cestino). Le sezioni seguono il
brief (§12):

  A  assegnazione: un agent crea un immobile assegnato a se';
  B  archivio: agent solo sul proprio, 403 sul collega; owner tutta l'agenzia;
  C  archivio bloccato da acquisizione aperta, appuntamento futuro, vendita;
  D  stime: agent/admin 403; owner + stima pulita passa; attivita'/task/
     lead_stime/messaggio/accesso proprietario bloccano; batch misto = rollback;
  E  rimozione proprietario: acquisizione, opportunita' Vende, ultimo
     proprietario con incarico; promozione del principale;
  F  property_lead seller operativo non scollegabile;
  G  figli: scope/assegnazione; documento condiviso 409 e non 500;
  H  visibilita' degli archiviati;
  I  multi-agenzia.

Stessa fixture `completo` di `test_censimento_3_backend_postgres` (P29_TEST_DSN,
database locale usa-e-getta con lo schema completo); senza DSN il modulo e'
SKIP. Prima della Fase 0 ogni test di questo modulo fallisce (fail-before).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from tests.test_censimento_3_backend_postgres import DSN, _q, completo  # noqa: F401  (fixture riusata)

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare DELETE-ARCH Fase 0")

ROMA = timezone(timedelta(hours=2))


def _futuro(giorni=30, ora=10):
    return (datetime.now(ROMA) + timedelta(days=giorni)).replace(hour=ora, minute=0, second=0, microsecond=0)


# ---------------------------------------------------------------------------
# fixture: client per ruolo, app di main.py per le stime, pulizia
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def operatori(completo):
    """Un secondo agente (collega) e un admin nell'agenzia 1."""
    with completo["conn"].cursor() as cur:
        def operatore(email, nome, agenzia, ruolo):
            cur.execute("INSERT INTO operator_users (email, email_normalized, password_hash, first_name) "
                        "VALUES (%s, %s, 'pbkdf2_sha256$1$x$y', %s) RETURNING id", (email, email, nome))
            uid = cur.fetchone()[0]
            cur.execute("INSERT INTO agency_memberships (agency_id, operator_user_id, role, status) "
                        "VALUES (%s, %s, %s, 'active')", (agenzia, uid, ruolo))
            return uid
        ids = dict(completo["ids"])
        ids["agent_a2"] = operatore("a.a2@x.test", "Aldo", 1, "agent")
        ids["admin_a"] = operatore("adm.a@x.test", "Ada", 1, "agency_admin")
        cur.execute("INSERT INTO operator_users (email, email_normalized, password_hash, first_name, is_platform_admin) "
                    "VALUES ('pa@x.test', 'pa@x.test', 'pbkdf2_sha256$1$x$y', 'Pia', TRUE) RETURNING id")
        ids["platform"] = cur.fetchone()[0]
    return ids


@pytest.fixture
def mondo(completo, operatori, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import main as main_module
    from acquisitions.router import router as acquisizioni
    from buy.router import router as acquirenti
    from core.router import router as core_router
    from crm.router import router as crm_router
    from operator_auth.context import OperatorContext
    from operator_auth.dependencies import (legacy_basic_agency_context, require_authenticated_operator,
                                            require_operator)
    from property.router import router as immobili

    ids = operatori
    ruoli = {"owner_a": (ids["owner_a"], 1, "agency_owner"), "agent_a": (ids["agent_a"], 1, "agent"),
             "agent_a2": (ids["agent_a2"], 1, "agent"), "admin_a": (ids["admin_a"], 1, "agency_admin"),
             "owner_b": (ids["owner_b"], 2, "agency_owner"),
             # platform admin: in acting (agenzia visitata, nessun ruolo -
             # operator_auth.service._effective_agency) e senza acting
             "platform_acting": (ids["platform"], 1, None),
             "platform_none": (ids["platform"], None, None)}
    stato = {"chi": "owner_a"}

    def contesto():
        uid, agenzia, ruolo = ruoli[stato["chi"]]
        return OperatorContext(user_id=uid, agency_id=agenzia, role=ruolo,
                               is_platform_admin=stato["chi"].startswith("platform"),
                               session_id=None, auth_channel="operator_session")

    app = FastAPI()
    for r in (immobili, acquisizioni, acquirenti, core_router, crm_router):
        app.include_router(r)
    app.dependency_overrides[legacy_basic_agency_context] = contesto
    app.dependency_overrides[require_operator] = contesto
    client = TestClient(app)

    # main.py (rotte legacy stime): stessa agenzia/ruolo, connessione al DB di
    # prova attraverso `core.database.get_connection`, che `completo` ha gia'
    # puntato al database usa-e-getta (nessun nuovo sito di connessione).
    from core import database as core_database
    monkeypatch.setattr(main_module, "get_connection", lambda *a, **k: core_database.get_connection())
    main_module.app.dependency_overrides[legacy_basic_agency_context] = contesto
    main_module.app.dependency_overrides[require_authenticated_operator] = lambda: None
    legacy = TestClient(main_module.app, raise_server_exceptions=False)

    class _Come:
        def __init__(self, chi, cl):
            self.chi, self.cl = chi, cl

        def __getattr__(self, metodo):
            def invia(*a, **k):
                stato["chi"] = self.chi
                return getattr(self.cl, metodo)(*a, **k)
            return invia

    def api(chi="owner_a"):
        return _Come(chi, client)

    def admin(chi="owner_a"):
        return _Come(chi, legacy)

    def pulisci():
        c = completo
        for tabella, trigger in (("acquisition_events", "trg_acquisition_events_append_only"),
                                 ("acquisitions", "trg_acquisitions_refuse_delete"),
                                 ("appointment_events", "trg_appointment_events_append_only"),
                                 ("appointments", "trg_appointments_refuse_delete"),
                                 ("activities", "trg_activities_property_history")):
            _q(c, f"ALTER TABLE {tabella} DISABLE TRIGGER {trigger}")
        _q(c, "ALTER TABLE properties DISABLE TRIGGER trg_properties_mandate_origin")
        _q(c, "UPDATE properties SET acquisition_id = NULL WHERE id > 25")
        _q(c, "ALTER TABLE properties ENABLE TRIGGER trg_properties_mandate_origin")
        _q(c, "DELETE FROM acquisition_events")
        _q(c, "DELETE FROM acquisitions")
        _q(c, "DELETE FROM appointment_events")
        _q(c, "DELETE FROM appointments")
        _q(c, "DELETE FROM activities")
        _q(c, "DELETE FROM tasks")
        for tabella, trigger in (("acquisition_events", "trg_acquisition_events_append_only"),
                                 ("acquisitions", "trg_acquisitions_refuse_delete"),
                                 ("appointment_events", "trg_appointment_events_append_only"),
                                 ("appointments", "trg_appointments_refuse_delete"),
                                 ("activities", "trg_activities_property_history")):
            _q(c, f"ALTER TABLE {tabella} ENABLE TRIGGER {trigger}")
        _q(c, "DELETE FROM property_sales")
        _q(c, "DELETE FROM property_proposals")
        _q(c, "DELETE FROM matches")
        _q(c, "DELETE FROM buy_request_history")
        _q(c, "DELETE FROM buy_requests")
        _q(c, "DELETE FROM owner_visit_feedback_publications")
        _q(c, "DELETE FROM owner_shared_documents")
        _q(c, "DELETE FROM owner_stima_access")
        _q(c, "DELETE FROM owner_home_notifications")
        _q(c, "DELETE FROM owner_accounts")
        _q(c, "ALTER TABLE communication_messages DISABLE TRIGGER trg_communication_messages_guard")
        _q(c, "DELETE FROM communication_messages")
        _q(c, "ALTER TABLE communication_messages ENABLE TRIGGER trg_communication_messages_guard")
        _q(c, "DELETE FROM lead_stime")
        _q(c, "DELETE FROM stime_dettagliate")
        _q(c, "DELETE FROM stime")
        _q(c, "DELETE FROM property_visits")
        _q(c, "DELETE FROM property_documents")
        _q(c, "DELETE FROM property_photos")
        _q(c, "DELETE FROM property_leads")
        _q(c, "DELETE FROM property_contacts")
        _q(c, "DELETE FROM property_status_history WHERE property_id > 25")
        _q(c, "DELETE FROM property_price_history WHERE property_id > 25")
        _q(c, "DELETE FROM properties WHERE id > 25")
        _q(c, "DELETE FROM leads")
        _q(c, "DELETE FROM contacts WHERE id <> %s", (c["mario"],))
        _q(c, "UPDATE contacts SET status = 'active', archived_at = NULL WHERE id = %s", (c["mario"],))

    pulisci()
    yield {**completo, "ids": ids, "api": api, "admin": admin, "ctx": contesto, "ruoli": ruoli, "stato": stato}
    pulisci()
    main_module.app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# costruttori
# ---------------------------------------------------------------------------

def _immobile(m, chi="owner_a", **extra):
    corpo = {"title": "Trilocale F0", "property_type": "apartment", "city": "Giulianova", "address": "Via Po",
             "civic_number": str(uuid.uuid4().int % 900 + 1), **extra}
    r = m["api"](chi).post("/api/property/properties", json=corpo)
    assert r.status_code == 201, r.text
    return r.json()


def _assegna(m, pid, uid):
    _q(m, "UPDATE properties SET assigned_agent_id = %s WHERE id = %s", (uid, pid))


def _contatto(m, nome="Luca Bianchi", agenzia=1):
    return _q(m, "INSERT INTO contacts (agency_id, display_name, first_name, last_name) VALUES (%s, %s, %s, %s) RETURNING id",
              (agenzia, nome, nome.split()[0], nome.split()[-1]))[0][0]


def _collega(m, pid, cid, ruolo="owner", primario=False):
    r = m["api"]().post(f"/api/property/properties/{pid}/contacts",
                        json={"contact_id": cid, "role": ruolo, "is_primary": primario})
    assert r.status_code == 201, r.text


def _lead(m, cid, pipeline="sell", status="open", agenzia=1):
    return _q(m, "INSERT INTO leads (agency_id, contact_id, pipeline, status, stage) VALUES (%s, %s, %s, %s, 'new') RETURNING id",
              (agenzia, cid, pipeline, status))[0][0]


def _property_lead(m, pid, lid, relazione="seller"):
    _q(m, "INSERT INTO property_leads (property_id, lead_id, relation_type) VALUES (%s, %s, %s)", (pid, lid, relazione))


def _acquisizione(m, pid, cid, chi="owner_a"):
    corpo = {"property_id": pid, "owner_contact_id": cid,
             "appointment": {"start_at": _futuro().isoformat(), "assigned_user_id": m["ids"]["owner_a"],
                             "client_request_id": str(uuid.uuid4())}}
    r = m["api"](chi).post("/api/acquisitions", json=corpo)
    assert r.status_code == 201, r.text
    return r.json()


def _appuntamento(m, pid, giorni=20):
    inizio = _futuro(giorni, 15)
    return _q(m, "INSERT INTO appointments (agency_id, assigned_user_id, appointment_type, status, start_at, end_at, property_id) "
                 "VALUES (1, %s, 'buyer_visit', 'scheduled', %s, %s, %s) RETURNING id",
              (m["ids"]["owner_a"], inizio, inizio + timedelta(hours=1), pid))[0][0]


def _richiesta(m, cid, agenzia=1):
    return _q(m, "INSERT INTO buy_requests (agency_id, contact_id, title, status) VALUES (%s, %s, 'Cerca trilocale', 'active') RETURNING id",
              (agenzia, cid))[0][0]


def _catena_vendita(m, pid, cid, *, proposta="submitted", vendita=None):
    """buy_request -> match -> proposta (-> vendita pendente)."""
    br = _richiesta(m, cid)
    mt = _q(m, "INSERT INTO matches (buy_request_id, property_id, compatibility_status, score_total, match_class, algorithm_version) "
               "VALUES (%s, %s, 'compatible', 80, 'good', 'test') RETURNING id", (br, pid))[0][0]
    pp = _q(m, "INSERT INTO property_proposals (match_id, amount, expires_at, idempotency_key, created_by, status) "
               "VALUES (%s, 100000, NOW() + INTERVAL '30 days', %s, 'test', %s) RETURNING id",
            (mt, str(uuid.uuid4()), proposta))[0][0]
    sale = None
    if vendita:
        sale = _q(m, "INSERT INTO property_sales (property_id, buy_request_id, proposal_id, sale_price, idempotency_key, created_by, status) "
                     "VALUES (%s, %s, %s, 100000, %s, 'test', %s) RETURNING id", (pid, br, pp, str(uuid.uuid4()), vendita))[0][0]
    return {"buy_request_id": br, "match_id": mt, "proposal_id": pp, "sale_id": sale}


def _stima(m, agenzia=1, nome="Stima F0"):
    sid = _q(m, "INSERT INTO stime (agency_id, nome, cognome, email, telefono, comune) VALUES (%s, %s, 'Test', 'f0@x.test', '333', 'Giulianova') RETURNING id",
             (agenzia, nome))[0][0]
    _q(m, "INSERT INTO stime_dettagliate (agency_id, stima_id, note) VALUES (%s, %s, %s)", (agenzia, sid, nome))
    return sid


def _stime_esistono(m, *ids):
    return {r[0] for r in _q(m, "SELECT id FROM stime WHERE id = ANY(%s)", (list(ids),))}


def _codice(r, atteso, codice=None):
    assert r.status_code == atteso, (r.status_code, r.text)
    if codice is not None:
        assert r.json().get("code") == codice, r.text
    return r.json() if r.content else None


# ---------------------------------------------------------------------------
# A - assegnazione alla creazione (D19)
# ---------------------------------------------------------------------------

def test_a01_un_agente_crea_un_immobile_assegnato_a_se(mondo):
    p = _immobile(mondo, "agent_a")
    assert p["assigned_agent_id"] == mondo["ids"]["agent_a"]
    # il payload non comanda: un altro agente nel corpo non cambia nulla (e non e' un 403)
    p2 = _immobile(mondo, "agent_a", assigned_agent_id=mondo["ids"]["agent_a2"])
    assert p2["assigned_agent_id"] == mondo["ids"]["agent_a"]


def test_a02_owner_e_admin_creano_senza_auto_assegnazione(mondo):
    assert _immobile(mondo, "owner_a")["assigned_agent_id"] is None
    assert _immobile(mondo, "admin_a")["assigned_agent_id"] is None
    p = _immobile(mondo, "owner_a", assigned_agent_id=mondo["ids"]["agent_a2"])
    assert p["assigned_agent_id"] == mondo["ids"]["agent_a2"]


# ---------------------------------------------------------------------------
# B - archivia / riattiva: permessi (D10)
# ---------------------------------------------------------------------------

def test_b01_agente_archivia_e_riattiva_solo_il_proprio(mondo):
    proprio = _immobile(mondo, "agent_a")
    del_collega = _immobile(mondo, "agent_a2")
    non_assegnato = _immobile(mondo, "owner_a")
    api = mondo["api"]("agent_a")
    _codice(api.post(f"/api/property/properties/{del_collega['id']}/archive"), 403, "NOT_ASSIGNED")
    _codice(api.post(f"/api/property/properties/{non_assegnato['id']}/archive"), 403, "NOT_ASSIGNED")
    esito = _codice(api.post(f"/api/property/properties/{proprio['id']}/archive"), 200)
    assert esito["commercial_status"] == "archived" and esito["archived_at"] is not None
    # riattiva: solo lui (o owner/admin), e torna allo stato precedente
    _codice(mondo["api"]("agent_a2").post(f"/api/property/properties/{proprio['id']}/unarchive"), 403, "NOT_ASSIGNED")
    esito = _codice(api.post(f"/api/property/properties/{proprio['id']}/unarchive"), 200)
    assert esito["commercial_status"] == "draft" and esito["archived_at"] is None and esito["warnings"] == []
    storico = [tuple(r) for r in _q(mondo, "SELECT old_value, new_value, note FROM property_status_history WHERE property_id = %s ORDER BY id", (proprio["id"],))]
    assert ("draft", "archived", "archived") in storico and ("archived", "draft", "unarchive") in storico


def test_b02_owner_e_admin_archiviano_tutta_l_agenzia_e_la_delete_legacy_resta_un_archivio(mondo):
    p1 = _immobile(mondo, "agent_a")
    p2 = _immobile(mondo, "agent_a2")
    _codice(mondo["api"]("owner_a").post(f"/api/property/properties/{p1['id']}/archive"), 200)
    _codice(mondo["api"]("admin_a").post(f"/api/property/properties/{p2['id']}/archive"), 200)
    _codice(mondo["api"]("owner_a").post(f"/api/property/properties/{p1['id']}/archive"), 409, "ALREADY_ARCHIVED")
    _codice(mondo["api"]("owner_a").post(f"/api/property/properties/{_immobile(mondo)['id']}/unarchive"), 409, "NOT_ARCHIVED")
    # DELETE legacy: stesso effetto, deprecata (D11), mai un cestino
    p3 = _immobile(mondo, "owner_a")
    r = mondo["api"]("owner_a").delete(f"/api/property/properties/{p3['id']}")
    assert r.status_code == 200 and r.headers.get("Deprecation") == "true", (r.status_code, r.headers)
    assert "/archive" in r.headers.get("Link", "")
    riga = _q(mondo, "SELECT commercial_status, archived_at FROM properties WHERE id = %s", (p3["id"],))[0]
    assert riga[0] == "archived" and riga[1] is not None
    assert _q(mondo, "SELECT count(*) FROM properties WHERE id = %s", (p3["id"],))[0][0] == 1


def test_b03_riattivazione_ripristina_lo_stato_precedente_o_draft_con_avviso(mondo):
    p = _immobile(mondo, "owner_a")
    api = mondo["api"]("owner_a")
    _codice(api.patch(f"/api/property/properties/{p['id']}", json={"commercial_status": "active"}), 200)
    _codice(api.post(f"/api/property/properties/{p['id']}/archive"), 200)
    esito = _codice(api.post(f"/api/property/properties/{p['id']}/unarchive"), 200)
    assert esito["commercial_status"] == "active" and esito["warnings"] == []
    # archiviato dallo storico (fixture: stato archived senza storico) -> draft
    assert _q(mondo, "SELECT commercial_status FROM properties WHERE id = 10")[0][0] == "archived"
    esito = _codice(api.post("/api/property/properties/10/unarchive"), 200)
    assert esito["commercial_status"] == "draft" and esito["archived_at"] is None
    _codice(api.post("/api/property/properties/10/archive"), 200)      # si rimette com'era
    # un incarico non si ricrea con la riattivazione (081): draft + avviso
    q = _immobile(mondo, "owner_a")
    _q(mondo, "INSERT INTO property_status_history (property_id, field_name, old_value, new_value) VALUES (%s, 'commercial_status', 'mandate', 'archived')", (q["id"],))
    _q(mondo, "UPDATE properties SET commercial_status = 'archived', archived_at = NOW() WHERE id = %s", (q["id"],))
    esito = _codice(api.post(f"/api/property/properties/{q['id']}/unarchive"), 200)
    assert esito["commercial_status"] == "draft" and "mandate_not_restored" in esito["warnings"]


# ---------------------------------------------------------------------------
# C - archivio bloccato dai processi aperti
# ---------------------------------------------------------------------------

def _blocchi(r):
    corpo = _codice(r, 409, "ARCHIVE_BLOCKED")
    return {b["code"] for b in corpo["blockers"]}


def test_c01_acquisizione_aperta_e_appuntamento_futuro_bloccano(mondo):
    p = _immobile(mondo, "owner_a")
    cid = _contatto(mondo)
    _collega(mondo, p["id"], cid, "owner", True)
    acq = _acquisizione(mondo, p["id"], cid)
    api = mondo["api"]("owner_a")
    codici = _blocchi(api.post(f"/api/property/properties/{p['id']}/archive"))
    assert {"open_acquisition", "future_appointment"} <= codici
    assert _q(mondo, "SELECT archived_at FROM properties WHERE id = %s", (p["id"],))[0][0] is None
    # chiusa l'acquisizione (persa), resta l'appuntamento futuro
    r = api.post(f"/api/acquisitions/{acq['id']}/lost", json={"lost_reason": "other", "version": acq["version"]})
    assert r.status_code == 200, r.text
    assert "open_acquisition" not in _blocchi(api.post(f"/api/property/properties/{p['id']}/archive"))


def test_c02_appuntamento_futuro_da_solo_e_vendita_pendente_bloccano(mondo):
    p = _immobile(mondo, "owner_a")
    _appuntamento(mondo, p["id"])
    api = mondo["api"]("owner_a")
    assert _blocchi(api.post(f"/api/property/properties/{p['id']}/archive")) == {"future_appointment"}
    _q(mondo, "ALTER TABLE appointments DISABLE TRIGGER trg_appointments_refuse_delete")
    _q(mondo, "DELETE FROM appointments WHERE property_id = %s", (p["id"],))
    _q(mondo, "ALTER TABLE appointments ENABLE TRIGGER trg_appointments_refuse_delete")
    cid = _contatto(mondo, "Anna Verdi")
    catena = _catena_vendita(mondo, p["id"], cid, proposta="accepted", vendita="pending")
    assert _blocchi(api.post(f"/api/property/properties/{p['id']}/archive")) == {"pending_sale"}
    _q(mondo, "UPDATE property_sales SET status = 'cancelled' WHERE id = %s", (catena["sale_id"],))
    _codice(api.post(f"/api/property/properties/{p['id']}/archive"), 200)


def test_c03_proposta_in_corso_e_immobile_venduto_bloccano(mondo):
    p = _immobile(mondo, "owner_a")
    cid = _contatto(mondo, "Carlo Neri")
    _catena_vendita(mondo, p["id"], cid, proposta="draft")
    api = mondo["api"]("owner_a")
    assert _blocchi(api.post(f"/api/property/properties/{p['id']}/archive")) == {"open_proposal"}
    venduto = _immobile(mondo, "owner_a")
    _q(mondo, "UPDATE properties SET commercial_status = 'sold' WHERE id = %s", (venduto["id"],))
    assert "sold" in _blocchi(api.post(f"/api/property/properties/{venduto['id']}/archive"))


# ---------------------------------------------------------------------------
# D - stime
# ---------------------------------------------------------------------------

def test_d01_solo_il_titolare_cancella_le_stime(mondo):
    """Review Fase 0: SOLO `agency_owner`. agency_admin, agent e platform admin
    in acting -> 403; platform admin senza acting -> 403 di assenza tenant
    (comportamento esistente di `agency_of`). Zero cancellazioni in tutti i
    casi rifiutati, su stime e dettagli."""
    sid = _stima(mondo)
    det = _q(mondo, "SELECT id FROM stime_dettagliate WHERE stima_id = %s", (sid,))[0][0]
    for chi in ("agent_a", "admin_a", "platform_acting", "platform_none"):
        r = mondo["admin"](chi).post("/api/admin/stime/delete", json={"ids": [sid]})
        assert r.status_code == 403, (chi, r.status_code, r.text)
        r = mondo["admin"](chi).post("/api/admin/stime_dettagliate/delete", json={"ids": [det]})
        assert r.status_code == 403, (chi, r.status_code, r.text)
        assert _stime_esistono(mondo, sid) == {sid}, chi
        assert _q(mondo, "SELECT count(*) FROM stime_dettagliate WHERE id = %s", (det,))[0][0] == 1, chi
    r = mondo["admin"]("owner_a").post("/api/admin/stime/delete", json={"ids": [sid]})
    assert r.status_code == 200 and r.json() == {"ok": True, "deleted": 1}, r.text
    assert _stime_esistono(mondo, sid) == set()
    assert _q(mondo, "SELECT count(*) FROM stime_dettagliate WHERE stima_id = %s", (sid,))[0][0] == 0


@pytest.mark.parametrize("riferimento", ["activity", "task", "lead_stime", "message", "owner_access", "appointment"])
def test_d02_una_stima_con_storico_non_si_cancella(mondo, riferimento):
    sid = _stima(mondo)
    cid = _contatto(mondo, "Dario Gialli")
    uid = mondo["ids"]["owner_a"]
    if riferimento == "activity":
        _q(mondo, "INSERT INTO activities (agency_id, activity_type, stima_id, created_by_user_id) VALUES (1, 'note', %s, %s)", (sid, uid))
    elif riferimento == "task":
        _q(mondo, "INSERT INTO tasks (agency_id, title, stima_id, created_by_user_id) VALUES (1, 'Richiama', %s, %s)", (sid, uid))
    elif riferimento == "lead_stime":
        _q(mondo, "INSERT INTO lead_stime (lead_id, stima_id) VALUES (%s, %s)", (_lead(mondo, cid), sid))
    elif riferimento == "message":
        _q(mondo, "INSERT INTO communication_messages (agency_id, stima_id, channel, direction, communication_type, mode, reason_code, "
                  "rendered_body, destination_snapshot, actor_type, idempotency_key, status) VALUES (1, %s, 'whatsapp', 'outbound', "
                  "'service', 'manual', 'stima_pdf', 'Ecco la stima', '+39333', 'system', %s, 'sent')", (sid, str(uuid.uuid4())))
    elif riferimento == "owner_access":
        acc = _q(mondo, "INSERT INTO owner_accounts (contact_id) VALUES (%s) RETURNING id", (cid,))[0][0]
        _q(mondo, "INSERT INTO owner_stima_access (owner_account_id, stima_id) VALUES (%s, %s)", (acc, sid))
    elif riferimento == "appointment":
        _q(mondo, "INSERT INTO appointments (agency_id, assigned_user_id, appointment_type, status, start_at, end_at, stima_id) "
                  "VALUES (1, %s, 'inspection', 'scheduled', %s, %s, %s)", (uid, _futuro(5, 9), _futuro(5, 10), sid))
    r = mondo["admin"]("owner_a").post("/api/admin/stime/delete", json={"ids": [sid]})
    assert r.status_code == 409, (riferimento, r.status_code, r.text)
    corpo = r.json()
    assert corpo["code"] == "NOT_PURGEABLE"
    assert [b["stima_id"] for b in corpo["blockers"]] == [sid]
    assert corpo["blockers"][0]["blockers"], corpo
    assert _stime_esistono(mondo, sid) == {sid}, "la stima deve restare"
    assert _q(mondo, "SELECT count(*) FROM stime_dettagliate WHERE stima_id = %s", (sid,))[0][0] == 1


def test_d03_batch_misto_nessuna_cancellazione(mondo):
    pulita = _stima(mondo, nome="Pulita")
    sporca = _stima(mondo, nome="Sporca")
    _q(mondo, "INSERT INTO tasks (agency_id, title, stima_id, created_by_user_id) VALUES (1, 'Richiama', %s, %s)", (sporca, mondo["ids"]["owner_a"]))
    r = mondo["admin"]("owner_a").post("/api/admin/stime/delete", json={"ids": [pulita, sporca]})
    assert r.status_code == 409 and r.json()["code"] == "NOT_PURGEABLE", r.text
    assert [b["stima_id"] for b in r.json()["blockers"]] == [sporca]
    assert _stime_esistono(mondo, pulita, sporca) == {pulita, sporca}, "tutto o niente"
    assert _q(mondo, "SELECT count(*) FROM stime_dettagliate WHERE stima_id IN (%s, %s)", (pulita, sporca))[0][0] == 2
    # da sola, la pulita va via
    assert mondo["admin"]("owner_a").post("/api/admin/stime/delete", json={"ids": [pulita]}).status_code == 200
    assert _stime_esistono(mondo, pulita, sporca) == {sporca}


def test_d04_le_righe_di_dettaglio_seguono_la_stessa_regola(mondo):
    sid = _stima(mondo)
    det = _q(mondo, "SELECT id FROM stime_dettagliate WHERE stima_id = %s", (sid,))[0][0]
    r = mondo["admin"]("owner_a").post("/api/admin/stime_dettagliate/delete", json={"ids": [det, 999999]})
    assert r.status_code == 409 and r.json()["code"] == "NOT_PURGEABLE", r.text
    assert _q(mondo, "SELECT count(*) FROM stime_dettagliate WHERE id = %s", (det,))[0][0] == 1
    r = mondo["admin"]("owner_a").post("/api/admin/stime_dettagliate/delete", json={"ids": [det]})
    assert r.status_code == 200 and r.json()["deleted"] == 1, r.text


# ---------------------------------------------------------------------------
# E - rimozione proprietario
# ---------------------------------------------------------------------------

def test_e01_referente_di_acquisizione_aperta_non_si_scollega(mondo):
    p = _immobile(mondo, "owner_a")
    cid = _contatto(mondo)
    _collega(mondo, p["id"], cid, "owner", True)
    _acquisizione(mondo, p["id"], cid)
    r = mondo["api"]("owner_a").delete(f"/api/property/properties/{p['id']}/contacts/{cid}/owner")
    _codice(r, 409, "OWNER_OF_OPEN_ACQUISITION")
    assert _q(mondo, "SELECT count(*) FROM property_contacts WHERE property_id = %s AND contact_id = %s", (p["id"], cid))[0][0] == 1


def test_e02_opportunita_vende_aperta_non_si_scollega_e_non_si_chiude(mondo):
    p = _immobile(mondo, "owner_a")
    cid = _contatto(mondo, "Elena Rosa")
    _collega(mondo, p["id"], cid, "owner", True)
    lid = _lead(mondo, cid, status="paused")
    _property_lead(mondo, p["id"], lid, "seller")
    r = mondo["api"]("owner_a").delete(f"/api/property/properties/{p['id']}/contacts/{cid}/owner")
    corpo = _codice(r, 409, "SELLER_OPPORTUNITY_OPEN")
    assert corpo["lead_id"] == lid and "Smetti" in corpo["detail"]
    assert _q(mondo, "SELECT status FROM leads WHERE id = %s", (lid,))[0][0] == "paused", "mai chiusa in silenzio"
    _q(mondo, "UPDATE leads SET status = 'closed' WHERE id = %s", (lid,))
    _codice(mondo["api"]("owner_a").delete(f"/api/property/properties/{p['id']}/contacts/{cid}/owner"), 204)


def test_e03_ultimo_proprietario_con_incarico_resta_e_il_principale_viene_promosso(mondo):
    p = _immobile(mondo, "owner_a")
    c1, c2 = _contatto(mondo, "Primo Prop"), _contatto(mondo, "Secondo Prop")
    _collega(mondo, p["id"], c1, "owner", True)
    _collega(mondo, p["id"], c2, "owner", False)
    _q(mondo, "ALTER TABLE properties DISABLE TRIGGER trg_properties_mandate_origin")
    _q(mondo, "UPDATE properties SET mandate_type = 'esclusiva', mandate_start = CURRENT_DATE, commercial_status = 'mandate' WHERE id = %s", (p["id"],))
    _q(mondo, "ALTER TABLE properties ENABLE TRIGGER trg_properties_mandate_origin")
    api = mondo["api"]("owner_a")
    _codice(api.delete(f"/api/property/properties/{p['id']}/contacts/{c1}/owner"), 204)
    assert [tuple(r) for r in _q(mondo, "SELECT contact_id FROM property_contacts WHERE property_id = %s AND is_primary", (p["id"],))] == [(c2,)]
    assert _q(mondo, "SELECT count(*) FROM contacts WHERE id = %s", (c1,))[0][0] == 1, "il contatto resta"
    _codice(api.delete(f"/api/property/properties/{p['id']}/contacts/{c2}/owner"), 409, "LAST_OWNER_WITH_MANDATE")
    # un agente non assegnato non tocca i collegamenti
    _codice(mondo["api"]("agent_a").delete(f"/api/property/properties/{p['id']}/contacts/{c2}/owner"), 403, "NOT_ASSIGNED")


# ---------------------------------------------------------------------------
# F - property_lead seller operativo
# ---------------------------------------------------------------------------

def test_f01_il_collegamento_seller_di_un_lead_vivo_non_si_toglie(mondo):
    p = _immobile(mondo, "owner_a")
    cid = _contatto(mondo, "Fabio Blu")
    vivo, chiuso = _lead(mondo, cid, status="open"), _lead(mondo, cid, status="closed")
    _property_lead(mondo, p["id"], vivo, "seller")
    _property_lead(mondo, p["id"], chiuso, "seller")
    api = mondo["api"]("owner_a")
    corpo = _codice(api.delete(f"/api/property/properties/{p['id']}/leads/{vivo}"), 409, "SELLER_LINK_ACTIVE")
    assert "Venditori" in corpo["detail"]
    _codice(api.delete(f"/api/property/properties/{p['id']}/leads/{chiuso}"), 204)
    assert [tuple(r) for r in _q(mondo, "SELECT lead_id FROM property_leads WHERE property_id = %s", (p["id"],))] == [(vivo,)]


# ---------------------------------------------------------------------------
# G - figli: foto, documenti, visite, accessori
# ---------------------------------------------------------------------------

def _figli(m, pid):
    foto = _q(m, "INSERT INTO property_photos (property_id, url) VALUES (%s, 'https://x/f.jpg') RETURNING id", (pid,))[0][0]
    doc = _q(m, "INSERT INTO property_documents (property_id, document_type, title, url) VALUES (%s, 'visura', 'Visura', 'https://x/v.pdf') RETURNING id", (pid,))[0][0]
    vis = _q(m, "INSERT INTO property_visits (property_id, scheduled_at) VALUES (%s, NOW() + INTERVAL '3 days') RETURNING id", (pid,))[0][0]
    return foto, doc, vis


def test_g01_agente_solo_sull_immobile_assegnato(mondo):
    altrui = _immobile(mondo, "agent_a2")
    foto, doc, vis = _figli(mondo, altrui["id"])
    api = mondo["api"]("agent_a")
    _codice(api.delete(f"/api/property/photos/{foto}"), 403, "NOT_ASSIGNED")
    _codice(api.delete(f"/api/property/documents/{doc}"), 403, "NOT_ASSIGNED")
    _codice(api.delete(f"/api/property/visits/{vis}"), 403, "NOT_ASSIGNED")
    assert _q(mondo, "SELECT count(*) FROM property_photos WHERE id = %s", (foto,))[0][0] == 1
    _assegna(mondo, altrui["id"], mondo["ids"]["agent_a"])
    _codice(api.delete(f"/api/property/photos/{foto}"), 204)
    _codice(api.delete(f"/api/property/documents/{doc}"), 204)
    _codice(api.delete(f"/api/property/visits/{vis}"), 204)


def test_g02_documento_condiviso_e_visita_con_feedback_errore_leggibile_non_500(mondo):
    p = _immobile(mondo, "owner_a")
    _, doc, vis = _figli(mondo, p["id"])
    # una condivisione in BOZZA: la guardia precedente guardava solo le pubblicate,
    # la FK RESTRICT rispondeva 500
    _q(mondo, "INSERT INTO owner_shared_documents (property_document_id, public_title, public_document_type, status) "
              "VALUES (%s, 'Visura', 'visura', 'draft')", (doc,))
    _q(mondo, "INSERT INTO owner_visit_feedback_publications (property_visit_id, category, public_summary, status) "
              "VALUES (%s, 'general', 'Piaciuto', 'draft')", (vis,))
    api = mondo["api"]("owner_a")
    _codice(api.delete(f"/api/property/documents/{doc}"), 409, "DOCUMENT_SHARED")
    _codice(api.delete(f"/api/property/visits/{vis}"), 409, "VISIT_HAS_FEEDBACK")
    assert _q(mondo, "SELECT count(*) FROM property_documents WHERE id = %s", (doc,))[0][0] == 1


def test_g03_accessorio_di_un_unita_altrui_403(mondo):
    from tests.test_censimento_3_backend_postgres import _unita
    u = _unita(mondo, "owner_a")
    r = mondo["api"]("owner_a").post(f"/api/property/properties/{u['id']}/accessories", json={"kind": "cantina", "cadastral_status": "unknown"})
    assert r.status_code == 201, r.text
    acc = r.json()["id"]
    _codice(mondo["api"]("agent_a").delete(f"/api/property/properties/{u['id']}/accessories/{acc}"), 403, "NOT_ASSIGNED")
    _assegna(mondo, u["id"], mondo["ids"]["agent_a"])
    _codice(mondo["api"]("agent_a").delete(f"/api/property/properties/{u['id']}/accessories/{acc}"), 204)


# ---------------------------------------------------------------------------
# H - archiviati fuori dalle superfici operative
# ---------------------------------------------------------------------------

def _ids(r):
    assert r.status_code == 200, r.text
    return {x["id"] for x in r.json()["items"]}


def test_h01_lista_immobili_ricerca_e_selettori_escludono_gli_archiviati(mondo):
    p = _immobile(mondo, "owner_a", title="Archiviando H01")
    api = mondo["api"]("owner_a")
    _codice(api.post(f"/api/property/properties/{p['id']}/archive"), 200)
    assert p["id"] not in _ids(api.get("/api/property/properties?limit=200"))
    assert p["id"] not in _ids(api.get("/api/property/properties?record_kind=all&search=H01&limit=200"))
    assert p["id"] in _ids(api.get("/api/property/properties?status=archived&limit=200"))
    assert p["id"] in _ids(api.get("/api/property/properties?include_archived=true&limit=200"))
    # gli 8 archiviati storici della fixture (stato + archived_at): esclusi
    assert not ({i for i in range(10, 18)} & _ids(api.get("/api/property/properties?limit=200")))


def test_h02_relazioni_dashboard_venditori_e_contatti(mondo):
    p = _immobile(mondo, "owner_a")
    cid = _contatto(mondo, "Hilde Nera")
    _collega(mondo, p["id"], cid, "owner", True)
    lid = _lead(mondo, cid)
    _property_lead(mondo, p["id"], lid, "seller")
    api = mondo["api"]("owner_a")
    _codice(api.patch(f"/api/property/properties/{p['id']}", json={"commercial_status": "active", "asking_price": "150000"}), 200)
    prima = api.get("/api/property/dashboard").json()
    assert p["id"] in {s["property"]["id"] for s in api.get("/api/crm/sellers?limit=200").json()["items"]}
    _codice(api.post(f"/api/property/properties/{p['id']}/archive"), 200)
    dopo = api.get("/api/property/dashboard").json()
    assert dopo["active"] == prima["active"] - 1 and dopo["total"] == prima["total"] - 1
    assert float(dopo["active_value"]) == float(prima["active_value"]) - 150000
    assert p["id"] not in {s["property"]["id"] for s in api.get("/api/crm/sellers?limit=200").json()["items"]}
    # la relazione resta visibile nelle viste per contatto/lead (storia), con lo stato
    per_contatto = api.get(f"/api/property/properties?contact_id={cid}&record_kind=all&limit=200").json()["items"]
    assert [x["commercial_status"] for x in per_contatto if x["id"] == p["id"]] == ["archived"]
    assert p["id"] in _ids(api.get(f"/api/property/properties?lead_id={lid}&record_kind=all&limit=200"))
    # contatti archiviati fuori dalla lista di default
    _q(mondo, "UPDATE contacts SET status = 'archived', archived_at = NOW() WHERE id = %s", (cid,))
    assert cid not in _ids(api.get("/api/core/contacts?limit=200"))
    assert cid in _ids(api.get("/api/core/contacts?status=archived&limit=200"))


# ---------------------------------------------------------------------------
# I - multi-agenzia
# ---------------------------------------------------------------------------

def test_i01_un_altra_agenzia_non_archivia_ne_cancella(mondo):
    p = _immobile(mondo, "owner_a")
    b = mondo["api"]("owner_b")
    _codice(b.post(f"/api/property/properties/{p['id']}/archive"), 404)
    _codice(b.post(f"/api/property/properties/{p['id']}/unarchive"), 404)
    _codice(b.delete(f"/api/property/properties/{p['id']}"), 404)
    sa, sb = _stima(mondo, 1, "di A"), _stima(mondo, 2, "di B")
    r = mondo["admin"]("owner_a").post("/api/admin/stime/delete", json={"ids": [sa, sb]})
    assert r.status_code == 409 and r.json()["code"] == "NOT_PURGEABLE", r.text
    assert r.json()["blockers"] == [{"stima_id": sb, "blockers": [{"table": "stime", "code": "not_found", "count": 0,
                                                                   "label": "stima inesistente o di un'altra agenzia"}]}]
    assert _stime_esistono(mondo, sa, sb) == {sa, sb}, "niente a meta': nemmeno la propria"
    r = mondo["admin"]("owner_b").post("/api/admin/stime/delete", json={"ids": [sb]})
    assert r.status_code == 200 and _stime_esistono(mondo, sa, sb) == {sa}


# ---------------------------------------------------------------------------
# attivita' / task e richieste acquirente
# ---------------------------------------------------------------------------

def test_j01_un_agente_non_cancella_attivita_e_task_owner_solo_le_proprie(mondo):
    cid = _contatto(mondo, "Jole Bruni")
    uid_o, uid_a = mondo["ids"]["owner_a"], mondo["ids"]["agent_a"]
    att_agente = _q(mondo, "INSERT INTO activities (agency_id, activity_type, contact_id, created_by_user_id) VALUES (1, 'note', %s, %s) RETURNING id", (cid, uid_a))[0][0]
    att_owner = _q(mondo, "INSERT INTO activities (agency_id, activity_type, contact_id, created_by_user_id) VALUES (1, 'note', %s, %s) RETURNING id", (cid, uid_o))[0][0]
    task_agente = _q(mondo, "INSERT INTO tasks (agency_id, title, contact_id, created_by_user_id) VALUES (1, 'T', %s, %s) RETURNING id", (cid, uid_a))[0][0]
    task_owner = _q(mondo, "INSERT INTO tasks (agency_id, title, contact_id, created_by_user_id) VALUES (1, 'T', %s, %s) RETURNING id", (cid, uid_o))[0][0]
    agente, owner = mondo["api"]("agent_a"), mondo["api"]("owner_a")
    assert agente.delete(f"/api/core/activities/{att_agente}").status_code == 403
    assert agente.delete(f"/api/core/tasks/{task_agente}").status_code == 403
    assert owner.delete(f"/api/core/activities/{att_agente}").status_code == 403
    assert owner.delete(f"/api/core/tasks/{task_agente}").status_code == 403
    assert _q(mondo, "SELECT count(*) FROM activities")[0][0] == 2 and _q(mondo, "SELECT count(*) FROM tasks")[0][0] == 2
    assert owner.delete(f"/api/core/activities/{att_owner}").status_code == 204
    assert owner.delete(f"/api/core/tasks/{task_owner}").status_code == 204
    assert owner.delete("/api/core/activities/999999").status_code == 404


def test_j02_richiesta_acquirente_archivio_riservato_e_bloccato_dai_processi(mondo):
    cid = _contatto(mondo, "Kim Verde")
    br = _richiesta(mondo, cid)
    assert mondo["api"]("agent_a").delete(f"/api/buy/requests/{br}").status_code == 403
    p = _immobile(mondo, "owner_a")
    mt = _q(mondo, "INSERT INTO matches (buy_request_id, property_id, compatibility_status, score_total, match_class, algorithm_version) "
                   "VALUES (%s, %s, 'compatible', 80, 'good', 'test') RETURNING id", (br, p["id"]))[0][0]
    pp = _q(mondo, "INSERT INTO property_proposals (match_id, amount, expires_at, idempotency_key, created_by, status) "
                   "VALUES (%s, 100000, NOW() + INTERVAL '30 days', %s, 'test', 'submitted') RETURNING id", (mt, str(uuid.uuid4())))[0][0]
    owner = mondo["api"]("owner_a")
    r = owner.delete(f"/api/buy/requests/{br}")
    assert r.status_code == 409 and r.json()["code"] == "ARCHIVE_BLOCKED", r.text
    assert {b["code"] for b in r.json()["blockers"]} == {"open_proposal"}
    assert tuple(_q(mondo, "SELECT status, archived_at FROM buy_requests WHERE id = %s", (br,))[0]) == ("active", None)
    # la PATCH di stato della scheda Acquirente E' l'azione Archivia: stessi
    # blocchi, stesso ruolo, `archived_at` scritto insieme allo stato
    r = owner.patch(f"/api/buy/requests/{br}", json={"status": "archived"})
    assert r.status_code == 409 and r.json()["code"] == "ARCHIVE_BLOCKED", r.text
    assert mondo["api"]("agent_a").patch(f"/api/buy/requests/{br}", json={"status": "archived"}).status_code == 403
    assert owner.patch(f"/api/buy/requests/{br}", json={"archived_at": None}).status_code == 400
    _q(mondo, "UPDATE property_proposals SET status = 'rejected' WHERE id = %s", (pp,))
    r = owner.patch(f"/api/buy/requests/{br}", json={"status": "archived"})
    assert r.status_code == 200 and r.json()["status"] == "archived" and r.json()["archived_at"], r.text
    assert _q(mondo, "SELECT archived_at IS NOT NULL FROM buy_requests WHERE id = %s", (br,))[0][0] is True
    assert owner.delete(f"/api/buy/requests/{br}").status_code == 409      # gia' archiviata
    # riattivazione: solo owner/admin, e azzera archived_at
    assert mondo["api"]("agent_a").patch(f"/api/buy/requests/{br}", json={"status": "active"}).status_code == 403
    assert owner.patch(f"/api/buy/requests/{br}", json={"status": "active"}).status_code == 200
    assert tuple(_q(mondo, "SELECT status, archived_at FROM buy_requests WHERE id = %s", (br,))[0]) == ("active", None)


# ---------------------------------------------------------------------------
# REVIEW 2 - nessun bypass di /archive e /unarchive via PATCH
# ---------------------------------------------------------------------------

def _stato(m, pid):
    return tuple(_q(m, "SELECT commercial_status, archived_at FROM properties WHERE id = %s", (pid,))[0])


def _storico(m, pid):
    return _q(m, "SELECT count(*) FROM property_status_history WHERE property_id = %s", (pid,))[0][0]


def test_k01_patch_verso_archived_rifiutata_senza_scritture(mondo):
    """La PATCH generica non archivia: niente ARCHIVE_BLOCKED saltato, niente
    `archived` con `archived_at` NULL. Rifiuto leggibile che indica /archive,
    zero scritture (stato, archived_at, storico)."""
    p = _immobile(mondo, "owner_a")
    cid = _contatto(mondo, "Kevin Blocco")
    _collega(mondo, p["id"], cid, "owner", True)
    _acquisizione(mondo, p["id"], cid)            # con l'endpoint sarebbe ARCHIVE_BLOCKED
    prima, storico = _stato(mondo, p["id"]), _storico(mondo, p["id"])
    for chi in ("owner_a", "admin_a"):
        r = mondo["api"](chi).patch(f"/api/property/properties/{p['id']}", json={"commercial_status": "archived"})
        corpo = _codice(r, 409, "USE_ARCHIVE_ACTION")
        assert "/archive" in corpo["detail"]
    # anche insieme ad altri campi: nessuna scrittura parziale
    r = mondo["api"]().patch(f"/api/property/properties/{p['id']}", json={"commercial_status": "archived", "title": "Nuovo"})
    _codice(r, 409, "USE_ARCHIVE_ACTION")
    assert _stato(mondo, p["id"]) == prima and prima[1] is None
    assert _storico(mondo, p["id"]) == storico
    assert _q(mondo, "SELECT title FROM properties WHERE id = %s", (p["id"],))[0][0] != "Nuovo"


def test_k02_patch_di_stato_su_archiviato_rifiutata_e_riattiva_resta_la_via(mondo):
    p = _immobile(mondo, "owner_a")
    api = mondo["api"]("owner_a")
    _codice(api.post(f"/api/property/properties/{p['id']}/archive"), 200)
    prima, storico = _stato(mondo, p["id"]), _storico(mondo, p["id"])
    for stato in ("active", "draft", "withdrawn"):
        corpo = _codice(api.patch(f"/api/property/properties/{p['id']}", json={"commercial_status": stato}),
                        409, "USE_UNARCHIVE_ACTION")
        assert "/unarchive" in corpo["detail"]
    assert _stato(mondo, p["id"]) == prima and prima[0] == "archived" and prima[1] is not None
    assert _storico(mondo, p["id"]) == storico
    # un `archived` invariato (property_admin rimanda lo stato) e' un no-op:
    # gli altri campi si salvano, lo stato resta coerente
    r = api.patch(f"/api/property/properties/{p['id']}", json={"commercial_status": "archived", "internal_notes": "ok"})
    assert r.status_code == 200, r.text
    assert _stato(mondo, p["id"]) == prima and _storico(mondo, p["id"]) == storico
    # zombie D8 (archived_at con stato operativo): anche qui solo /unarchive
    z = _immobile(mondo, "owner_a")
    _q(mondo, "UPDATE properties SET commercial_status = 'active', archived_at = NOW() WHERE id = %s", (z["id"],))
    _codice(api.patch(f"/api/property/properties/{z['id']}", json={"commercial_status": "draft"}), 409, "USE_UNARCHIVE_ACTION")
    esito = _codice(api.post(f"/api/property/properties/{z['id']}/unarchive"), 200)
    assert esito["archived_at"] is None
    # e la via ufficiale funziona
    esito = _codice(api.post(f"/api/property/properties/{p['id']}/unarchive"), 200)
    assert esito["commercial_status"] == "draft" and esito["archived_at"] is None


def test_k03_la_regola_tiene_anche_sotto_lock_contro_una_archiviazione_concorrente(mondo, monkeypatch):
    """Il service legge lo stato senza lock; il repository ricontrolla sulla
    riga `FOR UPDATE`. Simulazione: il service vede l'immobile operativo, ma
    nel frattempo e' stato archiviato -> la PATCH di stato e' comunque rifiutata."""
    from property import repository, service
    p = _immobile(mondo, "owner_a")
    _codice(mondo["api"]().post(f"/api/property/properties/{p['id']}/archive"), 200)
    vero = repository.get_property
    monkeypatch.setattr(service.repository, "get_property",
                        lambda ctx, i: {**vero(ctx, i), "commercial_status": "draft", "archived_at": None})
    _codice(mondo["api"]().patch(f"/api/property/properties/{p['id']}", json={"commercial_status": "active"}),
            409, "USE_UNARCHIVE_ACTION")
    stato = _stato(mondo, p["id"])
    assert stato[0] == "archived" and stato[1] is not None


# ---------------------------------------------------------------------------
# REVIEW 2 - hard delete stime: nessuna relazione si infila fra controllo e DELETE
# ---------------------------------------------------------------------------

def _rivale():
    from core import database as core_database
    conn = core_database.get_connection()
    with conn.cursor() as cur:
        cur.execute("SET application_name = 'delete_arch_rivale'")
    conn.commit()
    return conn


def _in_attesa(m, nome, timeout=10.0):
    import time
    fine = time.monotonic() + timeout
    while time.monotonic() < fine:
        righe = _q(m, "SELECT wait_event_type FROM pg_stat_activity WHERE application_name = %s", (nome,))
        if righe and righe[0][0] == "Lock":
            return True
        time.sleep(0.05)
    return False


def _purge_in_thread(m, ids):
    import threading
    esito = {}

    def lavoro():
        esito["r"] = m["admin"]("owner_a").post("/api/admin/stime/delete", json={"ids": ids})
    t = threading.Thread(target=lavoro)
    t.start()
    return t, esito


@pytest.mark.parametrize("riferimento", ["lead_stime", "appointment"])
def test_z01_una_relazione_in_volo_viene_attesa_e_blocca_il_purge(mondo, riferimento):
    """Il rivale scrive il riferimento e NON fa commit; il purge parte e deve
    aspettarlo (lock), poi vederlo e rifiutare. Per `lead_stime` decide il lock
    di riga (FK -> FOR KEY SHARE vs FOR UPDATE); per `appointments` (nessuna FK)
    il lock di tabella SHARE."""
    sid = _stima(mondo)
    rivale = _rivale()
    try:
        with rivale.cursor() as cur:
            if riferimento == "lead_stime":
                lid = _lead(mondo, _contatto(mondo, "Zeno Lead"))
                cur.execute("INSERT INTO lead_stime (lead_id, stima_id) VALUES (%s, %s)", (lid, sid))
            else:
                cur.execute("INSERT INTO appointments (agency_id, assigned_user_id, appointment_type, status, start_at, end_at, stima_id) "
                            "VALUES (1, %s, 'call', 'scheduled', %s, %s, %s)",
                            (mondo["ids"]["owner_a"], _futuro(40, 9), _futuro(40, 10), sid))
        t, esito = _purge_in_thread(mondo, [sid])
        import time
        fine = time.monotonic() + 10
        while time.monotonic() < fine and not _q(mondo, "SELECT 1 FROM pg_stat_activity WHERE wait_event_type = 'Lock' "
                                                    "AND query ILIKE %s", ("%stime%" if riferimento == "lead_stime" else "%LOCK TABLE appointments%",)):
            time.sleep(0.05)
        assert "r" not in esito, "il purge non ha aspettato la scrittura in volo"
        rivale.commit()
        t.join(30)
        r = esito["r"]
        assert r.status_code == 409 and r.json()["code"] == "NOT_PURGEABLE", r.text
        tabelle = {b["table"] for b in r.json()["blockers"][0]["blockers"]}
        assert ("lead_stime" if riferimento == "lead_stime" else "appointments") in tabelle
        assert _stime_esistono(mondo, sid) == {sid}
    finally:
        rivale.rollback()
        rivale.close()


@pytest.mark.parametrize("riferimento", ["lead_stime", "appointment"])
def test_z02_durante_il_purge_una_nuova_relazione_non_entra_nella_finestra(mondo, monkeypatch, riferimento):
    """Il purge ha fatto il controllo (nessun blocco) e sta per cancellare: in
    quel momento il rivale prova a scrivere il riferimento. Deve restare in
    attesa fino al commit del purge - dentro la finestra non entra nulla - e
    poi: per `lead_stime` fallisce sulla FK (la stima non c'e' piu'), per
    `appointments` si scrive DOPO (riferimento morbido, nessuna cascata l'ha
    toccato). In entrambi i casi il purge non cancella ne' altera storico."""
    import threading
    import stime_purge
    from psycopg2 import errors as pg_errors
    sid = _stima(mondo)
    lid = _lead(mondo, _contatto(mondo, "Zoe Lead"))
    rivale = _rivale()
    stato = {}

    def scrivi():
        try:
            with rivale.cursor() as cur:
                if riferimento == "lead_stime":
                    cur.execute("INSERT INTO lead_stime (lead_id, stima_id) VALUES (%s, %s)", (lid, sid))
                else:
                    cur.execute("INSERT INTO appointments (agency_id, assigned_user_id, appointment_type, status, start_at, end_at, stima_id) "
                                "VALUES (1, %s, 'call', 'scheduled', %s, %s, %s) RETURNING id",
                                (mondo["ids"]["owner_a"], _futuro(41, 9), _futuro(41, 10), sid))
                    stato["appuntamento"] = cur.fetchone()[0]
            rivale.commit()
            stato["esito"] = "scritto"
        except pg_errors.ForeignKeyViolation:
            rivale.rollback()
            stato["esito"] = "fk"

    vero = stime_purge.blockers_for

    def controllo_poi_rivale(cur, agency_id, ids):
        blocchi = vero(cur, agency_id, ids)
        stato["thread"] = threading.Thread(target=scrivi)
        stato["thread"].start()
        stato["in_attesa"] = _in_attesa(mondo, "delete_arch_rivale")
        stato["durante"] = "esito" in stato
        return blocchi

    monkeypatch.setattr(stime_purge, "blockers_for", controllo_poi_rivale)
    try:
        r = mondo["admin"]("owner_a").post("/api/admin/stime/delete", json={"ids": [sid]})
        stato["thread"].join(30)
        assert r.status_code == 200 and r.json()["deleted"] == 1, r.text
        assert stato["in_attesa"] is True and stato["durante"] is False, stato
        assert _stime_esistono(mondo, sid) == set()
        if riferimento == "lead_stime":
            assert stato["esito"] == "fk"
            assert _q(mondo, "SELECT count(*) FROM lead_stime WHERE lead_id = %s", (lid,))[0][0] == 0
        else:
            assert stato["esito"] == "scritto"
            assert _q(mondo, "SELECT stima_id FROM appointments WHERE id = %s", (stato["appuntamento"],))[0][0] == sid
    finally:
        rivale.rollback()
        rivale.close()


# ---------------------------------------------------------------------------
# FINAL FIX - i writer di `appointments.stima_id` (nessuna FK) e il purge
# ---------------------------------------------------------------------------

def _ctx_owner(m):
    from operator_auth.context import OperatorContext
    return OperatorContext(user_id=m["ids"]["owner_a"], agency_id=1, role="agency_owner",
                           is_platform_admin=False, session_id=None, auth_channel="operator_session")


def _corpo_appuntamento(m, sid, giorni):
    from appointments.schemas import AppointmentCreate
    return AppointmentCreate(appointment_type="call", status="scheduled", start_at=_futuro(giorni, 11),
                             end_at=_futuro(giorni, 12), assigned_user_id=m["ids"]["owner_a"], stima_id=sid)


def _appuntamenti_della_stima(m, sid):
    return _q(m, "SELECT count(*) FROM appointments WHERE stima_id = %s", (sid,))[0][0]


def test_w01_writer_crm_prima_del_purge_il_purge_aspetta_e_risponde_409(mondo, monkeypatch):
    """`appointments.service.create_appointment` valida la stima con
    `stima_agency` (FOR KEY SHARE) e inserisce; la transazione resta aperta.
    Il purge deve aspettare e, al commit del writer, vedere l'appuntamento."""
    import threading
    from appointments import service as agenda
    sid = _stima(mondo)
    inserito, rilascia = threading.Event(), threading.Event()
    vero = agenda.repository.insert_appointment

    def inserisci_e_trattieni(cur, valori, **kw):
        riga = vero(cur, valori, **kw)
        inserito.set()
        rilascia.wait(30)
        return riga

    monkeypatch.setattr(agenda.repository, "insert_appointment", inserisci_e_trattieni)
    esito = {}
    w = threading.Thread(target=lambda: esito.setdefault("w", agenda.create_appointment(_ctx_owner(mondo), _corpo_appuntamento(mondo, sid, 50))))
    w.start()
    assert inserito.wait(15)
    t, purge = _purge_in_thread(mondo, [sid])
    import time
    fine = time.monotonic() + 10
    while time.monotonic() < fine and not _q(mondo, "SELECT 1 FROM pg_stat_activity WHERE wait_event_type = 'Lock' AND query ILIKE %s",
                                                ("%FROM stime WHERE id = ANY%",)):
        time.sleep(0.05)
    assert "r" not in purge, "il purge non ha aspettato il writer"
    rilascia.set()
    w.join(30)
    t.join(30)
    assert esito["w"]["stima_id"] == sid
    r = purge["r"]
    assert r.status_code == 409 and r.json()["code"] == "NOT_PURGEABLE", r.text
    assert "appointments" in {b["table"] for b in r.json()["blockers"][0]["blockers"]}
    assert _stime_esistono(mondo, sid) == {sid} and _appuntamenti_della_stima(mondo, sid) == 1


def test_w02_purge_prima_del_writer_crm_il_writer_aspetta_e_fallisce_senza_orfani(mondo, monkeypatch):
    import threading
    import stime_purge
    from appointments import service as agenda
    from core.exceptions import NotFoundError
    sid = _stima(mondo)
    stato = {}

    def writer():
        try:
            stato["riga"] = agenda.create_appointment(_ctx_owner(mondo), _corpo_appuntamento(mondo, sid, 51))
        except NotFoundError as exc:
            stato["errore"] = str(exc)

    vero = stime_purge.blockers_for

    def controllo_poi_writer(cur, agency_id, ids):
        blocchi = vero(cur, agency_id, ids)
        stato["thread"] = threading.Thread(target=writer)
        stato["thread"].start()
        import time
        fine = time.monotonic() + 10
        while time.monotonic() < fine and not _q(mondo, "SELECT 1 FROM pg_stat_activity WHERE wait_event_type = 'Lock' "
                                                    "AND query ILIKE %s", ("%FROM stime WHERE id = %FOR KEY SHARE%",)):
            time.sleep(0.05)
        stato["in_attesa"] = bool(_q(mondo, "SELECT 1 FROM pg_stat_activity WHERE wait_event_type = 'Lock' "
                                            "AND query ILIKE %s", ("%FOR KEY SHARE%",)))
        stato["durante"] = "riga" in stato or "errore" in stato
        return blocchi

    monkeypatch.setattr(stime_purge, "blockers_for", controllo_poi_writer)
    r = mondo["admin"]("owner_a").post("/api/admin/stime/delete", json={"ids": [sid]})
    stato["thread"].join(30)
    assert r.status_code == 200 and r.json()["deleted"] == 1, r.text
    assert stato["in_attesa"] is True and stato["durante"] is False, stato
    assert stato.get("errore") == "Stima non trovata" and "riga" not in stato, stato
    assert _stime_esistono(mondo, sid) == set() and _appuntamenti_della_stima(mondo, sid) == 0


def _legacy(m, sid):
    _q(m, "UPDATE stime_dettagliate SET sopralluogo = %s WHERE stima_id = %s",
       (_futuro(52, 10).replace(tzinfo=None), sid))


def test_w03_import_legacy_prima_del_purge_il_purge_aspetta_e_risponde_409(mondo):
    """Il secondo writer reale con `stima_id` nuovo: l'import A30-6/A30-7
    (`appointments_legacy`). Inserisce nella transazione del chiamante; il
    purge aspetta e, al commit, vede l'appuntamento."""
    from psycopg2.extras import RealDictCursor
    import appointments_legacy.stime_dettagliate_import as legacy
    sid = _stima(mondo)
    _legacy(mondo, sid)
    rivale = _rivale()
    try:
        with rivale.cursor(cursor_factory=RealDictCursor) as cur:
            esito = legacy.run_import(cur, apply=True, agency_id=1)
        assert esito["inserted"] == 1, esito
        t, purge = _purge_in_thread(mondo, [sid])
        import time
        fine = time.monotonic() + 10
        while time.monotonic() < fine and not _q(mondo, "SELECT 1 FROM pg_stat_activity WHERE wait_event_type = 'Lock' AND query ILIKE %s",
                                                    ("%FROM stime WHERE id = ANY%",)):
            time.sleep(0.05)
        assert "r" not in purge
        rivale.commit()
        t.join(30)
        r = purge["r"]
        assert r.status_code == 409 and r.json()["code"] == "NOT_PURGEABLE", r.text
        assert _stime_esistono(mondo, sid) == {sid} and _appuntamenti_della_stima(mondo, sid) == 1
    finally:
        rivale.rollback()
        rivale.close()


def test_w04_purge_prima_dell_import_l_import_aspetta_e_conta_l_orfano(mondo, monkeypatch):
    import threading
    import stime_purge
    from psycopg2.extras import RealDictCursor
    import appointments_legacy.stime_dettagliate_import as legacy
    sid = _stima(mondo)
    _legacy(mondo, sid)
    rivale = _rivale()
    stato = {}

    def importa():
        with rivale.cursor(cursor_factory=RealDictCursor) as cur:
            stato["esito"] = legacy.run_import(cur, apply=True, agency_id=1)
        rivale.commit()

    vero = stime_purge.blockers_for

    def controllo_poi_import(cur, agency_id, ids):
        blocchi = vero(cur, agency_id, ids)
        stato["thread"] = threading.Thread(target=importa)
        stato["thread"].start()
        stato["in_attesa"] = _in_attesa(mondo, "delete_arch_rivale")
        stato["durante"] = "esito" in stato
        return blocchi

    monkeypatch.setattr(stime_purge, "blockers_for", controllo_poi_import)
    try:
        r = mondo["admin"]("owner_a").post("/api/admin/stime/delete", json={"ids": [sid]})
        stato["thread"].join(30)
        assert r.status_code == 200 and r.json()["deleted"] == 1, r.text
        assert stato["in_attesa"] is True and stato["durante"] is False, stato
        assert stato["esito"]["inserted"] == 0 and stato["esito"]["orphan"] == 1, stato["esito"]
        assert _stime_esistono(mondo, sid) == set() and _appuntamenti_della_stima(mondo, sid) == 0
    finally:
        rivale.rollback()
        rivale.close()


def test_w05_lock_timeout_del_purge_vale_solo_per_la_sua_transazione(mondo):
    """`SET LOCAL`: finita la transazione (commit o rollback), la connessione
    torna al valore di sessione."""
    import stime_purge
    from core import database as core_database
    for sid, atteso in ((_stima(mondo), 1), (None, None)):
        conn = core_database.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SHOW lock_timeout")
                prima = cur.fetchone()[0]
            conn.commit()
            if sid is not None:
                assert stime_purge.purge_stime(conn, _ctx_owner(mondo), 1, [sid]) == atteso
            else:
                with pytest.raises(stime_purge.StimeNotPurgeable):
                    stime_purge.purge_stime(conn, _ctx_owner(mondo), 1, [999999])
            with conn.cursor() as cur:
                cur.execute("SHOW lock_timeout")
                assert cur.fetchone()[0] == prima
                cur.execute("SELECT setting FROM pg_settings WHERE name = 'lock_timeout'")
                assert cur.fetchone()[0] == "0"
            conn.rollback()
        finally:
            conn.close()
