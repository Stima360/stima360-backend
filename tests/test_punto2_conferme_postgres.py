"""ROADMAP PUNTO 2 - conferme al cliente, ritiro dei messaggi in coda e
promemoria a 2 ore sul PostgreSQL VERO, attraverso il service dell'Agenda.

Banco: lo stesso di `test_a32_2_reminder_postgres.py` (catena Agenda
certificata + ledger P29 dai file veri + 079), piu' la 127 dal file VERO e la
colonna `agencies.settings` della 027 (il profilo dell'agenzia). Le scritture
passano dal service VERO (`appointments.service`): e' li' che vivono gli
agganci. Il provider e' finto e conta: nessuna email parte.
"""
from __future__ import annotations

import os
from datetime import timedelta

import pytest

from tests.test_a30_2_appointments_postgres import db, mondo  # noqa: F401
from tests.test_a30_13b_create_permissions_postgres import agenda_completa  # noqa: F401
from tests.test_a31_2_buyer_visits_postgres import schema_a31  # noqa: F401
from tests.test_a32_2_reminder_postgres import (  # noqa: F401
    ADESSO, DOMANI_10, MIGRAZIONI, dispatch, giro, messaggi, nuovo, owner, roma, schema_a32, x)

DSN = os.getenv("P29_TEST_DSN")

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare il Punto 2")

VERSIONE_127 = "127_appointment_confirmations"
#: Il CHECK come lo lascia la 079 (senza `appointment_confirmation`): per la
#: prova «127 non ancora applicata» si torna a questo, e poi si riapplica la 127.
SENZA_127 = """
ALTER TABLE communication_messages DROP CONSTRAINT IF EXISTS communication_messages_reason_code_chk;
ALTER TABLE communication_messages ADD CONSTRAINT communication_messages_reason_code_chk
    CHECK (reason_code IN ('stima_pdf','operator_manual','operator_reply','owner_login_link',
        'appointment_reminder','consent_confirmation','auto_reply',
        'm1','m2','m3','m4','m5','m6','m7','m8','m9','m10',
        'm11','m12','m13','m14','m15','m16','m17','m18','m19','m20'));
"""


def _testo(versione: str) -> str:
    return (MIGRAZIONI / f"{versione}.sql").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def schema_p2(schema_a32):  # noqa: F811
    conn = schema_a32["conn"]
    with conn.cursor() as cur:
        # la colonna vera della 027 che lo schema minimo condiviso non ha
        cur.execute("ALTER TABLE agencies ADD COLUMN IF NOT EXISTS settings JSONB NOT NULL "
                    "DEFAULT '{}'::jsonb")
        cur.execute(_testo(VERSIONE_127))
        cur.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (VERSIONE_127,))
    conn.commit()
    return schema_a32


@pytest.fixture
def y(x, schema_p2, monkeypatch):  # noqa: F811
    """Conferme ACCESE (come sul TEST durante il collaudo), nessuna allowlist,
    offset predefinito. Il profilo dell'agenzia A porta i riferimenti."""
    monkeypatch.setenv("APPOINTMENT_CONFIRMATIONS", "1")
    monkeypatch.delenv("APPOINTMENT_NOTIFICATIONS_ALLOWED_RECIPIENTS", raising=False)
    monkeypatch.delenv("APPOINTMENT_REMINDER_OFFSETS", raising=False)
    x["sql"]("UPDATE agencies SET settings = %s::jsonb WHERE id = %s",
             ('{"report_profile": {"display_name": "Mare Immobiliare", "phone": "085 123456", '
              '"email": "info@mare.example"}}', x["a"]))
    x["sql"]("UPDATE agencies SET settings = '{}'::jsonb WHERE id = %s", (x["b"],))
    return x


def crea(y, chi="giorgio", **kw):
    """L'appuntamento dal service VERO (creazione `crm_manual`)."""
    from appointments.schemas import AppointmentCreate
    base = {"appointment_type": "seller_meeting", "assigned_user_id": y["luca"],
            "start_at": DOMANI_10, "end_at": DOMANI_10 + timedelta(minutes=30),
            "contact_id": y["mario"]}
    base.update(kw)
    return y["service"].create_appointment(y["ctx"](chi), AppointmentCreate(**base))


