"""LMC-7 su PostgreSQL reale: gli eventi del radar, l'idempotenza, la tenancy.

Cio' che solo un database sa dire: che la seconda apertura nello stesso
giorno non scriva una seconda riga E non tocchi la prima, che il giorno dopo
ne scriva una nuova, che il lead collegato sia quello giusto fra piu'
candidati, e che un grant revocato o scaduto non lasci passare niente.

La proiezione dell'interesse viene poi costruita sugli eventi VERI appena
scritti, non su dizionari finti: e' l'unico modo di sapere che le due meta'
del radar - chi scrive e chi legge - parlano la stessa lingua.

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
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare LMC-7")

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

    nome = f"lmc7_probe_{os.getpid()}_{uuid.uuid4().hex[:6]}"
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


METRICHE_DOMANDA = {
    "evaluated_buyers": 24, "compatible_buyers": 9, "highly_compatible_buyers": 4,
    "recent_compatible_buyers_30d": 5, "average_match_score": 74.5,
    "maximum_match_score": 91.0, "average_budget": 238000.0,
    "algorithm_version": "match-1.0",
}


def eventi(mondo, tipo=None):
    sql = ("SELECT event_type, event_source, agency_id, contact_id, lead_id, "
           "stima_id, payload, idempotency_key, occurred_at "
           "FROM seller_timeline_events")
    params = ()
    if tipo is not None:
        sql += " WHERE event_type = %s"
        params = (tipo,)
    return righe(mondo, sql + " ORDER BY id", params)


def scrivi_snapshot(mondo, watch_id, chiave="k-vs"):
    with mondo["conn"].cursor() as cur:
        cur.execute("INSERT INTO property_watch_observations (watch_id,observation_type,"
                    "source,payload,idempotency_key) VALUES (%s,'valuation_snapshot',"
                    "'internal',%s,%s)",
                    (watch_id, json.dumps({"price_exact": 200000, "eur_mq_finale": 2100,
                                           "computed_at": "2026-09-10T08:00:00+00:00",
                                           "algorithm_fingerprint": "v1",
                                           "input_digest": "a" * 64}), chiave))
    mondo["conn"].commit()


def scrivi_pressure(mondo, watch_id, chiave="k-bp"):
    with mondo["conn"].cursor() as cur:
        cur.execute("INSERT INTO property_watch_observations (watch_id,observation_type,"
                    "source,payload,idempotency_key) VALUES (%s,'buyer_pressure_snapshot',"
                    "'internal',%s,%s)", (watch_id, json.dumps(METRICHE_DOMANDA), chiave))
    mondo["conn"].commit()


# ---------------------------------------------------------------------------
# 1-4: home_viewed nasce dall'apertura, una volta al giorno
# ---------------------------------------------------------------------------

def test_1_aprire_il_dettaglio_registra_home_viewed(mondo, modulo):
    modulo["home_service"].get_home(mondo["acc"], mondo["st_a"])
    righe_evento = eventi(mondo, "owner_home_viewed")
    assert len(righe_evento) == 1
    tipo, source, agency, contact, lead, stima, payload, chiave, _quando = righe_evento[0]
    assert source == "owner_portal"
    assert agency == mondo["a"]
    assert contact == mondo["contact"]
    assert lead == mondo["lead_sell"]
    assert stima == mondo["st_a"]
    assert payload == {"action": "home_viewed"}
    assert chiave.startswith("owner_portal:owner_home_viewed:owner:")
    assert chiave.endswith(datetime.now(timezone.utc).date().isoformat())


def test_2_la_seconda_apertura_dello_stesso_giorno_non_duplica(mondo, modulo):
    modulo["home_service"].get_home(mondo["acc"], mondo["st_a"])
    prima = eventi(mondo, "owner_home_viewed")[0]
    for _ in range(4):
        modulo["home_service"].get_home(mondo["acc"], mondo["st_a"])
    dopo = eventi(mondo, "owner_home_viewed")
    assert len(dopo) == 1, "una riga sola per giornata"
    assert dopo[0] == prima, "e la prima riga non viene toccata"


def test_3_il_giorno_dopo_e_un_evento_nuovo(mondo, modulo):
    ieri = datetime.now(timezone.utc) - timedelta(days=1)
    modulo["tracking"].track_home_viewed(mondo["acc"], mondo["st_a"], when=ieri)
    modulo["home_service"].get_home(mondo["acc"], mondo["st_a"])
    righe_evento = eventi(mondo, "owner_home_viewed")
    assert len(righe_evento) == 2
    giorni = {r[7].rsplit(":", 1)[1] for r in righe_evento}
    assert len(giorni) == 2, giorni


def test_4_la_lista_delle_case_non_registra_niente(mondo, modulo):
    modulo["home_service"].list_homes(mondo["acc"])
    assert eventi(mondo) == [], "aprire l'elenco non e' guardare una casa"


# ---------------------------------------------------------------------------
# 5-10: le due azioni esplicite
# ---------------------------------------------------------------------------

def test_5_senza_storico_nessun_evento_andamento(mondo, modulo):
    assert modulo["tracking"].track_action(
        mondo["acc"], mondo["st_a"], "value_history_viewed") is False
    assert eventi(mondo, "owner_value_history_viewed") == []


def test_6_con_storico_l_azione_registra(mondo, modulo):
    scrivi_snapshot(mondo, mondo["w_a"])
    assert modulo["tracking"].track_action(
        mondo["acc"], mondo["st_a"], "value_history_viewed") is True
    righe_evento = eventi(mondo, "owner_value_history_viewed")
    assert len(righe_evento) == 1
    assert righe_evento[0][6] == {"action": "value_history_viewed"}
    assert righe_evento[0][4] == mondo["lead_sell"]


def test_7_due_volte_nello_stesso_giorno_una_riga(mondo, modulo):
    scrivi_snapshot(mondo, mondo["w_a"])
    for _ in range(3):
        modulo["tracking"].track_action(mondo["acc"], mondo["st_a"], "value_history_viewed")
    assert len(eventi(mondo, "owner_value_history_viewed")) == 1


def test_8_senza_domanda_nessun_evento_domanda(mondo, modulo):
    assert modulo["tracking"].track_action(
        mondo["acc"], mondo["st_a"], "buyer_demand_viewed") is False
    assert eventi(mondo, "owner_buyer_demand_viewed") == []


def test_8b_una_rilevazione_illeggibile_non_e_una_domanda_disponibile(mondo, modulo):
    with mondo["conn"].cursor() as cur:
        cur.execute("INSERT INTO property_watch_observations (watch_id,observation_type,"
                    "source,payload,idempotency_key) VALUES (%s,'buyer_pressure_snapshot',"
                    "'internal',%s,'k-rotto')",
                    (mondo["w_a"], json.dumps({"score": 82, "budget_reference": 210000})))
    mondo["conn"].commit()
    assert modulo["tracking"].track_action(
        mondo["acc"], mondo["st_a"], "buyer_demand_viewed") is False
    assert eventi(mondo, "owner_buyer_demand_viewed") == []


def test_9_con_la_domanda_l_azione_registra(mondo, modulo):
    scrivi_pressure(mondo, mondo["w_a"])
    assert modulo["tracking"].track_action(
        mondo["acc"], mondo["st_a"], "buyer_demand_viewed") is True
    righe_evento = eventi(mondo, "owner_buyer_demand_viewed")
    assert len(righe_evento) == 1
    assert righe_evento[0][6] == {"action": "buyer_demand_viewed"}


def test_10_doppio_click_una_riga(mondo, modulo):
    scrivi_pressure(mondo, mondo["w_a"])
    for _ in range(3):
        modulo["tracking"].track_action(mondo["acc"], mondo["st_a"], "buyer_demand_viewed")
    assert len(eventi(mondo, "owner_buyer_demand_viewed")) == 1


def test_10b_un_azione_fuori_dall_insieme_non_registra(mondo, modulo):
    for finta in ("comparables_viewed", "home_updated", "consultation_requested",
                  "owner_home_viewed", "", "DROP TABLE"):
        assert modulo["tracking"].track_action(mondo["acc"], mondo["st_a"], finta) is False
    assert eventi(mondo) == []


# ---------------------------------------------------------------------------
# 11-15: tenancy e grant
# ---------------------------------------------------------------------------

def test_11_owner_di_a_non_registra_sulla_stima_di_b(mondo, modulo):
    assert modulo["tracking"].track_home_viewed(mondo["acc"], mondo["st_b"]) is False
    assert eventi(mondo) == []


def test_12_e_nemmeno_con_un_azione(mondo, modulo):
    assert modulo["tracking"].track_action(
        mondo["acc"], mondo["st_b"], "buyer_demand_viewed") is False
    assert eventi(mondo) == []


def test_12b_il_404_neutro_arriva_prima_di_qualunque_evento(mondo, modulo):
    from core.exceptions import NotFoundError
    with pytest.raises(NotFoundError):
        modulo["home_service"].get_home(mondo["acc"], mondo["st_b"])
    assert eventi(mondo) == []


@pytest.mark.parametrize("rottura", [
    "UPDATE owner_stima_access SET revoked_at = NOW(), access_status='revoked'",
    "UPDATE owner_stima_access SET valid_until = NOW() - INTERVAL '1 day', "
    "valid_from = NOW() - INTERVAL '2 days'",
    "UPDATE owner_stima_access SET access_status = 'expired'",
])
def test_13_e_14_grant_non_valido_nessun_evento(mondo, modulo, rottura):
    scrivi_pressure(mondo, mondo["w_a"])
    with mondo["conn"].cursor() as cur:
        cur.execute(rottura)
    mondo["conn"].commit()
    assert modulo["tracking"].track_home_viewed(mondo["acc"], mondo["st_a"]) is False
    assert modulo["tracking"].track_action(
        mondo["acc"], mondo["st_a"], "buyer_demand_viewed") is False
    assert eventi(mondo) == []


def test_15_il_contatto_viene_dall_account_non_dal_client(mondo, modulo):
    contesto = modulo["owner_repository"].home_tracking_context(mondo["acc"], mondo["st_a"])
    assert contesto["contact_id"] == mondo["contact"]
    assert contesto["agency_id"] == mondo["a"]
    import inspect
    firma = inspect.signature(modulo["owner_repository"].home_tracking_context)
    assert list(firma.parameters) == ["owner_account_id", "stima_id"], \
        "nessun agency_id, nessun contact_id fra i parametri"


# ---------------------------------------------------------------------------
# 16-17: la risoluzione del lead, quando i candidati sono piu' di uno
# ---------------------------------------------------------------------------

def test_16_fra_piu_lead_vince_quello_sell_di_questo_contatto(mondo, modulo):
    """Tre distrattori: un BUY dello stesso contatto, un SELL di un altro
    contatto nella stessa agenzia, e un SELL dell'altra agenzia."""
    with mondo["conn"].cursor() as cur:
        cur.execute("INSERT INTO leads (contact_id,agency_id,pipeline) VALUES (%s,%s,'buy') "
                    "RETURNING id", (mondo["contact"], mondo["a"]))
        buy = cur.fetchone()[0]
        cur.execute("INSERT INTO contacts (agency_id, display_name) VALUES (%s,'Altro') "
                    "RETURNING id", (mondo["a"],))
        altro = cur.fetchone()[0]
        cur.execute("INSERT INTO leads (contact_id,agency_id,pipeline) VALUES (%s,%s,'sell') "
                    "RETURNING id", (altro, mondo["a"]))
        sell_altrui = cur.fetchone()[0]
        cur.execute("INSERT INTO contacts (agency_id, display_name) VALUES (%s,'B') "
                    "RETURNING id", (mondo["b"],))
        contatto_b = cur.fetchone()[0]
        cur.execute("INSERT INTO leads (contact_id,agency_id,pipeline) VALUES (%s,%s,'sell') "
                    "RETURNING id", (contatto_b, mondo["b"]))
        sell_altro_tenant = cur.fetchone()[0]
        for lead in (buy, sell_altrui, sell_altro_tenant):
            cur.execute("INSERT INTO lead_stime (lead_id,stima_id,relation_type) "
                        "VALUES (%s,%s,'related')", (lead, mondo["st_a"]))
    mondo["conn"].commit()

    contesto = modulo["owner_repository"].home_tracking_context(mondo["acc"], mondo["st_a"])
    assert contesto["lead_id"] == mondo["lead_sell"], "il SELL 'origin' di questo contatto"


