"""BLOCCO-4A - ricezione WhatsApp nel CRM su PostgreSQL VERO.

Stesso banco del collaudo BLOCCO-2C (schema completo ricostruito come il TEST
su un cluster LOCALE usa-e-getta, migrazioni 115 -> 118 dal runner), app vera
(`main.app`), login vero e webhook firmato con un segreto sintetico. Gate:
`P29_TEST_DSN` locale e `BLOCCO_2C_TEST_CLUSTER_VERIFIED=1`.

Nessuna chiamata esterna: ogni funzione d'invio e `requests.post` sono
sostituite da trappole che fanno fallire il test se chiamate.

    W  firma errata, payload malformato, verifica GET con i nomi di Meta
    M  piu' messaggi e piu' entry in un payload; tipi non testuali senza testo finto
    S  stati di consegna e altri eventi separati dai messaggi
    D  reinvio e ricezioni concorrenti senza duplicati
    R  errore di persistenza: rollback completo, 500, poi reinvio riuscito
    U  numero destinatario sconosciuto o ambiguo: nessuna agenzia, nessuna esposizione
    A  due agenzie e stesso mittente; contatti duplicati -> associazione manuale
    L  associazione manuale: permessi, altra agenzia, sola lettura, registro, report invariato
"""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid

import pytest

from tests.test_blocco_2c_integrated_postgres import (  # noqa: F401 - fixture
    CLUSTER_VERIFIED,
    DSN,
    _Holding,
    _q,
    _run,
    _waiting_on_lock,
    app,
    banco,
)

pytestmark = [
    pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL locale"),
    pytest.mark.skipif(not CLUSTER_VERIFIED, reason="cluster usa-e-getta non dichiarato verificato "
                       "(BLOCCO_2C_TEST_CLUSTER_VERIFIED=1)"),
]

SECRET = "segreto-sintetico-di-prova"
NUMBER_A, NUMBER_B, NUMBER_UNKNOWN, NUMBER_SHARED = "900000000000001", "900000000000002", "900000000000009", "900000000000005"
API = "/api/inbound-messages"


def _sign(raw: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()


def _message(sender, *, kind="text", body="Ciao", name=None, mid=None, ts="1761800000", **extra):
    msg = {"from": sender, "id": mid or f"wamid.{uuid.uuid4().hex}", "timestamp": ts, "type": kind}
    if kind == "text":
        msg["text"] = {"body": body}
    msg.update(extra)
    return msg


def _change(number, messages=(), statuses=(), contacts=(), field="messages", errors=None):
    value = {"messaging_product": "whatsapp",
             "metadata": {"display_phone_number": "39000000000", "phone_number_id": number}}
    if messages:
        value["messages"] = list(messages)
    if statuses:
        value["statuses"] = list(statuses)
    if contacts:
        value["contacts"] = list(contacts)
    if errors:
        value["errors"] = errors
    return {"field": field, "value": value}


def _payload(*changes, entries=None):
    return {"object": "whatsapp_business_account",
            "entry": entries or [{"id": "waba-prova", "changes": list(changes)}]}


@pytest.fixture
def wa(app, monkeypatch):
    import requests

    import main

    ids = app["ids"]
    monkeypatch.setenv("WHATSAPP_APP_SECRET", SECRET)
    monkeypatch.setenv("WHATSAPP_INBOUND_ROUTES",
                       f"{NUMBER_A}:{ids['a']}, {NUMBER_B}:{ids['b']}, {NUMBER_SHARED}:{ids['a']}, {NUMBER_SHARED}:{ids['b']}")
    sent = []

    def trap(*args, **kwargs):
        sent.append(args)
        raise AssertionError("invio esterno durante la ricezione WhatsApp")

    for name in ("invia_whatsapp", "invia_whatsapp_text"):
        monkeypatch.setattr(main, name, trap)
    monkeypatch.setattr(requests, "post", trap)
    client = app["login"]("owner_a")

    def post(payload, *, raw=None, signature=None):
        body = raw if raw is not None else json.dumps(payload).encode()
        return client.post("/webhook/whatsapp", content=body,
                           headers={"Content-Type": "application/json",
                                    "X-Hub-Signature-256": signature or _sign(body)})

    yield {**app, "post": post, "sent": sent}
    assert sent == [], "nessun invio esterno doveva partire"


def _rows(b, where="TRUE", params=()):
    return _q(b, f"SELECT provider_message_id, agency_id, routing_status, routing_reason, message_type, text_body, "
                 f"contact_id, contact_match, metadata FROM whatsapp_inbound_messages WHERE {where} ORDER BY id", params)


def _contact(b, agency_id, name, phone, agent=None):
    return _q(b, "INSERT INTO contacts (agency_id, display_name, first_name, last_name, phone, phone_normalized, "
                 "assigned_agent_id) VALUES (%s, %s, %s, 'Prova', %s, %s, %s) RETURNING id",
              (agency_id, name, name, phone, phone, agent))[0][0]


def _sender():
    return "39" + str(uuid.uuid4().int)[:10]


# ---------------------------------------------------------------------------
# W - firma, payload, verifica GET
# ---------------------------------------------------------------------------

def test_w_bad_signature_and_tampered_body_store_nothing(wa):
    payload = _payload(_change(NUMBER_A, [_message(_sender(), mid="wamid.firma")]))
    raw = json.dumps(payload).encode()
    assert wa["post"](None, raw=raw, signature="sha256=" + "0" * 64).status_code == 403
    assert wa["post"](None, raw=raw.replace(b"Ciao", b"Alterato"), signature=_sign(raw)).status_code == 403
    assert _rows(wa, "provider_message_id = %s", ("wamid.firma",)) == []


def test_w_malformed_payload_is_400_after_a_valid_signature(wa):
    for raw in (b"non json", b'{"entry": "x"}', b'{"entry": [{"changes": "x"}]}'):
        assert wa["post"](None, raw=raw).status_code == 400


def test_w_get_verification_accepts_meta_parameter_names(wa, monkeypatch):
    from fastapi.testclient import TestClient

    import main

    c = TestClient(main.app)
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "token-di-prova")
    ok = c.get("/webhook/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "token-di-prova",
                                            "hub.challenge": "4242"})
    assert ok.status_code == 200 and ok.json() == 4242
    assert c.get("/webhook/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "altro",
                                              "hub.challenge": "1"}).status_code == 403
    monkeypatch.delenv("WHATSAPP_VERIFY_TOKEN")
    assert c.get("/webhook/whatsapp", params={"hub.mode": "subscribe", "hub.challenge": "1"}).status_code == 403


