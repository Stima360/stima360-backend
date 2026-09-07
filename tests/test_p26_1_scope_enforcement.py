"""P26-1 - structural guards for the agency scoping machinery.

Offline tests. No database, no FastAPI, no network.

Task 4 populates this module with the *structural* properties of the two
context types: immutability, the exact dataclass field lists, protocol
conformance, and the dependency constraints. Their behaviour is asserted in
tests/test_p26_1_operator_auth.py.

Task 10 adds the SQL-builder guards: the mandatory agency predicate and the
server-only public-STIMA context factory. Tasks 11 and 15 will add the
repository and route-walk guards; those are not present yet.

Coverage map from the approved design spec sections 2.5.1, 8, 8.1 and 9.1:

    C1  both context types are frozen
    C2  OperatorContext carries the six declared fields
    C3  SystemAgencyContext carries exactly two dataclass fields
    C4  the fields SystemAgencyContext must NOT declare
    C5  both types structurally satisfy AgencyScope
    C6  dependency constraints - no DB, no web framework, no owner/
    C7  core/scope.py constants
    C8  the scoped-table guard
    C9  the predicate per role and per table
    C10 structural fail-closed properties of scoped_source
    C11 the public-STIMA context factory
    C12 core/scope.py dependency constraints
"""
from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import pytest

from operator_auth import context as context_module
from operator_auth import permissions as permissions_module
from operator_auth.context import AgencyScope, OperatorContext, SystemAgencyContext
from operator_auth.exceptions import PlatformAdminAgencyRequired

import pathlib

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "operator_auth"

def _executable_source(path) -> str:
    """Return a module's code with every docstring and comment removed.

    Forbidden-identifier rules must describe what the code *does*. Four rules in
    this suite have now been tripped by a docstring that merely explains the
    rule - naming ADMIN_USER to say the Basic branch is deferred, naming
    agency_id to say it is untrusted. Rewording the prose each time would be
    fixing the symptom; stripping the prose is the fix.
    """
    import ast as _ast

    tree = _ast.parse(pathlib.Path(path).read_text(encoding="utf-8"))
    for node in _ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(node, (_ast.Module, _ast.ClassDef, _ast.FunctionDef,
                             _ast.AsyncFunctionDef)) and body:
            first = body[0]
            if (isinstance(first, _ast.Expr)
                    and isinstance(first.value, _ast.Constant)
                    and isinstance(first.value.value, str)):
                node.body = body[1:] or [_ast.Pass()]
    return _ast.unparse(tree)


# Fields that would let a system context impersonate an operator.
FORBIDDEN_SYSTEM_FIELDS = ("user_id", "role", "is_platform_admin", "session_id", "auth_channel")


def _operator(**overrides) -> OperatorContext:
    values = {
        "user_id": 1,
        "agency_id": 10,
        "role": "agency_owner",
        "is_platform_admin": False,
        "session_id": 100,
        "auth_channel": "operator_session",
    }
    values.update(overrides)
    return OperatorContext(**values)


def _imported_modules(path: Path) -> set[str]:
    """Top-level modules a file imports; relative imports become the package.

    The owning package is taken from the file's directory rather than hard-coded,
    so the same rule reads operator_auth/*.py and core/scope.py correctly.
    """
    path = Path(path)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                names.add(path.parent.name)
            elif node.module:
                names.add(node.module.split(".")[0])
    return names


# ---------------------------------------------------------------------------
# C1 - immutability
# ---------------------------------------------------------------------------

def test_c1_operator_context_is_frozen():
    ctx = _operator()
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.agency_id = 999


def test_c1_operator_context_rejects_every_field_mutation():
    ctx = _operator()
    for field in ("user_id", "agency_id", "role", "is_platform_admin",
                  "session_id", "auth_channel"):
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(ctx, field, "mutated")


def test_c1_system_context_is_frozen():
    ctx = SystemAgencyContext(agency_id=1, origin="public_stima")
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.agency_id = 999


def test_c1_system_context_cannot_acquire_operator_attributes():
    """Assigning a role onto a system context must fail, not shadow the class."""
    ctx = SystemAgencyContext(agency_id=1, origin="public_stima")
    for field in FORBIDDEN_SYSTEM_FIELDS:
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(ctx, field, "agent")


def test_c1_no_context_offers_a_mutating_helper():
    """No with_agency(), no setter, no copy-with-override escape hatch."""
    for kind in (OperatorContext, SystemAgencyContext):
        for name in dir(kind):
            assert not name.startswith("with_"), f"{kind.__name__}.{name}"
            assert not name.startswith("set_"), f"{kind.__name__}.{name}"


# ---------------------------------------------------------------------------
# C2 / C3 / C4 - the field lists
# ---------------------------------------------------------------------------

def test_c2_operator_context_fields():
    names = [f.name for f in dataclasses.fields(OperatorContext)]
    assert names == [
        "user_id",
        "agency_id",
        "role",
        "is_platform_admin",
        "session_id",
        "auth_channel",
    ], names


def test_c3_system_context_has_exactly_two_dataclass_fields():
    names = [f.name for f in dataclasses.fields(SystemAgencyContext)]
    assert names == ["agency_id", "origin"], names
    assert len(names) == 2


@pytest.mark.parametrize("field", FORBIDDEN_SYSTEM_FIELDS)
def test_c4_system_context_does_not_declare_operator_fields(field):
    """Spec 8.1: it can express 'this agency' and nothing else.

    These are class attributes, not dataclass fields, so no constructor
    argument can ever set them.
    """
    assert field not in {f.name for f in dataclasses.fields(SystemAgencyContext)}


def test_c4_system_context_constructor_rejects_operator_arguments():
    for field in FORBIDDEN_SYSTEM_FIELDS:
        with pytest.raises(TypeError):
            SystemAgencyContext(agency_id=1, origin="public_stima", **{field: "x"})


def test_c4_system_context_exposes_neutral_class_attributes():
    ctx = SystemAgencyContext(agency_id=1, origin="public_stima")
    assert ctx.user_id is None
    assert ctx.role is None
    assert ctx.is_platform_admin is False


# ---------------------------------------------------------------------------
# C5 - protocol conformance
# ---------------------------------------------------------------------------

def test_c5_agency_scope_declares_the_minimal_member_set():
    members = {
        name for name in getattr(AgencyScope, "__annotations__", {})
    } | {
        name for name in vars(AgencyScope) if not name.startswith("_")
    }
    assert {"agency_id", "role", "user_id", "is_platform_admin", "require_agency"} <= members
    extra = members - {"agency_id", "role", "user_id", "is_platform_admin", "require_agency"}
    assert not extra, f"AgencyScope must stay minimal; found {extra}"


@pytest.mark.parametrize(
    "ctx",
    [
        OperatorContext(1, 10, "agency_owner", False, 100, "operator_session"),
        SystemAgencyContext(agency_id=10, origin="public_stima"),
    ],
)
def test_c5_both_contexts_satisfy_the_protocol_structurally(ctx):
    for member in ("agency_id", "role", "user_id", "is_platform_admin"):
        assert hasattr(ctx, member), member
    assert callable(ctx.require_agency)


@pytest.mark.parametrize(
    "ctx",
    [
        OperatorContext(1, 10, "agency_owner", False, 100, "operator_session"),
        SystemAgencyContext(agency_id=10, origin="public_stima"),
    ],
)
def test_c5_both_contexts_pass_a_runtime_isinstance_check(ctx):
    assert isinstance(ctx, AgencyScope)


def test_c5_an_object_missing_a_member_does_not_satisfy_the_protocol():
    """Negative control: the protocol must actually discriminate."""

    class NotAScope:
        agency_id = 1
        role = None
        # user_id and is_platform_admin missing, no require_agency

    assert not isinstance(NotAScope(), AgencyScope)


# ---------------------------------------------------------------------------
# C6 - dependency constraints
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("module", ["context.py", "exceptions.py", "permissions.py"])
def test_c6_no_database_or_web_framework_import(module):
    imported = _imported_modules(PACKAGE / module)
    for forbidden in ("psycopg2", "database", "fastapi", "starlette", "pydantic", "owner"):
        assert forbidden not in imported, f"{module} imports {forbidden}"


def test_c6_permissions_is_pure():
    """No I/O, no state, no context import: five functions over two strings."""
    imported = _imported_modules(PACKAGE / "permissions.py")
    assert imported <= {"__future__", "operator_auth"}, imported
    source = (PACKAGE / "permissions.py").read_text(encoding="utf-8")
    for forbidden in ("open(", "requests", "logging", "print(", "global "):
        assert forbidden not in source, f"permissions.py contains {forbidden!r}"


def test_c6_exception_subclasses_exception_and_is_catchable():
    assert issubclass(PlatformAdminAgencyRequired, Exception)
    try:
        raise PlatformAdminAgencyRequired("needs an agency")
    except Exception as exc:  # never BaseException-only, so callers can catch it
        assert isinstance(exc, PlatformAdminAgencyRequired)
        assert str(exc) == "needs an agency"


