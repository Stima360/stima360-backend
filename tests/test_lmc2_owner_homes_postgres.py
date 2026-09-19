"""LMC-2 su PostgreSQL reale: grant, tenancy, 404 neutro, fallback, dashboard.

Cio' che solo un database sa dire: che un grant revocato o scaduto renda la
home invisibile ESATTAMENTE come una stima che non esiste, che l'owner di
un'agenzia non veda la stima dell'altra nemmeno indovinando l'id, che il
valore iniziale arrivi davvero dalla baseline del watch e, se il watch non
c'e', dall'evento `stima_completata` della stessa stima, e che la dashboard
continui a servire gli owner legacy mentre serve le nuove home.

Opt-in: senza `P29_TEST_DSN` si salta tutto. Database usa-e-getta, creato e
cancellato qui; non si passa da `database.get_connection()`.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest

DSN = os.getenv("P29_TEST_DSN")

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare LMC-2")

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
    vistamareyn VARCHAR(10), distanzamare VARCHAR(50), altrodescrizione TEXT,
    nome VARCHAR(50), cognome VARCHAR(50), email VARCHAR(100), telefono VARCHAR(30),
    prezzo_mq_base NUMERIC(10,2), lead_status VARCHAR(32), note_internal TEXT,
    data TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE properties (
    id BIGSERIAL PRIMARY KEY,
    agency_id BIGINT NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,
    title VARCHAR(200), address VARCHAR(300), city VARCHAR(120));
CREATE TABLE activities (id BIGSERIAL PRIMARY KEY);
CREATE TABLE seller_timeline_events (
    id BIGSERIAL PRIMARY KEY,
    agency_id BIGINT,
    contact_id BIGINT, lead_id BIGINT,
    stima_id INTEGER REFERENCES stime(id) ON DELETE SET NULL,
    property_id BIGINT,
    event_type VARCHAR(50) NOT NULL, event_source VARCHAR(30),
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    idempotency_key VARCHAR(255), created_by VARCHAR(200),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
CREATE TABLE next_best_actions (id BIGSERIAL PRIMARY KEY, agency_id BIGINT,
    subject_type VARCHAR(30), subject_id BIGINT, contact_id BIGINT, lead_id BIGINT,
    stima_id INTEGER);
CREATE TABLE schema_migrations (version VARCHAR(200) PRIMARY KEY);
"""

CATENA = ("009_owner_01", "022_property_watch", "066_lmc1_owner_stima_access")


def _dsn_per(nome: str) -> str:
    if "?" in DSN:
        base, query = DSN.split("?", 1)
        return base.rsplit("/", 1)[0] + "/" + nome + "?" + query
    return DSN.rsplit("/", 1)[0] + "/" + nome


@pytest.fixture(scope="module")
def db():
    psycopg2 = pytest.importorskip("psycopg2")

    nome = f"lmc2_probe_{os.getpid()}_{uuid.uuid4().hex[:6]}"
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
            # LMC-1A/1B non sono in gioco qui, ma `property_watches` deve avere
            # la colonna che la 046 le ha dato: il funnel la scrive e il read
            # model la usa come predicato di tenant.
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
    from owner import home_service, repository
    from property_watch import database as pw_database

    monkeypatch.setattr(core_database, "get_connection", lambda: psycopg2.connect(db["dsn"]))
    monkeypatch.setattr(pw_database, "get_connection", lambda: psycopg2.connect(db["dsn"]))
    return {"home_service": home_service, "repository": repository}


