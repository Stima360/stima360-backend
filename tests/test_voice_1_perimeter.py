"""STIMA Voice - il perimetro (Fasi 1-4).

  * Fasi 1-2: funzioni pure e le sole letture del risolutore;
  * Fase 3: SOLO ledger.py scrive, e solo sulle tabelle voice_*;
  * Fase 4: l'esecutore (executor.py) non importa nulla del CRM; gli
    adattatori (operations.py, undo.py) chiamano i servizi ESISTENTI e non
    scrivono SQL sulle tabelle del CRM; reconcile.py e undo.py scrivono solo
    sulle tabelle voice_*. Nessuna rete, nessuna rotta montata, nessun modulo
    CRM toccato, nessun fornitore AI reale.

SENTINELLA AGGIORNATA DA STIMA VOICE FASE 3: ammessi ledger.py e settings.py
e la sola migrazione 098_voice_commands (con il suo _down).
SENTINELLA AGGIORNATA DA STIMA VOICE FASE 4: ammessi executor.py,
operations.py, reconcile.py, undo.py e la migrazione 099_voice_undo (con il suo
_down); l'elenco degli import ora registra anche il sottomodulo di
`from X import Y`, cosi' `from core import service` non si nasconde dietro
`core`.
SENTINELLA AGGIORNATA DA STIMA VOICE FASE 5: ammessi provider.py (l'UNICO
modulo con la rete: `requests`), service.py (orchestrazione, solo letture
SQL) e router.py (l'UNICO con FastAPI e la sessione), NON montato in main.py;
operations.py legge anche il catalogo dei comuni del CRM."""
from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VOICE = ROOT / "voice"

FILE_FASE_1 = {"__init__.py", "schemas.py", "planner.py", "dates.py", "policy.py", "fake_provider.py"}
FILE_FASE_2 = {"repository.py", "resolver.py"}
FILE_FASE_3 = {"ledger.py", "settings.py"}
FILE_FASE_4 = {"executor.py", "operations.py", "reconcile.py", "undo.py"}
FILE_FASE_5 = {"provider.py", "service.py", "router.py"}
#: Le uniche tabelle su cui STIMA Voice scrive SQL.
TABELLE_VOICE = {"voice_commands", "voice_command_steps", "voice_command_events", "voice_step_undos"}
#: Chi scrive SQL, e dove.
SCRITTORI = {"ledger.py": {"voice_commands", "voice_command_steps", "voice_command_events"},
             "reconcile.py": {"voice_command_steps"},
             "undo.py": {"voice_step_undos"}}

#: Gli import del CRM (radici sotto) ammessi PER FILE. Tutto il resto e'
#: vietato. Un nome e' ammesso se coincide con una voce o ne e' un figlio.
RADICI_CRM = {"core", "property", "appointments", "crm", "acquisitions", "operator_auth", "psycopg2", "database",
              "main", "buy", "match", "owner", "flow", "communication", "public_submissions"}
PURI = {"appointments.enums", "core.enums", "core.normalization", "core.schemas", "property.enums",
        "property.schemas", "property.interactions.INTERACTION_TYPES"}
AMMESSI = {
    "__init__.py": set(), "fake_provider.py": set(), "policy.py": set(), "resolver.py": set(),
    "executor.py": set(), "provider.py": set(),
    "service.py": {"psycopg2"},
    "router.py": {"core.exceptions", "operator_auth.context", "operator_auth.dependencies"},
    "schemas.py": PURI, "planner.py": PURI, "dates.py": PURI,
    "repository.py": {"core.database", "core.scope", "core.normalization", "core.building_trash",
                      "core.contact_trash", "core.property_trash", "property.census", "appointments.repository",
                      # SENTINELLA AGGIORNATA DA STIMA VOICE V8-2: l'incarico di vendita si legge con le
                      # stesse funzioni dell'Agenda (definizione canonica e stati), mai con una regola propria
                      "appointments.enums", "core.property_mandate"},
    "settings.py": {"core.database"},
    "ledger.py": {"core.database", "psycopg2"},
    "reconcile.py": {"core.database", "core.normalization", "psycopg2.extras"},
    "operations.py": {"appointments.repository", "appointments.service", "appointments.schemas", "core.database",
                      "core.repository", "core.service", "core.exceptions", "core.normalization", "core.schemas",
                      "core.scope", "crm.sellers", "crm.schemas", "property.census", "property.interactions",
                      "property.service", "property.lifecycle", "property.schemas", "property.catalog"},
    "undo.py": {"appointments.service", "appointments.schemas", "core.service", "core.database", "core.schemas",
                "crm.sellers", "crm.schemas", "property.building_lifecycle", "property.census", "property.service",
                "property.schemas", "psycopg2"},
}
VIETATI_OVUNQUE = ("requests", "httpx", "openai", "anthropic", "boto3", "fastapi", "main", "database",
                   "acquisitions", "operator_auth.dependencies")
