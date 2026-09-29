"""Agenda: pannelli e dialog aperti sopravvivono al passaggio mobile/desktop.

Causa certificata in Chromium: la pagina ascolta la soglia
`(max-width: 767px)`; passando sotto la soglia con Settimana o Mese aperti
tornava SUBITO alla Lista con `navigate`, il router ridisegnava tutta la pagina
e i `<dialog>` figli della pagina (dettaglio, nuovo appuntamento,
Disponibilita', Link prenotazione, "+N") venivano staccati con i loro dati non
salvati. Inoltre il listener della pagina precedente restava registrato fino
al cambio di soglia successivo.

Comportamento voluto (e provato qui, sulla pagina VERA nello stub di DOM):
  * con un pannello/dialog aperto la pagina NON si ricostruisce: stesso nodo,
    stessi valori, stessa scheda; la vista si adegua (Lista) quando l'ultimo
    dialog si chiude;
  * senza pannelli Settimana/Mese -> Lista come prima; mobile -> desktop non
    ridisegna nulla e non riapre pannelli chiusi;
  * UN solo listener sulla soglia per volta, anche dopo molti cambi e molte
    navigazioni.

Lo stub di P26-4 non ha `matchMedia` e i suoi dialog non emettono `close`: qui,
e SOLO in questo file, una media query pilotabile e un `close` come nel
browser (asincrono, non risale, si cattura sugli antenati, rispetta `once`).
Senza node le prove sono SKIPPED (da riportare come BLOCKED), mai passate.
"""
from __future__ import annotations

import json

import pytest

from tests import test_a30_5_create_ui as a30_5
from tests import test_a30_7_schedule_ui as a30_7
from tests import test_a30_13b_quick_booking_ui as a30_13b
from tests import test_agenda_disponibilita_ui as dispo
from tests import test_agenda_ux_completion_ui as ux
from tests.test_a30_5_create_ui import staged  # noqa: F401

NODE = a30_5.NODE

pytestmark = pytest.mark.skipif(
    NODE is None, reason="node non disponibile: prove responsive Agenda NON eseguite (BLOCKED)")

SOGLIA = r"""
globalThis.__mq = { matches: false, reg: [], chiamate: 0 };
window.matchMedia = (q) => ({
  media: q,
  get matches() { return __mq.matches; },
  addEventListener(t, fn) { if (t === 'change' && !__mq.reg.includes(fn)) __mq.reg.push(fn); },
  removeEventListener(t, fn) { if (t === 'change') __mq.reg = __mq.reg.filter((f) => f !== fn); },
});
globalThis.__resize = async (mobile) => {
  if (__mq.matches !== mobile) {        // come il browser: `change` solo attraversando la soglia
    __mq.matches = mobile;
    const prima = window.location.hash;
    for (const fn of [...__mq.reg]) { __mq.chiamate += 1; fn({ matches: mobile }); }
    if (window.location.hash !== prima) await window._fire('hashchange');
  }
  await __settle(40);
};

// Eventi dei dialog come nel browser: capture e once; `close` asincrono, non
// risale ma passa dai listener in cattura degli antenati.
const __P = __dom.El.prototype;
const __addEv = __P.addEventListener, __remEv = __P.removeEventListener;
__P.addEventListener = function (type, fn, opt) {
  const capture = opt === true || !!(opt && opt.capture);
  const once = !!(opt && typeof opt === 'object' && opt.once);
  if (capture) { ((this._cap ||= {})[type] ||= []).push({ fn, once }); return; }
  if (!once) { __addEv.call(this, type, fn); return; }
  const nodo = this;
  const w = function (...a) { __remEv.call(nodo, type, w); return fn.apply(this, a); };
  (this._once ||= new Map()).set(fn, w);
  __addEv.call(this, type, w);
};
__P.removeEventListener = function (type, fn, opt) {
  if (this._cap && this._cap[type]) this._cap[type] = this._cap[type].filter((l) => l.fn !== fn);
  const w = this._once && this._once.get(fn);
  __remEv.call(this, type, w || fn);
};
__P.showModal = function () { this._open = true; this.setAttribute('open', ''); };
__P.close = function () {
  if (!this._open) return;
  this._open = false;
  delete this.attributes.open;
  const bersaglio = this;
  setTimeout(() => {
    const antenati = [];
    for (let n = bersaglio.parentNode; n; n = n.parentNode) antenati.unshift(n);
    for (const n of antenati) {
      for (const l of [...((n._cap || {}).close || [])]) {
        if (l.once) n._cap.close = n._cap.close.filter((x) => x !== l);
        l.fn({ type: 'close', target: bersaglio });
      }
    }
    bersaglio.dispatch('close', { target: bersaglio });
  }, 0);
};
"""

