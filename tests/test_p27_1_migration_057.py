"""P27-1 - la migration 057, letta come testo e come regola.

Questo file NON tocca un database. Prova che 057 e' conforme alle regole che il
runner di P26 fa rispettare, che dice le stesse cose che dice il codice, e che
non ha perso nessuna delle garanzie per cui e' stata scritta.

L'applicazione vera e' stata eseguita a mano su PostgreSQL 16 durante lo
sviluppo (up, ri-applicazione, sonde ostili su UPDATE / DELETE / TRUNCATE / FK,
down, up di nuovo). Quella e' evidenza di esecuzione e sta nel report; questo
file e' cio' che resta a guardia nella suite.

Mappa:

    M1  le regole del runner (G1-G8): validazione e contiguita'
    M2  la proprieta' dell'era 027+: il file non apre una transazione
    M3  il down: esiste, si bracketta, rimuove cio' che 057 crea
    M4  append-only: i due trigger, e cosa devono e non devono coprire
    M5  i vincoli, e il loro accordo con platform_admin/enums.py
    M6  l'assenza di chiavi esterne, e perche' e' obbligatoria
    M7  il perimetro: 057 non tocca nulla di P26
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from platform_admin.enums import ACTION_NAMESPACE, AUDIT_RESULTS

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"
VERSION = "057_p27_platform_audit_log"
UP = MIGRATIONS / f"{VERSION}.sql"
DOWN = MIGRATIONS / f"{VERSION}_down.sql"


@pytest.fixture(scope="module")
def up_sql() -> str:
    return UP.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def down_sql() -> str:
    return DOWN.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def runner():
    from scripts import p26_migrate

    return p26_migrate


def _executable(runner, text: str) -> str:
    """Il solo SQL eseguibile, senza commenti.

    Ogni asserzione di questo file che riguarda un COMPORTAMENTO passa di qui.
    I commenti di 057 nominano di proposito UPDATE, DELETE e TRUNCATE per
    spiegare cosa viene rifiutato: cercarli nel testo grezzo proverebbe che il
    file parla di quelle operazioni, non che le impedisce.
    """
    return runner.strip_sql_comments(text)


# ---------------------------------------------------------------------------
# M1 - le regole del runner
# ---------------------------------------------------------------------------

def test_m1_057_passes_every_rule_the_runner_enforces(runner):
    migrations = {m.version: m for m in runner.discover_migrations()}
    assert VERSION in migrations, sorted(migrations)
    assert runner.validate_migration(migrations[VERSION]) == []


def test_m1_the_whole_migration_set_is_still_valid_and_contiguous(runner):
    """Aggiungere 057 non deve rompere l'insieme.

    Un buco o un doppione nella sequenza e' il tipo di errore che si scopre in
    fase di deploy, cioe' nel momento peggiore.
    """
    migrations = runner.discover_migrations()
    runner.verify_contiguous(migrations)
    assert [v for m in migrations for v in runner.validate_migration(m)] == []


def test_m1_057_is_the_highest_version_and_follows_056(runner):
    numbers = sorted(m.number for m in runner.discover_migrations())
    assert numbers[-1] == 57
    assert 56 in numbers


def test_m1_057_is_above_the_runner_owned_transaction_gate(runner):
    assert 57 >= runner.RUNNER_OWNED_TRANSACTION_FROM


# ---------------------------------------------------------------------------
# M2 - il file non apre una transazione
# ---------------------------------------------------------------------------

def test_m2_the_up_file_opens_no_transaction_of_its_own(runner, up_sql):
    """Dalla 027 il runner esegue il corpo e scrive la riga di ledger insieme.

    Un file che si bracketta da solo committerebbe lo schema prima del ledger,
    e un guasto fra i due cambierebbe il database senza registrarlo.
    """
    executable = _executable(runner, up_sql)
    assert not runner.BEGIN_RE.search(executable), "057 apre un BEGIN"
    assert not runner.COMMIT_RE.search(executable), "057 chiude un COMMIT"


def test_m2_the_plpgsql_blocks_are_not_mistaken_for_a_transaction(runner, up_sql):
    """Controllo del controllo.

    057 contiene molti `BEGIN` - sono corpi plpgsql. Il test sopra passerebbe
    comunque, ma solo perche' `BEGIN_RE` richiede il punto e virgola: se quella
    regex cambiasse, il test sopra diventerebbe rumore e nessuno se ne
    accorgerebbe. Qui si asserisce che i BEGIN ci sono davvero.
    """
    assert "BEGIN" in _executable(runner, up_sql)


def test_m2_the_up_file_performs_no_concurrent_index_build(runner, up_sql):
    assert not runner.CONCURRENTLY_RE.search(_executable(runner, up_sql))


def test_m2_the_up_file_never_removes_a_ledger_row(runner, up_sql):
    assert not re.search(
        r"DELETE\s+FROM\s+schema_migrations",
        _executable(runner, up_sql),
        re.IGNORECASE,
    )


# ---------------------------------------------------------------------------
# M3 - il down
# ---------------------------------------------------------------------------

def test_m3_a_down_file_exists(runner):
    migrations = {m.version: m for m in runner.discover_migrations()}
    assert migrations[VERSION].down_available is True
    assert DOWN.exists()


def test_m3_the_down_brackets_its_own_transaction(runner, down_sql):
    executable = _executable(runner, down_sql)
    assert runner.BEGIN_RE.search(executable)
    assert runner.COMMIT_RE.search(executable)


def test_m3_the_down_removes_everything_the_up_creates(runner, down_sql):
    executable = _executable(runner, down_sql).upper()
    assert "DROP TABLE IF EXISTS PLATFORM_AUDIT_LOG" in executable
    for function in (
        "PLATFORM_AUDIT_LOG_APPEND_ONLY",
        "PLATFORM_AUDIT_LOG_NO_TRUNCATE",
    ):
        assert f"DROP FUNCTION IF EXISTS {function}" in executable, function


def test_m3_the_down_drops_the_functions_and_not_only_the_table(down_sql):
    """DROP TABLE porta via i suoi trigger, non le due funzioni.

    Sono oggetti di schema e sopravvivrebbero come orfani, che il tentativo
    successivo di applicare 057 poi sostituirebbe con CREATE OR REPLACE invece
    di creare - una differenza che nessuno nota finche' non conta.
    """
    table_at = down_sql.upper().index("DROP TABLE IF EXISTS PLATFORM_AUDIT_LOG")
    for function in (
        "DROP FUNCTION IF EXISTS PLATFORM_AUDIT_LOG_APPEND_ONLY",
        "DROP FUNCTION IF EXISTS PLATFORM_AUDIT_LOG_NO_TRUNCATE",
    ):
        assert down_sql.upper().index(function) < table_at, function


def test_m3_the_down_removes_its_own_ledger_row(down_sql):
    assert f"DELETE FROM schema_migrations WHERE version = '{VERSION}'" in down_sql


def test_m3_the_down_does_not_cascade(runner, down_sql):
    """Se qualcosa dipendesse da questa tabella, il down deve fallire e dirlo,
    non portarselo via in silenzio.

    Sul SQL eseguibile: il commento del file dice "dropped without CASCADE
    deliberately", e cercare la parola nel testo grezzo trasformerebbe la
    spiegazione della regola in una sua violazione.
    """
    assert "CASCADE" not in _executable(runner, down_sql).upper()


# ---------------------------------------------------------------------------
# M4 - append-only
# ---------------------------------------------------------------------------

def test_m4_a_row_trigger_refuses_update_and_delete(runner, up_sql):
    executable = _executable(runner, up_sql)
    assert "BEFORE UPDATE OR DELETE ON platform_audit_log" in executable
    assert "FOR EACH ROW EXECUTE FUNCTION platform_audit_log_append_only()" in executable


def test_m4_a_statement_trigger_refuses_truncate(runner, up_sql):
    """TRUNCATE non e' ne' UPDATE ne' DELETE, non attiva un trigger di riga, e
    svuota la tabella in un'istruzione. Una difesa da DELETE che lascia aperto
    TRUNCATE e' una difesa dal modo lento di fare la stessa cosa."""
    executable = _executable(runner, up_sql)
    assert "BEFORE TRUNCATE ON platform_audit_log" in executable
    assert (
        "FOR EACH STATEMENT EXECUTE FUNCTION platform_audit_log_no_truncate()"
        in executable
    )


