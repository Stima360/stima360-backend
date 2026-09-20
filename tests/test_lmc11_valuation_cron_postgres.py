"""LMC-11 su PostgreSQL reale: il giro periodico, dal watch allo storico owner.

CINQUE COSE CHE UN DOPPIO NON PUO' PROVARE.

LA TENANCY. Che un watch di un'altra agenzia non venga nemmeno letto, e che un
watch che punta alla stima di un altro tenant sia escluso invece di finire fra
i guasti.

L'IDEMPOTENZA. Che due giri nello stesso giorno lascino un punto solo, e che
il giorno dopo ne nasca uno nuovo ANCHE A VALORE IDENTICO - tre giorni di
210.000 sono tre osservazioni vere, non un duplicato. E' la regola di LMC-3, e
qui si verifica che il cron la erediti invece di inventarsene una.

IL LOCK. Che una seconda esecuzione trovi il lock occupato ed esca pulita, e
che il lock si liberi anche quando il giro solleva. Con un doppio si
proverebbe la propria fantasia sul lock, non il lock.

L'IMMUTABILITA'. Che `stime`, gli override di LMC-10, la baseline e gli
snapshot gia' scritti restino byte per byte quelli di prima.

LO STORICO OWNER. Che dopo qualche giro il proprietario veda davvero
`current_value` aggiornato, la storia crescere e `change_30d` passare da
`null` a un numero quando - e solo quando - la storia basta.

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

DSN = os.getenv("P29_TEST_DSN")

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare LMC-11")

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

    nome = f"lmc11_probe_{os.getpid()}_{uuid.uuid4().hex[:6]}"
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
    from property_watch import repository as pw_repository
    from owner import repository as owner_repository
    from property_watch import database as pw_database
    from seller_intelligence import database as si_database
    from property_watch import service as pw_service

    monkeypatch.setattr(core_database, "get_connection", lambda: psycopg2.connect(db["dsn"]))
    monkeypatch.setattr(pw_database, "get_connection", lambda: psycopg2.connect(db["dsn"]))
    # LMC-7: gli eventi passano da Seller Intelligence, che apre la propria
    # connessione. Senza questa riga il radar scriverebbe altrove.
    monkeypatch.setattr(si_database, "get_connection", lambda: psycopg2.connect(db["dsn"]))
    return {"pw": pw_service, "pw_repository": pw_repository,
            "home_service": home_service,
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


def righe(mondo, sql, params=()):
    with mondo["conn"].cursor() as cur:
        cur.execute(sql, params)
        valori = cur.fetchall()
    mondo["conn"].rollback()
    return valori


def snapshots(mondo, watch_id):
    return righe(mondo, "SELECT payload, observed_at, idempotency_key "
                        "FROM property_watch_observations "
                        "WHERE watch_id=%s AND observation_type='valuation_snapshot' "
                        "ORDER BY id", (watch_id,))


def eventi(mondo):
    return righe(mondo, "SELECT event_type FROM seller_timeline_events")


def prezzi(mondo, watch_id):
    return [p[0]["price_exact"] for p in snapshots(mondo, watch_id)]


GIORNO = timedelta(days=1)
OGGI = datetime(2026, 9, 20, 9, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# A - ELEGGIBILITA' E TENANCY (test 1-5)
# ---------------------------------------------------------------------------

def test_1_un_watch_attivo_viene_rivalutato(db, mondo, modulo):
    esito = modulo["pw"].refresh_valuation_snapshots_for_agency(mondo["a"], now=OGGI)
    assert esito["created"] == 1, esito
    assert esito["processed"] == 1
    assert len(snapshots(mondo, mondo["w_a"])) == 1


def test_2_un_watch_non_attivo_non_viene_nemmeno_letto(db, mondo, modulo):
    """Lo status non e' un filtro a valle: e' nella SELECT che decide chi e'
    eleggibile, quindi il watch non compare proprio."""
    conn = db["conn"]
    conn.rollback()
    with conn.cursor() as cur:
        cur.execute("UPDATE property_watches SET status='paused' WHERE id=%s",
                    (mondo["w_a"],))
    conn.commit()

    bersagli = modulo["pw_repository"].list_active_watch_page_for_agency(mondo["a"], page_size=100)
    assert bersagli == []
    esito = modulo["pw"].refresh_valuation_snapshots_for_agency(mondo["a"], now=OGGI)
    assert esito == {"agency_id": mondo["a"], "processed": 0, "created": 0,
                     "reused": 0, "skipped": 0, "failed": 0}
    assert snapshots(mondo, mondo["w_a"]) == []


def test_3_il_watch_di_un_altro_tenant_e_escluso(db, mondo, modulo):
    bersagli = modulo["pw_repository"].list_active_watch_page_for_agency(mondo["a"], page_size=100)
    assert [b["stima_id"] for b in bersagli] == [mondo["st_a"]]
    assert mondo["st_b"] not in [b["stima_id"] for b in bersagli]

    modulo["pw"].refresh_valuation_snapshots_for_agency(mondo["a"], now=OGGI)
    assert snapshots(mondo, mondo["w_b"]) == [], "il watch di B non e' stato toccato"


def test_4_un_watch_che_punta_alla_stima_di_un_altro_tenant_e_escluso(db, mondo, modulo):
    """Non e' un guasto, e' un'incoerenza: escluderla alla lettura evita di
    contarla come `failed`, dove sarebbe indistinguibile da un errore vero."""
    conn = db["conn"]
    conn.rollback()
    with conn.cursor() as cur:
        # Il watch resta di A, la stima diventa di B: la JOIN lo esclude.
        cur.execute("UPDATE stime SET agency_id=%s WHERE id=%s",
                    (mondo["b"], mondo["st_a"]))
    conn.commit()

    assert modulo["pw_repository"].list_active_watch_page_for_agency(mondo["a"], page_size=100) == []
    esito = modulo["pw"].refresh_valuation_snapshots_for_agency(mondo["a"], now=OGGI)
    assert esito["processed"] == 0 and esito["failed"] == 0


def test_5_ogni_refresh_usa_lo_scope_dell_agenzia(db, mondo, modulo, monkeypatch):
    """Mai una versione senza contesto: si registra lo scope di ogni chiamata."""
    visti = []
    vero = modulo["pw"].refresh_valuation_snapshot_scoped

    def spia(ctx, stima_id, *, reason, now=None):
        visti.append((ctx.require_agency(), stima_id, reason))
        return vero(ctx, stima_id, reason=reason, now=now)

    monkeypatch.setattr(modulo["pw"], "refresh_valuation_snapshot_scoped", spia)
    modulo["pw"].refresh_valuation_snapshots_for_all_agencies(now=OGGI)

    # Entrambe le agenzie vengono visitate - e' il giro su tutti i tenant -
    # e ognuna con lo scope della PROPRIA. La cosa che conta non e' quante
    # chiamate ci sono, e' che nessuna porti l'agenzia di una e la stima
    # dell'altra.
    ragione = modulo["pw"].CRON_REFRESH_REASON
    assert sorted(visti) == sorted([(mondo["a"], mondo["st_a"], ragione),
                                    (mondo["b"], mondo["st_b"], ragione)])
    proprietaria = dict(righe(mondo, "SELECT id, agency_id FROM stime"))
    for agency_id, stima_id, _ragione in visti:
        assert proprietaria[stima_id] == agency_id, (agency_id, stima_id)


def test_5b_il_giro_su_tutte_le_agenzie_somma_e_non_mescola(db, mondo, modulo):
    conn = db["conn"]
    conn.rollback()
    with conn.cursor() as cur:
        cur.execute("INSERT INTO property_watch_observations (watch_id,observation_type,"
                    "source,payload,idempotency_key,observed_at) VALUES "
                    "(%s,'watch_started','internal',%s,%s,NOW())",
                    (mondo["w_b"], json.dumps({"price_exact": 150000}), f"base-{mondo['st_b']}"))
    conn.commit()

    esito = modulo["pw"].refresh_valuation_snapshots_for_all_agencies(now=OGGI)
    assert esito["agencies"] == 2
    assert esito["created"] == 2
    per_agenzia = {r["agency_id"]: r for r in esito["runs"]}
    assert per_agenzia[mondo["a"]]["processed"] == 1
    assert per_agenzia[mondo["b"]]["processed"] == 1
    assert len(snapshots(mondo, mondo["w_a"])) == 1
    assert len(snapshots(mondo, mondo["w_b"])) == 1


# ---------------------------------------------------------------------------
# B - IDEMPOTENZA (test 6-9): la regola di LMC-3, ereditata
# ---------------------------------------------------------------------------

def test_7_stesso_giorno_stesso_input_e_reused(db, mondo, modulo):
    primo = modulo["pw"].refresh_valuation_snapshots_for_agency(mondo["a"], now=OGGI)
    secondo = modulo["pw"].refresh_valuation_snapshots_for_agency(
        mondo["a"], now=OGGI + timedelta(hours=3))
    assert primo["created"] == 1 and primo["reused"] == 0
    assert secondo["created"] == 0 and secondo["reused"] == 1
    assert len(snapshots(mondo, mondo["w_a"])) == 1, "un punto solo"


def test_6_9_giorno_nuovo_stesso_valore_nuovo_punto(db, mondo, modulo):
    """TEST 6 e 9 insieme, perche' sono la stessa affermazione.

    Tre giorni, stessi identici dati della casa, stesso prezzo: TRE snapshot.
    Sono tre osservazioni temporali vere - "il 21 valeva ancora 210.000" e'
    un'informazione, non un duplicato - e deduplicare per sempre
    sull'`input_digest` la cancellerebbe.
    """
    for scarto in (0, 1, 2):
        esito = modulo["pw"].refresh_valuation_snapshots_for_agency(
            mondo["a"], now=OGGI + scarto * GIORNO)
        assert esito["created"] == 1, (scarto, esito)

    punti = snapshots(mondo, mondo["w_a"])
    assert len(punti) == 3
    valori = [p[0]["price_exact"] for p in punti]
    assert len(set(valori)) == 1, f"stesso valore tutti e tre: {valori}"
    giorni = [p[2].split(":day:")[0] if ":day:" in p[2] else p[2] for p in punti]
    assert len(set(p[2] for p in punti)) == 3, "tre chiavi di idempotenza diverse"


def test_8_stesso_giorno_input_diverso_nuovo_snapshot(db, mondo, modulo):
    """La regola di LMC-3: l'impronta dell'input entra nella chiave, quindi un
    dato della casa che cambia fa nascere subito un punto, senza aspettare
    domani."""
    modulo["pw"].refresh_valuation_snapshots_for_agency(mondo["a"], now=OGGI)
    conn = db["conn"]
    conn.rollback()
    with conn.cursor() as cur:
        cur.execute("UPDATE stime SET mq=140 WHERE id=%s", (mondo["st_a"],))
    conn.commit()

    esito = modulo["pw"].refresh_valuation_snapshots_for_agency(
        mondo["a"], now=OGGI + timedelta(hours=1))
    assert esito["created"] == 1
    punti = snapshots(mondo, mondo["w_a"])
    assert len(punti) == 2
    assert punti[0][0]["price_exact"] != punti[1][0]["price_exact"]


def test_b1_il_cron_non_costruisce_chiavi_proprie(db, mondo, modulo):
    """La chiave e' quella di LMC-3, con la sua forma."""
    modulo["pw"].refresh_valuation_snapshots_for_agency(mondo["a"], now=OGGI)
    chiave = snapshots(mondo, mondo["w_a"])[0][2]
    assert chiave.startswith(f"property_watch:valuation_snapshot:watch:{mondo['w_a']}:")
    assert "2026-09-20" in chiave
    assert "cron" not in chiave and "scheduled" not in chiave


