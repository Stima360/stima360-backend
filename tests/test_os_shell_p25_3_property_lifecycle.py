"""P25.3 - Property commercial lifecycle control in the OS Shell.

Static/contract checks (same approach as the other P25 sub-project test
files): text-level assertions on the JS source plus real `node --check`
syntax validation. No JS test runner introduced.

Backend contracts these checks are pinned against (verified in
property/schemas.py, property/repository.py, property/router.py before
writing any JS):
  PATCH  /api/property/properties/{id}   (PropertyUpdate.commercial_status;
         validated only against PROPERTY_STATUSES membership, no
         server-side state machine/transition restriction)
  DELETE /api/property/properties/{id}   (archive_property: sets
         commercial_status='archived' AND archived_at - the only path that
         does; a raw PATCH to commercial_status='archived' would NOT set
         archived_at, breaking the archived_at IS NULL invariant relied on
         by KPI/alerts/mandate_expiring queries in property/repository.py)
No dedicated "unsold"/state-machine endpoint exists for commercial_status
besides these two - nothing else is used here.
"""

from __future__ import annotations

import re
import subprocess
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIEWS = ROOT / "static" / "os_shell" / "assets" / "views"
PROPERTY_ENUMS = ROOT / "property" / "enums.py"

IMMOBILE_JS = VIEWS / "immobile-dettaglio.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _strip_js_line_comments(text: str) -> str:
    return "\n".join(line.split("//", 1)[0] for line in text.splitlines())


