"""BLOCCO-2C - collaudo integrato di 1A, 1B, 2A e 2B su PostgreSQL VERO.

Gira solo con `P29_TEST_DSN` LOCALE (socket o localhost, come le prove
CENSIMENTO full-schema) E `BLOCCO_2C_TEST_CLUSTER_VERIFIED=1`, impostata da chi
lancia le prove dopo averne verificato l'identita' (cluster usa-e-getta).

Lo schema NON e' un insieme di migrazioni isolate: si rifa' da zero, su un
database locale usa-e-getta, la procedura con cui il TEST e' nato davvero
(stesso percorso di `test_censimento_1_fullschema_postgres.py`):

  1. tabelle legacy del sito con le funzioni vere di `database.py`;
  2. 001..025 come sul TEST (senza varianti `_prod`);
  3. 026 e poi 027..114 ESEGUITE dal runner vero (`scripts/p26_migrate.py`):
     e' lo "schema precedente" ai quattro blocchi;
  4. dati sintetici gia' presenti (due agenzie, operatori, un immobile con
     prezzo e storico, impostazioni agenzia);
  5. 115 -> 116 -> 117 dal runner sulla cartella reale: il piano contiene
     solo quelle tre, i dati presenti restano identici.

Il database si chiama `stima360_db_test` perche' 010/011 lo pretendono per
nome: e' creato e distrutto SUL CLUSTER LOCALE del DSN (mai su Render); se
un database con quel nome esiste gia' li', il modulo si ferma (skip).

Poi il percorso completo passa dall'APP VERA (`main.app`) con login vero
(cookie di sessione) - verifiche HTTP complete - e la concorrenza usa
connessioni vere sincronizzate su pg_stat_activity (niente sleep arbitrari):

    M  migrazioni 115/116/117 dal runner sullo schema completo con dati presenti
    A  1A: creazione e modifica di prezzo/stato scrivono l'autore della sessione
    P  percorso completo (HTTP): dati e logo, immobile senza incarico, bozza
       vendita+locazione, approvazione, PDF, SHA-256 e byte identici, logo
       cambiato senza alterare il PDF, nuova versione con PDF distinto, vecchio
       documento scaricabile, prezzo invariato, altra agenzia esclusa, permessi
       agente/admin e consultazione in sola lettura
    C  concorrenza vera: due bozze, due approvazioni, due generazioni PDF,
       aggiornamenti concorrenti delle impostazioni agenzia
"""
from __future__ import annotations

import argparse
import hashlib
import io
import os
import threading
import time
from decimal import Decimal
from urllib.parse import urlparse

import pytest

from tests.test_censimento_1_fullschema_postgres import (
    IMPRONTA_CERTIFICATA,
    MIGRAZIONI,
    NOME_DB,
    _cartella_fino_a,
    _dsn_per,
    _env_runner,
    _pre_baseline,
    _runner,
)
from tests.test_crm_ops_3_acquisitions_postgres import _dsn_locale

DSN = os.getenv("P29_TEST_DSN")
CLUSTER_VERIFIED = os.getenv("BLOCCO_2C_TEST_CLUSTER_VERIFIED") == "1"

pytestmark = [
    pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL locale per BLOCCO-2C"),
    pytest.mark.skipif(not CLUSTER_VERIFIED, reason="cluster usa-e-getta non dichiarato verificato "
                       "(BLOCCO_2C_TEST_CLUSTER_VERIFIED=1)"),
]

OPERATORE = "blocco2c.collaudo"
PASSWORD = "Blocco2c-prova-locale!"
# SENTINELLA AGGIORNATA DA BLOCCO-4A: dopo la 117 la cartella reale contiene anche la
# 118 (ricezione WhatsApp), applicata nello stesso passaggio dal runner.
# SENTINELLA AGGIORNATA DA BLOCCO-4D (modo d'invio, 123) e prima da BLOCCO-4D/5A: la 119 (simulazione delle risposte automatiche) e la 120
# (prenotazione online delle visite) si aggiungono in coda.
NUOVE = ("115_agency_logos", "116_property_valuations", "117_property_valuation_pdfs", "118_whatsapp_inbound",
         "119_whatsapp_auto_reply_simulation", "120_property_visit_booking", "121_visit_sheets",
         "122_signature_requests", "123_whatsapp_auto_reply_send",
         "124_do_not_contact_and_link_revocation")
