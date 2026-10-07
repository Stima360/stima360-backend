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
const responses = [];
const blocked = [];
const errors = [];
let quickPosts = 0;
let accepted;
await context.addInitScript((apiBase) => { window.STIMA360_PREVIEW_API_BASE = apiBase; }, origin.origin);
await context.route('**/*', async (route) => {
  const url = new URL(route.request().url());
  if (url.origin === origin.origin && url.pathname === '/api/salva_stima'
      && route.request().method() === 'POST') quickPosts++;
  if (phase === 'lost-response' && url.origin === origin.origin && url.pathname === '/api/salva_stima'
      && route.request().method() === 'POST') {
    const serverResponse = await route.fetch({ maxRetries: 0 });
    assert.equal(serverResponse.status(), 200, await serverResponse.text());
    const saved = await serverResponse.json();
    assert.ok(Number.isSafeInteger(saved.id) && saved.id > 0);
    const proof = await context.request.get(origin.origin + '/__journey_proof/quantities');
    assert.equal(proof.status(), 200);
    accepted = { id: saved.id, status: serverResponse.status(), quantities: await proof.json() };
    await writeFile(outputFile + '.accepted.json', JSON.stringify(accepted, null, 2));
    // This is a real accepted request: drop only its browser response, after
    // the server has committed and the independent DB read has completed.
    await route.abort('failed');
    return;
  }
  if (phase === 'crm' && url.origin === origin.origin && url.pathname === '/api/operator-auth/me') {
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(input.session) });
  }
  if (url.origin === origin.origin) return route.continue();
  blocked.push(url.origin + url.pathname);
  await route.abort();
});
context.on('page', (page) => {
  page.setDefaultTimeout(15000);
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('dialog', (dialog) => dialog.dismiss());
});
context.on('response', (response) => {
  const url = new URL(response.url());
  if (url.pathname === '/api/prefill') responses.push({ url: url.pathname, status: response.status(), body: response.json().catch(() => null) });
});

function detailFields(request) {
  const body = request.postDataBuffer()?.toString('utf8') || '';
  const fields = {};
  for (const match of body.matchAll(/name="([^\"]+)"\r\n\r\n([^]*?)\r\n--/g)) fields[match[1]] = match[2];
  return fields;
}

async function fillDetails(page) {
  await page.waitForFunction(() => !document.querySelector('#classe').disabled ||
    !document.querySelector('#detailNewIntent').hidden);
  if (await page.locator('#detailEdit').isVisible()) {
    await page.locator('#detailEdit').click();
    await page.waitForFunction(() => !document.querySelector('#classe').disabled);
  }
  const summary = await page.locator('#infoImmobile').textContent();
  assert.ok(summary.includes('Via Sintetica'), summary);
  for (const [field, value] of Object.entries({ classe: 'D', riscaldamento: 'Autonomo', condizionatore: 'No',
    spese_cond_flag: 'No', esposizione: 'Sud', arredo: 'Vuoto', contatto: 'No' })) {
    await page.locator('#' + field).selectOption(value);
  }
  await page.locator('#note').fill('Percorso sintetico locale senza invii esterni');
  const requestPromise = page.waitForRequest((request) => new URL(request.url()).pathname === '/api/salva_stima_dettagliata');
  const responsePromise = page.waitForResponse((response) => new URL(response.url()).pathname === '/api/salva_stima_dettagliata');
  await page.locator('button[type="submit"]').click();
  const request = await requestPromise;
  const response = await responsePromise;
  assert.equal(response.status(), 200, await response.text());
  const accepted = await response.json();
  assert.equal(accepted.ok, true);
  assert.equal(accepted.receipt.status, 'completed');
  await page.waitForFunction(() => document.getElementById('esito').textContent.includes('Richiesta inviata'));
  return detailFields(request);
}

async function openDetailsFromRedirect(page) {
  await page.waitForFunction(() => !document.querySelector('#stimaPro').disabled);
  const popupPromise = context.waitForEvent('page');
  await page.locator('#stimaPro').click();
  const detailPage = await popupPromise;
  await detailPage.waitForURL('**/stima_dettagliata.html?*');
  return detailPage;
}

