"""A30-4 - la UI dell'Agenda nella OS Shell (`#/agenda`).

Due famiglie di prove, nello stile gia' usato per `/os` (nessuna dipendenza):

* STATICHE sul testo dei moduli: rotta registrata una volta, UNA voce
  "Agenda" in SECTIONS (gate finale A30-4), nessun Mese, nessun trascinamento, solo `/api/appointments`,
  nessun Basic, nessun collegamento finto a stima o lead, `innerHTML` solo con
  markup fisso, `main.js` ridotto al minimo.
* ESEGUITE con node (ESM, moduli copiati come `.mjs`): le funzioni pure del
  modello (settimana lunedi'->domenica, fuso Europe/Rome anche con il browser
  in un altro fuso, cambio d'ora, geometria dei blocchi, sovrapposizioni,
  viste mobile), la traduzione degli errori 401/403/404/409/422 e il client
  `agenda-api.js` contro un `fetch` finto (cookie si', Authorization no, 401
  -> `sessionExpired`, codici e conflitti conservati).

Senza node le prove eseguite sono SKIPPED (da riportare come BLOCKED), mai
passate.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "static" / "os_shell" / "assets"
MAIN_JS = ASSETS / "main.js"
MODEL = ASSETS / "agenda" / "agenda-model.js"
API = ASSETS / "agenda" / "agenda-api.js"
PAGE = ASSETS / "views" / "agenda" / "agenda-page.js"
VIEWS_JS = ASSETS / "components" / "agenda" / "agenda-views.js"
DRAWER = ASSETS / "components" / "agenda" / "agenda-drawer.js"
DIALOGS = ASSETS / "components" / "agenda" / "agenda-dialogs.js"
CSS = ASSETS / "app.css"

AGENDA_FILES = (MODEL, API, PAGE, VIEWS_JS, DRAWER, DIALOGS)
NODE = shutil.which("node")


def _testo(percorso: Path) -> str:
    return percorso.read_text(encoding="utf-8")


def _senza_commenti(js: str) -> str:
    # Prima i commenti di riga: un `core/*.js` scritto in un commento `//`
    # aprirebbe altrimenti un finto blocco /* ... */.
    js = "\n".join(re.sub(r"(^|[^:'\"])//.*$", r"\1", riga) for riga in js.splitlines())
    return re.sub(r"/\*.*?\*/", "", js, flags=re.S)


def _agenda_css() -> str:
    css = _testo(CSS)
    inizio = css.index("/* --- A30-4 Agenda")
    return css[inizio:]


# ---------------------------------------------------------------------------
# ROTTA E BARRA LATERALE
# ---------------------------------------------------------------------------

def test_01_la_rotta_agenda_e_registrata_una_sola_volta():
    main = _testo(MAIN_JS)
    assert len(re.findall(r"registerRoute\('agenda'", main)) == 1
    assert main.count("from './views/agenda/agenda-page.js'") == 1
    assert "registerRoute('agenda', (container, params = []) => renderAgenda(container, params));" in main


def test_02_agenda_e_una_voce_normale_di_sections_una_sola_volta():
    """GATE FINALE A30-4: l'Agenda entra nella sidebar con lo STESSO meccanismo
    delle altre sezioni tenant - una riga in SECTIONS - e nient'altro: nessuna
    costante a parte, nessuna voce aggiunta a mano, nessun push."""
    main = _testo(MAIN_JS)
    inizio = main.index("const SECTIONS = [")
    sezioni = main[inizio:main.index("];", inizio)]
    assert re.findall(r"name:\s*'([a-z]+)'", sezioni) == [
        "oggi", "agenda", "contatti", "immobili", "acquirenti", "abbinamenti",
        "attivita", "automazioni"]
    assert sezioni.count("{ name: 'agenda', label: 'Agenda' },") == 1
    # la costante del workaround non esiste piu', in nessuna forma
    assert "SEZIONE_AGENDA" not in main
    assert "SECTIONS.push" not in main
    assert len(re.findall(r"name:\s*'agenda'", main)) == 1
    # il titolo passa dalla stessa ricerca di prima dell'Agenda
    assert ("const active = [...SECTIONS, SEZIONE_RETE].find((s) => s.name === name);"
            in main)
    # la home tenant resta la prima voce, e la prima voce resta "Oggi"
    assert "const ROTTA_TENANT_INIZIALE = SECTIONS[0].name;" in main
    # e nessun file dell'Agenda disegna voci di navigazione
    for f in AGENDA_FILES:
        assert "nav-item" not in _testo(f) and "#nav" not in _testo(f), f.name


def test_03_main_js_contiene_solo_import_voce_e_rotta():
    righe = [r.strip() for r in _senza_commenti(_testo(MAIN_JS)).splitlines()
             if "agenda" in r.lower() and r.strip()]
    assert righe == [
        "import { renderAgenda } from './views/agenda/agenda-page.js';",
        "{ name: 'agenda', label: 'Agenda' },",
        "registerRoute('agenda', (container, params = []) => renderAgenda(container, params));",
    ]
    # nessun riferimento all'API dal bootstrap
    assert "appointments" not in _testo(MAIN_JS)


def test_04_solo_main_js_importa_la_pagina_e_nessuna_vista_esistente_importa_l_agenda():
    for f in ASSETS.rglob("*.js"):
        if f in AGENDA_FILES or f == MAIN_JS:
            continue
        testo = _testo(f)
        assert "agenda/" not in testo, f.relative_to(ROOT)


# ---------------------------------------------------------------------------
# COSA NON C'E'
# ---------------------------------------------------------------------------

def test_05_nessuna_vista_mese():
    for f in AGENDA_FILES:
        codice = _senza_commenti(_testo(f))
        assert "'month'" not in codice.replace("month: 'long'", "").replace(
            "month: '2-digit'", "").replace("month: 'short'", ""), f.name
        assert not re.search(r"\bMese\b|month-calendar|MonthCalendar|renderMonth", codice), f.name
    assert "month" not in _agenda_css().lower()


def test_06_nessun_trascinamento_ne_ridimensionamento():
    for f in AGENDA_FILES:
        codice = _senza_commenti(_testo(f)).lower()
        for vietato in ("drag", "draggable", "'drop'", "dragover", "mousemove", "pointermove",
                        "pointerdown", "mousedown", "touchmove"):
            assert vietato not in codice, (f.name, vietato)
    css = _agenda_css().lower()
    assert "resize" not in css and "cursor: move" not in css and "ns-resize" not in css


def test_07_solo_api_appointments_e_solo_da_agenda_api():
    for f in AGENDA_FILES:
        codice = _senza_commenti(_testo(f))
        for percorso in re.findall(r"['\"`](/api/[^'\"`$]*)", codice):
            assert percorso.startswith("/api/appointments"), (f.name, percorso)
        if f != API:
            assert "fetch(" not in codice, f.name
            assert "core/api-client" not in codice, f.name
            assert "/api/" not in codice, f.name
    assert "const BASE = '/api/appointments';" in _testo(API)


def test_08_nessun_accesso_a_stime_dettagliate_visite_acquirente_o_google():
    for f in AGENDA_FILES:
        codice = _senza_commenti(_testo(f)).lower()
        for vietato in ("stime_dettagliate", "property_visits", "property-visits", "/visits",
                        "admin/stime", "salva_stima", "google", "booking"):
            assert vietato not in codice, (f.name, vietato)


def test_09_nessun_collegamento_finto_a_stima_o_lead():
    for f in AGENDA_FILES:
        codice = _senza_commenti(_testo(f))
        assert not re.search(r"(?i)apri\s+(la\s+)?(stima|lead)", codice), f.name
        for vietato in ("#/stime", "#/stima", "#/lead", "#/leads"):
            assert vietato not in codice, (f.name, vietato)
    # gli unici collegamenti: cliente e immobile, verso rotte che esistono
    drawer = _testo(DRAWER)
    assert "`#/contatti/${" in drawer and "`#/immobili/${" in drawer
    main = _testo(MAIN_JS)
    assert "registerRoute('contatti'" in main and "registerRoute('immobili'" in main


def test_10_nessun_basic_nessun_authorization_nessuna_agenzia_dal_client():
    for f in AGENDA_FILES:
        codice = _senza_commenti(_testo(f))
        for vietato in ("Authorization", "Basic ", "btoa(", "agency_id", "created_by",
                        "localStorage", "sessionStorage", "document.cookie"):
            assert vietato not in codice, (f.name, vietato)
    assert "credentials: 'include'" in _testo(API)


def test_11_innerhtml_solo_con_markup_fisso():
    for f in (MODEL, API, PAGE, VIEWS_JS, DRAWER):
        assert "innerHTML" not in _senza_commenti(_testo(f)), f.name
    dialoghi = _senza_commenti(_testo(DIALOGS))
    assert dialoghi.count("innerHTML") == 1                      # solo preparaDialog
    # Le sole interpolazioni nei template dei dialog: costanti e condizioni
    # fisse. Nessun dato del server passa di li'.
    ammesse = {
        "corpo", "BLOCCO_ORARIO", "BLOCCO_DISPONIBILITA",
        "action === 'schedule' ? ' *' : ''",
    }
    for espressione in re.findall(r"\$\{([^{}]+)\}", dialoghi):
        e = espressione.strip()
        if e in ammesse:
            continue
        # le interpolazioni fuori dai template HTML (testi via textContent,
        # ISO, orari) non contengono tag
        assert not e.startswith("<"), e
    # il blocco condizionale dell'agente e' markup fisso
    assert "${conAgente ? `<div class=\"form-field\"><label>Agente" in dialoghi


def test_12_rendering_con_dom_e_textcontent():
    for f in (VIEWS_JS, DRAWER, PAGE):
        codice = _testo(f)
        assert "textContent" in codice and "createElement" in codice, f.name
        assert "insertAdjacentHTML" not in codice and "outerHTML" not in codice, f.name


# ---------------------------------------------------------------------------
# UX: SETTIMANA, DOMENICA, MOBILE, VIEWPORT 08-20
# ---------------------------------------------------------------------------

def test_13_css_mobile_nasconde_solo_la_settimana_e_tablet_scorre():
    css = _agenda_css()
    mobile = css[css.index("@media (max-width: 767px)"):]
    assert ".agenda-desktop-only { display: none; }" in mobile
    tablet = css[css.index("@media (min-width: 768px) and (max-width: 1199px)"):
                 css.index("@media (max-width: 767px)")]
    assert ".agenda-grid-week { min-width: 880px; }" in tablet
    assert "overflow: auto" in css
    # nessuna regola che nasconda una colonna (la domenica resta)
    assert "nth-child" not in css and "nth-of-type" not in css


def test_14_la_pagina_non_offre_la_settimana_su_mobile():
    pagina = _testo(PAGE)
    assert "if (!MOBILE_VIEWS.includes(v)) b.classList.add('agenda-desktop-only');" in pagina
    assert "effectiveView(viewFromSlug(params[0]), mobile)" in pagina


def test_15_le_etichette_sono_quelle_del_backend():
    from appointments import state_machine
    from appointments.enums import APPOINTMENT_TYPE_LABELS_IT, DEFAULT_DURATION_MINUTES

    modello = _testo(MODEL)

    def blocco(nome):
        testo = modello[modello.index(f"export const {nome} = Object.freeze({{"):]
        testo = testo[:testo.index("});")]
        return dict(re.findall(r"(\w+): '([^']*)'", testo))

    assert blocco("STATUS_LABELS") == state_machine._ETICHETTE
    assert blocco("TYPE_LABELS") == APPOINTMENT_TYPE_LABELS_IT
    durate = modello[modello.index("export const DEFAULT_DURATION_MINUTES"):]
    durate = durate[:durate.index("});")]
    assert {k: int(v) for k, v in re.findall(r"(\w+): (\d+)", durate)} == DEFAULT_DURATION_MINUTES
    # le azioni del pannello sono quelle della macchina a stati
    azioni = modello[modello.index("export const ACTION_ORDER"):]
    azioni = azioni[:azioni.index(");")]
    assert set(re.findall(r"'(\w+)'", azioni)) == set(state_machine.ACTIONS)


# ---------------------------------------------------------------------------
# ESEGUITE CON NODE
# ---------------------------------------------------------------------------

def _stage(tmp_path: Path) -> Path:
    (tmp_path / "agenda").mkdir()
    (tmp_path / "core").mkdir()
    (tmp_path / "agenda" / "agenda-model.mjs").write_text(_testo(MODEL), encoding="utf-8")
    api = _testo(API)
    assert api.count("from '../core/auth.js'") == 1
    (tmp_path / "agenda" / "agenda-api.mjs").write_text(
        api.replace("from '../core/auth.js'", "from '../core/auth.mjs'"), encoding="utf-8")
    (tmp_path / "core" / "auth.mjs").write_text(
        "export let expired = 0;\nexport function sessionExpired() { expired += 1; }\n",
        encoding="utf-8")
    return tmp_path


def _node(tmp_path: Path, corpo: str, tz: str = "Europe/Rome") -> dict:
    if NODE is None:
        pytest.skip("node non disponibile: prove eseguite BLOCKED, non passate")
    cartella = _stage(tmp_path)
    driver = cartella / "driver.mjs"
    driver.write_text(textwrap.dedent(corpo), encoding="utf-8")
    esito = subprocess.run([NODE, str(driver)], capture_output=True, text=True, timeout=60,
                           env={"TZ": tz, "PATH": "/usr/bin:/bin"})
    assert esito.returncode == 0, esito.stderr
    return json.loads(esito.stdout.strip().splitlines()[-1])


MODELLO = """
import * as m from './agenda/agenda-model.mjs';
const out = {};
out.week = m.weekDays('2026-09-24');
out.weekSunday = m.weekDays('2026-09-27');
out.rangeWeek = m.rangeFor('week', '2026-09-24');
out.rangeDst = m.rangeFor('week', '2026-03-29');
out.rangeDay = m.rangeFor('day', '2026-10-25');
out.rangeList = m.rangeFor('list', '2026-09-24');
out.isoWinter = m.romeIso('2026-10-26', 9, 30);
out.isoSummer = m.romeIso('2026-10-24', 9, 30);
out.dateKey = m.romeDateKey('2026-09-24T22:30:00Z');
out.geom = m.blockGeometry({start_at: '2026-09-24T10:00:00+02:00', end_at: '2026-09-24T11:00:00+02:00'}, '2026-09-24');
out.geomCross = m.blockGeometry({start_at: '2026-09-24T23:00:00+02:00', end_at: '2026-09-25T01:00:00+02:00'}, '2026-09-25');
out.itemsDay = m.itemsForDay([
  {start_at: '2026-09-24T08:00:00Z', end_at: '2026-09-24T09:00:00Z'},
  {start_at: '2026-09-25T08:00:00Z', end_at: '2026-09-25T09:00:00Z'}], '2026-09-24').length;
