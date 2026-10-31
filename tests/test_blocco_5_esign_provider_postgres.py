"""BLOCCO-5 - firma elettronica: parti INDIPENDENTI dal fornitore (migrazione 125).

Nessun fornitore reale esiste nel codice. Qui un fornitore FINTO, registrato
solo dentro il test, recita il contratto (`esign.providers.SignatureProvider`)
in ambiente 'sandbox' o 'production'. Nessuna rete.

    E  ambiente: la firma e' valida SOLO in 'production'; la simulazione resta
       'simulation' e non riceve eventi
    W  eventi del fornitore: autenticita' (HMAC del corpo grezzo), una sola
       applicazione per id evento, stato RILETTO dal fornitore, documento e
       audit trail conservati con impronta, richiesta sconosciuta registrata
    C  creazione idempotente sulla chiave, annullamento presso il fornitore
"""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone

import pytest

from tests.test_blocco_2c_integrated_postgres import (  # noqa: F401 - fixture
    CLUSTER_VERIFIED,
    DSN,
    _q,
    app,
    banco,
)
from tests.test_blocco_6b_signature_requests_postgres import API, _create, _sheet

pytestmark = [
    pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL locale"),
    pytest.mark.skipif(not CLUSTER_VERIFIED, reason="cluster usa-e-getta non dichiarato verificato "
                       "(BLOCCO_2C_TEST_CLUSTER_VERIFIED=1)"),
]

SECRET = "segreto-webhook-di-prova"
HOOK = "/api/public/esign/webhooks/fornitore_prova"


class FakeProvider:
    """Recita il contratto. Lo stato remoto lo decide il test (`REMOTE`)."""

    name = "fornitore_prova"
    is_simulation = False
    environment = "sandbox"
    REMOTE: dict = {}
    CALLS: list = []

    def create_request(self, *, document, document_sha256, signer_name, signer_contact, expires_at, idempotency_key):
        from esign.providers import ProviderRequest
        self.CALLS.append(("create", idempotency_key))
        return ProviderRequest(reference=f"REM-{uuid.uuid5(uuid.NAMESPACE_URL, idempotency_key).hex[:12]}")

    def cancel_request(self, reference):
        self.CALLS.append(("cancel", reference))

    def fetch_status(self, reference):
        from esign.providers import ProviderStatus
        return self.REMOTE.get(reference, ProviderStatus("pending"))

    def fetch_document(self, reference, *, document_sha256, signer_name):
        from esign.providers import ProviderDocument
        return ProviderDocument(content=b"%PDF-1.4 documento firmato di prova " + reference.encode(),
                                media_type="application/pdf")

    def fetch_audit_trail(self, reference):
        from esign.providers import ProviderDocument
        return ProviderDocument(content=b"%PDF-1.4 rapporto di completamento " + reference.encode(),
                                media_type="application/pdf")

    def verify_event(self, headers, raw_body):
        from esign.providers import ProviderEvent, verify_hmac_sha256
        verify_hmac_sha256(SECRET, raw_body, headers.get("x-prova-signature"))
        data = json.loads(raw_body)
        return ProviderEvent(event_id=data["event_id"], event_type=data["event_name"], reference=data["reference"])


@pytest.fixture
def real(app, monkeypatch):
    from esign import providers

    FakeProvider.REMOTE, FakeProvider.CALLS = {}, []
    monkeypatch.setitem(providers.REAL_PROVIDERS, FakeProvider.name, FakeProvider)
    monkeypatch.setenv("ESIGN_PROVIDER", FakeProvider.name)
    monkeypatch.setattr(FakeProvider, "environment", "sandbox")
    return app


def _post_event(b, body: dict, *, secret=SECRET, raw=None):
    from fastapi.testclient import TestClient

    import main
    raw = raw if raw is not None else json.dumps(body).encode()
    sig = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return TestClient(main.app, base_url="https://testserver").post(
        HOOK, content=raw, headers={"Content-Type": "application/json", "X-Prova-Signature": sig})


def _signed(reference, level="aes"):
    from esign.providers import ProviderStatus
    FakeProvider.REMOTE[reference] = ProviderStatus("signed", signed_at=datetime.now(timezone.utc),
                                                    signature_level=level)


