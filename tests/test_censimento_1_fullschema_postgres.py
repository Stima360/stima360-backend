"""CENSIMENTO-1 / migration 083 sullo SCHEMA PRECEDENTE COMPLETO E REALE.

Il banco di `test_censimento_1_schema_postgres.py` ricostruisce le sole tabelle
che la 083 tocca. Qui invece si rifa' da zero, su un database locale
usa-e-getta, la procedura con cui il TEST e' nato davvero:

  1. le tabelle legacy del sito con le funzioni di `database.py`
     (`init_db.py`: stime, stime_dettagliate, zone_valori, allinea_stime);
  2. le migration pre-baseline 001..025 cosi' come sono state applicate sul
     TEST: in sequenza, ciascuna col proprio BEGIN/COMMIT, SENZA le varianti
     `*_prod` (014/015: controllano `current_database() = 'stima360_db'` e sul
     TEST non sono mai esistite);
  3. la 026 (baseline) e poi 027..082 ESEGUITE dal runner vero
     (`scripts/p26_migrate.py`), una per una, nell'ordine in cui il TEST le ha
     ricevute: prima con la cartella migration ferma alla 026, poi alla 082;
  4. la 083, sempre dal runner, sulla cartella reale.

Il database si DEVE chiamare `stima360_db_test`: le migration 010 e 011 lo
pretendono per nome (e il runner accetta solo nomi con 'test'). Il modulo lo
crea e lo distrugge sul cluster locale indicato da P29_TEST_DSN; se un
database con quel nome esiste gia' su quel cluster il modulo si FERMA (skip)
invece di toccarlo. Nessuna migration viene "registrata" al posto di
eseguirla.

Prima della 026 lo schema ottenuto viene confrontato con il certificato del
baseline TEST (`reports/p26_baseline_TEST_20260905T170601Z.json`): stesse
tabelle, stesse colonne. Cosi' la prova dice davvero "la 083 si applica sullo
schema che il TEST ha oggi, con tutti i suoi vincoli e trigger" - inclusa la
FK e il trigger della 081 sugli incarichi, assenti dal banco ridotto.

Gira solo con P29_TEST_DSN locale. Cluster usato per la certificazione:
PostgreSQL 16 (TEST e' 18); nessuna sintassi delle migration e' specifica di
versione.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import shutil
import sys
import tempfile
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from tests.test_crm_ops_3_acquisitions_postgres import _dsn_locale

DSN = os.getenv("P29_TEST_DSN")
pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare CENSIMENTO-1")

ROOT = Path(__file__).resolve().parents[1]
MIGRAZIONI = ROOT / "migrations"
NOME_DB = "stima360_db_test"            # imposto da 010/011; il runner lo accetta
VERSIONE = "083_censimento_1_buildings_units"
OPERATORE = "censimento.fullschema"
CERTIFICATO = ROOT / "reports" / "p26_baseline_TEST_20260905T170601Z.json"
IMPRONTA_CERTIFICATA = "669c18de" + "0" * 51 + "028c1"   # solo la forma: 64 esadecimali


def _dsn_per(nome):
    if "?" in DSN:
        base, query = DSN.split("?", 1)
        return base.rsplit("/", 1)[0] + "/" + nome + "?" + query
    return DSN.rsplit("/", 1)[0] + "/" + nome


def _runner():
    if str(ROOT / "scripts") not in sys.path:
        sys.path.insert(0, str(ROOT / "scripts"))
    import p26_migrate
    return p26_migrate


def _cartella_fino_a(massimo):
    """Una copia della cartella migration ferma alla versione `massimo` (up e
    down): e' cosi' che il runner vedeva il repository quando il TEST ha
    ricevuto quelle migration."""
    d = Path(tempfile.mkdtemp(prefix="censimento_mig_"))
    for p in MIGRAZIONI.glob("*.sql"):
        m = re.match(r"^(\d{3})_", p.name)
        if m and int(m.group(1)) <= massimo:
            shutil.copy(p, d / p.name)
    return d


def _pre_baseline():
    """001..025 nell'ordine del TEST: niente varianti _prod."""
    out = []
    for p in sorted(MIGRAZIONI.glob("*.sql")):
        m = re.match(r"^(\d{3})_", p.name)
        if not m or int(m.group(1)) > 25 or p.name.endswith("_down.sql") or "_prod" in p.name:
            continue
        out.append(p)
    return out