def conferme(y):
    return [m for m in messaggi(y) if m["reason_code"] == "appointment_confirmation"]


def chiave_conferma(appointment_id, start_at):
    from appointment_reminders import confirmation
    return confirmation.occurrence_key(appointment_id, start_at)


# ---------------------------------------------------------------------------
# 2B - la conferma
# ---------------------------------------------------------------------------

def test_01_fissato_dall_agente_una_conferma_in_coda_con_i_campi_esatti(y):
    riga = crea(y)
    [m] = messaggi(y)
    assert (m["agency_id"], m["contact_id"], m["channel"], m["communication_type"], m["mode"],
            m["reason_code"], m["template_key"], m["template_version"],
            m["destination_snapshot"], m["idempotency_key"], m["status"], m["actor_type"],
            m["actor_user_id"]) == (
        y["a"], y["mario"], "email", "service", "automatic", "appointment_confirmation",
        "appointment_confirmation", 1, "mario@example.it",
        chiave_conferma(riga["id"], DOMANI_10), "queued", "system", None)
    assert m["metadata"] == {"kind": "appointment_confirmation", "appointment_id": riga["id"],
                             "variant": "scheduled", "start_epoch": int(DOMANI_10.timestamp()),
                             "occurrence_key": chiave_conferma(riga["id"], DOMANI_10)}
    assert m["subject_snapshot"] == "Conferma: appuntamento venerdì 13 giugno 2031 alle 10:00"
    for atteso in ("Buongiorno Mario,", "venerdì 13 giugno 2031", "10:00",
                   "Mare Immobiliare, 085 123456, info@mare.example"):
        assert atteso in m["rendered_body"], atteso
    # l'appuntamento c'e', e la riga ha la sua storia come sempre
    ev = y["sql"]("SELECT event_type FROM appointment_events WHERE appointment_id = %s",
                  (riga["id"],))
    assert [e[0] for e in ev] == ["created"]


def test_02_la_visita_porta_l_indirizzo_strutturato_mai_il_luogo_libero(y):
    # La creazione di una visita dal service legge colonne di `properties` che
    # lo schema minimo di prova non ha (`code`): la riga nasce via SQL, come in
    # A32-2, e l'aggancio e' chiamato sul cursore CORE come lo chiama il service.
    from appointment_reminders import confirmation
    from core.database import core_cursor
    visita = nuovo(y, tipo="buyer_visit", immobile="casa", luogo="Citofono Rossi, codice 4455")
    with core_cursor(commit=True) as (_c, cur):
        assert confirmation.on_scheduled(cur, y["a"], visita) == "queued"
        assert confirmation.on_scheduled(cur, y["a"], visita) == "queued_idempotent"
    [m] = messaggi(y)
    assert m["subject_snapshot"].startswith("Conferma: visita all'immobile ")
    assert "Via Roma 12, Giulianova" in m["rendered_body"]
    for pezzo in ("Citofono", "4455"):
        assert pezzo not in m["rendered_body"] and pezzo not in str(m["metadata"])


def test_03_fissa_e_poi_conferma_una_sola_conferma_mai_due(y):
    from appointments.schemas import ConfirmBody
    riga = crea(y)
    assert len(conferme(y)) == 1
    confermata = y["service"].confirm_appointment(y["ctx"]("giorgio"), riga["id"],
                                                  ConfirmBody(version=riga["version"]))
    assert confermata["status"] == "confirmed"
    assert len(conferme(y)) == 1                      # la stessa occorrenza: idempotente
    # e creare DIRETTAMENTE una riga gia' confermata la manda una volta
    crea(y, status="confirmed", start_at=DOMANI_10 + timedelta(hours=2),
         end_at=DOMANI_10 + timedelta(hours=2, minutes=30))
    assert len(conferme(y)) == 2


def test_04_una_richiesta_non_ha_conferma_finche_non_viene_fissata(y):
    from appointments.schemas import ScheduleBody
    richiesta = crea(y, status="requested", assigned_user_id=None)
    assert messaggi(y) == []
    fissata = y["service"].schedule_appointment(
        y["ctx"]("giorgio"), richiesta["id"],
        ScheduleBody(version=richiesta["version"], assigned_user_id=y["luca"],
                     start_at=DOMANI_10, end_at=DOMANI_10 + timedelta(minutes=30)))
    assert fissata["status"] == "scheduled"
    [m] = conferme(y)
    assert m["metadata"]["appointment_id"] == richiesta["id"] and m["metadata"]["variant"] == "scheduled"


