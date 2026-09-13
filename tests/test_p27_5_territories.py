"""P27-5 - territori della rete.

L'audit geografico del repository ha trovato SOLO TESTO LIBERO:
`properties.city/province/postal_code/microzone` sono digitati a mano;
`stime.comune` esce da `main.normalizza_comune`, che e' una allowlist hardcoded
di tre nomi e restituisce una stringa di display; `zone_valori` indicizza
prezzi su quelle stringhe; `buy_location_criteria` (004) e' la lista dei
desideri di un acquirente. Nessun codice ISTAT, nessuna mappa comune ->
provincia.

P27-5 introduce quindi la propria chiave canonica, separa identita' e
assegnazione, e mette l'invariante di prodotto in un indice unico parziale.

P27-5 NON DECIDE A CHI VA UN LEAD. Quello e' P27-6, e il gruppo J lo sorveglia.

Mappa:

    A   il modello: identita' separata dall'assegnazione
    B   la migration 058
    C   territorio: dichiarazione, lettura, elenco
    D   assegnazione: esclusivita', sospensione, revoca, riattivazione
    E   trasferimento: esplicito e atomico
    F   stato agenzia: amministrabile sempre, nessun effetto automatico
    G   transazione e audit
    H   HTTP e sicurezza
    I   paginazione
    J   perimetro: niente P27-6, niente DELETE, niente cross-tenant
    K   l'elenco esaustivo, che appartiene alla fase piu' recente
"""
from __future__ import annotations

import ast
import re
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
from platform_admin import territories_repository, territories_service
from platform_admin import dependencies as platform_deps
from platform_admin.enums import (
    ACTION_TERRITORY_ASSIGNMENT_CREATE,
    ACTION_TERRITORY_ASSIGNMENT_UPDATE,
    ACTION_TERRITORY_CREATE,
    ACTION_TERRITORY_TRANSFER,
    ASSIGNMENT_ACTIVE,
    ASSIGNMENT_REVOKED,
    ASSIGNMENT_STATUSES,
    ASSIGNMENT_SUSPENDED,
    RESULT_ERROR,
    RESULT_SUCCESS,
    ROUTER_PREFIX,
    TARGET_TYPE_TERRITORY,
    TARGET_TYPE_TERRITORY_ASSIGNMENT,
    TERRITORY_CANONICAL_KEY_PATTERN,
    TERRITORY_KINDS,
    TERRITORY_PAGE_DEFAULT,
    TERRITORY_PAGE_MAX,
)
from platform_admin.exceptions import (
    AgencyNotFound,
    PlatformAuditUnavailable,
    PlatformConflict,
    TerritoryAssignmentNotFound,
    TerritoryNotFound,
)
from platform_admin.router import router as platform_router

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"
VERSION = "058_p27_network_territories"

PLATFORM_USER = 77
AGENCY_A = 1
AGENCY_B = 2
AGENCY_C = 3
EXPIRES = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)
CREATED = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 9, 13, 10, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def up_sql() -> str:
    return (MIGRATIONS / f"{VERSION}.sql").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def down_sql() -> str:
    return (MIGRATIONS / f"{VERSION}_down.sql").read_text(encoding="utf-8")


def _executable(sql: str) -> str:
    """Il solo SQL eseguibile, senza commenti, con lo stripper del runner.

    Lezione ripetuta da P27-1 a P27-4: cercare una parola nel file intero
    trova i commenti che la nominano per spiegare perche' NON c'e'. Ogni
    asserzione su cosa il file FA guarda solo cio' che il database esegue.
    """
    from scripts import p26_migrate

    return p26_migrate.strip_sql_comments(sql)


def _ctx(**overrides) -> OperatorContext:
    base = dict(
        user_id=PLATFORM_USER, agency_id=None, role=None,
        is_platform_admin=True, session_id=1, auth_channel="operator_session",
    )
    base.update(overrides)
    return OperatorContext(**base)


class FakeUnique(errors.UniqueViolation):
    """Una violazione di unicita' con il nome del vincolo, come psycopg2.

    Sottoclasse e non `Mock`: `_as_conflict` cattura `errors.UniqueViolation`,
    e un doppio che non superasse quell'`isinstance` farebbe passare i test su
    un percorso che in produzione non viene mai preso.
    """

    def __init__(self, constraint: str):
        super().__init__(constraint)
        self._constraint = constraint

    @property
    def diag(self):
        return SimpleNamespace(constraint_name=self._constraint)


class FakeConn:
    def __init__(self, *, commit_fails=False, store=None, order=None):
        self.commit_fails = commit_fails
        self.store = store
        self.order = order if order is not None else []

    def commit(self):
        self.order.append("commit")
        if self.commit_fails:
            raise RuntimeError("il commit e' fallito")
        if self.store is not None:
            self.store.commit()

    def rollback(self):
        self.order.append("rollback")
        if self.store is not None:
            self.store.restore()


class Store:
    """Tre agenzie, i territori, le assegnazioni, e i DUE VINCOLI VERI.

    I vincoli sono riprodotti qui di proposito: senza,
    `uq_agency_territory_single_active` non esisterebbe in nessun test che non
    parli a PostgreSQL, e una mutazione che toglie il controllo esplicito dal
    service passerebbe perche' non c'e' nessun altro a dire di no.

    P27-3 ha insegnato l'altra meta' della lezione: uno store che fa rispettare
    il vincolo puo' MASCHERARE una mutazione, perche' il risultato finale resta
    corretto anche quando il codice sbaglia. Per questo lo store registra anche
    `writes`, l'ordine delle scritture TENTATE: e' li' che si vede un
    trasferimento che crea prima di chiudere, e nessun vincolo lo puo'
    nascondere.
    """

    def __init__(self):
        self.agencies = {
            aid: {
                "id": aid, "name": f"Agenzia {aid}", "slug": f"agenzia-{aid}",
                "status": "active", "settings": {},
                "created_at": CREATED, "updated_at": CREATED,
            }
            for aid in (AGENCY_A, AGENCY_B, AGENCY_C)
        }
        self.territories: dict[int, dict] = {}
        self.assignments: dict[int, dict] = {}
        self.writes: list[str] = []
        self._next_t = 100
        self._next_a = 500
        self._snapshot = None

    # -- transazione simulata ------------------------------------------------
    def snapshot(self):
        self._snapshot = (
            {k: dict(v) for k, v in self.territories.items()},
            {k: dict(v) for k, v in self.assignments.items()},
        )

    def restore(self):
        if self._snapshot is None:
            return
        self.territories, self.assignments = self._snapshot
        self._snapshot = None

    def commit(self):
        self._snapshot = None

    # -- i due vincoli -------------------------------------------------------
    def _guard_identity(self, kind, canonical_key):
        for row in self.territories.values():
            if row["kind"] == kind and row["canonical_key"] == canonical_key:
                raise FakeUnique("network_territories_identity_unq")

    def _guard_single_active(self, territory_id, exclude_id=None):
        for row in self.assignments.values():
            if (row["territory_id"] == territory_id
                    and row["status"] == ASSIGNMENT_ACTIVE
                    and row["id"] != exclude_id):
                raise FakeUnique("uq_agency_territory_single_active")

    # -- scritture -----------------------------------------------------------
    def create_territory(self, *, kind, canonical_key, label):
        self.writes.append("create-territory")
        self._guard_identity(kind, canonical_key)
        self._next_t += 1
        row = {
            "id": self._next_t, "kind": kind, "canonical_key": canonical_key,
            "label": label, "created_at": CREATED, "updated_at": CREATED,
        }
        self.territories[row["id"]] = row
        return dict(row)

    def create_assignment(self, *, territory_id, agency_id, status):
        self.writes.append("create-assignment")
        if status == ASSIGNMENT_ACTIVE:
            self._guard_single_active(territory_id)
        self._next_a += 1
        row = {
            "id": self._next_a, "territory_id": territory_id,
            "agency_id": agency_id, "status": status,
            "created_at": CREATED, "updated_at": CREATED,
        }
        self.assignments[row["id"]] = row
        return dict(row)

    def update_assignment(self, assignment_id, fields):
        unknown = [
            k for k in fields
            if k not in territories_repository.ASSIGNMENT_UPDATABLE_COLUMNS
        ]
        if unknown or not fields:
            raise ValueError(f"colonne non aggiornabili: {sorted(unknown)}")
        self.writes.append(f"update-assignment:{fields.get('status')}")
        row = self.assignments.get(assignment_id)
        if row is None:
            return None
        if fields.get("status") == ASSIGNMENT_ACTIVE:
            self._guard_single_active(row["territory_id"], exclude_id=assignment_id)
        row.update(fields)
        row["updated_at"] = LATER
        return dict(row)

    # -- comodita' per i test -------------------------------------------------
    def seed_territory(self, key="alba-adriatica", kind="municipality",
                       label="Alba Adriatica"):
        return self.create_territory(kind=kind, canonical_key=key, label=label)

    def seed_assignment(self, territory_id, agency_id, status=ASSIGNMENT_ACTIVE):
        return self.create_assignment(
            territory_id=territory_id, agency_id=agency_id, status=status
        )

    def active_for(self, territory_id):
        return [
            r for r in self.assignments.values()
            if r["territory_id"] == territory_id and r["status"] == ASSIGNMENT_ACTIVE
        ]


@pytest.fixture
def service(monkeypatch):
    state = {
        "store": Store(), "audit": [], "audit_fails": False,
        "order": [], "commit_fails": False, "session": _ctx(),
    }

    @contextmanager
    def _cursor():
        state["order"].append("open")
        conn = FakeConn(
            commit_fails=state["commit_fails"],
            store=state["store"],
            order=state["order"],
        )
        state["conn"] = conn
        state["store"].snapshot()
        try:
            yield conn, object()
        except Exception:
            conn.rollback()
            raise

    monkeypatch.setattr(
        territories_service, "platform_operation_cursor", _cursor
    )

    store = state["store"]
    agencies_repo = territories_service.agencies_repository

    monkeypatch.setattr(
        agencies_repo, "get_agency",
        lambda cur, agency_id: (
            dict(store.agencies[agency_id]) if agency_id in store.agencies else None
        ),
    )

    repo = territories_service.territories_repository

    monkeypatch.setattr(
        repo, "get_territory",
        lambda cur, tid: dict(store.territories[tid]) if tid in store.territories else None,
    )
    monkeypatch.setattr(
        repo, "territory_identity_exists",
        lambda cur, *, kind, canonical_key: any(
            r["kind"] == kind and r["canonical_key"] == canonical_key
            for r in store.territories.values()
        ),
    )
    monkeypatch.setattr(
        repo, "create_territory",
        lambda cur, *, kind, canonical_key, label: store.create_territory(
            kind=kind, canonical_key=canonical_key, label=label
        ),
    )
    def _get_active(cur, tid):
        rows = store.active_for(tid)
        return dict(rows[0]) if rows else None

    monkeypatch.setattr(repo, "get_active_assignment", _get_active)
    monkeypatch.setattr(
        repo, "get_assignment",
        lambda cur, aid, agency_id: (
            dict(store.assignments[aid])
            if aid in store.assignments
            and store.assignments[aid]["agency_id"] == agency_id
            else None
        ),
    )
    monkeypatch.setattr(
        repo, "create_assignment",
        lambda cur, *, territory_id, agency_id, status: store.create_assignment(
            territory_id=territory_id, agency_id=agency_id, status=status
        ),
    )
    monkeypatch.setattr(
        repo, "update_assignment",
        lambda cur, aid, fields: store.update_assignment(aid, fields),
    )

    def _list_territories(cur, *, kind, agency_id, assignment_status, limit, offset):
        rows = []
        for t in store.territories.values():
            if kind is not None and t["kind"] != kind:
                continue
            related = [
                a for a in store.assignments.values() if a["territory_id"] == t["id"]
            ]
            if agency_id is not None or assignment_status is not None:
                if not any(
                    (agency_id is None or a["agency_id"] == agency_id)
                    and (assignment_status is None or a["status"] == assignment_status)
                    for a in related
                ):
                    continue
            active = [a for a in related if a["status"] == ASSIGNMENT_ACTIVE]
            rows.append({
                **t,
                "active_agency_id": active[0]["agency_id"] if active else None,
                "active_assignment_id": active[0]["id"] if active else None,
            })
        rows.sort(key=lambda r: (r["kind"], r["canonical_key"], r["id"]))
        return rows[offset:offset + limit]

    def _list_agency_assignments(cur, agency_id, *, status, limit, offset):
        rows = []
        for a in store.assignments.values():
            if a["agency_id"] != agency_id:
                continue
            if status is not None and a["status"] != status:
                continue
            t = store.territories[a["territory_id"]]
            rows.append({
                **a,
                "territory_kind": t["kind"],
                "territory_canonical_key": t["canonical_key"],
                "territory_label": t["label"],
            })
        rows.sort(key=lambda r: (r["territory_kind"], r["territory_canonical_key"], r["id"]))
        return rows[offset:offset + limit]

    monkeypatch.setattr(repo, "list_territories", _list_territories)
    monkeypatch.setattr(repo, "list_agency_assignments", _list_agency_assignments)

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


