"""BLOCCO-4B - risposta WhatsApp manuale su PostgreSQL VERO.

Stesso banco di BLOCCO-2C/4A (schema completo su un cluster LOCALE
usa-e-getta, migrazioni dal runner), app vera e login vero. Gate:
`P29_TEST_DSN` locale e `BLOCCO_2C_TEST_CLUSTER_VERIFIED=1`.

Il trasporto verso Meta e' SIMULATO: `message_replies.transport.current` e'
sostituito da un trasporto finto; `requests.post` e le vecchie funzioni
d'invio restano trappole (fixture `wa` del 4A) che fanno fallire il test.

    P  permessi: titolare, agente assegnato, agente non assegnato, altra agenzia,
       sola lettura, corpo chiuso, conferma obbligatoria
    W  finestra di 24 ore; contatto mancante; numero mittente non configurato;
       invio disattivato per impostazione predefinita
    S  STOP: rinuncia marketing registrata, risposta di assistenza consentita,
       nessun consenso marketing dalle risposte del cliente
    D  doppio invio: stessa richiesta, richieste concorrenti, claim mirato
    E  errori: rifiuto (finestra Meta), timeout incerto mai ritentato, eccezione
    L  registro: operatore, testo, data, stato; consegna dal webhook; altri
       messaggi in coda non toccati
"""
from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

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

API = "/api/message-replies/conversations"


class FakeTransport:
    """Il trasporto simulato: registra le chiamate, risponde come gli si dice."""

    def __init__(self):
        self.calls = []
        self.outcome = "accepted"
        self.error_code = None
        self.error_detail = None
        self.raise_exc = None
        self.delay = 0.0
        self._lock = threading.Lock()

    def send(self, phone_number_id, to, text):
        from message_replies.transport import SendResult

        with self._lock:
            self.calls.append((phone_number_id, to, text))
        if self.delay:
            time.sleep(self.delay)
        if self.raise_exc:
            raise self.raise_exc
        if self.outcome == "accepted":
            return SendResult("accepted", provider_message_id=f"wamid.out.{uuid.uuid4().hex}")
        return SendResult(self.outcome, error_code=self.error_code, error_detail=self.error_detail)


@pytest.fixture
def rp(wa, monkeypatch):
    from message_replies import transport

    fake = FakeTransport()
    monkeypatch.setenv("WHATSAPP_OUTBOUND_MODE", "meta")
    monkeypatch.setenv("WHATSAPP_TOKEN", "token-sintetico-non-reale")
    monkeypatch.setenv("WHATSAPP_GRAPH_VERSION", "v99.0")          # sintetica, ben formata
    monkeypatch.setattr(transport, "current", lambda: fake)
    return {**wa, "fake": fake}


def _conversation(b, *, agency="a", number=NUMBER_A, hours_ago=1, contact=True, agent=None, text="Ciao, info?"):
    ids = b["ids"]
    sender = _sender()
    contact_id = _contact(b, ids[agency], f"Cliente {sender[-4:]}", sender, agent=agent) if contact else None
    ts = str(int((datetime.now(timezone.utc) - timedelta(hours=hours_ago)).timestamp()))
    assert b["post"](_payload(_change(number, [_message(sender, body=text, ts=ts)]))).status_code == 200
    return sender, contact_id


def _reply(client, sender, text="Buongiorno, la richiamo oggi.", key=None, confirm=True):
    return client.post(f"{API}/{sender}", json={"text": text, "client_request_id": key or str(uuid.uuid4()),
                                                "confirm": confirm})


def _ledger(b, sender):
    return _q(b, "SELECT id, channel, communication_type, mode, reason_code, status, actor_type, actor_user_id, "
                 "rendered_body, provider_message_id, error_code, error_detail, attempt_count FROM "
                 "communication_messages WHERE destination_snapshot = %s ORDER BY id", (sender,))


# ---------------------------------------------------------------------------
# P - permessi
# ---------------------------------------------------------------------------

