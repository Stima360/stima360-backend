"""LMC-3 su PostgreSQL reale: scritture, idempotenza, tenancy, storico.

Cio' che solo un database sa dire: che un secondo refresh con gli stessi dati
non scriva una seconda riga, che dati diversi o algoritmo diverso ne scrivano
una nuova SENZA toccare le precedenti, che l'agenzia A non possa fare uno
snapshot della stima di B, e che il read-model del portale legga davvero gli
snapshot appena scritti.

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
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare LMC-3")

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
CREATE TABLE leads (id BIGSERIAL PRIMARY KEY, agency_id BIGINT);
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
CATENA = ("009_owner_01", "022_property_watch", "066_lmc1_owner_stima_access",
          "068_lmc10_owner_home_overrides")


def _dsn_per(nome: str) -> str:
    if "?" in DSN:
        base, query = DSN.split("?", 1)
        return base.rsplit("/", 1)[0] + "/" + nome + "?" + query
    return DSN.rsplit("/", 1)[0] + "/" + nome


@pytest.fixture(scope="module")
def db():
    psycopg2 = pytest.importorskip("psycopg2")

    nome = f"lmc3_probe_{os.getpid()}_{uuid.uuid4().hex[:6]}"
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
    from owner import home_service
    from property_watch import database as pw_database
    from property_watch import service as pw_service
    from property_watch import valuation_snapshot as vs

    monkeypatch.setattr(core_database, "get_connection", lambda: psycopg2.connect(db["dsn"]))
    monkeypatch.setattr(pw_database, "get_connection", lambda: psycopg2.connect(db["dsn"]))
    return {"pw": pw_service, "vs": vs, "home_service": home_service}


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
                        "seller_timeline_events", "stime", "contacts", "properties", "agencies"):
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
    conn.commit()
    return {"conn": conn, "a": a, "b": b, "st_a": st_a, "st_b": st_b,
            "st_senza_watch": st_senza_watch, "w_a": w_a, "w_b": w_b, "acc": acc}


def righe(mondo, sql, params=()):
    with mondo["conn"].cursor() as cur:
        cur.execute(sql, params)
        valori = cur.fetchall()
    mondo["conn"].rollback()
    return valori


def conta(mondo, sql, params=()):
    return righe(mondo, sql, params)[0][0]


def snapshots(mondo, watch_id):
    return righe(mondo, "SELECT payload, idempotency_key, observed_at FROM property_watch_observations "
                        "WHERE watch_id=%s AND observation_type='valuation_snapshot' "
                        "ORDER BY observed_at, id", (watch_id,))


# ---------------------------------------------------------------------------
# 1-10: il refresh scrive uno snapshot completo, la baseline non si muove
# ---------------------------------------------------------------------------

def test_1_la_baseline_resta_quella_che_era(mondo, modulo):
    prima = righe(mondo, "SELECT payload, observed_at FROM property_watch_observations "
                         "WHERE watch_id=%s AND observation_type='watch_started'", (mondo["w_a"],))
    modulo["pw"].refresh_valuation_snapshot_scoped(Scope(mondo["a"]), mondo["st_a"], reason="manual")
    dopo = righe(mondo, "SELECT payload, observed_at FROM property_watch_observations "
                        "WHERE watch_id=%s AND observation_type='watch_started'", (mondo["w_a"],))
    assert prima == dopo
    assert prima[0][0]["price_exact"] == 185000


def test_2_e_5_10_il_refresh_crea_uno_snapshot_completo(mondo, modulo):
    esito = modulo["pw"].refresh_valuation_snapshot_scoped(
        Scope(mondo["a"]), mondo["st_a"], reason="manual")
    assert esito["status"] == "created"

    righe_snapshot = snapshots(mondo, mondo["w_a"])
    assert len(righe_snapshot) == 1
    payload = righe_snapshot[0][0]
    for campo in ("price_exact", "eur_mq_finale", "base_mq", "computed_at", "reason",
                  "algorithm_fingerprint", "input_digest"):
        assert campo in payload, campo
    assert payload["reason"] == "manual"
    assert payload["algorithm_fingerprint"] == modulo["vs"].ALGORITHM_FINGERPRINT
    assert payload["price_exact"] > 0 and payload["eur_mq_finale"] > 0 and payload["base_mq"] > 0


def test_3_e_4_il_valore_e_quello_del_motore_ufficiale(mondo, modulo):
    from valuation import compute_from_payload

    modulo["pw"].refresh_valuation_snapshot_scoped(Scope(mondo["a"]), mondo["st_a"], reason="manual")
    payload = snapshots(mondo, mondo["w_a"])[0][0]

    stima = righe(mondo, "SELECT * FROM stime WHERE id=%s", (mondo["st_a"],))
    colonne = [d[0] for d in righe_desc(mondo, "SELECT * FROM stime WHERE id=%s", (mondo["st_a"],))]
    riga = dict(zip(colonne, stima[0]))
    atteso = compute_from_payload(modulo["vs"].build_engine_payload(riga))
    assert payload["price_exact"] == atteso["price_exact"]
    assert payload["eur_mq_finale"] == atteso["eur_mq_finale"]
    assert payload["base_mq"] == atteso["base_mq"]


def righe_desc(mondo, sql, params=()):
    with mondo["conn"].cursor() as cur:
        cur.execute(sql, params)
        descrizione = cur.description
    mondo["conn"].rollback()
    return descrizione


# ---------------------------------------------------------------------------
# 11-13: idempotenza
# ---------------------------------------------------------------------------

def test_11_stesso_input_e_stesso_algoritmo_non_scrivono_due_volte(mondo, modulo):
    primo = modulo["pw"].refresh_valuation_snapshot_scoped(Scope(mondo["a"]), mondo["st_a"], reason="manual")
    for _ in range(5):
        dopo = modulo["pw"].refresh_valuation_snapshot_scoped(Scope(mondo["a"]), mondo["st_a"], reason="scheduled")
        assert dopo["status"] == "reused"
        assert dopo["idempotency_key"] == primo["idempotency_key"]
    assert len(snapshots(mondo, mondo["w_a"])) == 1
    # E il primo non e' stato sovrascritto: la ragione e' ancora quella.
    assert snapshots(mondo, mondo["w_a"])[0][0]["reason"] == "manual"


def test_12_un_input_diverso_produce_un_nuovo_snapshot(mondo, modulo):
    primo = modulo["pw"].refresh_valuation_snapshot_scoped(Scope(mondo["a"]), mondo["st_a"], reason="manual")
    with mondo["conn"].cursor() as cur:
        cur.execute("UPDATE stime SET mq = 120 WHERE id=%s", (mondo["st_a"],))
    mondo["conn"].commit()

    secondo = modulo["pw"].refresh_valuation_snapshot_scoped(Scope(mondo["a"]), mondo["st_a"], reason="manual")
    assert secondo["status"] == "created"
    righe_snapshot = snapshots(mondo, mondo["w_a"])
    assert len(righe_snapshot) == 2
    assert righe_snapshot[0][0]["price_exact"] != righe_snapshot[1][0]["price_exact"]
    assert righe_snapshot[0][0]["input_digest"] != righe_snapshot[1][0]["input_digest"]
    # Il precedente e' intatto.
    assert righe_snapshot[0][1] == primo["idempotency_key"]


def test_13_un_algoritmo_diverso_produce_un_nuovo_snapshot(mondo, modulo, monkeypatch):
    modulo["pw"].refresh_valuation_snapshot_scoped(Scope(mondo["a"]), mondo["st_a"], reason="manual")
    monkeypatch.setattr(modulo["vs"], "ALGORITHM_FINGERPRINT", "valuation-metodo-nuovo")
    secondo = modulo["pw"].refresh_valuation_snapshot_scoped(Scope(mondo["a"]), mondo["st_a"], reason="manual")

    assert secondo["status"] == "created"
    righe_snapshot = snapshots(mondo, mondo["w_a"])
    assert len(righe_snapshot) == 2
    impronte = [r[0]["algorithm_fingerprint"] for r in righe_snapshot]
    assert impronte[1] == "valuation-metodo-nuovo" and impronte[0] != impronte[1]


def test_11b_giorni_diversi_sono_punti_diversi(mondo, modulo):
    ieri = datetime.now(timezone.utc) - timedelta(days=1)
    modulo["pw"].refresh_valuation_snapshot_scoped(Scope(mondo["a"]), mondo["st_a"],
                                                   reason="scheduled", now=ieri)
    oggi = modulo["pw"].refresh_valuation_snapshot_scoped(Scope(mondo["a"]), mondo["st_a"],
                                                          reason="scheduled")
    assert oggi["status"] == "created"
    assert len(snapshots(mondo, mondo["w_a"])) == 2


# ---------------------------------------------------------------------------
# 14-16: tenancy e casi limite
# ---------------------------------------------------------------------------

def test_14_l_agenzia_a_non_puo_fare_snapshot_della_stima_di_b(mondo, modulo):
    from property_watch.exceptions import StimaNotFoundError

    with pytest.raises(StimaNotFoundError):
        modulo["pw"].refresh_valuation_snapshot_scoped(Scope(mondo["a"]), mondo["st_b"], reason="manual")
    assert snapshots(mondo, mondo["w_b"]) == []
    assert conta(mondo, "SELECT count(*) FROM property_watch_observations") == 1  # solo la baseline


def test_14b_e_nemmeno_indovinando_il_watch_dell_altra_agenzia(mondo, modulo):
    from property_watch.exceptions import StimaNotFoundError

    with pytest.raises(StimaNotFoundError):
        modulo["pw"].refresh_valuation_snapshot_scoped(Scope(mondo["b"]), mondo["st_a"], reason="manual")
    assert snapshots(mondo, mondo["w_a"]) == []


def test_15_senza_watch_non_si_scrive_niente(mondo, modulo):
    from property_watch.exceptions import WatchNotFoundError

    with pytest.raises(WatchNotFoundError):
        modulo["pw"].refresh_valuation_snapshot_scoped(Scope(mondo["a"]), mondo["st_senza_watch"],
                                                       reason="manual")
    assert conta(mondo, "SELECT count(*) FROM property_watches") == 2
    assert conta(mondo, "SELECT count(*) FROM property_watch_observations "
                        "WHERE observation_type='valuation_snapshot'") == 0


def test_16_una_stima_inesistente_non_scrive_niente(mondo, modulo):
    from property_watch.exceptions import StimaNotFoundError

    with pytest.raises(StimaNotFoundError):
        modulo["pw"].refresh_valuation_snapshot_scoped(Scope(mondo["a"]), 999999, reason="manual")
    assert conta(mondo, "SELECT count(*) FROM property_watch_observations "
                        "WHERE observation_type='valuation_snapshot'") == 0


# ---------------------------------------------------------------------------
# 17-24: il read-model del portale, sugli snapshot veri
# ---------------------------------------------------------------------------

def test_17_e_18_current_value_e_storico_dal_database(mondo, modulo):
    modulo["pw"].refresh_valuation_snapshot_scoped(Scope(mondo["a"]), mondo["st_a"], reason="manual")
    vista = modulo["home_service"].get_home(mondo["acc"], mondo["st_a"])

    atteso = snapshots(mondo, mondo["w_a"])[0][0]["price_exact"]
    assert vista["valuation"]["current_value"] == atteso
    assert vista["valuation"]["current_value_status"] == "available"
    assert vista["valuation"]["initial_value"] == 185000, "la baseline non si muove"
    assert vista["capabilities"]["valuation_history"] is True
    assert len(vista["valuation_history"]) == 1
    assert vista["valuation_history"][0]["price_exact"] == atteso


def test_19_le_osservazioni_di_mercato_non_entrano_nello_storico(mondo, modulo):
    modulo["pw"].refresh_valuation_snapshot_scoped(Scope(mondo["a"]), mondo["st_a"], reason="manual")
    with mondo["conn"].cursor() as cur:
        cur.execute("INSERT INTO property_watch_observations (watch_id,observation_type,source,payload,"
                    "idempotency_key) VALUES (%s,'buyer_pressure_snapshot','internal',%s,'k-bp')",
                    (mondo["w_a"], json.dumps({"score": 82, "budget_reference": 210000})))
        cur.execute("INSERT INTO property_watch_observations (watch_id,observation_type,source,payload,"
                    "idempotency_key) VALUES (%s,'internal_supply_snapshot','internal',%s,'k-sup')",
                    (mondo["w_a"], json.dumps({"supply_count": 9})))
    mondo["conn"].commit()

    vista = modulo["home_service"].get_home(mondo["acc"], mondo["st_a"])
    assert len(vista["valuation_history"]) == 1
    assert vista["history"]["observation_count"] == 4
    testo = repr(vista).lower()
    for vietato in ("82", "210000", "budget", "score", "supply", "mario", "richiamare"):
        assert vietato not in testo, vietato


@pytest.mark.parametrize("periodo", ["change_30d", "change_90d", "change_365d"])
def test_20_21_22_senza_storia_abbastanza_vecchia_le_variazioni_sono_null(mondo, modulo, periodo):
    modulo["pw"].refresh_valuation_snapshot_scoped(Scope(mondo["a"]), mondo["st_a"], reason="manual")
    vista = modulo["home_service"].get_home(mondo["acc"], mondo["st_a"])
    assert vista["valuation"][periodo] is None


def test_23_la_variazione_su_snapshot_reali(mondo, modulo):
    vecchio = datetime.now(timezone.utc) - timedelta(days=100)
    with mondo["conn"].cursor() as cur:
        cur.execute("INSERT INTO property_watch_observations (watch_id,observation_type,source,payload,"
                    "idempotency_key,observed_at) VALUES (%s,'valuation_snapshot','internal',%s,'k-old',%s)",
                    (mondo["w_a"], json.dumps({
                        "price_exact": 180000, "eur_mq_finale": 1894, "base_mq": 1500,
                        "computed_at": vecchio.isoformat(), "reason": "scheduled",
                        "algorithm_fingerprint": modulo["vs"].ALGORITHM_FINGERPRINT,
                        "input_digest": "a" * 64}), vecchio))
    mondo["conn"].commit()
    modulo["pw"].refresh_valuation_snapshot_scoped(Scope(mondo["a"]), mondo["st_a"], reason="manual")

    vista = modulo["home_service"].get_home(mondo["acc"], mondo["st_a"])
    corrente = vista["valuation"]["current_value"]
    novanta = vista["valuation"]["change_90d"]
    assert novanta is not None
    assert novanta["from_value"] == 180000 and novanta["to_value"] == corrente
    assert novanta["methodology_changed"] is False
    assert novanta["change_percent"] == pytest.approx(
        (corrente - 180000) / 180000 * 100, abs=0.01)
    # Uno snapshot di 100 giorni fa e' oltre il confine anche dei 30: la
    # finestra piu' corta usa lo stesso punto, che e' il piu' recente fra
    # quelli abbastanza vecchi.
    assert vista["valuation"]["change_30d"]["from_value"] == 180000
    # Nessuno snapshot supera l'anno.
    assert vista["valuation"]["change_365d"] is None


def test_24_metodologia_diversa_niente_percentuale(mondo, modulo):
    vecchio = datetime.now(timezone.utc) - timedelta(days=100)
    with mondo["conn"].cursor() as cur:
        cur.execute("INSERT INTO property_watch_observations (watch_id,observation_type,source,payload,"
                    "idempotency_key,observed_at) VALUES (%s,'valuation_snapshot','internal',%s,'k-old',%s)",
                    (mondo["w_a"], json.dumps({
                        "price_exact": 180000, "eur_mq_finale": 1894, "base_mq": 1500,
                        "computed_at": vecchio.isoformat(), "reason": "scheduled",
                        "algorithm_fingerprint": "valuation-metodo-vecchio",
                        "input_digest": "a" * 64}), vecchio))
    mondo["conn"].commit()
    modulo["pw"].refresh_valuation_snapshot_scoped(Scope(mondo["a"]), mondo["st_a"], reason="manual")

    novanta = modulo["home_service"].get_home(mondo["acc"], mondo["st_a"])["valuation"]["change_90d"]
    assert novanta["methodology_changed"] is True
    assert novanta["change_percent"] is None
    assert novanta["from_value"] == 180000


# ---------------------------------------------------------------------------
# 25-27: l'ancora delle finestre e il timestamp del valore corrente, sul DB
# ---------------------------------------------------------------------------

def _scrivi_snapshot(mondo, *, quando, prezzo, chiave, fingerprint=None):
    """Uno snapshot vero in tabella, datato. Serve a costruire una storia che
    il motore non puo' produrre in un test (i giorni passati)."""
    with mondo["conn"].cursor() as cur:
        cur.execute("INSERT INTO property_watch_observations (watch_id,observation_type,source,payload,"
                    "idempotency_key,observed_at) VALUES (%s,'valuation_snapshot','internal',%s,%s,%s)",
                    (mondo["w_a"], json.dumps({
                        "price_exact": prezzo, "eur_mq_finale": 1894, "base_mq": 1500,
                        "computed_at": quando.isoformat(), "reason": "scheduled",
                        "algorithm_fingerprint": fingerprint or "valuation-fissa",
                        "input_digest": "a" * 64}), chiave, quando))
    mondo["conn"].commit()


