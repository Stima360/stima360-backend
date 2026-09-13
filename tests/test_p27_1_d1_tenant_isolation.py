"""P27-1 decisione D1 - nessun accesso globale IMPLICITO ai dati di tenant.

LA REGOLA, E COSA HA SOSTITUITO

Fino a P27-1 `core/scope.py::scoped_predicate` aveva un ramo - uno solo - che
restituiva il predicato `TRUE`: un operatore con `is_platform_admin` e senza
membership leggeva le righe di TUTTE le agenzie attraverso gli endpoint
ordinari di CORE. Era una decisione deliberata di P26-1, difendibile finche'
l'amministrazione di piattaforma non aveva una superficie propria.

P27-1 le da' quella superficie, e con essa la regola del modello Network:

    la superficie Platform amministra la RETE;
    la superficie tenant serve UNA agenzia;
    fra le due non esiste un accesso globale implicito.

Il ramo e' stato RIMOSSO, non ristretto. Un platform admin sbilanciato cade
adesso su `require_agency()` come qualunque altro contesto senza agenzia, e
viene rifiutato prima che una istruzione venga costruita.

QUESTO FILE E' IL PROPRIETARIO DELLA NUOVA REGOLA. I test di P26 che
codificavano quella vecchia sono stati ri-puntati, uno per uno, con una nota
che dice da cosa e perche'; non rilassati.

Mappa:

    D1-1  la funzione di scoping: il rifiuto, su ogni tabella
    D1-2  la prova strutturale che il ramo non esiste piu'
    D1-3  cio' che NON e' cambiato (D4, agente, contesto di sistema)
    D1-4  il repository: rifiutato prima del cursore
    D1-5  HTTP: 403, e non un 500 e non un 404
    D1-6  la via d'uscita che resta chiusa: l'assegnazione
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from core import repository
from core.scope import scoped_predicate, scoped_source
from operator_auth.context import OperatorContext, SystemAgencyContext
from operator_auth.exceptions import PlatformAdminAgencyRequired

ROOT = Path(__file__).resolve().parents[1]

ALL_TABLES = ("contacts", "leads", "activities", "tasks")
AGENCY = 4242
AGENT_USER = 9


def _operator(**overrides) -> OperatorContext:
    base = dict(
        user_id=1,
        agency_id=AGENCY,
        role="agency_owner",
        is_platform_admin=False,
        session_id=1,
        auth_channel="operator_session",
    )
    base.update(overrides)
    return OperatorContext(**base)


def _unbound_platform_admin() -> OperatorContext:
    return _operator(agency_id=None, role=None, is_platform_admin=True)


def _bound_platform_admin(role="agency_owner") -> OperatorContext:
    return _operator(agency_id=AGENCY, role=role, is_platform_admin=True)


def _scope_function(name: str) -> ast.FunctionDef:
    tree = ast.parse((ROOT / "core" / "scope.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} non trovata in core/scope.py")


def _returned_expressions(fn: ast.FunctionDef) -> list[str]:
    return [
        ast.unparse(node.value)
        for node in ast.walk(fn)
        if isinstance(node, ast.Return) and node.value is not None
    ]


# ---------------------------------------------------------------------------
# D1-1 - il rifiuto
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("table", ALL_TABLES)
def test_d1_an_unbound_platform_admin_is_refused_on_every_scoped_table(table):
    """Prima era `WHERE TRUE` su tutte e quattro. Adesso e' un rifiuto su tutte
    e quattro. Ri-puntato da
    test_p26_1_scope_enforcement::test_c9_an_unbound_platform_admin_sees_every_agency.
    """
    with pytest.raises(PlatformAdminAgencyRequired):
        scoped_predicate(_unbound_platform_admin(), table, "c")
    with pytest.raises(PlatformAdminAgencyRequired):
        scoped_source(_unbound_platform_admin(), table, "c")


def test_d1_the_refusal_reuses_the_exception_the_routers_already_translate():
    """Non un tipo nuovo.

    `PlatformAdminAgencyRequired` e' gia' mappata su 403 da CORE, da OWNER
    Admin e da `main.agency_of`. Introdurne un altro avrebbe significato
    centocinquanta route da insegnare a tradurlo, e un 500 ovunque ci si fosse
    dimenticati.
    """
    with pytest.raises(PlatformAdminAgencyRequired):
        scoped_predicate(_unbound_platform_admin(), "contacts", "c")


def test_d1_a_truthy_non_boolean_flag_is_refused_as_well():
    """Il vecchio ramo si difendeva da un flag non booleano con `is True`.

    Adesso non c'e' niente da difendere: non esiste un ramo che un valore
    truthy possa aprire. Il test resta perche' la proprieta' osservabile - un
    contesto malformato non legge nulla - e' quella che conta.
    """

    class _Sloppy:
        agency_id = None
        role = None
        user_id = None
        is_platform_admin = 1

        def require_agency(self):
            raise PlatformAdminAgencyRequired("unbound")

    with pytest.raises(PlatformAdminAgencyRequired):
        scoped_source(_Sloppy(), "contacts", "c")


# ---------------------------------------------------------------------------
# D1-2 - la prova strutturale
# ---------------------------------------------------------------------------

def test_d1_no_return_path_of_the_predicate_builder_emits_true():
    """Letto dal sorgente, cosi' un ramo mai eseguito e' comunque coperto."""
    for expression in _returned_expressions(_scope_function("scoped_predicate")):
        assert "TRUE" not in expression.upper(), expression


def test_d1_the_predicate_builder_no_longer_branches_on_the_platform_flag():
    """La proprieta' forte: non c'e' piu' un `if` che guardi il flag.

    Un ramo ristretto sarebbe un ramo che una fase successiva puo' riaprire
    allargandone la guardia. Un ramo che non esiste va riscritto da capo, il
    che e' precisamente l'atto deliberato che si vuole richiedere.
    """
    fn = _scope_function("scoped_predicate")
    for node in ast.walk(fn):
        if isinstance(node, ast.If):
            guard = ast.unparse(node.test)
            assert "is_platform_admin" not in guard, guard


def test_d1_every_predicate_return_path_binds_the_agency():
    fn = _scope_function("scoped_predicate")
    expressions = _returned_expressions(fn)
    assert expressions, "scoped_predicate non ritorna nulla"
    for expression in expressions:
        assert "parts" in expression or "agency_id" in expression, expression


def test_d1_require_agency_is_still_the_fail_closed_path():
    source = inspect.getsource(_scope_function.__globals__["scoped_predicate"]) \
        if False else (ROOT / "core" / "scope.py").read_text(encoding="utf-8")
    assert "ctx.require_agency()" in source


# ---------------------------------------------------------------------------
# D1-3 - cio' che NON e' cambiato
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("role", [None, "agency_owner", "agency_admin", "agent"])
@pytest.mark.parametrize("table", ALL_TABLES)
def test_d1_d4_a_bound_platform_admin_is_still_scoped_to_its_agency(role, table):
    """Decisione D4: la stessa identita' puo' essere platform admin e titolare.

    Il flag non allargava uno scope legato a un'agenzia prima e non lo allarga
    adesso. E' la meta' di D4 che rende D1 sostenibile: chi deve vedere i dati
    di un'agenzia li vede attraverso la SUA membership, non attraverso il flag.
    """
    source, params = scoped_source(_bound_platform_admin(role=role), table, "c")
    assert source.startswith(f"{table} c WHERE c.agency_id = %s")
    assert params[0] == AGENCY
    assert "TRUE" not in source


def test_d1_the_agent_narrowing_is_untouched():
    source, params = scoped_source(
        _operator(role="agent", user_id=AGENT_USER), "contacts", "c"
    )
    assert "assigned_agent_id" in source
    assert params == [AGENCY, AGENT_USER]


@pytest.mark.parametrize("table", ALL_TABLES)
def test_d1_the_public_stima_system_context_is_untouched(table):
    """Il funnel pubblico non passava da quel ramo e non ci passa adesso.

    Presenta `is_platform_admin=False` e un'agenzia risolta lato server, quindi
    prendeva gia' il ramo a una sola agenzia. Asserito perche' una modifica a
    questa funzione che rompesse il funnel pubblico romperebbe l'ingresso dei
    lead, che e' la cosa piu' costosa da rompere in tutto il sistema.
    """
    ctx = SystemAgencyContext(agency_id=AGENCY, origin="public_stima")
    source, params = scoped_source(ctx, table, "c")
    assert source == f"{table} c WHERE c.agency_id = %s"
    assert params == [AGENCY]


def test_d1_an_unbound_non_platform_operator_is_refused_exactly_as_before():
    with pytest.raises(PlatformAdminAgencyRequired):
        scoped_source(_operator(agency_id=None), "contacts", "c")


def test_d1_the_permission_matrix_was_not_touched():
    """D1 non e' una modifica ai permessi, e' una modifica allo SCOPE.

    `sees_all_agency_records` resta True per un platform admin: significa
    "vede tutti i record DELLO SCOPE", non "vede tutte le agenzie". Con D1 lo
    scope di un platform admin sbilanciato non esiste, quindi la predicato non
    concede nulla; per uno legato a un'agenzia concede cio' che concedeva
    prima. Asserito perche' un lettore futuro potrebbe leggerlo come un residuo
    di D1 e "sistemarlo".
    """
    from operator_auth import permissions

    assert permissions.sees_all_agency_records(None, True) is True
    assert permissions.may_assign_records(None, True) is True
    assert permissions.may_create_agency(None, True) is True
    assert permissions.may_create_agency("agency_owner", False) is False


# ---------------------------------------------------------------------------
# D1-4 - rifiutato prima del cursore
# ---------------------------------------------------------------------------

class _Tripwire:
    """Un cursore che fallisce il test se qualcuno lo usa."""

    def execute(self, sql, params=None):
        raise AssertionError(f"un'istruzione e' sfuggita al rifiuto: {sql!r}")

    def fetchone(self):
        raise AssertionError("una lettura e' sfuggita al rifiuto")

    def fetchall(self):
        raise AssertionError("una lettura e' sfuggita al rifiuto")


@pytest.fixture
def tripwire(monkeypatch):
    from contextlib import contextmanager

    @contextmanager
    def _cursor(*args, **kwargs):
        yield (None, _Tripwire())

    monkeypatch.setattr(repository, "core_cursor", _cursor)


READS = (
    lambda ctx: repository.list_contacts(ctx, 50, 0, None, None),
    lambda ctx: repository.list_leads(ctx, 50, 0, None, None, None, None),
    lambda ctx: repository.list_activities(ctx, 50, 0, None, None, None),
    lambda ctx: repository.list_tasks(ctx, 50, 0, None, None, None, None),
)


@pytest.mark.parametrize("read", READS)
def test_d1_no_statement_reaches_the_database_on_a_refused_read(read, tripwire):
    """Ri-puntato da
    test_p26_1_core_isolation::test_d7_unbound_platform_admin_reads_cross_agency,
    che asseriva `WHERE TRUE in statement.sql`.

    Non basta che il risultato sia vuoto: l'istruzione non deve partire. Il
    _Tripwire e' cio' che distingue "rifiutato" da "eseguito e filtrato".
    """
    with pytest.raises(PlatformAdminAgencyRequired):
        read(_unbound_platform_admin())


CREATES = (
    lambda ctx: repository.create_contact(ctx, {"display_name": "x"}),
    lambda ctx: repository.create_lead(ctx, {"contact_id": 1}),
    lambda ctx: repository.create_activity(ctx, {"activity_type": "note"}),
    lambda ctx: repository.create_task(ctx, {"title": "t"}),
)


@pytest.mark.parametrize("create", CREATES)
def test_d1_writes_were_already_refused_and_still_are(create, tripwire):
    """Le scritture erano gia' chiuse in P26-1 (`_stamp` chiama
    `require_agency`). Asserito perche' D1 non doveva toccarle, e un test che
    non c'e' non prova che non le abbia toccate."""
    with pytest.raises(PlatformAdminAgencyRequired):
        create(_unbound_platform_admin())


# ---------------------------------------------------------------------------
# D1-6 - l'assegnazione, che era l'eccezione dichiarata di P26-1
# ---------------------------------------------------------------------------

ASSIGN_FUNCTIONS = ("set_contact_assignment", "set_lead_assignment")


@pytest.mark.parametrize("name", ASSIGN_FUNCTIONS)
def test_d1_the_assignment_path_is_no_longer_an_exception(name, tripwire):
    """Ri-puntato da
    test_p26_1_core_isolation::test_d12_an_unbound_platform_admin_assigns_without_binding_an_agency.

    P26-1 aveva un'eccezione approvata: l'assegnazione non chiamava
    `require_agency()`, perche' l'agenzia veniva dal RECORD e non dal
    chiamante. Ma il record veniva letto con `WHERE TRUE`, cioe' scegliendo fra
    i record di tutte le agenzie - che e' esattamente l'accesso globale
    implicito che D1 chiude. L'eccezione cade con il ramo su cui si reggeva.

    Non e' una funzione persa: un platform admin CON membership assegna dentro
    la propria agenzia come sempre (D4).
    """
    with pytest.raises(PlatformAdminAgencyRequired):
        getattr(repository, name)(_unbound_platform_admin(), 7, 9)


@pytest.mark.parametrize("name", ASSIGN_FUNCTIONS)
def test_d1_a_bound_platform_admin_still_reaches_the_assignment_path(name, monkeypatch):
    """Controllo positivo: il rifiuto sopra e' dovuto allo scope mancante, non
    all'aver rotto l'assegnazione per tutti."""
    from contextlib import contextmanager

    seen = []

    class _Cursor:
        def execute(self, sql, params=None):
            seen.append(" ".join(str(sql).split()))

        def fetchone(self):
            return {"id": 7, "agency_id": AGENCY, "assigned_agent_id": 9}

    @contextmanager
    def _cursor(*args, **kwargs):
        yield (None, _Cursor())

    monkeypatch.setattr(repository, "core_cursor", _cursor)
    getattr(repository, name)(_bound_platform_admin(), 7, 9)
    assert seen, "nessuna istruzione e' stata eseguita"
    assert not any("WHERE TRUE" in statement.upper() for statement in seen), seen
    assert any("agency_id = %s" in statement for statement in seen), seen
