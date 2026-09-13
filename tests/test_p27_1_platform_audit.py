"""P27-1 - il writer di platform_audit_log.

Decisione D2: il writer ha una connessione e una transazione PROPRIE, e
committa da solo. La riga sopravvive quindi all'esito dell'operazione che
descrive - e' cio' che permette al registro di conservare i tentativi negati,
gli errori e le operazioni iniziate e poi fallite, e non soltanto quello che e'
stato committato.

Mappa:

    B1-B3   il confine transazionale: connessione propria, commit, rollback
    B4-B6   la validazione dei letterali (azione, esito)
    B7-B9   l'attore
    B10-B12 la forma della riga e dell'istruzione
    B13-B15 le proprieta' strutturali: nessun UPDATE, nessun DELETE, nessuna
            lettura, niente errori inghiottiti
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from operator_auth.context import OperatorContext
from platform_admin import audit, repository
from platform_admin.enums import (
    ANONYMOUS_ACTOR,
    AUDIT_RESULTS,
    RESULT_DENIED,
    RESULT_ERROR,
    RESULT_SUCCESS,
)
from platform_admin.exceptions import PlatformAuditUnavailable

ROOT = Path(__file__).resolve().parents[1]


class _Cursor:
    def __init__(self, rows=None, fail=False):
        self.calls = []
        self.fail = fail
        self._rows = rows if rows is not None else [{"id": 12345}]

    def execute(self, sql, params=None):
        if self.fail:
            raise RuntimeError("la connessione al database e' caduta")
        self.calls.append((" ".join(str(sql).split()), params))

    def fetchone(self):
        return self._rows[0]

    def close(self):
        self.closed = True


class _Connection:
    def __init__(self):
        self.committed = 0
        self.rolled_back = 0
        self.closed = 0

    def cursor(self, **kwargs):
        raise AssertionError("il repository non deve aprire un cursore proprio")

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1

    def close(self):
        self.closed += 1


@pytest.fixture
def writer(monkeypatch):
    """Sostituisce il contextmanager, non `get_connection`.

    Cosi' si osserva quello che il writer FA con la transazione - commit,
    rollback - invece di doverlo dedurre da una connessione finta.
    """
    from contextlib import contextmanager

    state = {"cursor": _Cursor(), "conn": _Connection(), "entered": 0}

    @contextmanager
    def _cursor():
        state["entered"] += 1
        conn = state["conn"]
        try:
            yield conn, state["cursor"]
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    monkeypatch.setattr(audit, "platform_audit_cursor", _cursor)
    return state


def _operator(user_id=7, **overrides) -> OperatorContext:
    base = dict(
        user_id=user_id,
        agency_id=None,
        role=None,
        is_platform_admin=True,
        session_id=1,
        auth_channel="operator_session",
    )
    base.update(overrides)
    return OperatorContext(**base)


# ---------------------------------------------------------------------------
# B1-B3 - il confine transazionale
# ---------------------------------------------------------------------------

def test_b1_the_writer_uses_its_own_transaction_and_commits_it(writer):
    audit.record(action="platform.admission", actor=_operator())
    assert writer["entered"] == 1
    assert writer["conn"].committed == 1
    assert writer["conn"].rolled_back == 0


def test_b2_the_writer_returns_the_id_of_the_row_it_wrote(writer):
    assert audit.record(action="platform.admission", actor=_operator()) == 12345


def test_b3_a_failed_write_rolls_back_and_raises_a_domain_error(writer):
    writer["cursor"] = _Cursor(fail=True)
    with pytest.raises(PlatformAuditUnavailable):
        audit.record(action="platform.admission", actor=_operator())
    assert writer["conn"].rolled_back == 1
    assert writer["conn"].committed == 0


def test_b3_the_database_message_is_not_carried_in_the_domain_error(writer):
    """Il testo originale resta nella causa, non nel messaggio.

    Chi lo rilancia verso HTTP non deve poterlo stampare per sbaglio: un errore
    di database porta nomi di oggetti e valori.
    """
    writer["cursor"] = _Cursor(fail=True)
    with pytest.raises(PlatformAuditUnavailable) as excinfo:
        audit.record(action="platform.admission", actor=_operator())
    assert "connessione al database" not in str(excinfo.value)
    assert excinfo.value.__cause__ is not None


def test_b3_the_writer_never_swallows_a_failure(writer):
    """Controllo negativo: un writer che logga e basta renderebbe impossibile
    la regola "un'operazione non registrabile non avviene"."""
    writer["cursor"] = _Cursor(fail=True)
    with pytest.raises(PlatformAuditUnavailable):
        audit.record(action="platform.admission")


# ---------------------------------------------------------------------------
# B4-B6 - la validazione dei letterali
# ---------------------------------------------------------------------------

def test_b4_an_action_outside_the_namespace_is_refused_before_any_write(writer):
    with pytest.raises(ValueError):
        audit.record(action="login", actor=_operator())
    assert writer["entered"] == 0, "una riga e' stata tentata comunque"


def test_b5_an_unknown_result_is_refused_before_any_write(writer):
    with pytest.raises(ValueError):
        audit.record(action="platform.x", result="parziale")
    assert writer["entered"] == 0


@pytest.mark.parametrize("result", AUDIT_RESULTS)
def test_b6_the_three_results_are_all_writable(writer, result):
    """D2 in una riga: `denied` ed `error` non sono gestione di errori, sono
    le righe che il registro esiste per conservare."""
    audit.record(action="platform.admission", result=result)
    assert writer["cursor"].calls[0][1][3] == result


def test_b6_the_application_results_match_the_database_check_constraint():
    """Le tre costanti e il CHECK di 057 devono dire la stessa cosa.

    Se divergessero, un esito valido per l'applicazione verrebbe rifiutato dal
    database nel momento peggiore: mentre si registra un rifiuto.
    """
    sql = (ROOT / "migrations" / "057_p27_platform_audit_log.sql").read_text(
        encoding="utf-8"
    )
    for result in AUDIT_RESULTS:
        assert f"'{result}'" in sql, result
    assert "result IN ('success', 'denied', 'error')" in sql


# ---------------------------------------------------------------------------
# B7-B9 - l'attore
# ---------------------------------------------------------------------------

def test_b7_the_actor_label_reuses_the_projects_existing_format():
    """Non una seconda grafia dello stesso attore.

    `operator_auth.dependencies.audit_actor` e' la definizione di quel formato
    dal P26-5. Due registri che scrivono lo stesso attore in due modi non si
    correlano.
    """
    from operator_auth.dependencies import audit_actor

    context = _operator(user_id=42)
    assert audit.actor_label(context) == audit_actor(context) == "operator:42"


def test_b8_an_absent_actor_is_recorded_as_anonymous_not_as_null(writer):
    audit.record(action="platform.admission", actor=None)
    params = writer["cursor"].calls[0][1]
    assert params[0] is None, "actor_user_id deve essere NULL"
    assert params[1] == ANONYMOUS_ACTOR, "actor_label non deve mai essere NULL"


def test_b8_a_context_without_a_user_id_is_anonymous_too(writer):
    """Il canale Basic legacy produceva un contesto senza persona dietro.

    Non raggiunge questa superficie (P26-5 lo ha rimosso), ma il writer e' una
    funzione pubblica e deve avere una risposta per ogni input.
    """
    audit.record(action="platform.admission", actor=_operator(user_id=None))
    assert writer["cursor"].calls[0][1][1] == ANONYMOUS_ACTOR


def test_b9_the_actor_label_never_carries_an_email(writer):
    audit.record(action="platform.admission", actor=_operator(user_id=42))
    assert "@" not in writer["cursor"].calls[0][1][1]


# ---------------------------------------------------------------------------
# B10-B12 - la forma della riga e dell'istruzione
# ---------------------------------------------------------------------------

def test_b10_the_statement_is_an_insert_with_returning_and_binds_every_value(
    writer,
):
    audit.record(
        action="platform.agency.create",
        actor=_operator(user_id=7),
        result=RESULT_SUCCESS,
        # Valori deliberatamente improbabili: un valore come "agency" sarebbe
        # una sottostringa del nome di colonna `target_agency_id`, e il
        # controllo di non-interpolazione qui sotto passerebbe per caso
        # sbagliato - proprio il modo in cui un test smette di provare cio' che
        # dice di provare.
        target_type="zzqq-tipo",
        target_id="zzqq-99",
        target_agency_id=987654,
        metadata={"path": "/api/platform/me"},
    )
    sql, params = writer["cursor"].calls[0]
    assert sql.upper().startswith("INSERT INTO PLATFORM_AUDIT_LOG")
    assert "RETURNING ID" in sql.upper()
    assert "UPDATE" not in sql.upper()
    assert len(params) == 8
    assert sql.count("%s") == 8, sql
    # Nessun valore viaggia nel testo dell'istruzione.
    for value in ("zzqq-tipo", "zzqq-99", "987654", "operator:7"):
        assert value not in sql, (sql, value)


def test_b11_a_target_id_is_stored_as_text(writer):
    audit.record(action="platform.x", target_type="agency", target_id=99)
    assert writer["cursor"].calls[0][1][5] == "99"


def test_b11_an_absent_target_id_stays_null_rather_than_becoming_none(writer):
    audit.record(action="platform.x")
    assert writer["cursor"].calls[0][1][5] is None


def _unwrap_json(value):
    """Il valore trasportato da `psycopg2.extras.Json`, o il valore stesso.

    Il conftest sostituisce `Json` con l'identita' quando psycopg2 non e'
    installato, quindi il test deve reggere entrambe le forme senza asserire
    quale delle due sia in uso.
    """
    return getattr(value, "adapted", value)


def test_b12_absent_metadata_becomes_an_empty_object_not_null(writer):
    audit.record(action="platform.x")
    assert _unwrap_json(writer["cursor"].calls[0][1][7]) == {}


def test_b12_metadata_travels_as_json_not_as_a_string(writer):
    """Come lo scrive gia' owner/repository.py: e' cio' che tiene il valore
    fuori dal testo dell'istruzione."""
    audit.record(action="platform.x", metadata={"path": "/api/platform/me"})
    assert _unwrap_json(writer["cursor"].calls[0][1][7]) == {
        "path": "/api/platform/me"
    }


