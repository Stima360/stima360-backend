"""P30 - la pagina pubblica di prenotazione `/prenota/{token}`, ESEGUITA.

Tre livelli, nessun database (quello e' in
`tests/test_p30_public_booking_page_postgres.py`):

1. SERVIRE LA PAGINA (`public_booking/page.py`, montata in `main.py`): lo
   stesso HTML per qualunque token ben formato, 404 per tutto il resto, e su
   ogni risposta `no-store`, `no-referrer`, `noindex` e una CSP che ammette
   solo la stessa origine.
2. IL MODELLO PURO (`booking-model.js`): token dalla URL, finestre di 7 giorni
   di Roma mai oltre le 168 ore che l'API accetta (cambio d'ora), slot SOLO
   dal server, dati cliente, corpo del submit.
3. LA PAGINA VERA (`booking-page.js`) dentro lo stub di DOM di P26-4 (riusato,
   non copiato: le sue trappole su localStorage/sessionStorage/indexedDB/cookie
   fanno fallire qualunque accesso), con un `fetch` scriptato: caricamento,
   link non disponibile, privacy dei metadata, slot reali, zero slot, scelta,
   validazione, invio, orario preso nel frattempo, doppio click, retry dopo un
   errore di rete, nessun token persistito o loggato, nessun id interno
   mostrato, layout mobile, tastiera.
4. MUTAZIONI: la pagina viene alterata apposta (token in console/DOM/storage,
   doppio invio, orario inventato, id interno mostrato) e le stesse verifiche
   DEVONO accorgersene.

Senza node le prove JavaScript sono SKIPPED (da riportare come BLOCKED), mai passate.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGINA = ROOT / "static" / "public_booking"
ASSETS = PAGINA / "assets"
PAGE_JS = ASSETS / "booking-page.js"
MODEL_JS = ASSETS / "booking-model.js"
CSS = ASSETS / "booking.css"
INDEX = PAGINA / "index.html"
NODE = shutil.which("node")

richiede_node = pytest.mark.skipif(
    NODE is None, reason="node non disponibile: prove P30 della pagina NON eseguite (BLOCKED)")

#: Valori finti, non segreti. Distinguibili, cosi' una fuga si vede.
TOKEN = "TOKENdiPROVA_" + "k" * 31
SUB1 = "SUBMISSIONuno_" + "s" * 30
SUB2 = "SUBMISSIONdue_" + "t" * 30
ORIGINE = "https://crm.example.test"

#: lunedi' 5 ottobre 2026, 08:00 a Roma: tutte le finestre partono da qui.
ADESSO = "2026-10-05T06:00:00Z"

INFO = {"agency_name": "Agenzia Mare Blu", "agent_name": "Luca Bianchi",
        "appointment_type": "seller_meeting",
        "appointment_type_label": "Appuntamento proprietario",
        "duration_minutes": 60, "submission_token": SUB1}


def _slot(inizio_utc: str, minuti: int = 60) -> dict:
    from datetime import datetime, timedelta
    s = datetime.fromisoformat(inizio_utc)
    return {"start_at": s.isoformat(), "end_at": (s + timedelta(minutes=minuti)).isoformat()}


# martedi' 6 ottobre 09:00 e 09:15, giovedi' 8 ottobre 15:00 (ora di Roma)
SLOT_SETTIMANA = {"slots": [_slot("2026-10-06T07:00:00+00:00"), _slot("2026-10-06T07:15:00+00:00"),
                            _slot("2026-10-08T13:00:00+00:00")]}
VUOTO = {"slots": []}
CONFERMATA = {"status": "scheduled", "start_at": "2026-10-06T07:15:00+00:00",
              "end_at": "2026-10-06T08:15:00+00:00", "appointment_type": "seller_meeting"}
NON_DISPONIBILE = {"available": False}


# ---------------------------------------------------------------------------
# 1 - SERVIRE LA PAGINA
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def pagina():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from public_booking.page import PAGE_PREFIX, PublicBookingPage
    app = FastAPI()
    app.mount(PAGE_PREFIX, PublicBookingPage(), name="public-booking-page")
    return TestClient(app)


def test_01_la_pagina_e_la_stessa_per_qualunque_token_e_non_lo_contiene(pagina):
    from public_booking.page import PAGE_HEADERS
    corpi = []
    for token in (TOKEN, "inventato-" + "x" * 30, "a" * 43, TOKEN + "/"):
        r = pagina.get(f"/prenota/{token}")
        assert r.status_code == 200, token
        assert r.headers["content-type"].startswith("text/html")
        for nome, valore in PAGE_HEADERS.items():
            assert r.headers[nome] == valore, nome
        assert token.strip("/") not in r.text          # la pagina non porta il token
        corpi.append(r.content)
    assert len(set(corpi)) == 1                        # nessuna enumerazione possibile
    assert corpi[0] == INDEX.read_bytes()


def test_02_tutto_il_resto_e_404_con_le_stesse_intestazioni(pagina):
    from public_booking.page import PAGE_HEADERS
    for percorso in ("/prenota/", "/prenota/corto", "/prenota/index.html",
                     "/prenota/assets/non-esiste.js", f"/prenota/{TOKEN}/altro",
                     "/prenota/%2e%2e/main.py", f"/prenota/{TOKEN}.html"):
        r = pagina.get(percorso)
        assert r.status_code == 404, percorso
        assert r.headers["cache-control"] == "no-store", percorso
        assert "STIMA360" not in r.text
    assert {k.lower() for k in PAGE_HEADERS} <= set(pagina.get("/prenota/corto").headers)
    assert pagina.post(f"/prenota/{TOKEN}").status_code == 405


def test_03_gli_asset_sono_serviti_con_le_stesse_intestazioni(pagina):
    for nome, tipo in (("booking-page.js", "javascript"), ("booking-model.js", "javascript"),
                       ("booking.css", "text/css")):
        r = pagina.get(f"/prenota/assets/{nome}")
        assert r.status_code == 200 and tipo in r.headers["content-type"], nome
        assert r.headers["cache-control"] == "no-store"
        assert r.headers["referrer-policy"] == "no-referrer"


def test_04_csp_solo_stessa_origine_niente_terzi():
    from public_booking.page import PAGE_HEADERS
    csp = PAGE_HEADERS["Content-Security-Policy"]
    assert "default-src 'none'" in csp and "connect-src 'self'" in csp
    assert "script-src 'self'" in csp and "unsafe" not in csp
    assert "frame-ancestors 'none'" in csp and "form-action 'none'" in csp
    assert "http" not in csp


def test_05_main_monta_la_pagina_su_prenota_come_mount_statico():
    """Un mount come `/owner`: nessuna rotta API nuova (l'inventario P26-5
    delle operazioni pubbliche resta quello di A30-12)."""
    import main
    from fastapi.testclient import TestClient
    from starlette.routing import Mount

    from public_booking.page import PublicBookingPage
    montati = [r for r in main.app.routes if isinstance(r, Mount) and r.path == "/prenota"]
    assert len(montati) == 1 and isinstance(montati[0].app, PublicBookingPage)
    assert not [p for p in main.app.openapi()["paths"] if p.startswith("/prenota")]
    r = TestClient(main.app).get(f"/prenota/{TOKEN}")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"


def test_06_html_mobile_nessuna_risorsa_esterna_nessuno_script_inline():
    html = INDEX.read_text(encoding="utf-8")
    assert '<html lang="it">' in html
    assert 'name="viewport" content="width=device-width, initial-scale=1' in html
    assert '<meta name="referrer" content="no-referrer">' in html
    assert not re.search(r"(?i)(src|href)=[\"']https?:", html)       # niente CDN/font/analytics
    assert re.findall(r"<script[^>]*>", html) == [
        '<script type="module" src="/prenota/assets/booking-page.js">']
    assert "style=" not in html and "<style" not in html              # CSP: niente inline
    assert 'id="booking-app"' in html


def test_07_il_codice_della_pagina_non_ha_canali_di_fuga():
    """Statico, sul sorgente senza commenti: nessuno storage, nessun cookie,
    nessuna console, nessun beacon, nessun innerHTML, nessuna URL assoluta,
    un solo `fetch` e solo verso l'API A30-12."""
    for f in (PAGE_JS, MODEL_JS):
        codice = _senza_commenti(f.read_text(encoding="utf-8"))
        for vietato in ("localStorage", "sessionStorage", "indexedDB", "document.cookie",
                        "console.", "sendBeacon", "innerHTML", "outerHTML", "insertAdjacentHTML",
                        "http://", "https://", "XMLHttpRequest", "WebSocket", "postMessage",
                        "history.", "window.name", "dataset.token", "eval(", "new Function"):
            assert vietato not in codice, (f.name, vietato)
    pagina = _senza_commenti(PAGE_JS.read_text(encoding="utf-8"))
    assert pagina.count("fetch(") == 1
    assert "fetch(API_BASE + encodeURIComponent(token) + percorso, opzioni)" in pagina
    assert "credentials: 'omit'" in pagina and "cache: 'no-store'" in pagina
    assert "referrerPolicy: 'no-referrer'" in pagina
    assert "export const API_BASE = '/api/public/booking/';" in MODEL_JS.read_text(encoding="utf-8")


