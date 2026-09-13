"""P27-2 - gestione agenzie sulla superficie Platform.

Quattro endpoint, nessuna DELETE, e una regola che vale piu' dei quattro
endpoint messi insieme:

    nessuna modifica amministrativa viene committata
    se il suo audit non e' stato scritto.

Mappa:

    A   repository: la forma del SQL, e cio' che il modulo NON fa
    B   service: l'ordine fra scrittura, audit e commit
    C   HTTP: contratti, validazioni, codici
    D   perimetro: cosa P27-2 non ha toccato

Nessun database. Il repository e' osservato con un cursore che registra; il
service e il router girano VERI sopra un magazzino in memoria. L'esecuzione su
un PostgreSQL reale e' stata fatta a mano durante lo sviluppo ed e' riportata
nel report: qui resta cio' che sorveglia la suite a ogni run.
"""
from __future__ import annotations

import ast
import inspect
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from psycopg2 import errors

from operator_auth.context import OperatorContext
from platform_admin import agencies_repository, agencies_service
from platform_admin import audit as platform_audit
from platform_admin import dependencies as platform_deps
from platform_admin.enums import (
    ACTION_AGENCY_CREATE,
    ACTION_AGENCY_UPDATE,
    AGENCY_NAME_MAX,
    AGENCY_SLUG_MAX,
    AGENCY_SLUG_PATTERN,
    AGENCY_STATUSES,
    RESULT_ERROR,
    RESULT_SUCCESS,
    ROUTER_PREFIX,
    TARGET_TYPE_AGENCY,
)
from platform_admin.exceptions import (
    AgencyNotFound,
    AgencySlugConflict,
    PlatformAuditUnavailable,
)
from platform_admin.router import router as platform_router

ROOT = Path(__file__).resolve().parents[1]
MIGRATION_027 = ROOT / "migrations" / "027_p26_agency_identity.sql"

PLATFORM_USER = 77
EXPIRES = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)
CREATED = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)


def _ctx(**overrides) -> OperatorContext:
    base = dict(
        user_id=PLATFORM_USER, agency_id=None, role=None,
        is_platform_admin=True, session_id=1, auth_channel="operator_session",
    )
    base.update(overrides)
    return OperatorContext(**base)


# ===========================================================================
# A - IL REPOSITORY
# ===========================================================================

class RecordingCursor:
    """Registra ogni istruzione e restituisce righe in coda."""

    def __init__(self, rows=None):
        self.calls: list[tuple[str, object]] = []
        self._rows = list(rows or [])

    def execute(self, sql, params=None):
        self.calls.append((" ".join(str(sql).split()), params))

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    def fetchall(self):
        rows, self._rows = self._rows, []
        return rows

    @property
    def last_sql(self) -> str:
        return self.calls[-1][0]

    @property
    def last_params(self):
        return self.calls[-1][1]


AGENCY_ROW = {
    "id": 1, "name": "Agenzia A", "slug": "agenzia-a", "status": "active",
    "settings": {}, "created_at": CREATED, "updated_at": CREATED,
}


def test_a1_list_names_its_columns_and_orders_deterministically():
    """Nessun `SELECT *`.

    Una colonna aggiunta ad `agencies` da P27-4 o P27-5 non deve poter
    raggiungere un client per il solo fatto di esistere.
    """
    cur = RecordingCursor([AGENCY_ROW])
    assert agencies_repository.list_agencies(cur) == [AGENCY_ROW]
    sql = cur.last_sql
    assert "SELECT *" not in sql, sql
    for column in agencies_repository.AGENCY_COLUMNS:
        assert column in sql, column
    assert sql.endswith("ORDER BY id"), sql


def test_a2_get_is_parametrised_and_returns_none_when_absent():
    cur = RecordingCursor([AGENCY_ROW])
    assert agencies_repository.get_agency(cur, 1) == AGENCY_ROW
    assert "WHERE id = %s" in cur.last_sql
    assert cur.last_params == (1,)

    empty = RecordingCursor()
    assert agencies_repository.get_agency(empty, 999) is None


def test_a3_slug_exists_without_exclusion_looks_at_the_whole_network():
    cur = RecordingCursor([{"?column?": 1}])
    assert agencies_repository.slug_exists(cur, "agenzia-a") is True
    assert cur.last_params == ("agenzia-a",)
    # Nessun filtro sullo stato: il vincolo UNIQUE del database non ne ha, e
    # uno slug "libero" perche' l'agenzia che lo tiene e' archiviata
    # produrrebbe una INSERT rifiutata.
    assert "status" not in cur.last_sql, cur.last_sql


def test_a3_slug_exists_has_no_exclusion_parameter_any_more():
    """Era: `exclude_agency_id`, che serviva a UNA cosa sola.

    Permetteva a una PATCH di riscrivere lo slug invariato senza ricevere 409.
    Con lo slug non piu' modificabile quel caso non esiste, e il parametro e'
    stato tolto invece di restare come un'opzione che nessuno usa - un
    argomento inutilizzato e' un invito a trovargli un uso, e l'uso sarebbe
    stato il rename.
    """
    signature = inspect.signature(agencies_repository.slug_exists)
    assert list(signature.parameters) == ["cur", "slug"], signature


def test_a4_create_writes_four_columns_and_returns_the_row():
    cur = RecordingCursor([AGENCY_ROW])
    row = agencies_repository.create_agency(
        cur, name="Agenzia A", slug="agenzia-a", status="active", settings={"x": 1}
    )
    assert row == AGENCY_ROW
    sql = cur.last_sql
    assert sql.startswith("INSERT INTO agencies (name, slug, status, settings)")
    assert "RETURNING" in sql
    assert sql.count("%s") == 4, sql
    # id, created_at e updated_at non sono nominati: sono della riga, non del
    # chiamante.
    assert "created_at" not in sql.split("RETURNING")[0]


def test_a4_create_is_keyword_only():
    """Tre stringhe adiacenti. In posizionale, name e slug scambiati
    produrrebbero una riga plausibile che nessun CHECK intercetta."""
    signature = inspect.signature(agencies_repository.create_agency)
    positional = [
        name for name, p in signature.parameters.items()
        if p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    ]
    assert positional == ["cur"], positional


@pytest.mark.parametrize("fields,expected", [
    ({"name": "Nuovo"}, ["name"]),
    ({"status": "suspended"}, ["status"]),
    ({"settings": {"citta": "Alba"}}, ["settings"]),
    ({"name": "N", "status": "active", "settings": {}},
     ["name", "status", "settings"]),
])
def test_a5_update_writes_only_the_fields_it_was_given(fields, expected):
    cur = RecordingCursor([AGENCY_ROW])
    agencies_repository.update_agency(cur, 1, fields)
    sql = cur.last_sql
    for column in expected:
        assert f"{column} = %s" in sql, (column, sql)
    for column in agencies_repository.UPDATABLE_COLUMNS:
        if column not in expected:
            assert f"{column} = %s" not in sql, (column, sql)


def test_a5_update_always_stamps_updated_at_and_never_takes_it_from_the_caller():
    """`agencies` non ha un trigger che mantenga `updated_at` (la 027 non ne
    installa nessuno), quindi senza questa riga la colonna resterebbe alla data
    di creazione per sempre."""
    cur = RecordingCursor([AGENCY_ROW])
    agencies_repository.update_agency(cur, 1, {"name": "Nuovo"})
    assert "updated_at = NOW()" in cur.last_sql
    assert "updated_at" not in agencies_repository.UPDATABLE_COLUMNS
    assert cur.last_params == ["Nuovo", 1]


def test_a5_update_returns_none_when_the_id_matched_nothing():
    assert agencies_repository.update_agency(RecordingCursor(), 999, {"name": "X"}) is None


def test_a6_update_refuses_a_column_it_does_not_own():
    """Rumorosamente, non ignorandola: un campo scartato in silenzio produce
    una PATCH che risponde 200 e non ha fatto nulla."""
    cur = RecordingCursor([AGENCY_ROW])
    for campo in ({"id": 9}, {"created_at": CREATED}, {"slug": "nuovo-slug"}):
        with pytest.raises(ValueError):
            agencies_repository.update_agency(cur, 1, campo)
    assert cur.calls == [], "un'istruzione e' partita comunque"


