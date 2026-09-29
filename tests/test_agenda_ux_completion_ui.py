"""Rifiniture UX dell'Agenda previste dal piano A30-4 congelato, ESEGUITE.

`main.js` VERO, router, sessione e pagina Agenda nello stub di DOM di
P26-4/P27-7 con il driver di A30-13B.1 (riusato, non copiato). Si prova:

  * §3/§12 LEGENDA sempre visibile: gli stati veri del backend con le stesse
    classi delle card; "Occupato" solo per chi puo' vedere impegni di colleghi
    in una vista a griglia;
  * §12 FUSO: "Orario di Roma" scritto nella barra, in ogni vista;
  * §12 TASTIERA: ← → T come ◀ ▶ Oggi, solo con il fuoco sulla barra (o su
    nessun elemento); ignorate in input/select/textarea/contenteditable, con
    Ctrl/Alt/Meta, durante la composizione, con un dialog aperto e con il fuoco
    fuori dalla barra; il listener non si accumula fra una pagina e l'altra;
  * §10 RETE: errore leggibile + [Riprova] che ripete la STESSA richiesta;
    i dati gia' a schermo restano con l'ora a cui risalgono; un doppio click su
    Riprova non disegna due volte (vince l'ultima richiesta).

  * §4.3 MESE (solo desktop, G3): griglia lunedi'->domenica, mesi da
    28/29/30/31 giorni, dicembre->gennaio, nessun appuntamento perso, click
    sul giorno -> Giorno, Oggi, ◀ ▶ e ← →, niente Mese su smartphone, filtri,
    vince l'ultima richiesta;
  * badge "Online" SOLO per `source='booking_link'` (A30-12), in Settimana,
    Giorno, Lista e Mese; mai su un "Occupato" di collega.

Senza node le prove sono SKIPPED (da riportare come BLOCKED), mai passate.
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from tests import test_a30_5_create_ui as a30_5
from tests import test_a30_13b_quick_booking_ui as a30_13b
from tests.test_a30_5_create_ui import staged  # noqa: F401

NODE = a30_5.NODE

pytestmark = pytest.mark.skipif(
    NODE is None, reason="node non disponibile: prove UX Agenda NON eseguite (BLOCKED)")

MODEL = a30_5.ASSETS / "agenda" / "agenda-model.js"
STATI = ["Richiesta", "Fissato", "Confermato", "Completato", "Annullato", "Assente", "Spostato"]

# Il DOM di P26-4 non registra i listener del documento ne' il fuoco: qui, e
# SOLO in questo file, lo si fa prima che l'app venga importata.
TASTIERA = r"""
const __docL = {};
document.addEventListener = (t, fn) => { (__docL[t] ||= []).push(fn); };
document.removeEventListener = (t, fn) => { __docL[t] = (__docL[t] || []).filter((f) => f !== fn); };
document.activeElement = null;
__dom.El.prototype.focus = function () { document.activeElement = this; };
Object.defineProperty(__dom.El.prototype, 'open', {
  configurable: true, get() { return this._open === true; } });
Object.defineProperty(__dom.El.prototype, 'isContentEditable', {
  configurable: true, get() { return this.getAttribute('contenteditable') === 'true'; } });