def test_p_owner_replies_and_the_ledger_records_operator_text_status(rp):
    ids = rp["ids"]
    sender, contact = _conversation(rp)
    owner = rp["login"]("owner_a")
    st = owner.get(f"{API}/{sender}").json()
    assert st["can_reply"] is True and st["window"]["open"] is True and st["stop"] == {"requested": False}
    r = _reply(owner, sender, "Buongiorno, la richiamo oggi.")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] is True and body["reply"]["status"] == "sent"
    assert body["reply"]["status_label"] == "Accettato da WhatsApp" and body["reply"]["operator"]
    assert rp["fake"].calls == [(NUMBER_A, sender, "Buongiorno, la richiamo oggi.")]
    (row,) = _ledger(rp, sender)
    assert row[1:9] == ("whatsapp", "service", "manual", "operator_reply", "sent", "operator", ids["owner_a"],
                        "Buongiorno, la richiamo oggi.")
    assert row[9].startswith("wamid.out.") and row[12] == 1
    attempts = _q(rp, "SELECT outcome, provider FROM communication_attempts WHERE message_id = %s", (row[0],))
    assert attempts == [("accepted", "meta_cloud_api")]


def test_p_permissions_agency_isolation_and_closed_body(rp):
    ids = rp["ids"]
    sender, contact = _conversation(rp, agent=ids["agent_a"])
    agent, agent2, owner_b = rp["login"]("agent_a"), rp["login"]("agent2_a"), rp["login"]("owner_b")
    assert agent.get(f"{API}/{sender}").json()["can_reply"] is True          # agente del contatto
    assert agent2.get(f"{API}/{sender}").status_code == 404                   # agente non assegnato
    assert _reply(agent2, sender).status_code == 404
    assert owner_b.get(f"{API}/{sender}").status_code == 404                  # altra agenzia
    assert _reply(owner_b, sender).status_code == 404
    owner = rp["login"]("owner_a")
    for extra in ({"to": "393330000000"}, {"phone_number_id": NUMBER_B}, {"agency_id": ids["b"]}):
        r = owner.post(f"{API}/{sender}", json={"text": "x", "client_request_id": str(uuid.uuid4()), "confirm": True,
                                                **extra})
        assert r.status_code == 422, extra
    r = _reply(owner, sender, confirm=False)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "CONFIRM_REQUIRED"
    assert _reply(owner, sender, text="x" * 4097).status_code == 422
    assert rp["fake"].calls == [] and _ledger(rp, sender) == []
    assert _reply(agent, sender).json()["reply"]["status"] == "sent"
    assert _ledger(rp, sender)[0][7] == ids["agent_a"]


def test_p_read_only_consultation_cannot_reply(rp):
    ids = rp["ids"]
    sender, _ = _conversation(rp)
    sa = rp["login"]("super")
    try:
        assert sa.patch(f"/api/platform/agencies/{ids['a']}", json={"status": "suspended"}).status_code == 200
        assert sa.post(f"/api/platform/agencies/{ids['a']}/enter").json()["read_only"] is True
        assert sa.get(f"{API}/{sender}").status_code == 200
        r = _reply(sa, sender)
        assert r.status_code == 403 and "sola lettura" in r.text.lower()
        assert sa.post("/api/platform/agency-context/exit").status_code == 204
    finally:
        sa.patch(f"/api/platform/agencies/{ids['a']}", json={"status": "active"})
        _q(rp, "UPDATE agencies SET status = 'active' WHERE id = %s", (ids["a"],))
    assert rp["fake"].calls == [] and _ledger(rp, sender) == []


# ---------------------------------------------------------------------------
# W - finestra, contatto, numero, invio disattivato
# ---------------------------------------------------------------------------

def test_w_window_closed_after_24_hours(rp):
    sender, _ = _conversation(rp, hours_ago=25)
    owner = rp["login"]("owner_a")
    st = owner.get(f"{API}/{sender}").json()
    assert st["can_reply"] is False and st["window"]["open"] is False
    assert [b["code"] for b in st["blocks"]] == ["WINDOW_CLOSED"]
    r = _reply(owner, sender)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "WINDOW_CLOSED"
    assert rp["fake"].calls == [] and _ledger(rp, sender) == []
    # un nuovo messaggio del cliente riapre la finestra
    now = str(int(datetime.now(timezone.utc).timestamp()))
    assert rp["post"](_payload(_change(NUMBER_A, [_message(sender, body="Ci sono ancora", ts=now)]))).status_code == 200
    assert owner.get(f"{API}/{sender}").json()["can_reply"] is True