def test_05_senza_contatto_o_senza_email_niente_conferma_l_appuntamento_si_salva(y):
    crea(y, contact_id=None)
    crea(y, contact_id=y["bruno"], start_at=DOMANI_10 + timedelta(hours=2),
         end_at=DOMANI_10 + timedelta(hours=2, minutes=30))          # Bruno non ha email
    assert y["sql"]("SELECT count(*) FROM appointments")[0][0] == 2
    assert messaggi(y) == []


# ---------------------------------------------------------------------------
# 2D - spostamento e annullamento
# ---------------------------------------------------------------------------

def test_06_spostato_la_conferma_vecchia_si_ritira_e_parte_il_nuovo_orario(y):
    from appointments.schemas import RescheduleBody
    vecchia = crea(y)
    giro(y)                                           # anche il promemoria 24h e' in coda
    assert [m["status"] for m in messaggi(y)] == ["queued", "queued"]
    nuovo_inizio = roma(2031, 6, 14, 11, 0)
    nuova = y["service"].reschedule_appointment(
        y["ctx"]("giorgio"), vecchia["id"],
        RescheduleBody(version=vecchia["version"], start_at=nuovo_inizio,
                       end_at=nuovo_inizio + timedelta(minutes=30)))
    assert nuova["id"] != vecchia["id"] and nuova["rescheduled_from_id"] == vecchia["id"]
    righe = messaggi(y)
    vecchi = [m for m in righe if m["metadata"]["appointment_id"] == vecchia["id"]]
    nuovi = [m for m in righe if m["metadata"]["appointment_id"] == nuova["id"]]
    assert len(vecchi) == 2 and all(m["status"] == "cancelled" for m in vecchi)
    [m] = nuovi
    assert (m["status"], m["reason_code"], m["metadata"]["variant"]) == (
        "queued", "appointment_confirmation", "rescheduled")
    assert m["idempotency_key"] == chiave_conferma(nuova["id"], nuovo_inizio)
    assert m["subject_snapshot"].startswith("Nuovo orario: appuntamento sabato 14 giugno 2031 alle 11:00")
    assert "sostituisce il precedente" in m["rendered_body"]
    # nessuna informazione vecchia nel nuovo testo
    assert "venerdì 13 giugno" not in m["rendered_body"] and "10:00" not in m["rendered_body"]
    # il dispatcher manda SOLO il nuovo orario
    p, c = dispatch(y, now=roma(2031, 6, 12, 10, 5))
    assert p.inviati == [m["id"]] and c["sent"] == 1


def test_07_annullato_promemoria_e_conferma_in_coda_si_ritirano_subito(y):
    from appointments.schemas import CancelBody
    riga = crea(y)
    giro(y)
    assert len(messaggi(y)) == 2
    annullata = y["service"].cancel_appointment(
        y["ctx"]("giorgio"), riga["id"], CancelBody(version=riga["version"], reason="cliente"))
    assert annullata["status"] == "cancelled"
    assert [m["status"] for m in messaggi(y)] == ["cancelled", "cancelled"]
    # il dispatcher non trova piu' nulla: niente parte, niente si ricrea
    p, c = dispatch(y, now=roma(2031, 6, 12, 10, 5))
    assert p.inviati == [] and c["sent"] == 0
    assert giro(y)["queued"] == 0 and len(messaggi(y)) == 2


def test_08_il_ritiro_tocca_solo_i_messaggi_di_quell_appuntamento(y):
    from appointments.schemas import CancelBody
    primo = crea(y)
    secondo = crea(y, start_at=DOMANI_10 + timedelta(hours=2),
                   end_at=DOMANI_10 + timedelta(hours=2, minutes=30))
    # un messaggio dell'operatore allo stesso contatto non e' una notifica
    from communication import service as cs
    cs.enqueue(owner(y), contact_id=y["mario"], channel="email", communication_type="service",
               mode="manual", reason_code="operator_manual", rendered_body="ciao",
               destination_snapshot="mario@example.it", idempotency_key="manuale-1",
               subject_snapshot="x")
    y["service"].cancel_appointment(y["ctx"]("giorgio"), primo["id"],
                                    CancelBody(version=primo["version"]))
    stati = {(m["reason_code"], (m["metadata"] or {}).get("appointment_id")): m["status"]
             for m in messaggi(y)}
    assert stati == {("appointment_confirmation", primo["id"]): "cancelled",
                     ("appointment_confirmation", secondo["id"]): "queued",
                     ("operator_manual", None): "queued"}


