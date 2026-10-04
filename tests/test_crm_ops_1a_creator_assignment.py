"""CRM-OPS-1A - autoassegnazione alla creazione per il ruolo `agent`.

Il difetto (TEST, contact 158): `core/repository.py` scriveva ogni contatto e
ogni lead con `assigned_agent_id = NULL`, chiunque lo creasse; il predicato di
`core/scope.py` nascondeva poi quella riga all'`agent` che l'aveva appena
creata. La regola di CRM-OPS-1A: se il creatore e' un `agent`,
`assigned_agent_id = ctx.user_id`, deciso lato server da
`core.scope.creator_assignment`, MAI letto dal payload. Per tutti gli altri
contesti (owner, admin, platform admin, Basic legacy, contesti di sistema del
sito e del booking) nasce NULL come prima.

Quattro strati, dal piu' piccolo al piu' reale:

    A  la regola in core/scope.py, per ogni tipo di contesto, e il suo accordo
       con `scoped_predicate` (una riga creata e' sempre una riga visibile)
    B  core/repository.py su cursore registrante: cosa va davvero in INSERT,
       e che il payload resta rifiutato
    C  la matrice HTTP con la fixture ostile di P26 (due agenzie, router vero,
       service vero, gate dei permessi vero): creatore 2xx, collega 404,
       altra agenzia 404, owner/admin 200, 422 sull'assegnazione nel corpo
    D  PostgreSQL vero (opt-in `P29_TEST_DSN`, database usa-e-getta, migration
       VERE 027/001/028/029/030): la FK composita di 030 e' viva e la riga
       la soddisfa; nessun ampliamento della visibilita'

Nessuna migration: 028 ha gia' la colonna, 030 la FK e l'indice.
"""
from __future__ import annotations

import ast
import inspect
import os
import uuid
from pathlib import Path

import pytest

from core import repository
from core.exceptions import NotFoundError
from core.scope import (
    AGENT_ASSIGNABLE,
    SCOPED_TABLES,
    ProgrammingError,
    creator_assignment,
    scoped_predicate,
)
from operator_auth.context import SYSTEM_CONTEXT_ORIGINS, OperatorContext, SystemAgencyContext

# La fixture ostile e il cursore registrante di P26-1 si RIUSANO, non si
# ricopiano: una copia divergerebbe dalla cosa che certifica.
from tests.test_p26_1_core_isolation import (  # noqa: F401 - fixture `hostile`, `cur`
    AGENCY,
    AGENCY_A,
    AGENCY_B,
    AGENT_A,
    AGENT_A2,
    AGENT_USER,
    CONTACT_A_UNASSIGNED,
    CONTACT_B,
    OWNER_A,
    RecordingCursor,
    admin_ctx,
    agent_ctx,
    bound_platform_admin_ctx,
    cur,
    hostile,
    install,
    legacy_basic_ctx,
    owner_ctx,
    system_ctx,
)

ROOT = Path(__file__).resolve().parents[1]
SCOPE_PATH = ROOT / "core" / "scope.py"
REPOSITORY_PATH = ROOT / "core" / "repository.py"
BOOKING_PATH = ROOT / "appointments" / "service.py"

ALL_TABLES = sorted(SCOPED_TABLES)

NON_AGENT_CONTEXTS = {
    "agency_owner": owner_ctx,
    "agency_admin": admin_ctx,
    "bound_platform_admin": bound_platform_admin_ctx,
    "legacy_basic": legacy_basic_ctx,
    "system_public_stima": system_ctx,
}


# ===========================================================================
# A - la regola
# ===========================================================================

@pytest.mark.parametrize("table", sorted(AGENT_ASSIGNABLE))
def test_a1_an_agent_creates_rows_assigned_to_itself(table):
    assert creator_assignment(agent_ctx(), table) == AGENT_USER


@pytest.mark.parametrize("table", sorted(SCOPED_TABLES - AGENT_ASSIGNABLE))
def test_a1_activities_and_tasks_are_never_assigned_at_birth(table):
    """Non hanno `assigned_agent_id` (028, AGENT_ASSIGNABLE): nemmeno per un agent."""
    assert creator_assignment(agent_ctx(), table) is None


