"""Prove su `scripts/p26_3_live_cert.py` - lo strumento di certificazione live.

PERCHE' UNO STRUMENTO DI VERIFICA VA VERIFICATO

Questo script fa due cose pericolose che nessun'altra parte del repository fa:
crea righe in `operator_users` e le cancella. Gira a mano, su TEST, e il suo
output e' l'evidenza su cui si dichiara certificato P26-3. Un difetto qui non
rompe la produzione - dice una cosa falsa su di essa, che e' peggio, perche'
non lo scopre nessuno.

Le tre cose che devono essere vere, e che sono provate qui:

* non puo' scrivere sul database sbagliato;
* non puo' lasciare righe dietro di se', qualunque cosa vada storta;
* non puo' dire PASS quando non ha finito.

Nessuna prova qui apre una connessione o una socket. Il database e l'HTTP sono
sostituiti da doppi, che e' il motivo per cui lo script e' scritto con quei due
seam invece che con chiamate diritte.
"""
from __future__ import annotations

import ast
import io
import py_compile
import re
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "p26_3_live_cert.py"

# Import diretto e non `importorskip`: se questo modulo smette di importarsi, la
# risposta giusta e' un fallimento, non uno skip silenzioso che farebbe sparire
# ventinove prove dal conteggio senza che nessuno se ne accorga.
from scripts import p26_3_live_cert as cert  # noqa: E402


# ---------------------------------------------------------------------------
# Doppi
# ---------------------------------------------------------------------------

class FakeCursor:
    """Un `operator_users` in memoria, abbastanza vero da poter essere rovinato.

    Non risponde solo per forma: tiene una tabella `state["users"]` di
    {id: email}, e le DELETE la modificano davvero. Senza questo, la prova che
    conta di piu' in questo file - un operatore di un altro run che sopravvive
    al cleanup - potrebbe solo ispezionare il testo delle query, che e'
    esattamente il tipo di verifica che non accorge di un WHERE sbagliato.

    `residue` e `delete_rowcount` restano come scavalcamenti espliciti, per le
    prove che devono simulare una DELETE che non ha fatto il suo lavoro.
    """

    def __init__(self, state: dict) -> None:
        self.state = state
        self.rowcount = 0
        self._row = None
        self._rows: list[dict] = []

    def _users(self) -> dict:
        return self.state.setdefault("users", {})

    def execute(self, sql, params=None):
        statement = " ".join(sql.split())
        self.state.setdefault("sql", []).append(statement)
        upper = statement.upper()

        if self.state.get("explode_on") and self.state["explode_on"] in statement:
            raise RuntimeError("il database e' caduto")

        if upper.startswith("SELECT CURRENT_DATABASE"):
            self._row = {"name": self.state.get("database", "stima360_db_test")}
        elif "FROM AGENCIES" in upper:
            self._rows = list(self.state.get("agencies", []))
        elif "COUNT(*) AS N FROM OPERATOR_USERS" in upper:
            if "leftovers" in self.state:
                self._row = {"n": self.state["leftovers"]}
            else:
                pattern = cert.CERT_PREFIX
                self._row = {"n": sum(1 for e in self._users().values()
                                      if e.startswith(pattern))}
        elif "AS USERS" in upper:
            if "residue" in self.state:
                self._row = dict(self.state["residue"])
            else:
                ids = set(params[0]) if params else set()
                remaining = len([i for i in ids if i in self._users()])
                self._row = {"users": remaining, "memberships": 0, "sessions": 0}
        elif upper.startswith("SELECT 1 FROM OPERATOR_USERS"):
            self._row = {"1": 1} if self.state.get("collide") else None
        elif upper.startswith("INSERT INTO OPERATOR_USERS"):
            self.state["next_id"] = self.state.get("next_id", 500) + 1
            self.state.setdefault("inserted", []).append(params)
            self._users()[self.state["next_id"]] = params[0]
            self._row = {"id": self.state["next_id"]}
            self.rowcount = 1
        elif upper.startswith("INSERT INTO AGENCY_MEMBERSHIPS"):
            self.state.setdefault("memberships", []).append(params)
            self.rowcount = 1
        elif upper.startswith("DELETE"):
            self.state.setdefault("deletes", []).append(statement)
            if "delete_rowcount" in self.state:
                self.rowcount = self.state["delete_rowcount"]
            elif "FROM OPERATOR_USERS" in upper and params:
                targets = [i for i in params[0] if i in self._users()]
                for identifier in targets:
                    del self._users()[identifier]
                self.rowcount = len(targets)
            else:
                self.rowcount = 1
        return None

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


