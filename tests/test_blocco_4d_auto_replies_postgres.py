"""BLOCCO-4D - motore delle risposte automatiche, SOLO SIMULAZIONE, su PostgreSQL VERO.

Stesso banco di BLOCCO-2C/4A/4B/4C (cluster LOCALE usa-e-getta, migrazioni dal
runner, compresa la 119), app vera e login vero. Gate: `P29_TEST_DSN` locale e
`BLOCCO_2C_TEST_CLUSTER_VERIFIED=1`. I trabocchetti d'invio del 4A (fixture
`wa`) restano attivi e il trasporto del 4B e' sostituito da una trappola.

    C  risposte certe, chiarimento, passaggio all'agente (attivita' solo pianificata)
    S  STOP, allegati, finestra chiusa, mittente senza contatto, rinuncia di piattaforma
    D  doppioni: reinvio del webhook, due giri, giri concorrenti
    A  separazione fra agenzie e permessi
    P  pausa manuale, risposta manuale dell'operatore, arresto generale, modo spento
    Z  zero invii, zero accodamenti, zero attivita' create
"""
from __future__ import annotations

import threading
import time
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
from tests.test_blocco_4c_reply_suggestions_postgres import _property

pytestmark = [
    pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL locale"),
    pytest.mark.skipif(not CLUSTER_VERIFIED, reason="cluster usa-e-getta non dichiarato verificato "
                       "(BLOCCO_2C_TEST_CLUSTER_VERIFIED=1)"),
]

API = "/api/auto-replies"


@pytest.fixture
def ar(wa, monkeypatch):
    """Arresto generale tolto (solo simulazione), modo 'simulate' per A e B,
    trasporto del 4B trasformato in trappola."""
    from message_replies import transport

    monkeypatch.setenv("WHATSAPP_AUTO_REPLY_MODE", "simulate")
    monkeypatch.setattr(transport, "current", lambda: pytest.fail("il motore non deve mai toccare il trasporto"))
    for who in ("owner_a", "owner_b"):
        assert wa["login"](who).put(f"{API}/settings", json={"mode": "simulate"}).status_code == 200
    time.sleep(0.01)
    return wa


def _now_ts(offset_hours=0):
    return str(int(time.time() + offset_hours * 3600))


def _incoming(b, text=None, *, agency="a", number=NUMBER_A, sender=None, contact=True, agent=None, kind="text",
              ts=None, mid=None):
    sender = sender or _sender()
    contact_id = None
    if contact:
        contact_id = _contact(b, b["ids"][agency], "Cliente Sintetico", sender, agent=agent)
    mid = mid or f"wamid.4d.{uuid.uuid4().hex}"
    extra = {"image": {"mime_type": "image/jpeg", "id": "media-1"}} if kind == "image" else {}
    msg = _message(sender, kind=kind, body=text or "", mid=mid, ts=ts or _now_ts(), **extra)
    assert b["post"](_payload(_change(number, [msg]))).status_code == 200
    message_id = _q(b, "SELECT id FROM whatsapp_inbound_messages WHERE provider_message_id = %s", (mid,))[0][0]
    return message_id, sender, contact_id


def _link(b, message_id, contact_id, property_id, who="owner_a"):
    r = b["login"](who).post(f"/api/inbound-messages/{message_id}/link",
                              json={"contact_id": contact_id, "property_id": property_id})
    assert r.status_code == 200, r.text


def _tick(b, who="owner_a"):
    r = b["login"](who).post(f"{API}/tick")
    assert r.status_code == 200, r.text
    return r.json()


def _decision(b, message_id):
    rows = _q(b, "SELECT decision, reason_code, proposed_text, rules_version, mode, data_used, handoff "
                 "FROM whatsapp_auto_reply_decisions WHERE inbound_message_id = %s", (message_id,))
    assert len(rows) <= 1
    return rows[0] if rows else None


def _ledger_counts(b):
    return _q(b, "SELECT (SELECT count(*) FROM communication_messages), (SELECT count(*) FROM communication_attempts), "
                 "(SELECT count(*) FROM tasks)")[0]


# ---------------------------------------------------------------------------
# C - risposte certe, chiarimento, agente
# ---------------------------------------------------------------------------

