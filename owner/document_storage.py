"""Private document storage adapter for OWNER 0.2 P4.

The production/test backend uses Cloudflare R2 through its S3-compatible API.
Unit, integration and isolated HTTP E2E tests inject ``InMemoryDocumentStorage``.
No local-filesystem persistence or public/presigned URL is implemented here.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import tempfile
import unicodedata
import uuid
from urllib.parse import urlparse
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import BinaryIO, Iterator, Mapping, Protocol

ALLOWED_MIME_TYPES: frozenset[str] = frozenset(
    {"application/pdf", "image/jpeg", "image/png"}
)
MIME_EXTENSIONS: Mapping[str, tuple[str, ...]] = {
    "application/pdf": (".pdf",),
    "image/jpeg": (".jpg", ".jpeg"),
    "image/png": (".png",),
}
DEFAULT_MAX_BYTES = 25 * 1024 * 1024
DEFAULT_CHUNK_SIZE = 1024 * 1024
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


class DocumentStorageError(RuntimeError):
    """Base class for normalized provider/storage errors."""

    error_code = "storage_error"


class StorageNotConfigured(DocumentStorageError):
    error_code = "storage_not_configured"


class StorageUnavailable(DocumentStorageError):
    error_code = "storage_unavailable"


class StorageAccessDenied(DocumentStorageError):
    error_code = "storage_access_denied"


class StorageObjectNotFound(DocumentStorageError):
    error_code = "storage_object_not_found"


class StorageMetadataMismatch(DocumentStorageError):
    error_code = "storage_metadata_mismatch"


class DocumentFileValidationError(ValueError):
    """Raised before any provider write when an uploaded file is unsafe."""

    def __init__(self, message: str, *, code: str = "invalid_file") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ObjectMetadata:
    size_bytes: int
    content_type: str
    sha256: str | None = None
    etag: str | None = None


@dataclass
class OpenedObject:
    body: BinaryIO
    metadata: ObjectMetadata

    def close(self) -> None:
        close = getattr(self.body, "close", None)
        if close:
            close()


@dataclass
class StagedUpload:
    fileobj: BinaryIO
    original_filename: str
    sanitized_filename: str
    mime_declared: str
    mime_detected: str
    size_bytes: int
    sha256: str

    def close(self) -> None:
        self.fileobj.close()


class DocumentStorage(Protocol):
    provider_name: str

    def is_configured(self) -> bool: ...

    def healthcheck(self) -> dict[str, object]: ...

    def generate_key(self) -> str: ...

    def put_object(
        self,
        fileobj: BinaryIO,
        *,
        key: str,
        content_type: str,
        size_bytes: int,
        sha256: str,
    ) -> ObjectMetadata: ...

    def head_object(self, key: str) -> ObjectMetadata: ...

    def open_stream(self, key: str) -> OpenedObject: ...

    def delete_object(self, key: str) -> None: ...



def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise StorageNotConfigured(f"Configurazione storage non valida: {name}") from exc
    if value < minimum:
        raise StorageNotConfigured(f"Configurazione storage non valida: {name}")
    return value


def upload_limits_from_env() -> tuple[int, int]:
    max_bytes = _env_int("OWNER_DOCUMENT_STORAGE_MAX_BYTES", DEFAULT_MAX_BYTES)
    chunk_size = _env_int("OWNER_DOCUMENT_STORAGE_CHUNK_SIZE_BYTES", DEFAULT_CHUNK_SIZE)
    if max_bytes > DEFAULT_MAX_BYTES:
        # P4 has an architectural hard ceiling of 25 MiB.
        max_bytes = DEFAULT_MAX_BYTES
    if chunk_size > max_bytes:
        chunk_size = max_bytes
    return max_bytes, chunk_size


def normalize_mime(value: str | None) -> str:
    return (value or "").split(";", 1)[0].strip().lower()


def sanitize_filename(filename: str | None) -> str:
    raw = unicodedata.normalize("NFKC", (filename or "").strip())
    if not raw:
        raise DocumentFileValidationError("Nome file obbligatorio", code="filename_missing")
    if "/" in raw or "\\" in raw or raw in {".", ".."} or ".." in raw.split("/"):
        raise DocumentFileValidationError("Percorso file non ammesso", code="path_traversal")
    if any(ord(char) < 32 for char in raw):
        raise DocumentFileValidationError("Nome file non valido", code="filename_control_char")
    safe = _SAFE_FILENAME_RE.sub("_", raw).strip("._-")
    if not safe:
        raise DocumentFileValidationError("Nome file non valido", code="filename_invalid")
    if len(safe) > 180:
        stem, dot, ext = safe.rpartition(".")
        if dot:
            safe = stem[: max(1, 179 - len(ext))] + "." + ext
        else:
            safe = safe[:180]
    return safe


def detect_mime(signature: bytes) -> str | None:
    if signature.startswith(b"%PDF-"):
        return "application/pdf"
    if signature.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if signature.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    return None


def stage_upload(
    fileobj: BinaryIO,
    *,
    filename: str | None,
    declared_mime: str | None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> StagedUpload:
    """Read an upload once, enforce limits/signature and create a request-scoped spool."""

    sanitized = sanitize_filename(filename)
    declared = normalize_mime(declared_mime)
    if declared not in ALLOWED_MIME_TYPES:
        raise DocumentFileValidationError("MIME type non ammesso", code="mime_not_allowed")

    suffix = "." + sanitized.rsplit(".", 1)[-1].lower() if "." in sanitized else ""
    if suffix not in MIME_EXTENSIONS[declared]:
        raise DocumentFileValidationError(
            "Estensione e MIME dichiarato non coerenti", code="extension_mismatch"
        )

    spool = tempfile.SpooledTemporaryFile(max_size=min(2 * 1024 * 1024, max_bytes), mode="w+b")
    digest = hashlib.sha256()
    total = 0
    signature = b""
    try:
        while True:
            chunk = fileobj.read(chunk_size)
            if not chunk:
                break
            if not isinstance(chunk, (bytes, bytearray)):
                raise DocumentFileValidationError("Stream file non valido", code="invalid_stream")
            total += len(chunk)
            if total > max_bytes:
                raise DocumentFileValidationError(
                    "Dimensione massima 25 MiB superata", code="file_too_large"
                )
            if len(signature) < 16:
                signature += bytes(chunk[: 16 - len(signature)])
            digest.update(chunk)
            spool.write(chunk)

        if total == 0:
            raise DocumentFileValidationError("File vuoto non ammesso", code="empty_file")
        detected = detect_mime(signature)
        if detected is None or detected not in ALLOWED_MIME_TYPES:
            raise DocumentFileValidationError("Formato file non riconosciuto", code="signature_invalid")
        if detected != declared:
            raise DocumentFileValidationError(
                "MIME dichiarato e contenuto reale non coerenti", code="mime_mismatch"
            )
        if suffix not in MIME_EXTENSIONS[detected]:
            raise DocumentFileValidationError(
                "Estensione e contenuto reale non coerenti", code="extension_mismatch"
            )
        spool.seek(0)
        return StagedUpload(
            fileobj=spool,
            original_filename=sanitized,
            sanitized_filename=sanitized,
            mime_declared=declared,
            mime_detected=detected,
            size_bytes=total,
            sha256=digest.hexdigest(),
        )
    except Exception:
        spool.close()
        raise


def safe_content_disposition(filename: str) -> str:
    safe = sanitize_filename(filename).replace('"', "")
    return f'attachment; filename="{safe}"'


class R2DocumentStorage:
    provider_name = "r2"

    def __init__(
        self,
        *,
        enabled: bool,
        endpoint: str,
        bucket: str,
        access_key_id: str,
        secret_access_key: str,
        region: str = "auto",
        prefix: str = "owner-documents/test",
        connect_timeout: int = 5,
        read_timeout: int = 60,
        max_attempts: int = 3,
    ) -> None:
        self.enabled = enabled
        self.endpoint = endpoint.strip()
        self.bucket = bucket.strip()
        self.access_key_id = access_key_id.strip()
        self.secret_access_key = secret_access_key.strip()
        self.region = region.strip() or "auto"
        self.prefix = prefix.strip().strip("/") or "owner-documents/test"
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.max_attempts = max_attempts
        self._client = None

    def _validate_configuration(self) -> None:
        if not self.enabled:
            return
        parsed = urlparse(self.endpoint)
        hostname = (parsed.hostname or "").lower()
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
            or not hostname.endswith(".eu.r2.cloudflarestorage.com")
        ):
            raise StorageNotConfigured("Endpoint storage non valido")
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", self.bucket):
            raise StorageNotConfigured("Bucket storage non valido")
        if any(part in {"", ".", ".."} for part in self.prefix.split("/")):
            raise StorageNotConfigured("Prefisso storage non valido")

    @classmethod
    def from_env(cls) -> "R2DocumentStorage":
        provider = os.getenv("OWNER_DOCUMENT_STORAGE_PROVIDER", "r2").strip().lower()
        enabled = _env_bool("OWNER_DOCUMENT_STORAGE_ENABLED", False)
        if enabled and provider != "r2":
            raise StorageNotConfigured("Provider storage non supportato")
        return cls(
            enabled=enabled,
            endpoint=os.getenv("OWNER_DOCUMENT_STORAGE_ENDPOINT", ""),
            bucket=os.getenv("OWNER_DOCUMENT_STORAGE_BUCKET", ""),
            access_key_id=os.getenv("OWNER_DOCUMENT_STORAGE_ACCESS_KEY_ID", ""),
            secret_access_key=os.getenv("OWNER_DOCUMENT_STORAGE_SECRET_ACCESS_KEY", ""),
            region=os.getenv("OWNER_DOCUMENT_STORAGE_REGION", "auto"),
            prefix=os.getenv("OWNER_DOCUMENT_STORAGE_PREFIX", "owner-documents/test"),
            connect_timeout=_env_int("OWNER_DOCUMENT_STORAGE_CONNECT_TIMEOUT_SECONDS", 5),
            read_timeout=_env_int("OWNER_DOCUMENT_STORAGE_READ_TIMEOUT_SECONDS", 60),
            max_attempts=_env_int("OWNER_DOCUMENT_STORAGE_MAX_ATTEMPTS", 3),
        )

    def is_configured(self) -> bool:
        if not (
            self.enabled
            and self.endpoint
            and self.bucket
            and self.access_key_id
            and self.secret_access_key
            and self.region
            and self.prefix
        ):
            return False
        try:
            self._validate_configuration()
        except StorageNotConfigured:
            return False
        return True

    def _require_configured(self) -> None:
        if not self.enabled:
            raise StorageNotConfigured("Storage documentale non configurato")
        self._validate_configuration()
        if not self.is_configured():
            raise StorageNotConfigured("Storage documentale non configurato")

    def _get_client(self):
        self._require_configured()
        if self._client is not None:
            return self._client
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:  # pragma: no cover - guarded by requirements install
            raise StorageNotConfigured("Dipendenza boto3 non disponibile") from exc
        self._client = boto3.client(
            "s3",
            endpoint_url=self.endpoint,
            aws_access_key_id=self.access_key_id,
            aws_secret_access_key=self.secret_access_key,
            region_name=self.region,
            config=Config(
                connect_timeout=self.connect_timeout,
                read_timeout=self.read_timeout,
                retries={"max_attempts": self.max_attempts, "mode": "standard"},
                signature_version="s3v4",
                s3={"addressing_style": "path"},
            ),
        )
        return self._client

    @staticmethod
    def _translate_error(exc: Exception) -> DocumentStorageError:
        try:
            from botocore.exceptions import (
                ClientError,
                ConnectTimeoutError,
                EndpointConnectionError,
                ReadTimeoutError,
            )
        except ImportError:
            return StorageUnavailable("Provider storage non disponibile")
        if isinstance(exc, ClientError):
            response = getattr(exc, "response", {}) or {}
            error = response.get("Error", {}) or {}
            code = str(error.get("Code", ""))
            status = (response.get("ResponseMetadata", {}) or {}).get("HTTPStatusCode")
            if code in {"AccessDenied", "InvalidAccessKeyId", "SignatureDoesNotMatch"} or status == 403:
                return StorageAccessDenied("Accesso storage negato")
            if code in {"NoSuchKey", "NotFound", "404"} or status == 404:
                return StorageObjectNotFound("Oggetto storage non trovato")
        if isinstance(exc, (ConnectTimeoutError, EndpointConnectionError, ReadTimeoutError, TimeoutError)):
            return StorageUnavailable("Storage temporaneamente non disponibile")
        return StorageUnavailable("Operazione storage non riuscita")

    def generate_key(self) -> str:
        self._require_configured()
        return f"{self.prefix}/objects/{uuid.uuid4().hex}"

    def head_object(self, key: str) -> ObjectMetadata:
        try:
            result = self._get_client().head_object(Bucket=self.bucket, Key=key)
        except DocumentStorageError:
            raise
        except Exception as exc:
            raise self._translate_error(exc) from exc
        metadata = result.get("Metadata") or {}
        return ObjectMetadata(
            size_bytes=int(result.get("ContentLength") or 0),
            content_type=normalize_mime(result.get("ContentType")),
            sha256=metadata.get("sha256"),
            etag=str(result.get("ETag", "")).strip('"') or None,
        )

    def put_object(
        self,
        fileobj: BinaryIO,
        *,
        key: str,
        content_type: str,
        size_bytes: int,
        sha256: str,
    ) -> ObjectMetadata:
        fileobj.seek(0)
        try:
            self._get_client().upload_fileobj(
                fileobj,
                self.bucket,
                key,
                ExtraArgs={
                    "ContentType": content_type,
                    "Metadata": {"sha256": sha256, "size-bytes": str(size_bytes)},
                },
            )
        except DocumentStorageError:
            raise
        except Exception as exc:
            raise self._translate_error(exc) from exc
        result = self.head_object(key)
        self._assert_metadata(result, size_bytes=size_bytes, content_type=content_type, sha256=sha256)
        return result

    def open_stream(self, key: str) -> OpenedObject:
        try:
            result = self._get_client().get_object(Bucket=self.bucket, Key=key)
        except DocumentStorageError:
            raise
        except Exception as exc:
            raise self._translate_error(exc) from exc
        metadata = result.get("Metadata") or {}
        return OpenedObject(
            body=result["Body"],
            metadata=ObjectMetadata(
                size_bytes=int(result.get("ContentLength") or 0),
                content_type=normalize_mime(result.get("ContentType")),
                sha256=metadata.get("sha256"),
                etag=str(result.get("ETag", "")).strip('"') or None,
            ),
        )

    def delete_object(self, key: str) -> None:
        try:
            self._get_client().delete_object(Bucket=self.bucket, Key=key)
        except DocumentStorageError:
            raise
        except Exception as exc:
            raise self._translate_error(exc) from exc

    def healthcheck(self) -> dict[str, object]:
        try:
            self._get_client().head_bucket(Bucket=self.bucket)
        except DocumentStorageError:
            raise
        except Exception as exc:
            raise self._translate_error(exc) from exc
        return {"configured": True, "available": True}

    @staticmethod
    def _assert_metadata(
        metadata: ObjectMetadata,
        *,
        size_bytes: int,
        content_type: str,
        sha256: str,
    ) -> None:
        if metadata.size_bytes != size_bytes:
            raise StorageMetadataMismatch("Dimensione storage non coerente")
        if normalize_mime(metadata.content_type) != normalize_mime(content_type):
            raise StorageMetadataMismatch("MIME storage non coerente")
        if metadata.sha256 and metadata.sha256 != sha256:
            raise StorageMetadataMismatch("Checksum storage non coerente")


class InMemoryDocumentStorage:
    """Deterministic private storage for isolated tests; never used as runtime fallback."""

    provider_name = "memory"

    def __init__(self, *, prefix: str = "owner-documents/test") -> None:
        self.prefix = prefix.strip("/")
        self.objects: dict[str, tuple[bytes, ObjectMetadata]] = {}
        self.failures: dict[str, Exception] = {}
        self.enabled = True

    def inject_failure(self, operation: str, exc: Exception) -> None:
        self.failures[operation] = exc

    def clear_failure(self, operation: str) -> None:
        self.failures.pop(operation, None)

    def _fail_if_needed(self, operation: str) -> None:
        exc = self.failures.get(operation)
        if exc is not None:
            raise exc

    def is_configured(self) -> bool:
        return self.enabled

    def generate_key(self) -> str:
        self._fail_if_needed("generate_key")
        return f"{self.prefix}/objects/{uuid.uuid4().hex}"

    def put_object(
        self,
        fileobj: BinaryIO,
        *,
        key: str,
        content_type: str,
        size_bytes: int,
        sha256: str,
    ) -> ObjectMetadata:
        self._fail_if_needed("put_object")
        fileobj.seek(0)
        payload = fileobj.read()
        if len(payload) != size_bytes or hashlib.sha256(payload).hexdigest() != sha256:
            raise StorageMetadataMismatch("Contenuto upload non coerente")
        metadata = ObjectMetadata(size_bytes, normalize_mime(content_type), sha256, None)
        self.objects[key] = (payload, metadata)
        return metadata

    def head_object(self, key: str) -> ObjectMetadata:
        self._fail_if_needed("head_object")
        try:
            return self.objects[key][1]
        except KeyError as exc:
            raise StorageObjectNotFound("Oggetto storage non trovato") from exc

    def open_stream(self, key: str) -> OpenedObject:
        self._fail_if_needed("open_stream")
        try:
            payload, metadata = self.objects[key]
        except KeyError as exc:
            raise StorageObjectNotFound("Oggetto storage non trovato") from exc
        return OpenedObject(io.BytesIO(payload), metadata)

    def delete_object(self, key: str) -> None:
        self._fail_if_needed("delete_object")
        self.objects.pop(key, None)

    def healthcheck(self) -> dict[str, object]:
        self._fail_if_needed("healthcheck")
        if not self.enabled:
            raise StorageNotConfigured("Storage documentale non configurato")
        return {"configured": True, "available": True}


_STORAGE_OVERRIDE: DocumentStorage | None = None


def set_document_storage_for_tests(storage: DocumentStorage | None) -> None:
    global _STORAGE_OVERRIDE
    _STORAGE_OVERRIDE = storage


def get_document_storage() -> DocumentStorage:
    if _STORAGE_OVERRIDE is not None:
        return _STORAGE_OVERRIDE
    return R2DocumentStorage.from_env()


def storage_metadata_for_database(staged: StagedUpload, *, provider: str) -> dict[str, object]:
    return {
        "original_filename": staged.original_filename,
        "sanitized_filename": staged.sanitized_filename,
        "mime_declared": staged.mime_declared,
        "mime_detected": staged.mime_detected,
        "size_bytes": staged.size_bytes,
        "sha256": staged.sha256,
        "storage_provider": provider,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
        "upload_source": "upload_admin",
        "scan_status": "signature_validated",
    }


def iter_stream(
    opened: OpenedObject,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    on_complete=None,
    on_error=None,
) -> Iterator[bytes]:
    completed = False
    try:
        while True:
            chunk = opened.body.read(chunk_size)
            if not chunk:
                completed = True
                break
            yield chunk
    except BaseException as exc:
        if on_error:
            on_error(exc)
        raise
    finally:
        opened.close()
        if completed and on_complete:
            on_complete()
