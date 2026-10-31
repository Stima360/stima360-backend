"""Casella «Messaggi WhatsApp»: non letti per operatore e stime collegate, su
PostgreSQL VERO (banco del 4A con la migrazione 126). Solo dati sintetici,
ogni invio esterno e' una trappola (fixture `wa`).

    N  non letti: per operatore, avanzano soltanto, nuovi messaggi tornano non letti
    G  segno di lettura: mai su un'altra conversazione o un'altra agenzia (database)
    T  stime collegate: dal contatto collegato o dallo stesso numero, solo dell'agenzia
"""
from __future__ import annotations

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
    _sender,
    app,
    banco,
    pytestmark,
    wa,
)


def _conv(client, sender):
    items = client.get(f"{API}/conversations").json()["items"]
    return next((c for c in items if c["sender_phone"] == sender), None)


def _send(wa, sender, *bodies, number=NUMBER_A):
    assert wa["post"](_payload(_change(number, [_message(sender, body=b) for b in bodies]))).status_code == 200


# ---------------------------------------------------------------------------
# N - non letti
# ---------------------------------------------------------------------------

def test_n_unread_is_per_operator_and_new_messages_are_unread_again(wa):
    owner, admin = wa["login"]("owner_a"), wa["login"]("admin_a")
    sender = _sender()
    _send(wa, sender, "Uno", "Due")
    listing = owner.get(f"{API}/conversations").json()
    assert listing["unread_available"] is True
    assert _conv(owner, sender)["unread"] == 2 and _conv(admin, sender)["unread"] == 2
    r = owner.post(f"{API}/conversations/{sender}/read")
    assert r.status_code == 200 and r.json()["unread"] == 0
    assert _conv(owner, sender)["unread"] == 0
    assert _conv(admin, sender)["unread"] == 2                     # la lettura del titolare non vale per altri
    _send(wa, sender, "Tre")
    assert _conv(owner, sender)["unread"] == 1
    assert owner.post(f"{API}/conversations/{sender}/read").status_code == 200
    assert owner.post(f"{API}/conversations/{sender}/read").status_code == 200    # ripetuta: nessun errore
    assert _conv(owner, sender)["unread"] == 0
    assert _q(wa, "SELECT count(*) FROM whatsapp_inbound_reads WHERE sender_phone = %s", (sender,))[0][0] == 1


def test_n_an_agent_marks_only_what_they_can_see_and_never_other_agencies(wa):
    ids = wa["ids"]
    agent, owner_b = wa["login"]("agent_a"), wa["login"]("owner_b")
    mine, other = _sender(), _sender()
    _contact(wa, ids["a"], "Assegnato", mine, agent=ids["agent_a"])
    _contact(wa, ids["a"], "Di un collega", other, agent=ids["agent2_a"])
    _send(wa, mine, "Per me")
    _send(wa, other, "Per il collega")
    assert _conv(agent, mine)["unread"] == 1 and _conv(agent, other) is None
    assert agent.post(f"{API}/conversations/{other}/read").status_code == 404
    assert owner_b.post(f"{API}/conversations/{mine}/read").status_code == 404
    assert agent.post(f"{API}/conversations/{mine}/read").status_code == 200
    assert _conv(agent, mine)["unread"] == 0
    assert _q(wa, "SELECT count(*) FROM whatsapp_inbound_reads WHERE sender_phone = %s", (other,))[0][0] == 0


# ---------------------------------------------------------------------------
# G - vincoli del database
# ---------------------------------------------------------------------------

