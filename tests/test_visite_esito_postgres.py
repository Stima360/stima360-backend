"""ROADMAP VISITE - fase B: l'esito della visita dalla scheda appuntamento, su
PostgreSQL VERO (banco 2C: cluster LOCALE usa-e-getta, migrazioni dal runner, app
vera e login vero, dati solo sintetici).

    B  esito: effettuata / cliente assente / non effettuata + interesse + feedback
    N  mai completata da sola: l'orario passato non chiude nulla
    V  visibile nella scheda immobile e nella scheda contatto
    P  permessi, isolamento tra agenzie, rifiuti
"""
from __future__ import annotations

import itertools
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

SEGRETO_INTERNO = "NOTA-INTERNA-DELL-AGENTE"
_GIORNO = itertools.count(1)       # una visita per giorno: nessuna sovrapposizione sull'agenda dell'agente


def _contatto(b, agency="a", nome="Acquirente Esito"):
    return _q(b, "INSERT INTO contacts (agency_id, display_name, first_name, last_name, email) "
                 "VALUES (%s, %s, %s, 'Prova', 'acquirente.esito@example.test') RETURNING id",
              (b["ids"][agency], nome, nome))[0][0]


def _visita_passata(b, pid, *, agente="agent_a", agency="a", giorni=2):
    """Una visita creata DALL'AGENDA (proiezione compresa) e portata nel passato: il
    servizio vero la fa chiudere, esattamente come accade dopo la visita."""
    ids = b["ids"]
    contatto = _contatto(b, agency)
    giorno = next(_GIORNO)
    inizio = (datetime.now(timezone.utc) + timedelta(days=3 + giorno)).replace(hour=10, minute=0, second=0, microsecond=0)
    r = b["login"]("owner_a" if agency == "a" else "owner_b").post("/api/appointments", json={
        "appointment_type": "buyer_visit", "assigned_user_id": ids[agente], "property_id": pid,
        "contact_id": contatto, "start_at": inizio.isoformat(),
        "end_at": (inizio + timedelta(minutes=45)).isoformat(), "client_request_id": str(uuid.uuid4())})
    assert r.status_code == 201, r.text
    aid = r.json()["id"]
    passato = (datetime.now(timezone.utc) - timedelta(days=giorni + giorno)).replace(hour=10, minute=0, second=0,
                                                                                      microsecond=0)
    _q(b, "SET session_replication_role = replica; UPDATE appointments SET start_at = %s, end_at = %s WHERE id = %s",
       (passato, passato + timedelta(minutes=45), aid))
    _q(b, "SET session_replication_role = replica; UPDATE property_visits SET scheduled_at = %s WHERE appointment_id = %s",
       (passato, aid))
    return aid, contatto


def _azione(client, aid, azione, **corpo):
    versione = client.get(f"/api/appointments/{aid}").json()["appointment"]["version"]
    r = client.post(f"/api/appointments/{aid}/{azione}", json={"version": versione, **corpo})
    assert r.status_code == 200, r.text
    return r.json()


def _esito(client, aid, **corpo):
    return client.put(f"/api/appointments/{aid}/visit-outcome", json=corpo)


def _riga_visita(b, aid):
    return _q(b, "SELECT outcome, feedback, rating, status FROM property_visits WHERE appointment_id = %s", (aid,))[0]


# ---------------------------------------------------------------------------
# B - l'esito
# ---------------------------------------------------------------------------

def test_b_visita_effettuata_poi_interesse_e_feedback_nella_stessa_visita(app):
    pid = _property(app)
    aid, _c = _visita_passata(app, pid)
    owner = app["login"]("owner_a")
    # prima dell'esito il riepilogo c'e' ma non consente di scrivere
    visita = owner.get(f"/api/appointments/{aid}").json()["visit"]
    assert visita["outcome"] is None and visita["can_record_feedback"] is False and visita["can_record_interest"] is False
    _azione(owner, aid, "complete")
    r = _esito(owner, aid, interest="high", feedback=f"Ha apprezzato la luce. {SEGRETO_INTERNO}")
    assert r.status_code == 200, r.text
    assert r.json()["interest"] == "high" and r.json()["interest_label"] == "Molto interessato"
    assert _riga_visita(app, aid) == ("Molto interessato", f"Ha apprezzato la luce. {SEGRETO_INTERNO}", None, "completed")
    # il dettaglio dell'appuntamento mostra l'esito e le possibilita'
    d = owner.get(f"/api/appointments/{aid}").json()
    assert d["appointment"]["status"] == "completed"
    assert (d["visit"]["interest"], d["visit"]["can_record_interest"], d["visit"]["can_record_feedback"]) == ("high", True, True)
    # correggere dopo: l'interesse cambia, il feedback resta (campo non indicato)
    assert _esito(owner, aid, interest="undecided").status_code == 200
    assert _riga_visita(app, aid)[:2] == ("Indeciso", f"Ha apprezzato la luce. {SEGRETO_INTERNO}")
    # null svuota; solo l'interesse se e' una frase di interesse
    assert _esito(owner, aid, interest=None, feedback=None).status_code == 200
    assert _riga_visita(app, aid)[:2] == (None, None)


