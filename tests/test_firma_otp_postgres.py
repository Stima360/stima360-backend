"""FIRMA OTP - il procedimento su PostgreSQL VERO (banco 2C), con il trasporto email FINTO che cattura i messaggi.

    F  flusso positivo: richiesta -> email con il collegamento -> pagina -> PDF -> dichiarazione -> OTP -> conferma ->
       firma dell'agente -> documento sottoscritto e rapporto archiviati -> stato nel CRM -> prove integre
    N  flussi negativi: codice errato/scaduto/riusato, troppi tentativi, troppe richieste, collegamento scaduto/revocato,
       cliente diverso, accesso non autorizzato, SMTP guasto, doppia conferma, due richieste insieme, altra agenzia,
       alterazione del PDF archiviato e delle prove, errori della firma dell'agente
Nessuna email reale: il trasporto e' finto; i destinatari sono comunque limitati dall'elenco.
"""
from __future__ import annotations

import hashlib
import json
import threading
import uuid

import pytest

from tests.firma_otp_support import API, CLIENTE, PUB, Posta, codice_da, configura, crea, foglio, link_da
from tests.test_blocco_2c_integrated_postgres import CLUSTER_VERIFIED, DSN, _q, app, banco  # noqa: F401

pytestmark = [
    pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL locale"),
    pytest.mark.skipif(not CLUSTER_VERIFIED, reason="cluster usa-e-getta non dichiarato verificato "
                       "(BLOCCO_2C_TEST_CLUSTER_VERIFIED=1)"),
]
PASSWORD_AGENTE = None
# SENTINELLA AGGIORNATA (FIRMA SEMPLIFICATA): la dichiarazione accettata oggi e' la v2.


@pytest.fixture
def otp(app, monkeypatch):
    posta = Posta()
    configura(monkeypatch, posta)
    _q(app, "UPDATE signature_requests SET status = 'cancelled', closed_at = NOW() WHERE status = 'sent'")
    from tests.test_blocco_2c_integrated_postgres import PASSWORD
    return {"app": app, "posta": posta, "owner": app["login"]("owner_a"), "agent": app["login"]("agent_a"), "password": PASSWORD,
            "public": _public(app)}


def _public(app):
    from fastapi.testclient import TestClient

    import main
    return TestClient(main.app, base_url="https://testserver")


def _avvia(o, *, email=CLIENTE):
    sheet = foglio(o["app"])
    r = crea(o["owner"], sheet["id"], email=email)
    assert r.status_code == 200, r.text
    req = r.json()["request"]
    url, token = link_da(o["posta"].per("signature_link", email)[-1])
    return sheet, req, token


def _richiedi_codice(o, token) -> str:
    r = o["public"].post(f"{PUB}/{token}/otp")
    assert r.status_code == 200, r.text
    return codice_da(o["posta"].per("signature_otp")[-1])


def _conferma(o, token, code, **extra):
    body = {"code": code, "consent_accepted": True, "consent_version": "dichiarazione_firma_otp_v2", **extra}
    return o["public"].post(f"{PUB}/{token}/confirm", json=body)


# ------------------------------------------------------------------------------------------------------------------ F