def test_g_the_marker_never_points_to_another_conversation_nor_moves_back(wa):
    ids = wa["ids"]
    s1, s2 = _sender(), _sender()
    _send(wa, s1, "Primo", "Secondo")
    _send(wa, s2, "Altro")
    _send(wa, s1, "Di Beta", number=NUMBER_B)
    m1 = [r[0] for r in _q(wa, "SELECT id FROM whatsapp_inbound_messages WHERE sender_phone = %s "
                               "AND agency_id = %s ORDER BY id", (s1, ids["a"]))]
    m2 = _q(wa, "SELECT id FROM whatsapp_inbound_messages WHERE sender_phone = %s", (s2,))[0][0]
    mb = _q(wa, "SELECT id FROM whatsapp_inbound_messages WHERE sender_phone = %s AND agency_id = %s",
            (s1, ids["b"]))[0][0]
    psycopg2 = wa["psycopg2"]

    def run(sql, params):
        conn = psycopg2.connect(wa["dsn"])
        try:
            with conn, conn.cursor() as cur:
                cur.execute(sql, params)
        finally:
            conn.close()

    insert = ("INSERT INTO whatsapp_inbound_reads (agency_id, user_id, sender_phone, last_read_message_id) "
              "VALUES (%s, %s, %s, %s)")
    for bad in (m2, mb):
        with pytest.raises(psycopg2.Error, match="another conversation"):
            run(insert, (ids["a"], ids["owner_a"], s1, bad))
    run(insert, (ids["a"], ids["owner_a"], s1, m1[1]))
    with pytest.raises(psycopg2.Error, match="only forward moves"):
        run("UPDATE whatsapp_inbound_reads SET last_read_message_id = %s WHERE sender_phone = %s",
            (m1[0], s1))


# ---------------------------------------------------------------------------
# T - stime collegate
# ---------------------------------------------------------------------------

def _stima(wa, agency_id, telefono, via):
    return _q(wa, "INSERT INTO stime (agency_id, nome, cognome, telefono, comune, via, civico, data) "
                  "VALUES (%s, 'Nome', 'Sintetico', %s, 'Comune Sintetico', %s, '1', NOW()) RETURNING id",
              (agency_id, telefono, via))[0][0]


def test_t_conversation_lists_the_stime_of_the_contact_or_of_the_same_number(wa):
    ids = wa["ids"]
    sender = _sender()                                     # 39 + 10 cifre
    national = sender[2:]
    contact = _contact(wa, ids["a"], "Con stima", sender)
    by_phone = _stima(wa, ids["a"], f"+39 {national[:3]} {national[3:]}", "Via del Numero")
    by_national = _stima(wa, ids["a"], national, "Via Nazionale")
    by_contact = _stima(wa, ids["a"], "", "Via del Contatto")
    lead = _q(wa, "INSERT INTO leads (contact_id, agency_id) VALUES (%s, %s) RETURNING id", (contact, ids["a"]))[0][0]
    _q(wa, "INSERT INTO lead_stime (lead_id, stima_id) VALUES (%s, %s)", (lead, by_contact))
    _stima(wa, ids["b"], national, "Via di Beta")          # altra agenzia, stesso numero
    _stima(wa, ids["a"], "3000000000", "Via Estranea")    # altro numero
    _send(wa, sender, "Ho ricevuto la stima")
    conv = wa["login"]("owner_a").get(f"{API}/conversations/{sender}").json()
    got = {s["id"]: s["match"] for s in conv["stime"]}
    assert got == {by_phone: "phone", by_national: "phone", by_contact: "contact"}
    assert all(s["comune"] == "Comune Sintetico" for s in conv["stime"])


# ---------------------------------------------------------------------------
# Z - codice nuovo su un database ancora senza la 126 (ultimo test del file:
#     rimuove la tabella e la ricrea con la migrazione stessa)
# ---------------------------------------------------------------------------

def test_z_without_migration_126_the_inbox_works_and_unread_is_unavailable(wa):
    from pathlib import Path

    owner = wa["login"]("owner_a")
    sender = _sender()
    _send(wa, sender, "Prima della 126")
    up = (Path(__file__).resolve().parents[1] / "migrations" / "126_whatsapp_inbound_reads.sql").read_text()
    down = (Path(__file__).resolve().parents[1] / "migrations" / "126_whatsapp_inbound_reads_down.sql").read_text()
    _q(wa, down)
    try:
        listing = owner.get(f"{API}/conversations").json()
        assert listing["unread_available"] is False
        assert _conv(owner, sender)["unread"] is None
        assert owner.get(f"{API}/conversations/{sender}").status_code == 200
        r = owner.post(f"{API}/conversations/{sender}/read")
        assert r.status_code == 503 and r.json()["detail"]["code"] == "READS_NOT_INSTALLED"
        _send(wa, sender, "Ricezione invariata")
        assert len(owner.get(f"{API}/conversations/{sender}").json()["messages"]) == 2
    finally:
        _q(wa, up)
    assert owner.post(f"{API}/conversations/{sender}/read").status_code == 200