HELP = r"""
const pagina = () => C().querySelector('.agenda-page');
let __pid = 0;
function idPagina() { const p = pagina(); if (p && !p.__id) p.__id = ++__pid; return p ? p.__id : null; }
const aperti = () => C().querySelectorAll('dialog').filter((d) => d._open === true);
function segna(nome) { const d = aperti()[0]; d.__segno = nome; }
function stato() {
  return { hash: window.location.hash, pagina: idPagina(), pagine: C().querySelectorAll('.agenda-page').length,
           aperti: aperti().map((d) => d.__segno || d.className), listener: __mq.reg.length };
}
// il router dello stub non sente il cambio di hash da solo: come il browser, lo si notifica
async function nav(azione) {
  const h = window.location.hash;
  await azione();
  await wait(10);
  if (window.location.hash !== h) await window._fire('hashchange');
  await wait();
}
const chiudi = (d) => nav(async () => { d.close(); });
const chiudiTutto = () => nav(async () => { for (const d of aperti()) d.close(); });
const clic = (testo) => nav(async () => { bottone(C(), testo).dispatch('click'); });
"""

A = "/api/appointments"
RIGA = {**a30_7.RIGA, "start_at": "2026-09-29T10:00:00+02:00", "end_at": "2026-09-29T11:00:00+02:00"}
CARD = {"id": 12, "kind": "appointment", "appointment_type": "inspection", "status": "requested",
        "start_at": RIGA["start_at"], "end_at": RIGA["end_at"], "assigned_user_id": None}
# cinque appuntamenti sovrapposti mercoledi' 30/09 alle 15: la colonna mostra "+N"
FOLLA = [{"id": 30 + i, "kind": "appointment", "appointment_type": "call", "status": "scheduled",
          "start_at": "2026-09-30T15:00:00+02:00", "end_at": "2026-09-30T16:00:00+02:00",
          "assigned_user_id": 3, "agent_name": "Anna Agente"} for i in range(5)]
CALENDARIO = {"items": [CARD, *FOLLA]}


def _rotte():
    rt = a30_5._rt()
    prima = [
        ("GET", f"{A}/calendar?", [rt.ok(CALENDARIO)]),
        ("GET", f"{A}/12", [rt.ok({**a30_7.DETTAGLIO, "appointment": RIGA})]),
        ("GET", f"{A}/booking-links", [rt.ok({"items": []})]),
        ("GET", f"{A}/agents/4/availability-exceptions?", [rt.ok({"items": []})]),
    ]
    return (ux.TASTIERA + SOGLIA
            + "\n".join(f"__route({json.dumps(m)}, {json.dumps(p)}, ...{json.dumps(r)});"
                        for m, p, r in prima)
            + "\n" + dispo._rotte("agency_owner"))


def run(staged, scenario, hash="#/agenda/settimana/2026-09-28"):  # noqa: F811
    return a30_13b.run(staged, HELP + scenario, _rotte(), hash=hash)


# ---------------------------------------------------------------------------
# A-G, J, +N: con un pannello aperto nulla si ricostruisce
# ---------------------------------------------------------------------------

APRI = {
    # nome: (come si apre, cosa si scrive, cosa si legge)
    "disponibilita": ("await clic('Disponibilità'); dlg().querySelector('[data-tab=\"exceptions\"]').dispatch('click'); await wait();"
                      " const s = f('[data-field=\"availability-agent\"]'); s.value = '4'; s.dispatch('change'); await wait();"
                      " dlg().querySelector('[data-tab=\"exceptions\"]').dispatch('click'); await wait();",
                      "f('[data-field=\"reason\"]').value = 'Ferie Pasqua';",
                      "({ agente: f('[data-field=\"availability-agent\"]').value, motivo: f('[data-field=\"reason\"]').value,"
                      "   scheda: dlg().querySelector('[data-tab=\"exceptions\"]').getAttribute('aria-selected') })"),
    "orari": ("await clic('Disponibilità');",
              "const g = dlg().querySelector('[data-day=\"2\"]'); const c = g.querySelector('[data-field=\"active\"]');"
              " c.checked = true; c.dispatch('change'); g.querySelector('[data-field=\"start\"]').value = '08:15';",
              "({ attivo: dlg().querySelector('[data-day=\"2\"]').querySelector('[data-field=\"active\"]').checked,"
              "   inizio: dlg().querySelector('[data-day=\"2\"]').querySelector('[data-field=\"start\"]').value })"),
    "link": ("await clic('Link prenotazione'); bottone(dlg(), 'Crea link').dispatch('click'); await wait();",
             "f('[data-field=\"label\"]').value = 'Campagna autunno'; f('input[aria-label=\"Durata in minuti\"]').value = '45';",
             "({ etichetta: f('[data-field=\"label\"]').value, durata: f('input[aria-label=\"Durata in minuti\"]').value })"),
    "nuovo": ("await apriNuovo();",
              "f('[data-field=\"start\"]').value = '11:30'; f('[data-field=\"date\"]').value = '2026-10-01';",
              "({ inizio: f('[data-field=\"start\"]').value, data: f('[data-field=\"date\"]').value })"),
    "dettaglio": ("C().querySelector('[data-appointment-id=\"12\"]').dispatch('click'); await wait();",
                  "",
                  "({ testo: C().querySelector('dialog.agenda-drawer').visibleText().slice(0, 200) })"),
    "altri": ("C().querySelector('.agenda-more').dispatch('click'); await wait();",
              "",
              "({ righe: C().querySelector('dialog.agenda-overflow').querySelectorAll('[data-appointment-id]').length })"),
}


