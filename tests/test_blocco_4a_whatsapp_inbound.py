"""BLOCCO-4A - prove offline della ricezione WhatsApp.

Persistenza, idempotenza, concorrenza, agenzie, contatti e permessi sono
provati su PostgreSQL vero in `test_blocco_4a_whatsapp_inbound_postgres.py`.
Qui: lettura del payload, configurazione dei numeri, risposte del webhook,
migrazione, assenza di invii e interfaccia.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from inbound_messages import parser, routing

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "static" / "os_shell" / "assets"
SECRET = "segreto-offline"


def _change(number, **value):
    return {"field": "messages", "value": {"metadata": {"phone_number_id": number}, **value}}


# ---------------------------------------------------------------------------
# lettura del payload
# ---------------------------------------------------------------------------

def test_parser_reads_every_entry_change_message_and_status():
    payload = {"entry": [
        {"changes": [_change("1", messages=[{"from": "393331111111", "id": "w1", "type": "text", "text": {"body": "a"}},
                                            {"from": "393331111111", "id": "w2", "type": "audio",
                                             "audio": {"mime_type": "audio/ogg", "voice": True, "id": "m", "url": "https://x"}}]),
                     _change("1", statuses=[{"id": "o1", "status": "read", "timestamp": "1761800000"}])]},
        {"changes": [_change("2", messages=[{"from": "+44 7700 900123", "id": "w3", "type": "text", "text": {"body": "b"}}]),
                     {"field": "message_template_status_update", "value": {"event": "APPROVED"}}]},
    ]}
    parsed = parser.parse(payload)
    assert [m.provider_message_id for m in parsed.messages] == ["w1", "w2", "w3"]
    assert parsed.messages[1].text_body is None and parsed.messages[1].metadata == {"mime_type": "audio/ogg", "voice": True}
    assert parsed.messages[2].sender_phone == "447700900123"                  # numero internazionale intatto
    assert [(e.event_kind, e.status) for e in parsed.events] == [("status", "read"), ("other", None)]


def test_parser_never_turns_non_text_into_text_and_keeps_minimal_metadata():
    msgs = [
        {"from": "393330000001", "id": "a", "type": "location", "location": {"latitude": 1, "longitude": 2, "name": "Casa"}},
        {"from": "393330000001", "id": "b", "type": "interactive", "interactive": {"button_reply": {"title": "Sì"}}},
        {"from": "393330000001", "id": "c", "type": "reaction", "reaction": {"emoji": "👍", "message_id": "x"}},
        {"from": "393330000001", "id": "d", "type": "unsupported", "errors": [{"code": 131051, "title": "Unsupported"}]},
        {"from": "393330000001", "id": "e", "type": "text", "text": {"body": "risposta"}, "context": {"id": "orig"}},
    ]
    parsed = parser.parse({"entry": [{"changes": [_change("1", messages=msgs)]}]})
    assert [m.text_body for m in parsed.messages] == [None, None, None, None, "risposta"]
    assert parsed.messages[0].metadata == {"name": "Casa"}
    assert parsed.messages[1].metadata == {"label": "Sì"}
    assert parsed.messages[2].metadata == {"emoji": "👍"}
    assert parsed.messages[3].metadata == {"errors": [{"code": 131051, "title": "Unsupported"}]}
    assert parsed.messages[4].metadata == {"reply_to": "orig"}


def test_invalid_messages_become_other_events_not_rows():
    parsed = parser.parse({"entry": [{"changes": [_change("1", messages=[{"from": "abc", "id": "x", "type": "text"},
                                                                         {"from": "393331234567", "type": "text"}])]}]})
    assert parsed.messages == [] and [e.details["reason"] for e in parsed.events] == ["invalid_message"] * 2


@pytest.mark.parametrize("payload", [None, [], {"entry": "x"}, {"entry": [{"changes": "x"}]}, {"entry": [{"changes": ["x"]}]}])
def test_malformed_payloads_are_refused(payload):
    with pytest.raises(parser.MalformedPayload):
        parser.parse(payload)


def test_status_dedupe_key_is_stable_and_distinguishes_states():
    st = lambda s: {"entry": [{"changes": [_change("1", statuses=[{"id": "o", "status": s, "timestamp": "5"}])]}]}  # noqa: E731
    assert parser.parse(st("sent")).events[0].dedupe_key == parser.parse(st("sent")).events[0].dedupe_key
    assert parser.parse(st("sent")).events[0].dedupe_key != parser.parse(st("read")).events[0].dedupe_key


# ---------------------------------------------------------------------------
# configurazione dei numeri
# ---------------------------------------------------------------------------

def test_routes_never_fall_back_and_detect_ambiguity():
    routes = routing.configured_routes(" 111:7, 222:8 ,222:9, malformato, 333:x, :4 ")
    assert routes == {"111": {7}, "222": {8, 9}}
    assert routing.configured_routes("") == {}

    class Cur:
        def __init__(self, exists):
            self.exists = exists

        def execute(self, sql, params):
            self.params = params

        def fetchone(self):
            return {"id": self.params[0]} if self.exists else None

    assert routing.resolve(Cur(True), "111", routes) == ("routed", 7, None)
    assert routing.resolve(Cur(True), "222", routes) == ("unrouted", None, "ambiguous_number")
    assert routing.resolve(Cur(True), "999", routes) == ("unrouted", None, "unknown_number")
    assert routing.resolve(Cur(True), None, routes) == ("unrouted", None, "unknown_number")
    assert routing.resolve(Cur(False), "111", routes) == ("unrouted", None, "agency_not_found")


# ---------------------------------------------------------------------------
# risposte del webhook
# ---------------------------------------------------------------------------

@pytest.fixture
def webhook(monkeypatch):
    import main

    monkeypatch.setenv("WHATSAPP_APP_SECRET", SECRET)
    client = TestClient(main.app, raise_server_exceptions=False)

    def post(payload, **kw):
        raw = json.dumps(payload).encode() if not isinstance(payload, bytes) else payload
        sig = "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
        return client.post("/webhook/whatsapp", content=raw, headers={"X-Hub-Signature-256": sig})

    return main, post


GOOD = {"entry": [{"changes": [_change("1", messages=[{"from": "393331234567", "id": "w", "type": "text",
                                                       "text": {"body": "Ciao"}}])]}]}


def test_webhook_answers_200_only_after_persistence(webhook, monkeypatch):
    main, post = webhook
    calls = []
    monkeypatch.setattr(main.inbound_service, "ingest", lambda parsed: calls.append(parsed) or {})
    assert post(GOOD).status_code == 200 and len(calls) == 1


def test_webhook_persistence_error_is_500_and_never_swallowed(webhook, monkeypatch):
    main, post = webhook

    def boom(parsed):
        raise RuntimeError("connessione persa con dati personali 393331234567")

    monkeypatch.setattr(main.inbound_service, "ingest", boom)
    r = post(GOOD)
    assert r.status_code == 500 and "393331234567" not in r.text


def test_webhook_without_migration_is_503(webhook, monkeypatch):
    main, post = webhook

    def missing(parsed):
        raise main.inbound_service.NotInstalled("NOT_INSTALLED", "manca la 118")

    monkeypatch.setattr(main.inbound_service, "ingest", missing)
    assert post(GOOD).status_code == 503


def test_webhook_malformed_payload_is_400_without_persistence(webhook, monkeypatch):
    main, post = webhook
    monkeypatch.setattr(main.inbound_service, "ingest", lambda parsed: pytest.fail("persistenza non attesa"))
    assert post(b"{non json").status_code == 400
    assert post({"entry": "x"}).status_code == 400


# ---------------------------------------------------------------------------
# migrazione e assenza di invii
# ---------------------------------------------------------------------------

def test_migration_118_is_valid_additive_and_keeps_whatsapp_incoming():
    sys.path.insert(0, str(ROOT / "scripts"))
    import p26_migrate as runner

    tutte = runner.discover_migrations()
    runner.verify_contiguous(tutte)
    # SENTINELLA AGGIORNATA DA BLOCCO-4D: la 118 non e' piu' l'ultima (119:
    # registro della simulazione delle risposte automatiche). La 118 resta valida.
    m118 = next(m for m in tutte if m.version == "118_whatsapp_inbound")
    assert m118.down_available and runner.validate_migration(m118) == []
    up = (ROOT / "migrations" / "118_whatsapp_inbound.sql").read_text(encoding="utf-8")
    executable = "\n".join(r for r in up.splitlines() if not r.lstrip().startswith("--"))
    assert "whatsapp_incoming" not in executable
    assert not re.search(r"\b(ALTER TABLE|DROP|DELETE FROM|UPDATE \w+ SET)\b", executable)
    assert "UNIQUE (provider_message_id)" in executable and "UNIQUE (dedupe_key)" in executable
    down = (ROOT / "migrations" / "118_whatsapp_inbound_down.sql").read_text(encoding="utf-8")
    down_exec = "\n".join(r for r in down.splitlines() if not r.lstrip().startswith("--"))
    assert "RAISE EXCEPTION" in down_exec and "DELETE FROM" not in down_exec and "TRUNCATE" not in down_exec
    assert "whatsapp_incoming" not in down_exec


def test_inbound_module_never_sends():
    for path in (ROOT / "inbound_messages").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for forbidden in ("requests", "httpx", "urllib", "graph.facebook", "invia_whatsapp", "WHATSAPP_TOKEN",
                          "WHATSAPP_PHONE_ID", "WHATSAPP_SERVICE_URL", "/send"):
            assert forbidden not in text, (path.name, forbidden)


def test_routes_are_mounted_behind_the_operator_session():
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    line = next(r for r in main.splitlines() if "inbound_messages_router" in r and "include_router" in r)
    assert "require_authenticated_operator" in line
    router = (ROOT / "inbound_messages" / "router.py").read_text(encoding="utf-8")
    assert "@router.post" in router and router.count("@router.post") == 1 and "/link" in router


# ---------------------------------------------------------------------------
# interfaccia
# ---------------------------------------------------------------------------

def test_shell_has_the_messages_section_and_route():
    main_js = (APP / "main.js").read_text(encoding="utf-8")
    assert "{ name: 'messaggi', label: 'Messaggi WhatsApp' }" in main_js
    assert "registerRoute('messaggi'" in main_js
    # ogni voce della sidebar ha la sua icona: senza, la Shell si ferma (creaVoceNav)
    assert re.search(r"^  messaggi: \[", main_js, re.M)


def test_messages_view_reads_safely_and_offers_no_send():
    js = (APP / "views" / "messaggi.js").read_text(encoding="utf-8")
    assert "innerHTML" not in js and "textContent" in js
    assert "can_link" in js and "Da associare" in js and "/link" in js
    lowered = js.lower()
    # SENTINELLA AGGIORNATA DA BLOCCO-4B: la pagina ospita ora la risposta manuale
    # (components/whatsapp-reply.js, /api/message-replies, con conferma). Resta
    # vietato ogni altro canale d'invio: il vecchio /api/admin/whatsapp/reply,
    # i link wa.me, l'apertura di finestre o URL.
    for forbidden in ("/api/admin/whatsapp", "/reply", "/send", "wa.me", "window.open", "href ="):
        assert forbidden not in lowered, forbidden
    assert "partono solo dopo la tua conferma" in lowered
