"""P27-3 - operatori, titolari e ruoli sulla superficie Platform.

Sei endpoint, nessuna DELETE, e quattro regole che il database gia' impone e
che P27-3 deve rendere leggibili invece che scoprirle come errori di indice:

    operator_users_email_unq              una persona, una riga
    agency_memberships_unq                una coppia (agenzia, persona), una riga
    uq_agency_memberships_single_active   una membership attiva per persona
    uq_agency_memberships_single_owner    un titolare attivo per agenzia

Mappa:

    A   repository: forma del SQL, e cio' che il modulo NON fa
    B   identita': creazione, riuso, nessun duplicato
    C   membership: ruoli, stati, sospensione, revoca, riattivazione
    D   titolare: trasferimento atomico, nessuna finestra a due titolari
    E   stato operatore: disable, e la sessione che smette di funzionare
    F   transazione e audit
    G   HTTP e sicurezza della superficie
    H   perimetro: cosa P27-3 non ha toccato

Nessun database: il magazzino in memoria fa rispettare i quattro vincoli, cosi'
le corse si possono provare senza. L'esecuzione su PostgreSQL reale e' riportata
nel report.
"""
from __future__ import annotations

import ast
import inspect
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from psycopg2 import errors

from operator_auth.context import OperatorContext
from platform_admin import audit as platform_audit
from platform_admin import dependencies as platform_deps
from platform_admin import operators_repository, operators_service
from platform_admin.enums import (
    ACTION_MEMBERSHIP_CREATE,
    ACTION_MEMBERSHIP_UPDATE,
    ACTION_OPERATOR_CREATE,
    ACTION_OPERATOR_UPDATE,
    ACTION_OWNER_TRANSFER,
    MEMBERSHIP_ROLES,
    MEMBERSHIP_STATUSES,
    OPERATOR_STATUSES,
    RESULT_ERROR,
    RESULT_SUCCESS,
    ROUTER_PREFIX,
    TARGET_TYPE_MEMBERSHIP,
    TARGET_TYPE_OPERATOR,
)
from platform_admin.exceptions import (
    MembershipNotFound,
    OperatorNotFound,
    PlatformAuditUnavailable,
    PlatformConflict,
)
from platform_admin.router import router as platform_router

ROOT = Path(__file__).resolve().parents[1]
MIGRATION_027 = ROOT / "migrations" / "027_p26_agency_identity.sql"

PLATFORM_USER = 77
EXPIRES = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)
CREATED = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 9, 13, 10, 0, tzinfo=timezone.utc)

AGENCY_A = 1
AGENCY_B = 2


def _ctx(**overrides) -> OperatorContext:
    base = dict(
        user_id=PLATFORM_USER, agency_id=None, role=None,
        is_platform_admin=True, session_id=1, auth_channel="operator_session",
    )
    base.update(overrides)
    return OperatorContext(**base)


class FakeUniqueViolation(errors.UniqueViolation):
    """Una violazione che sa quale vincolo ha rotto.

    `psycopg2.errors.UniqueViolation` espone `diag` come descrittore della
    classe C e non lo si puo' assegnare su un'istanza; una `property` definita
    nella sottoclasse lo copre invece per MRO. Serve perche' il service
    distingue i vincoli PER NOME, e un test che sollevasse una violazione senza
    nome proverebbe solo che il codice non indovina.
    """

    def __init__(self, constraint: str):
        super().__init__(
            f'duplicate key value violates unique constraint "{constraint}"'
        )
        self._constraint = constraint

    @property
    def diag(self):
        return SimpleNamespace(constraint_name=self._constraint)


# ===========================================================================
# A - IL REPOSITORY
# ===========================================================================

class RecordingCursor:
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


OPERATOR_ROW = {
    "id": 10, "email": "owner.b@test.local", "first_name": "Anna",
    "last_name": "Bianchi", "status": "active", "is_platform_admin": False,
    "last_login_at": None, "created_at": CREATED, "updated_at": CREATED,
}
MEMBERSHIP_ROW = {
    "id": 5, "agency_id": AGENCY_A, "operator_user_id": 10,
    "role": "agent", "status": "active",
    "created_at": CREATED, "updated_at": CREATED,
}


def test_a1_no_projection_selects_the_password_hash():
    """La ragione per cui `OPERATOR_COLUMNS` esiste invece di un `SELECT *`."""
    assert "password_hash" not in operators_repository.OPERATOR_COLUMNS
    # Sul SQL ESEGUIBILE: il docstring del modulo nomina `SELECT *` e
    # `password_hash` di proposito, per dire da cosa si tiene lontano.
    statements = _sql_literals(
        ast.parse((ROOT / "platform_admin" / "operators_repository.py").read_text())
    )
    assert statements, "nessun SQL trovato: la prova sarebbe vuota"
    for statement in statements:
        if "SELECT" in statement.upper():
            assert "password_hash" not in statement, statement
            assert "SELECT *" not in statement.upper(), statement


def test_a1_the_only_place_the_hash_appears_is_the_insert():
    """Ci arriva gia' calcolato, e non torna indietro: il RETURNING della
    INSERT e' la stessa proiezione di una lettura."""
    cur = RecordingCursor([OPERATOR_ROW])
    operators_repository.create_operator(
        cur, email="a@b.test", email_normalized="a@b.test",
        password_hash="pbkdf2_sha256$x", first_name=None, last_name=None,
        status="active",
    )
    sql = cur.last_sql
    assert "password_hash" in sql.split("RETURNING")[0]
    assert "password_hash" not in sql.split("RETURNING")[1]


def test_a2_the_join_does_not_select_two_columns_named_id():
    """Un RealDictCursor che ne riceve due tiene l'ultima, e la riga direbbe
    che l'operatore ha l'id della sua membership - in silenzio e in modo
    plausibile."""
    cur = RecordingCursor([])
    operators_repository.list_agency_operators(cur, AGENCY_A)
    projection = cur.last_sql.split(" FROM ")[0]
    assert "u.id" not in projection, projection
    assert "m.id" in projection, projection
    assert "m.operator_user_id" in projection, projection


def test_a3_get_membership_does_not_filter_on_status():
    """Domanda diversa da `operator_auth.membership_exists`.

    Li' e' "puo' operare adesso?", qui e' "esiste una riga?" - e una riga
    revocata occupa comunque la coppia protetta da UNIQUE.
    """
    cur = RecordingCursor([MEMBERSHIP_ROW])
    operators_repository.get_membership(cur, AGENCY_A, 10)
    # Sulla WHERE: `m.status` compare legittimamente nella proiezione, perche'
    # lo stato e' uno dei campi che la risposta contiene. Cercarlo nell'intera
    # istruzione darebbe un rosso su una riga corretta.
    where = cur.last_sql.split(" WHERE ")[1]
    assert "status" not in where, where
    assert cur.last_params == (AGENCY_A, 10)


def test_a3_active_membership_elsewhere_excludes_the_target_agency():
    cur = RecordingCursor()
    operators_repository.active_membership_elsewhere(
        cur, 10, excluding_agency_id=AGENCY_A
    )
    assert "m.status = 'active'" in cur.last_sql
    assert "m.agency_id <> %s" in cur.last_sql
    assert cur.last_params == (10, AGENCY_A)


def test_a4_the_demotion_targets_the_active_owner_only():
    """Non un id: il titolare in carica, qualunque sia. E' cio' che rende la
    prima meta' del trasferimento indipendente da una lettura precedente."""
    cur = RecordingCursor([MEMBERSHIP_ROW])
    operators_repository.demote_active_owner(cur, AGENCY_A, to_role="agency_admin")
    sql = cur.last_sql
    assert "role = 'agency_owner'" in sql
    assert "status = 'active'" in sql
    assert cur.last_params == ("agency_admin", AGENCY_A)


@pytest.mark.parametrize("fields", [{"email": "x@y.z"}, {"is_platform_admin": True},
                                    {"password_hash": "x"}, {"id": 3}, {}])
def test_a5_operator_update_refuses_a_column_it_does_not_own(fields):
    """Rumorosamente. Su `email` e `is_platform_admin` uno scarto in silenzio
    sarebbe il modo peggiore di far fallire una richiesta."""
    cur = RecordingCursor([OPERATOR_ROW])
    with pytest.raises(ValueError):
        operators_repository.update_operator(cur, 10, fields)
    assert cur.calls == []


@pytest.mark.parametrize("fields", [{"agency_id": 2}, {"operator_user_id": 3}, {}])
def test_a5_membership_update_refuses_a_column_it_does_not_own(fields):
    """Riassegnare una membership a un'altra agenzia cambiando `agency_id`
    aggirerebbe ogni controllo sulla creazione."""
    cur = RecordingCursor([MEMBERSHIP_ROW])
    with pytest.raises(ValueError):
        operators_repository.update_membership(cur, 5, fields)
    assert cur.calls == []


def test_a6_both_updates_stamp_updated_at():
    for call, ident in (
        (operators_repository.update_operator, {"first_name": "X"}),
        (operators_repository.update_membership, {"role": "agent"}),
    ):
        cur = RecordingCursor([OPERATOR_ROW])
        call(cur, 1, ident)
        assert "updated_at = NOW()" in cur.last_sql


def test_a7_the_repository_opens_no_connection_and_writes_no_audit():
    tree = ast.parse(
        (ROOT / "platform_admin" / "operators_repository.py").read_text()
    )
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imported.update(a.name for a in node.names)
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("fastapi"), node.module
                assert "audit" not in node.module, node.module
    for forbidden in ("get_connection", "platform_operation_cursor", "audit"):
        assert forbidden not in names, forbidden
        assert forbidden not in imported, forbidden


