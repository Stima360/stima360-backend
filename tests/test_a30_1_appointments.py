"""A30-1 senza database: la migration come testo, il perimetro, lo schema.

Il comportamento vero (vincoli, trigger, concorrenza) e' provato su
PostgreSQL in `test_a30_1_appointments_postgres.py`.
"""
from __future__ import annotations

import ast
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SU = (ROOT / "migrations/072_a30_1_appointments.sql").read_text(encoding="utf-8")
GIU = (ROOT / "migrations/072_a30_1_appointments_down.sql").read_text(encoding="utf-8")
PACCHETTO = ROOT / "appointments"


def _senza_commenti(testo):
    return "\n".join(riga.split("--", 1)[0] for riga in testo.splitlines())


CODICE = _senza_commenti(SU)


def _valori_check(nome):
    m = re.search(rf"CONSTRAINT {nome} CHECK \(\s*\w+ IN \((.*?)\)\)", CODICE, re.S)
    assert m, nome
    return tuple(re.findall(r"'([a-z0-9_]+)'", m.group(1)))


# ---------------------------------------------------------------------------
# A - LA MIGRATION
# ---------------------------------------------------------------------------

def test_01_la_072_e_valida_per_il_runner_ed_e_in_coda_alla_serie():
    from scripts import p26_migrate as runner
    trovate = {m.version: m for m in runner.discover_migrations()}
    m = trovate["072_a30_1_appointments"]
    assert runner.validate_migration(m) == [] and m.down_available
    assert not m.non_transactional
    numeri = sorted(x.number for x in trovate.values())
    # SENTINELLA AGGIORNATA DA A30-2P: la 073 ridefinisce due CHECK di
    # `appointments` per la facade LMC-15 (fonte `lmc15_facade`), approvata
    # dal GATE A30-2P FACADE DESIGN. Si nomina invece di smettere di
    # guardare: qualunque ALTRA migration comparisse farebbe ancora fallire.
    assert numeri[-2] == 72 and numeri[-3] == 71 and numeri[-1] == 73
    assert len(numeri) == len(set(numeri))


def test_02_i_cataloghi_del_codice_coincidono_con_i_check_del_database():
    from appointments import enums
    assert _valori_check("appointments_type_chk") == enums.APPOINTMENT_TYPES
    assert _valori_check("appointments_status_chk") == enums.APPOINTMENT_STATUSES
    # SENTINELLA AGGIORNATA DA A30-2P: la 072 definisce le fonti fino ad
    # `a30_test`; la 073 aggiunge SOLO `lmc15_facade`, e il suo CHECK coincide
    # con il catalogo del codice (test_a30_2p_073*).
    assert _valori_check("appointments_source_chk") + ("lmc15_facade",) == \
        enums.APPOINTMENT_SOURCES
    assert _valori_check("appointments_google_status_chk") == enums.GOOGLE_SYNC_STATUSES
    assert set(enums.APPOINTMENT_TYPE_LABELS_IT) == set(enums.APPOINTMENT_TYPES)
    esclusione = CODICE[CODICE.index("appointments_no_overlap_excl"):]
    esclusione = esclusione[:esclusione.index(";")]
    assert tuple(re.findall(r"'([a-z_]+)'", esclusione)) == enums.BLOCKING_STATUSES


def test_03_la_072_e_additiva_non_tocca_tabelle_esistenti_ne_il_sito():
    assert "ALTER TABLE" not in CODICE
    assert "DROP " not in CODICE
    for tabella in ("stime_dettagliate ", "property_visits", "tasks", "activities",
                    "seller_timeline_events", "communication_"):
        assert tabella not in CODICE, tabella
    # nessuna FK verso la tabella del sito: stima_id e' morbido
    assert "REFERENCES stime" not in CODICE
    # stima_inspections e' solo referenziata, mai scritta
    for scrittura in ("INSERT INTO stima_inspections", "UPDATE stima_inspections",
                      "DELETE FROM stima_inspections"):
        assert scrittura not in CODICE


def test_04_start_end_timestamptz_timezone_europe_rome_btree_gist():
    assert "CREATE EXTENSION IF NOT EXISTS btree_gist" in CODICE
    assert re.search(r"start_at\s+TIMESTAMPTZ\s+NOT NULL", CODICE)
    assert re.search(r"end_at\s+TIMESTAMPTZ\s+NOT NULL", CODICE)
    assert "DEFAULT 'Europe/Rome'" in CODICE
    assert "EXCLUDE USING gist" in CODICE