def test_w_contact_and_sender_number_are_required(rp, monkeypatch):
    ids = rp["ids"]
    owner = rp["login"]("owner_a")
    sender, _ = _conversation(rp, contact=False)
    r = _reply(owner, sender)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "NO_CONTACT"
    sender2, _ = _conversation(rp)
    monkeypatch.setenv("WHATSAPP_INBOUND_ROUTES", f"{NUMBER_B}:{ids['b']}")      # il numero non e' piu' dell'agenzia
    r = _reply(owner, sender2)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "SENDER_NOT_CONFIGURED"
    assert rp["fake"].calls == []


def test_w_sending_is_disabled_by_default(rp, monkeypatch):
    sender, _ = _conversation(rp)
    owner = rp["login"]("owner_a")
    monkeypatch.delenv("WHATSAPP_OUTBOUND_MODE")
    st = owner.get(f"{API}/{sender}").json()
    assert st["can_reply"] is False and st["blocks"][0]["code"] == "OUTBOUND_DISABLED"
    r = _reply(owner, sender)
    assert r.status_code == 503 and r.json()["detail"]["code"] == "OUTBOUND_DISABLED"
    monkeypatch.setenv("WHATSAPP_OUTBOUND_MODE", "meta")
    monkeypatch.delenv("WHATSAPP_TOKEN")
    assert _reply(owner, sender).json()["detail"]["code"] == "CREDENTIALS_MISSING"
    assert rp["fake"].calls == [] and _ledger(rp, sender) == []


# ---------------------------------------------------------------------------
# S - STOP e consenso
# ---------------------------------------------------------------------------

def _ctx(b, who, agency):
    from operator_auth.context import OperatorContext

    role = "agency_owner" if who.startswith("owner") else "agent"
    return OperatorContext(user_id=b["ids"][who], agency_id=b["ids"][agency], role=role, is_platform_admin=False,
                           session_id=1, auth_channel="operator_session")


def _revocations(b, contact):
    return _q(b, "SELECT decision, channel, source, actor_type FROM consent_events WHERE contact_id = %s "
                 "AND purpose = 'marketing' ORDER BY id", (contact,))


def test_s_stop_revokes_marketing_only_in_the_receiving_agency(rp):
    """Stesso telefono in due agenzie: lo STOP scritto al numero di A revoca il
    marketing WhatsApp del contatto di A, non quello del contatto di B; nessuna
    riga nel registro di piattaforma (che varrebbe per tutte le agenzie)."""
    from consent import guard
    from consent import service as consent_service

    ids = rp["ids"]
    sender, contact_a = _conversation(rp, text="Mi interessa, grazie")
    contact_b = _contact(rp, ids["b"], "Stesso telefono in B", sender)
    consent_service.record_grant(_ctx(rp, "owner_b", "b"), contact_id=contact_b, purpose="marketing", channel="whatsapp",
                                 source="crm", actor_type="operator", actor_ref=f"operator_user:{ids['owner_b']}",
                                 idempotency_key=f"t:grant:{contact_b}")
    assert guard.can_send_marketing(_ctx(rp, "owner_b", "b"), contact_b, channel="whatsapp").allowed is True
    optouts_before = _q(rp, "SELECT count(*) FROM communication_optouts")[0][0]
    assert _revocations(rp, contact_a) == []                       # una risposta del cliente non e' consenso
    assert rp["post"](_payload(_change(NUMBER_A, [_message(sender, body=" Stop. ")]))).status_code == 200
    assert _revocations(rp, contact_a) == [("revoked", "whatsapp", "whatsapp_optout", "subject")]
    assert guard.can_send_marketing(_ctx(rp, "owner_a", "a"), contact_a, channel="whatsapp").allowed is False
    assert guard.can_send_marketing(_ctx(rp, "owner_b", "b"), contact_b, channel="whatsapp").allowed is True
    assert _revocations(rp, contact_b) == [("granted", "whatsapp", "crm", "operator")]
    assert _q(rp, "SELECT count(*) FROM communication_optouts")[0][0] == optouts_before


def test_s_stop_notice_is_not_a_consent_and_service_reply_needs_the_window(rp):
    sender, contact = _conversation(rp)
    assert rp["post"](_payload(_change(NUMBER_A, [_message(sender, body="STOP",
                                                           ts=str(int(time.time())))]))).status_code == 200
    owner = rp["login"]("owner_a")
    st = owner.get(f"{API}/{sender}").json()
    assert st["stop"]["requested"] is True
    notice = st["stop"]["notice"]
    assert "Non è un consenso" in notice and "ultime 24 ore" in notice and "questa agenzia" in notice
    assert _reply(owner, sender, "Ricevuto, nessuna promozione.").json()["reply"]["status"] == "sent"
    assert _q(rp, "SELECT count(*) FROM contact_channel_consents WHERE contact_id = %s AND status = 'granted'",
              (contact,))[0][0] == 0