try {
  let result;
  if (phase === 'initial') {
    const page = await context.newPage();
    await page.goto(origin.origin + '/preview/index.html');
    await page.locator('#regione').selectOption('Abruzzo');
    await page.locator('#comune').selectOption('Tortoreto');
    await page.locator('#microzona').selectOption('Lido Sud');
    await page.locator('#via').fill('Via Sintetica');
    await page.locator('#civico').fill('12');
    await page.locator('#tipologia').selectOption('Appartamento');
    await page.locator('#mq').fill('85');
    await page.locator('#piano').selectOption('3');
    const baseResponse = page.waitForResponse((response) => new URL(response.url()).pathname === '/api/stima_base');
    await page.locator('#btnAvanti').click();
    assert.equal((await baseResponse).status(), 200);
    await page.locator('#locali').selectOption('Trilocale');
    await page.locator('#bagni').fill('2');
    await page.locator('#ascensore').selectOption('Sì');
    await page.locator('#anno').fill('1998');
    await page.locator('#stato').selectOption('ristrutturato');
    await page.locator('#garageChk').check();
    await page.locator('#mqGarage').fill('18');
    await page.locator('#posizioneMare').selectOption('Oltre la seconda fila');
    await page.locator('#distanzaMare').selectOption('100–300 m');
    await page.locator('#barrieraMare').selectOption('no');
    await page.locator('#vistaMareYN').selectOption('no');
    await page.locator('#btnSubmit').click();
    await page.waitForURL('**/dati_personali.html?*');
    assert.match(new URL(page.url()).searchParams.get('id'), /^ST/);
    for (const [field, value] of Object.entries({ nome: 'Cliente', cognome: 'Sintetico', email: 'journey@example.invalid', telefono: '3331234567' })) {
      await page.locator('#' + field).fill(value);
    }
    await page.locator('#privacy').check();
    const quickResponse = page.waitForResponse((response) => new URL(response.url()).pathname === '/api/salva_stima');
    await page.locator('button[type="submit"]').click();
    const quickHttp = await quickResponse;
    assert.equal(quickHttp.status(), 200, await quickHttp.text());
    const quick = await quickHttp.json();
    await page.waitForURL('**/pdf_redirect.html?*');
    const redirect = new URL(page.url());
    assert.equal(redirect.searchParams.get('token'), quick.token);
    assert.deepEqual([...redirect.searchParams.keys()], ['token']);
    const stored = await page.evaluate(() => JSON.parse(localStorage.getItem('stimaBase')));
    const pdfLink = page.locator('#pdfDownload');
    await pdfLink.waitFor({ state: 'visible' });
    assert.match(await pdfLink.getAttribute('href'), /^blob:/);
    assert.equal(await pdfLink.getAttribute('download'), 'stima_' + quick.id + '.pdf');
    const downloadPromise = page.waitForEvent('download');
    await pdfLink.click();
    const download = await downloadPromise;
    assert.equal(download.suggestedFilename(), 'stima_' + quick.id + '.pdf');
    await download.saveAs(outputFile + '.pdf');
    assert.match(new URL(page.url()).pathname, /pdf_redirect\.html$/);
    const detailPage = await openDetailsFromRedirect(page);
    assert.equal(new URL(detailPage.url()).searchParams.get('token'), quick.token);
    const detail = await fillDetails(detailPage);
    const pdfResponse = await context.request.get(quick.pdf_url);
    assert.equal(pdfResponse.status(), 200);
    assert.match(pdfResponse.headers()['content-type'], /application\/pdf/);
    assert.match(pdfResponse.headers()['cache-control'], /no-store/);
    assert.equal(pdfResponse.headers()['referrer-policy'], 'no-referrer');
    const pdf = await pdfResponse.body();
    assert.equal(pdf.subarray(0, 5).toString(), '%PDF-');
    const prefillResponses = await Promise.all(responses.filter((response) => response.url === '/api/prefill' && response.status === 200).map((response) => response.body));
    assert.ok(prefillResponses.length >= 2);
    result = { quick, stored, detail, prefill: prefillResponses.at(-1), pdf_bytes: pdf.length, redirect: redirect.href };
  } else if (phase === 'links') {
    const submissions = [];
    for (const link of input.links) {
      assert.equal(new URL(link).origin, origin.origin);
      const page = await context.newPage();
      await page.goto(link);
      const detailPage = new URL(link).pathname.endsWith('/pdf_redirect.html') ? await openDetailsFromRedirect(page) : page;
      const fields = await fillDetails(detailPage);
      assert.equal(fields.token, input.token);
      assert.equal(Number(fields.stima_id), input.id);
      submissions.push({ token: fields.token, prefill_id: Number(fields.stima_id) });
      if (detailPage !== page) await detailPage.close();
      await page.close();
    }
    result = { submissions };
  } else if (phase === 'crm') {
    const page = await context.newPage();
    await page.goto(origin.origin + '/os/#/immobili/' + input.property_id);
    const provenance = page.locator('#site-provenance');
    await provenance.waitFor({ state: 'visible' });
    const text = await provenance.textContent();
    assert.ok(text.includes('Stima n. ' + input.stima_id), text);
    assert.match(text, /dettagliata/i);
    await page.screenshot({ path: outputFile + '.png', fullPage: true });
    await page.locator('#property-edit-btn').click();
    assert.equal(await page.locator('#pf-energy').inputValue(), 'D');
    assert.equal(await page.locator('#pf-heating').inputValue(), 'Autonomo');
    await page.screenshot({ path: outputFile + '.edit.png', fullPage: true });
    result = { property_id: input.property_id, stima_id: input.stima_id, provenance: text,
      energy_class: 'D', heating: 'Autonomo', authentication: 'synthetic fixture session only', property_api: 'real backend' };
  } else if (phase === 'pdf-recovery') {
    const page = await context.newPage();
    const query = new URLSearchParams({ id: 'STf07-pdf-recovery', comune: 'Tortoreto', microzona: 'Lido Sud',
      via: 'Via Sintetica F07', civico: '16', tipologia: 'Appartamento', mq: '85', piano: '3',
      locali: 'Trilocale', bagni: '2', ascensore: 'Sì', anno: '1998', stato: 'ristrutturato' });
    await page.goto(origin.origin + '/preview/dati_personali.html?' + query.toString().replaceAll('+', '%20'));
    for (const [field, value] of Object.entries({ nome: 'Cliente', cognome: 'Sintetico F07',
      email: 'f07-pdf-recovery@example.invalid', telefono: '3331234569' })) {
      await page.locator('#' + field).fill(value);
    }
    await page.locator('#privacy').check();
    const quickResponse = page.waitForResponse((response) => new URL(response.url()).pathname === '/api/salva_stima');
    await page.locator('#personalForm button[type="submit"]').click();
    const quickHttp = await quickResponse;
    assert.equal(quickHttp.status(), 202, await quickHttp.text());
    const quick = await quickHttp.json();
    assert.equal(quick.pdf_status, 'failed');
    assert.equal(quick.receipt.status, 'partial');
    // Follow the service email's authorized PDF link while the receipt
    // truthfully reports the failed generation. No new quick submission.
    await page.goto(quick.pdf_redirect_url);
    const redirect = new URL(page.url());
    assert.deepEqual([...redirect.searchParams.keys()], ['token']);
    assert.equal(redirect.searchParams.get('token'), quick.token);
    const retry = page.locator('#pdfRetry');
    await retry.waitFor({ state: 'visible' });
    assert.equal(await retry.getAttribute('type'), 'button');
    assert.match(await retry.textContent(), /Recupera il PDF/);
    await page.screenshot({ path: outputFile + '.failed.png', fullPage: true });
    const beforeHttp = await context.request.get(origin.origin + '/__journey_proof/quantities');
    const afterCommit = await beforeHttp.json();
    let retryPosts = 0;
    page.on('request', (request) => {
      if (new URL(request.url()).pathname === '/api/stime/' + quick.id + '/pdf/retry'
          && request.method() === 'POST') retryPosts++;
    });
    const retryResponse = page.waitForResponse((response) => new URL(response.url()).pathname === '/api/stime/' + quick.id + '/pdf/retry');
    await retry.click();
    const retryHttp = await retryResponse;
    assert.equal(retryHttp.status(), 200);
    assert.match(retryHttp.headers()['content-type'], /application\/pdf/);
    const pdfLink = page.locator('#pdfDownload');
    await pdfLink.waitFor({ state: 'visible' });
    const downloadPromise = page.waitForEvent('download');
    await pdfLink.click();
    await (await downloadPromise).saveAs(outputFile + '.pdf');
    await page.screenshot({ path: outputFile + '.ready.png', fullPage: true });
    const proof = quickHttp.request().headers()['x-receipt-key'];
    const resumed = await context.request.post(origin.origin + '/api/submissions/' + quick.receipt.request_id + '/resume',
      { headers: { 'X-Receipt-Key': proof } });
    assert.equal(resumed.status(), 200, await resumed.text());
    const completed = await resumed.json();
    assert.equal(completed.id, quick.id);
    assert.equal(completed.receipt.status, 'completed');
    assert.equal(completed.pdf_status, 'ready');
    const afterHttp = await context.request.get(origin.origin + '/__journey_proof/quantities');
    assert.equal(quickPosts, 1);
    assert.equal(retryPosts, 1);
    result = { stima_id: quick.id, quick_posts: quickPosts, retry_posts: retryPosts,
      after_commit: afterCommit, after_retry: await afterHttp.json() };
  } else if (phase === 'lost-response') {
    const page = await context.newPage();
    const query = new URLSearchParams({ id: 'STr1-lost-response', comune: 'Tortoreto', microzona: 'Lido Sud',
      via: 'Via Sintetica R1', civico: '14', tipologia: 'Appartamento', mq: '85', piano: '3',
      locali: 'Trilocale', bagni: '2', ascensore: 'Sì', anno: '1998', stato: 'ristrutturato' });
    await page.goto(origin.origin + '/preview/dati_personali.html?' + query.toString().replaceAll('+', '%20'));
    for (const [field, value] of Object.entries({ nome: 'Cliente', cognome: 'Sintetico R1',
      email: 'r1-lost-response@example.invalid', telefono: '3331234568' })) {
      await page.locator('#' + field).fill(value);
    }
    await page.locator('#privacy').check();
    await page.locator('#personalForm button[type="submit"]').click();
    await page.waitForFunction(() => document.getElementById('testo-stima').textContent.includes('NON CONFERMATO'));
    assert.ok(accepted, 'backend acceptance was not observed before losing the response');
    const recovery = page.getByRole('button', { name: 'Ricarica la pagina', exact: true });
    await recovery.waitFor({ state: 'visible' });
    const reloadButtonType = await recovery.getAttribute('type');
    assert.equal(reloadButtonType, 'button');
    assert.equal(await page.evaluate(() => localStorage.getItem('stimaBase')), null);
    await page.screenshot({ path: outputFile + '.error.png', fullPage: true });
    // Even a second submit event cannot escape the terminal uncertain state.
    await page.evaluate(() => document.getElementById('personalForm').dispatchEvent(
      new Event('submit', { bubbles: true, cancelable: true })));
    const afterTerminal = await context.request.get(origin.origin + '/__journey_proof/quantities');
    const afterTerminalSubmit = await afterTerminal.json();
    assert.equal(quickPosts, 1);
    const reload = page.waitForEvent('framenavigated', (frame) => frame === page.mainFrame());
    await recovery.click();
    await reload;
    await page.waitForLoadState('load');
    await page.waitForURL('**/pdf_redirect.html?*');
    await page.locator('#pdfDownload').waitFor({ state: 'visible' });
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const afterReloadHttp = await context.request.get(origin.origin + '/__journey_proof/quantities');
    const afterReload = await afterReloadHttp.json();
    assert.equal(quickPosts, 1, 'recovery reload must not post the form');
    result = { accepted_stima_id: accepted.id, server_status: accepted.status,
      after_commit: accepted.quantities, after_terminal_submit: afterTerminalSubmit, after_reload: afterReload,
      quick_posts: quickPosts, reload_button_type: reloadButtonType, reload_completed: true };
  } else {
    throw new Error('Unknown phase: ' + phase);
  }
  assert.deepEqual(errors, [], 'unexpected page script failures');
  await writeFile(outputFile, JSON.stringify({ ...result, external_requests_blocked: blocked }, null, 2));
  console.log(phase + ': real browser/backend journey verified; external requests blocked=' + blocked.length);
} catch (error) {
  const page = context.pages().at(-1);
  if (page) {
    await page.screenshot({ path: outputFile + '.failure.png', fullPage: true }).catch(() => {});
    console.error('Failure URL:', page.url());
    console.error('Browser script errors:', errors);
  }
  throw error;
} finally {
  await context.close();
  await browser.close();
}