def test_09_gia_reclamato_dal_dispatcher_lo_chiude_la_revalida_non_il_ritiro(y):
    from appointments.schemas import CancelBody
    from communication import service as cs
    riga = crea(y)
    [m] = messaggi(y)
    preso = cs.claim_message(owner(y), m["id"], provider="prova", channel="email")
    assert preso["message"]["status"] == "sending"
    token = preso["attempt"]["claim_token"]
    y["service"].cancel_appointment(y["ctx"]("giorgio"), riga["id"],
                                    CancelBody(version=riga["version"]))
    [m] = messaggi(y)
    assert m["status"] == "sending"                   # il compare-and-set non lo tocca
    cs.finalize_suppressed(owner(y), m["id"], token, reason="status_not_allowed")
    assert messaggi(y)[0]["status"] == "suppressed"


# ---------------------------------------------------------------------------
# 2E - sicurezza, consensi, duplicati, spegnimento
# ---------------------------------------------------------------------------

def test_10_spente_per_impostazione_nessun_messaggio(y, monkeypatch):
    monkeypatch.delenv("APPOINTMENT_CONFIRMATIONS", raising=False)
    crea(y)
    assert messaggi(y) == []
    monkeypatch.setenv("APPOINTMENT_CONFIRMATIONS", "0")
    crea(y, start_at=DOMANI_10 + timedelta(hours=2), end_at=DOMANI_10 + timedelta(hours=2, minutes=30))
    assert messaggi(y) == [] and y["sql"]("SELECT count(*) FROM appointments")[0][0] == 2


def test_11_allowlist_del_collaudo_solo_l_indirizzo_autorizzato(y, monkeypatch):
    monkeypatch.setenv("APPOINTMENT_NOTIFICATIONS_ALLOWED_RECIPIENTS", "prova@example.test")
    crea(y)
    assert messaggi(y) == []                          # Mario non e' autorizzato
    assert giro(y)["skipped_by_reason"]["recipient_not_allowed"] == 1
    monkeypatch.setenv("APPOINTMENT_NOTIFICATIONS_ALLOWED_RECIPIENTS",
                       "prova@example.test, Mario@Example.it")
    crea(y, start_at=DOMANI_10 + timedelta(hours=2), end_at=DOMANI_10 + timedelta(hours=2, minutes=30))
    assert len(conferme(y)) == 1
    # e la revalida rilegge l'allowlist al momento dell'invio
    monkeypatch.setenv("APPOINTMENT_NOTIFICATIONS_ALLOWED_RECIPIENTS", "prova@example.test")
    p, c = dispatch(y, now=roma(2031, 6, 12, 10, 5))
    assert p.inviati == [] and conferme(y)[0]["suppressed_reason"] == "recipient_not_allowed"


def test_12_la_conferma_e_di_servizio_parte_una_volta_senza_consenso_marketing(y, monkeypatch):
    from communication import dispatcher
    from types import SimpleNamespace
    # il consenso marketing e' negato: per un messaggio di SERVIZIO non si interroga
    monkeypatch.setattr(dispatcher, "can_send_marketing",
                        lambda ctx, contact_id, *, channel: SimpleNamespace(allowed=False, reason="denied"))
    crea(y)
    p, c = dispatch(y, now=roma(2031, 6, 12, 10, 5))
    assert len(p.inviati) == 1 and c["sent"] == 1
    # una seconda passata non la rimanda
    p, c = dispatch(y, now=roma(2031, 6, 12, 10, 6))
    assert p.inviati == [] and c["sent"] == 0 and len(messaggi(y)) == 1


