"""A30-2P - lo script di certificazione LIVE della facade, provato SOLO su
PostgreSQL usa-e-getta.

Opt-in: senza `P29_TEST_DSN` si salta tutto. Il database usa-e-getta porta
DAVVERO il nome `stima360_db_test` (lo crea `_database_chiamato` dell'A30-1,
che salta se un database con quel nome esiste gia' nel cluster di prova): e'
il solo modo di provare le guardie che leggono `current_database()` e
`a30_is_certified_test_database()`. Ci si applicano la 073 e le righe di
ledger di 072/073, come farebbe il runner.

Nessuna connessione propria: lo script si connette con il suo `connect()`
(`scripts.p26_migrate`), alimentato dalle variabili `DB_*` ricavate dal DSN
di prova. Si prova che:

  * la certificazione passa, ogni scenario gira, gli id creati esistono DENTRO
    la transazione e nessuno sopravvive al ROLLBACK;
  * le guardie falliscono chiuse PRIMA di ogni scrittura (nome, argomenti,
    agenzia, operatore, 073, ledger, righe `lmc15_facade`, Q9, Q10, lock);
  * un fallimento a meta' resta un rollback completo;
  * timeout e advisory lock sono attivi nella transazione;
  * nessuna connessione in piu' oltre alle due dichiarate.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import timedelta
from pathlib import Path

import pytest

from tests.test_a30_1_appointments_postgres import (  # noqa: F401
    DSN, _database_chiamato, _svuota)
from tests.test_a30_2p_backfill_postgres import Q10 as Q10_TEST

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare lo script")

ROOT = Path(__file__).resolve().parents[1]
MIGRAZIONI = ROOT / "migrations"
SCRIPT = ROOT / "scripts" / "a30_2p_facade_live_cert.py"
NOME = "stima360_db_test"
TABELLE = ("appointments", "appointment_events", "stima_inspections", "seller_timeline_events")
SCENARI = ["create_scheduled", "complete", "create_cancel_no_reason",
           "create_completed_posthoc", "f2_both_routes", "atomic_rollback", "q10_zero"]


# ---------------------------------------------------------------------------
# il TEST certificato usa-e-getta
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def cert_db():
    with _database_chiamato(NOME) as info:
        conn = info["conn"]
        with conn.cursor() as cur:
            cur.execute("BEGIN")
            cur.execute((MIGRAZIONI / "073_a30_2p_lmc15_facade.sql").read_text(encoding="utf-8"))
            cur.execute("COMMIT")
            for versione in ("072_a30_1_appointments", "073_a30_2p_lmc15_facade"):
                cur.execute("INSERT INTO schema_migrations (version, applied_at) "
                            "VALUES (%s, NOW())", (versione,))
            cur.execute("INSERT INTO stime (agency_id, comune, via, mq) "
                        "VALUES (%s,'Alba Adriatica','Via Trieste',95) RETURNING id",
                        (info["agency"],))
            stima = cur.fetchone()[0]
            cur.execute("INSERT INTO agencies (slug) VALUES ('altra') RETURNING id")
            altra = cur.fetchone()[0]
            cur.execute("INSERT INTO stime (agency_id, comune, via, mq) "
                        "VALUES (%s,'Teramo','Via Roma',80) RETURNING id", (altra,))
            stima_altrui = cur.fetchone()[0]
        yield {**info, "stima": stima, "altra": altra, "stima_altrui": stima_altrui}


@pytest.fixture
def ambiente(cert_db, monkeypatch):
    """Le variabili `DB_*` che `connect()` dello script legge, dal DSN di
    prova; il mondo ripulito prima e dopo ogni prova."""
    from psycopg2.extensions import parse_dsn

    parti = parse_dsn(cert_db["dsn"])
    monkeypatch.setenv("DB_NAME", NOME)
    monkeypatch.setenv("DB_HOST", parti.get("host", "localhost"))
    monkeypatch.setenv("DB_PORT", str(parti.get("port", "5432")))
    monkeypatch.setenv("DB_USER", parti.get("user", ""))
    monkeypatch.setenv("DB_PASSWORD", parti.get("password", ""))
    _pulisci(cert_db["conn"])
    yield cert_db
    _pulisci(cert_db["conn"])


def _pulisci(conn):
    conn.autocommit = True
    with conn.cursor() as cur:
        _svuota(cur)
        cur.execute("DELETE FROM seller_timeline_events")
        cur.execute("DELETE FROM stima_inspections")
        cur.execute("UPDATE schema_migrations SET rolled_back_at = NULL")
        cur.execute("UPDATE agency_memberships SET status = 'active'")
        cur.execute("UPDATE operator_users SET status = 'active'")


def _modulo():
    from scripts import a30_2p_facade_live_cert
    return a30_2p_facade_live_cert


def _apri():
    from scripts.p26_migrate import connect
    return connect(NOME)


def _certifica(d, **kw):
    args = {"agency_id": d["agency"], "operator_user_id": d["giorgio"], "stima_id": d["stima"]}
    args.update(kw)
    return _modulo().certify(_apri, NOME, **args)


def _fotografia(conn):
    with conn.cursor() as cur:
        esito = {}
        for t in TABELLE:
            cur.execute(f"SELECT coalesce(json_agg(to_jsonb(x) ORDER BY x.id), '[]') FROM {t} x")
            esito[t] = cur.fetchone()[0]
        return esito


def _uno(conn, sql, par=None):
    with conn.cursor() as cur:
        cur.execute(sql, par)
        return cur.fetchone()


def _normalizza(sql: str) -> str:
    senza_commenti = re.sub(r"--[^\n]*", "", sql)
    return " ".join(senza_commenti.split()).rstrip(";")


# ---------------------------------------------------------------------------
# la certificazione
# ---------------------------------------------------------------------------

def test_01_certifica_tutto_e_non_lascia_nulla(ambiente):
    d = ambiente
    prima = _fotografia(d["conn"])
    report = _certifica(d)
    assert report["in_transaction"] == "passed", report
    assert report["scenarios"] == SCENARI
    assert report["failed_scenario"] is None
    assert report["passed"] is True
    assert report["before"]["operator_role"] == "agency_owner"
    # gli id sono stati generati DENTRO la transazione, in tutte e 4 le tabelle
    creati = report["created_ids"]
    assert all(creati[t] for t in TABELLE), creati
    assert len(creati["stima_inspections"]) == 4          # s1, s3, s4, s5
    assert all(v == 0 for v in report["q10_in_transaction"].values())
    # ... e nessuno e' sopravvissuto
    dopo = report["after_rollback"]
    assert dopo["surviving_ids"] == {}
    assert dopo["lmc15_facade"] == 0 and dopo["q5"] == report["before"]["q5"]
    for q in ("q7", "q7b", "q9", "q10"):
        assert all(v == 0 for v in dopo[q].values()), (q, dopo[q])
    # verifica indipendente, da un'altra connessione: il mondo e' identico
    assert _fotografia(d["conn"]) == prima
    for t, ids in creati.items():
        assert _uno(d["conn"], f"SELECT count(*) FROM {t} WHERE id = ANY(%s)", (ids,))[0] == 0
    # gli hash sono solo informativi, e qui (nessuna attivita' concorrente) uguali
    assert report["hashes_before_informational"] == report["hashes_after_informational"]


def test_02_le_sequence_avanzano_e_non_si_ripristinano(ambiente):
    """Atteso e approvato: nessun setval, i buchi negli id restano."""
    d = ambiente
    primo = _certifica(d)
    secondo = _certifica(d)
    assert primo["passed"] and secondo["passed"]
    for t in TABELLE:
        assert min(secondo["created_ids"][t]) > max(primo["created_ids"][t]), t
    sorgente = SCRIPT.read_text(encoding="utf-8")
    assert "setval" not in sorgente.split('"""', 2)[2]