def test_c6_exceptions_module_is_pure_domain_code():
    imported = _imported_modules(PACKAGE / "exceptions.py")
    assert imported <= {"__future__"}, imported


def test_c6_exception_carries_no_http_coupling():
    """The domain must not know about status codes; the router maps them."""
    source = (PACKAGE / "exceptions.py").read_text(encoding="utf-8")
    for forbidden in ("fastapi", "starlette", "HTTPException", "status_code"):
        assert forbidden not in source, f"exceptions.py references {forbidden!r}"
    assert not hasattr(PlatformAdminAgencyRequired, "status_code")


def test_c6_platform_admin_agency_required_is_the_only_exception_name():
    """The design spec is authoritative; the earlier draft name is retired."""
    source = (PACKAGE / "exceptions.py").read_text(encoding="utf-8")
    assert "AgencyContextRequired" not in source
    defined = [
        node.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ClassDef)
    ]
    # Task 6 moved AuthenticationFailed here from service.py: it is shared
    # between the service that raises it and the adapter that maps it to 401.
    assert defined == ["AuthenticationFailed", "PlatformAdminAgencyRequired"], defined


def test_c6_context_imports_only_its_own_package():
    imported = _imported_modules(PACKAGE / "context.py")
    assert imported <= {"__future__", "dataclasses", "typing", "operator_auth"}, imported