out.overlap = m.overlapLayout([
  {start_at: '2026-09-24T10:00:00+02:00', end_at: '2026-09-24T11:00:00+02:00'},
  {start_at: '2026-09-24T10:30:00+02:00', end_at: '2026-09-24T11:30:00+02:00'},
  {start_at: '2026-09-24T12:00:00+02:00', end_at: '2026-09-24T13:00:00+02:00'}]);
out.scroll = m.initialScrollTop();
out.viewport = [m.VIEWPORT_START_HOUR, m.VIEWPORT_END_HOUR];
out.views = m.VIEWS;
out.mobile = m.MOBILE_VIEWS;
out.eff = [m.effectiveView('week', true), m.effectiveView('day', true), m.effectiveView(null, true),
           m.effectiveView(null, false), m.effectiveView('month', false), m.effectiveView('list', false)];
out.slug = [m.viewFromSlug('settimana'), m.viewFromSlug('mese')];
out.dateOk = [m.isDateKey('2026-02-29'), m.isDateKey('2028-02-29'), m.isDateKey('x')];
out.title = [m.itemTitle({kind: 'busy', label: 'Occupato', contact_name: 'segreto'}),
             m.itemTitle({kind: 'appointment', contact_name: 'Mario'}),
             m.itemTitle({kind: 'appointment'})];
