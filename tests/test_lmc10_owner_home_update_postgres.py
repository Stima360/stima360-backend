"""LMC-10 su PostgreSQL reale: cio' che solo un database sa dire.

QUATTRO COSE CHE UN DOPPIO NON PUO' PROVARE.

I TIPI. Che la colonna `locali` di `owner_home_overrides` sia un intero
come quella di `stime` e non una stringa come quella - vicina e omonima -
di `stime_dettagliate`. Qui si legge `information_schema`, cioe' il
database vero, non la sorgente.

LA CONCORRENZA. Che due salvataggi partiti insieme dalla stessa versione
finiscano uno scritto e uno respinto, e che il respinto non abbia
cambiato niente. Con un doppio si proverebbe la propria fantasia sulla
UPDATE, non la UPDATE.

IL ROLLBACK. Che la down rifiuti davvero di cancellare una tabella con
dentro le correzioni dei proprietari, e che funzioni su una vuota.

IL MOTORE. Che lo snapshot nato dopo un aggiornamento sia calcolato sul
PROFILO EFFETTIVO, con `valuation.compute_from_payload` e con i numeri
corretti - e che gli snapshot gia' scritti restino dove sono.

Opt-in: senza `P29_TEST_DSN` si salta tutto. Database usa-e-getta.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import home_profile
from owner import tracking

DSN = os.getenv("P29_TEST_DSN")

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare LMC-10")

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

    nome = f"lmc10_probe_{os.getpid()}_{uuid.uuid4().hex[:6]}"
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
    from owner import home_service, home_update, interest_service, tracking
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
            "interest": interest_service, "update": home_update}


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
        for tabella in ("owner_home_overrides",
                        "owner_access_tokens", "owner_sessions", "owner_audit_log",
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


def override(mondo, stima_id):
    """La riga di override come dizionario. La connessione del test non usa
    un cursore a dizionario, quindi i nomi si prendono da `description`."""
    with mondo["conn"].cursor() as cur:
        cur.execute("SELECT * FROM owner_home_overrides WHERE stima_id=%s", (stima_id,))
        r = cur.fetchone()
        nomi = [c.name for c in cur.description]
    mondo["conn"].rollback()
    return dict(zip(nomi, r)) if r else None


def righe_stima(mondo, stima_id):
    """La riga di `stime` come dizionario, per chiamare il composer puro."""
    with mondo["conn"].cursor() as cur:
        cur.execute("SELECT * FROM stime WHERE id=%s", (stima_id,))
        r = cur.fetchone()
        nomi = [c.name for c in cur.description]
    mondo["conn"].rollback()
    return dict(zip(nomi, r))


def eventi(mondo, tipo=None):
    sql = "SELECT event_type, event_source, stima_id, payload, idempotency_key FROM seller_timeline_events"
    if tipo:
        sql += " WHERE event_type = %s"
        return righe(mondo, sql, (tipo,))
    return righe(mondo, sql)


def snapshots(mondo, watch_id):
    return righe(mondo, "SELECT payload, observed_at FROM property_watch_observations "
                        "WHERE watch_id=%s AND observation_type='valuation_snapshot' "
                        "ORDER BY id", (watch_id,))


# ---------------------------------------------------------------------------
# 51 - I TIPI REALI, LETTI DAL DATABASE
# ---------------------------------------------------------------------------

def test_51_i_tipi_combaciano_su_information_schema(db, mondo):
    """TEST 51 su PostgreSQL: non la sorgente, la tabella davvero creata.

    `locali` e' il campo per cui questo test esiste: in `stime` e' INTEGER,
    in `stime_dettagliate` la stessa parola e' VARCHAR(50). Un override
    tipizzato sulla tabella sbagliata accetterebbe "quattro" e il motore
    leggerebbe zero locali senza che nessuno se ne accorga.
    """
    tabella = []
    for campo in home_profile.OVERRIDABLE_FIELDS:
        mio, suo = righe(mondo, """
            SELECT
              (SELECT data_type || coalesce('(' || character_maximum_length || ')','')
                 FROM information_schema.columns
                WHERE table_name='owner_home_overrides' AND column_name=%s),
              (SELECT data_type || coalesce('(' || character_maximum_length || ')','')
                 FROM information_schema.columns
                WHERE table_name='stime' AND column_name=%s)
        """, (campo, campo))[0]
        tabella.append((campo, suo, mio))
        assert suo is not None, f"stime non ha la colonna {campo}"
        assert mio == suo, f"{campo}: stime={suo} owner_home_overrides={mio}"
    assert ("locali", "integer", "integer") in tabella, tabella


def test_51b_la_tabella_non_porta_agency_id_ne_campi_vietati(db, mondo):
    colonne = {r[0] for r in righe(mondo, "SELECT column_name FROM information_schema.columns "
                                          "WHERE table_name='owner_home_overrides'")}
    for vietato in ("agency_id", "comune", "microzona", "via", "civico", "tipologia",
                    "posizionemare", "distanzamare", "barrieramare", "vistamareyn",
                    "vistamare", "contact_id", "lead_id", "token", "prezzo_mq_base",
                    "lead_status", "note_internal", "nome", "email", "telefono"):
        assert vietato not in colonne, vietato
    assert colonne == set(home_profile.OVERRIDABLE_FIELDS) | set(home_profile.OVERRIDE_METADATA_FIELDS)


# ---------------------------------------------------------------------------
# 52 / 53 - LA DOWN PROTETTIVA
# ---------------------------------------------------------------------------

def _esegui_down(conn):
    testo = (MIGRAZIONI / "068_lmc10_owner_home_overrides_down.sql").read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(testo)


def test_52_la_down_rifiuta_con_dati_dentro(db, mondo):
    """TEST 52 - e la tabella resta intatta, con la sua riga."""
    import psycopg2
    conn = db["conn"]
    conn.rollback()
    with conn.cursor() as cur:
        cur.execute("INSERT INTO owner_home_overrides (stima_id, mq) VALUES (%s, 110)",
                    (mondo["st_a"],))
    conn.commit()

    with pytest.raises(psycopg2.errors.RaiseException) as errore:
        _esegui_down(conn)
    conn.rollback()
    assert "owner home override" in str(errore.value)
    assert override(mondo, mondo["st_a"])["mq"] == 110, "la riga e' ancora li'"
    assert righe(mondo, "SELECT to_regclass('public.owner_home_overrides')")[0][0] is not None


def test_53_la_down_funziona_su_tabella_vuota(db, mondo):
    """TEST 53 - e non tocca nient'altro."""
    conn = db["conn"]
    conn.rollback()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM owner_home_overrides")
    conn.commit()

    prima_stime = righe(mondo, "SELECT count(*) FROM stime")[0][0]
    _esegui_down(conn)
    conn.commit()
    assert righe(mondo, "SELECT to_regclass('public.owner_home_overrides')")[0][0] is None
    assert righe(mondo, "SELECT count(*) FROM stime")[0][0] == prima_stime
    assert righe(mondo, "SELECT to_regclass('public.owner_stima_access')")[0][0] is not None

    # Rimessa in piedi per i test che seguono: la down e' distruttiva per
    # definizione, e lasciarla applicata renderebbe questo file dipendente
    # dall'ordine.
    with conn.cursor() as cur:
        cur.execute((MIGRAZIONI / "068_lmc10_owner_home_overrides.sql").read_text(encoding="utf-8"))
    conn.commit()


