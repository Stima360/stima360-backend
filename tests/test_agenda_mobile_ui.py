"""AGENDA MOBILE - prove senza browser: funzioni pure, preferenza della vista, cascata CSS.

* Le funzioni pure delle nuove viste girano in Node in QUATTRO fusi del dispositivo (Roma, UTC,
  New York, Tokyo): settimana lunedi'-domenica, cambio di mese e di anno, ora legale e solare,
  mezzanotte, Lista cronologica, indicatori del Mese, gesto orizzontale, sovrapposizioni.
* `agenda/agenda-prefs.js` e' l'UNICO modulo della Shell che usa uno storage del browser, e vi
  scrive SOLO uno dei quattro nomi di vista: nessun dato sensibile.
* La cascata CSS VERA del CRM (i fogli di index.html, nell'ordine): nessuna regola nasconde un
  pulsante di vista; le regole che nascondono qualcosa dell'Agenda hanno almeno due classi, cosi'
  nessuna regola a una classe di aurora.css (caricato dopo) le annulla, come succedeva a
  `.agenda-desktop-only` con `.btn`. La prova "nel browser, con tutti i fogli" e' in
  test_agenda_mobile_browser_postgres.py.

Senza Node le prove di esecuzione sono SKIPPED (da riportare come BLOCKED), mai passate.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from tests.test_a30_4_agenda_ui import _senza_commenti, _stage

ROOT = Path(__file__).resolve().parents[1]
SHELL = ROOT / "static" / "os_shell"
ASSETS = SHELL / "assets"
MODEL = ASSETS / "agenda" / "agenda-model.js"
PREFS = ASSETS / "agenda" / "agenda-prefs.js"
PAGE = ASSETS / "views" / "agenda" / "agenda-page.js"
VIEWS_JS = ASSETS / "components" / "agenda" / "agenda-views.js"
NODE = shutil.which("node")


def _node(tmp_path: Path, corpo: str, tz: str = "Europe/Rome") -> dict:
    if NODE is None:
        pytest.skip("node non disponibile: prove eseguite BLOCKED, non passate")
    cartella = _stage(tmp_path)
    (cartella / "agenda" / "agenda-prefs.mjs").write_text(
        PREFS.read_text(encoding="utf-8").replace("'./agenda-model.js'", "'./agenda-model.mjs'"), encoding="utf-8")
    driver = cartella / "driver.mjs"
    driver.write_text(textwrap.dedent(corpo), encoding="utf-8")
    esito = subprocess.run([NODE, str(driver)], capture_output=True, text=True, timeout=60,
                           env={"TZ": tz, "PATH": "/usr/bin:/bin"})
    assert esito.returncode == 0, esito.stderr
    return json.loads(esito.stdout.strip().splitlines()[-1])


def _app(i, inizio, fine, **extra):
    return {"id": i, "kind": "appointment", "status": "scheduled", "type": "call",
            "start_at": inizio, "end_at": fine, **extra}


MODELLO = """
import * as m from './agenda/agenda-model.mjs';
const A = (id, s, e, extra = {}) => ({ id, kind: 'appointment', status: 'scheduled', type: 'call', start_at: s, end_at: e, ...extra });
const out = {};
out.views = m.VIEWS; out.mobile = m.MOBILE_VIEWS; out.compact = m.COMPACT_QUERY;
out.eff = [m.effectiveView('week', true), m.effectiveView('month', true), m.effectiveView(null, true),
           m.effectiveView(null, false), m.effectiveView(null, true, 'month'), m.effectiveView('day', true, 'month'),
           m.effectiveView(null, false, 'boh')];
