"""LMC-13 su PostgreSQL reale: la coorte, il ritorno, la tenancy.

SEI COSE CHE UN DOPPIO NON PUO' PROVARE.

L'UNITA'. Che una casa con proprietario, comproprietario e delegato conti UNA
volta in ogni `*_homes`, e che le persone si contino invece in
`activated_owners`. E' la decisione centrale di LMC-13B e si prova solo
scrivendo tre grant veri sulla stessa stima.

LA COORTE. Che `cohort_at` sia davvero `MIN(created_at)` fra i grant della
casa, che il confine UTC sia semiaperto, e che un evento anteriore
all'ingresso non entri.

IL RITORNO. Che due giorni UTC distinti dello STESSO proprietario facciano
returning, e che due proprietari diversi un giorno ciascuno NON lo facciano.
E' la differenza che un `COUNT(DISTINCT giorno)` per casa non vedrebbe.

LA TENANCY. Che il giro di un'agenzia non veda le case, i grant e gli eventi
dell'altra, e che un grant le cui due radici smettono di concordare esca da
tutti i conteggi.

LO STOCK. Che un grant revocato o scaduto tolga la casa da `active_homes_now`
ma la lasci nella coorte storica, e che un secondo grant valido la tenga.

IL CONFINE FRA NULL E ZERO, su dati veri: coorte vuota, tutti i conteggi a 0
e tutti i tassi `null`.

Opt-in: senza `P29_TEST_DSN` si salta tutto. Database usa-e-getta.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

DSN = os.getenv("P29_TEST_DSN")

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare LMC-13")

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
CREATE TABLE stime (
    id SERIAL PRIMARY KEY,
    agency_id BIGINT NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,
    comune VARCHAR(100), microzona VARCHAR(100), via VARCHAR(100), civico VARCHAR(20),
    tipologia VARCHAR(50), mq INTEGER,
    nome VARCHAR(50), cognome VARCHAR(50), email VARCHAR(100), telefono VARCHAR(30),
    data TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE leads (
    id BIGSERIAL PRIMARY KEY,
    contact_id BIGINT NOT NULL REFERENCES contacts(id) ON DELETE RESTRICT,
    agency_id BIGINT NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,
    pipeline VARCHAR(20) NOT NULL DEFAULT 'general',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
CREATE TABLE properties (
    id BIGSERIAL PRIMARY KEY,
    agency_id BIGINT NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,
    title VARCHAR(200));
CREATE TABLE activities (id BIGSERIAL PRIMARY KEY);
CREATE TABLE seller_timeline_events (
    id BIGSERIAL PRIMARY KEY,
    agency_id BIGINT NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,
    contact_id BIGINT REFERENCES contacts(id) ON DELETE SET NULL,
    lead_id BIGINT REFERENCES leads(id) ON DELETE SET NULL,
    stima_id INTEGER REFERENCES stime(id) ON DELETE SET NULL,
    property_id BIGINT REFERENCES properties(id) ON DELETE SET NULL,
    event_type VARCHAR(50) NOT NULL, event_source VARCHAR(30),
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    idempotency_key VARCHAR(255), created_by VARCHAR(200),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
CREATE UNIQUE INDEX idx_ste_idem ON seller_timeline_events (idempotency_key)
    WHERE idempotency_key IS NOT NULL;
CREATE TABLE schema_migrations (version VARCHAR(200) PRIMARY KEY);
"""

CATENA = ("009_owner_01", "066_lmc1_owner_stima_access")


def _dsn_per(nome: str) -> str:
    if "?" in DSN:
        base, query = DSN.split("?", 1)
        return base.rsplit("/", 1)[0] + "/" + nome + "?" + query
    return DSN.rsplit("/", 1)[0] + "/" + nome


@pytest.fixture(scope="module")
def db():
    psycopg2 = pytest.importorskip("psycopg2")

    nome = f"lmc13_probe_{os.getpid()}_{uuid.uuid4().hex[:6]}"
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
    from owner import home_metrics
    from owner import repository as owner_repository

    monkeypatch.setattr(core_database, "get_connection", lambda: psycopg2.connect(db["dsn"]))
    return {"repository": owner_repository, "metrics": home_metrics}