# ---------------------------------------------------------------------------
# 54..59 - PROFILE_VERSION E CONCORRENZA OTTIMISTICA
# ---------------------------------------------------------------------------

def test_54_profile_version_zero_senza_override(db, mondo, modulo):
    vista = modulo["home_service"].get_home(mondo["acc"], mondo["st_a"])
    assert vista["profile_version"] == 0
    assert vista["profile"]["version"] == 0


def test_55_la_prima_modifica_con_expected_version_zero(db, mondo, modulo):
    """TEST 55."""
    esito = modulo["update"].update_home(mondo["acc"], mondo["st_a"], {"mq": 110}, 0)
    assert esito["status"] == "updated"
    assert esito["profile_version"] == 1
    assert esito["updated_fields"] == ["mq"]
    riga = override(mondo, mondo["st_a"])
    assert riga["mq"] == 110 and riga["version"] == 1
    assert riga["updated_by_owner_account_id"] == mondo["acc"]
    # E la stima originale non e' cambiata.
    assert righe(mondo, "SELECT mq FROM stime WHERE id=%s", (mondo["st_a"],))[0][0] == 95


def test_56_la_seconda_modifica_porta_alla_versione_due(db, mondo, modulo):
    """TEST 56."""
    modulo["update"].update_home(mondo["acc"], mondo["st_a"], {"mq": 110}, 0)
    esito = modulo["update"].update_home(mondo["acc"], mondo["st_a"], {"bagni": 3}, 1)
    assert esito["status"] == "updated"
    assert esito["profile_version"] == 2
    riga = override(mondo, mondo["st_a"])
    assert riga["mq"] == 110, "la correzione precedente non si perde"
    assert riga["bagni"] == 3


def test_57_una_versione_vecchia_e_un_conflitto(db, mondo, modulo):
    """TEST 57."""
    modulo["update"].update_home(mondo["acc"], mondo["st_a"], {"mq": 110}, 0)
    with pytest.raises(modulo["update"].HomeVersionConflict):
        modulo["update"].update_home(mondo["acc"], mondo["st_a"], {"mq": 130}, 0)


def test_58_la_richiesta_stantia_non_cambia_niente(db, mondo, modulo):
    """TEST 58 - nessuna sovrascrittura silenziosa, e nemmeno rumorosa."""
    modulo["update"].update_home(mondo["acc"], mondo["st_a"], {"mq": 110}, 0)
    prima = override(mondo, mondo["st_a"])
    with pytest.raises(modulo["update"].HomeVersionConflict):
        modulo["update"].update_home(mondo["acc"], mondo["st_a"], {"mq": 999}, 0)
    dopo = override(mondo, mondo["st_a"])
    assert dopo["mq"] == 110 == prima["mq"]
    assert dopo["version"] == prima["version"]
    assert dopo["updated_at"] == prima["updated_at"]


def test_59_due_proprietari_insieme_uno_vince_e_l_altro_riceve_409(db, mondo, modulo):
    """TEST 59 - due comproprietari, la stessa versione di partenza.

    `owner_stima_access.access_role` ammette `co_owner`: non e' un caso di
    laboratorio. Entrambi hanno letto `profile_version = 0`; la tabella ha
    UNIQUE su `stima_id`, quindi la seconda INSERT non crea una riga
    parallela - diventa un conflitto.
    """
    conn = db["conn"]
    conn.rollback()
    with conn.cursor() as cur:
        cur.execute("INSERT INTO contacts (agency_id, display_name, email, email_normalized) "
                    "VALUES (%s,'Lucia Rossi','lucia@example.it','lucia@example.it') RETURNING id",
                    (mondo["a"],))
        k2 = cur.fetchone()[0]
        cur.execute("INSERT INTO owner_accounts (contact_id,status) VALUES (%s,'active') RETURNING id", (k2,))
        acc2 = cur.fetchone()[0]
        cur.execute("INSERT INTO owner_stima_access (owner_account_id,stima_id,access_role,granted_by) "
                    "VALUES (%s,%s,'co_owner','TEST')", (acc2, mondo["st_a"]))
    conn.commit()

    primo = modulo["update"].update_home(mondo["acc"], mondo["st_a"], {"mq": 110}, 0)
    assert primo["status"] == "updated"
    with pytest.raises(modulo["update"].HomeVersionConflict):
        modulo["update"].update_home(acc2, mondo["st_a"], {"mq": 120}, 0)
    assert override(mondo, mondo["st_a"])["mq"] == 110
    assert righe(mondo, "SELECT count(*) FROM owner_home_overrides WHERE stima_id=%s",
                 (mondo["st_a"],))[0][0] == 1


# ---------------------------------------------------------------------------
# 60..64 - IL NO-OP
# ---------------------------------------------------------------------------

def test_60_valori_identici_danno_unchanged(db, mondo, modulo):
    """TEST 60."""
    esito = modulo["update"].update_home(
        mondo["acc"], mondo["st_a"],
        {"mq": 95, "locali": 4, "bagni": 2, "ascensore": True, "stato": "buono"}, 0)
    assert esito["status"] == "unchanged"
    assert esito["updated_fields"] == []
    assert esito["value_recalculated"] is False


def test_61_il_no_op_non_crea_la_riga_ne_incrementa_la_versione(db, mondo, modulo):
    """TEST 61."""
    modulo["update"].update_home(mondo["acc"], mondo["st_a"], {"mq": 95}, 0)
    assert override(mondo, mondo["st_a"]) is None, "nessuna riga per un nulla di fatto"

    modulo["update"].update_home(mondo["acc"], mondo["st_a"], {"mq": 110}, 0)
    modulo["update"].update_home(mondo["acc"], mondo["st_a"], {"mq": 110}, 1)
    assert override(mondo, mondo["st_a"])["version"] == 1


def test_62_il_no_op_non_cambia_updated_at(db, mondo, modulo):
    """TEST 62."""
    modulo["update"].update_home(mondo["acc"], mondo["st_a"], {"mq": 110}, 0)
    prima = override(mondo, mondo["st_a"])["updated_at"]
    esito = modulo["update"].update_home(mondo["acc"], mondo["st_a"], {"mq": 110}, 1)
    assert esito["status"] == "unchanged"
    assert override(mondo, mondo["st_a"])["updated_at"] == prima


def test_63_il_no_op_non_crea_eventi(db, mondo, modulo):
    """TEST 63."""
    modulo["update"].update_home(mondo["acc"], mondo["st_a"], {"mq": 95}, 0)
    assert eventi(mondo, tracking.HOME_UPDATED) == []


def test_64_il_no_op_non_crea_snapshot(db, mondo, modulo):
    """TEST 64."""
    modulo["update"].update_home(mondo["acc"], mondo["st_a"], {"mq": 95}, 0)
    assert snapshots(mondo, mondo["w_a"]) == []


def test_64b_ascensore_scritto_diversamente_resta_un_no_op(db, mondo, modulo):
    """"True" in tabella, `True` dal client: la stessa cosa. Contarla come
    modifica farebbe nascere una versione, un evento e forse uno snapshot
    per una differenza di maiuscola."""
    esito = modulo["update"].update_home(mondo["acc"], mondo["st_a"], {"ascensore": True}, 0)
    assert esito["status"] == "unchanged"
    assert override(mondo, mondo["st_a"]) is None


