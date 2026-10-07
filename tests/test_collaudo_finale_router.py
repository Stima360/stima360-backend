"""COLLAUDO-FINALE-A-H - difetto trovato sul TEST live: una vista piu' lenta della
rotta PRECEDENTE sovrascriveva la nuova (URL e titolo «Immobili», contenuto
della scheda edificio di prima).

Router VERO (`core/router.js`) nello stub DOM di P26-4/A30-5, nessuna rete:

  r01  rotta lenta -> rotta veloce -> la lenta finisce DOPO: sullo schermo
       resta la veloce; la scrittura tardiva cade in una pagina staccata;
  r02  un errore tardivo della rotta superata non sostituisce la pagina nuova;
  r03  il toast mostrato prima della navigazione (sull'host
       `container.parentElement`) sopravvive al cambio di rotta, come quando
       viveva in <main>; un contenitore senza DOM completo funziona come prima.

Senza node la prova e' SKIPPED (BLOCKED), mai passata.
"""
from __future__ import annotations

import json
import subprocess

import pytest

from tests import test_a30_5_create_ui as a30_5
from tests.test_a30_5_create_ui import staged  # noqa: F401  (fixture riusata)

node = pytest.mark.skipif(a30_5.NODE is None, reason="node non disponibile: prova NON eseguita (BLOCKED)")


def _run(staged, scenario):  # noqa: F811
    driver = staged.parent / "driver-collaudo-router.mjs"
    driver.write_text(
        a30_5._dom()
        + f"\nconst R = await import('{(staged / 'core' / 'router.js').as_posix()}');\n"
        + "const C = document.getElementById('content');\n"
        + "const attesa = (ms = 0) => new Promise((r) => setTimeout(r, ms));\n"
        + scenario + "\n", encoding="utf-8")
    esito = subprocess.run([a30_5.NODE, str(driver)], capture_output=True, text=True, timeout=60,
                           cwd=staged.parent, env={"TZ": "Europe/Rome", "PATH": "/usr/bin:/bin"})
    if esito.returncode != 0:
        raise AssertionError(f"driver node fallito:\n{esito.stderr[-3000:]}\n{esito.stdout[-1500:]}")
    return json.loads(esito.stdout.strip().splitlines()[-1])


SCENARIO = r"""
let sblocca;
const lenta = new Promise((r) => { sblocca = r; });
R.registerRoute('edifici', async (container) => {
  container.innerHTML = '<p>Caricamento…</p>';
  const esito = await lenta;
  if (esito === 'errore') throw new Error('rete');
  container.innerHTML = '<h2 id="vecchia">Scheda edificio</h2>';
});
R.registerRoute('immobili', async (container) => {
  await attesa(1);
  container.innerHTML = '<h2 id="nuova">Immobili</h2>';
});
R.initRouter(C);
"""


@node
def test_r01_la_rotta_superata_non_sovrascrive(staged):  # noqa: F811
    out = _run(staged, SCENARIO + r"""
      window.location.hash = '#/edifici';
      const prima = R.renderCurrentRoute();
      window.location.hash = '#/immobili';
      await R.renderCurrentRoute();
      sblocca('ok'); await prima; await attesa(5);
      console.log(JSON.stringify({ testo: C.visibleText(), nuova: !!C.querySelector('#nuova'),
                                   vecchia: !!C.querySelector('#vecchia'), pagine: C.children.length }));
    """)
    assert out == {"testo": "Immobili", "nuova": True, "vecchia": False, "pagine": 1}, out


@node
def test_r02_un_errore_tardivo_non_sostituisce_la_pagina_nuova(staged):  # noqa: F811
    out = _run(staged, SCENARIO + r"""
      window.location.hash = '#/edifici';
      const prima = R.renderCurrentRoute();
      window.location.hash = '#/immobili';
      await R.renderCurrentRoute();
      sblocca('errore'); await prima; await attesa(5);
      console.log(JSON.stringify({ testo: C.visibleText(), errore: !!C.querySelector('.error-box') }));
    """)
    assert out == {"testo": "Immobili", "errore": False}, out


@node
def test_r03_il_toast_sopravvive_e_il_contenitore_minimo_funziona(staged):  # noqa: F811
    out = _run(staged, SCENARIO + r"""
      window.location.hash = '#/immobili';
      await R.renderCurrentRoute();
      // come showToast: sull'host `container.parentElement` della vista
      const host = C.children.find((c) => c.getAttribute('data-route-page') !== null).parentElement;
      const toast = document.createElement('div');
      toast.setAttribute('data-census-toast', ''); toast.dataset.censusToast = ''; toast.textContent = 'Edificio spostato nel Cestino';
      host.appendChild(toast);
      window.location.hash = '#/edifici';
      const dopo = R.renderCurrentRoute(); await attesa(1);
      const restaToast = !!C.querySelector('[data-census-toast]');
      sblocca('ok'); await dopo;
      // contenitore senza DOM completo (prove del solo router): come prima
      const minimo = { innerHTML: '', textContent: '' };
      R.initRouter(minimo);
      window.location.hash = '#/sconosciuta';
      await R.renderCurrentRoute();
      console.log(JSON.stringify({ host: host === C, restaToast, scheda: !!C.querySelector('#vecchia'),
                                   minimo: minimo.textContent }));
    """)
    assert out["host"] is True and out["restaToast"] is True and out["scheda"] is True
    assert "non trovata" in out["minimo"]