GIORNO = timedelta(days=1)
#: "Adesso" del test. Le coorti si misurano all'indietro da qui.
ORA = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def mondo(db):
    """Due agenzie vuote, e le funzioni per popolarle a piacere."""
    conn = db["conn"]
    conn.rollback()
    with conn.cursor() as cur:
        for tabella in ("seller_timeline_events", "owner_stima_access", "owner_accounts",
                        "leads", "stime", "properties", "contacts", "agencies"):
            cur.execute(f"DELETE FROM {tabella}")
        cur.execute("INSERT INTO agencies (slug) VALUES ('a-uno') RETURNING id"); a = cur.fetchone()[0]
        cur.execute("INSERT INTO agencies (slug) VALUES ('b-due') RETURNING id"); b = cur.fetchone()[0]
    conn.commit()

    stato = {"conn": conn, "a": a, "b": b, "seq": 0}

    def stima(agency, via="Via Trieste"):
        with conn.cursor() as cur:
            cur.execute("INSERT INTO stime (agency_id,comune,microzona,via,civico,mq,"
                        "nome,cognome,email,telefono) VALUES (%s,'Alba Adriatica','Villa Fiore',"
                        "%s,'12',95,'Mario','Rossi','mario@example.it','+39 333 1234567') RETURNING id",
                        (agency, via))
            i = cur.fetchone()[0]
        conn.commit()
        return i

    def account(agency, etichetta):
        with conn.cursor() as cur:
            cur.execute("INSERT INTO contacts (agency_id, display_name, email, email_normalized) "
                        "VALUES (%s,%s,%s,%s) RETURNING id",
                        (agency, etichetta, f"{etichetta}@example.it", f"{etichetta}@example.it"))
            contatto = cur.fetchone()[0]
            cur.execute("INSERT INTO owner_accounts (contact_id,status) VALUES (%s,'active') RETURNING id",
                        (contatto,))
            acc = cur.fetchone()[0]
        conn.commit()
        return {"account": acc, "contact": contatto}

    def grant(acc, stima_id, *, quando=None, ruolo="owner", stato_grant="active",
              revocato=None, valido_fino=None):
        with conn.cursor() as cur:
            cur.execute("INSERT INTO owner_stima_access "
                        "(owner_account_id,stima_id,access_role,access_status,created_at,"
                        " valid_from,revoked_at,valid_until,granted_by) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'LMC_PROVISIONING') RETURNING id",
                        (acc["account"], stima_id, ruolo, stato_grant,
                         quando or ORA - 10 * GIORNO, (quando or ORA - 10 * GIORNO) - GIORNO,
                         revocato, valido_fino))
            i = cur.fetchone()[0]
        conn.commit()
        return i

    def evento(agency, acc, stima_id, tipo, *, quando):
        """Un evento del portale nella forma che LMC-7 scrive davvero."""
        stato["seq"] += 1
        chiave = (f"owner_portal:{tipo}:owner:{acc['account']}:stima:{stima_id}"
                  f":day:{quando.astimezone(timezone.utc).date().isoformat()}")
        with conn.cursor() as cur:
            cur.execute("INSERT INTO seller_timeline_events "
                        "(agency_id,contact_id,stima_id,event_type,event_source,occurred_at,"
                        " payload,idempotency_key) "
                        "VALUES (%s,%s,%s,%s,'owner_portal',%s,%s,%s) "
                        "ON CONFLICT (idempotency_key) WHERE idempotency_key IS NOT NULL "
                        "DO NOTHING RETURNING id",
                        (agency, acc["contact"], stima_id, tipo, quando,
                         json.dumps({"action": tipo.replace("owner_", "", 1)}), chiave))
            r = cur.fetchone()
        conn.commit()
        return r[0] if r else None

    stato.update(stima=stima, account=account, grant=grant, evento=evento)
    return stato


def metriche(modulo, mondo, agency=None, *, days=30, now=ORA):
    """Il DTO completo, come lo produrrebbe la rotta."""
    da, a = modulo["metrics"].window(days, now=now)
    conteggi, inizio = modulo["repository"].home_metrics_counts(
        agency if agency is not None else mondo["a"], cohort_from=da, cohort_to=a)
    return modulo["metrics"].build(conteggi, days=days, cohort_from=da, cohort_to=a,
                                   measurement_started_at=inizio)


VISTA = "owner_home_viewed"
VALORE = "owner_value_history_viewed"
DOMANDA = "owner_buyer_demand_viewed"
AGGIORNATA = "owner_home_updated"
CONSULENZA = "owner_consultation_requested"


# ---------------------------------------------------------------------------
# A - L'UNITA' E' LA CASA (test 2, 3, 4, 5)
# ---------------------------------------------------------------------------

def test_2_una_stima_con_un_solo_owner_e_una_home(mondo, modulo):
    st = mondo["stima"](mondo["a"])
    mondo["grant"](mondo["account"](mondo["a"], "mario"), st)
    d = metriche(modulo, mondo)
    assert d["cohort_homes"] == 1 and d["activated_owners"] == 1
    assert d["unit"] == "stima"