@pytest.fixture
def client(service, monkeypatch):
    """L'applicazione vera, con il ROUTER vero e la sola sessione simulata.

    Montato con `require_platform_admin` come dipendenza del mount, come in
    `main.py`: e' cosi' che 401 e 403 sono quelli veri e non una simulazione.
    """
    from operator_auth import dependencies as operator_deps

    def _resolve(_token):
        if service["session"] is None:
            return None
        return {"context": service["session"], "agency_name": "Agenzia",
                "expires_at": EXPIRES}

    monkeypatch.setattr(operator_deps.service, "session_from_token", _resolve)

    app = FastAPI()
    app.include_router(
        platform_router,
        dependencies=[Depends(platform_deps.require_platform_admin)],
    )
    http = TestClient(app, raise_server_exceptions=False)
    http.cookies.set("stima360_operator_session", "un-token")
    return http


TERRITORIES = f"{ROUTER_PREFIX}/territories"


def _agency_territories(agency_id=AGENCY_A) -> str:
    return f"{ROUTER_PREFIX}/agencies/{agency_id}/territories"


# ===========================================================================
# A - IL MODELLO
#
# Identita' e assegnazione sono due oggetti. E' la decisione da cui discende
# tutto il resto: la storia, la revoca che non cancella, il territorio libero
# che si distingue da quello mai dichiarato.
# ===========================================================================

def test_a1_the_territory_table_carries_no_agency(up_sql):
    """`network_territories` non ha `agency_id`, e non e' una dimenticanza.

    Con quella colonna, "presidiato da nessuno" e "mai dichiarato" sarebbero
    lo stesso NULL, e revocare significherebbe o azzerarla - perdendo QUALE
    agenzia - o cancellare la riga, perdendo il territorio.
    """
    body = _executable(up_sql)
    create = body[body.index("CREATE TABLE IF NOT EXISTS network_territories"):]
    create = create[:create.index(");")]
    assert "agency_id" not in create, create


def test_a1_the_assignment_table_carries_both_sides(up_sql):
    body = _executable(up_sql)
    create = body[
        body.index("CREATE TABLE IF NOT EXISTS agency_territory_assignments"):
    ]
    create = create[:create.index(");")]
    for column in ("territory_id", "agency_id", "status"):
        assert column in create, (column, create)


def test_a2_identity_is_kind_plus_canonical_key_and_never_the_label(up_sql):
    """IL VINCOLO DI IDENTITA' NON NOMINA `label`.

    E' la proprieta' che rende un'etichetta correggibile. Se `label` entrasse
    in questa UNIQUE, riscrivere 'Alba adriatica' in 'Alba Adriatica'
    creerebbe un secondo territorio, e l'assegnazione sul primo resterebbe li'
    dove nessuno la guarda piu'.
    """
    body = _executable(up_sql)
    match = re.search(
        r"CONSTRAINT\s+network_territories_identity_unq\s+UNIQUE\s*\(([^)]*)\)",
        body,
    )
    assert match, body
    columns = [c.strip() for c in match.group(1).split(",")]
    assert columns == ["kind", "canonical_key"], columns


def test_a2_the_repository_checks_identity_on_the_pair_and_not_the_label():
    source = (ROOT / "platform_admin" / "territories_repository.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "territory_identity_exists"
    )
    sql = " ".join(
        node.value for node in ast.walk(fn)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and "SELECT" in node.value
    )
    assert "kind = %s" in sql and "canonical_key = %s" in sql, sql
    assert "label" not in sql, sql


def test_a3_the_three_kinds_match_the_database_check(up_sql):
    """La tupla dell'applicazione e il CHECK della 058, confrontati.

    Un quarto livello aggiunto da una parte sola fallisce qui invece che in
    produzione alla prima scrittura.
    """
    match = re.search(
        r"CONSTRAINT\s+network_territories_kind_chk\s+CHECK\s*\(kind IN \(([^)]*)\)",
        _executable(up_sql),
    )
    assert match, up_sql
    in_db = tuple(v.strip().strip("'") for v in match.group(1).split(","))
    assert in_db == TERRITORY_KINDS, (in_db, TERRITORY_KINDS)


def test_a3_region_and_microzone_are_not_levels():
    """I DUE LIVELLI ASSENTI, e l'assenza e' asserita.

    `region` esiste solo in `buy_location_criteria`, che sono criteri di
    ricerca di un acquirente. `microzone` e' testo libero indicizzato da
    `zone_valori` per fare un prezzo. Nessuno dei due e' un'autorita'
    territoriale, e includerli avrebbe fatto di una lista della spesa la fonte
    di chi possiede una zona.
    """
    assert "region" not in TERRITORY_KINDS
    assert "microzone" not in TERRITORY_KINDS
    assert "radius" not in TERRITORY_KINDS


def test_a4_the_three_assignment_statuses_match_the_database_check(up_sql):
    match = re.search(
        r"CONSTRAINT\s+agency_territory_assignments_status_chk\s+"
        r"CHECK\s*\(status IN \(([^)]*)\)",
        _executable(up_sql),
    )
    assert match, up_sql
    in_db = tuple(v.strip().strip("'") for v in match.group(1).split(","))
    assert in_db == ASSIGNMENT_STATUSES, (in_db, ASSIGNMENT_STATUSES)


def test_a5_the_canonical_key_pattern_matches_the_database_check(up_sql):
    body = _executable(up_sql)
    assert TERRITORY_CANONICAL_KEY_PATTERN in body, TERRITORY_CANONICAL_KEY_PATTERN


@pytest.mark.parametrize("key,valida", [
    ("alba-adriatica", True),
    ("te", True),
    ("64011", True),
    ("san-benedetto-del-tronto", True),
    ("Alba-Adriatica", False),
    ("alba adriatica", False),
    ("alba--adriatica", False),
    ("-alba", False),
    ("alba-", False),
    ("alba_adriatica", False),
    ("alba'adriatica", False),
    ("", False),
])
def test_a5_the_pattern_accepts_only_a_canonical_shape(key, valida):
    assert bool(re.match(TERRITORY_CANONICAL_KEY_PATTERN, key)) is valida, key


def test_a6_there_is_no_ended_at_column(up_sql):
    """NIENTE `ended_at`, ed e' una decisione.

    `updated_at` porta gia' l'istante in cui l'assegnazione ha cambiato stato.
    Una seconda colonna con lo stesso istante sarebbe una seconda verita' sullo
    stesso fatto, e alla prima divergenza - una sospensione seguita da una
    riattivazione - nessuno saprebbe a quale credere.
    """
    body = _executable(up_sql)
    assert "ended_at" not in body, body


# ===========================================================================
# B - LA MIGRATION 058
# ===========================================================================

@pytest.fixture(scope="module")
def runner():
    from scripts import p26_migrate

    return p26_migrate


def test_b1_058_passes_every_rule_the_runner_enforces(runner):
    migrations = {m.version: m for m in runner.discover_migrations()}
    assert VERSION in migrations, sorted(migrations)
    assert runner.validate_migration(migrations[VERSION]) == []


def test_b1_the_whole_migration_set_is_still_valid_and_contiguous(runner):
    """Aggiungere 058 non deve rompere l'insieme.

    Un buco o un doppione nella sequenza e' il tipo di errore che si scopre in
    fase di deploy, cioe' nel momento peggiore.
    """
    migrations = runner.discover_migrations()
    runner.verify_contiguous(migrations)
    assert [v for m in migrations for v in runner.validate_migration(m)] == []


def test_b1_058_follows_057(runner):
    """058 esiste e segue immediatamente 057.

    Questo test diceva anche "e' la piu' alta". Non poteva restare: P27-6 ha
    aggiunto la 059, e un perno sulla cima della sequenza dentro il file di una
    fase chiusa si rompe a ogni fase successiva - esattamente quel che dice il
    commento della sezione K poco piu' sotto, che l'elenco esaustivo appartiene
    SEMPRE alla fase piu' recente. La cima e' ora affermata in
    `tests/test_p27_6_lead_routing.py`, dove si rompera' quando arrivera' la
    060, che e' il posto giusto in cui accorgersene.

    Quel che P27-5 deve continuare a garantire e' la sua posizione: 058 c'e',
    e fra lei e 057 non si e' infilato niente.
    """
    numbers = sorted(m.number for m in runner.discover_migrations())
    assert 58 in numbers, numbers[-4:]
    assert max(n for n in numbers if n < 58) == 57, numbers[-4:]


def test_b1_058_is_above_the_runner_owned_transaction_gate(runner):
    assert 58 >= runner.RUNNER_OWNED_TRANSACTION_FROM


def test_b2_the_up_file_opens_no_transaction_of_its_own(up_sql, runner):
    body = _executable(up_sql)
    assert not runner.BEGIN_RE.search(body), body
    assert not runner.COMMIT_RE.search(body), body


def test_b2_the_up_file_never_removes_a_ledger_row(up_sql):
    body = _executable(up_sql)
    assert "schema_migrations" not in body, body


def test_b2_the_up_file_alters_no_pre_existing_table(up_sql):
    """Additiva. Nessuna ALTER, e nessuna colonna aggiunta ad `agencies`."""
    body = _executable(up_sql)
    assert "ALTER TABLE" not in body.upper(), body
    assert "DROP " not in body.upper(), body


def test_b2_the_up_file_seeds_no_row(up_sql):
    """Nessun territorio predefinito.

    Inventare i tre comuni di `main.normalizza_comune` trasformerebbe una
    allowlist scritta a mano dentro un file Python in un fatto sull'azienda, e
    la prima volta che la rete si allarga nessuno saprebbe da dove escono.

    Le uniche INSERT del file sono quelle della sonda, che si annulla.
    """
    body = _executable(up_sql)
    inserts = [
        line for line in body.splitlines()
        if "INSERT INTO" in line.upper() and "probe" not in line.lower()
    ]
    assert all(
        "agency_territory_assignments" in line or "network_territories" in line
        for line in inserts
    ), inserts
    assert "P27_5_058_PROBE_ROLLBACK" in body


def test_b3_a_down_file_exists_and_brackets_its_own_transaction(down_sql, runner):
    assert runner.BEGIN_RE.search(down_sql), down_sql
    assert runner.COMMIT_RE.search(down_sql), down_sql


def test_b3_the_down_removes_everything_the_up_creates(down_sql):
    for table in ("agency_territory_assignments", "network_territories"):
        assert f"DROP TABLE IF EXISTS {table}" in down_sql, table


def test_b3_the_down_drops_the_child_before_the_parent(down_sql):
    """L'ordine, perche' l'alternativa e' CASCADE.

    Le assegnazioni tengono le due chiavi esterne. Dropparle per prime le
    rilascia; l'ordine inverso richiederebbe un CASCADE, ed e' cosi' che in un
    file di down sparisce una tabella che nessuno voleva toccare.
    """
    child = down_sql.index("DROP TABLE IF EXISTS agency_territory_assignments")
    parent = down_sql.index("DROP TABLE IF EXISTS network_territories")
    assert child < parent, down_sql


def test_b3_the_down_does_not_cascade(down_sql):
    body = _executable(down_sql)
    assert "CASCADE" not in body.upper(), body


def test_b3_the_down_removes_its_own_ledger_row(down_sql):
    assert f"DELETE FROM schema_migrations WHERE version = '{VERSION}'" in down_sql


def test_b4_the_single_active_index_is_unique_and_partial(up_sql):
    """L'INVARIANTE DI PRODOTTO, NELL'INDICE.

    Tre proprieta', e togliendone una qualunque la regola smette di valere:

    * UNIQUE, o due agenzie prendono lo stesso territorio;
    * su `territory_id`, o non e' il territorio a essere protetto;
    * PARZIALE su `status='active'`, o la storia - le revocate, le sospese -
      diventa impossibile da conservare.
    """
    body = _executable(up_sql)
    match = re.search(
        r"CREATE UNIQUE INDEX IF NOT EXISTS uq_agency_territory_single_active\s*"
        r"ON agency_territory_assignments \(([^)]*)\)\s*WHERE status = '(\w+)'",
        body,
    )
    assert match, body
    assert match.group(1).strip() == "territory_id", match.group(1)
    assert match.group(2) == ASSIGNMENT_ACTIVE, match.group(2)