#: Le sole eccezioni, per file: la rete solo nel fornitore, FastAPI e la
#: sessione solo nel router.
ECCEZIONI = {"provider.py": {"requests"}, "router.py": {"fastapi", "operator_auth.dependencies"}}


def _import_names(path: Path) -> set[str]:
    albero = ast.parse(path.read_text(encoding="utf-8"))
    nomi = set()
    for nodo in ast.walk(albero):
        if isinstance(nodo, ast.Import):
            nomi |= {a.name for a in nodo.names}
        elif isinstance(nodo, ast.ImportFrom) and nodo.module and nodo.level == 0:
            nomi |= {f"{nodo.module}.{a.name}" for a in nodo.names}
    return nomi


def _ammesso(nome: str, ammessi: set[str]) -> bool:
    return any(nome == a or nome.startswith(a + ".") for a in ammessi)


def test_01_only_declared_files_exist():
    assert {p.name for p in VOICE.glob("*.py")} == FILE_FASE_1 | FILE_FASE_2 | FILE_FASE_3 | FILE_FASE_4 | FILE_FASE_5


def test_02_each_file_imports_only_what_it_is_allowed_to():
    for file in VOICE.glob("*.py"):
        for nome in _import_names(file):
            radice = nome.split(".")[0]
            if _ammesso(nome, ECCEZIONI.get(file.name, set())):
                continue
            assert not any(nome == v or nome.startswith(v + ".") for v in VIETATI_OVUNQUE), (file.name, nome)
            assert ".router" not in nome, (file.name, nome)
            if radice in RADICI_CRM:
                assert _ammesso(nome, AMMESSI[file.name]), (file.name, nome)


def test_03_sql_writes_only_on_voice_tables_and_no_http():
    scrittura = re.compile(r"\b(INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|TRUNCATE|ALTER\s+TABLE|CREATE\s+TABLE|DROP\s)", re.I)
    bersaglio = re.compile(r"\b(?:INSERT\s+INTO\s+(\w+)|UPDATE\s+(\w+)\s+SET)", re.I)
    for file in VOICE.glob("*.py"):
        testo = file.read_text(encoding="utf-8")
        if file.name != "provider.py":                 # l'unico confine di rete
            for vietato in ("http://", "https://", "fetch("):
                assert vietato not in testo.lower(), (file.name, vietato)
        assert not re.search(r"\b(DELETE\s+FROM|TRUNCATE|ALTER\s+TABLE|CREATE\s+TABLE|DROP\s)", testo, re.I), file.name
        assert ".commit()" not in testo, file.name
        if file.name in SCRITTORI:
            tabelle = {a or b for a, b in bersaglio.findall(testo)}
            assert tabelle and tabelle <= SCRITTORI[file.name], (file.name, tabelle)
            continue
        assert not scrittura.search(testo), file.name
        assert "commit=True" not in testo, file.name
    for file in VOICE.glob("*.py"):
        if file.name not in ("repository.py", "ledger.py", "settings.py", "operations.py", "reconcile.py", "undo.py",
                             "service.py"):
            assert "cur.execute" not in file.read_text(encoding="utf-8"), file.name
    # gli adattatori leggono soltanto: ogni loro statement SQL e' una SELECT
    operazioni = (VOICE / "operations.py").read_text(encoding="utf-8")
    assert not scrittura.search(operazioni)
    impostazioni = (VOICE / "settings.py").read_text(encoding="utf-8")
    assert "SET TRANSACTION READ ONLY" in impostazioni and "voice_agency_settings" in impostazioni


FASE6_UI = ("static/os_shell/assets/main.js", "static/os_shell/assets/app.css",
            "static/os_shell/assets/views/voce.js", "static/os_shell/assets/voice/voice-api.js",
            "static/os_shell/assets/voice/voice-model.js", "static/os_shell/assets/voice/voice-speech.js")


def test_04_main_and_crm_modules_untouched():
    """Nessun file del CRM modificato o aggiunto fuori da voice/ e dai test di
    STIMA Voice (lo stato git del working tree, come le sentinelle P27/P29)."""
    esito = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all", "--",
                            "main.py", "core/", "property/", "appointments/", "crm/", "acquisitions/",
                            "operator_auth/", "migrations/", "static/", "public_submissions.py"],
                           cwd=ROOT, capture_output=True, text=True)
    dichiarati = ("migrations/098_voice_commands.sql", "migrations/098_voice_commands_down.sql",
                  "migrations/099_voice_undo.sql", "migrations/099_voice_undo_down.sql")
    # Fase 6: l'interfaccia di STIMA Voice nella shell (nascosta finche' /api/voice/status non dice si')
    dichiarati += FASE6_UI
    # SENTINELLA AGGIORNATA DA STIMA VOICE V8 (dichiarata in tests/voice_v8_diff.py): il microfono nel
    # dialog «Nuovo appuntamento» e l'interfaccia della Voce. Nessun altro file del CRM.
    from tests.voice_v8_diff import FILE_MODIFICATI as MOD_V8, FILE_NUOVI as NUOVI_V8
    dichiarati += tuple(MOD_V8) + tuple(NUOVI_V8)
    righe = [r for r in esito.stdout.splitlines() if r[3:] not in dichiarati]
    assert righe == [], esito.stdout