def test_08_css_mobile_first():
    css = CSS.read_text(encoding="utf-8")
    assert "width: min(100%, 640px)" in css                 # mai piu' largo dello schermo
    assert "overflow-x: hidden" in css
    assert "repeat(auto-fill, minmax(88px, 1fr))" in css     # griglia orari che va a capo
    for regola in (".slot-btn {", ".btn {", ".input {"):
        blocco = css[css.index(regola):css.index("}", css.index(regola))]
        assert re.search(r"min-height: (4[4-9]|[5-9]\d)px", blocco), regola   # tocco >= 44px
    assert "font-size: 16px" in css                          # niente zoom automatico su iOS
    assert "@media (min-width: 560px)" in css                # il desktop e' l'eccezione
    larghezze = [int(x) for x in re.findall(r"(?<![-\w])width:\s*(\d+)px", css)]
    assert all(w <= 64 for w in larghezze), larghezze        # nessuna larghezza fissa ampia


def _senza_commenti(codice: str) -> str:
    codice = re.sub(r"/\*.*?\*/", "", codice, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$|(?<=[;{}),])\s*//.*$", "", codice)


# ---------------------------------------------------------------------------
# 2 - IL MODELLO PURO
# ---------------------------------------------------------------------------

def _node_modello(script: str):
    driver = f"const m = await import('{MODEL_JS.as_posix()}');\n{script}\n"
    esito = subprocess.run([NODE, "--input-type=module", "-e", driver], capture_output=True,
                           text=True, timeout=60, env={"TZ": "UTC", "PATH": "/usr/bin:/bin"})
    if esito.returncode != 0:
        raise AssertionError(esito.stderr[-3000:])
    return json.loads(esito.stdout.strip().splitlines()[-1])


@richiede_node
def test_09_token_dalla_url_solo_nella_forma_attesa():
    out = _node_modello(f"""console.log(JSON.stringify([
      '/prenota/{TOKEN}', '/prenota/{TOKEN}/', '/prenota/corto', '/prenota/{TOKEN}/x',
      '/api/public/booking/{TOKEN}', '/prenota/a%2Fb{"x" * 20}', '/prenota/<script>{"x" * 20}',
    ].map(m.tokenDaPercorso)));""")
    assert out == [TOKEN, TOKEN, None, None, None, None, None]


@richiede_node
def test_10_finestre_di_roma_mai_oltre_le_168_ore_dell_api():
    """Il fuso del processo e' UTC apposta: conta solo Europe/Rome."""
    out = _node_modello("""
      const f = (d, adesso = 0) => m.finestra(d, adesso);
      console.log(JSON.stringify({
        normale: f('2026-10-05'),
        cambioOttobre: f('2026-10-19'),     // 7 giorni di Roma = 169 ore: se ne chiedono 6
        dopo: f('2026-10-25'),
        marzo: f('2027-03-22'),             // 7 giorni = 167 ore: vanno bene
        daAdesso: f('2026-10-05', Date.parse('2026-10-05T06:00:00Z')),
        passata: f('2026-09-01', Date.parse('2026-10-05T06:00:00Z')),
        ore: ['2026-10-05', '2026-10-19', '2026-10-20', '2026-10-25', '2027-03-22', '2027-03-28']
          .map((d) => { const x = f(d); return (Date.parse(x.a) - Date.parse(x.da)) / 3600000; }),
      }));""")
    assert out["normale"] == {"da": "2026-10-04T22:00:00.000Z", "a": "2026-10-11T22:00:00.000Z",
                              "inizio": "2026-10-05", "fine": "2026-10-12"}
    assert out["cambioOttobre"] == {"da": "2026-10-18T22:00:00.000Z", "a": "2026-10-24T22:00:00.000Z",
                                    "inizio": "2026-10-19", "fine": "2026-10-25"}
    assert out["dopo"]["da"] == "2026-10-24T22:00:00.000Z" and out["dopo"]["fine"] == "2026-10-31"
    assert out["marzo"]["fine"] == "2027-03-29"
    assert out["daAdesso"]["da"] == "2026-10-05T06:00:00.000Z"      # mai il passato
    assert out["passata"] is None
    assert all(h <= 168 for h in out["ore"]), out["ore"]


@richiede_node
def test_11_slot_solo_dal_server_raggruppati_per_giorno_di_roma():
    out = _node_modello("""
      const s = m.slotDelServer({ slots: [
        { start_at: '2026-10-06T22:30:00+00:00', end_at: '2026-10-06T23:30:00+00:00' },   // 7 ott 00:30 Roma
        { start_at: '2026-10-06T07:00:00+00:00', end_at: '2026-10-06T08:00:00+00:00', agency_id: 9 },
        { start_at: 'non una data', end_at: '2026-10-06T08:00:00+00:00' },
        { start_at: '2026-10-06T09:00:00+00:00', end_at: '2026-10-06T08:00:00+00:00' },
        null,
      ] });
      console.log(JSON.stringify({
        chiavi: s.map((x) => Object.keys(x).sort()),
        giorni: m.giorniConSlot(s).map((g) => [g.giorno, g.slot.map((x) => m.ora(x.inizioMs))]),
        vuoto: m.slotDelServer({}), nullo: m.slotDelServer(null),
      }));""")
    assert out["chiavi"] == [["end_at", "fineMs", "inizioMs", "start_at"]] * 2    # agency_id scartato
    assert out["giorni"] == [["2026-10-06", ["09:00"]], ["2026-10-07", ["00:30"]]]
    assert out["vuoto"] == [] and out["nullo"] == []


