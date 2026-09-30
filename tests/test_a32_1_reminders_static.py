"""A32-1 - regole strutturali della foundation dei promemoria.

Senza database: si legge il codice. Il package e' PURO (niente DB, rete,
router, dominio COMMUNICATION o Agenda); la 079 e' additiva e tocca solo il
CHECK di `reason_code`; l'enum e il CHECK coincidono; nessun reason code per
offset; lo scope di A32-1 non e' uscito dai suoi confini.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACCHETTO = ROOT / "appointment_reminders"
VERSIONE = "079_a32_1_appointment_reminders"
SU = (ROOT / "migrations" / f"{VERSIONE}.sql").read_text(encoding="utf-8")
GIU = (ROOT / "migrations" / f"{VERSIONE}_down.sql").read_text(encoding="utf-8")


def _sql(testo: str) -> str:
    return "\n".join(r.split("--", 1)[0] for r in testo.splitlines())


def _import(percorso: Path) -> set[str]:
    albero = ast.parse(percorso.read_text(encoding="utf-8"))
    nomi = set()
    for nodo in ast.walk(albero):
        if isinstance(nodo, ast.Import):
            nomi |= {a.name for a in nodo.names}
        elif isinstance(nodo, ast.ImportFrom):
            nomi.add(("." * nodo.level) + (nodo.module or ""))
    return nomi


def test_01_il_package_ha_solo_i_moduli_della_foundation():
    assert {p.name for p in PACCHETTO.glob("*.py")} == {"__init__.py", "policy.py",
                                                        "template.py"}


def test_02_il_package_e_puro_nessun_db_rete_router_dominio():
    ammessi = {"__future__", "re", "dataclasses", "datetime", "typing", "zoneinfo",
               "html", ".", "inspect"}
    for file in PACCHETTO.glob("*.py"):
        importati = _import(file)
        assert importati <= ammessi, (file.name, importati - ammessi)
        codice = re.sub(r'""".*?"""', "", file.read_text(encoding="utf-8"), flags=re.S)
        codice = "\n".join(r.split("#", 1)[0] for r in codice.splitlines())
        for vietato in ("psycopg2", "core_cursor", "get_connection", "commit(", "cursor",
                        "communication", "appointments", "fastapi", "APIRouter", "requests",
                        "smtplib", "socket", "datetime.now(", ".now(", "utcnow", "os.getenv",
                        "open("):
            assert vietato not in codice, (file.name, vietato)


def test_03_nessuno_importa_il_package_fuori_dai_test():
    """A32-1 e' solo foundation: niente planner, router, hook, cron, main."""
    for file in ROOT.rglob("*.py"):
        relativo = file.relative_to(ROOT).as_posix()
        if relativo.startswith(("tests/", "appointment_reminders/", ".git/")):
            continue
        assert "appointment_reminders" not in file.read_text(encoding="utf-8",
                                                             errors="ignore"), relativo


def test_04_la_079_tocca_solo_il_check_di_reason_code():
    su = _sql(SU)
    for vietato in ("CREATE TABLE", "ADD COLUMN", "CREATE INDEX", "CREATE UNIQUE INDEX",
                    "INSERT INTO", "UPDATE ", "DELETE ", "DROP TABLE", "DROP COLUMN",
                    "BEGIN;", "COMMIT;", "appointments", "CREATE TRIGGER", "FUNCTION"):
        assert vietato not in su, vietato
    assert su.count("ALTER TABLE communication_messages") == 2
    assert "DROP CONSTRAINT IF EXISTS communication_messages_reason_code_chk" in su
    assert "ADD CONSTRAINT communication_messages_reason_code_chk" in su


def _valori_check(testo: str) -> set[str]:
    blocco = re.search(r"ADD CONSTRAINT communication_messages_reason_code_chk\s+CHECK\s*"
                       r"\(reason_code IN \((.*?)\)\)", _sql(testo), re.S).group(1)
    return set(re.findall(r"'([a-z0-9_]+)'", blocco))


def test_05_un_solo_valore_nuovo_nessun_offset_nel_reason_code():
    su, prima = _valori_check(SU), _valori_check(GIU)
    assert su - prima == {"appointment_reminder"}
    assert prima - su == set()
    assert not any(v.startswith("appointment_reminder_") for v in su)


def test_06_l_enum_e_il_check_della_079_coincidono():
    from communication import enums
    assert enums.REASON_APPOINTMENT_REMINDER == "appointment_reminder"
    assert set(enums.REASON_CODES) == _valori_check(SU)


def test_07_il_down_rifiuta_con_righe_e_cancella_la_sua_riga():
    giu = _sql(GIU)
    assert giu.strip().startswith("BEGIN;") and giu.strip().endswith("COMMIT;")
    assert "WHERE reason_code = 'appointment_reminder'" in giu
    assert "RAISE EXCEPTION" in giu
    assert f"DELETE FROM schema_migrations WHERE version = '{VERSIONE}'" in giu
    assert "DELETE FROM communication_messages" not in giu


def test_08_la_079_e_valida_per_il_runner_e_l_ultima():
    from scripts import p26_migrate as runner
    trovate = {m.version: m for m in runner.discover_migrations()}
    m = trovate[VERSIONE]
    assert runner.validate_migration(m) == [] and m.down_available and not m.non_transactional
    numeri = sorted(x.number for x in trovate.values())
    assert numeri[-1] == 79 and numeri[-2] == 78


def test_09_lo_storico_contatto_ha_l_etichetta_del_motivo():
    from communication import contact_view, enums
    assert contact_view.ETICHETTE_MOTIVO["appointment_reminder"] == "Promemoria appuntamento"
    assert set(contact_view.ETICHETTE_MOTIVO) == set(enums.REASON_CODES)


def test_10_fuori_scope_intatto():
    """A32-1 non tocca origini di sistema, dispatcher, integrations, router."""
    from operator_auth import context
    assert "appointment_reminders" not in context.SYSTEM_CONTEXT_ORIGINS
    for relativo in ("communication/dispatcher.py", "communication/integrations.py",
                     "communication/router.py", "main.py", "run_communication_dispatch_cron.py"):
        assert "appointment_reminder" not in (ROOT / relativo).read_text(encoding="utf-8"), relativo
