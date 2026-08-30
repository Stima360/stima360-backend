// STIMA360 OS — immobili.js
// Lista Immobili: ricerca + filtro stato, apertura scheda dettaglio.
// Endpoint reale verificato in property/router.py:11 (prefix /api/property da
// property/router.py:5): GET /api/property/properties?search=&status=&limit=&offset=
// search e' gia' server-side su title/code/address/city (property/repository.py:31),
// status filtra su commercial_status (property/repository.py:32). Stesso endpoint
// gia' usato da property_admin (static/property_admin/assets/app.js:72).
// Nessuna scrittura in questa vista.

import { apiGet } from '../core/api-client.js';
import { navigate } from '../core/router.js';
import { renderTable, bindTableRowClicks, escapeHtml, formatDate } from '../components/st-table.js';

const PAGE_SIZE = 50;

// Valori reali del CHECK constraint properties_status_check (migrations/002_property_01.sql:43).
const STATUS_OPTIONS = ['draft', 'evaluation', 'mandate', 'active', 'reserved', 'under_offer', 'sold', 'withdrawn', 'archived'];

export async function renderImmobili(container) {
  container.innerHTML = `
    <div class="card panel">
      <div class="list-toolbar">
        <input id="immobili-search" class="input" type="search" placeholder="Cerca per titolo, codice, indirizzo o comune…">
        <select id="immobili-status" class="input">
          <option value="">Tutti gli stati</option>
          ${STATUS_OPTIONS.map((s) => `<option value="${s}">${escapeHtml(s)}</option>`).join('')}
        </select>
      </div>
      <div id="immobili-list-area"><p class="muted">Caricamento…</p></div>
      <div id="immobili-pager" class="list-pager"></div>
    </div>
  `;

  const searchInput = container.querySelector('#immobili-search');
  const statusSelect = container.querySelector('#immobili-status');
  const listArea = container.querySelector('#immobili-list-area');
  const pagerArea = container.querySelector('#immobili-pager');

  let offset = 0;
  let debounceHandle = null;

  async function load() {
    listArea.innerHTML = '<p class="muted">Caricamento…</p>';
    pagerArea.innerHTML = '';
    const term = searchInput.value.trim();
    const status = statusSelect.value;
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
    if (term) params.set('search', term);
    if (status) params.set('status', status);

    let items = [];
    try {
      const data = await apiGet(`/api/property/properties?${params.toString()}`);
      items = Array.isArray(data?.items) ? data.items : [];
    } catch (error) {
      listArea.innerHTML = `<div class="error-box">Impossibile caricare gli immobili: ${escapeHtml(error.message)}</div>`;
      return;
    }

    listArea.innerHTML = renderTable(
      [
        { label: 'Immobile', render: (p) => `<strong>${escapeHtml(p.title || `Immobile #${p.id}`)}</strong><br><small class="muted">${escapeHtml(p.code || '—')}</small>` },
        { label: 'Comune', render: (p) => escapeHtml(p.city || '—') },
        { label: 'Tipologia', render: (p) => escapeHtml(p.property_type || '—') },
        { label: 'Stato', render: (p) => escapeHtml(p.commercial_status || '—') },
        { label: 'Prezzo', render: (p) => formatPrice(p.asking_price) },
        { label: 'Aggiornato il', render: (p) => escapeHtml(formatDate(p.updated_at)) },
      ],
      items,
      { emptyMessage: (term || status) ? 'Nessun immobile trovato per questi filtri.' : 'Nessun immobile presente.', onRowClick: true },
    );
    bindTableRowClicks(listArea, (id) => navigate('immobili', [id]));

    pagerArea.innerHTML = `
      <button class="btn" id="immobili-prev" ${offset === 0 ? 'disabled' : ''}>← Precedenti</button>
      <span class="muted">Risultati da ${items.length ? offset + 1 : 0} a ${offset + items.length}</span>
      <button class="btn" id="immobili-next" ${items.length < PAGE_SIZE ? 'disabled' : ''}>Successivi →</button>
    `;
    const prevBtn = pagerArea.querySelector('#immobili-prev');
    const nextBtn = pagerArea.querySelector('#immobili-next');
    if (prevBtn) prevBtn.onclick = () => { offset = Math.max(0, offset - PAGE_SIZE); load(); };
    if (nextBtn) nextBtn.onclick = () => { offset += PAGE_SIZE; load(); };
  }

  searchInput.addEventListener('input', () => {
    offset = 0;
    clearTimeout(debounceHandle);
    debounceHandle = setTimeout(load, 300);
  });
  statusSelect.addEventListener('change', () => { offset = 0; load(); });

  await load();
}

function formatPrice(value) {
  if (value === null || value === undefined) return '—';
  const n = Number(value);
  if (Number.isNaN(n)) return '—';
  return n.toLocaleString('it-IT', { style: 'currency', currency: 'EUR', maximumFractionDigits: 0 });
}
