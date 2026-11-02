"""ROADMAP VISITE - fase A: prenotazione -> Agenda -> immobile -> contatto -> agente,
su PostgreSQL VERO (banco 2C: cluster LOCALE usa-e-getta, migrazioni dal runner,
app vera e login vero, dati solo sintetici).

    A  ogni prenotazione e' collegata a: appuntamento, immobile, contatto, agente, data, ora, stato
    C  la conferma al cliente sul percorso della prenotazione dell'immobile (una sola, mai doppia)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from tests.test_blocco_2c_integrated_postgres import CLUSTER_VERIFIED, DSN, _q, app, banco  # noqa: F401
from tests.test_blocco_5a_visit_booking_postgres import (  # noqa: F401 - fixture e aiuti
    PUB, _appointment, _book, _property, _slots, _token, vb)

pytestmark = [
    pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL locale"),
    pytest.mark.skipif(not CLUSTER_VERIFIED, reason="cluster usa-e-getta non dichiarato verificato "
                       "(BLOCCO_2C_TEST_CLUSTER_VERIFIED=1)"),
]

EMAIL = "visitatore.sintetico@example.test"


def _book_mail(b, token, start_at, key=None, email=EMAIL, phone="3330001122"):
    return b["public"].post(f"{PUB}/{token}/book", json={
        "name": "Visitatore Sintetico", "phone": phone, "email": email, "start_at": start_at,
        "key": key or str(uuid.uuid4()), "privacy_accepted": True})


def _proiezione(b, appointment_id):
    r = _q(b, "SELECT id, property_id, contact_id, scheduled_at, status, assigned_to, appointment_id "
              "FROM property_visits WHERE appointment_id = %s", (appointment_id,))
    return r


def _conferme(b, appointment_id=None):
    sql = ("SELECT id, status, reason_code, idempotency_key, destination_snapshot, subject_snapshot, "
           "metadata->>'variant', metadata->>'appointment_id' FROM communication_messages "
           "WHERE reason_code = 'appointment_confirmation'")
    if appointment_id is not None:
        sql += f" AND (metadata->>'appointment_id')::bigint = {int(appointment_id)}"
    return _q(b, sql + " ORDER BY id")


@pytest.fixture
def notifiche(vb, monkeypatch):
    """Conferme accese come sul TEST, solo l'indirizzo di prova nell'elenco."""
    monkeypatch.setenv("APPOINTMENT_CONFIRMATIONS", "1")
    monkeypatch.setenv("APPOINTMENT_NOTIFICATIONS_ALLOWED_RECIPIENTS", EMAIL)
    monkeypatch.delenv("APPOINTMENT_REMINDER_OFFSETS", raising=False)
    return vb                                  # il ledger e' append-only: si legge per appuntamento


# ---------------------------------------------------------------------------
# A - i collegamenti
# ---------------------------------------------------------------------------

def test_a_la_prenotazione_e_collegata_ad_agenda_immobile_contatto_agente_data_e_stato(vb):
    ids = vb["ids"]
    pid = _property(vb)
    token = _token(vb, pid)
    start = _slots(vb, token)[0]["start_at"]
    out = _book_mail(vb, token, start)
    assert out.status_code == 200, out.text
    ((aid, tipo, stato, prop, contatto, agente, fonte, *_),) = _appointment(vb, start, pid)
    assert (tipo, stato, prop, agente, fonte) == ("buyer_visit", "scheduled", pid, ids["agent_a"], "booking_link")
    assert contatto is not None
    # il contatto e' l'acquirente della prenotazione, della stessa agenzia
    (nome, email, agenzia), = _q(vb, "SELECT display_name, email, agency_id FROM contacts WHERE id = %s", (contatto,))
    assert nome == "Visitatore Sintetico" and email == EMAIL and agenzia == ids["a"]
    # la STESSA visita sull'immobile: stesso immobile, contatto, data, stato; l'agente come etichetta
    ((vid, v_prop, v_contatto, v_quando, v_stato, v_agente, v_app),) = _proiezione(vb, aid)
    assert (v_prop, v_contatto, v_stato, v_app) == (pid, contatto, "scheduled", aid)
    assert v_quando == datetime.fromisoformat(start)
    assert v_agente                                   # etichetta di visualizzazione dell'agente
    # e la riga di prenotazione le lega
    ((booking_app,),) = _q(vb, "SELECT appointment_id FROM property_visit_bookings WHERE appointment_id = %s", (aid,))
    assert booking_app == aid
    # la scheda dell'appuntamento in Agenda mostra immobile, contatto e agente
    detail = vb["login"]("owner_a").get(f"/api/appointments/{aid}").json()
    assert detail["property"]["id"] == pid and detail["contact"]["id"] == contatto
    assert detail["agent"]["id"] == ids["agent_a"] and detail["appointment"]["status"] == "scheduled"
    # nessun duplicato: una visita, una proiezione, una prenotazione
    assert _q(vb, "SELECT count(*) FROM appointments WHERE property_id = %s AND start_at = %s", (pid, start)) == [(1,)]
    assert _q(vb, "SELECT count(*) FROM property_visits WHERE appointment_id = %s", (aid,)) == [(1,)]


def test_a_senza_consenso_del_proprietario_resta_richiesta_poi_fissata_e_proiettata(vb):
    ids = vb["ids"]
    pid = _property(vb)
    token = _token(vb, pid, owner="per_visit")
    start = _slots(vb, token)[0]["start_at"]
    assert _book_mail(vb, token, start).status_code == 200
    ((aid, _t, stato, *_),) = _appointment(vb, start, pid)
    assert stato == "requested" and _proiezione(vb, aid) == []          # D1: nessuna proiezione prima
    detail = vb["login"]("owner_a").get(f"/api/appointments/{aid}").json()
    r = vb["login"]("owner_a").post(f"/api/appointments/{aid}/schedule", json={
        "version": detail["appointment"]["version"], "assigned_user_id": ids["agent_a"],
        "start_at": start, "end_at": detail["appointment"]["end_at"]})
    assert r.status_code == 200, r.text
    ((vid, v_prop, v_contatto, _q2, v_stato, _ag, v_app),) = _proiezione(vb, aid)
    assert (v_prop, v_stato, v_app) == (pid, "scheduled", aid) and v_contatto is not None


def test_a_la_visita_creata_dall_agenda_e_collegata_allo_stesso_modo(vb):
    ids = vb["ids"]
    pid = _property(vb)
    contatto = _q(vb, "INSERT INTO contacts (agency_id, display_name, first_name, last_name) VALUES (%s, 'Acquirente Agenda', "
                      "'Acquirente', 'Agenda') RETURNING id", (ids["a"],))[0][0]
    inizio = (datetime.now(timezone.utc) + timedelta(days=5)).replace(minute=0, second=0, microsecond=0)
    r = vb["login"]("owner_a").post("/api/appointments", json={
        "appointment_type": "buyer_visit", "assigned_user_id": ids["agent_a"], "property_id": pid,
        "contact_id": contatto, "start_at": inizio.isoformat(), "end_at": (inizio + timedelta(minutes=45)).isoformat(),
        "client_request_id": str(uuid.uuid4())})
    assert r.status_code == 201, r.text
    aid = r.json()["id"]
    ((vid, v_prop, v_contatto, v_quando, v_stato, v_agente, v_app),) = _proiezione(vb, aid)
    assert (v_prop, v_contatto, v_stato, v_app) == (pid, contatto, "scheduled", aid) and v_quando == inizio
    assert v_agente


def test_a_spostamento_e_annullamento_del_cliente_seguono_in_agenda_e_sull_immobile(vb):
    pid = _property(vb)
    token = _token(vb, pid)
    slots = _slots(vb, token)
    out = _book_mail(vb, token, slots[0]["start_at"]).json()
    chiave = out["manage_url"].rsplit("/", 1)[1]
    ((aid, *_),) = _appointment(vb, slots[0]["start_at"], pid)
    ((vid, *_),) = _proiezione(vb, aid)
    nuovi = vb["public"].get(f"{PUB}/manage/{chiave}/slots", params={
        "date_from": slots[0]["start_at"],
        "date_to": (datetime.fromisoformat(slots[0]["start_at"]) + timedelta(days=1)).isoformat()}).json()["slots"]
    target = next(s["start_at"] for s in nuovi if s["start_at"] != slots[0]["start_at"])
    moved = vb["public"].post(f"{PUB}/manage/{chiave}/reschedule", json={"start_at": target, "key": str(uuid.uuid4())})
    assert moved.status_code == 200, moved.text
    ((nuovo_id, *_),) = _appointment(vb, target, pid)
    # la STESSA riga `property_visits` passa al successore, con la nuova data
    # (la prenotazione pubblica annulla la visita vecchia e ne crea una nuova: una riga
    # `property_visits` per appuntamento; solo lo spostamento dell'Agenda sposta la stessa riga)
    ((vid2, _p, v_contatto2, v_quando, v_stato, _a, v_app),) = _proiezione(vb, nuovo_id)
    assert vid2 != vid and v_app == nuovo_id and v_quando == datetime.fromisoformat(target) and v_stato == "scheduled"
    ((_v, _p, v_contatto1, _qq, v_stato1, _a1, _app1),) = _proiezione(vb, aid)
    assert v_stato1 == "cancelled" and v_contatto1 == v_contatto2          # stesso acquirente, storico leggibile
    assert _q(vb, "SELECT rescheduled_from_id FROM appointments WHERE id = %s", (nuovo_id,)) == [(aid,)]
    nuova_chiave = moved.json()["manage_url"].rsplit("/", 1)[1]
    assert vb["public"].post(f"{PUB}/manage/{nuova_chiave}/cancel").json()["state"] == "cancelled"
    assert _proiezione(vb, nuovo_id)[0][4] == "cancelled"
    assert _q(vb, "SELECT count(*) FROM property_visits WHERE property_id = %s", (pid,)) == [(2,)]


# ---------------------------------------------------------------------------
# C - la conferma sul percorso della prenotazione dell'immobile
# ---------------------------------------------------------------------------

def test_c_senza_impostazione_nessuna_conferma(vb, monkeypatch):
    monkeypatch.delenv("APPOINTMENT_CONFIRMATIONS", raising=False)
    pid = _property(vb)
    token = _token(vb, pid)
    start = _slots(vb, token)[0]["start_at"]
    assert _book_mail(vb, token, start).status_code == 200
    ((aid, *_),) = _appointment(vb, start, pid)
    assert _conferme(vb, aid) == []


def test_c_una_sola_conferma_con_indirizzo_e_mai_doppia(notifiche):
    vb = notifiche
    pid = _property(vb)
    token = _token(vb, pid)
    start = _slots(vb, token)[0]["start_at"]
    chiave = str(uuid.uuid4())
    assert _book_mail(vb, token, start, key=chiave).status_code == 200
    assert _book_mail(vb, token, start, key=chiave).status_code == 200      # secondo clic: stessa prenotazione
    ((aid, *_),) = _appointment(vb, start, pid)
    ((mid, stato, motivo, idem, dest, oggetto, variante, _a),) = _conferme(vb, aid)
    assert (stato, motivo, dest, variante) == ("queued", "appointment_confirmation", EMAIL, "scheduled")
    assert idem.startswith(f"appointment_confirmation:v1:{aid}:") and oggetto.startswith("Conferma: visita all'immobile")
    corpo = _q(vb, "SELECT rendered_body FROM communication_messages WHERE id = %s", (mid,))[0][0]
    assert "Via Segreta" in corpo and "NOTA-RISERVATA" not in corpo and "99999" not in corpo   # indirizzo si', riservato no


def test_c_destinatario_fuori_elenco_o_richiesta_non_ricevono_la_conferma(notifiche, monkeypatch):
    vb = notifiche
    pid = _property(vb)
    token = _token(vb, pid)
    start = _slots(vb, token)[0]["start_at"]
    assert _book_mail(vb, token, start, email="altro.indirizzo@example.test", phone="3339998877").status_code == 200
    ((aid, *_),) = _appointment(vb, start, pid)
    assert _conferme(vb, aid) == []                                           # fuori elenco
    pid2 = _property(vb)
    token2 = _token(vb, pid2, owner="per_visit")
    start2 = _slots(vb, token2)[0]["start_at"]
    assert _book_mail(vb, token2, start2, phone="3331112233").status_code == 200
    ((aid2, *_),) = _appointment(vb, start2, pid2)
    assert _conferme(vb, aid2) == []                                          # ancora una richiesta


def test_c_il_cliente_che_riprogramma_riceve_il_nuovo_orario_e_la_vecchia_conferma_si_ritira(notifiche):
    vb = notifiche
    pid = _property(vb)
    token = _token(vb, pid)
    slots = _slots(vb, token)
    out = _book_mail(vb, token, slots[0]["start_at"]).json()
    chiave = out["manage_url"].rsplit("/", 1)[1]
    ((aid, *_),) = _appointment(vb, slots[0]["start_at"], pid)
    assert [c[1] for c in _conferme(vb, aid)] == ["queued"]
    nuovi = vb["public"].get(f"{PUB}/manage/{chiave}/slots", params={
        "date_from": slots[0]["start_at"],
        "date_to": (datetime.fromisoformat(slots[0]["start_at"]) + timedelta(days=1)).isoformat()}).json()["slots"]
    target = next(s["start_at"] for s in nuovi if s["start_at"] != slots[0]["start_at"])
    assert vb["public"].post(f"{PUB}/manage/{chiave}/reschedule",
                             json={"start_at": target, "key": str(uuid.uuid4())}).status_code == 200
    ((nuovo, *_),) = _appointment(vb, target, pid)
    assert [c[1] for c in _conferme(vb, aid)] == ["cancelled"]                # mai partita, ritirata subito
    ((_m, stato, _r, _i, dest, oggetto, variante, _a),) = _conferme(vb, nuovo)
    assert (stato, variante, dest) == ("queued", "rescheduled", EMAIL) and oggetto.startswith("Nuovo orario:")
