"""BLOCCO-2A - valutazioni dell'agente su PostgreSQL VERO.

Gira solo con `P29_TEST_DSN` E `BLOCCO_2A_TEST_CLUSTER_VERIFIED=1`: creare un
database temporaneo nello stesso cluster non prova da solo che il cluster sia
di test, quindi la verifica e' una precondizione dichiarata da chi lancia le
prove (runbook BLOCCO-2A). Come le prove A30-1/LMC-15 e BLOCCO-1B NON scrive
nel database del DSN: crea un database usa-e-getta `blocco_2a_probe_*`, ci
applica la 027 REALE (agenzie, operatori), uno schema minimo di `properties`
con le sole colonne che servono e la 116 REALE, usa dati sintetici e alla fine
elimina il database. Un DSN che nomina la produzione e' rifiutato.

Copre:

    M  la 116: applicata, vincoli, indici parziali, down che rifiuta di perdere dati
    T  il trigger: immutabilita' delle approvate, nessuna cancellazione, agenzia e
       versione precedente coerenti, transizioni ammesse
    S  il service con lo SQL reale: creazione senza incarico, modifica con
       row_version, approvazione atomica con sostituzione, prezzo invariato
    C  concorrenza vera: due approvazioni e due creazioni simultanee
       (sincronizzazione su pg_stat_activity, niente sleep arbitrari)
"""
from __future__ import annotations

import os
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

import pytest

DSN = os.getenv("P29_TEST_DSN")
CLUSTER_VERIFIED = os.getenv("BLOCCO_2A_TEST_CLUSTER_VERIFIED") == "1"

pytestmark = [
    pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare BLOCCO-2A"),
    pytest.mark.skipif(not CLUSTER_VERIFIED, reason="cluster di test non dichiarato verificato "
                       "(BLOCCO_2A_TEST_CLUSTER_VERIFIED=1, runbook BLOCCO-2A)"),
]

ROOT = Path(__file__).resolve().parents[1]
MIGRAZIONI = ROOT / "migrations"
SU = (MIGRAZIONI / "116_property_valuations.sql").read_text(encoding="utf-8")
GIU = (MIGRAZIONI / "116_property_valuations_down.sql").read_text(encoding="utf-8")
PROD = ("stima360_db", "stima360")

#: Le sole colonne di `properties` che servono qui (lettura, permessi,
#: Cestino, fotografia). Lo schema completo e' fuori dall'oggetto della prova.
PROPERTIES_MINIMO = """
CREATE TABLE properties (
    id BIGSERIAL PRIMARY KEY,
    agency_id BIGINT NOT NULL REFERENCES agencies(id),
    title VARCHAR(200) NOT NULL DEFAULT 'Immobile di prova',
    city VARCHAR(120),
    surface_sqm NUMERIC(10,2),
    asking_price NUMERIC(14,2),
    assigned_agent_id BIGINT,
    deleted_at TIMESTAMPTZ,
    mandate_type VARCHAR(80)
);
"""

SALE = {"min": "180000", "max": "210000", "recommended": "195000"}
BASE = {"characteristics": "Trilocale di prova.", "surface": {"value": "85", "kind": "commerciale", "source": "planimetria"},
        "rationale": "Motivazione sintetica di prova.", "sources": "Fonti sintetiche di prova.",
        "comparables_note": "Nessun comparabile: dati sintetici.", "sale": SALE}


def _dsn_per(nome: str) -> str:
    if "?" in DSN:
        base, query = DSN.split("?", 1)
        return base.rsplit("/", 1)[0] + "/" + nome + "?" + query
    return DSN.rsplit("/", 1)[0] + "/" + nome


@pytest.fixture(scope="module")
def db():
    psycopg2 = pytest.importorskip("psycopg2")
    from psycopg2.extensions import parse_dsn

    assert parse_dsn(DSN).get("dbname") not in PROD, "P29_TEST_DSN nomina un database di produzione"
    nome = f"blocco_2a_probe_{os.getpid()}_{uuid.uuid4().hex[:6]}"
    servizio = psycopg2.connect(DSN)
    servizio.autocommit = True
    with servizio.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{nome}"')
    dsn = _dsn_per(nome)
    try:
        conn = psycopg2.connect(dsn)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute((MIGRAZIONI / "027_p26_agency_identity.sql").read_text(encoding="utf-8"))
            cur.execute(PROPERTIES_MINIMO)
            cur.execute(SU)
        conn.close()
        yield {"dsn": dsn, "nome": nome}
    finally:
        with servizio.cursor() as cur:
            cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = %s AND pid <> pg_backend_pid()", (nome,))
            cur.execute(f'DROP DATABASE IF EXISTS "{nome}"')
        servizio.close()