@richiede_node
def test_12_metadata_solo_i_campi_ammessi():
    out = _node_modello(f"""console.log(JSON.stringify([
      m.infoPubblica({json.dumps({**INFO, "agency_id": 7, "user_id": 8, "token_hash": "h"})}),
      m.infoPubblica({{ agency_name: 'X' }}),
      m.infoPubblica(null),
    ]));""")
    assert out[0] == {"agenzia": "Agenzia Mare Blu", "agente": "Luca Bianchi",
                      "tipo": "Appuntamento proprietario", "durata": 60, "submissionToken": SUB1}
    assert out[1] is None and out[2] is None


@richiede_node
def test_13_dati_cliente_e_corpo_del_submit():
    out = _node_modello(f"""
      const s = m.slotDelServer({json.dumps(SLOT_SETTIMANA)}).slice();
      const dati = m.controllaCliente({{ nome: '  Mario Rossi ', telefono: '+39 333 123 4567', email: '' }}).dati;
      const inventato = {{ ...s[0], start_at: '2026-10-06T07:05:00+00:00' }};
      let rifiuto = null;
      try {{ m.corpoPrenotazione('{SUB1}', inventato, s, dati); }} catch (e) {{ rifiuto = e.message; }}
      console.log(JSON.stringify({{
        errori: [
          m.controllaCliente({{ nome: '', telefono: '', email: '' }}).errori,
          m.controllaCliente({{ nome: 'x'.repeat(121), telefono: 'abc', email: 'no' }}).errori,
          m.controllaCliente({{ nome: 'A', telefono: '12345', email: '' }}).errori,
        ],
        corpo: m.corpoPrenotazione('{SUB1}', s[1], s, dati),
        conEmail: m.corpoPrenotazione('{SUB1}', s[1], s, {{ ...dati, email: 'm@x.it' }}),
        rifiuto,
      }}));""")
    e0, e1, e2 = out["errori"]
    assert set(e0) == {"nome", "telefono"} and set(e1) == {"nome", "telefono", "email"}
    assert set(e2) == {"telefono"}                                   # meno di 6 cifre
    assert out["corpo"] == {"submission_token": SUB1, "start_at": "2026-10-06T07:15:00+00:00",
                            "name": "Mario Rossi", "phone": "+39 333 123 4567"}
    assert out["conEmail"]["email"] == "m@x.it"
    assert out["rifiuto"] == "orario non proposto dal server"


# ---------------------------------------------------------------------------
# 3 - LA PAGINA VERA, nello stub di DOM di P26-4
# ---------------------------------------------------------------------------

def _dom_p26_4() -> str:
    from tests import test_p26_4_shell_runtime as rt
    return rt.DOM


# Cio' che la pagina trova in un browser e lo stub di P26-4 non ha: il
# contenitore #booking-app, `location.pathname`, un `focus` osservabile, un
# orologio fermo (le finestre partono da ADESSO), la console e la history
# registrate (la pagina non deve usarle).
AMBIENTE = r"""
const __app = new __dom.El('div');
__app.id = 'booking-app';
__dom.byId['booking-app'] = __app;
globalThis.window.location.pathname = __PATH__;
globalThis.window.location.origin = 'https://crm.example.test';
globalThis.document.activeElement = null;
__dom.El.prototype.focus = function () { globalThis.document.activeElement = this; };
const __adesso = Date.parse(__ADESSO__);
Date.now = () => __adesso;
const __history = [];
globalThis.window.history = { pushState: (...a) => __history.push(a), replaceState: (...a) => __history.push(a) };
globalThis.history = globalThis.window.history;
const __stampa = console.log.bind(console);
const __console = [];
for (const k of ['log', 'info', 'warn', 'error', 'debug', 'trace']) {
  console[k] = (...a) => __console.push([k, a.map((x) => { try { return typeof x === 'string' ? x : JSON.stringify(x); } catch (_e) { return String(x); } }).join(' ')]);
}
"""

# `fetch` scriptato per TIPO di chiamata. L'ultima risposta di un tipo si
# ripete; le precedenti si consumano. `throw: true` = errore di rete.
FETCH_SCRIPTATO = r"""
const __chiamate = [];
const __risposte = { info: [], slots: [], submit: [] };
globalThis.__route = (tipo, ...r) => { __risposte[tipo].push(...r); };
function __tipo(url) {
  if (url.endsWith('/submit')) return 'submit';
  if (url.includes('/slots?')) return 'slots';
  return 'info';
}
globalThis.fetch = async (url, options = {}) => {
  __chiamate.push({ url: String(url), options });
  const coda = __risposte[__tipo(String(url))];
  const spec = coda.length > 1 ? coda.shift() : coda[0];
  if (!spec) return { status: 500, ok: false, json: async () => ({ detail: 'non previsto' }) };
  if (spec.throw) throw new TypeError('rete non raggiungibile');
  return {
    status: spec.status, ok: spec.status >= 200 && spec.status < 300,
    json: async () => { if (spec.body === undefined) throw new Error('nessun corpo'); return JSON.parse(JSON.stringify(spec.body)); },
  };
};
"""