def _function_block(text: str, name: str) -> str:
    match = re.search(rf"(?:async\s+)?function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{", text)
    assert match, f"{name} non trovata"
    start = match.start()
    brace = match.end() - 1
    depth = 0
    for index in range(brace, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    raise AssertionError(f"{name} non chiusa")


def _real_enum_set(name: str, path: Path) -> set[str]:
    text = _read(path)
    match = re.search(rf"{name}\s*=\s*\{{([^}}]*)\}}", text)
    assert match, f"{name} non trovato in {path}"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def test_commercial_status_functions_exist():
    text = _read(IMMOBILE_JS)
    for fn in ("renderCommercialStatusSection", "bindCommercialStatusSection"):
        assert re.search(rf"function {fn}\(", text), f"{fn}() non trovata"


def test_manual_statuses_are_subset_of_real_backend_enum_and_exclude_sold():
    text = _read(IMMOBILE_JS)
    match = re.search(r"MANUAL_COMMERCIAL_STATUSES = \[(.*?)\];", text, re.DOTALL)
    assert match, "MANUAL_COMMERCIAL_STATUSES non trovato"
    used = set(re.findall(r"'([a-z_]+)'", match.group(1)))
    real = _real_enum_set("PROPERTY_STATUSES", PROPERTY_ENUMS)
    assert used.issubset(real), f"stati non reali usati: {used - real}"
    assert "sold" not in used, (
        "'sold' non deve mai essere una transizione manuale: e' raggiunto solo "
        "come side-effect di sale/repository.py::complete_sale (soldMismatch)"
    )
    # nessuno stato reale dimenticato per errore, a parte 'sold' (escluso di proposito)
    assert used == (real - {"sold"}), f"differenza inattesa rispetto a PROPERTY_STATUSES: {used.symmetric_difference(real - {'sold'})}"


def test_confirm_required_statuses_are_withdrawn_and_archived():
    text = _read(IMMOBILE_JS)
    match = re.search(r"CONFIRM_REQUIRED_STATUSES = new Set\(\[(.*?)\]\);", text)
    assert match, "CONFIRM_REQUIRED_STATUSES non trovato"
    used = set(re.findall(r"'([a-z_]+)'", match.group(1)))
    assert used == {"withdrawn", "archived"}


def test_archived_transition_uses_dedicated_delete_endpoint_not_raw_patch():
    """archive_property (DELETE) e' l'unico path che imposta anche
    archived_at - una PATCH diretta a commercial_status='archived' non lo
    farebbe, rompendo l'invariante usata da mandate_expiring/KPI/alerts."""
    text = _read(IMMOBILE_JS)
    save_handler = _function_block(text, "applyCommercialStatusSave")
    assert "target === 'archived'" in save_handler
    assert re.search(r"deleteRequest\(`/api/property/properties/\$\{property\.id\}`\)", save_handler)
    assert re.search(r"patchRequest\(`/api/property/properties/\$\{property\.id\}`, \{ commercial_status: target \}\)", save_handler)


def test_archive_repository_sets_status_timestamp_and_returns_persisted_row(monkeypatch):
    from property import repository

    persisted = {"id": 12, "commercial_status": "draft", "archived_at": None}

    def persist(property_id, changes):
        assert property_id == 12
        persisted.update(changes)
        return dict(persisted)

    monkeypatch.setattr(repository, "update_property", persist)
    archived = repository.archive_property(12)

    assert archived["commercial_status"] == "archived"
    assert isinstance(archived["archived_at"], datetime)
    assert persisted == archived


def test_sold_status_has_no_manual_edit_control():
    """Quando commercial_status e' gia' 'sold', la sezione non deve offrire
    alcun pulsante di modifica (ne' select, ne' 'Cambia stato')."""
    text = _read(IMMOBILE_JS)
    section = text[text.index("function renderCommercialStatusSection"):]
    section = section[:section.index("\n}\n")]
    sold_branch = section[section.index("if (p.commercial_status === 'sold')"):section.index("if (!editMode)")]
    assert "commercial-status-edit-btn" not in sold_branch
    assert "commercial-status-select" not in sold_branch


def test_two_step_confirm_not_window_confirm():
    code_only = _strip_js_line_comments(_read(IMMOBILE_JS))
    assert "window.confirm(" not in code_only
    assert "window.prompt(" not in code_only
    assert "commercialStatusPendingConfirm" in code_only


def test_two_step_confirm_preserves_the_selected_status_across_rerender():
    """The first confirmation click re-renders the section.

    The selected target therefore needs independent state; otherwise the new
    select falls back to the persisted status and the second click is a no-op.
    """
    text = _read(IMMOBILE_JS)
    assert "commercialStatusPendingTarget" in text
    render_call = re.search(
        r"renderCommercialStatusSection\(p,\s*commercialStatusEditMode,\s*commercialStatusPendingConfirm,\s*commercialStatusPendingTarget\)",
        text,
    )
    assert render_call, "il target pending deve attraversare il re-render"
    section = text[text.index("function renderCommercialStatusSection"):]
    assert "s === (pendingTarget || p.commercial_status)" in section
    save_handler = text[text.index("const saveBtn = panelEl.querySelector('#commercial-status-save-btn')"):]
    assert "commercialStatusPendingTarget = decision.pendingTarget;" in save_handler
    assert "resolveCommercialStatusSave(property.commercial_status, select.value, commercialStatusPendingTarget)" in save_handler
    assert "pendingConfirm ? 'disabled' : ''" in section


def test_archive_confirmation_state_machine_requires_two_clicks_before_submit():
    function = _function_block(_read(IMMOBILE_JS), "resolveCommercialStatusSave")
    script = f"""
const CONFIRM_REQUIRED_STATUSES = new Set(['withdrawn', 'archived']);
{function}
const first = resolveCommercialStatusSave('draft', 'archived', null);
const second = resolveCommercialStatusSave('draft', 'archived', first.pendingTarget);
process.stdout.write(JSON.stringify({{first, second}}));
"""
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    states = json.loads(result.stdout)
    assert states["first"] == {"action": "confirm", "target": "archived", "pendingTarget": "archived"}
    assert states["second"] == {"action": "submit", "target": "archived", "pendingTarget": None}


def test_archive_submit_calls_delete_once_and_applies_persisted_response():
    function = _function_block(_read(IMMOBILE_JS), "applyCommercialStatusSave")
    script = f"""
{function}
const calls = [];
const property = {{id: 12, commercial_status: 'draft', archived_at: null}};
const apiDelete = async (path) => {{
  calls.push(['DELETE', path]);
  return {{id: 12, commercial_status: 'archived', archived_at: '2026-09-04T22:00:00Z'}};
}};
const apiPatch = async (path, body) => {{ calls.push(['PATCH', path, body]); }};
await applyCommercialStatusSave(property, 'archived', apiDelete, apiPatch);
process.stdout.write(JSON.stringify({{calls, property}}));
"""
    result = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    outcome = json.loads(result.stdout)
    assert outcome["calls"] == [["DELETE", "/api/property/properties/12"]]
    assert outcome["property"]["commercial_status"] == "archived"
    assert outcome["property"]["archived_at"] == "2026-09-04T22:00:00Z"


def test_header_badge_still_refreshed_via_existing_helper_after_manual_change():
    """Riusa lo stesso #property-status-badge/headerBadgeHtml gia' introdotto
    in P11 per reloadPropertyStatus - nessun secondo meccanismo di badge
    inventato."""
    text = _read(IMMOBILE_JS)
    save_handler = text[text.index("const saveBtn = panelEl.querySelector('#commercial-status-save-btn')"):]
    save_handler = save_handler[:save_handler.index("\n  }\n\n  // P9:")]
    assert "#property-status-badge" in save_handler
    assert "headerBadgeHtml()" in save_handler


def test_incarico_section_still_never_sends_commercial_status():
    """Regressione P8: la sezione Incarico deve restare indipendente da
    commercial_status (nessun accoppiamento inventato tra le due sezioni)."""
    text = _read(IMMOBILE_JS)
    start = text.index("function bindIncaricoSection")
    end = text.index("function bindCommercialStatusSection")
    incarico_handler = _strip_js_line_comments(text[start:end])
    assert "commercial_status" not in incarico_handler


def test_sale_completion_flow_still_untouched():
    """Regressione P11: reloadPropertyStatus (side-effect di complete_sale)
    deve restare invariata - questa e' l'unica altra scrittrice di
    commercial_status nel file."""
    text = _read(IMMOBILE_JS)
    assert "async function reloadPropertyStatus()" in text
    assert "property.commercial_status = updated.commercial_status;" in text


def test_immobile_dettaglio_js_is_syntactically_valid():
    result = subprocess.run(["node", "--check", str(IMMOBILE_JS)], capture_output=True, text=True)
    assert result.returncode == 0, f"{IMMOBILE_JS.name}: {result.stderr.strip()}"
