"""ORDINAMENTO ALFABETICO - collaudo NEL BROWSER (Chromium) sull'interfaccia vera, con
PostgreSQL VERO (banco 2C, usa-e-getta, tutte le migrazioni) e l'app avviata nel processo
del test. Dati solo sintetici.

  01  Contatti: A–Z attivo all'apertura; con la ricerca l'elenco e' in ordine alfabetico
      (accenti, maiuscole e punteggiatura iniziale non contano); Z–A lo rovescia e la
      ricerca resta; uscendo e tornando Z–A resta scelto; ricaricando la pagina si riparte
      da A–Z (nessuno storage del browser: regola della Shell)
  02  Immobili: A–Z / Z–A per indirizzo con civico numerico, insieme al filtro Comune,
      al filtro Stato e alla ricerca
  03  Incarichi: A–Z / Z–A per indirizzo e poi cliente; «Altro ordine…» (Scadenza) spegne
      A–Z / Z–A e resta scelto tornando nella pagina; A–Z lo riaccende
  04  smartphone (390 px): il comando e' visibile e nessuna delle tre pagine scorre in orizzontale
"""
from __future__ import annotations

import json
import os
import subprocess
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.test_blocco_2c_integrated_postgres import CLUSTER_VERIFIED, DSN, PASSWORD, _q, app, banco  # noqa: F401
from tests.test_visite_browser_postgres import _node_con_playwright, server  # noqa: F401

pytestmark = [
    pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL locale"),
    pytest.mark.skipif(not CLUSTER_VERIFIED, reason="cluster usa-e-getta non dichiarato verificato "
                       "(BLOCCO_2C_TEST_CLUSTER_VERIFIED=1)"),
]

SHOTS = os.getenv("ORD_SHOTS_DIR")
CITTA_IMMOBILI = "Ordinopoli"
CITTA_INCARICHI = "Mandatopoli"