ATTESI = {
    "disponibilita": {"agente": "4", "motivo": "Ferie Pasqua", "scheda": "true"},
    "orari": {"attivo": True, "inizio": "08:15"},
    "link": {"etichetta": "Campagna autunno", "durata": "45"},
    "nuovo": {"inizio": "11:30", "data": "2026-10-01"},
}


def _valori_scritti(nome, valori):
    """Quello che si legge prima del resize e' davvero quello che si e' scritto."""
    if nome in ATTESI:
        assert valori == ATTESI[nome], valori
    elif nome == "dettaglio":
        assert "Tortoreto" in valori["testo"], valori
    else:
        assert valori["righe"] >= 2, valori


def _giro(nome, vista, da_mobile):
    apri, scrivi, leggi = APRI[nome]
    return f"""
      await __resize({str(da_mobile).lower()});
      {apri}
      {scrivi}
      segna('{nome}');
      const prima = {{ s: stato(), v: {leggi} }};
      await __resize({str(not da_mobile).lower()});
      const dopo = {{ s: stato(), v: {leggi} }};
      await __resize({str(da_mobile).lower()});
      await __resize({str(not da_mobile).lower()});
      const cicli = {{ s: stato(), v: {leggi} }};
      await chiudiTutto();
      const chiuso = stato();
      report({{ prima, dopo, cicli, chiuso }});
    """


@pytest.mark.parametrize("nome", list(APRI))
@pytest.mark.parametrize("vista", ["settimana", "mese"])
def test_desktop_mobile_pannello_aperto_resta_vivo_coi_suoi_dati(staged, nome, vista):
    if nome in ("dettaglio", "altri") and vista == "mese":
        pytest.skip("card e '+N' si aprono dalla griglia, non dal mese")
    out = run(staged, _giro(nome, vista, False), hash=f"#/agenda/{vista}/2026-09-28")
    prima, dopo, cicli, chiuso = out["prima"], out["dopo"], out["cicli"], out["chiuso"]
    assert prima["s"]["aperti"] == [nome], prima
    for fase in (dopo, cicli):
        assert fase["s"]["aperti"] == [nome], fase                # STESSO nodo, ancora aperto
        assert fase["s"]["pagina"] == prima["s"]["pagina"], fase  # nessuna ricostruzione
        assert fase["s"]["pagine"] == 1 and fase["s"]["listener"] == 1
        assert fase["v"] == prima["v"], (prima["v"], fase["v"])   # valori, scheda, agente intatti
        assert fase["s"]["hash"] == f"#/agenda/{vista}/2026-09-28"
    _valori_scritti(nome, prima["v"])
    # si chiude sul mobile: la vista si adegua ADESSO, una pagina, nessun dialog
    assert chiuso["hash"] == "#/agenda/lista/2026-09-28", chiuso
    assert chiuso["aperti"] == [] and chiuso["pagine"] == 1 and chiuso["listener"] == 1


@pytest.mark.parametrize("nome", ["disponibilita", "orari", "link", "nuovo"])
def test_mobile_desktop_pannello_aperto_resta_vivo_coi_suoi_dati(staged, nome):
    out = run(staged, _giro(nome, "lista", True), hash="#/agenda/lista/2026-09-28")
    prima, dopo, cicli, chiuso = out["prima"], out["dopo"], out["cicli"], out["chiuso"]
    _valori_scritti(nome, prima["v"])
    for fase in (dopo, cicli):
        assert fase["s"]["aperti"] == [nome] and fase["s"]["pagina"] == prima["s"]["pagina"], fase
        assert fase["v"] == prima["v"] and fase["s"]["listener"] == 1
    # si chiude sul desktop: la Lista e' valida anche li', niente da adeguare
    assert chiuso["hash"] == "#/agenda/lista/2026-09-28" and chiuso["aperti"] == []
    assert chiuso["pagina"] == prima["s"]["pagina"]


