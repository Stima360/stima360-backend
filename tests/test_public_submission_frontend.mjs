import assert from 'node:assert/strict';
import { before, after, test } from 'node:test';
import { createRequire } from 'node:module';
import { readFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const { chromium } = require('playwright');
const repo = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const origin = 'http://127.0.0.1:4173';
const requestId = '82646876-8777-4e3e-8494-a59c1a8e159e';
const receiptKey = 'a'.repeat(64);
const token = 'synthetic-token-current-estimate';
const quick = {
  id: 101, token, pdf_url: '', price_exact: 240000, eur_mq_finale: 3000,
  detail_url: 'https://www.stima360.it/stima_dettagliata.html?token=' + token,
  pdf_redirect_url: 'https://www.stima360.it/pdf_redirect.html?token=' + token
};
const prefill = { id: 101, nome: 'Cliente', cognome: 'Sintetico', email: 'test@example.invalid',
  telefono: '3331234567', comune: 'Tortoreto', microzona: 'Lido Sud', via: 'Via Sintetica',
  civico: '12', tipologia: 'Appartamento', mq: '85', piano: '3', locali: 'Trilocale', bagni: '2' };
let browser;

before(async () => {
  browser = await chromium.launch({ headless: true, executablePath: process.env.STIMA360_CHROMIUM_PATH });
});
after(async () => { await browser?.close(); });

async function openPage(t, name, options = {}) {
  const context = await browser.newContext({ serviceWorkers: 'block' });
  t.after(() => context.close());
  const page = await context.newPage();
  page.setDefaultTimeout(5000);
  const requests = [];
  await page.addInitScript(({ requestId, receiptKey, seedDraft }) => {
    window.STIMA360_PREVIEW_API_BASE = 'http://127.0.0.1:4173';
    const key = 'stima360:submission:v1:' + requestId;
    if (seedDraft && !localStorage.getItem(key)) {
      localStorage.setItem(key, JSON.stringify({ version: 1, request_id: requestId, receipt_key: receiptKey,
        kind: 'quick', scope: 'STsynthetic-draft', payload: null, completed: false }));
    }
    window.saveDispatches = 0;
    const originalFetch = window.fetch;
    window.fetch = (...args) => {
      if (String(args[0]).includes('/api/salva_stima') && args[1]?.method === 'POST') window.saveDispatches++;
      return originalFetch(...args);
    };
  }, { requestId, receiptKey, seedDraft: options.seedDraft !== false });
  if (options.storageUnavailable) {
    await page.addInitScript(() => {
      Storage.prototype.getItem = () => { throw new Error('Synthetic storage unavailable'); };
      Storage.prototype.setItem = () => { throw new Error('Synthetic storage unavailable'); };
    });
  }
  // A dialog can close with its page; a late dismissal is not a product result.
  page.on('dialog', (dialog) => dialog.dismiss().catch(() => {}));
  // Record API requests on the context event, which precedes the page event
  // awaited by waitForRequest, so counts never race the route handler.
  context.on('request', (request) => {
    if (new URL(request.url()).pathname.startsWith('/api/')) requests.push(request);
  });
  await context.route('**/*', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname.startsWith('/api/')) {
      const value = await options.api?.(request, url);
      if (value?.abort) return route.abort(value.abort);
      let data = value?.data ?? (url.pathname === '/api/prefill' ? prefill :
        url.pathname === '/api/salva_stima' ? quick :
        url.pathname === '/api/stima_base' ? { valore_riferimento: 240000 } : { ok: true });
      if (['/api/salva_stima', '/api/salva_stima_dettagliata'].includes(url.pathname) && !data.receipt && value?.receipt !== false) {
        const kind = url.pathname === '/api/salva_stima' ? 'quick' : 'detail';
        data = { ...data, receipt: { request_id: request.headers()['idempotency-key'], kind,
          status: 'completed', resumable: false, steps: { database: 'done' }, stima_id: 101,
          detail_id: kind === 'detail' ? 201 : null } };
      }
      return route.fulfill({ status: value?.status ?? 200, contentType: 'application/json', body: JSON.stringify(data) });
    }
    // All external requests are blocked; every served page is the isolated source.
    if (url.origin === origin) {
      const file = url.pathname.split('/').pop() || 'index.html';
      if (/^[a-z_-]+\.(?:html|js)$/.test(file)) {
        try {
          return route.fulfill({ contentType: file.endsWith('.js') ? 'text/javascript' : 'text/html',
            body: await readFile(resolve(repo, 'site-preview/public', file)) });
        } catch { /* A missing source must remain visible in the test. */ }
      }
    }
    return route.abort();
  });
  const query = new URLSearchParams(name === 'stima_dettagliata.html' ? { token } :
    { id: 'STsynthetic-draft', request_id: requestId, comune: 'Tortoreto', via: 'Via Sintetica', mq: '85' });
  await page.goto(origin + '/' + name + '?' + query.toString().replaceAll('+', '%20'));
  await page.waitForLoadState('networkidle');
  return { page, context, requests };
}