def _non_docstring_string_constants(path: Path) -> list[str]:
    """Every string literal in a file that is not a docstring.

    Prose must neither satisfy nor trip a rule about what the *code* does. A
    line-based comment strip is not enough: it leaves module, class and
    function docstrings in place, and those legitimately discuss the very
    identifiers being forbidden.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and body:
            first = body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                docstrings.add(id(first.value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def test_the_literal_scanner_ignores_docstrings_but_not_code(tmp_path):
    """Negative control for _non_docstring_string_constants.

    A first run of the rule below failed on permissions.py's own docstring,
    which explains that "platform_admin" is not a role. The scanner must tell
    an explanation apart from an instruction.
    """
    probe = tmp_path / "probe.py"
    probe.write_text(
        '"""A docstring mentioning platform_admin as prose."""\n'
        'ROLE = "platform_admin"\n'
        'def f():\n'
        '    """Another docstring naming platform_admin."""\n'
        '    return "agent"\n',
        encoding="utf-8",
    )
    found = _non_docstring_string_constants(probe)
    assert "platform_admin" in found
    assert "agent" in found
    assert len([x for x in found if x == "platform_admin"]) == 1


def test_c6_no_role_beyond_the_three_agency_roles():
    """platform_admin is a flag on the operator, never a fourth role.

    Asserted against executable string literals only, so the modules remain
    free to explain the rule in prose.
    """
    from operator_auth import enums

    for path in ("context.py", "permissions.py"):
        literals = _non_docstring_string_constants(PACKAGE / path)
        assert "platform_admin" not in literals, (
            f"{path} uses 'platform_admin' as a value; it is a flag on "
            "operator_users, not a role"
        )
        for literal in literals:
            if literal.startswith("agency_") or literal == "agent":
                assert literal in enums.AGENCY_ROLES, literal
    assert enums.AGENCY_ROLES == ("agency_owner", "agency_admin", "agent")


# ---------------------------------------------------------------------------
# Task boundary.
# ---------------------------------------------------------------------------

def test_the_scope_builder_exists_and_is_the_only_thing_task_10_added():
    """Task 10 delivers exactly one new module.

    Until Task 10 this guard asserted the opposite - that core/scope.py did not
    exist yet - and it fired, as designed, the moment the builder appeared. It
    is retired here into its positive form rather than deleted, so the file
    still pins where the builder lives.
    """
    assert (ROOT / "core" / "scope.py").exists(), "Task 10 creates core/scope.py"


# ===========================================================================
# Layer A - static admission rules for core/repository.py (Task 11).
#
# Approved plan section 11.1. The withdrawn draft rule banned SQL literals
# naming a scoped table; it outlawed legitimate statements and its only escape
# was dynamic SQL, which makes the code harder to audit. These rules use table
# names only to *discover* which functions to police.
#
# Layer B - that the executed statement carries a bound predicate - lives in
# tests/test_p26_1_core_isolation.py.
# ===========================================================================

REPOSITORY = ROOT / "core" / "repository.py"

# Public functions allowed to not take a scope, each for a recorded reason.
CTX_FREE = {
    "bridge_public_stima",           # SYSTEM_CONTEXT_FUNCTIONS: builds its own
    "create_activity_with_cursor",   # R-4: flow/, followup/, owner/
    "create_task_with_cursor",       # R-4: flow/, followup/, owner/
}


def _scoped_function_names(source: str) -> set[str]:
    """A1: functions whose own SQL mentions a scoped table."""
    tree = ast.parse(source)
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = node.body[1:] if (
            node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        ) else node.body
        for statement in body:
            for child in ast.walk(statement):
                if isinstance(child, ast.Constant) and isinstance(child.value, str):
                    if any(table in child.value for table in SCOPED_TABLES):
                        found.add(node.name)
    return found


def _context_admission_violations(source: str) -> list[str]:
    """A2: every scoped function admits a scope, or is a named exception."""
    tree = ast.parse(source)
    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    violations = []
    for name in sorted(_scoped_function_names(source)):
        node = functions[name]
        arguments = [argument.arg for argument in node.args.args]
        if name in CTX_FREE or name in SYSTEM_CONTEXT_FUNCTIONS:
            continue
        if arguments and arguments[0] == "ctx":
            continue
        # A helper that runs inside a caller's transaction takes `cur` first.
        # It is admitted only when it *also* declares `ctx` explicitly - the
        # cursor alone is not enough, and a keyword-only ctx counts.
        keyword_only = [argument.arg for argument in node.args.kwonlyargs]
        if arguments and arguments[0] == "cur" and "ctx" in arguments + keyword_only:
            continue
        violations.append(
            f"scoped function {name!r} does not admit a context (args: {arguments})"
        )
    return violations


def test_a1_discovery_finds_the_scoped_repository_functions():
    """The rule is only meaningful if it actually finds something to police."""
    found = _scoped_function_names(REPOSITORY.read_text(encoding="utf-8"))
    assert {"list_contacts", "get_contact", "update_contact", "list_leads",
            "list_activities", "list_tasks", "delete_task"} <= found, found


def test_a2_every_scoped_repository_function_admits_a_context():
    violations = _context_admission_violations(REPOSITORY.read_text(encoding="utf-8"))
    assert not violations, "\n".join(violations)


def test_a2_negative_control_an_unscoped_function_is_flagged():
    """B2 discipline applied to Layer A: no production file is edited.

    The auditor is fed a synthetic module. A guard never shown to reject has
    not been shown to detect.
    """
    synthetic = (
        "def list_secret_contacts(limit):\n"
        "    cur.execute('SELECT * FROM contacts LIMIT %s', [limit])\n"
    )
    violations = _context_admission_violations(synthetic)
    assert violations, "the auditor accepted an unscoped repository function"
    assert "list_secret_contacts" in violations[0]


def test_a2_negative_control_a_cursor_only_helper_is_flagged():
    """Taking `cur` is not the same as taking a scope."""
    synthetic = (
        "def touch_leads(cur, lead_id):\n"
        "    cur.execute('UPDATE leads SET x = 1 WHERE id = %s', [lead_id])\n"
    )
    assert _context_admission_violations(synthetic)


def test_a2_the_exception_list_is_exactly_the_documented_three():
    assert CTX_FREE == {
        "bridge_public_stima",
        "create_activity_with_cursor",
        "create_task_with_cursor",
    }


def test_a3_the_system_context_registry_stays_singular():
    assert SYSTEM_CONTEXT_FUNCTIONS == frozenset({"bridge_public_stima"})


def test_a4_the_bridge_validates_its_scope_before_it_opens_a_cursor():
    """A4, after P26-2B2B-R1: the scope arrives, so it must be *checked*.

    The rule used to require the scope to be built as the first statement
    inside the cursor block, so nothing could run unscoped. The bridge no
    longer builds one - the public writer resolves it once and passes it down,
    so the estimation and its CORE records share a single decision.

    The property therefore moves outward and gets stricter: the context is
    validated before the transaction opens at all. Nothing runs unscoped, and
    nothing runs under a scope this function has not accepted.
    """
    tree = ast.parse(REPOSITORY.read_text(encoding="utf-8"))
    bridge = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "bridge_public_stima"
    )

    with_blocks = [node for node in ast.walk(bridge) if isinstance(node, ast.With)]
    assert with_blocks, "bridge_public_stima opens no cursor"

    # Every guard must sit above the cursor block in the function body.
    cursor_line = with_blocks[0].lineno
    raises = [
        node for node in ast.walk(bridge)
        if isinstance(node, ast.Raise) and node.lineno < cursor_line
    ]
    assert len(raises) >= 2, (
        "the bridge admits its scope without checking both its type and its origin"
    )

    guard = ast.unparse(
        ast.Module(
            body=[s for s in bridge.body if s.lineno < cursor_line], type_ignores=[]
        )
    )
    assert "SystemAgencyContext" in guard, guard
    assert "origin" in guard, guard
    assert "require_agency()" in guard, guard

    # And it must not have quietly regained a resolution of its own.
    assert "system_context_for_public_stima" not in ast.unparse(bridge)


def test_a5_the_repository_never_reads_agency_from_the_payload():
    """A5: provenance columns come from the scope or the row, never the caller.

    Scoped to subscripts on a function's own *parameters* - the caller-supplied
    mappings. Task 12 legitimately reads `record["agency_id"]` from a row the
    repository itself fetched inside the caller's scope: that is the record-
    derived rule the assignment algorithm is built on, and it is the opposite
    of trusting the caller. `record` is a local, not a parameter, so the
    narrowed rule tells the two apart instead of banning the column outright.
    """
    tree = ast.parse(REPOSITORY.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        parameters = {
            a.arg for a in node.args.args + node.args.kwonlyargs + node.args.posonlyargs
        }
        for child in ast.walk(node):
            if (isinstance(child, ast.Subscript)
                    and isinstance(child.slice, ast.Constant)
                    and isinstance(child.value, ast.Name)
                    and child.value.id in parameters):
                assert child.slice.value not in ("agency_id", "created_by_user_id"), (
                    f"{node.name}: payload subscript {ast.unparse(child)} reads a "
                    "server-owned column"
                )
            if (isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Attribute)
                    and child.func.attr == "get"
                    and isinstance(child.func.value, ast.Name)
                    and child.func.value.id in parameters
                    and child.args
                    and isinstance(child.args[0], ast.Constant)):
                assert child.args[0].value not in ("agency_id", "created_by_user_id"), (
                    f"{node.name}: payload .get {ast.unparse(child)} reads a "
                    "server-owned column"
                )


def test_a5_negative_control_a_payload_read_is_still_caught():
    """The narrowing must not have made the rule vacuous."""
    synthetic = (
        "def create_contact(ctx, data):\n"
        "    agency = data['agency_id']\n"
        "    return agency\n"
    )
    tree = ast.parse(synthetic)
    node = tree.body[0]
    parameters = {a.arg for a in node.args.args}
    found = [
        child for child in ast.walk(node)
        if isinstance(child, ast.Subscript)
        and isinstance(child.slice, ast.Constant)
        and isinstance(child.value, ast.Name)
        and child.value.id in parameters
        and child.slice.value == "agency_id"
    ]
    assert found, "the narrowed rule no longer detects a payload read"


def test_a5_the_service_layer_never_reads_agency_from_the_payload():
    for module in ("service.py",):
        tree = ast.parse((ROOT / "core" / module).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
                assert node.slice.value not in ("agency_id", "created_by_user_id"), module


def test_a5_the_service_layer_constructs_no_context():
    """The scope arrives from the dependency; the service only forwards it."""
    code = _executable_source(ROOT / "core" / "service.py")
    for forbidden in ("OperatorContext(", "SystemAgencyContext(", "require_agency("):
        assert forbidden not in code, f"core/service.py contains {forbidden!r}"


# ---------------------------------------------------------------------------
# E - Task 13: one home for the domain errors, one home for the role matrix
# ---------------------------------------------------------------------------

def test_e1_permission_denied_is_a_core_domain_exception():
    from core.exceptions import CoreError, PermissionDenied

    assert issubclass(PermissionDenied, CoreError)
    assert issubclass(PermissionDenied, Exception)


def test_e1_permission_denied_is_defined_only_once():
    """No shadow copy may survive the move out of core/service.py."""
    defined_in = []
    for module in ("exceptions.py", "service.py", "router.py", "repository.py", "scope.py"):
        tree = ast.parse((ROOT / "core" / module).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "PermissionDenied":
                defined_in.append(module)
    assert defined_in == ["exceptions.py"], defined_in


def test_e1_the_service_imports_the_shared_exception():
    from core import exceptions, service

    assert service.PermissionDenied is exceptions.PermissionDenied


def test_e1_exceptions_module_stays_free_of_http():
    """A domain error must not know its status code; the router maps it.

    Asserted against executable source, so the module stays free to *explain*
    the D-6 mapping in prose while carrying none of it in code.
    """
    code = _executable_source(ROOT / "core" / "exceptions.py")
    for forbidden in ("fastapi", "starlette", "HTTPException", "status_code", "403"):
        assert forbidden not in code, f"core/exceptions.py references {forbidden!r}"
    assert _imported_modules(ROOT / "core" / "exceptions.py") <= {"__future__"}
    from core import exceptions

    assert not hasattr(exceptions.PermissionDenied, "status_code")


def test_e2_the_router_maps_the_four_outcomes():
    """403 permission, 404 missing/out-of-scope, 400 invalid target, 409 conflict."""
    tree = ast.parse(CORE_ROUTER.read_text(encoding="utf-8"))
    translate = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_translate"
    )
    mapping = {}
    for handler in [n for n in ast.walk(translate) if isinstance(n, ast.ExceptHandler)]:
        name = ast.unparse(handler.type)
        code = next(
            kw.value.value for raise_ in ast.walk(handler)
            if isinstance(raise_, ast.Call)
            for kw in raise_.keywords if kw.arg == "status_code"
        )
        mapping[name] = code
    assert mapping == {
        "NotFoundError": 404,
        "ConflictError": 409,
        "ValidationError": 400,
        "PermissionDenied": 403,
        # Task 19: an unbound platform admin reaching a generic create. Spec
        # 11.2 makes it 403 rather than inventing a default agency.
        "PlatformAdminAgencyRequired": 403,
    }, mapping


def test_e2_the_404_body_is_a_constant(hostile_free=None):
    """D-6: a foreign record and an absent one must be indistinguishable.

    Forwarding the exception message would name the id and the entity, which
    is exactly the disclosure the constant exists to prevent.
    """
    from core.router import NOT_FOUND_MESSAGE

    assert NOT_FOUND_MESSAGE == "Risorsa non trovata"
    translate = _function_source(CORE_ROUTER, "_translate")
    assert "status_code=404, detail=NOT_FOUND_MESSAGE" in translate, translate
    assert "detail=str(exc)) from exc\n    except ConflictError" not in translate


def test_e2_only_the_disclosing_outcome_uses_a_constant_message():
    """409 and 400 keep their specific messages - neither hides existence.

    A conflict is reported about a record the caller can already see, and a
    validation message is about the input they just sent. Flattening those to
    a constant would remove useful information for no security gain.
    """
    translate = _function_source(CORE_ROUTER, "_translate")
    assert "status_code=409, detail=str(exc)" in translate
    assert "status_code=400, detail=str(exc)" in translate


def test_e3_core_defines_no_role_matrix_of_its_own():
    """The agency role list lives in operator_auth.permissions and nowhere else."""
    for module in ("service.py", "router.py", "repository.py", "scope.py"):
        literals = _non_docstring_string_constants(ROOT / "core" / module)
        for role in ("agency_owner", "agency_admin"):
            assert role not in literals, (
                f"core/{module} names the role {role!r}; the matrix belongs to "
                "operator_auth.permissions"
            )


def test_e3_the_only_role_literal_in_core_is_the_agent_narrowing():
    """`agent` survives in core/scope.py because it selects a *predicate*,
    not a permission - the assignment authority question is answered by
    permissions.may_assign_records, which core must not restate."""
    literals = _non_docstring_string_constants(ROOT / "core" / "scope.py")
    assert "agent" in literals
    for module in ("service.py", "router.py", "repository.py"):
        assert "agent" not in _non_docstring_string_constants(ROOT / "core" / module), module


def test_e3_assignment_authority_comes_from_the_shared_primitive():
    code = _executable_source(ROOT / "core" / "service.py")
    assert "may_assign_records" in code
    for forbidden in ("== 'agent'", '== "agent"', "in ('agency_owner'", 'in ("agency_owner"'):
        assert forbidden not in code, f"core/service.py restates the role matrix: {forbidden}"


def test_e4_a_system_context_cannot_assign():
    """It has no principal, so it has no assignment authority - fail closed."""
    from core.service import PermissionDenied, _require_assignment_permission

    ctx = SystemAgencyContext(agency_id=1, origin="public_stima")
    with pytest.raises(PermissionDenied):
        _require_assignment_permission(ctx)


@pytest.mark.parametrize(
    "role,is_admin,allowed",
    [
        ("agency_owner", False, True),
        ("agency_admin", False, True),
        ("agent", False, False),
        (None, True, True),
        (None, False, False),
    ],
)
def test_e4_the_assignment_gate_matches_the_shared_primitive(role, is_admin, allowed):
    from operator_auth import permissions

    from core.service import PermissionDenied, _require_assignment_permission

    ctx = OperatorContext(
        user_id=1, agency_id=10, role=role, is_platform_admin=is_admin,
        session_id=1, auth_channel="operator_session",
    )
    assert permissions.may_assign_records(role, is_admin) is allowed
    if allowed:
        _require_assignment_permission(ctx)
    else:
        with pytest.raises(PermissionDenied):
            _require_assignment_permission(ctx)


def test_e5_the_denial_message_leaks_nothing():
    from core.service import ASSIGNMENT_DENIED_MESSAGE

    for leak in ("agency_id", "user_id", "stima360", "@"):
        assert leak not in ASSIGNMENT_DENIED_MESSAGE, ASSIGNMENT_DENIED_MESSAGE
    assert not any(character.isdigit() for character in ASSIGNMENT_DENIED_MESSAGE)


# ---------------------------------------------------------------------------
# A7 - the route-to-service walk (Task 11, deferred half)
#
# core/router.py is the last place a scope can be dropped. A handler that calls
# a ctx-required service function without forwarding its own ctx would fail at
# runtime rather than silently leak - but it would fail in production, not
# here. This walk moves that failure to the test suite, and fails closed when a
# future handler is added.
# ---------------------------------------------------------------------------

CORE_ROUTER = ROOT / "core" / "router.py"


def _ctx_required_service_functions() -> set[str]:
    """Service functions whose first positional parameter is the scope."""
    import core.service as service_module

    required = set()
    for name, value in inspect.getmembers(service_module, inspect.isfunction):
        if name.startswith("_") or value.__module__ != service_module.__name__:
            continue
        parameters = list(inspect.signature(value).parameters)
        if parameters and parameters[0] == "ctx":
            required.add(name)
    return required


def _declares_operator_scope(node: ast.FunctionDef) -> bool:
    """True when the handler takes `ctx` from Depends(require_operator)."""
    arguments = node.args.args + node.args.kwonlyargs
    defaults = list(node.args.defaults) + list(node.args.kw_defaults)
    padded = [None] * (len(arguments) - len(node.args.defaults)) + list(node.args.defaults)
    padded += list(node.args.kw_defaults)
    for argument, default in zip(arguments, padded):
        if argument.arg != "ctx" or default is None:
            continue
        rendered = ast.unparse(default)
        if "Depends(" in rendered and "require_operator" in rendered:
            return True
    return False


def _route_walk_violations(source: str, ctx_required: set[str]) -> list[str]:
    """Every handler calling a ctx-required service must forward its own ctx."""
    tree = ast.parse(source)
    violations: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue

            target, forwarded = None, None
            if (isinstance(call.func, ast.Name) and call.func.id == "_translate"
                    and call.args):
                target, forwarded = call.args[0], call.args[1:]
            elif isinstance(call.func, ast.Attribute):
                target, forwarded = call.func, call.args

            if not (isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "service"):
                continue
            if target.attr not in ctx_required:
                continue

            if not forwarded or not (
                isinstance(forwarded[0], ast.Name) and forwarded[0].id == "ctx"
            ):
                violations.append(
                    f"{node.name}: calls service.{target.attr} without forwarding ctx"
                )
            elif not _declares_operator_scope(node):
                violations.append(
                    f"{node.name}: forwards ctx but does not take it from "
                    "Depends(require_operator)"
                )
    return violations


def test_a7_the_service_layer_actually_requires_a_scope():
    """The walk is only meaningful if there is something to require."""
    required = _ctx_required_service_functions()
    assert {"list_contacts", "get_contact", "update_contact", "create_lead",
            "list_tasks", "delete_task"} <= required, required
    # The public bridge is server-originated and must NOT be in the set.
    assert "bridge_public_stima" not in required


def test_a7_every_core_handler_forwards_its_scope():
    violations = _route_walk_violations(
        CORE_ROUTER.read_text(encoding="utf-8"), _ctx_required_service_functions()
    )
    assert not violations, "\n".join(violations)


def test_a7_negative_control_a_handler_that_drops_ctx_is_flagged():
    """No production file is edited to manufacture this failure."""
    synthetic = (
        "@router.get('/contacts')\n"
        "def list_contacts(limit: int = 50):\n"
        "    return _translate(service.list_contacts, limit, 0, None, None)\n"
    )
    violations = _route_walk_violations(synthetic, {"list_contacts"})
    assert violations, "the walk accepted a handler that dropped its scope"
    assert "without forwarding ctx" in violations[0]


def test_a7_negative_control_a_direct_service_call_is_flagged():
    synthetic = (
        "def get_contact(contact_id: int):\n"
        "    return service.get_contact(contact_id)\n"
    )
    assert _route_walk_violations(synthetic, {"get_contact"})


def test_a7_negative_control_a_locally_built_scope_is_flagged():
    """Forwarding a `ctx` that the handler invented is not a scope."""
    synthetic = (
        "def list_contacts(limit: int = 50):\n"
        "    ctx = OperatorContext(1, 1, 'agency_owner', False, 1, 'x')\n"
        "    return _translate(service.list_contacts, ctx, limit, 0, None, None)\n"
    )
    violations = _route_walk_violations(synthetic, {"list_contacts"})
    assert violations, "the walk accepted a self-minted scope"
    assert "Depends(require_operator)" in violations[0]


def test_a7_the_router_constructs_no_context_of_its_own():
    code = _executable_source(CORE_ROUTER)
    for forbidden in ("OperatorContext(", "SystemAgencyContext(",
                      "system_context_for_public_stima"):
        assert forbidden not in code, f"core/router.py contains {forbidden!r}"


def test_a7_no_handler_accepts_a_scope_field_from_http():
    """No field of the caller's scope may arrive as a request parameter.

    `role` is deliberately absent from this list. In `/contacts/{contact_id}/
    roles/{role}` it is the CRM contact role - owner, seller, buyer - a
    different domain concept from the operator's agency role, and it is an
    existing path segment of a frozen route. The two handlers that take it are
    pinned below so it cannot spread.
    """
    tree = ast.parse(CORE_ROUTER.read_text(encoding="utf-8"))
    takes_role = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        names = {a.arg for a in node.args.args + node.args.kwonlyargs}
        for forbidden in ("agency_id", "user_id", "created_by_user_id",
                          "assigned_agent_id", "is_platform_admin", "auth_channel",
                          "session_id"):
            assert forbidden not in names, f"{node.name} accepts {forbidden} over HTTP"
        if "role" in names:
            takes_role.add(node.name)

    assert takes_role == {"delete_contact_role"}, (
        f"'role' is a contact-role path segment only; found in {takes_role}"
    )


def test_a7_the_contact_role_parameter_is_not_the_operator_role():
    """It is passed to the service as the CRM role, never near the scope."""
    tree = ast.parse(CORE_ROUTER.read_text(encoding="utf-8"))
    handler = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "delete_contact_role"
    )
    call = next(
        node for node in ast.walk(handler)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "_translate"
    )
    rendered = [ast.unparse(argument) for argument in call.args]
    # ctx comes first, straight from the dependency; role stays a plain value.
    assert rendered[:2] == ["service.delete_contact_role", "ctx"], rendered
    assert rendered[-1] == "role", rendered


def test_a7_no_handler_is_annotated_with_a_system_context():
    """SystemAgencyContext is server-originated and must not reach a route."""
    source = CORE_ROUTER.read_text(encoding="utf-8")
    assert "SystemAgencyContext" not in source


def test_a7_the_public_stima_bridge_gains_no_authentication():
    """The public estimation route is not in this router and stays untouched."""
    source = CORE_ROUTER.read_text(encoding="utf-8")
    assert "salva_stima" not in source
    assert "bridge_public_stima" not in source


# The frozen route inventory: path, method and status code. Task 11 is a
# scoping change, so any difference here means an unintended refactor.
FROZEN_CORE_ROUTES = {
    ("POST", "/api/core/contacts", 201),
    ("GET", "/api/core/contacts", 200),
    ("GET", "/api/core/contacts/{contact_id}", 200),
    ("PATCH", "/api/core/contacts/{contact_id}", 200),
    ("POST", "/api/core/contacts/{contact_id}/roles", 201),
    ("DELETE", "/api/core/contacts/{contact_id}/roles/{role}", 204),
    # Task 12: assignment is its own endpoint, never a field on the PATCH above.
    ("PATCH", "/api/core/contacts/{contact_id}/assignment", 200),
    ("PATCH", "/api/core/leads/{lead_id}/assignment", 200),
    ("POST", "/api/core/leads", 201),
    ("GET", "/api/core/leads", 200),
    ("GET", "/api/core/leads/{lead_id}", 200),
    ("PATCH", "/api/core/leads/{lead_id}", 200),
    ("POST", "/api/core/leads/{lead_id}/stime/{stima_id}", 201),
    ("DELETE", "/api/core/leads/{lead_id}/stime/{stima_id}", 204),
    ("POST", "/api/core/activities", 201),
    ("GET", "/api/core/activities", 200),
    ("DELETE", "/api/core/activities/{activity_id}", 204),
    ("POST", "/api/core/tasks", 201),
    ("GET", "/api/core/tasks", 200),
    ("PATCH", "/api/core/tasks/{task_id}", 200),
    ("DELETE", "/api/core/tasks/{task_id}", 204),
}


def test_a7_the_route_surface_is_unchanged():
    from core.router import router as core_router

    actual = {
        (method, route.path, route.status_code or 200)
        for route in core_router.routes
        for method in route.methods
    }
    assert actual == FROZEN_CORE_ROUTES, (
        f"added: {actual - FROZEN_CORE_ROUTES}\nremoved: {FROZEN_CORE_ROUTES - actual}"
    )


# The one schema allowed to carry an assigned agent. Task 12 gives assignment
# its own endpoint precisely so the field cannot appear on a generic update,
# where it would let a reassignment ride along on an ordinary edit (spec §10).
ASSIGNMENT_SCHEMA = "AssignmentUpdate"


def test_a6_no_core_request_schema_declares_a_server_owned_field():
    """A6: the columns cannot arrive over HTTP in the first place."""
    import core.schemas as schemas

    for name in dir(schemas):
        model = getattr(schemas, name)
        fields = getattr(model, "model_fields", None) or getattr(model, "__fields__", None)
        if not isinstance(fields, dict):
            continue
        forbidden_here = ["agency_id", "created_by_user_id"]
        if name != ASSIGNMENT_SCHEMA:
            forbidden_here.append("assigned_agent_id")
        for forbidden in forbidden_here:
            assert forbidden not in fields, f"{name} declares {forbidden}"


def test_a6_the_assignment_schema_carries_exactly_one_field():
    """It is an exception to A6, so its surface is pinned to one field."""
    from core.schemas import AssignmentUpdate

    fields = getattr(AssignmentUpdate, "model_fields", None) or AssignmentUpdate.__fields__
    assert set(fields) == {"assigned_agent_id"}, set(fields)


def test_a6_the_generic_update_schemas_still_refuse_an_assignment():
    """The separation only holds while these two stay closed."""
    from core.schemas import ContactUpdate, LeadUpdate

    for model in (ContactUpdate, LeadUpdate):
        fields = getattr(model, "model_fields", None) or model.__fields__
        assert "assigned_agent_id" not in fields, model.__name__
        assert model.model_config.get("extra") == "forbid" if hasattr(model, "model_config") \
            else model.Config.extra == "forbid"


def _function_source(path: Path, name: str) -> str:
    """One function's executable source, docstring and comments removed."""
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                node.body = body[1:] or [ast.Pass()]
            return ast.unparse(node)
    raise AssertionError(f"{path.name} defines no function named {name!r}")