// cambio di mese e di anno, settimana a cavallo dell'anno
out.mesi = [m.shiftKey('month', '2026-12-15', 1), m.shiftKey('month', '2027-01-01', -1), m.shiftKey('month', '2026-01-31', 1)];
out.settAnno = m.weekDays('2027-01-01');
out.gridNov = m.monthGrid('2026-11-05').map((r) => r.map((c) => c.key));
out.gridFeb = m.monthGrid('2027-02-10').length;
// ora solare (25/10/2026) e legale (29/03/2026): intervalli della Settimana e del Giorno
out.settOttobre = m.rangeFor('week', '2026-10-25');
out.giornoSolare = m.rangeFor('day', '2026-10-25');
out.giornoLegale = m.rangeFor('day', '2026-03-29');
out.meseOttobre = m.rangeFor('month', '2026-10-10');
out.lista = m.rangeFor('list', '2026-11-05');
out.passi = [m.shiftKey('list', '2026-11-05', 1), m.shiftKey('week', '2026-11-05', -1), m.shiftKey('day', '2026-12-31', 1)];
// mezzanotte
const notte = A(1, '2026-11-04T23:30:00+01:00', '2026-11-05T02:00:00+01:00');
const lunga = A(2, '2026-11-03T20:00:00+01:00', '2026-11-06T09:00:00+01:00');
out.notte = [m.dayTimeLabel(notte, '2026-11-04'), m.dayTimeLabel(notte, '2026-11-05'), m.dayTimeLabel(lunga, '2026-11-05')];
out.fineMezzanotte = m.dayTimeLabel(A(3, '2026-11-05T22:00:00+01:00', '2026-11-06T00:00:00+01:00'), '2026-11-05');
// ordine dentro il giorno: chi viene da ieri e' primo
const giorno = [A(10, '2026-11-05T10:00:00+01:00', '2026-11-05T11:00:00+01:00'), notte,
                A(11, '2026-11-05T09:00:00+01:00', '2026-11-05T09:30:00+01:00')];
out.ordine = m.sortedItemsForDay(giorno, '2026-11-05').map((x) => x.id);
out.ordineIeri = m.sortedItemsForDay(giorno, '2026-11-04').map((x) => x.id);
// Lista: gruppi e giorni vuoti
out.gruppi = m.listGroups(['2026-11-04', '2026-11-05', '2026-11-06', '2026-11-07', '2026-11-08', '2026-11-09'],
  [A(20, '2026-11-05T10:00:00+01:00', '2026-11-05T11:00:00+01:00'), A(21, '2026-11-09T10:00:00+01:00', '2026-11-09T11:00:00+01:00')])
  .map((g) => g.kind === 'day' ? ['day', g.key, g.items.map((x) => x.id)] : ['empty', g.from, g.to]);
out.vuoto = m.emptyRangeLabel('2026-11-06', '2026-11-08');
// Mese: visite prima, conteggio vero
const pieno = [1, 2, 3, 4].map((i) => A(30 + i, `2026-11-15T0${i + 5}:00:00+01:00`, `2026-11-15T0${i + 5}:30:00+01:00`));
pieno.push(A(40, '2026-11-15T18:00:00+01:00', '2026-11-15T19:00:00+01:00', { type: 'buyer_visit', status: 'requested' }));
out.indicatori = m.monthDayMarkers(pieno, '2026-11-15');
out.vuotoMese = m.monthDayMarkers([], '2026-11-16');
// gesto
out.swipe = [m.swipeDirection({ dx: -120, dy: 10, dt: 300 }), m.swipeDirection({ dx: 120, dy: 5, dt: 300 }),
             m.swipeDirection({ dx: -120, dy: 200, dt: 300 }), m.swipeDirection({ dx: -30, dy: 0, dt: 100 }),
             m.swipeDirection({ dx: -120, dy: 0, dt: 2000 }), m.swipeDirection({ dx: 120, dy: 0, dt: 300, startX: 5, width: 390 }),
             m.swipeDirection({ dx: -120, dy: 0, dt: 300, startX: 200, width: 390 }), m.swipeDirection({})];