def test_64c_le_pertinenze_riordinate_restano_un_no_op(db, mondo, modulo):
    esito = modulo["update"].update_home(
        mondo["acc"], mondo["st_a"], {"pertinenze": ["garage"]}, 0)
    assert esito["status"] == "unchanged", esito


# ---------------------------------------------------------------------------
# 67 - IL MOTORE LEGGE IL PROFILO EFFETTIVO
# ---------------------------------------------------------------------------

def test_67_lo_snapshot_usa_il_profilo_effettivo(db, mondo, modulo):
    """TEST 67 - e lo si verifica contro il motore, non contro un numero
    scritto a mano nel test."""
    import valuation

    esito = modulo["update"].update_home(mondo["acc"], mondo["st_a"], {"mq": 140}, 0)
    assert esito["status"] == "updated"
    assert esito["value_recalculated"] is True, esito

    punti = snapshots(mondo, mondo["w_a"])
    assert len(punti) == 1
    payload = punti[0][0]
    atteso = valuation.compute_from_payload({
        "comune": "Alba Adriatica", "microzona": "Villa Fiore",
        "tipologia": "Appartamento", "mq": 140, "piano": "3", "locali": 4,
        "bagni": 2, "ascensore": "Sì", "anno": 1998, "stato": "buono",
        "posizioneMare": "fronte", "distanzaMare": "0-100", "barrieraMare": "no",
        "vistaMareYN": "si", "vistaMareDettaglio": "frontale", "vistaMare": "mare",
        "pertinenze": "garage", "mqGiardino": 0, "mqGarage": 18, "mqCantina": 6,
        "mqPostoAuto": 0, "mqTaverna": 0, "mqSoffitta": 0, "mqTerrazzo": 12,
        "numBalconi": 2, "via": "Via Trieste", "altroDescrizione": "ristrutturato"})
    assert payload["price_exact"] == atteso["price_exact"], (payload, atteso)
    assert payload["reason"] == modulo["update"].REFRESH_REASON


def test_67b_gli_snapshot_gia_scritti_non_si_ricalcolano(db, mondo, modulo):
    """L'unica cosa che puo' succedere a uno snapshot e' che ne nasca un
    altro accanto."""
    conn = db["conn"]
    conn.rollback()
    with conn.cursor() as cur:
        cur.execute("INSERT INTO property_watch_observations (watch_id,observation_type,source,"
                    "payload,idempotency_key,observed_at) VALUES (%s,'valuation_snapshot','internal',"
                    "%s,%s, NOW() - INTERVAL '10 days')",
                    (mondo["w_a"], json.dumps({"price_exact": 185000, "computed_at":
                                               "2026-09-10T08:00:00+00:00"}), "vecchio-1"))
    conn.commit()
    prima = snapshots(mondo, mondo["w_a"])

    modulo["update"].update_home(mondo["acc"], mondo["st_a"], {"mq": 140}, 0)
    dopo = snapshots(mondo, mondo["w_a"])
    assert len(dopo) == len(prima) + 1
    assert dopo[0][0] == prima[0][0], "il punto storico e' identico"
    assert dopo[0][0]["price_exact"] == 185000


def test_67c_la_baseline_resta_immutabile(db, mondo, modulo):
    prima = righe(mondo, "SELECT payload FROM property_watch_observations "
                         "WHERE watch_id=%s AND observation_type='watch_started'", (mondo["w_a"],))
    modulo["update"].update_home(mondo["acc"], mondo["st_a"], {"mq": 140}, 0)
    dopo = righe(mondo, "SELECT payload FROM property_watch_observations "
                        "WHERE watch_id=%s AND observation_type='watch_started'", (mondo["w_a"],))
    assert dopo == prima


def test_67d_il_valore_iniziale_mostrato_resta_quello_di_allora(db, mondo, modulo):
    prima = modulo["home_service"].get_home(mondo["acc"], mondo["st_a"])["valuation"]["initial_value"]
    modulo["update"].update_home(mondo["acc"], mondo["st_a"], {"mq": 140}, 0)
    dopo = modulo["home_service"].get_home(mondo["acc"], mondo["st_a"])["valuation"]["initial_value"]
    assert dopo == prima == 185000


# ---------------------------------------------------------------------------
# 68 / 69 - I DUE FALLIMENTI CHE NON DEVONO PORTARSI VIA L'AGGIORNAMENTO
# ---------------------------------------------------------------------------

def test_68_un_guasto_del_ricalcolo_non_perde_l_override(db, mondo, modulo, monkeypatch):
    """TEST 68 - e la risposta non afferma che il valore e' stato ricalcolato."""
    from property_watch import service as pw_service

    def esplode(*a, **k):
        raise RuntimeError("motore giu'")

    monkeypatch.setattr(pw_service, "refresh_valuation_snapshot_scoped", esplode)
    esito = modulo["update"].update_home(mondo["acc"], mondo["st_a"], {"mq": 140}, 0)
    assert esito["status"] == "updated"
    assert esito["value_recalculated"] is False
    assert esito["valuation_status"] == modulo["update"].VALUATION_UNAVAILABLE
    assert override(mondo, mondo["st_a"])["mq"] == 140


def test_68b_una_casa_senza_watch_si_aggiorna_lo_stesso(db, mondo, modulo):
    """Il caso piu' comune, e non e' un guasto: LMC-3 pretende il watch e
    non lo crea. L'aggiornamento resta salvato, e non si annuncia nessun
    valore nuovo."""
    conn = db["conn"]
    conn.rollback()
    with conn.cursor() as cur:
        cur.execute("INSERT INTO owner_stima_access (owner_account_id,stima_id,granted_by) "
                    "VALUES (%s,%s,'TEST')", (mondo["acc"], mondo["st_senza_watch"]))
    conn.commit()
    esito = modulo["update"].update_home(mondo["acc"], mondo["st_senza_watch"], {"mq": 140}, 0)
    assert esito["status"] == "updated"
    assert esito["value_recalculated"] is False
    assert override(mondo, mondo["st_senza_watch"])["mq"] == 140


def test_69_un_guasto_di_seller_intelligence_non_perde_l_override(db, mondo, modulo, monkeypatch):
    """TEST 69 - si perde il SEGNALE, non il dato. E' la differenza con
    LMC-9, dove l'evento era la richiesta e perderlo era perderla."""
    from seller_intelligence import service as si_service

    def esplode(*a, **k):
        raise RuntimeError("seller intelligence giu'")

    monkeypatch.setattr(si_service, "record_event_scoped", esplode)
    esito = modulo["update"].update_home(mondo["acc"], mondo["st_a"], {"mq": 140}, 0)
    assert esito["status"] == "updated"
    assert override(mondo, mondo["st_a"])["mq"] == 140
    assert eventi(mondo, tracking.HOME_UPDATED) == []


# ---------------------------------------------------------------------------
# L'EVENTO, LA TENANCY E IL 404 NEUTRO
# ---------------------------------------------------------------------------