def test_f_il_procedimento_completo(otp):
    o, b, posta = otp, otp["app"], otp["posta"]
    sheet = foglio(b)
    prima = _q(b, "SELECT count(*) FROM communication_messages")[0][0]
    key = str(uuid.uuid4())
    r = crea(o["owner"], sheet["id"], key=key)
    assert r.status_code == 200, r.text
    req = r.json()["request"]
    assert r.json()["created"] is True and req["provider"] == "stima360_otp" and req["otp"] is True and req["simulation"] is False
    assert req["status"] == "sent" and req["state"] == "sent" and req["state_label"] == "Inviato" and req["procedure"].startswith("S360-")
    assert [(s["role"], s["status"]) for s in req["signers"]] == [("visitor", "pending"), ("agent", "pending")]
    assert req["link"]["valid"] is True and req["link"]["email"]["status"] == "sent"
    assert "signing_url" not in json.dumps(req) and "secret" not in json.dumps(req)
    # idempotenza: la stessa chiave, la stessa richiesta, nessuna seconda email
    again = crea(o["owner"], sheet["id"], key=key)
    assert again.json()["created"] is False and again.json()["request"]["id"] == req["id"]
    assert len(posta.per("signature_link")) == 1
    msg = posta.per("signature_link", CLIENTE)[0]
    assert msg["communication_type"] == "service" and msg["subject_snapshot"] == "STIMA360 — Foglio visita da firmare"
    url, token = link_da(msg)
    assert url.startswith("https://test.invalid/firma/") and len(token) >= 40
    assert _q(b, "SELECT count(*) FROM signature_challenges WHERE kind = 'access' AND secret_hash = %s",
              (hashlib.sha256(token.encode()).hexdigest(),)) == [(1,)]                # solo l'impronta nel database
    assert _q(b, "SELECT count(*) FROM signature_challenges WHERE secret_hash LIKE %s", (f"%{token}%",)) == [(0,)]

    # il cliente apre la pagina: stato, documento, dichiarazione; nessuna firma per il solo fatto di aprire
    pub = o["public"]
    st = pub.get(f"{PUB}/{token}")
    assert st.status_code == 200 and st.headers["cache-control"] == "no-store"
    s = st.json()
    assert s["state"] == "ready" and s["can_request_otp"] and not s["can_confirm"] and s["document"]["sha256"] == sheet["sha256"]
    assert s["signer"]["email_masked"] == "c***@esempio.invalid" and CLIENTE not in json.dumps(s)
    assert s["consent"]["sha256"] in json.dumps(s) and "firma elettronica semplice" in s["consent"]["text"]
    assert _q(b, "SELECT status FROM signature_request_signers WHERE request_id = %s AND role = 'visitor'", (req["id"],)) == [("pending",)]
    pdf = pub.get(f"{PUB}/{token}/pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF") and hashlib.sha256(pdf.content).hexdigest() == sheet["sha256"]
    # confermare senza codice: no; richiedere il codice senza accettare... l'accettazione viaggia con la conferma
    assert _conferma(o, token, "123456").status_code == 409                                    # OTP_MISSING
    assert _conferma(o, token, "123456", consent_accepted=False).status_code == 400
    # il codice: generato, spedito, mai in chiaro nel database
    r = pub.post(f"{PUB}/{token}/otp")
    assert r.status_code == 200 and r.json()["state"] == "otp_sent" and r.json()["otp"]["attempts_left"] == 5
    otp_mail = posta.per("signature_otp", CLIENTE)[-1]
    code = codice_da(otp_mail)
    assert otp_mail["subject_snapshot"] == "STIMA360 — Il tuo codice di conferma"
    assert _q(b, "SELECT count(*) FROM signature_challenges WHERE kind = 'otp' AND secret_hash LIKE %s", (f"%{code}%",)) == [(0,)]
    assert _q(b, "SELECT count(*) FROM signature_evidence WHERE details::text LIKE %s", (f"%{code}%",)) == [(0,)]
    # lo stato nel CRM: in attesa di OTP
    crm = o["owner"].get(API, params={"visit_sheet_id": sheet["id"]}).json()["items"][0]
    assert crm["state"] == "awaiting_otp" and crm["otp_pending"]["attempts_left"] == 5
    # codice sbagliato: tentativo contato; poi quello giusto
    wrong = _conferma(o, token, "000000" if code != "000000" else "000001")
    assert wrong.status_code == 400 and wrong.json()["detail"]["code"] == "OTP_INVALID" and "4" in wrong.json()["detail"]["message"]
    ok = _conferma(o, token, code)
    assert ok.status_code == 200, ok.text
    assert ok.json()["state"] == "signed"
    assert _q(b, "SELECT status, signed_at IS NOT NULL FROM signature_request_signers WHERE request_id = %s AND role = 'visitor'",
              (req["id"],)) == [("signed", True)]
    assert _q(b, "SELECT consumed_at IS NOT NULL, attempts FROM signature_challenges WHERE kind = 'otp' AND request_id = %s", (req["id"],)) == [(True, 2)]
    # doppia conferma (doppio clic, refresh): nessun secondo effetto
    again = _conferma(o, token, code)
    assert again.status_code == 409 and again.json()["detail"]["code"] == "ALREADY_DONE"
    assert _q(b, "SELECT count(*) FROM signature_evidence WHERE request_id = %s AND event_type = 'signer_signed'", (req["id"],)) == [(1,)]
    # nel CRM: in attesa della firma dell'agente; il documento non e' ancora sottoscritto
    crm = o["owner"].get(API, params={"visit_sheet_id": sheet["id"]}).json()["items"][0]
    assert crm["state"] == "awaiting_agent" and crm["status"] == "sent" and crm["has_document"] is False
    # l'agente firma: solo l'agente assegnato, con password e dichiarazione
    corpo = {"password": o["password"], "consent_accepted": True, "consent_version": "dichiarazione_firma_otp_v2"}
    assert o["owner"].post(f"{API}/{req['id']}/sign-as-agent", json=corpo).status_code == 403            # non e' l'agente
    assert o["agent"].post(f"{API}/{req['id']}/sign-as-agent", json={**corpo, "password": "sbagliata"}).status_code == 403
    assert o["agent"].post(f"{API}/{req['id']}/sign-as-agent", json={**corpo, "consent_accepted": False}).status_code == 400
    done = o["agent"].post(f"{API}/{req['id']}/sign-as-agent", json=corpo)
    assert done.status_code == 200, done.text
    final = done.json()["request"]
    assert final["status"] == "signed" and final["state"] == "completed" and final["signature_level"] == "ses"
    assert final["signature_level_label"] == "Firma elettronica semplice (FES)" and final["valid_signature"] is True
    assert final["has_document"] and final["has_audit_trail"] and [s["status"] for s in final["signers"]] == ["signed", "signed"]
    # documento sottoscritto e rapporto: archiviati con le impronte, scaricabili, verificabili
    row = _q(b, "SELECT received_document_sha256, audit_trail_sha256, document_sha256 FROM signature_requests WHERE id = %s", (req["id"],))[0]
    doc = o["owner"].get(f"{API}/{req['id']}/document")
    assert doc.status_code == 200 and doc.content.startswith(b"%PDF") and hashlib.sha256(doc.content).hexdigest() == row[0]
    assert doc.headers["content-disposition"].startswith('attachment; filename="foglio-visita-sottoscritto-S360-')
    rep = o["owner"].get(f"{API}/{req['id']}/audit-trail")
    assert rep.status_code == 200 and rep.content.startswith(b"%PDF") and hashlib.sha256(rep.content).hexdigest() == row[1]
    assert row[2] == sheet["sha256"] and _q(b, "SELECT sha256 FROM visit_sheets WHERE id = %s", (sheet["id"],)) == [(sheet["sha256"],)]
    ver = o["owner"].get(f"{API}/{req['id']}/verify").json()
    assert ver["ok"] is True and [c["name"] for c in ver["checks"]] == ["catena delle prove", "foglio originale", "documento sottoscritto", "rapporto delle prove"]
    ev = o["owner"].get(f"{API}/{req['id']}/events").json()
    tipi = [e["event_type"] for e in ev["items"]]
    for atteso in ("request_created", "link_issued", "link_email_queued", "link_opened", "document_viewed", "otp_requested", "otp_email_queued",
                   "otp_failed", "otp_verified", "consent_recorded", "signer_signed", "agent_authenticated", "signature_completed", "documents_archived"):
        assert atteso in tipi, atteso
    assert ev["integrity"]["ok"] is True and tipi.index("link_opened") < tipi.index("otp_requested") < tipi.index("otp_verified")
    assert code not in json.dumps(ev) and token not in json.dumps(ev)
    # il cliente scarica il documento sottoscritto dal suo collegamento; la pagina dice «completato»
    assert pub.get(f"{PUB}/{token}").json()["state"] == "completed"
    signed = pub.get(f"{PUB}/{token}/signed.pdf")
    assert signed.status_code == 200 and hashlib.sha256(signed.content).hexdigest() == row[0]
    # il badge del foglio e le email: due sole (collegamento e codice), al solo cliente
    assert _q(b, "SELECT count(*) FROM communication_messages")[0][0] == prima + 2
    assert {m["destination_snapshot"] for m in posta.messaggi} == {CLIENTE}