BASE = {"characteristics": "Trilocale sintetico al secondo piano.",
        "surface": {"value": "85", "kind": "commerciale", "source": "planimetria"},
        "rationale": "Motivazione sintetica: stato buono, zona servita.",
        "sources": "Sopralluogo sintetico e annunci di zona di prova.",
        "comparables_note": "Nessun comparabile: dati sintetici."}
SALE = {"min": "180000", "max": "210000", "recommended": "195000"}
RENT = {"segment": "residenziale_ordinario", "furnishing": "arredato", "expenses": "escluse",
        "min": "700", "max": "850", "recommended": "780"}


def _png(color, size=(240, 90)) -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, format="PNG")
    return out.getvalue()


# ---------------------------------------------------------------------------
# banco: schema completo fino alla 114, dati presenti, poi 115 -> 116 -> 117
# ---------------------------------------------------------------------------

def _seed_pre_115(cur):
    """Dati sintetici presenti PRIMA dei quattro blocchi."""
    from operator_auth.security import hash_password

    ids = {}
    for key, name in (("a", "Alfa Prova"), ("b", "Beta Prova")):
        cur.execute("INSERT INTO agencies (name, slug, status, settings) VALUES (%s, %s, 'active', %s::jsonb) "
                    "RETURNING id", (name, f"b2c-{key}", '{"timezone": "Europe/Rome", "locale": "it-IT"}'))
        ids[key] = cur.fetchone()[0]
    people = {"owner_a": ("a", "agency_owner", "Olga", "Alfa"), "admin_a": ("a", "agency_admin", "Adele", "Alfa"),
              "agent_a": ("a", "agent", "Aldo", "Alfa"), "agent2_a": ("a", "agent", "Ada", "Alfa"),
              "owner_b": ("b", "agency_owner", "Bruno", "Beta")}
    digest = hash_password(PASSWORD)
    for key, (agency, role, first, last) in people.items():
        email = f"b2c.{key}@example.test"
        cur.execute("INSERT INTO operator_users (email, email_normalized, password_hash, first_name, last_name, status) "
                    "VALUES (%s, %s, %s, %s, %s, 'active') RETURNING id", (email, email, digest, first, last))
        ids[key] = cur.fetchone()[0]
        cur.execute("INSERT INTO agency_memberships (agency_id, operator_user_id, role, status) "
                    "VALUES (%s, %s, %s, 'active')", (ids[agency], ids[key], role))
    cur.execute("INSERT INTO operator_users (email, email_normalized, password_hash, first_name, status, "
                "is_platform_admin) VALUES ('b2c.super@example.test', 'b2c.super@example.test', %s, 'Super', "
                "'active', TRUE) RETURNING id", (digest,))
    ids["super"] = cur.fetchone()[0]
    # un immobile storico con prezzo e storico gia' scritto (autore legacy)
    cur.execute("INSERT INTO properties (agency_id, title, property_type, commercial_status, city, asking_price) "
                "VALUES (%s, 'Immobile storico', 'apartment', 'active', 'Comune Sintetico', 150000) RETURNING id",
                (ids["a"],))
    ids["storico"] = cur.fetchone()[0]
    cur.execute("INSERT INTO property_price_history (property_id, new_price, change_reason, changed_by) "
                "VALUES (%s, 150000, 'initial price', 'legacy')", (ids["storico"],))
    return ids


