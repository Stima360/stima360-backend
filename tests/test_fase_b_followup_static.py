"""FOLLOW-UP 180 GIORNI - Fase B, prove STATICHE (senza database, senza rete).

  * gli insiemi chiusi: trigger `contact_eligible`, policy di sequenza, pause
    di sistema, ragioni di stop della journey sul contatto, `m1`..`m20`;
  * il catalogo v3: 17 passi ai giorni approvati, tutti automatici, tutti
    misurati dal momento iniziale, in bozza e MAI attivabili da qui;
  * le regole: prossimo passo in calendario (niente arretrati, 48 ore,
    orizzonte 180), email scrivibile;
  * la guardia: l'opt-out di piattaforma nega solo se successivo alla
    concessione, per il grant esplicito e per il legacy;
  * il consenso dell'operatore: niente senza prova, prova mai uguale a email o
    telefono, data mai nel futuro, nessuna identita' -> nessuna registrazione;
  * il link di disiscrizione con ambito: v1 identico, v2 firmato con l'ambito;
  * la migration 108: valida per il runner, con la down, ultima della serie;
  * i confini: nessuna lettura trasversale fuori dal repository, la platea non
    nomina la guardia ne' le colonne del consenso; la scheda contatto manda
    solo i campi del consenso, mai l'agenzia o l'indirizzo.
"""
from __future__ import annotations

import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ORA = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


def _codice(percorso: Path) -> str:
    testo = percorso.read_text(encoding="utf-8")
    testo = re.sub(r'"{3}[\s\S]*?"{3}', "", testo)
    return re.sub(r"#[^\n]*", "", testo)


# ---------------------------------------------------------------------------
# A  insiemi chiusi
# ---------------------------------------------------------------------------

def test_a1_gli_insiemi_della_journey_sul_contatto():
    from communication import enums, journey_enums as e
    assert e.TRIGGER_CONTACT_ELIGIBLE in e.TRIGGER_TYPES
    assert e.SEQUENCE_POLICIES == {"linear", "repeat_last"}
    assert e.SYSTEM_PAUSE_SOURCES == {"appointment", "commercial", "consultation"}
    assert e.STEP_REASON_CODES == tuple(f"m{i}" for i in range(1, 21))
    assert {f"m{i}" for i in range(1, 21)} <= enums.REASON_CODES
    # la priorita' delle v1/v2 e' INTATTA (11 ragioni, stesso ordine)
    assert len(e.STOP_PRIORITY) == 11 and e.STOP_PRIORITY[0] == "mandate_signed"
    # quella sul contatto non conosce incarichi, sopralluoghi, lead chiusi
    assert set(e.CONTACT_STOP_PRIORITY).isdisjoint({"mandate_signed", "acquisition_linked",
                                                     "inspection", "consultation_requested",
                                                     "lead_closed"})
    assert e.CONTACT_STOP_PRIORITY[0] == "consent_revoked" and e.CONTACT_STOP_PRIORITY[-1] == "operator"
    assert {"platform_optout", "email_invalid", "duplicate_email"} <= e.STOP_REASONS
    assert e.choose_stop_reason({"operator", "email_invalid", "platform_optout"},
                                e.CONTACT_STOP_PRIORITY) == "platform_optout"
    assert e.CONSENT_STOP_BY_GUARD_REASON["deny_platform_optout"] == "platform_optout"
    # e il CHECK della 108 le conosce tutte
    su = (ROOT / "migrations" / "108_followup_contact_journey.sql").read_text(encoding="utf-8")
    blocco = su.split("ADD CONSTRAINT comm_enroll_stop_reason_chk")[1].split("ALTER TABLE")[0]
    for r in (*e.STOP_PRIORITY, "request_too_old", *e.CONTACT_STOP_PRIORITY):
        assert f"'{r}'" in blocco, r


# ---------------------------------------------------------------------------
# B  il catalogo v3
# ---------------------------------------------------------------------------

