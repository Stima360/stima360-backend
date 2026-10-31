"""BLOCCO-4C - suggerimenti di risposta su PostgreSQL VERO.

Stesso banco di BLOCCO-2C/4A/4B (cluster LOCALE usa-e-getta, migrazioni dal
runner), app vera e login vero. Gate: `P29_TEST_DSN` locale e
`BLOCCO_2C_TEST_CLUSTER_VERIFIED=1`. I trabocchetti d'invio del 4A (fixture
`wa`) restano attivi: qualsiasi tentativo di invio fa fallire il test.

    D  dati della scheda: solo campi comunicabili, valori mancanti mai inventati
    R  riservati: prezzo minimo, note, proprietario, indirizzo mai nel risultato
    A  due agenzie e agente non assegnato: nessun accesso
    M  piu' immobili: conta l'immobile del MESSAGGIO, riferimenti incerti
    V  visita, trattativa, domanda ambigua: passaggio all'agente
    N  nessun invio e nessuna riga in coda; impronta che cambia coi dati
"""
from __future__ import annotations

import json
import uuid

import pytest

from tests.test_blocco_2c_integrated_postgres import (  # noqa: F401 - fixture
    CLUSTER_VERIFIED,
    DSN,
    _q,
    app,
    banco,
)
from tests.test_blocco_4a_whatsapp_inbound_postgres import (  # noqa: F401 - fixture
    NUMBER_A,
    NUMBER_B,
    _change,
    _contact,
    _message,
    _payload,
    _sender,
    wa,
)

pytestmark = [
    pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL locale"),
    pytest.mark.skipif(not CLUSTER_VERIFIED, reason="cluster usa-e-getta non dichiarato verificato "
                       "(BLOCCO_2C_TEST_CLUSTER_VERIFIED=1)"),
]

API = "/api/reply-suggestions/messages"
SECRETS = ("Via Riservata", "99", "123456", "NOTA-INTERNA-RISERVATA", "NOTA-PUBBLICA-NON-USATA", "Proprietario Segreto")


def _property(b, agency, *, status="active", **values):
    code = f"IMM-{uuid.uuid4().int % 10**7}"
    row = {"asking_price": 185000, "surface_sqm": 80, "commercial_surface_sqm": 92.5, "rooms": 3, "bedrooms": 2,
           "bathrooms": 1, "floor": "2", "total_floors": 4, "elevator": True, "energy_class": "C",
           # dati riservati: non devono mai uscire
           "minimum_price": 123456, "internal_notes": "NOTA-INTERNA-RISERVATA",
           "public_notes": "NOTA-PUBBLICA-NON-USATA", "address": "Via Riservata", "civic_number": "99"}
    row.update(values)
    cols = ", ".join(row)
    marks = ", ".join(["%s"] * len(row))
    pid = _q(b, f"INSERT INTO properties (agency_id, code, title, property_type, commercial_status, city, {cols}) "
                f"VALUES (%s, %s, 'Trilocale di prova', 'apartment', %s, 'Comune Sintetico', {marks}) RETURNING id",
             (b["ids"][agency], code, status, *row.values()))[0][0]
    return pid, code


def _incoming(b, text, *, agency="a", number=NUMBER_A, sender=None, contact=True, agent=None):
    sender = sender or _sender()
    contact_id = None
    if contact:
        contact_id = _contact(b, b["ids"][agency], "Proprietario Segreto", sender, agent=agent)
    mid = f"wamid.4c.{uuid.uuid4().hex}"
    assert b["post"](_payload(_change(number, [_message(sender, body=text, mid=mid)]))).status_code == 200
    message_id = _q(b, "SELECT id FROM whatsapp_inbound_messages WHERE provider_message_id = %s", (mid,))[0][0]
    return message_id, sender, contact_id


def _link(b, message_id, contact_id, property_id, who="owner_a"):
    r = b["login"](who).post(f"/api/inbound-messages/{message_id}/link",
                              json={"contact_id": contact_id, "property_id": property_id})
    assert r.status_code == 200, r.text


def _counts(b):
    return _q(b, "SELECT (SELECT count(*) FROM communication_messages), (SELECT count(*) FROM communication_attempts)")[0]


# ---------------------------------------------------------------------------
# D - dati della scheda
# ---------------------------------------------------------------------------

