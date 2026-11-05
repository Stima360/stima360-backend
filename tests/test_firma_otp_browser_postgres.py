"""FIRMA OTP - FIRMA SEMPLIFICATA e FOGLI VISITA: il percorso NEL BROWSER (Chromium/Playwright) sull'interfaccia vera,
con PostgreSQL vero e il trasporto email FINTO (nessun invio reale).

SENTINELLA AGGIORNATA (FIRMA SEMPLIFICATA): il vecchio percorso («Invia per firma OTP» -> modulo vuoto -> «Invia la
richiesta di firma», casella del cliente, «Verifica stato» manuale) e' sostituito dal gesto unico e dalla pagina
cliente semplificata. Le garanzie provate sono le stesse e qualcuna in piu'.

1. AGENDA (CRM desktop) + cliente iPhone 375: dati precompilati dal CRM; email mancante = errore e nessun invio; foglio
   generato al momento dell'invio; il cliente legge, «Accetto il documento e richiedo il codice», codice errato
   (tentativi), codice giusto, «Conferma firma» -> «La tua firma e' stata registrata. In attesa del completamento
   dell'agente»; il CRM si aggiorna DA SOLO; l'agente firma con dichiarazione e password; «Scarica PDF firmato»,
   menu «⋯» con le sole azioni possibili, integrita' verificata; il cliente scarica il documento firmato.
2. FOGLI VISITA (CRM su telefono 390) + cliente 390: voce di menu dopo Agenda; foglio gia' generato e riusato; password
   sbagliata = nulla inviato; «Firmo anche io adesso»; il cliente completa: «Documento firmato correttamente» con il
   download; elenco «Completato» con «Scarica PDF firmato»; avviso in «Oggi» che apre il foglio.
3. Cliente 430 che rifiuta; il CRM dice «Rifiutato» e offre «Invia di nuovo»; dal foglio si arriva all'Agenda.

In pagina cliente mai impronte SHA-256, codici di procedimento o identificativi tecnici; nessuna pagina piu' larga
dello schermo. Si salta (BLOCKED) senza Node o Playwright. Screenshot in VISITE_BROWSER_SHOTS se impostata.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tests.firma_otp_support import Posta, codice_da, configura, link_da
from tests.test_blocco_2c_integrated_postgres import CLUSTER_VERIFIED, DSN, PASSWORD, _q, app, banco  # noqa: F401
from tests.test_blocco_5a_visit_booking_postgres import _property
from tests.test_visite_browser_postgres import _node_con_playwright, server  # noqa: F401 - fixture

pytestmark = [
    pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL locale"),
    pytest.mark.skipif(not CLUSTER_VERIFIED, reason="cluster usa-e-getta non dichiarato verificato "
                       "(BLOCCO_2C_TEST_CLUSTER_VERIFIED=1)"),
]
ROMA = ZoneInfo("Europe/Rome")
C1, C2, C3 = "cliente.uno@esempio.invalid", "cliente.due@esempio.invalid", "cliente.tre@esempio.invalid"

SCRIPT = r"""
const { chromium } = require('playwright');
const fs = require('fs');
const [base, email, password, a1, g1, a2, a3, shots, c1, c2, c3, mailbox] = process.argv.slice(2);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function posta(dest, chiave, scarta) {        // link e codice letti dalle email catturate (come dalla casella)
  for (let i = 0; i < 400; i++) {
    try { const d = JSON.parse(fs.readFileSync(mailbox, 'utf8')); const v = d[dest] && d[dest][chiave]; if (v && v !== scarta) return v; } catch (e) {}
    await sleep(100);
  }
  throw new Error(`attesa scaduta per ${dest} ${chiave}`);
}
const out = { errors: [], s1: {}, s2: {}, s3: {} };
const testo = (p, sel) => p.$eval(sel, (n) => n.innerText.trim()).catch(() => null);
const foto = async (p, nome, full) => { await p.screenshot({ path: `${shots}/${nome}.png`, fullPage: !!full }); };
async function cliente(browser, dim, link) {
  const ctx = await browser.newContext({ viewport: dim, isMobile: true, hasTouch: true, deviceScaleFactor: 2, timezoneId: 'Europe/Rome', locale: 'it-IT',
    userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1' });
  const p = await ctx.newPage();
  p.on('pageerror', (e) => out.errors.push('cliente: ' + String(e)));
  p.on('dialog', (d) => d.accept());
  await p.goto(link.replace('https://test.invalid', base));
  await p.waitForSelector('[data-sign-primary]', { timeout: 15000 });
  return p;
}
const pulizia = (p) => p.evaluate(() => {
  const t = document.body.innerText;
  return { titolo: document.querySelector('.sign-title').textContent, logo: document.querySelector('.sign-logo').naturalWidth > 0,
    hash: /[0-9a-f]{64}/i.test(t), sha: /SHA/i.test(t), procedimento: /S360-/.test(t), agent: /Mozilla|AppleWebKit/.test(t),
    numeroInterno: /n\. \d/.test(t),
    larga: document.documentElement.scrollWidth > window.innerWidth, primario: (document.querySelector('[data-sign-primary]') || {}).textContent,
    dichiarazione: (document.querySelector('[data-sign-declaration] p') || {}).textContent, casella: !!document.querySelector('input[type=checkbox]') };
});
async function accedi(page) {
  await page.goto(`${base}/os/`);
  await page.fill('#login-email', email);
  await page.fill('#login-password', password);
  await page.click('#login-form button[type=submit]');
  await sleep(1500);
}

(async () => {
  const browser = await chromium.launch();
  // ---------------------------------------------------------------- 1. AGENDA (desktop) + cliente 375
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 }, timezoneId: 'Europe/Rome', locale: 'it-IT' });
  const page = await ctx.newPage();
  page.on('pageerror', (e) => out.errors.push(String(e)));
  page.on('console', (m) => { if (m.type() === 'error' && !/favicon|Failed to load resource/.test(m.text())) out.errors.push('console: ' + m.text()); });
  page.on('dialog', (d) => d.accept());
  globalThis.__pagina = page;
  await accedi(page);
  await page.goto(`${base}/os/#/agenda/giorno/${g1}`);
  await page.waitForSelector(`[data-appointment-id="${a1}"]`, { timeout: 15000 });
  await page.click(`[data-appointment-id="${a1}"]`);
  await page.waitForSelector('[data-appointment-sheet] [data-sign-state]', { timeout: 15000 });
  const s1 = out.s1;
  s1.stato = await testo(page, '[data-sign-state]');
  s1.nome = await page.$eval('[data-signature-name]', (n) => n.value);
  s1.email = await page.$eval('[data-signature-contact]', (n) => n.value);
  s1.pulsante = await testo(page, '[data-sign-send]');
  s1.firmoAnche = !!(await page.$('[data-sign-agent-now]'));
  await page.$eval('[data-sign-panel]', (n) => n.scrollIntoView());
  await foto(page, '1_agenda_da_inviare');
  await page.fill('[data-signature-contact]', '');
  await page.click('[data-sign-send]');
  s1.erroreEmail = await testo(page, '.sign-form-error');
  await page.fill('[data-signature-contact]', c1);
  await page.click('[data-sign-send]');
  await page.waitForSelector('[data-sign-state="awaiting_client"]', { timeout: 20000 });
  s1.inviato = await testo(page, '[data-sign-panel]');
  await foto(page, '2_agenda_in_attesa');

  const cp = await cliente(browser, { width: 375, height: 667 }, await posta(c1, 'link'));
  s1.pagina = await pulizia(cp);
  await foto(cp, '3_cliente_375_documento', true);
  await cp.click('[data-sign-primary]');
  await cp.waitForSelector('[data-sign-code]', { timeout: 15000 });
  s1.testoCodice = await testo(cp, '.sign-otp');
  s1.confermaDisabilitata = await cp.$eval('[data-sign-primary]', (b) => b.disabled);
  const codice = await posta(c1, 'code');
  await cp.fill('[data-sign-code]', codice === '000000' ? '000001' : '000000');
  await cp.click('[data-sign-primary]');
  await cp.waitForSelector('[data-sign-error]', { timeout: 15000 });
  s1.erroreCodice = await testo(cp, '[data-sign-error]');
  await foto(cp, '4_cliente_375_codice', true);
  await cp.fill('[data-sign-code]', codice);
  await cp.click('[data-sign-primary]');
  await cp.waitForSelector('[data-sign-final]', { timeout: 15000 });
  s1.finaleCliente = await testo(cp, '[data-sign-final]');
  s1.scaricabilePrima = !!(await cp.$('[data-sign-download-signed]'));
  await foto(cp, '5_cliente_375_registrata', true);
  // il CRM si aggiorna da solo (nessun clic)
  const t0 = Date.now();
  await page.waitForSelector('[data-sign-state="awaiting_agent"]', { timeout: 30000 });
  s1.aggiornatoInMs = Date.now() - t0;
  s1.attesaAgente = await testo(page, '[data-sign-panel]');
  await page.check('[data-agent-consent]');
  await page.fill('[data-agent-password]', password);
  await page.click('[data-agent-sign]');
  await page.waitForSelector('[data-sign-state="completed"]', { timeout: 20000 });
  s1.completato = await testo(page, '[data-sign-panel]');
  s1.scaricaPrimario = !!(await page.$('[data-sign-download-final]'));
  await page.click('[data-sign-menu]');
  s1.menu = await page.$$eval('[data-sign-menu-list] button', (b) => b.map((x) => x.textContent.trim()));
  await page.click('[data-signature-verify]');
  await page.waitForSelector('.sign-area .success-box, .sign-area .error-box', { timeout: 10000 });
  s1.integrita = await testo(page, '.sign-area .success-box');
  await foto(page, '6_agenda_completato');
  await cp.reload();
  await cp.waitForSelector('[data-sign-final="completed"]', { timeout: 15000 });
  s1.finaleCompletato = await testo(cp, '[data-sign-final]');
  s1.pdfFirmato = await cp.evaluate(async () => { const r = await fetch(document.querySelector('[data-sign-download-signed]').href); const b = new Uint8Array(await r.arrayBuffer()); return String.fromCharCode(...b.slice(0, 4)); });
  await foto(cp, '7_cliente_375_completato', true);
  await ctx.close();

  // ---------------------------------------------------------------- 2. FOGLI VISITA (CRM su telefono 390) + cliente 390
  const s2 = out.s2;
  const mctx = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2, timezoneId: 'Europe/Rome', locale: 'it-IT' });
  const mp = await mctx.newPage();
  mp.on('pageerror', (e) => out.errors.push('crm mobile: ' + String(e)));
  await accedi(mp);
  s2.menu = await mp.$$eval('#nav [data-route]', (b) => b.map((x) => x.dataset.route));
  await mp.goto(`${base}/os/#/fogli-visita`);
  await mp.waitForSelector(`[data-sheets-row="${a2}"]`, { timeout: 15000 });
  s2.rigaPrima = await mp.$eval(`[data-sheets-row="${a2}"]`, (n) => n.dataset.sheetsRowState);
  s2.rigaSenzaFoglio = await mp.$eval(`[data-sheets-row="${a3}"]`, (n) => ({ stato: n.dataset.sheetsRowState, testo: n.innerText })).catch(() => null);
  s2.larga = await mp.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  await foto(mp, '8_fogli_visita_elenco');
  await mp.tap(`[data-sheets-row="${a2}"] .sheets-row-main`);
  await mp.waitForSelector('[data-sheets-detail] [data-sign-send]', { timeout: 15000 });
  s2.email = await mp.$eval('[data-signature-contact]', (n) => n.value);
  await mp.tap('[data-sign-agent-now]');
  await mp.fill('[data-sign-agent-password]', 'password-sbagliata');
  await mp.tap('[data-sign-send]');
  await mp.waitForFunction(() => (document.querySelector('.sign-form-error') || {}).textContent, null, { timeout: 15000 });
  s2.errorePassword = await testo(mp, '.sign-form-error');
  await mp.fill('[data-sign-agent-password]', password);
  await mp.tap('[data-sign-send]');
  await mp.waitForSelector('[data-sign-state="awaiting_client"]', { timeout: 20000 });
  s2.inviato = await testo(mp, '[data-sign-panel]');
  await foto(mp, '9_fogli_visita_inviato');
  const cp2 = await cliente(browser, { width: 390, height: 844 }, await posta(c2, 'link'));
  s2.pagina = await pulizia(cp2);
  await cp2.tap('[data-sign-primary]');
  await cp2.waitForSelector('[data-sign-code]', { timeout: 15000 });
  await cp2.fill('[data-sign-code]', await posta(c2, 'code'));
  await cp2.tap('[data-sign-primary]');
  await cp2.waitForSelector('[data-sign-final]', { timeout: 15000 });
  s2.finale = await testo(cp2, '[data-sign-final]');
  s2.scaricabile = !!(await cp2.$('[data-sign-download-signed]'));
  await foto(cp2, '10_cliente_390_completato', true);
  await mp.goto(`${base}/os/#/fogli-visita`);
  await mp.waitForSelector(`[data-sheets-row="${a2}"][data-sheets-row-state="completed"]`, { timeout: 15000 });
  s2.riga = await testo(mp, `[data-sheets-row="${a2}"]`);
  s2.scaricaInElenco = !!(await mp.$(`[data-sheets-row="${a2}"] [data-sheets-download]`));
  await mp.goto(`${base}/os/#/oggi`);
  await mp.waitForSelector('[data-sign-alerts]', { timeout: 15000 });
  s2.avviso = await mp.$eval(`[data-sign-alerts] a[href="#/fogli-visita/${a2}"]`, (a) => ({ stato: a.dataset.signAlert, testo: a.innerText })).catch(() => null);
  await foto(mp, '11_oggi_avviso');
  await mp.tap(`[data-sign-alerts] a[href="#/fogli-visita/${a2}"]`);
  await mp.waitForSelector(`[data-sheets-detail="${a2}"] [data-sign-state="completed"]`, { timeout: 15000 });
  s2.apertoDallAvviso = true;
  await mctx.close();

  // ---------------------------------------------------------------- 3. cliente 430 che rifiuta
  const s3 = out.s3;
  const ctx3 = await browser.newContext({ viewport: { width: 1280, height: 900 }, timezoneId: 'Europe/Rome', locale: 'it-IT' });
  const p3 = await ctx3.newPage();
  p3.on('pageerror', (e) => out.errors.push(String(e)));
  await accedi(p3);
  await p3.goto(`${base}/os/#/fogli-visita/${a3}`);
  await p3.waitForSelector('[data-sign-send]', { timeout: 15000 });
  await p3.click('[data-sign-send]');
  await p3.waitForSelector('[data-sign-state="awaiting_client"]', { timeout: 20000 });
  const cp3 = await cliente(browser, { width: 430, height: 932 }, await posta(c3, 'link'));
  s3.pagina = await pulizia(cp3);
  await cp3.tap('[data-sign-decline]');
  await cp3.waitForSelector('[data-sign-final="declined"]', { timeout: 15000 });
  s3.finale = await testo(cp3, '[data-sign-final]');
  await foto(cp3, '12_cliente_430_rifiuto', true);
  await p3.reload();
  await p3.waitForSelector('[data-sign-state="declined"]', { timeout: 15000 });
  s3.crm = await testo(p3, '[data-sign-panel]');
  s3.invioDiNuovo = await testo(p3, '[data-sign-send]');
  await p3.click('[data-sheets-open-agenda]');
  await sleep(800);
  s3.agenda = await p3.evaluate(() => location.hash);
  await browser.close();
  console.log('RISULTATO ' + JSON.stringify(out));
})().catch(async (e) => {
  let scheda = null;
  try { scheda = await globalThis.__pagina.$eval('[data-appointment-sheet]', (n) => n.innerText); } catch (x) { scheda = String(x); }
  console.error('FALLITO', e && e.stack || e, JSON.stringify({ errors: out.errors, scheda }));
  process.exit(1);
});
"""


def _visita_passata(b, email, nome, giorni):
    """Una visita creata come la crea il CRM (API: appuntamento + registro visite dell'immobile), poi svolta nel passato."""
    ids = b["ids"]
    contatto = _q(b, "INSERT INTO contacts (agency_id, display_name, first_name, last_name, email) VALUES (%s, %s, %s, 'Prova', %s) "
                     "RETURNING id", (ids["a"], nome, nome.split()[0], email))[0][0]
    inizio = (datetime.now(timezone.utc) + timedelta(days=6 + giorni)).replace(hour=10, minute=0, second=0, microsecond=0)
    r = b["login"]("owner_a").post("/api/appointments", json={
        "appointment_type": "buyer_visit", "assigned_user_id": ids["agent_a"], "property_id": _property(b), "contact_id": contatto,
        "start_at": inizio.isoformat(), "end_at": (inizio + timedelta(minutes=45)).isoformat(), "client_request_id": str(uuid.uuid4())})
    assert r.status_code in (200, 201), r.text
    aid = r.json()["id"]
    passato = (datetime.now(timezone.utc) - timedelta(days=giorni)).replace(hour=9, minute=0, second=0, microsecond=0)
    _q(b, "SET session_replication_role = replica; UPDATE appointments SET start_at = %s, end_at = %s, status = 'completed', "
          "completed_at = %s WHERE id = %s", (passato, passato + timedelta(minutes=45), passato + timedelta(minutes=50), aid))
    _q(b, "SET session_replication_role = replica; UPDATE property_visits SET scheduled_at = %s WHERE appointment_id = %s", (passato, aid))
    return aid, passato.astimezone(ROMA).date().isoformat()


def test_firma_semplificata_e_fogli_visita_nel_browser(app, server, tmp_path, monkeypatch):
    attrezzi = _node_con_playwright()
    if attrezzi is None:
        pytest.skip("Node o Playwright non disponibili: prova browser NON eseguita (BLOCKED)")
    node, env = attrezzi
    posta = Posta()
    configura(monkeypatch, posta)
    _q(app, "UPDATE signature_requests SET status = 'cancelled', closed_at = NOW() WHERE status = 'sent'")
    a1, g1 = _visita_passata(app, C1, "Marco Uno", 2)
    a2, _ = _visita_passata(app, C2, "Laura Due", 3)
    a3, _ = _visita_passata(app, C3, "Paolo Tre", 4)
    owner = app["login"]("owner_a")
    assert owner.post(f"/api/visit-sheets/appointments/{a2}").status_code == 200          # foglio gia' pronto (riuso)
    foto = Path(os.environ.get("VISITE_BROWSER_SHOTS") or tmp_path)
    foto.mkdir(parents=True, exist_ok=True)
    mailbox = tmp_path / "mailbox.json"
    script = tmp_path / "firma_browser.js"
    script.write_text(SCRIPT, encoding="utf-8")
    fine = threading.Event()

    def postino():
        visto = {}
        while not fine.is_set():
            dati = {}
            for dest in (C1, C2, C3):
                d = {}
                links = posta.per("signature_link", dest)
                if links:
                    d["link"] = link_da(links[-1])[0]
                codici = posta.per("signature_otp", dest)
                if codici:
                    d["code"] = codice_da(codici[-1])
                if d:
                    dati[dest] = d
            if dati != visto:
                mailbox.write_text(json.dumps(dati), encoding="utf-8")
                visto = dati
            time.sleep(0.1)
    t = threading.Thread(target=postino, daemon=True)
    t.start()
    try:
        run = subprocess.run([node, str(script), server, "b2c.agent_a@example.test", PASSWORD, str(a1), g1, str(a2), str(a3),
                              str(foto), C1, C2, C3, str(mailbox)], env=env, capture_output=True, text=True, timeout=420)
    finally:
        fine.set()
    (foto / "node-firma.txt").write_text(run.stdout + "\n--- stderr ---\n" + run.stderr, encoding="utf-8")
    assert run.returncode == 0, (run.stdout[-1500:], run.stderr[:3000])
    out = json.loads(next(l for l in run.stdout.splitlines() if l.startswith("RISULTATO "))[len("RISULTATO "):])
    assert out["errors"] == [], out["errors"]
    s1, s2, s3 = out["s1"], out["s2"], out["s3"]

    # 1. Agenda + cliente 375
    assert s1["stato"] == "Da inviare" and s1["nome"] == "Marco Uno" and s1["email"] == C1
    assert s1["pulsante"] == "Invia per firma" and s1["firmoAnche"] is True
    assert "email valido" in s1["erroreEmail"]
    assert "In attesa del cliente" in s1["inviato"] and "accettata dal server di posta" in s1["inviato"]
    for pagina in (s1["pagina"], s2["pagina"], s3["pagina"]):
        assert pagina["titolo"] == "Firma il foglio visita" and pagina["logo"] is True
        assert not (pagina["hash"] or pagina["sha"] or pagina["procedimento"] or pagina["agent"] or pagina["larga"]
                    or pagina["numeroInterno"]), pagina
        assert pagina["primario"] == "Accetto il documento e richiedo il codice" and pagina["casella"] is False
        assert "firma elettronica semplice" in pagina["dichiarazione"] and "questa versione del documento" in pagina["dichiarazione"]
    assert "codice di 6 cifre" in s1["testoCodice"] and s1["confermaDisabilitata"] is True
    assert "Codice errato" in s1["erroreCodice"] and "4" in s1["erroreCodice"]
    assert "La tua firma è stata registrata" in s1["finaleCliente"] and "In attesa del completamento dell’agente" in s1["finaleCliente"]
    assert s1["scaricabilePrima"] is False
    assert s1["aggiornatoInMs"] < 30000 and "In attesa dell'agente" in s1["attesaAgente"]
    assert "Completato" in s1["completato"] and "Documento completato" in s1["completato"] and s1["scaricaPrimario"] is True
    assert s1["menu"] == ["Visualizza documento", "Scarica PDF finale", "Scarica rapporto delle prove", "Verifica integrità",
                          "Dettagli tecnici"]
    assert "Integrità verificata" in (s1["integrita"] or "")
    assert "Documento firmato correttamente" in s1["finaleCompletato"] and s1["pdfFirmato"] == "%PDF"
    # 2. Fogli visita + cliente 390
    assert s2["menu"][s2["menu"].index("agenda") + 1] == "fogli-visita"
    assert s2["rigaPrima"] == "to_send" and s2["larga"] is False and s2["email"] == C2
    assert s2["rigaSenzaFoglio"]["stato"] == "to_send" and "Foglio non ancora preparato" in s2["rigaSenzaFoglio"]["testo"]
    assert s2["errorePassword"] == "Password non valida: non è stato inviato nulla."
    assert "agente è già registrata" in s2["inviato"]
    assert "Documento firmato correttamente" in s2["finale"] and s2["scaricabile"] is True
    assert "Completato" in s2["riga"] and "Firmato da Laura Due" in s2["riga"] and s2["scaricaInElenco"] is True
    assert s2["avviso"] and s2["avviso"]["stato"] == "completed" and "Laura Due" in s2["avviso"]["testo"]
    assert s2["apertoDallAvviso"] is True
    # 3. rifiuto
    assert "Hai scelto di non firmare" in s3["finale"]
    assert "Rifiutato" in s3["crm"] and s3["invioDiNuovo"] == "Invia di nuovo"
    assert s3["agenda"].startswith("#/agenda/giorno/")

    # nessun invio in piu': un collegamento e (dove serve) un codice per cliente; la password sbagliata non ha inviato nulla
    per = {d: [m["reason_code"] for m in posta.messaggi if m["destination_snapshot"] == d] for d in (C1, C2, C3)}
    assert sorted(per[C1]) == ["signature_link", "signature_otp"]
    assert sorted(per[C2]) == ["signature_link", "signature_otp"]
    assert per[C3] == ["signature_link"]
    for aid, versioni in ((a1, 1), (a2, 1), (a3, 1)):
        assert _q(app, "SELECT count(*) FROM visit_sheets WHERE appointment_id = %s", (aid,)) == [(versioni,)]
        assert _q(app, "SELECT count(*) FROM signature_requests r JOIN visit_sheets s ON s.id = r.visit_sheet_id "
                       "WHERE s.appointment_id = %s", (aid,)) == [(1,)]
