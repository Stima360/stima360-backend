"""STIMA Voice Fase 1 - il perimetro: funzioni pure, nessun database,
nessuna rete, nessun servizio del CRM toccato, nessuna rotta montata."""
from __future__ import annotations

import ast
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VOICE = ROOT / "voice"

FILE_FASE_1 = {"__init__.py", "schemas.py", "planner.py", "dates.py", "policy.py", "fake_provider.py"}
#: Cio' che la Fase 1 puo' importare dal CRM: enum, schemi e normalizzazione.
IMPORT_AMMESSI = {
    "appointments.enums", "core.enums", "core.normalization", "core.schemas",
    "property.enums", "property.schemas", "property.interactions",
}
VIETATI = ("psycopg2", "requests", "httpx", "openai", "boto3", "fastapi", "database", "core.database",
           "core.repository", "core.service", "property.service", "property.census", "property.repository",
           "appointments.service", "appointments.repository", "crm.sellers", "acquisitions")


def _import_names(path: Path) -> set[str]:
    albero = ast.parse(path.read_text(encoding="utf-8"))
    nomi = set()
    for nodo in ast.walk(albero):
        if isinstance(nodo, ast.Import):
            nomi |= {a.name for a in nodo.names}
        elif isinstance(nodo, ast.ImportFrom) and nodo.module and nodo.level == 0:
            nomi.add(nodo.module)
    return nomi


def test_01_only_phase_1_files_exist():
    assert {p.name for p in VOICE.glob("*.py")} == FILE_FASE_1
    assert not (VOICE / "router.py").exists() and not list(VOICE.glob("*provider.py")) == []


def test_02_no_database_no_network_no_crm_services():
    for file in VOICE.glob("*.py"):
        for nome in _import_names(file):
            assert not nome.startswith(VIETATI), (file.name, nome)
            if "." in nome and not nome.startswith("voice"):
                assert nome in IMPORT_AMMESSI, (file.name, nome)


def test_03_no_sql_no_http_in_voice():
    for file in VOICE.glob("*.py"):
        testo = file.read_text(encoding="utf-8").lower()
        for vietato in ("select ", "insert ", "update ", "cursor(", "fetch(", "http://", "https://"):
            assert vietato not in testo, (file.name, vietato)


def test_04_main_and_crm_modules_untouched():
    """Nessun file del CRM modificato o aggiunto fuori da voice/ e dai test
    della Fase 1 (lo stato git del working tree, come le sentinelle P27/P29)."""
    esito = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all", "--",
                            "main.py", "core/", "property/", "appointments/", "crm/", "acquisitions/",
                            "operator_auth/", "migrations/", "static/", "public_submissions.py"],
                           cwd=ROOT, capture_output=True, text=True)
    assert esito.stdout.strip() == "", esito.stdout


def test_05_no_migration_and_no_route():
    assert not list((ROOT / "migrations").glob("*voice*"))
    assert "voice" not in (ROOT / "main.py").read_text(encoding="utf-8")