// sovrapposizioni con un limite di colonne: nessuno perso, nessuno doppio
const cinque = [0, 1, 2, 3, 4].map((i) => A(50 + i, '2026-11-05T10:00:00+01:00', '2026-11-05T11:00:00+01:00'));
for (const max of [2, 3, 4]) {
  const { placement, overflow } = m.overflowLayout(cinque, max);
  out[`ov${max}`] = { visibili: placement.map((p, i) => (p ? i : null)).filter((x) => x !== null),
                      nascosti: overflow.flatMap((o) => o.indices), colonne: Math.max(...placement.filter(Boolean).map((p) => p.columns)) };
}
// linea dell'ora
out.ora = [m.nowLineTop('2026-11-05', new Date('2026-11-05T09:30:00Z')), m.nowLineTop('2026-11-06', new Date('2026-11-05T09:30:00Z'))];
// filtri: stessi stati in ogni vista, agente nella Lista
out.lista_param = m.listFilterParams({});
out.stati = m.DEFAULT_VIEW_STATUSES;
out.visita = [m.isVisit({ type: 'buyer_visit' }), m.isVisit({ appointment_type: 'buyer_visit' }), m.isVisit({ kind: 'busy', type: 'buyer_visit' }), m.isVisit({ type: 'call' })];
console.log(JSON.stringify(out));
"""


@pytest.mark.parametrize("tz", ["Europe/Rome", "UTC", "America/New_York", "Asia/Tokyo"])
def test_funzioni_pure_delle_quattro_viste_in_ogni_fuso(tmp_path, tz):
    o = _node(tmp_path, MODELLO, tz)
    assert o["views"] == ["week", "day", "month", "list"] and o["mobile"] == o["views"]
    assert o["compact"] == "(max-width: 767px), (max-height: 500px) and (pointer: coarse)"
    # mai una vista diversa da quella chiesta; senza vista: l'ultima scelta o la partenza
    assert o["eff"] == ["week", "month", "list", "week", "month", "day", "week"]
    # mesi e anni
    assert o["mesi"] == ["2027-01-01", "2026-12-01", "2026-02-01"]
    assert o["settAnno"] == ["2026-12-28", "2026-12-29", "2026-12-30", "2026-12-31",
                             "2027-01-01", "2027-01-02", "2027-01-03"]
    nov = o["gridNov"]
    assert nov[0][0] == "2026-10-26" and nov[0][6] == "2026-11-01" and len(nov) == 6   # il 1/11 e' domenica
    assert all(len(r) == 7 for r in nov) and nov[-1][0] == "2026-11-30"
    assert o["gridFeb"] == 4                                                           # febbraio 2027: 4 righe
    # ora solare e legale: giorni di 25 e 23 ore, settimana lun-dom intera
    assert o["settOttobre"]["days"][0] == "2026-10-19" and o["settOttobre"]["days"][-1] == "2026-10-25"
    assert o["settOttobre"]["from"].endswith("+02:00") and o["settOttobre"]["to"].endswith("+01:00")
    assert o["giornoSolare"]["from"] == "2026-10-25T00:00:00+02:00" and o["giornoSolare"]["to"] == "2026-10-26T00:00:00+01:00"
    assert o["giornoLegale"]["from"] == "2026-03-29T00:00:00+01:00" and o["giornoLegale"]["to"] == "2026-03-30T00:00:00+02:00"
    assert o["meseOttobre"]["days"][0] == "2026-10-01" and o["meseOttobre"]["days"][-1] == "2026-10-31"
    assert len(o["lista"]["days"]) == 14 and o["lista"]["days"][0] == "2026-11-05"
    assert o["passi"] == ["2026-11-12", "2026-10-29", "2027-01-01"]
    # mezzanotte: da dove viene e dove va
    sera, mattina, lunga = o["notte"]
    assert sera == {"text": "23:30–domani 02:00", "startsBefore": False, "endsAfter": True}
    assert mattina == {"text": "ieri 23:30–02:00", "startsBefore": True, "endsAfter": False}
    assert lunga["text"] == "3 nov 20:00–domani 09:00" and lunga["startsBefore"] and lunga["endsAfter"]
    assert o["fineMezzanotte"] == {"text": "22:00–00:00", "startsBefore": False, "endsAfter": False}
    assert o["ordine"] == [1, 11, 10] and o["ordineIeri"] == [1]
    # Lista: un gruppo per giorno con appuntamenti, i giorni vuoti in una riga
    assert o["gruppi"] == [["empty", "2026-11-04", "2026-11-04"], ["day", "2026-11-05", [20]],
                           ["empty", "2026-11-06", "2026-11-08"], ["day", "2026-11-09", [21]]]
    assert o["vuoto"] == "6 – 8 novembre 2026"
    # Mese: la visita si vede per prima anche in un giorno pieno; conteggio vero
    ind = o["indicatori"]
    assert ind["total"] == 5 and ind["visits"] == 1 and ind["more"] == 2
    assert ind["dots"][0] == {"status": "requested", "visit": True} and len(ind["dots"]) == 3
    assert o["vuotoMese"] == {"total": 0, "visits": 0, "dots": [], "more": 0}
    # gesto: orizzontale si', verticale/corto/lento/dal bordo no
    assert o["swipe"] == [1, -1, 0, 0, 0, 0, 1, 0]
    # sovrapposizioni: tutti raggiungibili, nessuno due volte
    for max_ in (2, 3, 4):
        r = o[f"ov{max_}"]
        assert sorted(r["visibili"] + r["nascosti"]) == [0, 1, 2, 3, 4], (max_, r)
        assert not set(r["visibili"]) & set(r["nascosti"]) and r["colonne"] == max_
    assert o["ora"] == [(10 * 60 + 30) / 60 * 64, None]
    # stessi stati predefiniti in ogni vista
    assert o["lista_param"] == {"statuses": o["stati"]}
    assert o["visita"] == [True, True, False, False]


def test_stati_predefiniti_specchio_del_backend():
    from appointments.service import DEFAULT_CALENDAR_STATUSES

    testo = MODEL.read_text(encoding="utf-8")
    blocco = testo[testo.index("export const DEFAULT_VIEW_STATUSES"):]
    blocco = blocco[:blocco.index(");")]
    assert tuple(re.findall(r"'(\w+)'", blocco)) == tuple(DEFAULT_CALENDAR_STATUSES)


PREFERENZE = """
import { readPreferredView, savePreferredView, PREFERENCE_KEY } from './agenda/agenda-prefs.mjs';
const out = {};
const memoria = new Map();
const scritture = [];
globalThis.localStorage = { getItem: (k) => (memoria.has(k) ? memoria.get(k) : null),
                            setItem: (k, v) => { scritture.push([k, v]); memoria.set(k, String(v)); } };