# ---------------------------------------------------------------------------
# C - PROFILO EFFETTIVO LMC-10 (test 10) E IMMUTABILITA' (test 11-14)
# ---------------------------------------------------------------------------

def test_10_il_cron_usa_il_profilo_effettivo(db, mondo, modulo):
    """Il proprietario ha corretto i metri quadri: il cron deve calcolare su
    140, non sui 95 dell'originale. E lo si verifica contro il motore."""
    import valuation

    conn = db["conn"]
    conn.rollback()
    with conn.cursor() as cur:
        cur.execute("INSERT INTO owner_home_overrides (stima_id, mq) VALUES (%s, 140)",
                    (mondo["st_a"],))
    conn.commit()

    modulo["pw"].refresh_valuation_snapshots_for_agency(mondo["a"], now=OGGI)
    payload = snapshots(mondo, mondo["w_a"])[0][0]
    atteso = valuation.compute_from_payload({
        "comune": "Alba Adriatica", "microzona": "Villa Fiore",
        "tipologia": "Appartamento", "mq": 140, "piano": "3", "locali": 4,
        "bagni": 2, "ascensore": "Sì", "anno": 1998, "stato": "buono",
        "posizioneMare": "fronte", "distanzaMare": "0-100", "barrieraMare": "no",
        "vistaMareYN": "si", "vistaMareDettaglio": "frontale", "vistaMare": "mare",
        "pertinenze": "garage", "mqGiardino": 0, "mqGarage": 18, "mqCantina": 6,
        "mqPostoAuto": 0, "mqTaverna": 0, "mqSoffitta": 0, "mqTerrazzo": 12,
        "numBalconi": 2, "via": "Via Trieste", "altroDescrizione": "ristrutturato"})
    assert payload["price_exact"] == atteso["price_exact"]


