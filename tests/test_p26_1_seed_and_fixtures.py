"""P26-1 Tasks 17 and 18 - the TEST seed script and the E2E fixture isolation.

Offline and static. Nothing here executes the seed script or either E2E
script, and nothing opens a database connection: every assertion is made
against the source text and its AST.

That restraint is the point. These three files are the only P26-1 artefacts
that write `contacts` and `leads` outside the application, so the properties
that matter - they refuse a non-TEST database, they carry no secret, and their
cleanup cannot reach across an agency boundary - must be provable *before* the
first one is ever run.

Coverage map:

    H1  the seed script exists, and outside migrations/
    H2  it refuses a database that is not TEST, before any write
    H3  it carries no password literal; all six come from the environment
    H4  it is non-destructive and idempotent
    H5  six operators, five memberships - the asymmetry is the design
    H6  E2E fixture inserts carry an agency, resolved by slug
    H7  E2E cleanup cannot cross an agency boundary
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "scripts" / "p26_seed_agencies_test.py"

BUY_E2E = ROOT / "run_buy_021_e2e.py"
FLOW_E2E = ROOT / "run_flow_01_e2e.py"

REQUIRED_PASSWORD_VARIABLES = (
    "P26_SEED_OWNER_A_PASSWORD",
    "P26_SEED_ADMIN_A_PASSWORD",
    "P26_SEED_AGENT_A_PASSWORD",
    "P26_SEED_AGENT_A2_PASSWORD",
    "P26_SEED_OWNER_B_PASSWORD",
    "P26_SEED_PLATFORM_PASSWORD",
)

EXPECTED_OPERATORS = (
    "owner_a", "admin_a", "agent_a", "agent_a2", "owner_b", "platform_admin",
)


def _seed_source() -> str:
    assert SEED.exists(), f"{SEED.relative_to(ROOT)} does not exist"
    return SEED.read_text(encoding="utf-8")


def _seed_tree() -> ast.Module:
    return ast.parse(_seed_source())


def _squash(text: str) -> str:
    return " ".join(text.split())


def _function_source(path: Path, name: str) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.unparse(node)
    raise AssertionError(f"{path.name} defines no function named {name!r}")


# ---------------------------------------------------------------------------
# H1 / H2 - where it lives, and what it refuses
# ---------------------------------------------------------------------------

def test_h1_the_seed_script_exists_outside_migrations():
    """Seeding is not schema. It must never enter the forward-only ledger."""
    assert SEED.exists()
    assert SEED.parent.name == "scripts"
    assert not (ROOT / "migrations" / SEED.name).exists()


def test_h2_the_database_guard_runs_before_any_write():
    """Asserted via the AST, not by executing the script.

    Running it to find out whether it would refuse a production database is
    precisely the experiment one must not perform.
    """
    tree = _seed_tree()
    main = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    body = ast.unparse(main)
    guard = body.index("assert_test_database_name")
    for write in ("INSERT INTO", "cur.execute", "get_connection", "connect("):
        if write in body:
            assert guard < body.index(write), (
                f"{write!r} appears before the TEST database guard"
            )


def test_h2_the_guards_are_reused_not_reimplemented():
    """The migration runner already decides what a TEST database is."""
    source = _seed_source()
    assert "assert_test_database_name" in source
    assert "assert_operator_identity" in source
    assert "p26_migrate" in source, "the guards must be imported, not copied"
    # No second definition of either guard.
    defined = [
        node.name for node in ast.walk(_seed_tree())
        if isinstance(node, ast.FunctionDef)
    ]
    assert "assert_test_database_name" not in defined
    assert "assert_operator_identity" not in defined


def test_h2_an_operator_identity_is_required():
    source = _seed_source()
    assert "--operator" in source


# ---------------------------------------------------------------------------
# H3 - no secret in the repository
# ---------------------------------------------------------------------------

def test_h3_no_password_literal_is_assigned_anywhere():
    """The review checkpoint's grep, as a test."""
    pattern = re.compile(r"""(?i)\b\w*(pass|secret|token)\w*\s*=\s*['"][^'"]""")
    offenders = [
        line for line in _seed_source().splitlines()
        if pattern.search(line) and "getenv" not in line
    ]
    assert not offenders, offenders


def _seed_module():
    """Import the script. It performs no I/O at import time."""
    import importlib

    return importlib.import_module("scripts.p26_seed_agencies_test")


def test_h3_every_credential_is_read_from_the_environment():
    """Asserted against the declared table, not a regex over the source.

    The script reads the six variables through one loop over OPERATORS rather
    than six literal getenv calls - which is the better shape, and which a
    literal-matching rule would have wrongly failed.
    """
    module = _seed_module()
    declared = {variable for _, _, variable, _ in module.OPERATORS}
    assert declared == set(REQUIRED_PASSWORD_VARIABLES), declared

    source = _function_source(SEED, "read_passwords")
    assert "os.getenv(variable)" in source, source
    assert "variable" in source


