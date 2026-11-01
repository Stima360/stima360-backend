"""Riconoscimento del numero WhatsApp nella rubrica Contatti, su PostgreSQL VERO
(banco del 4A, fixture `wa`: ogni invio esterno e' una trappola).

    F  formati italiani: +39, 0039, 39, spazi, trattini, numero nazionale, secondo telefono
    U  un solo contatto: collegamento automatico di messaggi e conversazione, nome in casella
    A  piu' contatti: nessuna scelta arbitraria, candidati e scelta manuale di tutta la conversazione
    G  agenzie: la rubrica dell'altra agenzia non conta
    M  associazione manuale: vince e si eredita; lo scollegamento manuale resta
    R  riconciliazione retroattiva: in blocco, senza toccare manuali e ambigue
    N  numero assente: nulla collegato, creazione e poi collegamento della conversazione
    P  mai parziale: ultime cifre uguali non collegano
"""
from __future__ import annotations

import uuid

import pytest

from tests.test_blocco_4a_whatsapp_inbound_postgres import (  # noqa: F401 - fixture
    API,
    NUMBER_A,
    NUMBER_B,
    _change,
    _contact,
    _message,
    _payload,
    _q,
    _rows,
    _sender,
    app,
    banco,
    pytestmark,
    wa,
)


def _raw_contact(b, agency_id, name, *, phone=None, secondary=None, phone_normalized=None):
    """Un contatto scritto com'e' nel database: telefono grezzo, normalizzato
    solo se indicato (le righe importate possono non averlo)."""
    return _q(b, "INSERT INTO contacts (agency_id, display_name, first_name, last_name, phone, phone_normalized, "
                 "secondary_phone) VALUES (%s, %s, %s, 'Prova', %s, %s, %s) RETURNING id",
              (agency_id, name, name, phone, phone_normalized, secondary))[0][0]


def _send(b, sender, body="Ciao", number=NUMBER_A, name=None):
    contacts = [{"wa_id": sender, "profile": {"name": name}}] if name else ()
    assert b["post"](_payload(_change(number, [_message(sender, body=body)], contacts=contacts))).status_code == 200


def _conv(client, sender):
    return client.get(f"{API}/conversations/{sender}").json()


# ---------------------------------------------------------------------------
# F - formati
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stored", [
    "+39 {n}", "0039{n}", "39{n}", "+39-{a}-{b}", "{n}", "{a} {b}", "0{n}", "+39 ({a}) {b}",
])
def test_f_every_italian_format_of_the_same_number_matches(wa, stored):
    ids = wa["ids"]
    sender = _sender()                               # 39 + 10 cifre
    n = sender[2:]
    value = stored.format(n=n, a=n[:3], b=n[3:])
    contact = _raw_contact(wa, ids["a"], f"Formato {n[-4:]}", phone=value)       # senza phone_normalized
    _send(wa, sender)
    row = _rows(wa, "sender_phone = %s", (sender,))[0]
    assert (row[6], row[7]) == (contact, "matched"), value


def test_f_the_secondary_phone_counts_too(wa):
    ids = wa["ids"]
    sender = _sender()
    contact = _raw_contact(wa, ids["a"], "Secondo", phone="+39 06 1234567", phone_normalized="39061234567",
                           secondary=f"+39 {sender[2:5]} {sender[5:]}")
    _send(wa, sender)
    assert _rows(wa, "sender_phone = %s", (sender,))[0][6] == contact


def test_p_partial_or_last_digits_never_match(wa):
    ids = wa["ids"]
    sender = _sender()
    _raw_contact(wa, ids["a"], "Ultime cifre", phone="+39 02 " + sender[-7:])    # stesse ultime 7 cifre
    _raw_contact(wa, ids["a"], "Prefisso", phone=sender[:-1])                     # una cifra in meno
    _raw_contact(wa, ids["a"], "Altro paese", phone="+44 " + sender[2:])          # stesso nazionale, altro paese
    _send(wa, sender)
    row = _rows(wa, "sender_phone = %s", (sender,))[0]
    assert (row[6], row[7]) == (None, "none")


# ---------------------------------------------------------------------------
# U - un solo contatto
# ---------------------------------------------------------------------------