def test_b4_the_index_is_not_on_the_territory_agency_pair(up_sql):
    """NON (territory_id, agency_id), ed e' cio' che permette il ritorno.

    Un'agenzia che ha avuto un territorio, lo ha perso e lo riprende deve
    produrre una SECONDA riga. Con la coppia unica l'unica strada sarebbe
    riscrivere la riga revocata, cancellando il periodo in mezzo.
    """
    body = _executable(up_sql)
    assert "(territory_id, agency_id)" not in body.replace(" ", " "), body


def test_b5_both_foreign_keys_restrict_and_neither_cascades(up_sql):
    """RESTRICT esplicito su entrambe, come ogni figlia di `agencies` in P26.

    Nessun CASCADE - cancellare un'agenzia si porterebbe via il registro di
    cosa ha presidiato - e nessun SET NULL, che direbbe che qualcuno teneva
    questo territorio e rifiuterebbe di dire chi.

    RESTRICT e non il NO ACTION che l'omissione darebbe: e' cio' che
    dichiarano 027, 028, 031, 034, 037 e 043 senza eccezioni, e una clausola
    omessa si legge come una decisione che nessuno ha preso.
    """
    body = _executable(up_sql).upper()
    assert "ON DELETE CASCADE" not in body, body
    assert "ON DELETE SET NULL" not in body, body
    assert body.count("ON DELETE RESTRICT") == 2, body
    assert "REFERENCES NETWORK_TERRITORIES(ID) ON DELETE RESTRICT" in body, body
    assert "REFERENCES AGENCIES(ID)            ON DELETE RESTRICT" in body, body


def test_b5_the_restrict_on_agencies_is_in_the_p26_6_reviewed_inventory():
    """LA CONSEGUENZA E' DICHIARATA, NON SCOPERTA.

    Il cleanup di P26-6 cancella agenzie. Una FK non-CASCADE nuova verso
    `agencies` significa che quel cleanup puo' incontrare un rifiuto, e
    `test_86i_no_relevant_foreign_key_is_unaccounted_for` esiste proprio per
    non far passare una chiave del genere senza che qualcuno l'abbia guardata.

    Questo test e' il lato P27-5 di quella prova: se un domani l'inventario
    perdesse questa riga, fallirebbe qui oltre che li'.
    """
    source = (ROOT / "tests" / "test_p26_6_live_cert_script.py").read_text(
        encoding="utf-8"
    )
    assert (
        '("agency_territory_assignments", "agency_id", "agencies", "RESTRICT")'
        in source
    ), "la FK di P27-5 non e' nell'inventario rivisto di P26-6"


def test_b5_the_migration_verifies_its_own_objects_from_the_catalogue(up_sql):
    body = _executable(up_sql)
    assert "pg_index" in body, body
    assert "indisunique" in body, body
    assert "indpred" in body, body
    assert "confdeltype" in body, body


def test_b6_the_migration_proves_the_invariant_actually_refuses(up_sql):
    """Un indice che esiste e non morde non vale niente.

    La sonda scrive due assegnazioni attive vere sullo stesso territorio e
    pretende che la seconda sia rifiutata, poi annulla tutto attraverso il
    savepoint implicito di un blocco BEGIN ... EXCEPTION - lo stesso strumento
    della 057, e per la stessa ragione: PL/pgSQL non puo' emettere controllo di
    transazione.
    """
    body = _executable(up_sql)
    assert "unique_violation" in body, body
    assert "v_refused" in body, body
    assert "invariant is not effective" in body, body
    # Nessuna DELETE per annullare la sonda: si esce dal blocco sollevando.
    assert "DELETE FROM network_territories" not in body, body


# ===========================================================================
# C - TERRITORIO
# ===========================================================================

def test_c1_create_declares_a_territory_and_assigns_it_to_nobody(service):
    created = territories_service.create_territory(
        service["session"], kind="municipality",
        canonical_key="alba-adriatica", label="Alba Adriatica",
        created_fields=["kind", "canonical_key", "label"],
    )
    assert created["kind"] == "municipality"
    assert created["canonical_key"] == "alba-adriatica"
    assert created["active_assignment"] is None
    assert service["store"].assignments == {}


def test_c2_a_duplicate_identity_is_a_conflict(service):
    store = service["store"]
    store.seed_territory()
    with pytest.raises(PlatformConflict):
        territories_service.create_territory(
            service["session"], kind="municipality",
            canonical_key="alba-adriatica", label="Un'altra etichetta",
            created_fields=["kind", "canonical_key", "label"],
        )


def test_c2_the_race_on_identity_is_a_conflict_and_not_a_500(service, monkeypatch):
    """Fra il controllo e la INSERT c'e' una finestra.

    Si simula la corsa disattivando il controllo esplicito: cio' che resta e'
    il vincolo, e la sua violazione deve diventare un 409 e non un 500 con
    dentro il nome di un indice.
    """
    store = service["store"]
    store.seed_territory()
    monkeypatch.setattr(
        territories_service.territories_repository,
        "territory_identity_exists",
        lambda cur, *, kind, canonical_key: False,
    )
    with pytest.raises(PlatformConflict):
        territories_service.create_territory(
            service["session"], kind="municipality",
            canonical_key="alba-adriatica", label="Alba Adriatica",
            created_fields=["kind"],
        )


def test_c3_the_same_label_on_two_different_keys_is_two_territories(service):
    """LA PROVA CHE L'ETICHETTA NON E' L'IDENTITA'."""
    for key in ("alba-adriatica", "alba-adriatica-frazione"):
        territories_service.create_territory(
            service["session"], kind="municipality",
            canonical_key=key, label="Alba Adriatica",
            created_fields=["kind", "canonical_key", "label"],
        )
    assert len(service["store"].territories) == 2


def test_c3_the_same_key_on_two_different_kinds_is_two_territories(service):
    """`64011` come CAP e `64011` come comune non sono lo stesso posto, ed e'
    il `kind` a separarli."""
    for kind in ("municipality", "postal_code"):
        territories_service.create_territory(
            service["session"], kind=kind, canonical_key="64011",
            label="64011", created_fields=["kind"],
        )
    assert len(service["store"].territories) == 2


def test_c4_get_returns_the_territory_with_whoever_holds_it(service):
    store = service["store"]
    territory = store.seed_territory()
    store.seed_assignment(territory["id"], AGENCY_B)
    found = territories_service.get_territory(territory["id"])
    assert found["active_assignment"]["agency_id"] == AGENCY_B


def test_c4_get_returns_none_for_a_free_territory(service):
    territory = service["store"].seed_territory()
    assert territories_service.get_territory(territory["id"])["active_assignment"] is None


def test_c4_get_on_a_missing_territory_raises(service):
    with pytest.raises(TerritoryNotFound):
        territories_service.get_territory(999999)


def test_c5_the_list_shows_free_territories_too(service):
    """Il join sull'attiva e' un LEFT, e il filtro sullo stato sta nella ON.

    In una WHERE lo trasformerebbe in un INNER, e i territori liberi -
    esattamente quelli che si cercano quando si apre un affiliato nuovo -
    sparirebbero dall'elenco.
    """
    store = service["store"]
    libero = store.seed_territory("tortoreto", label="Tortoreto")
    preso = store.seed_territory("martinsicuro", label="Martinsicuro")
    store.seed_assignment(preso["id"], AGENCY_A)

    rows = territories_service.list_territories(limit=50, offset=0)
    by_key = {r["canonical_key"]: r for r in rows}
    assert by_key["tortoreto"]["active_agency_id"] is None
    assert by_key["martinsicuro"]["active_agency_id"] == AGENCY_A
    assert libero["id"] and preso["id"]


def _sql_of(module: str, function: str) -> str:
    """Il SQL ESEGUIBILE di una funzione, ricomposto dall'AST.

    Le query di questo repository sono f-string: i loro pezzi letterali sono
    piu' di una `Constant`, e prendere il file intero riporterebbe anche i
    docstring - che e' il modo in cui questo progetto si e' gia' fatto passare
    piu' di un test per la ragione sbagliata. Qui si guarda dentro le sole
    JoinedStr/Constant che compongono un `cur.execute`.
    """
    source = (ROOT / "platform_admin" / f"{module}.py").read_text(encoding="utf-8")
    fn = next(
        n for n in ast.walk(ast.parse(source))
        if isinstance(n, ast.FunctionDef) and n.name == function
    )
    pieces: list[str] = []
    for call in ast.walk(fn):
        if not (isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "execute"):
            continue
        target = call.args[0] if call.args else None
        if isinstance(target, ast.Constant):
            pieces.append(target.value)
        elif isinstance(target, ast.JoinedStr):
            pieces += [
                v.value for v in target.values
                if isinstance(v, ast.Constant) and isinstance(v.value, str)
            ]
    return " ".join(" ".join(p.split()) for p in pieces)


def test_c5_the_list_query_filters_the_active_join_inside_the_on():
    sql = _sql_of("territories_repository", "list_territories")
    assert "LEFT JOIN" in sql, sql
    on = sql[sql.index("LEFT JOIN"):sql.index("ORDER BY")]
    assert "a.status = 'active'" in on, on


# ===========================================================================
# D - ASSEGNAZIONE
#
# L'invariante di prodotto:
#
#     un territorio canonico ha AL MASSIMO UNA assegnazione attiva.
# ===========================================================================

def test_d1_a_free_territory_can_be_assigned(service):
    store = service["store"]
    territory = store.seed_territory()
    assignment = territories_service.assign_territory(
        service["session"], AGENCY_A,
        territory_id=territory["id"], created_fields=["territory_id"],
    )
    assert assignment["agency_id"] == AGENCY_A
    assert assignment["status"] == ASSIGNMENT_ACTIVE


def test_d2_a_second_agency_cannot_take_a_held_territory(service):
    """IL TERRITORIO PROTETTO."""
    store = service["store"]
    territory = store.seed_territory()
    store.seed_assignment(territory["id"], AGENCY_A)

    with pytest.raises(PlatformConflict) as exc:
        territories_service.assign_territory(
            service["session"], AGENCY_B,
            territory_id=territory["id"], created_fields=["territory_id"],
        )
    assert "altra agenzia" in str(exc.value)


def test_d2_the_refused_assignment_does_not_revoke_the_incumbent(service):
    """NESSUN TRASFERIMENTO IMPLICITO.

    Il difetto peggiore che questa route potesse avere: l'affiliato che
    perde il territorio non compare nella richiesta, e nessuno se ne
    accorgerebbe fino alla prima lamentela.
    """
    store = service["store"]
    territory = store.seed_territory()
    held = store.seed_assignment(territory["id"], AGENCY_A)

    with pytest.raises(PlatformConflict):
        territories_service.assign_territory(
            service["session"], AGENCY_B,
            territory_id=territory["id"], created_fields=["territory_id"],
        )
    assert store.assignments[held["id"]]["status"] == ASSIGNMENT_ACTIVE
    assert store.assignments[held["id"]]["agency_id"] == AGENCY_A
    assert len(store.assignments) == 1


def test_d3_the_same_agency_twice_is_a_distinct_conflict(service):
    """"ce l'hai gia' tu" e "ce l'ha un altro" portano a due azioni diverse."""
    store = service["store"]
    territory = store.seed_territory()
    store.seed_assignment(territory["id"], AGENCY_A)

    with pytest.raises(PlatformConflict) as exc:
        territories_service.assign_territory(
            service["session"], AGENCY_A,
            territory_id=territory["id"], created_fields=["territory_id"],
        )
    assert "questa agenzia" in str(exc.value)
    assert "altra agenzia" not in str(exc.value)


def test_d3_a_second_assignment_is_never_a_silent_no_op(service):
    """Non un 200 che non scrive: un conflitto."""
    store = service["store"]
    territory = store.seed_territory()
    store.seed_assignment(territory["id"], AGENCY_A)
    before = len(store.assignments)

    with pytest.raises(PlatformConflict):
        territories_service.assign_territory(
            service["session"], AGENCY_A,
            territory_id=territory["id"], created_fields=["territory_id"],
        )
    assert len(store.assignments) == before
    assert service["operations"]() == []