const E = (status, code, detail) => Object.assign(new Error(detail || ''), {status, code, detail});
out.errors = {
  e401: m.errorMessage(E(401, '', 'x')),
  e403: m.errorMessage(E(403, 'FORBIDDEN_ROLE', 'Solo owner e admin possono riassegnare un appuntamento')),
  e403p: m.errorMessage(E(403, 'PLATFORM_ADMIN_AGENCY_REQUIRED', 'Scegli')),
  e404: m.errorMessage(E(404, 'NOT_FOUND', 'Risorsa non trovata')),
  e409v: m.errorMessage(E(409, 'VERSION_CONFLICT', 'Questo appuntamento e stato modificato')),
  e409c: m.errorMessage(E(409, 'APPOINTMENT_CONFLICT', "Orario non disponibile per l'agente")),
  e409t: m.errorMessage(E(409, 'INVALID_TRANSITION', "Azione non piu' possibile: lo stato e' 'Completato'")),
  e422: m.errorMessage(E(422, 'AGENT_REQUIRED', 'per fissare scegli un agente')),
  e422list: m.errorMessage(E(422, '', '')),
  e500: m.errorMessage(E(500, '', 'Traceback (most recent call last): psycopg2.errors...')),
  net: m.errorMessage(E(0, '', '')),
};
out.reload = [m.errorNeedsReload(E(404)), m.errorNeedsReload(E(409, 'VERSION_CONFLICT')),
              m.errorNeedsReload(E(422, 'AGENT_REQUIRED'))];
