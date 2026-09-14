"""P28 - la migration 060 su un PostgreSQL VERO.

Stessa disciplina di `tests/test_p27_6_postgres_real.py`, e per la stessa
ragione: un CHECK su un'espressione booleana fra due colonne, una FK RESTRICT e
un ALTER TABLE idempotente sono cose che solo un database puo' provare. Un
doppio che li riproduce prova il doppio.

IL CLUSTER E' USA E GETTA, E NON PUO' RAGGIUNGERE NIENTE

`initdb` in una cartella temporanea, un server su socket unix con
`listen_addresses=''`, un database creato apposta, e tutto cancellato in
teardown. Questo file non legge nessuna variabile d'ambiente di connessione,
quindi non puo' toccare TEST o PROD nemmeno per sbaglio.

COSA SI PROVA QUI E NON ALTROVE

* apply / down / reapply della 060, nell'ordine, sul database vero;
* il CHECK che tiene appaiate le due colonne, provato scrivendo davvero una
  riga a meta';
* la FK RESTRICT, provata cancellando davvero l'agenzia;
* che il down NON porti via sessioni: e' la cosa che una CASCADE scritta per
  distrazione farebbe, e che nessun test sui doppi noterebbe.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

psycopg2 = pytest.importorskip("psycopg2")
from psycopg2.extras import RealDictCursor  # noqa: E402

PORTA = 54329

#: Le migration, nell'ordine in cui vanno applicate. 027 porta `agencies`,
#: `operator_users` e `operator_sessions`, che sono tutto cio' su cui la 060
#: lavora.
CATENA = (
    "027_p26_agency_identity",
    "060_p28_acting_agency_context",
)


def _bin_postgres() -> Path | None:
    for radice in sys.path:
        candidato = Path(radice) / "embedded_postgres" / "pginstall" / "bin"
        if (candidato / "initdb").exists():
            return candidato
    trovato = shutil.which("initdb")
    return Path(trovato).parent if trovato else None


PGBIN = _bin_postgres()

pytestmark = pytest.mark.skipif(
    PGBIN is None,
    reason="nessun PostgreSQL reale disponibile: le prove sui doppi restano",
)


@pytest.fixture(scope="session")
def cluster():
    """Un cluster vivo per la sessione, poi cancellato. Mai riusato."""
    base = Path(tempfile.mkdtemp(prefix="p28_pg_"))
    dati, socket = base / "data", base / "sock"
    socket.mkdir()
    ambiente = dict(os.environ)
    ambiente["LD_LIBRARY_PATH"] = (
        f"{PGBIN.parent / 'lib'}:{ambiente.get('LD_LIBRARY_PATH', '')}"
    )

    def corri(*argomenti):
        return subprocess.run(
            argomenti, env=ambiente, capture_output=True, text=True, timeout=120
        )

    esito = corri(
        str(PGBIN / "initdb"), "-D", str(dati), "-U", "postgres",
        "--auth=trust", "-E", "UTF8",
    )
    if esito.returncode != 0:
        shutil.rmtree(base, ignore_errors=True)
        pytest.skip(f"initdb non riuscito: {esito.stderr[-300:]}")

    avvio = corri(
        str(PGBIN / "pg_ctl"), "-D", str(dati), "-w", "-l", str(base / "pg.log"),
        "-o", f"-p {PORTA} -k {socket} -c listen_addresses=''", "start",
    )
    if avvio.returncode != 0:
        shutil.rmtree(base, ignore_errors=True)
        pytest.skip(f"pg_ctl start non riuscito: {avvio.stderr[-300:]}")

    for _ in range(40):
        try:
            psycopg2.connect(host=str(socket), port=PORTA, user="postgres",
                             dbname="postgres").close()
            break
        except psycopg2.OperationalError:
            time.sleep(0.25)
    else:  # pragma: no cover - il cluster non risponde
        corri(str(PGBIN / "pg_ctl"), "-D", str(dati), "-m", "immediate", "stop")
        shutil.rmtree(base, ignore_errors=True)
        pytest.skip("il cluster non ha accettato connessioni")

    try:
        yield {"host": str(socket), "port": PORTA, "user": "postgres"}
    finally:
        corri(str(PGBIN / "pg_ctl"), "-D", str(dati), "-m", "immediate", "stop")
        shutil.rmtree(base, ignore_errors=True)


def _crea_database(cluster, nome: str):
    conn = psycopg2.connect(dbname="postgres", **cluster)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(f'DROP DATABASE IF EXISTS "{nome}"')
        cur.execute(f'CREATE DATABASE "{nome}"')
    conn.close()
    return psycopg2.connect(dbname=nome, **cluster)


def _applica(conn, nome_migration: str):
    """Esegue il file di migration cosi' com'e', senza riscriverlo."""
    sql = (ROOT / "migrations" / f"{nome_migration}.sql").read_text(encoding="utf-8")
    conn.rollback()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.autocommit = False


