"""FIRMA SEMPLIFICATA e FOGLI VISITA - le API con PostgreSQL vero (banco 2C) e il trasporto email FINTO.

«Invia per firma» in un gesto (foglio generato o riusato, richiesta, collegamento, firma dell'agente facoltativa e
sempre autenticata), dati precompilati dal CRM senza invenzioni, stati per l'operatore, sezione Fogli visita (ricerca,
filtri, visibilita'), avvisi per «Oggi», dichiarazione v2 legata al documento nelle prove, vecchie dichiarazioni e
vecchi documenti intatti. Il motore OTP e' quello di sempre: qui si prova che il nuovo percorso lo usa e non lo aggira.
"""
from __future__ import annotations

import threading
import uuid

import pytest

from esign import consent
from tests.firma_otp_support import API, CLIENTE, PUB, codice_da, link_da
from tests.test_blocco_2c_integrated_postgres import CLUSTER_VERIFIED, DSN, PASSWORD, _q, app, banco  # noqa: F401
from tests.test_blocco_5a_visit_booking_postgres import _property
from tests.test_blocco_6a_visit_sheets_postgres import _visit
from tests.test_firma_otp_postgres import _conferma, otp  # noqa: F401

pytestmark = [
    pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL locale"),
    pytest.mark.skipif(not CLUSTER_VERIFIED, reason="cluster usa-e-getta non dichiarato verificato "
                       "(BLOCCO_2C_TEST_CLUSTER_VERIFIED=1)"),
]
SEND = f"{API}/send"
SHEETS = "/api/visit-sheets"


def _visita(b, *, agent="agent_a", email=CLIENTE, nome="Mario Prefill", completata=True):
    aid = _visit(b, _property(b), agent=agent)
    _q(b, "UPDATE contacts SET display_name = %s, email = %s WHERE id = (SELECT contact_id FROM appointments WHERE id = %s)",
       (nome, email, aid))
    if completata:
        _q(b, "SET session_replication_role = replica; UPDATE appointments SET status = 'completed', "
              "completed_at = start_at + interval '50 minutes' WHERE id = %s", (aid,))
    return aid


def _invia(client, aid, *, email=CLIENTE, nome="Mario Prefill", key=None, known=None, agent_sign=None):
    corpo = {"appointment_id": aid, "known_sheet_id": known, "signer_name": nome, "signer_contact": email,
             "idempotency_key": key or str(uuid.uuid4())}
    if agent_sign is not None:
        corpo["agent_sign"] = agent_sign
    return client.post(SEND, json=corpo)


def _firma_agente(password=PASSWORD):
    return {"password": password, "consent_accepted": True, "consent_version": consent.VERSION}


def _cliente_firma(o, email=CLIENTE):
    token = link_da(o["posta"].per("signature_link", email)[-1])[1]
    assert o["public"].post(f"{PUB}/{token}/otp").status_code == 200
    code = codice_da(o["posta"].per("signature_otp", email)[-1])
    r = _conferma(o, token, code, consent_version=consent.VERSION)
    assert r.status_code == 200, r.text
    return token, r.json()


def _stato(client, aid):
    data = client.get(f"{SHEETS}/appointments/{aid}").json()
    return data, (data["sheets"][0]["signature"] or {}).get("state")


# ------------------------------------------------------------------------------------------------------- precompilati

def test_precompilati_dal_crm_senza_inventare(otp):
    o, b = otp, otp["app"]
    aid = _visita(b)
    data = o["agent"].get(f"{SHEETS}/appointments/{aid}").json()
    assert data["signer_suggestion"] == {"name": "Mario Prefill", "email": CLIENTE}
    assert data["agent"] == {"name": "Aldo Alfa", "is_me": True}
    assert data["sheets"] == [] and data["client"]["name"] == "Mario Prefill"
    assert o["owner"].get(f"{SHEETS}/appointments/{aid}").json()["agent"]["is_me"] is False
    senza = _visita(b, email=None)
    vuoto = o["agent"].get(f"{SHEETS}/appointments/{senza}").json()["signer_suggestion"]
    assert vuoto == {"name": "Mario Prefill", "email": ""}                 # nessuna email inventata


# ------------------------------------------------------------------------------------------ un gesto, foglio e invio

