"""DELETE-ARCH Fase 1A - «creato per errore» per appuntamenti e acquisizioni,
su PostgreSQL VERO.

Banco: quello di CRM-OPS-3 (Agenda completa + 081 vere, router VERI), con in
piu' la migration 084 applicata dal suo file. Sezioni del brief:

  A  annullato dal cliente -> cancelled_kind='client'
  B  annullato dall'agenzia -> cancelled_kind='agency'
  C  creato per errore -> cancelled/mistake, fuori dall'Agenda e dalla lista
     normali, non blocca, recuperabile col filtro esplicito
  D  acquisizione per errore con appuntamento futuro: lost/created_by_mistake
     + appuntamento cancelled/mistake, una transazione
  E  acquisizione con appuntamento avvenuto: 409, nessuna modifica parziale
  F  perdita reale: mai created_by_mistake; appuntamento annullabile insieme
  G  created_by_mistake fuori dalla lista normale e dalle «Perse»
  H  multi-agenzia
  I  agent su record non proprio
  J  owner/admin consentito
  K  platform admin fuori acting
  L  migration up/down

REVIEW 1 (cancelled_kind mai NULL per un NUOVO annullamento; atomicita'):
  R1-A  API senza kind -> 'agency'                 (test_a0)
  R1-B  writer interni/legacy senza kind -> 'agency'
  R1-C/D/E  client / agency / mistake espliciti
  R1-F  righe storiche cancelled con NULL valide e leggibili
  R1-ATOM  «creata per errore» + annullamento mistake: un solo commit
"""
from __future__ import annotations

import pytest

from tests import test_crm_ops_3_acquisitions_postgres as base
from tests.test_crm_ops_3_acquisitions_postgres import (  # noqa: F401  (fixture riusate)
    DSN, _app, _appuntamento, _crea, _eventi, _nel_passato, _riga, _solo_locale, agenda_completa,
    db, k, mondo, schema_a31, schema_acq, v, w,
)

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

MIGRAZIONE = base.MIGRAZIONI / "084_delete_arch_1a_mistakes.sql"
GIU = base.MIGRAZIONI / "084_delete_arch_1a_mistakes_down.sql"


@pytest.fixture(scope="module")
def schema_084(schema_acq):  # noqa: F811
    with schema_acq["conn"].cursor() as cur:
        cur.execute(MIGRAZIONE.read_text(encoding="utf-8"))
    schema_acq["conn"].commit()
    return schema_acq


