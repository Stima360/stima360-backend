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
                                        "contact": {"phone": "333 1111111"}},
                       conferma=True)   # SENTINELLA AGGIORNATA DA STIMA VOICE V8-0: l'appuntamento si conferma sempre
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
    # SENTINELLA AGGIORNATA DA ELIMINAZIONE-EDIFICIO-1: poi la 101 (richieste di eliminazione degli edifici)
    assert [x.number for x in tutte if x.number >= 99] == [99, 100, 101]
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

# ELIMINAZIONE-EDIFICIO-1 (autorizzazione specifica del 2026-10-12): i SOLI file nuovi o toccati
# dall'eliminazione controllata degli edifici fuori dagli elenchi qui sopra. Nessun file PROTETTO.
ELIMINAZIONE_EDIFICIO_1 = ("property/building_deletion.py",
                           "static/os_shell/assets/building-deletion/building-deletion.js",
                           "static/os_shell/assets/views/rete-eliminazioni.js",
                           "static/os_shell/assets/views/rete.js",
                           "static/os_shell/assets/views/cestino.js")

# CESTINO-GENERALE-1 (autorizzazione specifica del 2026-10-13): i SOLI file nuovi o toccati dal
# Cestino unico in sola lettura fuori dagli elenchi qui sopra (main.js, app.css, rete.js e cestino.js
# vi sono gia'). Nessun file PROTETTO; `trash/` e `platform_admin/` sono fuori dalle cartelle
# sorvegliate. In `main.py` le SOLE due righe di montaggio qui sotto, verificate riga per riga.
CESTINO_GENERALE_1 = ("static/os_shell/assets/trash/trash-api.js",
                      "static/os_shell/assets/trash/trash-unified.js")
CESTINO_GENERALE_1_MAIN = ["from trash.router import router as trash_router",
                           "app.include_router(trash_router, dependencies=[Depends(require_authenticated_operator)])"]

# VENDITORI-SOSPESI-1 (autorizzazione specifica del 2026-10-13): le opportunita' Venditore sospese
# restano raggiungibili e chiudibili («Smetti…» anche da sospesa, `#/venditori/sospesi`, avviso
# nella lista Attivi vuota, aiuto nel blocco del Cestino). SOLO questi file d'interfaccia; nessun
# backend, nessun file PROTETTO (la regola open/paused di property/lifecycle.py resta identica).
VENDITORI_SOSPESI_1 = ("static/os_shell/assets/sellers/seller-model.js",
                       "static/os_shell/assets/sellers/seller-toggle.js",
                       "static/os_shell/assets/views/venditori.js",
                       "static/os_shell/assets/trash/trash-model.js",
                       "static/os_shell/assets/trash/trash-dialog.js")

# CESTINO-GENERALE-2 (autorizzazione specifica del 2026-10-13): «Elimina…» dagli elenchi di Immobili,
# Contatti, Richieste acquirente ed Edifici con i fogli e il pannello gia' esistenti. SOLO questi file
# d'interfaccia fuori dagli elenchi qui sopra (app.css e building-deletion.js vi sono gia'); nessun
# backend, nessun file PROTETTO.
CESTINO_GENERALE_2 = ("static/os_shell/assets/trash/trash-row-actions.js",
                      "static/os_shell/assets/views/immobili.js",
                      "static/os_shell/assets/views/contatti.js",
                      "static/os_shell/assets/views/acquirenti.js",
                      "static/os_shell/assets/views/edifici.js")

# STIMA360 TERRITORIO T2 (autorizzazione specifica del 2026-10-13): la mappa stradale `#/edifici/mappa`
# (MapLibre GL JS + OpenFreeMap). SOLO questi file d'interfaccia nuovi fuori dagli elenchi qui sopra
# (main.js e app.css vi sono gia'); nessun backend, nessun file PROTETTO, nessuna migration.
TERRITORIO_2 = ("static/os_shell/assets/territorio/maplibre-loader.js",
                "static/os_shell/assets/views/territorio.js")
