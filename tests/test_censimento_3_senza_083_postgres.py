"""CENSIMENTO-1 Fase 3 - il codice della Fase 3 su un database SENZA la 083.

Ordine DB-first: il codice puo' arrivare dove la migration non c'e' ancora
(PROD, o un ambiente rimasto alla 082). Qui lo schema e' ricostruito col
metodo delle altre prove ma FERMO alla 082, con un immobile esistente, e le
rotte VERE devono rispondere in modo controllato:
  * ogni rotta del censimento -> 503 CENSUS_NOT_INSTALLED (dal controllo
    esplicito sul catalogo, prima di leggere righe: niente KeyError, niente
    eccezioni catturate alla cieca), senza scrivere nulla;
  * POST/PATCH generici con un campo della 083 inviato esplicitamente -> 503
    controllato; senza quei campi -> funzionano come prima (201/200);
  * acquisizioni e readiness: invariate.
Opt-in `P29_TEST_DSN`, SOLO database locale; DB `stima360_db_test`.
"""
from __future__ import annotations

import argparse
import uuid

import pytest

from tests.test_censimento_1_fullschema_postgres import (
    DSN, IMPRONTA_CERTIFICATA, NOME_DB, _cartella_fino_a, _dsn_locale, _dsn_per, _env_runner, _fixture,
    _pre_baseline, _runner,
)

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare CENSIMENTO-1 Fase 3 senza 083")

OPERATORE = "censimento.fase3.senza083"


@pytest.fixture(scope="module")
def fino_alla_082():
    psycopg2 = pytest.importorskip("psycopg2")
    from psycopg2.extras import DictCursor
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
    conn = psycopg2.connect(dsn, cursor_factory=DictCursor)
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
        c = {"conn": conn, "dsn": dsn, "nome": NOME_DB, "psycopg2": psycopg2}
        runner = _runner()
        _env_runner(mp, dsn, NOME_DB)
        args = argparse.Namespace(**(runner.SHARED_DEFAULTS | {
            "operator": OPERATORE, "baseline_fingerprint": IMPRONTA_CERTIFICATA,
            "baseline_artifact": "reports/p26_baseline_TEST_20260905T170601Z.json"}))
        for massimo in (26, 80):
            mp.setattr(runner, "MIGRATIONS_DIR", _cartella_fino_a(massimo))
            assert runner.command_apply(args) == 0
        _fixture(c)
        mp.setattr(runner, "MIGRATIONS_DIR", _cartella_fino_a(82))     # FERMO alla 082
        assert runner.command_apply(args) == 0
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.buildings') IS NULL AND NOT EXISTS (SELECT 1 FROM information_schema.columns "
                        "WHERE table_name = 'properties' AND column_name = 'record_kind')")
            assert cur.fetchone()[0], "la 083 non deve esserci"
            cur.execute("INSERT INTO operator_users (email, email_normalized, password_hash, first_name) "
                        "VALUES ('o.a@x.test', 'o.a@x.test', 'pbkdf2_sha256$1$x$y', 'Olga') RETURNING id")
            uid = cur.fetchone()[0]
            cur.execute("INSERT INTO agency_memberships (agency_id, operator_user_id, role, status) VALUES (1, %s, 'agency_owner', 'active')", (uid,))
            cur.execute("INSERT INTO contacts (agency_id, display_name, first_name, last_name) VALUES (1, 'Mario Rossi', 'Mario', 'Rossi') RETURNING id")
            c["mario"] = cur.fetchone()[0]
            c["uid"] = uid
        from core import database as core_database
        mp.setattr(core_database, "get_connection", lambda: psycopg2.connect(dsn))
        yield c
    finally:
        mp.undo()
        conn.close()
        with servizio.cursor() as cur:
            cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = %s AND pid <> pg_backend_pid()", (NOME_DB,))
            cur.execute(f'DROP DATABASE IF EXISTS "{NOME_DB}"')
        servizio.close()


def _q(c, sql, params=None):
    with c["conn"].cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else None


@pytest.fixture
def api(fino_alla_082):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from acquisitions.router import router as acquisizioni
    from operator_auth.context import OperatorContext
    from operator_auth.dependencies import legacy_basic_agency_context, require_operator
    from property.router import router as immobili

    def contesto():
        return OperatorContext(user_id=fino_alla_082["uid"], agency_id=1, role="agency_owner", is_platform_admin=False,
                               session_id=None, auth_channel="operator_session")
    app = FastAPI()
    app.include_router(immobili)
    app.include_router(acquisizioni)
    app.dependency_overrides[legacy_basic_agency_context] = contesto
    app.dependency_overrides[require_operator] = contesto
    return TestClient(app, raise_server_exceptions=False)


def _impronta(c):
    return _q(c, "SELECT md5(string_agg(id || ':' || coalesce(code,'') || ':' || property_type || ':' || commercial_status "
                 "|| ':' || coalesce(title,''), ',' ORDER BY id)) FROM properties")[0][0]