@pytest.fixture
def f(schema_084, k):  # noqa: F811
    """Il banco di CRM-OPS-3 piu' un platform admin FUORI da acting."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from acquisitions.router import router as acquisizioni
    from appointments.router import router as agenda
    from operator_auth.context import OperatorContext
    from operator_auth.dependencies import legacy_basic_agency_context, require_operator

    ruoli = {"giorgio": (k["giorgio"], k["a"], "agency_owner", False),
             "anna": (k["anna"], k["a"], "agency_admin", False),
             "luca": (k["luca"], k["a"], "agent", False),
             "marta": (k["marta"], k["a"], "agent", False),
             "estraneo": (k["estraneo"], k["b"], "agent", False),
             "supremo": (k["supremo"], k["a"], None, True),
             "fuori": (k["supremo"], None, None, True)}
    stato = {"chi": "giorgio"}

    def contesto():
        user_id, agenzia, ruolo, platform = ruoli[stato["chi"]]
        return OperatorContext(user_id=user_id, agency_id=agenzia, role=ruolo,
                               is_platform_admin=platform, session_id=None,
                               auth_channel="operator_session")

    app = FastAPI()
    for r in (acquisizioni, agenda):
        app.include_router(r)
    app.dependency_overrides[legacy_basic_agency_context] = contesto
    app.dependency_overrides[require_operator] = contesto
    client = TestClient(app)

    def api(chi):
        stato["chi"] = chi
        return client

    return {**k, "api": api}


def _appuntamento_agenda(f, chi="giorgio", agente=None, h=11, tipo="call"):
    corpo = {"appointment_type": tipo, "status": "scheduled",
             "start_at": base.futuro(h).isoformat(), "end_at": base.futuro(h + 1).isoformat(),
             "assigned_user_id": agente or f["luca"], "client_request_id": base.chiave()}
    r = f["api"](chi).post("/api/appointments", json=corpo)
    assert r.status_code == 201, r.text
    return r.json()


def _annulla(f, app, kind, chi="giorgio", reason=None):
    return f["api"](chi).post(f"/api/appointments/{app['id']}/cancel",
                              json={"version": app["version"], "kind": kind, "reason": reason})


def _calendario(f, chi="giorgio", **extra):
    params = {"from": base.futuro(0).isoformat(), "to": base.futuro(23).isoformat(), **extra}
    r = f["api"](chi).get("/api/appointments/calendar", params=params)
    assert r.status_code == 200, r.text
    return {i["id"] for i in r.json()["items"] if i["kind"] == "appointment"}


def _lista(f, chi="giorgio", **extra):
    r = f["api"](chi).get("/api/appointments", params={"limit": 200, **extra})
    assert r.status_code == 200, r.text
    return {i["id"] for i in r.json()["items"]}


def _acquisizioni(f, chi="giorgio", **extra):
    r = f["api"](chi).get("/api/acquisitions", params={"limit": 200, **extra})
    assert r.status_code == 200, r.text
    return {i["id"] for i in r.json()["items"]}


def _ultimo_evento(f, appointment_id):
    return f["sql"]("SELECT changes FROM appointment_events WHERE appointment_id = %s "
                    "ORDER BY id DESC LIMIT 1", (appointment_id,))[0][0]


def _contesto(f, chi="giorgio"):
    from operator_auth.context import OperatorContext
    return OperatorContext(user_id=f[chi], agency_id=f["a"], role="agency_owner",
                           is_platform_admin=False, session_id=None,
                           auth_channel="operator_session")


def _istantanea(f, acq):
    """Tutto cio' che un rifiuto non deve toccare."""
    return (_riga(f, acq["id"]), _app(f, acq["appointment_id"]), _eventi(f, acq["id"]),
            f["sql"]("SELECT count(*) FROM appointment_events WHERE appointment_id = %s",
                     (acq["appointment_id"],))[0][0])


# ---------------------------------------------------------------------------
# A / B / C - appuntamenti
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["client", "agency"])
def test_ab_annullamento_reale_qualificato(f, kind):
    app = _appuntamento_agenda(f)
    r = _annulla(f, app, kind)
    assert r.status_code == 200, r.text
    riga = _app(f, app["id"])
    assert riga["status"] == "cancelled" and riga["cancelled_kind"] == kind and riga["cancelled_at"]
    ev = f["sql"]("SELECT changes FROM appointment_events WHERE appointment_id = %s ORDER BY id DESC LIMIT 1",
                  (app["id"],))[0][0]
    assert ev["cancelled_kind"]["a"] == kind and ev["azione"] == "cancel"
    # un annullamento reale resta visibile col filtro «Annullato» e nella lista
    assert app["id"] in _calendario(f, statuses="cancelled")
    assert app["id"] in _lista(f)
    assert app["id"] not in _calendario(f, mistakes="true")


def test_a0_senza_kind_vale_agency_e_kind_invalido_422(f):
    """REVIEW 1 - A: l'API senza `kind` scrive 'agency', mai NULL."""
    app = _appuntamento_agenda(f)
    r = f["api"]("giorgio").post(f"/api/appointments/{app['id']}/cancel",
                                 json={"version": app["version"], "kind": "boh"})
    assert r.status_code == 422, r.text
    assert _app(f, app["id"])["status"] == "scheduled"
    r = f["api"]("giorgio").post(f"/api/appointments/{app['id']}/cancel", json={"version": app["version"]})
    assert r.status_code == 200, r.text
    assert _app(f, app["id"])["cancelled_kind"] == "agency"
    assert r.json()["cancelled_kind"] == "agency"
    assert _ultimo_evento(f, app["id"])["cancelled_kind"] == {"da": None, "a": "agency"}
    # resta un annullamento normale: visibile con «Annullato», non fra gli errori
    assert app["id"] in _calendario(f, statuses="cancelled")
    assert app["id"] not in _calendario(f, mistakes="true")