// Come nel browser: `replaceChildren` STACCA i figli tolti (lo stub li lasciava
// col vecchio `parentNode`, cosi' una pagina gia' sostituita sembrava ancora
// nel documento).
const __sostituisci = __dom.El.prototype.replaceChildren;
__dom.El.prototype.replaceChildren = function (...nodi) {
  for (const figlio of this.children) figlio.parentNode = null;
  return __sostituisci.apply(this, nodi);
};
const __html = Object.getOwnPropertyDescriptor(__dom.El.prototype, 'innerHTML');
Object.defineProperty(__dom.El.prototype, 'innerHTML', {
  configurable: true, get: __html.get,
  set(valore) { for (const figlio of this.children) figlio.parentNode = null; __html.set.call(this, valore); },
});
globalThis.__listenerTastiera = () => (__docL.keydown || []).length;
globalThis.__tasto = async (key, extra = {}) => {
  const ev = { key, target: document.activeElement || document.body, ctrlKey: false, altKey: false,
               metaKey: false, isComposing: false, defaultPrevented: false, ...extra,
               preventDefault() { this.defaultPrevented = true; } };
  const prima = window.location.hash;
  for (const fn of [...(__docL.keydown || [])]) fn(ev);
  if (window.location.hash !== prima) await window._fire('hashchange');   // come il browser
  await __settle(40);
  return ev.defaultPrevented;
};
"""

UX = r"""
const pagina = () => C().querySelector('.agenda-page');
function statoUx() {
  const leg = C().querySelector('[data-legend]');
  const err = C().querySelector('[data-load-error]');
  return {
    hash: window.location.hash,
    legenda: leg ? leg.querySelectorAll('[data-legend-key]').map((s) => [s.dataset.legendKey, s.textContent, s.className]) : null,
    fuso: C().querySelector('[data-timezone]') ? C().querySelector('[data-timezone]').textContent : null,
    errore: err ? err.visibleText() : null,
    riprova: C().querySelectorAll('[data-retry]').length,
    card: C().querySelectorAll('[data-appointment-id]').map((b) => b.dataset.appointmentId),
    letture: __calls.filter((c) => String(c.url).startsWith('/api/appointments/calendar?')).map((c) => c.url),
  };
}
"""

CAL = {"items": [{"id": 700, "kind": "appointment", "appointment_type": "seller_meeting",
                  "status": "scheduled", "start_at": "2026-09-29T10:00:00+02:00",
                  "end_at": "2026-09-29T11:00:00+02:00", "assigned_user_id": 3,
                  "agent_name": "Anna Agente"}]}


def _rotte(sessione="agent", *, calendario=None, lista=None):
    rt = a30_5._rt()
    calendario = calendario or (rt.ok(CAL),)
    lista = lista or (rt.ok({"items": []}),)
    voci = [
        ("GET", "/api/operator-auth/me", [rt.ok(a30_13b._sessione(sessione))]),
        ("GET", "/api/appointments/agents", [rt.ok(a30_13b.AGENTI)]),
        ("GET", "/api/appointments/calendar?", list(calendario)),
        ("GET", "/api/appointments?", list(lista)),
    ]
    return TASTIERA + "\n".join(f"__route({json.dumps(m)}, {json.dumps(p)}, ...{json.dumps(r)});"
                                for m, p, r in voci)


def run(staged, scenario, rotte, hash="#/agenda/settimana/2026-09-28"):  # noqa: F811
    return a30_13b.run(staged, UX + scenario, rotte, hash=hash)


def _oggi_roma():
    return datetime.now(ZoneInfo("Europe/Rome")).date().isoformat()


# ---------------------------------------------------------------------------
# K, L - LEGENDA E FUSO
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sessione,vista,occupato", [
    ("agent", "settimana", True), ("agent", "giorno", True), ("agent", "lista", False),
    ("agency_owner", "settimana", False), ("supreme", "giorno", False),
])
def test_K_L_legenda_e_orario_di_roma_in_ogni_vista(staged, sessione, vista, occupato):
    out = run(staged, "report({ u: statoUx() });", _rotte(sessione),
              hash=f"#/agenda/{vista}/2026-09-28")
    u = out["u"]
    assert u["fuso"] == "Orario di Roma"
    assert [x[1] for x in u["legenda"]][:7] == STATI                  # gli stati veri, in ordine
    for chiave, _, classe in u["legenda"][:7]:
        assert classe == f"agenda-badge agenda-badge-{chiave}"        # le stesse classi delle card
    assert (["busy", "Occupato (collega)", "agenda-badge agenda-badge-busy"] in u["legenda"]) is occupato
    assert u["legenda"][-1] == ["online", "Online: prenotato dal link", "agenda-badge agenda-badge-online"]
    assert len(u["legenda"]) == 7 + (1 if occupato else 0) + 1


def test_K2_legenda_uguale_alle_etichette_del_backend():
    """La legenda NON e' un secondo vocabolario: e' STATUS_LABELS, a sua volta
    specchio di `appointments/state_machine.py` (test_15 di A30-4)."""
    from appointments.state_machine import _ETICHETTE
    driver = (f"const m = await import('{MODEL.as_posix()}');"
              "console.log(JSON.stringify([m.legendEntries(), m.legendEntries({ withBusy: true }), m.TIMEZONE_LABEL]));")
    esito = subprocess.run([NODE, "--input-type=module", "-e", driver], capture_output=True, text=True,
                           timeout=60, env={"TZ": "UTC", "PATH": "/usr/bin:/bin"})
    base, occupato, fuso = json.loads(esito.stdout)
    stati = [v for v in base if v["key"] != "online"]
    assert {v["key"]: v["label"] for v in stati} == dict(_ETICHETTE)
    assert base[-1]["key"] == "online" and occupato[-1]["key"] == "online"
    assert occupato[-2]["key"] == "busy" and len(occupato) == len(base) + 1
    assert fuso == "Orario di Roma"


# ---------------------------------------------------------------------------
# E, F, G - SCORCIATOIE
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("vista,tasto,atteso", [
    ("settimana", "ArrowLeft", "#/agenda/settimana/2026-09-21"),
    ("settimana", "ArrowRight", "#/agenda/settimana/2026-10-05"),
    ("giorno", "ArrowLeft", "#/agenda/giorno/2026-09-27"),
    ("giorno", "ArrowRight", "#/agenda/giorno/2026-09-29"),
    ("lista", "ArrowRight", "#/agenda/lista/2026-10-05"),
    ("settimana", "t", None), ("giorno", "T", None),
])
def test_E_F_G_frecce_e_T_come_i_pulsanti(staged, vista, tasto, atteso):
    out = run(staged, f"""
      const fermato = await __tasto({json.dumps(tasto)});
      report({{ u: statoUx(), fermato }});
    """, _rotte(), hash=f"#/agenda/{vista}/2026-09-28")
    atteso = atteso or f"#/agenda/{vista}/{_oggi_roma()}"
    assert out["u"]["hash"] == atteso
    assert out["fermato"] is True                                   # preventDefault: niente scroll


def test_E2_con_il_fuoco_sulla_barra_funzionano_ancora(staged):
    out = run(staged, """
      bottone(C(), 'Aggiorna').focus();
      await __tasto('ArrowRight');
      report({ u: statoUx() });
    """, _rotte())
    assert out["u"]["hash"] == "#/agenda/settimana/2026-10-05"


# ---------------------------------------------------------------------------
# H - SCORCIATOIE IGNORATE
# ---------------------------------------------------------------------------

IGNORATE = {
    "select-filtro": "C().querySelector('[data-filter=\"type\"]').focus(); const ev = {};",
    "input": "const i = document.createElement('input'); pagina().querySelector('.agenda-toolbar').appendChild(i); i.focus(); const ev = {};",
    "textarea": "const i = document.createElement('textarea'); pagina().querySelector('.agenda-toolbar').appendChild(i); i.focus(); const ev = {};",
    "contenteditable": "const i = document.createElement('div'); i.setAttribute('contenteditable', 'true'); pagina().querySelector('.agenda-toolbar').appendChild(i); i.focus(); const ev = {};",
    "ctrl": "const ev = { ctrlKey: true };",
    "alt": "const ev = { altKey: true };",
    "meta": "const ev = { metaKey: true };",
    "composizione": "const ev = { isComposing: true };",
    "gia-gestito": "const ev = { defaultPrevented: true };",
    "fuoco-sulla-card": "C().querySelector('[data-appointment-id]').focus(); const ev = {};",
    "dialog-aperto": "bottone(C(), '+ Nuovo appuntamento').dispatch('click'); await wait(); const ev = {};",
}


@pytest.mark.parametrize("caso", list(IGNORATE))
@pytest.mark.parametrize("tasto", ["ArrowLeft", "ArrowRight", "t"])
def test_H_scorciatoie_ignorate(staged, caso, tasto):
    out = run(staged, f"""
      {IGNORATE[caso]}
      const fermato = await __tasto({json.dumps(tasto)}, ev);
      report({{ u: statoUx(), fermato, dialogo: !!(dlg() && dlg()._open) }});
    """, _rotte())
    assert out["u"]["hash"] == "#/agenda/settimana/2026-09-28", caso
    if caso != "gia-gestito":
        assert out["fermato"] is False, caso                         # il tasto resta al campo/browser
    if caso == "dialog-aperto":
        assert out["dialogo"] is True


def test_H2_il_listener_non_si_accumula_fra_le_pagine(staged):
    """Ogni navigazione ridisegna la pagina: il listener della pagina vecchia
    si toglie da solo al primo tasto, e un tasto sposta di UN periodo solo."""
    out = run(staged, """
      bottone(C(), '▶').dispatch('click'); await window._fire('hashchange'); await __settle(40);
      bottone(C(), '▶').dispatch('click'); await window._fire('hashchange'); await __settle(40);
      const prima = __listenerTastiera();
      await __tasto('ArrowRight');
      const dopoUno = [window.location.hash, __listenerTastiera()];
      const conteggi = [];
      for (const k of ['ArrowLeft', 'ArrowLeft', 'ArrowRight', 't']) { await __tasto(k); conteggi.push(__listenerTastiera()); }
      report({ u: statoUx(), prima, dopoUno, conteggi });
    """, _rotte())
    # tre pagine disegnate, tre listener; al primo tasto quelli delle pagine
    # gia' sostituite si tolgono e il periodo avanza di UNO solo (+3 settimane)
    assert out["prima"] == 3
    assert out["dopoUno"] == ["#/agenda/settimana/2026-10-19", 2]    # la pagina nuova + quella appena lasciata
    assert out["conteggi"] == [2, 2, 2, 2]                            # mai di piu', anche navigando ancora


# ---------------------------------------------------------------------------
# I, J - RETE E RIPROVA
# ---------------------------------------------------------------------------

def test_I_errore_di_rete_riprova_ripete_la_stessa_richiesta_e_i_dati_restano(staged):
    rt = a30_5._rt()
    out = run(staged, """
      const primo = statoUx();
      bottone(C(), 'Aggiorna').dispatch('click'); await wait();
      const fallito = statoUx();
      C().querySelector('[data-retry]').dispatch('click'); await wait();
      report({ primo, fallito, u: statoUx() });
    """, _rotte(calendario=(rt.ok(CAL), {"throw": True}, rt.ok(CAL))))
    primo, fallito, u = out["primo"], out["fallito"], out["u"]
    assert primo["card"] == ["700"] and primo["errore"] is None
    assert fallito["errore"].startswith("Impossibile contattare il server. Verifica la connessione.")
    assert "Sono mostrati i dati delle" in fallito["errore"]        # i dati vecchi, dichiarati
    assert fallito["card"] == ["700"] and fallito["riprova"] == 1  # restano a schermo
    assert len(set(u["letture"])) == 1 and len(u["letture"]) == 3  # la STESSA richiesta
    assert u["errore"] is None and u["riprova"] == 0 and u["card"] == ["700"]


def test_I2_primo_caricamento_fallito_niente_schermata_ambigua(staged):
    rt = a30_5._rt()
    out = run(staged, """
      const fallito = statoUx();
      C().querySelector('[data-retry]').dispatch('click'); await wait();
      report({ fallito, u: statoUx() });
    """, _rotte(calendario=({"status": 500, "body": {"detail": "boom"}}, rt.ok(CAL))))
    f = out["fallito"]
    assert f["errore"].startswith("Errore del server (500). Riprova.")
    assert "Sono mostrati i dati" not in f["errore"]               # niente dati: non lo si dice
    assert "boom" not in f["errore"] and f["riprova"] == 1 and f["card"] == []
    assert out["u"]["card"] == ["700"] and out["u"]["errore"] is None


def test_J_doppio_click_su_riprova_vince_l_ultima_richiesta(staged):
    rt = a30_5._rt()
    out = run(staged, """
      const b = C().querySelector('[data-retry]');
      b.dispatch('click'); b.dispatch('click');
      await wait();
      report({ u: statoUx() });
    """, _rotte(calendario=({"throw": True}, rt.ok(CAL))))
    u = out["u"]
    assert u["card"] == ["700"]                                     # una sola card, nessun doppione
    assert u["errore"] is None and len(set(u["letture"])) == 1


def test_I3_riprova_e_un_vero_pulsante(staged):
    out = run(staged, """
      const b = C().querySelector('[data-retry]');
      report({ tag: b.tagName, tipo: b.getAttribute('type') || b.type });
    """, _rotte(calendario=({"throw": True},)))
    assert out["tag"] == "BUTTON" and out["tipo"] == "button"


# ---------------------------------------------------------------------------
# MODELLO PURO + MUTAZIONI
# ---------------------------------------------------------------------------

def _node(script):
    esito = subprocess.run([NODE, "--input-type=module", "-e",
                            f"const m = await import('{MODEL.as_posix()}');\n{script}"],
                           capture_output=True, text=True, timeout=60,
                           env={"TZ": "America/New_York", "PATH": "/usr/bin:/bin"})
    if esito.returncode != 0:
        raise AssertionError(esito.stderr[-2000:])
    return json.loads(esito.stdout.strip().splitlines()[-1])


def test_modello_shortcut_action_tabella_completa():
    out = _node("""
      const s = (key, ev = {}, ctx = { inToolbar: true }) => m.shortcutAction({ key, ...ev }, ctx);
      console.log(JSON.stringify([
        s('ArrowLeft'), s('ArrowRight'), s('t'), s('T'), s('x'), s('ArrowUp'),
        s('ArrowLeft', { ctrlKey: true }), s('t', { metaKey: true }), s('ArrowRight', { altKey: true }),
        s('t', { isComposing: true }), s('t', { defaultPrevented: true }),
        s('t', {}, { inToolbar: true, editing: true }), s('t', {}, { inToolbar: true, dialogOpen: true }),
        s('t', {}, { inToolbar: false }), m.shortcutAction(null, { inToolbar: true }),
        m.loadedAtLabel(new Date('2026-09-29T08:42:00Z')), m.loadedAtLabel(null),
      ]));""")
    assert out == ["prev", "next", "today", "today", None, None, None, None, None, None, None,
                   None, None, None, None, "10:42", ""]           # ora di ROMA anche a New York


def test_mut_senza_guardia_dei_campi_la_prova_H_lo_vede(tmp_path, staged):  # noqa: F811
    """Mutazione: `editing` ignorato. Con il fuoco su un <select> dei filtri
    la freccia cambierebbe settimana: la prova H se ne accorgerebbe."""
    import shutil
    copia = tmp_path / "assets"
    shutil.copytree(staged, copia)
    modello = copia / "agenda" / "agenda-model.js"
    testo = modello.read_text(encoding="utf-8")
    vecchio = "  if (editing || dialogOpen || !inToolbar) return null;"
    assert testo.count(vecchio) == 1
    modello.write_text(testo.replace(vecchio, "  if (dialogOpen || !inToolbar) return null;"), encoding="utf-8")
    pagina = (copia / "views" / "agenda" / "agenda-page.js").read_text(encoding="utf-8")
    assert "barra.contains(attivo)" in pagina
    out = run(copia, """
      const s = C().querySelector('[data-filter="type"]');
      // il select sta nella barra dei filtri: la si considera "barra" per la mutazione
      pagina().querySelector('.agenda-toolbar').appendChild(s); s.focus();
      await __tasto('ArrowRight');
      report({ u: statoUx() });
    """, _rotte())
    assert out["u"]["hash"] == "#/agenda/settimana/2026-10-05"     # la mutazione passa: H fallirebbe


def test_mut_riprova_senza_dati_dichiarati_la_prova_I_lo_vede(tmp_path, staged):  # noqa: F811
    import shutil
    rt = a30_5._rt()
    copia = tmp_path / "assets"
    shutil.copytree(staged, copia)
    pagina = copia / "views" / "agenda" / "agenda-page.js"
    testo = pagina.read_text(encoding="utf-8")
    vecchio = "      riprova.addEventListener('click', () => carica(messaggio));"
    assert testo.count(vecchio) == 1
    pagina.write_text(testo.replace(vecchio, "      riprova.addEventListener('click', () => {});"),
                      encoding="utf-8")
    out = run(copia, """
      bottone(C(), 'Aggiorna').dispatch('click'); await wait();
      C().querySelector('[data-retry]').dispatch('click'); await wait();
      report({ u: statoUx() });
    """, _rotte(calendario=(rt.ok(CAL), {"throw": True}, rt.ok(CAL))))
    assert out["u"]["errore"] is not None and len(out["u"]["letture"]) == 2   # I fallirebbe


# ---------------------------------------------------------------------------
# MESE (piano §4.3, G3) - modello puro
# ---------------------------------------------------------------------------

def test_A_B_C_D_griglia_e_intervallo_del_mese_modello():
    out = _node("""
      const g = (k) => m.monthGrid(k).map((w) => w.map((c) => c.key.slice(5) + (c.inMonth ? '' : '*')));
      const r = (k) => { const x = m.rangeFor('month', k); return [x.from, x.to, x.days.length, x.days[0], x.days[x.days.length - 1]]; };
      console.log(JSON.stringify({
        ott: g('2026-10-17'), giu: g('2026-06-05'), mar: g('2026-03-31'), feb26: g('2026-02-10'),
        rOtt: r('2026-10-17'), rFeb26: r('2026-02-10'), rFeb28: r('2028-02-29'), rApr: r('2026-04-30'), rGen: r('2027-01-31'),
        shift: [m.shiftKey('month', '2026-12-15', 1), m.shiftKey('month', '2027-01-31', -1),
                m.shiftKey('month', '2026-03-31', -1), m.shiftKey('week', '2026-09-28', 1), m.shiftKey('day', '2026-09-28', -1)],
        label: [m.formatMonth('2026-10-17'), m.formatMonth('2027-01-01')],
        first: m.firstOfMonth('2026-10-17'),
      }));""")
    # A - ottobre 2026 comincia di giovedi': la griglia parte dal lunedi' 28/09
    assert out["ott"][0] == ["09-28*", "09-29*", "09-30*", "10-01", "10-02", "10-03", "10-04"]
    assert out["ott"][-1] == ["10-26", "10-27", "10-28", "10-29", "10-30", "10-31", "11-01*"]
    assert len(out["ott"]) == 5 and sum(1 for w in out["ott"] for c in w if not c.endswith("*")) == 31
    # B - giugno 2026 comincia di lunedi' (nessun giorno prima); marzo 2026 di domenica (6 righe)
    assert out["giu"][0][0] == "06-01" and len(out["giu"]) == 5
    assert out["mar"][0] == ["02-23*", "02-24*", "02-25*", "02-26*", "02-27*", "02-28*", "03-01"]
    assert len(out["mar"]) == 6
    # C - 28/29/30/31 giorni (febbraio 2026 comincia di domenica)
    assert out["rFeb26"][2] == 28 and out["rFeb28"][2] == 29 and out["rApr"][2] == 30 and out["rGen"][2] == 31
    assert all(len(w) == 7 for w in out["feb26"])
    # l'intervallo chiesto all'API e' il mese CIVILE, in ora di Roma (anche col cambio d'ora)
    assert out["rOtt"][:2] == ["2026-10-01T00:00:00+02:00", "2026-11-01T00:00:00+01:00"]
    assert out["rOtt"][3:] == ["2026-10-01", "2026-10-31"]
    # D - dicembre -> gennaio e ritorno; le altre viste invariate
    assert out["shift"] == ["2027-01-01", "2026-12-01", "2026-02-01", "2026-10-05", "2026-09-27"]
    assert out["label"] == ["ottobre 2026", "gennaio 2027"] and out["first"] == "2026-10-01"


# ---------------------------------------------------------------------------
# MESE - pagina vera
# ---------------------------------------------------------------------------

def _app(i, giorno, ora="10:00", **extra):
    h = int(ora[:2])
    return {"id": i, "kind": "appointment", "appointment_type": "seller_meeting", "status": "scheduled",
            "start_at": f"{giorno}T{ora}:00+02:00", "end_at": f"{giorno}T{h + 1:02d}:{ora[3:]}:00+02:00",
            "assigned_user_id": 3, "agent_name": "Anna Agente", **extra}


MESE_ITEMS = {"items": [
    _app(801, "2026-10-01", "08:00"),                                   # primo giorno del mese
    _app(802, "2026-10-31", "18:00"),                                   # ultimo giorno
    *[_app(810 + i, "2026-10-14", f"{9 + i:02d}:00") for i in range(5)],  # 5 nello stesso giorno
    {**_app(820, "2026-09-30", "23:30"), "end_at": "2026-10-01T00:30:00+02:00"},  # scavalca il 1/10
    {"kind": "busy", "agent_id": 4, "agent_name": "Bruno Collega", "label": "Occupato",
     "start_at": "2026-10-20T10:00:00+02:00", "end_at": "2026-10-20T11:00:00+02:00"},
]}

MESE_JS = r"""
function statoMese() {
  const griglia = C().querySelector('.agenda-month');
  const celle = griglia ? griglia.querySelectorAll('[data-day]') : [];
  return {
    righe: griglia ? griglia.querySelectorAll('.agenda-month-row').length - 1 : 0,
    celle: celle.map((c) => [c.dataset.day, c.classList.contains('agenda-month-out'), c.dataset.count ?? null,
                            c.querySelectorAll('.agenda-month-chip').length,
                            c.querySelector('[data-more]') ? c.querySelector('[data-more]').textContent : null]),
    periodo: C().querySelector('.agenda-period') ? C().querySelector('.agenda-period').textContent : null,
    vista: (C().querySelector('.agenda-view-btn.active') || { dataset: {} }).dataset.view || null,
    meseDesktopOnly: (C().querySelectorAll('.agenda-view-btn').find((b) => b.dataset.view === 'month') || { classList: { contains: () => null } })
      .classList.contains('agenda-desktop-only'),
    online: griglia ? griglia.querySelectorAll('.agenda-badge-online').length : 0,
    chipCliccabili: C().querySelectorAll('.agenda-month-chip').filter((c) => (c.listeners.click || []).length).length,
  };
}
const cella = (k) => C().querySelector('.agenda-month').querySelectorAll('[data-day]').find((c) => c.dataset.day === k);
"""


def _run_mese(staged, scenario, rotte, hash="#/agenda/mese/2026-10-17"):  # noqa: F811
    return run(staged, MESE_JS + scenario, rotte, hash=hash)


def test_E_nessun_appuntamento_perso_e_celle_corrette(staged):
    rt = a30_5._rt()
    out = _run_mese(staged, "report({ m: statoMese(), u: statoUx() });",
                    _rotte(calendario=(rt.ok(MESE_ITEMS),)))
    m, u = out["m"], out["u"]
    assert m["vista"] == "month" and m["periodo"] == "ottobre 2026" and m["righe"] == 5
    celle = {c[0]: c for c in m["celle"]}
    assert len(celle) == 35
    assert celle["2026-10-01"][2] == "2"                    # il suo + quello che scavalca la mezzanotte
    assert celle["2026-10-31"][2] == "1"
    assert celle["2026-10-14"][2:] == ["5", 3, "+2 altri"]   # 3 righe, poi "+N altri" (piano §4.3)
    assert celle["2026-10-20"][2:4] == ["1", 1]             # l'Occupato del collega c'e'
    assert celle["2026-09-28"][1] is True and celle["2026-09-28"][2] is None   # contorno: nessun conteggio
    # ogni elemento della risposta (9: 8 appuntamenti + 1 Occupato) compare in una cella del mese;
    # quello che scavalca la mezzanotte del 30/09 conta nel 1/10 (il 30/09 e' contorno)
    assert sum(int(c[2]) for c in m["celle"] if c[2] is not None) == 9
    # UNA richiesta, il mese civile in ora di Roma (cambio d'ora compreso)
    assert u["letture"] == ["/api/appointments/calendar?from=2026-10-01T00%3A00%3A00%2B02%3A00"
                            "&to=2026-11-01T00%3A00%3A00%2B01%3A00"]
    assert m["chipCliccabili"] == 0                          # nessuna modifica dalla cella


def test_F_click_sul_giorno_porta_al_giorno(staged):
    rt = a30_5._rt()
    out = _run_mese(staged, """
      cella('2026-10-14').dispatch('click'); await window._fire('hashchange'); await __settle(40);
      report({ m: statoMese(), u: statoUx() });
    """, _rotte(calendario=(rt.ok(MESE_ITEMS),)))
    assert out["u"]["hash"] == "#/agenda/giorno/2026-10-14" and out["m"]["vista"] == "day"


@pytest.mark.parametrize("azione,atteso", [
    ("bottone(C(), '◀').dispatch('click')", "#/agenda/mese/2026-09-01"),
    ("bottone(C(), '▶').dispatch('click')", "#/agenda/mese/2026-11-01"),
    ("await __tasto('ArrowLeft')", "#/agenda/mese/2026-09-01"),
    ("await __tasto('ArrowRight')", "#/agenda/mese/2026-11-01"),
])
def test_H_precedente_e_successivo(staged, azione, atteso):
    out = _run_mese(staged, f"""
      {azione}; await window._fire('hashchange'); await __settle(40);
      report({{ u: statoUx(), m: statoMese() }});
    """, _rotte())
    assert out["u"]["hash"] == atteso and out["m"]["vista"] == "month"


def test_D2_dicembre_gennaio_nella_pagina(staged):
    out = _run_mese(staged, """
      bottone(C(), '▶').dispatch('click'); await window._fire('hashchange'); await __settle(40);
      const avanti = [window.location.hash, statoMese().periodo];
      bottone(C(), '◀').dispatch('click'); await window._fire('hashchange'); await __settle(40);
      report({ avanti, indietro: [window.location.hash, statoMese().periodo], u: statoUx() });
    """, _rotte(), hash="#/agenda/mese/2026-12-31")
    assert out["avanti"] == ["#/agenda/mese/2027-01-01", "gennaio 2027"]
    assert out["indietro"] == ["#/agenda/mese/2026-12-01", "dicembre 2026"]
    assert out["u"]["letture"][-1].startswith("/api/appointments/calendar?from=2026-12-01T00%3A00%3A00%2B01%3A00")


@pytest.mark.parametrize("via", ["bottone", "tasto"])
def test_G_oggi(staged, via):
    azione = "bottone(C(), 'Oggi').dispatch('click')" if via == "bottone" else "await __tasto('t')"
    out = _run_mese(staged, f"""
      {azione}; await window._fire('hashchange'); await __settle(40);
      report({{ u: statoUx(), m: statoMese() }});
    """, _rotte(), hash="#/agenda/mese/2025-01-10")
    oggi = _oggi_roma()
    assert out["u"]["hash"] == f"#/agenda/mese/{oggi}"
    celle_oggi = [c for c in out["m"]["celle"] if c[0] == oggi]
    assert len(celle_oggi) == 1 and celle_oggi[0][1] is False     # il mese di oggi


MOBILE = "window.matchMedia = () => ({ matches: true, addEventListener() {}, removeEventListener() {} });\n"


def test_I_su_smartphone_il_mese_non_esiste(staged):
    out = _run_mese(staged, "report({ m: statoMese(), u: statoUx() });",
                    MOBILE + _rotte(), hash="#/agenda/mese/2026-10-17")
    assert out["m"]["righe"] == 0 and out["m"]["vista"] == "list"   # degrada alla Lista
    assert out["m"]["meseDesktopOnly"] is True                       # il pulsante e' solo desktop
    assert out["u"]["letture"] == []                                 # nessuna richiesta del mese


@pytest.mark.parametrize("sessione", ["agency_owner", "agent"])
def test_J_i_filtri_si_applicano_al_mese(staged, sessione):
    rt = a30_5._rt()
    out = _run_mese(staged, """
      const t = C().querySelector('[data-filter="type"]'); t.value = 'inspection'; t.dispatch('change'); await wait();
      const s = C().querySelector('[data-filter="status"]'); s.value = 'confirmed'; s.dispatch('change'); await wait();
      report({ u: statoUx() });
    """, _rotte(sessione, calendario=(rt.ok(MESE_ITEMS),)))
    ultima = out["u"]["letture"][-1]
    assert "types=inspection" in ultima and "statuses=confirmed" in ultima
    assert ultima.startswith("/api/appointments/calendar?from=2026-10-01T00%3A00%3A00%2B02%3A00")


def test_K_nel_mese_vince_l_ultima_richiesta(staged):
    """La prima lettura del mese arriva DOPO la seconda (cambio filtro): non
    deve mai essere disegnata."""
    rt = a30_5._rt()
    lento = {"items": [_app(901, "2026-10-05")]}
    veloce = {"items": [_app(902, "2026-10-06")]}
    ritardo = r"""