def test_a6_no_update_statement_can_ever_write_the_slug():
    """La chiave stabile della 027 non e' scrivibile da nessun percorso UPDATE.

    Asserito sulla tupla E sul SQL prodotto: la tupla e' la regola, il SQL e'
    cio' che arriva al database, e un test che guardasse solo la prima non
    coprirebbe un `slug = %s` scritto a mano nell'istruzione.

    Il controllo e' sulla clausola SET, non sull'intera istruzione: `slug`
    compare legittimamente nel RETURNING, perche' resta parte della proiezione
    che la risposta contiene. Cercarlo ovunque avrebbe dato un rosso su una
    riga corretta - e sarebbe stato il tipo di test che si "sistema"
    allentandolo.
    """
    assert "slug" not in agencies_repository.UPDATABLE_COLUMNS

    for fields in (
        {"name": "N", "status": "active", "settings": {}},
        {column: "x" for column in agencies_repository.UPDATABLE_COLUMNS},
    ):
        cur = RecordingCursor([AGENCY_ROW])
        agencies_repository.update_agency(cur, 1, fields)
        set_clause = cur.last_sql.split(" SET ")[1].split(" WHERE ")[0]
        assert "slug" not in set_clause, set_clause
        assert "slug" in cur.last_sql, "lo slug deve restare nel RETURNING"


def test_a6_the_slug_is_still_readable_and_still_written_on_create():
    """Non modificabile non vuol dire sparito: resta nella proiezione e resta
    obbligatorio alla creazione."""
    assert "slug" in agencies_repository.AGENCY_COLUMNS
    cur = RecordingCursor([AGENCY_ROW])
    agencies_repository.create_agency(
        cur, name="A", slug="agenzia-a", status="active", settings={}
    )
    assert "slug" in cur.last_sql
    assert "agenzia-a" in cur.last_params


def test_a7_update_refuses_an_empty_field_set():
    with pytest.raises(ValueError):
        agencies_repository.update_agency(RecordingCursor(), 1, {})


def _repo_tree() -> ast.Module:
    return ast.parse(
        (ROOT / "platform_admin" / "agencies_repository.py").read_text(encoding="utf-8")
    )


def _sql_literals(tree: ast.Module) -> list[str]:
    """Le stringhe ESEGUIBILI, senza i docstring.

    Un docstring e' un `ast.Constant` come qualunque altra stringa, e i
    docstring di questo package nominano `platform_audit_log` di proposito -
    per spiegare da cosa il modulo e' tenuto separato. Contarli come SQL
    trasformerebbe la spiegazione della regola in una sua violazione, che e'
    lo stesso inciampo gia' incontrato nei test della migration 057.
    """
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(id(body[0].value))
    return [
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
        and id(n) not in docstrings
    ]


def test_a8_the_repository_opens_no_connection():
    """Il confine transazionale appartiene al service: e' l'unico posto in cui
    l'ordine "scrivi, audita, committa" puo' essere espresso."""
    names = {n.id for n in ast.walk(_repo_tree()) if isinstance(n, ast.Name)}
    imported = set()
    for node in ast.walk(_repo_tree()):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imported.update(a.name for a in node.names)
    for forbidden in ("get_connection", "platform_operation_cursor",
                      "platform_audit_cursor", "connect"):
        assert forbidden not in names, forbidden
        assert forbidden not in imported, forbidden


def test_a9_the_repository_imports_no_web_framework():
    for node in ast.walk(_repo_tree()):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("fastapi"), node.module
            assert not node.module.startswith("starlette"), node.module


def test_a10_the_repository_never_writes_an_audit_row():
    """Un repository che audita da solo produce una riga anche quando il
    chiamante poi annulla tutto."""
    source = (ROOT / "platform_admin" / "agencies_repository.py").read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert "audit" not in node.module, node.module
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert "audit" not in names


def test_a11_the_repository_names_agencies_and_no_other_table():
    """Nessuna membership, nessun operatore, nessun dato di tenant.

    P27-2 gestisce l'anagrafica della rete. Gli operatori sono P27-3 e le
    impostazioni sono P27-4; toccarli da qui sarebbe allargare la fase.
    """
    sql = " ".join(_sql_literals(_repo_tree())).upper()
    for table in ("AGENCY_MEMBERSHIPS", "OPERATOR_USERS", "OPERATOR_SESSIONS",
                  "CONTACTS", "LEADS", "PLATFORM_AUDIT_LOG"):
        assert table not in sql, table
    assert "AGENCIES" in sql


def test_a12_the_repository_columns_and_the_response_schema_agree():
    """Due elenchi che devono dire la stessa cosa, in file diversi."""
    from platform_admin.schemas import AgencyResponse

    assert set(agencies_repository.AGENCY_COLUMNS) == set(AgencyResponse.model_fields)


# ===========================================================================
# B - IL SERVICE: L'ORDINE FRA SCRITTURA, AUDIT E COMMIT
#
# La regola che P27-2 deve far rispettare:
#
#     nessuna modifica amministrativa viene committata
#     se il suo audit non e' stato scritto.
#
# Due connessioni non sono atomiche e non si finge che lo siano. Cio' che si
# sceglie e' l'ORDINE, che decide quale delle due incoerenze si accetta:
#
#     audit scritto, operazione non committata   ACCETTATA, e dichiarata da una
#                                                riga compensativa 'error'.
#     operazione committata, audit non scritto   MAI.
#
# Questo gruppo e' cio' che impedisce a quell'ordine di invertirsi. Invertirlo
# non produce un errore: produce una modifica senza traccia, che nessun test
# noterebbe se non andandolo a cercare.
# ===========================================================================

class FakeConn:
    """Una connessione operativa, con DUE proprieta' che devono essere fedeli.

    1. IL COMMIT COMPARE NELLA STESSA SEQUENZA DELLA SCRITTURA E DELL'AUDIT.
       Tenerlo in una lista separata sembra ordinato e non lo e': un service
       che committasse PRIMA di auditare lascerebbe intatte sia la sequenza
       "write, audit" sia il fatto che un commit e' avvenuto, e il test
       sull'ordine passerebbe mentre l'ordine e' invertito. Verificato con una
       mutazione: senza questa riga, l'inversione non veniva rilevata.

    2. IL COMMIT E' IRREVERSIBILE. Dopo un commit riuscito, un rollback
       successivo non riporta indietro nulla - ed e' proprio il caso che
       distingue "l'agenzia non e' stata creata" da "e' stata creata e poi il
       magazzino finto se l'e' dimenticata". Il commit riuscito consuma quindi
       lo snapshot dello Store; un commit FALLITO non lo consuma, perche' non
       e' avvenuto.
    """

    def __init__(self, *, commit_fails: bool = False, store=None, order=None):
        self.events: list[str] = []
        self.commit_fails = commit_fails
        self.store = store
        self.order = order if order is not None else []

    def commit(self):
        self.events.append("commit")
        self.order.append("commit")
        if self.commit_fails:
            raise RuntimeError("il commit e' fallito")
        if self.store is not None:
            self.store.commit()

    def rollback(self):
        self.events.append("rollback")
        self.order.append("rollback")
        if self.store is not None:
            self.store.restore()