async function fillPersonal(page) {
  for (const [field, value] of Object.entries({ nome: 'Cliente', cognome: 'Sintetico', email: 'test@example.invalid', telefono: '3331234567' })) {
    await page.locator('#' + field).fill(value);
  }
  await page.locator('#privacy').check();
}

async function fillDetails(page) {
  for (const [field, value] of Object.entries({ classe: 'D', riscaldamento: 'Autonomo', condizionatore: 'No',
    spese_cond_flag: 'No', esposizione: 'Sud', arredo: 'Vuoto', contatto: 'No' })) {
    await page.locator('#' + field).selectOption(value);
  }
}

function saves(requests, kind = 'quick') {
  const pathname = kind === 'quick' ? '/api/salva_stima' : '/api/salva_stima_dettagliata';
  return requests.filter((request) => request.method() === 'POST' && new URL(request.url()).pathname === pathname);
}

function envelope(request, kind = 'quick', status = 'completed', resumable = false, extra = {}) {
  return { ...(kind === 'quick' ? quick : { ok: true }), receipt: {
    request_id: request.headers()['idempotency-key'], kind, status, resumable,
    steps: { database: 'done', pdf: status === 'completed' ? 'done' : 'pending' },
    stima_id: 101, detail_id: kind === 'detail' ? 201 : null, ...extra
  } };
}

// Server-certified ready result whose only open item is an accessory notification.
function readyWithNotification(request, channel, state, status = 'attention') {
  const data = envelope(request, 'quick', status, status === 'partial', {
    steps: { bridge: 'succeeded', property: 'succeeded', pdf: 'succeeded', email_queue: 'succeeded',
      admin_email: 'succeeded', whatsapp: 'succeeded', [channel]: state },
    notifications: { admin_email: 'succeeded', whatsapp: 'succeeded', [channel]: state },
    result_available: true });
  return { ...data, success: false, ok: false, pdf_status: 'ready' };
}

test('F04 initial save sends a stable cryptorandom request identity', async (t) => {
  const { page, requests } = await openPage(t, 'dati_personali.html');
  await fillPersonal(page);
  await page.locator('#personalForm button').click();
  await page.waitForFunction(() => document.getElementById('testo-stima').textContent.includes('Valore totale'));
  assert.equal(saves(requests).length, 1);
  assert.equal(saves(requests)[0].headers()['idempotency-key'], requestId);
  assert.equal(saves(requests)[0].headers()['x-receipt-key'], receiptKey);
  assert.equal(new URL(page.url()).searchParams.has('receipt_key'), false);
  assert.equal(saves(requests)[0].postDataJSON().receipt_key, undefined);
});

test('F04 a lost initial response retains the exact request identity after reload', async (t) => {
  const { page, requests } = await openPage(t, 'dati_personali.html', { api: (_, url) =>
    url.pathname === '/api/salva_stima' ? { abort: 'failed' } : undefined });
  await fillPersonal(page);
  await page.locator('#personalForm button').click();
  await page.waitForFunction(() => !document.querySelector('.puntini'));
  const first = saves(requests)[0];
  assert.equal(first.headers()['idempotency-key'], requestId);
  await page.locator('#personalForm button').click();
  await page.waitForLoadState('networkidle');
  const recoveries = requests.filter((request) => /^\/api\/submissions\/[^/]+$/.test(new URL(request.url()).pathname));
  assert.ok(recoveries.length >= 1, 'reload checks a verifiable receipt before offering a retry');
  assert.equal(saves(requests).length, 1, 'reload does not submit a new estimate');
  assert.equal(recoveries.at(-1).headers()['idempotency-key'], requestId);
});