def test_b1_il_calendario_v3_e_quello_approvato():
    from communication import journey_catalog as c
    assert c.FOLLOWUP_V3_DAYS == (1, 3, 6, 10, 14, 21, 28, 38, 49, 60, 75, 90, 105, 120, 140, 160, 180)
    passi = c.passi_followup_v3()
    assert [p["step_no"] for p in passi] == list(range(1, 18))
    assert [p["delay_seconds"] // 86400 for p in passi] == list(c.FOLLOWUP_V3_DAYS)
    for p in passi:
        assert p["delay_from"] == "trigger" and p["default_mode"] == "automatic"
        assert p["communication_type"] == "marketing" and p["channel"] == "email"
        assert p["template_key"] == f"followup_v3_m{p['step_no']}" and p["template_version"] == 3
        assert p["reason_code"] == f"m{p['step_no']}" and p["stop_on"] == []
    assert c.FOLLOWUP_V3_VERSION == 3 and c.STIMA_LEAD_KEY == "stima_lead"


def test_b2_i_template_della_v3_non_esistono_ancora_nessun_invio_in_fase_b():
    from communication import templates
    for i in range(1, 18):
        with pytest.raises(templates.ValidationError):
            templates.get(f"followup_v3_m{i}", 3)
    # e le v1/v2 sono dove erano
    assert templates.get("stima_lead_m1", 1).version == 1
    assert templates.get("stima_lead_m10", 2).version == 2


# ---------------------------------------------------------------------------
# C  le regole
# ---------------------------------------------------------------------------

def _passi(*giorni):
    return [{"step_no": i, "delay_seconds": g * 86400, "send_window": None, "active": True}
            for i, g in enumerate(giorni, start=1)]


def test_c1_il_prossimo_passo_non_recupera_arretrati_e_rispetta_le_48_ore():
    from communication import followup_rules as r
    inizio = ORA
    passi = _passi(1, 3, 6, 10)
    # giorno 7: i passi 1, 3 e 6 sono passati -> il prossimo e' il 10
    passo, quando = r.prossimo_passo(passi, trigger_sent_at=inizio, now=inizio + timedelta(days=7),
                                     ultimo_invio=None, timezone_name="Europe/Rome")
    assert passo["step_no"] == 4 and quando == inizio + timedelta(days=10)
    # 48 ore dall'ultimo invio: il passo slitta, non salta
    passo, quando = r.prossimo_passo(passi, trigger_sent_at=inizio, now=inizio + timedelta(days=9, hours=12),
                                     ultimo_invio=inizio + timedelta(days=9), timezone_name="Europe/Rome")
    assert passo["step_no"] == 4 and quando == inizio + timedelta(days=11)
    # oltre l'ultimo passo: finito
    assert r.prossimo_passo(passi, trigger_sent_at=inizio, now=inizio + timedelta(days=11),
                            ultimo_invio=None, timezone_name="Europe/Rome") == (None, None)
    # oltre i 180 giorni, anche se un passo fosse definito piu' in la'
    assert r.prossimo_passo(_passi(1, 200), trigger_sent_at=inizio, now=inizio + timedelta(days=2),
                            ultimo_invio=None, timezone_name="Europe/Rome") == (None, None)
    assert r.oltre_orizzonte(inizio, inizio + timedelta(days=181))
    assert not r.oltre_orizzonte(inizio, inizio + timedelta(days=180))


def test_c2_la_finestra_di_invio_vale_anche_alla_ripresa():
    from communication import followup_rules as r
    finestra = {"days": [1, 2, 3, 4, 5, 6], "from": "09:00", "to": "19:00"}
    passi = [{"step_no": 1, "delay_seconds": 86400, "send_window": finestra, "active": True}]
    sabato = datetime(2026, 10, 24, 20, 0, tzinfo=timezone.utc)  # sabato sera a Roma
    _, quando = r.prossimo_passo(passi, trigger_sent_at=sabato, now=sabato,
                                 ultimo_invio=None, timezone_name="Europe/Rome")
    assert quando.astimezone(timezone.utc) > sabato + timedelta(days=1)
    assert quando.astimezone(timezone(timedelta(hours=2))).weekday() == 0  # lunedi'


def test_c3_email_scrivibile():
    from communication.followup_rules import email_valida
    assert email_valida("mario.rossi@example.it") and email_valida("a+b@sub.dominio.org")
    for cattiva in (None, "", "senza-chiocciola", "a@b", "a @b.it", "a@b .it", "@x.it"):
        assert not email_valida(cattiva), cattiva


def test_c4_le_durate_delle_pause_sono_quelle_decise():
    from communication import followup_rules as r
    assert r.DOPO_APPUNTAMENTO_SVOLTO == timedelta(days=14)
    assert r.DOPO_APPUNTAMENTO_ANNULLATO == timedelta(days=2)
    assert r.DOPO_CONSULENZA == timedelta(days=14)
    assert r.DOPO_RELAZIONE_FINITA == timedelta(days=2)
    assert r.DOPO_VENDITA == timedelta(days=60)
    assert r.DISTANZA_MINIMA == timedelta(hours=48) and r.ORIZZONTE == timedelta(days=180)
    from communication import audience
    assert audience.TETTO_GIORNALIERO == 20


# ---------------------------------------------------------------------------
# D  la guardia e l'opt-out di piattaforma (decisione pura)
# ---------------------------------------------------------------------------

def _contatto(**extra):
    base = {"id": 7, "agency_id": 1, "marketing_consent": True,
            "marketing_consent_at": ORA - timedelta(days=10), "marketing_revoked_at": None,
            "marketing_consent_source": "public_stima", "marketing_consent_notice_id": None}
    base.update(extra)
    return base


def test_d1_un_optout_dopo_la_concessione_nega_uno_prima_no():
    from consent import guard
    evento = {"id": 1, "decision": "granted", "decided_at": ORA - timedelta(days=10),
              "source": "public_stima", "notice_id": None}
    assert guard._decidi(_contatto(), evento).allowed
    dopo = guard._decidi(_contatto(platform_optout_at=ORA - timedelta(days=1)), evento)
    assert not dopo.allowed and dopo.reason == "deny_platform_optout"
    prima = guard._decidi(_contatto(platform_optout_at=ORA - timedelta(days=30)), evento)
    assert prima.allowed and prima.reason == "allow_explicit_grant"
    # legacy: stessa regola, misurata sulla proiezione
    legacy = guard._decidi(_contatto(platform_optout_at=ORA - timedelta(days=1)), None)
    assert not legacy.allowed and legacy.reason == "deny_platform_optout" and legacy.legacy
    assert guard._decidi(_contatto(platform_optout_at=ORA - timedelta(days=30)), None).allowed
    # una revoca resta una revoca, a prescindere dall'opt-out
    revoca = {"id": 2, "decision": "revoked", "decided_at": ORA, "source": "crm", "notice_id": None}
    esito = guard._decidi(_contatto(marketing_consent=False, marketing_revoked_at=ORA,
                                    platform_optout_at=ORA - timedelta(days=30)), revoca)
    assert not esito.allowed and esito.reason == "deny_revoked"


def test_d2_la_statement_decisionale_porta_l_optout_nella_stessa_query():
    from consent import repository
    sql = repository._sql_decisionale("consent_events ce WHERE ce.agency_id = %s", "c.agency_id = %s",
                                      "c.id = %s", con_optout=True)
    assert sql.count("LEFT JOIN LATERAL") == 2 and "communication_email_optouts" in sql
    assert "platform_optout_at" in sql and "FOR UPDATE" not in sql.upper()
    senza = repository._sql_decisionale("x ce WHERE 1=1", "1=1", "c.id = %s", con_optout=False)
    assert "communication_email_optouts" not in senza and "NULL::timestamptz AS platform_optout_at" in senza


# ---------------------------------------------------------------------------
# E  il consenso dell'operatore: le regole che non toccano il database
# ---------------------------------------------------------------------------

class _Ctx:
    def __init__(self, user_id=5, agency_id=1, role="agent", is_platform_admin=False):
        self.user_id, self.agency_id, self.role, self.is_platform_admin = user_id, agency_id, role, is_platform_admin

    def require_agency(self):
        return self.agency_id


def test_e1_senza_identita_o_agenzia_nessuna_registrazione():
    from consent import operator_service as s
    from core.exceptions import PermissionDenied
    assert s._operatore(_Ctx()) == "operator_user:5"
    assert s._operatore(_Ctx(role="agency_owner")) == "operator_user:5"
    for ctx in (_Ctx(user_id=None), _Ctx(agency_id=None), _Ctx(role=None)):
        with pytest.raises(PermissionDenied):
            s._operatore(ctx)


def test_e2_la_prova_non_e_mai_una_dichiarazione_ne_un_recapito():
    from consent import operator_service as s
    from consent.exceptions import ValidationError
    contatto = {"email": "Mario@Example.it", "email_normalized": "mario@example.it",
                "phone": "+39 333 1234567", "phone_normalized": "393331234567"}
    assert s._riferimento_prova("  PROT-2026/0042 ", contatto) == "PROT-2026/0042"
    for cattiva in ("", "abc", "......", "mario@example.it", "MARIO@EXAMPLE.IT",
                    "ok mario@example.it", "393331234567", "x" * 101, None, 12):
        with pytest.raises(ValidationError):
            s._riferimento_prova(cattiva, contatto)
    assert s.GRANT_EVIDENCE_TYPES == ("paper_form", "digital_form", "other_documented")
    assert "phone_call" not in s.REVOCATION_EVIDENCE_TYPES and "email_on_file" not in s.GRANT_EVIDENCE_TYPES


def test_e3_la_data_di_raccolta_e_nel_passato_e_con_fuso():
    from consent import operator_service as s
    from consent.exceptions import ValidationError
    adesso = datetime.now(timezone.utc)
    assert s._istante(adesso - timedelta(days=3), "decided_at") == adesso - timedelta(days=3)
    for cattiva in (adesso + timedelta(hours=1), adesso - timedelta(days=4000),
                    datetime(2026, 1, 1), "2026-01-01", None):
        with pytest.raises(ValidationError):
            s._istante(cattiva, "decided_at")


def test_e4_la_nota_di_una_revoca_racconta_come_e_stata_espressa():
    from consent import operator_service as s
    from consent.exceptions import ValidationError
    assert s._nota("  Detto allo sportello il 3/10  ", minimo=10) == "Detto allo sportello il 3/10"
    with pytest.raises(ValidationError):
        s._nota("no", minimo=10)
    assert s._nota(None) is None and s._nota("   ") is None


# ---------------------------------------------------------------------------
# F  il link di disiscrizione con ambito
# ---------------------------------------------------------------------------

def test_f1_il_token_v1_e_identico_e_quello_v2_porta_l_ambito(monkeypatch):
    from communication import unsubscribe as u
    monkeypatch.setenv(u.SECRET_ENV, "s" * 40)
    quando = datetime(2026, 10, 1, tzinfo=timezone.utc)
    v1 = u.issue(3, 9, issued_at=quando)
    assert u.issue(3, 9, issued_at=quando, scope=u.SCOPE_AGENCY) == v1
    assert u.verify(v1) == (3, 9, int(quando.timestamp()))
    assert u.verify_scoped(v1) == {"agency_id": 3, "contact_id": 9, "issued_at": int(quando.timestamp()),
                                   "scope": "agency"}
    v2 = u.issue(3, 9, issued_at=quando, scope=u.SCOPE_PLATFORM)
    assert v2 != v1 and u.verify_scoped(v2)["scope"] == "platform"
    assert u.verify(v2) == (3, 9, int(quando.timestamp()))
    assert u.verify_scoped(v2[:-2] + "zz") is None
    with pytest.raises(ValueError):
        u.issue(3, 9, scope="tutto")
    assert "?t=" in u.unsubscribe_url("https://x.it/", 3, 9, scope=u.SCOPE_PLATFORM)
    assert "STIMA360" in u.SCOPE_QUESTIONS["platform"] and "agenzia" in u.SCOPE_QUESTIONS["agency"]


def test_f2_le_rotte_pubbliche_restano_due_e_la_pagina_dice_l_ambito(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from communication import public_router, unsubscribe as u
    monkeypatch.setenv(u.SECRET_ENV, "s" * 40)
    assert {(m, r.path) for r in public_router.router.routes for m in r.methods if m != "HEAD"} == {
        ("GET", "/api/public/communication/unsubscribe"),
        ("POST", "/api/public/communication/unsubscribe")}
    app = FastAPI()
    app.include_router(public_router.router)
    client = TestClient(app)
    pagina = client.get("/api/public/communication/unsubscribe?t=" + u.issue(1, 2, scope="platform")).text
    assert "tutte le comunicazioni commerciali STIMA360" in pagina and "Conferma disiscrizione" in pagina
    pagina = client.get("/api/public/communication/unsubscribe?t=" + u.issue(1, 2)).text
    assert "da questa agenzia" in pagina
    assert "commerciali?" in client.get("/api/public/communication/unsubscribe?t=rotto").text


# ---------------------------------------------------------------------------
# G  la migration 108
# ---------------------------------------------------------------------------

def test_g1_la_108_e_valida_con_la_down_ed_e_l_ultima():
    sys.path.insert(0, str(ROOT / "scripts"))
    import p26_migrate as runner
    tutte = runner.discover_migrations()
    runner.verify_contiguous(tutte)
    ultima = tutte[-1]
    assert ultima.version == "108_followup_contact_journey" and ultima.down_available
    assert runner.validate_migration(ultima) == []
    su = (ROOT / "migrations" / "108_followup_contact_journey.sql").read_text(encoding="utf-8")
    giu = (ROOT / "migrations" / "108_followup_contact_journey_down.sql").read_text(encoding="utf-8")
    for pezzo in ("CREATE TABLE communication_email_optouts", "contact_eligible", "sequence_policy",
                  "resume_after", "uq_comm_enroll_open_per_email", "'m20'", "email_normalized_snapshot",
                  "communication_email_optouts_append_only", "communication_contact_enrollment_once",
                  "trg_comm_contact_enrollment_once"):
        assert pezzo in su, pezzo
    assert "DROP TABLE communication_email_optouts" in giu and "RAISE EXCEPTION '108 down" in giu
    assert "DROP TRIGGER IF EXISTS trg_comm_contact_enrollment_once" in giu
    # nessuna migration storica tocca
    import subprocess as sp
    cambiate = sp.run(["git", "--no-optional-locks", "diff", "--name-only", "HEAD", "--", "migrations/"],
                      cwd=ROOT, capture_output=True, text=True).stdout.split()
    assert all(c.startswith("migrations/108_") for c in cambiate), cambiate


# ---------------------------------------------------------------------------
# H  confini
# ---------------------------------------------------------------------------

def test_h1_la_lettura_trasversale_sta_solo_nel_repository_e_la_platea_non_decide():
    pacchetto = ROOT / "communication"
    for nome in ("anywhere",):
        dove = {p.name for p in pacchetto.glob("*.py") if nome in _codice(p)}
        assert dove == {"journey_repository.py", "audience.py"}, dove
    platea = _codice(pacchetto / "audience.py")
    regole = _codice(pacchetto / "followup_rules.py")
    for vietato in ("can_send_marketing", "marketing_consent", "invia_mail", "providers", "smtplib"):
        assert vietato not in platea and vietato not in regole, vietato
    assert "marketing_prefilter_sql" in platea
    # il prefiltro vive nel pacchetto del consenso e nomina SOLO la proiezione
    from consent.audience import marketing_granted_at_sql, marketing_prefilter_sql
    assert marketing_prefilter_sql("c") == "(c.marketing_consent IS TRUE AND c.marketing_revoked_at IS NULL)"
    assert marketing_granted_at_sql("c") == "c.marketing_consent_at"


def test_h2_la_scheda_contatto_manda_solo_i_campi_del_consenso():
    js = _codice(ROOT / "static" / "os_shell" / "assets" / "components" / "communications.js")
    assert "data-consent-grant-form" in js and "data-consent-refusal-form" in js
    corpo = js.split("const corpo = grant ? {", 1)[1].split("};", 1)[0]
    for campo in ("decided_at", "evidence_type", "evidence_ref", "notice_id", "note", "scope"):
        assert campo in corpo, campo
    for vietato in ("agency_id", "email", "contact_id:", "actor", "destination"):
        assert vietato not in corpo, vietato
    assert "/consent/${grant ? 'grant' : 'refusal'}" in js
    assert "Registra consenso marketing" in js and "senza prova" not in js.lower().replace("senza prova e senza informativa", "")
    esito = subprocess.run(["node", "--check", str(ROOT / "static/os_shell/assets/components/communications.js")],
                           capture_output=True, text=True, timeout=30)
    assert esito.returncode == 0, esito.stderr


def test_h3_le_rotte_del_consenso_sono_sul_router_autenticato_e_le_informative_dal_super_admin():
    from communication.router import router
    from platform_admin.router import router as piattaforma
    from platform_admin.dependencies import require_super_admin_context
    rotte = {(m, r.path) for r in router.routes for m in r.methods if m != "HEAD"}
    assert {("GET", "/api/communication/contacts/{contact_id}/consent"),
            ("POST", "/api/communication/contacts/{contact_id}/consent/grant"),
            ("POST", "/api/communication/contacts/{contact_id}/consent/refusal"),
            ("GET", "/api/communication/consent/notices")} <= rotte
    informative = [r for r in piattaforma.routes if "consent-notices" in getattr(r, "path", "")]
    assert len(informative) == 4
    for r in informative:
        nomi = {d.call for d in r.dependant.dependencies}
        assert require_super_admin_context in nomi, r.path
