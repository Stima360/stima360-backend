"""LMC-1A su PostgreSQL reale: la 066, il trigger, il provisioning idempotente.

Cio' che un cursore finto non puo' dire: che `UNIQUE (owner_account_id,
stima_id)` nega davvero un doppione, che il trigger rifiuta davvero un grant
fra un contatto di A e una stima di B, che due esecuzioni dello stesso
provisioning lasciano UNA riga per tabella e UNA riga di audit, che la down
toglie cio' che la up ha messo e la up si riapplica.

Opt-in: senza `P29_TEST_DSN` si salta tutto, come i moduli P29. Il database e'
usa-e-getta, creato e cancellato qui; non si passa da
`database.get_connection()`, che e' il choke point dell'applicazione: il modulo
owner viene puntato sul database di prova attraverso `core.database`.

Schema: le tabelle storiche che le migration non creano (`agencies`,
`contacts`, `stime`, `properties`, `activities`), poi la 009 (OWNER 0.1, che
crea `owner_accounts` e `owner_audit_log`) e la 066.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

DSN = os.getenv("P29_TEST_DSN")

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare la 066")

ROOT = Path(__file__).resolve().parents[1]
MIGRAZIONI = ROOT / "migrations"
VERSION = "066_lmc1_owner_stima_access"

SCHEMA_MINIMO = """
CREATE TABLE agencies (
    id BIGSERIAL PRIMARY KEY, slug VARCHAR(80) NOT NULL UNIQUE,
    status VARCHAR(20) NOT NULL DEFAULT 'active');
CREATE TABLE contacts (
    id BIGSERIAL PRIMARY KEY,
    agency_id BIGINT NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,
    display_name VARCHAR(200), email VARCHAR(320), status VARCHAR(20) NOT NULL DEFAULT 'active');