@pytest.mark.parametrize("context_name", sorted(NON_AGENT_CONTEXTS))
@pytest.mark.parametrize("table", ALL_TABLES)
def test_a2_every_other_context_creates_unassigned_rows(context_name, table):
    """Owner, admin, platform admin legato, Basic legacy, sistema: NULL, come prima."""
    assert creator_assignment(NON_AGENT_CONTEXTS[context_name](), table) is None


@pytest.mark.parametrize("origin", SYSTEM_CONTEXT_ORIGINS)
@pytest.mark.parametrize("table", ALL_TABLES)
def test_a2_no_system_origin_assigns_anybody(origin, table):
    """Bridge del sito, booking pubblico, dispatcher, promemoria, magic link,
    disiscrizione: nessuno ha un operatore dietro, nessuno assegna."""
    ctx = SystemAgencyContext(agency_id=AGENCY, origin=origin)
    assert creator_assignment(ctx, table) is None


def test_a3_an_unscoped_table_is_a_programming_error():
    with pytest.raises(ProgrammingError):
        creator_assignment(agent_ctx(), "stime")


def test_a3_the_platform_flag_never_assigns():
    """Un platform admin con ruolo `agent` in un'agenzia (D4) e' un agent li':
    riga assegnata a lui. Il flag da solo non assegna e non allarga."""
    ctx = OperatorContext(user_id=AGENT_USER, agency_id=AGENCY, role="agent",
                          is_platform_admin=True, session_id=1,
                          auth_channel="operator_session")
    assert creator_assignment(ctx, "contacts") == AGENT_USER
    flag_only = OperatorContext(user_id=AGENT_USER, agency_id=AGENCY, role=None,
                                is_platform_admin=True, session_id=1,
                                auth_channel="operator_session")
    assert creator_assignment(flag_only, "contacts") is None


def _row_is_admitted(ctx, table: str, row: dict) -> bool:
    """La riga passerebbe il predicato VERO di `scoped_predicate`?"""
    predicate, params = scoped_predicate(ctx, table, "x")
    assert params[0] == ctx.agency_id
    if row["agency_id"] != params[0]:
        return False
    if "assigned_agent_id" in predicate:
        assert len(params) == 2
        return row["assigned_agent_id"] == params[1]
    return True


@pytest.mark.parametrize("context_name", ["agent", *sorted(NON_AGENT_CONTEXTS)])
@pytest.mark.parametrize("table", sorted(AGENT_ASSIGNABLE))
def test_a4_a_row_born_from_a_context_is_admitted_by_that_contexts_predicate(
    context_name, table
):
    """La proprieta' che chiude il difetto: chi crea, vede. Per costruzione."""
    ctx = agent_ctx() if context_name == "agent" else NON_AGENT_CONTEXTS[context_name]()
    row = {"agency_id": ctx.agency_id,
           "assigned_agent_id": creator_assignment(ctx, table)}
    assert _row_is_admitted(ctx, table, row), (context_name, table, row)


def test_a4_but_a_colleague_agent_is_still_excluded():
    """Nessun ampliamento: la riga di agent 7 non e' ammessa dal predicato di agent 8."""
    creator = agent_ctx()
    colleague = OperatorContext(user_id=AGENT_USER + 1, agency_id=AGENCY, role="agent",
                                is_platform_admin=False, session_id=2,
                                auth_channel="operator_session")
    row = {"agency_id": AGENCY, "assigned_agent_id": creator_assignment(creator, "contacts")}
    assert _row_is_admitted(creator, "contacts", row)
    assert not _row_is_admitted(colleague, "contacts", row)


def test_a4_the_agent_predicate_itself_is_unchanged():
    """`scoped_predicate` per un agent: stesso testo, stessi parametri di P26/P27."""
    predicate, params = scoped_predicate(agent_ctx(), "contacts", "c")
    assert predicate == "c.agency_id = %s AND c.assigned_agent_id = %s"
    assert params == [AGENCY, AGENT_USER]
    predicate, params = scoped_predicate(owner_ctx(), "contacts", "c")
    assert predicate == "c.agency_id = %s"
    assert params == [AGENCY]


