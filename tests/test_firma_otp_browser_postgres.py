"""FIRMA OTP - il percorso NEL BROWSER (Chromium/Playwright) sull'interfaccia vera, con PostgreSQL vero e il trasporto
email FINTO: l'agente invia la richiesta dalla scheda della visita; il cliente, su un iPhone simulato (390x844), apre il
collegamento ricevuto, legge il PDF, accetta la dichiarazione, chiede il codice, lo inserisce e firma; l'agente firma
dal CRM; documento e rapporto scaricabili. Nessun invio reale. Si salta se Node o Playwright non ci sono."""
from __future__ import annotations

import json
import os
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.firma_otp_support import CLIENTE, Posta, codice_da, configura, link_da
from tests.test_blocco_2c_integrated_postgres import CLUSTER_VERIFIED, DSN, PASSWORD, _q, app, banco  # noqa: F401
from tests.test_blocco_5a_visit_booking_postgres import _property
from tests.test_visite_browser_postgres import _node_con_playwright, server  # noqa: F401 - fixture

pytestmark = [
    pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL locale"),
    pytest.mark.skipif(not CLUSTER_VERIFIED, reason="cluster usa-e-getta non dichiarato verificato "
                       "(BLOCCO_2C_TEST_CLUSTER_VERIFIED=1)"),
]

