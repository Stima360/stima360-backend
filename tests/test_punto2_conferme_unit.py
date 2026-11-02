"""ROADMAP PUNTO 2 - conferme al cliente e promemoria configurabili, SENZA
database.

Quattro famiglie:
  C  la policy pura: offset attivi, allowlist dei destinatari, decisione del
     promemoria a 2 ore;
  T  i template: la chiave per offset, la conferma e il nuovo orario;
  K  `confirmation`: chiave, metadata, decisione e gli agganci su un cursore
     finto (savepoint, spegnimento, migrazione assente, errore);
  D  la revalida e il dispatcher VERO su una conferma in coda: parte una
     volta, e non parte se l'appuntamento e' stato annullato o spostato.

Le stesse prove sul PostgreSQL vero stanno in `test_punto2_conferme_postgres.py`.
"""
from __future__ import annotations

import inspect
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from appointment_reminders import confirmation, policy, revalidation, settings, template
# il mondo finto del dispatcher (ledger, database riletto, provider che conta)
from tests.test_a32_2_reminder_dispatch import giro, messaggio, mondo, riga_db, soppresso  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
ROMA = ZoneInfo("Europe/Rome")


def R(*a):
    return datetime(*a, tzinfo=ROMA)


INIZIO = R(2031, 6, 13, 10, 0)
INDIRIZZO = {"address": "Via Roma", "civic_number": "12", "city": "Giulianova"}


def appuntamento(**diversi):
    base = {"appointment_type": "seller_meeting", "source": "crm_manual", "status": "scheduled",
            "start_at": INIZIO, "created_at": INIZIO - timedelta(days=5)}
    base.update(diversi)
    return base


def contatto(**diversi):
    base = {"status": "active", "email": "mario@example.it", "first_name": "Mario"}
    base.update(diversi)
    return base


# ---------------------------------------------------------------------------
# C - policy pura
# ---------------------------------------------------------------------------

def test_C1_offset_predefinito_solo_24h_e_lista_chiusa():
    assert policy.OFFSETS == ("24h", "2h") and policy.DEFAULT_OFFSETS == ("24h",)
    assert policy.enabled_offsets(None) == ("24h",)
    assert policy.enabled_offsets("   ") == ("24h",)
    assert policy.enabled_offsets("48h,1h") == ("24h",)          # sconosciuti ignorati
    assert policy.enabled_offsets("2h") == ("2h",)
    assert policy.enabled_offsets("2h, 24h") == ("24h", "2h")    # l'ordine e' di OFFSETS
    assert policy.enabled_offsets(" 24H ,2H ") == ("24h", "2h")


def test_C2_allowlist_dei_destinatari():
    assert policy.allowed_recipients(None) is None                # nessun limite
    assert policy.allowed_recipients("") == frozenset()          # limite: nessuno
    assert policy.allowed_recipients("A@x.it, b@y.it; c@z.it\n d@w.it senza-chiocciola") == \
        frozenset({"a@x.it", "b@y.it", "c@z.it", "d@w.it"})
    ammessi = frozenset({"mario@example.it"})
    assert policy.recipient_allowed("qualunque@example.it", None)
    assert policy.recipient_allowed(" Mario@Example.IT ", ammessi)
    assert not policy.recipient_allowed("altro@example.it", ammessi)
    assert not policy.recipient_allowed(None, ammessi)
    assert not policy.recipient_allowed("mario@example.it", frozenset())


def test_C3_destinatario_fuori_allowlist_e_un_motivo_dopo_quelli_di_sempre():
    assert policy.ineligibility_reason(appuntamento(), contatto()) is None
    assert policy.ineligibility_reason(
        appuntamento(), contatto(), allowed_recipients=frozenset({"altro@example.it"})
    ) == "recipient_not_allowed"
    assert policy.ineligibility_reason(
        appuntamento(), contatto(), allowed_recipients=frozenset({"mario@example.it"})) is None
    # i motivi di prima vengono PRIMA (lo stato, l'email mancante)
    assert policy.ineligibility_reason(
        appuntamento(status="cancelled"), contatto(),
        allowed_recipients=frozenset()) == "status_not_allowed"
    assert policy.ineligibility_reason(
        appuntamento(), contatto(email=None), allowed_recipients=frozenset()) == \
        "email_missing_or_invalid"


