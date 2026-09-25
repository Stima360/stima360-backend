"""A30-2P - backfill `stima_inspections` -> `appointments`: prove senza database.

Mapping puro, chiave d'idempotenza, perimetro statico e guardie dello script.
Le prove sul database sono in `test_a30_2p_backfill_postgres.py`.
"""
from __future__ import annotations

import ast
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from appointments import backfill

ROOT = Path(__file__).resolve().parents[1]
MODULO = ROOT / "appointments" / "backfill.py"
SCRIPT = ROOT / "scripts" / "a30_2p_backfill.py"
T = datetime(2026, 5, 4, 8, 30, tzinfo=timezone.utc)


def _riga(**kw):
    base = {"id": 41, "stima_id": 7, "agency_id": 3, "status": "scheduled",
            "scheduled_for": T, "completed_at": None, "cancelled_at": None,
            "cancelled_reason": None}
    base.update(kw)
    return base


def _codice(percorso):
    """Il sorgente senza docstring e commenti: si controlla cio' che gira."""
    albero = ast.parse(percorso.read_text(encoding="utf-8"))
    for nodo in ast.walk(albero):
        if isinstance(nodo, (ast.Module, ast.FunctionDef, ast.ClassDef)) and nodo.body \
                and isinstance(nodo.body[0], ast.Expr) \
                and isinstance(getattr(nodo.body[0], "value", None), ast.Constant):
            nodo.body = nodo.body[1:] or [ast.Pass()]
    return ast.unparse(albero)


# ---------------------------------------------------------------------------
# A - CHIAVE E MAPPING
# ---------------------------------------------------------------------------

def test_01_chiave_stabile_e_deterministica():
    assert backfill.SOURCE == "stima_inspections_backfill"
    assert backfill.source_key(41) == "stima_inspections:41"
    assert backfill.source_key("41") == backfill.source_key(41)
    assert len(backfill.source_key(10**18)) <= 100          # VARCHAR(100) della 072


def test_02_scheduled_diventa_requested_senza_agente_e_collegata():
    piano = backfill.map_inspection(_riga())
    v = piano["values"]
    assert v["status"] == "requested" and v["assigned_user_id"] is None     # Q4, D2
    assert v["appointment_type"] == "inspection" and v["stima_id"] == 7
    assert v["stima_inspection_id"] == 41 and v["agency_id"] == 3
    assert v["start_at"] == T and v["end_at"] == T + timedelta(minutes=60)
    assert v["source"] == "stima_inspections_backfill"
    assert v["source_record_id"] == "stima_inspections:41"
    assert v["created_by_user_id"] is None
    assert not {"completed_at", "cancelled_at", "cancelled_reason"} & set(v)
    assert piano["start_reconstructed"] is False


def test_03_completed_conserva_completed_at_esatto():
    fatto = T + timedelta(minutes=47, microseconds=123)
    v = backfill.map_inspection(_riga(status="completed", completed_at=fatto))["values"]
    assert v["status"] == "completed" and v["completed_at"] is fatto
    assert v["start_at"] == T


def test_04_completed_senza_scheduled_for_parte_da_completed_at_e_lo_dichiara():
    fatto = T + timedelta(hours=2)
    piano = backfill.map_inspection(_riga(status="completed", scheduled_for=None,
                                          completed_at=fatto))
    assert piano["start_reconstructed"] is True
    assert piano["values"]["start_at"] == fatto
    assert piano["values"]["completed_at"] == fatto


def test_05_cancelled_conserva_istante_e_motivo():
    annullato = T - timedelta(days=1)
    v = backfill.map_inspection(_riga(status="cancelled", cancelled_at=annullato,
                                      cancelled_reason="Rinviato"))["values"]
    assert v["status"] == "cancelled"
    assert v["cancelled_at"] is annullato and v["cancelled_reason"] == "Rinviato"
    v = backfill.map_inspection(_riga(status="cancelled", cancelled_at=annullato))["values"]
    assert v["cancelled_reason"] is None


def test_06_stato_sconosciuto_o_nessun_istante_rifiutati():
    with pytest.raises(ValueError):
        backfill.map_inspection(_riga(status="archived"))
    with pytest.raises(ValueError):
        backfill.map_inspection(_riga(status="completed", scheduled_for=None, completed_at=None))


# ---------------------------------------------------------------------------
# B - PERIMETRO
# ---------------------------------------------------------------------------

def test_10_il_backfill_non_scrive_stima_inspections():
    codice = _codice(MODULO)
    for vietato in (r"INSERT\s+INTO\s+stima_inspections", r"UPDATE\s+stima_inspections",
                    r"DELETE\s+FROM", r"TRUNCATE", r"ALTER\s+TABLE", r"\.commit\("):
        assert not re.search(vietato, codice, re.I), vietato
    assert "FROM stima_inspections" in codice


def test_11_nessun_accesso_a_domini_esclusi():
    for percorso in (MODULO, SCRIPT):
        codice = _codice(percorso).lower()
        for vietato in ("stime_dettagliate", "property_visits", "google"):
            assert vietato not in codice, (percorso.name, vietato)


def test_12_proiezione_accesa_facade_assente_e_main_non_monta_l_agenda():
    from appointments import projection
    assert projection.PROJECTION_ENABLED is True
    assert "appointments" not in (ROOT / "main.py").read_text(encoding="utf-8")
    # SENTINELLA AGGIORNATA DA A30-2P FACADE: il service LMC-15 delega alla
    # facade; il router LMC-15 resta senza Agenda.
    assert "from appointments import lmc15_facade" in \
        (ROOT / "acquisition" / "service.py").read_text(encoding="utf-8")
    assert "appointments" not in (ROOT / "acquisition" / "router.py").read_text(encoding="utf-8")


