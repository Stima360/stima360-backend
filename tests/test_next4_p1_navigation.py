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
    # Handler inline legacy, se presente.
    patterns = (
        r"(?:qs|\$)\(\s*['\"]#login-form['\"]\s*\)\.onsubmit\s*=\s*async\s+\w+\s*=>\s*\{",
        r"document\.getElementById\(\s*['\"]login-form['\"]\s*\)\.onsubmit\s*=\s*async\s+\w+\s*=>\s*\{",
    )
    for pattern in patterns:
        m = re.search(pattern, js)
        if m:
            opening_brace = js.find("{", m.start())
            return _extract_braced_block(js, opening_brace)

    # Contratto reale: il form può delegare a una funzione nominata.
    binding_patterns = (
        r"(?:qs|\$)\(\s*['\"]#login-form['\"]\s*\)\.addEventListener\(\s*['\"]submit['\"]\s*,\s*([A-Za-z_$][\w$]*)\s*\)",
        r"document\.getElementById\(\s*['\"]login-form['\"]\s*\)\.addEventListener\(\s*['\"]submit['\"]\s*,\s*([A-Za-z_$][\w$]*)\s*\)",
        r"el\(\s*['\"]login-form['\"]\s*\)\.addEventListener\(\s*['\"]submit['\"]\s*,\s*([A-Za-z_$][\w$]*)\s*\)",
    )
    for pattern in binding_patterns:
        binding = re.search(pattern, js)
        if binding:
            return function_block(js, binding.group(1))

    raise AssertionError("Handler reale di #login-form non trovato")


def assert_in_order(text: str, *needles: str) -> None:
    pos = -1
    for needle in needles:
        new_pos = text.find(needle, pos + 1)
        assert new_pos >= 0, f"{needle!r} non trovato nell'ordine richiesto"
        pos = new_pos


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
        indirect = re.search(
            rf"positiveId\s*\(\s*{raw_name}\s*\)",
            block,
        ) is not None

    assert direct or indirect, (
        f"applyDeepLink deve validare il query param id con positiveId in {fe}"
    )


def test_core_contact360_deep_link_and_post_login_order():
    js = read_js("core")
    dl = function_block(js, "applyDeepLink")
    login = login_handler_block(js)

    assert re.search(r"\.get\(\s*['\"]view['\"]\s*\)", dl)
    assert re.search(r"contact360", dl)
    assert "openContact360(" in dl

    # P26-5: `/api/admin/check` era il primo passo del login Basic, e il login
    # conservava la coppia in `state.credentials` per rimetterla in un header a
    # ogni richiesta. Entrambe le cose sono sparite: la login parla con
    # /api/operator-auth/login e non conserva nulla. Cio' che questo test
    # protegge - che il deep-link avvenga DOPO il login e nell'ordine giusto -
    # non e' cambiato, ed e' asserito piu' sotto come prima.
    assert "/api/admin/check" not in login
    assert "OperatorSession.login(" in login

    compact_login = re.sub(r"\s+", "", login)

    assert "state.credentials" not in compact_login, (
        "CORE non deve conservare alcuna credenziale, nemmeno nello state runtime"
    )

    assert_in_order(login, "refresh()", "applyDeepLink()")

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

    # P26-5: `/api/admin/check` era il primo passo del login Basic, e il login
    # conservava la coppia in `state.credentials`. Entrambe le cose sono
    # sparite. L'ordine post-login, che e' cio' che questo test protegge,
    # resta asserito sotto.
    assert "/api/admin/check" not in login
    assert "OperatorSession.login(" in login

    compact = re.sub(r"\s+", "", login)

    assert "state.credentials" not in compact, (
        "PROPERTY non deve conservare alcuna credenziale, nemmeno nello state runtime"
    )

    assert_in_order(login, "refresh()", "applyDeepLink()")


