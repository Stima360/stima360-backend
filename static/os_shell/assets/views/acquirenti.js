// STIMA360 OS — acquirenti.js
// Lista Acquirenti = lista operativa delle RICHIESTE BUY (buy_requests), non
// una seconda anagrafica persone. L'identita' resta il CORE Contact.
//
// Endpoint reale verificato in buy/router.py:16 (prefix /api/buy da
// buy/router.py:5): GET /api/buy/requests?search=&status=&limit=&offset=
// buy/repository.py:list_requests fa gia' JOIN su contacts (c.display_name,
// c.email, c.phone) e calcola locations_count/typologies_count/features_count/
// matches_count/open_tasks_count direttamente in SQL: NESSUNA chiamata N+1 per
// riga necessaria per mostrare il nome del Contatto o un riepilogo criteri.
// Router protetto da require_admin a livello di app (main.py: dependencies=
// [Depends(require_admin)] su buy_router), come tutti gli altri domini gia'
// usati dalla App Shell.
//
// Creazione nuova richiesta: POST /api/buy/requests (buy/router.py:14,
// schema BuyRequestCreate in buy/schemas.py). Selezione Contatto tramite
// GET /api/core/contacts?search= (core/router.py:40-47, gia' verificato in P1),
// mai un campo "ID contatto" digitabile. lead_id NON viene mai richiesto
// all'operatore: e' opzionale in BuyRequestCreate (lead_id: int|None=None) e
// viene semplicemente omesso dal payload — vedi note in acquirente-dettaglio.js
// e nel report finale.
//
// Zona e tipologia (aggiunte in chiusura P3): dopo la creazione della
// richiesta, se compilati, vengono aggiunti tramite gli endpoint gia'
// esistenti POST /api/buy/requests/{id}/locations (buy/router.py:46, schema
// LocationCreate — qui usato con location_type='municipality') e
// POST /api/buy/requests/{id}/typologies (buy/router.py:50, schema
// TypologyCreate). Sono scritture SECONDARIE ed opzionali: un loro fallimento
// non annulla la richiesta gia' creata e viene mostrato esplicitamente
// all'operatore (vedi gestione errori nel submit handler).

import { apiGet, apiPost } from '../core/api-client.js';
import { navigate } from '../core/router.js';
import { renderTable, bindTableRowClicks, renderBadge, escapeHtml, formatDate, formatDateTime } from '../components/st-table.js';

const PAGE_SIZE = 50;

// Valori reali di BUY_STATUSES (buy/enums.py). "archived" e' escluso dal filtro:
// buy/repository.py:list_requests applica sempre "b.archived_at IS NULL", quindi
// una richiesta archiviata non compare mai in questa lista (stesso comportamento
// della BUY Admin legacy, il cui BUY_STATUSES lato JS esclude 'archived' dal form).
const STATUS_OPTIONS = ['draft', 'active', 'paused', 'satisfied', 'closed'];
const STATUS_LABELS = { draft: 'Bozza', active: 'Attiva', paused: 'In pausa', satisfied: 'Soddisfatta', closed: 'Chiusa', archived: 'Archiviata' };
const PRIORITY_LABELS = { low: 'Bassa', normal: 'Normale', high: 'Alta', urgent: 'Urgente' };
const URGENCY_LABELS = { exploratory: 'Esplorativa', flexible: 'Flessibile', within_6_months: 'Entro 6 mesi', within_3_months: 'Entro 3 mesi', immediate: 'Immediata' };