out.vuoto = readPreferredView();
savePreferredView('month'); out.dopo = readPreferredView();
savePreferredView('token=abc'); savePreferredView(''); savePreferredView(null);
out.scritture = scritture;
memoria.set(PREFERENCE_KEY, '<script>'); out.sporco = readPreferredView();
globalThis.localStorage = { getItem() { throw new Error('bloccato'); }, setItem() { throw new Error('pieno'); } };
out.bloccato = readPreferredView(); savePreferredView('week');
Object.defineProperty(globalThis, 'localStorage', { get() { throw new Error('SecurityError'); }, configurable: true });
out.vietato = readPreferredView(); savePreferredView('day');
out.chiave = PREFERENCE_KEY;
console.log(JSON.stringify(out));
"""


def test_preferenza_solo_il_nome_della_vista_e_mai_un_errore(tmp_path):
    o = _node(tmp_path, PREFERENZE)
    assert o["vuoto"] is None and o["dopo"] == "month"
    assert o["scritture"] == [["stima360.agenda.vista", "month"]]      # nient'altro, mai
    assert o["sporco"] is None and o["bloccato"] is None and o["vietato"] is None
    assert o["chiave"] == "stima360.agenda.vista"


def test_un_solo_modulo_della_shell_tocca_uno_storage_del_browser():
    """AGENDA MOBILE: la preferenza della vista e' l'UNICA eccezione alla regola
    "nessuno storage del browser" della Shell, confinata in agenda-prefs.js."""
    toccano = []
    for f in SHELL.rglob("*.js"):
        codice = _senza_commenti(f.read_text(encoding="utf-8"))
        if any(x in codice for x in ("localStorage", "sessionStorage", "indexedDB", "document.cookie")):
            toccano.append(f.relative_to(ROOT).as_posix())
    assert toccano == ["static/os_shell/assets/agenda/agenda-prefs.js"]
    prefs = _senza_commenti(PREFS.read_text(encoding="utf-8"))
    for vietato in ("sessionStorage", "indexedDB", "document.cookie", "fetch(", "getSession", "agency", "token"):
        assert vietato not in prefs, vietato
    assert prefs.count("setItem(") == 1 and "s.setItem(CHIAVE, view)" in prefs
    assert "if (!VIEWS.includes(view)) return;" in prefs
    pagina = _senza_commenti(PAGE.read_text(encoding="utf-8"))
    assert "savePreferredView(view)" in pagina and pagina.count("savePreferredView(") == 1


def _fogli_del_crm() -> list[Path]:
    html = (SHELL / "index.html").read_text(encoding="utf-8")
    fogli = re.findall(r'<link rel="stylesheet" href="/os/assets/([^"]+)">', html)
    assert fogli == ["app.css", "aurora.css"], fogli          # l'ordine vero di caricamento
    return [ASSETS / f for f in fogli]


def _regole(css: str):
    """[(foglio, selettore, dichiarazioni)] di tutto il foglio, media query comprese."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out = []
    for sel, corpo in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        decl = dict((k.strip(), v.strip()) for k, v in
                    (d.split(":", 1) for d in corpo.split(";") if ":" in d))
        for s in sel.split(","):
            out.append((s.strip(), decl))
    return out


