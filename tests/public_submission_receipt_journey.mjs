import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFile, writeFile } from 'node:fs/promises';

const require = createRequire(import.meta.url);
const { chromium } = require('playwright');
const [phase, inputFile, outputFile] = process.argv.slice(2);
const input = JSON.parse(await readFile(inputFile, 'utf8'));
const origin = new URL(input.origin);
assert.equal(origin.hostname, '127.0.0.1');
const browser = await chromium.launch({ headless: true, executablePath: process.env.STIMA360_CHROMIUM_PATH });
const context = await browser.newContext({ serviceWorkers: 'block' });
const blocked = [];
const errors = [];
let quickPosts = 0;
let receiptReads = 0;
let resumePosts = 0;
let accepted;
let requestProof;
let latestReceipt;
let resolveAcceptance;
let storedDetailUrl;
const acceptanceObserved = new Promise((resolve) => { resolveAcceptance = resolve; });
// R1: a ready result whose only open item is an accessory notification.
const notificationPhases = { 'attention-admin': 'attention', 'attention-whatsapp': 'attention',
  'preflight-admin': 'partial' };
const firstStatus = phase === 'partial' || notificationPhases[phase] ? 202 : 200;
const firstReceipt = notificationPhases[phase] || (phase === 'partial' ? 'partial' : 'completed');
const finalReceipt = notificationPhases[phase] || 'completed';
await context.addInitScript((apiBase) => { window.STIMA360_PREVIEW_API_BASE = apiBase; }, origin.origin);
await context.route('**/*', async (route) => {
  const request = route.request();
  const url = new URL(request.url());
  if (url.origin !== origin.origin) {
    blocked.push(url.origin + url.pathname);
    return route.abort();
  }
  if (url.pathname === '/api/salva_stima' && request.method() === 'POST') {
    quickPosts++;
    const headers = request.headers();
    requestProof = { id: headers['idempotency-key'], key: headers['x-receipt-key'] };
    assert.match(requestProof.id, /^[0-9a-f-]{36}$/i);
    assert.match(requestProof.key, /^[0-9a-f]{64}$/i);
    if (quickPosts === 1) {
      const response = await route.fetch({ maxRetries: 0 });
      assert.equal(response.status(), firstStatus, await response.text());
      const result = await response.json();
      assert.ok(Number.isSafeInteger(result.id) && result.id > 0);
      assert.equal(result.receipt.status, firstReceipt);
      if (notificationPhases[phase]) assert.equal(result.receipt.result_available, true);
      const proof = await context.request.get(origin.origin + '/__journey_proof/quantities');
      assert.equal(proof.status(), 200);
      accepted = { id: result.id, token: result.token, receipt: result.receipt, afterSave: await proof.json() };
      await writeFile(outputFile + '.accepted.json', JSON.stringify({ id: result.id, receipt_status: result.receipt.status,
        quantities: accepted.afterSave }, null, 2));
      if (phase === 'lost-response') {
        await route.abort('failed');
        resolveAcceptance();
        return;
      }
      latestReceipt = result;
      await route.fulfill({ response });
      resolveAcceptance();
      return;
    }
  }
  if (/^\/api\/submissions\/[0-9a-f-]+$/i.test(url.pathname) && request.method() === 'GET') {
    receiptReads++;
    assert.equal(request.headers()['x-receipt-key'], requestProof.key);
  }
  if (/^\/api\/submissions\/[0-9a-f-]+\/resume$/i.test(url.pathname) && request.method() === 'POST') resumePosts++;
  return route.continue();
});
context.on('page', (page) => {
  page.setDefaultTimeout(15000);
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('dialog', (dialog) => dialog.dismiss());
});
context.on('response', async (response) => {
  if (/^\/api\/submissions\/[0-9a-f-]+(?:\/resume)?$/i.test(new URL(response.url()).pathname)
      && [200, 202].includes(response.status())) {
    latestReceipt = await response.json();
  }
});

