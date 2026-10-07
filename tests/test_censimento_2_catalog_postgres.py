"""CENSIMENTO-1 Fase 2 - CATALOGHI su PostgreSQL VERO, schema COMPLETO.

Lo schema e' ricostruito da zero col metodo gia' verificato in Fase 1
(`tests/test_censimento_1_fullschema_postgres.py`): tabelle legacy con le
funzioni vere di `database.py`, 001..025 come sul TEST, poi 026 e 027..083
eseguite dal runner vero (`scripts/p26_migrate.py`), fixture storica
inserita prima della 081. Tutto nella fixture di QUESTO modulo: nessun test
qui dipende dall'esecuzione di un altro test o di un altro modulo.

Cosa si prova, sul service e sul database reali:
  * `storage` nasce, si legge, si modifica e resta isolato per agenzia come
    ogni altra tipologia; lo storico non cambia;
  * ogni voce del catalogo catastale passa il CHECK di formato e la
    normalizzazione della 083, e la divisione dei compiti e' quella del
    progetto: il DB giudica il FORMATO, il service l'appartenenza al catalogo;
  * form-options letto con un contesto reale porta catalogo e suggerimenti.

Opt-in `P29_TEST_DSN`, SOLO database locale. Il database si chiama
`stima360_db_test` (lo pretendono 010/011): il modulo si ferma se ne esiste
gia' uno sul cluster.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from property import catalog, enums
from property import service as property_service
from property.schemas import PropertyCreate, PropertyUpdate
from tests.test_censimento_1_fullschema_postgres import (
    DSN, IMPRONTA_CERTIFICATA, MIGRAZIONI, NOME_DB, VERSIONE, _cartella_fino_a, _dsn_locale,
    _dsn_per, _env_runner, _fixture, _impronte, _pre_baseline, _runner,
)
from tests.test_crm_ops_2_property_form import _ctx

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare CENSIMENTO-1 Fase 2")

ROOT = Path(__file__).resolve().parents[1]
OPERATORE = "censimento.fase2"


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
        # 026 -> 080 -> fixture storica -> 082 -> 083, tutte dal runner vero
        runner = _runner()
        _env_runner(mp, dsn, NOME_DB)
        args = argparse.Namespace(**(runner.SHARED_DEFAULTS | {
            "operator": OPERATORE, "baseline_fingerprint": IMPRONTA_CERTIFICATA,
            "baseline_artifact": "reports/p26_baseline_TEST_20260905T170601Z.json"}))
        for massimo in (26, 80):
            mp.setattr(runner, "MIGRATIONS_DIR", _cartella_fino_a(massimo))
            assert runner.command_apply(args) == 0
        _fixture(c)
        mp.setattr(runner, "MIGRATIONS_DIR", _cartella_fino_a(82))
        assert runner.command_apply(args) == 0
        mp.setattr(runner, "MIGRATIONS_DIR", MIGRAZIONI)
        assert runner.command_apply(args) == 0
        # il service parla con QUESTO database
        from core import database as core_database
        mp.setattr(core_database, "get_connection", lambda: psycopg2.connect(dsn))
        c["impronte_storiche"] = _impronte(c)
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


def _errore(c, sql, params=None):
    """pgcode dell'errore, o None se il comando passa (autocommit: ogni
    comando e' la sua transazione)."""
    try:
        _q(c, sql, params)
    except c["psycopg2"].Error as exc:
        return exc.pgcode
    return None


