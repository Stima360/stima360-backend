"""CRM-OPS-3 - ACQUISIZIONI, su PostgreSQL VERO.

Banco: il mondo di A30-2/A30-13B/A31-2 (Agenda completa, Google acceso solo
come namespace) + la 081 VERA. Adattamenti del banco, dichiarati e copiati
dai file veri delle migration, mai riscritti:
  * `property_contacts` (002), `property_price_history` e
    `property_status_history` (003): estratti dai file;
  * `contacts_agency_scope_unq` (030): lo stesso ALTER;
  * `property_leads`, `property_documents`, `property_photos` (002) e le
    tabelle BUY lette dal dettaglio immobile (`_ddl_buy` di A31-3, stessi
    file veri 004-010): servono a `GET/PATCH /api/property`;
  * le colonne di `properties` che lo schema minimo condiviso non ha
    (002/080): aggiunte come colonne semplici.

SOLO un PostgreSQL LOCALE: un `P29_TEST_DSN` che non punta a un socket Unix
o a localhost fa FALLIRE il modulo prima che il banco crei qualunque cosa
(stessa guardia di CRM-OPS-2). Il banco crea e cancella il proprio database.

Ogni scrittura passa dai router VERI (`/api/acquisitions`, `/api/appointments`,
`/api/property`) su un'app FastAPI di test: lo scope e' sostituito, nient'altro.
"""
from __future__ import annotations

import os
import re
from urllib.parse import parse_qs, urlparse
from datetime import date, timedelta
from decimal import Decimal

import pytest

from tests import test_a30_2_appointments_postgres as a30_2
from tests.test_a30_2_appointments_postgres import db, mondo  # noqa: F401
from tests.test_a30_13b_create_permissions_postgres import agenda_completa, w  # noqa: F401
from tests.test_a31_2_buyer_visits_postgres import schema_a31, v  # noqa: F401
from tests.test_a31_3_buyer_visits_facade_postgres import _ddl_buy

DSN = os.getenv("P29_TEST_DSN")

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare CRM-OPS-3")



def _dsn_locale(dsn: str) -> None:
    """Fallisce se il DSN non e' locale: socket Unix o localhost, e nient'altro."""
    parsed = urlparse(dsn)
    host = parsed.hostname or (parse_qs(parsed.query).get("host") or [""])[0]
    if not (host == "" or host.startswith("/") or host in ("localhost", "127.0.0.1", "::1")):
        pytest.fail(f"P29_TEST_DSN non locale (host={host!r}): rifiutato")


@pytest.fixture(scope="module", autouse=True)
def _solo_locale():
    """Autouse e di modulo: gira PRIMA di `db`, che crea il database."""
    _dsn_locale(DSN)


MIGRAZIONI = a30_2.MIGRAZIONI
VERSIONE = "081_crm_ops_3_acquisitions"
ore, futuro, chiave = a30_2.ore, a30_2.futuro, a30_2.chiave
MESSAGGIO = "L’incarico può essere generato solo da un’acquisizione."


def _estrai(file, nome):
    testo = (MIGRAZIONI / file).read_text(encoding="utf-8")
    trovato = re.search(rf"CREATE TABLE IF NOT EXISTS {nome} \(.*?\n\);", testo, re.S)
    assert trovato, (file, nome)
    return trovato.group(0)


COLONNE_IMMOBILE = """
ALTER TABLE properties
    ADD COLUMN IF NOT EXISTS code VARCHAR(50) UNIQUE,
    ADD COLUMN IF NOT EXISTS property_type VARCHAR(50) NOT NULL DEFAULT 'apartment',
    ADD COLUMN IF NOT EXISTS classification VARCHAR(1),
    ADD COLUMN IF NOT EXISTS province VARCHAR(80),
    ADD COLUMN IF NOT EXISTS region VARCHAR(50),
    ADD COLUMN IF NOT EXISTS microzone VARCHAR(120),
    ADD COLUMN IF NOT EXISTS energy_class VARCHAR(10),
    ADD COLUMN IF NOT EXISTS asking_price NUMERIC(14,2),
    ADD COLUMN IF NOT EXISTS mandate_type VARCHAR(80),
    ADD COLUMN IF NOT EXISTS mandate_start DATE,
    ADD COLUMN IF NOT EXISTS mandate_end DATE,
    ADD COLUMN IF NOT EXISTS assigned_to VARCHAR(200),
    ADD COLUMN IF NOT EXISTS assigned_agent_id BIGINT,
    ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ;
"""


@pytest.fixture(scope="module")
def schema_acq(schema_a31):  # noqa: F811
    with schema_a31["conn"].cursor() as cur:
        cur.execute(COLONNE_IMMOBILE)
        cur.execute("DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = "
                    "'contacts_agency_scope_unq') THEN ALTER TABLE contacts ADD CONSTRAINT "
                    "contacts_agency_scope_unq UNIQUE (agency_id, id); END IF; END $$;")
        cur.execute(_estrai("002_property_01.sql", "property_contacts"))
        for figlia in ("property_leads", "property_documents", "property_photos"):
            cur.execute(_estrai("002_property_01.sql", figlia))
        cur.execute(_ddl_buy())
        cur.execute(_estrai("003_property_02.sql", "property_price_history"))
        cur.execute(_estrai("003_property_02.sql", "property_status_history"))
        cur.execute((MIGRAZIONI / f"{VERSIONE}.sql").read_text(encoding="utf-8"))
    schema_a31["conn"].commit()
    return schema_a31


def _pulisci(sql):
    sql("ALTER TABLE properties DISABLE TRIGGER trg_properties_mandate_origin")
    sql("UPDATE properties SET acquisition_id = NULL")
    sql("ALTER TABLE properties ENABLE TRIGGER trg_properties_mandate_origin")
    sql("ALTER TABLE acquisition_events DISABLE TRIGGER trg_acquisition_events_append_only")
    sql("DELETE FROM acquisition_events")
    sql("ALTER TABLE acquisition_events ENABLE TRIGGER trg_acquisition_events_append_only")
    sql("ALTER TABLE acquisitions DISABLE TRIGGER trg_acquisitions_refuse_delete")
    sql("DELETE FROM acquisitions")
    sql("ALTER TABLE acquisitions ENABLE TRIGGER trg_acquisitions_refuse_delete")
    for t in ("property_price_history", "property_status_history", "property_contacts"):
        sql(f"DELETE FROM {t}")