def _q(db, sql, params=(), *, commit=False):
    import psycopg2
    from psycopg2.extras import RealDictCursor

    conn = psycopg2.connect(db["dsn"], cursor_factory=RealDictCursor)
    try:
        with conn.cursor() as cur:
            # Senza parametri lo SQL va al server cosi' com'e': i `%` dei RAISE delle
            # migrazioni non sono segnaposto (con `()` psycopg2 li interpreterebbe).
            cur.execute(sql, params or None)
            rows = cur.fetchall() if cur.description else []
        if commit:
            conn.commit()
        return rows
    finally:
        conn.close()


def _waiting_on_lock(db, count=1, timeout=10) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        n = _q(db, "SELECT count(*) AS n FROM pg_stat_activity WHERE datname = %s AND wait_event_type = 'Lock'",
               (db["nome"],))[0]["n"]
        if n >= count:
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def world(db, monkeypatch):
    import psycopg2
    from psycopg2.extras import RealDictCursor

    from operator_auth.context import OperatorContext
    from property_valuations import service

    _q(db, "ALTER TABLE property_valuations DISABLE TRIGGER trg_property_valuations_guard", commit=True)
    _q(db, "DELETE FROM property_valuations", commit=True)
    _q(db, "ALTER TABLE property_valuations ENABLE TRIGGER trg_property_valuations_guard", commit=True)
    _q(db, "DELETE FROM properties", commit=True)
    _q(db, "DELETE FROM operator_users WHERE email LIKE 'b2a-%%'", commit=True)
    _q(db, "DELETE FROM agencies WHERE slug LIKE 'b2a-%%'", commit=True)
    a = _q(db, "INSERT INTO agencies (name, slug) VALUES ('Alfa', 'b2a-alfa') RETURNING id", commit=True)[0]["id"]
    b = _q(db, "INSERT INTO agencies (name, slug) VALUES ('Beta', 'b2a-beta') RETURNING id", commit=True)[0]["id"]
    users = {}
    for key in ("owner", "admin", "agent"):
        users[key] = _q(db, "INSERT INTO operator_users (email, email_normalized, password_hash) "
                            "VALUES (%s, %s, 'pbkdf2_sha256$x') RETURNING id",
                        (f"b2a-{key}@example.test", f"b2a-{key}@example.test"), commit=True)[0]["id"]
    prop = _q(db, "INSERT INTO properties (agency_id, city, surface_sqm, asking_price, assigned_agent_id) "
                  "VALUES (%s, 'Comune di prova', 80, 199000, %s) RETURNING id", (a, users["agent"]), commit=True)[0]["id"]
    prop_b = _q(db, "INSERT INTO properties (agency_id) VALUES (%s) RETURNING id", (b,), commit=True)[0]["id"]

    @contextmanager
    def cursor(*, commit=False):
        conn = psycopg2.connect(db["dsn"], cursor_factory=RealDictCursor)
        cur = conn.cursor()
        try:
            yield conn, cur
            if commit:
                conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    monkeypatch.setattr(service, "core_cursor", cursor)

    def ctx(role, user_id, agency_id=a):
        return OperatorContext(user_id=user_id, agency_id=agency_id, role=role, is_platform_admin=False,
                               session_id=1, auth_channel="operator_session")

    return {"a": a, "b": b, "users": users, "prop": prop, "prop_b": prop_b, "service": service,
            "owner": ctx("agency_owner", users["owner"]), "admin": ctx("agency_admin", users["admin"]),
            "agent": ctx("agent", users["agent"])}


def _content(**over):
    from property_valuations.schemas import ValuationContent

    return ValuationContent(**{**BASE, **over})


# ---------------------------------------------------------------------------
# M - la migrazione
# ---------------------------------------------------------------------------