def test_s_a_resent_stop_is_recorded_once(rp):
    sender, contact = _conversation(rp)
    payload = _payload(_change(NUMBER_A, [_message(sender, body="STOP", mid=f"wamid.stop.{uuid.uuid4().hex}")]))
    for _ in range(2):
        assert rp["post"](payload).status_code == 200
    assert _revocations(rp, contact) == [("revoked", "whatsapp", "whatsapp_optout", "subject")]


def test_s_stop_without_a_certain_contact_waits_for_the_manual_association(rp):
    ids = rp["ids"]
    sender, _ = _conversation(rp, contact=False)
    assert rp["post"](_payload(_change(NUMBER_A, [_message(sender, body="STOP")]))).status_code == 200
    contact = _contact(rp, ids["a"], "Associato dopo", "393000000099")
    assert _revocations(rp, contact) == []
    stop_id = _q(rp, "SELECT id FROM whatsapp_inbound_messages WHERE sender_phone = %s AND text_body = 'STOP'",
                 (sender,))[0][0]
    r = rp["login"]("owner_a").post(f"/api/inbound-messages/{stop_id}/link", json={"contact_id": contact})
    assert r.status_code == 200, r.text
    assert _revocations(rp, contact) == [("revoked", "whatsapp", "whatsapp_optout", "subject")]


# ---------------------------------------------------------------------------
# T - controlli ripetuti al momento dell'invio (dopo l'accodamento, prima di Meta)
# ---------------------------------------------------------------------------

def _between_enqueue_and_send(monkeypatch, action):
    """Esegue `action` dopo l'accodamento e prima del ricontrollo, nel punto in
    cui un messaggio accodato attende di essere inviato."""
    from message_replies import service

    original = service.communication.claim_message

    def claim(*args, **kwargs):
        claimed = original(*args, **kwargs)
        action()
        return claimed

    monkeypatch.setattr(service.communication, "claim_message", claim)


def _suppressed(b, sender):
    return _q(b, "SELECT status, suppressed_reason, provider_message_id FROM communication_messages "
                 "WHERE destination_snapshot = %s", (sender,))


def test_t_window_expiring_after_enqueue_blocks_without_calling_meta(rp, monkeypatch):
    from message_replies import service

    sender, _ = _conversation(rp, hours_ago=23)
    later = datetime.now(timezone.utc) + timedelta(hours=2)
    _between_enqueue_and_send(monkeypatch, lambda: monkeypatch.setattr(service, "_now", lambda: later))
    body = _reply(rp["login"]("owner_a"), sender).json()
    assert rp["fake"].calls == []
    assert _suppressed(rp, sender) == [("suppressed", "window_closed", None)]
    assert body["reply"]["status"] == "suppressed"


def test_t_a_future_provider_timestamp_cannot_extend_the_window(rp, monkeypatch):
    from message_replies import service

    sender, _ = _conversation(rp, hours_ago=-72)                     # Meta dichiara "fra tre giorni"
    owner = rp["login"]("owner_a")
    st = owner.get(f"{API}/{sender}").json()
    closes = datetime.fromisoformat(st["window"]["closes_at"])
    assert closes <= datetime.now(timezone.utc) + timedelta(hours=24, minutes=1)    # dalla ricezione, non dal futuro
    monkeypatch.setattr(service, "_now", lambda: datetime.now(timezone.utc) + timedelta(hours=25))
    r = _reply(owner, sender)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "WINDOW_CLOSED" and rp["fake"].calls == []


def test_t_sender_number_removed_after_enqueue_blocks(rp, monkeypatch):
    ids = rp["ids"]
    sender, _ = _conversation(rp)
    _between_enqueue_and_send(monkeypatch, lambda: monkeypatch.setenv("WHATSAPP_INBOUND_ROUTES", f"{NUMBER_A}:{ids['b']}"))
    _reply(rp["login"]("owner_a"), sender)
    assert rp["fake"].calls == [] and _suppressed(rp, sender) == [("suppressed", "sender_not_configured", None)]


