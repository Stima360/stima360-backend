"""STIMA Voice Fase 4 - annullamento protetto, migration 099, protezione dei
moduli esistenti, su PostgreSQL vero.

Opt-in `P29_TEST_DSN`, SOLO database locale usa-e-getta.

Cosa prova:
  * annullamento per ogni intento attraverso i percorsi reversibili gia'
    esistenti (Cestino, «creato per errore», annulla-creazione del censimento,
    annullamento d'appuntamento «per errore»); MAI una DELETE fisica;
  * cio' che non e' annullabile non si offre: collegamento proprietario
    (D-A), «Vende» riutilizzato o riaperto, record preesistenti;
  * finestra di 15 minuti dal momento della creazione, lato server, con
    mezzanotte e cambio dell'ora legale; la finestra non scavalca i blocchi;
  * dipendenze e ordine inverso; annullamento parziale registrato;
  * permessi ricontrollati al momento dell'annullamento;
  * migration 099: valida, append-only, down che rifiuta se popolata;
  * i file protetti del CRM sono identici al commit di partenza.
"""
from __future__ import annotations

import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.test_voice_2_resolver_postgres import DSN, ctx, db  # noqa: F401  (fixture condivisa)
from tests.test_voice_4_executor_postgres import (  # noqa: F401
    _voce_accesa, comando, conferma_tutto, conta, esegui, esito, passi, q,
)

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare STIMA Voice Fase 4")

ROOT = Path(__file__).resolve().parents[1]
BASE = "80b36dbe6536b8a725a804b925a282eec53f67dd"
ROMA = "Europe/Rome"


@pytest.fixture(scope="module", autouse=True)
def _nessuna_delete(db):
    """Nel SOLO database di prova: ogni DELETE sulle tabelle del CRM finisce
    in un registro, cosi' il test puo' dimostrare che non ce ne sono."""
    q(db, "CREATE TABLE _voice4_deletes (tabella TEXT, quando TIMESTAMPTZ DEFAULT clock_timestamp())")
    q(db, "CREATE FUNCTION _voice4_log_delete() RETURNS trigger AS $$ BEGIN "
          "INSERT INTO _voice4_deletes (tabella) VALUES (TG_TABLE_NAME); RETURN OLD; END $$ LANGUAGE plpgsql")
    for t in ("contacts", "properties", "property_contacts", "buildings", "appointments", "activities", "tasks",
              "leads", "property_leads"):
        q(db, f"CREATE TRIGGER _voice4_del_{t} BEFORE DELETE ON {t} FOR EACH ROW EXECUTE FUNCTION _voice4_log_delete()")
    yield


def _eseguito(c, chi, *cmds, transcript="vocale", conferma=False):
    cid = comando(c, chi, *cmds, transcript=transcript)
    if conferma:
        conferma_tutto(c, chi, cid)
    report = esegui(c, chi, cid)
    assert report["status"] == "completed", report
    return cid, report


def _annulla(c, chi, cid, ordinal=None):
    from voice import undo
    contesto = ctx(c, chi)
    return undo.undo_step(contesto, cid, ordinal) if ordinal else undo.undo_command(contesto, cid)


def _stato(c, chi, cid):
    from voice import undo
    return {s["ordinal"]: s for s in undo.assess(ctx(c, chi), cid)}


# ---------------------------------------------------------------------------
# Un intento alla volta
# ---------------------------------------------------------------------------

def test_u1_contact_goes_to_the_trash_as_created_by_mistake(db):
    cid, r = _eseguito(db, "agent_a1", {"intent": "create_contact", "quote": "nuovo", "first_name": "Aldo",
                                        "last_name": "Neri", "phone": "333 6060601"},
                       transcript="Nuovo contatto Aldo Neri 333 6060601")
    assert _stato(db, "agent_a1", cid)[1]["status"] == "undoable"
    assert _annulla(db, "agent_a1", cid, 1)["outcome"] == "undone"
    riga = q(db, "SELECT deleted_at IS NOT NULL, deleted_reason FROM contacts WHERE id = %s",
             (esito(r, 1)["result"]["id"],))[0]
    assert tuple(riga) == (True, "created_by_mistake")
    assert _stato(db, "agent_a1", cid)[1]["status"] == "undone"
    assert _annulla(db, "agent_a1", cid, 1)["outcome"] == "already_undone"