export async function renderAcquirenti(container) {
  container.innerHTML = `
    <div class="card panel">
      <div class="list-toolbar">
        <input id="acquirenti-search" class="input" type="search" placeholder="Cerca per titolo, nome, email o telefono del contatto…">
        <select id="acquirenti-status" class="input">
          <option value="">Tutti gli stati</option>
          ${STATUS_OPTIONS.map((s) => `<option value="${s}">${escapeHtml(STATUS_LABELS[s] || s)}</option>`).join('')}
        </select>
        <button type="button" id="acquirenti-new" class="btn primary">+ Nuova richiesta</button>
      </div>
      <div id="acquirenti-list-area"><p class="muted">Caricamento…</p></div>
      <div id="acquirenti-pager" class="list-pager"></div>
    </div>
    <dialog id="new-request-dialog" class="modal"></dialog>
  `;

  const searchInput = container.querySelector('#acquirenti-search');
  const statusSelect = container.querySelector('#acquirenti-status');
  const listArea = container.querySelector('#acquirenti-list-area');
  const pagerArea = container.querySelector('#acquirenti-pager');
  const dialogEl = container.querySelector('#new-request-dialog');

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
      const data = await apiGet(`/api/buy/requests?${params.toString()}`);
      items = Array.isArray(data?.items) ? data.items : [];
    } catch (error) {
      listArea.innerHTML = `<div class="error-box">Impossibile caricare le richieste: ${escapeHtml(error.message)}</div>`;
      return;
    }

    listArea.innerHTML = renderTable(
      [
        { label: 'Contatto', render: (r) => `<strong>${escapeHtml(r.contact_name || `Contatto #${r.contact_id}`)}</strong><br><small class="muted">${escapeHtml([r.contact_phone, r.contact_email].filter(Boolean).join(' · ') || '—')}</small>` },
        { label: 'Richiesta', render: (r) => `${escapeHtml(r.title || `Richiesta #${r.id}`)}<br>${renderBadge(STATUS_LABELS[r.status] || r.status || '—', statusTone(r.status))}` },
        { label: 'Budget', render: (r) => formatBudgetRange(r) },
        { label: 'Criteri', render: (r) => `<span class="muted">${escapeHtml(r.locations_count ?? 0)} zone · ${escapeHtml(r.typologies_count ?? 0)} tipologie · ${escapeHtml(r.features_count ?? 0)} caratteristiche</span>` },
        { label: 'Prossima azione', render: (r) => renderNextAction(r) },
        { label: 'Aggiornata il', render: (r) => escapeHtml(formatDate(r.updated_at)) },
      ],
      items,
      { emptyMessage: (term || status) ? 'Nessuna richiesta trovata per questi filtri.' : 'Nessuna richiesta BUY presente.', onRowClick: true },
    );
    bindTableRowClicks(listArea, (id) => navigate('acquirenti', [id]));

    pagerArea.innerHTML = `
      <button class="btn" id="acquirenti-prev" ${offset === 0 ? 'disabled' : ''}>← Precedenti</button>
      <span class="muted">Risultati da ${items.length ? offset + 1 : 0} a ${offset + items.length}</span>
      <button class="btn" id="acquirenti-next" ${items.length < PAGE_SIZE ? 'disabled' : ''}>Successivi →</button>
    `;
    const prevBtn = pagerArea.querySelector('#acquirenti-prev');
    const nextBtn = pagerArea.querySelector('#acquirenti-next');
    if (prevBtn) prevBtn.onclick = () => { offset = Math.max(0, offset - PAGE_SIZE); load(); };
    if (nextBtn) nextBtn.onclick = () => { offset += PAGE_SIZE; load(); };
  }

  searchInput.addEventListener('input', () => {
    offset = 0;
    clearTimeout(debounceHandle);
    debounceHandle = setTimeout(load, 300);
  });
  statusSelect.addEventListener('change', () => { offset = 0; load(); });

  container.querySelector('#acquirenti-new').addEventListener('click', () => {
    openNewRequestDialog(dialogEl);
  });

  await load();
}

// --- Nuova richiesta: dialog con ricerca Contatto + criteri minimi ---------