class Store:
    """Le agenzie in memoria, dietro le funzioni del repository.

    ANNULLA DAVVERO, E DEVE.

    Un magazzino che tiene le righe scritte anche dopo un rollback renderebbe
    inverificabile la sola cosa che P27-2 deve garantire: che dopo un 503 da
    audit non scrivibile l'agenzia NON esista. Il test potrebbe solo osservare
    che il commit non e' stato chiamato - un buon indizio, non la proprieta'.

    Quindi la transazione e' simulata per intero: `snapshot()` all'apertura,
    `restore()` sul rollback. Non e' un database, ma su questa proprieta' si
    comporta come tale, ed e' la proprieta' sotto esame.
    """

    def __init__(self, rows=None):
        self.rows: dict[int, dict] = {r["id"]: dict(r) for r in (rows or [])}
        self.next_id = max(self.rows, default=0) + 1
        self.unique_violation_on_create = False
        self._snapshot: tuple | None = None

    def snapshot(self) -> None:
        self._snapshot = (
            {key: dict(row) for key, row in self.rows.items()},
            self.next_id,
        )

    def restore(self) -> None:
        if self._snapshot is None:
            return
        self.rows, self.next_id = self._snapshot
        self._snapshot = None

    def commit(self) -> None:
        self._snapshot = None


@pytest.fixture
def service(monkeypatch):
    """Il service e il router VERI, sopra un magazzino in memoria.

    Sostituite tre cose e nient'altro: il cursore operativo, le funzioni del
    repository e il writer di audit. L'ordine fra le tre - che e' l'oggetto di
    questo gruppo - resta quello del codice di produzione.
    """
    state = {
        "conn": FakeConn(),
        "store": Store(),
        "audit": [],
        "audit_fails": False,
        "compensating_audit_fails": False,
        "order": [],
    }

    @contextmanager
    def _cursor():
        """Riproduce `platform_operation_cursor`: NON committa, annulla su
        eccezione. Il commit resta una chiamata esplicita del service."""
        state["order"].append("open")
        conn = state["conn"]
        conn.store = state["store"]
        conn.order = state["order"]
        conn.store.snapshot()
        try:
            yield conn, object()
        except Exception:
            conn.rollback()
            raise
        else:
            # Uscire senza committare e' cio' che fa una connessione chiusa:
            # quel che e' stato scritto non resta. Su una transazione gia'
            # committata `restore()` non fa nulla, perche' il commit ha
            # consumato lo snapshot.
            conn.store.restore()

    monkeypatch.setattr(agencies_service, "platform_operation_cursor", _cursor)

    store = state["store"]
    repo = agencies_service.agencies_repository

    def _list(cur):
        state["order"].append("read")
        return [dict(r) for r in store.rows.values()]

    def _get(cur, agency_id):
        state["order"].append("read")
        row = store.rows.get(agency_id)
        return dict(row) if row else None

    def _slug_exists(cur, slug):
        return any(r["slug"] == slug for r in store.rows.values())

    def _create(cur, *, name, slug, status, settings):
        if store.unique_violation_on_create:
            raise errors.UniqueViolation("duplicate key value violates ...")
        state["order"].append("write")
        row = {
            "id": store.next_id, "name": name, "slug": slug, "status": status,
            "settings": settings, "created_at": CREATED, "updated_at": CREATED,
        }
        store.rows[store.next_id] = row
        store.next_id += 1
        return dict(row)

    def _update(cur, agency_id, fields):
        # Il guardrail del repository VERO, applicato anche qui.
        #
        # Senza, il magazzino finto accetterebbe qualunque nome di campo - e un
        # test che chiama il service con `{"slug": ...}` passerebbe senza che
        # nulla lo abbia rifiutato, cioe' proverebbe il contrario di quel che
        # dice. E' la stessa fedelta' che si e' dovuta dare a FakeConn sul
        # commit irreversibile.
        unknown = [k for k in fields if k not in agencies_repository.UPDATABLE_COLUMNS]
        if unknown or not fields:
            raise ValueError(f"colonne non aggiornabili: {sorted(unknown)}")
        state["order"].append("write")
        row = store.rows.get(agency_id)
        if row is None:
            return None
        row.update(fields)
        # La colonna e' scritta dalla UPDATE, non dal chiamante: qui si
        # riproduce quell'effetto, cosi' "updated_at cambia" e' osservabile
        # anche senza un database.
        row["updated_at"] = datetime(2026, 9, 13, 10, 0, tzinfo=timezone.utc)
        return dict(row)

    monkeypatch.setattr(repo, "list_agencies", _list)
    monkeypatch.setattr(repo, "get_agency", _get)
    monkeypatch.setattr(repo, "slug_exists", _slug_exists)
    monkeypatch.setattr(repo, "create_agency", _create)
    monkeypatch.setattr(repo, "update_agency", _update)

    def _record(**kwargs):
        """Il writer sostituito, con UNA distinzione che conta.

        `audit_fails` fa fallire solo l'audit OPERATIVO, mai quello di
        ammissione di P27-1. La distinzione non e' cosmetica: facendoli fallire
        entrambi, `require_platform_admin` risponde 503 prima che la route
        parta, e ogni test che asserisse "un audit non scrivibile da' 503"
        passerebbe senza che il service sia mai stato eseguito - verde, e cieco
        sulla regola che dice di sorvegliare.

        Che un'ammissione non registrabile dia 503 e' gia' provato da
        tests/test_p27_1_platform_admission.py, dove e' l'oggetto del test.
        """
        is_admission = kwargs.get("action") == "platform.admission"
        is_compensating = kwargs.get("result") == RESULT_ERROR
        if state["audit_fails"] and not is_admission and not is_compensating:
            state["order"].append("audit-failed")
            raise PlatformAuditUnavailable("indisponibile")
        if is_compensating and state["compensating_audit_fails"]:
            raise PlatformAuditUnavailable("indisponibile")
        state["order"].append(
            "admission" if is_admission
            else "audit-error" if is_compensating
            else "audit"
        )
        state["audit"].append(kwargs)
        return len(state["audit"])

    # Sostituito sul MODULO `platform_admin.audit`, non attraverso un
    # riferimento che un service tiene: e' lo stesso oggetto per chiunque lo
    # importi, quindi la sostituzione vale anche per `transaction.py`, che dopo
    # P27-3 e' chi lo chiama davvero.
    monkeypatch.setattr(platform_audit, "record", _record)
    return state


def _create(state, **overrides):
    payload = dict(
        name="Agenzia A", slug="agenzia-a", status="active", settings={},
        created_fields=["name", "slug"],
    )
    payload.update(overrides)
    return agencies_service.create_agency(_ctx(), **payload)


# --- B1-B2: l'ordine, e cosa lo dimostra ------------------------------------

def test_b1_create_orders_write_audit_commit(service):
    _create(service)
    assert service["order"] == ["open", "write", "audit", "commit"]


def test_b1_update_orders_write_audit_commit(service):
    _create(service)
    service["order"].clear()
    service["conn"].events.clear()
    agencies_service.update_agency(_ctx(), 1, {"name": "Rinominata"})
    assert service["order"] == ["open", "read", "write", "audit", "commit"]


def test_b2_create_does_not_commit_when_the_audit_fails(service):
    """Il passo 4: l'eccezione esce dal `with`, la transazione viene annullata."""
    service["audit_fails"] = True
    with pytest.raises(PlatformAuditUnavailable):
        _create(service)
    assert "commit" not in service["conn"].events, service["conn"].events
    assert service["conn"].events == ["rollback"]
    assert service["audit"] == []


def test_b2_update_does_not_commit_when_the_audit_fails(service):
    _create(service)
    service["audit_fails"] = True
    service["conn"].events.clear()
    with pytest.raises(PlatformAuditUnavailable):
        agencies_service.update_agency(_ctx(), 1, {"name": "Rinominata"})
    assert service["conn"].events == ["rollback"]


def test_b2_the_write_happened_before_the_audit_refusal_and_was_undone(service):
    """Controllo negativo: la scrittura era davvero partita.

    Senza questa asserzione, un service che rifiutasse PRIMA di scrivere
    passerebbe B2 senza rispettare nulla - non avrebbe niente da annullare.
    """
    service["audit_fails"] = True
    with pytest.raises(PlatformAuditUnavailable):
        _create(service)
    assert service["order"] == ["open", "write", "audit-failed", "rollback"]


# --- B3-B4: il commit che fallisce dopo un audit riuscito --------------------

