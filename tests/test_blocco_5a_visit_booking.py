"""BLOCCO-5A - prove offline della prenotazione online delle visite.

Il percorso completo (incarico, autorizzazione, orari, concorrenza, gestione,
cambio d'ora) e' provato su PostgreSQL vero in
`test_blocco_5a_visit_booking_postgres.py`. Qui: migrazione, confini,
oscuramento dei token nei log, pagina pubblica.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_migration_120_is_valid_and_additive():
    sys.path.insert(0, str(ROOT / "scripts"))
    import p26_migrate as runner

    tutte = runner.discover_migrations()
    runner.verify_contiguous(tutte)
    m = next(x for x in tutte if x.version == "120_property_visit_booking")
    assert m.down_available and runner.validate_migration(m) == []
    up = (ROOT / "migrations" / "120_property_visit_booking.sql").read_text(encoding="utf-8")
    executable = "\n".join(r for r in up.splitlines() if not r.lstrip().startswith("--"))
    assert not re.search(r"\b(ALTER TABLE|DROP|DELETE FROM|UPDATE \w+ SET|INSERT INTO)\b", executable)
    assert "owner_authorization IN ('preauthorized', 'per_visit')" in executable
    assert "owner_authorization_recorded_by      BIGINT       NOT NULL" in executable
    down = (ROOT / "migrations" / "120_property_visit_booking_down.sql").read_text(encoding="utf-8")
    assert "RAISE EXCEPTION" in down and "DELETE FROM" not in down and "appointments" not in down.split("--")[-1]


def test_generic_links_still_never_book_visits():
    """SEMPLIFICAZIONE-CRM-1 resta: il percorso generico rifiuta le visite."""
    service = (ROOT / "appointments" / "service.py").read_text(encoding="utf-8")
    assert 'if link["appointment_type"] in MANDATE_REQUIRED_TYPES:' in service
    enums = (ROOT / "public_booking" / "enums.py").read_text(encoding="utf-8")
    assert "NOT_BOOKABLE_TYPES" in enums


def test_visit_writes_live_in_the_agenda_and_require_the_public_context():
    writes = (ROOT / "appointments" / "property_visit_writes.py").read_text(encoding="utf-8")
    assert "origin != \"public_booking\"" in writes and "lock_agents" in writes and "find_conflicts" in writes
    assert "_visite.on_create" in writes and "PUBLIC_MANDATE_STATE = \"active\"" in writes
    assert '"per_visit": "requested"' in writes                                   # mai confermata senza assenso
    module = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "visit_booking").glob("*.py"))
    assert "INSERT INTO appointments" not in module and "UPDATE appointments" not in module


def test_tokens_are_redacted_from_access_logs():
    from public_booking.log_redaction import redact_path

    for path in ("/visita/abcdefghijklmnopqrstuvwxyz", "/visita-gestione/11111111-2222-4333-8444-555555555555",
                 "/api/public/visit-booking/abcdefghijklmnopqrstu/slots",
                 "/api/public/visit-booking/manage/11111111-2222-4333-8444-555555555555/cancel"):
        assert "abcdefghijklmnop" not in redact_path(path) and "5555555555" not in redact_path(path), path
    assert redact_path("/visita/assets/visit-page.js") == "/visita/assets/visit-page.js"


def test_public_page_is_static_safe_and_says_requests_are_not_confirmed():
    html = (ROOT / "static" / "visit_booking" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "static" / "visit_booking" / "assets" / "visit-page.js").read_text(encoding="utf-8")
    assert "<script>" not in html and 'type="module"' in html
    assert "innerHTML" not in js and "textContent" in js and "credentials: 'omit'" in js
    assert "Europe/Rome" in js and "privacy_accepted: true" in js
    assert "result.state === 'confirmed'" in js                                  # titolo «confermata» solo se lo e'
    ops = (ROOT / "static" / "os_shell" / "assets" / "components" / "visit-booking-links.js").read_text(encoding="utf-8")
    assert "innerHTML" not in ops and "Indica l’autorizzazione del proprietario." in ops
    assert "non sarà più mostrato" in ops


def test_routes_are_mounted_as_declared():
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    ops = next(r for r in main.splitlines() if "visit_booking_router" in r and "include_router" in r
               and "public" not in r)
    assert "require_authenticated_operator" in ops
    assert "app.include_router(visit_booking_public_router)" in main
    assert 'app.mount("/visita",' in main and 'app.mount("/visita-gestione",' in main