const __f = globalThis.fetch; let __n = 0;
globalThis.fetch = async (u, o) => {
  const mio = String(u).includes('/calendar?') ? ++__n : 0;
  const r = await __f(u, o);
  if (mio === 1) await new Promise((ok) => setTimeout(ok, 120));
  return r;
};
"""
    out = _run_mese(staged, """
      const t = C().querySelector('[data-filter="type"]'); t.value = 'call'; t.dispatch('change');
      await new Promise((ok) => setTimeout(ok, 300)); await __settle(40);
      report({ m: statoMese() });
    """, _rotte(calendario=(rt.ok(lento), rt.ok(veloce))) + ritardo)
    celle = {c[0]: c[2] for c in out["m"]["celle"]}
    assert celle["2026-10-06"] == "1" and celle["2026-10-05"] == "0"


# ---------------------------------------------------------------------------
# BADGE "Online" (source = 'booking_link', A30-12)
# ---------------------------------------------------------------------------

ONLINE = _app(950, "2026-09-29", "15:00", source="booking_link")
ALTRE = [_app(951, "2026-09-30", "09:00", source="crm_manual"),
         _app(952, "2026-09-30", "11:00", source="legacy_stime_dettagliate"),
         _app(953, "2026-10-01", "09:00", source="a30_test", is_test=True),
         _app(954, "2026-10-01", "11:00"),
         _app(955, "2026-10-02", "09:00", source="booking_link_x"),
         _app(956, "2026-10-02", "11:00", source="BOOKING_LINK")]
BUSY_CON_SOURCE = {"kind": "busy", "agent_id": 4, "agent_name": "Bruno Collega", "label": "Occupato",
                   "source": "booking_link", "start_at": "2026-10-03T10:00:00+02:00",
                   "end_at": "2026-10-03T11:00:00+02:00"}

BADGE_JS = r"""
function badge() {
  return C().querySelectorAll('[data-appointment-id]').map((b) => [b.dataset.appointmentId,
    b.querySelectorAll('.agenda-badge-online').map((x) => x.textContent)]);
}
"""


def test_L_M_N_online_solo_per_booking_link_e_mai_su_occupato(staged):
    rt = a30_5._rt()
    out = run(staged, BADGE_JS + """
      const busy = C().querySelectorAll('.agenda-status-busy').map((c) => [c.visibleText(), c.querySelectorAll('.agenda-badge-online').length]);
      report({ b: badge(), busy });
    """, _rotte(calendario=(rt.ok({"items": [ONLINE, *ALTRE, BUSY_CON_SOURCE]}),)))
    b = dict(out["b"])
    assert b["950"] == ["Online"]                                        # L
    for i in ("951", "952", "953", "954", "955", "956"):
        assert b[i] == [], i                                             # M: solo il literal esatto
    assert out["busy"] == [["10:00–11:00OccupatoBruno Collega", 0]]      # N: niente badge, niente source


@pytest.mark.parametrize("vista", ["giorno", "lista", "mese"])
def test_O_online_coerente_in_giorno_lista_mese(staged, vista):
    rt = a30_5._rt()
    righe_lista = {"items": [{**ONLINE, "kind": None}, {**ALTRE[0], "kind": None}]}
    rotte = _rotte(calendario=(rt.ok({"items": [ONLINE, ALTRE[0]]}),), lista=(rt.ok(righe_lista),))
    data = "2026-09-29"
    out = run(staged, BADGE_JS + MESE_JS + """
      report({ b: badge(), m: statoMese() });
    """, rotte, hash=f"#/agenda/{vista}/{data}")
    if vista == "mese":
        assert out["m"]["online"] == 1                                    # una sola riga "Online"
    else:
        b = dict(out["b"])
        assert b["950"] == ["Online"] and b.get("951", []) == []


# ---------------------------------------------------------------------------
# MUTAZIONI richieste dal gate
# ---------------------------------------------------------------------------

def _mutante(tmp_path, staged, rel, vecchio, nuovo):  # noqa: F811
    import shutil
    copia = tmp_path / "assets"
    shutil.copytree(staged, copia)
    f = copia / rel
    testo = f.read_text(encoding="utf-8")
    assert testo.count(vecchio) == 1, vecchio
    f.write_text(testo.replace(vecchio, nuovo), encoding="utf-8")
    return copia


def test_mut_senza_month_nel_modello_il_mese_sparisce(tmp_path, staged):  # noqa: F811
    copia = _mutante(tmp_path, staged, "agenda/agenda-model.js",
                     "export const VIEWS = Object.freeze(['week', 'day', 'month', 'list']);",
                     "export const VIEWS = Object.freeze(['week', 'day', 'list']);")
    out = _run_mese(copia, "report({ m: statoMese() });", _rotte())
    assert out["m"]["vista"] != "month" and out["m"]["righe"] == 0      # test_E fallirebbe


def test_mut_mese_su_smartphone_viene_scoperto(tmp_path, staged):  # noqa: F811
    copia = _mutante(tmp_path, staged, "agenda/agenda-model.js",
                     "export const MOBILE_VIEWS = Object.freeze(['list', 'day']);",
                     "export const MOBILE_VIEWS = Object.freeze(['list', 'day', 'month']);")
    out = _run_mese(copia, "report({ m: statoMese() });", MOBILE + _rotte())
    assert out["m"]["righe"] > 0 and out["m"]["vista"] == "month"      # test_I fallirebbe


def test_mut_badge_a_tutte_le_source_viene_scoperto(tmp_path, staged):  # noqa: F811
    rt = a30_5._rt()
    copia = _mutante(tmp_path, staged, "agenda/agenda-model.js",
                     "return !!item && item.kind !== 'busy' && item.source === ONLINE_SOURCE;",
                     "return !!item && item.kind !== 'busy';")
    out = run(copia, BADGE_JS + "report({ b: badge() });",
              _rotte(calendario=(rt.ok({"items": [ONLINE, *ALTRE]}),)))
    assert dict(out["b"])["951"] == ["Online"]                         # test_L_M_N fallirebbe
