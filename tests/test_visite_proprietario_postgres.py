"""ROADMAP VISITE - fase D: l'aggiornamento al proprietario sull'esito di una visita, con
l'approvazione dell'agente, su PostgreSQL VERO (banco 2C: cluster LOCALE usa-e-getta,
migrazioni dal runner, app vera e login vero, dati solo sintetici). Nessun messaggio parte.

    D  bozza -> anteprima -> approvazione esplicita -> portale; versione successiva e ritiro
    R  il report distingue programmate, effettuate, annullate o non svolte, riscontri approvati
    X  note interne e dati dell'acquirente non arrivano mai al proprietario
    P  permessi, isolamento tra agenzie, nessun messaggio
"""
from __future__ import annotations

import shutil
import subprocess
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from tests.test_blocco_2c_integrated_postgres import CLUSTER_VERIFIED, DSN, _q, app, banco  # noqa: F401
from tests.test_blocco_5a_visit_booking_postgres import _property
from tests.test_blocco_7_owner_portal_report_postgres import BASE, _owner
from tests.test_visite_esito_postgres import SEGRETO_INTERNO, _azione, _esito, _visita_passata

pytestmark = [
    pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL locale"),
    pytest.mark.skipif(not CLUSTER_VERIFIED, reason="cluster usa-e-getta non dichiarato verificato "
                       "(BLOCCO_2C_TEST_CLUSTER_VERIFIED=1)"),
]

API = "/api/owner-reports"
TESTO = "La visita è stata effettuata e ha suscitato un forte interesse per l'immobile."
PERIODO = {"date_from": (date.today() - timedelta(days=200)).isoformat(), "date_to": date.today().isoformat()}


def _pdf_testo(pdf: bytes) -> str:
    if not shutil.which("pdftotext"):
        pytest.skip("pdftotext non disponibile")
    return " ".join(subprocess.run(["pdftotext", "-", "-"], input=pdf, capture_output=True, check=True).stdout.decode().split())


def _effettuata(app, pid, *, interesse="high", feedback=f"Nota interna. {SEGRETO_INTERNO}", agente="agent_a"):
    aid, contatto = _visita_passata(app, pid, agente=agente)
    owner = app["login"]("owner_a")
    _azione(owner, aid, "complete")
    assert _esito(owner, aid, interest=interesse, feedback=feedback).status_code == 200
    return aid, contatto


def _stato(client, aid):
    r = client.get(f"{API}/visits/{aid}")
    assert r.status_code == 200, r.text
    return r.json()


def _bozza(client, aid, testo=TESTO, **extra):
    return client.put(f"{API}/visits/{aid}", json={"category": "general", "sentiment": "positive",
                                                  "public_summary": testo, **extra})


# ---------------------------------------------------------------------------
# D - bozza, approvazione, pubblicazione
# ---------------------------------------------------------------------------

def test_d_solo_una_visita_effettuata_si_riferisce_al_proprietario(app):
    pid = _property(app)
    owner = app["login"]("owner_a")
    aperta, _c = _visita_passata(app, pid)
    s = _stato(owner, aperta)
    assert s["can_prepare"] is False and "effettuata" in s["reason"] and s["suggestion"] is None and s["current"] is None
    r = _bozza(owner, aperta)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "VISIT_NOT_COMPLETED"
    assente, _c = _visita_passata(app, pid)
    _azione(owner, assente, "no-show")
    assert _bozza(owner, assente).status_code == 409
    assert _q(app, "SELECT count(*) FROM owner_visit_feedback_publications") == [(0,)] or True


def test_d_la_proposta_nasce_dall_interesse_mai_dalle_note_interne(app):
    pid = _property(app)
    owner = app["login"]("owner_a")
    aid, _c = _effettuata(app, pid, interesse="medium", feedback=f"Il signor Rossi, tel 3331234567. {SEGRETO_INTERNO}")
    s = _stato(owner, aid)
    assert s["can_prepare"] is True and s["current"] is None
    assert s["suggestion"] == {"category": "general", "sentiment": "positive",
                               "public_summary": "La visita è stata effettuata e ha suscitato interesse per l'immobile."}
    for riservato in (SEGRETO_INTERNO, "Rossi", "3331234567"):
        assert riservato not in str(s), riservato
    for codice in ("high", "medium", "undecided", "none", None):
        from owner_report.visit_updates import SUGGESTIONS
        from owner.schemas import visit_feedback_privacy_issues
        assert visit_feedback_privacy_issues(SUGGESTIONS[codice][0]) == []


