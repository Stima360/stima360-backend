"""A31-2 - BUYER VISITS PROJECTION FOUNDATION, su PostgreSQL VERO.

Contratto congelato da A31-1 (D1-D8): una visita acquirente nasce in
`appointments` (`buyer_visit`, `crm_manual`) e `property_visits` ne e' la
PROIEZIONE, scritta dal package `buyer_visits/` nella STESSA transazione
della mutazione dell'Agenda. Migration 078: `property_visits.appointment_id`
(FK RESTRICT, UNIQUE) + guardia separata dal trigger P26.

Fixture riusate, non ricopiate: il database usa-e-getta e il `mondo` di
A30-2, la catena 073-076 e il sync Google ACCESO (solo il namespace) di
A30-13B. Qui si aggiungono: la 077 (per lo slot del booking pubblico), la
tabella `property_visits` presa dal file VERO della 002 (non riscritta) e la
078. Nessuna rete, nessun Google vero.
"""
from __future__ import annotations

import hashlib
import os
import re
from datetime import timedelta

import pytest

from tests import test_a30_2_appointments_postgres as a30_2
from tests.test_a30_2_appointments_postgres import db, http, mondo  # noqa: F401
from tests.test_a30_13b_create_permissions_postgres import agenda_completa, w  # noqa: F401

DSN = os.getenv("P29_TEST_DSN")

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare A31-2")

MIGRAZIONI = a30_2.MIGRAZIONI
VERSIONE = "078_a31_2_buyer_visits_projection"
ore, futuro, chiave = a30_2.ore, a30_2.futuro, a30_2.chiave


def _tabella_property_visits() -> str:
    """La CREATE TABLE della 002, letta dal file vero."""
    testo = (MIGRAZIONI / "002_property_01.sql").read_text(encoding="utf-8")
    trovato = re.search(r"CREATE TABLE IF NOT EXISTS property_visits \(.*?\n\);", testo, re.S)
    assert trovato, "002: CREATE TABLE property_visits non trovata"
    return trovato.group(0)


@pytest.fixture(scope="module")
def schema_a31(agenda_completa):  # noqa: F811
    with agenda_completa["conn"].cursor() as cur:
        cur.execute("ALTER TABLE agencies ADD COLUMN IF NOT EXISTS name VARCHAR(200)")
        cur.execute((MIGRAZIONI / "077_a30_12_public_booking.sql").read_text(encoding="utf-8"))
        cur.execute(_tabella_property_visits())
        cur.execute((MIGRAZIONI / f"{VERSIONE}.sql").read_text(encoding="utf-8"))
    agenda_completa["conn"].commit()
    return agenda_completa


def _pulisci(sql):
    sql("DELETE FROM property_visits")
    sql("DELETE FROM public_booking_rate_limits")
    sql("DELETE FROM public_booking_submissions")
    sql("DELETE FROM public_booking_links")


@pytest.fixture
def v(w, schema_a31, monkeypatch):  # noqa: F811
    """Il mondo di A30-13B (Google ACCESO) + le visite. Le proiezioni si tolgono
    PRIMA della pulizia di A30-2 del test successivo (FK RESTRICT)."""
    sql = w["sql"]
    _pulisci(sql)
    monkeypatch.setenv("PUBLIC_BOOKING_IP_PEPPER", "pepe-a31")
    yield w
    w["conn"].rollback()
    _pulisci(sql)


def _visita(http, v, chi="giorgio", **kw):  # noqa: F811
    corpo = {"appointment_type": "buyer_visit", "property_id": v["casa"],
             "contact_id": v["mario"], "assigned_user_id": v["luca"]}
    corpo.update(kw)
    return a30_2._crea(http, chi, **corpo)


def _pv(v, appointment_id=None):
    sql = ("SELECT id, property_id, contact_id, lead_id, scheduled_at, status, outcome, "
           "feedback, rating, assigned_to, created_by, appointment_id, updated_at "
           "FROM property_visits")
    righe = v["sql"](sql + (" WHERE appointment_id = %s" if appointment_id else "")
                     + " ORDER BY id", (appointment_id,) if appointment_id else None)
    return [dict(r) for r in righe]


