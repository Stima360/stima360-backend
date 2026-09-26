"""A30-2P - la FACADE LMC-15 -> Agenda su PostgreSQL usa-e-getta.

Opt-in: senza `P29_TEST_DSN` si salta tutto. Riusa il database usa-e-getta e
il mondo di prova di `test_a30_2_appointments_postgres.py` (catena LMC-15 +
072) e ci applica la 073; non apre connessioni proprie.

Le quattro rotte si chiamano via HTTP attraverso il router LMC-15 VERO
(`acquisition.router`), montato su un'app FastAPI di prova con
`require_operator` sostituito: e' il contratto che i client usano. Si prova
che il contratto resta quello di LMC-15 (status, detail, `INSPECTION_COLUMNS`,
eventi `lmc15:v1:*`) mentre `appointments` diventa la fonte autorevole, che
le compatibilita' valgono SOLO sulla facade, F2, l'adozione, l'impossibilita'
dei doppioni, il rollback atomico, e Q10 = 0 dopo ogni scenario.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tests.test_a30_2_appointments_postgres import (  # noqa: F401 - fixture
    DSN, db, futuro, http, mondo, ore)
from tests.test_a30_2p_backfill_postgres import Q10

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare la facade")

MIGRAZIONI = Path(__file__).resolve().parents[1] / "migrations"
SU_073 = (MIGRAZIONI / "073_a30_2p_lmc15_facade.sql").read_text(encoding="utf-8")
COLONNE = ("id", "stima_id", "status", "scheduled_for", "completed_at", "cancelled_at",
           "cancelled_reason")


# ---------------------------------------------------------------------------
# strumenti
# ---------------------------------------------------------------------------

@pytest.fixture
def lmc15(mondo):
    """Il router LMC-15 VERO su un'app di prova, con la 073 applicata al
    database del modulo (come il runner: corpo + riga del ledger)."""
    esiste = mondo["sql"]("SELECT 1 FROM pg_constraint WHERE conname = "
                          "'appointments_lmc15_facade_chk'")
    if not esiste:
        with mondo["conn"].cursor() as cur:
            cur.execute("BEGIN")
            cur.execute(SU_073)
            cur.execute("INSERT INTO schema_migrations (version, applied_at) "
                        "VALUES ('073_a30_2p_lmc15_facade', NOW())")
            cur.execute("COMMIT")

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from acquisition.router import router
    from operator_auth.dependencies import require_operator

    app = FastAPI()
    app.include_router(router)
    stato = {"ctx": mondo["ctx"]("giorgio")}
    app.dependency_overrides[require_operator] = lambda: stato["ctx"]
    client = TestClient(app, raise_server_exceptions=False)

    def come(chi="giorgio", **kw):
        stato["ctx"] = mondo["ctx"](chi, **kw)
        return client
    return come


def _q10(mondo):
    with mondo["conn"].cursor() as cur:
        cur.execute(Q10)
        nomi = [d[0] for d in cur.description]
        esito = dict(zip(nomi, cur.fetchone()))
    mondo["conn"].commit()
    assert all(v == 0 for v in esito.values()), esito
    return esito


def _db_now(mondo):
    return mondo["sql"]("SELECT NOW()")[0][0]


def _fotografia(mondo):
    tabelle = ("appointments", "appointment_events", "stima_inspections",
               "seller_timeline_events")
    return {t: [r[0] for r in mondo["sql"](f"SELECT to_jsonb(x) FROM {t} x ORDER BY id")]
            for t in tabelle}


def _appuntamento(mondo, ispezione_id):
    righe = mondo["sql"]("SELECT to_jsonb(a) FROM appointments a "
                         "WHERE stima_inspection_id = %s", (ispezione_id,))
    assert len(righe) == 1, righe
    return righe[0][0]


def _eventi(mondo, appointment_id):
    return [tuple(r) for r in mondo["sql"](
        "SELECT event_type, from_status, to_status, actor_user_id, changes->>'azione' "
        "FROM appointment_events WHERE appointment_id = %s ORDER BY id", (appointment_id,))]


def _timeline(mondo):
    return [tuple(r) for r in mondo["sql"](
        "SELECT event_type, idempotency_key FROM seller_timeline_events ORDER BY id")]


def _ist(valore):
    return datetime.fromisoformat(valore)


def _fissa(lmc15, mondo, quando, chi="giorgio"):
    r = lmc15(chi).post(f"/api/acquisition/stime/{mondo['stima']}/inspections",
                        json={"scheduled_for": quando.isoformat()})
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------------------
# A - CREAZIONE
# ---------------------------------------------------------------------------

def test_01_create_scheduled_contratto_lmc15_e_appointments_autorevole(lmc15, mondo):
    quando = futuro(10)
    r = lmc15().post(f"/api/acquisition/stime/{mondo['stima']}/inspections",
                     json={"scheduled_for": quando.isoformat()})
    assert r.status_code == 201, r.text
    corpo = r.json()
    assert tuple(corpo) == COLONNE                          # INSPECTION_COLUMNS, stesso ordine
    assert corpo["status"] == "scheduled" and _ist(corpo["scheduled_for"]) == quando
    a = _appuntamento(mondo, corpo["id"])
    assert a["source"] == "lmc15_facade" and a["status"] == "scheduled"
    assert a["assigned_user_id"] is None and a["appointment_type"] == "inspection"
    assert a["stima_id"] == mondo["stima"] and a["stima_inspection_id"] == corpo["id"]
    assert a["source_record_id"] == f"stima_inspections:{corpo['id']}"
    assert _ist(a["start_at"]) == quando and _ist(a["end_at"]) == quando + timedelta(minutes=60)
    assert a["created_by_user_id"] == mondo["giorgio"]
    assert _eventi(mondo, a["id"]) == [("created", None, "scheduled", mondo["giorgio"], None)]
    assert _timeline(mondo) == [
        ("inspection_scheduled", f"lmc15:v1:inspection_scheduled:insp:{corpo['id']}")]
    _q10(mondo)


def test_02_create_completed_a_posteriori(lmc15, mondo):
    fatto = _db_now(mondo) - timedelta(days=2, microseconds=7)
    r = lmc15().post(f"/api/acquisition/stime/{mondo['stima']}/inspections/completed",
                     json={"completed_at": fatto.isoformat()})
    assert r.status_code == 201, r.text
    corpo = r.json()
    assert tuple(corpo) == COLONNE and corpo["status"] == "completed"
    assert corpo["scheduled_for"] is None and _ist(corpo["completed_at"]) == fatto
    a = _appuntamento(mondo, corpo["id"])
    assert (a["source"], a["status"], a["assigned_user_id"]) == ("lmc15_facade", "completed", None)
    assert _ist(a["start_at"]) == fatto == _ist(a["completed_at"])
    assert _ist(a["end_at"]) == fatto + timedelta(minutes=60)
    assert _timeline(mondo) == [
        ("inspection_completed", f"lmc15:v1:inspection_completed:insp:{corpo['id']}")]
    _q10(mondo)


# ---------------------------------------------------------------------------
# B - CHIUSURA: compatibilita' LMC-15 solo sulla facade
# ---------------------------------------------------------------------------

def test_10_complete_anticipato_e_completed_at_prima_di_start_via_facade(lmc15, mondo):
    isp = _fissa(lmc15, mondo, futuro(15))                  # inizio nel futuro
    fatto = _db_now(mondo) - timedelta(hours=3)             # prima dell'inizio
    r = lmc15().post(f"/api/acquisition/inspections/{isp['id']}/complete",
                     json={"completed_at": fatto.isoformat()})
    assert r.status_code == 200, r.text
    assert tuple(r.json()) == COLONNE and r.json()["status"] == "completed"
    assert _ist(r.json()["completed_at"]) == fatto
    a = _appuntamento(mondo, isp["id"])
    assert a["status"] == "completed" and _ist(a["completed_at"]) == fatto
    assert _ist(a["completed_at"]) < _ist(a["start_at"])    # ammesso SOLO qui
    assert _eventi(mondo, a["id"])[-1] == (
        "status_changed", "scheduled", "completed", mondo["giorgio"], "lmc15_complete")
    assert [t[0] for t in _timeline(mondo)] == ["inspection_scheduled", "inspection_completed"]
    _q10(mondo)


@pytest.mark.parametrize("rotta", ["completed", "complete"])
def test_11_completed_at_futuro_422_e_nessuna_scrittura(lmc15, mondo, rotta):
    isp = _fissa(lmc15, mondo, futuro(9)) if rotta == "complete" else None
    prima = _fotografia(mondo)
    futuro_ = (_db_now(mondo) + timedelta(days=1)).isoformat()
    if rotta == "completed":
        url = f"/api/acquisition/stime/{mondo['stima']}/inspections/completed"
    else:
        url = f"/api/acquisition/inspections/{isp['id']}/complete"
    r = lmc15().post(url, json={"completed_at": futuro_})
    assert r.status_code == 422, r.text
    assert set(r.json()) == {"detail"}                     # forma d'errore LMC-15
    assert "COMPLETED_AT_INVALID" in r.json()["detail"]
    assert _fotografia(mondo) == prima                     # rollback totale
    _q10(mondo)


def test_12_cancel_senza_motivo_via_facade(lmc15, mondo):
    isp = _fissa(lmc15, mondo, futuro(11))
    r = lmc15().post(f"/api/acquisition/inspections/{isp['id']}/cancel", json={})
    assert r.status_code == 200, r.text
    assert tuple(r.json()) == COLONNE and r.json()["status"] == "cancelled"
    assert r.json()["cancelled_reason"] is None
    a = _appuntamento(mondo, isp["id"])
    assert a["status"] == "cancelled" and a["cancelled_reason"] is None
    assert _ist(a["cancelled_at"]) == _ist(r.json()["cancelled_at"])   # stesso istante
    assert _eventi(mondo, a["id"])[-1][4] == "lmc15_cancel"
    _q10(mondo)


def test_13_cancel_con_motivo_conservato(lmc15, mondo):
    isp = _fissa(lmc15, mondo, futuro(12))
    r = lmc15().post(f"/api/acquisition/inspections/{isp['id']}/cancel",
                     json={"cancelled_reason": "  Rinviato  "})
    assert r.status_code == 200 and r.json()["cancelled_reason"] == "Rinviato"
    assert _appuntamento(mondo, isp["id"])["cancelled_reason"] == "Rinviato"
    _q10(mondo)


# ---------------------------------------------------------------------------
# C - ERRORI: stessi status e detail di LMC-15
# ---------------------------------------------------------------------------

def test_20_404_legacy_inesistente_altra_agenzia_orfana(lmc15, mondo):
    isp = _fissa(lmc15, mondo, futuro(13))
    for chi, ident in (("giorgio", 999999), ("estraneo", isp["id"])):
        for azione, corpo in (("complete", {"completed_at": _db_now(mondo).isoformat()}),
                              ("cancel", {})):
            r = lmc15(chi).post(f"/api/acquisition/inspections/{ident}/{azione}", json=corpo)
            assert r.status_code == 404 and r.json() == {"detail": "Risorsa non trovata"}
    r = lmc15("estraneo").post(f"/api/acquisition/stime/{mondo['stima']}/inspections",
                               json={"scheduled_for": futuro(9).isoformat()})
    assert r.status_code == 404 and r.json() == {"detail": "Risorsa non trovata"}
    # orfana: la stima non c'e' piu' (ON DELETE SET NULL) -> 404 come LMC-15
    from acquisition import repository as legacy
    mondo["sql"]("INSERT INTO stime (agency_id, comune) VALUES (%s, 'X')", (mondo["a"],))
    stima2 = mondo["sql"]("SELECT max(id) FROM stime")[0][0]
    orfana = legacy.create_inspection(mondo["a"], stima_id=stima2, scheduled_for=futuro(8),
                                      actor_user_id=mondo["giorgio"])
    mondo["sql"]("DELETE FROM stime WHERE id = %s", (stima2,))
    r = lmc15().post(f"/api/acquisition/inspections/{orfana['id']}/cancel", json={})
    assert r.status_code == 404 and r.json() == {"detail": "Risorsa non trovata"}
    _q10(mondo)


def test_21_409_legacy_su_riga_gia_chiusa(lmc15, mondo):
    isp = _fissa(lmc15, mondo, futuro(14))
    ok = lmc15().post(f"/api/acquisition/inspections/{isp['id']}/cancel", json={})
    assert ok.status_code == 200
    for azione, corpo in (("complete", {"completed_at": _db_now(mondo).isoformat()}),
                          ("cancel", {})):
        r = lmc15().post(f"/api/acquisition/inspections/{isp['id']}/{azione}", json=corpo)
        assert r.status_code == 409
        assert r.json() == {"detail": "Sopralluogo gia' cancelled: nessuna transizione possibile"}
    assert mondo["sql"]("SELECT count(*) FROM appointments")[0][0] == 1
    _q10(mondo)


def test_22_visibilita_di_agenzia_come_lmc15(lmc15, mondo):
    """Un agent chiude via LMC-15 un sopralluogo di tutta l'agenzia (e' il
    contratto di oggi): la facade non restringe."""
    isp = _fissa(lmc15, mondo, futuro(16), chi="giorgio")
    r = lmc15("marta").post(f"/api/acquisition/inspections/{isp['id']}/cancel", json={})
    assert r.status_code == 200, r.text
    _q10(mondo)


# ---------------------------------------------------------------------------
# D - ADOZIONE, DOPPIONI, ROLLBACK
# ---------------------------------------------------------------------------

def test_30_adozione_di_una_riga_lmc15_non_rappresentata(lmc15, mondo):
    from acquisition import repository as legacy
    vecchia = legacy.create_inspection(mondo["a"], stima_id=mondo["stima"],
                                       scheduled_for=futuro(10), actor_user_id=mondo["giorgio"])
    assert mondo["sql"]("SELECT count(*) FROM appointments")[0][0] == 0
    fatto = _db_now(mondo) - timedelta(minutes=5)
    r = lmc15().post(f"/api/acquisition/inspections/{vecchia['id']}/complete",
                     json={"completed_at": fatto.isoformat()})
    assert r.status_code == 200, r.text
    a = _appuntamento(mondo, vecchia["id"])
    assert a["source"] == "stima_inspections_backfill"
    assert a["source_record_id"] == f"stima_inspections:{vecchia['id']}"
    assert a["status"] == "completed" and _ist(a["completed_at"]) == fatto
    assert [e[0] for e in _eventi(mondo, a["id"])] == ["created", "status_changed"]
    _q10(mondo)


def test_31_doppione_impossibile(lmc15, mondo):
    from acquisition import repository as legacy
    vecchia = legacy.create_inspection(mondo["a"], stima_id=mondo["stima"],
                                       scheduled_for=futuro(10), actor_user_id=mondo["giorgio"])
    # un'adozione che fallisce (409 dopo l'adozione) non lascia la riga adottata
    mondo["sql"]("UPDATE stima_inspections SET status='cancelled', cancelled_at=NOW(), "
                 "cancelled_recorded_at=NOW(), cancelled_by_operator_user_id=%s "
                 "WHERE id=%s", (mondo["giorgio"], vecchia["id"]))
    r = lmc15().post(f"/api/acquisition/inspections/{vecchia['id']}/cancel", json={})
    assert r.status_code == 409
    assert mondo["sql"]("SELECT count(*) FROM appointments")[0][0] == 0
    # facade + backfill + seconda chiamata: sempre UNA riga per sopralluogo
    isp = _fissa(lmc15, mondo, futuro(12))
    from appointments import backfill
    from core.database import core_cursor
    with core_cursor(commit=True) as (_, cur):
        assert backfill.run_backfill(cur, apply=True)["inserted"] == 1   # solo `vecchia`
    lmc15().post(f"/api/acquisition/inspections/{isp['id']}/cancel", json={})
    conteggi = mondo["sql"]("SELECT stima_inspection_id, count(*) FROM appointments "
                            "GROUP BY 1 HAVING count(*) > 1")
    assert conteggi == []
    import psycopg2
    with pytest.raises(psycopg2.errors.UniqueViolation):
        mondo["sql"]("INSERT INTO appointments (agency_id, appointment_type, status, start_at, "
                     "end_at, stima_id, stima_inspection_id, source, source_record_id) "
                     "VALUES (%s,'inspection','requested',NOW(),NOW()+interval '1 hour',%s,%s,"
                     "'crm_manual','x')", (mondo["a"], mondo["stima"], isp["id"]))
    mondo["conn"].rollback()
    _q10(mondo)


def test_32_rollback_atomico_se_l_agenda_fallisce(lmc15, mondo, monkeypatch):
    from appointments import repository

    def rotta(*_a, **_k):
        raise RuntimeError("guasto simulato dopo la scrittura LMC-15")

    prima = _fotografia(mondo)
    monkeypatch.setattr(repository, "insert_appointment", rotta)
    r = lmc15().post(f"/api/acquisition/stime/{mondo['stima']}/inspections",
                     json={"scheduled_for": futuro(10).isoformat()})
    assert r.status_code == 500
    assert _fotografia(mondo) == prima                     # niente riga LMC-15, niente timeline


def test_33_rollback_atomico_se_lmc15_fallisce(lmc15, mondo, monkeypatch):
    isp = _fissa(lmc15, mondo, futuro(10))
    from acquisition import repository as legacy

    def rotta(*_a, **_k):
        raise RuntimeError("guasto simulato in LMC-15")

    prima = _fotografia(mondo)
    monkeypatch.setattr(legacy, "complete_inspection_in", rotta)
    r = lmc15().post(f"/api/acquisition/inspections/{isp['id']}/complete",
                     json={"completed_at": _db_now(mondo).isoformat()})
    assert r.status_code == 500
    assert _fotografia(mondo) == prima                     # l'appuntamento resta scheduled
    _q10(mondo)


# ---------------------------------------------------------------------------
# E - PERIMETRO: l'Agenda nativa resta quella di A30-2
# ---------------------------------------------------------------------------

def test_40_agenda_nativa_invariata_sulle_righe_facade(lmc15, http, mondo):
    isp = _fissa(lmc15, mondo, futuro(10))
    a = _appuntamento(mondo, isp["id"])
    agenda = http("giorgio")
    # confirm senza agente: AGENT_REQUIRED (la 073 non ammette confirmed senza agente)
    r = agenda.post(f"/api/appointments/{a['id']}/confirm", json={"version": a["version"]})
    assert r.status_code == 422 and r.json()["code"] == "AGENT_REQUIRED"
    # spostare senza agente e registrare l'assenza senza agente: AGENT_REQUIRED
    r = agenda.post(f"/api/appointments/{a['id']}/reschedule",
                    json={"version": a["version"], "start_at": futuro(11).isoformat(),
                          "end_at": futuro(12).isoformat()})
    assert r.status_code == 422 and r.json()["code"] == "AGENT_REQUIRED"
    # completare prima dell'inizio: D11 resta (409), niente bypass
    r = agenda.post(f"/api/appointments/{a['id']}/complete", json={"version": a["version"]})
    # A30-8 D6: "troppo presto" e' un 422 con codice proprio (era 409).
    assert r.status_code == 422 and r.json()["code"] == "COMPLETE_TOO_EARLY"
    # annullare senza motivo un sopralluogo con stima: REASON_REQUIRED
    r = agenda.post(f"/api/appointments/{a['id']}/cancel", json={"version": a["version"]})
    assert r.status_code == 422 and r.json()["code"] == "REASON_REQUIRED"
    # con un agente assegnato la conferma nativa torna possibile
    r = agenda.post(f"/api/appointments/{a['id']}/reassign",
                    json={"version": a["version"], "assigned_user_id": mondo["luca"]})
    assert r.status_code == 200, r.text
    r = agenda.post(f"/api/appointments/{a['id']}/confirm",
                    json={"version": r.json()["version"]})
    assert r.status_code == 200 and r.json()["status"] == "confirmed"
    assert mondo["sql"]("SELECT status FROM stima_inspections WHERE id=%s",
                        (isp["id"],))[0][0] == "scheduled"
    _q10(mondo)


def test_41_no_show_nativo_su_riga_facade_senza_agente(lmc15, http, mondo):
    fine = _db_now(mondo) - timedelta(minutes=1)
    isp = _fissa(lmc15, mondo, fine - timedelta(hours=1))
    a = _appuntamento(mondo, isp["id"])
    r = http("giorgio").post(f"/api/appointments/{a['id']}/no-show",
                             json={"version": a["version"]})
    assert r.status_code == 422 and r.json()["code"] == "AGENT_REQUIRED"
    _q10(mondo)


def test_42_nessun_flag_ne_bypass_nelle_funzioni_native():
    import inspect

    from appointments import service
    codice = inspect.getsource(service)
    assert "lmc15_facade" not in codice and "facade" not in codice.lower().replace(
        "facade lmc-15", "")
    for nome in ("complete_appointment", "cancel_appointment", "no_show_appointment",
                 "confirm_appointment", "reschedule_appointment", "schedule_appointment"):
        firma = inspect.signature(getattr(service, nome))
        assert list(firma.parameters) in (["ctx", "appointment_id", "body"],
                                          ["ctx", "appointment_id", "payload"]), nome
    corpo = inspect.getsource(service.complete_appointment)
    assert "check_time_guard(\"complete\"" in corpo and "resolve_completed_at" in corpo


# ---------------------------------------------------------------------------
# F - ADOZIONE CONCORRENTE: due transazioni, la stessa riga LMC-15 legacy
# ---------------------------------------------------------------------------

def _aspetta_attesa_di_lock(mondo, secondi=10):
    """Deterministico: si guarda `pg_stat_activity`, non si dorme a caso."""
    import time
    scadenza = time.monotonic() + secondi
    while time.monotonic() < scadenza:
        n = mondo["sql"]("SELECT count(*) FROM pg_stat_activity "
                         "WHERE datname = current_database() AND wait_event_type = 'Lock' "
                         "AND pid <> pg_backend_pid()")[0][0]
        if n:
            return True
        time.sleep(0.02)
    return False


@pytest.mark.parametrize("seconda", ["cancel", "complete"])
def test_50_adozione_concorrente_una_sola_riga_nessun_500(lmc15, mondo, monkeypatch, seconda):
    """A adotta e si ferma PRIMA di scrivere LMC-15 (transazione aperta, riga
    adottata non ancora visibile); B tenta di adottare la stessa riga e resta
    in attesa sull'indice unico. Rilasciata A (commit), B rilegge la riga
    esistente e termina con il normale 409 di LMC-15. Mai un doppione, mai un
    UniqueViolation esposto, Q10 = 0."""
    import threading

    from acquisition import repository as legacy
    from appointments import lmc15_facade

    vecchia = legacy.create_inspection(mondo["a"], stima_id=mondo["stima"],
                                       scheduled_for=futuro(10), actor_user_id=mondo["giorgio"])
    adottata, rilascia = threading.Event(), threading.Event()
    originale = legacy.complete_inspection_in

    def in_pausa(cur, *a, **k):
        if threading.current_thread().name == "A":
            adottata.set()
            assert rilascia.wait(15)
        return originale(cur, *a, **k)

    monkeypatch.setattr(legacy, "complete_inspection_in", in_pausa)
    esiti = {}

    def esegui(nome, funzione):
        try:
            esiti[nome] = ("ok", funzione())
        except Exception as exc:  # noqa: BLE001 - l'esito si verifica sotto
            esiti[nome] = ("errore", exc)

    fatto = _db_now(mondo) - timedelta(minutes=5)
    a = threading.Thread(name="A", target=esegui, args=("A", lambda: lmc15_facade.complete_inspection(
        mondo["a"], inspection_id=vecchia["id"], completed_at=fatto,
        actor_user_id=mondo["giorgio"])))
    if seconda == "cancel":
        chiamata_b = lambda: lmc15_facade.cancel_inspection(  # noqa: E731
            mondo["a"], inspection_id=vecchia["id"], reason=None, actor_user_id=mondo["giorgio"])
    else:
        chiamata_b = lambda: lmc15_facade.complete_inspection(  # noqa: E731
            mondo["a"], inspection_id=vecchia["id"], completed_at=fatto,
            actor_user_id=mondo["giorgio"])
    b = threading.Thread(name="B", target=esegui, args=("B", chiamata_b))
    a.start()
    assert adottata.wait(15), "A non ha adottato"
    b.start()
    assert _aspetta_attesa_di_lock(mondo), "B non e' in attesa sulla riga adottata da A"
    rilascia.set()
    a.join(20)
    b.join(20)

    assert esiti["A"][0] == "ok" and esiti["A"][1]["status"] == "completed"
    stato, errore = esiti["B"]
    assert stato == "errore", esiti["B"]
    from core.exceptions import ConflictError
    import psycopg2
    assert isinstance(errore, ConflictError) and not isinstance(errore, psycopg2.Error)
    assert str(errore) == "Sopralluogo gia' completed: nessuna transizione possibile"
    righe = mondo["sql"]("SELECT source, status FROM appointments WHERE stima_inspection_id=%s",
                         (vecchia["id"],))
    assert [tuple(r) for r in righe] == [("stima_inspections_backfill", "completed")]
    _q10(mondo)