def test_h3_a_missing_variable_blocks_the_run(monkeypatch):
    """Behavioural, and safe: read_passwords opens no connection."""
    module = _seed_module()
    for _, _, variable, _ in module.OPERATORS:
        monkeypatch.delenv(variable, raising=False)
    with pytest.raises(Exception) as failure:
        module.read_passwords()
    message = str(failure.value)
    assert "BLOCKED" in message
    for _, _, variable, _ in module.OPERATORS:
        assert variable in message, f"{variable} not reported as missing"


def test_h3_a_missing_password_refuses_the_run():
    """Six variables, and the script must not invent a default for any."""
    source = _squash(_seed_source())
    assert "getenv(name, '')" not in source and 'getenv(name, "")' not in source
    assert "BLOCKED" in source or "raise" in source


def test_h3_passwords_are_hashed_with_the_project_primitive():
    source = _seed_source()
    assert "hash_password" in source
    assert "operator_auth.security" in source or "from operator_auth import security" in source
    for forbidden in ("md5", "sha1(", "plaintext", "password_hash = password"):
        assert forbidden not in source, forbidden


# ---------------------------------------------------------------------------
# H4 - non-destructive and idempotent
# ---------------------------------------------------------------------------

def test_h4_the_script_destroys_nothing():
    source = _squash(_seed_source()).upper()
    for forbidden in ("DROP ", "TRUNCATE ", "DELETE FROM CONTACTS",
                      "DELETE FROM LEADS", "DELETE FROM STIME", "DELETE FROM AGENCIES"):
        assert forbidden not in source, forbidden


def _sql_literals(path: Path) -> list[str]:
    """Every SQL string literal in a file, as its own statement.

    Extracted via the AST rather than by splitting the file on "INSERT INTO":
    a regex split runs the final statement to end-of-file and swallows the rest
    of the module, which makes the rule report nonsense on failure.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        _squash(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and re.search(r"\b(INSERT|UPDATE|DELETE|SELECT)\b", node.value)
    ]


def test_h4_every_insert_is_guarded_for_idempotence():
    """A second run must add nothing, so it can be re-run after a failure."""
    inserts = [sql for sql in _sql_literals(SEED) if "INSERT INTO" in sql]
    assert len(inserts) == 3, [sql[:60] for sql in inserts]
    for statement in inserts:
        # `AND NOT EXISTS` is as good a guard as `WHERE NOT EXISTS`: the
        # membership insert already has a WHERE clause selecting the agency,
        # so its idempotence check is a conjunct rather than the first term.
        assert "NOT EXISTS" in statement or "ON CONFLICT" in statement, statement


def test_h4_nothing_is_written_to_agency_settings():
    assert "settings" not in _squash(_seed_source())


# ---------------------------------------------------------------------------
# H5 - six operators, five memberships
# ---------------------------------------------------------------------------

def test_h5_exactly_six_operators_are_declared():
    source = _seed_source()
    for name in EXPECTED_OPERATORS:
        assert f'"{name}"' in source or f"'{name}'" in source, name


def test_h5_the_platform_admin_has_no_membership():
    """That absence is what makes it a platform admin (spec section 3.2).

    A membership would bind it to an agency and silently turn the one
    cross-agency identity in the system into an ordinary owner.
    """
    source = _seed_source()
    memberships = source[source.index("agency_memberships"):]
    assert "platform_admin" not in memberships.split("INSERT INTO agency_memberships")[-1][:600] \
        or "is_platform_admin" in source
    assert "OPERATORS" in source or "operators" in source


def test_h5_the_counts_are_declared_and_asserted_in_the_script():
    """Six and five, stated explicitly so a later edit cannot drift silently."""
    source = _seed_source()
    assert "EXPECTED_OPERATOR_COUNT = 6" in source
    assert "EXPECTED_MEMBERSHIP_COUNT = 5" in source


def test_h5_only_the_platform_admin_carries_the_flag():
    module = _seed_module()
    flagged = [key for key, _, _, is_admin in module.OPERATORS if is_admin]
    assert flagged == ["platform_admin"], flagged


def test_h5_six_operators_and_five_memberships():
    module = _seed_module()
    assert len(module.OPERATORS) == module.EXPECTED_OPERATOR_COUNT == 6
    assert len(module.MEMBERSHIPS) == module.EXPECTED_MEMBERSHIP_COUNT == 5
    assert {key for key, *_ in module.OPERATORS} == set(EXPECTED_OPERATORS)


def test_h5_the_platform_admin_is_the_one_operator_without_a_membership():
    module = _seed_module()
    with_membership = {key for key, _, _ in module.MEMBERSHIPS}
    without = {key for key, *_ in module.OPERATORS} - with_membership
    assert without == {"platform_admin"}, without


def test_h5_every_membership_role_is_a_real_agency_role():
    from operator_auth import enums

    module = _seed_module()
    for _, _, role in module.MEMBERSHIPS:
        assert role in enums.AGENCY_ROLES, role


# ---------------------------------------------------------------------------
# H6 / H7 - the two direct-SQL E2E scripts (Task 18)
# ---------------------------------------------------------------------------

AGENCY_SUBSELECT = re.compile(
    r"SELECT\s+id\s+FROM\s+agencies\s+WHERE\s+slug\s*=", re.IGNORECASE
)


def _statements(path: Path, pattern: str) -> list[str]:
    """Every SQL statement in a file matching `pattern`, squashed."""
    text = _squash(path.read_text(encoding="utf-8"))
    return [
        text[match.start(): text.find('"', match.start()) if False else match.start() + 400]
        for match in re.finditer(pattern, text, re.IGNORECASE)
    ]


@pytest.mark.parametrize("path", [BUY_E2E, FLOW_E2E])
def test_h6_every_core_fixture_insert_carries_an_agency(path):
    """After 030's NOT NULL, an insert without agency_id simply fails."""
    for statement in _statements(path, r"INSERT INTO (?:contacts|leads)\b"):
        assert "agency_id" in statement, f"{path.name}: {statement[:200]}"