@pytest.fixture(scope="session")
def schema(cluster):
    conn = _crea_database(cluster, "p28_acting")
    for nome in CATENA:
        _applica(conn, nome)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture()
def cur(schema):
    """Un cursore dict, e un ROLLBACK dopo ogni test."""
    with schema.cursor(cursor_factory=RealDictCursor) as c:
        try:
            yield c
        finally:
            schema.rollback()


def _agenzia(cur, slug="ospite", stato="active"):
    cur.execute(
        "INSERT INTO agencies (name, slug, status) VALUES (%s, %s, %s) "
        "RETURNING id",
        (slug.title(), slug, stato),
    )
    return cur.fetchone()["id"]


def _operatore(cur, email="giorgio@example.test"):
    cur.execute(
        "INSERT INTO operator_users (email, email_normalized, password_hash, "
        "status, is_platform_admin) VALUES (%s, %s, %s, 'active', TRUE) "
        "RETURNING id",
        (email, email, "pbkdf2_sha256$600000$" + "a" * 22 + "$" + "b" * 43),
    )
    return cur.fetchone()["id"]


def _sessione(cur, operatore, token="c" * 64):
    cur.execute(
        "INSERT INTO operator_sessions (operator_user_id, token_hash, "
        "expires_at) VALUES (%s, %s, NOW() + interval '1 hour') RETURNING id",
        (operatore, token),
    )
    return cur.fetchone()["id"]


# ---------------------------------------------------------------------------
# A - LA MIGRATION
# ---------------------------------------------------------------------------

def test_a1_060_applies_reverts_and_reapplies_on_a_real_cluster(cluster):
    """Applicata, annullata, riapplicata. Su un database suo, non su quello
    condiviso: il down toglie colonne che tutti gli altri test usano.
    """
    conn = _crea_database(cluster, "p28_ciclo")
    try:
        _applica(conn, "027_p26_agency_identity")
        _applica(conn, "060_p28_acting_agency_context")

        def colonne():
            with conn.cursor() as c:
                c.execute(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_name = 'operator_sessions' "
                    "AND column_name IN ('acting_agency_id','acting_entered_at')"
                )
                return c.fetchone()[0]

        assert colonne() == 2

        # Il ledger: il down lo nomina, e senza la tabella la DELETE
        # fallirebbe per un motivo che non e' quello sotto esame. Il runner la
        # crea in produzione; qui la si costruisce a mano, come fa gia'
        # tests/test_p27_6_postgres_real.py.
        conn.rollback()
        conn.autocommit = True
        with conn.cursor() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                " version VARCHAR(100) PRIMARY KEY,"
                " applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"
            )
            c.execute(
                "INSERT INTO schema_migrations (version) VALUES "
                "('060_p28_acting_agency_context')"
            )
        conn.autocommit = False

        _applica(conn, "060_p28_acting_agency_context_down")
        assert colonne() == 0
        with conn.cursor() as c:
            c.execute("SELECT count(*) FROM schema_migrations "
                      "WHERE version = '060_p28_acting_agency_context'")
            assert c.fetchone()[0] == 0

        # E le tabelle di P26 sono ancora li': la 060 ha aggiunto due colonne e
        # il down non si e' portato via nient'altro.
        with conn.cursor() as c:
            c.execute("SELECT to_regclass('public.operator_sessions')")
            assert c.fetchone()[0] is not None
            c.execute("SELECT to_regclass('public.agency_memberships')")
            assert c.fetchone()[0] is not None

        _applica(conn, "060_p28_acting_agency_context")
        assert colonne() == 2
    finally:
        conn.close()