def test_buy_deep_link_and_post_login_order():
    js = read_js("buy")
    dl = function_block(js, "applyDeepLink")
    login = login_handler_block(js)

    assert "detail(" in dl
    assert "catch" in dl
    assert "toast(" in dl

    # P26-5: `/api/admin/check` era il primo passo del login Basic, e il login
    # conservava la coppia in `state.credentials` per rimetterla in un header a
    # ogni richiesta. Entrambe le cose sono sparite: la login parla con
    # /api/operator-auth/login e non conserva nulla. Cio' che questo test
    # protegge - che il deep-link avvenga DOPO il login e nell'ordine giusto -
    # non e' cambiato, ed e' asserito piu' sotto come prima.
    assert "/api/admin/check" not in login
    assert "OperatorSession.login(" in login

    compact = re.sub(r"\s+", "", login)

    assert "credentials" not in compact.replace("credentials:'include'", ""), (
        "BUY non deve conservare alcuna credenziale, nemmeno nello state runtime"
    )

    assert_in_order(
        login,
        "dashboard()",
        "load()",
        "applyDeepLink()",
    )


def test_match_deep_link_owns_detail_or_dashboard_choice():
    js = read_js("match")
    dl = function_block(js, "applyDeepLink")
    login = login_handler_block(js)

    # P26-5: `/api/admin/check` era il primo passo del login Basic, e il login
    # conservava la coppia in `state.credentials` per rimetterla in un header a
    # ogni richiesta. Entrambe le cose sono sparite: la login parla con
    # /api/operator-auth/login e non conserva nulla. Cio' che questo test
    # protegge - che il deep-link avvenga DOPO il login e nell'ordine giusto -
    # non e' cambiato, ed e' asserito piu' sotto come prima.
    assert "/api/admin/check" not in login
    assert "OperatorSession.login(" in login

    compact = re.sub(r"\s+", "", login)

    assert "credentials" not in compact.replace("credentials:'include'", ""), (
        "MATCH non deve conservare alcuna credenziale, nemmeno nello state runtime"
    )

    assert "applyDeepLink()" in login

    assert "detail(" in dl

    assert (
        re.search(r"getElementById\(\s*['\"]matches['\"]\s*\)", dl)
        or re.search(r"el\(\s*['\"]matches['\"]\s*\)", dl)
    ), "MATCH deve aprire esplicitamente la view matches"

    assert (
        "load('dashboard')" in dl
        or 'load("dashboard")' in dl
    )

    assert "catch" in dl
    assert "toast(" in dl


def test_core_contact360_cross_links_use_only_validated_ids():
    js = read_js("core")
    block = function_block(js, "renderContact360")

    for marker in (
        "positiveId(p.id)",
        "positiveId(b.id)",
        "positiveId(m.id)",
        "positiveId(v.property_id)",
    ):
        assert marker in re.sub(r"\s+", "", block), (
            f"Manca {marker} in CORE"
        )

    assert 'href="/property-admin/?id=${pid}"' in block
    assert 'href="/buy-admin/?id=${bid}"' in block
    assert 'href="/match-admin/?id=${mid}"' in block

    assert block.count(
        'href="/property-admin/?id=${pid}"'
    ) >= 2

    assert block.count('target="_blank"') >= 4
    assert block.count('rel="noopener noreferrer"') >= 4


def test_property_contact_to_core360_uses_validated_contact_id():
    js = read_js("property")
    block = function_block(js, "renderDetail")

    compact = re.sub(r"\s+", "", block)

    assert "positiveId(x.contact_id)" in compact

    assert (
        'href="/core-admin/?view=contact360&id=${cid}"'
        in block
    )

    assert 'target="_blank"' in block
    assert 'rel="noopener noreferrer"' in block


def test_buy_contact_property_and_match_links_use_validated_ids():
    js = read_js("buy")
    block = function_block(js, "detail")

    compact = re.sub(r"\s+", "", block)

    assert "positiveId(x.contact_id)" in compact
    assert "positiveId(m.property_id)" in compact
    assert "positiveId(m.id)" in compact

    assert (
        'href="/core-admin/?view=contact360&id=${cid}"'
        in block
    )

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

    assert "/core-admin/?view=contact360" not in block