def _env_runner(monkeypatch, dsn, nome):
    p = urlparse(dsn)
    host = p.hostname or (parse_qs(p.query).get("host") or [""])[0]
    monkeypatch.setenv("DB_HOST", host)
    monkeypatch.setenv("DB_PORT", str(p.port or 5432))
    monkeypatch.setenv("DB_USER", p.username or getpass.getuser())
    monkeypatch.setenv("DB_PASSWORD", p.password or "")
    monkeypatch.setenv("DB_NAME", nome)


@pytest.fixture(scope="module")
def completo():
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
    try:
        # 1. tabelle legacy del sito, con le funzioni vere di database.py
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
        # 2. 001..025 come sul TEST
        with conn.cursor() as cur:
            for p in _pre_baseline():
                cur.execute(p.read_text(encoding="utf-8"))
        yield {"conn": conn, "dsn": dsn, "nome": NOME_DB, "psycopg2": psycopg2}
    finally:
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


def _errore(c, sql, params=None):
    psycopg2 = c["psycopg2"]
    conn = psycopg2.connect(c["dsn"])
    try:
        with conn.cursor() as cur:
            try:
                cur.execute(sql, params)
                conn.commit()
                return None
            except psycopg2.Error as exc:
                conn.rollback()
                return str(exc)
    finally:
        conn.close()


def _schema(c):
    tabelle = {r[0] for r in _q(c, "SELECT table_name FROM information_schema.tables "
                                   "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'")}
    colonne = {(r[0], r[1]) for r in _q(c, "SELECT table_name, column_name FROM information_schema.columns "
                                           "WHERE table_schema = 'public'")}
    return tabelle, colonne


def test_01_lo_schema_pre_baseline_e_quello_certificato_sul_test(completo):
    cert = json.loads(CERTIFICATO.read_text(encoding="utf-8"))["schema"]
    tabelle_cert = {r["table_name"] for r in cert["columns"]}
    colonne_cert = {(r["table_name"], r["column_name"]) for r in cert["columns"]}
    tabelle, colonne = _schema(completo)
    assert tabelle == tabelle_cert, (sorted(tabelle - tabelle_cert), sorted(tabelle_cert - tabelle))
    assert colonne == colonne_cert, (sorted(colonne - colonne_cert)[:10], sorted(colonne_cert - colonne)[:10])
    assert len(tabelle) == 60 and len(colonne) == 764


