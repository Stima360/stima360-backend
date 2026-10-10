"""STIMA Voice Fase 3 - il registro dei comandi (migration 098 +
voice/ledger.py + voice/settings.py) su PostgreSQL vero.

Stessa fixture della Fase 2 (schema CRM completo applicato dal runner, ora
fino alla 098; due agenzie; titolare, amministratore e due agenti). Nessun
servizio del CRM e' chiamato: i passi eseguono operazioni finte che
simulano un servizio con e senza chiave di idempotenza.

Opt-in `P29_TEST_DSN`, SOLO database locale usa-e-getta.
"""
from __future__ import annotations

import sys
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.test_voice_2_resolver_postgres import DSN, ctx, db, plan  # noqa: F401  (fixture condivisa)

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare STIMA Voice Fase 3")

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 10, 14, 0, tzinfo=timezone.utc)


def _q(c, sql, params=None):
    with c["conn"].cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else None


def _errore(c, sql, params=None):
    import psycopg2
    conn = psycopg2.connect(c["dsn"])
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()
        return None
    except psycopg2.Error as exc:
        conn.rollback()
        return str(exc)
    finally:
        conn.close()


def _decide(p, **facts):
    from voice.policy import Facts, Settings, decide
    return decide(p, Facts(**facts), Settings(mode="auto"))


def nuovo_contatto(nome="Elena", cognome="Costa", tel="333 7777777"):
    return {"intent": "create_contact", "quote": "nuovo", "first_name": nome, "last_name": cognome, "phone": tel}


def vendita():
    """Contatto nuovo (at_most_once) -> unita' (replayable) -> Vende (replayable)."""
    return plan(nuovo_contatto(),
                {"intent": "create_unit", "quote": "vuole vendere", "address": "Via Nuova", "civic_number": "1",
                 "city": "Pineto", "owner": {"step": 1}},
                {"intent": "activate_seller", "quote": "vuole vendere", "contact": {"step": 1}, "property": {"step": 2}},
                transcript="Elena Costa 333 7777777 vuole vendere in via Nuova 1 a Pineto")


def apri(db, chi, *, chiave=None, testo="vocale", **kw):
    from voice import ledger
    chiave = chiave or uuid.uuid4()
    return ledger.open_command(ctx(db, chi, **kw), chiave, input_kind="text",
                               request_sha256=ledger.request_fingerprint("text", testo), recorded_at=NOW)


def pianificato(db, chi="agent_a1", p=None, decisione=None):
    from voice import ledger
    p = p or vendita()
    comando, _ = apri(db, chi, testo=p.transcript)
    registrato = ledger.record_plan(ctx(db, chi), comando["id"], p, decisione or _decide(p), mode="auto", retention_days=30)
    return registrato


# ---------------------------------------------------------------------------
# MIGRAZIONE
# ---------------------------------------------------------------------------

def test_01_migration_098_is_valid_contiguous_and_has_a_down():
    sys.path.insert(0, str(ROOT / "scripts"))
    import p26_migrate as runner
    tutte = runner.discover_migrations(runner.MIGRATIONS_DIR)
    runner.verify_contiguous(tutte)
    m = [x for x in tutte if x.number == 98]
    assert [x.version for x in m] == ["098_voice_commands"] and m[0].down_available
    assert runner.validate_migration(m[0]) == []
    assert tutte[-1].number == 98


def test_02_applied_by_the_runner_with_its_guards(db):
    tabelle = {r[0] for r in _q(db, "SELECT table_name FROM information_schema.tables WHERE table_name LIKE 'voice_%%'")}
    assert tabelle == {"voice_agency_settings", "voice_commands", "voice_command_steps", "voice_command_events"}
    assert _q(db, "SELECT 1 FROM schema_migrations WHERE version = '098_voice_commands'")
    trigger = {r[0] for r in _q(db, "SELECT tgname FROM pg_trigger WHERE tgname LIKE 'trg_voice_%%'")}
    assert trigger == {"trg_voice_commands_guard", "trg_voice_command_steps_guard", "trg_voice_commands_no_delete",
                       "trg_voice_command_steps_no_delete", "trg_voice_command_events_append_only"}