def test_t_sending_disabled_after_enqueue_blocks(rp, monkeypatch):
    sender, _ = _conversation(rp)
    _between_enqueue_and_send(monkeypatch, lambda: monkeypatch.setenv("WHATSAPP_OUTBOUND_MODE", "off"))
    _reply(rp["login"]("owner_a"), sender)
    assert rp["fake"].calls == [] and _suppressed(rp, sender) == [("suppressed", "outbound_disabled", None)]


def test_t_lost_visibility_after_enqueue_blocks(rp, monkeypatch):
    ids = rp["ids"]
    sender, contact = _conversation(rp, agent=ids["agent_a"])
    _between_enqueue_and_send(monkeypatch, lambda: _q(rp, "UPDATE contacts SET assigned_agent_id = %s WHERE id = %s",
                                                     (ids["agent2_a"], contact)))
    _reply(rp["login"]("agent_a"), sender)
    assert rp["fake"].calls == [] and _suppressed(rp, sender) == [("suppressed", "conversation_not_visible", None)]


def test_t_the_generic_dispatcher_cannot_take_these_replies(rp, monkeypatch):
    from communication import service as communication

    sender, _ = _conversation(rp)
    seen = {}

    def peek():
        ctx = _ctx(rp, "owner_a", "a")
        seen["claimed"] = communication.claim_due(ctx, provider="generico", channel="whatsapp", limit=50)

    from message_replies import service

    original = service.communication.enqueue

    def enqueue(*args, **kwargs):
        out = original(*args, **kwargs)
        peek()                                                    # la risposta e' in coda: il claim generico la vede?
        return out

    monkeypatch.setattr(service.communication, "enqueue", enqueue)
    assert _reply(rp["login"]("owner_a"), sender).json()["reply"]["status"] == "sent"
    assert all(c["message"]["destination_snapshot"] != sender for c in seen["claimed"])
    assert rp["login"]("owner_a").post("/api/communication/dispatch", json={"channel": "whatsapp"}).status_code == 501


# ---------------------------------------------------------------------------
# D - doppio invio
# ---------------------------------------------------------------------------

def test_d_the_same_request_twice_sends_once(rp):
    sender, _ = _conversation(rp)
    owner = rp["login"]("owner_a")
    key = str(uuid.uuid4())
    first, second = _reply(owner, sender, key=key), _reply(owner, sender, key=key)
    assert first.json()["created"] is True and second.json()["created"] is False
    assert second.json()["reply"]["status"] == "sent"
    assert len(rp["fake"].calls) == 1 and len(_ledger(rp, sender)) == 1


def test_d_concurrent_double_click_sends_once(rp):
    sender, _ = _conversation(rp)
    clients = [rp["login"]("owner_a"), rp["login"]("owner_a")]
    key = str(uuid.uuid4())
    rp["fake"].delay = 0.3
    barrier = threading.Barrier(2)
    results = []

    def go(c):
        barrier.wait(5)
        results.append(_reply(c, sender, key=key))

    threads = [threading.Thread(target=go, args=(c,)) for c in clients]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert sorted(r.status_code for r in results) == [200, 200]
    assert sorted(r.json()["created"] for r in results) == [False, True]
    assert len(rp["fake"].calls) == 1 and len(_ledger(rp, sender)) == 1


def test_d_the_targeted_claim_never_takes_other_queued_messages(rp):
    from communication import service as communication
    from operator_auth.context import OperatorContext

    ids = rp["ids"]
    other_sender, other_contact = _conversation(rp)
    ctx = OperatorContext(user_id=ids["owner_a"], agency_id=ids["a"], role="agency_owner", is_platform_admin=False,
                          session_id=1, auth_channel="operator_session")
    queued = communication.enqueue(ctx, contact_id=other_contact, channel="whatsapp", communication_type="service",
                                   mode="manual", reason_code="operator_manual", rendered_body="In coda da un altro flusso",
                                   destination_snapshot=other_sender, idempotency_key=f"altro:{uuid.uuid4()}")
    sender, _ = _conversation(rp)
    assert _reply(rp["login"]("owner_a"), sender).json()["reply"]["status"] == "sent"
    status = _q(rp, "SELECT status, attempt_count FROM communication_messages WHERE id = %s", (queued["message"]["id"],))
    assert status == [("queued", 0)]
    assert [c[1] for c in rp["fake"].calls] == [sender]
    # e sullo stesso messaggio un secondo claim non prende nulla
    assert communication.claim_message(ctx, queued["message"]["id"], provider="x", channel="whatsapp") is not None
    assert communication.claim_message(ctx, queued["message"]["id"], provider="x", channel="whatsapp") is None