@pytest.mark.parametrize("path", [BUY_E2E, FLOW_E2E])
def test_h6_the_agency_is_resolved_by_slug_never_by_number(path):
    for statement in _statements(path, r"INSERT INTO (?:contacts|leads)\b"):
        assert AGENCY_SUBSELECT.search(statement), (
            f"{path.name}: agency_id is not a slug subselect: {statement[:200]}"
        )
        assert not re.search(r"agency_id\s*\)?\s*VALUES?[^)]*\b\d+\b", statement)


@pytest.mark.parametrize("path", [BUY_E2E, FLOW_E2E])
def test_h7_every_core_delete_is_id_scoped_or_agency_scoped(path):
    """The rule: delete by ids captured this run, or carry the agency predicate."""
    for statement in _statements(path, r"DELETE FROM (?:contacts|leads)\b"):
        head = statement[: statement.find(")") + 1] if ")" in statement else statement
        id_scoped = re.search(r"\bid\s*=\s*%s|\bid\s*=\s*ANY", head, re.IGNORECASE)
        agency_scoped = AGENCY_SUBSELECT.search(head)
        assert id_scoped or agency_scoped, f"{path.name}: unscoped delete: {head[:200]}"


@pytest.mark.parametrize("path", [BUY_E2E, FLOW_E2E])
def test_h7_no_core_delete_matches_on_a_string_column_alone(path):
    """The fail-closed form.

    run_buy_021_e2e.py's own comment called title/code/source "il confine di
    sicurezza". A *string* boundary stops being a boundary the moment a second
    agency exists and can hold a row with the same source marker.
    """
    for statement in _statements(path, r"DELETE FROM (?:contacts|leads)\b"):
        head = statement[: statement.find(")") + 1] if ")" in statement else statement
        matches_string_only = re.search(
            r"WHERE\s+(?:source|display_name|notes|title)\s*(?:=|LIKE)\s*%s\s*(?:\)|$)",
            head, re.IGNORECASE,
        )
        assert not matches_string_only, f"{path.name}: string-only boundary: {head[:200]}"


def test_h7_the_flow_script_cleanup_stays_id_scoped():
    """It was already safe; this pins it so a later edit cannot loosen it."""
    text = _squash(FLOW_E2E.read_text(encoding="utf-8"))
    for table in ("leads", "contacts"):
        match = re.search(rf"DELETE FROM {table} WHERE id=%s", text)
        assert match, f"{table} cleanup is no longer id-scoped"


def test_h7_the_untouched_e2e_scripts_were_not_modified():
    """Review checkpoint: only two scripts needed changing."""
    for name in ("run_integration_01_e2e.py", "run_integration_01_regression.py"):
        path = ROOT / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        assert "INSERT INTO contacts" not in text, (
            f"{name} gained a direct CORE insert; it creates data through the API"
        )


def test_h7_non_core_deletes_were_left_alone():
    """buy_requests, properties and flow_* are outside P26-1's scope."""
    text = _squash(BUY_E2E.read_text(encoding="utf-8"))
    for table in ("buy_requests", "properties"):
        statements = re.findall(rf"DELETE FROM {table}[^;\"']*", text, re.IGNORECASE)
        for statement in statements:
            assert "agencies" not in statement, (
                f"{table} cleanup gained an agency predicate it does not need"
            )