# ---------------------------------------------------------------------------
# M + S - piu' messaggi, tipi, stati, altri eventi
# ---------------------------------------------------------------------------

def test_m_every_message_of_every_entry_is_stored_and_types_stay_types(wa):
    s1, s2 = _sender(), _sender()
    image = _message(s1, kind="image", image={"mime_type": "image/jpeg", "caption": "Foto della cucina",
                                              "id": "media-id", "link": "https://esempio.example/x"})
    payload = _payload(entries=[
        {"id": "waba", "changes": [_change(NUMBER_A, [_message(s1, body="Primo"), image],
                                           contacts=[{"wa_id": s1, "profile": {"name": "Mittente Uno"}}])]},
        {"id": "waba", "changes": [_change(NUMBER_A, [_message(s2, body="Secondo"),
                                                      _message(s2, kind="sticker", sticker={"mime_type": "image/webp"}),
                                                      _message(s2, kind="nuovo_tipo")])]},
    ])
    r = wa["post"](payload)
    assert r.status_code == 200 and r.json() == {"ok": True}
    rows = _rows(wa, "sender_phone IN (%s, %s)", (s1, s2))
    assert [x[4] for x in rows] == ["text", "image", "text", "sticker", "nuovo_tipo"]
    assert [x[5] for x in rows] == ["Primo", None, "Secondo", None, None]          # nessun testo finto
    assert rows[1][8] == {"mime_type": "image/jpeg", "caption": "Foto della cucina"}  # niente id media, niente URL
    assert all(x[1] == wa["ids"]["a"] and x[2] == "routed" for x in rows)
    names = _q(wa, "SELECT DISTINCT sender_profile_name FROM whatsapp_inbound_messages WHERE sender_phone = %s", (s1,))
    assert names == [("Mittente Uno",)]
    times = _q(wa, "SELECT provider_timestamp IS NOT NULL, received_at IS NOT NULL FROM whatsapp_inbound_messages "
                   "WHERE sender_phone = %s", (s1,))
    assert set(times) == {(True, True)}