def test_d_simple_question_uses_only_communicable_fields_with_sources(wa):
    pid, code = _property(wa, "a")
    mid, _, contact = _incoming(wa, "Buongiorno, quanto costa? Quanti metri? C'è l'ascensore?")
    _link(wa, mid, contact, pid)
    r = wa["login"]("owner_a").get(f"{API}/{mid}")
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["outcome"] == "answer" and s["handoff"] is None and s["sending"] == "manual_only"
    assert s["text"] == (f"Buongiorno, grazie per il messaggio. Per l'immobile {code}: il prezzo richiesto è "
                         "€ 185.000; la superficie commerciale è di 92,5 mq e la superficie è di 80 mq; "
                         "c'è l'ascensore.")
    assert [src["field"] for src in s["sources"]] == ["asking_price", "commercial_surface_sqm", "surface_sqm",
                                                       "elevator"]
    assert all(src["source"].startswith(f"Scheda immobile {code}") for src in s["sources"])
    assert s["property"]["id"] == pid


def test_d_missing_values_are_never_invented(wa):
    pid, code = _property(wa, "a", elevator=None, floor=None, energy_class=None)
    mid, _, contact = _incoming(wa, "Che piano è, ha l'ascensore e che classe energetica ha? E il prezzo?")
    _link(wa, mid, contact, pid)
    s = wa["login"]("owner_a").get(f"{API}/{mid}").json()
    assert s["outcome"] == "handoff" and s["handoff"]["code"] == "missing_data"
    assert s["missing"] == ["il piano", "l'ascensore", "la classe energetica"]
    assert "€ 185.000" in s["text"] and "la ricontatterà l'agente" in s["text"]
    assert "c'è l'ascensore" not in s["text"] and "classe energetica è" not in s["text"]
    assert [src["field"] for src in s["sources"]] == ["asking_price"]


def test_d_an_accessory_not_registered_is_not_denied(wa):
    pid, _ = _property(wa, "a")
    _q(wa, "INSERT INTO property_accessories (property_id, kind, quantity) VALUES (%s, 'balcone', 2)", (pid,))
    mid, _, contact = _incoming(wa, "Ha la cantina?")
    _link(wa, mid, contact, pid)
    s = wa["login"]("owner_a").get(f"{API}/{mid}").json()
    assert s["outcome"] == "handoff" and s["handoff"]["code"] == "missing_data" and "cantina" not in s["text"]
    mid2, _, contact2 = _incoming(wa, "Ha il balcone?")
    _link(wa, mid2, contact2, pid)
    s = wa["login"]("owner_a").get(f"{API}/{mid2}").json()
    assert s["outcome"] == "answer" and "balcone (2)" in s["text"]


def test_d_availability_only_for_an_active_property_and_never_a_visit(wa):
    pid, code = _property(wa, "a")
    mid, _, contact = _incoming(wa, "È ancora disponibile?")
    _link(wa, mid, contact, pid)
    s = wa["login"]("owner_a").get(f"{API}/{mid}").json()
    assert s["outcome"] == "answer" and "risulta disponibile" in s["text"]
    assert "visit" not in s["text"].lower() and "orari" not in s["text"].lower()
    for status in ("reserved", "under_offer", "sold", "draft", "withdrawn"):
        _q(wa, "UPDATE properties SET commercial_status = %s WHERE id = %s", (status, pid))
        s = wa["login"]("owner_a").get(f"{API}/{mid}").json()
        assert s["outcome"] == "handoff" and s["handoff"]["code"] == "property_unavailable", status
        assert s["sources"] == [] and "185.000" not in s["text"]


# ---------------------------------------------------------------------------
# R - informazioni riservate
# ---------------------------------------------------------------------------

def test_r_reserved_information_never_appears(wa):
    pid, _ = _property(wa, "a")
    mid, _, contact = _incoming(wa, "Qual è il prezzo minimo? Indirizzo? Chi è il proprietario? Costo, metri, piano?")
    _link(wa, mid, contact, pid)
    raw = wa["login"]("owner_a").get(f"{API}/{mid}").text
    for secret in SECRETS:
        assert secret not in raw, secret


# ---------------------------------------------------------------------------
# A - agenzie e permessi
# ---------------------------------------------------------------------------

