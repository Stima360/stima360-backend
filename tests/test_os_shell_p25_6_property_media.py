"""P25.6 - Property photos/documents base in the OS Shell.

Static/contract checks (same approach as the other P25 sub-project test
files): text-level assertions on the JS source plus real `node --check`
syntax validation. No JS test runner introduced.

Backend contracts these checks are pinned against (verified in
property/schemas.py, property/service.py, property/repository.py,
property/router.py before writing any JS):
  POST   /api/property/properties/{id}/photos     (PhotoCreate: url is a
         plain string - a link to an already-hosted file, NOT a binary
         upload; no UploadFile/multipart endpoint exists anywhere in this
         backend)
  DELETE /api/property/photos/{photo_id}
  POST   /api/property/properties/{id}/documents  (DocumentCreate: same url/
         storage_key-as-string shape)
  DELETE /api/property/documents/{document_id}
No PATCH-based "modifica" is expected for either (base scope is
add/view/delete only, per the P25.6 brief); is_cover exclusivity is handled
server-side (property/repository.py:199,221-224), so no client-side "set as
cover" logic is invented.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIEWS = ROOT / "static" / "os_shell" / "assets" / "views"
PROPERTY_ENUMS = ROOT / "property" / "enums.py"

IMMOBILE_JS = VIEWS / "immobile-dettaglio.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _strip_js_line_comments(text: str) -> str:
    return "\n".join(line.split("//", 1)[0] for line in text.splitlines())


def _real_enum_set(name: str, path: Path) -> set[str]:
    text = _read(path)
    match = re.search(rf"{name}\s*=\s*\{{([^}}]*)\}}", text)
    assert match, f"{name} non trovato in {path}"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def test_media_functions_exist():
    text = _read(IMMOBILE_JS)
    for fn in (
        "bindFotoSection", "bindDocumentiSection", "renderMediaAddSection",
        "reloadPropertyPhotos", "reloadPropertyDocuments",
    ):
        assert re.search(rf"function {fn}\(", text), f"{fn}() non trovata"


def test_photo_and_document_use_exact_backend_endpoints():
    text = _read(IMMOBILE_JS)
    assert re.search(r"apiPost\(`/api/property/properties/\$\{property\.id\}/photos`, payload\)", text)
    assert re.search(r"apiDelete\(`/api/property/photos/\$\{photoId\}`\)", text)
    assert re.search(r"apiPost\(`/api/property/properties/\$\{property\.id\}/documents`, payload\)", text)
    assert re.search(r"apiDelete\(`/api/property/documents/\$\{documentId\}`\)", text)


def test_no_photo_or_document_patch_endpoint_used():
    """Scope 'base' esplicito nel brief P25.6: solo add/view/delete, nessuna
    'Modifica' (PATCH /api/property/photos/{id} o .../documents/{id})."""
    text = _read(IMMOBILE_JS)
    assert not re.search(r"apiPatch\(`/api/property/photos/", text)
    assert not re.search(r"apiPatch\(`/api/property/documents/", text)


def test_no_multipart_or_file_upload_invented():
    """Nessun endpoint di upload binario esiste nel backend (verificato in
    property/router.py/schemas.py/service.py/repository.py: PhotoCreate.url
    e DocumentCreate.url/storage_key sono stringhe). Questa UI non deve
    inventare FormData multipart, un input file, o un nuovo endpoint."""
    text = _read(IMMOBILE_JS)
    start = text.index("function bindFotoSection")
    end = text.index("function bindDocumentiSection")
    foto_section = text[start:end] + text[text.index("function bindDocumentiSection"):text.index("function bindDocumentiSection") + 4000]
    assert 'type="file"' not in foto_section
    assert "multipart" not in foto_section.lower()
    assert "new FormData(form)" in foto_section  # form data letta come campi testo, non multipart di file


def test_document_status_options_match_real_backend_enum():
    text = _read(IMMOBILE_JS)
    match = re.search(r"DOCUMENT_STATUS_LABELS = \{([^}]*)\}", text)
    assert match, "DOCUMENT_STATUS_LABELS non trovato"
    used = set(re.findall(r"(\w+):", match.group(1)))
    assert used == _real_enum_set("DOCUMENT_STATUSES", PROPERTY_ENUMS)


def test_photo_and_document_removal_use_two_step_inline_confirm_not_window_confirm():
    code_only = _strip_js_line_comments(_read(IMMOBILE_JS))
    assert "window.confirm(" not in code_only
    assert "window.prompt(" not in code_only
    assert "photoRemoveConfirm" in code_only
    assert "documentRemoveConfirm" in code_only
    assert "data-photo-remove=" in code_only
    assert "data-document-remove=" in code_only


def test_cover_exclusivity_not_duplicated_client_side():
    """property/repository.py gestisce gia' l'esclusivita' di is_cover
    lato server (un solo UPDATE ... SET is_cover=FALSE prima dell'insert/
    update): nessuna logica client-side che azzeri is_cover sulle altre foto
    deve essere reinventata qui."""
    text = _read(IMMOBILE_JS)
    start = text.index("function bindFotoSection")
    end = text.index("function bindDocumentiSection")
    foto_section = _strip_js_line_comments(text[start:end])
    assert "is_cover = false" not in foto_section.replace(" ", "").lower()
    assert ".forEach" not in foto_section or "is_cover" not in foto_section.split(".forEach")[1][:200]


def test_showtab_wires_new_bind_functions_for_foto_and_documenti():
    text = _read(IMMOBILE_JS)
    match = re.search(r"case 'foto':(.*?)break;", text, re.DOTALL)
    assert match and "bindFotoSection" in match.group(1)
    match = re.search(r"case 'documenti':(.*?)break;", text, re.DOTALL)
    assert match and "bindDocumentiSection" in match.group(1)


def test_proprietari_and_visite_sections_still_present_p12_p16_regression():
    """Regressione P12/P16: le sezioni Proprietari e Visite (gia' operative
    prima di P25.6) non devono essere toccate da questo sub-progetto."""
    text = _read(IMMOBILE_JS)
    assert "function bindProprietariSection" in text
    assert "function bindVisiteSection" in text
    assert "async function reloadPropertyContacts" in text
    assert "async function reloadPropertyVisits" in text


def test_commercial_status_section_still_present_p25_3_regression():
    text = _read(IMMOBILE_JS)
    assert "function bindCommercialStatusSection" in text
    assert "MANUAL_COMMERCIAL_STATUSES" in text


def test_immobile_dettaglio_js_is_syntactically_valid():
    result = subprocess.run(["node", "--check", str(IMMOBILE_JS)], capture_output=True, text=True)
    assert result.returncode == 0, f"{IMMOBILE_JS.name}: {result.stderr.strip()}"