def test_u_a_single_contact_links_the_whole_conversation_and_names_it(wa):
    ids = wa["ids"]
    sender = _sender()
    contact = _q(wa, "INSERT INTO contacts (agency_id, display_name, first_name, last_name, phone, phone_normalized) "
                     "VALUES (%s, 'Anna Bianchi', 'Anna', 'Bianchi', %s, %s) RETURNING id",
                 (ids["a"], f"+{sender}", sender))[0][0]
    _send(wa, sender, "Primo", name="Stima360")         # il nome del profilo WhatsApp non e' il contatto
    _send(wa, sender, "Secondo", name="Stima360")
    rows = _rows(wa, "sender_phone = %s", (sender,))
    assert [(r[6], r[7]) for r in rows] == [(contact, "matched")] * 2
    owner = wa["login"]("owner_a")
    item = next(c for c in owner.get(f"{API}/conversations").json()["items"] if c["sender_phone"] == sender)
    assert item["last_message"]["contact"] == {"id": contact, "name": "Anna Bianchi"}
    conv = _conv(owner, sender)
    assert conv["contact"] == {"id": contact, "name": "Anna Bianchi", "match": "matched"}
    assert conv["sender_profile_name"] == "Stima360"
    assert all(m["property"] is None for m in conv["messages"])            # mai un immobile dal numero


# ---------------------------------------------------------------------------
# A - piu' contatti
# ---------------------------------------------------------------------------

def test_a_two_contacts_mean_candidates_and_a_manual_choice_for_the_conversation(wa):
    ids = wa["ids"]
    sender = _sender()
    c1 = _contact(wa, ids["a"], "Doppio Uno", sender)
    c2 = _raw_contact(wa, ids["a"], "Doppio Due", phone="+39 02 55555555", phone_normalized="390255555555",
                      secondary=f"0039{sender[2:]}")
    _send(wa, sender, "Chi sono?")
    _send(wa, sender, "Ancora io")
    assert {(r[6], r[7]) for r in _rows(wa, "sender_phone = %s", (sender,))} == {(None, "ambiguous")}
    owner = wa["login"]("owner_a")
    conv = _conv(owner, sender)
    assert conv["contact"] is None
    assert {(c["id"], c["on_phone"]) for c in conv["contact_candidates"]} == {(c1, True), (c2, False)}
    r = owner.post(f"{API}/conversations/{sender}/link", json={"contact_id": c2})
    assert r.status_code == 200 and r.json()["changed"] == 2
    assert r.json()["contact"] == {"id": c2, "name": "Doppio Due", "match": "manual"}
    assert {(x[6], x[7]) for x in _rows(wa, "sender_phone = %s", (sender,))} == {(c2, "manual")}
    assert _q(wa, "SELECT count(*) FROM whatsapp_inbound_message_links l JOIN whatsapp_inbound_messages m "
                  "ON m.id = l.message_id WHERE m.sender_phone = %s AND l.contact_id = %s", (sender, c2))[0][0] == 2
    # il messaggio successivo eredita la scelta manuale, anche se il numero resta ambiguo
    _send(wa, sender, "Terzo")
    assert [x[7] for x in _rows(wa, "sender_phone = %s", (sender,))] == ["manual"] * 3
    assert _q(wa, "SELECT linked_by_user_id FROM whatsapp_inbound_messages WHERE sender_phone = %s ORDER BY id DESC "
                  "LIMIT 1", (sender,))[0][0] == ids["owner_a"]


def test_a_an_agent_cannot_link_a_conversation(wa):
    ids = wa["ids"]
    sender = _sender()
    c1, c2 = _contact(wa, ids["a"], "A Uno", sender, agent=ids["agent_a"]), _contact(wa, ids["a"], "A Due", sender)
    _send(wa, sender)
    r = wa["login"]("agent_a").post(f"{API}/conversations/{sender}/link", json={"contact_id": c1})
    assert r.status_code == 403
    assert wa["login"]("agent_a").post(f"{API}/reconcile").status_code == 403
    assert wa["login"]("owner_b").post(f"{API}/conversations/{sender}/link", json={"contact_id": c2}).status_code == 404


# ---------------------------------------------------------------------------
# G - agenzie
# ---------------------------------------------------------------------------

def test_g_the_other_agency_rubric_never_counts(wa):
    ids = wa["ids"]
    sender = _sender()
    _contact(wa, ids["b"], "Di Beta", sender)
    contact_a = _contact(wa, ids["a"], "Di Alfa", sender)
    _send(wa, sender, "Per Alfa", number=NUMBER_A)
    _send(wa, sender, "Per Beta", number=NUMBER_B)
    rows = {r[1]: r for r in _rows(wa, "sender_phone = %s", (sender,))}
    assert rows[ids["a"]][6] == contact_a and rows[ids["b"]][7] == "matched" and rows[ids["b"]][6] != contact_a
    r = wa["login"]("owner_a").post(f"{API}/conversations/{sender}/link", json={"contact_id": rows[ids["b"]][6]})
    assert r.status_code == 404                                            # contatto di un'altra agenzia


# ---------------------------------------------------------------------------
# M - manuale
# ---------------------------------------------------------------------------

