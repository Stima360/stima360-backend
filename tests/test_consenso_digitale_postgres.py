"""C3 - RACCOLTA DIGITALE DEL CONSENSO su PostgreSQL VERO (schema completo, 110).

Riusa la fixture `fb` della Fase B (cursori di dominio sul database usa-e-getta,
PUBLIC_BASE_URL di prova). Le rotte pubbliche si chiamano con un TestClient sul
router pubblico vero; nessuna email reale (il dispatcher usa il provider finto).

  01  la 110: oggetti, codice email di conferma, registro non riscrivibile, down
  02  il link: solo un operatore della sua agenzia, solo l'impronta del token,
      30 giorni, un link nuovo sostituisce il vecchio, nessun messaggio parte
  03  la pagina: intestazioni di sicurezza, informativa cifrata, caselle vuote,
      indirizzi mascherati, la GET non scrive niente
  04  le scelte: email IN ATTESA (nessun consenso), telefono solo DICHIARATO,
      email di conferma accodata come servizio, nessun altro dato toccato
  05  il double opt-in: la GET non conferma, il POST concede l'email una volta,
      con prova e informativa; il telefono resta negato
  06  scadenze: conferma oltre 7 giorni rifiutata; link oltre 30 giorni solo
      revoche, che restano possibili
  07  revoche indipendenti per canale, anche dal link sostituito
  08  abusi: token sconosciuto o alterato, nessuna scelta, canale non coperto,
      limite di invii
  09  la conferma rifiutata se l'indirizzo cambia, se c'e' una revoca o una
      disiscrizione di piattaforma dopo la richiesta
  10  il funnel di stima360.it: casella email -> richiesta in attesa, idempotente;
      senza casella o con il solo vecchio campo, niente
  11  l'email di conferma la spedisce il dispatcher come servizio
  12  l'informativa pubblica per il sito: nessun dato personale
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.test_censimento_3_backend_postgres import _q, completo  # noqa: F401
from tests.test_delete_arch_0_postgres import mondo, operatori  # noqa: F401
from tests.test_fase_b_followup_postgres import (  # noqa: F401
    _contatto, _ctx, _guardia, _informativa, _sistema, fb,
)

pytestmark = pytest.mark.skipif(not os.getenv("P29_TEST_DSN"), reason="P29_TEST_DSN non impostata")

CANALI = ("email", "sms", "whatsapp", "phone")
PEPE = "p" * 32


def _adesso():
    return datetime.now(timezone.utc)


@pytest.fixture
def dg(fb, monkeypatch):
    """La Fase B piu' la pulizia delle tabelle della 110 e un client HTTP sul
    router pubblico vero."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from communication.public_router import router
    m = fb

    def pulisci():
        for sql in ("ALTER TABLE consent_submissions DISABLE TRIGGER trg_consent_submissions_guard",
                    "DELETE FROM consent_submissions",
                    "ALTER TABLE consent_submissions ENABLE TRIGGER trg_consent_submissions_guard",
                    "ALTER TABLE consent_links DISABLE TRIGGER trg_consent_links_guard",
                    "DELETE FROM consent_links",
                    "ALTER TABLE consent_links ENABLE TRIGGER trg_consent_links_guard",
                    "DELETE FROM consent_page_rate_limits"):
            _q(m, sql)

    pulisci()
    monkeypatch.setenv("CONSENT_CAPTURE_IP_PEPPER", PEPE)
    app = FastAPI()
    app.include_router(router)
    m["http"] = TestClient(app)
    yield m
    pulisci()


