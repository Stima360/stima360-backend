"""F04/F06 R1: a ready result stays readable while notifications are uncertain.

Opt-in on the documented private socket only (same disposable cluster and
fixtures as test_public_submission_receipts_postgres). Every provider is a
local capture; nothing is delivered externally.
"""
from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.test_catalogo_canonico_1_postgres import (  # noqa: F401
    DSN, completo, mondo, sito,
)
from tests.test_censimento_3_backend_postgres import _q
from tests.test_public_submission_receipts_postgres import (  # noqa: F401
    _counts, _links, _only_the_disposable_cluster, _post, _receipt, _resume, submission,
)

pytestmark = pytest.mark.skipif(not DSN, reason="isolated PostgreSQL required")


def _sent(p):
    return {key: len(value) for key, value in p.deliveries.items()}


def _receipt_rows(p, payload):
    return _q(p.s.m, "SELECT count(*) FROM public_submission_receipts WHERE request_id=%s",
              (payload["request_id"],))[0][0]


def _assert_ready_result_is_usable(p, body):
    """Same private PDF and authorized detail through the same capability."""
    sid, token = body["id"], body["token"]
    assert body["pdf_status"] == "ready"
    pdf = p.client.get(f"/api/stime/{sid}/pdf", params={"t": token})
    assert pdf.status_code == 200, pdf.text
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF-")
    assert pdf.headers["cache-control"].startswith("private, no-store")
    stored = _q(p.s.m, "SELECT sha256 FROM stima_pdf_artifacts WHERE stima_id=%s", (sid,))[0][0]
    import hashlib
    assert hashlib.sha256(pdf.content).hexdigest() == stored
    prefill = p.client.get("/api/prefill", params={"t": token})
    assert prefill.status_code == 200, prefill.text
    assert prefill.json()["id"] == sid
    return pdf.content


def _notification_attempt(p, channel, behaviour):
    original_name = "invia_mail" if channel == "admin_email" else "invia_whatsapp"
    bucket = "mail" if channel == "admin_email" else "whatsapp"

    def uncertain(*a, **k):
        p.deliveries[bucket].append((a, k))
        if behaviour == "timeout":
            raise TimeoutError("synthetic-provider-timeout")
        return False

    return original_name, uncertain, bucket


@pytest.mark.parametrize("channel", ["admin_email", "whatsapp"])
@pytest.mark.parametrize("behaviour", ["false", "timeout"])
def test_uncertain_notification_keeps_attention_and_exposes_the_ready_result(submission, monkeypatch, channel, behaviour):
    p = submission
    payload = p.identity()
    name, uncertain, bucket = _notification_attempt(p, channel, behaviour)
    monkeypatch.setattr(p.s.main, name, uncertain)
    first = _post(p, payload)
    receipt = first["receipt"]
    assert receipt["status"] == "attention" and receipt["resumable"] is False
    assert receipt["steps"][channel] == "indeterminate"
    assert receipt["notifications"][channel] == "indeterminate"
    assert receipt["result_available"] is True
    # Never reported as a fully successful delivery.
    assert first["success"] is False and first["ok"] is False
    assert receipt["contact_id"] and receipt["lead_id"] and receipt["property_id"]
    pdf = _assert_ready_result_is_usable(p, first)
    before, sent, links = _counts(p), _sent(p), _links(p, first["id"])
    assert sent[bucket] == 1
    # Reload, lost response, explicit resume and an identical replay.
    for again in (_receipt(p, payload).json(), _receipt(p, payload).json(),
                  _resume(p, payload).json(), _post(p, payload)):
        assert again["id"] == first["id"] and again["token"] == first["token"]
        assert again["receipt"]["request_id"] == payload["request_id"]
        assert again["receipt"]["status"] == "attention"
        assert again["receipt"]["result_available"] is True
        assert again["receipt"]["steps"][channel] == "indeterminate"
    assert _sent(p) == sent, "an uncertain notification is never sent again automatically"
    assert _counts(p) == before
    assert _links(p, first["id"]) == links
    assert _receipt_rows(p, payload) == 1
    assert _assert_ready_result_is_usable(p, first) == pdf