AIUTI = r"""
// Ogni attesa fotografa il DOM: una fuga che compare in un passo intermedio e
// sparisce alla fine resta nella storia (vedi `dom` nel report).
const __foto = [];
// Con un server vero (test Postgres) si aspetta anche che nessuna richiesta
// sia in volo (`__inVolo`, tenuto dal fetch di quei test); con il fetch
// scriptato `__inVolo` non esiste e restano i soli giri.
const attendi = async (giri = 40) => {
  const giro = async () => { for (let i = 0; i < giri; i += 1) await new Promise((r) => setTimeout(r, 0)); };
  await giro();
  while ((globalThis.__inVolo || 0) > 0) { await new Promise((r) => setTimeout(r, 5)); await giro(); }
  __foto.push(dump());
};
const A = () => __app;
const sel = (s) => A().querySelector(s);
const tutti = (s) => A().querySelectorAll(s);
const bottone = (testo) => tutti('button').find((b) => b.textContent.trim() === testo);
async function clic(el) { if (!el) throw new Error('elemento mancante'); el.dispatch('click'); await attendi(); }
async function giorno(i) { await clic(tutti('[data-day]')[i]); }
async function orario(testo) { await clic(tutti('[data-slot]').find((b) => b.textContent === testo)); }
async function compila({ nome = 'Mario Rossi', telefono = '333 1234567', email = '' } = {}) {
  sel('#nome').value = nome; sel('#telefono').value = telefono; sel('#email').value = email;
  sel('form').dispatch('submit'); await attendi();
}
async function conferma() { await clic(sel('[data-confirm]')); }
function dump(n = A()) {
  const pezzi = [n.tagName, n.id, n.className, n._text, String(n.value ?? ''),
    JSON.stringify(n.attributes), JSON.stringify(n.dataset), JSON.stringify(n.style)];
  return pezzi.join('|') + n.children.map((c) => dump(c)).join('');
}
function cliccabili(n = A(), fuori = []) {
  if ((n.listeners.click || []).length) fuori.push([n.tagName, n.getAttribute('type')]);
  for (const c of n.children) cliccabili(c, fuori);
  return fuori;
}
function report(extra = {}) {
  const f = document.activeElement;
  __stampa(JSON.stringify({
    testo: A().visibleText(),
    passo: (sel('[data-step]') || {}).dataset ? (sel('[data-step]') || { dataset: {} }).dataset.step || null : null,
    stato: sel('[data-state]') ? sel('[data-state]').dataset.state : null,
    giorni: tutti('[data-day]').map((b) => [b.getAttribute('aria-label'), b.getAttribute('aria-pressed')]),
    orari: tutti('[data-slot]').map((b) => b.textContent),
    avvisi: tutti('.notice').map((p) => [p.getAttribute('role'), p.textContent]),
    errori: tutti('.field-error').map((p) => p.textContent),
    riepilogo: tutti('dt').map((dt, i) => [dt.textContent, tutti('dd')[i].textContent]),
    bottoni: tutti('button').map((b) => [b.textContent, !!b.disabled]),
    fuoco: f ? [f.tagName, f.getAttribute('tabindex'), f.textContent] : null,
    chiamate: __chiamate.map((c) => ({
      m: String(c.options.method || 'GET').toUpperCase(), url: c.url,
      body: c.options.body ? JSON.parse(c.options.body) : null,
      credentials: c.options.credentials || null, cache: c.options.cache || null,
      referrer: c.options.referrerPolicy || null, headers: c.options.headers || {},
    })),
    dom: __foto.join('\n') + '\n' + dump(), console: __console, history: __history, cliccabili: cliccabili(),
    immagini: tutti('img').length, ...extra,
  }));
}
"""


@pytest.fixture(scope="module")
def asset(tmp_path_factory):
    dst = tmp_path_factory.mktemp("p30-pagina") / "assets"
    shutil.copytree(ASSETS, dst)
    return dst


def _run(asset_dir: Path, rotte: str, scenario: str, *, path: str = f"/prenota/{TOKEN}",
         adesso: str = ADESSO, fetch: str = FETCH_SCRIPTATO, env_extra: dict | None = None) -> dict:
    driver = asset_dir.parent / f"driver-{abs(hash((rotte, scenario, path))) % 10**9}.mjs"
    ambiente = AMBIENTE.replace("__PATH__", json.dumps(path)).replace("__ADESSO__", json.dumps(adesso))
    driver.write_text(
        _dom_p26_4() + ambiente + fetch + f"\n{rotte}\n"
        + f"await import({json.dumps((asset_dir / 'booking-page.js').as_posix())});\n"
        + AIUTI + "\nawait attendi();\n" + scenario + "\n",
        encoding="utf-8")
    env = {"TZ": "UTC", "PATH": "/usr/bin:/bin", **(env_extra or {})}
    esito = subprocess.run([NODE, str(driver)], capture_output=True, text=True, timeout=90,
                           cwd=asset_dir.parent, env=env)
    if esito.returncode != 0:
        raise AssertionError(f"driver node fallito:\n{esito.stderr[-3000:]}\n{esito.stdout[-1500:]}")
    return json.loads(esito.stdout.strip().splitlines()[-1])


def _rotte(*, info=({"status": 200, "body": INFO},), slots=({"status": 200, "body": SLOT_SETTIMANA},),
           submit=({"status": 201, "body": CONFERMATA},)) -> str:
    return "\n".join(f"__route({json.dumps(t)}, ...{json.dumps(list(r))});"
                     for t, r in (("info", info), ("slots", slots), ("submit", submit)))


def _post(out):
    return [c for c in out["chiamate"] if c["m"] == "POST"]


def _flusso_completo(email: str = "") -> str:
    return f"""
      await giorno(0); await orario('09:15');
      await compila({{ email: {json.dumps(email)} }});
      await conferma();
      report();
    """


pagina_js = pytest.mark.skipif(NODE is None, reason="node non disponibile (BLOCKED)")


@pagina_js
def test_A_caricamento_intestazione_e_prima_settimana(asset):
    out = _run(asset, _rotte(), "report();")
    t = out["testo"]
    for atteso in ("Agenzia Mare Blu", "Appuntamento proprietario", "con Luca Bianchi",
                   "Durata: 1 ora", "Scegli giorno e orario"):
        assert atteso in t, atteso
    info, slots = out["chiamate"]
    assert info == {"m": "GET", "url": f"/api/public/booking/{TOKEN}", "body": None,
                    "credentials": "omit", "cache": "no-store", "referrer": "no-referrer",
                    "headers": {"Accept": "application/json"}}
    # la prima finestra parte da ADESSO (mai il passato) e finisce alla mezzanotte di Roma
    assert slots["url"] == (f"/api/public/booking/{TOKEN}/slots"
                            "?from=2026-10-05T06%3A00%3A00.000Z&to=2026-10-11T22%3A00%3A00.000Z")
    assert slots["credentials"] == "omit" and slots["cache"] == "no-store"
    # solo i giorni che hanno orari (mercoledi' 7 non c'e'), il primo gia' scelto
    assert out["giorni"] == [["Martedì 6 ottobre 2026", "true"], ["Giovedì 8 ottobre 2026", "false"]]
    assert out["orari"] == ["09:00", "09:15"]
    assert out["fuoco"] == ["H2", "-1", "Scegli giorno e orario"]


@pagina_js
@pytest.mark.parametrize("stato", [404])
def test_B_C_D_link_non_valido_scaduto_o_disattivato_stesso_messaggio_generico(asset, stato):
    """Il server da' la STESSA risposta 404 per token inesistente, scaduto,
    revocato o disattivato (e per il rate limit): la pagina mostra lo stesso
    messaggio neutro e non chiede altro."""
    out = _run(asset, _rotte(info=({"status": stato, "body": NON_DISPONIBILE},)), "report();")
    assert out["stato"] == "unavailable"
    assert out["testo"] == "Questo link non è disponibile.Contatta l’agenzia per riceverne uno nuovo."
    assert [c["url"] for c in out["chiamate"]] == [f"/api/public/booking/{TOKEN}"]


@pagina_js
@pytest.mark.parametrize("percorso", ["/prenota/corto", "/prenota/", f"/prenota/{TOKEN}/altro",
                                      "/api/public/booking/" + TOKEN])
def test_B2_url_malformata_nessuna_chiamata(asset, percorso):
    out = _run(asset, _rotte(), "report();", path=percorso)
    assert out["stato"] == "unavailable" and out["chiamate"] == []


@pagina_js
def test_B3_link_disattivato_mentre_si_sceglie_il_giorno(asset):
    out = _run(asset, _rotte(slots=({"status": 200, "body": SLOT_SETTIMANA},
                                    {"status": 404, "body": NON_DISPONIBILE})),
               "await clic(sel('[data-next]')); report();")
    assert out["stato"] == "unavailable"


