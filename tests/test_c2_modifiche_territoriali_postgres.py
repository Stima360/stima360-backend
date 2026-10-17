"""AGENZIE-TERRITORI C2 - proposte di modifica territoriali approvate dal Super Admin.

PostgreSQL usa-e-getta (schema completo dal runner, migration 104 compresa),
router veri (Immobili + Platform), dati di prova isolati. Agenzie e rete come
in C1: 1 competente per Alba Adriatica, 2 per Giulianova, 3 senza Comuni.

  01  il Supervisore competente propone; l'immobile resta identico
  02  Collaboratore e `agency_admin` competenti non propongono (403); agenzia non
      competente: 404, anche per l'elenco delle proposte
  03  campi vietati rifiutati (anche via API: 400 / 422); nessun campo arbitrario
  04  Super Admin approva: applicata UNA volta (seconda approvazione 409; due
      approvazioni simultanee: una sola passa)
  05  Super Admin rifiuta (motivo obbligatorio): immobile invariato
  06  non Super Admin non approva (tenant 403 sulla Platform; platform admin con
      ruolo revocato nel database 403)
  07  modifica concorrente dell'origine -> approvazione bloccata, proposta pending
  08  competenza cambiata (assegnazione trasferita) / proponente non piu' Supervisore -> bloccata
  09  difesa in profondita': una proposta con un campo fuori whitelist non si applica
  10  registro completo e immutabile; proposta mai cancellabile
  11  l'origine modifica come prima e non propone; C1 invariato
"""
from __future__ import annotations

import threading
import uuid

import pytest

from tests.test_c1_lettura_territoriale_postgres import _immobile, _operatore, rete  # noqa: F401
from tests.test_censimento_3_backend_postgres import DSN, _q, completo, mondo  # noqa: F401

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")


@pytest.fixture
def piattaforma(rete, monkeypatch):
    """Il router Platform montato sullo stesso client, con l'ammissione di prova:
    solo un contesto platform admin passa (come `require_platform_admin`)."""
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    import platform_admin.database as pdb
    from operator_auth.context import OperatorContext
    from operator_auth.dependencies import legacy_basic_agency_context, require_operator
    from platform_admin.dependencies import require_platform_admin
    from platform_admin.router import router as piattaforma_router
    from property.router import router as immobili

    m = rete
    monkeypatch.setattr(pdb, "get_connection", lambda: m["psycopg2"].connect(m["dsn"]))
    sa = _q(m, "SELECT id FROM operator_users WHERE email_normalized = 'c2.super@x.test'")
    sa = sa[0][0] if sa else _q(m, "INSERT INTO operator_users (email, email_normalized, password_hash, first_name, "
                                   "is_platform_admin) VALUES ('c2.super@x.test', 'c2.super@x.test', 'pbkdf2_sha256$1$x$y', "
                                   "'Super', TRUE) RETURNING id")[0][0]
    _q(m, "UPDATE operator_users SET is_platform_admin = TRUE, status = 'active' WHERE id = %s", (sa,))
    # ADM-1: approvare e rifiutare spetta al SOLO Super Admin generale designato.
    monkeypatch.setenv("STIMA360_SUPER_ADMIN_USER_ID", str(sa))
    m["ruoli"]["admin_b"] = (_operatore(m, "c2.admin.b@x.test", "Dario", 2, "agency_admin"), 2, "agency_admin")
    stato = m["stato"]

    def contesto():
        if stato["chi"] == "super":
            return OperatorContext(user_id=sa, agency_id=None, role=None, is_platform_admin=True,
                                   session_id=None, auth_channel="operator_session")
        uid, agenzia, ruolo = m["ruoli"][stato["chi"]]
        return OperatorContext(user_id=uid, agency_id=agenzia, role=ruolo, is_platform_admin=False,
                               session_id=None, auth_channel="operator_session")

    def ammissione():
        c = contesto()
        if not c.is_platform_admin:
            raise HTTPException(status_code=403, detail="riservato alla piattaforma")
        return c

    app = FastAPI()
    app.include_router(immobili)
    app.include_router(piattaforma_router)
    app.dependency_overrides[legacy_basic_agency_context] = contesto
    app.dependency_overrides[require_operator] = contesto
    app.dependency_overrides[require_platform_admin] = ammissione
    client = TestClient(app)

    def api(chi):
        class _C:
            def __getattr__(self, metodo):
                def invia(*a, **k):
                    stato["chi"] = chi
                    return getattr(client, metodo)(*a, **k)
                return invia
        return _C()

    try:
        yield {**m, "papi": api, "sa": sa, "ctx_di": lambda chi: (stato.__setitem__("chi", chi), contesto())[1]}
    finally:
        _q(m, "ALTER TABLE property_territorial_change_events DISABLE TRIGGER trg_property_territorial_change_events_append_only")
        _q(m, "ALTER TABLE property_territorial_change_requests DISABLE TRIGGER trg_property_territorial_change_requests_guard")
        _q(m, "DELETE FROM property_territorial_change_events")
        _q(m, "DELETE FROM property_territorial_change_requests")
        _q(m, "ALTER TABLE property_territorial_change_requests ENABLE TRIGGER trg_property_territorial_change_requests_guard")
        _q(m, "ALTER TABLE property_territorial_change_events ENABLE TRIGGER trg_property_territorial_change_events_append_only")


