from __future__ import annotations

import hashlib
import inspect
import io
import os
from contextlib import contextmanager
from pathlib import Path

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.exceptions import ConflictError
from owner import repository as repo
from owner.dependencies import current_owner
from owner.document_storage import (
    DEFAULT_MAX_BYTES,
    DocumentFileValidationError,
    InMemoryDocumentStorage,
    ObjectMetadata,
    OpenedObject,
    R2DocumentStorage,
    StorageAccessDenied,
    StorageMetadataMismatch,
    StorageNotConfigured,
    StorageObjectNotFound,
    StorageUnavailable,
    detect_mime,
    iter_stream,
    safe_content_disposition,
    sanitize_filename,
    stage_upload,
)
from owner.router_admin import router as admin_router
from owner.router_portal import router as portal_router
from property import repository as property_repo

ROOT = Path(__file__).resolve().parents[1]
PDF = b"%PDF-1.7\nP4 test document\n%%EOF\n"
JPEG = b"\xff\xd8\xff\xe0" + b"jpeg-data"
PNG = b"\x89PNG\r\n\x1a\n" + b"png-data"


def _staged(payload: bytes = PDF, filename: str = "documento.pdf", mime: str = "application/pdf"):
    return stage_upload(io.BytesIO(payload), filename=filename, declared_mime=mime)


def _portal_app(account_id: int = 7) -> FastAPI:
    app = FastAPI()
    app.include_router(portal_router)
    app.dependency_overrides[current_owner] = lambda: {
        "owner_account_id": account_id,
        "expires_at": "2099-01-01T00:00:00Z",
    }
    return app


@pytest.mark.parametrize(
    ("payload", "filename", "mime"),
    [
        (PDF, "documento.pdf", "application/pdf"),
        (JPEG, "foto.jpeg", "image/jpeg"),
        (PNG, "planimetria.png", "image/png"),
    ],
)
def test_stage_upload_accepts_allowlist_and_hashes(payload, filename, mime):
    staged = _staged(payload, filename, mime)
    try:
        assert staged.mime_detected == mime
        assert staged.size_bytes == len(payload)
        assert staged.sha256 == hashlib.sha256(payload).hexdigest()
        assert staged.fileobj.read() == payload
    finally:
        staged.close()


@pytest.mark.parametrize(
    ("payload", "filename", "mime", "code"),
    [
        (PDF, "documento.pdf", "image/png", "extension_mismatch"),
        (PNG, "documento.pdf", "application/pdf", "mime_mismatch"),
        (b"not-a-file", "documento.pdf", "application/pdf", "signature_invalid"),
        (b"", "documento.pdf", "application/pdf", "empty_file"),
        (PDF, "documento.exe", "application/pdf", "extension_mismatch"),
        (PDF, "documento.pdf", "text/plain", "mime_not_allowed"),
    ],
)
def test_stage_upload_rejects_mime_signature_and_extension_mismatch(payload, filename, mime, code):
    with pytest.raises(DocumentFileValidationError) as error:
        _staged(payload, filename, mime)
    assert error.value.code == code


def test_stage_upload_enforces_streaming_size_limit():
    with pytest.raises(DocumentFileValidationError) as error:
        stage_upload(
            io.BytesIO(PDF + b"x" * 64),
            filename="documento.pdf",
            declared_mime="application/pdf",
            max_bytes=len(PDF) + 8,
            chunk_size=7,
        )
    assert error.value.code == "file_too_large"
    assert DEFAULT_MAX_BYTES == 25 * 1024 * 1024


@pytest.mark.parametrize("filename", ["../segreto.pdf", "..\\segreto.pdf", "/tmp/segreto.pdf", "folder/file.pdf"])
def test_filename_path_traversal_is_rejected(filename):
    with pytest.raises(DocumentFileValidationError) as error:
        sanitize_filename(filename)
    assert error.value.code == "path_traversal"


