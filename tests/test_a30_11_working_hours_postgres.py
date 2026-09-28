"""A30-11B su PostgreSQL reale: le tre tabelle (076), provate dove vivono.

Cosa un doppio non puo' provare, e qui si prova:

  * i tre vincoli EXCLUDE (btree_gist): sovrapposizioni rifiutate,
    intervalli adiacenti ammessi, per ognuna delle tre tabelle;
  * la FK composita `(agency_id, user_id) REFERENCES agency_memberships`:
    un riferimento cross-tenant e' irrappresentabile;
    * il trigger di guardia: una membership non ATTIVA rifiuta la scrittura,
    anche se la FK (che dice solo "esiste") la lascerebbe passare;
  * la migration: up, down (che rifiuta se ci sono righe), di nuovo up.

Questo modulo NON applica 072-075: le tre tabelle di 076 dipendono solo da
`agencies`/`agency_memberships`/`operator_users` (stesso schema minimo che
072 stesso presuppone), quindi lo schema qui e' indipendente dall'Agenda.
L'integrazione end-to-end con `appointments.service.availability_check`
(D2, vincolo SOFT) e' provata a parte, sul runbook TEST di Render (stessa
disciplina di A30-10E/F: nessun accesso Render da qui).

Opt-in: senza `P29_TEST_DSN` si salta tutto. Database usa-e-getta.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

DSN = os.getenv("P29_TEST_DSN")

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare A30-11")

ROOT = Path(__file__).resolve().parents[1]
MIGRAZIONI = ROOT / "migrations"
VERSIONE = "076_a30_11_working_hours"

SCHEMA_MINIMO = """
CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE TABLE agencies (
    id BIGSERIAL PRIMARY KEY, slug VARCHAR(80) NOT NULL UNIQUE,
    status VARCHAR(20) NOT NULL DEFAULT 'active');