def test_13_annullato_da_un_altro_percorso_la_revalida_la_ferma_prima_del_provider(y):
    """La riga cambia stato senza passare dal service (es. una migrazione dati,
    un vecchio script): la conferma in coda non parte comunque."""
    riga = crea(y)
    y["sql"]("UPDATE appointments SET status = 'cancelled', cancelled_at = NOW() WHERE id = %s",
             (riga["id"],))
    p, c = dispatch(y, now=roma(2031, 6, 12, 10, 5))
    assert p.inviati == [] and c["suppressed"] == 1
    assert messaggi(y)[0]["suppressed_reason"] == "status_not_allowed"


def test_14_isolamento_fra_agenzie(y):
    riga_a = crea(y)
    [m] = messaggi(y)
    assert m["agency_id"] == y["a"]
    # l'agenzia B non vede ne' spedisce la conferma di A
    p, _ = dispatch(y, now=roma(2031, 6, 12, 10, 5), agenzia="b")
    assert p.inviati == []
    # e il ritiro dall'agenzia B non tocca i messaggi di A
    from appointment_reminders import confirmation
    from core.database import core_cursor
    with core_cursor(commit=True) as (_c, cur):
        assert confirmation.on_closed(cur, y["b"], riga_a["id"]) == 0
    assert messaggi(y)[0]["status"] == "queued"


def test_15_senza_la_127_l_appuntamento_si_salva_e_nessun_messaggio(y):
    conn = y["conn"]
    with conn.cursor() as cur:
        cur.execute(SENZA_127)
    conn.commit()
    try:
        riga = crea(y)
        assert riga["status"] == "scheduled" and messaggi(y) == []
        # un secondo appuntamento nella stessa sessione: il savepoint ha
        # lasciato la transazione sana
        crea(y, start_at=DOMANI_10 + timedelta(hours=2), end_at=DOMANI_10 + timedelta(hours=2, minutes=30))
        assert y["sql"]("SELECT count(*) FROM appointments")[0][0] == 2
    finally:
        with conn.cursor() as cur:
            cur.execute(_testo(VERSIONE_127))
        conn.commit()
    crea(y, start_at=DOMANI_10 + timedelta(hours=4), end_at=DOMANI_10 + timedelta(hours=4, minutes=30))
    assert len(conferme(y)) == 1


def test_16_il_log_dell_errore_non_porta_dati_personali(y, monkeypatch, caplog):
    from appointment_reminders import confirmation

    def esplode(*_a, **_k):
        raise RuntimeError("template rotto")
    monkeypatch.setattr(confirmation.template, "render_confirmation_email", esplode)
    with caplog.at_level("ERROR"):
        riga = crea(y)
    assert riga["status"] == "scheduled" and messaggi(y) == []
    testo = " ".join(r.getMessage() for r in caplog.records)
    assert "appointment confirmation failed" in testo and "mario@example.it" not in testo


# ---------------------------------------------------------------------------
# 2C - il promemoria a 2 ore, configurabile
# ---------------------------------------------------------------------------

def test_17_offset_predefinito_solo_24h(y):
    nuovo(y)
    c = giro(y)
    assert c["offsets"] == ["24h"] and c["queued"] == 1
    [m] = messaggi(y)
    assert m["template_key"] == "appointment_reminder_24h" and ":24h:" in m["idempotency_key"]


def test_18_con_24h_e_2h_due_promemoria_distinti_ognuno_al_suo_momento(y, monkeypatch):
    from appointment_reminders import policy
    monkeypatch.setenv("APPOINTMENT_REMINDER_OFFSETS", "24h,2h")
    app = nuovo(y)
    c = giro(y)                                       # 12/6 10:00: il 24h e' dovuto
    assert c["offsets"] == ["24h", "2h"] and c["queued"] == 1 and c["not_due"] == 1
    [m24] = messaggi(y)
    assert m24["template_key"] == "appointment_reminder_24h"
    # il cron alterna giro e dispatch: il 24h parte subito dopo
    p, c = dispatch(y, now=roma(2031, 6, 12, 10, 5))
    assert p.inviati == [m24["id"]] and c["sent"] == 1
    c = giro(y, now=roma(2031, 6, 13, 7, 59))         # il 2h non ancora
    # (il 24h, a meno di 3 ore, non e' piu' idoneo: la sua regola di sempre)
    assert c["queued"] == 0 and c["not_due"] == 1
    assert c["skipped_by_reason"]["less_than_3h_left"] == 1
    c = giro(y, now=roma(2031, 6, 13, 8, 0))
    assert c["queued"] == 1 and c["ineligible"] == 1
    righe = messaggi(y)
    assert [m["template_key"] for m in righe] == ["appointment_reminder_24h", "appointment_reminder_2h"]
    assert righe[1]["idempotency_key"] == policy.occurrence_key(app, DOMANI_10, "2h")
    assert righe[1]["metadata"]["offset"] == "2h"
    assert "tra circa due ore" in righe[1]["rendered_body"]
    assert "Promemoria: appuntamento venerdì 13 giugno 2031 alle 10:00" == righe[1]["subject_snapshot"]
    # e il 2h parte, dopo la revalida, una volta
    p, c = dispatch(y, now=roma(2031, 6, 13, 8, 5))
    assert p.inviati == [righe[1]["id"]] and c["sent"] == 1
    assert giro(y, now=roma(2031, 6, 13, 8, 10))["queued"] == 0
    assert [m["status"] for m in messaggi(y)] == ["sent", "sent"]