@pytest.mark.parametrize("channel", ["admin_email", "whatsapp"])
def test_definite_failure_before_transport_exposes_result_and_keeps_safe_recovery(submission, monkeypatch, channel):
    p = submission
    payload = p.identity()
    bucket = "mail" if channel == "admin_email" else "whatsapp"
    if channel == "admin_email":
        monkeypatch.delenv("SMTP_PASS", raising=False)
    else:
        monkeypatch.setattr(p.s.main, "WHATSAPP_SERVICE_URL", "")
    first = _post(p, payload)
    receipt = first["receipt"]
    assert receipt["status"] == "partial" and receipt["resumable"] is True
    assert receipt["steps"][channel] == "failed"
    assert receipt["errors"][channel]["before_transport"] is True
    assert receipt["result_available"] is True
    assert first["success"] is False
    assert p.deliveries[bucket] == []
    _assert_ready_result_is_usable(p, first)
    before = _counts(p)
    # Reading again never retries; nothing was sent.
    assert _receipt(p, payload).json()["receipt"]["result_available"] is True
    assert p.deliveries[bucket] == []
    if channel == "admin_email":
        monkeypatch.setenv("SMTP_PASS", "synthetic-fixture")
    else:
        monkeypatch.setattr(p.s.main, "WHATSAPP_SERVICE_URL", "http://127.0.0.1:55888/fake-whatsapp-provider")
    recovered = _resume(p, payload)
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["receipt"]["status"] == "completed"
    assert recovered.json()["receipt"]["result_available"] is True
    assert recovered.json()["id"] == first["id"] and recovered.json()["token"] == first["token"]
    assert len(p.deliveries[bucket]) == 1
    assert _resume(p, payload).json()["receipt"]["status"] == "completed"
    assert len(p.deliveries[bucket]) == 1
    assert _counts(p) == before
    assert _receipt_rows(p, payload) == 1


def test_completed_receipt_also_certifies_the_ready_result(submission):
    p = submission
    payload = p.identity()
    first = _post(p, payload)
    assert first["receipt"]["status"] == "completed"
    assert first["receipt"]["result_available"] is True
    assert first["receipt"]["notifications"] == {"admin_email": "succeeded", "whatsapp": "succeeded"}
    _assert_ready_result_is_usable(p, first)


def test_bridge_failure_never_exposes_a_result(submission, monkeypatch):
    p = submission
    payload = p.identity()
    original = p.s.main.core_service.bridge_public_stima

    def failing(*a, **k):
        raise RuntimeError("synthetic-bridge-failure")

    monkeypatch.setattr(p.s.main.core_service, "bridge_public_stima", failing)
    first = _post(p, payload)
    assert first["receipt"]["status"] == "partial" and first["receipt"]["resumable"] is True
    assert first["receipt"]["result_available"] is False
    assert first["success"] is False
    assert _receipt(p, payload).json()["receipt"]["result_available"] is False
    assert all(not value for value in p.deliveries.values())
    monkeypatch.setattr(p.s.main.core_service, "bridge_public_stima", original)
    recovered = _resume(p, payload).json()
    assert recovered["id"] == first["id"]
    assert recovered["receipt"]["status"] == "completed" and recovered["receipt"]["result_available"] is True
    assert _receipt_rows(p, payload) == 1


def test_missing_property_link_never_exposes_a_result(submission, monkeypatch):
    p = submission
    payload = p.identity()
    monkeypatch.setattr(p.s.main.property_site_sync, "sync_public_stima", lambda *a, **k: None)
    first = _post(p, payload)
    assert first["receipt"]["status"] == "partial"
    assert first["receipt"]["result_available"] is False
    assert first["receipt"]["property_id"] is None


def test_pdf_failure_keeps_explicit_recovery_without_false_success(submission, monkeypatch):
    p = submission
    payload = p.identity()

    def broken(*a, **k):
        raise RuntimeError("synthetic-renderer-failure")

    monkeypatch.setattr(p.s.main, "genera_pdf_stima", broken)
    first = _post(p, payload)
    assert first["pdf_status"] == "failed"
    assert first["receipt"]["status"] == "partial"
    assert first["receipt"]["result_available"] is False
    assert first["success"] is False


