"""F16: a token persistence failure must stop delivery of unusable links.

No database or external provider is used. Set STIMA360_TOKEN_FAILURE_BASELINE
to a Git ref to replay only the old writer in memory for the RED check.
Earlier committed writes remain outside this token-delivery regression (F04/F06).
"""
from __future__ import annotations

import ast
import asyncio
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException

from integration_p2_support import import_project_module
from tests.test_public_stima_core_crm_bridge import (
    JsonRequest, LegacyConnection, LegacyCursor, PUBLIC_WRITER_CTX,
)


class TokenUpdateFailureCursor(LegacyCursor):
    def execute(self, query, params=None):
        super().execute(query, params)
        if " ".join(query.split()).lower().startswith("update stime set token="):
            self.connection.token_update_attempts += 1
            raise RuntimeError("synthetic-token-write-failure")


class TokenUpdateFailureConnection(LegacyConnection):
    def __init__(self):
        super().__init__()
        self.rollback_count = 0
        self.token_update_attempts = 0

    def cursor(self, **kwargs):
        return TokenUpdateFailureCursor(self, dict_rows="cursor_factory" in kwargs)

    def rollback(self):
        self.rollback_count += 1


def _writer(main):
    baseline = os.getenv("STIMA360_TOKEN_FAILURE_BASELINE")
    if not baseline:
        return main.salva_stima
    source = subprocess.check_output(
        ["git", "show", f"{baseline}:main.py"],
        cwd=Path(__file__).resolve().parents[1], text=True,
    )
    function = next(node for node in ast.parse(source).body
                    if isinstance(node, ast.AsyncFunctionDef) and node.name == "salva_stima")
    function.decorator_list = []
    module = ast.Module(body=[function], type_ignores=[])
    namespace = dict(main.__dict__)
    exec(compile(ast.fix_missing_locations(module), f"main.py@{baseline}", "exec"), namespace)
    return namespace["salva_stima"]


def test_token_persistence_failure_returns_generic_error_before_pdf_or_delivery(monkeypatch):
    # SENTINELLA AGGIORNATA DA STIMA-CRM-AGENDA-1 (contratto F04). La scrittura
    # del token e' il passo `fields_token` della ricevuta: se fallisce, la
    # transazione del token torna indietro, nessun PDF, email, WhatsApp o coda
    # parte, e il sito riceve 202 `partial` (non un successo con link
    # inutilizzabili, e nemmeno un 500 anonimo: la ricevuta e' riprendibile).
    from tests.public_submission_fakes import ReceiptStore, identity_fields, response_body

    main = import_project_module("main")
    connections = []
    deliveries = {"pdf": [], "email": [], "whatsapp": [], "outbox": []}
    store = ReceiptStore()

    def connection_factory():
        connection = TokenUpdateFailureConnection()
        connections.append(connection)
        return connection

    monkeypatch.setattr(main, "get_connection", store.factory(connection_factory))
    monkeypatch.setattr(main, "_routed_public_stima_system_context", lambda conn, *, comune: (
        PUBLIC_WRITER_CTX,
        SimpleNamespace(agency_id=1, source="synthetic_fixture", matched_value=comune),
    ))
    monkeypatch.setattr(main.core_service, "bridge_public_stima", lambda *a, **k: {
        "status": "linked", "contact_id": 31, "lead_id": 41,
    })
    monkeypatch.setattr(main.owner_provisioning, "provision_for_public_stima",
                        lambda ctx, **k: {"status": "provisioned"})
    monkeypatch.setattr(main.property_site_sync, "sync_public_stima",
                        lambda ctx, **k: {"property_id": 71})
    monkeypatch.setattr(main.seller_intelligence_service, "record_event", lambda **k: {"recorded": True})
    monkeypatch.setattr(main.followup_service, "run_followup", lambda **k: {"action_id": 1})
    monkeypatch.setattr(main.property_watch_service, "ensure_watch_for_stima", lambda stima_id: {"watch_id": 1})
    monkeypatch.setattr(main, "compute_from_payload", lambda payload: {
        "price_exact": 180000, "eur_mq_finale": 2000,
        "valore_pertinenze": 5000, "base_mq": 1500,
    })
    monkeypatch.setattr(main, "genera_pdf_stima", lambda *a, **k:
                        deliveries["pdf"].append((a, k)) or b"%PDF-synthetic\n%%EOF")
    monkeypatch.setattr(main, "invia_mail", lambda *a, **k: deliveries["email"].append((a, k)) or True)
    monkeypatch.setattr(main, "invia_whatsapp", lambda *a, **k: deliveries["whatsapp"].append((a, k)) or True)
    monkeypatch.setattr(main.communication_service, "enqueue", lambda *a, **k:
                        deliveries["outbox"].append((a, k)))

    response = asyncio.run(_writer(main)(JsonRequest({
        "comune": "Alba Adriatica", "microzona": "Centro", "mq": 90,
        "nome": "Test", "cognome": "Locale", "email": "test@example.invalid",
        "telefono": "+39 333 123 4567", "prezzo_mq_base": 1500,
        **identity_fields(),
    })))
    body = response_body(response)

    delivery_counts = {key: len(value) for key, value in deliveries.items()}
    assert response.status_code == 202 and body["success"] is False and body["ok"] is False, (
        "The writer returned success after token persistence failed; "
        f"delivery counts: {delivery_counts}"
    )
    assert body["receipt"]["status"] == "partial" and body["receipt"]["resumable"] is True
    assert body["receipt"]["steps"]["fields_token"] == "failed"
    assert body["receipt"]["errors"]["fields_token"]["error_type"] == "HTTPException"
    assert "token" not in body and "pdf_url" not in body and "detail_url" not in body
    assert deliveries == {"pdf": [], "email": [], "whatsapp": [], "outbox": []}
    token_connection = next(conn for conn in connections if conn.token_update_attempts)
    assert token_connection.token_update_attempts == 1
    assert token_connection.commit_count == 0
    assert token_connection.rollback_count == 1
    assert token_connection.closed_cursor and token_connection.closed
    assert sum(1 for c in connections for q, _ in c.executions if q.startswith("INSERT INTO stime")) == 1
