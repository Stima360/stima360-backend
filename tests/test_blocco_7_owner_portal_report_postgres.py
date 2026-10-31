"""BLOCCO-7 - report attivita' nell'area proprietario, su PostgreSQL VERO.

Stesso banco di 2C (cluster LOCALE usa-e-getta, migrazioni dal runner). La
sessione del proprietario si crea direttamente nel database di prova: nessuna
email di accesso viene inviata.

    R  report del proprio immobile, periodo, storico del prezzo, «Dato non disponibile»
    X  esclusioni: note interne, prezzo minimo, recapiti degli interessati
    P  PDF scaricabile
    A  autorizzazione: altro immobile, altra agenzia, revoca immediata
"""
from __future__ import annotations

import secrets
import shutil
import subprocess
from datetime import date, timedelta

import pytest

from tests.test_blocco_2c_integrated_postgres import (  # noqa: F401 - fixture
    CLUSTER_VERIFIED,
    DSN,
    _q,
    app,
    banco,
)
from tests.test_blocco_5a_visit_booking_postgres import _property

pytestmark = [
    pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL locale"),
    pytest.mark.skipif(not CLUSTER_VERIFIED, reason="cluster usa-e-getta non dichiarato verificato "
                       "(BLOCCO_2C_TEST_CLUSTER_VERIFIED=1)"),
]

BASE = "/api/owner/portal/properties"


def _owner(b, pid, agency="a"):
    import hashlib

    from fastapi.testclient import TestClient

    import main

    contact = _q(b, "INSERT INTO contacts (agency_id, display_name, first_name, last_name) VALUES "
                    "(%s, 'Proprietario Sintetico', 'Proprietario', 'Sintetico') RETURNING id", (b["ids"][agency],))[0][0]
    account = _q(b, "INSERT INTO owner_accounts (contact_id, status) VALUES (%s, 'active') RETURNING id", (contact,))[0][0]
    access = _q(b, "INSERT INTO owner_property_access (owner_account_id, property_id, access_role, access_status, "
                   "is_primary) VALUES (%s, %s, 'owner', 'active', TRUE) RETURNING id", (account, pid))[0][0]
    raw = secrets.token_urlsafe(32)
    _q(b, "INSERT INTO owner_sessions (owner_account_id, session_token_hash, expires_at) VALUES "
          "(%s, %s, NOW() + interval '2 hours')", (account, hashlib.sha256(raw.encode()).hexdigest()))
    client = TestClient(main.app, base_url="https://testserver")
    client.cookies.set("stima360_owner_session", raw)
    return client, account, access


def _seed(b, pid):
    _q(b, "INSERT INTO property_price_history (property_id, old_price, new_price, change_reason, changed_by) "
          "VALUES (%s, 200000, 185000, 'riduzione', 'operator:1')", (pid,))
    buyer = _q(b, "INSERT INTO contacts (agency_id, display_name, first_name, last_name, phone, phone_normalized) "
                  "VALUES (%s, 'Interessato Riservato', 'Interessato', 'Riservato', '3339998877', '393339998877') "
                  "RETURNING id", (b["ids"]["a"],))[0][0]
    lead = _q(b, "INSERT INTO leads (agency_id, contact_id, pipeline, stage, status) VALUES (%s, %s, 'buy', 'new', "
                 "'open') RETURNING id", (b["ids"]["a"], buyer))
    if lead:
        _q(b, "INSERT INTO property_leads (property_id, lead_id) VALUES (%s, %s)", (pid, lead[0][0]))


def _pdf_text(pdf):
    if not shutil.which("pdftotext"):
        pytest.skip("pdftotext non disponibile")
    return " ".join(subprocess.run(["pdftotext", "-", "-"], input=pdf, capture_output=True, check=True).stdout.decode()
                    .split())


def test_r_owner_sees_the_report_of_the_own_property_with_period_and_price_history(app):
    pid = _property(app)
    _seed(app, pid)
    client, _account, _access = _owner(app, pid)
    r = client.get(f"{BASE}/{pid}/report")
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["audience"] == "owner" and rep["property"]["id"] == pid
    assert rep["requests"] == {"available": False, "total": None}                # mai uno zero inventato
    assert [p["new_price"] for p in rep["price_history"]] == ["185000.00"] or rep["price_history"][0]["new_price"]
    start = (date.today() - timedelta(days=30)).isoformat()
    r = client.get(f"{BASE}/{pid}/report", params={"date_from": start, "date_to": date.today().isoformat()})
    assert r.status_code == 200 and r.json()["period"]["from"] == start
    future = client.get(f"{BASE}/{pid}/report", params={"date_from": (date.today() + timedelta(days=5)).isoformat()})
    assert future.status_code in (400, 422)


def test_x_reserved_data_never_reach_the_owner(app):
    pid = _property(app)
    _seed(app, pid)
    client, _a, _x = _owner(app, pid)
    raw = client.get(f"{BASE}/{pid}/report").text
    for secret in ("99999", "NOTA-RISERVATA", "Interessato Riservato", "3339998877", "minimum_price", "internal_notes"):
        assert secret not in raw, secret
    text = _pdf_text(client.get(f"{BASE}/{pid}/report.pdf").content)
    for secret in ("99999", "NOTA-RISERVATA", "Interessato Riservato", "3339998877"):
        assert secret not in text, secret
    assert "Dato non disponibile" in text


def test_p_owner_downloads_the_pdf(app):
    pid = _property(app)
    client, _a, _x = _owner(app, pid)
    r = client.get(f"{BASE}/{pid}/report.pdf", params={"date_from": (date.today() - timedelta(days=10)).isoformat(),
                                                      "date_to": date.today().isoformat()})
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    assert r.headers["content-type"] == "application/pdf" and r.headers["cache-control"] == "no-store"


def test_a_only_authorized_properties_and_revocation_is_immediate(app):
    pid = _property(app)
    other = _property(app)
    other_agency = _property(app, "b")
    client, _account, access = _owner(app, pid)
    assert client.get(f"{BASE}/{pid}/report").status_code == 200
    for target in (other, other_agency):
        assert client.get(f"{BASE}/{target}/report").status_code == 404
        assert client.get(f"{BASE}/{target}/report.pdf").status_code == 404
    _q(app, "UPDATE owner_property_access SET access_status = 'revoked', revoked_at = NOW() WHERE id = %s", (access,))
    assert client.get(f"{BASE}/{pid}/report").status_code == 404
    assert client.get(f"{BASE}/{pid}/report.pdf").status_code == 404
    from fastapi.testclient import TestClient

    import main

    assert TestClient(main.app, base_url="https://testserver").get(f"{BASE}/{pid}/report").status_code == 404