def test_03_down_refuses_with_data_and_runs_on_empty(db):
    import psycopg2
    giu = (ROOT / "migrations" / "098_voice_commands_down.sql").read_text(encoding="utf-8")
    pianificato(db)  # almeno un comando
    conn = psycopg2.connect(db["dsn"])
    try:
        with conn.cursor() as cur, pytest.raises(psycopg2.errors.RaiseException):
            cur.execute(giu)
        conn.rollback()
        with conn.cursor() as cur:  # registro vuoto (dentro questa transazione): la down passa
            cur.execute("ALTER TABLE voice_command_events DISABLE TRIGGER USER; ALTER TABLE voice_command_steps DISABLE TRIGGER USER; "
                        "ALTER TABLE voice_commands DISABLE TRIGGER USER; DELETE FROM voice_command_events; "
                        "DELETE FROM voice_command_steps; DELETE FROM voice_commands; DELETE FROM voice_agency_settings")
            cur.execute(giu)
            cur.execute("SELECT count(*) FROM information_schema.tables WHERE table_name LIKE 'voice_%%'")
            assert cur.fetchone()[0] == 0
    finally:
        conn.rollback()
        conn.close()
    assert _q(db, "SELECT count(*) FROM voice_commands")[0][0] >= 1  # niente e' stato toccato davvero


# ---------------------------------------------------------------------------
# DUPLICATI
# ---------------------------------------------------------------------------

def test_04_same_key_same_content_is_the_same_command(db):
    chiave = uuid.uuid4()
    a, creato_a = apri(db, "agent_a1", chiave=chiave, testo="nota per Mario")
    b, creato_b = apri(db, "agent_a1", chiave=str(chiave).upper(), testo="nota per Mario")
    assert creato_a and not creato_b and a["id"] == b["id"]
    eventi = [r[0] for r in _q(db, "SELECT event_type FROM voice_command_events WHERE command_id = %s ORDER BY id", (a["id"],))]
    assert eventi == ["received", "replayed"]


def test_05_same_key_different_content_is_an_error(db):
    from voice.ledger import IdempotencyConflict
    chiave = uuid.uuid4()
    apri(db, "agent_a1", chiave=chiave, testo="nota per Mario")
    with pytest.raises(IdempotencyConflict) as e:
        apri(db, "agent_a1", chiave=chiave, testo="nota per Giulia")
    assert e.value.code == "VOICE_IDEMPOTENCY_KEY_REUSED"
    assert _q(db, "SELECT count(*) FROM voice_commands WHERE client_command_id = %s", (str(chiave),))[0][0] == 1


def test_06_same_key_other_actor_or_agency_is_another_command(db):
    chiave = uuid.uuid4()
    a, _ = apri(db, "agent_a1", chiave=chiave)
    b, creato = apri(db, "agent_a2", chiave=chiave)
    c, creato_c = apri(db, "owner_b", chiave=chiave)
    assert creato and creato_c and len({a["id"], b["id"], c["id"]}) == 3


def test_07_concurrent_opens_create_exactly_one(db):
    from voice import ledger
    chiave, esiti, errori = uuid.uuid4(), [], []

    def invio(testo):
        try:
            esiti.append(apri(db, "agent_a1", chiave=chiave, testo=testo))
        except ledger.IdempotencyConflict as exc:
            errori.append(exc)

    thread = [threading.Thread(target=invio, args=("stesso vocale",)) for _ in range(8)]
    thread += [threading.Thread(target=invio, args=("vocale diverso",)) for _ in range(4)]
    for t in thread:
        t.start()
    for t in thread:
        t.join()
    assert _q(db, "SELECT count(*) FROM voice_commands WHERE client_command_id = %s", (str(chiave),))[0][0] == 1
    assert sum(1 for _, creato in esiti if creato) == 1
    assert len({r["id"] for r, _ in esiti}) == 1
    assert len(esiti) + len(errori) == 12 and errori  # chi ha mandato l'altro contenuto riceve l'errore