# STIMA360 TERRITORIO T3 (autorizzazione specifica del 2026-10-13): pulsante «Mappa» ed edifici sulla mappa.
# SOLO il modulo puro nuovo fuori dagli elenchi qui sopra (edifici.js e' in CESTINO_GENERALE_2,
# territorio.js in TERRITORIO_2, app.css in FASE6_UI); nessun backend, nessun file PROTETTO.
TERRITORIO_3 = ("static/os_shell/assets/territorio/territorio-model.js",)
# STIMA360 TERRITORIO T4 (autorizzazione specifica del 2026-10-13): posizionamento manuale e verifica
# della posizione degli edifici. SOLO questi file nuovi fuori dagli elenchi qui sopra (router.py, schemas.py,
# census-api.js, census-model.js ed edificio-dettaglio.js sono in CENSIMENTO2_PROPERTY, territorio.js in
# TERRITORIO_2, territorio-model.js in TERRITORIO_3, app.css in FASE6_UI). Nessun file PROTETTO:
# property/census.py resta identico (building_position.py ne usa le letture, non lo modifica).
TERRITORIO_4 = ("property/building_position.py",
                "static/os_shell/assets/views/territorio-posiziona.js")
# STIMA360 TERRITORIO T5-bis (autorizzazione specifica del 2026-10-13): ricerca indirizzo per la mappa
# (Nominatim tramite il backend). SOLO il modulo nuovo fuori dagli elenchi qui sopra (router.py in
# CENSIMENTO2_PROPERTY, territorio-posiziona.js in TERRITORIO_4, census-api.js e census-model.js in
# CENSIMENTO2_PROPERTY, app.css in FASE6_UI). Nessun file PROTETTO, nessuna migration.
TERRITORIO_5BIS = ("property/geocode.py",)
# STIMA360 TERRITORIO ZONA-2 (autorizzazione specifica del 2026-10-13): inserimento geografico
# integrato da Zona (punto scelto -> procedura guidata esistente -> conferma T4). SOLO il modulo
# nuovo fuori dagli elenchi qui sopra (territorio.js in TERRITORIO_2, territorio-model.js in
# TERRITORIO_3, territorio-posiziona.js in TERRITORIO_4, census-wizard.js e app.css in
# CENSIMENTO2_PROPERTY / FASE6_UI). Nessun backend, nessun file PROTETTO, nessuna migration.
TERRITORIO_ZONA2 = ("static/os_shell/assets/views/territorio-inserisci.js",)
# STIMA360 ZONA-3 (autorizzazione specifica del 2026-10-14): posizione verificata degli immobili
# indipendenti (migration 103). SOLO il modulo backend nuovo fuori dagli elenchi qui sopra (router.py,
# schemas.py, census-api.js, census-model.js e census-wizard.js in CENSIMENTO2_PROPERTY; territorio.js in
# TERRITORIO_2, territorio-model.js in TERRITORIO_3, territorio-inserisci.js in TERRITORIO_ZONA2, app.css in
# FASE6_UI). Nessun file PROTETTO: property/repository.py e service.py restano identici.
TERRITORIO_ZONA3 = ("property/property_position.py",)
# STIMA360 ZONA-4 (autorizzazione specifica del 2026-10-14): «La mia posizione» (GPS del browser) su
# Zona. SOLO il modulo nuovo fuori dagli elenchi qui sopra (territorio.js in TERRITORIO_2,
# territorio-model.js in TERRITORIO_3, territorio-inserisci.js in TERRITORIO_ZONA2, app.css in FASE6_UI).
# Nessun backend, nessun file PROTETTO, nessuna migration.
TERRITORIO_ZONA4 = ("static/os_shell/assets/territorio/gps.js",)
# AGENZIE-TERRITORI C0-quater (autorizzazione specifica del 2026-10-14): Giulianova nel catalogo dei
# Comuni del CRM. SOLO il catalogo esistente; il suo contenuto e' vincolato da
# tests/test_crm_ops_2_property_form.py::test_a01/test_a02 (portale invariato voce per voce, un solo
# comune aggiunto e dichiarato in CRM_ADDED_MUNICIPALITIES). Nessun file PROTETTO, nessuna migration.
AGENZIE_C0QUATER = ("property/catalog.py",)
# AGENZIE-TERRITORI C1 (autorizzazione specifica del 2026-10-15): lettura territoriale condivisa degli
# immobili. SOLO il modulo nuovo (property/router.py e' gia' in CENSIMENTO2_PROPERTY). Nessun file
# PROTETTO: property/repository.py e property/service.py restano identici; nessuna migration.
AGENZIE_C1 = ("property/territorial_access.py",)
# AGENZIE-TERRITORI C2 (autorizzazione specifica del 2026-10-15): proposte di modifica territoriali
# approvate dal Super Admin. I moduli nuovi (backend e Shell) e la scheda immobile, che apre la vista
# condivisa in sola lettura PRIMA delle sezioni operative (property/router.py, rete.js e app.css sono
# gia' dichiarati). Nessun file PROTETTO. La migration 104 e' additiva e dichiarata a parte.
AGENZIE_C2 = ("property/territorial_changes.py", "static/os_shell/assets/views/immobile-dettaglio.js",
              "static/os_shell/assets/views/immobile-condiviso.js", "static/os_shell/assets/views/rete-modifiche.js",
              "static/os_shell/assets/territorial-changes/territorial-changes.js")