URL = "/api/property/properties/{}/territorial-change-requests"
PURL = "/api/platform/territorial-change-requests"


def _riga(m, pid):
    return dict(zip(("agency_id", "title", "rooms", "energy_class", "public_notes", "asking_price", "city", "updated_at"),
                    _q(m, "SELECT agency_id, title, rooms, energy_class, public_notes, asking_price, city, updated_at "
                          "FROM properties WHERE id = %s", (pid,))[0]))


def _eventi(m, rid):
    return [r[0] for r in _q(m, "SELECT action FROM property_territorial_change_events WHERE request_id = %s ORDER BY id", (rid,))]


def test_c2_modifiche_territoriali(piattaforma):
    m = piattaforma
    api = m["papi"]
    p = _immobile(m, "owner_a", "Giulianova", rooms=3, energy_class="E", asking_price=200000)   # origine 1, competente 2
    pid = p["id"]
    iniziale = _riga(m, pid)

    # 01 - proposta del Supervisore competente; l'immobile non cambia
    r = api("owner_b").get(URL.format(pid))
    assert r.status_code == 200 and r.json()["can_propose"] is True and r.json()["items"] == []
    assert [f["field"] for f in r.json()["allowed_fields"]][:3] == ["title", "rooms", "bedrooms"]
    assert api("owner_b").get(f"/api/property/properties/{pid}").json()["access"]["can_propose"] is True
    r = api("owner_b").post(URL.format(pid), json={"changes": {"rooms": 4, "energy_class": "C", "public_notes": "Vista mare"},
                                                    "note": "Verificato in sopralluogo"})
    assert r.status_code == 201, r.text
    req = r.json()
    assert req["status"] == "pending" and req["origin_agency_id"] == 1 and req["competent_agency_id"] == 2
    assert req["requested_by_role"] == "agency_owner" and req["territory_label"] == "Giulianova"
    assert {f["field"]: (f["old"], f["new"]) for f in req["fields"]} == {"rooms": (3, 4), "energy_class": ("E", "C"),
                                                                         "public_notes": (None, "Vista mare")}
    assert _riga(m, pid) == iniziale
    assert api("owner_b").post(URL.format(pid), json={"changes": {"rooms": 5}}).json()["code"] == "TERRITORIAL_CHANGE_PENDING"
    assert api("owner_b").get(URL.format(pid)).json()["can_propose"] is False         # una in attesa per immobile
    assert api("owner_a").get(URL.format(pid)).json()["items"][0]["id"] == req["id"]  # l'origine la vede

    # 02 - collaboratore / admin competenti, agenzia non competente
    for chi in ("agent_b", "admin_b"):
        r = api(chi).post(URL.format(pid), json={"changes": {"rooms": 9}})
        assert r.status_code == 403 and r.json()["code"] == "TERRITORIAL_SUPERVISOR_REQUIRED", (chi, r.text)
        assert api(chi).get(URL.format(pid)).json()["can_propose"] is False
    assert api("owner_c").post(URL.format(pid), json={"changes": {"rooms": 9}}).status_code == 404
    assert api("owner_c").get(URL.format(pid)).status_code == 404

    # 03 - campi vietati e corpi arbitrari
    q = _immobile(m, "owner_a", "Giulianova")
    for vietato in ({"asking_price": 1}, {"agency_id": 2}, {"city": "Alba Adriatica"}, {"commercial_status": "active"},
                    {"assigned_agent_id": m["ids"]["owner_b"]}, {"address": "Via X"}, {"latitude": 42.1},
                    {"mandate_type": "esclusiva"}, {"surface_sqm": 10}, {"cadastral_category": "A/2"}, {"rooms": 2, "deleted_at": "x"}):
        r = api("owner_b").post(URL.format(q["id"]), json={"changes": vietato})
        assert r.status_code == 400 and r.json()["code"] == "FIELD_NOT_ALLOWED", (vietato, r.text)
    assert api("owner_b").post(URL.format(q["id"]), json={"changes": {"rooms": 2}, "requested_by_user_id": 1}).status_code == 422
    assert api("owner_b").post(URL.format(q["id"]), json={"changes": {"rooms": -1}}).status_code == 400
    assert api("owner_b").post(URL.format(q["id"]), json={"changes": {"energy_class": "Z"}}).status_code == 400
    assert api("owner_b").post(URL.format(q["id"]), json={"changes": {}}).json()["code"] == "NO_CHANGES"
    assert api("owner_b").patch(f"/api/property/properties/{q['id']}", json={"rooms": 2}).status_code == 404   # C1: nessuna scrittura diretta
    assert _q(m, "SELECT count(*) FROM property_territorial_change_requests WHERE property_id = %s", (q["id"],))[0][0] == 0

    # 06 - chi non e' Super Admin non approva
    for chi in ("owner_b", "owner_a", "agent_b"):
        assert api(chi).post(f"{PURL}/{req['id']}/approve", json={}).status_code == 403
        assert api(chi).get(PURL).status_code == 403
    from core.exceptions import PermissionDenied
    from property import territorial_changes as tc
    with pytest.raises(PermissionDenied):
        tc.approve(m["ctx_di"]("owner_b"), req["id"])
    _q(m, "UPDATE operator_users SET is_platform_admin = FALSE WHERE id = %s", (m["sa"],))
    r = api("super").post(f"{PURL}/{req['id']}/approve", json={})
    assert r.status_code == 403, r.text                                                 # ruolo riletto nel database
    _q(m, "UPDATE operator_users SET is_platform_admin = TRUE WHERE id = %s", (m["sa"],))
    assert _riga(m, pid) == iniziale

    # 04 - Super Admin: elenco, dettaglio Prima/Dopo, approvazione una sola volta
    elenco = api("super").get(PURL).json()["items"]
    assert [x["id"] for x in elenco] == [req["id"]] and elenco[0]["origin_agency_name"] and elenco[0]["competent_agency_name"]
    d = api("super").get(f"{PURL}/{req['id']}").json()
    assert d["can_approve"] is True and d["blockers"] == []
    assert {f["field"]: (f["old"], f["current"], f["new"]) for f in d["fields"]}["rooms"] == (3, 3, 4)
    r = api("super").post(f"{PURL}/{req['id']}/approve", json={"note": "ok"})
    assert r.status_code == 200 and r.json()["status"] == "approved" and r.json()["apply_result"]["applied_fields"] == [
        "energy_class", "public_notes", "rooms"], r.text
    dopo = _riga(m, pid)
    assert (dopo["rooms"], dopo["energy_class"], dopo["public_notes"]) == (4, "C", "Vista mare")
    assert (dopo["agency_id"], dopo["title"], dopo["asking_price"], dopo["city"]) == (1, iniziale["title"], iniziale["asking_price"], "Giulianova")
    r = api("super").post(f"{PURL}/{req['id']}/approve", json={})
    assert r.status_code == 409 and r.json()["code"] == "REQUEST_NOT_PENDING"
    assert _eventi(m, req["id"]) == ["proposed", "approved"]
    assert api("owner_b").get(f"/api/property/properties/{pid}").json()["rooms"] == 4          # visibile alla competente
    assert api("owner_a").get(f"/api/property/properties/{pid}").json()["rooms"] == 4          # e all'origine

    # due approvazioni simultanee: una sola applica
    r2 = api("owner_b").post(URL.format(pid), json={"changes": {"rooms": 5}}).json()
    esiti = []
    def approva():
        try:
            esiti.append(("ok", tc.approve(m["ctx_di"]("super"), r2["id"])["status"]))
        except Exception as exc:                                      # noqa: BLE001
            esiti.append(("ko", getattr(exc, "code", type(exc).__name__)))
    fili = [threading.Thread(target=approva) for _ in range(2)]
    for f in fili:
        f.start()
    for f in fili:
        f.join()
    assert sorted(e[0] for e in esiti) == ["ko", "ok"] and ("ko", "REQUEST_NOT_PENDING") in esiti, esiti
    assert _eventi(m, r2["id"]) == ["proposed", "approved"] and _riga(m, pid)["rooms"] == 5

    # 05 - rifiuto: motivo obbligatorio, immobile invariato
    r3 = api("owner_b").post(URL.format(pid), json={"changes": {"title": "Nuovo titolo"}}).json()
    prima = _riga(m, pid)
    assert api("super").post(f"{PURL}/{r3['id']}/reject", json={"note": "  "}).status_code == 400
    r = api("super").post(f"{PURL}/{r3['id']}/reject", json={"note": "Titolo non corretto"})
    assert r.status_code == 200 and r.json()["status"] == "rejected" and r.json()["decision_note"] == "Titolo non corretto"
    assert _riga(m, pid) == prima
    assert api("super").post(f"{PURL}/{r3['id']}/approve", json={}).json()["code"] == "REQUEST_NOT_PENDING"
    assert _eventi(m, r3["id"]) == ["proposed", "rejected"]

    # 07 - modifica concorrente dell'origine: approvazione bloccata, proposta pending e consultabile
    r4 = api("owner_b").post(URL.format(pid), json={"changes": {"bathrooms": 2}}).json()
    assert api("owner_a").patch(f"/api/property/properties/{pid}", json={"asking_price": 210000}).status_code == 200
    prima = _riga(m, pid)
    r = api("super").post(f"{PURL}/{r4['id']}/approve", json={})
    assert r.status_code == 409 and r.json()["code"] == "TERRITORIAL_CHANGE_BLOCKED"
    assert [b["code"] for b in r.json()["blockers"]] == ["PROPERTY_CHANGED"]
    assert _riga(m, pid) == prima
    d = api("super").get(f"{PURL}/{r4['id']}").json()
    assert d["status"] == "pending" and d["can_approve"] is False and d["can_reject"] is True
    assert _eventi(m, r4["id"]) == ["proposed", "approval_blocked"]
    api("super").post(f"{PURL}/{r4['id']}/reject", json={"note": "Superata da una modifica dell'origine"})

    # 08 - competenza cambiata / proponente non piu' Supervisore
    r5 = api("owner_b").post(URL.format(pid), json={"changes": {"bedrooms": 2}}).json()
    giul = m["rete"]["Giulianova"]
    _q(m, "UPDATE agency_territory_assignments SET status = 'suspended' WHERE id = %s", (giul["assignment"],))
    r = api("super").post(f"{PURL}/{r5['id']}/approve", json={})
    assert r.status_code == 409 and "COMPETENCE_CHANGED" in [b["code"] for b in r.json()["blockers"]]
    _q(m, "UPDATE agency_territory_assignments SET status = 'active' WHERE id = %s", (giul["assignment"],))
    _q(m, "UPDATE agency_memberships SET role = 'agency_admin' WHERE agency_id = 2 AND operator_user_id = %s", (m["ids"]["owner_b"],))
    r = api("super").post(f"{PURL}/{r5['id']}/approve", json={})
    assert r.status_code == 409 and [b["code"] for b in r.json()["blockers"]] == ["PROPOSER_NOT_ENABLED"]
    _q(m, "UPDATE agency_memberships SET role = 'agency_owner' WHERE agency_id = 2 AND operator_user_id = %s", (m["ids"]["owner_b"],))
    assert _riga(m, pid)["rooms"] == 5 and _q(m, "SELECT bedrooms FROM properties WHERE id = %s", (pid,))[0][0] is None
    r = api("super").post(f"{PURL}/{r5['id']}/approve", json={})                          # ripristinato tutto: ora passa
    assert r.status_code == 200 and _q(m, "SELECT bedrooms FROM properties WHERE id = %s", (pid,))[0][0] == 2

    # 09 - una proposta manomessa con un campo fuori whitelist non si applica
    from property.territorial_changes import fingerprint, _immobile as leggi
    from core.database import core_cursor
    with core_cursor() as (_, cur):
        impronta = fingerprint(leggi(cur, pid))
    _q(m, "INSERT INTO property_territorial_change_requests (property_id, property_label, origin_agency_id, competent_agency_id, "
          "territory_id, territory_label, changes, base_fingerprint, requested_by_user_id, requested_by_role) "
          "VALUES (%s, 'x', 1, 2, %s, 'Giulianova', %s, %s, %s, 'agency_owner') ",
       (pid, giul["territory"], '{"agency_id": {"old": 1, "new": 2}}', impronta, m["ids"]["owner_b"]))
    manomessa = _q(m, "SELECT id FROM property_territorial_change_requests WHERE property_id = %s AND status = 'pending'", (pid,))[0][0]
    r = api("super").post(f"{PURL}/{manomessa}/approve", json={})
    assert r.status_code == 409 and r.json()["code"] == "FIELD_NOT_ALLOWED" and _riga(m, pid)["agency_id"] == 1
    api("super").post(f"{PURL}/{manomessa}/reject", json={"note": "test"})

    # 10 - registro immutabile, proposte non cancellabili, decisione definitiva
    for sql in ("UPDATE property_territorial_change_events SET action = 'approved' WHERE request_id = %s",
                "DELETE FROM property_territorial_change_events WHERE request_id = %s",
                "DELETE FROM property_territorial_change_requests WHERE id = %s",
                "UPDATE property_territorial_change_requests SET status = 'pending', decided_at = NULL WHERE id = %s",
                "UPDATE property_territorial_change_requests SET changes = '{\"rooms\": {\"old\": 1, \"new\": 9}}' WHERE id = %s"):
        with pytest.raises(Exception):
            _q(m, sql, (req["id"],))
    tutti = _q(m, "SELECT r.id, r.status, count(e.id) FROM property_territorial_change_requests r "
                  "JOIN property_territorial_change_events e ON e.request_id = r.id WHERE r.property_id = %s GROUP BY r.id, r.status", (pid,))
    assert len(tutti) == 6 and all(n >= 2 for rid, _, n in tutti if rid != manomessa)   # la manomessa non e' nata da una proposta
    assert {x[0] for x in _q(m, "SELECT DISTINCT actor_user_id FROM property_territorial_change_events WHERE action = 'proposed'")} == {m["ids"]["owner_b"]}
    assert {x[0] for x in _q(m, "SELECT DISTINCT actor_user_id FROM property_territorial_change_events WHERE action <> 'proposed'")} == {m["sa"]}

    # 11 - l'origine modifica come prima e non propone; C1 invariato
    assert api("owner_a").post(URL.format(pid), json={"changes": {"rooms": 7}}).json()["code"] == "ORIGIN_USE_DIRECT_EDIT"
    r = api("owner_a").patch(f"/api/property/properties/{pid}", json={"rooms": 7})
    assert r.status_code == 200 and r.json()["rooms"] == 7
    assert api("owner_b").get(f"/api/property/properties/{pid}").json()["access"]["mode"] == "territorial"
    assert api("owner_c").get(f"/api/property/properties/{pid}").status_code == 404
    assert _q(m, "SELECT agency_id FROM properties WHERE id = %s", (pid,))[0][0] == 1