def test_C4_il_promemoria_a_2h_e_dovuto_due_ore_prima_dentro_la_finestra():
    d = policy.send_decision(now=R(2031, 6, 13, 8, 0), start_at=INIZIO,
                             created_at=INIZIO - timedelta(hours=1), offset="2h")
    assert (d.due, d.reason) == (True, None)
    assert d.send_at == R(2031, 6, 13, 8, 0).astimezone(timezone.utc)
    # prima del target: non ancora dovuto, nessun motivo
    d = policy.send_decision(now=R(2031, 6, 13, 7, 30), start_at=INIZIO,
                             created_at=INIZIO - timedelta(days=1), offset="2h")
    assert (d.due, d.reason) == (False, None) and d.send_at == R(2031, 6, 13, 8, 0)
    # nessuna soglia di prenotazione a 12h (quella e' del 24h)
    d24 = policy.send_decision(now=R(2031, 6, 12, 10, 0), start_at=INIZIO,
                               created_at=INIZIO - timedelta(hours=1))
    assert d24.reason == "booked_less_than_12h_before"


def test_C5_il_promemoria_a_2h_fuori_finestra_o_senza_anticipo_non_parte():
    # 09:00 -> target 07:00, prima delle 08: fuori finestra (niente spostamenti)
    d = policy.send_decision(now=R(2031, 6, 13, 6, 0), start_at=R(2031, 6, 13, 9, 0),
                             created_at=R(2031, 6, 1, 9, 0), offset="2h")
    assert (d.due, d.reason) == (False, "outside_send_window")
    # giro saltato: a 30 minuti dall'inizio non ha piu' senso
    d = policy.send_decision(now=R(2031, 6, 13, 9, 30), start_at=INIZIO,
                             created_at=R(2031, 6, 1, 9, 0), offset="2h")
    assert (d.due, d.reason) == (False, "less_than_3h_left")
    # sera: 21:00 -> 19:00 e' dentro (la finestra chiude alle 20 escluse)
    d = policy.send_decision(now=R(2031, 6, 13, 19, 0), start_at=R(2031, 6, 13, 21, 0),
                             created_at=R(2031, 6, 1, 9, 0), offset="2h")
    assert (d.due, d.reason) == (True, None)
    # 22:30 -> 20:30: fuori
    d = policy.send_decision(now=R(2031, 6, 13, 20, 30), start_at=R(2031, 6, 13, 22, 30),
                             created_at=R(2031, 6, 1, 9, 0), offset="2h")
    assert d.reason == "outside_send_window"
    assert policy.min_lead("2h") == timedelta(hours=1) and policy.min_lead() == timedelta(hours=3)
    with pytest.raises(ValueError):
        policy.send_decision(now=INIZIO, start_at=INIZIO, created_at=INIZIO, offset="48h")


def test_C6_la_chiave_porta_l_offset_e_mai_l_email():
    epoca = int(INIZIO.timestamp())
    assert policy.occurrence_key(42, INIZIO) == f"appointment_reminder:v1:42:24h:{epoca}"
    assert policy.occurrence_key(42, INIZIO, "2h") == f"appointment_reminder:v1:42:2h:{epoca}"
    assert policy.occurrence_key(42, INIZIO) == policy.occurrence_key(42, INIZIO, "24h")
    d = policy.is_reminder_eligible(now=R(2031, 6, 13, 8, 0), appointment=appuntamento(),
                                    contact=contatto(), offset="2h",
                                    allowed_recipients=frozenset({"mario@example.it"}))
    assert d.due and d.reason is None
    d = policy.is_reminder_eligible(now=R(2031, 6, 13, 8, 0), appointment=appuntamento(),
                                    contact=contatto(), offset="2h",
                                    allowed_recipients=frozenset())
    assert d.reason == "recipient_not_allowed"


