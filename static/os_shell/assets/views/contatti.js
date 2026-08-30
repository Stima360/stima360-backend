// STIMA360 OS — contatti.js
// Lista Contatti: ricerca (nome/telefono/email) + apertura scheda dettaglio.
// Endpoint reale usato (verificato in core/router.py:40-47, prefix /api/core
// da core/router.py:21): GET /api/core/contacts?search=&limit=&offset=
// La ricerca è gia' server-side su display_name, first_name, last_name,
// company_name, email_normalized, phone_normalized (core/repository.py:45-63)
// quindi un solo campo di ricerca copre nome/telefono/email.
// Nessuna scrittura in questa vista.

import { apiGet } from '../core/api-client.js';
import { navigate } from '../core/router.js';
import { renderTable, bindTableRowClicks, escapeHtml, formatDate } from '../components/st-table.js';

const PAGE_SIZE = 50;

export async function renderContatti(container) {
  container.innerHTML = `
    <div class="card panel">
      <div class="list-toolbar">
        <input id="contatti-search" class="input" type="search" placeholder="Cerca per nome, telefono o email…">
      </div>
      <div id="contatti-list-area"><p class="muted">Caricamento…</p></div>
      <div id="contatti-pager" class="list-pager"></div>
    </div>
  `;

  const searchInput = container.querySelector('#contatti-search');
  const listArea = container.querySelector('#contatti-list-area');
  const pagerArea = container.querySelector('#contatti-pager');

  let offset = 0;
  let debounceHandle = null;

  async function load() {
    listArea.innerHTML = '<p class="muted">Caricamento…</p>';
    pagerArea.innerHTML = '';
    const term = searchInput.value.trim();
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
    if (term) params.set('search', term);

    let items = [];
    try {
      const data = await apiGet(`/api/core/contacts?${params.toString()}`);
      items = Array.isArray(data?.items) ? data.items : [];
    } catch (error) {
      listArea.innerHTML = `<div class="error-box">Impossibile caricare i contatti: ${escapeHtml(error.message)}</div>`;
      return;
    }

    listArea.innerHTML = renderTable(
      [
        { label: 'Nome', render: (c) => `<strong>${escapeHtml(c.display_name || contactFallbackName(c))}</strong>` },
        { label: 'Email', render: (c) => escapeHtml(c.email || '—') },
        { label: 'Telefono', render: (c) => escapeHtml(c.phone || '—') },
        { label: 'Stato', render: (c) => escapeHtml(c.status || '—') },
        { label: 'Creato il', render: (c) => escapeHtml(formatDate(c.created_at)) },
      ],
      items,
      { emptyMessage: term ? 'Nessun contatto trovato per questa ricerca.' : 'Nessun contatto presente.', onRowClick: true },
    );
    bindTableRowClicks(listArea, (id) => navigate('contatti', [id]));

    pagerArea.innerHTML = `
      <button class="btn" id="contatti-prev" ${offset === 0 ? 'disabled' : ''}>← Precedenti</button>
      <span class="muted">Risultati da ${items.length ? offset + 1 : 0} a ${offset + items.length}</span>
      <button class="btn" id="contatti-next" ${items.length < PAGE_SIZE ? 'disabled' : ''}>Successivi →</button>
    `;
    const prevBtn = pagerArea.querySelector('#contatti-prev');
    const nextBtn = pagerArea.querySelector('#contatti-next');
    if (prevBtn) prevBtn.onclick = () => { offset = Math.max(0, offset - PAGE_SIZE); load(); };
    if (nextBtn) nextBtn.onclick = () => { offset += PAGE_SIZE; load(); };
  }

  searchInput.addEventListener('input', () => {
    offset = 0;
    clearTimeout(debounceHandle);
    debounceHandle = setTimeout(load, 300);
  });

  await load();
}

function contactFallbackName(contact) {
  if (contact.contact_type === 'company') return contact.company_name || `Contatto #${contact.id}`;
  const parts = [contact.first_name, contact.last_name].filter(Boolean);
  return parts.length ? parts.join(' ') : `Contatto #${contact.id}`;
}
