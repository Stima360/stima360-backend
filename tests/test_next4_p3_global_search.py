import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
JS_PATH = ROOT / "static/core_admin/assets/app.js"
HTML_PATH = ROOT / "static/core_admin/index.html"

JS = JS_PATH.read_text(encoding="utf-8")
HTML = HTML_PATH.read_text(encoding="utf-8")


def block(name: str) -> str:
    match = re.search(
        rf"(?:async\s+)?function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{",
        JS,
    )
    assert match, f"Funzione {name} mancante"

    start = match.end() - 1
    depth = 0
    quote = None
    escape = False

    for index in range(start, len(JS)):
        character = JS[index]
        previous = JS[index - 1] if index else ""

        if escape:
            escape = False
            continue

        if character == "\\" and quote:
            escape = True
            continue

        if quote:
            if character == quote and previous != "\\":
                quote = None
            continue

        if character in ("'", '"', "`"):
            quote = character
            continue

        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return JS[start : index + 1]

    raise AssertionError(f"Funzione {name} non chiusa")


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def test_global_search_ui_exists_only_in_core_admin_and_caps_query_length():
    assert 'id="global-search-wrap"' in HTML
    assert 'id="global-search"' in HTML
    assert 'id="global-search-results"' in HTML
    assert 'maxlength="200"' in HTML
    assert "Cerca ovunque" in HTML

    marker = "NEXT4_P3_GLOBAL_SEARCH"
    assert marker in JS
    assert marker in HTML

    for frontend in ("property", "buy", "match"):
        frontend_root = ROOT / f"static/{frontend}_admin"
        for path in frontend_root.rglob("*"):
            if path.is_file():
                assert marker not in path.read_text(encoding="utf-8")


def test_search_requires_two_characters_and_debounces_for_300_ms():
    schedule = block("scheduleGlobalSearch")
    compact_schedule = compact(schedule)

    assert "query=input.value.trim()" in compact_schedule
    assert "query.length<2" in compact_schedule
    assert "clearTimeout(globalSearchTimer)" in compact_schedule
    assert re.search(r"setTimeout\([^,]+,\s*300\s*\)", schedule, re.DOTALL)


def test_query_is_encoded_and_existing_search_endpoints_are_reused():
    run = block("runGlobalSearch")

    assert "encodeURIComponent(query)" in compact(run)
    assert "/contacts?search=${encoded}&limit=5" in run
    assert "/api/property/properties?search=${encoded}&limit=5" in run
    assert "/api/buy/requests?search=${encoded}&limit=5" in run


def test_module_failure_does_not_block_results_and_stale_responses_are_ignored():
    run = block("runGlobalSearch")
    compact_run = compact(run)

    assert "Promise.allSettled(" in run
    assert "result.status==='fulfilled'" in compact_run
    assert "sequence!==globalSearchSequence" in compact_run


def test_numeric_match_lookup_uses_positive_id_and_404_is_not_global_error():
    run = block("runGlobalSearch")
    lookup = block("lookupGlobalSearchMatch")
    api = block("api")

    assert "positiveId(query)" in compact(run)
    assert "/api/match/matches/${id}" in lookup
    assert "e.status===404" in compact(lookup)
    assert "returnnull" in compact(lookup)
    assert "error.status=r.status" in compact(api)


def test_deep_links_are_built_only_after_positive_id_validation():
    href = block("globalSearchHref")
    compact_href = compact(href)

    assert "constid=positiveId(itemId)" in compact_href
    assert "if(id===null)returnnull" in compact_href
    assert "/core-admin/?view=contact360&id=${id}" in href
    assert "/property-admin/?id=${id}" in href
    assert "/buy-admin/?id=${id}" in href
    assert "/match-admin/?id=${id}" in href


def test_result_renderer_uses_safe_dom_and_safe_new_tab_links():
    result_node = block("globalSearchResultNode")
    render = block("renderGlobalSearchResults")
    combined = result_node + render

    assert ".innerHTML" not in combined
    assert "document.createElement" in result_node
    assert ".textContent" in result_node
    assert "globalSearchHref(" in result_node
    assert "target='_blank'" in compact(result_node)
    assert "noopener noreferrer" in result_node


def test_cancel_global_search_clears_timer_and_invalidates_pending_requests():
    cancel = compact(block("cancelGlobalSearch"))

    assert "clearTimeout(globalSearchTimer)" in cancel
    assert "globalSearchTimer=null" in cancel
    assert "globalSearchSequence+=1" in cancel
    assert "clearGlobalSearchResults()" in cancel


def test_click_away_and_logout_cancel_global_search():
    assert "cancelGlobalSearch()" in block("logout")
    click_away = compact(JS[JS.index("document.addEventListener('click'") :])
    assert "if(wrap&&!wrap.contains(event.target)){cancelGlobalSearch();}" in click_away


def test_next2_next3_p1_and_p2_contracts_are_preserved():
    compact_js = compact(JS)

    assert "/api/admin/check" in JS
    assert "state.credentials={username,password}" in compact_js
    assert (
        "headers.Authorization="
        "encodeBasic(state.credentials.username,state.credentials.password)"
        in compact_js
    )
    assert "if(r.status===401)" in compact_js

    for forbidden in ("localStorage", "sessionStorage", "document.cookie", "indexedDB"):
        assert forbidden not in JS

    assert "/api/crm/contacts/${id}/360" in block("openContact360")
    assert "renderContact360" in JS

    deep_link = block("applyDeepLink")
    assert "URLSearchParams" in deep_link
    assert "positiveId(rawId)" in deep_link
    assert "openContact360(id)" in deep_link

    agenda = block("renderAgenda")
    assert "/api/buy/requests?limit=200" in agenda
    assert "/api/property/visits?limit=500" in agenda
    assert 'data-view="agenda"' in HTML