def test_m_migration_is_valid_for_the_runner():
    sys.path.insert(0, str(ROOT / "scripts"))
    import p26_migrate as runner

    tutte = runner.discover_migrations()
    # SENTINELLA AGGIORNATA DA BLOCCO-2B: la 117 segue la 116.
    # SENTINELLA AGGIORNATA DA BLOCCO-4A: poi la 118 (ricezione WhatsApp).
    assert tutte[-1].version == "118_whatsapp_inbound"
    m116 = next(m for m in tutte if m.version == "116_property_valuations")
    assert runner.validate_migration(m116) == []


def test_m_partial_unique_indexes_exist(db):
    names = {r["indexname"] for r in _q(db, "SELECT indexname FROM pg_indexes WHERE tablename = 'property_valuations'")}
    assert {"uq_property_valuations_one_approved", "uq_property_valuations_one_draft",
            "property_valuations_version_unq"} <= names


def test_m_down_refuses_with_rows_and_round_trips_when_empty(world, db):
    import psycopg2

    world["service"].create_draft(world["owner"], world["prop"], _content())
    with pytest.raises(psycopg2.errors.RaiseException):
        _q(db, GIU, commit=True)
    _q(db, "DELETE FROM property_valuations", commit=True)          # bozza: cancellabile
    _q(db, GIU, commit=True)
    assert not _q(db, "SELECT 1 FROM pg_tables WHERE tablename = 'property_valuations'")
    _q(db, SU, commit=True)


# ---------------------------------------------------------------------------
# T - il trigger
# ---------------------------------------------------------------------------

def _approved(world):
    s = world["service"]
    v = s.create_draft(world["owner"], world["prop"], _content())
    return s.approve(world["owner"], v["id"], v["row_version"])


def test_t_approved_content_cannot_be_changed_directly(world, db):
    import psycopg2

    v = _approved(world)
    with pytest.raises(psycopg2.errors.RaiseException):
        _q(db, "UPDATE property_valuations SET sale_recommended = 1 WHERE id = %s", (v["id"],), commit=True)
    with pytest.raises(psycopg2.errors.RaiseException):
        _q(db, "UPDATE property_valuations SET content = '{}'::jsonb WHERE id = %s", (v["id"],), commit=True)


def test_t_approved_and_superseded_cannot_be_deleted(world, db):
    import psycopg2

    v = _approved(world)
    with pytest.raises(psycopg2.errors.RaiseException):
        _q(db, "DELETE FROM property_valuations WHERE id = %s", (v["id"],), commit=True)


def test_t_agency_mismatch_and_foreign_previous_are_refused(world, db):
    import psycopg2

    with pytest.raises(psycopg2.errors.RaiseException):
        _q(db, "INSERT INTO property_valuations (agency_id, property_id, version, created_by_user_id, "
               "updated_by_user_id) VALUES (%s, %s, 1, %s, %s)",
           (world["b"], world["prop"], world["users"]["owner"], world["users"]["owner"]), commit=True)
    v = world["service"].create_draft(world["owner"], world["prop"], _content())
    other = _q(db, "INSERT INTO properties (agency_id) VALUES (%s) RETURNING id", (world["a"],), commit=True)[0]["id"]
    with pytest.raises(psycopg2.errors.RaiseException):
        _q(db, "INSERT INTO property_valuations (agency_id, property_id, version, previous_valuation_id, "
               "created_by_user_id, updated_by_user_id) VALUES (%s, %s, 1, %s, %s, %s)",
           (world["a"], other, v["id"], world["users"]["owner"], world["users"]["owner"]), commit=True)


def test_t_a_draft_cannot_jump_to_superseded_or_change_identity(world, db):
    import psycopg2

    v = world["service"].create_draft(world["owner"], world["prop"], _content())
    with pytest.raises(psycopg2.errors.RaiseException):
        _q(db, "UPDATE property_valuations SET status = 'superseded', approved_by_user_id = %s, approved_at = NOW(), "
               "superseded_at = NOW(), superseded_by_valuation_id = id WHERE id = %s",
           (world["users"]["owner"], v["id"]), commit=True)
    with pytest.raises(psycopg2.errors.RaiseException):
        _q(db, "UPDATE property_valuations SET version = 9 WHERE id = %s", (v["id"],), commit=True)