def test_70a_l_evento_nasce_dopo_una_modifica_vera(db, mondo, modulo):
    modulo["update"].update_home(mondo["acc"], mondo["st_a"], {"mq": 140}, 0)
    scritti = eventi(mondo, tracking.HOME_UPDATED)
    assert len(scritti) == 1
    tipo, sorgente, stima_id, payload, chiave = scritti[0]
    assert (tipo, sorgente, stima_id) == ("owner_home_updated", "owner_portal", mondo["st_a"])
    assert payload == {"action": "home_updated"}
    assert chiave.endswith(f"day:{datetime.now(timezone.utc).date().isoformat()}")


def test_70b_due_aggiornamenti_nello_stesso_giorno_un_evento_solo(db, mondo, modulo):
    modulo["update"].update_home(mondo["acc"], mondo["st_a"], {"mq": 140}, 0)
    modulo["update"].update_home(mondo["acc"], mondo["st_a"], {"bagni": 3}, 1)
    assert len(eventi(mondo, tracking.HOME_UPDATED)) == 1
    assert override(mondo, mondo["st_a"])["version"] == 2, "ma la versione avanza"


def test_70c_il_radar_vede_l_aggiornamento(db, mondo, modulo):
    modulo["update"].update_home(mondo["acc"], mondo["st_a"], {"mq": 140}, 0)
    radar = modulo["interest"].interest_for_stima_scoped(Scope(mondo["a"]), mondo["st_a"])["interest"]
    assert radar["home_updated"] is True
    assert modulo["interest"].HOME_UPDATED_REASON in radar["reasons"]
    assert radar["level"] in ("medium", "high")


def test_70d_la_richiesta_lmc9_resta_high_dopo_l_aggiornamento(db, mondo, modulo):
    """TEST 70 su dati reali."""
    modulo["tracking"].track_consultation_request(mondo["acc"], mondo["st_a"])
    modulo["update"].update_home(mondo["acc"], mondo["st_a"], {"mq": 140}, 0)
    radar = modulo["interest"].interest_for_stima_scoped(Scope(mondo["a"]), mondo["st_a"])["interest"]
    assert radar["level"] == "high"
    assert radar["consultation_requested"] is True
    assert radar["reasons"][0] == modulo["interest"].CONSULTATION_REASON


def test_t1_la_casa_di_un_altro_tenant_non_esiste(db, mondo, modulo):
    from core.exceptions import NotFoundError
    with pytest.raises(NotFoundError):
        modulo["update"].update_home(mondo["acc"], mondo["st_b"], {"mq": 140}, 0)
    assert override(mondo, mondo["st_b"]) is None


def test_t2_una_stima_inesistente_da_lo_stesso_errore(db, mondo, modulo):
    from core.exceptions import NotFoundError
    with pytest.raises(NotFoundError):
        modulo["update"].update_home(mondo["acc"], 99999, {"mq": 140}, 0)


def test_t3_un_grant_revocato_non_scrive_piu(db, mondo, modulo):
    from core.exceptions import NotFoundError
    conn = db["conn"]
    conn.rollback()
    with conn.cursor() as cur:
        cur.execute("UPDATE owner_stima_access SET access_status='revoked', revoked_at=NOW() "
                    "WHERE owner_account_id=%s AND stima_id=%s", (mondo["acc"], mondo["st_a"]))
    conn.commit()
    with pytest.raises(NotFoundError):
        modulo["update"].update_home(mondo["acc"], mondo["st_a"], {"mq": 140}, 0)
    assert override(mondo, mondo["st_a"]) is None


def test_t4_un_grant_scaduto_non_scrive_piu(db, mondo, modulo):
    from core.exceptions import NotFoundError
    conn = db["conn"]
    conn.rollback()
    with conn.cursor() as cur:
        cur.execute("UPDATE owner_stima_access SET valid_from=NOW() - INTERVAL '10 days', "
                    "valid_until=NOW() - INTERVAL '1 day' "
                    "WHERE owner_account_id=%s AND stima_id=%s", (mondo["acc"], mondo["st_a"]))
    conn.commit()
    with pytest.raises(NotFoundError):
        modulo["update"].update_home(mondo["acc"], mondo["st_a"], {"mq": 140}, 0)


def test_t5_il_trigger_del_database_rifiuta_il_cross_tenant(db, mondo):
    """L'ultima rete, sotto l'applicazione: anche una INSERT diretta con un
    account dell'agenzia sbagliata viene respinta."""
    import psycopg2
    conn = db["conn"]
    conn.rollback()
    with conn.cursor() as cur:
        cur.execute("INSERT INTO contacts (agency_id, display_name) VALUES (%s,'Altro') RETURNING id",
                    (mondo["b"],))
        kb = cur.fetchone()[0]
        cur.execute("INSERT INTO owner_accounts (contact_id,status) VALUES (%s,'active') RETURNING id", (kb,))
        accb = cur.fetchone()[0]
    conn.commit()
    with pytest.raises(psycopg2.errors.RaiseException) as errore:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO owner_home_overrides (stima_id,mq,updated_by_owner_account_id) "
                        "VALUES (%s,110,%s)", (mondo["st_a"], accb))
    conn.rollback()
    assert "tenancy" in str(errore.value)


# ---------------------------------------------------------------------------
# IL GIRO COMPLETO: la casa che il proprietario rilegge, e quella che vede il CRM
# ---------------------------------------------------------------------------

def test_z1_il_portale_mostra_subito_il_profilo_effettivo(db, mondo, modulo):
    modulo["update"].update_home(mondo["acc"], mondo["st_a"], {"mq": 140, "bagni": 3}, 0)
    vista = modulo["home_service"].get_home(mondo["acc"], mondo["st_a"])
    assert vista["property"]["mq"] == 140
    assert vista["property"]["bagni"] == 3
    assert vista["profile_version"] == 1
    assert sorted(vista["profile"]["overridden_fields"]) == ["bagni", "mq"]


def test_z2_anche_la_lista_mostra_il_profilo_effettivo(db, mondo, modulo):
    """La lista mostra `mq`: se leggesse solo l'originale, il riepilogo
    direbbe 95 e il dettaglio 140 della stessa casa."""
    modulo["update"].update_home(mondo["acc"], mondo["st_a"], {"mq": 140}, 0)
    case = {c["stima_id"]: c for c in modulo["home_service"].list_homes(mondo["acc"])}
    assert case[mondo["st_a"]]["mq"] == 140


def test_z3_il_crm_eredita_il_profilo_effettivo(db, mondo, modulo):
    from owner import crm_radar
    modulo["update"].update_home(mondo["acc"], mondo["st_a"], {"mq": 140}, 0)
    blocco = crm_radar.contact_homes_block(Scope(mondo["a"]), mondo["contact"])
    casa = [h for h in blocco["homes"] if h["stima_id"] == mondo["st_a"]][0]
    assert casa["mq"] == 140
    assert casa["interest"]["home_updated"] is True
    assert modulo["interest"].HOME_UPDATED_REASON in casa["interest"]["reasons"]


def test_z4_la_risposta_dell_update_porta_la_casa_aggiornata(db, mondo, modulo):
    esito = modulo["update"].update_home(mondo["acc"], mondo["st_a"], {"mq": 140}, 0)
    assert esito["home"]["property"]["mq"] == 140
    assert esito["home"]["profile_version"] == 1


