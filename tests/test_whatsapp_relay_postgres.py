"""Inoltro dal ricevitore WhatsApp esistente al webhook del CRM, su PostgreSQL VERO.

Stesso banco e stessa app del 4A (`tests/test_blocco_4a_whatsapp_inbound_postgres.py`):
il CRM non ha un secondo ricevitore, l'inoltro entra in `POST /webhook/whatsapp`
con una firma propria e passa da parser, instradamento e deduplicazione del 4A.
Solo numeri sintetici. Ogni invio esterno e' una trappola.

    R  ricezione inoltrata, contatto collegato da solo, visibile nella casella
    D  stesso messaggio inoltrato due volte e consegnato anche da Meta: una riga
    F  filtro dei mittenti: gli altri numeri non vengono salvati
    X  inoltri non autenticati o non configurati: rifiutati, nulla salvato
    I  isolamento delle agenzie: l'inoltro non sceglie l'agenzia
"""
from __future__ import annotations

import json
import time
import uuid

import pytest

from inbound_messages import relay
from tests.test_blocco_4a_whatsapp_inbound_postgres import (  # noqa: F401 - fixture
    API,
    NUMBER_A,
    NUMBER_B,
    NUMBER_UNKNOWN,
    _change,
    _contact,
    _message,
    _payload,
    _q,
    _rows,
    _sender,
    _sign,
    app,
    banco,
    pytestmark,
    wa,
)

RELAY_SECRET = "segreto-sintetico-dell-inoltro-lungo-almeno-32"


@pytest.fixture
def rl(wa, monkeypatch):
    monkeypatch.setenv(relay.RELAY_SECRET_ENV, RELAY_SECRET)
    monkeypatch.setenv(relay.ALLOWED_SENDERS_ENV, "")
    client = wa["login"]("owner_a")

    def relayed(payload, *, raw=None, secret=RELAY_SECRET, ts=None, headers=None, signed_body=None):
        body = raw if raw is not None else json.dumps(payload).encode()
        stamp = str(int(time.time()) if ts is None else ts)
        h = {"Content-Type": "application/json", relay.TIMESTAMP_HEADER: stamp,
             relay.SIGNATURE_HEADER: relay.signature(secret, stamp, body if signed_body is None else signed_body)}
        h.update(headers or {})
        return client.post("/webhook/whatsapp", content=body, headers={k: v for k, v in h.items() if v is not None})

    return {**wa, "relayed": relayed}


def _counts(b):
    return (_q(b, "SELECT count(*) FROM whatsapp_inbound_messages")[0][0],
            _q(b, "SELECT count(*) FROM whatsapp_webhook_events")[0][0])


# ---------------------------------------------------------------------------
# R - ricezione, associazione automatica, casella
# ---------------------------------------------------------------------------

def test_r_relayed_reply_is_stored_linked_and_shown_in_the_inbox(rl, monkeypatch):
    ids = rl["ids"]
    sender = _sender()
    monkeypatch.setenv(relay.ALLOWED_SENDERS_ENV, sender)
    contact = _contact(rl, ids["a"], "Cliente Sintetico", sender)
    mid = f"wamid.relay.{uuid.uuid4().hex}"
    payload = _payload(_change(NUMBER_A, [_message(sender, body="Grazie per la stima", mid=mid)],
                               contacts=[{"wa_id": sender, "profile": {"name": "Cliente Sintetico"}}]))
    r = rl["relayed"](payload)
    assert r.status_code == 200 and r.json() == {"ok": True}
    row = _rows(rl, "provider_message_id = %s", (mid,))
    assert len(row) == 1 and row[0][1] == ids["a"] and row[0][2] == "routed"
    assert row[0][6] == contact and row[0][7] == "matched"
    inbox = rl["login"]("owner_a").get(f"{API}/conversations").json()
    item = next(c for c in inbox["items"] if c["sender_phone"] == sender)
    assert item["last_message"]["text"] == "Grazie per la stima"
    assert item["last_message"]["contact"] == {"id": contact, "name": "Cliente Sintetico"}
    conv = rl["login"]("owner_a").get(f"{API}/conversations/{sender}").json()
    assert [m["text"] for m in conv["messages"]] == ["Grazie per la stima"]


# ---------------------------------------------------------------------------
# D - duplicati fra inoltro e consegna diretta
# ---------------------------------------------------------------------------