def _conta(v, tabella, where="TRUE", par=None):
    return v["sql"](f"SELECT count(*) FROM {tabella} WHERE {where}", par)[0][0]


def _sync(v, appointment_id):
    return v["sql"]("SELECT id, dirty_generation, current_appointment_id, "
                    "chain_root_appointment_id FROM appointment_calendar_sync "
                    "WHERE current_appointment_id = %s", (appointment_id,))


# ---------------------------------------------------------------------------
# A - MIGRATION 078
# ---------------------------------------------------------------------------

def test_a1_migration_078_up_down_reup(v):
    conn = v["conn"]
    conn.autocommit = True
    giu = (MIGRAZIONI / f"{VERSIONE}_down.sql").read_text(encoding="utf-8")
    su = (MIGRAZIONI / f"{VERSIONE}.sql").read_text(encoding="utf-8")
    colonna = ("SELECT count(*) FROM information_schema.columns WHERE table_name='property_visits' "
               "AND column_name='appointment_id'")
    oggetti = ("SELECT (SELECT count(*) FROM pg_trigger WHERE tgname="
               "'trg_property_visits_appointment_guard'), "
               "(SELECT count(*) FROM pg_proc WHERE proname='property_visits_appointment_guard')")
    altri = ("SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
             "WHERE c.relname='property_visits' AND NOT t.tgisinternal "
             "AND t.tgname <> 'trg_property_visits_appointment_guard'")
    riapplicata = False
    try:
        with conn.cursor() as cur:
            cur.execute(altri)
            altri_prima = cur.fetchone()[0]
            cur.execute(giu)
            cur.execute(colonna)
            assert cur.fetchone()[0] == 0
            cur.execute(oggetti)
            assert list(cur.fetchone()) == [0, 0]
            cur.execute(altri)
            assert cur.fetchone()[0] == altri_prima          # gli altri trigger restano
            cur.execute(su)
            riapplicata = True
            cur.execute(colonna)
            assert cur.fetchone()[0] == 1
            cur.execute(oggetti)
            assert list(cur.fetchone()) == [1, 1]
            # fail-closed: una seconda "su" senza "giu" fallisce
            with pytest.raises(Exception):
                cur.execute(su)
    finally:
        if not riapplicata:
            with conn.cursor() as cur:
                cur.execute(colonna)
                if cur.fetchone()[0] == 0:
                    cur.execute(su)
        conn.autocommit = False


def test_a2_schema_fk_restrict_unique_nullable(v):
    r = v["sql"]("""
        SELECT c.data_type, c.is_nullable,
               (SELECT confdeltype FROM pg_constraint WHERE conname='property_visits_appointment_fk'),
               (SELECT contype FROM pg_constraint WHERE conname='property_visits_appointment_unq')
          FROM information_schema.columns c
         WHERE c.table_name='property_visits' AND c.column_name='appointment_id'""")[0]
    assert list(r) == ["bigint", "YES", "r", "u"]


def test_a3_down_rifiuta_se_ci_sono_proiezioni(http, v):  # noqa: F811
    _visita(http, v)
    giu = (MIGRAZIONI / f"{VERSIONE}_down.sql").read_text(encoding="utf-8")
    with pytest.raises(Exception):
        with v["conn"].cursor() as cur:
            cur.execute(giu)
    v["conn"].rollback()
    assert _conta(v, "property_visits", "appointment_id IS NOT NULL") == 1


def _inserisci_pv(v, *, property_id, appointment_id, contact_id=None):
    return v["sql"]("INSERT INTO property_visits (property_id, contact_id, scheduled_at, status, "
                    "appointment_id) VALUES (%s,%s,NOW(),'scheduled',%s) RETURNING id",
                    (property_id, contact_id, appointment_id))[0][0]