@pytest.mark.parametrize("fe", FRONTENDS)
def test_no_persistent_auth_or_credentials_in_urls(fe):
    js = read_js(fe)

    for forbidden in (
        "localStorage",
        "sessionStorage",
        "document.cookie",
        "indexedDB",
    ):
        assert forbidden not in js, (
            f"{forbidden} non ammesso in {fe}"
        )

    credential_query = re.compile(
        r"[\?&]\s*(?:user(?:name)?|pass(?:word)?|token|access_token|auth(?:orization)?)\s*=",
        re.IGNORECASE,
    )

    assert not credential_query.search(js), (
        f"Possibile credenziale/token inserita "
        f"in query string in {fe}"
    )

    params_set = re.compile(
        r"\.set\(\s*['\"](?:user(?:name)?|pass(?:word)?|token|access_token|auth(?:orization)?)['\"]\s*,",
        re.IGNORECASE,
    )

    assert not params_set.search(js), (
        f"Possibile credenziale/token inserita "
        f"via URLSearchParams.set in {fe}"
    )


@pytest.mark.parametrize("fe", FRONTENDS)
def test_next2_frontend_auth_contract_is_preserved(fe):
    js = read_js(fe)

    for forbidden in (
        "localStorage",
        "sessionStorage",
        "document.cookie",
        "indexedDB",
    ):
        assert forbidden not in js, (
            f"{forbidden} non ammesso in {fe}"
        )

    # P26-5 HA INVERTITO QUESTA META' DEL CONTRATTO.
    #
    # NEXT-2 congelava il canale Basic: stato credenziali runtime, encodeBasic,
    # btoa, header Authorization e il gate `/api/admin/check`. Era la
    # descrizione corretta di com'era autenticato questo frontend. Adesso
    # autentica con la sessione operatore, e ognuna di quelle cinque cose e'
    # diventata un divieto invece di un obbligo.
    #
    # Le due righe che NON cambiano sono la gestione del 401 e la presenza del
    # logout: quelle regole valgono su qualunque canale, e restano identiche.
    #
    # I commenti vengono tolti prima di cercare: i commenti di P26-5 nominano
    # apposta cio' che il codice non fa piu' ("NESSUN header Authorization"), e
    # una ricerca sul testo grezzo scambierebbe la spiegazione per la cosa
    # spiegata.
    code = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    code = re.sub(r"(?m)^\s*//.*$|(?<=[;{}\s])//[^\n]*", "", code)

    for banned, why in (
        ("state.credentials", "stato credenziali runtime"),
        ("encodeBasic", "codifica Basic"),
        ("btoa(", "codifica Basic"),
        ("Authorization", "header Authorization"),
        ("/api/admin/check", "gate di login legacy"),
    ):
        assert banned not in code, f"{why} ancora presente in {fe}: {banned}"

    # Il canale nuovo, altrimenti il blocco sopra passerebbe anche su un
    # frontend che ha semplicemente perso l'autenticazione.
    for required in ("OperatorSession.login(", "OperatorSession.restore(",
                     "OperatorSession.logout(", "OperatorSession.authFetch("):
        assert required in code, f"{required} mancante in {fe}"

    assert (
        "=== 401" in js
        or "===401" in js
    ), f"Gestione 401 mancante in {fe}"

    assert "logout" in js.lower(), f"Logout mancante in {fe}"


def test_next2_wrapper_names_are_not_replaced_by_p1():
    core = read_js("core")
    prop = read_js("property")
    buy = read_js("buy")
    match = read_js("match")

    assert re.search(
        r"async\s+function\s+api\s*\(\s*path\b",
        core,
    )

    assert re.search(
        r"async\s+function\s+api\s*\(\s*base\s*,\s*path\b",
        prop,
    )

    assert re.search(
        r"async\s+function\s+req\s*\(\s*url\b",
        buy,
    )

    assert re.search(
        r"async\s+function\s+api\s*\(\s*path\b",
        match,
    )