def test_11_12_13_14_il_giro_non_modifica_niente(db, mondo, modulo):
    """TEST 11-14 insieme: si fotografa tutto prima, si gira, si riconfronta."""
    conn = db["conn"]
    conn.rollback()
    with conn.cursor() as cur:
        cur.execute("INSERT INTO owner_home_overrides (stima_id, mq) VALUES (%s, 140)",
                    (mondo["st_a"],))
        cur.execute("INSERT INTO property_watch_observations (watch_id,observation_type,"
                    "source,payload,idempotency_key,observed_at) VALUES "
                    "(%s,'valuation_snapshot','internal',%s,%s, NOW() - INTERVAL '10 days')",
                    (mondo["w_a"], json.dumps({"price_exact": 185000,
                                               "computed_at": "2026-09-10T08:00:00+00:00"}),
                     "vecchio-lmc11"))
    conn.commit()

    prima = {
        "stime": righe(mondo, "SELECT * FROM stime ORDER BY id"),
        "override": righe(mondo, "SELECT * FROM owner_home_overrides ORDER BY id"),
        "baseline": righe(mondo, "SELECT payload, observed_at FROM property_watch_observations "
                                 "WHERE observation_type='watch_started' ORDER BY id"),
        "vecchio": righe(mondo, "SELECT payload, observed_at FROM property_watch_observations "
                                "WHERE idempotency_key='vecchio-lmc11'"),
        "watch": righe(mondo, "SELECT id, status, stima_id, agency_id "
                              "FROM property_watches ORDER BY id"),
    }

    modulo["pw"].refresh_valuation_snapshots_for_all_agencies(now=OGGI)

    assert righe(mondo, "SELECT * FROM stime ORDER BY id") == prima["stime"]
    assert righe(mondo, "SELECT * FROM owner_home_overrides ORDER BY id") == prima["override"]
    assert righe(mondo, "SELECT payload, observed_at FROM property_watch_observations "
                        "WHERE observation_type='watch_started' ORDER BY id") == prima["baseline"]
    assert righe(mondo, "SELECT payload, observed_at FROM property_watch_observations "
                        "WHERE idempotency_key='vecchio-lmc11'") == prima["vecchio"]
    assert righe(mondo, "SELECT id, status, stima_id, agency_id "
                        "FROM property_watches ORDER BY id") == prima["watch"]


# ---------------------------------------------------------------------------
# D - ISOLAMENTO DEI GUASTI (test 15-16) E RIEPILOGO (test 17-20)
# ---------------------------------------------------------------------------

def _seconda_casa(db, mondo, comune="Tortoreto", microzona="Lido Centro"):
    """Una seconda casa monitorata nell'agenzia A, per provare l'isolamento."""
    conn = db["conn"]
    conn.rollback()
    with conn.cursor() as cur:
        colonne = {"comune": comune, "microzona": microzona, "via": "Via Due",
                   "civico": "5", "tipologia": "Appartamento", "mq": 80,
                   "piano": "1", "locali": 3, "bagni": 1, "pertinenze": "cantina",
                   "ascensore": "False", "anno": 2005, "stato": "buono",
                   "posizionemare": "seconda", "distanzamare": "100-300",
                   "barrieramare": "no", "vistamareyn": "no",
                   "vistamaredettaglio": "", "vistamare": "",
                   "mqgiardino": 0, "mqgarage": 0, "mqcantina": 5,
                   "mqpostoauto": 0, "mqtaverna": 0, "mqsoffitta": 0,
                   "mqterrazzo": 0, "numbalconi": 1, "altrodescrizione": ""}
        cur.execute(f"INSERT INTO stime (agency_id,{','.join(colonne)}) VALUES "
                    f"({','.join(['%s'] * (len(colonne) + 1))}) RETURNING id",
                    [mondo["a"], *colonne.values()])
        stima_id = cur.fetchone()[0]
        cur.execute("INSERT INTO property_watches (stima_id,status,agency_id) "
                    "VALUES (%s,'active',%s) RETURNING id", (stima_id, mondo["a"]))
        watch_id = cur.fetchone()[0]
    conn.commit()
    return stima_id, watch_id


def test_15_16_un_watch_che_fallisce_non_ferma_il_successivo(db, mondo, modulo, monkeypatch):
    """E soprattutto: il watch fallito resta ATTIVO. Un guasto non toglie di
    mezzo la casa."""
    seconda_stima, seconda_watch = _seconda_casa(db, mondo)
    vero = modulo["pw"].refresh_valuation_snapshot_scoped

    def capriccioso(ctx, stima_id, *, reason, now=None):
        if stima_id == mondo["st_a"]:
            raise RuntimeError("motore rotto per questa casa")
        return vero(ctx, stima_id, reason=reason, now=now)

    monkeypatch.setattr(modulo["pw"], "refresh_valuation_snapshot_scoped", capriccioso)
    esito = modulo["pw"].refresh_valuation_snapshots_for_agency(mondo["a"], now=OGGI)

    assert esito["processed"] == 2
    assert esito["failed"] == 1
    assert esito["created"] == 1, "la seconda casa e' stata lavorata lo stesso"
    assert snapshots(mondo, mondo["w_a"]) == []
    assert len(snapshots(mondo, seconda_watch)) == 1
    stati = dict(righe(mondo, "SELECT id, status FROM property_watches"))
    assert stati[mondo["w_a"]] == "active", "il watch fallito NON viene disabilitato"
    assert stati[seconda_watch] == "active"


def test_17_18_19_20_il_riepilogo_conta_i_quattro_esiti(db, mondo, modulo, monkeypatch):
    """Quattro case, quattro esiti diversi, un riepilogo che li distingue."""
    from property_watch.exceptions import StimaNotFoundError

    # PRIMO GIRO con la sola `st_a`: e' quella che al secondo giro sara'
    # `reused`. Le altre tre case nascono DOPO, cosi' ognuna arriva al
    # secondo giro con l'esito che questo test vuole isolare.
    modulo["pw"].refresh_valuation_snapshots_for_agency(mondo["a"], now=OGGI)

    s2, w2 = _seconda_casa(db, mondo)
    s3, w3 = _seconda_casa(db, mondo, comune="Martinsicuro", microzona="Centro")
    s4, w4 = _seconda_casa(db, mondo, comune="Alba Adriatica", microzona="Nord")
    vero = modulo["pw"].refresh_valuation_snapshot_scoped

    def misto(ctx, stima_id, *, reason, now=None):
        if stima_id == s3:
            raise StimaNotFoundError("non raggiungibile")
        if stima_id == s4:
            raise RuntimeError("guasto vero")
        return vero(ctx, stima_id, reason=reason, now=now)

    monkeypatch.setattr(modulo["pw"], "refresh_valuation_snapshot_scoped", misto)
    esito = modulo["pw"].refresh_valuation_snapshots_for_agency(mondo["a"], now=OGGI)

    assert esito["processed"] == 4
    assert esito["created"] == 1, esito     # s2, la prima volta
    assert esito["reused"] == 1, esito      # st_a, gia' fatta oggi
    assert esito["skipped"] == 1, esito     # s3, eccezione di dominio
    assert esito["failed"] == 1, esito      # s4, guasto vero