def test_d_relay_retries_and_a_direct_meta_delivery_keep_one_row(rl, monkeypatch):
    sender = _sender()
    monkeypatch.setenv(relay.ALLOWED_SENDERS_ENV, sender)
    mid = f"wamid.dup.{uuid.uuid4().hex}"
    payload = _payload(_change(NUMBER_A, [_message(sender, body="Una volta sola", mid=mid)]))
    raw = json.dumps(payload).encode()
    for _ in range(3):
        assert rl["relayed"](None, raw=raw).status_code == 200
    assert rl["post"](None, raw=raw).status_code == 200            # stessa consegna firmata da Meta
    assert len(_rows(rl, "provider_message_id = %s", (mid,))) == 1


# ---------------------------------------------------------------------------
# F - filtro dei mittenti
# ---------------------------------------------------------------------------

def test_f_only_allowed_senders_are_stored_from_a_mixed_payload(rl, monkeypatch):
    allowed, other = _sender(), _sender()
    monkeypatch.setenv(relay.ALLOWED_SENDERS_ENV, f"+{allowed[:2]} {allowed[2:]}")   # spazi e + ammessi
    out_id = f"wamid.out.{uuid.uuid4().hex}"
    payload = _payload(
        _change(NUMBER_A, [_message(allowed, body="Ammesso"), _message(other, body="Cliente reale")],
                statuses=[{"id": out_id, "status": "read", "timestamp": "1761800001", "recipient_id": other}],
                contacts=[{"wa_id": other, "profile": {"name": "Nome Reale"}}]),
        {"field": "account_update", "value": {"event": "VERIFIED_ACCOUNT"}})
    events_before = _counts(rl)[1]
    assert rl["relayed"](payload).status_code == 200
    assert [x[5] for x in _rows(rl, "sender_phone = %s", (allowed,))] == ["Ammesso"]
    assert _rows(rl, "sender_phone = %s", (other,)) == []
    assert _q(rl, "SELECT count(*) FROM whatsapp_inbound_messages WHERE sender_profile_name = 'Nome Reale'")[0][0] == 0
    assert _q(rl, "SELECT count(*) FROM whatsapp_webhook_events WHERE provider_message_id = %s", (out_id,))[0][0] == 0
    assert _counts(rl)[1] == events_before                          # nemmeno l'evento di account


def test_f_a_payload_of_only_excluded_senders_touches_nothing(rl, monkeypatch):
    monkeypatch.setenv(relay.ALLOWED_SENDERS_ENV, _sender())
    before = _counts(rl)
    payload = _payload(_change(NUMBER_A, [_message(_sender(), body="Cliente reale")]))
    assert rl["relayed"](payload).status_code == 200
    assert _counts(rl) == before


def test_f_the_filter_also_applies_to_a_direct_meta_delivery(rl, monkeypatch):
    allowed, other = _sender(), _sender()
    monkeypatch.setenv(relay.ALLOWED_SENDERS_ENV, allowed)
    assert rl["post"](_payload(_change(NUMBER_A, [_message(allowed), _message(other)]))).status_code == 200
    assert len(_rows(rl, "sender_phone = %s", (allowed,))) == 1 and _rows(rl, "sender_phone = %s", (other,)) == []


def test_f_without_the_variable_a_direct_meta_delivery_keeps_every_sender(rl, monkeypatch):
    monkeypatch.delenv(relay.ALLOWED_SENDERS_ENV)
    s1, s2 = _sender(), _sender()
    assert rl["post"](_payload(_change(NUMBER_A, [_message(s1), _message(s2)]))).status_code == 200
    assert len(_rows(rl, "sender_phone IN (%s, %s)", (s1, s2))) == 2


# ---------------------------------------------------------------------------
# X - inoltri rifiutati
# ---------------------------------------------------------------------------

