"""BLOCCO-4D - modo d'INVIO del motore delle risposte automatiche, su PostgreSQL VERO.

Stesso banco di 2C/4A/4D (cluster LOCALE usa-e-getta, migrazioni dal runner,
compresa la 123). Il trasporto di Meta e' SEMPRE un finto trasporto che
registra le chiamate: nessun invio reale. Le trappole d'invio del 4A restano.

    S  spento di partenza: ambiente e agenzia devono dire entrambi `send`
    R  risposta accodata nel registro, inviata una volta, esito registrato
    H  passaggio all'agente con attivita' REALE (assegnata o no) e testo coerente
    B  collegamento di prenotazione DEDICATO: indirizzo solo verso Meta, mai nel
       registro; scadenza, revoca, controlli al momento dell'uso
    C  controlli subito prima dell'invio, esito incerto mai ritentato, ripresa
    D  concorrenza: due giri, un solo invio
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from tests.test_blocco_2c_integrated_postgres import (  # noqa: F401 - fixture
    CLUSTER_VERIFIED,
    DSN,
    _q,
    app,
    banco,
)
from tests.test_blocco_4a_whatsapp_inbound_postgres import NUMBER_A, wa  # noqa: F401 - fixture
from tests.test_blocco_4c_reply_suggestions_postgres import _property as _plain_property
from tests.test_blocco_4d_auto_replies_postgres import _incoming, _link
from tests.test_blocco_5a_visit_booking_postgres import _link as _booking_link
from tests.test_blocco_5a_visit_booking_postgres import _property as _mandate_property

pytestmark = [
    pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL locale"),
    pytest.mark.skipif(not CLUSTER_VERIFIED, reason="cluster usa-e-getta non dichiarato verificato "
                       "(BLOCCO_2C_TEST_CLUSTER_VERIFIED=1)"),
]

API = "/api/auto-replies"
PLACEHOLDER = "[collegamento di prenotazione della visita]"
BASE = "https://crm-prova.example"


class FakeTransport:
    def __init__(self, outcome="accepted", raises=False):
        self.calls, self.outcome, self.raises = [], outcome, raises
        self.lock = threading.Lock()

    def send(self, phone_number_id, to, text):
        from message_replies.transport import SendResult
        with self.lock:
            self.calls.append((phone_number_id, to, text))
            n = len(self.calls)
        if self.raises:
            raise ConnectionError("rete caduta dopo la richiesta")
        if self.outcome == "accepted":
            return SendResult("accepted", provider_message_id=f"wamid.finto.{n}.{time.time_ns()}")
        return SendResult(self.outcome, error_code="provider_rejected", error_detail="rifiuto di prova")


@pytest.fixture
def sm(wa, monkeypatch):
    """Ambiente e agenzia A in `send`, trasporto finto e acceso, URL pubblico di prova."""
    from message_replies import transport

    fake = FakeTransport()
    monkeypatch.setenv("WHATSAPP_AUTO_REPLY_MODE", "send")
    monkeypatch.setenv("PUBLIC_BASE_URL", BASE)
    monkeypatch.setenv("PUBLIC_BOOKING_IP_PEPPER", "pepe-sintetico-di-prova")
    monkeypatch.setattr(transport, "enabled", lambda: True)
    monkeypatch.setattr(transport, "credentials_present", lambda: True)
    monkeypatch.setattr(transport, "graph_version", lambda: "v-prova")
    monkeypatch.setattr(transport, "current", lambda: fake)
    owner = wa["login"]("owner_a")
    # off -> send: l'attivazione riparte adesso, i messaggi delle prove precedenti restano fuori
    assert owner.put(f"{API}/settings", json={"mode": "off"}).status_code == 200
    time.sleep(0.01)
    assert owner.put(f"{API}/settings", json={"mode": "send"}).status_code == 200
    ids = wa["ids"]
    _q(wa, "DELETE FROM agent_working_hours WHERE user_id = %s", (ids["agent_a"],))
    for day in range(1, 8):
        _q(wa, "INSERT INTO agent_working_hours (agency_id, user_id, day_of_week, start_minute, end_minute) "
               "VALUES (%s, %s, %s, 480, 1200)", (ids["a"], ids["agent_a"], day))
    time.sleep(0.01)
    return {**wa, "fake": fake, "owner": owner}


def _tick(b, who="owner_a"):
    r = b["login"](who).post(f"{API}/tick")
    assert r.status_code == 200, r.text
    return r.json()


def _ledger(b, decision_id):
    return _q(b, "SELECT m.id, m.status, m.rendered_body, m.mode, m.reason_code, m.communication_type, m.attempt_count, "
                 "m.suppressed_reason FROM whatsapp_auto_reply_dispatches d JOIN communication_messages m "
                 "ON m.id = d.communication_message_id WHERE d.decision_id = %s", (decision_id,))


def _decision(b, mid):
    rows = _q(b, "SELECT id, decision, reason_code, mode, proposed_text, handoff FROM whatsapp_auto_reply_decisions "
                 "WHERE inbound_message_id = %s", (mid,))
    assert len(rows) <= 1
    return rows[0] if rows else None


# ---------------------------------------------------------------------------
# S - spento di partenza
# ---------------------------------------------------------------------------

def test_s_simulate_on_the_environment_never_sends_even_with_the_agency_on_send(sm, monkeypatch):
    monkeypatch.setenv("WHATSAPP_AUTO_REPLY_MODE", "simulate")
    pid, _ = _plain_property(sm, "a")
    mid, _, contact = _incoming(sm, "Quanto costa?")
    _link(sm, mid, contact, pid)
    out = _tick(sm)
    assert out["status"] == "simulated" and out["sent"] == 0 and sm["fake"].calls == []
    assert _decision(sm, mid)[3] == "simulate"
    st = sm["owner"].get(f"{API}/settings").json()
    assert st["mode"] == "send" and st["effective"] == "simulate" and st["sending"] == "disabled"


def test_s_without_the_environment_variable_nothing_happens(sm, monkeypatch):
    monkeypatch.delenv("WHATSAPP_AUTO_REPLY_MODE")
    mid, _, _ = _incoming(sm, "Quanto costa?")
    assert _tick(sm)["status"] == "stopped" and _decision(sm, mid) is None and sm["fake"].calls == []


def test_s_an_agent_cannot_turn_on_sending(sm):
    assert sm["login"]("agent_a").put(f"{API}/settings", json={"mode": "send"}).status_code == 403


# ---------------------------------------------------------------------------
# R - risposta con i dati della scheda
# ---------------------------------------------------------------------------

def test_r_a_certain_answer_is_queued_sent_once_and_recorded(sm):
    pid, code = _plain_property(sm, "a")
    mid, sender, contact = _incoming(sm, "Buongiorno, quanto costa? C'è l'ascensore?")
    _link(sm, mid, contact, pid)
    out = _tick(sm)
    assert out["status"] == "sending" and (out["queued"], out["sent"]) == (1, 1)
    did, decision, _r, mode, text, _h = _decision(sm, mid)
    assert (decision, mode) == ("answer", "send") and "€ 185.000" in text and code not in text
    (call,) = sm["fake"].calls
    assert call == (NUMBER_A, sender, text)
    (_id, status, body, cmode, reason, ctype, attempts, _s), = _ledger(sm, did)
    assert (status, body, cmode, reason, ctype, attempts) == ("sent", text, "automatic", "auto_reply", "service", 1)
    st = sm["owner"].get(f"{API}/conversations/{sender}").json()
    (d,) = [x for x in st["decisions"] if x["message_id"] == mid]
    assert d["label"] == "Inviato" and d["sent"] is True and d["mode"] == "send"
    # un secondo giro non rimanda nulla
    assert _tick(sm)["sent"] == 0 and len(sm["fake"].calls) == 1


def test_r_skipped_messages_send_nothing(sm):
    mid, _, _ = _incoming(sm, "", kind="image")
    _tick(sm)
    assert _decision(sm, mid)[1] == "skipped" and sm["fake"].calls == []
    assert _q(sm, "SELECT count(*) FROM whatsapp_auto_reply_dispatches d JOIN whatsapp_auto_reply_decisions x "
                  "ON x.id = d.decision_id WHERE x.inbound_message_id = %s", (mid,)) == [(0,)]


# ---------------------------------------------------------------------------
# H - passaggio all'agente con attivita' reale
# ---------------------------------------------------------------------------

def test_h_handoff_creates_an_assigned_task_and_only_then_promises_a_callback(sm):
    ids = sm["ids"]
    pid, _ = _plain_property(sm, "a")
    mid, _, contact = _incoming(sm, "Il prezzo è trattabile?", agent=ids["agent_a"])
    _link(sm, mid, contact, pid)
    _tick(sm)
    did, decision, reason, _m, text, handoff = _decision(sm, mid)
    assert (decision, reason) == ("handoff", "negotiation") and "ricontatterà" in text
    assert handoff["created"] is True and handoff["assigned"] is True and handoff["assignee_user_id"] == ids["agent_a"]
    (task,) = _q(sm, "SELECT id, assigned_to, status, contact_id, agency_id, metadata->>'assigned_user_id' FROM tasks "
                     "WHERE id = %s", (handoff["task_id"],))
    assert task[1] and task[2] == "open" and task[3] == contact and task[4] == ids["a"]
    assert task[5] == str(ids["agent_a"])
    assert sm["fake"].calls[-1][2] == text


def test_h_without_an_active_agent_the_task_is_unassigned_and_nothing_is_promised(sm):
    pid, _ = _plain_property(sm, "a")
    mid, _, contact = _incoming(sm, "Il prezzo è trattabile?")
    _link(sm, mid, contact, pid)
    _tick(sm)
    _id, decision, _r, _m, text, handoff = _decision(sm, mid)
    assert decision == "handoff" and handoff["assigned"] is False and "ricontatt" not in text
    assert _q(sm, "SELECT assigned_to FROM tasks WHERE id = %s", (handoff["task_id"],)) == [(None,)]


# ---------------------------------------------------------------------------
# B - collegamento dedicato
# ---------------------------------------------------------------------------

def _visit_request(b):
    pid = _mandate_property(b)
    parent = _booking_link(b, pid).json()
    mid, sender, contact = _incoming(b, "Buongiorno, posso venire a vederlo?")
    _link(b, mid, contact, pid)
    return pid, parent, mid, sender, contact


def test_b_the_link_reaches_meta_only_and_the_registry_keeps_the_placeholder(sm):
    pid, parent, mid, sender, contact = _visit_request(sm)
    _tick(sm)
    did, decision, reason, _m, text, _h = _decision(sm, mid)
    assert (decision, reason) == ("answer", "visit_booking_link") and PLACEHOLDER in text
    (_p, _to, delivered), = sm["fake"].calls
    assert PLACEHOLDER not in delivered and f"{BASE}/visita/" in delivered
    token = delivered.split(f"{BASE}/visita/")[1].split(".")[0].split(" ")[0]
    assert len(token) >= 16
    # mai nel registro: ne' decisione, ne' messaggio, ne' tentativi, ne' collegamenti
    for table, col in (("whatsapp_auto_reply_decisions", "proposed_text"), ("communication_messages", "rendered_body"),
                       ("communication_messages", "metadata::text"), ("communication_attempts", "row_to_json(communication_attempts)::text"),
                       ("property_visit_booking_links", "row_to_json(property_visit_booking_links)::text")):
        assert _q(sm, f"SELECT count(*) FROM {table} WHERE {col} LIKE %s", (f"%{token}%",)) == [(0,)], table
    (_mid, status, body, *_), = _ledger(sm, did)
    assert status == "sent" and body == text
    (child,) = _q(sm, "SELECT parent_link_id, issued_decision_id, issued_contact_id, expires_at, status "
                      "FROM property_visit_booking_links WHERE parent_link_id = %s", (parent["id"],))
    assert child[:3] == (parent["id"], did, contact) and child[4] == "active"
    assert child[3] <= datetime.now(timezone.utc) + timedelta(days=7, minutes=1)
    # il collegamento dedicato funziona sulla pagina pubblica
    from fastapi.testclient import TestClient

    import main
    pub = TestClient(main.app, base_url="https://testserver")
    assert pub.get(f"/api/public/visit-booking/{token}").status_code == 200
    # non e' fra i collegamenti dell'operatore
    listed = sm["owner"].get("/api/visit-booking-links", params={"property_id": pid}).json()["items"]
    assert [x["id"] for x in listed] == [parent["id"]]
    # al momento dell'uso: immobile non piu' «Attivo» -> rifiuto neutro
    _q(sm, "SET session_replication_role = replica; UPDATE properties SET commercial_status = 'reserved' WHERE id = %s",
       (pid,))
    assert pub.get(f"/api/public/visit-booking/{token}").status_code == 404
    _q(sm, "SET session_replication_role = replica; UPDATE properties SET commercial_status = 'active' WHERE id = %s",
       (pid,))
    assert pub.get(f"/api/public/visit-booking/{token}").status_code == 200
    # revocare il collegamento dell'operatore revoca anche il dedicato
    assert sm["owner"].post(f"/api/visit-booking-links/{parent['id']}/disable").status_code == 200
    assert _q(sm, "SELECT status FROM property_visit_booking_links WHERE parent_link_id = %s", (parent["id"],)) == \
        [("disabled",)]
    assert pub.get(f"/api/public/visit-booking/{token}").status_code == 404


def test_b_an_expired_dedicated_link_is_refused(sm):
    pid, parent, mid, _s, _c = _visit_request(sm)
    _tick(sm)
    token = sm["fake"].calls[-1][2].split(f"{BASE}/visita/")[1].split(".")[0].split(" ")[0]
    _q(sm, "UPDATE property_visit_booking_links SET expires_at = NOW() - interval '1 minute' WHERE parent_link_id = %s",
       (parent["id"],))
    from fastapi.testclient import TestClient

    import main
    assert TestClient(main.app, base_url="https://testserver").get(
        f"/api/public/visit-booking/{token}").status_code == 404


def test_b_without_a_public_https_address_the_reply_is_blocked(sm, monkeypatch):
    monkeypatch.delenv("PUBLIC_BASE_URL")
    _pid, parent, mid, _s, _c = _visit_request(sm)
    out = _tick(sm)
    assert out["suppressed"] == 1 and sm["fake"].calls == []
    did = _decision(sm, mid)[0]
    assert _ledger(sm, did)[0][1:2] == ("suppressed",) and _ledger(sm, did)[0][7] == "public_base_url_missing"
    assert _q(sm, "SELECT count(*) FROM property_visit_booking_links WHERE parent_link_id = %s", (parent["id"],)) == [(0,)]


def test_b_dedicated_links_must_match_their_parent_in_the_database(sm):
    _pid, parent, mid, _s, contact = _visit_request(sm)
    _tick(sm)
    did = _decision(sm, mid)[0]
    with pytest.raises(Exception, match="already exists|duplicate|issued_unq"):
        _q(sm, "INSERT INTO property_visit_booking_links (agency_id, property_id, assigned_user_id, token_hash, "
               "duration_minutes, owner_authorization, owner_authorization_recorded_by, created_by_user_id, expires_at, "
               "parent_link_id, issued_decision_id, issued_contact_id) SELECT agency_id, property_id, assigned_user_id, "
               "repeat('a', 64), 45, owner_authorization, owner_authorization_recorded_by, created_by_user_id, "
               "NOW() + interval '1 day', id, %s, %s FROM property_visit_booking_links WHERE id = %s",
           (did, contact, parent["id"]))
    with pytest.raises(Exception, match="only disabling is allowed"):
        _q(sm, "UPDATE property_visit_booking_links SET parent_link_id = NULL, issued_decision_id = NULL, "
               "issued_contact_id = NULL WHERE parent_link_id = %s", (parent["id"],))


# ---------------------------------------------------------------------------
# C - controlli dell'ultimo momento, esito incerto, ripresa
# ---------------------------------------------------------------------------

def test_c_a_pause_set_after_the_decision_blocks_the_send(sm, monkeypatch):
    from auto_replies import service

    pid, _ = _plain_property(sm, "a")
    mid, sender, contact = _incoming(sm, "Quanto costa?")
    _link(sm, mid, contact, pid)
    real = service._send_time_check

    def paused_meanwhile(agency_id, message, **kw):
        _q(sm, "INSERT INTO whatsapp_auto_reply_pauses (agency_id, sender_phone, paused_by_user_id) VALUES (%s, %s, %s)",
           (sm["ids"]["a"], sender, sm["ids"]["owner_a"]))
        return real(agency_id, message, **kw)

    monkeypatch.setattr(service, "_send_time_check", paused_meanwhile)
    out = _tick(sm)
    assert out["suppressed"] == 1 and sm["fake"].calls == []
    assert _ledger(sm, _decision(sm, mid)[0])[0][7] == "conversation_paused"


@pytest.mark.parametrize("change, reason", [
    ("transport_off", "outbound_disabled"), ("agency_off", "auto_reply_not_in_send_mode"),
    ("stop", "stop_in_conversation"), ("not_active", "property_no_longer_active"),
])
def test_c_each_send_time_check_blocks_without_calling_meta(sm, monkeypatch, change, reason):
    from auto_replies import service
    from message_replies import transport

    pid, _ = _plain_property(sm, "a")
    mid, sender, contact = _incoming(sm, "Quanto costa?")
    _link(sm, mid, contact, pid)
    real = service._send_time_check

    def meanwhile(agency_id, message, **kw):
        if change == "transport_off":
            monkeypatch.setattr(transport, "enabled", lambda: False)
        elif change == "agency_off":
            _q(sm, "UPDATE whatsapp_auto_reply_settings SET mode = 'off', enabled_at = NULL WHERE agency_id = %s",
               (sm["ids"]["a"],))
        elif change == "stop":
            _incoming(sm, "STOP", sender=sender, contact=False)
        else:
            _q(sm, "UPDATE properties SET commercial_status = 'reserved' WHERE id = %s", (pid,))
        return real(agency_id, message, **kw)

    monkeypatch.setattr(service, "_send_time_check", meanwhile)
    _tick(sm)
    assert sm["fake"].calls == []
    (_i, status, *_rest, suppressed), = _ledger(sm, _decision(sm, mid)[0])
    assert (status, suppressed) == ("suppressed", reason)


def test_c_an_uncertain_outcome_is_never_retried(sm):
    sm["fake"].raises = True
    pid, _ = _plain_property(sm, "a")
    mid, _, contact = _incoming(sm, "Quanto costa?")
    _link(sm, mid, contact, pid)
    assert _tick(sm)["indeterminate"] == 1
    sm["fake"].raises = False
    for _ in range(2):
        assert _tick(sm)["sent"] == 0
    assert len(sm["fake"].calls) == 1
    assert _ledger(sm, _decision(sm, mid)[0])[0][1] == "indeterminate"


def test_c_a_rejected_send_is_recorded_as_failed(sm):
    sm["fake"].outcome = "rejected"
    pid, _ = _plain_property(sm, "a")
    mid, _, contact = _incoming(sm, "Quanto costa?")
    _link(sm, mid, contact, pid)
    assert _tick(sm)["failed"] == 1
    assert _ledger(sm, _decision(sm, mid)[0])[0][1] == "failed"


def test_c_a_round_interrupted_after_queueing_resumes_once(sm, monkeypatch):
    from auto_replies import service

    pid, _ = _plain_property(sm, "a")
    mid, _, contact = _incoming(sm, "Quanto costa?")
    _link(sm, mid, contact, pid)
    real = service._deliver
    monkeypatch.setattr(service, "_deliver", lambda *a, **k: (_ for _ in ()).throw(SystemExit("interrotto")))
    with pytest.raises(BaseException):
        service.run_for_agency(sm["ids"]["a"])
    did = _decision(sm, mid)[0]
    assert _ledger(sm, did)[0][1] == "queued" and sm["fake"].calls == []
    monkeypatch.setattr(service, "_deliver", real)
    assert _tick(sm)["sent"] == 1 and _tick(sm)["sent"] == 0
    assert len(sm["fake"].calls) == 1


# ---------------------------------------------------------------------------
# D - concorrenza
# ---------------------------------------------------------------------------

def test_d_concurrent_rounds_send_each_reply_once(sm):
    pid, _ = _plain_property(sm, "a")
    mids = []
    for _ in range(3):
        mid, _s, contact = _incoming(sm, "Quanto costa?")
        _link(sm, mid, contact, pid)
        mids.append(mid)
    clients = [sm["login"]("owner_a") for _ in range(3)]
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
    assert len(sm["fake"].calls) == len(mids)
    for mid in mids:
        assert _q(sm, "SELECT count(*) FROM whatsapp_auto_reply_decisions WHERE inbound_message_id = %s",
                  (mid,)) == [(1,)]
    assert _q(sm, "SELECT count(*) FROM communication_messages WHERE reason_code = 'auto_reply' AND status = 'sent' "
                  "AND (metadata->>'inbound_message_id')::bigint = ANY(%s)", (mids,)) == [(len(mids),)]


def test_d_agency_b_messages_are_never_sent_by_agency_a(sm):
    from tests.test_blocco_4a_whatsapp_inbound_postgres import NUMBER_B
    mid, _, _ = _incoming(sm, "Quanto costa?", agency="b", number=NUMBER_B)
    _tick(sm)
    assert _decision(sm, mid) is None and sm["fake"].calls == []