def fake_database(**state) -> cert.Database:
    shared = dict(state)

    @contextmanager
    def factory(*, commit=False):
        shared.setdefault("commits", []).append(commit)
        yield (None, FakeCursor(shared))

    database = cert.Database(factory)
    database.state = shared          # type: ignore[attr-defined]
    return database


AGENCIES = [
    {"id": 1, "slug": cert.DEFAULT_AGENCY_SLUG, "name": "STIMA360", "status": "active"},
    {"id": 2, "slug": cert.AGENCY_B_SLUG, "name": "Agenzia B (TEST)", "status": "active"},
]


def quiet_report() -> tuple[cert.Report, io.StringIO]:
    stream = io.StringIO()
    return cert.Report(stream=stream), stream


# ---------------------------------------------------------------------------
# 1 - sintassi e compilazione
# ---------------------------------------------------------------------------

def test_1_the_script_compiles(tmp_path):
    """Uno script incollato in una shell di produzione non ha una seconda
    occasione per avere un SyntaxError."""
    py_compile.compile(str(SCRIPT), cfile=str(tmp_path / "out.pyc"), doraise=True)


def test_2_the_module_imports_without_doing_anything():
    """Importarlo non deve connettersi, non deve leggere l'ambiente e non deve
    stampare. Tutto il lavoro sta dietro `main()`, che e' anche cio' che rende
    verificabile il resto di questo file."""
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    allowed = (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef,
               ast.ClassDef, ast.Assign, ast.AnnAssign, ast.Expr, ast.If, ast.Try)
    for node in tree.body:
        assert isinstance(node, allowed), f"istruzione di modulo inattesa: {node}"

    # Una sola chiamata a livello di modulo, e nominata: mettere la radice del
    # repository su sys.path serve a `python scripts/p26_3_live_cert.py`, che
    # altrimenti non troverebbe operator_auth. Enumerata invece che vietata,
    # cosi' che una seconda non passi inosservata.
    calls = [
        ast.unparse(n) for n in tree.body
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
    ]
    assert len(calls) == 1 and calls[0].startswith("sys.path.insert"), calls


def test_3_the_entry_point_is_guarded():
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'if __name__ == "__main__":' in source
    assert "raise SystemExit(main())" in source
    # os._exit salterebbe il finally del cleanup e ucciderebbe pytest.
    assert "os._exit" not in source, "os._exit impedisce al cleanup di completare"


# ---------------------------------------------------------------------------
# 4 - rifiuto del database sbagliato
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", [
    None,
    "",
    "   ",
    "stima360_db",                 # produzione
    "stima360_db_prod",
    "stima360_db_test_2",          # un ALTRO database di test: comunque no
    "stima360_db_test_copy",
    "STIMA360_DB_TEST",            # il confronto e' esatto, non case-insensitive
    "postgres",
])
def test_4_every_other_database_is_refused(name):
    with pytest.raises(cert.GuardFailure):
        cert.assert_certification_database(name)


def test_5_only_the_certification_database_is_accepted():
    assert cert.assert_certification_database("stima360_db_test") == "stima360_db_test"
    assert cert.assert_certification_database("  stima360_db_test  ") == "stima360_db_test"