CREATE TABLE stime (
    id SERIAL PRIMARY KEY,
    agency_id BIGINT NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,
    comune VARCHAR(100), data TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE properties (id BIGSERIAL PRIMARY KEY, agency_id BIGINT, title VARCHAR(200));
CREATE TABLE activities (id BIGSERIAL PRIMARY KEY);
CREATE TABLE schema_migrations (version VARCHAR(200) PRIMARY KEY);
"""


def _dsn_per(nome: str) -> str:
    if "?" in DSN:
        base, query = DSN.split("?", 1)
        return base.rsplit("/", 1)[0] + "/" + nome + "?" + query
    return DSN.rsplit("/", 1)[0] + "/" + nome


def _up() -> str:
    return (MIGRAZIONI / f"{VERSION}.sql").read_text(encoding="utf-8")


def _down() -> str:
    return (MIGRAZIONI / f"{VERSION}_down.sql").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def db():
    psycopg2 = pytest.importorskip("psycopg2")

    nome = f"lmc1a_probe_{os.getpid()}_{uuid.uuid4().hex[:6]}"
    servizio = psycopg2.connect(DSN)
    servizio.autocommit = True
    with servizio.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{nome}"')

    dsn = _dsn_per(nome)
    conn = psycopg2.connect(dsn)
    conn.autocommit = True  # la 009 porta il proprio BEGIN/COMMIT, la 066 no
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_MINIMO)
            cur.execute((MIGRAZIONI / "009_owner_01.sql").read_text(encoding="utf-8"))
            cur.execute(_up())
            cur.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (VERSION,))
        conn.autocommit = False
        yield {"conn": conn, "dsn": dsn}
    finally:
        conn.close()
        with servizio.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{nome}"')
        servizio.close()


@pytest.fixture
def modulo(db, monkeypatch):
    """Il dominio owner, con il suo choke point puntato sul database di prova."""
    import psycopg2

    from core import database as core_database
    from owner import provisioning
    from owner import repository

    monkeypatch.setattr(core_database, "get_connection", lambda: psycopg2.connect(db["dsn"]))
    return {"provisioning": provisioning, "repository": repository}


@pytest.fixture
def mondo(db):
    """Due agenzie; un contatto e una stima in A, una stima in B."""
    conn = db["conn"]
    with conn.cursor() as cur:
        cur.execute("DELETE FROM owner_stima_access")
        cur.execute("DELETE FROM owner_audit_log")
        cur.execute("DELETE FROM owner_accounts")
        cur.execute("DELETE FROM stime")
        cur.execute("DELETE FROM contacts")
        cur.execute("DELETE FROM agencies")
        cur.execute("INSERT INTO agencies (slug) VALUES ('a-uno') RETURNING id")
        a = cur.fetchone()[0]
        cur.execute("INSERT INTO agencies (slug) VALUES ('b-due') RETURNING id")
        b = cur.fetchone()[0]
        cur.execute("INSERT INTO contacts (agency_id, display_name, email) "
                    "VALUES (%s, 'Mario Rossi', 'mario@example.it') RETURNING id", (a,))
        k = cur.fetchone()[0]
        cur.execute("INSERT INTO stime (agency_id, comune) VALUES (%s, 'Alba Adriatica') RETURNING id", (a,))
        st = cur.fetchone()[0]
        cur.execute("INSERT INTO stime (agency_id, comune) VALUES (%s, 'Tortoreto') RETURNING id", (b,))
        st_b = cur.fetchone()[0]
    conn.commit()
    return {"conn": conn, "a": a, "b": b, "k": k, "st": st, "st_b": st_b}


def ctx(agency_id):
    from operator_auth.context import SystemAgencyContext
    return SystemAgencyContext(agency_id=agency_id, origin="public_stima")


def bridge(contact_id, stima_id, status="linked"):
    return {"status": status, "stima_id": stima_id, "contact_id": contact_id, "lead_id": 1}


def conta(mondo, sql, params=()):
    """Legge e chiude la transazione: una lettura lasciata aperta terrebbe un
    lock di tabella e bloccherebbe il DROP della down in test_13."""
    with mondo["conn"].cursor() as cur:
        cur.execute(sql, params)
        valore = cur.fetchone()[0]
    mondo["conn"].rollback()
    return valore


def righe(mondo, sql, params=()):
    with mondo["conn"].cursor() as cur:
        cur.execute(sql, params)
        valori = cur.fetchall()
    mondo["conn"].rollback()
    return valori


def prova(mondo, sql, params=()):
    try:
        with mondo["conn"].cursor() as cur:
            cur.execute(sql, params)
        mondo["conn"].commit()
        return None
    except Exception as exc:
        mondo["conn"].rollback()
        return exc


# ---------------------------------------------------------------------------
# 1-3: stessa agenzia -> account + grant; account esistente riusato; retry
# ---------------------------------------------------------------------------

def test_1_contatto_e_stima_della_stessa_agenzia_creano_account_e_accesso(mondo, modulo):
    esito = modulo["provisioning"].provision_for_public_stima(
        ctx(mondo["a"]), stima_id=mondo["st"], bridge_result=bridge(mondo["k"], mondo["st"]))

    assert esito["status"] == "provisioned"
    assert esito["account_created"] is True and esito["access_created"] is True

    account = righe(mondo, "SELECT id, contact_id, status FROM owner_accounts")
    assert len(account) == 1
    assert account[0][1] == mondo["k"] and account[0][2] == "invited"
    assert esito["owner_account_id"] == account[0][0]

    grant = righe(mondo, "SELECT owner_account_id, stima_id, access_role, access_status, is_primary, "
                         "granted_by, revoked_at, valid_until FROM owner_stima_access")
    assert grant == [(account[0][0], mondo["st"], "owner", "active", True,
                      modulo["provisioning"].PROVISIONING_ACTOR, None, None)]


def test_2_un_account_gia_esistente_viene_riusato(mondo, modulo):
    with mondo["conn"].cursor() as cur:
        cur.execute("INSERT INTO owner_accounts (contact_id, status) VALUES (%s, 'active') RETURNING id",
                    (mondo["k"],))
        preesistente = cur.fetchone()[0]
    mondo["conn"].commit()

    esito = modulo["provisioning"].provision_for_public_stima(
        ctx(mondo["a"]), stima_id=mondo["st"], bridge_result=bridge(mondo["k"], mondo["st"]))

    assert esito["status"] == "provisioned"
    assert esito["owner_account_id"] == preesistente
    assert esito["account_created"] is False and esito["access_created"] is True
    assert conta(mondo, "SELECT count(*) FROM owner_accounts") == 1
    assert conta(mondo, "SELECT status FROM owner_accounts WHERE id = %s", (preesistente,)) == "active"


def test_3_il_retry_non_duplica_ne_righe_ne_audit(mondo, modulo):
    primo = modulo["provisioning"].provision_for_public_stima(
        ctx(mondo["a"]), stima_id=mondo["st"], bridge_result=bridge(mondo["k"], mondo["st"]))
    secondo = modulo["provisioning"].provision_for_public_stima(
        ctx(mondo["a"]), stima_id=mondo["st"],
        bridge_result=bridge(mondo["k"], mondo["st"], status="already_linked"))

    assert secondo["status"] == "already_provisioned"
    assert secondo["owner_account_id"] == primo["owner_account_id"]
    assert secondo["access_id"] == primo["access_id"]
    assert secondo["account_created"] is False and secondo["access_created"] is False
    assert conta(mondo, "SELECT count(*) FROM owner_accounts") == 1
    assert conta(mondo, "SELECT count(*) FROM owner_stima_access") == 1

    audit = righe(mondo, "SELECT action, owner_account_id, entity_type, entity_id, metadata "
                         "FROM owner_audit_log ORDER BY id")
    assert [r[0] for r in audit] == ["account_created", "stima_access_granted"]
    assert audit[1][1] == primo["owner_account_id"]
    assert audit[1][2] == "owner_stima_access" and audit[1][3] == str(primo["access_id"])
    assert audit[1][4]["stima_id"] == mondo["st"]
    assert audit[1][4]["granted_by"] == modulo["provisioning"].PROVISIONING_ACTOR


def test_4_already_linked_provisiona_come_linked(mondo, modulo):
    esito = modulo["provisioning"].provision_for_public_stima(
        ctx(mondo["a"]), stima_id=mondo["st"],
        bridge_result=bridge(mondo["k"], mondo["st"], status="already_linked"))
    assert esito["status"] == "provisioned"
    assert conta(mondo, "SELECT count(*) FROM owner_stima_access") == 1


def test_4b_una_seconda_stima_dello_stesso_contatto_aggiunge_solo_il_grant(mondo, modulo):
    with mondo["conn"].cursor() as cur:
        cur.execute("INSERT INTO stime (agency_id, comune) VALUES (%s, 'Martinsicuro') RETURNING id",
                    (mondo["a"],))
        st2 = cur.fetchone()[0]
    mondo["conn"].commit()

    primo = modulo["provisioning"].provision_for_public_stima(
        ctx(mondo["a"]), stima_id=mondo["st"], bridge_result=bridge(mondo["k"], mondo["st"]))
    secondo = modulo["provisioning"].provision_for_public_stima(
        ctx(mondo["a"]), stima_id=st2, bridge_result=bridge(mondo["k"], st2))

    assert secondo["owner_account_id"] == primo["owner_account_id"]
    assert secondo["account_created"] is False and secondo["access_created"] is True
    assert conta(mondo, "SELECT count(*) FROM owner_accounts") == 1
    assert conta(mondo, "SELECT count(*) FROM owner_stima_access") == 2
    assert conta(mondo, "SELECT count(*) FROM owner_audit_log WHERE action = 'account_created'") == 1
    assert conta(mondo, "SELECT count(*) FROM owner_audit_log WHERE action = 'stima_access_granted'") == 2


# ---------------------------------------------------------------------------
# 5-7: bridge non valido -> nessuna scrittura
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bridge_result", [
    None,
    {"status": "conflict", "stima_id": 0, "reason": "identity_conflict"},
    {"status": "skipped", "stima_id": 0, "reason": "insufficient_contact_identity"},
])
def test_5_6_7_bridge_non_valido_non_scrive_niente(mondo, modulo, bridge_result):
    if bridge_result is not None:
        bridge_result["stima_id"] = mondo["st"]
    esito = modulo["provisioning"].provision_for_public_stima(
        ctx(mondo["a"]), stima_id=mondo["st"], bridge_result=bridge_result)
    assert esito["status"] == "skipped"
    assert conta(mondo, "SELECT count(*) FROM owner_accounts") == 0
    assert conta(mondo, "SELECT count(*) FROM owner_stima_access") == 0
    assert conta(mondo, "SELECT count(*) FROM owner_audit_log") == 0


# ---------------------------------------------------------------------------
# 8-9: tenant incoerente -> rifiutato, transazione annullata
# ---------------------------------------------------------------------------

def test_8_contatto_di_a_e_stima_di_b_sono_rifiutati(mondo, modulo):
    from core.exceptions import NotFoundError

    with pytest.raises(NotFoundError):
        modulo["provisioning"].provision_for_public_stima(
            ctx(mondo["a"]), stima_id=mondo["st_b"], bridge_result=bridge(mondo["k"], mondo["st_b"]))
    assert conta(mondo, "SELECT count(*) FROM owner_accounts") == 0
    assert conta(mondo, "SELECT count(*) FROM owner_stima_access") == 0
    assert conta(mondo, "SELECT count(*) FROM owner_audit_log") == 0


def test_9_un_contesto_di_altra_agenzia_e_rifiutato(mondo, modulo):
    from core.exceptions import NotFoundError

    with pytest.raises(NotFoundError):
        modulo["provisioning"].provision_for_public_stima(
            ctx(mondo["b"]), stima_id=mondo["st"], bridge_result=bridge(mondo["k"], mondo["st"]))
    assert conta(mondo, "SELECT count(*) FROM owner_accounts") == 0
    assert conta(mondo, "SELECT count(*) FROM owner_stima_access") == 0


def test_9b_contatto_o_stima_inesistenti_sono_rifiutati(mondo, modulo):
    from core.exceptions import NotFoundError

    with pytest.raises(NotFoundError):
        modulo["provisioning"].provision_for_public_stima(
            ctx(mondo["a"]), stima_id=mondo["st"], bridge_result=bridge(mondo["k"] + 1000, mondo["st"]))
    with pytest.raises(NotFoundError):
        modulo["provisioning"].provision_for_public_stima(
            ctx(mondo["a"]), stima_id=mondo["st"] + 1000, bridge_result=bridge(mondo["k"], mondo["st"] + 1000))
    assert conta(mondo, "SELECT count(*) FROM owner_accounts") == 0


# ---------------------------------------------------------------------------
# 10: account disabled -> nessun grant
# ---------------------------------------------------------------------------

def test_10_un_account_disabilitato_non_riceve_il_grant(mondo, modulo):
    with mondo["conn"].cursor() as cur:
        cur.execute("INSERT INTO owner_accounts (contact_id, status, disabled_at) "
                    "VALUES (%s, 'disabled', NOW()) RETURNING id", (mondo["k"],))
        disabilitato = cur.fetchone()[0]
    mondo["conn"].commit()

    esito = modulo["provisioning"].provision_for_public_stima(
        ctx(mondo["a"]), stima_id=mondo["st"], bridge_result=bridge(mondo["k"], mondo["st"]))

    assert esito["status"] == "account_disabled"
    assert esito["owner_account_id"] == disabilitato
    assert esito["access_created"] is False
    assert conta(mondo, "SELECT count(*) FROM owner_stima_access") == 0
    assert conta(mondo, "SELECT count(*) FROM owner_audit_log") == 0
    assert conta(mondo, "SELECT status FROM owner_accounts WHERE id = %s", (disabilitato,)) == "disabled"


def test_10b_un_grant_revocato_non_viene_riaperto_dal_retry(mondo, modulo):
    primo = modulo["provisioning"].provision_for_public_stima(
        ctx(mondo["a"]), stima_id=mondo["st"], bridge_result=bridge(mondo["k"], mondo["st"]))
    with mondo["conn"].cursor() as cur:
        cur.execute("UPDATE owner_stima_access SET access_status='revoked', revoked_at=NOW() WHERE id=%s",
                    (primo["access_id"],))
    mondo["conn"].commit()

    secondo = modulo["provisioning"].provision_for_public_stima(
        ctx(mondo["a"]), stima_id=mondo["st"], bridge_result=bridge(mondo["k"], mondo["st"]))
    assert secondo["status"] == "already_provisioned"
    assert secondo["access_created"] is False
    assert conta(mondo, "SELECT access_status FROM owner_stima_access WHERE id = %s",
                 (primo["access_id"],)) == "revoked"


# ---------------------------------------------------------------------------
# 11: safe_* con il database che rifiuta
# ---------------------------------------------------------------------------

def test_11_safe_inghiotte_un_rifiuto_del_database(mondo, modulo):
    esito = modulo["provisioning"].safe_provision_for_public_stima(
        ctx(mondo["a"]), stima_id=mondo["st_b"], bridge_result=bridge(mondo["k"], mondo["st_b"]))
    assert esito is None
    assert conta(mondo, "SELECT count(*) FROM owner_stima_access") == 0


# ---------------------------------------------------------------------------
# 12: il trigger e la UNIQUE, direttamente in SQL
# ---------------------------------------------------------------------------

def test_12_il_trigger_rifiuta_un_grant_cross_tenant(mondo):
    with mondo["conn"].cursor() as cur:
        cur.execute("INSERT INTO owner_accounts (contact_id) VALUES (%s) RETURNING id", (mondo["k"],))
        account = cur.fetchone()[0]
    mondo["conn"].commit()

    esito = prova(mondo, "INSERT INTO owner_stima_access (owner_account_id, stima_id) VALUES (%s, %s)",
                  (account, mondo["st_b"]))
    assert esito is not None
    assert "LMC-1A owner/stima tenancy" in str(esito)
    assert conta(mondo, "SELECT count(*) FROM owner_stima_access") == 0

    ok = prova(mondo, "INSERT INTO owner_stima_access (owner_account_id, stima_id) VALUES (%s, %s)",
               (account, mondo["st"]))
    assert ok is None

    spostamento = prova(mondo, "UPDATE owner_stima_access SET stima_id = %s WHERE owner_account_id = %s",
                        (mondo["st_b"], account))
    assert spostamento is not None and "LMC-1A owner/stima tenancy" in str(spostamento)


def test_12b_la_unique_nega_il_doppione(mondo):
    with mondo["conn"].cursor() as cur:
        cur.execute("INSERT INTO owner_accounts (contact_id) VALUES (%s) RETURNING id", (mondo["k"],))
        account = cur.fetchone()[0]
        cur.execute("INSERT INTO owner_stima_access (owner_account_id, stima_id) VALUES (%s, %s)",
                    (account, mondo["st"]))
    mondo["conn"].commit()
    doppione = prova(mondo, "INSERT INTO owner_stima_access (owner_account_id, stima_id) VALUES (%s, %s)",
                     (account, mondo["st"]))
    assert doppione is not None
    assert "owner_stima_access_owner_account_id_stima_id_key" in str(doppione) or "unique" in str(doppione).lower()


def test_12c_i_check_di_ruolo_stato_e_date(mondo):
    with mondo["conn"].cursor() as cur:
        cur.execute("INSERT INTO owner_accounts (contact_id) VALUES (%s) RETURNING id", (mondo["k"],))
        account = cur.fetchone()[0]
    mondo["conn"].commit()
    base = "INSERT INTO owner_stima_access (owner_account_id, stima_id, {col}) VALUES (%s, %s, {val})"
    assert prova(mondo, base.format(col="access_role", val="'tenant'"), (account, mondo["st"])) is not None
    assert prova(mondo, base.format(col="access_status", val="'pending'"), (account, mondo["st"])) is not None
    assert prova(mondo, base.format(col="valid_until", val="NOW() - INTERVAL '1 day'"),
                 (account, mondo["st"])) is not None
    assert prova(mondo, base.format(col="access_status", val="'revoked'"), (account, mondo["st"])) is not None, \
        "revoked senza revoked_at"
    assert conta(mondo, "SELECT count(*) FROM owner_stima_access") == 0


def test_12d_cancellare_la_stima_o_l_account_porta_via_il_grant(mondo, modulo):
    esito = modulo["provisioning"].provision_for_public_stima(
        ctx(mondo["a"]), stima_id=mondo["st"], bridge_result=bridge(mondo["k"], mondo["st"]))
    assert prova(mondo, "DELETE FROM stime WHERE id = %s", (mondo["st"],)) is None
    assert conta(mondo, "SELECT count(*) FROM owner_stima_access") == 0
    assert conta(mondo, "SELECT count(*) FROM owner_accounts WHERE id = %s", (esito["owner_account_id"],)) == 1


# ---------------------------------------------------------------------------
# 13: up / down / up
# ---------------------------------------------------------------------------

def test_13_la_down_toglie_tutto_e_la_up_si_riapplica(db):
    psycopg2 = pytest.importorskip("psycopg2")
    db["conn"].rollback()  # nessuna transazione aperta altrove sul database di prova
    conn = psycopg2.connect(db["dsn"])
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("SET lock_timeout = '5s'")
            cur.execute(_down())
            cur.execute("SELECT to_regclass('public.owner_stima_access')")
            assert cur.fetchone()[0] is None
            cur.execute("SELECT count(*) FROM pg_proc WHERE proname = 'owner_stima_access_agency_integrity'")
            assert cur.fetchone()[0] == 0
            cur.execute("SELECT count(*) FROM schema_migrations WHERE version = %s", (VERSION,))
            assert cur.fetchone()[0] == 0
            # Le tabelle OWNER storiche non sono state toccate.
            cur.execute("SELECT to_regclass('public.owner_accounts')")
            assert cur.fetchone()[0] is not None

            cur.execute(_up())
            cur.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (VERSION,))
            cur.execute("SELECT to_regclass('public.owner_stima_access')")
            assert cur.fetchone()[0] is not None
            cur.execute("SELECT tgname FROM pg_trigger WHERE tgrelid = 'public.owner_stima_access'::regclass "
                        "AND NOT tgisinternal")
            assert {r[0] for r in cur.fetchall()} == {"trg_owner_stima_access_agency_integrity"}
            cur.execute("SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'owner_stima_access' ORDER BY ordinal_position")
            colonne = [r[0] for r in cur.fetchall()]
            assert "agency_id" not in colonne
            assert colonne == ["id", "owner_account_id", "stima_id", "access_role", "access_status",
                               "is_primary", "valid_from", "valid_until", "created_at", "updated_at",
                               "revoked_at", "granted_by"]
            # La up e' idempotente sul proprio stato: una seconda applicazione non fallisce.
            cur.execute(_up())
    finally:
        conn.close()