def test_f_rifiuto_del_cliente(otp):
    o = otp
    sheet, req, token = _avvia(o)
    r = o["public"].post(f"{PUB}/{token}/decline", json={"reason": "Non sono d'accordo con il testo"})
    assert r.status_code == 200 and r.json()["state"] == "declined"
    crm = o["owner"].get(API, params={"visit_sheet_id": sheet["id"]}).json()["items"][0]
    assert crm["status"] == "declined" and crm["state"] == "declined" and crm["state_label"] == "Rifiutato"
    assert o["public"].post(f"{PUB}/{token}/otp").status_code == 409
    assert "Non sono" in json.dumps(o["owner"].get(f"{API}/{req['id']}/events").json())


def test_f_reinvio_copia_link_e_revoca(otp):
    o, b, posta = otp, otp["app"], otp["posta"]
    sheet, req, token1 = _avvia(o)
    # reinvio: nuovo collegamento, il precedente non vale piu'
    r = o["owner"].post(f"{API}/{req['id']}/links", json={"send": True})
    assert r.status_code == 200 and r.json()["sent"] is True and "url" not in r.json()
    _, token2 = link_da(posta.per("signature_link")[-1])
    assert token2 != token1
    assert o["public"].get(f"{PUB}/{token1}").json()["state"] == "link_revoked"
    assert o["public"].get(f"{PUB}/{token2}").json()["state"] == "ready"
    # copia link: il collegamento torna UNA volta, senza email
    n = len(posta.messaggi)
    r = o["owner"].post(f"{API}/{req['id']}/links", json={"send": False})
    assert r.status_code == 200 and r.json()["sent"] is False and r.json()["url"].startswith("https://test.invalid/firma/")
    assert len(posta.messaggi) == n and r.headers["cache-control"] == "no-store"
    token3 = r.json()["url"].rsplit("/", 1)[1]
    assert o["public"].get(f"{PUB}/{token2}").json()["state"] == "link_revoked"
    # limite dei reinvii
    for _ in range(10):
        last = o["owner"].post(f"{API}/{req['id']}/links", json={"send": False})
    assert last.status_code == 429
    # revoca: tutto chiuso, il collegamento non serve piu'
    r = o["owner"].post(f"{API}/{req['id']}/cancel")
    assert r.status_code == 200 and r.json()["state"] == "revoked"
    assert o["public"].get(f"{PUB}/{token3}").json()["state"] == "revoked"
    assert o["public"].post(f"{PUB}/{token3}/otp").status_code == 409
    assert _q(b, "SELECT count(*) FROM signature_challenges WHERE request_id = %s AND invalidated_at IS NULL", (req["id"],)) == [(0,)]
    # dopo la revoca si puo' creare un'altra richiesta per lo stesso foglio
    assert crea(o["owner"], sheet["id"]).status_code == 200