def _fingerprint(cur, ids):
    out = {}
    for table, where in (("agencies", "id IN (%s, %s)"), ("properties", "agency_id IN (%s, %s)")):
        cur.execute(f"SELECT md5(string_agg(row_to_json(t)::text, '|' ORDER BY id)) FROM {table} t WHERE {where}",
                    (ids["a"], ids["b"]))
        out[table] = cur.fetchone()[0]
    cur.execute("SELECT md5(string_agg(row_to_json(t)::text, '|' ORDER BY id)) FROM property_price_history t")
    out["property_price_history"] = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM operator_users WHERE email LIKE 'b2c.%%'")
    out["operators"] = cur.fetchone()[0]
    return out


@pytest.fixture(scope="module")
def banco():
    psycopg2 = pytest.importorskip("psycopg2")
    _dsn_locale(DSN)
    servizio = psycopg2.connect(DSN)
    servizio.autocommit = True
    with servizio.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (NOME_DB,))
        if cur.fetchone():
            servizio.close()
            pytest.skip(f"sul cluster locale esiste gia' un database {NOME_DB}: non lo tocco")
        cur.execute(f'CREATE DATABASE "{NOME_DB}"')
    dsn = _dsn_per(NOME_DB)
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    mp = pytest.MonkeyPatch()
    try:
        import database as legacy
        originale = legacy.get_connection
        legacy.get_connection = lambda: psycopg2.connect(dsn)
        try:
            legacy.crea_tabella_stime()
            legacy.crea_tabella_stime_dettagliate()
            legacy.crea_tabella_zone_valori()
            legacy.migrazione_allinea_stime()
        finally:
            legacy.get_connection = originale
        with conn.cursor() as cur:
            for p in _pre_baseline():
                cur.execute(p.read_text(encoding="utf-8"))
        runner = _runner()
        _env_runner(mp, dsn, NOME_DB)
        args = argparse.Namespace(**(runner.SHARED_DEFAULTS | {
            "operator": OPERATORE, "baseline_fingerprint": IMPRONTA_CERTIFICATA,
            "baseline_artifact": "reports/p26_baseline_TEST_20260905T170601Z.json"}))
        for massimo in (26, 114):
            mp.setattr(runner, "MIGRATIONS_DIR", _cartella_fino_a(massimo))
            assert runner.command_apply(args) == 0
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('agency_logos'), to_regclass('property_valuations'), "
                        "to_regclass('property_valuation_pdfs')")
            assert cur.fetchone() == (None, None, None)
            ids = _seed_pre_115(cur)
            before = _fingerprint(cur, ids)
        # 115 -> 116 -> 117 dal runner, sulla cartella REALE
        mp.setattr(runner, "MIGRATIONS_DIR", MIGRAZIONI)
        status_out = io.StringIO()
        import contextlib
        with contextlib.redirect_stdout(status_out):
            assert runner.command_status(args) == 0
        pending = [line.split()[1] for line in status_out.getvalue().splitlines()
                   if line.strip().startswith("pending")]
        apply_out = io.StringIO()
        with contextlib.redirect_stdout(apply_out):
            assert runner.command_apply(args) == 0
        applied = [line.split()[1] for line in apply_out.getvalue().splitlines()
                   if line.strip().startswith("applied ")]
        with conn.cursor() as cur:
            after = _fingerprint(cur, ids)
            cur.execute("SELECT version FROM schema_migrations WHERE version >= '115' AND rolled_back_at IS NULL "
                        "ORDER BY version")
            ledger = [r[0] for r in cur.fetchall()]
        yield {"psycopg2": psycopg2, "dsn": dsn, "nome": NOME_DB, "ids": ids, "pending": pending,
               "applied": applied, "ledger": ledger, "before": before, "after": after}
    finally:
        mp.undo()
        conn.close()
        with servizio.cursor() as cur:
            cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = %s AND pid <> pg_backend_pid()", (NOME_DB,))
            cur.execute(f'DROP DATABASE IF EXISTS "{NOME_DB}"')
        servizio.close()


def _q(b, sql, params=None):
    conn = b["psycopg2"].connect(b["dsn"])
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall() if cur.description else None
    finally:
        conn.close()