function openNewRequestDialog(dialogEl) {
  let selectedContact = null;
  let searchDebounce = null;
  let submitting = false;

  dialogEl.innerHTML = `
    <form id="new-request-form">
      <h2 style="margin-top:0">Nuova richiesta BUY</h2>

      <div class="form-field">
        <label>Contatto</label>
        <div id="contact-picker">
          <input type="search" id="contact-search-input" class="input" placeholder="Cerca per nome, telefono o email…" autocomplete="off">
          <div id="contact-search-results"></div>
        </div>
        <div id="contact-selected" hidden></div>
      </div>

      <div class="form-field">
        <label>Titolo richiesta</label>
        <input type="text" id="request-title" class="input" maxlength="200" placeholder="Es. Appartamento centro città" required>
      </div>

      <div class="form-grid-2">
        <div class="form-field">
          <label>Stato</label>
          <select id="request-status" class="input">
            ${STATUS_OPTIONS.map((s) => `<option value="${s}" ${s === 'draft' ? 'selected' : ''}>${escapeHtml(STATUS_LABELS[s] || s)}</option>`).join('')}
          </select>
        </div>
        <div class="form-field">
          <label>Urgenza</label>
          <select id="request-urgency" class="input">
            ${Object.keys(URGENCY_LABELS).map((u) => `<option value="${u}" ${u === 'flexible' ? 'selected' : ''}>${escapeHtml(URGENCY_LABELS[u])}</option>`).join('')}
          </select>
        </div>
      </div>

      <h3 class="section-title">Zona e tipologia</h3>
      <div class="form-grid-2">
        <div class="form-field"><label>Comune *</label><input type="text" id="request-municipality" class="input" maxlength="120" placeholder="Es. Milano" required></div>
        <div class="form-field"><label>Tipo immobile *</label><input type="text" id="request-property-type" class="input" maxlength="80" placeholder="Es. apartment" required></div>
      </div>

      <h3 class="section-title">Budget (€)</h3>
      <div class="form-grid-3">
        <div class="form-field"><label>Minimo</label><input type="number" id="budget-min" class="input" min="0" step="1000"></div>
        <div class="form-field"><label>Target</label><input type="number" id="budget-target" class="input" min="0" step="1000"></div>
        <div class="form-field"><label>Massimo</label><input type="number" id="budget-max" class="input" min="0" step="1000"></div>
      </div>

      <h3 class="section-title">Superficie (mq)</h3>
      <div class="form-grid-3">
        <div class="form-field"><label>Minima</label><input type="number" id="surface-min" class="input" min="0" step="1"></div>
        <div class="form-field"><label>Target</label><input type="number" id="surface-target" class="input" min="0" step="1"></div>
        <div class="form-field"><label>Massima</label><input type="number" id="surface-max" class="input" min="0" step="1"></div>
      </div>

      <h3 class="section-title">Minimi</h3>
      <div class="form-grid-3">
        <div class="form-field"><label>Locali</label><input type="number" id="rooms-min" class="input" min="0" step="1"></div>
        <div class="form-field"><label>Camere</label><input type="number" id="bedrooms-min" class="input" min="0" step="1"></div>
        <div class="form-field"><label>Bagni</label><input type="number" id="bathrooms-min" class="input" min="0" step="1"></div>
      </div>

      <div class="form-field">
        <label>Note</label>
        <textarea id="request-notes" class="input" rows="3"></textarea>
      </div>

      <div id="new-request-error" class="field-error"></div>

      <div class="modal-actions">
        <button type="button" id="new-request-cancel" class="btn ghost">Annulla</button>
        <button type="submit" id="new-request-submit" class="btn primary" disabled>Crea richiesta</button>
      </div>
    </form>
  `;

  const form = dialogEl.querySelector('#new-request-form');
  const searchInput = dialogEl.querySelector('#contact-search-input');
  const resultsEl = dialogEl.querySelector('#contact-search-results');
  const selectedEl = dialogEl.querySelector('#contact-selected');
  const submitBtn = dialogEl.querySelector('#new-request-submit');
  const errorEl = dialogEl.querySelector('#new-request-error');
  const titleInput = dialogEl.querySelector('#request-title');
  const municipalityInput = dialogEl.querySelector('#request-municipality');
  const propertyTypeInput = dialogEl.querySelector('#request-property-type');

  function updateSubmitState() {
    submitBtn.disabled = submitting
      || !selectedContact
      || !titleInput.value.trim()
      || !municipalityInput.value.trim()
      || !propertyTypeInput.value.trim();
  }

  function selectContact(contact) {
    selectedContact = contact;
    resultsEl.innerHTML = '';
    searchInput.value = '';
    searchInput.hidden = true;
    selectedEl.hidden = false;
    selectedEl.innerHTML = `
      <div class="selected-contact-card">
        <div>
          <strong>${escapeHtml(contactLabel(contact))}</strong><br>
          <small class="muted">${escapeHtml([contact.phone, contact.email].filter(Boolean).join(' · ') || '—')}</small>
        </div>
        <button type="button" class="btn ghost" id="contact-change-btn">Cambia</button>
      </div>
    `;
    selectedEl.querySelector('#contact-change-btn').addEventListener('click', () => {
      selectedContact = null;
      selectedEl.hidden = true;
      selectedEl.innerHTML = '';
      searchInput.hidden = false;
      searchInput.value = '';
      searchInput.focus();
      updateSubmitState();
    });
    updateSubmitState();
  }

  searchInput.addEventListener('input', () => {
    clearTimeout(searchDebounce);
    const term = searchInput.value.trim();
    if (!term) { resultsEl.innerHTML = ''; return; }
    searchDebounce = setTimeout(async () => {
      resultsEl.innerHTML = '<p class="muted">Ricerca…</p>';
      try {
        const data = await apiGet(`/api/core/contacts?search=${encodeURIComponent(term)}&limit=10`);
        const contacts = Array.isArray(data?.items) ? data.items : [];
        resultsEl.innerHTML = contacts.length
          ? `<div class="list">${contacts.map((c) => `<div class="list-item contact-result" data-contact-id="${escapeHtml(c.id)}" style="cursor:pointer"><span><strong>${escapeHtml(contactLabel(c))}</strong><br><small class="muted">${escapeHtml([c.phone, c.email].filter(Boolean).join(' · ') || '—')}</small></span></div>`).join('')}</div>`
          : '<p class="muted">Nessun contatto trovato.</p>';
        resultsEl.querySelectorAll('.contact-result').forEach((el) => {
          el.addEventListener('click', () => {
            const contact = contacts.find((c) => String(c.id) === el.dataset.contactId);
            if (contact) selectContact(contact);
          });
        });
      } catch (error) {
        resultsEl.innerHTML = `<div class="error-box">Errore nella ricerca: ${escapeHtml(error.message)}</div>`;
      }
    }, 300);
  });

  titleInput.addEventListener('input', updateSubmitState);
  municipalityInput.addEventListener('input', updateSubmitState);
  propertyTypeInput.addEventListener('input', updateSubmitState);

  const cancelBtn = dialogEl.querySelector('#new-request-cancel');
  cancelBtn.onclick = () => dialogEl.close();

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!selectedContact || submitting) return;
    const title = titleInput.value.trim();
    if (!title) return;

    submitting = true;
    updateSubmitState();
    errorEl.innerHTML = '';

    const payload = {
      contact_id: selectedContact.id,
      title,
      status: dialogEl.querySelector('#request-status').value,
      urgency: dialogEl.querySelector('#request-urgency').value,
    };
    addNumberIfPresent(payload, 'budget_min', dialogEl.querySelector('#budget-min').value);
    addNumberIfPresent(payload, 'budget_target', dialogEl.querySelector('#budget-target').value);
    addNumberIfPresent(payload, 'budget_max', dialogEl.querySelector('#budget-max').value);
    addNumberIfPresent(payload, 'surface_min', dialogEl.querySelector('#surface-min').value);
    addNumberIfPresent(payload, 'surface_target', dialogEl.querySelector('#surface-target').value);
    addNumberIfPresent(payload, 'surface_max', dialogEl.querySelector('#surface-max').value);
    addIntegerIfPresent(payload, 'rooms_min', dialogEl.querySelector('#rooms-min').value);
    addIntegerIfPresent(payload, 'bedrooms_min', dialogEl.querySelector('#bedrooms-min').value);
    addIntegerIfPresent(payload, 'bathrooms_min', dialogEl.querySelector('#bathrooms-min').value);
    const notes = dialogEl.querySelector('#request-notes').value.trim();
    if (notes) payload.notes = notes;

    const municipality = dialogEl.querySelector('#request-municipality').value.trim();
    const propertyType = dialogEl.querySelector('#request-property-type').value.trim();

    let created;
    try {
      created = await apiPost('/api/buy/requests', payload);
    } catch (error) {
      errorEl.textContent = error.message || 'Errore nella creazione della richiesta.';
      submitting = false;
      updateSubmitState();
      return;
    }

    // La richiesta principale e' creata: da qui in avanti NON viene mai
    // cancellata. Le due scritture secondarie (zona/tipologia) sono opzionali
    // e riusano solo endpoint gia' esistenti (buy/router.py:46,50). Un loro
    // fallimento non deve nascondere ne' annullare la creazione gia' avvenuta.
    const criteriaFailures = [];
    if (municipality) {
      try {
        await apiPost(`/api/buy/requests/${created.id}/locations`, { location_type: 'municipality', municipality });
      } catch (error) {
        criteriaFailures.push(`Comune "${municipality}": ${error.message || 'errore sconosciuto'}`);
      }
    }
    if (propertyType) {
      try {
        await apiPost(`/api/buy/requests/${created.id}/typologies`, { property_type: propertyType });
      } catch (error) {
        criteriaFailures.push(`Tipo immobile "${propertyType}": ${error.message || 'errore sconosciuto'}`);
      }
    }

    if (!criteriaFailures.length) {
      dialogEl.close();
      navigate('acquirenti', [created.id]);
      return;
    }

    // Scrittura secondaria fallita: la richiesta resta creata, l'errore viene
    // mostrato esplicitamente e l'operatore decide quando proseguire.
    errorEl.innerHTML = `<div class="error-box">Richiesta creata (#${escapeHtml(created.id)}), ma alcuni criteri non sono stati salvati:<br>${criteriaFailures.map((m) => escapeHtml(m)).join('<br>')}</div>`;
    submitBtn.hidden = true;
    cancelBtn.textContent = 'Vai alla richiesta creata';
    cancelBtn.onclick = () => {
      dialogEl.close();
      navigate('acquirenti', [created.id]);
    };
  });

  updateSubmitState();
  dialogEl.showModal();
}