def test_d1_la_pagina_non_e_un_tetto_il_terzo_watch_viene_lavorato(db, mondo, modulo):
    """IL DIFETTO CHE QUESTA REVISIONE CHIUDE.

    Tre watch, pagine da due: prima il terzo restava fuori, e ci restava
    tutte le notti perche' l'ordine e' deterministico. Adesso il giro fa due
    pagine e li lavora tutti e tre.
    """
    s2, w2 = _seconda_casa(db, mondo)
    s3, w3 = _seconda_casa(db, mondo, comune="Martinsicuro", microzona="Centro")

    prima = modulo["pw_repository"].list_active_watch_page_for_agency(
        mondo["a"], page_size=100)
    assert [b["watch_id"] for b in prima] == sorted(b["watch_id"] for b in prima)

    esito = modulo["pw"].refresh_valuation_snapshots_for_agency(
        mondo["a"], page_size=2, now=OGGI)
    assert esito["processed"] == 3, esito
    assert esito["created"] == 3
    for watch_id in (mondo["w_a"], w2, w3):
        assert len(snapshots(mondo, watch_id)) == 1, watch_id


def test_d1b_le_pagine_non_si_sovrappongono(db, mondo, modulo):
    """Il cursore in azione: pagina per pagina, senza ripetizioni e senza
    buchi."""
    s2, w2 = _seconda_casa(db, mondo)
    s3, w3 = _seconda_casa(db, mondo, comune="Martinsicuro", microzona="Centro")
    attesi = sorted([mondo["w_a"], w2, w3])

    visti, dopo = [], 0
    while True:
        pagina = modulo["pw_repository"].list_active_watch_page_for_agency(
            mondo["a"], after_watch_id=dopo, page_size=2)
        if not pagina:
            break
        ids = [b["watch_id"] for b in pagina]
        assert ids == sorted(ids)
        assert not (set(ids) & set(visti)), "nessun watch in due pagine"
        visti.extend(ids)
        dopo = ids[-1]
    assert visti == attesi


# ---------------------------------------------------------------------------
# E - NESSUN SIDE EFFECT COMMERCIALE (test 22-25), sui dati
# ---------------------------------------------------------------------------

def test_22_25_il_giro_non_scrive_niente_di_commerciale(db, mondo, modulo):
    prima_eventi = eventi(mondo)
    prima_attivita = righe(mondo, "SELECT count(*) FROM activities")
    prima_owner = righe(mondo, "SELECT count(*) FROM owner_audit_log")

    modulo["pw"].refresh_valuation_snapshots_for_all_agencies(now=OGGI)

    assert eventi(mondo) == prima_eventi, "nessun evento Seller Intelligence"
    assert righe(mondo, "SELECT count(*) FROM activities") == prima_attivita
    assert righe(mondo, "SELECT count(*) FROM owner_audit_log") == prima_owner
    # L'unica cosa scritta e' l'osservazione di valutazione.
    tipi = righe(mondo, "SELECT DISTINCT observation_type "
                        "FROM property_watch_observations ORDER BY 1")
    assert set(t[0] for t in tipi) <= {"watch_started", "valuation_snapshot"}, tipi


# ---------------------------------------------------------------------------
# F - IL LOCK, SU POSTGRESQL VERO (test 26-27)
# ---------------------------------------------------------------------------

def test_26_una_seconda_esecuzione_trova_il_lock_occupato(db, mondo, modulo):
    from property_watch.database import VALUATION_CRON_LOCK_SCOPE, advisory_job_lock

    with advisory_job_lock(VALUATION_CRON_LOCK_SCOPE) as primo:
        assert primo is True
        with advisory_job_lock(VALUATION_CRON_LOCK_SCOPE) as secondo:
            assert secondo is False, "il secondo giro non deve entrare"


def test_27_il_lock_si_rilascia_all_uscita(db, mondo, modulo):
    from property_watch.database import VALUATION_CRON_LOCK_SCOPE, advisory_job_lock

    with advisory_job_lock(VALUATION_CRON_LOCK_SCOPE) as ottenuto:
        assert ottenuto is True
    with advisory_job_lock(VALUATION_CRON_LOCK_SCOPE) as dopo:
        assert dopo is True, "rilasciato all'uscita dal blocco"


def test_27b_il_lock_si_rilascia_anche_dopo_un_errore(db, mondo, modulo):
    from property_watch.database import VALUATION_CRON_LOCK_SCOPE, advisory_job_lock

    with pytest.raises(RuntimeError):
        with advisory_job_lock(VALUATION_CRON_LOCK_SCOPE) as ottenuto:
            assert ottenuto is True
            raise RuntimeError("giro rotto")
    with advisory_job_lock(VALUATION_CRON_LOCK_SCOPE) as dopo:
        assert dopo is True, "rilasciato anche sull'eccezione"


def test_27c_scope_diversi_non_si_bloccano_a_vicenda(db, mondo, modulo):
    from property_watch.database import VALUATION_CRON_LOCK_SCOPE, advisory_job_lock

    with advisory_job_lock(VALUATION_CRON_LOCK_SCOPE) as uno:
        with advisory_job_lock("property_watch:altro_giro") as due:
            assert uno is True and due is True


# ---------------------------------------------------------------------------
# G - LO STORICO DEL PROPRIETARIO, END-TO-END (test 28-33)
# ---------------------------------------------------------------------------

def test_28_29_current_value_e_storia_dopo_il_giro(db, mondo, modulo):
    prima = modulo["home_service"].get_home(mondo["acc"], mondo["st_a"])
    assert prima["valuation"]["current_value"] is None
    assert prima["valuation"]["current_value_status"] == "history_not_available"
    assert prima["valuation_history"] == []

    modulo["pw"].refresh_valuation_snapshots_for_agency(mondo["a"], now=OGGI)
    dopo = modulo["home_service"].get_home(mondo["acc"], mondo["st_a"])

    atteso = snapshots(mondo, mondo["w_a"])[0][0]
    assert dopo["valuation"]["current_value"] == atteso["price_exact"]
    assert dopo["valuation"]["current_value_status"] == "available"
    assert dopo["valuation"]["current_value_computed_at"] == atteso["computed_at"]
    assert len(dopo["valuation_history"]) == 1
    assert dopo["capabilities"]["valuation_history"] is True