test('F04 detailed double submission dispatches one request while the first is pending', async (t) => {
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  const { page, requests } = await openPage(t, 'stima_dettagliata.html', { api: async (_, url) => {
    if (url.pathname === '/api/salva_stima_dettagliata') await pending;
  } });
  t.after(() => release());
  await fillDetails(page);
  const started = page.waitForRequest('**/api/salva_stima_dettagliata');
  await page.locator('#dettaglioForm button').click();
  await started;
  await page.evaluate(() => document.getElementById('dettaglioForm').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));
  assert.equal(await page.evaluate(() => window.saveDispatches), 1);
  assert.equal(saves(requests, 'details').length, 1);
  release();
});

test('F04 detailed save has a persistent request identity for retry after an uncertain result', async (t) => {
  const { page, requests } = await openPage(t, 'stima_dettagliata.html', { api: (_, url) =>
    url.pathname === '/api/salva_stima_dettagliata' ? { abort: 'failed' } : undefined });
  await fillDetails(page);
  const response = page.waitForEvent('requestfailed', (request) => new URL(request.url()).pathname === '/api/salva_stima_dettagliata');
  await page.locator('#dettaglioForm button').click();
  await response;
  const key = saves(requests, 'details')[0].headers()['idempotency-key'];
  assert.match(key || '', /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i);
  await page.reload();
  await page.waitForLoadState('networkidle');
  assert.equal(saves(requests, 'details').length, 1);
  const recoveries = requests.filter((request) => /^\/api\/submissions\/[^/]+$/.test(new URL(request.url()).pathname));
  assert.ok(recoveries.length >= 1);
  assert.equal(recoveries.at(-1).headers()['idempotency-key'], key);
});

test('F04 a retry after reload keeps the original submitted payload even if inputs change', async (t) => {
  const { page, requests } = await openPage(t, 'dati_personali.html', { api: (_, url) => {
    if (/^\/api\/submissions\/[^/]+$/.test(url.pathname)) return { status: 404, data: { detail: 'Synthetic request not received' } };
    if (url.pathname === '/api/salva_stima') return { abort: 'failed' };
  } });
  await fillPersonal(page);
  await page.locator('#personalForm button').click();
  await page.waitForFunction(() => !document.querySelector('.puntini'));
  const originalPayload = saves(requests)[0].postDataJSON();
  await page.locator('#personalForm button').click();
  await page.waitForLoadState('networkidle');
  await fillPersonal(page);
  await page.locator('#nome').fill('Other intentional customer');
  const nextRequest = page.waitForRequest('**/api/salva_stima');
  await page.locator('#personalForm button').click();
  await nextRequest;
  assert.equal(saves(requests).length, 2);
  assert.deepEqual(saves(requests)[1].postDataJSON(), originalPayload);
});

async function initialDraft(page) {
  await page.goto(origin + '/index.html');
  await page.locator('#regione').selectOption('Abruzzo');
  await page.locator('#comune').selectOption('Tortoreto');
  await page.locator('#microzona').selectOption('Lido Sud');
  await page.locator('#via').fill('Via Sintetica');
  await page.locator('#civico').fill('12');
  await page.locator('#tipologia').selectOption('Appartamento');
  await page.locator('#mq').fill('85');
  await page.locator('#piano').selectOption('3');
  await page.locator('#btnAvanti').click();
  await page.locator('#locali').selectOption('Trilocale');
  await page.locator('#bagni').fill('2');
  await page.locator('#ascensore').selectOption('Sì');
  await page.locator('#anno').fill('1998');
  await page.locator('#stato').selectOption('ristrutturato');
  await page.locator('#posizioneMare').selectOption('Oltre la seconda fila');
  await page.locator('#distanzaMare').selectOption('100–300 m');
  await page.locator('#barrieraMare').selectOption('no');
  await page.locator('#vistaMareYN').selectOption('no');
  await page.locator('#btnSubmit').click();
  await page.waitForURL('**/dati_personali.html?*');
  return new URL(page.url());
}

