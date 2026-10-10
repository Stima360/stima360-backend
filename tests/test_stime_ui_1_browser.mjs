// Browser locale con API fittizie: nessuna richiesta al sito o ai servizi veri.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const { chromium } = require('playwright');
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../static/os_shell');
const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml' };
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (!url.pathname.startsWith('/os')) { res.writeHead(404).end(); return; }
  const relative = url.pathname === '/os' || url.pathname === '/os/'
    ? '/index.html' : url.pathname.slice(3);
  const filename = path.resolve(root, `.${relative}`);
  if (!filename.startsWith(`${root}${path.sep}`) && filename !== root) { res.writeHead(403).end(); return; }
  try {
    const body = await fs.readFile(filename);
    res.writeHead(200, { 'Content-Type': types[path.extname(filename)] || 'application/octet-stream' }).end(body);
  } catch { res.writeHead(404).end(); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const base = `http://127.0.0.1:${server.address().port}`;

const first = {
  id: 10, data: '2026-10-01T10:00:00', nome: '<img src=x onerror=alert(1)>',
  cognome: 'Rossi', comune: 'Alba Adriatica', via: 'Via Lago', mq: 82,
  tipologia: 'Appartamento', detail_id: 32, detail_data: '2026-10-05T10:00:00',
  contact_id: 100, lead_id: 110, property_id: 120, pdf_status: 'ready',
  mqgarage: 17, pertinenze: 'garage', classe: 'B', price_exact: '185000',
};
const second = {
  id: 11, data: '2026-10-02T10:00:00', nome: 'Ada', cognome: 'Verdi',
  comune: 'Giulianova', via: 'Via Uno', mq: 65, tipologia: 'Casa',
  detail_id: null, contact_id: null, property_id: null, pdf_status: 'pending',
};
let browser;
try {
  browser = await chromium.launch({ headless: true,
    ...(process.env.STIMA_UI_CHROMIUM ? { executablePath: process.env.STIMA_UI_CHROMIUM } : {}) });
  for (const width of [1280, 390, 320]) {
    const page = await browser.newPage({ viewport: { width, height: 800 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/api/**', async route => {
      const u = new URL(route.request().url());
      let body;
      if (u.pathname === '/api/operator-auth/me') {
        body = { user_id: 6, agency_id: 1, role: 'agency_owner', is_platform_admin: false };
      } else if (u.pathname === '/api/crm/stime/10') {
        body = first;
      } else if (u.pathname === '/api/crm/stime/11') {
        body = second;
      } else if (u.pathname === '/api/crm/stime') {
        let items = [first, second];
        if (u.searchParams.get('view') === 'detailed') items = [first];
        if (u.searchParams.get('view') === 'base') items = [second];
        if (u.searchParams.get('search') === 'pagina') {
          items = Array.from({ length: 31 }, (_, index) => ({ ...second, id: 1000 + index, nome: `Pagina ${index}` }));
        } else if (u.searchParams.get('search')) {
          items = items.filter(x => `${x.nome} ${x.cognome}`.toLowerCase().includes(u.searchParams.get('search').toLowerCase()));
        }
        const offset = Number(u.searchParams.get('offset'));
        body = { items: items.slice(offset, offset + 30), total: items.length,
          has_more: offset + 30 < items.length, stats: { total: 2, detailed: 1 } };
      } else {
        await route.fulfill({ status: 404, contentType: 'application/json', body: '{}' });
        return;
      }
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
    });
    await page.goto(`${base}/os/#/stime`);
    try {
      await page.locator('.stime-card').first().waitFor({ timeout: 5000 });
    } catch (error) {
      console.error('BROWSER DIAGNOSTIC', width, errors, await page.locator('body').innerText());
      throw error;
    }
    assert.equal(await page.locator('#nav .nav-item').count(), await page.locator('#nav .nav-icon').count());
    assert.equal(await page.locator('.stime-card').count(), 2);
    assert.match(await page.locator('.stime-estimate').first().innerText(), /185\.000/);
    assert.equal(await page.locator('.stime-card img').count(), 0);
    assert.equal(await page.locator('.stime-card a[href="#/contatti/100"]').count(), 1);
    assert.equal(await page.locator('.stime-card a[href="#/immobili/120"]').count(), 1);
    assert.equal(await page.locator('.stime-card a[href="/api/crm/stime/10/pdf"]').count(), 1);
    assert.equal(await page.locator('.stime-card a[href="/api/crm/stime/11/pdf"]').count(), 0);
    await page.locator('select[aria-label="Tipo di stima"]').selectOption('base');
    await page.waitForFunction(() => document.querySelectorAll('.stime-card').length === 1);
    await page.locator('select[aria-label="Tipo di stima"]').selectOption('all');
    await page.waitForFunction(() => document.querySelectorAll('.stime-card').length === 2);
    await page.locator('input[aria-label="Cerca stime"]').fill('Ada');
    await page.waitForFunction(() => document.querySelectorAll('.stime-card').length === 1);
    await page.locator('input[aria-label="Cerca stime"]').fill('pagina');
    await page.waitForFunction(() => document.querySelectorAll('.stime-card').length === 30);
    await page.getByRole('button', { name: 'Successive →' }).click();
    await page.waitForFunction(() => document.querySelectorAll('.stime-card').length === 1);
    assert.match(await page.locator('.list-pager').innerText(), /31–31 di 31/);
    await page.locator('[data-route="stime-dettagliate"]').click();
    await page.waitForURL('**/#/stime-dettagliate');
    await page.waitForFunction(() => document.querySelector('.stime-card .stime-name-link')?.textContent.includes('<img'));
    assert.equal(await page.locator('.stime-card').count(), 1);
    await page.locator('.stime-name-link').click();
    await page.locator('.stime-detail-grid').waitFor();
    assert.equal(await page.getByText('17', { exact: true }).count(), 1);
    assert.equal(await page.locator('.stime-detail-card').count(), 6);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
    assert.equal(overflow, false, `horizontal overflow at ${width}px`);
    assert.deepEqual(errors, [], `browser errors at ${width}px`);
    await page.close();
  }
  console.log('PASS: desktop + smartphone 390/320, menu SVG, archivio unico, filtri, ricerca, paginazione, dettagli, link CRM/PDF, XSS, nessun overflow');
} finally {
  if (browser) await browser.close();
  server.close();
}