def test_cascata_vera_del_crm_nessuna_vista_nascosta():
    regole = []
    for foglio in _fogli_del_crm():
        regole += [(foglio.name, s, d) for s, d in _regole(foglio.read_text(encoding="utf-8"))]
    for nome, sel, decl in regole:
        # nessuna regola, in NESSUN foglio, nasconde i pulsanti o il gruppo delle viste
        if decl.get("display", "").startswith("none"):
            assert "agenda-view" not in sel and not sel.endswith(".agenda-views"), (nome, sel)
        assert "agenda-desktop-only" not in sel, (nome, sel)
    # ogni regola dell'Agenda che NASCONDE qualcosa ha almeno due classi: una
    # regola a una classe di aurora.css (es. `.btn { display: inline-flex }`)
    # non puo' piu' annullarla.
    for nome, sel, decl in regole:
        if "agenda" in sel and decl.get("display", "") == "none" and nome == "app.css":
            assert sel.count(".") >= 2, (nome, sel)
    # aurora.css non decide mai cosa si vede dell'Agenda
    for nome, sel, decl in regole:
        if nome == "aurora.css" and re.search(r"\.agenda-", sel):
            assert "display" not in decl, (nome, sel, decl)


def test_pagina_gesto_passivo_orologio_che_non_trattiene_e_nessuna_sostituzione():
    pagina = _senza_commenti(PAGE.read_text(encoding="utf-8"))
    assert "MOBILE_VIEWS" not in pagina and "agenda-desktop-only" not in pagina
    for evento in ("touchstart", "touchend", "touchcancel"):
        assert f"area.addEventListener('{evento}'" in pagina
    assert pagina.count("{ passive: true }") == 3 and "preventDefault" not in pagina[pagina.index("touchstart"):pagina.index("touchend") + 600]
    assert "orologio.unref()" in pagina and "clearInterval(orologio)" in pagina
    # attraversare la soglia non naviga mai: ridisegna con i dati gia' caricati
    adegua = pagina[pagina.index("const adegua = () => {"):]
    adegua = adegua[:adegua.index("\n    };")]
    assert "vai(" not in adegua and "navigate(" not in adegua and "disegna();" in adegua
    viste = _senza_commenti(VIEWS_JS.read_text(encoding="utf-8"))
    assert "innerHTML" not in viste