@pytest.fixture
def mondo(db):
    """Agenzia A: Mario con due stime (una con watch+baseline, una con solo
    l'evento), piu' Anna owner legacy con un immobile. Agenzia B: Carla con la
    sua stima. Tutti con grant attivo."""
    conn = db["conn"]
    conn.rollback()
    with conn.cursor() as cur:
        for tabella in ("owner_access_tokens", "owner_sessions", "owner_audit_log",
                        "owner_stima_access", "owner_property_access", "owner_accounts",
                        "property_watch_observations", "property_watches",
                        "seller_timeline_events", "stime", "contacts", "properties", "agencies"):
            cur.execute(f"DELETE FROM {tabella}")

        def agenzia(slug):
            cur.execute("INSERT INTO agencies (slug) VALUES (%s) RETURNING id", (slug,))
            return cur.fetchone()[0]

        def contatto(agency, nome, email):
            cur.execute("INSERT INTO contacts (agency_id, display_name, email, email_normalized) "
                        "VALUES (%s,%s,%s,%s) RETURNING id", (agency, nome, email, email))
            return cur.fetchone()[0]

        def account(contact_id):
            cur.execute("INSERT INTO owner_accounts (contact_id, status) "
                        "VALUES (%s,'active') RETURNING id", (contact_id,))
            return cur.fetchone()[0]

        def stima(agency, comune, **extra):
            colonne = {"comune": comune, "microzona": "Lungomare", "via": "Via Trieste",
                       "civico": "12", "tipologia": "Appartamento", "mq": 95, "piano": "3",
                       "locali": 4, "bagni": 2, "ascensore": "True", "anno": 1998,
                       "stato": "buono", "pertinenze": "garage", "vistamareyn": "si",
                       "distanzamare": "0-100", "altrodescrizione": "ristrutturato",
                       "nome": "Mario", "cognome": "Rossi", "email": "mario@example.it",
                       "telefono": "+39 333 1234567", "prezzo_mq_base": 1500,
                       "lead_status": "nuovo", "note_internal": "richiamare"}
            colonne.update(extra)
            nomi = ", ".join(["agency_id"] + list(colonne))
            segna = ", ".join(["%s"] * (len(colonne) + 1))
            cur.execute(f"INSERT INTO stime ({nomi}) VALUES ({segna}) RETURNING id",
                        [agency] + list(colonne.values()))
            return cur.fetchone()[0]

        def grant(account_id, stima_id):
            cur.execute("INSERT INTO owner_stima_access (owner_account_id, stima_id, granted_by) "
                        "VALUES (%s,%s,'LMC_PROVISIONING') RETURNING id", (account_id, stima_id))
            return cur.fetchone()[0]

        a, b = agenzia("a-uno"), agenzia("b-due")
        k_mario = contatto(a, "Mario Rossi", "mario@example.it")
        acc_mario = account(k_mario)
        st_watch = stima(a, "Alba Adriatica")
        st_evento = stima(a, "Tortoreto")
        g_watch, g_evento = grant(acc_mario, st_watch), grant(acc_mario, st_evento)

        # La stima con watch e baseline reale.
        cur.execute("INSERT INTO property_watches (stima_id, status, agency_id) "
                    "VALUES (%s,'active',%s) RETURNING id", (st_watch, a))
        watch = cur.fetchone()[0]
        cur.execute("INSERT INTO property_watch_observations (watch_id, observation_type, source, "
                    "payload, idempotency_key, observed_at) VALUES (%s,'watch_started','internal',"
                    "%s,%s, NOW() - INTERVAL '10 days')",
                    (watch, json.dumps({"price_exact": 185000, "eur_mq_finale": 1947,
                                        "prezzo_mq_base": 1500}), f"k-watch-{st_watch}"))
        # La stima senza watch, ma con l'evento di valutazione completata.
        cur.execute("INSERT INTO seller_timeline_events (agency_id, stima_id, event_type, "
                    "event_source, payload) VALUES (%s,%s,'stima_completata','stima360_it',%s)",
                    (a, st_evento, json.dumps({"price_exact": 149000, "eur_mq_finale": 1568})))

        # L'owner legacy: solo un immobile.
        k_anna = contatto(a, "Anna Legacy", "anna@example.it")
        acc_anna = account(k_anna)
        cur.execute("INSERT INTO properties (agency_id, title, address, city) "
                    "VALUES (%s,'Villa','Via Roma 1','Alba Adriatica') RETURNING id", (a,))
        prop = cur.fetchone()[0]
        cur.execute("INSERT INTO owner_property_access (owner_account_id, property_id) "
                    "VALUES (%s,%s)", (acc_anna, prop))

        # L'altra agenzia.
        k_carla = contatto(b, "Carla Bianchi", "carla@example.it")
        acc_carla = account(k_carla)
        st_b = stima(b, "Giulianova")
        grant(acc_carla, st_b)
    conn.commit()
    return {"conn": conn, "a": a, "b": b, "acc_mario": acc_mario, "acc_anna": acc_anna,
            "acc_carla": acc_carla, "st_watch": st_watch, "st_evento": st_evento,
            "st_b": st_b, "g_watch": g_watch, "g_evento": g_evento, "prop": prop,
            "watch": watch}


def righe(mondo, sql, params=()):
    with mondo["conn"].cursor() as cur:
        cur.execute(sql, params)
        valori = cur.fetchall()
    mondo["conn"].rollback()
    return valori


def conta(mondo, sql, params=()):
    return righe(mondo, sql, params)[0][0]


