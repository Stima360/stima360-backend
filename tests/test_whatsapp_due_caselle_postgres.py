"""WHATSAPP DUE CASELLE (fase A) su PostgreSQL VERO - banco del 4A (fixture
`wa`: due agenzie, migrazioni dal runner compresa la 130, ogni invio esterno
e' una trappola). Un secondo numero ricevente (NUMBER_A2) instradato
all'agenzia A, oltre a NUMBER_A.

    D1  stesso cliente su due numeri: due conversazioni, un solo contatto, non letti separati,
        la rotta senza `inbox` risponde 409 quando il cliente ha scritto a due numeri
    D2  la risposta parte dal numero che ha ricevuto (trasporto finto): casella per casella,
        e le risposte di una casella non compaiono nell'altra
    D3  casella 2 riservata: la vede il titolare, non un amministratore ne' un agente finche'
        non sono autorizzati; configura solo il titolare; i membri per l'autorizzazione
    D4  automazioni: sulla casella senza automazioni il motore registra «skipped» con il
        motivo dedicato, sulla casella 1 decide come prima; pausa per casella
    D5  isolamento fra agenzie: l'agenzia B non vede ne' configura i numeri di A
    D6  numero 1 sui dati esistenti: senza alcuna riga di casella tutto funziona come prima
    D7  token per casella: il trasporto usa WHATSAPP_TOKEN__<numero> se c'e', altrimenti WHATSAPP_TOKEN
"""
from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone

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

NUMBER_A2 = "900000000000003"
INBOXES = "/api/whatsapp-inboxes"
REPLIES = "/api/message-replies/conversations"
AUTO = "/api/auto-replies"


class FakeTransport:
    def __init__(self):
        self.calls, self.lock = [], threading.Lock()

    def send(self, phone_number_id, to, text):
        from message_replies.transport import SendResult

        with self.lock:
            self.calls.append((phone_number_id, to, text))
        return SendResult("accepted", provider_message_id=f"wamid.out.{uuid.uuid4().hex}")


@pytest.fixture
def dc(wa, monkeypatch):
    """Il banco del 4A con un secondo numero per l'agenzia A e il trasporto finto."""
    from message_replies import transport

    ids = wa["ids"]
    monkeypatch.setenv("WHATSAPP_INBOUND_ROUTES",
                       f"{NUMBER_A}:{ids['a']}, {NUMBER_A2}:{ids['a']}, {NUMBER_B}:{ids['b']}")
    fake = FakeTransport()
    monkeypatch.setenv("WHATSAPP_OUTBOUND_MODE", "meta")
    monkeypatch.setenv("WHATSAPP_TOKEN", "token-sintetico-non-reale")
    monkeypatch.setenv("WHATSAPP_GRAPH_VERSION", "v99.0")
    monkeypatch.setenv("WHATSAPP_OUTBOUND_ALLOWED_RECIPIENTS", "*")
    monkeypatch.setattr(transport, "current", lambda: fake)
    # nessuna casella configurata all'inizio: il numero 1 di oggi
    _q(wa, "DELETE FROM whatsapp_inbox_access")
    _q(wa, "DELETE FROM whatsapp_inboxes")
    return {**wa, "fake": fake}


def _now_ts():
    return str(int(datetime.now(timezone.utc).timestamp()))


def _send(b, sender, body, number):
    assert b["post"](_payload(_change(number, [_message(sender, body=body, ts=_now_ts())]))).status_code == 200


def _convs(client, sender, **params):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    items = client.get(f"{API}/conversations{'?' + q if q else ''}").json()["items"]
    return [c for c in items if c["sender_phone"] == sender]


def _reply(client, sender, number, text="Buongiorno, la richiamo."):
    return client.post(f"{REPLIES}/{sender}?inbox={number}",
                       json={"text": text, "client_request_id": str(uuid.uuid4()), "confirm": True})