def test_19_il_2h_di_un_appuntamento_alle_9_e_fuori_finestra_e_non_parte(y, monkeypatch):
    monkeypatch.setenv("APPOINTMENT_REMINDER_OFFSETS", "2h")
    nuovo(y, inizio=roma(2031, 6, 13, 9, 0))
    c = giro(y, now=roma(2031, 6, 13, 7, 0))
    assert c["offsets"] == ["2h"] and c["skipped_by_reason"]["outside_send_window"] == 1
    assert messaggi(y) == []
    c = giro(y, now=roma(2031, 6, 13, 8, 30))         # 30 minuti prima: troppo tardi
    assert c["skipped_by_reason"]["less_than_3h_left"] == 1 and messaggi(y) == []


def test_20_spostato_il_2h_in_coda_si_ritira_con_il_resto(y, monkeypatch):
    from appointments.schemas import RescheduleBody
    monkeypatch.setenv("APPOINTMENT_REMINDER_OFFSETS", "24h,2h")
    riga = crea(y)
    giro(y)
    giro(y, now=roma(2031, 6, 13, 8, 0))
    assert sorted(m["reason_code"] for m in messaggi(y)) == [
        "appointment_confirmation", "appointment_reminder", "appointment_reminder"]
    nuovo_inizio = roma(2031, 6, 14, 11, 0)
    y["service"].reschedule_appointment(
        y["ctx"]("giorgio"), riga["id"],
        RescheduleBody(version=riga["version"], start_at=nuovo_inizio,
                       end_at=nuovo_inizio + timedelta(minutes=30)))
    vecchi = [m for m in messaggi(y) if m["metadata"]["appointment_id"] == riga["id"]]
    assert len(vecchi) == 3 and {m["status"] for m in vecchi} == {"cancelled"}


# ---------------------------------------------------------------------------
# la 127: up, down, re-up (e il down rifiuta se ci sono conferme)
# ---------------------------------------------------------------------------

def test_21_la_127_down_e_re_up_e_il_down_rifiuta_con_conferme_nel_ledger(y):
    from tests.test_a32_1_reminders_postgres import _definizione
    conn = y["conn"]
    giu = (MIGRAZIONI / f"{VERSIONE_127}_down.sql").read_text(encoding="utf-8")
    with conn.cursor() as cur:
        assert "appointment_confirmation" in _definizione(cur)
        # DOWN a ledger vuoto: il valore sparisce, il resto resta
        cur.execute(giu)
        dopo = _definizione(cur)
        assert "appointment_confirmation" not in dopo
        for atteso in ("appointment_reminder", "auto_reply", "operator_reply", "m20"):
            assert atteso in dopo, atteso
        # re-UP dal file vero
        cur.execute(_testo(VERSIONE_127))
        assert "appointment_confirmation" in _definizione(cur)
    conn.commit()
    # con una conferma nel ledger il DOWN rifiuta e non tocca il vincolo
    crea(y)
    assert len(conferme(y)) == 1
    with pytest.raises(Exception) as errore:
        with conn.cursor() as cur:
            cur.execute(giu)
    conn.rollback()
    assert "appointment_confirmation messages exist" in str(errore.value)
    with conn.cursor() as cur:
        assert "appointment_confirmation" in _definizione(cur)