def test_b3_a_failed_commit_after_a_successful_audit_writes_a_compensating_row(
    service,
):
    """Il passo 6.

    Esiste una riga 'success' che descrive qualcosa che non e' andato in porto.
    La si corregge come si corregge sempre un registro append-only: con una
    riga nuova, mai riscrivendo quella di prima.
    """
    service["conn"] = FakeConn(commit_fails=True)
    with pytest.raises(RuntimeError):
        _create(service)

    assert [entry["result"] for entry in service["audit"]] == [
        RESULT_SUCCESS, RESULT_ERROR
    ]
    compensating = service["audit"][-1]
    assert compensating["action"] == ACTION_AGENCY_CREATE
    assert compensating["metadata"] == {"commit_failed": True}
    assert compensating["target_type"] == TARGET_TYPE_AGENCY


def test_b3_the_original_error_is_what_propagates_not_the_audit_one(service):
    """Chi legge il 500 deve vedere il fallimento del commit, non "audit non
    disponibile" - che lo manderebbe a cercare il problema altrove."""
    service["conn"] = FakeConn(commit_fails=True)
    with pytest.raises(RuntimeError, match="commit"):
        _create(service)


def test_b4_a_failed_compensating_row_is_swallowed_best_effort(service):
    """Se il database non committa, probabilmente non scrive nemmeno questa.

    Sollevare qui sostituirebbe l'errore vero con uno peggiore.
    """
    service["conn"] = FakeConn(commit_fails=True)
    service["compensating_audit_fails"] = True
    with pytest.raises(RuntimeError, match="commit"):
        _create(service)


def test_b4_the_compensating_row_survives_the_absence_of_foreign_keys(service):
    """La nota che regge proprio qui.

    La riga compensativa nomina l'id di un'agenzia che NON e' stata committata
    e che quindi non esiste. Funziona perche' `platform_audit_log` non ha
    chiavi esterne (P27-1): i suoi id sono istantanee storiche. Con una FK
    sarebbe stata rifiutata esattamente nel momento in cui serviva.
    """
    service["conn"] = FakeConn(commit_fails=True)
    with pytest.raises(RuntimeError):
        _create(service)
    assert service["audit"][-1]["target_agency_id"] is not None


# --- B5-B7: il contenuto dell'audit -----------------------------------------

def test_b5_the_create_audit_names_the_agency_and_the_fields_only(service):
    row = _create(service, created_fields=["name", "slug", "status"])
    entry = service["audit"][0]
    assert entry["action"] == ACTION_AGENCY_CREATE
    assert entry["result"] == RESULT_SUCCESS
    assert entry["target_type"] == TARGET_TYPE_AGENCY
    assert entry["target_id"] == row["id"]
    assert entry["target_agency_id"] == row["id"]
    assert entry["metadata"] == {"created_fields": ["name", "slug", "status"]}
    assert entry["actor"].user_id == PLATFORM_USER


def test_b6_the_update_audit_names_the_changed_fields_only(service):
    _create(service)
    agencies_service.update_agency(
        _ctx(), 1, {"name": "Rinominata", "status": "suspended"}
    )
    entry = service["audit"][-1]
    assert entry["action"] == ACTION_AGENCY_UPDATE
    assert entry["target_id"] == 1
    assert entry["target_agency_id"] == 1
    assert entry["metadata"] == {"changed_fields": ["name", "status"]}


def test_b7_no_audit_metadata_ever_carries_a_value_or_a_body(service):
    """Il registro dice CHE COSA e' stato toccato, mai con quale contenuto.

    Un nome commerciale, un indirizzo o un JSON di configurazione dentro una
    tabella append-only ci restano per sempre, e nessuno li ha guardati prima
    di scriverli.
    """
    _create(service, name="Nome Riservato", slug="slug-riservato",
            settings={"segreto": "valore"}, created_fields=["name", "slug", "settings"])
    agencies_service.update_agency(
        _ctx(), 1, {"name": "Altro Nome", "settings": {"altro": "segreto"}}
    )
    blob = repr(service["audit"])
    for value in ("Nome Riservato", "slug-riservato", "valore",
                  "Altro Nome", "segreto"):
        assert value not in blob, (value, blob)
    for entry in service["audit"]:
        for key in entry["metadata"]:
            assert key in ("created_fields", "changed_fields"), key


# --- B8: le letture non auditano --------------------------------------------

def test_b8_reads_write_no_operational_audit_row(service):
    """L'audit di ammissione di P27-1 e' gia' la traccia di una lettura.

    Una riga per ogni GET trasformerebbe il registro degli ATTI in un log di
    traffico, e la prima schermata di P27-7 che fa polling lo renderebbe
    illeggibile.
    """
    _create(service)
    service["audit"].clear()
    agencies_service.list_agencies()
    agencies_service.get_agency(1)
    assert service["audit"] == []


def test_b8_reads_never_commit(service):
    _create(service)
    service["conn"].events.clear()
    agencies_service.list_agencies()
    agencies_service.get_agency(1)
    assert service["conn"].events == []


# --- B9: conflitti e assenze, a livello di service --------------------------

def test_b9_a_duplicate_slug_is_refused_before_the_write(service):
    _create(service)
    service["order"].clear()
    with pytest.raises(AgencySlugConflict):
        _create(service)
    assert "write" not in service["order"], service["order"]
    assert service["conn"].events[-1] == "rollback"


def test_b9_a_lost_race_becomes_a_conflict_and_not_a_driver_error(service):
    """Il controllo esplicito e la INSERT non sono un'operazione sola.

    Senza la cattura di UniqueViolation, chi perde la corsa riceve un 500 con
    dentro il nome del vincolo e il valore in conflitto.
    """
    service["store"].unique_violation_on_create = True
    with pytest.raises(AgencySlugConflict):
        _create(service)


def test_b9_update_of_an_absent_agency_is_not_found(service):
    """Era: la stessa prova con uno slug altrui nel corpo, per verificare che
    l'id venisse controllato per primo. Con lo slug non piu' aggiornabile quel
    corpo non e' nemmeno costruibile, e resta la meta' che conta."""
    _create(service)
    with pytest.raises(AgencyNotFound):
        agencies_service.update_agency(_ctx(), 999, {"name": "X"})


def test_b9_the_service_never_builds_an_update_that_touches_the_slug(service):
    """Il percorso completo service -> repository, con lo slug fra i campi.

    Non passa dallo schema, quindi e' il controllo dello strato sotto: anche
    chiamando il service direttamente - come fara' un domani P27-8 - lo slug
    non si scrive.
    """
    _create(service)
    with pytest.raises(ValueError):
        agencies_service.update_agency(
            _ctx(), 1, {"slug": "slug-nuovo"}
        )


def test_b9_the_slug_survives_every_other_update(service):
    _create(service)
    agencies_service.update_agency(
        _ctx(), 1, {"name": "Rinominata", "status": "suspended"}
    )
    assert service["store"].rows[1]["slug"] == "agenzia-a"


def test_b9_no_audit_row_is_written_for_a_refused_operation(service):
    """Un conflitto o un 404 non sono atti amministrativi: non e' cambiato
    nulla. L'ammissione di P27-1 ha gia' registrato il tentativo."""
    _create(service)
    service["audit"].clear()
    with pytest.raises(AgencySlugConflict):
        _create(service)
    with pytest.raises(AgencyNotFound):
        agencies_service.get_agency(999)
    assert service["audit"] == []


# ===========================================================================
# C - HTTP
#
# Il router e il service VERI, montati come li monta main.py. Cambia solo da
# dove arrivano le righe.
# ===========================================================================

@pytest.fixture
def client(service, monkeypatch):
    """L'app con il router platform montato dietro `require_platform_admin`.

    `state["session"]` decide chi chiama. La dipendenza di P27-1 scrive la
    propria riga di ammissione attraverso lo stesso writer sostituito, quindi
    `state["audit"]` le contiene entrambe: `operations()` separa quelle
    operative, che sono l'oggetto di questo gruppo.
    """
    from operator_auth import dependencies as operator_deps

    service["session"] = _ctx()

    def _resolve(_token):
        if service["session"] is None:
            return None
        return {
            "context": service["session"],
            "agency_name": "Agenzia di prova",
            "expires_at": EXPIRES,
        }

    monkeypatch.setattr(operator_deps.service, "session_from_token", _resolve)

    service["operations"] = lambda: [
        entry for entry in service["audit"]
        if entry["action"] != "platform.admission"
    ]

    app = FastAPI()
    app.include_router(
        platform_router,
        dependencies=[Depends(platform_deps.require_platform_admin)],
    )
    http = TestClient(app, raise_server_exceptions=False)
    http.cookies.set("stima360_operator_session", "un-token")
    return http


