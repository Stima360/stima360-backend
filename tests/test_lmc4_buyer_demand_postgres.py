"""LMC-4 su PostgreSQL reale: la domanda acquirenti vista dal proprietario.

Cio' che solo un database sa dire: che la rilevazione letta sia quella del
watch di QUESTA stima in QUESTA agenzia, che un grant revocato o scaduto non
apra nessuna lettura, e che dal payload reale di `buyer_pressure_snapshot` -
che contiene punteggi, budget medi e conteggi interni - esca soltanto il
blocco aggregato.

LMC-4 e' sola lettura: nessun test qui scrive attraverso il codice di
produzione, e un test lo verifica contando le righe prima e dopo.

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
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare LMC-4")

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

    nome = f"lmc4_probe_{os.getpid()}_{uuid.uuid4().hex[:6]}"
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
    from owner import repository as owner_repository
    from property_watch import database as pw_database
    from property_watch import service as pw_service

    monkeypatch.setattr(core_database, "get_connection", lambda: psycopg2.connect(db["dsn"]))
    monkeypatch.setattr(pw_database, "get_connection", lambda: psycopg2.connect(db["dsn"]))
    return {"pw": pw_service, "home_service": home_service,
            "owner_repository": owner_repository}


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


METRICHE_REALI = {
    "evaluated_buyers": 24,
    "compatible_buyers": 9,
    "highly_compatible_buyers": 4,
    "recent_compatible_buyers_30d": 5,
    "average_match_score": 74.5,
    "maximum_match_score": 91.0,
    # Il campo che il portale non deve nemmeno leggere.
    "average_budget": 238000.0,
    "algorithm_version": "match-1.0",
}


def scrivi_pressure(mondo, watch_id, metriche=None, *, chiave="k-bp",
                    tipo="buyer_pressure_snapshot", quando="NOW()"):
    with mondo["conn"].cursor() as cur:
        cur.execute(
            "INSERT INTO property_watch_observations (watch_id,observation_type,source,"
            f"payload,idempotency_key,observed_at) VALUES (%s,%s,'internal',%s,%s,{quando})",
            (watch_id, tipo, json.dumps(metriche or METRICHE_REALI), chiave))
    mondo["conn"].commit()


def owner_di_b(mondo):
    """Un secondo proprietario, nell'altra agenzia, con il grant sulla SUA stima."""
    with mondo["conn"].cursor() as cur:
        cur.execute("INSERT INTO contacts (agency_id, display_name, email, email_normalized) "
                    "VALUES (%s,'Lucia Bianchi','lucia@example.it','lucia@example.it') RETURNING id",
                    (mondo["b"],))
        k = cur.fetchone()[0]
        cur.execute("INSERT INTO owner_accounts (contact_id,status) VALUES (%s,'active') RETURNING id", (k,))
        acc = cur.fetchone()[0]
        cur.execute("INSERT INTO owner_stima_access (owner_account_id,stima_id,granted_by) "
                    "VALUES (%s,%s,'LMC_PROVISIONING')", (acc, mondo["st_b"]))
    mondo["conn"].commit()
    return acc


def blocco_di(modulo, account, stima_id):
    return modulo["home_service"].get_home(account, stima_id)["buyer_demand"]


def numeri_in(oggetto):
    """Tutti i valori numerici della struttura, a qualunque profondita'.

    Cercare "82" come sottostringa del JSON e' inaffidabile: quelle due cifre
    vivono anche dentro i microsecondi di un timestamp. Un numero interno o
    c'e' come VALORE o non c'e'.
    """
    if isinstance(oggetto, bool):
        return []
    if isinstance(oggetto, (int, float)):
        return [float(oggetto)]
    if isinstance(oggetto, dict):
        return [n for v in oggetto.values() for n in numeri_in(v)]
    if isinstance(oggetto, (list, tuple)):
        return [n for v in oggetto for n in numeri_in(v)]
    return []


def parole_in(oggetto):
    """Le sole stringhe della struttura, unite. Niente date, niente numeri."""
    if isinstance(oggetto, str):
        return oggetto.lower()
    if isinstance(oggetto, dict):
        return " ".join(f"{k} {parole_in(v)}" for k, v in oggetto.items()).lower()
    if isinstance(oggetto, (list, tuple)):
        return " ".join(parole_in(v) for v in oggetto).lower()
    return ""