# ------------------------------------------------------------------------------------------------------------------ N

def test_n_codici_errati_scaduti_riusati_e_troppi_tentativi(otp):
    o, b = otp, otp["app"]
    sheet, req, token = _avvia(o)
    code = _richiedi_codice(o, token)
    r = o["public"].post(f"{PUB}/{token}/otp")
    assert r.status_code == 429 and r.json()["detail"]["code"] == "OTP_TOO_SOON"       # attesa minima fra due codici
    cid = _q(b, "SELECT id FROM signature_challenges WHERE kind = 'otp' AND request_id = %s", (req["id"],))[0][0]
    # scaduto
    _q(b, "SET session_replication_role = replica; UPDATE signature_challenges SET created_at = NOW() - interval '20 minutes', "
          "expires_at = NOW() - interval '1 second' WHERE id = %s", (cid,))
    r = _conferma(o, token, code)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "OTP_EXPIRED"
    assert _q(b, "SELECT invalidation_reason FROM signature_challenges WHERE id = %s", (cid,)) == [("expired",)]
    assert _conferma(o, token, code).json()["detail"]["code"] == "OTP_MISSING"          # non si riusa
    # troppi tentativi
    _q(b, "SET session_replication_role = replica; UPDATE signature_challenges SET created_at = created_at - interval '2 minutes' WHERE kind = 'otp'")
    code2 = _richiedi_codice(o, token)
    wrong = "000000" if code2 != "000000" else "000001"
    esiti = [_conferma(o, token, wrong) for _ in range(5)]
    assert [e.status_code for e in esiti[:4]] == [400] * 4 and esiti[4].status_code == 409
    assert esiti[4].json()["detail"]["code"] == "OTP_LOCKED"
    assert _conferma(o, token, code2).json()["detail"]["code"] == "OTP_MISSING"        # il codice giusto, ormai invalidato, non vale
    assert _q(b, "SELECT attempts, invalidation_reason FROM signature_challenges WHERE kind = 'otp' AND request_id = %s ORDER BY id DESC LIMIT 1",
              (req["id"],)) == [(5, "too_many_attempts")]
    assert _q(b, "SELECT status FROM signature_request_signers WHERE request_id = %s AND role = 'visitor'", (req["id"],)) == [("pending",)]
    # un nuovo codice funziona; il vecchio no
    _q(b, "SET session_replication_role = replica; UPDATE signature_challenges SET created_at = created_at - interval '2 minutes' WHERE kind = 'otp'")
    code3 = _richiedi_codice(o, token)
    assert _conferma(o, token, code2).json()["detail"]["code"] == "OTP_INVALID"
    assert _conferma(o, token, code3).status_code == 200


