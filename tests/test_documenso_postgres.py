"""DOCUMENSO - il flusso di firma del foglio visita su PostgreSQL VERO (banco 2C), contro un Documenso FINTO.

    C  creazione: due firmatari, nessun invio, link personali, idempotenza, un solo aperto per foglio
    W  webhook: segreto condiviso, stato riletto dal fornitore, deduplica, prove archiviate con SHA-256
    R  riconciliazione senza webhook, scadenza, annullamento, errori del fornitore
    P  permessi, agenzie, dati riservati; la firma e' SEMPLICE (mai avanzata/qualificata); sandbox mai «valida»
    D  il database: i firmatari sono append-only nelle parti che contano

Nessuna credenziale vera, nessun invio, nessuna rete esterna.
"""
from __future__ import annotations

import hashlib
import json
import uuid

import pytest

from tests.documenso_fake import TOKEN, WEBHOOK_SECRET, FakeDocumenso, webhook_body
from tests.test_blocco_2c_integrated_postgres import CLUSTER_VERIFIED, DSN, _q, app, banco  # noqa: F401
from tests.test_blocco_5a_visit_booking_postgres import _property
from tests.test_blocco_6a_visit_sheets_postgres import _visit

pytestmark = [
    pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL locale"),
    pytest.mark.skipif(not CLUSTER_VERIFIED, reason="cluster usa-e-getta non dichiarato verificato "
                       "(BLOCCO_2C_TEST_CLUSTER_VERIFIED=1)"),
]

API = "/api/signature-requests"
HOOK = "/api/public/esign/webhooks/documenso"
CLIENTE = "cliente.documenso@esempio.invalid"


@pytest.fixture
def fake():
    f = FakeDocumenso()
    yield f
    f.close()