def test_03_main_end_to_end(ambiente, capsys):
    d = ambiente
    esito = _modulo().main(["--agency-id", str(d["agency"]), "--operator-user-id",
                            str(d["giorgio"]), "--stima-id", str(d["stima"])])
    uscita = capsys.readouterr().out
    assert esito == 0, uscita
    report = json.loads(uscita)
    assert report["passed"] is True and report["scenarios"] == SCENARI
    assert _uno(d["conn"], "SELECT count(*) FROM appointments")[0] == 0


def test_04_timeout_e_lock_attivi_dentro_la_transazione(ambiente, monkeypatch):
    d = ambiente
    modulo = _modulo()
    originale = modulo.run_scenarios
    visto = {}

    def spia(conn, **kw):
        with conn.cursor() as cur:
            cur.execute("SHOW lock_timeout")
            visto["lock_timeout"] = cur.fetchone()[0]
            cur.execute("SHOW statement_timeout")
            visto["statement_timeout"] = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
                        "AND pid = pg_backend_pid() AND granted")
            visto["advisory"] = cur.fetchone()[0]
        return originale(conn, **kw)

    monkeypatch.setattr(modulo, "run_scenarios", spia)
    assert _certifica(d)["passed"]
    assert visto == {"lock_timeout": "5s", "statement_timeout": "1min", "advisory": 1}
    # SET LOCAL: finiti con la transazione; il lock pure
    assert _uno(d["conn"], "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory'")[0] == 0


