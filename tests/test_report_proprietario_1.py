"""REPORT-PROPRIETARIO-1 - prove offline del report attivita' per il proprietario.

Le letture SQL, i permessi e la separazione tra agenzie sono provati su
PostgreSQL vero in `test_report_proprietario_1_postgres.py`. Qui: periodo
(anche quello che parte dall'incarico), mesi, impaginazione del PDF, corpo
chiuso delle rotte e interfaccia.
"""
from __future__ import annotations

import hashlib
import io
import re
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from operator_auth.context import OperatorContext
from owner_report import router as report_router
from owner_report import service
from owner_report.enums import AGENT_NOTES_MAX
from owner_report.pdf_render import render_owner_report_pdf
from property_valuations.pdf_render import PdfLogoError

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "static" / "os_shell" / "assets"
TODAY = date(2026, 10, 29)


@pytest.fixture(autouse=True)
def _today(monkeypatch):
    monkeypatch.setattr(service, "today_rome", lambda: TODAY)


# ---------------------------------------------------------------------------
# periodo
# ---------------------------------------------------------------------------

def test_default_period_starts_at_the_mandate_when_there_is_one():
    prop = {"mandate_type": "esclusiva", "mandate_start": date(2026, 6, 1), "acquisition_id": None}
    assert service.resolve_period(prop, None, None) == (date(2026, 6, 1), TODAY)


def test_default_period_is_ninety_days_without_a_mandate_or_with_partial_data():
    for prop in ({}, {"mandate_type": "esclusiva"}, {"mandate_start": date(2026, 1, 1)}):
        assert service.resolve_period(prop, None, None) == (TODAY - timedelta(days=90), TODAY)


def test_a_mandate_starting_after_the_period_end_falls_back_to_ninety_days():
    prop = {"mandate_type": "esclusiva", "mandate_start": date(2026, 10, 1)}
    end = date(2026, 9, 1)
    assert service.resolve_period(prop, None, end) == (end - timedelta(days=90), end)


@pytest.mark.parametrize("date_from, date_to", [
    (date(2026, 10, 2), date(2026, 10, 1)),                 # inizio dopo la fine
    (None, TODAY + timedelta(days=1)),                      # fine nel futuro
    (TODAY - timedelta(days=800), None),                    # oltre il massimo
])
def test_invalid_periods_are_refused(date_from, date_to):
    with pytest.raises(service.InvalidPeriod):
        service.resolve_period({}, date_from, date_to)


def test_months_cover_the_period_across_the_year():
    months = list(service._months(date(2025, 11, 20), date(2026, 2, 3)).values())
    assert [m["month"] for m in months] == ["2025-11", "2025-12", "2026-01", "2026-02"]
    assert months[0]["label"] == "novembre 2025" and months[-1]["label"] == "febbraio 2026"


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

def _report(**over):
    rep = {
        "property": {"id": 7, "code": "IMM-7", "title": "Trilocale prova", "address": "Via Prova 1, Comune",
                     "asking_price": "239000.00", "commercial_status": "under_offer",
                     "commercial_status_label": "Sotto offerta"},
        "mandate": {"exists": True, "type": "esclusiva", "start": "2026-06-01", "end": "2026-12-01",
                    "state": "active", "state_label": "In corso"},
        "period": {"from": "2026-06-01", "to": "2026-10-29", "days": 151},
        "inquiries": {"total": 3}, "visits": {"completed": 2, "upcoming": 1},
        "monthly": [{"month": "2026-06", "label": "giugno 2026", "inquiries": 1, "visits": 0}],
        "proposals": {"total": 1, "by_status": [{"status": "submitted", "label": "In attesa di risposta", "count": 1}]},
        "price_history": [{"date": "2026-07-01", "old_price": "250000.00", "new_price": "239000.00"}],
        "status_history": [{"date": "2026-07-02", "old_label": "Attivo", "new_label": "Sotto offerta"}],
        "definitions": ["Definizione di prova."],
    }
    rep.update(over)
    return rep


def _identity(logo=None):
    out = {"agency_id": 1, "agency_name": "Agenzia Prova", "header_name": "Agenzia Prova", "phone": "+39 000"}
    if logo is not None:
        out["logo"] = {"content_type": "image/png", "width": 10, "height": 10,
                       "sha256": hashlib.sha256(logo).hexdigest(), "data": logo}
    else:
        out["logo"] = None
    return out


def _png():
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (200, 80), (20, 60, 120)).save(out, format="PNG")
    return out.getvalue()


