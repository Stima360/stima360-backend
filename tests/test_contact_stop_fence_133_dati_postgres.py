"""MIGRATION 133 su un database CON DATI, come sara' su TEST: il runner VERO porta un
database usa-e-getta fino alla 132, lo si popola (due agenzie, migliaia di contatti,
lead, stime collegate, immobili, proprietari, pause delle automazioni), poi lo
STESSO runner applica la 133.

    D1  il piano del runner contiene SOLO la 133; l'applicazione riesce e la registra
    D2  nessun dato cambia: l'impronta (md5 di tutte le righe) di OGNI tabella del
        database e' identica prima e dopo, tranne il registro delle migration
    D3  dopo la 133 la guardia funziona sui dati esistenti: le scritture coinvolte
        aspettano il contatto tenuto, le altre passano, e l'insieme e' quello della 097
"""
from __future__ import annotations

import argparse
import json

import pytest

from tests.test_censimento_1_fullschema_postgres import (
    DSN, IMPRONTA_CERTIFICATA, OPERATORE, _cartella_fino_a, _dsn_locale, _dsn_per, _env_runner, _pre_baseline,
    _runner,
)
from tests.test_censimento_3_backend_postgres import MIGRAZIONI
from tests.test_contact_stop_fence_133_postgres import NUOVA, RIFERIMENTO

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

# Sul cluster LOCALE usa-e-getta (P29_TEST_DSN, 127.0.0.1): le migration di base (OWNER 0.2)
# richiedono questo nome esatto, come per le altre suite a schema completo. Se il database
# esiste gia' la fixture si ferma senza toccarlo; alla fine lo elimina.
NOME_DB = "stima360_db_test"