def test_c_creato_per_errore_fuori_dall_agenda_non_blocca_recuperabile(f):
    app = _appuntamento_agenda(f, h=12)
    assert app["id"] in _calendario(f)
    r = _annulla(f, app, "mistake")
    assert r.status_code == 200, r.text
    riga = _app(f, app["id"])
    assert riga["status"] == "cancelled" and riga["cancelled_kind"] == "mistake"
    assert f["sql"]("SELECT count(*) FROM appointments WHERE id = %s", (app["id"],))[0][0] == 1, "nessuna DELETE"
    # Agenda normale: ne' di default, ne' con «Annullato», ne' «Tutti gli stati»
    tutti = "requested,scheduled,confirmed,completed,cancelled,no_show,rescheduled"
    for extra in ({}, {"statuses": "cancelled"}, {"statuses": tutti}):
        assert app["id"] not in _calendario(f, **extra), extra
        assert app["id"] not in _lista(f, **extra), extra
    # recuperabile SOLO col filtro esplicito, e il dettaglio resta leggibile
    assert app["id"] in _calendario(f, mistakes="true")
    assert app["id"] in _lista(f, mistakes="true")
    assert not (_lista(f, mistakes="true") & _lista(f)), "i due insiemi non si sovrappongono"
    voce = [i for i in f["api"]("giorgio").get("/api/appointments/calendar", params={
        "from": base.futuro(0).isoformat(), "to": base.futuro(23).isoformat(), "mistakes": "true"}).json()["items"]
        if i.get("id") == app["id"]][0]
    assert voce["cancelled_kind"] == "mistake"
    d = f["api"]("giorgio").get(f"/api/appointments/{app['id']}")
    assert d.status_code == 200 and d.json()["appointment"]["cancelled_kind"] == "mistake"
    assert d.json()["allowed_actions"] == [] or not set(d.json()["allowed_actions"]) & {"cancel", "reschedule"}
    # non occupa disponibilita': lo stesso agente, stessa ora, si fissa
    altro = _appuntamento_agenda(f, h=12)
    assert altro["status"] == "scheduled"
    # e non e' visto come «Occupato» da un collega
    r = f["api"]("marta").get("/api/appointments/calendar", params={
        "from": base.futuro(0).isoformat(), "to": base.futuro(23).isoformat()})
    occupati = [i for i in r.json()["items"] if i["kind"] == "busy"]
    assert len([o for o in occupati if o["start_at"] == altro["start_at"]]) == 1


# ---------------------------------------------------------------------------
# D / E - acquisizione creata per errore
# ---------------------------------------------------------------------------

