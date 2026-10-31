"""BLOCCO-6A - prove offline del foglio visita (PDF reale, nessun database).

Dati, permessi, agenzie, versioni e immutabilita' sono provati su PostgreSQL
vero in `test_blocco_6a_visit_sheets_postgres.py`.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from visit_sheets.pdf_render import DRAFT_NOTICE, TEMPLATE_VERSION, render_visit_sheet_pdf

ROOT = Path(__file__).resolve().parents[1]
SNAP = {"sheet_version": 1, "visit_state": "booked",
        "property": {"code": "IMM-1", "address": "Via Esempio 10, Comune Sintetico", "type_label": "Appartamento"},
        "mandate": {"exists": True, "type": "Esclusiva", "start": "01/10/2026", "end": None},
        "visit": {"date_text": "05/11/2026", "time_text": "10:00–10:45 (ora italiana)", "agent_name": "Agente",
                  "visitor_name": "Visitatore", "participants": []}, "author_name": "Titolare"}


def _text(pdf):
    if not shutil.which("pdftotext"):
        pytest.skip("pdftotext non disponibile")
    out = subprocess.run(["pdftotext", "-", "-"], input=pdf, capture_output=True, check=True).stdout.decode()
    return " ".join(out.split())


def test_booked_sheet_never_attests_the_visit_and_has_no_invented_clauses():
    text = _text(render_visit_sheet_pdf(SNAP, {"header_name": "Agenzia"}, generated_at_text="31/10/2026"))
    assert "non attesta che la visita sia avvenuta" in text and "Visita svolta" not in text
    assert "non contiene clausole provvigionali" in text and TEMPLATE_VERSION in text
    for invented in ("provvigione pari", "si obbliga", "si impegna", "penale", "corrispettivo"):
        assert invented not in text.lower(), invented


def test_completed_sheet_states_the_recorded_outcome():
    snap = {**SNAP, "visit_state": "completed", "visit": {**SNAP["visit"], "completed_at_text": "05/11/2026 10:50"}}
    text = _text(render_visit_sheet_pdf(snap, {"header_name": "Agenzia"}, generated_at_text="31/10/2026"))
    assert "Visita svolta: esito registrato dall'operatore in agenda il 05/11/2026 10:50" in text


def test_migration_121_is_valid_and_immutable_by_design():
    sys.path.insert(0, str(ROOT / "scripts"))
    import p26_migrate as runner

    m = next(x for x in runner.discover_migrations() if x.version == "121_visit_sheets")
    assert m.down_available and runner.validate_migration(m) == []
    up = (ROOT / "migrations" / "121_visit_sheets.sql").read_text(encoding="utf-8")
    assert "visit_state IN ('booked', 'completed')" in up and "is immutable" in up and "no recorded outcome" in up
    assert not re.search(r"\b(ALTER TABLE|UPDATE \w+ SET|INSERT INTO)\b",
                         "\n".join(r for r in up.splitlines() if not r.lstrip().startswith("--")))


def test_nothing_is_published_to_the_owner_and_routes_are_protected():
    code = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "visit_sheets").glob("*.py"))
    assert "owner_publications" not in code and "owner_property_access" not in code
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    line = next(r for r in main.splitlines() if "visit_sheets_router" in r and "include_router" in r)
    assert "require_authenticated_operator" in line
    js = (ROOT / "static" / "os_shell" / "assets" / "components" / "visit-sheets.js").read_text(encoding="utf-8")
    assert "innerHTML" not in js and "non è un documento firmato" in js and "credentials: 'include'" in js
    assert DRAFT_NOTICE.startswith("Bozza di modello")