def test_u2_note_and_task_are_marked_as_mistakes(db):
    cid, r = _eseguito(db, "agent_a1", {"intent": "add_note", "quote": "nota", "text": "Da annullare",
                                        "contact": {"phone": "333 1111111"}})
    assert _annulla(db, "agent_a1", cid, 1)["outcome"] == "undone"
    assert q(db, "SELECT metadata->>'mistake' FROM activities WHERE id = %s", (esito(r, 1)["result"]["id"],))[0][0] == "true"
    cid, r = _eseguito(db, "agent_a1", {"intent": "add_task", "quote": "ricordami", "title": "Task da annullare",
                                        "contact": {"phone": "333 1111111"}})
    assert _annulla(db, "agent_a1", cid, 1)["outcome"] == "undone"
    assert q(db, "SELECT status FROM tasks WHERE id = %s", (esito(r, 1)["result"]["id"],))[0][0] == "cancelled"


def test_u3_note_on_a_property_is_marked_as_a_mistake(db):
    cid, r = _eseguito(db, "agent_a1", {"intent": "add_note", "quote": "nota su VR10", "text": "Nota immobile",
                                        "property": {"code": "VR10"}})
    assert _annulla(db, "agent_a1", cid, 1)["outcome"] == "undone"
    assert q(db, "SELECT metadata->>'mistake' FROM activities WHERE id = %s", (esito(r, 1)["result"]["id"],))[0][0] == "true"


def test_u4_building_and_units(db):
    cid, r = _eseguito(db, "agent_a1", {"intent": "create_building", "quote": "palazzina via Undo 1 Tortoreto",
                                        "address": "Via Undo", "civic_number": "1", "city": "Tortoreto"},
                       transcript="censisci la palazzina di via Undo 1")
    assert _annulla(db, "agent_a1", cid, 1)["outcome"] == "undone"
    assert q(db, "SELECT deleted_reason FROM buildings WHERE id = %s", (esito(r, 1)["result"]["id"],))[0][0] == \
        "created_by_mistake"
    # unita' di censimento: l'«Annulla» del censimento (archivia), anche per un agente
    cid, r = _eseguito(db, "agent_a1", {"intent": "create_unit", "quote": "censisci il bilocale di via Undo 2",
                                        "record_kind": "census", "address": "Via Undo", "civic_number": "2"},
                       transcript="censisci il bilocale di via Undo 2")
    assert _annulla(db, "agent_a1", cid, 1)["outcome"] == "undone"
    assert q(db, "SELECT commercial_status FROM properties WHERE id = %s", (esito(r, 1)["result"]["id"],))[0][0] == \
        "archived"
    # unita' commerciale di un agente: assegnata a lui, va nel Cestino
    cid, r = _eseguito(db, "agent_a1", {"intent": "create_unit", "quote": "il trilocale in vendita in via Undo 3",
                                        "record_kind": "crm", "address": "Via Undo", "civic_number": "3"},
                       transcript="il trilocale in vendita in via Undo 3")
    assert _annulla(db, "agent_a1", cid, 1)["outcome"] == "undone"
    assert q(db, "SELECT deleted_reason FROM properties WHERE id = %s", (esito(r, 1)["result"]["id"],))[0][0] == \
        "created_by_mistake"


def test_u5_appointment_is_cancelled_as_mistake(db):
    cid, r = _eseguito(db, "agent_a1", {"intent": "create_appointment", "quote": "lunedi alle 12 telefonata con Mario",
                                        "when": {"date_text": "lunedì", "time_text": "alle 12"},
                                        "contact": {"phone": "333 1111111"}})
    assert _annulla(db, "agent_a1", cid, 1)["outcome"] == "undone"
    assert tuple(q(db, "SELECT status, cancelled_kind FROM appointments WHERE id = %s",
             (esito(r, 1)["result"]["id"],))[0]) == ("cancelled", "mistake")