def test_C7_settings_e_l_unico_lettore_dell_ambiente(monkeypatch):
    for nome in (policy.OFFSETS_ENV, policy.RECIPIENTS_ENV, settings.CONFIRMATIONS_ENV):
        monkeypatch.delenv(nome, raising=False)
    assert settings.offsets() == ("24h",)
    assert settings.allowed_recipients() is None
    assert settings.confirmations_enabled() is False
    monkeypatch.setenv("APPOINTMENT_REMINDER_OFFSETS", "24h,2h")
    monkeypatch.setenv("APPOINTMENT_NOTIFICATIONS_ALLOWED_RECIPIENTS", "Prova@Example.it")
    monkeypatch.setenv("APPOINTMENT_CONFIRMATIONS", " 1 ")
    assert settings.offsets() == ("24h", "2h")
    assert settings.allowed_recipients() == frozenset({"prova@example.it"})
    assert settings.confirmations_enabled() is True
    monkeypatch.setenv("APPOINTMENT_CONFIRMATIONS", "true")
    assert settings.confirmations_enabled() is False                 # solo "1"
    # la policy e il template restano puri: nessun `os`
    for modulo in ("policy.py", "template.py"):
        codice = (ROOT / "appointment_reminders" / modulo).read_text(encoding="utf-8")
        assert not re.search(r"^\s*(import os|from os)", codice, re.M), modulo
    assert set(inspect.signature(settings.offsets).parameters) == set()


# ---------------------------------------------------------------------------
# T - template
# ---------------------------------------------------------------------------

def test_T1_la_chiave_del_template_dipende_dall_offset():
    assert template.template_key() == "appointment_reminder_24h" == template.TEMPLATE_KEY
    assert template.template_key("2h") == "appointment_reminder_2h"
    with pytest.raises(ValueError):
        template.template_key("48h")
    _, corpo24 = template.render_email(appointment_type="seller_meeting", start_at=INIZIO,
                                       agency_name="Agenzia Mare", customer_name="Mario")
    _, corpo2 = template.render_email(appointment_type="seller_meeting", start_at=INIZIO,
                                      agency_name="Agenzia Mare", customer_name="Mario", offset="2h")
    assert "prossimo appuntamento" in corpo24 and "tra circa due ore" in corpo2
    with pytest.raises(ValueError):
        template.render_email(appointment_type="seller_meeting", start_at=INIZIO,
                              agency_name="Agenzia Mare", offset="48h")


def conferma(**kw):
    base = {"kind": "scheduled", "appointment_type": "buyer_visit", "start_at": INIZIO,
            "agency_name": "Agenzia Mare", "customer_name": "Mario",
            "property_address": INDIRIZZO, "agency_phone": "085 123456",
            "agency_email": "info@agenziamare.it"}
    base.update(kw)
    return template.render_confirmation_email(**base)


def test_T2_la_conferma_ha_giorno_ora_indirizzo_e_riferimenti():
    oggetto, corpo = conferma()
    assert oggetto == "Conferma: visita all'immobile venerdì 13 giugno 2031 alle 10:00"
    for atteso in ("Conferma appuntamento", "Buongiorno Mario,", "è fissato",
                   "venerdì 13 giugno 2031", "10:00", "Via Roma 12, Giulianova",
                   "Riferimenti", "Agenzia Mare, 085 123456, info@agenziamare.it",
                   "contatta l'agenzia"):
        assert atteso in corpo, atteso
    assert (template.CONFIRMATION_TEMPLATE_KEY, template.CONFIRMATION_TEMPLATE_VERSION) == \
        ("appointment_confirmation", 1)


def test_T3_il_nuovo_orario_dice_che_sostituisce_il_precedente():
    oggetto, corpo = conferma(kind="rescheduled", start_at=R(2031, 6, 14, 15, 30))
    assert oggetto.startswith("Nuovo orario: visita all'immobile sabato 14 giugno 2031 alle 15:30")
    assert "Nuovo orario dell&#x27;appuntamento" in corpo and "sostituisce il precedente" in corpo
    with pytest.raises(ValueError):
        conferma(kind="cancelled")


def test_T4_la_conferma_ha_la_stessa_lista_chiusa_di_dati_del_promemoria():
    """Niente note, luogo libero, agente, id, token: non c'e' un parametro."""
    assert list(inspect.signature(template.render_confirmation_email).parameters) == [
        "kind", "appointment_type", "start_at", "agency_name", "customer_name",
        "property_address", "agency_phone", "agency_email"]
    # indirizzo SOLO strutturato e SOLO per la visita acquirente
    _, corpo = conferma(appointment_type="seller_meeting")
    assert "Via Roma" not in corpo and "Indirizzo" not in corpo
    with pytest.raises(TypeError):
        conferma(property_address="chiavi dal vicino")
    # i riferimenti mancanti non lasciano virgole: solo il nome dell'agenzia
    _, corpo = conferma(agency_phone=None, agency_email="  ")
    assert "<strong>Agenzia Mare</strong>" in corpo
    _, corpo = conferma(customer_name=None)
    assert "Buongiorno," in corpo
    with pytest.raises(ValueError):
        conferma(agency_name=" ")


