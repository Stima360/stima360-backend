"""LMC-8 su PostgreSQL reale: il radar del proprietario dentro il Contact 360.

Cio' che solo un database sa dire: che le case elencate nella scheda siano
quelle a cui QUEL contatto ha davvero accesso in QUELLA agenzia, che un
contatto omonimo in un'altra agenzia non ne porti dentro nemmeno una, e che
il livello mostrato sia quello che LMC-7 calcola sugli eventi veri - non una
copia che puo' divergere.

Opt-in: senza `P29_TEST_DSN` si salta tutto. Database usa-e-getta.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

DSN = os.getenv("P29_TEST_DSN")

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare LMC-8")

ROOT = Path(__file__).resolve().parents[1]
MIGRAZIONI = ROOT / "migrations"

SCHEMA_MINIMO = """
CREATE TABLE agencies (
    id BIGSERIAL PRIMARY KEY, slug VARCHAR(80) NOT NULL UNIQUE,
    status VARCHAR(20) NOT NULL DEFAULT 'active');
CREATE TABLE contacts (
    id BIGSERIAL PRIMARY KEY,
    agency_id BIGINT NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,
    display_name VARCHAR(200), email VARCHAR(320), email_normalized VARCHAR(320),
    status VARCHAR(20) NOT NULL DEFAULT 'active');