test('F04 an intentional new initial evaluation receives a different request identity', async (t) => {
  const { page, requests } = await openPage(t, 'index.html');
  const first = (await initialDraft(page)).searchParams.get('request_id');
  const second = (await initialDraft(page)).searchParams.get('request_id');
  assert.match(first || '', /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i);
  assert.match(second || '', /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i);
  assert.notEqual(first, second);
  assert.equal(saves(requests).length, 0);
});

test('F06 a committed initial result is recovered by read-only receipt after a lost response', async (t) => {
  const { page, requests } = await openPage(t, 'dati_personali.html', { api: (request, url) => {
    if (url.pathname === '/api/salva_stima') return { abort: 'failed' };
    if (/^\/api\/submissions\/[^/]+$/.test(url.pathname)) return { data: envelope(request) };
  } });
  await fillPersonal(page);
  await page.locator('#personalForm button').click();
  await page.waitForFunction(() => !document.querySelector('.puntini'));
  await page.locator('#personalForm button').click();
  await page.waitForLoadState('networkidle');
  await page.waitForFunction(() => document.getElementById('testo-stima').textContent.includes('Valore totale'));
  assert.equal(saves(requests).length, 1);
  assert.equal(requests.filter((request) => request.method() === 'POST').length, 1);
  assert.ok((await page.locator('#submissionReceipt').textContent()).includes(requestId));
  const stored = await page.evaluate(() => JSON.parse(localStorage.getItem('stimaBase')));
  assert.equal(stored.id, 101);
  assert.equal(stored.token, token);
});

test('F06 a partial initial receipt waits for explicit recovery and sends no new payload', async (t) => {
  const { page, requests } = await openPage(t, 'dati_personali.html', { api: (request, url) => {
    if (url.pathname === '/api/salva_stima') return { status: 202, data: envelope(request, 'quick', 'partial', true) };
    if (url.pathname.endsWith('/resume')) return { data: envelope(request) };
  } });
  await fillPersonal(page);
  await page.locator('#personalForm button').click();
  await page.getByRole('button', { name: 'Recupera la richiesta', exact: true }).waitFor({ state: 'visible' });
  assert.equal(requests.filter((request) => request.method() === 'POST').length, 1);
  assert.ok(!(await page.locator('#testo-stima').textContent()).includes('Valore totale'));
  assert.equal(new URL(page.url()).pathname, '/dati_personali.html');
  await page.locator('#nome').fill('Changed input must not be resent');
  await page.getByRole('button', { name: 'Recupera la richiesta', exact: true }).click();
  await page.waitForFunction(() => document.getElementById('testo-stima').textContent.includes('Valore totale'));
  const resumes = requests.filter((request) => new URL(request.url()).pathname.endsWith('/resume'));
  assert.equal(resumes.length, 1);
  assert.equal(resumes[0].method(), 'POST');
  assert.equal(resumes[0].postData(), null);
  assert.equal(resumes[0].headers()['idempotency-key'], requestId);
  assert.equal(resumes[0].headers()['x-receipt-key'], receiptKey);
  assert.equal(saves(requests).length, 1);
});

test('F06 attention receipt without a certified result offers only read-only verification', async (t) => {
  const { page, requests } = await openPage(t, 'dati_personali.html', { api: (request, url) => {
    if (url.pathname === '/api/salva_stima' || url.pathname.startsWith('/api/submissions/')) {
      return { status: 202, data: envelope(request, 'quick', 'attention', false, { result_available: false }) };
    }
  } });
  await fillPersonal(page);
  await page.locator('#personalForm button').click();
  const verify = page.getByRole('button', { name: 'Verifica ricevuta', exact: true });
  const read = page.waitForRequest((request) => request.method() === 'GET' && /^\/api\/submissions\/[^/]+$/.test(new URL(request.url()).pathname));
  await verify.click();
  await read;
  await verify.waitFor({ state: 'visible' });
  assert.equal(requests.filter((request) => request.method() === 'POST').length, 1);
  assert.equal(requests.filter((request) => request.method() === 'GET' && /^\/api\/submissions\/[^/]+$/.test(new URL(request.url()).pathname)).length, 1);
  assert.ok(!(await page.locator('#testo-stima').textContent()).includes('Valore totale'));
  assert.equal(await page.evaluate(() => localStorage.getItem('stimaBase')), null);
});