def test_d_acquisizione_per_errore_con_appuntamento_futuro(f):
    acq = _crea(f)
    assert acq["allowed_actions"]["mistake"] is True
    r = f["api"]("giorgio").post(f"/api/acquisitions/{acq['id']}/mistake",
                                 json={"version": acq["version"], "notes": "doppione"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["status"] == "lost" and d["lost_reason"] == "created_by_mistake" and d["created_by_mistake"]
    assert d["status_label"] == "Creata per errore" and d["lost_at"] and d["lost_notes"] == "doppione"
    app = _app(f, acq["appointment_id"])
    assert app["status"] == "cancelled" and app["cancelled_kind"] == "mistake"
    # una transazione coerente: evento appuntamento annullato (con la
    # qualifica) e poi "lost", nello stesso istante del database
    ev = f["sql"]("SELECT event_type, changes, occurred_at FROM acquisition_events "
                  "WHERE acquisition_id = %s ORDER BY id", (acq["id"],))
    assert [e[0] for e in ev][-2:] == ["appointment_cancelled", "lost"]
    assert ev[-2][1]["appointment_cancelled_kind"] == "mistake"
    assert ev[-1][1]["lost_reason"] == "created_by_mistake" and ev[-1][1]["appointment_cancelled"] is True
    assert ev[-2][2] == ev[-1][2]
    # nessun fantasma in Agenda
    assert acq["appointment_id"] not in _calendario(f, statuses="cancelled")
    # terminale: non si riapre, non si ri-segna
    r = f["api"]("giorgio").post(f"/api/acquisitions/{acq['id']}/mistake", json={"version": d["version"]})
    assert r.status_code == 409
    r = f["api"]("giorgio").post(f"/api/acquisitions/{acq['id']}/status",
                                 json={"version": d["version"], "status": "inspection_done"})
    assert r.status_code == 409


@pytest.mark.parametrize("come", ["completato", "assente", "pipeline_avanzata", "predecessore_assente"])
def test_e_appuntamento_avvenuto_409_nessuna_modifica(f, come):
    acq = _crea(f)
    api = f["api"]("giorgio")
    if come == "completato":
        _nel_passato(f, acq)
        app = _app(f, acq["appointment_id"])
        assert api.post(f"/api/appointments/{app['id']}/complete", json={"version": app["version"]}).status_code == 200
        f["sql"]("UPDATE acquisitions SET status = 'appointment_set' WHERE id = %s", (acq["id"],))
    elif come == "assente":
        _nel_passato(f, acq)
        app = _app(f, acq["appointment_id"])
        assert api.post(f"/api/appointments/{app['id']}/no-show", json={"version": app["version"]}).status_code == 200
    elif come == "pipeline_avanzata":
        f["sql"]("UPDATE acquisitions SET status = 'valuation_presented' WHERE id = %s", (acq["id"],))
    else:
        _nel_passato(f, acq)
        app = _app(f, acq["appointment_id"])
        assert api.post(f"/api/appointments/{app['id']}/no-show", json={"version": app["version"]}).status_code == 200
        riga = _riga(f, acq["id"])
        r = api.post(f"/api/acquisitions/{acq['id']}/appointment", json={
            "version": riga["version"], "appointment": _appuntamento(f["luca"], h=16)})
        assert r.status_code == 201, r.text
        acq = r.json()
    acq = {**acq, **api.get(f"/api/acquisitions/{acq['id']}").json()}
    prima = _istantanea(f, acq)
    r = api.post(f"/api/acquisitions/{acq['id']}/mistake", json={"version": acq["version"]})
    assert r.status_code == 409 and r.json()["code"] == "APPOINTMENT_ALREADY_HAPPENED", r.text
    assert _istantanea(f, acq) == prima, "nessuna modifica parziale"
    assert acq["allowed_actions"]["mistake"] is False or come == "predecessore_assente"


# ---------------------------------------------------------------------------
# F / G - perdita reale e liste
# ---------------------------------------------------------------------------

def test_f_perdita_reale_mai_created_by_mistake_e_appuntamento_annullabile(f):
    acq = _crea(f)
    api = f["api"]("giorgio")
    r = api.post(f"/api/acquisitions/{acq['id']}/lost",
                 json={"version": acq["version"], "lost_reason": "created_by_mistake"})
    assert r.status_code == 422 and _riga(f, acq["id"])["status"] == "appointment_set"
    r = api.post(f"/api/acquisitions/{acq['id']}/lost", json={
        "version": acq["version"], "lost_reason": "other_agency", "cancel_appointment": True,
        "appointment_cancelled_kind": "mistake"})
    assert r.status_code == 422 and _app(f, acq["appointment_id"])["status"] == "scheduled"
    r = api.post(f"/api/acquisitions/{acq['id']}/lost", json={
        "version": acq["version"], "lost_reason": "other_agency", "cancel_appointment": True,
        "appointment_cancelled_kind": "client"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["lost_reason"] == "other_agency" and not d["created_by_mistake"] and d["status_label"] == "Persa"
    app = _app(f, acq["appointment_id"])
    assert app["status"] == "cancelled" and app["cancelled_kind"] == "client"
    assert _eventi(f, acq["id"])[-2:] == ["appointment_cancelled", "lost"]
    # e senza la richiesta l'appuntamento resta com'era (comportamento di prima)
    acq2 = _crea(f, appointment=_appuntamento(f["marta"], h=17))
    r = api.post(f"/api/acquisitions/{acq2['id']}/lost", json={"version": acq2["version"], "lost_reason": "other"})
    assert r.status_code == 200 and _app(f, acq2["appointment_id"])["status"] == "scheduled"


def test_g_created_by_mistake_fuori_dalle_liste_normali_e_dalle_perse(f):
    persa = _crea(f)
    f["api"]("giorgio").post(f"/api/acquisitions/{persa['id']}/lost",
                             json={"version": persa["version"], "lost_reason": "other"})
    errore = _crea(f, appointment=_appuntamento(f["marta"], h=17))
    r = f["api"]("giorgio").post(f"/api/acquisitions/{errore['id']}/mistake", json={"version": errore["version"]})
    assert r.status_code == 200, r.text
    assert persa["id"] in _acquisizioni(f) and errore["id"] not in _acquisizioni(f)
    assert _acquisizioni(f, statuses="lost") == {persa["id"]}
    assert _acquisizioni(f, mistakes="true") == {errore["id"]}
    voce = f["api"]("giorgio").get("/api/acquisitions", params={"mistakes": "true"}).json()["items"][0]
    assert voce["status_label"] == "Creata per errore"
    # le opzioni non offrono il motivo «per errore» fra le perdite
    assert "created_by_mistake" not in {m["value"] for m in
                                        f["api"]("giorgio").get("/api/acquisitions/options").json()["lost_reasons"]}


# ---------------------------------------------------------------------------
# H / I / J / K - permessi e scope
# ---------------------------------------------------------------------------

def test_h_multi_agenzia(f):
    acq = _crea(f)
    app = _app(f, acq["appointment_id"])
    r = f["api"]("estraneo").post(f"/api/acquisitions/{acq['id']}/mistake", json={"version": acq["version"]})
    assert r.status_code == 404
    r = f["api"]("estraneo").post(f"/api/appointments/{app['id']}/cancel",
                                  json={"version": app["version"], "kind": "mistake"})
    assert r.status_code == 404
    assert _riga(f, acq["id"])["status"] == "appointment_set" and _app(f, app["id"])["status"] == "scheduled"
    assert acq["id"] not in _acquisizioni(f, "estraneo", mistakes="true")


def test_i_agente_solo_sui_propri_record(f):
    """L'agente agisce sui propri (acquisizione assegnata, appuntamento
    assegnato); su quelli di un collega la risposta e' quella gia' in uso per
    Agenda e Acquisizioni: 404, la riga non esiste per lui (D-6)."""
    acq = _crea(f)                                   # assegnata a luca
    app = _app(f, acq["appointment_id"])
    r = f["api"]("marta").post(f"/api/acquisitions/{acq['id']}/mistake", json={"version": acq["version"]})
    assert r.status_code == 404
    r = f["api"]("marta").post(f"/api/appointments/{app['id']}/cancel",
                               json={"version": app["version"], "kind": "mistake"})
    assert r.status_code == 404
    assert _riga(f, acq["id"])["status"] == "appointment_set"
    r = f["api"]("luca").post(f"/api/acquisitions/{acq['id']}/mistake", json={"version": acq["version"]})
    assert r.status_code == 200, r.text
    proprio = _appuntamento_agenda(f, chi="luca", agente=f["luca"], h=14)
    assert _annulla(f, proprio, "mistake", chi="luca").status_code == 200


@pytest.mark.parametrize("chi", ["giorgio", "anna", "supremo"])
def test_j_owner_admin_e_platform_in_acting_consentiti(f, chi):
    acq = _crea(f)
    r = f["api"](chi).post(f"/api/acquisitions/{acq['id']}/mistake", json={"version": acq["version"]})
    assert r.status_code == 200, r.text
    app = _appuntamento_agenda(f, h=15)
    assert _annulla(f, app, "mistake", chi=chi).status_code == 200


def test_k_platform_admin_fuori_acting_non_bypassa_lo_scope(f):
    acq = _crea(f)
    app = _app(f, acq["appointment_id"])
    api = f["api"]("fuori")
    assert api.post(f"/api/acquisitions/{acq['id']}/mistake", json={"version": acq["version"]}).status_code == 403
    assert api.post(f"/api/appointments/{app['id']}/cancel",
                    json={"version": app["version"], "kind": "mistake"}).status_code == 403
    assert api.get("/api/acquisitions", params={"mistakes": "true"}).status_code == 403
    assert api.get("/api/appointments/calendar", params={
        "from": base.futuro(0).isoformat(), "to": base.futuro(23).isoformat(), "mistakes": "true"}).status_code == 403
    assert _riga(f, acq["id"])["status"] == "appointment_set" and _app(f, app["id"])["status"] == "scheduled"


# ---------------------------------------------------------------------------
# L - migration
# ---------------------------------------------------------------------------

def test_l_migration_vincoli_e_down_sicura(f):
    sql = f["sql"]
    app = _appuntamento_agenda(f, h=13)
    # CHECK: la qualifica solo su un annullato, e solo dal catalogo
    with pytest.raises(Exception):
        sql("UPDATE appointments SET cancelled_kind = 'mistake' WHERE id = %s", (app["id"],))
    f["conn"].rollback()
    prova = _crea(f, appointment=_appuntamento(f["marta"], h=20))
    with pytest.raises(Exception):
        sql("UPDATE acquisitions SET status = 'lost', lost_at = NOW(), lost_reason = 'boh' WHERE id = %s",
            (prova["id"],))
    f["conn"].rollback()
    assert _annulla(f, app, "mistake").status_code == 200
    # down: rifiuta con righe qualificate, nulla cambia
    with pytest.raises(Exception) as exc:
        sql(GIU.read_text(encoding="utf-8").replace("BEGIN;", "").replace("COMMIT;", ""))
    assert "084 down" in str(exc.value)
    f["conn"].rollback()
    assert _app(f, app["id"])["cancelled_kind"] == "mistake"
    # senza righe qualificate la down passa e la up si riapplica (idempotente)
    sql("ALTER TABLE appointments DISABLE TRIGGER trg_appointments_guard")
    sql("UPDATE appointments SET cancelled_kind = NULL")
    sql("ALTER TABLE appointments ENABLE TRIGGER trg_appointments_guard")
    sql(GIU.read_text(encoding="utf-8").replace("BEGIN;", "").replace("COMMIT;", ""))
    assert not sql("SELECT 1 FROM information_schema.columns WHERE table_name = 'appointments' "
                   "AND column_name = 'cancelled_kind'")
    assert "created_by_mistake" not in sql("SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                                           "WHERE conname = 'acquisitions_lost_reason_chk'")[0][0]
    # codice deployato senza 084: «per errore» rifiuta leggibile, il resto funziona
    acq = f["api"]("giorgio").get(f"/api/acquisitions/{prova['id']}").json()
    r = f["api"]("giorgio").post(f"/api/acquisitions/{acq['id']}/mistake", json={"version": acq["version"]})
    assert r.status_code == 409 and r.json()["code"] == "MISTAKES_NOT_INSTALLED"
    app2 = _appuntamento_agenda(f, h=19)
    r = _annulla(f, app2, "mistake")
    assert r.status_code == 409 and r.json()["code"] == "MISTAKES_NOT_INSTALLED"
    assert _annulla(f, app2, "client").status_code == 200       # senza 084: annullamento come prima
    assert app2["id"] in _calendario(f, statuses="cancelled")
    sql(MIGRAZIONE.read_text(encoding="utf-8"))
    sql(MIGRAZIONE.read_text(encoding="utf-8"))
    assert sql("SELECT count(*) FROM pg_constraint WHERE conname IN "
               "('appointments_cancelled_kind_chk', 'acquisitions_lost_reason_chk')")[0][0] == 2
    f["conn"].commit()


# ---------------------------------------------------------------------------
# REVIEW 1 - cancelled_kind mai NULL per un NUOVO annullamento
# ---------------------------------------------------------------------------

def test_r1_b_writer_interni_e_legacy_senza_kind_scrivono_agency(f, monkeypatch):
    """Tutti i writer reali di `status='cancelled'` diversi dall'API Agenda."""
    from appointments import lmc15_facade, repository, service
    from appointments.schemas import CancelBody
    from appointments_legacy import stime_dettagliate_import as legacy
    from calendar_sync.inbound import REASON_GOOGLE_CANCELLED
    from core.database import core_cursor

    # 1. annullamento interno (stessa chiamata di calendar_sync.inbound._apply_cancel)
    app = _appuntamento_agenda(f, h=8)
    service.cancel_appointment(_contesto(f), app["id"], CancelBody(
        version=app["version"], reason=REASON_GOOGLE_CANCELLED, follow_up=None))
    assert _app(f, app["id"])["cancelled_kind"] == "agency"

    # 2. facade LMC-15 (POST /inspections/{id}/cancel): il collegamento al
    #    sopralluogo si simula, l'UPDATE sull'appuntamento e' quello vero
    app = _appuntamento_agenda(f, h=9)
    def _riga_bloccata(cur, agency_id, inspection_id):
        return {"status": "scheduled"}, repository.lock_appointment(cur, agency_id, app["id"])
    monkeypatch.setattr(lmc15_facade, "_appuntamento_o_adozione", _riga_bloccata)
    monkeypatch.setattr(lmc15_facade.lmc15, "cancel_inspection_in", lambda *a, **kw: {"ok": True})
    lmc15_facade.cancel_inspection(f["a"], inspection_id=0, reason="x", actor_user_id=f["giorgio"])
    riga = _app(f, app["id"])
    assert riga["status"] == "cancelled" and riga["cancelled_kind"] == "agency"

    # 3. rollback dell'import legacy A30-6 (solo righe importate e intatte)
    with core_cursor(commit=True) as (_, cur):
        importata = repository.insert_appointment(cur, {
            "agency_id": f["a"], "appointment_type": "inspection", "status": "requested",
            "start_at": base.futuro(10), "end_at": base.futuro(11), "source": legacy.SOURCE,
            "source_record_id": legacy.source_key(990001)}, actor_user_id=None)
    with core_cursor(commit=True) as (_, cur):
        esito = legacy.rollback_untouched(cur, apply=True)
    assert esito["cancelled_ids"] == [importata["id"]]
    riga = _app(f, importata["id"])
    assert riga["status"] == "cancelled" and riga["cancelled_kind"] == "agency"

    # 4. rete di sicurezza: qualunque writer che passi da update_appointment
    #    senza qualifica (anche uno futuro) scrive 'agency'
    app = _appuntamento_agenda(f, h=13)
    with core_cursor(commit=True) as (_, cur):
        bloccata = repository.lock_appointment(cur, f["a"], app["id"])
        repository.update_appointment(
            cur, app["id"], {"status": "cancelled", "cancelled_at": repository.db_now(cur)},
            actor_user_id=None, event_type="status_changed", from_status=bloccata["status"],
            azione="prova_review_1")
    assert _app(f, app["id"])["cancelled_kind"] == "agency"

    # nessun annullamento nuovo con qualifica NULL in tutto il test
    assert f["sql"]("SELECT count(*) FROM appointments WHERE status = 'cancelled' "
                    "AND cancelled_kind IS NULL")[0][0] == 0


@pytest.mark.parametrize("kind", ["client", "agency", "mistake"])
def test_r1_cde_kind_esplicito_scritto_tale_e_quale(f, kind):
    app = _appuntamento_agenda(f, h=14)
    r = _annulla(f, app, kind)
    assert r.status_code == 200, r.text
    assert r.json()["cancelled_kind"] == kind
    assert _app(f, app["id"])["cancelled_kind"] == kind
    assert _ultimo_evento(f, app["id"])["cancelled_kind"] == {"da": None, "a": kind}


def test_r1_f_storico_cancelled_con_null_resta_valido_e_leggibile(f):
    """Nessun backfill: una riga annullata prima della 084 (qui scritta come la
    scrive il backfill da `stima_inspections`, l'unico writer che porta
    storico) resta NULL, il CHECK la accetta, e si legge come un annullamento
    normale: lista, calendario con «Annullato», dettaglio. Non e' un errore."""
    from appointments import repository
    from core.database import core_cursor
    with core_cursor(commit=True) as (_, cur):
        storica = repository.insert_appointment(cur, {
            "agency_id": f["a"], "appointment_type": "inspection", "status": "cancelled",
            "start_at": base.futuro(10), "end_at": base.futuro(11),
            "cancelled_at": repository.db_now(cur), "cancelled_reason": "storico",
            "source": "stima_inspections_backfill", "source_record_id": "stima_inspections:990002"},
            actor_user_id=None)
    assert _app(f, storica["id"])["cancelled_kind"] is None
    assert storica["id"] in _calendario(f, statuses="cancelled")
    assert storica["id"] in _lista(f, statuses="cancelled")
    assert storica["id"] not in _calendario(f, mistakes="true")
    assert storica["id"] not in _lista(f, mistakes="true")
    d = f["api"]("giorgio").get(f"/api/appointments/{storica['id']}")
    assert d.status_code == 200 and d.json()["appointment"]["cancelled_kind"] is None
    assert d.json()["appointment"]["status"] == "cancelled"
    # il CHECK la accetta anche se riscritta (nessuna migrazione forzata)
    f["sql"]("UPDATE appointments SET notes = 'riletta' WHERE id = %s", (storica["id"],))
    assert _app(f, storica["id"])["cancelled_kind"] is None


# ---------------------------------------------------------------------------
# REVIEW 1 - atomicita' «creata per errore» + annullamento mistake
# ---------------------------------------------------------------------------

def test_r1_atom_errore_dopo_l_annullamento_rollback_di_tutto(f, monkeypatch):
    """Errore forzato DOPO l'annullamento dell'appuntamento e PRIMA del
    completamento dell'acquisizione: dopo il rollback appuntamento,
    acquisizione ed eventi sono quelli di prima. Fallisce se
    `cancel_locked` committa per conto suo (vedi fail-before)."""
    from acquisitions import service as acq_service
    acq = _crea(f)
    prima = _istantanea(f, acq)
    totali = f["sql"]("SELECT (SELECT count(*) FROM appointment_events), "
                      "(SELECT count(*) FROM acquisition_events)")[0]
    visto = {}
    vero_cancel = acq_service._agenda.cancel_locked

    def cancel_spiato(cur, *a, **kw):
        nuova = vero_cancel(cur, *a, **kw)
        cur.execute("SELECT status, cancelled_kind FROM appointments WHERE id = %s", (nuova["id"],))
        visto["dentro"] = dict(cur.fetchone())
        return nuova

    def esplode(*a, **kw):
        raise RuntimeError("errore forzato prima del completamento dell'acquisizione")

    monkeypatch.setattr(acq_service._agenda, "cancel_locked", cancel_spiato)
    monkeypatch.setattr(acq_service.repository, "update_acquisition", esplode)
    with pytest.raises(RuntimeError, match="errore forzato"):
        f["api"]("giorgio").post(f"/api/acquisitions/{acq['id']}/mistake",
                                 json={"version": acq["version"], "notes": "doppione"})
    # dentro la transazione l'annullamento c'era davvero...
    assert visto["dentro"] == {"status": "cancelled", "cancelled_kind": "mistake"}
    # ...e dopo il rollback non resta nulla
    assert _istantanea(f, acq) == prima
    assert f["sql"]("SELECT (SELECT count(*) FROM appointment_events), "
                    "(SELECT count(*) FROM acquisition_events)")[0] == totali
    app = _app(f, acq["appointment_id"])
    assert app["status"] == "scheduled" and app["cancelled_kind"] is None
    assert _riga(f, acq["id"])["status"] == "appointment_set"


# ---------------------------------------------------------------------------
# FINAL GATE - traccia del platform admin in acting (sistema ESISTENTE)
# ---------------------------------------------------------------------------

def test_gate_platform_admin_in_acting_traccia_come_le_azioni_equivalenti(f):
    """L'audit esistente per una mutazione TENANT fatta in acting e':
    l'ingresso/uscita in `platform_audit_log` (`platform.acting.enter/exit`,
    P28) piu' l'attore sul registro del dominio (`appointment_events`,
    `acquisition_events`). Nessuna azione tenant scrive in
    `platform_audit_log`. «Creato/Creata per errore» lascia la stessa
    traccia delle azioni equivalenti gia' esistenti (annulla, persa)."""
    supremo = f["supremo"]

    def attore_app(app_id):
        return f["sql"]("SELECT actor_user_id, to_status FROM appointment_events "
                        "WHERE appointment_id = %s ORDER BY id DESC LIMIT 1", (app_id,))[0]

    def attori_acq(acq_id):
        return [(r[0], r[1]) for r in f["sql"](
            "SELECT event_type, actor_user_id FROM acquisition_events "
            "WHERE acquisition_id = %s ORDER BY id", (acq_id,))][-2:]

    # A. appuntamento -> creato per errore, e l'equivalente (annullato dal cliente)
    for kind in ("mistake", "client"):
        app = _appuntamento_agenda(f, h=15 if kind == "mistake" else 16)
        assert _annulla(f, app, kind, chi="supremo").status_code == 200
        assert tuple(attore_app(app["id"])) == (supremo, "cancelled")

    # B. acquisizione -> creata per errore, e l'equivalente (persa con annullamento)
    errore = _crea(f)
    r = f["api"]("supremo").post(f"/api/acquisitions/{errore['id']}/mistake", json={"version": errore["version"]})
    assert r.status_code == 200, r.text
    assert attori_acq(errore["id"]) == [("appointment_cancelled", supremo), ("lost", supremo)]
    assert tuple(attore_app(errore["appointment_id"])) == (supremo, "cancelled")

    persa = _crea(f, appointment=_appuntamento(f["marta"], h=17))
    r = f["api"]("supremo").post(f"/api/acquisitions/{persa['id']}/lost", json={
        "version": persa["version"], "lost_reason": "other", "cancel_appointment": True})
    assert r.status_code == 200, r.text
    assert attori_acq(persa["id"]) == [("appointment_cancelled", supremo), ("lost", supremo)]