def nf():
    from core.exceptions import NotFoundError
    return NotFoundError


# ---------------------------------------------------------------------------
# 1-2: la lista
# ---------------------------------------------------------------------------

def test_1_e_2_l_owner_vede_tutte_e_sole_le_sue_home(mondo, modulo):
    lista = modulo["home_service"].list_homes(mondo["acc_mario"])
    assert [h["stima_id"] for h in lista] == sorted([mondo["st_watch"], mondo["st_evento"]])
    assert {h["comune"] for h in lista} == {"Alba Adriatica", "Tortoreto"}

    con_watch = next(h for h in lista if h["stima_id"] == mondo["st_watch"])
    senza = next(h for h in lista if h["stima_id"] == mondo["st_evento"])
    assert con_watch["has_watch"] is True and con_watch["initial_value"] == 185000
    assert senza["has_watch"] is False and senza["initial_value"] == 149000
    assert con_watch["data_status"] == "ready"
    assert senza["data_status"] == "building_history"


def test_2b_la_lista_non_porta_dati_privati(mondo, modulo):
    testo = repr(modulo["home_service"].list_homes(mondo["acc_mario"])).lower()
    for vietato in ("mario", "rossi", "mario@example.it", "333", "richiamare",
                    "nuovo", "1500", "agency"):
        assert vietato not in testo, vietato


def test_3_un_owner_non_vede_le_home_di_un_altro(mondo, modulo):
    assert modulo["home_service"].list_homes(mondo["acc_anna"]) == []
    with pytest.raises(nf()):
        modulo["home_service"].get_home(mondo["acc_anna"], mondo["st_watch"])


def test_4_un_owner_non_vede_la_stima_dell_altra_agenzia(mondo, modulo):
    assert [h["stima_id"] for h in modulo["home_service"].list_homes(mondo["acc_carla"])] \
        == [mondo["st_b"]]
    with pytest.raises(nf()):
        modulo["home_service"].get_home(mondo["acc_carla"], mondo["st_watch"])
    with pytest.raises(nf()):
        modulo["home_service"].get_home(mondo["acc_mario"], mondo["st_b"])


def test_4b_un_grant_fra_due_agenzie_non_esiste_e_non_si_legge(mondo, modulo):
    """Il trigger della 066 rifiuta la scrittura; e se una riga incoerente
    fosse arrivata prima del trigger, il predicato del read model la ignora."""
    esito = None
    try:
        with mondo["conn"].cursor() as cur:
            cur.execute("INSERT INTO owner_stima_access (owner_account_id, stima_id) VALUES (%s,%s)",
                        (mondo["acc_mario"], mondo["st_b"]))
        mondo["conn"].commit()
    except Exception as exc:
        mondo["conn"].rollback()
        esito = exc
    assert esito is not None and "LMC-1A owner/stima tenancy" in str(esito)
    with pytest.raises(nf()):
        modulo["home_service"].get_home(mondo["acc_mario"], mondo["st_b"])


# ---------------------------------------------------------------------------
# 5-8: il 404 neutro
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("caso", ["revocato", "scaduto", "inesistente", "altro_owner", "altro_tenant"])
def test_5_6_7_8_ogni_rifiuto_e_lo_stesso_rifiuto(mondo, modulo, caso):
    bersaglio = mondo["st_watch"]
    account = mondo["acc_mario"]
    if caso == "revocato":
        with mondo["conn"].cursor() as cur:
            cur.execute("UPDATE owner_stima_access SET access_status='revoked', revoked_at=NOW() "
                        "WHERE id=%s", (mondo["g_watch"],))
        mondo["conn"].commit()
    elif caso == "scaduto":
        with mondo["conn"].cursor() as cur:
            cur.execute("UPDATE owner_stima_access SET valid_from = NOW() - INTERVAL '2 hours', "
                        "valid_until = NOW() - INTERVAL '1 hour' WHERE id=%s", (mondo["g_watch"],))
        mondo["conn"].commit()
    elif caso == "inesistente":
        bersaglio = 999999
    elif caso == "altro_owner":
        account = mondo["acc_anna"]
    elif caso == "altro_tenant":
        account = mondo["acc_carla"]

    with pytest.raises(nf()) as errore:
        modulo["home_service"].get_home(account, bersaglio)
    messaggio = str(errore.value)
    assert messaggio == "Risorsa non trovata", messaggio
    # E il rifiuto non dice nemmeno indirettamente che la stima esiste.
    for rivelatore in ("revoc", "scad", "expired", "agency", "tenant", str(bersaglio)):
        assert rivelatore.lower() not in messaggio.lower(), rivelatore