def test_z5_l_aggiornamento_non_conta_come_visita(db, mondo, modulo):
    """Chi salva non e' "tornato a guardare": `compose_home` non traccia."""
    modulo["update"].update_home(mondo["acc"], mondo["st_a"], {"mq": 140}, 0)
    assert eventi(mondo, "owner_home_viewed") == []
    assert righe(mondo, "SELECT count(*) FROM owner_audit_log WHERE action='home_viewed'")[0][0] == 0


# ---------------------------------------------------------------------------
# LA ROTTA, ESEGUITA. Quattro esiti, e ognuno dice una cosa diversa.
#
# Il servizio si prova sopra; qui si prova che la rotta traduca ogni
# rifiuto nel codice giusto - perche' un 404 dove serviva un 409 manderebbe
# il proprietario a cercare un problema che non ha, e un 200 dove serviva
# un 503 gli farebbe credere salvato cio' che non lo e'.
# ---------------------------------------------------------------------------

@pytest.fixture
def client(db, modulo, mondo):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from owner.dependencies import current_owner
    from owner.router_portal import router as portal_router

    app = FastAPI()
    app.include_router(portal_router)
    stato = {"account": mondo["acc"]}
    app.dependency_overrides[current_owner] = lambda: {"owner_account_id": stato["account"]}
    cliente = TestClient(app, raise_server_exceptions=False)
    cliente.owner = stato
    return cliente


def patch(client, stima_id, corpo):
    return client.patch(f"/api/owner/portal/homes/{stima_id}", json=corpo)


def test_r1_la_patch_valida_risponde_200_e_aggiorna(mondo, modulo, client):
    risposta = patch(client, mondo["st_a"], {"expected_version": 0, "mq": 140})
    assert risposta.status_code == 200, risposta.text
    corpo = risposta.json()
    assert corpo["status"] == "updated"
    assert corpo["profile_version"] == 1
    assert corpo["updated_fields"] == ["mq"]
    assert corpo["home"]["property"]["mq"] == 140
    assert override(mondo, mondo["st_a"])["mq"] == 140


def test_r2_una_casa_di_un_altro_tenant_da_404_neutro(mondo, client):
    risposta = patch(client, mondo["st_b"], {"expected_version": 0, "mq": 140})
    assert risposta.status_code == 404
    assert risposta.json()["detail"] == "Risorsa non trovata"
    assert override(mondo, mondo["st_b"]) is None


def test_r3_una_stima_inesistente_da_la_stessa_risposta(mondo, client):
    risposta = patch(client, 99999, {"expected_version": 0, "mq": 140})
    assert risposta.status_code == 404
    assert risposta.json()["detail"] == "Risorsa non trovata"


def test_r4_una_versione_vecchia_da_409_con_una_frase_leggibile(mondo, client):
    assert patch(client, mondo["st_a"], {"expected_version": 0, "mq": 140}).status_code == 200
    risposta = patch(client, mondo["st_a"], {"expected_version": 0, "mq": 150})
    assert risposta.status_code == 409
    testo = risposta.json()["detail"]
    assert "Ricarica" in testo
    # Nessun dettaglio tecnico nella frase che legge il proprietario.
    for vietato in ("version", "SQL", "UPDATE", "owner_home_overrides", "409"):
        assert vietato not in testo, vietato
    assert override(mondo, mondo["st_a"])["mq"] == 140


def test_r5_un_campo_fuori_whitelist_da_422_senza_toccare_niente(mondo, client):
    for campo, valore in (("comune", "Milano"), ("microzona", "Brera"),
                          ("tipologia", "Villa"), ("agency_id", 2),
                          ("contact_id", 5), ("prezzo_mq_base", 9999)):
        risposta = patch(client, mondo["st_a"], {"expected_version": 0, campo: valore})
        assert risposta.status_code == 422, (campo, risposta.status_code)
    assert override(mondo, mondo["st_a"]) is None
    assert righe(mondo, "SELECT comune FROM stime WHERE id=%s",
                 (mondo["st_a"],))[0][0] == "Alba Adriatica"


def test_r6_un_valore_non_valido_da_422_e_nomina_il_campo(mondo, client):
    risposta = patch(client, mondo["st_a"], {"expected_version": 0, "stato": "abitabile"})
    assert risposta.status_code == 422
    dettaglio = risposta.json()["detail"]
    assert dettaglio["fields"] == ["stato"], dettaglio


def test_r7_il_no_op_risponde_200_unchanged(mondo, client):
    risposta = patch(client, mondo["st_a"], {"expected_version": 0, "mq": 95})
    assert risposta.status_code == 200
    corpo = risposta.json()
    assert corpo["status"] == "unchanged"
    assert corpo["profile_version"] == 0
    assert override(mondo, mondo["st_a"]) is None


def test_r8_un_guasto_non_diventa_un_successo(mondo, client, monkeypatch):
    """La lezione di LMC-9, applicata a una scrittura: se non abbiamo
    salvato, non si risponde 200 - e nemmeno 404, che vorrebbe dire "non e'
    tua"."""
    from owner import home_update as modulo_update

    def esplode(*a, **k):
        raise RuntimeError("database giu'")

    monkeypatch.setattr(modulo_update.owner_repository, "upsert_home_override", esplode)
    risposta = patch(client, mondo["st_a"], {"expected_version": 0, "mq": 140})
    assert risposta.status_code == 503, risposta.text
    assert "Riprova" in risposta.json()["detail"]
    assert override(mondo, mondo["st_a"]) is None


def test_r9_expected_version_e_obbligatoria(mondo, client):
    risposta = patch(client, mondo["st_a"], {"mq": 140})
    assert risposta.status_code == 422
    assert override(mondo, mondo["st_a"]) is None


def test_r10_i_tipi_strani_non_passano_dallo_schema(mondo, client):
    for corpo in ({"expected_version": 0, "mq": "110"},
                  {"expected_version": 0, "mq": True},
                  {"expected_version": "0", "mq": 110},
                  {"expected_version": -1, "mq": 110},
                  {"expected_version": 0, "pertinenze": "garage"},
                  {"expected_version": 0, "ascensore": "si"}):
        risposta = patch(client, mondo["st_a"], corpo)
        assert risposta.status_code == 422, (corpo, risposta.status_code)
    assert override(mondo, mondo["st_a"]) is None


def test_r11_la_rotta_non_accetta_un_agency_id_dal_client(mondo, client):
    """Nemmeno insieme a un campo lecito, e nemmeno se fosse quello giusto."""
    risposta = patch(client, mondo["st_a"],
                     {"expected_version": 0, "mq": 140, "agency_id": mondo["a"]})
    assert risposta.status_code == 422
    assert override(mondo, mondo["st_a"]) is None


def test_r12_il_giro_completo_dalla_rotta_al_radar(mondo, modulo, client):
    """Una PATCH vera, e tutto cio' che deve essere successo dopo."""
    risposta = patch(client, mondo["st_a"],
                     {"expected_version": 0, "mq": 140, "bagni": 3})
    assert risposta.status_code == 200
    assert risposta.json()["value_recalculated"] is True

    assert override(mondo, mondo["st_a"])["bagni"] == 3
    assert len(snapshots(mondo, mondo["w_a"])) == 1
    assert len(eventi(mondo, tracking.HOME_UPDATED)) == 1
    radar = modulo["interest"].interest_for_stima_scoped(
        Scope(mondo["a"]), mondo["st_a"])["interest"]
    assert radar["home_updated"] is True
    # E la stima originale non e' cambiata di una virgola.
    assert righe(mondo, "SELECT mq, bagni FROM stime WHERE id=%s",
                 (mondo["st_a"],))[0] == (95, 2)