def test_a2_applying_it_twice_changes_nothing(cluster):
    """Idempotente: il runner puo' ripassarci sopra senza rompere niente."""
    conn = _crea_database(cluster, "p28_due_volte")
    try:
        _applica(conn, "027_p26_agency_identity")
        _applica(conn, "060_p28_acting_agency_context")
        _applica(conn, "060_p28_acting_agency_context")
        with conn.cursor() as c:
            c.execute(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name = 'operator_sessions' "
                "AND column_name LIKE 'acting%'"
            )
            assert c.fetchone()[0] == 2
    finally:
        conn.close()


def test_a3_the_down_does_not_take_sessions_with_it(cluster):
    """La prova che nessun doppio puo' dare: le righe sopravvivono al down.

    Se la FK fosse CASCADE, o se il down cancellasse righe invece di colonne,
    qui sparirebbe una sessione - cioe' il diritto di stare collegati di
    qualcuno che con l'acting non c'entra.
    """
    conn = _crea_database(cluster, "p28_down_sessioni")
    try:
        _applica(conn, "027_p26_agency_identity")
        _applica(conn, "060_p28_acting_agency_context")

        conn.rollback()
        conn.autocommit = True
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            utente = _operatore(c)
            agenzia = _agenzia(c)
            sessione = _sessione(c, utente)
            c.execute(
                "UPDATE operator_sessions SET acting_agency_id = %s, "
                "acting_entered_at = NOW() WHERE id = %s",
                (agenzia, sessione),
            )

        with conn.cursor() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                " version VARCHAR(100) PRIMARY KEY,"
                " applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"
            )
        conn.autocommit = False

        _applica(conn, "060_p28_acting_agency_context_down")
        with conn.cursor() as c:
            c.execute("SELECT count(*) FROM operator_sessions WHERE id = %s",
                      (sessione,))
            assert c.fetchone()[0] == 1
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# B - IL CHECK MORDE
# ---------------------------------------------------------------------------

def test_b1_an_acting_agency_without_an_entry_instant_is_refused(cur):
    utente = _operatore(cur, "b1@example.test")
    agenzia = _agenzia(cur, "b1")
    sessione = _sessione(cur, utente, "1" * 64)
    with pytest.raises(psycopg2.errors.CheckViolation):
        cur.execute(
            "UPDATE operator_sessions SET acting_agency_id = %s WHERE id = %s",
            (agenzia, sessione),
        )


def test_b2_an_entry_instant_without_an_agency_is_refused(cur):
    """L'altro verso: il CHECK e' un'uguaglianza, non un'implicazione sola."""
    utente = _operatore(cur, "b2@example.test")
    sessione = _sessione(cur, utente, "2" * 64)
    with pytest.raises(psycopg2.errors.CheckViolation):
        cur.execute(
            "UPDATE operator_sessions SET acting_entered_at = NOW() WHERE id = %s",
            (sessione,),
        )


def test_b3_the_complete_pair_is_accepted(cur):
    utente = _operatore(cur, "b3@example.test")
    agenzia = _agenzia(cur, "b3")
    sessione = _sessione(cur, utente, "3" * 64)
    cur.execute(
        "UPDATE operator_sessions SET acting_agency_id = %s, "
        "acting_entered_at = NOW() WHERE id = %s RETURNING acting_agency_id",
        (agenzia, sessione),
    )
    assert cur.fetchone()["acting_agency_id"] == agenzia


