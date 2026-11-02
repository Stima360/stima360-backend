"""ROADMAP VISITE - fase C: il foglio visita aperto o generato dall'APPUNTAMENTO, su
PostgreSQL VERO (banco 2C: cluster LOCALE usa-e-getta, migrazioni dal runner, app vera e
login vero, dati solo sintetici). Il testo del PDF si legge con `pdftotext` se presente.

    C  dall'appuntamento: elenco, generazione, versionamento, archiviazione, collegamento
    F  contenuto: agenzia, cliente, immobile, data, agente, spazio e stato della firma
    S  la firma: sempre «Da firmare», mai simulata come valida
    P  permessi, isolamento tra agenzie, riservatezza
"""
from __future__ import annotations

import hashlib
import itertools
import shutil
import subprocess
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from tests.test_blocco_2c_integrated_postgres import CLUSTER_VERIFIED, DSN, _q, app, banco  # noqa: F401
from tests.test_blocco_5a_visit_booking_postgres import _property

pytestmark = [
    pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL locale"),
    pytest.mark.skipif(not CLUSTER_VERIFIED, reason="cluster usa-e-getta non dichiarato verificato "
                       "(BLOCCO_2C_TEST_CLUSTER_VERIFIED=1)"),
]

API = "/api/visit-sheets"
_GIORNO = itertools.count(40)


def _testo(pdf: bytes) -> str:
    if not shutil.which("pdftotext"):
        pytest.skip("pdftotext non disponibile")
    out = subprocess.run(["pdftotext", "-layout", "-", "-"], input=pdf, capture_output=True, check=True).stdout.decode()
    return " ".join(out.split())


def _profilo(app):
    r = app["login"]("owner_a").put("/api/agency-profile", json={
        "display_name": "Alfa Immobiliare", "legal_name": "Alfa Prova S.r.l.", "legal_address": "Via Prova 1, Teramo",
        "vat_number": "01234567890", "phone": "+39 000 111222", "email": "info@alfa.example"})
    assert r.status_code == 200, r.text


def _visita(app, pid, *, agente="agent_a", agenzia="a", telefono="3331112233", email="visitatore.foglio@example.test"):
    """Una visita creata DALL'AGENDA (collegata a immobile, contatto e agente)."""
    ids = app["ids"]
    contatto = _q(app, "INSERT INTO contacts (agency_id, display_name, first_name, last_name, phone, email, notes, source) "
                       "VALUES (%s, 'Maria Visitatrice', 'Maria', 'Visitatrice', %s, %s, 'NOTA-RISERVATA-CONTATTO', "
                       "'FONTE-INTERNA') RETURNING id", (ids[agenzia], telefono, email))[0][0]
    inizio = (datetime.now(timezone.utc) + timedelta(days=next(_GIORNO))).replace(hour=10, minute=0, second=0, microsecond=0)
    r = app["login"]("owner_a" if agenzia == "a" else "owner_b").post("/api/appointments", json={
        "appointment_type": "buyer_visit", "assigned_user_id": ids[agente], "property_id": pid, "contact_id": contatto,
        "start_at": inizio.isoformat(), "end_at": (inizio + timedelta(minutes=45)).isoformat(),
        "client_request_id": str(uuid.uuid4())})
    assert r.status_code == 201, r.text
    return r.json()["id"], contatto


def _scarica(client, sheet):
    r = client.get(f"{API}/{sheet['id']}/pdf")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf" and r.headers["cache-control"] == "no-store"
    return r.content


# ---------------------------------------------------------------------------
# C - dall'appuntamento
# ---------------------------------------------------------------------------