@pytest.fixture
def app(banco, monkeypatch):
    """L'app VERA, con il database usa-e-getta e il Super Admin designato."""
    import database
    from fastapi.testclient import TestClient
    from platform_admin.super_admin import SUPER_ADMIN_ENV

    import main

    p = urlparse(banco["dsn"])
    for name, value in (("DB_HOST", p.hostname), ("DB_PORT", str(p.port or 5432)), ("DB_NAME", banco["nome"]),
                        ("DB_USER", p.username), ("DB_PASSWORD", p.password)):
        monkeypatch.setattr(database, name, value)
    monkeypatch.setenv(SUPER_ADMIN_ENV, str(banco["ids"]["super"]))

    def login(who):
        client = TestClient(main.app, base_url="https://testserver")
        r = client.post("/api/operator-auth/login", json={"email": f"b2c.{who}@example.test", "password": PASSWORD})
        assert r.status_code == 204, (who, r.status_code, r.text)
        return client

    return {**banco, "login": login, "app": main.app}


def _waiting_on_lock(b, count=1, timeout=15) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        n = _q(b, "SELECT count(*) FROM pg_stat_activity WHERE datname = %s AND wait_event_type = 'Lock'",
               (b["nome"],))[0][0]
        if n >= count:
            return True
        time.sleep(0.02)
    return False


class _Holding:
    """Una transazione vera che tiene il lock di una riga finche' non esce."""

    def __init__(self, b, sql, params):
        self.b, self.sql, self.params = b, sql, params

    def __enter__(self):
        self.conn = self.b["psycopg2"].connect(self.b["dsn"])
        self.cur = self.conn.cursor()
        self.cur.execute(self.sql, self.params)
        return self

    def __exit__(self, *exc):
        self.conn.commit()
        self.conn.close()


def _run(target):
    out = {}

    def runner():
        try:
            out["value"] = target()
        except Exception as exc:          # pragma: no cover - riportato nell'assert
            out["error"] = exc

    t = threading.Thread(target=runner)
    t.start()
    return t, out


# ---------------------------------------------------------------------------
# M - migrazioni sullo schema completo con dati presenti
# ---------------------------------------------------------------------------

def test_m_115_116_117_are_the_only_pending_and_apply_in_order_on_the_full_schema(banco):
    assert banco["pending"] == list(NUOVE)
    assert banco["applied"] == list(NUOVE)
    assert banco["ledger"] == list(NUOVE)
    assert banco["after"] == banco["before"], "i dati presenti prima delle migrazioni sono cambiati"
    tables = _q(banco, "SELECT to_regclass('agency_logos')::text, to_regclass('property_valuations')::text, "
                       "to_regclass('property_valuation_pdfs')::text")[0]
    assert tables == ("agency_logos", "property_valuations", "property_valuation_pdfs")
    triggers = {r[0] for r in _q(banco, "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND tgrelid IN "
                                        "('property_valuations'::regclass, 'property_valuation_pdfs'::regclass)")}
    assert {"trg_property_valuations_guard", "trg_property_valuation_pdfs_guard"} <= triggers


def test_m_the_runner_has_nothing_left_to_apply(banco, monkeypatch, capsys):
    runner = _runner()
    _env_runner(monkeypatch, banco["dsn"], banco["nome"])
    monkeypatch.setattr(runner, "MIGRATIONS_DIR", MIGRAZIONI)
    args = argparse.Namespace(**(runner.SHARED_DEFAULTS | {"operator": OPERATORE}))
    assert runner.command_status(args) == 0
    out = capsys.readouterr().out
    assert "pending" not in out and "PROBLEM" not in out


# ---------------------------------------------------------------------------
# A + P - percorso completo con l'app vera e il login vero
# ---------------------------------------------------------------------------

def _history(b, property_id):
    price = _q(b, "SELECT new_price, changed_by, change_reason FROM property_price_history WHERE property_id = %s "
                  "ORDER BY id", (property_id,))
    status = _q(b, "SELECT field_name, new_value, changed_by FROM property_status_history WHERE property_id = %s "
                   "ORDER BY id", (property_id,))
    return price, status


