"""WHATSAPP COEXISTENCE senza database: lettura dei webhook, filtro dei mittenti
per casella, client Graph, rifiuto senza migrazione 132 e il pannello del browser.
Numeri e testi FINTI.

    P1  eco dall'app: messaggio `out`, il cliente e' `to`
    P2  storico: direzione per messaggio, ora originale, stato, avanzamento senza dati personali,
        storico rifiutato come evento
    P3  rubrica: aggiunte e rimozioni; tipi diversi restano eventi
    P4  filtro per casella: il numero personale accetta tutti, il numero 1 resta filtrato,
        anche su eco, storico e rubrica
    P5  client Graph: mai un'eccezione, codici tecnici senza dati, versione dichiarata
    P6  senza la 132 un payload Coexistence e' rifiutato (503 a Meta, che ritenta)
    P7  pannello: esito accettato solo dalle origini di Facebook; «Confermato da Meta» solo se lo e'
"""
from __future__ import annotations

import json
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path

import pytest
import requests

from inbound_messages import parser, relay

ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "static" / "os_shell" / "assets" / "components" / "whatsapp-coexistence.js"
N1, N2 = "900000000000001", "900000000000003"
CUSTOMER, OTHER = "393330000001", "393330000002"


def _value(number):
    return {"messaging_product": "whatsapp", "metadata": {"display_phone_number": "39000", "phone_number_id": number}}


def _payload(*changes):
    return {"object": "whatsapp_business_account", "entry": [{"id": "w", "changes": list(changes)}]}


def _echo(number, to, body="ciao", mid="wamid.e1"):
    return {"field": "smb_message_echoes", "value": {**_value(number), "message_echoes": [
        {"from": "39000", "to": to, "id": mid, "timestamp": "1761800000", "type": "text", "text": {"body": body}}]}}


def _history(number, threads, **meta):
    return {"field": "history", "value": {**_value(number), "history": [
        {"metadata": {"phase": 0, "chunk_order": 1, "progress": 40, **meta}, "threads": threads}]}}


def _sync(number, *items):
    return {"field": "smb_app_state_sync", "value": {**_value(number), "state_sync": list(items)}}


def _contact_item(phone, action="add", name="Nome Finto"):
    return {"type": "contact", "contact": {"full_name": name, "first_name": name.split()[0], "phone_number": phone},
            "action": action, "metadata": {"timestamp": "1761800000"}}


# ---------------------------------------------------------------------------
# P1-P3 parser
# ---------------------------------------------------------------------------

def test_p1_echo_is_an_outgoing_message_to_the_customer():
    out = parser.parse(_payload(_echo(N2, "+39 333 000 0001", "Arrivo alle 10")))
    m, = out.messages
    assert (m.direction, m.origin, m.sender_phone, m.text_body, m.phone_number_id) == \
        ("out", "app_echo", CUSTOMER, "Arrivo alle 10", N2)
    assert out.coexistence and m.coexistence
    bad = parser.parse(_payload({"field": "smb_message_echoes", "value": {**_value(N2), "message_echoes": [{"id": "x"}]}}))
    assert bad.messages == [] and bad.events[0].details["reason"] == "invalid_echo"


def test_p2_history_direction_time_status_and_progress():
    threads = [{"id": CUSTOMER, "messages": [
        {"from": CUSTOMER, "id": "h1", "timestamp": "1700000000", "type": "text", "text": {"body": "Salve"},
         "history_context": {"status": "READ"}},
        {"from": "39000", "id": "h2", "timestamp": "1700000060", "type": "image",
         "image": {"mime_type": "image/jpeg", "id": "media.1", "url": "https://segreto"}}]}]
    out = parser.parse(_payload(_history(N2, threads)))
    a, b = out.messages
    assert (a.direction, a.origin, a.sender_phone, a.metadata["history_status"]) == ("in", "history", CUSTOMER, "read")
    assert (b.direction, b.sender_phone, b.text_body) == ("out", CUSTOMER, None)
    assert b.metadata == {"mime_type": "image/jpeg", "media_id": "media.1"}         # mai l'URL
    assert a.provider_timestamp.year == 2023
    progress, = [e for e in out.events if e.details.get("reason") == "history_progress"]
    assert progress.details == {"reason": "history_progress", "phase": 0, "chunk_order": 1, "progress": 40, "messages": 2}
    assert CUSTOMER not in json.dumps(progress.details)
    declined = parser.parse(_payload({"field": "history", "value": {**_value(N2), "history": [
        {"metadata": {"phase": 0}, "errors": [{"code": 2593109, "title": "History sharing declined"}]}]}}))
    assert declined.messages == [] and {e.details["reason"] for e in declined.events} == {"history_errors", "history_progress"}