console.log(JSON.stringify(out));
"""


@pytest.mark.parametrize("tz", ["Europe/Rome", "UTC", "America/New_York", "Asia/Tokyo"])
def test_20_modello_puro_uguale_in_ogni_fuso_del_browser(tmp_path, tz):
    o = _node(tmp_path, MODELLO, tz)
    # settimana lunedi' -> DOMENICA, sempre sette giorni
    assert o["week"] == ["2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24",
                         "2026-09-25", "2026-09-26", "2026-09-27"]
    assert o["weekSunday"] == o["week"]                # la domenica e' nella SUA settimana
    assert o["rangeWeek"]["from"] == "2026-09-21T00:00:00+02:00"
    assert o["rangeWeek"]["to"] == "2026-09-28T00:00:00+02:00"
    assert o["rangeWeek"]["days"][-1] == "2026-09-27"
    # cambio d'ora: la settimana del 29/03 inizia in inverno e finisce in estate
    assert o["rangeDst"]["from"] == "2026-03-23T00:00:00+01:00"
    assert o["rangeDst"]["to"] == "2026-03-30T00:00:00+02:00"
    assert o["rangeDay"] == {"from": "2026-10-25T00:00:00+02:00",
                             "to": "2026-10-26T00:00:00+01:00", "days": ["2026-10-25"]}
    assert len(o["rangeList"]["days"]) == 7
    assert o["isoWinter"] == "2026-10-26T09:30:00+01:00"
    assert o["isoSummer"] == "2026-10-24T09:30:00+02:00"
    assert o["dateKey"] == "2026-09-25"                  # 22:30Z e' gia' domani a Roma
    assert o["geom"] == {"top": 640, "height": 64}
    assert o["geomCross"] == {"top": 0, "height": 64}
    assert o["itemsDay"] == 1
    assert o["overlap"] == [{"column": 0, "columns": 2}, {"column": 1, "columns": 2},
                            {"column": 0, "columns": 1}]
    assert o["scroll"] == 8 * 64 and o["viewport"] == [8, 20]
    assert o["views"] == ["week", "day", "list"]         # nessun Mese
    assert o["mobile"] == ["list", "day"]
    assert o["eff"] == ["list", "day", "list", "week", "week", "list"]
    assert o["slug"] == ["week", None]
    assert o["dateOk"] == [False, True, False]
    # un impegno di collega mostra SOLO cio' che il server gli ha dato
    assert o["title"] == ["Occupato", "Mario", ""]


def test_21_errori_401_403_404_409_422_leggibili_mai_grezzi(tmp_path):
    o = _node(tmp_path, MODELLO)
    e = o["errors"]
    assert e["e401"] == "Sessione scaduta. Effettua di nuovo il login."
    assert e["e403"] == "Solo owner e admin possono riassegnare un appuntamento"
    assert e["e403p"] == "Scegli un'agenzia per usare l'Agenda."
    assert e["e404"] == "Appuntamento non trovato o non più disponibile."
    assert e["e409v"].startswith("Questo appuntamento è stato modificato da un altro operatore")
    assert e["e409c"] == "Orario non disponibile per l'agente."
    assert e["e409t"].startswith("Azione non piu' possibile")
    assert e["e422"] == "per fissare scegli un agente"
    assert e["e422list"] == "Dati non validi. Controlla i campi e riprova."
    assert e["e500"] == "Errore del server (500). Riprova."   # mai uno stack
    assert e["net"].startswith("Impossibile contattare il server")
    assert o["reload"] == [True, True, False]


CLIENT = """
import * as api from './agenda/agenda-api.mjs';
import * as auth from './core/auth.mjs';
const calls = [];
let scripted = [];
function trap(name) {
  const boom = () => { throw new Error('FORBIDDEN: ' + name); };
  return new Proxy({}, { get: boom, set: boom, has: boom });
}
globalThis.localStorage = trap('localStorage');
globalThis.sessionStorage = trap('sessionStorage');
globalThis.fetch = async (url, options = {}) => {
  calls.push({ url, options: JSON.parse(JSON.stringify(options)) });
  const next = scripted.shift() || { status: 200, body: { items: [] } };
  if (next.network) throw new TypeError('down');
  return { ok: next.status >= 200 && next.status < 300, status: next.status,
           json: async () => next.body ?? null };
};
const out = { calls: [], errors: [] };
async function run(fn) { try { await fn(); } catch (e) {
  out.errors.push({ status: e.status, code: e.code, detail: e.detail ?? null,
    message: e.message, conflicts: e.conflicts, alternatives: e.alternatives,
    currentVersion: e.currentVersion ?? null }); } }