@pytest.mark.parametrize("name", ["optional_session", "current_session"])
def test_the_session_path_never_builds_a_context_from_client_data(name):
    """The operator-session branch derives scope only from the cookie.

    An agency_id arriving in a header, a query parameter or a body is exactly
    what the approved rule forbids trusting, so this branch must never read one
    and must never construct a context itself.

    Narrowed twice, both times in target and never in strength. Task 11
    scoped it from the whole module to the session functions, because
    `legacy_basic_agency_context` legitimately constructs an agency-bound
    context. Task 15 moved `require_operator` off this list too: it now chooses
    between the two approved channels and must read the Authorization header to
    do so. What remains is the cookie path, checked exactly as before.
    """
    code = _function_source(PACKAGE / "dependencies.py", name)
    for forbidden in ("query_params", "headers.get", "agency_id", "OperatorContext("):
        assert forbidden not in code, f"{name} references {forbidden!r}"


def test_the_session_path_accepts_only_the_cookie():
    code = _function_source(PACKAGE / "dependencies.py", "optional_session")
    assert "cookies" in code, "the session channel accepts only the cookie"


@pytest.mark.parametrize("name", ["optional_session", "current_session"])
def test_the_session_path_has_no_legacy_basic_fallback(name):
    """Basic must never be a way to obtain an operator *session*.

    require_operator may choose the legacy channel - that is Task 15's whole
    point - but it must not be able to manufacture a session from it. These two
    functions are what a session is, and they stay cookie-only.
    """
    code = _function_source(PACKAGE / "dependencies.py", name)
    for forbidden in ("HTTPBasic", "ADMIN_USER", "ADMIN_PASS", "legacy_basic", "Authorization"):
        assert forbidden not in code, f"{name} references {forbidden!r}"