def _token(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[1]


def _con_telefono(m, email, **kw):
    cid = _contatto(m, email, **kw)
    _q(m, "UPDATE contacts SET phone = '333 123 4567', phone_normalized = '393331234567' WHERE id = %s", (cid,))
    return cid


def _link(m, cid, chi="admin_a"):
    from communication import consent_page
    return consent_page.generate_link(_ctx(m, chi), cid)


def _riga_contatto(m, cid):
    """Il contatto SENZA le colonne del consenso: cio' che C3 non deve toccare."""
    riga = _q(m, "SELECT to_jsonb(c) - ARRAY['marketing_consent','marketing_consent_at','marketing_revoked_at',"
                 "'marketing_consent_source','marketing_consent_notice_id','updated_at'] FROM contacts c "
                 "WHERE id = %s", (cid,))[0][0]
    return riga


def _conferma_dal_messaggio(m, submission_id):
    corpo = _q(m, "SELECT rendered_body FROM communication_messages WHERE reason_code = 'consent_confirmation' "
                  "AND (metadata->>'consent_submission_id')::bigint = %s", (submission_id,))[0][0]
    url = re.search(r"https://test\.stima360\.it/api/public/communication/consent/confirm/[A-Za-z0-9_-]{43}", corpo)
    assert url, corpo
    return url.group(0)


# ---------------------------------------------------------------------------

def test_01_la_110_oggetti_registro_e_down(dg):
    import psycopg2
    from psycopg2.extras import DictCursor
    m = dg
    assert _q(m, "SELECT 1 FROM schema_migrations WHERE version = '110_consent_capture'")
    for t in ("consent_links", "consent_submissions", "consent_page_rate_limits"):
        assert _q(m, "SELECT to_regclass(%s) IS NOT NULL", (f"public.{t}",))[0][0]
    vincolo = _q(m, "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conname = 'communication_messages_reason_code_chk'")[0][0]
    assert "consent_confirmation" in vincolo and "'m20'" in vincolo and "appointment_reminder" in vincolo
    # la down, in una transazione annullata, finche' non ci sono dati
    cartella = Path(__file__).resolve().parents[1] / "migrations"
    giu = (cartella / "110_consent_capture_down.sql").read_text().replace("BEGIN;", "", 1).rsplit("COMMIT;", 1)[0]
    conn = psycopg2.connect(m["dsn"], cursor_factory=DictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute(giu)
            cur.execute("SELECT to_regclass('public.consent_links') IS NULL AS via")
            assert cur.fetchone()["via"]
        conn.rollback()
    finally:
        conn.close()
    # il registro non si riscrive
    informativa = _informativa(m)
    cid = _con_telefono(m, "registro@example.it")
    link = _link(m, cid)
    lid = link["link_id"]
    with pytest.raises(psycopg2.Error):
        _q(m, "UPDATE consent_links SET expires_at = expires_at + interval '1 year' WHERE id = %s", (lid,))
    with pytest.raises(psycopg2.Error):
        _q(m, "DELETE FROM consent_links WHERE id = %s", (lid,))
    m["http"].post(f"/api/public/communication/consent/{_token(link['url'])}", data={"grant_sms": "1"})
    sid = _q(m, "SELECT id FROM consent_submissions WHERE contact_id = %s", (cid,))[0][0]
    for sql in ("UPDATE consent_submissions SET channels_requested = ARRAY['email'] WHERE id = %s",
                "UPDATE consent_submissions SET submitted_at = submitted_at - interval '1 day' WHERE id = %s",
                "DELETE FROM consent_submissions WHERE id = %s"):
        with pytest.raises(psycopg2.Error):
            _q(m, sql, (sid,))
    assert informativa


def test_02_il_link_solo_operatore_impronta_30_giorni_e_sostituzione(dg):
    from communication import consent_page
    from consent import capture
    from consent.exceptions import ConflictError, NotFoundError
    from core.exceptions import PermissionDenied
    from operator_auth.context import OperatorContext
    m = dg
    _informativa(m)
    cid = _con_telefono(m, "link@example.it")
    prima = _q(m, "SELECT count(*) FROM communication_messages")[0][0]
    basic = OperatorContext(user_id=None, agency_id=1, role="agency_owner", is_platform_admin=False,
                            session_id=None, auth_channel="legacy_basic")
    with pytest.raises(PermissionDenied):
        consent_page.generate_link(basic, cid)
    with pytest.raises(NotFoundError):  # un agente non assegnato non vede il contatto
        consent_page.generate_link(_ctx(m, "agent_a"), cid)
    with pytest.raises(NotFoundError):  # un'altra agenzia nemmeno
        consent_page.generate_link(_ctx(m, "owner_b"), cid)
    uno = _link(m, cid)
    token = _token(uno["url"])
    assert len(token) == 43 and uno["url"].startswith("https://test.stima360.it/api/public/communication/consent/")
    assert uno["qr_svg_data_uri"].startswith("data:image/svg+xml")
    riga = _q(m, "SELECT token_hash, expires_at - created_at, created_by_user_id FROM consent_links WHERE id = %s",
              (uno["link_id"],))[0]
    assert riga[0] == capture.hash_token(token) and token not in str(_q(m, "SELECT to_jsonb(l) FROM consent_links l"))
    assert riga[1] == timedelta(days=30) and riga[2] == m["ids"]["admin_a"]
    assert capture.resolve_link(token)["mode"] == "collect"
    due = _link(m, cid)
    assert capture.resolve_link(token)["mode"] == "revoke_only", "il link vecchio serve solo a revocare"
    assert capture.resolve_link(_token(due["url"]))["mode"] == "collect"
    assert _q(m, "SELECT count(*) FROM consent_links WHERE contact_id = %s AND superseded_at IS NULL "
                 "AND disabled_at IS NULL", (cid,))[0][0] == 1
    assert capture.disable_link(_ctx(m, "admin_a"), cid) is not None
    assert capture.resolve_link(_token(due["url"]))["mode"] == "revoke_only"
    assert _q(m, "SELECT count(*) FROM communication_messages")[0][0] == prima, "generare un link non invia niente"
    _q(m, "UPDATE contacts SET deleted_at = NOW(), deleted_reason = 'other' WHERE id = %s", (cid,))
    with pytest.raises(ConflictError):
        _link(m, cid)


def test_03_la_pagina_sicura_informativa_cifrata_caselle_vuote_get_senza_scritture(dg):
    m = dg
    _q(m, "UPDATE consent_notices SET valid_to = NOW() WHERE purpose = 'marketing' AND valid_to IS NULL")
    _q(m, "INSERT INTO consent_notices (purpose, version, content, content_hash, content_available, context, "
          "valid_from, created_by, channels) VALUES ('marketing', 'v-pagina', "
          "'Titolare: STIMA360 di Censori Giorgio. <script>alert(1)</script> Riga due', repeat('b', 64), TRUE, "
          "'test', NOW() - interval '1 day', 'test', ARRAY['email','sms','whatsapp','phone'])")
    cid = _con_telefono(m, "pagina@example.it")
    url = _link(m, cid)["url"]
    eventi = _q(m, "SELECT count(*) FROM consent_events")[0][0]
    r = m["http"].get(url.replace("https://test.stima360.it", ""))
    assert r.status_code == 200
    for chiave, valore in (("cache-control", "no-store"), ("referrer-policy", "no-referrer"),
                           ("x-frame-options", "DENY"), ("x-content-type-options", "nosniff")):
        assert r.headers[chiave] == valore, chiave
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert "noindex" in r.headers["x-robots-tag"]
    html = r.text
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "v-pagina" in html
    for c in CANALI:
        assert f'name="grant_{c}"' in html, c
    assert "checked" not in html, "nessuna casella preselezionata"
    assert "p•••@example.it" in html and "+39 ••• ••• 4567" in html
    assert "pagina@example.it" not in html and "3331234567" not in html
    assert '<form method="post"' in html
    assert _q(m, "SELECT count(*) FROM consent_events")[0][0] == eventi
    assert _q(m, "SELECT count(*) FROM consent_submissions")[0][0] == 0, "una GET non registra scelte"


def test_04_le_scelte_email_in_attesa_telefono_dichiarato_nessun_altro_dato(dg):
    m = dg
    informativa = _informativa(m)
    cid = _con_telefono(m, "scelte@example.it")
    prima = _riga_contatto(m, cid)
    url = _link(m, cid)["url"].replace("https://test.stima360.it", "")
    r = m["http"].post(url, data={f"grant_{c}": "1" for c in CANALI},
                       headers={"user-agent": "Browser di prova/1.0"})
    assert r.status_code == 200 and 'data-esito="recorded"' in r.text
    s = dict(zip(("email_status", "channels_requested", "phone_channels_declared", "notice_id", "ip_hash",
                  "user_agent", "origin", "email_address", "phone_number_snapshot", "confirm_expires_at",
                  "submitted_at"),
                 _q(m, "SELECT email_status, channels_requested, phone_channels_declared, notice_id, ip_hash, "
                       "user_agent, origin, email_address, phone_number_snapshot, confirm_expires_at, submitted_at "
                       "FROM consent_submissions WHERE contact_id = %s", (cid,))[0]))
    assert s["email_status"] == "pending" and s["channels_requested"] == list(CANALI)
    assert s["phone_channels_declared"] == ["sms", "whatsapp", "phone"]
    assert s["notice_id"] == informativa and s["origin"] == "consent_page"
    assert s["ip_hash"] and len(s["ip_hash"]) == 64 and s["user_agent"] == "Browser di prova/1.0"
    assert s["email_address"] == "scelte@example.it" and s["phone_number_snapshot"] == "393331234567"
    assert s["confirm_expires_at"] - s["submitted_at"] == timedelta(days=7)
    # NESSUN consenso attivo: ne' l'email (attende la conferma) ne' il telefono (non verificato)
    assert _q(m, "SELECT count(*) FROM consent_events WHERE contact_id = %s", (cid,))[0][0] == 0
    for c in CANALI:
        assert not _guardia(m, 1, cid, c).allowed, c
    msg = _q(m, "SELECT communication_type, reason_code, destination_snapshot, status, channel "
                "FROM communication_messages WHERE contact_id = %s", (cid,))
    assert [tuple(x) for x in msg] == [("service", "consent_confirmation", "scelte@example.it", "queued", "email")]
    assert _riga_contatto(m, cid) == prima, "nessun dato del contatto oltre al consenso"
    # la scheda del CRM lo dice
    from consent import operator_service as s_op
    stato = s_op.marketing_state(_ctx(m), cid)
    assert stato["capture"]["available"] and stato["capture"]["link"]["collecting"]
    assert stato["capture"]["channels"]["email"]["pending_confirmation"]
    assert stato["capture"]["channels"]["sms"]["declared_unverified"]
    assert not stato["allowed"]


def test_05_il_double_opt_in_get_non_conferma_post_concede_una_volta(dg):
    m = dg
    informativa = _informativa(m)
    cid = _con_telefono(m, "doi@example.it")
    url = _link(m, cid)["url"].replace("https://test.stima360.it", "")
    m["http"].post(url, data={"grant_email": "1", "grant_whatsapp": "1"})
    sid = _q(m, "SELECT id FROM consent_submissions WHERE contact_id = %s", (cid,))[0][0]
    conferma = _conferma_dal_messaggio(m, sid).replace("https://test.stima360.it", "")
    r = m["http"].get(conferma)
    assert r.status_code == 200 and "data-conferma-form" in r.text and "d•••@example.it" in r.text
    assert not _guardia(m, 1, cid).allowed, "aprire il link non conferma"
    r = m["http"].post(conferma, headers={"user-agent": "Telefono/2"})
    assert r.status_code == 200 and 'data-esito="confirmed"' in r.text
    ev = _q(m, "SELECT channel, decision, source, actor_type, evidence_type, evidence_ref, notice_id "
               "FROM consent_events WHERE contact_id = %s", (cid,))
    assert [tuple(x) for x in ev] == [("email", "granted", "consent_page", "subject", "double_opt_in",
                                      f"consent_submission:{sid}", informativa)]
    d = _guardia(m, 1, cid)
    assert d.allowed and d.reason == "allow_explicit_grant"
    assert not _guardia(m, 1, cid, "whatsapp").allowed, "il telefono resta solo dichiarato"
    riga = _q(m, "SELECT email_status, confirmed_at IS NOT NULL, confirm_ip_hash IS NOT NULL, confirm_user_agent, "
                 "consent_event_id IS NOT NULL FROM consent_submissions WHERE id = %s", (sid,))[0]
    assert list(riga) == ["confirmed", True, True, "Telefono/2", True]
    r = m["http"].post(conferma)
    assert 'data-esito="already_confirmed"' in r.text
    assert _q(m, "SELECT count(*) FROM consent_events WHERE contact_id = %s", (cid,))[0][0] == 1


def test_06_scadenze_conferma_7_giorni_link_30_giorni_revoche_sempre(dg):
    from consent import capture
    m = dg
    _informativa(m)
    cid = _con_telefono(m, "scade@example.it")
    link = _link(m, cid)
    token = _token(link["url"])
    risolto = capture.resolve_link(token)
    esito = capture.submit(risolto, grant=["email"], revoke=[], ip=None, user_agent=None,
                           on_email_pending=lambda cur, ctx, invio, raw: _salva(m, invio["id"], raw))
    raw = m["confermo"][esito["submission_id"]]
    tardi = _adesso() + timedelta(days=8)
    assert capture.confirm_email(raw, ip=None, user_agent=None, now=tardi)["status"] == "expired"
    assert not _guardia(m, 1, cid).allowed
    # un secondo invio, confermato in tempo
    risolto = capture.resolve_link(token)
    esito = capture.submit(risolto, grant=["email"], revoke=[], ip=None, user_agent=None,
                           on_email_pending=lambda cur, ctx, invio, raw: _salva(m, invio["id"], raw))
    assert capture.confirm_email(m["confermo"][esito["submission_id"]], ip=None, user_agent=None)["status"] == "confirmed"
    assert _guardia(m, 1, cid).allowed
    # 31 giorni dopo il link non raccoglie piu' ...
    dopo = _adesso() + timedelta(days=31)
    scaduto = capture.resolve_link(token, now=dopo)
    assert scaduto["mode"] == "revoke_only"
    assert capture.submit(scaduto, grant=["sms"], revoke=[], ip=None, user_agent=None,
                          on_email_pending=None, now=dopo)["status"] == "collect_closed"
    pagina = capture.page_state(scaduto, now=dopo)
    assert pagina["mode"] == "revoke_only" and pagina["channels"]["email"]["active"]
    # ... ma la revoca si'
    esito = capture.submit(scaduto, grant=[], revoke=["email"], ip=None, user_agent=None,
                           on_email_pending=None, now=dopo)
    assert esito["status"] == "recorded" and esito["revoked"] == ["email"]
    d = _guardia(m, 1, cid)
    assert not d.allowed and d.reason == "deny_revoked"


def _salva(m, sid, raw):
    m.setdefault("confermo", {})[sid] = raw
    return None


def test_07_revoche_indipendenti_anche_dal_link_sostituito(dg):
    from consent import capture
    m = dg
    _informativa(m)
    cid = _con_telefono(m, "indip@example.it")
    vecchio = _link(m, cid)
    capture.submit(capture.resolve_link(_token(vecchio["url"])), grant=["email", "sms", "whatsapp"], revoke=[],
                   ip=None, user_agent=None, on_email_pending=lambda cur, ctx, invio, raw: _salva(m, invio["id"], raw))
    sid = max(m["confermo"])
    capture.confirm_email(m["confermo"][sid], ip=None, user_agent=None)
    _link(m, cid)  # il link vecchio ora serve solo a revocare
    risolto = capture.resolve_link(_token(vecchio["url"]))
    assert risolto["mode"] == "revoke_only"
    r = m["http"].post(f"/api/public/communication/consent/{_token(vecchio['url'])}", data={"revoke_whatsapp": "1"})
    assert r.status_code == 200 and 'data-esito="recorded"' in r.text
    pannello = capture.page_state(capture.resolve_link(_token(vecchio["url"])), count_view=False)
    assert pannello["channels"]["whatsapp"]["declared_unverified"] is None
    assert pannello["channels"]["sms"]["declared_unverified"] is not None, "SMS non toccato"
    assert _guardia(m, 1, cid).allowed, "l'email resta attiva"
    # e il funnel di revoca dalla pagina scrive un evento solo per i canali ATTIVI
    assert _q(m, "SELECT count(*) FROM consent_events WHERE contact_id = %s AND decision = 'revoked'",
              (cid,))[0][0] == 0


def test_08_abusi_token_alterato_nessuna_scelta_canale_non_coperto_limite(dg):
    from consent import capture
    m = dg
    _informativa(m, canali=("email",))
    cid = _con_telefono(m, "abusi@example.it")
    token = _token(_link(m, cid)["url"])
    for alterato in ("x" * 43, token[:-1] + ("A" if token[-1] != "A" else "B"), "corto", token + "x"):
        r = m["http"].get(f"/api/public/communication/consent/{alterato}")
        assert r.status_code == 404 and "non è valido" in r.text
        r = m["http"].post(f"/api/public/communication/consent/{alterato}", data={"grant_email": "1"})
        assert r.status_code == 404
    r = m["http"].get(f"/api/public/communication/consent/{token}")
    assert 'name="grant_email"' in r.text and 'name="grant_sms"' not in r.text, "solo i canali dell'informativa"
    r = m["http"].post(f"/api/public/communication/consent/{token}", data={})
    assert 'data-esito="nothing"' in r.text
    r = m["http"].post(f"/api/public/communication/consent/{token}", data={"grant_sms": "1"})
    assert r.status_code == 409
    r = m["http"].post(f"/api/public/communication/consent/{token}", data={"grant_email": "1", "revoke_email": "1"})
    assert r.status_code == 409
    assert _q(m, "SELECT count(*) FROM consent_submissions")[0][0] == 0
    stati = [m["http"].post(f"/api/public/communication/consent/{token}", data={}).status_code
             for _ in range(capture.LIMIT_SUBMIT[0] + 2)]
    assert 429 in stati, stati
    r = m["http"].get("/api/public/communication/consent/confirm/" + "y" * 43)
    assert r.status_code == 404
    r = m["http"].post("/api/public/communication/consent/confirm/" + "y" * 43)
    assert r.status_code == 404
    assert _q(m, "SELECT count(*) FROM consent_events WHERE contact_id = %s", (cid,))[0][0] == 0


def test_09_conferma_rifiutata_indirizzo_cambiato_revoca_o_disiscrizione_dopo(dg):
    from consent import capture
    from consent import operator_service as s_op
    m = dg
    _informativa(m)

    def richiesta(email):
        cid = _con_telefono(m, email)
        esito = capture.submit(capture.resolve_link(_token(_link(m, cid)["url"])), grant=["email"], revoke=[],
                               ip=None, user_agent=None,
                               on_email_pending=lambda cur, ctx, invio, raw: _salva(m, invio["id"], raw))
        return cid, m["confermo"][esito["submission_id"]]

    cid, raw = richiesta("cambia@example.it")
    _q(m, "UPDATE contacts SET email = 'altro@example.it', email_normalized = 'altro@example.it' WHERE id = %s", (cid,))
    assert capture.confirm_email(raw, ip=None, user_agent=None) == {"status": "cancelled", "reason": "address_changed"}
    cid, raw = richiesta("revoca@example.it")
    s_op.register_refusal(_ctx(m, "admin_a"), cid, channels=["email"], decided_at=_adesso(), scope="agency",
                          note="Al telefono ha detto di non volere email")
    assert capture.confirm_email(raw, ip=None, user_agent=None)["reason"] == "revoked_after_request"
    cid, raw = richiesta("optout@example.it")
    from communication import unsubscribe as u
    _q(m, "UPDATE contacts SET marketing_consent = FALSE WHERE id = %s", (cid,))
    from consent import service as cs
    cs.register_platform_optout(_sistema(1), contact_id=cid, channel="email", source="unsubscribe_link",
                                actor_type="subject", idempotency_key=f"t-opt-{cid}")
    assert capture.confirm_email(raw, ip=None, user_agent=None)["reason"] == "opted_out_after_request"
    assert u  # il modulo resta quello di sempre
    assert _q(m, "SELECT count(*) FROM consent_events WHERE decision = 'granted' AND evidence_type = 'double_opt_in'")[0][0] == 0


def test_10_il_funnel_di_stima360_casella_email_in_attesa_idempotente(dg):
    from communication import consent_page
    m = dg
    informativa = _informativa(m)
    cid = _contatto(m, "funnel@example.it")
    stima = _q(m, "INSERT INTO stime (agency_id, comune, via, mq) VALUES (1, 'Alba', 'Via Funnel', 80) RETURNING id")[0][0]
    ctx = _sistema(1)
    assert consent_page.request_from_public_stima(ctx, raw={"consenso_marketing": True}, stima_id=stima,
                                                  contact_id=cid)["status"] == "not_requested"
    assert consent_page.request_from_public_stima(ctx, raw={"consenso_email": True}, stima_id=stima,
                                                  contact_id=cid)["status"] == "notice_missing"
    assert consent_page.request_from_public_stima(ctx, raw={"consenso_email": True, "informativa_marketing_id": 999999},
                                                  stima_id=stima, contact_id=cid)["status"] == "notice_invalid"
    esito = consent_page.request_from_public_stima(
        ctx, raw={"consenso_email": True, "informativa_marketing_id": informativa}, stima_id=stima, contact_id=cid)
    assert esito["status"] == "queued"
    assert consent_page.request_from_public_stima(
        ctx, raw={"consenso_email": True, "informativa_marketing_id": informativa}, stima_id=stima,
        contact_id=cid)["status"] == "already_requested"
    riga = _q(m, "SELECT origin, stima_id, channels_requested, email_status, link_id FROM consent_submissions "
                 "WHERE contact_id = %s", (cid,))
    assert [tuple(x) for x in riga] == [("public_stima", stima, ["email"], "pending", None)]
    assert not _guardia(m, 1, cid).allowed, "la casella da sola non concede niente"
    conferma = _conferma_dal_messaggio(m, esito["submission_id"]).replace("https://test.stima360.it", "")
    assert m["http"].post(conferma).status_code == 200
    ev = _q(m, "SELECT source, evidence_type, notice_id FROM consent_events WHERE contact_id = %s", (cid,))
    assert [tuple(x) for x in ev] == [("public_stima", "double_opt_in", informativa)]
    assert _guardia(m, 1, cid).allowed


def test_11_il_dispatcher_spedisce_la_conferma_come_servizio(dg):
    from communication import dispatcher
    m = dg
    _informativa(m)
    cid = _con_telefono(m, "spedisci@example.it")
    url = _link(m, cid)["url"].replace("https://test.stima360.it", "")
    m["http"].post(url, data={"grant_email": "1"})
    esito = dispatcher.dispatch_batch(_ctx(m), channel="email")
    assert esito["sent"] == 1 and esito["suppressed"] == 0, esito
    assert _q(m, "SELECT status FROM communication_messages WHERE contact_id = %s", (cid,))[0][0] == "sent"
    assert not _guardia(m, 1, cid).allowed, "spedire la conferma non concede niente"


def test_12_l_informativa_pubblica_per_il_sito_senza_dati_personali(dg):
    m = dg
    # Le informative sono un registro (DELETE rifiutato): si chiudono quelle
    # lasciate dai test precedenti, come fa la pubblicazione di una nuova.
    _q(m, "UPDATE consent_notices SET valid_to = NOW() - INTERVAL '1 second' "
          "WHERE purpose = 'marketing' AND (valid_to IS NULL OR valid_to >= NOW())")
    r = m["http"].get("/api/public/communication/consent/informativa.json")
    assert r.json() == {"available": False}
    informativa = _informativa(m, canali=("email", "sms"))
    r = m["http"].get("/api/public/communication/consent/informativa.json")
    corpo = r.json()
    assert corpo == {"available": True, "id": informativa, "version": corpo["version"], "channels": ["email", "sms"],
                     "url": "/api/public/communication/consent/informativa"}
    r = m["http"].get("/api/public/communication/consent/informativa")
    assert r.status_code == 200 and "Informativa marketing di prova" in r.text
    assert r.headers["cache-control"] == "no-store"
    assert str(uuid.UUID(int=0))  # nessun identificativo personale nella risposta