for (const [channel, status, state] of [['admin_email', 'attention', 'indeterminate'], ['whatsapp', 'attention', 'indeterminate'],
  ['admin_email', 'partial', 'failed'], ['whatsapp', 'partial', 'failed']]) {
  test(`F06 R1 ${status} ${channel} ${state} with a certified ready result shows the value and detail without resending`, async (t) => {
    const { page, requests } = await openPage(t, 'dati_personali.html', { api: (request, url) => {
      if (url.pathname === '/api/salva_stima') return { status: 202, data: readyWithNotification(request, channel, state, status) };
    } });
    await fillPersonal(page);
    await page.locator('#personalForm button').click();
    await page.waitForFunction(() => document.getElementById('testo-stima').textContent.includes('Valore totale'));
    const text = await page.locator('#testo-stima').textContent();
    assert.ok(text.includes('240.000'));
    assert.ok(text.includes('in verifica'), 'the uncertain notification is not reported as delivered');
    assert.ok(!text.includes('Ti arriverà subito'));
    assert.equal(await page.locator('#personalForm button').isVisible(), false, 'no recovery or verification button');
    assert.ok((await page.locator('#submissionReceipt').textContent()).includes(requestId));
    const stored = await page.evaluate(() => JSON.parse(localStorage.getItem('stimaBase')));
    assert.equal(stored.id, 101);
    assert.equal(stored.token, token);
    assert.equal(new URL(stored.detail_url).pathname, '/stima_dettagliata.html');
    assert.equal(new URL(stored.detail_url).searchParams.get('token'), token);
    assert.equal(new URL(stored.pdf_redirect_url).searchParams.get('token'), token);
    assert.equal(saves(requests).length, 1);
    assert.equal(requests.filter((request) => request.method() === 'POST').length, 1);
    assert.equal(requests.filter((request) => new URL(request.url()).pathname.endsWith('/resume')).length, 0);
  });
}

test('F06 R1 attention with a certified ready PDF opens the same private PDF loader', async (t) => {
  const { page, requests } = await openPage(t, 'dati_personali.html', { api: (request, url) => {
    if (url.pathname === '/api/salva_stima') {
      const data = readyWithNotification(request, 'admin_email', 'indeterminate');
      return { status: 202, data: { ...data, pdf_url: 'https://api.example.invalid/api/stime/101/pdf?t=' + token } };
    }
  } });
  await fillPersonal(page);
  await page.locator('#personalForm button').click();
  await page.waitForURL('**/pdf_redirect.html?*');
  assert.equal(new URL(page.url()).searchParams.get('token'), token);
  assert.equal(saves(requests).length, 1);
  assert.equal(requests.filter((request) => new URL(request.url()).pathname.endsWith('/resume')).length, 0);
});

test('F06 R1 reload of an attention receipt with a ready result restores it read-only', async (t) => {
  const { page, requests } = await openPage(t, 'dati_personali.html', { api: (request, url) => {
    if (url.pathname === '/api/salva_stima') return { abort: 'failed' };
    if (/^\/api\/submissions\/[^/]+$/.test(url.pathname)) {
      return { status: 202, data: readyWithNotification(request, 'whatsapp', 'indeterminate') };
    }
  } });
  await fillPersonal(page);
  await page.locator('#personalForm button').click();
  await page.waitForFunction(() => !document.querySelector('.puntini'));
  await page.reload();
  await page.waitForFunction(() => document.getElementById('testo-stima').textContent.includes('Valore totale'));
  assert.equal(saves(requests).length, 1, 'reload never submits a new estimate');
  assert.equal(requests.filter((request) => request.method() === 'POST').length, 1);
  assert.equal(requests.filter((request) => new URL(request.url()).pathname.endsWith('/resume')).length, 0);
  const reads = requests.filter((request) => /^\/api\/submissions\/[^/]+$/.test(new URL(request.url()).pathname));
  assert.ok(reads.length >= 1);
  assert.equal(reads.at(-1).headers()['idempotency-key'], requestId);
  assert.equal((await page.evaluate(() => JSON.parse(localStorage.getItem('stimaBase')))).id, 101);
});