await run(() => api.getCalendar({ from: '2026-09-21T00:00:00+02:00', to: '2026-09-28T00:00:00+02:00' }));
await run(() => api.getList({ from: 'a', to: 'b' }));
await run(() => api.getAgents());
await run(() => api.getAvailability({ userId: 7, from: 'a', to: 'b', duration: 60 }));
await run(() => api.checkAvailability({ assigned_user_id: 7, start_at: 'a', end_at: 'b' }));
await run(() => api.getAppointment(12));
await run(() => api.getEvents(12));
await run(() => api.createAppointment({ appointment_type: 'call', client_request_id: 'k' }));
await run(() => api.patchAppointment(12, { version: 3, notes: 'x' }));
for (const a of ['schedule', 'confirm', 'reschedule', 'reassign', 'cancel', 'complete', 'no_show']) {
  await run(() => api.runAction(12, a, { version: 3 }));
}
out.calls = calls.map((c) => ({ url: c.url, method: c.options.method,
  credentials: c.options.credentials, headers: c.options.headers, body: c.options.body ?? null }));
calls.length = 0;
scripted = [
  { status: 401, body: { detail: 'Non autorizzato' } },
  { status: 403, body: { detail: 'Solo owner e admin', code: 'FORBIDDEN_ROLE' } },
  { status: 404, body: { detail: 'Risorsa non trovata', code: 'NOT_FOUND' } },
  { status: 409, body: { detail: 'Orario non disponibile', code: 'APPOINTMENT_CONFLICT',
                         conflicts: [{ start_at: 'x', end_at: 'y', label: 'Occupato' }],
                         alternatives: [{ start_at: 'p', end_at: 'q' }] } },
  { status: 409, body: { detail: 'modificato', code: 'VERSION_CONFLICT', current_version: 5 } },
  { status: 422, body: { detail: 'start_at: fuso', code: 'TIMEZONE_REQUIRED' } },
  { status: 422, body: { detail: [{ loc: ['query', 'from'] }] } },
  { network: true },
];
for (let i = 0; i < 8; i += 1) await run(() => api.getAppointment(1));
out.expired = auth.expired;
out.afterErrors = calls.length;
let refused = 0;
for (const bad of [0, -1, 'x', 1.5, null]) { try { api.getAppointment(bad); } catch (_e) { refused += 1; } }
out.refused = refused;
try { api.runAction(1, 'delete', {}); } catch (e) { out.unknownAction = e.message; }
console.log(JSON.stringify(out));
"""


def test_22_client_solo_api_appointments_cookie_e_nessun_authorization(tmp_path):
    o = _node(tmp_path, CLIENT)
    attese = [
        ("GET", "/api/appointments/calendar?from=2026-09-21T00%3A00%3A00%2B02%3A00"
                "&to=2026-09-28T00%3A00%3A00%2B02%3A00"),
        ("GET", "/api/appointments?from=a&to=b&limit=200&offset=0"),
        ("GET", "/api/appointments/agents"),
        ("GET", "/api/appointments/availability?user_id=7&from=a&to=b&duration=60&step=30"),
        ("POST", "/api/appointments/availability/check"),
        ("GET", "/api/appointments/12"),
        ("GET", "/api/appointments/12/events"),
        ("POST", "/api/appointments"),
        ("PATCH", "/api/appointments/12"),
        ("POST", "/api/appointments/12/schedule"),
        ("POST", "/api/appointments/12/confirm"),
        ("POST", "/api/appointments/12/reschedule"),
        ("POST", "/api/appointments/12/reassign"),
        ("POST", "/api/appointments/12/cancel"),
        ("POST", "/api/appointments/12/complete"),
        ("POST", "/api/appointments/12/no-show"),
    ]
    assert [(c["method"], c["url"]) for c in o["calls"]] == attese
    for c in o["calls"]:
        assert c["credentials"] == "include"
        assert set(c["headers"]) == {"Content-Type"}           # niente Authorization
        if c["body"]:
            assert "agency_id" not in c["body"]
    assert json.loads(o["calls"][9]["body"]) == {"version": 3}
    assert o["refused"] == 5 and "delete" in o["unknownAction"]


def test_23_client_conserva_codici_conflitti_e_gestisce_il_401(tmp_path):
    o = _node(tmp_path, CLIENT)
    errori = o["errors"]
    assert [e["status"] for e in errori] == [401, 403, 404, 409, 409, 422, 422, 0]
    assert o["expired"] == 1                  # il 401 riporta al login, una volta
    assert errori[1]["code"] == "FORBIDDEN_ROLE"
    assert errori[2]["code"] == "NOT_FOUND"
    assert errori[3]["conflicts"] == [{"start_at": "x", "end_at": "y", "label": "Occupato"}]
    assert errori[3]["alternatives"] == [{"start_at": "p", "end_at": "q"}]
    assert errori[4]["currentVersion"] == 5
    assert errori[5]["code"] == "TIMEZONE_REQUIRED" and errori[5]["detail"] == "start_at: fuso"
    assert errori[6]["detail"] == ""          # un detail non testuale non si mostra grezzo
    assert errori[7]["message"].startswith("Impossibile contattare il server")
    assert o["afterErrors"] == 8               # nessun nuovo tentativo automatico


def test_24_tutti_i_moduli_agenda_sono_sintatticamente_validi():
    if NODE is None:
        pytest.skip("node non disponibile: BLOCKED")
    for f in (*AGENDA_FILES, MAIN_JS):
        esito = subprocess.run([NODE, "--check", str(f)], capture_output=True, text=True)
        assert esito.returncode == 0, (f.name, esito.stderr)


# ---------------------------------------------------------------------------
# DST: orari da muro inesistenti (fix pre-staging)
# ---------------------------------------------------------------------------

DST = """
import * as m from './agenda/agenda-model.mjs';
const casi = [
  ['normale', '2026-09-24', 10, 0],
  ['avanti_0130', '2026-03-29', 1, 30],
  ['avanti_0200', '2026-03-29', 2, 0],
  ['avanti_0230', '2026-03-29', 2, 30],
  ['avanti_0259', '2026-03-29', 2, 59],
  ['avanti_0300', '2026-03-29', 3, 0],
  ['indietro_0230', '2026-10-25', 2, 30],
  // stessa regola in un altro anno, senza nessuna data scritta nel codice
  ['avanti_2027_0230', '2027-03-28', 2, 30],
  ['indietro_2027_0230', '2027-10-31', 2, 30],
];
const out = {};
for (const [nome, key, h, mi] of casi) {
  let iso = null, errore = null;
  try { iso = m.romeIso(key, h, mi); } catch (e) { errore = { name: e.name, message: e.message, status: e.status ?? null }; }
  out[nome] = { exists: m.romeWallTimeExists(key, h, mi), iso, errore };
}
out.mezzanotte = [m.rangeFor('day', '2026-03-29'), m.rangeFor('day', '2026-10-25')];
console.log(JSON.stringify(out));
"""

MESSAGGIO_DST = ("Questo orario non esiste a causa del cambio dell'ora legale. "
                 "Scegli un altro orario.")


@pytest.mark.parametrize("tz", ["Europe/Rome", "UTC", "America/New_York"])
def test_25_orario_inesistente_non_viene_spostato_in_silenzio(tmp_path, tz):
    o = _node(tmp_path, DST, tz)
    validi = {
        "normale": "2026-09-24T10:00:00+02:00",
        "avanti_0130": "2026-03-29T01:30:00+01:00",
        "avanti_0300": "2026-03-29T03:00:00+02:00",
        # ORA AMBIGUA: comportamento corrente congelato - seconda occorrenza,
        # in ora solare (+01:00). Nessun selettore in questo fix.
        "indietro_0230": "2026-10-25T02:30:00+01:00",
        "indietro_2027_0230": "2027-10-31T02:30:00+01:00",
    }
    for nome, iso in validi.items():
        assert o[nome] == {"exists": True, "iso": iso, "errore": None}, (nome, o[nome])
    for nome in ("avanti_0200", "avanti_0230", "avanti_0259", "avanti_2027_0230"):
        assert o[nome]["exists"] is False, nome
        assert o[nome]["iso"] is None, nome                    # niente 03:30+02:00
        assert o[nome]["errore"] == {"name": "NonexistentLocalTimeError",
                                     "message": MESSAGGIO_DST, "status": None}, nome
    # le mezzanotti degli intervalli restano valide nei giorni del cambio
    assert o["mezzanotte"][0]["from"] == "2026-03-29T00:00:00+01:00"
    assert o["mezzanotte"][1]["to"] == "2026-10-26T00:00:00+01:00"


def test_26_nessuna_data_del_cambio_ora_scritta_nel_codice():
    codice = _senza_commenti(_testo(MODEL))
    for vietato in ("2026-03-29", "2026-10-25", "-03-", "-10-", "lastSunday", "ultima domenica"):
        assert vietato not in codice, vietato
    corpo = codice[codice.index("export function romeWallTimeExists"):]
    corpo = corpo[:corpo.index("\n}\n")]
    assert "romeToUtcMs(key, hour, minute)" in corpo and "romeParts(" in corpo


def test_27_ogni_orario_scritto_dall_operatore_passa_da_romeiso_dentro_l_invio():
    """Nuovo, pianifica, sposta e completa: l'unico modo di costruire un
    istante e' `romeIso`, che rifiuta l'orario inesistente; l'errore (senza
    `status`) arriva al dialog come testo e l'invio non parte."""
    dialoghi = _senza_commenti(_testo(DIALOGS))
    assert "toISOString" not in dialoghi and "new Date(" not in dialoghi
    assert "Date.UTC" not in dialoghi and "getTimezoneOffset" not in dialoghi
    chiamate = re.findall(r"romeIso\(([^)]*)\)", dialoghi)
    assert chiamate == [
        "data, inizio[0], inizio[1]", "data, fine[0], fine[1]",        # leggiIntervallo
        "data", "addDays(data, 1", "data, ora[0], ora[1]",                # slot (mezzanotti), completa
    ], chiamate
    # leggiIntervallo serve nuovo appuntamento, pianifica e sposta, sempre
    # dentro la funzione di invio (quindi prima di qualunque richiesta)
    assert dialoghi.count("const { startAt, endAt } = leggiIntervallo(form);") == 2
    assert "collegaInvio(dialogEl, form, async () => {\n    const { startAt, endAt } = leggiIntervallo(form);" in dialoghi
    assert "const { startAt, endAt } = leggiIntervallo(form);\n      const scelto" in dialoghi
    # completa: romeIso dentro il corpo dell'invio
    completa = dialoghi[dialoghi.index("if (action === 'complete') {"):]
    completa = completa[:completa.index("dialogEl.showModal();")]
    assert "collegaInvio(dialogEl, form, () => {" in completa
    assert "corpo.completed_at = romeIso(data, ora[0], ora[1]);" in completa
    # un errore senza `status` si mostra con il suo testo
    assert ("errore.textContent = e && e.status !== undefined ? errorMessage(e) : "
            "(e.message || errorMessage(e));") in dialoghi


