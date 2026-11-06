"""WHATSAPP COEXISTENCE su PostgreSQL VERO - banco del 4A e delle due caselle
(fixture `dc`: migrazioni dal runner compresa la 132, webhook firmato con un
segreto sintetico, ogni invio esterno e' una trappola). Numeri, nomi e testi
sono FINTI; nessuna chiamata reale a Meta (Graph e allegati sono simulati).

    K1  eco dall'app e storico entrano nella conversazione della casella, in ordine
        d'ora originale, con l'etichetta giusta; non contano come non letti; il
        contatto si collega anche sui messaggi dell'azienda
    K2  duplicati: eco, storico e rubrica reinviati non duplicano; un'eco con il wamid
        di una risposta gia' inviata dal CRM non la duplica
    K3  regole del cliente: lo STOP scritto dall'app non revoca, le automazioni non
        decidono su eco e storico, la finestra di 24 ore conta solo il cliente
    K4  permessi e isolamento: casella riservata, agente, altra agenzia, numero non instradato
    K5  rubrica dell'app: aggiunte, rimozioni, ordine di consegna; import con le regole della fase B
    K6  allegati: solo a chi vede il messaggio, tipi pericolosi mai in linea, limiti ed errori
    K7  collegamento Meta: solo il titolare, nessun token salvato, «Confermato da Meta» solo
        se Meta lo dice, casella protetta per impostazione predefinita
    K8  migrazione 132: senza la 132 i webhook Coexistence sono rifiutati (503); il
        rientro rifiuta quando ci sono dati
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import psycopg2
import pytest

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
    _sender,
    app,
    banco,
    pytestmark,
    wa,
)
from tests.test_whatsapp_due_caselle_postgres import NUMBER_A2, REPLIES, _inbox2, _send, dc  # noqa: F401

COEX = "/api/whatsapp-coexistence"
MEDIA = "/api/whatsapp-coexistence/messages"
IMPORTS = "/api/contact-imports"
WABA = "1000000000000077"


def _ts(dt: datetime) -> str:
    return str(int(dt.timestamp()))


def _value(number):
    return {"messaging_product": "whatsapp",
            "metadata": {"display_phone_number": "39000000002", "phone_number_id": number}}


def _echo(number, customer, body="Risposta dal telefono", *, mid=None, ts=None, kind="text", **extra):
    msg = {"from": "39000000002", "to": customer, "id": mid or f"wamid.echo.{uuid.uuid4().hex}",
           "timestamp": ts or _ts(datetime.now(timezone.utc)), "type": kind}
    if kind == "text":
        msg["text"] = {"body": body}
    msg.update(extra)
    return {"field": "smb_message_echoes", "value": {**_value(number), "message_echoes": [msg]}}


def _history(number, threads, *, phase=0, progress=100, chunk=1, errors=None):
    block = {"metadata": {"phase": phase, "chunk_order": chunk, "progress": progress}, "threads": threads}
    if errors:
        block["errors"] = errors
    return {"field": "history", "value": {**_value(number), "history": [block]}}


def _hmsg(sender_digits, body, when, *, mid=None, status="READ"):
    return {"from": sender_digits, "id": mid or f"wamid.hist.{uuid.uuid4().hex}", "timestamp": _ts(when),
            "type": "text", "text": {"body": body}, "history_context": {"status": status}}


def _book(number, *items):
    return {"field": "smb_app_state_sync", "value": {**_value(number), "state_sync": [
        {"type": "contact", "contact": {"full_name": n, "first_name": n.split()[0], "phone_number": p},
         "action": a, "metadata": {"timestamp": _ts(t)}} for (n, p, a, t) in items]}}


def _conv(client, sender, number):
    r = client.get(f"{API}/conversations/{sender}?inbox={number}")
    assert r.status_code == 200, r.text
    return r.json()


def _item(client, sender, number):
    items = client.get(f"{API}/conversations").json()["items"]
    return next((c for c in items if c["sender_phone"] == sender and c["phone_number_id"] == number), None)


# ---------------------------------------------------------------------------
# K1 - eco e storico nella conversazione
# ---------------------------------------------------------------------------

def test_k1_echoes_and_history_join_the_conversation_in_time_order(dc):
    ids = dc["ids"]
    sender = _sender()
    contact = _contact(dc, ids["a"], "Cliente Coex", sender)
    owner, _ = _inbox2(dc, access="agency", automations=False)
    now = datetime.now(timezone.utc)
    history = _history(NUMBER_A2, [{"id": sender, "messages": [
        _hmsg(sender, "Buongiorno, la casa è ancora libera?", now - timedelta(days=20)),
        _hmsg("39000000002", "Sì, quando vuole vederla?", now - timedelta(days=20, minutes=-5)),
    ]}], progress=50)
    assert dc["post"](_payload(history)).status_code == 200
    _send(dc, sender, "Posso passare domani?", NUMBER_A2)
    assert dc["post"](_payload(_echo(NUMBER_A2, sender, "Certo, alle 10"))).status_code == 200

    conv = _conv(owner, sender, NUMBER_A2)
    got = [(m["direction"], m["origin"], m["text"], m["origin_label"]) for m in reversed(conv["messages"])]
    assert got == [("in", "history", "Buongiorno, la casa è ancora libera?", "Dallo storico"),
                   ("out", "history", "Sì, quando vuole vederla?", "Dallo storico · inviato da te"),
                   ("in", "webhook", "Posso passare domani?", None),
                   ("out", "app_echo", "Certo, alle 10", "Dal telefono")], got
    # il contatto si collega su TUTTI i messaggi della conversazione, anche quelli dell'azienda
    assert conv["contact"]["id"] == contact
    assert {r[0] for r in _q(dc, "SELECT contact_id FROM whatsapp_inbound_messages WHERE sender_phone = %s",
                              (sender,))} == {contact}
    # lo storico conserva l'ora originale (non sembra arrivato adesso)
    oldest = _q(dc, "SELECT received_at FROM whatsapp_inbound_messages WHERE sender_phone = %s AND origin = 'history' "
                    "ORDER BY received_at LIMIT 1", (sender,))[0][0]
    assert oldest < now - timedelta(days=19)
    # non letti: solo il messaggio del cliente arrivato in diretta
    item = _item(owner, sender, NUMBER_A2)
    assert item["unread"] == 1 and item["messages"] == 4
    assert item["last_message"]["direction"] == "out" and item["last_message"]["text"] == "Certo, alle 10"
    # l'avanzamento dello storico e' un evento senza dati personali
    ev = _q(dc, "SELECT details FROM whatsapp_webhook_events WHERE phone_number_id = %s "
                "AND details->>'reason' = 'history_progress' ORDER BY id DESC LIMIT 1", (NUMBER_A2,))[0][0]
    assert ev["progress"] == 50 and ev["messages"] == 2 and sender not in json.dumps(ev)


def test_k1b_business_first_conversation_exists_without_customer_messages(dc):
    """Una chat iniziata dal titolare sul telefono (solo eco) compare comunque, col nome della rubrica."""
    ids = dc["ids"]
    owner, _ = _inbox2(dc, access="agency", automations=False)
    sender = _sender()
    now = datetime.now(timezone.utc)
    assert dc["post"](_payload(_book(NUMBER_A2, ("Lucia Rubrica Finta", sender, "add", now)))).status_code == 200
    assert dc["post"](_payload(_echo(NUMBER_A2, sender, "Le mando le foto della casa"))).status_code == 200
    item = _item(owner, sender, NUMBER_A2)
    assert item and item["unread"] == 0 and item["app_contact_name"] == "Lucia Rubrica Finta"
    conv = _conv(owner, sender, NUMBER_A2)
    assert conv["app_contact_name"] == "Lucia Rubrica Finta" and conv["contact"] is None
    assert ids  # agenzia A


# ---------------------------------------------------------------------------
# K2 - duplicati
# ---------------------------------------------------------------------------

def test_k2_resent_webhooks_never_duplicate(dc):
    owner, _ = _inbox2(dc, access="agency", automations=False)
    sender = _sender()
    now = datetime.now(timezone.utc)
    echo = _payload(_echo(NUMBER_A2, sender, "Una volta sola", mid="wamid.echo.dup1"))
    hist = _payload(_history(NUMBER_A2, [{"id": sender, "messages": [
        _hmsg(sender, "Storico uno", now - timedelta(days=3), mid="wamid.hist.dup1"),
        _hmsg("39000000002", "Storico due", now - timedelta(days=3, minutes=-1), mid="wamid.hist.dup2")]}]))
    book = _payload(_book(NUMBER_A2, ("Mario Finto", sender, "add", now)))
    for _ in range(3):
        for p in (echo, hist, book):
            assert dc["post"](p).status_code == 200
    assert _q(dc, "SELECT count(*) FROM whatsapp_inbound_messages WHERE sender_phone = %s", (sender,))[0][0] == 3
    assert _q(dc, "SELECT count(*) FROM whatsapp_app_contacts WHERE contact_phone = %s", (sender,))[0][0] == 1
    assert len(_conv(owner, sender, NUMBER_A2)["messages"]) == 3


def test_k2b_echo_of_a_crm_reply_is_not_duplicated(dc):
    ids = dc["ids"]
    owner, _ = _inbox2(dc, access="agency", automations=False)
    sender = _sender()
    _contact(dc, ids["a"], "Cliente Risposta", sender)
    _send(dc, sender, "Mi richiama?", NUMBER_A2)
    r = owner.post(f"{REPLIES}/{sender}?inbox={NUMBER_A2}",
                   json={"text": "La richiamo io.", "client_request_id": str(uuid.uuid4()), "confirm": True})
    assert r.status_code == 200, r.text
    wamid = _q(dc, "SELECT provider_message_id FROM communication_messages WHERE provider_message_id IS NOT NULL "
                   "ORDER BY id DESC LIMIT 1")[0][0]
    assert dc["post"](_payload(_echo(NUMBER_A2, sender, "La richiamo io.", mid=wamid))).status_code == 200
    assert _q(dc, "SELECT count(*) FROM whatsapp_inbound_messages WHERE provider_message_id = %s", (wamid,))[0][0] == 0
    assert dc["fake"].calls and dc["fake"].calls[-1][0] == NUMBER_A2      # la risposta parte dalla casella giusta


# ---------------------------------------------------------------------------
# K3 - regole del cliente
# ---------------------------------------------------------------------------

def test_k3_stop_automations_and_window_count_only_the_customer(dc, monkeypatch):
    from auto_replies import repository as auto_repo
    from core.database import core_cursor
    from inbound_messages import repository as inbound_repo

    ids = dc["ids"]
    owner, _ = _inbox2(dc, access="agency", automations=True)
    sender = _sender()
    contact = _contact(dc, ids["a"], "Cliente Stop", sender)
    now = datetime.now(timezone.utc)
    # il titolare scrive «STOP» dall'app: NON e' una revoca del cliente
    assert dc["post"](_payload(_echo(NUMBER_A2, sender, "STOP"))).status_code == 200
    assert dc["post"](_payload(_history(NUMBER_A2, [{"id": sender, "messages": [
        _hmsg("39000000002", "STOP", now - timedelta(days=2))]}]))).status_code == 200
    assert _q(dc, "SELECT count(*) FROM consent_events WHERE contact_id = %s AND source = 'whatsapp_optout'",
              (contact,))[0][0] == 0
    with core_cursor() as (_, cur):
        assert auto_repo.stop_in_conversation(cur, ids["a"], sender) is False
        # le automazioni non decidono mai su eco o storico
        pending = auto_repo.pending_message_ids(cur, ids["a"], now - timedelta(days=30), "coex-test", 100)
        mine = [r[0] for r in _q(dc, "SELECT id FROM whatsapp_inbound_messages WHERE sender_phone = %s", (sender,))]
        assert not set(pending) & set(mine)
        # la finestra di 24 ore: nessun messaggio del cliente = nessuna finestra
        assert inbound_repo.last_inbound(cur, ids["a"], sender, agent_user_id=None, phone_number_id=NUMBER_A2) is None
    # poi il cliente scrive: e' il solo messaggio che apre la finestra e che le automazioni vedono
    _send(dc, sender, "Ancora disponibile?", NUMBER_A2)
    with core_cursor() as (_, cur):
        last = inbound_repo.last_inbound(cur, ids["a"], sender, agent_user_id=None, phone_number_id=NUMBER_A2)
        assert last is not None
        pending = auto_repo.pending_message_ids(cur, ids["a"], now - timedelta(days=30), "coex-test", 100)
    live = _q(dc, "SELECT id FROM whatsapp_inbound_messages WHERE sender_phone = %s AND origin = 'webhook'", (sender,))
    assert [r[0] for r in live] == [last["id"]] and last["id"] in pending
    assert owner  # titolare


# ---------------------------------------------------------------------------
# K4 - permessi e isolamento
# ---------------------------------------------------------------------------

def test_k4_restricted_inbox_agents_other_agency_and_unrouted_numbers(dc):
    ids = dc["ids"]
    _inbox2(dc, access="restricted", automations=False)
    sender = _sender()
    _contact(dc, ids["a"], "Cliente Riservato", sender, agent=ids["agent_a"])
    assert dc["post"](_payload(_echo(NUMBER_A2, sender, "Dal telefono personale"))).status_code == 200
    owner, admin, agent, owner_b = (dc["login"](k) for k in ("owner_a", "admin_a", "agent_a", "owner_b"))
    assert _item(owner, sender, NUMBER_A2) is not None
    assert _item(admin, sender, NUMBER_A2) is None and _item(agent, sender, NUMBER_A2) is None
    assert admin.get(f"{API}/conversations/{sender}?inbox={NUMBER_A2}").status_code == 404
    assert owner_b.get(f"{API}/conversations/{sender}?inbox={NUMBER_A2}").status_code == 404
    # numero sconosciuto: salvato senza agenzia, invisibile a tutti; la sua rubrica non si conserva
    other = _sender()
    now = datetime.now(timezone.utc)
    assert dc["post"](_payload(_echo(NUMBER_UNKNOWN, other, "Sconosciuto"),
                               _book(NUMBER_UNKNOWN, ("Nome Finto", other, "add", now)))).status_code == 200
    assert _q(dc, "SELECT routing_status, agency_id FROM whatsapp_inbound_messages WHERE sender_phone = %s",
              (other,)) == [("unrouted", None)]
    assert _q(dc, "SELECT count(*) FROM whatsapp_app_contacts WHERE contact_phone = %s", (other,))[0][0] == 0
    for c in (owner, admin, owner_b):
        assert _item(c, other, NUMBER_UNKNOWN) is None


# ---------------------------------------------------------------------------
# K5 - rubrica dell'app
# ---------------------------------------------------------------------------

def test_k5_app_address_book_sync_and_import_with_phase_b_rules(dc):
    ids = dc["ids"]
    _inbox2(dc, access="restricted", automations=False)
    owner, admin = dc["login"]("owner_a"), dc["login"]("admin_a")
    now = datetime.now(timezone.utc)
    p1, p2, p3 = _sender(), _sender(), _sender()
    existing = _contact(dc, ids["a"], "Gia In Rubrica", p2)
    assert dc["post"](_payload(_book(NUMBER_A2, ("Anna Finta", p1, "add", now), ("Bruno Finto", p2, "add", now),
                                     ("Carla Finta", p3, "add", now)))).status_code == 200
    # una consegna IN RITARDO (piu' vecchia) non riporta indietro lo stato
    assert dc["post"](_payload(_book(NUMBER_A2, ("Carla Finta", p3, "remove", now + timedelta(minutes=1))))).status_code == 200
    assert dc["post"](_payload(_book(NUMBER_A2, ("Carla Vecchia", p3, "add", now - timedelta(days=1))))).status_code == 200
    assert _q(dc, "SELECT state, full_name FROM whatsapp_app_contacts WHERE contact_phone = %s", (p3,)) == \
        [("removed", "Carla Finta")]
    # la rubrica di una casella riservata la vede solo chi vede la casella
    present = _q(dc, "SELECT count(*) FROM whatsapp_app_contacts WHERE agency_id = %s AND phone_number_id = %s "
                     "AND state = 'present'", (ids["a"], NUMBER_A2))[0][0]
    src = owner.get(f"{IMPORTS}/whatsapp-sources").json()["items"]
    assert [(s["phone_number_id"], s["contacts"]) for s in src] == [(NUMBER_A2, present)]
    assert admin.get(f"{IMPORTS}/whatsapp-sources").json()["items"] == []
    assert admin.post(f"{IMPORTS}/whatsapp/{NUMBER_A2}/preview").status_code == 404
    # anteprima e import: nuovi riservati al titolare, l'esistente completato, nessun doppione
    prev = owner.post(f"{IMPORTS}/whatsapp/{NUMBER_A2}/preview")
    assert prev.status_code == 200, prev.text
    data = prev.json()
    assert data["summary"]["entries"] == present and data["summary"]["new"] >= 1 and data["summary"]["existing"] >= 1
    done = owner.post(f"{IMPORTS}/{data['import_id']}/commit", json={"decisions": {}})
    while not done.json().get("done", True):
        done = owner.post(f"{IMPORTS}/{data['import_id']}/commit", json={"decisions": {}})
    assert done.status_code == 200, done.text
    created = _q(dc, "SELECT id, private_user_id FROM contacts WHERE phone_normalized = %s", (p1,))
    assert len(created) == 1 and created[0][1] == ids["owner_a"]
    assert _q(dc, "SELECT count(*) FROM contacts WHERE phone_normalized = %s", (p2,))[0][0] == 1
    assert _q(dc, "SELECT source FROM contact_imports WHERE id = %s", (data["import_id"],))[0][0] == "whatsapp"
    assert existing
    # niente invii durante l'import
    assert dc["fake"].calls == []


# ---------------------------------------------------------------------------
# K6 - allegati
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, status, payload=None, content=b""):
        self.status_code, self._payload, self._content = status, payload, content

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload

    def iter_content(self, chunk_size=1):
        for i in range(0, len(self._content), chunk_size):
            yield self._content[i:i + chunk_size]

    def close(self):
        pass


def test_k6_attachments_only_for_who_sees_them_and_never_as_pages(dc, monkeypatch):
    import requests

    ids = dc["ids"]
    _inbox2(dc, access="agency", automations=False)
    sender = _sender()
    _contact(dc, ids["a"], "Cliente Foto", sender, agent=ids["agent_a"])
    other = _sender()
    _contact(dc, ids["a"], "Cliente Altro Agente", other)
    pic = _message(sender, kind="image", image={"mime_type": "image/jpeg", "id": "media.foto.1", "caption": "Facciata"})
    page = _message(sender, kind="document", document={"mime_type": "text/html", "id": "media.pagina.1",
                                                        "filename": "<script>.html"})
    hidden = _message(other, kind="image", image={"mime_type": "image/png", "id": "media.altro.1"})
    assert dc["post"](_payload(_change(NUMBER_A2, [pic, page]), _change(NUMBER_A2, [hidden]))).status_code == 200
    ids_by = dict(_q(dc, "SELECT metadata->>'media_id', id FROM whatsapp_inbound_messages "
                         "WHERE metadata ? 'media_id' AND sender_phone IN (%s, %s)", (sender, other)))
    calls = []
    files = {"media.foto.1": ("image/jpeg", b"\xff\xd8finto-jpeg"), "media.pagina.1": ("text/html", b"<script>x</script>"),
             "media.altro.1": ("image/png", b"png")}

    def fake_get(url, headers=None, timeout=None, stream=False):
        calls.append(url)
        assert headers and headers["Authorization"].startswith("Bearer ")
        for mid, (mime, content) in files.items():
            if url == f"https://lookaside.example.test/{mid}":
                return _Resp(200, content=content)
        for mid, (mime, content) in files.items():
            if url.endswith("/" + mid):
                return _Resp(200, {"url": f"https://lookaside.example.test/{mid}", "mime_type": mime,
                                   "file_size": len(content)})
        return _Resp(404, {"error": {"code": 100}})

    monkeypatch.setattr(requests, "get", fake_get)
    owner, agent = dc["login"]("owner_a"), dc["login"]("agent_a")
    conv = _conv(owner, sender, NUMBER_A2)
    assert all("media_id" not in m["metadata"] for m in conv["messages"])          # l'id resta sul server
    assert {m["has_media"] for m in conv["messages"]} == {True}
    r = agent.get(f"{MEDIA}/{ids_by['media.foto.1']}/media")
    assert r.status_code == 200 and r.content == b"\xff\xd8finto-jpeg"
    assert r.headers["content-type"].startswith("image/jpeg") and r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-disposition"].startswith("inline") and "no-store" in r.headers["cache-control"]
    r = owner.get(f"{MEDIA}/{ids_by['media.pagina.1']}/media")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/octet-stream")
    assert r.headers["content-disposition"].startswith("attachment") and "<" not in r.headers["content-disposition"]
    # l'agente non vede la chat dell'altro contatto: nessuna chiamata a Meta
    before = len(calls)
    assert agent.get(f"{MEDIA}/{ids_by['media.altro.1']}/media").status_code == 404
    assert len(calls) == before
    assert dc["login"]("owner_b").get(f"{MEDIA}/{ids_by['media.foto.1']}/media").status_code == 404
    # troppo grande, sparito, configurazione assente
    monkeypatch.setenv("WHATSAPP_MEDIA_MAX_MB", "1")
    files["media.foto.1"] = ("image/jpeg", b"x" * (2 * 1024 * 1024))
    assert owner.get(f"{MEDIA}/{ids_by['media.foto.1']}/media").status_code == 413
    files.pop("media.foto.1")
    assert owner.get(f"{MEDIA}/{ids_by['media.foto.1']}/media").status_code == 410
    monkeypatch.delenv("WHATSAPP_GRAPH_VERSION")
    assert owner.get(f"{MEDIA}/{ids_by['media.pagina.1']}/media").status_code == 503


# ---------------------------------------------------------------------------
# K7 - collegamento con la procedura Meta (Meta simulato)
# ---------------------------------------------------------------------------

class FakeGraph:
    def __init__(self, *, platform="CLOUD_API", numbers=(NUMBER_A2,), code_ok=True, biz_app=True):
        self.platform, self.numbers, self.code_ok, self.biz_app = platform, list(numbers), code_ok, biz_app
        self.calls = []

    def exchange_code(self, app_id, app_secret, code):
        self.calls.append(("exchange", app_id, code))
        return (True, {"access_token": "TOKEN-DELLA-PROCEDURA-FINTO"}, None) if self.code_ok else (False, None, "meta_100")

    def waba_phone_numbers(self, token, waba_id):
        self.calls.append(("numbers", token, waba_id))
        return True, {"data": [{"id": n, "display_phone_number": "+39 000 000 0002"} for n in self.numbers]}, None

    def phone_number(self, token, pnid):
        self.calls.append(("phone", token, pnid))
        return True, {"id": pnid, "display_phone_number": "+39 000 000 0002", "verified_name": "Agenzia Finta",
                      "platform_type": self.platform, "status": "CONNECTED", "is_on_biz_app": self.biz_app}, None

    def subscribe_app(self, token, waba_id, callback_url=None, verify_token=None):
        self.calls.append(("subscribe", token, waba_id, callback_url))
        return True, {"success": True}, None

    def request_sync(self, token, pnid, sync_type):
        self.calls.append(("sync", token, pnid, sync_type))
        return True, {"request_id": "x"}, None


@pytest.fixture
def meta(dc, monkeypatch):
    from whatsapp_coexistence import service

    monkeypatch.setenv("META_APP_ID", "1234567890")
    monkeypatch.setenv("META_COEXISTENCE_CONFIG_ID", "9876543210")
    holder = {"graph": FakeGraph()}
    monkeypatch.setattr(service, "make_graph", lambda version: holder["graph"])
    # il registro non si cancella (guardia della 132): ogni prova conta dal proprio punto di partenza
    base = _q(dc, "SELECT COALESCE(max(id), 0) FROM whatsapp_coexistence_onboardings")[0][0]
    return {**dc, "meta": holder, "base": base}


def test_k7_only_the_owner_connects_and_meta_confirms(meta, monkeypatch):
    owner, admin, agent, owner_b = (meta["login"](k) for k in ("owner_a", "admin_a", "agent_a", "owner_b"))
    cfg = owner.get(f"{COEX}/config").json()
    assert cfg["enabled"] and cfg["app_id"] == "1234567890" and cfg["can_connect"] is True
    assert "secret" not in json.dumps(cfg).lower() or cfg["missing"] == []
    assert admin.get(f"{COEX}/config").status_code == 403 and agent.get(f"{COEX}/config").status_code == 403
    body = {"code": "codice-monouso-finto", "waba_id": WABA, "phone_number_id": NUMBER_A2}
    assert admin.post(f"{COEX}/onboardings", json=body).status_code == 403
    assert agent.post(f"{COEX}/onboardings", json=body).status_code == 403
    assert owner.post(f"{COEX}/onboardings", json={**body, "token": "x"}).status_code == 422     # corpo chiuso
    r = owner.post(f"{COEX}/onboardings", json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["connected"] is True and out["status"] == "meta_confirmed" and out["meta"]["is_on_biz_app"] is True
    assert all(out["steps"][k]["ok"] for k in ("code_exchange", "subscribe_webhooks", "sync_contacts",
                                               "sync_history", "meta_check"))
    kinds = [c[0] for c in meta["meta"]["graph"].calls]
    assert kinds[:2] == ["exchange", "numbers"] and kinds.count("sync") == 2
    # nessun token della procedura salvato da nessuna parte
    dump = json.dumps(_q(meta, "SELECT row_to_json(o) FROM whatsapp_coexistence_onboardings o"), default=str)
    assert "TOKEN-DELLA-PROCEDURA-FINTO" not in dump and "codice-monouso-finto" not in dump
    # la casella del numero personale nasce riservata e senza automazioni
    assert _q(meta, "SELECT kind, access_mode, automations_enabled FROM whatsapp_inboxes WHERE phone_number_id = %s",
              (NUMBER_A2,)) == [("coexistence", "restricted", False)]
    # l'altra agenzia non vede nulla; la prova di ricezione arriva solo con i webhook
    assert owner_b.get(f"{COEX}/onboardings").json()["items"] == []
    before = owner.get(f"{COEX}/onboardings").json()["items"][0]["evidence"]["app_echoes"]
    assert meta["post"](_payload(_echo(NUMBER_A2, _sender(), "prova"))).status_code == 200
    assert owner.get(f"{COEX}/onboardings").json()["items"][0]["evidence"]["app_echoes"] == before + 1


def test_k7c_webhook_address_moves_only_for_a_waba_with_just_the_personal_number(meta, monkeypatch):
    """Il cambio d'indirizzo dei webhook vale per TUTTO il WABA: mai se ci sono altri numeri (il numero API)."""
    owner = meta["login"]("owner_a")
    monkeypatch.setenv("WHATSAPP_COEXISTENCE_CALLBACK_URL", "https://crm-test.example.test/webhook/whatsapp")
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "verifica-finta")
    body = {"code": "codice-finto", "waba_id": WABA, "phone_number_id": NUMBER_A2}
    meta["meta"]["graph"] = FakeGraph(numbers=(NUMBER_A, NUMBER_A2))
    out = owner.post(f"{COEX}/onboardings", json=body).json()
    sub = [c for c in meta["meta"]["graph"].calls if c[0] == "subscribe"]
    assert sub and sub[0][3] is None                                  # nessun cambio d'indirizzo
    assert out["steps"]["callback_override"] == {"ok": False, "error": "shared_waba_override_skipped"}
    meta["meta"]["graph"] = FakeGraph(numbers=(NUMBER_A2,))
    owner.post(f"{COEX}/onboardings", json=body)
    sub = [c for c in meta["meta"]["graph"].calls if c[0] == "subscribe"]
    assert sub[0][3] == "https://crm-test.example.test/webhook/whatsapp"


