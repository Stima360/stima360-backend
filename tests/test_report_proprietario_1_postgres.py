"""REPORT-PROPRIETARIO-1 - report attivita' per il proprietario su PostgreSQL VERO.

Stesso banco del collaudo BLOCCO-2C (`tests/test_blocco_2c_integrated_postgres.py`):
schema completo ricostruito come il TEST su un cluster LOCALE usa-e-getta,
app vera (`main.app`) e login vero. Gate: `P29_TEST_DSN` locale e
`BLOCCO_2C_TEST_CLUSTER_VERIFIED=1`.

I dati di contorno (contatti, richieste, abbinamenti, proposte, visite) si
scrivono con SQL nel database usa-e-getta; il report si legge SOLO dalle
route vere. Copre:

    R  conteggi: richieste ricevute, visite effettuate (calendario + storiche),
       visite in programma, proposte per stato (mai le bozze), mese per mese
    P  prezzo richiesto e stato commerciale nel periodo; periodo predefinito
       e periodi non validi
    D  nessun dato interno nel report (note, prezzo minimo, acquirenti, importi)
    S  permessi e separazione tra agenzie; nessuna scrittura
    F  PDF con logo agenzia e indicazioni dell'agente
"""
from __future__ import annotations

import io
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from tests.test_blocco_2c_integrated_postgres import (  # noqa: F401 - fixture
    CLUSTER_VERIFIED,
    DSN,
    _png,
    _q,
    app,
    banco,
)

pytestmark = [
    pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL locale"),
    pytest.mark.skipif(not CLUSTER_VERIFIED, reason="cluster usa-e-getta non dichiarato verificato "
                       "(BLOCCO_2C_TEST_CLUSTER_VERIFIED=1)"),
]

API = "/api/owner-reports"


def _ts(days_ago: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days_ago)


def _contact(b, agency_id, name):
    return _q(b, "INSERT INTO contacts (agency_id, display_name, first_name, last_name) VALUES (%s, %s, %s, 'Prova') "
                 "RETURNING id", (agency_id, name, name))[0][0]


def _inquiry(b, agency_id, property_id, days_ago):
    contact = _contact(b, agency_id, f"Interessato{uuid.uuid4().hex[:4]}")
    lead = _q(b, "INSERT INTO leads (agency_id, contact_id, pipeline, stage, status) VALUES (%s, %s, 'buy', 'new', 'open') "
                 "RETURNING id", (agency_id, contact))[0][0]
    _q(b, "INSERT INTO property_leads (property_id, lead_id, relation_type, created_at) VALUES (%s, %s, 'buyer_interest', %s)",
       (property_id, lead, _ts(days_ago)))


def _legacy_visit(b, property_id, days_ago, status="completed", contact_id=None):
    _q(b, "INSERT INTO property_visits (property_id, contact_id, scheduled_at, status) VALUES (%s, %s, %s, %s)",
       (property_id, contact_id, _ts(days_ago), status))


def _operator(b, agency_id):
    """Un operatore sintetico per scenario: il calendario vieta appuntamenti
    sovrapposti dello stesso operatore, e il banco e' condiviso fra i test."""
    email = f"b2c.report.{uuid.uuid4().hex[:8]}@example.test"
    uid = _q(b, "INSERT INTO operator_users (email, email_normalized, password_hash, first_name, status) "
                "VALUES (%s, %s, 'pbkdf2_sha256$1$x$y', 'Calendario', 'active') RETURNING id", (email, email))[0][0]
    _q(b, "INSERT INTO agency_memberships (agency_id, operator_user_id, role, status) VALUES (%s, %s, 'agent', 'active')",
       (agency_id, uid))
    return uid


def _visit(b, agency_id, agent_id, property_id, contact_id, days_from_now, status):
    """Un appuntamento `buyer_visit` (giorni negativi = passato)."""
    start = datetime.now(timezone.utc) + timedelta(days=days_from_now)
    stamps = {"completed": "completed_at", "confirmed": "confirmed_at"}
    column = stamps.get(status)
    _q(b, "INSERT INTO appointments (agency_id, assigned_user_id, appointment_type, status, start_at, end_at, "
          "contact_id, property_id" + (f", {column}" if column else "") + ") VALUES "
          "(%s, %s, 'buyer_visit', %s, %s, %s, %s, %s" + (", %s" if column else "") + ")",
       (agency_id, agent_id, status, start, start + timedelta(hours=1), contact_id, property_id)
       + ((start + timedelta(hours=1),) if column else ()))


def _buy_request(b, agency_id):
    contact = _contact(b, agency_id, f"Acquirente{uuid.uuid4().hex[:4]}")
    return _q(b, "INSERT INTO buy_requests (agency_id, contact_id, title) VALUES (%s, %s, 'Richiesta prova') RETURNING id",
              (agency_id, contact))[0][0]