def test_6_the_guard_runs_before_any_write():
    """Non basta che la guardia esista: deve stare prima del primo INSERT.

    Provato eseguendo davvero il preflight con un DB_NAME sbagliato e
    verificando che nessuna istruzione di scrittura sia mai partita.
    """
    report, _ = quiet_report()
    database = fake_database(agencies=AGENCIES)
    env = {"DB_NAME": "stima360_db", "RENDER_GIT_BRANCH": cert.APPROVED_BRANCH,
           "RENDER_EXTERNAL_URL": "https://test.example"}

    # `approved_commit` volutamente irraggiungibile: se la guardia sul database
    # non fosse la prima cosa, questo controllo la mascherebbe.
    with pytest.raises(cert.GuardFailure):
        cert.preflight(report, database, env, approved_commit="qualunque")

    assert report.rows == [] or report.rows[0][1] == "0.1"
    executed = " ".join(database.state.get("sql", [])).upper()
    assert "INSERT" not in executed and "DELETE" not in executed


def test_7_a_connection_to_another_database_is_refused_even_if_db_name_lies(monkeypatch):
    """DB_NAME e' una variabile; la connessione reale e' l'autorita'."""
    report, _ = quiet_report()
    database = fake_database(database="stima360_db", agencies=AGENCIES)
    monkeypatch.setattr(cert, "_git", lambda *a: "abc123"
                        if a[0] == "rev-parse" else cert.APPROVED_BRANCH)
    env = {"DB_NAME": cert.REQUIRED_DB_NAME, "RENDER_GIT_BRANCH": cert.APPROVED_BRANCH,
           "RENDER_EXTERNAL_URL": "https://test.example"}

    with pytest.raises(cert.CheckFailed):
        cert.preflight(report, database, env, approved_commit="abc123")

    failed = [ident for kind, ident, _ in report.rows if kind == cert.FAIL]
    assert "0.2" in failed
    executed = " ".join(database.state.get("sql", [])).upper()
    assert "INSERT" not in executed


# ---------------------------------------------------------------------------
# 8 - il cleanup avviene anche dopo un fallimento
# ---------------------------------------------------------------------------

def _run_with_failure(monkeypatch, where, **db_state):
    """Esegue `run()` con un preflight superato e una prova che esplode."""
    report, stream = quiet_report()
    database = fake_database(agencies=AGENCIES, **db_state)
    monkeypatch.setattr(cert, "_git",
                        lambda *a: "abc123" if a[0] == "rev-parse" else cert.APPROVED_BRANCH)
    monkeypatch.setattr(cert, "read_agencies", lambda r, d: (AGENCIES[0], AGENCIES[1]))
    monkeypatch.setattr(cert.Database, "current_database",
                        lambda self: cert.REQUIRED_DB_NAME)

    class Exploding(cert.HttpProbe):
        def request(self, method, path, *, jar=None, basic=None, payload=None):
            if path == cert.PUBLIC:
                return cert.Response(200, {}, b"{}")
            raise where

    def certify_that_fails(*args, **kwargs):
        # Un operatore viene creato PRIMA del fallimento: e' il caso che conta.
        report_, _http, operators, agency_a = args[0], args[1], args[2], args[3]
        operators.create(agency_a)
        raise where

    monkeypatch.setattr(cert, "certify", certify_that_fails)
    env = {"DB_NAME": cert.REQUIRED_DB_NAME, "RENDER_GIT_BRANCH": cert.APPROVED_BRANCH,
           "RENDER_EXTERNAL_URL": "https://test.example",
           "ADMIN_USER": "u", "ADMIN_PASS": "p"}
    code = cert.run(report, database, env, "abc123", http_factory=Exploding)
    return code, report, database, stream


def test_8_a_failed_check_still_deletes_the_temporary_rows(monkeypatch):
    code, report, database, _ = _run_with_failure(
        monkeypatch, cert.CheckFailed("prova fallita"))

    deletes = " ".join(database.state.get("deletes", []))
    for table in ("operator_sessions", "agency_memberships", "operator_users"):
        assert table in deletes, f"il cleanup non ha toccato {table}"
    assert code != 0


def test_9_an_unexpected_exception_still_deletes_the_temporary_rows(monkeypatch):
    """Non solo i fallimenti previsti: anche un errore che nessuno aspettava."""
    code, report, database, _ = _run_with_failure(
        monkeypatch, RuntimeError("qualcosa di imprevisto"))

    deletes = " ".join(database.state.get("deletes", []))
    assert "operator_users" in deletes
    assert code == 1
    assert any(ident == "RUN" for kind, ident, _ in report.rows if kind == cert.FAIL)