def test_a4_guardia_tipo_immobile_agenzia(http, v):  # noqa: F811
    altro = v["sql"]("INSERT INTO properties (agency_id, title) VALUES (%s,'Trilocale') "
                     "RETURNING id", (v["a"],))[0][0]
    straniero = v["sql"]("INSERT INTO properties (agency_id, title) VALUES (%s,'Estero') "
                         "RETURNING id", (v["b"],))[0][0]
    riunione = a30_2._crea(http, assigned_user_id=v["luca"], property_id=v["casa"])
    richiesta = _visita(http, v, status="requested")             # D1: nessuna proiezione
    casi = [
        (v["casa"], riunione["id"]),          # non e' una buyer_visit
        (altro, richiesta["id"]),             # immobile diverso
        (straniero, richiesta["id"]),         # immobile di un'altra agenzia
        (v["casa"], 987654321),               # appuntamento inesistente
    ]
    for immobile, appuntamento in casi:
        with pytest.raises(Exception):
            _inserisci_pv(v, property_id=immobile, appointment_id=appuntamento)
        v["conn"].rollback()
    # il caso buono passa (e poi UNIQUE: mai due proiezioni per un appuntamento)
    _inserisci_pv(v, property_id=v["casa"], appointment_id=richiesta["id"])
    with pytest.raises(Exception):
        _inserisci_pv(v, property_id=v["casa"], appointment_id=richiesta["id"])
    v["conn"].rollback()


def test_a5_appointment_id_si_sposta_solo_al_successore_e_non_si_stacca(http, v):  # noqa: F811
    a = _visita(http, v)
    b = _visita(http, v, assigned_user_id=v["marta"])              # un'altra visita
    pv = _pv(v, a["id"])[0]
    for nuovo in (b["id"], None):
        with pytest.raises(Exception):
            v["sql"]("UPDATE property_visits SET appointment_id=%s WHERE id=%s", (nuovo, pv["id"]))
        v["conn"].rollback()
    # e l'immobile di una riga proiettata non si cambia neppure in SQL diretto
    altro = v["sql"]("INSERT INTO properties (agency_id, title) VALUES (%s,'Attico') "
                     "RETURNING id", (v["a"],))[0][0]
    with pytest.raises(Exception):
        v["sql"]("UPDATE property_visits SET property_id=%s WHERE id=%s", (altro, pv["id"]))
    v["conn"].rollback()
    assert _pv(v, a["id"])[0]["appointment_id"] == a["id"]


def test_a7_guardia_agenzia_indipendente_dal_guard_072(v):
    """Difesa in profondita': anche se un appuntamento con un immobile di
    un'altra agenzia esistesse (guardia 072 aggirata SOLO per costruire il
    caso), la 078 rifiuta di proiettarlo."""
    straniero = v["sql"]("INSERT INTO properties (agency_id, title) VALUES (%s,'Estero') "
                         "RETURNING id", (v["b"],))[0][0]
    v["sql"]("ALTER TABLE appointments DISABLE TRIGGER trg_appointments_guard")
    try:
        storto = v["sql"](
            "INSERT INTO appointments (agency_id, assigned_user_id, appointment_type, status, "
            "start_at, end_at, blocked_range, property_id, created_by_user_id) VALUES "
            "(%s,%s,'buyer_visit','scheduled',%s,%s,tstzrange(%s,%s,'[)'),%s,%s) RETURNING id",
            (v["a"], v["luca"], futuro(10), futuro(11), futuro(10), futuro(11), straniero,
             v["giorgio"]))[0][0]
    finally:
        v["sql"]("ALTER TABLE appointments ENABLE TRIGGER trg_appointments_guard")
    with pytest.raises(Exception) as errore:
        _inserisci_pv(v, property_id=straniero, appointment_id=storto)
    v["conn"].rollback()
    assert "tenancy" in str(errore.value)