# ---------------------------------------------------------------------------
# C2 - the legacy-Basic compatibility context
# ---------------------------------------------------------------------------

COMPAT = "legacy_basic_agency_context"


def test_c2_the_compatibility_context_requires_basic_authentication():
    """It cannot be obtained without the credential that justifies it."""
    from operator_auth import dependencies

    parameters = inspect.signature(dependencies.legacy_basic_agency_context).parameters
    assert len(parameters) == 1, parameters
    default = list(parameters.values())[0].default
    assert getattr(default, "dependency", None).__name__ == "require_admin"


def test_c2_the_compatibility_context_takes_no_caller_selector():
    """No agency, slug, header or body parameter anywhere in the signature."""
    from operator_auth import dependencies

    parameters = inspect.signature(dependencies.legacy_basic_agency_context).parameters
    for forbidden in ("agency_id", "agency", "slug", "request", "headers", "body", "tenant"):
        assert forbidden not in parameters, forbidden


def test_c2_the_agency_is_resolved_server_side_not_hard_coded():
    code = _function_source(PACKAGE / "dependencies.py", COMPAT)
    assert "resolve_default_agency_id(cur)" in code, code
    # No numeric literal may stand in for the agency. bool is a subclass of
    # int in Python, so is_platform_admin=False must not be mistaken for one.
    tree = ast.parse(code)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, int):
            if isinstance(node.value, bool):
                continue
            raise AssertionError(f"numeric literal {node.value} in {COMPAT}")


def test_c2_the_context_is_agency_bound_and_never_platform_admin():
    code = _function_source(PACKAGE / "dependencies.py", COMPAT)
    assert "is_platform_admin=False" in code, code
    assert "auth_channel='legacy_basic'" in code, code
    assert "user_id=None" in code and "session_id=None" in code, code
    assert "role=LEGACY_BASIC_ROLE" in code, code
    from operator_auth import dependencies

    assert dependencies.LEGACY_BASIC_ROLE == "agency_owner"


def test_c2_the_context_is_an_operator_context_not_a_system_context():
    """NBA is operator work. SystemAgencyContext is for server-originated work."""
    code = _function_source(PACKAGE / "dependencies.py", COMPAT)
    assert "OperatorContext(" in code
    assert "SystemAgencyContext" not in code
    module = _executable_source(PACKAGE / "dependencies.py")
    assert "SystemAgencyContext" not in module


def test_c2_the_resolved_context_scopes_to_one_agency_and_never_widens():
    """End to end: what the dependency returns, fed to the scope builder."""
    ctx = OperatorContext(
        user_id=None,
        agency_id=4242,
        role="agency_owner",
        is_platform_admin=False,
        session_id=None,
        auth_channel="legacy_basic",
    )
    for table in ("contacts", "leads", "activities", "tasks"):
        source, params = scoped_source(ctx, table, "x")
        assert "TRUE" not in source, source
        assert params == [4242]
        assert "assigned_agent_id" not in source


def test_c2_a_missing_default_agency_fails_the_dependency_closed():
    """No active STIMA360 agency means no context, not a fallback."""
    class _EmptyCursor:
        def execute(self, sql, params=None):
            self.sql = sql

        def fetchone(self):
            return None

    with pytest.raises(ConflictError):
        from core.scope import resolve_default_agency_id

        resolve_default_agency_id(_EmptyCursor())


def test_c2_gate_ma1_is_recorded_where_the_bridge_is_defined():
    """The compatibility path must carry its own expiry note.

    P26-1 does not certify platform-wide isolation while these routes exist,
    and the place a maintainer will read is the function itself.
    """
    source = (PACKAGE / "dependencies.py").read_text(encoding="utf-8")
    assert "GATE-MA1" in source
    assert "D-1" in source


def test_c2_the_d1_allowlist_is_unchanged():
    """The operator session reaches CORE and operator-auth, and nothing else.

    Task 15 put require_operator into main.py by design - on the CORE mount
    only. The rule is therefore no longer "it must not appear" but "it must
    appear on exactly one mount", which is the allowlist restated.
    """
    main_source = (ROOT / "main.py").read_text(encoding="utf-8")
    mounts = [
        line for line in main_source.splitlines()
        if "include_router" in line and "require_operator" in line
    ]
    assert len(mounts) == 1, mounts
    assert "core_router" in mounts[0], mounts[0]

    nba = (ROOT / "next_best_action" / "router.py").read_text(encoding="utf-8")
    assert "require_operator" not in nba, "NBA must stay on the legacy Basic channel"
    assert COMPAT in nba


def test_c2_the_nba_router_still_authenticates_with_basic():
    """The OS Shell Oggi view sends Basic; that must keep working."""
    main_source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "app.include_router(next_best_action_router, dependencies=[Depends(require_admin)])" in main_source