def test_08_plan_is_recorded_once_and_a_different_plan_is_an_error(db):
    from voice import ledger
    p = vendita()
    comando, _ = apri(db, "agent_a1", testo=p.transcript)
    c = ctx(db, "agent_a1")
    primo = ledger.record_plan(c, comando["id"], p, _decide(p), mode="auto", retention_days=30)
    secondo = ledger.record_plan(c, comando["id"], p, _decide(p), mode="auto", retention_days=30)
    assert primo["frozen"] == secondo["frozen"] and len(secondo["steps"]) == 3
    diverso = plan(nuovo_contatto(nome="Altro"), transcript=p.transcript)
    with pytest.raises(ledger.IdempotencyConflict):
        ledger.record_plan(c, comando["id"], diverso, _decide(diverso), mode="auto", retention_days=30)


def test_09_replayable_steps_get_a_frozen_key_at_most_once_do_not(db):
    r = pianificato(db)
    passi = {s["ordinal"]: s for s in r["steps"]}
    assert passi[1]["step_class"] == "at_most_once" and passi[1]["client_request_id"] is None
    for n in (2, 3):
        assert passi[n]["step_class"] == "replayable"
        assert str(passi[n]["client_request_id"]) == r["frozen"][f"step:{n}:client_request_id"]
    assert r["status"] == "planned" and r["transcript"] and r["transcript_expires_at"] is not None


def test_10_blocked_steps_are_skipped_and_questions_wait(db):
    from voice.policy import RefFacts
    p = plan({"intent": "unsupported", "quote": "cancella", "description": "cancella il contatto"},
             {"intent": "add_note", "quote": "nota", "text": "ok", "contact": {"first_name": "Mario", "last_name": "Rossi"}})
    d = _decide(p, refs={(2, "contact"): RefFacts(1, exact=False)})
    r = pianificato(db, p=p, decisione=d)
    passi = {s["ordinal"]: s for s in r["steps"]}
    assert passi[1]["state"] == "skipped" and passi[1]["decision"] == "blocked"
    assert passi[2]["decision"] == "ask" and r["status"] == "awaiting_answers"


# ---------------------------------------------------------------------------
# SEPARAZIONE TRA AGENZIE E AGENTI
# ---------------------------------------------------------------------------

def test_11_nobody_else_sees_or_advances_a_command(db):
    from voice import ledger
    r = pianificato(db, "agent_a1")
    for chi in ("agent_a2", "owner_a", "admin_a", "owner_b", "agent_b"):
        c = ctx(db, chi)
        with pytest.raises(ledger.CommandNotFound):
            ledger.get_command(c, r["id"])
        with pytest.raises(ledger.CommandNotFound):
            ledger.begin_step(c, r["id"], 1)
        with pytest.raises(ledger.CommandNotFound):
            ledger.cancel(c, r["id"])
        with pytest.raises(ledger.CommandNotFound):
            ledger.freeze(c, r["id"], "x", lambda: 1)
    assert ledger.get_command(ctx(db, "agent_a1"), r["id"])["id"] == r["id"]


def test_12_no_agency_or_no_actor_is_refused_before_any_connection(db, monkeypatch):
    from core import database as core_database
    from operator_auth.exceptions import PlatformAdminAgencyRequired
    from voice import ledger
    aperture = []
    monkeypatch.setattr(core_database, "get_connection", lambda: aperture.append(1))
    with pytest.raises(PlatformAdminAgencyRequired):
        ledger.open_command(ctx(db, "owner_a", agenzia=None, ruolo=None, platform=True), uuid.uuid4(),
                            input_kind="text", request_sha256="0" * 64, recorded_at=NOW)
    from operator_auth.context import OperatorContext
    senza_attore = OperatorContext(user_id=None, agency_id=1, role="agency_owner", is_platform_admin=False,
                                   session_id=None, auth_channel="basic")
    with pytest.raises(ledger.ActorRequired):
        ledger.get_command(senza_attore, 1)
    assert aperture == []


def test_13_step_and_event_agency_must_match_the_command(db):
    r = pianificato(db, "agent_a1")
    assert "step agency differs" in _errore(db, "INSERT INTO voice_command_steps (command_id, ordinal, agency_id, intent, decision, step_class) "
                                               "VALUES (%s, 9, 2, 'add_note', 'auto', 'at_most_once')", (r["id"],))
    assert "event agency differs" in _errore(db, "INSERT INTO voice_command_events (agency_id, command_id, event_type) VALUES (2, %s, 'replayed')",
                                             (r["id"],))