def test_3_una_stima_con_owner_e_co_owner_resta_UNA_home(mondo, modulo):
    """LA DECISIONE CENTRALE. Tre grant sulla stessa casa: una opportunita'."""
    st = mondo["stima"](mondo["a"])
    uno = mondo["account"](mondo["a"], "mario")
    due = mondo["account"](mondo["a"], "luisa")
    tre = mondo["account"](mondo["a"], "delegato")
    mondo["grant"](uno, st, ruolo="owner")
    mondo["grant"](due, st, ruolo="co_owner")
    mondo["grant"](tre, st, ruolo="delegate")
    # E tutti e tre la aprono, in giorni diversi.
    for i, acc in enumerate((uno, due, tre)):
        mondo["evento"](mondo["a"], acc, st, VISTA, quando=ORA - (5 - i) * GIORNO)
    d = metriche(modulo, mondo)
    assert d["cohort_homes"] == 1, "tre grant, una casa"
    assert d["viewed_homes"] == 1, "tre aperture, una casa"
    assert d["active_homes_now"] == 1
    # 4: le persone, invece, si contano tutte.
    assert d["activated_owners"] == 3
    # E i tassi non superano mai 1.
    assert d["rates"]["view_rate"] == 1.0


def test_5_un_owner_con_tre_stime_fa_tre_homes(mondo, modulo):
    acc = mondo["account"](mondo["a"], "mario")
    for i in range(3):
        mondo["grant"](acc, mondo["stima"](mondo["a"], via=f"Via {i}"))
    d = metriche(modulo, mondo)
    assert d["cohort_homes"] == 3 and d["activated_owners"] == 1


# ---------------------------------------------------------------------------
# B - LA COORTE (test 6, 7, 8)
# ---------------------------------------------------------------------------

def test_6_cohort_at_e_il_minimo_dei_grant_della_stima(mondo, modulo):
    """Il secondo grant, piu' recente, non deve spostare la casa in avanti."""
    st = mondo["stima"](mondo["a"])
    vecchio = mondo["account"](mondo["a"], "mario")
    nuovo = mondo["account"](mondo["a"], "luisa")
    mondo["grant"](vecchio, st, quando=ORA - 100 * GIORNO)   # fuori dai 30 giorni
    mondo["grant"](nuovo, st, quando=ORA - 2 * GIORNO)       # dentro
    # Con MIN, la casa e' entrata 100 giorni fa: non e' nella coorte a 30.
    assert metriche(modulo, mondo, days=30)["cohort_homes"] == 0
    # A 365 giorni c'e'.
    d = metriche(modulo, mondo, days=365)
    assert d["cohort_homes"] == 1 and d["activated_owners"] == 2


def test_7_il_confine_della_coorte_e_utc_e_semiaperto(mondo, modulo):
    acc = mondo["account"](mondo["a"], "mario")
    dentro = mondo["stima"](mondo["a"], via="dentro")
    fuori = mondo["stima"](mondo["a"], via="fuori")
    sul_bordo = mondo["stima"](mondo["a"], via="bordo")
    mondo["grant"](acc, dentro, quando=ORA - 30 * GIORNO + timedelta(seconds=1))
    mondo["grant"](acc, fuori, quando=ORA - 30 * GIORNO - timedelta(seconds=1))
    # Esattamente a cohort_from: incluso, perche' il confine basso e' chiuso.
    mondo["grant"](acc, sul_bordo, quando=ORA - 30 * GIORNO)
    d = metriche(modulo, mondo, days=30)
    assert d["cohort_homes"] == 2, d
    # Un grant nel futuro rispetto a cohort_to non entra: il confine alto e'
    # aperto.
    futuro = mondo["stima"](mondo["a"], via="futuro")
    mondo["grant"](acc, futuro, quando=ORA + GIORNO)
    assert metriche(modulo, mondo, days=30)["cohort_homes"] == 2


def test_8_un_evento_precedente_all_ingresso_non_conta(mondo, modulo):
    st = mondo["stima"](mondo["a"])
    acc = mondo["account"](mondo["a"], "mario")
    mondo["grant"](acc, st, quando=ORA - 5 * GIORNO)
    mondo["evento"](mondo["a"], acc, st, VISTA, quando=ORA - 6 * GIORNO)
    d = metriche(modulo, mondo)
    assert d["cohort_homes"] == 1 and d["viewed_homes"] == 0
    assert d["rates"]["view_rate"] == 0.0, "misurato e zero, non null"
    # Lo stesso evento dopo l'ingresso conta.
    mondo["evento"](mondo["a"], acc, st, VISTA, quando=ORA - 4 * GIORNO)
    assert metriche(modulo, mondo)["viewed_homes"] == 1


# ---------------------------------------------------------------------------
# C - VISITE E RITORNO (test 9, 10, 11, 12)
# ---------------------------------------------------------------------------

def test_9_viewed_homes_e_distinto_per_stima(mondo, modulo):
    st = mondo["stima"](mondo["a"])
    acc = mondo["account"](mondo["a"], "mario")
    mondo["grant"](acc, st)
    for g in (5, 4, 3):
        mondo["evento"](mondo["a"], acc, st, VISTA, quando=ORA - g * GIORNO)
    assert metriche(modulo, mondo)["viewed_homes"] == 1