def test_p3_address_book_changes_and_other_sync_types():
    out = parser.parse(_payload(_sync(N2, _contact_item("+39 333 000 0001"), _contact_item(OTHER, "remove"),
                                      {"type": "label", "label": {"name": "x"}})))
    assert [(c.contact_phone, c.action, c.full_name) for c in out.app_contacts] == \
        [(CUSTOMER, "add", "Nome Finto"), (OTHER, "remove", "Nome Finto")]
    assert [e.details["reason"] for e in out.events] == ["state_sync_other"]
    assert out.coexistence and not parser.parse(_payload({"field": "messages", "value": _value(N1)})).coexistence


# ---------------------------------------------------------------------------
# P4 filtro per casella
# ---------------------------------------------------------------------------

def test_p4_per_inbox_sender_filter_on_every_coexistence_field(monkeypatch):
    monkeypatch.setenv("WHATSAPP_INBOUND_ALLOWED_SENDERS", CUSTOMER)
    monkeypatch.setenv(f"WHATSAPP_INBOUND_ALLOWED_SENDERS__{N2}", "*")
    payload = _payload(
        _echo(N1, OTHER, mid="e-n1-other"), _echo(N1, CUSTOMER, mid="e-n1-ok"), _echo(N2, OTHER, mid="e-n2-other"),
        _history(N1, [{"id": OTHER, "messages": [{"from": OTHER, "id": "h-other", "timestamp": "1", "type": "text",
                                                   "text": {"body": "x"}}]}]),
        _sync(N1, _contact_item(OTHER), _contact_item(CUSTOMER)),
        {"field": "messages", "value": {**_value(N1), "messages": [
            {"from": OTHER, "id": "m-other", "type": "text", "text": {"body": "x"}}]}})
    filtered, dropped = relay.filter_payload(payload, relay.allowed_senders())
    parsed = parser.parse(filtered)
    assert sorted(m.provider_message_id for m in parsed.messages) == ["e-n1-ok", "e-n2-other"]
    assert [c.contact_phone for c in parsed.app_contacts] == [CUSTOMER]
    assert dropped == 4                       # eco, storico, rubrica e messaggio del mittente non ammesso sul numero 1
    # l'avanzamento dello storico resta (senza thread): dice a che punto e' Meta
    assert any(e.details.get("reason") == "history_progress" for e in parsed.events)
    # nessuna variabile: nulla cambia, come nel 4A
    monkeypatch.delenv("WHATSAPP_INBOUND_ALLOWED_SENDERS")
    monkeypatch.delenv(f"WHATSAPP_INBOUND_ALLOWED_SENDERS__{N2}")
    assert relay.filter_payload(payload, relay.allowed_senders()) == (payload, 0)


# ---------------------------------------------------------------------------
# P5 client Graph
# ---------------------------------------------------------------------------

class _Session:
    def __init__(self, outcome):
        self.outcome, self.calls = outcome, []

    def request(self, method, url, params=None, json=None, headers=None, timeout=None):
        self.calls.append((method, url, params, json, headers))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        status, body = self.outcome

        class R:
            status_code = status

            def json(self_inner):
                if body is None:
                    raise ValueError
                return body
        return R()


def test_p5_graph_client_never_raises_and_hides_details():
    from whatsapp_coexistence.graph import Graph

    s = _Session((200, {"access_token": "t"}))
    g = Graph("v99.0", session=s)
    assert g.exchange_code("app", "secret", "code") == (True, {"access_token": "t"}, None)
    assert s.calls[0][1] == "https://graph.facebook.com/v99.0/oauth/access_token"
    ok, body, err = Graph("v99.0", session=_Session((400, {"error": {"code": 100, "error_subcode": 33,
                                                                     "message": "Numero +39333 non valido"}}))) \
        .phone_number("tok", N2)
    assert (ok, body, err) == (False, None, "meta_100_33")
    assert Graph("v99.0", session=_Session(requests.exceptions.ReadTimeout())).request_sync("t", N2, "history") == \
        (False, None, "timeout")
    assert Graph("v99.0", session=_Session(requests.exceptions.ConnectionError())).subscribe_app("t", "1") == \
        (False, None, "network")
    sub = _Session((200, {"success": True}))
    Graph("v99.0", session=sub).subscribe_app("tok", "123", callback_url="https://crm.example/webhook", verify_token="v")
    assert sub.calls[0][3] == {"override_callback_uri": "https://crm.example/webhook", "verify_token": "v"}
    assert sub.calls[0][4] == {"Authorization": "Bearer tok"}
    sync = _Session((200, {}))
    Graph("v99.0", session=sync).request_sync("tok", N2, "smb_app_state_sync")
    assert sync.calls[0][3] == {"messaging_product": "whatsapp", "sync_type": "smb_app_state_sync"}