def _calls_in(function_node: ast.FunctionDef) -> set[str]:
    return {
        node.func.id
        for node in ast.walk(function_node)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def test_a5_predicate_and_creator_assignment_share_one_condition():
    """Una sola copia della regola del ruolo: entrambe chiedono a
    `_narrowed_to_own_records`, e il letterale `agent` compare una volta sola
    nel codice eseguibile di core/scope.py."""
    tree = ast.parse(SCOPE_PATH.read_text(encoding="utf-8"))
    functions = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "_narrowed_to_own_records" in _calls_in(functions["scoped_predicate"])
    assert "_narrowed_to_own_records" in _calls_in(functions["creator_assignment"])

    docstrings = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)) and body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            docstrings.add(id(body[0].value))
    agent_literals = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and node.value == "agent"
        and id(node) not in docstrings
    ]
    assert len(agent_literals) == 1, "the agent rule must be stated exactly once"


# ===========================================================================
# B - il repository, su cursore registrante
# ===========================================================================

def test_b1_an_agent_contact_is_inserted_assigned_to_the_agent(cur):
    repository.create_contact(agent_ctx(), {"display_name": "Mario"})
    assert len(cur.calls) == 1, cur.calls
    insert = cur.calls[0]
    assert "INSERT INTO contacts" in insert.sql
    assert "assigned_agent_id" in insert.sql
    assert insert.params["agency_id"] == AGENCY
    assert insert.params["created_by_user_id"] == AGENT_USER
    assert insert.params["assigned_agent_id"] == AGENT_USER


def test_b1_an_agent_lead_is_inserted_assigned_to_the_agent(cur):
    repository.create_lead(agent_ctx(), {"contact_id": 1})
    insert = [c for c in cur.calls if "INSERT INTO leads" in c.sql][0]
    assert "assigned_agent_id" in insert.sql
    assert insert.params["agency_id"] == AGENCY
    assert insert.params["created_by_user_id"] == AGENT_USER
    assert insert.params["assigned_agent_id"] == AGENT_USER


def test_b2_the_lead_still_checks_its_contact_under_the_agents_own_scope(cur):
    """Il controllo sul contatto collegato resta e resta ristretto: un agent
    apre un lead solo su un contatto gia' assegnato a lui."""
    repository.create_lead(agent_ctx(), {"contact_id": 1})
    check, insert = cur.calls[0], cur.calls[1]
    assert "SELECT 1 FROM contacts" in check.sql and "INSERT" not in check.sql
    assert "assigned_agent_id = %s" in check.sql, check
    assert check.bound[:2] == [AGENCY, AGENT_USER], check
    assert "INSERT INTO leads" in insert.sql


def test_b2_a_lead_on_an_invisible_contact_inserts_nothing(monkeypatch):
    recorder = install(monkeypatch, RecordingCursor(rows=[None]))
    with pytest.raises(NotFoundError):
        repository.create_lead(agent_ctx(), {"contact_id": 1})
    assert not any("INSERT" in c.sql for c in recorder.calls), recorder.calls


@pytest.mark.parametrize("context_name", sorted(NON_AGENT_CONTEXTS))
def test_b3_every_other_context_inserts_an_unassigned_contact_and_lead(context_name, cur):
    ctx = NON_AGENT_CONTEXTS[context_name]()
    repository.create_contact(ctx, {"display_name": "x"})
    contact_insert = cur.calls[-1]
    assert "INSERT INTO contacts" in contact_insert.sql
    assert contact_insert.params["assigned_agent_id"] is None
    assert contact_insert.params["agency_id"] == AGENCY

    repository.create_lead(ctx, {"contact_id": 1})
    lead_insert = cur.calls[-1]
    assert "INSERT INTO leads" in lead_insert.sql
    assert lead_insert.params["assigned_agent_id"] is None
    assert lead_insert.params["agency_id"] == AGENCY


@pytest.mark.parametrize(
    "call",
    [
        lambda ctx: repository.create_contact(ctx, {"display_name": "x", "assigned_agent_id": 99}),
        lambda ctx: repository.create_lead(ctx, {"contact_id": 1, "assigned_agent_id": 99}),
        lambda ctx: repository.update_contact(ctx, 1, {"assigned_agent_id": 99}),
        lambda ctx: repository.update_lead(ctx, 1, {"assigned_agent_id": 99}),
        lambda ctx: repository.create_contact(ctx, {"display_name": "x", "assigned_agent_id": None}),
    ],
    ids=["create_contact", "create_lead", "update_contact", "update_lead", "create_contact_null"],
)
@pytest.mark.parametrize("context_name", ["agent", "agency_owner"])
def test_b4_an_assignment_in_the_payload_is_refused_before_any_statement(
    call, context_name, cur
):
    """Anche a NULL, anche dall'owner: l'assegnazione non viaggia nel payload.
    Il repository la rifiuta come rifiuta `agency_id` (SERVER_OWNED_COLUMNS)."""
    ctx = agent_ctx() if context_name == "agent" else owner_ctx()
    with pytest.raises(ProgrammingError):
        call(ctx)
    assert cur.calls == [], cur.calls