def test_10_piu_aperture_lo_stesso_giorno_non_sono_un_ritorno(mondo, modulo):
    """L'idempotenza giornaliera di LMC-7 rende una seconda apertura una
    non-scrittura: il test la esercita davvero, non la assume."""
    st = mondo["stima"](mondo["a"])
    acc = mondo["account"](mondo["a"], "mario")
    mondo["grant"](acc, st)
    quando = ORA - 3 * GIORNO
    primo = mondo["evento"](mondo["a"], acc, st, VISTA, quando=quando)
    secondo = mondo["evento"](mondo["a"], acc, st, VISTA, quando=quando + timedelta(hours=6))
    assert primo is not None and secondo is None, "la seconda non scrive"
    d = metriche(modulo, mondo)
    assert d["viewed_homes"] == 1 and d["returning_homes"] == 0
    assert d["rates"]["return_rate"] == 0.0


def test_11_stesso_owner_in_due_giorni_utc_e_un_ritorno(mondo, modulo):
    st = mondo["stima"](mondo["a"])
    acc = mondo["account"](mondo["a"], "mario")
    mondo["grant"](acc, st)
    mondo["evento"](mondo["a"], acc, st, VISTA, quando=ORA - 3 * GIORNO)
    mondo["evento"](mondo["a"], acc, st, VISTA, quando=ORA - 2 * GIORNO)
    d = metriche(modulo, mondo)
    assert d["returning_homes"] == 1 and d["rates"]["return_rate"] == 1.0


def test_11b_due_giorni_utc_distinti_anche_a_cavallo_di_mezzanotte(mondo, modulo):
    st = mondo["stima"](mondo["a"])
    acc = mondo["account"](mondo["a"], "mario")
    mondo["grant"](acc, st, quando=ORA - 10 * GIORNO)
    mezzanotte = datetime(2026, 9, 18, 0, 0, tzinfo=timezone.utc)
    mondo["evento"](mondo["a"], acc, st, VISTA, quando=mezzanotte - timedelta(minutes=30))
    mondo["evento"](mondo["a"], acc, st, VISTA, quando=mezzanotte + timedelta(minutes=30))
    assert metriche(modulo, mondo)["returning_homes"] == 1


def test_12_due_owner_diversi_un_giorno_ciascuno_NON_e_un_ritorno(mondo, modulo):
    """LA DISTINZIONE CHE UN CONTEGGIO PER CASA NON VEDREBBE. Owner A apre il
    giorno 1, owner B il giorno 2: sono due prime visite, non un
    proprietario che e' tornato."""
    st = mondo["stima"](mondo["a"])
    uno = mondo["account"](mondo["a"], "mario")
    due = mondo["account"](mondo["a"], "luisa")
    mondo["grant"](uno, st, ruolo="owner")
    mondo["grant"](due, st, ruolo="co_owner")
    mondo["evento"](mondo["a"], uno, st, VISTA, quando=ORA - 3 * GIORNO)
    mondo["evento"](mondo["a"], due, st, VISTA, quando=ORA - 2 * GIORNO)
    d = metriche(modulo, mondo)
    assert d["viewed_homes"] == 1
    assert d["returning_homes"] == 0, "due persone, non un ritorno"
    # Ma se UNO dei due torna davvero, allora si'.
    mondo["evento"](mondo["a"], uno, st, VISTA, quando=ORA - GIORNO)
    assert metriche(modulo, mondo)["returning_homes"] == 1


# ---------------------------------------------------------------------------
# D - INTERESSE E CONSULENZA (test 13, 14, 15, 16, 17)
# ---------------------------------------------------------------------------

def test_13_14_15_16_gli_interessi_e_la_loro_unione(mondo, modulo):
    acc = mondo["account"](mondo["a"], "mario")
    solo_valore = mondo["stima"](mondo["a"], via="valore")
    solo_domanda = mondo["stima"](mondo["a"], via="domanda")
    solo_update = mondo["stima"](mondo["a"], via="update")
    tutti = mondo["stima"](mondo["a"], via="tutti")
    niente = mondo["stima"](mondo["a"], via="niente")
    for st in (solo_valore, solo_domanda, solo_update, tutti, niente):
        mondo["grant"](acc, st)
    mondo["evento"](mondo["a"], acc, solo_valore, VALORE, quando=ORA - 3 * GIORNO)
    mondo["evento"](mondo["a"], acc, solo_domanda, DOMANDA, quando=ORA - 3 * GIORNO)
    mondo["evento"](mondo["a"], acc, solo_update, AGGIORNATA, quando=ORA - 3 * GIORNO)
    for tipo in (VALORE, DOMANDA, AGGIORNATA):
        mondo["evento"](mondo["a"], acc, tutti, tipo, quando=ORA - 3 * GIORNO)
    d = metriche(modulo, mondo)
    assert d["value_interest_homes"] == 2      # solo_valore + tutti
    assert d["demand_interest_homes"] == 2
    assert d["updated_homes"] == 2
    # 16: UNIONE, non somma. Quattro case, non sei.
    assert d["strong_interest_homes"] == 4
    assert d["cohort_homes"] == 5
    assert d["rates"]["strong_interest_rate"] == 0.8


