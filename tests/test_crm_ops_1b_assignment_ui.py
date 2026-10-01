"""CRM-OPS-1B - agente assegnato nella scheda contatto (OS Shell).

Solo frontend. Il contratto backend e' quello esistente e NON cambia:

* scrittura: PATCH /api/core/contacts/{id}/assignment, body AssignmentUpdate
  (un solo campo, `assigned_agent_id`, null = nessun agente), gate
  `permissions.may_assign_records` (403 per un agent), bersaglio validato con
  `membership_exists` sull'agenzia del record (400);
* elenco: GET /api/appointments/agents (via `getAgents`, l'unico client di
  quell'API), i membri ATTIVI dell'agenzia della sessione - lo stesso
  predicato di `membership_exists`.

Livelli:
  A. statico - endpoint esatti, nessun campo di agenzia o ruolo inviato,
     esattamente due import dall'Agenda (il client e la regola del ruolo,
     nessuna copia), nessuna stringa `/api/appointments` nella vista
     (sentinella A30 test_33 intatta), la PATCH generica del contatto non
     porta mai assigned_agent_id;
  B. Node - `assignmentErrorMessage` e `canAssignRecords` eseguite davvero;
  D. prova comportamentale ESEGUITA (main.js vero nello stub DOM della Shell):
     agent in sola lettura senza chiedere l'elenco operatori; owner, admin e
     platform admin acting caricano l'elenco e scrivono UNA PATCH solo al
     secondo click; rimozione = null; errori 400/403/404; nessun header o
     campo di agenzia;
  C. coerenza col backend - lo specchio del ruolo coincide con
     `operator_auth.permissions.may_assign_records` su tutta la matrice, e i
     due predicati SQL (elenco e validazione) filtrano la stessa cosa.
"""
from __future__ import annotations

import inspect
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "static" / "os_shell" / "assets"
CONTATTO_JS = ASSETS / "views" / "contatto-dettaglio.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _assignment_block() -> str:
    text = _read(CONTATTO_JS)
    start = text.index("// --- CRM-OPS-1B: agente assegnato")
    end = text.index("// P25.4: ricarica contatto+ruoli dopo una modifica", start)
    return text[start:end]


def _without_comments(code: str) -> str:
    return "\n".join(line for line in code.splitlines() if not line.strip().startswith("//"))


# ---------------------------------------------------------------------------
# A. Statico
# ---------------------------------------------------------------------------

def test_a01_placeholder_in_header_and_block_present():
    text = _read(CONTATTO_JS)
    assert '<div id="contact-assignment"></div>' in text
    header = text[text.index('<div class="contact-header card">'):text.index('<div class="tabs" id="contact-tabs">')]
    assert 'id="contact-assignment"' in header
    assert "function renderAssignment(" in _assignment_block()


def test_a02_write_uses_only_the_dedicated_assignment_endpoint():
    code = _without_comments(_assignment_block())
    patches = re.findall(r"apiPatch\(([^)]*)\)", code)
    assert patches == ["`/api/core/contacts/${contact.id}/assignment`, { assigned_agent_id: target }"], patches
    for forbidden in ("apiPost(", "apiDelete(", "apiPut(", "fetch("):
        assert forbidden not in code, forbidden


def test_a03_operator_list_comes_from_the_single_agenda_client():
    code = _without_comments(_assignment_block())
    assert "await getAgents();" in code
    assert "apiGet(" not in code
    # Nessuna vista nomina l'API Agenda: resta solo in agenda-api.js (A30 test_33).
    assert "/api/appointments" not in _without_comments(_read(CONTATTO_JS))


def test_a04_payload_never_carries_agency_or_role():
    code = _without_comments(_assignment_block())
    for forbidden in ("agency_id", "role:", "is_platform_admin:", "acting:"):
        assert forbidden not in code.split("apiPatch(", 1)[1].split(")", 1)[0], forbidden