def test_c_certain_answer_from_the_message_property_without_internal_code(ar):
    pid, code = _property(ar, "a")
    mid, sender, contact = _incoming(ar, "Buongiorno, quanto costa? C'è l'ascensore?")
    _link(ar, mid, contact, pid)
    before = _ledger_counts(ar)
    out = _tick(ar)
    assert out["status"] == "simulated" and out["sent"] == 0 and out["queued"] == 0
    decision, reason, text, version, mode, data, handoff = _decision(ar, mid)
    assert (decision, reason, version, mode, handoff) == ("answer", None, "5b-1", "simulate", None)
    assert text == ("Buongiorno, grazie per il messaggio. Per l'immobile di cui ci scrive: il prezzo richiesto è "
                    "€ 185.000; c'è l'ascensore.")
    assert code not in text and "Trilocale" not in text
    assert data["property_id"] == pid and [f["field"] for f in data["fields"]] == ["asking_price", "elevator"]
    assert _ledger_counts(ar) == before
    # nell'interfaccia: «Simulazione — non inviato»
    st = ar["login"]("owner_a").get(f"{API}/conversations/{sender}").json()
    (d,) = [x for x in st["decisions"] if x["message_id"] == mid]
    assert d["label"] == "Simulazione — non inviato" and d["sent"] is False


def test_c_missing_reference_asks_for_clarification(ar):
    mid, _, _ = _incoming(ar, "Quanto costa?")
    _tick(ar)
    decision, reason, text, *_ = _decision(ar, mid)
    assert (decision, reason) == ("ask_reference", "no_property") and "riferimento dell'annuncio" in text


@pytest.mark.parametrize("text, code", [
    ("Posso venire a vederlo domani alle 18?", "visit"),
    ("Il prezzo è trattabile?", "negotiation"),
    ("Ciao", "ambiguous"),
    ("È ancora disponibile?", "missing_data"),
])
def test_c_visits_negotiation_ambiguity_and_missing_data_go_to_the_agent(ar, text, code):
    ids = ar["ids"]
    pid, _ = _property(ar, "a")
    mid, _, contact = _incoming(ar, text, agent=ids["agent_a"])
    _link(ar, mid, contact, pid)
    tasks_before = _q(ar, "SELECT count(*) FROM tasks")[0][0]
    _tick(ar)
    decision, reason, proposed, _v, _m, data, handoff = _decision(ar, mid)
    assert (decision, reason) == ("handoff", code)
    assert handoff["task"] == "planned" and handoff["created"] is False and handoff["simulation"] is True
    assert handoff["contact_id"] == contact and handoff["assignee_user_id"] == ids["agent_a"]
    assert "ricontatt" not in proposed and "18" not in proposed and "disponibile" not in proposed
    assert _q(ar, "SELECT count(*) FROM tasks")[0][0] == tasks_before          # nessuna attivita' reale


# ---------------------------------------------------------------------------
# S - STOP, allegati, finestra, contatto, rinuncia
# ---------------------------------------------------------------------------

def test_s_stop_gets_no_reply_and_silences_the_conversation(ar):
    mid, sender, contact = _incoming(ar, "STOP")
    mid2, _, _ = _incoming(ar, "Quanto costa?", sender=sender, contact=False)
    _tick(ar)
    assert _decision(ar, mid)[:3] == ("none", "stop", None)
    assert _decision(ar, mid2)[:3] == ("skipped", "stop_in_conversation", None)


def test_s_attachments_window_and_unknown_sender_need_a_person(ar):
    img, _, _ = _incoming(ar, kind="image")
    old, _, _ = _incoming(ar, "Quanto costa?", ts=_now_ts(-30))
    nobody, _, _ = _incoming(ar, "Quanto costa?", contact=False)
    _tick(ar)
    assert _decision(ar, img)[:3] == ("skipped", "attachment", None)
    assert _decision(ar, old)[:3] == ("skipped", "window_closed", None)
    assert _decision(ar, nobody)[:3] == ("skipped", "no_contact", None)


def test_s_a_platform_optout_is_respected(ar):
    mid, sender, contact = _incoming(ar, "Quanto costa?")
    _q(ar, "INSERT INTO communication_optouts (channel, address_normalized, opted_out_at, source, actor_type, "
           "idempotency_key) VALUES ('whatsapp', %s, NOW(), 'whatsapp_stop', 'subject', %s)",
       (sender, f"t4d:{uuid.uuid4()}"))
    _tick(ar)
    assert _decision(ar, mid)[:2] == ("skipped", "platform_optout")