SCRIPT = r"""
const { chromium } = require('playwright');
const fs = require('fs');
const [base, email, password, aid, giorno, shots, cliente, mailbox] = process.argv.slice(2);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function attendi(file, chiave) {       // il test python scrive qui link e codice letti dalle email catturate
  for (let i = 0; i < 300; i++) {
    try { const d = JSON.parse(fs.readFileSync(file, 'utf8')); if (d[chiave]) return d[chiave]; } catch (e) {}
    await sleep(100);
  }
  throw new Error('attesa scaduta per ' + chiave);
}
(async () => {
  const out = { errors: [], steps: {} };
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await ctx.newPage();
  page.on('pageerror', (e) => out.errors.push(String(e)));
  page.on('console', (m) => { if (m.type() === 'error' && !/favicon|Failed to load resource/.test(m.text())) out.errors.push(m.text()); });
  const testo = (p, sel) => p.$eval(sel, (n) => n.innerText).catch(() => null);
  // --- agente: invia la richiesta dalla scheda della visita
  await page.goto(`${base}/os/`);
  await page.fill('#login-email', email);
  await page.fill('#login-password', password);
  await page.click('#login-form button[type=submit]');
  await page.waitForTimeout(1500);
  await page.goto(`${base}/os/#/agenda/giorno/${giorno}`);
  await page.waitForSelector(`[data-appointment-id="${aid}"]`, { timeout: 15000 });
  await page.click(`[data-appointment-id="${aid}"]`);
  await page.waitForSelector('[data-appointment-sheet] [data-signature-new]', { timeout: 15000 });
  out.steps.titolo = await testo(page, '.visit-sign-title');
  out.steps.pulsante = await testo(page, '[data-signature-new]');
  await page.click('[data-signature-new]');
  out.steps.avviso = await testo(page, '.visit-sign-form p');
  await page.fill('[data-signature-name]', 'Cliente Browser');
  await page.fill('[data-signature-contact]', cliente);
  await page.screenshot({ path: `${shots}/1_richiesta.png` });
  await page.click('[data-signature-create]');
  await page.waitForSelector('[data-signature-state]', { timeout: 15000 });
  out.steps.statoInviato = await page.$eval('[data-signature-state]', (n) => n.getAttribute('data-signature-state'));
  out.steps.rigaInviata = await testo(page, '[data-signature]');
  out.steps.badge = await testo(page, '[data-visit-sheet-signature-state]');
  await page.screenshot({ path: `${shots}/2_inviata.png` });
  // --- cliente: smartphone
  const link = await attendi(mailbox, 'link');
  const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true,
    userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1' });
  const cp = await phone.newPage();
  cp.on('pageerror', (e) => out.errors.push('cliente: ' + String(e)));
  cp.on('console', (m) => { if (m.type() === 'error' && !/favicon|Failed to load resource/.test(m.text())) out.errors.push('cliente: ' + m.text()); });
  await cp.goto(link.replace('https://test.invalid', base));
  await cp.waitForSelector('[data-sign-consent]', { timeout: 15000 });
  out.steps.clienteTitolo = await testo(cp, '.sign-doc h2');
  out.steps.pdfPresente = !!(await cp.$('[data-sign-pdf]'));
  out.steps.pdfLink = await cp.$eval('[data-sign-open-pdf]', (a) => a.href);
  out.steps.primaDisabilitato = await cp.$eval('[data-sign-primary]', (b) => b.disabled);
  out.steps.consensoPreselezionato = await cp.$eval('[data-sign-consent]', (c) => c.checked);
  out.steps.overflow = await cp.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  await cp.screenshot({ path: `${shots}/3_cliente_pagina.png`, fullPage: true });
  await cp.check('[data-sign-consent]');
  out.steps.dopoConsenso = await cp.$eval('[data-sign-primary]', (b) => b.disabled);
  await cp.click('[data-sign-primary]');
  await cp.waitForSelector('[data-sign-code]', { timeout: 15000 });
  out.steps.testoOtp = await testo(cp, '.sign-otp p');
  const code = await attendi(mailbox, 'code');
  await cp.fill('[data-sign-code]', '000000' === code ? '000001' : '000000');
  await cp.check('[data-sign-consent]');
  await cp.click('[data-sign-primary]');
  await cp.waitForSelector('[data-sign-error]', { timeout: 15000 });
  out.steps.erroreCodice = await testo(cp, '[data-sign-error]');
  await cp.screenshot({ path: `${shots}/4_cliente_codice_errato.png`, fullPage: true });
  await cp.fill('[data-sign-code]', code);
  await cp.click('[data-sign-primary]');
  await cp.waitForSelector('[data-sign-final="signed"]', { timeout: 15000 });
  out.steps.clienteFinale = await testo(cp, '[data-sign-final]');
  await cp.screenshot({ path: `${shots}/5_cliente_firmato.png`, fullPage: true });
  // --- agente: vede lo stato, firma come agente
  await page.click('[data-signature-refresh]');
  await page.waitForFunction(() => document.querySelector('[data-signature-state]')?.getAttribute('data-signature-state') === 'awaiting_agent', null, { timeout: 15000 });
  out.steps.statoAttesaAgente = await testo(page, '[data-signature]');
  await page.click('[data-signature-agent]');
  await page.waitForSelector('[data-agent-sign-form]', { timeout: 8000 });
  out.steps.agenteDisabilitato = await page.$eval('[data-agent-sign]', (b) => b.disabled);
  await page.check('[data-agent-consent]');
  await page.fill('[data-agent-password]', password);
  await page.screenshot({ path: `${shots}/6_agente_firma.png` });
  await page.click('[data-agent-sign]');
  await page.waitForFunction(() => document.querySelector('[data-signature-state]')?.getAttribute('data-signature-state') === 'completed', null, { timeout: 20000 });
  out.steps.completato = await testo(page, '[data-signature]');
  out.steps.badgeFinale = await testo(page, '[data-visit-sheet-signature-state]');
  out.steps.scarichi = await page.$$eval('[data-signature-document],[data-signature-audit]', (b) => b.map((x) => x.textContent.trim()));
  await page.click('[data-signature-verify]');
  await page.waitForSelector('.visit-sign-events-holder .success-box, .visit-sign-events-holder .error-box', { timeout: 8000 });
  out.steps.verifica = await testo(page, '.visit-sign-events-holder');
  await page.click('[data-signature-events]');
  await page.waitForSelector('.visit-sign-events li', { timeout: 8000 });
  out.steps.eventi = await page.$$eval('.visit-sign-events li', (l) => l.length);
  await page.screenshot({ path: `${shots}/7_completato.png` });
  await cp.reload();
  await cp.waitForSelector('[data-sign-final="completed"]', { timeout: 15000 });
  out.steps.clienteCompletato = !!(await cp.$('[data-sign-download-signed]'));
  await cp.screenshot({ path: `${shots}/8_cliente_completato.png`, fullPage: true });
  await browser.close();
  console.log('RISULTATO ' + JSON.stringify(out));
})().catch((e) => { console.error('FALLITO', e && e.stack || e); process.exit(1); });
"""