AGENCIES = f"{ROUTER_PREFIX}/agencies"


def _post(client, **overrides):
    body = {"name": "Agenzia A", "slug": "agenzia-a"}
    body.update(overrides)
    return client.post(AGENCIES, json=body)


# --- C1-C3: il percorso felice ----------------------------------------------

def test_c1_list_returns_the_network(client, service):
    assert _post(client).status_code == 201
    assert _post(client, name="Agenzia B", slug="agenzia-b").status_code == 201
    response = client.get(AGENCIES)
    assert response.status_code == 200, response.text
    assert [row["slug"] for row in response.json()] == ["agenzia-a", "agenzia-b"]


def test_c2_get_returns_one_agency_with_the_seven_declared_fields(client):
    created = _post(client).json()
    response = client.get(f"{AGENCIES}/{created['id']}")
    assert response.status_code == 200, response.text
    assert set(response.json()) == {
        "id", "name", "slug", "status", "settings", "created_at", "updated_at"
    }


def test_c2_get_of_an_absent_agency_is_404(client):
    response = client.get(f"{AGENCIES}/999999")
    assert response.status_code == 404, response.text


def test_c3_post_creates_with_201_and_the_defaults(client):
    response = _post(client)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "Agenzia A"
    assert body["slug"] == "agenzia-a"
    assert body["status"] == "active"
    assert body["settings"] == {}
    assert isinstance(body["id"], int)


def test_c3_post_accepts_an_explicit_status_and_settings(client):
    """P27-4: `settings` NON E' PIU' UN JSONB LIBERO.

    Questo test usava una chiave inventata (`citta`), che era legittima finche'
    il contenitore non aveva un contratto. P27-4 gliene ha dato uno - timezone
    e locale, validati - quindi una chiave sconosciuta e' adesso un 422, e il
    test usa un campo reale.
    """
    response = _post(
        client, status="suspended", settings={"timezone": "Europe/Paris"}
    )
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "suspended"
    assert response.json()["settings"] == {"timezone": "Europe/Paris"}


def test_c3_post_refuses_an_unknown_settings_key(client):
    """La chiusura della superficie libera, provata dal lato POST."""
    assert _post(client, settings={"citta": "Alba"}).status_code == 422


def test_c3_post_refuses_an_invalid_timezone(client):
    assert _post(client, settings={"timezone": "Mars/Olympus"}).status_code == 422


def test_c3_post_without_settings_writes_an_empty_object(client):
    """I default non si scrivono nella riga: vivono nell'applicazione e si
    applicano in lettura, cosi' non ci sono due sorgenti di verita' su cosa
    significhi "non configurato"."""
    assert _post(client).json()["settings"] == {}


def test_c3_the_name_is_stored_trimmed(client):
    """Il CHECK del database accetterebbe gli spazi ai lati, e due agenzie
    potrebbero risultare indistinguibili sullo schermo e diverse nel database."""
    assert _post(client, name="   Agenzia A   ").json()["name"] == "Agenzia A"


# --- C4: conflitto di slug ---------------------------------------------------

def test_c4_a_duplicate_slug_is_409(client):
    assert _post(client).status_code == 201
    response = _post(client, name="Un altro nome")
    assert response.status_code == 409, response.text


def test_c4_the_409_body_leaks_no_postgresql_detail(client):
    _post(client)
    body = _post(client).text
    for leak in ("duplicate key", "agencies_slug_unq", "psycopg", "DETAIL",
                 "constraint"):
        assert leak not in body, (leak, body)


def test_c4_a_lost_race_is_also_409_and_not_500(client, service):
    service["store"].unique_violation_on_create = True
    assert _post(client).status_code == 409


# --- C5-C6: PATCH ------------------------------------------------------------

def test_c5_patch_updates_and_returns_the_row(client):
    created = _post(client).json()
    response = client.patch(f"{AGENCIES}/{created['id']}", json={"name": "Rinominata"})
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Rinominata"


def test_c5_patch_of_an_absent_agency_is_404(client):
    response = client.patch(f"{AGENCIES}/999999", json={"name": "X"})
    assert response.status_code == 404, response.text


def test_c6_patch_touches_only_the_fields_it_names(client):
    created = _post(
        client, status="active", settings={"timezone": "Europe/Paris"}
    ).json()
    updated = client.patch(
        f"{AGENCIES}/{created['id']}", json={"status": "suspended"}
    ).json()
    assert updated["status"] == "suspended"
    assert updated["name"] == created["name"]
    assert updated["slug"] == created["slug"]
    # La PATCH generica non nomina `settings` e non lo tocca - adesso non
    # potrebbe nemmeno nominarlo.
    assert updated["settings"] == {"timezone": "Europe/Paris"}


def test_c6_patch_bumps_updated_at_and_leaves_created_at_alone(client):
    created = _post(client).json()
    updated = client.patch(
        f"{AGENCIES}/{created['id']}", json={"name": "Rinominata"}
    ).json()
    assert updated["updated_at"] != created["updated_at"]
    assert updated["created_at"] == created["created_at"]


def test_c6_patch_can_move_an_agency_through_every_status(client):
    """`suspended` e `archived` rendono il tenant inutilizzabile: lo decide
    gia' operator_auth, che richiede agency_status='active'. P27-2 non tocca
    quella regola, si limita a poter impostare lo stato."""
    created = _post(client).json()
    for status in AGENCY_STATUSES:
        response = client.patch(f"{AGENCIES}/{created['id']}", json={"status": status})
        assert response.status_code == 200, response.text
        assert response.json()["status"] == status


# --- C7: le validazioni, tutte 422 ------------------------------------------

@pytest.mark.parametrize("body,perche", [
    ({"name": "A", "slug": "agenzia-a", "status": "attiva"}, "stato inventato"),
    ({"name": "A", "slug": "agenzia-a", "status": "deleted"}, "stato plausibile ma assente"),
    ({"name": "A", "slug": "Agenzia-A"}, "slug con maiuscole"),
    ({"name": "A", "slug": "-agenzia"}, "slug che inizia con trattino"),
    ({"name": "A", "slug": "agenzia-"}, "slug che finisce con trattino"),
    ({"name": "A", "slug": "agenzia a"}, "slug con spazio"),
    ({"name": "A", "slug": "agenzia_a"}, "slug con underscore"),
    ({"name": "A", "slug": ""}, "slug vuoto"),
    ({"name": "", "slug": "agenzia-a"}, "nome vuoto"),
    ({"name": "   ", "slug": "agenzia-a"}, "nome di soli spazi"),
    ({"name": "A" * (AGENCY_NAME_MAX + 1), "slug": "agenzia-a"}, "nome troppo lungo"),
    ({"name": "A", "slug": "a" * (AGENCY_SLUG_MAX + 1)}, "slug troppo lungo"),
    ({"name": "A", "slug": "agenzia-a", "settings": []}, "settings non e' un oggetto"),
    ({"name": "A", "slug": "agenzia-a", "settings": "x"}, "settings e' una stringa"),
    ({"name": "A", "slug": "agenzia-a", "settings": {"citta": "Alba"}},
     "chiave di configurazione sconosciuta"),
    ({"name": "A", "slug": "agenzia-a", "settings": {"timezone": "Mars/Olympus"}},
     "timezone inesistente"),
    ({"slug": "agenzia-a"}, "nome mancante"),
    ({"name": "A"}, "slug mancante"),
    ({"name": "A", "slug": "agenzia-a", "id": 9}, "id introdotto di contrabbando"),
    ({"name": "A", "slug": "agenzia-a", "created_at": "2026-01-01T00:00:00Z"},
     "created_at introdotto di contrabbando"),
], ids=lambda v: v if isinstance(v, str) else "")
def test_c7_post_refuses_a_malformed_body_with_422(client, body, perche):
    response = client.post(AGENCIES, json=body)
    assert response.status_code == 422, (perche, response.text)