# ---------------------------------------------------------------------------
# GATE FINALE - LA VOCE IN SIDEBAR, ESEGUITA
# ---------------------------------------------------------------------------
#
# `main.js` VERO, router e sessione veri, nello stub di DOM e con il `fetch`
# scriptato di P26-4/P27-7 (riusati, non copiati). Si legge la sidebar che
# la Shell disegna davvero, non il testo di SECTIONS.

def _shell():
    from tests import test_p27_7_network_runtime as rt
    return rt


@pytest.fixture(scope="module")
def shell_staged(tmp_path_factory):
    if NODE is None:
        pytest.skip("node non disponibile: prova di runtime NON eseguita (BLOCKED)")
    return _shell()._stage(tmp_path_factory.mktemp("agenda-sidebar"))


_VUOTO = {"items": []}
_CLASSI_NAV = """
console.log(JSON.stringify({
  active: __dom.byId['nav'].children.filter((b) => b.classList.contains('active'))
    .map((b) => b.dataset.route),
  hash: window.location.hash,
}));
"""


def _agenda_ok(n=8):
    rt = _shell()
    return [rt.ok(_VUOTO) for _ in range(n)]


def test_s1_un_tenant_vede_agenda_una_volta_subito_dopo_oggi(shell_staged):
    rt = _shell()
    out = rt.run(shell_staged, rt.REPORT, rt.script(rt.ok(rt.TENANT), *_agenda_ok()), hash="#/oggi")
    assert out["navRoutes"] == ["oggi", "agenda", "contatti", "immobili", "acquirenti",
                                "abbinamenti", "attivita", "automazioni"], out["navRoutes"]
    assert out["navRoutes"].count("agenda") == 1
    assert out["nav"].count("Agenda") == 1
    # Rete resta assente per un tenant
    assert "rete" not in out["navRoutes"]