def test_d4_the_race_is_closed_by_the_constraint(service, monkeypatch):
    """LA CORSA, e chi la chiude.

    Due richieste simultanee superano entrambe il controllo esplicito: fra il
    SELECT e la INSERT c'e' una finestra. Si simula disattivando il controllo,
    e cio' che resta e' il vincolo del database - che deve diventare un 409 e
    non un 500 con dentro il nome di un indice.
    """
    store = service["store"]
    territory = store.seed_territory()
    store.seed_assignment(territory["id"], AGENCY_A)

    monkeypatch.setattr(
        territories_service.territories_repository,
        "get_active_assignment", lambda cur, tid: None,
    )
    with pytest.raises(PlatformConflict) as exc:
        territories_service.assign_territory(
            service["session"], AGENCY_B,
            territory_id=territory["id"], created_fields=["territory_id"],
        )
    assert "assegnazione attiva" in str(exc.value)
    assert len(store.active_for(territory["id"])) == 1


def test_d4_an_unknown_constraint_is_not_guessed_into_a_409(service, monkeypatch):
    """Un 409 inventato su un vincolo sconosciuto direbbe al chiamante di
    cambiare la richiesta, quando magari deve cambiarla chi ha scritto il
    codice."""
    territory = service["store"].seed_territory()

    def _boom(cur, **kwargs):
        raise FakeUnique("un_vincolo_che_nessuno_conosce")

    monkeypatch.setattr(
        territories_service.territories_repository, "create_assignment", _boom
    )
    with pytest.raises(errors.UniqueViolation):
        territories_service.assign_territory(
            service["session"], AGENCY_A,
            territory_id=territory["id"], created_fields=["territory_id"],
        )


def test_d5_assigning_a_missing_territory_is_not_found(service):
    with pytest.raises(TerritoryNotFound):
        territories_service.assign_territory(
            service["session"], AGENCY_A,
            territory_id=999999, created_fields=["territory_id"],
        )


def test_d5_assigning_to_a_missing_agency_is_not_found(service):
    territory = service["store"].seed_territory()
    with pytest.raises(AgencyNotFound):
        territories_service.assign_territory(
            service["session"], 999999,
            territory_id=territory["id"], created_fields=["territory_id"],
        )


def test_d6_suspend_keeps_the_row_and_frees_the_territory(service):
    """`suspended` conserva la relazione E libera il territorio.

    Il vincolo guarda solo le attive: sospendere serve proprio a poter dare il
    territorio a un altro senza cancellare la storia del primo.
    """
    store = service["store"]
    territory = store.seed_territory()
    held = store.seed_assignment(territory["id"], AGENCY_A)

    updated = territories_service.update_assignment(
        service["session"], AGENCY_A, held["id"], {"status": ASSIGNMENT_SUSPENDED}
    )
    assert updated["status"] == ASSIGNMENT_SUSPENDED
    assert held["id"] in store.assignments

    # e ora il territorio si puo' dare a un altro
    nuova = territories_service.assign_territory(
        service["session"], AGENCY_B,
        territory_id=territory["id"], created_fields=["territory_id"],
    )
    assert nuova["agency_id"] == AGENCY_B


def test_d6_revoke_keeps_the_row(service):
    """NESSUNA DELETE FISICA. Mai."""
    store = service["store"]
    territory = store.seed_territory()
    held = store.seed_assignment(territory["id"], AGENCY_A)

    territories_service.update_assignment(
        service["session"], AGENCY_A, held["id"], {"status": ASSIGNMENT_REVOKED}
    )
    assert held["id"] in store.assignments
    assert store.assignments[held["id"]]["status"] == ASSIGNMENT_REVOKED


def test_d7_reactivation_from_suspended_succeeds_when_the_territory_is_free(service):
    """Da `suspended` si torna. E' la differenza fra sospendere e revocare."""
    store = service["store"]
    territory = store.seed_territory()
    held = store.seed_assignment(
        territory["id"], AGENCY_A, status=ASSIGNMENT_SUSPENDED
    )
    updated = territories_service.update_assignment(
        service["session"], AGENCY_A, held["id"], {"status": ASSIGNMENT_ACTIVE}
    )
    assert updated["status"] == ASSIGNMENT_ACTIVE


def test_d7_reactivation_is_refused_when_another_agency_took_it(service):
    """IL CASO DELICATO.

    Riportare `active` un'assegnazione sospesa e' l'unica strada per rimettere
    un affiliato su un territorio, ma se nel frattempo il territorio e' passato
    a un altro la riattivazione e' un conflitto - NON una revoca silenziosa
    dell'altra.
    """
    store = service["store"]
    territory = store.seed_territory()
    sospesa = store.seed_assignment(
        territory["id"], AGENCY_A, status=ASSIGNMENT_SUSPENDED
    )
    altra = store.seed_assignment(territory["id"], AGENCY_B)

    with pytest.raises(PlatformConflict) as exc:
        territories_service.update_assignment(
            service["session"], AGENCY_A, sospesa["id"],
            {"status": ASSIGNMENT_ACTIVE},
        )
    assert "altra agenzia" in str(exc.value)
    assert store.assignments[altra["id"]]["status"] == ASSIGNMENT_ACTIVE
    assert store.assignments[sospesa["id"]]["status"] == ASSIGNMENT_SUSPENDED


def test_d7_reactivating_an_already_active_assignment_writes_nothing_wrong(service):
    """Riattivare cio' che e' gia' attivo non deve confliggere con se stesso.

    Il guardrail esclude la riga stessa, ed e' il motivo per cui questa PATCH
    passa invece di rimbalzare contro la propria assegnazione.
    """
    store = service["store"]
    territory = store.seed_territory()
    held = store.seed_assignment(territory["id"], AGENCY_A)

    updated = territories_service.update_assignment(
        service["session"], AGENCY_A, held["id"], {"status": ASSIGNMENT_ACTIVE}
    )
    assert updated["status"] == ASSIGNMENT_ACTIVE


def test_d8_an_assignment_of_another_agency_is_not_found_here(service):
    """La route e' `/agencies/{agency_id}/territories/{assignment_id}`.

    `agency_id` e' nella WHERE del repository e non un controllo successivo:
    non esiste un percorso in cui la riga sbagliata viene letta e poi scartata
    - il caso in cui il controllo successivo si dimentica.
    """
    store = service["store"]
    territory = store.seed_territory()
    altrui = store.seed_assignment(territory["id"], AGENCY_B)

    with pytest.raises(TerritoryAssignmentNotFound):
        territories_service.update_assignment(
            service["session"], AGENCY_A, altrui["id"],
            {"status": ASSIGNMENT_SUSPENDED},
        )
    assert store.assignments[altrui["id"]]["status"] == ASSIGNMENT_ACTIVE


def test_d9_only_status_is_updatable():
    """`agency_id` e `territory_id` NON sono colonne aggiornabili.

    Cambiare `agency_id` da una PATCH sarebbe un trasferimento eseguito da una
    route che dice di aggiornare uno stato, con una riga di registro che
    direbbe `assignment.update`: il momento in cui un territorio ha cambiato
    mano sarebbe irrecuperabile.
    """
    assert territories_repository.ASSIGNMENT_UPDATABLE_COLUMNS == ("status",)


@pytest.mark.parametrize("campo", ["agency_id", "territory_id", "id", "created_at"])
def test_d9_the_repository_refuses_a_column_it_does_not_own(campo):
    with pytest.raises(ValueError):
        territories_repository.update_assignment(object(), 1, {campo: 2})


def test_d10_an_agency_can_hold_a_territory_again_with_a_second_row(service):
    """IL RITORNO PRODUCE UNA SECONDA RIGA, non la riscrittura della prima.

    E' cio' che il vincolo su `territory_id` soltanto - e non sulla coppia -
    rende possibile: il periodo in mezzo resta leggibile.
    """
    store = service["store"]
    territory = store.seed_territory()
    prima = store.seed_assignment(
        territory["id"], AGENCY_A, status=ASSIGNMENT_REVOKED
    )
    seconda = territories_service.assign_territory(
        service["session"], AGENCY_A,
        territory_id=territory["id"], created_fields=["territory_id"],
    )
    assert seconda["id"] != prima["id"]
    assert store.assignments[prima["id"]]["status"] == ASSIGNMENT_REVOKED
    assert len(store.assignments) == 2


# ===========================================================================
# E - TRASFERIMENTO
#
# Esplicito e atomico. Non l'effetto collaterale di una PATCH.
# ===========================================================================

def test_e1_transfer_moves_the_territory(service):
    store = service["store"]
    territory = store.seed_territory()
    vecchia = store.seed_assignment(territory["id"], AGENCY_A)

    result = territories_service.transfer_territory(
        service["session"], territory["id"], AGENCY_B
    )
    assert result["assignment"]["agency_id"] == AGENCY_B
    assert result["assignment"]["status"] == ASSIGNMENT_ACTIVE
    assert result["revoked"]["id"] == vecchia["id"]
    assert result["revoked"]["status"] == ASSIGNMENT_REVOKED


def test_e1_the_previous_assignment_survives_as_history(service):
    store = service["store"]
    territory = store.seed_territory()
    vecchia = store.seed_assignment(territory["id"], AGENCY_A)

    territories_service.transfer_territory(
        service["session"], territory["id"], AGENCY_B
    )
    assert vecchia["id"] in store.assignments
    assert store.assignments[vecchia["id"]]["agency_id"] == AGENCY_A
    assert len(store.assignments) == 2


def test_e2_the_old_assignment_is_closed_before_the_new_one_opens(service):
    """L'ORDINE DELLE DUE SCRITTURE, ASSERITO DIRETTAMENTE.

    Non sul risultato: `uq_agency_territory_single_active` farebbe fallire
    l'ordine sbagliato, quindi un test sul solo esito finale sarebbe verde
    anche con il codice scritto male - la lezione che P27-3 ha imparato due
    volte. Qui si guarda la sequenza delle scritture TENTATE, che nessun
    vincolo puo' nascondere.
    """
    store = service["store"]
    territory = store.seed_territory()
    store.seed_assignment(territory["id"], AGENCY_A)
    store.writes.clear()

    territories_service.transfer_territory(
        service["session"], territory["id"], AGENCY_B
    )
    assert store.writes == [
        f"update-assignment:{ASSIGNMENT_REVOKED}",
        "create-assignment",
    ], store.writes


def test_e2_no_committed_state_ever_holds_two_active_assignments(service):
    store = service["store"]
    territory = store.seed_territory()
    store.seed_assignment(territory["id"], AGENCY_A)

    territories_service.transfer_territory(
        service["session"], territory["id"], AGENCY_B
    )
    assert len(store.active_for(territory["id"])) == 1
    assert store.active_for(territory["id"])[0]["agency_id"] == AGENCY_B


def test_e3_transfer_is_atomic_when_the_audit_fails(service):
    """Audit indisponibile -> il territorio resta dov'era. ENTRAMBE le
    scritture annullate, non una."""
    store = service["store"]
    territory = store.seed_territory()
    vecchia = store.seed_assignment(territory["id"], AGENCY_A)
    service["audit_fails"] = True

    with pytest.raises(PlatformAuditUnavailable):
        territories_service.transfer_territory(
            service["session"], territory["id"], AGENCY_B
        )
    assert store.assignments[vecchia["id"]]["status"] == ASSIGNMENT_ACTIVE
    assert store.assignments[vecchia["id"]]["agency_id"] == AGENCY_A
    assert len(store.assignments) == 1


def test_e3_transfer_is_atomic_when_the_second_write_fails(service, monkeypatch):
    """Se la creazione della nuova fallisce, la vecchia NON resta revocata.

    E' il caso in cui un territorio resterebbe di nessuno: revocato al primo
    affiliato e mai dato al secondo.
    """
    store = service["store"]
    territory = store.seed_territory()
    vecchia = store.seed_assignment(territory["id"], AGENCY_A)

    def _boom(cur, **kwargs):
        raise RuntimeError("la INSERT e' fallita")

    monkeypatch.setattr(
        territories_service.territories_repository, "create_assignment", _boom
    )
    with pytest.raises(RuntimeError):
        territories_service.transfer_territory(
            service["session"], territory["id"], AGENCY_B
        )
    assert store.assignments[vecchia["id"]]["status"] == ASSIGNMENT_ACTIVE


def test_e4_transferring_a_free_territory_is_a_conflict(service):
    """Trasferire cio' che nessuno presidia non e' un trasferimento: e'
    un'assegnazione, e ha il suo endpoint."""
    territory = service["store"].seed_territory()
    with pytest.raises(PlatformConflict) as exc:
        territories_service.transfer_territory(
            service["session"], territory["id"], AGENCY_B
        )
    assert "non ha un'assegnazione attiva" in str(exc.value)
    assert service["store"].assignments == {}