def test_m4_neither_trigger_fires_on_insert(runner, up_sql):
    """La tabella e' append-only, non di sola lettura.

    Un trigger che scattasse anche su INSERT la renderebbe non scrivibile, e la
    prima cosa a scoprirlo sarebbe il primo login di piattaforma dopo il
    deploy.
    """
    executable = _executable(runner, up_sql)
    for line in executable.splitlines():
        if "BEFORE" in line and "ON platform_audit_log" in line:
            assert "INSERT" not in line, line


def test_m4_the_migration_verifies_its_own_triggers_from_the_catalogue(up_sql):
    """La 057 non si fida delle proprie istruzioni: rilegge pg_trigger.

    Stessa disciplina della 055. Sono asserite le quattro proprieta' che una
    corrispondenza per solo nome lascerebbe passare.
    """
    assert "FROM pg_trigger t" in up_sql
    assert "tgisinternal" in up_sql
    assert "tgenabled" in up_sql
    assert "t.tgrelid = ('public.' || v_row[2])::regclass" in up_sql


def test_m4_the_migration_proves_an_insert_still_works(up_sql):
    """La sonda interna: un INSERT accettato e un UPDATE rifiutato, nello
    stesso blocco, prima che la migration si dichiari riuscita."""
    assert "platform.migration.probe" in up_sql
    assert "the guard is over-broad" in up_sql
    assert "the append-only guard is not effective" in up_sql