def test_7b_un_grant_attivo_apre_il_detail(mondo, modulo):
    vista = modulo["home_service"].get_home(mondo["acc_mario"], mondo["st_watch"])
    assert vista["stima_id"] == mondo["st_watch"]
    assert vista["property"]["comune"] == "Alba Adriatica"


def test_8b_le_home_revocate_spariscono_anche_dalla_lista(mondo, modulo):
    with mondo["conn"].cursor() as cur:
        cur.execute("UPDATE owner_stima_access SET access_status='revoked', revoked_at=NOW() "
                    "WHERE id=%s", (mondo["g_watch"],))
    mondo["conn"].commit()
    assert [h["stima_id"] for h in modulo["home_service"].list_homes(mondo["acc_mario"])] \
        == [mondo["st_evento"]]


# ---------------------------------------------------------------------------
# 9-10: dashboard, legacy e convivenza
# ---------------------------------------------------------------------------

def test_9_l_owner_legacy_continua_a_funzionare(mondo, modulo):
    cruscotto = modulo["home_service"].build_dashboard(mondo["acc_anna"])
    assert cruscotto["property_count"] == 1
    assert cruscotto["properties"][0]["title"] == "Villa"
    assert cruscotto["home_count"] == 0 and cruscotto["homes"] == []


def test_10_property_e_home_convivono(mondo, modulo):
    with mondo["conn"].cursor() as cur:
        cur.execute("INSERT INTO owner_property_access (owner_account_id, property_id) "
                    "VALUES (%s,%s)", (mondo["acc_mario"], mondo["prop"]))
    mondo["conn"].commit()

    cruscotto = modulo["home_service"].build_dashboard(mondo["acc_mario"])
    assert cruscotto["property_count"] == 1 and cruscotto["home_count"] == 2
    assert {h["stima_id"] for h in cruscotto["homes"]} == {mondo["st_watch"], mondo["st_evento"]}
    assert set(cruscotto) == {"properties", "property_count", "homes", "home_count"}


# ---------------------------------------------------------------------------
# 11-13: il valore iniziale, sui dati veri
# ---------------------------------------------------------------------------

def test_11_la_baseline_del_watch_e_la_fonte(mondo, modulo):
    vista = modulo["home_service"].get_home(mondo["acc_mario"], mondo["st_watch"])
    assert vista["valuation"]["initial_value"] == 185000
    assert vista["valuation"]["initial_value_source"] == "property_watch_baseline"
    assert vista["valuation"]["current_value"] is None
    assert vista["valuation"]["current_value_status"] == "history_not_available"


def test_12_senza_watch_si_ripiega_sull_evento(mondo, modulo):
    vista = modulo["home_service"].get_home(mondo["acc_mario"], mondo["st_evento"])
    assert vista["valuation"]["initial_value"] == 149000
    assert vista["valuation"]["initial_value_source"] == "seller_timeline_event"
    assert vista["watch"]["has_watch"] is False


def test_12b_l_evento_di_un_altra_agenzia_non_e_un_fallback(mondo, modulo):
    """Il fallback e' scopato: un evento con lo stesso `stima_id` ma un'altra
    agenzia non puo' diventare il valore di questa casa."""
    with mondo["conn"].cursor() as cur:
        cur.execute("UPDATE seller_timeline_events SET agency_id = %s WHERE stima_id = %s",
                    (mondo["b"], mondo["st_evento"]))
    mondo["conn"].commit()
    vista = modulo["home_service"].get_home(mondo["acc_mario"], mondo["st_evento"])
    assert vista["valuation"]["initial_value"] is None
    assert vista["data_status"] == "partial"


def test_13_senza_baseline_e_senza_evento_il_valore_e_null(mondo, modulo):
    with mondo["conn"].cursor() as cur:
        cur.execute("DELETE FROM seller_timeline_events WHERE stima_id = %s", (mondo["st_evento"],))
    mondo["conn"].commit()
    vista = modulo["home_service"].get_home(mondo["acc_mario"], mondo["st_evento"])
    assert vista["valuation"]["initial_value"] is None
    assert vista["valuation"]["initial_value_source"] is None
    assert vista["data_status"] == "partial"


# ---------------------------------------------------------------------------
# 14-16: cosa NON succede e cosa NON esce
# ---------------------------------------------------------------------------