CREATE TABLE buy_requests (id SERIAL PRIMARY KEY, agency_id BIGINT);
CREATE TABLE matches (id SERIAL PRIMARY KEY, buy_request_id INTEGER);
CREATE TABLE stime (
    id SERIAL PRIMARY KEY,
    agency_id BIGINT NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,
    comune VARCHAR(100), microzona VARCHAR(100), via VARCHAR(100), civico VARCHAR(20),
    tipologia VARCHAR(50), mq INTEGER, piano VARCHAR(30), locali INTEGER, bagni INTEGER,
    pertinenze VARCHAR(200), ascensore VARCHAR(10), anno INTEGER, stato VARCHAR(40),
    posizionemare VARCHAR(50), distanzamare VARCHAR(50), barrieramare VARCHAR(50),
    vistamareyn VARCHAR(10), vistamaredettaglio VARCHAR(50), vistamare VARCHAR(50),
    mqgiardino INTEGER, mqgarage INTEGER, mqcantina INTEGER, mqpostoauto INTEGER,
    mqtaverna INTEGER, mqsoffitta INTEGER, mqterrazzo INTEGER, numbalconi INTEGER,
    altrodescrizione TEXT, nome VARCHAR(50), cognome VARCHAR(50), email VARCHAR(100),
    telefono VARCHAR(30), prezzo_mq_base NUMERIC(10,2), lead_status VARCHAR(32),
    note_internal TEXT, data TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE leads (
    id BIGSERIAL PRIMARY KEY,
    contact_id BIGINT NOT NULL REFERENCES contacts(id) ON DELETE RESTRICT,
    agency_id BIGINT NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,
    pipeline VARCHAR(20) NOT NULL DEFAULT 'general',
    stage VARCHAR(30) NOT NULL DEFAULT 'new',
    status VARCHAR(20) NOT NULL DEFAULT 'open',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT leads_pipeline_chk CHECK (pipeline IN ('sell', 'buy', 'general')));
CREATE TABLE lead_stime (
    id BIGSERIAL PRIMARY KEY,
    lead_id BIGINT NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    stima_id INTEGER NOT NULL REFERENCES stime(id) ON DELETE CASCADE,
    relation_type VARCHAR(20) NOT NULL DEFAULT 'related',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT lead_stime_relation_chk CHECK (relation_type IN ('origin','related','follow_up')),
    CONSTRAINT lead_stime_unq UNIQUE (lead_id, stima_id));
CREATE TABLE properties (
    id BIGSERIAL PRIMARY KEY,
    agency_id BIGINT NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,
    title VARCHAR(200), address VARCHAR(300), city VARCHAR(120));
CREATE TABLE activities (id BIGSERIAL PRIMARY KEY);
CREATE TABLE seller_timeline_events (
    id BIGSERIAL PRIMARY KEY, agency_id BIGINT, contact_id BIGINT, lead_id BIGINT,
    stima_id INTEGER REFERENCES stime(id) ON DELETE SET NULL, property_id BIGINT,
    event_type VARCHAR(50) NOT NULL, event_source VARCHAR(30),
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    idempotency_key VARCHAR(255), created_by VARCHAR(200),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
CREATE TABLE schema_migrations (version VARCHAR(200) PRIMARY KEY);
"""

# LMC-10 (collisione autorizzata): la catena cresce di una voce.
# Il read-model di "La Mia Casa" legge ora anche `owner_home_overrides`
# per comporre il PROFILO EFFETTIVO, quindi senza la 068 questo
# database usa-e-getta non ha piu' la forma che il codice si aspetta e
# ogni test qui fallirebbe per una tabella mancante invece che per il
# proprio motivo. La tabella resta vuota in tutti i test di questo
# file: nessuna correzione del proprietario esiste, quindi il profilo
# effettivo coincide con l'originale e cio' che si verificava prima si
# verifica identico.
CATENA = ("009_owner_01", "017_seller_intelligence_01", "022_property_watch",
          "066_lmc1_owner_stima_access", "068_lmc10_owner_home_overrides")


def _dsn_per(nome: str) -> str:
    if "?" in DSN:
        base, query = DSN.split("?", 1)
        return base.rsplit("/", 1)[0] + "/" + nome + "?" + query
    return DSN.rsplit("/", 1)[0] + "/" + nome


@pytest.fixture(scope="module")
def db():
    psycopg2 = pytest.importorskip("psycopg2")

    nome = f"lmc8_probe_{os.getpid()}_{uuid.uuid4().hex[:6]}"
    servizio = psycopg2.connect(DSN)
    servizio.autocommit = True
    with servizio.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{nome}"')

    dsn = _dsn_per(nome)
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_MINIMO)
            for versione in CATENA:
                cur.execute((MIGRAZIONI / f"{versione}.sql").read_text(encoding="utf-8"))
            cur.execute("ALTER TABLE property_watches ADD COLUMN IF NOT EXISTS agency_id BIGINT")
        conn.autocommit = False
        yield {"conn": conn, "dsn": dsn}
    finally:
        conn.close()
        with servizio.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{nome}"')
        servizio.close()


@pytest.fixture
def modulo(db, monkeypatch):
    import psycopg2

    from core import database as core_database
    from owner import crm_radar, home_service, interest_service, tracking
    from owner import repository as owner_repository
    from property_watch import database as pw_database
    from seller_intelligence import database as si_database
    from property_watch import service as pw_service

    monkeypatch.setattr(core_database, "get_connection", lambda: psycopg2.connect(db["dsn"]))
    monkeypatch.setattr(pw_database, "get_connection", lambda: psycopg2.connect(db["dsn"]))
    # LMC-7: gli eventi passano da Seller Intelligence, che apre la propria
    # connessione. Senza questa riga il radar scriverebbe altrove.
    monkeypatch.setattr(si_database, "get_connection", lambda: psycopg2.connect(db["dsn"]))
    return {"pw": pw_service, "home_service": home_service,
            "owner_repository": owner_repository, "tracking": tracking,
            "interest": interest_service, "crm_radar": crm_radar}


class Scope:
    """Il minimo che le funzioni scopate di PROPERTY WATCH chiedono."""

    def __init__(self, agency_id):
        self._a = agency_id

    def require_agency(self):
        return self._a


@pytest.fixture
def mondo(db):
    conn = db["conn"]
    conn.rollback()
    with conn.cursor() as cur:
        for tabella in ("owner_access_tokens", "owner_sessions", "owner_audit_log",
                        "owner_stima_access", "owner_property_access", "owner_accounts",
                        "property_watch_observations", "property_watches",
                        "seller_timeline_events", "lead_stime", "leads",
                        "stime", "contacts", "properties", "agencies"):
            cur.execute(f"DELETE FROM {tabella}")
        cur.execute("INSERT INTO agencies (slug) VALUES ('a-uno') RETURNING id"); a = cur.fetchone()[0]
        cur.execute("INSERT INTO agencies (slug) VALUES ('b-due') RETURNING id"); b = cur.fetchone()[0]

        def stima(agency, comune="Alba Adriatica", microzona="Villa Fiore", **extra):
            # "Villa Fiore" e "Lido Centro" sono microzone che il motore
            # conosce davvero (valuation.BASE_MQ): con una inventata il prezzo
            # base sarebbe 0 e il test proverebbe il caso sbagliato.
            col = {"comune": comune, "microzona": microzona, "via": "Via Trieste",
                   "civico": "12", "tipologia": "Appartamento", "mq": 95, "piano": "3",
                   "locali": 4, "bagni": 2, "pertinenze": "garage", "ascensore": "True",
                   "anno": 1998, "stato": "buono", "posizionemare": "fronte",
                   "distanzamare": "0-100", "barrieramare": "no", "vistamareyn": "si",
                   "vistamaredettaglio": "frontale", "vistamare": "mare",
                   "mqgiardino": 0, "mqgarage": 18, "mqcantina": 6, "mqpostoauto": 0,
                   "mqtaverna": 0, "mqsoffitta": 0, "mqterrazzo": 12, "numbalconi": 2,
                   "altrodescrizione": "ristrutturato", "nome": "Mario", "cognome": "Rossi",
                   "email": "mario@example.it", "telefono": "+39 333 1234567",
                   "prezzo_mq_base": 1500, "lead_status": "nuovo", "note_internal": "richiamare"}
            col.update(extra)
            cur.execute(f"INSERT INTO stime (agency_id,{','.join(col)}) VALUES "
                        f"({','.join(['%s'] * (len(col) + 1))}) RETURNING id",
                        [agency] + list(col.values()))
            return cur.fetchone()[0]

        st_a = stima(a)
        st_b = stima(b, comune="Tortoreto", microzona="Lido Centro")
        st_senza_watch = stima(a, comune="Martinsicuro", microzona="Centro")
        cur.execute("INSERT INTO property_watches (stima_id,status,agency_id) VALUES (%s,'active',%s) RETURNING id",
                    (st_a, a)); w_a = cur.fetchone()[0]
        cur.execute("INSERT INTO property_watch_observations (watch_id,observation_type,source,payload,"
                    "idempotency_key,observed_at) VALUES (%s,'watch_started','internal',%s,%s, NOW() - INTERVAL '400 days')",
                    (w_a, json.dumps({"price_exact": 185000, "eur_mq_finale": 1947}), f"base-{st_a}"))
        cur.execute("INSERT INTO property_watches (stima_id,status,agency_id) VALUES (%s,'active',%s) RETURNING id",
                    (st_b, b)); w_b = cur.fetchone()[0]

        # Un owner che guarda la casa di A, per il read-model.
        cur.execute("INSERT INTO contacts (agency_id, display_name, email, email_normalized) "
                    "VALUES (%s,'Mario Rossi','mario@example.it','mario@example.it') RETURNING id", (a,))
        k = cur.fetchone()[0]
        cur.execute("INSERT INTO owner_accounts (contact_id,status) VALUES (%s,'active') RETURNING id", (k,))
        acc = cur.fetchone()[0]
        cur.execute("INSERT INTO owner_stima_access (owner_account_id,stima_id,granted_by) "
                    "VALUES (%s,%s,'LMC_PROVISIONING')", (acc, st_a))

        # Il lead SELL che il funnel crea insieme alla stima, legato come
        # 'origin'. E' quello che il radar deve trovare.
        cur.execute("INSERT INTO leads (contact_id,agency_id,pipeline) "
                    "VALUES (%s,%s,'sell') RETURNING id", (k, a))
        lead_sell = cur.fetchone()[0]
        cur.execute("INSERT INTO lead_stime (lead_id,stima_id,relation_type) "
                    "VALUES (%s,%s,'origin')", (lead_sell, st_a))
    conn.commit()
    return {"conn": conn, "a": a, "b": b, "st_a": st_a, "st_b": st_b,
            "st_senza_watch": st_senza_watch, "w_a": w_a, "w_b": w_b,
            "acc": acc, "contact": k, "lead_sell": lead_sell}


def righe(mondo, sql, params=()):
    with mondo["conn"].cursor() as cur:
        cur.execute(sql, params)
        valori = cur.fetchall()
    mondo["conn"].rollback()
    return valori


def conta(mondo, sql, params=()):
    return righe(mondo, sql, params)[0][0]


class ScopeOperatore:
    """Il contesto di un operatore: l'unica cosa da cui arriva il tenant."""

    def __init__(self, agency_id):
        self._a = agency_id

    def require_agency(self):
        return self._a


def scrivi_snapshot(mondo, watch_id, prezzo=200000, chiave="k-vs"):
    with mondo["conn"].cursor() as cur:
        cur.execute("INSERT INTO property_watch_observations (watch_id,observation_type,"
                    "source,payload,idempotency_key) VALUES (%s,'valuation_snapshot',"
                    "'internal',%s,%s)",
                    (watch_id, json.dumps({"price_exact": prezzo, "eur_mq_finale": 2100,
                                           "computed_at": "2026-09-10T08:00:00+00:00",
                                           "algorithm_fingerprint": "v1",
                                           "input_digest": "a" * 64}), chiave))
    mondo["conn"].commit()


def scrivi_pressure(mondo, watch_id, chiave="k-bp"):
    metriche = {"evaluated_buyers": 24, "compatible_buyers": 9,
                "highly_compatible_buyers": 4, "recent_compatible_buyers_30d": 5,
                "average_match_score": 74.5, "maximum_match_score": 91.0,
                "average_budget": 238000.0, "algorithm_version": "match-1.0"}
    with mondo["conn"].cursor() as cur:
        cur.execute("INSERT INTO property_watch_observations (watch_id,observation_type,"
                    "source,payload,idempotency_key) VALUES (%s,'buyer_pressure_snapshot',"
                    "'internal',%s,%s)", (watch_id, json.dumps(metriche), chiave))
    mondo["conn"].commit()


def blocco(modulo, mondo, agency=None, contact=None):
    return modulo["crm_radar"].contact_homes_block(
        ScopeOperatore(agency or mondo["a"]), contact or mondo["contact"])


# ---------------------------------------------------------------------------
# 1-4: quali case entrano nella scheda
# ---------------------------------------------------------------------------

def test_1_il_contatto_con_una_casa(mondo, modulo):
    vista = blocco(modulo, mondo)
    assert vista["available"] is True
    assert [h["stima_id"] for h in vista["homes"]] == [mondo["st_a"]]
    home = vista["homes"][0]
    assert "Via Trieste" in home["address"]
    assert home["tipologia"] == "Appartamento" and home["mq"] == 95


def test_2_un_contatto_senza_grant_non_ha_case(mondo, modulo):
    with mondo["conn"].cursor() as cur:
        cur.execute("INSERT INTO contacts (agency_id, display_name) VALUES (%s,'Senza') "
                    "RETURNING id", (mondo["a"],))
        estraneo = cur.fetchone()[0]
    mondo["conn"].commit()
    assert blocco(modulo, mondo, contact=estraneo) == {"available": False, "homes": []}


def test_3_piu_case_dello_stesso_contatto(mondo, modulo):
    """Chi ha chiesto una stima per la casa al mare e una per quella in
    citta' ha due storie: il radar e' separato per ciascuna."""
    with mondo["conn"].cursor() as cur:
        cur.execute("INSERT INTO owner_stima_access (owner_account_id,stima_id,granted_by) "
                    "VALUES (%s,%s,'P')", (mondo["acc"], mondo["st_senza_watch"]))
    mondo["conn"].commit()
    vista = blocco(modulo, mondo)
    assert len(vista["homes"]) == 2
    assert {h["stima_id"] for h in vista["homes"]} == {mondo["st_a"], mondo["st_senza_watch"]}
    livelli = {h["stima_id"]: h["interest"]["level"] for h in vista["homes"]}
    assert set(livelli.values()) == {"none"}, "nessuna attivita' ancora"


def test_4_un_grant_revocato_toglie_la_casa_dalla_scheda(mondo, modulo):
    with mondo["conn"].cursor() as cur:
        cur.execute("UPDATE owner_stima_access SET access_status='revoked', revoked_at=NOW()")
    mondo["conn"].commit()
    assert blocco(modulo, mondo)["available"] is False


# ---------------------------------------------------------------------------
# 5-7: tenancy
# ---------------------------------------------------------------------------

def test_5_l_agenzia_sbagliata_non_vede_la_casa(mondo, modulo):
    assert blocco(modulo, mondo, agency=mondo["b"])["available"] is False


def test_6_un_omonimo_in_un_altra_agenzia_non_porta_dentro_niente(mondo, modulo):
    """Stessa persona, due agenzie, due contatti distinti: il CRM di B non
    deve vedere la casa che quella persona ha in A."""
    with mondo["conn"].cursor() as cur:
        cur.execute("INSERT INTO contacts (agency_id, display_name, email, email_normalized) "
                    "VALUES (%s,'Mario Rossi','mario@example.it','mario@example.it') "
                    "RETURNING id", (mondo["b"],))
        gemello = cur.fetchone()[0]
        cur.execute("INSERT INTO owner_accounts (contact_id,status) VALUES (%s,'active') "
                    "RETURNING id", (gemello,))
        acc_b = cur.fetchone()[0]
        cur.execute("INSERT INTO owner_stima_access (owner_account_id,stima_id,granted_by) "
                    "VALUES (%s,%s,'P')", (acc_b, mondo["st_b"]))
    mondo["conn"].commit()

    in_a = blocco(modulo, mondo)
    in_b = blocco(modulo, mondo, agency=mondo["b"], contact=gemello)
    assert [h["stima_id"] for h in in_a["homes"]] == [mondo["st_a"]]
    assert [h["stima_id"] for h in in_b["homes"]] == [mondo["st_b"]]


def test_7_il_tenant_arriva_solo_dal_contesto(mondo, modulo):
    import inspect
    firma = inspect.signature(modulo["crm_radar"].contact_homes_block)
    assert list(firma.parameters) == ["ctx", "contact_id"]
    sorgente = inspect.getsource(modulo["crm_radar"].contact_homes_block)
    assert "ctx.require_agency()" in sorgente


# ---------------------------------------------------------------------------
# 8-10: i valori e il radar, sugli eventi veri
# ---------------------------------------------------------------------------

def test_8_i_valori_sono_quelli_che_vede_il_proprietario(mondo, modulo):
    scrivi_snapshot(mondo, mondo["w_a"], prezzo=212000)
    dal_crm = blocco(modulo, mondo)["homes"][0]
    dal_portale = modulo["home_service"].get_home(mondo["acc"], mondo["st_a"])["valuation"]
    assert dal_crm["initial_value"] == dal_portale["initial_value"] == 185000
    assert dal_crm["current_value"] == dal_portale["current_value"] == 212000
    assert dal_crm["current_value_computed_at"] == dal_portale["current_value_computed_at"]


def test_9_il_livello_e_quello_calcolato_da_lmc7(mondo, modulo):
    scrivi_snapshot(mondo, mondo["w_a"])
    adesso = datetime.now(timezone.utc)
    modulo["tracking"].track_home_viewed(mondo["acc"], mondo["st_a"],
                                         when=adesso - timedelta(days=3))
    modulo["tracking"].track_home_viewed(mondo["acc"], mondo["st_a"], when=adesso)
    modulo["tracking"].track_action(mondo["acc"], mondo["st_a"], "value_history_viewed",
                                    when=adesso)

    dal_crm = blocco(modulo, mondo)["homes"][0]["interest"]
    da_lmc7 = modulo["interest"].interest_for_stima_scoped(
        ScopeOperatore(mondo["a"]), mondo["st_a"])["interest"]
    assert dal_crm == da_lmc7, "il CRM non ricalcola: passa l'oggetto"
    assert dal_crm["level"] == "high"
    assert dal_crm["active_days_7d"] == 2
    assert dal_crm["value_history_viewed"] is True
    assert dal_crm["reasons"]


def test_10_ordine_per_ultima_attivita(mondo, modulo):
    with mondo["conn"].cursor() as cur:
        cur.execute("INSERT INTO owner_stima_access (owner_account_id,stima_id,granted_by) "
                    "VALUES (%s,%s,'P')", (mondo["acc"], mondo["st_senza_watch"]))
    mondo["conn"].commit()
    # Solo la seconda casa ha attivita': deve passare davanti.
    modulo["tracking"].track_home_viewed(mondo["acc"], mondo["st_senza_watch"])
    ordine = [h["stima_id"] for h in blocco(modulo, mondo)["homes"]]
    assert ordine[0] == mondo["st_senza_watch"], ordine


# ---------------------------------------------------------------------------
# 11-12: privacy, sul JSON che il CRM riceve davvero
# ---------------------------------------------------------------------------

def test_11_la_rilevazione_buyer_pressure_non_arriva_nel_crm(mondo, modulo):
    scrivi_pressure(mondo, mondo["w_a"])
    scrivi_snapshot(mondo, mondo["w_a"])
    modulo["tracking"].track_action(mondo["acc"], mondo["st_a"], "buyer_demand_viewed")

    vista = blocco(modulo, mondo)
    radar = vista["homes"][0]["interest"]
    assert radar["buyer_demand_viewed"] is True, "il CRM sa CHE l'ha aperta"

    testo = json.dumps(vista, default=str).lower()
    for vietato in ("238000", "74.5", "91.0", "score", "budget", "pressure",
                    "evaluated", "match-1.0", "compatible_buyers",
                    "mario", "@example.it", "idempotency"):
        assert vietato not in testo, vietato


def test_12_nessun_punteggio_numerico(mondo, modulo):
    scrivi_snapshot(mondo, mondo["w_a"])
    modulo["tracking"].track_home_viewed(mondo["acc"], mondo["st_a"])
    vista = blocco(modulo, mondo)
    assert "score" not in json.dumps(vista, default=str).lower()
    radar = vista["homes"][0]["interest"]
    assert set(radar) == {"level", "active_days_7d", "active_days_30d",
                          "last_activity_at", "home_viewed_days_30d",
                          "value_history_viewed", "buyer_demand_viewed",
                          "home_updated", "consultation_requested", "reasons"}


# ---------------------------------------------------------------------------
# 13: leggere la scheda non scrive niente
# ---------------------------------------------------------------------------

def test_13_il_crm_non_crea_task_attivita_ne_eventi(mondo, modulo):
    scrivi_snapshot(mondo, mondo["w_a"])
    modulo["tracking"].track_home_viewed(mondo["acc"], mondo["st_a"])
    prima = conta(mondo, "SELECT COUNT(*) FROM seller_timeline_events")
    for _ in range(3):
        blocco(modulo, mondo)
    assert conta(mondo, "SELECT COUNT(*) FROM seller_timeline_events") == prima
    assert conta(mondo, "SELECT COUNT(*) FROM activities") == 0
