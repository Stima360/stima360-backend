"""F04/F06 browser recovery with real backend/CRM and disposable PostgreSQL."""
from __future__ import annotations

import importlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.test_catalogo_canonico_1_postgres import DSN, completo, mondo  # noqa: F401
from tests.test_censimento_3_backend_postgres import _q
from tests.test_public_site_journey_postgres import journey  # noqa: F401

pytestmark = pytest.mark.skipif(
    not DSN or os.getenv("STIMA360_RUN_PUBLIC_JOURNEY") != "1",
    reason="opt-in isolated PostgreSQL/browser journey required",
)
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module", autouse=True)
def _private_socket_only():
    from psycopg2.extensions import parse_dsn
    params = parse_dsn(DSN)
    assert params.get("host") == "/private/tmp/stima360-isolated-pg.vQlFs50D/socket"
    assert params.get("port") == "55473"
    assert os.getenv("PYTHON_DOTENV_DISABLED") == "1"


@pytest.fixture
def receipt_journey(journey, monkeypatch):
    j = journey
    for name in ("seller_intelligence.database", "followup.database", "property_watch.database"):
        monkeypatch.setattr(importlib.import_module(name), "get_connection",
                            lambda: j.m["psycopg2"].connect(j.m["dsn"]))

    def browser(phase, extra=None):
        input_file = j.artifacts / f"receipts-{phase}-input.json"
        output_file = j.artifacts / f"receipts-{phase}-output.json"
        input_file.write_text(json.dumps({"origin": j.origin, **(extra or {})}), encoding="utf-8")
        command = [os.getenv("STIMA360_NODE", "node"), str(ROOT / "tests/public_submission_receipt_journey.mjs"),
                   phase, str(input_file), str(output_file)]
        result = subprocess.run(command, text=True, capture_output=True, timeout=120)
        (j.artifacts / f"receipts-{phase}-browser.log").write_text(result.stdout + result.stderr, encoding="utf-8")
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(output_file.read_text(encoding="utf-8"))

    yield j, browser


def _business_links(m, sid):
    return _q(m, "SELECT s.id, s.agency_id, l.contact_id, l.id, p.property_id "
                 "FROM stime s JOIN lead_stime ls ON ls.stima_id=s.id JOIN leads l ON l.id=ls.lead_id "
                 "JOIN property_site_sources p ON p.stima_id=s.id AND p.status='active' WHERE s.id=%s", (sid,))


def test_browser_lost_saved_response_recovers_after_reload_and_reaches_same_private_pdf(receipt_journey):
    j, browser = receipt_journey
    proof = browser("lost-response")
    sid = proof["stima_id"]
    assert proof["quick_posts"] == 1
    assert proof["receipt_reads"] >= 1
    assert proof["resume_posts"] == 0
    assert proof["receipt_status"] == "completed"
    assert proof["after_save"] == proof["after_recovery"]
    links = _business_links(j.m, sid)
    assert len(links) == 1 and all(value is not None for value in links[0])
    assert _q(j.m, "SELECT count(*) FROM communication_messages WHERE stima_id=%s AND reason_code='stima_pdf'", (sid,)) == [[1]]
    assert len(j.captures["whatsapp"]) == 1
    assert len([message for message in j.captures["smtp"] if message["to"] == "admin@example.invalid"]) == 1