def test_a6_legacy_invariato(http, v):  # noqa: F811
    """Una riga legacy (appointment_id NULL) resta com'era: nessun backfill,
    nessuna mutazione, anche con visite nuove sullo stesso immobile."""
    legacy = v["sql"]("INSERT INTO property_visits (property_id, contact_id, scheduled_at, "
                      "status, outcome, feedback, rating, assigned_to, created_by) VALUES "
                      "(%s,%s,NOW()-interval '3 days','completed','ok','Bella casa',4,"
                      "'Mario Agente','legacy') RETURNING *", (v["casa"], v["mario"]))[0]
    a = _visita(http, v)
    http("giorgio").post(f"/api/appointments/{a['id']}/cancel", json={"version": a["version"]})
    dopo = v["sql"]("SELECT * FROM property_visits WHERE id=%s", (legacy["id"],))[0]
    assert dict(dopo) == dict(legacy)
    assert _conta(v, "property_visits") == 2


# ---------------------------------------------------------------------------
# B-D - NASCITA DELLA PROIEZIONE (D1, D2)
# ---------------------------------------------------------------------------

def test_b_requested_nessuna_proiezione(http, v):  # noqa: F811
    r = _visita(http, v, status="requested", assigned_user_id=None)
    assert r["status"] == "requested"
    assert _conta(v, "property_visits") == 0


def test_c_schedule_crea_una_proiezione(http, v):  # noqa: F811
    req = _visita(http, v, status="requested", assigned_user_id=None)
    r = http("giorgio").post(f"/api/appointments/{req['id']}/schedule",
                             json={"version": req["version"], "assigned_user_id": v["luca"],
                                   "start_at": ore(14).isoformat(), "end_at": ore(15).isoformat()})
    assert r.status_code == 200, r.text
    righe = _pv(v)
    assert len(righe) == 1
    pv = righe[0]
    assert pv["appointment_id"] == req["id"] and pv["status"] == "scheduled"
    assert pv["scheduled_at"] == ore(14)
    assert (pv["property_id"], pv["contact_id"]) == (v["casa"], v["mario"])
    assert pv["assigned_to"] == "Luca Test"                       # snapshot D3
    assert pv["created_by"] == f"operator:{v['giorgio']}"         # convenzione P26-5
    assert (pv["outcome"], pv["feedback"], pv["rating"]) == (None, None, None)


def test_d_create_scheduled_una_riga_e_una_proiezione(http, v):  # noqa: F811
    a = _visita(http, v, lead_id=v["lead_mario"])
    assert _conta(v, "appointments") == 1
    pv = _pv(v)
    assert len(pv) == 1 and pv[0]["appointment_id"] == a["id"]
    assert pv[0]["status"] == "scheduled" and pv[0]["lead_id"] == v["lead_mario"]
    assert pv[0]["scheduled_at"] == ore(10)
    assert v["sql"]("SELECT source FROM appointments WHERE id=%s", (a["id"],))[0][0] == "crm_manual"


def test_d2_agente_obbligatorio_gia_garantito(http, v):  # noqa: F811
    """D2: una buyer_visit fissata senza agente non esiste (regola gia'
    esistente, non duplicata): nessun appuntamento, nessuna proiezione."""
    r = http("giorgio").post("/api/appointments", json=a30_2._nuovo(
        appointment_type="buyer_visit", property_id=v["casa"], status="scheduled",
        assigned_user_id=None))
    assert r.status_code == 422, r.text
    assert _conta(v, "appointments") == 0 and _conta(v, "property_visits") == 0


def test_d3_senza_immobile_o_altro_tipo_nessuna_proiezione(http, v):  # noqa: F811
    _visita(http, v, property_id=None)
    a30_2._crea(http, assigned_user_id=v["marta"], property_id=v["casa"])   # seller_meeting
    assert _conta(v, "property_visits") == 0


# ---------------------------------------------------------------------------
# E-H - STATI
# ---------------------------------------------------------------------------

def test_e_confirm(http, v):  # noqa: F811
    a = _visita(http, v)
    r = http("giorgio").post(f"/api/appointments/{a['id']}/confirm", json={"version": a["version"]})
    assert r.status_code == 200
    assert _pv(v, a["id"])[0]["status"] == "confirmed"
    # CONFIRM idempotente: nessuna seconda riga
    http("giorgio").post(f"/api/appointments/{a['id']}/confirm", json={"version": r.json()["version"]})
    assert _conta(v, "property_visits") == 1