def _proposal(b, agency_id, property_id, status, days_ago, amount="190000"):
    br = _buy_request(b, agency_id)
    match = _q(b, "INSERT INTO matches (buy_request_id, property_id, compatibility_status, score_total, match_class, algorithm_version) "
                  "VALUES (%s, %s, 'compatible', 80, 'strong', 'prova') RETURNING id", (br, property_id))[0][0]
    _q(b, "INSERT INTO property_proposals (match_id, amount, expires_at, status, idempotency_key, created_by, created_at) "
          "VALUES (%s, %s, NOW() + INTERVAL '10 days', %s, %s, 'prova', %s)",
       (match, amount, status, str(uuid.uuid4()), _ts(days_ago)))


@pytest.fixture
def mondo(app):
    """Un immobile senza incarico (l'incarico nasce solo da un'acquisizione: il
    periodo che parte dall'incarico e' provato in `test_report_proprietario_1.py`),
    con attivita' sintetica e dati interni."""
    b, ids = app, app["ids"]
    owner = app["login"]("owner_a")
    r = owner.post("/api/property/properties", json={
        "title": "Report proprietario prova", "city": "Comune Sintetico", "address": "Via Report", "civic_number": "7",
        "asking_price": "250000", "minimum_price": "210000", "commercial_status": "active",
        "internal_notes": "NOTA-INTERNA-RISERVATA"})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    r = owner.patch(f"/api/property/properties/{pid}", json={"asking_price": "239000", "commercial_status": "under_offer",
                                                              "assigned_agent_id": ids["agent_a"]})
    assert r.status_code == 200, r.text
    for days in (50, 20, 3):
        _inquiry(b, ids["a"], pid, days)
    _inquiry(b, ids["a"], pid, 200)                    # fuori dal periodo predefinito (90 giorni)
    buyer = _contact(b, ids["a"], "Visitatore")
    agenda = _operator(b, ids["a"])
    _legacy_visit(b, pid, 40)
    _legacy_visit(b, pid, 10, contact_id=buyer)       # la stessa visita registrata anche in agenda...
    _visit(b, ids["a"], agenda, pid, buyer, -10, "completed")   # ...conta una volta sola
    _legacy_visit(b, pid, 5, status="cancelled")      # non svolta
    _visit(b, ids["a"], agenda, pid, buyer, 10, "scheduled")    # in programma nei 30 giorni
    _visit(b, ids["a"], agenda, pid, buyer, 12, "confirmed")    # in programma nei 30 giorni
    _visit(b, ids["a"], agenda, pid, buyer, 45, "scheduled")    # oltre l'intervallo futuro
    _visit(b, ids["a"], agenda, pid, buyer, 6, "requested")     # richiesta non ancora fissata
    _proposal(b, ids["a"], pid, "submitted", 4, amount="231111")
    _proposal(b, ids["a"], pid, "rejected", 200, amount="222222")         # fuori periodo: conta (stato attuale)
    _proposal(b, ids["a"], pid, "draft", 2, amount="233333")     # bozza interna: mai contata
    return {"pid": pid, "owner": owner}


def test_r_counts_and_monthly_trend_over_the_default_period(app, mondo):
    r = mondo["owner"].get(f"{API}/{mondo['pid']}")
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["period"]["to"] == date.today().isoformat()
    assert rep["period"]["from"] == (date.today() - timedelta(days=90)).isoformat()
    assert rep["mandate"] == {"exists": False, "type": None, "start": None, "end": None, "state": None,
                              "state_label": None}
    assert rep["interested_contacts"]["total"] == 3
    assert rep["requests"] == {"available": False, "total": None}            # mai zero al posto del dato
    assert rep["visits"]["completed"] == 2                                     # 40 giorni fa + quella di 10 (una)
    up = rep["visits"]["upcoming"]
    assert up["count"] == 2 and up["from"] == date.today().isoformat()
    assert up["to"] == (date.today() + timedelta(days=30)).isoformat()
    assert rep["proposals"]["scope"] == "current" and rep["proposals"]["total"] == 2
    assert {p["status"]: p["count"] for p in rep["proposals"]["by_status"]} == {"submitted": 1, "rejected": 1}
    assert sum(m["interested_contacts"] for m in rep["monthly"]) == 3
    assert sum(m["visits"] for m in rep["monthly"]) == 2
    assert rep["property"]["asking_price"] == "239000.00"
    assert rep["property"]["commercial_status_label"] == "Sotto offerta"


def test_p_price_and_status_history_within_the_period(app, mondo):
    rep = mondo["owner"].get(f"{API}/{mondo['pid']}").json()
    assert [(p["old_price"], p["new_price"]) for p in rep["price_history"]] == [
        (None, "250000.00"), ("250000.00", "239000.00")]
    assert [(s["old_label"], s["new_label"]) for s in rep["status_history"]][-1] == ("Attivo", "Sotto offerta")