def test_05_solo_le_due_connessioni_dichiarate(ambiente, monkeypatch):
    """La transazione esterna e la verifica in sola lettura: `apri()` e'
    chiamata DUE volte e basta. Durante gli scenari il codice applicativo
    riceve solo l'involucro della transazione esterna (`core.database`), e la
    radice `database.get_connection` e' vietata; dopo, entrambe tornano come
    prima."""
    import database as radice
    from core import database as core_database

    d = ambiente
    modulo = _modulo()
    aperte, visto = [], {}
    originale = modulo.run_scenarios
    prima = (core_database.get_connection, radice.get_connection)

    def apri():
        aperte.append(1)
        return _apri()

    def spia(conn, **kw):
        involucro = core_database.get_connection()
        visto["involucro"] = type(involucro).__name__
        visto["stessa_connessione"] = involucro._conn is conn
        involucro.close()
        visto["radice_vietata"] = radice.get_connection is modulo._vietata
        return originale(conn, **kw)

    monkeypatch.setattr(modulo, "run_scenarios", spia)
    report = modulo.certify(apri, NOME, agency_id=d["agency"], operator_user_id=d["giorgio"],
                            stima_id=d["stima"])
    assert report["passed"]
    assert len(aperte) == 2
    assert visto == {"involucro": "_ConnessioneCondivisa", "stessa_connessione": True,
                     "radice_vietata": True}
    assert (core_database.get_connection, radice.get_connection) == prima


def _importata(d):
    """Una riga LMC-15 importata dal backfill (`requested`, agente NULL), come
    quella gia' presente sul Render TEST."""
    from psycopg2.extras import RealDictCursor

    from appointments import backfill, repository

    isp = _sopralluogo(d, con_appuntamento=False)
    with d["conn"].cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT i.*, s.agency_id FROM stima_inspections i "
                    "JOIN stime s ON s.id = i.stima_id WHERE i.id = %s", (isp["id"],))
        repository.insert_appointment(cur, backfill.map_inspection(dict(cur.fetchone()))["values"],
                                      actor_user_id=None)
    return isp


def test_06_con_una_riga_gia_importata_come_sul_test(ambiente):
    """Il mondo del Render TEST: una riga del backfill. La certificazione non
    la tocca e Q5/Q9/Q10 restano quelli di prima."""
    d = ambiente
    _importata(d)
    prima = _fotografia(d["conn"])
    report = _certifica(d)
    assert report["passed"] is True, report
    assert report["before"]["q5"] == report["after_rollback"]["q5"] == 0
    assert _fotografia(d["conn"]) == prima


