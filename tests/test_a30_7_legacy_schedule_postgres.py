"""A30-7 - sincronizzazione delle richieste dal sito e "Pianifica / Fissa
sopralluogo" su PostgreSQL vero, via HTTP.

Opt-in: senza `P29_TEST_DSN` si salta tutto. MAI PROD: il database e' quello
usa-e-getta di A30-2 (`test_a30_2_appointments_postgres.db`), con la 073 e la
tabella legacy delle migration vere 049-051 aggiunte da A30-6
(`test_a30_6_legacy_import_postgres.db_legacy`). Nessuna connessione nuova
nasce qui (P26 H11): tutto passa da `core.database.get_connection()`.

Il router dell'Agenda e quello della sincronizzazione sono montati su
un'applicazione di prova, con `require_operator` sostituito da un contesto
costruito dal test (la stessa `OperatorContext` della produzione). Il mount
vero e i 401 anonimi sono provati in `test_a30_mount_api.py`.
"""
from __future__ import annotations

import threading
from datetime import timedelta

import pytest

from tests.test_a30_2_appointments_postgres import (  # noqa: F401 - fixture
    DSN, db, futuro, mondo, ore, proiezione_accesa)
from tests.test_a30_6_legacy_import_postgres import (  # noqa: F401 - fixture
    PII, db_legacy, legacy_pulito, m)

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare A30-7")


# ---------------------------------------------------------------------------
# banco
# ---------------------------------------------------------------------------

@pytest.fixture
def w(m, mondo):
    """Il mondo di A30-6 (stime, lead, legacy) piu' gli operatori di A30-2."""
    return {**m, "luca": mondo["luca"], "marta": mondo["marta"],
            "estraneo": mondo["estraneo"], "ctx": mondo["ctx"]}


def _ctx(w, chi, *, agenzia=None, ruolo=None, platform=False):
    from operator_auth.context import OperatorContext
    if chi == "owner_b":
        return OperatorContext(user_id=w["estraneo"], agency_id=w["b"], role="agency_owner",
                               is_platform_admin=False, session_id=None,
                               auth_channel="session")
    if chi == "platform_senza_agenzia":
        return OperatorContext(user_id=w["giorgio"], agency_id=None, role=None,
                               is_platform_admin=True, session_id=None,
                               auth_channel="session")
    return w["ctx"](chi)


@pytest.fixture
def http(w):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from appointments.router import router
    from appointments_legacy.router import router as legacy_router
    from operator_auth.dependencies import require_operator

    app = FastAPI()
    app.include_router(router)
    app.include_router(legacy_router)
    stato = {"ctx": _ctx(w, "giorgio")}
    app.dependency_overrides[require_operator] = lambda: stato["ctx"]
    client = TestClient(app)

    def come(chi):
        stato["ctx"] = _ctx(w, chi)
        return client

    return come


def _sync(http, chi="giorgio", **kw):
    return http(chi).post("/api/appointments/legacy-requests/sync", **kw)


def _richiesta(w, record_id):
    righe = w["sql"]("SELECT * FROM appointments WHERE source = 'legacy_stime_dettagliate' "
                     "AND source_record_id = %s", (f"stime_dettagliate:{record_id}",))
    assert len(righe) <= 1
    return righe[0] if righe else None


def _conta(w, tabella, where="TRUE", par=None):
    return w["sql"](f"SELECT count(*) AS n FROM {tabella} WHERE {where}", par)[0]["n"]


def _pianifica(http, riga, agente, inizio, fine, *, chi="giorgio", versione=None):
    return http(chi).post(f"/api/appointments/{riga['id']}/schedule", json={
        "version": riga["version"] if versione is None else versione,
        "assigned_user_id": agente, "start_at": inizio.isoformat(),
        "end_at": fine.isoformat()})


def _importa(w, http, *stime, delta=5):
    ids = [w["dettaglio"](s, w["giorno"](delta + i)) for i, s in enumerate(stime)]
    r = _sync(http)
    assert r.status_code == 200, r.text
    return [_richiesta(w, i) for i in ids], ids