def test_a05_generic_contact_patch_still_never_carries_assigned_agent_id():
    text = _read(CONTATTO_JS)
    # La PATCH generica (P25.4) resta identica e il dialog di modifica non ha
    # alcun campo per l'assegnazione.
    assert "apiPatch(`/api/core/contacts/${contact.id}`, payload)" in text
    edit_start = text.index("// --- P25.4: Modifica contatto (ContactUpdate)")
    edit_block = _without_comments(text[edit_start:edit_start + 12000])
    assert "assigned_agent_id" not in edit_block


#: Le SOLE righe della vista che nominano "agenda/": e' l'esenzione chiusa
#: proposta per tests/test_a30_4_agenda_ui.py::test_04 (stesso schema di A31-4).
CRM_OPS_1B_IMPORT_AGENDA = (
    "import { getAgents } from '../agenda/agenda-api.js';",
    "import { canAssignRecords } from '../agenda/agenda-model.js';",
)


def test_a06_agenda_imports_are_exactly_the_client_and_the_role_rule():
    text = _read(CONTATTO_JS)
    righe = [r.strip() for r in text.splitlines() if "agenda/" in r]
    assert righe == list(CRM_OPS_1B_IMPORT_AGENDA)
    imports = [line for line in text.splitlines() if line.startswith("import ")]
    assert "import { getSession } from '../core/auth.js';" in imports
    # Nessuna seconda copia della regola del ruolo nella vista.
    code = _without_comments(text)
    assert "'agency_owner'" not in code and "'agency_admin' ||" not in code


def test_a07_selector_only_rendered_when_may_assign():
    code = _without_comments(_assignment_block())
    assert "const mayAssign = canAssignRecords(getSession());" in code
    # l'elenco operatori si chiede SOLO a chi puo' assegnare
    chiamate = re.findall(r"(?<!function )loadAssignableOperators\(\)", code)
    assert len(chiamate) == 1, chiamate
    assert "if (mayAssign) loadAssignableOperators();" in code
    render = code[code.index("function renderAssignment("):code.index("function bindAssignmentActions(")]
    # Il <select> esiste solo nel ramo `if (mayAssign)`.
    branch = render[render.index("if (mayAssign) {"):render.index("assignmentBox.innerHTML")]
    assert 'id="assignment-select"' in branch
    assert render.count('id="assignment-select"') == 1


def test_a08_two_step_inline_confirm_never_window_confirm():
    code = _without_comments(_assignment_block())
    assert "window.confirm" not in code and "confirm(" not in code.replace("confirming", "")
    assert "assignment.confirming = true;" in code
    assert "Conferma: assegna a" in code


def test_a09_inactive_current_assignee_is_shown_but_not_selectable():
    code = _without_comments(_assignment_block())
    assert "(non attivo)" in code
    assert "selected disabled" in code


def test_a10_reload_after_contact_edit_refreshes_assignment():
    text = _read(CONTATTO_JS)
    reload = text[text.index("async function reloadContactAndRoles()"):]
    reload = reload[:reload.index("\n  }\n")]
    assert "renderAssignment();" in reload


def test_a11_all_dynamic_values_escaped():
    code = _without_comments(_assignment_block())
    render = code[code.index("function renderAssignment("):code.index("function bindAssignmentActions(")]
    for expr in re.findall(r"\$\{([^}]*)\}", render):
        # Ammessi: valori passati da escapeHtml, condizionali che emettono solo
        # letterali (' selected'), e frammenti gia' costruiti qui sopra.
        if expr.strip().startswith(("escapeHtml(", "current === null ?", "Number(o.id) === current ?",
                                    "control ?", "options", "currentInactive")):
            continue
        assert False, f"valore non escapato nel markup: {expr}"


# ---------------------------------------------------------------------------
# B. Node: funzioni pure eseguite davvero
# ---------------------------------------------------------------------------

