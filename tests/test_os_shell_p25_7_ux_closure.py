"""P25.7 - UX closure.

Covers, in one file (each concern gets its own clearly-named test group):

1. Gap C fix (OGGI showing "Venditore #23" instead of a real name): the
   additive `subject_label` field on next_best_action, computed by a
   dynamic LEFT JOIN on contacts at read time (next_best_action/
   repository.py::_row / _subject_label_from_contact /
   _SELECT_WITH_CONTACT_LABEL). No migration, no new column on
   next_best_actions, no change to next_best_action/engine.py's frozen
   ranking/precedence/eligibility logic.
2. The invisible-sale.js dead link fix (#/buy/richieste/{id} ->
   #/acquirenti/{id}) and a systematic search for other legacy/dead links
   across static/os_shell/assets.
3. Removal of the "Impostazioni" placeholder from the sidebar (and the
   now-unused makePlaceholderView import, if main.js no longer needs it).
4. Replacement of the one remaining window.alert() in attivita.js with the
   existing inline error-box pattern.

Static/contract checks for the frontend pieces (same approach as the other
P25 sub-project test files): text-level assertions on the JS source plus
real `node --check` syntax validation. The backend piece (subject_label) is
tested as plain Python unit tests against the pure `_row`/
`_subject_label_from_contact` functions - no FakeCursor needed, they take
plain dicts.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from next_best_action import repository as nba_repository

ROOT = Path(__file__).resolve().parents[1]
VIEWS = ROOT / "static" / "os_shell" / "assets" / "views"
COMPONENTS = ROOT / "static" / "os_shell" / "assets" / "components"
CORE = ROOT / "static" / "os_shell" / "assets" / "core"
MAIN_JS = ROOT / "static" / "os_shell" / "assets" / "main.js"

ATTIVITA_JS = VIEWS / "attivita.js"
INVISIBLE_SALE_JS = COMPONENTS / "invisible-sale.js"
OGGI_JS = VIEWS / "oggi.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _strip_js_line_comments(text: str) -> str:
    return "\n".join(line.split("//", 1)[0] for line in text.splitlines())


# --- 1. Gap C: subject_label -----------------------------------------------


def _contact_row(**overrides):
    base = {
        "id": 1,
        "subject_type": "lead",
        "subject_id": 23,
        "contact_id": 10,
        "priority": "urgent",
        "_contact_type": "person",
        "_contact_display_name": None,
        "_contact_first_name": None,
        "_contact_last_name": None,
        "_contact_company_name": None,
    }
    base.update(overrides)
    return base


def test_row_uses_display_name_when_present():
    row = nba_repository._row(_contact_row(_contact_display_name="Maria Rossi"))
    assert row["subject_label"] == "Maria Rossi"


def test_row_uses_company_name_when_contact_type_is_company():
    row = nba_repository._row(_contact_row(
        _contact_type="company", _contact_company_name="Rossi Immobiliare Srl",
    ))
    assert row["subject_label"] == "Rossi Immobiliare Srl"


def test_row_uses_first_and_last_name_when_person_and_no_display_name():
    row = nba_repository._row(_contact_row(
        _contact_first_name="Maria", _contact_last_name="Rossi",
    ))
    assert row["subject_label"] == "Maria Rossi"


def test_row_subject_label_none_when_no_contact_id_or_join_match():
    """contact_id NULL (or no matching contacts row): the LEFT JOIN columns
    come back NULL. subject_label must be None, NEVER a synthetic
    "{Type} #{id}" string - that fallback formatting stays owned by the
    frontend (oggi.js::nbaSubjectLabel), unchanged."""
    row = nba_repository._row(_contact_row(contact_id=None))
    assert row["subject_label"] is None


def test_row_subject_label_none_when_contact_has_no_usable_name():
    row = nba_repository._row(_contact_row(_contact_type="company"))  # no company_name either
    assert row["subject_label"] is None


def test_row_never_leaks_internal_contact_columns_into_the_returned_dict():
    row = nba_repository._row(_contact_row(_contact_display_name="Maria Rossi"))
    assert not any(k.startswith("_contact_") for k in row)


def test_row_returns_none_for_falsy_input():
    assert nba_repository._row(None) is None
    assert nba_repository._row({}) is None


def test_list_current_and_get_current_query_uses_dynamic_left_join_on_contacts():
    """Locks in the 'dynamic LEFT JOIN, no migration' contract: the SELECT
    constant itself (not a whole-file substring search, which would
    false-positive against this file's own prose comments) must join
    contacts on next_best_actions.contact_id."""
    sql = nba_repository._SELECT_WITH_CONTACT_LABEL
    normalized = " ".join(sql.split()).lower()
    assert "left join contacts c on c.id = nba.contact_id" in normalized
    assert "from next_best_actions nba" in normalized


def test_next_best_action_schema_has_additive_optional_subject_label():
    """Verifica testuale (non un import diretto): questo sandbox non ha
    pydantic installato - nessun test esistente nel repo importa
    next_best_action.schemas per lo stesso motivo (verificato via grep prima
    di scrivere questo test). Stessa strategia gia' adottata altrove nel
    progetto per limiti equivalenti del sandbox (fastapi/psycopg2)."""
    schemas_path = ROOT / "next_best_action" / "schemas.py"
    text = _read(schemas_path)
    match = re.search(r"subject_label:\s*str \| None\s*=\s*None", text)
    assert match, "subject_label deve essere un campo opzionale (str | None = None), additivo e mai richiesto"


def test_no_new_table_or_column_introduced_for_gap_c():
    """Il fix Gap C non deve introdurre alcuna migration ne' alcuna nuova
    colonna su next_best_actions: la SELECT deve restare `nba.*` (tutte le
    colonne esistenti) piu' SOLO colonne aliasate `_contact_*` provenienti
    dal JOIN, mai una scrittura."""
    sql = nba_repository._SELECT_WITH_CONTACT_LABEL
    normalized = " ".join(sql.split()).lower()
    assert normalized.startswith("select nba.*,")
    assert "insert" not in normalized and "update" not in normalized and "alter" not in normalized


def test_oggi_js_falls_back_to_type_and_id_when_no_subject_label():
    """Regressione frontend: oggi.js deve continuare a produrre
    '{Type} #{id}' quando l'API non fornisce (o fornisce null)
    subject_label - il fix e' additivo, non deve rompere il fallback
    esistente."""
    text = _read(OGGI_JS)
    assert "n.subject_label" in text, "oggi.js non usa ancora il nuovo campo subject_label"
    match = re.search(r"function nbaSubjectLabel\(n\) \{(.*?)\n\}", text, re.DOTALL)
    assert match, "nbaSubjectLabel non trovata"
    body = match.group(1)
    assert "subject_id" in body and "subject_type" in body, "il fallback '{Type} #{id}' deve restare presente"


# --- 2. Dead links -----------------------------------------------------------


def test_invisible_sale_dead_link_is_fixed():
    text = _read(INVISIBLE_SALE_JS)
    assert "#/buy/richieste/" not in text, "link morto ancora presente in invisible-sale.js"
    assert "#/acquirenti/" in text, "route corretta verso la scheda Richiesta BUY mancante"


def test_no_dead_links_anywhere_in_os_shell():
    """Ricerca sistematica di pattern di route note come morte/mai
    registrate in main.js (nessuna sezione 'buy/richieste', solo
    'acquirenti' esiste - vedi main.js::SECTIONS)."""
    dead_patterns = [r"#/buy/richieste/", r"#/buy-admin", r"#/property-admin", r"#/core-admin", r"#/match-admin", r"#/flow-admin", r"#/owner-admin", r"#/owner-portal"]
    offenders = []
    for path in list(VIEWS.glob("*.js")) + list(COMPONENTS.glob("*.js")) + list(CORE.glob("*.js")) + [MAIN_JS]:
        text = _read(path)
        for pattern in dead_patterns:
            if re.search(pattern, text):
                offenders.append(f"{path.name}: {pattern}")
    assert not offenders, f"link morti/legacy trovati: {offenders}"


# --- 3. Impostazioni placeholder removal -------------------------------------


def test_impostazioni_no_longer_uses_placeholder_view():
    main_js = _read(MAIN_JS)
    assert "Sezione in preparazione" not in main_js
    explicit = set(re.findall(r"registerRoute\('([a-z]+)'", main_js))
    assert "impostazioni" in explicit, "impostazioni deve avere una route esplicita reale, non il placeholder"


def test_placeholder_view_import_removed_if_now_unused():
    main_js = _read(MAIN_JS)
    if "makePlaceholderView" not in main_js:
        # Rimosso interamente: nessun import orfano deve restare.
        assert "placeholder.js" not in main_js
    # Se ancora presente (es. riutilizzato altrove), non e' un errore: il
    # solo vincolo e' che 'impostazioni' non lo usi piu' (verificato sopra).


# --- 4. window.alert() replacement in attivita.js ----------------------------


def test_attivita_js_has_zero_window_alert_calls():
    code_only = _strip_js_line_comments(_read(ATTIVITA_JS))
    assert len(re.findall(r"window\.alert\(", code_only)) == 0, (
        "window.alert() residuo in attivita.js: deve essere sostituito dal pattern error-box inline"
    )


def test_attivita_js_task_completion_error_uses_inline_error_box():
    text = _read(ATTIVITA_JS)
    assert "error-box" in text


# --- Syntax validation --------------------------------------------------------


def test_all_touched_js_files_are_syntactically_valid():
    for path in (ATTIVITA_JS, INVISIBLE_SALE_JS, OGGI_JS, MAIN_JS):
        result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
        assert result.returncode == 0, f"{path.name}: {result.stderr.strip()}"