def test_16b_senza_un_lead_ammissibile_l_evento_si_scrive_lo_stesso(mondo, modulo):
    """Meglio un evento con contatto e stima che un evento attribuito al lead
    sbagliato."""
    with mondo["conn"].cursor() as cur:
        cur.execute("DELETE FROM lead_stime")
        cur.execute("DELETE FROM leads")
    mondo["conn"].commit()

    contesto = modulo["owner_repository"].home_tracking_context(mondo["acc"], mondo["st_a"])
    assert contesto["lead_id"] is None
    assert modulo["tracking"].track_home_viewed(mondo["acc"], mondo["st_a"]) is True
    riga = eventi(mondo, "owner_home_viewed")[0]
    assert riga[4] is None and riga[3] == mondo["contact"] and riga[5] == mondo["st_a"]


def test_17_fra_due_sell_dello_stesso_contatto_vince_origin(mondo, modulo):
    with mondo["conn"].cursor() as cur:
        cur.execute("INSERT INTO leads (contact_id,agency_id,pipeline) VALUES (%s,%s,'sell') "
                    "RETURNING id", (mondo["contact"], mondo["a"]))
        secondo = cur.fetchone()[0]
        # Legato DOPO, ma come 'related': l'origine resta l'altro.
        cur.execute("INSERT INTO lead_stime (lead_id,stima_id,relation_type) "
                    "VALUES (%s,%s,'related')", (secondo, mondo["st_a"]))
    mondo["conn"].commit()
    contesto = modulo["owner_repository"].home_tracking_context(mondo["acc"], mondo["st_a"])
    assert contesto["lead_id"] == mondo["lead_sell"]