def test_chiuso_di_nuovo_su_desktop_prima_di_chiudere_resta_settimana(staged):
    out = run(staged, """
      await clic('Disponibilità'); segna('d'); const p = stato();
      await __resize(true); await __resize(false);
      await chiudiTutto();
      report({ p, fine: stato() });
    """)
    assert out["fine"]["hash"] == "#/agenda/settimana/2026-09-28"
    assert out["fine"]["pagina"] == out["p"]["pagina"] and out["fine"]["aperti"] == []


def test_dettaglio_poi_azione_poi_chiusura_non_ricostruisce_a_meta(staged):
    # il dialog dell'azione si apre SOPRA il pannello: chiudendo l'azione il
    # pannello e' ancora aperto, la vista non cambia finche' non si chiude anche lui
    out = run(staged, """
      C().querySelector('[data-appointment-id="12"]').dispatch('click'); await wait();
      await __resize(true);
      const azione = C().querySelector('dialog.agenda-drawer').querySelectorAll('button')
        .find((b) => b.textContent.trim() === 'Annulla appuntamento');
      azione.dispatch('click'); await wait();
      const due = stato();
      await chiudi(dlg());
      const soloPannello = stato();
      await chiudi(C().querySelector('dialog.agenda-drawer'));
      report({ due, soloPannello, fine: stato() });
    """)
    assert len(out["due"]["aperti"]) == 2 and out["due"]["hash"].startswith("#/agenda/settimana/")
    assert out["soloPannello"]["aperti"] == ["modal agenda-drawer"]
    assert out["soloPannello"]["pagina"] == out["due"]["pagina"]
    assert out["fine"]["hash"] == "#/agenda/lista/2026-09-28" and out["fine"]["aperti"] == []


# ---------------------------------------------------------------------------
# H, I, K, L
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("vista", ["settimana", "mese"])
def test_H_senza_pannelli_desktop_mobile_va_in_lista_e_non_torna_indietro(staged, vista):
    out = run(staged, """
      const d = stato();
      await __resize(true); const m = stato();
      await __resize(false); const d2 = stato();
      report({ d, m, d2 });
    """, hash=f"#/agenda/{vista}/2026-09-28")
    assert out["m"]["hash"] == "#/agenda/lista/2026-09-28" and out["m"]["pagina"] != out["d"]["pagina"]
    assert out["d2"]["hash"] == "#/agenda/lista/2026-09-28" and out["d2"]["pagina"] == out["m"]["pagina"]
    assert out["m"]["aperti"] == [] and out["d2"]["aperti"] == []


@pytest.mark.parametrize("vista", ["lista", "giorno"])
def test_I_viste_valide_ovunque_non_ridisegnano_mai(staged, vista):
    out = run(staged, """
      const p = stato(); const letture = () => __calls.length;
      const n = letture();
      for (const m of [true, false, true, false, true]) await __resize(m);
      report({ p, fine: stato(), nuove: letture() - n });
    """, hash=f"#/agenda/{vista}/2026-09-28")
    assert out["fine"]["pagina"] == out["p"]["pagina"] and out["fine"]["hash"] == out["p"]["hash"]
    assert out["nuove"] == 0                                  # nessun ricaricamento


def test_K_mobile_desktop_non_riapre_pannelli_chiusi(staged):
    out = run(staged, """
      await __resize(true);
      await clic('Disponibilità'); await chiudiTutto();
      await apriNuovo(); await chiudiTutto();
      for (const m of [false, true, false]) await __resize(m);
      report({ fine: stato() });
    """, hash="#/agenda/lista/2026-09-28")
    assert out["fine"]["aperti"] == []


def test_L_un_solo_listener_sulla_soglia_dopo_cicli_e_navigazioni(staged):
    out = run(staged, """
      const conti = [];
      for (let i = 0; i < 4; i += 1) {
        await __resize(true); conti.push(__mq.reg.length);
        await __resize(false); conti.push(__mq.reg.length);
        await clic('Settimana'); conti.push(__mq.reg.length);
      }
      for (let i = 0; i < 3; i += 1) {
        await clic('▶');
        conti.push(__mq.reg.length);
      }
      __mq.chiamate = 0;
      await __resize(true);
      report({ conti, chiamate: __mq.chiamate, pagine: C().querySelectorAll('.agenda-page').length });
    """)
    assert set(out["conti"]) == {1}, out["conti"]
    assert out["chiamate"] == 1 and out["pagine"] == 1