# ---------------------------------------------------------------------------
# E - ambiente
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("environment, valid", [("sandbox", False), ("production", True)])
def test_e_a_signature_is_valid_only_in_production(real, monkeypatch, environment, valid):
    monkeypatch.setattr(FakeProvider, "environment", environment)
    owner = real["login"]("owner_a")
    req = _create(owner, _sheet(real)).json()["request"]
    assert req["provider"] == "fornitore_prova" and req["provider_environment"] == environment
    assert req["simulation"] is False and req["valid_signature"] is False
    _signed(req["provider_reference"])
    r = _post_event(real, {"event_id": f"evt-{uuid.uuid4()}", "event_name": "signature_request.done",
                           "reference": req["provider_reference"]})
    assert r.status_code == 200 and r.json()["outcome"] == "applied", r.text
    (now,) = owner.get(API, params={"visit_sheet_id": req["visit_sheet_id"]}).json()["items"]
    assert now["status"] == "signed" and now["valid_signature"] is valid and now["signature_level"] == "aes"
    doc = owner.get(f"{API}/{req['id']}/document")
    assert doc.status_code == 200 and doc.content.startswith(b"%PDF") and b"documento firmato" in doc.content
    trail = owner.get(f"{API}/{req['id']}/audit-trail")
    assert trail.status_code == 200 and b"rapporto di completamento" in trail.content


def test_e_the_simulation_keeps_its_environment_and_receives_no_events(app):
    owner = app["login"]("owner_a")
    req = _create(owner, _sheet(app)).json()["request"]
    assert req["provider_environment"] == "simulation" and req["environment_label"] == "Simulazione locale"
    r = _post_event(app, {"event_id": "x", "event_name": "done", "reference": req["provider_reference"]})
    assert r.status_code == 404
    from fastapi.testclient import TestClient

    import main
    assert TestClient(main.app).post("/api/public/esign/webhooks/simulated", content=b"{}").status_code == 404
    with pytest.raises(Exception, match="not_simulated_chk"):
        _q(app, "INSERT INTO signature_provider_events (provider, provider_event_id, event_type, body_sha256, outcome) "
                "VALUES ('simulated', 'e1', 't', repeat('a', 64), 'applied')")
    with pytest.raises(Exception, match="only status, outcome and document may change"):       # immutabile
        _q(app, "UPDATE signature_requests SET provider_environment = 'production' WHERE id = %s", (req["id"],))
    with pytest.raises(Exception, match="simulation_env_chk"):
        _q(app, "INSERT INTO signature_requests (agency_id, visit_sheet_id, provider, provider_reference, signer_name, "
                "signer_contact, status, expires_at, idempotency_key, document_sha256, created_by_user_id, "
                "provider_environment) SELECT agency_id, visit_sheet_id, provider, NULL, signer_name, signer_contact, "
                "'sent', expires_at, 'chiave-ambiente', document_sha256, created_by_user_id, 'production' "
                "FROM signature_requests WHERE id = %s", (req["id"],))


# ---------------------------------------------------------------------------
# W - eventi del fornitore
# ---------------------------------------------------------------------------

def test_w_an_unauthentic_event_changes_nothing(real):
    owner = real["login"]("owner_a")
    req = _create(owner, _sheet(real)).json()["request"]
    _signed(req["provider_reference"])
    body = {"event_id": "evt-falso", "event_name": "signature_request.done", "reference": req["provider_reference"]}
    assert _post_event(real, body, secret="segreto-sbagliato").status_code == 401
    raw = json.dumps(body).encode()
    from fastapi.testclient import TestClient

    import main
    assert TestClient(main.app).post(HOOK, content=raw).status_code == 401                 # senza firma
    assert _q(real, "SELECT status FROM signature_requests WHERE id = %s", (req["id"],)) == [("sent",)]
    assert _q(real, "SELECT count(*) FROM signature_provider_events WHERE provider_event_id = 'evt-falso'") == [(0,)]


def test_w_a_repeated_event_is_applied_once(real):
    owner = real["login"]("owner_a")
    req = _create(owner, _sheet(real)).json()["request"]
    _signed(req["provider_reference"])
    body = {"event_id": f"evt-{uuid.uuid4()}", "event_name": "signature_request.done",
            "reference": req["provider_reference"]}
    assert _post_event(real, body).json()["outcome"] == "applied"
    assert _post_event(real, body).json()["outcome"] == "duplicate"
    assert _q(real, "SELECT count(*) FROM signature_request_events WHERE request_id = %s AND to_status = 'signed'",
              (req["id"],)) == [(1,)]
    assert _q(real, "SELECT count(*) FROM signature_provider_events WHERE provider_event_id = %s",
              (body["event_id"],)) == [(1,)]