def test_x_unauthenticated_or_misconfigured_relays_store_nothing(rl, monkeypatch):
    sender = _sender()
    monkeypatch.setenv(relay.ALLOWED_SENDERS_ENV, sender)
    payload = _payload(_change(NUMBER_A, [_message(sender, body="Non deve entrare")]))
    raw = json.dumps(payload).encode()
    before = _counts(rl)
    stale = int(time.time()) - relay.MAX_SKEW_SECONDS - 5
    future = int(time.time()) + relay.MAX_SKEW_SECONDS + 5
    cases = [
        rl["relayed"](None, raw=raw, secret="un-altro-segreto-lungo-almeno-trentadue-car"),
        rl["relayed"](None, raw=raw, ts=stale),
        rl["relayed"](None, raw=raw, ts=future),
        rl["relayed"](None, raw=raw, headers={relay.SIGNATURE_HEADER: None}),
        rl["relayed"](None, raw=raw, headers={relay.TIMESTAMP_HEADER: None}),
        rl["relayed"](None, raw=raw, headers={relay.TIMESTAMP_HEADER: "adesso"}),
        rl["relayed"](None, raw=raw.replace(b"Non deve", b"Alterato"), signed_body=raw),   # corpo alterato
        rl["relayed"](None, raw=raw, headers={"X-Hub-Signature-256": _sign(raw)}),   # due firme insieme
    ]
    assert [r.status_code for r in cases] == [403] * len(cases)
    # senza firma ne' inoltro: la firma Meta resta obbligatoria
    no_auth = rl["login"]("owner_a").post("/webhook/whatsapp", content=raw,
                                          headers={"Content-Type": "application/json"})
    assert no_auth.status_code == 403
    monkeypatch.setenv(relay.RELAY_SECRET_ENV, "corto")
    assert rl["relayed"](None, raw=raw, secret="corto").status_code == 503
    monkeypatch.delenv(relay.RELAY_SECRET_ENV)
    assert rl["relayed"](None, raw=raw).status_code == 503
    monkeypatch.setenv(relay.RELAY_SECRET_ENV, RELAY_SECRET)
    monkeypatch.delenv(relay.ALLOWED_SENDERS_ENV)
    assert rl["relayed"](None, raw=raw).status_code == 503         # inoltro senza filtro configurato
    assert _counts(rl) == before


def test_x_a_relay_cannot_use_the_meta_secret(rl, monkeypatch):
    from tests.test_blocco_4a_whatsapp_inbound_postgres import SECRET

    sender = _sender()
    monkeypatch.setenv(relay.ALLOWED_SENDERS_ENV, sender)
    raw = json.dumps(_payload(_change(NUMBER_A, [_message(sender)]))).encode()
    assert rl["relayed"](None, raw=raw, secret=SECRET).status_code == 403
    assert _rows(rl, "sender_phone = %s", (sender,)) == []


# ---------------------------------------------------------------------------
# I - isolamento delle agenzie
# ---------------------------------------------------------------------------

def test_i_routing_stays_on_the_recipient_number_not_on_the_relay(rl, monkeypatch):
    ids = rl["ids"]
    sender = _sender()
    monkeypatch.setenv(relay.ALLOWED_SENDERS_ENV, sender)
    payload = _payload(_change(NUMBER_A, [_message(sender, body="Per Alfa")]),
                       _change(NUMBER_B, [_message(sender, body="Per Beta")]),
                       _change(NUMBER_UNKNOWN, [_message(sender, body="Numero sconosciuto")]))
    assert rl["relayed"](payload, headers={"X-Agency-Id": str(ids["b"])}).status_code == 200
    rows = {x[5]: x for x in _rows(rl, "sender_phone = %s", (sender,))}
    assert rows["Per Alfa"][1] == ids["a"] and rows["Per Beta"][1] == ids["b"]
    assert rows["Numero sconosciuto"][1] is None and rows["Numero sconosciuto"][2] == "unrouted"
    a = rl["login"]("owner_a").get(f"{API}/conversations/{sender}").json()
    b = rl["login"]("owner_b").get(f"{API}/conversations/{sender}").json()
    assert [m["text"] for m in a["messages"]] == ["Per Alfa"]
    assert [m["text"] for m in b["messages"]] == ["Per Beta"]


def test_i_an_agent_sees_a_relayed_reply_only_for_an_assigned_contact(rl, monkeypatch):
    ids = rl["ids"]
    mine, other = _sender(), _sender()
    monkeypatch.setenv(relay.ALLOWED_SENDERS_ENV, f"{mine},{other}")
    _contact(rl, ids["a"], "Assegnato", mine, agent=ids["agent_a"])
    _contact(rl, ids["a"], "Di un collega", other, agent=ids["agent2_a"])
    assert rl["relayed"](_payload(_change(NUMBER_A, [_message(mine), _message(other)]))).status_code == 200
    agent = rl["login"]("agent_a")
    seen = {c["sender_phone"] for c in agent.get(f"{API}/conversations").json()["items"]}
    assert mine in seen and other not in seen
    assert agent.get(f"{API}/conversations/{other}").status_code == 404