def test_b12_the_writer_takes_keyword_arguments_only():
    """Nove valori, di cui tre stringhe adiacenti.

    In posizionale, scambiare `action` e `result` o `target_type` e `target_id`
    produrrebbe una riga plausibile e sbagliata che nessun vincolo intercetta.
    """
    for function in (audit.record, repository.insert_audit_entry):
        signature = inspect.signature(function)
        positional = [
            name
            for name, parameter in signature.parameters.items()
            if parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
        ]
        assert positional in ([], ["cur"]), (function.__name__, positional)


# ---------------------------------------------------------------------------
# B13-B15 - le proprieta' strutturali
# ---------------------------------------------------------------------------

def test_b13_the_repository_offers_no_way_to_modify_or_remove_a_row():
    """Append-only anche a livello di applicazione, non solo di database.

    Il trigger della 057 e' la difesa; l'assenza di una funzione e' il motivo
    per cui non ci si arriva mai.
    """
    source = (ROOT / "platform_admin" / "repository.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    assert functions == ["insert_audit_entry"], functions

    statements = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    executable = " ".join(statements).upper()
    assert "UPDATE PLATFORM_AUDIT_LOG" not in executable
    assert "DELETE FROM PLATFORM_AUDIT_LOG" not in executable
    assert "TRUNCATE" not in executable


def test_b14_the_repository_opens_no_connection_of_its_own():
    """Il confine transazionale e' una decisione di prodotto (D2) e sta in un
    posto solo: platform_admin/database.py."""
    tree = ast.parse(
        (ROOT / "platform_admin" / "repository.py").read_text(encoding="utf-8")
    )
    # Sugli IMPORT e sui nomi usati, non sul testo: il docstring del modulo
    # nomina `platform_audit_cursor` di proposito, per spiegare dove vive il
    # confine transazionale, e un controllo su sottostringa lo leggerebbe come
    # una violazione.
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    used = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}

    for forbidden in ("get_connection", "platform_audit_cursor", "connect"):
        assert forbidden not in imported, forbidden
        assert forbidden not in used, forbidden