# ---------------------------------------------------------------------------
# 18-20: la proiezione, costruita sugli eventi veri
# ---------------------------------------------------------------------------

class ScopeOperatore:
    def __init__(self, agency_id):
        self._a = agency_id

    def require_agency(self):
        return self._a


def test_18_la_proiezione_legge_gli_eventi_appena_scritti(mondo, modulo):
    scrivi_snapshot(mondo, mondo["w_a"])
    adesso = datetime.now(timezone.utc)
    modulo["tracking"].track_home_viewed(mondo["acc"], mondo["st_a"],
                                         when=adesso - timedelta(days=3))
    modulo["tracking"].track_home_viewed(mondo["acc"], mondo["st_a"], when=adesso)
    modulo["tracking"].track_action(mondo["acc"], mondo["st_a"], "value_history_viewed",
                                    when=adesso)

    vista = modulo["interest"].interest_for_stima_scoped(
        ScopeOperatore(mondo["a"]), mondo["st_a"])["interest"]
    assert vista["active_days_30d"] == 2
    assert vista["active_days_7d"] == 2
    assert vista["home_viewed_days_30d"] == 2
    assert vista["value_history_viewed"] is True
    assert vista["buyer_demand_viewed"] is False
    assert vista["level"] == "high"
    assert vista["reasons"]
    assert "score" not in json.dumps(vista).lower()


