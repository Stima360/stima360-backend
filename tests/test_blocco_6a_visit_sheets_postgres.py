"""BLOCCO-6A - foglio visita su PostgreSQL VERO.

Stesso banco di 2C/5A (cluster LOCALE usa-e-getta, migrazioni dal runner,
compresa la 121). Dati solo sintetici. Il testo del PDF si legge con
`pdftotext` (poppler) se presente.

    G  generazione: dati, versione del modello, autore, stessi dati = stessa versione
    S  prenotata / svolta / mai firmata
    P  permessi e agenzie, download protetto, integrita'
    I  immutabilita' e guardie del database, concorrenza
"""
from __future__ import annotations

import hashlib
import itertools
import shutil
import subprocess
import threading
from datetime import datetime, timedelta, timezone

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

API = "/api/visit-sheets"
_ORARI = itertools.count(1)


def _contact(b, agency="a", name="Visitatore Sintetico", agent=None):
    return _q(b, "INSERT INTO contacts (agency_id, display_name, first_name, last_name, assigned_agent_id) "
                 "VALUES (%s, %s, %s, 'Prova', %s) RETURNING id", (b["ids"][agency], name, name, agent))[0][0]


def _visit(b, pid, *, agency="a", agent="agent_a", status="scheduled", days=3):
    start = (datetime.now(timezone.utc) + timedelta(days=days, hours=2 * next(_ORARI))).replace(
        minute=0, second=0, microsecond=0)
    contact = _contact(b, agency)
    return _q(b, "INSERT INTO appointments (agency_id, assigned_user_id, appointment_type, status, start_at, end_at, "
                 "contact_id, property_id, source, created_by_user_id) VALUES (%s, %s, 'buyer_visit', %s, %s, %s, %s, "
                 "%s, 'crm_manual', %s) RETURNING id",
              (b["ids"][agency], b["ids"][agent], status, start, start + timedelta(minutes=45), contact, pid,
               b["ids"][agent]))[0][0]


def _complete(b, aid):
    _q(b, "SET session_replication_role = replica; UPDATE appointments SET status = 'completed', "
          "completed_at = start_at + interval '50 minutes' WHERE id = %s", (aid,))


def _text(pdf: bytes) -> str:
    if not shutil.which("pdftotext"):
        pytest.skip("pdftotext non disponibile")
    return subprocess.run(["pdftotext", "-layout", "-", "-"], input=pdf, capture_output=True, check=True).stdout.decode()


def _download(client, sheet_id):
    r = client.get(f"{API}/{sheet_id}/pdf")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf" and r.headers["cache-control"] == "no-store"
    return r.content


def test_g_a_booked_visit_sheet_with_data_template_and_author(app):
    pid = _property(app)
    aid = _visit(app, pid)
    owner = app["login"]("owner_a")
    r = owner.post(f"{API}/appointments/{aid}")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["created"] is True and out["sheet"]["version"] == 1 and out["sheet"]["visit_state"] == "booked"
    assert out["sheet"]["template_version"] == "foglio_visita_bozza_v1" and out["sheet"]["signed"] is False
    pdf = _download(owner, out["sheet"]["id"])
    assert pdf.startswith(b"%PDF") and hashlib.sha256(pdf).hexdigest() == out["sheet"]["sha256"]
    text = _text(pdf)
    for expected in ("Foglio visita", "Via Segreta 77, Comune Sintetico", "Esclusiva", "Visitatore Sintetico",
                     "non attesta che la visita", "Firma del visitatore", "Bozza di modello",
                     "non contiene clausole provvigionali"):
        assert expected in " ".join(text.split()), expected
    for reserved in ("99999", "NOTA-RISERVATA", "provvigione del", "si impegna", "penale"):
        assert reserved not in text, reserved
    row = _q(app, "SELECT generated_by_user_id, data_snapshot->'visit'->>'visitor_name', template_version "
                  "FROM visit_sheets WHERE id = %s", (out["sheet"]["id"],))[0]
    assert row == (app["ids"]["owner_a"], "Visitatore Sintetico", "foglio_visita_bozza_v1")
    again = owner.post(f"{API}/appointments/{aid}").json()
    assert again["created"] is False and again["sheet"]["id"] == out["sheet"]["id"]