def test_browser_partial_receipt_resumes_original_request_after_reload(receipt_journey, monkeypatch):
    j, browser = receipt_journey
    main = importlib.import_module("main")
    original = main.core_service.bridge_public_stima
    calls = []

    def failure_once(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("synthetic-journey-bridge-failure")
        return original(*a, **k)

    monkeypatch.setattr(main.core_service, "bridge_public_stima", failure_once)
    proof = browser("partial")
    sid = proof["stima_id"]
    assert proof["quick_posts"] == 1 and proof["resume_posts"] == 1
    assert proof["receipt_reads"] >= 1 and proof["receipt_status"] == "completed"
    assert proof["after_save"]["stime"] == proof["after_recovery"]["stime"]
    assert len(_business_links(j.m, sid)) == 1
    assert _q(j.m, "SELECT count(*) FROM communication_messages WHERE stima_id=%s AND reason_code='stima_pdf'", (sid,)) == [[1]]
    assert len(j.captures["whatsapp"]) == 1


def test_browser_double_submit_sends_one_request_and_one_crm_valuation(receipt_journey):
    j, browser = receipt_journey
    proof = browser("double-click")
    sid = proof["stima_id"]
    assert proof["quick_posts"] == 1 and proof["resume_posts"] == 0
    assert proof["receipt_status"] == "completed"
    assert len(_business_links(j.m, sid)) == 1
    assert _q(j.m, "SELECT count(*) FROM communication_messages WHERE stima_id=%s AND reason_code='stima_pdf'", (sid,)) == [[1]]
    assert len(j.captures["whatsapp"]) == 1


def _assert_ready_notification_journey(j, proof, status):
    sid = proof["stima_id"]
    assert proof["quick_posts"] == 1 and proof["resume_posts"] == 0
    assert proof["receipt_reads"] >= 1
    assert proof["receipt_status"] == status and proof["result_available"] is True
    assert proof["after_save"] == proof["after_recovery"]
    assert proof["detail_prefill_stima_id"] == sid
    assert proof["browser_errors"] == []
    links = _business_links(j.m, sid)
    assert len(links) == 1 and all(value is not None for value in links[0])
    assert _q(j.m, "SELECT count(*) FROM communication_messages WHERE stima_id=%s AND reason_code='stima_pdf'", (sid,)) == [[1]]
    assert _q(j.m, "SELECT status FROM stima_pdf_artifacts WHERE stima_id=%s", (sid,)) == [["ready"]]
    assert _q(j.m, "SELECT count(*) FROM public_submission_receipts WHERE stima_id=%s", (sid,)) == [[1]]
    return sid


def test_browser_uncertain_admin_email_still_reaches_result_pdf_and_detail(receipt_journey, monkeypatch):
    j, browser = receipt_journey
    database = importlib.import_module("database")
    attempts = []
    base_smtp = database.smtplib.SMTP

    class TimeoutForAdmin(base_smtp):
        def sendmail(self, sender, recipient, raw):
            if recipient == "admin@example.invalid":
                attempts.append(recipient)
                raise TimeoutError("synthetic-smtp-timeout-after-data")
            return super().sendmail(sender, recipient, raw)

    monkeypatch.setattr(database.smtplib, "SMTP", TimeoutForAdmin)
    proof = browser("attention-admin")
    _assert_ready_notification_journey(j, proof, "attention")
    assert proof["notifications"]["admin_email"] == "indeterminate"
    assert len(attempts) == 1, "an uncertain SMTP attempt is never repeated"
    assert len(j.captures["whatsapp"]) == 1


def test_browser_uncertain_whatsapp_still_reaches_result_pdf_and_detail(receipt_journey, monkeypatch):
    j, browser = receipt_journey
    main = importlib.import_module("main")
    attempts = []

    def timeout(url, *, json, timeout):
        attempts.append(json)
        raise TimeoutError("synthetic-whatsapp-timeout")

    monkeypatch.setattr(main, "requests", type("R", (), {"post": staticmethod(timeout)}))
    proof = browser("attention-whatsapp")
    _assert_ready_notification_journey(j, proof, "attention")
    assert proof["notifications"]["whatsapp"] == "indeterminate"
    assert len(attempts) == 1, "an uncertain WhatsApp attempt is never repeated"
    assert len([m for m in j.captures["smtp"] if m["to"] == "admin@example.invalid"]) == 1


def test_browser_admin_preflight_failure_shows_result_without_any_transport(receipt_journey, monkeypatch):
    j, browser = receipt_journey
    monkeypatch.delenv("SMTP_PASS", raising=False)
    proof = browser("preflight-admin")
    _assert_ready_notification_journey(j, proof, "partial")
    assert proof["notifications"]["admin_email"] == "failed"
    assert [m for m in j.captures["smtp"] if m["to"] == "admin@example.invalid"] == []
    assert len(j.captures["whatsapp"]) == 1
