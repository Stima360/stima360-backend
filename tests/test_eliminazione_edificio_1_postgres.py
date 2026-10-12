"""ELIMINAZIONE-EDIFICIO-1 - eliminazione controllata di un edificio con le sue
unita', autorizzata dal Super Admin, su PostgreSQL VERO (schema completo dal
runner, migration 101 compresa; database locale usa-e-getta).

Rotte VERE: Immobili (tenant, contesto per ruolo) e Platform (Super Admin,
ammissione sostituita da un contesto `is_platform_admin`; il registro
`platform_audit_log` e' scritto davvero, sullo stesso database).

  01  richiesta di un utente ordinario: riepilogo unita', stato «in attesa»,
      registro; ritiro solo del richiedente;
  02  richiesta in attesa: nessuna unita' ne' edificio cambia;
  03  approvazione: edificio e unita' nel Cestino insieme, chi/quando, registro,
      audit di piattaforma; l'approvazione non cancella nulla;
  04  rifiuto: nota obbligatoria, nulla cambia, nuova richiesta possibile;
  05  unita' attive e gia' nel Cestino: le seconde restano come erano;
  06  atomicita': un errore a meta' (o l'audit che non si scrive) non lascia
      nulla a meta';
  07  ripristino: edificio e SOLO le unita' spostate dall'approvazione;
      un'unita' non esce da sola dal Cestino mentre l'edificio c'e';
  08  operazioni non autorizzate: rotte dirette, ruoli, richiedente, guardie DB;
  09  isolamento multiagenzia;
  10  richieste duplicate e concorrenti; approvazione su unita' cambiate;
      approvazione contro un'unita' che nasce;
  11  cancellazione definitiva: solo Super Admin, solo dal Cestino, seconda
      conferma; i contatti restano; storico e registro restano;
  12  cancellazione definitiva bloccata dalle dipendenze da conservare
      (attivita', foto, storico stati, incarico, comando STIMA Voice IN CORSO;
      uno concluso si conserva: ELIMINAZIONE-EDIFICIO-2); blocchi del
      Cestino (pertinenze a cavallo, processi).
"""
from __future__ import annotations

import json
import threading
import uuid

import pytest

from tests.test_censimento_3_backend_postgres import (  # noqa: F401
    DSN, _in_attesa_di_lock, _in_parallelo, _q, _unita, completo, mondo,
)

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")


# ---------------------------------------------------------------------------
# banco
# ---------------------------------------------------------------------------

def _rimetti(c):
    """Il banco del censimento ripulisce con UPDATE che il Cestino rifiuta:
    qui si tolgono i collegamenti di pertinenza delle unita' di prova (le
    cancella poi il banco) e si escono dal Cestino edifici e storici."""
    _q(c, "ALTER TABLE properties DISABLE TRIGGER trg_properties_trash_freeze")
    _q(c, "UPDATE properties SET parent_property_id = NULL WHERE id > 25 AND parent_property_id IS NOT NULL")
    _q(c, "ALTER TABLE properties ENABLE TRIGGER trg_properties_trash_freeze")
    _q(c, "UPDATE buildings SET deleted_at = NULL, deleted_by_user_id = NULL, deleted_reason = NULL "
          "WHERE deleted_at IS NOT NULL")
    _q(c, "UPDATE properties SET deleted_at = NULL, deleted_by_user_id = NULL, deleted_reason = NULL "
          "WHERE deleted_at IS NOT NULL AND id <= 25")