@pagina_js
def test_E_O_metadata_e_risposte_con_id_interni_non_vengono_mai_mostrati(asset):
    info = {**INFO, "agency_name": "<img src=x onerror=alert(1)>Agenzia", "agency_id": 918273,
            "user_id": 918274, "assigned_user_id": 918275, "token_hash": "f" * 64,
            "link_id": 918276}
    slots = {"slots": [{**s, "id": 918277, "agency_id": 918273, "user_id": 918274}
                       for s in SLOT_SETTIMANA["slots"]], "agency_id": 918273}
    conferma = {**CONFERMATA, "id": 918278, "appointment_id": 918278, "contact_id": 918279,
                "agency_id": 918273, "assigned_user_id": 918275,
                "submission_hash": "e" * 64, "source_record_id": "d" * 64}
    out = _run(asset, _rotte(info=({"status": 200, "body": info},),
                             slots=({"status": 200, "body": slots},),
                             submit=({"status": 201, "body": conferma},)),
               _flusso_completo())
    assert out["passo"] == "done"
    for segreto in ("91827", "f" * 20, "e" * 20, "d" * 20, "agency_id", "user_id", "contact_id",
                    "appointment_id", "submission"):
        assert segreto not in out["dom"], segreto
    # il nome dell'agenzia e' TESTO: nessun elemento creato dal markup
    assert out["immagini"] == 0 and "<img src=x onerror=alert(1)>Agenzia" in out["testo"]


@pagina_js
def test_F_H_orari_solo_quelli_del_server_e_scelta(asset):
    out = _run(asset, _rotte(), """
      const primo = tutti('[data-slot]').map((b) => b.textContent);
      await giorno(1);
      const giovedi = tutti('[data-slot]').map((b) => b.textContent);
      await orario('15:00');
      report({ primo, giovedi });
    """)
    assert out["primo"] == ["09:00", "09:15"] and out["giovedi"] == ["15:00"]
    assert out["passo"] == "details"
    assert "Giovedì 8 ottobre 2026, ore 15:00" in out["testo"]
    assert out["fuoco"] == ["H2", "-1", "I tuoi dati"]


@pagina_js
def test_G_nessun_orario_nelle_prossime_settimane(asset):
    out = _run(asset, _rotte(slots=({"status": 200, "body": VUOTO},)), "report();")
    finestre = [c["url"].split("?")[1] for c in out["chiamate"] if "/slots?" in c["url"]]
    assert len(finestre) == 4                                   # ricerca automatica: 4 settimane
    # finestre contigue, senza buchi e senza sovrapposizioni
    coppie = [dict(p.split("=") for p in f.split("&")) for f in finestre]
    assert all(coppie[i]["to"] == coppie[i + 1]["from"] for i in range(3))
    assert "Al momento non ci sono orari disponibili nelle prossime settimane." in out["testo"]
    assert out["giorni"] == [] and out["orari"] == []
    assert ["Giorni successivi ›", False] in out["bottoni"]    # si puo' cercare oltre


@pagina_js
def test_G2_settimana_vuota_poi_successiva_con_orari(asset):
    out = _run(asset, _rotte(slots=({"status": 200, "body": VUOTO},
                                    {"status": 200, "body": SLOT_SETTIMANA},
                                    {"status": 200, "body": VUOTO})), """
      const dopoApertura = tutti('[data-slot]').map((b) => b.textContent);
      await clic(sel('[data-prev]'));
      const tornato = sel('[data-empty]') ? sel('[data-empty]').visibleText() : null;
      report({ dopoApertura, tornato });
    """)
    # la prima settimana era vuota: la pagina e' andata da sola alla successiva
    assert out["dopoApertura"] == ["09:00", "09:15"]
    assert out["tornato"] == "Nessun orario disponibile in questi giorni."


@pagina_js
def test_I_validazione_dei_dati_cliente_prima_di_qualunque_invio(asset):
    out = _run(asset, _rotte(), """
      await giorno(0); await orario('09:00');
      await compila({ nome: '', telefono: '' });
      const vuoti = tutti('.field-error').map((p) => p.textContent);
      await compila({ nome: 'Mario', telefono: 'chiamami', email: 'non-una-email' });
      const sbagliati = tutti('.field-error').map((p) => p.textContent);
      const invalidi = tutti('input').filter((i) => i.getAttribute('aria-invalid') === 'true').map((i) => i.id);
      await compila({ nome: 'Mario Rossi', telefono: '+39 333 123 4567', email: 'mario@example.it' });
      report({ vuoti, sbagliati, invalidi });
    """)
    assert out["vuoti"] == ["Inserisci il tuo nome.", "Inserisci un numero di telefono."]
    assert out["sbagliati"] == ["Inserisci un numero di telefono valido.",
                                "Inserisci un indirizzo email valido, oppure lascia vuoto."]
    assert out["invalidi"] == ["telefono", "email"]
    assert _post(out) == []                                    # nessun invio con dati non validi
    assert out["passo"] == "review"
    assert out["riepilogo"] == [
        ["Data", "Martedì 6 ottobre 2026"], ["Ora", "09:00 – 10:00"], ["Durata", "1 ora"],
        ["Con", "Luca Bianchi"], ["Tipo", "Appuntamento proprietario"], ["Nome", "Mario Rossi"],
        ["Telefono", "+39 333 123 4567"], ["Email", "mario@example.it"]]


@pagina_js
@pytest.mark.parametrize("email", ["", "mario@example.it"])
def test_J_invio_riuscito_conferma_chiara(asset, email):
    out = _run(asset, _rotte(), _flusso_completo(email))
    (post,) = _post(out)
    atteso = {"submission_token": SUB1, "start_at": "2026-10-06T07:15:00+00:00",
              "name": "Mario Rossi", "phone": "333 1234567"}
    if email:
        atteso["email"] = email
    assert post["body"] == atteso                               # nessun altro campo
    assert post["url"] == f"/api/public/booking/{TOKEN}/submit"
    assert post["credentials"] == "omit" and post["headers"]["Content-Type"] == "application/json"
    assert out["passo"] == "done"
    assert out["riepilogo"] == [["Giorno", "Martedì 6 ottobre 2026"], ["Ora", "09:15 – 10:15"],
                                ["Con", "Luca Bianchi"], ["Tipo", "Appuntamento proprietario"]]
    assert "Prenotazione confermata" in out["testo"]
    assert out["fuoco"] == ["H2", "-1", "Prenotazione confermata"]
    assert out["bottoni"] == []                                 # niente da ripremere