# AGENZIE-TERRITORI C3 (autorizzazione specifica del 2026-10-15): l'elenco «Immobili» unificato
# (propri + condivisi C1). SOLO il modulo nuovo, in sola lettura (property/router.py, immobili.js e
# app.css sono gia' dichiarati). Nessun file PROTETTO, nessuna migration.
AGENZIE_C3 = ("property/accessible_list.py",)
# AGENZIE-AMMINISTRAZIONE ADM-1 (autorizzazione specifica del proprietario): la Rete e le proposte
# C2 riservate al Super Admin generale designato. SOLO il controllo d'identita' nelle decisioni C2 e il
# cancello della UI Rete; main.py, i PROTETTI e le migrazioni restano identici.
AGENZIE_ADM1 = ("property/territorial_changes.py", "static/os_shell/assets/components/network.js",
                "static/os_shell/assets/views/rete.js", "static/os_shell/assets/views/rete-agenzia.js",
                "static/os_shell/assets/views/rete-territorio.js")
# AGENZIE-AMMINISTRAZIONE ADM-2 (autorizzazione specifica del proprietario): «Verifica conseguenze»,
# sole regole CSS additive del dialogo nella sezione Rete.
AGENZIE_ADM2 = ("static/os_shell/assets/app.css",)


# CENSIMENTO-2 FASE A (autorizzazione specifica del 2026-10-12): in property/site_sync.py la SOLA
# regola di `_applica` che non aggiorna da sola una superficie verificata. L'eccezione e' vincolata
# al CONTENUTO: il file deve essere identico alla base oppure identico alla base con ESATTAMENTE
# questa modifica, nel punto previsto di `_applica`. Nessuna modifica parziale, nessun'altra riga.
SITE_SYNC = "property/site_sync.py"
SITE_SYNC_ANCORA = "        prima = precedente.get(campo, _MANCA)\n"
SITE_SYNC_SEGUENTE = "        if _uguale(attuale, nuovo):\n"
SITE_SYNC_AGGIUNTE = (
    "        # CENSIMENTO-2 Fase A: una superficie verificata con una fonte documentata non si\n"
    "        # aggiorna da sola: la differenza diventa un conflitto («Applica» / «Ignora»)\n"
    "        protetto = campo == \"surface_sqm\" and prop.get(\"surface_source\") == \"verified\"\n")
SITE_SYNC_VECCHIA = "        elif prima is not _MANCA and attuale is not None and _uguale(attuale, prima):\n"
SITE_SYNC_NUOVA = "        elif not protetto and prima is not _MANCA and attuale is not None and _uguale(attuale, prima):\n"


def _site_sync_approvato(base: str) -> str:
    """La base con la sola modifica approvata, applicata DENTRO `_applica`:
    tre righe subito dopo l'ancora (che nella base precede il primo confronto
    dei valori) e la sostituzione della condizione «si aggiorna»."""
    inizio = base.index("\ndef _applica(")
    fine = base.index("\ndef ", inizio + 1)
    corpo = base[inizio:fine]
    assert corpo.count(SITE_SYNC_ANCORA + SITE_SYNC_SEGUENTE) == 1 and base.count(SITE_SYNC_ANCORA) == 1
    assert corpo.count(SITE_SYNC_VECCHIA) == 1 and base.count(SITE_SYNC_VECCHIA) == 1
    corpo = corpo.replace(SITE_SYNC_ANCORA, SITE_SYNC_ANCORA + SITE_SYNC_AGGIUNTE, 1)
    corpo = corpo.replace(SITE_SYNC_VECCHIA, SITE_SYNC_NUOVA, 1)
    return base[:inizio] + corpo + base[fine:]


def _site_sync_ammesso(root=ROOT, base_ref=BASE) -> bool:
    base = subprocess.run(["git", "show", f"{base_ref}:{SITE_SYNC}"], cwd=root, capture_output=True,
                          text=True, check=True).stdout
    attuale = (Path(root) / SITE_SYNC).read_text(encoding="utf-8")
    return attuale in (base, _site_sync_approvato(base))