def test_59b_il_predicato_di_versione_vive_anche_nella_query(db, mondo, modulo):
    """La seconda rete, provata per conto suo.

    Il servizio confronta `expected_version` prima di scrivere, e nei test
    57-59 e' lui a fermare tutto: la UPDATE non viene nemmeno tentata.
    Quel controllo pero' legge la versione un istante prima della
    scrittura, e in quell'istante un'altra transazione puo' scrivere - e'
    esattamente la finestra per cui la concorrenza ottimistica esiste.
    Percio' il predicato `AND version = %s` sta anche nella query, e qui lo
    si chiama direttamente per dimostrare che c'e' e che morde: senza
    questo test la sua rimozione non farebbe fallire niente.
    """
    modulo["update"].update_home(mondo["acc"], mondo["st_a"], {"mq": 110}, 0)
    assert override(mondo, mondo["st_a"])["version"] == 1

    repo = modulo["owner_repository"]
    assert repo.upsert_home_override(mondo["acc"], mondo["st_a"], {"mq": 999}, 0) is None
    assert override(mondo, mondo["st_a"])["mq"] == 110

    # E con la versione giusta scrive davvero: il predicato non blocca tutto.
    riga = repo.upsert_home_override(mondo["acc"], mondo["st_a"], {"mq": 120}, 1)
    assert riga["mq"] == 120 and riga["version"] == 2


def test_59c_la_prima_modifica_concorrente_non_crea_due_righe(db, mondo, modulo):
    """L'INSERT ha la propria rete: `ON CONFLICT (stima_id) DO NOTHING`.

    Due "prime modifiche" partite insieme leggono entrambe
    `profile_version = 0` e arrivano entrambe all'INSERT; la seconda trova
    la chiave gia' usata e torna `None`, cioe' il 409. Anche questa e' una
    rete che il controllo di servizio nasconde, e che senza una chiamata
    diretta nessun test toccherebbe.
    """
    repo = modulo["owner_repository"]
    prima = repo.upsert_home_override(mondo["acc"], mondo["st_a"], {"mq": 110}, 0)
    assert prima["version"] == 1
    assert repo.upsert_home_override(mondo["acc"], mondo["st_a"], {"mq": 120}, 0) is None
    assert righe(mondo, "SELECT count(*) FROM owner_home_overrides WHERE stima_id=%s",
                 (mondo["st_a"],))[0][0] == 1


# ---------------------------------------------------------------------------
# "QUESTA CASA NON HA NESSUNA PERTINENZA"
#
# La correzione piu' difficile da fare e la piu' facile da sbagliare: togliere
# l'ULTIMA pertinenza. Se la stringa vuota non significasse niente, il profilo
# effettivo tornerebbe a mostrare il garage dell'originale e il proprietario
# vedrebbe la propria correzione sparire - o, peggio, non la vedrebbe sparire
# subito ma alla prossima apertura della pagina.
#
# Qui si prova il giro intero, dal token al prezzo: cio' che il database
# conserva, cio' che il proprietario rilegge, cio' che il motore calcola, e
# cio' che NON succede la seconda volta.
# ---------------------------------------------------------------------------

def test_p1_la_lista_vuota_e_accettata(db, mondo, modulo):
    """TEST 1 - l'originale ha un garage, il proprietario dice che non c'e'."""
    assert righe(mondo, "SELECT pertinenze, mqgarage FROM stime WHERE id=%s",
                 (mondo["st_a"],))[0] == ("garage", 18)
    esito = modulo["update"].update_home(mondo["acc"], mondo["st_a"],
                                         {"pertinenze": []}, 0)
    assert esito["status"] == "updated", esito
    assert esito["updated_fields"] == ["pertinenze"]


def test_p2_in_tabella_la_colonna_e_stringa_vuota_non_null(db, mondo, modulo):
    """TEST 2 - la differenza fra "nessun override" e "nessuna pertinenza"
    e' scritta nella colonna, non dedotta."""
    modulo["update"].update_home(mondo["acc"], mondo["st_a"], {"pertinenze": []}, 0)
    riga = override(mondo, mondo["st_a"])
    assert riga["pertinenze"] == "", repr(riga["pertinenze"])
    assert riga["pertinenze"] is not None
    # Gli altri campi, non toccati, restano NULL: la semantica cambia solo qui.
    assert riga["mq"] is None and riga["altrodescrizione"] is None


def test_p3_il_profilo_effettivo_non_recupera_il_garage(db, mondo, modulo):
    """TEST 3."""
    modulo["update"].update_home(mondo["acc"], mondo["st_a"], {"pertinenze": []}, 0)
    profilo = home_profile.build_effective_home_profile(
        righe_stima(mondo, mondo["st_a"]),
        modulo["owner_repository"].home_override(mondo["a"], mondo["st_a"]))
    assert profilo["home"]["pertinenze"] == ""
    assert "pertinenze" in profilo["overridden_fields"]


def test_p4_il_portale_mostra_nessuna_pertinenza(db, mondo, modulo):
    """TEST 4."""
    modulo["update"].update_home(mondo["acc"], mondo["st_a"], {"pertinenze": []}, 0)
    vista = modulo["home_service"].get_home(mondo["acc"], mondo["st_a"])
    assert vista["property"]["pertinenze"] == ""
    assert "pertinenze" in vista["profile"]["overridden_fields"]
    # E la stima originale non e' cambiata.
    assert righe(mondo, "SELECT pertinenze FROM stime WHERE id=%s",
                 (mondo["st_a"],))[0][0] == "garage"


def test_p6_lo_snapshot_non_valorizza_piu_il_garage(db, mondo, modulo):
    """TEST 6 - end-to-end, e verificato CONTRO IL MOTORE.

    `mqgarage` resta 18 in tabella: e' un dato descrittivo e nessuno lo
    cancella. Ma `valore_pertinenze` guarda prima il FLAG `Garage`, che
    nasce dai token, e senza quel flag i metri quadri non producono valore -
    comportamento gia' esistente del motore, che qui riceve semplicemente il
    profilo effettivo giusto. `valuation.py` non e' stato toccato.
    """
    import valuation

    base = {"comune": "Alba Adriatica", "microzona": "Villa Fiore",
            "tipologia": "Appartamento", "mq": 95, "piano": "3", "locali": 4,
            "bagni": 2, "ascensore": "Sì", "anno": 1998, "stato": "buono",
            "posizioneMare": "fronte", "distanzaMare": "0-100",
            "barrieraMare": "no", "vistaMareYN": "si",
            "vistaMareDettaglio": "frontale", "vistaMare": "mare",
            "mqGiardino": 0, "mqGarage": 18, "mqCantina": 6, "mqPostoAuto": 0,
            "mqTaverna": 0, "mqSoffitta": 0, "mqTerrazzo": 12, "numBalconi": 2,
            "via": "Via Trieste", "altroDescrizione": "ristrutturato"}
    con_garage = valuation.compute_from_payload({**base, "pertinenze": "garage"})
    senza = valuation.compute_from_payload({**base, "pertinenze": ""})
    assert senza["price_exact"] < con_garage["price_exact"], (senza, con_garage)

    esito = modulo["update"].update_home(mondo["acc"], mondo["st_a"],
                                         {"pertinenze": []}, 0)
    assert esito["value_recalculated"] is True, esito
    punti = snapshots(mondo, mondo["w_a"])
    assert len(punti) == 1
    assert punti[0][0]["price_exact"] == senza["price_exact"], (punti[0][0], senza)
    # `mqgarage` e' ancora li', intatto: non e' stato cancellato per far
    # tornare il conto.
    assert righe(mondo, "SELECT mqgarage FROM stime WHERE id=%s",
                 (mondo["st_a"],))[0][0] == 18


