"""AGENDA MOBILE - le quattro viste NEL BROWSER, a sei dimensioni di schermo, sull'app vera.

PostgreSQL usa-e-getta (banco 2C), app avviata nel processo del test, Playwright (Chromium) con
tocchi simulati sugli schermi touch. Carica la pagina VERA del CRM, quindi TUTTI i fogli di stile
che il CRM usa (app.css e aurora.css, nell'ordine di index.html): i vecchi test che leggevano solo
app.css non vedevano che aurora.css rimetteva in vista i pulsanti Settimana e Mese.

Dati solo sintetici, relativi al giorno di oggi a Roma: quattro appuntamenti sovrapposti oggi, uno
a cavallo della mezzanotte, una visita immobile, un annullato domani, un giorno affollato fra dieci
giorni, uno fra venti giorni (oltre il primo blocco della Lista), uno la domenica della settimana.

Cosa si prova, a 375, 390, 430, 768, 1280 px e iPhone in orizzontale (932x430):
  1-4  premere Giorno / Settimana / Mese / Lista mostra QUELLA vista (classe, pulsante attivo,
       indirizzo e contenuto: 1 colonna, 7 colonne lun-dom, griglia del mese, elenco cronologico);
  5    cambiare vista mantiene il giorno scelto (anche quello toccato nel Mese);
  6-7  frecce e Oggi;
  8    swipe orizzontale cambia periodo, uno scorrimento verticale no;
  9    un appuntamento si apre (pannello);
  10   filtro Agente in tutte le viste; stati predefiniti coerenti fra Lista e Mese;
  11   sovrapposizioni tutte raggiungibili ("+N");
  12   mezzanotte rappresentata in entrambi i giorni ("ieri 23:30", "domani 02:00");
  13   nessun appuntamento perso o fuori posto (id attesi = id mostrati);
  14   nessuno scorrimento orizzontale della pagina; calendario nella prima schermata; bersagli
       touch da 44px; ultima vista ricordata; "Mostra altri giorni"; "+" e menu "⋯".

Si salta (SKIPPED, da riportare come BLOCKED) senza PostgreSQL o senza Node/Playwright.
Con AGENDA_SCREENSHOTS_DIR impostata salva uno screenshot per vista e dimensione.
"""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tests.test_blocco_2c_integrated_postgres import CLUSTER_VERIFIED, DSN, PASSWORD, _q, app, banco  # noqa: F401
from tests.test_blocco_5a_visit_booking_postgres import _property
from tests.test_blocco_6a_visit_sheets_postgres import _contact
from tests.test_visite_browser_postgres import _node_con_playwright, server  # noqa: F401

pytestmark = [
    pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL locale"),
    pytest.mark.skipif(not CLUSTER_VERIFIED, reason="cluster usa-e-getta non dichiarato verificato "
                       "(BLOCCO_2C_TEST_CLUSTER_VERIFIED=1)"),
]

ROMA = ZoneInfo("Europe/Rome")
STATI_PREDEFINITI = {"requested", "scheduled", "confirmed", "completed", "no_show"}
VIEWPORTS = ["375", "390", "430", "768", "1280", "932x430"]


def _ins(b, *, chi, tipo, stato, inizio, minuti, pid, cid):
    extra_col, extra_val = "", ""
    if stato == "cancelled":
        extra_col, extra_val = ", cancelled_at, cancelled_reason", ", NOW(), 'prova agenda mobile'"
    return _q(b, "INSERT INTO appointments (agency_id, assigned_user_id, appointment_type, status, start_at, end_at, "
                 f"contact_id, property_id, source, created_by_user_id{extra_col}) "
                 f"VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'crm_manual',%s{extra_val}) RETURNING id",
              (b["ids"]["a"], b["ids"][chi], tipo, stato, inizio, inizio + timedelta(minutes=minuti), cid, pid,
               b["ids"][chi]))[0][0]


def _seme(b):
    """Gli appuntamenti sintetici, relativi a oggi (Roma). Restituisce le righe con id e intervalli."""
    _q(b, "SET session_replication_role = replica; DELETE FROM appointments WHERE agency_id = %s", (b["ids"]["a"],))
    pid, cid = _property(b), _contact(b, "a")
    oggi = datetime.now(ROMA).replace(hour=0, minute=0, second=0, microsecond=0)
    lun = oggi - timedelta(days=oggi.weekday())
    ora = lambda g, h, m=0: g + timedelta(hours=h, minutes=m)  # noqa: E731
    piano = [
        ("ov_owner", "owner_a", "seller_meeting", "scheduled", ora(oggi, 10), 60),
        ("ov_admin", "admin_a", "inspection", "scheduled", ora(oggi, 10, 15), 60),
        ("ov_agent2", "agent2_a", "call", "scheduled", ora(oggi, 10, 30), 30),
        ("ov_richiesta", "agent_a", "inspection", "requested", ora(oggi, 10, 45), 60),
        ("visita", "agent_a", "buyer_visit", "scheduled", ora(oggi, 15), 90),
        ("mezzanotte", "agent2_a", "seller_meeting", "scheduled", ora(oggi, -1, 30), 150),
        ("annullato", "agent_a", "call", "cancelled", ora(oggi + timedelta(days=1), 18), 30),
        ("domenica", "agent_a", "call", "scheduled", ora(lun + timedelta(days=6), 20), 30),
        *[(f"pieno_{i}", ["owner_a", "admin_a", "agent_a", "agent2_a"][i % 4], "call", "scheduled",
           ora(oggi + timedelta(days=10), 9 + i), 30) for i in range(5)],
        ("pieno_visita", "agent_a", "buyer_visit", "scheduled", ora(oggi + timedelta(days=10), 16), 45),
        ("lontano", "agent_a", "notary", "scheduled", ora(oggi + timedelta(days=20), 12), 60),
    ]
    righe = {}
    for nome, chi, tipo, stato, inizio, minuti in piano:
        if nome == "domenica" and inizio.date() == oggi.date():
            inizio = ora(oggi, 20)
        rid = _ins(b, chi=chi, tipo=tipo, stato=stato, inizio=inizio, minuti=minuti, pid=pid, cid=cid)
        righe[nome] = {"id": rid, "start": inizio, "end": inizio + timedelta(minutes=minuti), "chi": chi,
                       "agent_id": b["ids"][chi], "stato": stato, "tipo": tipo}
    return righe, oggi