def test_e4_transferring_to_the_incumbent_is_a_conflict(service):
    """Non un no-op silenzioso: un 200 che non ha scritto nulla affermerebbe
    che il territorio ha cambiato mano."""
    store = service["store"]
    territory = store.seed_territory()
    store.seed_assignment(territory["id"], AGENCY_A)
    store.writes.clear()

    with pytest.raises(PlatformConflict):
        territories_service.transfer_territory(
            service["session"], territory["id"], AGENCY_A
        )
    assert store.writes == [], store.writes
    assert service["operations"]() == []


def test_e5_transfer_to_a_missing_agency_is_not_found(service):
    store = service["store"]
    territory = store.seed_territory()
    store.seed_assignment(territory["id"], AGENCY_A)
    with pytest.raises(AgencyNotFound):
        territories_service.transfer_territory(
            service["session"], territory["id"], 999999
        )


def test_e5_transfer_of_a_missing_territory_is_not_found(service):
    with pytest.raises(TerritoryNotFound):
        territories_service.transfer_territory(
            service["session"], 999999, AGENCY_B
        )


def test_e6_the_old_assignment_becomes_revoked_and_not_suspended(service):
    """`suspended` significa "in pausa, torna". Un territorio passato a un
    altro affiliato non torna per conto suo."""
    store = service["store"]
    territory = store.seed_territory()
    vecchia = store.seed_assignment(territory["id"], AGENCY_A)

    territories_service.transfer_territory(
        service["session"], territory["id"], AGENCY_B
    )
    assert store.assignments[vecchia["id"]]["status"] == ASSIGNMENT_REVOKED


# ===========================================================================
# F - STATO DELL'AGENZIA
#
# Amministrabile sempre, e nessun effetto automatico sulle assegnazioni.
# ===========================================================================

@pytest.mark.parametrize("stato", ["active", "suspended", "archived"])
def test_f1_territories_are_administrable_whatever_the_agency_status(service, stato):
    """E' il momento in cui serve di piu'.

    Si sospende un affiliato proprio quando qualcosa non va, e dover prima
    riattivarlo per sistemarne i territori sarebbe un giro assurdo che nel
    frattempo riapre il tenant. Stessa decisione di P27-3.
    """
    store = service["store"]
    store.agencies[AGENCY_A]["status"] = stato
    territory = store.seed_territory()

    assignment = territories_service.assign_territory(
        service["session"], AGENCY_A,
        territory_id=territory["id"], created_fields=["territory_id"],
    )
    assert assignment["status"] == ASSIGNMENT_ACTIVE

    updated = territories_service.update_assignment(
        service["session"], AGENCY_A, assignment["id"],
        {"status": ASSIGNMENT_SUSPENDED},
    )
    assert updated["status"] == ASSIGNMENT_SUSPENDED