def test_invia_genera_il_foglio_se_manca_e_lo_riusa_se_uguale(otp):
    o, b = otp, otp["app"]
    aid = _visita(b)
    r = _invia(o["agent"], aid)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["sheet_created"] is True and out["created"] is True and out["request"]["ui"]["state"] == "awaiting_client"
    assert "accettata dal server di posta" in out["request"]["ui"]["note"] and "consegnat" not in out["request"]["ui"]["note"]
    assert len(o["posta"].per("signature_link", CLIENTE)) == 1
    assert _q(b, "SELECT count(*) FROM visit_sheets WHERE appointment_id = %s", (aid,)) == [(1,)]
    # annullata, si reinvia: stesso foglio, nessuna nuova versione
    assert o["agent"].post(f"{API}/{out['request']['id']}/cancel").status_code == 200
    di_nuovo = _invia(o["agent"], aid, known=out["sheet"]["id"]).json()
    assert di_nuovo["sheet_created"] is False and di_nuovo["sheet"]["id"] == out["sheet"]["id"]
    assert _q(b, "SELECT count(*) FROM visit_sheets WHERE appointment_id = %s", (aid,)) == [(1,)]


def test_dati_cambiati_si_ferma_e_chiede_di_controllare_la_nuova_versione(otp):
    o, b = otp, otp["app"]
    aid = _visita(b, completata=False)
    v1 = o["agent"].post(f"{SHEETS}/appointments/{aid}").json()["sheet"]
    _q(b, "SET session_replication_role = replica; UPDATE appointments SET status = 'completed', "
          "completed_at = start_at + interval '50 minutes' WHERE id = %s", (aid,))
    r = _invia(o["agent"], aid, known=v1["id"])
    assert r.status_code == 409 and r.json()["detail"]["code"] == "SHEET_UPDATED"
    assert not o["posta"].messaggi and _q(b, "SELECT count(*) FROM signature_requests r JOIN visit_sheets s ON s.id = r.visit_sheet_id "
                                             "WHERE s.appointment_id = %s", (aid,)) == [(0,)]
    v2 = o["agent"].get(f"{SHEETS}/appointments/{aid}").json()["sheets"][0]
    assert v2["version"] == 2
    assert _invia(o["agent"], aid, known=v2["id"]).status_code == 200


def test_dati_obbligatori_verificati_prima_di_tutto(otp):
    o, b = otp, otp["app"]
    aid = _visita(b)
    assert _invia(o["agent"], aid, email="non-una-email").status_code == 400
    assert _invia(o["agent"], aid, nome="   ").status_code == 400
    assert o["agent"].post(SEND, json={"appointment_id": aid, "signer_name": "x", "signer_contact": CLIENTE}).status_code == 422
    assert not o["posta"].messaggi
    assert _q(b, "SELECT count(*) FROM visit_sheets WHERE appointment_id = %s", (aid,)) == [(0,)]       # niente generato a vuoto


def test_richiesta_gia_aperta_doppio_clic_e_concorrenza(otp):
    o, b = otp, otp["app"]
    aid = _visita(b)
    chiave = str(uuid.uuid4())
    primo = _invia(o["agent"], aid, key=chiave)
    secondo = _invia(o["agent"], aid, key=chiave)                       # doppio clic: la stessa richiesta
    assert primo.status_code == secondo.status_code == 200
    assert secondo.json()["created"] is False and secondo.json()["request"]["id"] == primo.json()["request"]["id"]
    altro = _invia(o["owner"], aid)                                     # un altro operatore, altra chiave
    assert altro.status_code == 409 and altro.json()["detail"]["code"] == "REQUEST_OPEN"
    assert len(o["posta"].per("signature_link", CLIENTE)) == 1
    # due invii davvero contemporanei su un'altra visita: una sola richiesta
    aid2 = _visita(b)
    esiti = []
    t = [threading.Thread(target=lambda: esiti.append(_invia(b["login"]("agent_a"), aid2).status_code)) for _ in range(2)]
    for x in t:
        x.start()
    for x in t:
        x.join()
    assert sorted(esiti) in ([200, 409], [200, 200]) and esiti.count(200) >= 1
    assert _q(b, "SELECT count(*) FROM signature_requests r JOIN visit_sheets s ON s.id = r.visit_sheet_id "
                 "WHERE s.appointment_id = %s AND r.status = 'sent'", (aid2,)) == [(1,)]


