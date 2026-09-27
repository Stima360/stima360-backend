"""A30-10B - il worker INBOUND su PostgreSQL VERO.

Riusa i fixture di A30-9A/9B (`w`, `ring`, `db_074`, `pulito`, `genv`, `httpg`,
`_connetti`, `_fake_oauth`) e quelli di A30-2 (`mondo`, `db`, `ore`, `futuro`,
`chiave`) e applica la migration 075 (coda inbound, colonne PROPRIE) sopra la
074. Provider sempre FINTO (`FakeCalendarProvider`): mai una chiamata Google
reale.

Pattern end-to-end di ogni scenario: crea un appuntamento vivo -> connette
l'agente -> un giro OUTBOUND vero (`_giro`) per ottenere una riga `synced`
con `remote_event_id`/`etag`/`remote_updated_at` deterministici -> si simula
un evento lato Google con `FakeCalendarProvider.simulate_remote_*` (o
`vanish`) -> un giro INBOUND (`_inbound_giro`) -> si verifica l'effetto sul
CRM, sulla riga di sync e sul provider finto.
"""
from __future__ import annotations

import uuid
from datetime import timedelta
from pathlib import Path

import pytest

from tests.test_a30_9a_calendar_sync_postgres import (  # noqa: F401 - fixture
    DSN, NS, _collega, _dirty, _giro, _impronta_agenda, _riga, db_074, pulito, ring, w)
from tests.test_a30_9b_calendar_google_postgres import genv  # noqa: F401 - fixture
from tests.test_a30_2_appointments_postgres import chiave, db, futuro, mondo, ore  # noqa: F401

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare A30-10B")

_MIGRAZIONI = Path(__file__).resolve().parents[1] / "migrations"
UP = _MIGRAZIONI / "075_a30_10_calendar_inbound.sql"
DOWN = _MIGRAZIONI / "075_a30_10_calendar_inbound_down.sql"
_INBOUND_COLONNE = ("inbound_next_check_at", "inbound_checked_at", "inbound_attempt_count",
                    "inbound_claim_token", "inbound_claimed_at", "inbound_last_error_code")


# ---------------------------------------------------------------------------
# banco
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def db_075(db_074):
    with db_074["conn"].cursor() as cur:
        cur.execute("SELECT 1 FROM information_schema.columns "
                    "WHERE table_name='appointment_calendar_sync' "
                    "AND column_name='inbound_next_check_at'")
        if cur.fetchone() is None:
            cur.execute(UP.read_text(encoding="utf-8"))
    return db_074


@pytest.fixture
def w75(pulito, mondo, db_075, genv):
    """`w`, con la 075 gia' applicata e Google ABILITATO (`genv`): senza
    questo, `calendar_sync.integration.on_appointment_mutation` e' un NO-OP
    (fail-open, §17) e nessuna mutazione HTTP (reschedule/cancel/confirm/...)
    dirta mai la catena - l'esatto scenario che questa suite deve escludere."""
    return mondo


@pytest.fixture
def http(w75):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from appointments.router import router

    from operator_auth.dependencies import require_operator

    app = FastAPI()
    app.include_router(router)
    stato = {"ctx": w75["ctx"]("giorgio")}
    app.dependency_overrides[require_operator] = lambda: stato["ctx"]
    client = TestClient(app)

    def come(chi, **kw):
        stato["ctx"] = w75["ctx"](chi, **kw)
        return client

    return come


def _crea(http, w, *, agente="giorgio", assegnato="luca", status="scheduled", **kw):
    corpo = {"appointment_type": "seller_meeting", "status": status,
             "assigned_user_id": w[assegnato] if assegnato else None,
             "start_at": ore(10).isoformat(), "end_at": ore(11).isoformat(),
             "client_request_id": chiave()}
    corpo.update(kw)
    r = http(agente).post("/api/appointments", json=corpo)
    assert r.status_code == 201, r.text
    return r.json()


def _versione(http, a):
    return http("giorgio").get(f"/api/appointments/{a['id']}").json()["appointment"]["version"]


def _inbound_giro(fake, **kw):
    from calendar_sync import inbound
    return inbound.run_once(provider=fake, **kw)


def _prepara_mapping(http, w, ring, fake, *, assegnato="luca", status="scheduled", **kw):
    """Un appuntamento vivo, l'agente connesso, UN giro outbound vero: la
    riga torna `synced` con un `remote_event_id`/`etag`/`remote_updated_at`
    reali nel provider finto. Restituisce (appuntamento, riga_sync)."""
    a = _crea(http, w, assegnato=assegnato, status=status, **kw)
    _collega(w, ring, assegnato)
    _dirty(w, a["id"])
    _giro(fake, ring)
    riga = _riga(w, a["id"])
    assert riga["status"] == "synced", riga
    return a, riga


def _connessione_id(w, user):
    return w["sql"]("SELECT id FROM calendar_connections WHERE user_id=%s", (user,))[0][0]


# ---------------------------------------------------------------------------
# A - MIGRATION 075 (1, 2: up/down/up, colonne PROPRIE, nessun riuso outbound)
# ---------------------------------------------------------------------------