def _inbox2(b, *, access="restricted", automations=False):
    owner = b["login"]("owner_a")
    r = owner.put(f"{INBOXES}/{NUMBER_A2}", json={"label": "Numero personale", "kind": "coexistence",
                                                 "access_mode": access, "automations_enabled": automations})
    assert r.status_code == 200, r.text
    return owner, r.json()


# ---------------------------------------------------------------------------
# D1 - due conversazioni, un contatto, non letti separati
# ---------------------------------------------------------------------------

def test_d1_same_customer_on_two_numbers_has_two_conversations_and_one_contact(dc):
    ids = dc["ids"]
    sender = _sender()
    contact = _contact(dc, ids["a"], "Cliente Doppio", sender)
    _send(dc, sender, "Scrivo al numero 1", NUMBER_A)
    _send(dc, sender, "Scrivo al numero 2", NUMBER_A2)
    _send(dc, sender, "Ancora al numero 2", NUMBER_A2)
    owner, admin = dc["login"]("owner_a"), dc["login"]("admin_a")
    convs = _convs(owner, sender)
    assert sorted(c["phone_number_id"] for c in convs) == sorted([NUMBER_A, NUMBER_A2])
    by_number = {c["phone_number_id"]: c for c in convs}
    assert by_number[NUMBER_A]["messages"] == 1 and by_number[NUMBER_A2]["messages"] == 2
    assert by_number[NUMBER_A]["unread"] == 1 and by_number[NUMBER_A2]["unread"] == 2
    assert {c["last_message"]["contact"]["id"] for c in convs} == {contact}          # UN contatto
    assert all(c["inbox"]["phone_number_id"] == c["phone_number_id"] for c in convs)
    listing = owner.get(f"{API}/conversations").json()
    assert sorted(i["phone_number_id"] for i in listing["inboxes"]) == sorted([NUMBER_A, NUMBER_A2])
    # senza `inbox`: ambiguo
    r = owner.get(f"{API}/conversations/{sender}")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "INBOX_REQUIRED"
    assert owner.get(f"{REPLIES}/{sender}").status_code == 409
    # con `inbox`: solo i messaggi di quella casella, e l'altra casella e' indicata
    a2 = owner.get(f"{API}/conversations/{sender}?inbox={NUMBER_A2}").json()
    assert [m["text"] for m in a2["messages"]] == ["Ancora al numero 2", "Scrivo al numero 2"]
    assert a2["phone_number_id"] == NUMBER_A2 and [o["phone_number_id"] for o in a2["other_inboxes"]] == [NUMBER_A]
    assert a2["contact"]["id"] == contact
    a1 = owner.get(f"{API}/conversations/{sender}?inbox={NUMBER_A}").json()
    assert [m["text"] for m in a1["messages"]] == ["Scrivo al numero 1"]
    # filtro per casella nell'elenco
    assert [c["phone_number_id"] for c in _convs(owner, sender, inbox=NUMBER_A2)] == [NUMBER_A2]
    # letti: solo la casella letta, solo per chi legge
    assert owner.post(f"{API}/conversations/{sender}/read?inbox={NUMBER_A}").status_code == 200
    by_number = {c["phone_number_id"]: c for c in _convs(owner, sender)}
    assert by_number[NUMBER_A]["unread"] == 0 and by_number[NUMBER_A2]["unread"] == 2
    assert {c["phone_number_id"]: c["unread"] for c in _convs(admin, sender)} == {NUMBER_A: 1, NUMBER_A2: 2}
    assert _q(dc, "SELECT phone_number_id FROM whatsapp_inbound_reads WHERE sender_phone = %s", (sender,)) == [(NUMBER_A,)]
    assert owner.post(f"{API}/conversations/{sender}/read").status_code == 409


# ---------------------------------------------------------------------------
# D2 - la risposta parte dal numero che ha ricevuto
# ---------------------------------------------------------------------------