def test_interrupted_save_before_final_receipt_is_not_a_result(submission, monkeypatch):
    p = submission
    payload = p.identity()
    import importlib
    module = importlib.import_module("public_submissions")
    original = module.Receipt.checkpoint
    lost = []

    def lose(self, name, state, *a, **k):
        if name == "admin_email" and state == "succeeded" and not lost:
            lost.append(1)
            raise RuntimeError("synthetic-lost-admin-checkpoint")
        return original(self, name, state, *a, **k)

    monkeypatch.setattr(module.Receipt, "checkpoint", lose)
    first = _post(p, payload)
    assert first["receipt"]["status"] == "partial"
    assert first["receipt"]["result_available"] is False
    resumed = _resume(p, payload).json()
    assert resumed["receipt"]["status"] == "attention"
    assert resumed["receipt"]["steps"]["admin_email"] == "indeterminate"
    assert resumed["receipt"]["result_available"] is True
    assert len(p.deliveries["mail"]) == 1


def test_expired_capability_withdraws_result_access(submission, monkeypatch):
    p = submission
    payload = p.identity()
    name, uncertain, _ = _notification_attempt(p, "admin_email", "false")
    monkeypatch.setattr(p.s.main, name, uncertain)
    first = _post(p, payload)
    assert first["receipt"]["result_available"] is True
    _q(p.s.m, "UPDATE stime SET token_expires=NOW() - INTERVAL '1 minute' WHERE id=%s RETURNING id", (first["id"],))
    again = _receipt(p, payload).json()
    assert again["receipt"]["status"] == "attention"
    assert again["receipt"]["result_available"] is False
    assert p.client.get(f"/api/stime/{first['id']}/pdf", params={"t": first["token"]}).status_code == 404


def test_concurrent_double_submit_with_uncertain_notification_creates_one_result(submission, monkeypatch):
    p = submission
    payload = p.identity()
    entered, release = threading.Event(), threading.Event()
    calls = []

    def slow_uncertain(*a, **k):
        calls.append(1)
        entered.set()
        assert release.wait(15)
        return False

    monkeypatch.setattr(p.s.main, "invia_mail", slow_uncertain)
    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(p.client.post, "/api/salva_stima", json=payload)
        assert entered.wait(15)
        two = pool.submit(p.client.post, "/api/salva_stima", json=payload)
        release.set()
        responses = [one.result(timeout=60), two.result(timeout=60)]
    bodies = [r.json() for r in responses]
    assert [r.status_code for r in responses] == [202, 202]
    assert len(calls) == 1
    assert bodies[0]["id"] == bodies[1]["id"] and bodies[0]["token"] == bodies[1]["token"]
    assert all(b["receipt"]["status"] == "attention" and b["receipt"]["result_available"] for b in bodies)
    assert _receipt_rows(p, payload) == 1
    assert _q(p.s.m, "SELECT count(*) FROM stime WHERE email=%s", (payload["email"],))[0][0] == 1
    assert _q(p.s.m, "SELECT count(*) FROM communication_messages WHERE stima_id=%s", (bodies[0]["id"],))[0][0] == 1


def test_detail_after_attention_result_is_authorized_once(submission, monkeypatch):
    p = submission
    payload = p.identity()
    name, uncertain, _ = _notification_attempt(p, "whatsapp", "timeout")
    monkeypatch.setattr(p.s.main, name, uncertain)
    first = _post(p, payload)
    assert first["receipt"]["result_available"] is True
    detail = {"stima_id": first["id"], "token": first["token"], "request_id": str(uuid.uuid4()),
              "receipt_key": uuid.uuid4().hex + uuid.uuid4().hex,
              "classe": "D", "riscaldamento": "Autonomo", "campi_dichiarati": ["classe", "riscaldamento"]}
    saved = p.client.post("/api/salva_stima_dettagliata", json=detail)
    assert saved.status_code == 200, saved.text
    assert saved.json()["receipt"]["status"] == "completed"
    assert saved.json()["receipt"].get("result_available") is False  # quick-only certification
    before = _counts(p)
    assert p.client.post("/api/salva_stima_dettagliata", json=detail).status_code == 200
    assert _counts(p) == before
    assert _q(p.s.m, "SELECT count(*) FROM stime_dettagliate WHERE stima_id=%s", (first["id"],)) == [[1]]
