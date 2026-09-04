"""P25.2 - Seller Lead workflow + property_leads in the OS Shell.

Static/contract checks (same approach as test_os_shell_p25_1_tasks_activities.py):
text-level assertions on the JS source plus real `node --check` syntax
validation. No JS test runner introduced.

Backend contracts these checks are pinned against (verified in core/router.py,
core/schemas.py, core/enums.py, property/router.py, property/schemas.py,
property/enums.py before writing any JS):
  POST   /api/core/leads                              (LeadCreate)
  PATCH  /api/core/leads/{id}                          (LeadUpdate; closed_at
         is server-derived from status - never sent by the client)
  GET    /api/property/properties?lead_id={id}&limit=  (properties linked to a lead)
  GET    /api/property/properties?search=&limit=       (property search picker)
  POST   /api/property/properties/{pid}/leads          (PropertyLeadCreate)
  DELETE /api/property/properties/{pid}/leads/{lid}
No DELETE /api/core/leads/{id} endpoint exists - lead deletion must never be
offered by this UI.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIEWS = ROOT / "static" / "os_shell" / "assets" / "views"
CORE_ENUMS = ROOT / "core" / "enums.py"
PROPERTY_ENUMS = ROOT / "property" / "enums.py"

CONTATTO_JS = VIEWS / "contatto-dettaglio.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _real_enum_set(name: str, path: Path) -> set[str]:
    text = _read(path)
    match = re.search(rf"{name}\s*=\s*\{{([^}}]*)\}}", text)
    assert match, f"{name} non trovato in {path}"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def test_lead_tab_registered_in_tabs():
    text = _read(CONTATTO_JS)
    match = re.search(r"const TABS = \[(.*?)\];", text, re.DOTALL)
    assert match, "TABS non trovato"
    assert "key: 'lead'" in match.group(1), "tab 'lead' non registrata in TABS"


def test_lead_tab_wired_in_show_tab_switch():
    text = _read(CONTATTO_JS)
    match = re.search(r"case 'lead':(.*?)break;", text, re.DOTALL)
    assert match, "case 'lead' non trovato nello switch di showTab"
    assert "renderLeadTab" in match.group(1)
    assert "bindLeadTabActions" in match.group(1)


def test_lead_create_edit_functions_exist():
    text = _read(CONTATTO_JS)
    for fn in ("renderLeadTab", "bindLeadTabActions", "openNewLeadDialog", "openEditLeadDialog", "openLeadPropertiesDialog", "reloadLeads"):
        assert re.search(rf"function {fn}\(", text), f"{fn}() non trovata in contatto-dettaglio.js"


def test_lead_dialogs_declared_in_container_template():
    text = _read(CONTATTO_JS)
    for dialog_id in ("lead-new-dialog", "lead-edit-dialog", "lead-properties-dialog"):
        assert f'id="{dialog_id}"' in text, f"<dialog id=\"{dialog_id}\"> mancante"


def test_lead_workflow_uses_exact_backend_endpoints():
    text = _read(CONTATTO_JS)
    assert "apiPost('/api/core/leads'" in text
    assert re.search(r"apiPatch\(`/api/core/leads/\$\{lead\.id\}`", text)
    assert re.search(r"apiGet\(`/api/property/properties\?lead_id=\$\{lead\.id\}&limit=50`\)", text)
    assert re.search(r"apiGet\(`/api/property/properties\?search=\$\{encodeURIComponent\(term\)\}&limit=10`\)", text)
    assert re.search(r"apiPost\(`/api/property/properties/\$\{pickedProperty\.id\}/leads`", text)
    assert re.search(r"apiDelete\(`/api/property/properties/\$\{propertyId\}/leads/\$\{lead\.id\}`\)", text)


def test_no_lead_delete_endpoint_used():
    """Nessun endpoint DELETE /api/core/leads/{id} esiste nel backend
    (verificato in core/router.py): questa UI non deve offrire la
    cancellazione di un lead."""
    text = _read(CONTATTO_JS)
    assert not re.search(r"apiDelete\(`/api/core/leads/", text)


def test_lead_edit_never_sends_closed_at_manually():
    """core/service.py::update_lead deriva closed_at automaticamente da
    status lato server - il client non deve mai inviarlo esplicitamente
    (stesso principio già applicato a completed_at per i task in P25.1)."""
    text = _read(CONTATTO_JS)
    match = re.search(r"await apiPatch\(`/api/core/leads/\$\{lead\.id\}`, \{(.*?)\}\);", text, re.DOTALL)
    assert match, "payload PATCH lead non trovato"
    assert "closed_at" not in match.group(1)


def test_lead_edit_does_not_invent_lost_reason_requirement():
    """Nessuna validazione lato frontend deve rendere lost_reason
    obbligatorio quando stage='lost' o status='closed': il backend non lo
    richiede (LeadUpdate.lost_reason è opzionale, nessun root_validator lo
    impone)."""
    text = _read(CONTATTO_JS)
    lead_dialog_section = text[text.index("function openEditLeadDialog"):text.index("function openLeadPropertiesDialog")]
    assert "required" not in re.sub(r"//.*", "", lead_dialog_section).replace('name="lost_reason"', ''), (
        "il campo lost_reason (o altri campi del dialog Modifica lead) non deve essere reso 'required' lato frontend"
    )


def test_lead_pipeline_status_priority_options_match_real_backend_enums():
    text = _read(CONTATTO_JS)
    pipeline_match = re.search(r"LEAD_PIPELINE_LABELS = \{([^}]*)\}", text)
    status_match = re.search(r"LEAD_STATUS_LABELS = \{([^}]*)\}", text)
    priority_match = re.search(r"LEAD_PRIORITY_LABELS = \{([^}]*)\}", text)
    assert pipeline_match and status_match and priority_match
    used_pipelines = set(re.findall(r"(\w+):", pipeline_match.group(1)))
    used_statuses = set(re.findall(r"(\w+):", status_match.group(1)))
    used_priorities = set(re.findall(r"(\w+):", priority_match.group(1)))
    assert used_pipelines == _real_enum_set("LEAD_PIPELINES", CORE_ENUMS)
    assert used_statuses == _real_enum_set("LEAD_STATUSES", CORE_ENUMS)
    assert used_priorities == _real_enum_set("PRIORITIES", CORE_ENUMS)


def test_lead_stage_options_are_a_subset_of_real_backend_enum():
    """SELLER_STAGE_LABELS è già usato altrove nel file (Panoramica/Timeline)
    e viene riusato anche per il dialog Modifica lead - non duplicato."""
    text = _read(CONTATTO_JS)
    match = re.search(r"SELLER_STAGE_LABELS = \{([^}]*)\}", text, re.DOTALL)
    assert match
    used = set(re.findall(r"(\w+):", match.group(1)))
    assert used == _real_enum_set("LEAD_STAGES", CORE_ENUMS)


def test_property_lead_relation_options_match_real_backend_enum():
    text = _read(CONTATTO_JS)
    match = re.search(r"PROPERTY_LEAD_RELATION_LABELS = \{([^}]*)\}", text, re.DOTALL)
    assert match, "PROPERTY_LEAD_RELATION_LABELS non trovato"
    used = set(re.findall(r"(\w+):", match.group(1)))
    assert used == _real_enum_set("PROPERTY_LEAD_RELATIONS", PROPERTY_ENUMS)


def test_lead_properties_dialog_uses_two_step_inline_confirm_not_window_confirm():
    code_only = "\n".join(line.split("//", 1)[0] for line in _read(CONTATTO_JS).splitlines())
    assert "window.confirm(" not in code_only
    assert "window.prompt(" not in code_only
    assert "unlinkConfirm" in code_only
    assert "data-unlink=" in code_only


def test_lead_properties_dialog_links_to_property_detail_view():
    """CTA verso l'immobile collegato deve puntare alla scheda immobile reale
    (route #/immobili/{id}), non a un link morto."""
    text = _read(CONTATTO_JS)
    assert re.search(r"#/immobili/\$\{escapeHtml\(p\.id\)\}", text)


def test_contatto_dettaglio_js_is_syntactically_valid():
    result = subprocess.run(["node", "--check", str(CONTATTO_JS)], capture_output=True, text=True)
    assert result.returncode == 0, f"{CONTATTO_JS.name}: {result.stderr.strip()}"
