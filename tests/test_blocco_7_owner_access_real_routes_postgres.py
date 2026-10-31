"""BLOCCO-7 - accesso del proprietario al report, SOLO con route reali.

Account, autorizzazione all'immobile e token di accesso si creano con le route
amministrative del portale (`/api/owner/admin/...`, token a visualizzazione
unica: nessuna email); la sessione nasce da `POST /api/owner/portal/auth/token`.
Revoca con `POST /api/owner/admin/access/{id}/revoke`. Nessun trigger
disattivato; nessun dato inserito a mano oltre a immobile e contatti di prova.

    report e PDF solo per immobili autorizzati, altra agenzia esclusa
    revoca efficace subito anche sul download diretto del PDF
    nessun accesso a bozze di pubblicazione, fogli visita, valutazioni, dati interni
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

pytestmark = [
    pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL locale"),
    pytest.mark.skipif(not CLUSTER_VERIFIED, reason="cluster usa-e-getta non dichiarato verificato "
                       "(BLOCCO_2C_TEST_CLUSTER_VERIFIED=1)"),
]

PORTAL = "/api/owner/portal"
ADMIN = "/api/owner/admin"
RESERVED = ("88888", "NOTA-INTERNA-7B", "Interessato Riservato 7B", "3337776655")


def _property(b, agency="a"):
    return _q(b, "INSERT INTO properties (agency_id, code, title, property_type, commercial_status, city, address, "
                 "asking_price, minimum_price, internal_notes) VALUES (%s, %s, 'Casa proprietario', 'apartment', "
                 "'active', 'Comune Sintetico', 'Via Proprietario', 210000, 88888, 'NOTA-INTERNA-7B') RETURNING id",
              (b["ids"][agency], f"OWN-{uuid.uuid4().int % 10**7}"))[0][0]


def _owner_session(b, pid):
    """Account, accesso e sessione tramite le route reali del portale."""
    from fastapi.testclient import TestClient

    import main

    admin = b["login"]("owner_a")
    contact = _q(b, "INSERT INTO contacts (agency_id, display_name, first_name, last_name, email, email_normalized) "
                    "VALUES (%s, 'Proprietario 7B', 'Proprietario', '7B', %s, %s) RETURNING id",
                 (b["ids"]["a"], f"p7b-{uuid.uuid4().hex[:8]}@esempio.invalid",
                  f"p7b-{uuid.uuid4().hex[:8]}@esempio.invalid"))[0][0]
    r = admin.post(f"{ADMIN}/accounts", json={"contact_id": contact})
    assert r.status_code == 201, r.text
    account = r.json()["id"]
    r = admin.post(f"{ADMIN}/access", json={"owner_account_id": account, "property_id": pid, "is_primary": True})
    assert r.status_code == 201, r.text
    access = r.json()["id"]
    r = admin.post(f"{ADMIN}/accounts/{account}/tokens", json={"token_type": "invitation", "expires_minutes": 30})
    assert r.status_code == 200, r.text
    owner = TestClient(main.app, base_url="https://testserver")
    r = owner.post(f"{PORTAL}/auth/token", json={"token": r.json()["token"]})
    assert r.status_code == 204, r.text
    assert owner.get(f"{PORTAL}/session").status_code == 200
    return owner, account, access


def _seed_interest(b, pid):
    buyer = _q(b, "INSERT INTO contacts (agency_id, display_name, first_name, last_name, phone, phone_normalized) "
                  "VALUES (%s, 'Interessato Riservato 7B', 'Interessato', 'Riservato', '3337776655', '393337776655') "
                  "RETURNING id", (b["ids"]["a"],))[0][0]
    lead = _q(b, "INSERT INTO leads (agency_id, contact_id, pipeline, stage, status) VALUES (%s, %s, 'buy', 'new', "
                 "'open') RETURNING id", (b["ids"]["a"], buyer))[0][0]
    _q(b, "INSERT INTO property_leads (property_id, lead_id, relation_type) VALUES (%s, %s, 'buyer_interest')",
       (pid, lead))


def test_report_and_pdf_only_for_authorized_properties(app):
    pid = _property(app)
    _seed_interest(app, pid)
    other = _property(app)
    other_agency = _property(app, "b")
    owner, _account, _access = _owner_session(app, pid)
    r = owner.get(f"{PORTAL}/properties/{pid}/report")
    assert r.status_code == 200 and r.json()["property"]["id"] == pid
    assert r.json()["interested_contacts"]["total"] == 1
    pdf = owner.get(f"{PORTAL}/properties/{pid}/report.pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    for target in (other, other_agency):
        assert owner.get(f"{PORTAL}/properties/{target}/report").status_code == 404
        assert owner.get(f"{PORTAL}/properties/{target}/report.pdf").status_code == 404
    for secret in RESERVED:
        assert secret not in r.text, secret
    if shutil.which("pdftotext"):
        text = subprocess.run(["pdftotext", "-", "-"], input=pdf.content, capture_output=True, check=True).stdout.decode()
        for secret in RESERVED:
            assert secret not in text, secret


def test_revocation_is_immediate_even_on_direct_pdf_download(app):
    pid = _property(app)
    owner, _account, access = _owner_session(app, pid)
    url = f"{PORTAL}/properties/{pid}/report.pdf"
    assert owner.get(url).status_code == 200
    r = app["login"]("owner_a").post(f"{ADMIN}/access/{access}/revoke")
    assert r.status_code == 200, r.text
    assert owner.get(url).status_code == 404                                  # stesso URL, subito dopo
    assert owner.get(f"{PORTAL}/properties/{pid}/report").status_code == 404
    assert owner.get(f"{PORTAL}/properties").json()["items"] == []


def test_no_access_to_drafts_visit_documents_valuations_or_internal_data(app):
    pid = _property(app)
    owner, _account, _access = _owner_session(app, pid)
    admin = app["login"]("owner_a")
    draft = admin.post(f"{ADMIN}/publications", json={"property_id": pid, "publication_type": "general_update",
                                                      "title": "Bozza riservata", "body": "Testo in bozza"})
    assert draft.status_code == 201, draft.text
    assert owner.get(f"{PORTAL}/publications/{draft.json()['id']}").status_code == 404
    assert "Bozza riservata" not in owner.get(f"{PORTAL}/properties/{pid}/timeline").text
    # le superfici operatore restano chiuse alla sessione del proprietario
    for path in (f"/api/visit-sheets?property_id={pid}", "/api/visit-sheets/1/pdf",
                 "/api/signature-requests?visit_sheet_id=1", f"/api/owner-reports/{pid}",
                 f"/api/property/properties/{pid}", f"/api/visit-booking-links?property_id={pid}"):
        assert owner.get(path).status_code in (401, 403, 404), path
    detail = owner.get(f"{PORTAL}/properties/{pid}").text
    for secret in ("88888", "NOTA-INTERNA-7B", "minimum_price", "internal_notes"):
        assert secret not in detail, secret