def _run_js(expression: str, module: Path = CONTATTO_JS) -> None:
    if shutil.which("node") is None:
        pytest.skip("node non disponibile")
    source = _read(ASSETS / "components" / "st-table.js") + "\n" + _read(module)
    script = "const vm=require('node:vm');const assert=require('node:assert/strict');\n"
    script += "let source=" + json.dumps(source) + ";\n"
    script += "source=source.replace(/^import [\\s\\S]*?;$/mg,'').replace(/export /g,'');\n"
    script += ("vm.runInNewContext(source + '\\n' + " + json.dumps(expression)
               + ", {assert, console, Date, Set, Map, process});")
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_b01_role_rule_used_by_the_card_matrix():
    # La funzione e' quella dell'Agenda (non modificata): qui se ne fissa il
    # comportamento per l'uso che ne fa la scheda contatto.
    _run_js(r"""
      assert.equal(canAssignRecords(null), false);
      assert.equal(canAssignRecords(undefined), false);
      assert.equal(canAssignRecords({role:'agent', is_platform_admin:false}), false);
      assert.equal(canAssignRecords({role:'agency_owner', is_platform_admin:false}), true);
      assert.equal(canAssignRecords({role:'agency_admin', is_platform_admin:false}), true);
      assert.equal(canAssignRecords({role:null, is_platform_admin:false}), false);
      assert.equal(canAssignRecords({role:'agent', is_platform_admin:true, acting:null}), false);
      assert.equal(canAssignRecords({role:null, is_platform_admin:true}), false);
      assert.equal(canAssignRecords({role:'agent', is_platform_admin:true, acting:{agency_id:3}}), true);
    """, module=ASSETS / "agenda" / "agenda-model.js")


def test_b02_error_messages():
    _run_js(r"""
      const e = (status, message='') => Object.assign(new Error(message), {status});
      assert.equal(assignmentErrorMessage(e(403, 'Questa operazione richiede un ruolo di amministrazione dell\'agenzia.')),
                   'Questa operazione richiede un ruolo di amministrazione dell\'agenzia.');
      assert.match(assignmentErrorMessage(e(403)), /permessi/);
      assert.match(assignmentErrorMessage(e(404, 'not found')), /non più disponibile/);
      const four = assignmentErrorMessage(e(400, 'assigned_agent_id is not an active member of this record\'s agency'));
      assert.match(four, /membro attivo/);
      assert.doesNotMatch(four, /assigned_agent_id/);
      assert.match(assignmentErrorMessage(e(422)), /non valida/);
      assert.equal(assignmentErrorMessage(e(0, 'Impossibile contattare il server. Verifica la connessione.')),
                   'Impossibile contattare il server. Verifica la connessione.');
      assert.match(assignmentErrorMessage(null), /Errore nel salvataggio/);
    """)


# ---------------------------------------------------------------------------
# C. Coerenza col backend (nessuna modifica: solo lettura del codice)
# ---------------------------------------------------------------------------

def test_c01_role_mirror_matches_backend_may_assign_records():
    from operator_auth import permissions
    from operator_auth.enums import AGENCY_ROLES

    mirrored = {"agency_owner", "agency_admin"}
    for role in tuple(AGENCY_ROLES) + (None, "unknown"):
        assert permissions.may_assign_records(role, False) == (role in mirrored), role
    assert permissions.may_assign_records(None, True) is True
    # Il ramo platform admin del client richiede `acting`: e' l'unico modo in
    # cui la Shell sa che opera dentro un'agenzia (core/auth.js
    # ::canUseTenantSurface); senza agenzia il server rifiuta comunque.
    assert set(AGENCY_ROLES) >= mirrored


def test_c02_list_and_validation_filter_the_same_memberships():
    from appointments import repository as appointments_repository
    from operator_auth import repository as operator_repository

    listing = inspect.getsource(appointments_repository.agents)
    validation = inspect.getsource(operator_repository.membership_exists)
    for source in (listing, validation):
        squashed = " ".join(source.split())
        assert "agency_memberships" in squashed
        assert "status = 'active'" in squashed
    assert "m.agency_id = %s" in listing
    assert "agency_id = %s" in validation