# ---------------------------------------------------------------------------
# fallimenti a meta': sempre un rollback completo
# ---------------------------------------------------------------------------

def test_10_uno_scenario_fallito_e_comunque_tutto_annullato(ambiente, monkeypatch):
    from appointments import lmc15_facade

    d = ambiente
    prima = _fotografia(d["conn"])
    monkeypatch.setattr(lmc15_facade, "DURATA", timedelta(minutes=30))
    report = _certifica(d)
    assert report["passed"] is False
    assert report["failed_scenario"] == "create_scheduled"
    assert report["in_transaction"].startswith("FAILED: CertificationFailure")
    assert report["after_rollback"]["passed"] is True
    assert report["after_rollback"]["surviving_ids"] == {}
    assert _fotografia(d["conn"]) == prima


def test_11_un_errore_inatteso_e_riportato_e_annullato(ambiente, monkeypatch):
    from appointments import lmc15_facade

    d = ambiente
    prima = _fotografia(d["conn"])

    def rotto(*_a, **_k):
        raise KeyError("rotto")

    # s2 chiama la facade; il 500 diventa una CertificationFailure di s2
    monkeypatch.setattr(lmc15_facade, "_deve_essere_aperto", rotto)
    report = _certifica(d)
    assert report["passed"] is False and report["failed_scenario"] == "complete"
    assert report["scenarios"] == ["create_scheduled"]
    assert report["created_ids"]["stima_inspections"]      # s1 era stato scritto...
    assert report["after_rollback"]["surviving_ids"] == {}  # ...e non sopravvive
    assert _fotografia(d["conn"]) == prima


def test_12_main_esce_1_se_la_certificazione_fallisce(ambiente, monkeypatch, capsys):
    from appointments import lmc15_facade

    d = ambiente
    monkeypatch.setattr(lmc15_facade, "DURATA", timedelta(minutes=30))
    assert _modulo().main(["--agency-id", str(d["agency"]), "--operator-user-id",
                           str(d["giorgio"]), "--stima-id", str(d["stima"])]) == 1
    assert json.loads(capsys.readouterr().out)["passed"] is False


# ---------------------------------------------------------------------------
# guardie: falliscono chiuse PRIMA di ogni scrittura
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("nome", [None, "", "stima360", "stima360_db_test2", "STIMA360_DB_TEST",
                                  "stima360_db_tes"])
def test_20_nome_non_certificato_nessuna_connessione(ambiente, monkeypatch, capsys, nome):
    modulo = _modulo()
    if nome is None:
        monkeypatch.delenv("DB_NAME", raising=False)
    else:
        monkeypatch.setenv("DB_NAME", nome)

    def vietato(*_a, **_k):
        raise AssertionError("connect() chiamato nonostante il nome non certificato")

    monkeypatch.setattr(modulo, "connect", vietato)
    assert modulo.main(["--agency-id", "1", "--operator-user-id", "1", "--stima-id", "1"]) == 2
    assert "BLOCKED" in capsys.readouterr().err


@pytest.mark.parametrize("mancante", ["--agency-id", "--operator-user-id", "--stima-id"])
def test_21_nessuna_auto_selezione_gli_argomenti_sono_obbligatori(monkeypatch, mancante):
    modulo = _modulo()
    monkeypatch.setattr(modulo, "connect", lambda *_a: pytest.fail("connect() chiamato"))
    argv = {"--agency-id": "1", "--operator-user-id": "1", "--stima-id": "1"}
    argv.pop(mancante)
    with pytest.raises(SystemExit) as exc:
        modulo.main([x for kv in argv.items() for x in kv])
    assert exc.value.code == 2