def test_w_the_event_content_never_decides_alone(real):
    """L'evento dice «firmata», il fornitore riletto dice «in attesa»: nulla cambia."""
    owner = real["login"]("owner_a")
    req = _create(owner, _sheet(real)).json()["request"]
    out = _post_event(real, {"event_id": f"evt-{uuid.uuid4()}", "event_name": "signature_request.done",
                             "reference": req["provider_reference"]}).json()
    assert out["outcome"] == "no_change"
    assert _q(real, "SELECT status FROM signature_requests WHERE id = %s", (req["id"],)) == [("sent",)]


def test_w_declined_and_unknown_requests(real):
    from esign.providers import ProviderStatus
    owner = real["login"]("owner_a")
    req = _create(owner, _sheet(real)).json()["request"]
    FakeProvider.REMOTE[req["provider_reference"]] = ProviderStatus("declined")
    assert _post_event(real, {"event_id": f"evt-{uuid.uuid4()}", "event_name": "signature_request.declined",
                              "reference": req["provider_reference"]}).json()["outcome"] == "applied"
    assert _q(real, "SELECT status, received_document IS NULL FROM signature_requests WHERE id = %s",
              (req["id"],)) == [("declined", True)]
    # un evento su una richiesta chiusa: registrato come incongruente, nessuna transizione
    _signed(req["provider_reference"])
    assert _post_event(real, {"event_id": f"evt-{uuid.uuid4()}", "event_name": "signature_request.done",
                              "reference": req["provider_reference"]}).json()["outcome"] == "status_mismatch"
    out = _post_event(real, {"event_id": f"evt-{uuid.uuid4()}", "event_name": "x", "reference": "REM-sconosciuto"})
    assert out.json()["outcome"] == "unknown_request"
    assert _q(real, "SELECT request_id FROM signature_provider_events WHERE provider_reference = 'REM-sconosciuto'") == \
        [(None,)]


def test_w_signed_without_level_or_date_is_refused(real):
    from esign.providers import ProviderStatus
    owner = real["login"]("owner_a")
    req = _create(owner, _sheet(real)).json()["request"]
    FakeProvider.REMOTE[req["provider_reference"]] = ProviderStatus("signed")
    r = _post_event(real, {"event_id": f"evt-{uuid.uuid4()}", "event_name": "done", "reference": req["provider_reference"]})
    assert r.status_code == 409
    assert _q(real, "SELECT status FROM signature_requests WHERE id = %s", (req["id"],)) == [("sent",)]
    # anche il database rifiuta una firma senza prove
    with pytest.raises(Exception, match="signed_evidence_chk"):
        _q(real, "UPDATE signature_requests SET status = 'signed', closed_at = NOW() WHERE id = %s", (req["id"],))


# ---------------------------------------------------------------------------
# C - creazione e annullamento
# ---------------------------------------------------------------------------

def test_c_create_is_idempotent_and_cancel_reaches_the_provider(real):
    owner = real["login"]("owner_a")
    sheet = _sheet(real)
    key = str(uuid.uuid4())
    first, second = _create(owner, sheet, key).json(), _create(owner, sheet, key).json()
    assert first["created"] is True and second["created"] is False
    assert first["request"]["id"] == second["request"]["id"]
    assert FakeProvider.CALLS.count(("create", key)) == 1
    rid, ref = first["request"]["id"], first["request"]["provider_reference"]
    assert owner.post(f"{API}/{rid}/cancel").json()["status"] == "cancelled"
    assert ("cancel", ref) in FakeProvider.CALLS
    with pytest.raises(Exception, match="already exists|duplicate|uq_signature_requests_provider_reference"):
        _q(real, "INSERT INTO signature_requests (agency_id, visit_sheet_id, provider, provider_reference, signer_name, "
                 "signer_contact, status, expires_at, idempotency_key, document_sha256, created_by_user_id, "
                 "provider_environment) SELECT agency_id, visit_sheet_id, provider, provider_reference, signer_name, "
                 "signer_contact, 'sent', expires_at, 'altra-chiave', document_sha256, created_by_user_id, "
                 "provider_environment FROM signature_requests WHERE id = %s", (rid,))
