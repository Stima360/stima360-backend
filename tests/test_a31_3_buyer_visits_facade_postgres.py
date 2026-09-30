"""A31-3 - BUYER VISITS BUY/PROPERTY FACADE, su PostgreSQL VERO.

Una visita da svolgere nasce nell'Agenda (`appointments`, `buyer_visit`,
`crm_manual`) anche quando la si programma da BUY ("Visita programmata" sul
match) o da PROPERTY ("Programma visita"): le facade chiamano
`appointments.service.create_appointment_with_cursor` sul PROPRIO cursore,
la proiezione A31-2 scrive `property_visits`, e BUY aggiunge interazione,
stato del match e storico nello STESSO commit.

Banco: il mondo di A30-2/A30-13B + la 077 e la 078 (fixture di A31-2), piu'
le tabelle BUY prese dai file VERI delle migration 004/005/006/007 (non
riscritte). Due soli adattamenti del banco, dichiarati: la colonna
`buy_requests.agency_id` (037) aggiunta come colonna semplice con FK, e le
FK verso tabelle che questo banco non ha (`tasks`, `owner_accounts`) tolte
dal DDL estratto.
"""
from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timedelta, timezone

import pytest

from tests import test_a30_2_appointments_postgres as a30_2
from tests.test_a30_2_appointments_postgres import db, http, mondo  # noqa: F401
from tests.test_a30_13b_create_permissions_postgres import agenda_completa, w  # noqa: F401
from tests.test_a31_2_buyer_visits_postgres import schema_a31, v  # noqa: F401

DSN = os.getenv("P29_TEST_DSN")

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare A31-3")

MIGRAZIONI = a30_2.MIGRAZIONI
ore, futuro, chiave = a30_2.ore, a30_2.futuro, a30_2.chiave


def _estrai(file, nome):
    testo = (MIGRAZIONI / file).read_text(encoding="utf-8")
    trovato = re.search(rf"CREATE TABLE {nome} \(.*?\n\);", testo, re.S)
    assert trovato, (file, nome)
    return trovato.group(0)


def _ddl_buy() -> str:
    storico = _estrai("006_buy_02.sql", "buy_request_history").replace(
        "task_id BIGINT REFERENCES tasks(id) ON DELETE SET NULL", "task_id BIGINT")
    feedback = _estrai("010_owner_02_p1.sql", "owner_visit_feedback_publications").replace(
        "owner_account_id BIGINT\n        REFERENCES owner_accounts(id) ON DELETE CASCADE",
        "owner_account_id BIGINT")
    commerciale = re.search(r"ALTER TABLE matches DROP CONSTRAINT IF EXISTS "
                            r"matches_commercial_status_check;.*?\)\);",
                            (MIGRAZIONI / "007_match_02.sql").read_text(encoding="utf-8"),
                            re.S).group(0)
    return "\n".join((
        _estrai("004_buy_01.sql", "buy_requests"),
        "ALTER TABLE buy_requests ADD COLUMN agency_id BIGINT REFERENCES agencies(id) "
        "ON DELETE RESTRICT, ADD COLUMN next_action_at TIMESTAMPTZ, "
        "ADD COLUMN next_action_note TEXT;",
        _estrai("005_match_01.sql", "match_runs"),
        _estrai("005_match_01.sql", "matches"),
        commerciale,
        _estrai("006_buy_02.sql", "buy_request_interactions"),
        storico,
        feedback,
    ))


@pytest.fixture(scope="module")
def schema_buy(schema_a31):  # noqa: F811
    with schema_a31["conn"].cursor() as cur:
        cur.execute(_ddl_buy())
    schema_a31["conn"].commit()
    return schema_a31


def _pulisci_buy(sql):
    for tabella in ("owner_visit_feedback_publications", "buy_request_history",
                    "buy_request_interactions", "matches", "match_runs", "buy_requests"):
        sql(f"DELETE FROM {tabella}")