def test_05_only_the_voice_migrations_and_no_route():
    # SENTINELLA AGGIORNATA DA STIMA VOICE V9: la 111 allarga SOLO l'elenco degli intenti del registro
    assert sorted(p.name for p in (ROOT / "migrations").glob("*voice*")) == [
        "098_voice_commands.sql", "098_voice_commands_down.sql", "099_voice_undo.sql", "099_voice_undo_down.sql",
        "111_voice_mandate_intents.sql", "111_voice_mandate_intents_down.sql"]
    testo = (ROOT / "main.py").read_text(encoding="utf-8")
    # SENTINELLA AGGIORNATA DA STIMA VOICE FASE 7: il router e' montato UNA volta, come gli altri
    # domini (mount `require_authenticated_operator`, scope da `require_operator` su ogni rotta), e
    # `main.py` non contiene altro di Voice: nessuna logica, nessuna variabile, nessun interruttore.
    righe = [r.strip() for r in testo.splitlines() if "voice" in r.lower() and not r.strip().startswith("#")]
    assert righe == ["from voice.router import router as voice_router",
                     "app.include_router(voice_router, dependencies=[Depends(require_authenticated_operator)])"], righe


def test_05b_the_ai_provider_is_the_only_network_boundary_and_is_off_by_default():
    """Fase 5: solo provider.py parla in rete; senza configurazione non c'e'
    fornitore e senza STIMA_VOICE_ENABLED=1 il servizio e' spento."""
    import pytest
    from voice import provider, service
    with pytest.raises(provider.ProviderNotConfigured):
        provider.ProviderConfig.from_env({})
    assert service.globally_enabled({}) is False
    testo = (VOICE / "provider.py").read_text(encoding="utf-8")
    assert "timeout=" in testo and "verify=False" not in testo


def test_06_census_helpers_reused_keep_their_signature():
    """Il risolutore richiama tre funzioni private del censimento, senza
    modificarle: se la firma cambia, questo test lo dice prima che la
    risoluzione si rompa in esercizio."""
    import inspect
    from property import census
    for nome in ("_simili_edificio", "_simili_unita", "_duplicato_catastale"):
        firma = list(inspect.signature(getattr(census, nome)).parameters)
        assert firma == ["cur", "agency_id", "data", "escluso"], (nome, firma)


def test_07_crm_functions_reused_by_the_executor_keep_their_signature():
    """Fase 4: le funzioni del CRM che gli adattatori richiamano, con la firma
    su cui contano. Una firma cambiata va vista qui, non in esercizio."""
    import inspect
    from appointments import service as agenda
    from core import repository as core_repository
    from core import service as core_service
    from crm import sellers
    from property import building_lifecycle, census, interactions
    from property import service as property_service
    attese = {
        (core_service, "create_contact"): ["ctx", "payload"],
        (core_service, "trash_contact"): ["ctx", "contact_id", "payload"],
        (core_service, "mark_activity_mistake"): ["ctx", "activity_id", "payload"],
        (core_service, "mark_task_mistake"): ["ctx", "task_id", "payload"],
        (core_repository, "create_activity_with_cursor"): ["cur", "data", "ctx"],
        (core_repository, "create_task_with_cursor"): ["cur", "data", "ctx", "created_by_user_id"],
        (census, "create_unit"): ["ctx", "body"],
        (census, "create_building"): ["ctx", "body"],
        (census, "undo_create"): ["ctx", "property_id"],
        (interactions, "create_interaction"): ["ctx", "property_id", "body"],
        (property_service, "add_contact"): ["ctx", "i", "p"],
        (property_service, "trash_property"): ["ctx", "i", "p"],
        (building_lifecycle, "trash_building"): ["ctx", "building_id", "reason_code", "note"],
        (sellers, "activate"): ["ctx", "body"],
        (sellers, "deactivate"): ["ctx", "body"],
        (agenda, "create_appointment_idempotent"): ["ctx", "payload"],
        (agenda, "cancel_appointment"): ["ctx", "appointment_id", "body"],
        (agenda, "find_conflicts"): ["ctx", "assigned_user_id", "start_at", "end_at", "buffer_before_minutes",
                                     "buffer_after_minutes", "exclude_appointment_id"],
    }
    for (modulo, nome), parametri in attese.items():
        assert list(inspect.signature(getattr(modulo, nome)).parameters) == parametri, (modulo.__name__, nome)