try {
  assert.ok(['lost-response', 'partial', 'double-click', ...Object.keys(notificationPhases)].includes(phase));
  const page = await context.newPage();
  const query = new URLSearchParams({ id: 'STf0406-lost-response', comune: 'Tortoreto', microzona: 'Lido Sud',
    via: 'Via Sintetica F04 F06', civico: '18', tipologia: 'Appartamento', mq: '85', piano: '3',
    locali: 'Trilocale', bagni: '2', ascensore: 'Sì', anno: '1998', stato: 'ristrutturato' });
  await page.goto(origin.origin + '/preview/dati_personali.html?' + query.toString().replaceAll('+', '%20'));
  for (const [field, value] of Object.entries({ nome: 'Cliente', cognome: 'Sintetico F04 F06',
    email: 'f0406-lost-response@example.invalid', telefono: '3331234568' })) {
    await page.locator('#' + field).fill(value);
  }
  await page.locator('#privacy').check();
  if (notificationPhases[phase]) {
    const personalUrl = page.url();
    await page.locator('#personalForm button[type="submit"]').click();
    await acceptanceObserved;
    await page.waitForFunction(() => document.getElementById('testo-stima').textContent.includes('Valore totale'));
    const shown = await page.locator('#testo-stima').textContent();
    assert.ok(shown.includes('in verifica') && !shown.includes('Ti arriverà subito'), shown);
    await page.screenshot({ path: outputFile + '.ready-notification.png', fullPage: true });
    await page.waitForURL('**/pdf_redirect.html?*');
    // Reopen the personal page: the stored receipt is read again, never resent.
    await page.goto(personalUrl);
    await page.waitForFunction(() => document.getElementById('testo-stima').textContent.includes('Valore totale'));
    assert.equal(await page.locator('#personalForm button').isVisible(), false);
    const stored = await page.evaluate(() => JSON.parse(localStorage.getItem('stimaBase')));
    assert.equal(stored.id, accepted.id);
    assert.equal(new URL(stored.detail_url).searchParams.get('token'), accepted.token);
    storedDetailUrl = stored.detail_url;
  } else if (phase === 'double-click') {
    await page.evaluate(() => {
      const form = document.getElementById('personalForm');
      form.requestSubmit();
      form.requestSubmit();
    });
  } else {
    await page.locator('#personalForm button[type="submit"]').click();
    await acceptanceObserved;
    await page.waitForFunction(() => !document.querySelector('#personalForm button').disabled);
    assert.ok(accepted, 'server acceptance must precede the browser recovery');
    assert.equal(quickPosts, 1);
    await page.screenshot({ path: outputFile + '.uncertain.png', fullPage: true });
    // Proof was persisted before POST and survives this real reload.
    await page.reload();
    if (phase === 'partial') {
      const resume = page.getByRole('button', { name: 'Recupera la richiesta', exact: true });
      await resume.waitFor({ state: 'visible' });
      assert.equal(await resume.getAttribute('type'), 'button');
      await page.screenshot({ path: outputFile + '.partial.png', fullPage: true });
      await resume.click();
    }
  }
  await page.waitForURL('**/pdf_redirect.html?*');
  await page.locator('#pdfDownload').waitFor({ state: 'visible' });
  assert.ok(latestReceipt);
  assert.equal(latestReceipt.id, accepted.id);
  if (accepted.token) assert.equal(latestReceipt.token, accepted.token);
  assert.equal(latestReceipt.receipt.status, finalReceipt);
  if (notificationPhases[phase]) assert.equal(latestReceipt.receipt.result_available, true);
  assert.equal(new URL(page.url()).searchParams.get('token'), latestReceipt.token);
  const downloadPromise = page.waitForEvent('download');
  await page.locator('#pdfDownload').click();
  await (await downloadPromise).saveAs(outputFile + '.pdf');
  await page.screenshot({ path: outputFile + '.recovered.png', fullPage: true });
  let detailPrefill = null;
  if (storedDetailUrl) {
    // The authorized detailed form opens through the same capability.
    const prefill = page.waitForResponse((response) => new URL(response.url()).pathname === '/api/prefill');
    await page.goto(storedDetailUrl);
    const prefillResponse = await prefill;
    assert.equal(prefillResponse.status(), 200);
    detailPrefill = (await prefillResponse.json()).id;
    assert.equal(detailPrefill, accepted.id);
    await page.locator('#dettaglioForm').waitFor({ state: 'visible' });
    await page.screenshot({ path: outputFile + '.detail.png', fullPage: true });
  }
  const after = await context.request.get(origin.origin + '/__journey_proof/quantities');
  const result = { stima_id: accepted.id, quick_posts: quickPosts, receipt_reads: receiptReads,
    resume_posts: resumePosts, receipt_status: latestReceipt.receipt.status,
    result_available: latestReceipt.receipt.result_available, notifications: latestReceipt.receipt.notifications,
    after_save: accepted.afterSave, after_recovery: await after.json(), blocked_external_requests: blocked,
    browser_errors: errors, detail_prefill_stima_id: detailPrefill };
  assert.equal(quickPosts, 1);
  assert.equal(resumePosts, phase === 'partial' ? 1 : 0);
  if (phase !== 'double-click') assert.ok(receiptReads >= 1);
  if (phase === 'partial') assert.equal(result.after_recovery.stime, result.after_save.stime);
  else assert.deepEqual(result.after_recovery, result.after_save);
  assert.deepEqual(errors, []);
  await writeFile(outputFile, JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result));
} finally {
  await context.close();
  await browser.close();
}