# ---------------------------------------------------------------------------
# 1-4: la rilevazione vera, letta dal database
# ---------------------------------------------------------------------------

def test_1_una_rilevazione_reale_produce_il_blocco(mondo, modulo):
    scrivi_pressure(mondo, mondo["w_a"])
    vista = modulo["home_service"].get_home(mondo["acc"], mondo["st_a"])
    blocco = vista["buyer_demand"]

    assert blocco["status"] in ("high", "medium", "low")
    assert blocco["compatible_requests"] == 9
    assert blocco["recent_compatible_requests"] == 5
    assert blocco["recency_days"] == 30
    assert blocco["updated_at"] is not None
    assert vista["capabilities"]["buyer_demand"] is True


def test_2_senza_rilevazione_unavailable_e_capability_false(mondo, modulo):
    vista = modulo["home_service"].get_home(mondo["acc"], mondo["st_a"])
    assert vista["buyer_demand"]["status"] == "unavailable"
    assert vista["buyer_demand"]["compatible_requests"] is None
    assert vista["capabilities"]["buyer_demand"] is False


def test_3_vale_anche_l_osservazione_di_cambiamento(mondo, modulo):
    """Il dominio scrive `buyer_pressure_snapshot` la prima volta e
    `buyer_pressure_changed` dopo: contano entrambe, e vince la piu' recente."""
    scrivi_pressure(mondo, mondo["w_a"], chiave="k-1",
                    quando="NOW() - INTERVAL '10 days'")
    recente = {**METRICHE_REALI, "compatible_buyers": 2,
               "highly_compatible_buyers": 0, "recent_compatible_buyers_30d": 1,
               "average_match_score": 57.0, "maximum_match_score": 58.0}
    scrivi_pressure(mondo, mondo["w_a"], recente, chiave="k-2",
                    tipo="buyer_pressure_changed")
    blocco = blocco_di(modulo, mondo["acc"], mondo["st_a"])
    assert blocco["compatible_requests"] == 2, "l'ultima rilevazione, non la prima"


def test_4_un_payload_non_canonico_non_rompe_il_portale(mondo, modulo):
    scrivi_pressure(mondo, mondo["w_a"],
                    {"score": 82, "buy_requests": [11, 12], "budget_reference": 210000})
    vista = modulo["home_service"].get_home(mondo["acc"], mondo["st_a"])
    assert vista["buyer_demand"]["status"] == "unavailable"
    assert vista["capabilities"]["buyer_demand"] is False
    for vietato in (82, 210000, 11, 12):
        assert vietato not in numeri_in(vista), vietato
    for vietato in ("budget", "score", "buy_request", "pressure"):
        assert vietato not in parole_in(vista), vietato


# ---------------------------------------------------------------------------
# 5-8: privacy sul JSON davvero serializzato
# ---------------------------------------------------------------------------

def test_5_il_json_del_dettaglio_non_porta_niente_di_interno(mondo, modulo):
    scrivi_pressure(mondo, mondo["w_a"])
    vista = modulo["home_service"].get_home(mondo["acc"], mondo["st_a"])
    for vietato in (238000, 24, 74.5, 91.0):
        assert vietato not in numeri_in(vista), vietato
    for vietato in ("average_budget", "budget", "evaluated", "match-1.0", "score",
                    "pressure", "factor", "buy_request", "match_id", "ranking",
                    "mario", "rossi", "@example.it", "richiamare",
                    "note_internal", "lead_status"):
        assert vietato not in parole_in(vista), vietato


def test_6_i_dati_personali_dei_buyer_non_esistono_nel_percorso(mondo, modulo):
    """Non c'e' bisogno di filtrarli: la rilevazione e' gia' aggregata, e la
    query del portale non tocca ne' `buy_requests` ne' `contacts`."""
    import inspect
    sorgente = inspect.getsource(modulo["owner_repository"].home_buyer_pressure).lower()
    for tabella in ("buy_requests", "matches", "contacts", "leads"):
        assert tabella not in sorgente, tabella