def test_p_full_path_over_http(app):
    b, ids = app, app["ids"]
    owner, admin, agent, agent2, owner_b = (app["login"](k) for k in
                                            ("owner_a", "admin_a", "agent_a", "agent2_a", "owner_b"))

    # --- 1B: dati e logo dell'agenzia --------------------------------------
    r = owner.put("/api/agency-profile", json={"display_name": "Alfa Immobiliare", "legal_name": "Alfa Prova S.r.l.",
                                                "vat_number": "01234567890", "phone": "+39 000 111222",
                                                "email": "info@alfa.example"})
    assert r.status_code == 200, r.text
    assert agent.put("/api/agency-profile", json={"display_name": "Agente"}).status_code == 403
    logo_1 = _png((20, 70, 140))
    r = owner.put("/api/agency-profile/logo", content=logo_1, headers={"Content-Type": "image/png"})
    assert r.status_code == 200, r.text
    logo_1_sha = r.json()["sha256"]
    settings = _q(b, "SELECT settings FROM agencies WHERE id = %s", (ids["a"],))[0][0]
    assert settings["report_profile"]["display_name"] == "Alfa Immobiliare" and settings["timezone"] == "Europe/Rome"
    profile_b = owner_b.get("/api/agency-profile").json()
    assert profile_b["agency_id"] == ids["b"] and profile_b["agency_name"] == "Beta Prova"
    assert profile_b["configured"] is False and profile_b["logo"] is None

    # --- 1A: immobile senza incarico, autore dello storico dalla sessione -----
    # in creazione un autore dal browser non e' nemmeno accettato (corpo chiuso)
    r = owner.post("/api/property/properties", json={"title": "Rifiutato", "changed_by": "falso-dal-browser"})
    assert r.status_code == 422
    r = owner.post("/api/property/properties", json={
        "title": "Trilocale collaudo 2C", "city": "Comune Sintetico", "address": "Via Prova", "civic_number": "1",
        "surface_sqm": "80", "asking_price": "199000", "commercial_status": "draft"})
    assert r.status_code == 201, r.text
    prop = r.json()
    pid = prop["id"]
    assert prop.get("mandate_type") is None
    price, status = _history(b, pid)
    assert price == [(Decimal("199000.00"), f"operator:{ids['owner_a']}", "initial price")]
    assert ("commercial_status", "draft", f"operator:{ids['owner_a']}") in status
    r = admin.patch(f"/api/property/properties/{pid}", json={
        "asking_price": "205000", "commercial_status": "evaluation", "assigned_agent_id": ids["agent_a"],
        "changed_by": "falso-dal-browser", "change_reason": "Revisione collaudo"})
    assert r.status_code == 200, r.text
    price, status = _history(b, pid)
    assert price[-1][:2] == (Decimal("205000.00"), f"operator:{ids['admin_a']}")
    assert status[-1] == ("commercial_status", "evaluation", f"operator:{ids['admin_a']}")
    assert all("falso" not in (row[1] or "") for row in price) and all("falso" not in (row[2] or "") for row in status)
    asking_before = _q(b, "SELECT asking_price FROM properties WHERE id = %s", (pid,))[0][0]

    # --- 2A: bozza vendita + locazione, approvazione ------------------------
    r = agent.post("/api/property-valuations", json={"property_id": pid,
                                                     "content": {**BASE, "sale": SALE, "rent": RENT}})
    assert r.status_code == 201, r.text
    draft = r.json()
    assert draft["created_by_user_id"] == ids["agent_a"] and draft["status"] == "draft"
    assert agent2.post("/api/property-valuations", json={"property_id": pid, "content": BASE}).status_code == 403
    assert _approve(agent, draft).status_code == 403
    r = _approve(admin, draft)
    assert r.status_code == 200, r.text
    v1 = r.json()
    assert v1["status"] == "approved" and v1["approved_by_user_id"] == ids["admin_a"]

    # --- 2B: PDF -------------------------------------------------------------
    assert agent2.get(f"/api/property-valuations/{v1['id']}/pdf").status_code == 404
    assert agent2.post(f"/api/property-valuations/{v1['id']}/pdf").status_code == 403
    r = owner.post(f"/api/property-valuations/{v1['id']}/pdf")
    assert r.status_code == 201, r.text
    meta1 = r.json()["pdf"]
    stored1 = _q(b, "SELECT pdf_bytes, sha256, generated_by_user_id, valuation_version, header_snapshot "
                    "FROM property_valuation_pdfs WHERE valuation_id = %s", (v1["id"],))[0]
    bytes1 = bytes(stored1[0])
    assert hashlib.sha256(bytes1).hexdigest() == stored1[1] == meta1["sha256"]
    assert stored1[2] == ids["owner_a"] and stored1[3] == 1
    assert stored1[4]["logo"]["sha256"] == logo_1_sha and stored1[4]["header_name"] == "Alfa Immobiliare"
    downloads = [c.get(f"/api/property-valuations/{v1['id']}/pdf") for c in (owner, agent, agent2, admin)]
    for d in downloads:
        assert d.status_code == 200 and d.content == bytes1
        assert d.headers["etag"] == f'"{stored1[1]}"' and d.headers["cache-control"] == "private, no-store"

    # --- logo e dati cambiati: il PDF esistente non cambia --------------------
    r = owner.put("/api/agency-profile/logo", content=_png((200, 30, 30)), headers={"Content-Type": "image/png"})
    assert r.status_code == 200 and r.json()["sha256"] != logo_1_sha
    assert owner.put("/api/agency-profile", json={"display_name": "Alfa Rinominata"}).status_code == 200
    again = owner.post(f"/api/property-valuations/{v1['id']}/pdf")
    assert again.status_code == 200 and again.json()["created"] is False
    assert owner.get(f"/api/property-valuations/{v1['id']}/pdf").content == bytes1

    # --- nuova versione approvata con PDF distinto ---------------------------
    r = owner.post(f"/api/property-valuations/{v1['id']}/new-version")
    assert r.status_code == 201, r.text
    nv = r.json()
    r = owner.put(f"/api/property-valuations/{nv['id']}", json={
        "row_version": nv["row_version"], "content": {**BASE, "sale": {**SALE, "recommended": "198000"}, "rent": RENT}})
    assert r.status_code == 200, r.text
    r = _approve(owner, r.json())
    assert r.status_code == 200, r.text
    v2 = r.json()
    r = owner.post(f"/api/property-valuations/{v2['id']}/pdf")
    assert r.status_code == 201, r.text
    bytes2 = owner.get(f"/api/property-valuations/{v2['id']}/pdf").content
    assert bytes2 != bytes1 and r.json()["pdf"]["valuation_version"] == 2
    snapshot2 = _q(b, "SELECT header_snapshot FROM property_valuation_pdfs WHERE valuation_id = %s", (v2["id"],))[0][0]
    assert snapshot2["header_name"] == "Alfa Rinominata" and snapshot2["logo"]["sha256"] != logo_1_sha
    old = agent.get(f"/api/property-valuations/{v1['id']}/pdf")
    assert old.status_code == 200 and old.content == bytes1
    listing = owner.get("/api/property-valuations", params={"property_id": pid}).json()["items"]
    by_version = {i["version"]: i for i in listing}
    assert by_version[1]["status"] == "superseded" and by_version[1]["pdf"]["sha256"] == stored1[1]
    assert by_version[2]["status"] == "approved" and by_version[2]["can_generate_pdf"] is False
    assert _q(b, "SELECT count(*) FROM property_valuation_pdfs WHERE property_id = %s", (pid,))[0][0] == 2

    # --- prezzo dell'immobile invariato dalle valutazioni --------------------
    assert _q(b, "SELECT asking_price FROM properties WHERE id = %s", (pid,))[0][0] == asking_before
    assert _history(b, pid)[0][-1][0] == Decimal("205000.00")

    # --- l'altra agenzia non vede nulla --------------------------------------
    for r in (owner_b.get("/api/property-valuations", params={"property_id": pid}),
              owner_b.get(f"/api/property-valuations/{v1['id']}"),
              owner_b.post(f"/api/property-valuations/{v1['id']}/pdf"),
              owner_b.get(f"/api/property-valuations/{v1['id']}/pdf"),
              owner_b.post("/api/property-valuations", json={"property_id": pid, "content": BASE})):
        assert r.status_code == 404, (r.request.method, r.request.url, r.text)
        assert b"%PDF" not in r.content
    assert owner_b.get("/api/agency-profile/logo").status_code == 404

    # --- consultazione in sola lettura (Super Admin in agenzia sospesa) ------
    sa = app["login"]("super")
    try:
        r = sa.patch(f"/api/platform/agencies/{ids['a']}", json={"status": "suspended"})
        assert r.status_code == 200, r.text
        r = sa.post(f"/api/platform/agencies/{ids['a']}/enter")
        assert r.status_code == 200, r.text
        assert r.json()["read_only"] is True
        d = sa.get(f"/api/property-valuations/{v1['id']}/pdf")
        assert d.status_code == 200 and d.content == bytes1
        assert sa.get("/api/property-valuations", params={"property_id": pid}).status_code == 200
        counts = _q(b, "SELECT (SELECT count(*) FROM property_valuations), (SELECT count(*) FROM property_valuation_pdfs)")
        for r in (sa.post(f"/api/property-valuations/{v2['id']}/pdf"),
                  sa.post(f"/api/property-valuations/{v2['id']}/new-version"),
                  sa.post("/api/property-valuations", json={"property_id": pid, "content": BASE}),
                  sa.put("/api/agency-profile", json={"display_name": "Sola lettura"}),
                  sa.put("/api/agency-profile/logo", content=logo_1, headers={"Content-Type": "image/png"})):
            assert r.status_code == 403 and "sola lettura" in r.text.lower(), (
                r.request.method, r.request.url, r.status_code, r.text)
        assert _q(b, "SELECT (SELECT count(*) FROM property_valuations), "
                     "(SELECT count(*) FROM property_valuation_pdfs)") == counts
        assert sa.post("/api/platform/agency-context/exit").status_code == 204
    finally:
        sa.patch(f"/api/platform/agencies/{ids['a']}", json={"status": "active"})
        _q(b, "UPDATE agencies SET status = 'active' WHERE id = %s", (ids["a"],))
    # la sospensione ha chiuso le sessioni dell'agenzia (ADM-1); dopo la
    # riattivazione un nuovo login torna a pieni poteri
    assert owner.get(f"/api/property-valuations/{v2['id']}").status_code == 401
    owner = app["login"]("owner_a")
    assert owner.get(f"/api/property-valuations/{v2['id']}").status_code == 200
    assert owner.get(f"/api/property-valuations/{v2['id']}/pdf").content == bytes2