def _giorni_toccati(r):
    """I giorni di Roma ('AAAA-MM-GG') che l'intervallo [start, end) tocca."""
    g = r["start"].astimezone(ROMA).replace(hour=0, minute=0, second=0, microsecond=0)
    fine = r["end"].astimezone(ROMA)
    out = []
    while g < fine:
        out.append(g.date().isoformat())
        g += timedelta(days=1)
    return out


def _attesi(righe, giorno, *, stati=STATI_PREDEFINITI, agente=None):
    return sorted(r["id"] for r in righe.values()
                  if giorno in _giorni_toccati(r) and r["stato"] in stati
                  and (agente is None or r["agent_id"] == agente))


SCRIPT = r"""
const { chromium } = require('playwright');
const fs = require('fs');
const [base, email, password, oggi, ieri, domani, pieno, pienoMese, lontano, agenteFiltro, shots] = process.argv.slice(2);
const DIM = {
  '375': { viewport: { width: 375, height: 667 }, isMobile: true, hasTouch: true },
  '390': { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true },
  '430': { viewport: { width: 430, height: 932 }, isMobile: true, hasTouch: true },
  '768': { viewport: { width: 768, height: 1024 }, isMobile: true, hasTouch: true },
  '1280': { viewport: { width: 1280, height: 800 }, isMobile: false, hasTouch: false },
  '932x430': { viewport: { width: 932, height: 430 }, isMobile: true, hasTouch: true },
};
const out = {};
const pausa = (page, ms) => page.waitForTimeout(ms);
async function carica(page) {
  await page.waitForFunction(() => { const n = document.querySelector('.agenda-notice'); return n && !/Caricamento/.test(n.textContent); }, null, { timeout: 15000 }).catch(() => {});
  await pausa(page, 250);
}
async function vai(page, hash) { await page.goto(`${base}/os/#${hash}`); await carica(page); }
async function tocca(page, dim, loc) { if (dim.hasTouch) await loc.tap(); else await loc.click(); }
async function stato(page) {
  return page.evaluate(() => {
    const vis = (n) => { if (!n) return false; const s = getComputedStyle(n); const r = n.getBoundingClientRect(); return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0; };
    const pg = document.querySelector('.agenda-page');
    const area = document.querySelector('.agenda-area');
    const ids = (sel) => [...document.querySelectorAll(sel)].filter(vis).map((n) => Number(n.dataset.appointmentId)).filter(Number.isFinite);
    return {
      hash: location.hash,
      view: pg ? pg.dataset.view : null,
      compact: pg ? pg.classList.contains('agenda-compact') : null,
      pageClass: pg ? pg.className : '',
      active: [...document.querySelectorAll('.agenda-views button.active')].map((b) => b.dataset.view),
      pressed: [...document.querySelectorAll('.agenda-views button[aria-pressed="true"]')].map((b) => b.dataset.view),
      viewButtons: [...document.querySelectorAll('.agenda-views button')].map((b) => ({ v: b.dataset.view, vis: vis(b), h: Math.round(b.getBoundingClientRect().height) })),
      navHeights: [...document.querySelectorAll('.agenda-nav button')].filter(vis).map((b) => Math.round(b.getBoundingClientRect().height)),
      period: (document.querySelector('.agenda-period') || {}).textContent || '',
      columns: [...document.querySelectorAll('.agenda-day-column')].map((c) => c.dataset.day),
      columnsVisible: [...document.querySelectorAll('.agenda-day-column')].filter((c) => { const r = c.getBoundingClientRect(); return r.left >= -1 && r.right <= window.innerWidth + 1 && r.width > 0; }).length,
      monthCells: [...document.querySelectorAll('.agenda-month-cell')].map((c) => ({ d: c.dataset.day, n: c.dataset.count === undefined ? null : Number(c.dataset.count), out: c.classList.contains('agenda-month-out'), today: c.classList.contains('agenda-today'), sel: c.classList.contains('agenda-month-selected'), dots: c.querySelectorAll('.agenda-dot').length, visits: Number(c.dataset.visits || 0) })),
      panelDay: (document.querySelector('[data-selected-day]') || { dataset: {} }).dataset.selectedDay || null,
      panelIds: ids('.agenda-month-day-panel [data-appointment-id]'),
      gridIds: ids('.agenda-day-column [data-appointment-id]'),
      listIds: ids('.agenda-list [data-appointment-id]'),
      listDays: [...document.querySelectorAll('.agenda-list-day')].map((n) => n.dataset.day),
      listEmpty: document.querySelectorAll('.agenda-list-empty').length,
      more: [...document.querySelectorAll('.agenda-day-column .agenda-more')].filter(vis).map((n) => n.textContent.trim()),
      nowLine: document.querySelectorAll('[data-now-line]').length,
      startsBefore: [...document.querySelectorAll('[data-starts-before]')].filter(vis).map((n) => n.querySelector('.agenda-card-time') ? n.querySelector('.agenda-card-time').textContent : ''),
      endsAfter: [...document.querySelectorAll('[data-ends-after]')].filter(vis).map((n) => n.querySelector('.agenda-card-time') ? n.querySelector('.agenda-card-time').textContent : ''),
      areaTop: area ? Math.round(area.getBoundingClientRect().top + window.scrollY) : null,
      innerWidth: window.innerWidth, innerHeight: window.innerHeight,
      docWidth: document.documentElement.scrollWidth,
      filtersVisible: vis(document.querySelector('.agenda-filters')),
      commandsVisible: vis(document.querySelector('.agenda-commands')),
      disponibilitaVisible: vis([...document.querySelectorAll('.agenda-commands button')].find((b) => b.textContent.trim() === 'Disponibilità')),
      gcalVisible: vis(document.querySelector('.gcal-panel-slot')),
      fabVisible: vis(document.querySelector('[data-fab]')),
      filterAgent: !!document.querySelector('.agenda-filters [data-filter="agent"]'),
      errorBox: (document.querySelector('.agenda-notice .error-box') || {}).textContent || null,
    };
  });
}
async function swipe(page, cdp, dx, dy) {
  // l'area in vista (sotto la testata fissa), poi il dito DENTRO la griglia
  await page.evaluate(() => { const a = document.querySelector('.agenda-area'); window.scrollTo(0, a.getBoundingClientRect().top + window.scrollY - 160); });
  await pausa(page, 200);
  const area = await page.$('.agenda-area');
  const r = await area.boundingBox();
  const vh = page.viewportSize().height;
  const y0 = Math.round(Math.max(r.y + 40, Math.min(r.y + 120, vh - 60)));
  const x0 = Math.round(r.x + r.width / 2 - dx / 2);
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: x0, y: y0 }] });
  for (let i = 1; i <= 8; i += 1) {
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: x0 + Math.round((dx * i) / 8), y: y0 + Math.round((dy * i) / 8) }] });
  }
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  await pausa(page, 900);
  await carica(page);
}

(async () => {
  const browser = await chromium.launch();
  for (const [nome, dim] of Object.entries(DIM)) {
    const r = { errors: [], views: {} };
    out[nome] = r;
    const ctx = await browser.newContext({ ...dim, deviceScaleFactor: shots ? 2 : 1, timezoneId: 'Europe/Rome', locale: 'it-IT' });
    const page = await ctx.newPage();
    page.on('pageerror', (e) => r.errors.push(String(e)));
    page.on('console', (m) => { if (m.type() === 'error' && !/favicon|Failed to load resource/.test(m.text())) r.errors.push(m.text()); });
    const foto = async (n) => { if (shots) await page.screenshot({ path: `${shots}/${nome}__${n}.png` }); };
    await page.goto(`${base}/os/`);
    await page.fill('#login-email', email);
    await page.fill('#login-password', password);
    await page.click('#login-form button[type=submit]');
    await pausa(page, 1500);

    // 1-4: ogni pulsante mostra la sua vista
    await vai(page, `/agenda/giorno/${oggi}`);
    for (const v of ['week', 'month', 'list', 'day']) {
      await tocca(page, dim, page.locator(`.agenda-views button[data-view="${v}"]`));
      await carica(page);
      r.views[v] = await stato(page);
      await foto(`vista_${v}`);
    }
    // 5: il giorno toccato nel Mese resta scelto passando alla Settimana
    await vai(page, `/agenda/mese/${pienoMese}`);
    r.mesePrimaDelTocco = (await stato(page)).panelDay;
    await tocca(page, dim, page.locator(`.agenda-month-cell[data-day="${pieno}"]`).first());
    await pausa(page, 300);
    r.meseScelto = await stato(page);
    await tocca(page, dim, page.locator('.agenda-views button[data-view="week"]'));
    await carica(page);
    r.dopoSceltaSettimana = (await stato(page)).hash;
    // 6-7: frecce e Oggi nel Mese e nella Settimana
    await vai(page, `/agenda/mese/${oggi}`);
    await tocca(page, dim, page.locator('.agenda-nav .agenda-next'));
    await carica(page);
    r.meseAvanti = (await stato(page)).hash;
    await tocca(page, dim, page.locator('.agenda-nav .agenda-today-btn'));
    await carica(page);
    r.meseOggi = (await stato(page)).hash;
    await vai(page, `/agenda/settimana/${oggi}`);
    await tocca(page, dim, page.locator('.agenda-nav .agenda-prev'));
    await carica(page);
    r.settimanaIndietro = await stato(page);
    // 8: swipe
    if (dim.hasTouch) {
      const cdp = await ctx.newCDPSession(page);
      await vai(page, `/agenda/mese/${oggi}`);
      await swipe(page, cdp, -240, 0);
      r.swipeMese = (await stato(page)).hash;
      await vai(page, `/agenda/settimana/${oggi}`);
      await swipe(page, cdp, -230, 10);
      r.swipeSettimana = (await stato(page)).hash;
      await vai(page, `/agenda/giorno/${oggi}`);
      const scrollPrima = await page.evaluate(() => (document.querySelector('.agenda-scroll') || {}).scrollTop || 0);
      await swipe(page, cdp, 10, -260);
      r.swipeVerticale = { hash: (await stato(page)).hash, scrollPrima, scrollDopo: await page.evaluate(() => (document.querySelector('.agenda-scroll') || {}).scrollTop || 0) };
    }
    // 11-12: Giorno di oggi (sovrapposizioni e mezzanotte), poi ieri
    await vai(page, `/agenda/giorno/${oggi}`);
    const giorno = await stato(page);
    let nascosti = [];
    const piuN = page.locator('.agenda-day-column .agenda-more').first();
    if (await piuN.count()) {
      await tocca(page, dim, piuN);
      await pausa(page, 400);
      nascosti = await page.$$eval('dialog.agenda-overflow[open] [data-appointment-id]', (n) => n.map((x) => Number(x.dataset.appointmentId)));
      await page.keyboard.press('Escape');
      await pausa(page, 300);
    }
    r.giornoOggi = { ...giorno, nascosti };
    await foto('giorno_oggi');
    await vai(page, `/agenda/giorno/${ieri}`);
    r.giornoIeri = await stato(page);
    // 9: tocco su un appuntamento nella Settimana -> pannello
    await vai(page, `/agenda/settimana/${oggi}`);
    const blocco = page.locator(`.agenda-day-column[data-day="${oggi}"] button.agenda-card`).first();
    await blocco.scrollIntoViewIfNeeded();
    await tocca(page, dim, blocco);
    await page.waitForSelector('dialog.agenda-drawer[open] h2', { timeout: 10000 }).catch(() => {});
    await pausa(page, 500);
    r.pannello = await page.evaluate(() => { const d = document.querySelector('dialog.agenda-drawer[open]'); return d ? { titolo: (d.querySelector('h2') || {}).textContent, azioni: [...d.querySelectorAll('.agenda-drawer-actions button')].map((b) => Math.round(b.getBoundingClientRect().height)) } : null; });
    await foto('pannello');
    await page.keyboard.press('Escape');
    await pausa(page, 300);
    // Mese: oggi e il riquadro del giorno; tocco su un appuntamento del riquadro
    await vai(page, `/agenda/mese/${oggi}`);
    r.meseOggiStato = await stato(page);
    const delRiquadro = page.locator('.agenda-month-day-panel button.agenda-card').first();
    if (await delRiquadro.count()) {
      await delRiquadro.scrollIntoViewIfNeeded();
      await tocca(page, dim, delRiquadro);
      await page.waitForSelector('dialog.agenda-drawer[open] h2', { timeout: 10000 }).catch(() => {});
      r.pannelloDalMese = !!(await page.$('dialog.agenda-drawer[open] h2'));
      await page.keyboard.press('Escape');
      await pausa(page, 300);
    }
    // Lista: "Mostra altri giorni"
    await vai(page, `/agenda/lista/${oggi}`);
    r.listaPrima = await stato(page);
    await tocca(page, dim, page.locator('[data-list-more]'));
    await pausa(page, 800);
    r.listaDopo = await stato(page);
    // comandi dello schermo stretto: Filtri, menu, Google, "+"
    await vai(page, `/agenda/giorno/${oggi}`);
    const compatto = (await stato(page)).compact;
    r.comandi = { compatto, prima: await stato(page) };
    if (compatto) {
      await tocca(page, dim, page.locator('.agenda-filters-toggle'));
      r.comandi.filtriAperti = (await stato(page)).filtersVisible;
      await tocca(page, dim, page.locator('.agenda-filters-toggle'));
      await tocca(page, dim, page.locator('.agenda-more-toggle'));
      r.comandi.menuAperto = await stato(page);
      await foto('menu');
      await tocca(page, dim, page.locator('.agenda-gcal-toggle'));
      r.comandi.google = (await stato(page)).gcalVisible;
      await tocca(page, dim, page.locator('[data-fab]'));
      await page.waitForSelector('dialog.agenda-dialog[open]', { timeout: 8000 }).catch(() => {});
      r.comandi.fabDialog = !!(await page.$('dialog.agenda-dialog[open]'));
      await page.keyboard.press('Escape');
      await pausa(page, 300);
    }
    // 10: filtro Agente in ogni vista, e stati coerenti
    await vai(page, `/agenda/giorno/${oggi}`);
    if (compatto) await tocca(page, dim, page.locator('.agenda-filters-toggle'));
    await page.selectOption('.agenda-filters [data-filter="agent"]', agenteFiltro);
    await carica(page);
    r.filtro = { giorno: await stato(page) };
    for (const [v, slug] of [['week', 'settimana'], ['month', 'mese'], ['list', 'lista']]) {
      await vai(page, `/agenda/${slug}/${oggi}`);
      r.filtro[v] = await stato(page);
    }
    // stati: "Tutti gli stati" mostra l'annullato in Lista E nel Mese
    await vai(page, `/agenda/mese/${domani}`);
    r.statiMesePredefiniti = await stato(page);
    if ((await stato(page)).compact) await tocca(page, dim, page.locator('.agenda-filters-toggle'));
    await page.selectOption('.agenda-filters [data-filter="agent"]', '');
    await page.selectOption('.agenda-filters [data-filter="status"]', 'all');
    await carica(page);
    r.statiMeseTutti = await stato(page);
    await vai(page, `/agenda/lista/${domani}`);
    r.statiListaTutti = await stato(page);
    if ((await stato(page)).compact) await tocca(page, dim, page.locator('.agenda-filters-toggle'));
    await page.selectOption('.agenda-filters [data-filter="status"]', '');
    await carica(page);
    r.statiListaPredefiniti = await stato(page);
    // ultima vista ricordata
    await vai(page, `/agenda/mese/${oggi}`);
    await page.goto(`${base}/os/#/oggi`);
    await pausa(page, 600);
    await vai(page, '/agenda');
    r.ritorno = (await stato(page)).view;
    await ctx.close();
  }
  await browser.close();
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


def test_agenda_mobile_quattro_viste_a_sei_dimensioni(app, server, tmp_path):
    nodo = _node_con_playwright()
    if not nodo:
        pytest.skip("Node o Playwright non disponibili: prova browser NON eseguita (BLOCKED)")
    node, env = nodo
    righe, oggi_dt = _seme(app)
    oggi = oggi_dt.date().isoformat()
    ieri = (oggi_dt - timedelta(days=1)).date().isoformat()
    domani = (oggi_dt + timedelta(days=1)).date().isoformat()
    pieno = (oggi_dt + timedelta(days=10)).date().isoformat()
    pieno_mese = (oggi_dt + timedelta(days=10)).replace(day=1).date().isoformat()
    lontano = (oggi_dt + timedelta(days=20)).date().isoformat()
    agente = app["ids"]["agent_a"]
    shots = os.environ.get("AGENDA_SCREENSHOTS_DIR", "")
    if shots:
        Path(shots).mkdir(parents=True, exist_ok=True)
    script = tmp_path / "agenda_mobile.js"
    script.write_text(SCRIPT, encoding="utf-8")
    esito = subprocess.run([node, str(script), server, "b2c.owner_a@example.test", PASSWORD, oggi, ieri, domani,
                            pieno, pieno_mese, lontano, str(agente), shots], env=env, capture_output=True, text=True, timeout=1500)
    assert esito.returncode == 0, esito.stderr[-4000:]
    out = json.loads(esito.stdout.strip().splitlines()[-1])
    if shots:
        (Path(shots) / "risultati.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")

    lun = oggi_dt - timedelta(days=oggi_dt.weekday())
    settimana = [(lun + timedelta(days=i)).date().isoformat() for i in range(7)]
    ids_oggi = _attesi(righe, oggi)
    mezzanotte = righe["mezzanotte"]["id"]
    annullato = righe["annullato"]["id"]

    for nome in VIEWPORTS:
        r = out[nome]
        compatto = nome in ("375", "390", "430", "932x430")
        assert r["errors"] == [], (nome, r["errors"])
        v = r["views"]
        # 1-4: la vista mostrata e' quella scelta, con il suo contenuto
        for vista in ("day", "week", "month", "list"):
            s = v[vista]
            assert s["view"] == vista and f"agenda-view-{vista}" in s["pageClass"], (nome, vista, s["pageClass"])
            assert s["active"] == [vista] and s["pressed"] == [vista], (nome, vista, s["active"])
            assert s["compact"] is compatto, (nome, vista, s["compact"])
            assert all(b["vis"] for b in s["viewButtons"]), (nome, vista, s["viewButtons"])
            assert s["docWidth"] <= s["innerWidth"], (nome, vista, s["docWidth"], s["innerWidth"])
            assert s["errorBox"] is None, (nome, vista, s["errorBox"])
        assert v["day"]["columns"] == [oggi]
        assert v["week"]["columns"] == settimana and v["week"]["columnsVisible"] == 7, (nome, v["week"]["columnsVisible"])
        celle = v["month"]["monthCells"]
        assert len(celle) % 7 == 0 and 28 <= len(celle) <= 42
        del_mese = [c for c in celle if not c["out"]]
        assert [c["d"] for c in del_mese][0].endswith("-01") and len({c["d"] for c in del_mese}) == len(del_mese)
        cella_oggi = next(c for c in celle if c["d"] == oggi)
        assert cella_oggi["today"] and cella_oggi["n"] == len(ids_oggi) and cella_oggi["visits"] == 1, (nome, cella_oggi)
        if compatto:
            assert cella_oggi["dots"] >= 1
        assert v["month"]["panelDay"] == oggi and sorted(v["month"]["panelIds"]) == ids_oggi, (nome, v["month"]["panelIds"])
        assert v["list"]["listDays"] and v["list"]["listDays"][0] >= oggi
        assert v["list"]["listDays"] == sorted(v["list"]["listDays"])
        # 5: il giorno scelto nel Mese passa alla Settimana
        assert r["mesePrimaDelTocco"] == pieno_mese
        assert r["meseScelto"]["panelDay"] == pieno and r["dopoSceltaSettimana"] == f"#/agenda/settimana/{pieno}"
        assert sorted(r["meseScelto"]["panelIds"]) == _attesi(righe, pieno)
        # 6-7: frecce e Oggi
        primo_mese_dopo = (oggi_dt.replace(day=1) + timedelta(days=32)).replace(day=1).date().isoformat()
        assert r["meseAvanti"] == f"#/agenda/mese/{primo_mese_dopo}", (nome, r["meseAvanti"])
        assert r["meseOggi"] == f"#/agenda/mese/{oggi}"
        assert r["settimanaIndietro"]["columns"][0] == (lun - timedelta(days=7)).date().isoformat()
        # 8: swipe (solo touch)
        if nome != "1280":
            assert r["swipeMese"] == f"#/agenda/mese/{primo_mese_dopo}", (nome, r["swipeMese"])
            assert r["swipeSettimana"] == f"#/agenda/settimana/{(oggi_dt + timedelta(days=7)).date().isoformat()}", (nome, r["swipeSettimana"])
            assert r["swipeVerticale"]["hash"] == f"#/agenda/giorno/{oggi}", (nome, r["swipeVerticale"])
        # 11: tutti gli appuntamenti di oggi raggiungibili (visibili o dietro "+N")
        g = r["giornoOggi"]
        assert sorted(set(g["gridIds"]) | set(g["nascosti"])) == ids_oggi, (nome, g["gridIds"], g["nascosti"])
        assert not set(g["gridIds"]) & set(g["nascosti"])
        if compatto:
            assert g["more"], (nome, "nessun +N con 4 sovrapposti e 3 colonne")
        # 12: mezzanotte in entrambi i giorni, con l'indicazione giusta
        assert mezzanotte in g["gridIds"] + g["nascosti"]
        assert any(t.startswith("ieri 23:30") for t in g["startsBefore"]), (nome, g["startsBefore"])
        assert mezzanotte in r["giornoIeri"]["gridIds"]
        assert any("domani 02:00" in t for t in r["giornoIeri"]["endsAfter"]), (nome, r["giornoIeri"]["endsAfter"])
        assert g["nowLine"] == 1
        # 9: il pannello si apre (dalla Settimana e dal riquadro del Mese)
        assert r["pannello"] and r["pannello"]["titolo"], (nome, r["pannello"])
        assert r["pannelloDalMese"] is True
        if nome != "1280":
            assert all(h >= 44 for h in r["pannello"]["azioni"]), (nome, r["pannello"]["azioni"])
        # Lista: "Mostra altri giorni" porta l'appuntamento lontano, in ordine
        assert righe["lontano"]["id"] not in r["listaPrima"]["listIds"]
        assert righe["lontano"]["id"] in r["listaDopo"]["listIds"] and r["listaDopo"]["listDays"][-1] == lontano
        assert r["listaPrima"]["listEmpty"] >= 1
        # 14: schermo stretto - calendario nella prima schermata, comandi a portata
        c = r["comandi"]
        assert c["compatto"] is compatto
        if compatto:
            p = c["prima"]
            assert p["areaTop"] < p["innerHeight"] * 0.75, (nome, p["areaTop"], p["innerHeight"])
            assert not p["filtersVisible"] and not p["commandsVisible"] and not p["gcalVisible"] and p["fabVisible"]
            assert all(b["h"] >= 44 for b in p["viewButtons"]) and min(p["navHeights"]) >= 44, (nome, p["viewButtons"], p["navHeights"])
            assert c["filtriAperti"] is True
            assert c["menuAperto"]["commandsVisible"] and c["menuAperto"]["disponibilitaVisible"]
            assert c["google"] is True and c["fabDialog"] is True
        else:
            assert c["prima"]["filtersVisible"] and c["prima"]["commandsVisible"] and not c["prima"]["fabVisible"]
        # 10: filtro Agente in TUTTE le viste
        f = r["filtro"]
        assert sorted(set(f["giorno"]["gridIds"])) == _attesi(righe, oggi, agente=agente), (nome, f["giorno"]["gridIds"])
        assert f["week"]["filterAgent"] and f["month"]["filterAgent"] and f["list"]["filterAgent"]
        attesi_sett = sorted({i for d in settimana for i in _attesi(righe, d, agente=agente)})
        assert sorted(set(f["week"]["gridIds"])) == attesi_sett, (nome, f["week"]["gridIds"], attesi_sett)
        cella_f = next(x for x in f["month"]["monthCells"] if x["d"] == oggi)
        assert cella_f["n"] == len(_attesi(righe, oggi, agente=agente)), (nome, cella_f)
        assert all(i in {x["id"] for x in righe.values() if x["agent_id"] == agente} for i in f["list"]["listIds"])
        assert f["list"]["listIds"], (nome, "lista vuota col filtro agente")
        # stati: stesso criterio in Mese e Lista
        cella_domani = next(x for x in r["statiMesePredefiniti"]["monthCells"] if x["d"] == domani)
        assert cella_domani["n"] == len(_attesi(righe, domani)), (nome, cella_domani)
        cella_domani_tutti = next(x for x in r["statiMeseTutti"]["monthCells"] if x["d"] == domani)
        assert cella_domani_tutti["n"] == cella_domani["n"] + 1
        assert annullato in r["statiListaTutti"]["listIds"] and annullato not in r["statiListaPredefiniti"]["listIds"]
        # ultima vista ricordata
        assert r["ritorno"] == "month", (nome, r["ritorno"])


# ---------------------------------------------------------------------------------------------------
# SETTIMANA LEGGIBILE: blocchi con ora e tipo, giorno scelto, riepilogo del giorno, nessun ricaricamento
# ---------------------------------------------------------------------------------------------------

SETTIMANA = r"""
const { chromium } = require('playwright');
const [base, email, password, oggi, altro, shots] = process.argv.slice(2);
const DIM = {
  '375': { viewport: { width: 375, height: 667 }, isMobile: true, hasTouch: true },
  '390': { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true },
  '430': { viewport: { width: 430, height: 932 }, isMobile: true, hasTouch: true },
  '768': { viewport: { width: 768, height: 1024 }, isMobile: true, hasTouch: true },
  '1280': { viewport: { width: 1280, height: 800 }, isMobile: false, hasTouch: false },
};
const pausa = (page, ms) => page.waitForTimeout(ms);
async function carica(page) {
  await page.waitForFunction(() => { const n = document.querySelector('.agenda-notice'); return n && !/Caricamento/.test(n.textContent); }, null, { timeout: 15000 }).catch(() => {});
  await pausa(page, 300);
}
async function tocca(dim, loc) { if (dim.hasTouch) await loc.tap(); else await loc.click(); }
const misura = (page) => page.evaluate(() => {
  const vis = (n) => { const s = getComputedStyle(n); const r = n.getBoundingClientRect(); return s.display !== 'none' && r.width > 0 && r.height > 0; };
  const blocchi = [...document.querySelectorAll('.agenda-week-block, .agenda-week-cluster')].filter(vis).map((b) => {
    const r = b.getBoundingClientRect();
    const righe = [...b.querySelectorAll('span')].map((s) => ({ t: s.textContent, font: parseFloat(getComputedStyle(s).fontSize), sw: s.scrollWidth, cw: s.clientWidth, top: s.getBoundingClientRect().top, bottom: s.getBoundingClientRect().bottom }));
    return { id: b.dataset.appointmentId || null, cluster: b.dataset.clusterIds || null, day: b.closest('[data-day]').dataset.day, cat: [...b.classList].find((c) => c.startsWith('agenda-type-cat-')) || null, bg: getComputedStyle(b).backgroundColor, x: r.left, y: r.top, w: r.width, h: r.height, righe, bw: b.clientWidth, bsw: b.scrollWidth };
  });
  const pannello = document.querySelector('.agenda-week-day-panel');
  const card = pannello ? [...pannello.querySelectorAll('.agenda-card')].map((c) => ({
    id: c.dataset.appointmentId ? Number(c.dataset.appointmentId) : null,
    ora: (c.querySelector('.agenda-card-time') || {}).textContent || '',
    tipo: (c.querySelector('.agenda-card-type') || {}).textContent || '',
    titolo: (c.querySelector('.agenda-card-title') || {}).textContent || '',
    luogo: (c.querySelector('.agenda-card-place') || {}).textContent || '',
    agente: (c.querySelector('.agenda-card-agent') || {}).textContent || '',
    stato: (c.querySelector('.agenda-badge') || {}).textContent || '',
  })) : [];
  return {
    hash: location.hash, compact: document.querySelector('.agenda-page').classList.contains('agenda-compact'),
    colonne: [...document.querySelectorAll('.agenda-day-column')].map((c) => c.dataset.day),
    colonneVisibili: [...document.querySelectorAll('.agenda-day-column')].filter((c) => { const r = c.getBoundingClientRect(); return r.left >= -1 && r.right <= innerWidth + 1; }).length,
    scelti: [...document.querySelectorAll('.agenda-day-head.agenda-day-selected')].map((n) => n.dataset.day),
    colonnaScelta: [...document.querySelectorAll('.agenda-day-column.agenda-day-selected')].map((n) => n.dataset.day),
    pannelloGiorno: pannello ? pannello.dataset.selectedDay : null,
    card, blocchi, docW: document.documentElement.scrollWidth, innerW: innerWidth,
    chiave: !!document.querySelector('.agenda-type-key'),
  };
});