def test_d2_each_reply_leaves_from_the_inbox_that_received(dc):
    ids = dc["ids"]
    sender = _sender()
    _contact(dc, ids["a"], "Cliente Risposte", sender)
    _send(dc, sender, "Al numero 1", NUMBER_A)
    _send(dc, sender, "Al numero 2", NUMBER_A2)
    owner = dc["login"]("owner_a")
    st2 = owner.get(f"{REPLIES}/{sender}?inbox={NUMBER_A2}").json()
    assert st2["can_reply"] is True and st2["phone_number_id"] == NUMBER_A2 and st2["inbox"]["phone_number_id"] == NUMBER_A2
    r = _reply(owner, sender, NUMBER_A2, "Risposta dal numero 2")
    assert r.status_code == 200 and r.json()["reply"]["status"] == "sent", r.text
    r = _reply(owner, sender, NUMBER_A, "Risposta dal numero 1")
    assert r.status_code == 200 and r.json()["reply"]["status"] == "sent", r.text
    assert [(c[0], c[2]) for c in dc["fake"].calls] == [(NUMBER_A2, "Risposta dal numero 2"), (NUMBER_A, "Risposta dal numero 1")]
    assert all(c[1] == sender for c in dc["fake"].calls)
    # ogni casella mostra solo le proprie risposte
    assert [x["text"] for x in owner.get(f"{REPLIES}/{sender}?inbox={NUMBER_A2}").json()["replies"]] == ["Risposta dal numero 2"]
    assert [x["text"] for x in owner.get(f"{REPLIES}/{sender}?inbox={NUMBER_A}").json()["replies"]] == ["Risposta dal numero 1"]
    # nel registro il numero mittente e' quello della casella
    rows = _q(dc, "SELECT rendered_body, metadata->>'phone_number_id' FROM communication_messages "
                  "WHERE destination_snapshot = %s ORDER BY id", (sender,))
    assert rows == [("Risposta dal numero 2", NUMBER_A2), ("Risposta dal numero 1", NUMBER_A)]
    # una casella su cui il cliente NON ha scritto: nessuna conversazione
    assert owner.get(f"{REPLIES}/{sender}?inbox={NUMBER_B}").status_code == 404
    # un cliente che ha scritto a UN numero: la rotta senza `inbox` funziona come prima
    solo = _sender()
    _contact(dc, ids["a"], "Cliente Singolo", solo)
    _send(dc, solo, "Solo numero 1", NUMBER_A)
    st = owner.get(f"{REPLIES}/{solo}").json()
    assert st["phone_number_id"] == NUMBER_A and st["can_reply"] is True
    assert owner.get(f"{API}/conversations/{solo}").json()["phone_number_id"] == NUMBER_A


# ---------------------------------------------------------------------------
# D3 - casella riservata: titolare, autorizzati, non autorizzati
# ---------------------------------------------------------------------------