# ---------------------------------------------------------------------------
# E - errori ed esiti incerti
# ---------------------------------------------------------------------------

def test_e_meta_rejection_is_a_definite_failure(rp):
    sender, _ = _conversation(rp)
    rp["fake"].outcome, rp["fake"].error_code, rp["fake"].error_detail = "rejected", "provider_rejected", "meta_131047_window_closed"
    body = _reply(rp["login"]("owner_a"), sender).json()
    assert body["reply"]["status"] == "failed" and body["reply"]["error_detail"] == "meta_131047_window_closed"
    (row,) = _ledger(rp, sender)
    assert row[5] == "failed" and row[10] == "provider_rejected" and row[9] is None


def test_e_uncertain_timeout_is_never_retried(rp):
    from communication import service as communication
    from operator_auth.context import OperatorContext

    ids = rp["ids"]
    sender, _ = _conversation(rp)
    owner = rp["login"]("owner_a")
    rp["fake"].outcome, rp["fake"].error_code, rp["fake"].error_detail = "indeterminate", "timeout", "read_timeout"
    key = str(uuid.uuid4())
    body = _reply(owner, sender, key=key).json()
    assert body["reply"]["status"] == "indeterminate" and body["has_uncertain_reply"] is True
    # la stessa richiesta non riparte, e nessun claim la riprende da sola
    rp["fake"].outcome = "accepted"
    assert _reply(owner, sender, key=key).json()["created"] is False
    ctx = OperatorContext(user_id=ids["owner_a"], agency_id=ids["a"], role="agency_owner", is_platform_admin=False,
                          session_id=1, auth_channel="operator_session")
    claimed = communication.claim_due(ctx, provider="x", channel="whatsapp", limit=50)
    assert all(c["message"]["destination_snapshot"] != sender for c in claimed)
    assert len(rp["fake"].calls) == 1
    (row,) = _ledger(rp, sender)
    assert row[5] == "indeterminate" and row[10] == "timeout"


def test_e_a_transport_exception_is_an_uncertain_outcome(rp):
    sender, _ = _conversation(rp)
    rp["fake"].raise_exc = RuntimeError("connessione caduta")
    body = _reply(rp["login"]("owner_a"), sender).json()
    assert body["reply"]["status"] == "indeterminate" and body["reply"]["error_detail"] == "RuntimeError"


# ---------------------------------------------------------------------------
# L - consegna dal webhook
# ---------------------------------------------------------------------------

def test_l_delivery_status_from_the_webhook_is_shown(rp):
    sender, _ = _conversation(rp)
    owner = rp["login"]("owner_a")
    body = _reply(owner, sender).json()
    wamid = _ledger(rp, sender)[0][9]
    statuses = [{"id": wamid, "status": "sent", "timestamp": str(int(time.time()))},
                {"id": wamid, "status": "delivered", "timestamp": str(int(time.time()) + 1)}]
    assert rp["post"](_payload(_change(NUMBER_A, statuses=statuses))).status_code == 200
    reply = next(r for r in owner.get(f"{API}/{sender}").json()["replies"] if r["id"] == body["reply"]["id"])
    assert reply["status"] == "sent" and reply["delivery"]["status"] == "delivered"
    assert reply["delivery"]["label"] == "Consegnato"


# ---------------------------------------------------------------------------
# G - presa in carico generica: le risposte manuali escluse, il resto invariato
# ---------------------------------------------------------------------------

def _owner_ctx(b):
    return _ctx(b, "owner_a", "a")