@pytest.fixture
def k(v, schema_acq, monkeypatch):  # noqa: F811
    """Il mondo di A31-2 + proprietari: Mario (owner, principale) e Bruno
    (seller) sul Bilocale; un immobile senza proprietari; un immobile
    dell'agenzia B. Client HTTP con i router VERI."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from acquisitions.router import router as acquisizioni
    from appointments.router import router as agenda
    from operator_auth.context import OperatorContext
    from operator_auth.dependencies import legacy_basic_agency_context, require_operator
    from property.router import router as immobili

    sql = v["sql"]
    v["conn"].rollback()
    _pulisci(sql)
    sql("UPDATE properties SET code = 'IMM-' || id, property_type = 'apartment', "
        "address = 'Via Roma', civic_number = '1', asking_price = 180000 WHERE id = %s",
        (v["casa"],))
    sql("INSERT INTO property_contacts (property_id, contact_id, role, is_primary) "
        "VALUES (%s,%s,'owner',TRUE), (%s,%s,'seller',FALSE)",
        (v["casa"], v["mario"], v["casa"], v["bruno"]))
    vuota = sql("INSERT INTO properties (agency_id, title, city) VALUES (%s,'Senza proprietari',"
                "'Tortoreto') RETURNING id", (v["a"],))[0][0]
    altra = sql("INSERT INTO properties (agency_id, title, city) VALUES (%s,'Di B','Teramo') "
                "RETURNING id", (v["b"],))[0][0]
    sql("INSERT INTO property_contacts (property_id, contact_id, role) VALUES (%s,%s,'owner')",
        (altra, v["contatto_b"]))
    seconda = sql("INSERT INTO properties (agency_id, title, city) VALUES (%s,'Trilocale',"
                  "'Giulianova') RETURNING id", (v["a"],))[0][0]
    sql("INSERT INTO property_contacts (property_id, contact_id, role) VALUES (%s,%s,'owner'),"
        "(%s,%s,'tenant')", (seconda, v["bruno"], seconda, v["mario"]))

    ruoli = {"giorgio": (v["giorgio"], v["a"], "agency_owner", False),
             "anna": (v["anna"], v["a"], "agency_admin", False),
             "luca": (v["luca"], v["a"], "agent", False),
             "marta": (v["marta"], v["a"], "agent", False),
             "estraneo": (v["estraneo"], v["b"], "agent", False),
             "supremo": (v["supremo"], v["a"], None, True)}
    stato = {"chi": "giorgio"}

    def contesto():
        user_id, agenzia, ruolo, platform = ruoli[stato["chi"]]
        return OperatorContext(user_id=user_id, agency_id=agenzia, role=ruolo,
                               is_platform_admin=platform, session_id=None,
                               auth_channel="operator_session")

    app = FastAPI()
    for r in (acquisizioni, agenda, immobili):
        app.include_router(r)
    app.dependency_overrides[legacy_basic_agency_context] = contesto
    app.dependency_overrides[require_operator] = contesto
    client = TestClient(app)

    def api(chi):
        stato["chi"] = chi
        return client

    yield {**v, "api": api, "vuota": vuota, "altra": altra, "seconda": seconda}
    v["conn"].rollback()
    _pulisci(sql)


def _appuntamento(agente=None, h=10, **kw):
    corpo = {"start_at": futuro(h).isoformat(), "client_request_id": chiave()}
    if agente is not None:
        corpo["assigned_user_id"] = agente
    corpo.update(kw)
    return corpo


def _corpo(k, **kw):
    corpo = {"property_id": k["casa"], "owner_contact_id": k["mario"],
             "asking_price": "185000.00", "sale_timing": "within_3_months",
             "source": "referral", "notes": "Vuole vendere entro l'estate",
             "appointment": _appuntamento(k["luca"], notes="Citofono Rossi")}
    corpo.update(kw)
    return corpo


def _crea(k, chi="giorgio", **kw):
    r = k["api"](chi).post("/api/acquisitions", json=_corpo(k, **kw))
    assert r.status_code == 201, r.text
    return r.json()


def _conta(k, tabella, where="TRUE", par=None):
    return k["sql"](f"SELECT count(*) FROM {tabella} WHERE {where}", par)[0][0]


def _riga(k, acquisition_id):
    return dict(k["sql"]("SELECT * FROM acquisitions WHERE id = %s", (acquisition_id,))[0])


def _eventi(k, acquisition_id):
    return [r[0] for r in k["sql"]("SELECT event_type FROM acquisition_events "
                                   "WHERE acquisition_id = %s ORDER BY id", (acquisition_id,))]


def _app(k, appointment_id):
    return dict(k["sql"]("SELECT * FROM appointments WHERE id = %s", (appointment_id,))[0])


def _al_sopralluogo(k, acq, chi="giorgio"):
    """Porta l'acquisizione a `inspection_done` dall'Agenda (completamento)."""
    app = _app(k, acq["appointment_id"])
    r = k["api"](chi).post(f"/api/appointments/{app['id']}/complete",
                           json={"version": app["version"]})
    assert r.status_code == 200, r.text
    return k["api"](chi).get(f"/api/acquisitions/{acq['id']}").json()


def _nel_passato(k, acq, h=10):
    """Sposta l'appuntamento (gia' creato) nel passato del database, cosi'
    l'Agenda lo lascia completare/registrare come mancato."""
    k["sql"]("UPDATE appointments SET start_at = %s, end_at = %s WHERE id = %s",
             (ore(h), ore(h + 1), acq["appointment_id"]))


# ---------------------------------------------------------------------------
# A - MIGRATION 081
# ---------------------------------------------------------------------------

def test_01_migrazione_oggetti_e_down_sicura(k):
    sql = k["sql"]
    assert sql("SELECT is_nullable FROM information_schema.columns WHERE table_name = "
               "'properties' AND column_name = 'acquisition_id'")[0][0] == "YES"
    trigger = {r[0] for r in sql("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal "
                                 "AND tgname LIKE 'trg_%acquisition%' OR tgname = "
                                 "'trg_properties_mandate_origin'")}
    assert {"trg_acquisitions_guard", "trg_acquisitions_refuse_delete",
            "trg_acquisition_events_append_only", "trg_properties_mandate_origin"} <= trigger
    # con un'acquisizione la down rifiuta e non cambia nulla
    _crea(k)
    giu = (MIGRAZIONI / f"{VERSIONE}_down.sql").read_text(encoding="utf-8")
    import psycopg2
    with pytest.raises(psycopg2.Error, match="acquisition"):
        sql(giu)
    # la down apre la PROPRIA transazione (BEGIN): la si chiude qui
    sql("ROLLBACK")
    assert _conta(k, "acquisitions") == 1


def test_02_migrazione_down_e_riapplicazione_idempotente(k):
    sql = k["sql"]
    _pulisci(sql)
    giu = (MIGRAZIONI / f"{VERSIONE}_down.sql").read_text(encoding="utf-8")
    su = (MIGRAZIONI / f"{VERSIONE}.sql").read_text(encoding="utf-8")
    try:
        sql(giu)
        assert sql("SELECT to_regclass('acquisitions'), to_regclass('acquisition_events')")[0] \
            == [None, None]
        assert _conta(k, "information_schema.columns",
                      "table_name = 'properties' AND column_name = 'acquisition_id'") == 0
        assert _conta(k, "pg_trigger", "tgname = 'trg_properties_mandate_origin'") == 0
    finally:
        sql(su)
        sql(su)   # idempotente: la seconda applicazione non cambia nulla
    assert _conta(k, "pg_trigger", "tgname = 'trg_properties_mandate_origin'") == 1


# ---------------------------------------------------------------------------
# B - CREAZIONE: acquisizione + appuntamento in UNA transazione
# ---------------------------------------------------------------------------

def test_03_crea_acquisizione_e_appuntamento_agenda(k):
    r = _crea(k)
    assert r["status"] == "appointment_set" and r["assigned_agent_id"] == k["luca"]
    app = _app(k, r["appointment_id"])
    assert app["appointment_type"] == "seller_meeting" and app["status"] == "scheduled"
    assert app["contact_id"] == k["mario"] and app["property_id"] == k["casa"]
    assert app["assigned_user_id"] == k["luca"] and app["source"] == "crm_manual"
    assert app["end_at"] - app["start_at"] == timedelta(minutes=60)
    # note separate: operative nell'Agenda, commerciali nell'acquisizione
    assert app["notes"] == "Citofono Rossi"
    assert _riga(k, r["id"])["notes"] == "Vuole vendere entro l'estate"
    assert Decimal(str(r["asking_price"])) == Decimal("185000.00")
    assert _eventi(k, r["id"]) == ["created"]
    assert _conta(k, "appointment_events", "appointment_id = %s", (app["id"],)) == 1
    # dettaglio: immobile, proprietari reali (principale + seller), appuntamento
    assert r["property"]["code"] == f"IMM-{k['casa']}"
    owners = {o["contact_id"]: o for o in r["owners"]}
    assert set(owners) == {k["mario"], k["bruno"]} and owners[k["mario"]]["is_main"]
    assert r["appointment"]["agent_name"] and r["allowed_actions"]["mandate"] is False