def test_14_e_15_leggere_non_ricalcola_e_non_scrive_niente(mondo, modulo, monkeypatch):
    import valuation
    from property_watch import repository as pw_repository

    def vietato(*_a, **_k):
        raise AssertionError("LMC-2 non ricalcola e non scrive")

    monkeypatch.setattr(valuation, "compute_from_payload", vietato)
    monkeypatch.setattr(pw_repository, "ensure_watch_with_baseline", vietato)
    monkeypatch.setattr(pw_repository, "insert_observation", vietato)

    prima = (conta(mondo, "SELECT count(*) FROM property_watches"),
             conta(mondo, "SELECT count(*) FROM property_watch_observations"),
             conta(mondo, "SELECT count(*) FROM seller_timeline_events"),
             conta(mondo, "SELECT count(*) FROM stime"))

    modulo["home_service"].list_homes(mondo["acc_mario"])
    modulo["home_service"].get_home(mondo["acc_mario"], mondo["st_watch"])
    modulo["home_service"].get_home(mondo["acc_mario"], mondo["st_evento"])

    dopo = (conta(mondo, "SELECT count(*) FROM property_watches"),
            conta(mondo, "SELECT count(*) FROM property_watch_observations"),
            conta(mondo, "SELECT count(*) FROM seller_timeline_events"),
            conta(mondo, "SELECT count(*) FROM stime"))
    assert prima == dopo


def test_16_nessun_dato_privato_nel_detail(mondo, modulo):
    with mondo["conn"].cursor() as cur:
        cur.execute("INSERT INTO property_watch_observations (watch_id, observation_type, source, "
                    "payload, idempotency_key) VALUES (%s,'buyer_pressure_snapshot','internal',%s,%s)",
                    (mondo["watch"], json.dumps({"score": 82, "buy_requests": [11, 12],
                                                 "budget_reference": 210000}), "k-bp"))
        cur.execute("INSERT INTO property_watch_observations (watch_id, observation_type, source, "
                    "payload, idempotency_key) VALUES (%s,'internal_supply_snapshot','internal',%s,%s)",
                    (mondo["watch"], json.dumps({"supply_count": 9}), "k-supply"))
    mondo["conn"].commit()

    vista = modulo["home_service"].get_home(mondo["acc_mario"], mondo["st_watch"])
    testo = repr(vista).lower()
    for vietato in ("mario", "rossi", "@example.it", "333", "richiamare", "nuovo",
                    "pressure", "supply", "budget", "score", "82", "210000",
                    "1500", "note_internal", "lead_status"):
        assert vietato not in testo, vietato
    # "buyer" compare UNA volta sola, come nome della capability dichiarata
    # `buyer_demand` (che vale False): mai come dato.
    senza_capability = {k: v for k, v in vista.items() if k != "capabilities"}
    assert "buyer" not in repr(senza_capability).lower()
    assert vista["capabilities"]["buyer_demand"] is False


# ---------------------------------------------------------------------------
# 17-19: profilo, capability, superficie
# ---------------------------------------------------------------------------

def test_17_la_completezza_e_deterministica_e_spiegabile(mondo, modulo):
    vista = modulo["home_service"].get_home(mondo["acc_mario"], mondo["st_watch"])
    profilo = vista["profile"]
    totali = len(profilo["considered_fields"])
    assert profilo["completion_percent"] == round(len(profilo["known_fields"]) / totali * 100)
    assert profilo["completion_percent"] == 100, "la fixture riempie ogni campo considerato"

    with mondo["conn"].cursor() as cur:
        cur.execute("UPDATE stime SET civico=NULL, altrodescrizione='', pertinenze=NULL "
                    "WHERE id=%s", (mondo["st_watch"],))
    mondo["conn"].commit()
    dopo = modulo["home_service"].get_home(mondo["acc_mario"], mondo["st_watch"])["profile"]
    assert set(dopo["missing_fields"]) == {"civico", "altrodescrizione", "pertinenze"}
    assert dopo["completion_percent"] == round((totali - 3) / totali * 100)


