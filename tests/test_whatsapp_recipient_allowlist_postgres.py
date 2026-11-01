"""Allowlist dei destinatari WhatsApp (`WHATSAPP_OUTBOUND_ALLOWED_RECIPIENTS`), su
PostgreSQL VERO: banco e fixture del 4B (`rp`: trasporto simulato, trappole
sugli invii esterni). Qui la fixture `rp` ha `*`; ogni prova la restringe.

    A  solo il numero in allowlist riceve; gli altri sono bloccati prima di Meta,
       anche chiamando l'API direttamente; allowlist assente o vuota = nessuno
    W  finestra di 24 ore e permessi restano
    L  la risposta e' nella conversazione e nella scheda contatto; nessun doppio invio
    T  il trasporto reale rifiuta da solo un destinatario fuori allowlist
    D  la risposta automatica rispetta la stessa allowlist
"""
from __future__ import annotations

import uuid

from message_replies import transport
from tests.test_blocco_4b_whatsapp_reply_postgres import (  # noqa: F401 - fixture
    API,
    FakeTransport,
    _conversation,
    _ledger,
    _reply,
    app,
    banco,
    pytestmark,
    rp,
    wa,
)
from tests.test_blocco_2c_integrated_postgres import _q  # noqa: F401


def _state(client, sender):
    return client.get(f"{API}/{sender}").json()


def _codes(state):
    return [b["code"] for b in state["blocks"]]


# ---------------------------------------------------------------------------
# A - allowlist
# ---------------------------------------------------------------------------

def test_a_only_the_listed_recipient_can_be_answered(rp, monkeypatch):
    owner = rp["login"]("owner_a")
    allowed, other = _conversation(rp), _conversation(rp)
    monkeypatch.setenv("WHATSAPP_OUTBOUND_ALLOWED_RECIPIENTS", f"+39 {allowed[0][2:5]} {allowed[0][5:]}")
    assert _state(owner, allowed[0])["can_reply"] is True
    st = _state(owner, other[0])
    assert st["can_reply"] is False and _codes(st) == ["RECIPIENT_NOT_ALLOWED"]
    ok = _reply(owner, allowed[0], "Risposta consentita")
    assert ok.status_code == 200 and ok.json()["reply"]["status"] == "sent"
    denied = _reply(owner, other[0], "Non deve partire")
    assert denied.status_code == 409 and denied.json()["detail"]["code"] == "RECIPIENT_NOT_ALLOWED"
    assert [c[1] for c in rp["fake"].calls] == [allowed[0]]
    assert _ledger(rp, other[0]) == []                                    # nemmeno in coda


def test_a_without_the_variable_nobody_can_be_answered(rp, monkeypatch):
    owner = rp["login"]("owner_a")
    sender, _ = _conversation(rp)
    for value in (None, "", "  ", "abc"):
        if value is None:
            monkeypatch.delenv("WHATSAPP_OUTBOUND_ALLOWED_RECIPIENTS", raising=False)
        else:
            monkeypatch.setenv("WHATSAPP_OUTBOUND_ALLOWED_RECIPIENTS", value)
        st = _state(owner, sender)
        assert st["can_reply"] is False and "RECIPIENT_NOT_ALLOWED" in _codes(st), value
        assert _reply(owner, sender).status_code == 409
    assert rp["fake"].calls == []


def test_a_the_final_gate_holds_even_if_the_allowlist_changes_after_the_queue(rp, monkeypatch):
    """La richiesta supera i controlli, poi l'allowlist cambia prima del claim:
    la risposta resta soppressa, Meta non viene chiamata."""
    from message_replies import service

    owner = rp["login"]("owner_a")
    sender, _ = _conversation(rp)
    monkeypatch.setenv("WHATSAPP_OUTBOUND_ALLOWED_RECIPIENTS", sender)
    real = service._deliver

    def flip_then_deliver(ctx, sender_phone, message):
        monkeypatch.setenv("WHATSAPP_OUTBOUND_ALLOWED_RECIPIENTS", "393000000009")
        return real(ctx, sender_phone, message)

    monkeypatch.setattr(service, "_deliver", flip_then_deliver)
    r = _reply(owner, sender, "Cambio in corsa")
    assert r.status_code == 200 and r.json()["reply"]["status"] == "suppressed"
    assert r.json()["reply"]["suppressed_reason"] == "recipient_not_allowed"
    assert rp["fake"].calls == []


# ---------------------------------------------------------------------------
# W - finestra e permessi restano
# ---------------------------------------------------------------------------