def test_f2_no_module_changes_an_assignment_when_the_agency_status_changes():
    """NESSUN EFFETTO AUTOMATICO, e lo si dimostra sull'unico posto da cui
    potrebbe arrivare.

    `agencies_service.update_agency` e' cio' che cambia `agencies.status`. Se
    toccasse le assegnazioni, riattivare un affiliato non gli restituirebbe il
    territorio, perche' nel frattempo qualcosa lo avrebbe revocato per conto
    suo. Quali combinazioni di stato siano ELEGGIBILI per un lead lo decide
    P27-6, leggendo entrambi.
    """
    source = (ROOT / "platform_admin" / "agencies_service.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    called = {
        node.func.attr for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "territories_repository" not in source.split('"""')[0] or True
    for forbidden in (
        "create_assignment", "update_assignment", "get_active_assignment",
        "list_agency_assignments",
    ):
        assert forbidden not in called, (forbidden, sorted(called))
    assert "territories_repository" not in _code_of(source), source


def _code_of(source: str) -> str:
    """Il modulo senza docstring: cio' che viene ESEGUITO.

    Un modulo puo' nominare `territories_repository` in un commento per dire
    che NON lo usa. Cercare la parola nel file intero proverebbe il contrario
    di quel che si vuole - errore gia' fatto piu' volte in P27.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                node.body[0].value.value = ""
    return ast.unparse(tree)


def test_f2_no_territory_module_reads_or_writes_the_agency_status():
    """Il contrario: i territori non cambiano lo stato di un'agenzia."""
    code = _code_of(
        (ROOT / "platform_admin" / "territories_service.py").read_text(
            encoding="utf-8"
        )
    )
    assert "update_agency" not in code, code
    assert "create_agency" not in code, code


# ===========================================================================
# G - TRANSAZIONE E AUDIT
# ===========================================================================

@pytest.mark.parametrize("azione,atteso", [
    ("create", ACTION_TERRITORY_CREATE),
    ("assign", ACTION_TERRITORY_ASSIGNMENT_CREATE),
    ("update", ACTION_TERRITORY_ASSIGNMENT_UPDATE),
    ("transfer", ACTION_TERRITORY_TRANSFER),
])
def test_g1_every_mutation_records_its_own_action(service, azione, atteso):
    store = service["store"]
    territory = store.seed_territory()

    if azione == "create":
        territories_service.create_territory(
            service["session"], kind="province", canonical_key="te",
            label="Teramo", created_fields=["kind"],
        )
    elif azione == "assign":
        territories_service.assign_territory(
            service["session"], AGENCY_A,
            territory_id=territory["id"], created_fields=["territory_id"],
        )
    elif azione == "update":
        held = store.seed_assignment(territory["id"], AGENCY_A)
        territories_service.update_assignment(
            service["session"], AGENCY_A, held["id"],
            {"status": ASSIGNMENT_SUSPENDED},
        )
    else:
        store.seed_assignment(territory["id"], AGENCY_A)
        territories_service.transfer_territory(
            service["session"], territory["id"], AGENCY_B
        )

    rows = service["operations"]()
    assert len(rows) == 1, rows
    assert rows[0]["action"] == atteso
    assert rows[0]["result"] == RESULT_SUCCESS


def test_g2_territory_create_targets_the_territory_and_no_agency(service):
    """Un territorio appena dichiarato non appartiene a nessuno.

    `target_agency_id=None` e non l'agenzia dell'attore: metterci un'agenzia
    qualunque farebbe comparire questo atto nella cronologia di un'agenzia che
    non c'entra.
    """
    created = territories_service.create_territory(
        service["session"], kind="municipality", canonical_key="tortoreto",
        label="Tortoreto", created_fields=["kind", "canonical_key", "label"],
    )
    row = service["operations"]()[0]
    assert row["target_type"] == TARGET_TYPE_TERRITORY
    assert row["target_id"] == created["id"]
    assert row["target_agency_id"] is None


def test_g2_assignment_create_targets_the_assignment_and_the_agency(service):
    store = service["store"]
    territory = store.seed_territory()
    assignment = territories_service.assign_territory(
        service["session"], AGENCY_A,
        territory_id=territory["id"], created_fields=["territory_id"],
    )
    row = service["operations"]()[0]
    assert row["target_type"] == TARGET_TYPE_TERRITORY_ASSIGNMENT
    assert row["target_id"] == assignment["id"]
    assert row["target_agency_id"] == AGENCY_A


def test_g2_assignment_update_targets_the_assignment_and_the_agency(service):
    store = service["store"]
    territory = store.seed_territory()
    held = store.seed_assignment(territory["id"], AGENCY_A)
    territories_service.update_assignment(
        service["session"], AGENCY_A, held["id"],
        {"status": ASSIGNMENT_REVOKED},
    )
    row = service["operations"]()[0]
    assert row["target_type"] == TARGET_TYPE_TERRITORY_ASSIGNMENT
    assert row["target_id"] == held["id"]
    assert row["target_agency_id"] == AGENCY_A


def test_g2_transfer_targets_the_territory_and_the_receiving_agency(service):
    """Il territorio, e l'agenzia che LO RICEVE.

    Quella che lo perde e' ricostruibile dalla riga revocata; metterla anche
    nei metadata duplicherebbe nel registro un dato che le tabelle gia'
    portano.
    """
    store = service["store"]
    territory = store.seed_territory()
    store.seed_assignment(territory["id"], AGENCY_A)
    territories_service.transfer_territory(
        service["session"], territory["id"], AGENCY_B
    )
    row = service["operations"]()[0]
    assert row["target_type"] == TARGET_TYPE_TERRITORY
    assert row["target_id"] == territory["id"]
    assert row["target_agency_id"] == AGENCY_B


def test_g3_the_transfer_writes_one_audit_row_for_one_act(service):
    """UNA riga per UN atto.

    Le due righe cambiate sono le due meta' di una cosa sola, e due voci nel
    registro suggerirebbero che possano essere avvenute separatamente - che e'
    precisamente cio' che la transazione esclude.
    """
    store = service["store"]
    territory = store.seed_territory()
    store.seed_assignment(territory["id"], AGENCY_A)
    territories_service.transfer_territory(
        service["session"], territory["id"], AGENCY_B
    )
    assert len(service["operations"]()) == 1


def test_g4_transfer_metadata_is_the_structural_change_and_nothing_else(service):
    store = service["store"]
    territory = store.seed_territory()
    store.seed_assignment(territory["id"], AGENCY_A)
    territories_service.transfer_territory(
        service["session"], territory["id"], AGENCY_B
    )
    assert service["operations"]()[0]["metadata"] == {"changed_fields": ["agency_id"]}


@pytest.mark.parametrize("azione", ["create", "assign", "update", "transfer"])
def test_g4_no_metadata_ever_carries_a_geographic_value(service, azione):
    """IL REGISTRO NON CONTIENE GEOGRAFIA.

    Nomi di campo e categorie, mai valori: non `canonical_key`, non `label`,
    non il nome commerciale dell'agenzia. Una riga di audit si legge fra anni e
    non e' il posto in cui conservare dati che le tabelle gia' tengono.
    """
    store = service["store"]
    territory = store.seed_territory("alba-adriatica", label="Alba Adriatica")

    if azione == "create":
        territories_service.create_territory(
            service["session"], kind="municipality", canonical_key="tortoreto",
            label="Tortoreto", created_fields=["kind", "canonical_key", "label"],
        )
    elif azione == "assign":
        territories_service.assign_territory(
            service["session"], AGENCY_A,
            territory_id=territory["id"], created_fields=["territory_id"],
        )
    elif azione == "update":
        held = store.seed_assignment(territory["id"], AGENCY_A)
        territories_service.update_assignment(
            service["session"], AGENCY_A, held["id"],
            {"status": ASSIGNMENT_SUSPENDED},
        )
    else:
        store.seed_assignment(territory["id"], AGENCY_A)
        territories_service.transfer_territory(
            service["session"], territory["id"], AGENCY_B
        )

    blob = repr(service["operations"]()[0]["metadata"])
    for leak in ("Alba", "alba-adriatica", "Tortoreto", "tortoreto",
                 "Agenzia", "agenzia-"):
        assert leak not in blob, (leak, blob)


@pytest.mark.parametrize("azione", ["create", "assign", "update", "transfer"])
def test_g5_nothing_is_committed_when_the_audit_fails(service, azione):
    """LA REGOLA DELL'INTERO PACKAGE, su ognuna delle quattro mutazioni.

        nessuna modifica amministrativa viene committata
        se il suo audit non e' stato scritto.
    """
    store = service["store"]
    territory = store.seed_territory()
    held = store.seed_assignment(territory["id"], AGENCY_A)
    prima = {k: dict(v) for k, v in store.assignments.items()}
    prima_t = {k: dict(v) for k, v in store.territories.items()}
    service["audit_fails"] = True

    with pytest.raises(PlatformAuditUnavailable):
        if azione == "create":
            territories_service.create_territory(
                service["session"], kind="province", canonical_key="te",
                label="Teramo", created_fields=["kind"],
            )
        elif azione == "assign":
            libero = store.seed_territory("tortoreto", label="Tortoreto")
            prima_t = {k: dict(v) for k, v in store.territories.items()}
            territories_service.assign_territory(
                service["session"], AGENCY_A,
                territory_id=libero["id"], created_fields=["territory_id"],
            )
        elif azione == "update":
            territories_service.update_assignment(
                service["session"], AGENCY_A, held["id"],
                {"status": ASSIGNMENT_REVOKED},
            )
        else:
            territories_service.transfer_territory(
                service["session"], territory["id"], AGENCY_B
            )

    assert store.assignments == prima, store.assignments
    assert store.territories == prima_t, store.territories
    assert "commit" not in service["order"], service["order"]


def test_g6_the_audit_is_written_before_the_commit(service):
    store = service["store"]
    territory = store.seed_territory()
    territories_service.assign_territory(
        service["session"], AGENCY_A,
        territory_id=territory["id"], created_fields=["territory_id"],
    )
    order = service["order"]
    assert order.index("audit") < order.index("commit"), order


def test_g7_a_commit_failure_after_a_successful_audit_is_compensated(service):
    """Il passo 6: esiste una riga 'success' che descrive qualcosa che non e'
    andato in porto, e la riga compensativa lo dichiara."""
    store = service["store"]
    territory = store.seed_territory()
    service["commit_fails"] = True

    with pytest.raises(RuntimeError):
        territories_service.assign_territory(
            service["session"], AGENCY_A,
            territory_id=territory["id"], created_fields=["territory_id"],
        )
    rows = service["operations"]()
    assert [r["result"] for r in rows] == [RESULT_SUCCESS, RESULT_ERROR], rows
    assert rows[1]["metadata"] == {"commit_failed": True}


def test_g8_the_reads_write_no_operational_audit_row(service):
    store = service["store"]
    territory = store.seed_territory()
    store.seed_assignment(territory["id"], AGENCY_A)

    territories_service.list_territories(limit=10, offset=0)
    territories_service.get_territory(territory["id"])
    territories_service.list_agency_territories(AGENCY_A, limit=10, offset=0)

    assert service["operations"]() == []


# ===========================================================================
# H - HTTP E SICUREZZA
# ===========================================================================

def test_h1_create_a_territory_over_http(client, service):
    response = client.post(TERRITORIES, json={
        "kind": "municipality", "canonical_key": "alba-adriatica",
        "label": "Alba Adriatica",
    })
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["canonical_key"] == "alba-adriatica"
    assert body["active_assignment"] is None


def test_h1_assign_and_read_it_back_over_http(client, service):
    created = client.post(TERRITORIES, json={
        "kind": "municipality", "canonical_key": "tortoreto",
        "label": "Tortoreto",
    }).json()

    assigned = client.post(
        _agency_territories(AGENCY_A), json={"territory_id": created["id"]}
    )
    assert assigned.status_code == 201, assigned.text
    assert assigned.json()["status"] == ASSIGNMENT_ACTIVE

    detail = client.get(f"{TERRITORIES}/{created['id']}").json()
    assert detail["active_assignment"]["agency_id"] == AGENCY_A

    listed = client.get(_agency_territories(AGENCY_A)).json()
    assert [r["territory_canonical_key"] for r in listed] == ["tortoreto"]


def test_h1_transfer_over_http(client, service):
    created = client.post(TERRITORIES, json={
        "kind": "province", "canonical_key": "te", "label": "Teramo",
    }).json()
    client.post(_agency_territories(AGENCY_A), json={"territory_id": created["id"]})

    response = client.post(
        f"{TERRITORIES}/{created['id']}/transfer", json={"agency_id": AGENCY_B}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["assignment"]["agency_id"] == AGENCY_B
    assert body["revoked"]["agency_id"] == AGENCY_A
    assert body["revoked"]["status"] == ASSIGNMENT_REVOKED


def test_h2_a_protected_territory_is_409(client, service):
    created = client.post(TERRITORIES, json={
        "kind": "municipality", "canonical_key": "martinsicuro",
        "label": "Martinsicuro",
    }).json()
    client.post(_agency_territories(AGENCY_A), json={"territory_id": created["id"]})

    response = client.post(
        _agency_territories(AGENCY_B), json={"territory_id": created["id"]}
    )
    assert response.status_code == 409, response.text


def test_h2_a_duplicate_identity_is_409(client, service):
    payload = {"kind": "municipality", "canonical_key": "alba-adriatica",
               "label": "Alba Adriatica"}
    assert client.post(TERRITORIES, json=payload).status_code == 201
    assert client.post(TERRITORIES, json=payload).status_code == 409


def test_h2_transferring_a_free_territory_is_409(client, service):
    created = client.post(TERRITORIES, json={
        "kind": "province", "canonical_key": "ap", "label": "Ascoli Piceno",
    }).json()
    response = client.post(
        f"{TERRITORIES}/{created['id']}/transfer", json={"agency_id": AGENCY_B}
    )
    assert response.status_code == 409, response.text


@pytest.mark.parametrize("url", [
    f"{ROUTER_PREFIX}/territories/999999",
    f"{ROUTER_PREFIX}/agencies/999999/territories",
])
def test_h3_a_missing_object_is_404(client, service, url):
    assert client.get(url).status_code == 404


def test_h3_a_missing_assignment_is_404(client, service):
    response = client.patch(
        f"{_agency_territories(AGENCY_A)}/999999", json={"status": "suspended"}
    )
    assert response.status_code == 404, response.text


def test_h3_an_assignment_of_another_agency_is_404_from_here(client, service):
    created = client.post(TERRITORIES, json={
        "kind": "municipality", "canonical_key": "tortoreto",
        "label": "Tortoreto",
    }).json()
    assignment = client.post(
        _agency_territories(AGENCY_B), json={"territory_id": created["id"]}
    ).json()

    response = client.patch(
        f"{_agency_territories(AGENCY_A)}/{assignment['id']}",
        json={"status": "revoked"},
    )
    assert response.status_code == 404, response.text


@pytest.mark.parametrize("body,perche", [
    ({"kind": "region", "canonical_key": "abruzzo", "label": "Abruzzo"},
     "region non e' un livello"),
    ({"kind": "microzone", "canonical_key": "centro", "label": "Centro"},
     "microzone non e' un livello"),
    ({"kind": "municipality", "canonical_key": "Alba Adriatica",
      "label": "Alba"}, "chiave non canonica"),
    ({"kind": "municipality", "canonical_key": "alba--adriatica",
      "label": "Alba"}, "doppio trattino"),
    ({"kind": "municipality", "canonical_key": "", "label": "Alba"},
     "chiave vuota"),
    ({"kind": "municipality", "canonical_key": "alba", "label": "   "},
     "etichetta vuota"),
    ({"kind": "municipality", "canonical_key": "alba"}, "etichetta mancante"),
    ({"canonical_key": "alba", "label": "Alba"}, "kind mancante"),
    ({"kind": "municipality", "canonical_key": "alba", "label": "Alba",
      "agency_id": 1}, "agency_id non e' un campo di questo schema"),
    ({"kind": "municipality", "canonical_key": "alba", "label": "Alba",
      "id": 5}, "id di provenienza"),
], ids=lambda v: v if isinstance(v, str) else "")
def test_h4_a_malformed_territory_is_422(client, service, body, perche):
    assert client.post(TERRITORIES, json=body).status_code == 422, perche


@pytest.mark.parametrize("body,perche", [
    ({"status": "archived"}, "stato che non appartiene a un'assegnazione"),
    ({"status": ""}, "stato vuoto"),
    ({}, "PATCH vuota"),
    ({"agency_id": 2}, "trasferimento mascherato da PATCH"),
    ({"territory_id": 2}, "spostare la storia di un posto su un altro"),
    ({"status": "suspended", "agency_id": 2}, "stato piu' trasferimento"),
], ids=lambda v: v if isinstance(v, str) else "")
def test_h4_a_malformed_assignment_patch_is_422(client, service, body, perche):
    created = client.post(TERRITORIES, json={
        "kind": "municipality", "canonical_key": "alba-adriatica",
        "label": "Alba Adriatica",
    }).json()
    assignment = client.post(
        _agency_territories(AGENCY_A), json={"territory_id": created["id"]}
    ).json()

    response = client.patch(
        f"{_agency_territories(AGENCY_A)}/{assignment['id']}", json=body
    )
    assert response.status_code == 422, (perche, response.text)


def test_h4_a_refused_patch_changes_nothing(client, service):
    created = client.post(TERRITORIES, json={
        "kind": "municipality", "canonical_key": "alba-adriatica",
        "label": "Alba Adriatica",
    }).json()
    assignment = client.post(
        _agency_territories(AGENCY_A), json={"territory_id": created["id"]}
    ).json()
    prima = len(service["audit"])

    client.patch(
        f"{_agency_territories(AGENCY_A)}/{assignment['id']}",
        json={"agency_id": AGENCY_B},
    )
    assert service["store"].assignments[assignment["id"]]["agency_id"] == AGENCY_A
    assert [
        r for r in service["audit"][prima:]
        if r["action"] != "platform.admission"
    ] == []


def test_h5_an_anonymous_caller_is_401(client, service):
    service["session"] = None
    assert client.get(TERRITORIES).status_code == 401
    assert client.post(TERRITORIES, json={
        "kind": "municipality", "canonical_key": "alba", "label": "Alba",
    }).status_code == 401


def test_h5_a_tenant_operator_is_403(client, service):
    """Un `agency_owner` normale non entra sulla superficie platform.

    E' la regola di P27-1, e P27-5 non la riapre: la si riverifica perche'
    sette route nuove sono sette occasioni per dimenticare il mount.
    """
    service["session"] = _ctx(
        is_platform_admin=False, agency_id=AGENCY_A, role="agency_owner"
    )
    assert client.get(TERRITORIES).status_code == 403
    assert client.get(_agency_territories(AGENCY_A)).status_code == 403
    assert client.post(
        _agency_territories(AGENCY_A), json={"territory_id": 1}
    ).status_code == 403
    assert client.post(
        f"{TERRITORIES}/1/transfer", json={"agency_id": AGENCY_B}
    ).status_code == 403


def test_h6_an_unavailable_audit_is_503_and_not_a_500(client, service):
    created = client.post(TERRITORIES, json={
        "kind": "municipality", "canonical_key": "alba-adriatica",
        "label": "Alba Adriatica",
    }).json()
    service["audit_fails"] = True

    response = client.post(
        _agency_territories(AGENCY_A), json={"territory_id": created["id"]}
    )
    assert response.status_code == 503, response.text
    assert service["store"].assignments == {}


def test_h7_no_error_body_ever_names_a_database_object(client, service):
    """Il nome di un vincolo racconta a un chiamante com'e' fatto lo schema."""
    payload = {"kind": "municipality", "canonical_key": "alba-adriatica",
               "label": "Alba Adriatica"}
    created = client.post(TERRITORIES, json=payload).json()
    client.post(_agency_territories(AGENCY_A), json={"territory_id": created["id"]})

    bodies = [
        client.post(TERRITORIES, json=payload).text,
        client.post(
            _agency_territories(AGENCY_B), json={"territory_id": created["id"]}
        ).text,
        client.post(
            f"{TERRITORIES}/{created['id']}/transfer", json={"agency_id": AGENCY_A}
        ).text,
    ]
    for body in bodies:
        for leak in ("uq_agency_territory_single_active",
                     "network_territories_identity_unq",
                     "psycopg2", "DETAIL", "INSERT", "SELECT"):
            assert leak not in body, (leak, body)


# ===========================================================================
# I - PAGINAZIONE
#
# R6 e' gia' costato una volta: una lista senza limite.
# ===========================================================================

def test_i1_both_lists_declare_a_bounded_limit():
    import main

    spec = main.app.openapi()
    for path in (f"{ROUTER_PREFIX}/territories",
                 f"{ROUTER_PREFIX}/agencies/{{agency_id}}/territories"):
        params = {
            p["name"]: p["schema"]
            for p in spec["paths"][path]["get"]["parameters"]
        }
        assert params["limit"]["maximum"] == TERRITORY_PAGE_MAX, params["limit"]
        assert params["limit"]["minimum"] == 1, params["limit"]
        assert params["limit"]["default"] == TERRITORY_PAGE_DEFAULT, params["limit"]
        assert params["offset"]["minimum"] == 0, params["offset"]


@pytest.mark.parametrize("limit", [0, -1, TERRITORY_PAGE_MAX + 1, 100000])
def test_i1_a_limit_outside_the_bound_is_422(client, service, limit):
    """Il massimo non lo sceglie il chiamante."""
    assert client.get(f"{TERRITORIES}?limit={limit}").status_code == 422


def test_i1_a_negative_offset_is_422(client, service):
    assert client.get(f"{TERRITORIES}?offset=-1").status_code == 422


def test_i2_the_pages_do_not_overlap_and_do_not_skip(client, service):
    for n in range(5):
        client.post(TERRITORIES, json={
            "kind": "municipality", "canonical_key": f"comune-{n}",
            "label": f"Comune {n}",
        })
    prima = client.get(f"{TERRITORIES}?limit=2&offset=0").json()
    seconda = client.get(f"{TERRITORIES}?limit=2&offset=2").json()
    terza = client.get(f"{TERRITORIES}?limit=2&offset=4").json()

    keys = [r["canonical_key"] for r in prima + seconda + terza]
    assert keys == sorted(keys), keys
    assert len(set(keys)) == 5, keys


def test_i3_both_queries_order_deterministically():
    """Un elenco che cambia ordine fra due chiamate e' illeggibile per una
    persona, instabile per un test, e paginabile per nessuno.

    `a.id` in coda non e' decorativo: un'agenzia puo' avere piu' righe storiche
    sullo stesso territorio, e senza di esso due pagine consecutive potrebbero
    ripetere una riga e saltarne un'altra.
    """
    elenco = _sql_of("territories_repository", "list_territories")
    assert "ORDER BY t.kind, t.canonical_key, t.id" in elenco, elenco

    agenzia = _sql_of("territories_repository", "list_agency_assignments")
    assert "ORDER BY t.kind, t.canonical_key, a.id" in agenzia, agenzia


def test_i4_neither_list_query_can_run_without_a_limit():
    for function in ("list_territories", "list_agency_assignments"):
        sql = _sql_of("territories_repository", function)
        assert "LIMIT %s OFFSET %s" in sql, (function, sql)


def test_i4_the_repository_declares_limit_and_offset_without_a_default():
    """Keyword-only e SENZA default.

    Un default qui sarebbe il posto in cui la paginazione smette di essere
    applicata il giorno in cui qualcuno chiama questa funzione senza passarli.
    """
    import inspect

    for function in ("list_territories", "list_agency_assignments"):
        signature = inspect.signature(getattr(territories_repository, function))
        for name in ("limit", "offset"):
            parameter = signature.parameters[name]
            assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, (function, name)
            assert parameter.default is inspect.Parameter.empty, (function, name)


# ===========================================================================
# J - PERIMETRO
#
# P27-5 consegna il dato amministrativo. Non decide a chi va un lead.
# ===========================================================================

P27_5_MODULES = (
    "territories_repository.py",
    "territories_service.py",
)


@pytest.mark.parametrize("modulo", P27_5_MODULES)
def test_j1_no_p27_5_module_touches_the_lead_domain(modulo):
    """LA FRONTIERA CON P27-6, ASSERITA E NON PROMESSA.

    Niente `stime`, niente `leads`, niente `contacts`, niente `properties`,
    nessun import di CORE. Scrivere la regola di routing dentro il posto che
    tiene i dati su cui lavora significherebbe che nessun test di routing
    andrebbe a cercarla li'.
    """
    code = _code_of((ROOT / "platform_admin" / modulo).read_text(encoding="utf-8"))
    for forbidden in (
        "stime", "leads", "contacts", "properties", "lead_stime",
        "zone_valori", "normalizza_comune", "buy_location_criteria",
    ):
        assert forbidden not in code, (modulo, forbidden)


@pytest.mark.parametrize("modulo", P27_5_MODULES)
def test_j1_no_p27_5_module_imports_a_tenant_package(modulo):
    source = (ROOT / "platform_admin" / modulo).read_text(encoding="utf-8")
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])
    for forbidden in ("core", "buy", "match", "property", "flow", "owner",
                      "seller_intelligence", "property_watch", "sale", "main"):
        assert forbidden not in imported, (modulo, forbidden, sorted(imported))