def test_u6_owner_link_is_never_offered_and_seller_reused_is_not_undone(db):
    cid, r = _eseguito(db, "owner_a", {"intent": "link_owner", "quote": "Mario proprietario di VR10",
                                       "contact": {"phone": "333 1111111"}, "property": {"code": "VR10"}},
                       conferma=True)
    assert _stato(db, "owner_a", cid)[1]["status"] == "not_undoable"
    assert _annulla(db, "owner_a", cid, 1)["outcome"] == "not_undoable"
    assert q(db, "SELECT count(*) FROM property_contacts WHERE property_id = %s AND contact_id = %s",
             (db["ids"]["roma10"], db["ids"]["mario"]))[0][0] == 1
    # «Vende» la prima volta: creato dal comando -> annullabile
    cid2, r2 = _eseguito(db, "owner_a", {"intent": "activate_seller", "quote": "Mario vende VR10",
                                         "contact": {"phone": "333 1111111"}, "property": {"code": "VR10"}},
                         conferma=True)
    assert esito(r2, 1)["result"]["origin"] == "created"
    # di nuovo: lead gia' aperto, riutilizzato -> non lo chiudiamo noi
    cid3, r3 = _eseguito(db, "owner_a", {"intent": "activate_seller", "quote": "Mario vende VR10",
                                         "contact": {"phone": "333 1111111"}, "property": {"code": "VR10"}},
                         transcript="Mario vende VR10, di nuovo", conferma=True)
    assert esito(r3, 1)["result"]["origin"] == "reused"
    assert _annulla(db, "owner_a", cid3, 1)["outcome"] == "not_undoable"
    assert q(db, "SELECT l.status FROM leads l WHERE l.id = %s", (esito(r3, 1)["result"]["lead_id"],))[0][0] == "open"
    assert _annulla(db, "owner_a", cid2, 1)["outcome"] == "undone"
    assert q(db, "SELECT lost_reason FROM leads WHERE id = %s", (esito(r2, 1)["result"]["lead_id"],))[0][0] == \
        "created_by_mistake"


# ---------------------------------------------------------------------------
# Dipendenze, ordine inverso, parziale
# ---------------------------------------------------------------------------

def _vendita(c, chi, tel, via):
    cid, r = _eseguito(c, chi,
                       {"intent": "create_contact", "quote": "nuovo", "first_name": "Rino", "last_name": "Dati",
                        "phone": tel},
                       {"intent": "create_unit", "quote": f"trilocale di {via}", "address": via, "civic_number": "1",
                        "owner": {"step": 1}},
                       {"intent": "activate_seller", "quote": "vuole vendere", "contact": {"step": 1},
                        "property": {"step": 2}},
                       {"intent": "add_task", "quote": "ricordami", "title": f"Richiamare {via}",
                        "contact": {"step": 1}},
                       transcript=f"Rino Dati {tel} vuole vendere il trilocale di {via}", conferma=True)
    return cid, r


def test_v1_a_step_with_live_dependents_is_blocked(db):
    cid, _ = _vendita(db, "agent_a1", "333 6060602", "Via Dip")
    esito_ = _annulla(db, "agent_a1", cid, 1)
    assert esito_["outcome"] == "blocked" and esito_["code"] == "VOICE_DEPENDENTS_ACTIVE"
    assert q(db, "SELECT deleted_at FROM contacts WHERE phone_normalized = '393336060602'")[0][0] is None


def test_v2_whole_command_undo_runs_in_reverse_order(db):
    cid, r = _vendita(db, "agent_a1", "333 6060603", "Via Inversa")
    esiti = {e["ordinal"]: e for e in _annulla(db, "agent_a1", cid)}
    # 1 contatto, 2 unita', 3 collegamento (D-F), 4 Vende, 5 task
    assert [e["ordinal"] for e in _annulla(db, "agent_a1", cid)] == [5, 4, 3, 2, 1]
    assert esiti[5]["outcome"] == "undone" and esiti[4]["outcome"] == "undone"
    assert esiti[3]["outcome"] == "not_undoable"                      # D-A: il collegamento resta
    assert esiti[2]["outcome"] == "undone" and esiti[1]["outcome"] == "undone"
    assert q(db, "SELECT deleted_reason FROM contacts WHERE phone_normalized = '393336060603'")[0][0] == \
        "created_by_mistake"
    unita = esito(r, 2)["result"]["id"]
    assert q(db, "SELECT deleted_reason FROM properties WHERE id = %s", (unita,))[0][0] == "created_by_mistake"
    assert q(db, "SELECT count(*) FROM property_contacts WHERE property_id = %s", (unita,))[0][0] == 1


