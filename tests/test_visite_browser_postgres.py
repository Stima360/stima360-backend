"""ROADMAP VISITE - fase E: il percorso della visita NEL BROWSER, sull'interfaccia vera, con
PostgreSQL VERO (banco 2C) e l'app avviata nel processo del test. Playwright (Chromium) guida la
scheda dell'appuntamento in Agenda come l'agente della visita:

    Visita effettuata (interesse + feedback) -> Esito visibile -> Foglio visita «Da firmare» ->
    Aggiornamento al proprietario: controllo, bozza, anteprima, approvazione -> portale

Dati solo sintetici, nessun messaggio. Si salta se Node o Playwright non ci sono.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import threading
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.test_blocco_2c_integrated_postgres import CLUSTER_VERIFIED, DSN, PASSWORD, _q, app, banco  # noqa: F401
from tests.test_blocco_5a_visit_booking_postgres import _property
from tests.test_blocco_7_owner_portal_report_postgres import BASE, _owner

pytestmark = [
    pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL locale"),
    pytest.mark.skipif(not CLUSTER_VERIFIED, reason="cluster usa-e-getta non dichiarato verificato "
                       "(BLOCCO_2C_TEST_CLUSTER_VERIFIED=1)"),
]

ROOT = Path(__file__).resolve().parents[1]
SEGRETO = "NOTA-INTERNA-BROWSER"
TESTO = "La visita è stata effettuata e ha suscitato interesse per l'immobile."


def _node_con_playwright():
    node = shutil.which("node")
    if not node or not shutil.which("npm"):
        return None
    radice = subprocess.run(["npm", "root", "-g"], capture_output=True, text=True).stdout.strip()
    env = {**os.environ, "NODE_PATH": radice}
    prova = subprocess.run([node, "-e", "require('playwright')"], env=env, capture_output=True)
    return (node, env) if prova.returncode == 0 else None


@pytest.fixture
def server(app):
    """L'app VERA su una porta libera, nello stesso processo (stesso database di prova)."""
    import uvicorn

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    porta = sock.getsockname()[1]
    sock.close()
    config = uvicorn.Config(app["app"], host="127.0.0.1", port=porta, log_level="warning")
    srv = uvicorn.Server(config)
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", porta), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    yield f"http://127.0.0.1:{porta}"
    srv.should_exit = True
    thread.join(timeout=10)