def test_s2_la_voce_porta_a_agenda_e_la_pagina_si_apre_con_il_suo_titolo(shell_staged):
    rt = _shell()
    scenario = """
      const voce = __dom.byId['nav'].children.find((b) => b.dataset.route === 'agenda');
      voce.dispatch('click');
      // lo stub non emette `hashchange` da solo: lo fa il browser, e i test
      // di P26-4 lo simulano cosi'
      await window._fire('hashchange');
      await __settle(40);
    """
    # `run` legge l'ULTIMA riga stampata: qui e' quella di _CLASSI_NAV.
    out = rt.run(shell_staged, scenario + _CLASSI_NAV,
                 rt.script(rt.ok(rt.TENANT), *_agenda_ok(12)), hash="#/oggi")
    assert out["hash"] == "#/agenda", out
    assert out["active"] == ["agenda"], out


def test_s3_agenda_aperta_a_mano_ha_titolo_agenda_e_voce_attiva(shell_staged):
    rt = _shell()
    out = rt.run(shell_staged, rt.REPORT, rt.script(rt.ok(rt.TENANT), *_agenda_ok(12)),
                 hash="#/agenda")
    assert out["title"] == "Agenda", out["title"]
    assert out["navRoutes"].count("agenda") == 1
    # la pagina parla solo con /api/appointments (oltre alla sessione)
    assert [u for u in out["urls"] if not u.startswith(("/api/operator-auth", "/api/appointments"))] == [], out["urls"]
    assert any(u.startswith("/api/appointments") for u in out["urls"]), out["urls"]


