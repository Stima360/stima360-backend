"""A30-6 - import legacy `stime_dettagliate.sopralluogo`: mapping puro, ora
Europe/Rome senza normalizzazioni silenziose, confini del package, guardie
dello script. Nessun database (il PostgreSQL vero e' in
`test_a30_6_legacy_import_postgres.py`).
"""
from __future__ import annotations

import ast
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from appointments_legacy import stime_dettagliate_import as legacy

ROOT = Path(__file__).resolve().parents[1]
MODULO = ROOT / "appointments_legacy" / "stime_dettagliate_import.py"
PACCHETTO = ROOT / "appointments_legacy"
SCRIPT = ROOT / "scripts" / "a30_6_legacy_import.py"
ROMA = ZoneInfo("Europe/Rome")


def _riga(**kw):
    base = {"id": 41, "agency_id": 7, "stima_id": 900, "stima_trovata": 900,
            "stima_agency_id": 7, "sopralluogo": datetime(2026, 11, 12, 10, 30),
            "n_lead": 0, "primo_lead": None, "gia_importato": False}
    base.update(kw)
    return base


def _codice(percorso: Path) -> str:
    """Il sorgente senza commenti ne' docstring: si controlla il codice vero."""
    albero = ast.parse(percorso.read_text(encoding="utf-8"))
    for nodo in ast.walk(albero):
        if isinstance(nodo, (ast.Module, ast.FunctionDef, ast.ClassDef)) and nodo.body:
            primo = nodo.body[0]
            if isinstance(primo, ast.Expr) and isinstance(getattr(primo, "value", None),
                                                         ast.Constant):
                nodo.body = nodo.body[1:] or [ast.Pass()]
    return ast.unparse(albero)


# ---------------------------------------------------------------------------
# A - ORA A PARETE EUROPE/ROME (regola DST)
# ---------------------------------------------------------------------------

def test_01_ora_normale_diventa_offset_aware_roma_senza_spostamenti():
    esito, istante = legacy.to_rome_instant(datetime(2026, 11, 12, 10, 30))
    assert esito == "ok"
    assert istante.tzinfo is not None and istante.utcoffset() == timedelta(hours=1)
    assert istante.astimezone(timezone.utc) == datetime(2026, 11, 12, 9, 30, tzinfo=timezone.utc)
    esito, estate = legacy.to_rome_instant(datetime(2026, 7, 1, 10, 30))
    assert esito == "ok" and estate.utcoffset() == timedelta(hours=2)
    # l'ora a parete resta quella scritta dal cliente, secondi compresi
    esito, preciso = legacy.to_rome_instant(datetime(2026, 7, 1, 10, 30, 17))
    assert preciso.replace(tzinfo=None) == datetime(2026, 7, 1, 10, 30, 17)


@pytest.mark.parametrize("wall", [datetime(2026, 3, 29, 2, 0), datetime(2026, 3, 29, 2, 30),
                                  datetime(2026, 3, 29, 2, 59), datetime(2027, 3, 28, 2, 15)])
def test_02_ora_inesistente_primavera_non_si_importa(wall):
    assert legacy.to_rome_instant(wall) == ("dst_nonexistent", None)


@pytest.mark.parametrize("wall", [datetime(2026, 10, 25, 2, 0), datetime(2026, 10, 25, 2, 30),
                                  datetime(2026, 10, 25, 2, 59), datetime(2027, 10, 31, 2, 45)])
def test_03_ora_ambigua_autunno_non_si_sceglie(wall):
    assert legacy.to_rome_instant(wall) == ("dst_ambiguous", None)


def test_04_i_bordi_del_cambio_ora_esistono():
    for wall in (datetime(2026, 3, 29, 1, 59), datetime(2026, 3, 29, 3, 0),
                 datetime(2026, 10, 25, 1, 59), datetime(2026, 10, 25, 3, 0)):
        assert legacy.to_rome_instant(wall)[0] == "ok", wall


def test_05_valori_non_naive_o_non_datetime_sono_errori():
    assert legacy.to_rome_instant(datetime(2026, 7, 1, 10, tzinfo=timezone.utc)) == \
        ("unexpected_timezone", None)
    assert legacy.to_rome_instant("2026-07-01T10:00") == ("unparseable", None)
    assert legacy.to_rome_instant(None) == ("unparseable", None)


def test_06_nessuna_conversione_affidata_a_postgresql():
    codice = _codice(MODULO)
    # AT TIME ZONE e' ammesso solo per leggere la data di oggi da NOW()
    # (un istante, nessuna ambiguita'), mai sul valore legacy.
    usi = re.findall(r"AT TIME ZONE", codice)
    assert len(usi) == 1
    assert "NOW() AT TIME ZONE" in codice
    assert "sopralluogo AT TIME ZONE" not in codice