def test_filename_is_sanitized_and_content_disposition_is_safe():
    assert sanitize_filename("  atto notarile (finale).PDF  ") == "atto_notarile_finale_.PDF"
    header = safe_content_disposition("atto notarile.pdf")
    assert header == 'attachment; filename="atto_notarile.pdf"'
    assert "\r" not in header and "\n" not in header


def test_signature_detection_is_explicit_allowlist():
    assert detect_mime(PDF) == "application/pdf"
    assert detect_mime(JPEG) == "image/jpeg"
    assert detect_mime(PNG) == "image/png"
    assert detect_mime(b"GIF89a") is None


def test_in_memory_storage_put_head_stream_delete():
    storage = InMemoryDocumentStorage()
    staged = _staged()
    try:
        key = storage.generate_key()
        metadata = storage.put_object(
            staged.fileobj,
            key=key,
            content_type=staged.mime_detected,
            size_bytes=staged.size_bytes,
            sha256=staged.sha256,
        )
        assert key.startswith("owner-documents/test/objects/")
        assert "documento" not in key
        assert storage.head_object(key) == metadata
        opened = storage.open_stream(key)
        assert opened.body.read() == PDF
        opened.close()
        storage.delete_object(key)
        with pytest.raises(StorageObjectNotFound):
            storage.head_object(key)
    finally:
        staged.close()


@pytest.mark.parametrize(
    ("operation", "error"),
    [
        ("put_object", StorageUnavailable("timeout")),
        ("head_object", StorageAccessDenied("denied")),
        ("open_stream", StorageObjectNotFound("missing")),
        ("delete_object", StorageUnavailable("timeout")),
    ],
)
def test_in_memory_storage_supports_normalized_provider_failures(operation, error):
    storage = InMemoryDocumentStorage()
    storage.inject_failure(operation, error)
    staged = _staged()
    key = storage.generate_key()
    try:
        if operation == "put_object":
            with pytest.raises(type(error)):
                storage.put_object(
                    staged.fileobj,
                    key=key,
                    content_type=staged.mime_detected,
                    size_bytes=staged.size_bytes,
                    sha256=staged.sha256,
                )
        else:
            storage.objects[key] = (
                PDF,
                ObjectMetadata(len(PDF), "application/pdf", hashlib.sha256(PDF).hexdigest()),
            )
            with pytest.raises(type(error)):
                getattr(storage, operation)(key)
    finally:
        staged.close()