# ------------------------------------------------------------------------------------------------- firma dell'agente

def test_firmo_anche_io_adesso_password_verificata_prima_di_inviare(otp):
    o, b = otp, otp["app"]
    aid = _visita(b)
    sbagliata = _invia(o["agent"], aid, agent_sign=_firma_agente("sbagliata"))
    assert sbagliata.status_code == 403 and "non è stato inviato nulla" in sbagliata.json()["detail"]["message"]
    assert not o["posta"].messaggi
    assert _q(b, "SELECT count(*) FROM visit_sheets WHERE appointment_id = %s", (aid,)) == [(0,)]
    senza_consenso = _invia(o["agent"], aid, agent_sign={**_firma_agente(), "consent_accepted": False})
    assert senza_consenso.status_code == 400 and not o["posta"].messaggi
    non_agente = _invia(o["owner"], aid, agent_sign=_firma_agente())    # il titolare non e' l'agente della visita
    assert non_agente.status_code == 403 and non_agente.json()["detail"]["code"] == "NOT_THE_AGENT"
    ok = _invia(o["agent"], aid, agent_sign=_firma_agente()).json()
    assert ok["agent_signed"] is True and ok["agent_error"] is None
    assert ok["request"]["ui"]["state"] == "awaiting_client" and "agente è già registrata" in ok["request"]["ui"]["note"]
    rid = ok["request"]["id"]
    consenso = _q(b, "SELECT details FROM signature_evidence WHERE request_id = %s AND event_type = 'consent_recorded' "
                     "AND actor_kind = 'operator'", (rid,))[0][0]
    sheet_sha = _q(b, "SELECT sha256 FROM visit_sheets WHERE appointment_id = %s", (aid,))[0][0]
    assert consenso["consent_version"] == consent.VERSION and consenso["document_sha256"] == sheet_sha
    # il cliente firma: documento completo subito
    token, stato = _cliente_firma(o)
    assert stato["state"] == "completed" and stato["can_download_signed"] is True
    data, ui = _stato(o["agent"], aid)
    assert ui == "completed" and data["sheets"][0]["signature"]["has_document"] is True
    assert o["agent"].get(f"{API}/{rid}/verify").json()["ok"] is True
    assert o["public"].get(f"{PUB}/{token}/signed.pdf").content.startswith(b"%PDF")


def test_agente_firma_dopo_il_cliente(otp):
    o, b = otp, otp["app"]
    aid = _visita(b)
    rid = _invia(o["agent"], aid).json()["request"]["id"]
    _, stato = _cliente_firma(o)
    assert stato["state"] == "signed" and stato["can_download_signed"] is False      # mai «concluso» prima del tempo
    assert _stato(o["agent"], aid)[1] == "awaiting_agent"
    r = o["agent"].post(f"{API}/{rid}/sign-as-agent", json=_firma_agente())
    assert r.status_code == 200 and r.json()["request"]["ui"]["state"] == "completed"
    assert _q(b, "SELECT count(*) FROM signature_evidence WHERE request_id = %s AND event_type = 'documents_archived'", (rid,)) == [(1,)]


# ------------------------------------------------------------------------------------------------- stati e avvisi