def test_b_cliente_assente_e_visita_non_effettuata_accettano_il_feedback_non_l_interesse(app):
    pid = _property(app)
    owner = app["login"]("owner_a")
    assente, _c = _visita_passata(app, pid)
    _azione(owner, assente, "no-show")
    assert _esito(owner, assente, feedback="Non si e' presentato, richiamato.").status_code == 200
    r = _esito(owner, assente, interest="high")
    assert r.status_code == 409 and r.json()["code"] == "BUYER_VISIT_OUTCOME_NOT_ALLOWED"
    assert _riga_visita(app, assente)[:2] == (None, "Non si e' presentato, richiamato.")
    annullata, _c = _visita_passata(app, pid)
    _azione(owner, annullata, "cancel", kind="agency", reason="Immobile non accessibile")
    assert _esito(owner, annullata, feedback="Chiavi non disponibili.").status_code == 200
    assert _esito(owner, annullata, interest="medium").status_code == 409
    assert _riga_visita(app, annullata) == (None, "Chiavi non disponibili.", None, "cancelled")


def test_b_il_valore_legacy_dell_esito_non_si_perde(app):
    pid = _property(app)
    aid, _c = _visita_passata(app, pid)
    owner = app["login"]("owner_a")
    _azione(owner, aid, "complete")
    _q(app, "UPDATE property_visits SET outcome = 'visita effettuata, cliente tiepido' WHERE appointment_id = %s", (aid,))
    visita = owner.get(f"/api/appointments/{aid}").json()["visit"]
    assert visita["interest"] is None and visita["outcome"] == "visita effettuata, cliente tiepido"
    assert _esito(owner, aid, feedback="Solo una nota").status_code == 200
    assert _riga_visita(app, aid)[0] == "visita effettuata, cliente tiepido"       # interesse non indicato: non cambia
    assert _esito(owner, aid, interest=None).status_code == 200
    assert _riga_visita(app, aid)[0] == "visita effettuata, cliente tiepido"       # null svuota solo una frase di interesse
    assert _esito(owner, aid, interest="none").status_code == 200
    assert _riga_visita(app, aid)[0] == "Non interessato"


def test_b_mai_rating_stato_o_data_ne_altri_campi(app):
    pid = _property(app)
    aid, _c = _visita_passata(app, pid)
    owner = app["login"]("owner_a")
    _azione(owner, aid, "complete")
    _q(app, "UPDATE property_visits SET rating = 4 WHERE appointment_id = %s", (aid,))
    prima = _q(app, "SELECT status, scheduled_at, contact_id, property_id, assigned_to FROM property_visits "
                    "WHERE appointment_id = %s", (aid,))[0]
    assert _esito(owner, aid, interest="medium", feedback="ok").status_code == 200
    assert _riga_visita(app, aid)[2] == 4
    assert _q(app, "SELECT status, scheduled_at, contact_id, property_id, assigned_to FROM property_visits "
                   "WHERE appointment_id = %s", (aid,))[0] == prima
    for corpo in ({"rating": 5}, {"status": "cancelled"}, {"scheduled_at": "2030-01-01T10:00:00+01:00"},
                  {"contact_id": 1}, {"outcome": "libero"}):
        assert _esito(owner, aid, **corpo).status_code == 422, corpo


# ---------------------------------------------------------------------------
# N - mai completata da sola
# ---------------------------------------------------------------------------

def test_n_l_orario_passato_non_chiude_la_visita_e_l_esito_non_si_registra(app):
    pid = _property(app)
    aid, _c = _visita_passata(app, pid, giorni=30)             # passata da un mese
    owner = app["login"]("owner_a")
    # letture, elenchi e calendario non cambiano nulla
    owner.get(f"/api/appointments/{aid}")
    owner.get("/api/appointments", params={"property_id": pid})
    assert _q(app, "SELECT status FROM appointments WHERE id = %s", (aid,)) == [("scheduled",)]
    assert _riga_visita(app, aid)[3] == "scheduled"
    r = _esito(owner, aid, feedback="Provo a scrivere l'esito di una visita ancora aperta")
    assert r.status_code == 409 and r.json()["code"] == "BUYER_VISIT_OUTCOME_NOT_ALLOWED"
    assert _riga_visita(app, aid)[:2] == (None, None)
    # l'Agenda offre le azioni di esito, ma le esegue SOLO l'operatore
    azioni = owner.get(f"/api/appointments/{aid}").json()["allowed_actions"]
    assert "complete" in azioni and "no_show" in azioni
    assert _q(app, "SELECT status FROM appointments WHERE id = %s", (aid,)) == [("scheduled",)]