def test_b4_clearing_both_columns_is_accepted(cur):
    """Uscire e' scrivere due NULL: il CHECK non deve impedirlo."""
    utente = _operatore(cur, "b4@example.test")
    agenzia = _agenzia(cur, "b4")
    sessione = _sessione(cur, utente, "4" * 64)
    cur.execute(
        "UPDATE operator_sessions SET acting_agency_id = %s, "
        "acting_entered_at = NOW() WHERE id = %s",
        (agenzia, sessione),
    )
    cur.execute(
        "UPDATE operator_sessions SET acting_agency_id = NULL, "
        "acting_entered_at = NULL WHERE id = %s RETURNING acting_agency_id",
        (sessione,),
    )
    assert cur.fetchone()["acting_agency_id"] is None


def test_b5_a_session_with_no_acting_context_is_the_normal_case(cur):
    utente = _operatore(cur, "b5@example.test")
    sessione = _sessione(cur, utente, "5" * 64)
    cur.execute(
        "SELECT acting_agency_id, acting_entered_at FROM operator_sessions "
        "WHERE id = %s",
        (sessione,),
    )
    riga = cur.fetchone()
    assert riga["acting_agency_id"] is None
    assert riga["acting_entered_at"] is None


# ---------------------------------------------------------------------------
# C - LA CHIAVE ESTERNA
# ---------------------------------------------------------------------------

def test_c1_an_acting_context_cannot_point_at_an_agency_that_does_not_exist(cur):
    utente = _operatore(cur, "c1@example.test")
    sessione = _sessione(cur, utente, "6" * 64)
    with pytest.raises(psycopg2.errors.ForeignKeyViolation):
        cur.execute(
            "UPDATE operator_sessions SET acting_agency_id = 999999, "
            "acting_entered_at = NOW() WHERE id = %s",
            (sessione,),
        )


def test_c2_the_foreign_key_restricts_and_does_not_cascade(cur):
    """Cancellare l'agenzia mentre qualcuno ci sta dentro deve FALLIRE.

    Con una CASCADE questa DELETE porterebbe via la sessione; con SET NULL
    lascerebbe una riga a meta' che il CHECK rifiuterebbe subito dopo. RESTRICT
    e' l'unica delle tre che dice la verita': quell'agenzia non si cancella
    finche' c'e' dentro qualcuno.
    """
    utente = _operatore(cur, "c2@example.test")
    agenzia = _agenzia(cur, "c2")
    sessione = _sessione(cur, utente, "7" * 64)
    cur.execute(
        "UPDATE operator_sessions SET acting_agency_id = %s, "
        "acting_entered_at = NOW() WHERE id = %s",
        (agenzia, sessione),
    )
    # `RestrictViolation` e non il generico `ForeignKeyViolation`: e' la
    # sottoclasse che PostgreSQL solleva SOLO per un RESTRICT, quindi e'
    # l'asserzione che distingue questa FK da una NO ACTION scritta per
    # distrazione - che qui passerebbe ugualmente.
    with pytest.raises(psycopg2.errors.RestrictViolation):
        cur.execute("DELETE FROM agencies WHERE id = %s", (agenzia,))


def test_c3_an_agency_nobody_is_visiting_can_still_be_deleted(cur):
    """Il vincolo non deve essere piu' largo di cio' che difende."""
    agenzia = _agenzia(cur, "c3")
    cur.execute("DELETE FROM agencies WHERE id = %s", (agenzia,))
    cur.execute("SELECT count(*) AS n FROM agencies WHERE id = %s", (agenzia,))
    assert cur.fetchone()["n"] == 0


# ---------------------------------------------------------------------------
# D - L'INDICE
# ---------------------------------------------------------------------------