def test_k7b_never_connected_without_meta_and_failures_are_explicit(meta, monkeypatch):
    owner = meta["login"]("owner_a")
    body = {"code": "codice-finto", "waba_id": WABA, "phone_number_id": NUMBER_A2}
    meta["meta"]["graph"] = FakeGraph(platform="ON_PREMISE")
    out = owner.post(f"{COEX}/onboardings", json=body).json()
    assert out["connected"] is False and out["status"] == "requested" and out["steps"]["meta_check"]["ok"] is False
    # codice rifiutato: nessuna riga, messaggio chiaro
    meta["meta"]["graph"] = FakeGraph(code_ok=False)
    r = owner.post(f"{COEX}/onboardings", json=body)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "CODE_EXCHANGE_FAILED"
    # numero non nel WABA
    meta["meta"]["graph"] = FakeGraph(numbers=("900000000000099",))
    r = owner.post(f"{COEX}/onboardings", json=body)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "PHONE_NOT_IN_WABA"
    mine = _q(meta, "SELECT id FROM whatsapp_coexistence_onboardings WHERE id > %s ORDER BY id", (meta["base"],))
    assert len(mine) == 1                                   # solo il tentativo arrivato fino a Meta
    # verifica successiva: senza token di sistema del numero e' 503, con il token Meta conferma
    first = mine[0][0]
    monkeypatch.delenv("WHATSAPP_TOKEN")
    assert owner.post(f"{COEX}/onboardings/{first}/verify").status_code == 503
    monkeypatch.setenv(f"WHATSAPP_TOKEN__{NUMBER_A2}", "token-di-sistema-finto")
    meta["meta"]["graph"] = FakeGraph()
    v = owner.post(f"{COEX}/onboardings/{first}/verify").json()
    assert v["connected"] is True and v["meta_confirmed_at"]
    # numero non ancora instradato: il collegamento si registra, ma rubrica e storico NON si chiedono
    # (arriverebbero senza agenzia); si chiedono dal pannello dopo l'instradamento
    ids = meta["ids"]
    monkeypatch.setenv("WHATSAPP_INBOUND_ROUTES", f"{NUMBER_A}:{ids['a']}, {NUMBER_B}:{ids['b']}")
    meta["meta"]["graph"] = FakeGraph()
    out = owner.post(f"{COEX}/onboardings", json=body).json()
    assert out["routed"] is False and out["steps"]["sync_history"] == {"ok": False, "error": "not_routed_yet"}
    assert [c for c in meta["meta"]["graph"].calls if c[0] == "sync"] == []
    r = owner.post(f"{COEX}/onboardings/{out['id']}/sync", json={"kind": "history"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "NOT_ROUTED"
    monkeypatch.setenv("WHATSAPP_INBOUND_ROUTES", f"{NUMBER_A}:{ids['a']}, {NUMBER_A2}:{ids['a']}, {NUMBER_B}:{ids['b']}")
    again = owner.post(f"{COEX}/onboardings/{out['id']}/sync", json={"kind": "history"})
    assert again.status_code == 200 and again.json()["history_sync_requested_at"]
    assert [c[3] for c in meta["meta"]["graph"].calls if c[0] == "sync"] == ["history"]
    # configurazione mancante: la procedura non parte e si dicono i NOMI mancanti
    monkeypatch.delenv("META_COEXISTENCE_CONFIG_ID")
    r = owner.post(f"{COEX}/onboardings", json=body)
    assert r.status_code == 503 and "META_COEXISTENCE_CONFIG_ID" in r.json()["detail"]["missing"]


# ---------------------------------------------------------------------------
# K8 - migrazione 132
# ---------------------------------------------------------------------------

def test_k8_down_migration_refuses_when_coexistence_data_exists(dc):
    from pathlib import Path

    _inbox2(dc, access="agency", automations=False)
    assert dc["post"](_payload(_echo(NUMBER_A2, _sender(), "dato da proteggere"))).status_code == 200
    down = (Path(__file__).resolve().parents[1] / "migrations" / "132_whatsapp_coexistence_down.sql").read_text()
    conn = psycopg2.connect(dc["dsn"])
    try:
        with pytest.raises(psycopg2.Error) as err:
            with conn.cursor() as cur:
                cur.execute(down)
        assert "132 down refused" in str(err.value)
    finally:
        conn.rollback()
        conn.close()
    assert _q(dc, "SELECT count(*) FROM whatsapp_inbound_messages WHERE origin = 'app_echo'")[0][0] >= 1


def test_k8b_messages_stay_immutable_including_direction_and_origin(dc):
    _inbox2(dc, access="agency", automations=False)
    sender = _sender()
    assert dc["post"](_payload(_echo(NUMBER_A2, sender, "immutabile"))).status_code == 200
    with pytest.raises(Exception):
        _q(dc, "UPDATE whatsapp_inbound_messages SET direction = 'in', origin = 'webhook' WHERE sender_phone = %s",
           (sender,))