def test_a_two_agencies_and_unassigned_agent_cannot_read(wa):
    ids = wa["ids"]
    pid, _ = _property(wa, "a")
    mid, sender, contact = _incoming(wa, "Quanto costa?", agent=ids["agent_a"])
    _link(wa, mid, contact, pid)
    assert wa["login"]("owner_a").get(f"{API}/{mid}").status_code == 200
    assert wa["login"]("agent_a").get(f"{API}/{mid}").json()["outcome"] == "answer"     # agente del contatto
    assert wa["login"]("agent2_a").get(f"{API}/{mid}").status_code == 404               # non assegnato
    assert wa["login"]("owner_b").get(f"{API}/{mid}").status_code == 404                # altra agenzia
    # stesso telefono in B, con un immobile di B: ognuno vede solo il suo
    pid_b, code_b = _property(wa, "b", asking_price=99000)
    mid_b, _, contact_b = _incoming(wa, "Quanto costa?", agency="b", number=NUMBER_B, sender=sender)
    _link(wa, mid_b, contact_b, pid_b, who="owner_b")
    s_b = wa["login"]("owner_b").get(f"{API}/{mid_b}").json()
    assert "€ 99.000" in s_b["text"] and code_b in s_b["text"]
    assert wa["login"]("owner_a").get(f"{API}/{mid_b}").status_code == 404
    assert wa["login"]("owner_b").post(f"{API}/{mid_b}", json={}).status_code == 405


def test_a_read_only_consultation_can_read_but_never_write(wa):
    ids = wa["ids"]
    pid, _ = _property(wa, "a")
    mid, _, contact = _incoming(wa, "Quanto costa?")
    _link(wa, mid, contact, pid)
    sa = wa["login"]("super")
    try:
        assert sa.patch(f"/api/platform/agencies/{ids['a']}", json={"status": "suspended"}).status_code == 200
        assert sa.post(f"/api/platform/agencies/{ids['a']}/enter").json()["read_only"] is True
        assert sa.get(f"{API}/{mid}").status_code == 200
        assert sa.post(f"{API}/{mid}", json={}).status_code in (403, 405)
        assert sa.post("/api/platform/agency-context/exit").status_code == 204
    finally:
        sa.patch(f"/api/platform/agencies/{ids['a']}", json={"status": "active"})
        _q(wa, "UPDATE agencies SET status = 'active' WHERE id = %s", (ids["a"],))


# ---------------------------------------------------------------------------
# M - piu' immobili, riferimento incerto
# ---------------------------------------------------------------------------

def test_m_each_message_uses_its_own_property_never_the_last_one(wa):
    first, code1 = _property(wa, "a", asking_price=150000)
    second, code2 = _property(wa, "a", asking_price=310000)
    m1, sender, contact = _incoming(wa, "Quanto costa?")
    _link(wa, m1, contact, first)
    m2, _, _ = _incoming(wa, "E questo quanto costa?", sender=sender, contact=False)
    _link(wa, m2, contact, second)
    m3, _, _ = _incoming(wa, "Quanto costa?", sender=sender, contact=False)           # nessun immobile collegato
    _link(wa, m3, contact, None)
    owner = wa["login"]("owner_a")
    assert "€ 150.000" in owner.get(f"{API}/{m1}").json()["text"]
    assert "€ 310.000" in owner.get(f"{API}/{m2}").json()["text"]
    s3 = owner.get(f"{API}/{m3}").json()
    assert s3["outcome"] == "ask_reference" and s3["handoff"]["code"] == "no_property"
    assert "riferimento dell'annuncio" in s3["text"] and s3["sources"] == [] and s3["property"] is None