def test_n1_protected_files_and_migrations_are_identical_to_the_base():
    if subprocess.run(["git", "cat-file", "-e", BASE], cwd=ROOT, capture_output=True).returncode != 0:
        pytest.skip("commit di partenza non disponibile in questo clone")
    migrazioni = [str(p.relative_to(ROOT)) for p in sorted((ROOT / "migrations").glob("0*.sql"))
                  if not p.name.startswith("099_")]
    # SENTINELLA AGGIORNATA DA STIMA VOICE FASE 7: `main.py` cambia SOLO per il montaggio del router
    # (righe aggiunte, nessuna tolta), verificato riga per riga; il resto dei protetti resta identico.
    # SENTINELLA AGGIORNATA DA STIMA VOICE FASE 7 (conversazione): `voice/planner.py` cambia SOLO per la
    # parola «acquisizion» -> seller_meeting (il tipo che usa il modulo Acquisizioni), verificata riga per riga.
    # SENTINELLA AGGIORNATA DA SEMPLIFICAZIONE-CRM-1 (dichiarata in tests/semplificazione_crm_1_diff.py):
    # `appointments/service.py` porta la regola «visita immobile solo su un incarico» (service + 107).
    # SENTINELLA AGGIORNATA DA STIMA VOICE V8 (autorizzazione V8-1..V8-5): i moduli del planner,
    # della politica, del risolutore, del repository e dello schema evolvono per ruoli, sinonimi,
    # incarico obbligatorio e ricerca tollerante; le prove sono in tests/test_voice_8_*. Gli altri
    # protetti (property, core, crm, settings) restano identici alla base.
    VOICE_V8 = ("voice/planner.py", "voice/policy.py", "voice/resolver.py", "voice/repository.py",
                "voice/schemas.py")
    APPUNTAMENTI_VENDITA_106 = ("appointments/enums.py", "appointments/errors.py", "appointments/repository.py",
                                "appointments/router.py", "appointments/service.py")
    from tests.semplificazione_crm_1_diff import FILE_MODIFICATI as SCRM1, FILE_NUOVI as SCRM1_NUOVI
    PRECEDENTI_DICHIARATI_TARDI = ("static/os_shell/assets/components/agenda/agenda-drawer.js",
                                   "static/os_shell/assets/views/rete-registro.js")
    esito_ = subprocess.run(["git", "diff", "--name-only", BASE, "--",
                             *[p for p in PROTETTI if p not in ("main.py", *VOICE_V8) and p not in SCRM1],
                             *migrazioni], cwd=ROOT, capture_output=True, text=True)
    assert esito_.returncode == 0 and esito_.stdout.strip() == "", esito_.stdout
    diff_main = subprocess.run(["git", "diff", "-U0", BASE, "--", "main.py"], cwd=ROOT,
                               capture_output=True, text=True).stdout.splitlines()
    # (la verifica riga per riga di `voice/planner.py` della Fase 7 e' superata dalla V8: il planner
    # e' fra i moduli dichiarati sopra e le sue regole sono fissate da tests/test_voice_8_1_language.py)
    tolte = [r for r in diff_main if r.startswith("-") and not r.startswith("---")]
    aggiunte = [r[1:].strip() for r in diff_main if r.startswith("+") and not r.startswith("+++")]
    assert tolte == [], tolte
    # SENTINELLA AGGIORNATA DA CESTINO-GENERALE-1: oltre alle due righe Voice, ESATTAMENTE le due
    # righe autorizzate del Cestino unico, nello stesso ordine del file (import, poi montaggio).
    assert [r for r in aggiunte if r and not r.startswith("#")] == [
        "from voice.router import router as voice_router",
        CESTINO_GENERALE_1_MAIN[0],
        "app.include_router(voice_router, dependencies=[Depends(require_authenticated_operator)])",
        CESTINO_GENERALE_1_MAIN[1]], aggiunte
    for cartella in ("core", "property", "appointments", "crm", "static"):
        diff = subprocess.run(["git", "diff", "--name-only", BASE, "--", cartella], cwd=ROOT,
                              capture_output=True, text=True).stdout.split()
        # Fase 6: solo i file dichiarati dell'interfaccia Voice nella shell
        # SENTINELLA AGGIORNATA DA CENSIMENTO-2: i soli file dichiarati della composizione
        # degli edifici (modulo nuovo, classi, rotte e schemi); i PROTETTI qui sopra restano identici.
        diff = [f for f in diff if f not in FASE6_UI and f not in CENSIMENTO2_PROPERTY]
        # SENTINELLA AGGIORNATA DA ELIMINAZIONE-EDIFICIO-1: i soli file dichiarati qui sopra
        diff = [f for f in diff if f not in ELIMINAZIONE_EDIFICIO_1]
        # SENTINELLA AGGIORNATA DA CESTINO-GENERALE-1: i soli file dichiarati qui sopra
        diff = [f for f in diff if f not in CESTINO_GENERALE_1]
        # SENTINELLA AGGIORNATA DA VENDITORI-SOSPESI-1: i soli file dichiarati qui sopra
        diff = [f for f in diff if f not in VENDITORI_SOSPESI_1]
        # SENTINELLA AGGIORNATA DA CESTINO-GENERALE-2: i soli file dichiarati qui sopra
        diff = [f for f in diff if f not in CESTINO_GENERALE_2]
        # SENTINELLA AGGIORNATA DA STIMA360 TERRITORIO T2: i soli file dichiarati qui sopra
        diff = [f for f in diff if f not in TERRITORIO_2]
        # SENTINELLA AGGIORNATA DA STIMA360 TERRITORIO T3: il solo file dichiarato qui sopra
        diff = [f for f in diff if f not in TERRITORIO_3]
        # SENTINELLA AGGIORNATA DA STIMA360 TERRITORIO T4: i soli file dichiarati qui sopra
        diff = [f for f in diff if f not in TERRITORIO_4]
        # SENTINELLA AGGIORNATA DA STIMA360 TERRITORIO T5-bis: il solo file dichiarato qui sopra
        diff = [f for f in diff if f not in TERRITORIO_5BIS]
        # SENTINELLA AGGIORNATA DA STIMA360 TERRITORIO ZONA-2: il solo file dichiarato qui sopra
        diff = [f for f in diff if f not in TERRITORIO_ZONA2]
        # SENTINELLA AGGIORNATA DA STIMA360 ZONA-3: il solo file dichiarato qui sopra
        diff = [f for f in diff if f not in TERRITORIO_ZONA3]
        # SENTINELLA AGGIORNATA DA STIMA360 ZONA-4: il solo file dichiarato qui sopra
        diff = [f for f in diff if f not in TERRITORIO_ZONA4]
        # SENTINELLA AGGIORNATA DA AGENZIE-TERRITORI C0-quater: il solo file dichiarato qui sopra
        diff = [f for f in diff if f not in AGENZIE_C0QUATER]
        # SENTINELLA AGGIORNATA DA AGENZIE-TERRITORI C1: il solo file dichiarato qui sopra
        diff = [f for f in diff if f not in AGENZIE_C1]
        # SENTINELLA AGGIORNATA DA AGENZIE-TERRITORI C2: i soli file dichiarati qui sopra
        diff = [f for f in diff if f not in AGENZIE_C2]
        # SENTINELLA AGGIORNATA DA AGENZIE-TERRITORI C3: il solo file dichiarato qui sopra
        diff = [f for f in diff if f not in AGENZIE_C3]
        # SENTINELLA AGGIORNATA DA AGENZIE-AMMINISTRAZIONE ADM-1: i soli file dichiarati qui sopra
        diff = [f for f in diff if f not in AGENZIE_ADM1]
        # SENTINELLA AGGIORNATA DA AGENZIE-AMMINISTRAZIONE ADM-2: il solo file dichiarato qui sopra
        diff = [f for f in diff if f not in AGENZIE_ADM2]
        # SENTINELLA AGGIORNATA DA CENSIMENTO-2 FASE A: site_sync.py solo se identico alla base
        # o alla base con esattamente la modifica approvata (vincolo sul contenuto, non sul nome)
        if SITE_SYNC in diff:
            assert _site_sync_ammesso(), "property/site_sync.py diverso dalla base e dalla modifica approvata"
            diff = [f for f in diff if f != SITE_SYNC]
        # SENTINELLA AGGIORNATA DA APPUNTAMENTI DI VENDITA (106) e SEMPLIFICAZIONE-CRM-1: i soli file
        # dell'Agenda dichiarati (la 106 in tests/test_appuntamenti_vendita_static.py, la CRM-1 nel suo diff)
        diff = [f for f in diff if f not in APPUNTAMENTI_VENDITA_106 and f not in SCRM1 and f not in SCRM1_NUOVI]
        # SENTINELLA AGGIORNATA (dichiarazione tardiva, verificata con `git log`): il drawer dell'Agenda
        # («Apri incarico», appuntamenti di vendita 106) e il registro della rete (ADM-7) erano gia'
        # diversi dalla base prima della V8; nessun altro file.
        diff = [f for f in diff if f not in PRECEDENTI_DICHIARATI_TARDI]
        assert diff == [], diff
