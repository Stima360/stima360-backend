"""LMC-12 su PostgreSQL reale: le notifiche PRE-INCARICO, dal grant alla card.

SEI COSE CHE UN DOPPIO NON PUO' PROVARE.

LA MIGRATION. Che la 069 si applichi, che la down rifiuti di distruggere
righe vere e accetti una tabella vuota, e che la up si riapplichi dopo.

IL TRIGGER. Che il database rifiuti da solo una notifica il cui proprietario
e la cui casa stanno in due agenzie diverse, anche con un INSERT scritto a
mano che salta il repository.

L'IDEMPOTENZA. Che due giri sullo stesso fatto lascino una riga sola, e che
tre giorni identici dopo il fatto non ne aggiungano nessuna. La regola
fondamentale - uno snapshot nuovo non e' una notifica - provata sulla tabella.

LA TENANCY. Che il giro di A non legga i grant di B, che un account di A non
possa essere notificato su una casa di B da nessuna via, e che un grant
revocato o scaduto renda la notifica invisibile e non marcabile.

LA CONCORRENZA. Che il lock `owner:home_alerts` tenga fuori un secondo giro
e che sia DIVERSO da quello di LMC-11: i due cron non si escludono.

LA PAGINAZIONE. Che un'agenzia con piu' grant della pagina venga percorsa
per intero, e che un guasto sull'ultimo elemento della pagina non rilegga la
stessa pagina per sempre.

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
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare LMC-12")

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
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
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

#: La forma della 011 (P5), senza la guardia sul nome del database che
#: impedisce di applicarla qui. E' la tabella che LMC-12 LEGGE per la sola
#: `in_app_enabled`; non la scrive e non la modifica.
PREFERENZE_P5 = """
CREATE TABLE owner_notification_preferences (
    owner_account_id BIGINT PRIMARY KEY REFERENCES owner_accounts(id) ON DELETE CASCADE,
    in_app_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    publication_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    visit_feedback_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    document_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    request_update_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
"""

CATENA = ("009_owner_01", "017_seller_intelligence_01", "022_property_watch",
          "066_lmc1_owner_stima_access", "068_lmc10_owner_home_overrides",
          "069_lmc12_owner_home_notifications")


def _dsn_per(nome: str) -> str:
    if "?" in DSN:
        base, query = DSN.split("?", 1)
        return base.rsplit("/", 1)[0] + "/" + nome + "?" + query
    return DSN.rsplit("/", 1)[0] + "/" + nome


@pytest.fixture(scope="module")
def db():
    psycopg2 = pytest.importorskip("psycopg2")

    nome = f"lmc12_probe_{os.getpid()}_{uuid.uuid4().hex[:6]}"
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
                if versione.startswith("066"):
                    cur.execute(PREFERENZE_P5)
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
    from owner import home_alert_service, home_alerts
    from owner import repository as owner_repository
    from property_watch import database as pw_database

    monkeypatch.setattr(core_database, "get_connection", lambda: psycopg2.connect(db["dsn"]))
    monkeypatch.setattr(pw_database, "get_connection", lambda: psycopg2.connect(db["dsn"]))
    return {"service": home_alert_service, "alerts": home_alerts,
            "repository": owner_repository, "pw_database": pw_database}


GIORNO = timedelta(days=1)
T0 = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def mondo(db):
    conn = db["conn"]
    conn.rollback()
    with conn.cursor() as cur:
        for tabella in ("owner_home_notifications", "owner_home_overrides",
                        "owner_notification_preferences",
                        "owner_access_tokens", "owner_sessions", "owner_audit_log",
                        "owner_stima_access", "owner_property_access", "owner_accounts",
                        "property_watch_observations", "property_watches",
                        "seller_timeline_events", "leads",
                        "stime", "contacts", "properties", "agencies"):
            cur.execute(f"DELETE FROM {tabella}")
        cur.execute("INSERT INTO agencies (slug) VALUES ('a-uno') RETURNING id"); a = cur.fetchone()[0]
        cur.execute("INSERT INTO agencies (slug) VALUES ('b-due') RETURNING id"); b = cur.fetchone()[0]

        def stima(agency, via="Via Trieste"):
            cur.execute("INSERT INTO stime (agency_id,comune,microzona,via,civico,tipologia,mq,"
                        "nome,cognome,email,telefono) VALUES (%s,'Alba Adriatica','Villa Fiore',%s,"
                        "'12','Appartamento',95,'Mario','Rossi','mario@example.it','+39 333 1234567') "
                        "RETURNING id", (agency, via))
            return cur.fetchone()[0]

        def watch(agency, stima_id):
            cur.execute("INSERT INTO property_watches (stima_id,status,agency_id) VALUES (%s,'active',%s) "
                        "RETURNING id", (stima_id, agency))
            return cur.fetchone()[0]

        def account(agency, nome, email):
            cur.execute("INSERT INTO contacts (agency_id, display_name, email, email_normalized) "
                        "VALUES (%s,%s,%s,%s) RETURNING id", (agency, nome, email, email))
            contatto = cur.fetchone()[0]
            cur.execute("INSERT INTO owner_accounts (contact_id,status) VALUES (%s,'active') RETURNING id",
                        (contatto,))
            return cur.fetchone()[0]

        def grant(acc, stima_id):
            cur.execute("INSERT INTO owner_stima_access (owner_account_id,stima_id,granted_by) "
                        "VALUES (%s,%s,'LMC_PROVISIONING') RETURNING id", (acc, stima_id))
            return cur.fetchone()[0]

        st_a = stima(a); w_a = watch(a, st_a)
        st_b = stima(b, via="Via Verdi"); w_b = watch(b, st_b)
        acc_a = account(a, "Mario Rossi", "mario@example.it")
        acc_b = account(b, "Luisa Bianchi", "luisa@example.it")
        g_a = grant(acc_a, st_a)
        g_b = grant(acc_b, st_b)
    conn.commit()
    return {"conn": conn, "a": a, "b": b, "st_a": st_a, "st_b": st_b, "w_a": w_a, "w_b": w_b,
            "acc_a": acc_a, "acc_b": acc_b, "g_a": g_a, "g_b": g_b,
            "stima": stima, "watch": watch, "account": account, "grant": grant}


def righe(mondo, sql, params=()):
    with mondo["conn"].cursor() as cur:
        cur.execute(sql, params)
        valori = cur.fetchall()
    mondo["conn"].rollback()
    return valori


def esegui(mondo, sql, params=()):
    with mondo["conn"].cursor() as cur:
        cur.execute(sql, params)
    mondo["conn"].commit()


def snapshot(mondo, watch_id, prezzo, *, giorno, fp="fp-a", reason="scheduled_refresh"):
    """Uno snapshot LMC-3/LMC-11 scritto a mano, con la forma vera del payload."""
    quando = T0 + giorno * GIORNO
    payload = {"price_exact": prezzo, "eur_mq_finale": prezzo / 95, "base_mq": 1500,
               "computed_at": quando.isoformat(), "reason": reason,
               "algorithm_fingerprint": fp, "input_digest": "d" * 16}
    with mondo["conn"].cursor() as cur:
        cur.execute("INSERT INTO property_watch_observations (watch_id,observation_type,source,payload,"
                    "idempotency_key,observed_at) VALUES (%s,'valuation_snapshot','internal',%s,%s,%s) "
                    "RETURNING id",
                    (watch_id, json.dumps(payload), f"vs:{watch_id}:{giorno}:{fp}:{reason}", quando))
        oid = cur.fetchone()[0]
    mondo["conn"].commit()
    return oid


def rilevazione(mondo, watch_id, compatibili, *, giorno, tipo="buyer_pressure_changed"):
    quando = T0 + giorno * GIORNO
    alti = compatibili // 3
    payload = {"evaluated_buyers": 40, "compatible_buyers": compatibili,
               "highly_compatible_buyers": alti, "recent_compatible_buyers_30d": alti,
               "average_match_score": 60 if compatibili else None,
               "maximum_match_score": 80 if compatibili else None,
               "average_budget": 250000 if compatibili else None,
               "algorithm_version": "p21a-v1"}
    with mondo["conn"].cursor() as cur:
        cur.execute("INSERT INTO property_watch_observations (watch_id,observation_type,source,payload,"
                    "idempotency_key,observed_at) VALUES (%s,%s,'internal',%s,%s,%s) RETURNING id",
                    (watch_id, tipo, json.dumps(payload), f"bp:{watch_id}:{giorno}", quando))
        oid = cur.fetchone()[0]
    mondo["conn"].commit()
    return oid


def notifiche(mondo, account=None):
    sql = ("SELECT id, owner_account_id, stima_id, notification_type, title, body, evidence, "
           "idempotency_key, read_at FROM owner_home_notifications")
    if account is not None:
        return righe(mondo, sql + " WHERE owner_account_id=%s ORDER BY id", (account,))
    return righe(mondo, sql + " ORDER BY id")


def audit(mondo, azione):
    return righe(mondo, "SELECT owner_account_id, property_id, entity_type, entity_id, result, metadata "
                        "FROM owner_audit_log WHERE action=%s ORDER BY id", (azione,))


# ---------------------------------------------------------------------------
# A - IL VALORE: SOGLIE, RIFERIMENTO, IDEMPOTENZA (test 1-11 sul database)
# ---------------------------------------------------------------------------

def test_1_snapshot_identici_zero_notifiche(mondo, modulo):
    for g in range(4):
        snapshot(mondo, mondo["w_a"], 200000, giorno=g)
    esito = modulo["service"].run_for_agency(mondo["a"])
    assert esito["processed"] == 1 and esito["created"] == 0, esito
    assert notifiche(mondo) == []


def test_2_sotto_soglia_percentuale_zero(mondo, modulo):
    snapshot(mondo, mondo["w_a"], 300000, giorno=0)
    snapshot(mondo, mondo["w_a"], 314700, giorno=1)   # +4,9%, +14.700
    assert modulo["service"].run_for_agency(mondo["a"])["created"] == 0
    assert notifiche(mondo) == []


def test_3_sotto_soglia_euro_zero(mondo, modulo):
    snapshot(mondo, mondo["w_a"], 62000, giorno=0)
    snapshot(mondo, mondo["w_a"], 66999, giorno=1)    # +8%, +4.999
    assert modulo["service"].run_for_agency(mondo["a"])["created"] == 0
    assert notifiche(mondo) == []


def test_4_e_6_e_7_sopra_soglia_una_riga_e_nessun_duplicato(mondo, modulo):
    o1 = snapshot(mondo, mondo["w_a"], 100000, giorno=0)
    o2 = snapshot(mondo, mondo["w_a"], 105000, giorno=1)   # +5%, +5.000: entrambe
    esito = modulo["service"].run_for_agency(mondo["a"])
    assert esito["created"] == 1, esito
    righe_ = notifiche(mondo)
    assert len(righe_) == 1
    _, acc, st, tipo, titolo, corpo, evidence, chiave, letta = righe_[0]
    assert (acc, st, tipo) == (mondo["acc_a"], mondo["st_a"], "home_value_changed")
    assert chiave == f"lmc12:v1:home_value_changed:stima:{st}:from:{o1}:to:{o2}:account:{acc}"
    assert evidence == {"from_observation_id": o1, "to_observation_id": o2,
                        "from_value": 100000, "to_value": 105000, "delta_pct": 5.0}
    assert "105.000 €" in corpo and letta is None
    # 6. retry dello stesso confronto: reused, zero duplicati.
    esito = modulo["service"].run_for_agency(mondo["a"])
    assert esito["created"] == 0 and esito["reused"] == 0, esito
    assert len(notifiche(mondo)) == 1
    # 7. tre giorni identici dopo l'evento: ancora una riga sola.
    for g in (2, 3, 4):
        snapshot(mondo, mondo["w_a"], 105000, giorno=g)
        assert modulo["service"].run_for_agency(mondo["a"])["created"] == 0
    assert len(notifiche(mondo)) == 1
    # L'audit di creazione: property_id NULL, metadata senza corpo.
    creati = audit(mondo, "home_notification_created")
    assert len(creati) == 1
    assert creati[0][1] is None and creati[0][2] == "owner_home_notification"
    assert set(creati[0][5]) == {"notification_type", "stima_id", "idempotency_key"}


def test_6b_la_chiave_viene_rifiutata_dal_database_al_secondo_inserimento(mondo, modulo):
    snapshot(mondo, mondo["w_a"], 100000, giorno=0)
    snapshot(mondo, mondo["w_a"], 106000, giorno=1)
    assert modulo["service"].run_for_agency(mondo["a"])["created"] == 1
    chiave = notifiche(mondo)[0][7]
    # Scrittura diretta dal repository con la stessa chiave: 'reused', una riga.
    esito = modulo["repository"].create_home_notification(
        mondo["acc_a"], mondo["st_a"], notification_type="home_value_changed", title="T", body="B",
        evidence={}, idempotency_key=chiave)
    assert esito == "reused"
    assert len(notifiche(mondo)) == 1


def test_5_variazione_negativa_significativa(mondo, modulo):
    snapshot(mondo, mondo["w_a"], 100000, giorno=0)
    snapshot(mondo, mondo["w_a"], 94000, giorno=1)
    assert modulo["service"].run_for_agency(mondo["a"])["created"] == 1
    assert "diminuito" in notifiche(mondo)[0][5]


def test_8_la_variazione_cumulativa_viene_rilevata(mondo, modulo):
    o1 = snapshot(mondo, mondo["w_a"], 100000, giorno=0)
    # Un giro al giorno, come in produzione: 102k, 104k non bastano, 106k si'.
    assert modulo["service"].run_for_agency(mondo["a"])["created"] == 0
    snapshot(mondo, mondo["w_a"], 102000, giorno=1)
    assert modulo["service"].run_for_agency(mondo["a"])["created"] == 0
    snapshot(mondo, mondo["w_a"], 104000, giorno=2)
    assert modulo["service"].run_for_agency(mondo["a"])["created"] == 0
    o4 = snapshot(mondo, mondo["w_a"], 106000, giorno=3)
    assert modulo["service"].run_for_agency(mondo["a"])["created"] == 1
    ev = notifiche(mondo)[0][6]
    assert (ev["from_observation_id"], ev["to_observation_id"]) == (o1, o4)
    # E da li' in poi il riferimento e' 106k: 108k (+1,9%) non e' niente.
    snapshot(mondo, mondo["w_a"], 108000, giorno=4)
    assert modulo["service"].run_for_agency(mondo["a"])["created"] == 0
    assert len(notifiche(mondo)) == 1


def test_9_owner_profile_updated_avanza_il_riferimento_senza_notifica(mondo, modulo):
    from owner import home_update
    snapshot(mondo, mondo["w_a"], 100000, giorno=0)
    o_edit = snapshot(mondo, mondo["w_a"], 130000, giorno=1, reason=home_update.REFRESH_REASON)
    assert modulo["service"].run_for_agency(mondo["a"])["created"] == 0
    assert notifiche(mondo) == []
    # Il giorno dopo LMC-11 riscrive 130k: ancora niente.
    snapshot(mondo, mondo["w_a"], 130000, giorno=2)
    assert modulo["service"].run_for_agency(mondo["a"])["created"] == 0
    # Poi +5% da 130k: la notifica parte dal NUOVO stato, non da 100k.
    o5 = snapshot(mondo, mondo["w_a"], 136500, giorno=3)
    assert modulo["service"].run_for_agency(mondo["a"])["created"] == 1
    ev = notifiche(mondo)[0][6]
    assert (ev["from_observation_id"], ev["to_observation_id"]) == (o_edit, o5)
    assert ev["from_value"] == 130000


def test_10_e_11_il_metodo_che_cambia(mondo, modulo):
    """AGGIORNATO AL FINAL GATE. La prima versione di questo test aspettava
    `home_method_changed` da 100k(A) -> 101k(B) -> 108k(B): il metodo era
    cambiato due snapshot prima, sotto soglia, e il terzo lo ereditava. La
    regola fissata al gate dice il contrario, ed e' `test_11b`. Qui resta il
    caso in cui il metodo cambia NEL candidato che supera la soglia."""
    snapshot(mondo, mondo["w_a"], 100000, giorno=0, fp="fp-a")
    snapshot(mondo, mondo["w_a"], 101000, giorno=1, fp="fp-a")     # sotto soglia: niente
    assert modulo["service"].run_for_agency(mondo["a"])["created"] == 0
    snapshot(mondo, mondo["w_a"], 108000, giorno=2, fp="fp-b")     # +8% da 100k, e il metodo cambia QUI
    assert modulo["service"].run_for_agency(mondo["a"])["created"] == 1
    riga = notifiche(mondo)[0]
    assert riga[3] == "home_method_changed"
    assert "metodo di stima" in riga[4].lower() and "mercato" not in riga[5].lower()
    assert riga[7].startswith("lmc12:v1:home_method_changed:")
    # 10: fingerprint nuovo ma sotto soglia, il giorno dopo: niente.
    snapshot(mondo, mondo["w_a"], 109000, giorno=3, fp="fp-c")
    assert modulo["service"].run_for_agency(mondo["a"])["created"] == 0
    assert len(notifiche(mondo)) == 1


def test_11b_il_metodo_cambiato_due_snapshot_fa_e_un_cambio_di_valore_oggi(mondo, modulo):
    """IL CASO DEL FINAL GATE, sul database, un giro al giorno."""
    o1 = snapshot(mondo, mondo["w_a"], 100000, giorno=0, fp="A")
    assert modulo["service"].run_for_agency(mondo["a"])["created"] == 0
    snapshot(mondo, mondo["w_a"], 104000, giorno=1, fp="B")      # metodo nuovo, sotto soglia
    assert modulo["service"].run_for_agency(mondo["a"])["created"] == 0
    assert notifiche(mondo) == []
    o3 = snapshot(mondo, mondo["w_a"], 106000, giorno=2, fp="B")  # stesso metodo di ieri, +6% da o1
    assert modulo["service"].run_for_agency(mondo["a"])["created"] == 1
    riga = notifiche(mondo)[0]
    assert riga[3] == "home_value_changed", riga[3]
    assert riga[6]["from_observation_id"] == o1 and riga[6]["to_observation_id"] == o3
    assert riga[7].startswith("lmc12:v1:home_value_changed:")
    assert "metodo" not in riga[4].lower() and "aumentato" in riga[5]


# ---------------------------------------------------------------------------
# B - LA DOMANDA (test 12-14 sul database)
# ---------------------------------------------------------------------------

def test_12_stessa_fascia_zero(mondo, modulo):
    rilevazione(mondo, mondo["w_a"], 30, giorno=0, tipo="buyer_pressure_snapshot")
    rilevazione(mondo, mondo["w_a"], 33, giorno=1)
    assert modulo["service"].run_for_agency(mondo["a"])["created"] == 0
    assert notifiche(mondo) == []


def test_13_cambio_di_fascia_una_riga_senza_metriche(mondo, modulo):
    rilevazione(mondo, mondo["w_a"], 0, giorno=0, tipo="buyer_pressure_snapshot")
    o2 = rilevazione(mondo, mondo["w_a"], 30, giorno=1)
    assert modulo["service"].run_for_agency(mondo["a"])["created"] == 1
    riga = notifiche(mondo)[0]
    assert riga[3] == "home_demand_changed"
    assert riga[6] == {"observation_id": o2, "from_status": "low", "to_status": "high"}
    assert riga[7] == f"lmc12:v1:home_demand_changed:stima:{mondo['st_a']}:obs:{o2}:account:{mondo['acc_a']}"
    testo = (riga[4] + riga[5]).lower()
    for privato in ("30", "40", "250", "budget", "score", "buy", "match"):
        assert privato not in testo, privato
    assert "«domanda alta»" in testo
    # Retry: niente.
    assert modulo["service"].run_for_agency(mondo["a"])["created"] == 0
    assert len(notifiche(mondo)) == 1


def test_14_unavailable_coinvolto_zero(mondo, modulo):
    quando = T0
    with mondo["conn"].cursor() as cur:
        cur.execute("INSERT INTO property_watch_observations (watch_id,observation_type,source,payload,"
                    "idempotency_key,observed_at) VALUES (%s,'buyer_pressure_snapshot','internal',%s,%s,%s)",
                    (mondo["w_a"], json.dumps({"evaluated_buyers": "rotto"}), "bp:rotto", quando))
    mondo["conn"].commit()
    rilevazione(mondo, mondo["w_a"], 30, giorno=1)
    assert modulo["service"].run_for_agency(mondo["a"])["created"] == 0
    assert notifiche(mondo) == []


# ---------------------------------------------------------------------------
# C - PREFERENZA, GRANT, TENANCY, TRIGGER (test 15-18)
# ---------------------------------------------------------------------------

def test_15_in_app_disabilitato_zero_righe_e_una_soppressione(mondo, modulo):
    esegui(mondo, "INSERT INTO owner_notification_preferences (owner_account_id,in_app_enabled) "
                  "VALUES (%s,FALSE)", (mondo["acc_a"],))
    snapshot(mondo, mondo["w_a"], 100000, giorno=0)
    snapshot(mondo, mondo["w_a"], 106000, giorno=1)
    esito = modulo["service"].run_for_agency(mondo["a"])
    assert esito["created"] == 0 and esito["suppressed"] == 1, esito
    assert notifiche(mondo) == []
    soppressi = audit(mondo, "home_notification_suppressed")
    assert len(soppressi) == 1
    assert soppressi[0][1] is None
    assert soppressi[0][5]["reason_code"] == "preference_disabled"
    assert "body" not in soppressi[0][5] and "title" not in soppressi[0][5]
    # Il giorno dopo: stessa soppressione, nessun secondo audit.
    snapshot(mondo, mondo["w_a"], 106000, giorno=2)
    assert modulo["service"].run_for_agency(mondo["a"])["suppressed"] == 1
    assert len(audit(mondo, "home_notification_suppressed")) == 1
    # Riattivate le notifiche, il cambiamento cumulativo arriva (non e' stato
    # assorbito), una volta.
    esegui(mondo, "UPDATE owner_notification_preferences SET in_app_enabled=TRUE WHERE owner_account_id=%s",
           (mondo["acc_a"],))
    assert modulo["service"].run_for_agency(mondo["a"])["created"] == 1
    assert len(notifiche(mondo)) == 1


def test_16_grant_revocato_o_scaduto_non_leggibile_ne_marcabile(mondo, modulo):
    from core.exceptions import NotFoundError
    snapshot(mondo, mondo["w_a"], 100000, giorno=0)
    snapshot(mondo, mondo["w_a"], 106000, giorno=1)
    assert modulo["service"].run_for_agency(mondo["a"])["created"] == 1
    nid = notifiche(mondo)[0][0]
    repo = modulo["repository"]
    assert [n["id"] for n in repo.portal_home_notifications(mondo["acc_a"])] == [nid]
    # Revocato.
    esegui(mondo, "UPDATE owner_stima_access SET access_status='revoked', revoked_at=NOW() WHERE id=%s",
           (mondo["g_a"],))
    assert repo.portal_home_notifications(mondo["acc_a"]) == []
    with pytest.raises(NotFoundError):
        repo.mark_home_notification_read(mondo["acc_a"], nid)
    assert notifiche(mondo)[0][8] is None
    # Scaduto (e non revocato).
    esegui(mondo, "UPDATE owner_stima_access SET access_status='active', revoked_at=NULL, "
                  "valid_from=NOW()-INTERVAL '10 days', valid_until=NOW()-INTERVAL '1 day' WHERE id=%s",
           (mondo["g_a"],))
    assert repo.portal_home_notifications(mondo["acc_a"]) == []
    with pytest.raises(NotFoundError):
        repo.mark_home_notification_read(mondo["acc_a"], nid)
    # E il cron non lo elabora nemmeno: nessuna riga nuova per un grant morto.
    snapshot(mondo, mondo["w_a"], 130000, giorno=2)
    assert modulo["service"].run_for_agency(mondo["a"])["processed"] == 0


def test_17_cross_tenant_impossibile_creare_o_leggere(mondo, modulo):
    import psycopg2
    from core.exceptions import NotFoundError
    repo = modulo["repository"]
    # Il giro di A non legge il grant di B: la casa di B ha un salto, A non
    # la vede e non scrive niente.
    snapshot(mondo, mondo["w_b"], 100000, giorno=0)
    snapshot(mondo, mondo["w_b"], 106000, giorno=1)
    esito = modulo["service"].run_for_agency(mondo["a"])
    assert esito["processed"] == 1 and esito["created"] == 0
    assert notifiche(mondo) == []
    # Senza grant non si scrive, qualunque cosa chieda il chiamante.
    with pytest.raises(NotFoundError):
        repo.create_home_notification(mondo["acc_a"], mondo["st_b"], notification_type="home_value_changed",
                                      title="T", body="B", evidence={}, idempotency_key="k-cross")
    # Un grant account-di-A/stima-di-B non puo' nemmeno nascere: la 066 lo
    # rifiuta a monte...
    with mondo["conn"].cursor() as cur:
        with pytest.raises(psycopg2.Error):
            cur.execute("INSERT INTO owner_stima_access (owner_account_id,stima_id,granted_by) VALUES (%s,%s,'x')",
                        (mondo["acc_a"], mondo["st_b"]))
    mondo["conn"].rollback()
    # ...e se le due radici smettono di essere d'accordo DOPO (il contatto
    # cambia agenzia), il grant esistente non viene piu' seguito da nessuno:
    # ne' dal giro di A, ne' da quello di B, ne' dal portale.
    snapshot(mondo, mondo["w_a"], 100000, giorno=0)
    snapshot(mondo, mondo["w_a"], 106000, giorno=1)
    esegui(mondo, "UPDATE contacts SET agency_id=%s WHERE id=(SELECT contact_id FROM owner_accounts WHERE id=%s)",
           (mondo["b"], mondo["acc_a"]))
    assert repo.list_home_alert_grants_page(mondo["a"], page_size=10) == []
    assert [g["stima_id"] for g in repo.list_home_alert_grants_page(mondo["b"], page_size=10)] == [mondo["st_b"]]
    assert modulo["service"].run_for_all_agencies()["created"] == 1   # solo B, sulla sua casa
    assert [n[1] for n in notifiche(mondo)] == [mondo["acc_b"]]
    with pytest.raises(NotFoundError):
        repo.create_home_notification(mondo["acc_a"], mondo["st_a"], notification_type="home_value_changed",
                                      title="T", body="B", evidence={}, idempotency_key="k-incoerente")
    assert repo.portal_home_notifications(mondo["acc_a"]) == []
    assert repo.portal_home_notifications(mondo["acc_b"]) != []


def test_18_il_trigger_rifiuta_account_e_stima_di_agenzie_diverse(mondo):
    import psycopg2
    with mondo["conn"].cursor() as cur:
        with pytest.raises(psycopg2.Error) as info:
            cur.execute("INSERT INTO owner_home_notifications (owner_account_id,stima_id,notification_type,"
                        "title,body,idempotency_key) VALUES (%s,%s,'home_value_changed','T','B','k-trg')",
                        (mondo["acc_a"], mondo["st_b"]))
        assert "LMC-12 owner/stima tenancy" in str(info.value)
    mondo["conn"].rollback()
    assert notifiche(mondo) == []
    # Lo stesso INSERT con le due radici d'accordo passa: e' il trigger, non
    # un vincolo che rifiuta tutto.
    esegui(mondo, "INSERT INTO owner_home_notifications (owner_account_id,stima_id,notification_type,"
                  "title,body,idempotency_key) VALUES (%s,%s,'home_value_changed','T','B','k-ok')",
           (mondo["acc_a"], mondo["st_a"]))
    assert len(notifiche(mondo)) == 1
    # E un UPDATE che sposta la riga su un'altra agenzia viene rifiutato.
    with mondo["conn"].cursor() as cur:
        with pytest.raises(psycopg2.Error):
            cur.execute("UPDATE owner_home_notifications SET stima_id=%s WHERE idempotency_key='k-ok'",
                        (mondo["st_b"],))
    mondo["conn"].rollback()


def test_18b_il_check_dei_tipi_e_chiuso(mondo):
    import psycopg2
    with mondo["conn"].cursor() as cur:
        with pytest.raises(psycopg2.Error):
            cur.execute("INSERT INTO owner_home_notifications (owner_account_id,stima_id,notification_type,"
                        "title,body,idempotency_key) VALUES (%s,%s,'publication_published','T','B','k-tipo')",
                        (mondo["acc_a"], mondo["st_a"]))
    mondo["conn"].rollback()


# ---------------------------------------------------------------------------
# D - LETTURA, MARK READ, DTO (test 19-21)
# ---------------------------------------------------------------------------

def test_19_mark_read_idempotente_primo_timestamp_vince(mondo, modulo):
    snapshot(mondo, mondo["w_a"], 100000, giorno=0)
    snapshot(mondo, mondo["w_a"], 106000, giorno=1)
    assert modulo["service"].run_for_agency(mondo["a"])["created"] == 1
    repo = modulo["repository"]
    nid = notifiche(mondo)[0][0]
    prima = repo.mark_home_notification_read(mondo["acc_a"], nid)
    assert prima["read_at"] is not None
    seconda = repo.mark_home_notification_read(mondo["acc_a"], nid)
    assert seconda["read_at"] == prima["read_at"]
    letti = audit(mondo, "home_notification_read")
    assert len(letti) == 2 and all(r[1] is None for r in letti)
    assert repo.portal_home_notifications(mondo["acc_a"], unread_only=True) == []
    assert len(repo.portal_home_notifications(mondo["acc_a"])) == 1


def test_20_e_21_il_read_model_e_la_whitelist_e_niente_di_privato(mondo, modulo):
    from owner.schemas import OwnerHomeNotificationDTO
    rilevazione(mondo, mondo["w_a"], 0, giorno=0, tipo="buyer_pressure_snapshot")
    rilevazione(mondo, mondo["w_a"], 30, giorno=1)
    snapshot(mondo, mondo["w_a"], 100000, giorno=0)
    snapshot(mondo, mondo["w_a"], 106000, giorno=1)
    assert modulo["service"].run_for_agency(mondo["a"])["created"] == 2
    righe_ = modulo["repository"].portal_home_notifications(mondo["acc_a"])
    assert len(righe_) == 2
    for riga in righe_:
        assert set(riga) == set(OwnerHomeNotificationDTO.model_fields)
        OwnerHomeNotificationDTO.model_validate(riga)
        assert riga["stima_id"] == mondo["st_a"]
        testo = json.dumps(riga, default=str).lower()
        for privato in ("evidence", "fingerprint", "observation", "idempotency", "budget",
                        "score", "evaluated", "compatible_buyers", "mario", "example.it", "333"):
            assert privato not in testo, privato
    # Ordine: la piu' recente prima, e la paginazione come P5.
    assert [r["id"] for r in righe_] == sorted((r["id"] for r in righe_), reverse=True)
    assert len(modulo["repository"].portal_home_notifications(mondo["acc_a"], limit=1)) == 1
    assert len(modulo["repository"].portal_home_notifications(mondo["acc_a"], limit=1, offset=1)) == 1
    assert modulo["repository"].portal_home_notifications(mondo["acc_a"], limit=1, offset=2) == []


def test_20b_la_rotta_http_espone_solo_il_dto(mondo, modulo):
    from fastapi.testclient import TestClient
    import main
    from owner import dependencies
    snapshot(mondo, mondo["w_a"], 100000, giorno=0)
    snapshot(mondo, mondo["w_a"], 106000, giorno=1)
    assert modulo["service"].run_for_agency(mondo["a"])["created"] == 1
    app = main.app
    app.dependency_overrides[dependencies.current_owner] = lambda: {"owner_account_id": mondo["acc_a"]}
    try:
        client = TestClient(app)
        r = client.get("/api/owner/portal/home-notifications?limit=1")
        assert r.status_code == 200, r.text
        corpo = r.json()
        assert set(corpo) == {"items", "limit", "offset", "has_more"}
        assert len(corpo["items"]) == 1 and set(corpo["items"][0]) == {
            "id", "type", "stima_id", "title", "body", "created_at", "read_at"}
        nid = corpo["items"][0]["id"]
        r = client.post(f"/api/owner/portal/home-notifications/{nid}/read")
        assert r.status_code == 200 and r.json()["read_at"] is not None
        assert set(r.json()) == {"id", "type", "stima_id", "title", "body", "created_at", "read_at"}
        r = client.post(f"/api/owner/portal/home-notifications/{nid + 1000}/read")
        assert r.status_code == 404 and r.json() == {"detail": "Risorsa non trovata"}
        negati = audit(mondo, "home_notification_access_denied")
        assert len(negati) == 1 and negati[0][4] == "denied"
        # Le preferenze P5 rispondono come prima (riga assente = tutto TRUE) e
        # non hanno colonne nuove.
        r = client.get("/api/owner/portal/notification-preferences")
        assert r.status_code == 200
        assert set(r.json()) == {"in_app_enabled", "publication_enabled", "visit_feedback_enabled",
                                 "document_enabled", "request_update_enabled"}
    finally:
        app.dependency_overrides.pop(dependencies.current_owner, None)


# ---------------------------------------------------------------------------
# E - PAGINAZIONE, ISOLAMENTO, LOCK (test 22-25)
# ---------------------------------------------------------------------------

def _molte_case(mondo, quante):
    """`quante` case di A, ciascuna con il suo grant e un salto sopra soglia."""
    conn = mondo["conn"]
    stime = []
    with conn.cursor() as cur:
        for i in range(quante):
            cur.execute("INSERT INTO stime (agency_id,comune,microzona,via,civico,mq) VALUES "
                        "(%s,'Alba Adriatica','Villa Fiore','Via Test',%s,95) RETURNING id",
                        (mondo["a"], str(i)))
            st = cur.fetchone()[0]
            cur.execute("INSERT INTO property_watches (stima_id,status,agency_id) VALUES (%s,'active',%s) "
                        "RETURNING id", (st, mondo["a"]))
            w = cur.fetchone()[0]
            cur.execute("INSERT INTO owner_stima_access (owner_account_id,stima_id,granted_by) "
                        "VALUES (%s,%s,'x')", (mondo["acc_a"], st))
            stime.append((st, w))
    conn.commit()
    for st, w in stime:
        snapshot(mondo, w, 100000, giorno=0)
        snapshot(mondo, w, 106000, giorno=1)
    return stime


def test_22_multi_pagina_senza_starvation(mondo, modulo):
    _molte_case(mondo, 7)   # + la casa della fixture = 8 grant
    esito = modulo["service"].run_for_agency(mondo["a"], page_size=3)
    assert esito["processed"] == 8, esito
    assert esito["created"] == 7   # la casa della fixture non ha snapshot
    assert len(notifiche(mondo)) == 7
    # Il giorno dopo, con page_size 3, nessuna casa viene "rivista": tutto reused/0.
    esito = modulo["service"].run_for_agency(mondo["a"], page_size=3)
    assert esito["processed"] == 8 and esito["created"] == 0


def test_23_guasto_sull_ultimo_elemento_della_pagina_non_crea_loop(mondo, modulo, monkeypatch):
    stime = _molte_case(mondo, 5)   # 6 grant in tutto
    repo = modulo["repository"]
    originale = repo.create_home_notification
    vittima = stime[1][0]           # terzo grant in ordine di id -> ultimo della pagina 1 da 3
    letture = []
    pagina_orig = repo.list_home_alert_grants_page

    def pagina(agency_id, *, after_grant_id=0, page_size):
        letture.append(after_grant_id)
        return pagina_orig(agency_id, after_grant_id=after_grant_id, page_size=page_size)

    def crea(account, stima, **kw):
        if stima == vittima:
            raise RuntimeError("guasto simulato")
        return originale(account, stima, **kw)
    monkeypatch.setattr(repo, "create_home_notification", crea)
    monkeypatch.setattr(repo, "list_home_alert_grants_page", pagina)
    esito = modulo["service"].run_for_agency(mondo["a"], page_size=3)
    assert esito["processed"] == 6 and esito["failed"] == 1 and esito["created"] == 4, esito
    assert len(letture) == 3, letture   # 3 + 3, poi la pagina vuota che chiude


def test_24_tenant_a_che_fallisce_non_blocca_b(mondo, modulo, monkeypatch):
    snapshot(mondo, mondo["w_b"], 100000, giorno=0)
    snapshot(mondo, mondo["w_b"], 106000, giorno=1)
    repo = modulo["repository"]
    pagina_orig = repo.list_home_alert_grants_page

    def pagina(agency_id, *, after_grant_id=0, page_size):
        if agency_id == mondo["a"]:
            raise RuntimeError("tenant A giu'")
        return pagina_orig(agency_id, after_grant_id=after_grant_id, page_size=page_size)
    monkeypatch.setattr(repo, "list_home_alert_grants_page", pagina)
    esito = modulo["service"].run_for_all_agencies()
    assert esito["agencies"] == 2 and esito["failed"] == 1 and esito["created"] == 1, esito
    assert [n[1] for n in notifiche(mondo)] == [mondo["acc_b"]]


def test_25_il_lock_esclude_il_secondo_giro_e_non_e_quello_di_lmc11(db, mondo, modulo, monkeypatch, capsys):
    import psycopg2
    import run_owner_home_alert_cron as runner
    from owner.home_alert_service import HOME_ALERT_LOCK_SCOPE
    from property_watch.database import VALUATION_CRON_LOCK_SCOPE
    snapshot(mondo, mondo["w_a"], 100000, giorno=0)
    snapshot(mondo, mondo["w_a"], 106000, giorno=1)
    # Un altro giro tiene il lock LMC-12 da una connessione sua.
    altro = psycopg2.connect(db["dsn"])
    try:
        with altro.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(hashtextextended(%s,0))", (HOME_ALERT_LOCK_SCOPE,))
            assert cur.fetchone()[0] is True
        assert runner.main(["--agency-id", str(mondo["a"])]) == 0
        assert "another_run_active" in capsys.readouterr().out
        assert notifiche(mondo) == []
        # Il lock di LMC-11 e' un altro: tenerlo NON ferma LMC-12.
        with altro.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))", (HOME_ALERT_LOCK_SCOPE,))
            cur.execute("SELECT pg_try_advisory_lock(hashtextextended(%s,0))", (VALUATION_CRON_LOCK_SCOPE,))
            assert cur.fetchone()[0] is True
        assert runner.main(["--agency-id", str(mondo["a"])]) == 0
        out = capsys.readouterr().out
        assert "status=completed" in out and "created=1" in out
        assert len(notifiche(mondo)) == 1
        # Nessun dato personale nel log.
        for privato in ("mario", "example.it", "Via Trieste", "106.000"):
            assert privato not in out
        with altro.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))", (VALUATION_CRON_LOCK_SCOPE,))
    finally:
        altro.close()


def test_25b_il_lock_si_libera_dopo_il_giro(db, mondo, modulo):
    import psycopg2
    import run_owner_home_alert_cron as runner
    from owner.home_alert_service import HOME_ALERT_LOCK_SCOPE
    assert runner.main(["--agency-id", str(mondo["a"])]) == 0
    altro = psycopg2.connect(db["dsn"])
    try:
        with altro.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(hashtextextended(%s,0))", (HOME_ALERT_LOCK_SCOPE,))
            assert cur.fetchone()[0] is True
            cur.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))", (HOME_ALERT_LOCK_SCOPE,))
    finally:
        altro.close()


# ---------------------------------------------------------------------------
# F - INVARIANTI E MIGRATION (test 26-28 sul database, up/down/up)
# ---------------------------------------------------------------------------

def test_26_p5_e_lmc11_restano_intatti_dopo_un_giro(mondo, modulo):
    prima_obs = righe(mondo, "SELECT id, payload, idempotency_key FROM property_watch_observations ORDER BY id")
    prima_pref = righe(mondo, "SELECT * FROM owner_notification_preferences")
    prima_stime = righe(mondo, "SELECT * FROM stime ORDER BY id")
    snapshot(mondo, mondo["w_a"], 100000, giorno=0)
    snapshot(mondo, mondo["w_a"], 106000, giorno=1)
    dopo_snap = righe(mondo, "SELECT id, payload, idempotency_key FROM property_watch_observations ORDER BY id")
    assert modulo["service"].run_for_agency(mondo["a"])["created"] == 1
    assert righe(mondo, "SELECT id, payload, idempotency_key FROM property_watch_observations ORDER BY id") == dopo_snap
    assert righe(mondo, "SELECT * FROM owner_notification_preferences") == prima_pref
    assert righe(mondo, "SELECT * FROM stime ORDER BY id") == prima_stime
    assert righe(mondo, "SELECT count(*) FROM owner_notifications")[0][0] == 0 \
        if righe(mondo, "SELECT to_regclass('public.owner_notifications')")[0][0] else True
    assert righe(mondo, "SELECT count(*) FROM seller_timeline_events")[0][0] == 0
    assert prima_obs is not None


def test_27_la_down_rifiuta_con_righe_e_accetta_vuota_poi_la_up_si_riapplica(mondo, db):
    import psycopg2
    giu = (MIGRAZIONI / "069_lmc12_owner_home_notifications_down.sql").read_text(encoding="utf-8")
    su = (MIGRAZIONI / "069_lmc12_owner_home_notifications.sql").read_text(encoding="utf-8")
    esegui(mondo, "INSERT INTO owner_home_notifications (owner_account_id,stima_id,notification_type,"
                  "title,body,idempotency_key) VALUES (%s,%s,'home_value_changed','T','B','k-down')",
           (mondo["acc_a"], mondo["st_a"]))
    conn = psycopg2.connect(db["dsn"])
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            with pytest.raises(psycopg2.Error) as info:
                cur.execute(giu)
            assert "would be destroyed" in str(info.value)
            # La down apre la propria transazione (BEGIN/COMMIT): dopo il
            # RAISE resta abortita e va chiusa, come farebbe il runner. In
            # autocommit psycopg2 non sa di quel BEGIN: il ROLLBACK e' esplicito.
            cur.execute("ROLLBACK")
            cur.execute("SELECT to_regclass('public.owner_home_notifications')")
            assert cur.fetchone()[0] is not None, "niente e' stato cambiato"
        esegui(mondo, "DELETE FROM owner_home_notifications")
        with conn.cursor() as cur:
            cur.execute(giu)
            cur.execute("SELECT to_regclass('public.owner_home_notifications')")
            assert cur.fetchone()[0] is None
            cur.execute("SELECT 1 FROM pg_proc WHERE proname='owner_home_notifications_agency_integrity'")
            assert cur.fetchone() is None
            # Le vicine sono ancora li'.
            for tabella in ("owner_home_overrides", "owner_stima_access", "owner_notification_preferences",
                            "property_watch_observations"):
                cur.execute("SELECT to_regclass(%s)", (f"public.{tabella}",))
                assert cur.fetchone()[0] is not None, tabella
            cur.execute(su)
            cur.execute("SELECT to_regclass('public.owner_home_notifications')")
            assert cur.fetchone()[0] is not None
            cur.execute("SELECT count(*) FROM pg_trigger WHERE tgname='trg_owner_home_notifications_agency_integrity'")
            assert cur.fetchone()[0] == 1
            # Riapplicare la UP e' un no-op, non un errore.
            cur.execute(su)
    finally:
        conn.close()
