"""BLOCCO-2B - PDF delle valutazioni approvate su PostgreSQL VERO.

Gira solo con `P29_TEST_DSN` E `BLOCCO_2B_TEST_CLUSTER_VERIFIED=1`: creare un
database temporaneo nello stesso cluster non prova da solo che il cluster sia
di test, quindi la verifica e' una precondizione dichiarata da chi lancia le
prove (runbook BLOCCO-2B). Come le prove BLOCCO-1B e BLOCCO-2A NON scrive nel
database del DSN: crea un database usa-e-getta `blocco_2b_probe_*`, ci applica
la 027 REALE (agenzie, operatori), uno schema minimo di `properties`, la 116 e
la 117 REALI, usa dati sintetici e alla fine elimina il database. Un DSN che
nomina la produzione e' rifiutato. L'intestazione agenzia e' sintetica (la
115 e il profilo sono provati da BLOCCO-1B).

Copre:

    M  la 117: applicata, indice, down che rifiuta di perdere documenti
    T  il trigger: nessuna UPDATE/DELETE, INSERT solo per l'approvata della
       stessa agenzia, dello stesso immobile e della stessa versione
    S  il service con lo SQL reale: generazione, download identico, seconda
       generazione che restituisce lo stesso artefatto
    C  concorrenza vera: due generazioni simultanee (un solo artefatto) e una
       generazione contro l'approvazione di una nuova versione (esito coerente),
       sincronizzate su pg_stat_activity, niente sleep arbitrari
"""
from __future__ import annotations

import hashlib
import os
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest

DSN = os.getenv("P29_TEST_DSN")
CLUSTER_VERIFIED = os.getenv("BLOCCO_2B_TEST_CLUSTER_VERIFIED") == "1"

pytestmark = [
    pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare BLOCCO-2B"),
    pytest.mark.skipif(not CLUSTER_VERIFIED, reason="cluster di test non dichiarato verificato "
                       "(BLOCCO_2B_TEST_CLUSTER_VERIFIED=1, runbook BLOCCO-2B)"),
]

ROOT = Path(__file__).resolve().parents[1]
MIGRAZIONI = ROOT / "migrations"
SU_116 = (MIGRAZIONI / "116_property_valuations.sql").read_text(encoding="utf-8")
SU = (MIGRAZIONI / "117_property_valuation_pdfs.sql").read_text(encoding="utf-8")
GIU = (MIGRAZIONI / "117_property_valuation_pdfs_down.sql").read_text(encoding="utf-8")
PROD = ("stima360_db", "stima360")

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
    nome = f"blocco_2b_probe_{os.getpid()}_{uuid.uuid4().hex[:6]}"
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
            cur.execute(SU_116)
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


def _clean(db):
    """Solo nel database usa-e-getta: i trigger si spengono per azzerarlo."""
    for table, trigger in (("property_valuation_pdfs", "trg_property_valuation_pdfs_guard"),
                           ("property_valuations", "trg_property_valuations_guard")):
        _q(db, f"ALTER TABLE {table} DISABLE TRIGGER {trigger}", commit=True)
        _q(db, f"DELETE FROM {table}", commit=True)
        _q(db, f"ALTER TABLE {table} ENABLE TRIGGER {trigger}", commit=True)


@pytest.fixture
def world(db, monkeypatch):
    import psycopg2
    from psycopg2.extras import RealDictCursor

    from agency_profile import service as agency_profile_service
    from operator_auth.context import OperatorContext
    from property_valuations import pdf_service, service

    _clean(db)
    _q(db, "DELETE FROM properties", commit=True)
    _q(db, "DELETE FROM operator_users WHERE email LIKE 'b2b-%%'", commit=True)
    _q(db, "DELETE FROM agencies WHERE slug LIKE 'b2b-%%'", commit=True)
    a = _q(db, "INSERT INTO agencies (name, slug) VALUES ('Alfa', 'b2b-alfa') RETURNING id", commit=True)[0]["id"]
    b = _q(db, "INSERT INTO agencies (name, slug) VALUES ('Beta', 'b2b-beta') RETURNING id", commit=True)[0]["id"]
    users = {}
    for key in ("owner", "admin", "agent"):
        users[key] = _q(db, "INSERT INTO operator_users (email, email_normalized, password_hash) "
                            "VALUES (%s, %s, 'pbkdf2_sha256$x') RETURNING id",
                        (f"b2b-{key}@example.test", f"b2b-{key}@example.test"), commit=True)[0]["id"]
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
    monkeypatch.setattr(pdf_service, "core_cursor", cursor)

    def identity(ctx):
        name = "Alfa" if ctx.agency_id == a else "Beta"
        return {"agency_id": ctx.agency_id, "agency_name": name, "header_name": name,
                "display_name_configured": False, "display_name": None, "legal_name": None, "vat_number": None,
                "tax_code": None, "legal_address": None, "phone": None, "email": None, "website": None,
                "logo": None}

    monkeypatch.setattr(agency_profile_service, "report_identity", identity)

    def ctx(role, user_id, agency_id=a):
        return OperatorContext(user_id=user_id, agency_id=agency_id, role=role, is_platform_admin=False,
                               session_id=1, auth_channel="operator_session")

    return {"a": a, "b": b, "users": users, "prop": prop, "prop_b": prop_b, "service": service,
            "pdf": pdf_service, "owner": ctx("agency_owner", users["owner"]),
            "admin": ctx("agency_admin", users["admin"]), "agent": ctx("agent", users["agent"])}