@pytest.mark.parametrize("body,perche", [
    ({}, "PATCH vuota"),
    ({"name": None}, "name esplicitamente null"),
    ({"status": None}, "status esplicitamente null"),
    ({"settings": None}, "settings esplicitamente null"),
    ({"status": "attiva"}, "stato inventato"),
    ({"name": "  "}, "nome di soli spazi"),
    ({"settings": {}}, "settings: ha la sua route dalla P27-4"),
    ({"id": 9}, "id introdotto di contrabbando"),
    ({"updated_at": "2026-01-01T00:00:00Z"}, "updated_at introdotto di contrabbando"),
], ids=lambda v: v if isinstance(v, str) else "")
def test_c7_patch_refuses_a_malformed_body_with_422(client, body, perche):
    created = _post(client).json()
    response = client.patch(f"{AGENCIES}/{created['id']}", json=body)
    assert response.status_code == 422, (perche, response.text)


def test_c7_an_empty_patch_changes_nothing_and_writes_no_audit(client, service):
    """422 e non 200: una richiesta che non dice cosa fare non e' un
    aggiornamento riuscito, ed e' esattamente il modo in cui l'errore di un
    client resterebbe invisibile."""
    created = _post(client).json()
    service["audit"].clear()
    assert client.patch(f"{AGENCIES}/{created['id']}", json={}).status_code == 422
    assert service["operations"]() == []
    assert client.get(f"{AGENCIES}/{created['id']}").json() == created


def test_c7_the_generic_patch_no_longer_accepts_settings_at_all(client):
    """Era: `settings` si svuotava con `{}` da questa PATCH.

    P27-4: `settings` NON E' PIU' UN JSONB LIBERO.

    Questo test usava una chiave inventata (`citta`), che era legittima finche'
    il contenitore non aveva un contratto. P27-4 gliene ha dato uno - timezone
    e locale, validati - quindi una chiave sconosciuta e' adesso un 422, e il
    test usa un campo reale.

    E soprattutto: la PATCH generica non e' piu' una strada verso quel JSONB.
    Due strade verso la stessa colonna, una validata e una libera, avrebbero
    significato che quella libera era il contratto vero. La configurazione si
    aggiorna da `PATCH /agencies/{id}/configuration`.
    """
    created = _post(client, settings={"timezone": "Europe/Paris"}).json()
    for body in ({"settings": {}},
                 {"settings": {"timezone": "Europe/Rome"}},
                 {"settings": {"citta": "Alba"}}):
        response = client.patch(f"{AGENCIES}/{created['id']}", json=body)
        assert response.status_code == 422, (body, response.text)

    # E la riga non e' stata toccata da nessuno dei tre tentativi.
    assert client.get(f"{AGENCIES}/{created['id']}").json()["settings"] == {
        "timezone": "Europe/Paris"
    }


# --- C8: nessuna DELETE ------------------------------------------------------

def test_c8_the_platform_surface_exposes_no_delete_at_all():
    """L'assenza e' una decisione.

    Un'agenzia con dati dentro non deve poter sparire da una chiamata HTTP, e
    `status='archived'` la spegne gia' davvero - operator_auth rende
    inutilizzabile un tenant che non sia 'active'.
    """
    import main

    spec = main.app.openapi()
    for path, operations in spec["paths"].items():
        if path.startswith(ROUTER_PREFIX):
            assert "delete" not in operations, (path, sorted(operations))


def test_c8_a_delete_request_is_405_and_not_a_silent_success(client):
    created = _post(client).json()
    assert client.delete(f"{AGENCIES}/{created['id']}").status_code == 405


def test_c8_the_real_application_exposes_the_four_p27_2_routes():
    """Era: `found == {...}`, cioe' l'elenco ESAUSTIVO della superficie.

    P27-3 ha aggiunto sei route su operatori, membership e titolarita', e un
    perno sulla dimensione totale dentro il file di P27-2 si romperebbe a ogni
    fase che la allarga: rumore, non sorveglianza. E' lo stesso passaggio di
    consegne gia' fatto per il perno sul numero di migration in P27-1.

    Cio' che questo file possiede resta asserito qui - le quattro route delle
    agenzie ci sono, con i metodi giusti, e nessuna DELETE. L'elenco esaustivo
    appartiene alla fase piu' recente:
    tests/test_p27_3_operators.py::test_g8_the_real_application_exposes_exactly_the_p27_3_routes.
    """
    import main

    spec = main.app.openapi()
    found = {
        (method.upper(), path)
        for path, operations in spec["paths"].items()
        if path.startswith(ROUTER_PREFIX)
        for method in operations
    }
    assert {
        ("GET", AGENCIES),
        ("POST", AGENCIES),
        ("GET", f"{AGENCIES}/{{agency_id}}"),
        ("PATCH", f"{AGENCIES}/{{agency_id}}"),
    } <= found, sorted(found)

    # E niente DELETE, su nessuna route della superficie: la meta' del perno
    # che non dipende da quante route esistano.
    assert not [pair for pair in found if pair[0] == "DELETE"], sorted(found)


# --- C9: l'audit che non si scrive ------------------------------------------

def test_c9_an_unwritable_audit_makes_a_create_answer_503(client, service):
    service["audit_fails"] = True
    response = _post(client)
    assert response.status_code == 503, response.text


def test_c9_the_agency_is_not_created_when_the_audit_fails(client, service):
    """La prova che conta: dopo il 503, la rete e' come prima."""
    service["audit_fails"] = True
    assert _post(client).status_code == 503
    service["audit_fails"] = False
    assert client.get(AGENCIES).json() == []


def test_c9_an_unwritable_audit_makes_a_patch_answer_503_and_commits_nothing(
    client, service
):
    """Il magazzino in memoria non sa annullare, e non deve saperlo.

    Cio' che il service controlla davvero e' il COMMIT, ed e' quello che si
    osserva: durante la PATCH rifiutata la transazione riceve un rollback e
    nessun commit. In un database vero e' quel rollback a rimettere la riga
    com'era - verificato dal vivo, e riportato nel report.
    """
    created = _post(client).json()
    service["audit_fails"] = True
    service["conn"].events.clear()

    response = client.patch(
        f"{AGENCIES}/{created['id']}", json={"name": "Rinominata"}
    )

    assert response.status_code == 503, response.text
    assert service["conn"].events == ["rollback"], service["conn"].events


def test_c9_the_503_body_is_the_constant_message(client, service):
    from platform_admin.enums import AUDIT_UNAVAILABLE_MESSAGE

    service["audit_fails"] = True
    response = _post(client)
    assert response.json() == {"detail": AUDIT_UNAVAILABLE_MESSAGE}
    assert "indisponibile" not in response.text


# --- C10-C11: l'ammissione di P27-1 vale su tutte e quattro ------------------

@pytest.mark.parametrize("method,path,body", [
    ("get", AGENCIES, None),
    ("get", f"{AGENCIES}/1", None),
    ("post", AGENCIES, {"name": "A", "slug": "agenzia-a"}),
    ("patch", f"{AGENCIES}/1", {"name": "A"}),
])
def test_c10_an_agency_owner_is_403_on_every_agency_route(
    client, service, method, path, body
):
    """P27-2 non ridichiara l'autorizzazione: la eredita dal mount.

    Se una route nuova dimenticasse `require_platform_admin`, il mount la
    proteggerebbe comunque - ed e' per questo che la dipendenza e' dichiarata
    in entrambi i posti.
    """
    service["session"] = _ctx(
        agency_id=4242, role="agency_owner", is_platform_admin=False
    )
    response = getattr(client, method)(path, **({"json": body} if body else {}))
    assert response.status_code == 403, response.text