def test_c_dalla_scheda_appuntamento_elenco_generazione_archivio_e_collegamento(app):
    pid = _property(app)
    aid, _c = _visita(app, pid)
    owner = app["login"]("owner_a")
    prima = owner.get(f"{API}/appointments/{aid}")
    assert prima.status_code == 200, prima.text
    assert prima.json() == {**prima.json(), "appointment_id": aid, "property_id": pid, "status": "scheduled",
                            "can_generate": True, "sheets": []}
    out = owner.post(f"{API}/appointments/{aid}").json()
    assert out["created"] is True and out["sheet"]["version"] == 1 and out["sheet"]["appointment_id"] == aid
    dopo = owner.get(f"{API}/appointments/{aid}").json()
    assert [s["id"] for s in dopo["sheets"]] == [out["sheet"]["id"]]
    # archiviato: stessi byte, impronta registrata, collegato a visita e immobile
    pdf = _scarica(owner, out["sheet"])
    assert hashlib.sha256(pdf).hexdigest() == out["sheet"]["sha256"]
    assert _q(app, "SELECT appointment_id, property_id, sha256, octet_length(pdf_bytes) > 0 FROM visit_sheets WHERE id = %s",
              (out["sheet"]["id"],)) == [(aid, pid, out["sheet"]["sha256"], True)]
    # secondo clic: stessa versione, nessuna copia
    again = owner.post(f"{API}/appointments/{aid}").json()
    assert again["created"] is False and again["sheet"]["id"] == out["sheet"]["id"]
    assert _q(app, "SELECT count(*) FROM visit_sheets WHERE appointment_id = %s", (aid,)) == [(1,)]
    # lo stesso foglio si vede anche dall'elenco dell'immobile
    per_immobile = owner.get(API, params={"property_id": pid}).json()["items"]
    assert [s["id"] for v in per_immobile if v["appointment_id"] == aid for s in v["sheets"]] == [out["sheet"]["id"]]


def test_c_versionamento_cambiano_i_dati_nasce_la_versione_successiva_e_la_prima_resta(app):
    pid = _property(app)
    aid, _c = _visita(app, pid)
    owner = app["login"]("owner_a")
    v1 = owner.post(f"{API}/appointments/{aid}").json()["sheet"]
    pdf1 = _scarica(owner, v1)
    _q(app, "UPDATE contacts SET phone = '3339990000' WHERE id = (SELECT contact_id FROM appointments WHERE id = %s)", (aid,))
    v2 = owner.post(f"{API}/appointments/{aid}").json()
    assert v2["created"] is True and v2["sheet"]["version"] == 2
    assert _scarica(owner, v1) == pdf1                                        # la v1 resta, byte per byte
    assert "3339990000" in _testo(_scarica(owner, v2["sheet"])) and "3339990000" not in _testo(pdf1)
    assert [s["version"] for s in owner.get(f"{API}/appointments/{aid}").json()["sheets"]] == [2, 1]


# ---------------------------------------------------------------------------
# F - contenuto
# ---------------------------------------------------------------------------

def test_f_il_foglio_contiene_agenzia_cliente_immobile_data_agente_e_firma(app):
    _profilo(app)
    pid = _property(app)
    aid, _c = _visita(app, pid)
    owner = app["login"]("owner_a")
    sheet = owner.post(f"{API}/appointments/{aid}").json()["sheet"]
    testo = _testo(_scarica(owner, sheet))
    inizio = _q(app, "SELECT start_at AT TIME ZONE 'Europe/Rome' FROM appointments WHERE id = %s", (aid,))[0][0]
    attesi = (
        "Foglio visita", "Alfa Immobiliare", "Alfa Prova S.r.l.", "Via Prova 1, Teramo", "01234567890",
        "+39 000 111222", "info@alfa.example",                                              # dati dell'agenzia
        "Maria Visitatrice", "3331112233", "visitatore.foglio@example.test",                # dati del cliente
        "Via Segreta Civico-S77, Comune Sintetico",                                          # immobile visitato
        inizio.strftime("%d/%m/%Y"), inizio.strftime("%H:%M"),                              # data della visita
        "Aldo Alfa",                                                                         # agente incaricato
        "Firma del visitatore", "Firma dell'agente", "Stato della firma: Da firmare",        # spazio e stato firma
        "non attesta che la visita sia avvenuta",
    )
    for atteso in attesi:
        assert atteso in testo, atteso
    for riservato in ("99999", "NOTA-RISERVATA", "NOTA-RISERVATA-CONTATTO", "FONTE-INTERNA"):
        assert riservato not in testo, riservato