def test_c03_assignment_endpoint_contract_unchanged():
    from core import router as core_router
    from core.schemas import AssignmentUpdate

    fields = getattr(AssignmentUpdate, "model_fields", None) or AssignmentUpdate.__fields__
    assert set(fields) == {"assigned_agent_id"}
    paths = {(r.path, tuple(sorted(r.methods))) for r in core_router.router.routes}
    assert ("/api/core/contacts/{contact_id}/assignment", ("PATCH",)) in paths


# ---------------------------------------------------------------------------
# D. Prova comportamentale ESEGUITA: `main.js` vero, router, sessione e vista
#    veri, dentro lo stub di DOM di P26-4/P27-7 con il `fetch` instradato di
#    A30-5 e la <select> letta per proprieta' di A30-13B (riusati, non
#    copiati - stesso schema di tests/test_a31_4_buyer_visits_ui_runtime.py).
#    Nessun database: il server e' il `fetch` instradato.
#    Senza node le prove sono SKIPPED (da riportare come BLOCKED), mai passate.
# ---------------------------------------------------------------------------

from tests import test_a30_5_create_ui as a30_5  # noqa: E402
from tests import test_a30_13b_quick_booking_ui as a30_13b  # noqa: E402
from tests.test_a30_5_create_ui import staged  # noqa: E402,F401  (fixture riusata)

SCHEDA = "#/contatti/159"
URL_ASSEGNAZIONE = "/api/core/contacts/159/assignment"
OPERATORI = {"items": [
    {"id": 3, "name": "Anna Agente", "role": "agent", "is_me": True},
    {"id": 4, "name": "Bruno Collega", "role": "agent", "is_me": False},
    {"id": 5, "name": "Carla Titolare", "role": "agency_owner", "is_me": False},
]}


def _contatto(assegnato):
    return {"contact": {"id": 159, "contact_type": "person", "first_name": "Prova",
                        "last_name": "Uno", "display_name": "Prova Uno",
                        "assigned_agent_id": assegnato},
            "roles": [], "leads": [], "properties": [], "buy_requests": [], "matches": [],
            "visits": [], "activities": [], "tasks": []}


def _rotte_scheda(sessione, *, assegnato=3, patch=None):
    rt = a30_5._rt()
    patch = patch or ({"status": 200, "body": {"id": 159, "assigned_agent_id": 4}},)
    voci = [
        ("GET", "/api/operator-auth/me", [rt.ok(a30_13b._sessione(sessione))]),
        ("GET", "/api/crm/contacts/159/360", [rt.ok(_contatto(assegnato))]),
        ("GET", "/api/appointments/agents", [rt.ok(OPERATORI)]),
        ("PATCH", URL_ASSEGNAZIONE, list(patch)),
    ]
    return "\n".join(f"__route({json.dumps(m)}, {json.dumps(p)}, ...{json.dumps(r)});"
                     for m, p, r in voci)


D_HELPERS = r"""
const B = () => __dom.byId['content'].querySelector('#contact-assignment');
const sel = () => B().querySelector('#assignment-select');
const btn = () => B().querySelector('#assignment-save-btn');
function stato() {
  return {
    testo: B() ? B().visibleText() : null,
    selettore: !!sel(), bottone: !!btn(),
    valore: sel() ? sel().value : null,
    opzioni: sel() ? sel().querySelectorAll('option').map((o) => o.getAttribute('value')) : [],
    bottoneTesto: btn() ? btn().textContent : null,
    bottoneDisabilitato: btn() ? btn().disabled === true : null,
    errore: B() && B().querySelector('#assignment-error') ? B().querySelector('#assignment-error').textContent : null,
    messaggio: B() && B().querySelector('#assignment-message') ? B().querySelector('#assignment-message').textContent : null,
  };
}
async function scegli(v) { sel().value = String(v); sel().dispatch('change'); await wait(); }
// "Nessun agente" ha value="": lo stub di P27-7 tratta `_value === ''` come
// "non impostato" e ricade sull'opzione marcata `selected` nel markup, mentre
// un browser restituisce ''. Qui si seleziona l'opzione come la legge la
// <select> di A30-13B (`option.selected`), cioe' come farebbe l'utente.
async function scegliNessuno() {
  sel().querySelectorAll('option').forEach((o) => { o.selected = o.getAttribute('value') === ''; });
  sel()._value = undefined; sel().dispatch('change'); await wait();
}
async function premi() { btn().dispatch('click'); await wait(); }
"""