def test_s_messages_received_before_activation_are_not_processed(ar):
    owner = ar["login"]("owner_a")
    assert owner.put(f"{API}/settings", json={"mode": "off"}).status_code == 200
    mid, _, _ = _incoming(ar, "Quanto costa?")
    assert _tick(ar)["status"] == "off"
    assert owner.put(f"{API}/settings", json={"mode": "simulate"}).status_code == 200
    _tick(ar)
    assert _decision(ar, mid) is None


# ---------------------------------------------------------------------------
# D - doppioni e concorrenza
# ---------------------------------------------------------------------------

def test_d_resent_webhook_and_repeated_ticks_record_one_decision(ar):
    wamid = f"wamid.4d.{uuid.uuid4().hex}"
    mid, sender, _ = _incoming(ar, "Quanto costa?", mid=wamid)
    msg = _message(sender, body="Quanto costa?", mid=wamid, ts=_now_ts())
    assert ar["post"](_payload(_change(NUMBER_A, [msg]))).status_code == 200      # reinvio del webhook
    first, second = _tick(ar), _tick(ar)
    assert _q(ar, "SELECT count(*) FROM whatsapp_auto_reply_decisions WHERE inbound_message_id = %s", (mid,)) == [(1,)]
    assert first["recorded"] >= 1 and second["recorded"] == 0


def test_d_concurrent_ticks_record_one_decision_per_message(ar):
    mids = [_incoming(ar, "Quanto costa?")[0] for _ in range(4)]
    clients = [ar["login"]("owner_a") for _ in range(3)]
    barrier = threading.Barrier(len(clients))
    results = []

    def go(c):
        barrier.wait(5)
        results.append(c.post(f"{API}/tick"))

    threads = [threading.Thread(target=go, args=(c,)) for c in clients]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert [r.status_code for r in results] == [200] * len(clients)
    for mid in mids:
        assert _q(ar, "SELECT count(*) FROM whatsapp_auto_reply_decisions WHERE inbound_message_id = %s",
                  (mid,)) == [(1,)]
    assert sum(r.json()["recorded"] for r in results) >= len(mids)


def test_d_the_register_is_append_only(ar):
    mid, _, _ = _incoming(ar, "Ciao")
    _tick(ar)
    with pytest.raises(Exception, match="append-only"):
        _q(ar, "UPDATE whatsapp_auto_reply_decisions SET proposed_text = 'x' WHERE inbound_message_id = %s", (mid,))
    with pytest.raises(Exception, match="append-only"):
        _q(ar, "DELETE FROM whatsapp_auto_reply_decisions WHERE inbound_message_id = %s", (mid,))


# ---------------------------------------------------------------------------
# A - agenzie e permessi
# ---------------------------------------------------------------------------

def test_a_each_agency_processes_and_sees_only_its_messages(ar):
    ids = ar["ids"]
    mid_a, sender, _ = _incoming(ar, "Ciao", agent=ids["agent_a"])
    mid_b, _, _ = _incoming(ar, "Ciao", agency="b", number=NUMBER_B, sender=sender)
    _tick(ar, "owner_b")
    assert _decision(ar, mid_a) is None and _decision(ar, mid_b)[0] == "handoff"
    _tick(ar, "owner_a")
    assert _decision(ar, mid_a)[0] == "handoff"
    a = ar["login"]("owner_a").get(f"{API}/conversations/{sender}").json()
    b = ar["login"]("owner_b").get(f"{API}/conversations/{sender}").json()
    assert [d["message_id"] for d in a["decisions"]] == [mid_a]
    assert [d["message_id"] for d in b["decisions"]] == [mid_b]
    assert ar["login"]("agent_a").get(f"{API}/conversations/{sender}").status_code == 200
    assert ar["login"]("agent2_a").get(f"{API}/conversations/{sender}").status_code == 404
    with pytest.raises(Exception, match="another agency"):
        _q(ar, "INSERT INTO whatsapp_auto_reply_decisions (agency_id, inbound_message_id, rules_version, mode, decision, "
               "reason_code) VALUES (%s, %s, 'x', 'simulate', 'none', 'stop')", (ids["b"], mid_a))