def test_07_passato_oggi_futuro_rispetto_alla_data_di_roma():
    oggi = date(2026, 9, 26)
    assert legacy.classify_day(datetime(2026, 9, 25, 23, 59), oggi) == "past"
    assert legacy.classify_day(datetime(2026, 9, 26, 0, 0), oggi) == "today"
    assert legacy.classify_day(datetime(2026, 9, 27, 0, 0), oggi) == "future"


# ---------------------------------------------------------------------------
# B - CLASSIFICAZIONE E MAPPING (puri)
# ---------------------------------------------------------------------------

def test_10_classificazione():
    assert legacy.classify(_riga())[0] == "eligible"
    assert legacy.classify(_riga(gia_importato=True)) == ("already_imported", None)
    assert legacy.classify(_riga(agency_id=None)) == ("missing_agency", None)
    assert legacy.classify(_riga(stima_id=None, stima_trovata=None)) == ("orphan", None)
    assert legacy.classify(_riga(stima_trovata=None)) == ("orphan", None)
    # stima di un'altra agenzia: orfano, non si importa (D3)
    assert legacy.classify(_riga(stima_agency_id=8)) == ("orphan", None)
    assert legacy.classify(_riga(sopralluogo=datetime(2026, 3, 29, 2, 30))) == \
        ("dst_nonexistent", None)
    assert legacy.classify(_riga(sopralluogo=datetime(2026, 10, 25, 2, 30))) == \
        ("dst_ambiguous", None)
    assert legacy.classify(_riga(sopralluogo="x")) == ("error", None)


def test_11_mapping_autorizzato():
    riga = _riga(sopralluogo=datetime(2026, 11, 12, 10, 30))
    _, inizio = legacy.classify(riga)
    v = legacy.map_values(riga, inizio, lead_id=None, contact_id=None)
    assert v["appointment_type"] == "inspection" and v["status"] == "requested"
    assert v["source"] == "legacy_stime_dettagliate"
    assert v["source_record_id"] == "stime_dettagliate:41"
    assert v["agency_id"] == 7 and v["stima_id"] == 900
    for campo in ("assigned_user_id", "property_id", "location_text", "created_by_user_id",
                  "lead_id", "contact_id"):
        assert v[campo] is None, campo
    assert v["buffer_before_minutes"] == 0 and v["buffer_after_minutes"] == 0
    assert v["start_at"] == datetime(2026, 11, 12, 10, 30, tzinfo=ROMA)


def test_12_durata_policy_di_import_esattamente_60_minuti():
    assert legacy.IMPORT_DURATION_MINUTES == 60
    for wall in (datetime(2026, 11, 12, 10, 30), datetime(2026, 7, 1, 23, 30)):
        riga = _riga(sopralluogo=wall)
        _, inizio = legacy.classify(riga)
        v = legacy.map_values(riga, inizio, lead_id=None, contact_id=None)
        assert v["end_at"] - v["start_at"] == timedelta(minutes=60)


def test_13_lead_e_contatto_passano_solo_se_dati():
    riga = _riga()
    _, inizio = legacy.classify(riga)
    v = legacy.map_values(riga, inizio, lead_id=5, contact_id=6)
    assert (v["lead_id"], v["contact_id"]) == (5, 6)


def test_14_note_senza_dati_personali_e_con_la_provenienza():
    riga = _riga(id=123)
    _, inizio = legacy.classify(riga)
    note = legacy.map_values(riga, inizio, lead_id=None, contact_id=None)["notes"]
    assert "stime_dettagliate #123" in note and "non confermato" in note
    assert "@" not in note and not re.search(r"\d{6,}", note)


def test_15_chiave_idempotenza():
    assert legacy.source_key(7) == "stime_dettagliate:7"
    assert legacy.source_key("7") == "stime_dettagliate:7"


def test_16_il_report_ha_tutte_le_voci_richieste():
    richieste = {"eligible", "inserted", "already_imported", "past", "future", "today",
                 "orphan", "dst_nonexistent", "dst_ambiguous", "missing_agency",
                 "zero_lead", "one_lead", "multiple_leads", "errors"}
    assert richieste <= set(legacy.REPORT_KEYS)


# ---------------------------------------------------------------------------
# C - CONFINI (D5, nessun side effect, nessuna scrittura legacy)
# ---------------------------------------------------------------------------

def test_20_il_package_appointments_non_nomina_stime_dettagliate():
    for file in (ROOT / "appointments").glob("*.py"):
        testo = file.read_text(encoding="utf-8").replace("legacy_stime_dettagliate", "")
        codice = re.sub(r'""".*?"""', "", testo, flags=re.S)
        codice = "\n".join(r.split("#", 1)[0] for r in codice.splitlines())
        assert "stime_dettagliate" not in codice, file.name
        assert "appointments_legacy" not in codice, file.name


