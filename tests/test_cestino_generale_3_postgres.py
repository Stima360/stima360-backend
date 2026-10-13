"""CESTINO-GENERALE-3 (CG-3) - cancellazione definitiva controllata di
immobili, contatti e richieste acquirente nel Cestino, su PostgreSQL VERO
(schema completo dal runner; database locale usa-e-getta). Rotte VERE della
piattaforma (`/api/platform/trash/{kind}/{id}/purge-check|purge`).

  p01  immobile semplice: riepilogo, conferma, la riga sparisce con i soli
       contenuti propri; evento `purge` con la copia; audit di piattaforma;
       gli eventi del Cestino restano;
  p02  immobile: foto, documenti, prezzi bloccano; nulla cambia;
  p03  immobile: incarico e vendita bloccano (iniettati: il Cestino stesso
       non li ammette); unita' di un edificio nel Cestino: solo con l'edificio;
  c01  contatto senza dipendenze: cancellato, pausa automazioni e ruoli con
       lui; nel registro nessun dato personale;
  c02  contatto con consensi: blocco «da conservare» (anonimizzazione);
  c03  contatto proprietario di un immobile, o con un lead: blocco;
  b01  richiesta acquirente eleggibile: criteri e storico di creazione con lei
       (storico copiato); il contatto resta;
  b02  richiesta con una nota o con un abbinamento con seguito: blocco;
  s01  permessi: titolare, admin, agente, altro platform admin, email
       designata senza designazione, account disattivato -> 403; designato ok;
  s02  non nel Cestino, testo sbagliato, impronta vecchia, richiesta ripetuta;
  s03  errore durante la transazione (audit non scrivibile): rollback COMPLETO;
  s04  comando STIMA Voice in corso: blocco;
  s05  STIMA Voice non conosce la cancellazione definitiva; nessuna DELETE HTTP.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from tests.test_censimento_3_backend_postgres import DSN, _q, _unita, completo, mondo  # noqa: F401
from tests.test_eliminazione_edificio_1_postgres import _approvato, _codice, _edificio, banco  # noqa: F401

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

ROOT = Path(__file__).resolve().parents[1]
CONFERMA = "ELIMINA DEFINITIVAMENTE"


def _ctx(m, chi):
    from operator_auth.context import OperatorContext
    uid, agenzia, ruolo = m["ruoli"][chi]
    return OperatorContext(user_id=uid, agency_id=agenzia, role=ruolo, is_platform_admin=False, session_id=None,
                           auth_channel="operator_session")


def _controllo(m, kind, eid, atteso=200):
    return _codice(m["sa"].get(f"/api/platform/trash/{kind}/{eid}/purge-check"), atteso)


def _purge(m, kind, eid, impronta, testo=CONFERMA):
    return m["sa"].post(f"/api/platform/trash/{kind}/{eid}/purge", json={"confirm_text": testo, "fingerprint": impronta})


def _esiste(m, tabella, eid):
    return bool(_q(m, f"SELECT count(*) FROM {tabella} WHERE id = %s", (eid,))[0][0])


def _eventi(m, kind, eid):
    return [r[0] for r in _q(m, "SELECT action FROM record_lifecycle_events WHERE entity_type = %s AND entity_id = %s "
                                "ORDER BY id", (kind, eid))]


def _audit(m, kind, eid):
    return _q(m, "SELECT result FROM platform_audit_log WHERE action = 'platform.trash.purge' AND target_type = %s "
                 "AND target_id = %s", (kind, str(eid)))


def _immobile_nel_cestino(m, **kw):
    from property import lifecycle
    p = _unita(m, floor="1", internal_number=f"P{uuid.uuid4().hex[:5]}", **kw)
    return p, lambda: lifecycle.trash_property(_ctx(m, "owner_a"), p["id"], "test_record")


def _contatto(m, **kw):
    sfx = uuid.uuid4().hex[:6]
    return _q(m, "INSERT INTO contacts (agency_id, display_name, first_name, last_name, email, phone) "
                 "VALUES (1, %s, 'Gino', %s, %s, '+39 333 1234567') RETURNING id",
              (f"Gino Purga {sfx}", f"Purga{sfx}", f"gino.{sfx}@x.test"))[0][0]


def _cestina_contatto(m, cid):
    from core import contact_lifecycle
    contact_lifecycle.trash_contact(_ctx(m, "owner_a"), cid, "invalid_data")


def _richiesta(m):
    c = _q(m, "INSERT INTO contacts (agency_id, display_name) VALUES (1, %s) RETURNING id",
           (f"Bruna {uuid.uuid4().hex[:5]}",))[0][0]
    r = _q(m, "INSERT INTO buy_requests (agency_id, contact_id, title) VALUES (1, %s, 'Trilocale al mare') RETURNING id",
           (c,))[0][0]
    _q(m, "INSERT INTO buy_request_history (buy_request_id, event_type, description) "
          "VALUES (%s, 'request_created', 'Richiesta BUY creata')", (r,))
    _q(m, "INSERT INTO buy_request_locations (buy_request_id, location_type, municipality) "
          "VALUES (%s, 'municipality', 'Giulianova')", (r,))
    return c, r


def _abbinamento(m, rid, pid):
    return _q(m, "INSERT INTO matches (buy_request_id, property_id, compatibility_status, score_total, match_class, "
                 "algorithm_version) VALUES (%s, %s, 'compatible', 80, 'good', 'test') RETURNING id", (rid, pid))[0][0]


def _cestina_richiesta(m, rid):
    from buy import lifecycle as buy_lifecycle
    buy_lifecycle.trash_request(_ctx(m, "owner_a"), rid, "duplicate")


# ---------------------------------------------------------------------------
# immobili
# ---------------------------------------------------------------------------

def test_p01_immobile_semplice(banco):
    m = banco
    p, cestina = _immobile_nel_cestino(m)
    pid = p["id"]
    _q(m, "INSERT INTO property_contacts (property_id, contact_id, role) VALUES (%s, %s, 'owner')", (pid, m["mario"]))
    cestina()
    c = _controllo(m, "property", pid)
    assert c["eligible"] is True and c["blockers"] == [] and c["in_trash"] is True
    assert c["confirm_text"] == CONFERMA and len(c["fingerprint"]) == 64
    assert {x["table"] for x in c["will_delete"]} >= {"property_contacts"}
    assert "OWNER_LINKS" in {x["code"] for x in c["preserved"]}
    esito = _codice(_purge(m, "property", pid, c["fingerprint"]), 200)
    assert esito["purged"] is True
    assert not _esiste(m, "properties", pid)
    assert _q(m, "SELECT count(*) FROM property_contacts WHERE property_id = %s", (pid,))[0][0] == 0
    assert _esiste(m, "contacts", m["mario"])                                     # il contatto resta
    assert _eventi(m, "property", pid) == ["trash", "purge"]                      # lo storico del Cestino resta
    copia = _q(m, "SELECT before_state FROM record_lifecycle_events WHERE entity_type = 'property' "
                  "AND entity_id = %s AND action = 'purge'", (pid,))[0][0]
    assert copia["copied"]["owner_links"][0]["contact_id"] == m["mario"]
    assert _audit(m, "property", pid) == [["success"]]


def test_p02_foto_documenti_prezzi_bloccano(banco):
    m = banco
    for inserisci, codice in (
            ("INSERT INTO property_photos (property_id, url) VALUES (%s, 'https://x.test/f.jpg')", "FK_property_photos"),
            ("INSERT INTO property_documents (property_id, document_type, title, url) "
             "VALUES (%s, 'visura', 'Visura', 'https://x.test/v.pdf')", "FK_property_documents"),
            ("INSERT INTO property_price_history (property_id, old_price, new_price) VALUES (%s, 100, 200)",
             "FK_property_price_history")):
        p, cestina = _immobile_nel_cestino(m)
        _q(m, inserisci, (p["id"],))
        cestina()
        c = _controllo(m, "property", p["id"])
        assert c["eligible"] is False and codice in {b["code"] for b in c["blockers"]}, c["blockers"]
        r = _codice(_purge(m, "property", p["id"], c["fingerprint"]), 409)
        assert r["code"] == "NOT_PURGEABLE" and codice in {b["code"] for b in r["blockers"]}
        assert _esiste(m, "properties", p["id"]) and _eventi(m, "property", p["id"]) == ["trash"]


def test_p03_incarico_vendita_e_unita_di_edificio(banco):
    m = banco
    # incarico: il Cestino non lo ammette; lo si inietta per provare la difesa della cancellazione
    p, cestina = _immobile_nel_cestino(m)
    cestina()
    _q(m, "ALTER TABLE properties DISABLE TRIGGER USER")
    try:
        _q(m, "UPDATE properties SET mandate_type = 'exclusive' WHERE id = %s", (p["id"],))
    finally:
        _q(m, "ALTER TABLE properties ENABLE TRIGGER USER")
    try:
        c = _controllo(m, "property", p["id"])
        assert "MANDATE" in {b["code"] for b in c["blockers"]}, c["blockers"]
        assert _codice(_purge(m, "property", p["id"], c["fingerprint"]), 409)["code"] == "NOT_PURGEABLE"
        assert _esiste(m, "properties", p["id"])
    finally:                                         # la base condivisa del modulo torna coerente
        _q(m, "ALTER TABLE properties DISABLE TRIGGER USER")
        try:
            _q(m, "UPDATE properties SET mandate_type = NULL WHERE id = %s", (p["id"],))
        finally:
            _q(m, "ALTER TABLE properties ENABLE TRIGGER USER")
    # vendita registrata
    q, cestina2 = _immobile_nel_cestino(m)
    cestina2()
    # (riga iniettata senza catena proposta/richiesta: replica role solo per l'INSERT e la pulizia)
    vid = _q(m, "SET session_replication_role = replica; "
                "INSERT INTO property_sales (property_id, buy_request_id, proposal_id, sale_price, status, "
                "idempotency_key, created_by) VALUES (%s, -1, -1, 100000, 'completed', %s, 'test') RETURNING id",
             (q["id"], str(uuid.uuid4())))[0][0]
    _q(m, "RESET session_replication_role")
    try:
        c = _controllo(m, "property", q["id"])
        assert "FK_property_sales" in {b["code"] for b in c["blockers"]}, c["blockers"]
        assert _codice(_purge(m, "property", q["id"], c["fingerprint"]), 409)["code"] == "NOT_PURGEABLE"
        assert _esiste(m, "properties", q["id"])
    finally:
        _q(m, "SET session_replication_role = replica; DELETE FROM property_sales WHERE id = %s", (vid,))
        _q(m, "RESET session_replication_role")
    # unita' di un edificio nel Cestino: solo con l'edificio
    e = _edificio(m)
    u = _unita(m, building_id=e["id"], floor="2", internal_number="7")
    _approvato(m, e["id"])
    c = _controllo(m, "property", u["id"])
    assert c["blockers"][0]["code"] == "BUILDING_IN_TRASH" and c["blockers"][0]["href"] == "#/cestino/richieste-edifici"
    assert _codice(_purge(m, "property", u["id"], c["fingerprint"]), 409)["code"] == "NOT_PURGEABLE"
    assert _esiste(m, "properties", u["id"]) and _esiste(m, "buildings", e["id"])


# ---------------------------------------------------------------------------
# contatti
# ---------------------------------------------------------------------------

def test_c01_contatto_senza_dipendenze(banco):
    m = banco
    cid = _contatto(m)
    _q(m, "INSERT INTO contact_roles (contact_id, role) VALUES (%s, 'prospect')", (cid,))
    _cestina_contatto(m, cid)
    c = _controllo(m, "contact", cid)
    assert c["eligible"] is True, c["blockers"]
    assert {"contact_roles", "seller_timeline_events"} <= {x["table"] for x in c["will_delete"]}
    traccia = [r[0] for r in _q(m, "SELECT id FROM seller_timeline_events WHERE contact_id = %s", (cid,))]
    assert len(traccia) == 1                                                      # la pausa scritta dal Cestino
    _codice(_purge(m, "contact", cid, c["fingerprint"]), 200)
    # cancellata davvero (la FK e' SET NULL: niente righe orfane rimaste senza contatto)
    assert _q(m, "SELECT count(*) FROM seller_timeline_events WHERE id = ANY(%s)", (traccia,))[0][0] == 0
    assert not _esiste(m, "contacts", cid)
    assert _q(m, "SELECT count(*) FROM contact_roles WHERE contact_id = %s", (cid,))[0][0] == 0
    assert _eventi(m, "contact", cid) == ["trash", "purge"]
    evento = _q(m, "SELECT before_state, metadata FROM record_lifecycle_events WHERE entity_type = 'contact' "
                   "AND entity_id = %s AND action = 'purge'", (cid,))[0]
    testo = json.dumps(evento[0]) + json.dumps(evento[1])
    for dato in ("Gino", "Purga", "@x.test", "333"):                              # nessun dato personale nel registro
        assert dato not in testo, dato
    assert evento[0]["copied"]["roles"] == ["prospect"]
    assert [x["event_type"] for x in evento[0]["copied"]["automation_trail"]] == ["automation_paused"]
    assert _q(m, "SELECT count(*) FROM seller_timeline_events WHERE contact_id = %s", (cid,))[0][0] == 0
    assert _audit(m, "contact", cid) == [["success"]]


def test_c02_consensi_da_conservare(banco):
    m = banco
    cid = _contatto(m)
    _q(m, "INSERT INTO consent_events (agency_id, contact_id, purpose, decision, decided_at, source, actor_type) "
          "VALUES (1, %s, 'privacy_terms', 'granted', NOW(), 'test', 'subject')", (cid,))
    _cestina_contatto(m, cid)
    c = _controllo(m, "contact", cid)
    codici = [b["code"] for b in c["blockers"]]
    assert codici[0] == "LEGAL_RECORDS" and "FK_consent_events" in codici
    assert "anonimizzazione" in c["blockers"][0]["label"]
    assert _codice(_purge(m, "contact", cid, c["fingerprint"]), 409)["code"] == "NOT_PURGEABLE"
    assert _esiste(m, "contacts", cid)
    assert _q(m, "SELECT count(*) FROM consent_events WHERE contact_id = %s", (cid,))[0][0] == 1


def test_c03_proprietario_o_lead_bloccano(banco):
    m = banco
    cid = _contatto(m)
    p = _unita(m, floor="3", internal_number=f"C{uuid.uuid4().hex[:4]}")
    _q(m, "INSERT INTO property_contacts (property_id, contact_id, role) VALUES (%s, %s, 'owner')", (p["id"], cid))
    _cestina_contatto(m, cid)
    c = _controllo(m, "contact", cid)
    assert "FK_property_contacts" in {b["code"] for b in c["blockers"]}
    cid2 = _contatto(m)
    _q(m, "INSERT INTO leads (agency_id, contact_id, pipeline, status, stage) VALUES (1, %s, 'sell', 'closed', 'lost')",
       (cid2,))
    _cestina_contatto(m, cid2)
    c2 = _controllo(m, "contact", cid2)
    assert "FK_leads" in {b["code"] for b in c2["blockers"]}
    assert _codice(_purge(m, "contact", cid2, c2["fingerprint"]), 409)["code"] == "NOT_PURGEABLE"
    assert _esiste(m, "contacts", cid) and _esiste(m, "contacts", cid2)
    assert _esiste(m, "properties", p["id"])
    # storico venditore diverso dalla traccia delle automazioni: e' storia, BLOCCA (e resta)
    cid3 = _contatto(m)
    ev = _q(m, "INSERT INTO seller_timeline_events (agency_id, contact_id, event_type, event_source) "
               "VALUES (1, %s, 'note_added', 'crm') RETURNING id", (cid3,))[0][0]
    _cestina_contatto(m, cid3)
    c3 = _controllo(m, "contact", cid3)
    assert "FK_seller_timeline_events" in {b["code"] for b in c3["blockers"]}, c3["blockers"]
    assert all(b.get("help") for b in c3["blockers"])                              # sempre «come risolvere»
    assert _codice(_purge(m, "contact", cid3, c3["fingerprint"]), 409)["code"] == "NOT_PURGEABLE"
    assert _esiste(m, "contacts", cid3) and _esiste(m, "seller_timeline_events", ev)


# ---------------------------------------------------------------------------
# richieste acquirente
# ---------------------------------------------------------------------------

def test_b01_richiesta_eleggibile(banco):
    m = banco
    contatto, rid = _richiesta(m)
    p = _unita(m, floor="3", internal_number=f"B{uuid.uuid4().hex[:4]}")
    mid = _abbinamento(m, rid, p["id"])                                           # suggerimento senza seguito
    _cestina_richiesta(m, rid)
    c = _controllo(m, "buy_request", rid)
    assert c["eligible"] is True, c["blockers"]
    assert {x["table"] for x in c["will_delete"]} >= {"buy_request_locations", "buy_request_history", "matches"}
    _codice(_purge(m, "buy_request", rid, c["fingerprint"]), 200)
    assert not _esiste(m, "buy_requests", rid)
    assert _q(m, "SELECT count(*) FROM buy_request_locations WHERE buy_request_id = %s", (rid,))[0][0] == 0
    assert not _esiste(m, "matches", mid) and _esiste(m, "properties", p["id"])   # l'immobile resta
    assert _esiste(m, "contacts", contatto)                                       # il contatto resta
    copia = _q(m, "SELECT before_state FROM record_lifecycle_events WHERE entity_type = 'buy_request' "
                  "AND entity_id = %s AND action = 'purge'", (rid,))[0][0]
    assert [h["event_type"] for h in copia["copied"]["history"]] == ["request_created"]
    assert _audit(m, "buy_request", rid) == [["success"]]


def test_b02_nota_o_abbinamento_con_seguito_bloccano(banco):
    m = banco
    _c, rid = _richiesta(m)
    _q(m, "INSERT INTO buy_request_history (buy_request_id, event_type, description) VALUES (%s, 'note', 'Chiamata')", (rid,))
    _cestina_richiesta(m, rid)
    c = _controllo(m, "buy_request", rid)
    assert "FK_buy_request_history" in {b["code"] for b in c["blockers"]}
    _c2, rid2 = _richiesta(m)
    p = _unita(m, floor="4", internal_number=f"M{uuid.uuid4().hex[:4]}")
    mid = _abbinamento(m, rid2, p["id"])
    _q(m, "INSERT INTO match_feedback (match_id, source, feedback_type) VALUES (%s, 'agent', 'positive')", (mid,))
    _cestina_richiesta(m, rid2)
    c2 = _controllo(m, "buy_request", rid2)
    assert "FK_matches" in {b["code"] for b in c2["blockers"]}
    assert _codice(_purge(m, "buy_request", rid2, c2["fingerprint"]), 409)["code"] == "NOT_PURGEABLE"
    assert _esiste(m, "buy_requests", rid2) and _esiste(m, "matches", mid)


# ---------------------------------------------------------------------------
# sicurezza
# ---------------------------------------------------------------------------

def test_s01_solo_il_super_admin_designato(banco):
    m = banco
    from operator_auth.context import OperatorContext
    from platform_admin.super_admin import SuperAdminDenied
    from trash import purge as definitiva
    cid = _contatto(m)
    _cestina_contatto(m, cid)
    impronta = _controllo(m, "contact", cid)["fingerprint"]
    for chi in ("owner_a", "agent_a", "owner_b"):                                 # ruoli d'agenzia: mai
        with pytest.raises(SuperAdminDenied):
            definitiva.purge(_ctx(m, chi), "contact", cid, CONFERMA, impronta)
        with pytest.raises(SuperAdminDenied):
            definitiva.purge_check(_ctx(m, chi), "contact", cid)
    altro = _q(m, "INSERT INTO operator_users (email, email_normalized, password_hash, is_platform_admin) "
                  "VALUES ('altro.cg3@x.test', 'altro.cg3@x.test', 'pbkdf2_sha256$1$x$y', TRUE) "
                  "ON CONFLICT (email_normalized) DO UPDATE SET is_platform_admin = TRUE RETURNING id")[0][0]
    info = _q(m, "INSERT INTO operator_users (email, email_normalized, password_hash, is_platform_admin) "
                 "VALUES ('info@stima360.it', 'info@stima360.it', 'pbkdf2_sha256$1$x$y', TRUE) "
                 "ON CONFLICT (email_normalized) DO UPDATE SET is_platform_admin = TRUE RETURNING id")[0][0]
    prima = _q(m, "SELECT count(*) FROM platform_audit_log WHERE action = 'platform.trash.denied'")[0][0]
    for uid in (altro, info):
        m["sa_ctx"]["ctx"] = OperatorContext(user_id=uid, agency_id=None, role=None, is_platform_admin=True,
                                             session_id=None, auth_channel="operator_session")
        assert _codice(_purge(m, "contact", cid, impronta), 403)["code"] == "SUPER_ADMIN_REQUIRED"
        assert _codice(m["sa"].get(f"/api/platform/trash/contact/{cid}/purge-check"), 403)["code"] == "SUPER_ADMIN_REQUIRED"
    assert _q(m, "SELECT count(*) FROM platform_audit_log WHERE action = 'platform.trash.denied'")[0][0] == prima + 4
    m["sa_ctx"]["ctx"] = OperatorContext(user_id=m["ids"]["super"], agency_id=None, role=None, is_platform_admin=True,
                                         session_id=None, auth_channel="operator_session")
    stato = _q(m, "SELECT status FROM operator_users WHERE id = %s", (m["ids"]["super"],))[0][0]
    _q(m, "UPDATE operator_users SET status = 'disabled' WHERE id = %s", (m["ids"]["super"],))
    try:
        assert _codice(_purge(m, "contact", cid, impronta), 403)["code"] == "SUPER_ADMIN_REQUIRED"
    finally:
        _q(m, "UPDATE operator_users SET status = %s WHERE id = %s", (stato, m["ids"]["super"]))
    _q(m, "UPDATE operator_users SET is_platform_admin = FALSE WHERE id = %s", (m["ids"]["super"],))
    try:
        assert _codice(_purge(m, "contact", cid, impronta), 403)["code"] == "SUPER_ADMIN_REQUIRED"
    finally:
        _q(m, "UPDATE operator_users SET is_platform_admin = TRUE WHERE id = %s", (m["ids"]["super"],))
    assert _esiste(m, "contacts", cid)
    _codice(_purge(m, "contact", cid, impronta), 200)                             # il designato, attivo: si'
    assert not _esiste(m, "contacts", cid)


def test_s02_stato_testo_impronta_ripetizione(banco):
    m = banco
    cid = _contatto(m)
    c = _controllo(m, "contact", cid)                                            # non nel Cestino
    assert c["eligible"] is False and c["blockers"][0]["code"] == "NOT_DELETED"
    assert _codice(_purge(m, "contact", cid, c["fingerprint"]), 409)["code"] == "NOT_DELETED"
    _cestina_contatto(m, cid)
    c = _controllo(m, "contact", cid)
    assert _codice(_purge(m, "contact", cid, c["fingerprint"], testo="elimina"), 400)["code"] == "CONFIRM_MISMATCH"
    _q(m, "INSERT INTO contact_roles (contact_id, role) VALUES (%s, 'buyer')", (cid,)) if False else None
    vecchia = c["fingerprint"]
    _q(m, "ALTER TABLE contact_roles DISABLE TRIGGER USER")
    try:
        _q(m, "INSERT INTO contact_roles (contact_id, role) VALUES (%s, 'buyer')", (cid,))   # cambia dopo il riepilogo
    finally:
        _q(m, "ALTER TABLE contact_roles ENABLE TRIGGER USER")
    r = _codice(_purge(m, "contact", cid, vecchia), 409)
    assert r["code"] == "PURGE_STALE" and r["fingerprint"] != vecchia
    assert _esiste(m, "contacts", cid)
    nuova = _controllo(m, "contact", cid)["fingerprint"]
    _codice(_purge(m, "contact", cid, nuova), 200)
    assert _codice(_purge(m, "contact", cid, nuova), 404)["code"] == "NOT_FOUND"  # ripetuta: nulla da fare
    assert _codice(m["sa"].get("/api/platform/trash/building/1/purge-check"), 400)["code"] == "INVALID_TYPE"


def test_s03_errore_in_transazione_rollback_completo(banco, monkeypatch):
    m = banco
    from platform_admin import audit
    from platform_admin.exceptions import PlatformAuditUnavailable
    _c, rid = _richiesta(m)
    _cestina_richiesta(m, rid)
    c = _controllo(m, "buy_request", rid)

    def rotto(**_k):
        raise PlatformAuditUnavailable("audit non scrivibile")
    originale = audit.record
    monkeypatch.setattr(audit, "record", rotto)
    r = _purge(m, "buy_request", rid, c["fingerprint"])
    assert r.status_code == 503, r.text
    assert _esiste(m, "buy_requests", rid)
    assert _q(m, "SELECT count(*) FROM buy_request_locations WHERE buy_request_id = %s", (rid,))[0][0] == 1
    assert _eventi(m, "buy_request", rid) == ["trash"]                            # nemmeno l'evento purge
    monkeypatch.setattr(audit, "record", originale)          # (`undo` toglierebbe anche la designazione)
    _codice(_purge(m, "buy_request", rid, _controllo(m, "buy_request", rid)["fingerprint"]), 200)


def test_s04_comando_voice_in_corso_blocca(banco, monkeypatch):
    m = banco
    from trash import purge as definitiva
    cid = _contatto(m)
    _cestina_contatto(m, cid)
    vero = definitiva.riferimenti_voice
    monkeypatch.setattr(definitiva, "riferimenti_voice", lambda cur, kind, eid: {
        "steps": [{"command_id": 9, "ordinal": 1, "intent": "create_contact", "state": "running", "property_ids": [],
                   "building": False, "in_progress": True}], "undos": []} if eid == cid else vero(cur, kind, eid))
    c = _controllo(m, "contact", cid)
    assert "VOICE_IN_PROGRESS" in {b["code"] for b in c["blockers"]}
    assert _codice(_purge(m, "contact", cid, c["fingerprint"]), 409)["code"] == "NOT_PURGEABLE"
    assert _esiste(m, "contacts", cid)


def test_s05_voice_non_cancella_e_nessuna_delete_http():
    for p in (ROOT / "voice").glob("*.py"):
        testo = p.read_text(encoding="utf-8")
        assert "trash.purge" not in testo and "from trash import purge" not in testo and "/purge" not in testo, p.name
    import main
    rotte = [(m.upper(), p) for p, ops in main.app.openapi()["paths"].items() for m in ops if "/purge" in p]
    assert ("POST", "/api/platform/trash/{kind}/{entity_id}/purge") in rotte
    assert all(metodo in ("GET", "POST") for metodo, _p in rotte), rotte
    assert not any(p.startswith("/api/trash") for _m, p in rotte)                 # nessuna rotta tenant
