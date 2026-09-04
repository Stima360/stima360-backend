"""P25.1 - Task/Activity operational in the OS Shell.

Static/contract checks (same approach as test_os_shell_p25_shell_contracts.py):
text-level assertions on the JS source plus real `node --check` syntax
validation. No JS test runner introduced.

Backend contracts these checks are pinned against (verified in core/router.py,
core/schemas.py, core/enums.py before writing any JS):
  POST   /api/core/activities         (ActivityCreate)
  DELETE /api/core/activities/{id}    (hard delete, no ActivityUpdate exists)
  POST   /api/core/tasks              (TaskCreate)
  PATCH  /api/core/tasks/{id}         (TaskUpdate)
  DELETE /api/core/tasks/{id}         (hard delete)
  GET    /api/core/leads?contact_id=  (used only when no preset lead list)
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIEWS = ROOT / "static" / "os_shell" / "assets" / "views"
COMPONENTS = ROOT / "static" / "os_shell" / "assets" / "components"
CORE_ENUMS = ROOT / "core" / "enums.py"

ATTIVITA_JS = VIEWS / "attivita.js"
CONTATTO_JS = VIEWS / "contatto-dettaglio.js"
DIALOGS_JS = COMPONENTS / "activity-task-dialogs.js"
PICKER_JS = COMPONENTS / "contact-picker.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _strip_js_line_comments(text: str) -> str:
    """Same self-inflicted-false-positive fix already applied elsewhere in
    this suite (see test_database_revival_isolation.py::
    _strip_sql_line_comments): a raw substring check on 'window.confirm('
    would false-positive on this file's OWN prose comments explaining that
    window.confirm() is deliberately NOT used. Strip '//' line comments
    before checking for an actual call."""
    return "\n".join(line.split("//", 1)[0] for line in text.splitlines())


def _real_enum_set(name: str) -> set[str]:
    enums_text = _read(CORE_ENUMS)
    match = re.search(rf"{name}\s*=\s*\{{([^}}]*)\}}", enums_text)
    assert match, f"{name} non trovato in core/enums.py"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def test_new_component_files_exist():
    assert DIALOGS_JS.exists(), "components/activity-task-dialogs.js mancante"
    assert PICKER_JS.exists(), "components/contact-picker.js mancante"


def test_dialogs_component_exports_expected_functions():
    text = _read(DIALOGS_JS)
    for fn in (
        "openNewActivityDialog", "deleteActivity",
        "openNewTaskDialog", "openEditTaskDialog", "deleteTask",
    ):
        assert f"export function {fn}(" in text or f"export async function {fn}(" in text, (
            f"activity-task-dialogs.js non esporta {fn}()"
        )
    # NON deve esistere una modifica attività: nessun endpoint PATCH
    # activities esiste nel backend (verificato in core/router.py).
    assert "openEditActivityDialog" not in text, (
        "modifica attività non deve esistere: il backend non espone alcun PATCH /api/core/activities/{id}"
    )


def test_picker_component_exports_expected_function():
    text = _read(PICKER_JS)
    assert "export function createContactPicker(" in text


def test_dialogs_use_exact_backend_endpoints():
    text = _read(DIALOGS_JS)
    assert "apiPost('/api/core/activities'" in text
    assert re.search(r"apiDelete\(`/api/core/activities/\$\{.*?\}`\)", text)
    assert "apiPost('/api/core/tasks'" in text
    assert re.search(r"apiPatch\(`/api/core/tasks/\$\{.*?\}`", text)
    assert re.search(r"apiDelete\(`/api/core/tasks/\$\{.*?\}`\)", text)
    assert re.search(r"apiGet\(`/api/core/leads\?contact_id=\$\{.*?\}&limit=50`\)", text)


def test_activity_type_options_are_a_subset_of_real_backend_enum():
    real = _real_enum_set("ACTIVITY_TYPES")
    text = _read(DIALOGS_JS)
    match = re.search(r"ACTIVITY_TYPE_LABELS\s*=\s*\{([^}]*)\}", text)
    assert match, "ACTIVITY_TYPE_LABELS non trovato"
    used = set(re.findall(r"(\w+):", match.group(1)))
    assert used, "nessun activity_type trovato nel dialog"
    assert used.issubset(real), f"activity_type non reali usati nel dialog: {used - real}"


def test_activity_direction_options_match_real_backend_enum():
    real = _real_enum_set("ACTIVITY_DIRECTIONS")
    text = _read(DIALOGS_JS)
    match = re.search(r"ACTIVITY_DIRECTION_LABELS\s*=\s*\{([^}]*)\}", text)
    assert match
    used = set(re.findall(r"(\w+):", match.group(1)))
    assert used == real, f"direction non allineate: usate={used} reali={real}"


def test_task_priority_and_status_options_match_real_backend_enums():
    text = _read(DIALOGS_JS)
    priorities_match = re.search(r"TASK_PRIORITY_LABELS\s*=\s*\{([^}]*)\}", text)
    statuses_match = re.search(r"TASK_STATUS_LABELS\s*=\s*\{([^}]*)\}", text)
    assert priorities_match and statuses_match
    used_priorities = set(re.findall(r"(\w+):", priorities_match.group(1)))
    used_statuses = set(re.findall(r"(\w+):", statuses_match.group(1)))
    assert used_priorities == _real_enum_set("PRIORITIES")
    assert used_statuses == _real_enum_set("TASK_STATUSES")


def test_attivita_js_wires_create_edit_delete_task_and_create_delete_activity():
    text = _read(ATTIVITA_JS)
    assert "openNewActivityDialog" in text
    assert "openNewTaskDialog" in text
    assert "openEditTaskDialog" in text
    assert "deleteTask" in text
    assert "deleteActivity" in text
    # La sola azione preesistente (completamento task) deve restare intatta.
    assert "status: 'completed'" in text


def test_attivita_js_still_uses_all_original_read_endpoints():
    """Regressione: le 4 GET originali (task/attività/visite, P0-era fix
    paginazione) devono restare invariate."""
    text = _read(ATTIVITA_JS)
    assert "fetchAllPages('/api/core/tasks'" in text
    assert "fetchAllPages('/api/property/visits'" in text
    assert "fetchAllPages('/api/core/activities'" in text
    assert "MAX_PAGES = 25" in text


def test_attivita_js_has_no_more_window_alert_than_baseline():
    """Baseline (pre-P25.1): esattamente 1 window.alert() nel file (il
    completamento task, deliberatamente NON toccato qui - la sua rimozione è
    P25.7). Questo test impedisce che il nuovo codice P25.1 introduca altri
    alert() (vietato dal brief: 'NON usare... nuovi alert() se evitabili')."""
    text = _read(ATTIVITA_JS)
    assert len(re.findall(r"window\.alert\(", text)) == 1


def test_attivita_js_delete_actions_use_inline_confirm_not_window_confirm():
    code_only = _strip_js_line_comments(_read(ATTIVITA_JS))
    assert "window.confirm(" not in code_only, "window.confirm() non deve essere usato: conferma inline a due click richiesta"
    assert "data-delete-task-confirm" in code_only
    assert "data-delete-activity-confirm" in code_only


def test_contatto_dettaglio_js_is_no_longer_read_only():
    """Fino a P25.1 questo file non conteneva ALCUNA apiPost/apiPatch/
    apiDelete (verificato durante l'audit P25 Fase 1). Ora deve contenere le
    due azioni rapide."""
    text = _read(CONTATTO_JS)
    assert "openNewActivityDialog" in text
    assert "openNewTaskDialog" in text
    assert "presetContact: { id: contact.id" in text.replace("\n", " ") or "presetContact:" in text


def test_all_touched_js_files_are_syntactically_valid():
    for path in (ATTIVITA_JS, CONTATTO_JS, DIALOGS_JS, PICKER_JS):
        result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
        assert result.returncode == 0, f"{path.name}: {result.stderr.strip()}"