def test_7_la_rilevazione_esce_solo_aggregata(mondo, modulo):
    scrivi_pressure(mondo, mondo["w_a"])
    blocco = blocco_di(modulo, mondo["acc"], mondo["st_a"])
    # Valori piatti soltanto: nessuna lista, nessun dizionario in cui un
    # dettaglio interno possa viaggiare di nascosto. `updated_at` arriva da
    # PostgreSQL come datetime, come ogni altra data del read-model.
    assert all(isinstance(v, (str, int, datetime, type(None)))
               and not isinstance(v, bool) for v in blocco.values()), blocco
    assert set(blocco) == {"status", "label", "message", "compatible_requests",
                           "recent_compatible_requests", "recency_days",
                           "updated_at", "disclaimer"}


def test_8_il_disclaimer_arriva_fino_al_read_model(mondo, modulo):
    scrivi_pressure(mondo, mondo["w_a"])
    disclaimer = blocco_di(modulo, mondo["acc"], mondo["st_a"])["disclaimer"].lower()
    # Senza apostrofi: la copy usa quello tipografico.
    piatto = disclaimer.replace("\u2019", "").replace("'", "")
    assert "non garantisce" in piatto
    assert "non rappresenta lintero mercato" in piatto
    assert "tutto il mercato" not in piatto


# ---------------------------------------------------------------------------
# 9-13: tenancy e grant
# ---------------------------------------------------------------------------

def test_9_l_owner_di_a_non_vede_la_stima_di_b(mondo, modulo):
    from core.exceptions import NotFoundError
    scrivi_pressure(mondo, mondo["w_b"])
    with pytest.raises(NotFoundError):
        modulo["home_service"].get_home(mondo["acc"], mondo["st_b"])


def test_10_e_non_vede_la_domanda_dell_altra_agenzia_sulla_propria_casa(mondo, modulo):
    """La rilevazione esiste, ma appartiene al watch di B: per la casa di A
    non c'e' niente da leggere."""
    scrivi_pressure(mondo, mondo["w_b"])
    blocco = blocco_di(modulo, mondo["acc"], mondo["st_a"])
    assert blocco["status"] == "unavailable"
    assert blocco["compatible_requests"] is None


def test_11_due_owner_due_agenzie_due_risposte(mondo, modulo):
    acc_b = owner_di_b(mondo)
    scrivi_pressure(mondo, mondo["w_a"], chiave="k-a")
    scrivi_pressure(mondo, mondo["w_b"],
                    {**METRICHE_REALI, "compatible_buyers": 1,
                     "highly_compatible_buyers": 0, "recent_compatible_buyers_30d": 0,
                     "average_match_score": 56.0, "maximum_match_score": 56.0},
                    chiave="k-b")
    assert blocco_di(modulo, mondo["acc"], mondo["st_a"])["compatible_requests"] == 9
    assert blocco_di(modulo, acc_b, mondo["st_b"])["compatible_requests"] == 1


def test_12_la_query_porta_sempre_l_agenzia(mondo, modulo):
    import inspect
    sorgente = inspect.getsource(modulo["owner_repository"].home_buyer_pressure)
    assert "agency_id" in sorgente, "stima_id da solo non e' un tenant"


@pytest.mark.parametrize("rottura", [
    "UPDATE owner_stima_access SET revoked_at = NOW(), access_status='revoked'",
    "UPDATE owner_stima_access SET valid_until = NOW() - INTERVAL '1 day', "
    "valid_from = NOW() - INTERVAL '2 days'",
    "UPDATE owner_stima_access SET access_status = 'expired'",
])
def test_13_grant_non_valido_nessuna_lettura(mondo, modulo, rottura):
    """Il 404 neutro arriva PRIMA di qualunque lettura di Property Watch."""
    from core.exceptions import NotFoundError
    scrivi_pressure(mondo, mondo["w_a"])
    with mondo["conn"].cursor() as cur:
        cur.execute(rottura)
    mondo["conn"].commit()
    with pytest.raises(NotFoundError):
        modulo["home_service"].get_home(mondo["acc"], mondo["st_a"])


# ---------------------------------------------------------------------------
# 14-15: LMC-4 non scrive
# ---------------------------------------------------------------------------

def test_14_leggere_il_dettaglio_non_crea_osservazioni(mondo, modulo):
    scrivi_pressure(mondo, mondo["w_a"])
    prima = conta(mondo, "SELECT COUNT(*) FROM property_watch_observations")
    for _ in range(3):
        modulo["home_service"].get_home(mondo["acc"], mondo["st_a"])
    assert conta(mondo, "SELECT COUNT(*) FROM property_watch_observations") == prima