def test_pdf_is_deterministic_with_and_without_logo():
    for logo in (None, _png()):
        a = render_owner_report_pdf(_report(), _identity(logo), agent_notes="Nota", generated_at="2026-10-29T09:00:00+00:00")
        b = render_owner_report_pdf(_report(), _identity(logo), agent_notes="Nota", generated_at="2026-10-29T09:00:00+00:00")
        assert a == b and a.startswith(b"%PDF-")
        assert (b"/Subtype /Image" in a) is (logo is not None)


def test_an_undecodable_logo_is_an_error():
    with pytest.raises(PdfLogoError):
        render_owner_report_pdf(_report(), _identity(b"GIF89a-rotto"), agent_notes=None,
                                generated_at="2026-10-29T09:00:00+00:00")


def test_long_tables_and_notes_span_pages():
    months = [{"month": f"m{i}", "label": f"mese {i}", "inquiries": i, "visits": i} for i in range(25)]
    prices = [{"date": "2026-07-01", "old_price": "1", "new_price": "2"}] * 40
    data = render_owner_report_pdf(_report(monthly=months, price_history=prices), _identity(),
                                   agent_notes="Testo lungo. " * 250, generated_at="2026-10-29T09:00:00+00:00")
    assert len(re.findall(rb"/Type /Page[^s]", data)) >= 2


# ---------------------------------------------------------------------------
# rotte: corpo chiuso, limiti, errori
# ---------------------------------------------------------------------------

OWNER = OperatorContext(user_id=1, agency_id=10, role="agency_owner", is_platform_admin=False, session_id=1,
                        auth_channel="operator_session")


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(report_router.router)
    app.dependency_overrides[report_router.require_operator] = lambda: OWNER
    calls = []

    def fake_pdf(ctx, property_id, date_from, date_to, notes):
        calls.append((ctx.agency_id, property_id, date_from, date_to, notes))
        return {"data": b"%PDF-1.4 prova", "sha256": "x", "filename": "report.pdf"}

    monkeypatch.setattr(service, "render_pdf", fake_pdf)
    return TestClient(app), calls


@pytest.mark.parametrize("extra", [{"agency_id": 99}, {"created_by": 1}, {"property_id": 5}])
def test_pdf_body_is_closed(client, extra):
    c, calls = client
    assert c.post("/api/owner-reports/7/pdf", json=extra).status_code == 422 and calls == []


def test_agent_notes_over_the_limit_are_refused(client):
    c, calls = client
    assert c.post("/api/owner-reports/7/pdf", json={"agent_notes": "x" * (AGENT_NOTES_MAX + 1)}).status_code == 422
    r = c.post("/api/owner-reports/7/pdf", json={"agent_notes": "ok", "date_from": "2026-06-01"})
    assert r.status_code == 200 and r.headers["cache-control"] == "private, no-store"
    assert calls == [(10, 7, date(2026, 6, 1), None, "ok")]


def test_service_errors_become_structured_http_errors(monkeypatch):
    app = FastAPI()
    app.include_router(report_router.router)
    app.dependency_overrides[report_router.require_operator] = lambda: OWNER

    def refuse(*args):
        raise service.Forbidden("NOT_ALLOWED", "no")

    monkeypatch.setattr(service, "build_report", refuse)
    r = TestClient(app).get("/api/owner-reports/7")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "NOT_ALLOWED"


def test_routes_are_mounted_behind_the_operator_session():
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    line = next(r for r in main.splitlines() if "owner_report_router" in r and "include_router" in r)
    assert "require_authenticated_operator" in line
    src = (ROOT / "owner_report" / "repository.py").read_text(encoding="utf-8")
    assert not any(w in src.upper() for w in ("INSERT ", "UPDATE ", "DELETE "))


# ---------------------------------------------------------------------------
# interfaccia
# ---------------------------------------------------------------------------

def test_property_detail_has_the_owner_report_tab():
    view = (APP / "views" / "immobile-dettaglio.js").read_text(encoding="utf-8")
    assert "{ key: 'report-proprietario', label: 'Report proprietario' }" in view
    assert "mountOwnerReport(contentEl, { propertyId: property.id })" in view


def test_component_uses_safe_dom_and_session_only_download():
    js = (APP / "components" / "owner-report.js").read_text(encoding="utf-8")
    assert "innerHTML" not in js and "textContent" in js
    assert "credentials: 'include'" in js and "sessionExpired()" in js
    for needle in ("Scarica PDF", "Indicazioni dell’agente", "maxLength = NOTES_MAX", "/pdf"):
        assert needle in js, needle
    assert "mailto:" not in js and "wa.me" not in js and "whatsapp" not in js.lower()
