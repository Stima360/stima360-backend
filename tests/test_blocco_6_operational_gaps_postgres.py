"""BLOCCO-6 - lacune operative, su PostgreSQL VERO (migrazione 124).

    N  divieto totale di contatto: distinto dal marketing, registrato e revocato
       dall'operatore con una nota, separato fra agenzie, rispettato da ogni
       percorso d'invio (dispatch, risposta manuale 4B, motore delle risposte)
    Q  risposte in coda con la finestra chiusa: chiuse come non inviate, mai
       inviate dopo; `sending` e `indeterminate` intatte
    V  revoca dell'autorizzazione del proprietario alle visite: motivo, nota,
       autore e ora registrati; i collegamenti dedicati chiusi con il padre;
       le visite «richieste» restanti contate, non cambiate
"""
from __future__ import annotations

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
from tests.test_blocco_4a_whatsapp_inbound_postgres import wa  # noqa: F401 - fixture
from tests.test_blocco_4c_reply_suggestions_postgres import _property as _plain_property
from tests.test_blocco_4d_auto_replies_postgres import _incoming, _link
from tests.test_blocco_4d_send_postgres import FakeTransport, _decision
from tests.test_blocco_5a_visit_booking_postgres import PUB, _book, _slots, _token, vb  # noqa: F401 - fixture
from tests.test_blocco_5a_visit_booking_postgres import _link as _booking_link
from tests.test_blocco_5a_visit_booking_postgres import _property as _mandate_property

pytestmark = [
    pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL locale"),
    pytest.mark.skipif(not CLUSTER_VERIFIED, reason="cluster usa-e-getta non dichiarato verificato "
                       "(BLOCCO_2C_TEST_CLUSTER_VERIFIED=1)"),
]

DNC = "/api/communication/contacts/{}/do-not-contact"


def _contact(b, agency="a"):
    return _q(b, "INSERT INTO contacts (agency_id, display_name, first_name, last_name, phone, phone_normalized) "
                 "VALUES (%s, 'Divieto Prova', 'Divieto', 'Prova', %s, %s) RETURNING id",
              (b["ids"][agency], f"39380{uuid.uuid4().int % 10**7:07d}", f"39380{uuid.uuid4().int % 10**7:07d}"))[0][0]


# ---------------------------------------------------------------------------
# N - divieto di contatto
# ---------------------------------------------------------------------------

def test_n_ban_is_recorded_lifted_with_a_note_and_kept_in_history(app):
    ids = app["ids"]
    cid = _contact(app)
    agent = app["login"]("agent_a")
    # un agente vede (e quindi puo' vietare) solo i contatti assegnati a lui
    assert agent.post(DNC.format(cid), json={"source": "subject_request", "reason": "contatto non suo"}
                      ).status_code == 404
    _q(app, "UPDATE contacts SET assigned_agent_id = %s WHERE id = %s", (ids["agent_a"], cid))
    r = agent.post(DNC.format(cid), json={"source": "subject_request", "reason": "Telefonata del 31/10: non contattatemi"})
    assert r.status_code == 201, r.text
    st = r.json()
    assert st["active"] is True and st["source_label"] == "Richiesta dell'interessato"
    assert st["set_by_user_id"] == ids["agent_a"]                          # l'operatore e' quello della sessione
    assert agent.post(DNC.format(cid), json={"source": "subject_request", "reason": "di nuovo"}).status_code == 409
    assert agent.post(DNC.format(cid), json={"source": "subject_request", "reason": "x",
                                             "set_by_user_id": 1}).status_code == 422          # corpo chiuso
    assert agent.post(DNC.format(cid) + "/lift", json={"note": "no"}).status_code == 422       # nota obbligatoria
    owner = app["login"]("owner_a")
    r = owner.post(DNC.format(cid) + "/lift", json={"note": "Ha richiesto di nuovo informazioni per email"})
    assert r.status_code == 200 and r.json()["active"] is False
    hist = owner.get(DNC.format(cid)).json()["history"]
    assert len(hist) == 1 and hist[0]["lifted_by_user_id"] == ids["owner_a"] and hist[0]["lift_note"]
    # una seconda volta si puo' registrare di nuovo: la storia resta
    assert owner.post(DNC.format(cid), json={"source": "agency_decision", "reason": "Comportamento scorretto"}
                      ).status_code == 201
    assert len(owner.get(DNC.format(cid)).json()["history"]) == 2