def test_22_database_diverso_dal_nome_dichiarato(ambiente):
    from scripts.p26_migrate import GuardFailure

    d = ambiente
    with pytest.raises(GuardFailure, match="expected 'stima360_db_test2'"):
        _modulo().certify(_apri, "stima360_db_test2", agency_id=d["agency"],
                          operator_user_id=d["giorgio"], stima_id=d["stima"])


def _rifiutata(d, messaggio, **kw):
    from scripts.p26_migrate import GuardFailure

    prima = _fotografia(d["conn"])
    with pytest.raises(GuardFailure, match=messaggio):
        _certifica(d, **kw)
    assert _fotografia(d["conn"]) == prima


def test_23_stima_di_un_altra_agenzia(ambiente):
    _rifiutata(ambiente, "estimate does not belong", stima_id=ambiente["stima_altrui"])


def test_24_stima_inesistente(ambiente):
    _rifiutata(ambiente, "estimate does not belong", stima_id=10**9)


def test_25_operatore_non_membro(ambiente):
    d = ambiente
    _rifiutata(d, "not ACTIVE", agency_id=d["altra"], stima_id=d["stima_altrui"])


def test_26_operatore_o_membership_non_attivi(ambiente):
    d = ambiente
    with d["conn"].cursor() as cur:
        cur.execute("UPDATE agency_memberships SET status = 'suspended' "
                    "WHERE operator_user_id = %s", (d["luca"],))
        cur.execute("UPDATE operator_users SET status = 'disabled' WHERE id = %s",
                    (d["marta"],))
    _rifiutata(d, "not ACTIVE", operator_user_id=d["luca"])
    _rifiutata(d, "not ACTIVE", operator_user_id=d["marta"])


def test_27_ledger_072_o_073_ritirata(ambiente):
    d = ambiente
    for versione in ("072_a30_1_appointments", "073_a30_2p_lmc15_facade"):
        with d["conn"].cursor() as cur:
            cur.execute("UPDATE schema_migrations SET rolled_back_at = NOW() "
                        "WHERE version = %s", (versione,))
        _rifiutata(d, "not both applied")
        with d["conn"].cursor() as cur:
            cur.execute("UPDATE schema_migrations SET rolled_back_at = NULL")


def test_28_check_della_073_assente(ambiente):
    d = ambiente
    giu = (MIGRAZIONI / "073_a30_2p_lmc15_facade_down.sql").read_text(encoding="utf-8")
    su = (MIGRAZIONI / "073_a30_2p_lmc15_facade.sql").read_text(encoding="utf-8")
    with d["conn"].cursor() as cur:
        cur.execute("BEGIN")
        cur.execute(giu)
        cur.execute("COMMIT")
    try:
        # la down della 073 toglie anche la sua riga di ledger: fermata li'
        _rifiutata(d, "not both applied")
        # deriva: ledger "applicata" ma CHECK assente -> fermata sul catalogo
        with d["conn"].cursor() as cur:
            cur.execute("INSERT INTO schema_migrations (version, applied_at) "
                        "VALUES ('073_a30_2p_lmc15_facade', NOW())")
        _rifiutata(d, "appointments_lmc15_facade_chk is missing")
    finally:
        with d["conn"].cursor() as cur:
            cur.execute("BEGIN")
            cur.execute(su)
            cur.execute("INSERT INTO schema_migrations (version, applied_at) "
                        "VALUES ('073_a30_2p_lmc15_facade', NOW()) "
                        "ON CONFLICT (version) DO UPDATE SET rolled_back_at = NULL")
            cur.execute("COMMIT")


def _sopralluogo(d, *, con_appuntamento):
    """Una riga LMC-15 scritta come la scriverebbe la facade (o solo LMC-15)."""
    from psycopg2.extras import RealDictCursor

    from acquisition import repository as lmc15
    from appointments import lmc15_facade

    conn = d["conn"]
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT NOW() + INTERVAL '3 days' AS t")
        quando = cur.fetchone()["t"]
        isp = lmc15.create_inspection_in(cur, d["agency"], stima_id=d["stima"],
                                         scheduled_for=quando, actor_user_id=d["giorgio"])
        if con_appuntamento:
            lmc15_facade._nuova_riga(cur, isp, status="scheduled", start_at=quando,
                                     actor_user_id=d["giorgio"], agency_id=d["agency"])
    return isp