def test_s_status_updates_and_other_events_are_not_messages(wa):
    wamid = f"wamid.out.{uuid.uuid4().hex}"
    statuses = [{"id": wamid, "status": "sent", "timestamp": "1761800001", "recipient_id": "393330000000"},
                {"id": wamid, "status": "delivered", "timestamp": "1761800002", "recipient_id": "393330000000"},
                {"id": wamid, "status": "failed", "timestamp": "1761800003", "recipient_id": "393330000000",
                 "errors": [{"code": 131047, "title": "Re-engagement message"}]}]
    payload = _payload(_change(NUMBER_A, statuses=statuses),
                       {"field": "account_update", "value": {"event": "VERIFIED_ACCOUNT"}})
    before = _q(wa, "SELECT count(*) FROM whatsapp_inbound_messages")[0][0]
    assert wa["post"](payload).status_code == 200
    assert _q(wa, "SELECT count(*) FROM whatsapp_inbound_messages")[0][0] == before
    events = _q(wa, "SELECT event_kind, status, agency_id, details FROM whatsapp_webhook_events "
                    "WHERE provider_message_id = %s ORDER BY id", (wamid,))
    assert [(e[0], e[1], e[2]) for e in events] == [("status", s, wa["ids"]["a"]) for s in ("sent", "delivered", "failed")]
    assert events[2][3] == {"errors": [{"code": 131047, "title": "Re-engagement message"}]}
    other = _q(wa, "SELECT event_kind, routing_status, details->>'field' FROM whatsapp_webhook_events "
                   "WHERE details->>'field' = 'account_update'")
    assert other == [("other", "unrouted", "account_update")]
    assert wa["post"](payload).status_code == 200            # reinvio: nessun evento in piu'
    assert _q(wa, "SELECT count(*) FROM whatsapp_webhook_events WHERE provider_message_id = %s", (wamid,))[0][0] == 3


# ---------------------------------------------------------------------------
# D - reinvio e concorrenza
# ---------------------------------------------------------------------------

def test_d_a_resent_webhook_duplicates_nothing(wa):
    sender = _sender()
    payload = _payload(_change(NUMBER_A, [_message(sender, mid="wamid.reinvio.1"), _message(sender, mid="wamid.reinvio.2")]))
    for _ in range(3):
        assert wa["post"](payload).status_code == 200
    assert len(_rows(wa, "sender_phone = %s", (sender,))) == 2
    assert _q(wa, "SELECT count(*) FROM activities WHERE description LIKE %s", ("%reinvio%",))[0][0] == 0


def test_d_a_concurrent_delivery_of_the_same_message_keeps_one_row(wa):
    sender = _sender()
    wamid = f"wamid.gara.{uuid.uuid4().hex}"
    payload = _payload(_change(NUMBER_A, [_message(sender, mid=wamid)]))
    # Un'altra consegna dello stesso messaggio e' a meta' transazione: la riga e' scritta, non ancora confermata.
    with _Holding(wa, "INSERT INTO whatsapp_inbound_messages (provider_message_id, phone_number_id, agency_id, "
                      "routing_status, sender_phone, message_type, text_body) VALUES (%s, %s, %s, 'routed', %s, "
                      "'text', 'Ciao')", (wamid, NUMBER_A, wa["ids"]["a"], sender)):
        t, out = _run(lambda: wa["post"](payload))
        assert _waiting_on_lock(wa, 1), "la seconda consegna non ha atteso la prima sul vincolo unico"
    t.join(30)
    assert out["value"].status_code == 200
    assert len(_rows(wa, "provider_message_id = %s", (wamid,))) == 1


# ---------------------------------------------------------------------------
# R - errore di persistenza
# ---------------------------------------------------------------------------

def test_r_a_write_error_rolls_back_everything_and_meta_can_retry(wa, monkeypatch):
    from inbound_messages import repository

    sender = _sender()
    payload = _payload(_change(NUMBER_A, [_message(sender, mid="wamid.rollback.1"), _message(sender, mid="wamid.rollback.2")],
                               statuses=[{"id": "wamid.out.rb", "status": "read", "timestamp": "1761800009"}]))
    original = repository.insert_event

    def broken(*args, **kwargs):
        raise RuntimeError("disco pieno (simulato)")

    monkeypatch.setattr(repository, "insert_event", broken)
    r = wa["post"](payload)
    assert r.status_code == 500 and "disco" not in r.text
    assert _rows(wa, "sender_phone = %s", (sender,)) == []            # anche i messaggi gia' scritti sono annullati
    monkeypatch.setattr(repository, "insert_event", original)
    assert wa["post"](payload).status_code == 200
    assert len(_rows(wa, "sender_phone = %s", (sender,))) == 2


# ---------------------------------------------------------------------------
# U - numero sconosciuto o ambiguo
# ---------------------------------------------------------------------------

