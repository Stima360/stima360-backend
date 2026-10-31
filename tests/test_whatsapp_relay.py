"""Inoltro WhatsApp: firma e filtro dei mittenti, senza database."""
from __future__ import annotations

import pytest

from inbound_messages import parser, relay

SECRET = "segreto-sintetico-dell-inoltro-lungo-almeno-32"
BODY = b'{"entry": []}'


def _headers(ts, sig):
    return {relay.TIMESTAMP_HEADER: str(ts), relay.SIGNATURE_HEADER: sig}


def test_signature_covers_timestamp_and_body():
    s = relay.signature(SECRET, "1000", BODY)
    assert s.startswith("sha256=") and len(s) == 71
    assert s != relay.signature(SECRET, "1001", BODY) != relay.signature(SECRET, "1000", BODY + b" ")


def test_verify_accepts_a_fresh_valid_signature(monkeypatch):
    monkeypatch.setenv(relay.RELAY_SECRET_ENV, SECRET)
    relay.verify(BODY, _headers(1000, relay.signature(SECRET, "1000", BODY)), now=1000 + relay.MAX_SKEW_SECONDS)


@pytest.mark.parametrize("secret, headers, status", [
    ("", _headers(1000, "sha256=x"), 503),
    ("troppo-corto", _headers(1000, "sha256=x"), 503),
    (SECRET, _headers(1000, "sha256=" + "0" * 64), 403),
    (SECRET, _headers(1000 - relay.MAX_SKEW_SECONDS - 1, "valid"), 403),
    (SECRET, {relay.TIMESTAMP_HEADER: "1000"}, 403),
    (SECRET, {**_headers(1000, "valid"), "X-Hub-Signature-256": "sha256=x"}, 403),
])
def test_verify_rejects(monkeypatch, secret, headers, status):
    monkeypatch.setenv(relay.RELAY_SECRET_ENV, secret)
    headers = {k: (relay.signature(SECRET, headers[relay.TIMESTAMP_HEADER], BODY) if v == "valid" else v)
               for k, v in headers.items()}
    with pytest.raises(relay.RelayRejected) as exc:
        relay.verify(BODY, headers, now=1000)
    assert exc.value.status == status


def test_allowed_senders_parsing():
    assert relay.allowed_senders("") is None and relay.allowed_senders("  ") is None
    assert relay.allowed_senders("*") == relay.ALL_SENDERS
    assert relay.allowed_senders("+39 300 000 0001, 393000000002 ,123") == {"393000000001", "393000000002"}
    assert relay.allowed_senders("abc") == frozenset()          # elenco senza voci valide: nessuno


def _payload():
    return {"entry": [{"id": "w", "changes": [
        {"field": "messages", "value": {
            "metadata": {"phone_number_id": "1"},
            "contacts": [{"wa_id": "393000000001", "profile": {"name": "Prova"}},
                         {"wa_id": "393999999999", "profile": {"name": "Altro"}}],
            "messages": [{"from": "393000000001", "id": "a", "type": "text", "text": {"body": "si"}},
                         {"from": "393999999999", "id": "b", "type": "text", "text": {"body": "no"}}],
            "statuses": [{"id": "s", "status": "read", "recipient_id": "393999999999"}]}},
        {"field": "account_update", "value": {"event": "X"}}]}]}


def test_filter_keeps_only_allowed_messages_statuses_and_profiles():
    original = _payload()
    out, dropped = relay.filter_payload(original, frozenset({"393000000001"}))
    assert dropped == 3 and original == _payload()               # l'originale non cambia
    parsed = parser.parse(out)
    assert [m.provider_message_id for m in parsed.messages] == ["a"] and parsed.events == []
    assert [c["wa_id"] for c in out["entry"][0]["changes"][0]["value"]["contacts"]] == ["393000000001"]


def test_filter_is_a_no_op_without_restriction():
    for allowed in (None, relay.ALL_SENDERS):
        assert relay.filter_payload(_payload(), allowed) == (_payload(), 0)


def test_filter_with_nobody_allowed_leaves_nothing():
    out, dropped = relay.filter_payload(_payload(), frozenset())
    assert out["entry"] == [] and dropped == 4