def _fotografia(w, *, escluse=()):
    tabelle = [r["tablename"] for r in w["sql"](
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1")]
    return {t: w["sql"](f"SELECT count(*) AS n, md5(coalesce(string_agg(x::text, '|' "
                        f"ORDER BY x::text), '')) AS h FROM {t} x")[0]
            for t in tabelle if t not in escluse}


# ---------------------------------------------------------------------------
# A - SINCRONIZZAZIONE (D2)
# ---------------------------------------------------------------------------

def test_01_sync_agenzia_a_importa_solo_a(http, w):
    ra = w["dettaglio"](w["s_un_lead"], w["giorno"](+3))
    rb = w["dettaglio"](w["s_b"], w["giorno"](+3), agenzia=w["b"])
    r = _sync(http)
    assert r.status_code == 200, r.text
    assert r.json()["imported"] == 1 and r.json()["already_present"] == 0
    assert _richiesta(w, ra)["agency_id"] == w["a"]
    assert _richiesta(w, rb) is None                               # B non passa da A
    # un agency_id nel corpo non ha dove entrare
    r = _sync(http, json={"agency_id": w["b"]})
    assert r.status_code == 200 and r.json()["imported"] == 0
    assert _richiesta(w, rb) is None
    # l'owner di B importa la sua
    r = _sync(http, "owner_b")
    assert r.status_code == 200 and r.json()["imported"] == 1
    assert _richiesta(w, rb)["agency_id"] == w["b"]


def test_02_03_04_sync_idempotente_e_nuovo_record_al_giro_dopo(http, w):
    for delta in (+1, +2):
        w["dettaglio"](w["s_un_lead"], w["giorno"](delta))
    w["dettaglio"](None, w["giorno"](+3))                          # orfano: escluso
    primo = _sync(http).json()
    assert (primo["imported"], primo["already_present"], primo["excluded"]) == (2, 0, 1)
    eventi = _conta(w, "appointment_events")
    secondo = _sync(http).json()
    assert (secondo["imported"], secondo["already_present"], secondo["excluded"]) == (0, 2, 1)
    assert _conta(w, "appointment_events") == eventi == 2          # nessun evento nuovo
    nuovo = w["dettaglio"](w["s_zero_lead"], w["giorno"](+9))
    terzo = _sync(http).json()
    assert (terzo["imported"], terzo["already_present"]) == (1, 2)
    assert _richiesta(w, nuovo)["status"] == "requested"
    assert set(terzo) == {"imported", "already_present", "excluded", "counts"}


def test_05_sync_solo_owner_admin_403_per_agent(http, w):
    w["dettaglio"](w["s_un_lead"], w["giorno"](+3))
    for chi in ("luca", "marta"):
        r = _sync(http, chi)
        assert r.status_code == 403 and r.json()["code"] == "FORBIDDEN_ROLE", r.text
    assert _conta(w, "appointments") == 0
    r = _sync(http, "platform_senza_agenzia")
    assert r.status_code == 403 and r.json()["code"] == "PLATFORM_ADMIN_AGENCY_REQUIRED"
    assert _conta(w, "appointments") == 0


def test_06_sync_nessun_side_effect_ne_scrittura_legacy(http, w):
    for delta in (-3, +3):
        w["dettaglio"](w["s_un_lead"], w["giorno"](delta), pii=True)
    prima = _fotografia(w, escluse=("appointments", "appointment_events"))
    r = _sync(http)
    assert r.status_code == 200 and r.json()["imported"] == 2
    assert _fotografia(w, escluse=("appointments", "appointment_events")) == prima
    for dato in PII:
        assert dato not in r.text
    assert w["sql"]("SELECT DISTINCT event_type, actor_user_id FROM appointment_events") == \
        [{"event_type": "created", "actor_user_id": None}]


# ---------------------------------------------------------------------------
# B - PIANIFICA: STESSA RIGA, requested -> scheduled
# ---------------------------------------------------------------------------

def test_10_la_richiesta_importata_si_fissa_sulla_stessa_riga(http, w, proiezione_accesa):
    (req,), (rid,) = _importa(w, http, w["s_un_lead"])
    legacy_prima = w["sql"]("SELECT sopralluogo FROM stime_dettagliate WHERE id = %s", (rid,))
    assert req["status"] == "requested" and req["version"] == 1
    r = _pianifica(http, req, w["luca"], futuro(10), futuro(11))
    assert r.status_code == 200, r.text
    dopo = r.json()
    assert dopo["id"] == req["id"] and dopo["status"] == "scheduled"
    # due UPDATE nella stessa transazione: il passaggio di stato e il
    # collegamento alla riga LMC-15 scritto dalla proiezione (A30-2P). La
    # risposta porta la version vera, quella che la UI usera' dopo.
    assert dopo["version"] == req["version"] + 2
    assert dopo["version"] == _richiesta(w, rid)["version"]
    assert dopo["assigned_user_id"] == w["luca"]
    assert (dopo["source"], dopo["source_record_id"]) == (req["source"], req["source_record_id"])
    assert dopo["end_at"] and dopo["confirmed_at"] is None          # scheduled, non confirmed
    # nessun appuntamento nuovo, una sola proiezione
    assert _conta(w, "appointments") == 1
    assert dopo["stima_inspection_id"] is not None
    assert _conta(w, "stima_inspections") == 1
    stato, quando = [(r["status"], r["scheduled_for"]) for r in w["sql"](
        "SELECT status, scheduled_for FROM stima_inspections")][0]
    assert stato == "scheduled" and quando == futuro(10)
    # eventi: created (sistema) + status_changed (operatore)
    eventi = w["sql"]("SELECT event_type, from_status, to_status, actor_user_id "
                      "FROM appointment_events WHERE appointment_id = %s ORDER BY id",
                      (req["id"],))
    assert eventi == [
        {"event_type": "created", "from_status": None, "to_status": "requested",
         "actor_user_id": None},
        {"event_type": "status_changed", "from_status": "requested", "to_status": "scheduled",
         "actor_user_id": w["giorgio"]}]
    # il legacy non si tocca
    assert w["sql"]("SELECT sopralluogo FROM stime_dettagliate WHERE id = %s",
                    (rid,)) == legacy_prima


def test_11_retry_non_duplica_ne_proiezione_ne_eventi(http, w, proiezione_accesa):
    (req,), _ = _importa(w, http, w["s_un_lead"])
    fatto = _pianifica(http, req, w["luca"], futuro(10), futuro(11))
    assert fatto.status_code == 200
    r = _pianifica(http, req, w["luca"], futuro(10), futuro(11))       # stessa version
    assert r.status_code == 409 and r.json()["code"] == "VERSION_CONFLICT"
    r = _pianifica(http, req, w["luca"], futuro(10), futuro(11),
                   versione=fatto.json()["version"])                  # version aggiornata
    assert r.status_code == 409 and r.json()["code"] == "INVALID_TRANSITION"
    assert _conta(w, "stima_inspections") == 1
    assert _conta(w, "appointment_events") == 2
    assert _conta(w, "appointments") == 1


def test_12_agente_obbligatorio(http, w):
    (req,), _ = _importa(w, http, w["s_un_lead"])
    r = http("giorgio").post(f"/api/appointments/{req['id']}/schedule", json={
        "version": 1, "start_at": futuro(10).isoformat(), "end_at": futuro(11).isoformat()})
    assert r.status_code == 422 and r.json()["code"] == "AGENT_REQUIRED"


# ---------------------------------------------------------------------------
# C - D4: MAI NEL PASSATO
# ---------------------------------------------------------------------------

def test_20_schedule_nel_passato_422_e_nulla_cambia(http, w, proiezione_accesa):
    (req,), _ = _importa(w, http, w["s_un_lead"])
    # l'orologio del service e' ORA (mezzogiorno di GIORNO): le 9 sono passate
    r = _pianifica(http, req, w["luca"], ore(9), ore(10))
    assert r.status_code == 422 and r.json()["code"] == "SCHEDULE_IN_PAST", r.text
    riga = _richiesta(w, req["source_record_id"].split(":")[1])
    assert (riga["status"], riga["version"], riga["assigned_user_id"]) == ("requested", 1, None)
    assert _conta(w, "stima_inspections") == 0


def test_21_schedule_senza_fuso_422(http, w):
    (req,), _ = _importa(w, http, w["s_un_lead"])
    r = http("giorgio").post(f"/api/appointments/{req['id']}/schedule", json={
        "version": 1, "assigned_user_id": w["luca"],
        "start_at": futuro(10).replace(tzinfo=None).isoformat(),
        "end_at": futuro(11).replace(tzinfo=None).isoformat()})
    assert r.status_code == 422 and r.json()["code"] == "TIMEZONE_REQUIRED"


def test_22_confronto_tra_istanti_non_tra_orologi_locali(w, monkeypatch):
    """Lo stesso istante scritto in due fusi: il confronto e' sull'istante."""
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    from appointments import errors, service
    adesso = datetime(2030, 3, 10, 9, 0, tzinfo=timezone.utc)          # 10:00 a Roma
    monkeypatch.setattr(service, "_adesso", lambda: adesso)
    service._non_nel_passato(datetime(2030, 3, 10, 10, 30, tzinfo=ZoneInfo("Europe/Rome")))
    with pytest.raises(errors.ScheduleInPast):
        service._non_nel_passato(datetime(2030, 3, 10, 9, 30, tzinfo=ZoneInfo("Europe/Rome")))
    with pytest.raises(errors.ScheduleInPast):
        service._non_nel_passato(datetime(2030, 3, 10, 8, 59, tzinfo=timezone.utc))


# ---------------------------------------------------------------------------
# D - DISPONIBILITA' E CORSE
# ---------------------------------------------------------------------------

def _occupa_luca(http, w, inizio, fine):
    import uuid
    r = http("giorgio").post("/api/appointments", json={
        "appointment_type": "seller_meeting", "assigned_user_id": w["luca"],
        "start_at": inizio.isoformat(), "end_at": fine.isoformat(),
        "client_request_id": str(uuid.uuid4())})
    assert r.status_code == 201, r.text
    return r.json()


def test_30_disponibilita_slot_occupato_e_libero(http, w):
    (req,), _ = _importa(w, http, w["s_un_lead"])
    _occupa_luca(http, w, futuro(10), futuro(11))
    giorno = futuro(0)
    r = http("giorgio").get("/api/appointments/availability", params={
        "user_id": w["luca"], "from": giorno.isoformat(),
        "to": (giorno + timedelta(days=1)).isoformat(), "duration": 60, "step": 30,
        "exclude_appointment_id": req["id"]})
    assert r.status_code == 200, r.text
    from datetime import datetime
    slot = {datetime.fromisoformat(s["start_at"]): s["available"] for s in r.json()["slots"]}
    assert slot[futuro(10)] is False                  # occupato da luca
    assert slot[futuro(9, 30)] is False               # finirebbe dentro l'occupato
    assert slot[futuro(11)] is True and slot[futuro(8)] is True
    assert r.json()["step"] == 30 and r.json()["duration"] == 60
    assert all((b - a) == timedelta(minutes=30) for a, b in zip(sorted(slot), sorted(slot)[1:]))


def test_31_slot_occupato_409_con_alternative_e_nulla_cambia(http, w, proiezione_accesa):
    (req,), _ = _importa(w, http, w["s_un_lead"])
    _occupa_luca(http, w, futuro(10), futuro(11))
    r = _pianifica(http, req, w["luca"], futuro(10, 30), futuro(11, 30))
    assert r.status_code == 409 and r.json()["code"] == "APPOINTMENT_CONFLICT"
    assert r.json()["alternatives"], r.json()
    assert _richiesta(w, req["source_record_id"].split(":")[1])["status"] == "requested"
    assert _conta(w, "stima_inspections") == 0
    # il check preventivo dice lo stesso
    r = http("giorgio").post("/api/appointments/availability/check", json={
        "assigned_user_id": w["luca"], "start_at": futuro(10, 30).isoformat(),
        "end_at": futuro(11, 30).isoformat(), "exclude_appointment_id": req["id"]})
    assert r.status_code == 200 and r.json()["available"] is False


def _in_parallelo(*lavori):
    esiti, fili = [None] * len(lavori), []
    barriera = threading.Barrier(len(lavori))

    def corri(i, lavoro):
        barriera.wait()
        try:
            esiti[i] = ("ok", lavoro())
        except Exception as exc:  # noqa: BLE001
            esiti[i] = ("errore", exc)

    for i, lavoro in enumerate(lavori):
        fili.append(threading.Thread(target=corri, args=(i, lavoro)))
    for f in fili:
        f.start()
    for f in fili:
        f.join(timeout=30)
    return esiti


def _corpo(w, agente, inizio, fine, versione=1):
    from appointments.schemas import ScheduleBody
    return ScheduleBody(version=versione, assigned_user_id=agente, start_at=inizio, end_at=fine)


def test_32_corsa_sullo_stesso_slot_un_solo_vincitore(w, proiezione_accesa):
    """A vede le 10 libere, B le occupa, A conferma: A riceve il conflitto."""
    from appointments import errors, service
    richieste, _ = _importa_diretto(w, w["s_un_lead"], w["s_zero_lead"])
    ctx = _ctx(w, "giorgio")
    esiti = _in_parallelo(*[
        (lambda r=r: service.schedule_appointment(
            ctx, r["id"], _corpo(w, w["luca"], futuro(10), futuro(11))))
        for r in richieste])
    vinti = [e for e in esiti if e[0] == "ok"]
    persi = [e for e in esiti if e[0] == "errore"]
    assert len(vinti) == 1 and len(persi) == 1, esiti
    assert isinstance(persi[0][1], errors.AppointmentConflict)
    assert _conta(w, "appointments", "assigned_user_id = %s AND status = 'scheduled'",
                  (w["luca"],)) == 1


def test_33_corsa_sulla_stessa_richiesta_una_sola_proiezione(w, proiezione_accesa):
    from appointments import errors, service
    (req,), _ = _importa_diretto(w, w["s_un_lead"])
    ctx = _ctx(w, "giorgio")
    esiti = _in_parallelo(
        lambda: service.schedule_appointment(ctx, req["id"],
                                             _corpo(w, w["luca"], futuro(10), futuro(11))),
        lambda: service.schedule_appointment(ctx, req["id"],
                                             _corpo(w, w["marta"], futuro(14), futuro(15))))
    vinti = [e for e in esiti if e[0] == "ok"]
    assert len(vinti) == 1, esiti
    (perso,) = [e[1] for e in esiti if e[0] == "errore"]
    assert isinstance(perso, errors.VersionConflict)
    assert _conta(w, "stima_inspections") == 1
    assert _conta(w, "appointment_events", "event_type = 'status_changed'") == 1


def _importa_diretto(w, *stime):
    from appointments_legacy import stime_dettagliate_import as legacy
    from core.database import core_cursor
    ids = [w["dettaglio"](s, w["giorno"](5 + i)) for i, s in enumerate(stime)]
    with core_cursor(commit=True) as (_, cur):
        legacy.run_import(cur, apply=True, agency_id=w["a"])
    return [_richiesta(w, i) for i in ids], ids


# ---------------------------------------------------------------------------
# E - D5: UN SOLO SOPRALLUOGO APERTO PER STIMA
# ---------------------------------------------------------------------------

def test_40_secondo_sopralluogo_aperto_409_con_id_per_chi_lo_vede(http, w, proiezione_accesa):
    (r1, r2), _ = _importa(w, http, w["s_un_lead"], w["s_un_lead"])
    # due richieste aperte della stessa stima: nessuna si fissa finche' l'altra e' aperta
    r = _pianifica(http, r1, w["luca"], futuro(10), futuro(11))
    assert r.status_code == 409 and r.json()["code"] == "STIMA_INSPECTION_ALREADY_OPEN"
    assert r.json()["existing_appointment_id"] == r2["id"]
    assert _conta(w, "stima_inspections") == 0
    assert _richiesta(w, r1["source_record_id"].split(":")[1])["status"] == "requested"
    # annullata l'altra, la prima si fissa
    c = http("giorgio").post(f"/api/appointments/{r2['id']}/cancel",
                             json={"version": r2["version"], "reason": "Doppione del sito"})
    assert c.status_code == 200, c.text
    assert _pianifica(http, r1, w["marta"], futuro(10), futuro(11)).status_code == 200
    # una richiesta nuova della stessa stima ora trova quella FISSATA
    nuovo = w["dettaglio"](w["s_un_lead"], w["giorno"](+12))
    _sync(http)
    r3 = _richiesta(w, nuovo)
    r = _pianifica(http, r3, w["luca"], futuro(14), futuro(15))
    assert r.status_code == 409 and r.json()["existing_appointment_id"] == r1["id"]
    assert _conta(w, "stima_inspections") == 1


def test_41_un_agent_non_riceve_l_id_che_non_puo_vedere(http, w, proiezione_accesa):
    (r1, r2), _ = _importa(w, http, w["s_un_lead"], w["s_un_lead"])
    # r2 assegnata a luca (resta una richiesta), r1 resta senza agente: per
    # luca r1 non esiste
    a = http("giorgio").post(f"/api/appointments/{r2['id']}/reassign",
                             json={"version": r2["version"], "assigned_user_id": w["luca"]})
    assert a.status_code == 200, a.text
    r = _pianifica(http, a.json(), w["luca"], futuro(10), futuro(11), chi="luca")
    assert r.status_code == 409 and r.json()["code"] == "STIMA_INSPECTION_ALREADY_OPEN"
    assert "existing_appointment_id" not in r.json()


def test_42_corsa_su_due_richieste_della_stessa_stima_nessun_doppio(w, proiezione_accesa):
    from appointments import errors, service
    (r1, r2), _ = _importa_diretto(w, w["s_un_lead"], w["s_un_lead"])
    ctx = _ctx(w, "giorgio")
    esiti = _in_parallelo(
        lambda: service.schedule_appointment(ctx, r1["id"],
                                             _corpo(w, w["luca"], futuro(10), futuro(11))),
        lambda: service.schedule_appointment(ctx, r2["id"],
                                             _corpo(w, w["marta"], futuro(10), futuro(11))))
    assert all(e[0] == "errore" and isinstance(e[1], errors.StimaInspectionAlreadyOpen)
               for e in esiti), esiti
    assert _conta(w, "appointments", "status = 'scheduled'") == 0
    assert _conta(w, "stima_inspections") == 0


def test_43_la_guardia_legge_sotto_il_lock_della_stima(w, proiezione_accesa):
    """Un'altra transazione tiene la stima e ci sta aprendo un secondo
    sopralluogo (non ancora committato). La pianificazione deve ASPETTARE
    quel lock prima di cercare i sopralluoghi aperti, e quindi vedere quello
    nuovo: 409, nessun doppio. Senza il lock la ricerca partirebbe subito, non
    vedrebbe la riga non committata e fisserebbe il secondo sopralluogo."""
    import uuid

    from psycopg2.extras import RealDictCursor

    from appointments import errors, repository, service
    from core import database as core_database
    (req,), _ = _importa_diretto(w, w["s_un_lead"])
    tenuta = core_database.get_connection()
    try:
        with tenuta.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM stime WHERE id = %s FOR UPDATE", (w["s_un_lead"],))
            repository.insert_appointment(cur, {
                "agency_id": w["a"], "assigned_user_id": w["marta"],
                "appointment_type": "inspection", "status": "scheduled",
                "start_at": futuro(15), "end_at": futuro(16), "stima_id": w["s_un_lead"],
                "source": "crm_manual", "source_record_id": str(uuid.uuid4()),
                "created_by_user_id": w["giorgio"]}, actor_user_id=w["giorgio"])
        ctx = _ctx(w, "giorgio")
        esito = {}

        def pianifica():
            try:
                esito["riga"] = service.schedule_appointment(
                    ctx, req["id"], _corpo(w, w["luca"], futuro(10), futuro(11)))
            except Exception as exc:  # noqa: BLE001
                esito["errore"] = exc

        filo = threading.Thread(target=pianifica)
        filo.start()
        filo.join(timeout=1.0)
        assert filo.is_alive(), "la pianificazione non ha aspettato il lock della stima"
        tenuta.commit()
        filo.join(timeout=30)
    finally:
        tenuta.close()
    assert isinstance(esito.get("errore"), errors.StimaInspectionAlreadyOpen), esito
    assert _conta(w, "appointments", "stima_id = %s AND status = 'scheduled'",
                  (w["s_un_lead"],)) == 1
    assert _conta(w, "stima_inspections") == 0


# ---------------------------------------------------------------------------
# F - TENANT E ERRORI
# ---------------------------------------------------------------------------

def test_50_agente_di_un_altra_agenzia_rifiutato(http, w):
    (req,), _ = _importa(w, http, w["s_un_lead"])
    r = _pianifica(http, req, w["estraneo"], futuro(10), futuro(11))
    assert r.status_code == 422 and r.json()["code"] == "AGENT_NOT_ACTIVE"


def test_51_appuntamento_di_un_altra_agenzia_404(http, w):
    rb = w["dettaglio"](w["s_b"], w["giorno"](+3), agenzia=w["b"])
    assert _sync(http, "owner_b").json()["imported"] == 1
    riga = _richiesta(w, rb)
    r = _pianifica(http, riga, w["luca"], futuro(10), futuro(11))
    assert r.status_code == 404 and r.json()["code"] == "NOT_FOUND"
    assert _richiesta(w, rb)["status"] == "requested"


def test_52_stima_di_un_altra_agenzia_rifiutata_alla_creazione(http, w):
    import uuid
    r = http("giorgio").post("/api/appointments", json={
        "appointment_type": "inspection", "stima_id": w["s_b"], "status": "requested",
        "start_at": futuro(10).isoformat(), "end_at": futuro(11).isoformat(),
        "client_request_id": str(uuid.uuid4())})
    assert r.status_code == 404


def test_53_404_richiesta_inesistente(http, w):
    r = http("giorgio").post("/api/appointments/999999/schedule", json={
        "version": 1, "assigned_user_id": w["luca"], "start_at": futuro(10).isoformat(),
        "end_at": futuro(11).isoformat()})
    assert r.status_code == 404


def test_54_il_form_pubblico_e_il_legacy_restano_intatti():
    """Nessuna modifica al funnel pubblico: `salva_stima_dettagliata` non
    nomina l'Agenda, e nessun codice A30-7 scrive su `stime_dettagliate`."""
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    main = (root / "main.py").read_text(encoding="utf-8")
    funzione = main[main.index("async def salva_stima_dettagliata"):]
    funzione = funzione[:funzione.index("\n@app.")]
    assert "appointments" not in funzione
    for file in (root / "appointments_legacy").glob("*.py"):
        codice = re.sub(r'""".*?"""', "", file.read_text(encoding="utf-8"), flags=re.S)
        for vietato in (r"INSERT\s+INTO\s+stime", r"UPDATE\s+stime", r"DELETE\s+FROM\s+stime"):
            assert not re.search(vietato, codice, re.I), (file.name, vietato)