def test_invio_non_riuscito_rifiuto_e_avvisi_senza_doppioni(otp):
    o, b = otp, otp["app"]
    o["posta"].rifiuta = True
    fallita = _visita(b)
    out = _invia(o["agent"], fallita).json()
    assert out["request"]["ui"]["state"] == "send_failed" and out["request"]["ui"]["label"] == "Invio non riuscito"
    o["posta"].rifiuta = False
    assert o["agent"].post(f"{API}/{out['request']['id']}/links", json={"send": True}).status_code == 200
    assert _stato(o["agent"], fallita)[1] == "awaiting_client"
    # rifiuto del cliente
    rifiutata = _visita(b, email="rifiuta@esempio.invalid")
    _invia(o["agent"], rifiutata, email="rifiuta@esempio.invalid")
    token = link_da(o["posta"].per("signature_link", "rifiuta@esempio.invalid")[-1])[1]
    assert o["public"].post(f"{PUB}/{token}/decline", json={"reason": None}).status_code == 200
    assert _stato(o["agent"], rifiutata)[1] == "declined"
    # scaduta e poi reinviata e completata: UN avviso per visita, quello vero (completato)
    doppia = _visita(b, email="doppia@esempio.invalid")
    prima = _invia(o["agent"], doppia, email="doppia@esempio.invalid", agent_sign=_firma_agente()).json()["request"]["id"]
    _q(b, "SET session_replication_role = replica; UPDATE signature_requests SET expires_at = NOW() - interval '1 minute' "
          "WHERE id = %s", (prima,))
    assert _stato(o["agent"], doppia)[1] == "expired"
    _invia(o["agent"], doppia, email="doppia@esempio.invalid", agent_sign=_firma_agente())
    _cliente_firma(o, "doppia@esempio.invalid")
    avvisi = o["agent"].get(f"{API}/notifications").json()["items"]
    per_visita = {}
    for a in avvisi:
        per_visita.setdefault(a["appointment_id"], []).append(a["state"])
    assert per_visita[rifiutata] == ["declined"] and per_visita[doppia] == ["completed"]
    assert fallita not in per_visita                                      # reinviata: non e' piu' un problema
    # l'altro agente non vede gli avvisi delle visite altrui
    assert b["login"]("agent2_a").get(f"{API}/notifications").json()["items"] == []


# ------------------------------------------------------------------------------------------------- sezione Fogli visita

def test_overview_ricerca_filtri_visibilita_e_isolamento(otp):
    o, b = otp, otp["app"]
    mia = _visita(b, nome="Giulia Ricercabile", email="giulia@esempio.invalid")
    sua = _visita(b, agent="agent2_a", nome="Paolo Altro", email="paolo@esempio.invalid")
    for aid in (mia, sua):
        assert o["owner"].post(f"{SHEETS}/appointments/{aid}").status_code == 200
    _invia(o["agent"], mia, email="giulia@esempio.invalid", nome="Giulia Ricercabile", agent_sign=_firma_agente())
    _cliente_firma(o, "giulia@esempio.invalid")
    tutti = o["owner"].get(f"{SHEETS}/overview").json()
    ids = {i["appointment_id"] for i in tutti["items"]}
    assert {mia, sua} <= ids and tutti["agents"]
    riga = next(i for i in tutti["items"] if i["appointment_id"] == mia)
    assert riga["signature"]["state"] == "completed" and riga["signature"]["list_label"] == "Completato"
    assert riga["signature"]["signer_name"] == "Giulia Ricercabile" and riga["signature"]["signed_at"]
    assert riga["client_name"] == "Giulia Ricercabile" and riga["agent_name"] == "Aldo Alfa" and riga["property_address"]
    sua_riga = next(i for i in tutti["items"] if i["appointment_id"] == sua)
    assert sua_riga["signature"]["state"] == "to_send" and sua_riga["signature"]["list_label"] == "Da firmare"
    # una visita ancora senza foglio c'e' anche lei, «Da firmare»: si fa firmare da qui
    nuda = _visita(b, nome="Senza Foglio", email="nuda@esempio.invalid")
    riga_nuda = next(i for i in o["owner"].get(f"{SHEETS}/overview").json()["items"] if i["appointment_id"] == nuda)
    assert riga_nuda["has_sheet"] is False and riga_nuda["signature"]["list_label"] == "Da firmare"
    assert riga_nuda["last_activity_at"] is None and riga_nuda["client_name"] == "Senza Foglio"
    assert [i["appointment_id"] for i in o["owner"].get(f"{SHEETS}/overview", params={"q": "ricercab"}).json()["items"]] == [mia]
    completati = o["owner"].get(f"{SHEETS}/overview", params={"state": "completed"}).json()["items"]
    assert mia in {i["appointment_id"] for i in completati} and sua not in {i["appointment_id"] for i in completati}
    per_agente = o["owner"].get(f"{SHEETS}/overview", params={"agent_id": b["ids"]["agent2_a"]}).json()["items"]
    assert {i["appointment_id"] for i in per_agente} >= {sua} and mia not in {i["appointment_id"] for i in per_agente}
    futuro = o["owner"].get(f"{SHEETS}/overview", params={"from": "2099-01-01T00:00:00+00:00"}).json()
    assert futuro["items"] == [] and futuro["total"] == 0
    # l'agente vede solo le sue visite e non riceve l'elenco degli agenti
    mie = o["agent"].get(f"{SHEETS}/overview").json()
    assert mia in {i["appointment_id"] for i in mie["items"]} and sua not in {i["appointment_id"] for i in mie["items"]}
    assert mie["agents"] == []
    # un'altra agenzia non vede nulla e non tocca nulla
    estranea = b["login"]("owner_b")
    assert estranea.get(f"{SHEETS}/overview").json()["items"] == []
    assert estranea.get(f"{SHEETS}/appointments/{mia}").status_code == 404
    assert _invia(estranea, sua).status_code == 404
    assert estranea.get(f"{API}/notifications").json()["items"] == []