(async () => {
  const out = {};
  const browser = await chromium.launch();
  for (const [nome, dim] of Object.entries(DIM)) {
    const r = { errors: [] };
    out[nome] = r;
    const ctx = await browser.newContext({ ...dim, deviceScaleFactor: shots ? 2 : 1, timezoneId: 'Europe/Rome', locale: 'it-IT' });
    const page = await ctx.newPage();
    page.on('pageerror', (e) => r.errors.push(String(e)));
    let richieste = 0;
    page.on('request', (q) => { if (q.url().includes('/api/appointments/calendar')) richieste += 1; });
    await page.goto(`${base}/os/`);
    await page.fill('#login-email', email);
    await page.fill('#login-password', password);
    await page.click('#login-form button[type=submit]');
    await pausa(page, 1500);
    await page.goto(`${base}/os/#/agenda/settimana/${oggi}`);
    await carica(page);
    r.dopoApertura = richieste;
    r.inizio = await misura(page);
    // la griglia alle 10 e il riepilogo sotto
    await page.evaluate(() => { const s = document.querySelector('.agenda-scroll'); s.scrollTop = 9 * 64; document.querySelector('.agenda-area').scrollIntoView(); window.scrollBy(0, -170); });
    await pausa(page, 300);
    if (shots) await page.screenshot({ path: `${shots}/${nome}__settimana_griglia.png` });
    await page.evaluate(() => { document.querySelector('.agenda-week-day-panel').scrollIntoView(); window.scrollBy(0, -200); });
    await pausa(page, 300);
    if (shots) await page.screenshot({ path: `${shots}/${nome}__settimana_riepilogo.png` });
    // cambio giorno dall'intestazione: immediato, nessuna nuova richiesta, stessa settimana
    await page.evaluate(() => window.scrollTo(0, 0));
    await tocca(dim, page.locator(`.agenda-day-head[data-day="${altro}"]`));
    await pausa(page, 200);
    r.cambio = await misura(page);
    r.dopoCambio = richieste;
    await tocca(dim, page.locator(`.agenda-day-head[data-day="${oggi}"]`));
    await pausa(page, 200);
    r.ritorno = await misura(page);
    // tocco su una scheda del riepilogo: si apre il dettaglio esistente
    const scheda = page.locator('.agenda-week-day-panel button.agenda-card').first();
    await scheda.scrollIntoViewIfNeeded();
    await tocca(dim, scheda);
    await page.waitForSelector('dialog.agenda-drawer[open] h2', { timeout: 10000 }).catch(() => {});
    r.dettaglio = await page.evaluate(() => { const d = document.querySelector('dialog.agenda-drawer[open]'); return d ? (d.querySelector('h2') || {}).textContent : null; });
    await page.keyboard.press('Escape');
    await pausa(page, 300);
    // gruppo di sovrapposti (settimana stretta): li elenca tutti
    const gruppo = page.locator('.agenda-week-cluster').first();
    if (await gruppo.count()) {
      await gruppo.scrollIntoViewIfNeeded();
      await tocca(dim, gruppo);
      await pausa(page, 400);
      r.gruppo = await page.$$eval('dialog.agenda-overflow[open] [data-appointment-id]', (n) => n.map((x) => Number(x.dataset.appointmentId)));
      await page.keyboard.press('Escape');
      await pausa(page, 300);
    }
    r.fine = richieste;
    await ctx.close();
  }
  await browser.close();
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""

ABBREVIATI = {"call": "Tel.", "video_call": "Video", "seller_meeting": "Propr.", "inspection": "Sopr.",
              "buyer_visit": "Visita", "valuation_presentation": "Valut.", "mandate_signing": "Incar.",
              "proposal": "PdA", "preliminary_contract": "Prelim", "notary": "Rogito"}
ETICHETTE_TIPO = {"call": "Telefonata", "seller_meeting": "Appuntamento proprietario", "inspection": "Sopralluogo",
                  "buyer_visit": "Visita immobile", "notary": "Rogito notarile"}
STATI = {"scheduled": "Fissato", "requested": "Richiesta"}
NOMI = {"owner_a": "Olga Alfa", "admin_a": "Adele Alfa", "agent_a": "Aldo Alfa", "agent2_a": "Ada Alfa"}


def test_settimana_leggibile_blocchi_giorno_scelto_e_riepilogo(app, server, tmp_path):
    nodo = _node_con_playwright()
    if not nodo:
        pytest.skip("Node o Playwright non disponibili: prova browser NON eseguita (BLOCKED)")
    node, env = nodo
    righe, oggi_dt = _seme(app)
    oggi = oggi_dt.date().isoformat()
    lun = oggi_dt - timedelta(days=oggi_dt.weekday())
    settimana = [(lun + timedelta(days=i)).date().isoformat() for i in range(7)]
    domenica = settimana[6]
    altro = domenica if domenica != oggi else settimana[0]
    shots = os.environ.get("AGENDA_SCREENSHOTS_DIR", "")
    if shots:
        Path(shots).mkdir(parents=True, exist_ok=True)
    script = tmp_path / "settimana.js"
    script.write_text(SETTIMANA, encoding="utf-8")
    esito = subprocess.run([node, str(script), server, "b2c.owner_a@example.test", PASSWORD, oggi, altro, shots],
                           env=env, capture_output=True, text=True, timeout=900)
    assert esito.returncode == 0, esito.stderr[-4000:]
    out = json.loads(esito.stdout.strip().splitlines()[-1])
    per_id = {r["id"]: r for r in righe.values()}
    sovrapposti = sorted(righe[k]["id"] for k in ("ov_owner", "ov_admin", "ov_agent2", "ov_richiesta"))

    for nome, r in out.items():
        assert r["errors"] == [], (nome, r["errors"])
        s = r["inizio"]
        compatto = nome in ("375", "390", "430")
        assert s["compact"] is compatto
        # 1. sette giorni lun-dom, tutti nello schermo, nessuno scorrimento orizzontale
        assert s["colonne"] == settimana and s["colonneVisibili"] == 7, (nome, s["colonneVisibili"])
        assert s["docW"] <= s["innerW"], (nome, s["docW"], s["innerW"])
        # 3. il giorno scelto (dall'indirizzo: oggi) evidenziato, con la sua colonna
        assert s["scelti"] == [oggi] and s["colonnaScelta"] == [oggi], (nome, s["scelti"])
        # 4-5. riepilogo del giorno: tutti gli appuntamenti, ciascuno con i suoi dati veri
        assert s["pannelloGiorno"] == oggi
        assert sorted(c["id"] for c in s["card"]) == _attesi(righe, oggi), (nome, s["card"])
        for c in s["card"]:
            riga = per_id[c["id"]]
            assert c["ora"] and c["tipo"] == ETICHETTE_TIPO[riga["tipo"]], (nome, c)
            assert c["titolo"] == "Visitatore Sintetico" and c["luogo"] == "Comune Sintetico", (nome, c)
            assert NOMI[riga["chi"]] in c["agente"] and c["stato"] == STATI[riga["stato"]], (nome, c)
        if compatto:
            assert s["chiave"] is True
            blocchi = s["blocchi"]
            # 2. ogni blocco: ora e tipo, almeno 11px, nessun testo tagliato o sovrapposto
            for b in blocchi:
                assert len(b["righe"]) == 2, (nome, b)
                assert all(x["font"] >= 11 for x in b["righe"]), (nome, b)
                assert all(x["sw"] <= x["cw"] + 1 for x in b["righe"]), (nome, "testo tagliato", json.dumps(b))
                assert b["righe"][0]["bottom"] <= b["righe"][1]["top"] + 0.5, (nome, "righe sovrapposte", b)
                if b["id"]:
                    tipo = per_id[int(b["id"])]["tipo"]
                    assert b["righe"][1]["t"] == ABBREVIATI[tipo], (nome, b)
                    assert b["cat"] and b["bg"] not in ("rgba(0, 0, 0, 0)", "transparent"), (nome, b)
            # nessun blocco sopra un altro nella stessa colonna
            for d in settimana:
                col = sorted((b for b in blocchi if b["day"] == d), key=lambda b: b["y"])
                for a, b in zip(col, col[1:]):
                    assert a["y"] + a["h"] <= b["y"] + 0.5, (nome, d, a, b)
            # i sovrapposti di oggi sono UN gruppo, che li elenca tutti
            gruppi = [b for b in blocchi if b["cluster"]]
            assert [sorted(map(int, g["cluster"].split(","))) for g in gruppi if g["day"] == oggi] == [sovrapposti]
            assert sorted(r["gruppo"]) == sovrapposti, (nome, r.get("gruppo"))
            visita = next(b for b in blocchi if b["id"] == str(righe["visita"]["id"]))
            assert visita["righe"][1]["t"] == "Visita" and visita["cat"] == "agenda-type-cat-visit"
        # 7. cambio giorno immediato: nessuna nuova richiesta, stessa settimana, riepilogo aggiornato
        c = r["cambio"]
        assert c["colonne"] == settimana and c["scelti"] == [altro] and c["colonnaScelta"] == [altro]
        assert c["pannelloGiorno"] == altro and sorted(x["id"] for x in c["card"]) == _attesi(righe, altro)
        assert c["hash"] == f"#/agenda/settimana/{altro}"
        assert r["dopoApertura"] == 1 and r["dopoCambio"] == 1, (nome, r["dopoApertura"], r["dopoCambio"])
        assert r["ritorno"]["scelti"] == [oggi]
        # 6. tocco su una scheda: il dettaglio esistente; 9. nessun caricamento in piu'
        assert r["dettaglio"], (nome, r["dettaglio"])
        assert r["fine"] == 1, (nome, r["fine"])