# ---------------------------------------------------------------------------
# ESECUZIONE: chi puo' partire, e in che ordine
# ---------------------------------------------------------------------------

def test_14_blocked_never_runs_ask_needs_confirmation(db):
    from voice import ledger
    from voice.policy import RefFacts
    p = plan({"intent": "unsupported", "quote": "x", "description": "d"},
             {"intent": "add_note", "quote": "nota", "text": "ok", "contact": {"first_name": "Mario", "last_name": "Rossi"}})
    r = pianificato(db, p=p, decisione=_decide(p, refs={(2, "contact"): RefFacts(1, exact=False)}))
    c = ctx(db, "agent_a1")
    with pytest.raises(ledger.StepNotAllowed):
        ledger.begin_step(c, r["id"], 1, confirmed=True)
    with pytest.raises(ledger.StepNotAllowed):
        ledger.begin_step(c, r["id"], 2)
    assert ledger.begin_step(c, r["id"], 2, confirmed=True)["state"] == "running"


def test_15_dependencies_must_have_succeeded(db):
    from voice import ledger
    r = pianificato(db)
    c = ctx(db, "agent_a1")
    with pytest.raises(ledger.StepNotAllowed):
        ledger.begin_step(c, r["id"], 2)                 # dipende dal contatto (1)
    ledger.run_step(c, r["id"], 1, lambda passo: {"contact_id": 501})
    assert ledger.begin_step(c, r["id"], 2)["state"] == "running"
    with pytest.raises(ledger.InvalidTransition):
        ledger.begin_step(c, r["id"], 2)                 # gia' in esecuzione


def test_16_complete_run_and_history(db):
    from voice import ledger
    r = pianificato(db)
    c = ctx(db, "agent_a1")
    with ledger.executing(c, r["id"]):
        for n, esito in ((1, {"contact_id": 1}), (2, {"property_id": 2}), (3, {"lead_id": 3})):
            ledger.run_step(c, r["id"], n, lambda passo, esito=esito: esito)
        assert ledger.finalize(c, r["id"]) == "completed"
    finale = ledger.get_command(c, r["id"])
    assert finale["status"] == "completed" and finale["completed_at"] is not None
    assert [s["result"] for s in finale["steps"]] == [{"contact_id": 1}, {"property_id": 2}, {"lead_id": 3}]
    eventi = [e[0] for e in _q(db, "SELECT event_type FROM voice_command_events WHERE command_id = %s ORDER BY id", (r["id"],))]
    assert eventi[:2] == ["received", "planned"] and eventi[-1] == "status_changed"
    assert eventi.count("step_succeeded") == 3


# ---------------------------------------------------------------------------
# CRASH, RIPRESA, ESECUZIONI CONCORRENTI
# ---------------------------------------------------------------------------

class FakeCrm:
    """Un CRM finto: i servizi ripetibili deduplicano per chiave (come
    census.create_unit o appointments), quelli senza chiave creano sempre."""

    def __init__(self):
        self.con_chiave: dict[str, int] = {}
        self.senza_chiave = 0

    def replayable(self, passo):
        chiave = str(passo["client_request_id"])
        self.con_chiave.setdefault(chiave, len(self.con_chiave) + 1)
        return {"id": self.con_chiave[chiave]}

    def at_most_once(self, passo):
        self.senza_chiave += 1
        return {"id": self.senza_chiave}


def _crash_dopo_il_servizio(c, command_id, ordinal, operazione):
    """Il servizio scrive, poi il processo muore prima del checkpoint."""
    from voice import ledger
    passo = ledger.begin_step(c, command_id, ordinal)
    operazione(passo)