SCRIPT = r"""
const { chromium } = require('playwright');
const [base, email, password, aid, giorno, shots, SEGRETO, TESTO] = process.argv.slice(2);
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
  const apri = async () => {
    const aperta = await page.$('dialog.agenda-drawer[open] .agenda-drawer-close');
    if (aperta) { await aperta.click(); await pausa(400); }
    await page.goto(`${base}/os/#/agenda/giorno/${giorno}`);
    await page.waitForSelector(`[data-appointment-id="${aid}"]`, { timeout: 15000 });
    await page.click(`[data-appointment-id="${aid}"]`);
    await page.waitForSelector('.agenda-drawer-body h2', { timeout: 15000 });
    await pausa(700);
  };
  const testo = (sel) => page.$eval(sel, (n) => n.innerText).catch(() => null);
  await apri();
  out.steps.azioni = await page.$$eval('.agenda-drawer-actions button', (b) => b.map((x) => x.textContent.trim()));
  out.steps.esitoPrima = await testo('[data-visit-outcome]');
  out.steps.fogliPrima = !!(await page.$('[data-appointment-sheet]'));
  out.steps.aggiornamentoPrima = !!(await page.$('[data-owner-update]'));
  await page.screenshot({ path: `${shots}/1_scheda_prima.png` });

  // 1. Visita effettuata, con interesse e feedback unico
  await page.click('button[data-action="complete"]');
  await page.waitForSelector('[data-field="visit-interest"]', { timeout: 8000 });
  out.steps.dialogoTitolo = await testo('dialog[open] [data-title]');
  out.steps.dialogoOpzioni = await page.$$eval('[data-field="visit-interest"] option', (o) => o.map((x) => x.textContent.trim()));
  await page.selectOption('[data-field="visit-interest"]', 'high');
  await page.fill('[data-field="outcome-note"]', `Nota interna. ${SEGRETO}`);
  await page.screenshot({ path: `${shots}/2_dialogo_esito.png` });
  await page.click('dialog[open] [data-submit]');
  await pausa(2500);
  await apri();
  out.steps.esitoDopo = await testo('[data-visit-outcome]');
  out.steps.statoDopo = await testo('.agenda-drawer-head');
  await page.screenshot({ path: `${shots}/3_esito_registrato.png` });

  // 2. Foglio visita dall'appuntamento
  await page.waitForSelector('[data-appointment-sheet] [data-visit-sheet-generate]', { timeout: 8000 });
  await page.click('[data-appointment-sheet] [data-visit-sheet-generate]');
  await page.waitForSelector('[data-appointment-sheet] .success-box', { timeout: 15000 });
  await page.waitForSelector('[data-appointment-sheet] [data-visit-sheet-signature-state]', { timeout: 15000 });   // il foglio si ridisegna dopo l'avviso
  out.steps.foglioMessaggio = await testo('[data-appointment-sheet] .success-box');
  out.steps.foglioRiga = await testo('[data-appointment-sheet] .visit-sheet-line');
  out.steps.foglioFirma = await page.$eval('[data-visit-sheet-signature-state]', (n) => n.textContent.trim()).catch(() => null);
  const [download] = await Promise.all([page.waitForEvent('download', { timeout: 15000 }), page.click('[data-visit-sheet-download]')]);
  out.steps.foglioFile = download.suggestedFilename();
  await page.screenshot({ path: `${shots}/4_foglio_visita.png` });

  // 3. Aggiornamento al proprietario: controllo, bozza, anteprima, approvazione
  await page.waitForSelector('[data-owner-update-form]', { timeout: 10000 });
  out.steps.proposta = await page.$eval('[data-field="owner-update-text"]', (n) => n.value);
  out.steps.approvaPrima = !!(await page.$('[data-owner-update-approve]'));
  await page.fill('[data-field="owner-update-text"]', 'Il signor Rossi ha chiesto di essere richiamato al 333 1234567.');
  await page.click('[data-owner-update-form] button:not([type=submit])');
  await page.waitForSelector('[data-owner-update-form] .field-error li', { timeout: 8000 });
  out.steps.problemiPrivacy = await page.$$eval('[data-owner-update-form] .field-error li', (l) => l.map((x) => x.textContent));
  await page.click('[data-owner-update-form] [data-submit]');
  await pausa(1200);
  out.steps.bozzaNonSalvataPerPrivacy = !(await page.$('[data-owner-update-preview]'));
  await page.fill('[data-field="owner-update-text"]', TESTO);
  await page.click('[data-owner-update-form] [data-submit]');
  await page.waitForSelector('[data-owner-update-preview]', { timeout: 10000 });
  out.steps.anteprima = await testo('[data-owner-update-preview]');
  out.steps.statoBozza = await testo('.owner-update-state');
  out.steps.approvaDisabilitata = await page.$eval('[data-owner-update-approve]', (b) => b.disabled);
  await page.screenshot({ path: `${shots}/5_bozza_anteprima.png` });
  await page.check('[data-owner-update-confirm]');
  out.steps.approvaAbilitata = await page.$eval('[data-owner-update-approve]', (b) => !b.disabled);
  await page.click('[data-owner-update-approve]');
  await page.waitForFunction(() => /Pubblicato al proprietario il/.test(document.querySelector('[data-owner-update]')?.textContent || ''), null, { timeout: 10000 });
  out.steps.pubblicato = await testo('[data-owner-update]');
  await page.screenshot({ path: `${shots}/6_pubblicato.png` });

  // 4. telefono: nessuno scorrimento orizzontale della scheda
  const mobile = await ctx.newPage();
  await mobile.setViewportSize({ width: 390, height: 844 });
  await mobile.goto(`${base}/os/#/agenda/giorno/${giorno}`);
  await mobile.waitForSelector(`[data-appointment-id="${aid}"]`, { timeout: 15000 });
  await mobile.click(`[data-appointment-id="${aid}"]`);
  await mobile.waitForSelector('[data-owner-update]', { timeout: 15000 });
  await mobile.waitForTimeout(1200);
  out.steps.overflowTelefono = await mobile.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  await mobile.screenshot({ path: `${shots}/7_telefono.png`, fullPage: true });
  await browser.close();
  console.log('RISULTATO ' + JSON.stringify(out));
})().catch((e) => { console.error('FALLITO', e && e.stack || e); process.exit(1); });
"""