def test_9b_a_keyboard_interrupt_still_deletes_the_temporary_rows(monkeypatch):
    """Il motivo per cui il cleanup sta in un `finally` e non semplicemente
    dopo il try.

    `except Exception` copre tutto il resto, quindi spostare il cleanup subito
    dopo il blocco si comporterebbe allo stesso modo in ogni caso previsto. La
    differenza e' qui: un Ctrl-C su una richiesta HTTP lenta, o un SystemExit
    da una libreria, non sono Exception - risalgono, e senza il `finally` si
    porterebbero via due operatori attivi lasciati nel database.
    """
    with pytest.raises(KeyboardInterrupt):
        _run_with_failure(monkeypatch, KeyboardInterrupt())

    # La stessa cosa, ma osservando il database: il run viene rifatto qui
    # perche' l'eccezione impedisce a _run_with_failure di restituire.
    report, _ = quiet_report()
    database = fake_database(residue={"users": 0, "memberships": 0, "sessions": 0})
    operators = cert.TemporaryOperators(database, report)

    try:
        try:
            operators.create(AGENCIES[0])
            raise KeyboardInterrupt
        finally:
            operators.cleanup()
    except KeyboardInterrupt:
        pass

    deletes = " ".join(database.state.get("deletes", []))
    assert "operator_users" in deletes


def test_9c_the_cleanup_is_lexically_inside_a_finally():
    """L'osservazione sopra, fissata anche staticamente: e' la struttura del
    sorgente a garantirla, non l'ordine delle istruzioni."""
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    run_fn = next(n for n in tree.body
                  if isinstance(n, ast.FunctionDef) and n.name == "run")
    in_finally = [
        ast.unparse(stmt)
        for node in ast.walk(run_fn) if isinstance(node, ast.Try)
        for stmt in node.finalbody
    ]
    assert any("operators.cleanup()" in stmt for stmt in in_finally), in_finally


def test_10_the_deletes_run_in_dependency_order(monkeypatch):
    """Sessioni, poi membership, poi utenti: un fallimento parziale resta
    attribuibile alla tabella giusta."""
    _code, _report, database, _ = _run_with_failure(
        monkeypatch, cert.CheckFailed("prova fallita"))

    order = [
        table
        for statement in database.state.get("deletes", [])
        for table in ("operator_sessions", "agency_memberships", "operator_users")
        if f"FROM {table}" in statement
    ]
    assert order[:3] == ["operator_sessions", "agency_memberships", "operator_users"]


def test_11_the_cleanup_never_raises_on_a_dead_database():
    """Se il database cade proprio durante il cleanup, lo script deve
    dichiararlo - non morire con una traccia che nasconde il verdetto."""
    report, _ = quiet_report()
    database = fake_database(explode_on="DELETE FROM operator_sessions")
    operators = cert.TemporaryOperators(database, report)
    operators.created_ids.append(1)

    operators.cleanup()          # non solleva

    assert report.exit_code == 1
    assert any(ident == "CLEAN-DB" for kind, ident, _ in report.rows if kind == cert.FAIL)


# ---------------------------------------------------------------------------
# 12 - un cleanup incompleto non puo' produrre PASS
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("residue", [
    {"users": 1, "memberships": 0, "sessions": 0},
    {"users": 0, "memberships": 1, "sessions": 0},
    {"users": 0, "memberships": 0, "sessions": 1},
    {"users": 2, "memberships": 2, "sessions": 3},
])
def test_12_leftover_rows_of_any_kind_are_a_failure(residue):
    report, _ = quiet_report()
    database = fake_database(residue=residue, delete_rowcount=0)
    operators = cert.TemporaryOperators(database, report)
    operators.created_ids.extend([1, 2])

    operators.cleanup()

    assert report.exit_code == 1
    assert report.verdict.startswith("FAIL")
    assert not any(ident == "CLEAN-CHK" for kind, ident, _ in report.rows
                   if kind == cert.PASS)