def test_f_cancel(http, v):  # noqa: F811
    a = _visita(http, v)
    r = http("giorgio").post(f"/api/appointments/{a['id']}/cancel",
                             json={"version": a["version"], "reason": "Cliente indisponibile"})
    assert r.status_code == 200
    pv = _pv(v, a["id"])[0]
    assert pv["status"] == "cancelled" and pv["outcome"] is None and pv["feedback"] is None


def test_g_complete_updated_at_avanza_e_d4(http, v):  # noqa: F811
    a = _visita(http, v)
    prima = _pv(v, a["id"])[0]["updated_at"]
    v["sql"]("SELECT pg_sleep(0.02)")
    r = http("giorgio").post(f"/api/appointments/{a['id']}/complete",
                             json={"version": a["version"], "outcome_note": "Molto interessato"})
    assert r.status_code == 200, r.text
    pv = _pv(v, a["id"])[0]
    assert pv["status"] == "completed" and pv["updated_at"] > prima
    # D4: la nota di esito resta nell'evento, mai in outcome/feedback/rating
    assert (pv["outcome"], pv["feedback"], pv["rating"]) == (None, None, None)
    assert _conta(v, "appointment_events",
                  "appointment_id=%s AND changes->>'outcome_note'='Molto interessato'",
                  (a["id"],)) == 1


def test_h_no_show(http, v):  # noqa: F811
    a = _visita(http, v)
    r = http("giorgio").post(f"/api/appointments/{a['id']}/no-show", json={"version": a["version"]})
    assert r.status_code == 200, r.text
    assert _pv(v, a["id"])[0]["status"] == "no_show"


# ---------------------------------------------------------------------------
# I-K - REASSIGN, PATCH
# ---------------------------------------------------------------------------

def test_i_reassign_solo_snapshot(http, v):  # noqa: F811
    a = _visita(http, v)
    prima = _pv(v, a["id"])[0]
    r = http("giorgio").post(f"/api/appointments/{a['id']}/reassign",
                             json={"version": a["version"], "assigned_user_id": v["marta"]})
    assert r.status_code == 200, r.text
    dopo = _pv(v, a["id"])[0]
    assert dopo["assigned_to"] == "Marta Test" and dopo["id"] == prima["id"]
    for campo in ("status", "scheduled_at", "contact_id", "property_id", "appointment_id"):
        assert dopo[campo] == prima[campo], campo
    # l'identita' resta assigned_user_id dell'appuntamento, non il testo
    assert v["sql"]("SELECT assigned_user_id FROM appointments WHERE id=%s",
                    (a["id"],))[0][0] == v["marta"]


def test_j_patch_contatto_e_lead(http, v):  # noqa: F811
    a = _visita(http, v, contact_id=v["bruno"])
    r = http("giorgio").patch(f"/api/appointments/{a['id']}",
                              json={"version": a["version"], "contact_id": v["mario"],
                                    "lead_id": v["lead_mario"]})
    assert r.status_code == 200, r.text
    pv = _pv(v, a["id"])[0]
    assert (pv["contact_id"], pv["lead_id"]) == (v["mario"], v["lead_mario"])
    r = http("giorgio").patch(f"/api/appointments/{a['id']}",
                              json={"version": r.json()["version"], "lead_id": None})
    assert r.status_code == 200 and _pv(v, a["id"])[0]["lead_id"] is None


def test_k_patch_immobile_proiettata_409(http, v):  # noqa: F811
    a = _visita(http, v)
    altro = v["sql"]("INSERT INTO properties (agency_id, title) VALUES (%s,'Villa') RETURNING id",
                     (v["a"],))[0][0]
    for valore in (altro, None):
        r = http("giorgio").patch(f"/api/appointments/{a['id']}",
                                  json={"version": a["version"], "property_id": valore})
        assert r.status_code == 409 and r.json()["code"] == "BUYER_VISIT_PROPERTY_LOCKED", r.text
    riga = v["sql"]("SELECT property_id, version FROM appointments WHERE id=%s", (a["id"],))[0]
    assert list(riga) == [v["casa"], a["version"]]                # niente scritto
    assert _pv(v, a["id"])[0]["property_id"] == v["casa"]
    # lo stesso immobile ripetuto non e' un cambio
    r = http("giorgio").patch(f"/api/appointments/{a['id']}",
                              json={"version": a["version"], "property_id": v["casa"]})
    assert r.status_code == 200