def test_T5_escape_html_anche_nei_riferimenti():
    _, corpo = conferma(agency_phone="<script>alert(1)</script>", customer_name="<b>M</b>")
    assert "<script>" not in corpo and "&lt;script&gt;" in corpo
    assert "<b>M</b>" not in corpo


# ---------------------------------------------------------------------------
# K - confirmation: chiave, metadata, decisione, agganci su cursore finto
# ---------------------------------------------------------------------------

def test_K1_chiave_e_metadata_della_conferma():
    epoca = int(INIZIO.timestamp())
    assert confirmation.occurrence_key(7, INIZIO) == f"appointment_confirmation:v1:7:{epoca}"
    # la stessa occorrenza in UTC o a Roma: la stessa chiave
    assert confirmation.occurrence_key(7, INIZIO.astimezone(timezone.utc)) == \
        confirmation.occurrence_key(7, INIZIO)
    for cattivo in (0, -1, True, "7"):
        with pytest.raises(ValueError):
            confirmation.occurrence_key(cattivo, INIZIO)
    with pytest.raises(ValueError):
        confirmation.occurrence_key(7, datetime(2031, 6, 13, 10))
    meta = confirmation.metadata_for(7, INIZIO, "rescheduled")
    assert meta == {"kind": "appointment_confirmation", "appointment_id": 7, "start_epoch": epoca,
                    "occurrence_key": f"appointment_confirmation:v1:7:{epoca}",
                    "variant": "rescheduled"}
    assert set(meta) == set(revalidation.CONFIRMATION_METADATA_KEYS)
    with pytest.raises(ValueError):
        confirmation.metadata_for(7, INIZIO, "cancelled")
    assert confirmation.ORIGIN == "appointment_reminder"
    assert confirmation.CLOSABLE_REASONS == ("appointment_reminder", "appointment_confirmation")


def letto(**diversi):
    r = riga_db(**{k: v for k, v in diversi.items() if k != "contatto"})
    if "contatto" in diversi:
        r["contact"] = diversi["contatto"]
    r["property_address"] = None
    r["agency"] = {"name": "Agenzia Mare", "profile": {"display_name": "Mare Immobiliare",
                                                       "phone": "085 1", "email": "info@mare.it"}}
    return r


def test_K2_decide_e_pura_e_ordinata(monkeypatch):
    monkeypatch.delenv(policy.RECIPIENTS_ENV, raising=False)
    adesso = R(2031, 6, 12, 10, 0)
    assert confirmation.decide(None, now=adesso) == "appointment_missing"
    assert confirmation.decide(letto(), now=adesso) is None
    assert confirmation.decide(letto(status="cancelled"), now=adesso) == "status_not_allowed"
    assert confirmation.decide(letto(contatto=None), now=adesso) == "contact_missing"
    assert confirmation.decide(letto(c_email="x"), now=adesso) == "email_missing_or_invalid"
    assert confirmation.decide(letto(), now=INIZIO) == "appointment_already_started"
    monkeypatch.setenv(policy.RECIPIENTS_ENV, "altro@example.it")
    assert confirmation.decide(letto(), now=adesso) == "recipient_not_allowed"


class Cursore:
    """Un cursore che registra i SAVEPOINT; nessun SQL vero."""

    def __init__(self):
        self.sql: list[str] = []

    def execute(self, testo, *_a):
        self.sql.append(testo)


def test_K3_spenta_per_impostazione_nessun_cursore_toccato(monkeypatch):
    monkeypatch.delenv(settings.CONFIRMATIONS_ENV, raising=False)
    cur = Cursore()
    assert confirmation.on_scheduled(cur, 7, 101) == "disabled"
    assert cur.sql == []
    with pytest.raises(ValueError):
        confirmation.on_scheduled(cur, 7, 101, kind="cancelled")