def test_17_crash_replayable_resumes_with_the_same_key(db):
    from voice import ledger
    r = pianificato(db)
    c, crm = ctx(db, "agent_a1"), FakeCrm()
    ledger.run_step(c, r["id"], 1, crm.at_most_once)
    chiave = str(ledger.get_command(c, r["id"])["steps"][1]["client_request_id"])
    for _ in range(3):                                   # tre crash di fila sullo stesso passo
        _crash_dopo_il_servizio(c, r["id"], 2, crm.replayable)
        assert ledger.recover(c, r["id"]) == [2]
    with ledger.executing(c, r["id"]):
        ledger.run_step(c, r["id"], 2, crm.replayable)
    passo = ledger.get_command(c, r["id"])["steps"][1]
    assert passo["state"] == "succeeded" and str(passo["client_request_id"]) == chiave and passo["attempts"] == 4
    assert crm.con_chiave == {chiave: 1}               # una sola unita' nel CRM


def test_18_crash_at_most_once_becomes_indeterminate_and_never_repeats(db):
    from voice import ledger
    r = pianificato(db)
    c, crm = ctx(db, "agent_a1"), FakeCrm()
    _crash_dopo_il_servizio(c, r["id"], 1, crm.at_most_once)
    with ledger.executing(c, r["id"]):                   # la ripresa automatica
        pass
    passo = ledger.get_command(c, r["id"])["steps"][0]
    assert passo["state"] == "indeterminate" and passo["error_type"] == "interrupted"
    for tentativo in (ledger.begin_step, ledger.retry_step):
        with pytest.raises((ledger.InvalidTransition, ledger.StepNotAllowed)):
            tentativo(c, r["id"], 1)
    assert crm.senza_chiave == 1                         # il contatto e' stato creato UNA volta
    assert "not allowed" in _errore(db, "UPDATE voice_command_steps SET state = 'pending' WHERE command_id = %s AND ordinal = 1", (r["id"],))
    with pytest.raises(ledger.StepNotAllowed):
        ledger.begin_step(c, r["id"], 2)                 # chi dipende dal contatto aspetta la verifica


def test_19_errors_safe_and_uncertain(db):
    import psycopg2
    from voice import ledger
    r = pianificato(db)
    c = ctx(db, "agent_a1")

    def rifiutato(passo):
        raise ValueError("validazione del CRM")

    def connessione_caduta(passo):
        raise psycopg2.OperationalError("server closed the connection unexpectedly")

    with pytest.raises(ValueError):
        ledger.run_step(c, r["id"], 1, rifiutato)
    assert ledger.get_command(c, r["id"])["steps"][0]["state"] == "failed"
    ledger.retry_step(c, r["id"], 1)
    with pytest.raises(psycopg2.OperationalError):
        ledger.run_step(c, r["id"], 1, connessione_caduta)
    assert ledger.get_command(c, r["id"])["steps"][0]["state"] == "indeterminate"   # at_most_once: forse creato
    with pytest.raises(ledger.InvalidTransition):                      # mai ritentato da solo
        ledger.run_step(c, r["id"], 1, lambda p: {})
    q = pianificato(db)
    ledger.run_step(c, q["id"], 1, lambda p: {"contact_id": 1})
    with pytest.raises(psycopg2.OperationalError):
        ledger.run_step(c, q["id"], 2, connessione_caduta)
    assert ledger.get_command(c, q["id"])["steps"][1]["state"] == "failed"           # replayable: si ripete con la chiave
    ledger.retry_step(c, q["id"], 2)


def test_20_one_executor_at_a_time_and_lock_freed_on_crash(db):
    import psycopg2
    from voice import ledger
    r = pianificato(db)
    c = ctx(db, "agent_a1")
    with ledger.executing(c, r["id"]):
        with pytest.raises(ledger.CommandBusy):
            with ledger.executing(c, r["id"]):
                pass
    morto = psycopg2.connect(db["dsn"])                   # un esecutore che muore tenendo il lock
    with morto.cursor() as cur:
        cur.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (f"voice:execute:{r['id']}",))
    with pytest.raises(ledger.CommandBusy):
        with ledger.executing(c, r["id"]):
            pass
    morto.close()
    with ledger.executing(c, r["id"]):
        pass