def test_f_la_visita_svolta_lo_dice_solo_dopo_l_esito_registrato(app):
    pid = _property(app)
    aid, _c = _visita(app, pid)
    owner = app["login"]("owner_a")
    prenotata = owner.post(f"{API}/appointments/{aid}").json()["sheet"]
    assert prenotata["visit_state"] == "booked"
    _q(app, "SET session_replication_role = replica; UPDATE appointments SET status = 'completed', "
            "completed_at = start_at + interval '50 minutes' WHERE id = %s", (aid,))
    svolta = owner.post(f"{API}/appointments/{aid}").json()
    assert svolta["created"] is True and svolta["sheet"]["visit_state"] == "completed" and svolta["sheet"]["version"] == 2
    assert "Visita svolta" in _testo(_scarica(owner, svolta["sheet"]))
    assert "non attesta che la visita" not in _testo(_scarica(owner, svolta["sheet"]))
    assert "non attesta che la visita" in _testo(_scarica(owner, prenotata))


# ---------------------------------------------------------------------------
# S - la firma: sempre «Da firmare»
# ---------------------------------------------------------------------------

def test_s_mai_una_firma_valida_ne_un_documento_sottoscritto(app):
    pid = _property(app)
    aid, _c = _visita(app, pid)
    owner = app["login"]("owner_a")
    sheet = owner.post(f"{API}/appointments/{aid}").json()["sheet"]
    assert (sheet["signed"], sheet["signature_state"], sheet["signature_label"]) == (False, "unsigned", "Da firmare")
    testo = _testo(_scarica(owner, sheet))
    assert "Stato della firma: Da firmare" in testo
    for vietato in ("firmato digitalmente", "firmato da", "Firma valida", "sottoscritto il"):
        assert vietato not in testo, vietato
    snap = _q(app, "SELECT data_snapshot->'signature' FROM visit_sheets WHERE id = %s", (sheet["id"],))[0][0]
    assert snap == {"state": "unsigned", "label": "Da firmare"}


# ---------------------------------------------------------------------------
# P - permessi e agenzie
# ---------------------------------------------------------------------------

def test_p_permessi_agente_proprio_collega_altra_agenzia_e_visite_non_valide(app):
    pid = _property(app)
    aid, _c = _visita(app, pid, agente="agent_a")
    owner, admin, agent, agent2, owner_b = (app["login"](k) for k in
                                            ("owner_a", "admin_a", "agent_a", "agent2_a", "owner_b"))
    assert agent.get(f"{API}/appointments/{aid}").status_code == 200
    assert agent.post(f"{API}/appointments/{aid}").status_code == 200
    assert admin.get(f"{API}/appointments/{aid}").status_code == 200
    for estraneo in (agent2, owner_b):
        assert estraneo.get(f"{API}/appointments/{aid}").status_code == 404
        assert estraneo.post(f"{API}/appointments/{aid}").status_code == 404
    sheet = owner.get(f"{API}/appointments/{aid}").json()["sheets"][0]
    assert agent2.get(f"{API}/{sheet['id']}/pdf").status_code == 404
    assert owner_b.get(f"{API}/{sheet['id']}/pdf").status_code == 404
    # non e' una visita a un immobile: niente scheda foglio
    ids = app["ids"]
    inizio = (datetime.now(timezone.utc) + timedelta(days=next(_GIORNO))).replace(hour=15, minute=0, second=0, microsecond=0)
    altro = owner.post("/api/appointments", json={
        "appointment_type": "seller_meeting", "assigned_user_id": ids["agent_a"], "start_at": inizio.isoformat(),
        "end_at": (inizio + timedelta(minutes=30)).isoformat(), "client_request_id": str(uuid.uuid4())}).json()["id"]
    assert owner.get(f"{API}/appointments/{altro}").status_code == 404
    assert owner.get(f"{API}/appointments/999999999").status_code == 404
    from fastapi.testclient import TestClient

    import main
    assert TestClient(main.app, base_url="https://testserver").get(f"{API}/appointments/{aid}").status_code in (401, 403)


def test_p_una_visita_annullata_non_genera_il_foglio(app):
    pid = _property(app)
    aid, _c = _visita(app, pid)
    owner = app["login"]("owner_a")
    versione = owner.get(f"/api/appointments/{aid}").json()["appointment"]["version"]
    assert owner.post(f"/api/appointments/{aid}/cancel", json={"version": versione, "kind": "agency",
                                                              "reason": "Immobile non accessibile"}).status_code == 200
    elenco = owner.get(f"{API}/appointments/{aid}").json()
    assert elenco["can_generate"] is False and elenco["status"] == "cancelled"
    r = owner.post(f"{API}/appointments/{aid}")
    assert r.status_code == 400 and r.json()["detail"]["code"] == "VISIT_NOT_SHEETABLE"