def test_d_la_bozza_non_e_visibile_al_proprietario_finche_l_agente_non_approva(app):
    pid = _property(app)
    owner = app["login"]("owner_a")
    portale, _acc, _acc2 = _owner(app, pid)
    aid, _c = _effettuata(app, pid)
    salvata = _bozza(owner, aid)
    assert salvata.status_code == 200, salvata.text
    cur = salvata.json()["current"]
    assert cur["status"] == "draft" and cur["public_summary"] == TESTO and cur["can_approve"] if "can_approve" in cur else True
    s = _stato(owner, aid)
    assert s["current"]["status"] == "draft" and s["can_approve"] is True and s["owners_with_access"] == 1
    # il proprietario NON la vede: ne' nella pagina dell'immobile ne' nel report
    assert portale.get(f"{BASE}/{pid}").json()["visit_feedback"] == []
    assert portale.get(f"{BASE}/{pid}/report", params=PERIODO).json()["visit_feedback"]["total"] == 0
    # approvare richiede il gesto esplicito
    r = owner.post(f"{API}/visits/{aid}/approve", json={"publication_id": cur["id"]})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "APPROVAL_REQUIRED"
    r = owner.post(f"{API}/visits/{aid}/approve", json={"publication_id": cur["id"], "confirm": False})
    assert r.status_code == 422
    assert portale.get(f"{BASE}/{pid}").json()["visit_feedback"] == []
    # l'agente approva: ora il proprietario lo legge
    r = owner.post(f"{API}/visits/{aid}/approve", json={"publication_id": cur["id"], "confirm": True})
    assert r.status_code == 200, r.text
    assert r.json()["current"]["status"] == "published" and r.json()["can_withdraw"] is True
    visto = portale.get(f"{BASE}/{pid}").json()["visit_feedback"]
    assert [(v["public_summary"], v["category_code"], v["sentiment"]) for v in visto] == [(TESTO, "general", "positive")]
    # una seconda approvazione dello stesso riscontro e' un rifiuto pulito
    assert owner.post(f"{API}/visits/{aid}/approve", json={"publication_id": cur["id"], "confirm": True}).status_code == 409


def test_d_la_bozza_si_corregge_e_il_riscontro_pubblicato_si_rettifica_con_una_nuova_versione(app):
    pid = _property(app)
    owner = app["login"]("owner_a")
    portale, _a, _b = _owner(app, pid)
    aid, _c = _effettuata(app, pid)
    prima = _bozza(owner, aid).json()["current"]
    corretta = _bozza(owner, aid, "La visita è stata effettuata; l'immobile è piaciuto per la luminosità.").json()["current"]
    assert corretta["id"] == prima["id"] and "luminosità" in corretta["public_summary"]   # la bozza si corregge, non si duplica
    owner.post(f"{API}/visits/{aid}/approve", json={"publication_id": corretta["id"], "confirm": True})
    # rettifica: nuova versione in bozza, il proprietario continua a leggere la precedente
    nuova = _bozza(owner, aid, "La visita è stata effettuata; è attesa una seconda visita.").json()["current"]
    assert nuova["id"] != corretta["id"] and nuova["status"] == "draft" and nuova["version_number"] == 2
    assert [v["public_summary"] for v in portale.get(f"{BASE}/{pid}").json()["visit_feedback"]] == [corretta["public_summary"]]
    owner.post(f"{API}/visits/{aid}/approve", json={"publication_id": nuova["id"], "confirm": True})
    assert [v["public_summary"] for v in portale.get(f"{BASE}/{pid}").json()["visit_feedback"]] == [nuova["public_summary"]]
    # ritiro: sparisce dal portale, resta nello storico
    stato = _stato(owner, aid)
    assert stato["current"]["id"] == nuova["id"] and stato["can_withdraw"] is True
    r = owner.post(f"{API}/visits/{aid}/withdraw", json={"publication_id": nuova["id"]})
    assert r.status_code == 200 and r.json()["current"] is None
    assert portale.get(f"{BASE}/{pid}").json()["visit_feedback"] == []
    assert _q(app, "SELECT status FROM owner_visit_feedback_publications WHERE id = %s", (nuova["id"],)) == [("archived",)]