@pagina_js
def test_K_orario_preso_nel_frattempo_nuovo_submission_token_e_slot_ricaricati(asset):
    senza_915 = {"slots": [SLOT_SETTIMANA["slots"][0], SLOT_SETTIMANA["slots"][2]]}
    rotte = _rotte(
        info=({"status": 200, "body": INFO}, {"status": 200, "body": {**INFO, "submission_token": SUB2}}),
        slots=({"status": 200, "body": SLOT_SETTIMANA}, {"status": 200, "body": senza_915}),
        submit=({"status": 409, "body": {"detail": "Questo orario non e' piu' disponibile"}},
                {"status": 201, "body": {**CONFERMATA, "start_at": "2026-10-06T07:00:00+00:00",
                                         "end_at": "2026-10-06T08:00:00+00:00"}}))
    out = _run(asset, rotte, """
      await giorno(0); await orario('09:15'); await compila({ nome: 'Anna Verdi', telefono: '347 7654321' });
      await conferma();
      const dopo409 = { passo: sel('[data-step]').dataset.step,
                        avvisi: tutti('.notice').map((p) => p.textContent),
                        orari: tutti('[data-slot]').map((b) => b.textContent) };
      await orario('09:00');
      const precompilati = [sel('#nome').value, sel('#telefono').value];
      sel('form').dispatch('submit'); await attendi();
      await conferma();
      report({ dopo409, precompilati });
    """)
    assert out["dopo409"] == {"passo": "choose", "orari": ["09:00"], "avvisi": [
        "Questo orario non è più disponibile. Scegline un altro."]}
    assert out["precompilati"] == ["Anna Verdi", "347 7654321"]
    primo, secondo = _post(out)
    assert primo["body"]["submission_token"] == SUB1 and primo["body"]["start_at"].endswith("07:15:00+00:00")
    assert secondo["body"]["submission_token"] == SUB2 and secondo["body"]["start_at"].endswith("07:00:00+00:00")
    assert [c["m"] + (" slots" if "/slots?" in c["url"] else "") for c in out["chiamate"]] == [
        "GET", "GET slots", "POST", "GET", "GET slots", "POST"]
    assert out["passo"] == "done"


@pagina_js
def test_L_doppio_click_un_solo_invio(asset):
    out = _run(asset, _rotte(), """
      await giorno(0); await orario('09:15'); await compila();
      const b = sel('[data-confirm]');
      b.dispatch('click'); b.dispatch('click'); b.dispatch('click');
      const durante = [b.disabled, b.textContent];
      await attendi();
      report({ durante });
    """)
    assert len(_post(out)) == 1
    assert out["durante"] == [True, "Invio in corso…"]
    assert out["passo"] == "done"


@pagina_js
def test_L2_errore_di_rete_retry_con_lo_stesso_corpo_e_lo_stesso_token(asset):
    out = _run(asset, _rotte(submit=({"throw": True}, {"status": 500, "body": {"detail": "x"}},
                                     {"status": 201, "body": CONFERMATA})), """
      await giorno(0); await orario('09:15'); await compila();
      await conferma();
      const incerto = { titolo: sel('.step-title').textContent, bottoni: tutti('button').map((b) => b.textContent),
                        avvisi: tutti('.notice').map((p) => p.textContent) };
      await clic(bottone('Riprova'));
      await clic(bottone('Riprova'));
      report({ incerto });
    """)
    assert out["incerto"]["titolo"] == "Conferma in sospeso"
    assert out["incerto"]["bottoni"] == ["Riprova"]            # niente "Modifica": il corpo e' bloccato
    assert out["incerto"]["avvisi"] == [
        "Non abbiamo ricevuto la conferma. Premi “Riprova”: la prenotazione non verrà duplicata."]
    corpi = [c["body"] for c in _post(out)]
    assert len(corpi) == 3 and corpi[0] == corpi[1] == corpi[2]
    assert corpi[0]["submission_token"] == SUB1
    assert [c["m"] for c in out["chiamate"]].count("GET") == 2  # nessun nuovo token chiesto
    assert out["passo"] == "done"


@pagina_js
def test_L3_dati_rifiutati_dal_server_si_correggono_con_lo_stesso_token(asset):
    out = _run(asset, _rotte(submit=({"status": 422, "body": {"detail": "phone: String should have at most 32 characters"}},
                                     {"status": 201, "body": CONFERMATA})), """
      await giorno(0); await orario('09:15'); await compila();
      await conferma();
      const dopo422 = { passo: sel('[data-step]').dataset.step, avvisi: tutti('.notice').map((p) => p.textContent),
                        testo: A().visibleText(),
                        campi: [sel('#nome').value, sel('#telefono').value, sel('#email').value] };
      await compila({ telefono: '333 7654321' });
      await conferma();
      report({ dopo422 });
    """)
    assert out["dopo422"]["passo"] == "details"
    assert out["dopo422"]["avvisi"] == [
        "Controlla i dati inseriti: nome e telefono sono obbligatori, l’email deve essere valida."]
    assert "String should" not in out["dopo422"]["testo"]       # mai il dettaglio tecnico
    assert out["dopo422"]["campi"] == ["Mario Rossi", "333 1234567", ""]   # dati mantenuti
    primo, secondo = _post(out)
    assert primo["body"]["submission_token"] == secondo["body"]["submission_token"] == SUB1
    assert secondo["body"]["phone"] == "333 7654321"
    assert out["passo"] == "done"


@pagina_js
def test_L4_rate_limit_o_link_revocato_all_invio_messaggio_neutro_e_retry_identico(asset):
    out = _run(asset, _rotte(submit=({"status": 404, "body": NON_DISPONIBILE},
                                     {"status": 201, "body": CONFERMATA})), """
      await giorno(0); await orario('09:15'); await compila();
      await conferma();
      const neutro = tutti('.notice').map((p) => p.textContent);
      await clic(bottone('Riprova'));
      report({ neutro });
    """)
    assert out["neutro"] == ["Non è stato possibile completare la prenotazione. Il link potrebbe non "
                             "essere più disponibile oppure sono stati fatti troppi tentativi: riprova "
                             "tra qualche minuto."]
    primo, secondo = _post(out)
    assert primo["body"] == secondo["body"]
    assert out["passo"] == "done"


@pagina_js
def test_rete_assente_all_apertura_poi_riprova(asset):
    out = _run(asset, _rotte(info=({"throw": True}, {"status": 200, "body": INFO})), """
      const prima = { stato: sel('[data-state]').dataset.state, testo: A().visibleText() };
      await clic(bottone('Riprova'));
      report({ prima });
    """)
    assert out["prima"]["stato"] == "network-error"
    assert "Controlla la connessione e riprova." in out["prima"]["testo"]
    assert "TypeError" not in out["prima"]["testo"] and "rete non raggiungibile" not in out["prima"]["testo"]
    assert out["orari"] == ["09:00", "09:15"]


@pagina_js
def test_rete_assente_sugli_slot_poi_riprova(asset):
    out = _run(asset, _rotte(slots=({"throw": True}, {"status": 200, "body": SLOT_SETTIMANA})), """
      const prima = sel('[data-state]').dataset.state;
      await clic(bottone('Riprova'));
      report({ prima });
    """)
    assert out["prima"] == "network-error" and out["orari"] == ["09:00", "09:15"]


@pagina_js
def test_navigazione_settimane(asset):
    out = _run(asset, _rotte(), """
      await clic(sel('[data-next]'));
      await clic(sel('[data-next]'));
      await clic(sel('[data-prev]'));
      report();
    """)
    finestre = [c["url"].split("?")[1] for c in out["chiamate"] if "/slots?" in c["url"]]
    assert finestre == [
        "from=2026-10-05T06%3A00%3A00.000Z&to=2026-10-11T22%3A00%3A00.000Z",
        "from=2026-10-11T22%3A00%3A00.000Z&to=2026-10-18T22%3A00%3A00.000Z",
        # la settimana del cambio d'ora: 6 giorni, per restare entro le 168 ore dell'API
        "from=2026-10-18T22%3A00%3A00.000Z&to=2026-10-24T22%3A00%3A00.000Z",
        "from=2026-10-11T22%3A00%3A00.000Z&to=2026-10-18T22%3A00%3A00.000Z",
    ]