def test_g_a_queued_reply_is_due_now_but_the_generic_claim_never_takes_it(rp, monkeypatch):
    """Nessuna data fittizia: la risposta in coda e' "dovuta" adesso, e il claim
    generico la esclude per canale e motivo. Un altro messaggio WhatsApp in coda
    (motivo diverso) resta candidato come prima."""
    from communication import service as communication
    from message_replies import service

    sender, _ = _conversation(rp)
    other_sender, other_contact = _conversation(rp)
    other = communication.enqueue(_owner_ctx(rp), contact_id=other_contact, channel="whatsapp",
                                  communication_type="service", mode="manual", reason_code="operator_manual",
                                  rendered_body="Altro flusso", destination_snapshot=other_sender,
                                  idempotency_key=f"altro:{uuid.uuid4()}")
    monkeypatch.setattr(service.communication, "claim_message",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("processo interrotto")))
    with pytest.raises(RuntimeError):
        _reply(rp["login"]("owner_a"), sender)
    ((reply_id, status, attempts, due),) = _q(
        rp, "SELECT id, status, attempt_count, scheduled_at <= NOW() FROM communication_messages "
            "WHERE destination_snapshot = %s", (sender,))
    assert (status, attempts, due) == ("queued", 0, True)
    claimed = communication.claim_due(_owner_ctx(rp), provider="generico", channel="whatsapp", limit=50)
    ids = {c["message"]["id"] for c in claimed}
    assert reply_id not in ids and other["message"]["id"] in ids
    assert _q(rp, "SELECT status FROM communication_messages WHERE id = %s", (reply_id,)) == [("queued",)]
    assert rp["fake"].calls == []


# ---------------------------------------------------------------------------
# R - ripresa dopo un'interruzione
# ---------------------------------------------------------------------------

def _crash_before_claim(monkeypatch):
    from message_replies import service

    original = service.communication.claim_message
    monkeypatch.setattr(service.communication, "claim_message",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("processo interrotto")))
    return lambda: monkeypatch.setattr(service.communication, "claim_message", original)


def _crash_after_claim(monkeypatch):
    """Il processo muore dopo la presa in carico: da fuori non si sa se Meta e'
    stato chiamato."""
    from message_replies import service

    original = service._send_time_block
    monkeypatch.setattr(service, "_send_time_block",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("processo interrotto")))
    return lambda: monkeypatch.setattr(service, "_send_time_block", original)


def test_r_the_same_request_resumes_a_reply_never_taken(rp, monkeypatch):
    sender, _ = _conversation(rp)
    owner = rp["login"]("owner_a")
    key = str(uuid.uuid4())
    restore = _crash_before_claim(monkeypatch)
    with pytest.raises(RuntimeError):
        _reply(owner, sender, "Testo originale", key=key)
    st = owner.get(f"{API}/{sender}").json()
    (pending,) = st["replies"]
    assert pending["status"] == "queued" and pending["can_resume"] is True and st["has_uncertain_reply"] is False
    restore()
    body = _reply(owner, sender, "Testo cambiato", key=key).json()            # stessa chiave
    assert body["created"] is False and body["reply"]["status"] == "sent"
    assert rp["fake"].calls == [(NUMBER_A, sender, "Testo originale")]        # il testo registrato, una volta
    (row,) = _ledger(rp, sender)
    assert row[5] == "sent" and row[12] == 1


def test_r_resume_by_id_only_for_its_author_and_with_confirmation(rp, monkeypatch):
    ids = rp["ids"]
    sender, _ = _conversation(rp, agent=ids["agent_a"])
    owner, agent = rp["login"]("owner_a"), rp["login"]("agent_a")
    restore = _crash_before_claim(monkeypatch)
    with pytest.raises(RuntimeError):
        _reply(owner, sender)
    restore()
    reply_id = _ledger(rp, sender)[0][0]
    url = f"{API}/{sender}/replies/{reply_id}/resume"
    assert agent.get(f"{API}/{sender}").json()["replies"][0]["can_resume"] is False
    r = agent.post(url, json={"confirm": True})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "NOT_RESUMABLE"
    assert rp["login"]("owner_b").post(url, json={"confirm": True}).status_code == 404
    assert owner.post(url, json={"confirm": False}).json()["detail"]["code"] == "CONFIRM_REQUIRED"
    assert owner.post(url, json={"confirm": True, "text": "altro"}).status_code == 422
    assert rp["fake"].calls == []
    body = owner.post(url, json={"confirm": True}).json()
    assert body["reply"]["status"] == "sent" and len(rp["fake"].calls) == 1
    again = owner.post(url, json={"confirm": True})
    assert again.status_code == 409 and len(rp["fake"].calls) == 1 and len(_ledger(rp, sender)) == 1


def test_r_resume_still_rechecks_the_window(rp, monkeypatch):
    from message_replies import service

    sender, _ = _conversation(rp, hours_ago=23)
    owner = rp["login"]("owner_a")
    restore = _crash_before_claim(monkeypatch)
    with pytest.raises(RuntimeError):
        _reply(owner, sender)
    restore()
    reply_id = _ledger(rp, sender)[0][0]
    later = datetime.now(timezone.utc) + timedelta(hours=2)
    monkeypatch.setattr(service, "_now", lambda: later)
    r = owner.post(f"{API}/{sender}/replies/{reply_id}/resume", json={"confirm": True})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "WINDOW_CLOSED"
    assert rp["fake"].calls == [] and _ledger(rp, sender)[0][5] == "queued"