def test_19_la_proiezione_di_un_altra_agenzia_non_e_raggiungibile(mondo, modulo):
    modulo["tracking"].track_home_viewed(mondo["acc"], mondo["st_a"])
    vista = modulo["interest"].interest_for_stima_scoped(
        ScopeOperatore(mondo["b"]), mondo["st_a"])["interest"]
    assert vista["level"] == "none"
    assert vista["active_days_30d"] == 0


def test_20_gli_eventi_del_funnel_non_contano_come_attivita(mondo, modulo):
    with mondo["conn"].cursor() as cur:
        cur.execute("INSERT INTO seller_timeline_events (agency_id,stima_id,event_type,"
                    "event_source,payload,idempotency_key) VALUES "
                    "(%s,%s,'stima_richiesta','stima360_it','{}','k-funnel')",
                    (mondo["a"], mondo["st_a"]))
    mondo["conn"].commit()
    vista = modulo["interest"].interest_for_stima_scoped(
        ScopeOperatore(mondo["a"]), mondo["st_a"])["interest"]
    assert vista["level"] == "none"
    assert vista["last_activity_at"] is None


def test_20b_il_payload_nel_database_non_porta_niente_di_personale(mondo, modulo):
    scrivi_pressure(mondo, mondo["w_a"])
    modulo["home_service"].get_home(mondo["acc"], mondo["st_a"])
    modulo["tracking"].track_action(mondo["acc"], mondo["st_a"], "buyer_demand_viewed")
    testo = json.dumps([r[6] for r in eventi(mondo)]).lower()
    for vietato in ("mario", "rossi", "@example.it", "333", "238000", "score",
                    "budget", "pressure", "ip", "agent"):
        assert vietato not in testo, vietato


