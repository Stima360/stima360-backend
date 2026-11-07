"""SELEZIONE MULTIPLA dei Contatti - collaudo NEL BROWSER (Chromium) sull'interfaccia vera, con
PostgreSQL VERO (banco 2C, usa-e-getta, tutte le migrazioni) e l'app avviata nel processo del test.
Dati solo sintetici.

  01  desktop, titolare: una casella, piu' caselle, «Seleziona questa pagina», pagina successiva
      (la selezione resta), ordine Z–A (resta), «Deseleziona tutti»; «Seleziona tutti i risultati»
      (solo i visibili: non il riservato di un agente ne' l'altra agenzia), una casella tolta; la
      ricerca cambiata azzera; conferma: motivo e numero scritto obbligatori, «Annulla» non sposta
      nulla; poi lo spostamento a blocchi, il riepilogo con il bloccato, «Annulla: ripristina»;
      nel Cestino: due card selezionate e «Ripristina selezionati», poi «Ripristina questa operazione»
  02  agente: «Seleziona tutti i risultati» prende solo i suoi contatti
  03  iPhone (390 px): 5.200 contatti, tutti i risultati, numero scritto, avanzamento, riepilogo;
      la barra sta sopra la barra del telefono; nessuno scorrimento orizzontale; poi «Ripristina
      questa operazione» dal Cestino
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from tests.test_blocco_2c_integrated_postgres import CLUSTER_VERIFIED, DSN, PASSWORD, _q, app, banco  # noqa: F401
from tests.test_visite_browser_postgres import _node_con_playwright, server  # noqa: F401

pytestmark = [
    pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL locale"),
    pytest.mark.skipif(not CLUSTER_VERIFIED, reason="cluster usa-e-getta non dichiarato verificato "
                       "(BLOCCO_2C_TEST_CLUSTER_VERIFIED=1)"),
]

SHOTS = os.getenv("MULTI_SHOTS_DIR")

COMUNE = r"""
const { chromium, devices } = require('playwright');
const out = { errors: [] };
const pausa = (p, ms) => p.waitForTimeout(ms);
async function entra(browser, email, password, opts) {
  const ctx = await browser.newContext(opts);
  const page = await ctx.newPage();
  page.on('pageerror', (e) => out.errors.push(String(e)));
  page.on('console', (m) => { if (m.type() === 'error' && !/favicon|Failed to load resource|status of 40[49]/.test(m.text())) out.errors.push(m.text()); });
  await page.goto(`${BASE}/os/`);
  await page.fill('#login-email', email);
  await page.fill('#login-password', password);
  await page.click('#login-form button[type=submit]');
  await pausa(page, 1500);
  return page;
}
const pronta = async (page, area) => {
  await pausa(page, 600);
  await page.waitForFunction((a) => { const e = document.querySelector(a); return e && !/Caricamento/.test(e.textContent); }, area, { timeout: 20000 });
};
const vai = async (page, hash, area) => { await page.evaluate((h) => { window.location.hash = h; }, hash); await pronta(page, area); };
const CA = '#contatti-list-area';
const cerca = async (page, testo) => { await page.fill('#contatti-search', testo); await pausa(page, 400); await pronta(page, CA); };
const BARRA = (id = 'contatti-bulk-bar') => `[data-bulk-bar-for="${id}"]`;
const conta = (page, id = 'contatti-bulk-bar') => page.$eval(BARRA(id), (b) => (b.hidden ? 0 : Number(b.querySelector('[data-bulk-count]').textContent.replace(/\D/g, ''))));
const righe = (page) => page.$$eval(`${CA} [data-bulk-select]`, (els) => els.map((e) => Number(e.dataset.bulkSelect)));
const foto = async (page, name) => { if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: false }); };
const sommario = async (page, dlg) => {
  await page.waitForSelector(`${dlg} [data-bulk-summary]`, { timeout: 900000 });
  return page.$eval(`${dlg} [data-bulk-summary]`, (s) => ({
    done: Number(s.querySelector('[data-bulk-done]').dataset.bulkDone),
    gruppi: Object.fromEntries([...s.querySelectorAll('[data-bulk-group]')].map((g) => [g.dataset.bulkGroup, g.textContent.replace(/\s+/g, ' ').trim()])),
  }));
};
const selezionabili = (page, cerca) => page.evaluate(async (q) => {
  const r = await fetch(`/api/core/contacts/selection?search=${encodeURIComponent(q)}`, { credentials: 'same-origin' });
  return (await r.json()).total;
}, cerca);
"""

SCRIPT_DESKTOP = COMUNE + r"""
(async () => {
  const browser = await chromium.launch();
  const page = await entra(browser, 'b2c.owner_a@example.test', PASSWORD, { viewport: { width: 1280, height: 900 }, deviceScaleFactor: 2 });
  const D = '#contact-bulk-dialog';
  const r = {};
  await vai(page, '#/contatti', CA);
  await cerca(page, 'Massa');
  const p1 = await righe(page);
  await page.check(`[data-bulk-select="${p1[0]}"]`);
  await page.check(`[data-bulk-select="${p1[1]}"]`);
  r.due = await conta(page);
  r.url_dopo_casella = await page.evaluate(() => window.location.hash);       // la casella non apre la scheda
  await page.uncheck(`[data-bulk-select="${p1[1]}"]`);
  r.uno = await conta(page);
  await page.check('[data-bulk-page]');
  r.pagina = await conta(page);
  await page.click('#contatti-next'); await pronta(page, CA);
  const p2 = await righe(page);
  r.pagina2_box = await page.$eval('[data-bulk-page]', (b) => b.checked);
  for (const id of p2.slice(0, 3)) await page.check(`[data-bulk-select="${id}"]`);
  r.dopo_pagina2 = await conta(page);
  await page.click('#contatti-sort [data-sort=desc]'); await pronta(page, CA);
  r.dopo_ordine = await conta(page);
  r.barra_desktop = await page.evaluate(() => {
    const b = document.querySelector('[data-bulk-bar-for="contatti-bulk-bar"]').getBoundingClientRect();
    return { top: b.top, bottom: b.bottom, h: window.innerHeight };
  });
  await foto(page, 'desktop-selezione');
  await page.click(`${BARRA()} [data-bulk-clear]`);
  r.dopo_deseleziona = await conta(page);
  await page.click('[data-bulk-all]');
  await page.waitForFunction(() => /Selezionati tutti/.test(document.querySelector('[data-bulk-scope]').textContent));
  r.tutti = await conta(page);
  r.tutti_testo = await page.$eval('[data-bulk-scope]', (e) => e.textContent);
  const visibili = await righe(page);
  await page.uncheck(`[data-bulk-select="${visibili[0]}"]`);
  r.tutti_meno_uno = await conta(page);
  await cerca(page, 'Massa 1');
  r.dopo_ricerca = await conta(page);
  await cerca(page, 'Massa');
  await page.click('#contatti-sort [data-sort=asc]'); await pronta(page, CA);   // in A–Z il primo e' un «Massa N»
  await page.click('[data-bulk-all]');
  await page.waitForFunction(() => /Selezionati tutti/.test(document.querySelector('[data-bulk-scope]').textContent));
  const tolto = (await righe(page))[0];
  await page.uncheck(`[data-bulk-select="${tolto}"]`);
  const n = await conta(page);
  r.n = n;
  // conferma: nulla senza motivo e numero
  await page.click(`${BARRA()} [data-bulk-action]`);
  await page.waitForSelector(`${D}[open] [data-bulk-confirm]`);
  r.titolo = await page.$eval(`${D} .trash-title`, (e) => e.textContent);
  r.disab_iniziale = await page.$eval(`${D} [data-bulk-confirm]`, (b) => b.disabled);
  await page.selectOption(`${D} [data-bulk-reason]`, 'duplicate');
  r.disab_senza_numero = await page.$eval(`${D} [data-bulk-confirm]`, (b) => b.disabled);
  await page.fill(`${D} [data-bulk-typed]`, '999');
  r.disab_numero_sbagliato = await page.$eval(`${D} [data-bulk-confirm]`, (b) => b.disabled);
  await page.fill(`${D} [data-bulk-typed]`, String(n));
  r.abilitato = !(await page.$eval(`${D} [data-bulk-confirm]`, (b) => b.disabled));
  await foto(page, 'desktop-conferma');
  await page.click(`${D} [data-bulk-cancel]`);
  r.dopo_annulla_selezionabili = await selezionabili(page, 'Massa');
  // ora davvero
  await page.click(`${BARRA()} [data-bulk-action]`);
  await page.waitForSelector(`${D}[open] [data-bulk-confirm]`);
  await page.selectOption(`${D} [data-bulk-reason]`, 'duplicate');
  await page.fill(`${D} [data-bulk-typed]`, n.toLocaleString('it-IT'));
  await page.click(`${D} [data-bulk-confirm]`);
  r.spostamento = await sommario(page, D);
  await foto(page, 'desktop-riepilogo');
  r.dopo_spostamento_selezionabili = await selezionabili(page, 'Massa');
  r.lista_dopo = await righe(page);
  await page.click(`${D} [data-bulk-undo]`);
  r.annulla = await sommario(page, D);
  await page.click(`${D} [data-bulk-close]`);
  await pronta(page, CA);
  r.dopo_undo_selezionabili = await selezionabili(page, 'Massa');
  // di nuovo, per il Cestino
  await cerca(page, 'Massa');
  await page.click('[data-bulk-all]');
  await page.waitForFunction(() => /Selezionati tutti/.test(document.querySelector('[data-bulk-scope]').textContent));
  const n2 = await conta(page);
  await page.click(`${BARRA()} [data-bulk-action]`);
  await page.waitForSelector(`${D}[open] [data-bulk-confirm]`);
  await page.selectOption(`${D} [data-bulk-reason]`, 'test_record');
  await page.fill(`${D} [data-bulk-typed]`, String(n2));
  await page.click(`${D} [data-bulk-confirm]`);
  r.secondo = await sommario(page, D);
  await page.click(`${D} [data-bulk-close]`);
  // Cestino: due card e «Ripristina selezionati», poi l'operazione
  const T = '[data-contact-trash-list]';
  await vai(page, '#/cestino/contatti', T);
  await page.waitForSelector('[data-contact-trash-op-restore]');
  r.barre_orfane = await page.$$eval('[data-bulk-bar-for="contatti-bulk-bar"]', (els) => els.length);
  r.operazioni = await page.$$eval('[data-contact-trash-op]', (els) => els.map((e) => e.textContent.replace(/\s+/g, ' ').trim()));
  const carte = await page.$$eval('[data-contact-trash-select]', (els) => els.map((e) => Number(e.dataset.contactTrashSelect)));
  await page.check(`[data-contact-trash-select="${carte[0]}"]`);
  await page.check(`[data-contact-trash-select="${carte[1]}"]`);
  r.cestino_selezionati = await conta(page, 'contatti-cestino-bulk-bar');
  await foto(page, 'desktop-cestino');
  await page.click(`${BARRA('contatti-cestino-bulk-bar')} [data-bulk-action]`);
  const C = '[data-contact-bulk-dialog]';
  await page.waitForSelector(`${C}[open] [data-bulk-confirm]`);
  await page.click(`${C} [data-bulk-confirm]`);
  r.cestino_ripristino = await sommario(page, C);
  await page.click(`${C} [data-bulk-close]`);
  await pronta(page, T);
  await page.waitForSelector('[data-contact-trash-op-restore]');
  await page.click('[data-contact-trash-op-restore]');
  await page.waitForSelector(`${C}[open] [data-bulk-confirm]`);
  r.op_titolo = await page.$eval(`${C} .trash-title`, (e) => e.textContent);
  await page.click(`${C} [data-bulk-confirm]`);
  r.op_ripristino = await sommario(page, C);
  await page.click(`${C} [data-bulk-close]`);
  await pausa(page, 800);
  r.op_dopo = await page.$$eval('[data-contact-trash-op]', (els) => els.length);
  out.desktop = r;

  // 02 agente
  const ag = await entra(browser, 'b2c.agent_a@example.test', PASSWORD, { viewport: { width: 1280, height: 900 } });
  await vai(ag, '#/contatti', CA);
  await cerca(ag, 'Massa');
  await ag.click('[data-bulk-all]');
  await ag.waitForFunction(() => /Selezionati tutti/.test(document.querySelector('[data-bulk-scope]').textContent));
  out.agente = { tutti: await conta(ag), righe: await righe(ag) };
  await browser.close();
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""

SCRIPT_IPHONE = COMUNE + r"""
(async () => {
  const browser = await chromium.launch();
  const page = await entra(browser, 'b2c.owner_a@example.test', PASSWORD, { ...devices['iPhone 13'] });
  const D = '#contact-bulk-dialog';
  const r = {};
  await vai(page, '#/contatti', CA);
  await cerca(page, 'Rubrica');
  await page.click('[data-bulk-all]');
  await page.waitForFunction(() => /Selezionati tutti/.test(document.querySelector('[data-bulk-scope]').textContent), null, { timeout: 30000 });
  r.tutti = await conta(page);
  r.barra = await page.evaluate(() => {
    const b = document.querySelector('[data-bulk-bar-for="contatti-bulk-bar"]').getBoundingClientRect();
    const nav = document.querySelector('.mobile-nav');
    const n = nav ? nav.getBoundingClientRect() : null;
    return { bottom: b.bottom, left: b.left, right: b.right, navTop: n ? n.top : null,
             scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth };
  });
  await foto(page, 'iphone-selezione');
  await page.click(`${BARRA()} [data-bulk-action]`);
  await page.waitForSelector(`${D}[open] [data-bulk-confirm]`);
  await page.selectOption(`${D} [data-bulk-reason]`, 'duplicate');
  await page.fill(`${D} [data-bulk-typed]`, r.tutti.toLocaleString('it-IT'));
  await foto(page, 'iphone-conferma');
  const t0 = Date.now();
  await page.click(`${D} [data-bulk-confirm]`);
  await page.waitForSelector(`${D} [data-bulk-progress]`);
  await pausa(page, 4000);
  r.avanzamento = await page.$eval(`${D} [data-bulk-progress-text]`, (e) => e.textContent);
  await foto(page, 'iphone-avanzamento');
  r.spostamento = await sommario(page, D);
  r.secondi_spostamento = Math.round((Date.now() - t0) / 1000);
  await foto(page, 'iphone-riepilogo');
  await page.click(`${D} [data-bulk-close]`);
  await pronta(page, CA);
  const T = '[data-contact-trash-list]';
  await vai(page, '#/cestino/contatti', T);
  await page.waitForSelector('[data-contact-trash-op-restore]');
  await foto(page, 'iphone-cestino');
  r.cestino_largo = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }));
  const t1 = Date.now();
  await page.click('[data-contact-trash-op-restore]');
  const C = '[data-contact-bulk-dialog]';
  await page.waitForSelector(`${C}[open] [data-bulk-confirm]`);
  await page.click(`${C} [data-bulk-confirm]`);
  r.ripristino = await sommario(page, C);
  r.secondi_ripristino = Math.round((Date.now() - t1) / 1000);
  out.iphone = r;
  await browser.close();
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


def _contatti(b, agenzia, n, prefisso, agente=None, privato=None):
    righe = _q(b, """INSERT INTO contacts (agency_id, display_name, first_name, last_name, assigned_agent_id, private_user_id)
                     SELECT %s, %s || ' ' || g, %s, 'N' || g, %s, %s FROM generate_series(1, %s) g RETURNING id""",
               (agenzia, prefisso, prefisso, agente, privato, n))
    return sorted(r[0] for r in righe)


def _esegui(node, env, script_testo, tmp_path, server, timeout):
    shots = SHOTS or str(tmp_path)
    Path(shots).mkdir(parents=True, exist_ok=True)
    script = tmp_path / "multi.js"
    script.write_text(f"const BASE = {json.dumps(server)}; const PASSWORD = {json.dumps(PASSWORD)}; "
                      f"const SHOTS = {json.dumps(shots)};\n" + script_testo, encoding="utf-8")
    esito = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=timeout, env=env)
    assert esito.returncode == 0, esito.stderr[-4000:]
    out = json.loads(esito.stdout.strip().splitlines()[-1])
    assert out["errors"] == [], out["errors"]
    return out


def _nel_cestino(b, ids):
    return _q(b, "SELECT count(*) FROM contacts WHERE id = ANY(%s) AND deleted_at IS NOT NULL", (ids,))[0][0]


def test_01_02_selezione_conferma_cestino_e_agente(app, server, tmp_path):
    found = _node_con_playwright()
    if found is None:
        pytest.skip("node con playwright non disponibile: collaudo nel browser NON eseguito (BLOCKED)")
    node, env = found
    ids = app["ids"]
    massa = _contatti(app, ids["a"], 130, "Massa")
    suoi = _contatti(app, ids["a"], 5, "Massa", agente=ids["agent_a"])
    [bloccato] = _contatti(app, ids["a"], 1, "Massa Bloccato")
    _q(app, "INSERT INTO leads (agency_id, contact_id, pipeline, status, stage) VALUES (%s, %s, 'sell', 'open', 'new')",
       (ids["a"], bloccato))
    [riservato] = _contatti(app, ids["a"], 1, "Massa Riservato", agente=ids["agent_a"], privato=ids["agent_a"])
    di_b = _contatti(app, ids["b"], 20, "Massa")
    visibili = len(massa) + len(suoi) + 1                                  # il riservato e l'altra agenzia no

    out = _esegui(node, env, SCRIPT_DESKTOP, tmp_path, server, 900)
    d = out["desktop"]
    assert (d["due"], d["uno"], d["pagina"]) == (2, 1, 50) and d["url_dopo_casella"] == "#/contatti"
    assert d["pagina2_box"] is False and d["dopo_pagina2"] == 53 and d["dopo_ordine"] == 53
    assert d["dopo_deseleziona"] == 0
    assert 0 < d["barra_desktop"]["top"] < d["barra_desktop"]["bottom"] <= d["barra_desktop"]["h"]   # visibile
    assert d["tutti"] == visibili and f"{visibili}" in d["tutti_testo"].replace(".", "")
    assert d["tutti_meno_uno"] == visibili - 1 and d["dopo_ricerca"] == 0
    assert d["n"] == visibili - 1 and str(visibili - 1) in d["titolo"]
    assert d["disab_iniziale"] and d["disab_senza_numero"] and d["disab_numero_sbagliato"] and d["abilitato"]
    assert d["dopo_annulla_selezionabili"] == visibili                         # «Annulla»: nulla spostato
    assert d["spostamento"]["done"] == visibili - 2                            # meno il tolto e il bloccato
    assert "Massa Bloccato" in d["spostamento"]["gruppi"]["blocked"]
    assert d["dopo_spostamento_selezionabili"] == 2
    assert d["annulla"]["done"] == visibili - 2 and d["dopo_undo_selezionabili"] == visibili
    assert d["secondo"]["done"] == visibili - 1
    assert d["barre_orfane"] == 0                                              # la barra se ne va con la vista
    assert len(d["operazioni"]) >= 1 and f"{visibili - 1} contatti" in d["operazioni"][0]
    assert d["cestino_selezionati"] == 2 and d["cestino_ripristino"]["done"] == 2
    assert d["op_ripristino"]["done"] == visibili - 3 and str(visibili - 3) in d["op_titolo"]
    assert d["op_dopo"] == 0
    tutti = massa + suoi + [bloccato]
    assert _nel_cestino(app, tutti) == 0
    assert _nel_cestino(app, [riservato]) == 0 and _nel_cestino(app, di_b) == 0      # mai toccati

    ag = out["agente"]
    assert ag["tutti"] == len(suoi) + 1 and set(ag["righe"]) == set(suoi) | {riservato}


def test_03_iphone_5200_contatti(app, server, tmp_path, capsys):
    found = _node_con_playwright()
    if found is None:
        pytest.skip("node con playwright non disponibile: collaudo nel browser NON eseguito (BLOCKED)")
    node, env = found
    ids = app["ids"]
    rubrica = _contatti(app, ids["a"], 5200, "Rubrica")
    di_b = _contatti(app, ids["b"], 300, "Rubrica")
    t0 = time.monotonic()
    out = _esegui(node, env, SCRIPT_IPHONE, tmp_path, server, 1800)
    i = out["iphone"]
    with capsys.disabled():
        print(f"\n[iPhone] 5.200 contatti: spostamento {i['secondi_spostamento']}s, ripristino "
              f"{i['secondi_ripristino']}s, totale {time.monotonic() - t0:.0f}s; avanzamento: {i['avanzamento']!r}")
    assert i["tutti"] == 5200
    b = i["barra"]
    assert b["scroll"] <= b["client"] + 1 and b["left"] >= 0 and b["right"] <= b["client"] + 1
    assert b["navTop"] is None or b["bottom"] <= b["navTop"] + 1, b            # sopra la barra del telefono
    assert "di 5.200" in i["avanzamento"]
    assert i["spostamento"]["done"] == 5200 and i["ripristino"]["done"] == 5200
    assert i["cestino_largo"]["scroll"] <= i["cestino_largo"]["client"] + 1
    assert _nel_cestino(app, rubrica) == 0 and _nel_cestino(app, di_b) == 0
