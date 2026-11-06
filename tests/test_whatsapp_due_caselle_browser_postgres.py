"""WHATSAPP DUE CASELLE + IMPORTA RUBRICA - collaudo NEL BROWSER (Chromium), sull'interfaccia vera,
con PostgreSQL VERO (banco 2C/4A, usa-e-getta, migrazioni fino alla 131) e l'app avviata nel
processo del test. Dati solo sintetici; il trasporto verso Meta e' finto (nessuna chiamata).

  01  titolare: Impostazioni → Caselle WhatsApp, la casella 2 diventa «Numero personale»,
      riservata e senza automazioni; in WhatsApp lo stesso cliente ha due chat con il chip della
      casella e il filtro per casella; la chat della casella 2 dice «ricevuto su …», segnala l'altra
      casella e la risposta parte dalla casella 2 («Partirà da …», trasporto finto con quel numero)
  02  amministratore: la chat della casella 2 non compare e l'indirizzo diretto non la apre;
      autorizzato dal titolare, la vede
  03  titolare: Contatti → Importa rubrica con un .vcf finto: anteprima, «Importa tutti», esito;
      secondo import identico senza doppioni; la chat WhatsApp gia' ricevuta si collega al contatto
  04  smartphone (390 px): WhatsApp e la finestra d'importazione senza scorrimento orizzontale
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.test_blocco_2c_integrated_postgres import CLUSTER_VERIFIED, DSN, PASSWORD, _q, app, banco  # noqa: F401
from tests.test_blocco_4a_whatsapp_inbound_postgres import NUMBER_A, NUMBER_B, _change, _contact, _message, _payload, wa  # noqa: F401
from tests.test_visite_browser_postgres import _node_con_playwright, server  # noqa: F401

pytestmark = [
    pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL locale"),
    pytest.mark.skipif(not CLUSTER_VERIFIED, reason="cluster usa-e-getta non dichiarato verificato "
                       "(BLOCCO_2C_TEST_CLUSTER_VERIFIED=1)"),
]

NUMBER_A2 = "900000000000003"
SHOTS = os.getenv("WA2_SHOTS_DIR")


class FakeTransport:
    def __init__(self):
        self.calls, self.lock = [], threading.Lock()

    def send(self, phone_number_id, to, text):
        from message_replies.transport import SendResult

        with self.lock:
            self.calls.append((phone_number_id, to, text))
        return SendResult("accepted", provider_message_id=f"wamid.out.{uuid.uuid4().hex}")


SCRIPT = r"""
const { chromium, devices } = require('playwright');
const [base, password, sender, sender2, nA, nA2, vcf, shots, adminId] = process.argv.slice(2);
(async () => {
  const out = { errors: [], owner: {}, admin: {}, imp: {}, mobile: {} };
  const browser = await chromium.launch();
  const pausa = (p, ms) => p.waitForTimeout(ms);
  async function entra(ctx, email) {
    const page = await ctx.newPage();
    page.on('pageerror', (e) => out.errors.push(String(e)));
    page.on('console', (m) => { if (m.type() === 'error' && !/favicon|Failed to load resource|status of 40[49]/.test(m.text())) out.errors.push(m.text()); });
    await page.goto(`${base}/os/`);
    await page.fill('#login-email', email);
    await page.fill('#login-password', password);
    await page.click('#login-form button[type=submit]');
    await pausa(page, 1500);
    return page;
  }
  const vai = async (page, hash) => { await page.evaluate((h) => { window.location.hash = h; }, hash); await pausa(page, 1500); };
  const foto = async (page, name) => { if (shots) await page.screenshot({ path: `${shots}/${name}.png`, fullPage: false }); };
  const chat = (page) => page.evaluate((s) => [...document.querySelectorAll('[data-messages-conversation]')]
    .filter((b) => b.dataset.sender === s).map((b) => ({ inbox: b.dataset.inbox, chip: (b.querySelector('[data-messages-item-inbox]') || {}).textContent || null })), sender);

  // ---------------- 01 titolare: casella 2 e due chat
  const ownerCtx = await browser.newContext({ viewport: { width: 1280, height: 900 }, deviceScaleFactor: 2 });
  const owner = await entra(ownerCtx, 'b2c.owner_a@example.test');
  await vai(owner, '#/impostazioni/whatsapp');
  const card2 = `[data-wa-inbox="${nA2}"]`;
  await owner.waitForSelector(card2, { timeout: 15000 });
  out.owner.cards = await owner.$$eval('[data-wa-inbox]', (els) => els.map((e) => e.dataset.waInbox));
  await owner.fill(`${card2} input[name=label]`, 'Numero personale');
  await owner.selectOption(`${card2} select[name=kind]`, 'coexistence');
  await owner.selectOption(`${card2} select[name=access_mode]`, 'restricted');
  await owner.uncheck(`${card2} input[name=automations_enabled]`);
  await owner.click(`${card2} [data-wa-inbox-save]`);
  await pausa(owner, 1500);
  out.owner.saved = await owner.$eval('[data-wa-inboxes-status]', (e) => e.textContent);
  out.owner.summary2 = await owner.$eval(`${card2} .wa-inbox-head`, (e) => e.textContent);
  out.owner.title1 = await owner.$eval(`[data-wa-inbox="${nA}"] .wa-inbox-head strong`, (e) => e.textContent);
  await foto(owner, '01-caselle-whatsapp');

  await vai(owner, '#/messaggi');
  await owner.waitForSelector('[data-messages-conversation]', { timeout: 15000 });
  out.owner.chats = await chat(owner);
  out.owner.inboxChips = await owner.$$eval('[data-messages-inboxes] button', (bs) => bs.map((b) => b.textContent));
  await foto(owner, '02-whatsapp-due-chat');
  // filtro per casella
  await owner.click(`[data-messages-inboxes] [data-inbox="${nA2}"]`);
  await pausa(owner, 1200);
  out.owner.filtered = await chat(owner);
  await owner.click('[data-messages-inbox-all]');
  await pausa(owner, 1200);
  // apri la chat della casella 2
  await owner.click(`[data-messages-conversation][data-sender="${sender}"][data-inbox="${nA2}"]`);
  await owner.waitForSelector('[data-messages-head-inbox]', { timeout: 15000 });
  await pausa(owner, 800);
  out.owner.head = await owner.$eval('[data-messages-head-inbox]', (e) => [e.dataset.messagesHeadInbox, e.textContent]);
  out.owner.other = await owner.$$eval('[data-messages-other-inbox]', (els) => els.map((e) => [e.dataset.messagesOtherInbox, e.textContent]));
  out.owner.bubbles = await owner.$$eval('.wa-bubble-in .wa-bubble-text', (els) => els.map((e) => e.textContent));
  await owner.fill('textarea[name=wa_reply_text]', 'Risposta dalla casella personale');
  await owner.click('[data-wa-reply-prepare]');
  await pausa(owner, 400);
  out.owner.confirmText = await owner.$eval('.wa-reply-confirm p', (e) => e.textContent);
  await foto(owner, '03-conferma-da-casella-2');
  await owner.click('[data-wa-reply-confirm]');
  await pausa(owner, 2000);
  out.owner.outBubbles = await owner.$$eval('.wa-bubble-out .wa-bubble-text', (els) => els.map((e) => e.textContent));
  // la chat della casella 1 non mostra quella risposta
  await owner.click(`[data-messages-open-other]`);
  await pausa(owner, 1800);
  out.owner.head1 = await owner.$eval('[data-messages-head-inbox]', (e) => e.dataset.messagesHeadInbox);
  out.owner.outBubbles1 = await owner.$$eval('.wa-bubble-out .wa-bubble-text', (els) => els.map((e) => e.textContent));
  await foto(owner, '04-chat-casella-1');

  // ---------------- 02 amministratore
  const adminCtx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const admin = await entra(adminCtx, 'b2c.admin_a@example.test');
  await vai(admin, '#/messaggi');
  await admin.waitForSelector('[data-messages-conversation]', { timeout: 15000 });
  out.admin.chats = await chat(admin);
  out.admin.inboxChips = await admin.$$eval('[data-messages-inboxes] button', (bs) => bs.map((b) => b.textContent));
  await vai(admin, `#/messaggi/${sender}/${nA2}`);
  out.admin.direct = await admin.$eval('[data-messages-detail]', (e) => e.textContent);
  await vai(admin, '#/impostazioni/whatsapp');
  await admin.waitForSelector('[data-wa-inboxes-status]', { timeout: 15000 });
  out.admin.settings = { cards: await admin.$$eval('[data-wa-inbox]', (els) => els.map((e) => e.dataset.waInbox)),
                         save: await admin.$$eval('[data-wa-inbox-save]', (els) => els.length),
                         status: await admin.$eval('[data-wa-inboxes-status]', (e) => e.textContent) };
  // il titolare autorizza l'amministratore
  await vai(owner, '#/impostazioni/whatsapp');
  await owner.waitForSelector(`${card2} [data-wa-inbox-member="${adminId}"]`, { timeout: 15000 });
  await owner.check(`${card2} [data-wa-inbox-member="${adminId}"]`);
  await owner.click(`${card2} [data-wa-inbox-save]`);
  await pausa(owner, 1500);
  await vai(admin, '#/oggi');
  await vai(admin, '#/messaggi');
  await admin.waitForSelector('[data-messages-conversation]', { timeout: 15000 });
  out.admin.chatsAfterGrant = await chat(admin);

  // ---------------- 03 importazione rubrica
  async function importa() {
    await vai(owner, '#/contatti');
    await owner.click('[data-contatti-import]');
    await owner.waitForSelector('[data-contact-import-file]', { timeout: 10000 });
    await owner.setInputFiles('[data-contact-import-file]', vcf);
    await owner.click('[data-contact-import-analyze]');
    await owner.waitForSelector('[data-contact-import-summary]', { timeout: 15000 });
    const summary = await owner.$eval('[data-contact-import-summary]', (e) => e.textContent);
    const rows = await owner.$$eval('[data-contact-import-entry]', (els) => els.map((e) => [e.dataset.contactImportEntry, e.innerText]));
    await foto(owner, 'xx-anteprima');
    await owner.click('[data-contact-import-commit]');
    await owner.waitForSelector('[data-contact-import-result]', { timeout: 15000 });
    const result = await owner.$eval('[data-contact-import-result]', (e) => e.textContent);
    return { summary, rows, result };
  }
  out.imp.first = await importa();
  await foto(owner, '05-import-esito');
  await owner.click('[data-contact-import-close]');
  out.imp.second = await importa();
  await owner.click('[data-contact-import-close]');
  await vai(owner, '#/messaggi');
  await owner.waitForSelector(`[data-messages-conversation][data-sender="${sender2}"]`, { timeout: 15000 });
  out.imp.chatName = await owner.$eval(`[data-messages-conversation][data-sender="${sender2}"] .wa-item-name`, (e) => e.textContent);

  // ---------------- 04 smartphone
  const mob = await browser.newContext({ ...devices['iPhone 13'] });
  const phone = await entra(mob, 'b2c.owner_a@example.test');
  const larghezza = () => phone.evaluate(() => ({ scroll: document.documentElement.scrollWidth, view: window.innerWidth }));
  await vai(phone, '#/messaggi');
  await phone.waitForSelector('[data-messages-conversation]', { timeout: 15000 });
  out.mobile.list = await larghezza();
  await phone.click(`[data-messages-conversation][data-sender="${sender}"][data-inbox="${nA2}"]`);
  await pausa(phone, 1800);
  out.mobile.chat = await larghezza();
  // il campo di scrittura resta sopra la barra di navigazione in basso
  out.mobile.composer = await phone.evaluate(() => {
    const c = document.querySelector('textarea[name=wa_reply_text]');
    const nav = document.querySelector('.mobile-nav');
    return { composerBottom: c ? Math.round(c.getBoundingClientRect().bottom) : null,
             navTop: nav ? Math.round(nav.getBoundingClientRect().top) : null };
  });
  await phone.screenshot({ path: shots ? `${shots}/06-iphone-chat-casella-2.png` : '/dev/null' });
  await vai(phone, '#/contatti');
  await phone.click('[data-contatti-import]');
  await pausa(phone, 600);
  out.mobile.importDialog = await larghezza();
  await vai(phone, '#/impostazioni/whatsapp');
  await phone.waitForSelector('[data-wa-inbox]', { timeout: 15000 });
  out.mobile.settings = await larghezza();

  await browser.close();
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


def test_due_caselle_permessi_e_importazione_nel_browser(wa, server, tmp_path, monkeypatch):
    found = _node_con_playwright()
    if found is None:
        pytest.skip("node con playwright non disponibile: collaudo nel browser NON eseguito (BLOCKED)")
    node, env = found
    from message_replies import transport

    ids = wa["ids"]
    monkeypatch.setenv("WHATSAPP_INBOUND_ROUTES",
                       f"{NUMBER_A}:{ids['a']}, {NUMBER_A2}:{ids['a']}, {NUMBER_B}:{ids['b']}")
    fake = FakeTransport()
    monkeypatch.setenv("WHATSAPP_OUTBOUND_MODE", "meta")
    monkeypatch.setenv("WHATSAPP_TOKEN", "token-sintetico-non-reale")
    monkeypatch.setenv("WHATSAPP_GRAPH_VERSION", "v99.0")
    monkeypatch.setenv("WHATSAPP_OUTBOUND_ALLOWED_RECIPIENTS", "*")
    monkeypatch.setattr(transport, "current", lambda: fake)
    _q(wa, "DELETE FROM whatsapp_inbox_access")
    _q(wa, "DELETE FROM whatsapp_inboxes")

    sender, sender2, sender3 = "393401110001", "393401110002", "393401110003"
    contact = _contact(wa, ids["a"], "Cliente Doppio", sender)
    ts = str(int(datetime.now(timezone.utc).timestamp()))
    for number, text in ((NUMBER_A, "Buongiorno, scrivo al numero dell'agenzia"),
                         (NUMBER_A2, "Ciao, ti scrivo sul tuo numero personale")):
        assert wa["post"](_payload(_change(number, [_message(sender, body=text, ts=ts)]))).status_code == 200
    # una chat senza contatto, che l'importazione dovra' collegare
    assert wa["post"](_payload(_change(NUMBER_A, [_message(sender2, body="Sono Lucia, vorrei una stima", ts=ts)]))).status_code == 200

    vcf = tmp_path / "rubrica-finta.vcf"
    vcf.write_text("\r\n".join([
        "BEGIN:VCARD", "VERSION:3.0", "N:Verdi;Lucia;;;", "FN:Lucia Verdi",
        f"TEL;type=CELL:+39 {sender2[2:5]} {sender2[5:]}", f"TEL;type=HOME:+{sender3}", "EMAIL:lucia@example.test", "END:VCARD",
        "BEGIN:VCARD", "VERSION:3.0", "FN:Cliente Doppio (rubrica)", f"TEL;type=CELL:+{sender}", "END:VCARD",
        "BEGIN:VCARD", "VERSION:3.0", "FN:Senza Numero", "END:VCARD", ""]), encoding="utf-8")
    shots = SHOTS or str(tmp_path)
    Path(shots).mkdir(parents=True, exist_ok=True)
    script = tmp_path / "collaudo.js"
    script.write_text(SCRIPT, encoding="utf-8")
    esito = subprocess.run([node, str(script), server, PASSWORD, sender, sender2, NUMBER_A, NUMBER_A2, str(vcf), shots,
                            str(ids["admin_a"])], capture_output=True, text=True, timeout=300, env=env)
    assert esito.returncode == 0, esito.stderr[-4000:]
    out = json.loads(esito.stdout.strip().splitlines()[-1])
    assert out["errors"] == [], out["errors"]

    o = out["owner"]
    assert set(o["cards"]) == {NUMBER_A, NUMBER_A2}
    assert o["title1"] == "+39000000000"                               # etichetta = numero: una volta sola
    assert "Casella salvata" in o["saved"] and "senza automazioni" in o["summary2"] and "accesso riservato" in o["summary2"]
    assert sorted(c["inbox"] for c in o["chats"]) == sorted([NUMBER_A, NUMBER_A2])
    assert {c["inbox"]: c["chip"] for c in o["chats"]}[NUMBER_A2] == "Numero personale"
    assert o["inboxChips"][0] == "Tutte le caselle" and "Numero personale" in o["inboxChips"]
    assert [c["inbox"] for c in o["filtered"]] == [NUMBER_A2]
    assert o["head"][0] == NUMBER_A2 and "ricevuto su Numero personale" in o["head"][1]
    assert [x[0] for x in o["other"]] == [NUMBER_A] and "ha scritto anche a" in o["other"][0][1]
    assert o["bubbles"] == ["Ciao, ti scrivo sul tuo numero personale"]
    assert "Partirà da Numero personale" in o["confirmText"]
    assert o["outBubbles"] == ["Risposta dalla casella personale"]
    assert o["head1"] == NUMBER_A and o["outBubbles1"] == []
    assert fake.calls == [(NUMBER_A2, sender, "Risposta dalla casella personale")]

    a = out["admin"]
    assert [c["inbox"] for c in a["chats"]] == [NUMBER_A]
    assert a["inboxChips"] == []                                     # una sola casella visibile: niente filtro
    assert "Ciao, ti scrivo sul tuo numero personale" not in a["direct"]
    assert a["settings"]["cards"] == [NUMBER_A] and a["settings"]["save"] == 0
    assert "Solo il titolare" in a["settings"]["status"]
    assert sorted(c["inbox"] for c in a["chatsAfterGrant"]) == sorted([NUMBER_A, NUMBER_A2])

    i = out["imp"]
    assert "3 schede nel file" in i["first"]["summary"] and "1 nuove" in i["first"]["summary"]
    assert "1 già in rubrica" in i["first"]["summary"] and "1 senza numero" in i["first"]["summary"]
    assert "1 contatti creati" in i["first"]["result"] and "1 chat WhatsApp collegate" in i["first"]["result"]
    assert "0 nuove" in i["second"]["summary"] and "2 già in rubrica" in i["second"]["summary"]
    assert "0 contatti creati" in i["second"]["result"]
    assert i["chatName"] == "Lucia Verdi"
    rows = _q(wa, "SELECT id, private_user_id, email FROM contacts WHERE phone_normalized = %s", (sender2,))
    assert len(rows) == 1 and rows[0][1] == ids["owner_a"] and rows[0][2] == "lucia@example.test"
    assert _q(wa, "SELECT count(*) FROM contact_phones WHERE contact_id = %s", (rows[0][0],))[0][0] == 2
    assert _q(wa, "SELECT count(*) FROM contacts WHERE phone_normalized = %s", (sender,))[0][0] == 1
    assert contact

    composer = out["mobile"].pop("composer")
    assert composer["composerBottom"] is not None and composer["composerBottom"] <= composer["navTop"], composer
    for k, v in out["mobile"].items():
        assert v["scroll"] <= v["view"], (k, v)


GRANDE = r"""
const { chromium } = require('playwright');
const [base, password, vcf, heavy, shots] = process.argv.slice(2);
(async () => {
  const out = { errors: [], progress: [] };
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 }, deviceScaleFactor: 2 });
  const page = await ctx.newPage();
  page.on('pageerror', (e) => out.errors.push(String(e)));
  page.on('console', (m) => { if (m.type() === 'error' && !/favicon|Failed to load resource/.test(m.text())) out.errors.push(m.text()); });
  await page.goto(`${base}/os/`);
  await page.fill('#login-email', 'b2c.owner_b@example.test');
  await page.fill('#login-password', password);
  await page.click('#login-form button[type=submit]');
  await page.waitForTimeout(1500);
  await page.evaluate(() => { window.location.hash = '#/contatti'; });
  await page.waitForTimeout(1500);
  // file oltre il limite: fermato prima dell'invio, con il messaggio chiaro
  await page.click('[data-contatti-import]');
  await page.waitForTimeout(800);
  await page.setInputFiles('[data-contact-import-file]', heavy);
  await page.click('[data-contact-import-analyze]');
  await page.waitForSelector('[data-contact-import-too-large]', { timeout: 10000 });
  out.tooLarge = await page.$eval('[data-contact-import-too-large]', (e) => e.textContent);
  await page.click('[data-contact-import-close]');
  // rubrica grande: anteprima, «Importa tutti» a blocchi con l'avanzamento
  await page.click('[data-contatti-import]');
  await page.waitForTimeout(800);
  await page.setInputFiles('[data-contact-import-file]', vcf);
  await page.click('[data-contact-import-analyze]');
  await page.waitForSelector('[data-contact-import-summary]', { timeout: 60000 });
  out.summary = await page.$eval('[data-contact-import-summary]', (e) => e.textContent);
  out.sample = await page.$eval('[data-contact-import-sample]', (e) => e.textContent);
  out.rows = await page.$$eval('[data-contact-import-entry]', (els) => els.length);
  await page.click('[data-contact-import-commit]');
  const t0 = Date.now();
  while (Date.now() - t0 < 180000) {
    const done = await page.$('[data-contact-import-result]');
    if (done) break;
    const p = await page.$('[data-contact-import-progress]');
    if (p) { const t = await p.textContent(); if (t && out.progress[out.progress.length - 1] !== t) out.progress.push(t); }
    if (out.progress.length === 3) {
      out.progressVisible = await page.$eval('[data-contact-import-progress]', (e) => {
        const r = e.getBoundingClientRect(); return r.top >= 0 && r.bottom <= window.innerHeight; });
      if (shots) await page.screenshot({ path: `${shots}/07-import-grande-avanzamento.png` });
    }
    await page.waitForTimeout(150);
  }
  out.result = await page.$eval('[data-contact-import-result]', (e) => e.textContent);
  if (shots) await page.screenshot({ path: `${shots}/08-import-grande-esito.png` });
  await browser.close();
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


def test_rubrica_oltre_5000_schede_nel_browser(app, server, tmp_path, monkeypatch):
    found = _node_con_playwright()
    if found is None:
        pytest.skip("node con playwright non disponibile: collaudo nel browser NON eseguito (BLOCKED)")
    node, env = found
    from contact_imports import service
    from tests.test_contact_import_postgres import _big_vcf

    monkeypatch.setattr(service, "BLOCK_SIZE", 1000)
    monkeypatch.setattr(service, "TIME_BUDGET_SECONDS", 0.0)       # un blocco per chiamata: si vede l'avanzamento
    monkeypatch.setenv("CONTACT_IMPORT_MAX_MB", "2")
    n = 6200
    vcf = tmp_path / "rubrica-grande.vcf"
    vcf.write_bytes(_big_vcf(n, start=3450000000, photo_every=200))
    heavy = tmp_path / "rubrica-con-foto.vcf"
    heavy.write_bytes(_big_vcf(800, start=3440000000, photo_every=1))
    assert heavy.stat().st_size > 2 * 1024 * 1024 > vcf.stat().st_size
    shots = SHOTS or str(tmp_path)
    Path(shots).mkdir(parents=True, exist_ok=True)
    script = tmp_path / "grande.js"
    script.write_text(GRANDE, encoding="utf-8")
    esito = subprocess.run([node, str(script), server, PASSWORD, str(vcf), str(heavy), shots],
                           capture_output=True, text=True, timeout=400, env=env)
    assert esito.returncode == 0, esito.stderr[-4000:]
    out = json.loads(esito.stdout.strip().splitlines()[-1])
    assert out["errors"] == [], out["errors"]
    distinct = n - n // 25
    assert "il massimo è 2 MB" in out["tooLarge"] and "senza foto" in out["tooLarge"]
    assert "5.952 schede nel file" in out["summary"] and "5.952 nuove" in out["summary"] and distinct == 5952
    assert out["rows"] == service.PREVIEW_SAMPLE and "«Importa tutti» le importa tutte" in out["sample"]
    steps = [t for t in out["progress"] if t.startswith("Importate ")]
    assert len(steps) >= 3 and steps[0] == "Importate 1.000 di 5.952 schede…"
    assert out["progressVisible"] is True                          # l'avanzamento si vede senza scorrere
    assert "5.952 contatti creati" in out["result"]
    ids = app["ids"]
    assert _q(app, "SELECT count(*) FROM contacts WHERE agency_id = %s AND display_name LIKE 'Rubrica Grande%%' "
                   "AND phone_normalized LIKE '39345%%'", (ids["b"],))[0][0] == distinct