test('F06 R1 double click while an attention result is pending dispatches one request', async (t) => {
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  const { page, requests } = await openPage(t, 'dati_personali.html', { api: async (request, url) => {
    if (url.pathname === '/api/salva_stima') {
      await pending;
      return { status: 202, data: readyWithNotification(request, 'admin_email', 'indeterminate') };
    }
  } });
  t.after(() => release());
  await fillPersonal(page);
  const started = page.waitForRequest('**/api/salva_stima');
  await page.locator('#personalForm button').click();
  await started;
  await page.evaluate(() => document.getElementById('personalForm').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));
  assert.equal(await page.evaluate(() => window.saveDispatches), 1);
  release();
  await page.waitForFunction(() => document.getElementById('testo-stima').textContent.includes('Valore totale'));
  assert.equal(saves(requests).length, 1);
});

for (const forged of [{ result_available: 'true' }, { result_available: true, stima_id: null }]) {
  test(`F06 R1 rejects an unverifiable result certification ${JSON.stringify(forged)}`, async (t) => {
    const { page, requests } = await openPage(t, 'dati_personali.html', { api: (request, url) => {
      if (url.pathname === '/api/salva_stima') {
        const data = readyWithNotification(request, 'admin_email', 'indeterminate');
        data.receipt = { ...data.receipt, ...forged };
        return { status: 202, data };
      }
    } });
    await fillPersonal(page);
    await page.locator('#personalForm button').click();
    await page.waitForFunction(() => !document.querySelector('.puntini'));
    assert.ok((await page.locator('#testo-stima').textContent()).includes('NON CONFERMATO'));
    assert.equal(await page.evaluate(() => localStorage.getItem('stimaBase')), null);
    assert.equal(saves(requests).length, 1);
  });
}

test('F06 storage unavailable prevents every initial save dispatch', async (t) => {
  const { page, requests } = await openPage(t, 'dati_personali.html', { seedDraft: false, storageUnavailable: true });
  await page.evaluate(() => document.getElementById('personalForm').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));
  assert.equal(saves(requests).length, 0);
  assert.equal(await page.locator('#newEvaluation').isVisible(), true);
  assert.ok((await page.locator('#testo-stima').textContent()).includes('memoria locale'));
});

test('F06 failure persisting the frozen payload prevents network dispatch', async (t) => {
  const { page, requests } = await openPage(t, 'dati_personali.html');
  await fillPersonal(page);
  await page.evaluate(() => { Storage.prototype.setItem = () => { throw new Error('Synthetic write failure'); }; });
  await page.locator('#personalForm button').click();
  assert.equal(saves(requests).length, 0);
  assert.ok((await page.locator('#testo-stima').textContent()).includes('Nessun nuovo invio'));
});

test('F04 two open draft tabs retain the same identity, proof and frozen payload', async (t) => {
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  const { page, context, requests } = await openPage(t, 'dati_personali.html', { api: async (_, url) => {
    if (url.pathname === '/api/salva_stima') await pending;
  } });
  t.after(() => release());
  const second = await context.newPage();
  await second.goto(page.url());
  await second.waitForLoadState('networkidle');
  await fillPersonal(page);
  await fillPersonal(second);
  const firstStarted = page.waitForRequest('**/api/salva_stima');
  await page.locator('#personalForm button').click();
  await firstStarted;
  const secondStarted = second.waitForRequest('**/api/salva_stima');
  await second.locator('#personalForm button').click();
  await secondStarted;
  const posts = saves(requests);
  assert.equal(posts.length, 2);
  assert.deepEqual(posts[0].postDataJSON(), posts[1].postDataJSON());
  assert.equal(posts[0].headers()['idempotency-key'], posts[1].headers()['idempotency-key']);
  assert.equal(posts[0].headers()['x-receipt-key'], posts[1].headers()['x-receipt-key']);
  release();
});