SETTIMANA_PURA = """
import * as m from './agenda/agenda-model.mjs';
const A = (id, s, e, extra = {}) => ({ id, kind: 'appointment', status: 'scheduled', type: 'call', start_at: s, end_at: e, ...extra });
const out = {};
out.brevi = Object.keys(m.TYPE_LABELS).map((t) => [t, m.typeShortLabel({ type: t }), m.typeCategory({ type: t })]);
out.lista = [m.typeShortLabel({ appointment_type: 'buyer_visit' }), m.typeCategory({ appointment_type: 'notary' })];
out.collega = [m.typeShortLabel({ kind: 'busy', label: 'Occupato' }), m.typeCategory({ kind: 'busy' })];
out.ignoto = [m.typeShortLabel({ type: 'nuovo_tipo' }), m.typeCategory({ type: 'nuovo_tipo' }), m.typeShortLabel(null)];
out.famiglie = m.TYPE_CATEGORY_LABELS;
const giorno = [A(1, '2026-11-05T10:00:00+01:00', '2026-11-05T11:00:00+01:00'),
                A(2, '2026-11-05T10:30:00+01:00', '2026-11-05T11:30:00+01:00'),
                A(3, '2026-11-05T11:15:00+01:00', '2026-11-05T12:00:00+01:00'),
                A(4, '2026-11-05T15:00:00+01:00', '2026-11-05T16:00:00+01:00'),
                A(5, '2026-11-05T16:00:00+01:00', '2026-11-05T16:30:00+01:00')];
out.gruppi = m.clusterLayout(giorno).map((g) => ({ ids: g.indices.map((i) => giorno[i].id), s: g.start_at, e: g.end_at }));
out.vuoto = m.clusterLayout([]);
console.log(JSON.stringify(out));
"""


def test_settimana_tipo_abbreviato_famiglia_e_gruppi(tmp_path):
    o = _node(tmp_path, SETTIMANA_PURA)
    from appointments.enums import APPOINTMENT_TYPE_LABELS_IT

    tipi = {t for t, _, _ in o["brevi"]}
    assert tipi == set(APPOINTMENT_TYPE_LABELS_IT)                 # ogni tipo del backend ha la sua sigla
    for tipo, breve, famiglia in o["brevi"]:
        assert breve and len(breve) <= 6, (tipo, breve)          # sta in un blocco stretto
        assert famiglia in o["famiglie"], (tipo, famiglia)
    assert len({b for _, b, _ in o["brevi"]}) == len(o["brevi"])  # nessuna sigla ambigua
    assert dict((t, b) for t, b, _ in o["brevi"])["buyer_visit"] == "Visita"
    assert o["lista"] == ["Visita", "deal"]                      # anche le righe dell'API lista
    assert o["collega"] == ["Occup.", "busy"]
    assert o["ignoto"] == ["", "other", ""]                      # niente sigle inventate
    # sovrapposti in catena = un gruppo; i tocchi di bordo (16:00) non si sovrappongono
    assert [g["ids"] for g in o["gruppi"]] == [[1, 2, 3], [4], [5]]
    assert o["gruppi"][0]["s"] == "2026-11-05T09:00:00.000Z" and o["gruppi"][0]["e"] == "2026-11-05T11:00:00.000Z"
    assert o["vuoto"] == []