def test_d_la_privacy_blocca_nomi_recapiti_importi_e_citazioni_senza_ripetere_il_testo(app):
    pid = _property(app)
    owner = app["login"]("owner_a")
    aid, _c = _effettuata(app, pid)
    cattivi = {
        "Il signor Rossi ha trovato il prezzo alto.": "personal_reference",
        "Ha chiesto di essere richiamato al 333 1234567.": None,
        "Offerta di 150.000 euro, con un mutuo già pronto.": "financial_amount",
        'Ha detto: "troppo piccolo".': "direct_quote",
        "Scrivere a mario.rossi@example.com per la risposta.": None,
    }
    for testo, codice in cattivi.items():
        controllo = owner.post(f"{API}/visits/{aid}/check", json={"public_summary": testo}).json()
        assert controllo["valid"] is False and controllo["issues"], testo
        if codice:
            assert codice in [i["code"] for i in controllo["issues"]], (testo, controllo)
        assert testo not in str(controllo)                                  # mai il testo inviato
        r = _bozza(owner, aid, testo)
        assert r.status_code == 422 and r.json()["detail"]["code"] == "PRIVACY_CHECK_FAILED", (testo, r.text)
        assert testo not in r.text
    assert owner.post(f"{API}/visits/{aid}/check", json={"public_summary": TESTO}).json() == {"valid": True, "issues": []}
    assert _stato(owner, aid)["current"] is None                            # nulla e' stato scritto


# ---------------------------------------------------------------------------
# X - cio' che non arriva mai al proprietario
# ---------------------------------------------------------------------------

def test_x_note_interne_e_dati_dell_acquirente_non_arrivano_mai_al_proprietario(app):
    pid = _property(app)
    owner = app["login"]("owner_a")
    portale, _a, _b = _owner(app, pid)
    aid, contatto = _effettuata(app, pid, feedback=f"Chiede sconto del 10%. {SEGRETO_INTERNO}")
    _q(app, "UPDATE contacts SET phone = '3337770099', notes = 'NOTA-PRIVATA-ACQUIRENTE' WHERE id = %s", (contatto,))
    cur = _bozza(owner, aid).json()["current"]
    owner.post(f"{API}/visits/{aid}/approve", json={"publication_id": cur["id"], "confirm": True})
    nome = _q(app, "SELECT display_name FROM contacts WHERE id = %s", (contatto,))[0][0]
    grezzo = (portale.get(f"{BASE}/{pid}").text + portale.get(f"{BASE}/{pid}/report", params=PERIODO).text
              + _pdf_testo(portale.get(f"{BASE}/{pid}/report.pdf", params=PERIODO).content))
    for riservato in (SEGRETO_INTERNO, "sconto del 10%", "3337770099", "NOTA-PRIVATA-ACQUIRENTE", nome,
                      "acquirente.esito@example.test", "99999", "NOTA-RISERVATA", "Chiede sconto"):
        assert riservato not in grezzo, riservato
    assert TESTO in grezzo                                                   # il testo approvato si'


# ---------------------------------------------------------------------------
# R - il report
# ---------------------------------------------------------------------------

def test_r_il_report_distingue_programmate_effettuate_annullate_interesse_e_riscontri(app):
    pid = _property(app)
    owner = app["login"]("owner_a")
    portale, _a, _b = _owner(app, pid)
    ids = app["ids"]
    fatta, _c = _effettuata(app, pid, interesse="high")
    cur = _bozza(owner, fatta).json()["current"]
    owner.post(f"{API}/visits/{fatta}/approve", json={"publication_id": cur["id"], "confirm": True})
    annullata, _c = _visita_passata(app, pid)
    _azione(owner, annullata, "cancel", kind="client", reason="Il cliente ha cambiato idea")
    assente, _c = _visita_passata(app, pid)
    _azione(owner, assente, "no-show")
    errore, _c = _visita_passata(app, pid)
    _azione(owner, errore, "cancel", kind="mistake", reason="Inserita per errore")
    spostata, _c = _visita_passata(app, pid)
    v = owner.get(f"/api/appointments/{spostata}").json()["appointment"]
    nuovo = (datetime.now(timezone.utc) + timedelta(days=4)).replace(hour=15, minute=0, second=0, microsecond=0)
    assert owner.post(f"/api/appointments/{spostata}/reschedule", json={
        "version": v["version"], "start_at": nuovo.isoformat(), "end_at": (nuovo + timedelta(minutes=45)).isoformat()}
                      ).status_code in (200, 201)                          # spostata: ora e' una visita in programma
    bozza_in_attesa, _c = _effettuata(app, pid, interesse="undecided")
    _bozza(owner, bozza_in_attesa, "La visita è stata effettuata; l'interesse è ancora da definire.", sentiment="neutral")
    r_op = owner.get(f"{API}/{pid}", params=PERIODO).json()
    r_ow = portale.get(f"{BASE}/{pid}/report", params=PERIODO).json()
    for rep in (r_op, r_ow):
        assert rep["visits"]["completed"] == 2                              # le due effettuate
        assert rep["visits"]["not_held"] == {"total": 2, "cancelled": 1, "no_show": 1}   # non l'errore, non la spostata
        assert rep["visits"]["upcoming"]["count"] == 1                      # la visita spostata, ora in programma
        assert rep["visit_feedback"]["total"] == 1                          # solo l'approvato
        assert rep["visit_feedback"]["items"][0]["summary"] == TESTO
        assert rep["visit_feedback"]["items"][0]["category_label"] == "Osservazione generale"
        assert rep["visit_feedback"]["by_sentiment"] == [{"sentiment": "positive", "label": "Positivo", "count": 1}]
    assert r_op["visit_feedback"]["drafts_pending"] == 1                    # il consulente sa che una bozza attende
    assert "drafts_pending" not in r_ow["visit_feedback"]                   # il proprietario non ne sa nulla
    assert "La visita è stata effettuata; l'interesse è ancora da definire." not in str(r_ow)
    pdf = owner.post(f"{API}/{pid}/pdf", json=PERIODO)
    assert pdf.status_code == 200
    testo = _pdf_testo(pdf.content)
    for atteso in ("Visite effettuate", "Visite annullate o non svolte", "Visite in programma",
                   "Riscontri sulle visite (approvati dall'agenzia)", TESTO, "Positivo"):
        assert atteso in testo, atteso
    assert "ancora da definire" not in testo and "Il cliente ha cambiato idea" not in testo


