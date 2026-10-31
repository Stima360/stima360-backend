"""BLOCCO-6B - richieste di firma (adattatore SIMULATO) su PostgreSQL VERO.

    R  richiesta, idempotenza, permessi e agenzie
    T  transizioni: annullamento, scadenza, esiti simulati, stati finali
    S  la simulazione non e' mai «firmata»: vincolo del database, documento
       ricevuto riconoscibile, nessun fornitore reale attivabile
"""
from __future__ import annotations

import shutil
import subprocess
import uuid

import pytest

from tests.test_blocco_2c_integrated_postgres import (  # noqa: F401 - fixture
    CLUSTER_VERIFIED,
    DSN,
    _q,
    app,
    banco,
)
from tests.test_blocco_5a_visit_booking_postgres import _property
from tests.test_blocco_6a_visit_sheets_postgres import _visit

pytestmark = [
    pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL locale"),
    pytest.mark.skipif(not CLUSTER_VERIFIED, reason="cluster usa-e-getta non dichiarato verificato "
                       "(BLOCCO_2C_TEST_CLUSTER_VERIFIED=1)"),
]

API = "/api/signature-requests"


def _sheet(b, who="owner_a"):
    pid = _property(b)
    aid = _visit(b, pid)
    return b["login"](who).post(f"/api/visit-sheets/appointments/{aid}").json()["sheet"]["id"]


def _create(client, sheet_id, key=None, **extra):
    return client.post(API, json={"visit_sheet_id": sheet_id, "signer_name": "Visitatore Sintetico",
                                  "signer_contact": "visitatore@esempio.invalid", "expires_in_days": 7,
                                  "idempotency_key": key or str(uuid.uuid4()), **extra})


def test_r_create_is_idempotent_simulated_and_scoped(app):
    sheet = _sheet(app)
    owner = app["login"]("owner_a")
    key = str(uuid.uuid4())
    first, second = _create(owner, sheet, key), _create(owner, sheet, key)
    assert first.status_code == second.status_code == 200, first.text
    req = first.json()["request"]
    assert first.json()["created"] is True and second.json()["created"] is False
    assert second.json()["request"]["id"] == req["id"]
    assert req["provider"] == "simulated" and req["simulation"] is True and req["valid_signature"] is False
    assert req["status"] == "sent" and req["provider_reference"].startswith("SIM-")
    assert "SIMULAZIONE" not in req["status_label"] and "idempotency_key" not in req
    for who in ("owner_b", "agent2_a"):
        assert _create(app["login"](who), sheet).status_code == 404, who
        assert app["login"](who).get(API, params={"visit_sheet_id": sheet}).status_code == 404, who
        assert app["login"](who).post(f"{API}/{req['id']}/cancel").status_code == 404, who
    assert _create(owner, sheet, provider="firmaqualificata").status_code == 422          # corpo chiuso
    events = _q(app, "SELECT from_status, to_status FROM signature_request_events WHERE request_id = %s", (req["id"],))
    assert events == [(None, "sent")]


def test_t_cancel_and_final_states(app):
    sheet = _sheet(app)
    owner = app["login"]("owner_a")
    rid = _create(owner, sheet).json()["request"]["id"]
    r = owner.post(f"{API}/{rid}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    for action, body in (("cancel", {}), ("simulate", {"outcome": "completed"})):
        again = owner.post(f"{API}/{rid}/{action}", json=body)
        assert again.status_code == 409 and again.json()["detail"]["code"] == "REQUEST_CLOSED"
    with pytest.raises(Exception, match="is closed"):
        _q(app, "UPDATE signature_requests SET status = 'sent', closed_at = NULL WHERE id = %s", (rid,))


def test_t_expiry_is_applied_and_blocks_outcomes(app):
    sheet = _sheet(app)
    owner = app["login"]("owner_a")
    rid = _create(owner, sheet).json()["request"]["id"]
    _q(app, "ALTER TABLE signature_requests DISABLE TRIGGER trg_signature_requests_guard; "
            "UPDATE signature_requests SET expires_at = NOW() - interval '1 minute' WHERE id = %s; "
            "ALTER TABLE signature_requests ENABLE TRIGGER trg_signature_requests_guard", (rid,))
    r = owner.post(f"{API}/{rid}/simulate", json={"outcome": "completed"})
    assert r.status_code == 409
    (item,) = [x for x in owner.get(API, params={"visit_sheet_id": sheet}).json()["items"] if x["id"] == rid]
    assert item["status"] == "expired" and item["has_document"] is False


def test_s_simulated_completion_is_never_a_signature(app):
    sheet = _sheet(app)
    owner = app["login"]("owner_a")
    rid = _create(owner, sheet).json()["request"]["id"]
    r = owner.post(f"{API}/{rid}/simulate", json={"outcome": "completed"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "simulated_completed" and out["valid_signature"] is False and out["has_document"] is True
    assert "nessuna firma valida" in out["status_label"]
    doc = owner.get(f"{API}/{rid}/document")
    assert doc.status_code == 200 and doc.content.startswith(b"%PDF")
    assert "SIMULAZIONE-non-firmato" in doc.headers["content-disposition"]
    if shutil.which("pdftotext"):
        text = " ".join(subprocess.run(["pdftotext", "-", "-"], input=doc.content, capture_output=True,
                                       check=True).stdout.decode().split())
        assert "SIMULAZIONE — NESSUNA FIRMA VALIDA" in text and "Non contiene alcuna firma elettronica" in text
    # il database rifiuta una simulazione «firmata», anche scritta a mano
    rid2 = _create(owner, sheet).json()["request"]["id"]
    with pytest.raises(Exception, match="simulated_never_signed"):
        _q(app, "UPDATE signature_requests SET status = 'signed', closed_at = NOW() WHERE id = %s", (rid2,))
    declined = owner.post(f"{API}/{rid2}/simulate", json={"outcome": "declined"}).json()
    assert declined["status"] == "declined" and declined["has_document"] is False
    assert owner.post(f"{API}/{rid2}/simulate", json={"outcome": "signed"}).status_code == 422
    # il foglio visita resta non firmato
    assert all(s["signed"] is False for v in owner.get("/api/visit-sheets", params={"property_id": _q(
        app, "SELECT property_id FROM visit_sheets WHERE id = %s", (sheet,))[0][0]}).json()["items"] for s in v["sheets"])


def test_s_no_real_provider_can_be_enabled(app, monkeypatch):
    sheet = _sheet(app)
    monkeypatch.setenv("ESIGN_PROVIDER", "fornitore-reale")
    r = _create(app["login"]("owner_a"), sheet)
    assert r.status_code == 503 and r.json()["detail"]["code"] == "PROVIDER_NOT_CONFIGURED"