def test_29_esiste_gia_una_riga_lmc15_facade(ambiente):
    _sopralluogo(ambiente, con_appuntamento=True)
    _rifiutata(ambiente, "lmc15_facade appointment")


def test_30_q10_non_zero(ambiente):
    """Una riga LMC-15 non rappresentata nell'Agenda: Q10 != 0."""
    _sopralluogo(ambiente, con_appuntamento=False)
    _rifiutata(ambiente, "Q10 is not all 0")


def test_31_q9_non_zero(ambiente):
    """Una richiesta importata intatta, poi chiusa in LMC-15: Q9 != 0."""
    from psycopg2.extras import RealDictCursor

    from acquisition import repository as lmc15

    d = ambiente
    isp = _importata(d)
    with d["conn"].cursor(cursor_factory=RealDictCursor) as cur:
        # chiusa da LMC-15 "a monte" (come prima della facade): Q9 lo vede
        lmc15.cancel_inspection_in(cur, d["agency"], inspection_id=isp["id"], reason=None,
                                   actor_user_id=d["giorgio"])
    _rifiutata(d, "Q9 is not 0/0")


def test_32_una_seconda_corsa_concorrente_e_bloccata(ambiente):
    """L'advisory lock tenuto da un'altra transazione: rifiuto immediato, e
    `lock_timeout` non serve nemmeno (e' un try-lock)."""
    d = ambiente
    modulo = _modulo()
    altra = _apri()
    try:
        with altra.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                        (modulo.LOCK_KEY,))
        _rifiutata(d, "another facade live certification is running")
    finally:
        altra.rollback()
        altra.close()
    assert _certifica(d)["passed"]           # rilasciato il lock, si certifica


def test_33_due_corse_insieme_una_sola_passa(ambiente):
    """Due certificazioni davvero concorrenti: una entra, l'altra e' rifiutata
    dal lock. Deterministico: la prima si ferma dentro il lock finche' la
    seconda non ha finito."""
    from scripts.p26_migrate import GuardFailure

    d = ambiente
    modulo = _modulo()
    dentro, via = threading.Event(), threading.Event()
    originale = modulo.run_scenarios
    esiti = {}

    def lenta(conn, **kw):
        dentro.set()
        assert via.wait(30)
        return originale(conn, **kw)

    def prima_corsa():
        esiti["prima"] = _certifica(d)

    modulo.run_scenarios = lenta
    try:
        t = threading.Thread(target=prima_corsa)
        t.start()
        assert dentro.wait(30)
        modulo.run_scenarios = originale
        with pytest.raises(GuardFailure, match="another facade live certification"):
            _certifica(d)
        via.set()
        t.join(60)
    finally:
        modulo.run_scenarios = originale
        via.set()
    assert esiti["prima"]["passed"] is True


# ---------------------------------------------------------------------------
# struttura
# ---------------------------------------------------------------------------

def test_40_q10_e_la_stessa_query_del_census_e_dei_test():
    assert _normalizza(_modulo().Q10) == _normalizza(Q10_TEST)


def test_41_nessun_commit_sulla_transazione_esterna():
    """L'unico `commit` nello script e' quello neutralizzato dell'involucro;
    il `finally` fa sempre rollback."""
    import ast

    albero = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    chiamate = [n for n in ast.walk(albero) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute) and n.func.attr == "commit"]
    assert chiamate == []
    testo = SCRIPT.read_text(encoding="utf-8")
    assert not re.search(r"execute\([^)]*\bCOMMIT\b", testo, re.IGNORECASE)
    assert re.search(r"finally:\n(?:.*\n){0,5}\s+conn\.rollback\(\)", testo)
    assert not re.search(r"\.autocommit\s*=", testo)