def test_d3_restricted_inbox_is_visible_to_the_owner_and_the_granted_only(dc):
    ids = dc["ids"]
    owner, data = _inbox2(dc)
    item = next(i for i in data["items"] if i["phone_number_id"] == NUMBER_A2)
    assert item["label"] == "Numero personale" and item["access_mode"] == "restricted" and item["automations_enabled"] is False
    assert item["kind"] == "coexistence" and item["configured"] is True and item["routed"] is True and item["access"] == []
    one = next(i for i in data["items"] if i["phone_number_id"] == NUMBER_A)
    assert one["configured"] is False and one["access_mode"] == "agency" and one["automations_enabled"] is True
    sender = _sender()
    _contact(dc, ids["a"], "Cliente Riservato", sender, agent=ids["agent_a"])
    _send(dc, sender, "Al numero personale", NUMBER_A2)
    _send(dc, sender, "Al numero 1", NUMBER_A)
    admin, agent, agent2 = dc["login"]("admin_a"), dc["login"]("agent_a"), dc["login"]("agent2_a")
    # il titolare vede entrambe
    assert sorted(c["phone_number_id"] for c in _convs(owner, sender)) == sorted([NUMBER_A, NUMBER_A2])
    # l'amministratore e l'agente assegnato vedono SOLO il numero 1; la casella 2 non esiste per loro
    for client in (admin, agent):
        assert [c["phone_number_id"] for c in _convs(client, sender)] == [NUMBER_A]
        assert client.get(f"{API}/conversations/{sender}?inbox={NUMBER_A2}").status_code == 404
        assert client.get(f"{REPLIES}/{sender}?inbox={NUMBER_A2}").status_code == 404
        assert client.post(f"{API}/conversations/{sender}/read?inbox={NUMBER_A2}").status_code == 404
        assert _reply(client, sender, NUMBER_A2).status_code == 404
        assert client.get(f"{API}/conversations/{sender}").json()["phone_number_id"] == NUMBER_A   # per loro e' l'unica
        assert [i["phone_number_id"] for i in client.get(f"{API}/conversations").json()["inboxes"]] == [NUMBER_A]
    assert _convs(agent2, sender) == []                                              # contatto di un collega
    # configura solo il titolare
    assert admin.put(f"{INBOXES}/{NUMBER_A2}", json={"label": "x"}).status_code == 403
    assert admin.put(f"{INBOXES}/{NUMBER_A2}/access", json={"user_ids": [ids["admin_a"]]}).status_code == 403
    assert admin.get(f"{INBOXES}/members").status_code == 403
    listed = admin.get(INBOXES).json()
    assert listed["can_configure"] is False and [i["phone_number_id"] for i in listed["items"]] == [NUMBER_A]
    assert "access" not in listed["items"][0]
    # il titolare autorizza l'amministratore: da quel momento vede la casella 2
    members = owner.get(f"{INBOXES}/members").json()["items"]
    assert {m["id"] for m in members} >= {ids["admin_a"], ids["agent_a"], ids["agent2_a"]}
    r = owner.put(f"{INBOXES}/{NUMBER_A2}/access", json={"user_ids": [ids["admin_a"]]})
    assert r.status_code == 200
    assert [a["user_id"] for a in next(i for i in r.json()["items"] if i["phone_number_id"] == NUMBER_A2)["access"]] == [ids["admin_a"]]
    assert sorted(c["phone_number_id"] for c in _convs(admin, sender)) == sorted([NUMBER_A, NUMBER_A2])
    assert admin.get(f"{API}/conversations/{sender}?inbox={NUMBER_A2}").status_code == 200
    assert admin.get(f"{REPLIES}/{sender}?inbox={NUMBER_A2}").json()["can_reply"] is True
    assert [c["phone_number_id"] for c in _convs(agent, sender)] == [NUMBER_A]       # l'agente no
    # un operatore di un'altra agenzia non si puo' autorizzare
    assert owner.put(f"{INBOXES}/{NUMBER_A2}/access", json={"user_ids": [ids["owner_b"]]}).status_code == 400
    # revoca: torna invisibile
    assert owner.put(f"{INBOXES}/{NUMBER_A2}/access", json={"user_ids": []}).status_code == 200
    assert [c["phone_number_id"] for c in _convs(admin, sender)] == [NUMBER_A]
    # accesso di agenzia: tutti come il numero 1
    assert owner.put(f"{INBOXES}/{NUMBER_A2}", json={"access_mode": "agency"}).status_code == 200
    assert sorted(c["phone_number_id"] for c in _convs(agent, sender)) == sorted([NUMBER_A, NUMBER_A2])
    # casella disattivata: nessuno la vede, i messaggi restano
    assert owner.put(f"{INBOXES}/{NUMBER_A2}", json={"status": "disabled"}).status_code == 200
    assert [c["phone_number_id"] for c in _convs(owner, sender)] == [NUMBER_A]
    assert _q(dc, "SELECT count(*) FROM whatsapp_inbound_messages WHERE sender_phone = %s AND phone_number_id = %s",
              (sender, NUMBER_A2))[0][0] == 1
    # etichetta doppia rifiutata; etichetta vuota rifiutata
    assert owner.put(f"{INBOXES}/{NUMBER_A}", json={"label": "Numero personale"}).status_code == 409
    assert owner.put(f"{INBOXES}/{NUMBER_A}", json={"label": "   "}).status_code == 400


