"""DOCUMENSO - la firma del foglio visita NEL BROWSER (Chromium/Playwright), sull'interfaccia vera, con PostgreSQL vero e
un Documenso FINTO. Prova che l'operatore: chiede la firma, vede i due firmatari, apre i collegamenti personali e il QR,
aggiorna lo stato e vede «Firma elettronica semplice» / «firma di prova, non utilizzabile». Nessun invio. Si salta se
Node o Playwright non ci sono."""
from __future__ import annotations

import json
import os
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.documenso_fake import TOKEN, WEBHOOK_SECRET, FakeDocumenso
from tests.test_blocco_2c_integrated_postgres import CLUSTER_VERIFIED, DSN, PASSWORD, _q, app, banco  # noqa: F401
from tests.test_blocco_5a_visit_booking_postgres import _property
from tests.test_visite_browser_postgres import _node_con_playwright, server  # noqa: F401 - fixture

pytestmark = [
    pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL locale"),
    pytest.mark.skipif(not CLUSTER_VERIFIED, reason="cluster usa-e-getta non dichiarato verificato "
                       "(BLOCCO_2C_TEST_CLUSTER_VERIFIED=1)"),
]

CLIENTE = "cliente.browser@esempio.invalid"
SCRIPT = r"""
const { chromium } = require('playwright');
const [base, email, password, aid, giorno, shots, cliente, fakeOrigin, signFile] = process.argv.slice(2);
const fs = require('fs');
(async () => {
  const out = { errors: [], steps: {} };
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await ctx.newPage();
  page.on('pageerror', (e) => out.errors.push(String(e)));
  page.on('console', (m) => { if (m.type() === 'error' && !/favicon|Failed to load resource/.test(m.text())) out.errors.push(m.text()); });
  const pausa = (ms) => page.waitForTimeout(ms);
  await page.goto(`${base}/os/`);
  await page.fill('#login-email', email);
  await page.fill('#login-password', password);
  await page.click('#login-form button[type=submit]');
  await pausa(1500);
  await page.goto(`${base}/os/#/agenda/giorno/${giorno}`);
  await page.waitForSelector(`[data-appointment-id="${aid}"]`, { timeout: 15000 });
  await page.click(`[data-appointment-id="${aid}"]`);
  await page.waitForSelector('[data-appointment-sheet] [data-signature-new]', { timeout: 15000 });
  const testo = (sel) => page.$eval(sel, (n) => n.innerText).catch(() => null);
  out.steps.pulsante = await testo('[data-signature-new]');
  await page.click('[data-signature-new]');
  out.steps.avviso = await testo('.visit-sign-form p');
  out.steps.tipoEmail = await page.$eval('.visit-sign-form input[type=email]', (n) => n.type);
  await page.fill('.visit-sign-form input:not([type=email])', 'Cliente Browser');
  await page.fill('.visit-sign-form input[type=email]', cliente);
  await page.screenshot({ path: `${shots}/1_richiesta.png` });
  await page.click('[data-signature-create]');
  await page.waitForSelector('[data-signature] .visit-sign-signers', { timeout: 15000 });
  out.steps.riga = await testo('[data-signature]');
  out.steps.firmatari = await page.$$eval('.visit-sign-signers li', (l) => l.map((x) => x.textContent.trim()));
  out.steps.altraRichiesta = !!(await page.$('[data-signature-new]'));            // una sola aperta per foglio
  out.steps.badgeAttesa = await testo('[data-visit-sheet-signature-state]');
  // i collegamenti: solo su richiesta
  out.steps.linkPrima = !!(await page.$('[data-signature-open]'));
  await page.click('[data-signature-links]');
  await page.waitForSelector('[data-signature-open]', { timeout: 8000 });
  out.steps.link = await page.$$eval('[data-signature-open]', (a) => a.map((x) => ({ href: x.href, target: x.target, rel: x.rel })));
  out.steps.qr = await page.$eval('[data-signature-qr]', (i) => i.src.split('/').slice(-3).join('/'));
  await page.waitForFunction(() => { const i = document.querySelector('[data-signature-qr]'); return i && i.complete && i.naturalWidth > 0; }, null, { timeout: 8000 });
  out.steps.qrCaricato = true;
  await page.screenshot({ path: `${shots}/2_collegamenti.png` });
  // fuori dal browser i due firmano; l'operatore preme «Aggiorna stato»
  fs.writeFileSync(signFile, 'firmano');
  await pausa(1500);
  await page.click('[data-signature-refresh]');
  await page.waitForFunction(() => /Firmata/.test(document.querySelector('[data-signature]')?.innerText || ''), null, { timeout: 15000 });
  out.steps.dopo = await testo('[data-signature]');
  out.steps.firmatariDopo = await page.$$eval('.visit-sign-signers li', (l) => l.map((x) => x.textContent.trim()));
  out.steps.badgeDopo = await testo('[data-visit-sheet-signature-state]');
  out.steps.collegamentiDopo = !!(await page.$('[data-signature-open]'));
  out.steps.documento = !!(await page.$('[data-signature-document]'));
  out.steps.rapporto = !!(await page.$('[data-signature-audit]'));
  await page.screenshot({ path: `${shots}/3_firmata.png` });
  const mobile = await ctx.newPage();
  await mobile.setViewportSize({ width: 390, height: 844 });
  await mobile.goto(`${base}/os/#/agenda/giorno/${giorno}`);
  await mobile.waitForSelector(`[data-appointment-id="${aid}"]`, { timeout: 15000 });
  await mobile.click(`[data-appointment-id="${aid}"]`);
  await mobile.waitForSelector('[data-signature]', { timeout: 15000 });
  await mobile.waitForTimeout(800);
  out.steps.overflowTelefono = await mobile.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  await browser.close();
  console.log('RISULTATO ' + JSON.stringify(out));
})().catch((e) => { console.error('FALLITO', e && e.stack || e); process.exit(1); });
"""


