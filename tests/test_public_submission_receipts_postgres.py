"""F04/F06: request replay on real isolated PostgreSQL and the public writer.

Opt-in on the documented private socket only. Providers are locally captured;
no production target or externally delivered notification is available here.
"""
from __future__ import annotations

import asyncio
import importlib
import hashlib
import os
import threading
import uuid
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tests.test_catalogo_canonico_1_postgres import (
    COMPLETA, DSN, _persona, completo, mondo, sito,  # noqa: F401
)
from tests.test_censimento_3_backend_postgres import _in_attesa_di_lock, _q

pytestmark = pytest.mark.skipif(not DSN, reason="isolated PostgreSQL required")


@pytest.fixture(scope="module", autouse=True)
def _only_the_disposable_cluster():
    from psycopg2.extensions import parse_dsn
    params = parse_dsn(DSN)
    assert params.get("host") == "/private/tmp/stima360-isolated-pg.vQlFs50D/socket"
    assert params.get("port") == "55473"
    assert os.getenv("PYTHON_DOTENV_DISABLED") == "1"


@pytest.fixture
def submission(sito, monkeypatch):
    s = sito
    deliveries = {"mail": [], "whatsapp": [], "outbox": []}
    def deliver(channel):
        def accepted(*a, **k):
            deliveries[channel].append((a, k))
            return True
        return accepted

    monkeypatch.setattr(s.main, "invia_mail", deliver("mail"))
    monkeypatch.setattr(s.main, "invia_whatsapp", deliver("whatsapp"))
    connection_factory = lambda: s.m["psycopg2"].connect(s.m["dsn"])
    for module_name in ("communication.database", "followup.database", "seller_intelligence.database",
                        "property_watch.database"):
        module = importlib.import_module(module_name)
        monkeypatch.setattr(module, "get_connection", connection_factory)
    # The legacy catalog fixture stubs enqueue; restore this real module so
    # request receipts exercise the existing durable email ledger.
    original_enqueue = importlib.reload(s.main.communication_service).enqueue

    def enqueue(*a, **k):
        result = original_enqueue(*a, **k)
        deliveries["outbox"].append(result)
        return result

    monkeypatch.setattr(s.main.communication_service, "enqueue", enqueue)
    monkeypatch.setattr(s.main, "PUBLIC_BASE_URL", "http://127.0.0.1:55888")
    monkeypatch.setattr(s.main, "PUBLIC_SITE_BASE_URL", "http://127.0.0.1:55888/preview")
    for name, value in {"SMTP_HOST": "127.0.0.1", "SMTP_PORT": "587", "SMTP_USER": "synthetic@example.invalid",
                        "SMTP_PASS": "synthetic-fixture", "ADMIN_EMAIL": "admin@example.invalid"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(s.main, "WHATSAPP_SERVICE_URL", "http://127.0.0.1:55888/fake-whatsapp-provider")
    client = TestClient(s.main.app, raise_server_exceptions=False)

    def identity(**changes):
        request_id = str(uuid.uuid4())
        return {**COMPLETA, **_persona(email=f"receipt.{uuid.uuid4().hex}@example.invalid"),
                "request_id": request_id, "client_request_id": request_id,
                "receipt_key": uuid.uuid4().hex + uuid.uuid4().hex, **changes}

    yield SimpleNamespace(s=s, client=client, deliveries=deliveries, identity=identity)
    client.close()


def _counts(p):
    return {table: _q(p.s.m, f"SELECT count(*) FROM {table}")[0][0]
            for table in ("stime", "stime_dettagliate", "contacts", "leads", "lead_stime", "properties",
                          "property_site_sources", "site_submissions", "communication_messages",
                          "activities", "tasks", "followup_actions", "seller_timeline_events", "stima_pdf_artifacts",
                          "property_watches", "property_watch_observations", "owner_stima_access")}


def _links(p, sid):
    rows = _q(p.s.m, "SELECT s.id, s.agency_id, l.contact_id, l.id, pss.property_id "
                    "FROM stime s LEFT JOIN lead_stime ls ON ls.stima_id=s.id "
                    "LEFT JOIN leads l ON l.id=ls.lead_id "
                    "LEFT JOIN property_site_sources pss ON pss.stima_id=s.id AND pss.status='active' "
                    "WHERE s.id=%s", (sid,))
    return [list(row) for row in rows]


def _post(p, payload):
    response = p.client.post("/api/salva_stima", json=payload)
    assert response.status_code in (200, 202), response.text
    return response.json()


def test_repeated_quick_request_keeps_one_stima_and_all_crm_links(submission):
    p = submission
    payload = p.identity()
    first = _post(p, payload)
    before, notifications, links = _counts(p), {key: len(value) for key, value in p.deliveries.items()}, _links(p, first["id"])
    replay = _post(p, payload)
    print({"scenario": "quick_replay", "first_id": first["id"], "replay_id": replay["id"],
           "before": before, "after": _counts(p), "notifications": {key: len(value) for key, value in p.deliveries.items()}})
    assert replay["id"] == first["id"]
    assert _links(p, replay["id"]) == links
    assert _counts(p) == before
    assert {key: len(value) for key, value in p.deliveries.items()} == notifications
    assert replay["token"] == first["token"]


def test_concurrent_same_quick_request_creates_one_stima_and_one_notification(submission, monkeypatch):
    p = submission
    payload = p.identity()
    entered, release = threading.Event(), threading.Event()
    original = p.s.main.core_service.bridge_public_stima

    def blocked_first_bridge(*args, **kwargs):
        entered.set()
        assert release.wait(15), "test did not release the first bridge"
        return original(*args, **kwargs)

    monkeypatch.setattr(p.s.main.core_service, "bridge_public_stima", blocked_first_bridge)
    before = _counts(p)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(_post, p, payload)
        assert entered.wait(10), "first request did not reach the bridge"
        second = pool.submit(_post, p, payload)
        try:
            assert _in_attesa_di_lock(p.s.m, "%pg_advisory_lock%"), "second request did not wait on the same request identity"
        finally:
            release.set()
        results = [first.result(timeout=30), second.result(timeout=30)]
    print({"scenario": "quick_concurrent", "ids": [row["id"] for row in results],
           "before": before, "after": _counts(p), "notifications": {key: len(value) for key, value in p.deliveries.items()}})
    assert results[0]["id"] == results[1]["id"]
    assert _counts(p)["stime"] == before["stime"] + 1
    assert len(p.deliveries["outbox"]) == 1
    assert len(p.deliveries["whatsapp"]) == 1


def test_detail_replay_keeps_one_detail_row_and_one_sync(submission):
    p = submission
    first = _post(p, p.identity())
    payload = {"stima_id": first["id"], "token": first["token"], "request_id": str(uuid.uuid4()),
               "receipt_key": uuid.uuid4().hex + uuid.uuid4().hex,
               "classe": "D", "riscaldamento": "Autonomo", "campi_dichiarati": ["classe", "riscaldamento"]}
    response = p.client.post("/api/salva_stima_dettagliata", json=payload)
    assert response.status_code in (200, 202), response.text
    before = _counts(p)
    replay = p.client.post("/api/salva_stima_dettagliata", json=payload)
    assert replay.status_code in (200, 202), replay.text
    print({"scenario": "detail_replay", "before": before, "after": _counts(p)})
    assert _counts(p) == before
    assert _q(p.s.m, "SELECT count(*) FROM stime_dettagliate WHERE stima_id=%s", (first["id"],)) == [[1]]


def test_bridge_partial_failure_recovers_original_stima_without_repeating_deliveries(submission, monkeypatch):
    p = submission
    payload = p.identity()
    original = p.s.main.core_service.bridge_public_stima

    def failing(*args, **kwargs):
        raise RuntimeError("synthetic-bridge-failure")

    monkeypatch.setattr(p.s.main.core_service, "bridge_public_stima", failing)
    first = _post(p, payload)
    sid = first["id"]
    assert _q(p.s.m, "SELECT count(*) FROM lead_stime WHERE stima_id=%s", (sid,)) == [[0]]
    assert first["receipt"]["status"] == "partial" and first["receipt"]["resumable"]
    assert all(not value for value in p.deliveries.values())
    monkeypatch.setattr(p.s.main.core_service, "bridge_public_stima", original)
    recovered = _post(p, payload)
    print({"scenario": "bridge_partial", "saved_id": sid, "recovery_id": recovered["id"],
           "original_links": _links(p, sid), "recovery_links": _links(p, recovered["id"])})
    assert recovered["id"] == sid
    assert _links(p, sid)[0][2:5] == _links(p, recovered["id"])[0][2:5]
    assert all(value is not None for value in _links(p, sid)[0][2:5])
    assert all(len(value) == 1 for value in p.deliveries.values())


def test_missing_property_inbox_recovers_original_stima_and_existing_crm_bridge(submission, monkeypatch):
    p = submission
    payload = p.identity()
    original = p.s.main.property_site_sync.sync_public_stima
    monkeypatch.setattr(p.s.main.property_site_sync, "sync_public_stima", lambda *a, **k: None)
    first = _post(p, payload)
    sid = first["id"]
    links = _links(p, sid)
    assert links[0][2] and links[0][3] and links[0][4] is None
    assert first["receipt"]["status"] == "partial" and first["receipt"]["resumable"]
    assert all(not value for value in p.deliveries.values())
    monkeypatch.setattr(p.s.main.property_site_sync, "sync_public_stima", original)
    recovered = _post(p, payload)
    print({"scenario": "inbox_partial", "saved_id": sid, "recovery_id": recovered["id"],
           "original_links": _links(p, sid), "recovery_links": _links(p, recovered["id"])})
    assert recovered["id"] == sid
    assert _links(p, sid)[0][2:4] == links[0][2:4]
    assert _links(p, sid)[0][4] is not None
    assert all(len(value) == 1 for value in p.deliveries.values())


def test_intentional_new_quick_identity_allows_a_second_valuation(submission):
    p = submission
    payload = p.identity()
    first = _post(p, payload)
    different_id = str(uuid.uuid4())
    second = _post(p, {**payload, "request_id": different_id, "client_request_id": different_id,
                       "receipt_key": uuid.uuid4().hex + uuid.uuid4().hex})
    assert second["id"] != first["id"]
    assert second["token"] != first["token"]
    one, two = _links(p, first["id"])[0], _links(p, second["id"])[0]
    assert one[2] == two[2]  # existing contact deduplication remains in the bridge
    assert one[3] != two[3] and one[4] != two[4]
    assert len(p.deliveries["outbox"]) == 2
    assert len(p.deliveries["whatsapp"]) == 2


def test_disabled_owner_account_preserves_new_public_valuation_without_new_grant(submission):
    p = submission
    payload = p.identity()
    first = _post(p, payload)
    assert first["receipt"]["status"] == "completed"
    first_links = _links(p, first["id"])[0]
    contact_id = first_links[2]
    accounts = _q(p.s.m, "SELECT id FROM owner_accounts WHERE contact_id=%s", (contact_id,))
    assert len(accounts) == 1
    account_id = accounts[0][0]
    grants_before = _q(p.s.m, "SELECT stima_id FROM owner_stima_access WHERE owner_account_id=%s ORDER BY stima_id", (account_id,))
    assert grants_before == [[first["id"]]]
    _q(p.s.m, "UPDATE owner_accounts SET status='disabled',disabled_at=NOW() WHERE id=%s", (account_id,))
    intentional_id = str(uuid.uuid4())
    new_payload = {**payload, "request_id": intentional_id, "client_request_id": intentional_id,
                   "receipt_key": uuid.uuid4().hex + uuid.uuid4().hex}
    response = p.client.post("/api/salva_stima", json=new_payload)
    assert response.status_code == 200, response.text
    second = response.json()
    assert second["receipt"]["status"] == "completed"
    assert second["receipt"]["steps"]["owner"] == "not_applicable"
    second_links = _links(p, second["id"])[0]
    assert second["id"] != first["id"] and second_links[2] == contact_id
    assert second_links[3] != first_links[3] and second_links[4] != first_links[4]
    assert all(second_links[3:5])
    assert _q(p.s.m, "SELECT status,disabled_at IS NOT NULL FROM owner_accounts WHERE id=%s", (account_id,)) == [["disabled", True]]
    assert _q(p.s.m, "SELECT stima_id FROM owner_stima_access WHERE owner_account_id=%s ORDER BY stima_id", (account_id,)) == grants_before
    assert _q(p.s.m, "SELECT count(*) FROM stima_pdf_artifacts WHERE stima_id=%s AND status='ready'", (second["id"],)) == [[1]]
    assert all(len(values) == 2 for values in p.deliveries.values())
    before = _counts(p)
    assert _post(p, new_payload)["receipt"]["steps"]["owner"] == "not_applicable"
    assert _counts(p) == before and all(len(values) == 2 for values in p.deliveries.values())


def _receipt(p, payload, *, proof=None):
    return p.client.get(f"/api/submissions/{payload['request_id']}",
                        headers={"X-Receipt-Key": payload["receipt_key"] if proof is None else proof})


def _resume(p, payload, *, proof=None):
    return p.client.post(f"/api/submissions/{payload['request_id']}/resume",
                         headers={"X-Receipt-Key": payload["receipt_key"] if proof is None else proof})


def test_receipt_recovers_lost_response_without_repeating_successful_steps(submission):
    p = submission
    payload = p.identity()
    # Discard the HTTP response exactly as a browser whose connection is lost
    # after persistence would do; the browser already retained both proofs.
    saved = _post(p, payload)
    before = _counts(p)
    notifications = {key: len(value) for key, value in p.deliveries.items()}
    receipt = _receipt(p, payload)
    assert receipt.status_code == 200, receipt.text
    result = receipt.json()
    assert result["id"] == saved["id"] and result["token"] == saved["token"]
    assert result["receipt"]["status"] == "completed" and not result["receipt"]["resumable"]
    assert result["receipt"]["request_id"] == payload["request_id"]
    assert result["receipt"]["stima_id"] == saved["id"]
    assert _resume(p, payload).json()["id"] == saved["id"]
    assert _counts(p) == before
    assert {key: len(value) for key, value in p.deliveries.items()} == notifications


@pytest.mark.parametrize("kind", ["missing", "wrong", "unknown", "malformed"])
def test_receipt_proof_does_not_disclose_the_saved_stima(submission, kind):
    p = submission
    payload = p.identity()
    saved = _post(p, payload)
    before = _counts(p)
    proof = "" if kind == "missing" else uuid.uuid4().hex + uuid.uuid4().hex
    target = dict(payload)
    if kind == "unknown":
        target["request_id"] = str(uuid.uuid4())
        proof = payload["receipt_key"]
    elif kind == "malformed":
        target["request_id"] = "not-a-uuid"
    for method in (_receipt, _resume):
        result = method(p, target, proof=proof)
        assert result.status_code in (400, 403, 404), result.text
        assert "token" not in result.json() and "pdf_url" not in result.json()
        assert str(saved["token"]) not in result.text
    assert _counts(p) == before


def test_same_identity_refuses_a_different_payload_without_overwriting(submission):
    p = submission
    payload = p.identity()
    saved = _post(p, payload)
    before, links = _counts(p), _links(p, saved["id"])
    response = p.client.post("/api/salva_stima", json={**payload, "mq": "199"})
    assert response.status_code == 409, response.text
    assert _counts(p) == before and _links(p, saved["id"]) == links
    assert _receipt(p, payload).json()["id"] == saved["id"]


def test_public_writers_require_identity_and_receipt_proof(submission):
    p = submission
    payload = p.identity()
    for missing in ("request_id", "receipt_key"):
        incomplete = {key: value for key, value in payload.items() if key != missing}
        if missing == "request_id":
            incomplete.pop("client_request_id", None)
        response = p.client.post("/api/salva_stima", json=incomplete)
        assert response.status_code == 400, response.text
    assert not any(p.deliveries.values())


def test_partial_bridge_checkpoint_reuses_committed_core_records(submission, monkeypatch):
    p = submission
    payload = p.identity()
    original = p.s.main.core_service.bridge_public_stima
    results = []

    def lost_checkpoint(*a, **k):
        result = original(*a, **k)
        results.append(result)
        if len(results) == 1:
            raise RuntimeError("synthetic-result-lost-after-core-commit")
        return result

    monkeypatch.setattr(p.s.main.core_service, "bridge_public_stima", lost_checkpoint)
    first = _post(p, payload)
    sid = first["id"]
    assert first["receipt"]["status"] == "partial"
    core_links = _links(p, sid)[0][2:4]
    assert all(value is not None for value in core_links)
    assert all(not value for value in p.deliveries.values())
    repaired = _resume(p, payload)
    assert repaired.status_code == 200, repaired.text
    assert repaired.json()["id"] == sid
    assert _links(p, sid)[0][2:4] == core_links
    assert [result["status"] for result in results] == ["linked", "already_linked"]
    assert all(len(value) == 1 for value in p.deliveries.values())


def test_detail_request_cannot_rebind_to_another_authorized_parent(submission):
    p = submission
    one, two = _post(p, p.identity()), _post(p, p.identity())
    identity = p.identity()
    payload = {"request_id": identity["request_id"], "receipt_key": identity["receipt_key"],
               "stima_id": one["id"], "token": one["token"], "classe": "D"}
    first = p.client.post("/api/salva_stima_dettagliata", json=payload)
    assert first.status_code == 200, first.text
    before = _counts(p)
    forged = p.client.post("/api/salva_stima_dettagliata",
                           json={**payload, "stima_id": two["id"], "token": two["token"]})
    assert forged.status_code == 409, forged.text
    assert _counts(p) == before
    wrong_token_identity = p.identity()
    wrong = p.client.post("/api/salva_stima_dettagliata", json={**payload,
                         "request_id": wrong_token_identity["request_id"],
                         "receipt_key": wrong_token_identity["receipt_key"], "token": two["token"]})
    assert wrong.status_code == 403, wrong.text
    assert _counts(p) == before


def test_uncertain_admin_delivery_is_not_automatically_sent_again(submission, monkeypatch):
    p = submission
    payload = p.identity()

    def uncertain(*a, **k):
        p.deliveries["mail"].append((a, k))
        return False

    monkeypatch.setattr(p.s.main, "invia_mail", uncertain)
    first = _post(p, payload)
    assert first["receipt"]["status"] == "attention"
    assert first["receipt"]["steps"]["admin_email"] == "indeterminate"
    assert len(p.deliveries["mail"]) == 1
    before = _counts(p)
    assert _receipt(p, payload).json()["id"] == first["id"]
    assert _resume(p, payload).json()["id"] == first["id"]
    assert _post(p, payload)["id"] == first["id"]
    assert len(p.deliveries["mail"]) == 1
    assert _counts(p) == before


@pytest.fixture
def followup_case(mondo, monkeypatch):
    from core import service as core_service
    from followup import database as followup_database
    from followup import repository, service
    from operator_auth.context import SystemAgencyContext
    m = mondo
    monkeypatch.setattr(followup_database, "get_connection", lambda: m["psycopg2"].connect(m["dsn"]))
    tag = uuid.uuid4().hex
    sid = _q(m, "INSERT INTO stime (agency_id, comune, microzona, nome, cognome, email, telefono) "
                "VALUES (1, 'Tortoreto', 'Lido Sud', 'Followup', 'Sintetico', %s, %s) RETURNING id",
             (f"followup.{tag}@example.invalid", f"+39 333 {int(tag[:6], 16) % 10000000:07d}"))[0][0]
    bridge = core_service.bridge_public_stima(
        sid, first_name="Followup", last_name="Sintetico", email=f"followup.{tag}@example.invalid",
        phone=None, marketing_consent=False, marketing_consent_at=None,
        system_ctx=SystemAgencyContext(agency_id=1, origin="public_stima"))
    deadline = datetime(2026, 10, 8, 9, 0, tzinfo=timezone.utc)
    kwargs = {"rule_code": "FOLLOWUP_STIMA_RICHIESTA", "trigger_type": "event",
              "stima_id": sid, "contact_id": bridge["contact_id"], "lead_id": bridge["lead_id"],
              "created_by": "TEST_SYNTHETIC", "due_at_override": deadline}

    def pending(status):
        return _q(m, "INSERT INTO followup_actions (rule_code, trigger_type, stima_id, contact_id, lead_id, "
                     "idempotency_key, status, error_message, agency_id) "
                     "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1) RETURNING id",
                  (kwargs["rule_code"], kwargs["trigger_type"], sid, kwargs["contact_id"], kwargs["lead_id"],
                   f"followup:stima_richiesta:{sid}", status, "synthetic" if status == "failed" else None))[0][0]

    yield SimpleNamespace(m=m, service=service, repository=repository, kwargs=kwargs, pending=pending, sid=sid, deadline=deadline)


@pytest.mark.parametrize("status", ["pending", "failed"])
def test_followup_recovery_completes_original_action_with_frozen_deadline(followup_case, status):
    from followup.exceptions import ConflictError
    f = followup_case
    action_id = f.pending(status)
    with pytest.raises(ConflictError):
        f.service.run_followup(**f.kwargs)
    completed = f.service.run_followup(**f.kwargs, recover=True)
    replay = f.service.run_followup(**f.kwargs, recover=True)
    assert completed["followup_action_id"] == replay["followup_action_id"] == action_id
    assert replay["status"] == "already_completed"
    assert completed["task_id"] == replay["task_id"]
    assert _q(f.m, "SELECT status, task_id, error_message FROM followup_actions WHERE id=%s", (action_id,)) == [["completed", completed["task_id"], None]]
    assert _q(f.m, "SELECT count(*), min(due_at) FROM tasks WHERE stima_id=%s", (f.sid,)) == [[1, f.deadline]]


@pytest.mark.parametrize("prior", [False, True])
def test_followup_initial_worker_and_recovery_are_serialized(followup_case, monkeypatch, prior):
    f = followup_case
    if prior:
        f.pending("pending")
    original = f.repository.core_repository.create_task_with_cursor
    entered, release = threading.Event(), threading.Event()
    calls = []

    def blocked(cur, data):
        calls.append(dict(data))
        entered.set()
        assert release.wait(15), "test did not release the task transaction"
        return original(cur, data)

    monkeypatch.setattr(f.repository.core_repository, "create_task_with_cursor", blocked)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(f.service.run_followup, **f.kwargs, recover=prior)
        assert entered.wait(10)
        second = pool.submit(f.service.run_followup, **f.kwargs, recover=True)
        try:
            assert _in_attesa_di_lock(f.m, "%followup_actions%"), "second worker did not wait on the original action"
        finally:
            release.set()
        results = [first.result(timeout=30), second.result(timeout=30)]
    assert results[0]["task_id"] == results[1]["task_id"]
    assert results[0]["followup_action_id"] == results[1]["followup_action_id"]
    assert len(calls) == 1
    assert _q(f.m, "SELECT count(*) FROM tasks WHERE stima_id=%s", (f.sid,)) == [[1]]


def test_followup_lost_receipt_after_task_commit_returns_existing_task(followup_case):
    f = followup_case
    committed = f.service.run_followup(**f.kwargs)
    resumed = f.service.run_followup(**f.kwargs, recover=True)
    assert resumed["status"] == "already_completed"
    assert resumed["task_id"] == committed["task_id"]
    assert _q(f.m, "SELECT count(*) FROM tasks WHERE stima_id=%s", (f.sid,)) == [[1]]


def test_followup_task_failure_is_durable_and_recoverable(followup_case, monkeypatch):
    f = followup_case
    original = f.repository.core_repository.create_task_with_cursor
    monkeypatch.setattr(f.repository.core_repository, "create_task_with_cursor", lambda *a, **k:
                        (_ for _ in ()).throw(RuntimeError("synthetic-task-write-failure")))
    with pytest.raises(RuntimeError, match="synthetic-task-write-failure"):
        f.service.run_followup(**f.kwargs)
    assert _q(f.m, "SELECT status FROM followup_actions WHERE stima_id=%s", (f.sid,)) == [["failed"]]
    assert _q(f.m, "SELECT count(*) FROM tasks WHERE stima_id=%s", (f.sid,)) == [[0]]
    monkeypatch.setattr(f.repository.core_repository, "create_task_with_cursor", original)
    recovered = f.service.run_followup(**f.kwargs, recover=True)
    assert recovered["status"] == "completed"
    assert _q(f.m, "SELECT count(*) FROM tasks WHERE stima_id=%s", (f.sid,)) == [[1]]


def test_followup_recovery_rejects_changed_original_references(followup_case):
    from followup.exceptions import ConflictError
    f = followup_case
    action_id = f.pending("failed")
    with pytest.raises(ConflictError):
        f.service.run_followup(**{**f.kwargs, "contact_id": None}, recover=True)
    assert _q(f.m, "SELECT status, task_id FROM followup_actions WHERE id=%s", (action_id,)) == [["failed", None]]
    assert _q(f.m, "SELECT count(*) FROM tasks WHERE stima_id=%s", (f.sid,)) == [[0]]


def test_expired_receipt_proof_cannot_read_or_resume(submission):
    p = submission
    payload = p.identity()
    saved = _post(p, payload)
    _q(p.s.m, "UPDATE public_submission_receipts SET expires_at=NOW()-INTERVAL '1 minute' WHERE request_id=%s",
       (payload["request_id"],))
    before = _counts(p)
    notifications = {key: len(value) for key, value in p.deliveries.items()}
    assert _receipt(p, payload).status_code == 403
    assert _resume(p, payload).status_code == 403
    response = p.client.post("/api/salva_stima", json=payload)
    assert response.status_code == 403
    assert _counts(p) == before
    assert {key: len(value) for key, value in p.deliveries.items()} == notifications
    assert _links(p, saved["id"])[0][0] == saved["id"]


def test_business_insert_and_receipt_attachment_rollback_together(submission, monkeypatch):
    p = submission
    payload = p.identity()
    module = importlib.import_module("public_submissions")
    original = module.Receipt.attach_on_cursor
    calls = []

    def failed_attachment(self, cur, *a, **k):
        original(self, cur, *a, **k)
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("synthetic-after-attachment-before-commit")

    monkeypatch.setattr(module.Receipt, "attach_on_cursor", failed_attachment)
    before = _counts(p)
    failed = _post(p, payload)
    assert failed["receipt"]["status"] == "partial" and failed["receipt"]["stima_id"] is None
    assert _counts(p) == before
    assert _q(p.s.m, "SELECT stima_id, agency_id FROM public_submission_receipts WHERE request_id=%s",
              (payload["request_id"],)) == [[None, None]]
    resumed = _resume(p, payload)
    assert resumed.status_code == 200, resumed.text
    sid = resumed.json()["id"]
    assert _counts(p)["stime"] == before["stime"] + 1
    assert _q(p.s.m, "SELECT stima_id FROM public_submission_receipts WHERE request_id=%s",
              (payload["request_id"],)) == [[sid]]
    assert all(len(value) == 1 for value in p.deliveries.values())


def test_lost_insert_commit_acknowledgment_recovers_attached_stima(submission, monkeypatch):
    p = submission
    payload = p.identity()
    original_factory = p.s.main.get_connection
    failures = []

    class Cursor:
        def __init__(self, cur, connection):
            self.cur, self.connection = cur, connection

        def execute(self, sql, params=None):
            normalized = " ".join(str(sql).split()).lower()
            if normalized.startswith("insert into stime "):
                self.connection.has_stima_insert = True
            return self.cur.execute(sql, params)

        def __getattr__(self, name):
            return getattr(self.cur, name)

        def __enter__(self):
            self.cur.__enter__()
            return self

        def __exit__(self, *a):
            return self.cur.__exit__(*a)

    class Connection:
        def __init__(self, conn):
            self.conn, self.has_stima_insert = conn, False

        def cursor(self, *a, **k):
            return Cursor(self.conn.cursor(*a, **k), self)

        def commit(self):
            self.conn.commit()
            if self.has_stima_insert and not failures:
                failures.append(1)
                raise p.s.m["psycopg2"].OperationalError("synthetic-lost-commit-ack")

        def __getattr__(self, name):
            return getattr(self.conn, name)

    monkeypatch.setattr(p.s.main, "get_connection", lambda: Connection(original_factory()))
    before = _counts(p)
    failed = _post(p, payload)
    assert failed["receipt"]["status"] == "partial"
    sid = failed["receipt"]["stima_id"]
    assert isinstance(sid, int) and _counts(p)["stime"] == before["stime"] + 1
    assert not any(p.deliveries.values())
    resumed = _resume(p, payload)
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["id"] == sid
    assert _counts(p)["stime"] == before["stime"] + 1
    assert all(len(value) == 1 for value in p.deliveries.values())


def test_interrupted_delivery_checkpoint_never_repeats_the_attempt(submission, monkeypatch):
    p = submission
    payload = p.identity()
    module = importlib.import_module("public_submissions")
    original = module.Receipt.checkpoint
    failures = []

    def lose_result(self, name, state, *a, **k):
        if name == "admin_email" and state == "succeeded" and not failures:
            failures.append(1)
            raise RuntimeError("synthetic-lost-admin-acceptance-checkpoint")
        return original(self, name, state, *a, **k)

    monkeypatch.setattr(module.Receipt, "checkpoint", lose_result)
    first = _post(p, payload)
    assert len(p.deliveries["mail"]) == 1
    assert first["receipt"]["status"] == "partial"
    assert first["receipt"]["steps"]["admin_email"] == "sending"
    before = _counts(p)
    resumed = _resume(p, payload)
    assert resumed.status_code == 202, resumed.text
    assert resumed.json()["receipt"]["status"] == "attention"
    assert resumed.json()["receipt"]["steps"]["admin_email"] == "indeterminate"
    assert resumed.json()["id"] == first["id"]
    assert len(p.deliveries["mail"]) == 1
    assert _counts(p) == before
    assert _resume(p, payload).json()["id"] == first["id"]
    assert len(p.deliveries["mail"]) == 1


def test_lost_email_enqueue_checkpoint_reuses_the_existing_outbox_row(submission, monkeypatch):
    p = submission
    payload = p.identity()
    original = p.s.main.communication_service.enqueue
    calls = []

    def lost_result(*a, **k):
        result = original(*a, **k)
        calls.append(result)
        if len(calls) == 1:
            raise RuntimeError("synthetic-lost-email-queue-result")
        return result

    monkeypatch.setattr(p.s.main.communication_service, "enqueue", lost_result)
    first = _post(p, payload)
    assert first["receipt"]["status"] == "partial"
    assert len(p.deliveries["mail"]) == len(p.deliveries["whatsapp"]) == 0
    sid = first["id"]
    assert _q(p.s.m, "SELECT count(*) FROM communication_messages WHERE stima_id=%s", (sid,)) == [[1]]
    resumed = _resume(p, payload)
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["id"] == sid
    assert _q(p.s.m, "SELECT count(*) FROM communication_messages WHERE stima_id=%s", (sid,)) == [[1]]
    assert [result["created"] for result in calls] == [True, False]
    assert len(p.deliveries["mail"]) == len(p.deliveries["whatsapp"]) == 1


def test_receipt_migration_rollback_refuses_existing_recovery_state(submission):
    from pathlib import Path
    p = submission
    payload = p.identity()
    first = _post(p, payload)
    conn = p.s.m["psycopg2"].connect(p.s.m["dsn"])
    try:
        with conn.cursor() as cur:
            with pytest.raises(p.s.m["psycopg2"].errors.RaiseException, match="Cannot rollback populated"):
                cur.execute((Path(__file__).resolve().parents[1] / "migrations/094_public_submission_receipts_down.sql").read_text())
        conn.rollback()
    finally:
        conn.close()
    assert _receipt(p, payload).json()["id"] == first["id"]
    assert _q(p.s.m, "SELECT count(*) FROM public_submission_receipts WHERE request_id=%s", (payload["request_id"],)) == [[1]]


def _operator_override(p, monkeypatch):
    from operator_auth.dependencies import legacy_basic_agency_context, require_authenticated_operator, require_operator
    dependencies = (legacy_basic_agency_context, require_authenticated_operator, require_operator)
    overrides = dict(p.s.main.app.dependency_overrides)
    overrides.update({dependency: p.s.m["ctx"] for dependency in dependencies})
    monkeypatch.setattr(p.s.main.app, "dependency_overrides", overrides)


def test_operator_receipts_require_authentication_and_existing_agency_scope(submission, monkeypatch):
    p = submission
    payload = p.identity()
    saved = _post(p, payload)
    path = f"/api/admin/submissions/{payload['request_id']}"
    assert p.client.get(path).status_code == 401
    assert p.client.post(path + "/resume").status_code == 401
    _operator_override(p, monkeypatch)
    for who, expected in (("owner_a", 200), ("agent_a", 200), ("owner_b", 404)):
        p.s.m["stato"]["chi"] = who
        for method, url in (("get", path), ("post", path + "/resume")):
            result = getattr(p.client, method)(url)
            assert result.status_code == expected, (who, url, result.text)
            if expected == 200:
                assert result.json()["id"] == saved["id"]
                assert result.json()["receipt"]["stima_id"] == saved["id"]
            else:
                assert "token" not in result.json()
    assert all(len(value) == 1 for value in p.deliveries.values())


def test_operator_cannot_access_a_receipt_without_committed_parent(submission, monkeypatch):
    p = submission
    payload = p.identity()
    module = importlib.import_module("public_submissions")
    monkeypatch.setattr(module.Receipt, "attach_on_cursor", lambda *a, **k:
                        (_ for _ in ()).throw(RuntimeError("synthetic-unattached-request")))
    failed = _post(p, payload)
    assert failed["receipt"]["stima_id"] is None
    _operator_override(p, monkeypatch)
    path = f"/api/admin/submissions/{payload['request_id']}"
    assert p.client.get(path).status_code == 404
    assert p.client.post(path + "/resume").status_code == 404


def test_operator_recovers_expired_quick_pdf_without_extending_public_capabilities(submission, monkeypatch):
    p = submission
    payload = p.identity()
    original = p.s.main.genera_pdf_stima
    monkeypatch.setattr(p.s.main, "genera_pdf_stima", lambda *a, **k:
                        (_ for _ in ()).throw(RuntimeError("synthetic-pdf-generation-failure")))
    failed = _post(p, payload)
    sid, token = failed["id"], failed["token"]
    assert failed["receipt"]["status"] == "partial" and failed["pdf_status"] == "failed"
    _q(p.s.m, "UPDATE public_submission_receipts SET expires_at=NOW()-INTERVAL '1 minute' WHERE request_id=%s", (payload["request_id"],))
    _q(p.s.m, "UPDATE stime SET token_expires=NOW()-INTERVAL '1 minute' WHERE id=%s", (sid,))
    original_capability = _q(p.s.m, "SELECT token, token_expires FROM stime WHERE id=%s", (sid,))
    links, before = _links(p, sid), _counts(p)
    notifications = {key: len(value) for key, value in p.deliveries.items()}
    assert _receipt(p, payload).status_code == 403 and _resume(p, payload).status_code == 403
    monkeypatch.setattr(p.s.main, "genera_pdf_stima", original)
    monkeypatch.setattr(p.s.main, "compute_from_payload", lambda *a, **k:
                        pytest.fail("operator recovery recomputed the original valuation"))
    _operator_override(p, monkeypatch)
    path = f"/api/admin/submissions/{payload['request_id']}"
    recovered = p.client.post(path + "/resume")
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["id"] == sid and recovered.json()["pdf_status"] == "ready"
    assert _counts(p) == before and _links(p, sid) == links
    assert {key: len(value) for key, value in p.deliveries.items()} == notifications
    assert _q(p.s.m, "SELECT token, token_expires FROM stime WHERE id=%s", (sid,)) == original_capability
    assert p.client.get(f"/api/stime/{sid}/pdf", params={"t": token}).status_code == 404
    private = p.client.get(f"/api/admin/stime/{sid}/pdf")
    assert private.status_code == 200 and private.content.startswith(b"%PDF-")
    assert _receipt(p, payload).status_code == 403


def test_operator_recovers_expired_detail_receipt_using_its_original_parent(submission, monkeypatch):
    p = submission
    quick = _post(p, p.identity())
    identity = p.identity()
    payload = {"request_id": identity["request_id"], "receipt_key": identity["receipt_key"],
               "stima_id": quick["id"], "token": quick["token"], "classe": "D"}
    original = p.s.main.property_site_sync.sync_detail
    monkeypatch.setattr(p.s.main.property_site_sync, "sync_detail", lambda *a, **k: None)
    failed = p.client.post("/api/salva_stima_dettagliata", json=payload)
    assert failed.status_code == 202, failed.text
    receipt = failed.json()["receipt"]
    detail_id = receipt["detail_id"]
    assert detail_id is not None
    _q(p.s.m, "UPDATE public_submission_receipts SET expires_at=NOW()-INTERVAL '1 minute' WHERE request_id=%s", (payload["request_id"],))
    _q(p.s.m, "UPDATE stime SET token_expires=NOW()-INTERVAL '1 minute' WHERE id=%s", (quick["id"],))
    monkeypatch.setattr(p.s.main.property_site_sync, "sync_detail", original)
    _operator_override(p, monkeypatch)
    recovered = p.client.post(f"/api/admin/submissions/{payload['request_id']}/resume")
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["receipt"]["detail_id"] == detail_id
    assert recovered.json()["receipt"]["stima_id"] == quick["id"]
    assert _q(p.s.m, "SELECT count(*) FROM stime_dettagliate WHERE stima_id=%s", (quick["id"],)) == [[1]]
    pid = _links(p, quick["id"])[0][4]
    assert _q(p.s.m, "SELECT energy_class FROM properties WHERE id=%s", (pid,)) == [["D"]]
    assert _receipt(p, payload).status_code == 403


@pytest.mark.parametrize("channel", ["admin_email", "whatsapp"])
def test_definite_notification_configuration_failure_is_recoverable_without_repeating_other_channels(submission, monkeypatch, channel):
    p = submission
    payload = p.identity()
    if channel == "admin_email":
        monkeypatch.delenv("SMTP_PASS", raising=False)
        delivery_name = "mail"
    else:
        monkeypatch.setattr(p.s.main, "WHATSAPP_SERVICE_URL", "")
        delivery_name = "whatsapp"
    first = _post(p, payload)
    assert first["receipt"]["status"] == "partial"
    assert first["receipt"]["steps"][channel] == "failed"
    assert not p.deliveries[delivery_name]
    prior_other = {key: len(value) for key, value in p.deliveries.items() if key != delivery_name}
    before = _counts(p)
    if channel == "admin_email":
        monkeypatch.setenv("SMTP_PASS", "synthetic-fixture")
    else:
        monkeypatch.setattr(p.s.main, "WHATSAPP_SERVICE_URL", "http://127.0.0.1:55888/fake-whatsapp-provider")
    resumed = _resume(p, payload)
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["id"] == first["id"]
    assert len(p.deliveries[delivery_name]) == 1
    assert {key: len(value) for key, value in p.deliveries.items() if key != delivery_name} == prior_other
    assert _counts(p) == before


def test_pdf_checkpoint_lost_after_private_storage_recovers_cached_pdf_without_rerender(submission, monkeypatch):
    p = submission
    payload = p.identity()
    module = importlib.import_module("public_submissions")
    original_checkpoint = module.Receipt.checkpoint
    original_renderer = p.s.main.genera_pdf_stima
    rendered, failures = [], []

    def renderer(*a, **k):
        rendered.append(1)
        return original_renderer(*a, **k)

    def lose_result(self, name, state, *a, **k):
        if name == "pdf" and state == "succeeded" and not failures:
            failures.append(1)
            raise RuntimeError("synthetic-lost-private-pdf-checkpoint")
        return original_checkpoint(self, name, state, *a, **k)

    monkeypatch.setattr(p.s.main, "genera_pdf_stima", renderer)
    monkeypatch.setattr(module.Receipt, "checkpoint", lose_result)
    first = _post(p, payload)
    assert first["receipt"]["status"] == "partial"
    sid = first["id"]
    assert _q(p.s.m, "SELECT status FROM stima_pdf_artifacts WHERE stima_id=%s", (sid,)) == [["ready"]]
    before = _counts(p)
    notifications = {key: len(value) for key, value in p.deliveries.items()}
    recovered = _resume(p, payload)
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["id"] == sid and recovered.json()["pdf_status"] == "ready"
    assert len(rendered) == 1
    assert _counts(p) == before
    assert {key: len(value) for key, value in p.deliveries.items()} == notifications


def test_deleted_partial_stima_does_not_allow_the_same_request_to_create_another(submission, monkeypatch):
    p = submission
    payload = p.identity()
    original = p.s.main.core_service.bridge_public_stima
    monkeypatch.setattr(p.s.main.core_service, "bridge_public_stima", lambda *a, **k:
                        (_ for _ in ()).throw(RuntimeError("synthetic-before-core")))
    partial = _post(p, payload)
    sid = partial["id"]
    assert partial["receipt"]["status"] == "partial"
    _q(p.s.m, "DELETE FROM stime WHERE id=%s", (sid,))
    before = _counts(p)
    monkeypatch.setattr(p.s.main.core_service, "bridge_public_stima", original)
    replay = p.client.post("/api/salva_stima", json=payload)
    print({"scenario": "deleted_quick_identity", "deleted_stima": sid, "replay_status": replay.status_code,
           "replay_id": replay.json().get("id")})
    assert replay.status_code == 410, replay.text
    assert _counts(p) == before
    assert _receipt(p, payload).status_code == 410
    assert _resume(p, payload).status_code == 410
    assert not any(p.deliveries.values())
    tombstone = _q(p.s.m, "SELECT request_payload, frozen, checkpoints, response_payload, voided_at, proof_sha256, stima_id, agency_id "
                         "FROM public_submission_receipts WHERE request_id=%s", (payload["request_id"],))[0]
    assert tombstone[:4] == [{}, {}, {}, None]
    assert tombstone[4] is not None and tombstone[5] == hashlib.sha256(payload["receipt_key"].encode()).hexdigest()
    assert tombstone[6:] == [None, None]


def test_deleted_detail_does_not_allow_the_same_request_to_create_another(submission):
    p = submission
    quick = _post(p, p.identity())
    identity = p.identity()
    payload = {"request_id": identity["request_id"], "receipt_key": identity["receipt_key"],
               "stima_id": quick["id"], "token": quick["token"], "classe": "D"}
    accepted = p.client.post("/api/salva_stima_dettagliata", json=payload)
    assert accepted.status_code == 200, accepted.text
    detail_id = accepted.json()["receipt"]["detail_id"]
    _q(p.s.m, "DELETE FROM stime_dettagliate WHERE id=%s", (detail_id,))
    before = _counts(p)
    replay = p.client.post("/api/salva_stima_dettagliata", json=payload)
    print({"scenario": "deleted_detail_identity", "deleted_detail": detail_id, "replay_status": replay.status_code,
           "replay_detail": replay.json().get("receipt", {}).get("detail_id")})
    assert replay.status_code == 410, replay.text
    assert _counts(p) == before
    assert _receipt(p, payload).status_code == 410
    assert _resume(p, payload).status_code == 410
    tombstone = _q(p.s.m, "SELECT request_payload, frozen, checkpoints, response_payload, voided_at, proof_sha256, stima_id, detail_id "
                         "FROM public_submission_receipts WHERE request_id=%s", (payload["request_id"],))[0]
    assert tombstone[:4] == [{}, {}, {}, None]
    assert tombstone[4] is not None and tombstone[5] == hashlib.sha256(payload["receipt_key"].encode()).hexdigest()
    assert tombstone[6:] == [quick["id"], None]


@pytest.mark.parametrize("final_status", ["completed", "attention"])
def test_lost_final_receipt_commit_acknowledgment_does_not_regress_durable_status(submission, monkeypatch, final_status):
    p = submission
    payload = p.identity()
    if final_status == "attention":
        def uncertain(*a, **k):
            p.deliveries["mail"].append((a, k))
            return False
        monkeypatch.setattr(p.s.main, "invia_mail", uncertain)
    module = importlib.import_module("public_submissions")
    original = module.Receipt._save
    failures = []

    def applied_then_lost(self, column, value):
        result = original(self, column, value)
        if column == "status" and value == final_status and not failures:
            failures.append(1)
            raise RuntimeError("synthetic-lost-final-receipt-commit-ack")
        return result

    monkeypatch.setattr(module.Receipt, "_save", applied_then_lost)
    response = p.client.post("/api/salva_stima", json=payload)
    expected_http = 200 if final_status == "completed" else 202
    assert response.status_code == expected_http, response.text
    saved = response.json()
    assert saved["receipt"]["status"] == final_status
    assert saved["receipt"]["resumable"] is False
    before = _counts(p)
    notifications = {key: len(value) for key, value in p.deliveries.items()}
    assert _receipt(p, payload).json()["receipt"]["status"] == final_status
    assert _resume(p, payload).json()["receipt"]["status"] == final_status
    assert _post(p, payload)["receipt"]["status"] == final_status
    assert _counts(p) == before
    assert {key: len(value) for key, value in p.deliveries.items()} == notifications


def test_public_resume_rejects_a_changed_parent_agency_before_business_writes(submission, monkeypatch):
    p = submission
    payload = p.identity()
    original = p.s.main.core_service.bridge_public_stima
    monkeypatch.setattr(p.s.main.core_service, "bridge_public_stima", lambda *a, **k:
                        (_ for _ in ()).throw(RuntimeError("synthetic-before-any-core-links")))
    partial = _post(p, payload)
    sid = partial["id"]
    assert partial["receipt"]["status"] == "partial"
    assert _q(p.s.m, "SELECT count(*) FROM lead_stime WHERE stima_id=%s", (sid,)) == [[0]]
    # Synthetic administrative move, possible here because the stima has no
    # domain children. The receipt must remain bound to its original agency.
    _q(p.s.m, "UPDATE stime SET agency_id=2 WHERE id=%s", (sid,))
    before = _counts(p)
    monkeypatch.setattr(p.s.main.core_service, "bridge_public_stima", original)
    resumed = _resume(p, payload)
    assert resumed.status_code == 404, resumed.text
    assert _counts(p) == before
    assert _q(p.s.m, "SELECT count(*) FROM lead_stime WHERE stima_id=%s", (sid,)) == [[0]]
    assert not any(p.deliveries.values())