def test_b4_assigned_agent_id_is_a_server_owned_column():
    assert "assigned_agent_id" in repository.SERVER_OWNED_COLUMNS
    assert "agency_id" in repository.SERVER_OWNED_COLUMNS
    assert "created_by_user_id" in repository.SERVER_OWNED_COLUMNS


def test_b5_the_public_stima_bridge_and_the_public_booking_are_untouched():
    """Le due INSERT di sistema non nominano `assigned_agent_id`: il contatto
    del sito e quello del booking nascono non assegnati, come prima."""
    bridge = inspect.getsource(repository.bridge_public_stima)
    assert "INSERT INTO contacts(" in bridge
    assert "assigned_agent_id" not in bridge
    assert "creator_assignment" not in bridge

    booking = BOOKING_PATH.read_text(encoding="utf-8")
    assert "INSERT INTO contacts(" in booking
    assert "assigned_agent_id" not in booking
    assert "creator_assignment" not in booking


def test_b6_the_repository_binds_assigned_agent_id_only_from_the_scope():
    """Statico. Ogni scrittura di `assigned_agent_id` in core/repository.py e'
    `creator_assignment(ctx, <tabella>)` dentro create_contact / create_lead,
    oppure il parametro esplicito di `_set_assignment`; nessuna legge il
    payload (estende la regola A5 di P26 alla nuova colonna)."""
    tree = ast.parse(REPOSITORY_PATH.read_text(encoding="utf-8"))
    writers = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef):
            continue
        parameters = {a.arg for a in fn.args.args + fn.args.kwonlyargs}
        for node in ast.walk(fn):
            if (isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant)
                    and node.slice.value == "assigned_agent_id"
                    and isinstance(node.value, ast.Name)):
                assert node.value.id not in parameters, (
                    f"{fn.name}: reads assigned_agent_id from the payload")
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "get" and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value == "assigned_agent_id"):
                assert not (isinstance(node.func.value, ast.Name)
                            and node.func.value.id in parameters), fn.name
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (isinstance(target, ast.Subscript)
                            and isinstance(target.slice, ast.Constant)
                            and target.slice.value == "assigned_agent_id"):
                        value = node.value
                        assert (isinstance(value, ast.Call)
                                and isinstance(value.func, ast.Name)
                                and value.func.id == "creator_assignment"
                                and ast.unparse(value.args[0]) == "ctx"), (
                            f"{fn.name}: assigned_agent_id set from {ast.unparse(value)}")
                        writers[fn.name] = ast.unparse(value)
    # SENTINELLA AGGIORNATA DA VENDITORI-1: `create_lead` apre la transazione e
    # delega a `create_lead_with_cursor(ctx, cur, data)`, lo STESSO INSERT sul
    # cursore del chiamante (l'attivazione «Vende» crea lead e collegamento
    # all'immobile in una transazione). L'assegnazione resta una sola, dallo
    # scope: il nome della funzione che la scrive e' cambiato, la regola no.
    assert writers == {
        "create_contact": "creator_assignment(ctx, 'contacts')",
        "create_lead_with_cursor": "creator_assignment(ctx, 'leads')",
    }, writers


# ===========================================================================
# C - la matrice HTTP sulla fixture ostile di P26 (due agenzie)
# ===========================================================================