# ---------------------------------------------------------------------------
# D4 - automazioni solo sul numero 1; pausa per casella
# ---------------------------------------------------------------------------

def test_d4_automations_stay_on_number_one(dc, monkeypatch):
    ids = dc["ids"]
    owner, _ = _inbox2(dc, access="agency", automations=False)
    monkeypatch.setenv("WHATSAPP_AUTO_REPLY_MODE", "simulate")
    assert owner.put(f"{AUTO}/settings", json={"mode": "off"}).status_code == 200
    assert owner.put(f"{AUTO}/settings", json={"mode": "simulate"}).status_code == 200
    sender = _sender()
    _contact(dc, ids["a"], "Cliente Automazioni", sender)
    _send(dc, sender, "Quanto costa l'appartamento?", NUMBER_A2)
    _send(dc, sender, "Quanto costa l'appartamento?", NUMBER_A)
    out = owner.post(f"{AUTO}/tick").json()
    assert out["status"] == "simulated" and out["recorded"] == 2, out
    on2 = owner.get(f"{AUTO}/conversations/{sender}?inbox={NUMBER_A2}").json()
    assert on2["inbox_automations_enabled"] is False
    assert [(d["decision"], d["reason_code"]) for d in on2["decisions"]] == [("skipped", "inbox_automations_disabled")]
    on1 = owner.get(f"{AUTO}/conversations/{sender}?inbox={NUMBER_A}").json()
    assert on1["inbox_automations_enabled"] is True
    assert len(on1["decisions"]) == 1 and on1["decisions"][0]["reason_code"] != "inbox_automations_disabled"
    assert owner.get(f"{AUTO}/conversations/{sender}").status_code == 409                # due caselle: va detto quale
    # pausa per casella
    assert owner.post(f"{AUTO}/conversations/{sender}/pause?inbox={NUMBER_A}").status_code == 200
    assert owner.get(f"{AUTO}/conversations/{sender}?inbox={NUMBER_A}").json()["paused"] is True
    assert owner.get(f"{AUTO}/conversations/{sender}?inbox={NUMBER_A2}").json()["paused"] is False
    assert owner.post(f"{AUTO}/conversations/{sender}/resume?inbox={NUMBER_A}").status_code == 200
    assert owner.get(f"{AUTO}/conversations/{sender}?inbox={NUMBER_A}").json()["paused"] is False
    assert _q(dc, "SELECT phone_number_id, resumed_at IS NOT NULL FROM whatsapp_auto_reply_pauses WHERE sender_phone = %s",
              (sender,)) == [(NUMBER_A, True)]
    # riattivando le automazioni sulla casella 2 un NUOVO messaggio viene deciso come sugli altri numeri
    assert owner.put(f"{INBOXES}/{NUMBER_A2}", json={"automations_enabled": True}).status_code == 200
    _send(dc, sender, "E il garage?", NUMBER_A2)
    owner.post(f"{AUTO}/tick")
    reasons = [d["reason_code"] for d in owner.get(f"{AUTO}/conversations/{sender}?inbox={NUMBER_A2}").json()["decisions"]]
    assert len(reasons) == 2 and reasons[1] != "inbox_automations_disabled"


# ---------------------------------------------------------------------------
# D5 - isolamento fra agenzie
# ---------------------------------------------------------------------------

