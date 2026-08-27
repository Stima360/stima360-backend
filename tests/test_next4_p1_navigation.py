import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
FRONTENDS = ("core", "property", "buy", "match")


def read_js(fe: str) -> str:
    return (ROOT / f"static/{fe}_admin/assets/app.js").read_text(encoding="utf-8")


def _extract_braced_block(js: str, opening_brace: int) -> str:
    """
    Extract a JS {...} block starting at opening_brace.

    The target blocks used by these tests (login handlers / small helper
    functions) do not contain unmatched literal braces, so brace balancing is
    sufficient and avoids fragile global `.*` regexes.
    """
    assert js[opening_brace] == "{"
    depth = 0

    for i in range(opening_brace, len(js)):
        ch = js[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return js[opening_brace : i + 1]

    raise AssertionError("Blocco JavaScript non chiuso")


def function_block(js: str, name: str) -> str:
    m = re.search(
        rf"(?:async\s+)?function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{",
        js,
    )
    assert m, f"Funzione {name} mancante"
    opening_brace = js.find("{", m.start())
    return _extract_braced_block(js, opening_brace)


def login_handler_block(js: str) -> str:
    # Pattern inline legacy, se presente.
    patterns = (
        r"(?:qs|\\$)\\(\\s*['\\\"]#login-form['\\\"]\\s*\\)\\.onsubmit\\s*=\\s*async\\s+\\w+\\s*=>\\s*\\{",
        r"document\\.getElementById\\(\\s*['\\\"]login-form['\\\"]\\s*\\)\\.onsubmit\\s*=\\s*async\\s+\\w+\\s*=>\\s*\\{",
    )
    for pattern in patterns:
        m = re.search(pattern, js)
        if m:
            opening_brace = js.find("{", m.start())
            return _extract_braced_block(js, opening_brace)

    # Contratto reale CORE e compatibile con gli altri frontend:
    # #login-form.addEventListener('submit', login)
    binding = re.search(
        r"(?:qs\\(\\s*['\\\"]#login-form['\\\"]\\s*\\)|"
        r"document\\.getElementById\\(\\s*['\\\"]login-form['\\\"]\\s*\\))"
        r"\\.addEventListener\\(\\s*['\\\"]submit['\\\"]\\s*,\\s*([A-Za-z_$][\\w$]*)\\s*\\)",
        js,
    )
    if binding:
        return function_block(js, binding.group(1))

    raise AssertionError("Handler reale di #login-form non trovato")


def assert_in_order(text: str, *needles: str) -> None:
    pos = -1
    for needle in needles:
        new_pos = text.find(needle, pos + 1)
        assert new_pos >= 0, f"{needle!r} non trovato nell'ordine richiesto"
        pos = new_pos


# ---------------------------------------------------------------------------
# 1-3. Deep-link foundation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fe", FRONTENDS)
def test_frontend_has_deep_link_foundation(fe):
    js = read_js(fe)

    assert "URLSearchParams" in js, f"Manca URLSearchParams in {fe}"
    assert "applyDeepLink" in js, f"Manca applyDeepLink in {fe}"
    assert "positiveId" in js, f"Manca positiveId in {fe}"

    positive = function_block(js, "positiveId")
    assert re.search(r"\bNumber\s*\(\s*value\s*\)", positive), (
        f"positiveId deve convertire value con Number() in {fe}"
    )
    assert re.search(r"Number\.isInteger\s*\(\s*n\s*\)", positive), (
        f"positiveId deve verificare Number.isInteger(n) in {fe}"
    )
    assert re.search(r"\bn\s*>\s*0\b", positive), (
        f"positiveId deve accettare solo ID > 0 in {fe}"
    )


@pytest.mark.parametrize("fe", FRONTENDS)
def test_apply_deep_link_uses_positive_id_for_query_id(fe):
    js = read_js(fe)
    block = function_block(js, "applyDeepLink")

    assert "window.location.search" in block
    assert "URLSearchParams" in block
    direct = re.search(
        r"positiveId\s*\([^;]*?\.get\(\s*['\"]id['\"]\s*\)\s*\)",
        block,
        re.DOTALL,
    )
    raw_assignment = re.search(
        r"(?:const|let)\s+([A-Za-z_$][\w$]*)\s*=\s*[^;]*?\.get\(\s*['\"]id['\"]\s*\)\s*;",
        block,
        re.DOTALL,
    )
    indirect = False
    if raw_assignment:
        raw_name = re.escape(raw_assignment.group(1))
        indirect = re.search(rf"positiveId\s*\(\s*{raw_name}\s*\)", block) is not None

    assert direct or indirect, (
        f"applyDeepLink deve validare il query param id con positiveId in {fe}"
    )


# ---------------------------------------------------------------------------
# 4-7 + 23-24. Deep-link destination, bootstrap order and safe fallback
# ---------------------------------------------------------------------------

def test_core_contact360_deep_link_and_post_login_order():
    js = read_js("core")
    dl = function_block(js, "applyDeepLink")
    login = login_handler_block(js)

    assert re.search(r"\.get\(\s*['\"]view['\"]\s*\)", dl)
    assert re.search(r"contact360", dl)
    assert "openContact360(" in dl

    # NEXT.2 login succeeds first; normal CORE bootstrap remains first.
    assert "/api/admin/check" in login
    compact_login = re.sub(r"\s+", "", login)
    assert (
        "state.credentials={username,password}" in compact_login
        or "state.credentials={u,p}" in compact_login
    ), "CORE deve conservare le credenziali esclusivamente nello state runtime"
    assert_in_order(login, "refresh()", "applyDeepLink()")

    # CORE openContact360 already owns error handling in NEXT.3; an outer catch
    # is allowed but not required.
    open_360 = function_block(js, "openContact360")
    assert "catch" in dl or "catch" in open_360, (
        "CORE deve gestire 404/errori nel deep-link o in openContact360"
    )
    assert "toast(" in dl or "toast(" in open_360


def test_property_deep_link_and_post_login_order():
    js = read_js("property")
    dl = function_block(js, "applyDeepLink")
    login = login_handler_block(js)

    assert "openDetail(" in dl
    assert "catch" in dl
    assert "toast(" in dl

    assert "/api/admin/check" in login
    compact = re.sub(r"\s+", "", login)
    assert "state.credentials={u,p}" in compact
    assert_in_order(login, "refresh()", "applyDeepLink()")


def test_buy_deep_link_and_post_login_order():
    js = read_js("buy")
    dl = function_block(js, "applyDeepLink")
    login = login_handler_block(js)

    assert "detail(" in dl
    assert "catch" in dl
    assert "toast(" in dl

    assert "/api/admin/check" in login
    compact = re.sub(r"\s+", "", login)
    assert "credentials={u,p}" in compact

    # BUY must preserve its existing dashboard + list bootstrap, then deep-link.
    assert_in_order(login, "dashboard()", "load()", "applyDeepLink()")


def test_match_deep_link_owns_detail_or_dashboard_choice():
    js = read_js("match")
    dl = function_block(js, "applyDeepLink")
    login = login_handler_block(js)

    # Post-login enters the deep-link dispatcher directly. We intentionally do
    # NOT require load('dashboard') before applyDeepLink().
    assert "/api/admin/check" in login
    compact = re.sub(r"\s+", "", login)
    assert "credentials={u,p}" in compact
    assert "applyDeepLink()" in login

    # ?id= valido -> MATCH view + detail(id)
    assert "detail(" in dl
    assert re.search(r"getElementById\(\s*['\"]matches['\"]\s*\)", dl)

    # Nessun/invalid ID or detail error -> safe dashboard.
    assert "load('dashboard')" in dl or 'load("dashboard")' in dl
    assert "catch" in dl
    assert "toast(" in dl


# ---------------------------------------------------------------------------
# 8-18. Approved cross-module links and validated IDs
# ---------------------------------------------------------------------------

def test_core_contact360_cross_links_use_only_validated_ids():
    js = read_js("core")
    block = function_block(js, "renderContact360")

    # CRM -> PROPERTY / BUY / MATCH / visit -> PROPERTY
    for marker in (
        "positiveId(p.id)",
        "positiveId(b.id)",
        "positiveId(m.id)",
        "positiveId(v.property_id)",
    ):
        assert marker in re.sub(r"\s+", "", block), f"Manca {marker} in CORE"

    assert 'href="/property-admin/?id=${pid}"' in block
    assert 'href="/buy-admin/?id=${bid}"' in block
    assert 'href="/match-admin/?id=${mid}"' in block

    # PROPERTY appears once for property and once for visit.
    assert block.count('href="/property-admin/?id=${pid}"') >= 2

    assert block.count('target="_blank"') >= 4
    assert block.count('rel="noopener noreferrer"') >= 4


def test_property_contact_to_core360_uses_validated_contact_id():
    js = read_js("property")
    block = function_block(js, "renderDetail")
    compact = re.sub(r"\s+", "", block)

    assert "positiveId(x.contact_id)" in compact
    assert 'href="/core-admin/?view=contact360&id=${cid}"' in block
    assert 'target="_blank"' in block
    assert 'rel="noopener noreferrer"' in block


def test_buy_contact_property_and_match_links_use_validated_ids():
    js = read_js("buy")
    block = function_block(js, "detail")
    compact = re.sub(r"\s+", "", block)

    assert "positiveId(x.contact_id)" in compact
    assert "positiveId(m.property_id)" in compact
    assert "positiveId(m.id)" in compact

    assert 'href="/core-admin/?view=contact360&id=${cid}"' in block
    assert 'href="/property-admin/?id=${pid}"' in block
    assert 'href="/match-admin/?id=${mid}"' in block

    assert block.count('target="_blank"') >= 3
    assert block.count('rel="noopener noreferrer"') >= 3


def test_match_buy_and_property_links_and_no_contact360_link():
    js = read_js("match")
    block = function_block(js, "detail")
    compact = re.sub(r"\s+", "", block)

    assert "positiveId(m.buy_request_id)" in compact
    assert "positiveId(m.property_id)" in compact

    assert 'href="/buy-admin/?id=${bid}"' in block
    assert 'href="/property-admin/?id=${pid}"' in block

    assert block.count('target="_blank"') >= 2
    assert block.count('rel="noopener noreferrer"') >= 2

    # Official P1 limitation: MATCH payload has no contact_id.
    assert "/core-admin/?view=contact360" not in block


# ---------------------------------------------------------------------------
# 19-22 + 25. Navigation security and exact NEXT.2 auth contract preservation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fe", FRONTENDS)
def test_no_persistent_auth_or_credentials_in_urls(fe):
    js = read_js(fe)

    # NEXT.2 memory-only contract.
    for forbidden in ("localStorage", "sessionStorage", "document.cookie", "indexedDB"):
        assert forbidden not in js, f"{forbidden} non ammesso in {fe}"

    # No credential-like query-string parameters in literal URLs.
    credential_query = re.compile(
        r"[\?&]\s*(?:user(?:name)?|pass(?:word)?|token|access_token|auth(?:orization)?)\s*=",
        re.IGNORECASE,
    )
    assert not credential_query.search(js), (
        f"Possibile credenziale/token inserita in query string in {fe}"
    )

    # Also reject URLSearchParams.set('credential', ...).
    params_set = re.compile(
        r"\.set\(\s*['\"](?:user(?:name)?|pass(?:word)?|token|access_token|auth(?:orization)?)['\"]\s*,",
        re.IGNORECASE,
    )
    assert not params_set.search(js), (
        f"Possibile credenziale/token inserita via URLSearchParams.set in {fe}"
    )


@pytest.mark.parametrize("fe", FRONTENDS)
def test_next2_frontend_auth_contract_is_preserved(fe):
    """
    Congela il contratto NEXT.2 già validato senza imporre nomi diversi
    da quelli realmente usati dai singoli frontend.
    """
    js = read_js(fe)

    for forbidden in ("localStorage", "sessionStorage", "document.cookie", "indexedDB"):
        assert forbidden not in js, f"{forbidden} non ammesso in {fe}"

    assert (
        "credentials =" in js
        or "credentials=" in js
        or "state.credentials" in js
    ), f"Stato credenziali runtime mancante in {fe}"

    assert "encodeBasic" in js, f"encodeBasic mancante in {fe}"
    assert "btoa(" in js, f"Basic encoding mancante in {fe}"
    assert "Authorization" in js, f"Header Authorization mancante in {fe}"
    assert "=== 401" in js or "===401" in js, f"Gestione 401 mancante in {fe}"
    assert "logout" in js.lower(), f"Logout mancante in {fe}"
    assert "/api/admin/check" in js, f"Login gate legacy mancante in {fe}"


def test_next2_wrapper_names_are_not_replaced_by_p1():
    """
    P1 aggiunge solo navigazione: i wrapper già esistenti devono restare.
    Le firme sono volutamente tolleranti alla formattazione.
    """
    core = read_js("core")
    prop = read_js("property")
    buy = read_js("buy")
    match = read_js("match")

    assert re.search(r"async\s+function\s+api\s*\(\s*path\b", core)
    assert re.search(r"async\s+function\s+api\s*\(\s*base\s*,\s*path\b", prop)
    assert re.search(r"async\s+function\s+req\s*\(\s*url\b", buy)
    assert re.search(r"async\s+function\s+api\s*\(\s*path\b", match)