def test_n_a_lift_without_a_note_is_refused_by_the_database(app):
    cid = _contact(app)
    app["login"]("owner_a").post(DNC.format(cid), json={"source": "agency_decision", "reason": "Divieto di prova"})
    with pytest.raises(Exception, match="contact_do_not_contact_lift_chk"):
        _q(app, "UPDATE contact_do_not_contact SET lifted_at = NOW(), lifted_by_user_id = set_by_user_id "
                "WHERE contact_id = %s", (cid,))


def test_n_ban_is_separate_per_agency_and_cannot_be_rewritten(app):
    cid = _contact(app)
    owner_b = app["login"]("owner_b")
    assert owner_b.get(DNC.format(cid)).status_code == 404
    assert owner_b.post(DNC.format(cid), json={"source": "agency_decision", "reason": "tentativo altrui"}
                        ).status_code == 404
    app["login"]("owner_a").post(DNC.format(cid), json={"source": "agency_decision", "reason": "Divieto di prova"})
    with pytest.raises(Exception, match="cannot be deleted"):
        _q(app, "DELETE FROM contact_do_not_contact WHERE contact_id = %s", (cid,))
    with pytest.raises(Exception, match="only lifting"):
        _q(app, "UPDATE contact_do_not_contact SET reason = 'altro motivo' WHERE contact_id = %s", (cid,))
    with pytest.raises(Exception, match="another agency"):
        _q(app, "INSERT INTO contact_do_not_contact (agency_id, contact_id, source, reason, set_by_user_id) "
                "VALUES (%s, %s, 'agency_decision', 'agenzia sbagliata', %s)",
           (app["ids"]["b"], cid, app["ids"]["owner_b"]))


def test_n_ban_is_not_a_marketing_optout_and_vice_versa(app):
    cid = _contact(app)
    owner = app["login"]("owner_a")
    owner.post(DNC.format(cid), json={"source": "subject_request", "reason": "Non vuole essere contattato"})
    # il divieto non scrive eventi di consenso ne' opt-out di piattaforma
    assert _q(app, "SELECT count(*) FROM consent_events WHERE contact_id = %s", (cid,)) == [(0,)]
    phone = _q(app, "SELECT phone_normalized FROM contacts WHERE id = %s", (cid,))[0][0]
    assert _q(app, "SELECT count(*) FROM communication_optouts WHERE address_normalized = %s", (phone,)) == [(0,)]


def test_n_the_dispatcher_suppresses_any_message_to_a_banned_contact(app):
    from communication import dispatcher
    from operator_auth.context import SystemAgencyContext

    cid = _contact(app)
    ctx = SystemAgencyContext(agency_id=app["ids"]["a"], origin="whatsapp_inbound")
    assert dispatcher._divieto_di_contatto(ctx, {"id": 1, "contact_id": cid}) is False
    app["login"]("owner_a").post(DNC.format(cid), json={"source": "subject_request", "reason": "Divieto per il dispatch"})
    assert dispatcher._divieto_di_contatto(ctx, {"id": 1, "contact_id": cid}) is True
    assert dispatcher._divieto_di_contatto(ctx, {"id": 1, "contact_id": None}) is False   # stima senza contatto
    # fail closed: una lettura che non riesce non manda
    from consent import do_not_contact

    def broken(*a, **k):
        raise RuntimeError("database non raggiungibile")
    original = do_not_contact.active
    do_not_contact.active = broken
    try:
        lifted = _contact(app)
        assert dispatcher._divieto_di_contatto(ctx, {"id": 1, "contact_id": lifted}) is True
    finally:
        do_not_contact.active = original