def test_n_troppe_richieste_di_codice(otp, monkeypatch):
    o, b = otp, otp["app"]
    monkeypatch.setenv("ESIGN_OTP_RESEND_SECONDS", "1")
    monkeypatch.setenv("ESIGN_OTP_MAX_PER_HOUR_SIGNER", "3")
    sheet, req, token = _avvia(o)
    for _ in range(3):
        _q(b, "SET session_replication_role = replica; UPDATE signature_challenges SET created_at = created_at - interval '2 minutes' WHERE kind = 'otp'")
        assert o["public"].post(f"{PUB}/{token}/otp").status_code == 200
    _q(b, "SET session_replication_role = replica; UPDATE signature_challenges SET created_at = created_at - interval '2 minutes' WHERE kind = 'otp'")
    r = o["public"].post(f"{PUB}/{token}/otp")
    assert r.status_code == 429 and r.json()["detail"]["code"] == "OTP_RATE_LIMITED"
    assert len(o["posta"].per("signature_otp")) == 3
    # i codici precedenti sono stati invalidati dal successivo: ne vale uno solo
    assert _q(b, "SELECT count(*) FROM signature_challenges WHERE kind = 'otp' AND request_id = %s AND invalidated_at IS NULL", (req["id"],)) == [(1,)]


def test_n_collegamento_scaduto_sconosciuto_e_cliente_diverso(otp):
    o, b = otp, otp["app"]
    sheet, req, token = _avvia(o)
    pub = o["public"]
    assert pub.get(f"{PUB}/{token[:-1]}x").status_code == 404                              # token sconosciuto: neutro
    assert pub.get(f"{PUB}/abc").status_code in (404, 422)
    assert pub.post(f"{PUB}/{token[:-2]}zz/otp").status_code == 404
    # scadenza della richiesta: il collegamento non vale piu', lo stato diventa scaduto, nessun codice
    _q(b, "SET session_replication_role = replica; UPDATE signature_requests SET expires_at = NOW() - interval '1 minute' WHERE id = %s", (req["id"],))
    assert pub.get(f"{PUB}/{token}").json()["state"] == "expired"
    assert pub.post(f"{PUB}/{token}/otp").status_code == 409
    crm = o["owner"].get(API, params={"visit_sheet_id": sheet["id"]}).json()["items"][0]
    assert crm["status"] == "expired" and crm["state_label"] == "Scaduto"
    assert "request_expired" in [e["event_type"] for e in o["owner"].get(f"{API}/{req['id']}/events").json()["items"]]
    # un altro cliente (altro foglio, altro collegamento) non puo' firmare questo documento con il proprio token
    sheet2, req2, token2 = _avvia(o)
    code2 = _richiedi_codice(o, token2)
    assert _conferma(o, token, code2).status_code == 409                                    # il primo e' scaduto
    s2 = pub.get(f"{PUB}/{token2}").json()
    assert s2["document"]["sha256"] == sheet2["sha256"] != sheet["sha256"]                 # ogni token vede SOLO il proprio documento


