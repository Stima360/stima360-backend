"""PW-FIX - il Property Watch del funnel pubblico, su PostgreSQL reale.

PERCHE' QUESTO FILE ESISTE

`tests/test_p20_property_watch.py` prova `ensure_watch_with_baseline` con un
cursore finto che accetta qualunque INSERT. Quel doppio non sa niente della
046 (colonna `property_watches.agency_id`) ne' della 048 (NOT NULL, nessun
default, trigger che VERIFICA la coerenza ma non deriva l'agenzia). Da quando la
048 e' applicata, la INSERT ctx-less che il funnel pubblico esegue attraverso
`safe_ensure_watch_for_stima` e' rifiutata dal database, e `safe_ensure` la
inghiotte: nessun watch, nessuna baseline, nessun errore visibile.

Qui la funzione REALMENTE usata dal funnel gira contro lo schema REALE di
022 + 046 + 047 + 048, e le domande sono quelle che solo un database sa dire:
la riga esiste? porta l'agenzia della stima? la seconda chiamata ne crea una
seconda? il trigger di P26-6B continua a rifiutare un'agenzia sbagliata?

Opt-in: senza `P29_TEST_DSN` si salta tutto, esattamente come i moduli P29 su
PostgreSQL. Il database e' usa-e-getta, creato e cancellato qui; non si passa
da `database.get_connection()`, che e' il choke point dell'applicazione.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest

DSN = os.getenv("P29_TEST_DSN")

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare 048")

ROOT = Path(__file__).resolve().parents[1]
MIGRAZIONI = ROOT / "migrations"

# Le tabelle storiche che le migration non creano (nascono fuori dalla catena,
# vedi tests/test_p27_6_postgres_real.py) e che 022-025 e 046-048 referenziano.
SCHEMA_MINIMO = """
CREATE TABLE agencies (
    id BIGSERIAL PRIMARY KEY, slug VARCHAR(80) NOT NULL UNIQUE,
    status VARCHAR(20) NOT NULL DEFAULT 'active');