def _run_scheda(staged, scenario, rotte):  # noqa: F811
    rt = a30_5._rt()
    driver = staged.parent / "driver-crm-ops-1b.mjs"
    driver.write_text(
        a30_5._dom() + rt.FETCH + a30_13b._extra_dom() + a30_5.IS_CONNECTED + a30_5.ROUTED_FETCH
        + f"\n{rotte}\n"
        + f"window.location.hash = '{SCHEDA}';\n"
        + f"await import('{(staged / 'main.js').as_posix()}');\n"
        + "await __settle(40);\n"
        + a30_5.HELPERS + D_HELPERS + scenario + "\n",
        encoding="utf-8")
    esito = subprocess.run([a30_5.NODE, str(driver)], capture_output=True, text=True, timeout=60,
                           cwd=staged.parent, env={"TZ": "Europe/Rome", "PATH": "/usr/bin:/bin"})
    if esito.returncode != 0:
        raise AssertionError(f"driver node fallito:\n{esito.stderr[-3000:]}\n{esito.stdout[-1500:]}")
    return json.loads(esito.stdout.strip().splitlines()[-1])


def _chiamate(out, metodo, url):
    return [c for c in out["calls"] if c["m"] == metodo and c["url"] == url]


def _scritture(out):
    return [c for c in out["calls"] if c["m"] in ("POST", "PATCH", "PUT", "DELETE")]


node_richiesto = pytest.mark.skipif(
    a30_5.NODE is None, reason="node non disponibile: prove CRM-OPS-1B D NON eseguite (BLOCKED)")


@node_richiesto
def test_d01_agent_sola_lettura_nessun_elenco_nessun_selettore(staged):  # noqa: F811
    out = _run_scheda(staged, "await wait(); report({ s: stato() });", _rotte_scheda("agent"))
    s = out["s"]
    assert "Agente assegnato:" in s["testo"] and "Tu" in s["testo"]
    assert s["selettore"] is False and s["bottone"] is False
    # l'elenco operatori NON viene chiesto, e nulla viene scritto
    assert _chiamate(out, "GET", "/api/appointments/agents") == []
    assert _scritture(out) == []
    # la scheda e' stata davvero caricata dal 360
    assert len(_chiamate(out, "GET", "/api/crm/contacts/159/360")) >= 1


@node_richiesto
@pytest.mark.parametrize("sessione", ["agency_owner", "agency_admin", "supreme"])
def test_d02_chi_assegna_carica_l_elenco_e_salva_solo_dopo_conferma(staged, sessione):  # noqa: F811
    scenario = r"""
      await wait();
      const iniziale = stato();
      await scegli(4);
      const scelto = stato();
      await premi();                                   // primo click: solo conferma
      const dopoPrimo = { s: stato(), patch: chiamate().filter((c) => c.m === 'PATCH').length };
      await premi(); await wait();                     // secondo click: scrive
      report({ iniziale, scelto, dopoPrimo, finale: stato() });
    """
    out = _run_scheda(staged, scenario, _rotte_scheda(sessione))
    assert len(_chiamate(out, "GET", "/api/appointments/agents")) == 1
    i = out["iniziale"]
    assert i["selettore"] is True
    assert i["opzioni"] == ["", "3", "4", "5"]
    assert i["valore"] == "3"
    assert i["bottoneDisabilitato"] is True
    assert out["scelto"]["bottoneDisabilitato"] is False
    # il primo click non scrive nulla e chiede conferma
    assert out["dopoPrimo"]["patch"] == 0
    assert out["dopoPrimo"]["s"]["bottoneTesto"].startswith("Conferma: assegna a Bruno Collega")
    # il secondo click scrive UNA volta, sull'endpoint dedicato, con il solo campo
    patch = _chiamate(out, "PATCH", URL_ASSEGNAZIONE)
    assert len(patch) == 1
    assert patch[0]["body"] == {"assigned_agent_id": 4}
    assert _scritture(out) == patch
    f = out["finale"]
    assert "Bruno Collega" in f["testo"] and f["valore"] == "4"
    assert f["messaggio"] == "Assegnazione salvata."