def test_n_manual_whatsapp_reply_and_auto_reply_respect_the_ban(wa, monkeypatch):
    from message_replies import transport

    fake = FakeTransport()
    monkeypatch.setenv("WHATSAPP_AUTO_REPLY_MODE", "send")
    monkeypatch.setenv("WHATSAPP_OUTBOUND_MODE", "meta")
    for name, value in (("enabled", lambda: True), ("credentials_present", lambda: True),
                        ("graph_version", lambda: "v-prova"), ("current", lambda: fake)):
        monkeypatch.setattr(transport, name, value)
    owner = wa["login"]("owner_a")
    owner.put("/api/auto-replies/settings", json={"mode": "off"})
    time.sleep(0.01)
    owner.put("/api/auto-replies/settings", json={"mode": "send"})
    pid, _ = _plain_property(wa, "a")
    mid, sender, contact = _incoming(wa, "Quanto costa?")
    _link(wa, mid, contact, pid)
    owner.post(DNC.format(contact), json={"source": "subject_request", "reason": "Ha chiesto di non essere contattato"})
    st = owner.get(f"/api/message-replies/conversations/{sender}").json()
    assert st["can_reply"] is False and "DO_NOT_CONTACT" in [b["code"] for b in st["blocks"]]
    r = owner.post(f"/api/message-replies/conversations/{sender}",
                   json={"text": "Buongiorno", "client_request_id": str(uuid.uuid4()), "confirm": True})
    assert r.status_code == 409 and "DO_NOT_CONTACT" in r.text
    assert _q(wa, "SELECT count(*) FROM communication_messages WHERE contact_id = %s", (contact,)) == [(0,)]
    out = owner.post("/api/auto-replies/tick").json()
    assert _decision(wa, mid)[1:3] == ("skipped", "do_not_contact") and out["sent"] == 0 and fake.calls == []


# ---------------------------------------------------------------------------
# Q - risposte in coda che non possono piu' partire
# ---------------------------------------------------------------------------

def _queued_reply(b, *, closes_at, reason="operator_reply"):
    from communication import service as communication
    from operator_auth.context import SystemAgencyContext

    cid = _contact(b)
    ctx = SystemAgencyContext(agency_id=b["ids"]["a"], origin="whatsapp_inbound")
    return communication.enqueue(
        ctx, contact_id=cid, channel="whatsapp", communication_type="service", mode="automatic", reason_code=reason,
        rendered_body="Risposta rimasta in coda", destination_snapshot="393330000001",
        idempotency_key=f"prova-coda:{uuid.uuid4()}",
        metadata={"service_basis": "customer_initiated_whatsapp", "window_closes_at": closes_at.isoformat()})["message"]


