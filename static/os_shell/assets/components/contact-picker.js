// STIMA360 OS — contact-picker.js
// Ricerca+selezione di un contatto CORE gia' esistente (GET /api/core/contacts
// ?search=, core/router.py:40-47), stesso pattern gia' presente in
// immobile-dettaglio.js (openContactLinkDialog/openVisitDialog), acquirenti.js
// (openNewRequestDialog) e abbinamenti.js (createEntityPicker). Estratto qui
// (P25.1) perche' i nuovi dialog Attivita'/Task lo avrebbero altrimenti
// duplicato una quarta e quinta volta nello stesso file (attivita.js). Le tre
// implementazioni gia' esistenti NON vengono toccate (nessun refactor di file
// gia' funzionanti fuori dallo scope stretto di P25.1).

import { apiGet } from '../core/api-client.js';
import { escapeHtml } from './st-table.js';

export function contactLabel(contact) {
  if (!contact) return '';
  if (contact.display_name) return contact.display_name;
  if (contact.contact_type === 'company') return contact.company_name || `Contatto #${contact.id}`;
  const parts = [contact.first_name, contact.last_name].filter(Boolean);
  return parts.length ? parts.join(' ') : `Contatto #${contact.id}`;
}

// rootEl: contenitore vuoto in cui montare input+risultati+selezione.
// onChange(contact|null): richiamato ad ogni selezione/deselezione.
// Ritorna { get selected() }.
export function createContactPicker(rootEl, { onChange, placeholder = 'Cerca per nome, email o telefono…' } = {}) {
  let selected = null;
  let debounceHandle = null;

  rootEl.innerHTML = `
    <input type="search" class="input contact-picker-search" placeholder="${escapeHtml(placeholder)}" autocomplete="off">
    <div class="contact-picker-results"></div>
    <div class="contact-picker-selected" hidden></div>
  `;
  const searchInput = rootEl.querySelector('.contact-picker-search');
  const resultsEl = rootEl.querySelector('.contact-picker-results');
  const selectedEl = rootEl.querySelector('.contact-picker-selected');

  function select(contact) {
    selected = contact;
    resultsEl.innerHTML = '';
    searchInput.value = '';
    searchInput.hidden = true;
    selectedEl.hidden = false;
    selectedEl.innerHTML = `
      <div class="selected-contact-card">
        <div><strong>${escapeHtml(contactLabel(contact))}</strong><br><small class="muted">${escapeHtml([contact.phone, contact.email].filter(Boolean).join(' · ') || '—')}</small></div>
        <button type="button" class="btn ghost contact-picker-change">Cambia</button>
      </div>
    `;
    selectedEl.querySelector('.contact-picker-change').addEventListener('click', () => {
      selected = null;
      selectedEl.hidden = true;
      selectedEl.innerHTML = '';
      searchInput.hidden = false;
      searchInput.value = '';
      searchInput.focus();
      onChange(null);
    });
    onChange(contact);
  }

  searchInput.addEventListener('input', () => {
    clearTimeout(debounceHandle);
    const term = searchInput.value.trim();
    if (!term) { resultsEl.innerHTML = ''; return; }
    debounceHandle = setTimeout(async () => {
      resultsEl.innerHTML = '<p class="muted">Ricerca…</p>';
      try {
        const data = await apiGet(`/api/core/contacts?search=${encodeURIComponent(term)}&limit=10`);
        const contacts = Array.isArray(data?.items) ? data.items : [];
        resultsEl.innerHTML = contacts.length
          ? `<div class="list">${contacts.map((c, i) => `<div class="list-item contact-picker-result" data-index="${i}" style="cursor:pointer"><span><strong>${escapeHtml(contactLabel(c))}</strong><br><small class="muted">${escapeHtml([c.phone, c.email].filter(Boolean).join(' · ') || '—')}</small></span></div>`).join('')}</div>`
          : '<p class="muted">Nessun contatto trovato.</p>';
        resultsEl.querySelectorAll('.contact-picker-result').forEach((el) => {
          el.addEventListener('click', () => select(contacts[Number(el.dataset.index)]));
        });
      } catch (error) {
        resultsEl.innerHTML = `<div class="error-box">Errore nella ricerca: ${escapeHtml(error.message)}</div>`;
      }
    }, 300);
  });

  return { get selected() { return selected; } };
}