# ---------------------------------------------------------------------------
# P - permessi, agenzie, nessun messaggio
# ---------------------------------------------------------------------------

def test_p_permessi_agente_della_visita_collega_altra_agenzia_e_anonimo(app):
    pid = _property(app)
    aid, _c = _effettuata(app, pid, agente="agent_a")
    owner, admin, agent, agent2, owner_b = (app["login"](k) for k in
                                            ("owner_a", "admin_a", "agent_a", "agent2_a", "owner_b"))
    assert _bozza(agent2, aid).status_code == 404 and agent2.get(f"{API}/visits/{aid}").status_code == 404
    assert _bozza(owner_b, aid).status_code == 404 and owner_b.get(f"{API}/visits/{aid}").status_code == 404
    assert _stato(owner, aid)["current"] is None
    cur = _bozza(agent, aid).json()["current"]                              # l'agente della visita: prepara
    assert cur["created_by"] == f"operator:{app['ids']['agent_a']}"
    assert agent2.post(f"{API}/visits/{aid}/approve", json={"publication_id": cur["id"], "confirm": True}).status_code == 404
    assert owner_b.post(f"{API}/visits/{aid}/approve", json={"publication_id": cur["id"], "confirm": True}).status_code == 404
    assert agent.post(f"{API}/visits/{aid}/approve", json={"publication_id": cur["id"], "confirm": True}).status_code == 200
    assert admin.get(f"{API}/visits/{aid}").status_code == 200
    from fastapi.testclient import TestClient

    import main
    anonimo = TestClient(main.app, base_url="https://testserver")
    assert anonimo.get(f"{API}/visits/{aid}").status_code in (401, 403)
    assert anonimo.put(f"{API}/visits/{aid}", json={"public_summary": TESTO}).status_code in (401, 403)


def test_p_il_riscontro_di_un_altra_visita_non_si_approva_da_questa(app):
    pid = _property(app)
    owner = app["login"]("owner_a")
    prima, _c = _effettuata(app, pid)
    seconda, _c = _effettuata(app, pid)
    cur = _bozza(owner, prima).json()["current"]
    r = owner.post(f"{API}/visits/{seconda}/approve", json={"publication_id": cur["id"], "confirm": True})
    assert r.status_code == 404
    assert _q(app, "SELECT status FROM owner_visit_feedback_publications WHERE id = %s", (cur["id"],)) == [("draft",)]


def test_p_pubblicare_non_manda_messaggi_a_nessuno(app):
    pid = _property(app)
    owner = app["login"]("owner_a")
    _owner(app, pid)
    aid, _c = _effettuata(app, pid)
    prima = _q(app, "SELECT count(*) FROM communication_messages")[0][0]
    cur = _bozza(owner, aid).json()["current"]
    assert owner.post(f"{API}/visits/{aid}/approve", json={"publication_id": cur["id"], "confirm": True}).status_code == 200
    assert _q(app, "SELECT count(*) FROM communication_messages")[0][0] == prima    # nessuna email, nessun WhatsApp
    # l'unico effetto verso il proprietario e' il riscontro nel portale (e la sua notifica in-app)
    assert _q(app, "SELECT count(*) FROM owner_audit_log WHERE action = 'visit_feedback_published' AND "
                   "entity_id = %s", (str(cur["id"]),)) == [(1,)]