def test_02_il_runner_applica_026_027_082_e_poi_la_083(completo, monkeypatch, capsys):
    runner = _runner()
    _env_runner(monkeypatch, completo["dsn"], completo["nome"])
    args = argparse.Namespace(**(runner.SHARED_DEFAULTS | {
        "operator": OPERATORE, "baseline_fingerprint": IMPRONTA_CERTIFICATA,
        "baseline_artifact": "reports/p26_baseline_TEST_20260905T170601Z.json"}))
    # la cartella come la vedeva il runner quando il TEST ha ricevuto la 026...
    monkeypatch.setattr(runner, "MIGRATIONS_DIR", _cartella_fino_a(26))
    assert runner.command_apply(args) == 0
    assert "applied 026_p26_baseline" in capsys.readouterr().out
    # ...poi fino alla 080, una per una, eseguite davvero
    monkeypatch.setattr(runner, "MIGRATIONS_DIR", _cartella_fino_a(80))
    assert runner.command_apply(args) == 0
    assert capsys.readouterr().out.count("applied ") == 54
    # dati rappresentativi inseriti QUI, come lo storico vero: prima della 081,
    # cosi' gli incarichi storici esistono senza acquisizione (grandfathering
    # della 081) e la 081/082 li trovano gia' in tabella
    _fixture(completo)
    monkeypatch.setattr(runner, "MIGRATIONS_DIR", _cartella_fino_a(82))
    assert runner.command_apply(args) == 0
    uscita = capsys.readouterr().out
    assert uscita.count("applied ") == 2 and "applied 082_crm_ops_4_property_interactions" in uscita
    assert _q(completo, "SELECT count(*) FROM schema_migrations WHERE rolled_back_at IS NULL")[0][0] == 56
    # lo stato "TEST di oggi": una fotografia prima della 083
    tabelle, colonne = _schema(completo)
    assert "acquisitions" in tabelle and ("activities", "property_id") in colonne
    assert _q(completo, "SELECT count(*) FROM pg_constraint WHERE conrelid = 'properties'::regclass AND contype = 'f' "
                        "AND pg_get_constraintdef(oid) LIKE '%acquisition_id%'")[0][0] == 1
    trigger_prima = {r[0] for r in _q(completo, "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal")}
    impronte_prima = _impronte(completo)
    assert len(impronte_prima) == 25 and _q(completo, "SELECT count(*) FROM properties WHERE mandate_type IS NOT NULL")[0][0] == 12
    # 4. la 083 dal runner sulla cartella reale
    # SENTINELLA AGGIORNATA DA DELETE-ARCH FASE 1A: la cartella reale ha ora
    # anche la 084; questo test certifica la 083 sul TEST di allora, quindi
    # la cartella si ferma alla 083 (stesso idioma dei passi 1-3).
    monkeypatch.setattr(runner, "MIGRATIONS_DIR", _cartella_fino_a(83))
    assert runner.command_status(args) == 0
    stato = capsys.readouterr().out
    assert f"pending      {VERSIONE}" in stato and stato.count("pending") == 1 and "PROBLEM" not in stato
    assert runner.command_apply(args) == 0
    uscita = capsys.readouterr().out
    assert uscita.count("applied ") == 1 and f"applied {VERSIONE}" in uscita
    assert _impronte(completo) == impronte_prima
    tabelle_dopo, colonne_dopo = _schema(completo)
    assert tabelle_dopo - tabelle == {"buildings", "property_accessories"}
    assert {c for t, c in colonne_dopo - colonne if t == "properties"} == {
        "building_id", "parent_property_id", "whole_building", "staircase", "internal_number",
        "cadastral_municipality_code", "cadastral_section", "cadastral_sheet", "cadastral_parcel",
        "cadastral_subunit", "cadastral_category", "record_kind", "address_inherited",
        "client_request_id", "client_request_fingerprint"}
    trigger_dopo = {r[0] for r in _q(completo, "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal")}
    assert trigger_prima <= trigger_dopo          # nessun trigger esistente e' sparito
    assert len(trigger_dopo - trigger_prima) == 8


COLONNE_STORICHE = (
    "id", "code", "title", "property_type", "commercial_status", "address", "city", "surface_sqm",
    "mandate_type", "mandate_start", "mandate_end", "assigned_to", "metadata", "created_at",
    "updated_at", "archived_at", "agency_id", "region", "assigned_agent_id", "acquisition_id",
)


def _fixture(c):
    _q(c, "INSERT INTO agencies (id, slug, name) VALUES (1, 'agenzia-uno', 'Agenzia Uno'), (2, 'agenzia-due', 'Agenzia Due') "
          "ON CONFLICT (id) DO NOTHING")
    for i in range(1, 26):
        stato = (["sold"] * 9 + ["archived"] * 8 + ["draft"] * 3 + ["under_offer"] * 2 + ["active"] * 2 + ["mandate"])[i - 1]
        _q(c, "INSERT INTO properties (code, title, property_type, commercial_status, agency_id, mandate_type, mandate_start, archived_at) "
              "VALUES (%s, %s, %s, %s, 1, %s, %s, CASE WHEN %s = 'archived' THEN NOW() END)",
           ("IMM-1" if i == 1 else (None if i <= 5 else f"ST-{i:03d}"), f"Immobile {i}",
            "villa" if i > 23 else "apartment", stato,
            "esclusiva" if i <= 12 else None, "2026-01-01" if i <= 12 else None, stato))


def _impronte(c):
    cols = ", ".join(COLONNE_STORICHE)
    return dict(_q(c, f"SELECT id, md5(row_to_json(t)::text) FROM (SELECT {cols} FROM properties ORDER BY id) t"))