@pagina_js
def test_M_N_token_e_submission_token_mai_persistiti_loggati_o_nel_dom(asset):
    """Lo stub di P26-4 fa fallire QUALUNQUE accesso a localStorage,
    sessionStorage, indexedDB e document.cookie: arrivare in fondo e' gia'
    la prova. In piu': nessuna console, nessuna history, nessun token nel DOM,
    il token solo nelle URL dell'API, il submission_token solo nel corpo del POST."""
    for rotte, scenario in ((_rotte(), _flusso_completo("m@x.it")),
                            (_rotte(submit=({"throw": True},)), _flusso_completo()),
                            (_rotte(info=({"status": 404, "body": NON_DISPONIBILE},)), "report();")):
        out = _run(asset, rotte, scenario)
        assert out["console"] == [] and out["history"] == []
        for segreto in (TOKEN, SUB1, TOKEN[:16], SUB1[:16]):
            assert segreto not in out["dom"], segreto
        for c in out["chiamate"]:
            assert c["url"].startswith(f"/api/public/booking/{TOKEN}")
            assert SUB1 not in c["url"]
            assert c["credentials"] == "omit"
        for c in out["chiamate"]:
            if c["m"] == "GET":
                assert c["body"] is None


@pagina_js
def test_P_struttura_mobile_del_dom(asset):
    out = _run(asset, _rotte(), """
      const griglia = sel('.slot-grid');
      const giorni = sel('.day-list');
      report({ griglia: [griglia.getAttribute('role'), griglia.getAttribute('aria-label')],
               giorniRuolo: giorni.getAttribute('role'), sezioni: A().children.map((c) => c.className) });
    """)
    assert out["griglia"] == ["group", "Orari disponibili, Martedì 6 ottobre 2026"]
    assert out["giorniRuolo"] == "group"
    assert out["sezioni"] == ["panel summary-card", "panel"]    # una colonna, due schede


@pagina_js
def test_Q_tastiera_solo_bottoni_veri_etichette_e_fuoco_sul_titolo(asset):
    out = _run(asset, _rotte(), """
      const scelta = cliccabili();
      await giorno(0); await orario('09:15');
      const etichette = tutti('label').map((l) => [l.getAttribute('for'), !!sel('#' + l.getAttribute('for'))]);
      const fuocoDati = document.activeElement.textContent;
      await compila();
      const fuocoRiepilogo = document.activeElement.textContent;
      const riepilogo = cliccabili();
      report({ scelta, etichette, fuocoDati, fuocoRiepilogo, riepilogo });
    """)
    for nodo in out["scelta"] + out["riepilogo"]:
        assert nodo == ["BUTTON", "button"], nodo              # mai un div cliccabile
    assert out["etichette"] == [["nome", True], ["telefono", True], ["email", True]]
    assert out["fuocoDati"] == "I tuoi dati" and out["fuocoRiepilogo"] == "Controlla e conferma"
    # l'invio del modulo e' un vero submit (Invio da tastiera)
    assert "form.addEventListener('submit'" in PAGE_JS.read_text(encoding="utf-8")
    assert "avanti.setAttribute('type', 'submit');" in PAGE_JS.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 4 - MUTAZIONI: le verifiche sopra DEVONO accorgersi di una pagina sbagliata
# ---------------------------------------------------------------------------

def _mutante(tmp_path: Path, file: str, vecchio: str, nuovo: str) -> Path:
    dst = tmp_path / "mutante" / "assets"
    shutil.copytree(ASSETS, dst)
    testo = (dst / file).read_text(encoding="utf-8")
    assert testo.count(vecchio) == 1, f"punto di mutazione sparito: {vecchio!r}"
    (dst / file).write_text(testo.replace(vecchio, nuovo), encoding="utf-8")
    return dst


def _fuga(out) -> list[str]:
    """Le stesse verifiche di test_M_N, come elenco di violazioni."""
    v = []
    if out["console"]:
        v.append("console")
    if out["history"]:
        v.append("history")
    for segreto in (TOKEN, SUB1):
        if segreto in out["dom"]:
            v.append(f"dom:{segreto[:10]}")
    return v


@pagina_js
@pytest.mark.parametrize("vecchio,nuovo", [
    ("  info = r.info;\n  oggi = dataRoma(Date.now());",
     "  info = r.info;\n  console.info('link', token);\n  oggi = dataRoma(Date.now());"),
    ("  box.dataset.step = 'choose';", "  box.dataset.step = 'choose';\n  box.dataset.link = token;"),
    ("  riga(dl, 'Tipo', info.tipo);\n  riga(dl, 'Nome', cliente.nome);",
     "  riga(dl, 'Tipo', info.tipo);\n  riga(dl, 'Rif.', info.submissionToken);\n  riga(dl, 'Nome', cliente.nome);"),
])
def test_mut_token_in_console_o_nel_dom_viene_scoperto(tmp_path, vecchio, nuovo):
    out = _run(_mutante(tmp_path, "booking-page.js", vecchio, nuovo), _rotte(), _flusso_completo())
    assert _fuga(out) != []


@pagina_js
def test_mut_token_in_storage_fa_fallire_la_pagina(tmp_path):
    dst = _mutante(tmp_path, "booking-page.js", "  info = r.info;\n  oggi = dataRoma(Date.now());",
                   "  info = r.info;\n  localStorage.setItem('t', token);\n  oggi = dataRoma(Date.now());")
    with pytest.raises(AssertionError, match="FORBIDDEN"):
        _run(dst, _rotte(), "report();")


@pagina_js
def test_mut_doppio_invio_viene_scoperto(tmp_path):
    dst = _mutante(tmp_path, "booking-page.js", "  if (inVolo) return;                           // doppio click: un solo invio\n", "")
    out = _run(dst, _rotte(), """
      await giorno(0); await orario('09:15'); await compila();
      const b = sel('[data-confirm]'); b.dispatch('click'); b.dispatch('click');
      await attendi(); report();
    """)
    assert len(_post(out)) == 2                                 # la verifica di test_L lo vede


@pagina_js
def test_mut_retry_con_un_nuovo_token_viene_scoperto(tmp_path):
    """Una pagina che dopo un errore di rete chiedesse un nuovo submission_token
    (rischio di doppia prenotazione) cambierebbe il corpo del retry."""
    dst = _mutante(tmp_path, "booking-page.js",
                   "  mostraRiepilogo(TESTI.esitoIncerto, { soloRiprova: true });\n}",
                   "  invioBloccato = null;\n  info = { ...info, submissionToken: 'ALTRO_' + Date.now() };\n"
                   "  mostraRiepilogo(TESTI.esitoIncerto, { soloRiprova: true });\n}")
    out = _run(dst, _rotte(submit=({"throw": True}, {"status": 201, "body": CONFERMATA})), """
      await giorno(0); await orario('09:15'); await compila(); await conferma();
      await clic(bottone('Riprova')); report();
    """)
    primo, secondo = _post(out)
    assert primo["body"] != secondo["body"]                     # test_L2 lo vede