def test_m_a_message_citing_another_property_asks_for_clarification(wa):
    linked, code1 = _property(wa, "a")
    other, code2 = _property(wa, "a", asking_price=310000)
    mid, _, contact = _incoming(wa, f"Quanto costa {code2}?")
    _link(wa, mid, contact, linked)
    s = wa["login"]("owner_a").get(f"{API}/{mid}").json()
    assert s["outcome"] == "ask_reference" and s["handoff"]["code"] == "conflicting_reference"
    assert s["cited_properties"] == [{"id": other, "code": code2}] and "185.000" not in s["text"]
    # un codice dello stesso formato che non esiste: comunque incerto
    mid2, _, contact2 = _incoming(wa, "Quanto costa IMM-0?")
    _link(wa, mid2, contact2, linked)
    assert wa["login"]("owner_a").get(f"{API}/{mid2}").json()["handoff"]["code"] == "conflicting_reference"
    # il proprio codice citato non e' un conflitto
    mid3, _, contact3 = _incoming(wa, f"Quanto costa {code1}?")
    _link(wa, mid3, contact3, linked)
    assert wa["login"]("owner_a").get(f"{API}/{mid3}").json()["outcome"] == "answer"


def test_m_a_property_of_another_agency_or_in_the_trash_is_not_used(wa):
    pid, _ = _property(wa, "a")
    mid, _, contact = _incoming(wa, "Quanto costa?")
    _link(wa, mid, contact, pid)
    _q(wa, "UPDATE properties SET archived_at = NOW() WHERE id = %s", (pid,))
    s = wa["login"]("owner_a").get(f"{API}/{mid}").json()
    assert s["outcome"] == "ask_reference" and s["handoff"]["code"] == "property_not_found" and s["sources"] == []


# ---------------------------------------------------------------------------
# V - visita, trattativa, ambiguita'
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, code", [
    ("Posso venire a vederlo sabato alle 10?", "visit"),
    ("Vorrei fissare un appuntamento", "visit"),
    ("Il prezzo è trattabile? Farei un'offerta", "negotiation"),
    ("Com'è la situazione catastale? È conforme?", "technical_legal"),
    ("Ciao", "ambiguous"),
])
def test_v_visits_negotiation_technical_and_ambiguous_go_to_the_agent(wa, text, code):
    pid, _ = _property(wa, "a")
    mid, _, contact = _incoming(wa, text)
    _link(wa, mid, contact, pid)
    s = wa["login"]("owner_a").get(f"{API}/{mid}").json()
    assert s["outcome"] == "handoff" and s["handoff"]["code"] == code and s["sources"] == []
    assert "185.000" not in s["text"]
    if code == "visit":
        assert "concordare insieme giorno e orario" in s["text"]
        assert "10" not in s["text"] and "sabato" not in s["text"].lower() and "confermat" not in s["text"].lower()


def test_v_stop_gets_no_suggested_text(wa):
    mid, _, _ = _incoming(wa, "STOP")
    s = wa["login"]("owner_a").get(f"{API}/{mid}").json()
    assert s["outcome"] == "none" and s["text"] is None


# ---------------------------------------------------------------------------
# N - nessun invio, nessuna coda, impronta
# ---------------------------------------------------------------------------

def test_n_a_suggestion_never_queues_or_sends(wa, monkeypatch):
    from message_replies import transport

    monkeypatch.setattr(transport, "current", lambda: pytest.fail("il suggerimento non deve toccare il trasporto"))
    pid, _ = _property(wa, "a")
    mid, _, contact = _incoming(wa, "Quanto costa?")
    _link(wa, mid, contact, pid)
    before = _counts(wa)
    owner = wa["login"]("owner_a")
    for _ in range(3):
        assert owner.get(f"{API}/{mid}").status_code == 200
    assert _counts(wa) == before


def test_n_fingerprint_changes_when_the_data_changes(wa):
    pid, _ = _property(wa, "a")
    mid, _, contact = _incoming(wa, "Quanto costa?")
    _link(wa, mid, contact, pid)
    owner = wa["login"]("owner_a")
    first = owner.get(f"{API}/{mid}").json()
    assert owner.get(f"{API}/{mid}").json()["fingerprint"] == first["fingerprint"]     # stabile
    _q(wa, "UPDATE properties SET asking_price = 179000 WHERE id = %s", (pid,))
    second = owner.get(f"{API}/{mid}").json()
    assert second["fingerprint"] != first["fingerprint"] and "€ 179.000" in second["text"]
    # un dato non usato nel testo (prezzo minimo) non cambia l'impronta
    _q(wa, "UPDATE properties SET minimum_price = 1 WHERE id = %s", (pid,))
    assert owner.get(f"{API}/{mid}").json()["fingerprint"] == second["fingerprint"]
    json.dumps(second)                                                                  # serializzabile