def test_d1_the_acting_index_exists_and_is_partial(cur):
    """Parziale: la stragrande maggioranza delle righe ha NULL qui."""
    cur.execute(
        """
        SELECT pg_get_expr(i.indpred, i.indrelid) AS predicato
          FROM pg_index i
          JOIN pg_class c ON c.oid = i.indexrelid
         WHERE c.relname = 'idx_operator_sessions_acting'
        """
    )
    riga = cur.fetchone()
    assert riga is not None, "idx_operator_sessions_acting non installato"
    assert riga["predicato"] is not None, "l'indice non e' parziale"


def test_d2_the_acting_index_is_not_unique(cur):
    """Due sessioni possono visitare la stessa agenzia: due finestre, due
    postazioni, due persone. Un indice unico qui vieterebbe la seconda.
    """
    cur.execute(
        """
        SELECT i.indisunique
          FROM pg_index i
          JOIN pg_class c ON c.oid = i.indexrelid
         WHERE c.relname = 'idx_operator_sessions_acting'
        """
    )
    assert cur.fetchone()["indisunique"] is False


def test_d3_two_sessions_can_visit_the_same_agency(cur):
    utente = _operatore(cur, "d3@example.test")
    altro = _operatore(cur, "d3bis@example.test")
    agenzia = _agenzia(cur, "d3")
    for operatore, token in ((utente, "8" * 64), (altro, "9" * 64)):
        sessione = _sessione(cur, operatore, token)
        cur.execute(
            "UPDATE operator_sessions SET acting_agency_id = %s, "
            "acting_entered_at = NOW() WHERE id = %s",
            (agenzia, sessione),
        )
    cur.execute(
        "SELECT count(*) AS n FROM operator_sessions WHERE acting_agency_id = %s",
        (agenzia,),
    )
    assert cur.fetchone()["n"] == 2


# ---------------------------------------------------------------------------
# E - IL ROLLBACK, che e' cio' su cui si regge l'isolamento di questo file
# ---------------------------------------------------------------------------

def test_e1_a_rollback_really_undoes_an_acting_context(schema):
    with schema.cursor(cursor_factory=RealDictCursor) as c:
        utente = _operatore(c, "e1@example.test")
        agenzia = _agenzia(c, "e1")
        sessione = _sessione(c, utente, "e" * 64)
        c.execute(
            "UPDATE operator_sessions SET acting_agency_id = %s, "
            "acting_entered_at = NOW() WHERE id = %s",
            (agenzia, sessione),
        )
    schema.rollback()
    with schema.cursor(cursor_factory=RealDictCursor) as c:
        c.execute("SELECT count(*) AS n FROM operator_sessions WHERE id = %s",
                  (sessione,))
        assert c.fetchone()["n"] == 0


# ---------------------------------------------------------------------------
# F - I WRITER VERI, SULLA TABELLA VERA
#
# I test sui doppi provano che il service DECIDE di cancellare. Questi provano
# che l'istruzione che esegue quella decisione lascia davvero la riga pulita -
# ed e' la riga che la richiesta successiva rileggera'.
#
# Qui gira il codice di produzione: `operator_auth.repository`, non una copia
# del suo SQL. Una divergenza fra il testo del repository e cio' che la tabella
# accetta si vede qui, e non su Render.
# ---------------------------------------------------------------------------

from operator_auth import repository as auth_repository  # noqa: E402


def test_f1_set_acting_context_writes_both_columns(cur):
    utente = _operatore(cur, "f1@example.test")
    agenzia = _agenzia(cur, "f1")
    sessione = _sessione(cur, utente, "f" * 64)

    toccate = auth_repository.set_acting_context(cur, sessione, agenzia)
    assert toccate == 1

    cur.execute(
        "SELECT acting_agency_id, acting_entered_at FROM operator_sessions "
        "WHERE id = %s",
        (sessione,),
    )
    riga = cur.fetchone()
    assert riga["acting_agency_id"] == agenzia
    assert riga["acting_entered_at"] is not None