@pagina_js
@pytest.mark.parametrize("file,vecchio,nuovo", [
    # l'orario inviato non e' piu' quello del server
    ("booking-model.js", "    start_at: slot.start_at,",
     "    start_at: new Date(slot.inizioMs + 5 * 60000).toISOString(),"),
    # la pagina "inventa" un orario in piu' nella griglia
    ("booking-model.js", "  return esito.sort((x, y) => x.inizioMs - y.inizioMs);",
     "  if (esito.length) esito.push({ ...esito[0], start_at: 'x', inizioMs: esito[0].inizioMs + 5 * 60000,"
     " fineMs: esito[0].fineMs + 5 * 60000 });\n  return esito.sort((x, y) => x.inizioMs - y.inizioMs);"),
])
def test_mut_orario_inventato_lato_client_viene_scoperto(tmp_path, file, vecchio, nuovo):
    out = _run(_mutante(tmp_path, file, vecchio, nuovo), _rotte(), _flusso_completo())
    posts = _post(out)
    inviato = posts[0]["body"]["start_at"] if posts else None
    server = {s["start_at"] for s in SLOT_SETTIMANA["slots"]}
    assert out["orari"] != ["09:00", "09:15"] or inviato not in server


@pagina_js
@pytest.mark.parametrize("vecchio,nuovo", [
    ("    tipo: testo(json.appointment_type_label) || 'Appuntamento',",
     "    tipo: `${testo(json.appointment_type_label) || 'Appuntamento'} #${json.agency_id}`,"),
    ("  return { inizioMs: inizio, fineMs: Number.isFinite(fine) ? fine : null };",
     "  return { inizioMs: inizio, fineMs: Number.isFinite(fine) ? fine : null, id: json.appointment_id };"),
])
def test_mut_id_interno_mostrato_viene_scoperto(tmp_path, vecchio, nuovo):
    dst = _mutante(tmp_path, "booking-model.js", vecchio, nuovo)
    if "appointment_id" in nuovo:
        dst_page = dst / "booking-page.js"
        testo = dst_page.read_text(encoding="utf-8")
        testo = testo.replace("  riga(dl, 'Tipo', info.tipo);\n  box.append(dl);\n  if (info.agenzia)",
                              "  riga(dl, 'Tipo', info.tipo);\n  riga(dl, 'Rif.', String(conferma.id));\n"
                              "  box.append(dl);\n  if (info.agenzia)")
        dst_page.write_text(testo, encoding="utf-8")
    info = {**INFO, "agency_id": 918273}
    out = _run(dst, _rotte(info=({"status": 200, "body": info},),
                           submit=({"status": 201, "body": {**CONFERMATA, "appointment_id": 918278}},)),
               _flusso_completo())
    assert "91827" in out["dom"]                                # test_E_O lo vede


@pagina_js
def test_mut_409_senza_refresh_viene_scoperto(tmp_path):
    """Una pagina che dopo un 409 tornasse alla scelta SENZA chiedere un nuovo
    submission_token e SENZA ricaricare gli orari: la sequenza di chiamate e il
    token del secondo invio di test_K la smascherano."""
    dst = _mutante(tmp_path, "booking-page.js", "    await ricaricaDopoConflitto();",
                   "    info = { ...info, submissionToken: '" + SUB1 + "' };\n"
                   "    mostraScelta(TESTI.orarioPreso);")
    rotte = _rotte(
        info=({"status": 200, "body": INFO}, {"status": 200, "body": {**INFO, "submission_token": SUB2}}),
        submit=({"status": 409, "body": {"detail": "x"}}, {"status": 201, "body": CONFERMATA}))
    out = _run(dst, rotte, """
      await giorno(0); await orario('09:15'); await compila(); await conferma();
      await orario('09:00'); sel('form').dispatch('submit'); await attendi(); await conferma();
      report();
    """)
    _, secondo = _post(out)
    sequenza = [c["m"] + (" slots" if "/slots?" in c["url"] else "") for c in out["chiamate"]]
    assert secondo["body"]["submission_token"] != SUB2 or sequenza != [
        "GET", "GET slots", "POST", "GET", "GET slots", "POST"]


@pagina_js
def test_mut_finestra_oltre_168_ore_viene_scoperta(tmp_path):
    """Una finestra da 7 giorni di Roma SEMPRE (anche nella settimana del
    cambio d'ora: 169 ore, che l'API rifiuta con 422) viene vista dal test
    del modello e dalla navigazione."""
    dst = _mutante(tmp_path, "booking-model.js", "    if (a - da <= FINESTRA_MASSIMA_MS) {",
                   "    if (true) {")
    driver = f"""const m = await import('{(dst / 'booking-model.js').as_posix()}');
      const x = m.finestra('2026-10-19', 0);
      console.log(JSON.stringify((Date.parse(x.a) - Date.parse(x.da)) / 3600000));"""
    esito = subprocess.run([NODE, "--input-type=module", "-e", driver], capture_output=True,
                           text=True, timeout=60, env={"TZ": "UTC", "PATH": "/usr/bin:/bin"})
    assert json.loads(esito.stdout.strip()) == 169               # test_10 lo vede (<= 168)
    out = _run(dst, _rotte(), "await clic(sel('[data-next]')); await clic(sel('[data-next]')); report();")
    terza = [c["url"] for c in out["chiamate"] if "/slots?" in c["url"]][2]
    assert "to=2026-10-25T23%3A00%3A00.000Z" in terza            # test_navigazione lo vede


@pagina_js
def test_dst_ottobre_la_pagina_non_chiede_mai_piu_di_168_ore(asset):
    """Aperta la domenica prima del cambio d'ora e sfogliata per 3 settimane:
    ogni finestra chiesta all'API e' <= 168 ore reali, e le finestre sono contigue."""
    from datetime import datetime
    from urllib.parse import parse_qs, urlparse
    out = _run(asset, _rotte(), """
      await clic(sel('[data-next]')); await clic(sel('[data-next]')); await clic(sel('[data-next]'));
      report();
    """, adesso="2026-10-18T08:00:00Z")
    finestre = []
    for c in out["chiamate"]:
        if "/slots?" in c["url"]:
            q = parse_qs(urlparse(c["url"]).query)
            finestre.append((datetime.fromisoformat(q["from"][0].replace("Z", "+00:00")),
                             datetime.fromisoformat(q["to"][0].replace("Z", "+00:00"))))
    assert len(finestre) == 4
    assert all((b - a).total_seconds() <= 168 * 3600 for a, b in finestre), finestre
    assert all(finestre[i][1] == finestre[i + 1][0] for i in range(3))
    # 18-24 ottobre: 7 giorni pieni (da ADESSO a mezzanotte del 25, < 168h);
    # dal 25 (giorno del cambio d'ora) 7 giorni sarebbero 169h: se ne chiedono 6
    assert finestre[0][1].isoformat() == "2026-10-24T22:00:00+00:00"
    assert (finestre[1][0].isoformat(), finestre[1][1].isoformat()) == (
        "2026-10-24T22:00:00+00:00", "2026-10-30T23:00:00+00:00")
    assert (finestre[1][1] - finestre[1][0]).total_seconds() == 145 * 3600