def test_il_percorso_della_visita_nel_browser(app, server, tmp_path):
    attrezzi = _node_con_playwright()
    if attrezzi is None:
        pytest.skip("Node o Playwright non disponibili")
    node, env = attrezzi
    ids = app["ids"]
    pid = _property(app)
    portale, _account, _grant = _owner(app, pid)
    owner = app["login"]("owner_a")
    contatto = _q(app, "INSERT INTO contacts (agency_id, display_name, first_name, last_name, phone, email) VALUES "
                       "(%s, 'Giulia Browser', 'Giulia', 'Browser', '3337770011', 'giulia.browser@example.test') "
                       "RETURNING id", (ids["a"],))[0][0]
    inizio = (datetime.now(timezone.utc) + timedelta(days=5)).replace(hour=10, minute=0, second=0, microsecond=0)
    r = owner.post("/api/appointments", json={
        "appointment_type": "buyer_visit", "assigned_user_id": ids["agent_a"], "property_id": pid, "contact_id": contatto,
        "start_at": inizio.isoformat(), "end_at": (inizio + timedelta(minutes=45)).isoformat(),
        "client_request_id": str(uuid.uuid4())})
    assert r.status_code == 201, r.text
    aid = r.json()["id"]
    passato = (datetime.now(timezone.utc) - timedelta(days=2)).replace(hour=10, minute=0, second=0, microsecond=0)
    _q(app, "SET session_replication_role = replica; UPDATE appointments SET start_at = %s, end_at = %s WHERE id = %s",
       (passato, passato + timedelta(minutes=45), aid))
    _q(app, "SET session_replication_role = replica; UPDATE property_visits SET scheduled_at = %s WHERE appointment_id = %s",
       (passato, aid))
    giorno = passato.astimezone(__import__("zoneinfo").ZoneInfo("Europe/Rome")).date().isoformat()
    foto = Path(os.environ.get("VISITE_BROWSER_SHOTS") or tmp_path)
    foto.mkdir(parents=True, exist_ok=True)

    script = tmp_path / "visite_browser.js"
    script.write_text(SCRIPT, encoding="utf-8")
    run = subprocess.run([node, str(script), server, "b2c.agent_a@example.test", PASSWORD, str(aid), giorno, str(foto),
                          SEGRETO, TESTO],
                         env=env, capture_output=True, text=True, timeout=240)
    (foto / "node-output.txt").write_text(run.stdout + "\n--- stderr ---\n" + run.stderr, encoding="utf-8")
    assert run.returncode == 0, (run.stdout[-1500:], run.stderr[:2200])
    riga = next(l for l in run.stdout.splitlines() if l.startswith("RISULTATO "))
    out = json.loads(riga[len("RISULTATO "):])
    passi = out["steps"]
    assert out["errors"] == [], out["errors"]
    # la scheda: azioni con i nomi del consulente, sezioni presenti
    assert {"Visita effettuata", "Cliente assente"} <= set(passi["azioni"]) and any("non effettuata" in a for a in passi["azioni"])
    assert passi["fogliPrima"] is True and passi["aggiornamentoPrima"] is False               # prima dell'esito niente aggiornamento
    assert "Registra prima la visita" in passi["esitoPrima"]
    # esito: un solo campo di feedback, interesse tra i livelli
    assert passi["dialogoTitolo"].lower() == "visita effettuata"      # il CSS lo mostra in maiuscolo
    assert passi["dialogoOpzioni"][1:] == ["Molto interessato", "Interessato", "Indeciso", "Non interessato"]
    assert "Molto interessato" in passi["esitoDopo"] and SEGRETO in passi["esitoDopo"] and "Completato" in passi["statoDopo"]
    assert _q(app, "SELECT outcome, feedback, status FROM property_visits WHERE appointment_id = %s", (aid,)) == [
        ("Molto interessato", f"Nota interna. {SEGRETO}", "completed")]
    # foglio visita: generato dall'appuntamento, «Da firmare», scaricato
    assert "versione 1" in passi["foglioMessaggio"] and "Da firmare" in passi["foglioRiga"] and passi["foglioFirma"] == "Da firmare"
    assert passi["foglioFile"] == f"foglio-visita-{aid}-v1.pdf"
    assert _q(app, "SELECT visit_state FROM visit_sheets WHERE appointment_id = %s", (aid,)) == [("completed",)]
    # aggiornamento al proprietario: proposta dall'interesse, privacy, bozza, anteprima, approvazione esplicita
    assert "forte interesse" in passi["proposta"] and SEGRETO not in passi["proposta"] and "Browser" not in passi["proposta"]
    assert passi["approvaPrima"] is False and passi["bozzaNonSalvataPerPrivacy"] is True
    assert any("riferimenti identificativi" in p.lower() or "recapiti" in p.lower() or "telefon" in p.lower()
               for p in passi["problemiPrivacy"]), passi["problemiPrivacy"]
    assert TESTO in passi["anteprima"] and "Bozza" in passi["statoBozza"]
    assert passi["approvaDisabilitata"] is True and passi["approvaAbilitata"] is True            # serve la casella
    assert "pubblicato al proprietario il" in passi["pubblicato"].lower() and TESTO in passi["pubblicato"]
    assert "approvato e pubblicato" in passi["pubblicato"].lower()
    assert passi["overflowTelefono"] is False
    # il proprietario lo legge nel portale; nulla di interno o dell'acquirente
    visto = portale.get(f"{BASE}/{pid}").json()["visit_feedback"]
    assert [v["public_summary"] for v in visto] == [TESTO]
    grezzo = portale.get(f"{BASE}/{pid}").text + portale.get(
        f"{BASE}/{pid}/report", params={"date_from": (date.today() - timedelta(days=60)).isoformat(),
                                        "date_to": date.today().isoformat()}).text
    for riservato in (SEGRETO, "Giulia Browser", "3337770011", "giulia.browser@example.test", "Nota interna"):
        assert riservato not in grezzo, riservato
