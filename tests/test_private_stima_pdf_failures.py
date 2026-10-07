"""F07 failure handling without any database or delivery service."""
from __future__ import annotations

import hashlib
from pathlib import Path
import re

from fastapi import HTTPException
from psycopg2 import InterfaceError, OperationalError
import pytest

import stima_pdf


TOKEN = "00000000-0000-4000-8000-000000000001"
PDF = b"%PDF-1.4\nsynthetic-private-report\n%%EOF\n"


class DisconnectedConnection:
    def __init__(self, cleanup_error):
        self.cleanup_error = cleanup_error
        self.closed = False

    def cursor(self):
        raise OperationalError("synthetic-connection-lost")

    def rollback(self):
        if self.cleanup_error in {"rollback", "both"}:
            raise InterfaceError("synthetic-rollback-on-closed-connection")

    def close(self):
        self.closed = True
        if self.cleanup_error in {"close", "both"}:
            raise InterfaceError("synthetic-close-on-closed-connection")


@pytest.mark.parametrize("operation", [stima_pdf.download, stima_pdf.generate])
@pytest.mark.parametrize("cleanup_error", ["rollback", "close", "both"])
def test_lost_connection_remains_a_retryable_pdf_error(operation, cleanup_error):
    connection = DisconnectedConnection(cleanup_error)
    with pytest.raises(HTTPException) as caught:
        operation(501, token=TOKEN, connection_factory=lambda: connection)
    assert caught.value.status_code == 503
    assert "synthetic" not in caught.value.detail
    assert connection.closed


class ReadyCursor:
    def __init__(self):
        self.query = ""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, query, *args):
        self.query = query

    def fetchone(self):
        if "FROM stima_pdf_artifacts" in self.query:
            return ({}, "ready", PDF, hashlib.sha256(PDF).hexdigest())
        return (1,)


class ReadyConnection:
    def __init__(self):
        self.committed = False
        self.closed = False

    def cursor(self):
        return ReadyCursor()

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        self.closed = True
        raise InterfaceError("synthetic-close-after-commit")


@pytest.mark.parametrize("operation", [stima_pdf.download, stima_pdf.generate])
def test_cleanup_failure_does_not_discard_an_authorized_committed_pdf(operation):
    connection = ReadyConnection()
    result = operation(501, token=TOKEN, connection_factory=lambda: connection)
    assert result == PDF
    assert connection.committed and connection.closed


def test_pdf_rollback_locks_the_table_before_checking_that_it_is_empty():
    down = (Path(__file__).resolve().parents[1] / "migrations" /
            "093_stima_private_pdf_down.sql").read_text()
    sql = re.sub(r"--[^\n]*", "", down).upper()
    table_lock = re.search(r"LOCK\s+TABLE\s+STIMA_PDF_ARTIFACTS\s+IN\s+ACCESS\s+EXCLUSIVE\s+MODE\s*;", sql)
    empty_check = re.search(r"IF\s+EXISTS\s*\(", sql)
    table_drop = re.search(r"DROP\s+TABLE\s+STIMA_PDF_ARTIFACTS\s*;", sql)
    assert table_lock is not None, "a PDF may be inserted between the empty check and DROP"
    assert empty_check is not None and table_drop is not None
    assert table_lock.start() < empty_check.start() < table_drop.start()