def test_la_firma_del_foglio_nel_browser(app, server, tmp_path, monkeypatch):
    attrezzi = _node_con_playwright()
    if attrezzi is None:
        pytest.skip("Node o Playwright non disponibili")
    node, env = attrezzi
    fake = FakeDocumenso()
    try:
        monkeypatch.setenv("ESIGN_PROVIDER", "documenso")
        monkeypatch.setenv("DOCUMENSO_API_URL", fake.url)
        monkeypatch.setenv("DOCUMENSO_API_TOKEN", TOKEN)
        monkeypatch.setenv("DOCUMENSO_WEBHOOK_SECRET", WEBHOOK_SECRET)
        monkeypatch.delenv("DOCUMENSO_ENVIRONMENT", raising=False)
        _q(app, "UPDATE signature_requests SET status = 'cancelled', closed_at = NOW() WHERE status = 'sent'")
        ids = app["ids"]
        pid = _property(app)
        owner = app["login"]("owner_a")
        contatto = _q(app, "INSERT INTO contacts (agency_id, display_name, first_name, last_name, phone, email) VALUES "
                           "(%s, 'Marco Browser', 'Marco', 'Browser', '3337770022', 'marco.browser@example.test') RETURNING id",
                      (ids["a"],))[0][0]
        inizio = (datetime.now(timezone.utc) + timedelta(days=6)).replace(hour=10, minute=0, second=0, microsecond=0)
        aid = owner.post("/api/appointments", json={
            "appointment_type": "buyer_visit", "assigned_user_id": ids["agent_a"], "property_id": pid, "contact_id": contatto,
            "start_at": inizio.isoformat(), "end_at": (inizio + timedelta(minutes=45)).isoformat(),
            "client_request_id": str(uuid.uuid4())}).json()["id"]
        passato = (datetime.now(timezone.utc) - timedelta(days=3)).replace(hour=10, minute=0, second=0, microsecond=0)
        _q(app, "SET session_replication_role = replica; UPDATE appointments SET start_at = %s, end_at = %s, status = 'completed', "
                "completed_at = %s WHERE id = %s", (passato, passato + timedelta(minutes=45), passato + timedelta(minutes=50), aid))
        _q(app, "SET session_replication_role = replica; UPDATE property_visits SET scheduled_at = %s WHERE appointment_id = %s", (passato, aid))
        assert owner.post(f"/api/visit-sheets/appointments/{aid}").status_code == 200
        giorno = passato.astimezone(__import__("zoneinfo").ZoneInfo("Europe/Rome")).date().isoformat()
        foto = Path(os.environ.get("VISITE_BROWSER_SHOTS") or tmp_path)
        foto.mkdir(parents=True, exist_ok=True)
        segnale = tmp_path / "firmano"

        script = tmp_path / "documenso_browser.js"
        script.write_text(SCRIPT, encoding="utf-8")
        import threading
        import time

        def firma_quando_richiesto():
            for _ in range(600):
                if segnale.exists():
                    envelope = next(iter(fake.envelopes))
                    agente = _q(app, "SELECT email FROM operator_users WHERE id = %s", (ids["agent_a"],))[0][0]
                    fake.sign(envelope, CLIENTE)
                    fake.sign(envelope, agente)
                    return
                time.sleep(0.1)
        t = threading.Thread(target=firma_quando_richiesto, daemon=True)
        t.start()
        run = subprocess.run([node, str(script), server, "b2c.agent_a@example.test", PASSWORD, str(aid), giorno, str(foto),
                              CLIENTE, fake.origin, str(segnale)], env=env, capture_output=True, text=True, timeout=240)
        t.join(timeout=5)
        (foto / "node-documenso.txt").write_text(run.stdout + "\n--- stderr ---\n" + run.stderr, encoding="utf-8")
        assert run.returncode == 0, (run.stdout[-1500:], run.stderr[:2200])
        out = json.loads(next(l for l in run.stdout.splitlines() if l.startswith("RISULTATO "))[len("RISULTATO "):])
        s = out["steps"]
        assert out["errors"] == [], out["errors"]
        assert s["pulsante"] == "Richiedi firma elettronica" and s["tipoEmail"] == "email"
        assert "SEMPLICE" in s["avviso"] and "nessun messaggio viene inviato" in s["avviso"] and "AMBIENTE DI PROVA" in s["avviso"]
        assert s["firmatari"] == ["Cliente: Cliente Browser — Da firmare", "Agente: " + s["firmatari"][1].split(": ", 1)[1]]
        assert s["firmatari"][1].endswith("— Da firmare") and s["altraRichiesta"] is False
        assert "AMBIENTE DI PROVA DEL FORNITORE" in s["riga"] and "In attesa del firmatario" in s["riga"]
        assert s["linkPrima"] is False                                              # i link si caricano solo su richiesta
        assert len(s["link"]) == 2 and all(l["href"].startswith(fake.origin + "/sign/") and l["target"] == "_blank"
                                           and "noopener" in l["rel"] for l in s["link"])
        assert s["link"][0]["href"] != s["link"][1]["href"] and s["qr"].endswith("signing-links/visitor/qr.svg") and s["qrCaricato"]
        assert "Firmata" in s["dopo"] and "firma di prova, non utilizzabile" in s["dopo"]
        assert "AMBIENTE DI PROVA DEL FORNITORE" in s["dopo"] and "avanzata" not in s["dopo"].lower()
        assert all(x.endswith("Ha firmato") for x in s["firmatariDopo"])
        assert s["badgeAttesa"] == "In attesa di firma" and s["badgeDopo"] == "Firmato in prova — non utilizzabile"
        assert s["collegamentiDopo"] is False and s["documento"] is True and s["rapporto"] is True
        assert s["overflowTelefono"] is False
        # nessun invio da parte del fornitore e nessun messaggio dal CRM
        assert all(json.loads(r["raw"])["meta"]["distributionMethod"] == "NONE" for r in fake.only("POST", "/envelope/distribute"))
    finally:
        fake.close()