def test_r2_configuration_is_fail_closed_and_requires_https(monkeypatch):
    for name in (
        "OWNER_DOCUMENT_STORAGE_ENABLED",
        "OWNER_DOCUMENT_STORAGE_ENDPOINT",
        "OWNER_DOCUMENT_STORAGE_BUCKET",
        "OWNER_DOCUMENT_STORAGE_ACCESS_KEY_ID",
        "OWNER_DOCUMENT_STORAGE_SECRET_ACCESS_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    disabled = R2DocumentStorage.from_env()
    assert disabled.is_configured() is False
    with pytest.raises(StorageNotConfigured):
        disabled.generate_key()

    invalid = R2DocumentStorage(
        enabled=True,
        endpoint="http://account.eu.r2.cloudflarestorage.com",
        bucket="stima360-test",
        access_key_id="key",
        secret_access_key="secret",
    )
    assert invalid.is_configured() is False
    with pytest.raises(StorageNotConfigured):
        invalid.healthcheck()

    wrong_provider = R2DocumentStorage(
        enabled=True,
        endpoint="https://s3.example.invalid",
        bucket="stima360-test",
        access_key_id="key",
        secret_access_key="secret",
    )
    assert wrong_provider.is_configured() is False

    wrong_jurisdiction = R2DocumentStorage(
        enabled=True,
        endpoint="https://account.r2.cloudflarestorage.com",
        bucket="stima360-test",
        access_key_id="key",
        secret_access_key="secret",
    )
    assert wrong_jurisdiction.is_configured() is False


def test_r2_configuration_does_not_leak_secrets_in_errors():
    secret = "super-secret-value"
    storage = R2DocumentStorage(
        enabled=True,
        endpoint="not-an-endpoint",
        bucket="stima360-test",
        access_key_id="key",
        secret_access_key=secret,
    )
    with pytest.raises(StorageNotConfigured) as error:
        storage.generate_key()
    assert secret not in str(error.value)
    assert "not-an-endpoint" not in str(error.value)


def test_r2_provider_errors_are_normalized():
    access_denied = ClientError(
        {"Error": {"Code": "AccessDenied"}, "ResponseMetadata": {"HTTPStatusCode": 403}},
        "GetObject",
    )
    missing = ClientError(
        {"Error": {"Code": "NoSuchKey"}, "ResponseMetadata": {"HTTPStatusCode": 404}},
        "GetObject",
    )
    timeout = EndpointConnectionError(endpoint_url="https://example.invalid")
    assert isinstance(R2DocumentStorage._translate_error(access_denied), StorageAccessDenied)
    assert isinstance(R2DocumentStorage._translate_error(missing), StorageObjectNotFound)
    assert isinstance(R2DocumentStorage._translate_error(timeout), StorageUnavailable)


class _FakeR2Client:
    def __init__(self):
        self.objects = {}

    def upload_fileobj(self, fileobj, bucket, key, ExtraArgs):
        self.objects[key] = (
            fileobj.read(),
            ExtraArgs["ContentType"],
            dict(ExtraArgs["Metadata"]),
        )

    def head_object(self, Bucket, Key):
        payload, mime, metadata = self.objects[Key]
        return {"ContentLength": len(payload), "ContentType": mime, "Metadata": metadata, "ETag": '"etag"'}

    def get_object(self, Bucket, Key):
        payload, mime, metadata = self.objects[Key]
        return {
            "Body": io.BytesIO(payload),
            "ContentLength": len(payload),
            "ContentType": mime,
            "Metadata": metadata,
            "ETag": '"etag"',
        }

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)

    def head_bucket(self, Bucket):
        return {}


def test_r2_adapter_contract_with_fake_s3_client():
    storage = R2DocumentStorage(
        enabled=True,
        endpoint="https://account.eu.r2.cloudflarestorage.com",
        bucket="stima360-test",
        access_key_id="key",
        secret_access_key="secret",
    )
    storage._client = _FakeR2Client()
    staged = _staged()
    try:
        key = storage.generate_key()
        metadata = storage.put_object(
            staged.fileobj,
            key=key,
            content_type=staged.mime_detected,
            size_bytes=staged.size_bytes,
            sha256=staged.sha256,
        )
        assert metadata.sha256 == staged.sha256
        assert storage.healthcheck() == {"configured": True, "available": True}
        opened = storage.open_stream(key)
        assert opened.body.read() == PDF
        opened.close()
        storage.delete_object(key)
        assert key not in storage._client.objects
    finally:
        staged.close()


def test_iter_stream_audits_completion_and_failure():
    calls = []
    opened = OpenedObject(io.BytesIO(b"abcdef"), ObjectMetadata(6, "application/pdf"))
    assert b"".join(iter_stream(opened, chunk_size=2, on_complete=lambda: calls.append("done"))) == b"abcdef"
    assert calls == ["done"]

    class Broken:
        def read(self, _size):
            raise OSError("broken")
        def close(self):
            calls.append("closed")

    errors = []
    with pytest.raises(OSError):
        list(iter_stream(OpenedObject(Broken(), ObjectMetadata(1, "application/pdf")), on_error=errors.append))
    assert isinstance(errors[0], OSError)
    assert "closed" in calls


def test_p4_routes_declared_without_method_path_collisions():
    app = FastAPI()
    app.include_router(admin_router)
    app.include_router(portal_router)
    expected = {
        ("POST", "/api/owner/admin/documents/upload"),
        ("GET", "/api/owner/admin/document-storage/health"),
        ("GET", "/api/owner/admin/documents/{i}/reads"),
        ("GET", "/api/owner/admin/documents/{i}/download"),
        ("GET", "/api/owner/portal/documents/{i}/download"),
        ("POST", "/api/owner/portal/documents/{i}/acknowledge"),
    }
    actual = set()
    for route in app.routes:
        for method in getattr(route, "methods", set()):
            if method not in {"HEAD", "OPTIONS"}:
                key = (method, route.path)
                assert key not in actual
                actual.add(key)
    assert expected <= actual


