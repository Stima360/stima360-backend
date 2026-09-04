"""P25.5 - Buyer workflow completion in the OS Shell.

Static/contract checks (same approach as the other P25 sub-project test
files): text-level assertions on the JS source plus real `node --check`
syntax validation. No JS test runner introduced.

Backend contracts these checks are pinned against (verified in
buy/schemas.py, buy/service.py, buy/router.py, buy/enums.py, match/router.py
before writing any JS):
  PATCH  /api/buy/requests/{id}                          (BuyRequestUpdate;
         buy/service.py::update_request uses dump(p,True)=exclude_unset)
  POST   /api/buy/requests/{id}/locations                (LocationCreate)
  DELETE /api/buy/locations/{location_id}
  POST   /api/buy/requests/{id}/typologies               (TypologyCreate)
  DELETE /api/buy/typologies/{typology_id}
  POST   /api/buy/requests/{id}/features                 (FeatureCreate)
  DELETE /api/buy/features/{feature_id}
  POST   /api/buy/requests/{id}/matches/{match_id}/decision  (MatchDecision;
         action must be in INTERACTION_TYPES - {'other'}; writes to
         buy_request_interactions via service.py::match_decision)
  POST   /api/match/buy-requests/{id}/calculate           (BatchMatchRequest)
No DELETE /api/buy/requests/{id} call is expected here (archive_request
exists but no dedicated archive workflow is invented, same principle as
P25.3/P25.4).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIEWS = ROOT / "static" / "os_shell" / "assets" / "views"
BUY_ENUMS = ROOT / "buy" / "enums.py"
BUY_SCHEMAS = ROOT / "buy" / "schemas.py"

ACQUIRENTE_JS = VIEWS / "acquirente-dettaglio.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _strip_js_line_comments(text: str) -> str:
    return "\n".join(line.split("//", 1)[0] for line in text.splitlines())


def _real_enum_set(name: str, path: Path) -> set[str]:
    text = _read(path)
    match = re.search(rf"{name}\s*=\s*\{{([^}}]*)\}}", text)
    assert match, f"{name} non trovato in {path}"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def test_edit_request_and_helper_functions_exist():
    text = _read(ACQUIRENTE_JS)
    for fn in (
        "openEditRequestDialog", "reloadRequest",
        "bindAbbinamentiSection", "openMatchDecisionDialog",
        "bindCriteriSection", "reloadCriteria",
    ):
        assert re.search(rf"function {fn}\(", text), f"{fn}() non trovata"


def test_request_edit_dialog_declared_in_container_template():
    text = _read(ACQUIRENTE_JS)
    assert 'id="request-edit-dialog"' in text
    assert 'id="request-edit-btn"' in text
    assert 'id="match-decision-dialog"' in text


def test_request_edit_uses_exact_patch_endpoint_and_only_changed_fields():
    text = _read(ACQUIRENTE_JS)
    assert re.search(r"apiPatch\(`/api/buy/requests/\$\{requestId\}`, payload\)", text)
    start = text.index("function openEditRequestDialog")
    end = text.index("const tabsEl = container.querySelector('#request-tabs')")
    dialog_fn = text[start:end]
    assert "const payload = {};" in dialog_fn
    assert "if (!Object.keys(payload).length)" in dialog_fn


def test_no_buy_request_delete_endpoint_used():
    """archive_request (DELETE /api/buy/requests/{id}) esiste nel backend ma
    nessuna archiviazione dedicata viene inventata in questa UI (stesso
    principio di P25.3/P25.4)."""
    text = _read(ACQUIRENTE_JS)
    assert not re.search(r"apiDelete\(`/api/buy/requests/\$\{requestId\}`\)", text)


def test_criteria_add_uses_exact_backend_endpoints():
    text = _read(ACQUIRENTE_JS)
    assert re.search(r"apiPost\(`/api/buy/requests/\$\{requestId\}/locations`, payload\)", text)
    assert re.search(r"apiPost\(`/api/buy/requests/\$\{requestId\}/typologies`, \{", text)
    assert re.search(r"apiPost\(`/api/buy/requests/\$\{requestId\}/features`, payload\)", text)


def test_criteria_delete_uses_exact_backend_endpoints_not_nested_under_request():
    """DELETE /locations/{id}, /typologies/{id}, /features/{id} sono montati
    direttamente sotto /api/buy (verificato in buy/router.py), NON sotto
    /api/buy/requests/{id}/... - un URL nidificato sarebbe un endpoint
    inventato che il backend non espone."""
    text = _read(ACQUIRENTE_JS)
    assert "const endpoint = kind === 'location' ? 'locations' : kind === 'typology' ? 'typologies' : 'features';" in text
    assert re.search(r"apiDelete\(`/api/buy/\$\{endpoint\}/\$\{id\}`\)", text)


def test_match_decision_uses_exact_endpoint_and_real_action_enum():
    text = _read(ACQUIRENTE_JS)
    assert re.search(r"apiPost\(`/api/buy/requests/\$\{requestId\}/matches/\$\{match\.id\}/decision`, payload\)", text)
    match = re.search(r"MATCH_DECISION_ACTIONS = Object\.keys\(INTERACTION_TYPE_LABELS\)\.filter\(\(k\) => k !== 'other'\);", text)
    assert match, "MATCH_DECISION_ACTIONS non derivato correttamente da INTERACTION_TYPE_LABELS - {'other'}"
    # buy/schemas.py::MatchDecision.validate_action: action in INTERACTION_TYPES - {'other'}
    interaction_types_match = re.search(r'INTERACTION_TYPES\s*=\s*\{([^}]*)\}', _read(BUY_SCHEMAS))
    assert interaction_types_match
    real_actions = set(re.findall(r'"([^"]+)"', interaction_types_match.group(1))) - {"other"}
    labels_match = re.search(r"const INTERACTION_TYPE_LABELS = \{([^}]*)\}", text)
    assert labels_match
    used = set(re.findall(r"(\w+):", labels_match.group(1))) - {"other"}
    assert used == real_actions, f"azioni MATCH decision non allineate: {used.symmetric_difference(real_actions)}"


def test_match_decision_requires_reason_code_when_discarded_and_scheduled_at_when_visit_scheduled():
    """buy/schemas.py::MatchDecision.validate_action richiede reason_code
    quando action='discarded' e scheduled_at quando action='visit_scheduled'
    - il dialog deve rivelare questi campi condizionalmente."""
    text = _read(ACQUIRENTE_JS)
    start = text.index("function openMatchDecisionDialog")
    end = text.index("// --- P25.5: Criteri")
    dialog_fn = text[start:end]
    assert "reasonField.hidden = actionSelect.value !== 'discarded';" in dialog_fn
    assert "scheduleField.hidden = actionSelect.value !== 'visit_scheduled';" in dialog_fn
    assert "if (action === 'discarded') payload.reason_code" in dialog_fn
    assert "Data e ora visita obbligatorie" in dialog_fn


def test_rejection_reason_labels_match_real_backend_enum():
    text = _read(ACQUIRENTE_JS)
    match = re.search(r"REJECTION_REASON_LABELS = \{([^}]*)\}", text, re.DOTALL)
    assert match, "REJECTION_REASON_LABELS non trovato"
    used = set(re.findall(r"(\w+):", match.group(1)))
    reasons_match = re.search(r'REJECTION_REASONS\s*=\s*\{([^}]*)\}', _read(BUY_SCHEMAS))
    assert reasons_match
    real = set(re.findall(r'"([^"]+)"', reasons_match.group(1)))
    assert used == real


def test_match_recalculate_uses_exact_endpoint_and_is_error_isolated_from_request_save():
    """Il ricalcolo abbinamenti (match/router.py:56-58) deve avere un
    proprio feedback box (#abbinamenti-feedback), mai condiviso con
    l'error-box del dialog "Modifica richiesta" ne' con #proposal-feedback."""
    text = _read(ACQUIRENTE_JS)
    assert re.search(r"apiPost\(`/api/match/buy-requests/\$\{requestId\}/calculate`, \{\}\)", text)
    start = text.index("function bindAbbinamentiSection")
    end = text.index("function openMatchDecisionDialog")
    recalc_fn = text[start:end]
    assert "#abbinamenti-feedback" in recalc_fn
    assert "#proposal-feedback" not in recalc_fn
    assert "#request-edit-dialog" not in recalc_fn


def test_location_typology_feature_value_types_match_real_backend_enums():
    text = _read(ACQUIRENTE_JS)
    location_match = re.search(r"const LOCATION_TYPE_LABELS = \{([^}]*)\}", text)
    requirement_match = re.search(r"const REQUIREMENT_LEVEL_LABELS = \{([^}]*)\}", text)
    feature_value_match = re.search(r"FEATURE_VALUE_TYPE_LABELS = \{([^}]*)\}", text)
    assert location_match and requirement_match and feature_value_match
    assert set(re.findall(r"(\w+):", location_match.group(1))) == _real_enum_set("LOCATION_TYPES", BUY_ENUMS)
    assert set(re.findall(r"(\w+):", requirement_match.group(1))) == _real_enum_set("REQUIREMENT_LEVELS", BUY_ENUMS)
    assert set(re.findall(r"(\w+):", feature_value_match.group(1))) == _real_enum_set("FEATURE_VALUE_TYPES", BUY_ENUMS)


def test_criteria_removal_uses_two_step_inline_confirm_not_window_confirm():
    code_only = _strip_js_line_comments(_read(ACQUIRENTE_JS))
    assert "window.confirm(" not in code_only
    assert "window.prompt(" not in code_only
    assert "criteriaRemoveConfirm" in code_only
    assert "data-criteria-remove=" in code_only


def test_quick_activity_and_task_buttons_reuse_p25_1_shared_dialogs():
    text = _read(ACQUIRENTE_JS)
    assert "import { openNewActivityDialog, openNewTaskDialog } from '../components/activity-task-dialogs.js';" in text
    assert 'id="request-quick-activity"' in text
    assert 'id="request-quick-task"' in text
    assert "presetContact: { id: contactId, label: contactName }" in text


def test_proposal_and_sale_flows_still_present_p9_p11_regression():
    """Regressione P9/P11: le sezioni Proposte e Vendite non devono essere
    toccate da questo sub-progetto."""
    text = _read(ACQUIRENTE_JS)
    assert "function bindProposteSection" in text
    assert "function openSaleCreateDialog" in text
    assert "async function reloadSales" in text
    assert "async function reloadProposals" in text


def test_acquirente_dettaglio_js_is_syntactically_valid():
    result = subprocess.run(["node", "--check", str(ACQUIRENTE_JS)], capture_output=True, text=True)
    assert result.returncode == 0, f"{ACQUIRENTE_JS.name}: {result.stderr.strip()}"