def test_p7_la_baseline_resta_invariata(db, mondo, modulo):
    """TEST 7."""
    prima = righe(mondo, "SELECT payload, observed_at FROM property_watch_observations "
                         "WHERE watch_id=%s AND observation_type='watch_started'",
                  (mondo["w_a"],))
    modulo["update"].update_home(mondo["acc"], mondo["st_a"], {"pertinenze": []}, 0)
    assert righe(mondo, "SELECT payload, observed_at FROM property_watch_observations "
                        "WHERE watch_id=%s AND observation_type='watch_started'",
                 (mondo["w_a"],)) == prima


def test_p8_i_vecchi_snapshot_restano_invariati(db, mondo, modulo):
    """TEST 8."""
    conn = db["conn"]
    conn.rollback()
    with conn.cursor() as cur:
        cur.execute("INSERT INTO property_watch_observations (watch_id,observation_type,source,"
                    "payload,idempotency_key,observed_at) VALUES (%s,'valuation_snapshot','internal',"
                    "%s,%s, NOW() - INTERVAL '10 days')",
                    (mondo["w_a"], json.dumps({"price_exact": 185000,
                                               "computed_at": "2026-09-10T08:00:00+00:00"}),
                     "vecchio-pertinenze"))
    conn.commit()
    prima = snapshots(mondo, mondo["w_a"])

    modulo["update"].update_home(mondo["acc"], mondo["st_a"], {"pertinenze": []}, 0)
    dopo = snapshots(mondo, mondo["w_a"])
    assert len(dopo) == len(prima) + 1
    assert dopo[0] == prima[0], "il punto storico e' identico, byte per byte"


def test_p9_il_secondo_salvataggio_vuoto_e_un_no_op(db, mondo, modulo):
    """TEST 9 - niente versione, niente `updated_at`, niente evento, niente
    snapshot."""
    modulo["update"].update_home(mondo["acc"], mondo["st_a"], {"pertinenze": []}, 0)
    prima = override(mondo, mondo["st_a"])
    eventi_prima = len(eventi(mondo, tracking.HOME_UPDATED))
    punti_prima = len(snapshots(mondo, mondo["w_a"]))

    esito = modulo["update"].update_home(mondo["acc"], mondo["st_a"],
                                         {"pertinenze": []}, prima["version"])
    assert esito["status"] == "unchanged", esito
    dopo = override(mondo, mondo["st_a"])
    assert dopo["version"] == prima["version"]
    assert dopo["updated_at"] == prima["updated_at"]
    assert dopo["pertinenze"] == ""
    assert len(eventi(mondo, tracking.HOME_UPDATED)) == eventi_prima
    assert len(snapshots(mondo, mondo["w_a"])) == punti_prima


def test_p10_un_override_null_continua_a_usare_l_originale(db, mondo, modulo):
    """TEST 10 - la semantica generale non e' cambiata.

    Si corregge un altro campo: la riga di override nasce con `pertinenze`
    NULL, e il profilo effettivo continua a mostrare quelle della stima.
    """
    modulo["update"].update_home(mondo["acc"], mondo["st_a"], {"mq": 140}, 0)
    riga = override(mondo, mondo["st_a"])
    assert riga["pertinenze"] is None
    vista = modulo["home_service"].get_home(mondo["acc"], mondo["st_a"])
    assert vista["property"]["pertinenze"] == "garage"
    assert vista["profile"]["overridden_fields"] == ["mq"]


def test_p11_un_token_sconosciuto_continua_a_dare_422(mondo, client):
    """TEST 11 - dalla rotta, non solo dal validatore."""
    for lista in (["box auto"], ["garage", "elicottero"], ["", "garage"]):
        risposta = client.patch(f"/api/owner/portal/homes/{mondo['st_a']}",
                                json={"expected_version": 0, "pertinenze": lista})
        assert risposta.status_code == 422, (lista, risposta.status_code)
    assert override(mondo, mondo["st_a"]) is None


def test_p12_dopo_la_lista_vuota_si_puo_aggiungere_la_cantina(db, mondo, modulo):
    """TEST 12 - e il garage non torna da solo."""
    modulo["update"].update_home(mondo["acc"], mondo["st_a"], {"pertinenze": []}, 0)
    esito = modulo["update"].update_home(mondo["acc"], mondo["st_a"],
                                         {"pertinenze": ["cantina"]}, 1)
    assert esito["status"] == "updated"
    assert esito["profile_version"] == 2
    riga = override(mondo, mondo["st_a"])
    assert home_profile.parse_pertinenze(riga["pertinenze"]) == ("cantina",)
    vista = modulo["home_service"].get_home(mondo["acc"], mondo["st_a"])
    assert "garage" not in (vista["property"]["pertinenze"] or "")


def test_p13_dalla_rotta_il_giro_completo(mondo, modulo, client):
    """La stessa cosa dall'HTTP, perche' e' da li' che arrivera'."""
    risposta = client.patch(f"/api/owner/portal/homes/{mondo['st_a']}",
                            json={"expected_version": 0, "pertinenze": []})
    assert risposta.status_code == 200, risposta.text
    corpo = risposta.json()
    assert corpo["status"] == "updated"
    assert corpo["updated_fields"] == ["pertinenze"]
    assert corpo["home"]["property"]["pertinenze"] == ""
    assert override(mondo, mondo["st_a"])["pertinenze"] == ""

    # Secondo invio identico: NO-OP anche dalla rotta.
    di_nuovo = client.patch(f"/api/owner/portal/homes/{mondo['st_a']}",
                            json={"expected_version": 1, "pertinenze": []})
    assert di_nuovo.status_code == 200
    assert di_nuovo.json()["status"] == "unchanged"


def test_p14_gli_extra_dell_originale_sopravvivono_alla_lista_vuota(db, mondo, modulo):
    """Un pezzo che il motore non conosce non se ne va perche' il
    proprietario ha deselezionato le caselle: non e' un token suo."""
    conn = db["conn"]
    conn.rollback()
    with conn.cursor() as cur:
        cur.execute("UPDATE stime SET pertinenze='garage, box auto' WHERE id=%s",
                    (mondo["st_a"],))
    conn.commit()
    modulo["update"].update_home(mondo["acc"], mondo["st_a"], {"pertinenze": []}, 0)
    riga = override(mondo, mondo["st_a"])
    assert riga["pertinenze"] == "box auto"
    assert home_profile.parse_pertinenze(riga["pertinenze"]) == ()


# ---------------------------------------------------------------------------
# LA COMPLETEZZA DOPO "NESSUNA PERTINENZA", SU DATI VERI
# ---------------------------------------------------------------------------