def test_v3_partial_undo_is_recorded_and_explained(db):
    """Il task e' stato completato da qualcuno nel frattempo: non si annulla
    piu'; il resto si ferma davanti a quel blocco e lo dice."""
    cid, r = _vendita(db, "agent_a1", "333 6060604", "Via Parziale")
    q(db, "UPDATE tasks SET status = 'completed', completed_at = NOW() WHERE id = %s", (esito(r, 5)["result"]["id"],))
    esiti = {e["ordinal"]: e for e in _annulla(db, "agent_a1", cid)}
    assert esiti[5]["outcome"] == "blocked"
    assert esiti[1]["outcome"] == "blocked"                            # il contatto ha un task vivo
    registrati = q(db, "SELECT ordinal, outcome FROM voice_step_undos WHERE command_id = %s ORDER BY id", (cid,))
    assert (5, "blocked") in [tuple(x) for x in registrati]


def test_v4_concurrent_undo_happens_once(db):
    cid, r = _eseguito(db, "agent_a1", {"intent": "add_note", "quote": "nota", "text": "Doppio annulla",
                                        "contact": {"phone": "333 1111111"}})
    barriera, esiti = threading.Barrier(2), []

    def lavora():
        barriera.wait()
        try:
            esiti.append(_annulla(db, "agent_a1", cid, 1)["outcome"])
        except Exception as exc:  # noqa: BLE001
            esiti.append(type(exc).__name__)
    fili = [threading.Thread(target=lavora) for _ in range(2)]
    for f in fili:
        f.start()
    for f in fili:
        f.join(60)
    assert q(db, "SELECT count(*) FROM voice_step_undos WHERE command_id = %s AND outcome = 'undone'", (cid,))[0][0] == 1
    assert "undone" in esiti


# ---------------------------------------------------------------------------
# Finestra di 15 minuti (server), mezzanotte, ora legale
# ---------------------------------------------------------------------------

def _con_orologio(monkeypatch, creato_alle, adesso):
    from voice import undo
    monkeypatch.setattr(undo, "_server_now", lambda cur: adesso)
    return creato_alle


def _contatto_con_creazione(c, chi, tel, creato_alle):
    cid, r = _eseguito(c, chi, {"intent": "create_contact", "quote": "nuovo", "first_name": "Orazio",
                                "last_name": "Tempo", "phone": tel}, transcript=f"Orazio Tempo {tel}")
    q(c, "UPDATE contacts SET created_at = %s WHERE id = %s", (creato_alle, esito(r, 1)["result"]["id"]))
    return cid


def test_w1_after_fifteen_minutes_it_expires(db, monkeypatch):
    from zoneinfo import ZoneInfo
    creato = datetime(2026, 10, 10, 10, 0, tzinfo=ZoneInfo(ROMA))
    cid = _contatto_con_creazione(db, "agent_a1", "333 7070701", creato)
    _con_orologio(monkeypatch, creato, creato + timedelta(minutes=15, seconds=1))
    assert _stato(db, "agent_a1", cid)[1]["status"] == "expired"
    assert _annulla(db, "agent_a1", cid, 1)["outcome"] == "expired"
    assert q(db, "SELECT deleted_at FROM contacts WHERE phone_normalized = '393337070701'")[0][0] is None


def test_w2_crossing_midnight_counts_real_minutes(db, monkeypatch):
    from zoneinfo import ZoneInfo
    creato = datetime(2026, 10, 10, 23, 55, tzinfo=ZoneInfo(ROMA))
    cid = _contatto_con_creazione(db, "agent_a1", "333 7070702", creato)
    _con_orologio(monkeypatch, creato, datetime(2026, 10, 11, 0, 5, tzinfo=ZoneInfo(ROMA)))
    assert _annulla(db, "agent_a1", cid, 1)["outcome"] == "undone"