@pytest.fixture
def banco(mondo, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import platform_admin.database as pdb
    from operator_auth.context import OperatorContext
    from platform_admin.dependencies import require_platform_admin
    from platform_admin.router import router as piattaforma

    m = mondo
    psycopg2 = m["psycopg2"]
    monkeypatch.setattr(pdb, "get_connection", lambda: psycopg2.connect(m["dsn"]))
    with m["conn"].cursor() as cur:
        cur.execute("SELECT id FROM operator_users WHERE email = 'super@x.test'")
        riga = cur.fetchone()
        if riga is None:
            cur.execute("INSERT INTO operator_users (email, email_normalized, password_hash, first_name, "
                        "is_platform_admin) VALUES ('super@x.test', 'super@x.test', 'pbkdf2_sha256$1$x$y', "
                        "'Super', TRUE) RETURNING id")
            riga = cur.fetchone()
        superadmin = riga[0]
        cur.execute("SELECT id FROM operator_users WHERE email = 'a2.a@x.test'")
        riga = cur.fetchone()
        if riga is None:
            cur.execute("INSERT INTO operator_users (email, email_normalized, password_hash, first_name) "
                        "VALUES ('a2.a@x.test', 'a2.a@x.test', 'pbkdf2_sha256$1$x$y', 'Cesare') RETURNING id")
            riga = cur.fetchone()
            cur.execute("INSERT INTO agency_memberships (agency_id, operator_user_id, role, status) "
                        "VALUES (1, %s, 'agent', 'active')", (riga[0],))
        m["ids"]["agent_a2"] = riga[0]
        m["ruoli"]["agent_a2"] = (riga[0], 1, "agent")
    m["ids"]["super"] = superadmin
    # ELIMINAZIONE-EDIFICIO-2: il Super Admin generale e' l'account DESIGNATO nella configurazione del servizio
    from property.building_deletion import SUPER_ADMIN_ENV
    monkeypatch.setenv(SUPER_ADMIN_ENV, str(superadmin))
    stato = {"ctx": OperatorContext(user_id=superadmin, agency_id=None, role=None, is_platform_admin=True,
                                    session_id=None, auth_channel="operator_session")}
    app = FastAPI()
    app.include_router(piattaforma)
    app.dependency_overrides[require_platform_admin] = lambda: stato["ctx"]
    m["sa"] = TestClient(app)
    m["sa_ctx"] = stato
    _rimetti(m)
    yield m
    _rimetti(m)


def _edificio(m, chi="owner_a", **kw):
    corpo = {"city": "Fermo", "address": f"Via Elimina {uuid.uuid4().hex[:6]}", "civic_number": "1",
             "units_declared": 4, "units_declared_source": "survey", "name": "Palazzina da eliminare", **kw}
    r = m["api"](chi).post("/api/property/buildings", json=corpo)
    assert r.status_code == 201, r.text
    return r.json()


def _agenzia(m, aid):
    return _q(m, "SELECT name FROM agencies WHERE id = %s", (aid,))[0][0]


def _codice(r, atteso):
    assert r.status_code == atteso, r.text
    return r.json()


def _stato(m, bid, chi="owner_a"):
    return _codice(m["api"](chi).get(f"/api/property/buildings/{bid}/deletion-request"), 200)


def _richiedi(m, bid, chi="agent_a", **corpo):
    return m["api"](chi).post(f"/api/property/buildings/{bid}/deletion-request",
                              json={"reason_code": "created_by_mistake", **corpo})


def _dettaglio(m, rid):
    return _codice(m["sa"].get(f"/api/platform/building-deletion-requests/{rid}"), 200)


def _approva(m, rid, impronta=None):
    if impronta is None:
        impronta = _dettaglio(m, rid)["fingerprint"]
    return m["sa"].post(f"/api/platform/building-deletion-requests/{rid}/approve", json={"fingerprint": impronta})


def _righe(m, bid):
    """Le righe dell'edificio e delle sue unita', per il confronto «nulla cambia»."""
    return (_q(m, "SELECT to_jsonb(b)::text FROM buildings b WHERE id = %s", (bid,)),
            _q(m, "SELECT id, to_jsonb(p)::text FROM properties p WHERE building_id = %s ORDER BY id", (bid,)))


def _nel_cestino(m, ids):
    return dict(_q(m, "SELECT id, deleted_at IS NOT NULL FROM properties WHERE id = ANY(%s)", (list(ids),)))


def _eventi(m, tipo, eid):
    return [r[0] for r in _q(m, "SELECT action FROM record_lifecycle_events WHERE entity_type = %s AND entity_id = %s "
                                "ORDER BY id", (tipo, eid))]


def _errore(m, sql, params=None):
    psycopg2 = m["psycopg2"]
    try:
        _q(m, sql, params)
    except psycopg2.Error as exc:
        with m["conn"].cursor() as cur:
            cur.execute("ROLLBACK")
        return str(exc).splitlines()[0]
    return None


def _cestina_unita(m, pid, chi="owner_a"):
    r = m["api"](chi).post(f"/api/property/properties/{pid}/trash", json={"reason_code": "duplicate"})
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# 01-02 richiesta, ritiro, nessun effetto finche' e' in attesa
# ---------------------------------------------------------------------------

def test_01_richiesta_utente_ordinario_e_ritiro(banco):
    m = banco
    e = _edificio(m)
    u1 = _unita(m, building_id=e["id"], floor="1", internal_number="1")
    u2 = _unita(m, building_id=e["id"], floor="2", internal_number="2")
    s = _stato(m, e["id"], "agent_a")
    assert s["can_request"] is True and s["request"] is None and s["blockers"] == []
    assert (s["units"]["total"], s["units"]["active"], s["units"]["trash"]) == (2, 2, 0)
    assert _codice(_richiedi(m, e["id"], reason_code="boh"), 400)["code"] == "INVALID_TRASH_REASON"
    r = _codice(_richiedi(m, e["id"], note="  creato due volte  "), 201)
    assert (r["status"], r["requested_by_user_id"], r["requested_by_name"]) == ("pending", m["ids"]["agent_a"], "Anna")
    assert (r["units_total"], r["units_already_trashed"], r["request_note"]) == (2, 0, "creato due volte")
    assert r["agency_name"] == _agenzia(m, 1) and r["building_label"].startswith("Palazzina da eliminare")
    assert _eventi(m, "building", e["id"]) == ["deletion_requested"]
    # lo stato: in attesa per tutti nell'agenzia; ritiro solo per chi l'ha chiesta
    s = _stato(m, e["id"], "agent_a")
    assert s["request"]["status"] == "pending" and s["can_request"] is False and s["can_withdraw"] is True
    assert _stato(m, e["id"], "owner_a")["can_withdraw"] is False
    w = f"/api/property/building-deletion-requests/{r['id']}/withdraw"
    assert _codice(m["api"]("owner_a").post(w), 403)["code"] == "NOT_REQUESTER"
    assert _codice(m["api"]("agent_a2").post(w), 403)["code"] == "NOT_REQUESTER"
    ritirata = _codice(m["api"]("agent_a").post(w), 200)
    assert ritirata["status"] == "withdrawn" and ritirata["decided_by_user_id"] == m["ids"]["agent_a"]
    assert _codice(m["api"]("agent_a").post(w), 409)["code"] == "REQUEST_NOT_PENDING"
    assert _eventi(m, "building", e["id"]) == ["deletion_requested", "deletion_withdrawn"]
    assert _nel_cestino(m, [u1["id"], u2["id"]]) == {u1["id"]: False, u2["id"]: False}
    # ritirata: si puo' chiedere di nuovo; anche un titolare deve chiedere
    assert _codice(_richiedi(m, e["id"], "owner_a"), 201)["requested_by_role"] == "agency_owner"


def test_02_richiesta_in_attesa_non_cambia_nulla(banco):
    m = banco
    e = _edificio(m)
    u = _unita(m, building_id=e["id"], floor="1", internal_number="1")
    p = _unita(m, building_id=e["id"], parent_property_id=u["id"], property_type="garage", floor="-1")
    prima = _righe(m, e["id"])
    r = _codice(_richiedi(m, e["id"]), 201)
    assert _righe(m, e["id"]) == prima
    # l'edificio resta modificabile e utilizzabile mentre la richiesta aspetta
    assert m["api"]().patch(f"/api/property/buildings/{e['id']}", json={"notes": "in attesa"}).status_code == 200
    assert _dettaglio(m, r["id"])["can_approve"] is True
    assert _nel_cestino(m, [u["id"], p["id"]]) == {u["id"]: False, p["id"]: False}
    assert _q(m, "SELECT deleted_at FROM buildings WHERE id = %s", (e["id"],))[0][0] is None


# ---------------------------------------------------------------------------
# 03-05 approvazione, rifiuto, unita' gia' nel Cestino
# ---------------------------------------------------------------------------

def test_03_approvazione_sposta_tutto_insieme(banco):
    m = banco
    e = _edificio(m)
    u1 = _unita(m, building_id=e["id"], floor="1", internal_number="1")
    u2 = _unita(m, building_id=e["id"], floor="2", internal_number="2")
    g = _unita(m, building_id=e["id"], parent_property_id=u1["id"], property_type="garage", floor="-1")
    r = _codice(_richiedi(m, e["id"]), 201)
    lista = _codice(m["sa"].get("/api/platform/building-deletion-requests"), 200)["items"]
    voce = next(x for x in lista if x["id"] == r["id"])
    assert (voce["agency_name"], voce["requested_by_name"], voce["stale"]) == (_agenzia(m, 1), "Anna", False)
    d = _dettaglio(m, r["id"])
    assert d["units"]["total"] == 3 and d["blockers"] == [] and d["can_approve"] and d["can_reject"]
    audit_prima = _q(m, "SELECT count(*) FROM platform_audit_log WHERE action = 'platform.building_deletion.approve'")[0][0]
    ok = _codice(_approva(m, r["id"]), 200)
    assert ok["status"] == "approved" and ok["decided_by_user_id"] == m["ids"]["super"]
    assert sorted(ok["trashed_unit_ids"]) == sorted([u1["id"], u2["id"], g["id"]]) and ok["already_trashed_unit_ids"] == []
    b = _q(m, "SELECT deleted_at IS NOT NULL, deleted_by_user_id, deleted_reason FROM buildings WHERE id = %s", (e["id"],))[0]
    assert b == [True, m["ids"]["super"], "created_by_mistake"]
    unita = _q(m, "SELECT id, deleted_at IS NOT NULL, deleted_by_user_id, building_id, parent_property_id "
                  "FROM properties WHERE building_id = %s ORDER BY id", (e["id"],))
    assert [x[1:4] for x in unita] == [[True, m["ids"]["super"], e["id"]]] * 3      # nessun orfano
    assert [x[4] for x in unita if x[0] == g["id"]] == [u1["id"]]                  # pertinenza ancora collegata
    assert _eventi(m, "building", e["id"]) == ["deletion_requested", "trash", "deletion_approved"]
    assert _eventi(m, "property", u2["id"])[-1] == "trash"
    meta = _q(m, "SELECT metadata FROM record_lifecycle_events WHERE entity_type = 'property' AND entity_id = %s "
                 "ORDER BY id DESC LIMIT 1", (u2["id"],))[0][0]
    assert meta["building_deletion_request_id"] == r["id"] and meta["actor_role"] == "platform_admin"
    assert _q(m, "SELECT count(*) FROM platform_audit_log WHERE action = 'platform.building_deletion.approve'")[0][0] == audit_prima + 1
    # nulla e' cancellato: le righe ci sono tutte
    assert _q(m, "SELECT count(*) FROM properties WHERE building_id = %s", (e["id"],))[0][0] == 3
    # la scheda per l'operatore: nel Cestino, ripristino non suo; lo stato della richiesta: approvata
    scheda = _codice(m["api"]().get(f"/api/property/buildings/{e['id']}"), 200)
    assert scheda["trash"]["can_restore"] is False and scheda["trash"]["restore_by"] == "super_admin"
    assert _stato(m, e["id"])["request"]["status"] == "approved"
    # il Cestino Edifici dell'agenzia: edificio e unita' raggruppati
    gruppi = _codice(m["api"]().get("/api/property/trash/building-groups"), 200)["items"]
    gr = next(x for x in gruppi if x["id"] == e["id"])
    assert gr["units_total"] == 3 and gr["units_moved_by_approval"] == 3 and gr["request"]["id"] == r["id"]
    # una seconda approvazione: la richiesta non e' piu' in attesa
    assert _codice(_approva(m, r["id"], d["fingerprint"]), 409)["code"] == "REQUEST_NOT_PENDING"


def test_04_rifiuto(banco):
    m = banco
    e = _edificio(m)
    u = _unita(m, building_id=e["id"], floor="1")
    r = _codice(_richiedi(m, e["id"]), 201)
    prima = _righe(m, e["id"])
    base = f"/api/platform/building-deletion-requests/{r['id']}/reject"
    assert m["sa"].post(base, json={}).status_code == 422
    assert _codice(m["sa"].post(base, json={"note": "   "}), 400)["code"] == "REJECT_NOTE_REQUIRED"
    no = _codice(m["sa"].post(base, json={"note": "L'edificio esiste davvero"}), 200)
    assert (no["status"], no["decision_note"], no["decided_by_user_id"]) == ("rejected", "L'edificio esiste davvero",
                                                                           m["ids"]["super"])
    assert _righe(m, e["id"]) == prima and _nel_cestino(m, [u["id"]]) == {u["id"]: False}
    assert _eventi(m, "building", e["id"]) == ["deletion_requested", "deletion_rejected"]
    assert _codice(_approva(m, r["id"], "0" * 64), 409)["code"] == "REQUEST_NOT_PENDING"
    assert _codice(m["sa"].post(base, json={"note": "di nuovo"}), 409)["code"] == "REQUEST_NOT_PENDING"
    s = _stato(m, e["id"], "agent_a")
    assert s["request"]["status"] == "rejected" and s["request"]["decision_note"] == "L'edificio esiste davvero"
    assert s["can_request"] is True


def test_05_unita_attive_e_gia_nel_cestino(banco):
    m = banco
    e = _edificio(m)
    attiva = _unita(m, building_id=e["id"], floor="1", internal_number="1")
    gia = _unita(m, building_id=e["id"], floor="2", internal_number="2")
    _cestina_unita(m, gia["id"], "owner_a")
    prima = _q(m, "SELECT deleted_at, deleted_by_user_id, deleted_reason FROM properties WHERE id = %s", (gia["id"],))[0]
    s = _stato(m, e["id"])
    assert (s["units"]["total"], s["units"]["active"], s["units"]["trash"]) == (2, 1, 1)
    r = _codice(_richiedi(m, e["id"]), 201)
    assert (r["units_total"], r["units_already_trashed"]) == (2, 1)
    ok = _codice(_approva(m, r["id"]), 200)
    assert ok["trashed_unit_ids"] == [attiva["id"]] and ok["already_trashed_unit_ids"] == [gia["id"]]
    # la gia' cestinata resta esattamente com'era (chi, quando, perche')
    assert _q(m, "SELECT deleted_at, deleted_by_user_id, deleted_reason FROM properties WHERE id = %s",
              (gia["id"],))[0] == prima
    assert _eventi(m, "property", gia["id"]) == ["trash"]


# ---------------------------------------------------------------------------
# 06 atomicita'
# ---------------------------------------------------------------------------

def test_06_spostamento_atomico(banco, monkeypatch):
    m = banco
    from platform_admin import audit as paudit
    from platform_admin.exceptions import PlatformAuditUnavailable
    from property import building_deletion as bd
    e = _edificio(m)
    ids = [_unita(m, building_id=e["id"], floor=str(i), internal_number=str(i))["id"] for i in range(1, 4)]
    r = _codice(_richiedi(m, e["id"]), 201)
    prima = _righe(m, e["id"])
    eventi_prima = _q(m, "SELECT count(*) FROM record_lifecycle_events")[0][0]
    # 1. un errore a meta' (dopo la seconda unita')
    originale, conta = bd._evento, {"n": 0}

    def rotto(cur, ctx, agency_id, tipo, eid, azione, **k):
        if tipo == "property" and azione == "trash":
            conta["n"] += 1
            if conta["n"] == 2:
                raise RuntimeError("guasto simulato")
        return originale(cur, ctx, agency_id, tipo, eid, azione, **k)
    monkeypatch.setattr(bd, "_evento", rotto)
    with pytest.raises(RuntimeError):
        _approva(m, r["id"])
    monkeypatch.setattr(bd, "_evento", originale)
    assert _righe(m, e["id"]) == prima
    assert _q(m, "SELECT status FROM building_deletion_requests WHERE id = %s", (r["id"],))[0][0] == "pending"
    assert _q(m, "SELECT count(*) FROM record_lifecycle_events")[0][0] == eventi_prima
    # 2. l'audit di piattaforma non si scrive: 503 e nessuna modifica
    def senza_audit(**_k):
        raise PlatformAuditUnavailable("giu'")
    monkeypatch.setattr(paudit, "record", senza_audit)
    assert m["sa"].post(f"/api/platform/building-deletion-requests/{r['id']}/approve",
                        json={"fingerprint": _dettaglio(m, r["id"])["fingerprint"]}).status_code == 503
    assert _righe(m, e["id"]) == prima
    assert _q(m, "SELECT status FROM building_deletion_requests WHERE id = %s", (r["id"],))[0][0] == "pending"
    monkeypatch.undo()
    import platform_admin.database as pdb
    monkeypatch.setattr(pdb, "get_connection", lambda: m["psycopg2"].connect(m["dsn"]))
    monkeypatch.setenv(bd.SUPER_ADMIN_ENV, str(m["ids"]["super"]))     # `undo` toglie anche la designazione
    # 3. poi riesce, tutto insieme
    _codice(_approva(m, r["id"]), 200)
    assert set(_nel_cestino(m, ids).values()) == {True}


# ---------------------------------------------------------------------------
# 07 ripristino coerente
# ---------------------------------------------------------------------------

def test_07_ripristino_coerente(banco):
    m = banco
    e = _edificio(m)
    a = _unita(m, building_id=e["id"], floor="1", internal_number="1")
    b = _unita(m, building_id=e["id"], floor="1", internal_number="2")
    gia = _unita(m, building_id=e["id"], floor="2", internal_number="3")
    _cestina_unita(m, gia["id"])
    r = _codice(_richiedi(m, e["id"]), 201)
    _codice(_approva(m, r["id"]), 200)
    # un'unita' non esce da sola dal Cestino mentre l'edificio c'e' (servizio e database)
    rr = m["api"]().post(f"/api/property/properties/{a['id']}/restore")
    assert _codice(rr, 409)["code"] == "BUILDING_IN_TRASH"
    assert "BUILDING_IN_TRASH" in _errore(m, "UPDATE properties SET deleted_at = NULL, deleted_by_user_id = NULL, "
                                             "deleted_reason = NULL WHERE id = %s", (a["id"],))
    # ripristino diretto dell'edificio dal tenant: riservato al Super Admin
    assert _codice(m["api"]().post(f"/api/property/buildings/{e['id']}/restore"), 403)["code"] == "BUILDING_RESTORE_SUPER_ADMIN"
    cestino = _codice(m["sa"].get("/api/platform/building-deletions/trash"), 200)["items"]
    voce = next(x for x in cestino if x["id"] == e["id"])
    assert voce["agency_name"] == _agenzia(m, 1) and voce["units_moved_by_approval"] == 2 and voce["units_total"] == 3
    esito = _codice(m["sa"].post(f"/api/platform/building-deletions/{e['id']}/restore"), 200)
    assert sorted(esito["restored_unit_ids"]) == sorted([a["id"], b["id"]]) and esito["still_in_trash_unit_ids"] == [gia["id"]]
    assert _nel_cestino(m, [a["id"], b["id"], gia["id"]]) == {a["id"]: False, b["id"]: False, gia["id"]: True}
    assert _q(m, "SELECT deleted_at FROM buildings WHERE id = %s", (e["id"],))[0][0] is None
    rq = _q(m, "SELECT restored_by_user_id, restored_at IS NOT NULL, status FROM building_deletion_requests WHERE id = %s",
            (r["id"],))[0]
    assert rq == [m["ids"]["super"], True, "approved"]
    assert _eventi(m, "building", e["id"])[-1] == "restore" and _eventi(m, "property", a["id"])[-1] == "restore"
    assert _codice(m["sa"].post(f"/api/platform/building-deletions/{e['id']}/restore"), 409)["code"] == "NOT_DELETED"
    # la gia' cestinata ora si ripristina come sempre (edificio fuori dal Cestino)
    assert m["api"]().post(f"/api/property/properties/{gia['id']}/restore").status_code == 200
    # conflitto di identita' catastale al ripristino: 409, nulla cambia
    e2 = _edificio(m)
    catasto = {"cadastral_municipality_code": "D542", "cadastral_section": "A", "cadastral_sheet": "7", "cadastral_parcel": "88",
               "cadastral_subunit": "4"}
    x = _unita(m, building_id=e2["id"], floor="1", **catasto)
    r2 = _codice(_richiedi(m, e2["id"]), 201)
    _codice(_approva(m, r2["id"]), 200)
    _unita(m, floor="3", **catasto)                         # stessa identita', attiva, fuori edificio
    c = _codice(m["sa"].post(f"/api/platform/building-deletions/{e2['id']}/restore"), 409)
    assert c["code"] == "RESTORE_CONFLICT" and c["conflicts"][0]["property_id"] == x["id"]
    assert _q(m, "SELECT deleted_at IS NOT NULL FROM buildings WHERE id = %s", (e2["id"],))[0][0] is True
    assert _nel_cestino(m, [x["id"]]) == {x["id"]: True}


# ---------------------------------------------------------------------------
# 08 operazioni non autorizzate
# ---------------------------------------------------------------------------

def test_08_operazioni_non_autorizzate(banco):
    m = banco
    from operator_auth.context import OperatorContext
    from platform_admin.dependencies import require_platform_admin
    from platform_admin.router import router as piattaforma
    from property import building_deletion as bd
    e = _edificio(m)
    _unita(m, building_id=e["id"], floor="1")
    # nessun operatore sposta da solo un edificio nel Cestino: neanche il titolare, neanche vuoto
    vuoto = _edificio(m)
    for chi in ("owner_a", "agent_a"):
        r = m["api"](chi).post(f"/api/property/buildings/{vuoto['id']}/trash", json={"reason_code": "duplicate"})
        assert _codice(r, 403)["code"] == "BUILDING_DELETION_REQUIRES_APPROVAL"
    assert _q(m, "SELECT deleted_at FROM buildings WHERE id = %s", (vuoto["id"],))[0][0] is None
    # il ruolo e' verificato nel backend: un contesto senza `is_platform_admin` e' rifiutato dal service
    r = _codice(_richiedi(m, e["id"]), 201)
    for ruolo in ("agency_owner", "agency_admin", "agent"):
        ctx = OperatorContext(user_id=m["ids"]["owner_a"], agency_id=1, role=ruolo, is_platform_admin=False,
                              session_id=None, auth_channel="operator_session")
        m["sa_ctx"]["ctx"] = ctx
        for chiamata in (lambda: m["sa"].post(f"/api/platform/building-deletion-requests/{r['id']}/approve",
                                              json={"fingerprint": "0" * 64}),
                         lambda: m["sa"].post(f"/api/platform/building-deletion-requests/{r['id']}/reject",
                                              json={"note": "no"}),
                         lambda: m["sa"].get("/api/platform/building-deletion-requests"),
                         lambda: m["sa"].post(f"/api/platform/building-deletions/{e['id']}/purge",
                                              json={"confirm_text": bd.PURGE_CONFIRM_TEXT, "fingerprint": "0" * 64})):
            assert _codice(chiamata(), 403)["code"] == "SUPER_ADMIN_REQUIRED"
    assert _q(m, "SELECT status FROM building_deletion_requests WHERE id = %s", (r["id"],))[0][0] == "pending"
    # ogni rotta nuova della superficie Platform dichiara l'ammissione del platform admin
    for rotta in piattaforma.routes:
        if "building-deletion" in rotta.path:
            assert require_platform_admin in [d.call for d in rotta.dependant.dependencies], rotta.path
    # guardie nel database: la richiesta e' storia
    assert "history" in _errore(m, "DELETE FROM building_deletion_requests WHERE id = %s", (r["id"],))
    assert "immutable" in _errore(m, "UPDATE building_deletion_requests SET building_label = 'x' WHERE id = %s",
                                  (r["id"],))
    # un edificio con unita' non va nel Cestino con una UPDATE diretta, nemmeno con le unita' tutte nel Cestino
    assert "BUILDING_HAS_UNITS" in _errore(m, "UPDATE buildings SET deleted_at = NOW(), deleted_reason = 'other' "
                                              "WHERE id = %s", (e["id"],))
    e2 = _edificio(m)
    sola = _unita(m, building_id=e2["id"], floor="1")
    _cestina_unita(m, sola["id"])
    assert "BUILDING_HAS_UNITS" in _errore(m, "UPDATE buildings SET deleted_at = NOW(), deleted_reason = 'other' "
                                              "WHERE id = %s", (e2["id"],))
    # e nemmeno con una richiesta solo IN ATTESA: serve l'approvazione
    _codice(_richiedi(m, e2["id"]), 201)
    assert "BUILDING_HAS_UNITS" in _errore(m, "UPDATE buildings SET deleted_at = NOW(), deleted_reason = 'other' "
                                              "WHERE id = %s", (e2["id"],))


# ---------------------------------------------------------------------------
# 09 isolamento multiagenzia
# ---------------------------------------------------------------------------

def test_09_isolamento_multiagenzia(banco):
    m = banco
    e = _edificio(m)
    _unita(m, building_id=e["id"], floor="1")
    r = _codice(_richiedi(m, e["id"]), 201)
    b = m["api"]("owner_b")
    assert b.get(f"/api/property/buildings/{e['id']}/deletion-request").status_code == 404
    assert b.post(f"/api/property/buildings/{e['id']}/deletion-request", json={}).status_code == 404
    assert b.post(f"/api/property/building-deletion-requests/{r['id']}/withdraw").status_code == 404
    _codice(_approva(m, r["id"]), 200)
    assert e["id"] not in [x["id"] for x in _codice(b.get("/api/property/trash/building-groups"), 200)["items"]]
    assert e["id"] in [x["id"] for x in _codice(m["api"]().get("/api/property/trash/building-groups"), 200)["items"]]
    # agent: vede nel Cestino cio' che ha chiesto lui, non quello degli altri
    assert e["id"] in [x["id"] for x in _codice(m["api"]("agent_a").get("/api/property/trash/building-groups"),
                                                    200)["items"]]
    assert e["id"] not in [x["id"] for x in _codice(m["api"]("agent_a2").get("/api/property/trash/building-groups"),
                                                        200)["items"]]
    # l'agenzia B: la sua richiesta, il Super Admin le vede tutte con l'agenzia
    eb = _edificio(m, "owner_b")
    rb = _codice(_richiedi(m, eb["id"], "owner_b"), 201)
    assert rb["agency_id"] == 2
    tutte = {x["id"]: x for x in _codice(m["sa"].get("/api/platform/building-deletion-requests",
                                                     params={"status": "all"}), 200)["items"]}
    assert tutte[rb["id"]]["agency_name"] == _agenzia(m, 2) and tutte[r["id"]]["agency_name"] == _agenzia(m, 1)
    assert _agenzia(m, 1) != _agenzia(m, 2)
    assert m["api"]().get(f"/api/property/buildings/{eb['id']}/deletion-request").status_code == 404


# ---------------------------------------------------------------------------
# 10 duplicate, concorrenti, unita' cambiate
# ---------------------------------------------------------------------------

def test_10_duplicate_concorrenti_e_unita_cambiate(banco):
    m = banco
    e = _edificio(m)
    _unita(m, building_id=e["id"], floor="1")
    r = _codice(_richiedi(m, e["id"]), 201)
    dup = _codice(_richiedi(m, e["id"], "owner_a"), 409)
    assert dup["code"] == "DELETION_REQUEST_PENDING" and dup["request_id"] == r["id"]
    # unita' cambiate durante l'attesa: approvazione ferma, nuova verifica
    _unita(m, building_id=e["id"], floor="2")
    d = _dettaglio(m, r["id"])
    assert d["stale"] is True and d["can_approve"] is False and d["can_reject"] is True
    st = _codice(_approva(m, r["id"], d["fingerprint"]), 409)
    assert st["code"] == "REQUEST_STALE" and st["units"]["total"] == 2
    assert _codice(_approva(m, r["id"], "0" * 64), 409)["code"] == "REQUEST_STALE"
    assert _q(m, "SELECT deleted_at FROM buildings WHERE id = %s", (e["id"],))[0][0] is None
    # due richieste concorrenti sullo stesso edificio: una sola
    e2 = _edificio(m)
    _unita(m, building_id=e2["id"], floor="1")
    esiti = _in_parallelo([lambda: _richiedi(m, e2["id"], "agent_a"), lambda: _richiedi(m, e2["id"], "agent_a2")])
    assert sorted(x.status_code for x in esiti) == [201, 409]
    assert _q(m, "SELECT count(*) FROM building_deletion_requests WHERE building_id = %s AND status = 'pending'",
              (e2["id"],))[0][0] == 1
    # approvazione e rifiuto concorrenti: una sola decisione
    rid = next(x.json()["id"] for x in esiti if x.status_code == 201)
    impronta = _dettaglio(m, rid)["fingerprint"]
    esiti = _in_parallelo([lambda: _approva(m, rid, impronta),
                           lambda: m["sa"].post(f"/api/platform/building-deletion-requests/{rid}/reject",
                                                json={"note": "no"})])
    assert sorted(x.status_code for x in esiti) == [200, 409]
    stato = _q(m, "SELECT status FROM building_deletion_requests WHERE id = %s", (rid,))[0][0]
    cestino = _q(m, "SELECT deleted_at IS NOT NULL FROM buildings WHERE id = %s", (e2["id"],))[0][0]
    assert (stato, cestino) in (("approved", True), ("rejected", False))
    # approvazione mentre nasce un'unita': mai entrambe (o l'unita' e' rifiutata, o l'approvazione si ferma)
    e3 = _edificio(m)
    _unita(m, building_id=e3["id"], floor="1")
    r3 = _codice(_richiedi(m, e3["id"]), 201)
    impronta = _dettaglio(m, r3["id"])["fingerprint"]
    esiti = _in_parallelo([lambda: _approva(m, r3["id"], impronta),
                           lambda: m["api"]("owner_a").post("/api/property/census/units",
                                                            json={"building_id": e3["id"], "floor": "9"})])
    codici = sorted(x.status_code for x in esiti)
    n_unita = _q(m, "SELECT count(*) FROM properties WHERE building_id = %s", (e3["id"],))[0][0]
    nel_cestino = _q(m, "SELECT deleted_at IS NOT NULL FROM buildings WHERE id = %s", (e3["id"],))[0][0]
    attive = _q(m, "SELECT count(*) FROM properties WHERE building_id = %s AND deleted_at IS NULL", (e3["id"],))[0][0]
    if nel_cestino:
        assert codici == [200, 409] and n_unita == 1 and attive == 0
    else:
        assert codici == [201, 409] and n_unita == 2
    # gruppo nel Cestino: una sola approvazione attiva per edificio (indice)
    assert _q(m, "SELECT count(*) FROM building_deletion_requests WHERE status = 'approved' AND restored_at IS NULL "
                 "AND purged_at IS NULL GROUP BY building_id ORDER BY 1 DESC LIMIT 1")[0][0] == 1


# ---------------------------------------------------------------------------
# 11-12 cancellazione definitiva
# ---------------------------------------------------------------------------

def _purge(m, bid, testo=None, impronta=None):
    from property import building_deletion as bd
    if impronta is None:
        impronta = _codice(m["sa"].get(f"/api/platform/building-deletions/{bid}/purge-check"), 200)["fingerprint"]
    return m["sa"].post(f"/api/platform/building-deletions/{bid}/purge",
                        json={"confirm_text": bd.PURGE_CONFIRM_TEXT if testo is None else testo, "fingerprint": impronta})


def _approvato(m, bid):
    r = _codice(_richiedi(m, bid), 201)
    _codice(_approva(m, r["id"]), 200)
    return r


def test_11_cancellazione_definitiva_solo_super_admin(banco):
    m = banco
    from operator_auth.context import OperatorContext
    from property import building_deletion as bd
    e = _edificio(m)
    u = _unita(m, building_id=e["id"], floor="1", internal_number="1")
    g = _unita(m, building_id=e["id"], parent_property_id=u["id"], property_type="garage", floor="-1")
    gia = _unita(m, building_id=e["id"], floor="2")
    _cestina_unita(m, gia["id"])
    # un proprietario collegato: il collegamento va, il contatto resta
    _q(m, "INSERT INTO property_contacts (property_id, contact_id, role) VALUES (%s, %s, 'owner')", (u["id"], m["mario"]))
    # fuori dal Cestino: non si cancella
    assert _codice(_purge(m, e["id"], impronta="0" * 64), 409)["code"] == "NOT_DELETED"
    r = _approvato(m, e["id"])
    # l'approvazione non ha cancellato nulla
    assert _q(m, "SELECT count(*) FROM properties WHERE building_id = %s", (e["id"],))[0][0] == 3
    chk = _codice(m["sa"].get(f"/api/platform/building-deletions/{e['id']}/purge-check"), 200)
    assert chk["eligible"] is True and chk["blockers"] == [] and chk["confirm_text"] == bd.PURGE_CONFIRM_TEXT, chk["blockers"]
    assert {p["table"] for p in chk["will_delete"]} >= {"property_contacts", "property_status_history"}
    # solo il Super Admin
    for ruolo in ("agency_owner", "agency_admin"):
        with pytest.raises(bd.PermissionDenied):
            bd.purge(OperatorContext(user_id=m["ids"]["owner_a"], agency_id=1, role=ruolo, is_platform_admin=False,
                                     session_id=None, auth_channel="operator_session"),
                     e["id"], bd.PURGE_CONFIRM_TEXT, chk["fingerprint"])
    # seconda conferma: testo esatto e impronta delle unita' viste
    assert _codice(_purge(m, e["id"], testo="elimina"), 400)["code"] == "CONFIRM_MISMATCH"
    assert _codice(_purge(m, e["id"], impronta="0" * 64), 409)["code"] == "REQUEST_STALE"
    assert _q(m, "SELECT count(*) FROM properties WHERE building_id = %s", (e["id"],))[0][0] == 3
    esito = _codice(_purge(m, e["id"]), 200)
    assert sorted(esito["purged_unit_ids"]) == sorted([u["id"], g["id"], gia["id"]])
    assert _q(m, "SELECT count(*) FROM buildings WHERE id = %s", (e["id"],))[0][0] == 0
    assert _q(m, "SELECT count(*) FROM properties WHERE id = ANY(%s)", ([u["id"], g["id"], gia["id"]],))[0][0] == 0
    assert _q(m, "SELECT count(*) FROM contacts WHERE id = %s", (m["mario"],))[0][0] == 1          # il contatto resta
    # storico: la richiesta e il registro restano, con chi e quando
    rq = _q(m, "SELECT status, purged_by_user_id, purged_at IS NOT NULL FROM building_deletion_requests WHERE id = %s",
            (r["id"],))[0]
    assert rq == ["approved", m["ids"]["super"], True]
    assert _eventi(m, "building", e["id"])[-1] == "purge" and _eventi(m, "property", g["id"])[-1] == "purge"
    assert _q(m, "SELECT count(*) FROM platform_audit_log WHERE action = 'platform.building_deletion.purge' "
                 "AND target_id = %s", (str(e["id"]),))[0][0] == 1
    # dopo: niente ripristino, niente seconda cancellazione
    assert m["sa"].post(f"/api/platform/building-deletions/{e['id']}/restore").status_code == 404
    assert _purge(m, e["id"], impronta="0" * 64).status_code == 404


def test_12_cancellazione_bloccata_dalle_dipendenze(banco):
    m = banco
    e = _edificio(m)
    a = _unita(m, building_id=e["id"], floor="1", internal_number="1")
    b = _unita(m, building_id=e["id"], floor="2", internal_number="2")
    c = _unita(m, building_id=e["id"], floor="3", internal_number="3")
    d = _unita(m, building_id=e["id"], floor="4", internal_number="4")
    # storico commerciale (attivita'), foto (file esterno), transizione di stato, comando Voice
    _q(m, "INSERT INTO activities (agency_id, property_id, activity_type, description) "
          "VALUES (1, %s, 'note', 'telefonata')", (a["id"],))
    _q(m, "INSERT INTO property_photos (property_id, url) VALUES (%s, 'https://cdn.example/x.jpg')", (b["id"],))
    _q(m, "INSERT INTO property_status_history (property_id, field_name, old_value, new_value, note) "
          "VALUES (%s, 'commercial_status', 'draft', 'evaluation', 'prova')", (c["id"],))
    _approvato(m, e["id"])
    chk = _codice(m["sa"].get(f"/api/platform/building-deletions/{e['id']}/purge-check"), 200)
    codici = {x["code"]: x for x in chk["blockers"]}
    assert chk["eligible"] is False
    assert set(codici) >= {"FK_activities", "FK_property_photos", "FK_property_status_history"}
    assert [i["property_id"] for i in codici["FK_activities"]["items"]] == [a["id"]]
    assert "file esterni" in codici["FK_property_photos"]["label"]
    righe = _q(m, "SELECT count(*) FROM properties WHERE building_id = %s", (e["id"],))[0][0]
    no = _codice(_purge(m, e["id"], impronta=chk["fingerprint"]), 409)
    assert no["code"] == "NOT_PURGEABLE" and {x["code"] for x in no["blockers"]} == set(codici)
    assert _q(m, "SELECT count(*) FROM properties WHERE building_id = %s", (e["id"],))[0][0] == righe
    assert _q(m, "SELECT count(*) FROM buildings WHERE id = %s", (e["id"],))[0][0] == 1
    assert "purge" not in _eventi(m, "building", e["id"])
    # STIMA Voice. COMPORTAMENTO CAMBIATO DA ELIMINAZIONE-EDIFICIO-2 (autorizzazione del 2026-10-12): un
    # comando vocale CONCLUSO non blocca piu' (i suoi riferimenti restano nel registro di Voice e nell'evento
    # `purge`); blocca solo un comando IN CORSO sugli stessi dati (VOICE_IN_PROGRESS). Qui: in corso, poi concluso.
    e2 = _edificio(m)
    v = _unita(m, building_id=e2["id"], floor="1")
    w = _unita(m, building_id=e2["id"], floor="2")
    comando = _q(m, "INSERT INTO voice_commands (agency_id, actor_user_id, client_command_id, request_sha256, "
                    "input_kind, recorded_at) VALUES (1, %s, %s, %s, 'text', NOW()) RETURNING id",
                 (m["ids"]["owner_a"], str(uuid.uuid4()), "a" * 64))[0][0]
    _q(m, "INSERT INTO voice_command_steps (command_id, ordinal, agency_id, intent, decision, step_class, "
          "client_request_id) VALUES (%s, 1, 1, 'create_unit', 'auto', 'replayable', %s)", (comando, str(uuid.uuid4())))
    _q(m, "UPDATE voice_command_steps SET state = 'running', result = %s::jsonb WHERE command_id = %s",
       (json.dumps({"id": v["id"], "entity": "property", "created": True}), comando))
    _approvato(m, e2["id"])
    chk = _codice(m["sa"].get(f"/api/platform/building-deletions/{e2['id']}/purge-check"), 200)
    voce = {x["code"]: x for x in chk["blockers"]}
    assert set(voce) == {"VOICE_IN_PROGRESS"} and voce["VOICE_IN_PROGRESS"]["count"] == 1
    assert _codice(_purge(m, e2["id"], impronta=chk["fingerprint"]), 409)["code"] == "NOT_PURGEABLE"
    assert _q(m, "SELECT count(*) FROM properties WHERE id = ANY(%s)", ([v["id"], w["id"]],))[0][0] == 2
    _q(m, "UPDATE voice_command_steps SET state = 'succeeded' WHERE command_id = %s", (comando,))
    chk = _codice(m["sa"].get(f"/api/platform/building-deletions/{e2['id']}/purge-check"), 200)
    assert chk["eligible"] is True and "VOICE_COMMANDS" in {x["code"] for x in chk["preserved"]}
    # blocchi del Cestino: pertinenza collegata fuori dall'edificio
    e3 = _edificio(m)
    p = _unita(m, building_id=e3["id"], floor="1")
    fuori = _unita(m, parent_property_id=p["id"], property_type="garage", floor="-1")
    s = _stato(m, e3["id"])
    assert s["can_request"] is False and [x["code"] for x in s["blockers"]] == ["PERTINENZA_FUORI_EDIFICIO"]
    assert s["blockers"][0]["items"][0]["property_id"] == fuori["id"]
    assert _codice(_richiedi(m, e3["id"]), 409)["code"] == "DELETION_BLOCKED"
    # processo aperto su un'unita' (venduta): blocco con il codice del Cestino Immobili
    e4 = _edificio(m)
    _q(m, "UPDATE properties SET building_id = %s WHERE id = 1", (e4["id"],))
    blocchi = _stato(m, e4["id"])["blockers"]
    assert blocchi and {x["property_id"] for x in blocchi} == {1}
    assert _codice(_richiedi(m, e4["id"]), 409)["code"] == "DELETION_BLOCKED"
    _q(m, "UPDATE properties SET building_id = NULL WHERE id = 1")
    assert _nel_cestino(m, [1]) == {1: False}


# ---------------------------------------------------------------------------
# 99 migration 101: la down si ferma con storico; senza, torna allo stato 091/085; up di nuovo
# ---------------------------------------------------------------------------

def test_99_migration_101_down_protetta_e_up_di_nuovo(banco):
    from pathlib import Path
    m = banco
    cartella = Path(__file__).resolve().parents[1] / "migrations"
    su = (cartella / "101_eliminazione_edificio_1.sql").read_text(encoding="utf-8")
    giu = (cartella / "101_eliminazione_edificio_1_down.sql").read_text(encoding="utf-8")
    e = _edificio(m)
    _unita(m, building_id=e["id"], floor="1")
    r = _codice(_richiedi(m, e["id"]), 201)
    _codice(_approva(m, r["id"]), 200)
    assert "esistono richieste di eliminazione" in _errore(m, giu)
    assert _q(m, "SELECT to_regclass('public.building_deletion_requests') IS NOT NULL")[0][0] is True
    # database usa-e-getta: si toglie lo storico sospendendo le guardie, poi la down passa
    _rimetti(m)
    for tabella, trigger in (("building_deletion_requests", "trg_building_deletion_requests_guard"),
                             ("record_lifecycle_events", "trg_record_lifecycle_events_append_only")):
        _q(m, f"ALTER TABLE {tabella} DISABLE TRIGGER {trigger}")
    _q(m, "DELETE FROM building_deletion_requests")
    _q(m, "DELETE FROM record_lifecycle_events WHERE action NOT IN ('trash', 'restore')")
    for tabella, trigger in (("building_deletion_requests", "trg_building_deletion_requests_guard"),
                             ("record_lifecycle_events", "trg_record_lifecycle_events_append_only")):
        _q(m, f"ALTER TABLE {tabella} ENABLE TRIGGER {trigger}")
    assert _errore(m, giu) is None
    assert _q(m, "SELECT to_regclass('public.building_deletion_requests')")[0][0] is None
    assert _q(m, "SELECT count(*) FROM pg_trigger WHERE tgname = 'trg_properties_unit_restore_guard'")[0][0] == 0
    assert "building_deletion_requests" not in _q(m, "SELECT prosrc FROM pg_proc WHERE proname = 'cestino_edifici_freeze'")[0][0]
    # il codice nuovo senza la 101: 503 leggibile, nessun 500; la rotta diretta resta rifiutata
    assert _codice(m["api"]().get(f"/api/property/buildings/{e['id']}/deletion-request"), 503)["code"] == \
        "BUILDING_DELETION_NOT_INSTALLED"
    assert _codice(m["sa"].get("/api/platform/building-deletion-requests"), 503)["code"] == "BUILDING_DELETION_NOT_INSTALLED"
    # up di nuovo, idempotente rispetto allo stato
    with m["conn"].cursor() as cur:
        cur.execute("BEGIN")
        cur.execute(su)
        cur.execute("COMMIT")
    assert _q(m, "SELECT count(*) FROM pg_trigger WHERE tgname IN ('trg_building_deletion_requests_guard', "
                 "'trg_properties_unit_restore_guard')")[0][0] == 2
    assert _codice(m["api"]().get(f"/api/property/buildings/{e['id']}/deletion-request"), 200)["can_request"] is True