def test_15_e_non_crea_eventi_di_seller_intelligence(mondo, modulo):
    scrivi_pressure(mondo, mondo["w_a"])
    prima = conta(mondo, "SELECT COUNT(*) FROM seller_timeline_events")
    modulo["home_service"].get_home(mondo["acc"], mondo["st_a"])
    assert conta(mondo, "SELECT COUNT(*) FROM seller_timeline_events") == prima


# ---------------------------------------------------------------------------
# 16-17: la lista resta quella di LMC-2
# ---------------------------------------------------------------------------

def test_16_la_lista_non_espone_la_domanda(mondo, modulo):
    """Scelta di LMC-4 (par. 9): il sintetico in lista costerebbe una lettura
    di Property Watch per ogni casa, e il dettaglio basta."""
    scrivi_pressure(mondo, mondo["w_a"])
    for casa in modulo["home_service"].list_homes(mondo["acc"]):
        assert "buyer_demand" not in casa
        assert "buyer_demand_status" not in casa


def test_17_il_resto_del_dettaglio_non_e_cambiato(mondo, modulo):
    scrivi_pressure(mondo, mondo["w_a"])
    vista = modulo["home_service"].get_home(mondo["acc"], mondo["st_a"])
    assert vista["valuation"]["initial_value"] == 185000
    assert vista["valuation"]["current_value"] is None
    assert vista["capabilities"]["valuation_history"] is False
    assert vista["capabilities"]["comparables"] is False
    # LMC-10 (collisione autorizzata): la capability di modifica e' vera da
    # quella fase. Cio' che questo test verifica - che il resto del
    # dettaglio non sia cambiato per colpa di LMC-4 - resta nelle righe
    # attorno.
    assert vista["capabilities"]["profile_update"] is True
    assert vista["history"]["observation_count"] == 2


# ---------------------------------------------------------------------------
# 18: la copy owner-safe, letta dal database
# ---------------------------------------------------------------------------

GERGO_INTERNO = ("buy", "match", "flow", "score", "punteggio", "/100", "pressure",
                 "pressione", "algorithm", "algoritmo", "fingerprint", "factor",
                 "fattore", "metric", "metrica", "digest", "payload", "snapshot",
                 "watch", "band", "version", "evaluated", "budget", "engine",
                 "compatibility", "readiness", "internal", "interno")

CASI_REALI = {
    "high": {"compatible_buyers": 10, "highly_compatible_buyers": 5,
             "recent_compatible_buyers_30d": 8, "average_match_score": 90.0,
             "maximum_match_score": 98.0},
    "medium": {"compatible_buyers": 6, "highly_compatible_buyers": 2,
               "recent_compatible_buyers_30d": 3, "average_match_score": 70.0,
               "maximum_match_score": 80.0},
    "low": {"compatible_buyers": 1, "highly_compatible_buyers": 0,
            "recent_compatible_buyers_30d": 0, "average_match_score": 56.0,
            "maximum_match_score": 56.0},
    "none": {"compatible_buyers": 0, "highly_compatible_buyers": 0,
             "recent_compatible_buyers_30d": 0, "average_match_score": None,
             "maximum_match_score": None, "average_budget": None},
    "unavailable": None,
}


@pytest.mark.parametrize("caso", list(CASI_REALI))
def test_18_nessun_gergo_interno_in_nessun_caso_reale(mondo, modulo, caso):
    """Gli stessi cinque casi dei test in memoria, ma passando davvero per
    PostgreSQL: il payload viene scritto, riletto e serializzato."""
    if CASI_REALI[caso] is not None:
        scrivi_pressure(mondo, mondo["w_a"], {**METRICHE_REALI, **CASI_REALI[caso]})
    blocco = blocco_di(modulo, mondo["acc"], mondo["st_a"])
    atteso = {"high": "high", "medium": "medium", "low": "low",
              "none": "low", "unavailable": "unavailable"}[caso]
    assert blocco["status"] == atteso
    testo = json.dumps(blocco, ensure_ascii=False, default=str).lower()
    for termine in GERGO_INTERNO:
        assert termine not in testo, (caso, termine)
