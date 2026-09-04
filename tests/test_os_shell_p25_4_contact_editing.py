"""P25.4 - Contact editing (ContactUpdate + contact_roles) in the OS Shell.

Static/contract checks (same approach as the other P25 sub-project test
files): text-level assertions on the JS source plus real `node --check`
syntax validation. No JS test runner introduced.

Backend contracts these checks are pinned against (verified in
core/schemas.py, core/service.py, core/router.py, core/enums.py before
writing any JS):
  PATCH  /api/core/contacts/{id}                (ContactUpdate; server uses
         exclude_unset=True - core/service.py::update_contact - so the
         client must only send fields actually changed)
  POST   /api/core/contacts/{id}/roles          (ContactRoleCreate)
  DELETE /api/core/contacts/{id}/roles/{role}   (204)
No DELETE /api/core/contacts/{id} endpoint exists - no archive/delete action
must be offered for the contact itself. ContactUpdate.archived_at and
.marketing_consent_at both exist as fields but are never written manually
from this UI (archived_at: no dedicated archive workflow exists or is
requested; marketing_consent_at: server-derived only on create, not update).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIEWS = ROOT / "static" / "os_shell" / "assets" / "views"
CORE_ENUMS = ROOT / "core" / "enums.py"

CONTATTO_JS = VIEWS / "contatto-dettaglio.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _strip_js_line_comments(text: str) -> str:
    return "\n".join(line.split("//", 1)[0] for line in text.splitlines())


def _real_enum_set(name: str) -> set[str]:
    text = _read(CORE_ENUMS)
    match = re.search(rf"{name}\s*=\s*\{{([^}}]*)\}}", text)
    assert match, f"{name} non trovato in core/enums.py"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def test_edit_contact_functions_exist():
    text = _read(CONTATTO_JS)
    for fn in ("openEditContactDialog", "reloadContactAndRoles", "renderRoleBadges", "bindRoleBadgeActions"):
        assert re.search(rf"function {fn}\(", text), f"{fn}() non trovata"


def test_contact_edit_dialog_declared_in_container_template():
    text = _read(CONTATTO_JS)
    assert 'id="contact-edit-dialog"' in text
    assert 'id="contact-edit-btn"' in text


def test_contact_workflow_uses_exact_backend_endpoints():
    text = _read(CONTATTO_JS)
    assert re.search(r"apiPatch\(`/api/core/contacts/\$\{contact\.id\}`, payload\)", text)
    assert re.search(r"apiPost\(`/api/core/contacts/\$\{contact\.id\}/roles`, \{ role \}\)", text)
    assert re.search(r"apiDelete\(`/api/core/contacts/\$\{contact\.id\}/roles/\$\{role\}`\)", text)


def test_no_contact_delete_endpoint_used():
    """Nessun endpoint DELETE /api/core/contacts/{id} esiste nel backend
    (verificato in core/router.py): questa UI non deve offrire la
    cancellazione/archiviazione dedicata di un contatto."""
    text = _read(CONTATTO_JS)
    assert not re.search(r"apiDelete\(`/api/core/contacts/\$\{contact\.id\}`\)", text)


def test_contact_roles_list_matches_real_backend_enum():
    text = _read(CONTATTO_JS)
    assert "const CONTACT_ROLES_LIST = Object.keys(ROLE_LABELS);" in text
    match = re.search(r"const ROLE_LABELS = \{([^}]*)\}", text, re.DOTALL)
    assert match
    used = set(re.findall(r"(\w+):", match.group(1)))
    assert used == _real_enum_set("CONTACT_ROLES")


def test_contact_status_options_match_real_backend_enum():
    text = _read(CONTATTO_JS)
    match = re.search(r"CONTACT_STATUS_LABELS = \{([^}]*)\}", text)
    assert match, "CONTACT_STATUS_LABELS non trovato"
    used = set(re.findall(r"(\w+):", match.group(1)))
    assert used == _real_enum_set("CONTACT_STATUSES")


def test_edit_payload_never_includes_archived_at_or_marketing_consent_at():
    text = _read(CONTATTO_JS)
    start = text.index("function openEditContactDialog")
    end = text.index("// --- P25.2: Lead SELL")
    dialog_fn = text[start:end]
    assert "archived_at" not in dialog_fn
    assert "marketing_consent_at" not in dialog_fn


def test_edit_payload_only_includes_changed_fields():
    """core/service.py::update_contact usa exclude_unset=True: il client
    deve costruire il payload solo con i campi realmente modificati (stesso
    principio gia' applicato a bindIncaricoSection in immobile-dettaglio.js),
    mai inviare l'intero form indiscriminatamente."""
    text = _read(CONTATTO_JS)
    start = text.index("function openEditContactDialog")
    end = text.index("// --- P25.2: Lead SELL")
    dialog_fn = text[start:end]
    assert "const payload = {};" in dialog_fn
    assert "textField(" in dialog_fn and "selectField(" in dialog_fn
    assert "if (!Object.keys(payload).length)" in dialog_fn


def test_marketing_consent_is_tristate_not_plain_boolean():
    """marketing_consent e' bool|None lato backend (core/schemas.py): il
    dialog deve offrire tre opzioni (Non specificato/Si/No), non un semplice
    checkbox a due stati."""
    text = _read(CONTATTO_JS)
    start = text.index("function openEditContactDialog")
    end = text.index("// --- P25.2: Lead SELL")
    dialog_fn = text[start:end]
    assert 'name="marketing_consent"' in dialog_fn
    assert '<option value=""' in dialog_fn
    assert '<option value="true"' in dialog_fn
    assert '<option value="false"' in dialog_fn
    assert "consentTarget" in dialog_fn


def test_contact_type_toggles_person_vs_company_fields():
    text = _read(CONTATTO_JS)
    assert "contact-edit-person-fields" in text
    assert "contact-edit-company-fields" in text


def test_role_management_uses_two_step_inline_confirm_not_window_confirm():
    code_only = _strip_js_line_comments(_read(CONTATTO_JS))
    assert "window.confirm(" not in code_only
    assert "window.prompt(" not in code_only
    assert "roleRemoveConfirm" in code_only
    assert "data-role=" in code_only


def test_header_title_and_subtitle_are_refreshed_after_edit():
    """Regressione UX: display_name puo' cambiare durante la modifica - il
    titolo header (non solo i tab) deve riflettersi senza reload pagina."""
    text = _read(CONTATTO_JS)
    start = text.index("async function reloadContactAndRoles")
    end = text.index("async function reloadTasksAndActivities")
    reload_fn = text[start:end]
    assert "#contact-header-title" in reload_fn
    assert "#contact-header-subtitle" in reload_fn


def test_quick_activity_and_task_buttons_still_present_p25_1_regression():
    """Regressione P25.1: le azioni rapide Nuova attivita'/Nuovo task devono
    restare presenti e wired dopo l'aggiunta del pulsante Modifica."""
    text = _read(CONTATTO_JS)
    assert "openNewActivityDialog" in text
    assert "openNewTaskDialog" in text
    assert 'id="contact-quick-activity"' in text
    assert 'id="contact-quick-task"' in text


def test_lead_tab_still_present_p25_2_regression():
    text = _read(CONTATTO_JS)
    assert "key: 'lead'" in text
    assert "renderLeadTab" in text


def test_contatto_dettaglio_js_is_syntactically_valid():
    result = subprocess.run(["node", "--check", str(CONTATTO_JS)], capture_output=True, text=True)
    assert result.returncode == 0, f"{CONTATTO_JS.name}: {result.stderr.strip()}"