def test_r_a_reply_interrupted_while_sending_is_never_resent(rp, monkeypatch):
    from communication import service as communication

    sender, _ = _conversation(rp)
    owner = rp["login"]("owner_a")
    key = str(uuid.uuid4())
    restore = _crash_after_claim(monkeypatch)
    with pytest.raises(RuntimeError):
        _reply(owner, sender, key=key)
    restore()
    (row,) = _ledger(rp, sender)
    assert row[5] == "sending" and row[12] == 1
    st = owner.get(f"{API}/{sender}").json()
    assert st["replies"][0]["status"] == "sending" and st["replies"][0]["can_resume"] is False
    assert st["has_uncertain_reply"] is True
    # stessa richiesta, ripresa per id, claim generico: nessuno la rimanda
    assert _reply(owner, sender, key=key).json()["reply"]["status"] == "sending"
    r = owner.post(f"{API}/{sender}/replies/{row[0]}/resume", json={"confirm": True})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "NOT_RESUMABLE"
    claimed = communication.claim_due(_owner_ctx(rp), provider="x", channel="whatsapp", limit=50)
    assert all(c["message"]["id"] != row[0] for c in claimed)
    # il recupero dei claim scaduti la chiude come esito incerto, mai come reinvio
    _q(rp, "UPDATE communication_messages SET claimed_at = NOW() - interval '1 hour' WHERE id = %s", (row[0],))
    communication.recover_stale(_owner_ctx(rp), stale_after_seconds=60)
    assert _ledger(rp, sender)[0][5] == "indeterminate"
    r = owner.post(f"{API}/{sender}/replies/{row[0]}/resume", json={"confirm": True})
    assert r.status_code == 409 and rp["fake"].calls == [] and len(_ledger(rp, sender)) == 1


def test_r_an_uncertain_reply_cannot_be_resumed(rp):
    sender, _ = _conversation(rp)
    owner = rp["login"]("owner_a")
    rp["fake"].outcome, rp["fake"].error_code = "indeterminate", "timeout"
    reply_id = _reply(owner, sender).json()["reply"]["id"]
    rp["fake"].outcome = "accepted"
    r = owner.post(f"{API}/{sender}/replies/{reply_id}/resume", json={"confirm": True})
    assert r.status_code == 409 and len(rp["fake"].calls) == 1


# ---------------------------------------------------------------------------
# O - l'operatore, riletto al momento dell'invio
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("revoke, restore", [
    ("UPDATE agency_memberships SET status = 'suspended' WHERE operator_user_id = %(u)s AND agency_id = %(a)s",
     "UPDATE agency_memberships SET status = 'active' WHERE operator_user_id = %(u)s AND agency_id = %(a)s"),
    ("UPDATE operator_users SET status = 'disabled' WHERE id = %(u)s",
     "UPDATE operator_users SET status = 'active' WHERE id = %(u)s"),
    ("UPDATE agency_memberships SET role = 'agent' WHERE operator_user_id = %(u)s AND agency_id = %(a)s",
     "UPDATE agency_memberships SET role = 'agency_owner' WHERE operator_user_id = %(u)s AND agency_id = %(a)s"),
    ("UPDATE operator_sessions SET revoked_at = NOW() WHERE operator_user_id = %(u)s AND revoked_at IS NULL",
     None),
], ids=["membership_suspended", "user_disabled", "role_changed", "session_revoked"])
def test_o_operator_revoked_after_enqueue_blocks_without_calling_meta(rp, monkeypatch, revoke, restore):
    ids = rp["ids"]
    params = {"u": ids["owner_a"], "a": ids["a"]}
    sender, _ = _conversation(rp)
    owner = rp["login"]("owner_a")
    _between_enqueue_and_send(monkeypatch, lambda: _q(rp, revoke, params))
    try:
        _reply(owner, sender)
    finally:
        if restore:
            _q(rp, restore, params)
    assert rp["fake"].calls == []
    assert _suppressed(rp, sender) == [("suppressed", "operator_not_authorized", None)]