def test_21_concurrent_executors_run_each_step_once(db):
    from voice import ledger
    r = pianificato(db)
    c, crm = ctx(db, "agent_a1"), FakeCrm()
    esiti = []

    def esecutore():
        try:
            with ledger.executing(c, r["id"]):
                for n, op in ((1, crm.at_most_once), (2, crm.replayable), (3, crm.replayable)):
                    try:
                        ledger.run_step(c, r["id"], n, op)
                    except ledger.InvalidTransition:
                        pass
                ledger.finalize(c, r["id"])
            esiti.append("ok")
        except ledger.CommandBusy:
            esiti.append("busy")

    thread = [threading.Thread(target=esecutore) for _ in range(6)]
    for t in thread:
        t.start()
    for t in thread:
        t.join()
    assert "ok" in esiti and crm.senza_chiave == 1 and len(crm.con_chiave) == 2
    assert ledger.get_command(c, r["id"])["status"] == "completed"


def test_22_partial_then_resume_then_complete(db):
    from voice import ledger
    r = pianificato(db)
    c, crm = ctx(db, "agent_a1"), FakeCrm()
    ledger.run_step(c, r["id"], 1, crm.at_most_once)
    with pytest.raises(RuntimeError):
        ledger.run_step(c, r["id"], 2, lambda p: (_ for _ in ()).throw(RuntimeError("giu'")))
    assert ledger.finalize(c, r["id"]) == "partial"
    ledger.retry_step(c, r["id"], 2)
    with ledger.executing(c, r["id"]):
        ledger.run_step(c, r["id"], 2, crm.replayable)
        ledger.run_step(c, r["id"], 3, crm.replayable)
        assert ledger.finalize(c, r["id"]) == "completed"
    assert crm.senza_chiave == 1 and len(crm.con_chiave) == 2


# ---------------------------------------------------------------------------
# GUARDIE DEL DATABASE
# ---------------------------------------------------------------------------

def test_23_database_guards(db):
    r = pianificato(db)
    i = r["id"]
    casi = {
        "identity of a voice command is immutable": ("UPDATE voice_commands SET actor_user_id = %s WHERE id = %s", (db["ids"]["agent_a2"], i)),
        "plan of a voice command is immutable": ("UPDATE voice_commands SET plan = '{}'::jsonb WHERE id = %s", (i,)),
        "frozen value": ("UPDATE voice_commands SET frozen = '{}'::jsonb WHERE id = %s", (i,)),
        "status planned -> completed": ("UPDATE voice_commands SET status = 'completed', completed_at = NOW() WHERE id = %s", (i,)),
        "never deleted": ("DELETE FROM voice_commands WHERE id = %s", (i,)),
        "identity of a step is immutable": ("UPDATE voice_command_steps SET intent = 'add_note' WHERE command_id = %s AND ordinal = 2", (i,)),
        "step state pending -> succeeded": ("UPDATE voice_command_steps SET state = 'succeeded' WHERE command_id = %s AND ordinal = 1", (i,)),
        "transcript cannot be rewritten": ("UPDATE voice_commands SET transcript = 'altro' WHERE id = %s", (i,)),
        "append-only": ("UPDATE voice_command_events SET detail = '{}'::jsonb WHERE command_id = %s", (i,)),
    }
    for atteso, (sql, params) in casi.items():
        errore = _errore(db, sql, params)
        assert errore and atteso in errore, (atteso, errore)
    assert "append-only" in _errore(db, "DELETE FROM voice_command_events WHERE command_id = %s", (i,))
    assert _errore(db, "INSERT INTO voice_command_events (agency_id, command_id, event_type, detail) "
                       "VALUES (1, %s, 'replayed', '{\"transcript\": \"x\"}')", (i,))


def test_24_the_same_key_on_a_step_cannot_be_added_twice(db):
    r = pianificato(db)
    assert "duplicate key" in _errore(db, "INSERT INTO voice_command_steps (command_id, ordinal, agency_id, intent, decision, step_class) "
                                          "VALUES (%s, 1, 1, 'add_note', 'auto', 'at_most_once')", (r["id"],))
    assert "voice_steps_key_chk" in _errore(db, "INSERT INTO voice_command_steps (command_id, ordinal, agency_id, intent, decision, step_class) "
                                                "VALUES (%s, 7, 1, 'create_unit', 'auto', 'replayable')", (r["id"],))