test('F04 detailed receipt survives reload and an explicit update gets a new identity for the same estimate', async (t) => {
  const { page, requests } = await openPage(t, 'stima_dettagliata.html', { api: (request, url) => {
    if (/^\/api\/submissions\/[^/]+$/.test(url.pathname)) return { data: envelope(request, 'detail') };
  } });
  await fillDetails(page);
  await page.locator('#dettaglioForm button').click();
  await page.waitForFunction(() => document.getElementById('esito').textContent.includes('Richiesta inviata'));
  const original = saves(requests, 'details')[0];
  await page.reload();
  await page.waitForLoadState('networkidle');
  assert.equal(saves(requests, 'details').length, 1);
  assert.equal(await page.locator('#dettaglioForm').isVisible(), false);
  await page.locator('#detailEdit').click();
  await page.locator('#dettaglioForm button[type="submit"]').click();
  await page.waitForFunction(() => document.getElementById('esito').textContent.includes('Richiesta inviata'));
  const update = saves(requests, 'details')[1];
  assert.notEqual(update.headers()['idempotency-key'], original.headers()['idempotency-key']);
  const fields = Object.fromEntries([...update.postData().matchAll(/name="([^"]+)"\r\n\r\n([^]*?)\r\n--/g)].map((match) => [match[1], match[2]]));
  assert.equal(fields.stima_id, '101');
  assert.equal(fields.token, token);
});

for (const change of [{ kind: 'detail' }, { request_id: '44283e82-45df-4ad5-929a-2d7a848d66f2' }, { stima_id: 999 }]) {
  test(`F06 rejects a mismatched receipt ${Object.keys(change)[0]} without claiming success`, async (t) => {
    const { page, requests } = await openPage(t, 'dati_personali.html', { api: (request, url) => {
      if (url.pathname === '/api/salva_stima') {
        const data = envelope(request);
        data.receipt = { ...data.receipt, ...change };
        return { data };
      }
    } });
    await fillPersonal(page);
    await page.locator('#personalForm button').click();
    await page.waitForFunction(() => !document.querySelector('.puntini'));
    assert.ok((await page.locator('#testo-stima').textContent()).includes('NON CONFERMATO'));
    assert.equal(await page.evaluate(() => localStorage.getItem('stimaBase')), null);
    assert.equal(saves(requests).length, 1);
  });
}

test('F06 a durable received receipt resumes only after an explicit click', async (t) => {
  const { page, requests } = await openPage(t, 'dati_personali.html', { api: (request, url) => {
    if (url.pathname === '/api/salva_stima') return { abort: 'failed' };
    if (/^\/api\/submissions\/[^/]+$/.test(url.pathname)) {
      const data = envelope(request, 'quick', 'received', true);
      data.receipt.stima_id = null;
      return { status: 202, data: { receipt: data.receipt } };
    }
    if (url.pathname.endsWith('/resume')) return { data: envelope(request) };
  } });
  await fillPersonal(page);
  await page.locator('#personalForm button').click();
  await page.waitForFunction(() => !document.querySelector('.puntini'));
  await page.locator('#personalForm button').click();
  await page.waitForLoadState('networkidle');
  const recover = page.getByRole('button', { name: 'Recupera la richiesta', exact: true });
  await recover.waitFor({ state: 'visible' });
  assert.equal(requests.filter((request) => request.method() === 'POST').length, 1);
  assert.ok(!(await page.locator('#testo-stima').textContent()).includes('Valore totale'));
  await recover.click();
  await page.waitForFunction(() => document.getElementById('testo-stima').textContent.includes('Valore totale'));
  assert.equal(saves(requests).length, 1);
  assert.equal(requests.filter((request) => new URL(request.url()).pathname.endsWith('/resume')).length, 1);
});

test('F06 a removed request remains closed and requires an intentional new evaluation', async (t) => {
  const { page, requests } = await openPage(t, 'dati_personali.html', { api: (_, url) => {
    if (url.pathname === '/api/salva_stima') return { abort: 'failed' };
    if (/^\/api\/submissions\/[^/]+$/.test(url.pathname)) return { status: 410, data: { detail: 'Synthetic removed request' } };
  } });
  await fillPersonal(page);
  await page.locator('#personalForm button').click();
  await page.waitForFunction(() => !document.querySelector('.puntini'));
  await page.locator('#personalForm button').click();
  await page.waitForLoadState('networkidle');
  assert.ok((await page.locator('#testo-stima').textContent()).includes('rimossa'));
  assert.equal(await page.locator('#newEvaluation').isVisible(), true);
  assert.equal(await page.locator('#personalForm button').isVisible(), false);
  assert.equal(requests.filter((request) => request.method() === 'POST').length, 1);
  assert.equal(new URL(page.url()).searchParams.get('request_id'), requestId);
});