# SENTINELLA AGGIORNATA (correzione urgente FIRMA OTP): la whitelist dei destinatari non esiste piu'; restano il rifiuto
# di un indirizzo non valido e il blocco quando la firma OTP non e' configurata.
def test_n_senza_configurazione_o_con_indirizzo_non_valido_non_parte_nulla(otp, monkeypatch):
    o, b = otp, otp["app"]
    sheet = foglio(b)
    prima = _q(b, "SELECT count(*) FROM signature_requests")
    assert crea(o["owner"], sheet["id"], email="3331234567").status_code == 400
    assert crea(o["owner"], sheet["id"], email="non-una-email").status_code == 400
    assert crea(o["owner"], sheet["id"], email="").status_code == 422
    monkeypatch.delenv("ESIGN_OTP_PEPPER")
    r = crea(o["owner"], sheet["id"])
    assert r.status_code == 503 and r.json()["detail"]["code"] == "OTP_NOT_CONFIGURED"
    assert _q(b, "SELECT count(*) FROM signature_requests") == prima and not o["posta"].messaggi
    cfg = o["owner"].get(f"{API}/config/provider").json()
    assert cfg["otp"] is True and cfg["otp_ready"] is False and "ESIGN_OTP_PEPPER" in cfg["otp_problem"]
    assert "recipients_restricted" not in cfg


def test_f_un_indirizzo_mai_autorizzato_riceve_link_e_codice_e_firma(otp, monkeypatch):
    """Correzione urgente: nessuna whitelist. Un indirizzo mai elencato riceve il collegamento e il codice e firma,
    anche se sull'ambiente e' rimasta impostata la vecchia variabile con altri indirizzi (ora ignorata)."""
    o, b, posta = otp, otp["app"], otp["posta"]
    monkeypatch.setenv("ESIGN_ALLOWED_RECIPIENTS", "solo.questo@esempio.invalid")       # residuo: non deve contare
    nuovo = f"cliente.nuovo.{uuid.uuid4().hex[:8]}@dominio-mai-visto.example"
    sheet, req, token = _avvia(o, email=nuovo)
    assert req["status"] == "sent" and req["link"]["email"]["status"] == "sent"
    assert [s["email"] for s in req["signers"] if s["role"] == "visitor"] == [nuovo]
    link = posta.per("signature_link", nuovo)
    assert len(link) == 1 and link[0]["destination_snapshot"] == nuovo
    assert o["public"].get(f"{PUB}/{token}").json()["state"] == "ready"
    r = o["public"].post(f"{PUB}/{token}/otp")
    assert r.status_code == 200, r.text
    otp_mail = posta.per("signature_otp", nuovo)
    assert len(otp_mail) == 1
    ok = _conferma(o, token, codice_da(otp_mail[0]))
    assert ok.status_code == 200 and ok.json()["state"] == "signed", ok.text
    assert _q(b, "SELECT status FROM signature_request_signers WHERE request_id = %s AND role = 'visitor'", (req["id"],)) == [("signed",)]
    assert not posta.per("signature_link", "solo.questo@esempio.invalid")
    eventi = [e["event_type"] for e in o["owner"].get(f"{API}/{req['id']}/events").json()["items"]]
    assert "signer_signed" in eventi


def test_n_smtp_guasto_resta_tracciato_e_si_puo_reinviare(otp):
    o, b, posta = otp, otp["app"], otp["posta"]
    sheet = foglio(b)
    posta.rifiuta = True
    r = crea(o["owner"], sheet["id"])
    assert r.status_code == 200
    req = r.json()["request"]
    assert req["state"] == "error" and req["link"]["email"]["status"] == "failed" and req["link"]["email"]["error_code"] == "provider_unavailable"
    tipi = [e["event_type"] for e in o["owner"].get(f"{API}/{req['id']}/events").json()["items"]]
    assert "link_email_queued" in tipi and "link_opened" not in tipi
    posta.rifiuta = False
    r = o["owner"].post(f"{API}/{req['id']}/links", json={"send": True})
    assert r.status_code == 200 and r.json()["request"]["state"] == "sent"