def _content(**over):
    from property_valuations.schemas import ValuationContent

    return ValuationContent(**{**BASE, **over})


def _approved(world):
    s = world["service"]
    v = s.create_draft(world["owner"], world["prop"], _content())
    return s.approve(world["owner"], v["id"], v["row_version"])


def _insert(db, *, valuation_id, agency_id, property_id, version, user_id):
    data = b"%PDF-1.4 sintetico"
    _q(db, "INSERT INTO property_valuation_pdfs (valuation_id, agency_id, property_id, valuation_version, "
           "template_version, header_snapshot, pdf_bytes, sha256, generated_by_user_id) "
           "VALUES (%s, %s, %s, %s, 'prova', '{}'::jsonb, %s, %s, %s)",
       (valuation_id, agency_id, property_id, version, data, hashlib.sha256(data).hexdigest(), user_id), commit=True)


# ---------------------------------------------------------------------------
# M - la migrazione
# ---------------------------------------------------------------------------

def test_m_migration_is_valid_for_the_runner():
    sys.path.insert(0, str(ROOT / "scripts"))
    import p26_migrate as runner

    tutte = runner.discover_migrations()
    # SENTINELLA AGGIORNATA DA BLOCCO-2B
    assert tutte[-1].version == "117_property_valuation_pdfs"
    assert runner.validate_migration(tutte[-1]) == []


def test_m_table_index_and_trigger_exist(db):
    assert _q(db, "SELECT 1 FROM pg_indexes WHERE indexname = 'idx_property_valuation_pdfs_agency_property'")
    assert _q(db, "SELECT 1 FROM pg_trigger WHERE tgname = 'trg_property_valuation_pdfs_guard'")


def test_m_down_refuses_with_documents_and_round_trips_when_empty(world, db):
    import psycopg2

    v = _approved(world)
    world["pdf"].generate_pdf(world["owner"], v["id"])
    with pytest.raises(psycopg2.errors.RaiseException):
        _q(db, GIU, commit=True)
    assert _q(db, "SELECT count(*) AS n FROM property_valuation_pdfs")[0]["n"] == 1
    _clean(db)
    _q(db, GIU, commit=True)
    assert not _q(db, "SELECT 1 FROM pg_tables WHERE tablename = 'property_valuation_pdfs'")
    _q(db, SU, commit=True)


# ---------------------------------------------------------------------------
# T - il trigger
# ---------------------------------------------------------------------------

def test_t_a_saved_document_cannot_be_updated_or_deleted(world, db):
    import psycopg2

    v = _approved(world)
    world["pdf"].generate_pdf(world["owner"], v["id"])
    with pytest.raises(psycopg2.errors.RaiseException):
        _q(db, "UPDATE property_valuation_pdfs SET pdf_bytes = 'x' WHERE valuation_id = %s", (v["id"],), commit=True)
    with pytest.raises(psycopg2.errors.RaiseException):
        _q(db, "DELETE FROM property_valuation_pdfs WHERE valuation_id = %s", (v["id"],), commit=True)


