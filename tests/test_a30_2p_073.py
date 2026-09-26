"""A30-2P - migration 073 (facade LMC-15) senza database: il testo.

La 073 e' valida per il runner P26, ridefinisce SOLO i due CHECK previsti e ne
aggiunge uno; la down ripristina ESATTAMENTE le definizioni della 072. Le
prove sul database sono in `test_a30_2p_073_postgres.py`.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRAZIONI = ROOT / "migrations"
VERSIONE = "073_a30_2p_lmc15_facade"


def _codice(nome):
    from scripts import p26_migrate as runner
    return runner.strip_sql_comments((MIGRAZIONI / nome).read_text(encoding="utf-8"))


SU = _codice(f"{VERSIONE}.sql")
GIU = _codice(f"{VERSIONE}_down.sql")
C072 = _codice("072_a30_1_appointments.sql")


def _norm(testo):
    return re.sub(r"\s+", " ", testo).strip()


def _check(codice, nome):
    """Il corpo di `CONSTRAINT <nome> CHECK (...)`, parentesi bilanciate."""
    i = codice.index(f"CONSTRAINT {nome} CHECK")
    i = codice.index("(", i)
    profondita = 0
    for j in range(i, len(codice)):
        if codice[j] == "(":
            profondita += 1
        elif codice[j] == ")":
            profondita -= 1
            if profondita == 0:
                return _norm(codice[i:j + 1])
    raise AssertionError(nome)


def test_01_la_073_e_valida_per_il_runner_e_in_coda_alla_serie():
    from scripts import p26_migrate as runner
    trovate = {m.version: m for m in runner.discover_migrations()}
    m = trovate[VERSIONE]
    assert runner.validate_migration(m) == [] and m.down_available
    assert not m.non_transactional
    numeri = sorted(x.number for x in trovate.values())
    # SENTINELLA AGGIORNATA DA A30-9A: la 074 crea le fondamenta della
    # sincronizzazione in uscita verso Google Calendar (`calendar_connections`,
    # `calendar_oauth_states`, `appointment_calendar_sync`), approvata dal
    # GATE A30-9A. Si nomina invece di smettere di guardare: qualunque ALTRA
    # migration comparisse farebbe ancora fallire.
    assert numeri[-1] == 74 and numeri[-2] == 73 and numeri[-3] == 72 and len(numeri) == len(set(numeri))


def test_02_la_up_non_apre_transazioni_e_non_scrive_il_ledger():
    assert not re.search(r"^\s*BEGIN\s*;", SU, re.M | re.I)
    assert not re.search(r"^\s*COMMIT\s*;", SU, re.M | re.I)
    assert "schema_migrations" not in SU


def test_03_la_up_tocca_solo_i_tre_check_previsti():
    istruzioni = [_norm(x) for x in SU.split(";") if x.strip()]
    assert len(istruzioni) == 5
    bersagli = [re.match(r"ALTER TABLE appointments (DROP|ADD) CONSTRAINT (\w+)", x)
                for x in istruzioni]
    assert all(bersagli), istruzioni
    assert [(b.group(1), b.group(2)) for b in bersagli] == [
        ("DROP", "appointments_source_chk"), ("ADD", "appointments_source_chk"),
        ("DROP", "appointments_agent_required_chk"), ("ADD", "appointments_agent_required_chk"),
        ("ADD", "appointments_lmc15_facade_chk")]
    # FAIL-CLOSED: nessun IF EXISTS, e nessun DROP del CHECK nuovo prima
    # dell'ADD (se esiste gia', l'ADD fallisce e segnala la deriva di schema)
    assert "IF EXISTS" not in SU and "IF NOT EXISTS" not in SU
    assert "DROP CONSTRAINT appointments_lmc15_facade_chk" not in SU
    for vietato in ("stima_inspections (", "INSERT", "UPDATE ", "DELETE", "CREATE",
                    "property_visits", "google"):
        assert vietato not in SU, vietato
    # `legacy_stime_dettagliate` e' solo un valore di fonte gia' della 072
    assert "stime_dettagliate" not in SU.replace("legacy_stime_dettagliate", "")


def test_04_la_fonte_nuova_e_solo_lmc15_facade_e_coincide_con_il_codice():
    from appointments import enums
    fonti_072 = re.findall(r"'([a-z0-9_]+)'", _check(C072, "appointments_source_chk"))
    fonti_073 = re.findall(r"'([a-z0-9_]+)'", _check(SU, "appointments_source_chk"))
    assert fonti_073 == fonti_072 + ["lmc15_facade"]
    assert tuple(fonti_073) == enums.APPOINTMENT_SOURCES


def test_05_agent_required_072_intatto_piu_una_sola_eccezione():
    vecchio = _check(C072, "appointments_agent_required_chk")
    nuovo = _check(SU, "appointments_agent_required_chk")
    eccezione = _norm("""OR (status IN ('scheduled', 'completed')
        AND source = 'lmc15_facade'
        AND appointment_type = 'inspection'
        AND stima_inspection_id IS NOT NULL)""")
    assert nuovo == vecchio[:-1] + " " + eccezione + ")"
    assert "'confirmed'" not in nuovo


def test_06_il_check_della_facade_e_quello_approvato():
    assert _check(SU, "appointments_lmc15_facade_chk") == _norm("""(
        source <> 'lmc15_facade'
        OR (
            appointment_type = 'inspection'
            AND stima_id IS NOT NULL
            AND stima_inspection_id IS NOT NULL
            AND source_record_id IS NOT NULL
        ))""")


def test_07_la_down_ripristina_esattamente_la_072():
    for nome in ("appointments_source_chk", "appointments_agent_required_chk"):
        assert _check(GIU, nome) == _check(C072, nome), nome
    assert "ADD CONSTRAINT appointments_lmc15_facade_chk" not in GIU
    # FAIL-CLOSED: nessun IF EXISTS; ogni DROP pretende lo stato della 073
    assert "IF EXISTS" not in GIU
    for nome in ("appointments_lmc15_facade_chk", "appointments_agent_required_chk",
                 "appointments_source_chk"):
        assert f"DROP CONSTRAINT {nome};" in GIU, nome


def test_08_la_down_segue_le_convenzioni_della_072():
    assert re.search(r"^\s*BEGIN\s*;", GIU, re.M) and re.search(r"^\s*COMMIT\s*;", GIU, re.M)
    assert "WHERE source = 'lmc15_facade'" in GIU and "RAISE EXCEPTION" in GIU
    assert "DELETE FROM schema_migrations WHERE version = '073_a30_2p_lmc15_facade'" in GIU
    # il rifiuto viene PRIMA di qualunque modifica
    assert GIU.index("RAISE EXCEPTION") < GIU.index("ALTER TABLE")
    for vietato in ("DELETE FROM appointments", "UPDATE ", "stima_inspections ",
                    "stima_inspections;", "INSERT"):
        assert vietato not in GIU, vietato


def test_09_facade_non_ancora_implementata():
    # SENTINELLA AGGIORNATA DA A30-2P FACADE: la facade ora esiste (fase
    # successiva alla 073); resta vero che il router LMC-15 non nomina
    # l'Agenda. MOUNT A30: `main.py` la nomina solo per import + mount.
    from tests.test_a30_mount_api import _righe_codice_agenda
    assert (ROOT / "appointments" / "lmc15_facade.py").exists()
    assert "appointments" not in (ROOT / "acquisition" / "router.py").read_text(encoding="utf-8")
    assert len(_righe_codice_agenda((ROOT / "main.py").read_text(encoding="utf-8"))) == 2