def _approve(client, v):
    return client.post(f"/api/property-valuations/{v['id']}/approve", json={"row_version": v["row_version"]})


# ---------------------------------------------------------------------------
# C - concorrenza vera (route vere, connessioni vere)
# ---------------------------------------------------------------------------

def _new_property(app, client, title):
    r = client.post("/api/property/properties", json={"title": title, "city": "Comune Sintetico",
                                                       "asking_price": "120000"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_c_two_simultaneous_drafts_only_one_exists(app):
    owner = app["login"]("owner_a")
    pid = _new_property(app, owner, "Concorrenza bozze 2C")
    clients = [app["login"]("owner_a"), app["login"]("admin_a")]
    with _Holding(app, "SELECT id FROM properties WHERE id = %s FOR UPDATE", (pid,)):
        runs = [_run(lambda c=c: c.post("/api/property-valuations", json={"property_id": pid, "content": BASE}))
                for c in clients]
        assert _waiting_on_lock(app, 2), "le due creazioni non si sono messe in fila sul lock dell'immobile"
    for t, _ in runs:
        t.join(30)
    codes = sorted(o["value"].status_code for _, o in runs)
    assert codes == [201, 409], [o for _, o in runs]
    assert _q(app, "SELECT count(*) FROM property_valuations WHERE property_id = %s AND status = 'draft'",
              (pid,))[0][0] == 1


def test_c_two_simultaneous_approvals_one_wins(app):
    owner = app["login"]("owner_a")
    pid = _new_property(app, owner, "Concorrenza approvazioni 2C")
    v = owner.post("/api/property-valuations", json={"property_id": pid, "content": {**BASE, "sale": SALE}}).json()
    clients = [app["login"]("owner_a"), app["login"]("admin_a")]
    with _Holding(app, "SELECT id FROM properties WHERE id = %s FOR UPDATE", (pid,)):
        runs = [_run(lambda c=c: _approve(c, v)) for c in clients]
        assert _waiting_on_lock(app, 2)
    for t, _ in runs:
        t.join(30)
    responses = [o["value"] for _, o in runs]
    assert sorted(r.status_code for r in responses) == [200, 409], [r.text for r in responses]
    assert _q(app, "SELECT status FROM property_valuations WHERE property_id = %s", (pid,)) == [("approved",)]


def test_c_two_simultaneous_pdf_generations_keep_one_artifact(app):
    owner = app["login"]("owner_a")
    pid = _new_property(app, owner, "Concorrenza PDF 2C")
    v = owner.post("/api/property-valuations", json={"property_id": pid, "content": {**BASE, "rent": RENT}}).json()
    v = _approve(owner, v).json()
    clients = [app["login"]("owner_a"), app["login"]("admin_a")]
    with _Holding(app, "SELECT id FROM properties WHERE id = %s FOR UPDATE", (pid,)):
        runs = [_run(lambda c=c: c.post(f"/api/property-valuations/{v['id']}/pdf")) for c in clients]
        assert _waiting_on_lock(app, 2), "i due salvataggi non si sono messi in fila"
    for t, _ in runs:
        t.join(60)
    responses = [o["value"] for _, o in runs]
    assert sorted(r.status_code for r in responses) == [200, 201], [r.text for r in responses]
    assert len({r.json()["pdf"]["sha256"] for r in responses}) == 1
    rows = _q(app, "SELECT sha256, pdf_bytes FROM property_valuation_pdfs WHERE valuation_id = %s", (v["id"],))
    assert len(rows) == 1 and hashlib.sha256(bytes(rows[0][1])).hexdigest() == rows[0][0]
    assert owner.get(f"/api/property-valuations/{v['id']}/pdf").content == bytes(rows[0][1])


def test_c_concurrent_agency_settings_updates_keep_both(app):
    """Dati agenzia (owner) e configurazione Platform (Super Admin) sulla stessa
    riga `agencies`, in coda dietro una transazione che la tiene: nessuna delle
    due perde l'altra."""
    ids = app["ids"]
    owner, sa = app["login"]("owner_a"), app["login"]("super")
    with _Holding(app, "SELECT id FROM agencies WHERE id = %s FOR UPDATE", (ids["a"],)):
        runs = [_run(lambda: owner.put("/api/agency-profile", json={"display_name": "Alfa Concorrente",
                                                                     "phone": "+39 000 999888"})),
                _run(lambda: sa.patch(f"/api/platform/agencies/{ids['a']}/configuration",
                                      json={"timezone": "Europe/Paris"}))]
        assert _waiting_on_lock(app, 2), "i due aggiornamenti non hanno atteso il lock della riga agenzia"
    for t, _ in runs:
        t.join(30)
    responses = [o.get("value") for _, o in runs]
    assert all(r is not None and r.status_code == 200 for r in responses), [o for _, o in runs]
    settings = _q(app, "SELECT settings FROM agencies WHERE id = %s", (ids["a"],))[0][0]
    assert settings["timezone"] == "Europe/Paris"
    assert settings["report_profile"]["display_name"] == "Alfa Concorrente"
    assert settings["report_profile"]["phone"] == "+39 000 999888"
