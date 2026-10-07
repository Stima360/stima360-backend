import assert from 'node:assert/strict';
import { before, after, test } from 'node:test';
import { createRequire } from 'node:module';
import { readFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const { chromium } = require('playwright');
const repo = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const siteDir = process.env.STIMA360_SITE_DIR || resolve(repo, 'site-preview/public');
const origin = 'http://127.0.0.1:4173';
const marker = '<b data-injection="yes">F13 harmless marker</b>';
const oldState = { id: 999, token: 'token-other-estimate', via: 'Other estimate' };
const prefill = {
  id: 101, nome: 'Test', cognome: 'Locale', email: 'test@example.invalid',
  telefono: '3331234567', comune: 'San Benedetto del Tronto', microzona: 'Centro',
  via: 'Via test', civico: '1', tipologia: 'Appartamento', mq: '80', piano: '2',
  locali: '3', bagni: '1', pertinenze: 'Garage', ascensore: 'Sì', anno: '2000',
  stato: 'Buono', posizioneMare: 'Oltre ferrovia', distanzaMare: '500', barrieraMare: '',
  vistaMareYN: 'no', vistaMareDettaglio: '', vistaMare: '', mqGiardino: '', mqGarage: '18',
  mqCantina: '', mqPostoAuto: '', mqTaverna: '', mqSoffitta: '', mqTerrazzo: '',
  numBalconi: '', altroDescrizione: ''
};
const response = {
  id: 101, token: 'token-current-estimate', pdf_url: '',
  detail_url: 'https://www.stima360.it/stima_dettagliata.html?token=token-current-estimate',
  pdf_redirect_url: 'https://www.stima360.it/pdf_redirect.html?token=token-current-estimate',
  stima_uuid: 'STclient-generated', price_exact: 240000, eur_mq_finale: 3000,
  valore_pertinenze: 10000, base_mq: 3000
};
const pdfBytes = Buffer.from('%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n2 0 obj\n<< /Type /Pages /Kids [] /Count 0 >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n');
let browser;

before(async () => {
  const options = { headless: true };
  if (process.env.STIMA360_CHROMIUM_PATH) options.executablePath = process.env.STIMA360_CHROMIUM_PATH;
  browser = await chromium.launch(options);
});
after(async () => { await browser?.close(); });

function multipartFields(request) {
  const body = request.postData() || '';
  const fields = {};
  for (const match of body.matchAll(/name="([^"]+)"\r\n\r\n([^]*?)\r\n--/g)) fields[match[1]] = match[2];
  return fields;
}

async function openPage(t, name, query = {}, options = {}) {
  const context = await browser.newContext({ javaScriptEnabled: options.javaScriptEnabled !== false });
  t.after(() => context.close());
  const requests = [];
  const attemptedUrls = [];
  const allRequests = [];
  const documents = [];
  const page = await context.newPage();
  if (options.clock) await page.clock.install();
  page.setDefaultTimeout(4000);
  await page.addInitScript(({ state, apiBase }) => {
    if (state) localStorage.setItem('stimaBase', JSON.stringify(state));
    if (apiBase) window.STIMA360_PREVIEW_API_BASE = apiBase;
    window.saveDispatches = 0;
    const originalFetch = window.fetch;
    window.fetch = (...args) => {
      if (String(args[0]).includes('/api/salva_stima') && args[1]?.method === 'POST') window.saveDispatches++;
      return originalFetch(...args);
    };
    window.openedUrls = [];
    window.open = (url) => { window.openedUrls.push(String(url)); return null; };
  }, { state: options.state, apiBase: options.apiBase });
  page.on('dialog', (dialog) => dialog.dismiss());
  await context.route('**/*', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    attemptedUrls.push(request.url());
    allRequests.push(request);
    if (url.pathname.startsWith('/api/')) {
      requests.push(request);
      const value = await options.api?.(request, url);
      if (value?.abort) {
        await route.abort(value.abort);
        return;
      }
      const status = value?.status ?? 200;
      if (/^\/api\/stime\/\d+\/pdf(?:\/retry)?$/.test(url.pathname) && !value?.data && value?.rawBody === undefined) {
        await route.fulfill({ status, contentType: value?.contentType ?? 'application/pdf', body: value?.body ?? pdfBytes });
        return;
      }
      let data = value?.data ?? (url.pathname === '/api/prefill' ? prefill
        : url.pathname === '/api/salva_stima' ? response
        : url.pathname === '/api/salva_stima_dettagliata' ? { ok: true } : { count: 0 });
      if (['/api/salva_stima', '/api/salva_stima_dettagliata'].includes(url.pathname) && !data.receipt) {
        const kind = url.pathname === '/api/salva_stima' ? 'quick' : 'detail';
        data = { ...data, receipt: { request_id: request.headers()['idempotency-key'], kind,
          status: 'completed', resumable: false, steps: { database: 'done' }, stima_id: 101,
          detail_id: kind === 'detail' ? 201 : null } };
      }
      await route.fulfill({ status, contentType: value?.contentType ?? 'application/json', body: value?.rawBody ?? JSON.stringify(data) });
      return;
    }
    // No request reaches the public site, analytics, messaging or other external service.
    const basename = url.pathname.split('/').pop() || 'index.html';
    if (['index.html', 'dati_personali.html', 'stima_dettagliata.html', 'pdf_redirect.html', 'preview-config.js', 'submission.js'].includes(basename)) {
      try {
        if (request.isNavigationRequest()) documents.push(request.url());
        const file = basename === 'preview-config.js' && options.releaseConfig
          ? resolve(repo, 'site-preview/release/preview-config.js') : resolve(siteDir, basename);
        await route.fulfill({ contentType: basename.endsWith('.js') ? 'text/javascript' : 'text/html', body: await readFile(file, 'utf8') });
        return;
      } catch { /* A missing preview file remains a real failure. */ }
    }
    await route.abort();
  });
  const search = Object.entries(query).map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(value)}`).join('&');
  await page.goto(`${origin}/${name}?${search}`);
  if (options.waitForIdle !== false) await page.waitForLoadState('networkidle');
  return { page, requests, documents, attemptedUrls, allRequests };
}

async function fillPersonal(page) {
  await page.locator('#nome').fill('Test');
  await page.locator('#cognome').fill('Locale');
  await page.locator('#email').fill('test@example.invalid');
  await page.locator('#telefono').fill('3331234567');
  await page.locator('#privacy').check();
}

async function submitPersonal(page) {
  await fillPersonal(page);
  await page.locator('button[type="submit"]').click();
}

async function submitDetails(page, force = false) {
  if (force) {
    await page.evaluate(() => document.getElementById('dettaglioForm').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));
    await page.waitForLoadState('networkidle');
    return;
  }
  const values = { classe: 'D', riscaldamento: 'Autonomo', condizionatore: 'No', spese_cond_flag: 'No', esposizione: 'Sud', arredo: 'Vuoto', contatto: 'No' };
  for (const [id, value] of Object.entries(values)) await page.locator('#'+id).selectOption(value);
  await page.locator('button[type="submit"]').click();
}

test('personal summary displays query HTML as literal text', async (t) => {
  const { page } = await openPage(t, 'dati_personali.html', { comune: marker, via: marker, pertinenze: 'Altro', altroDescrizione: marker });
  assert.equal(await page.locator('#riepilogoGrid [data-injection]').count(), 0);
  assert.ok((await page.locator('#riepilogoGrid').textContent()).includes(marker));
  assert.ok(await page.locator('#riepilogoGrid strong').count());
});

test('personal result displays address HTML as literal text', async (t) => {
  const { page } = await openPage(t, 'dati_personali.html', { id: 'STclient-generated', comune: marker, via: marker });
  await submitPersonal(page);
  await page.waitForFunction(() => document.getElementById('testo-stima').textContent.includes('Valore totale'));
  assert.equal(await page.locator('#testo-stima [data-injection]').count(), 0);
  assert.ok((await page.locator('#testo-stima').textContent()).includes(marker));
});

test('personal page never persists the client ST id while the save response is pending', async (t) => {
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  const { page, requests } = await openPage(t, 'dati_personali.html', { id: 'STclient-generated' }, {
    state: oldState,
    api: async (_, url) => { if (url.pathname === '/api/salva_stima') await pending; }
  });
  t.after(() => release());
  const started = page.waitForRequest('**/api/salva_stima');
  await submitPersonal(page);
  await started;
  assert.equal(requests.filter((request) => new URL(request.url()).pathname === '/api/salva_stima').length, 1);
  const stored = await page.evaluate(() => JSON.parse(localStorage.getItem('stimaBase')));
  assert.notEqual(stored?.id, 'STclient-generated');
  release();
});

test('personal page persists the server id and token after a valid save response', async (t) => {
  const { page } = await openPage(t, 'dati_personali.html', { id: 'STclient-generated' });
  await submitPersonal(page);
  await page.waitForFunction(() => document.getElementById('testo-stima').textContent.includes('Valore totale'));
  const stored = await page.evaluate(() => JSON.parse(localStorage.getItem('stimaBase')));
  assert.equal(stored.id, 101);
  assert.equal(stored.token, 'token-current-estimate');
});

test('personal save without a token fails closed and stores no unauthorised detail state', async (t) => {
  const { page } = await openPage(t, 'dati_personali.html', { id: 'STclient-generated' }, { api: (_, url) => url.pathname === '/api/salva_stima' ? { data: { ...response, token: undefined } } : undefined });
  await submitPersonal(page);
  await page.waitForFunction(() => !document.querySelector('.puntini'));
  assert.equal(await page.evaluate(() => localStorage.getItem('stimaBase')), null);
  assert.equal(new URL(page.url()).pathname, '/dati_personali.html');
  assert.ok(!(await page.locator('#testo-stima').textContent()).includes('Valore totale'));
});

test('personal save opens the local tokenised PDF redirect returned by the server', async (t) => {
  const data = { ...response, pdf_url: origin+'/report.pdf', pdf_redirect_url: 'https://www.stima360.it/pdf_redirect.html?token=token-current-estimate&pdf='+encodeURIComponent(origin+'/report.pdf') };
  const { page } = await openPage(t, 'dati_personali.html', { id: 'STclient-generated' }, { api: (_, url) => url.pathname === '/api/salva_stima' ? { data } : undefined });
  await submitPersonal(page);
  await page.waitForURL('**/pdf_redirect.html?*');
  const url = new URL(page.url());
  assert.equal(url.origin, origin);
  assert.equal(url.searchParams.get('token'), 'token-current-estimate');
  assert.equal(url.searchParams.has('pdf'), false);
});

test('PDF redirect uses its URL token instead of another estimate in localStorage', async (t) => {
  const { page } = await openPage(t, 'pdf_redirect.html', { token: 'token-current-estimate' }, { state: oldState });
  await page.evaluate(() => vaiAStimaDettagliataConParametri());
  const opened = await page.evaluate(() => window.openedUrls);
  assert.equal(opened.length, 1);
  const url = new URL(opened[0], origin);
  assert.equal(url.origin, origin);
  assert.equal(url.pathname, '/stima_dettagliata.html');
  assert.equal(url.searchParams.get('token'), 'token-current-estimate');
  assert.equal(url.searchParams.has('id'), false);
});

test('PDF redirect without a URL token cannot reopen a stored estimate', async (t) => {
  const { page } = await openPage(t, 'pdf_redirect.html', {}, { state: oldState });
  await page.evaluate(() => vaiAStimaDettagliataConParametri());
  assert.deepEqual(await page.evaluate(() => window.openedUrls), []);
  assert.equal(await page.locator('#stimaPro').isDisabled(), true);
});

test('detailed summary displays prefill HTML as literal text while preserving bold labels', async (t) => {
  const unsafe = { ...prefill, via: marker, nome: marker, mqGarage: marker, email: marker };
  const { page } = await openPage(t, 'stima_dettagliata.html', { token: 'valid-token' }, { api: (_, url) => url.pathname === '/api/prefill' ? { data: unsafe } : undefined });
  await page.waitForFunction(() => document.getElementById('infoImmobile').textContent.includes('Indirizzo'));
  assert.equal(await page.locator('#infoImmobile [data-injection]').count(), 0);
  assert.ok((await page.locator('#infoImmobile').textContent()).includes(marker));
  assert.ok(await page.locator('#infoImmobile b').count());
});

test('detailed summary also displays fallback pertinenze as literal text', async (t) => {
  const { page } = await openPage(t, 'stima_dettagliata.html', { token: 'valid-token' }, { api: (_, url) => url.pathname === '/api/prefill' ? { data: { ...prefill, mqGarage: '', pertinenze: marker } } : undefined });
  await page.waitForFunction(() => document.getElementById('infoImmobile').textContent.includes('Pertinenze'));
  assert.equal(await page.locator('#infoImmobile [data-injection]').count(), 0);
  assert.ok((await page.locator('#infoImmobile').textContent()).includes(marker));
});

test('detailed request without a token never uses query data or submits an orphan', async (t) => {
  const { page, requests } = await openPage(t, 'stima_dettagliata.html', { id: 'STorphan', via: marker, comune: 'Query fallback' });
  await submitDetails(page, true);
  assert.equal(requests.filter((request) => request.method() === 'POST').length, 0);
  assert.equal(await page.locator('button[type="submit"]').isDisabled(), true);
  assert.ok(!(await page.locator('#infoImmobile').textContent()).includes('Query fallback'));
});

for (const status of [401, 404, 500]) {
  test(`detailed request with rejected prefill ${status} remains disabled and cannot submit`, async (t) => {
    const { page, requests } = await openPage(t, 'stima_dettagliata.html', { token: 'expired-or-invalid', id: 'STorphan', via: 'Query fallback' }, { api: (_, url) => url.pathname === '/api/prefill' ? { status, data: { detail: 'Token non valido' } } : undefined });
    await submitDetails(page, true);
    assert.equal(requests.filter((request) => request.method() === 'POST').length, 0);
    assert.equal(await page.locator('button[type="submit"]').isDisabled(), true);
    assert.ok(!(await page.locator('#infoImmobile').textContent()).includes('Query fallback'));
  });
}

test('detailed request with malformed prefill id fails closed', async (t) => {
  const { page, requests } = await openPage(t, 'stima_dettagliata.html', { token: 'valid-token' }, { api: (_, url) => url.pathname === '/api/prefill' ? { data: { ...prefill, id: 'STorphan' } } : undefined });
  await submitDetails(page, true);
  assert.equal(requests.filter((request) => request.method() === 'POST').length, 0);
});

test('valid detailed request sends URL token and prefill server id despite forged query and hidden id', async (t) => {
  const { page, requests } = await openPage(t, 'stima_dettagliata.html', { token: 'valid-token', id: 'STorphan', stima_id: '999', via: 'Query fallback' });
  await page.waitForFunction(() => document.getElementById('infoImmobile').textContent.includes('Via test'));
  await page.locator('input[name="id"]').evaluate((input) => { input.value = 'STforged-hidden'; });
  await submitDetails(page);
  await page.waitForFunction(() => document.getElementById('esito').textContent.includes('Richiesta inviata'));
  const request = requests.find((request) => request.method() === 'POST');
  const fields = multipartFields(request);
  assert.equal(fields.token, 'valid-token');
  assert.equal(fields.stima_id, '101');
  assert.ok(!fields.id || fields.id === '101');
  assert.equal(fields.via, 'Via test');
});

test('preview submits only to the configured local backend', async (t) => {
  const { page, requests } = await openPage(t, 'dati_personali.html', { id: 'STclient-generated' }, { apiBase: 'http://127.0.0.1:8123' });
  await submitPersonal(page);
  await page.waitForFunction(() => document.getElementById('testo-stima').textContent.includes('Valore totale'));
  const request = requests.find((request) => request.method() === 'POST');
  assert.equal(new URL(request.url()).origin, 'http://127.0.0.1:8123');
});

test('preview rejects an external backend configuration before any submit', async (t) => {
  const { page, requests } = await openPage(t, 'dati_personali.html', { id: 'STclient-generated' }, { apiBase: 'https://stima360-backend.onrender.com' });
  await submitPersonal(page);
  await page.waitForFunction(() => !document.querySelector('.puntini'));
  assert.equal(requests.filter((request) => request.method() === 'POST').length, 0);
});

for (const status of [401, 404]) {
  test(`PDF redirect rejects prefill ${status} despite a stored estimate`, async (t) => {
    const { page } = await openPage(t, 'pdf_redirect.html', { token: 'expired-or-invalid' }, { state: oldState, api: (_, url) => url.pathname === '/api/prefill' ? { status, data: { detail: 'Token non valido' } } : undefined });
    await page.evaluate(() => vaiAStimaDettagliataConParametri());
    assert.deepEqual(await page.evaluate(() => window.openedUrls), []);
    assert.equal(await page.locator('#stimaPro').isDisabled(), true);
  });
}

for (const changes of [{ id: 'STclient-generated' }, { detail_url: 'https://www.stima360.it/stima_dettagliata.html?token=token-other-estimate' }]) {
  test(`personal save rejects an inconsistent detail response ${Object.keys(changes)[0]}`, async (t) => {
    const { page } = await openPage(t, 'dati_personali.html', { id: 'STclient-generated' }, { api: (_, url) => url.pathname === '/api/salva_stima' ? { data: { ...response, ...changes } } : undefined });
    await submitPersonal(page);
    await page.waitForFunction(() => !document.querySelector('.puntini'));
    assert.equal(await page.evaluate(() => localStorage.getItem('stimaBase')), null);
    assert.ok(!(await page.locator('#testo-stima').textContent()).includes('Valore totale'));
  });
}

test('detailed request disables the form when a previously valid token is rejected at submit with 403', async (t) => {
  const { page } = await openPage(t, 'stima_dettagliata.html', { token: 'valid-then-expired' }, { api: (_, url) => url.pathname === '/api/salva_stima_dettagliata' ? { status: 403, data: { detail: 'Token non valido o scaduto' } } : undefined });
  const failureDialog = page.waitForEvent('dialog');
  await submitDetails(page);
  await failureDialog;
  assert.equal(await page.locator('button[type="submit"]').isDisabled(), true);
  assert.equal(await page.locator('#esito').isVisible(), true);
  assert.ok((await page.locator('#esito').textContent()).includes('Link non valido o scaduto'));
});

async function openReleaseConfig(t) {
  const context = await browser.newContext();
  t.after(() => context.close());
  await context.route('**/*', (route) => route.abort());
  const page = await context.newPage();
  const source = await readFile(resolve(repo, 'site-preview/release/preview-config.js'), 'utf8').catch((error) => {
    if (error.code === 'ENOENT') return '';
    throw error;
  });
  await page.setContent('<html><head><script>window.analyticsCalls=[];window.fbq=(...args)=>window.analyticsCalls.push(args);</script></head><body></body></html>');
  if (source) await page.addScriptTag({ content: source });
  assert.equal(await page.evaluate(() => typeof window.Stima360Preview), 'object', 'release helper contract is available');
  return page;
}

test('release configuration resolves the explicit public API and matching tokenised site URL', async (t) => {
  const page = await openReleaseConfig(t);
  const result = await page.evaluate(() => ({
    api: Stima360Preview.apiUrl('/api/prefill?t=valid-token'),
    detail: Stima360Preview.pageUrl('https://www.stima360.it/stima_dettagliata.html?token=valid-token&id=untrusted', 'stima_dettagliata.html', 'valid-token')
  }));
  assert.equal(result.api, 'https://stima360-backend.onrender.com/api/prefill?t=valid-token');
  assert.equal(result.detail, 'https://www.stima360.it/stima_dettagliata.html?token=valid-token');
});

test('release configuration rejects unexpected site origin, page, token and API origin', async (t) => {
  const page = await openReleaseConfig(t);
  const result = await page.evaluate(() => {
    const calls = [
      () => Stima360Preview.pageUrl('https://example.invalid/stima_dettagliata.html?token=valid-token', 'stima_dettagliata.html', 'valid-token'),
      () => Stima360Preview.pageUrl('https://www.stima360.it/index.html?token=valid-token', 'stima_dettagliata.html', 'valid-token'),
      () => Stima360Preview.pageUrl('https://www.stima360.it/stima_dettagliata.html?token=other', 'stima_dettagliata.html', 'valid-token'),
      () => Stima360Preview.apiUrl('https://example.invalid/api/salva_stima')
    ];
    return calls.map((call) => { try { call(); return false; } catch { return true; } });
  });
  assert.deepEqual(result, [true, true, true, true]);
});

test('F07 release configuration binds PDF access to the protected backend and preserves analytics', async (t) => {
  const page = await openReleaseConfig(t);
  assert.equal(await page.evaluate(() => typeof Stima360Preview.pdfUrl), 'function');
  const result = await page.evaluate(() => {
    fbq('track', 'Lead');
    return {
      pdf: Stima360Preview.pdfUrl(101, 'valid-token'),
      arbitraryPdfHelper: typeof Stima360Preview.localHttpUrl,
      analytics: window.analyticsCalls
    };
  });
  assert.equal(result.pdf, 'https://stima360-backend.onrender.com/api/stime/101/pdf?t=valid-token');
  assert.equal(result.arbitraryPdfHelper, 'undefined');
  assert.deepEqual(result.analytics, [['track', 'Lead']]);
});

const r1Failures = [
  ['network', { abort: 'failed' }],
  ['invalid JSON', { rawBody: '{invalid-json' }],
  ['missing token', { data: { ...response, token: undefined } }],
  ['HTTP non-OK', { status: 503, data: { detail: 'Synthetic unavailable' } }],
  ['missing server id', { data: { ...response, id: undefined } }],
  ['inconsistent capability URL', { data: { ...response, detail_url: 'https://www.stima360.it/stima_dettagliata.html?token=other-token' } }]
];

async function personalValues(page) {
  return page.evaluate(() => Object.fromEntries(['nome', 'cognome', 'email', 'telefono', 'privacy'].map((id) => {
    const input = document.getElementById(id);
    return [id, input.type === 'checkbox' ? input.checked : input.value];
  })));
}

async function buttonAppearance(button) {
  return button.evaluate((node) => {
    const properties = ['backgroundImage', 'backgroundSize', 'animationName', 'borderRadius', 'borderStyle', 'color', 'fontFamily', 'fontSize', 'fontWeight', 'padding', 'position', 'overflow'];
    const result = { button: Object.fromEntries(properties.map((name) => [name, getComputedStyle(node)[name]])) };
    for (const pseudo of ['::before', '::after']) {
      const css = getComputedStyle(node, pseudo);
      result[pseudo] = Object.fromEntries(['content', 'backgroundImage', 'borderRadius', 'opacity', 'position', 'filter'].map((name) => [name, css[name]]));
    }
    return result;
  });
}

for (const [kind, failure] of r1Failures) {
  test(`R1 ${kind} leaves an uncertain outcome and the same styled reload-only button`, async (t) => {
    const { page, requests } = await openPage(t, 'dati_personali.html', { id: 'STclient-generated' }, { clock: true, api: (_, url) => url.pathname === '/api/salva_stima' ? failure : undefined });
    await fillPersonal(page);
    const values = await personalValues(page);
    const button = page.locator('#personalForm button');
    const originalButton = await button.elementHandle();
    const appearance = await buttonAppearance(button);
    if (kind === 'network') await button.screenshot({ path: '/tmp/stima360-r1-normal-button.png', animations: 'disabled' });
    await button.click();
    await page.waitForFunction(() => !document.querySelector('.puntini'));
    const text = await page.locator('#testo-stima').textContent();
    assert.ok(text.includes('NON CONFERMATO'));
    assert.ok(/già.*salvat/i.test(text));
    assert.ok(/email/i.test(text) && /WhatsApp/i.test(text));
    assert.equal(await button.isVisible(), true);
    assert.equal(await button.getAttribute('type'), 'button');
    assert.equal(await button.textContent(), 'Ricarica la pagina');
    assert.equal(await page.evaluate((node) => node === document.querySelector('#personalForm button'), originalButton), true);
    assert.deepEqual(await buttonAppearance(button), appearance);
    assert.deepEqual(await personalValues(page), values);
    assert.equal(await page.evaluate(() => localStorage.getItem('stimaBase')), null);
    assert.equal(requests.filter((request) => request.method() === 'POST').length, 1);
    if (kind === 'network') await button.screenshot({ path: '/tmp/stima360-r1-recovery-button.png', animations: 'disabled' });
  });
}

test('R1 recovery button reloads only after the explicit click and sends no new POST', async (t) => {
  const { page, requests, documents } = await openPage(t, 'dati_personali.html', { id: 'STclient-generated' }, { clock: true, api: (_, url) => url.pathname === '/api/salva_stima' ? { abort: 'failed' } : undefined });
  await submitPersonal(page);
  await page.waitForFunction(() => !document.querySelector('.puntini'));
  await page.clock.fastForward(5000);
  assert.equal(documents.length, 1);
  const navigation = page.waitForEvent('domcontentloaded');
  await page.getByRole('button', { name: 'Ricarica la pagina', exact: true }).click();
  await navigation;
  await page.waitForLoadState('networkidle');
  assert.equal(documents.length, 2);
  assert.equal(requests.filter((request) => request.method() === 'POST').length, 1);
  assert.equal(await page.evaluate(() => window.saveDispatches), 0);
});

test('R1 HTTP non-OK never reloads automatically past the former 1800ms timer', async (t) => {
  const { page, documents } = await openPage(t, 'dati_personali.html', { id: 'STclient-generated' }, { clock: true, api: (_, url) => url.pathname === '/api/salva_stima' ? { status: 503, data: { detail: 'Synthetic unavailable' } } : undefined });
  await submitPersonal(page);
  await page.waitForFunction(() => !document.querySelector('.puntini'));
  await page.clock.fastForward(5000);
  await page.waitForLoadState('networkidle');
  assert.equal(documents.length, 1);
  assert.equal(await page.locator('#personalForm button').getAttribute('type'), 'button');
});

test('R1 pending save blocks Enter and programmatic duplicate submits', async (t) => {
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  const { page, requests } = await openPage(t, 'dati_personali.html', { id: 'STclient-generated' }, { api: async (_, url) => { if (url.pathname === '/api/salva_stima') await pending; } });
  t.after(() => release());
  const started = page.waitForRequest('**/api/salva_stima');
  await submitPersonal(page);
  await started;
  await page.locator('#email').press('Enter');
  await page.evaluate(() => document.getElementById('personalForm').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));
  assert.equal(await page.evaluate(() => window.saveDispatches), 1);
  assert.equal(requests.filter((request) => request.method() === 'POST').length, 1);
  release();
});

for (const state of ['success', 'uncertain']) {
  test(`R1 ${state} save blocks Enter and programmatic duplicate submits`, async (t) => {
    const { page, requests } = await openPage(t, 'dati_personali.html', { id: 'STclient-generated' }, { api: (_, url) => state === 'uncertain' && url.pathname === '/api/salva_stima' ? { abort: 'failed' } : undefined });
    await submitPersonal(page);
    await page.waitForFunction(() => !document.querySelector('.puntini'));
    await page.locator('#email').press('Enter');
    await page.evaluate(() => document.getElementById('personalForm').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));
    assert.equal(await page.evaluate(() => window.saveDispatches), 1);
    assert.equal(requests.filter((request) => request.method() === 'POST').length, 1);
  });
}

test('R1 validation failures consume no dispatch attempts before one valid save', async (t) => {
  const { page } = await openPage(t, 'dati_personali.html', { id: 'STclient-generated' });
  await fillPersonal(page);
  for (const invalid of ['privacy', 'email', 'phone']) {
    await page.locator('#privacy').setChecked(invalid !== 'privacy');
    await page.locator('#email').fill(invalid === 'email' ? 'invalid-email' : 'test@example.invalid');
    await page.locator('#telefono').fill(invalid === 'phone' ? '123' : '3331234567');
    const dialog = page.waitForEvent('dialog');
    await page.evaluate(() => document.getElementById('personalForm').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));
    await dialog;
  }
  assert.equal(await page.evaluate(() => window.saveDispatches), 0);
  await submitPersonal(page);
  assert.equal(await page.evaluate(() => window.saveDispatches), 1);
});

test('R1 an invalid current token does not persist new identity or authorise an old stored estimate', async (t) => {
  const { page, documents } = await openPage(t, 'dati_personali.html', { id: 'STclient-generated' }, { state: oldState, api: (_, url) => url.pathname === '/api/salva_stima' ? { data: { ...response, token: undefined } } : undefined });
  await submitPersonal(page);
  await page.waitForFunction(() => !document.querySelector('.puntini'));
  assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('stimaBase'))), oldState);
  const dialog = page.waitForEvent('dialog');
  await page.evaluate(() => vaiStimaDettagliata());
  await dialog;
  assert.equal(documents.length, 1);
  assert.equal(await page.locator('#personalForm button').getAttribute('type'), 'button');
});

function pdfRequests(requests) {
  return requests.filter((request) => /^\/api\/stime\/\d+\/pdf(?:\/retry)?$/.test(new URL(request.url()).pathname));
}

async function verifyPdfDownload(page) {
  const link = page.locator('#pdfDownload');
  await link.waitFor({ state: 'visible' });
  assert.equal(await link.isVisible(), true);
  assert.match(await link.getAttribute('href'), /^blob:http:\/\/127\.0\.0\.1:4173\//);
  assert.equal(await link.getAttribute('download'), 'stima_101.pdf');
  const downloading = page.waitForEvent('download');
  await link.click();
  const download = await downloading;
  assert.equal(download.suggestedFilename(), 'stima_101.pdf');
  assert.deepEqual(await readFile(await download.path()), pdfBytes);
}

for (const forgedPdf of ['https://example.invalid/other-stima.pdf', origin + '/stima_999.pdf', '/reports/stima_999.pdf']) {
  test(`F07 redirect ignores caller PDF ${forgedPdf} and downloads only the token-authorised server id`, async (t) => {
    const { page, requests, attemptedUrls } = await openPage(t, 'pdf_redirect.html', { token: 'token-current-estimate', id: '999', stima_id: '999', pdf: forgedPdf }, { state: oldState, clock: true });
    await page.clock.fastForward(1000);
    await page.waitForLoadState('networkidle');
    assert.equal(attemptedUrls.includes(new URL(forgedPdf, origin).href), false);
    const pdf = pdfRequests(requests);
    assert.equal(pdf.length, 1);
    assert.equal(pdf[0].method(), 'GET');
    const url = new URL(pdf[0].url());
    assert.equal(url.origin, 'http://127.0.0.1:8000');
    assert.equal(url.pathname, '/api/stime/101/pdf');
    assert.equal(url.search, '?t=token-current-estimate');
    assert.equal(await page.locator('.stima360-btn').first().isDisabled(), false);
    await verifyPdfDownload(page);
    assert.equal(new URL(page.url()).pathname, '/pdf_redirect.html');
    assert.equal(requests.filter((request) => request.method() === 'POST').length, 0);
  });
}

const rejectedPdfPrefills = [
  ['missing token', {}, undefined],
  ['expired token', { token: 'expired-token' }, { status: 403, data: { detail: marker } }],
  ['invalid token', { token: 'invalid-token' }, { status: 401, data: { detail: marker } }],
  ['missing estimate', { token: 'other-token' }, { status: 404, data: { detail: marker } }],
  ['client-generated id', { token: 'valid-token' }, { data: { ...prefill, id: 'STclient-generated' } }],
  ['missing id', { token: 'valid-token' }, { data: { ...prefill, id: undefined } }],
  ['invalid JSON', { token: 'valid-token' }, { rawBody: '{invalid-json' }],
  ['network failure', { token: 'valid-token' }, { abort: 'failed' }]
];
for (const [kind, query, failure] of rejectedPdfPrefills) {
  test(`F07 ${kind} never fetches a PDF or enables details despite query PDF and stored identity`, async (t) => {
    const { page, requests, attemptedUrls } = await openPage(t, 'pdf_redirect.html', { ...query, pdf: origin + '/stima_999.pdf', id: '999' }, { state: oldState, clock: true, api: (_, url) => url.pathname === '/api/prefill' ? failure : undefined });
    await page.clock.fastForward(1000);
    await page.waitForLoadState('networkidle');
    assert.equal(new URL(page.url()).pathname, '/pdf_redirect.html');
    assert.equal(attemptedUrls.includes(origin + '/stima_999.pdf'), false);
    assert.equal(pdfRequests(requests).length, 0);
    assert.equal(await page.locator('.stima360-btn').first().isDisabled(), true);
    assert.equal(await page.locator('#pdfDownload').isVisible(), false);
    assert.equal(await page.locator('#pdfRetry').isVisible(), false);
    assert.ok((await page.locator('#pdfStatus').textContent()).includes('Link non valido o scaduto'));
    assert.equal(await page.locator('[data-injection]').count(), 0);
  });
}

test('F07 PDF 503 exposes a styled explicit recovery button without automatic POST', async (t) => {
  const { page, requests } = await openPage(t, 'pdf_redirect.html', { token: 'valid-token' }, { clock: true, api: (_, url) => url.pathname === '/api/stime/101/pdf' ? { status: 503, data: { detail: marker } } : undefined });
  const recovery = page.getByRole('button', { name: 'Recupera il PDF', exact: true });
  assert.equal(await recovery.isVisible(), true);
  assert.equal(await recovery.getAttribute('type'), 'button');
  assert.deepEqual(await buttonAppearance(recovery), await buttonAppearance(page.locator('.stima360-btn').first()));
  assert.equal(await page.locator('#pdfDownload').isVisible(), false);
  assert.ok((await page.locator('#pdfStatus').textContent()).includes('PDF non disponibile'));
  assert.equal(await page.locator('[data-injection]').count(), 0);
  await page.clock.fastForward(5000);
  assert.equal(pdfRequests(requests).length, 1);
  assert.equal(requests.filter((request) => request.method() === 'POST').length, 0);
  await page.screenshot({ path: '/tmp/stima360-f07-pdf-recovery.png', fullPage: true, animations: 'disabled' });
});

for (const succeeds of [false, true]) {
  test(`F07 explicit PDF retry ${succeeds ? 'succeeds' : 'fails safely'} on the same authorised server id without saving another estimate`, async (t) => {
    const { page, requests } = await openPage(t, 'pdf_redirect.html', { token: 'token-current-estimate', id: '999' }, { state: oldState, api: (_, url) => {
      if (url.pathname === '/api/stime/101/pdf') return { status: 503, data: { detail: 'Synthetic PDF unavailable' } };
      if (url.pathname.endsWith('/pdf/retry') && !succeeds) return { status: 503, data: { detail: marker } };
    } });
    const retry = page.getByRole('button', { name: 'Recupera il PDF', exact: true });
    assert.equal(await retry.isVisible(), true);
    await retry.click();
    await page.waitForLoadState('networkidle');
    const pdf = pdfRequests(requests);
    assert.equal(pdf.length, 2);
    assert.equal(pdf[1].method(), 'POST');
    assert.equal(new URL(pdf[1].url()).pathname, '/api/stime/101/pdf/retry');
    assert.equal(new URL(pdf[1].url()).search, '?t=token-current-estimate');
    assert.equal(requests.filter((request) => request.method() === 'POST' && new URL(request.url()).pathname !== '/api/stime/101/pdf/retry').length, 0);
    if (succeeds) {
      await verifyPdfDownload(page);
      assert.equal(await retry.isVisible(), false);
    } else {
      await page.waitForFunction(() => !document.getElementById('pdfRetry').disabled);
      assert.equal(await retry.isVisible(), true);
      assert.equal(await page.locator('#pdfDownload').isVisible(), false);
      assert.equal(await page.locator('[data-injection]').count(), 0);
    }
  });
}

for (const name of ['pdf_redirect.html', 'stima_dettagliata.html']) {
  for (const javaScriptEnabled of [true, false]) {
    test(`F07 private token page ${name} sends no Facebook request with ${javaScriptEnabled ? 'JavaScript' : 'noscript'}`, async (t) => {
      const { page, allRequests } = await openPage(t, name, { token: 'synthetic-sensitive-capability' }, { releaseConfig: true, javaScriptEnabled });
      const tracking = allRequests.filter((request) => /(^|\.)(facebook\.com|facebook\.net)$/.test(new URL(request.url()).hostname));
      assert.deepEqual(tracking.map((request) => request.url()), []);
      assert.equal(await page.locator('script[src*="facebook"], noscript img[src*="facebook"]').count(), 0);
      const externalResources = allRequests.filter((request) => !['127.0.0.1', 'stima360-backend.onrender.com'].includes(new URL(request.url()).hostname));
      assert.ok(externalResources.every((request) => !request.headers().referer));
    });
  }
  test(`F07 private token page ${name} removes Pixel and declares no-referrer before external resources`, async () => {
    const source = await readFile(resolve(siteDir, name), 'utf8');
    const head = source.match(/<head>([^]*?)<\/head>/)[1];
    assert.doesNotMatch(head, /facebook|fbq\s*\(/i);
    const policy = head.match(/<meta\s+name="referrer"\s+content="no-referrer"\s*\/?\s*>/);
    assert.ok(policy);
    assert.ok(policy.index < head.indexOf('https://'));
  });
}

for (const expired of [false, true]) {
  test(`F07 BFCache return ${expired ? 'rejects an expired token' : 'revalidates prefill and recreates a fresh private PDF blob'}`, async (t) => {
    let prefillCalls = 0;
    const { page, requests } = await openPage(t, 'pdf_redirect.html', { token: 'valid-token' }, { api: (_, url) => {
      if (url.pathname === '/api/prefill' && ++prefillCalls > 1 && expired) return { status: 403, data: { detail: marker } };
    } });
    const previousBlob = await page.locator('#pdfDownload').getAttribute('href');
    assert.ok(previousBlob);
    await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true })));
    assert.equal(await page.locator('#pdfDownload').isVisible(), false);
    await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true })));
    if (expired) {
      await page.waitForFunction(() => document.getElementById('pdfStatus').textContent.includes('Link non valido o scaduto'));
      assert.equal(await page.locator('#stimaPro').isDisabled(), true);
      assert.equal(await page.locator('#pdfDownload').isVisible(), false);
      assert.equal(await page.locator('#pdfRetry').isVisible(), false);
      assert.equal(pdfRequests(requests).length, 1);
    } else {
      await page.locator('#pdfDownload').waitFor({ state: 'visible' });
      assert.notEqual(await page.locator('#pdfDownload').getAttribute('href'), previousBlob);
      assert.equal(pdfRequests(requests).length, 2);
    }
    assert.equal(prefillCalls, 2);
    assert.equal(requests.filter((request) => request.method() === 'POST').length, 0);
  });
}

test('F07 BFCache return discards a previous pending PDF response after token revalidation fails', async (t) => {
  let release;
  let started;
  let prefillCalls = 0;
  const pending = new Promise((resolve) => { release = resolve; });
  const oldStarted = new Promise((resolve) => { started = resolve; });
  const { page, requests } = await openPage(t, 'pdf_redirect.html', { token: 'valid-then-expired' }, { waitForIdle: false, api: async (_, url) => {
    if (url.pathname === '/api/prefill' && ++prefillCalls > 1) return { status: 403, data: { detail: marker } };
    if (url.pathname === '/api/stime/101/pdf') { started(); await pending; }
  } });
  t.after(() => release());
  await oldStarted;
  await page.evaluate(() => {
    window.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true }));
    window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true }));
  });
  await page.waitForFunction(() => document.getElementById('pdfStatus').textContent.includes('Link non valido o scaduto'));
  const completed = page.waitForResponse('**/api/stime/101/pdf?*');
  release();
  await completed;
  await page.waitForLoadState('networkidle');
  assert.equal(await page.locator('#pdfDownload').isVisible(), false);
  assert.equal(await page.locator('#pdfDownload').getAttribute('href'), null);
  assert.equal(await page.locator('#stimaPro').isDisabled(), true);
  assert.equal(prefillCalls, 2);
  assert.equal(requests.filter((request) => request.method() === 'POST').length, 0);
});

test('F07 pending PDF recovery rejects duplicate clicks and programmatic retry events', async (t) => {
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  const { page, requests } = await openPage(t, 'pdf_redirect.html', { token: 'valid-token' }, { api: async (_, url) => {
    if (url.pathname === '/api/stime/101/pdf') return { status: 503, data: {} };
    if (url.pathname.endsWith('/pdf/retry')) await pending;
  } });
  t.after(() => release());
  const retry = page.getByRole('button', { name: 'Recupera il PDF', exact: true });
  assert.equal(await retry.isVisible(), true);
  const started = page.waitForRequest('**/api/stime/101/pdf/retry?*');
  await retry.click();
  await started;
  assert.equal(await retry.isDisabled(), true);
  await retry.evaluate((button) => {
    button.click();
    button.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
  assert.equal(requests.filter((request) => request.method() === 'POST').length, 1);
  release();
  await page.locator('#pdfDownload').waitFor({ state: 'visible' });
  assert.equal(await page.locator('#pdfDownload').isVisible(), true);
});

for (const status of [401, 403, 404]) {
  test(`F07 PDF auth rejection ${status} after valid prefill exposes no blob or recovery operation`, async (t) => {
    const { page } = await openPage(t, 'pdf_redirect.html', { token: 'valid-then-expired' }, { api: (_, url) => url.pathname === '/api/stime/101/pdf' ? { status, data: { detail: marker } } : undefined });
    assert.equal(await page.locator('.stima360-btn').first().isDisabled(), true);
    assert.equal(await page.locator('#pdfDownload').isVisible(), false);
    assert.equal(await page.locator('#pdfRetry').isVisible(), false);
    assert.ok((await page.locator('#pdfStatus').textContent()).includes('Link non valido o scaduto'));
  });
}

test('F07 a non-PDF response never becomes a downloadable blob', async (t) => {
  const { page } = await openPage(t, 'pdf_redirect.html', { token: 'valid-token' }, { api: (_, url) => url.pathname === '/api/stime/101/pdf' ? { data: { detail: marker } } : undefined });
  assert.equal(await page.locator('#pdfDownload').isVisible(), false);
  assert.equal(await page.locator('#pdfStatus').count(), 1);
  assert.ok((await page.locator('#pdfStatus').textContent()).includes('PDF non disponibile'));
  assert.equal(await page.locator('[data-injection]').count(), 0);
});

for (const configuration of ['preview', 'release']) {
  test(`F07 ${configuration} helpers remove caller PDF and bind private URL to a positive server id and token`, async (t) => {
    const page = configuration === 'release' ? await openReleaseConfig(t) : (await openPage(t, 'pdf_redirect.html')).page;
    assert.equal(await page.evaluate(() => typeof Stima360Preview.pdfUrl), 'function');
    const result = await page.evaluate(() => {
      const invalidCalls = [
        () => Stima360Preview.pdfUrl('101', 'valid-token'),
        () => Stima360Preview.pdfUrl(0, 'valid-token'),
        () => Stima360Preview.pdfUrl(101, ''),
        () => Stima360Preview.pdfUrl(101, 'valid-token', '/reports/stima_999.pdf'),
        () => Stima360Preview.apiUrl('https://example.invalid/api/stime/101/pdf?t=valid-token'),
        () => Stima360Preview.apiUrl('/reports/stima_999.pdf')
      ];
      return {
        redirect: Stima360Preview.pageUrl('https://www.stima360.it/pdf_redirect.html?token=valid-token&pdf=https%3A%2F%2Fexample.invalid%2Fother.pdf&id=999', 'pdf_redirect.html', 'valid-token'),
        pdf: Stima360Preview.pdfUrl(101, 'valid-token'),
        retry: Stima360Preview.pdfUrl(101, 'valid-token', true),
        rejected: invalidCalls.map((call) => { try { call(); return false; } catch { return true; } })
      };
    });
    const base = configuration === 'release' ? 'https://stima360-backend.onrender.com' : 'http://127.0.0.1:8000';
    assert.equal(new URL(result.redirect).search, '?token=valid-token');
    assert.equal(result.pdf, base + '/api/stime/101/pdf?t=valid-token');
    assert.equal(result.retry, base + '/api/stime/101/pdf/retry?t=valid-token');
    assert.deepEqual(result.rejected, [true, true, true, true, true, true]);
  });
}