def test_w_window_and_permissions_still_apply_to_the_listed_recipient(rp, monkeypatch):
    ids = rp["ids"]
    old, _ = _conversation(rp, hours_ago=25)
    mine, _ = _conversation(rp, agent=ids["agent2_a"])
    monkeypatch.setenv("WHATSAPP_OUTBOUND_ALLOWED_RECIPIENTS", f"{old},{mine}")
    owner = rp["login"]("owner_a")
    st = _state(owner, old)
    assert st["can_reply"] is False and _codes(st) == ["WINDOW_CLOSED"]
    assert _reply(owner, old).status_code == 409
    agent = rp["login"]("agent_a")                                       # non assegnato: non la vede
    assert agent.get(f"{API}/{mine}").status_code == 404 and _reply(agent, mine).status_code == 404
    assert rp["login"]("owner_b").get(f"{API}/{mine}").status_code == 404
    assert _reply(owner, mine, confirm=False).status_code == 409        # conferma obbligatoria
    assert rp["fake"].calls == []


# ---------------------------------------------------------------------------
# L - registrazione e doppio invio
# ---------------------------------------------------------------------------

def test_l_reply_is_recorded_in_conversation_and_contact_card_without_double_send(rp, monkeypatch):
    ids = rp["ids"]
    owner = rp["login"]("owner_a")
    sender, contact = _conversation(rp)
    monkeypatch.setenv("WHATSAPP_OUTBOUND_ALLOWED_RECIPIENTS", sender)
    key = str(uuid.uuid4())
    first = _reply(owner, sender, "La richiamo tra poco.", key=key)
    again = _reply(owner, sender, "La richiamo tra poco.", key=key)
    assert first.status_code == again.status_code == 200
    assert first.json()["created"] is True and again.json()["created"] is False
    assert len(rp["fake"].calls) == 1 and rp["fake"].calls[0][1] == sender
    rows = _ledger(rp, sender)
    assert len(rows) == 1 and rows[0][5] == "sent" and rows[0][7] == ids["owner_a"] and rows[0][8] == "La richiamo tra poco."
    # nella conversazione
    st = _state(owner, sender)
    assert [r["text"] for r in st["replies"]] == ["La richiamo tra poco."] and st["replies"][0]["operator"]
    # nella scheda contatto (registro comunicazioni del contatto)
    card = owner.get(f"/api/communication/contacts/{contact}/messages")
    assert card.status_code == 200, card.text
    body = card.json()
    items = body if isinstance(body, list) else body.get("items") or body.get("messages")
    mine = [m for m in items if m.get("reason_code") == "operator_reply" or m.get("motivo") == "Risposta operatore"
            or "Risposta operatore" in str(m)]
    assert mine, body
    assert any("La richiamo" in str(m) for m in mine)


# ---------------------------------------------------------------------------
# T - il trasporto reale rifiuta da solo
# ---------------------------------------------------------------------------

def test_t_the_meta_transport_refuses_an_unlisted_recipient_without_any_request(monkeypatch):
    calls = []

    class Session:
        def post(self, *a, **k):
            calls.append(a)
            raise AssertionError("nessuna richiesta doveva partire")

    monkeypatch.setenv("WHATSAPP_OUTBOUND_ALLOWED_RECIPIENTS", "393000000001")
    t = transport.MetaCloudTransport("tok", "v21.0", session=Session())
    result = t.send("900000000000001", "393000000002", "Ciao")
    assert result.outcome == "rejected" and result.error_detail == "recipient_not_allowed" and calls == []
    monkeypatch.delenv("WHATSAPP_OUTBOUND_ALLOWED_RECIPIENTS")
    assert t.send("900000000000001", "393000000001", "Ciao").outcome == "rejected" and calls == []


# ---------------------------------------------------------------------------
# D - risposta automatica
# ---------------------------------------------------------------------------

def test_d_the_auto_reply_engine_respects_the_same_allowlist(rp, monkeypatch):
    from auto_replies import service as auto_service

    ids = rp["ids"]
    fake = FakeTransport()
    monkeypatch.setattr(transport, "current", lambda: fake)
    monkeypatch.setenv("WHATSAPP_AUTO_REPLY_MODE", "send")
    monkeypatch.setenv("WHATSAPP_OUTBOUND_ALLOWED_RECIPIENTS", "393000000009")
    _q(rp, "INSERT INTO whatsapp_auto_reply_settings (agency_id, mode, enabled_at) VALUES (%s, 'send', NOW()) "
           "ON CONFLICT (agency_id) DO UPDATE SET mode = 'send', enabled_at = NOW()", (ids["a"],))
    sender, _ = _conversation(rp, text="Buongiorno, vorrei informazioni su un immobile")
    out = auto_service.run_for_agency(ids["a"])
    assert out["sent"] == 0 and fake.calls == []
    assert _q(rp, "SELECT count(*) FROM communication_messages WHERE destination_snapshot = %s AND status = 'sent'",
              (sender,))[0][0] == 0
