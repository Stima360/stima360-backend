"""Opt-in Cloudflare R2 TEST E2E for OWNER 0.2 P4.

It is disabled by default and never touches the database. The test creates a
small opaque object under the configured TEST prefix and always attempts cleanup.
"""
from __future__ import annotations

import io
import os
import uuid

import pytest

from owner.document_storage import R2DocumentStorage

LIVE = os.getenv("OWNER_P4_LIVE_STORAGE_E2E", "").strip().lower() == "true"
pytestmark = pytest.mark.skipif(
    not LIVE,
    reason="OWNER_P4_LIVE_STORAGE_E2E=true non autorizzato/configurato",
)


def test_r2_live_put_head_stream_delete():
    storage = R2DocumentStorage.from_env()
    assert storage.is_configured()
    payload = b"%PDF-1.7\nOWNER P4 live storage probe\n%%EOF\n"
    import hashlib

    digest = hashlib.sha256(payload).hexdigest()
    key = f"{storage.prefix}/live-e2e/{uuid.uuid4().hex}"
    try:
        storage.put_object(
            io.BytesIO(payload),
            key=key,
            content_type="application/pdf",
            size_bytes=len(payload),
            sha256=digest,
        )
        metadata = storage.head_object(key)
        assert metadata.size_bytes == len(payload)
        assert metadata.content_type == "application/pdf"
        assert metadata.sha256 == digest
        opened = storage.open_stream(key)
        try:
            assert opened.body.read() == payload
        finally:
            opened.close()
    finally:
        storage.delete_object(key)
    with pytest.raises(Exception):
        storage.head_object(key)