@pytest.mark.parametrize("method,path,body", [
    ("get", AGENCIES, None),
    ("post", AGENCIES, {"name": "A", "slug": "agenzia-a"}),
])
def test_c11_an_unauthenticated_caller_is_401(client, service, method, path, body):
    client.cookies.clear()
    response = getattr(client, method)(path, **({"json": body} if body else {}))
    assert response.status_code == 401, response.text


def test_c10_a_refused_caller_creates_nothing(client, service):
    service["session"] = _ctx(
        agency_id=4242, role="agency_owner", is_platform_admin=False
    )
    assert _post(client).status_code == 403
    service["session"] = _ctx()
    assert client.get(AGENCIES).json() == []


# ===========================================================================
# D - IL PERIMETRO
#
# Cio' che P27-2 NON ha toccato. Ogni fase che allarga un sistema multi-tenant
# ha un modo di allargarsi di piu' di quanto dichiara, e questi test sono il
# posto in cui quel di piu' diventa rosso.
# ===========================================================================

def test_d1_the_statuses_match_the_check_constraint_in_migration_027():
    """Due elenchi in due linguaggi, che devono dire la stessa cosa.

    Se divergessero, uno stato valido per l'applicazione verrebbe rifiutato dal
    database al momento della scrittura - con un 500 invece di un 422.
    """
    sql = MIGRATION_027.read_text(encoding="utf-8")
    match = re.search(r"status IN \(([^)]*)\)", sql)
    assert match, "il CHECK su agencies.status non e' stato trovato"
    declared = tuple(v.strip().strip("'") for v in match.group(1).split(","))
    assert declared == AGENCY_STATUSES, (declared, AGENCY_STATUSES)


def test_d1_the_slug_pattern_is_byte_identical_to_the_check_constraint():
    """La regex e' duplicata fra Python e SQL, e la duplicazione e' sorvegliata.

    Validare in Python non e' ridondante: il database risponderebbe con un
    errore di vincolo - un 500, o un messaggio che nomina oggetti interni -
    mentre la richiesta e' malformata e merita un 422 che dice quale campo.
    """
    sql = MIGRATION_027.read_text(encoding="utf-8")
    match = re.search(r"slug ~ '([^']*)'", sql)
    assert match, "il CHECK su agencies.slug non e' stato trovato"
    assert match.group(1) == AGENCY_SLUG_PATTERN, (match.group(1), AGENCY_SLUG_PATTERN)


def test_d1_the_column_lengths_match_migration_027():
    sql = MIGRATION_027.read_text(encoding="utf-8")
    assert f"name       VARCHAR({AGENCY_NAME_MAX})" in sql
    assert f"slug       VARCHAR({AGENCY_SLUG_MAX})" in sql


def test_d2_p27_2_added_no_migration():
    """`agencies` esiste dalla 027 con i suoi CHECK. P27-2 non ha nessuna
    ragione tecnica di aggiungerne una, e non averla aggiunta e' verificabile."""
    # P27-5: IL PERNO SI E' SPOSTATO DAL SOFFITTO AL NOME.
    #
    # Questo test asseriva che la migration piu' alta fosse la 057. Era vero
    # finche' nessuna fase successiva ne aggiungeva una, e ha smesso di esserlo
    # con la 058 di P27-5, che una migration ce l'ha e deve averla. Un perno sul
    # SOFFITTO dentro il file di una fase vecchia fallisce a ogni fase che
    # aggiunge legittimamente uno schema: rumore, non sorveglianza.
    #
    # Cio' che P27-2 deve davvero garantire e' che non ne abbia aggiunta una
    # SUA, ed e' quello che si asserisce adesso - una proprieta' che resta vera
    # per sempre, qualunque cosa facciano le fasi dopo.
    nomi = [
        path.name for path in (ROOT / "migrations").glob("*.sql")
        if "p27_2" in path.name
    ]
    assert nomi == [], nomi