def test_18_le_capability_seguono_i_dati_reali(mondo, modulo):
    """Il monitoraggio cresce, le capability no: nessuna delle osservazioni
    che PROPERTY WATCH scrive e' una rivalutazione dell'immobile."""
    tutte_false = {"valuation_history": False, "buyer_demand": False,
                   "comparables": False, "profile_update": False}

    con_watch = modulo["home_service"].get_home(mondo["acc_mario"], mondo["st_watch"])
    assert con_watch["capabilities"] == tutte_false
    assert con_watch["history"]["history_status"] == "building"
    assert con_watch["history"]["observation_count"] == 1

    with mondo["conn"].cursor() as cur:
        cur.execute("INSERT INTO property_watch_observations (watch_id, observation_type, source, "
                    "payload, idempotency_key, observed_at) VALUES "
                    "(%s,'microzone_price_changed','internal','{}',%s, NOW())",
                    (mondo["watch"], "k-micro"))
    mondo["conn"].commit()

    dopo = modulo["home_service"].get_home(mondo["acc_mario"], mondo["st_watch"])
    assert dopo["capabilities"] == tutte_false
    assert dopo["history"]["history_status"] == "building"
    assert dopo["history"]["history_available"] is False
    # Il conteggio sale, perche' l'osservazione c'e' davvero.
    assert dopo["history"]["observation_count"] == 2
    assert dopo["history"]["first_observed_at"] < dopo["history"]["last_observed_at"]
    assert dopo["valuation"]["current_value"] is None
    assert dopo["valuation"]["current_value_status"] == "history_not_available"


def test_18b_baseline_piu_buyer_pressure_piu_offerta_interna_non_fanno_storia(mondo, modulo):
    """Il caso dichiarato nel gate, sui dati veri: tre osservazioni reali,
    nessun valuation_snapshot, quindi nessuna storia del valore."""
    with mondo["conn"].cursor() as cur:
        cur.execute("INSERT INTO property_watch_observations (watch_id, observation_type, source, "
                    "payload, idempotency_key, observed_at) VALUES "
                    "(%s,'buyer_pressure_snapshot','internal',%s,'k-bp18', NOW() - INTERVAL '2 days')",
                    (mondo["watch"], json.dumps({"score": 82})))
        cur.execute("INSERT INTO property_watch_observations (watch_id, observation_type, source, "
                    "payload, idempotency_key, observed_at) VALUES "
                    "(%s,'internal_supply_snapshot','internal',%s,'k-sup18', NOW())",
                    (mondo["watch"], json.dumps({"supply_count": 9})))
    mondo["conn"].commit()

    vista = modulo["home_service"].get_home(mondo["acc_mario"], mondo["st_watch"])
    assert vista["valuation"]["current_value"] is None
    assert vista["valuation"]["current_value_status"] == "history_not_available"
    assert vista["capabilities"]["valuation_history"] is False
    assert vista["history"]["history_status"] == "building"
    # Le tre osservazioni reali risultano nel conteggio.
    assert vista["history"]["observation_count"] == 3
    assert conta(mondo, "SELECT count(*) FROM property_watch_observations WHERE watch_id = %s",
                 (mondo["watch"],)) == 3
    # E il valore ORIGINARIO non e' stato toccato.
    assert vista["valuation"]["initial_value"] == 185000
    assert vista["valuation"]["initial_value_source"] == "property_watch_baseline"


def test_19_lista_e_detail_hanno_la_stessa_idea_di_chi_puo_vedere(mondo, modulo):
    for account in (mondo["acc_mario"], mondo["acc_anna"], mondo["acc_carla"]):
        visibili = {h["stima_id"] for h in modulo["home_service"].list_homes(account)}
        for stima_id in (mondo["st_watch"], mondo["st_evento"], mondo["st_b"]):
            if stima_id in visibili:
                assert modulo["home_service"].get_home(account, stima_id)["stima_id"] == stima_id
            else:
                with pytest.raises(nf()):
                    modulo["home_service"].get_home(account, stima_id)


# ---------------------------------------------------------------------------
# L'audit dell'apertura
# ---------------------------------------------------------------------------

def test_20_aprire_il_detail_lascia_una_traccia_e_la_lista_no(mondo, modulo):
    modulo["home_service"].list_homes(mondo["acc_mario"])
    assert conta(mondo, "SELECT count(*) FROM owner_audit_log") == 0

    modulo["home_service"].get_home(mondo["acc_mario"], mondo["st_watch"])
    tracce = righe(mondo, "SELECT action, owner_account_id, property_id, entity_type, entity_id "
                          "FROM owner_audit_log ORDER BY id")
    assert tracce == [("home_viewed", mondo["acc_mario"], None, "stima", str(mondo["st_watch"]))]


def test_20b_un_rifiuto_non_scrive_un_audit_di_successo(mondo, modulo):
    with pytest.raises(nf()):
        modulo["home_service"].get_home(mondo["acc_carla"], mondo["st_watch"])
    assert conta(mondo, "SELECT count(*) FROM owner_audit_log WHERE result = 'success'") == 0