def test_w3_dst_fall_back_uses_absolute_time(db, monkeypatch):
    """25 ottobre 2026: alle 03:00 CEST si torna alle 02:00 CET. Creato alle
    02:55 CEST; alle 02:05 CET (orologio piu' indietro!) sono passati 10
    minuti: annullabile. Alle 02:15 CET ne sono passati 20: scaduto."""
    creato = datetime(2026, 10, 25, 0, 55, tzinfo=timezone.utc)           # 02:55 CEST
    cid = _contatto_con_creazione(db, "agent_a1", "333 7070703", creato)
    _con_orologio(monkeypatch, creato, datetime(2026, 10, 25, 1, 15, tzinfo=timezone.utc))   # 02:15 CET
    assert _annulla(db, "agent_a1", cid, 1)["outcome"] == "expired"
    cid2 = _contatto_con_creazione(db, "agent_a1", "333 7070704", creato)
    _con_orologio(monkeypatch, creato, datetime(2026, 10, 25, 1, 5, tzinfo=timezone.utc))    # 02:05 CET
    assert _annulla(db, "agent_a1", cid2, 1)["outcome"] == "undone"


def test_w4_the_window_never_overrides_crm_blocks(db):
    """Dentro la finestra, ma il contatto ha nel frattempo un task aperto
    scritto da altri: il Cestino lo rifiuta e l'esito e' «bloccato»."""
    cid, r = _eseguito(db, "agent_a1", {"intent": "create_contact", "quote": "nuovo", "first_name": "Bea",
                                        "last_name": "Blocco", "phone": "333 7070705"},
                       transcript="Bea Blocco 333 7070705")
    q(db, "INSERT INTO tasks (contact_id, title, agency_id) VALUES (%s, 'aperto da altri', 1)",
      (esito(r, 1)["result"]["id"],))
    esito_ = _annulla(db, "agent_a1", cid, 1)
    assert esito_["outcome"] == "blocked" and esito_["code"] == "TRASH_BLOCKED"


# ---------------------------------------------------------------------------
# Permessi al momento dell'annullamento
# ---------------------------------------------------------------------------

def test_x1_only_the_actor_and_with_current_permissions(db):
    from voice import ledger
    cid, r = _eseguito(db, "agent_a1", {"intent": "create_contact", "quote": "nuovo", "first_name": "Ada",
                                        "last_name": "Perm", "phone": "333 7070706"},
                       transcript="Ada Perm 333 7070706")
    for altro in ("agent_a2", "owner_a", "agent_b"):
        with pytest.raises(ledger.CommandNotFound):
            _annulla(db, altro, cid, 1)
    q(db, "UPDATE contacts SET assigned_agent_id = %s WHERE id = %s",
      (db["ids"]["agent_a2"], esito(r, 1)["result"]["id"]))
    esito_ = _annulla(db, "agent_a1", cid, 1)
    assert esito_["outcome"] == "blocked"
    assert q(db, "SELECT deleted_at FROM contacts WHERE id = %s", (esito(r, 1)["result"]["id"],))[0][0] is None


def test_x2_platform_admin_cannot_undo(db):
    from voice import executor, undo
    with pytest.raises(executor.VoiceRefused):
        undo.undo_command(ctx(db, "owner_a", ruolo=None, platform=True), 1)


# ---------------------------------------------------------------------------
# Nessuna DELETE fisica (verificata alla fine del modulo)
# ---------------------------------------------------------------------------

def test_zz_no_physical_delete_in_the_whole_module(db):
    assert q(db, "SELECT tabella, count(*) FROM _voice4_deletes GROUP BY 1") == []


# ---------------------------------------------------------------------------
# Migration 099
# ---------------------------------------------------------------------------