def test_a_only_managers_configure_and_run_the_engine(ar):
    agent = ar["login"]("agent_a")
    assert agent.put(f"{API}/settings", json={"mode": "off"}).status_code == 403
    assert agent.post(f"{API}/tick").status_code == 403
    assert agent.get(f"{API}/settings").json()["can_configure"] is False
    owner = ar["login"]("owner_a")
    assert owner.put(f"{API}/settings", json={"mode": "send"}).status_code == 422       # nessun modo d'invio
    assert owner.put(f"{API}/settings", json={"mode": "simulate", "agency_id": ar["ids"]["b"]}).status_code == 422


# ---------------------------------------------------------------------------
# P - pausa, risposta dell'operatore, arresto generale
# ---------------------------------------------------------------------------

def test_p_manual_pause_and_resume(ar):
    mid, sender, _ = _incoming(ar, "Ciao")
    owner = ar["login"]("owner_a")
    st = owner.post(f"{API}/conversations/{sender}/pause").json()
    assert st["paused"] is True
    assert owner.post(f"{API}/conversations/{sender}/pause").json()["paused"] is True      # idempotente
    _tick(ar)
    assert _decision(ar, mid)[:2] == ("skipped", "conversation_paused")
    assert owner.post(f"{API}/conversations/{sender}/resume").json()["paused"] is False
    mid2, _, _ = _incoming(ar, "Ciao", sender=sender, contact=False)
    _tick(ar)
    assert _decision(ar, mid2)[0] == "handoff"
    assert ar["login"]("owner_b").post(f"{API}/conversations/{sender}/pause").status_code == 404


def test_p_a_manual_operator_reply_pauses_the_engine(ar):
    from communication import service as communication
    from operator_auth.context import OperatorContext

    ids = ar["ids"]
    mid0, sender, contact = _incoming(ar, "Ciao")
    _tick(ar)
    ctx = OperatorContext(user_id=ids["owner_a"], agency_id=ids["a"], role="agency_owner", is_platform_admin=False,
                          session_id=1, auth_channel="operator_session")
    communication.enqueue(ctx, contact_id=contact, channel="whatsapp", communication_type="service", mode="manual",
                          reason_code="operator_reply", rendered_body="Risposta a mano", destination_snapshot=sender,
                          idempotency_key=f"t4d:{uuid.uuid4()}")
    mid, _, _ = _incoming(ar, "Grazie, e il prezzo?", sender=sender, contact=False)
    _tick(ar)
    assert _decision(ar, mid)[:2] == ("skipped", "operator_replied")


def test_p_global_stop_and_agency_off_record_nothing(ar, monkeypatch):
    mid, _, _ = _incoming(ar, "Ciao")
    monkeypatch.delenv("WHATSAPP_AUTO_REPLY_MODE")
    out = _tick(ar)
    assert out["status"] == "stopped" and out["recorded"] == 0 and _decision(ar, mid) is None
    for value in ("", "send", "meta", "on", "true"):
        monkeypatch.setenv("WHATSAPP_AUTO_REPLY_MODE", value)
        assert _tick(ar)["status"] == "stopped"
    st = ar["login"]("owner_a").get(f"{API}/settings").json()
    assert st["global_enabled"] is False and st["effective"] == "off" and st["sending"] == "disabled"
    monkeypatch.setenv("WHATSAPP_AUTO_REPLY_MODE", "simulate")
    assert ar["login"]("owner_a").put(f"{API}/settings", json={"mode": "off"}).status_code == 200
    assert _tick(ar)["status"] == "off" and _decision(ar, mid) is None


# ---------------------------------------------------------------------------
# Z - nessun invio, nessun accodamento
# ---------------------------------------------------------------------------

def test_z_a_full_round_never_sends_or_queues(ar):
    pid, _ = _property(ar, "a")
    mids = []
    for text in ("Quanto costa?", "Posso visitarlo?", "Ciao", "STOP"):
        mid, _, contact = _incoming(ar, text)
        _link(ar, mid, contact, pid)
        mids.append(mid)
    before = _ledger_counts(ar)
    _tick(ar)
    assert _ledger_counts(ar) == before
    assert all(_decision(ar, m) is not None for m in mids)