# ---------------------------------------------------------------------------
# 21-22: il confine fail-open, provato facendo rompere il dominio
# ---------------------------------------------------------------------------

def test_21_se_seller_intelligence_esplode_il_proprietario_vede_la_casa(mondo, modulo,
                                                                        monkeypatch):
    """La proprieta' che giustifica l'eccezione alla regola di isolamento.

    Non basta che il tracciamento sia avvolto da un try: deve esserlo TUTTO
    il percorso, lettura del contesto compresa. Qui si fa sollevare il
    dominio e si verifica che `get_home` risponda come se niente fosse.
    """
    from seller_intelligence import service as si

    def esplode(*_a, **_k):
        raise RuntimeError("seller intelligence giu'")

    monkeypatch.setattr(si, "record_event_scoped", esplode)
    vista = modulo["home_service"].get_home(mondo["acc"], mondo["st_a"])
    assert vista["stima_id"] == mondo["st_a"]
    assert vista["valuation"]["initial_value"] == 185000
    assert eventi(mondo) == []


def test_22_anche_se_la_lettura_del_contesto_esplode(mondo, modulo, monkeypatch):
    """Il caso che un try messo troppo in basso avrebbe lasciato scoperto:
    la prima cosa che tocca il database e' la risoluzione del contesto."""
    from owner import repository as owner_repository

    def esplode(*_a, **_k):
        raise RuntimeError("database giu'")

    monkeypatch.setattr(owner_repository, "home_tracking_context", esplode)
    vista = modulo["home_service"].get_home(mondo["acc"], mondo["st_a"])
    assert vista["stima_id"] == mondo["st_a"]
    assert modulo["tracking"].track_action(
        mondo["acc"], mondo["st_a"], "buyer_demand_viewed") is False


def test_22b_e_anche_se_esplode_il_controllo_della_capability(mondo, modulo, monkeypatch):
    scrivi_pressure(mondo, mondo["w_a"])
    from owner import repository as owner_repository

    def esplode(*_a, **_k):
        raise RuntimeError("lettura giu'")

    monkeypatch.setattr(owner_repository, "home_buyer_pressure", esplode)
    assert modulo["tracking"].track_action(
        mondo["acc"], mondo["st_a"], "buyer_demand_viewed") is False
    assert eventi(mondo) == []