def test_k2_immobile_aggiunto_a_visita_fissata_la_proietta(http, v):  # noqa: F811
    a = _visita(http, v, property_id=None)
    assert _conta(v, "property_visits") == 0
    r = http("giorgio").patch(f"/api/appointments/{a['id']}",
                              json={"version": a["version"], "property_id": v["casa"]})
    assert r.status_code == 200, r.text
    assert len(_pv(v, a["id"])) == 1


# ---------------------------------------------------------------------------
# L - RESCHEDULE: STESSA VISITA, APPUNTAMENTO SUCCESSORE
# ---------------------------------------------------------------------------

def test_l_reschedule_stessa_visita_sul_successore(http, v):  # noqa: F811
    a = _visita(http, v)
    pv = _pv(v, a["id"])[0]
    r = http("giorgio").post(f"/api/appointments/{a['id']}/reschedule",
                             json={"version": a["version"], "start_at": ore(16).isoformat(),
                                   "end_at": ore(17).isoformat(), "assigned_user_id": v["marta"]})
    assert r.status_code == 201, r.text
    nuovo = r.json()
    assert nuovo["rescheduled_from_id"] == a["id"]
    righe = _pv(v)
    assert len(righe) == 1                                        # nessuna seconda visita
    assert righe[0]["id"] == pv["id"]                             # STESSO property_visits.id
    assert righe[0]["appointment_id"] == nuovo["id"]
    assert righe[0]["scheduled_at"] == ore(16) and righe[0]["status"] == "scheduled"
    assert righe[0]["assigned_to"] == "Marta Test"
    assert _pv(v, a["id"]) == []                                  # il vecchio non e' piu' collegato
    # un secondo spostamento segue ancora la stessa riga
    r2 = http("giorgio").post(f"/api/appointments/{nuovo['id']}/reschedule",
                              json={"version": nuovo["version"], "start_at": ore(18).isoformat(),
                                    "end_at": ore(19).isoformat()})
    assert r2.status_code == 201
    assert [x["id"] for x in _pv(v)] == [pv["id"]]
    assert _pv(v)[0]["appointment_id"] == r2.json()["id"]


# ---------------------------------------------------------------------------
# M - ATOMICITA'
# ---------------------------------------------------------------------------

def test_m1_proiezione_fallita_annulla_la_creazione(http, v, monkeypatch):  # noqa: F811
    from buyer_visits import errors, projection

    def rotta(*a, **k):
        raise errors.BuyerVisitProjectionIntegrity("forzato dal test")
    monkeypatch.setattr(projection, "insert", rotta)
    r = http("giorgio").post("/api/appointments", json=a30_2._nuovo(
        appointment_type="buyer_visit", property_id=v["casa"], assigned_user_id=v["luca"]))
    assert r.status_code == 409 and r.json()["code"] == "BUYER_VISIT_PROJECTION_INTEGRITY"
    assert _conta(v, "appointments") == 0 and _conta(v, "appointment_events") == 0
    assert _conta(v, "property_visits") == 0 and _conta(v, "appointment_calendar_sync") == 0


def test_m2_proiezione_fallita_annulla_lo_schedule(http, v, monkeypatch):  # noqa: F811
    from buyer_visits import errors, projection

    req = _visita(http, v, status="requested", assigned_user_id=None)
    eventi = _conta(v, "appointment_events")

    def rotta(*a, **k):
        raise errors.BuyerVisitProjectionIntegrity("forzato dal test")
    monkeypatch.setattr(projection, "insert", rotta)
    r = http("giorgio").post(f"/api/appointments/{req['id']}/schedule",
                             json={"version": req["version"], "assigned_user_id": v["luca"],
                                   "start_at": ore(14).isoformat(), "end_at": ore(15).isoformat()})
    assert r.status_code == 409
    riga = v["sql"]("SELECT status, version, assigned_user_id FROM appointments WHERE id=%s",
                    (req["id"],))[0]
    assert list(riga) == ["requested", req["version"], None]
    assert _conta(v, "appointment_events") == eventi and _conta(v, "property_visits") == 0