@pytest.fixture
def doc(app, fake, monkeypatch):
    """Il banco con Documenso configurato (sull'URL del finto) e la cassetta degli attrezzi."""
    monkeypatch.setenv("ESIGN_PROVIDER", "documenso")
    monkeypatch.setenv("DOCUMENSO_API_URL", fake.url)
    monkeypatch.setenv("DOCUMENSO_API_TOKEN", TOKEN)
    monkeypatch.setenv("DOCUMENSO_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.delenv("DOCUMENSO_ENVIRONMENT", raising=False)
    # il database e' condiviso fra i test del modulo: le richieste rimaste aperte da un test precedente (il cui Documenso
    # finto non esiste piu') si chiudono, cosi' ogni test riconcilia solo cio' che ha creato
    _q(app, "UPDATE signature_requests SET status = 'cancelled', closed_at = NOW() WHERE status = 'sent'")
    return {"app": app, "fake": fake, "owner": app["login"]("owner_a"), "agent": app["login"]("agent_a")}


def _sheet(b):
    """Un foglio vero di una visita svolta (agente: agent_a)."""
    aid = _visit(b, _property(b))
    _q(b, "SET session_replication_role = replica; UPDATE appointments SET status = 'completed', "
          "completed_at = start_at + interval '50 minutes' WHERE id = %s", (aid,))
    return b["login"]("owner_a").post(f"/api/visit-sheets/appointments/{aid}").json()["sheet"]


def _create(client, sheet_id, key=None, email=CLIENTE, **extra):
    return client.post(API, json={"visit_sheet_id": sheet_id, "signer_name": "Cliente Documenso", "signer_contact": email,
                                  "expires_in_days": 7, "idempotency_key": key or str(uuid.uuid4()), **extra})


def _post_hook(client, fake, ref, event, secret=WEBHOOK_SECRET, **kw):
    body = webhook_body(event, fake.envelopes[ref], **kw)
    headers = {"X-Documenso-Secret": secret} if secret is not None else {}
    return client.post(HOOK, content=body, headers=headers)


def _agent_email(b):
    return _q(b, "SELECT email FROM operator_users WHERE id = %s", (b["ids"]["agent_a"],))[0][0]


# ---------------------------------------------------------------------------------------------------------- C

def test_c_la_richiesta_ha_due_firmatari_nessun_invio_e_link_personali(doc):
    b, fake, owner = doc["app"], doc["fake"], doc["owner"]
    sheet = _sheet(b)
    r = _create(owner, sheet["id"])
    assert r.status_code == 200, r.text
    req = r.json()["request"]
    assert r.json()["created"] is True and req["provider"] == "documenso" and req["simulation"] is False
    assert req["status"] == "sent" and req["provider_environment"] == "sandbox" and req["valid_signature"] is False
    assert [(s["role"], s["status"], s["has_link"]) for s in req["signers"]] == [("visitor", "pending", True), ("agent", "pending", True)]
    assert [s["email"] for s in req["signers"]] == [CLIENTE, _agent_email(b)]
    # nessun invio e nessun segreto nella vista della richiesta
    assert "signing_url" not in json.dumps(req) and "token" not in json.dumps(req).lower()
    created = fake.only("POST", "/envelope/create")[0]["payload"]
    assert created["meta"]["distributionMethod"] == "NONE" and not any(created["meta"]["emailSettings"].values())
    assert created["externalId"] == _q(b, "SELECT idempotency_key FROM signature_requests WHERE id = %s", (req["id"],))[0][0]
    # il PDF caricato e' il foglio archiviato, byte per byte; i campi sono due, sulla stessa riga
    ref = req["provider_reference"]
    assert hashlib.sha256(fake.envelopes[ref]["pdf"]).hexdigest() == sheet["sha256"]
    f0, f1 = (r_["fields"][0] for r_ in created["recipients"])
    assert f0["positionY"] == f1["positionY"] and f0["positionX"] < f1["positionX"]
    # i link: personali, distinti, a due ruoli
    links = owner.get(f"{API}/{req['id']}/signing-links")
    assert links.status_code == 200 and links.headers["cache-control"] == "no-store"
    urls = {i["role"]: i["url"] for i in links.json()["items"]}
    assert set(urls) == {"visitor", "agent"} and urls["visitor"] != urls["agent"] and all(u.startswith(fake.origin + "/sign/") for u in urls.values())
    qr = owner.get(f"{API}/{req['id']}/signing-links/visitor/qr.svg")
    assert qr.status_code == 200 and qr.headers["content-type"] == "image/svg+xml" and b"<svg" in qr.content
    # il collegamento NON compare nell'elenco della scheda, ne' in config
    listing = owner.get(API, params={"visit_sheet_id": sheet["id"]})
    assert urls["visitor"] not in listing.text and urls["agent"] not in listing.text
    cfg = owner.get(f"{API}/config/provider").json()
    assert cfg == {"configured": True, "provider": "documenso", "simulation": False, "environment": "sandbox", "signers": True}
    assert TOKEN not in json.dumps(cfg)
    # eventi e registro: una sola riga «sent» con provider e ambiente
    assert _q(b, "SELECT from_status, to_status FROM signature_request_events WHERE request_id = %s", (req["id"],)) == [(None, "sent")]


def test_c_idempotenza_un_solo_aperto_e_nessun_doppione(doc):
    b, fake, owner = doc["app"], doc["fake"], doc["owner"]
    sheet = _sheet(b)
    key = str(uuid.uuid4())
    a, a2 = _create(owner, sheet["id"], key), _create(owner, sheet["id"], key)
    assert a.json()["created"] is True and a2.json()["created"] is False
    assert a2.json()["request"]["id"] == a.json()["request"]["id"] and len(fake.envelopes) == 1
    # un altro tentativo (chiave diversa) mentre una richiesta e' aperta: rifiutato, nessuna seconda busta
    again = _create(owner, sheet["id"])
    assert again.status_code == 409 and again.json()["detail"]["code"] == "REQUEST_OPEN" and len(fake.envelopes) == 1
    assert _q(b, "SELECT count(*) FROM signature_requests WHERE visit_sheet_id = %s", (sheet["id"],)) == [(1,)]
    # annullata, se ne puo' fare un'altra
    assert owner.post(f"{API}/{a.json()['request']['id']}/cancel").status_code == 200
    assert _create(owner, sheet["id"]).status_code == 200 and len(fake.envelopes) == 2


def test_c_cliente_e_agente_devono_essere_persone_diverse_e_serve_l_email(doc):
    b, owner, fake = doc["app"], doc["owner"], doc["fake"]
    sheet = _sheet(b)
    prima = _q(b, "SELECT count(*) FROM signature_requests")
    assert _create(owner, sheet["id"], email=_agent_email(b)).status_code == 409
    assert _create(owner, sheet["id"], email=_agent_email(b).upper()).status_code == 409
    assert _create(owner, sheet["id"], email="3331234567").status_code == 400             # un telefono non e' un indirizzo
    assert _create(owner, sheet["id"], email="non-un-indirizzo").status_code == 400
    assert not fake.envelopes and _q(b, "SELECT count(*) FROM signature_requests") == prima


def test_c_senza_credenziali_non_si_simula_nulla(app, monkeypatch):
    monkeypatch.setenv("ESIGN_PROVIDER", "documenso")
    monkeypatch.delenv("DOCUMENSO_API_TOKEN", raising=False)
    owner = app["login"]("owner_a")
    sheet = _sheet(app)
    prima = _q(app, "SELECT count(*) FROM signature_requests")
    r = _create(owner, sheet["id"])
    assert r.status_code == 503 and r.json()["detail"]["code"] == "PROVIDER_NOT_CONFIGURED"
    assert owner.get(f"{API}/config/provider").json()["configured"] is False
    assert _q(app, "SELECT count(*) FROM signature_requests") == prima          # nessuna richiesta, nemmeno «simulata»


def test_c_errore_del_fornitore_non_lascia_righe_e_si_puo_riprovare(doc):
    b, fake, owner = doc["app"], doc["fake"], doc["owner"]
    sheet = _sheet(b)
    key = str(uuid.uuid4())
    fake.fail["POST /envelope/create"] = 500
    r = _create(owner, sheet["id"], key)
    assert r.status_code == 503 and r.json()["detail"]["code"] == "PROVIDER_REJECTED"
    assert "errore finto" not in r.text and TOKEN not in r.text
    assert _q(b, "SELECT count(*) FROM signature_requests WHERE idempotency_key = %s", (key,)) == [(0,)]
    ok = _create(owner, sheet["id"], key)
    assert ok.status_code == 200 and len(fake.envelopes) == 1


# ---------------------------------------------------------------------------------------------------------- W

def _setup_signed(doc):
    b, fake, owner = doc["app"], doc["fake"], doc["owner"]
    sheet = _sheet(b)
    req = _create(owner, sheet["id"]).json()["request"]
    return b, fake, owner, sheet, req, req["provider_reference"]


def test_w_firma_completa_via_webhook_archivia_pdf_e_prove(doc):
    b, fake, owner, sheet, req, ref = _setup_signed(doc)
    public = b["login"]("owner_b").__class__  # noqa: F841 - il webhook e' pubblico: client senza sessione
    from fastapi.testclient import TestClient

    import main
    hook = TestClient(main.app, base_url="https://testserver")
    # segreto assente o sbagliato -> 401 e NESSUN effetto
    eventi = _q(b, "SELECT count(*) FROM signature_provider_events")
    assert _post_hook(hook, fake, ref, "DOCUMENT_COMPLETED", secret=None).status_code == 401
    assert _post_hook(hook, fake, ref, "DOCUMENT_COMPLETED", secret="sbagliato").status_code == 401
    assert _q(b, "SELECT count(*) FROM signature_provider_events") == eventi
    # un evento di completamento FALSO (nessuno ha firmato): il segreto e' giusto ma lo stato si rilegge -> nessun cambio
    r = _post_hook(hook, fake, ref, "DOCUMENT_COMPLETED")
    assert r.status_code == 200 and r.json()["outcome"] == "no_change"
    assert _q(b, "SELECT status FROM signature_requests WHERE id = %s", (req["id"],)) == [("sent",)]
    # firma il cliente: arriva DOCUMENT_SIGNED -> lo stato personale si aggiorna, la richiesta resta aperta
    fake.sign(ref, CLIENTE)
    assert _post_hook(hook, fake, ref, "DOCUMENT_SIGNED").json()["outcome"] == "no_change"
    assert _q(b, "SELECT role, status, signed_at IS NOT NULL FROM signature_request_signers WHERE request_id = %s ORDER BY role",
              (req["id"],)) == [("agent", "pending", False), ("visitor", "signed", True)]
    assert _q(b, "SELECT status FROM signature_requests WHERE id = %s", (req["id"],)) == [("sent",)]
    # firma l'agente: completato
    fake.sign(ref, _agent_email(b))
    done = _post_hook(hook, fake, ref, "DOCUMENT_COMPLETED")
    assert done.status_code == 200 and done.json() == {"outcome": "applied", "status": "signed"}
    row = _q(b, "SELECT status, signature_level, signed_at IS NOT NULL, provider_environment, received_document_sha256, "
                "audit_trail_sha256, closed_at IS NOT NULL FROM signature_requests WHERE id = %s", (req["id"],))[0]
    assert row[:4] == ("signed", "ses", True, "sandbox") and row[4] and row[5] and row[6]
    # PDF firmato e prove archiviati con la loro impronta
    pdf = owner.get(f"{API}/{req['id']}/document")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF") and hashlib.sha256(pdf.content).hexdigest() == row[4]
    assert pdf.content != fake.envelopes[ref]["pdf"] and pdf.headers["content-disposition"].startswith('attachment; filename="documento-firmato')
    trail = owner.get(f"{API}/{req['id']}/audit-trail")
    assert trail.status_code == 200 and trail.headers["content-type"] == "application/json"
    assert hashlib.sha256(trail.content).hexdigest() == row[5] and json.loads(trail.content)["envelope_id"] == ref
    # stato finale: i due firmatari hanno firmato, i link sono azzerati
    final = owner.get(API, params={"visit_sheet_id": sheet["id"]}).json()["items"][0]
    assert [s["status"] for s in final["signers"]] == ["signed", "signed"] and not any(s["has_link"] for s in final["signers"])
    assert owner.get(f"{API}/{req['id']}/signing-links").status_code == 409
    # la firma e' SEMPLICE, dichiarata come tale; in sandbox non e' mai «valida»
    assert final["signature_level"] == "ses" and final["signature_level_label"] == "Firma elettronica semplice"
    assert final["valid_signature"] is False and "avanzata" not in json.dumps(final).lower() and "qualificata" not in json.dumps(final).lower()
    # lo stesso evento riconsegnato (retry del fornitore): nessun secondo effetto
    again = _post_hook(hook, fake, ref, "DOCUMENT_COMPLETED")
    assert again.json()["outcome"] == "duplicate"
    assert _q(b, "SELECT count(*) FROM signature_request_events WHERE request_id = %s AND to_status = 'signed'", (req["id"],)) == [(1,)]
    outcomes = _q(b, "SELECT outcome FROM signature_provider_events ORDER BY id")
    assert outcomes[-1] == ("applied",) and ("no_change",) in outcomes
    # il PDF firmato non e' il foglio: il foglio resta immutabile con la sua impronta
    assert _q(b, "SELECT sha256 FROM visit_sheets WHERE id = %s", (sheet["id"],)) == [(sheet["sha256"],)]


def test_w_in_produzione_la_firma_conta_ma_resta_semplice(doc, monkeypatch):
    monkeypatch.setenv("DOCUMENSO_ENVIRONMENT", "production")
    b, fake, owner, sheet, req, ref = _setup_signed(doc)
    assert req["provider_environment"] == "production"
    fake.sign(ref, CLIENTE)
    fake.sign(ref, _agent_email(b))
    out = b["login"]("owner_a").__class__  # noqa: F841
    assert owner.get(f"{API}/config/provider").json()["environment"] == "production"
    from fastapi.testclient import TestClient

    import main
    assert _post_hook(TestClient(main.app, base_url="https://testserver"), fake, ref, "DOCUMENT_COMPLETED").json()["outcome"] == "applied"
    final = owner.get(API, params={"visit_sheet_id": sheet["id"]}).json()["items"][0]
    assert final["valid_signature"] is True                            # condizione TECNICA...
    assert final["signature_level"] == "ses" and final["signature_level_label"] == "Firma elettronica semplice"   # ...di livello semplice


def test_w_evento_di_un_altro_documento_o_senza_riferimento(doc):
    b, fake, owner, sheet, req, ref = _setup_signed(doc)
    from fastapi.testclient import TestClient

    import main
    hook = TestClient(main.app, base_url="https://testserver")
    ignoto = json.dumps({"event": "DOCUMENT_COMPLETED", "payload": {"externalId": str(uuid.uuid4())}}).encode()
    r = hook.post(HOOK, content=ignoto, headers={"X-Documenso-Secret": WEBHOOK_SECRET})
    assert r.status_code == 200 and r.json()["outcome"] == "unknown_request"
    assert hook.post(HOOK, content=ignoto, headers={"X-Documenso-Secret": WEBHOOK_SECRET}).json()["outcome"] == "duplicate"
    assert hook.post("/api/public/esign/webhooks/simulated", content=ignoto).status_code == 404
    assert hook.post("/api/public/esign/webhooks/altro", content=ignoto).status_code == 404
    assert _q(b, "SELECT status FROM signature_requests WHERE id = %s", (req["id"],)) == [("sent",)]


def test_w_rifiuto_del_firmatario(doc):
    b, fake, owner, sheet, req, ref = _setup_signed(doc)
    from fastapi.testclient import TestClient

    import main
    fake.reject(ref, CLIENTE)
    r = _post_hook(TestClient(main.app, base_url="https://testserver"), fake, ref, "DOCUMENT_REJECTED")
    assert r.json() == {"outcome": "applied", "status": "declined"}
    assert _q(b, "SELECT status, received_document IS NULL FROM signature_requests WHERE id = %s", (req["id"],)) == [("declined", True)]
    assert _q(b, "SELECT role, status FROM signature_request_signers WHERE request_id = %s ORDER BY role", (req["id"],)) == [
        ("agent", "pending"), ("visitor", "declined")]


def test_w_se_il_fornitore_ha_firmato_un_altro_file_la_firma_non_si_registra(doc):
    b, fake, owner, sheet, req, ref = _setup_signed(doc)
    from fastapi.testclient import TestClient

    import main
    fake.sign(ref, CLIENTE)
    fake.sign(ref, _agent_email(b))
    fake.tamper_original = True
    eventi = _q(b, "SELECT count(*) FROM signature_provider_events")
    r = _post_hook(TestClient(main.app, base_url="https://testserver"), fake, ref, "DOCUMENT_COMPLETED")
    assert r.status_code == 503 and r.json()["detail"]["code"] == "ORIGINAL_MISMATCH"
    assert _q(b, "SELECT status, received_document IS NULL FROM signature_requests WHERE id = %s", (req["id"],)) == [("sent", True)]
    assert _q(b, "SELECT count(*) FROM signature_provider_events") == eventi    # niente evento registrato: il retry puo' riuscire


# ---------------------------------------------------------------------------------------------------------- R

def test_r_riconciliazione_senza_webhook(doc):
    from esign import service
    b, fake, owner, sheet, req, ref = _setup_signed(doc)
    ag = b["ids"]["a"]
    assert service.reconcile(ag)["unchanged"] == 1                       # niente e' successo
    fake.sign(ref, CLIENTE)
    fake.sign(ref, _agent_email(b))
    assert service.reconcile(ag) == {"checked": 1, "applied": 1, "expired": 0, "unchanged": 0, "not_configured": 0, "errors": 0}
    assert _q(b, "SELECT status, signature_level FROM signature_requests WHERE id = %s", (req["id"],)) == [("signed", "ses")]
    assert _q(b, "SELECT actor FROM signature_request_events WHERE request_id = %s AND to_status = 'signed'", (req["id"],)) == [("reconcile:documenso",)]
    assert service.reconcile(ag)["checked"] == 0                         # chiusa: non si rilegge piu'
    # un errore del fornitore non rompe il giro e si ritenta
    sheet2 = _sheet(b)
    _create(owner, sheet2["id"])
    fake.fail["GET /envelope/"] = 500
    assert service.reconcile(ag)["errors"] == 1
    assert service.reconcile(ag)["unchanged"] == 1


def test_r_scadenza_revoca_il_link_presso_il_fornitore(doc):
    from esign import service
    b, fake, owner, sheet, req, ref = _setup_signed(doc)
    _q(b, "SET session_replication_role = replica; UPDATE signature_requests SET expires_at = NOW() - interval '1 minute' "
          "WHERE id = %s", (req["id"],))
    assert service.reconcile(b["ids"]["a"])["expired"] == 1
    assert fake.envelopes[ref]["deletedAt"] is not None                  # il fornitore non accetta piu' firme
    assert _q(b, "SELECT status FROM signature_requests WHERE id = %s", (req["id"],)) == [("expired",)]
    assert _q(b, "SELECT count(*) FROM signature_request_signers WHERE request_id = %s AND signing_url IS NOT NULL", (req["id"],)) == [(0,)]


def test_r_annullamento_chiude_anche_presso_il_fornitore_e_se_non_conferma_resta_aperta(doc):
    b, fake, owner, sheet, req, ref = _setup_signed(doc)
    fake.fail["POST /envelope/delete"] = 500
    r = owner.post(f"{API}/{req['id']}/cancel")
    assert r.status_code == 503 and _q(b, "SELECT status FROM signature_requests WHERE id = %s", (req["id"],)) == [("sent",)]
    ok = owner.post(f"{API}/{req['id']}/cancel")
    assert ok.status_code == 200 and ok.json()["status"] == "cancelled" and fake.envelopes[ref]["deletedAt"] is not None
    assert owner.get(f"{API}/{req['id']}/signing-links").status_code == 409
    assert _q(b, "SELECT count(*) FROM signature_request_signers WHERE request_id = %s AND signing_url IS NOT NULL", (req["id"],)) == [(0,)]


def test_r_la_rotta_interna_di_riconciliazione_e_protetta_dal_segreto_di_servizio(doc, monkeypatch):
    from fastapi.testclient import TestClient

    import main
    b = doc["app"]
    c = TestClient(main.app, base_url="https://testserver")
    body = {"agency_id": b["ids"]["a"]}
    monkeypatch.delenv("COMMUNICATION_CRON_TOKEN", raising=False)
    assert c.post("/api/internal/esign/reconcile", json=body).status_code == 503          # spenta senza segreto
    token = "t" * 40
    monkeypatch.setenv("COMMUNICATION_CRON_TOKEN", token)
    monkeypatch.setenv("COMMUNICATION_CRON_AGENCY_IDS", str(b["ids"]["a"]))
    assert c.post("/api/internal/esign/reconcile", json=body).status_code == 401
    assert c.post("/api/internal/esign/reconcile", json=body, headers={"X-Stima360-Cron-Token": "x" * 40}).status_code == 401
    assert c.post("/api/internal/esign/reconcile", json={"agency_id": b["ids"]["b"]}, headers={"X-Stima360-Cron-Token": token}).status_code == 403
    ok = c.post("/api/internal/esign/reconcile", json=body, headers={"X-Stima360-Cron-Token": token})
    assert ok.status_code == 200 and set(ok.json()) == {"checked", "applied", "expired", "unchanged", "not_configured", "errors"}
    assert c.post("/api/internal/esign/reconcile", json={**body, "x": 1}, headers={"X-Stima360-Cron-Token": token}).status_code == 422


# ---------------------------------------------------------------------------------------------------------- P

def test_p_permessi_e_isolamento(doc):
    b, fake, owner, sheet, req, ref = _setup_signed(doc)
    agent = doc["agent"]
    # l'agente assegnato alla visita vede la richiesta e i suoi link (e firma, se serve, dal CRM)
    assert agent.get(f"{API}/{req['id']}/signing-links").status_code == 200
    # un altro agente della stessa agenzia e un'altra agenzia: 404 su tutto, mai i link
    for who in ("agent2_a", "owner_b"):
        c = b["login"](who)
        assert c.get(f"{API}/{req['id']}/signing-links").status_code == 404, who
        assert c.get(f"{API}/{req['id']}/signing-links/visitor/qr.svg").status_code == 404, who
        assert c.get(API, params={"visit_sheet_id": sheet["id"]}).status_code == 404, who
        assert c.get(f"{API}/{req['id']}/document").status_code == 404, who
        assert c.get(f"{API}/{req['id']}/audit-trail").status_code == 404, who
        assert c.post(f"{API}/{req['id']}/cancel").status_code == 404, who
        assert _create(c, sheet["id"]).status_code == 404, who
    # senza sessione: nulla
    from fastapi.testclient import TestClient

    import main
    anon = TestClient(main.app, base_url="https://testserver")
    for path in (f"{API}/{req['id']}/signing-links", f"{API}/{req['id']}/signing-links/visitor/qr.svg", f"{API}/config/provider"):
        assert anon.get(path).status_code == 401, path
    assert owner.get(f"{API}/{req['id']}/signing-links/altro/qr.svg").status_code == 422
    assert owner.get(f"{API}/999999/signing-links").status_code == 404


def test_p_nessun_invio_di_messaggi_dal_crm(doc):
    b = doc["app"]
    prima = _q(b, "SELECT count(*) FROM communication_messages")
    b, fake, owner, sheet, req, ref = _setup_signed(doc)
    owner.get(f"{API}/{req['id']}/signing-links")
    assert _q(b, "SELECT count(*) FROM communication_messages") == prima           # nessun messaggio accodato dal CRM
    assert all(r["path"] not in ("/envelope/redistribute",) for r in fake.requests)         # nessun re-invio mai
    assert not any(r["path"].startswith("/envelope/distribute") and json.loads(r["raw"])["meta"]["distributionMethod"] != "NONE" for r in fake.requests)


# ---------------------------------------------------------------------------------------------------------- D

def test_d_i_firmatari_sono_protetti_dal_database(doc):
    b, fake, owner, sheet, req, ref = _setup_signed(doc)
    import psycopg2
    sid = _q(b, "SELECT id FROM signature_request_signers WHERE request_id = %s AND role = 'visitor'", (req["id"],))[0][0]
    for sql in ("DELETE FROM signature_request_signers WHERE id = %s",
                "UPDATE signature_request_signers SET email = 'altro@esempio.invalid' WHERE id = %s",
                "UPDATE signature_request_signers SET role = 'agent' WHERE id = %s",
                "UPDATE signature_request_signers SET status = 'signed' WHERE id = %s"):               # firmato senza data
        with pytest.raises(psycopg2.Error):
            _q(b, sql, (sid,))
    _q(b, "UPDATE signature_request_signers SET status = 'signed', signed_at = NOW() WHERE id = %s", (sid,))
    with pytest.raises(psycopg2.Error):                                                              # finale
        _q(b, "UPDATE signature_request_signers SET status = 'declined', signed_at = NULL WHERE id = %s", (sid,))
    with pytest.raises(psycopg2.Error):                                                              # un solo firmatario per ruolo
        _q(b, "INSERT INTO signature_request_signers (request_id, agency_id, role, name, email) SELECT request_id, agency_id, 'visitor', 'x', 'x@y.zz' FROM signature_request_signers WHERE id = %s", (sid,))


# ---------------------------------------------------------------------------------------------------------- «Aggiorna stato»

def test_r_aggiorna_stato_dall_operatore_senza_webhook(doc):
    b, fake, owner, sheet, req, ref = _setup_signed(doc)
    assert owner.post(f"{API}/{req['id']}/refresh").json()["outcome"] == "no_change"
    fake.sign(ref, CLIENTE)
    r = owner.post(f"{API}/{req['id']}/refresh").json()
    assert r["outcome"] == "no_change" and [s["status"] for s in r["request"]["signers"]] == ["signed", "pending"]
    fake.sign(ref, _agent_email(b))
    done = owner.post(f"{API}/{req['id']}/refresh").json()
    assert done["outcome"] == "applied" and done["request"]["status"] == "signed" and done["request"]["signature_level"] == "ses"
    assert owner.post(f"{API}/{req['id']}/refresh").status_code == 409                   # chiusa
    assert doc["app"]["login"]("owner_b").post(f"{API}/{req['id']}/refresh").status_code == 404
    assert doc["app"]["login"]("agent2_a").post(f"{API}/{req['id']}/refresh").status_code == 404


def test_r_aggiorna_stato_non_vale_per_la_simulazione(app, monkeypatch):
    monkeypatch.delenv("ESIGN_PROVIDER", raising=False)
    owner = app["login"]("owner_a")
    sheet = _sheet(app)
    r = owner.post(API, json={"visit_sheet_id": sheet["id"], "signer_name": "Sim", "signer_contact": "sim@esempio.invalid",
                              "expires_in_days": 7, "idempotency_key": str(uuid.uuid4())}).json()["request"]
    assert r["simulation"] is True and owner.post(f"{API}/{r['id']}/refresh").status_code == 409
    assert owner.get(f"{API}/config/provider").json() == {"configured": True, "provider": "simulated", "simulation": True,
                                                         "environment": "simulation", "signers": False}


def test_r_la_scadenza_non_chiude_in_locale_una_busta_ancora_valida_presso_il_fornitore(doc):
    """Segnare `expired` senza revocare lascerebbe un link ancora valido: per un fornitore reale chiude SOLO la riconciliazione."""
    from esign import service
    b, fake, owner, sheet, req, ref = _setup_signed(doc)
    _q(b, "SET session_replication_role = replica; UPDATE signature_requests SET expires_at = NOW() - interval '1 minute' WHERE id = %s",
       (req["id"],))
    listing = owner.get(API, params={"visit_sheet_id": sheet["id"]}).json()["items"][0]
    assert listing["status"] == "sent" and fake.envelopes[ref]["deletedAt"] is None             # nessuna chiusura «a metà»
    links = owner.get(f"{API}/{req['id']}/signing-links")                                       # ma il link non si consegna piu'
    assert links.status_code == 409 and links.json()["detail"]["code"] == "REQUEST_EXPIRED"
    assert _create(owner, sheet["id"]).status_code == 409                                       # e non si crea un doppione
    assert service.reconcile(b["ids"]["a"])["expired"] == 1
    assert fake.envelopes[ref]["deletedAt"] is not None
    assert owner.get(API, params={"visit_sheet_id": sheet["id"]}).json()["items"][0]["status"] == "expired"
