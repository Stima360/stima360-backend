"""ROADMAP VISITE - fase E: il PERCORSO COMPLETO della visita, su PostgreSQL VERO (banco 2C:
cluster LOCALE usa-e-getta, migrazioni dal runner, app vera e login vero, dati solo sintetici).

    Prenotazione -> Agenda -> Esito -> Foglio visita PDF -> Aggiornamento al proprietario -> Portale

Un solo scenario, scritto come lo vive il consulente: l'agente assegnato alla visita fa tutto dal
CRM, senza inserire due volte gli stessi dati. Nessun messaggio reale parte: l'unica email che il
CRM accoda (la conferma, spenta salvo `APPOINTMENT_CONFIRMATIONS=1`) non viene spedita da nessuno
perche' nessun cron gira.
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from tests.test_blocco_2c_integrated_postgres import CLUSTER_VERIFIED, DSN, _q, app, banco  # noqa: F401
from tests.test_blocco_5a_visit_booking_postgres import (  # noqa: F401 - fixture e aiuti
    PUB, _appointment, _property, _slots, _token, vb)
from tests.test_blocco_7_owner_portal_report_postgres import BASE, _owner
from tests.test_visite_esito_postgres import SEGRETO_INTERNO

pytestmark = [
    pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL locale"),
    pytest.mark.skipif(not CLUSTER_VERIFIED, reason="cluster usa-e-getta non dichiarato verificato "
                       "(BLOCCO_2C_TEST_CLUSTER_VERIFIED=1)"),
]

EMAIL = "acquirente.percorso@example.test"
TELEFONO = "3335550101"
PERIODO = {"date_from": (date.today() - timedelta(days=200)).isoformat(), "date_to": date.today().isoformat()}
TESTO = "La visita è stata effettuata e ha suscitato interesse per l'immobile. Prossimo passo: una seconda visita."


def _pdf_testo(pdf: bytes) -> str:
    if not shutil.which("pdftotext"):
        pytest.skip("pdftotext non disponibile")
    return " ".join(subprocess.run(["pdftotext", "-layout", "-", "-"], input=pdf, capture_output=True,
                                   check=True).stdout.decode().split())


def test_percorso_completo_dalla_prenotazione_al_portale_del_proprietario(vb, monkeypatch):
    monkeypatch.setenv("APPOINTMENT_CONFIRMATIONS", "1")
    monkeypatch.setenv("APPOINTMENT_NOTIFICATIONS_ALLOWED_RECIPIENTS", EMAIL)
    ids = vb["ids"]
    owner, agent, agent2, owner_b = (vb["login"](k) for k in ("owner_a", "agent_a", "agent2_a", "owner_b"))
    _q(vb, "UPDATE agencies SET name = 'Agenzia Percorso' WHERE id = %s", (ids["a"],))
    assert owner.put("/api/agency-profile", json={
        "display_name": "Percorso Immobiliare", "phone": "+39 085 000111", "email": "info@percorso.example"}).status_code == 200
    pid = _property(vb)
    portale, account, _grant = _owner(vb, pid)

    # 1. PRENOTAZIONE collegata all'Agenda: appuntamento, immobile, contatto, agente, data, stato
    token = _token(vb, pid)
    start = _slots(vb, token)[0]["start_at"]
    chiave = str(uuid.uuid4())
    prenota = {"name": "Maria Percorso", "phone": TELEFONO, "email": EMAIL, "start_at": start, "key": chiave,
               "privacy_accepted": True}
    assert vb["public"].post(f"{PUB}/{token}/book", json=prenota).status_code == 200
    assert vb["public"].post(f"{PUB}/{token}/book", json=prenota).status_code == 200            # doppio clic
    ((aid, tipo, stato, prop, contatto, agente, fonte, *_),) = _appointment(vb, start, pid)
    assert (tipo, stato, prop, agente, fonte) == ("buyer_visit", "scheduled", pid, ids["agent_a"], "booking_link")
    assert contatto is not None
    ((vid,),) = _q(vb, "SELECT id FROM property_visits WHERE appointment_id = %s AND property_id = %s AND contact_id = %s",
                   (aid, pid, contatto))
    conferme = _q(vb, "SELECT status FROM communication_messages WHERE reason_code = 'appointment_confirmation' AND "
                      "(metadata->>'appointment_id')::bigint = %s", (aid,))
    assert conferme == [("queued",)]                                                              # UNA sola conferma
    scheda = agent.get(f"/api/appointments/{aid}").json()
    assert (scheda["property"]["id"], scheda["contact"]["id"], scheda["agent"]["id"]) == (pid, contatto, ids["agent_a"])
    assert scheda["visit"]["outcome"] is None and scheda["visit"]["can_record_feedback"] is False

    # la visita e' finita: l'orario e' passato, ma NON si chiude da sola
    passato = datetime.now(timezone.utc).replace(hour=10, minute=0, second=0, microsecond=0) - timedelta(days=3)
    _q(vb, "SET session_replication_role = replica; UPDATE appointments SET start_at = %s, end_at = %s WHERE id = %s",
       (passato, passato + timedelta(minutes=45), aid))
    _q(vb, "SET session_replication_role = replica; UPDATE property_visits SET scheduled_at = %s WHERE id = %s", (passato, vid))
    assert _q(vb, "SELECT status FROM appointments WHERE id = %s", (aid,)) == [("scheduled",)]
    assert agent.put(f"/api/appointments/{aid}/visit-outcome", json={"feedback": "troppo presto"}).status_code == 409
    assert agent.put(f"{'/api/owner-reports/visits'}/{aid}", json={"public_summary": TESTO}).status_code == 409

    # 2. ESITO: «Visita effettuata» + interesse + feedback interno
    versione = scheda["appointment"]["version"]
    assert agent.post(f"/api/appointments/{aid}/complete", json={
        "version": versione, "outcome_note": f"Nota interna. {SEGRETO_INTERNO}"}).status_code == 200
    assert agent.put(f"/api/appointments/{aid}/visit-outcome", json={
        "interest": "medium", "feedback": f"Nota interna. {SEGRETO_INTERNO}"}).status_code == 200

    # 3. COLLEGAMENTO alla scheda immobile e alla scheda contatto
    visite = owner.get(f"/api/property/properties/{pid}").json()["visits"]
    assert [(v["status"], v["outcome"]) for v in visite if v["appointment_id"] == aid] == [("completed", "Interessato")]
    mie = [v for v in owner.get(f"/api/crm/contacts/{contatto}/360").json()["visits"] if v["property_id"] == pid]
    assert [(v["status"], v["outcome"]) for v in mie] == [("completed", "Interessato")]

    # 4. FOGLIO VISITA PDF dall'appuntamento, 5. ARCHIVIATO e collegato
    assert agent.get(f"/api/visit-sheets/appointments/{aid}").json()["can_generate"] is True
    foglio = agent.post(f"/api/visit-sheets/appointments/{aid}").json()
    assert foglio["created"] is True and foglio["sheet"]["visit_state"] == "completed"
    assert foglio["sheet"]["signature_label"] == "Da firmare" and foglio["sheet"]["signed"] is False
    pdf = agent.get(f"/api/visit-sheets/{foglio['sheet']['id']}/pdf").content
    assert hashlib.sha256(pdf).hexdigest() == foglio["sheet"]["sha256"]
    testo = _pdf_testo(pdf)
    for atteso in ("Percorso Immobiliare", "+39 085 000111", "Maria Percorso", TELEFONO, EMAIL, "Via Segreta Civico-S77",
                   "Aldo Alfa", "Stato della firma: Da firmare", "Visita svolta"):
        assert atteso in testo, atteso
    for riservato in ("99999", "NOTA-RISERVATA", SEGRETO_INTERNO):
        assert riservato not in testo, riservato
    assert agent.post(f"/api/visit-sheets/appointments/{aid}").json()["created"] is False        # nessun duplicato
    assert _q(vb, "SELECT appointment_id, property_id FROM visit_sheets WHERE id = %s", (foglio["sheet"]["id"],)) == [(aid, pid)]

    # 6. AGGIORNAMENTO AL PROPRIETARIO: bozza (invisibile) -> anteprima -> approvazione -> portale
    prima_dei_messaggi = _q(vb, "SELECT count(*) FROM communication_messages")[0][0]
    stato = agent.get(f"/api/owner-reports/visits/{aid}").json()
    assert stato["can_prepare"] is True and "interesse" in stato["suggestion"]["public_summary"]
    salvata = agent.put(f"/api/owner-reports/visits/{aid}", json={
        "category": "general", "sentiment": "positive", "public_summary": TESTO}).json()
    bozza = salvata["current"]
    assert bozza["status"] == "draft" and salvata["can_approve"] is True
    assert portale.get(f"{BASE}/{pid}").json()["visit_feedback"] == []                            # il proprietario non la vede
    senza_gesto = agent.post(f"/api/owner-reports/visits/{aid}/approve", json={"publication_id": bozza["id"]})
    assert senza_gesto.status_code == 422                                                         # serve l'approvazione esplicita
    # 7. APPROVAZIONE e visualizzazione nel PORTALE
    assert agent.post(f"/api/owner-reports/visits/{aid}/approve",
                      json={"publication_id": bozza["id"], "confirm": True}).status_code == 200
    visto = portale.get(f"{BASE}/{pid}").json()["visit_feedback"]
    assert [(v["public_summary"], v["category_code"], v["sentiment"]) for v in visto] == [(TESTO, "general", "positive")]
    rapporto = portale.get(f"{BASE}/{pid}/report", params=PERIODO).json()
    assert rapporto["visits"]["completed"] == 1 and rapporto["visits"]["not_held"]["total"] == 0
    assert rapporto["visit_feedback"]["total"] == 1 and rapporto["visit_feedback"]["items"][0]["summary"] == TESTO

    # 8. ASSENZA DI DUPLICATI: una riga per fatto, e nessun messaggio in piu'
    assert _q(vb, "SELECT count(*) FROM appointments WHERE property_id = %s AND appointment_type = 'buyer_visit'", (pid,)) == [(1,)]
    assert _q(vb, "SELECT count(*) FROM property_visits WHERE property_id = %s", (pid,)) == [(1,)]
    assert _q(vb, "SELECT count(*) FROM property_visit_bookings WHERE appointment_id = %s", (aid,)) == [(1,)]
    assert _q(vb, "SELECT count(*) FROM visit_sheets WHERE appointment_id = %s", (aid,)) == [(1,)]
    assert _q(vb, "SELECT count(*) FROM owner_visit_feedback_publications WHERE property_visit_id = %s", (vid,)) == [(1,)]
    assert _q(vb, "SELECT count(*) FROM communication_messages")[0][0] == prima_dei_messaggi    # pubblicare non manda nulla
    assert _q(vb, "SELECT count(*) FROM communication_messages WHERE reason_code = 'appointment_confirmation' AND "
                  "(metadata->>'appointment_id')::bigint = %s", (aid,)) == [(1,)]

    # 9. PERMESSI e ISOLAMENTO tra agenzie: collega e altra agenzia non vedono ne' scrivono nulla
    for estraneo in (agent2, owner_b):
        assert estraneo.get(f"/api/appointments/{aid}").status_code == 404
        assert estraneo.put(f"/api/appointments/{aid}/visit-outcome", json={"feedback": "x"}).status_code == 404
        assert estraneo.get(f"/api/visit-sheets/appointments/{aid}").status_code == 404
        assert estraneo.post(f"/api/visit-sheets/appointments/{aid}").status_code == 404
        assert estraneo.get(f"/api/visit-sheets/{foglio['sheet']['id']}/pdf").status_code == 404
        assert estraneo.get(f"/api/owner-reports/visits/{aid}").status_code == 404
        assert estraneo.put(f"/api/owner-reports/visits/{aid}", json={"public_summary": TESTO}).status_code == 404
        assert estraneo.post(f"/api/owner-reports/visits/{aid}/withdraw", json={"publication_id": bozza["id"]}).status_code == 404
    assert portale.get(f"{BASE}/{pid + 100000}").status_code == 404                              # altro immobile: neutro

    # 10. PROTEZIONE DEI DATI PERSONALI: al proprietario non arriva nulla dell'acquirente ne' di interno
    grezzo = (portale.get(f"{BASE}/{pid}").text + portale.get(f"{BASE}/{pid}/report", params=PERIODO).text
              + _pdf_testo(portale.get(f"{BASE}/{pid}/report.pdf", params=PERIODO).content))
    for riservato in ("Maria Percorso", TELEFONO, EMAIL, SEGRETO_INTERNO, "Nota interna", "99999", "NOTA-RISERVATA",
                      "Interessato"):
        assert riservato not in grezzo, riservato
    assert TESTO in grezzo and "Prossimo passo" in grezzo                                        # il testo approvato si'