def test_13_a_clean_cleanup_is_the_only_way_to_pass():
    report, _ = quiet_report()
    database = fake_database(residue={"users": 0, "memberships": 0, "sessions": 0})
    operators = cert.TemporaryOperators(database, report)
    operators.created_ids.extend([1, 2])

    operators.cleanup()

    assert report.exit_code == 0
    assert report.verdict.startswith("PASS")


def test_14_a_cleanup_failure_outranks_every_passing_check():
    """Venti prove superate non compensano una riga rimasta nel database."""
    report, _ = quiet_report()
    for index in range(20):
        report.note(f"x{index}", "prova superata")
    assert report.exit_code == 0

    report.fail("CLEAN-CHK", "cleanup INCOMPLETO")

    assert report.exit_code == 1
    assert report.verdict == "FAIL"


def test_15_blocked_is_never_pass_and_never_outranks_fail():
    report, _ = quiet_report()
    report.note("a", "ok")
    report.blocked("b", "credenziale non disponibile")
    assert report.exit_code == 2
    assert "INCOMPLETO" in report.verdict

    report.fail("c", "rotto")
    assert report.exit_code == 1, "un FAIL insieme a un BLOCKED e' un run fallito"


def _run_to_completion(monkeypatch, database, certify=None, http=None):
    """Esegue `run()` con preflight e prove neutralizzate, per osservare il DB."""
    report, stream = quiet_report()
    monkeypatch.setattr(cert, "_git",
                        lambda *a: "abc123" if a[0] == "rev-parse" else cert.APPROVED_BRANCH)
    monkeypatch.setattr(cert.Database, "current_database",
                        lambda self: cert.REQUIRED_DB_NAME)
    monkeypatch.setattr(cert, "read_agencies", lambda r, d: (AGENCIES[0], AGENCIES[1]))
    monkeypatch.setattr(cert, "certify", certify or (lambda *a, **k: None))
    monkeypatch.setattr(cert, "scan_for_leaks", lambda *a, **k: None)

    class Ok(cert.HttpProbe):
        def request(self, method, path, *, jar=None, basic=None, payload=None):
            return cert.Response(200, {}, b"{}")

    env = {"DB_NAME": cert.REQUIRED_DB_NAME, "RENDER_GIT_BRANCH": cert.APPROVED_BRANCH,
           "RENDER_EXTERNAL_URL": "https://test.example"}
    code = cert.run(report, database, env, "abc123", http_factory=http or Ok)
    return code, report, stream


def test_16_leftovers_stop_the_run_without_creating_or_deleting(monkeypatch):
    """Righe di certificazione gia' presenti: si ferma, e non tocca niente.

    Rimuoverle da sole sarebbe la cosa comoda e la cosa sbagliata. Da qui non
    si distingue il residuo di un run finito male ieri da una certificazione
    avviata dieci secondi fa in un'altra shell, e le due chiedono risposte
    opposte. Nel dubbio: FAIL, nessun INSERT, nessuna DELETE, e la decisione
    passa a una persona.
    """
    other_run = {900: f"{cert.CERT_PREFIX}deadbeef-1111{cert.CERT_DOMAIN}"}
    database = fake_database(agencies=AGENCIES, users=dict(other_run))

    # `certify` crea davvero: se il run non si fermasse, l'INSERT che questa
    # prova nega comparirebbe. Con uno stub inerte la prova passerebbe anche
    # dopo aver tolto l'arresto, che e' esattamente cio' che deve intercettare.
    def creating_certify(report_, http_, operators, agency_a, *rest):
        operators.create(agency_a)

    code, report, _ = _run_to_completion(monkeypatch, database,
                                         certify=creating_certify)

    assert code == 1
    assert any(ident == "0.15" for kind, ident, _ in report.rows if kind == cert.FAIL)

    executed = " ".join(database.state.get("sql", [])).upper()
    assert "INSERT" not in executed, "ha creato un operatore nonostante il residuo"
    assert "DELETE" not in executed, "ha cancellato righe che non ha creato"
    assert database.state["users"] == other_run, "la riga altrui e' stata toccata"