@pytest.fixture
def b(v, schema_buy):  # noqa: F811
    """Una richiesta d'acquisto di Mario (agenzia A) con un match sul
    Bilocale, e un client HTTP con i router VERI BUY + PROPERTY + Agenda."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from appointments.router import router as agenda
    from buy.router import router as buy
    from operator_auth.context import OperatorContext
    from operator_auth.dependencies import legacy_basic_agency_context, require_operator
    from property.router import router as immobili

    sql = v["sql"]
    _pulisci_buy(sql)
    richiesta = sql("INSERT INTO buy_requests (agency_id, contact_id, lead_id, title, status) "
                    "VALUES (%s,%s,%s,'Cerca bilocale','active') RETURNING id",
                    (v["a"], v["mario"], v["lead_mario"]))[0][0]
    match = sql("INSERT INTO matches (buy_request_id, property_id, compatibility_status, "
                "score_total, match_class, algorithm_version, commercial_status) "
                "VALUES (%s,%s,'compatible',80,'strong','test','interested') RETURNING id",
                (richiesta, v["casa"]))[0][0]
    ruoli = {"giorgio": (v["giorgio"], v["a"], "agency_owner", False),
             "anna": (v["anna"], v["a"], "agency_admin", False),
             "luca": (v["luca"], v["a"], "agent", False),
             "marta": (v["marta"], v["a"], "agent", False),
             "estraneo": (v["estraneo"], v["b"], "agent", False),
             "supremo": (v["supremo"], v["a"], None, True)}
    stato = {}

    def contesto():
        user_id, agenzia, ruolo, platform = ruoli[stato["chi"]]
        return OperatorContext(user_id=user_id, agency_id=agenzia, role=ruolo,
                               is_platform_admin=platform, session_id=None,
                               auth_channel="operator_session")

    app = FastAPI()
    for r in (agenda, buy, immobili):
        app.include_router(r)
    app.dependency_overrides[legacy_basic_agency_context] = contesto
    app.dependency_overrides[require_operator] = contesto
    client = TestClient(app)

    def api(chi):
        stato["chi"] = chi
        return client

    def ctx(chi):
        stato["chi"] = chi
        return contesto()

    yield {**v, "richiesta": richiesta, "match": match, "api": api, "octx": ctx}
    v["conn"].rollback()
    _pulisci_buy(sql)


def _conta(b, tabella, where="TRUE", par=None):
    return b["sql"](f"SELECT count(*) FROM {tabella} WHERE {where}", par)[0][0]


def _decidi(b, chi="luca", quando=None, **kw):
    corpo = {"action": "visit_scheduled",
             "scheduled_at": (quando or futuro(10)).isoformat()}
    corpo.update(kw)
    return b["api"](chi).post(
        f"/api/buy/requests/{b['richiesta']}/matches/{b['match']}/decision", json=corpo)


def _match(b):
    return b["sql"]("SELECT commercial_status FROM matches WHERE id=%s", (b["match"],))[0][0]


def _istantanea(b):
    return {t: _conta(b, t) for t in ("appointments", "appointment_events", "property_visits",
                                      "buy_request_interactions", "buy_request_history",
                                      "appointment_calendar_sync")} | {"match": _match(b)}


# ---------------------------------------------------------------------------
# A - BUY: "Visita programmata" -> appuntamento + proiezione + interazione
# ---------------------------------------------------------------------------

def test_a_buy_agente_crea_appuntamento_proiezione_interazione(b):
    r = _decidi(b, notes="porta la planimetria")
    assert r.status_code == 201, r.text
    interazione = r.json()
    # risposta invariata: la riga dell'interazione
    assert interazione["interaction_type"] == "visit_scheduled"
    assert interazione["match_id"] == b["match"] and interazione["property_id"] == b["casa"]
    assert interazione["notes"] == "porta la planimetria"
    app = b["sql"]("SELECT * FROM appointments")
    assert len(app) == 1
    a = app[0]
    assert (a["appointment_type"], a["status"], a["source"]) == ("buyer_visit", "scheduled",
                                                                 "crm_manual")
    assert a["assigned_user_id"] == b["luca"] and a["created_by_user_id"] == b["luca"]
    assert (a["contact_id"], a["lead_id"], a["property_id"]) == (b["mario"], b["lead_mario"],
                                                                 b["casa"])
    assert a["start_at"] == futuro(10) and a["end_at"] == futuro(11)
    pv = b["sql"]("SELECT * FROM property_visits")
    assert len(pv) == 1 and pv[0]["appointment_id"] == a["id"]
    assert interazione["property_visit_id"] == pv[0]["id"]
    assert (pv[0]["contact_id"], pv[0]["lead_id"], pv[0]["scheduled_at"]) == (
        b["mario"], b["lead_mario"], futuro(10))
    assert pv[0]["assigned_to"] == "Luca Test" and pv[0]["created_by"] == f"operator:{b['luca']}"
    assert _conta(b, "buy_request_interactions") == 1
    assert _match(b) == "visit_scheduled"
    storia = b["sql"]("SELECT event_type, match_id, property_id FROM buy_request_history")
    assert [tuple(s) for s in storia] == [("visit_scheduled", b["match"], b["casa"])]
    eventi = b["sql"]("SELECT event_type FROM appointment_events WHERE appointment_id=%s",
                      (a["id"],))
    assert [e[0] for e in eventi] == ["created"]


def test_a2_owner_con_agente_esplicito(b):
    r = _decidi(b, chi="giorgio", assigned_user_id=b["marta"])
    assert r.status_code == 201, r.text
    a = b["sql"]("SELECT assigned_user_id, created_by_user_id FROM appointments")[0]
    assert list(a) == [b["marta"], b["giorgio"]]
    assert _conta(b, "property_visits", "appointment_id IS NOT NULL") == 1


# ---------------------------------------------------------------------------
# B - IDEMPOTENZA
# ---------------------------------------------------------------------------

def test_b_doppio_invio_stessa_client_request_id(b):
    k = chiave()
    r1 = _decidi(b, client_request_id=k)
    r2 = _decidi(b, client_request_id=k)
    assert (r1.status_code, r2.status_code) == (201, 201)
    assert r1.json()["id"] == r2.json()["id"]
    assert _istantanea(b) == {"appointments": 1, "appointment_events": 1, "property_visits": 1,
                              "buy_request_interactions": 1, "buy_request_history": 1,
                              "appointment_calendar_sync": 1, "match": "visit_scheduled"}
    assert b["sql"]("SELECT dirty_generation FROM appointment_calendar_sync")[0][0] == 2


def test_b2_doppio_invio_senza_chiave_stesso_orario(b):
    r1 = _decidi(b)
    r2 = _decidi(b)
    assert r1.json()["id"] == r2.json()["id"]
    assert _conta(b, "appointments") == 1 and _conta(b, "buy_request_interactions") == 1


def test_b3_dopo_annullamento_si_riprenota_lo_stesso_match(b):
    k = chiave()
    _decidi(b, client_request_id=k)
    a = b["sql"]("SELECT id, version FROM appointments")[0]
    r = b["api"]("luca").post(f"/api/appointments/{a['id']}/cancel",
                              json={"version": a["version"]})
    assert r.status_code == 200, r.text
    # stesso orario, senza chiave: la visita annullata NON la blocca
    r2 = _decidi(b)
    assert r2.status_code == 201, r2.text
    # e una chiave nuova e' una prenotazione nuova
    r3 = _decidi(b, quando=futuro(15), client_request_id=chiave())
    assert r3.status_code == 201, r3.text
    assert _conta(b, "appointments") == 3 and _conta(b, "property_visits") == 3
    assert _conta(b, "buy_request_interactions") == 3


def test_b4_stessa_chiave_con_dati_diversi_rifiutata(b):
    k = chiave()
    _decidi(b, client_request_id=k)
    r = _decidi(b, quando=futuro(15), client_request_id=k)
    assert r.status_code == 409
    assert _conta(b, "appointments") == 1 and _conta(b, "buy_request_interactions") == 1


# ---------------------------------------------------------------------------
# C - CONFLITTO
# ---------------------------------------------------------------------------

def test_c_sovrapposizione_nessuna_scrittura(b):
    a30_2._crea(b["api"], assigned_user_id=b["luca"],
                start_at=futuro(10).isoformat(), end_at=futuro(11).isoformat())
    prima = _istantanea(b)
    r = _decidi(b, quando=futuro(10, 30))
    assert r.status_code == 409, r.text
    assert _istantanea(b) == prima
    assert _match(b) == "interested"


# ---------------------------------------------------------------------------
# D - TENANT
# ---------------------------------------------------------------------------

def test_d_cross_tenant_rifiutato(b):
    prima = _istantanea(b)
    r = _decidi(b, chi="estraneo")
    assert r.status_code == 404
    r = _decidi(b, chi="giorgio", assigned_user_id=b["estraneo"])
    assert r.status_code == 400 and "membro attivo" in r.json()["detail"]
    assert _istantanea(b) == prima


# ---------------------------------------------------------------------------
# E - D8: l'interazione generica non crea `visit_scheduled`
# ---------------------------------------------------------------------------

def test_e_interazione_generica_visit_scheduled_rifiutata(b):
    url = f"/api/buy/requests/{b['richiesta']}/interactions"
    r = b["api"]("giorgio").post(url, json={"match_id": b["match"],
                                            "interaction_type": "visit_scheduled"})
    assert r.status_code == 409 and "Agenda" in r.json()["detail"]
    assert _conta(b, "buy_request_interactions") == 0 and _match(b) == "interested"
    # le altre restano invariate
    r = b["api"]("giorgio").post(url, json={"match_id": b["match"],
                                            "interaction_type": "visit_requested"})
    assert r.status_code == 201 and _match(b) == "visit_requested"
    # e non la si ottiene nemmeno trasformando un'interazione esistente
    r = b["api"]("giorgio").patch(f"/api/buy/interactions/{r.json()['id']}",
                                  json={"interaction_type": "visit_scheduled"})
    assert r.status_code == 409
    assert _conta(b, "buy_request_interactions", "interaction_type='visit_scheduled'") == 0


# ---------------------------------------------------------------------------
# F - PROPERTY: "Programma visita"
# ---------------------------------------------------------------------------

def _visita_ui(b, **kw):
    """Il payload che la OS Shell costruisce (immobile-dettaglio.js)."""
    corpo = {"status": "scheduled", "contact_id": b["mario"], "outcome": None,
             "feedback": None, "assigned_to": None, "lead_id": None,
             "scheduled_at": futuro(10).isoformat(), "rating": None}
    corpo.update(kw)
    return corpo


def test_f_property_create_facade(b):
    legacy = b["api"]("giorgio").post(f"/api/property/properties/{b['casa']}/visits",
                                      json=_visita_ui(b, status="completed",
                                                      scheduled_at=ore(10).isoformat()))
    assert legacy.status_code == 201
    r = b["api"]("luca").post(f"/api/property/properties/{b['casa']}/visits",
                              json=_visita_ui(b))
    assert r.status_code == 201, r.text
    visita = r.json()
    assert set(legacy.json()) == set(visita)            # stessa forma di risposta
    a = b["sql"]("SELECT * FROM appointments")
    assert len(a) == 1 and visita["appointment_id"] == a[0]["id"]
    assert (a[0]["appointment_type"], a[0]["assigned_user_id"], a[0]["property_id"],
            a[0]["contact_id"]) == ("buyer_visit", b["luca"], b["casa"], b["mario"])
    assert _conta(b, "property_visits", "appointment_id IS NOT NULL") == 1
    assert _conta(b, "property_visits") == 2                  # + la visita storica
    assert legacy.json()["appointment_id"] is None


def test_f2_property_owner_esplicito_e_campi_legacy_in_creazione(b):
    r = b["api"]("giorgio").post(f"/api/property/properties/{b['casa']}/visits",
                                 json=_visita_ui(b, assigned_user_id=b["marta"],
                                                 status="confirmed", feedback="da richiamare"))
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "confirmed" and r.json()["feedback"] == "da richiamare"
    a = b["sql"]("SELECT assigned_user_id, status FROM appointments")[0]
    assert list(a) == [b["marta"], "confirmed"]


def test_f3_property_senza_fuso_errore_pulito(b):
    r = b["api"]("luca").post(f"/api/property/properties/{b['casa']}/visits",
                              json=_visita_ui(b, scheduled_at="2031-01-10T10:00:00"))
    assert r.status_code == 400 and "fuso" in r.json()["detail"]
    assert _conta(b, "appointments") == 0 and _conta(b, "property_visits") == 0


# ---------------------------------------------------------------------------
# G/H - riga PROIETTATA: PATCH solo campi legacy, DELETE vietato
# ---------------------------------------------------------------------------

def _proiettata(b):
    r = b["api"]("luca").post(f"/api/property/properties/{b['casa']}/visits",
                              json=_visita_ui(b))
    assert r.status_code == 201, r.text
    return r.json()


def test_g_patch_proiettata_solo_esito_feedback_voto(b):
    pv = _proiettata(b)
    url = f"/api/property/visits/{pv['id']}"
    # il form rimanda TUTTO il record: stessi valori + esito -> ammesso
    r = b["api"]("giorgio").patch(url, json=_visita_ui(
        b, assigned_to=pv["assigned_to"], outcome="interessato", feedback="luminoso",
        rating=4, scheduled_at=datetime.fromisoformat(pv["scheduled_at"]).isoformat()))
    assert r.status_code == 200, r.text
    assert (r.json()["outcome"], r.json()["feedback"], r.json()["rating"]) == (
        "interessato", "luminoso", 4)
    cambi = ({"scheduled_at": futuro(15).isoformat()}, {"status": "completed"},
             {"status": "cancelled"}, {"contact_id": b["bruno"]},
             {"lead_id": b["lead_mario"]}, {"assigned_to": "Qualcun altro"})
    for cambio in cambi:
        r = b["api"]("giorgio").patch(url, json=cambio)
        assert r.status_code == 409 and "Agenda" in r.json()["detail"], cambio
    riga = b["sql"]("SELECT scheduled_at, status, contact_id, lead_id, assigned_to "
                    "FROM property_visits WHERE id=%s", (pv["id"],))[0]
    assert list(riga) == [futuro(10), "scheduled", b["mario"], None, "Luca Test"]


def test_h_delete_proiettata_409(b):
    pv = _proiettata(b)
    r = b["api"]("giorgio").delete(f"/api/property/visits/{pv['id']}")
    assert r.status_code == 409 and "Agenda" in r.json()["detail"]
    assert _conta(b, "property_visits", "id=%s", (pv["id"],)) == 1


# ---------------------------------------------------------------------------
# I/J - riga LEGACY
# ---------------------------------------------------------------------------

def _legacy(b, quando, stato):
    return b["sql"]("INSERT INTO property_visits (property_id, contact_id, scheduled_at, "
                    "status) VALUES (%s,%s,%s,%s) RETURNING id",
                    (b["casa"], b["mario"], quando, stato))[0][0]


def test_i_patch_legacy_storico_e_divieto_di_riapertura(b):
    passata = _legacy(b, ore(10), "scheduled")
    url = f"/api/property/visits/{passata}"
    r = b["api"]("giorgio").patch(url, json={"status": "completed", "outcome": "fatta",
                                             "rating": 5})
    assert r.status_code == 200 and r.json()["status"] == "completed"
    r = b["api"]("giorgio").patch(url, json={"scheduled_at": ore(11).isoformat()})
    assert r.status_code == 200                                   # storico: invariato
    # futura + aperta in un colpo solo -> 409
    r = b["api"]("giorgio").patch(url, json={"scheduled_at": futuro(10).isoformat(),
                                             "status": "scheduled"})
    assert r.status_code == 409 and "Agenda" in r.json()["detail"]
    riga = b["sql"]("SELECT status, scheduled_at FROM property_visits WHERE id=%s",
                    (passata,))[0]
    assert list(riga) == ["completed", ore(11)]
    # una legacy gia' futura/aperta: esito con gli stessi valori ammesso, spostarla no
    aperta = _legacy(b, futuro(9), "scheduled")
    url = f"/api/property/visits/{aperta}"
    r = b["api"]("giorgio").patch(url, json={"scheduled_at": futuro(9).isoformat(),
                                             "status": "scheduled", "feedback": "ok"})
    assert r.status_code == 200, r.text
    r = b["api"]("giorgio").patch(url, json={"scheduled_at": futuro(12).isoformat()})
    assert r.status_code == 409
    # chiuderla resta possibile
    r = b["api"]("giorgio").patch(url, json={"status": "cancelled"})
    assert r.status_code == 200


def test_j_delete_legacy_invariato_e_restrict_owner_feedback(b):
    libera = _legacy(b, ore(10), "completed")
    r = b["api"]("giorgio").delete(f"/api/property/visits/{libera}")
    assert r.status_code == 204
    assert _conta(b, "property_visits", "id=%s", (libera,)) == 0
    pubblicata = _legacy(b, ore(10), "completed")
    b["sql"]("INSERT INTO owner_visit_feedback_publications (property_visit_id, category, "
             "public_summary) VALUES (%s,'general','Visita positiva')", (pubblicata,))
    from property import repository
    with pytest.raises(Exception) as exc:
        repository.delete_visit(b["octx"]("giorgio"), pubblicata)
    assert "foreign key" in str(exc.value).lower()
    assert _conta(b, "property_visits", "id=%s", (pubblicata,)) == 1


# ---------------------------------------------------------------------------
# K - ROLLBACK
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("dove", ["storico", "interazione"])
def test_k_errore_dopo_l_appuntamento_annulla_tutto(b, monkeypatch, dove):
    from buy import repository

    prima = _istantanea(b)

    def rotta(*a, **k):
        raise RuntimeError("forzato dal test")
    if dove == "storico":
        monkeypatch.setattr(repository, "history", rotta)
    else:
        monkeypatch.setattr(repository, "_insert_visit_scheduled", rotta)
    with pytest.raises(RuntimeError):
        repository.schedule_match_visit_scoped(
            b["octx"]("luca"), b["richiesta"], b["match"], {"scheduled_at": futuro(10)})
    assert _istantanea(b) == prima


def test_k2_errore_della_proiezione_annulla_tutto(b, monkeypatch):
    from buyer_visits import errors, projection

    def rotta(*a, **k):
        raise errors.BuyerVisitProjectionIntegrity("forzato dal test")
    monkeypatch.setattr(projection, "insert", rotta)
    prima = _istantanea(b)
    r = _decidi(b)
    assert r.status_code == 409
    assert _istantanea(b) == prima


# ---------------------------------------------------------------------------
# L - GOOGLE
# ---------------------------------------------------------------------------

def test_l_google_dirty_una_volta_e_contenuto_neutro(b):
    from calendar_sync import service as gcal
    _decidi(b)
    visita = b["sql"]("SELECT * FROM appointments")[0]
    controllo = a30_2._crea(b["api"], assigned_user_id=b["marta"])
    righe = b["sql"]("SELECT chain_root_appointment_id, dirty_generation "
                     "FROM appointment_calendar_sync ORDER BY id")
    assert [tuple(r) for r in righe] == [(visita["id"], 2), (controllo["id"], 2)]
    payload = gcal.build_payload(dict(visita), agency_id=b["a"],
                                 chain_root_appointment_id=visita["id"], event_id="s360test")
    testo = repr(payload)
    assert "Visita" in testo
    for vietato in ("Mario", "Rossi", "333", "mario@example.it", "Bilocale", "Alba",
                    "Luca", "operator:"):
        assert vietato not in testo, vietato


# ---------------------------------------------------------------------------
# M - OCCUPAZIONE E BOOKING PUBBLICO
# ---------------------------------------------------------------------------

def test_m_la_visita_buy_occupa_l_agente_e_toglie_lo_slot_pubblico(b, monkeypatch):
    from appointments import repository
    from public_booking import service as pubblico

    giorno = futuro(10)
    b["sql"]("INSERT INTO agent_working_hours (agency_id, user_id, day_of_week, start_minute, "
             "end_minute) VALUES (%s,%s,%s,480,1200)", (b["a"], b["luca"], giorno.isoweekday()))
    token = "a31-3-token-" + "y" * 30
    b["sql"]("INSERT INTO public_booking_links (agency_id, assigned_user_id, token_hash, "
             "appointment_type, duration_minutes) VALUES (%s,%s,%s,'call',60)",
             (b["a"], b["luca"], hashlib.sha256(token.encode()).hexdigest()))

    def inizi():
        esito = pubblico.get_public_slots(token, date_from=futuro(8), date_to=futuro(13),
                                          client_ip="203.0.113.9")
        return {s["start_at"] for s in esito["slots"]}

    assert futuro(10) in inizi()
    assert _decidi(b).status_code == 201
    with b["conn"].cursor() as cur:
        occupato = repository.busy_intervals(cur, assigned_user_id=b["luca"],
                                             date_from=futuro(8), date_to=futuro(13))
    b["conn"].commit()
    assert occupato == [(futuro(10), futuro(11))]
    dopo = inizi()
    assert futuro(10) not in dopo and futuro(11) in dopo
    r = b["api"]("giorgio").post("/api/appointments", json=a30_2._nuovo(
        assigned_user_id=b["luca"], start_at=futuro(10, 30).isoformat(),
        end_at=futuro(11, 30).isoformat()))
    assert r.status_code == 409 and r.json()["code"] == "APPOINTMENT_CONFLICT"


# ---------------------------------------------------------------------------
# N - PERMESSI
# ---------------------------------------------------------------------------

def test_n1_agente_solo_nella_propria_agenda(b):
    prima = _istantanea(b)
    r = _decidi(b, chi="luca", assigned_user_id=b["marta"])
    assert r.status_code == 403
    assert _istantanea(b) == prima


def test_n2_admin_e_supreme_con_agente_esplicito(b):
    assert _decidi(b, chi="anna", assigned_user_id=b["luca"]).status_code == 201
    assert _decidi(b, chi="supremo", assigned_user_id=b["marta"],
                   quando=futuro(15)).status_code == 201
    righe = b["sql"]("SELECT assigned_user_id, created_by_user_id FROM appointments ORDER BY id")
    assert [tuple(r) for r in righe] == [(b["luca"], b["anna"]), (b["marta"], b["supremo"])]


def test_n3_owner_senza_agente_percorso_legacy_dichiarato(b):
    """A31-3 NON attiva la facade per owner/admin senza agente: la UI attuale
    non ha un selettore (attivazione in A31-4). Il flusso resta quello di
    prima - nessun agente inferito, nessun appuntamento."""
    for n, chi in enumerate(("giorgio", "anna", "supremo")):
        b["sql"]("UPDATE matches SET commercial_status='interested' WHERE id=%s", (b["match"],))
        r = _decidi(b, chi=chi, quando=futuro(9 + n))
        assert r.status_code == 201, (chi, r.text)
    assert _conta(b, "appointments") == 0
    assert _conta(b, "property_visits", "appointment_id IS NULL") == 3
    assert _conta(b, "buy_request_interactions", "interaction_type='visit_scheduled'") == 3
    r = b["api"]("giorgio").post(f"/api/property/properties/{b['casa']}/visits",
                                 json=_visita_ui(b, scheduled_at=futuro(18).isoformat()))
    assert r.status_code == 201 and r.json()["appointment_id"] is None
    assert _conta(b, "appointments") == 0


def test_n4_visita_storica_dell_agente_resta_legacy(b):
    r = b["api"]("luca").post(f"/api/property/properties/{b['casa']}/visits",
                              json=_visita_ui(b, status="completed",
                                              scheduled_at=ore(10).isoformat()))
    assert r.status_code == 201 and r.json()["appointment_id"] is None
    assert _conta(b, "appointments") == 0
    passato = datetime.now(timezone.utc) - timedelta(days=2)
    r = b["api"]("luca").post(f"/api/property/properties/{b['casa']}/visits",
                              json=_visita_ui(b, scheduled_at=passato.isoformat()))
    assert r.status_code == 201 and r.json()["appointment_id"] is None
