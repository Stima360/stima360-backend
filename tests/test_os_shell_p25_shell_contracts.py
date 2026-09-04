"""P25.0 - OS Shell safety net.

Static/contract checks on the vanilla-JS OS Shell (static/os_shell/), the
same text-level regex approach already used for backend isolation checks
(see tests/test_database_revival_isolation.py) applied to the frontend.
No JS test runner is introduced (explicit P25 constraint): "tests" here are
(a) structural assertions on the route registration source and (b) real
syntax validation via `node --check` (Node.js is available in this sandbox,
verified: `node --version` succeeds).

This file is the P25.0 baseline: it must be green on the CURRENT (pre-P25)
state of the shell. It intentionally does NOT yet assert the P25 fixes
(dead link, Impostazioni removal, alert removal) - those get their own
assertions in tests/test_os_shell_p25_ux_closure.py once implemented,
per the "test that fails -> minimal implementation -> test passes" flow
for each sub-project individually.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OS_SHELL = ROOT / "static" / "os_shell"
ASSETS = OS_SHELL / "assets"
VIEWS = ASSETS / "views"
COMPONENTS = ASSETS / "components"
CORE = ASSETS / "core"
MAIN_JS = ASSETS / "main.js"

EXPECTED_SECTION_NAMES = [
    "oggi", "contatti", "immobili", "acquirenti", "abbinamenti",
    "attivita", "automazioni",
]
# NOTA (P25 pre-push compliance patch): 'impostazioni' era nella baseline
# P0/P25.0 come sezione placeholder in sidebar. La specifica P25.7 approvata
# richiedeva di NON implementare Impostazioni e di rimuoverla/nasconderla
# dalla sidebar; una precedente implementazione le aveva invece dato una
# vista reale + route esplicita, deviando dalla specifica - corretto qui.
# 'impostazioni' non fa piu' parte delle sezioni attese: non deve comparire
# ne' in SECTIONS (main.js) ne' come registerRoute (vedi
# test_os_shell_p25_7_ux_closure.py::test_impostazioni_not_present_in_sections_or_routes).


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_main_js_exists():
    assert MAIN_JS.exists(), "main.js mancante: routing OS Shell non trovato"


def test_expected_sections_present_in_nav():
    main_js = _read(MAIN_JS)
    names = re.findall(r"name:\s*'([a-z]+)'", main_js)
    for expected in EXPECTED_SECTION_NAMES:
        assert expected in names, f"sezione '{expected}' non trovata in SECTIONS (main.js)"


def test_every_section_has_a_registered_route_or_placeholder():
    """Every name in SECTIONS must have a route registered somehow. At the
    P25.0 baseline this could be an explicit registerRoute(...) call OR a
    fall-through to the makePlaceholderView loop (main.js:75-78, pre-P25.7).
    As of the P25 pre-push compliance patch, every remaining SECTIONS name
    (oggi/contatti/immobili/acquirenti/abbinamenti/attivita/automazioni) has
    an explicit registerRoute and the placeholder loop/import were removed
    as no longer used by any section - 'impostazioni' itself was removed
    from SECTIONS entirely (see test_os_shell_p25_7_ux_closure.py), not
    given a route. This check now simplifies to "every SECTIONS name has an
    explicit registerRoute", a strictly stronger guarantee than the original
    either/or baseline (a section wired to neither would still be a real
    dead route)."""
    main_js = _read(MAIN_JS)
    names = re.findall(r"name:\s*'([a-z]+)'", main_js)
    explicit = set(re.findall(r"registerRoute\('([a-z]+)'", main_js))
    missing = [name for name in names if name not in explicit]
    assert not missing, f"sezioni senza registerRoute esplicita: {missing}"


def test_core_infrastructure_files_exist():
    for name in ("api-client.js", "auth.js", "router.js", "env-badge.js"):
        assert (CORE / name).exists(), f"core/{name} mancante"


def test_api_client_exposes_get_post_patch_delete():
    api_client = _read(CORE / "api-client.js")
    for fn in ("apiGet", "apiPost", "apiPatch", "apiDelete"):
        assert f"export function {fn}(" in api_client, f"api-client.js non esporta {fn}()"


def test_no_hardcoded_legacy_admin_links_in_os_shell():
    """The OS Shell must never link to a legacy admin app: every legacy
    capability gap is meant to be closed by adding OS Shell UI on top of
    existing backend endpoints, never by linking out to /core-admin,
    /property-admin, /buy-admin, /match-admin, /flow-admin, /owner-admin,
    /owner-portal (main.py:77-126 mounts, verified during the P25 audit)."""
    pattern = re.compile(r"/(core|property|buy|match|flow|owner)-admin|/owner-portal")
    offenders = []
    for path in list(VIEWS.glob("*.js")) + list(COMPONENTS.glob("*.js")) + list(CORE.glob("*.js")) + [MAIN_JS]:
        text = _read(path)
        if pattern.search(text):
            offenders.append(path.name)
    assert not offenders, f"link hardcoded a UI legacy trovati in: {offenders}"


def test_all_shell_js_files_are_syntactically_valid():
    """Real syntax validation via `node --check` (no JS test runner
    introduced, per explicit P25 constraint). Every .js file under
    static/os_shell/assets must parse."""
    js_files = sorted(
        list(VIEWS.glob("*.js"))
        + list(COMPONENTS.glob("*.js"))
        + list(CORE.glob("*.js"))
        + [MAIN_JS]
    )
    assert len(js_files) >= 17, f"attese almeno 17 file JS, trovate {len(js_files)}"
    failures = []
    for path in js_files:
        result = subprocess.run(
            ["node", "--check", str(path)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            failures.append((path.name, result.stderr.strip()))
    assert not failures, f"errori di sintassi: {failures}"


def test_placeholder_view_no_longer_used_after_p25_7():
    """Was the P25.0 baseline snapshot ("only 'impostazioni' is rendered by
    makePlaceholderView"); superseded by P25.7 as documented in the original
    docstring here ("if 'impostazioni' stops using it, P25.7 owns updating
    this expectation - see test_os_shell_p25_7_ux_closure.py"). An earlier
    version of P25.7 had instead given 'impostazioni' a real explicit route
    (views/impostazioni.js) - this deviated from the approved specification
    (which required 'impostazioni' to be removed/hidden from the sidebar,
    not implemented) and was corrected by a pre-push compliance patch:
    'impostazioni' was removed from SECTIONS entirely rather than routed.
    Every remaining SECTIONS name has an explicit registerRoute and
    makePlaceholderView is unused - test_os_shell_p25_7_ux_closure.py owns
    asserting both the import removal and 'impostazioni' absence; this test
    only locks in that no *remaining* section falls back to the placeholder
    any more."""
    main_js = _read(MAIN_JS)
    explicit = set(re.findall(r"registerRoute\('([a-z]+)'", main_js))
    non_explicit = [n for n in EXPECTED_SECTION_NAMES if n not in explicit]
    assert non_explicit == [], (
        f"attese tutte le sezioni con route esplicita, mancanti: {non_explicit}"
    )