def test_16b_a_concurrent_run_survives_this_run_cleanup(monkeypatch):
    """LA REGRESSIONE CHE HA MOTIVATO IL CAMBIO.

    Due certificazioni in parallelo condividono il prefisso dell'email. Se il
    cleanup cancellasse per prefisso, la prima a finire porterebbe via gli
    operatori della seconda mentre li sta ancora usando - e quella fallirebbe
    con 401 inspiegabili, su un difetto che non e' suo.

    Qui l'operatore dell'altro run e' gia' nella tabella quando questo crea i
    propri. Il fake cancella davvero, quindi la sopravvivenza si legge dalla
    tabella e non dal testo della query.
    """
    concurrent_id = 900
    concurrent_email = f"{cert.CERT_PREFIX}aaaabbbbcccc-9999{cert.CERT_DOMAIN}"

    report, _ = quiet_report()
    database = fake_database()
    operators = cert.TemporaryOperators(database, report)

    mine = [operators.create(AGENCIES[0])["id"], operators.create(AGENCIES[1])["id"]]
    # L'altro run inserisce il proprio operatore mentre questo sta lavorando.
    database.state["users"][concurrent_id] = concurrent_email

    operators.cleanup()

    survivors = database.state["users"]
    assert concurrent_id in survivors, (
        "il cleanup ha cancellato l'operatore di un run concorrente"
    )
    assert survivors[concurrent_id] == concurrent_email
    for identifier in mine:
        assert identifier not in survivors, "un proprio operatore e' sopravvissuto"

    # E il verdetto resta PASS: il conteggio finale guarda i propri id, non il
    # prefisso, quindi la riga altrui non lo fa fallire.
    assert report.exit_code == 0


def test_16c_no_delete_is_ever_written_by_email(monkeypatch):
    """Il criterio e' l'id. Statico, perche' una DELETE per email potrebbe
    non comparire in nessun percorso esercitato dalle prove sopra."""
    source = SCRIPT.read_text(encoding="utf-8")
    deletes = re.findall(r"DELETE FROM [a-z_]+ WHERE [^\"]+", source)
    assert deletes, "nessuna DELETE trovata: il pattern e' cambiato"
    for statement in deletes:
        assert "IN %s" in statement, statement
        for forbidden in ("email", "LIKE", "run_id"):
            assert forbidden not in statement, f"DELETE per {forbidden}: {statement}"


def test_16d_the_residue_check_is_scoped_to_this_run_ids():
    """Contare per prefisso trasformerebbe il lavoro altrui in un fallimento
    di questo run - o spingerebbe a cancellarlo per far tornare il conto."""
    report, _ = quiet_report()
    database = fake_database()
    operators = cert.TemporaryOperators(database, report)
    operators.create(AGENCIES[0])

    # Un run concorrente lascia una riga: non deve entrare nel conteggio.
    database.state["users"][901] = f"{cert.CERT_PREFIX}altro-2222{cert.CERT_DOMAIN}"
    operators.cleanup()

    assert report.exit_code == 0, [r for r in report.rows if r[0] == cert.FAIL]

    residue_queries = [q for q in database.state["sql"] if "AS users" in q]
    assert residue_queries, "il controllo finale non e' stato eseguito"
    for query in residue_queries:
        assert "LIKE" not in query and "email" not in query, query


def test_16f_with_nothing_created_the_cleanup_claims_nothing_verified():
    """Se non ha creato niente non c'e' niente da cancellare - e nemmeno
    niente da verificare. Il ramo deve dirlo, non riusare l'etichetta del
    controllo finale: leggere CLEAN-CHK in un report significa che il
    conteggio residuo e' stato interrogato, e qui non lo e' stato.
    """
    report, _ = quiet_report()
    database = fake_database()
    operators = cert.TemporaryOperators(database, report)

    operators.cleanup()

    idents = [ident for _kind, ident, _ in report.rows]
    assert idents == ["CLEAN-DB"], idents
    assert "CLEAN-CHK" not in idents, (
        "dichiara verificato un conteggio che non ha eseguito"
    )
    assert database.state.get("sql") is None, "ha interrogato il database per nulla"