def test_01_lo_schema_completo_viene_dal_runner_fino_alla_083(completo):
    # SENTINELLA AGGIORNATA DA DELETE-ARCH FASE 1A: il runner applica la cartella
    # reale, che ora arriva alla 084: 57 -> 58 righe di ledger.
    # SENTINELLA AGGIORNATA DA DELETE-ARCH FASE 2B1: la cartella reale arriva
    # ora alla 085 (Cestino Immobili): 58 -> 59 righe di ledger.
    # SENTINELLA AGGIORNATA DA DELETE-ARCH FASE 2B2: la cartella arriva alla 086: 59 -> 60 righe di ledger.
    # SENTINELLA AGGIORNATA DA CATALOGO-CANONICO-1: la cartella arriva alla 087: 60 -> 61 righe di ledger.
    # SENTINELLA AGGIORNATA DA CATALOGO-CANONICO-1: la cartella arriva alla 088: 61 -> 62 righe di ledger.
    # SENTINELLA AGGIORNATA DA PERTINENZE-1: la cartella arriva alla 089: 62 -> 63 righe di ledger.
    # SENTINELLA AGGIORNATA DA CESTINO-CONTATTI-1: la cartella arriva alla 090: 63 -> 64 righe di ledger.
    # SENTINELLA AGGIORNATA DA CESTINO-EDIFICI-1: la cartella arriva alla 091: 64 -> 65 righe di ledger.
    # SENTINELLA AGGIORNATA DA CESTINO-RICHIESTE-1: la cartella arriva alla 092: 65 -> 66 righe di ledger.
    assert _q(completo, "SELECT count(*) FROM schema_migrations WHERE rolled_back_at IS NULL")[0][0] == 66
    assert _q(completo, "SELECT count(*) FROM schema_migrations WHERE version = %s AND rolled_back_at IS NULL",
              (VERSIONE,))[0][0] == 1
    assert "~ '^[A-F]/[0-9]{1,2}$'" in _q(completo, "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                                               "WHERE conname = 'properties_cadastral_category_chk'")[0][0]
    assert len(completo["impronte_storiche"]) == 25


def test_02_storage_nasce_si_modifica_e_resta_isolato_per_agenzia(completo):
    from core.exceptions import NotFoundError, ValidationError
    owner_a, owner_b = _ctx("agency_owner", 1, agency_id=1), _ctx("agency_owner", 2, agency_id=2)
    creato = property_service.create_property(owner_a, PropertyCreate(
        property_type="storage", region="Marche", province="FM", city="Fermo", address="Via Mare", civic_number="3"))
    pid = creato["id"]
    riga = _q(completo, "SELECT property_type, title, code, record_kind, commercial_status, agency_id, "
                        "cadastral_category FROM properties WHERE id = %s", (pid,))[0]
    assert list(riga) == ["storage", "Cantina / Deposito · Fermo · Via Mare 3", f"IMM-{pid}", "crm", "draft", 1, None]
    # letto dal service, con il contesto dell'agenzia giusta; invisibile all'altra
    assert property_service.get_property(owner_a, pid)["property_type"] == "storage"
    with pytest.raises(NotFoundError):
        property_service.get_property(owner_b, pid)
    # un appartamento storico diventa cantina: tipologia e descrizione seguono
    storico = _q(completo, "SELECT id FROM properties WHERE property_type = 'apartment' "
                           "AND commercial_status = 'draft' ORDER BY id LIMIT 1")[0][0]
    property_service.update_property(owner_a, storico, PropertyUpdate(property_type="storage"))
    assert _q(completo, "SELECT property_type FROM properties WHERE id = %s", (storico,))[0][0] == "storage"
    property_service.update_property(owner_a, storico, PropertyUpdate(property_type="apartment"))
    # una tipologia fuori enum non arriva al database
    with pytest.raises((ValidationError, ValueError)):
        property_service.create_property(owner_a, PropertyCreate(property_type="cantina"))
    # lo storico e' intatto (titolo compreso: senza campi sorgente cambiati non si rigenera)
    impronte = _impronte(completo)
    for k, v in completo["impronte_storiche"].items():
        if k != storico:
            assert impronte[k] == v, k
    assert _q(completo, "SELECT title, property_type FROM properties WHERE id = %s", (storico,))[0][0] == \
        f"Immobile {storico}"


def test_03_ogni_voce_del_catalogo_passa_il_check_e_la_normalizzazione_della_083(completo):
    pid = _q(completo, "SELECT id FROM properties WHERE record_kind = 'crm' ORDER BY id LIMIT 1")[0][0]
    for voce in catalog.CADASTRAL_CATEGORIES:
        assert _errore(completo, "UPDATE properties SET cadastral_category = %s WHERE id = %s",
                       (voce["code"], pid)) is None, voce["code"]
        assert _q(completo, "SELECT cadastral_category FROM properties WHERE id = %s", (pid,))[0][0] == voce["code"]
    # il DB normalizza come il service (maiuscolo, spazi attorno tolti)
    assert _errore(completo, "UPDATE properties SET cadastral_category = %s WHERE id = %s", (" a/3 ", pid)) is None
    assert _q(completo, "SELECT cadastral_category FROM properties WHERE id = %s", (pid,))[0][0] == \
        catalog.validate_cadastral_category(" a/3 ") == "A/3"
    # divisione dei compiti: formato al DB, catalogo al service
    assert _errore(completo, "UPDATE properties SET cadastral_category = 'X/1' WHERE id = %s", (pid,)) == "23514"
    assert _errore(completo, "UPDATE properties SET cadastral_category = 'A/99' WHERE id = %s", (pid,)) is None
    with pytest.raises(ValueError):
        catalog.validate_cadastral_category("A/99")
    _q(completo, "UPDATE properties SET cadastral_category = NULL WHERE id = %s", (pid,))
    assert _impronte(completo)[pid] == completo["impronte_storiche"][pid]   # le colonne storiche non sono cambiate


def test_04_form_options_sul_database_reale(completo):
    opzioni = property_service.form_options(_ctx("agency_owner", 1, agency_id=1))
    assert opzioni["can_assign"] is True and opzioni["agents"] == []
    assert {t["value"] for t in opzioni["property_types"]} == enums.PROPERTY_TYPES
    assert len(opzioni["cadastral_categories"]) == 52
    assert set(opzioni["cadastral_suggestions"]) == enums.PROPERTY_TYPES