def test_m3_errore_dopo_la_proiezione_annulla_anche_la_proiezione(http, v, monkeypatch):  # noqa: F811
    """Se fallisce il passo DOPO (mark dirty Google), la proiezione gia'
    scritta sparisce con tutto il resto: stesso cursore, nessun commit
    proprio del package."""
    from calendar_sync import integration

    def rotta(*a, **k):
        raise RuntimeError("google giu'")
    monkeypatch.setattr(integration, "on_appointment_mutation", rotta)
    with pytest.raises(RuntimeError):
        v["service"].create_appointment(v["ctx"]("giorgio"), _corpo(
            appointment_type="buyer_visit", property_id=v["casa"], contact_id=v["mario"],
            assigned_user_id=v["luca"]))
    assert _conta(v, "appointments") == 0 and _conta(v, "property_visits") == 0


def _corpo(**kw):
    from appointments.schemas import AppointmentCreate
    return AppointmentCreate(**a30_2._nuovo(**kw))


# ---------------------------------------------------------------------------
# N - DUPLICATI
# ---------------------------------------------------------------------------

def test_n_nessuna_seconda_proiezione(http, v):  # noqa: F811
    corpo = a30_2._nuovo(appointment_type="buyer_visit", property_id=v["casa"],
                         assigned_user_id=v["luca"])
    r1 = http("giorgio").post("/api/appointments", json=corpo)
    r2 = http("giorgio").post("/api/appointments", json=corpo)            # replica idempotente
    assert (r1.status_code, r2.status_code) == (201, 200)
    assert _conta(v, "property_visits") == 1
    # l'hook ripetuto sulla stessa riga non raddoppia
    from appointments import repository
    from buyer_visits import integration
    with v["conn"].cursor() as cur:
        riga = repository.get_appointment(cur, v["a"], r1.json()["id"])
        integration.on_create(cur, v["a"], riga, actor_user_id=v["giorgio"])
        integration.on_status(cur, v["a"], riga, actor_user_id=v["giorgio"])
    v["conn"].commit()
    assert _conta(v, "property_visits") == 1


# ---------------------------------------------------------------------------
# O - TENANT
# ---------------------------------------------------------------------------

def test_o_cross_agency_rifiutato(http, v):  # noqa: F811
    straniero = v["sql"]("INSERT INTO properties (agency_id, title) VALUES (%s,'Estero') "
                         "RETURNING id", (v["b"],))[0][0]
    r = http("giorgio").post("/api/appointments", json=a30_2._nuovo(
        appointment_type="buyer_visit", property_id=straniero, assigned_user_id=v["luca"]))
    assert r.status_code in (404, 422), r.text
    assert _conta(v, "appointments") == 0 and _conta(v, "property_visits") == 0
    # e in SQL diretto la guardia 078 rifiuta l'accoppiamento fra agenzie
    a = _visita(http, v)
    with pytest.raises(Exception):
        _inserisci_pv(v, property_id=straniero, appointment_id=a["id"])
    v["conn"].rollback()


# ---------------------------------------------------------------------------
# P - OCCUPAZIONE, CONFLITTI, BOOKING PUBBLICO
# ---------------------------------------------------------------------------

