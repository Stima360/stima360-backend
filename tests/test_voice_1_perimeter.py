"""STIMA Voice - il perimetro (Fasi 1-2): funzioni pure piu' le sole
letture del risolutore. Nessuna scrittura, nessuna rete, nessun servizio del
CRM che scrive, nessuna rotta montata, nessun modulo CRM toccato."""
from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VOICE = ROOT / "voice"

FILE_FASE_1 = {"__init__.py", "schemas.py", "planner.py", "dates.py", "policy.py", "fake_provider.py"}
FILE_FASE_2 = {"repository.py", "resolver.py"}
#: Cio' che il modulo puo' importare dal CRM: enum, schemi, normalizzazione
#: e - SOLO in repository.py - le letture: scope, cursore, predicati del
#: Cestino, avvisi del censimento, agenti dell'Agenda.
IMPORT_PURI = {
    "appointments.enums", "core.enums", "core.normalization", "core.schemas",
    "property.enums", "property.schemas", "property.interactions",
}
IMPORT_LETTURE = {"core.database", "core.scope", "core", "property", "appointments"}
VIETATI = ("psycopg2", "requests", "httpx", "openai", "boto3", "fastapi", "database",
           "core.repository", "core.service", "property.service", "property.repository",
           "appointments.service", "crm.sellers", "acquisitions")


def _import_names(path: Path) -> set[str]:
    albero = ast.parse(path.read_text(encoding="utf-8"))
    nomi = set()
    for nodo in ast.walk(albero):
        if isinstance(nodo, ast.Import):
            nomi |= {a.name for a in nodo.names}
        elif isinstance(nodo, ast.ImportFrom) and nodo.module and nodo.level == 0:
            nomi.add(nodo.module)
    return nomi


def test_01_only_declared_files_exist():
    assert {p.name for p in VOICE.glob("*.py")} == FILE_FASE_1 | FILE_FASE_2
    assert not (VOICE / "router.py").exists() and not (VOICE / "executor.py").exists()


def test_02_only_repository_touches_the_database():
    for file in VOICE.glob("*.py"):
        for nome in _import_names(file):
            assert not nome.startswith(VIETATI), (file.name, nome)
            if nome.startswith("voice") or "." not in nome and nome not in IMPORT_LETTURE:
                continue
            if file.name == "repository.py":
                assert nome in IMPORT_PURI | IMPORT_LETTURE, (file.name, nome)
            else:
                assert nome in IMPORT_PURI, (file.name, nome)


def test_03_no_write_sql_and_no_http_anywhere():
    scrittura = re.compile(r"\b(INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|TRUNCATE|ALTER\s+TABLE|CREATE\s+TABLE|DROP\s)", re.I)
    for file in VOICE.glob("*.py"):
        testo = file.read_text(encoding="utf-8")
        assert not scrittura.search(testo), file.name
        assert "commit=True" not in testo and ".commit()" not in testo, file.name
        for vietato in ("http://", "https://", "fetch("):
            assert vietato not in testo.lower(), (file.name, vietato)
    for file in VOICE.glob("*.py"):
        if file.name != "repository.py":
            assert "cur.execute" not in file.read_text(encoding="utf-8"), file.name


def test_04_main_and_crm_modules_untouched():
    """Nessun file del CRM modificato o aggiunto fuori da voice/ e dai test di
    STIMA Voice (lo stato git del working tree, come le sentinelle P27/P29)."""
    esito = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all", "--",
                            "main.py", "core/", "property/", "appointments/", "crm/", "acquisitions/",
                            "operator_auth/", "migrations/", "static/", "public_submissions.py"],
                           cwd=ROOT, capture_output=True, text=True)
    assert esito.stdout.strip() == "", esito.stdout


def test_05_no_migration_and_no_route():
    assert not list((ROOT / "migrations").glob("*voice*"))
    assert "voice" not in (ROOT / "main.py").read_text(encoding="utf-8")


def test_06_census_helpers_reused_keep_their_signature():
    """Il risolutore richiama tre funzioni private del censimento, senza
    modificarle: se la firma cambia, questo test lo dice prima che la
    risoluzione si rompa in esercizio."""
    import inspect
    from property import census
    for nome in ("_simili_edificio", "_simili_unita", "_duplicato_catastale"):
        firma = list(inspect.signature(getattr(census, nome)).parameters)
        assert firma == ["cur", "agency_id", "data", "escluso"], (nome, firma)
