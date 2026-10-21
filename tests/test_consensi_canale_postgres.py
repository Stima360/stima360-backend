"""CONSENSI MARKETING PER CANALE (C1 + C2) su PostgreSQL VERO.

Schema completo (109 compresa), dati di prova propri, nessun invio reale (il
dispatcher usa il provider finto). Riusa la fixture `fb` della Fase B.

  01  la 109: oggetti, vincolo del canale sugli eventi nuovi, opt-out 108
      copiati sul canale email, down eseguibile (in una transazione annullata)
  02  quattro canali INDIPENDENTI: concedere l'email non concede altro;
      revocare WhatsApp non tocca l'email; il riassunto su `contacts` segue
  03  consenso ASSENTE e STORICO AMBIGUO: il vecchio flag e la spunta del
      funnel senza canale non autorizzano nessun canale; lo storico resta
  04  nessun consenso "generale": niente canali, canale ignoto, canale non
      coperto dall'informativa citata, evento marketing senza canale
  05  opt-out di piattaforma PER NUMERO: WhatsApp bloccato in ogni agenzia,
      l'email no; un canale senza indirizzo non inventa un opt-out
  06  il link di disiscrizione (email) revoca SOLO l'email
  07  il dispatcher chiede il consenso del CANALE del messaggio
  08  un messaggio di servizio non aggira il consenso: senza una richiesta
      dell'interessato e' rifiutato; con una, il ledger ne porta la base
  09  la platea della journey email legge solo il consenso email
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.test_censimento_3_backend_postgres import _q, completo  # noqa: F401
from tests.test_delete_arch_0_postgres import mondo, operatori  # noqa: F401
from tests.test_fase_b_followup_postgres import (  # noqa: F401
    GIORNO, ORA, _contatto, _ctx, _guardia, _informativa, _sistema, fb,
)

_SENZA_DB = pytest.mark.skipif(not os.getenv("P29_TEST_DSN"), reason="P29_TEST_DSN non impostata")

CANALI = ("email", "sms", "whatsapp", "phone")
PROVA = dict(evidence_type="paper_form", evidence_ref="PROT-2026/0101")


def _adesso():
    return datetime.now(timezone.utc)


def _stati(m, agenzia, cid):
    return {c: _guardia(m, agenzia, cid, c) for c in CANALI}


# ---------------------------------------------------------------------------

@_SENZA_DB
def test_01_la_109_oggetti_vincoli_copia_degli_optout_e_down(fb):
    import psycopg2
    from psycopg2.extras import DictCursor
    m = fb
    assert _q(m, "SELECT 1 FROM schema_migrations WHERE version = '109_consent_channels'")
    for tabella in ("contact_channel_consents", "communication_optouts"):
        assert _q(m, "SELECT to_regclass(%s) IS NOT NULL", (f"public.{tabella}",))[0][0]
    cid = _contatto(m, "vincoli@example.it")
    # un evento marketing nuovo SENZA canale e' rifiutato dal database...
    with pytest.raises(psycopg2.Error):
        _q(m, "INSERT INTO consent_events (agency_id, contact_id, purpose, decision, decided_at, source, "
              "actor_type, actor_ref) VALUES (1, %s, 'marketing', 'granted', NOW(), 'crm', 'operator', 'x')", (cid,))
    # ...con la sola eccezione della spunta del funnel pubblico (pre-canale)
    _q(m, "INSERT INTO consent_events (agency_id, contact_id, purpose, decision, decided_at, source, actor_type) "
          "VALUES (1, %s, 'marketing', 'granted', NOW(), 'public_stima', 'subject')", (cid,))
    with pytest.raises(psycopg2.Error):  # il canale e' solo del marketing
        _q(m, "INSERT INTO consent_events (agency_id, contact_id, purpose, channel, decision, decided_at, source, "
              "actor_type) VALUES (1, %s, 'privacy_terms', 'email', 'granted', NOW(), 'public_stima', 'subject')",
           (cid,))
    with pytest.raises(psycopg2.Error):  # canale fuori dall'insieme
        _q(m, "INSERT INTO consent_events (agency_id, contact_id, purpose, channel, decision, decided_at, source, "
              "actor_type) VALUES (1, %s, 'marketing', 'fax', 'granted', NOW(), 'public_stima', 'subject')", (cid,))
    with pytest.raises(psycopg2.Error):  # un numero non e' un indirizzo email
        _q(m, "INSERT INTO communication_optouts (channel, address_normalized, opted_out_at, source, actor_type, "
              "idempotency_key) VALUES ('email', '393331234567', NOW(), 'crm', 'subject', 'kx')")

    # Copia degli opt-out 108 e down, in una transazione ANNULLATA: giu' la 109
    # (nessun dato per canale), una riga nella tabella 108, su di nuovo la 109.
    cartella = Path(__file__).resolve().parents[1] / "migrations"
    giu = (cartella / "109_consent_channels_down.sql").read_text().replace("BEGIN;", "", 1).rsplit("COMMIT;", 1)[0]
    su = (cartella / "109_consent_channels.sql").read_text()
    conn = psycopg2.connect(m["dsn"], cursor_factory=DictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute(giu)
            cur.execute("SELECT to_regclass('public.communication_optouts') IS NULL AS via")
            assert cur.fetchone()["via"]
            cur.execute("INSERT INTO communication_email_optouts (email_normalized, opted_out_at, source, actor_type, "
                        "idempotency_key) VALUES ('storico@example.it', '2026-09-01T10:00:00Z', 'unsubscribe_link', "
                        "'subject', 'storico-108')")
            cur.execute(su)
            cur.execute("SELECT channel, address_normalized, opted_out_at, idempotency_key FROM communication_optouts")
            righe = [dict(r) for r in cur.fetchall()]
            assert righe == [{"channel": "email", "address_normalized": "storico@example.it",
                              "opted_out_at": datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc),
                              "idempotency_key": "storico-108"}]
            with pytest.raises(psycopg2.Error):  # la 108 e' di nuovo chiusa
                cur.execute("SAVEPOINT s")
                cur.execute("INSERT INTO communication_email_optouts (email_normalized, opted_out_at, source, "
                            "actor_type, idempotency_key) VALUES ('x@y.it', NOW(), 'crm', 'subject', 'k9')")
            cur.execute("ROLLBACK TO SAVEPOINT s")
        conn.rollback()
    finally:
        conn.close()


@_SENZA_DB
def test_02_quattro_canali_indipendenti(fb):
    from consent import operator_service as s
    m = fb
    informativa = _informativa(m)
    cid = _contatto(m, "anna.canali@example.it")
    _q(m, "UPDATE contacts SET phone = '333 123 4567', phone_normalized = '393331234567' WHERE id = %s", (cid,))
    quando = _adesso() - 2 * GIORNO
    # SOLO email: gli altri tre restano mai dati
    esito = s.register_grant(_ctx(m, "admin_a"), cid, channels=["email"], decided_at=quando,
                             notice_id=informativa, **PROVA)
    assert esito["channels"] == ["email"] and len(esito["events"]) == 1
    stati = _stati(m, 1, cid)
    assert stati["email"].allowed and stati["email"].reason == "allow_explicit_grant"
    for canale in ("sms", "whatsapp", "phone"):
        assert not stati[canale].allowed and stati[canale].reason == "deny_never_given", canale
    # email + WhatsApp, poi revoca SOLO WhatsApp
    s.register_grant(_ctx(m, "admin_a"), cid, channels=["whatsapp"], decided_at=quando + timedelta(hours=1),
                     notice_id=informativa, evidence_type="digital_form", evidence_ref="FORM-WA-1")
    assert _guardia(m, 1, cid, "whatsapp").allowed
    s.register_refusal(_ctx(m, "admin_a"), cid, channels=["whatsapp"], decided_at=quando + GIORNO, scope="agency",
                       note="Al telefono: niente WhatsApp, l'email va bene")
    stati = _stati(m, 1, cid)
    assert stati["email"].allowed, "revocare WhatsApp non tocca l'email"
    assert not stati["whatsapp"].allowed and stati["whatsapp"].reason == "deny_revoked"
    assert not stati["sms"].allowed and stati["sms"].reason == "deny_never_given"
    # proiezione per canale e riassunto su `contacts`
    righe = {r[0]: r[1] for r in _q(m, "SELECT channel, status FROM contact_channel_consents WHERE contact_id = %s",
                                    (cid,))}
    assert righe == {"email": "granted", "whatsapp": "revoked"}
    assert _q(m, "SELECT marketing_consent FROM contacts WHERE id = %s", (cid,))[0][0] is True
    # revoca dell'email: nessun canale concesso, il riassunto scende a FALSE
    s.register_refusal(_ctx(m, "admin_a"), cid, channels=["email"], decided_at=quando + 2 * GIORNO, scope="agency",
                       note="Ha chiesto di non ricevere piu' niente")
    assert _q(m, "SELECT marketing_consent, marketing_revoked_at IS NOT NULL FROM contacts WHERE id = %s",
              (cid,))[0] == [False, True]
    stato = s.marketing_state(_ctx(m), cid)
    assert not stato["allowed"] and [c for c in stato["channel_order"]] == list(CANALI)
    assert stato["channels"]["whatsapp"]["projection"]["status"] == "revoked"
    assert stato["channels"]["sms"]["projection"]["status"] == "never_given"
    # lo storico e' intero: 4 eventi, tutti con canale
    assert _q(m, "SELECT count(*), count(channel) FROM consent_events WHERE contact_id = %s", (cid,))[0] == [4, 4]


@_SENZA_DB
def test_03_assente_e_storico_ambiguo_non_autorizzano(fb):
    from consent import operator_service as s
    m = fb
    mai = _contatto(m, "mai@example.it")
    flag = _contatto(m, "flag@example.it", consenso="legacy", quando=ORA - 300 * GIORNO)
    spunta = _contatto(m, "spunta@example.it", consenso="pre_canale", quando=ORA - 5 * GIORNO)
    for cid in (mai, flag, spunta):
        for canale, d in _stati(m, 1, cid).items():
            assert not d.allowed and d.reason == "deny_never_given", (cid, canale, d.reason)
    assert _guardia(m, 1, flag).legacy is True, "si vede la spunta storica, non si manda niente"
    assert _guardia(m, 1, mai).legacy is False
    # lo storico resta com'era: il flag, e l'evento pre-canale senza canale
    assert _q(m, "SELECT marketing_consent FROM contacts WHERE id = %s", (flag,))[0][0] is True
    assert _q(m, "SELECT count(*), count(channel) FROM consent_events WHERE contact_id = %s", (spunta,))[0] == [1, 0]
    assert _q(m, "SELECT count(*) FROM contact_channel_consents WHERE contact_id IN (%s, %s)", (flag, spunta))[0][0] == 0
    stato = s.marketing_state(_ctx(m), flag)
    assert stato["legacy"] and not stato["allowed"]
    assert s.marketing_state(_ctx(m), spunta)["pre_channel_event"]["source"] == "public_stima"
    # una spunta pre-canale DOPO un consenso per canale non cambia lo stato dei canali
    from consent import service as cs
    doc = _contatto(m, "doc@example.it", consenso="granted")
    cs.record_grant(_sistema(1), contact_id=doc, purpose="marketing", source="public_stima", actor_type="subject",
                    decided_at=ORA - GIORNO, idempotency_key=f"t:{doc}:pre2")
    assert _guardia(m, 1, doc).allowed and not _guardia(m, 1, doc, "sms").allowed


@_SENZA_DB
def test_04_nessun_consenso_generale(fb):
    from consent import operator_service as s
    from consent import service as cs
    from consent.exceptions import ValidationError
    m = fb
    solo_email = _informativa(m, canali=("email",))
    cid = _contatto(m, "generale@example.it")
    quando = _adesso() - GIORNO
    for canali in ([], None, ["tutti"], ["email", "fax"]):
        with pytest.raises(ValidationError):
            s.register_grant(_ctx(m), cid, channels=canali, decided_at=quando, notice_id=solo_email, **PROVA)
    # l'informativa presentata non descrive gli SMS: non si puo' registrare
    with pytest.raises(ValidationError):
        s.register_grant(_ctx(m), cid, channels=["email", "sms"], decided_at=quando, notice_id=solo_email, **PROVA)
    assert _q(m, "SELECT count(*) FROM consent_events WHERE contact_id = %s", (cid,))[0][0] == 0, "tutto o niente"
    # il servizio rifiuta un evento marketing senza canale (salvo il funnel pubblico)
    with pytest.raises(ValidationError):
        cs.record_grant(_sistema(1), contact_id=cid, purpose="marketing", source="crm", actor_type="operator",
                        actor_ref="operator_user:1", decided_at=quando)
    with pytest.raises(ValidationError):
        cs.record_revocation(_sistema(1), contact_id=cid, purpose="marketing", source="unsubscribe_link",
                             actor_type="subject", decided_at=quando)
    s.register_grant(_ctx(m), cid, channels=["email"], decided_at=quando, notice_id=solo_email, **PROVA)
    assert _guardia(m, 1, cid).allowed and not _guardia(m, 1, cid, "sms").allowed


@_SENZA_DB
def test_05_optout_di_piattaforma_per_numero(fb):
    from consent import operator_service as s
    m = fb
    informativa = _informativa(m)
    quando = _adesso() - 3 * GIORNO
    a = _contatto(m, "num.a@example.it", agenzia=1)
    b = _contatto(m, "num.b@example.it", agenzia=2)
    for cid in (a, b):
        _q(m, "UPDATE contacts SET phone = '+39 333 765 4321', phone_normalized = '393337654321' WHERE id = %s", (cid,))
    s.register_grant(_ctx(m, "admin_a"), a, channels=["email", "whatsapp"], decided_at=quando,
                     notice_id=informativa, **PROVA)
    s.register_grant(_ctx(m, "owner_b"), b, channels=["email", "whatsapp", "sms"], decided_at=quando,
                     notice_id=informativa, evidence_type="digital_form", evidence_ref="FORM-B-77")
    # "basta WhatsApp da STIMA360", dichiarato in agenzia 1
    esito = s.register_refusal(_ctx(m, "admin_a"), a, channels=["whatsapp"], decided_at=quando + GIORNO,
                               scope="platform", note="Ha risposto STOP ai WhatsApp di tutte le agenzie")
    assert esito["platform_optouts"][0]["channel"] == "whatsapp"
    assert _q(m, "SELECT channel, address_normalized FROM communication_optouts")[0] == ["whatsapp", "393337654321"]
    # in agenzia 2, stesso numero: WhatsApp bloccato, SMS ed email no
    assert _guardia(m, 2, b, "whatsapp").reason == "deny_platform_optout"
    assert _guardia(m, 2, b, "sms").allowed and _guardia(m, 2, b, "email").allowed
    assert _q(m, "SELECT count(*) FROM consent_events WHERE contact_id = %s", (b,))[0][0] == 3, \
        "nessun evento scritto per conto dell'agenzia 2"
    # un canale senza indirizzo: revoca si', opt-out inventato no
    senza_numero = _contatto(m, "nonum@example.it", consenso="granted")
    esito = s.register_refusal(_ctx(m), senza_numero, channels=["sms", "email"], decided_at=_adesso() - GIORNO,
                               scope="platform", note="Basta comunicazioni commerciali, da tutti")
    assert esito["channels_without_address"] == ["sms"]
    assert [o["channel"] for o in esito["platform_optouts"]] == ["email"]


@_SENZA_DB
def test_06_il_link_email_revoca_solo_l_email(fb):
    from communication import unsubscribe as u
    from consent import operator_service as s
    m = fb
    informativa = _informativa(m)
    cid = _contatto(m, "link@example.it")
    _q(m, "UPDATE contacts SET phone_normalized = '393339990000' WHERE id = %s", (cid,))
    s.register_grant(_ctx(m), cid, channels=["email", "whatsapp"], decided_at=_adesso() - 2 * GIORNO,
                     notice_id=informativa, **PROVA)
    assert u.process(u.issue(1, cid, scope=u.SCOPE_PLATFORM)) is True
    assert _guardia(m, 1, cid).reason == "deny_revoked"
    assert _guardia(m, 1, cid, "whatsapp").allowed, "un link in una email non decide sul WhatsApp"
    assert _q(m, "SELECT channel FROM communication_optouts")[0][0] == "email"


@_SENZA_DB
def test_07_il_dispatcher_chiede_il_consenso_del_canale_del_messaggio(fb):
    from communication import dispatcher, service as comm
    m = fb
    informativa = _informativa(m)
    from consent import operator_service as s
    cid = _contatto(m, "disp@example.it")
    _q(m, "UPDATE contacts SET phone_normalized = '393331112222' WHERE id = %s", (cid,))
    s.register_grant(_ctx(m), cid, channels=["email"], decided_at=_adesso() - GIORNO, notice_id=informativa, **PROVA)
    for canale, destinazione, oggetto in (("whatsapp", "393331112222", None), ("email", "disp@example.it", "Ciao")):
        comm.enqueue(_ctx(m), contact_id=cid, channel=canale, communication_type="marketing", mode="manual",
                     reason_code="operator_manual", rendered_body="Proposta", destination_snapshot=destinazione,
                     subject_snapshot=oggetto, idempotency_key=f"c2:{canale}:{cid}")
    wa = dispatcher.dispatch_batch(_ctx(m), channel="whatsapp")
    em = dispatcher.dispatch_batch(_ctx(m), channel="email")
    assert wa["suppressed"] == 1 and wa["sent"] == 0, wa
    assert em["sent"] == 1 and em["suppressed"] == 0, em
    righe = {r[0]: (r[1], r[2]) for r in _q(m, "SELECT channel, status, error_code FROM communication_messages "
                                               "WHERE contact_id = %s", (cid,))}
    assert righe["whatsapp"][0] == "suppressed" and righe["email"][0] == "sent"


@_SENZA_DB
def test_08_il_servizio_non_aggira_il_consenso(fb):
    from communication import contact_view
    from communication.exceptions import ValidationError
    m = fb
    nessuno = _contatto(m, "nessuno@example.it")
    with pytest.raises(ValidationError):
        contact_view.send_manual(_ctx(m), nessuno, subject="Offerta", body="Le proponiamo...",
                                 communication_type="service")
    assert _q(m, "SELECT count(*) FROM communication_messages WHERE contact_id = %s", (nessuno,))[0][0] == 0
    # con una richiesta dell'interessato (una stima collegata al suo lead) il servizio parte
    cliente = _contatto(m, "cliente@example.it")
    stima = _q(m, "INSERT INTO stime (agency_id, comune, via, mq) VALUES (1, 'Alba', 'Via Servizio', 70) RETURNING id")[0][0]
    lead = _q(m, "INSERT INTO leads (agency_id, contact_id) VALUES (1, %s) RETURNING id", (cliente,))[0][0]
    _q(m, "INSERT INTO lead_stime (lead_id, stima_id, relation_type) VALUES (%s, %s, 'origin')", (lead, stima))
    esito = contact_view.send_manual(_ctx(m), cliente, subject="La sua stima", body="In allegato i dettagli",
                                     communication_type="service")
    meta = _q(m, "SELECT communication_type, metadata->>'service_basis' FROM communication_messages WHERE id = %s",
              (esito["message"]["id"],))[0]
    assert meta == ["service", f"stima:{stima}"]
    # e senza consenso il marketing resta rifiutato anche per lui
    with pytest.raises(ValidationError):
        contact_view.send_manual(_ctx(m), cliente, subject="Offerta", body="x", communication_type="marketing")


@_SENZA_DB
def test_09_la_platea_email_legge_solo_il_consenso_email(fb):
    from communication import audience
    from consent import operator_service as s
    m = fb
    informativa = _informativa(m)
    email = _contatto(m, "platea.email@example.it", consenso="granted")
    solo_wa = _contatto(m, "platea.wa@example.it")
    _q(m, "UPDATE contacts SET phone_normalized = '393334445555' WHERE id = %s", (solo_wa,))
    s.register_grant(_ctx(m), solo_wa, channels=["whatsapp"], decided_at=_adesso() - GIORNO,
                     notice_id=informativa, **PROVA)
    flag = _contatto(m, "platea.flag@example.it", consenso="legacy")
    spunta = _contatto(m, "platea.spunta@example.it", consenso="pre_canale")
    from communication import database as comm_db
    with comm_db.communication_cursor() as (_, cur):
        scelti = {c["contact_id"] for c in audience.candidates(cur, _sistema(1), now=ORA, limit=50)}
    assert email in scelti
    for escluso in (solo_wa, flag, spunta):
        assert escluso not in scelti, escluso


# ---------------------------------------------------------------------------
# Statico (gira anche senza database): l'interfaccia non preseleziona canali
# ---------------------------------------------------------------------------

def test_10_la_scheda_contatto_chiede_i_canali_uno_per_uno():
    root = Path(__file__).resolve().parents[1]
    js = (root / "static" / "os_shell" / "assets" / "components" / "communications.js").read_text(encoding="utf-8")
    assert "name=\"channels\" value=\"${c}\"" in js
    assert "checked" not in js.split("function caselleCanali", 1)[1].split("function rigaCanale", 1)[0]
    assert "channels: canaliScelti," in js and js.count("channels: canaliScelti,") == 2
    assert "Spunta almeno un canale" in js
    inf = (root / "static" / "os_shell" / "assets" / "views" / "rete-informative.js").read_text(encoding="utf-8")
    assert "name=\"channels\" value=\"${k}\"" in inf and "checked" not in inf