def test_25_current_value_computed_at_arriva_dallo_snapshot_scritto(mondo, modulo):
    modulo["pw"].refresh_valuation_snapshot_scoped(Scope(mondo["a"]), mondo["st_a"], reason="manual")
    vista = modulo["home_service"].get_home(mondo["acc"], mondo["st_a"])
    valutazione = vista["valuation"]

    scritto = snapshots(mondo, mondo["w_a"])[0][0]["computed_at"]
    assert valutazione["current_value_computed_at"] == scritto
    assert valutazione["current_value_computed_at"] == \
        vista["valuation_history"][-1]["computed_at"]
    assert valutazione["current_value_status"] == "available"


def test_26_senza_snapshot_il_timestamp_corrente_e_null(mondo, modulo):
    valutazione = modulo["home_service"].get_home(mondo["acc"], mondo["st_a"])["valuation"]
    assert valutazione["current_value"] is None
    assert valutazione["current_value_computed_at"] is None
    assert valutazione["current_value_status"] == "history_not_available"
    assert valutazione["initial_value"] == 185000, "la baseline resta, e non e' un valore di oggi"


def test_27_la_finestra_e_ancorata_all_ultimo_snapshot_non_a_oggi(mondo, modulo):
    """Storia reale in tabella: 80 giorni fa e 40 giorni fa, nessun ricalcolo
    di oggi. Il confine dei 30 giorni e' `40 + 30 = 70 giorni fa`, quindi il
    FROM e' il punto di 80; quello dei 90 e' `130 giorni fa`, che nessuno
    supera. Con l'orologio come ancora, `change_90d` avrebbe risposto."""
    adesso = datetime.now(timezone.utc)
    ottanta = adesso - timedelta(days=80)
    quaranta = adesso - timedelta(days=40)
    _scrivi_snapshot(mondo, quando=ottanta, prezzo=180000, chiave="k-80")
    _scrivi_snapshot(mondo, quando=quaranta, prezzo=200000, chiave="k-40")

    valutazione = modulo["home_service"].get_home(mondo["acc"], mondo["st_a"])["valuation"]
    assert valutazione["current_value"] == 200000
    assert valutazione["current_value_computed_at"] == quaranta.isoformat()

    trenta = valutazione["change_30d"]
    assert trenta is not None
    assert trenta["from_computed_at"] == ottanta.isoformat()
    assert trenta["to_computed_at"] == quaranta.isoformat()
    assert trenta["from_value"] == 180000 and trenta["to_value"] == 200000
    assert trenta["methodology_changed"] is False

    assert valutazione["change_90d"] is None, "40 + 90 = 130 giorni fa: nessuno lo supera"
    assert valutazione["change_365d"] is None