def test_17_consultation(mondo, modulo):
    st = mondo["stima"](mondo["a"])
    altra = mondo["stima"](mondo["a"], via="altra")
    acc = mondo["account"](mondo["a"], "mario")
    mondo["grant"](acc, st)
    mondo["grant"](acc, altra)
    mondo["evento"](mondo["a"], acc, st, CONSULENZA, quando=ORA - GIORNO)
    d = metriche(modulo, mondo)
    assert d["consultation_homes"] == 1 and d["cohort_homes"] == 2
    assert d["rates"]["consultation_rate"] == 0.5


# ---------------------------------------------------------------------------
# E - STOCK E GRANT NON PIU' VALIDI (test 18, 19, 20)
# ---------------------------------------------------------------------------

def test_18_grant_revocato_resta_nella_coorte_ma_esce_dallo_stock(mondo, modulo):
    st = mondo["stima"](mondo["a"])
    acc = mondo["account"](mondo["a"], "mario")
    gid = mondo["grant"](acc, st, quando=ORA - 5 * GIORNO)
    assert metriche(modulo, mondo)["active_homes_now"] == 1
    with mondo["conn"].cursor() as cur:
        cur.execute("UPDATE owner_stima_access SET access_status='revoked', revoked_at=NOW() "
                    "WHERE id=%s", (gid,))
    mondo["conn"].commit()
    d = metriche(modulo, mondo)
    assert d["cohort_homes"] == 1, "la storia non si riscrive"
    assert d["active_homes_now"] == 0


def test_19_grant_scaduto_idem(mondo, modulo):
    st = mondo["stima"](mondo["a"])
    acc = mondo["account"](mondo["a"], "mario")
    gid = mondo["grant"](acc, st, quando=ORA - 5 * GIORNO)
    with mondo["conn"].cursor() as cur:
        cur.execute("UPDATE owner_stima_access SET valid_from=NOW()-INTERVAL '10 days', "
                    "valid_until=NOW()-INTERVAL '1 day' WHERE id=%s", (gid,))
    mondo["conn"].commit()
    d = metriche(modulo, mondo)
    assert d["cohort_homes"] == 1 and d["active_homes_now"] == 0


def test_20_un_secondo_grant_valido_tiene_la_casa_nello_stock(mondo, modulo):
    st = mondo["stima"](mondo["a"])
    uno = mondo["account"](mondo["a"], "mario")
    due = mondo["account"](mondo["a"], "luisa")
    gid = mondo["grant"](uno, st, quando=ORA - 5 * GIORNO)
    mondo["grant"](due, st, quando=ORA - 4 * GIORNO, ruolo="co_owner")
    with mondo["conn"].cursor() as cur:
        cur.execute("UPDATE owner_stima_access SET access_status='revoked', revoked_at=NOW() "
                    "WHERE id=%s", (gid,))
    mondo["conn"].commit()
    d = metriche(modulo, mondo)
    assert d["active_homes_now"] == 1, "l'altro grant e' ancora valido"
    assert d["cohort_homes"] == 1


def test_20b_un_account_disabilitato_non_tiene_in_piedi_lo_stock(mondo, modulo):
    st = mondo["stima"](mondo["a"])
    acc = mondo["account"](mondo["a"], "mario")
    mondo["grant"](acc, st, quando=ORA - 5 * GIORNO)
    with mondo["conn"].cursor() as cur:
        cur.execute("UPDATE owner_accounts SET status='disabled' WHERE id=%s", (acc["account"],))
    mondo["conn"].commit()
    assert metriche(modulo, mondo)["active_homes_now"] == 0


# ---------------------------------------------------------------------------
# F - TENANCY (test 1, 26)
# ---------------------------------------------------------------------------

def test_1_isolamento_fra_tenant(mondo, modulo):
    st_a = mondo["stima"](mondo["a"], via="di A")
    st_b = mondo["stima"](mondo["b"], via="di B")
    acc_a = mondo["account"](mondo["a"], "mario")
    acc_b = mondo["account"](mondo["b"], "luisa")
    mondo["grant"](acc_a, st_a)
    mondo["grant"](acc_b, st_b)
    for st, acc, ag in ((st_a, acc_a, mondo["a"]), (st_b, acc_b, mondo["b"])):
        for tipo in (VISTA, VALORE, CONSULENZA):
            mondo["evento"](ag, acc, st, tipo, quando=ORA - 2 * GIORNO)
    da = metriche(modulo, mondo, mondo["a"])
    db_ = metriche(modulo, mondo, mondo["b"])
    for d in (da, db_):
        assert d["cohort_homes"] == 1 and d["viewed_homes"] == 1
        assert d["consultation_homes"] == 1 and d["activated_owners"] == 1
        assert d["active_homes_now"] == 1