def test_n_due_conferme_contemporanee_una_sola_firma(otp):
    o, b = otp, otp["app"]
    sheet, req, token = _avvia(o)
    code = _richiedi_codice(o, token)
    esiti = []

    def prova():
        esiti.append(_conferma(o, token, code).status_code)
    t1, t2 = threading.Thread(target=prova), threading.Thread(target=prova)
    t1.start(); t2.start(); t1.join(); t2.join()
    assert sorted(esiti) == [200, 409]
    assert _q(b, "SELECT count(*) FROM signature_evidence WHERE request_id = %s AND event_type = 'signer_signed'", (req["id"],)) == [(1,)]
    assert _q(b, "SELECT count(*) FROM signature_challenges WHERE kind = 'otp' AND request_id = %s AND consumed_at IS NOT NULL", (req["id"],)) == [(1,)]


def test_n_due_richieste_insieme_per_lo_stesso_foglio(otp):
    o, b = otp, otp["app"]
    sheet = foglio(b)
    esiti = []

    def prova():
        esiti.append(crea(o["owner"], sheet["id"]).status_code)
    t1, t2 = threading.Thread(target=prova), threading.Thread(target=prova)
    t1.start(); t2.start(); t1.join(); t2.join()
    assert sorted(esiti) == [200, 409]
    assert _q(b, "SELECT count(*) FROM signature_requests WHERE visit_sheet_id = %s AND status = 'sent'", (sheet["id"],)) == [(1,)]


def test_n_permessi_e_isolamento(otp):
    o, b = otp, otp["app"]
    sheet, req, token = _avvia(o)
    for who in ("agent2_a", "owner_b"):
        c = b["login"](who)
        for method, path in (("get", API + f"?visit_sheet_id={sheet['id']}"), ("post", f"{API}/{req['id']}/cancel"),
                             ("post", f"{API}/{req['id']}/links"), ("post", f"{API}/{req['id']}/sign-as-agent"),
                             ("get", f"{API}/{req['id']}/document"), ("get", f"{API}/{req['id']}/audit-trail"),
                             ("get", f"{API}/{req['id']}/events"), ("get", f"{API}/{req['id']}/verify")):
            body = {"send": False} if path.endswith("/links") else ({"password": "x", "consent_accepted": True, "consent_version": "dichiarazione_firma_otp_v2"}
                                                                   if path.endswith("sign-as-agent") else None)
            r = getattr(c, method)(path, json=body) if method == "post" else c.get(path)
            assert r.status_code == 404, (who, path, r.status_code)
    anon = o["public"]
    assert anon.get(API, params={"visit_sheet_id": sheet["id"]}).status_code == 401
    assert anon.get(f"{API}/{req['id']}/document").status_code == 401
    assert anon.get(f"{API}/config/provider").status_code == 401
    # il token del cliente non apre nulla del CRM e non vede altri documenti
    assert anon.get(f"{API}/{req['id']}/events", headers={"Authorization": token}).status_code == 401


def test_n_alterazione_del_pdf_archiviato_e_delle_prove_viene_rilevata(otp):
    o, b = otp, otp["app"]
    sheet, req, token = _avvia(o)
    code = _richiedi_codice(o, token)
    assert _conferma(o, token, code).status_code == 200
    assert o["agent"].post(f"{API}/{req['id']}/sign-as-agent", json={"password": o["password"], "consent_accepted": True,
                                                                     "consent_version": "dichiarazione_firma_otp_v2"}).status_code == 200
    assert o["owner"].get(f"{API}/{req['id']}/verify").json()["ok"] is True
    # le prove sono append-only e la catena/sigillo scoprono una manomissione fatta a basso livello
    import psycopg2
    with pytest.raises(psycopg2.Error):
        _q(b, "UPDATE signature_evidence SET details = '{}'::jsonb WHERE request_id = %s AND seq = 3", (req["id"],))
    with pytest.raises(psycopg2.Error):
        _q(b, "DELETE FROM signature_evidence WHERE request_id = %s", (req["id"],))
    _q(b, "SET session_replication_role = replica; UPDATE signature_evidence SET details = details || '{\"x\": 1}'::jsonb WHERE request_id = %s AND seq = 3",
       (req["id"],))
    ver = o["owner"].get(f"{API}/{req['id']}/verify").json()
    assert ver["ok"] is False and any("voce 3" in c["detail"] for c in ver["checks"] if c["name"] == "catena delle prove")
    # il documento archiviato alterato non corrisponde alla sua impronta: il download viene rifiutato
    with pytest.raises(psycopg2.Error):                                                   # il database protegge la riga chiusa
        _q(b, "UPDATE signature_requests SET received_document = %s WHERE id = %s", (b"%PDF-altro", req["id"]))
    _q(b, "SET session_replication_role = replica; UPDATE signature_requests SET received_document = %s WHERE id = %s", (b"%PDF-altro", req["id"]))
    assert o["owner"].get(f"{API}/{req['id']}/document").status_code == 400
    assert o["owner"].get(f"{API}/{req['id']}/verify").json()["checks"][2]["ok"] is False
    # il foglio originale alterato
    _q(b, "SET session_replication_role = replica; UPDATE visit_sheets SET pdf_bytes = %s WHERE id = %s", (b"%PDF-x", sheet["id"]))
    assert o["owner"].get(f"{API}/{req['id']}/verify").json()["checks"][1]["ok"] is False