def _accesa(monkeypatch, *, supportato=True, riga=None, enqueue=None):
    monkeypatch.setenv(settings.CONFIRMATIONS_ENV, "1")
    monkeypatch.delenv(policy.RECIPIENTS_ENV, raising=False)
    chiamate = {"enqueue": [], "cancel": []}
    monkeypatch.setattr(confirmation.communication_service, "reason_code_supported",
                        lambda reason_code, *, cur=None: supportato)
    monkeypatch.setattr(confirmation.repository, "appointment_for_confirmation",
                        lambda cur, agency_id, appointment_id: riga)

    def _enqueue(ctx, **kw):
        chiamate["enqueue"].append((ctx, kw))
        if enqueue is not None:
            return enqueue(ctx, **kw)
        return {"created": True, "message": {"id": 1}}

    def _cancel(ctx, *, appointment_id, reason_codes, cur=None):
        chiamate["cancel"].append((ctx, appointment_id, tuple(reason_codes), cur))
        return 2

    monkeypatch.setattr(confirmation.communication_service, "enqueue", _enqueue)
    monkeypatch.setattr(confirmation.communication_service, "cancel_queued_for_appointment", _cancel)
    return chiamate


def test_K4_migrazione_127_assente_nessun_accodamento_savepoint_rilasciato(monkeypatch):
    chiamate = _accesa(monkeypatch, supportato=False, riga=letto())
    cur = Cursore()
    assert confirmation.on_scheduled(cur, 7, 101) == "not_migrated"
    assert cur.sql == ["SAVEPOINT appointment_confirmation", "RELEASE SAVEPOINT appointment_confirmation"]
    assert chiamate["enqueue"] == []


def test_K5_accoda_con_i_campi_esatti_e_il_profilo_dell_agenzia(monkeypatch):
    chiamate = _accesa(monkeypatch, riga=letto())
    cur = Cursore()
    assert confirmation.on_scheduled(cur, 7, 101, now=R(2031, 6, 12, 10, 0)) == "queued"
    [(ctx, kw)] = chiamate["enqueue"]
    assert (type(ctx).__name__, ctx.agency_id, ctx.origin) == ("SystemAgencyContext", 7,
                                                               "appointment_reminder")
    epoca = int(INIZIO.timestamp())
    assert kw["contact_id"] == 501 and kw["channel"] == "email"
    assert (kw["communication_type"], kw["mode"], kw["reason_code"]) == (
        "service", "automatic", "appointment_confirmation")
    assert kw["idempotency_key"] == f"appointment_confirmation:v1:101:{epoca}"
    assert (kw["template_key"], kw["template_version"]) == ("appointment_confirmation", 1)
    assert kw["destination_snapshot"] == "mario@example.it" and kw["cur"] is cur
    assert kw["metadata"]["variant"] == "scheduled" and kw["property_id"] is None
    assert kw["subject_snapshot"].startswith("Conferma: appuntamento venerdì 13 giugno 2031")
    # i riferimenti vengono dal profilo (nome visualizzato, telefono, email)
    assert "Mare Immobiliare, 085 1, info@mare.it" in kw["rendered_body"]
    assert "Agenzia Mare" not in kw["rendered_body"]
    assert cur.sql[-1] == "RELEASE SAVEPOINT appointment_confirmation"
    # e la stessa occorrenza gia' in coda e' idempotente, non un duplicato
    chiamate = _accesa(monkeypatch, riga=letto(),
                       enqueue=lambda ctx, **kw: {"created": False, "message": {"id": 1}})
    assert confirmation.on_scheduled(Cursore(), 7, 101, now=R(2031, 6, 12, 10, 0)) == \
        "queued_idempotent"


def test_K6_senza_profilo_firma_il_nome_dell_agenzia(monkeypatch):
    riga = letto()
    riga["agency"] = {"name": "Agenzia Mare", "profile": {}}
    chiamate = _accesa(monkeypatch, riga=riga)
    confirmation.on_scheduled(Cursore(), 7, 101, now=R(2031, 6, 12, 10, 0))
    [(_, kw)] = chiamate["enqueue"]
    assert "<strong>Agenzia Mare</strong>" in kw["rendered_body"]