def test_q_queued_replies_with_a_closed_window_are_closed_as_not_sent(wa, monkeypatch):
    monkeypatch.delenv("WHATSAPP_AUTO_REPLY_MODE", raising=False)      # anche col motore fermo
    now = datetime.now(timezone.utc)
    old_manual = _queued_reply(wa, closes_at=now - timedelta(hours=1))
    old_auto = _queued_reply(wa, closes_at=now - timedelta(minutes=1), reason="auto_reply")
    nearly = _queued_reply(wa, closes_at=now + timedelta(minutes=2))      # dentro il margine di sicurezza
    fresh = _queued_reply(wa, closes_at=now + timedelta(hours=5))
    # una risposta gia' presa in carico (il claim vero) non si tocca: puo' essere partita
    from communication import service as communication
    from operator_auth.context import SystemAgencyContext
    taken = _queued_reply(wa, closes_at=now - timedelta(hours=2))
    ctx = SystemAgencyContext(agency_id=wa["ids"]["a"], origin="whatsapp_inbound")
    assert communication.claim_message(ctx, taken["id"], provider="prova", channel="whatsapp") is not None
    out = wa["login"]("owner_a").post("/api/auto-replies/tick").json()
    assert out["status"] == "stopped" and out["expired"] >= 3
    status = dict(_q(wa, "SELECT id, status || ':' || coalesce(suppressed_reason, '') FROM communication_messages "
                         "WHERE id = ANY(%s)", ([old_manual["id"], old_auto["id"], nearly["id"], fresh["id"]],)))
    assert status[old_manual["id"]] == status[old_auto["id"]] == status[nearly["id"]] == "suppressed:window_expired"
    assert status[fresh["id"]] == "queued:"
    assert _q(wa, "SELECT status FROM communication_messages WHERE id = %s", (taken["id"],)) == [("sending",)]
    # un secondo giro non chiude nulla di nuovo fra queste
    wa["login"]("owner_a").post("/api/auto-replies/tick")
    assert _q(wa, "SELECT status FROM communication_messages WHERE id = ANY(%s) ORDER BY id",
              ([fresh["id"], taken["id"]],)) == [("queued",), ("sending",)]


# ---------------------------------------------------------------------------
# V - revoca dell'autorizzazione alle visite
# ---------------------------------------------------------------------------

def test_v_owner_revocation_is_recorded_with_a_note_and_pending_requests_are_counted(vb):
    ids = vb["ids"]
    pid = _mandate_property(vb)
    token = _token(vb, pid, owner="per_visit")
    start = _slots(vb, token)[0]["start_at"]
    assert _book(vb, token, start).json()["state"] == "pending_owner"
    link_id = vb["login"]("owner_a").get("/api/visit-booking-links", params={"property_id": pid}).json()["items"][0]["id"]
    owner = vb["login"]("owner_a")
    r = owner.post(f"/api/visit-booking-links/{link_id}/disable", json={"reason": "owner_authorization_revoked"})
    assert r.status_code == 422                                               # nota obbligatoria
    r = owner.post(f"/api/visit-booking-links/{link_id}/disable",
                   json={"reason": "owner_authorization_revoked", "note": "Email del proprietario del 31/10"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "disabled" and out["disabled_reason"] == "owner_authorization_revoked"
    assert out["pending_owner_requests"] == 1
    (row,) = _q(vb, "SELECT disabled_by_user_id, disabled_at IS NOT NULL, disabled_note FROM property_visit_booking_links "
                    "WHERE id = %s", (link_id,))
    assert row == (ids["owner_a"], True, "Email del proprietario del 31/10")
    # la visita richiesta NON cambia significato da sola
    assert _q(vb, "SELECT status FROM appointments WHERE property_id = %s AND start_at = %s", (pid, start)) == \
        [("requested",)]
    assert vb["public"].get(f"{PUB}/{token}").status_code == 404
    listed = owner.get("/api/visit-booking-links", params={"property_id": pid}).json()["items"][0]
    assert listed["disabled_reason"] == "owner_authorization_revoked" and listed["disabled_note"]
    # il database non accetta una revoca del proprietario senza nota
    other = _booking_link(vb, pid).json()["id"]
    with pytest.raises(Exception, match="revocation_note_chk"):
        _q(vb, "UPDATE property_visit_booking_links SET status = 'disabled', disabled_at = NOW(), "
               "disabled_reason = 'owner_authorization_revoked', disabled_note = NULL WHERE id = %s", (other,))


def test_v_a_plain_disable_without_body_still_works(vb):
    pid = _mandate_property(vb)
    link = _booking_link(vb, pid).json()
    r = vb["login"]("owner_a").post(f"/api/visit-booking-links/{link['id']}/disable")
    assert r.status_code == 200 and r.json()["disabled_reason"] == "operator"