def test_f2_clear_acting_context_leaves_the_row_null(cur):
    """LA PROVA DIRETTA CHIESTA IN REVIEW: si guarda `operator_sessions`."""
    utente = _operatore(cur, "f2@example.test")
    agenzia = _agenzia(cur, "f2")
    sessione = _sessione(cur, utente, "2" * 63 + "a")
    auth_repository.set_acting_context(cur, sessione, agenzia)

    auth_repository.clear_acting_context(cur, sessione)

    cur.execute(
        "SELECT acting_agency_id, acting_entered_at FROM operator_sessions "
        "WHERE id = %s",
        (sessione,),
    )
    riga = cur.fetchone()
    assert riga["acting_agency_id"] is None
    assert riga["acting_entered_at"] is None


def test_f3_clearing_twice_is_accepted_and_still_null(cur):
    """L'idempotenza e' cio' che permette all'uscita di non fallire mai."""
    utente = _operatore(cur, "f3@example.test")
    agenzia = _agenzia(cur, "f3")
    sessione = _sessione(cur, utente, "3" * 63 + "a")
    auth_repository.set_acting_context(cur, sessione, agenzia)

    auth_repository.clear_acting_context(cur, sessione)
    auth_repository.clear_acting_context(cur, sessione)

    cur.execute(
        "SELECT acting_agency_id FROM operator_sessions WHERE id = %s",
        (sessione,),
    )
    assert cur.fetchone()["acting_agency_id"] is None


def test_f4_clearing_a_session_that_never_acted_writes_the_same_nulls(cur):
    utente = _operatore(cur, "f4@example.test")
    sessione = _sessione(cur, utente, "4" * 63 + "a")
    assert auth_repository.clear_acting_context(cur, sessione) == 1
    cur.execute(
        "SELECT acting_agency_id, acting_entered_at FROM operator_sessions "
        "WHERE id = %s",
        (sessione,),
    )
    riga = cur.fetchone()
    assert riga["acting_agency_id"] is None
    assert riga["acting_entered_at"] is None


def test_f5_clearing_an_unknown_session_touches_nothing(cur):
    """Zero righe, e nessun errore: il chiamante decide cosa farne."""
    assert auth_repository.clear_acting_context(cur, 99999999) == 0


def test_f6_setting_an_acting_context_on_a_missing_session_touches_nothing(cur):
    """Zero righe: la sessione e' sparita fra l'ammissione e la scrittura, e
    `acting_service` lo traduce in un ingresso NON avvenuto."""
    agenzia = _agenzia(cur, "f6")
    assert auth_repository.set_acting_context(cur, 99999999, agenzia) == 0


def test_f7_the_repository_writes_survive_a_read_back_by_another_cursor(schema):
    """Scritto da un cursore, riletto da un altro: e' cio' che accade fra due
    richieste HTTP consecutive della stessa sessione."""
    with schema.cursor(cursor_factory=RealDictCursor) as c:
        utente = _operatore(c, "f7@example.test")
        agenzia = _agenzia(c, "f7")
        sessione = _sessione(c, utente, "7" * 63 + "a")
        auth_repository.set_acting_context(c, sessione, agenzia)

    with schema.cursor(cursor_factory=RealDictCursor) as c:
        c.execute(
            "SELECT acting_agency_id FROM operator_sessions WHERE id = %s",
            (sessione,),
        )
        assert c.fetchone()["acting_agency_id"] == agenzia
        auth_repository.clear_acting_context(c, sessione)

    with schema.cursor(cursor_factory=RealDictCursor) as c:
        c.execute(
            "SELECT acting_agency_id, acting_entered_at FROM operator_sessions "
            "WHERE id = %s",
            (sessione,),
        )
        riga = c.fetchone()
        assert riga["acting_agency_id"] is None
        assert riga["acting_entered_at"] is None

    schema.rollback()
