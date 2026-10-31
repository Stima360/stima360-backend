"""BLOCCO-7 - prove offline del report nell'area proprietario.

Autorizzazione, revoca, periodo, esclusioni e PDF sono provati su PostgreSQL
vero in `test_blocco_7_owner_portal_report_postgres.py`.
"""
from __future__ import annotations

import inspect
from pathlib import Path

from owner_report import service

ROOT = Path(__file__).resolve().parents[1]


def test_owner_and_operator_reports_share_one_composition():
    assert "_compose(cur, agency_id, prop, date_from, date_to)" in inspect.getsource(service.build_report)
    owner = inspect.getsource(service.build_owner_report)
    assert "_owner_property(" in owner and "_compose(" in owner
    assert "require_property" in inspect.getsource(service._owner_property)          # riletta a ogni richiesta
    pdf = inspect.getsource(service.render_owner_pdf_for_owner)
    assert "agent_notes=None" in pdf                                                  # niente note del consulente


def test_portal_routes_use_the_owner_session_and_answer_404_when_not_authorized():
    code = (ROOT / "owner" / "router_portal.py").read_text(encoding="utf-8")
    assert '@router.get("/properties/{p}/report")' in code and '@router.get("/properties/{p}/report.pdf")' in code
    assert code.count("s=Depends(current_owner)") >= 2
    assert "status = 404 if exc.status in (403, 404) else exc.status" in code


def test_portal_ui_is_safe_and_states_missing_data():
    js = (ROOT / "static" / "owner_portal" / "assets" / "owner-report.js").read_text(encoding="utf-8")
    assert "innerHTML" not in js and "textContent" in js and "Dato non disponibile" in js
    assert "response.status === 404" in js and "section.hidden = true" in js           # revoca: sezione nascosta
    # la prima impostazione del prezzo non e' un «dato non disponibile»
    assert "prezzo iniziale ${money(p.new_price)}" in js
    html = (ROOT / "static" / "owner_portal" / "index.html").read_text(encoding="utf-8")
    assert 'id="owner-report-section"' in html and "/owner/assets/owner-report.js" in html