# ------------------------------------------------------------------------------------- dichiarazioni e vecchi documenti

def test_dichiarazione_v2_senza_impronta_e_vecchie_versioni_intatte(otp, monkeypatch):
    o, b = otp, otp["app"]
    aid = _visita(b)
    rid = _invia(o["agent"], aid).json()["request"]["id"]
    token = link_da(o["posta"].per("signature_link", CLIENTE)[-1])[1]
    stato = o["public"].get(f"{PUB}/{token}").json()
    sha = _q(b, "SELECT sha256 FROM visit_sheets WHERE appointment_id = %s", (aid,))[0][0]
    assert stato["consent"]["version"] == "dichiarazione_firma_otp_v2" and sha not in stato["consent"]["text"]
    assert "questa versione del documento" in stato["consent"]["text"] and "Via Segreta" in stato["consent"]["text"]
    assert f"n. {aid}" not in stato["consent"]["text"] and "versione 1" in stato["consent"]["text"]   # nessun id interno
    # un cliente che aveva accettato la v1 (pagina aperta prima del rilascio): la v1 resta la SUA dichiarazione
    monkeypatch.setattr(consent, "VERSION", consent.VERSION_V1)
    assert o["public"].post(f"{PUB}/{token}/otp").status_code == 200
    code = codice_da(o["posta"].per("signature_otp", CLIENTE)[-1])
    assert _conferma(o, token, code, consent_version=consent.VERSION_V1).status_code == 200
    monkeypatch.setattr(consent, "VERSION", consent.VERSION_V2)
    assert o["agent"].post(f"{API}/{rid}/sign-as-agent", json=_firma_agente()).status_code == 200
    registrate = _q(b, "SELECT actor_kind, details->>'consent_version' FROM signature_evidence WHERE request_id = %s "
                       "AND event_type = 'consent_recorded' ORDER BY seq", (rid,))
    assert registrate == [("signer", consent.VERSION_V1), ("operator", consent.VERSION_V2)]
    from core.database import core_cursor
    from esign import otp_service
    from esign import service as esign_service
    with core_cursor() as (_, cur):
        cur.execute(f"SELECT {esign_service.COLUMNS} FROM signature_requests WHERE id = %s", (rid,))
        row = dict(cur.fetchone())
        sheet = otp_service._sheet_full(cur, row["visit_sheet_id"])
        dati = otp_service._report_data(cur, row, sheet, otp_service._signers(cur, rid), outcome="x", signed_sha256=None)
    assert dati["consent"]["version"] == consent.VERSION_V1 and sha in dati["consent"]["text"]   # mai riscritta


def test_documento_firmato_resta_dopo_una_nuova_versione(otp):
    o, b = otp, otp["app"]
    aid = _visita(b, completata=False)
    primo = _invia(o["agent"], aid, agent_sign=_firma_agente()).json()
    _cliente_firma(o)
    _q(b, "SET session_replication_role = replica; UPDATE appointments SET status = 'completed', "
          "completed_at = start_at + interval '50 minutes' WHERE id = %s", (aid,))
    nuovo = _invia(o["agent"], aid, known=primo["sheet"]["id"])
    assert nuovo.status_code == 409 and nuovo.json()["detail"]["code"] == "SHEET_UPDATED"
    data = o["agent"].get(f"{SHEETS}/appointments/{aid}").json()
    assert [s["version"] for s in data["sheets"]] == [2, 1]
    vecchio = data["sheets"][1]["signature"]
    assert vecchio["state"] == "completed" and vecchio["has_document"] is True
    assert o["agent"].get(f"{API}/{vecchio['request_id']}/document").content.startswith(b"%PDF")
    assert _q(b, "SELECT count(*) FROM visit_sheets WHERE appointment_id = %s", (aid,)) == [(2,)]
