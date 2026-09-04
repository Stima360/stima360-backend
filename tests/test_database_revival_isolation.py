"""P24 - migration shape, package boundary and cursor-shape checks.

Same pattern as tests/test_followup_isolation.py: static, text-level checks
of the migration SQL and of module import boundaries. No database, no
network.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UP = ROOT / "migrations" / "025_seller_revival_suppressions.sql"
DOWN = ROOT / "migrations" / "025_seller_revival_suppressions_down.sql"
PACKAGE_DIR = ROOT / "database_revival"


def _strip_sql_line_comments(sql: str) -> str:
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


def test_migration_files_exist():
    assert UP.exists(), "migration up P24 mancante"
    assert DOWN.exists(), "migration down P24 mancante"


def test_migration_creates_only_seller_revival_suppressions():
    up = UP.read_text(encoding="utf-8")
    assert re.search(r"CREATE TABLE\s+IF NOT EXISTS\s+seller_revival_suppressions", up, re.IGNORECASE)
    assert len(re.findall(r"CREATE TABLE", up, re.IGNORECASE)) == 1


def test_migration_does_not_alter_any_existing_table():
    up_sql_only = _strip_sql_line_comments(UP.read_text(encoding="utf-8"))
    assert "ALTER TABLE" not in up_sql_only.upper(), (
        "P24 non deve modificare nessuna tabella esistente"
    )


def test_migration_contact_id_is_not_null_cascade():
    up = UP.read_text(encoding="utf-8")
    assert re.search(
        r"contact_id\s+BIGINT\s+NOT NULL\s+REFERENCES\s+contacts\(id\)\s+ON DELETE CASCADE",
        up,
        re.IGNORECASE,
    ), "contact_id deve essere NOT NULL ON DELETE CASCADE verso contacts"


def test_migration_lead_id_is_nullable_set_null():
    up = UP.read_text(encoding="utf-8")
    assert re.search(
        r"lead_id\s+BIGINT\s+REFERENCES\s+leads\(id\)\s+ON DELETE SET NULL",
        up,
        re.IGNORECASE,
    ), "lead_id deve essere nullable ON DELETE SET NULL verso leads"
    assert not re.search(r"lead_id\s+BIGINT\s+NOT NULL", up, re.IGNORECASE)


def test_migration_has_unique_contact_id_constraint():
    up = UP.read_text(encoding="utf-8")
    assert re.search(r"UNIQUE\s*\(\s*contact_id\s*\)", up, re.IGNORECASE)


def test_migration_has_no_status_snapshot_or_idempotency_key_columns():
    up_sql_only = _strip_sql_line_comments(UP.read_text(encoding="utf-8"))
    create_block_match = re.search(
        r"CREATE TABLE.*?seller_revival_suppressions\s*\((.*?)\);",
        up_sql_only,
        re.IGNORECASE | re.DOTALL,
    )
    assert create_block_match, "blocco CREATE TABLE non trovato"
    create_block = create_block_match.group(1).lower()
    for forbidden in ("status", "snapshot", "idempotency_key"):
        assert forbidden not in create_block, (
            f"colonna non necessaria in V1 (YAGNI): {forbidden}"
        )


def test_migration_has_created_at_and_expires_at_indexes():
    up = UP.read_text(encoding="utf-8")
    for target in ("created_at", "expires_at"):
        assert re.search(rf"CREATE INDEX[\s\S]*?\({target}\)", up, re.IGNORECASE), (
            f"indice mancante su {target}"
        )


def test_down_migration_only_drops_the_new_table():
    down = DOWN.read_text(encoding="utf-8")
    assert re.search(r"DROP TABLE(?: IF EXISTS)? seller_revival_suppressions", down, re.IGNORECASE)
    assert "ALTER TABLE" not in down.upper()
    assert len(re.findall(r"DROP TABLE", down, re.IGNORECASE)) == 1


def test_package_files_exist():
    for name in ("__init__.py", "database.py"):
        assert (PACKAGE_DIR / name).exists(), f"database_revival/{name} mancante"


def test_database_module_mirrors_next_best_action_database_shape():
    database_py = (PACKAGE_DIR / "database.py").read_text(encoding="utf-8")
    assert "from database import get_connection" in database_py
    assert "def database_revival_cursor" in database_py


def test_package_does_not_import_forbidden_domain_packages():
    forbidden_imports = (
        "property", "buy", "match", "proposal", "owner", "flow",
        "seller_intelligence", "seller_intent", "property_watch",
        "next_best_action",
    )
    for path in PACKAGE_DIR.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for module in forbidden_imports:
            assert not re.search(rf"^\s*(from|import)\s+{module}(\.|\s|$)", text, re.MULTILINE), (
                f"database_revival/{path.name} importa {module} - il modulo deve restare isolato"
            )


def test_p24_does_not_create_core_tasks():
    # Checked as an actual import statement, not a raw substring: unlike
    # followup/ (which legitimately imports core.repository.
    # create_task_with_cursor to create CORE tasks - see
    # test_followup_isolation.py::test_package_is_allowed_to_import_core_
    # for_task_creation), database_revival/ never needs any CORE write
    # access at all, so it never imports core.repository in the first
    # place. Its own docstrings legitimately *mention*
    # core.repository.create_task_with_cursor by name only as a naming
    # precedent/comparison (see eligibility.py/repository.py module
    # docstrings) - a raw substring check on "create_task" would
    # false-positive on that prose, the same class of self-inflicted bug
    # already hit on migrations 017/018 and fixed the same way elsewhere
    # in this test suite (anchored import-statement regex, not substring).
    import_pattern = r"^\s*(from|import)\s+core(\.|\s|$)"
    for path in PACKAGE_DIR.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(import_pattern, text, re.MULTILINE), (
            f"{path.name}: P24 non deve importare core (nessun accesso in scrittura a CORE necessario)"
        )
        assert "create_task_with_cursor(" not in text, (
            f"{path.name}: P24 non deve chiamare create_task_with_cursor (business rule frozen)"
        )


def test_p24_does_not_send_communications():
    forbidden_tokens = ("send_email", "send_whatsapp", "smtp", "twilio", "sendgrid")
    for path in PACKAGE_DIR.glob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        for token in forbidden_tokens:
            assert token not in text, (
                f"{path.name}: P24 non deve inviare comunicazioni (trovato {token!r})"
            )


def test_p24_no_destructive_core_changes_in_migration():
    up_sql_only = _strip_sql_line_comments(UP.read_text(encoding="utf-8"))
    assert "DROP TABLE" not in up_sql_only.upper()
    assert "ALTER TABLE" not in up_sql_only.upper()


def test_no_p17_p22_module_imports_database_revival():
    """Dependency direction guard: none of the P17-P22 modules that
    database_revival may read from can import it back. next_best_action is
    deliberately NOT in this list: it is the aggregator and is EXPECTED to
    import database_revival (same relationship it already has with
    seller_intent/followup/flow/property_watch) - see
    test_next_best_action_isolation.py for the correct-direction check."""
    modules = ["seller_intent", "followup", "flow", "property_watch", "core", "match"]
    for module_name in modules:
        module_dir = ROOT / module_name
        if not module_dir.is_dir():
            continue
        for py_file in module_dir.rglob("*.py"):
            tree = ast.parse(py_file.read_text(), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module] if node.module else []
                else:
                    continue
                for name in names:
                    assert not (name or "").startswith("database_revival"), (
                        f"{py_file} imports database_revival - forbidden dependency direction"
                    )