CREATE TABLE contacts (id BIGSERIAL PRIMARY KEY, agency_id BIGINT);
CREATE TABLE leads (id BIGSERIAL PRIMARY KEY, agency_id BIGINT);
CREATE TABLE buy_requests (id SERIAL PRIMARY KEY, agency_id BIGINT);
CREATE TABLE matches (id SERIAL PRIMARY KEY, buy_request_id INTEGER);
CREATE TABLE stime (
    id SERIAL PRIMARY KEY,
    agency_id BIGINT NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,
    comune VARCHAR(100), microzona VARCHAR(100), tipologia VARCHAR(50),
    mq INTEGER, prezzo_mq_base NUMERIC(10,2),
    data TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE seller_timeline_events (
    id BIGSERIAL PRIMARY KEY,
    stima_id INTEGER REFERENCES stime(id) ON DELETE SET NULL,
    event_type VARCHAR(50) NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
"""

#: Le migration che disegnano property_watches cosi' com'e' oggi, in ordine.
#: 023-025 servono solo perche' 046-048 ne verificano/alterano le tabelle.
CATENA = (
    "022_property_watch",
    "023_invisible_sale",
    "024_next_best_action",
    "025_seller_revival_suppressions",
    "046_p26_intelligence_agency_columns",
    "047_p26_intelligence_agency_backfill",
    "048_p26_intelligence_agency_enforce",
)

BASELINE = {
    "comune": "Alba Adriatica",
    "microzona": "Lungomare",
    "tipologia": "Appartamento",
    "mq": 80,
    "prezzo_mq_base": 1500.0,
    "price_exact": 210000,
    "eur_mq_finale": 2625.0,
    "base_mq": 1500,
}


def _dsn_per(nome: str) -> str:
    if "?" in DSN:
        base, query = DSN.split("?", 1)
        return base.rsplit("/", 1)[0] + "/" + nome + "?" + query
    return DSN.rsplit("/", 1)[0] + "/" + nome


@pytest.fixture(scope="module")
def db():
    psycopg2 = pytest.importorskip("psycopg2")

    nome = f"p20_pw_probe_{os.getpid()}_{uuid.uuid4().hex[:6]}"
    servizio = psycopg2.connect(DSN)
    servizio.autocommit = True
    with servizio.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{nome}"')

    dsn = _dsn_per(nome)
    conn = psycopg2.connect(dsn)
    # Autocommit durante lo schema: 022/024/025 portano il proprio
    # BEGIN/COMMIT (era pre-runner), 046-048 sono runner-owned e non lo hanno.
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_MINIMO)
            for versione in CATENA:
                cur.execute((MIGRAZIONI / f"{versione}.sql").read_text(encoding="utf-8"))
        conn.autocommit = False
        yield {"conn": conn, "dsn": dsn}
    finally:
        conn.close()
        with servizio.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{nome}"')
        servizio.close()


@pytest.fixture
def modulo(db, monkeypatch):
    """Il modulo Property Watch, con il suo choke point puntato sul database
    usa-e-getta. Ogni chiamata apre una connessione propria, come in
    produzione: `property_watch_cursor` la chiude alla fine."""
    import psycopg2

    from property_watch import database as pw_database
    from property_watch import repository, service

    monkeypatch.setattr(pw_database, "get_connection", lambda: psycopg2.connect(db["dsn"]))
    return {"repository": repository, "service": service}


@pytest.fixture
def mondo(db):
    """Due agenzie, una stima in A con la sua valutazione completata."""
    conn = db["conn"]
    with conn.cursor() as cur:
        cur.execute("DELETE FROM property_watch_observations")
        cur.execute("DELETE FROM property_watches")
        cur.execute("DELETE FROM seller_timeline_events")
        cur.execute("DELETE FROM stime")
        cur.execute("DELETE FROM agencies")
        cur.execute("INSERT INTO agencies (slug) VALUES ('a-uno') RETURNING id")
        a = cur.fetchone()[0]
        cur.execute("INSERT INTO agencies (slug) VALUES ('b-due') RETURNING id")
        b = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO stime (agency_id, comune, microzona, tipologia, mq, prezzo_mq_base) "
            "VALUES (%s, 'Alba Adriatica', 'Lungomare', 'Appartamento', 80, 1500.00) RETURNING id",
            (a,),
        )
        st = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO seller_timeline_events (stima_id, event_type, payload) "
            "VALUES (%s, 'stima_completata', %s)",
            (st, json.dumps({"price_exact": 210000, "eur_mq_finale": 2625.0, "base_mq": 1500})),
        )
    conn.commit()
    return {"conn": conn, "a": a, "b": b, "st": st}


def _watches(mondo, stima_id):
    with mondo["conn"].cursor() as cur:
        cur.execute(
            "SELECT id, agency_id, status FROM property_watches WHERE stima_id = %s ORDER BY id",
            (stima_id,),
        )
        return cur.fetchall()


def _osservazioni(mondo, watch_id):
    with mondo["conn"].cursor() as cur:
        cur.execute(
            "SELECT observation_type, source, payload, idempotency_key "
            "FROM property_watch_observations WHERE watch_id = %s ORDER BY id",
            (watch_id,),
        )
        return cur.fetchall()


def _prova(mondo, sql, params=()):
    """Esegue e riporta l'esito senza lasciare la transazione sporca."""
    try:
        with mondo["conn"].cursor() as cur:
            cur.execute(sql, params)
        mondo["conn"].commit()
        return None
    except Exception as exc:
        mondo["conn"].rollback()
        return exc


# ---------------------------------------------------------------------------
# Lo schema e' davvero quello di 048.
# ---------------------------------------------------------------------------

def test_0_lo_schema_e_quello_di_048(mondo):
    with mondo["conn"].cursor() as cur:
        cur.execute(
            "SELECT is_nullable, column_default FROM information_schema.columns "
            "WHERE table_name = 'property_watches' AND column_name = 'agency_id'")
        is_nullable, default = cur.fetchone()
        cur.execute(
            "SELECT tgname FROM pg_trigger WHERE tgrelid = 'public.property_watches'::regclass "
            "AND NOT tgisinternal")
        trigger = {r[0] for r in cur.fetchall()}
    assert is_nullable == "NO"
    assert default is None
    assert "trg_property_watches_agency_integrity" in trigger


# ---------------------------------------------------------------------------
# Il bug, e la sua correzione: la funzione del funnel contro il database.
# ---------------------------------------------------------------------------

def test_1_il_funnel_crea_il_watch_con_l_agenzia_della_stima(mondo, modulo):
    """RED prima del fix: la INSERT senza agency_id viola il NOT NULL di 048."""
    esito = modulo["repository"].ensure_watch_with_baseline(mondo["st"], dict(BASELINE))

    righe = _watches(mondo, mondo["st"])
    assert len(righe) == 1
    watch_id, agency_id, status = righe[0]
    assert agency_id == mondo["a"], "l'agenzia del watch deve essere quella della stima"
    assert status == "active"
    assert esito["watch"]["id"] == watch_id
    assert esito["watch"]["agency_id"] == mondo["a"]


def test_2_la_baseline_watch_started_nasce_normalmente(mondo, modulo):
    esito = modulo["repository"].ensure_watch_with_baseline(mondo["st"], dict(BASELINE))

    osservazioni = _osservazioni(mondo, esito["watch"]["id"])
    assert len(osservazioni) == 1
    tipo, sorgente, payload, chiave = osservazioni[0]
    assert tipo == "watch_started"
    assert sorgente == "internal"
    assert payload == BASELINE
    assert chiave == f"property_watch:watch_started:stima:{mondo['st']}:v1"
    assert esito["baseline"]["observation_type"] == "watch_started"


def test_3_la_seconda_chiamata_e_idempotente(mondo, modulo):
    primo = modulo["repository"].ensure_watch_with_baseline(mondo["st"], dict(BASELINE))
    secondo = modulo["repository"].ensure_watch_with_baseline(
        mondo["st"], {**BASELINE, "price_exact": 999999})

    assert secondo["watch"]["id"] == primo["watch"]["id"]
    assert secondo["watch"]["agency_id"] == mondo["a"]
    assert secondo["baseline"]["id"] == primo["baseline"]["id"]
    assert len(_watches(mondo, mondo["st"])) == 1, "nessun secondo watch"

    osservazioni = _osservazioni(mondo, primo["watch"]["id"])
    assert len(osservazioni) == 1, "nessuna seconda baseline"
    assert osservazioni[0][2]["price_exact"] == 210000, "la baseline originaria resta quella"


def test_4_il_servizio_del_funnel_non_fallisce_piu(mondo, modulo):
    """`safe_ensure_watch_for_stima` e' cio' che `/api/salva_stima` chiama:
    legge la stima e la valutazione completata dal database e apre il watch.
    Prima del fix ritornava None inghiottendo NotNullViolation."""
    esito = modulo["service"].safe_ensure_watch_for_stima(mondo["st"])

    assert esito is not None
    assert esito["watch"]["agency_id"] == mondo["a"]
    assert esito["baseline"]["payload"]["price_exact"] == 210000
    assert esito["baseline"]["payload"]["prezzo_mq_base"] == 1500.0
    assert len(_watches(mondo, mondo["st"])) == 1


def test_5_una_stima_inesistente_non_scrive_niente(mondo, modulo):
    """Comportamento storico, invariato dal PW-FIX: nessuna riga da inserire e
    nessuna da rileggere e' il `RuntimeError` di conflitto di sempre."""
    inesistente = mondo["st"] + 1000
    with pytest.raises(RuntimeError, match="property watch conflict"):
        modulo["repository"].ensure_watch_with_baseline(inesistente, dict(BASELINE))
    assert _watches(mondo, inesistente) == []
    with mondo["conn"].cursor() as cur:
        cur.execute("SELECT count(*) FROM property_watch_observations")
        assert cur.fetchone()[0] == 0


# ---------------------------------------------------------------------------
# Il trigger P26-6B resta la difesa del database: il fix non lo aggira.
# ---------------------------------------------------------------------------

def test_6_il_trigger_rifiuta_ancora_un_agenzia_diversa_dalla_stima(mondo):
    esito = _prova(
        mondo,
        "INSERT INTO property_watches (stima_id, status, agency_id) VALUES (%s, 'active', %s)",
        (mondo["st"], mondo["b"]),
    )
    assert esito is not None
    assert "P26-6B watch integrity" in str(esito)
    assert _watches(mondo, mondo["st"]) == []


def test_7_il_trigger_rifiuta_ancora_di_spostare_un_watch(mondo, modulo):
    esito = modulo["repository"].ensure_watch_with_baseline(mondo["st"], dict(BASELINE))
    spostamento = _prova(
        mondo,
        "UPDATE property_watches SET agency_id = %s WHERE id = %s",
        (mondo["b"], esito["watch"]["id"]),
    )
    assert spostamento is not None
    assert "P26-6B watch integrity" in str(spostamento)
    assert _watches(mondo, mondo["st"])[0][1] == mondo["a"]