P27_5_ROUTES = (
    "list_territories", "get_territory", "create_territory",
    "list_agency_territories", "assign_territory", "update_assignment",
    "transfer_territory",
)


def _p27_5_router_code() -> str:
    """Il codice delle SOLE sette route di P27-5.

    Non il file intero: `router.py` contiene anche le route di P27-3, che
    parlano legittimamente di `email` perche' creano operatori. Cercare una
    parola nel file intero la troverebbe li' e farebbe fallire un test su una
    fase che non c'entra - l'errore gia' fatto piu' volte in P27, in senso
    inverso.
    """
    tree = ast.parse((ROOT / "platform_admin" / "router.py").read_text(
        encoding="utf-8"
    ))
    return "\n".join(
        _code_of(ast.unparse(node))
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in P27_5_ROUTES
    )


def test_j2_the_seven_routes_are_all_found_in_the_router():
    """Il perno del test seguente: se una route venisse rinominata, l'estrattore
    restituirebbe meno codice e il divieto smetterebbe di sorvegliarla in
    silenzio."""
    code = _p27_5_router_code()
    for name in P27_5_ROUTES:
        assert f"def {name}(" in code, name


@pytest.mark.parametrize("parola", [
    "round_robin", "round-robin", "scoring", "fallback", "sla",
    "capacity", "load_balanc", "priorit", "notif", "whatsapp", "email",
])
def test_j2_nothing_in_p27_5_anticipates_routing(parola):
    """Le cose vietate esplicitamente dal perimetro di P27-5.

    Sul CODICE ESEGUIBILE e non sul file: i commenti nominano P27-6 di
    proposito, per dire dove finisce questa fase.
    """
    sorgenti = {
        modulo: _code_of(
            (ROOT / "platform_admin" / modulo).read_text(encoding="utf-8")
        )
        for modulo in P27_5_MODULES
    }
    sorgenti["router.py (sole route P27-5)"] = _p27_5_router_code()
    for modulo, code in sorgenti.items():
        assert parola not in code.lower(), (modulo, parola)


def test_j3_there_is_no_delete_route_on_the_territory_surface():
    paths = {
        (route.methods and sorted(route.methods)[0], route.path)
        for route in platform_router.routes
    }
    assert not any(method == "DELETE" for method, _ in paths), sorted(paths)