def test_04b_q4_l_agente_puo_mancare_solo_dove_deciso():
    from appointments import enums
    m = re.search(r"CONSTRAINT appointments_agent_required_chk CHECK \((.*?)\),\n\n", CODICE, re.S)
    corpo = m.group(1)
    assert "status IN ('requested', 'cancelled', 'rescheduled')" in corpo
    assert "status IN ('completed', 'no_show')" in corpo
    assert tuple(re.findall(r"'([a-z0-9_]+)'", corpo.split("source IN")[1])) == \
        enums.HISTORICAL_SOURCES
    stati_senza_agente = set(re.findall(r"'([a-z_]+)'", corpo.split("source IN")[0]))
    assert stati_senza_agente == {"requested", "cancelled", "rescheduled", "completed", "no_show"}
    assert not stati_senza_agente & {"scheduled", "confirmed"}


def test_04c2_q7_purge_e_delete_sono_in_allowlist_test_esatta():
    """Allowlist, non blacklist: INSERT dei dati di prova, purge ed eccezioni
    alla DELETE leggono SOLO `a30_is_certified_test_database()`, confronto
    esatto, e l'elenco coincide con il nome TEST certificato dal progetto e
    con lo script. Nessuna blacklist di PROD e' rimasta."""
    from scripts import a30_test_cleanup, p26_6_live_cert
    m = re.search(r"a30_is_certified_test_database\(\) RETURNS boolean AS \$fn\$\s*"
                  r"SELECT current_database\(\) IN \((.*?)\);", CODICE)
    ammessi = set(re.findall(r"'([a-z0-9_]+)'", m.group(1)))
    assert ammessi == {p26_6_live_cert.REQUIRED_DB_NAME} == set(
        a30_test_cleanup.CERTIFIED_TEST_DATABASES)
    corpo_cert = CODICE[m.start():m.end()]
    assert "LIKE" not in corpo_cert.upper() and "~" not in corpo_cert
    assert "a30_is_production_database" not in CODICE
    for funzione in ("appointments_guard", "appointments_refuse_delete",
                     "appointment_events_append_only", "a30_test_purge"):
        i = CODICE.index(f"CREATE OR REPLACE FUNCTION {funzione}(")
        corpo = CODICE[i:CODICE.index("$fn$ LANGUAGE", i)]
        assert "a30_is_certified_test_database()" in corpo, funzione
        assert "a30_is_production_database" not in corpo, funzione
    # la DELETE si sblocca SOLO nella purge, e solo per a30_test
    assert CODICE.count("stima360.a30_test_purge") == 4


def test_04d_q7_lo_script_di_pulizia_non_apre_connessioni_proprie():
    testo = (ROOT / "scripts/a30_test_cleanup.py").read_text(encoding="utf-8")
    # (spezzato di proposito: il censimento P26 delle connessioni legge il
    # testo dei file, e questo test non apre connessioni)
    assert "psycopg2" + ".connect" not in testo
    assert "assert_test_database_name" in testo and "from scripts.p26_migrate import" in testo


def test_04e_q5_durata_visita_acquirente_60_minuti():
    from appointments import enums
    assert enums.default_duration_minutes("buyer_visit") == 60
    assert enums.default_duration_minutes("inspection") == 60
    assert enums.default_duration_minutes("other") == enums.FALLBACK_DURATION_MINUTES


def test_05_la_down_rifiuta_con_righe_e_non_tocca_la_070_ne_l_estensione():
    codice = _senza_commenti(GIU)
    assert "would be destroyed" in codice
    assert "DROP EXTENSION" not in codice
    assert "lmc15_assert_operator_may_act" not in codice
    assert "stima_inspections" not in codice
    assert "DELETE FROM schema_migrations WHERE version = '072_a30_1_appointments'" in codice
    for funzione in ("a30_test_purge(TEXT)", "a30_is_certified_test_database()"):
        assert f"DROP FUNCTION IF EXISTS {funzione}" in codice


# ---------------------------------------------------------------------------
# B - IL PERIMETRO DEL CODICE
# ---------------------------------------------------------------------------

def test_10_il_pacchetto_non_ha_rotte_e_non_e_montato_in_main():
    # SENTINELLA AGGIORNATA DA A30-2: A30-1 non aveva rotte; A30-2 aggiunge
    # `router.py` PREPARATO MA NON MONTATO (D1).
    # SENTINELLA AGGIORNATA DAL MOUNT A30: `main.py` nomina l'Agenda SOLO per
    # importarne il router e montarlo (tests/test_a30_mount_api.py); nessun
    # altro modulo di primo livello la nomina.
    from tests.test_a30_mount_api import _righe_codice_agenda
    assert (PACCHETTO / "router.py").exists()
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    assert _righe_codice_agenda(main) == [
        "from appointments.router import router as appointments_router",
        "app.include_router(appointments_router, "
        "dependencies=[Depends(require_authenticated_operator)])",
    ]
    for file in ROOT.glob("*.py"):
        if file.name != "main.py":
            assert "appointments" not in file.read_text(encoding="utf-8"), file.name