def test_u_unknown_or_ambiguous_recipient_is_never_attributed_nor_shown(wa):
    s_unknown, s_shared = _sender(), _sender()
    payload = _payload(_change(NUMBER_UNKNOWN, [_message(s_unknown, body="Numero sconosciuto")]),
                       _change(NUMBER_SHARED, [_message(s_shared, body="Numero conteso")]))
    assert wa["post"](payload).status_code == 200
    assert [(x[1], x[2], x[3]) for x in _rows(wa, "sender_phone = %s", (s_unknown,))] == [(None, "unrouted", "unknown_number")]
    assert [(x[1], x[2], x[3]) for x in _rows(wa, "sender_phone = %s", (s_shared,))] == [(None, "unrouted", "ambiguous_number")]
    for who in ("owner_a", "owner_b"):
        c = wa["login"](who)
        text = c.get(f"{API}/conversations").text
        assert s_unknown not in text and s_shared not in text
        assert c.get(f"{API}/conversations/{s_unknown}").status_code == 404


# ---------------------------------------------------------------------------
# A - due agenzie, stesso mittente; contatti duplicati
# ---------------------------------------------------------------------------

def test_a_same_sender_to_two_agencies_stays_separated(wa):
    ids = wa["ids"]
    sender = _sender()
    contact_a = _contact(wa, ids["a"], "Mario Alfa", sender)
    payload = _payload(_change(NUMBER_A, [_message(sender, body="Per Alfa")]),
                       _change(NUMBER_B, [_message(sender, body="Per Beta")]))
    assert wa["post"](payload).status_code == 200
    rows = {x[1]: x for x in _rows(wa, "sender_phone = %s", (sender,))}
    assert rows[ids["a"]][6] == contact_a and rows[ids["a"]][7] == "matched"
    assert rows[ids["b"]][6] is None and rows[ids["b"]][7] == "none"
    a = wa["login"]("owner_a").get(f"{API}/conversations/{sender}").json()
    b = wa["login"]("owner_b").get(f"{API}/conversations/{sender}").json()
    assert [m["text"] for m in a["messages"]] == ["Per Alfa"] and [m["text"] for m in b["messages"]] == ["Per Beta"]
    assert a["messages"][0]["contact"]["id"] == contact_a and b["messages"][0]["contact"] is None


def test_a_duplicate_contacts_require_manual_association(wa):
    ids = wa["ids"]
    sender = _sender()
    c1, c2 = _contact(wa, ids["a"], "Doppio Uno", sender), _contact(wa, ids["a"], "Doppio Due", sender)
    assert wa["post"](_payload(_change(NUMBER_A, [_message(sender)]))).status_code == 200
    row = _rows(wa, "sender_phone = %s", (sender,))[0]
    assert row[6] is None and row[7] == "ambiguous"
    conv = wa["login"]("owner_a").get(f"{API}/conversations/{sender}").json()
    assert {c["id"] for c in conv["contact_candidates"]} == {c1, c2}
    unlinked = wa["login"]("owner_a").get(f"{API}/conversations", params={"only_unlinked": "true"}).json()
    assert any(c["sender_phone"] == sender for c in unlinked["items"])


# ---------------------------------------------------------------------------
# L - associazione manuale
# ---------------------------------------------------------------------------