CREATE TABLE operator_users (
    id BIGSERIAL PRIMARY KEY, email VARCHAR(320) NOT NULL UNIQUE,
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    is_platform_admin BOOLEAN NOT NULL DEFAULT FALSE);
CREATE TABLE agency_memberships (
    id BIGSERIAL PRIMARY KEY,
    agency_id BIGINT NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,
    operator_user_id BIGINT NOT NULL REFERENCES operator_users(id) ON DELETE RESTRICT,
    role VARCHAR(20) NOT NULL DEFAULT 'agent',
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    UNIQUE (agency_id, operator_user_id));
CREATE TABLE schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    rolled_back_at TIMESTAMPTZ);
"""

SU = (MIGRAZIONI / f"{VERSIONE}.sql").read_text(encoding="utf-8")
GIU = (MIGRAZIONI / f"{VERSIONE}_down.sql").read_text(encoding="utf-8")


def _dsn_per(nome: str) -> str:
    if "?" in DSN:
        base, query = DSN.split("?", 1)
        return base.rsplit("/", 1)[0] + "/" + nome + "?" + query
    return DSN.rsplit("/", 1)[0] + "/" + nome


@pytest.fixture(scope="module")
def db():
    psycopg2 = pytest.importorskip("psycopg2")
    nome = f"a30_11_probe_{os.getpid()}_{uuid.uuid4().hex[:6]}"
    servizio = psycopg2.connect(DSN)
    servizio.autocommit = True
    with servizio.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{nome}"')

    dsn = _dsn_per(nome)
    from psycopg2.extras import DictCursor
    conn = psycopg2.connect(dsn, cursor_factory=DictCursor)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_MINIMO)
            cur.execute(SU)
        conn.autocommit = False
        yield {"conn": conn, "dsn": dsn}
    finally:
        conn.close()
        with servizio.cursor() as cur:
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()", (nome,))
            cur.execute(f'DROP DATABASE IF EXISTS "{nome}"')
        servizio.close()


@pytest.fixture
def mondo(db):
    conn = db["conn"]
    conn.rollback()
    with conn.cursor() as cur:
        for tabella in ("agent_working_hours", "agent_availability_exceptions",
                        "agency_closures", "agency_memberships", "operator_users",
                        "agencies"):
            cur.execute(f"DELETE FROM {tabella}")
        cur.execute("INSERT INTO agencies (slug) VALUES ('a-uno') RETURNING id")
        a = cur.fetchone()[0]
        cur.execute("INSERT INTO agencies (slug) VALUES ('b-due') RETURNING id")
        b = cur.fetchone()[0]

        def operatore(email, agenzia=None, ruolo="agent", stato="active"):
            cur.execute("INSERT INTO operator_users (email) VALUES (%s) RETURNING id", (email,))
            i = cur.fetchone()[0]
            if agenzia is not None:
                cur.execute(
                    "INSERT INTO agency_memberships (agency_id, operator_user_id, role, status) "
                    "VALUES (%s,%s,%s,%s)", (agenzia, i, ruolo, stato))
            return i

        luca = operatore("luca@example.it", a, "agent")
        marta = operatore("marta@example.it", a, "agent")
        revocato = operatore("revocato@example.it", a, "agent", stato="revoked")
        altra_agenzia = operatore("altrove@example.it", b, "agent")
    conn.commit()
    yield {"conn": conn, "agency_a": a, "agency_b": b, "luca": luca, "marta": marta,
           "revocato": revocato, "altra_agenzia": altra_agenzia}
    conn.rollback()


def _tx(conn):
    return conn.cursor()


# ---------------------------------------------------------------------------
# A - LA MIGRATION
# ---------------------------------------------------------------------------

def test_01_migration_up_down_up(db):
    conn = db["conn"]
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(GIU)
        cur.execute(
            "SELECT to_regclass('public.agent_working_hours'), "
            "       to_regclass('public.agent_availability_exceptions'), "
            "       to_regclass('public.agency_closures')")
        # DictCursor: fetchone() torna una DictRow (sottoclasse di list),
        # mai uguale a una tupla letterale anche a valori identici - il
        # confronto va fatto per valore, non per tipo.
        assert list(cur.fetchone()) == [None, None, None]
        cur.execute(SU)
        cur.execute(
            "SELECT to_regclass('public.agent_working_hours'), "
            "       to_regclass('public.agent_availability_exceptions'), "
            "       to_regclass('public.agency_closures')")
        riga = cur.fetchone()
        assert all(v is not None for v in riga)
    conn.autocommit = False


def test_02_down_rifiuta_se_ci_sono_righe(mondo):
    conn = mondo["conn"]
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agent_working_hours (agency_id, user_id, day_of_week, "
            "start_minute, end_minute) VALUES (%s,%s,1,540,780)",
            (mondo["agency_a"], mondo["luca"]))
    conn.commit()
    with pytest.raises(Exception):
        with conn.cursor() as cur:
            cur.execute(GIU)
    conn.rollback()


# ---------------------------------------------------------------------------
# B - EXCLUDE: agent_working_hours
# ---------------------------------------------------------------------------

def test_03_weekly_overlap_rifiutato(mondo):
    conn = mondo["conn"]
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agent_working_hours (agency_id, user_id, day_of_week, "
            "start_minute, end_minute) VALUES (%s,%s,1,540,780)",
            (mondo["agency_a"], mondo["luca"]))
    conn.commit()
    with pytest.raises(Exception):
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agent_working_hours (agency_id, user_id, day_of_week, "
                "start_minute, end_minute) VALUES (%s,%s,1,700,900)",
                (mondo["agency_a"], mondo["luca"]))
    conn.rollback()


def test_04_weekly_adiacenti_accettati(mondo):
    conn = mondo["conn"]
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agent_working_hours (agency_id, user_id, day_of_week, "
            "start_minute, end_minute) VALUES (%s,%s,1,540,780)",
            (mondo["agency_a"], mondo["luca"]))
        cur.execute(
            "INSERT INTO agent_working_hours (agency_id, user_id, day_of_week, "
            "start_minute, end_minute) VALUES (%s,%s,1,780,1080)",
            (mondo["agency_a"], mondo["luca"]))
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM agent_working_hours WHERE user_id = %s",
                    (mondo["luca"],))
        assert cur.fetchone()[0] == 2


def test_05_weekly_giorni_diversi_non_confrontati(mondo):
    conn = mondo["conn"]
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agent_working_hours (agency_id, user_id, day_of_week, "
            "start_minute, end_minute) VALUES (%s,%s,1,540,780)",
            (mondo["agency_a"], mondo["luca"]))
        cur.execute(
            "INSERT INTO agent_working_hours (agency_id, user_id, day_of_week, "
            "start_minute, end_minute) VALUES (%s,%s,2,540,780)",
            (mondo["agency_a"], mondo["luca"]))
    conn.commit()


def test_06_weekly_membership_non_attiva_rifiutata(mondo):
    conn = mondo["conn"]
    with pytest.raises(Exception):
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agent_working_hours (agency_id, user_id, day_of_week, "
                "start_minute, end_minute) VALUES (%s,%s,1,540,780)",
                (mondo["agency_a"], mondo["revocato"]))
    conn.rollback()


def test_07_weekly_cross_tenant_rifiutato_dalla_fk(mondo):
    """L'agente e' di un'altra agenzia: la FK composita non trova la
    membership (agency_id, user_id) e rifiuta, prima ancora del trigger."""
    conn = mondo["conn"]
    with pytest.raises(Exception):
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agent_working_hours (agency_id, user_id, day_of_week, "
                "start_minute, end_minute) VALUES (%s,%s,1,540,780)",
                (mondo["agency_a"], mondo["altra_agenzia"]))
    conn.rollback()


# ---------------------------------------------------------------------------
# C - EXCLUDE: agent_availability_exceptions
# ---------------------------------------------------------------------------

def test_08_exception_overlap_rifiutato(mondo):
    conn = mondo["conn"]
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agent_availability_exceptions "
            "(agency_id, user_id, exception_date, start_minute, end_minute, is_available) "
            "VALUES (%s,%s,'2026-12-25',0,1440,false)", (mondo["agency_a"], mondo["luca"]))
    conn.commit()
    with pytest.raises(Exception):
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agent_availability_exceptions "
                "(agency_id, user_id, exception_date, start_minute, end_minute, is_available) "
                "VALUES (%s,%s,'2026-12-25',600,720,true)", (mondo["agency_a"], mondo["luca"]))
    conn.rollback()


def test_09_exception_giorni_diversi_accettati(mondo):
    conn = mondo["conn"]
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agent_availability_exceptions "
            "(agency_id, user_id, exception_date, start_minute, end_minute, is_available) "
            "VALUES (%s,%s,'2026-12-24',0,1440,false)", (mondo["agency_a"], mondo["luca"]))
        cur.execute(
            "INSERT INTO agent_availability_exceptions "
            "(agency_id, user_id, exception_date, start_minute, end_minute, is_available) "
            "VALUES (%s,%s,'2026-12-25',0,1440,false)", (mondo["agency_a"], mondo["luca"]))
    conn.commit()


# ---------------------------------------------------------------------------
# D - EXCLUDE: agency_closures
# ---------------------------------------------------------------------------

def test_10_closure_overlap_rifiutato(mondo):
    conn = mondo["conn"]
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agency_closures (agency_id, closure_date, start_minute, end_minute) "
            "VALUES (%s,'2026-12-25',0,1440)", (mondo["agency_a"],))
    conn.commit()
    with pytest.raises(Exception):
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agency_closures (agency_id, closure_date, start_minute, end_minute) "
                "VALUES (%s,'2026-12-25',600,720)", (mondo["agency_a"],))
    conn.rollback()


def test_11_closure_altra_agenzia_non_confrontata(mondo):
    conn = mondo["conn"]
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agency_closures (agency_id, closure_date, start_minute, end_minute) "
            "VALUES (%s,'2026-12-25',0,1440)", (mondo["agency_a"],))
        cur.execute(
            "INSERT INTO agency_closures (agency_id, closure_date, start_minute, end_minute) "
            "VALUES (%s,'2026-12-25',0,1440)", (mondo["agency_b"],))
    conn.commit()


# ---------------------------------------------------------------------------
# E - CHECK sui minuti
# ---------------------------------------------------------------------------

def test_12_minuti_fuori_range_rifiutati(mondo):
    conn = mondo["conn"]
    with pytest.raises(Exception):
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agent_working_hours (agency_id, user_id, day_of_week, "
                "start_minute, end_minute) VALUES (%s,%s,1,780,540)",  # end < start
                (mondo["agency_a"], mondo["luca"]))
    conn.rollback()
    with pytest.raises(Exception):
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agent_working_hours (agency_id, user_id, day_of_week, "
                "start_minute, end_minute) VALUES (%s,%s,8,540,780)",  # day_of_week fuori 1..7
                (mondo["agency_a"], mondo["luca"]))
    conn.rollback()