def test_1b_un_evento_dell_altra_agenzia_non_entra(mondo, modulo):
    """Un evento scritto con l'agency sbagliata non deve poter gonfiare i
    conteggi: il filtro sull'agenzia dell'evento e il join sul grant sono due
    difese indipendenti."""
    st = mondo["stima"](mondo["a"])
    acc = mondo["account"](mondo["a"], "mario")
    mondo["grant"](acc, st)
    with mondo["conn"].cursor() as cur:
        cur.execute("INSERT INTO seller_timeline_events (agency_id,contact_id,stima_id,"
                    "event_type,event_source,occurred_at,idempotency_key) "
                    "VALUES (%s,%s,%s,%s,'owner_portal',%s,'chiave-b')",
                    (mondo["b"], acc["contact"], st, VISTA, ORA - GIORNO))
    mondo["conn"].commit()
    d = metriche(modulo, mondo, mondo["a"])
    assert d["viewed_homes"] == 0, "l'evento porta l'agenzia sbagliata"
    assert metriche(modulo, mondo, mondo["b"])["cohort_homes"] == 0


def test_1c_radici_in_disaccordo_escono_da_tutto(mondo, modulo):
    """Il contatto cambia agenzia dopo il grant: le due radici non
    concordano piu' e la casa esce da coorte, stock ed eventi."""
    st = mondo["stima"](mondo["a"])
    acc = mondo["account"](mondo["a"], "mario")
    mondo["grant"](acc, st)
    mondo["evento"](mondo["a"], acc, st, VISTA, quando=ORA - GIORNO)
    assert metriche(modulo, mondo, mondo["a"])["cohort_homes"] == 1
    with mondo["conn"].cursor() as cur:
        cur.execute("UPDATE contacts SET agency_id=%s WHERE id=%s", (mondo["b"], acc["contact"]))
    mondo["conn"].commit()
    for agenzia in (mondo["a"], mondo["b"]):
        d = metriche(modulo, mondo, agenzia)
        assert d["cohort_homes"] == 0 and d["viewed_homes"] == 0
        assert d["active_homes_now"] == 0 and d["activated_owners"] == 0


def test_26_nessun_aggregato_di_piattaforma(mondo, modulo):
    """Non esiste una chiamata "tutte le agenzie": `agency_id` e' obbligatorio
    e produce sempre un solo tenant. La somma dei due tenant non e' ottenibile
    da una chiamata sola."""
    import inspect
    st_a = mondo["stima"](mondo["a"]); st_b = mondo["stima"](mondo["b"])
    mondo["grant"](mondo["account"](mondo["a"], "mario"), st_a)
    mondo["grant"](mondo["account"](mondo["b"], "luisa"), st_b)
    assert metriche(modulo, mondo, mondo["a"])["cohort_homes"] == 1
    assert metriche(modulo, mondo, mondo["b"])["cohort_homes"] == 1
    firma = inspect.signature(modulo["repository"].home_metrics_counts)
    assert firma.parameters["agency_id"].default is inspect.Parameter.empty
    with pytest.raises(TypeError):
        modulo["repository"].home_metrics_counts(cohort_from=ORA, cohort_to=ORA)


# ---------------------------------------------------------------------------
# G - NULL, ZERO, DTO (test 21, 22, 23, 24, 25)
# ---------------------------------------------------------------------------

def test_21_coorte_vuota_conteggi_zero_e_tassi_null(mondo, modulo):
    # Una casa esiste, ma e' entrata troppo tempo fa: coorte a 7 giorni vuota.
    st = mondo["stima"](mondo["a"])
    acc = mondo["account"](mondo["a"], "mario")
    mondo["grant"](acc, st, quando=ORA - 200 * GIORNO)
    mondo["evento"](mondo["a"], acc, st, VISTA, quando=ORA - GIORNO)
    d = metriche(modulo, mondo, days=7)
    assert d["cohort_homes"] == 0
    for nome in ("viewed_homes", "returning_homes", "consultation_homes",
                 "strong_interest_homes", "activated_owners"):
        assert d[nome] == 0, nome
    assert all(v is None for v in d["rates"].values())
    # Lo stock resta misurato: la casa esiste davvero.
    assert d["active_homes_now"] == 1


