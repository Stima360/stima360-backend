"""F07 private reports on the disposable PostgreSQL, real public writer/CRM.

External delivery is captured by the established catalog fixture. Every PDF
read/retry uses the real main.app route and the database-backed capability.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import shutil
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from fastapi import HTTPException

from tests.test_catalogo_canonico_1_postgres import (
    COMPLETA, DSN, JsonRequest, _persona, completo, mondo, sito,  # noqa: F401
)
from tests.test_censimento_3_backend_postgres import _in_attesa_di_lock, _q

pytestmark = pytest.mark.skipif(not DSN, reason="isolated PostgreSQL required")
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module", autouse=True)
def _only_the_disposable_cluster():
    import os
    from psycopg2.extensions import parse_dsn
    params = parse_dsn(DSN)
    assert params.get("host", "").startswith("/private/tmp/stima360-isolated-pg.")
    assert params["host"].endswith("/socket") and params.get("port") == "55473"
    assert os.getenv("PYTHON_DOTENV_DISABLED") == "1"


@pytest.fixture
def private_pdf(sito, monkeypatch):
    from operator_auth.dependencies import (
        legacy_basic_agency_context, require_authenticated_operator, require_operator,
    )
    service = importlib.import_module("stima_pdf")
    renderer = importlib.import_module("pdf_report")
    s = sito
    monkeypatch.setattr(s.main, "genera_pdf_stima", renderer.genera_pdf_stima)
    deliveries = {"mail": [], "whatsapp": [], "outbox": []}
    def delivered(channel):
        def accepted(*a, **k):
            deliveries[channel].append((a, k))
            return True
        return accepted

    def enqueue(*a, **k):
        deliveries["outbox"].append((a, k))
        return {"message": {"id": 1}, "created": True}

    monkeypatch.setattr(s.main, "invia_mail", delivered("mail"))
    monkeypatch.setattr(s.main, "invia_whatsapp", delivered("whatsapp"))
    monkeypatch.setattr(s.main.communication_service, "enqueue", enqueue)
    monkeypatch.setattr(s.main, "PUBLIC_BASE_URL", "http://127.0.0.1:55887")
    monkeypatch.setattr(s.main, "PUBLIC_SITE_BASE_URL", "http://127.0.0.1:55887/preview")
    overrides = dict(s.main.app.dependency_overrides)
    for dependency in (legacy_basic_agency_context, require_authenticated_operator, require_operator):
        overrides[dependency] = s.m["ctx"]
    monkeypatch.setattr(s.main.app, "dependency_overrides", overrides)
    client = TestClient(s.main.app, raise_server_exceptions=False)

    def quick(agency=1, **extra):
        return s.stima({**COMPLETA, **_persona(), **extra}, agency=agency)

    def public(result, *, retry=False, token=None, sid=None, **kwargs):
        chosen = result["token"] if token is None else token
        path = f"/api/stime/{result['id'] if sid is None else sid}/pdf"
        return getattr(client, "post" if retry else "get")(
            path + ("/retry" if retry else ""), params={"t": chosen}, **kwargs)

    yield SimpleNamespace(s=s, module=service, renderer=renderer, client=client,
                          quick=quick, public=public, deliveries=deliveries)
    client.close()


def _counts(p):
    return {table: _q(p.s.m, f"SELECT count(*) FROM {table}")[0][0]
            for table in ("stime", "contacts", "leads", "lead_stime", "properties",
                          "property_site_sources", "site_submissions", "communication_messages",
                          "activities", "tasks", "seller_timeline_events")}


def _private_response(response):
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/pdf")
    assert "no-store" in response.headers["cache-control"]
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.content.startswith(b"%PDF-")
    assert response.content.rstrip().endswith(b"%%EOF")
    assert len(response.content) > 1000


def test_ready_pdf_is_private_and_uses_one_capability(private_pdf):
    p = private_pdf
    result = p.quick()
    assert result["success"] is True and result["pdf_status"] == "ready"
    assert parse_qs(urlparse(result["pdf_redirect_url"]).query) == {"token": [result["token"]]}
    assert urlparse(result["pdf_url"]).path == f"/api/stime/{result['id']}/pdf"
    assert parse_qs(urlparse(result["pdf_url"]).query) == {"t": [result["token"]]}
    before = _counts(p)
    first = p.public(result)
    _private_response(first)
    assert p.public(result).content == first.content
    assert _counts(p) == before
    artifact = _q(p.s.m, "SELECT status, pdf_bytes, sha256, attempts FROM stima_pdf_artifacts WHERE stima_id=%s",
                  (result["id"],))[0]
    assert artifact[0] == "ready" and bytes(artifact[1]) == first.content
    assert artifact[2] == hashlib.sha256(first.content).hexdigest() and artifact[3] == 1
    assert p.client.get(f"/reports/stima_{result['id']}.pdf").status_code == 404
    assert p.client.get(f"/static/reports/stima_{result['id']}.pdf").status_code == 404


@pytest.mark.parametrize("failure", ["missing", "empty", "malformed", "unknown", "expired", "null_expiry", "other_parent", "ambiguous"])
@pytest.mark.parametrize("retry", [False, True])
def test_invalid_capabilities_neither_read_nor_regenerate(private_pdf, monkeypatch, failure, retry):
    p = private_pdf
    result = p.quick()
    token, sid = result["token"], result["id"]
    if failure in {"missing", "empty"}:
        token = ""
    elif failure == "malformed":
        token = "not-a-uuid"
    elif failure == "unknown":
        token = "00000000-0000-4000-8000-000000000001"
    elif failure in {"expired", "null_expiry"}:
        value = "NULL" if failure == "null_expiry" else "NOW() - INTERVAL '1 minute'"
        _q(p.s.m, f"UPDATE stime SET token_expires={value} WHERE id=%s", (sid,))
    elif failure == "other_parent":
        sid = p.quick(agency=2)["id"]
    elif failure == "ambiguous":
        other = p.quick(agency=2)
        _q(p.s.m, "UPDATE stime SET token=%s, token_expires=NOW() - INTERVAL '1 minute' WHERE id=%s",
           (token, other["id"]))
    monkeypatch.setattr(p.s.main, "genera_pdf_stima", lambda *a, **k:
                        pytest.fail("an unauthorized request reached rendering"))
    before = _counts(p)
    if failure == "missing":
        path = f"/api/stime/{sid}/pdf" + ("/retry" if retry else "")
        response = getattr(p.client, "post" if retry else "get")(path)
    else:
        response = p.public(result, retry=retry, token=token, sid=sid)
    assert response.status_code == 404, response.text
    assert not response.content.startswith(b"%PDF-")
    assert _counts(p) == before


def test_head_and_range_do_not_bypass_capability_checks(private_pdf):
    p = private_pdf
    result = p.quick()
    path = f"/api/stime/{result['id']}/pdf"
    for method, headers in (("head", {}), ("get", {"Range": "bytes=0-31"})):
        response = getattr(p.client, method)(path, headers=headers)
        assert response.status_code in (404, 405), response.status_code
        assert not response.content.startswith(b"%PDF-")
    valid_range = p.public(result, headers={"Range": "bytes=0-31"})
    _private_response(valid_range)
    assert valid_range.status_code != 206


def test_failed_generation_recovers_same_rows_snapshot_and_notifications(private_pdf, monkeypatch):
    p = private_pdf
    original = p.s.main.genera_pdf_stima
    attempts = []

    def failing(payload, *a, **k):
        attempts.append(dict(payload))
        raise RuntimeError("synthetic-rendering-failure")

    monkeypatch.setattr(p.s.main, "genera_pdf_stima", failing)
    result = p.quick()
    assert result["success"] is False and result["pdf_status"] == "failed"
    assert result["receipt"]["status"] == "partial"
    assert p.public(result).status_code == 503
    before, delivered = _counts(p), {key: len(value) for key, value in p.deliveries.items()}
    snapshot = dict(attempts[0])
    # Recovery must use the committed snapshot, rather than fresh property
    # values or a changed valuation engine/catalog.
    _q(p.s.m, "UPDATE stime SET mq=999, prezzo_mq_base=1 WHERE id=%s", (result["id"],))
    monkeypatch.setattr(p.s.main, "compute_from_payload", lambda *a, **k:
                        pytest.fail("recovery recomputed the original valuation"))

    def repaired(payload, *a, **k):
        attempts.append(dict(payload))
        return original(payload, *a, **k)

    monkeypatch.setattr(p.s.main, "genera_pdf_stima", repaired)
    recovered = p.public(result, retry=True)
    _private_response(recovered)
    assert attempts == [snapshot, snapshot]
    assert p.public(result).content == recovered.content
    assert p.public(result, retry=True).content == recovered.content
    assert _counts(p) == before
    assert {key: len(value) for key, value in p.deliveries.items()} == delivered
    assert p.s.token(result["id"]) == result["token"]


def test_operator_pdf_uses_the_existing_stima_agency_scope(private_pdf):
    p = private_pdf
    one, two = p.quick(), p.quick(agency=2)
    for who, result, status in (("owner_a", one, 200), ("owner_a", two, 404),
                                ("owner_b", one, 404), ("owner_b", two, 200),
                                ("agent_a", one, 200), ("agent_a", two, 404)):
        p.s.m["stato"]["chi"] = who
        for retry in (False, True):
            path = f"/api/admin/stime/{result['id']}/pdf" + ("/retry" if retry else "")
            response = getattr(p.client, "post" if retry else "get")(path)
            assert response.status_code == status, (who, path, response.text)


def test_operator_pdf_requires_authentication_and_a_bound_agency(private_pdf, monkeypatch):
    from operator_auth.context import OperatorContext
    from operator_auth.dependencies import (
        legacy_basic_agency_context, require_authenticated_operator, require_operator,
    )
    p = private_pdf
    result = p.quick()
    dependencies = (legacy_basic_agency_context, require_authenticated_operator, require_operator)
    overrides = {dep: fn for dep, fn in p.s.main.app.dependency_overrides.items() if dep not in dependencies}
    monkeypatch.setattr(p.s.main.app, "dependency_overrides", overrides)
    for retry in (False, True):
        path = f"/api/admin/stime/{result['id']}/pdf" + ("/retry" if retry else "")
        assert getattr(p.client, "post" if retry else "get")(path).status_code == 401
    unbound = OperatorContext(user_id=p.s.m["ids"]["owner_a"], agency_id=None, role=None,
                              is_platform_admin=True, session_id=None, auth_channel="operator_session")
    monkeypatch.setattr(p.s.main.app, "dependency_overrides", {**overrides, **{dep: lambda: unbound for dep in dependencies}})
    assert p.client.get(f"/api/admin/stime/{result['id']}/pdf").status_code == 403


def test_concurrent_recovery_renders_once_and_creates_no_business_rows(private_pdf, monkeypatch):
    p = private_pdf
    original = p.s.main.genera_pdf_stima
    monkeypatch.setattr(p.s.main, "genera_pdf_stima", lambda *a, **k:
                        (_ for _ in ()).throw(RuntimeError("synthetic-first-attempt")))
    result = p.quick()
    before = _counts(p)
    delivered = {key: len(value) for key, value in p.deliveries.items()}
    entered, release = threading.Event(), threading.Event()
    calls = []

    def blocked(payload, *a, **k):
        calls.append(dict(payload))
        entered.set()
        assert release.wait(20), "test did not release the rendering boundary"
        return original(payload, *a, **k)

    def recover():
        return p.module.generate(result["id"], token=result["token"], renderer=blocked,
                                 connection_factory=lambda: p.s.m["psycopg2"].connect(p.s.m["dsn"]))

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(recover)
        assert entered.wait(10)
        second = pool.submit(recover)
        try:
            assert _in_attesa_di_lock(p.s.m, "%stima_pdf_artifacts%"), "second recovery did not wait on the same report"
        finally:
            release.set()
        first_pdf, second_pdf = first.result(timeout=20), second.result(timeout=20)
    assert first_pdf == second_pdf and first_pdf.startswith(b"%PDF-")
    assert len(calls) == 1
    assert _counts(p) == before
    assert {key: len(value) for key, value in p.deliveries.items()} == delivered
    assert _q(p.s.m, "SELECT status, attempts FROM stima_pdf_artifacts WHERE stima_id=%s", (result["id"],)) == [["ready", 2]]


def test_token_expiring_during_render_and_lock_wait_delivers_no_public_bytes(private_pdf, monkeypatch):
    p = private_pdf
    original = p.s.main.genera_pdf_stima
    monkeypatch.setattr(p.s.main, "genera_pdf_stima", lambda *a, **k:
                        (_ for _ in ()).throw(RuntimeError("synthetic-first-attempt")))
    result = p.quick()
    before = _counts(p)
    _q(p.s.m, "UPDATE stime SET token_expires=clock_timestamp()+INTERVAL '4 seconds' WHERE id=%s", (result["id"],))
    entered, release = threading.Event(), threading.Event()

    def blocked(payload, *a, **k):
        entered.set()
        assert release.wait(20)
        return original(payload, *a, **k)

    def recover():
        return p.module.generate(result["id"], token=result["token"], renderer=blocked,
                                 connection_factory=lambda: p.s.m["psycopg2"].connect(p.s.m["dsn"]))

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(recover)
        assert entered.wait(10)
        second = pool.submit(recover)
        try:
            assert _in_attesa_di_lock(p.s.m, "%stima_pdf_artifacts%")
            assert _q(p.s.m, "SELECT token_expires > clock_timestamp() FROM stime WHERE id=%s", (result["id"],)) == [[True]]
            deadline = time.monotonic() + 10
            while _q(p.s.m, "SELECT token_expires > clock_timestamp() FROM stime WHERE id=%s", (result["id"],))[0][0]:
                assert time.monotonic() < deadline, "the server clock did not reach the committed expiry"
                threading.Event().wait(0.02)
        finally:
            release.set()
        for request in (first, second):
            with pytest.raises(HTTPException) as denied:
                request.result(timeout=20)
            assert denied.value.status_code == 404
    assert _counts(p) == before


@pytest.mark.parametrize("phase", ["update", "commit"])
def test_binary_persistence_failure_is_recoverable_and_preserves_ready_reports(private_pdf, phase):
    p = private_pdf
    ready = p.quick()
    saved = _q(p.s.m, "SELECT status, sha256, pdf_bytes, attempts FROM stima_pdf_artifacts WHERE stima_id=%s", (ready["id"],))
    _q(p.s.m, "CREATE FUNCTION pg_temp.reject_stima_pdf_bytes() RETURNS trigger LANGUAGE plpgsql AS $$ "
              "BEGIN IF NEW.pdf_bytes IS NOT NULL THEN RAISE EXCEPTION 'synthetic-binary-save-failure'; END IF; RETURN NEW; END $$")
    trigger = ("CREATE TRIGGER synthetic_reject_stima_pdf_bytes BEFORE UPDATE ON stima_pdf_artifacts "
               if phase == "update" else
               "CREATE CONSTRAINT TRIGGER synthetic_reject_stima_pdf_bytes AFTER UPDATE ON stima_pdf_artifacts "
               "DEFERRABLE INITIALLY DEFERRED ")
    _q(p.s.m, trigger + "FOR EACH ROW EXECUTE FUNCTION pg_temp.reject_stima_pdf_bytes()")
    try:
        failed = p.quick()
        assert failed["success"] is False and failed["pdf_status"] == "failed"
        assert failed["receipt"]["status"] == "partial"
        response = p.public(failed)
        assert response.status_code == 503 and "synthetic-binary-save-failure" not in response.text
        snapshot = _q(p.s.m, "SELECT status, pdf_bytes, sha256, render_payload FROM stima_pdf_artifacts WHERE stima_id=%s", (failed["id"],))[0]
        assert snapshot[0] == "pending" and snapshot[1] is None and snapshot[2] is None
        assert snapshot[3]["id_stima"] == failed["id"] and snapshot[3]["price_exact"] > 0
        assert _q(p.s.m, "SELECT status, sha256, pdf_bytes, attempts FROM stima_pdf_artifacts WHERE stima_id=%s", (ready["id"],)) == saved
    finally:
        _q(p.s.m, "DROP TRIGGER synthetic_reject_stima_pdf_bytes ON stima_pdf_artifacts")
        _q(p.s.m, "DROP FUNCTION pg_temp.reject_stima_pdf_bytes()")
    before = _counts(p)
    delivered = {key: len(value) for key, value in p.deliveries.items()}
    recovered = p.public(failed, retry=True)
    _private_response(recovered)
    assert _q(p.s.m, "SELECT render_payload FROM stima_pdf_artifacts WHERE stima_id=%s", (failed["id"],)) == [[snapshot[3]]]
    assert _counts(p) == before
    assert {key: len(value) for key, value in p.deliveries.items()} == delivered
    assert _q(p.s.m, "SELECT status, sha256, pdf_bytes, attempts FROM stima_pdf_artifacts WHERE stima_id=%s", (ready["id"],)) == saved


def test_snapshot_write_failure_returns_generic_error_before_delivery(private_pdf):
    p = private_pdf
    _q(p.s.m, "CREATE FUNCTION pg_temp.reject_stima_pdf_snapshot() RETURNS trigger LANGUAGE plpgsql AS $$ "
              "BEGIN RAISE EXCEPTION 'synthetic-snapshot-save-failure'; END $$")
    _q(p.s.m, "CREATE TRIGGER synthetic_reject_stima_pdf_snapshot BEFORE INSERT ON stima_pdf_artifacts "
              "FOR EACH ROW EXECUTE FUNCTION pg_temp.reject_stima_pdf_snapshot()")
    before = {key: len(value) for key, value in p.deliveries.items()}
    try:
        response = p.client.post("/api/salva_stima", json=JsonRequest({**COMPLETA, **_persona()}).payload)
        assert response.status_code == 202
        assert response.json()["receipt"]["status"] == "partial"
        assert response.json()["receipt"]["steps"]["pdf_snapshot"] == "failed"
        assert "synthetic-snapshot-save-failure" not in response.text
        assert "token" not in response.json() and "pdf_url" not in response.json()
        assert {key: len(value) for key, value in p.deliveries.items()} == before
    finally:
        _q(p.s.m, "DROP TRIGGER synthetic_reject_stima_pdf_snapshot ON stima_pdf_artifacts")
        _q(p.s.m, "DROP FUNCTION pg_temp.reject_stima_pdf_snapshot()")


def test_corrupt_binary_and_missing_schema_fail_closed(private_pdf):
    p = private_pdf
    result = p.quick()
    _q(p.s.m, "UPDATE stima_pdf_artifacts SET pdf_bytes=%s WHERE stima_id=%s", (b"synthetic-corrupt-binary", result["id"]))
    corrupted = p.public(result)
    assert corrupted.status_code == 503 and b"synthetic-corrupt-binary" not in corrupted.content
    _q(p.s.m, "ALTER TABLE stima_pdf_artifacts RENAME TO synthetic_hidden_stima_pdf_artifacts")
    try:
        for retry in (False, True):
            response = p.public(result, retry=retry)
            assert response.status_code == 503
            assert "stima_pdf_artifacts" not in response.text
            assert not response.content.startswith(b"%PDF-")
    finally:
        _q(p.s.m, "ALTER TABLE synthetic_hidden_stima_pdf_artifacts RENAME TO stima_pdf_artifacts")


def test_real_pdf_foreign_key_is_owned_content_for_stima_purge(private_pdf):
    import stime_purge
    p = private_pdf
    with p.s.m["conn"].cursor() as cur:
        catalog = stime_purge._catalog_references(cur)
        assert ("stima_pdf_artifacts", "stima_id") in catalog
        references = stime_purge.references(cur)
    assert not any(table == "stima_pdf_artifacts" for table, column, label in references)
    assert any(table == "lead_stime" for table, column, label in references)


def test_down_migration_refuses_to_remove_saved_pdf_bytes(private_pdf):
    p = private_pdf
    result = p.quick()
    original = p.public(result).content
    conn = p.s.m["psycopg2"].connect(p.s.m["dsn"])
    try:
        with conn.cursor() as cur:
            with pytest.raises(p.s.m["psycopg2"].Error, match="Private PDFs exist"):
                cur.execute((ROOT / "migrations/093_stima_private_pdf_down.sql").read_text(encoding="utf-8"))
        conn.rollback()
    finally:
        conn.close()
    assert p.public(result).content == original
    assert _q(p.s.m, "SELECT sha256 FROM stima_pdf_artifacts WHERE stima_id=%s", (result["id"],)) == [[hashlib.sha256(original).hexdigest()]]


def test_up_down_migrations_on_an_empty_isolated_schema(private_pdf):
    from psycopg2 import sql
    p = private_pdf
    schema = "f07_migration_test_" + uuid.uuid4().hex
    conn = p.s.m["psycopg2"].connect(p.s.m["dsn"])
    try:
        with conn.cursor() as cur:
            cur.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            cur.execute(sql.SQL("SET LOCAL search_path TO {}, public").format(sql.Identifier(schema)))
            for repeat in range(2):
                cur.execute((ROOT / "migrations/093_stima_private_pdf.sql").read_text(encoding="utf-8"))
                cur.execute("SELECT to_regclass(%s)", (schema + ".stima_pdf_artifacts",))
                assert cur.fetchone()[0] is not None
                cur.execute((ROOT / "migrations/093_stima_private_pdf_down.sql").read_text(encoding="utf-8"))
                cur.execute("SELECT to_regclass(%s)", (schema + ".stima_pdf_artifacts",))
                assert cur.fetchone()[0] is None
    finally:
        conn.rollback()  # the scratch schema never becomes durable
        conn.close()


def test_down_exclusive_lock_prevents_a_concurrent_snapshot_from_being_lost(private_pdf):
    from psycopg2 import sql
    p = private_pdf
    result = p.quick()
    payload = _q(p.s.m, "SELECT render_payload FROM stima_pdf_artifacts WHERE stima_id=%s", (result["id"],))[0][0]
    saved = p.public(result).content
    schema = "f07_downrace_" + uuid.uuid4().hex
    _q(p.s.m, sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))

    def scratch_connection():
        conn = p.s.m["psycopg2"].connect(p.s.m["dsn"])
        with conn.cursor() as cur:
            cur.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
        return conn

    setup = scratch_connection()
    try:
        with setup.cursor() as cur:
            cur.execute((ROOT / "migrations/093_stima_private_pdf.sql").read_text(encoding="utf-8"))
        setup.commit()
    finally:
        setup.close()
    locked, release = threading.Event(), threading.Event()
    migration = (ROOT / "migrations/093_stima_private_pdf_down.sql").read_text(encoding="utf-8")
    lock_statement = "LOCK TABLE stima_pdf_artifacts IN ACCESS EXCLUSIVE MODE;"
    assert migration.count(lock_statement) == 1
    before, after = migration.split(lock_statement, 1)

    def down():
        conn = scratch_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(before + lock_statement)
                locked.set()
                assert release.wait(20)
                cur.execute(after)
            conn.commit()
        finally:
            conn.close()

    def insert():
        return p.module.prepare(result["id"], 1, payload, connection_factory=scratch_connection)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            dropping = pool.submit(down)
            assert locked.wait(10)
            preparing = pool.submit(insert)
            try:
                assert _in_attesa_di_lock(p.s.m, "%stima_pdf_artifacts%")
            finally:
                release.set()
            dropping.result(timeout=20)
            with pytest.raises(p.s.m["psycopg2"].Error):
                preparing.result(timeout=20)
        assert _q(p.s.m, "SELECT to_regclass(%s)", (schema + ".stima_pdf_artifacts",)) == [[None]]
        assert p.public(result).content == saved
    finally:
        release.set()
        _q(p.s.m, sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_private_pdf_backup_restore_preserves_bytes_snapshot_and_capability(private_pdf, tmp_path):
    """Real pg_dump/pg_restore, confined to a second disposable local DB."""
    from psycopg2 import sql
    from psycopg2.extensions import parse_dsn
    p = private_pdf
    result = p.quick()
    row = _q(p.s.m, "SELECT a.agency_id, a.render_payload, a.pdf_bytes, a.sha256, s.token "
                   "FROM stima_pdf_artifacts a JOIN stime s ON s.id=a.stima_id WHERE a.stima_id=%s", (result["id"],))[0]
    receipt_columns = "request_id, proof_sha256, payload_sha256, request_payload, checkpoints, frozen, response_payload, stima_id, detail_id, agency_id, status, expires_at"
    receipt_row = _q(p.s.m, "SELECT " + receipt_columns + " FROM public_submission_receipts WHERE stima_id=%s AND kind='quick'", (result["id"],))[0]
    params = parse_dsn(DSN)
    assert params["host"].startswith("/private/tmp/stima360-isolated-pg.")
    pg_dump = shutil.which("pg_dump")
    pg_restore = shutil.which("pg_restore")
    assert pg_dump and pg_restore, "PostgreSQL client tools unavailable"
    artifacts = Path(os.getenv("STIMA360_JOURNEY_ARTIFACTS", str(tmp_path))) / "backup"
    artifacts.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup = artifacts / ("private-pdf-" + uuid.uuid4().hex + ".dump")
    fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    connection_args = ["--host", params["host"], "--port", params["port"],
                       "--username", params["user"], "--no-password"]
    env = {"PATH": os.path.dirname(pg_dump), "LC_ALL": "C", "PGCONNECT_TIMEOUT": "5",
           "PGPASSFILE": str(artifacts / "no-password-file"),
           "PGSERVICEFILE": str(artifacts / "no-service-file"), "PGAPPNAME": "stima360-f07-private-backup"}
    dumped = subprocess.run([pg_dump, *connection_args, "--format=custom", "--file", str(backup),
                             "--dbname", "stima360_db_test"], env=env, capture_output=True, text=True, timeout=60)
    assert dumped.returncode == 0, dumped.stderr
    assert backup.stat().st_mode & 0o777 == 0o600
    restore_name = "stima360_f07_restore_test_" + uuid.uuid4().hex
    service = p.s.m["psycopg2"].connect(DSN)
    service.autocommit = True
    try:
        with service.cursor() as cur:
            cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(restore_name)))
        restored = subprocess.run([pg_restore, *connection_args, "--no-owner", "--no-acl", "--exit-on-error",
                                   "--dbname", restore_name, str(backup)], env=env, capture_output=True, text=True, timeout=60)
        assert restored.returncode == 0, restored.stderr
        restored_dsn = p.s.m["dsn"].replace("/stima360_db_test?", f"/{restore_name}?")
        assert restored_dsn != p.s.m["dsn"]
        conn = p.s.m["psycopg2"].connect(restored_dsn)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT a.agency_id, a.render_payload, a.pdf_bytes, a.sha256, s.token "
                            "FROM stima_pdf_artifacts a JOIN stime s ON s.id=a.stima_id WHERE a.stima_id=%s", (result["id"],))
                restored_row = cur.fetchone()
            assert restored_row[0] == row[0] == 1
            assert restored_row[1] == row[1]
            assert bytes(restored_row[2]) == bytes(row[2])
            assert restored_row[3] == row[3] == hashlib.sha256(bytes(row[2])).hexdigest()
            assert restored_row[4] == row[4]
            with conn.cursor() as cur:
                cur.execute("SELECT " + receipt_columns + " FROM public_submission_receipts WHERE request_id=%s", (receipt_row[0],))
                restored_receipt = cur.fetchone()
            assert list(restored_receipt) == list(receipt_row)
            receipts = importlib.import_module("public_submissions")
            with receipts.open_receipt(lambda: p.s.m["psycopg2"].connect(restored_dsn), str(receipt_row[0]), receipt_row[1]) as restored_state:
                envelope = restored_state.envelope()
                assert envelope["receipt"]["status"] == "completed"
                assert envelope["id"] == result["id"] and envelope["token"] == result["token"]
                assert envelope["receipt"]["stima_id"] == result["id"]
        finally:
            conn.close()
        proof = {"stima_id": result["id"], "agency_id": row[0], "pdf_bytes": len(row[2]),
                 "pdf_sha256": row[3], "snapshot_sha256": hashlib.sha256(json.dumps(row[1], sort_keys=True).encode()).hexdigest(),
                 "capability_preserved": True, "restore_database_dropped": True,
                 "receipt_uuid_proof_hash_payload_checkpoints_frozen_response_ids_and_expiry_preserved": True,
                 "restored_receipt_read_authorized": True,
                 "backup_file_mode": "0600", "source_database": "stima360_db_test"}
    finally:
        with service.cursor() as cur:
            cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()", (restore_name,))
            cur.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(restore_name)))
            cur.execute("SELECT 1 FROM pg_database WHERE datname=%s", (restore_name,))
            assert cur.fetchone() is None
        service.close()
    (artifacts / "backup-restore-proof.json").write_text(json.dumps(proof, indent=2), encoding="utf-8")