def test_router_carries_no_router_level_auth_dependency():
    """login and logout must stay reachable unauthenticated."""
    source = (PACKAGE / "router.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "APIRouter":
            kwargs = {kw.arg for kw in node.keywords}
            assert "dependencies" not in kwargs, (
                "a router-level dependency would make /login unreachable"
            )
            return
    raise AssertionError("no APIRouter(...) call found in router.py")


def test_task_4_context_module_defines_no_factory():
    """system_context_for_public_stima lives in core/scope.py (Task 10).

    Keeping the factory out of this module is what stops a context being built
    from anything other than a server-side database lookup.
    """
    source = (PACKAGE / "context.py").read_text(encoding="utf-8")
    assert "system_context_for_public_stima" not in source
    assert not hasattr(context_module, "system_context_for_public_stima")
    assert not hasattr(permissions_module, "system_context_for_public_stima")


# ===========================================================================
# Task 10 - core/scope.py
#
# The mandatory agency predicate builder and the server-only public-STIMA
# context factory. Design spec sections 2.5.1 and 9.1; spec section 15 items
# 49, 52b and 52d.
#
# Everything below is offline: no database, no driver, no FastAPI. The factory
# is exercised against a recording fake cursor, the same discipline used by
# tests/test_p26_1_operator_auth.py.
# ===========================================================================

import inspect

from core.exceptions import ConflictError, CoreError
from core.scope import (
    AGENT_ASSIGNABLE,
    SCOPED_TABLES,
    SYSTEM_CONTEXT_FUNCTIONS,
    ProgrammingError,
    scoped_predicate,
    scoped_source,
    system_context_for_public_stima,
)

SCOPE_PATH = ROOT / "core" / "scope.py"

AGENCY = 10
OTHER_AGENCY = 20
AGENT_USER = 7


def _owner(agency_id: int = AGENCY) -> OperatorContext:
    return _operator(role="agency_owner", agency_id=agency_id)


def _admin(agency_id: int = AGENCY) -> OperatorContext:
    return _operator(role="agency_admin", agency_id=agency_id)


def _agent(agency_id: int = AGENCY, user_id: int = AGENT_USER) -> OperatorContext:
    return _operator(role="agent", agency_id=agency_id, user_id=user_id)


def _unbound_platform_admin() -> OperatorContext:
    """The only context entitled to a cross-agency query."""
    return _operator(agency_id=None, role=None, is_platform_admin=True)


def _bound_platform_admin(agency_id: int = AGENCY, role=None) -> OperatorContext:
    """A platform admin who does hold a membership: scoped like anyone else."""
    return _operator(agency_id=agency_id, role=role, is_platform_admin=True)


def _system(agency_id: int = AGENCY) -> SystemAgencyContext:
    return SystemAgencyContext(agency_id=agency_id, origin="public_stima")


ALL_TABLES = ("contacts", "leads", "activities", "tasks")


class _RecordingCursor:
    """Records every execute() and replays one queued row.

    Deliberately not a Mock: the assertions below are about the exact SQL and
    the exact parameters, and a Mock would happily accept a call shape that a
    real cursor would reject.
    """

    def __init__(self, row=None):
        self._row = row
        self.calls: list[tuple[str, object]] = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchone(self):
        return self._row

    @property
    def sql(self) -> str:
        assert len(self.calls) == 1, f"expected one statement, got {len(self.calls)}"
        return " ".join(self.calls[0][0].split())

    @property
    def params(self):
        assert len(self.calls) == 1, f"expected one statement, got {len(self.calls)}"
        return self.calls[0][1]


# ---------------------------------------------------------------------------
# AST helpers - the structural, fail-closed half of this task.
#
# Behavioural tests can only prove the branches they happen to reach. These
# read every return statement in the builder, including any a test forgets to
# exercise, which is what makes "no branch omits the predicate" a property
# rather than a sample.
# ---------------------------------------------------------------------------

def _scope_function(name: str) -> ast.FunctionDef:
    tree = ast.parse(SCOPE_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"core/scope.py defines no function named {name!r}")


def _return_nodes(fn) -> list[ast.Return]:
    return [node for node in ast.walk(fn) if isinstance(node, ast.Return)]


def _returned_sql_expressions(fn) -> list[str]:
    """The unparsed first element of every ``return <sql>, <params>``."""
    expressions = []
    for node in _return_nodes(fn):
        assert node.value is not None, "a bare `return` would yield None, not a source"
        assert isinstance(node.value, ast.Tuple), (
            f"scoped_source must return a 2-tuple; found {ast.unparse(node.value)!r}"
        )
        assert len(node.value.elts) == 2, ast.unparse(node.value)
        expressions.append(ast.unparse(node.value.elts[0]))
    return expressions


def _guard_of_return_containing(fn, needle: str):
    """The ``if`` test that directly guards the return carrying ``needle``."""
    for node in ast.walk(fn):
        if isinstance(node, ast.If):
            for statement in node.body:
                if isinstance(statement, ast.Return) and needle in ast.unparse(statement):
                    return node.test
    return None


def _sql_string_constants(fn) -> list[str]:
    """Every plain string literal inside a function, docstring excluded."""
    body = fn.body[1:] if (
        fn.body
        and isinstance(fn.body[0], ast.Expr)
        and isinstance(fn.body[0].value, ast.Constant)
        and isinstance(fn.body[0].value.value, str)
    ) else fn.body
    found = []
    for statement in body:
        for node in ast.walk(statement):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                found.append(node.value)
    return found


# ---------------------------------------------------------------------------
# C7 - the constants
# ---------------------------------------------------------------------------

def test_c7_scoped_tables_is_the_exact_four():
    assert SCOPED_TABLES == frozenset({"contacts", "leads", "activities", "tasks"})
    assert isinstance(SCOPED_TABLES, frozenset), "a mutable set could be widened at runtime"


def test_c7_agent_assignable_is_the_exact_two():
    """activities and tasks carry no assigned_agent_id in P26-1 (spec 3.3)."""
    assert AGENT_ASSIGNABLE == frozenset({"contacts", "leads"})
    assert isinstance(AGENT_ASSIGNABLE, frozenset)
    assert AGENT_ASSIGNABLE < SCOPED_TABLES, "assignable tables must be scoped tables"


def test_c7_system_context_functions_has_exactly_one_member():
    """Spec section 15 item 52b: a second system-context writer must be a
    deliberate edit that fails this test until it is acknowledged."""
    assert SYSTEM_CONTEXT_FUNCTIONS == frozenset({"bridge_public_stima"})
    assert len(SYSTEM_CONTEXT_FUNCTIONS) == 1
    assert isinstance(SYSTEM_CONTEXT_FUNCTIONS, frozenset)


# ---------------------------------------------------------------------------
# C8 - the scoped-table guard
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("table", ALL_TABLES)
def test_c8_every_scoped_table_is_accepted(table):
    source, params = scoped_source(_owner(), table, "x")
    assert source.startswith(f"{table} x WHERE ")
    assert params == [AGENCY]


@pytest.mark.parametrize(
    "table",
    ["agencies", "operator_users", "stime", "properties", "contacts_archive",
     "CONTACTS", "contacts; DROP TABLE contacts", "", "contact"],
)
def test_c8_an_unscoped_table_is_refused(table):
    with pytest.raises(ProgrammingError):
        scoped_source(_owner(), table, "x")


def test_c8_the_table_guard_fires_for_every_context_type():
    """Fail closed for the unbound platform admin too, whose branch is first."""
    for ctx in (_owner(), _agent(), _unbound_platform_admin(), _system()):
        with pytest.raises(ProgrammingError):
            scoped_source(ctx, "agencies", "a")


def test_c8_the_table_guard_error_is_not_a_client_facing_domain_error():
    """A table name is a developer literal, never request input.

    core/router.py translates NotFoundError, ConflictError and ValidationError
    into 4xx responses. A builder called with a table it does not scope is a
    defect in this codebase, and must surface as a failure rather than be
    dressed up as a client mistake, so it deliberately sits outside CoreError.
    """
    assert issubclass(ProgrammingError, Exception)
    assert not issubclass(ProgrammingError, CoreError)


def test_c8_every_accepted_table_produces_a_predicate_for_every_context():
    """The exhaustive sweep: no (context, table) pair escapes the WHERE."""
    contexts = [
        _owner(), _admin(), _agent(),
        _bound_platform_admin(), _unbound_platform_admin(), _system(),
    ]
    for ctx in contexts:
        for table in ALL_TABLES:
            source, params = scoped_source(ctx, table, "z")
            assert " WHERE " in source, (ctx, table, source)
            assert source != f"{table} z", (ctx, table)
            assert source.count("%s") == len(params), (ctx, table, source, params)


# ---------------------------------------------------------------------------
# C9 - the predicate, per role and per table
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ctx_factory", [_owner, _admin])
@pytest.mark.parametrize("table", ALL_TABLES)
def test_c9_owner_and_admin_get_the_plain_agency_predicate(ctx_factory, table):
    source, params = scoped_source(ctx_factory(), table, "c")
    assert source == f"{table} c WHERE c.agency_id = %s"
    assert params == [AGENCY]


def test_c9_the_predicate_is_alias_qualified():
    """An unqualified agency_id would be ambiguous the moment a JOIN appears."""
    source, _ = scoped_source(_owner(), "contacts", "c")
    assert "c.agency_id = %s" in source
    assert "WHERE agency_id" not in source


@pytest.mark.parametrize("table", ["contacts", "leads"])
def test_c9_agent_on_an_assignable_table_gets_both_filters(table):
    source, params = scoped_source(_agent(), table, "c")
    assert source == (
        f"{table} c WHERE c.agency_id = %s AND c.assigned_agent_id = %s"
    )
    assert params == [AGENCY, AGENT_USER]


@pytest.mark.parametrize("table", ["activities", "tasks"])
def test_c9_agent_on_a_non_assignable_table_gets_the_agency_predicate_only(table):
    """Spec 9.1, stated as a decision rather than an omission: activities and
    tasks have no assigned_agent_id, and their free-text assigned_to is written
    by automation. Narrowing them belongs to P26-2."""
    source, params = scoped_source(_agent(), table, "t")
    assert source == f"{table} t WHERE t.agency_id = %s"
    assert params == [AGENCY]
    assert "assigned" not in source


def test_c9_the_agent_branch_never_invents_assigned_to():
    """The column exists on activities and tasks but means something else."""
    for table in ALL_TABLES:
        source, _ = scoped_source(_agent(), table, "t")
        assert "assigned_to" not in source, table
    assert "assigned_to" not in _executable_source(SCOPE_PATH)


def test_c9_the_agent_filter_uses_the_context_user_not_a_literal():
    source, params = scoped_source(_agent(user_id=4242), "contacts", "c")
    assert params == [AGENCY, 4242]
    assert "4242" not in source


def test_c9_the_agency_comes_from_the_context_not_the_caller():
    _, params = scoped_source(_owner(agency_id=OTHER_AGENCY), "leads", "l")
    assert params == [OTHER_AGENCY]


def test_c9_an_unbound_non_platform_admin_is_refused_rather_than_widened():
    """require_agency() is the fail-closed path: no agency, no query."""
    ctx = _operator(agency_id=None, role="agency_owner", is_platform_admin=False)
    with pytest.raises(PlatformAdminAgencyRequired):
        scoped_source(ctx, "contacts", "c")


# ---------------------------------------------------------------------------
# C9 - platform admin
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("table", ALL_TABLES)
def test_c9_an_unbound_platform_admin_sees_every_agency(table):
    source, params = scoped_source(_unbound_platform_admin(), table, "c")
    assert source == f"{table} c WHERE TRUE"
    assert params == []


@pytest.mark.parametrize("role", [None, "agency_owner", "agency_admin", "agent"])
@pytest.mark.parametrize("table", ALL_TABLES)
def test_c9_a_bound_platform_admin_is_scoped_and_never_sees_where_true(role, table):
    """Holding a membership is what decides it, not the flag."""
    source, params = scoped_source(_bound_platform_admin(role=role), table, "c")
    assert "TRUE" not in source, source
    assert source.startswith(f"{table} c WHERE c.agency_id = %s")
    assert params[0] == AGENCY


def test_c9_where_true_needs_the_flag_as_well_as_the_missing_agency():
    """Neither condition alone opens the cross-agency branch."""
    # flag set, agency bound -> scoped
    assert "TRUE" not in scoped_source(_bound_platform_admin(), "contacts", "c")[0]
    # agency unbound, flag clear -> refused outright
    with pytest.raises(PlatformAdminAgencyRequired):
        scoped_source(
            _operator(agency_id=None, role="agent", is_platform_admin=False),
            "contacts",
            "c",
        )


def test_c9_a_truthy_non_boolean_flag_does_not_open_the_cross_agency_branch():
    """Fail closed on an identity check, not on truthiness.

    A scope is server-built, so this should be unreachable; the point is that
    if it ever were reached, the widening branch stays shut.
    """

    class _Sloppy:
        agency_id = None
        role = None
        user_id = None
        is_platform_admin = 1  # truthy, but not True

        def require_agency(self):
            raise PlatformAdminAgencyRequired("unbound")

    with pytest.raises(PlatformAdminAgencyRequired):
        scoped_source(_Sloppy(), "contacts", "c")


# ---------------------------------------------------------------------------
# C9 - SystemAgencyContext
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("table", ALL_TABLES)
def test_c9_system_context_gets_the_single_agency_predicate(table):
    source, params = scoped_source(_system(), table, "c")
    assert source == f"{table} c WHERE c.agency_id = %s"
    assert params == [AGENCY]


@pytest.mark.parametrize("table", ALL_TABLES)
def test_c9_system_context_can_never_reach_where_true(table):
    """It presents is_platform_admin=False and a resolved integer agency, so
    the widening branch is structurally out of reach (spec 2.5.1)."""
    ctx = _system()
    assert ctx.is_platform_admin is False
    assert ctx.agency_id is not None
    source, params = scoped_source(ctx, table, "c")
    assert "TRUE" not in source
    assert params == [ctx.agency_id]


def test_c9_system_context_gets_no_agent_narrowing():
    """role is None and user_id is None: the agent branch must not fire."""
    for table in ("contacts", "leads"):
        source, params = scoped_source(_system(), table, "c")
        assert "assigned_agent_id" not in source
        assert len(params) == 1
        assert None not in params


def test_c9_system_context_carries_its_own_agency():
    _, params = scoped_source(_system(agency_id=OTHER_AGENCY), "contacts", "c")
    assert params == [OTHER_AGENCY]


# ---------------------------------------------------------------------------
# C10 - structural fail-closed properties of scoped_source
# ---------------------------------------------------------------------------

def test_c10_every_return_path_emits_a_where():
    """Read from the source, so an unexercised branch is still covered."""
    expressions = _returned_sql_expressions(_scope_function("scoped_source"))
    assert expressions, "scoped_source returns nothing"
    for expression in expressions:
        assert "WHERE" in expression, f"a return path omits WHERE: {expression}"


def test_c10_no_branch_returns_a_bare_table_source():
    """Explicitly: f'{table} {alias}' on its own must appear nowhere."""
    expressions = _returned_sql_expressions(_scope_function("scoped_source"))
    for expression in expressions:
        normalised = expression.lstrip("f").strip("'\"")
        assert normalised != "{table} {alias}", f"bare table source: {expression}"
        assert not normalised.rstrip().endswith("{alias}"), (
            f"the source ends at the alias, with no predicate: {expression}"
        )


def test_c10_the_where_keyword_is_emitted_by_the_builder_itself():
    """Callers append ' AND ...'; none of them may have to add the WHERE."""
    for ctx in (_owner(), _agent(), _unbound_platform_admin(), _system()):
        for table in ALL_TABLES:
            source, _ = scoped_source(ctx, table, "c")
            head, _, tail = source.partition(" WHERE ")
            assert head == f"{table} c", source
            assert tail.strip(), f"empty predicate after WHERE: {source!r}"


def test_c10_scoped_source_has_a_single_return():
    """Task 11 moved the branching into scoped_predicate.

    scoped_source is now the one place that emits the WHERE keyword, and it
    does so unconditionally. The role branches are asserted below against
    scoped_predicate, where they now live - retargeted, not relaxed.
    """
    returns = _return_nodes(_scope_function("scoped_source"))
    assert len(returns) == 1, f"expected one return, found {len(returns)}"
    assert "WHERE" in _returned_sql_expressions(_scope_function("scoped_source"))[0]


def test_c10_every_predicate_return_path_is_non_empty():
    """No branch of scoped_predicate may yield an empty or whitespace string."""
    fn = _scope_function("scoped_predicate")
    expressions = _returned_sql_expressions(fn)
    assert expressions, "scoped_predicate returns nothing"
    for expression in expressions:
        normalised = expression.lstrip("f").strip("'\"")
        assert normalised.strip(), f"empty predicate branch: {expression}"


def test_c10_there_is_exactly_one_where_true_branch():
    expressions = _returned_sql_expressions(_scope_function("scoped_predicate"))
    widening = [e for e in expressions if "TRUE" in e.upper()]
    assert len(widening) == 1, f"expected one widening branch, found {widening}"


def test_c10_the_where_true_branch_is_guarded_by_both_conditions():
    """The guard must be a conjunction of the flag and the missing agency."""
    fn = _scope_function("scoped_predicate")
    test_node = _guard_of_return_containing(fn, "TRUE")
    assert test_node is not None, "the WHERE TRUE return is not inside an if"
    assert isinstance(test_node, ast.BoolOp) and isinstance(test_node.op, ast.And), (
        f"the widening guard must be a conjunction; found {ast.unparse(test_node)}"
    )
    guard = ast.unparse(test_node)
    assert "is_platform_admin" in guard, guard
    assert "agency_id is None" in guard, guard
    assert len(test_node.values) == 2, guard


def test_c10_the_table_guard_is_the_first_statement():
    """An unscoped table must be refused before any predicate is chosen."""
    fn = _scope_function("scoped_predicate")
    body = fn.body[1:] if isinstance(fn.body[0], ast.Expr) else fn.body
    first = body[0]
    assert isinstance(first, ast.If), ast.unparse(first)
    assert "SCOPED_TABLES" in ast.unparse(first.test), ast.unparse(first.test)
    assert any(isinstance(node, ast.Raise) for node in ast.walk(first))


def test_c10_the_builder_interpolates_no_caller_value_into_sql():
    """table and alias are developer literals checked against SCOPED_TABLES;
    every runtime value travels as a %s parameter, never as text."""
    for ctx in (_owner(), _agent(), _system()):
        source, params = scoped_source(ctx, "contacts", "c")
        for value in params:
            assert str(value) not in source, (source, params)


def test_c10_scoped_source_returns_a_list_of_params_not_a_tuple():
    """Callers concatenate their own filters onto it (spec 9.2)."""
    for ctx in (_owner(), _agent(), _unbound_platform_admin(), _system()):
        _, params = scoped_source(ctx, "contacts", "c")
        assert isinstance(params, list), type(params)


def test_c10_the_builder_names_no_table_it_does_not_scope():
    """Every SQL-ish literal in the builder stays inside the predicate shape."""
    literals = _sql_string_constants(_scope_function("scoped_predicate"))
    literals += _sql_string_constants(_scope_function("scoped_source"))
    assert literals, "the builder has no literals to check - the rule is vacuous"
    for literal in literals:
        for table in ("agencies", "operator_users", "agency_memberships",
                      "operator_sessions", "stime"):
            assert table not in literal, literal


# ---------------------------------------------------------------------------
# C11 - the public-STIMA context factory
# ---------------------------------------------------------------------------

def test_c11_the_signature_accepts_a_cursor_and_nothing_else():
    """Spec section 15 item 52d: the agency cannot be an argument."""
    parameters = list(inspect.signature(system_context_for_public_stima).parameters)
    assert parameters == ["cur"], parameters


@pytest.mark.parametrize(
    "forbidden",
    ["agency_id", "agency", "slug", "client", "tenant", "request", "headers",
     "body", "payload", "origin"],
)
def test_c11_the_signature_declares_no_caller_selector(forbidden):
    parameters = inspect.signature(system_context_for_public_stima).parameters
    assert forbidden not in parameters, forbidden


def test_c11_it_resolves_the_agency_from_the_agencies_table():
    cur = _RecordingCursor({"id": 4242})
    system_context_for_public_stima(cur)
    assert "FROM agencies" in cur.sql


def test_c11_the_query_filters_on_the_default_slug():
    cur = _RecordingCursor({"id": 4242})
    system_context_for_public_stima(cur)
    assert "slug = %s" in cur.sql, cur.sql
    assert "stima360" in [str(value) for value in cur.params]


def test_c11_the_slug_matches_the_single_source_of_truth():
    from operator_auth import enums

    cur = _RecordingCursor({"id": 4242})
    system_context_for_public_stima(cur)
    assert list(cur.params) == [enums.DEFAULT_AGENCY_SLUG]
    assert enums.DEFAULT_AGENCY_SLUG == "stima360"


def test_c11_the_query_filters_on_an_active_status():
    """A suspended Default Agency must not keep serving public estimations."""
    cur = _RecordingCursor({"id": 4242})
    system_context_for_public_stima(cur)
    assert "status = 'active'" in cur.sql, cur.sql


def test_c11_it_returns_a_system_agency_context_carrying_the_resolved_id():
    cur = _RecordingCursor({"id": 4242})
    ctx = system_context_for_public_stima(cur)
    assert isinstance(ctx, SystemAgencyContext)
    assert ctx.agency_id == 4242
    assert ctx.origin == "public_stima"


def test_c11_the_id_is_the_row_value_and_not_a_default():
    """Two different rows must give two different agencies."""
    first = system_context_for_public_stima(_RecordingCursor({"id": 1}))
    second = system_context_for_public_stima(_RecordingCursor({"id": 99}))
    assert (first.agency_id, second.agency_id) == (1, 99)


def test_c11_no_active_default_agency_raises_conflict():
    with pytest.raises(ConflictError):
        system_context_for_public_stima(_RecordingCursor(None))


def test_c11_it_never_returns_none_and_never_falls_back():
    """Silently returning None would push an unscoped write downstream."""
    cur = _RecordingCursor(None)
    try:
        result = system_context_for_public_stima(cur)
    except ConflictError:
        result = "raised"
    assert result == "raised"


def test_c11_the_factory_only_reads():
    """It resolves an agency; creating or repairing one is a migration's job.

    Asserted against resolve_default_agency_id, which owns the statement since
    Task 11 shared it with the legacy-Basic compatibility context.
    """
    literals = _sql_string_constants(_scope_function("resolve_default_agency_id"))
    literals += _sql_string_constants(_scope_function("system_context_for_public_stima"))
    assert any("SELECT" in literal for literal in literals), "no statement to check"
    for literal in literals:
        upper = literal.upper()
        for verb in ("INSERT", "UPDATE ", "DELETE", "CREATE", "ALTER", "UPSERT"):
            assert verb not in upper, literal


def test_c11_it_issues_exactly_one_statement():
    cur = _RecordingCursor({"id": 4242})
    system_context_for_public_stima(cur)
    assert len(cur.calls) == 1


def test_c11_the_context_it_returns_scopes_to_that_agency():
    """End to end: factory output straight into the builder."""
    ctx = system_context_for_public_stima(_RecordingCursor({"id": 4242}))
    source, params = scoped_source(ctx, "contacts", "c")
    assert source == "contacts c WHERE c.agency_id = %s"
    assert params == [4242]


def test_c11_it_is_the_only_factory_in_the_module():
    """Spec 2.5.1: exactly one way to obtain a SystemAgencyContext."""
    tree = ast.parse(SCOPE_PATH.read_text(encoding="utf-8"))
    constructing = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", None) == "SystemAgencyContext"
    ]
    assert len(constructing) == 1, "one construction site only"