def test_04_crea_idempotente_stessa_chiave(k):
    corpo = _corpo(k)
    api = k["api"]("giorgio")
    primo = api.post("/api/acquisitions", json=corpo)
    secondo = api.post("/api/acquisitions", json=corpo)
    assert primo.status_code == 201 and secondo.status_code == 200, secondo.text
    assert primo.json()["id"] == secondo.json()["id"] and secondo.json()["replayed"] is True
    assert _conta(k, "acquisitions") == 1 and _conta(k, "appointments") == 1
    # la stessa chiave su un altro immobile e' un riuso
    altro = dict(corpo, property_id=k["seconda"], owner_contact_id=k["bruno"])
    r = api.post("/api/acquisitions", json=altro)
    assert r.status_code == 409 and r.json()["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_05_immobile_senza_proprietari_niente_scritto(k):
    r = k["api"]("giorgio").post("/api/acquisitions", json=_corpo(
        k, property_id=k["vuota"]))
    assert r.status_code == 422 and r.json()["code"] == "PROPERTY_WITHOUT_OWNER"
    assert _conta(k, "acquisitions") == 0 and _conta(k, "appointments") == 0


def test_06_referente_non_proprietario(k):
    # Mario sul Trilocale e' solo inquilino
    r = k["api"]("giorgio").post("/api/acquisitions", json=_corpo(
        k, property_id=k["seconda"], owner_contact_id=k["mario"]))
    assert r.status_code == 422 and r.json()["code"] == "OWNER_NOT_LINKED"
    assert _conta(k, "appointments") == 0


def test_07_una_sola_acquisizione_aperta_per_immobile(k):
    _crea(k)
    r = k["api"]("giorgio").post("/api/acquisitions", json=_corpo(k))
    assert r.status_code == 409 and r.json()["code"] == "OPEN_ACQUISITION_EXISTS"
    assert _conta(k, "appointments") == 1
    assert _conta(k, "acquisitions") == 1
    # nel database: l'indice parziale lo garantisce anche senza service
    import psycopg2
    altro = k["api"]("giorgio").post("/api/appointments", json={
        "appointment_type": "seller_meeting", "assigned_user_id": k["marta"],
        "start_at": futuro(18).isoformat(), "end_at": futuro(19).isoformat(),
        "client_request_id": chiave()}).json()["id"]
    with pytest.raises(psycopg2.Error, match="idx_acquisitions_open_property"):
        k["sql"]("INSERT INTO acquisitions (agency_id, property_id, owner_contact_id, "
                 "assigned_agent_id, appointment_id, created_by_user_id) VALUES "
                 "(%s,%s,%s,%s,%s,%s)", (k["a"], k["casa"], k["mario"], k["marta"],
                                         altro, k["giorgio"]))
    k["conn"].rollback()


def test_08_tenancy_agency_id_rifiutato_e_immobile_di_altra_agenzia(k):
    api = k["api"]("giorgio")
    r = api.post("/api/acquisitions", json=dict(_corpo(k), agency_id=k["b"]))
    assert r.status_code == 422 and r.json()["code"] == "VALIDATION_ERROR"
    r = api.post("/api/acquisitions", json=_corpo(k, property_id=k["altra"],
                                                   owner_contact_id=k["contatto_b"]))
    assert r.status_code == 404
    assert _conta(k, "appointments") == 0


def test_09_agente_solo_per_se_stesso_manager_sceglie(k):
    # un agent senza agente esplicito: e' se stesso
    corpo = _corpo(k, appointment=_appuntamento())
    r = k["api"]("luca").post("/api/acquisitions", json=corpo)
    assert r.status_code == 201, r.text
    assert r.json()["assigned_agent_id"] == k["luca"]
    # un agent per un collega: vietato
    r = k["api"]("luca").post("/api/acquisitions", json=_corpo(
        k, property_id=k["seconda"], owner_contact_id=k["bruno"],
        appointment=_appuntamento(k["marta"])))
    assert r.status_code == 403
    # owner senza agente: obbligatorio
    r = k["api"]("giorgio").post("/api/acquisitions", json=_corpo(
        k, property_id=k["seconda"], owner_contact_id=k["bruno"], appointment=_appuntamento()))
    assert r.status_code == 422 and r.json()["code"] == "AGENT_REQUIRED"
    # un agente revocato non e' assegnabile
    r = k["api"]("giorgio").post("/api/acquisitions", json=_corpo(
        k, property_id=k["seconda"], owner_contact_id=k["bruno"],
        appointment=_appuntamento(k["revocato"])))
    assert r.status_code == 422 and r.json()["code"] == "AGENT_NOT_ACTIVE"


def test_10_conflitto_agenda_annulla_tutto(k):
    _crea(k)
    r = k["api"]("giorgio").post("/api/acquisitions", json=_corpo(
        k, property_id=k["seconda"], owner_contact_id=k["bruno"],
        appointment=_appuntamento(k["luca"])))
    assert r.status_code == 409 and r.json()["code"] == "APPOINTMENT_CONFLICT"
    assert r.json().get("alternatives") is not None
    assert _conta(k, "acquisitions") == 1 and _conta(k, "appointments") == 1


# ---------------------------------------------------------------------------
# C - VISIBILITA' E LETTURE
# ---------------------------------------------------------------------------

def test_11_agente_vede_solo_le_proprie(k):
    acq = _crea(k)
    assert k["api"]("marta").get(f"/api/acquisitions/{acq['id']}").status_code == 404
    assert k["api"]("marta").get("/api/acquisitions").json()["items"] == []
    assert [r["id"] for r in k["api"]("luca").get("/api/acquisitions").json()["items"]] \
        == [acq["id"]]
    for chi in ("giorgio", "anna", "supremo"):
        assert k["api"](chi).get(f"/api/acquisitions/{acq['id']}").status_code == 200, chi
    assert k["api"]("estraneo").get(f"/api/acquisitions/{acq['id']}").status_code == 404
    r = k["api"]("marta").patch(f"/api/acquisitions/{acq['id']}",
                                json={"version": acq["version"], "notes": "x"})
    assert r.status_code == 404


def test_12_elenco_filtri_e_colonne(k):
    acq = _crea(k)
    api = k["api"]("giorgio")
    riga = api.get("/api/acquisitions").json()["items"][0]
    for campo in ("property_code", "owner_name", "agent_name", "appointment_start_at",
                  "appointment_status", "status", "status_label", "asking_price",
                  "last_activity_at", "property_city"):
        assert campo in riga, campo
    assert riga["owner_name"] == "Mario Rossi" and riga["status_label"] == "Appuntamento fissato"
    assert api.get("/api/acquisitions?statuses=lost").json()["items"] == []
    assert len(api.get("/api/acquisitions?statuses=appointment_set,lost").json()["items"]) == 1
    assert api.get(f"/api/acquisitions?agent_id={k['marta']}").json()["items"] == []
    assert len(api.get("/api/acquisitions?search=rossi").json()["items"]) == 1
    assert len(api.get("/api/acquisitions?city=alba%20adriatica").json()["items"]) == 1
    da = (futuro(0) - timedelta(days=1)).isoformat().replace("+", "%2B")
    a = (futuro(0) + timedelta(days=1)).isoformat().replace("+", "%2B")
    assert len(api.get(f"/api/acquisitions?from={da}&to={a}").json()["items"]) == 1
    r = api.get("/api/acquisitions?statuses=boh")
    assert r.status_code == 422
    assert acq["id"]


def test_13_options_unica_fonte(k):
    o = k["api"]("luca").get("/api/acquisitions/options").json()
    assert [s["value"] for s in o["statuses"]] == [
        "appointment_set", "inspection_done", "valuation_presented", "mandate_negotiation",
        "acquired", "lost"]
    assert [s["value"] for s in o["statuses"] if not s["terminal"]] == [
        "appointment_set", "inspection_done", "valuation_presented", "mandate_negotiation"]
    etichette = {s["label"] for s in o["statuses"]}
    assert not etichette & {"Da contattare", "Contattato"}
    assert {s["value"]: s["label"] for s in o["statuses"]}["mandate_negotiation"] == \
        "Trattativa incarico"
    assert [r["value"] for r in o["lost_reasons"]] == [
        "other_agency", "commission", "price_disagreement", "owner_no_longer_selling",
        "unreachable", "property_or_documents_issue", "other"]
    assert o["appointment_type"] == "seller_meeting" and o["default_duration_minutes"] == 60
    assert o["can_assign"] is False and o["agents"]
    assert k["api"]("giorgio").get("/api/acquisitions/options").json()["can_assign"] is True


# ---------------------------------------------------------------------------
# D - AGENDA -> ACQUISIZIONE (hook nella transazione dell'Agenda)
# ---------------------------------------------------------------------------

def test_14_reschedule_dall_agenda_segue_la_riga_nuova(k):
    acq = _crea(k)
    vecchio = _app(k, acq["appointment_id"])
    r = k["api"]("giorgio").post(f"/api/appointments/{vecchio['id']}/reschedule", json={
        "version": vecchio["version"], "start_at": futuro(15).isoformat(),
        "end_at": futuro(16).isoformat()})
    assert r.status_code == 201, r.text
    nuovo = r.json()["id"]
    riga = _riga(k, acq["id"])
    assert riga["appointment_id"] == nuovo and riga["status"] == "appointment_set"
    assert _eventi(k, acq["id"])[-1] == "appointment_rescheduled"
    det = k["api"]("giorgio").get(f"/api/acquisitions/{acq['id']}").json()
    assert det["appointment"]["id"] == nuovo and det["appointment"]["status"] == "scheduled"


def test_15_completato_porta_a_sopralluogo_effettuato_mai_indietro(k):
    acq = _crea(k)
    _nel_passato(k, acq)
    det = _al_sopralluogo(k, acq)
    assert det["status"] == "inspection_done"
    assert _eventi(k, acq["id"])[-1] == "appointment_completed"
    # avanti a mano, poi un nuovo completamento non fa tornare indietro
    r = k["api"]("giorgio").post(f"/api/acquisitions/{acq['id']}/status", json={
        "version": det["version"], "status": "valuation_presented"})
    assert r.status_code == 200, r.text
    k["sql"]("UPDATE appointments SET status = 'scheduled', completed_at = NULL WHERE id = %s",
             (acq["appointment_id"],))
    _al_sopralluogo(k, acq)
    assert _riga(k, acq["id"])["status"] == "valuation_presented"


def test_16_annullato_e_mancato_non_chiudono_nuovo_appuntamento(k):
    acq = _crea(k)
    api = k["api"]("giorgio")
    # finche' l'appuntamento e' valido non se ne crea un altro
    r = api.post(f"/api/acquisitions/{acq['id']}/appointment", json={
        "version": acq["version"], "appointment": _appuntamento(k["luca"], h=15)})
    assert r.status_code == 409 and r.json()["code"] == "APPOINTMENT_STILL_OPEN"
    app = _app(k, acq["appointment_id"])
    r = api.post(f"/api/appointments/{app['id']}/cancel", json={"version": app["version"]})
    assert r.status_code == 200, r.text
    riga = _riga(k, acq["id"])
    assert riga["status"] == "appointment_set" and _eventi(k, acq["id"])[-1] == \
        "appointment_cancelled"
    r = api.post(f"/api/acquisitions/{acq['id']}/appointment", json={
        "version": riga["version"], "appointment": _appuntamento(k["luca"], h=15)})
    assert r.status_code == 201, r.text
    nuovo = r.json()["appointment_id"]
    assert nuovo != app["id"] and _app(k, nuovo)["appointment_type"] == "seller_meeting"
    assert _eventi(k, acq["id"])[-1] == "appointment_replaced"
    # sostituire l'appuntamento NON tocca lo stato commerciale
    assert r.json()["status"] == riga["status"] == "appointment_set"
    ev = k["sql"]("SELECT from_status, to_status FROM acquisition_events WHERE acquisition_id = %s "
                  "AND event_type = 'appointment_replaced'", (acq["id"],))[0]
    assert ev[0] == ev[1] == "appointment_set"
    # no_show: stessa regola
    _nel_passato(k, {"appointment_id": nuovo})
    app2 = _app(k, nuovo)
    r = api.post(f"/api/appointments/{nuovo}/no-show", json={"version": app2["version"]})
    assert r.status_code == 200, r.text
    assert _riga(k, acq["id"])["status"] == "appointment_set"
    assert _eventi(k, acq["id"])[-1] == "appointment_no_show"


def test_17_agenda_non_cambia_l_immobile_dell_acquisizione(k):
    acq = _crea(k)
    app = _app(k, acq["appointment_id"])
    r = k["api"]("giorgio").patch(f"/api/appointments/{app['id']}", json={
        "version": app["version"], "property_id": k["seconda"]})
    assert r.status_code == 409 and r.json()["code"] == "APPOINTMENT_LINKED_TO_ACQUISITION"
    assert _app(k, app["id"])["property_id"] == k["casa"]
    # le note operative si cambiano dall'Agenda e non toccano quelle commerciali
    r = k["api"]("giorgio").patch(f"/api/appointments/{app['id']}", json={
        "version": app["version"], "notes": "Portare planimetria"})
    assert r.status_code == 200, r.text
    assert _riga(k, acq["id"])["notes"] == "Vuole vendere entro l'estate"


# ---------------------------------------------------------------------------
# E - PIPELINE, PERSA, MODIFICHE
# ---------------------------------------------------------------------------

def test_18_transizioni_lato_server(k):
    acq = _crea(k)
    api = k["api"]("giorgio")
    for stato in ("valuation_presented", "inspection_done", "acquired", "lost", "boh"):
        r = api.post(f"/api/acquisitions/{acq['id']}/status",
                     json={"version": acq["version"], "status": stato})
        assert r.status_code in (409, 422), (stato, r.text)
    _nel_passato(k, acq)
    det = _al_sopralluogo(k, acq)
    r = api.post(f"/api/acquisitions/{acq['id']}/status",
                 json={"version": det["version"] - 1, "status": "valuation_presented"})
    assert r.status_code == 409 and r.json()["code"] == "VERSION_CONFLICT"
    r = api.post(f"/api/acquisitions/{acq['id']}/status",
                 json={"version": det["version"], "status": "mandate_negotiation"})
    assert r.status_code == 200 and r.json()["status"] == "mandate_negotiation"
    r = api.post(f"/api/acquisitions/{acq['id']}/status",
                 json={"version": r.json()["version"], "status": "valuation_presented"})
    assert r.status_code == 409 and r.json()["code"] == "INVALID_TRANSITION"
    assert _eventi(k, acq["id"])[-1] == "status_changed"


def test_19_persa_richiede_motivo_e_chiude(k):
    acq = _crea(k)
    api = k["api"]("luca")
    r = api.post(f"/api/acquisitions/{acq['id']}/lost", json={"version": acq["version"]})
    assert r.status_code == 422 and r.json()["code"] == "LOST_REASON_REQUIRED"
    r = api.post(f"/api/acquisitions/{acq['id']}/lost",
                 json={"version": acq["version"], "lost_reason": "boh"})
    assert r.status_code == 422
    r = api.post(f"/api/acquisitions/{acq['id']}/lost", json={
        "version": acq["version"], "lost_reason": "other_agency", "lost_notes": "Firmato con X"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["status"] == "lost" and d["lost_reason_label"] == "Altra agenzia"
    assert d["lost_notes"] == "Firmato con X" and d["lost_at"]
    assert _eventi(k, acq["id"])[-1] == "lost"
    # chiusa: niente modifiche, niente incarico
    r = api.patch(f"/api/acquisitions/{acq['id']}", json={"version": d["version"], "notes": "x"})
    assert r.status_code == 409
    # il lead e l'appuntamento non si sincronizzano
    assert _app(k, acq["appointment_id"])["status"] == "scheduled"
    # chiusa, l'immobile torna disponibile per una nuova acquisizione
    _crea(k, appointment=_appuntamento(k["marta"], h=17))


def test_20_patch_dati_commerciali_e_riassegnazione(k):
    acq = _crea(k)
    r = k["api"]("luca").patch(f"/api/acquisitions/{acq['id']}", json={
        "version": acq["version"], "assigned_agent_id": k["marta"]})
    assert r.status_code == 403
    r = k["api"]("luca").patch(f"/api/acquisitions/{acq['id']}", json={
        "version": acq["version"], "valuation_price": "175000.50",
        "owner_contact_id": k["bruno"], "sale_timing": "within_6_months"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert Decimal(str(d["valuation_price"])) == Decimal("175000.50")
    assert d["owner_contact_id"] == k["bruno"] and d["version"] == acq["version"] + 1
    r = k["api"]("luca").patch(f"/api/acquisitions/{acq['id']}", json={
        "version": d["version"], "owner_contact_id": k["contatto_b"]})
    assert r.status_code == 422 and r.json()["code"] == "OWNER_NOT_LINKED"
    r = k["api"]("luca").patch(f"/api/acquisitions/{acq['id']}", json={
        "version": d["version"], "asking_price": "-1"})
    assert r.status_code == 422
    r = k["api"]("giorgio").patch(f"/api/acquisitions/{acq['id']}", json={
        "version": d["version"], "assigned_agent_id": k["marta"]})
    assert r.status_code == 200 and r.json()["assigned_agent_id"] == k["marta"]
    assert k["api"]("luca").get(f"/api/acquisitions/{acq['id']}").status_code == 404
    assert k["api"]("marta").get(f"/api/acquisitions/{acq['id']}").status_code == 200
    # l'agente dell'appuntamento resta quello dell'Agenda
    assert _app(k, acq["appointment_id"])["assigned_user_id"] == k["luca"]
    assert _eventi(k, acq["id"]).count("updated") == 2


def test_21_lead_facoltativo_solo_riferimento(k):
    lead_b = k["sql"]("INSERT INTO leads (contact_id, agency_id, pipeline) VALUES (%s,%s,'sell') "
                      "RETURNING id", (k["contatto_b"], k["b"]))[0][0]
    r = k["api"]("giorgio").post("/api/acquisitions", json=_corpo(k, lead_id=lead_b))
    assert r.status_code == 404
    acq = _crea(k, lead_id=k["lead_mario"])
    assert acq["lead"]["id"] == k["lead_mario"]
    stato = k["sql"]("SELECT stage, status FROM leads WHERE id = %s", (k["lead_mario"],))[0]
    k["api"]("giorgio").post(f"/api/acquisitions/{acq['id']}/lost", json={
        "version": acq["version"], "lost_reason": "unreachable"})
    assert k["sql"]("SELECT stage, status FROM leads WHERE id = %s",
                    (k["lead_mario"],))[0] == stato


# ---------------------------------------------------------------------------
# F - INCARICO: solo da un'acquisizione, atomico
# ---------------------------------------------------------------------------

def _incarico(**kw):
    corpo = {"mandate_type": "Esclusiva", "mandate_start": date.today().isoformat(),
             "mandate_end": (date.today() + timedelta(days=180)).isoformat(),
             "agreed_price": "179000.00"}
    corpo.update(kw)
    return corpo


def test_22_incarico_solo_dopo_il_sopralluogo(k):
    acq = _crea(k)
    r = k["api"]("giorgio").post(f"/api/acquisitions/{acq['id']}/mandate",
                                 json=dict(_incarico(), version=acq["version"]))
    assert r.status_code == 409 and r.json()["code"] == "MANDATE_NOT_ALLOWED"
    assert k["sql"]("SELECT acquisition_id, mandate_type FROM properties WHERE id = %s",
                    (k["casa"],))[0] == [None, None]


def test_23_genera_incarico_atomico(k):
    acq = _crea(k)
    _nel_passato(k, acq)
    det = _al_sopralluogo(k, acq, chi="luca")
    r = k["api"]("luca").post(f"/api/acquisitions/{acq['id']}/mandate",
                              json=dict(_incarico(), version=det["version"]))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["status"] == "acquired" and d["acquired_at"]
    p = dict(k["sql"]("SELECT * FROM properties WHERE id = %s", (k["casa"],))[0])
    assert p["acquisition_id"] == acq["id"] and p["commercial_status"] == "mandate"
    assert p["mandate_type"] == "Esclusiva" and p["mandate_start"] == date.today()
    assert p["asking_price"] == Decimal("179000.00")
    assert p["assigned_agent_id"] == k["luca"] and p["assigned_to"]
    assert _conta(k, "property_status_history",
                  "property_id = %s AND new_value = 'mandate'", (k["casa"],)) == 1
    assert _conta(k, "property_price_history", "property_id = %s", (k["casa"],)) == 1
    assert _eventi(k, acq["id"])[-1] == "mandate_created"
    # una seconda volta: gia' generato
    r = k["api"]("luca").post(f"/api/acquisitions/{acq['id']}/mandate",
                              json=dict(_incarico(), version=d["version"]))
    assert r.status_code == 409 and r.json()["code"] == "MANDATE_ALREADY_EXISTS"
    # `acquired` non si imposta a mano
    r = k["api"]("giorgio").post(f"/api/acquisitions/{acq['id']}/status",
                                 json={"version": d["version"], "status": "acquired"})
    assert r.status_code == 409
    # un immobile con incarico da acquisizione non apre un'altra acquisizione
    r = k["api"]("giorgio").post("/api/acquisitions", json=_corpo(
        k, appointment=_appuntamento(k["marta"], h=17)))
    assert r.status_code == 409 and r.json()["code"] == "MANDATE_ALREADY_EXISTS"


def test_24_incarico_rollback_completo(k, monkeypatch):
    from acquisitions import repository

    acq = _crea(k)
    _nel_passato(k, acq)
    det = _al_sopralluogo(k, acq)
    eventi_prima = _eventi(k, acq["id"])
    originale = repository.record_event

    def guasto(cur, **kw):
        if kw.get("event_type") == "mandate_created":
            raise RuntimeError("guasto simulato")
        return originale(cur, **kw)

    monkeypatch.setattr(repository, "record_event", guasto)
    with pytest.raises(RuntimeError):
        k["api"]("giorgio").post(f"/api/acquisitions/{acq['id']}/mandate",
                                 json=dict(_incarico(), version=det["version"]))
    p = dict(k["sql"]("SELECT * FROM properties WHERE id = %s", (k["casa"],))[0])
    assert p["acquisition_id"] is None and p["mandate_type"] is None
    assert p["commercial_status"] == "draft"
    assert _riga(k, acq["id"])["status"] == "inspection_done"
    assert _eventi(k, acq["id"]) == eventi_prima
    assert _conta(k, "property_status_history") == 0


# ---------------------------------------------------------------------------
# G - PROPERTY: la regola nel service e nel database
# ---------------------------------------------------------------------------

def test_25_property_nuovo_incarico_senza_acquisizione_400(k):
    api = k["api"]("giorgio")
    r = api.patch(f"/api/property/properties/{k['seconda']}",
                  json={"mandate_end": (date.today() + timedelta(days=30)).isoformat()})
    assert r.status_code == 400 and r.json()["detail"] == MESSAGGIO
    r = api.patch(f"/api/property/properties/{k['seconda']}",
                  json={"commercial_status": "mandate"})
    assert r.status_code == 400 and r.json()["detail"] == MESSAGGIO
    # e nel database, anche scrivendo a mano
    import psycopg2
    with pytest.raises(psycopg2.errors.CheckViolation, match="CRM-OPS-3"):
        k["sql"]("UPDATE properties SET mandate_type = 'Esclusiva' WHERE id = %s",
                 (k["seconda"],))
    k["conn"].rollback()
    with pytest.raises(psycopg2.errors.CheckViolation, match="CRM-OPS-3"):
        k["sql"]("INSERT INTO properties (agency_id, title, commercial_status) "
                 "VALUES (%s, 'x', 'mandate')", (k["a"],))
    k["conn"].rollback()
    # gli aggiornamenti estranei all'incarico non si rompono
    r = api.patch(f"/api/property/properties/{k['seconda']}", json={"title": "Trilocale vista mare"})
    assert r.status_code == 200, r.text
    # `acquisition_id` non e' un campo del form Immobili
    r = api.patch(f"/api/property/properties/{k['seconda']}", json={"acquisition_id": 1})
    assert r.status_code == 422


def test_26_incarico_storico_resta_com_era(k):
    """Grandfathering: un incarico storico (senza acquisizione) si rimanda
    invariato e si azzera; cambiarne i valori e' un incarico nuovo."""
    sql = k["sql"]
    sql("ALTER TABLE properties DISABLE TRIGGER trg_properties_mandate_origin")
    sql("UPDATE properties SET mandate_type = 'Esclusiva', mandate_start = '2026-01-01', "
        "mandate_end = '2026-12-31', commercial_status = 'mandate' WHERE id = %s",
        (k["seconda"],))
    sql("ALTER TABLE properties ENABLE TRIGGER trg_properties_mandate_origin")
    api = k["api"]("giorgio")
    # property_admin rimanda tutto invariato + un altro campo
    r = api.patch(f"/api/property/properties/{k['seconda']}", json={
        "title": "Trilocale", "mandate_type": "Esclusiva", "mandate_start": "2026-01-01",
        "mandate_end": "2026-12-31", "commercial_status": "mandate", "asking_price": 210000})
    assert r.status_code == 200, r.text
    r = api.patch(f"/api/property/properties/{k['seconda']}", json={"mandate_end": "2027-06-30"})
    assert r.status_code == 400 and r.json()["detail"] == MESSAGGIO
    r = api.patch(f"/api/property/properties/{k['seconda']}", json={"commercial_status": "active"})
    assert r.status_code == 200, r.text
    r = api.patch(f"/api/property/properties/{k['seconda']}", json={"mandate_end": None})
    assert r.status_code == 200, r.text


def test_27_incarico_da_acquisizione_modificabile_origine_permanente(k):
    acq = _crea(k)
    _nel_passato(k, acq)
    det = _al_sopralluogo(k, acq)
    k["api"]("giorgio").post(f"/api/acquisitions/{acq['id']}/mandate",
                             json=dict(_incarico(), version=det["version"]))
    api = k["api"]("giorgio")
    r = api.patch(f"/api/property/properties/{k['casa']}", json={
        "mandate_type": "Non esclusiva", "mandate_end": "2027-03-31"})
    assert r.status_code == 200, r.text
    assert r.json()["acquisition_id"] == acq["id"]
    import psycopg2
    with pytest.raises(psycopg2.Error, match="permanent"):
        k["sql"]("UPDATE properties SET acquisition_id = NULL WHERE id = %s", (k["casa"],))
    k["conn"].rollback()


def test_28_property_create_non_nasce_con_incarico(k):
    api = k["api"]("giorgio")
    r = api.post("/api/property/properties", json={
        "title": "Nuovo", "city": "Tortoreto", "mandate_end": "2027-01-01"})
    assert r.status_code == 400 and r.json()["detail"] == MESSAGGIO
    r = api.post("/api/property/properties", json={
        "title": "Nuovo", "city": "Tortoreto", "commercial_status": "mandate"})
    assert r.status_code == 400 and r.json()["detail"] == MESSAGGIO


# ---------------------------------------------------------------------------
# H - GARANZIE DEL DATABASE
# ---------------------------------------------------------------------------

def test_29_database_guardie_acquisizioni(k):
    import psycopg2
    acq = _crea(k)
    sql = k["sql"]
    casi = [
        ("DELETE FROM acquisitions WHERE id = %s", (acq["id"],), "DELETE"),
        ("UPDATE acquisitions SET status = 'acquired', acquired_at = NOW() WHERE id = %s",
         (acq["id"],), "without its mandate"),
        # un contatto di un'altra agenzia: il trigger (BEFORE) lo ferma per
        # primo, la FK composita resta dietro di lui
        ("UPDATE acquisitions SET owner_contact_id = %s WHERE id = %s",
         (k["contatto_b"], acq["id"]), "not an owner"),
        ("UPDATE acquisitions SET property_id = %s WHERE id = %s",
         (k["seconda"], acq["id"]), "immutable"),
        ("UPDATE acquisition_events SET to_status = 'lost' WHERE acquisition_id = %s",
         (acq["id"],), "append-only"),
        ("UPDATE acquisitions SET status = 'lost' WHERE id = %s", (acq["id"],),
         "acquisitions_lost_chk"),
    ]
    for testo, par, atteso in casi:
        with pytest.raises(psycopg2.Error, match=atteso):
            sql(testo, par)
        k["conn"].rollback()
    # referente non proprietario: il trigger lo rifiuta anche senza service
    sql("INSERT INTO contacts (agency_id, display_name) VALUES (%s, 'Terzo')", (k["a"],))
    terzo = sql("SELECT max(id) FROM contacts")[0][0]
    with pytest.raises(psycopg2.Error, match="not an owner"):
        sql("UPDATE acquisitions SET owner_contact_id = %s WHERE id = %s", (terzo, acq["id"]))
    k["conn"].rollback()
    # terminale: non si riapre
    sql("UPDATE acquisitions SET status = 'lost', lost_reason = 'other', lost_at = NOW() "
        "WHERE id = %s", (acq["id"],))
    with pytest.raises(psycopg2.Error, match="terminal"):
        sql("UPDATE acquisitions SET status = 'appointment_set', lost_reason = NULL, "
            "lost_at = NULL WHERE id = %s", (acq["id"],))
    k["conn"].rollback()


def test_30_riferimenti_restrict_e_compositi(k):
    """L'appuntamento, l'immobile e il referente di un'acquisizione non si
    cancellano sotto di lei; ogni FK verso una tabella con (agency_id, id) e'
    composita."""
    righe = k["sql"]("""
        SELECT conname, confdeltype, array_length(conkey, 1) AS colonne
          FROM pg_constraint
         WHERE conrelid = 'acquisitions'::regclass AND contype = 'f'""")
    fk = {r[0]: (r[1], r[2]) for r in righe}
    assert fk["acquisitions_appointment_same_agency_fk"] == ("r", 2)
    assert fk["acquisitions_owner_same_agency_fk"] == ("r", 2)
    assert fk["acquisitions_agent_same_agency_fk"][1] == 2
    assert fk["acquisitions_property_id_fkey"] == ("r", 1)
    assert fk["acquisitions_lead_id_fkey"] == ("n", 1)
    composita = k["sql"]("SELECT array_length(conkey, 1) FROM pg_constraint "
                         "WHERE conname = 'properties_acquisition_same_agency_fk'")[0][0]
    assert composita == 2


# ---------------------------------------------------------------------------
# POST-COMMIT - le cause reali emerse su TEST (RC-1, RC-3)
# ---------------------------------------------------------------------------

def test_31_senza_la_081_le_acquisizioni_chiudono_con_503_leggibile(k):
    """RC-1: su TEST il codice girava su un database SENZA la 081
    (`relation "acquisitions" does not exist` -> "Errore 500"). Ora ogni
    lettura/scrittura delle Acquisizioni risponde 503 con il suo `code`, le
    opzioni (senza tabella) restano 200, l'Agenda continua a funzionare e
    NIENTE viene scritto. La 081 si riapplica alla fine."""
    sql = k["sql"]
    _pulisci(sql)
    giu = (MIGRAZIONI / f"{VERSIONE}_down.sql").read_text(encoding="utf-8")
    su = (MIGRAZIONI / f"{VERSIONE}.sql").read_text(encoding="utf-8")
    api = k["api"]("giorgio")
    try:
        sql(giu)
        k["conn"].commit()
        assert sql("SELECT to_regclass('acquisitions')")[0][0] is None
        atteso = {"detail": "Il modulo Acquisizioni non è installato su questo database "
                            "(migration 081 non applicata).", "code": "ACQUISITIONS_NOT_INSTALLED"}
        r = api.get("/api/acquisitions?limit=50&offset=0")
        assert (r.status_code, r.json()) == (503, atteso), r.text
        assert api.get("/api/acquisitions/options").status_code == 200
        assert api.get("/api/acquisitions/1").status_code == 503
        r = api.post("/api/acquisitions", json=_corpo(k))
        assert (r.status_code, r.json()["code"]) == (503, "ACQUISITIONS_NOT_INSTALLED"), r.text
        assert _conta(k, "appointments") == 0          # la transazione e' tornata indietro
        # l'Agenda, con gli hook a vuoto, crea e completa come prima
        r = api.post("/api/appointments", json={**_appuntamento(k["luca"]), "end_at": futuro(11).isoformat(),
                                                "appointment_type": "seller_meeting",
                                                "property_id": k["casa"], "contact_id": k["mario"]})
        assert r.status_code == 201, r.text
    finally:
        sql(su)
        k["conn"].commit()
    assert sql("SELECT to_regclass('acquisitions')")[0][0] == "acquisitions"
    assert api.get("/api/acquisitions?limit=50&offset=0").status_code == 200


def test_32_il_ruolo_del_contatto_non_e_un_collegamento_all_immobile(k):
    """RC-3 (Tortoreto Alto Casa Fernando): il contatto ha il ruolo
    «Proprietario» in `contact_roles` e un appuntamento sull'immobile, ma
    nessuna riga in `property_contacts`. Scheda Immobile (GET dettaglio,
    `contacts`) e Acquisizioni (`property_owners`) leggono la STESSA fonte e
    danno lo stesso esito: nessun proprietario, niente scritto. Collegandolo
    dalla scheda immobile (endpoint reale, ruolo owner) l'acquisizione parte."""
    from acquisitions import repository
    from acquisitions.enums import OWNER_ROLES
    from core.database import core_cursor

    sql = k["sql"]
    sql(_estrai("001_core_contacts_leads.sql", "contact_roles"))
    sql("DELETE FROM contact_roles")
    fernando = sql("INSERT INTO contacts (agency_id, display_name) VALUES "
                   "(%s,'Fernando Micucci') RETURNING id", (k["a"],))[0][0]
    sql("INSERT INTO contact_roles (contact_id, role) VALUES (%s,'owner'), (%s,'seller')",
        (fernando, fernando))
    casa = sql("INSERT INTO properties (agency_id, title, city) VALUES "
               "(%s,'Tortoreto Alto Casa Fernando','Tortoreto') RETURNING id", (k["a"],))[0][0]
    api = k["api"]("giorgio")
    r = api.post("/api/appointments", json={**_appuntamento(k["luca"], h=14), "end_at": futuro(15).isoformat(),
                                            "appointment_type": "seller_meeting",
                                            "property_id": casa, "contact_id": fernando})
    assert r.status_code == 201, r.text
    # scheda Immobile e Acquisizioni: stessa fonte, stesso esito
    scheda = api.get(f"/api/property/properties/{casa}")
    assert scheda.status_code == 200 and scheda.json()["contacts"] == []
    with core_cursor() as (_, cur):
        assert repository.property_owners(cur, k["a"], casa, OWNER_ROLES) == []
    r = api.post("/api/acquisitions", json=_corpo(k, property_id=casa, owner_contact_id=fernando))
    assert (r.status_code, r.json()["code"]) == (422, "PROPERTY_WITHOUT_OWNER"), r.text
    assert _conta(k, "acquisitions") == 0
    # il collegamento vero: «Collega contatto» dalla scheda immobile
    r = api.post(f"/api/property/properties/{casa}/contacts",
                 json={"contact_id": fernando, "role": "owner", "is_primary": True})
    assert r.status_code == 201, r.text
    assert [c["contact_id"] for c in api.get(f"/api/property/properties/{casa}").json()["contacts"]] \
        == [fernando]
    with core_cursor() as (_, cur):
        proprietari = repository.property_owners(cur, k["a"], casa, OWNER_ROLES)
    assert [(p["contact_id"], p["roles"], p["is_primary"]) for p in proprietari] \
        == [(fernando, ["owner"], True)]
    acq = _crea(k, property_id=casa, owner_contact_id=fernando)
    assert (acq["property_id"], acq["owner_contact_id"]) == (casa, fernando)
    sql("DELETE FROM contact_roles")


# ---------------------------------------------------------------------------
# AUDIT ACQUISIZIONE -> INCARICO (post-commit): i buchi reali dei test sopra
# ---------------------------------------------------------------------------

def _pronta(k, chi="giorgio", stato="inspection_done", h=10, **kw):
    """Un'acquisizione al sopralluogo (o oltre, via /status), col dettaglio."""
    acq = _crea(k, **kw)
    _nel_passato(k, acq, h=h)
    det = _al_sopralluogo(k, acq, chi=chi)
    if stato != "inspection_done":
        r = k["api"](chi).post(f"/api/acquisitions/{acq['id']}/status",
                               json={"version": det["version"], "status": stato})
        assert r.status_code == 200, r.text
        det = r.json()
    return det


def _immobile(k, property_id=None):
    return dict(k["sql"]("SELECT * FROM properties WHERE id = %s",
                         (property_id or k["casa"],))[0])


def _altro_immobile(k, titolo):
    """Un immobile in piu' dell'agenzia A, con Mario proprietario."""
    pid = k["sql"]("INSERT INTO properties (agency_id, title, city, asking_price) VALUES "
                   "(%s, %s, 'Giulianova', 150000) RETURNING id", (k["a"], titolo))[0][0]
    k["sql"]("INSERT INTO property_contacts (property_id, contact_id, role, is_primary) "
             "VALUES (%s, %s, 'owner', TRUE)", (pid, k["mario"]))
    return pid


def test_33_incarico_da_valutazione_e_trattativa_mai_da_persa_o_appuntamento(k):
    """Matrice stati: SI da inspection_done (test 23), valuation_presented,
    mandate_negotiation; NO da appointment_set (test 22), lost, acquired."""
    for ora, stato in ((10, "valuation_presented"), (12, "mandate_negotiation")):
        pid = _altro_immobile(k, f"Immobile {stato}")
        det = _pronta(k, stato=stato, h=ora, property_id=pid, appointment=_appuntamento(k["luca"], h=ora))
        assert det["allowed_actions"]["mandate"] is True
        r = k["api"]("giorgio").post(f"/api/acquisitions/{det['id']}/mandate",
                                     json=dict(_incarico(), version=det["version"]))
        assert r.status_code == 200, (stato, r.text)
        d = r.json()
        assert d["status"] == "acquired" and d["allowed_actions"]["mandate"] is False
        # SENTINELLA AGGIORNATA DA DELETE-ARCH FASE 1A: + «mistake», spenta su una chiusa.
        assert d["allowed_actions"] == {"edit": False, "reassign": False, "transitions": [],
                                        "lost": False, "mistake": False, "new_appointment": False,
                                        "mandate": False}
        ev = d["events"][-1]
        assert (ev["event_type"], ev["from_status"], ev["to_status"]) == ("mandate_created", stato, "acquired")
        assert _immobile(k, pid)["acquisition_id"] == det["id"]
    # persa: niente incarico, e l'immobile resta com'era
    det = _pronta(k, h=14, appointment=_appuntamento(k["luca"], h=14))
    r = k["api"]("giorgio").post(f"/api/acquisitions/{det['id']}/lost",
                                 json={"version": det["version"], "lost_reason": "commission"})
    assert r.status_code == 200, r.text
    persa = r.json()
    assert persa["allowed_actions"]["mandate"] is False
    r = k["api"]("giorgio").post(f"/api/acquisitions/{det['id']}/mandate",
                                 json=dict(_incarico(), version=persa["version"]))
    assert r.status_code == 409 and r.json()["code"] == "MANDATE_NOT_ALLOWED", r.text
    p = _immobile(k)
    assert (p["acquisition_id"], p["mandate_type"], p["commercial_status"]) == (None, None, "draft")
    assert _eventi(k, det["id"])[-1] == "lost"


def test_34_incarico_tenant_versione_e_dati(k):
    det = _pronta(k)
    url = f"/api/acquisitions/{det['id']}/mandate"
    # altra agenzia: 404 e nulla scritto
    r = k["api"]("estraneo").post(url, json=dict(_incarico(), version=det["version"]))
    assert r.status_code == 404, r.text
    # agente della stessa agenzia che NON e' l'assegnatario: non la vede (404)
    r = k["api"]("marta").post(url, json=dict(_incarico(), version=det["version"]))
    assert r.status_code == 404, r.text
    # versione vecchia: 409 con la versione corrente
    r = k["api"]("giorgio").post(url, json=dict(_incarico(), version=det["version"] - 1))
    assert r.status_code == 409 and r.json()["code"] == "VERSION_CONFLICT"
    assert r.json()["current_version"] == det["version"]
    # dati: tipo obbligatorio, inizio obbligatorio, fine >= inizio, agency_id vietato
    for corpo in (dict(_incarico(mandate_type="  "), version=det["version"]),
                  dict(_incarico(), version=det["version"], mandate_type=None),
                  {k2: v2 for k2, v2 in dict(_incarico(), version=det["version"]).items()
                   if k2 != "mandate_start"},
                  dict(_incarico(mandate_end=(date.today() - timedelta(days=1)).isoformat()),
                       version=det["version"]),
                  dict(_incarico(mandate_start="31/12/2026"), version=det["version"]),
                  dict(_incarico(agreed_price="-1"), version=det["version"]),
                  dict(_incarico(), version=det["version"], agency_id=k["a"])):
        r = k["api"]("giorgio").post(url, json=corpo)
        assert r.status_code == 422 and r.json()["code"] == "VALIDATION_ERROR", (corpo, r.text)
    p = _immobile(k)
    assert p["acquisition_id"] is None and p["commercial_status"] == "draft"
    assert _riga(k, det["id"])["status"] == "inspection_done"
    assert _eventi(k, det["id"]) == ["created", "appointment_completed"]
    # senza scadenza e senza prezzo concordato: ammesso (il contratto li lascia facoltativi)
    r = k["api"]("giorgio").post(url, json={"version": det["version"], "mandate_type": "Esclusiva",
                                            "mandate_start": date.today().isoformat()})
    assert r.status_code == 200, r.text
    p = _immobile(k)
    assert p["mandate_end"] is None and p["asking_price"] == Decimal("180000")
    assert _conta(k, "property_price_history", "property_id = %s", (k["casa"],)) == 0


def test_35_rollback_in_ogni_punto_della_transazione(k, monkeypatch):
    """Failure injection: dopo il lock dell'acquisizione, dopo l'UPDATE
    dell'immobile, prima dell'UPDATE dell'acquisizione, prima dell'evento,
    violazione di vincolo. Sempre: nulla resta."""
    from acquisitions import repository, service

    det = _pronta(k)
    prima_p = _immobile(k)
    prima_a = _riga(k, det["id"])
    eventi = _eventi(k, det["id"])
    storico = _conta(k, "property_status_history")

    def verifica_intatto(etichetta):
        dopo_p = _immobile(k)
        dopo_a = _riga(k, det["id"])
        assert dopo_p == prima_p, (etichetta, "immobile cambiato")
        assert dopo_a == prima_a, (etichetta, "acquisizione cambiata")
        assert _eventi(k, det["id"]) == eventi, etichetta
        assert _conta(k, "property_status_history") == storico, etichetta
        assert _conta(k, "property_price_history") == 0, etichetta

    def rotto(*a, **kw):
        raise RuntimeError("guasto simulato")

    casi = {
        "dopo lock acquisizione": ("_blocca", service, lambda orig: (lambda *a, **kw: (orig(*a, **kw), rotto())[0])),
        "dopo update immobile": ("write_mandate", repository, lambda orig: (lambda *a, **kw: (orig(*a, **kw), rotto())[0])),
        "prima update acquisizione": ("update_acquisition", repository, lambda orig: rotto),
        "prima insert evento": ("record_event", repository, lambda orig: rotto),
    }
    for etichetta, (nome, modulo, fabbrica) in casi.items():
        originale = getattr(modulo, nome)
        monkeypatch.setattr(modulo, nome, fabbrica(originale))
        with pytest.raises(RuntimeError):
            k["api"]("giorgio").post(f"/api/acquisitions/{det['id']}/mandate",
                                     json=dict(_incarico(), version=det["version"]))
        monkeypatch.setattr(modulo, nome, originale)
        verifica_intatto(etichetta)
    # violazione di vincolo: l'evento con un tipo fuori catalogo
    originale = repository.record_event

    def tipo_sbagliato(cur, **kw):
        kw["event_type"] = "non_in_catalogo"
        return originale(cur, **kw)

    import psycopg2
    monkeypatch.setattr(repository, "record_event", tipo_sbagliato)
    with pytest.raises(psycopg2.errors.CheckViolation):
        k["api"]("giorgio").post(f"/api/acquisitions/{det['id']}/mandate",
                                 json=dict(_incarico(), version=det["version"]))
    monkeypatch.setattr(repository, "record_event", originale)
    verifica_intatto("violazione di vincolo")
    # e dopo i guasti, l'operazione vera riesce
    r = k["api"]("giorgio").post(f"/api/acquisitions/{det['id']}/mandate",
                                 json=dict(_incarico(), version=det["version"]))
    assert r.status_code == 200, r.text


def test_36_doppio_invio_e_richieste_simultanee(k):
    """Due POST /mandate con la stessa versione, davvero in parallelo (due
    connessioni, lock FOR UPDATE): uno solo riesce, l'altro 409; un solo
    incarico, un solo evento, una sola riga di storico."""
    import threading

    det = _pronta(k)
    esiti = [None, None]
    via = threading.Barrier(2)

    def invia(i):
        via.wait()
        esiti[i] = k["api"]("giorgio").post(f"/api/acquisitions/{det['id']}/mandate",
                                            json=dict(_incarico(), version=det["version"]))

    fili = [threading.Thread(target=invia, args=(i,)) for i in range(2)]
    for f in fili:
        f.start()
    for f in fili:
        f.join(timeout=30)
    codici = sorted(r.status_code for r in esiti)
    assert codici == [200, 409], [r.text for r in esiti]
    perdente = next(r for r in esiti if r.status_code == 409).json()
    # il lock FOR UPDATE serializza: il secondo rilegge la riga gia' acquired
    # (version +1) e si ferma al controllo di versione, prima di qualsiasi scrittura
    assert perdente["code"] == "VERSION_CONFLICT", perdente
    p = _immobile(k)
    assert p["acquisition_id"] == det["id"] and p["commercial_status"] == "mandate"
    assert _riga(k, det["id"])["status"] == "acquired"
    assert _eventi(k, det["id"]).count("mandate_created") == 1
    assert _conta(k, "property_status_history", "property_id = %s AND new_value = 'mandate'",
                  (k["casa"],)) == 1
    assert _conta(k, "property_price_history", "property_id = %s", (k["casa"],)) == 1
    # il doppio click "sequenziale" (stessa versione, dopo il successo): 409
    r = k["api"]("giorgio").post(f"/api/acquisitions/{det['id']}/mandate",
                                 json=dict(_incarico(), version=det["version"]))
    assert r.status_code == 409


def test_37_agente_proprietari_e_audit_dopo_l_incarico(k):
    """Agente: l'immobile che ne ha gia' uno lo tiene (la riassegnazione
    resta CRM-OPS-2); quello senza prende l'agente dell'acquisizione (test
    23). Proprietari: invariati. Audit: evento ed eventi con attore, agenzia,
    stati e cambi; storico immobile con nota e autore."""
    sql = k["sql"]
    sql("UPDATE properties SET assigned_agent_id = %s, assigned_to = 'Marta' WHERE id = %s",
        (k["marta"], k["casa"]))
    proprietari_prima = sql("SELECT contact_id, role, is_primary FROM property_contacts "
                            "WHERE property_id = %s ORDER BY id", (k["casa"],))
    det = _pronta(k, chi="luca")                    # assegnata a luca
    assert det["assigned_agent_id"] == k["luca"]
    r = k["api"]("luca").post(f"/api/acquisitions/{det['id']}/mandate",
                              json=dict(_incarico(), version=det["version"]))
    assert r.status_code == 200, r.text
    d = r.json()
    p = _immobile(k)
    # agente: coerenza NON forzata; divergenza dichiarata dal contratto
    assert p["assigned_agent_id"] == k["marta"] and p["assigned_to"] == "Marta"
    assert d["assigned_agent_id"] == k["luca"]
    # proprietari: stessi, nessuna riga in piu'
    assert sql("SELECT contact_id, role, is_primary FROM property_contacts "
               "WHERE property_id = %s ORDER BY id", (k["casa"],)) == proprietari_prima
    assert [o["contact_id"] for o in d["owners"]] == [k["mario"], k["bruno"]]
    # evento
    ev = dict(sql("SELECT * FROM acquisition_events WHERE acquisition_id = %s "
                  "ORDER BY id DESC LIMIT 1", (det["id"],))[0])
    assert ev["agency_id"] == k["a"] and ev["actor_user_id"] == k["luca"]
    assert (ev["event_type"], ev["from_status"], ev["to_status"]) == ("mandate_created", "inspection_done", "acquired")
    assert ev["occurred_at"] is not None
    cambi = ev["changes"]
    assert cambi["property_id"] == k["casa"] and cambi["mandate_type"] == "Esclusiva"
    assert cambi["mandate_start"] == date.today().isoformat()
    assert cambi["previous_commercial_status"] == "draft" and cambi["agreed_price"] == "179000.00"
    assert d["events"][-1]["actor_name"]                   # risolto per la UI
    assert d["acquired_at"] and _riga(k, det["id"])["acquired_at"] is not None
    # storico immobile
    st = dict(sql("SELECT * FROM property_status_history WHERE property_id = %s "
                  "ORDER BY id DESC LIMIT 1", (k["casa"],))[0])
    assert (st["field_name"], st["old_value"], st["new_value"]) == ("commercial_status", "draft", "mandate")
    assert st["note"] == f"incarico generato dall'acquisizione {det['id']}"
    assert st["changed_by"] == f"operator:{k['luca']}"
    pr = dict(sql("SELECT * FROM property_price_history WHERE property_id = %s", (k["casa"],))[0])
    assert (pr["old_price"], pr["new_price"]) == (Decimal("180000"), Decimal("179000"))
    assert pr["changed_by"] == f"operator:{k['luca']}"
    # l'evento non si tocca
    import psycopg2
    with pytest.raises(psycopg2.Error):
        sql("UPDATE acquisition_events SET to_status = 'lost' WHERE id = %s", (ev["id"],))
    k["conn"].rollback()


def test_38_immobile_gia_in_stato_mandato_storico(k):
    """Un immobile storico gia' in `mandate` senza acquisizione: l'incarico
    si genera (origine nuova), lo stato non cambia e lo storico non si
    duplica; la seconda acquisizione sullo stesso immobile e' poi rifiutata."""
    sql = k["sql"]
    sql("ALTER TABLE properties DISABLE TRIGGER trg_properties_mandate_origin")
    sql("UPDATE properties SET commercial_status = 'mandate', mandate_type = 'Storico' WHERE id = %s",
        (k["casa"],))
    sql("ALTER TABLE properties ENABLE TRIGGER trg_properties_mandate_origin")
    det = _pronta(k)
    assert det["allowed_actions"]["mandate"] is True
    r = k["api"]("giorgio").post(f"/api/acquisitions/{det['id']}/mandate",
                                 json=dict(_incarico(), version=det["version"]))
    assert r.status_code == 200, r.text
    p = _immobile(k)
    assert p["acquisition_id"] == det["id"] and p["mandate_type"] == "Esclusiva"
    assert p["commercial_status"] == "mandate"
    assert _conta(k, "property_status_history", "property_id = %s", (k["casa"],)) == 0
    assert r.json()["events"][-1]["changes"]["previous_commercial_status"] == "mandate"
    r = k["api"]("giorgio").post("/api/acquisitions", json=_corpo(
        k, appointment=_appuntamento(k["marta"], h=17)))
    assert r.status_code == 409 and r.json()["code"] == "MANDATE_ALREADY_EXISTS"