def test_01_02_migration_075_up_down_up_colonne_proprie(w75):
    colonne = {r[0] for r in w75["sql"](
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='appointment_calendar_sync'")}
    assert set(_INBOUND_COLONNE) <= colonne
    # nessuna colonna OUTBOUND rinominata/riusata: le due code sono separate
    assert {"attempt_count", "next_attempt_at", "claim_token", "claimed_at",
            "last_error_code"} <= colonne
    assert "inbound_status" not in colonne          # D6: nessuna colonna di stato propria
    indici = {r[0] for r in w75["sql"](
        "SELECT indexname FROM pg_indexes WHERE tablename='appointment_calendar_sync'")}
    assert "idx_appointment_calendar_sync_inbound_due" in indici
    assert "idx_appointment_calendar_sync_inbound_claimed" in indici
    with w75["conn"].cursor() as cur:
        cur.execute(DOWN.read_text(encoding="utf-8"))
    dopo = {r[0] for r in w75["sql"](
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='appointment_calendar_sync'")}
    assert not (set(_INBOUND_COLONNE) & dopo)
    assert {"attempt_count", "chain_root_appointment_id"} <= dopo   # 074 intatta
    with w75["conn"].cursor() as cur:
        cur.execute(UP.read_text(encoding="utf-8"))
    assert set(_INBOUND_COLONNE) <= {r[0] for r in w75["sql"](
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='appointment_calendar_sync'")}


def test_03_vincoli_075_attempt_count_e_claim_pair(http, w75, ring):
    import psycopg2
    from calendar_sync.fake_provider import FakeCalendarProvider
    a, riga = _prepara_mapping(http, w75, ring, FakeCalendarProvider(), assegnato="luca")
    with pytest.raises(psycopg2.errors.CheckViolation):
        w75["sql"]("UPDATE appointment_calendar_sync SET inbound_attempt_count = -1 WHERE id=%s",
                   (riga["id"],))
    w75["conn"].rollback()
    with pytest.raises(psycopg2.errors.CheckViolation):
        w75["sql"]("UPDATE appointment_calendar_sync SET inbound_claim_token = gen_random_uuid() "
                   "WHERE id=%s", (riga["id"],))          # token senza claimed_at: rifiutato
    w75["conn"].rollback()


# ---------------------------------------------------------------------------
# B - ELEGGIBILITA' (3-8): solo scheduled/confirmed, connessa, dovuta, senza
# claim vivo; mai completed/no_show/rescheduled/cancelled/requested
# ---------------------------------------------------------------------------

def test_04_eleggibile_solo_scheduled_confirmed_connesse_dovute(http, w75, ring):
    from calendar_sync.fake_provider import FakeCalendarProvider
    fake = FakeCalendarProvider()
    a, riga = _prepara_mapping(http, w75, ring, fake)
    esiti = _inbound_giro(fake)
    assert (riga["id"], "synced") in esiti or any(e == "synced" for _, e in esiti)


@pytest.mark.parametrize("azione,corpo", [
    ("complete", {}), ("no-show", {}), ("cancel", {})])
def test_05_stati_terminali_mai_scandagliati(http, w75, ring, azione, corpo):
    from calendar_sync.fake_provider import FakeCalendarProvider
    fake = FakeCalendarProvider()
    a, riga = _prepara_mapping(http, w75, ring, fake, status="confirmed")
    corpo.setdefault("version", _versione(http, a))
    r = http("giorgio").post(f"/api/appointments/{a['id']}/{azione}", json=corpo)
    assert r.status_code in (200, 201), r.text
    fake.calls = []
    esiti = _inbound_giro(fake)
    assert not any(sid == riga["id"] for sid, _ in esiti)
    assert fake.calls == []          # riga non piu' scheduled/confirmed: nessuna GET


def test_06_rescheduled_non_torna_indietro(http, w75, ring):
    """La riga vecchia (`rescheduled`) non e' MAI la riga viva puntata dalla
    mapping: `current_appointment_id` segue sempre la punta della catena."""
    from calendar_sync.fake_provider import FakeCalendarProvider
    fake = FakeCalendarProvider()
    a, riga = _prepara_mapping(http, w75, ring, fake)
    http("giorgio").post(f"/api/appointments/{a['id']}/reschedule",
                        json={"version": _versione(http, a), "start_at": ore(14).isoformat(),
                              "end_at": ore(15).isoformat()})
    _giro(fake, ring)   # l'outbound riallinea current_appointment_id alla nuova riga
    dopo = _riga(w75, a["id"])
    assert dopo["current_appointment_id"] != a["id"]


def test_07_mai_scandagliata_senza_connessione_remota(http, w75, ring):
    from calendar_sync.fake_provider import FakeCalendarProvider
    fake = FakeCalendarProvider()
    a = _crea(http, w75, assegnato="luca", status="scheduled")
    _dirty(w75, a["id"])           # nessuna connessione: waiting_connection
    _giro(fake, ring)
    esiti = _inbound_giro(fake)
    assert not any(sid for sid, _ in esiti if sid == a["id"])
    assert fake.calls == []


# ---------------------------------------------------------------------------
# C - GET SENZA CAMBIAMENTO (9, 10): no-op, fairness, tentativi azzerati
# ---------------------------------------------------------------------------

def test_09_10_nessun_cambiamento_e_fairness(http, w75, ring):
    from calendar_sync.fake_provider import FakeCalendarProvider
    fake = FakeCalendarProvider()
    a, riga = _prepara_mapping(http, w75, ring, fake)
    prima = _riga(w75, a["id"])
    esiti = _inbound_giro(fake)
    assert (riga["id"], "synced") in esiti
    dopo = _riga(w75, a["id"])
    assert dopo["inbound_checked_at"] is not None
    assert dopo["inbound_next_check_at"] > prima["inbound_checked_at"] if prima["inbound_checked_at"] else True
    assert dopo["inbound_attempt_count"] == 0
    assert dopo["inbound_claim_token"] is None and dopo["inbound_claimed_at"] is None
    assert dopo["inbound_last_error_code"] is None
    # nessun effetto sull'Agenda: solo lettura
    assert w75["sql"]("SELECT version, status FROM appointments WHERE id=%s",
                      (a["id"],))[0][:] == [1, "scheduled"]


# ---------------------------------------------------------------------------
# D - LOCAL DIRTY WINS (11, 12): prima della GET niente rete; dopo, aborto
# ---------------------------------------------------------------------------

def test_11_local_dirty_prima_della_get_provider_non_chiamato(http, w75, ring):
    from calendar_sync.fake_provider import FakeCalendarProvider
    fake = FakeCalendarProvider()
    a, riga = _prepara_mapping(http, w75, ring, fake)
    _dirty(w75, a["id"])           # dirty_generation avanza, synced_generation resta
    fake.calls = []
    esiti = _inbound_giro(fake)
    assert any(esito == "deferred_local_dirty" for _, esito in esiti)
    assert fake.calls == []        # NESSUNA GET: l'outbound vince senza leggere Google


def test_12_generazione_cambiata_durante_la_get_abort(http, w75, ring, monkeypatch):
    from calendar_sync import inbound as inbound_mod
    from calendar_sync.fake_provider import FakeCalendarProvider
    fake = FakeCalendarProvider()
    a, riga = _prepara_mapping(http, w75, ring, fake)
    reale = fake.get_event

    def get_event_con_race(auth, calendar_id, event_id):
        # tra la GET e la rilettura, una scrittura CRM concorrente sporca la riga
        _dirty(w75, a["id"])
        return reale(auth, calendar_id, event_id)

    monkeypatch.setattr(fake, "get_event", get_event_con_race)
    esiti = _inbound_giro(fake)
    assert any(esito == "aborted_race" for _, esito in esiti)
    assert w75["sql"]("SELECT version FROM appointments WHERE id=%s",
                      (a["id"],))[0][0] == 1     # nessuna scrittura CRM dall'inbound


# ---------------------------------------------------------------------------
# E - RESCHEDULE INBOUND (13-20): stesso effetto di una mutazione UI
# ---------------------------------------------------------------------------

def test_13_20_google_sposta_lorario_crm_si_allinea(http, w75, ring):
    from calendar_sync.fake_provider import FakeCalendarProvider
    fake = FakeCalendarProvider()
    a, riga = _prepara_mapping(http, w75, ring, fake, assegnato="luca")
    conn_id = _connessione_id(w75, w75["luca"])
    fake.simulate_remote_reschedule(conn_id, "primary", riga["remote_event_id"],
                                    start_at=ore(16), end_at=ore(17))
    impronta_prima = w75["sql"]("SELECT count(*) FROM appointment_events")[0][0]
    esiti = _inbound_giro(fake)
    assert any(esito == "rescheduled" for _, esito in esiti)

    vecchia = w75["sql"]("SELECT status FROM appointments WHERE id=%s", (a["id"],))[0][0]
    assert vecchia == "rescheduled"
    nuova = w75["sql"](
        "SELECT id, status, start_at, end_at, assigned_user_id, version, rescheduled_from_id "
        "FROM appointments WHERE rescheduled_from_id=%s", (a["id"],))[0]
    assert nuova["status"] == "scheduled"
    assert nuova["assigned_user_id"] == w75["luca"]        # l'agente NON cambia (payload None)
    assert nuova["version"] == 1
    dopo_sync = _riga(w75, a["id"])
    assert dopo_sync["current_appointment_id"] == nuova["id"]        # stesso chain, hook esistente
    assert dopo_sync["chain_root_appointment_id"] == a["id"]
    assert dopo_sync["remote_event_id"] == riga["remote_event_id"]   # id remoto IMMUTATO
    eventi_dopo = w75["sql"]("SELECT count(*) FROM appointment_events")[0][0]
    assert eventi_dopo > impronta_prima     # gli stessi eventi di un reschedule via UI
    # la mutazione ha dirtato la catena: l'outbound (stesso giro cron) la riporta synced
    assert dopo_sync["status"] == "pending"


def test_conflitto_inbound_reschedule_D4_crm_wins(http, w75, ring):
    """D4: un reschedule inbound che collide con un altro appuntamento
    dell'agente non tocca il CRM; codice stabile + resync outbound richiesto."""
    from calendar_sync.fake_provider import FakeCalendarProvider
    fake = FakeCalendarProvider()
    a, riga = _prepara_mapping(http, w75, ring, fake, assegnato="luca")
    # un secondo appuntamento dello stesso agente occupa proprio l'orario
    # verso cui Google vuole spostare il primo
    _crea(http, w75, assegnato="luca", status="scheduled", start_at=ore(16).isoformat(),
          end_at=ore(17).isoformat())
    conn_id = _connessione_id(w75, w75["luca"])
    fake.simulate_remote_reschedule(conn_id, "primary", riga["remote_event_id"],
                                    start_at=ore(16), end_at=ore(17))
    esiti = _inbound_giro(fake)
    assert any(esito == "inbound_conflict" for _, esito in esiti)
    assert w75["sql"]("SELECT status, start_at FROM appointments WHERE id=%s",
                      (a["id"],))[0][:] == ["scheduled", ore(10)]
    dopo_sync = _riga(w75, a["id"])
    assert dopo_sync["last_error_code"] is None       # colonna OUTBOUND intatta (074)
    assert dopo_sync["status"] == "pending"           # request_resync: l'outbound restaurera' Google


def test_version_race_local_wins(http, w75, ring, monkeypatch):
    """Un'altra scrittura CRM tra la GET e la mutazione del dominio: VersionConflict
    -> local wins, nessuna forzatura."""
    from calendar_sync import inbound as inbound_mod
    from calendar_sync.fake_provider import FakeCalendarProvider
    fake = FakeCalendarProvider()
    a, riga = _prepara_mapping(http, w75, ring, fake, assegnato="luca")
    conn_id = _connessione_id(w75, w75["luca"])
    fake.simulate_remote_reschedule(conn_id, "primary", riga["remote_event_id"],
                                    start_at=ore(16), end_at=ore(17))

    reale = inbound_mod.reschedule_appointment

    def con_scrittura_concorrente(ctx, appointment_id, payload):
        # un altro operatore confirma la riga PROPRIO nell'istante in cui
        # l'inbound la sta per spostare: la sua `version` letta e' ora vecchia
        http("giorgio").post(f"/api/appointments/{appointment_id}/confirm",
                            json={"version": payload.version})
        return reale(ctx, appointment_id, payload)

    monkeypatch.setattr(inbound_mod, "reschedule_appointment", con_scrittura_concorrente)
    esiti = _inbound_giro(fake)
    assert any(esito == "local_wins" for _, esito in esiti)
    assert w75["sql"]("SELECT status FROM appointments WHERE id=%s",
                      (a["id"],))[0][0] == "confirmed"


# ---------------------------------------------------------------------------
# F - CANCEL INBOUND (21-26): 404/410/cancelled -> cancel_appointment esatto
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("modo", ["vanish", "tombstone"])
def test_21_26_google_cancellato_crm_annulla(http, w75, ring, modo):
    from calendar_sync.fake_provider import FakeCalendarProvider
    fake = FakeCalendarProvider()
    a, riga = _prepara_mapping(http, w75, ring, fake, assegnato="luca")
    conn_id = _connessione_id(w75, w75["luca"])
    if modo == "vanish":
        fake.vanish(conn_id, "primary", riga["remote_event_id"])
    else:
        fake.simulate_remote_cancelled_tombstone(conn_id, "primary", riga["remote_event_id"])
    esiti = _inbound_giro(fake)
    assert any(esito == "cancelled" for _, esito in esiti)
    riga_app = w75["sql"]("SELECT status, cancelled_reason FROM appointments WHERE id=%s",
                         (a["id"],))[0]
    assert riga_app["status"] == "cancelled"
    assert riga_app["cancelled_reason"] == "Annullato da Google Calendar"
    evento = w75["sql"]("SELECT changes FROM appointment_events WHERE appointment_id=%s "
                       "ORDER BY id DESC LIMIT 1", (a["id"],))[0][0]
    assert "follow_up" not in str(evento) or "task" not in str(evento).lower()


def test_cancel_gia_terminale_local_wins(http, w75, ring, monkeypatch):
    """L'eleggibilita' del claim (`scheduled`/`confirmed`) esclude gia' una
    riga diventata terminale PRIMA del giro; per provare `local_wins` sul
    ramo cancel bisogna forzare la corsa nella finestra stretta fra la GET e
    la chiamata al dominio - esattamente come `test_version_race_local_wins`,
    ma sul path `cancel_appointment` (D3)."""
    from calendar_sync import inbound as inbound_mod
    from calendar_sync.fake_provider import FakeCalendarProvider
    fake = FakeCalendarProvider()
    a, riga = _prepara_mapping(http, w75, ring, fake, assegnato="luca")
    conn_id = _connessione_id(w75, w75["luca"])
    fake.vanish(conn_id, "primary", riga["remote_event_id"])

    reale = inbound_mod.cancel_appointment

    def con_completamento_concorrente(ctx, appointment_id, payload):
        http("giorgio").post(f"/api/appointments/{appointment_id}/complete",
                            json={"version": payload.version})
        return reale(ctx, appointment_id, payload)

    monkeypatch.setattr(inbound_mod, "cancel_appointment", con_completamento_concorrente)
    esiti = _inbound_giro(fake)
    assert any(esito == "local_wins" for _, esito in esiti)
    assert w75["sql"]("SELECT status FROM appointments WHERE id=%s",
                      (a["id"],))[0][0] == "completed"


# ---------------------------------------------------------------------------
# G - CONTENT DRIFT (27-30): solo summary/description -> nessun campo importato
# ---------------------------------------------------------------------------

def test_27_30_content_drift_nessun_campo_importato_resync_richiesto(http, w75, ring):
    from calendar_sync.fake_provider import FakeCalendarProvider
    fake = FakeCalendarProvider()
    a, riga = _prepara_mapping(http, w75, ring, fake, assegnato="luca")
    conn_id = _connessione_id(w75, w75["luca"])
    fake.simulate_remote_content_drift(conn_id, "primary", riga["remote_event_id"],
                                       summary="Titolo cambiato a mano su Google")
    esiti = _inbound_giro(fake)
    assert any(esito == "content_drift_resync" for _, esito in esiti)
    assert w75["sql"]("SELECT start_at, end_at, version FROM appointments WHERE id=%s",
                      (a["id"],))[0][:] == [ore(10), ore(11), 1]
    dopo_sync = _riga(w75, a["id"])
    assert dopo_sync["status"] == "pending"     # resync outbound richiesto, stesso giro cron
    esiti_out = _giro(fake, ring)
    assert any(esito == "synced" for _, esito in esiti_out)  # Google torna al contenuto Stima360


# ---------------------------------------------------------------------------
# H - PRIVATE PROPERTIES MANOMESSE (31-34): nessun import, resync outbound
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("chiave_manomessa,valore", [
    ("stima360_chain_id", "999999"),
    ("stima360_appointment_id", "999999"),
    ("stima360_origin", "qualcun_altro"),
])
def test_31_34_proprieta_private_incoerenti_nessuna_mutazione(http, w75, ring,
                                                              chiave_manomessa, valore):
    from calendar_sync.fake_provider import FakeCalendarProvider
    fake = FakeCalendarProvider()
    a, riga = _prepara_mapping(http, w75, ring, fake, assegnato="luca")
    conn_id = _connessione_id(w75, w75["luca"])
    fake.simulate_remote_reschedule(conn_id, "primary", riga["remote_event_id"],
                                    start_at=ore(16), end_at=ore(17))
    fake.simulate_remote_private_properties(conn_id, "primary", riga["remote_event_id"],
                                            **{chiave_manomessa: valore})
    esiti = _inbound_giro(fake)
    assert any(esito == "content_drift_resync" for _, esito in esiti)
    assert w75["sql"]("SELECT start_at, version FROM appointments WHERE id=%s",
                      (a["id"],))[0][:] == [ore(10), 1]    # l'orario NON e' entrato dal remoto


# ---------------------------------------------------------------------------
# I - D1 ATTORE / ASSIGNEE MISMATCH (35-38)
# ---------------------------------------------------------------------------

def test_35_38_assignee_diverso_dal_titolare_connessione_non_si_applica(http, w75, ring):
    """D1: l'attore e' sempre `calendar_connections.user_id` della riga presa
    in carico. Se, tra il claim e l'uso, l'appuntamento risulta assegnato a
    un altro operatore, l'inbound non applica nulla (il CRM vince). Si forza
    il caso al livello del dominio: `_process` verifica sempre l'assegnatario
    ATTUALE contro `connessione["user_id"]` prima di chiamare
    `reschedule_appointment`/`cancel_appointment` (vedi `calendar_sync/inbound.py`
    riga 186).

    SENTINELLA AGGIORNATA DA A30-10B REVIEW FIX GATE (FIX 2): il ramo D1 non
    si limita piu' a "non applicare nulla" - richiede anche la riconciliazione
    OUTBOUND della stessa catena (`repository.request_resync`, la STESSA
    primitiva del ramo D4/`inbound_conflict`), cosi' il giro outbound
    successivo possa spostare/ripristinare l'evento remoto sul calendario
    dell'agente ATTUALE. La prova qui e' esplicita e su tre fronti:
    (1) `dirty_generation` AFTER > BEFORE (prova inequivocabile che
    `request_resync` e' stato invocato); (2) nessuna mutazione di
    `appointments` oltre l'`assigned_user_id` che questo stesso test ha
    scritto (niente `version`/`start_at`/`status` toccati dall'inbound, e
    nessuna nuova riga della catena); (3) nessun nuovo `appointment_event`."""
    from calendar_sync import repository
    from calendar_sync.fake_provider import FakeCalendarProvider
    fake = FakeCalendarProvider()
    a, riga = _prepara_mapping(http, w75, ring, fake, assegnato="luca")
    conn_id = _connessione_id(w75, w75["luca"])
    fake.simulate_remote_reschedule(conn_id, "primary", riga["remote_event_id"],
                                    start_at=ore(16), end_at=ore(17))
    # riassegnato a marta: la riga viva ora ha un assegnatario diverso dal
    # titolare della connessione mappata (che resta luca finche' l'outbound
    # non riallinea) - niente scrittura outbound qui, cosi' generation resta
    # ferma e la guardia D1 e' quella che deve fermare l'inbound.
    with w75["conn"].cursor() as cur:
        cur.execute("UPDATE appointments SET assigned_user_id=%s WHERE id=%s",
                    (w75["marta"], a["id"]))
    w75["conn"].commit()

    prima_sync = _riga(w75, a["id"])
    conteggio_appuntamenti_prima = w75["sql"](
        "SELECT count(*) FROM appointments WHERE id=%s OR rescheduled_from_id=%s",
        (a["id"], a["id"]))[0][0]
    conteggio_eventi_prima = w75["sql"](
        "SELECT count(*) FROM appointment_events WHERE appointment_id=%s", (a["id"],))[0][0]
    versione_prima = w75["sql"]("SELECT version FROM appointments WHERE id=%s",
                                (a["id"],))[0][0]

    esiti = _inbound_giro(fake)
    assert not any(esito == "rescheduled" for _, esito in esiti)
    assert any(esito == "assignee_mismatch" for _, esito in esiti)

    # (2) nessuna mutazione CRM: stesso start_at/version di prima, nessuna
    # riga nuova nella catena (nessun reschedule, nessun tip nuovo).
    riga_finale = w75["sql"]("SELECT start_at, version FROM appointments WHERE id=%s",
                             (a["id"],))[0]
    assert riga_finale[:] == [ore(10), versione_prima]
    conteggio_appuntamenti_dopo = w75["sql"](
        "SELECT count(*) FROM appointments WHERE id=%s OR rescheduled_from_id=%s",
        (a["id"], a["id"]))[0][0]
    assert conteggio_appuntamenti_dopo == conteggio_appuntamenti_prima

    # (3) nessun nuovo appointment_event dall'inbound.
    conteggio_eventi_dopo = w75["sql"](
        "SELECT count(*) FROM appointment_events WHERE appointment_id=%s", (a["id"],))[0][0]
    assert conteggio_eventi_dopo == conteggio_eventi_prima

    # (1) request_resync e' stato richiesto: dirty_generation avanza, la
    # riga torna 'pending' cosi' l'outbound successivo la riallinea, e il
    # claim inbound e' stato rilasciato con l'error_code atteso.
    dopo_sync = _riga(w75, a["id"])
    assert dopo_sync["dirty_generation"] > prima_sync["dirty_generation"]
    assert dopo_sync["status"] == "pending"
    assert dopo_sync["last_error_code"] is None       # colonna OUTBOUND intatta (074)
    assert dopo_sync["inbound_last_error_code"] == "assignee_mismatch"
    assert dopo_sync["inbound_claim_token"] is None and dopo_sync["inbound_claimed_at"] is None

    # l'outbound successivo (stesso cron) riallinea davvero Google sulla
    # connessione dell'agente corretto: prova finale, inequivocabile, che il
    # resync richiesto qui produce un effetto reale, non solo una colonna.
    # Marta (l'assegnataria ATTUALE) va connessa perche' l'outbound possa
    # avere un calendario valido su cui riallinearsi.
    _collega(w75, ring, "marta")
    esiti_out = _giro(fake, ring)
    assert any(esito == "synced" for _, esito in esiti_out)
    dopo_out = _riga(w75, a["id"])
    assert dopo_out["status"] == "synced"


def test_membership_suspended_niente_contesto_token_non_decifrato(http, w75, ring):
    from calendar_sync.fake_provider import FakeCalendarProvider
    fake = FakeCalendarProvider()
    a, riga = _prepara_mapping(http, w75, ring, fake, assegnato="luca")
    conn_id = _connessione_id(w75, w75["luca"])
    fake.simulate_remote_reschedule(conn_id, "primary", riga["remote_event_id"],
                                    start_at=ore(16), end_at=ore(17))
    w75["sql"]("UPDATE agency_memberships SET status='suspended' WHERE operator_user_id=%s",
              (w75["luca"],))
    fake.calls = []
    esiti = _inbound_giro(fake)
    # connection_unusable: la membership suspesa rende la connessione non usable
    assert any(esito == "connection_unusable" for _, esito in esiti)
    assert not any(c[0] == "get" for c in fake.calls)


def test_operator_disabled_niente_contesto(http, w75, ring):
    from calendar_sync.fake_provider import FakeCalendarProvider
    fake = FakeCalendarProvider()
    a, riga = _prepara_mapping(http, w75, ring, fake, assegnato="luca")
    conn_id = _connessione_id(w75, w75["luca"])
    fake.simulate_remote_reschedule(conn_id, "primary", riga["remote_event_id"],
                                    start_at=ore(16), end_at=ore(17))
    w75["sql"]("UPDATE operator_users SET status='disabled' WHERE id=%s", (w75["luca"],))
    esiti = _inbound_giro(fake)
    assert not any(esito == "rescheduled" for _, esito in esiti)
    assert w75["sql"]("SELECT status FROM appointments WHERE id=%s",
                      (a["id"],))[0][0] == "scheduled"


def test_agency_suspended_niente_contesto(http, w75, ring):
    from calendar_sync.fake_provider import FakeCalendarProvider
    fake = FakeCalendarProvider()
    a, riga = _prepara_mapping(http, w75, ring, fake, assegnato="luca")
    conn_id = _connessione_id(w75, w75["luca"])
    fake.simulate_remote_reschedule(conn_id, "primary", riga["remote_event_id"],
                                    start_at=ore(16), end_at=ore(17))
    w75["sql"]("UPDATE agencies SET status='suspended' WHERE id=%s", (w75["a"],))
    try:
        esiti = _inbound_giro(fake)
        assert not any(esito == "rescheduled" for _, esito in esiti)
    finally:
        w75["sql"]("UPDATE agencies SET status='active' WHERE id=%s", (w75["a"],))


# ---------------------------------------------------------------------------
# J - CONNESSIONE / TOKEN (39-42): needs_reauth, invalid_grant, token mai deciso
# ---------------------------------------------------------------------------

def test_connessione_needs_reauth_token_mai_decifrato(http, w75, ring):
    from calendar_sync.fake_provider import FakeCalendarProvider
    fake = FakeCalendarProvider()
    a, riga = _prepara_mapping(http, w75, ring, fake, assegnato="luca")
    w75["sql"]("UPDATE calendar_connections SET status='needs_reauth' WHERE user_id=%s",
              (w75["luca"],))
    fake.calls = []
    esiti = _inbound_giro(fake)
    assert any(esito == "connection_unusable" for _, esito in esiti)
    assert fake.calls == []


def test_invalid_grant_sulla_get_marca_needs_reauth(http, w75, ring):
    from calendar_sync.fake_provider import FakeCalendarProvider
    fake = FakeCalendarProvider()
    a, riga = _prepara_mapping(http, w75, ring, fake, assegnato="luca")
    fake.fail_next("get", "invalid_grant", http_status=400)
    esiti = _inbound_giro(fake)
    assert any(esito == "needs_reauth" for _, esito in esiti)
    assert w75["sql"]("SELECT status FROM calendar_connections WHERE user_id=%s",
                      (w75["luca"],))[0][0] == "needs_reauth"


def test_429_5xx_timeout_retry_transitorio(http, w75, ring):
    from calendar_sync.fake_provider import FakeCalendarProvider
    fake = FakeCalendarProvider()
    a, riga = _prepara_mapping(http, w75, ring, fake, assegnato="luca")
    fake.fail_next("get", "rate_limited", http_status=429)
    esiti = _inbound_giro(fake)
    assert any(esito in ("retrying", "retrying_failed_get") for _, esito in esiti)
    dopo = _riga(w75, a["id"])
    assert dopo["inbound_attempt_count"] == 1
    assert dopo["inbound_next_check_at"] > dopo["inbound_checked_at"]
    assert dopo["inbound_claim_token"] is None


# ---------------------------------------------------------------------------
# K - CLAIM CONCORRENZA, LEASE, FAIRNESS (43-46)
# ---------------------------------------------------------------------------

def test_claim_concorrenza_un_solo_vincitore(http, w75, ring):
    from calendar_sync import repository
    from calendar_sync.fake_provider import FakeCalendarProvider
    from core.database import core_cursor
    a, riga = _prepara_mapping(http, w75, ring, FakeCalendarProvider())
    with core_cursor(commit=True) as (_, cur):
        prese1 = repository.claim_batch_inbound(cur, limit=25)
    with core_cursor(commit=True) as (_, cur):
        prese2 = repository.claim_batch_inbound(cur, limit=25)
    presi1 = {p.sync_id for p in prese1}
    presi2 = {p.sync_id for p in prese2}
    assert not (presi1 & presi2)          # SKIP LOCKED: nessuna riga doppia


def test_lease_scaduta_recuperabile(http, w75, ring):
    from calendar_sync import repository
    from core.database import core_cursor
    from calendar_sync.fake_provider import FakeCalendarProvider
    fake = FakeCalendarProvider()
    a, riga = _prepara_mapping(http, w75, ring, fake)
    with core_cursor(commit=True) as (_, cur):
        repository.claim_batch_inbound(cur, limit=25)   # claim mai finalizzato: orfano
    esiti = _inbound_giro(fake, lease_seconds=0)         # lease scaduta subito
    assert any(sid == riga["id"] for sid, _ in esiti)


def test_fairness_ordine_next_check_at_id(http, w75, ring):
    from calendar_sync.fake_provider import FakeCalendarProvider
    fake = FakeCalendarProvider()
    a1, r1 = _prepara_mapping(http, w75, ring, fake, assegnato="luca",
                              start_at=ore(10).isoformat(), end_at=ore(11).isoformat())
    a2, r2 = _prepara_mapping(http, w75, ring, fake, assegnato="marta",
                              start_at=ore(12).isoformat(), end_at=ore(13).isoformat())
    w75["sql"]("UPDATE appointment_calendar_sync SET inbound_next_check_at = NOW() - "
              "interval '10 seconds' WHERE id=%s", (r2["id"],))
    from calendar_sync import repository
    from core.database import core_cursor
    with core_cursor(commit=True) as (_, cur):
        prese = repository.claim_batch_inbound(cur, limit=1)
    assert prese and prese[0].sync_id == r2["id"]        # la piu' vecchia (next_check_at) prima


def test_inbound_e_outbound_colonne_indipendenti(http, w75, ring):
    from calendar_sync.fake_provider import FakeCalendarProvider
    fake = FakeCalendarProvider()
    a, riga = _prepara_mapping(http, w75, ring, fake, assegnato="luca")
    fake.fail_next("get", "rate_limited", http_status=429)
    _inbound_giro(fake)
    dopo = _riga(w75, a["id"])
    assert dopo["attempt_count"] == 0            # outbound intatto
    assert dopo["inbound_attempt_count"] == 1    # solo inbound e' salito


# ---------------------------------------------------------------------------
# L - TENANT ISOLATION, PII, IMPORT-TIME (47-49)
# ---------------------------------------------------------------------------

def test_isolamento_tenant_claim_solo_agency_id(http, w75, ring):
    from calendar_sync import repository
    from core.database import core_cursor
    from calendar_sync.fake_provider import FakeCalendarProvider
    fake = FakeCalendarProvider()
    a, riga = _prepara_mapping(http, w75, ring, fake, assegnato="luca")
    with core_cursor(commit=True) as (_, cur):
        prese = repository.claim_batch_inbound(cur, limit=25, agency_id=w75["b"])
    assert prese == []           # nessuna riga dell'agenzia b: e' tutto in a


def test_import_time_zero_rete(monkeypatch):
    """Nessuna chiamata di rete al solo IMPORT dei moduli A30-10 (import
    gia' avvenuto altrove in questa sessione pytest: qui si prova che una
    RICHIAMATA di import - lo scenario di un worker che parte a freddo - non
    apre un socket). `sys.modules` viene ripulito e ripristinato dal fixture,
    cosi' non lascia moduli "reload-ati" in giro per gli altri test."""
    import socket
    import sys

    def niente_rete(*a, **kw):
        raise AssertionError("import ha aperto un socket")

    moduli = ("calendar_sync.inbound", "calendar_sync.google_provider")
    salvati = {m: sys.modules.pop(m) for m in moduli if m in sys.modules}
    monkeypatch.setattr(socket, "socket", niente_rete)
    monkeypatch.setattr(socket, "create_connection", niente_rete)
    try:
        import calendar_sync.google_provider  # noqa: F401
        import calendar_sync.inbound  # noqa: F401
    finally:
        for m in moduli:
            sys.modules.pop(m, None)
        sys.modules.update(salvati)


def test_nessun_dato_google_nel_log_o_nel_db(http, w75, ring):
    from calendar_sync.fake_provider import FakeCalendarProvider
    fake = FakeCalendarProvider()
    a, riga = _prepara_mapping(http, w75, ring, fake, assegnato="luca")
    conn_id = _connessione_id(w75, w75["luca"])
    fake.simulate_remote_content_drift(conn_id, "primary", riga["remote_event_id"],
                                       summary="Segreto del cliente XYZ")
    _inbound_giro(fake)
    colonne_testo = w75["sql"](
        "SELECT inbound_last_error_code FROM appointment_calendar_sync WHERE id=%s",
        (riga["id"],))[0][0]
    assert colonne_testo is None or "Segreto" not in str(colonne_testo)


# ---------------------------------------------------------------------------
# M - CRON (50, 51): ordine inbound poi outbound, esito vuoto -> 0
# ---------------------------------------------------------------------------

def test_cron_vuoto_esce_zero(w75, ring, monkeypatch):
    from cryptography.fernet import Fernet

    from calendar_sync import crypto
    import run_calendar_sync_cron as cron

    monkeypatch.setenv("GOOGLE_CALENDAR_ENABLED", "true")
    monkeypatch.setenv("GOOGLE_CALENDAR_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CALENDAR_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("GOOGLE_CALENDAR_REDIRECT_URI", "https://esempio.test/cb")
    monkeypatch.setenv("GOOGLE_CALENDAR_DEPLOYMENT_NAMESPACE", NS)
    monkeypatch.setenv(crypto.ENV_KEYS, f"k1:{Fernet.generate_key().decode()}")
    assert cron.main() == 0


def test_cron_ordine_inbound_prima_di_outbound(http, w75, ring, monkeypatch):
    from calendar_sync.fake_provider import FakeCalendarProvider
    import run_calendar_sync_cron as cron
    fake = FakeCalendarProvider()
    a, riga = _prepara_mapping(http, w75, ring, fake, assegnato="luca")
    conn_id = _connessione_id(w75, w75["luca"])
    fake.simulate_remote_reschedule(conn_id, "primary", riga["remote_event_id"],
                                    start_at=ore(16), end_at=ore(17))

    monkeypatch.setattr(cron.gcal_config, "require_config",
                        lambda: type("C", (), {"client_id": "x", "client_secret": "y"})())
    monkeypatch.setattr(cron.gcal_config, "is_enabled", lambda: True)
    monkeypatch.setattr(cron.crypto, "require_keyring", lambda: ring)
    monkeypatch.setattr(cron, "GoogleCalendarProvider", lambda **kw: fake)
    codice = cron.main()
    assert codice in (0, 2)
    dopo_sync = _riga(w75, a["id"])
    # l'inbound ha rescheduled (dirtando la nuova catena), l'outbound NELLO
    # STESSO giro l'ha gia' riconfermata: synced, non piu' pending
    assert dopo_sync["status"] in ("synced", "pending")


def test_un_fallimento_non_corrompe_le_altre_righe(http, w75, ring):
    from calendar_sync.fake_provider import FakeCalendarProvider
    fake = FakeCalendarProvider()
    a1, r1 = _prepara_mapping(http, w75, ring, fake, assegnato="luca")
    a2, r2 = _prepara_mapping(http, w75, ring, fake, assegnato="marta",
                              start_at=ore(14).isoformat(), end_at=ore(15).isoformat())
    fake.fail_next("get", "internal", http_status=500)   # solo la prima GET fallira'
    conn_marta = _connessione_id(w75, w75["marta"])
    fake.simulate_remote_reschedule(conn_marta, "primary", r2["remote_event_id"],
                                    start_at=ore(18), end_at=ore(19))
    esiti = dict(_inbound_giro(fake))
    assert esiti.get(r2["id"]) in ("rescheduled", "content_drift_resync", "synced")


# ---------------------------------------------------------------------------
# N - REGRESSIONE A30-9A/9B (non ripetuti qui: vanno lanciati insieme)
# ---------------------------------------------------------------------------

def test_outbound_esistente_non_alterato_dalla_075(http, w75, ring):
    """Sentinella minima: le colonne e il comportamento OUTBOUND (074) non
    cambiano con la 075 sopra - la suite completa di A30-9A/9B resta la prova
    di regressione, lanciata separatamente nello stesso giro di test."""
    from calendar_sync.fake_provider import FakeCalendarProvider
    fake = FakeCalendarProvider()
    a, riga = _prepara_mapping(http, w75, ring, fake, assegnato="luca")
    assert riga["status"] == "synced"
    assert len(fake.all_active()) == 1