def test_n_la_firma_dell_agente_ha_le_sue_regole(otp):
    o, b = otp, otp["app"]
    sheet, req, token = _avvia(o)
    corpo = {"password": o["password"], "consent_accepted": True, "consent_version": "dichiarazione_firma_otp_v2"}
    # l'agente puo' firmare anche prima del cliente; la richiesta resta aperta finche' manca il cliente
    r = o["agent"].post(f"{API}/{req['id']}/sign-as-agent", json=corpo)
    assert r.status_code == 200 and r.json()["request"]["status"] == "sent" and r.json()["request"]["signers"][1]["status"] == "signed"
    assert o["agent"].post(f"{API}/{req['id']}/sign-as-agent", json=corpo).status_code == 409           # non due volte
    # versione della dichiarazione sbagliata; password sbagliata lascia traccia
    assert o["agent"].post(f"{API}/{req['id']}/sign-as-agent", json={**corpo, "consent_version": "altra"}).status_code == 400
    tipi = [e["event_type"] for e in o["owner"].get(f"{API}/{req['id']}/events").json()["items"]]
    assert "agent_authenticated" in tipi and "agent_auth_failed" not in tipi
    # senza agente: la richiesta si completa con il solo cliente
    sheet2 = foglio(b)
    r = crea(o["owner"], sheet2["id"], include_agent=False)
    assert r.status_code == 200 and [s["role"] for s in r.json()["request"]["signers"]] == ["visitor"]
    _, token2 = link_da(o["posta"].per("signature_link")[-1])
    code = _richiedi_codice(o, token2)
    assert _conferma(o, token2, code).json()["state"] == "completed"
    assert o["agent"].post(f"{API}/{r.json()['request']['id']}/sign-as-agent", json=corpo).status_code == 409


def test_n_il_database_protegge_sfide_e_prove(otp):
    o, b = otp, otp["app"]
    sheet, req, token = _avvia(o)
    import psycopg2
    cid = _q(b, "SELECT id FROM signature_challenges WHERE request_id = %s", (req["id"],))[0][0]
    for sql in ("DELETE FROM signature_challenges WHERE id = %s", "UPDATE signature_challenges SET secret_hash = 'x' WHERE id = %s",
                "UPDATE signature_challenges SET expires_at = NOW() + interval '1 year' WHERE id = %s",
                "UPDATE signature_challenges SET attempts = attempts - 1 WHERE id = %s"):
        with pytest.raises(psycopg2.Error):
            _q(b, sql, (cid,))
    _q(b, "UPDATE signature_challenges SET invalidated_at = NOW(), invalidation_reason = 'prova' WHERE id = %s", (cid,))
    with pytest.raises(psycopg2.Error):                                                   # chiusa: non si riapre
        _q(b, "UPDATE signature_challenges SET invalidated_at = NULL, invalidation_reason = NULL WHERE id = %s", (cid,))
    with pytest.raises(psycopg2.Error):                                                   # un'altra agenzia non puo' agganciarsi
        _q(b, "INSERT INTO signature_evidence (request_id, agency_id, seq, event_type, occurred_at, actor_kind, entry_hash, seal) "
              "VALUES (%s, %s, 99, 'x', NOW(), 'system', repeat('a', 64), repeat('b', 64))", (req["id"], b["ids"]["b"]))