def test_21_nessuna_scrittura_fuori_da_appointments_e_nessun_side_effect():
    codice = _codice(MODULO)
    for vietato in (r"INSERT\s+INTO", r"UPDATE\s+\w+\s+SET", r"DELETE\s+FROM", r"TRUNCATE",
                    r"ALTER\s+TABLE", r"\.commit\(", r"COPY\s"):
        assert not re.search(vietato, codice, re.I), vietato
    importati = {n.module for n in ast.walk(ast.parse(MODULO.read_text(encoding="utf-8")))
                 if isinstance(n, ast.ImportFrom) and n.module}
    importati |= {a.name for n in ast.walk(ast.parse(MODULO.read_text(encoding="utf-8")))
                  if isinstance(n, ast.Import) for a in n.names}
    # solo il repository dell'Agenda: niente service (disponibilita', lock,
    # proiezione), niente LMC-15, niente comunicazioni, notifiche o Google
    assert importati == {"__future__", "datetime", "zoneinfo", "appointments"}
    testo = MODULO.read_text(encoding="utf-8")
    assert "from appointments import repository" in testo
    for vietato in ("service", "projection", "lmc15", "acquisition", "communication",
                    "notification", "google", "timeline", "stima_inspections_in"):
        assert not re.search(rf"\b{vietato}\b", codice), vietato


def test_22_il_modulo_scrive_solo_attraverso_il_repository():
    codice = _codice(MODULO)
    chiamate = set(re.findall(r"repository\.(\w+)\(", codice))
    assert chiamate == {"insert_appointment", "update_appointment", "db_now"}
    # stima_inspections: mai nominata in una query di scrittura o lettura
    assert "INTO stima_inspections" not in codice


def test_23_rollback_solo_annullamento_logico():
    codice = _codice(MODULO)
    assert "DELETE" not in codice.upper().replace("DELETED", "")
    assert "'cancelled'" in codice or '"cancelled"' in codice
    assert "version = 1" in codice and "status = 'requested'" in codice


def test_24_log_senza_dati_personali():
    codice = _codice(MODULO)
    for colonna in ("nome", "cognome", "email", "telefono", "indirizzo", "note ",
                    "d.note", "contatto"):
        assert f"d.{colonna.strip()}" not in codice, colonna
    assert "print(" not in codice and "logging" not in codice


# ---------------------------------------------------------------------------
# D - SCRIPT: guardie e modalita'
# ---------------------------------------------------------------------------

def _script():
    import importlib
    return importlib.import_module("scripts.a30_6_legacy_import")


@pytest.mark.parametrize("nome", [None, "", "stima360_db", "stima360_prod",
                                  "stima360_db_test_copy", "postgres"])
def test_30_database_non_certificato_rifiutato_prima_di_connettersi(monkeypatch, nome, capsys):
    script = _script()
    if nome is None:
        monkeypatch.delenv("DB_NAME", raising=False)
    else:
        monkeypatch.setenv("DB_NAME", nome)
    monkeypatch.setattr(script, "connect", lambda *_: pytest.fail("non deve connettersi"))
    assert script.main(["--census"]) == 2
    assert "BLOCKED" in capsys.readouterr().err


@pytest.mark.parametrize("modo", ["--apply", "--rollback"])
def test_31_apply_e_rollback_pretendono_la_conferma(monkeypatch, modo, capsys):
    script = _script()
    monkeypatch.setenv("DB_NAME", "stima360_db_test")
    monkeypatch.setattr(script, "connect", lambda *_: pytest.fail("non deve connettersi"))
    assert script.main([modo]) == 2
    assert script.main([modo, "--confirm-database", "stima360_db"]) == 2
    assert "--confirm-database" in capsys.readouterr().err


def test_32_modalita_esclusive():
    script = _script()
    with pytest.raises(SystemExit):
        script.main(["--apply", "--dry-run"])
    assert set(script.MODES) == {"census", "dry-run", "apply", "rollback-dry-run", "rollback"}
    assert set(script.COMMITTING_MODES) == {"apply", "rollback"}


def test_33_nessuna_credenziale_nel_codice():
    testo = SCRIPT.read_text(encoding="utf-8")
    assert not re.search(r"password\s*=\s*['\"]", testo, re.I)
    assert "postgresql://" not in testo
    assert "from scripts.p26_migrate import GuardFailure, connect" in testo
    assert "from scripts.a30_test_cleanup import assert_certified_test_database" in testo


def test_34_il_package_legacy_non_e_montato_ne_importato_dall_app():
    # SENTINELLA AGGIORNATA DA A30-7: `main.py` monta SOLO il router della
    # sincronizzazione (import + mount, tests/test_a30_mount_api.py test_30b);
    # l'import e lo script restano fuori dall'app.
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    righe = [r.strip() for r in main.splitlines()
             if "appointments_legacy" in r.split("#", 1)[0]]
    assert righe == [
        "from appointments_legacy.router import router as appointments_legacy_router",
        "app.include_router(appointments_legacy_router, "
        "dependencies=[Depends(require_authenticated_operator)])",
    ]
    assert "stime_dettagliate_import" not in main
    for file in ROOT.glob("*.py"):
        if file.name != "main.py":
            assert "appointments_legacy" not in file.read_text(encoding="utf-8"), file.name