def _create_contact_as(hostile, operator, name="Nuovo Cliente"):
    response = hostile.as_operator(operator).post(
        "/api/core/contacts", json={"display_name": name, "email": f"{uuid.uuid4().hex}@example.test"}
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_c1_an_agent_sees_and_edits_the_contact_it_just_created(hostile):
    created = _create_contact_as(hostile, "agent_a")
    assert created["assigned_agent_id"] == AGENT_A
    path = f"/api/core/contacts/{created['id']}"

    assert hostile.as_operator("agent_a").get(path).status_code == 200
    updated = hostile.as_operator("agent_a").patch(path, json={"notes": "primo contatto"})
    assert updated.status_code == 200, updated.text
    assert hostile.store.raw("contacts", created["id"])["notes"] == "primo contatto"
    listed = hostile.as_operator("agent_a").get("/api/core/contacts").json()["items"]
    assert created["id"] in {row["id"] for row in listed}


def test_c1_the_colleague_agent_and_the_other_agency_stay_excluded(hostile):
    created = _create_contact_as(hostile, "agent_a")
    path = f"/api/core/contacts/{created['id']}"
    for outsider in ("agent_a2", "owner_b"):
        assert hostile.as_operator(outsider).get(path).status_code == 404, outsider
        assert hostile.as_operator(outsider).patch(path, json={"notes": "x"}).status_code == 404
        listed = hostile.as_operator(outsider).get("/api/core/contacts").json()["items"]
        assert created["id"] not in {row["id"] for row in listed}, outsider
    assert hostile.store.raw("contacts", created["id"])["notes"] is None


def test_c1_owner_and_admin_keep_full_access_to_the_agents_contact(hostile):
    created = _create_contact_as(hostile, "agent_a")
    path = f"/api/core/contacts/{created['id']}"
    for boss in ("owner_a", "admin_a"):
        assert hostile.as_operator(boss).get(path).status_code == 200, boss
        assert hostile.as_operator(boss).patch(path, json={"notes": boss}).status_code == 200
    # ...and may still reassign it, exactly as before.
    moved = hostile.as_operator("owner_a").patch(
        f"{path}/assignment", json={"assigned_agent_id": AGENT_A2})
    assert moved.status_code == 200, moved.text
    assert hostile.as_operator("agent_a2").get(path).status_code == 200
    assert hostile.as_operator("agent_a").get(path).status_code == 404


def test_c2_an_agent_lead_on_its_own_contact_follows_the_same_matrix(hostile):
    contact = _create_contact_as(hostile, "agent_a")
    response = hostile.as_operator("agent_a").post(
        "/api/core/leads", json={"contact_id": contact["id"], "pipeline": "sell"})
    assert response.status_code == 201, response.text
    lead = response.json()
    assert lead["assigned_agent_id"] == AGENT_A
    path = f"/api/core/leads/{lead['id']}"
    assert hostile.as_operator("agent_a").get(path).status_code == 200
    assert hostile.as_operator("agent_a").patch(path, json={"stage": "contacted"}).status_code == 200
    assert hostile.as_operator("agent_a2").get(path).status_code == 404
    assert hostile.as_operator("owner_b").get(path).status_code == 404
    assert hostile.as_operator("owner_a").get(path).status_code == 200
    assert hostile.as_operator("admin_a").get(path).status_code == 200


@pytest.mark.parametrize("contact_id", [CONTACT_A_UNASSIGNED, CONTACT_B])
def test_c2_an_agent_cannot_open_a_lead_on_a_contact_it_does_not_see(hostile, contact_id):
    """Il controllo sul contatto collegato: non assegnato a lui, o di un'altra
    agenzia -> 404, e nessun lead nasce."""
    before = len(hostile.store.rows["leads"])
    response = hostile.as_operator("agent_a").post("/api/core/leads", json={"contact_id": contact_id})
    assert response.status_code == 404, response.text
    assert len(hostile.store.rows["leads"]) == before


def test_c3_an_owner_created_contact_is_still_unassigned_and_invisible_to_agents(hostile):
    """Nessun ampliamento della visibilita': owner/admin creano come prima."""
    for boss in ("owner_a", "admin_a"):
        created = _create_contact_as(hostile, boss)
        assert created["assigned_agent_id"] is None, boss
        path = f"/api/core/contacts/{created['id']}"
        assert hostile.as_operator("agent_a").get(path).status_code == 404
        assert hostile.as_operator("agent_a2").get(path).status_code == 404
        assert hostile.as_operator(boss).get(path).status_code == 200


@pytest.mark.parametrize("operator", ["agent_a", "owner_a"])
@pytest.mark.parametrize("value", [AGENT_A, AGENT_A2, OWNER_A, None])
def test_c4_an_assignment_in_the_create_body_is_422_for_contacts_and_leads(
    hostile, operator, value
):
    contacts = hostile.as_operator(operator).post(
        "/api/core/contacts", json={"display_name": "X", "assigned_agent_id": value})
    assert contacts.status_code == 422, contacts.text
    leads = hostile.as_operator(operator).post(
        "/api/core/leads", json={"contact_id": CONTACT_A_UNASSIGNED, "assigned_agent_id": value})
    assert leads.status_code == 422, leads.text


def test_c4_an_assignment_in_an_update_body_is_still_422(hostile):
    created = _create_contact_as(hostile, "agent_a")
    response = hostile.as_operator("agent_a").patch(
        f"/api/core/contacts/{created['id']}", json={"assigned_agent_id": AGENT_A2})
    assert response.status_code == 422, response.text
    assert hostile.store.raw("contacts", created["id"])["assigned_agent_id"] == AGENT_A


def test_c5_an_agent_still_may_not_reassign_even_its_own_contact(hostile):
    created = _create_contact_as(hostile, "agent_a")
    response = hostile.as_operator("agent_a").patch(
        f"/api/core/contacts/{created['id']}/assignment", json={"assigned_agent_id": AGENT_A2})
    assert response.status_code == 403, response.text
    assert hostile.store.raw("contacts", created["id"])["assigned_agent_id"] == AGENT_A


def test_c6_an_unbound_platform_admin_is_still_refused_a_create(hostile):
    response = hostile.as_operator("platform_admin").post(
        "/api/core/contacts", json={"display_name": "X"})
    assert response.status_code == 403, response.text


# ===========================================================================
# D - PostgreSQL vero: migration VERE, FK composita di 030 viva
# ===========================================================================

DSN = os.getenv("P29_TEST_DSN")
MIGRAZIONI = ROOT / "migrations"
CATENA = ("027_p26_agency_identity", "001_core_contacts_leads",
          "028_p26_core_agency_columns", "029_p26_core_agency_backfill",
          "030_p26_core_agency_enforce")

pg = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare CRM-OPS-1A")


def _dsn_per(nome: str) -> str:
    if "?" in DSN:
        base, query = DSN.split("?", 1)
        return base.rsplit("/", 1)[0] + "/" + nome + "?" + query
    return DSN.rsplit("/", 1)[0] + "/" + nome


@pytest.fixture(scope="module")
def db():
    psycopg2 = pytest.importorskip("psycopg2")
    nome = f"crmops1a_probe_{os.getpid()}_{uuid.uuid4().hex[:6]}"
    servizio = psycopg2.connect(DSN)
    servizio.autocommit = True
    with servizio.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{nome}"')
    dsn = _dsn_per(nome)
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            # 001 referenzia `stime(id)` (lead_stime, activities, tasks): basta
            # la chiave; il resto della tabella del sito non serve qui.
            cur.execute("CREATE TABLE stime (id SERIAL PRIMARY KEY)")
            for versione in CATENA:
                cur.execute((MIGRAZIONI / f"{versione}.sql").read_text(encoding="utf-8"))
        yield {"dsn": dsn, "conn": conn}
    finally:
        conn.close()
        with servizio.cursor() as cur:
            cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = %s AND pid <> pg_backend_pid()", (nome,))
            cur.execute(f'DROP DATABASE IF EXISTS "{nome}"')
        servizio.close()


def _operator(user_id, agency_id, role, platform=False) -> OperatorContext:
    return OperatorContext(user_id=user_id, agency_id=agency_id, role=role,
                           is_platform_admin=platform, session_id=user_id,
                           auth_channel="operator_session")


@pytest.fixture
def mondo(db, monkeypatch):
    """Due agenzie, owner/admin/agent/agent2 in A, owner in B; tutte le
    membership attive. `core.database.get_connection` punta al database
    usa-e-getta (P26 H11: il repository non apre connessioni proprie)."""
    psycopg2 = pytest.importorskip("psycopg2")
    from core import database as core_database
    monkeypatch.setattr(core_database, "get_connection", lambda: psycopg2.connect(db["dsn"]))

    conn = db["conn"]
    with conn.cursor() as cur:
        for tabella in ("lead_stime", "leads", "contact_roles", "contacts", "activities",
                        "tasks", "agency_memberships", "operator_users"):
            cur.execute(f"DELETE FROM {tabella}")
        cur.execute("DELETE FROM agencies WHERE slug <> 'stima360'")
        cur.execute("SELECT id FROM agencies WHERE slug = 'stima360'")
        a = cur.fetchone()[0]
        cur.execute("INSERT INTO agencies (name, slug, status) VALUES ('B', 'b-due', 'active') RETURNING id")
        b = cur.fetchone()[0]

        def operatore(email, agenzia, ruolo):
            cur.execute("INSERT INTO operator_users (email, email_normalized, password_hash) "
                        "VALUES (%s, %s, 'pbkdf2_sha256$1$x$y') RETURNING id", (email, email))
            uid = cur.fetchone()[0]
            cur.execute("INSERT INTO agency_memberships (agency_id, operator_user_id, role, status) "
                        "VALUES (%s, %s, %s, 'active')", (agenzia, uid, ruolo))
            return uid

        owner_a = operatore("owner.a@example.test", a, "agency_owner")
        admin_a = operatore("admin.a@example.test", a, "agency_admin")
        agent_a = operatore("agent.a@example.test", a, "agent")
        agent_a2 = operatore("agent.a2@example.test", a, "agent")
        owner_b = operatore("owner.b@example.test", b, "agency_owner")

    def sql(testo, parametri=None):
        with conn.cursor() as cur:
            cur.execute(testo, parametri)
            return cur.fetchall() if cur.description else None

    return {
        "sql": sql, "A": a, "B": b,
        "owner_a": _operator(owner_a, a, "agency_owner"),
        "admin_a": _operator(admin_a, a, "agency_admin"),
        "agent_a": _operator(agent_a, a, "agent"),
        "agent_a2": _operator(agent_a2, a, "agent"),
        "owner_b": _operator(owner_b, b, "agency_owner"),
    }


@pg
def test_d0_the_composite_fk_of_030_is_live_in_this_database(mondo):
    """Controllo negativo: senza questo, D1 proverebbe solo che l'INSERT passa."""
    from psycopg2 import errors
    with pytest.raises(errors.ForeignKeyViolation):
        mondo["sql"]("INSERT INTO contacts (display_name, agency_id, assigned_agent_id) "
                     "VALUES ('x', %s, %s)", (mondo["A"], mondo["owner_b"].user_id))
    with pytest.raises(errors.ForeignKeyViolation):
        mondo["sql"]("INSERT INTO contacts (display_name, agency_id, assigned_agent_id) "
                     "VALUES ('x', %s, %s)", (mondo["A"], 424242))
    assert mondo["sql"]("SELECT count(*) FROM contacts")[0][0] == 0


@pg
def test_d1_an_agent_creates_a_contact_it_can_see_and_edit(mondo):
    agent, colleague = mondo["agent_a"], mondo["agent_a2"]
    row = repository.create_contact(agent, {
        "contact_type": "person", "first_name": "Mario", "last_name": "Rossi",
        "display_name": "Mario Rossi", "company_name": None, "email": None,
        "email_normalized": None, "phone": None, "phone_normalized": None,
        "secondary_phone": None, "source": "crm_manual", "status": "active", "notes": None,
    })
    assert row["agency_id"] == mondo["A"]
    assert row["created_by_user_id"] == agent.user_id
    assert row["assigned_agent_id"] == agent.user_id

    # The FK target really exists: (agency, agent) is an active membership.
    assert mondo["sql"](
        "SELECT count(*) FROM agency_memberships WHERE agency_id = %s AND operator_user_id = %s "
        "AND status = 'active'", (row["agency_id"], row["assigned_agent_id"]))[0][0] == 1

    assert repository.get_contact(agent, row["id"])["id"] == row["id"]
    assert [c["id"] for c in repository.list_contacts(agent, 50, 0, None, None)] == [row["id"]]
    updated = repository.update_contact(agent, row["id"], {"notes": "visto"})
    assert updated["notes"] == "visto"

    with pytest.raises(NotFoundError):
        repository.get_contact(colleague, row["id"])
    with pytest.raises(NotFoundError):
        repository.update_contact(colleague, row["id"], {"notes": "no"})
    assert repository.list_contacts(colleague, 50, 0, None, None) == []
    with pytest.raises(NotFoundError):
        repository.get_contact(mondo["owner_b"], row["id"])

    for boss in ("owner_a", "admin_a"):
        assert repository.get_contact(mondo[boss], row["id"])["notes"] == "visto"
    assert mondo["sql"]("SELECT notes FROM contacts WHERE id = %s", (row["id"],))[0][0] == "visto"


@pg
def test_d2_an_agent_lead_follows_the_contact_and_the_same_matrix(mondo):
    agent, colleague = mondo["agent_a"], mondo["agent_a2"]
    contact = repository.create_contact(agent, {
        "contact_type": "person", "first_name": None, "last_name": None,
        "display_name": "Venditore", "company_name": None, "email": None,
        "email_normalized": None, "phone": None, "phone_normalized": None,
        "secondary_phone": None, "source": None, "status": "active", "notes": None,
    })
    lead_data = {"contact_id": contact["id"], "source": None, "pipeline": "sell",
                 "stage": "new", "priority": "normal", "status": "open", "assigned_to": None,
                 "estimated_value": None, "next_action_at": None, "lost_reason": None,
                 "notes": None}
    lead = repository.create_lead(agent, lead_data)
    assert lead["agency_id"] == mondo["A"]
    assert lead["assigned_agent_id"] == agent.user_id
    assert lead["created_by_user_id"] == agent.user_id
    assert repository.get_lead(agent, lead["id"])["id"] == lead["id"]
    with pytest.raises(NotFoundError):
        repository.get_lead(colleague, lead["id"])
    with pytest.raises(NotFoundError):
        repository.get_lead(mondo["owner_b"], lead["id"])
    assert repository.get_lead(mondo["owner_a"], lead["id"])["id"] == lead["id"]

    # The contact check is kept: a colleague cannot open a lead on it.
    with pytest.raises(NotFoundError):
        repository.create_lead(colleague, lead_data)
    assert mondo["sql"]("SELECT count(*) FROM leads")[0][0] == 1


@pg
def test_d3_an_agent_cannot_open_a_lead_on_an_owner_contact(mondo):
    owner_contact = repository.create_contact(mondo["owner_a"], {
        "contact_type": "person", "first_name": None, "last_name": None,
        "display_name": "Del titolare", "company_name": None, "email": None,
        "email_normalized": None, "phone": None, "phone_normalized": None,
        "secondary_phone": None, "source": None, "status": "active", "notes": None,
    })
    assert owner_contact["assigned_agent_id"] is None
    with pytest.raises(NotFoundError):
        repository.get_contact(mondo["agent_a"], owner_contact["id"])
    with pytest.raises(NotFoundError):
        repository.create_lead(mondo["agent_a"], {
            "contact_id": owner_contact["id"], "source": None, "pipeline": "sell",
            "stage": "new", "priority": "normal", "status": "open", "assigned_to": None,
            "estimated_value": None, "next_action_at": None, "lost_reason": None,
            "notes": None})
    assert mondo["sql"]("SELECT count(*) FROM leads")[0][0] == 0


@pg
@pytest.mark.parametrize("boss", ["owner_a", "admin_a"])
def test_d4_owner_and_admin_still_create_unassigned_rows(mondo, boss):
    contact = repository.create_contact(mondo[boss], {
        "contact_type": "person", "first_name": None, "last_name": None,
        "display_name": "Senza agente", "company_name": None, "email": None,
        "email_normalized": None, "phone": None, "phone_normalized": None,
        "secondary_phone": None, "source": None, "status": "active", "notes": None,
    })
    assert contact["assigned_agent_id"] is None
    lead = repository.create_lead(mondo[boss], {
        "contact_id": contact["id"], "source": None, "pipeline": "sell", "stage": "new",
        "priority": "normal", "status": "open", "assigned_to": None, "estimated_value": None,
        "next_action_at": None, "lost_reason": None, "notes": None})
    assert lead["assigned_agent_id"] is None
    assert mondo["sql"]("SELECT assigned_agent_id FROM contacts WHERE id = %s",
                        (contact["id"],))[0][0] is None
    assert mondo["sql"]("SELECT assigned_agent_id FROM leads WHERE id = %s",
                        (lead["id"],))[0][0] is None