# ---------------------------------------------------------------------------
# 23-31: LA ROTTA. Sempre 204, e mai un oracolo.
#
# I test sopra provano il SERVIZIO, che torna False quando non registra. Non
# e' la stessa cosa che provare la ROTTA: fra i due c'e' il livello che
# traduce un esito in uno stato HTTP, ed e' li' che nasce un oracolo. Se
# `POST /homes/{id}/events` rispondesse 404 per una stima altrui e 204 per
# una propria, basterebbero due richieste per sapere quali stime esistono -
# e il 404 neutro della GET, che esiste proprio per impedirlo, sarebbe
# aggirabile dalla porta accanto.
#
# Quindi qui si guarda solo il codice di stato, su tutti i modi in cui la
# richiesta puo' non produrre niente.
# ---------------------------------------------------------------------------

@pytest.fixture
def client(db, modulo, mondo):
    """Il portale vero, con la sola sessione sostituita.

    `current_owner` legge un cookie e non c'entra con cio' che si sta
    provando: l'autenticazione e' un cancello a monte, la regola riguarda
    cosa succede DOPO che qualcuno e' entrato legittimamente.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from owner.dependencies import current_owner
    from owner.router_portal import router as portal_router

    app = FastAPI()
    app.include_router(portal_router)
    stato = {"account": mondo["acc"]}
    app.dependency_overrides[current_owner] = lambda: {"owner_account_id": stato["account"]}
    cliente = TestClient(app)
    cliente.owner = stato
    return cliente


def evento_post(client, stima_id, action="buyer_demand_viewed"):
    return client.post(f"/api/owner/portal/homes/{stima_id}/events",
                       json={"action": action})


def test_23_owner_corretto_e_azione_valida_204_ed_evento(mondo, modulo, client):
    scrivi_pressure(mondo, mondo["w_a"])
    risposta = evento_post(client, mondo["st_a"])
    assert risposta.status_code == 204
    assert risposta.content == b""
    assert len(eventi(mondo, "owner_buyer_demand_viewed")) == 1


def test_24_stima_inesistente_204_e_zero_eventi(mondo, modulo, client):
    inesistente = mondo["st_a"] + 99_000
    risposta = evento_post(client, inesistente)
    assert risposta.status_code == 204
    assert eventi(mondo) == []


def test_25_altro_owner_204_e_zero_eventi(mondo, modulo, client):
    """Un account che esiste ma non ha nessun grant su questa stima."""
    scrivi_pressure(mondo, mondo["w_a"])
    with mondo["conn"].cursor() as cur:
        cur.execute("INSERT INTO contacts (agency_id, display_name) VALUES (%s,'Estraneo') "
                    "RETURNING id", (mondo["a"],))
        contatto = cur.fetchone()[0]
        cur.execute("INSERT INTO owner_accounts (contact_id,status) VALUES (%s,'active') "
                    "RETURNING id", (contatto,))
        client.owner["account"] = cur.fetchone()[0]
    mondo["conn"].commit()

    risposta = evento_post(client, mondo["st_a"])
    assert risposta.status_code == 204
    assert eventi(mondo) == []


def test_26_altro_tenant_204_e_zero_eventi(mondo, modulo, client):
    """L'owner dell'agenzia B punta alla stima dell'agenzia A."""
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

    risposta = evento_post(client, mondo["st_a"])
    assert risposta.status_code == 204
    assert eventi(mondo) == []


@pytest.mark.parametrize("caso,rottura", [
    ("revoked", "UPDATE owner_stima_access SET revoked_at = NOW(), access_status='revoked'"),
    ("expired", "UPDATE owner_stima_access SET valid_until = NOW() - INTERVAL '1 day', "
                "valid_from = NOW() - INTERVAL '2 days'"),
])
def test_27_e_28_grant_non_valido_204_e_zero_eventi(mondo, modulo, client, caso, rottura):
    scrivi_pressure(mondo, mondo["w_a"])
    with mondo["conn"].cursor() as cur:
        cur.execute(rottura)
    mondo["conn"].commit()

    risposta = evento_post(client, mondo["st_a"])
    assert risposta.status_code == 204, caso
    assert eventi(mondo) == [], caso