def test_p_explicit_and_invalid_periods(app, mondo):
    c, pid = mondo["owner"], mondo["pid"]
    today = date.today()
    rep = c.get(f"{API}/{pid}", params={"date_from": (today - timedelta(days=7)).isoformat()}).json()
    assert rep["interested_contacts"]["total"] == 1 and rep["visits"]["completed"] == 0
    rep = c.get(f"{API}/{pid}", params={"date_from": (today - timedelta(days=300)).isoformat()}).json()
    assert rep["interested_contacts"]["total"] == 4
    for params in ({"date_from": today.isoformat(), "date_to": (today - timedelta(days=1)).isoformat()},
                   {"date_to": (today + timedelta(days=1)).isoformat()},
                   {"date_from": (today - timedelta(days=900)).isoformat()}):
        r = c.get(f"{API}/{pid}", params=params)
        assert r.status_code == 422 and r.json()["detail"]["code"] == "PERIOD_INVALID", (params, r.text)


def test_d_no_internal_data_leaks_into_the_report(app, mondo):
    text = mondo["owner"].get(f"{API}/{mondo['pid']}").text
    for secret in ("NOTA-INTERNA-RISERVATA", "210000", "231111", "222222", "233333", "Interessato", "Acquirente",
                   "Visitatore"):
        assert secret not in text, secret


def test_s_permissions_and_agency_isolation(app, mondo):
    pid = mondo["pid"]
    assert app["login"]("agent_a").get(f"{API}/{pid}").status_code == 200        # agente assegnato
    assert app["login"]("admin_a").get(f"{API}/{pid}").status_code == 200
    r = app["login"]("agent2_a").get(f"{API}/{pid}")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "NOT_ALLOWED"
    other = app["login"]("owner_b")
    for r in (other.get(f"{API}/{pid}"), other.post(f"{API}/{pid}/pdf", json={})):
        assert r.status_code == 404 and b"%PDF" not in r.content
    assert other.post(f"{API}/{pid}/pdf", json={"agency_id": app["ids"]["a"]}).status_code == 422


def test_s_the_report_writes_nothing(app, mondo):
    tables = ("properties", "property_price_history", "property_status_history", "activities", "leads",
              "property_visits", "property_proposals")
    before = {t: _q(app, f"SELECT md5(coalesce(string_agg(row_to_json(t)::text, '|' ORDER BY id), '')) FROM {t} t")[0][0]
              for t in tables}
    c = mondo["owner"]
    c.get(f"{API}/{mondo['pid']}")
    c.post(f"{API}/{mondo['pid']}/pdf", json={"agent_notes": "Indicazione"})
    after = {t: _q(app, f"SELECT md5(coalesce(string_agg(row_to_json(t)::text, '|' ORDER BY id), '')) FROM {t} t")[0][0]
             for t in tables}
    assert after == before


def test_f_pdf_has_the_agency_logo_and_the_agent_notes(app, mondo, monkeypatch):
    from reportlab.platypus import SimpleDocTemplate

    c = mondo["owner"]
    assert c.put("/api/agency-profile", json={"display_name": "Alfa Report"}).status_code == 200
    assert c.put("/api/agency-profile/logo", content=_png((10, 90, 60)),
                 headers={"Content-Type": "image/png"}).status_code == 200
    seen = []
    original = SimpleDocTemplate.build

    def walk(item):
        if isinstance(item, (list, tuple)):
            for x in item:
                walk(x)
        elif hasattr(item, "getPlainText"):
            seen.append(item.getPlainText())
        elif hasattr(item, "_cellvalues"):
            walk(item._cellvalues)
        elif hasattr(item, "_content"):
            walk(item._content)

    def build(self, flowables, *args, **kwargs):
        walk(list(flowables))
        return original(self, flowables, *args, **kwargs)

    monkeypatch.setattr(SimpleDocTemplate, "build", build)
    r = c.post(f"{API}/{mondo['pid']}/pdf", json={"agent_notes": "Consiglio di mantenere il prezzo <b>fino a fine mese</b> & più visite."})
    assert r.status_code == 200, r.text
    assert r.content.startswith(b"%PDF-") and b"/Subtype /Image" in r.content
    assert r.headers["content-type"] == "application/pdf" and r.headers["cache-control"] == "private, no-store"
    assert r.headers["content-disposition"].startswith(f'attachment; filename="report-proprietario-immobile-{mondo["pid"]}-')
    text = "\n".join(seen)
    assert "Alfa Report" in text and "Report attività per il proprietario" in text
    assert "Consiglio di mantenere il prezzo <b>fino a fine mese</b> & più visite." in text
    assert "NOTA-INTERNA-RISERVATA" not in text and "231111" not in text
    assert "Richieste ricevute\nDato non disponibile" in text and "Contatti interessati registrati\n3" in text
    assert "non rappresentano l'andamento del valore di mercato" in text