def test_m1_migration_099_is_valid_contiguous_and_has_a_down():
    sys.path.insert(0, str(ROOT / "scripts"))
    import p26_migrate as runner
    tutte = runner.discover_migrations(runner.MIGRATIONS_DIR)
    runner.verify_contiguous(tutte)
    m = [x for x in tutte if x.number == 99]
    assert [x.version for x in m] == ["099_voice_undo"] and m[0].down_available
    assert runner.validate_migration(m[0]) == []
    # SENTINELLA AGGIORNATA DA CENSIMENTO-2: dopo la 099 viene solo la 100 (composizione degli edifici)
    assert [x.number for x in tutte if x.number >= 99] == [99, 100]
    testo = (ROOT / "migrations" / "099_voice_undo.sql").read_text(encoding="utf-8")
    assert "ALTER TABLE" not in testo.upper() and "voice_command_events" not in testo


def test_m2_undo_rows_are_append_only_and_guarded(db):
    from tests.test_voice_3_ledger_postgres import _errore
    cid, _ = _eseguito(db, "agent_a1", {"intent": "add_note", "quote": "nota", "text": "Per la guardia",
                                        "contact": {"phone": "333 1111111"}})
    _annulla(db, "agent_a1", cid, 1)
    riga = q(db, "SELECT id FROM voice_step_undos WHERE command_id = %s", (cid,))[0][0]
    assert "append-only" in _errore(db, "UPDATE voice_step_undos SET outcome = 'failed' WHERE id = %s", (riga,))
    assert "append-only" in _errore(db, "DELETE FROM voice_step_undos WHERE id = %s", (riga,))
    assert "agency or actor" in _errore(
        db, "INSERT INTO voice_step_undos (agency_id, command_id, ordinal, actor_user_id, entity_type, entity_id, "
            "outcome) VALUES (1, %s, 1, %s, 'activity', 1, 'blocked')", (cid, db["ids"]["agent_a2"]))
    assert "uq_voice_step_undos_done" in _errore(
        db, "INSERT INTO voice_step_undos (agency_id, command_id, ordinal, actor_user_id, entity_type, entity_id, "
            "outcome) VALUES (1, %s, 1, %s, 'activity', 1, 'undone')", (cid, db["ids"]["agent_a1"]))
    assert "voice_undos_detail_chk" in (_errore(
        db, "INSERT INTO voice_step_undos (agency_id, command_id, ordinal, actor_user_id, entity_type, entity_id, "
            "outcome, detail) VALUES (1, %s, 1, %s, 'activity', 1, 'blocked', '{\"transcript\": \"x\"}')",
        (cid, db["ids"]["agent_a1"])) or "")


def test_m3_down_refuses_when_populated_and_runs_when_empty(db):
    import psycopg2
    giu = (ROOT / "migrations" / "099_voice_undo_down.sql").read_text(encoding="utf-8")
    su = (ROOT / "migrations" / "099_voice_undo.sql").read_text(encoding="utf-8")
    conn = psycopg2.connect(db["dsn"])
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM voice_step_undos")
            assert cur.fetchone()[0] > 0
            with pytest.raises(psycopg2.Error, match="populated voice undo ledger"):
                cur.execute(giu)
        conn.rollback()
        with conn.cursor() as cur:                       # a vuoto, in una transazione annullata
            cur.execute("ALTER TABLE voice_step_undos DISABLE TRIGGER trg_voice_step_undos_guard")
            cur.execute("DELETE FROM voice_step_undos")
            cur.execute(giu)
            cur.execute("SELECT to_regclass('voice_step_undos')")
            assert cur.fetchone()[0] is None
            cur.execute(su)
            cur.execute("SELECT to_regclass('voice_step_undos')")
            assert cur.fetchone()[0] is not None
        conn.rollback()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Protezione dei moduli esistenti
# ---------------------------------------------------------------------------

PROTETTI = ("main.py", "property/census.py", "property/service.py", "property/interactions.py",
            "property/lifecycle.py", "property/building_lifecycle.py", "property/repository.py",
            "core/service.py", "core/repository.py", "core/contact_lifecycle.py", "crm/sellers.py",
            "appointments/service.py", "appointments/schemas.py", "voice/planner.py", "voice/policy.py",
            "voice/resolver.py", "voice/repository.py", "voice/schemas.py", "voice/settings.py")


from tests.test_voice_1_perimeter import FASE6_UI  # noqa: E402