def test_K7_non_idonea_nessun_accodamento(monkeypatch):
    chiamate = _accesa(monkeypatch, riga=letto(status="requested"))
    assert confirmation.on_scheduled(Cursore(), 7, 101, now=R(2031, 6, 12, 10, 0)) == \
        "status_not_allowed"
    chiamate2 = _accesa(monkeypatch, riga=None)
    assert confirmation.on_scheduled(Cursore(), 7, 101) == "appointment_missing"
    assert chiamate["enqueue"] == [] and chiamate2["enqueue"] == []


def test_K8_un_errore_torna_al_savepoint_e_non_rompe_l_agenda(monkeypatch, caplog):
    def esplode(ctx, **kw):
        raise RuntimeError("ledger giu' con l'email mario@example.it dentro")
    _accesa(monkeypatch, riga=letto(), enqueue=esplode)
    cur = Cursore()
    with caplog.at_level("ERROR"):
        assert confirmation.on_scheduled(cur, 7, 101, now=R(2031, 6, 12, 10, 0)) == "error"
    assert cur.sql == ["SAVEPOINT appointment_confirmation",
                       "ROLLBACK TO SAVEPOINT appointment_confirmation"]
    # il log dice agenzia e appuntamento, mai l'indirizzo
    [record] = [r for r in caplog.records if "appointment confirmation failed" in r.getMessage()]
    assert "agency_id=7 appointment_id=101" in record.getMessage()
    assert "mario@example.it" not in record.getMessage()


def test_K9_on_closed_ritira_promemoria_e_conferme_nel_savepoint(monkeypatch):
    chiamate = _accesa(monkeypatch, riga=letto())
    cur = Cursore()
    assert confirmation.on_closed(cur, 7, 101) == 2
    [(ctx, appointment_id, motivi, cur_passato)] = chiamate["cancel"]
    assert (ctx.agency_id, ctx.origin, appointment_id, cur_passato) == (
        7, "appointment_reminder", 101, cur)
    assert motivi == ("appointment_reminder", "appointment_confirmation")
    assert cur.sql == ["SAVEPOINT appointment_notifications_close",
                       "RELEASE SAVEPOINT appointment_notifications_close"]

    def esplode(ctx, **kw):
        raise RuntimeError("giu'")
    monkeypatch.setattr(confirmation.communication_service, "cancel_queued_for_appointment", esplode)
    cur = Cursore()
    assert confirmation.on_closed(cur, 7, 101) == -1
    assert cur.sql[-1] == "ROLLBACK TO SAVEPOINT appointment_notifications_close"


def test_K10_gli_agganci_stanno_nei_write_path_dell_agenda_dentro_la_transazione():
    """Lettura statica di `appointments/service.py`: creazione gia' fissata,
    booking pubblico, «fissa», spostamento (ritiro + conferma nuova),
    annullamento (ritiro)."""
    codice = (ROOT / "appointments" / "service.py").read_text(encoding="utf-8")
    assert codice.count("_conferme.on_scheduled(cur, agency_id, ") == 4
    assert codice.count("_conferme.on_closed(cur, agency_id, ") == 2
    assert 'kind=_conferme.KIND_RESCHEDULED' in codice
    # nel reschedule: prima il ritiro della vecchia, poi la conferma della nuova
    ritiro = codice.index('_conferme.on_closed(cur, agency_id, vecchia["id"])')
    nuova = codice.index('_conferme.on_scheduled(cur, agency_id, nuova["id"], kind=_conferme.KIND_RESCHEDULED)')
    assert ritiro < nuova
    # nel booking pubblico: dopo l'INSERT e il collegamento della submission
    inizio = codice.index("def create_public_booking_appointment")
    fine = codice.index("\ndef ", inizio + 10)          # il prossimo def di primo livello
    blocco = codice[inizio:fine]
    assert blocco.index("repository.insert_appointment(cur, valori") < \
        blocco.index("UPDATE public_booking_submissions") < \
        blocco.index("_conferme.on_scheduled(cur, agency_id")
    # nella creazione: solo una riga gia' fissata o confermata
    assert 'if riga["status"] in ("scheduled", "confirmed"):\n            _conferme.on_scheduled' in codice
    # il modulo che li espone non apre connessioni proprie: lavora sul cursore
    conf = (ROOT / "appointment_reminders" / "confirmation.py").read_text(encoding="utf-8")
    for vietato in ("communication_cursor", "core_cursor", "get_connection", "commit("):
        assert vietato not in conf, vietato
    assert conf.count("SAVEPOINT") >= 3