function addNumberIfPresent(payload, key, rawValue) {
  if (rawValue === '' || rawValue === null || rawValue === undefined) return;
  const n = Number(rawValue);
  if (!Number.isNaN(n)) payload[key] = n;
}

function addIntegerIfPresent(payload, key, rawValue) {
  if (rawValue === '' || rawValue === null || rawValue === undefined) return;
  const n = Number.parseInt(rawValue, 10);
  if (!Number.isNaN(n)) payload[key] = n;
}

function contactLabel(contact) {
  if (contact.display_name) return contact.display_name;
  if (contact.contact_type === 'company') return contact.company_name || `Contatto #${contact.id}`;
  const parts = [contact.first_name, contact.last_name].filter(Boolean);
  return parts.length ? parts.join(' ') : `Contatto #${contact.id}`;
}

function formatBudgetRange(r) {
  const parts = [r.budget_min, r.budget_target, r.budget_max].map((v) => (v === null || v === undefined ? null : formatCompactPrice(v)));
  if (!parts[0] && !parts[1] && !parts[2]) return '<span class="muted">—</span>';
  if (parts[1] && !parts[0] && !parts[2]) return escapeHtml(parts[1]);
  return escapeHtml([parts[0] || '—', parts[2] || '—'].join(' – '));
}

function formatCompactPrice(value) {
  const n = Number(value);
  if (Number.isNaN(n)) return null;
  return n.toLocaleString('it-IT', { style: 'currency', currency: 'EUR', maximumFractionDigits: 0 });
}

function renderNextAction(r) {
  if (!r.next_action_at && !r.next_action_note) return '<span class="muted">—</span>';
  const when = r.next_action_at ? escapeHtml(formatDateTime(r.next_action_at)) : '';
  const note = r.next_action_note ? escapeHtml(r.next_action_note) : '';
  return [when, note].filter(Boolean).join('<br>');
}

function statusTone(status) {
  if (['active', 'satisfied'].includes(status)) return 'ok';
  if (status === 'paused') return 'warn';
  if (['closed', 'archived'].includes(status)) return 'gray';
  return 'gray';
}