def test_29b_la_storia_cresce_di_un_punto_al_giorno(db, mondo, modulo):
    for scarto in range(4):
        modulo["pw"].refresh_valuation_snapshots_for_agency(
            mondo["a"], now=OGGI + scarto * GIORNO)
    vista = modulo["home_service"].get_home(mondo["acc"], mondo["st_a"])
    assert len(vista["valuation_history"]) == 4
    date = [p["computed_at"][:10] for p in vista["valuation_history"]]
    assert date == ["2026-09-20", "2026-09-21", "2026-09-22", "2026-09-23"]


def test_30_con_storia_insufficiente_change_30d_resta_null(db, mondo, modulo):
    for scarto in (0, 1, 2):
        modulo["pw"].refresh_valuation_snapshots_for_agency(
            mondo["a"], now=OGGI + scarto * GIORNO)
    vista = modulo["home_service"].get_home(mondo["acc"], mondo["st_a"])
    assert len(vista["valuation_history"]) == 3
    assert vista["valuation"]["change_30d"] is None, "tre giorni non sono trenta"
    assert vista["valuation"]["change_90d"] is None
    assert vista["valuation"]["change_365d"] is None


def test_31_con_storia_sufficiente_change_30d_e_reale(db, mondo, modulo):
    """Due giri a quaranta giorni di distanza, con i dati della casa cambiati
    in mezzo: la finestra a 30 giorni ha due punti veri da confrontare."""
    modulo["pw"].refresh_valuation_snapshots_for_agency(mondo["a"], now=OGGI)
    conn = db["conn"]
    conn.rollback()
    with conn.cursor() as cur:
        cur.execute("UPDATE stime SET mq=140 WHERE id=%s", (mondo["st_a"],))
    conn.commit()
    modulo["pw"].refresh_valuation_snapshots_for_agency(
        mondo["a"], now=OGGI + 40 * GIORNO)

    vista = modulo["home_service"].get_home(mondo["acc"], mondo["st_a"])
    cambio = vista["valuation"]["change_30d"]
    assert cambio is not None, vista["valuation"]
    assert cambio["methodology_changed"] is False
    assert cambio["change_percent"] is not None
    assert cambio["to_value"] > cambio["from_value"]


def test_32_cambio_di_metodologia_percentuale_nulla(db, mondo, modulo, monkeypatch):
    """Il cron non normalizza il passato: il punto vecchio resta com'e', il
    nuovo nasce con l'impronta nuova, e la percentuale che li legherebbe
    diventa `null` perche' quei due numeri non si sottraggono."""
    from property_watch import valuation_snapshot as vs

    modulo["pw"].refresh_valuation_snapshots_for_agency(mondo["a"], now=OGGI)
    primo = snapshots(mondo, mondo["w_a"])[0][0]

    monkeypatch.setattr(vs, "ALGORITHM_FINGERPRINT", "valuation-metodo-nuovo")
    monkeypatch.setattr(vs, "algorithm_fingerprint", lambda: "valuation-metodo-nuovo")
    modulo["pw"].refresh_valuation_snapshots_for_agency(
        mondo["a"], now=OGGI + 40 * GIORNO)

    punti = snapshots(mondo, mondo["w_a"])
    assert len(punti) == 2
    assert punti[0][0] == primo, "il punto vecchio non e' stato normalizzato"
    assert punti[1][0]["algorithm_fingerprint"] == "valuation-metodo-nuovo"

    cambio = modulo["home_service"].get_home(
        mondo["acc"], mondo["st_a"])["valuation"]["change_30d"]
    assert cambio["methodology_changed"] is True
    assert cambio["change_percent"] is None
    assert cambio["from_value"] is not None and cambio["to_value"] is not None


def test_33_il_crm_vede_il_nuovo_valore_senza_logica_nuova(db, mondo, modulo):
    from owner import crm_radar

    prima = crm_radar.contact_homes_block(Scope(mondo["a"]), mondo["contact"])
    casa_prima = [h for h in prima["homes"] if h["stima_id"] == mondo["st_a"]][0]
    assert casa_prima["current_value"] is None

    modulo["pw"].refresh_valuation_snapshots_for_agency(mondo["a"], now=OGGI)

    dopo = crm_radar.contact_homes_block(Scope(mondo["a"]), mondo["contact"])
    casa_dopo = [h for h in dopo["homes"] if h["stima_id"] == mondo["st_a"]][0]
    atteso = snapshots(mondo, mondo["w_a"])[0][0]
    assert casa_dopo["current_value"] == atteso["price_exact"]
    assert casa_dopo["current_value_computed_at"] == atteso["computed_at"]


def test_g1_il_giro_con_override_arriva_fino_al_portale(db, mondo, modulo):
    """Il giro completo di LMC-10 + LMC-11: il proprietario corregge, il cron
    ricalcola, il proprietario rilegge il valore nuovo."""
    esito = modulo["update"].update_home(mondo["acc"], mondo["st_a"], {"mq": 140}, 0)
    assert esito["status"] == "updated"
    valore_owner = modulo["home_service"].get_home(
        mondo["acc"], mondo["st_a"])["valuation"]["current_value"]

    # Il giorno dopo il cron gira: stessi dati, punto nuovo, stesso valore.
    modulo["pw"].refresh_valuation_snapshots_for_agency(mondo["a"], now=OGGI + GIORNO)
    vista = modulo["home_service"].get_home(mondo["acc"], mondo["st_a"])
    assert len(vista["valuation_history"]) == 2
    assert vista["valuation"]["current_value"] == valore_owner
    assert righe(mondo, "SELECT mq FROM stime WHERE id=%s", (mondo["st_a"],))[0][0] == 95


# ---------------------------------------------------------------------------
# H - IL RUNNER VERO, ESEGUITO CONTRO IL DATABASE
#
# Sopra si prova il servizio; qui si prova il comando che Render eseguira':
# la stessa `main()`, con il lock vero, il riepilogo vero e il codice di
# uscita vero.
# ---------------------------------------------------------------------------

def test_h1_il_runner_gira_su_tutte_le_agenzie(db, mondo, modulo, capsys):
    import run_property_watch_valuation_cron as cron

    assert cron.main(["--all-agencies"]) == 0
    uscita = capsys.readouterr().out
    assert "phase=runner status=started" in uscita
    assert "phase=runner status=completed" in uscita
    assert "agencies=2" in uscita
    assert "created=2" in uscita
    assert "failed=0" in uscita
    assert len(snapshots(mondo, mondo["w_a"])) == 1


def test_h2_il_runner_su_una_sola_agenzia_non_tocca_l_altra(db, mondo, modulo, capsys):
    import run_property_watch_valuation_cron as cron

    assert cron.main(["--agency-id", str(mondo["a"])]) == 0
    assert "agencies=1" in capsys.readouterr().out
    assert len(snapshots(mondo, mondo["w_a"])) == 1
    assert snapshots(mondo, mondo["w_b"]) == []