# ---------------------------------------------------------------------------
# P6 senza la 132
# ---------------------------------------------------------------------------

def test_p6_coexistence_payload_is_refused_without_migration_132(monkeypatch):
    from inbound_messages import repository, service

    @contextmanager
    def fake_cursor(commit=False):
        yield None, object()

    monkeypatch.setattr(service, "core_cursor", fake_cursor)
    monkeypatch.setattr(repository, "installed", lambda cur: True)
    monkeypatch.setattr(repository, "coexistence_installed", lambda cur: False)
    with pytest.raises(service.NotInstalled) as err:
        service.ingest(parser.parse(_payload(_echo(N2, CUSTOMER))), routes={N2: {1}})
    assert err.value.code == "COEXISTENCE_NOT_INSTALLED" and err.value.status == 503


# ---------------------------------------------------------------------------
# P7 il pannello del browser
# ---------------------------------------------------------------------------

@pytest.mark.skipif(shutil.which("node") is None, reason="node non disponibile")
def test_p7_panel_accepts_only_facebook_and_never_claims_a_connection():
    code = f"""
globalThis.window = {{}}; globalThis.document = {{}};
const m = await import({json.dumps(PANEL.as_uri())});
const ev = (origin, data) => ({{ origin, data }});
const fin = JSON.stringify({{ type: 'WA_EMBEDDED_SIGNUP', event: 'FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING',
  data: {{ waba_id: '1000', phone_number_id: '{N2}', business_id: '2000' }} }});
const out = {{
  ok: m.sessionInfoFrom(ev('https://www.facebook.com', fin)),
  web: m.sessionInfoFrom(ev('https://web.facebook.com', fin)),
  evil: m.sessionInfoFrom(ev('https://www.facebook.com.evil.test', fin)),
  other: m.sessionInfoFrom(ev('https://example.test', fin)),
  cancel: m.sessionInfoFrom(ev('https://www.facebook.com', {{ type: 'WA_EMBEDDED_SIGNUP', event: 'CANCEL',
    data: {{ current_step: 'PHONE_NUMBER_SETUP' }} }})),
  junk: m.sessionInfoFrom(ev('https://www.facebook.com', 'non json')),
  waiting: m.onboardingLines({{ connected: false, status_label: 'Procedura completata, in attesa della conferma di Meta',
    meta: {{}}, evidence: {{}}, routed: false, token_present: false }}),
  done: m.onboardingLines({{ connected: true, meta_confirmed_at: '2026-11-06T10:00:00Z',
    meta: {{ platform_type: 'CLOUD_API', is_on_biz_app: true }},
    evidence: {{ live_messages: 2, app_echoes: 1, history_messages: 30, app_contacts_present: 12,
      history: {{ progress: 100 }} }}, routed: true, token_present: true }}),
}};
console.log(JSON.stringify(out));
"""
    r = subprocess.run(["node", "--input-type=module", "-e", code], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["ok"] == {"finished": True, "waba_id": "1000", "phone_number_id": N2, "business_id": "2000"}
    assert out["web"]["finished"] is True
    assert out["evil"] is None and out["other"] is None and out["junk"] is None
    assert out["cancel"] == {"finished": False, "cancelled": True, "step": "PHONE_NUMBER_SETUP"}
    waiting = " ".join(out["waiting"])
    assert "Confermato" not in waiting and "in attesa della conferma di Meta" in waiting
    assert "non è ancora instradato" in waiting and "manca il token" in waiting
    done = " ".join(out["done"])
    assert done.startswith("Confermato da Meta") and "app WhatsApp Business attiva sul telefono" in done
    assert "30 dallo storico" in done and "12 contatti in rubrica" in done and "Storico: 100%" in done


def test_p8_panel_and_views_never_use_innerhtml():
    for rel in ("components/whatsapp-coexistence.js", "views/messaggi.js", "views/caselle-whatsapp.js",
                "components/contact-import.js"):
        code = (ROOT / "static" / "os_shell" / "assets" / rel).read_text(encoding="utf-8")
        assert ".innerHTML" not in code and "insertAdjacentHTML" not in code, rel
    panel = PANEL.read_text(encoding="utf-8")
    # niente segreti o token nel browser: solo valori pubblici e il codice monouso inoltrato al server
    for forbidden in ("client_secret", "WHATSAPP_APP_SECRET", "access_token"):
        assert forbidden not in panel, forbidden