def test_s4_platform_admin_senza_agenzia_resta_sulla_rete_senza_agenda(shell_staged):
    """#/rete invariato: chi non ha una superficie tenant non vede la voce, e
    #/agenda lo rimanda alla Rete come ogni altra sezione tenant."""
    rt = _shell()
    out = rt.run(shell_staged, rt.REPORT,
                 rt.script(rt.ok(rt.PLATFORM_ADMIN), rt.ok(rt.PLATFORM_ME), rt.ok(rt.AGENZIE)),
                 hash="#/agenda")
    assert out["navRoutes"] == ["rete"], out["navRoutes"]
    assert out["title"] == "Rete", out["title"]
    assert not any(u.startswith("/api/appointments") for u in out["urls"]), out["urls"]


def test_s5_un_tenant_su_rete_torna_a_oggi_come_prima(shell_staged):
    rt = _shell()
    out = rt.run(shell_staged, rt.REPORT, rt.script(rt.ok(rt.TENANT), *_agenda_ok()),
                 hash="#/rete")
    assert out["title"] == "Oggi", out["title"]
    assert [u for u in out["urls"] if u.startswith("/api/platform")] == []


def test_s6_una_rotta_sconosciuta_resta_pagina_non_trovata(shell_staged):
    rt = _shell()
    out = rt.run(shell_staged, rt.REPORT, rt.script(rt.ok(rt.TENANT), *_agenda_ok()),
                 hash="#/non-esiste")
    assert out["title"] == "Pagina non trovata", out["title"]
    assert out["navRoutes"].count("agenda") == 1


def test_s7_anonimo_la_sidebar_nel_dom_ha_agenda_una_volta(shell_staged):
    rt = _shell()
    out = rt.run(shell_staged, rt.REPORT, rt.script({"status": 401, "body": {"detail": "no"}}),
                 hash="#/agenda")
    assert out["navRoutes"].count("agenda") == 1
    assert not any(u.startswith("/api/appointments") for u in out["urls"]), out["urls"]