def test_h3_due_giri_nello_stesso_giorno_lasciano_un_punto(db, mondo, modulo, capsys):
    import run_property_watch_valuation_cron as cron

    assert cron.main(["--all-agencies"]) == 0
    capsys.readouterr()
    assert cron.main(["--all-agencies"]) == 0
    uscita = capsys.readouterr().out
    assert "reused=2" in uscita and "created=0" in uscita
    assert len(snapshots(mondo, mondo["w_a"])) == 1


def test_h4_il_lock_impedisce_la_seconda_esecuzione(db, mondo, modulo, capsys):
    """Il lock vero, con il runner vero: mentre il primo lo tiene, il secondo
    esce con `another_run_active` e senza scrivere niente."""
    import run_property_watch_valuation_cron as cron
    from property_watch.database import VALUATION_CRON_LOCK_SCOPE, advisory_job_lock

    with advisory_job_lock(VALUATION_CRON_LOCK_SCOPE) as tenuto:
        assert tenuto is True
        capsys.readouterr()
        assert cron.main(["--all-agencies"]) == 0
        uscita = capsys.readouterr().out

    assert "status=another_run_active" in uscita
    assert "status=started" not in uscita
    assert snapshots(mondo, mondo["w_a"]) == [], "nessuna scrittura senza lock"

    # E appena il lock si libera, il giro successivo lavora.
    assert cron.main(["--all-agencies"]) == 0
    assert len(snapshots(mondo, mondo["w_a"])) == 1


def test_h5_un_watch_fallito_da_exit_2_e_lo_dice(db, mondo, modulo, monkeypatch, capsys):
    import run_property_watch_valuation_cron as cron

    def esplode(ctx, stima_id, *, reason, now=None):
        raise RuntimeError("motore giu'")

    monkeypatch.setattr(modulo["pw"], "refresh_valuation_snapshot_scoped", esplode)
    assert cron.main(["--all-agencies"]) == 2
    uscita = capsys.readouterr().out
    assert "phase=runner status=failed" in uscita
    assert "failed=2" in uscita
    # E i watch restano attivi: un guasto non toglie di mezzo le case.
    stati = {r[1] for r in righe(mondo, "SELECT id, status FROM property_watches")}
    assert stati == {"active"}


def test_h6_il_log_del_runner_non_porta_dati_personali(db, mondo, modulo, capsys):
    """La casa ha nome, email, telefono e indirizzo in tabella: dopo un giro
    completo nessuno dei quattro deve comparire nell'output."""
    import run_property_watch_valuation_cron as cron

    personali = righe(mondo, "SELECT nome, cognome, email, telefono, via, civico "
                             "FROM stime WHERE id=%s", (mondo["st_a"],))[0]
    assert cron.main(["--all-agencies"]) == 0
    uscita = capsys.readouterr().out.lower()
    for valore in personali:
        if valore:
            assert str(valore).lower() not in uscita, valore
    for vietato in ("price_exact", "eur_mq", "payload", "token", "digest"):
        assert vietato not in uscita, vietato


# ---------------------------------------------------------------------------
# I - PAGINAZIONE KEYSET: NESSUN WATCH RESTA INDIETRO
#
# Il difetto che questa revisione chiude non era un rallentamento: con 700
# watch e un tetto di 500, i watch dal 501 al 700 non venivano rivalutati
# MAI - l'ordine e' deterministico, quindi ogni notte il giro rileggeva gli
# stessi primi 500. E il riepilogo diceva `processed=500 failed=0`, il che
# era vero: e' proprio questo che rendeva la fame invisibile.
#
# Qui si costruiscono centinaia di watch veri e si conta. Non su un doppio:
# il cursore, l'ordine e l'assenza di sovrapposizioni sono affermazioni sul
# comportamento di PostgreSQL sotto una finestra che si muove.
# ---------------------------------------------------------------------------