def test_d5_another_agency_sees_and_configures_nothing(dc):
    ids = dc["ids"]
    sender = _sender()
    _contact(dc, ids["a"], "Cliente di A", sender)
    _send(dc, sender, "Ciao A2", NUMBER_A2)
    owner_b = dc["login"]("owner_b")
    assert _convs(owner_b, sender) == []
    assert owner_b.get(f"{API}/conversations/{sender}?inbox={NUMBER_A2}").status_code == 404
    assert owner_b.get(f"{REPLIES}/{sender}?inbox={NUMBER_A2}").status_code == 404
    r = owner_b.put(f"{INBOXES}/{NUMBER_A2}", json={"label": "Mio"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "INBOX_NOT_ROUTED"
    assert [i["phone_number_id"] for i in owner_b.get(INBOXES).json()["items"]] == [NUMBER_B]
    # la stessa persona scrive anche a B: conversazione di B, invisibile ad A
    _send(dc, sender, "Ciao B", NUMBER_B)
    assert [c["phone_number_id"] for c in _convs(owner_b, sender)] == [NUMBER_B]
    assert [c["phone_number_id"] for c in _convs(dc["login"]("owner_a"), sender)] == [NUMBER_A2]


# ---------------------------------------------------------------------------
# D6 - numero 1 sui dati esistenti, senza alcuna casella configurata
# ---------------------------------------------------------------------------

def test_d6_number_one_keeps_working_without_any_inbox_row(dc):
    ids = dc["ids"]
    assert _q(dc, "SELECT count(*) FROM whatsapp_inboxes")[0][0] == 0
    sender = _sender()
    _send(dc, sender, "Prima di ogni casella", NUMBER_A)
    owner, agent = dc["login"]("owner_a"), dc["login"]("agent_a")
    conv = _convs(owner, sender)
    assert len(conv) == 1 and conv[0]["phone_number_id"] == NUMBER_A and conv[0]["inbox"]["label"] == "+39000000000"
    assert conv[0]["last_message"]["contact"] is None
    # il riconoscimento in rubrica all'apertura della casella funziona ancora (contatto creato dopo)
    contact = _contact(dc, ids["a"], "Arrivato Dopo", sender, agent=ids["agent_a"])
    assert _convs(owner, sender)[0]["last_message"]["contact"]["id"] == contact
    assert [c["phone_number_id"] for c in _convs(agent, sender)] == [NUMBER_A]
    assert owner.post(f"{API}/conversations/{sender}/read").status_code == 200           # senza `inbox`: l'unica
    assert owner.get(f"{API}/conversations/{sender}").json()["inbox"]["automations_enabled"] is True
    listing = owner.get(f"{API}/conversations").json()
    assert sorted(i["phone_number_id"] for i in listing["inboxes"]) == sorted([NUMBER_A, NUMBER_A2])
    assert all(i["configured"] is False for i in owner.get(INBOXES).json()["items"])


# ---------------------------------------------------------------------------
# D7 - token per casella
# ---------------------------------------------------------------------------

def test_d7_the_transport_uses_the_inbox_token_when_present(monkeypatch):
    from message_replies import transport

    monkeypatch.setenv("WHATSAPP_TOKEN", "generale")
    monkeypatch.setenv(f"WHATSAPP_TOKEN__{NUMBER_A2}", "della-casella-2")
    assert transport.token_for(NUMBER_A2) == "della-casella-2" and transport.token_for(NUMBER_A) == "generale"
    assert transport.credentials_present(NUMBER_A2) and transport.credentials_present(NUMBER_A) and transport.credentials_present()
    monkeypatch.delenv("WHATSAPP_TOKEN")
    assert transport.token_for(NUMBER_A) is None and not transport.credentials_present(NUMBER_A)
    assert transport.credentials_present(NUMBER_A2) and transport.credentials_present()      # almeno una casella

    class Session:
        def __init__(self):
            self.calls = []

        def post(self, url, **kw):
            self.calls.append((url, kw["headers"]["Authorization"]))

            class R:
                status_code = 200

                def json(self):
                    return {"messages": [{"id": "wamid.x"}]}
            return R()

    monkeypatch.setenv("WHATSAPP_OUTBOUND_ALLOWED_RECIPIENTS", "*")
    s = Session()
    t = transport.MetaCloudTransport("", "v99.0", session=s)
    assert t.send(NUMBER_A2, "393330000000", "ciao").outcome == "accepted"
    assert s.calls[0][1] == "Bearer della-casella-2" and f"/{NUMBER_A2}/messages" in s.calls[0][0]
    out = t.send(NUMBER_A, "393330000000", "ciao")
    assert out.outcome == "rejected" and out.error_code == "missing_credentials" and len(s.calls) == 1