def test_m4_the_probe_undoes_itself_without_a_delete(runner, up_sql):
    """DELETE sarebbe rifiutato dal trigger, e SAVEPOINT non e' disponibile in
    plpgsql: la sonda usa il savepoint implicito di un blocco EXCEPTION."""
    executable = _executable(runner, up_sql)
    assert "SAVEPOINT" not in executable.upper()
    assert "P27_1_057_PROBE_ROLLBACK" in executable


# ---------------------------------------------------------------------------
# M5 - i vincoli, e l'accordo con il codice
# ---------------------------------------------------------------------------

def test_m5_the_result_check_lists_exactly_the_application_results(up_sql):
    """Il CHECK e `AUDIT_RESULTS` devono dire la stessa cosa.

    Se divergessero, un esito valido per l'applicazione verrebbe rifiutato dal
    database mentre si registra un rifiuto - cioe' l'audit fallirebbe proprio
    nel momento che esiste per registrare.
    """
    match = re.search(r"result IN \(([^)]*)\)", up_sql)
    assert match, "il CHECK su result non e' stato trovato"
    declared = tuple(
        value.strip().strip("'") for value in match.group(1).split(",")
    )
    assert declared == AUDIT_RESULTS, (declared, AUDIT_RESULTS)


def test_m5_the_action_namespace_check_matches_the_application_constant(up_sql):
    assert ACTION_NAMESPACE == "platform."
    assert f"action LIKE '{ACTION_NAMESPACE}%'" in up_sql


def test_m5_an_actor_label_can_never_be_blank(up_sql):
    """NOT NULL non basta: ' ' e' un attore valido per NOT NULL e non e' un
    attore."""
    assert "actor_label      VARCHAR(120) NOT NULL" in up_sql
    assert "CHECK (BTRIM(actor_label) <> '')" in up_sql


def test_m5_metadata_defaults_to_an_empty_object_and_is_not_nullable(up_sql):
    assert "metadata         JSONB        NOT NULL DEFAULT '{}'::jsonb" in up_sql


def test_m5_the_three_read_paths_are_indexed(runner, up_sql):
    executable = _executable(runner, up_sql)
    for index in (
        "idx_platform_audit_created",
        "idx_platform_audit_target_agency",
        "idx_platform_audit_actor",
    ):
        assert index in executable, index


# ---------------------------------------------------------------------------
# M6 - le chiavi esterne
# ---------------------------------------------------------------------------

