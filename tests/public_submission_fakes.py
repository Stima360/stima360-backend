"""Receipt-table double for existing offline tests of legacy consumers (F04).

Identity, advisory locking, SQL and recovery of ``public_submission_receipts``
are tested with PostgreSQL in test_public_submission_receipts_postgres.py and
in the journeys. The legacy consumers (P17, P18-C, P27-6, CORE bridge, F16)
keep their focused connection doubles for ``stime``; this module lets them
reach the pipeline through the REAL F04 entry point (``salva_stima`` ->
``_receive_submission`` -> the real ``Receipt`` class) by answering, in
memory, only the statements the receipt module itself issues on
``public_submission_receipts``. Every other statement is delegated untouched
to the legacy double, so nothing of the receipt semantics (``step``,
``direct_once``, ``finish``, ``envelope``) is re-implemented here.

SENTINELLA AGGIORNATA DA STIMA-CRM-AGENDA-1.
"""
from __future__ import annotations

import copy
import json
import uuid
from datetime import datetime, timedelta, timezone

from psycopg2.extras import Json


def identity_fields(request_id=None, receipt_key=None):
    """Body fields that satisfy ``public_submissions.identity`` (same shape the
    public pages send: ``request_id``/``client_request_id`` plus ``receipt_key``)."""
    request_id = str(request_id or uuid.uuid4())
    receipt_key = receipt_key or (uuid.uuid4().hex + uuid.uuid4().hex)
    return {"request_id": request_id, "client_request_id": request_id, "receipt_key": receipt_key}


def response_body(response):
    """The JSON the browser receives from ``_submission_response``."""
    return json.loads(response.body)


def _normalize(sql):
    return " ".join(sql.split()).lower()


def _plain(value):
    if isinstance(value, Json):
        value = value.adapted
    return copy.deepcopy(value)


class ReceiptStore:
    """In-memory ``public_submission_receipts`` shared by every connection of a test."""

    def __init__(self, *, now=None, ttl=timedelta(days=7)):
        self.now = now or datetime(2026, 10, 7, 9, 0, tzinfo=timezone.utc)
        self.ttl = ttl
        self.rows = {}
        self.parents = set()      # (stima_id, agency_id) attached by the pipeline
        self.statements = []

    # --- wiring -----------------------------------------------------------

    def connection(self, inner):
        return _ReceiptConnection(self, inner)

    def factory(self, inner_factory):
        return lambda: self.connection(inner_factory())

    def receipt(self, request_id):
        return copy.deepcopy(self.rows[str(request_id)])

    # --- the statements the receipt module issues --------------------------

    def execute(self, sql, params, *, dict_rows):
        """Returns (handled, rows, rowcount)."""
        norm = _normalize(sql)
        if "public_submission_receipts" not in norm and "pg_advisory_" not in norm \
                and "to_regclass" not in norm and not norm.startswith("select 1 from stime where id=%s and agency_id=%s"):
            return False, None, None
        self.statements.append((norm, params))
        if "pg_advisory_lock" in norm or "pg_advisory_unlock" in norm:
            return True, [(True,)], 1
        if "to_regclass" in norm:
            # The legacy doubles have no CRM tables: the envelope then leaves
            # the committed contact/lead/property ids empty, exactly as the
            # real module does when those tables are absent.
            return True, [tuple(None for _ in range(norm.count("to_regclass")))], 1
        if norm.startswith("select 1 from stime where id=%s and agency_id=%s"):
            # The receipt's parent check: the row the receipt was attached to
            # exists (attachment and INSERT share one transaction).
            return True, ([(1,)] if tuple(params) in self.parents else []), 1
        if norm.startswith("select") and "from public_submission_receipts where request_id=%s" in norm:
            row = self.rows.get(str(params[0]))
            if row is None:
                return True, [], 0
            row = copy.deepcopy(row)
            if " as live " in norm:
                row["live"] = row["expires_at"] > self.now
            return True, [row if dict_rows else tuple(row.values())], 1
        if norm.startswith("insert into public_submission_receipts"):
            request_id, kind, proof, payload_hash, payload = params
            if request_id in self.rows:
                raise RuntimeError("duplicate key value violates unique constraint public_submission_receipts_pkey")
            self.rows[request_id] = {
                "request_id": request_id, "kind": kind, "status": "received",
                "proof_sha256": proof, "payload_sha256": payload_hash,
                "request_payload": _plain(payload), "frozen": {}, "checkpoints": {},
                "response_payload": None, "stima_id": None, "agency_id": None, "detail_id": None,
                "created_at": self.now, "updated_at": self.now, "expires_at": self.now + self.ttl,
                "voided_at": None,
            }
            return True, [], 1
        if norm.startswith("update public_submission_receipts set stima_id=%s, agency_id=%s, detail_id=%s"):
            stima_id, agency_id, detail_id, request_id = params
            row = self.rows.get(str(request_id))
            if row is None or row["stima_id"] is not None:
                return True, [], 0
            row.update(stima_id=stima_id, agency_id=agency_id, detail_id=detail_id, updated_at=self.now)
            self.parents.add((stima_id, agency_id))
            return True, [], 1
        if norm.startswith("update public_submission_receipts set "):
            column = norm[len("update public_submission_receipts set "):].split("=", 1)[0].strip()
            value, request_id = params
            row = self.rows.get(str(request_id))
            if row is None:
                return True, [], 0
            row[column] = _plain(value)
            row["updated_at"] = self.now
            return True, [], 1
        raise AssertionError(f"receipt statement not emulated: {norm}")


class _ReceiptCursor:
    def __init__(self, store, inner, *, dict_rows):
        self._store, self._inner, self._dict_rows = store, inner, dict_rows
        self._rows, self._rowcount, self._handled = None, None, False

    def execute(self, sql, params=None):
        handled, rows, rowcount = self._store.execute(sql, params or (), dict_rows=self._dict_rows)
        self._handled = handled
        if handled:
            self._rows, self._rowcount = list(rows), rowcount
            return
        self._inner.execute(sql, params)

    def fetchone(self):
        if self._handled:
            return self._rows.pop(0) if self._rows else None
        return self._inner.fetchone()

    def fetchall(self):
        if self._handled:
            rows, self._rows = self._rows, []
            return rows
        return self._inner.fetchall()

    @property
    def rowcount(self):
        if self._handled:
            return self._rowcount
        # The legacy doubles count no rows: the pipeline's UPDATEs on `stime`
        # address the row they have just inserted, so one row.
        return getattr(self._inner, "rowcount", 1)

    def close(self):
        close = getattr(self._inner, "close", None)
        if close is not None:
            close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def __getattr__(self, name):
        return getattr(self._inner, name)


class _ReceiptConnection:
    def __init__(self, store, inner):
        self._store, self._inner = store, inner

    def cursor(self, **kwargs):
        return _ReceiptCursor(self._store, self._inner.cursor(**kwargs), dict_rows="cursor_factory" in kwargs)

    def rollback(self):
        rollback = getattr(self._inner, "rollback", None)
        if rollback is not None:
            rollback()

    def __getattr__(self, name):
        return getattr(self._inner, name)