def test_29_capability_assente_204_e_zero_eventi(mondo, modulo, client):
    """Il grant e' valido, la casa e' sua, ma non c'e' nessuna rilevazione
    da guardare: niente da registrare, e la risposta non lo dice."""
    risposta = evento_post(client, mondo["st_a"], "buyer_demand_viewed")
    assert risposta.status_code == 204
    risposta = evento_post(client, mondo["st_a"], "value_history_viewed")
    assert risposta.status_code == 204
    assert eventi(mondo) == []


def test_30_un_guasto_di_seller_intelligence_resta_204(mondo, modulo, client, monkeypatch):
    scrivi_pressure(mondo, mondo["w_a"])
    from seller_intelligence import service as si

    def esplode(*_a, **_k):
        raise RuntimeError("seller intelligence giu'")

    monkeypatch.setattr(si, "record_event_scoped", esplode)
    risposta = evento_post(client, mondo["st_a"])
    assert risposta.status_code == 204
    assert risposta.content == b""
    assert eventi(mondo) == []


def test_31_la_rotta_non_e_un_oracolo(mondo, modulo, client):
    """LA PROPRIETA', in una riga: la risposta non cambia.

    Stessa richiesta su una stima propria, una altrui, una di un altro
    tenant e una inesistente. Se anche solo uno dei quattro differisse -
    nello stato, nel corpo o in un header di contenuto - la rotta direbbe a
    chi prova gli id quali esistono.
    """
    scrivi_pressure(mondo, mondo["w_a"])
    risposte = [evento_post(client, stima) for stima in
                (mondo["st_a"], mondo["st_b"], mondo["st_senza_watch"],
                 mondo["st_a"] + 99_000)]
    assert {r.status_code for r in risposte} == {204}
    assert {r.content for r in risposte} == {b""}
    assert len({r.headers.get("content-type") for r in risposte}) == 1


def test_31b_un_azione_fuori_insieme_e_422_e_non_dipende_dalla_stima(mondo, modulo, client):
    """Il 422 c'e', ma non e' un oracolo: dipende dal CORPO, non dalla stima.

    Una richiesta malformata resta malformata su qualunque id, quindi non
    distingue una stima esistente da una inventata.
    """
    propria = client.post(f"/api/owner/portal/homes/{mondo['st_a']}/events",
                          json={"action": "comparables_viewed"})
    altrui = client.post(f"/api/owner/portal/homes/{mondo['st_b']}/events",
                         json={"action": "comparables_viewed"})
    inventata = client.post(f"/api/owner/portal/homes/{mondo['st_a'] + 99_000}/events",
                            json={"action": "comparables_viewed"})
    assert {r.status_code for r in (propria, altrui, inventata)} == {422}
    assert eventi(mondo) == []


def test_31c_la_rotta_non_puo_rispondere_404(mondo, modulo, client):
    """`nf()` e `HTTPException` non compaiono nella rotta degli eventi: la
    traduzione di un esito in uno stato e' proprio cio' che non deve
    esistere qui."""
    import ast
    import inspect

    from owner import router_portal

    albero = ast.parse(inspect.getsource(router_portal))
    for nodo in albero.body:
        if isinstance(nodo, ast.FunctionDef) and nodo.name == "home_event":
            rotta = nodo
            break
    else:
        raise AssertionError("rotta home_event non trovata")

    # Il CORPO, senza i decoratori: `status_code=204` sta nel decoratore ed
    # e' esattamente cio' che si vuole, mentre nel corpo non deve esserci
    # niente che traduca un esito in uno stato.
    corpo = "\n".join(ast.unparse(riga) for riga in rotta.body)
    for vietato in ("nf(", "HTTPException", "raise", "status_code", "Response"):
        assert vietato not in corpo, vietato
    assert "track_action" in corpo
    assert corpo.rstrip().endswith("return None")

    decoratori = "\n".join(ast.unparse(d) for d in rotta.decorator_list)
    assert "status_code=204" in decoratori, decoratori