def test_22_inspection_e_mandate_null_finche_il_ponte_non_e_acceso(mondo, modulo):
    """AGGIORNATO DA LMC-15.

    Erano `null` per sempre, perche' non esisteva una fonte autorevole. Da
    LMC-15 esiste, ma su QUESTO database la 070 non e' applicata: restano
    `null`, e la ragione lo dice - "non attivo su questo ambiente" invece di
    "non esiste nessuna fonte". La prova che i numeri veri escono quando il
    ponte c'e' sta nei test di LMC-15, che quella migration la applicano.
    """
    st = mondo["stima"](mondo["a"])
    acc = mondo["account"](mondo["a"], "mario")
    mondo["grant"](acc, st)
    mondo["evento"](mondo["a"], acc, st, CONSULENZA, quando=ORA - GIORNO)
    d = metriche(modulo, mondo)
    assert d["inspection_homes"] is None and d["mandate_homes"] is None
    assert d["rates"]["inspection_rate"] is None and d["rates"]["mandate_rate"] is None
    assert d["measurement_started_at"] is None
    assert (d["not_measurable"]["inspection_homes"]
            == modulo["metrics"].REASON_NOT_APPLIED)
    assert (d["not_measurable"]["mandate_homes"]
            == modulo["metrics"].REASON_NOT_APPLIED)


#: LE CHIAVI APPROVATE, scritte a mano e non derivate dalle costanti del
#: modulo. La differenza e' la sostanza della prova: un elenco derivato da
#: `home_metrics.COHORT_COUNTS` direbbe soltanto che il DTO e' coerente con
#: se' stesso, e una chiave `email` aggiunta a quella costante passerebbe.
#: Qui l'elenco e' il contratto approvato, e ogni chiave in piu' o in meno
#: fa fallire il test.
CHIAVI_DTO_APPROVATE = {
    "period_days", "cohort_from", "cohort_to", "unit",
    "measurement_started_at",
    "cohort_homes", "active_homes_now", "activated_owners",
    "viewed_homes", "returning_homes",
    "value_interest_homes", "demand_interest_homes", "updated_homes",
    "strong_interest_homes", "consultation_homes",
    "inspection_homes", "mandate_homes",
    "rates", "not_measurable",
}
CHIAVI_RATES_APPROVATE = {
    "view_rate", "return_rate", "strong_interest_rate",
    "consultation_rate", "inspection_rate", "mandate_rate",
}

#: Le chiavi che non devono esistere da nessuna parte nella struttura.
CHIAVI_VIETATE = {
    "stima_id", "owner_account_id", "account_id", "contact_id", "lead_id",
    "property_id", "email", "phone", "telefono", "nome", "cognome", "via",
    "civico", "indirizzo", "address", "score", "punteggio", "payload",
    "buyer", "budget", "idempotency_key", "agency_id",
}


def _chiavi_ricorsive(valore, prefisso=""):
    """Tutte le chiavi della struttura, a qualunque profondita'."""
    trovate = set()
    if isinstance(valore, dict):
        for chiave, dentro in valore.items():
            trovate.add(chiave)
            trovate |= _chiavi_ricorsive(dentro, f"{prefisso}{chiave}.")
    elif isinstance(valore, (list, tuple)):
        for dentro in valore:
            trovate |= _chiavi_ricorsive(dentro, prefisso)
    return trovate


