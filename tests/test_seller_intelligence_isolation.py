"""P17-A static checks: migration shape and module isolation.

These tests never touch a database. They read source files as text and
verify, mechanically, the two guarantees the design review demanded:

1. migrations/017_seller_intelligence_01.sql adds ONE new table, does not
   ALTER any existing (CORE or otherwise) table, and does NOT define a SQL
   CHECK requiring "at least one reference" - the FKs are ON DELETE SET
   NULL, and such a CHECK would make that action fail once it nulled out
   the last remaining reference on a row (see repository test for the
   dynamic proof of what this would otherwise block).
2. seller_intelligence/ does not import from core/, property/, buy/,
   match/, proposal/, owner/ or flow/, and nothing outside the new package
   (in particular main.py) references seller_intelligence yet - the module
   is authored but not wired into anything.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UP = ROOT / "migrations" / "017_seller_intelligence_01.sql"
DOWN = ROOT / "migrations" / "017_seller_intelligence_01_down.sql"
PACKAGE_DIR = ROOT / "seller_intelligence"
MAIN_PY = ROOT / "main.py"


def test_migration_files_exist():
    assert UP.exists(), "migration up P17 mancante"
    assert DOWN.exists(), "migration down P17 mancante"


def test_migration_creates_only_seller_timeline_events():
    up = UP.read_text(encoding="utf-8")
    assert re.search(r"CREATE TABLE\s+IF NOT EXISTS\s+seller_timeline_events", up, re.IGNORECASE)
    # Only one CREATE TABLE in the whole file.
    assert len(re.findall(r"CREATE TABLE", up, re.IGNORECASE)) == 1


def test_migration_has_no_reference_check_constraint():
    up = UP.read_text(encoding="utf-8")
    assert "CHECK" not in up.upper(), (
        "seller_timeline_events non deve avere alcun CHECK SQL: la regola "
        "'almeno un riferimento' e' applicativa, non database (vedi "
        "seller_intelligence/service.py::record_event)"
    )
    assert "seller_timeline_events_reference_chk" not in up


def test_migration_has_no_event_type_enum_check():
    up = UP.read_text(encoding="utf-8")
    assert "event_type IN" not in up.upper().replace("  ", " ")
    assert re.search(r"event_type\s+VARCHAR\(50\)\s+NOT NULL\s*,", up, re.IGNORECASE), (
        "event_type deve essere vocabolario aperto: VARCHAR(50) NOT NULL, nessun CHECK enumerativo"
    )


def test_migration_fks_use_set_null_not_cascade():
    up = UP.read_text(encoding="utf-8")
    for column, table in (
        ("contact_id", "contacts"),
        ("lead_id", "leads"),
        ("stima_id", "stime"),
        ("property_id", "properties"),
    ):
        pattern = rf"{column}\s+(?:BIGINT|INTEGER)\s+REFERENCES\s+{table}\(id\)\s+ON DELETE SET NULL"
        assert re.search(pattern, up, re.IGNORECASE), f"{column} deve essere ON DELETE SET NULL verso {table}"
    assert "ON DELETE CASCADE" not in up.upper()
    assert "ON DELETE RESTRICT" not in up.upper()


def test_migration_does_not_alter_any_existing_table():
    up = UP.read_text(encoding="utf-8")
    assert "ALTER TABLE" not in up.upper(), (
        "P17-A non deve modificare nessuna tabella esistente (CORE o altro)"
    )


def test_migration_has_partial_unique_index_on_idempotency_key():
    up = UP.read_text(encoding="utf-8")
    assert re.search(
        r"CREATE UNIQUE INDEX[\s\S]+idempotency_key\)[\s\S]+WHERE idempotency_key IS NOT NULL",
        up,
        re.IGNORECASE,
    )


def test_migration_has_indexes_on_every_fk_and_occurred_at_and_event_type():
    up = UP.read_text(encoding="utf-8")
    for target in ("contact_id", "lead_id", "stima_id", "property_id", "occurred_at DESC", "event_type"):
        assert re.search(rf"CREATE INDEX[\s\S]*?\({re.escape(target)}\)", up, re.IGNORECASE), (
            f"indice mancante su {target}"
        )


def test_down_migration_only_drops_the_new_table():
    down = DOWN.read_text(encoding="utf-8")
    assert re.search(r"DROP TABLE(?: IF EXISTS)? seller_timeline_events", down, re.IGNORECASE)
    assert "ALTER TABLE" not in down.upper()
    assert "DROP TABLE" in down.upper()
    assert len(re.findall(r"DROP TABLE", down, re.IGNORECASE)) == 1


def test_package_files_exist():
    for name in ("__init__.py", "database.py", "exceptions.py", "repository.py", "service.py", "schemas.py", "router.py"):
        assert (PACKAGE_DIR / name).exists(), f"seller_intelligence/{name} mancante"


def test_package_does_not_import_other_domain_packages():
    forbidden_imports = (
        "core", "property", "buy", "match", "proposal", "owner", "flow",
    )
    for path in PACKAGE_DIR.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for module in forbidden_imports:
            assert not re.search(rf"^\s*(from|import)\s+{module}(\.|\s|$)", text, re.MULTILINE), (
                f"seller_intelligence/{path.name} importa {module} - il modulo deve restare isolato"
            )


def test_package_only_depends_on_shared_database_module():
    database_py = (PACKAGE_DIR / "database.py").read_text(encoding="utf-8")
    assert "from database import get_connection" in database_py


def test_main_py_references_seller_intelligence_only_through_the_p17b1_contract():
    # Aggiornato in P17-B1: prima di questa fase main.py non doveva
    # referenziare seller_intelligence affatto (P17-A). Da P17-B1 lo fa,
    # deliberatamente, ma solo attraverso i due import espliciti e la
    # singola integrazione stima_richiesta autorizzati - non un import
    # sparso o un accesso a moduli interni (repository/schemas) diretti.
    main_source = MAIN_PY.read_text(encoding="utf-8")
    assert "from seller_intelligence import service as seller_intelligence_service" in main_source
    assert "from seller_intelligence.router import router as seller_intelligence_router" in main_source
    assert "app.include_router(seller_intelligence_router, dependencies=[Depends(require_admin)])" in main_source
    assert "seller_intelligence_service.safe_record_event(" in main_source
    # Nessun accesso diretto a repository/schemas/exceptions del modulo da main.py.
    for forbidden in ("seller_intelligence.repository", "seller_intelligence.schemas", "seller_intelligence.exceptions"):
        assert forbidden not in main_source, f"main.py non deve accedere direttamente a {forbidden}"
    # record_event() non va mai chiamato direttamente da main.py: solo il
    # wrapper non-bloccante safe_record_event() e' un punto di ingresso
    # autorizzato dal funnel pubblico.
    assert "seller_intelligence_service.record_event(" not in main_source


def test_core_property_buy_match_proposal_owner_flow_still_do_not_import_seller_intelligence():
    # Isolamento verso il resto del sistema resta valido anche dopo P17-B1:
    # solo main.py (funnel pubblico) puo' referenziare seller_intelligence.
    for domain_dir in ("core", "property", "buy", "match", "proposal", "owner", "flow"):
        for path in (ROOT / domain_dir).glob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert "seller_intelligence" not in text, f"{path} non deve importare seller_intelligence"


def test_contatto_dettaglio_only_uses_the_approved_read_only_p17b3_timeline_integration():
    """At P17B3 time contatto-dettaglio.js had no write capability at all, so
    scanning the whole file for apiPost/apiPatch/apiDelete was a valid proxy
    for "the timeline integration is read-only". Since then, P25 (P25.2
    Lead workflow, P25.4 contact editing, P25.5 buyer/match actions - all
    explicitly authorized additive OS Shell work, unrelated to seller
    intelligence) legitimately added real write actions to OTHER tabs of
    this same file. The proxy no longer holds, so this test now scopes the
    write-absence checks to the timeline case-block itself (main.js-style
    `case 'timeline': { ... break; }`), which is the only region this test
    is actually meant to guard - the P17B3 read-only contract for the
    timeline integration is unchanged and still verified precisely."""
    view = ROOT / "static" / "os_shell" / "assets" / "views" / "contatto-dettaglio.js"
    if view.exists():
        text = view.read_text(encoding="utf-8")
        assert "from '../components/timeline.js'" in text
        assert "loadSellerTimeline(contact.id, lazyCache)" in text
        assert "renderSellerTimeline" in text
        assert "/api/seller-intelligence" not in text

        match = re.search(r"case 'timeline': \{(.*?)\n\s*break;\n\s*\}", text, re.DOTALL)
        assert match, "blocco case 'timeline' non trovato in contatto-dettaglio.js"
        timeline_block = match.group(1)
        for forbidden in ("apiPost", "apiPatch", "apiDelete", "/api/core/activities"):
            assert forbidden not in timeline_block