def test_03_le_guardie_della_083_convivono_con_quelle_della_081(completo):
    """Sul banco ridotto la FK e il trigger della 081 non c'erano. Qui ci sono:
    il trigger della 083 scatta prima (ordine alfabetico), quello della 081
    continua a proteggere le righe commerciali."""
    i = _q(completo, "INSERT INTO properties (title, agency_id, record_kind) VALUES ('censita', 1, 'census') RETURNING id")[0][0]
    # census + incarico: rifiuto della 083
    assert "take it in charge" in _errore(completo, "UPDATE properties SET mandate_type = 'esclusiva', mandate_start = CURRENT_DATE WHERE id = %s", (i,))
    # crm + incarico NUOVO senza acquisizione: rifiuto della 081 (invariato)
    j = _q(completo, "INSERT INTO properties (title, agency_id) VALUES ('commerciale', 1) RETURNING id")[0][0]
    err = _errore(completo, "UPDATE properties SET mandate_type = 'esclusiva', mandate_start = CURRENT_DATE WHERE id = %s", (j,))
    assert err and "acquisi" in err.lower()
    # acquisition_id inventato: rifiuto della FK della 081, anche su una riga crm
    err = _errore(completo, "UPDATE properties SET acquisition_id = 999 WHERE id = %s", (j,))
    assert err and ("foreign key" in err.lower() or "acquisi" in err.lower())
    # lo storico con incarico resta modificabile negli altri campi
    assert _errore(completo, "UPDATE properties SET title = 'rinominato' WHERE id = 1") is None


def test_04_i_vincoli_della_083_sullo_schema_completo(completo):
    e = _q(completo, "INSERT INTO buildings (agency_id, city) VALUES (1, 'Alba Adriatica') RETURNING id")[0][0]
    a = _q(completo, "INSERT INTO properties (title, agency_id, record_kind, building_id) VALUES ('A', 1, 'census', %s) RETURNING id", (e,))[0][0]
    g = _q(completo, "INSERT INTO properties (title, agency_id, record_kind, property_type, parent_property_id) VALUES ('G', 1, 'census', 'garage', %s) RETURNING id", (a,))[0][0]
    assert "has linked properties and cannot change agency" in _errore(completo, "UPDATE buildings SET agency_id = 2 WHERE id = %s", (e,))
    # A e' collegata a un edificio (rifiuto per l'edificio) E ha una pertinenza: tolto l'edificio, resta il rifiuto per la pertinenza
    assert "tenancy" in _errore(completo, "UPDATE properties SET agency_id = 2 WHERE id = %s", (a,))
    assert "has linked pertinenze and cannot change agency" in _errore(completo, "UPDATE properties SET agency_id = 2, building_id = NULL WHERE id = %s", (a,))
    assert "cannot be a parent" in _errore(completo, "INSERT INTO properties (title, agency_id, parent_property_id) VALUES ('X', 1, %s)", (g,))
    assert "cannot change its commercial fields in the same statement" in _errore(
        completo, "UPDATE properties SET record_kind = 'crm', commercial_status = 'active' WHERE id = %s", (a,))
    # MATCH/acquisizioni leggono properties: una riga census resta draft e invisibile a quei filtri
    assert _q(completo, "SELECT commercial_status, record_kind FROM properties WHERE id = %s", (a,))[0] == ["draft", "census"]


def test_05_la_down_sullo_schema_completo(completo):
    giu = (MIGRAZIONI / f"{VERSIONE}_down.sql").read_text(encoding="utf-8")
    err = _errore(completo, giu)
    assert err and "would be lost. Nothing has been changed" in err
    _q(completo, "DELETE FROM properties WHERE record_kind = 'census' OR parent_property_id IS NOT NULL")
    _q(completo, "DELETE FROM buildings")
    prima = _impronte(completo)
    assert _errore(completo, giu) is None
    tabelle, colonne = _schema(completo)
    assert "buildings" not in tabelle and ("properties", "record_kind") not in colonne
    assert _impronte(completo) == prima
    # e si riapplica: la 083 e' idempotente anche dopo una down
    assert _errore(completo, (MIGRAZIONI / f"{VERSIONE}.sql").read_text(encoding="utf-8")) is None
