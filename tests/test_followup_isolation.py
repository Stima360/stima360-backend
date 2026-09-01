"""P18-B static checks: migration shape, module isolation, and the P18-B
safety barrier (the engine ships "off").

These tests never touch a database. They read source files as text and
verify, mechanically, the guarantees P18-B was scoped to:

1. migrations/020_followup_actions.sql adds ONE new table, does not ALTER
   any existing table, and its four FKs are ON DELETE SET NULL (same
   rationale as P17's seller_timeline_events).
2. followup/ does not import from property/, buy/, match/, proposal/,
   owner/, flow/ or seller_intelligence/ - it is allowed to import core/
   (unlike seller_intelligence/), because creating CORE tasks is the whole
   point of this module (see followup/repository.py).
3. Nothing outside followup/ imports it yet, and - the critical P18-B
   barrier - main.py is completely untouched: no import, no reference, no
   wiring. The engine exists but is not called by any real flow.
4. No router.py and no cron script exist yet (both are explicitly out of
   scope for P18-B; they arrive in P18-D).
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UP = ROOT / "migrations" / "020_followup_actions.sql"
DOWN = ROOT / "migrations" / "020_followup_actions_down.sql"
PACKAGE_DIR = ROOT / "followup"
MAIN_PY = ROOT / "main.py"


def test_migration_files_exist():
    assert UP.exists(), "migration up P18-B mancante"
    assert DOWN.exists(), "migration down P18-B mancante"


def test_migration_creates_only_followup_actions():
    up = UP.read_text(encoding="utf-8")
    assert re.search(r"CREATE TABLE\s+IF NOT EXISTS\s+followup_actions", up, re.IGNORECASE)
    assert len(re.findall(r"CREATE TABLE", up, re.IGNORECASE)) == 1


def _strip_sql_line_comments(sql: str) -> str:
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


def test_migration_does_not_alter_any_existing_table():
    # Checked against the SQL with '--' line comments stripped: the
    # migration's own explanatory prose mentions "a separate ALTER TABLE"
    # (explaining why one was NOT used), which would otherwise false-
    # positive a naive substring check - the same class of self-inflicted
    # bug already hit twice on migrations 017/018 ("CHECK"/"DEFAULT" in
    # prose), fixed here by checking actual SQL, not comment text.
    up_sql_only = _strip_sql_line_comments(UP.read_text(encoding="utf-8"))
    assert "ALTER TABLE" not in up_sql_only.upper(), (
        "P18-B non deve modificare nessuna tabella esistente (CORE, P17 o altro)"
    )


def test_migration_fks_use_set_null_not_cascade():
    up = UP.read_text(encoding="utf-8")
    for column, table in (
        ("contact_id", "contacts"),
        ("lead_id", "leads"),
        ("stima_id", "stime"),
        ("task_id", "tasks"),
    ):
        pattern = rf"{column}\s+(?:BIGINT|INTEGER)\s+REFERENCES\s+{table}\(id\)\s+ON DELETE SET NULL"
        assert re.search(pattern, up, re.IGNORECASE), f"{column} deve essere ON DELETE SET NULL verso {table}"
    assert "ON DELETE CASCADE" not in up.upper()
    assert "ON DELETE RESTRICT" not in up.upper()


def test_migration_idempotency_key_is_not_null_unique():
    up = UP.read_text(encoding="utf-8")
    assert re.search(
        r"idempotency_key\s+VARCHAR\(300\)\s+NOT NULL\s+UNIQUE",
        up,
        re.IGNORECASE,
    ), "idempotency_key deve essere NOT NULL UNIQUE (garanzia DB-level vera)"


def test_migration_has_no_status_enum_check():
    up = UP.read_text(encoding="utf-8")
    assert "STATUS IN" not in up.upper().replace("  ", " "), (
        "status deve restare vocabolario aperto in Python, nessun CHECK enumerativo SQL"
    )


def test_migration_has_indexes_on_every_fk_and_status_and_created_at():
    up = UP.read_text(encoding="utf-8")
    for target in ("rule_code", "contact_id", "lead_id", "stima_id", "task_id", "status", "created_at DESC"):
        assert re.search(rf"CREATE INDEX[\s\S]*?\({re.escape(target)}\)", up, re.IGNORECASE), (
            f"indice mancante su {target}"
        )


def test_down_migration_only_drops_the_new_table():
    down = DOWN.read_text(encoding="utf-8")
    assert re.search(r"DROP TABLE(?: IF EXISTS)? followup_actions", down, re.IGNORECASE)
    assert "ALTER TABLE" not in down.upper()
    assert len(re.findall(r"DROP TABLE", down, re.IGNORECASE)) == 1


def test_package_files_exist():
    for name in ("__init__.py", "database.py", "exceptions.py", "rules.py", "repository.py", "service.py"):
        assert (PACKAGE_DIR / name).exists(), f"followup/{name} mancante"


def test_no_router_or_cron_script_exists_yet():
    # Explicitly out of scope for P18-B (arrive in P18-D per the design).
    assert not (PACKAGE_DIR / "router.py").exists(), "router.py non deve esistere ancora in P18-B"
    for path in ROOT.glob("run_followup*"):
        raise AssertionError(f"cron script non atteso in P18-B: {path.name}")


def test_package_does_not_import_forbidden_domain_packages():
    forbidden_imports = ("property", "buy", "match", "proposal", "owner", "flow", "seller_intelligence")
    for path in PACKAGE_DIR.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for module in forbidden_imports:
            assert not re.search(rf"^\s*(from|import)\s+{module}(\.|\s|$)", text, re.MULTILINE), (
                f"followup/{path.name} importa {module} - il modulo deve restare isolato da questo dominio"
            )


def test_package_is_allowed_to_import_core_for_task_creation():
    # Unlike seller_intelligence/, followup/ is explicitly allowed to
    # depend on core/ - creating CORE tasks is the point of this module.
    repository_source = (PACKAGE_DIR / "repository.py").read_text(encoding="utf-8")
    assert "from core import repository as core_repository" in repository_source
    assert "create_task_with_cursor" in repository_source


def test_package_only_depends_on_shared_database_module():
    database_py = (PACKAGE_DIR / "database.py").read_text(encoding="utf-8")
    assert "from database import get_connection" in database_py


def test_main_py_is_completely_untouched_by_followup():
    # The P18-B safety barrier: the engine exists but is not wired in.
    main_source = MAIN_PY.read_text(encoding="utf-8")
    assert "followup" not in main_source.lower(), (
        "main.py non deve contenere alcun riferimento a followup in P18-B - "
        "il wiring reale arriva in P18-C"
    )


def test_core_property_buy_match_proposal_owner_flow_do_not_import_followup():
    for domain_dir in ("core", "property", "buy", "match", "proposal", "owner", "flow"):
        for path in (ROOT / domain_dir).glob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert "followup" not in text, f"{path} non deve importare followup"


def test_seller_intelligence_does_not_import_followup_and_vice_versa():
    # Checks actual import statements only - both modules' docstrings
    # legitimately *mention* each other by name (followup/'s docs compare
    # its non-blocking wrapper to seller_intelligence.service.
    # safe_record_event(), by design, see followup/service.py), which a
    # naive substring check would false-positive on.
    import_pattern = r"^\s*(from|import)\s+{module}(\.|\s|$)"

    si_dir = ROOT / "seller_intelligence"
    for path in si_dir.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(import_pattern.format(module="followup"), text, re.MULTILINE), (
            f"{path} non deve importare followup"
        )
    for path in PACKAGE_DIR.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(import_pattern.format(module="seller_intelligence"), text, re.MULTILINE), (
            f"{path} non deve importare seller_intelligence"
        )


def test_contatto_dettaglio_view_is_untouched_by_p18_b():
    view = ROOT / "static" / "os_shell" / "assets" / "views" / "contatto-dettaglio.js"
    if view.exists():
        text = view.read_text(encoding="utf-8")
        assert "followup" not in text