def test_11_il_pacchetto_non_importa_main_ne_moduli_di_altri_domini_da_scrivere():
    # SENTINELLA AGGIORNATA DA A30-2: la lista chiusa si allarga SOLO di quello
    # che A30-2 dichiara - librerie standard/pydantic, le varianti LMC-15 sul
    # cursore (solo `projection.py`, Q1/Q2) e FastAPI + operator_auth (solo
    # `router.py`). Mai `main`, mai stime/stime_dettagliate/MATCH.
    ammessi = {"core.database", "core.exceptions", "psycopg2", "pydantic",
               "__future__", "datetime", "json",
               "re", "zoneinfo", "pydantic_core"}
    per_file = {
        "projection.py": {"acquisition"},
        # SENTINELLA AGGIORNATA DA A30-2P: la facade LMC-15 scrive la
        # proiezione con le stesse varianti `*_in` (Q1/Q2).
        "lmc15_facade.py": {"acquisition"},
        "router.py": {"fastapi", "fastapi.encoders", "fastapi.responses",
                      "operator_auth.context", "operator_auth.dependencies",
                      "operator_auth.exceptions"},
    }
    for file in PACCHETTO.glob("*.py"):
        consentiti = ammessi | per_file.get(file.name, set())
        albero = ast.parse(file.read_text(encoding="utf-8"))
        for nodo in ast.walk(albero):
            if isinstance(nodo, ast.ImportFrom) and nodo.level == 0:
                assert nodo.module in consentiti, (file.name, nodo.module)
            elif isinstance(nodo, ast.Import):
                for alias in nodo.names:
                    assert alias.name in consentiti, (file.name, alias.name)


def test_11b_nessun_trigger_scrive_in_una_tabella():
    """P26-6 serie 100: il registro lo scrive il repository, in chiaro."""
    assert "INSERT INTO" not in CODICE
    repository = (PACCHETTO / "repository.py").read_text(encoding="utf-8")
    assert "INSERT INTO appointment_events" in repository


def test_12_il_service_ha_i_tre_livelli_e_l_ordine_dei_lock():
    service = (PACCHETTO / "service.py").read_text(encoding="utf-8")
    repository = (PACCHETTO / "repository.py").read_text(encoding="utf-8")
    assert "pg_advisory_xact_lock(hashtextextended(" in repository
    assert "sorted(" in repository                       # agenti in ordine crescente
    assert "ExclusionViolation" in service
    assert "_occupa(" in service and "find_conflicts" in service
    # SENTINELLA AGGIORNATA DA A30-2: ogni azione su una riga passa da
    # `_su_riga`, che blocca la riga appointments PRIMA che l'azione blocchi
    # gli agenti (ordine: riga -> agenti crescenti -> stima -> ispezione).
    su_riga = service[service.index("def _su_riga"):]
    su_riga = su_riga[:su_riga.index("\ndef ")]
    assert "lock_appointment" in su_riga and "lock_agents" not in su_riga
    corpo = service[service.index("def reschedule_appointment"):]
    corpo = corpo[:corpo.index("\ndef ")]
    assert "_su_riga(" in corpo and "lock_agents" in corpo


def test_13_nessun_campo_di_attore_o_agenzia_nei_corpi():
    from appointments.schemas import AppointmentCreate, AppointmentReschedule
    for modello in (AppointmentCreate, AppointmentReschedule):
        campi = set(modello.model_fields)
        assert not campi & {"agency_id", "created_by_user_id", "source",
                            "source_record_id", "blocked_range", "version"}, modello


# ---------------------------------------------------------------------------
# C - LO SCHEMA
# ---------------------------------------------------------------------------

ROMA = timezone(timedelta(hours=2))


def _crea(**kw):
    from appointments.schemas import AppointmentCreate
    base = {"appointment_type": "call", "assigned_user_id": 1,
            "start_at": datetime(2026, 10, 5, 10, tzinfo=ROMA),
            "end_at": datetime(2026, 10, 5, 11, tzinfo=ROMA)}
    base.update(kw)
    return AppointmentCreate(**base)


def test_20_lo_schema_accetta_il_caso_normale():
    assert _crea().status == "scheduled"
    assert _crea(status="requested", assigned_user_id=None).assigned_user_id is None


@pytest.mark.parametrize("modifica", [
    {"start_at": datetime(2026, 10, 5, 10)},                     # senza fuso
    {"end_at": datetime(2026, 10, 5, 9, tzinfo=ROMA)},            # fine prima dell'inizio
    {"end_at": datetime(2026, 10, 7, 10, tzinfo=ROMA)},           # piu' di 24 ore
    {"status": "completed"},                                      # non si nasce chiusi
    {"status": "scheduled", "assigned_user_id": None},            # fissato senza agente
    {"appointment_type": "visita"},
    {"buffer_after_minutes": 241},
    {"agency_id": 3},                                             # extra vietato
    {"created_by_user_id": 3},
])
def test_21_lo_schema_rifiuta(modifica):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        _crea(**modifica)