def _molti_watch(db, mondo, quanti, agency_id=None):
    """`quanti` watch attivi, creati in blocco. Ritorna gli id, in ordine."""
    agency_id = agency_id or mondo["a"]
    conn = db["conn"]
    conn.rollback()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO stime (agency_id, comune, microzona, tipologia, mq, piano,
                               locali, bagni, pertinenze, ascensore, anno, stato,
                               posizionemare, distanzamare, barrieramare,
                               vistamareyn, vistamaredettaglio, vistamare,
                               mqgiardino, mqgarage, mqcantina, mqpostoauto,
                               mqtaverna, mqsoffitta, mqterrazzo, numbalconi,
                               via, civico, altrodescrizione)
            SELECT %s, 'Alba Adriatica', 'Villa Fiore', 'Appartamento',
                   60 + (g %% 40), '2', 3, 1, 'cantina', 'False', 2000, 'buono',
                   'seconda', '100-300', 'no', 'no', '', '',
                   0, 0, 5, 0, 0, 0, 0, 1, 'Via Massiva', g::text, ''
              FROM generate_series(1, %s) AS g
            RETURNING id
            """,
            (agency_id, quanti),
        )
        stime = [r[0] for r in cur.fetchall()]
        cur.execute(
            """
            INSERT INTO property_watches (stima_id, status, agency_id)
            SELECT s, 'active', %s FROM unnest(%s::int[]) AS s
            RETURNING id
            """,
            (agency_id, stime),
        )
        watch = sorted(r[0] for r in cur.fetchall())
    conn.commit()
    return watch


def _watch_con_snapshot(mondo, agency_id=None):
    """Gli id dei watch che hanno almeno uno snapshot, in ordine."""
    return sorted(r[0] for r in righe(mondo, """
        SELECT DISTINCT w.id FROM property_watches w
        JOIN property_watch_observations o ON o.watch_id = w.id
        WHERE o.observation_type = 'valuation_snapshot'
          AND (%s IS NULL OR w.agency_id = %s)
    """, (agency_id, agency_id)))


def test_i1_501_watch_con_pagine_da_500_li_processa_tutti(db, mondo, modulo):
    """TEST 1 - il caso esatto del difetto, al minimo che lo mostra."""
    creati = _molti_watch(db, mondo, 500)          # + `w_a` della fixture = 501
    attesi = sorted([mondo["w_a"], *creati])
    assert len(attesi) == 501

    esito = modulo["pw"].refresh_valuation_snapshots_for_agency(
        mondo["a"], page_size=500, now=OGGI)

    assert esito["processed"] == 501, esito
    assert esito["created"] == 501
    assert _watch_con_snapshot(mondo, mondo["a"]) == attesi
    assert attesi[-1] in _watch_con_snapshot(mondo, mondo["a"]), \
        "l'ultimo watch, quello che prima restava sempre fuori"


def test_i2_1200_watch_con_pagine_da_500_li_processa_tutti(db, mondo, modulo):
    """TEST 2 - tre pagine, la terza parziale."""
    creati = _molti_watch(db, mondo, 1199)
    attesi = sorted([mondo["w_a"], *creati])
    assert len(attesi) == 1200

    esito = modulo["pw"].refresh_valuation_snapshots_for_agency(
        mondo["a"], page_size=500, now=OGGI)

    assert esito["processed"] == 1200, esito
    assert esito["created"] == 1200
    assert esito["failed"] == 0 and esito["skipped"] == 0
    assert _watch_con_snapshot(mondo, mondo["a"]) == attesi


def test_i3_i4_ordine_deterministico_e_nessun_duplicato(db, mondo, modulo):
    """TEST 3 e 4 - sulla sequenza di pagine, non sul risultato finale."""
    creati = _molti_watch(db, mondo, 120)
    attesi = sorted([mondo["w_a"], *creati])

    visti, dopo, pagine = [], 0, 0
    while True:
        pagina = modulo["pw_repository"].list_active_watch_page_for_agency(
            mondo["a"], after_watch_id=dopo, page_size=25)
        if not pagina:
            break
        pagine += 1
        ids = [b["watch_id"] for b in pagina]
        assert ids == sorted(ids), f"pagina {pagine} fuori ordine: {ids}"
        assert min(ids) > dopo, "il cursore non e' stato rispettato"
        assert not (set(ids) & set(visti)), f"duplicati fra pagine: {set(ids) & set(visti)}"
        visti.extend(ids)
        dopo = ids[-1]

    assert visti == attesi
    assert visti == sorted(visti)
    assert len(visti) == len(set(visti))
    assert pagine == 5, pagine


def test_i5_un_watch_che_fallisce_non_ferma_le_pagine_successive(db, mondo, modulo,
                                                                 monkeypatch):
    """TEST 5 - il guasto e' nella PRIMA pagina, e tutto il resto deve andare.

    E' il caso che il cursore deve reggere: se avanzasse solo sui successi,
    il watch rotto verrebbe riletto all'infinito nella stessa pagina.
    """
    creati = _molti_watch(db, mondo, 599)
    attesi = sorted([mondo["w_a"], *creati])
    rotto = attesi[249]                       # il 250esimo, in prima pagina
    vero = modulo["pw"].refresh_valuation_snapshot_scoped
    per_watch = dict(righe(mondo, "SELECT id, stima_id FROM property_watches "
                                  "WHERE agency_id=%s", (mondo["a"],)))

    def capriccioso(ctx, stima_id, *, reason, now=None):
        if stima_id == per_watch[rotto]:
            raise RuntimeError("motore rotto per questa casa")
        return vero(ctx, stima_id, reason=reason, now=now)

    monkeypatch.setattr(modulo["pw"], "refresh_valuation_snapshot_scoped", capriccioso)
    esito = modulo["pw"].refresh_valuation_snapshots_for_agency(
        mondo["a"], page_size=100, now=OGGI)

    assert esito["processed"] == 600, esito
    assert esito["failed"] == 1
    assert esito["created"] == 599
    con_snapshot = _watch_con_snapshot(mondo, mondo["a"])
    assert rotto not in con_snapshot
    assert con_snapshot == [w for w in attesi if w != rotto]
    # E il watch fallito resta attivo: un guasto non toglie di mezzo la casa.
    assert righe(mondo, "SELECT status FROM property_watches WHERE id=%s",
                 (rotto,))[0][0] == "active"


def test_i6_due_tenant_con_piu_pagine_ciascuno(db, mondo, modulo):
    """TEST 6 - e nessuno dei due vede i watch dell'altro."""
    a_creati = _molti_watch(db, mondo, 249, agency_id=mondo["a"])
    b_creati = _molti_watch(db, mondo, 300, agency_id=mondo["b"])
    attesi_a = sorted([mondo["w_a"], *a_creati])
    attesi_b = sorted([mondo["w_b"], *b_creati])

    esito = modulo["pw"].refresh_valuation_snapshots_for_all_agencies(
        page_size=100, now=OGGI)

    assert esito["agencies"] == 2
    assert esito["processed"] == 250 + 301
    assert esito["created"] == 551
    per_agenzia = {r["agency_id"]: r for r in esito["runs"]}
    assert per_agenzia[mondo["a"]]["processed"] == 250
    assert per_agenzia[mondo["b"]]["processed"] == 301
    assert _watch_con_snapshot(mondo, mondo["a"]) == attesi_a
    assert _watch_con_snapshot(mondo, mondo["b"]) == attesi_b


def test_i7_un_watch_disattivato_durante_il_giro_non_viene_processato(db, mondo, modulo,
                                                                      monkeypatch):
    """TEST 7 - l'eleggibilita' si rilegge a ogni pagina.

    Mentre la prima pagina viene elaborata, un watch della SECONDA viene
    disattivato: quando la seconda pagina viene chiesta, quel watch non c'e'
    piu'. Una lista presa tutta all'inizio lo avrebbe lavorato lo stesso, su
    uno stato vecchio.
    """
    creati = _molti_watch(db, mondo, 29)
    attesi = sorted([mondo["w_a"], *creati])
    da_spegnere = attesi[20]                  # in seconda pagina, con page_size=10
    conn = db["conn"]
    vero = modulo["pw"].refresh_valuation_snapshot_scoped
    fatti = {"n": 0}

    def con_effetto(ctx, stima_id, *, reason, now=None):
        fatti["n"] += 1
        if fatti["n"] == 5:
            conn.rollback()
            with conn.cursor() as cur:
                cur.execute("UPDATE property_watches SET status='paused' WHERE id=%s",
                            (da_spegnere,))
            conn.commit()
        return vero(ctx, stima_id, reason=reason, now=now)

    monkeypatch.setattr(modulo["pw"], "refresh_valuation_snapshot_scoped", con_effetto)
    esito = modulo["pw"].refresh_valuation_snapshots_for_agency(
        mondo["a"], page_size=10, now=OGGI)

    assert esito["processed"] == 29, esito
    assert da_spegnere not in _watch_con_snapshot(mondo, mondo["a"])
    assert _watch_con_snapshot(mondo, mondo["a"]) == [w for w in attesi if w != da_spegnere]


def test_i8_il_giro_termina_e_non_cicla(db, mondo, modulo):
    """TEST 8 - senza timeout artificiali: si contano le letture.

    Con 25 watch e pagine da 10 le letture devono essere 3 (10, 10, 5) e non
    una di piu': una pagina non piena chiude il ciclo. Se il cursore non
    avanzasse, questo contatore crescerebbe senza fine e il test non
    finirebbe - quindi c'e' anche un tetto che lo fa fallire invece di
    appendersi.
    """
    _molti_watch(db, mondo, 24)
    vero = modulo["pw_repository"].list_active_watch_page_for_agency
    letture = {"n": 0}

    def contata(agency_id, *, after_watch_id=0, page_size):
        letture["n"] += 1
        if letture["n"] > 20:
            raise AssertionError("il ciclo non termina: cursore fermo?")
        return vero(agency_id, after_watch_id=after_watch_id, page_size=page_size)

    import property_watch.repository as repo
    originale = repo.list_active_watch_page_for_agency
    repo.list_active_watch_page_for_agency = contata
    try:
        esito = modulo["pw"].refresh_valuation_snapshots_for_agency(
            mondo["a"], page_size=10, now=OGGI)
    finally:
        repo.list_active_watch_page_for_agency = originale

    assert esito["processed"] == 25
    assert letture["n"] == 3, letture["n"]