@node_richiesto
def test_d03_cambiare_scelta_annulla_la_conferma_senza_scrivere(staged):  # noqa: F811
    scenario = r"""
      await wait();
      await scegli(4); await premi(); await scegli(3);
      report({ s: stato() });
    """
    out = _run_scheda(staged, scenario, _rotte_scheda("agency_owner"))
    assert out["s"]["bottoneTesto"] == "Assegna" and out["s"]["bottoneDisabilitato"] is True
    assert _scritture(out) == []


@node_richiesto
def test_d04_rimuovere_l_agente_manda_null(staged):  # noqa: F811
    scenario = r"""
      await wait();
      await scegliNessuno(); await premi();
      const testoConferma = stato().bottoneTesto;
      await premi(); await wait();
      report({ testoConferma, finale: stato() });
    """
    out = _run_scheda(staged, scenario, _rotte_scheda(
        "agency_admin", patch=({"status": 200, "body": {"id": 159, "assigned_agent_id": None}},)))
    assert out["testoConferma"] == "Conferma: rimuovi agente"
    assert [c["body"] for c in _chiamate(out, "PATCH", URL_ASSEGNAZIONE)] == [{"assigned_agent_id": None}]
    assert "Nessun agente assegnato" in out["finale"]["testo"]


@node_richiesto
@pytest.mark.parametrize("status,detail,atteso", [
    (400, "assigned_agent_id is not an active member of this record's agency", "membro attivo"),
    (403, "Questa operazione richiede un ruolo di amministrazione dell'agenzia.", "ruolo di amministrazione"),
    (404, "Risorsa non trovata", "non più disponibile"),
])
def test_d05_errore_del_server_mostrato_valore_invariato(staged, status, detail, atteso):  # noqa: F811
    scenario = r"""
      await wait();
      await scegli(4); await premi(); await premi(); await wait();
      report({ s: stato() });
    """
    out = _run_scheda(staged, scenario, _rotte_scheda(
        "agency_owner", patch=({"status": status, "body": {"detail": detail}},)))
    s = out["s"]
    assert atteso in s["errore"]
    assert "assigned_agent_id" not in s["errore"]
    assert s["valore"] == "3" and "Anna Agente" in s["testo"]
    assert len(_chiamate(out, "PATCH", URL_ASSEGNAZIONE)) == 1


@node_richiesto
def test_d06_nessuna_richiesta_porta_agenzia_ruolo_o_authorization(staged):  # noqa: F811
    scenario = r"""
      await wait(); await scegli(5); await premi(); await premi(); await wait();
      report({});
    """
    out = _run_scheda(staged, scenario, _rotte_scheda("agency_owner"))
    for c in out["calls"]:
        assert "agency_id" not in c["url"], c["url"]
        assert "Authorization" not in c["headers"], c
        assert c["cred"] == "include", c
    assert [c["body"] for c in _chiamate(out, "PATCH", URL_ASSEGNAZIONE)] == [{"assigned_agent_id": 5}]
    # la PATCH generica del contatto non parte mai
    assert _chiamate(out, "PATCH", "/api/core/contacts/159") == []