def test_a8_the_repository_deletes_nothing():
    """`revoked` e `disabled` sono stati. La relazione fra una persona e
    un'agenzia e' un fatto storico."""
    tree = ast.parse(
        (ROOT / "platform_admin" / "operators_repository.py").read_text()
    )
    sql = " ".join(_sql_literals(tree)).upper()
    assert "DELETE" not in sql, sql
    assert "TRUNCATE" not in sql, sql
    functions = [
        n.name for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    assert not any("delete" in name for name in functions), functions


def _sql_literals(tree: ast.Module) -> list[str]:
    """Le stringhe eseguibili, senza i docstring - che nominano di proposito
    cio' da cui il modulo si tiene lontano."""
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


# ===========================================================================
# IL MAGAZZINO: FA RISPETTARE I QUATTRO VINCOLI
#
# Non e' un dettaglio della fixture. I quattro indici unici sono la meta' della
# specifica di P27-3, e un magazzino che li ignorasse renderebbe verdi i test
# sulle corse senza che nulla le abbia impedite. Qui una violazione solleva
# `FakeUniqueViolation` con il NOME del vincolo, esattamente come PostgreSQL,
# cosi' la traduzione in 409 si puo' provare per vincolo e non in blocco.
#
# La transazione e' simulata per intero - snapshot all'apertura, restore sul
# rollback, commit irreversibile - per la stessa ragione di P27-2: senza,
# "l'operatore non e' stato creato" non sarebbe verificabile.
# ===========================================================================

class Store:
    def __init__(self):
        self.agencies = {
            AGENCY_A: {"id": AGENCY_A, "name": "Agenzia A", "slug": "agenzia-a",
                       "status": "active", "settings": {},
                       "created_at": CREATED, "updated_at": CREATED},
            AGENCY_B: {"id": AGENCY_B, "name": "Agenzia B", "slug": "agenzia-b",
                       "status": "active", "settings": {},
                       "created_at": CREATED, "updated_at": CREATED},
        }
        self.operators: dict[int, dict] = {}
        self.memberships: dict[int, dict] = {}
        self.next_operator_id = 10
        self.next_membership_id = 100
        self._snapshot = None

    # -- vincoli -----------------------------------------------------------
    def check_operator_unique(self, email_normalized, *, excluding=None):
        for row in self.operators.values():
            if row["email_normalized"] == email_normalized and row["id"] != excluding:
                raise FakeUniqueViolation("operator_users_email_unq")

    def check_membership_constraints(self, candidate):
        for row in self.memberships.values():
            if row["id"] == candidate["id"]:
                continue
            if (row["agency_id"] == candidate["agency_id"]
                    and row["operator_user_id"] == candidate["operator_user_id"]):
                raise FakeUniqueViolation("agency_memberships_unq")
            if (candidate["status"] == "active" and row["status"] == "active"
                    and row["operator_user_id"] == candidate["operator_user_id"]):
                raise FakeUniqueViolation("uq_agency_memberships_single_active")
            if (candidate["status"] == "active" and candidate["role"] == "agency_owner"
                    and row["status"] == "active" and row["role"] == "agency_owner"
                    and row["agency_id"] == candidate["agency_id"]):
                raise FakeUniqueViolation("uq_agency_memberships_single_owner")

    # -- transazione -------------------------------------------------------
    def snapshot(self):
        self._snapshot = (
            {k: dict(v) for k, v in self.operators.items()},
            {k: dict(v) for k, v in self.memberships.items()},
            self.next_operator_id, self.next_membership_id,
        )

    def restore(self):
        if self._snapshot is None:
            return
        (self.operators, self.memberships,
         self.next_operator_id, self.next_membership_id) = self._snapshot
        self._snapshot = None

    def commit(self):
        self._snapshot = None


class FakeConn:
    """Commit irreversibile e presente nella stessa sequenza delle scritture.

    Le due proprieta' che in P27-2 si erano rivelate necessarie: senza la
    prima, un rollback "annullerebbe" dati gia' committati; senza la seconda,
    invertire commit e audit lascerebbe la sequenza intatta.
    """

    def __init__(self, *, commit_fails=False, store=None, order=None):
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


@pytest.fixture
def service(monkeypatch):
    state = {
        "conn": FakeConn(), "store": Store(), "audit": [],
        "audit_fails": False, "order": [], "session": _ctx(),
    }
    store = state["store"]

    @contextmanager
    def _cursor():
        state["order"].append("open")
        conn = state["conn"]
        conn.store = store
        conn.order = state["order"]
        store.snapshot()
        try:
            yield conn, object()
        except Exception:
            conn.rollback()
            raise
        else:
            store.restore()

    monkeypatch.setattr(operators_service, "platform_operation_cursor", _cursor)

    agencies_repo = operators_service.agencies_repository
    monkeypatch.setattr(
        agencies_repo, "get_agency",
        lambda cur, agency_id: (dict(store.agencies[agency_id])
                                if agency_id in store.agencies else None),
    )

    repo = operators_service.operators_repository

    def _get_operator(cur, operator_user_id):
        row = store.operators.get(operator_user_id)
        return _projected(row, operators_repository.OPERATOR_COLUMNS) if row else None

    def _find_by_email(cur, email_normalized):
        for row in store.operators.values():
            if row["email_normalized"] == email_normalized:
                return _projected(row, operators_repository.OPERATOR_COLUMNS)
        return None

    def _create_operator(cur, *, email, email_normalized, password_hash,
                         first_name, last_name, status):
        # Il TENTATIVO viene registrato prima della verifica dei vincoli, ed e'
        # la distinzione che rende osservabili le guardie applicative: un
        # vincolo rifiuta la INSERT atomicamente - dall'esterno non si vede una
        # scrittura avvenuta e poi annullata - quindi l'unico modo di sapere se
        # la guardia ha fatto il suo lavoro e' vedere se la scrittura e' stata
        # TENTATA. Trovato con una mutazione: senza questa riga, togliere il
        # controllo su single-active-membership lasciava la suite verde.
        state["order"].append("attempt-operator")
        store.check_operator_unique(email_normalized)
        state["order"].append("write-operator")
        row = {
            "id": store.next_operator_id, "email": email,
            "email_normalized": email_normalized, "password_hash": password_hash,
            "first_name": first_name, "last_name": last_name, "status": status,
            "is_platform_admin": False, "last_login_at": None,
            "created_at": CREATED, "updated_at": CREATED,
        }
        store.operators[row["id"]] = row
        store.next_operator_id += 1
        return _projected(row, operators_repository.OPERATOR_COLUMNS)

    def _create_membership(cur, *, agency_id, operator_user_id, role, status):
        candidate = {
            "id": store.next_membership_id, "agency_id": agency_id,
            "operator_user_id": operator_user_id, "role": role, "status": status,
            "created_at": CREATED, "updated_at": CREATED,
        }
        state["order"].append("attempt-membership")
        store.check_membership_constraints(candidate)
        state["order"].append("write-membership")
        store.memberships[candidate["id"]] = candidate
        store.next_membership_id += 1
        return dict(candidate)

    def _get_membership(cur, agency_id, operator_user_id):
        for row in store.memberships.values():
            if (row["agency_id"] == agency_id
                    and row["operator_user_id"] == operator_user_id):
                return dict(row)
        return None

    def _list_memberships(cur, operator_user_id):
        return [dict(r) for r in store.memberships.values()
                if r["operator_user_id"] == operator_user_id]

    def _active_elsewhere(cur, operator_user_id, *, excluding_agency_id):
        for row in store.memberships.values():
            if (row["operator_user_id"] == operator_user_id
                    and row["status"] == "active"
                    and row["agency_id"] != excluding_agency_id):
                return dict(row)
        return None

    def _active_owner(cur, agency_id):
        for row in store.memberships.values():
            if (row["agency_id"] == agency_id and row["role"] == "agency_owner"
                    and row["status"] == "active"):
                return dict(row)
        return None

    def _list_agency_operators(cur, agency_id):
        out = []
        for m in sorted(store.memberships.values(), key=lambda r: r["id"]):
            if m["agency_id"] != agency_id:
                continue
            u = store.operators[m["operator_user_id"]]
            row = {k: u[k] for k in operators_repository.OPERATOR_COLUMNS if k != "id"}
            row.update(m)
            out.append(row)
        return out

    def _update_operator(cur, operator_user_id, fields):
        operators_repository._reject_unknown(
            fields, operators_repository.OPERATOR_UPDATABLE_COLUMNS, "operator_users")
        state["order"].append("write-operator")
        row = store.operators.get(operator_user_id)
        if row is None:
            return None
        row.update(fields)
        row["updated_at"] = LATER
        return _projected(row, operators_repository.OPERATOR_COLUMNS)

    def _update_membership(cur, membership_id, fields):
        operators_repository._reject_unknown(
            fields, operators_repository.MEMBERSHIP_UPDATABLE_COLUMNS,
            "agency_memberships")
        row = store.memberships.get(membership_id)
        if row is None:
            return None
        candidate = {**row, **fields}
        state["order"].append("attempt-membership")
        store.check_membership_constraints(candidate)
        state["order"].append("write-membership")
        row.update(fields)
        row["updated_at"] = LATER
        return dict(row)

    def _demote(cur, agency_id, *, to_role):
        for row in store.memberships.values():
            if (row["agency_id"] == agency_id and row["role"] == "agency_owner"
                    and row["status"] == "active"):
                state["order"].append("demote")
                row["role"] = to_role
                row["updated_at"] = LATER
                return dict(row)
        return None

    for name, fake in (
        ("get_operator", _get_operator),
        ("find_operator_by_normalized_email", _find_by_email),
        ("create_operator", _create_operator),
        ("create_membership", _create_membership),
        ("get_membership", _get_membership),
        ("list_memberships_of_operator", _list_memberships),
        ("active_membership_elsewhere", _active_elsewhere),
        ("active_owner_of", _active_owner),
        ("list_agency_operators", _list_agency_operators),
        ("update_operator", _update_operator),
        ("update_membership", _update_membership),
        ("demote_active_owner", _demote),
    ):
        monkeypatch.setattr(repo, name, fake)

    def _record(**kwargs):
        is_admission = kwargs.get("action") == "platform.admission"
        is_compensating = kwargs.get("result") == RESULT_ERROR
        if state["audit_fails"] and not is_admission and not is_compensating:
            state["order"].append("audit-failed")
            raise PlatformAuditUnavailable("indisponibile")
        state["order"].append(
            "admission" if is_admission
            else "audit-error" if is_compensating else "audit"
        )
        state["audit"].append(kwargs)
        return len(state["audit"])

    monkeypatch.setattr(platform_audit, "record", _record)
    state["operations"] = lambda: [
        e for e in state["audit"] if e["action"] != "platform.admission"
    ]
    return state


def _projected(row, columns):
    return {key: row[key] for key in columns}


def _make(service, agency_id=AGENCY_A, email="uno@test.local", role="agent",
          password="una-password", **kw):
    """Crea una persona NUOVA: la password serve.

    Per il percorso "email gia' nota" c'e' `_attach`, che non la manda - e non
    puo' mandarla, perche' la credenziale di una persona esistente non si tocca
    da questa route.
    """
    return operators_service.create_agency_operator(
        _ctx(), agency_id, email=email, password=password, first_name=None,
        last_name=None, operator_status=kw.pop("operator_status", "active"),
        role=role, created_fields=kw.pop("created_fields", ["email", "role"]),
    )


def _attach(service, agency_id=AGENCY_B, email="uno@test.local", role="agent"):
    """Lega un'identita' GIA' ESISTENTE a un'agenzia. Nessuna password."""
    return operators_service.create_agency_operator(
        _ctx(), agency_id, email=email, password=None, first_name=None,
        last_name=None, operator_status="active", role=role,
        created_fields=["email", "role"],
    )


# ===========================================================================
# B - IDENTITA': UNA PERSONA, UNA RIGA
# ===========================================================================

def test_b1_a_new_email_creates_one_operator_and_one_membership(service):
    created = _make(service)
    assert len(service["store"].operators) == 1
    assert len(service["store"].memberships) == 1
    assert created["operator"]["email"] == "uno@test.local"
    assert created["membership"]["agency_id"] == AGENCY_A


def test_b1_the_operator_and_the_membership_are_written_in_one_transaction(service):
    _make(service)
    assert service["order"] == [
        "open", "attempt-operator", "write-operator",
        "attempt-membership", "write-membership", "audit", "commit"
    ]


def test_b2_an_existing_email_does_not_create_a_second_operator(service):
    """`operator_users_email_unq` e' GLOBALE: il login riceve un'email e
    nient'altro, quindi due righe con la stessa email renderebbero ambigua la
    risoluzione della persona."""
    first = _make(service)
    operators_service.update_membership(
        _ctx(), AGENCY_A, first["operator"]["id"], {"status": "revoked"}
    )
    second = _attach(service)
    assert len(service["store"].operators) == 1
    assert second["operator"]["id"] == first["operator"]["id"]
    assert second["membership"]["agency_id"] == AGENCY_B


def test_b2_the_existing_operator_path_writes_no_operator_row(service):
    first = _make(service)
    operators_service.update_membership(
        _ctx(), AGENCY_A, first["operator"]["id"], {"status": "revoked"}
    )
    service["order"].clear()
    _attach(service)
    assert "attempt-operator" not in service["order"], service["order"]


def test_b3_the_email_is_matched_through_the_projects_own_normalisation(service):
    """Non una seconda normalizzazione: `core.normalization.normalize_email` e'
    la stessa funzione che CORE usa sui contatti, ed e' l'idea di "stesso
    indirizzo" su cui `email_normalized` e' UNIQUE."""
    from core.normalization import normalize_email

    _make(service, email="Mario.Rossi@Test.Local")
    stored = list(service["store"].operators.values())[0]
    assert stored["email_normalized"] == normalize_email("Mario.Rossi@Test.Local")
    assert stored["email_normalized"] == "mario.rossi@test.local"

    with pytest.raises(PlatformConflict):
        _make(service, agency_id=AGENCY_B, email="  MARIO.ROSSI@TEST.LOCAL  ")


def test_b3_the_service_does_not_define_its_own_normalisation(service):
    source = (ROOT / "platform_admin" / "operators_service.py").read_text()
    assert "from core.normalization import normalize_email" in source
    assert ".lower()" not in source, "normalizzazione riscritta a mano"


def test_b4_the_password_is_hashed_with_the_projects_own_function(service):
    """Riuso completo di `operator_auth.security`: e' la stessa funzione che
    verifica il login, e il formato e' quello che il CHECK della 027 impone."""
    from operator_auth.security import verify_password

    _make(service)
    stored = list(service["store"].operators.values())[0]["password_hash"]
    assert stored.startswith("pbkdf2_sha256$")
    assert stored != "una-password"
    assert verify_password("una-password", stored) is True
    assert verify_password("sbagliata", stored) is False


def test_b4_no_plaintext_password_is_stored_anywhere(service):
    _make(service)
    blob = repr(service["store"].operators) + repr(service["store"].memberships)
    assert "una-password" not in blob, blob


def test_b4_the_password_never_reaches_the_audit(service):
    _make(service)
    blob = repr(service["audit"])
    assert "una-password" not in blob
    assert "password" not in blob, blob


def test_b5_an_operator_with_an_active_membership_elsewhere_is_refused(service):
    """409, e NESSUNA revoca automatica della precedente.

    Trasferire qualcuno fra due affiliati e' una decisione commerciale che deve
    essere chiesta, non un effetto collaterale di una creazione.
    """
    first = _make(service)
    with pytest.raises(PlatformConflict):
        _attach(service)
    membership = list(service["store"].memberships.values())[0]
    assert membership["status"] == "active", "la membership precedente e' stata toccata"
    assert membership["agency_id"] == AGENCY_A
    assert len(service["store"].memberships) == 1


@pytest.mark.parametrize("status", ["active", "suspended", "revoked"])
def test_b6_an_existing_membership_in_the_same_agency_is_a_conflict(service, status):
    """In OGNI stato. Riattivare un rapporto revocato assegnandogli per giunta
    un ruolo nuovo, dentro una chiamata che si chiama "crea", e' il genere di
    scorciatoia che si scopre dopo: si fa dalla PATCH."""
    created = _make(service)
    if status != "active":
        operators_service.update_membership(
            _ctx(), AGENCY_A, created["operator"]["id"], {"status": status}
        )
    with pytest.raises(PlatformConflict):
        _make(service)
    assert len(service["store"].memberships) == 1


def test_b7_a_lost_race_on_the_email_becomes_a_conflict(service, monkeypatch):
    """Il controllo applicativo e la INSERT non sono un'operazione sola."""
    def _racing(cur, **kwargs):
        raise FakeUniqueViolation("operator_users_email_unq")

    monkeypatch.setattr(
        operators_service.operators_repository, "create_operator", _racing
    )
    with pytest.raises(PlatformConflict):
        _make(service)


@pytest.mark.parametrize("constraint", [
    "agency_memberships_unq",
    "uq_agency_memberships_single_active",
    "uq_agency_memberships_single_owner",
])
def test_b7_every_expected_constraint_becomes_a_conflict(service, monkeypatch, constraint):
    def _racing(cur, **kwargs):
        raise FakeUniqueViolation(constraint)

    monkeypatch.setattr(
        operators_service.operators_repository, "create_membership", _racing
    )
    with pytest.raises(PlatformConflict):
        _make(service)


def test_b7_an_unexpected_constraint_is_not_disguised_as_a_conflict(service, monkeypatch):
    """NON si cattura genericamente ogni violazione di integrita'.

    Un vincolo che non e' fra quelli previsti e' un difetto, e deve salire come
    tale: un 409 inventato direbbe al chiamante di cambiare la richiesta,
    quando magari deve cambiarla chi ha scritto il codice.
    """
    def _racing(cur, **kwargs):
        raise FakeUniqueViolation("qualche_altro_indice")

    monkeypatch.setattr(
        operators_service.operators_repository, "create_membership", _racing
    )
    with pytest.raises(errors.UniqueViolation):
        _make(service)


def test_b7_a_violation_without_a_constraint_name_is_not_guessed(service, monkeypatch):
    def _racing(cur, **kwargs):
        raise errors.UniqueViolation("duplicate key, senza diagnostica")

    monkeypatch.setattr(
        operators_service.operators_repository, "create_membership", _racing
    )
    with pytest.raises(errors.UniqueViolation):
        _make(service)


def test_b8_a_conflict_message_never_names_a_database_constraint(service):
    _make(service)
    with pytest.raises(PlatformConflict) as excinfo:
        _make(service)
    message = str(excinfo.value)
    for leak in ("uq_", "_unq", "duplicate key", "constraint", "psycopg"):
        assert leak not in message, (leak, message)


def test_b9_a_missing_agency_is_not_found(service):
    with pytest.raises(operators_service.AgencyNotFound):
        _make(service, agency_id=999999)
    assert service["store"].operators == {}, "un operatore e' nato comunque"


# ===========================================================================
# C - MEMBERSHIP: RUOLI E STATI
# ===========================================================================

@pytest.mark.parametrize("role", ["agent", "agency_admin", "agency_owner"])
def test_c1_every_role_can_be_assigned_at_creation(service, role):
    created = _make(service, role=role)
    assert created["membership"]["role"] == role


def test_c1_a_second_owner_at_creation_is_a_conflict(service):
    _make(service, role="agency_owner")
    with pytest.raises(PlatformConflict):
        _make(service, email="due@test.local", role="agency_owner")


@pytest.mark.parametrize("status", ["suspended", "revoked"])
def test_c2_a_membership_can_be_suspended_and_revoked(service, status):
    created = _make(service)
    updated = operators_service.update_membership(
        _ctx(), AGENCY_A, created["operator"]["id"], {"status": status}
    )
    assert updated["status"] == status
    assert len(service["store"].memberships) == 1, "la riga e' stata sostituita"


def test_c2_revoking_never_deletes_the_row(service):
    created = _make(service)
    operators_service.update_membership(
        _ctx(), AGENCY_A, created["operator"]["id"], {"status": "revoked"}
    )
    assert list(service["store"].memberships.values())[0]["status"] == "revoked"


def test_c3_a_suspended_membership_can_be_reactivated(service):
    created = _make(service)
    operator_id = created["operator"]["id"]
    operators_service.update_membership(
        _ctx(), AGENCY_A, operator_id, {"status": "suspended"}
    )
    back = operators_service.update_membership(
        _ctx(), AGENCY_A, operator_id, {"status": "active"}
    )
    assert back["status"] == "active"


def test_c3_reactivation_is_refused_when_another_membership_is_active(service):
    """`uq_agency_memberships_single_active`: una sola alla volta, e la
    riattivazione non revoca in silenzio l'altra."""
    first = _make(service)
    operator_id = first["operator"]["id"]
    operators_service.update_membership(
        _ctx(), AGENCY_A, operator_id, {"status": "revoked"}
    )
    _attach(service)
    with pytest.raises(PlatformConflict):
        operators_service.update_membership(
            _ctx(), AGENCY_A, operator_id, {"status": "active"}
        )
    elsewhere = [m for m in service["store"].memberships.values()
                 if m["agency_id"] == AGENCY_B][0]
    assert elsewhere["status"] == "active", "l'altra membership e' stata toccata"


def test_c4_the_owner_role_cannot_be_assigned_through_the_membership_patch(service):
    """409 e non 422: il ruolo esiste e la richiesta e' ben formata.

    Cio' che non va e' lo STATO del sistema - assegnarlo richiede di degradare
    qualcun altro, cioe' di cambiare una riga che la richiesta non nomina.
    """
    created = _make(service)
    with pytest.raises(PlatformConflict):
        operators_service.update_membership(
            _ctx(), AGENCY_A, created["operator"]["id"], {"role": "agency_owner"}
        )


def test_c4_the_other_two_roles_can_be_assigned_freely(service):
    created = _make(service)
    operator_id = created["operator"]["id"]
    for role in ("agency_admin", "agent"):
        updated = operators_service.update_membership(
            _ctx(), AGENCY_A, operator_id, {"role": role}
        )
        assert updated["role"] == role


def test_c5_a_membership_that_does_not_exist_is_not_found(service):
    _make(service)
    with pytest.raises(MembershipNotFound):
        operators_service.update_membership(_ctx(), AGENCY_B, 10, {"status": "suspended"})


def test_c5_a_patch_on_an_absent_agency_is_not_found(service):
    created = _make(service)
    with pytest.raises(operators_service.AgencyNotFound):
        operators_service.update_membership(
            _ctx(), 999999, created["operator"]["id"], {"status": "suspended"}
        )


# ===========================================================================
# D - IL TITOLARE
# ===========================================================================

def _two_members(service):
    """A titolare, B agency_admin, entrambi in AGENCY_A."""
    a = _make(service, email="a@test.local", role="agency_owner")
    b = _make(service, email="b@test.local", role="agency_admin")
    return a["operator"]["id"], b["operator"]["id"]


def test_d1_an_agency_has_at_most_one_active_owner(service):
    _make(service, email="a@test.local", role="agency_owner")
    with pytest.raises(PlatformConflict):
        _make(service, email="b@test.local", role="agency_owner")
    owners = [m for m in service["store"].memberships.values()
              if m["role"] == "agency_owner" and m["status"] == "active"]
    assert len(owners) == 1


def test_d2_the_transfer_promotes_b_and_demotes_a(service):
    a, b = _two_members(service)
    result = operators_service.transfer_owner(_ctx(), AGENCY_A, b)

    assert result["membership"]["operator_user_id"] == b
    assert result["membership"]["role"] == "agency_owner"
    assert result["demoted"]["operator_user_id"] == a
    assert result["demoted"]["role"] == "agency_admin"


def test_d2_the_previous_owner_is_demoted_and_never_revoked(service):
    """Un titolare sostituito resta una persona che lavora li'. Toglierle
    l'accesso sarebbe una seconda decisione che nessuno ha preso."""
    a, b = _two_members(service)
    operators_service.transfer_owner(_ctx(), AGENCY_A, b)
    previous = [m for m in service["store"].memberships.values()
                if m["operator_user_id"] == a][0]
    assert previous["status"] == "active"
    assert previous["role"] == "agency_admin"


def test_d3_the_demotion_happens_before_the_promotion(service):
    """L'ORDINE NON E' NEGOZIABILE.

    `uq_agency_memberships_single_owner` e' un indice unico parziale verificato
    a ogni istruzione: promuovere B per primo lo violerebbe subito, dentro la
    transazione, e nessun riordino successivo potrebbe rimediarlo. Degradando
    per primo, la sequenza attraversa uno stato con ZERO titolari, che l'indice
    ammette.
    """
    _, b = _two_members(service)
    service["order"].clear()
    operators_service.transfer_owner(_ctx(), AGENCY_A, b)
    assert service["order"] == [
        "open", "demote", "attempt-membership", "write-membership",
        "audit", "commit"
    ], service["order"]


def test_d3_no_committed_state_ever_holds_two_active_owners(service):
    """La prova sul magazzino, non sull'ordine: dopo il commit c'e' un
    titolare, e durante la transazione il vincolo non e' mai stato violato -
    se lo fosse stato, il magazzino avrebbe sollevato."""
    a, b = _two_members(service)
    operators_service.transfer_owner(_ctx(), AGENCY_A, b)
    owners = [m for m in service["store"].memberships.values()
              if m["role"] == "agency_owner" and m["status"] == "active"]
    assert [m["operator_user_id"] for m in owners] == [b]


def test_d4_a_transfer_to_the_current_owner_writes_nothing(service):
    """Registrare un trasferimento da B a B sarebbe una riga falsa in un
    registro che non si puo' correggere."""
    a, _ = _two_members(service)
    service["order"].clear()
    service["audit"].clear()
    result = operators_service.transfer_owner(_ctx(), AGENCY_A, a)
    assert result["demoted"] is None
    assert "demote" not in service["order"], service["order"]
    assert "commit" not in service["order"], service["order"]
    assert service["audit"] == []


def test_d5_an_agency_without_an_owner_gets_one_without_a_demotion(service):
    """Il trasferimento copre anche la prima assegnazione: un'agenzia il cui
    titolare e' stato revocato non resta senza per sempre."""
    b = _make(service, email="b@test.local", role="agency_admin")["operator"]["id"]
    result = operators_service.transfer_owner(_ctx(), AGENCY_A, b)
    assert result["membership"]["role"] == "agency_owner"
    assert result["demoted"] is None
    assert service["audit"][-1]["metadata"] == {"demoted_previous_owner": False}


def test_d6_the_new_owner_must_already_belong_to_the_agency(service):
    """Inventargli una membership qui significherebbe farlo entrare
    nell'agenzia da una route che parla di titolarita', saltando i controlli
    della creazione - a partire da "ha gia' una membership attiva altrove?"."""
    _make(service, email="a@test.local", role="agency_owner")
    outsider = _make(
        service, agency_id=AGENCY_B, email="fuori@test.local", role="agent"
    )["operator"]["id"]
    with pytest.raises(MembershipNotFound):
        operators_service.transfer_owner(_ctx(), AGENCY_A, outsider)
    assert len(service["store"].memberships) == 2, "e' nata una membership"


def test_d6_the_new_owner_must_have_an_active_membership(service):
    """Promuovere una membership sospesa creerebbe un titolare che non puo'
    lavorare - e l'indice unico, che guarda solo le righe attive, non se ne
    accorgerebbe."""
    a, b = _two_members(service)
    operators_service.update_membership(_ctx(), AGENCY_A, b, {"status": "suspended"})
    with pytest.raises(PlatformConflict):
        operators_service.transfer_owner(_ctx(), AGENCY_A, b)
    owner = [m for m in service["store"].memberships.values()
             if m["operator_user_id"] == a][0]
    assert owner["role"] == "agency_owner", "A e' stato degradato comunque"


def test_d7_an_unknown_operator_is_not_found(service):
    _make(service, email="a@test.local", role="agency_owner")
    with pytest.raises(OperatorNotFound):
        operators_service.transfer_owner(_ctx(), AGENCY_A, 999999)


def test_d8_a_failed_audit_rolls_the_whole_transfer_back(service):
    """Entrambe le righe, non una."""
    a, b = _two_members(service)
    service["audit_fails"] = True
    with pytest.raises(PlatformAuditUnavailable):
        operators_service.transfer_owner(_ctx(), AGENCY_A, b)

    owners = {m["operator_user_id"]: m for m in service["store"].memberships.values()}
    assert owners[a]["role"] == "agency_owner", "A e' rimasto degradato"
    assert owners[b]["role"] == "agency_admin", "B e' rimasto promosso"


def test_d9_a_race_on_the_owner_constraint_becomes_a_conflict(service, monkeypatch):
    a, b = _two_members(service)
    real = operators_service.operators_repository.update_membership

    def _racing(cur, membership_id, fields):
        if fields.get("role") == "agency_owner":
            raise FakeUniqueViolation("uq_agency_memberships_single_owner")
        return real(cur, membership_id, fields)

    monkeypatch.setattr(
        operators_service.operators_repository, "update_membership", _racing
    )
    with pytest.raises(PlatformConflict):
        operators_service.transfer_owner(_ctx(), AGENCY_A, b)


# ===========================================================================
# E - STATO DELL'OPERATORE
# ===========================================================================

@pytest.mark.parametrize("status", OPERATOR_STATUSES)
def test_e1_an_operator_can_be_moved_through_every_declared_status(service, status):
    created = _make(service)
    updated = operators_service.update_operator(
        _ctx(), created["operator"]["id"], {"status": status}
    )
    assert updated["status"] == status


def test_e2_disabling_an_operator_leaves_the_membership_alone(service):
    """Sono due fatti diversi: la persona non puo' entrare, il rapporto con
    l'agenzia resta quello che era."""
    created = _make(service)
    operators_service.update_operator(
        _ctx(), created["operator"]["id"], {"status": "disabled"}
    )
    assert list(service["store"].memberships.values())[0]["status"] == "active"


def test_e3_a_disabled_operator_cannot_use_an_existing_session():
    """LA LOGICA E' GIA' IN operator_auth, E P27-3 NON NE SCRIVE UNA SECONDA.

    `resolve_session` rilegge `u.status` a ogni richiesta e `_scope_is_usable`
    rifiuta tutto cio' che non e' `active`. Questo test esercita QUELLA
    funzione con la riga che una disabilitazione produce: se P27-3 avesse
    scritto una propria invalidazione, questa prova continuerebbe a passare
    mentre le due logiche divergono - per questo il test guarda l'originale.
    """
    from operator_auth.service import _scope_is_usable

    vivo = {
        "user_status": "active", "is_platform_admin": False,
        "membership_status": "active", "agency_status": "active",
    }
    assert _scope_is_usable(vivo) is True
    assert _scope_is_usable({**vivo, "user_status": "disabled"}) is False
    assert _scope_is_usable({**vivo, "user_status": "invited"}) is False
    # E le altre due meta' della stessa regola, che P27-3 non tocca.
    assert _scope_is_usable({**vivo, "membership_status": "suspended"}) is False
    assert _scope_is_usable({**vivo, "membership_status": "revoked"}) is False
    assert _scope_is_usable({**vivo, "agency_status": "suspended"}) is False


def test_e3_p27_3_does_not_reimplement_session_invalidation():
    """Controllo strutturale del test sopra: nessun modulo di P27-3 tocca le
    sessioni."""
    for name in ("operators_service.py", "operators_repository.py"):
        source = (ROOT / "platform_admin" / name).read_text()
        tree = ast.parse(source)
        sql = " ".join(_sql_literals(tree)).upper()
        assert "OPERATOR_SESSIONS" not in sql, name
        assert "REVOKED_AT" not in sql, name


# ===========================================================================
# F - TRANSAZIONE E AUDIT
# ===========================================================================

MUTATIONS = ("create", "update_operator", "update_membership", "transfer")


def _run_mutation(service, which):
    if which == "create":
        return _make(service)
    created = _make(service, email="base@test.local", role="agency_admin")
    operator_id = created["operator"]["id"]
    if which == "update_operator":
        return operators_service.update_operator(
            _ctx(), operator_id, {"first_name": "Anna"}
        )
    if which == "update_membership":
        return operators_service.update_membership(
            _ctx(), AGENCY_A, operator_id, {"status": "suspended"}
        )
    return operators_service.transfer_owner(_ctx(), AGENCY_A, operator_id)


@pytest.mark.parametrize("which", MUTATIONS)
def test_f1_every_mutation_audits_before_it_commits(service, which):
    _run_mutation(service, which)
    order = service["order"]
    assert "audit" in order and "commit" in order, order
    assert order.index("audit") < order.index("commit"), order


@pytest.mark.parametrize("which", MUTATIONS)
def test_f2_a_failed_audit_prevents_the_commit(service, which):
    """La regola, su tutte e quattro: nessuna modifica amministrativa viene
    committata se il suo audit non e' stato scritto."""
    if which != "create":
        _make(service, email="base@test.local", role="agency_admin")
    service["audit_fails"] = True
    service["conn"].events.clear()

    with pytest.raises(PlatformAuditUnavailable):
        if which == "create":
            _make(service)
        elif which == "update_operator":
            operators_service.update_operator(_ctx(), 10, {"first_name": "Anna"})
        elif which == "update_membership":
            operators_service.update_membership(
                _ctx(), AGENCY_A, 10, {"status": "suspended"}
            )
        else:
            operators_service.transfer_owner(_ctx(), AGENCY_A, 10)

    assert "commit" not in service["conn"].events, service["conn"].events
    assert service["conn"].events[-1] == "rollback"


def test_f2_a_failed_audit_on_create_leaves_no_operator_behind(service):
    """La prova che conta: non "non e' stato committato", ma "non c'e'"."""
    service["audit_fails"] = True
    with pytest.raises(PlatformAuditUnavailable):
        _make(service)
    assert service["store"].operators == {}
    assert service["store"].memberships == {}


@pytest.mark.parametrize("which", MUTATIONS)
def test_f3_a_failed_commit_writes_a_compensating_error_row(service, which):
    """Esiste una riga 'success' che descrive qualcosa che non e' andato in
    porto: la si corregge con una riga nuova, mai riscrivendo quella di prima."""
    if which != "create":
        _make(service, email="base@test.local", role="agency_admin")
    service["conn"] = FakeConn(commit_fails=True)
    service["audit"].clear()

    with pytest.raises(RuntimeError, match="commit"):
        if which == "create":
            _make(service)
        elif which == "update_operator":
            operators_service.update_operator(_ctx(), 10, {"first_name": "Anna"})
        elif which == "update_membership":
            operators_service.update_membership(
                _ctx(), AGENCY_A, 10, {"status": "suspended"}
            )
        else:
            operators_service.transfer_owner(_ctx(), AGENCY_A, 10)

    results = [entry["result"] for entry in service["operations"]()]
    assert results == [RESULT_SUCCESS, RESULT_ERROR], results
    assert service["operations"]()[-1]["metadata"] == {"commit_failed": True}


AUDIT_EXPECTATIONS = {
    "create": (ACTION_OPERATOR_CREATE, AGENCY_A),
    "update_operator": (ACTION_OPERATOR_UPDATE, None),
    "update_membership": (ACTION_MEMBERSHIP_UPDATE, AGENCY_A),
    "transfer": (ACTION_OWNER_TRANSFER, AGENCY_A),
}


@pytest.mark.parametrize("which", MUTATIONS)
def test_f4_every_mutation_records_the_right_action_and_target(service, which):
    _run_mutation(service, which)
    entry = service["operations"]()[-1]
    action, agency = AUDIT_EXPECTATIONS[which]
    assert entry["action"] == action
    assert entry["target_type"] == TARGET_TYPE_OPERATOR
    assert entry["target_agency_id"] == agency
    assert entry["actor"].user_id == PLATFORM_USER


def test_f4_the_operator_update_carries_no_agency(service):
    """L'identita' di una persona non appartiene a un'agenzia: e' globale, e
    attribuirne la modifica a quella in cui lavora oggi renderebbe il registro
    sbagliato appena si sposta."""
    created = _make(service)
    operators_service.update_operator(
        _ctx(), created["operator"]["id"], {"status": "disabled"}
    )
    assert service["operations"]()[-1]["target_agency_id"] is None


def test_f5_creation_produces_one_audit_row_not_two(service):
    """Creare l'utente e legarlo all'agenzia e' UNA operazione di business.

    Due righe direbbero due fatti veri e nasconderebbero che sono successi
    insieme o per niente.
    """
    _make(service)
    assert len(service["operations"]()) == 1, service["operations"]()


def test_f5_a_new_email_is_recorded_as_an_operator_creation(service):
    """L'AZIONE e il TARGET dicono cosa e' realmente accaduto.

    Qui l'oggetto nuovo e' la persona, quindi il target e' la persona - anche
    se nella stessa transazione nasce pure una membership. E' l'operazione di
    business a decidere il target, e l'operazione e' "apri un operatore in
    questa agenzia".
    """
    created = _make(service)
    entry = service["operations"]()[0]
    assert entry["action"] == ACTION_OPERATOR_CREATE
    assert entry["target_type"] == TARGET_TYPE_OPERATOR
    assert entry["target_id"] == created["operator"]["id"]
    assert entry["target_agency_id"] == AGENCY_A


def test_f5_an_existing_email_is_recorded_as_a_membership_creation(service):
    """Era: la stessa azione con `operator_created=false` nei metadata.

    In un registro append-only l'azione deve descrivere cio' che e' accaduto,
    non richiedere la lettura di un metadato per correggerne il significato:
    meta' delle righe avrebbero affermato una creazione di operatore che non
    c'e' stata, nel campo che si legge per primo, e non ci sarebbe stato un
    secondo momento per rimediarlo.
    """
    first = _make(service)
    operators_service.update_membership(
        _ctx(), AGENCY_A, first["operator"]["id"], {"status": "revoked"}
    )
    service["audit"].clear()
    second = _attach(service)

    entry = service["operations"]()[0]
    assert entry["action"] == ACTION_MEMBERSHIP_CREATE
    # Il TARGET e' la membership appena creata, non l'operatore: quello
    # esisteva gia' e nessuno lo ha creato. Una riga che dicesse
    # `membership.create` puntando all'operatore sarebbe incoerente con se
    # stessa, e in un registro append-only resterebbe cosi' per sempre.
    assert entry["target_type"] == TARGET_TYPE_MEMBERSHIP
    assert entry["target_id"] == second["membership"]["id"]
    assert entry["target_id"] != first["operator"]["id"]
    assert entry["target_agency_id"] == AGENCY_B


def test_f5_no_audit_row_ever_carries_the_operator_created_metadata(service):
    """Il metadato che correggeva l'azione non deve tornare."""
    first = _make(service)
    operators_service.update_membership(
        _ctx(), AGENCY_A, first["operator"]["id"], {"status": "revoked"}
    )
    _attach(service)
    for entry in service["operations"]():
        assert "operator_created" not in entry["metadata"], entry


def test_f6_no_audit_metadata_carries_personal_data_or_credentials(service):
    """Il registro dice CHE COSA e' stato toccato, mai con quale contenuto.

    Un'email, un nome o una password dentro una tabella append-only ci restano
    per sempre, e nessuno li ha guardati prima di scriverli.
    """
    operators_service.create_agency_operator(
        _ctx(), AGENCY_A, email="Mario.Rossi@test.local", password="segretissima",
        first_name="Mario", last_name="Rossi", operator_status="active",
        role="agency_owner", created_fields=["email", "role", "first_name"],
    )
    operators_service.update_operator(_ctx(), 10, {"first_name": "Mariolino"})

    blob = repr(service["audit"])
    for leak in ("Mario", "Rossi", "segretissima", "test.local", "pbkdf2"):
        assert leak not in blob, (leak, blob)


def test_f6_the_metadata_keys_are_a_closed_set(service):
    _make(service)
    keys = set()
    for entry in service["operations"]():
        keys |= set(entry["metadata"])
    assert keys <= {
        "created_fields", "changed_fields",
        "demoted_previous_owner", "commit_failed",
    }, keys


@pytest.mark.parametrize("role", ["agent", "agency_admin", "agency_owner"])
def test_f6_the_metadata_never_carries_the_value_of_the_role(service, role):
    """Il VALORE del ruolo era nei metadata ed e' stato tolto.

    `agent` o `agency_owner` sono contenuto della richiesta, e una tabella
    append-only non e' il posto in cui farlo entrare per comodita' di lettura.
    Che il ruolo sia stato indicato resta visibile: il suo NOME compare fra i
    `created_fields`.
    """
    _make(service, role=role, created_fields=["email", "role"])
    entry = service["operations"]()[0]
    assert "role" not in entry["metadata"], entry["metadata"]
    assert role not in repr(entry["metadata"]), entry["metadata"]
    assert entry["metadata"] == {"created_fields": ["email", "role"]}


def test_f7_a_refused_operation_writes_no_audit_row(service):
    """Un conflitto o un 404 non sono atti amministrativi: non e' cambiato
    nulla. L'ammissione di P27-1 ha gia' registrato il tentativo."""
    _make(service)
    service["audit"].clear()
    with pytest.raises(PlatformConflict):
        _make(service)
    with pytest.raises(OperatorNotFound):
        operators_service.get_operator(999999)
    assert service["operations"]() == []


def test_f8_reads_write_no_operational_audit_row(service):
    _make(service)
    service["audit"].clear()
    operators_service.list_agency_operators(AGENCY_A)
    operators_service.get_operator(10)
    assert service["operations"]() == []


# ===========================================================================
# G - HTTP E SICUREZZA DELLA SUPERFICIE
# ===========================================================================

@pytest.fixture
def client(service, monkeypatch):
    from operator_auth import dependencies as operator_deps

    def _resolve(_token):
        if service["session"] is None:
            return None
        return {
            "context": service["session"],
            "agency_name": "Agenzia di prova",
            "expires_at": EXPIRES,
        }

    monkeypatch.setattr(operator_deps.service, "session_from_token", _resolve)

    app = FastAPI()
    app.include_router(
        platform_router,
        dependencies=[Depends(platform_deps.require_platform_admin)],
    )
    http = TestClient(app, raise_server_exceptions=False)
    http.cookies.set("stima360_operator_session", "un-token")
    return http


OPERATORS_A = f"{ROUTER_PREFIX}/agencies/{AGENCY_A}/operators"
OWNER_A = f"{ROUTER_PREFIX}/agencies/{AGENCY_A}/owner"


def _http_create(client, **overrides):
    body = {
        "email": "uno@test.local", "password": "una-password", "role": "agent",
    }
    body.update(overrides)
    return client.post(OPERATORS_A, json=body)


def test_g1_create_returns_201_with_identity_and_membership_separated(client):
    response = _http_create(client)
    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == {"operator", "membership"}
    assert set(body["operator"]) == {
        "id", "email", "first_name", "last_name", "status",
        "is_platform_admin", "last_login_at", "created_at", "updated_at",
    }
    assert set(body["membership"]) == {
        "id", "agency_id", "operator_user_id", "role", "status",
        "created_at", "updated_at",
    }


def test_g1_no_response_ever_carries_the_password_or_its_hash(client):
    created = _http_create(client, password="segretissima").json()
    listed = client.get(OPERATORS_A).text
    detail = client.get(
        f"{ROUTER_PREFIX}/operators/{created['operator']['id']}"
    ).text
    for blob in (str(created), listed, detail):
        for leak in ("password", "pbkdf2", "segretissima", "token", "hash"):
            assert leak not in blob.lower(), (leak, blob)


def test_g2_list_returns_the_agency_roster_with_correct_identities(client):
    _http_create(client, email="uno@test.local")
    _http_create(client, email="due@test.local", role="agency_admin")
    response = client.get(OPERATORS_A)
    assert response.status_code == 200, response.text
    rows = response.json()
    assert [r["operator"]["email"] for r in rows] == ["uno@test.local", "due@test.local"]
    # L'identita' non deve prendere l'id della propria membership: sono due
    # colonne che si chiamano entrambe `id`.
    for row in rows:
        assert row["operator"]["id"] == row["membership"]["operator_user_id"]
        assert row["operator"]["id"] != row["membership"]["id"]


def test_g2_get_operator_returns_every_membership(client):
    created = _http_create(client).json()
    operator_id = created["operator"]["id"]
    client.patch(
        f"{OPERATORS_A}/{operator_id}/membership", json={"status": "revoked"}
    )
    # Identita' gia' esistente: nessuna password, altrimenti 409.
    client.post(
        f"{ROUTER_PREFIX}/agencies/{AGENCY_B}/operators",
        json={"email": "uno@test.local", "role": "agent"},
    )
    body = client.get(f"{ROUTER_PREFIX}/operators/{operator_id}").json()
    assert {m["agency_id"] for m in body["memberships"]} == {AGENCY_A, AGENCY_B}
    assert {m["status"] for m in body["memberships"]} == {"revoked", "active"}


@pytest.mark.parametrize("path", [
    f"{ROUTER_PREFIX}/agencies/999999/operators",
    f"{ROUTER_PREFIX}/operators/999999",
])
def test_g3_absent_resources_are_404(client, path):
    assert client.get(path).status_code == 404


def test_g3_a_membership_patch_on_a_stranger_is_404(client):
    _http_create(client)
    assert client.patch(
        f"{ROUTER_PREFIX}/agencies/{AGENCY_B}/operators/10/membership",
        json={"status": "suspended"},
    ).status_code == 404


def test_g4_a_duplicate_membership_is_409(client):
    _http_create(client)
    assert _http_create(client).status_code == 409


def test_g4_an_active_membership_elsewhere_is_409(client):
    _http_create(client)
    response = client.post(
        f"{ROUTER_PREFIX}/agencies/{AGENCY_B}/operators",
        json={"email": "uno@test.local", "role": "agent"},
    )
    assert response.status_code == 409, response.text


def test_g4_a_second_owner_is_409(client):
    _http_create(client, email="a@test.local", role="agency_owner")
    response = _http_create(client, email="b@test.local", role="agency_owner")
    assert response.status_code == 409, response.text


def test_g4_no_409_body_names_a_database_constraint(client):
    _http_create(client)
    body = _http_create(client).text
    for leak in ("uq_", "_unq", "duplicate key", "constraint", "psycopg", "SELECT"):
        assert leak not in body, (leak, body)


@pytest.mark.parametrize("body,perche", [
    ({"email": "a@b.test", "password": "x", "role": "superuser"}, "ruolo inventato"),
    ({"email": "a@b.test", "password": "x", "role": "platform_admin"}, "ruolo di piattaforma"),
    ({"email": "a@b.test", "password": "x"}, "ruolo mancante"),
    ({"email": "a@b.test", "role": "agent"}, "password mancante"),
    ({"password": "x", "role": "agent"}, "email mancante"),
    ({"email": "senza-chiocciola", "password": "x", "role": "agent"}, "email malformata"),
    ({"email": "   ", "password": "x", "role": "agent"}, "email di soli spazi"),
    ({"email": "a@b.test", "password": "x", "role": "agent", "status": "sospeso"},
     "stato operatore inventato"),
    ({"email": "a@b.test", "password": "x", "role": "agent", "is_platform_admin": True},
     "flag di piattaforma di contrabbando"),
    ({"email": "a@b.test", "password": "x", "role": "agent", "id": 9},
     "id di contrabbando"),
    ({"email": "a@b.test", "password": "x", "role": "agent", "password_hash": "x"},
     "hash di contrabbando"),
], ids=lambda v: v if isinstance(v, str) else "")
def test_g5_a_malformed_create_is_422(client, body, perche):
    assert client.post(OPERATORS_A, json=body).status_code == 422, perche


@pytest.mark.parametrize("body,perche", [
    ({}, "PATCH vuota"),
    ({"status": "sospeso"}, "stato inventato"),
    ({"status": None}, "stato null"),
    ({"email": "nuova@test.local"}, "email: e' la chiave di identita' globale"),
    ({"is_platform_admin": True}, "escalation nascosta in una modifica anagrafica"),
    ({"password": "nuova"}, "la password non si cambia da qui"),
    ({"id": 9}, "id di contrabbando"),
], ids=lambda v: v if isinstance(v, str) else "")
def test_g5_a_malformed_operator_patch_is_422(client, body, perche):
    created = _http_create(client).json()
    response = client.patch(
        f"{ROUTER_PREFIX}/operators/{created['operator']['id']}", json=body
    )
    assert response.status_code == 422, (perche, response.text)


@pytest.mark.parametrize("body,perche", [
    ({}, "PATCH vuota"),
    ({"role": "superuser"}, "ruolo inventato"),
    ({"status": "cancellata"}, "stato inventato"),
    ({"role": None}, "ruolo null"),
    ({"agency_id": 2}, "riassegnazione di agenzia di contrabbando"),
    ({"operator_user_id": 3}, "riassegnazione di persona di contrabbando"),
], ids=lambda v: v if isinstance(v, str) else "")
def test_g5_a_malformed_membership_patch_is_422(client, body, perche):
    created = _http_create(client).json()
    response = client.patch(
        f"{OPERATORS_A}/{created['operator']['id']}/membership", json=body
    )
    assert response.status_code == 422, (perche, response.text)


def test_g6_the_owner_role_through_the_membership_patch_is_409_not_422(client):
    """Il ruolo esiste e la richiesta e' ben formata: cio' che non va e' lo
    stato del sistema."""
    created = _http_create(client).json()
    response = client.patch(
        f"{OPERATORS_A}/{created['operator']['id']}/membership",
        json={"role": "agency_owner"},
    )
    assert response.status_code == 409, response.text


def test_g7_the_owner_transfer_works_over_http(client):
    a = _http_create(client, email="a@test.local", role="agency_owner").json()
    b = _http_create(client, email="b@test.local", role="agency_admin").json()
    response = client.put(
        OWNER_A, json={"operator_user_id": b["operator"]["id"]}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["membership"]["role"] == "agency_owner"
    assert body["membership"]["operator_user_id"] == b["operator"]["id"]
    assert body["demoted"]["operator_user_id"] == a["operator"]["id"]
    assert body["demoted"]["role"] == "agency_admin"


def test_g7_the_transfer_is_idempotent(client):
    a = _http_create(client, email="a@test.local", role="agency_owner").json()
    first = client.put(OWNER_A, json={"operator_user_id": a["operator"]["id"]})
    assert first.status_code == 200, first.text
    assert first.json()["demoted"] is None


def test_g8_there_is_no_delete_anywhere_on_the_platform_surface():
    """`revoked` e `disabled` sono stati. Cancellare toglierebbe la differenza
    fra "non c'e' mai stato" e "non c'e' piu'"."""
    import main

    spec = main.app.openapi()
    for path, operations in spec["paths"].items():
        if path.startswith(ROUTER_PREFIX):
            assert "delete" not in operations, (path, sorted(operations))


def test_g8_a_delete_request_is_405(client):
    created = _http_create(client).json()
    assert client.delete(
        f"{ROUTER_PREFIX}/operators/{created['operator']['id']}"
    ).status_code == 405


def test_g8_the_real_application_exposes_the_p27_3_routes():
    """Era: `found == {...}`, l'elenco ESAUSTIVO della superficie.

    P27-4 ha aggiunto le due route della configurazione, e un perno sulla
    dimensione totale dentro il file di P27-3 si romperebbe a ogni fase che la
    allarga. Le route di P27-3 restano asserite qui; l'elenco completo
    appartiene alla fase piu' recente:
    tests/test_p27_4_configuration.py::test_g7_the_real_application_exposes_exactly_the_platform_surface.
    """
    import main

    spec = main.app.openapi()
    found = {
        (method.upper(), path)
        for path, operations in spec["paths"].items()
        if path.startswith(ROUTER_PREFIX)
        for method in operations
    }
    assert found >= {
        ("GET", f"{ROUTER_PREFIX}/me"),
        ("GET", f"{ROUTER_PREFIX}/agencies"),
        ("POST", f"{ROUTER_PREFIX}/agencies"),
        ("GET", f"{ROUTER_PREFIX}/agencies/{{agency_id}}"),
        ("PATCH", f"{ROUTER_PREFIX}/agencies/{{agency_id}}"),
        ("GET", f"{ROUTER_PREFIX}/agencies/{{agency_id}}/operators"),
        ("POST", f"{ROUTER_PREFIX}/agencies/{{agency_id}}/operators"),
        ("GET", f"{ROUTER_PREFIX}/operators/{{operator_user_id}}"),
        ("PATCH", f"{ROUTER_PREFIX}/operators/{{operator_user_id}}"),
        ("PATCH", f"{ROUTER_PREFIX}/agencies/{{agency_id}}/operators"
                  "/{operator_user_id}/membership"),
        ("PUT", f"{ROUTER_PREFIX}/agencies/{{agency_id}}/owner"),
    }, sorted(found)
    # E niente DELETE, la meta' del perno che non dipende da quante route
    # esistano.
    assert not [pair for pair in found if pair[0] == "DELETE"], sorted(found)


ALL_P27_3_CALLS = [
    ("get", OPERATORS_A, None),
    ("get", f"{ROUTER_PREFIX}/operators/10", None),
    ("post", OPERATORS_A, {"email": "a@b.test", "password": "x", "role": "agent"}),
    ("patch", f"{ROUTER_PREFIX}/operators/10", {"first_name": "X"}),
    ("patch", f"{OPERATORS_A}/10/membership", {"status": "suspended"}),
    ("put", OWNER_A, {"operator_user_id": 10}),
]


@pytest.mark.parametrize("method,path,body", ALL_P27_3_CALLS)
def test_g9_a_tenant_operator_is_403_on_every_route(client, service, method, path, body):
    service["session"] = _ctx(
        agency_id=AGENCY_A, role="agency_owner", is_platform_admin=False
    )
    response = getattr(client, method)(path, **({"json": body} if body else {}))
    assert response.status_code == 403, response.text


@pytest.mark.parametrize("method,path,body", ALL_P27_3_CALLS)
def test_g9_an_anonymous_caller_is_401_on_every_route(client, method, path, body):
    client.cookies.clear()
    response = getattr(client, method)(path, **({"json": body} if body else {}))
    assert response.status_code == 401, response.text


def test_g9_a_refused_caller_changes_nothing(client, service):
    service["session"] = _ctx(
        agency_id=AGENCY_A, role="agency_owner", is_platform_admin=False
    )
    assert _http_create(client).status_code == 403
    service["session"] = _ctx()
    assert client.get(OPERATORS_A).json() == []


@pytest.mark.parametrize("agency_status", ["suspended", "archived"])
def test_g10_operators_can_be_administered_in_a_suspended_or_archived_agency(
    client, service, agency_status
):
    """DECISIONE ESPLICITA DI P27-3.

    E' il momento in cui serve di piu': si sospende un affiliato proprio
    quando qualcosa non va, e dover prima riattivarlo per sistemarne
    l'organico sarebbe un giro assurdo che nel frattempo riapre il tenant.

    Non allenta nulla sul lato tenant: `operator_auth` continua a richiedere
    `agency_status='active'` perche' una sessione sia utilizzabile, e il test
    E3 lo verifica sulla funzione originale.
    """
    service["store"].agencies[AGENCY_A]["status"] = agency_status

    created = _http_create(client)
    assert created.status_code == 201, created.text
    operator_id = created.json()["operator"]["id"]

    assert client.get(OPERATORS_A).status_code == 200
    assert client.patch(
        f"{OPERATORS_A}/{operator_id}/membership", json={"status": "suspended"}
    ).status_code == 200
    assert client.patch(
        f"{ROUTER_PREFIX}/operators/{operator_id}", json={"status": "disabled"}
    ).status_code == 200


def test_g11_an_unwritable_audit_answers_503(client, service):
    service["audit_fails"] = True
    assert _http_create(client).status_code == 503


def test_g11_nothing_is_created_when_the_audit_fails(client, service):
    service["audit_fails"] = True
    assert _http_create(client).status_code == 503
    service["audit_fails"] = False
    assert client.get(OPERATORS_A).json() == []


# ===========================================================================
# H - IL PERIMETRO
# ===========================================================================

def test_h1_the_roles_match_the_check_constraint_in_migration_027():
    import re

    sql = MIGRATION_027.read_text(encoding="utf-8")
    match = re.search(r"role IN \(([^)]*)\)", sql)
    assert match, "il CHECK su agency_memberships.role non e' stato trovato"
    declared = tuple(v.strip().strip("'") for v in match.group(1).split(","))
    assert set(declared) == set(MEMBERSHIP_ROLES), (declared, MEMBERSHIP_ROLES)
    assert "platform_admin" not in declared, "e' un flag, non un ruolo di agenzia"


def test_h1_the_statuses_match_their_check_constraints():
    import re

    sql = MIGRATION_027.read_text(encoding="utf-8")
    found = re.findall(r"status IN \(([^)]*)\)", sql)
    parsed = [tuple(v.strip().strip("'") for v in group.split(",")) for group in found]
    assert set(MEMBERSHIP_STATUSES) in [set(p) for p in parsed], parsed
    assert set(OPERATOR_STATUSES) in [set(p) for p in parsed], parsed


def test_h2_the_two_partial_unique_indexes_are_untouched():
    """`uq_agency_memberships_single_active` e `uq_agency_memberships_single_owner`
    sono la specifica, non un dettaglio: P27-3 ci si appoggia e non li modifica.

    Nessuna migration di P27-3, quindi nessun file puo' averli alterati - ma
    l'assenza va provata, non assunta.
    """
    sql = MIGRATION_027.read_text(encoding="utf-8")
    assert "uq_agency_memberships_single_active" in sql
    assert "uq_agency_memberships_single_owner" in sql

    # P27-5: SUL SQL ESEGUIBILE, NON SUL TESTO DEL FILE.
    #
    # La 058 di P27-5 NOMINA `uq_agency_memberships_single_active` in un
    # commento, per dire che il suo indice parziale segue quel precedente. Un
    # confronto sul testo grezzo lo trovava e falliva - provando che un file
    # PARLA di quell'indice, non che lo tocca. E' lo stesso errore che P27-1,
    # P27-2 e P27-4 hanno gia' fatto ciascuna una volta, in senso inverso.
    from scripts import p26_migrate

    for path in sorted((ROOT / "migrations").glob("*.sql")):
        if path.name.startswith("027_"):
            continue
        text = p26_migrate.strip_sql_comments(
            path.read_text(encoding="utf-8")
        ).lower()
        for index in ("uq_agency_memberships_single_active",
                      "uq_agency_memberships_single_owner"):
            assert index not in text, (path.name, index)


def test_h3_p27_3_added_no_migration():
    """Lo schema 027 basta: le due tabelle, i loro CHECK e i quattro vincoli
    unici esistono gia'. Nessuna colonna "per comodita'"."""
    # P27-5: IL PERNO SI E' SPOSTATO DAL SOFFITTO AL NOME.
    #
    # Questo test asseriva che la migration piu' alta fosse la 057. Era vero
    # finche' nessuna fase successiva ne aggiungeva una, e ha smesso di esserlo
    # con la 058 di P27-5, che una migration ce l'ha e deve averla. Un perno sul
    # SOFFITTO dentro il file di una fase vecchia fallisce a ogni fase che
    # aggiunge legittimamente uno schema: rumore, non sorveglianza.
    #
    # Cio' che P27-3 deve davvero garantire e' che non ne abbia aggiunta una
    # SUA, ed e' quello che si asserisce adesso - una proprieta' che resta vera
    # per sempre, qualunque cosa facciano le fasi dopo.
    nomi = [
        path.name for path in (ROOT / "migrations").glob("*.sql")
        if "p27_3" in path.name
    ]
    assert nomi == [], nomi


def test_h4_operator_auth_was_not_modified_by_p27_3():
    """P27-3 riusa `security.hash_password` e si appoggia a `_scope_is_usable`.
    Riscriverne una qualunque significherebbe averne due che possono divergere.
    """
    service_source = (ROOT / "operator_auth" / "service.py").read_text()
    assert 'row.get("membership_status") == "active"' in service_source
    assert 'row.get("agency_status") == "active"' in service_source

    security_source = (ROOT / "operator_auth" / "security.py").read_text()
    assert "def hash_password" in security_source
    assert "pbkdf2_sha256" in security_source

    # E P27-3 non si e' scritto un proprio hashing.
    for name in ("operators_service.py", "operators_repository.py"):
        source = (ROOT / "platform_admin" / name).read_text()
        assert "pbkdf2" not in source.lower(), name
        assert "hashlib" not in source, name


def test_h5_the_audit_repository_is_still_only_the_audit_writer():
    tree = ast.parse((ROOT / "platform_admin" / "repository.py").read_text())
    functions = [
        node.name for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    assert functions == ["insert_audit_entry"], functions


def test_h6_the_order_of_audit_and_commit_still_has_one_implementation():
    """P27-3 ha spostato la sequenza in `transaction.py` invece di copiarla:
    sei operazioni mutative fra P27-2 e P27-3, una sola copia dell'ordine."""
    package = ROOT / "platform_admin"
    commits = {
        path.name: path.read_text(encoding="utf-8").count("conn.commit()")
        for path in sorted(package.glob("*.py"))
        if path.name != "database.py"
    }
    assert sum(commits.values()) == 1, commits
    assert commits["transaction.py"] == 1, commits

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
    # Le quattro mutazioni di P27-3 passano tutte di li'. L'elenco COMPLETO
    # dei chiamanti - che e' il perno che impedisce a una mutazione nuova di
    # aggirare l'ordine - sta nella fase piu' recente:
    # tests/test_p27_4_configuration.py::test_g8_every_mutation_in_the_package_goes_through_the_shared_order.
    assert set(callers) >= {
        "create_agency", "update_agency",
        "create_agency_operator", "update_operator", "update_membership",
        "transfer_owner",
    }, sorted(callers)


def test_h7_no_p27_3_module_reaches_a_tenant_query_builder():
    """La superficie Platform amministra la RETE. Nessuna scorciatoia verso i
    dati di un'agenzia: la decisione D1 di P27-1 vale ancora."""
    forbidden = {"core.scope", "core.repository", "core.service"}
    for name in ("operators_repository.py", "operators_service.py",
                 "transaction.py"):
        tree = ast.parse((ROOT / "platform_admin" / name).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert node.module not in forbidden, (name, node.module)
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name not in forbidden, (name, alias.name)


def test_h8_the_router_writes_no_audit_row_and_holds_no_domain_rule():
    """Il router traduce, non decide."""
    tree = ast.parse((ROOT / "platform_admin" / "router.py").read_text())
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert "audit" not in names
    assert "audit_then_commit" not in names
    source = (ROOT / "platform_admin" / "router.py").read_text()
    assert "hash_password" not in source
    assert "normalize_email" not in source


def test_h9_the_service_is_keyword_only_where_it_matters():
    """`create_agency_operator` riceve email, password, nome, cognome, stato e
    ruolo: sei valori di cui cinque stringhe. In posizionale uno scambio
    produrrebbe un conto plausibile e sbagliato."""
    signature = inspect.signature(operators_service.create_agency_operator)
    positional = [
        name for name, p in signature.parameters.items()
        if p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    ]
    assert positional == ["actor", "agency_id"], positional


# ===========================================================================
# I - LE GUARDIE APPLICATIVE RIFIUTANO *PRIMA* DI SCRIVERE
#
# Questo gruppo esiste per una lacuna trovata con una mutazione: togliendo il
# controllo applicativo su `uq_agency_memberships_single_active`, l'intera
# suite restava verde - perche' il vincolo di database rifiutava comunque, e
# nessun test distingueva "rifiutato dalla guardia" da "rifiutato dall'indice".
#
# I due esiti non sono equivalenti. Il vincolo e' l'autorita' finale e chiude
# le corse; la guardia decide PRIMA che una riga venga tentata, e senza di lei
# ogni conflitto normale diventa una INSERT fallita che aborta la transazione.
# Su una creazione in due righe - operatore piu' membership - significa perdere
# anche la prima.
#
# Si asserisce quindi il punto in cui il rifiuto avviene, non solo che avvenga.
# ===========================================================================

def test_i1_an_active_membership_elsewhere_is_refused_before_any_write(service):
    _make(service)
    service["order"].clear()
    with pytest.raises(PlatformConflict):
        _attach(service)
    assert "attempt-membership" not in service["order"], service["order"]
    assert "attempt-operator" not in service["order"], service["order"]


def test_i2_an_existing_membership_is_refused_before_any_write(service):
    _make(service)
    service["order"].clear()
    with pytest.raises(PlatformConflict):
        _make(service)
    assert "attempt-membership" not in service["order"], service["order"]


def test_i3_a_refused_reactivation_never_reaches_the_update(service):
    first = _make(service)
    operator_id = first["operator"]["id"]
    operators_service.update_membership(
        _ctx(), AGENCY_A, operator_id, {"status": "revoked"}
    )
    _attach(service)
    service["order"].clear()
    with pytest.raises(PlatformConflict):
        operators_service.update_membership(
            _ctx(), AGENCY_A, operator_id, {"status": "active"}
        )
    assert "attempt-membership" not in service["order"], service["order"]


def test_i4_the_owner_role_is_refused_before_any_write(service):
    created = _make(service)
    service["order"].clear()
    with pytest.raises(PlatformConflict):
        operators_service.update_membership(
            _ctx(), AGENCY_A, created["operator"]["id"], {"role": "agency_owner"}
        )
    assert "attempt-membership" not in service["order"], service["order"]


def test_i5_a_second_owner_at_creation_is_refused_before_the_operator_is_written(
    service,
):
    """La creazione scrive DUE righe. Se il conflitto sul titolare arrivasse
    solo dalla seconda, la prima sarebbe gia' stata tentata - e in un database
    vero avrebbe abortito la transazione portandosi via anche quella."""
    _make(service, email="a@test.local", role="agency_owner")
    service["order"].clear()
    with pytest.raises(PlatformConflict):
        _make(service, email="b@test.local", role="agency_owner")
    # Qui il rifiuto arriva dal vincolo, non da una guardia: e' l'unico dei
    # quattro conflitti che il service non anticipa, perche' anticiparlo
    # richiederebbe una lettura in piu' su ogni creazione per un caso raro.
    # Cio' che conta e' che l'operatore non sopravviva al rifiuto.
    assert service["store"].operators.get(11) is None
    assert len([o for o in service["store"].operators.values()
                if o["email_normalized"] == "b@test.local"]) == 0


def test_i6_the_reactivation_guard_is_skipped_when_nothing_is_being_reactivated(
    service,
):
    """Controllo negativo delle guardie: una PATCH sul ruolo di una membership
    gia' attiva non sta riattivando niente, e farla passare dai controlli le
    farebbe trovare se stessa e rifiutarsi."""
    created = _make(service)
    updated = operators_service.update_membership(
        _ctx(), AGENCY_A, created["operator"]["id"], {"role": "agency_admin"}
    )
    assert updated["role"] == "agency_admin"

    # E anche una PATCH che riscrive `active` su una riga gia' attiva.
    again = operators_service.update_membership(
        _ctx(), AGENCY_A, created["operator"]["id"], {"status": "active"}
    )
    assert again["status"] == "active"


# ===========================================================================
# J - IL CONTRATTO DELLA CREDENZIALE
#
#     email nuova     -> la password SERVE          senza -> 422
#     email gia' nota -> la password NON va inviata inviata -> 409
#
# I due rifiuti sono deterministici, e nessuno dei due percorsi tocca mai la
# credenziale di una persona che esiste gia': questa route non e' un
# reimposta-password implicito, e non lo diventa ignorando in silenzio un
# campo che il client ha mandato.
# ===========================================================================

from platform_admin.exceptions import PasswordRequired  # noqa: E402


def test_j1_a_new_identity_without_a_password_is_refused(service):
    """Deterministico, e non un conto senza credenziale.

    `password_hash` e' NOT NULL con un CHECK sul formato: una persona nuova
    non puo' esistere senza. Il rifiuto arriva prima di qualunque scrittura.
    """
    service["order"].clear()
    with pytest.raises(PasswordRequired):
        _make(service, password=None)
    assert service["store"].operators == {}
    assert "attempt-operator" not in service["order"], service["order"]


def test_j2_an_existing_identity_with_a_password_is_refused(service):
    """409, e NON un aggiornamento silenzioso della credenziale."""
    first = _make(service)
    operators_service.update_membership(
        _ctx(), AGENCY_A, first["operator"]["id"], {"status": "revoked"}
    )
    with pytest.raises(PlatformConflict):
        _make(service, agency_id=AGENCY_B, password="una-nuova-password")


def test_j2_the_refusal_happens_before_any_write(service):
    first = _make(service)
    operators_service.update_membership(
        _ctx(), AGENCY_A, first["operator"]["id"], {"status": "revoked"}
    )
    service["order"].clear()
    with pytest.raises(PlatformConflict):
        _make(service, agency_id=AGENCY_B, password="una-nuova-password")
    assert "attempt-membership" not in service["order"], service["order"]
    assert "attempt-operator" not in service["order"], service["order"]


def test_j3_an_existing_identity_never_has_its_password_hash_touched(service):
    """La prova diretta: l'hash prima e dopo e' lo stesso byte per byte."""
    first = _make(service, password="quella-originale")
    operator_id = first["operator"]["id"]
    before = service["store"].operators[operator_id]["password_hash"]

    operators_service.update_membership(
        _ctx(), AGENCY_A, operator_id, {"status": "revoked"}
    )
    _attach(service)

    after = service["store"].operators[operator_id]["password_hash"]
    assert after == before

    from operator_auth.security import verify_password

    assert verify_password("quella-originale", after) is True


def test_j3_a_refused_password_is_never_applied_even_partially(service):
    """Controllo negativo di J2: dopo il 409 la credenziale e' quella di prima."""
    from operator_auth.security import verify_password

    first = _make(service, password="quella-originale")
    operator_id = first["operator"]["id"]
    operators_service.update_membership(
        _ctx(), AGENCY_A, operator_id, {"status": "revoked"}
    )
    with pytest.raises(PlatformConflict):
        _make(service, agency_id=AGENCY_B, password="quella-nuova")

    stored = service["store"].operators[operator_id]["password_hash"]
    assert verify_password("quella-originale", stored) is True
    assert verify_password("quella-nuova", stored) is False


def test_j4_an_existing_identity_without_a_password_gets_its_membership(service):
    """Il percorso normale: nessuna password, nessun rifiuto, solo la
    membership."""
    first = _make(service)
    operators_service.update_membership(
        _ctx(), AGENCY_A, first["operator"]["id"], {"status": "revoked"}
    )
    second = _attach(service)
    assert second["operator"]["id"] == first["operator"]["id"]
    assert second["membership"]["agency_id"] == AGENCY_B
    assert second["membership"]["status"] == "active"


def test_j5_an_explicit_null_password_is_the_same_as_omitting_it(service):
    """`{"password": null}` e' un modo di non mandarla: la regola e' una sola e
    non dipende da quale delle due forme il client scelga."""
    from platform_admin.schemas import OperatorCreateRequest

    omessa = OperatorCreateRequest(email="a@b.test", role="agent")
    nulla = OperatorCreateRequest(email="a@b.test", role="agent", password=None)
    assert omessa.supplies_password() is False
    assert nulla.supplies_password() is False
    assert OperatorCreateRequest(
        email="a@b.test", role="agent", password="x"
    ).supplies_password() is True


def test_j6_p27_3_introduces_no_local_password_policy():
    """NESSUNA regola di forma, e non per dimenticanza.

    `operator_auth` non ne ha - `LoginRequest.password` e' un `str` senza
    vincoli, `hash_password` accetta qualunque stringa, e il solo CHECK della
    027 riguarda il formato dell'HASH. Imporne una qui sarebbe stata la
    politica password dell'intero prodotto, decisa da dentro questa fase e
    applicata alla sola superficie Platform.

    Il gap e' dichiarato come rischio residuo, non chiuso di nascosto.
    """
    from platform_admin.schemas import OperatorCreateRequest

    field = OperatorCreateRequest.model_fields["password"]
    assert field.metadata == [], field.metadata
    assert field.is_required() is False

    enums_source = (ROOT / "platform_admin" / "enums.py").read_text()
    assert "PASSWORD_MIN_LENGTH" not in enums_source
    assert "PASSWORD_MAX_LENGTH" not in enums_source

    # E nessun controllo di forma scritto a mano nel service o nello schema.
    for name in ("operators_service.py", "schemas.py"):
        tree = ast.parse((ROOT / "platform_admin" / name).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "len" and "password" in ast.unparse(node):
                    raise AssertionError(f"{name}: controllo di lunghezza su password")


def test_j6_the_login_schema_has_no_password_rule_either():
    """La prova che il gap e' del prodotto e non di P27-3: la stessa assenza
    sta nello schema di login, che esiste dal P26-1."""
    from operator_auth.schemas import LoginRequest

    assert LoginRequest.model_fields["password"].metadata == []


def test_j7_the_hashing_is_only_ever_the_projects_own_function():
    source = (ROOT / "platform_admin" / "operators_service.py").read_text()
    assert "from operator_auth.security import hash_password" in source
    assert source.count("hash_password(") == 1, "la password si trasforma in un posto solo"
    assert "hashlib" not in source
    assert "pbkdf2" not in source.lower()


# --- gli stessi due rifiuti, su HTTP ---------------------------------------

def test_j8_http_a_new_identity_without_a_password_is_422(client):
    response = client.post(OPERATORS_A, json={"email": "uno@test.local", "role": "agent"})
    assert response.status_code == 422, response.text


def test_j8_http_an_existing_identity_with_a_password_is_409(client):
    created = _http_create(client).json()
    client.patch(
        f"{OPERATORS_A}/{created['operator']['id']}/membership",
        json={"status": "revoked"},
    )
    response = client.post(
        f"{ROUTER_PREFIX}/agencies/{AGENCY_B}/operators",
        json={"email": "uno@test.local", "password": "nuova", "role": "agent"},
    )
    assert response.status_code == 409, response.text


def test_j8_http_an_existing_identity_without_a_password_is_201(client):
    created = _http_create(client).json()
    client.patch(
        f"{OPERATORS_A}/{created['operator']['id']}/membership",
        json={"status": "revoked"},
    )
    response = client.post(
        f"{ROUTER_PREFIX}/agencies/{AGENCY_B}/operators",
        json={"email": "uno@test.local", "role": "agent"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["operator"]["id"] == created["operator"]["id"]


@pytest.mark.parametrize("body,atteso", [
    # La password di prova e' deliberatamente improbabile: un valore come
    # "nuova" e' una parola italiana che compare legittimamente nel messaggio
    # di rifiuto, e il controllo di non-divulgazione passerebbe per caso
    # sbagliato - il modo in cui un test smette di provare cio' che dice.
    ({"email": "uno@test.local", "password": "zzqq-credenziale", "role": "agent"}, 409),
    ({"email": "uno@test.local", "role": "agent"}, 422),
])
def test_j9_neither_refusal_leaks_a_credential_or_a_database_detail(
    client, service, body, atteso
):
    if atteso == 409:
        created = _http_create(client).json()
        client.patch(
            f"{OPERATORS_A}/{created['operator']['id']}/membership",
            json={"status": "revoked"},
        )
        path = f"{ROUTER_PREFIX}/agencies/{AGENCY_B}/operators"
    else:
        path = OPERATORS_A

    response = client.post(path, json=body)
    assert response.status_code == atteso, response.text
    text = response.text
    for leak in ("zzqq-credenziale", "pbkdf2", "hash", "operator_users",
                 "constraint", "duplicate key", "psycopg", "SELECT"):
        assert leak not in text, (leak, text)


def test_j10_the_two_audit_actions_over_http(client, service):
    """Fine a fine: due POST, due azioni diverse nel registro."""
    created = _http_create(client).json()
    client.patch(
        f"{OPERATORS_A}/{created['operator']['id']}/membership",
        json={"status": "revoked"},
    )
    service["audit"].clear()
    client.post(
        f"{ROUTER_PREFIX}/agencies/{AGENCY_B}/operators",
        json={"email": "uno@test.local", "role": "agent"},
    )
    assert [e["action"] for e in service["operations"]()] == [
        ACTION_MEMBERSHIP_CREATE
    ]


def test_j10_no_password_reaches_the_audit_on_either_path(client, service):
    _http_create(client, email="a@test.local", password="primaria")
    _http_create(client, email="b@test.local", password="secondaria")
    blob = repr(service["audit"])
    for leak in ("primaria", "secondaria", "password", "pbkdf2"):
        assert leak not in blob, (leak, blob)


# ===========================================================================
# K - LA RIGA DI AUDIT DEL POST E' COERENTE CON SE STESSA
#
# L'azione dice cosa e' successo e il target dice a cosa. Su una tabella
# append-only i due campi non possono contraddirsi, perche' non c'e' un
# secondo momento in cui rimediare.
# ===========================================================================

def test_k1_the_two_paths_produce_two_different_targets(service):
    """La prova affiancata: stessa route, due righe con oggetti diversi."""
    first = _make(service, email="uno@test.local")
    operators_service.update_membership(
        _ctx(), AGENCY_A, first["operator"]["id"], {"status": "revoked"}
    )
    second = _attach(service)

    # Fra le due creazioni c'e' anche la revoca, che scrive la propria riga:
    # si selezionano le due che questo test riguarda invece di assumere che
    # siano le uniche.
    creazione, aggancio = [
        entry for entry in service["operations"]()
        if entry["action"] in (ACTION_OPERATOR_CREATE, ACTION_MEMBERSHIP_CREATE)
    ]

    assert (creazione["action"], creazione["target_type"], creazione["target_id"]) == (
        ACTION_OPERATOR_CREATE, TARGET_TYPE_OPERATOR, first["operator"]["id"]
    )
    assert (aggancio["action"], aggancio["target_type"], aggancio["target_id"]) == (
        ACTION_MEMBERSHIP_CREATE, TARGET_TYPE_MEMBERSHIP, second["membership"]["id"]
    )


def test_k2_the_membership_target_is_the_row_that_was_really_created(service):
    """`target_id` deve essere l'id della membership NUOVA, non di una qualsiasi.

    La persona su quel percorso ne ha gia' un'altra - revocata, in un'altra
    agenzia - e puntare a quella renderebbe la riga sbagliata in un modo che
    nessuno noterebbe leggendo solo l'azione.
    """
    first = _make(service, email="uno@test.local")
    vecchia = first["membership"]["id"]
    operators_service.update_membership(
        _ctx(), AGENCY_A, first["operator"]["id"], {"status": "revoked"}
    )
    service["audit"].clear()
    second = _attach(service)

    entry = service["operations"]()[0]
    assert entry["target_id"] == second["membership"]["id"]
    assert entry["target_id"] != vecchia
    assert service["store"].memberships[entry["target_id"]]["agency_id"] == AGENCY_B


@pytest.mark.parametrize("agency_id", [AGENCY_A, AGENCY_B])
def test_k3_the_agency_is_recorded_on_both_paths(service, agency_id):
    """`target_agency_id` valorizzato in entrambi i casi: e' cio' che rende
    rispondibile "cosa e' stato fatto a questa agenzia"."""
    _make(service, agency_id=agency_id, email="prima@test.local")
    assert service["operations"]()[-1]["target_agency_id"] == agency_id

    altra = AGENCY_B if agency_id == AGENCY_A else AGENCY_A
    _make(service, agency_id=altra, email="seconda@test.local")
    operators_service.update_membership(
        _ctx(), altra, service["store"].next_operator_id - 1, {"status": "revoked"}
    )
    service["audit"].clear()
    _attach(service, agency_id=agency_id, email="seconda@test.local")
    assert service["operations"]()[0]["target_agency_id"] == agency_id


def test_k4_the_post_metadata_is_only_field_names(service):
    """Nessun valore, di nessun tipo: ne' ruolo, ne' email, ne' nomi."""
    operators_service.create_agency_operator(
        _ctx(), AGENCY_A, email="Mario.Rossi@test.local", password="segretissima",
        first_name="Mario", last_name="Rossi", operator_status="active",
        role="agency_owner", created_fields=["email", "role", "first_name",
                                             "last_name", "status"],
    )
    metadata = service["operations"]()[0]["metadata"]
    assert set(metadata) == {"created_fields"}
    assert metadata["created_fields"] == [
        "email", "first_name", "last_name", "role", "status"
    ]
    blob = repr(metadata)
    for leak in ("Mario", "Rossi", "segretissima", "test.local",
                 "agency_owner", "active", "pbkdf2"):
        assert leak not in blob, (leak, blob)


def test_k5_both_target_types_are_declared_constants_not_literals():
    """Il valore con cui si ritrovano queste righe fra dieci mesi sta in un
    posto solo."""
    from platform_admin.enums import TARGET_TYPE_MEMBERSHIP, TARGET_TYPE_OPERATOR

    assert TARGET_TYPE_OPERATOR == "operator"
    assert TARGET_TYPE_MEMBERSHIP == "agency_membership"

    # Il controllo e' sull'ARGOMENTO della chiamata di audit, non sulla parola:
    # "operator" compare legittimamente come CHIAVE del dizionario che il
    # service restituisce, e cercarla nel testo darebbe un rosso su una riga
    # corretta.
    tree = ast.parse(
        (ROOT / "platform_admin" / "operators_service.py").read_text()
    )
    passati = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "audit_then_commit"):
            for keyword in node.keywords:
                if keyword.arg in ("target_type", "action"):
                    passati.append(keyword.value)
    assert passati, "nessuna chiamata di audit trovata"
    for valore in passati:
        assert not isinstance(valore, ast.Constant), ast.unparse(valore)


def test_k6_the_membership_target_row_exists_when_the_audit_is_written(service):
    """Controllo negativo: il target non e' un id previsto prima della
    scrittura, e' quello della riga realmente inserita."""
    first = _make(service, email="uno@test.local")
    operators_service.update_membership(
        _ctx(), AGENCY_A, first["operator"]["id"], {"status": "revoked"}
    )
    service["audit"].clear()
    _attach(service)

    entry = service["operations"]()[0]
    riga = service["store"].memberships[entry["target_id"]]
    assert riga["operator_user_id"] == first["operator"]["id"]
    assert riga["agency_id"] == AGENCY_B
    assert riga["status"] == "active"
