"""Public browser journey against the real backend and disposable PostgreSQL.

Opt-in: P29_TEST_DSN and STIMA360_RUN_PUBLIC_JOURNEY=1. No shared database,
external SMTP/WhatsApp/GitHub transport or public browser request is permitted.
The break caught here is loss of the server capability between quick valuation,
PDF/email/WhatsApp entry points and the existing CRM property.
"""
from __future__ import annotations

import importlib
import hashlib
import json
import os
import socket
import subprocess
import threading
import time
from email import message_from_string
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen

import pytest

from tests.test_catalogo_canonico_1_postgres import DSN, completo, mondo  # noqa: F401
from tests.test_censimento_3_backend_postgres import _q

pytestmark = pytest.mark.skipif(
    not DSN or os.getenv("STIMA360_RUN_PUBLIC_JOURNEY") != "1",
    reason="opt-in disposable PostgreSQL/browser journey required",
)
ROOT = Path(__file__).resolve().parents[1]


def _journey_quantities(m):
    return {table: _q(m, f"SELECT count(*) FROM {table}")[0][0]
            for table in ("stime", "contacts", "properties")}


@pytest.fixture(scope="module", autouse=True)
def _only_the_disposable_cluster():
    """Run before the reused schema fixture can create its test database."""
    from psycopg2.extensions import parse_dsn
    params = parse_dsn(DSN)
    assert params.get("host", "").startswith("/private/tmp/stima360-isolated-pg.")
    assert params["host"].endswith("/socket") and params.get("port") == "55473"
    assert os.getenv("PYTHON_DOTENV_DISABLED") == "1", "use the documented offline journey guard"