# ---------------------------------------------------------------------------
# D - revalida e dispatcher VERO su una conferma in coda
# ---------------------------------------------------------------------------

def conferma_in_coda(i=1, **diversi):
    m = messaggio(i, reason_code="appointment_confirmation",
                  template_key="appointment_confirmation", template_version=1,
                  subject_snapshot="Conferma",
                  idempotency_key=confirmation.occurrence_key(101, INIZIO),
                  metadata=confirmation.metadata_for(101, INIZIO, "scheduled"))
    m.update(diversi)
    return m


def test_D1_la_conferma_in_coda_parte_una_volta(mondo):  # noqa: F811
    c = giro(mondo, conferma_in_coda())
    assert len(mondo.inviati) == 1 and c["sent"] == 1 and c["suppressed"] == 0
    # la revalida ha riletto l'appuntamento PRIMA del provider
    assert mondo.eventi.index(("revalida", 101)) < mondo.eventi.index(("provider", 1))


def test_D2_annullato_o_spostato_nel_frattempo_la_conferma_non_parte(mondo):  # noqa: F811
    mondo.db[(7, 101)] = riga_db(status="cancelled")
    giro(mondo, conferma_in_coda())
    assert mondo.inviati == [] and soppresso(mondo, "status_not_allowed")
    mondo.db[(7, 101)] = riga_db(status="rescheduled")
    giro(mondo, conferma_in_coda(2))
    assert mondo.inviati == [] and ("suppressed", 2, "status_not_allowed") in mondo.eventi
    # l'orario e' cambiato sulla stessa riga: la chiave non e' piu' quella
    mondo.db[(7, 101)] = riga_db(start_at=INIZIO + timedelta(hours=1))
    giro(mondo, conferma_in_coda(3))
    assert mondo.inviati == [] and ("suppressed", 3, "occurrence_changed") in mondo.eventi


def test_D3_appuntamento_gia_cominciato_o_email_cambiata(mondo, monkeypatch):  # noqa: F811
    monkeypatch.setattr(revalidation, "_adesso", lambda: INIZIO + timedelta(minutes=1))
    giro(mondo, conferma_in_coda())
    assert mondo.inviati == [] and soppresso(mondo, "appointment_already_started")
    monkeypatch.setattr(revalidation, "_adesso", lambda: R(2031, 6, 12, 10, 5))
    mondo.db[(7, 101)] = riga_db(c_email="nuova@example.it")
    giro(mondo, conferma_in_coda(2))
    assert mondo.inviati == [] and ("suppressed", 2, "destination_changed") in mondo.eventi


def test_D4_metadata_della_conferma_esatti_o_niente(mondo):  # noqa: F811
    meta = confirmation.metadata_for(101, INIZIO, "scheduled")
    for guasto in (dict(meta, variant="cancelled"), {k: v for k, v in meta.items() if k != "variant"},
                   dict(meta, extra=1)):
        giro(mondo, conferma_in_coda(metadata=guasto))
    giro(mondo, conferma_in_coda(template_version=2))
    assert mondo.inviati == [] and mondo.eventi.count(("suppressed", 1, "reminder_metadata_invalid")) == 4
    # e un promemoria a 2h con il suo template passa dalla stessa revalida
    assert revalidation.decide(
        messaggio(template_key="appointment_reminder_2h",
                  idempotency_key=policy.occurrence_key(101, INIZIO, "2h"),
                  metadata={**messaggio()["metadata"], "offset": "2h",
                            "occurrence_key": policy.occurrence_key(101, INIZIO, "2h")}),
        riga_db(), agency_id=7, now=R(2031, 6, 13, 8, 0)) is None


def test_D5_il_motivo_nuovo_e_nella_lista_chiusa_e_nelle_etichette():
    assert "appointment_already_started" in revalidation.REVALIDATION_REASONS
    from communication import contact_view, enums
    assert enums.REASON_APPOINTMENT_CONFIRMATION == "appointment_confirmation" in enums.REASON_CODES
    assert contact_view.ETICHETTE_MOTIVO["appointment_confirmation"] == "Conferma appuntamento"