def test_m6_the_register_declares_no_foreign_key_at_all(runner, up_sql):
    """NESSUNA FK. E' la proprieta' che tiene insieme le altre tre.

    Un registro immutabile deve poter fare tre cose contemporaneamente: non
    modificarsi, non cancellarsi, e non impedire la cancellazione di cio' che
    descrive. Ogni azione referenziale ne rompe almeno una.

      SET NULL  -> e' una UPDATE su questa tabella, che la tabella rifiuta.
                   La DELETE del genitore fallisce, citando la regola sbagliata.
      CASCADE   -> cancella le righe di audit, che la tabella rifiuta, e che
                   comunque distruggerebbe la storia con l'entita'.
      RESTRICT  -> non muta nulla, ma il registro pone un VETO sulla
                   cancellazione di chiunque abbia mai nominato, e l'unica via
                   d'uscita - cancellare prima la riga di audit - e' vietata
                   dallo stesso trigger. Una regola senza uscita.

    Quindi nessuna delle tre: gli id sono istantanee storiche, non riferimenti
    vivi. L'asserzione e' sul SQL eseguibile perche' i commenti del file
    nominano tutte e tre le azioni per spiegare perche' non ci sono.
    """
    executable = _executable(runner, up_sql).upper()
    assert "REFERENCES" not in executable, executable
    assert "FOREIGN KEY" not in executable, executable
    assert "ON DELETE" not in executable, executable


def test_m6_the_two_historical_ids_are_plain_nullable_integers(up_sql):
    assert "actor_user_id    BIGINT," in up_sql
    assert "target_agency_id BIGINT," in up_sql


def test_m6_the_reason_for_having_no_foreign_key_is_recorded_in_the_file(up_sql):
    """Una scelta contro-intuitiva senza la sua ragione accanto e' una scelta
    che qualcuno annullera' - e questa si annulla aggiungendo una riga."""
    assert "THERE ARE NO FOREIGN KEYS ON THIS TABLE, AND THAT IS THE DESIGN" in up_sql


def test_m6_the_actor_is_stored_twice_on_purpose(up_sql):
    """L'intero serve a fare join finche' il genitore esiste; il testo E' il
    record. Senza la seconda meta', togliere la FK avrebbe perso l'attore."""
    assert "actor_user_id    BIGINT," in up_sql
    assert "actor_label      VARCHAR(120) NOT NULL" in up_sql


# ---------------------------------------------------------------------------
# M7 - il perimetro
# ---------------------------------------------------------------------------

def test_m7_057_creates_exactly_one_table(runner, up_sql):
    executable = _executable(runner, up_sql)
    tables = re.findall(r"CREATE TABLE(?:\s+IF NOT EXISTS)?\s+(\w+)", executable)
    assert tables == ["platform_audit_log"], tables


def test_m7_057_alters_no_existing_table(runner, up_sql):
    """P27-1 e' additiva. Nessuna colonna su agencies, operator_users o
    agency_memberships: quelle appartengono a P27-2, P27-3 e P27-4."""
    executable = _executable(runner, up_sql).upper()
    assert "ALTER TABLE" not in executable


def test_m7_057_touches_none_of_the_p26_identity_tables(runner, up_sql):
    """La sonda LEGGE operator_users? No: non la nomina affatto.

    057 la referenzia con una FK, il che e' una dipendenza dichiarata, non una
    modifica. Nessuna scrittura su una tabella di P26.
    """
    executable = _executable(runner, up_sql).upper()
    for table in ("AGENCY_MEMBERSHIPS", "OPERATOR_SESSIONS"):
        assert table not in executable, table
    for statement in ("INSERT INTO OPERATOR_USERS", "UPDATE OPERATOR_USERS",
                      "INSERT INTO AGENCIES", "UPDATE AGENCIES"):
        assert statement not in executable, statement


def test_m7_057_seeds_no_row_that_survives(runner, up_sql):
    """L'unica INSERT e' la sonda, e la sonda si annulla.

    Non esiste un atto amministrativo predefinito: la prima riga vera di questa
    tabella deve essere qualcosa che qualcuno ha fatto.
    """
    executable = _executable(runner, up_sql)
    inserts = re.findall(r"INSERT INTO (\w+)", executable, re.IGNORECASE)
    assert inserts == ["platform_audit_log"], inserts
    assert "platform.migration.probe" in executable