class _Links(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.links = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.links.extend(value for key, value in attrs if key == "href")


@pytest.fixture
def journey(mondo, monkeypatch, tmp_path):
    """Real persistence and providers, with external transport captured locally."""
    import uvicorn
    import database
    import pdf_report
    from communication import database as communication_database
    from fastapi.staticfiles import StaticFiles
    from operator_auth.context import SystemAgencyContext
    from operator_auth.dependencies import (
        legacy_basic_agency_context, require_authenticated_operator, require_operator,
    )

    main = importlib.import_module("main")
    m = mondo
    monkeypatch.setattr(main, "get_connection", lambda: m["psycopg2"].connect(m["dsn"]))
    monkeypatch.setattr(communication_database, "get_connection", lambda: m["psycopg2"].connect(m["dsn"]))
    for name in ("seller_intelligence.database", "followup.database", "property_watch.database"):
        monkeypatch.setattr(importlib.import_module(name), "get_connection", lambda: m["psycopg2"].connect(m["dsn"]))
    monkeypatch.setitem(main.core_service.repository.core_cursor.__wrapped__.__globals__,
                        "get_connection", lambda: m["psycopg2"].connect(m["dsn"]))
    monkeypatch.setattr(main, "_routed_public_stima_system_context", lambda conn, *, comune: (
        SystemAgencyContext(agency_id=1, origin="public_stima"),
        SimpleNamespace(agency_id=1, source="journey_fixture", matched_value=comune),
    ))
    for obj, method in (
        (main.seller_intelligence_service, "safe_record_event"),
        (main.followup_service, "safe_run_followup"),
        (main.property_watch_service, "safe_ensure_watch_for_stima"),
        (main.owner_provisioning, "safe_provision_for_public_stima"),
    ):
        monkeypatch.setattr(obj, method, lambda *a, **k: None)

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
    artifacts = Path(os.getenv("STIMA360_JOURNEY_ARTIFACTS", str(tmp_path)))
    artifacts.mkdir(parents=True, exist_ok=True)
    reports = artifacts / "reports"
    reports.mkdir(exist_ok=True)
    captures = {"smtp": [], "whatsapp": [], "pdf": []}
    failures = {"remaining": 0}

    def render(payload, nome_file="stima360.pdf"):
        if failures["remaining"]:
            failures["remaining"] -= 1
            raise RuntimeError("synthetic-journey-pdf-failure")
        pdf = pdf_report.genera_pdf_stima(payload, nome_file=nome_file)
        assert isinstance(pdf, bytes) and pdf.startswith(b"%PDF-")
        (reports / Path(nome_file).name).write_bytes(pdf)
        captures["pdf"].append({"filename": nome_file, "bytes": len(pdf),
                                "sha256": hashlib.sha256(pdf).hexdigest()})
        return pdf

    monkeypatch.setattr(main, "genera_pdf_stima", render)
    if hasattr(pdf_report, "_upload_pdf_to_github"):
        monkeypatch.setattr(pdf_report, "_upload_pdf_to_github", lambda *a, **k:
                            pytest.fail("private PDF reached an external upload boundary"))
    monkeypatch.setattr(main, "PUBLIC_BASE_URL", origin)
    monkeypatch.setattr(main, "PUBLIC_SITE_BASE_URL", origin + "/preview")
    monkeypatch.setattr(main, "WHATSAPP_SERVICE_URL", origin + "/fake-whatsapp-provider")

    class LocalSMTP:
        def __init__(self, host, port, timeout=20):
            assert timeout == 20
            assert (host, port) == ("smtp.example.invalid", 587)

        def ehlo(self):
            pass

        def starttls(self):
            pass

        def login(self, username, password):
            assert (username, password) == ("synthetic@example.invalid", "synthetic-fixture")

        def sendmail(self, sender, recipient, raw):
            message = message_from_string(raw)
            html = next(part.get_payload(decode=True).decode("utf-8")
                        for part in message.walk() if part.get_content_type() == "text/html")
            captures["smtp"].append({"to": recipient, "html": html, "links": _Links(html).links})

        def quit(self):
            pass

    monkeypatch.setattr(database.smtplib, "SMTP", LocalSMTP)
    for name, value in {"SMTP_HOST": "smtp.example.invalid", "SMTP_PORT": "587",
                        "SMTP_USER": "synthetic@example.invalid", "SMTP_PASS": "synthetic-fixture",
                        "ADMIN_EMAIL": "admin@example.invalid"}.items():
        monkeypatch.setenv(name, value)

    def whatsapp_post(url, *, json, timeout):
        assert url == origin + "/fake-whatsapp-provider" and timeout == 10
        captures["whatsapp"].append(json)
        return SimpleNamespace(status_code=200, text="synthetic provider accepted")

    monkeypatch.setattr(main, "requests", SimpleNamespace(post=whatsapp_post))
    monkeypatch.setattr(main.app.router, "routes", list(main.app.router.routes))
    site_directory = Path(os.getenv("STIMA360_SITE_DIR", str(ROOT / "site-preview/public")))
    main.app.mount("/preview", StaticFiles(directory=str(site_directory), html=True))
    # Test-only read endpoint lets the browser observe the committed quantities
    # before its accepted quick response is deliberately dropped. No tokens,
    # identities or production endpoint behavior are substituted here.
    main.app.add_api_route("/__journey_proof/quantities", lambda: _journey_quantities(m), methods=["GET"])
    overrides = dict(main.app.dependency_overrides)
    for dependency in (legacy_basic_agency_context, require_authenticated_operator, require_operator):
        overrides[dependency] = m["ctx"]
    monkeypatch.setattr(main.app, "dependency_overrides", overrides)

    server = uvicorn.Server(uvicorn.Config(main.app, log_level="warning", lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        threading.Event().wait(0.01)
    assert server.started, "local backend did not start"

    def browser(phase, input_data):
        input_file, output_file = artifacts / f"{phase}-input.json", artifacts / f"{phase}-output.json"
        input_file.write_text(json.dumps({"origin": origin, **input_data}), encoding="utf-8")
        node = os.getenv("STIMA360_NODE", "node")
        run = subprocess.run([node, str(ROOT / "tests/public_site_journey.mjs"), phase,
                              str(input_file), str(output_file)], text=True, capture_output=True, timeout=120)
        (artifacts / f"{phase}-browser.log").write_text(run.stdout + run.stderr, encoding="utf-8")
        assert run.returncode == 0, run.stdout + run.stderr
        return json.loads(output_file.read_text(encoding="utf-8"))

    try:
        yield SimpleNamespace(m=m, origin=origin, artifacts=artifacts, captures=captures, browser=browser,
                              fail_pdf_once=lambda: failures.update(remaining=1))
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        assert not thread.is_alive(), "local backend thread did not stop"
        _q(m, "DELETE FROM property_site_sources")


def test_public_capability_reaches_the_same_crm_property_from_pdf_email_and_whatsapp(journey):
    j = journey
    quick = j.browser("initial", {})
    sid = quick["quick"]["id"]
    token = quick["quick"]["token"]
    assert isinstance(sid, int) and sid > 0
    assert quick["stored"]["id"] == sid and quick["stored"]["token"] == token
    assert quick["quick"]["pdf_status"] == "ready"
    assert quick["pdf_bytes"] > 1000
    assert quick["prefill"]["id"] == sid
    assert quick["detail"]["token"] == token and int(quick["detail"]["stima_id"]) == sid
    property_id = _q(j.m, "SELECT property_id FROM property_site_sources WHERE stima_id=%s AND status='active'", (sid,))[0][0]
    before = _q(j.m, "SELECT count(*) FROM properties")[0][0]

    # Enqueue and dispatcher are real. The SMTP primitive receives only the
    # synthetic provider; 'sent' here means fake-provider acceptance, not delivery.
    from communication import dispatcher
    from communication.providers import email_smtp
    queued = _q(j.m, "SELECT id, status, destination_snapshot, rendered_body FROM communication_messages "
                    "WHERE stima_id=%s AND reason_code='stima_pdf'", (sid,))
    assert len(queued) == 1 and queued[0][1] == "queued"
    assert queued[0][2] == "journey@example.invalid"
    outbox_links = _Links(queued[0][3]).links
    email_link = next(link for link in outbox_links if "/pdf_redirect.html?" in link)
    whatsapp = j.captures["whatsapp"]
    assert len(whatsapp) == 1 and whatsapp[0]["to"] == "393331234567"
    whatsapp_link = whatsapp[0]["p3"]
    assert email_link == quick["quick"]["pdf_redirect_url"]
    assert parse_qs(urlparse(email_link).query) == {"token": [token]}
    for link in (whatsapp_link, quick["quick"]["detail_url"]):
        assert link.startswith(j.origin + "/preview/stima_dettagliata.html?")
        assert parse_qs(urlparse(link).query) == {"token": [token]}
    dispatched = dispatcher.dispatch_batch(j.m["ctx"](), channel="email", provider=email_smtp)
    assert dispatched["sent"] == 1, dispatched
    smtp_client = [message for message in j.captures["smtp"] if message["to"] == "journey@example.invalid"]
    assert len(smtp_client) == 1 and email_link in smtp_client[0]["links"]
    linked = j.browser("links", {"links": [email_link, whatsapp_link], "id": sid, "token": token})
    assert len(linked["submissions"]) == 2
    assert all(row["prefill_id"] == sid and row["token"] == token for row in linked["submissions"])
    assert _q(j.m, "SELECT count(*) FROM properties")[0][0] == before
    assert _q(j.m, "SELECT DISTINCT property_id FROM property_site_sources WHERE stima_id=%s AND status='active'", (sid,)) == [[property_id]]
    assert _q(j.m, "SELECT DISTINCT stima_id, agency_id FROM stime_dettagliate WHERE stima_id=%s", (sid,)) == [[sid, 1]]
    assert _q(j.m, "SELECT energy_class, heating FROM properties WHERE id=%s", (property_id,)) == [["D", "Autonomo"]]
    relation = _q(j.m, "SELECT p.agency_id, pl.relation_type, pc.role, l.agency_id, c.agency_id "
                      "FROM properties p JOIN property_leads pl ON pl.property_id=p.id "
                      "JOIN leads l ON l.id=pl.lead_id JOIN property_contacts pc ON pc.property_id=p.id "
                      "JOIN contacts c ON c.id=pc.contact_id WHERE p.id=%s", (property_id,))
    assert relation == [[1, "origin", "contact", 1, 1]]
    with urlopen(j.origin + f"/api/property/properties/{property_id}", timeout=10) as response:
        property_data = json.load(response)
    assert property_data["id"] == property_id and property_data["energy_class"] == "D"
    assert property_data["record_kind"] == "census" and property_data["commercial_status"] == "draft"
    crm = j.browser("crm", {"property_id": property_id, "stima_id": sid,
        "session": {"user_id": j.m["ids"]["owner_a"], "agency_id": 1, "agency_name": "Agenzia Uno",
                    "role": "agency_owner", "is_platform_admin": False, "expires_at": "2030-01-01T00:00:00Z"}})
    assert crm["energy_class"] == "D" and crm["heating"] == "Autonomo"
    result = {"stima_id": sid, "property_id": property_id, "capability_entry_points": ["pdf", "email", "whatsapp"],
              "smtp_messages_captured": len(j.captures["smtp"]), "whatsapp_messages_captured": len(whatsapp),
              "pdf": j.captures["pdf"], "outbox_fake_provider_sent": dispatched["sent"],
              "crm_property_api": "verified", "crm_database_relationships": "verified",
              "operator_crm_ui": "verified with synthetic session and real APIs", "external_delivery": "not attempted",
              "fixture_limitations": ["/api/successi requires case_vendute absent from the reused legacy fixture"]}
    (j.artifacts / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")


def test_lost_accepted_quick_response_recovers_without_another_post_or_row(journey):
    """A committed quick response lost in transit must never cause a retry."""
    j = journey
    before = _journey_quantities(j.m)
    lost = j.browser("lost-response", {})
    expected = {table: quantity + 1 for table, quantity in before.items()}
    assert lost["server_status"] == 200 and isinstance(lost["accepted_stima_id"], int)
    assert lost["after_commit"] == expected
    assert lost["after_terminal_submit"] == lost["after_reload"] == expected
    assert _journey_quantities(j.m) == expected
    assert lost["quick_posts"] == 1
    assert lost["reload_button_type"] == "button" and lost["reload_completed"]
    sid = lost["accepted_stima_id"]
    assert _q(j.m, "SELECT id FROM stime WHERE id=%s AND email='r1-lost-response@example.invalid'", (sid,)) == [[sid]]
    submission = _q(j.m, "SELECT status, property_id FROM site_submissions WHERE kind='quick' AND stima_id=%s", (sid,))
    assert len(submission) == 1 and submission[0][0] == "synced" and submission[0][1] is not None
    assert len(j.captures["whatsapp"]) == 1 and len(j.captures["pdf"]) == 1
    result = {"before": before, "after_commit": lost["after_commit"],
              "after_terminal_submit": lost["after_terminal_submit"], "after_reload": lost["after_reload"],
              "quick_posts": lost["quick_posts"], "server_status": lost["server_status"],
              "response_deliberately_lost": True, "reload_completed": True,
              "reload_button_type": "button", "duplicate_rows_created_after_commit": 0,
              "external_delivery": "not attempted"}
    (j.artifacts / "lost-response-proof.json").write_text(json.dumps(result, indent=2), encoding="utf-8")


def test_failed_pdf_recovers_on_the_saved_stima_without_another_submission(journey):
    j = journey
    before = _journey_quantities(j.m)
    j.fail_pdf_once()
    recovered = j.browser("pdf-recovery", {})
    expected = {table: count + 1 for table, count in before.items()}
    assert recovered["quick_posts"] == 1 and recovered["retry_posts"] == 1
    assert recovered["after_commit"] == recovered["after_retry"] == expected
    assert _journey_quantities(j.m) == expected
    sid = recovered["stima_id"]
    assert _q(j.m, "SELECT status, attempts FROM stima_pdf_artifacts WHERE stima_id=%s", (sid,)) == [["ready", 2]]
    assert _q(j.m, "SELECT count(*) FROM communication_messages WHERE stima_id=%s AND reason_code='stima_pdf'", (sid,)) == [[1]]
    assert len(j.captures["whatsapp"]) == 1 and len(j.captures["pdf"]) == 1
    submission = _q(j.m, "SELECT status, property_id FROM site_submissions WHERE kind='quick' AND stima_id=%s", (sid,))
    assert len(submission) == 1 and submission[0][0] == "synced" and submission[0][1] is not None
    proof = {"stima_id": sid, "property_id": submission[0][1], "before": before,
             "after_commit": recovered["after_commit"], "after_retry": recovered["after_retry"],
             "quick_posts": 1, "retry_posts": 1, "initial_pdf_status": "failed", "recovered_pdf_status": "ready",
             "outbox_messages": 1, "whatsapp_provider_calls": 1,
             "duplicate_rows_after_commit": 0, "external_delivery": "not attempted"}
    (j.artifacts / "pdf-recovery-proof.json").write_text(json.dumps(proof, indent=2), encoding="utf-8")