def _q(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else None


def _args(runner):
    return argparse.Namespace(**(runner.SHARED_DEFAULTS | {
        "operator": OPERATORE, "baseline_fingerprint": IMPRONTA_CERTIFICATA,
        "baseline_artifact": "reports/p26_baseline_TEST_20260905T170601Z.json"}))


def _impronte(conn) -> dict[str, tuple[int, str]]:
    """Per ogni tabella dello schema public: (righe, md5 di tutte le righe in ordine)."""
    tabelle = [r[0] for r in _q(conn, "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1")]
    out = {}
    for t in tabelle:
        n, h = _q(conn, f'SELECT count(*), md5(COALESCE(string_agg(x::text, \'|\' ORDER BY x::text), \'\')) '
                        f'FROM "{t}" x')[0]
        out[t] = (n, h)
    return out


@pytest.fixture(scope="module")
def db133():
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
        for p in _pre_baseline():
            _q(conn, p.read_text(encoding="utf-8"))
        runner = _runner()
        _env_runner(mp, dsn, NOME_DB)
        for massimo in (26, 132):                                            # baseline, poi fino a TEST di oggi
            mp.setattr(runner, "MIGRATIONS_DIR", _cartella_fino_a(massimo))
            assert runner.command_apply(_args(runner)) == 0, massimo
        yield {"conn": conn, "dsn": dsn, "psycopg2": psycopg2, "runner": runner, "mp": mp}
    finally:
        mp.undo()
        conn.close()
        with servizio.cursor() as cur:
            cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = %s AND pid <> pg_backend_pid()", (NOME_DB,))
            cur.execute(f'DROP DATABASE IF EXISTS "{NOME_DB}"')
        servizio.close()


def _popola(conn) -> dict:
    """Due agenzie, 5.000 contatti, lead, stime collegate, immobili e proprietari, pause."""
    agenzie = [r[0] for r in _q(conn, "SELECT id FROM agencies ORDER BY id")]
    while len(agenzie) < 2:
        agenzie.append(_q(conn, "INSERT INTO agencies (name, slug) VALUES (%s, %s) RETURNING id",
                          (f"Agenzia {len(agenzie) + 1}", f"agenzia-133-{len(agenzie) + 1}"))[0][0])
    a, b = agenzie[:2]
    for agenzia, n in ((a, 4000), (b, 1000)):
        _q(conn, "INSERT INTO contacts (agency_id, display_name, first_name, last_name) "
                 "SELECT %s, 'Dati ' || g, 'Dati', 'N' || g FROM generate_series(1, %s) g", (agenzia, n))
    for agenzia in (a, b):
        _q(conn, """INSERT INTO leads (agency_id, contact_id, pipeline, status, stage)
                    SELECT agency_id, id, 'sell', 'open', 'new' FROM contacts WHERE agency_id = %s
                     ORDER BY id LIMIT 300""", (agenzia,))
        _q(conn, """INSERT INTO properties (agency_id, title, property_type, city)
                    SELECT %s, 'Dati ' || g, 'apartment', 'Datopoli' FROM generate_series(1, 200) g""", (agenzia,))
        _q(conn, """INSERT INTO property_contacts (property_id, contact_id, role)
                    SELECT p.id, c.id, 'owner' FROM
                      (SELECT id, row_number() OVER (ORDER BY id) r FROM properties WHERE agency_id = %s) p
                      JOIN (SELECT id, row_number() OVER (ORDER BY id DESC) r FROM contacts WHERE agency_id = %s) c
                        ON c.r = p.r""", (agenzia, agenzia))
        for _ in range(20):
            sid = _q(conn, "INSERT INTO stime (agency_id, nome, cognome, email, telefono) VALUES (%s, 'D', 'D', "
                           "'d@x.test', '3330000000') RETURNING id", (agenzia,))[0][0]
            _q(conn, "INSERT INTO lead_stime (lead_id, stima_id) SELECT id, %s FROM leads WHERE agency_id = %s "
                     "AND NOT EXISTS (SELECT 1 FROM lead_stime ls WHERE ls.lead_id = leads.id) ORDER BY id LIMIT 1",
                     (sid, agenzia))
    return {"a": a, "b": b}


def test_d1_d2_d3_la_133_sui_dati_esistenti(db133):
    conn, runner, mp = db133["conn"], db133["runner"], db133["mp"]
    agenzie = _popola(conn)
    assert _q(conn, "SELECT count(*) FROM contacts")[0][0] >= 5000
    prima = _impronte(conn)
    funzione_prima = _q(conn, "SELECT pg_get_functiondef('communication_contact_stop_fence'::regproc)")[0][0]
    assert "OR EXISTS(SELECT 1 FROM leads l LEFT JOIN lead_stime ls" in funzione_prima         # la 097

    # D1: il piano del runner sul repository di oggi contiene SOLO la 133
    mp.setattr(runner, "MIGRATIONS_DIR", MIGRAZIONI)
    lettura = runner.connect(NOME_DB)
    try:
        cur = lettura.cursor()
        cur.execute("SET TRANSACTION READ ONLY")
        _ledger, baseline, righe = runner.read_state(cur)
        lettura.rollback()
    finally:
        lettura.close()
    piano = runner.build_plan(runner.discover_migrations(MIGRAZIONI), righe, baseline)
    assert piano.problems == [] and [m.version for m in piano.to_apply] == ["133_contact_stop_fence_indexed"], \
        (piano.problems, [m.version for m in piano.to_apply])
    assert runner.command_apply(_args(runner)) == 0
    assert _q(conn, "SELECT count(*) FROM schema_migrations WHERE version = '133_contact_stop_fence_indexed'")[0][0] == 1
    assert runner.command_apply(_args(runner)) == 0                     # una seconda volta: nulla da fare

    # D2: nessun dato cambiato, in nessuna tabella (solo il registro delle migration ha una riga in piu')
    dopo = _impronte(conn)
    cambiate = sorted(t for t in set(prima) | set(dopo) if prima.get(t) != dopo.get(t))
    assert cambiate == ["schema_migrations"], cambiate
    assert dopo["schema_migrations"][0] == prima["schema_migrations"][0] + 1
    assert "communication_contact_stop_fence_targets" in \
        _q(conn, "SELECT pg_get_functiondef('communication_contact_stop_fence'::regproc)")[0][0]
    assert _q(conn, "SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal AND tgname LIKE 'trg_t6_%%_fence' "
                    "AND tgfoid = 'communication_contact_stop_fence'::regproc")[0][0] == 9

    # D3: sui dati esistenti, l'insieme e' quello della 097 per ogni lead, stima e immobile reali
    righe = _q(conn, "SELECT row_to_json(l)::text FROM leads l ORDER BY id LIMIT 60") + \
        _q(conn, "SELECT row_to_json(ls)::text FROM lead_stime ls ORDER BY id LIMIT 30") + \
        _q(conn, "SELECT row_to_json(pc)::text FROM property_contacts pc ORDER BY id LIMIT 60")
    with conn.cursor() as cur:
        for (riga,) in righe:
            parametri = {"data": riga, "old": json.dumps({})}
            cur.execute(RIFERIMENTO, parametri)
            atteso = cur.fetchall()
            cur.execute(NUOVA, parametri)
            assert cur.fetchall() == atteso and atteso, riga
    # ... e blocca davvero: un lead del contatto tenuto aspetta, uno di un altro contatto passa
    psycopg2 = db133["psycopg2"]
    (lead1, contatto1), (lead2, _c2) = _q(conn, "SELECT id, contact_id FROM leads WHERE agency_id = %s "
                                                "ORDER BY id LIMIT 2", (agenzie["a"],))
    blocco, scrittore = psycopg2.connect(db133["dsn"]), psycopg2.connect(db133["dsn"])
    try:
        with blocco.cursor() as cur:
            cur.execute("SELECT id FROM contacts WHERE id = %s FOR UPDATE", (contatto1,))
        with scrittore.cursor() as cur:
            cur.execute("SET lock_timeout = '300ms'")
            with pytest.raises(psycopg2.errors.LockNotAvailable):
                cur.execute("UPDATE leads SET stage = stage WHERE id = %s", (lead1,))
        scrittore.rollback()
        with scrittore.cursor() as cur:
            cur.execute("UPDATE leads SET stage = stage WHERE id = %s", (lead2,))
        scrittore.rollback()
    finally:
        blocco.rollback()
        blocco.close()
        scrittore.close()
    assert _impronte(conn) == dopo                                    # le prove non hanno lasciato tracce