def test_n_il_modulo_dell_esito_non_cambia_mai_lo_stato():
    import re
    from pathlib import Path
    codice = (Path(__file__).resolve().parents[1] / "buyer_visits" / "visit_outcome.py").read_text(encoding="utf-8")
    scritture = re.findall(r'UPDATE property_visits SET ([^"]+)"', codice)
    assert scritture == ["outcome = %s, feedback = %s, updated_at = NOW() "], scritture
    assert "INSERT" not in codice and "DELETE" not in codice and "rating = " not in codice


# ---------------------------------------------------------------------------
# V - visibile su immobile e contatto
# ---------------------------------------------------------------------------

def test_v_l_esito_e_visibile_nella_scheda_immobile_e_nella_scheda_contatto(app):
    pid = _property(app)
    aid, contatto = _visita_passata(app, pid)
    owner = app["login"]("owner_a")
    _azione(owner, aid, "complete")
    assert _esito(owner, aid, interest="medium", feedback="Chiede una seconda visita con il coniuge").status_code == 200
    prop = owner.get(f"/api/property/properties/{pid}").json()
    visite = prop["visits"]
    assert [(v["status"], v["outcome"], v["feedback"]) for v in visite if v["appointment_id"] == aid] == [
        ("completed", "Interessato", "Chiede una seconda visita con il coniuge")]
    scheda = owner.get(f"/api/crm/contacts/{contatto}/360").json()
    mie = [v for v in scheda["visits"] if v["property_id"] == pid]
    assert [(v["status"], v["outcome"]) for v in mie] == [("completed", "Interessato")]


# ---------------------------------------------------------------------------
# P - permessi e agenzie
# ---------------------------------------------------------------------------

def test_p_permessi_agente_proprio_collega_altra_agenzia(app):
    pid = _property(app)
    aid, _c = _visita_passata(app, pid, agente="agent_a")
    owner, admin, agent, agent2, owner_b = (app["login"](k) for k in
                                            ("owner_a", "admin_a", "agent_a", "agent2_a", "owner_b"))
    _azione(owner, aid, "complete")
    assert _esito(agent2, aid, feedback="non e' la mia visita").status_code == 404      # collega: non la vede
    assert agent2.get(f"/api/appointments/{aid}").status_code == 404
    assert _esito(owner_b, aid, feedback="altra agenzia").status_code == 404
    assert _riga_visita(app, aid)[:2] == (None, None)
    assert _esito(agent, aid, interest="high", feedback="la mia").status_code == 200
    assert _esito(admin, aid, feedback="corretto dall'amministratore").status_code == 200
    assert _riga_visita(app, aid)[1] == "corretto dall'amministratore"
    from fastapi.testclient import TestClient
    import main
    anonimo = TestClient(main.app, base_url="https://testserver")
    assert anonimo.put(f"/api/appointments/{aid}/visit-outcome", json={"feedback": "x"}).status_code in (401, 403)


def test_p_rifiuti_puliti_senza_scritture(app):
    pid = _property(app)
    aid, _c = _visita_passata(app, pid)
    owner = app["login"]("owner_a")
    _azione(owner, aid, "complete")
    r = _esito(owner, aid, interest="entusiasta")
    assert r.status_code == 422 and r.json()["code"] == "BUYER_VISIT_OUTCOME_INVALID"
    assert _esito(owner, aid, feedback="x" * 4001).status_code == 422
    assert _riga_visita(app, aid)[:2] == (None, None)
    # un appuntamento che non e' una visita a un immobile
    ids = app["ids"]
    inizio = (datetime.now(timezone.utc) + timedelta(days=9)).replace(second=0, microsecond=0)
    altro = owner.post("/api/appointments", json={
        "appointment_type": "seller_meeting", "assigned_user_id": ids["agent_a"], "start_at": inizio.isoformat(),
        "end_at": (inizio + timedelta(minutes=30)).isoformat(), "client_request_id": str(uuid.uuid4())}).json()["id"]
    r = _esito(owner, altro, feedback="non e' una visita")
    assert r.status_code in (409, 422)
    assert owner.get(f"/api/appointments/{altro}").json()["visit"] is None
    assert _esito(owner, 999999999, feedback="x").status_code == 404