def test_13_lo_script_non_apre_connessioni_proprie():
    """Riusa `scripts.p26_migrate.connect`: nessun nuovo punto di connessione."""
    codice = SCRIPT.read_text(encoding="utf-8")
    assert not re.search(r"psycopg2\s*\.\s*connect", codice)
    assert "from scripts.p26_migrate import GuardFailure, connect" in codice


# ---------------------------------------------------------------------------
# C - GUARDIE DELLO SCRIPT (nessuna connessione: falliscono prima)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("nome", [None, "", "stima360_db", "stima360_db_prod",
                                  "stima360_db_test2", "STIMA360_DB_TEST"])
def test_20_database_non_certificato_rifiutato(monkeypatch, capsys, nome):
    from scripts import a30_2p_backfill as script

    def mai(*_a, **_k):
        raise AssertionError("non deve connettersi")

    monkeypatch.setattr(script, "connect", mai)
    if nome is None:
        monkeypatch.delenv("DB_NAME", raising=False)
    else:
        monkeypatch.setenv("DB_NAME", nome)
    for argomenti in ([], ["--dry-run"], ["--apply", "--confirm-database", nome or "x"]):
        assert script.main(argomenti) == 2


def test_21_apply_pretende_la_conferma_del_nome(monkeypatch):
    from scripts import a30_2p_backfill as script

    def mai(*_a, **_k):
        raise AssertionError("non deve connettersi")

    monkeypatch.setattr(script, "connect", mai)
    monkeypatch.setenv("DB_NAME", "stima360_db_test")
    assert script.main(["--apply"]) == 2
    assert script.main(["--apply", "--confirm-database", "stima360_db"]) == 2


def test_22_identita_del_database_verificata_dopo_la_connessione():
    from scripts import a30_2p_backfill as script
    from scripts.p26_migrate import GuardFailure

    class Cur:
        def __init__(self, db, ok):
            self.riga = {"db": db, "ok": ok}

        def execute(self, *_a):
            pass

        def fetchone(self):
            return self.riga

    script.assert_database_identity(Cur("stima360_db_test", True), "stima360_db_test")
    for db, ok in (("stima360_db", True), ("stima360_db_test", False), ("altro", False)):
        with pytest.raises(GuardFailure):
            script.assert_database_identity(Cur(db, ok), "stima360_db_test")


# ---------------------------------------------------------------------------
# D - GUARDIE DELLA PROIEZIONE: nessuna chiamata LMC-15 senza collegamento
# ---------------------------------------------------------------------------

def _lmc15_mai_chiamata(monkeypatch):
    from appointments import projection
    chiamate = []

    def registra(nome):
        def finta(*a, **k):
            chiamate.append(nome)
        return finta

    for nome in ("complete_inspection_in", "cancel_inspection_in"):
        monkeypatch.setattr(projection.lmc15, nome, registra(nome))
    return chiamate


_SENZA_COLLEGAMENTO = {"id": 99, "appointment_type": "inspection", "stima_id": 7,
                       "stima_inspection_id": None}


@pytest.mark.parametrize("azione", ["complete", "cancel", "no_show"])
def test_30_senza_stima_inspection_id_e_conflitto_e_lmc15_non_e_chiamata(monkeypatch, azione):
    from appointments import errors, projection
    chiamate = _lmc15_mai_chiamata(monkeypatch)

    class Cursore:
        def execute(self, *_a, **_k):
            raise AssertionError("nessuna query senza collegamento")

    cur = Cursore()
    with pytest.raises(errors.ProjectionConflict) as preso:
        if azione == "complete":
            projection.on_complete(cur, 3, dict(_SENZA_COLLEGAMENTO),
                                   completed_at=T, actor_user_id=1)
        elif azione == "cancel":
            projection.on_cancel(cur, 3, dict(_SENZA_COLLEGAMENTO), reason="x",
                                 actor_user_id=1)
        else:
            projection.on_no_show(cur, 3, dict(_SENZA_COLLEGAMENTO), actor_user_id=1)
    assert preso.value.code == "PROJECTION_CONFLICT"
    assert preso.value.extra == {"appointment_id": 99}
    assert "99" in str(preso.value) and "stima_inspections" in str(preso.value)
    assert chiamate == []


@pytest.mark.parametrize("azione", ["complete", "cancel", "no_show"])
def test_31_con_collegamento_lmc15_riceve_l_id_collegato(monkeypatch, azione):
    from appointments import projection
    ricevuti = []

    def finta(cur, agency_id, *, inspection_id, **_k):
        ricevuti.append((agency_id, inspection_id))

    monkeypatch.setattr(projection.lmc15, "complete_inspection_in", finta)
    monkeypatch.setattr(projection.lmc15, "cancel_inspection_in", finta)
    riga = dict(_SENZA_COLLEGAMENTO, stima_inspection_id=55)
    if azione == "complete":
        projection.on_complete(None, 3, riga, completed_at=T, actor_user_id=1)
    elif azione == "cancel":
        projection.on_cancel(None, 3, riga, reason="x", actor_user_id=1)
    else:
        projection.on_no_show(None, 3, riga, actor_user_id=1)
    assert ricevuti == [(3, 55)]