def test_16e_each_run_has_its_own_identifier():
    report, _ = quiet_report()
    database = fake_database()
    identifiers = {
        cert.TemporaryOperators(database, report).run_id for _ in range(50)
    }
    assert len(identifiers) == 50, "i run_id devono essere distinti"

    operators = cert.TemporaryOperators(database, report)
    created = operators.create(AGENCIES[0])
    assert operators.run_id in created["email"], (
        "il run_id deve comparire nell'email, per rendere leggibile la provenienza"
    )


# ---------------------------------------------------------------------------
# 17 - nessuna credenziale hardcoded o stampata
# ---------------------------------------------------------------------------

def test_17_no_credential_is_hardcoded():
    """Nessuna password, token o hash letterale nel sorgente.

    L'unica stringa che somiglia a una credenziale e' il Basic deliberatamente
    sbagliato, che esiste per essere rifiutato: e' asserito per nome, cosi' che
    aggiungerne un altro non passi inosservato.
    """
    source = SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(source)

    suspicious = re.compile(
        r"(pbkdf2_sha256\$|[A-Za-z0-9+/]{40,}={0,2}|[0-9a-f]{40,})"
    )
    allowed = {
        "questa-non-e-la-password-di-admin",   # Basic errato, deve fallire
        "not-a-real-session-token",            # cookie invalido, deve fallire
        "prova-di-forma",                      # argomento del controllo di forma
        "pbkdf2_sha256$",                      # prefisso di formato, non un hash
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value
            if value in allowed or len(value) > 400:      # docstring
                continue
            assert not suspicious.search(value), f"letterale sospetto: {value[:40]!r}"

    # Le credenziali arrivano solo dall'ambiente e dal generatore casuale.
    assert 'env.get("ADMIN_USER")' in source
    assert 'env.get("ADMIN_PASS")' in source
    assert "secrets.token_urlsafe" in source
    assert "os.environ" in source and source.count("os.environ") == 1


def test_18_the_generated_password_never_reaches_the_output():
    """La password di un operatore temporaneo esiste per essere inviata al
    server, non per essere letta da chi guarda la shell."""
    report, stream = quiet_report()
    database = fake_database(residue={"users": 0, "memberships": 0, "sessions": 0})
    operators = cert.TemporaryOperators(database, report)

    created = operators.create(AGENCIES[0])
    operators.cleanup()

    printed = stream.getvalue()
    assert created["password"] not in printed
    assert created["password"] in operators.secrets, (
        "la password deve restare disponibile allo scanner di leak"
    )
    # L'hash persistito non e' il valore in chiaro.
    inserted = database.state["inserted"][0]
    assert created["password"] not in inserted
    assert inserted[2].startswith("pbkdf2_sha256$")


def test_19_no_report_line_can_carry_a_secret():
    """Nessuna riga del report interpola una password o un token.

    Statico e sull'AST: cerca le chiamate a record/note/fail/check/blocked e
    verifica che nessuna nomini un valore sensibile dentro il testo.
    """
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    forbidden = ("password", "token", "secret", "ADMIN_PASS", "cookie_value")
    offenders = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"record", "note", "fail", "check", "blocked"}):
            continue
        for argument in node.args:
            if not isinstance(argument, ast.JoinedStr):
                continue
            for piece in argument.values:
                if not isinstance(piece, ast.FormattedValue):
                    continue
                rendered = ast.unparse(piece.value)
                if any(word in rendered for word in forbidden):
                    offenders.append(rendered)
    assert offenders == [], offenders


def test_20_the_leak_scanner_treats_admin_user_as_public():
    """ADMIN_USER e' un identificativo e puo' comparire nei dati di un'agenzia.
    Trattarlo da segreto renderebbe la certificazione rossa per un motivo
    inventato."""
    source = SCRIPT.read_text(encoding="utf-8")
    block = source[source.index("if admin_pass:"):source.index("leftovers =")]
    assert "operators.secrets.append(admin_pass)" in block
    assert "admin_user" not in block


