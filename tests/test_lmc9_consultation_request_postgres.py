"""LMC-9 su PostgreSQL reale: la richiesta di verifica gratuita.

Cio' che solo un database sa dire: che un doppio invio nello stesso giorno
lasci una riga sola, che il giorno dopo ne nasca una nuova, che un accesso
che non c'e' non scriva niente e risponda come tutti gli altri accessi che
non ci sono, e - la cosa che conta di piu' - che quando la scrittura
fallisce il proprietario NON si senta dire "fatto".

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
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare LMC-9")

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

    nome = f"lmc9_probe_{os.getpid()}_{uuid.uuid4().hex[:6]}"
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
    from owner import home_service, interest_service, tracking
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
            "interest": interest_service}


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


CONSULTATION = "owner_consultation_requested"


class ScopeOperatore:
    def __init__(self, agency_id):
        self._a = agency_id

    def require_agency(self):
        return self._a


def eventi(mondo, tipo=None):
    sql = ("SELECT event_type, event_source, agency_id, contact_id, lead_id, "
           "stima_id, payload, idempotency_key FROM seller_timeline_events")
    params = ()
    if tipo is not None:
        sql += " WHERE event_type = %s"
        params = (tipo,)
    return righe(mondo, sql + " ORDER BY id", params)


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


def chiedi(client, stima_id):
    return client.post(f"/api/owner/portal/homes/{stima_id}/consultation-request")


# ---------------------------------------------------------------------------
# 1-4: la richiesta riuscita, e l'idempotenza
# ---------------------------------------------------------------------------

def test_1_una_richiesta_valida_registra_l_evento(mondo, modulo, client):
    risposta = chiedi(client, mondo["st_a"])
    assert risposta.status_code == 200, risposta.text
    assert risposta.json() == {"status": "recorded"}

    righe_evento = eventi(mondo, CONSULTATION)
    assert len(righe_evento) == 1
    tipo, source, agency, contact, lead, stima, payload, chiave = righe_evento[0]
    assert source == "owner_portal"
    assert agency == mondo["a"]
    assert contact == mondo["contact"]
    assert lead == mondo["lead_sell"]
    assert stima == mondo["st_a"]
    assert payload == {"action": "consultation_requested"}
    assert chiave.startswith("owner_portal:owner_consultation_requested:owner:")
    assert chiave.endswith(datetime.now(timezone.utc).date().isoformat())


def test_2_doppio_invio_stesso_giorno_una_riga_sola(mondo, modulo, client):
    prima = chiedi(client, mondo["st_a"])
    righe_prima = eventi(mondo, CONSULTATION)
    for _ in range(4):
        assert chiedi(client, mondo["st_a"]).status_code == 200
    dopo = eventi(mondo, CONSULTATION)
    assert prima.status_code == 200
    assert len(dopo) == 1, "una richiesta, non cinque"
    assert dopo == righe_prima, "e la prima riga non viene toccata"


def test_3_il_giorno_dopo_e_una_richiesta_nuova(mondo, modulo, client):
    ieri = datetime.now(timezone.utc) - timedelta(days=1)
    modulo["tracking"].track_consultation_request(mondo["acc"], mondo["st_a"], when=ieri)
    assert chiedi(client, mondo["st_a"]).status_code == 200
    righe_evento = eventi(mondo, CONSULTATION)
    assert len(righe_evento) == 2
    assert len({r[7] for r in righe_evento}) == 2, "due chiavi, due giorni"


def test_4_il_payload_non_porta_niente_di_personale(mondo, modulo, client):
    chiedi(client, mondo["st_a"])
    testo = json.dumps([r[6] for r in eventi(mondo)]).lower()
    for vietato in ("mario", "rossi", "@example.it", "333", "ip", "agent",
                    "token", "budget", "score", "pressure"):
        assert vietato not in testo, vietato


# ---------------------------------------------------------------------------
# 5-9: l'accesso che non c'e', sempre uguale
# ---------------------------------------------------------------------------

def test_5_stima_inesistente_404_neutro_e_zero_eventi(mondo, modulo, client):
    risposta = chiedi(client, mondo["st_a"] + 99_000)
    assert risposta.status_code == 404
    assert risposta.json() == {"detail": "Risorsa non trovata"}
    assert eventi(mondo) == []


def test_6_stima_di_un_altro_owner_404_neutro(mondo, modulo, client):
    with mondo["conn"].cursor() as cur:
        cur.execute("INSERT INTO contacts (agency_id, display_name) VALUES (%s,'Estraneo') "
                    "RETURNING id", (mondo["a"],))
        contatto = cur.fetchone()[0]
        cur.execute("INSERT INTO owner_accounts (contact_id,status) VALUES (%s,'active') "
                    "RETURNING id", (contatto,))
        client.owner["account"] = cur.fetchone()[0]
    mondo["conn"].commit()
    assert chiedi(client, mondo["st_a"]).status_code == 404
    assert eventi(mondo) == []


def test_7_altro_tenant_404_neutro(mondo, modulo, client):
    with mondo["conn"].cursor() as cur:
        cur.execute("INSERT INTO contacts (agency_id, display_name) VALUES (%s,'Lucia') "
                    "RETURNING id", (mondo["b"],))
        contatto_b = cur.fetchone()[0]
        cur.execute("INSERT INTO owner_accounts (contact_id,status) VALUES (%s,'active') "
                    "RETURNING id", (contatto_b,))
        acc_b = cur.fetchone()[0]
        cur.execute("INSERT INTO owner_stima_access (owner_account_id,stima_id,granted_by) "
                    "VALUES (%s,%s,'P')", (acc_b, mondo["st_b"]))
    mondo["conn"].commit()
    client.owner["account"] = acc_b
    assert chiedi(client, mondo["st_a"]).status_code == 404
    assert eventi(mondo) == []


@pytest.mark.parametrize("caso,rottura", [
    ("revoked", "UPDATE owner_stima_access SET access_status='revoked', revoked_at=NOW()"),
    ("expired", "UPDATE owner_stima_access SET valid_until = NOW() - INTERVAL '1 day', "
                "valid_from = NOW() - INTERVAL '2 days'"),
])
def test_8_e_9_grant_non_valido_404_e_zero_eventi(mondo, modulo, client, caso, rottura):
    with mondo["conn"].cursor() as cur:
        cur.execute(rottura)
    mondo["conn"].commit()
    assert chiedi(client, mondo["st_a"]).status_code == 404, caso
    assert eventi(mondo) == [], caso


def test_9b_i_quattro_rifiuti_sono_indistinguibili(mondo, modulo, client):
    risposte = [chiedi(client, s) for s in
                (mondo["st_b"], mondo["st_senza_watch"] + 50_000, mondo["st_a"] + 99_000)]
    assert {r.status_code for r in risposte} == {404}
    assert {r.text for r in risposte} == {'{"detail":"Risorsa non trovata"}'}


# ---------------------------------------------------------------------------
# 10-12: IL PUNTO DELLA FASE. Un guasto non diventa un successo.
# ---------------------------------------------------------------------------

def test_10_se_seller_intelligence_fallisce_la_risposta_non_e_un_successo(
        mondo, modulo, client, monkeypatch):
    from seller_intelligence import service as si

    def esplode(*_a, **_k):
        raise RuntimeError("seller intelligence giu'")

    monkeypatch.setattr(si, "record_event_scoped", esplode)
    risposta = chiedi(client, mondo["st_a"])

    assert risposta.status_code == 503, risposta.status_code
    assert risposta.status_code != 200, "mai un falso 'fatto'"
    assert eventi(mondo) == []
    corpo = risposta.text.lower()
    for tecnico in ("traceback", "seller intelligence", "runtimeerror", "insert",
                    "select", "psycopg", "sql"):
        assert tecnico not in corpo, tecnico
    assert "riprova" in corpo


def test_11_un_guasto_del_database_non_diventa_un_404(mondo, modulo, client, monkeypatch):
    """Il 404 significa "non tua" e chiuderebbe il discorso; il 503 dice
    "riprova", che e' la verita'."""
    from owner import repository as owner_repository

    def esplode(*_a, **_k):
        raise RuntimeError("database giu'")

    monkeypatch.setattr(owner_repository, "home_tracking_context", esplode)
    risposta = chiedi(client, mondo["st_a"])
    assert risposta.status_code == 503
    assert eventi(mondo) == []


def test_12_dopo_un_errore_la_richiesta_si_puo_rifare(mondo, modulo, client, monkeypatch):
    from seller_intelligence import service as si

    vero = si.record_event_scoped
    rotto = {"si": True}

    def forse(*a, **k):
        if rotto["si"]:
            raise RuntimeError("giu'")
        return vero(*a, **k)

    monkeypatch.setattr(si, "record_event_scoped", forse)
    assert chiedi(client, mondo["st_a"]).status_code == 503
    assert eventi(mondo) == []

    rotto["si"] = False
    assert chiedi(client, mondo["st_a"]).status_code == 200
    assert len(eventi(mondo, CONSULTATION)) == 1


# ---------------------------------------------------------------------------
# 13-15: l'effetto sul radar e sul CRM, senza ricalcoli
# ---------------------------------------------------------------------------

def test_13_il_radar_diventa_high_con_la_sola_richiesta(mondo, modulo, client):
    assert chiedi(client, mondo["st_a"]).status_code == 200
    vista = modulo["interest"].interest_for_stima_scoped(
        ScopeOperatore(mondo["a"]), mondo["st_a"])["interest"]
    assert vista["consultation_requested"] is True
    assert vista["level"] == "high"
    assert vista["reasons"][0] == "Ha richiesto una verifica gratuita"
    assert vista["home_viewed_days_30d"] == 0, "non ha nemmeno aperto la scheda"


def test_14_il_crm_lo_riflette_senza_ricalcolare(mondo, modulo, client):
    from owner import crm_radar

    assert chiedi(client, mondo["st_a"]).status_code == 200
    blocco = crm_radar.contact_homes_block(ScopeOperatore(mondo["a"]), mondo["contact"])
    radar = blocco["homes"][0]["interest"]
    da_lmc7 = modulo["interest"].interest_for_stima_scoped(
        ScopeOperatore(mondo["a"]), mondo["st_a"])["interest"]
    assert radar == da_lmc7
    assert radar["level"] == "high"
    assert "Ha richiesto una verifica gratuita" in radar["reasons"]
    assert "score" not in json.dumps(blocco, default=str).lower()


def test_15_una_richiesta_vecchia_non_e_piu_attuale(mondo, modulo):
    """L'evento resta nel database; il radar, che dichiara 30 giorni, smette
    di dire che sta chiedendo adesso."""
    vecchia = datetime.now(timezone.utc) - timedelta(days=40)
    modulo["tracking"].track_consultation_request(mondo["acc"], mondo["st_a"], when=vecchia)
    assert len(eventi(mondo, CONSULTATION)) == 1, "la riga esiste"

    vista = modulo["interest"].interest_for_stima_scoped(
        ScopeOperatore(mondo["a"]), mondo["st_a"])["interest"]
    assert vista["consultation_requested"] is False
    assert vista["level"] == "none"


# ---------------------------------------------------------------------------
# 16: nessun task, nessuna attivita', nessuna comunicazione
# ---------------------------------------------------------------------------

def test_16_la_richiesta_non_crea_nient_altro(mondo, modulo, client):
    prima_task = conta(mondo, "SELECT COUNT(*) FROM tasks") \
        if righe(mondo, "SELECT to_regclass('public.tasks')")[0][0] else 0
    assert chiedi(client, mondo["st_a"]).status_code == 200
    assert conta(mondo, "SELECT COUNT(*) FROM activities") == 0
    if righe(mondo, "SELECT to_regclass('public.tasks')")[0][0]:
        assert conta(mondo, "SELECT COUNT(*) FROM tasks") == prima_task
    assert len(eventi(mondo)) == 1, "un evento solo: nessuna conseguenza automatica"
