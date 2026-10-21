"""FOLLOW-UP 180 GIORNI - Fase B su PostgreSQL VERO (schema completo, 108 compresa).

Opt-in con `P29_TEST_DSN`. Lo schema e' quello di `completo` (tutte le
migration tramite il runner), le connessioni di ogni modulo sono dirottate
sul database usa-e-getta, nessuna email parte (nessun provider viene
chiamato: i messaggi si leggono nel ledger).

  A  la 108: oggetti, backfill della v2, append-only degli opt-out, down.
  B  il consenso dell'operatore: niente senza prova, informativa valida,
     identita', agenzia, revoca locale vs globale, nuovo consenso dopo.
  C  il link di disiscrizione con ambito.
  D  la journey v3: bozza, nessun invio senza template, attivazione che ritira
     la v2, platea ed eleggibilita', dedup nella stessa agenzia e fra agenzie,
     tetto giornaliero, momento iniziale.
  E  il calendario completo a 180 giorni, tick per tick.
  F  pausa e ripresa: appuntamenti, consulenza, relazione commerciale, vendita.
  G  stop: opt-out, indirizzo cambiato, contatto archiviato; 48 ore.
  H  concorrenza: due agenzie, stessa email, un'iscrizione sola.
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from tests.test_censimento_3_backend_postgres import (  # noqa: F401  (fixture riusate)
    DSN, _in_parallelo, _q, completo,
)
from tests.test_delete_arch_0_postgres import (  # noqa: F401
    _acquisizione, _collega, _futuro, _immobile, mondo, operatori,
)

pytestmark = pytest.mark.skipif(not os.getenv("P29_TEST_DSN"), reason="P29_TEST_DSN non impostata")

ORA = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)   # martedi'
GIORNO = timedelta(days=1)
SECRET = "u" * 40


# ---------------------------------------------------------------------------
# fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def fb(mondo, monkeypatch):
    """Il mondo di `completo`, con i cursori di dominio sul database, i
    template della v3 finti (solo per i test del motore) e la pulizia delle
    tabelle di Fase B."""
    import psycopg2
    from psycopg2.extras import DictCursor
    from communication import database as comm_db, templates, unsubscribe
    from consent import database as consent_db
    from core import database as core_db
    from operator_auth import database as operator_db
    from seller_intelligence import database as si_db, repository as si_repo

    def connessione():
        return psycopg2.connect(mondo["dsn"], cursor_factory=DictCursor)

    for modulo in list(sys.modules.values()):
        if getattr(modulo, "__name__", "").startswith("tests."):
            continue
        if getattr(modulo, "get_connection", None) is not None:
            try:
                monkeypatch.setattr(modulo, "get_connection", connessione)
            except (AttributeError, TypeError):
                pass
    for aiutante in (comm_db.communication_cursor, consent_db.consent_cursor, core_db.core_cursor,
                     si_db.si_cursor, si_repo.si_cursor, operator_db.operator_cursor):
        globali = getattr(aiutante, "__wrapped__", aiutante).__globals__
        if "get_connection" in globali:
            monkeypatch.setitem(globali, "get_connection", connessione)
    monkeypatch.setenv(unsubscribe.SECRET_ENV, SECRET)
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://test.stima360.it")
    monkeypatch.delenv("COMMUNICATION_REPLY_TO", raising=False)

    def pulisci():
        for sql in ("ALTER TABLE communication_messages DISABLE TRIGGER trg_communication_messages_guard",
                    "DELETE FROM communication_messages",
                    "ALTER TABLE communication_messages ENABLE TRIGGER trg_communication_messages_guard",
                    "DELETE FROM communication_enrollments", "DELETE FROM communication_automation_controls",
                    "DELETE FROM communication_journey_steps", "DELETE FROM communication_journeys",
                    "ALTER TABLE communication_email_optouts DISABLE TRIGGER trg_comm_optouts_append_only",
                    "DELETE FROM communication_email_optouts",
                    "ALTER TABLE communication_email_optouts ENABLE TRIGGER trg_comm_optouts_append_only",
                    # C1/C2 (109): la lista per (canale, indirizzo) e la proiezione per canale.
                    "ALTER TABLE communication_optouts DISABLE TRIGGER trg_comm_optouts2_append_only",
                    "DELETE FROM communication_optouts",
                    "ALTER TABLE communication_optouts ENABLE TRIGGER trg_comm_optouts2_append_only",
                    "DELETE FROM contact_channel_consents",
                    "ALTER TABLE consent_events DISABLE TRIGGER ALL", "DELETE FROM consent_events",
                    "ALTER TABLE consent_events ENABLE TRIGGER ALL",
                    "DELETE FROM consent_notices", "DELETE FROM seller_revival_suppressions",
                    "ALTER TABLE seller_timeline_events DISABLE TRIGGER ALL",
                    "DELETE FROM seller_timeline_events",
                    "ALTER TABLE seller_timeline_events ENABLE TRIGGER ALL",
                    "DELETE FROM platform_audit_log WHERE action LIKE 'platform.consent_notice.%'"):
            try:
                _q(mondo, sql)
            except Exception:  # noqa: BLE001 - una tabella di audit puo' chiamarsi diversamente
                pass

    pulisci()
    m = {**mondo, "connessione": connessione, "templates": templates}
    yield m
    pulisci()


def _ctx(m, chi="owner_a"):
    m["stato"]["chi"] = chi
    return m["ctx"]()


def _sistema(agenzia=1):
    from operator_auth.context import SystemAgencyContext
    return SystemAgencyContext(agency_id=agenzia, origin="communication_dispatch")


def _contatto(m, email, *, agenzia=1, nome=None, consenso=None, quando=None):
    """Un contatto con email; `consenso`:
        None        mai dato
        'legacy'    il vecchio flag TRUE senza eventi: da C2 NON autorizza
        'pre_canale' la spunta del funnel pubblico senza canale: NON autorizza
        'granted'   concessione documentata sul canale EMAIL (funnel pubblico)
        'revoked'   concessione e poi revoca sul canale email

    SENTINELLA AGGIORNATA DA C1/C2: 'granted' e 'revoked' scrivono eventi con
    canale. I test che prima usavano 'legacy' come "contatto con consenso"
    usano 'granted': il flag storico non e' piu' un'autorizzazione.
    """
    nome = nome or f"C {uuid.uuid4().hex[:6]}"
    cid = _q(m, "INSERT INTO contacts (agency_id, display_name, first_name, email, email_normalized, status) "
                "VALUES (%s, %s, %s, %s, %s, 'active') RETURNING id",
             (agenzia, nome, nome.split()[0], email, email.strip().lower()))[0][0]
    quando = quando or (ORA - 10 * GIORNO)
    if consenso == "legacy":
        _q(m, "UPDATE contacts SET marketing_consent = TRUE, marketing_consent_at = %s WHERE id = %s", (quando, cid))
    elif consenso == "pre_canale":
        from consent import service as cs
        cs.record_grant(_sistema(agenzia), contact_id=cid, purpose="marketing", source="public_stima",
                        actor_type="subject", decided_at=quando, idempotency_key=f"t:{cid}:pre")
    elif consenso in ("granted", "revoked"):
        from consent import service as cs
        cs.record_grant(_sistema(agenzia), contact_id=cid, purpose="marketing", channel="email",
                        source="public_stima", actor_type="subject", decided_at=quando,
                        idempotency_key=f"t:{cid}:g")
        if consenso == "revoked":
            cs.record_revocation(_sistema(agenzia), contact_id=cid, purpose="marketing", channel="email",
                                 source="unsubscribe_link", actor_type="subject", decided_at=quando + GIORNO,
                                 idempotency_key=f"t:{cid}:r")
    return cid


def _informativa(m, versione=None, da=None, fino=None, canali=("email", "sms", "whatsapp", "phone")):
    versione = versione or f"v-{uuid.uuid4().hex[:8]}"
    if fino is None:
        _q(m, "UPDATE consent_notices SET valid_to = NOW() WHERE purpose = 'marketing' AND valid_to IS NULL")
    return _q(m, "INSERT INTO consent_notices (purpose, version, content, content_hash, content_available, "
                 "context, valid_from, valid_to, created_by, channels) VALUES ('marketing', %s, "
                 "'Informativa marketing di prova', repeat('a', 64), TRUE, 'test', %s, %s, 'test', %s) RETURNING id",
              (versione, da or ORA - 365 * GIORNO, fino, list(canali)))[0][0]


def _template_finti(m, monkeypatch):
    from communication.templates import BASE_STIMA, Template
    for i in range(1, 18):
        monkeypatch.setitem(m["templates"].REGISTRY, (f"followup_v3_m{i}", 3), Template(
            key=f"followup_v3_m{i}", version=3, channel="email", communication_type="marketing",
            required_fields=BASE_STIMA,
            subject=lambda c, i=i: f"[v3] M{i}",
            body=lambda c, i=i: f"Ciao {c['contact_first_name']}, passo {i}.\n\nStop: {c['unsubscribe_url']}\n"))


def _v3_attiva(m, monkeypatch, agenzia=1, chi="owner_a"):
    from communication import journey_catalog, journey_service
    _template_finti(m, monkeypatch)
    ctx = _ctx(m, chi)
    j = journey_catalog.ensure_followup_v3(ctx)["journey"]
    return journey_service.activate_journey(ctx, j["id"])


def _tick(m, agenzia=1, now=ORA):
    from communication import journey_tick
    return journey_tick.tick(_sistema(agenzia), now=now)


def _iscrizioni(m, **filtro):
    where = " AND ".join(f"{k} = %s" for k in filtro) or "TRUE"
    return [dict(r) for r in _q(m, f"SELECT * FROM communication_enrollments WHERE {where} ORDER BY id",
                                 tuple(filtro.values()))]


def _messaggi(m, enrollment_id):
    return [dict(r) for r in _q(m, "SELECT * FROM communication_messages WHERE enrollment_id = %s ORDER BY step_no",
                                 (enrollment_id,))]


def _spedisci(m, enrollment_id, quando):
    """I `queued` del passo corrente diventano `sent`, come li farebbe il dispatcher."""
    _q(m, "UPDATE communication_messages SET status = 'sent', sent_at = %s "
          "WHERE enrollment_id = %s AND status = 'queued'", (quando, enrollment_id))


def _guardia(m, agenzia, cid, canale="email"):
    from consent.guard import can_send_marketing
    return can_send_marketing(_sistema(agenzia), cid, channel=canale)


# ---------------------------------------------------------------------------
# A  la 108
# ---------------------------------------------------------------------------

def test_a1_la_108_e_applicata_con_i_suoi_oggetti(fb):
    m = fb
    assert _q(m, "SELECT 1 FROM schema_migrations WHERE version = '108_followup_contact_journey'")
    assert _q(m, "SELECT to_regclass('public.communication_email_optouts') IS NOT NULL")[0][0]
    colonne = {r[0] for r in _q(m, "SELECT column_name FROM information_schema.columns "
                                   "WHERE table_name = 'communication_enrollments'")}
    assert {"email_normalized_snapshot", "resume_after", "pause_ref"} <= colonne
    assert _q(m, "SELECT is_nullable FROM information_schema.columns WHERE table_name = "
                 "'communication_enrollments' AND column_name = 'stima_id_snapshot'")[0][0] == "YES"
    assert _q(m, "SELECT 1 FROM pg_indexes WHERE indexname = 'uq_comm_enroll_open_per_email'")


def test_a2_la_v2_resta_repeat_last_e_la_v3_nasce_linear_in_bozza(fb, monkeypatch):
    from communication import journey_catalog
    m = fb
    v2 = journey_catalog.ensure_stima_lead_v2(_ctx(m))["journey"]
    assert v2["sequence_policy"] == "repeat_last" and v2["status"] == "draft"
    _template_finti(m, monkeypatch)
    v3 = journey_catalog.ensure_followup_v3(_ctx(m))
    assert v3["created"] and v3["journey"]["status"] == "draft"
    assert v3["journey"]["sequence_policy"] == "linear" and v3["journey"]["trigger_type"] == "contact_eligible"
    assert len(v3["journey"]["steps"]) == 17
    assert journey_catalog.ensure_followup_v3(_ctx(m))["created"] is False


def test_a3_gli_optout_sono_append_only(fb):
    """SENTINELLA AGGIORNATA DA C1/C2: dalla 109 gli opt-out vivono in
    `communication_optouts` (canale, indirizzo); la tabella 108 e' chiusa in
    scrittura. Le stesse garanzie, sulla tabella nuova."""
    import psycopg2
    m = fb
    _q(m, "INSERT INTO communication_optouts (channel, address_normalized, opted_out_at, source, actor_type, idempotency_key) "
          "VALUES ('email', 'x@y.it', NOW(), 'unsubscribe_link', 'subject', 'k1')")
    with pytest.raises(psycopg2.Error):
        _q(m, "INSERT INTO communication_optouts (channel, address_normalized, opted_out_at, source, actor_type, idempotency_key) "
              "VALUES ('email', 'x@y.it', NOW(), 'crm', 'operator', 'k2')")  # operatore senza actor_ref
    for sql in ("UPDATE communication_optouts SET note = 'x' WHERE idempotency_key = 'k1'",
                "DELETE FROM communication_optouts WHERE idempotency_key = 'k1'"):
        with pytest.raises(psycopg2.Error):
            _q(m, sql)
    with pytest.raises(psycopg2.Error):  # la tabella 108 non accetta piu' righe
        _q(m, "INSERT INTO communication_email_optouts (email_normalized, opted_out_at, source, actor_type, idempotency_key) "
              "VALUES ('x@y.it', NOW(), 'unsubscribe_link', 'subject', 'k3')")


def test_a4_la_down_della_108_si_esegue_e_riporta_lo_schema_a_097(fb):
    """Dentro una transazione poi ANNULLATA: la down gira sullo schema
    completo senza dati di Fase B, toglie gli oggetti, e il database del
    modulo resta con la 108."""
    import psycopg2
    from psycopg2.extras import DictCursor
    from pathlib import Path
    m = fb
    cartella = Path(__file__).resolve().parents[1] / "migrations"
    # SENTINELLA AGGIORNATA DA C1/C2: le down si eseguono in ordine inverso,
    # prima la 109 e poi la 108.
    corpo = "".join(
        (cartella / nome).read_text().replace("BEGIN;", "", 1).rsplit("COMMIT;", 1)[0]
        for nome in ("109_consent_channels_down.sql", "108_followup_contact_journey_down.sql"))
    conn = psycopg2.connect(m["dsn"], cursor_factory=DictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute(corpo)
            cur.execute("SELECT to_regclass('public.communication_email_optouts') IS NULL AS via")
            assert cur.fetchone()["via"]
            cur.execute("SELECT count(*) AS n FROM information_schema.columns WHERE table_name = "
                        "'communication_enrollments' AND column_name IN ('resume_after', 'email_normalized_snapshot', 'pause_ref')")
            assert cur.fetchone()["n"] == 0
            cur.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'comm_journeys_trigger_chk'")
            assert "contact_eligible" not in cur.fetchone()[0]
        conn.rollback()
    finally:
        conn.close()
    assert _q(m, "SELECT to_regclass('public.communication_email_optouts') IS NOT NULL")[0][0]


# ---------------------------------------------------------------------------
# B  il consenso dell'operatore
# ---------------------------------------------------------------------------

def test_b1_niente_consenso_senza_prova_informativa_o_identita(fb):
    from consent import operator_service as s
    from consent.exceptions import ConflictError, NotFoundError, ValidationError
    from core.exceptions import PermissionDenied
    from operator_auth.context import OperatorContext
    m = fb
    cid = _contatto(m, "anna@example.it")
    quando = datetime.now(timezone.utc) - 2 * GIORNO
    buono = dict(decided_at=quando, evidence_type="paper_form", evidence_ref="PROT-2026/0042")
    # senza informativa valida
    with pytest.raises(ValidationError):
        s.register_grant(_ctx(m), cid, channels=["email"], notice_id=999, **buono)
    informativa = _informativa(m)
    # prova = email del contatto, o troppo corta, o modalita' non ammessa
    with pytest.raises(ValidationError):
        s.register_grant(_ctx(m), cid, channels=["email"], notice_id=informativa, decided_at=quando,
                         evidence_type="paper_form", evidence_ref="anna@example.it")
    with pytest.raises(ValidationError):
        s.register_grant(_ctx(m), cid, channels=["email"], notice_id=informativa, decided_at=quando,
                         evidence_type="phone_call", evidence_ref="PROT-2026/0042")
    # informativa non ancora valida alla data di raccolta
    futura = _informativa(m, da=datetime.now(timezone.utc) + 30 * GIORNO,
                          fino=datetime.now(timezone.utc) + 60 * GIORNO)
    with pytest.raises(ValidationError):
        s.register_grant(_ctx(m), cid, channels=["email"], notice_id=futura, **buono)
    # il canale Basic (nessuna persona) non registra
    basic = OperatorContext(user_id=None, agency_id=1, role="agency_owner", is_platform_admin=False,
                            session_id=None, auth_channel="legacy_basic")
    with pytest.raises(PermissionDenied):
        s.register_grant(basic, cid, channels=["email"], notice_id=informativa, **buono)
    # un'altra agenzia non vede il contatto
    with pytest.raises(NotFoundError):
        s.register_grant(_ctx(m, "owner_b"), cid, channels=["email"], notice_id=informativa, **buono)
    assert not _guardia(m, 1, cid).allowed
    assert _q(m, "SELECT count(*) FROM consent_events WHERE contact_id = %s", (cid,))[0][0] == 0

    # un agente vede solo i contatti che gli sono assegnati (scope CORE): questo no
    with pytest.raises(NotFoundError):
        s.register_grant(_ctx(m, "agent_a"), cid, channels=["email"], notice_id=informativa, **buono)
    # il grant buono, dall'admin dell'agenzia
    esito = s.register_grant(_ctx(m, "admin_a"), cid, channels=["email"], notice_id=informativa, note="modulo in archivio B", **buono)
    assert esito["created"] and esito["notice"]["version"].startswith("v-")
    ev = dict(_q(m, "SELECT * FROM consent_events WHERE id = %s", (esito["event"]["id"],))[0]) if False else \
        [dict(zip(("source", "actor_type", "actor_ref", "evidence_type", "evidence_ref", "notice_id", "decided_at"), r))
         for r in _q(m, "SELECT source, actor_type, actor_ref, evidence_type, evidence_ref, notice_id, decided_at "
                        "FROM consent_events WHERE id = %s", (esito["event"]["id"],))][0]
    assert ev["source"] == "crm" and ev["actor_type"] == "operator"
    assert ev["actor_ref"] == f"operator_user:{m['ids']['admin_a']}"
    assert ev["evidence_type"] == "paper_form" and ev["evidence_ref"] == "PROT-2026/0042"
    assert ev["notice_id"] == informativa and ev["decided_at"] == quando
    decisione = _guardia(m, 1, cid)
    assert decisione.allowed and decisione.reason == "allow_explicit_grant" and decisione.source == "crm"
    # idempotente: stessa raccolta, stesso evento
    assert s.register_grant(_ctx(m, "admin_a"), cid, channels=["email"], notice_id=informativa, **buono)["created"] is False
    stato = s.marketing_state(_ctx(m), cid)
    assert stato["allowed"] and stato["channels"]["email"]["last_event"]["evidence_type"] == "paper_form"
    assert stato["can_record"] and not stato["channels"]["sms"]["allowed"], "solo il canale spuntato"
    # contatto nel cestino: niente
    _q(m, "UPDATE contacts SET deleted_at = NOW(), deleted_reason = 'other' WHERE id = %s", (cid,))
    with pytest.raises(ConflictError):
        s.register_grant(_ctx(m), cid, channels=["email"], notice_id=informativa, **buono)


def test_b2_revoca_locale_e_globale_senza_falsificare_le_altre_agenzie(fb):
    from consent import operator_service as s
    from consent.exceptions import ConflictError, ValidationError
    m = fb
    a1 = _contatto(m, "Luca@Example.it", agenzia=1, consenso="granted")
    a2 = _contatto(m, "luca@example.it", agenzia=2, consenso="granted")
    assert _guardia(m, 1, a1).allowed and _guardia(m, 2, a2).allowed
    adesso = datetime.now(timezone.utc)
    with pytest.raises(ValidationError):
        s.register_refusal(_ctx(m), a1, channels=["email"], decided_at=adesso, scope="agency", note="no")
    # LOCALE: solo questa agenzia
    esito = s.register_refusal(_ctx(m), a1, channels=["email"], decided_at=adesso - GIORNO, scope="agency",
                               note="Lo ha detto allo sportello, non vuole piu' email")
    assert esito["created"] and esito["platform_optout"] is None
    assert not _guardia(m, 1, a1).allowed and _guardia(m, 1, a1).reason == "deny_revoked"
    assert _guardia(m, 2, a2).allowed, "la revoca locale non tocca l'altra agenzia"
    assert _q(m, "SELECT count(*) FROM communication_optouts")[0][0] == 0
    # GLOBALE: tutto STIMA360, dichiarato dall'operatore dell'agenzia 2
    esito = s.register_refusal(_ctx(m, "owner_b"), a2, channels=["email"], decided_at=adesso - timedelta(hours=1), scope="platform",
                               note="Ha scritto: basta email da STIMA360, da chiunque")
    assert esito["platform_optout"]["address_normalized"] == "luca@example.it"
    assert esito["platform_optout"]["channel"] == "email"
    assert esito["platform_optout"]["actor_ref"] == f"operator_user:{m['ids']['owner_b']}"
    assert not _guardia(m, 2, a2).allowed
    # l'agenzia 1 non ha nessun evento nuovo, ma non puo' piu' scrivere a quell'indirizzo
    assert _q(m, "SELECT count(*) FROM consent_events WHERE contact_id = %s", (a1,))[0][0] == 2
    d1 = _guardia(m, 1, a1)
    assert not d1.allowed and d1.reason == "deny_revoked"  # la revoca locale resta la ragione
    # un terzo contatto, in agenzia 1, stesso indirizzo, concesso: negato dall'opt-out
    a3 = _contatto(m, "luca@example.it", agenzia=1, consenso="granted")
    d3 = _guardia(m, 1, a3)
    assert not d3.allowed and d3.reason == "deny_platform_optout"
    # un nuovo consenso documentato DOPO l'opt-out torna a valere, per quell'agenzia
    informativa = _informativa(m)
    with pytest.raises(ConflictError):
        s.register_grant(_ctx(m), a3, channels=["email"], decided_at=adesso - 2 * GIORNO, evidence_type="digital_form",
                         evidence_ref="FORM-77", notice_id=informativa)
    s.register_grant(_ctx(m), a3, channels=["email"], decided_at=adesso - timedelta(minutes=10), evidence_type="digital_form",
                     evidence_ref="FORM-77", notice_id=informativa)
    assert _guardia(m, 1, a3).allowed
    assert not _guardia(m, 2, a2).allowed, "l'altra agenzia resta ferma: il suo consenso non e' nuovo"
    assert s.marketing_state(_ctx(m, "owner_b"), a2)["channels"]["email"]["platform_optout_at"] is not None


def test_b3_le_informative_le_pubblica_solo_il_super_admin(fb, monkeypatch):
    from platform_admin import consent_notices_service as ns
    from platform_admin.exceptions import ConsentNoticeConflict
    from platform_admin.super_admin import SuperAdminDenied
    m = fb
    monkeypatch.setenv("STIMA360_SUPER_ADMIN_USER_ID", str(m["ids"]["platform"]))
    adesso = datetime.now(timezone.utc)
    _q(m, "UPDATE consent_notices SET valid_to = NOW() WHERE purpose = 'marketing' AND valid_to IS NULL")
    v1 = f"v-{uuid.uuid4().hex[:8]}"
    with pytest.raises(SuperAdminDenied):
        ns.create_notice(_ctx(m, "owner_a"), purpose="marketing", version=v1, context="crm",
                         content="Testo dell'informativa marketing", valid_from=adesso, valid_to=None, channels=["email"])
    riga = ns.create_notice(_ctx(m, "platform_none"), purpose="marketing", version=v1, context="crm",
                            content="Testo dell'informativa marketing", valid_from=adesso - GIORNO, valid_to=None,
                            channels=["email", "sms"])
    assert riga["content_hash"] and riga["created_by"].startswith("operator:")
    with pytest.raises(ConsentNoticeConflict):   # stessa versione
        ns.create_notice(_ctx(m, "platform_none"), purpose="marketing", version=v1, context="crm",
                         content="Altro testo", valid_from=adesso, valid_to=None, channels=["email"])
    with pytest.raises(ConsentNoticeConflict):   # un'altra corrente mentre questa e' aperta
        ns.create_notice(_ctx(m, "platform_none"), purpose="marketing", version=v1 + "b", context="crm",
                         content="Altro testo ancora", valid_from=adesso, valid_to=None, channels=["email"])
    from consent import operator_service as s
    assert [n["id"] for n in s.list_notices()][:1] == [riga["id"]]
    chiusa = ns.close_notice(_ctx(m, "platform_none"), riga["id"])
    assert chiusa["valid_to"] is not None and riga["id"] not in [n["id"] for n in s.list_notices()]
    assert _q(m, "SELECT count(*) FROM platform_audit_log WHERE action LIKE 'platform.consent_notice.%%'")[0][0] >= 2


# ---------------------------------------------------------------------------
# C  disiscrizione con ambito
# ---------------------------------------------------------------------------

def test_c1_il_link_di_piattaforma_registra_l_optout_quello_di_agenzia_no(fb):
    from communication import unsubscribe as u
    m = fb
    a1 = _contatto(m, "gina@example.it", agenzia=1, consenso="granted")
    a2 = _contatto(m, "gina@example.it", agenzia=2, consenso="granted")
    assert u.process(u.issue(1, a1)) is True
    assert not _guardia(m, 1, a1).allowed and _guardia(m, 2, a2).allowed
    assert _q(m, "SELECT count(*) FROM communication_optouts")[0][0] == 0
    token = u.issue(2, a2, scope=u.SCOPE_PLATFORM)
    assert u.process(token) is True and u.process(token) is True  # idempotente
    righe = _q(m, "SELECT channel, source, actor_type, source_agency_id, source_contact_id FROM communication_optouts")
    assert [tuple(r) for r in righe] == [("email", "unsubscribe_link", "subject", 2, a2)]
    assert not _guardia(m, 2, a2).allowed
    a3 = _contatto(m, "gina@example.it", agenzia=1, consenso="granted")
    assert _guardia(m, 1, a3).reason == "deny_platform_optout"


# ---------------------------------------------------------------------------
# D  la v3: attivazione, platea, dedup, tetto
# ---------------------------------------------------------------------------

def test_d1_senza_template_la_v3_non_nasce_e_in_bozza_non_iscrive(fb, monkeypatch):
    from communication import journey_catalog
    from communication.exceptions import ValidationError
    m = fb
    _contatto(m, "p@example.it", consenso="granted")
    with pytest.raises(ValidationError):
        journey_catalog.ensure_followup_v3(_ctx(m))
    assert _q(m, "SELECT count(*) FROM communication_journeys")[0][0] == 0
    _template_finti(m, monkeypatch)
    journey_catalog.ensure_followup_v3(_ctx(m))
    conteggi = _tick(m)
    assert conteggi["enrolled_active"] == 0 and _iscrizioni(m) == []


def test_d2_attivare_la_v3_ritira_la_v2_e_iscrive_solo_gli_eleggibili(fb, monkeypatch):
    from communication import journey_catalog, journey_service
    m = fb
    v2 = journey_catalog.ensure_stima_lead_v2(_ctx(m))["journey"]
    journey_service.activate_journey(_ctx(m), v2["id"])
    v3 = _v3_attiva(m, monkeypatch)
    assert v3["status"] == "active"
    assert _q(m, "SELECT status FROM communication_journeys WHERE id = %s", (v2["id"],))[0][0] == "retired"

    ok_esplicito = _contatto(m, "a@example.it", consenso="granted", quando=ORA - 2 * GIORNO)
    # SENTINELLA AGGIORNATA DA C1/C2: lo "storico" che entra e' un consenso
    # email DOCUMENTATO vecchio; il vecchio flag senza eventi resta fuori.
    ok_legacy = _contatto(m, "b@example.it", consenso="granted", quando=ORA - 400 * GIORNO)
    solo_flag = _contatto(m, "flag@example.it", consenso="legacy", quando=ORA - 400 * GIORNO)
    spunta = _contatto(m, "spunta@example.it", consenso="pre_canale", quando=ORA - 3 * GIORNO)
    mai = _contatto(m, "c@example.it")
    revocato = _contatto(m, "d@example.it", consenso="revoked")
    doppio = _contatto(m, "A@example.it", consenso="granted", quando=ORA - 30 * GIORNO)   # stessa email di ok_esplicito
    senza_email = _q(m, "INSERT INTO contacts (agency_id, display_name, first_name, status) VALUES (1, 'X Y', 'X', 'active') RETURNING id")[0][0]
    archiviato = _contatto(m, "e@example.it", consenso="granted")
    _q(m, "UPDATE contacts SET status = 'archived', archived_at = NOW() WHERE id = %s", (archiviato,))
    in_pausa = _contatto(m, "f@example.it", consenso="granted")
    journey_service.pause_automations(_ctx(m), in_pausa, reason="non ora")
    in_revival = _contatto(m, "g@example.it", consenso="granted")
    _q(m, "INSERT INTO seller_revival_suppressions (contact_id, created_at, expires_at) VALUES (%s, NOW(), NOW() + interval '30 days')", (in_revival,))
    altrove = _contatto(m, "h@example.it", agenzia=2, consenso="granted")  # agenzia 2: non tocca l'agenzia 1

    conteggi = _tick(m)
    assert conteggi["enrolled_active"] == 2 and conteggi["enrolled_stopped"] == 0, conteggi
    iscritti = {e["contact_id"]: e for e in _iscrizioni(m, agency_id=1)}
    assert set(iscritti) == {ok_esplicito, ok_legacy}
    # momento iniziale: la data del consenso se recente, altrimenti l'ammissione
    assert iscritti[ok_esplicito]["trigger_sent_at"] == ORA - 2 * GIORNO
    assert iscritti[ok_legacy]["trigger_sent_at"] == ORA
    assert iscritti[ok_esplicito]["email_normalized_snapshot"] == "a@example.it"
    assert iscritti[ok_esplicito]["next_step_no"] == 2, "il giorno 1 e' gia' passato: si parte dal giorno 3"
    assert iscritti[ok_legacy]["next_step_no"] == 1
    for c in (mai, revocato, doppio, senza_email, archiviato, in_pausa, in_revival, altrove, solo_flag, spunta):
        assert c not in iscritti
    # un secondo giro non raddoppia niente
    assert _tick(m)["enrolled_active"] == 0 and len(_iscrizioni(m)) == 2


def test_d3_una_email_una_sequenza_in_tutta_la_piattaforma_e_tetto_giornaliero(fb, monkeypatch):
    m = fb
    _v3_attiva(m, monkeypatch, agenzia=1, chi="owner_a")
    _v3_attiva(m, monkeypatch, agenzia=2, chi="owner_b")
    _contatto(m, "comune@example.it", agenzia=1, consenso="granted")
    _contatto(m, "comune@example.it", agenzia=2, consenso="granted")
    for i in range(24):
        _contatto(m, f"lotto{i}@example.it", agenzia=1, consenso="granted")
    c1 = _tick(m, 1)
    assert c1["enrolled_active"] == 20, c1
    c2 = _tick(m, 2)
    assert c2["enrolled_active"] == 0 and c2["enrolled_skipped"] == 0, "l'indirizzo e' gia' in sequenza nell'agenzia 1"
    assert len(_iscrizioni(m, agency_id=2)) == 0
    # il giorno dopo entrano gli altri 5 (24 + comune - 20)
    assert _tick(m, 1, now=ORA + GIORNO)["enrolled_active"] == 5
    assert _tick(m, 1, now=ORA + GIORNO)["enrolled_active"] == 0
    assert _q(m, "SELECT count(DISTINCT email_normalized_snapshot) FROM communication_enrollments")[0][0] == 25


# ---------------------------------------------------------------------------
# E  il calendario a 180 giorni
# ---------------------------------------------------------------------------

def test_e1_diciassette_email_nei_giorni_approvati_poi_completata_senza_ripartire(fb, monkeypatch):
    from communication.journey_catalog import FOLLOWUP_V3_DAYS
    m = fb
    _v3_attiva(m, monkeypatch)
    cid = _contatto(m, "cal@example.it", consenso="granted")
    inviati = []
    for giorno in range(0, 185):
        adesso = ORA + giorno * GIORNO
        _tick(m, now=adesso)
        iscr = _iscrizioni(m, contact_id=cid)
        if not iscr:
            continue
        for msg in _messaggi(m, iscr[0]["id"]):
            if msg["status"] == "queued" and msg["scheduled_at"] <= adesso + timedelta(hours=12):
                _spedisci(m, iscr[0]["id"], msg["scheduled_at"])
                inviati.append((msg["step_no"], msg["scheduled_at"]))
        _tick(m, now=adesso)  # avanza al passo successivo nello stesso giorno
    iscr = _iscrizioni(m, contact_id=cid)[0]
    assert [n for n, _ in inviati] == list(range(1, 18))
    for (n, quando), atteso in zip(inviati, FOLLOWUP_V3_DAYS):
        scarto = (quando - ORA) - atteso * GIORNO
        assert timedelta(0) <= scarto <= 2 * GIORNO, (n, quando, atteso)  # finestra: domenica -> lunedi'
    assert iscr["status"] == "completed" and iscr["run_no"] == 1
    assert len(_messaggi(m, iscr["id"])) == 17
    # nessuna ripartenza: altri giri non producono niente
    for giorno in (190, 200):
        _tick(m, now=ORA + giorno * GIORNO)
    assert len(_messaggi(m, iscr["id"])) == 17 and len(_iscrizioni(m, contact_id=cid)) == 1, "nessuna ripartenza, mai"
    # il link di disiscrizione delle email e' quello di PIATTAFORMA
    from communication import unsubscribe as u
    corpo = _messaggi(m, iscr["id"])[0]["rendered_body"]
    token = corpo.split("?t=", 1)[1].split()[0]
    assert u.verify_scoped(token)["scope"] == "platform"
    assert _messaggi(m, iscr["id"])[0]["metadata"]["reply_to"] == "info@stima360.it"


# ---------------------------------------------------------------------------
# F  pausa e ripresa
# ---------------------------------------------------------------------------

def _appuntamento(m, cid, *, inizio, tipo="seller_meeting", stato="scheduled"):
    return _q(m, "INSERT INTO appointments (agency_id, assigned_user_id, appointment_type, status, start_at, end_at, contact_id) "
                 "VALUES (1, %s, %s, %s, %s, %s, %s) RETURNING id",
              (m["ids"]["owner_a"], tipo, stato, inizio, inizio + timedelta(hours=1), cid))[0][0]


def _iscrivi(m, monkeypatch, email, now=ORA):
    _v3_attiva(m, monkeypatch)
    cid = _contatto(m, email, consenso="granted")
    _tick(m, now=now)
    return cid, _iscrizioni(m, contact_id=cid)[0]


def test_f1_un_appuntamento_sospende_e_la_ripresa_e_dal_prossimo_passo(fb, monkeypatch):
    m = fb
    cid, e = _iscrivi(m, monkeypatch, "app@example.it")
    # giorno 1: parte M1
    _tick(m, now=ORA + GIORNO); _spedisci(m, e["id"], ORA + GIORNO); _tick(m, now=ORA + GIORNO)
    assert _iscrizioni(m, contact_id=cid)[0]["next_step_no"] == 2
    # giorno 2: un appuntamento fissato per il giorno 5 -> pausa, il queued di M2 (giorno 3) non c'e'
    app = _appuntamento(m, cid, inizio=ORA + 5 * GIORNO)
    _tick(m, now=ORA + 2 * GIORNO)
    e = _iscrizioni(m, contact_id=cid)[0]
    assert e["status"] == "paused" and e["paused_source"] == "appointment" and e["resume_after"] is None
    assert e["paused_by_operator_user_id"] is None and e["pause_ref"] == f"appointment:{app}"
    _tick(m, now=ORA + 3 * GIORNO)
    assert not [x for x in _messaggi(m, e["id"]) if x["status"] == "queued"], "in pausa non si accoda"
    # giorno 5: svolto -> pausa fino a +14 (giorno 19)
    _q(m, "UPDATE appointments SET status = 'completed', completed_at = %s WHERE id = %s", (ORA + 5 * GIORNO + timedelta(hours=1), app))
    _tick(m, now=ORA + 6 * GIORNO)
    e = _iscrizioni(m, contact_id=cid)[0]
    assert e["status"] == "paused" and e["resume_after"] == ORA + 19 * GIORNO + timedelta(hours=1)
    _tick(m, now=ORA + 18 * GIORNO)
    assert _iscrizioni(m, contact_id=cid)[0]["status"] == "paused"
    # giorno 20: riprende dal prossimo passo in calendario (21), saltando 3, 6, 10, 14
    _tick(m, now=ORA + 20 * GIORNO)
    e = _iscrizioni(m, contact_id=cid)[0]
    assert e["status"] == "active" and e["next_step_no"] == 6 and e["run_no"] == 2
    assert e["next_action_at"].date() == (ORA + 21 * GIORNO).date()
    assert [x["step_no"] for x in _messaggi(m, e["id"]) if x["status"] == "sent"] == [1]
    # annullato -> +2 giorni
    app2 = _appuntamento(m, cid, inizio=ORA + 25 * GIORNO)
    _tick(m, now=ORA + 20 * GIORNO + timedelta(hours=1))
    assert _iscrizioni(m, contact_id=cid)[0]["status"] == "paused"
    _q(m, "UPDATE appointments SET status = 'cancelled', cancelled_at = %s, cancelled_kind = 'client' WHERE id = %s",
       (ORA + 21 * GIORNO, app2))
    _tick(m, now=ORA + 22 * GIORNO)
    e = _iscrizioni(m, contact_id=cid)[0]
    assert e["status"] == "paused" and e["resume_after"] == ORA + 23 * GIORNO
    _tick(m, now=ORA + 23 * GIORNO + timedelta(hours=1))
    e = _iscrizioni(m, contact_id=cid)[0]
    assert e["status"] == "active" and e["next_step_no"] == 7  # giorno 28


def test_f2_consulenza_relazione_commerciale_e_vendita(fb, monkeypatch):
    m = fb
    cid, e = _iscrivi(m, monkeypatch, "comm@example.it")
    # consulenza richiesta: 14 giorni
    _q(m, "INSERT INTO seller_timeline_events (agency_id, contact_id, event_type, event_source, occurred_at, idempotency_key, created_by) "
          "VALUES (1, %s, 'owner_consultation_requested', 'owner_portal', %s, %s, 'test')", (cid, ORA + GIORNO, f"cons:{cid}"))
    _tick(m, now=ORA + 2 * GIORNO)
    e = _iscrizioni(m, contact_id=cid)[0]
    assert e["status"] == "paused" and e["paused_source"] == "consultation" and e["resume_after"] == ORA + 15 * GIORNO
    # un incarico (acquisizione aperta) tiene ferma la sequenza finche' dura, poi +2 giorni
    casa = _immobile(m)["id"]
    _collega(m, casa, cid, "owner")
    acq = _acquisizione(m, casa, cid)   # porta anche un appuntamento fissato nel futuro
    _tick(m, now=ORA + 16 * GIORNO)
    e = _iscrizioni(m, contact_id=cid)[0]
    assert e["status"] == "paused" and e["paused_source"] in ("appointment", "commercial") and e["resume_after"] is None
    _q(m, "UPDATE appointments SET status = 'cancelled', cancelled_at = %s, cancelled_kind = 'agency' WHERE agency_id = 1 AND contact_id = %s",
       (ORA + 16 * GIORNO, cid))
    _tick(m, now=ORA + 19 * GIORNO)
    e = _iscrizioni(m, contact_id=cid)[0]
    assert e["status"] == "paused" and e["paused_source"] == "commercial" and e["resume_after"] is None
    _q(m, "ALTER TABLE acquisitions DISABLE TRIGGER ALL")
    _q(m, "UPDATE acquisitions SET status = 'lost', lost_reason = 'other', lost_at = %s, updated_at = %s WHERE id = %s",
       (ORA + 30 * GIORNO, ORA + 30 * GIORNO, acq["id"]))
    _q(m, "ALTER TABLE acquisitions ENABLE TRIGGER ALL")
    _q(m, "UPDATE properties SET updated_at = %s WHERE id = %s", (ORA + 30 * GIORNO, casa))
    _tick(m, now=ORA + 31 * GIORNO)
    e = _iscrizioni(m, contact_id=cid)[0]
    assert e["status"] == "paused" and e["resume_after"] == ORA + 32 * GIORNO
    _tick(m, now=ORA + 33 * GIORNO)
    e = _iscrizioni(m, contact_id=cid)[0]
    assert e["status"] == "active" and e["next_step_no"] == 8  # giorno 38
    # immobile venduto: 60 giorni dalla vendita
    _q(m, "ALTER TABLE properties DISABLE TRIGGER ALL")
    _q(m, "UPDATE properties SET commercial_status = 'sold', updated_at = %s WHERE id = %s", (ORA + 40 * GIORNO, casa))
    _q(m, "ALTER TABLE properties ENABLE TRIGGER ALL")
    _tick(m, now=ORA + 41 * GIORNO)
    e = _iscrizioni(m, contact_id=cid)[0]
    assert e["status"] == "paused" and e["paused_source"] == "commercial" and e["resume_after"] == ORA + 100 * GIORNO
    _tick(m, now=ORA + 101 * GIORNO)
    e = _iscrizioni(m, contact_id=cid)[0]
    assert e["status"] == "active" and e["next_step_no"] == 13  # giorno 105
    # la pausa dell'OPERATORE non viene tolta dal sistema
    from communication import journey_service
    journey_service.pause_enrollment(_ctx(m), e["id"])
    _tick(m, now=ORA + 102 * GIORNO)
    assert _iscrizioni(m, contact_id=cid)[0]["status"] == "paused"
    ripresa = journey_service.resume_enrollment(_ctx(m), e["id"], now=ORA + 150 * GIORNO)
    assert ripresa["status"] == "active" and ripresa["next_step_no"] == 16  # giorno 160, mai gli arretrati
    assert journey_service.resume_enrollment(_ctx(m), journey_service.pause_enrollment(_ctx(m), e["id"])["id"],
                                             now=ORA + 181 * GIORNO)["status"] == "completed"


# ---------------------------------------------------------------------------
# G  stop e 48 ore
# ---------------------------------------------------------------------------

def test_g1_optout_indirizzo_cambiato_e_contatto_archiviato_fermano(fb, monkeypatch):
    from communication import unsubscribe as u
    m = fb
    _v3_attiva(m, monkeypatch)
    opt = _contatto(m, "opt@example.it", consenso="granted")
    cambia = _contatto(m, "cambia@example.it", consenso="granted")
    arch = _contatto(m, "arch@example.it", consenso="granted")
    _tick(m)
    _tick(m, now=ORA + GIORNO)   # M1 in coda per tutti
    per = {e["contact_id"]: e for e in _iscrizioni(m)}
    assert all(any(x["status"] == "queued" for x in _messaggi(m, e["id"])) for e in per.values())
    u.process(u.issue(1, opt, scope=u.SCOPE_PLATFORM))
    _q(m, "UPDATE contacts SET email = 'nuovo@example.it', email_normalized = 'nuovo@example.it' WHERE id = %s", (cambia,))
    _q(m, "UPDATE contacts SET status = 'archived', archived_at = NOW() WHERE id = %s", (arch,))
    _tick(m, now=ORA + GIORNO + timedelta(hours=1))
    per = {e["contact_id"]: e for e in _iscrizioni(m)}
    assert per[opt]["status"] == "stopped" and per[opt]["stop_reason"] in ("consent_revoked", "platform_optout")
    assert _q(m, "SELECT count(*) FROM communication_optouts WHERE channel = 'email' "
                 "AND address_normalized = 'opt@example.it'")[0][0] == 1
    assert per[cambia]["status"] == "stopped" and per[cambia]["stop_reason"] == "duplicate_email"
    assert per[arch]["status"] == "stopped" and per[arch]["stop_reason"] == "contact_inactive"
    for e in per.values():
        assert all(x["status"] == "cancelled" for x in _messaggi(m, e["id"])), "i queued si annullano"


def test_g2_un_manuale_di_marketing_fa_slittare_il_passo_di_48_ore(fb, monkeypatch):
    m = fb
    cid, e = _iscrivi(m, monkeypatch, "gap@example.it")
    _q(m, "INSERT INTO communication_messages (agency_id, contact_id, channel, direction, communication_type, mode, "
          "reason_code, subject_snapshot, rendered_body, destination_snapshot, status, scheduled_at, sent_at, actor_type, actor_user_id, idempotency_key) "
          "VALUES (1, %s, 'email', 'outbound', 'marketing', 'manual', 'operator_manual', 'O', 'c', 'gap@example.it', 'sent', %s, %s, 'operator', %s, %s)",
       (cid, ORA + GIORNO - timedelta(hours=6), ORA + GIORNO - timedelta(hours=6), m["ids"]["owner_a"], f"man:{cid}"))
    conteggi = _tick(m, now=ORA + GIORNO)
    assert conteggi["deferred"] == 1 and _messaggi(m, e["id"]) == []
    e = _iscrizioni(m, contact_id=cid)[0]
    assert e["next_action_at"] >= ORA + GIORNO + timedelta(hours=42)
    _tick(m, now=ORA + 3 * GIORNO)
    assert [x["step_no"] for x in _messaggi(m, e["id"])] == [1]


# ---------------------------------------------------------------------------
# H  concorrenza
# ---------------------------------------------------------------------------

def test_h1_due_agenzie_in_parallelo_stessa_email_una_sola_iscrizione(fb, monkeypatch):
    m = fb
    _v3_attiva(m, monkeypatch, agenzia=1, chi="owner_a")
    _v3_attiva(m, monkeypatch, agenzia=2, chi="owner_b")
    for i in range(6):
        _contatto(m, f"gara{i}@example.it", agenzia=1, consenso="granted")
        _contatto(m, f"gara{i}@example.it", agenzia=2, consenso="granted")
    esiti = _in_parallelo([lambda: _tick(m, 1), lambda: _tick(m, 2)])
    assert sum(c["enrolled_active"] for c in esiti) + 0 == 6, esiti
    assert sum(c["errors"] for c in esiti) == 0
    assert _q(m, "SELECT count(DISTINCT email_normalized_snapshot) FROM communication_enrollments WHERE status = 'active'")[0][0] == 6
    assert _q(m, "SELECT count(*) FROM communication_enrollments")[0][0] == 6


# ---------------------------------------------------------------------------
# I  i due casi di verifica prima del commit
# ---------------------------------------------------------------------------

def _completa(m, enrollment_id):
    _q(m, "UPDATE communication_enrollments SET status = 'completed', completed_at = NOW(), next_step_no = NULL, "
          "next_action_at = NULL, next_action_kind = NULL, awaiting_since = NULL WHERE id = %s", (enrollment_id,))


def test_i1_un_ciclo_completato_in_A_vieta_per_sempre_una_seconda_sequenza_in_B(fb, monkeypatch):
    import psycopg2
    m = fb
    _v3_attiva(m, monkeypatch, agenzia=1, chi="owner_a")
    _v3_attiva(m, monkeypatch, agenzia=2, chi="owner_b")
    a = _contatto(m, "fatto@example.it", agenzia=1, consenso="granted")
    assert _tick(m, 1)["enrolled_active"] == 1
    _completa(m, _iscrizioni(m, contact_id=a)[0]["id"])
    # molto dopo, con una concessione NUOVA ed esplicita, in B: niente
    b = _contatto(m, "Fatto@Example.it", agenzia=2, consenso="granted", quando=ORA + 300 * GIORNO)
    c2 = _tick(m, 2, now=ORA + 301 * GIORNO)
    assert c2["enrolled_active"] == 0 and c2["enrolled_stopped"] == 0 and c2["errors"] == 0, c2
    assert _iscrizioni(m, contact_id=b) == []
    assert _guardia(m, 2, b).allowed, "il consenso in B e' valido: e' la SEQUENZA a non ripartire"
    # ne' in A, per lo stesso contatto, con una concessione nuova
    assert _tick(m, 1, now=ORA + 301 * GIORNO)["enrolled_active"] == 0
    # e il database lo rifiuta anche a chi aggira la platea
    j2 = _q(m, "SELECT id FROM communication_journeys WHERE agency_id = 2 AND version = 3")[0][0]
    with pytest.raises(psycopg2.Error, match="already completed"):
        _q(m, "INSERT INTO communication_enrollments (agency_id, journey_id, contact_id, trigger_sent_at, "
              "email_normalized_snapshot, status, next_step_no, next_action_at, next_action_kind, enrolled_by_type, idempotency_key) "
              "VALUES (2, %s, %s, NOW(), 'fatto@example.it', 'active', 1, NOW(), 'enqueue', 'system', 'forzato')", (j2, b))
    assert _q(m, "SELECT count(*) FROM communication_enrollments WHERE email_normalized_snapshot = 'fatto@example.it'")[0][0] == 1


def test_i2_completamento_in_A_e_tick_di_B_in_parallelo_mai_una_seconda_sequenza(fb, monkeypatch):
    m = fb
    _v3_attiva(m, monkeypatch, agenzia=1, chi="owner_a")
    _v3_attiva(m, monkeypatch, agenzia=2, chi="owner_b")
    for i in range(6):
        _contatto(m, f"corsa{i}@example.it", agenzia=1, consenso="granted")
        _contatto(m, f"corsa{i}@example.it", agenzia=2, consenso="granted")
    assert _tick(m, 1)["enrolled_active"] == 6
    aperte = [e["id"] for e in _iscrizioni(m, agency_id=1)]

    def completa_tutte():
        for i in aperte:
            _completa(m, i)
        return "ok"

    esiti = _in_parallelo([lambda: _tick(m, 2), completa_tutte, lambda: _tick(m, 2)])
    assert all(c["errors"] == 0 for c in esiti if isinstance(c, dict)), esiti
    assert _tick(m, 2, now=ORA + GIORNO)["enrolled_active"] == 0
    assert _q(m, "SELECT count(*) FROM communication_enrollments WHERE agency_id = 2")[0][0] == 0
    assert _q(m, "SELECT count(*) FROM communication_enrollments WHERE status = 'completed'")[0][0] == 6


def test_i3_dopo_una_disiscrizione_globale_B_non_riprende_su_un_consenso_vecchio(fb, monkeypatch):
    from communication import unsubscribe as u
    from consent import operator_service as s
    m = fb
    _v3_attiva(m, monkeypatch, agenzia=1, chi="owner_a")
    _v3_attiva(m, monkeypatch, agenzia=2, chi="owner_b")
    a = _contatto(m, "due@example.it", agenzia=1, consenso="granted", quando=ORA - 30 * GIORNO)
    b = _contatto(m, "due@example.it", agenzia=2, consenso="granted", quando=ORA - 20 * GIORNO)
    assert _tick(m, 2)["enrolled_active"] == 1          # B parte per prima (consenso piu' recente)
    eb = _iscrizioni(m, contact_id=b)[0]
    # disiscrizione GLOBALE dal link della sequenza di B
    assert u.process(u.issue(2, b, scope=u.SCOPE_PLATFORM))
    _tick(m, 2, now=ORA + timedelta(hours=1))
    eb = _iscrizioni(m, contact_id=b)[0]
    assert eb["status"] == "stopped" and eb["stop_reason"] in ("consent_revoked", "platform_optout")
    assert not _guardia(m, 1, a).allowed and _guardia(m, 1, a).reason == "deny_platform_optout"
    assert _tick(m, 1, now=ORA + timedelta(hours=2))["enrolled_active"] == 0
    # nuovo consenso DOCUMENTATO ad A, dopo la disiscrizione
    informativa = _informativa(m)
    adesso = datetime.now(timezone.utc)
    # datato PRIMA della disiscrizione: rifiutato; DOPO: accettato
    from consent.exceptions import ConflictError
    with pytest.raises(ConflictError):
        s.register_grant(_ctx(m, "owner_a"), a, channels=["email"], decided_at=adesso - 5 * GIORNO, evidence_type="paper_form",
                         evidence_ref="PROT-2026/0099", notice_id=informativa)
    s.register_grant(_ctx(m, "owner_a"), a, channels=["email"], decided_at=adesso + timedelta(minutes=1), evidence_type="paper_form",
                     evidence_ref="PROT-2026/0099", notice_id=informativa)
    assert _guardia(m, 1, a).allowed
    dopo = adesso + timedelta(minutes=2)
    assert _tick(m, 1, now=dopo)["enrolled_active"] == 1, "A puo' partire: consenso nuovo, successivo all'opt-out"
    # B: il suo consenso e' PRECEDENTE alla disiscrizione -> niente, mai
    db = _guardia(m, 2, b)
    assert not db.allowed and db.reason == "deny_revoked"
    for giorno in (0, 1, 30):
        c = _tick(m, 2, now=dopo + giorno * GIORNO)
        assert c["enrolled_active"] == 0 and c["errors"] == 0, c
    assert [e["status"] for e in _iscrizioni(m, contact_id=b)] == ["stopped"]
    # un terzo contatto in B, stesso indirizzo, consenso email vecchio (prima dell'opt-out): negato
    b2 = _contatto(m, "due@example.it", agenzia=2, consenso="granted", quando=ORA - 40 * GIORNO)
    d2 = _guardia(m, 2, b2)
    assert not d2.allowed and d2.reason == "deny_platform_optout"
    assert _tick(m, 2, now=dopo + 2 * GIORNO)["enrolled_active"] == 0
    assert _q(m, "SELECT count(*) FROM communication_enrollments WHERE agency_id = 2 AND status IN ('active','paused')")[0][0] == 0
    # un messaggio MANUALE di marketing da B: rifiutato dal consenso
    from communication import contact_view
    from communication.exceptions import ValidationError
    with pytest.raises(ValidationError):
        contact_view.send_manual(_ctx(m, "owner_b"), b, subject="Ciao", body="x", communication_type="marketing")


def test_i4_nuovo_consenso_in_A_e_tick_di_B_in_parallelo(fb, monkeypatch):
    from communication import unsubscribe as u
    from consent import operator_service as s
    m = fb
    _v3_attiva(m, monkeypatch, agenzia=1, chi="owner_a")
    _v3_attiva(m, monkeypatch, agenzia=2, chi="owner_b")
    coppie = []
    for i in range(5):
        a = _contatto(m, f"gara2{i}@example.it", agenzia=1, consenso="granted", quando=ORA - 30 * GIORNO)
        b = _contatto(m, f"gara2{i}@example.it", agenzia=2, consenso="granted", quando=ORA - 20 * GIORNO)
        coppie.append((a, b))
        assert u.process(u.issue(1, a, scope=u.SCOPE_PLATFORM))
    informativa = _informativa(m)
    adesso = datetime.now(timezone.utc)

    def concedi():
        for a, _ in coppie:
            s.register_grant(_ctx(m, "owner_a"), a, channels=["email"], decided_at=adesso + timedelta(minutes=1),
                             evidence_type="digital_form", evidence_ref=f"FORM-{a}", notice_id=informativa)
        return "ok"

    tardi = adesso + timedelta(minutes=2)
    esiti = _in_parallelo([lambda: _tick(m, 2, now=tardi), concedi, lambda: _tick(m, 2, now=tardi),
                           lambda: _tick(m, 1, now=tardi)])
    assert all(c["errors"] == 0 for c in esiti if isinstance(c, dict)), esiti
    assert _tick(m, 2, now=tardi + GIORNO)["enrolled_active"] == 0
    assert _q(m, "SELECT count(*) FROM communication_enrollments WHERE agency_id = 2")[0][0] == 0
    _tick(m, 1, now=tardi + GIORNO)
    assert _q(m, "SELECT count(*) FROM communication_enrollments WHERE agency_id = 1 AND status = 'active'")[0][0] == 5