def test_i9_i10_i_contatori_sommano_su_tutte_le_pagine(db, mondo, modulo, monkeypatch):
    """TEST 9 e 10 - `processed` conta tutti i watch tentati, e i quattro
    esiti sommano attraverso le pagine."""
    from property_watch.exceptions import StimaNotFoundError

    creati = _molti_watch(db, mondo, 29)
    attesi = sorted([mondo["w_a"], *creati])
    per_watch = dict(righe(mondo, "SELECT id, stima_id FROM property_watches "
                                  "WHERE agency_id=%s", (mondo["a"],)))
    saltati = {per_watch[attesi[3]], per_watch[attesi[15]], per_watch[attesi[27]]}
    rotti = {per_watch[attesi[7]], per_watch[attesi[22]]}

    # Un primo giro parziale per creare due `reused` nel secondo.
    gia_fatti = {per_watch[attesi[0]], per_watch[attesi[1]]}
    vero = modulo["pw"].refresh_valuation_snapshot_scoped
    for stima_id in gia_fatti:
        vero(Scope(mondo["a"]), stima_id, reason=modulo["pw"].CRON_REFRESH_REASON,
             now=OGGI)

    def misto(ctx, stima_id, *, reason, now=None):
        if stima_id in saltati:
            raise StimaNotFoundError("non raggiungibile")
        if stima_id in rotti:
            raise RuntimeError("guasto vero")
        return vero(ctx, stima_id, reason=reason, now=now)

    monkeypatch.setattr(modulo["pw"], "refresh_valuation_snapshot_scoped", misto)
    esito = modulo["pw"].refresh_valuation_snapshots_for_agency(
        mondo["a"], page_size=7, now=OGGI)

    assert esito["processed"] == 30, esito
    assert esito["skipped"] == 3, esito
    assert esito["failed"] == 2, esito
    assert esito["reused"] == 2, esito
    assert esito["created"] == 30 - 3 - 2 - 2, esito
    assert (esito["created"] + esito["reused"] + esito["skipped"]
            + esito["failed"]) == esito["processed"]


def test_i11_il_runner_vero_arriva_in_fondo(db, mondo, modulo, capsys):
    """Il comando che Render eseguira', con piu' pagine per davvero."""
    import run_property_watch_valuation_cron as cron

    creati = _molti_watch(db, mondo, 249)
    monkeypatch_env = os.environ.get("PROPERTY_WATCH_VALUATION_LIMIT")
    os.environ["PROPERTY_WATCH_VALUATION_LIMIT"] = "100"
    try:
        assert cron.main(["--agency-id", str(mondo["a"])]) == 0
    finally:
        if monkeypatch_env is None:
            os.environ.pop("PROPERTY_WATCH_VALUATION_LIMIT", None)
        else:
            os.environ["PROPERTY_WATCH_VALUATION_LIMIT"] = monkeypatch_env

    uscita = capsys.readouterr().out
    assert "processed=250" in uscita, uscita
    assert "created=250" in uscita
    assert len(_watch_con_snapshot(mondo, mondo["a"])) == 250


def test_i5b_un_guasto_in_FONDO_alla_pagina_non_fa_ciclare(db, mondo, modulo,
                                                           monkeypatch):
    """IL CASO CHE IL CURSORE DEVE REGGERE, e che il test 5 da solo non copre.

    Un watch che fallisce a META' pagina non dimostra niente: gli elementi
    successivi della stessa pagina portano comunque il cursore oltre di lui.
    Il caso pericoloso e' il guasto sull'ULTIMO elemento della pagina: se il
    cursore avanzasse solo sui successi, si fermerebbe al penultimo, la
    pagina dopo ricomincerebbe dal watch rotto, e con un guasto persistente
    il giro non finirebbe mai - di notte, senza nessuno a guardarlo.

    Qui il cursore avanza PRIMA di elaborare la pagina, quindi il giro passa
    oltre e arriva in fondo. Il contatore di letture fa fallire il test
    invece di lasciarlo appeso.
    """
    creati = _molti_watch(db, mondo, 29)
    attesi = sorted([mondo["w_a"], *creati])
    per_watch = dict(righe(mondo, "SELECT id, stima_id FROM property_watches "
                                  "WHERE agency_id=%s", (mondo["a"],)))
    # Ultimo elemento della prima pagina con page_size=10.
    in_fondo = attesi[9]
    vero = modulo["pw"].refresh_valuation_snapshot_scoped

    def capriccioso(ctx, stima_id, *, reason, now=None):
        if stima_id == per_watch[in_fondo]:
            raise RuntimeError("fallisce sempre, e sta in fondo alla pagina")
        return vero(ctx, stima_id, reason=reason, now=now)

    monkeypatch.setattr(modulo["pw"], "refresh_valuation_snapshot_scoped", capriccioso)

    import property_watch.repository as repo
    originale = repo.list_active_watch_page_for_agency
    letture = {"n": 0}

    def contata(agency_id, *, after_watch_id=0, page_size):
        letture["n"] += 1
        if letture["n"] > 10:
            raise AssertionError(
                "il giro non termina: il cursore si e' fermato sul watch rotto")
        return originale(agency_id, after_watch_id=after_watch_id, page_size=page_size)

    repo.list_active_watch_page_for_agency = contata
    try:
        esito = modulo["pw"].refresh_valuation_snapshots_for_agency(
            mondo["a"], page_size=10, now=OGGI)
    finally:
        repo.list_active_watch_page_for_agency = originale

    # Quattro letture e non tre: 30 watch in pagine da 10 fanno tre pagine
    # PIENE, quindi la scorciatoia "pagina non piena" non scatta e la
    # condizione di uscita vera - la pagina vuota - richiede una lettura in
    # piu'. E' il prezzo di un numero che e' un multiplo esatto, non un
    # giro di troppo.
    assert letture["n"] == 4, letture["n"]
    assert esito["processed"] == 30, esito
    assert esito["failed"] == 1
    assert esito["created"] == 29
    assert in_fondo not in _watch_con_snapshot(mondo, mondo["a"])
    assert _watch_con_snapshot(mondo, mondo["a"]) == [w for w in attesi if w != in_fondo]