def test_q1_dichiarare_nessuna_pertinenza_non_abbassa_la_completezza(db, mondo, modulo):
    """TEST 2, 3 e 4 end-to-end.

    Il proprietario risponde "non ce ne sono". La percentuale non deve
    scendere: ha appena fornito un dato, non ne ha tolto uno.
    """
    prima = modulo["home_service"].get_home(mondo["acc"], mondo["st_a"])["profile"]
    assert "pertinenze" in prima["known_fields"]

    modulo["update"].update_home(mondo["acc"], mondo["st_a"], {"pertinenze": []}, 0)
    dopo = modulo["home_service"].get_home(mondo["acc"], mondo["st_a"])["profile"]

    assert "pertinenze" in dopo["known_fields"]
    assert "pertinenze" not in dopo["missing_fields"]
    assert dopo["completion_percent"] == prima["completion_percent"]
    assert dopo["completion_percent"] == round(
        len(dopo["known_fields"]) / len(dopo["considered_fields"]) * 100)


def test_q2_su_una_stima_senza_pertinenze_la_risposta_alza_la_completezza(db, mondo, modulo):
    """TEST 3 nella forma che conta davvero: da sconosciuto a conosciuto.

    La stima nasce senza pertinenze dichiarate - silenzio, campo mancante.
    Il proprietario dice "non ce ne sono": ora il database lo SA.
    """
    conn = db["conn"]
    conn.rollback()
    with conn.cursor() as cur:
        cur.execute("UPDATE stime SET pertinenze=NULL WHERE id=%s", (mondo["st_a"],))
    conn.commit()

    prima = modulo["home_service"].get_home(mondo["acc"], mondo["st_a"])["profile"]
    assert "pertinenze" in prima["missing_fields"]

    modulo["update"].update_home(mondo["acc"], mondo["st_a"], {"pertinenze": []}, 0)
    dopo = modulo["home_service"].get_home(mondo["acc"], mondo["st_a"])["profile"]

    assert "pertinenze" in dopo["known_fields"]
    assert len(dopo["known_fields"]) == len(prima["known_fields"]) + 1
    assert dopo["completion_percent"] > prima["completion_percent"]


def test_q3_un_override_su_un_altro_campo_non_tocca_le_pertinenze(db, mondo, modulo):
    """TEST 6 su dati veri: NULL resta silenzio."""
    conn = db["conn"]
    conn.rollback()
    with conn.cursor() as cur:
        cur.execute("UPDATE stime SET pertinenze=NULL WHERE id=%s", (mondo["st_a"],))
    conn.commit()

    modulo["update"].update_home(mondo["acc"], mondo["st_a"], {"mq": 140}, 0)
    assert override(mondo, mondo["st_a"])["pertinenze"] is None
    profilo = modulo["home_service"].get_home(mondo["acc"], mondo["st_a"])["profile"]
    assert "pertinenze" in profilo["missing_fields"]
    assert "pertinenze" not in profilo["overridden_fields"]


def test_q4_altrodescrizione_non_puo_essere_dichiarata_vuota(db, mondo, modulo):
    """TEST 5 dal percorso reale: il gesto non esiste proprio.

    Non c'e' modo, passando dal servizio, di ottenere un override vuoto su
    un campo diverso dalle pertinenze: la validazione lo rifiuta prima.
    Quindi la semantica speciale non e' raggiungibile per sbaglio.
    """
    with pytest.raises(modulo["update"].InvalidHomeUpdate) as errore:
        modulo["update"].update_home(mondo["acc"], mondo["st_a"],
                                     {"altrodescrizione": "   "}, 0)
    assert errore.value.fields == ("altrodescrizione",)
    assert override(mondo, mondo["st_a"]) is None
    profilo = modulo["home_service"].get_home(mondo["acc"], mondo["st_a"])["profile"]
    assert "altrodescrizione" in profilo["known_fields"], "quello originale c'e' ancora"


def test_q5_il_crm_resta_coerente(db, mondo, modulo):
    """TEST 7, lato operatore.

    La scheda contatto non espone la completezza - non e' un suo campo - ma
    deve continuare a mostrare il profilo effettivo e il radar senza
    incepparsi su una stringa vuota.
    """
    from owner import crm_radar
    modulo["update"].update_home(mondo["acc"], mondo["st_a"],
                                 {"pertinenze": [], "mq": 140}, 0)
    blocco = crm_radar.contact_homes_block(Scope(mondo["a"]), mondo["contact"])
    casa = [h for h in blocco["homes"] if h["stima_id"] == mondo["st_a"]][0]
    assert casa["mq"] == 140
    assert set(casa) == set(crm_radar.HOME_FIELDS)
    assert casa["interest"]["home_updated"] is True
    # E la scheda non porta le pertinenze: non sono un campo del CRM.
    assert "pertinenze" not in casa


def test_q6_il_secondo_save_vuoto_resta_un_no_op_anche_per_la_completezza(db, mondo, modulo):
    """TEST 8 - niente cambia, nemmeno la percentuale."""
    modulo["update"].update_home(mondo["acc"], mondo["st_a"], {"pertinenze": []}, 0)
    prima_profilo = modulo["home_service"].get_home(mondo["acc"], mondo["st_a"])["profile"]
    prima_riga = override(mondo, mondo["st_a"])

    esito = modulo["update"].update_home(mondo["acc"], mondo["st_a"], {"pertinenze": []}, 1)
    assert esito["status"] == "unchanged"

    dopo_riga = override(mondo, mondo["st_a"])
    assert (dopo_riga["version"], dopo_riga["updated_at"]) == \
           (prima_riga["version"], prima_riga["updated_at"])
    assert modulo["home_service"].get_home(
        mondo["acc"], mondo["st_a"])["profile"] == prima_profilo


def test_q7_il_valore_calcolato_non_dipende_dalla_completezza(db, mondo, modulo):
    """TEST 9 - la modifica tocca una metrica di presentazione, non il motore.

    Lo snapshot nato dall'update deve valere ESATTAMENTE quanto gia'
    certificato in `test_p6`: `compute_from_payload` con `pertinenze = ''`.
    La percentuale di completezza non entra in nessun payload.
    """
    import valuation

    base = {"comune": "Alba Adriatica", "microzona": "Villa Fiore",
            "tipologia": "Appartamento", "mq": 95, "piano": "3", "locali": 4,
            "bagni": 2, "ascensore": "Sì", "anno": 1998, "stato": "buono",
            "posizioneMare": "fronte", "distanzaMare": "0-100",
            "barrieraMare": "no", "vistaMareYN": "si",
            "vistaMareDettaglio": "frontale", "vistaMare": "mare",
            "mqGiardino": 0, "mqGarage": 18, "mqCantina": 6, "mqPostoAuto": 0,
            "mqTaverna": 0, "mqSoffitta": 0, "mqTerrazzo": 12, "numBalconi": 2,
            "via": "Via Trieste", "altroDescrizione": "ristrutturato"}
    atteso = valuation.compute_from_payload({**base, "pertinenze": ""})

    modulo["update"].update_home(mondo["acc"], mondo["st_a"], {"pertinenze": []}, 0)
    payload = snapshots(mondo, mondo["w_a"])[0][0]
    assert payload["price_exact"] == atteso["price_exact"]
    for chiave in ("completion_percent", "known_fields", "missing_fields"):
        assert chiave not in payload, chiave