# ---------------------------------------------------------------------------
# TRASCRITTI: scadenza e cancellazione sicura
# ---------------------------------------------------------------------------

def test_25_expired_transcripts_are_purged_and_never_return(db):
    from voice import ledger
    vecchio, nuovo = pianificato(db), pianificato(db)
    _q(db, "ALTER TABLE voice_commands DISABLE TRIGGER trg_voice_commands_guard; "
           "UPDATE voice_commands SET transcript_expires_at = NOW() - INTERVAL '1 day' WHERE id = %s; "
           "ALTER TABLE voice_commands ENABLE TRIGGER trg_voice_commands_guard", (vecchio["id"],))
    assert ledger.purge_expired_transcripts() >= 1
    v = ledger.get_command(ctx(db, "agent_a1"), vecchio["id"])
    n = ledger.get_command(ctx(db, "agent_a1"), nuovo["id"])
    assert v["transcript"] is None and v["transcript_purged_at"] is not None and v["plan"] is not None
    assert n["transcript"] is not None
    assert ledger.purge_expired_transcripts() == 0                  # idempotente
    assert "purged transcript cannot come back" in _errore(db, "UPDATE voice_commands SET transcript = 'ritorno' WHERE id = %s", (vecchio["id"],))
    evento = _q(db, "SELECT actor_user_id, detail FROM voice_command_events WHERE command_id = %s AND event_type = 'transcript_purged'",
                (vecchio["id"],))
    assert [tuple(r) for r in evento] == [(None, {})]


def test_26_retention_comes_from_the_caller_and_events_never_hold_the_transcript(db):
    from voice import ledger
    p = vendita()
    comando, _ = apri(db, "agent_a1", testo=p.transcript)
    ledger.record_plan(ctx(db, "agent_a1"), comando["id"], p, _decide(p), mode="review", retention_days=7)
    giorni = _q(db, "SELECT EXTRACT(DAY FROM transcript_expires_at - created_at) FROM voice_commands WHERE id = %s",
                (comando["id"],))[0][0]
    assert int(giorni) in (6, 7)
    testi = " ".join(str(r[0]) for r in _q(db, "SELECT detail FROM voice_command_events"))
    assert "Costa" not in testi and "7777777" not in testi
    with pytest.raises(ValueError):
        ledger.record_plan(ctx(db, "agent_a1"), comando["id"], p, _decide(p), mode="review", retention_days=0)


# ---------------------------------------------------------------------------
# IMPOSTAZIONI: spento per definizione
# ---------------------------------------------------------------------------

def test_27_settings_default_disabled_and_review(db):
    from voice import settings
    s = settings.load(ctx(db, "agent_a1"))
    assert s.enabled is False and s.policy.mode == "review" and s.transcript_retention_days == 30
    assert _q(db, "SELECT count(*) FROM voice_agency_settings")[0][0] == 0      # nessuna agenzia attivata


def test_28_settings_values_and_checks(db):
    import psycopg2
    from voice import settings
    conn = psycopg2.connect(db["dsn"])
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO voice_agency_settings (agency_id, enabled, mode, auto_intents, transcript_retention_days) "
                        "VALUES (2, TRUE, 'assisted', ARRAY['add_note'], 10)")
        conn.commit()
        s = settings.load(ctx(db, "owner_b"))
        assert s.enabled and s.policy.mode == "assisted" and s.policy.auto_intents == frozenset({"add_note"})
        assert settings.load(ctx(db, "owner_a")).enabled is False                   # l'altra agenzia resta spenta
    finally:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM voice_agency_settings WHERE agency_id = 2")
        conn.commit()
        conn.close()
    for sql in ("INSERT INTO voice_agency_settings (agency_id, mode) VALUES (1, 'turbo')",
                "INSERT INTO voice_agency_settings (agency_id, auto_intents) VALUES (1, ARRAY['delete_everything'])",
                "INSERT INTO voice_agency_settings (agency_id, transcript_retention_days) VALUES (1, 0)"):
        assert _errore(db, sql), sql
