from pathlib import Path
import re
import pytest

ROOT = Path(__file__).resolve().parent.parent
FRONTENDS = ("core", "property", "buy", "match")


def read_html(fe: str) -> str:
    return (ROOT / f"static/{fe}_admin/index.html").read_text(encoding="utf-8")


def read_js(fe: str) -> str:
    return (ROOT / f"static/{fe}_admin/assets/app.js").read_text(encoding="utf-8")


@pytest.mark.parametrize("fe", FRONTENDS)
def test_frontend_has_login_and_hidden_app(fe):
    html = read_html(fe)
    assert 'id="login-view"' in html
    assert 'id="app-view"' in html
    assert re.search(r'id="app-view"[^>]*\bhidden\b', html)
    assert 'id="login-form"' in html
    assert 'id="admin-username"' in html
    assert 'id="admin-password"' in html
    assert 'type="password"' in html
    assert 'id="logout-btn"' in html


def _strip_comments(js: str) -> str:
    """Il sorgente senza commenti di riga e di blocco.

    Serve perche' i commenti di P26-5 nominano apposta cio' che il codice non
    fa piu' ("NESSUN header Authorization"), e una ricerca sul testo grezzo
    scambierebbe la spiegazione per la cosa spiegata.
    """
    without_block = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$|(?<=[;{}\s])//[^\n]*", "", without_block)


@pytest.mark.parametrize("fe", FRONTENDS)
def test_frontend_credentials_are_memory_only(fe):
    js = read_js(fe)
    # P26-5 HA INVERTITO QUESTA REGOLA, e il test resta a dirlo.
    #
    # Fino a P26-4 questi frontend autenticavano con HTTP Basic: la password
    # viveva in memoria e ogni richiesta la rimetteva in un header. Era la cosa
    # giusta da congelare finche' era il canale in uso. Adesso non lo e' piu':
    # la migrazione alla sessione operatore e' l'obiettivo di P26-5, e un test
    # che pretendesse ancora `encodeBasic` bloccherebbe esattamente cio' che la
    # fase deve ottenere.
    #
    # Il test non viene rimosso: viene girato. Cio' che prima era obbligatorio
    # adesso e' vietato, e viceversa.
    # Nessuna credenziale, nemmeno in memoria: dopo la login la password non
    # esiste piu' da nessuna parte in questo file.
    code = _strip_comments(js).replace("credentials: 'include'", "")
    assert "credentials" not in code
    assert "localStorage" not in js
    assert "sessionStorage" not in js
    assert "document.cookie" not in js
    assert "indexedDB" not in js


@pytest.mark.parametrize("fe", FRONTENDS)
def test_frontend_session_authorization_and_401_flow(fe):
    """P26-5: il canale e' la sessione operatore, non piu' HTTP Basic."""
    js = read_js(fe)
    # P26-5 HA INVERTITO QUESTA REGOLA, e il test resta a dirlo.
    #
    # Fino a P26-4 questi frontend autenticavano con HTTP Basic: la password
    # viveva in memoria e ogni richiesta la rimetteva in un header. Era la cosa
    # giusta da congelare finche' era il canale in uso. Adesso non lo e' piu':
    # la migrazione alla sessione operatore e' l'obiettivo di P26-5, e un test
    # che pretendesse ancora `encodeBasic` bloccherebbe esattamente cio' che la
    # fase deve ottenere.
    #
    # Il test non viene rimosso: viene girato. Cio' che prima era obbligatorio
    # adesso e' vietato, e viceversa.
    # I commenti vengono tolti prima di cercare: una regola di sicurezza va
    # verificata sul codice, non sulla prosa che lo descrive. Un commento che
    # dice "nessun header Authorization" non deve poter far fallire - ne',
    # peggio, far passare - un controllo su cio' che il file fa davvero.
    code = _strip_comments(js)
    for banned in ("encodeBasic", "btoa(", "Authorization"):
        assert banned not in code, f"{fe} costruisce ancora un header Basic: {banned}"
    assert "OperatorSession" in js, f"{fe} non usa la sessione operatore"
    assert re.search(r"status\s*===\s*401", js)
    assert "logout" in js


@pytest.mark.parametrize("fe", FRONTENDS)
def test_frontend_uses_the_operator_auth_contract(fe):
    """P26-5: `/api/admin/check` era il primo passo del vecchio login Basic.

    Se ricomparisse sarebbe il canale Basic che rientra da una porta laterale,
    quindi la sua assenza e' asserita quanto la presenza della login nuova.
    """
    js = read_js(fe)
    # P26-5 HA INVERTITO QUESTA REGOLA, e il test resta a dirlo.
    #
    # Fino a P26-4 questi frontend autenticavano con HTTP Basic: la password
    # viveva in memoria e ogni richiesta la rimetteva in un header. Era la cosa
    # giusta da congelare finche' era il canale in uso. Adesso non lo e' piu':
    # la migrazione alla sessione operatore e' l'obiettivo di P26-5, e un test
    # che pretendesse ancora `encodeBasic` bloccherebbe esattamente cio' che la
    # fase deve ottenere.
    #
    # Il test non viene rimosso: viene girato. Cio' che prima era obbligatorio
    # adesso e' vietato, e viceversa.
    assert "/api/admin/check" not in _strip_comments(js)
    # La login vive nello script condiviso; qui si verifica che il frontend la
    # chiami invece di reimplementarla.
    assert "OperatorSession.login(" in js
    assert "OperatorSession.restore(" in js
    assert "OperatorSession.logout(" in js


def test_backend_is_protected_in_p3():
    main_py = (ROOT / "main.py").read_text(encoding="utf-8")
    # P26-5: `main.py` non importa piu' `require_admin` perche' non lo usa piu'.
    #
    # La protezione che questo test esiste per garantire non cambia - nessuna
    # di queste superfici e' raggiungibile senza una credenziale - ma la
    # credenziale e' la sessione operatore, e l'import era diventato codice
    # morto. Un import inutilizzato dentro il modulo che monta l'applicazione
    # non e' innocuo: e' una porta pronta a una riga di distanza.
    assert "from admin_security import require_admin" not in main_py
    # P26-1 Task 15: core_router moved from require_admin to require_operator.
    # The protection this test exists to guarantee is unchanged - CORE is still
    # unreachable without a credential - but the dependency now accepts BOTH
    # approved channels: an operator session cookie, or the same legacy
    # ADMIN_USER/ADMIN_PASS Basic credential as before, resolved server-side
    # into a Default-Agency-bound scope (design spec D-2). The other four
    # routers below are deliberately untouched.
    assert "app.include_router(core_router, dependencies=[Depends(require_operator)])" in main_py
    # P26-6C added `legacy_basic_agency_context` to the same import. What this
    # assertion protects is that `require_operator` comes from the certified
    # dependency module, not that it is imported alone.
    assert re.search(
        r"^from operator_auth\.dependencies import [^\n]*\brequire_operator\b",
        main_py,
        re.MULTILINE,
    ), "require_operator is no longer imported from operator_auth.dependencies"
    for router_name in ("property_router", "buy_router", "match_router", "proposal_router"):
        assert f"app.include_router({router_name}, dependencies=[Depends(require_authenticated_operator)])" in main_py