def test_t_only_the_approved_version_of_the_same_agency_gets_a_document(world, db):
    import psycopg2

    s = world["service"]
    draft = s.create_draft(world["owner"], world["prop"], _content())
    with pytest.raises(psycopg2.errors.RaiseException):
        _insert(db, valuation_id=draft["id"], agency_id=world["a"], property_id=world["prop"], version=1,
                user_id=world["users"]["owner"])
    v = s.approve(world["owner"], draft["id"], draft["row_version"])
    for agency_id, property_id, version in ((world["b"], world["prop"], 1), (world["a"], world["prop_b"], 1),
                                            (world["a"], world["prop"], 2)):
        with pytest.raises(psycopg2.errors.RaiseException):
            _insert(db, valuation_id=v["id"], agency_id=agency_id, property_id=property_id, version=version,
                    user_id=world["users"]["owner"])
    nv = s.create_version(world["owner"], v["id"])
    s.approve(world["owner"], nv["id"], nv["row_version"])          # v1 ora e' superata
    with pytest.raises(psycopg2.errors.RaiseException):
        _insert(db, valuation_id=v["id"], agency_id=world["a"], property_id=world["prop"], version=1,
                user_id=world["users"]["owner"])
    assert _q(db, "SELECT count(*) AS n FROM property_valuation_pdfs")[0]["n"] == 0


# ---------------------------------------------------------------------------
# S - il service con lo SQL reale
# ---------------------------------------------------------------------------

def test_s_generate_download_and_regenerate_return_the_same_artifact(world, db):
    v = _approved(world)
    first = world["pdf"].generate_pdf(world["owner"], v["id"])
    second = world["pdf"].generate_pdf(world["admin"], v["id"])
    assert first["created"] is True and second["created"] is False
    assert first["pdf"]["sha256"] == second["pdf"]["sha256"]
    got = world["pdf"].download_pdf(world["agent"], v["id"])
    stored = bytes(_q(db, "SELECT pdf_bytes FROM property_valuation_pdfs WHERE valuation_id = %s", (v["id"],))[0]["pdf_bytes"])
    assert got["data"] == stored and hashlib.sha256(stored).hexdigest() == first["pdf"]["sha256"]
    listed = world["service"].list_valuations(world["owner"], world["prop"])["items"][0]
    assert listed["pdf"]["sha256"] == first["pdf"]["sha256"] and listed["can_generate_pdf"] is False


def test_s_other_agency_is_not_found(world):
    v = _approved(world)
    world["pdf"].generate_pdf(world["owner"], v["id"])
    other = world["owner"].__class__(user_id=world["users"]["owner"], agency_id=world["b"], role="agency_owner",
                                     is_platform_admin=False, session_id=1, auth_channel="operator_session")
    for call in (world["pdf"].generate_pdf, world["pdf"].download_pdf):
        with pytest.raises(world["service"].NotFound):
            call(other, v["id"])


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


def test_c_two_simultaneous_generations_keep_one_artifact(world, db):
    v = _approved(world)
    g = world["pdf"]
    with _holding_property_lock(db, world["prop"]):
        runs = [_run(lambda c=c: g.generate_pdf(c, v["id"])) for c in (world["owner"], world["admin"])]
        assert _waiting_on_lock(db, 2), "i due salvataggi non si sono messi in fila sul lock dell'immobile"
    for t, _ in runs:
        t.join(30)
    assert all("value" in o for _, o in runs), runs
    assert sorted(o["value"]["created"] for _, o in runs) == [False, True]
    assert len({o["value"]["pdf"]["sha256"] for _, o in runs}) == 1
    assert _q(db, "SELECT count(*) AS n FROM property_valuation_pdfs")[0]["n"] == 1


def test_c_generation_racing_a_new_approval_stays_coherent(world, db):
    s, g = world["service"], world["pdf"]
    v = _approved(world)
    nv = s.create_version(world["owner"], v["id"])
    with _holding_property_lock(db, world["prop"]):
        runs = [_run(lambda: g.generate_pdf(world["owner"], v["id"])),
                _run(lambda: s.approve(world["admin"], nv["id"], nv["row_version"]))]
        assert _waiting_on_lock(db, 2)
    for t, _ in runs:
        t.join(30)
    gen, appr = runs[0][1], runs[1][1]
    assert "value" in appr, appr
    rows = _q(db, "SELECT valuation_id, valuation_version FROM property_valuation_pdfs")
    if "value" in gen:                     # salvato prima della sostituzione: documento della v1, ora storica
        assert rows == [{"valuation_id": v["id"], "valuation_version": 1}]
    else:                                  # la sostituzione e' passata prima: nessun documento
        assert gen["error"].code == "PDF_SUPERSEDED" and rows == []
    assert _q(db, "SELECT status FROM property_valuations WHERE id = %s", (v["id"],))[0]["status"] == "superseded"