def test_p1_occupa_e_blocca_le_sovrapposizioni(http, v):  # noqa: F811
    from appointments import repository
    a = _visita(http, v, start_at=futuro(10).isoformat(), end_at=futuro(11).isoformat())
    with v["conn"].cursor() as cur:
        occupato = repository.busy_intervals(cur, assigned_user_id=v["luca"],
                                             date_from=futuro(8), date_to=futuro(13))
    v["conn"].commit()
    assert occupato == [(futuro(10), futuro(11))]
    r = http("giorgio").post("/api/appointments", json=a30_2._nuovo(
        assigned_user_id=v["luca"], start_at=futuro(10, 30).isoformat(),
        end_at=futuro(11, 30).isoformat()))
    assert r.status_code == 409 and r.json()["code"] == "APPOINTMENT_CONFLICT"
    assert a["id"] in [c["id"] for c in r.json()["conflicts"]]


def test_p2_toglie_lo_slot_del_booking_pubblico(http, v):  # noqa: F811
    from public_booking import service as pubblico
    giorno = futuro(10)
    v["sql"]("INSERT INTO agent_working_hours (agency_id, user_id, day_of_week, start_minute, "
             "end_minute) VALUES (%s,%s,%s,480,1200)", (v["a"], v["luca"], giorno.isoweekday()))
    token = "a31-token-" + "x" * 30
    v["sql"]("INSERT INTO public_booking_links (agency_id, assigned_user_id, token_hash, "
             "appointment_type, duration_minutes) VALUES (%s,%s,%s,'call',60)",
             (v["a"], v["luca"], hashlib.sha256(token.encode()).hexdigest()))

    def inizi():
        esito = pubblico.get_public_slots(token, date_from=futuro(8), date_to=futuro(13),
                                          client_ip="203.0.113.7")
        return {s["start_at"] for s in esito["slots"]}

    assert futuro(10) in inizi()
    _visita(http, v, start_at=futuro(10).isoformat(), end_at=futuro(11).isoformat())
    dopo = inizi()
    for bloccato in (futuro(9, 15), futuro(10), futuro(10, 45)):
        assert bloccato not in dopo, bloccato
    assert futuro(11) in dopo


# ---------------------------------------------------------------------------
# GOOGLE - REGRESSIONE (calendar_sync non e' toccato)
# ---------------------------------------------------------------------------

def test_g1_dirty_una_volta_per_mutazione_e_stessa_catena(http, v):  # noqa: F811
    """Stessa sequenza su una visita proiettata e su un appuntamento di
    controllo (senza proiezione): la riga di sync evolve IDENTICA, +1 per
    mutazione (074: nasce a 1, il primo mark la porta a 2), una sola catena."""
    def generazioni(radice):
        return v["sql"]("SELECT dirty_generation, current_appointment_id FROM "
                        "appointment_calendar_sync WHERE chain_root_appointment_id=%s",
                        (radice,))

    visita = _visita(http, v)
    controllo = a30_2._crea(http, assigned_user_id=v["marta"])
    passi = []
    for radice, riga in ((visita["id"], visita), (controllo["id"], controllo)):
        storia = [generazioni(radice)[0][0]]
        r = http("giorgio").post(f"/api/appointments/{riga['id']}/reschedule",
                                 json={"version": riga["version"], "start_at": ore(16).isoformat(),
                                       "end_at": ore(17).isoformat()})
        nuovo = r.json()
        righe = generazioni(radice)
        assert len(righe) == 1 and righe[0][1] == nuovo["id"]     # stessa catena, riga viva nuova
        storia.append(righe[0][0])
        http("giorgio").post(f"/api/appointments/{nuovo['id']}/cancel",
                             json={"version": nuovo["version"]})
        storia.append(generazioni(radice)[0][0])
        passi.append(storia)
    assert passi[0] == passi[1] == [2, 3, 4]
    assert _conta(v, "appointment_calendar_sync") == 2


def test_g2_contenuto_google_invariato(http, v):  # noqa: F811
    from calendar_sync import service as gcal
    a = _visita(http, v)
    riga = v["sql"]("SELECT * FROM appointments WHERE id=%s", (a["id"],))[0]
    payload = gcal.build_payload(dict(riga), agency_id=v["a"], chain_root_appointment_id=a["id"],
                                 event_id="s360test")
    testo = repr(payload)
    assert "Visita" in testo
    for vietato in ("Mario", "Bilocale", "Luca", "operator:", "property"):
        assert vietato not in testo, vietato