def test_la_firma_otp_nel_browser(app, server, tmp_path, monkeypatch):
    attrezzi = _node_con_playwright()
    if attrezzi is None:
        pytest.skip("Node o Playwright non disponibili")
    node, env = attrezzi
    posta = Posta()
    configura(monkeypatch, posta)
    _q(app, "UPDATE signature_requests SET status = 'cancelled', closed_at = NOW() WHERE status = 'sent'")
    ids = app["ids"]
    pid = _property(app)
    owner = app["login"]("owner_a")
    contatto = _q(app, "INSERT INTO contacts (agency_id, display_name, first_name, last_name, phone, email) VALUES "
                       "(%s, 'Marco Browser', 'Marco', 'Browser', '3337770033', 'marco.browser@example.test') RETURNING id", (ids["a"],))[0][0]
    inizio = (datetime.now(timezone.utc) + timedelta(days=6)).replace(hour=10, minute=0, second=0, microsecond=0)
    aid = owner.post("/api/appointments", json={
        "appointment_type": "buyer_visit", "assigned_user_id": ids["agent_a"], "property_id": pid, "contact_id": contatto,
        "start_at": inizio.isoformat(), "end_at": (inizio + timedelta(minutes=45)).isoformat(), "client_request_id": str(uuid.uuid4())}).json()["id"]
    passato = (datetime.now(timezone.utc) - timedelta(days=3)).replace(hour=10, minute=0, second=0, microsecond=0)
    _q(app, "SET session_replication_role = replica; UPDATE appointments SET start_at = %s, end_at = %s, status = 'completed', "
            "completed_at = %s WHERE id = %s", (passato, passato + timedelta(minutes=45), passato + timedelta(minutes=50), aid))
    _q(app, "SET session_replication_role = replica; UPDATE property_visits SET scheduled_at = %s WHERE appointment_id = %s", (passato, aid))
    assert owner.post(f"/api/visit-sheets/appointments/{aid}").status_code == 200
    giorno = passato.astimezone(__import__("zoneinfo").ZoneInfo("Europe/Rome")).date().isoformat()
    foto = Path(os.environ.get("VISITE_BROWSER_SHOTS") or tmp_path)
    foto.mkdir(parents=True, exist_ok=True)
    mailbox = tmp_path / "mailbox.json"
    script = tmp_path / "firma_browser.js"
    script.write_text(SCRIPT, encoding="utf-8")
    import threading
    import time

    def postino():
        """Legge le email catturate e passa al browser del cliente link e codice (come farebbe il cliente dalla sua casella)."""
        visto = {}
        for _ in range(900):
            dati = dict(visto)
            links = posta.per("signature_link", CLIENTE)
            if links:
                dati["link"] = link_da(links[-1])[0]
            codici = posta.per("signature_otp", CLIENTE)
            if codici:
                dati["code"] = codice_da(codici[-1])
            if dati != visto:
                mailbox.write_text(json.dumps(dati), encoding="utf-8")
                visto = dati
            if "code" in visto and not threading.main_thread().is_alive():
                return
            time.sleep(0.1)
    t = threading.Thread(target=postino, daemon=True)
    t.start()
    run = subprocess.run([node, str(script), server, "b2c.agent_a@example.test", PASSWORD, str(aid), giorno, str(foto), CLIENTE, str(mailbox)],
                         env=env, capture_output=True, text=True, timeout=300)
    (foto / "node-firma.txt").write_text(run.stdout + "\n--- stderr ---\n" + run.stderr, encoding="utf-8")
    assert run.returncode == 0, (run.stdout[-1500:], run.stderr[:2500])
    out = json.loads(next(l for l in run.stdout.splitlines() if l.startswith("RISULTATO "))[len("RISULTATO "):])
    s = out["steps"]
    assert out["errors"] == [], out["errors"]
    assert s["titolo"] == "Firma elettronica OTP" and s["pulsante"] == "Invia per firma OTP" and "SEMPLICE" in s["avviso"]
    assert s["statoInviato"] == "sent" and "Inviato" in s["rigaInviata"] and s["badge"] == "Inviato"
    assert s["clienteTitolo"].startswith("Foglio visita n.") and s["pdfPresente"] is False and "/pdf" in s["pdfLink"]   # telefono: lettore del dispositivo
    assert s["primaDisabilitato"] is True and s["consensoPreselezionato"] is False and s["dopoConsenso"] is False
    assert s["overflow"] is False
    assert "codice di 6 cifre" in s["testoOtp"] and "c***@esempio.invalid" in s["testoOtp"]
    assert "Codice errato" in s["erroreCodice"] and "4" in s["erroreCodice"]
    assert "Firma registrata" in s["clienteFinale"]
    assert "In attesa della firma agente" in s["statoAttesaAgente"] and s["agenteDisabilitato"] is True
    assert "Completato" in s["completato"] and "Firma elettronica semplice" in s["completato"]
    assert s["badgeFinale"] == "Sottoscritto (firma elettronica semplice)"
    assert s["scarichi"] == ["Scarica documento sottoscritto", "Scarica rapporto delle prove"]
    assert "OK" in s["verifica"] and "NON VALIDO" not in s["verifica"] and s["eventi"] >= 12
    assert s["clienteCompletato"] is True
    assert {m["destination_snapshot"] for m in posta.messaggi} == {CLIENTE} and len(posta.messaggi) == 2