# ---------------------------------------------------------------------------
# C12 - core/scope.py dependency constraints
# ---------------------------------------------------------------------------

def test_c12_scope_imports_only_what_the_boundary_allows():
    imported = _imported_modules(SCOPE_PATH)
    assert imported <= {"__future__", "core", "operator_auth"}, imported


@pytest.mark.parametrize(
    "forbidden",
    ["fastapi", "starlette", "pydantic", "psycopg2", "database", "owner", "requests"],
)
def test_c12_scope_imports_no_transport_or_driver(forbidden):
    assert forbidden not in _imported_modules(SCOPE_PATH), forbidden


def test_c12_scope_does_not_import_the_http_or_service_layers():
    """operator_auth.dependencies and .service belong to the request path."""
    tree = ast.parse(SCOPE_PATH.read_text(encoding="utf-8"))
    modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    for forbidden in ("operator_auth.dependencies", "operator_auth.service",
                      "operator_auth.router", "core.router", "core.service",
                      "core.repository", "core.database"):
        assert forbidden not in modules, forbidden


def test_c12_scope_never_constructs_an_operator_context():
    """Only the request dependency may mint an operator scope (spec 8.1)."""
    code = _executable_source(SCOPE_PATH)
    assert "OperatorContext(" not in code
    assert "OperatorContext" not in code