def test_j3_the_repository_issues_no_delete_at_all():
    """NESSUNA DELETE, verificata sul SQL ESEGUIBILE.

    Il docstring del modulo spiega perche' non ce n'e' nessuna, quindi cercare
    la parola nel file intero la troverebbe e proverebbe il contrario.
    """
    source = (ROOT / "platform_admin" / "territories_repository.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    for call in ast.walk(tree):
        if not (isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "execute"):
            continue
        target = call.args[0]
        pieces = [target.value] if isinstance(target, ast.Constant) else [
            v.value for v in getattr(target, "values", [])
            if isinstance(v, ast.Constant) and isinstance(v.value, str)
        ]
        sql = " ".join(pieces).upper()
        assert "DELETE" not in sql, sql
        assert "TRUNCATE" not in sql, sql
        assert "DROP" not in sql, sql


def test_j4_p27_5_opens_no_cross_tenant_path():
    """`core/scope.py` e `operator_auth` non sono toccati da P27-5.

    L'isolamento di tenant e' la decisione D1 di P27-1, e sette route nuove
    sulla superficie platform non sono un motivo per riaprirla.
    """
    scope = _code_of((ROOT / "core" / "scope.py").read_text(encoding="utf-8"))
    assert "territor" not in scope.lower(), "core/scope.py nomina i territori"
    # `platform_admin` compare nei COMMENTI di scope.py dalla 057 - spiegano
    # dove vive la superficie platform. Cio' che conta e' che non ci sia una
    # IMPORTAZIONE: sarebbe la dipendenza da cui D1 potrebbe essere riaperta.
    assert "platform_admin" not in scope, scope


def test_j4_no_territory_query_is_scoped_by_agency_context():
    """Le query di P27-5 non passano da `scoped_predicate`.

    Non e' un buco: sono query di PIATTAFORMA, e un platform admin non ha uno
    scope di agenzia da applicare (D1). Se una di loro lo importasse, la
    tentazione successiva - passare quello scope a un repository di tenant -
    sarebbe a una riga di distanza.
    """
    for modulo in P27_5_MODULES:
        code = _code_of(
            (ROOT / "platform_admin" / modulo).read_text(encoding="utf-8")
        )
        assert "scoped_predicate" not in code, modulo
        assert "scoped_source" not in code, modulo


def test_j5_p27_5_hands_the_public_funnel_over_to_p27_6_intact():
    """IL CONSEGNAMENTO REALE P27-5 -> P27-6, non una ricerca testuale.

    La versione precedente di questo test cercava la stringa 'territor' in
    `main.py` e chiedeva che non ci fosse. Non provava niente: P27-6 instrada
    davvero i lead per territorio e questo test resta verde lo stesso, perche'
    la parola vive in `network_routing` e non in `main.py`. Un test che non puo'
    fallire quando la cosa che sorveglia accade e' peggio di nessun test - dice
    "controllato" e non ha controllato.

    Quel che P27-5 deve davvero garantire e' che la fabbrica congelata da P26-1
    sia ancora li', intatta, e che sia LEI il ripiego di P27-6. Il territorio
    non e' piu' estraneo all'ingresso pubblico - e' il punto di P27-6 - ma il
    comportamento di P26-1 deve restare raggiungibile per intero quando nessun
    alias dichiara il comune.
    """
    import inspect

    from core.scope import system_context_for_public_stima
    from network_routing.service import (
        RoutingDecision,
        resolve_agency_for_public_stima,
        system_context_for_routed_public_stima,
    )

    # 1. La fabbrica P26-1 e' intatta: risolve ancora per SLUG costante, e non
    #    sa niente di territori ne' di alias.
    congelata = inspect.getsource(system_context_for_public_stima)
    assert "resolve_default_agency_id(cur)" in congelata
    assert "territor" not in congelata.lower()
    assert "alias" not in congelata.lower()

    # 2. P27-6 non la sostituisce: la affianca, e le sue due strade finiscono
    #    nello stesso tipo di contesto con la stessa origine.
    assert inspect.isfunction(system_context_for_routed_public_stima)

    # 3. Il ripiego di P27-6 E' il comportamento di P26-1: stesso slug.
    from operator_auth.enums import DEFAULT_AGENCY_SLUG

    instradato = inspect.getsource(system_context_for_routed_public_stima)
    assert "DEFAULT_AGENCY_SLUG" in instradato

    class _CursoreSenzaRete:
        """Nessun alias, nessun territorio: solo l'agenzia di ripiego."""

        def __init__(self):
            self.viste = []

        def execute(self, query, params=None):
            self.viste.append(" ".join(query.split()))
            self._riga = (
                {"id": 1}
                if "FROM agencies WHERE slug" in self.viste[-1]
                else None
            )

        def fetchone(self):
            return self._riga

    cur = _CursoreSenzaRete()
    decisione = resolve_agency_for_public_stima(
        cur, comune="Un Comune Non Dichiarato", fallback_slug=DEFAULT_AGENCY_SLUG
    )
    assert decisione.source == RoutingDecision.FALLBACK
    assert decisione.agency_id == 1
    assert any("network_territory_aliases" in q for q in cur.viste), cur.viste

    # 4. E P27-5 stessa resta fuori dall'ingresso pubblico: `main.py` non
    #    importa il pacchetto amministrativo dei territori.
    codice = _code_of((ROOT / "main.py").read_text(encoding="utf-8"))
    assert "territories_service" not in codice
    assert "aliases_service" not in codice


def test_j6_no_pre_existing_migration_was_modified(runner):
    """Le migration precedenti restano al loro checksum.

    Il ledger e' append-only: una 057 modificata sarebbe una migration gia'
    applicata che cambia sotto i piedi di chi l'ha applicata.
    """
    import subprocess

    changed = subprocess.run(
        ["git", "status", "--porcelain", "--", "migrations/"],
        cwd=ROOT, capture_output=True, text=True,
    ).stdout.splitlines()
    for line in changed:
        stato, path = line[:2].strip(), line[3:]
        # `??` = non tracciato, cioe' NUOVO. Una migration gia' applicata che
        # cambia comparirebbe come ` M` ed e' esattamente quel che questo test
        # vieta. Il perno era sul numero 058; ora il numero deve solo essere
        # >= 058, perche' P27-6 aggiunge la 059 e le fasi seguenti ne
        # aggiungeranno altre - mentre "nessuna PRE-ESISTENTE e' stata
        # modificata" e' la garanzia che non deve mai indebolirsi.
        assert stato == "??", line
        numero = int(Path(path).name.split("_", 1)[0])
        assert numero >= int(VERSION.split("_", 1)[0]), line


# ===========================================================================
# K - L'ELENCO ESAUSTIVO
#
# Appartiene sempre alla fase piu' recente. Un perno sulla dimensione totale
# dentro il file di una fase vecchia si romperebbe a ogni fase che la allarga,
# e diventerebbe rumore invece che sorveglianza.
# ===========================================================================

# k1 (la superficie completa di /api/platform) e k2 (l'elenco esaustivo delle
# mutazioni) SONO STATI SPOSTATI in tests/test_p27_6_lead_routing.py, aggiornati
# con le tre route e le due mutazioni degli alias.
#
# Non e' una rimozione: e' la regola scritta qui sopra applicata. Un elenco
# esaustivo appartiene alla fase piu' recente, altrimenti ogni fase successiva
# lo fa fallire e chi lo ripara finisce per allentarlo - da uguaglianza a
# sottoinsieme - che e' il modo in cui una sorveglianza smette di sorvegliare.

def test_k3_no_p27_5_module_declares_a_tenant_dependency():
    """La superficie Platform non e' una route di tenant con piu' privilegi."""
    for modulo in P27_5_MODULES:
        code = _code_of(
            (ROOT / "platform_admin" / modulo).read_text(encoding="utf-8")
        )
        for dependency in ("require_operator", "require_authenticated_operator",
                           "legacy_basic_agency_context",
                           "require_owner_admin_context"):
            assert dependency not in code, (modulo, dependency)


def test_k4_every_territory_action_is_in_the_platform_namespace():
    for action in (ACTION_TERRITORY_CREATE, ACTION_TERRITORY_ASSIGNMENT_CREATE,
                   ACTION_TERRITORY_ASSIGNMENT_UPDATE, ACTION_TERRITORY_TRANSFER):
        assert action.startswith("platform."), action


# ===========================================================================
# L - `revoked` E' TERMINALE
#
#     active    -> suspended    si'
#     suspended -> active       si'
#     active    -> revoked      si'
#     suspended -> revoked      si'
#     revoked   -> active       NO
#     revoked   -> suspended    NO
#
# Una riga revocata descrive un periodo di presidio FINITO. Riportarla in vita
# riscriverebbe quel periodo invece di aggiungerne uno nuovo, e quando il
# presidio precedente sia cominciato e finito resterebbe ricostruibile solo da
# `platform_audit_log` - un registro fatto per raccontare gli ATTI, non per
# essere l'unica fonte dello STATO.
# ===========================================================================

@pytest.mark.parametrize("verso", [ASSIGNMENT_ACTIVE, ASSIGNMENT_SUSPENDED])
def test_l1_a_revoked_assignment_cannot_be_brought_back(service, verso):
    """revoked -> active e revoked -> suspended: 409."""
    store = service["store"]
    territory = store.seed_territory()
    revocata = store.seed_assignment(
        territory["id"], AGENCY_A, status=ASSIGNMENT_REVOKED
    )
    with pytest.raises(PlatformConflict) as exc:
        territories_service.update_assignment(
            service["session"], AGENCY_A, revocata["id"], {"status": verso}
        )
    assert "terminale" in str(exc.value)


@pytest.mark.parametrize("verso", [ASSIGNMENT_ACTIVE, ASSIGNMENT_SUSPENDED])
def test_l2_the_refused_transition_writes_nothing_at_all(service, verso):
    """Nessuna scrittura e NESSUN AUDIT OPERATIVO DI SUCCESSO.

    Il rifiuto arriva prima della UPDATE, quindi `store.writes` resta vuoto:
    non e' una scrittura annullata dopo, e' una scrittura mai tentata.
    """
    store = service["store"]
    territory = store.seed_territory()
    revocata = store.seed_assignment(
        territory["id"], AGENCY_A, status=ASSIGNMENT_REVOKED
    )
    prima = dict(store.assignments[revocata["id"]])
    store.writes.clear()

    with pytest.raises(PlatformConflict):
        territories_service.update_assignment(
            service["session"], AGENCY_A, revocata["id"], {"status": verso}
        )

    assert store.writes == [], store.writes
    assert store.assignments[revocata["id"]] == prima
    assert store.assignments[revocata["id"]]["status"] == ASSIGNMENT_REVOKED
    assert service["operations"]() == []


def test_l3_reaffirming_revoked_on_a_revoked_row_is_not_refused(service):
    """`revoked -> revoked` non riapre niente.

    Rifiutarlo significherebbe che riaffermare uno stato terminale e' un
    errore, e non lo e'.
    """
    store = service["store"]
    territory = store.seed_territory()
    revocata = store.seed_assignment(
        territory["id"], AGENCY_A, status=ASSIGNMENT_REVOKED
    )
    updated = territories_service.update_assignment(
        service["session"], AGENCY_A, revocata["id"],
        {"status": ASSIGNMENT_REVOKED},
    )
    assert updated["status"] == ASSIGNMENT_REVOKED


@pytest.mark.parametrize("verso_agenzia,etichetta", [
    (AGENCY_A, "la STESSA agenzia di prima"),
    (AGENCY_B, "un'agenzia diversa"),
])
def test_l4_a_territory_whose_only_assignment_is_revoked_can_be_assigned_again(
    service, verso_agenzia, etichetta
):
    """LA STRADA CHE RESTA APERTA, ed e' quella giusta.

    La vecchia riga resta revocata, la nuova nasce attiva, e i due periodi
    restano due righe distinte leggibili senza aprire il registro. Vale anche
    quando l'agenzia coincide, perche' `uq_agency_territory_single_active`
    guarda il solo `territory_id`.
    """
    store = service["store"]
    territory = store.seed_territory()
    vecchia = store.seed_assignment(
        territory["id"], AGENCY_A, status=ASSIGNMENT_REVOKED
    )

    nuova = territories_service.assign_territory(
        service["session"], verso_agenzia,
        territory_id=territory["id"], created_fields=["territory_id"],
    )

    assert nuova["id"] != vecchia["id"], etichetta
    assert nuova["status"] == ASSIGNMENT_ACTIVE
    assert nuova["agency_id"] == verso_agenzia
    assert store.assignments[vecchia["id"]]["status"] == ASSIGNMENT_REVOKED
    assert store.assignments[vecchia["id"]]["agency_id"] == AGENCY_A
    assert len(store.assignments) == 2
    assert len(store.active_for(territory["id"])) == 1


def test_l5_the_transfer_still_produces_a_revoked_row_and_a_new_active_one(service):
    """Il trasferimento non passa dal guardrail e non deve.

    Revoca una riga ATTIVA, che e' una transizione ammessa, e ne crea una
    nuova: e' gia' la forma che questa regola impone a tutti gli altri
    percorsi.
    """
    store = service["store"]
    territory = store.seed_territory()
    vecchia = store.seed_assignment(territory["id"], AGENCY_A)
    store.writes.clear()

    result = territories_service.transfer_territory(
        service["session"], territory["id"], AGENCY_B
    )
    assert store.writes == [
        f"update-assignment:{ASSIGNMENT_REVOKED}",
        "create-assignment",
    ], store.writes
    assert result["revoked"]["id"] == vecchia["id"]
    assert result["revoked"]["status"] == ASSIGNMENT_REVOKED
    assert result["assignment"]["id"] != vecchia["id"]
    assert result["assignment"]["status"] == ASSIGNMENT_ACTIVE
    assert len(store.active_for(territory["id"])) == 1


def test_l5_a_transferred_away_agency_cannot_reactivate_its_old_row(service):
    """Il caso che la regola esiste per chiudere.

    Dopo un trasferimento la vecchia assegnazione e' revocata. Se si potesse
    riattivare, un affiliato tornerebbe sul territorio riscrivendo la riga che
    documenta di averlo perso - e il periodo dell'altro sparirebbe dallo stato.
    """
    store = service["store"]
    territory = store.seed_territory()
    vecchia = store.seed_assignment(territory["id"], AGENCY_A)
    territories_service.transfer_territory(
        service["session"], territory["id"], AGENCY_B
    )

    with pytest.raises(PlatformConflict) as exc:
        territories_service.update_assignment(
            service["session"], AGENCY_A, vecchia["id"],
            {"status": ASSIGNMENT_ACTIVE},
        )
    assert "terminale" in str(exc.value)
    assert store.active_for(territory["id"])[0]["agency_id"] == AGENCY_B


def test_l6_the_audit_failure_still_rolls_the_whole_transfer_back(service):
    """La regola nuova non ha spostato quella vecchia."""
    store = service["store"]
    territory = store.seed_territory()
    vecchia = store.seed_assignment(territory["id"], AGENCY_A)
    service["audit_fails"] = True

    with pytest.raises(PlatformAuditUnavailable):
        territories_service.transfer_territory(
            service["session"], territory["id"], AGENCY_B
        )
    assert store.assignments[vecchia["id"]]["status"] == ASSIGNMENT_ACTIVE
    assert len(store.assignments) == 1
    assert "commit" not in service["order"], service["order"]


def test_l6_the_audit_failure_rolls_back_a_reassignment_after_a_revoke(service):
    """E vale anche sulla strada nuova: assegnare di nuovo dopo una revoca."""
    store = service["store"]
    territory = store.seed_territory()
    vecchia = store.seed_assignment(
        territory["id"], AGENCY_A, status=ASSIGNMENT_REVOKED
    )
    service["audit_fails"] = True

    with pytest.raises(PlatformAuditUnavailable):
        territories_service.assign_territory(
            service["session"], AGENCY_A,
            territory_id=territory["id"], created_fields=["territory_id"],
        )
    assert list(store.assignments) == [vecchia["id"]]
    assert store.assignments[vecchia["id"]]["status"] == ASSIGNMENT_REVOKED


# --- HTTP -------------------------------------------------------------------

@pytest.mark.parametrize("verso", ["active", "suspended"])
def test_l7_bringing_a_revoked_assignment_back_over_http_is_409(
    client, service, verso
):
    created = client.post(TERRITORIES, json={
        "kind": "municipality", "canonical_key": "alba-adriatica",
        "label": "Alba Adriatica",
    }).json()
    assignment = client.post(
        _agency_territories(AGENCY_A), json={"territory_id": created["id"]}
    ).json()
    url = f"{_agency_territories(AGENCY_A)}/{assignment['id']}"
    assert client.patch(url, json={"status": "revoked"}).status_code == 200

    prima = len(service["audit"])
    response = client.patch(url, json={"status": verso})

    assert response.status_code == 409, response.text
    assert service["store"].assignments[assignment["id"]]["status"] == "revoked"
    assert [
        r for r in service["audit"][prima:]
        if r["action"] != "platform.admission"
    ] == []


def test_l7_the_409_body_names_no_database_object(client, service):
    created = client.post(TERRITORIES, json={
        "kind": "province", "canonical_key": "te", "label": "Teramo",
    }).json()
    assignment = client.post(
        _agency_territories(AGENCY_A), json={"territory_id": created["id"]}
    ).json()
    url = f"{_agency_territories(AGENCY_A)}/{assignment['id']}"
    client.patch(url, json={"status": "revoked"})

    body = client.patch(url, json={"status": "active"}).text
    for leak in ("uq_agency_territory_single_active", "psycopg2", "UPDATE",
                 "agency_territory_assignments"):
        assert leak not in body, (leak, body)


def test_l8_reassignment_over_http_creates_a_new_row(client, service):
    """Il giro completo: revoca, riassegna, e i due periodi restano due righe."""
    created = client.post(TERRITORIES, json={
        "kind": "municipality", "canonical_key": "tortoreto",
        "label": "Tortoreto",
    }).json()
    prima = client.post(
        _agency_territories(AGENCY_A), json={"territory_id": created["id"]}
    ).json()
    client.patch(
        f"{_agency_territories(AGENCY_A)}/{prima['id']}", json={"status": "revoked"}
    )

    seconda = client.post(
        _agency_territories(AGENCY_A), json={"territory_id": created["id"]}
    )
    assert seconda.status_code == 201, seconda.text
    assert seconda.json()["id"] != prima["id"]
    assert seconda.json()["status"] == "active"

    righe = client.get(_agency_territories(AGENCY_A)).json()
    assert sorted(r["status"] for r in righe) == ["active", "revoked"], righe


def test_l9_the_service_refuses_the_two_transitions_before_any_write():
    """STRUTTURALE: il guardrail sta PRIMA della UPDATE, nel corpo della
    funzione.

    Non e' la stessa cosa di rifiutare dopo aver scritto e annullare: un
    controllo che viene dopo dipende dal rollback per non lasciare traccia, e
    il rollback e' proprio cio' che una riga di audit gia' scritta non
    annullerebbe.
    """
    source = (ROOT / "platform_admin" / "territories_service.py").read_text(
        encoding="utf-8"
    )
    fn = next(
        n for n in ast.walk(ast.parse(source))
        if isinstance(n, ast.FunctionDef) and n.name == "update_assignment"
    )
    chiamate = [
        node.func.id if isinstance(node.func, ast.Name) else node.func.attr
        for node in ast.walk(fn) if isinstance(node, ast.Call)
    ]
    assert "_guard_revoked_is_final" in chiamate, chiamate
    assert chiamate.index("_guard_revoked_is_final") < \
        chiamate.index("update_assignment"), chiamate
    assert chiamate.index("_guard_revoked_is_final") < \
        chiamate.index("audit_then_commit"), chiamate