def test_b15_the_cursor_helper_commits_on_success_and_rolls_back_on_failure(
    monkeypatch,
):
    """Il contextmanager vero, non quello della fixture."""
    from platform_admin import database as platform_database

    connection = _Connection()
    connection.cursor = lambda **kwargs: _Cursor()
    monkeypatch.setattr(platform_database, "get_connection", lambda: connection)

    with platform_database.platform_audit_cursor() as (_, cur):
        cur.execute("SELECT 1")
    assert (connection.committed, connection.rolled_back, connection.closed) == (1, 0, 1)

    connection_two = _Connection()
    connection_two.cursor = lambda **kwargs: _Cursor()
    monkeypatch.setattr(platform_database, "get_connection", lambda: connection_two)
    with pytest.raises(RuntimeError):
        with platform_database.platform_audit_cursor() as (_, cur):
            raise RuntimeError("boom")
    assert (connection_two.committed, connection_two.rolled_back) == (0, 1)
    assert connection_two.closed == 1


def test_b15_the_helper_has_no_commit_false_escape_hatch():
    """`operator_cursor(commit=False)` esiste perche' ha molti usi con politiche
    diverse. Questo ne ha uno solo, e un parametro `commit=False` sarebbe un
    modo per scrivere un audit che non viene salvato."""
    from platform_admin.database import platform_audit_cursor

    signature = inspect.signature(platform_audit_cursor)
    assert list(signature.parameters) == [], signature