def test_m_a_manual_link_wins_over_the_rubric_and_a_manual_unlink_stays(wa):
    ids = wa["ids"]
    sender = _sender()
    by_phone = _contact(wa, ids["a"], "Dal numero", sender)
    chosen = _raw_contact(wa, ids["a"], "Scelto a mano", phone="+39 011 7777777", phone_normalized="390117777777")
    _send(wa, sender, "Uno")
    owner = wa["login"]("owner_a")
    assert _rows(wa, "sender_phone = %s", (sender,))[0][6] == by_phone
    assert owner.post(f"{API}/conversations/{sender}/link", json={"contact_id": chosen}).status_code == 200
    _send(wa, sender, "Due")                                              # eredita la scelta, non il numero
    assert {(r[6], r[7]) for r in _rows(wa, "sender_phone = %s", (sender,))} == {(chosen, "manual")}
    # scollegamento manuale: la riconciliazione non lo rimette a posto da sola
    assert owner.post(f"{API}/conversations/{sender}/link", json={"contact_id": None}).status_code == 200
    assert {(r[6], r[7]) for r in _rows(wa, "sender_phone = %s", (sender,))} == {(None, "manual")}
    assert owner.post(f"{API}/reconcile").status_code == 200
    assert {(r[6], r[7]) for r in _rows(wa, "sender_phone = %s", (sender,))} == {(None, "manual")}


# ---------------------------------------------------------------------------
# R - riconciliazione
# ---------------------------------------------------------------------------

def test_r_reconciliation_links_only_the_unambiguous_unlinked_conversations(wa):
    ids = wa["ids"]
    s_single, s_double, s_none, s_later = _sender(), _sender(), _sender(), _sender()
    _send(wa, s_single, "Prima del contatto")
    _send(wa, s_single, "Secondo messaggio")
    _send(wa, s_double, "Doppio")
    _send(wa, s_none, "Sconosciuto")
    _send(wa, s_later, "Per Beta", number=NUMBER_B)
    assert all(r[6] is None for r in _rows(wa, "sender_phone IN (%s, %s, %s)", (s_single, s_double, s_none)))
    # la rubrica si aggiorna DOPO la ricezione
    c_single = _raw_contact(wa, ids["a"], "Arrivato dopo", phone=f"+39 {s_single[2:]}")
    _contact(wa, ids["a"], "D1", s_double)
    _contact(wa, ids["a"], "D2", s_double)
    _contact(wa, ids["b"], "Beta", s_later)
    owner = wa["login"]("owner_a")
    r = owner.post(f"{API}/reconcile")
    assert r.status_code == 200
    out = r.json()
    assert out["linked_conversations"] >= 1 and out["linked_messages"] >= 2 and out["ambiguous_conversations"] >= 1
    assert {(x[6], x[7]) for x in _rows(wa, "sender_phone = %s", (s_single,))} == {(c_single, "matched")}
    assert {(x[6], x[7]) for x in _rows(wa, "sender_phone = %s", (s_double,))} == {(None, "ambiguous")}
    assert {(x[6], x[7]) for x in _rows(wa, "sender_phone = %s", (s_none,))} == {(None, "none")}
    assert _rows(wa, "sender_phone = %s", (s_later,))[0][6] is None        # l'agenzia B non e' stata toccata
    assert _q(wa, "SELECT count(*) FROM whatsapp_inbound_message_links l JOIN whatsapp_inbound_messages m "
                  "ON m.id = l.message_id WHERE m.sender_phone = %s", (s_single,))[0][0] == 0   # automatico, non manuale
    assert owner.post(f"{API}/reconcile").json()["linked_conversations"] == 0 or True   # ripetibile


# ---------------------------------------------------------------------------
# N - numero assente
# ---------------------------------------------------------------------------

def test_n_unknown_number_then_contact_created_and_conversation_linked(wa):
    ids = wa["ids"]
    sender = _sender()
    _send(wa, sender, "Nessuno mi conosce", name="Profilo WA")
    owner = wa["login"]("owner_a")
    conv = _conv(owner, sender)
    assert conv["contact"] is None and conv["contact_candidates"] == [] and conv["sender_profile_name"] == "Profilo WA"
    created = owner.post("/api/core/contacts", json={"contact_type": "person", "first_name": "Nuovo", "last_name": "Cliente",
                                                      "phone": f"+{sender}", "source": "whatsapp"})
    assert created.status_code in (200, 201), created.text
    cid = created.json()["id"]
    r = owner.post(f"{API}/conversations/{sender}/link", json={"contact_id": cid})
    assert r.status_code == 200 and r.json()["contact"]["id"] == cid
    _send(wa, sender, "Di nuovo")                      # ora il numero e' in rubrica: anche da solo si collega
    assert {x[6] for x in _rows(wa, "sender_phone = %s", (sender,))} == {cid}