def test_d3_the_audit_repository_is_still_only_the_audit_writer():
    """`platform_admin/repository.py` non ha acquisito funzioni sulle agenzie.

    Quel modulo non ha letture, UPDATE o DELETE, e l'assenza e' una garanzia
    leggibile a colpo d'occhio: il registro e' append-only anche a livello di
    applicazione. Un `update_agency` li' dentro costerebbe quella leggibilita'
    per sempre - ed e' il motivo per cui P27-2 ha un file suo.
    """
    tree = ast.parse(
        (ROOT / "platform_admin" / "repository.py").read_text(encoding="utf-8")
    )
    functions = [
        node.name for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    assert functions == ["insert_audit_entry"], functions


def test_d4_no_p27_2_module_names_an_operator_or_membership_table():
    """Gli operatori sono P27-3, le impostazioni sono P27-4, i territori P27-5.

    P27-2 gestisce l'anagrafica della rete e nient'altro: creare un titolare
    insieme all'agenzia sarebbe comodo, e sarebbe P27-3 fatto di nascosto.
    """
    for name in ("agencies_repository.py", "agencies_service.py"):
        tree = ast.parse((ROOT / "platform_admin" / name).read_text(encoding="utf-8"))
        sql = " ".join(_sql_literals(tree)).upper()
        for table in ("AGENCY_MEMBERSHIPS", "OPERATOR_USERS", "OPERATOR_SESSIONS"):
            assert table not in sql, (name, table)


def test_d4_operator_auth_was_not_touched_by_p27_2():
    """La semantica degli stati e' gia' decisa li': una sessione e' utilizzabile
    solo con membership_status='active' E agency_status='active'. P27-2 imposta
    lo stato, non ridefinisce cosa significhi."""
    source = (ROOT / "operator_auth" / "service.py").read_text(encoding="utf-8")
    assert 'row.get("agency_status") == "active"' in source
    assert 'row.get("membership_status") == "active"' in source


def test_d5_the_whole_package_has_exactly_one_copy_of_the_order():
    """UNA copia dell'ordine, adesso per tutto il package.

    Era: la stessa prova ristretta a `agencies_service`, dove la sequenza viveva
    quando P27-2 era l'unica fase a mutare qualcosa. P27-3 ne ha portate altre
    quattro, e ha spostato la sequenza in `platform_admin/transaction.py`
    perche' due copie sono due posti in cui invertirla - e invertirla non
    produce un errore, produce una modifica senza traccia.

    Il perno si e' quindi allargato invece di essere tolto: `conn.commit()`
    deve comparire una volta sola in TUTTO il package, non in un file solo.
    """
    package = ROOT / "platform_admin"
    # `database.py` e' escluso, e la ragione e' proprio la decisione D2: li'
    # dentro c'e' il commit della connessione DELL'AUDIT, che e' una
    # transazione diversa e deve committare da sola. Contarlo insieme
    # all'altro confonderebbe le due meta' della regola invece di
    # sorvegliarle.
    commits = {
        path.name: path.read_text(encoding="utf-8").count("conn.commit()")
        for path in sorted(package.glob("*.py"))
        if path.name != "database.py"
    }
    assert sum(commits.values()) == 1, commits
    assert commits["transaction.py"] == 1, commits

    # E il commit escluso e' davvero quello dell'audit, non un secondo commit
    # operativo nascosto nel modulo sbagliato.
    database_source = (package / "database.py").read_text(encoding="utf-8")
    audit_cursor = database_source.split("def platform_audit_cursor")[1].split("def ")[0]
    operation_cursor = database_source.split("def platform_operation_cursor")[1]
    assert "conn.commit()" in audit_cursor
    assert "conn.commit()" not in operation_cursor, (
        "il cursore operativo committa da solo: la regola non e' piu' "
        "esprimibile dal chiamante"
    )

    # E ogni mutazione passa di li'.
    callers = []
    for path in sorted(package.glob("*_service.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        callers += [
            node.name for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and any(
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Name)
                and inner.func.id == "audit_then_commit"
                for inner in ast.walk(node)
            )
        ]
    assert {"create_agency", "update_agency"} <= set(callers), callers


def test_d5_the_router_writes_no_audit_row_of_its_own():
    """Il router traduce, non decide. Un audit scritto li' sarebbe scritto
    prima o dopo il commit a seconda di dove capita la riga."""
    tree = ast.parse((ROOT / "platform_admin" / "router.py").read_text(encoding="utf-8"))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert "audit" not in names
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert node.module != ".audit", node.module


def test_d6_p27_1_me_is_unchanged():
    """La superficie di P27-1 resta quella che era: P27-2 aggiunge route, non
    ne modifica."""
    from platform_admin.schemas import PlatformMeResponse

    assert set(PlatformMeResponse.model_fields) == {
        "user_id", "is_platform_admin", "agency_id", "session_expires_at"
    }


# ===========================================================================
# E - LO SLUG E' IMMUTABILE DOPO LA CREAZIONE
#
# La migration 027 lo definisce come chiave stabile ("slug is the stable key")
# e ne vincola la forma perche' viene RISOLTO: il funnel pubblico trova la
# Default Agency per slug (`core.scope.resolve_default_agency_id`), e il
# backfill della 029 ci ha attaccato ogni record legacy.
#
# Rinominarlo da una PATCH romperebbe quei lookup lasciando la riga intatta -
# un guasto che non si vede finche' un'estimazione pubblica non finisce
# nell'agenzia sbagliata, o in nessuna. Questo gruppo e' cio' che tiene chiusa
# quella porta su tutti e tre gli strati.
# ===========================================================================

def test_e1_a_patch_that_names_the_slug_is_422(client):
    """Uno slug VALIDO e diverso: sarebbe stato accettato prima.

    Il caso importante e' questo, non uno slug malformato - quello sarebbe 422
    comunque, per la sua forma, e non proverebbe nulla sull'immutabilita'.
    """
    created = _post(client).json()
    response = client.patch(f"{AGENCIES}/{created['id']}", json={"slug": "slug-nuovo"})
    assert response.status_code == 422, response.text


def test_e1_the_422_names_the_offending_field(client):
    """`extra="forbid"` mette il nome del campo nel messaggio: chi ha inviato
    la richiesta scopre CHE COSA non e' accettato, invece di un rifiuto muto."""
    created = _post(client).json()
    body = client.patch(f"{AGENCIES}/{created['id']}", json={"slug": "slug-nuovo"}).text
    assert "slug" in body, body


def test_e1_a_patch_mixing_the_slug_with_valid_fields_is_refused_whole(client):
    """Non si applica la parte buona ignorando quella rifiutata.

    Una PATCH parzialmente applicata e' il modo in cui un client crede di aver
    rinominato lo slug e ha rinominato solo l'agenzia.
    """
    created = _post(client).json()
    response = client.patch(
        f"{AGENCIES}/{created['id']}",
        json={"name": "Rinominata", "slug": "slug-nuovo"},
    )
    assert response.status_code == 422, response.text
    assert client.get(f"{AGENCIES}/{created['id']}").json()["name"] == created["name"]


def test_e2_the_slug_is_unchanged_after_every_permitted_update(client):
    """Il controllo positivo: tutto il resto si aggiorna, lo slug no."""
    created = _post(client, settings={"timezone": "Europe/Paris"}).json()
    for body in (
        {"name": "Agenzia Alba Adriatica"},
        {"status": "suspended"},
        {"name": "Terzo nome", "status": "archived"},
    ):
        response = client.patch(f"{AGENCIES}/{created['id']}", json=body)
        assert response.status_code == 200, response.text
        assert response.json()["slug"] == created["slug"], body


def test_e3_the_patch_schema_declares_two_fields_and_the_slug_is_not_one(client):
    """Erano tre: P27-4 ha tolto anche `settings`, che ora ha la sua route."""
    from platform_admin.schemas import AgencyCreateRequest, AgencyUpdateRequest

    assert set(AgencyUpdateRequest.model_fields) == {"name", "status"}
    # Alla creazione resta obbligatorio: non modificabile non vuol dire sparito.
    assert "slug" in AgencyCreateRequest.model_fields
    assert AgencyCreateRequest.model_fields["slug"].is_required()


def test_e4_changed_fields_can_never_contain_the_slug(service):
    """L'audit non puo' nominare un campo che nessuna UPDATE puo' scrivere.

    Asserito sullo schema, che e' cio' che produce quei nomi: `changed_fields`
    li prende da `model_fields_set`, e lo slug non e' un campo dichiarato.
    """
    from platform_admin.schemas import AgencyUpdateRequest

    payload = AgencyUpdateRequest(name="N", status="active")
    assert "slug" not in payload.changed_fields()

    _create(service)
    agencies_service.update_agency(_ctx(), 1, payload.changed_fields())
    assert "slug" not in service["audit"][-1]["metadata"]["changed_fields"]


def test_e5_create_is_untouched_valid_slug_still_works(client):
    """La correzione non doveva toccare la creazione."""
    response = _post(client, slug="agenzia-nuova")
    assert response.status_code == 201, response.text
    assert response.json()["slug"] == "agenzia-nuova"


def test_e5_create_with_a_duplicate_slug_is_still_409(client):
    _post(client)
    assert _post(client, name="Altro nome").status_code == 409


def test_e5_the_race_on_a_duplicate_slug_is_still_handled_on_create(client, service):
    """La cattura di UniqueViolation resta dove la corsa e' reale.

    E' stata tolta dalla UPDATE, dove non poteva piu' scattare - l'unico
    vincolo di unicita' di `agencies` e' sullo slug.
    """
    service["store"].unique_violation_on_create = True
    assert _post(client).status_code == 409


def _function_node(module_path, name) -> ast.FunctionDef:
    tree = ast.parse(pathlib_read(module_path))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} non trovata in {module_path}")


def pathlib_read(module_path) -> str:
    return (ROOT / module_path).read_text(encoding="utf-8")


def _raises_and_handlers(fn: ast.FunctionDef) -> set[str]:
    """I nomi sollevati o catturati nel CORPO, docstring esclusi.

    Sull'AST e non sul testo: il docstring di `update_agency` nomina
    `AgencySlugConflict` di proposito, per dire che NON lo solleva piu', e un
    controllo su sottostringa leggerebbe quella spiegazione come una
    violazione. E' lo stesso inciampo gia' incontrato altrove in questa suite.
    """
    found = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Raise) and node.exc is not None:
            call = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
            if isinstance(call, ast.Name):
                found.add(call.id)
        if isinstance(node, ast.ExceptHandler) and node.type is not None:
            found.add(ast.unparse(node.type))
    return found


def test_e5_the_update_path_no_longer_claims_a_conflict_it_cannot_meet():
    """Un `except` che non puo' scattare afferma che il caso esista ancora, e
    manda chi legge a cercare un percorso che non c'e'.

    Con lo slug non piu' aggiornabile, nessuna UPDATE su `agencies` puo'
    violare un vincolo di unicita': l'unico e' `agencies_slug_unq`.
    """
    update = _raises_and_handlers(
        _function_node("platform_admin/agencies_service.py", "update_agency")
    )
    assert "AgencySlugConflict" not in update, update
    assert not any("UniqueViolation" in name for name in update), update
    assert "AgencyNotFound" in update, update

    # Sulla creazione, dove la corsa e' reale, entrambi restano.
    create = _raises_and_handlers(
        _function_node("platform_admin/agencies_service.py", "create_agency")
    )
    assert "AgencySlugConflict" in create, create
    assert any("UniqueViolation" in name for name in create), create


def test_e5_the_patch_route_no_longer_handles_a_409():
    """Stessa regola un livello sopra: il router non traduce un conflitto che
    il service non puo' produrre. Sulla POST resta."""
    patch_route = _raises_and_handlers(
        _function_node("platform_admin/router.py", "update_agency")
    )
    assert "AgencySlugConflict" not in patch_route, patch_route
    assert "AgencyNotFound" in patch_route, patch_route

    post_route = _raises_and_handlers(
        _function_node("platform_admin/router.py", "create_agency")
    )
    assert "AgencySlugConflict" in post_route, post_route