def test_admin_upload_route_stages_and_passes_safe_metadata(monkeypatch):
    captured = {}

    def fake_create(data, staged):
        captured["data"] = data
        captured["mime"] = staged.mime_detected
        captured["sha256"] = staged.sha256
        captured["payload"] = staged.fileobj.read()
        return {"id": 41, "status": "draft"}

    monkeypatch.setattr(repo, "create_uploaded_shared_document", fake_create)
    app = FastAPI()
    app.include_router(admin_router)
    client = TestClient(app)
    response = client.post(
        "/api/owner/admin/documents/upload",
        files={"file": ("atto.pdf", PDF, "application/pdf")},
        data={
            "property_id": "11",
            "document_type": "owner_ape",
            "source_title": "APE",
            "public_title": "Attestato energetico",
            "public_document_type": "ape",
        },
    )
    assert response.status_code == 201
    assert captured["payload"] == PDF
    assert captured["mime"] == "application/pdf"
    assert captured["sha256"] == hashlib.sha256(PDF).hexdigest()
    assert "storage_key" not in captured["data"]


def test_admin_upload_route_rejects_mime_mismatch_before_repository(monkeypatch):
    called = False

    def fake_create(*_args, **_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(repo, "create_uploaded_shared_document", fake_create)
    app = FastAPI()
    app.include_router(admin_router)
    response = TestClient(app).post(
        "/api/owner/admin/documents/upload",
        files={"file": ("atto.pdf", PNG, "application/pdf")},
        data={
            "property_id": "11",
            "document_type": "owner_ape",
            "source_title": "APE",
            "public_title": "Attestato energetico",
            "public_document_type": "ape",
        },
    )
    assert response.status_code == 422
    assert called is False


def test_upload_compensates_storage_when_database_fails(monkeypatch):
    storage = InMemoryDocumentStorage()
    staged = _staged()

    @contextmanager
    def failing_cursor(*, commit=False):
        assert commit is True
        raise RuntimeError("db failed")
        yield  # pragma: no cover

    monkeypatch.setattr(repo, "core_cursor", failing_cursor)
    monkeypatch.setattr(repo, "audit", lambda *args, **kwargs: None)
    try:
        with pytest.raises(RuntimeError, match="db failed"):
            repo.create_uploaded_shared_document(
                {
                    "property_id": 11,
                    "document_type": "owner_ape",
                    "source_title": "APE",
                    "public_title": "Attestato energetico",
                    "public_document_type": "ape",
                },
                staged,
                storage,
            )
        assert storage.objects == {}
    finally:
        staged.close()


def test_publish_is_fail_closed_before_write_when_storage_is_unavailable(monkeypatch):
    calls = []
    row = {
        "id": 1,
        "status": "draft",
        "source_status": "available",
        "source_expires_at": None,
        "storage_key": "opaque-key",
        "source_metadata": {
            "mime_detected": "application/pdf",
            "size_bytes": len(PDF),
            "sha256": hashlib.sha256(PDF).hexdigest(),
            "sanitized_filename": "documento.pdf",
            "storage_provider": "memory",
        },
    }

    @contextmanager
    def fake_cursor(*, commit=False):
        calls.append(commit)
        if commit:
            raise AssertionError("write transaction must not start")
        yield object(), object()

    storage = InMemoryDocumentStorage()
    storage.inject_failure("head_object", StorageUnavailable("timeout"))
    monkeypatch.setattr(repo, "core_cursor", fake_cursor)
    monkeypatch.setattr(repo, "_shared_document_with_source", lambda *_args, **_kwargs: row)
    with pytest.raises(StorageUnavailable):
        repo.publish_shared_document(1, storage)
    assert calls == [False]


def test_public_document_dto_is_explicit_whitelist_without_locators():
    row = {
        "id": 8,
        "public_title": "APE",
        "public_document_type": "ape",
        "version_number": 2,
        "published_at": "now",
        "expires_at": None,
        "acknowledgement_required": True,
        "first_viewed_at": None,
        "last_viewed_at": None,
        "view_count": 0,
        "acknowledged_at": None,
        "mime_type": "application/pdf",
        "size_bytes": 123,
        "download_filename": "ape.pdf",
        "source_status": "available",
        "storage_key": "secret/key",
        "url": "https://secret.invalid",
        "metadata": {"sha256": "x" * 64, "bucket": "secret"},
        "owner_account_id": 7,
        "property_document_id": 9,
    }
    dto = repo._public_shared_document(row)
    assert set(dto) == {
        "id",
        "public_title",
        "public_document_type",
        "public_document_type_label",
        "version_number",
        "published_at",
        "expires_at",
        "acknowledgement_required",
        "first_viewed_at",
        "last_viewed_at",
        "view_count",
        "acknowledged_at",
        "mime_type",
        "size_bytes",
        "download_filename",
        "download_available",
    }
    for forbidden in ("storage_key", "url", "bucket", "sha256", "owner_account_id", "property_document_id"):
        assert forbidden not in dto
        assert "secret/key" not in repr(dto)


def test_portal_document_access_is_uniform_404_and_audited(monkeypatch):
    app = _portal_app(7)
    audits = []
    monkeypatch.setattr(repo, "portal_shared_document", lambda *_args: (_ for _ in ()).throw(Exception("denied")))
    monkeypatch.setattr(repo, "audit_shared_document_access_denied", lambda *args, **kwargs: audits.append((args, kwargs)))
    response = TestClient(app).get("/api/owner/portal/documents/999")
    assert response.status_code == 404
    assert response.json() == {"detail": "Risorsa non trovata"}
    assert audits[-1][1]["document_id"] == 999
    assert "denied" not in response.text


def test_portal_download_streams_private_bytes_with_safe_headers(monkeypatch):
    app = _portal_app(7)
    audit_calls = []
    item = {
        "shared_document_id": 3,
        "property_id": 11,
        "owner_account_id": 7,
        "filename": "atto.pdf",
        "mime_type": "application/pdf",
        "size_bytes": len(PDF),
        "opened": OpenedObject(io.BytesIO(PDF), ObjectMetadata(len(PDF), "application/pdf", hashlib.sha256(PDF).hexdigest())),
    }
    monkeypatch.setattr(repo, "prepare_shared_document_download", lambda account, item_id: item)
    monkeypatch.setattr(repo, "audit_shared_document_download", lambda *args, **kwargs: audit_calls.append((args, kwargs)))
    response = TestClient(app).get("/api/owner/portal/documents/3/download")
    assert response.status_code == 200
    assert response.content == PDF
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "attachment" in response.headers["content-disposition"]
    assert "storage" not in response.text.lower()
    assert audit_calls and audit_calls[-1][1]["scope"] == "portal"


def test_detail_view_is_not_acknowledgement(monkeypatch):
    app = _portal_app(7)
    calls = []
    monkeypatch.setattr(repo, "portal_shared_document", lambda account, item_id: {"id": item_id})
    monkeypatch.setattr(
        repo,
        "read_shared_document",
        lambda account, item_id, ack=False: calls.append(ack) or {"acknowledged_at": "now" if ack else None},
    )
    client = TestClient(app)
    assert client.get("/api/owner/portal/documents/3").status_code == 200
    assert calls == [False]
    assert client.post("/api/owner/portal/documents/3/acknowledge").status_code == 200
    assert calls == [False, True]


def test_acknowledge_sql_is_idempotent():
    source = inspect.getsource(repo.read_shared_document)
    assert "ON CONFLICT(shared_document_id,owner_account_id) DO UPDATE" in source
    assert "COALESCE(owner_document_reads.acknowledged_at,NOW())" in source


def test_published_revoked_archived_documents_are_immutable(monkeypatch):
    for status in ("published", "revoked", "archived"):
        monkeypatch.setattr(repo, "get_shared_document", lambda _id, status=status: {"id": 1, "status": status})
        with pytest.raises(ConflictError):
            repo.update_shared_document(1, {"public_title": "Nuovo"})


def test_supersede_keeps_previous_current_until_successor_publish():
    supersede = inspect.getsource(repo.supersede_shared_document)
    upload = inspect.getsource(repo.create_uploaded_shared_document)
    publish = inspect.getsource(repo.publish_shared_document)
    assert "UPDATE owner_shared_documents" not in supersede
    assert "SET superseded_by_shared_document_id" not in upload
    assert "SET superseded_by_shared_document_id=%s" in publish
    assert "supersedes_shared_document_id" in supersede


def test_property_repository_blocks_binary_mutation_and_delete_after_owner_publish():
    update_source = inspect.getsource(property_repo.update_child)
    delete_source = inspect.getsource(property_repo.delete_child)
    assert "_document_has_published_owner_share" in update_source
    assert "_binary_document_change" in update_source
    assert "creare una nuova versione" in update_source
    assert "_document_has_published_owner_share" in delete_source


def test_download_checks_binary_hash_snapshot():
    portal_source = inspect.getsource(repo.prepare_shared_document_download)
    admin_source = inspect.getsource(repo.prepare_admin_shared_document_download)
    assert 'opened.metadata.sha256 != contract["sha256"]' in portal_source
    assert 'opened.metadata.sha256 != contract["sha256"]' in admin_source


def test_storage_locators_are_internal_only_in_portal_queries_and_dto():
    portal_list = inspect.getsource(repo.portal_shared_documents)
    portal_detail = inspect.getsource(repo.portal_shared_document)
    assert "owner_property_access" in portal_list
    assert "valid_until" in portal_list and "revoked_at" in portal_list
    public_dto = inspect.getsource(repo._public_shared_document)
    for source in (portal_list, portal_detail, public_dto):
        assert "storage_key" not in source
        assert "url" not in source
        assert "source_metadata" not in source
    internal = inspect.getsource(repo._authorized_shared_document_source)
    assert "storage_key" in internal
    assert "owner_property_access" in internal
    assert "valid_until" in internal and "revoked_at" in internal


def test_audit_actions_cover_sensitive_p4_operations_without_locator_values():
    source = (ROOT / "owner/repository.py").read_text(encoding="utf-8")
    for action in (
        "shared_document_uploaded",
        "shared_document_created",
        "shared_document_published",
        "shared_document_viewed",
        "shared_document_acknowledged",
        "shared_document_downloaded",
        "shared_document_download_failed",
        "shared_document_revoked",
        "shared_document_archived",
        "shared_document_access_denied",
        "shared_document_cleanup_failed",
    ):
        assert action in source
    for audit_call in (
        inspect.getsource(repo.create_uploaded_shared_document),
        inspect.getsource(repo.audit_shared_document_download),
        inspect.getsource(repo.audit_shared_document_access_denied),
    ):
        assert 'meta={"storage_key"' not in audit_call
        assert 'meta={"bucket"' not in audit_call


def test_no_p4_migration_and_p1_hashes_unchanged():
    migrations = sorted(path.name for path in (ROOT / "migrations").glob("*.sql"))
    assert not any("owner_04" in name or "p4" in name.lower() for name in migrations)
    assert hashlib.sha256((ROOT / "migrations/010_owner_02_p1.sql").read_bytes()).hexdigest() == "46f21b5f073607b178fe6d257e37d95cb04bdc6210da73539bc4c9a23e57e5a6"
    assert hashlib.sha256((ROOT / "migrations/010_owner_02_p1_down.sql").read_bytes()).hexdigest() == "66d8fdb3012d914a79e8763bcd29e44de4db4e3b3a050c0ae267b27afcb4327a"


def test_boto3_is_pinned_and_no_local_filesystem_storage_fallback():
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    assert "boto3==1.43.18" in requirements
    source = (ROOT / "owner/document_storage.py").read_text(encoding="utf-8")
    assert "LocalDocumentStorage" not in source
    assert "open(key" not in source
    assert "generate_presigned_url" not in source