SCRIPT = r"""
const { chromium } = require('playwright');
const [base, password, shots] = process.argv.slice(2);
(async () => {
  const out = { errors: [], c: {}, p: {}, m: {}, mobile: {} };
  const browser = await chromium.launch();
  const pausa = (p, ms) => p.waitForTimeout(ms);
  async function entra(ctx) {
    const page = await ctx.newPage();
    page.on('pageerror', (e) => out.errors.push(String(e)));
    page.on('console', (m) => { if (m.type() === 'error' && !/favicon|Failed to load resource|status of 40[49]/.test(m.text())) out.errors.push(m.text()); });
    await page.goto(`${base}/os/`);
    await page.fill('#login-email', 'b2c.owner_a@example.test');
    await page.fill('#login-password', password);
    await page.click('#login-form button[type=submit]');
    await pausa(page, 1500);
    return page;
  }
  const pronta = async (page, area) => {
    await pausa(page, 700);
    await page.waitForFunction((a) => { const e = document.querySelector(a); return e && !/Caricamento/.test(e.textContent); }, area, { timeout: 15000 });
  };
  const vai = async (page, hash, area) => { await page.evaluate((h) => { window.location.hash = h; }, hash); await pronta(page, area); };
  const righe = (page, area) => page.$$eval(`${area} tr[data-row-id]`, (els) => els.map((e) => Number(e.dataset.rowId)));
  const premuti = (page, host) => page.$$eval(`${host} [data-sort]`, (els) => Object.fromEntries(els.map((e) => [e.dataset.sort, e.getAttribute('aria-pressed')])));
  const scrivi = async (page, sel, testo, area) => { await page.fill(sel, testo); await pausa(page, 400); await pronta(page, area); };
  const clicca = async (page, sel, area) => { await page.click(sel); await pronta(page, area); };
  const foto = async (page, name) => { if (shots) await page.screenshot({ path: `${shots}/${name}.png`, fullPage: false }); };

  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 }, deviceScaleFactor: 2 });
  const page = await entra(ctx);

  // ---------------- 01 Contatti
  const CA = '#contatti-list-area';
  await vai(page, '#/contatti', CA);
  out.c.iniziale = await premuti(page, '#contatti-sort');
  await scrivi(page, '#contatti-search', 'Brt', CA);
  out.c.az = await righe(page, CA);
  await foto(page, 'contatti-az');
  await clicca(page, '#contatti-sort [data-sort=desc]', CA);
  out.c.za = await righe(page, CA);
  out.c.ricerca_dopo = await page.$eval('#contatti-search', (e) => e.value);
  out.c.premuti_za = await premuti(page, '#contatti-sort');
  await foto(page, 'contatti-za');
  await vai(page, '#/immobili', '#immobili-list-area');
  await vai(page, '#/contatti', CA);
  out.c.dopo_navigazione = await premuti(page, '#contatti-sort');
  await scrivi(page, '#contatti-search', 'Brt', CA);
  out.c.za_dopo_navigazione = await righe(page, CA);
  await page.reload();
  await pausa(page, 1200);
  await vai(page, '#/contatti', CA);
  out.c.dopo_ricarica = await premuti(page, '#contatti-sort');

  // ---------------- 02 Immobili
  const PA = '#immobili-list-area';
  await vai(page, '#/immobili', PA);
  out.p.iniziale = await premuti(page, '#immobili-sort');
  await scrivi(page, '#immobili-city', 'Ordinopoli', PA);
  out.p.az = await righe(page, PA);
  await foto(page, 'immobili-az');
  await clicca(page, '#immobili-sort [data-sort=desc]', PA);
  out.p.za = await righe(page, PA);
  await page.selectOption('#immobili-status', 'reserved');
  await pronta(page, PA);
  out.p.za_stato = await righe(page, PA);
  await clicca(page, '#immobili-sort [data-sort=asc]', PA);
  out.p.az_stato = await righe(page, PA);
  await page.selectOption('#immobili-status', '');
  await pronta(page, PA);
  await scrivi(page, '#immobili-search', 'roma', PA);
  out.p.az_ricerca = await righe(page, PA);
  await clicca(page, '#immobili-sort [data-sort=desc]', PA);
  out.p.za_ricerca = await righe(page, PA);
  await foto(page, 'immobili-za-ricerca');

  // ---------------- 03 Incarichi
  const MA = '#inc-list-area';
  await vai(page, '#/incarichi', MA);
  out.m.iniziale = await premuti(page, '#inc-alpha');
  await scrivi(page, '#inc-city', 'Mandatopoli', MA);
  out.m.az = await righe(page, MA);
  await foto(page, 'incarichi-az');
  await clicca(page, '#inc-alpha [data-sort=desc]', MA);
  out.m.za = await righe(page, MA);
  await scrivi(page, '#inc-search', 'garibaldi', MA);
  out.m.za_ricerca = await righe(page, MA);
  await scrivi(page, '#inc-search', '', MA);
  await page.selectOption('#inc-sort', 'expiry');
  await pronta(page, MA);
  out.m.scadenza = await righe(page, MA);
  out.m.premuti_scadenza = await premuti(page, '#inc-alpha');
  await vai(page, '#/contatti', CA);
  await vai(page, '#/incarichi', MA);
  out.m.select_dopo_navigazione = await page.$eval('#inc-sort', (e) => e.value);
  out.m.premuti_dopo_navigazione = await premuti(page, '#inc-alpha');
  await scrivi(page, '#inc-city', 'Mandatopoli', MA);
  out.m.scadenza_dopo_navigazione = await righe(page, MA);
  await clicca(page, '#inc-alpha [data-sort=asc]', MA);
  out.m.select_dopo_az = await page.$eval('#inc-sort', (e) => e.value);
  out.m.az_di_nuovo = await righe(page, MA);
  await foto(page, 'incarichi-az-di-nuovo');

  // ---------------- 04 smartphone
  const mctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true });
  const mob = await entra(mctx);
  for (const [nome, hash, area, host] of [['contatti', '#/contatti', CA, '#contatti-sort'], ['immobili', '#/immobili', PA, '#immobili-sort'],
                                          ['incarichi', '#/incarichi', MA, '#inc-alpha']]) {
    await vai(mob, hash, area);
    const box = await mob.$eval(`${host} .sort-toggle`, (e) => { const r = e.getBoundingClientRect(); return { left: r.left, right: r.right, h: r.height }; });
    const larghezza = await mob.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }));
    out.mobile[nome] = { box, ...larghezza };
    await foto(mob, `mobile-${nome}`);
  }
  await browser.close();
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


def _contatto(b, agenzia, nome, cognome, display):
    return _q(b, "INSERT INTO contacts (agency_id, display_name, first_name, last_name) VALUES (%s, %s, %s, %s) "
                 "RETURNING id", (agenzia, display, nome, cognome))[0][0]


def _immobile(client, b, citta, indirizzo, civico, titolo="Immobile"):
    r = client.post("/api/property/properties", json={"title": titolo, "property_type": "apartment", "city": citta,
                                                      "address": "Via Provvisoria", "civic_number": "1"})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    _q(b, "UPDATE properties SET address = %s, civic_number = %s WHERE id = %s", (indirizzo, civico, pid))
    return pid


def _incarico(client, b, ids, indirizzo, civico, cliente, giorno, durata):
    pid = _immobile(client, b, CITTA_INCARICHI, indirizzo, civico, "Incarico")
    nome, cognome = cliente.split()
    cid = _contatto(b, ids["a"], nome, cognome, cliente)
    r = client.post(f"/api/property/properties/{pid}/contacts", json={"contact_id": cid, "role": "owner", "is_primary": True})
    assert r.status_code == 201, r.text
    inizio = (datetime.now(timezone.utc) + timedelta(days=40 + giorno)).replace(hour=8, minute=0, second=0, microsecond=0)
    r = client.post("/api/acquisitions", json={"property_id": pid, "owner_contact_id": cid, "appointment": {
        "start_at": inizio.isoformat(), "assigned_user_id": ids["owner_a"], "client_request_id": str(uuid.uuid4())}})
    assert r.status_code == 201, r.text
    acq = r.json()["id"]
    _q(b, "UPDATE acquisitions SET status = 'inspection_done' WHERE id = %s", (acq,))
    versione = _q(b, "SELECT version FROM acquisitions WHERE id = %s", (acq,))[0][0]
    r = client.post(f"/api/acquisitions/{acq}/mandate", json={
        "version": versione, "mandate_type": "Esclusiva", "mandate_start": date.today().isoformat(),
        "mandate_end": (date.today() + timedelta(days=durata)).isoformat()})
    assert r.status_code == 200, r.text
    return pid


def test_ordinamento_a_z_z_a_nelle_tre_schermate(app, server, tmp_path):
    found = _node_con_playwright()
    if found is None:
        pytest.skip("node con playwright non disponibile: collaudo nel browser NON eseguito (BLOCKED)")
    node, env = found
    ids = app["ids"]
    owner = app["login"]("owner_a")

    c = {k: _contatto(app, ids["a"], n, s, d) for k, n, s, d in (
        ("zeno", "Zeno", "Brt", "Zeno Brt"), ("alba", "Àlba", "Brt", "Àlba Brt"),
        ("bruno", "bruno", "Brt", "bruno Brt"), ("carla", "Carla", "Brt", "«Carla» Brt"))}
    _contatto(app, ids["b"], "Anna", "Brt", "Anna Brt")                     # altra agenzia: mai in elenco
    p = {"roma10": _immobile(owner, app, CITTA_IMMOBILI, "Via Roma", "10"),
         "roma2": _immobile(owner, app, CITTA_IMMOBILI, "via ROMA", "2"),
         "ercole": _immobile(owner, app, CITTA_IMMOBILI, "Viale Èrcole", "1"),
         "corso": _immobile(owner, app, CITTA_IMMOBILI, "  Corso Alfa", "5")}
    _q(app, "UPDATE properties SET commercial_status = 'reserved' WHERE id IN (%s, %s)", (p["ercole"], p["corso"]))
    m = {"gar3_bruno": _incarico(owner, app, ids, "Via Garibaldi", "3", "Bruno Neri", 1, 100),
         "gar3_anna": _incarico(owner, app, ids, "via garibaldi", "3", "Anna Zeta", 2, 50),
         "gar12": _incarico(owner, app, ids, "Via Garibaldi", "12", "Carlo Bianchi", 3, 20),
         "aurora": _incarico(owner, app, ids, "Corso Aurora", "7", "Zeno Verdi", 4, 200)}

    shots = SHOTS or str(tmp_path)
    Path(shots).mkdir(parents=True, exist_ok=True)
    script = tmp_path / "ordinamento.js"
    script.write_text(SCRIPT, encoding="utf-8")
    esito = subprocess.run([node, str(script), server, PASSWORD, shots], capture_output=True, text=True, timeout=300,
                           env=env)
    assert esito.returncode == 0, esito.stderr[-4000:]
    out = json.loads(esito.stdout.strip().splitlines()[-1])
    assert out["errors"] == [], out["errors"]

    # 01 Contatti
    co = out["c"]
    az_c = [c[k] for k in ("alba", "bruno", "carla", "zeno")]
    assert co["iniziale"] == {"asc": "true", "desc": "false"}
    assert co["az"] == az_c
    assert co["za"] == list(reversed(az_c)) and co["ricerca_dopo"] == "Brt"
    assert co["premuti_za"] == {"asc": "false", "desc": "true"}
    assert co["dopo_navigazione"] == {"asc": "false", "desc": "true"}
    assert co["za_dopo_navigazione"] == list(reversed(az_c))
    assert co["dopo_ricarica"] == {"asc": "true", "desc": "false"}

    # 02 Immobili
    po = out["p"]
    az_p = [p[k] for k in ("corso", "roma2", "roma10", "ercole")]
    assert po["iniziale"] == {"asc": "true", "desc": "false"}
    assert po["az"] == az_p and po["za"] == list(reversed(az_p))
    assert po["za_stato"] == [p["ercole"], p["corso"]] and po["az_stato"] == [p["corso"], p["ercole"]]
    assert po["az_ricerca"] == [p["roma2"], p["roma10"]] and po["za_ricerca"] == [p["roma10"], p["roma2"]]

    # 03 Incarichi
    mo = out["m"]
    az_m = [m[k] for k in ("aurora", "gar3_anna", "gar3_bruno", "gar12")]
    assert mo["iniziale"] == {"asc": "true", "desc": "false"}
    assert mo["az"] == az_m and mo["za"] == list(reversed(az_m))
    assert mo["za_ricerca"] == [m["gar12"], m["gar3_bruno"], m["gar3_anna"]]
    scadenza = [m[k] for k in ("gar12", "gar3_anna", "gar3_bruno", "aurora")]
    assert mo["scadenza"] == scadenza and mo["premuti_scadenza"] == {"asc": "false", "desc": "false"}
    assert mo["select_dopo_navigazione"] == "expiry" and mo["premuti_dopo_navigazione"] == {"asc": "false", "desc": "false"}
    assert mo["scadenza_dopo_navigazione"] == scadenza
    assert mo["select_dopo_az"] == "" and mo["az_di_nuovo"] == az_m

    # 04 smartphone
    for nome, v in out["mobile"].items():
        assert v["scroll"] <= v["client"] + 1, (nome, v)
        assert 0 <= v["box"]["left"] and v["box"]["right"] <= v["client"] + 1 and v["box"]["h"] >= 36, (nome, v)