def _property(c, title):
    r = c.post("/api/property/properties", json={"title": title, "city": "Comune Sintetico"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_l_manual_association_permissions_audit_and_report(wa):
    ids = wa["ids"]
    owner, admin, agent, owner_b = (wa["login"](k) for k in ("owner_a", "admin_a", "agent_a", "owner_b"))
    sender = _sender()
    assert wa["post"](_payload(_change(NUMBER_A, [_message(sender, body="Vorrei vedere la casa")]))).status_code == 200
    msg_id = _q(wa, "SELECT id FROM whatsapp_inbound_messages WHERE sender_phone = %s", (sender,))[0][0]
    contact = _contact(wa, ids["a"], "Giulio Associato", "393000000000", agent=ids["agent_a"])
    contact_b = _contact(wa, ids["b"], "Altra Agenzia", "393000000001")
    pid = _property(owner, "Immobile messaggi 4A")
    pid_b = _property(owner_b, "Immobile altra agenzia 4A")
    report_before = owner.get(f"/api/owner-reports/{pid}").json()

    # l'agente non vede il messaggio non associato e non puo' associare
    assert agent.get(f"{API}/{msg_id}").status_code == 404
    assert agent.post(f"{API}/{msg_id}/link", json={"contact_id": contact}).status_code == 403
    # l'altra agenzia non lo raggiunge; riferimenti di un'altra agenzia rifiutati
    assert owner_b.post(f"{API}/{msg_id}/link", json={"contact_id": contact_b}).status_code == 404
    assert owner.post(f"{API}/{msg_id}/link", json={"contact_id": contact_b}).json()["detail"]["code"] == "CONTACT_NOT_FOUND"
    assert owner.post(f"{API}/{msg_id}/link", json={"property_id": pid_b}).json()["detail"]["code"] == "PROPERTY_NOT_FOUND"
    assert owner.post(f"{API}/{msg_id}/link", json={"contact_id": contact, "agency_id": ids["b"]}).status_code == 422

    r = admin.post(f"{API}/{msg_id}/link", json={"contact_id": contact, "property_id": pid})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["changed"] is True and body["contact"]["id"] == contact and body["property"]["id"] == pid
    assert body["contact_match"] == "manual" and body["linked_by_user_id"] == ids["admin_a"]
    assert owner.post(f"{API}/{msg_id}/link", json={"contact_id": contact, "property_id": pid}).json()["changed"] is False
    history = _q(wa, "SELECT previous_contact_id, contact_id, property_id, linked_by_user_id FROM "
                     "whatsapp_inbound_message_links WHERE message_id = %s", (msg_id,))
    assert history == [(None, contact, pid, ids["admin_a"])]
    # ora l'agente, a cui il contatto e' assegnato, vede il messaggio (senza poter associare)
    seen = agent.get(f"{API}/{msg_id}")
    assert seen.status_code == 200 and seen.json()["can_link"] is False
    # l'associazione non crea richieste, attivita' o contatti, e non cambia il report proprietario
    report_after = owner.get(f"/api/owner-reports/{pid}").json()
    assert report_after["requests"] == {"available": False, "total": None}
    assert report_after["interested_contacts"] == report_before["interested_contacts"]
    assert _q(wa, "SELECT count(*) FROM activities WHERE contact_id = %s", (contact,))[0][0] == 0


def test_l_read_only_consultation_can_read_but_not_associate(wa):
    ids = wa["ids"]
    sender = _sender()
    assert wa["post"](_payload(_change(NUMBER_A, [_message(sender)]))).status_code == 200
    msg_id = _q(wa, "SELECT id FROM whatsapp_inbound_messages WHERE sender_phone = %s", (sender,))[0][0]
    sa = wa["login"]("super")
    try:
        assert sa.patch(f"/api/platform/agencies/{ids['a']}", json={"status": "suspended"}).status_code == 200
        assert sa.post(f"/api/platform/agencies/{ids['a']}/enter").json()["read_only"] is True
        assert sa.get(f"{API}/{msg_id}").status_code == 200
        r = sa.post(f"{API}/{msg_id}/link", json={"contact_id": None})
        assert r.status_code == 403 and "sola lettura" in r.text.lower()
        assert _q(wa, "SELECT count(*) FROM whatsapp_inbound_message_links WHERE message_id = %s", (msg_id,))[0][0] == 0
        assert sa.post("/api/platform/agency-context/exit").status_code == 204
    finally:
        sa.patch(f"/api/platform/agencies/{ids['a']}", json={"status": "active"})
        _q(wa, "UPDATE agencies SET status = 'active' WHERE id = %s", (ids["a"],))


def test_l_database_refuses_cross_agency_links_and_deletes(wa):
    import psycopg2

    ids = wa["ids"]
    sender = _sender()
    assert wa["post"](_payload(_change(NUMBER_A, [_message(sender)]))).status_code == 200
    msg_id = _q(wa, "SELECT id FROM whatsapp_inbound_messages WHERE sender_phone = %s", (sender,))[0][0]
    contact_b = _contact(wa, ids["b"], "Fuori Agenzia", "393000000002")
    with pytest.raises(psycopg2.Error):
        _q(wa, "UPDATE whatsapp_inbound_messages SET contact_id = %s, contact_match = 'manual' WHERE id = %s",
           (contact_b, msg_id))
    with pytest.raises(psycopg2.Error):
        _q(wa, "UPDATE whatsapp_inbound_messages SET text_body = 'riscritto' WHERE id = %s", (msg_id,))
    with pytest.raises(psycopg2.Error):
        _q(wa, "DELETE FROM whatsapp_inbound_messages WHERE id = %s", (msg_id,))