def test_c12_scope_reads_no_request_state():
    code = _executable_source(SCOPE_PATH)
    for forbidden in ("Request", "headers", "query_params", "cookies", "Depends"):
        assert forbidden not in code, f"core/scope.py references {forbidden!r}"


def test_c12_scope_opens_no_connection_of_its_own():
    """The factory receives the caller's cursor so it shares the transaction."""
    code = _executable_source(SCOPE_PATH)
    for forbidden in ("get_connection", "core_cursor", "operator_cursor", "connect("):
        assert forbidden not in code, f"core/scope.py references {forbidden!r}"


def test_c12_scope_logs_nothing():
    """A scope carries agency identifiers; nothing here should emit them."""
    code = _executable_source(SCOPE_PATH)
    for forbidden in ("logging", "print(", "sys.stderr"):
        assert forbidden not in code, f"core/scope.py references {forbidden!r}"


def test_c12_the_module_exposes_no_unexpected_public_surface():
    import core.scope as scope_module

    public = {name for name in vars(scope_module) if not name.startswith("_")}
    imported = {"AgencyScope", "SystemAgencyContext", "ConflictError", "annotations",
                "DEFAULT_AGENCY_SLUG"}
    defined = {
        "SCOPED_TABLES", "AGENT_ASSIGNABLE", "SYSTEM_CONTEXT_FUNCTIONS",
        "ProgrammingError", "scoped_predicate", "scoped_source",
        "resolve_default_agency_id", "system_context_for_public_stima",
    }
    assert public <= imported | defined, public - (imported | defined)
    assert defined <= public, defined - public