def test_01_ogni_rotta_del_censimento_risponde_503_senza_scrivere(fino_alla_082, api):
    esistente = _q(fino_alla_082, "SELECT id FROM properties WHERE commercial_status = 'draft' ORDER BY id LIMIT 1")[0][0]
    prima = _impronta(fino_alla_082)
    rotte = (
        ("get", "/api/property/buildings", None), ("post", "/api/property/buildings", {"city": "Fermo"}),
        ("get", "/api/property/buildings/1", None), ("patch", "/api/property/buildings/1", {"name": "x"}),
        ("post", "/api/property/census/units", {"floor": "1"}),
        ("get", f"/api/property/properties/{esistente}/census", None),
        ("post", f"/api/property/properties/{esistente}/pertinenze/link", {"pertinenza_id": esistente + 1}),
        ("post", f"/api/property/properties/{esistente}/pertinenze/{esistente + 1}/unlink", None),
        ("post", f"/api/property/properties/{esistente}/accessories", {"kind": "box"}),
        ("patch", f"/api/property/properties/{esistente}/accessories/1", {"notes": "x"}),
        ("delete", f"/api/property/properties/{esistente}/accessories/1", None),
        ("post", f"/api/property/properties/{esistente}/accessories/1/resolve", {"outcome": "included"}),
        ("post", f"/api/property/properties/{esistente}/take-in-charge", {}),
        ("post", f"/api/property/properties/{esistente}/undo-create", None),
    )
    assert len(rotte) == 14
    for metodo, percorso, corpo in rotte:
        r = getattr(api, metodo)(percorso, json=corpo) if corpo is not None else getattr(api, metodo)(percorso)
        assert r.status_code == 503, (metodo, percorso, r.status_code, r.text)
        assert r.json() == {"detail": "Il modulo Censimento non e' installato su questo database (migration 083 non applicata).",
                            "code": "CENSUS_NOT_INSTALLED"}, (metodo, percorso)
    assert _impronta(fino_alla_082) == prima
    assert _q(fino_alla_082, "SELECT count(*) FROM activities")[0][0] == 0


def test_02_post_e_patch_generici_con_campi_083_errore_controllato_senza_niente_funzionano(fino_alla_082, api):
    esistente = _q(fino_alla_082, "SELECT id FROM properties WHERE commercial_status = 'draft' ORDER BY id LIMIT 1")[0][0]
    prima = _impronta(fino_alla_082)
    totale = _q(fino_alla_082, "SELECT count(*) FROM properties")[0][0]
    for corpo in ({"city": "Fermo", "cadastral_category": "A/3"}, {"staircase": "B"}, {"cadastral_section": ""}):
        r = api.post("/api/property/properties", json=corpo)
        assert r.status_code == 503 and "083" in r.json()["detail"], (corpo, r.status_code, r.text)
    for corpo in ({"cadastral_category": "A/3"}, {"internal_number": "4", "surface_sqm": "50"}):
        r = api.patch(f"/api/property/properties/{esistente}", json=corpo)
        assert r.status_code == 503, (corpo, r.status_code, r.text)
    assert _q(fino_alla_082, "SELECT count(*) FROM properties")[0][0] == totale and _impronta(fino_alla_082) == prima
    # senza campi della 083: la creazione e la modifica di sempre
    r = api.post("/api/property/properties", json={"city": "Fermo", "property_type": "storage"})
    assert r.status_code == 201 and r.json()["code"] == f"IMM-{r.json()['id']}", r.text
    r = api.patch(f"/api/property/properties/{esistente}", json={"surface_sqm": "61", "floor": "2"})
    assert r.status_code == 200 and r.json()["floor"] == "2"
    assert api.get(f"/api/property/properties/{esistente}").status_code == 200
    assert api.delete(f"/api/property/properties/{r.json()['id']}").status_code == 200


def test_03_acquisizioni_e_readiness_invariate_senza_083(fino_alla_082, api):
    from match.readiness import property_readiness
    esistente = _q(fino_alla_082, "SELECT id FROM properties WHERE commercial_status = 'draft' AND archived_at IS NULL ORDER BY id DESC LIMIT 1")[0][0]
    api.post(f"/api/property/properties/{esistente}/contacts", json={"contact_id": fino_alla_082["mario"], "role": "owner"})
    r = api.post("/api/acquisitions", json={"property_id": esistente, "owner_contact_id": fino_alla_082["mario"],
                                            "appointment": {"start_at": "2027-03-01T10:00:00+01:00",
                                                            "assigned_user_id": fino_alla_082["uid"],
                                                            "client_request_id": str(uuid.uuid4())}})
    assert r.status_code == 201, r.text            # `record_kind` letto da to_jsonb: NULL, nessun errore
    riga = _q(fino_alla_082, "SELECT row_to_json(p) FROM properties p WHERE id = %s", (esistente,))[0][0]
    assert "record_kind" not in riga and "Immobile in censimento" not in property_readiness(riga)["eligibility_reasons"]