def test_s_completed_only_after_the_recorded_outcome_and_never_signed(app):
    pid = _property(app)
    aid = _visit(app, pid)
    owner = app["login"]("owner_a")
    first = owner.post(f"{API}/appointments/{aid}").json()["sheet"]
    _complete(app, aid)
    second = owner.post(f"{API}/appointments/{aid}").json()
    assert second["created"] is True and second["sheet"]["version"] == 2
    assert second["sheet"]["visit_state"] == "completed" and first["visit_state"] == "booked"
    text = " ".join(_text(_download(owner, second["sheet"]["id"])).split())
    assert "Visita svolta: esito registrato" in text and "non attesta" not in text
    assert "non è un documento sottoscritto" in text
    listed = owner.get(API, params={"property_id": pid}).json()["items"]
    (visit,) = [v for v in listed if v["appointment_id"] == aid]
    assert [s["version"] for s in visit["sheets"]] == [2, 1] and all(s["signed"] is False for s in visit["sheets"])


@pytest.mark.parametrize("status", ["requested", "cancelled"])
def test_s_no_sheet_for_requests_or_cancelled_visits(app, status):
    pid = _property(app)
    aid = _visit(app, pid, status="scheduled")
    if status == "cancelled":
        _q(app, "SET session_replication_role = replica; UPDATE appointments SET status = 'cancelled', "
                "cancelled_at = NOW() WHERE id = %s", (aid,))
    else:
        _q(app, "SET session_replication_role = replica; UPDATE appointments SET status = 'requested' WHERE id = %s",
           (aid,))
    r = app["login"]("owner_a").post(f"{API}/appointments/{aid}")
    assert r.status_code == 400 and r.json()["detail"]["code"] == "VISIT_NOT_SHEETABLE"


def test_p_permissions_and_agencies(app):
    pid = _property(app)
    aid = _visit(app, pid)
    sheet = app["login"]("agent_a").post(f"{API}/appointments/{aid}").json()["sheet"]   # agente della visita
    for who in ("agent2_a", "owner_b"):
        client = app["login"](who)
        assert client.post(f"{API}/appointments/{aid}").status_code == 404, who
        assert client.get(f"{API}/{sheet['id']}/pdf").status_code == 404, who
    assert app["login"]("owner_b").get(API, params={"property_id": pid}).status_code == 404
    agent2_list = app["login"]("agent2_a").get(API, params={"property_id": pid}).json()["items"]
    assert all(v["appointment_id"] != aid for v in agent2_list)
    assert app["login"]("owner_a").get(f"{API}/{sheet['id']}/pdf").status_code == 200


def test_i_immutable_guarded_and_single_under_concurrency(app):
    pid = _property(app)
    aid = _visit(app, pid)
    owner_clients = [app["login"]("owner_a") for _ in range(3)]
    barrier = threading.Barrier(3)
    results = []

    def go(c):
        barrier.wait(5)
        results.append(c.post(f"{API}/appointments/{aid}"))

    threads = [threading.Thread(target=go, args=(c,)) for c in owner_clients]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert [r.status_code for r in results] == [200] * 3
    assert sum(r.json()["created"] for r in results) == 1
    assert _q(app, "SELECT count(*) FROM visit_sheets WHERE appointment_id = %s", (aid,)) == [(1,)]
    sid = results[0].json()["sheet"]["id"]
    with pytest.raises(Exception, match="immutable"):
        _q(app, "UPDATE visit_sheets SET visit_state = 'completed' WHERE id = %s", (sid,))
    with pytest.raises(Exception, match="immutable"):
        _q(app, "DELETE FROM visit_sheets WHERE id = %s", (sid,))
    with pytest.raises(Exception, match="no recorded outcome"):
        _q(app, "INSERT INTO visit_sheets (agency_id, appointment_id, property_id, sheet_version, template_version, "
                "visit_state, data_snapshot, pdf_bytes, sha256, generated_by_user_id) VALUES (%s, %s, %s, 9, 'x', "
                "'completed', '{}', 'x', %s, %s)", (app["ids"]["a"], aid, pid, "0" * 64, app["ids"]["owner_a"]))