CENSIMENTO2_PROPERTY = ("property/census_classes.py", "property/composition.py", "property/router.py",
                        "property/schemas.py",
                        # la UI di CENSIMENTO-2 nel censimento esistente della Shell
                        "static/os_shell/assets/app.css", "static/os_shell/assets/census/census-api.js",
                        "static/os_shell/assets/census/census-model.js", "static/os_shell/assets/census/census-wizard.js",
                        "static/os_shell/assets/census/composition-model.js",
                        "static/os_shell/assets/census/composition-panel.js",
                        "static/os_shell/assets/census/property-census-tab.js",
                        "static/os_shell/assets/views/edificio-dettaglio.js")


def test_n1_protected_files_and_migrations_are_identical_to_the_base():
    if subprocess.run(["git", "cat-file", "-e", BASE], cwd=ROOT, capture_output=True).returncode != 0:
        pytest.skip("commit di partenza non disponibile in questo clone")
    migrazioni = [str(p.relative_to(ROOT)) for p in sorted((ROOT / "migrations").glob("0*.sql"))
                  if not p.name.startswith("099_")]
    # SENTINELLA AGGIORNATA DA STIMA VOICE FASE 7: `main.py` cambia SOLO per il montaggio del router
    # (righe aggiunte, nessuna tolta), verificato riga per riga; il resto dei protetti resta identico.
    # SENTINELLA AGGIORNATA DA STIMA VOICE FASE 7 (conversazione): `voice/planner.py` cambia SOLO per la
    # parola «acquisizion» -> seller_meeting (il tipo che usa il modulo Acquisizioni), verificata riga per riga.
    esito_ = subprocess.run(["git", "diff", "--name-only", BASE, "--",
                             *[p for p in PROTETTI if p not in ("main.py", "voice/planner.py")],
                             *migrazioni], cwd=ROOT, capture_output=True, text=True)
    assert esito_.returncode == 0 and esito_.stdout.strip() == "", esito_.stdout
    diff_main = subprocess.run(["git", "diff", "-U0", BASE, "--", "main.py"], cwd=ROOT,
                               capture_output=True, text=True).stdout.splitlines()
    diff_planner = subprocess.run(["git", "diff", "-U0", BASE, "--", "voice/planner.py"], cwd=ROOT,
                                  capture_output=True, text=True).stdout.splitlines()
    assert [r for r in diff_planner if r.startswith("-") and not r.startswith("---")] == []
    assert [r[1:].strip() for r in diff_planner if r.startswith("+") and not r.startswith("+++")
            and not r[1:].strip().startswith("#")] in ([], [
        '("acquisizion", "seller_meeting"), ("proprietari", "seller_meeting"),',
        'SELLER_MEETING_EVIDENCE = ("acquisizion", "proprietari")',
        'if tipo == "seller_meeting" and not any(p in dates.fold(c.quote) or p in folded',
        'for p in SELLER_MEETING_EVIDENCE):',
        'tipo = appointment_type_from(dates.fold(c.quote))'])
    tolte = [r for r in diff_main if r.startswith("-") and not r.startswith("---")]
    aggiunte = [r[1:].strip() for r in diff_main if r.startswith("+") and not r.startswith("+++")]
    assert tolte == [], tolte
    assert [r for r in aggiunte if r and not r.startswith("#")] == [
        "from voice.router import router as voice_router",
        "app.include_router(voice_router, dependencies=[Depends(require_authenticated_operator)])"], aggiunte
    for cartella in ("core", "property", "appointments", "crm", "static"):
        diff = subprocess.run(["git", "diff", "--name-only", BASE, "--", cartella], cwd=ROOT,
                              capture_output=True, text=True).stdout.split()
        # Fase 6: solo i file dichiarati dell'interfaccia Voice nella shell
        # SENTINELLA AGGIORNATA DA CENSIMENTO-2: i soli file dichiarati della composizione
        # degli edifici (modulo nuovo, classi, rotte e schemi); i PROTETTI qui sopra restano identici.
        diff = [f for f in diff if f not in FASE6_UI and f not in CENSIMENTO2_PROPERTY]
        assert diff == [], diff