def test_23_24_25_il_dto_non_porta_pii_ne_punteggi(mondo, modulo):
    """LA PROVA E' STRUTTURALE, MAI SUI VALORI.

    Non si guarda quanto vale un numero: un conteggio legittimo puo' valere
    qualunque intero, e dedurre "grande quindi e' un id" sarebbe un test che
    fallisce il giorno in cui un'agenzia ha mille case. Si guarda la FORMA:
    quali chiavi esistono, e di che tipo e' cio' che portano.

    Il mondo di questo test contiene nome, cognome, email, telefono e
    indirizzo veri in `stime` e in `contacts`, e cinque eventi con la loro
    chiave di idempotenza: se una di quelle cose potesse risalire fino al
    DTO, qui si vedrebbe.
    """
    from owner.schemas import HomeMetricsResponse

    st = mondo["stima"](mondo["a"])
    acc = mondo["account"](mondo["a"], "mario")
    mondo["grant"](acc, st)
    for tipo in (VISTA, VALORE, DOMANDA, AGGIORNATA, CONSULENZA):
        mondo["evento"](mondo["a"], acc, st, tipo, quando=ORA - GIORNO)
    d = metriche(modulo, mondo)

    # 1. Le chiavi sono ESATTAMENTE quelle approvate, ai due livelli.
    assert set(d) == CHIAVI_DTO_APPROVATE
    assert set(d["rates"]) == CHIAVI_RATES_APPROVATE

    # 2. Nessuna chiave vietata, a nessuna profondita'.
    presenti = _chiavi_ricorsive(d)
    assert presenti & CHIAVI_VIETATE == set(), sorted(presenti & CHIAVI_VIETATE)
    # E nemmeno come sottostringa di una chiave (`owner_email`, `buyer_count`).
    for chiave in presenti:
        for vietata in CHIAVI_VIETATE:
            assert vietata not in chiave.lower(), (chiave, vietata)

    # 3. I TIPI: solo numeri, stringhe dichiarate e null. Una struttura
    #    annidata inattesa - una lista di righe, un oggetto - sarebbe il modo
    #    in cui un dato per casa potrebbe uscire senza che una chiave lo dica.
    for chiave, valore in d.items():
        if chiave in ("rates", "not_measurable"):
            assert isinstance(valore, dict)
            continue
        assert isinstance(valore, (int, str)) or valore is None, (chiave, type(valore))
        assert not isinstance(valore, (list, tuple, dict)), chiave
    for chiave, valore in d["rates"].items():
        assert isinstance(valore, float) or valore is None, (chiave, type(valore))
    # `not_measurable` porta ragioni, non dati: due stringhe fisse.
    assert set(d["not_measurable"]) == {"inspection_homes", "mandate_homes"}
    for valore in d["not_measurable"].values():
        assert isinstance(valore, str)

    # 4. Le sole stringhe libere del DTO sono le due date, l'unita' e le due
    #    ragioni: nessun valore puo' essere un dato personale del mondo di
    #    prova. Il confronto e' su PAROLE, non su cifre - un numero non e'
    #    mai una prova di PII.
    for chiave in ("cohort_from", "cohort_to"):
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", d[chiave]), d[chiave]
    assert d["unit"] == "stima"
    parole = " ".join([d["unit"], *d["not_measurable"].values()]).lower()
    for pii in ("mario", "rossi", "example.it", "1234567", "via trieste",
                "alba adriatica", "villa fiore", "owner_portal"):
        assert pii not in parole, pii

    HomeMetricsResponse.model_validate(d)


class _ConnessioneContata:
    """Delega tutto alla connessione vera e conta le `execute`.

    Serve un proxy e non un monkeypatch dell'attributo: su una connessione
    psycopg2 `cursor` e' di sola lettura, quindi l'unico modo per osservare
    le query senza toccare il codice di produzione e' avvolgere l'oggetto.
    """

    def __init__(self, conn, registro):
        self._conn = conn
        self._registro = registro

    def cursor(self, *a, **kw):
        return _CursoreContato(self._conn.cursor(*a, **kw), self._registro)

    def __getattr__(self, nome):
        return getattr(self._conn, nome)


class _CursoreContato:
    def __init__(self, cur, registro):
        self._cur = cur
        self._registro = registro

    def execute(self, sql, *a, **kw):
        self._registro.append(sql)
        return self._cur.execute(sql, *a, **kw)

    def __getattr__(self, nome):
        return getattr(self._cur, nome)

    def __iter__(self):
        return iter(self._cur)


def test_28_nessun_n_piu_uno_il_numero_di_query_non_dipende_dai_dati(
        mondo, modulo, monkeypatch, db):
    """AGGIORNATO DA LMC-15: il conteggio resta UNA query, e il totale e' fisso.

    Diceva "un solo `execute`". LMC-15 ne ha aggiunta una che chiede al
    catalogo se le tabelle del ponte esistono, e - solo se esistono - una che
    legge la data dal registro delle migration. Cio' che questo test
    protegge non e' il numero 1 ma l'assenza di N+1, e per dimostrarla non
    basta piu' contare: si conta CON 5 case e CON 20, e il numero deve essere
    lo stesso. Una query per casa lo farebbe cambiare.

    In piu' resta vero che i conteggi vengono da UNA query sola: le altre non
    toccano i dati del funnel, interrogano il catalogo e il registro.
    """
    import psycopg2

    from core import database as core_database

    def misura(quante, via):
        acc = mondo["account"](mondo["a"], f"mario{via}")
        for i in range(quante):
            st = mondo["stima"](mondo["a"], via=f"{via} {i}")
            mondo["grant"](acc, st)
            mondo["evento"](mondo["a"], acc, st, VISTA, quando=ORA - GIORNO)
        eseguite: list[str] = []
        monkeypatch.setattr(
            core_database, "get_connection",
            lambda: _ConnessioneContata(psycopg2.connect(db["dsn"]), eseguite))
        return metriche(modulo, mondo), eseguite

    d5, q5 = misura(5, "Prima")
    d20, q20 = misura(15, "Poi")
    assert d5["cohort_homes"] == 5 and d20["cohort_homes"] == 20
    assert d20["viewed_homes"] == 20
    assert len(q5) == len(q20), (len(q5), len(q20))
    # E una sola di quelle query conta righe del funnel.
    sul_funnel = [s for s in q20 if "coherent_grants" in s]
    assert len(sul_funnel) == 1, len(sul_funnel)