def test_21_the_leak_scanner_actually_finds_a_secret():
    """Lo scanner deve poter fallire, altrimenti il suo PASS non dice nulla."""
    report, _ = quiet_report()

    class Probe(cert.HttpProbe):
        def __init__(self):
            self.exchanges = [("GET /x", 200, {}, b'{"eco": "super-segreto"}')]

    with pytest.raises(cert.CheckFailed):
        cert.scan_for_leaks(report, Probe(), ["super-segreto"])

    report_ok, _ = quiet_report()
    cert.scan_for_leaks(report_ok, Probe(), ["un-altro-valore"])
    assert report_ok.exit_code == 0


def test_22_the_set_cookie_header_is_excluded_from_the_scan():
    """Il token DEVE stare nel Set-Cookie della login: e' il suo posto. Lo
    scanner lo esclude di proposito, e questo lo fissa."""
    report, _ = quiet_report()

    class Probe(cert.HttpProbe):
        def __init__(self):
            self.exchanges = [(
                "POST /api/operator-auth/login", 204,
                {"Set-Cookie": f"{cert.COOKIE_NAME}=il-token; HttpOnly"}, b"",
            )]

    cert.scan_for_leaks(report, Probe(), ["il-token"])
    assert report.exit_code == 0


# ---------------------------------------------------------------------------
# 23 - il dominio non viene toccato
# ---------------------------------------------------------------------------

def test_23_only_authentication_tables_are_written():
    """Le sole tabelle scritte sono le tre dell'autenticazione. Derivato dal
    sorgente, non da una lista tenuta a mano altrove."""
    source = SCRIPT.read_text(encoding="utf-8")
    written = set(re.findall(
        r"(?:INSERT\s+INTO|DELETE\s+FROM|UPDATE)\s+([a-z_]+)", source, re.IGNORECASE))
    assert written == {"operator_users", "agency_memberships", "operator_sessions"}, written


def test_24_agencies_are_read_and_never_written():
    source = SCRIPT.read_text(encoding="utf-8")
    assert re.search(r"SELECT[^;]*FROM agencies", source)
    for verb in ("INSERT INTO agencies", "UPDATE agencies", "DELETE FROM agencies"):
        assert verb not in source, verb


def test_25_the_temporary_operator_is_an_agent_not_an_owner():
    """migrations/027 ammette un solo agency_owner attivo per agenzia: un
    operatore di certificazione con quel ruolo non entrerebbe, e in TEST
    rischierebbe di scontrarsi con l'owner vero."""
    assert cert.CERT_ROLE == "agent"

    report, _ = quiet_report()
    database = fake_database()
    operators = cert.TemporaryOperators(database, report)
    operators.create(AGENCIES[1])

    _agency_id, _user_id, role, *_ = database.state["memberships"][0]
    assert role == "agent"


def test_26_the_generated_identity_is_collision_safe():
    report, _ = quiet_report()
    database = fake_database()
    operators = cert.TemporaryOperators(database, report)

    emails = {operators.create(AGENCIES[0])["email"] for _ in range(25)}
    assert len(emails) == 25, "le email generate devono essere distinte"
    for email in emails:
        assert email.startswith(cert.CERT_PREFIX)
        assert email.endswith(cert.CERT_DOMAIN)

    checks = [s for s in database.state["sql"]
              if s.upper().startswith("SELECT 1 FROM OPERATOR_USERS")]
    assert len(checks) == 25, "ogni creazione deve fare il proprio collision check"


def test_27_a_collision_stops_the_run_before_inserting():
    report, _ = quiet_report()
    database = fake_database(collide=True)
    operators = cert.TemporaryOperators(database, report)

    with pytest.raises(cert.CheckFailed):
        operators.create(AGENCIES[0])

    assert database.state.get("inserted") is None
    assert operators.created_ids == []


def test_28_a_user_created_before_a_later_failure_is_still_tracked():
    """L'id viene registrato fra i due INSERT: se la membership fallisce,
    l'utente esiste comunque e il cleanup deve poterlo raggiungere."""
    report, _ = quiet_report()
    database = fake_database(explode_on="INSERT INTO agency_memberships")
    operators = cert.TemporaryOperators(database, report)

    with pytest.raises(RuntimeError):
        operators.create(AGENCIES[0])

    assert operators.created_ids, "l'utente inserito non e' stato registrato"