def test_c2_sentinelle():
    """Whitelist chiusa e senza campi di competenza, agenzia, Cestino, incarichi o censimento;
    il modulo non cancella e non tocca altre tabelle."""
    import inspect
    from property import territorial_changes as tc
    vietati = {"agency_id", "city", "province", "region", "microzone", "address", "civic_number", "postal_code",
               "latitude", "longitude", "asking_price", "minimum_price", "commercial_status", "classification",
               "assigned_agent_id", "assigned_to", "mandate_type", "mandate_start", "mandate_end", "archived_at",
               "surface_sqm", "commercial_surface_sqm", "record_kind", "building_id", "internal_notes", "metadata"}
    assert not vietati & set(tc.ALLOWED_FIELDS)
    sorgente = inspect.getsource(tc)
    assert "DELETE " not in sorgente
    tabelle_scritte = {t for t in ("properties", "property_territorial_change_requests", "property_territorial_change_events",
                                   "property_contacts", "property_leads", "buildings", "activities")
                       if f"UPDATE {t} " in sorgente or f"INSERT INTO {t} " in sorgente}
    assert tabelle_scritte == {"properties", "property_territorial_change_requests", "property_territorial_change_events"}


def test_c2_migration_104_down_protetta_e_riapplicabile(piattaforma):
    """Con una proposta il down si ferma; a vuoto toglie le due tabelle e la 104 si riapplica."""
    from pathlib import Path
    m = piattaforma
    radice = Path(__file__).resolve().parents[1] / "migrations"
    su = (radice / "104_agenzie_c2_territorial_change_requests.sql").read_text(encoding="utf-8")
    giu = (radice / "104_agenzie_c2_territorial_change_requests_down.sql").read_text(encoding="utf-8")
    giu = giu.replace("BEGIN;", "").replace("COMMIT;", "")
    p = _immobile(m, "owner_a", "Giulianova", rooms=1)
    assert m["papi"]("owner_b").post(URL.format(p["id"]), json={"changes": {"rooms": 2}}).status_code == 201
    conn, psycopg2 = m["conn"], m["psycopg2"]
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            with pytest.raises(psycopg2.Error, match="104 down: esistono proposte"):
                cur.execute(giu)
        conn.rollback()
        with conn.cursor() as cur:
            for t, g in (("property_territorial_change_events", "trg_property_territorial_change_events_append_only"),
                         ("property_territorial_change_requests", "trg_property_territorial_change_requests_guard")):
                cur.execute(f"ALTER TABLE {t} DISABLE TRIGGER {g}")
                cur.execute(f"DELETE FROM {t}")
            cur.execute(giu)
            cur.execute("SELECT to_regclass('public.property_territorial_change_requests') IS NULL, "
                        "to_regclass('public.property_territorial_change_events') IS NULL")
            assert list(cur.fetchone()) == [True, True]
            cur.execute(su)
            cur.execute(su)                                                   # idempotente
            cur.execute("SELECT count(*) FROM pg_trigger WHERE tgname IN ('trg_property_territorial_change_requests_guard', "
                        "'trg_property_territorial_change_events_append_only')")
            assert cur.fetchone()[0] == 2
        conn.rollback()                                                       # stato di prima: la riga di prova torna
    finally:
        conn.rollback()
        conn.autocommit = True
