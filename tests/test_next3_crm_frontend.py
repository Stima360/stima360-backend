from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS_PATH = ROOT / "static" / "core_admin" / "assets" / "app.js"


def js_source() -> str:
    return JS_PATH.read_text(encoding="utf-8")


def test_contact_detail_exposes_open_360_action():
    js = js_source()
    assert "Apri Vista 360" in js
    assert 'id="open-360"' in js
    assert "openContact360(c.id)" in js


def test_contact360_is_registered_as_a_renderable_view():
    js = js_source()
    assert "contact360:['Contact 360'" in js
    assert "contact360:renderContact360" in js
    assert "'contact360'" in js


def test_open_contact360_calls_crm_through_existing_api_wrapper():
    js = js_source()
    assert "async function openContact360" in js
    assert "await api(`/api/crm/contacts/${id}/360`)" in js
    assert "state.selected360" in js


def test_api_wrapper_supports_absolute_api_paths_without_bypassing_auth():
    """P26-5 HA CAMBIATO IL CANALE, NON LA REGOLA.

    La regola che questo test protegge e' sempre la stessa: un percorso
    assoluto `/api/...` deve passare per il wrapper autenticato e non
    scavalcarlo con un `fetch` nudo. Cio' che e' cambiato e' come il wrapper
    autentica - non piu' un header Basic ricostruito dalle credenziali tenute
    in memoria, ma il cookie di sessione che il browser allega da solo.

    Pretendere ancora `headers.Authorization=encodeBasic(...)` significherebbe
    pretendere il canale che P26-5 rimuove.
    """
    js = js_source()
    assert "path.startsWith('/api/')" in js
    # Il percorso assoluto passa comunque per il wrapper autenticato...
    assert "OperatorSession.authFetch(url" in js
    # ...e il wrapper non ricostruisce piu' alcuna credenziale.
    assert "encodeBasic" not in js
    assert "state.credentials" not in js


def test_contact360_renders_all_nine_contract_sections():
    js = js_source()
    for token in (
        "d.contact",
        "d.roles",
        "d.leads",
        "d.properties",
        "d.buy_requests",
        "d.matches",
        "d.visits",
        "d.activities",
        "d.tasks",
    ):
        assert token in js


def test_contact360_has_named_operational_blocks():
    js = js_source()
    for label in (
        "Leads",
        "Immobili",
        "Richieste BUY",
        "Match",
        "Visite",
        "Attività",
        "Task",
    ):
        assert label in js


def test_contact360_renders_empty_state():
    js = js_source()
    assert "Nessun dato" in js


def test_contact360_can_return_to_original_contact_detail():
    js = js_source()
    assert "Torna al contatto" in js
    assert 'id="back-to-contact"' in js
    assert "state.view='contactDetail'" in js or 'state.view = \'contactDetail\'' in js


def test_next3_frontend_does_not_persist_credentials_or_add_new_auth():
    """P26-5: la credenziale non e' piu' nemmeno in memoria.

    NEXT-3 congelava "in memoria e non altrove": era il massimo ottenibile
    finche' il canale era HTTP Basic, che per costruzione richiede la password
    a ogni richiesta. La sessione operatore toglie anche quella - la password
    parte una volta e non torna - quindi l'asserzione si stringe invece di
    allentarsi: da "solo in memoria" a "da nessuna parte".

    Le tre righe sullo storage restano identiche: quel divieto non e' cambiato.
    """
    js = js_source()
    assert "localStorage" not in js
    assert "sessionStorage" not in js
    assert "document.cookie" not in js
    assert "state.credentials" not in js
    assert "encodeBasic" not in js
    # E il canale nuovo c'e' davvero: senza questa riga il test passerebbe
    # anche su un frontend che ha perso del tutto l'autenticazione.
    assert "OperatorSession.login(" in js
    assert "OperatorSession.authFetch(" in js


def test_existing_core_contact_detail_and_actions_remain_present():
    js = js_source()
    assert "function renderContactDetail" in js
    assert 'id="edit-contact"' in js
    assert 'id="contact-new-lead"' in js
    assert 'id="contact-new-activity"' in js
    assert 'id="contact-new-task"' in js
    assert 'id="back-contacts"' in js