def test_t_zero_amounts_and_inverted_ranges_are_refused_by_the_database(world, db):
    import psycopg2

    v = world["service"].create_draft(world["owner"], world["prop"], _content())
    for sql in ("UPDATE property_valuations SET sale_min = 0 WHERE id = %s",
                "UPDATE property_valuations SET rent_min = 900, rent_max = 700 WHERE id = %s"):
        with pytest.raises(psycopg2.errors.CheckViolation):
            _q(db, sql, (v["id"],), commit=True)


# ---------------------------------------------------------------------------
# S - il service con lo SQL reale
# ---------------------------------------------------------------------------

def test_s_full_flow_with_supersession_and_unchanged_price(world, db):
    s = world["service"]
    v1 = s.create_draft(world["agent"], world["prop"], _content(sale={**SALE, "recommended": "200000"}))
    assert v1["version"] == 1 and v1["created_by_user_id"] == world["users"]["agent"]
    v1 = s.update_draft(world["agent"], v1["id"], v1["row_version"], _content())
    v1 = s.approve(world["admin"], v1["id"], v1["row_version"])
    v2 = s.create_version(world["owner"], v1["id"])
    assert _q(db, "SELECT status FROM property_valuations WHERE id = %s", (v1["id"],))[0]["status"] == "approved"
    v2 = s.approve(world["owner"], v2["id"], v2["row_version"])
    rows = {r["id"]: r for r in _q(db, "SELECT * FROM property_valuations")}
    assert rows[v1["id"]]["status"] == "superseded" and rows[v1["id"]]["superseded_by_valuation_id"] == v2["id"]
    assert rows[v2["id"]]["status"] == "approved" and rows[v2["id"]]["sale_recommended"] == Decimal("195000.00")
    assert _q(db, "SELECT asking_price FROM properties WHERE id = %s", (world["prop"],))[0]["asking_price"] == Decimal("199000.00")


def test_s_other_agency_is_not_found(world):
    s = world["service"]
    v = s.create_draft(world["owner"], world["prop"], _content())
    from dataclasses import replace

    owner_b = replace(world["owner"], agency_id=world["b"])
    with pytest.raises(s.NotFound):
        s.get_valuation(owner_b, v["id"])
    with pytest.raises(s.NotFound):
        s.create_draft(world["owner"], world["prop_b"], _content())


# ---------------------------------------------------------------------------
# C - concorrenza vera
# ---------------------------------------------------------------------------

def _run(target):
    out = {}

    def runner():
        try:
            out["value"] = target()
        except Exception as exc:
            out["error"] = exc

    t = threading.Thread(target=runner)
    t.start()
    return t, out


@contextmanager
def _holding_property_lock(db, property_id):
    import psycopg2

    conn = psycopg2.connect(db["dsn"])
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM properties WHERE id = %s FOR UPDATE", (property_id,))
            yield
        conn.commit()
    finally:
        conn.close()


def test_c_two_simultaneous_approvals_one_wins(world, db):
    s = world["service"]
    v = s.create_draft(world["owner"], world["prop"], _content())
    with _holding_property_lock(db, world["prop"]):
        runs = [_run(lambda c=c: s.approve(c, v["id"], v["row_version"])) for c in (world["owner"], world["admin"])]
        assert _waiting_on_lock(db, 2), "le due approvazioni non si sono messe in fila sul lock dell'immobile"
    for t, _ in runs:
        t.join(15)
    outcomes = sorted("ok" if "value" in o else o["error"].code for _, o in runs)
    assert outcomes == ["ALREADY_APPROVED", "ok"], runs
    assert [r["status"] for r in _q(db, "SELECT status FROM property_valuations")] == ["approved"]


def test_c_two_simultaneous_drafts_only_one_exists(world, db):
    s = world["service"]
    with _holding_property_lock(db, world["prop"]):
        runs = [_run(lambda: s.create_draft(world["owner"], world["prop"], _content())) for _ in range(2)]
        assert _waiting_on_lock(db, 2)
    for t, _ in runs:
        t.join(15)
    outcomes = sorted("ok" if "value" in o else o["error"].code for _, o in runs)
    assert outcomes == ["DRAFT_EXISTS", "ok"], runs
    assert _q(db, "SELECT count(*) AS n FROM property_valuations WHERE status = 'draft'")[0]["n"] == 1
