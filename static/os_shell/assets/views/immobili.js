// STIMA360 OS — immobili.js
// Lista Immobili: ricerca + filtro stato, apertura scheda dettaglio.
// Endpoint reale verificato in property/router.py:11 (prefix /api/property da
// property/router.py:5): GET /api/property/properties?search=&status=&limit=&offset=
// search e' gia' server-side su title/code/address/city (property/repository.py:31),
// status filtra su commercial_status (property/repository.py:32). Stesso endpoint
// gia' usato da property_admin (static/property_admin/assets/app.js:72).
//
// P14: creazione immobile aggiunta a questa vista ("+ Nuovo immobile").
// Contratto backend verificato in fase di audit P14, nessun endpoint nuovo:
//   POST /api/property/properties (property/router.py:11, status_code=201)
//   schema PropertyCreate (property/schemas.py) — extra="forbid".
// Unico campo realmente obbligatorio: title (min_length=1). property_type e
// commercial_status hanno default backend ("apartment"/"draft") ma vengono
// comunque inviati esplicitamente (stesso comportamento del form legacy
// static/property_admin/assets/app.js:78-108, propertyForm/buildPropertyPayload).
// Tutti gli altri campi sono opzionali e vengono omessi dal payload se vuoti
// (nessun invio di stringa vuota/NaN — mai un valore inventato).
// Enum reali (property/enums.py): PROPERTY_TYPES, PROPERTY_STATUSES (STATUS_OPTIONS
// gia' esistente, riusato), PROPERTY_CLASSES = {A,B,C}. Vincoli reali
// (migrations/002_property_01.sql): title NOT NULL, code UNIQUE (409 se duplicato,
// gestito da property/repository.py:19 -> ConflictError, mostrato in UI come
// errore reale, non mascherato), surface_sqm >= 0, asking_price >= 0.
// property_type/commercial_status non validi -> 400 ValidationError dal
// root_validator di PropertyCreate (property/schemas.py) mostrato in UI.
//
// Distinzione verificata: "property" (record principale, questa vista/tabella
// properties), "listing"/"mandate" (NON sono entita' separate: mandate_type/
// mandate_start/mandate_end sono semplici colonne di properties, incarico e
// annuncio non hanno tabelle proprie), "property_contacts" (tabella separata,
// relazione N:M con contacts, MAI scritta da questo endpoint — POST
// /api/property/properties non tocca property_contacts, property_leads, ne'
// crea contatti/match/BUY/SALE: unico side effect verificato in
// property/repository.py:create_property e' l'inserimento automatico di una
// riga in property_price_history se asking_price e' valorizzato e in
// property_status_history se commercial_status/classification sono valorizzati
// — pura cronologia interna del record appena creato, nessuna nuova entita'
// di dominio). Questa vista non crea mai incarichi, proprietari, contatti,
// listing/pubblicazioni, match, BUY o SALE.
//
// Post-creazione: stesso pattern gia' in produzione in acquirenti.js
// (openNewRequestDialog) — dialog.close() poi navigate('immobili', [id]),
// che il router (main.js:51-52) instrada direttamente su
// renderImmobileDettaglio, aprendo subito la scheda del nuovo immobile senza
// window.location.reload(). La lista, quando rivisitata, effettua sempre una
// nuova GET (nessuna cache client-side), quindi risulta gia' aggiornata senza
// bisogno di refresh manuale.

import { apiGet, apiPost } from '../core/api-client.js';
import { navigate } from '../core/router.js';
import { renderTable, bindTableRowClicks, escapeHtml, formatDate } from '../components/st-table.js';

const PAGE_SIZE = 50;

// Valori reali del CHECK constraint properties_status_check (migrations/002_property_01.sql:43).
const STATUS_OPTIONS = ['draft', 'evaluation', 'mandate', 'active', 'reserved', 'under_offer', 'sold', 'withdrawn', 'archived'];

// Valori reali di PROPERTY_TYPES (property/enums.py) e PROPERTY_CLASSES (property/enums.py),
// usati solo nel form di creazione (select coerenti col contratto backend, nessun valore inventato).
const PROPERTY_TYPE_OPTIONS = ['apartment', 'villa', 'house', 'rustic', 'land', 'commercial', 'garage', 'office', 'building', 'other'];
const PROPERTY_CLASS_OPTIONS = ['A', 'B', 'C'];

export async function renderImmobili(container) {
  container.innerHTML = `
    <div class="card panel">
      <div class="list-toolbar">
        <input id="immobili-search" class="input" type="search" placeholder="Cerca per titolo, codice, indirizzo o comune…">
        <select id="immobili-status" class="input">
          <option value="">Tutti gli stati</option>
          ${STATUS_OPTIONS.map((s) => `<option value="${s}">${escapeHtml(s)}</option>`).join('')}
        </select>
        <button type="button" id="immobili-new" class="btn primary">+ Nuovo immobile</button>
      </div>
      <div id="immobili-list-area"><p class="muted">Caricamento…</p></div>
      <div id="immobili-pager" class="list-pager"></div>
    </div>
    <dialog id="new-property-dialog" class="modal modal-wide"></dialog>
  `;

  const searchInput = container.querySelector('#immobili-search');
  const statusSelect = container.querySelector('#immobili-status');
  const listArea = container.querySelector('#immobili-list-area');
  const pagerArea = container.querySelector('#immobili-pager');
  const dialogEl = container.querySelector('#new-property-dialog');

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
  container.querySelector('#immobili-new').addEventListener('click', () => openNewPropertyDialog(dialogEl));

  await load();
}

// --- Nuovo immobile: dialog con campi reali di PropertyCreate ---------------
// Set di campi = quelli gia' presenti nel form legacy property_admin
// (static/property_admin/assets/app.js:78-100, propertyForm), che a sua volta
// riflette esattamente PropertyCreate (property/schemas.py). Nessun campo
// obbligatorio inventato: solo "title" e' required lato form (mirror esatto
// del vincolo backend NOT NULL / min_length=1); tutti gli altri sono opzionali
// e omessi dal payload se vuoti.
function openNewPropertyDialog(dialogEl) {
  let submitting = false;

  dialogEl.innerHTML = `
    <form id="new-property-form">
      <h2 style="margin-top:0">Nuovo immobile</h2>

      <div class="form-field">
        <label>Titolo *</label>
        <input type="text" id="np-title" class="input" maxlength="200" placeholder="Es. Appartamento centro città" required>
      </div>

      <div class="form-grid-2">
        <div class="form-field"><label>Codice</label><input type="text" id="np-code" class="input" maxlength="50"></div>
        <div class="form-field">
          <label>Tipologia</label>
          <select id="np-type" class="input">
            ${PROPERTY_TYPE_OPTIONS.map((t) => `<option value="${t}" ${t === 'apartment' ? 'selected' : ''}>${escapeHtml(t)}</option>`).join('')}
          </select>
        </div>
      </div>

      <div class="form-grid-2">
        <div class="form-field">
          <label>Stato commerciale</label>
          <select id="np-status" class="input">
            ${STATUS_OPTIONS.map((s) => `<option value="${s}" ${s === 'draft' ? 'selected' : ''}>${escapeHtml(s)}</option>`).join('')}
          </select>
        </div>
        <div class="form-field">
          <label>Classe</label>
          <select id="np-class" class="input">
            <option value="">—</option>
            ${PROPERTY_CLASS_OPTIONS.map((c) => `<option value="${c}">${c}</option>`).join('')}
          </select>
        </div>
      </div>

      <h3 class="section-title">Ubicazione</h3>
      <div class="form-grid-2">
        <div class="form-field"><label>Città</label><input type="text" id="np-city" class="input" maxlength="120"></div>
        <div class="form-field"><label>Provincia</label><input type="text" id="np-province" class="input" maxlength="10"></div>
      </div>
      <div class="form-field"><label>Indirizzo</label><input type="text" id="np-address" class="input" maxlength="250"></div>
      <div class="form-field"><label>Microzona</label><input type="text" id="np-microzone" class="input" maxlength="150"></div>

      <h3 class="section-title">Caratteristiche</h3>
      <div class="form-grid-3">
        <div class="form-field"><label>Locali</label><input type="number" id="np-rooms" class="input" min="0" step="1"></div>
        <div class="form-field"><label>Camere</label><input type="number" id="np-bedrooms" class="input" min="0" step="1"></div>
        <div class="form-field"><label>Bagni</label><input type="number" id="np-bathrooms" class="input" min="0" step="1"></div>
      </div>
      <div class="form-grid-2">
        <div class="form-field"><label>Superficie (mq)</label><input type="number" id="np-surface" class="input" min="0" step="any"></div>
        <div class="form-field"><label>Superficie commerciale (mq)</label><input type="number" id="np-commercial-surface" class="input" min="0" step="any"></div>
      </div>
      <div class="form-grid-2">
        <div class="form-field"><label>Condizione</label><input type="text" id="np-condition" class="input" maxlength="80"></div>
        <div class="form-field"><label>Classe energetica</label><input type="text" id="np-energy" class="input" maxlength="20"></div>
      </div>
      <div class="form-field">
        <label>Ascensore</label>
        <select id="np-elevator" class="input">
          <option value="">—</option>
          <option value="true">Sì</option>
          <option value="false">No</option>
        </select>
      </div>

      <h3 class="section-title">Commerciale</h3>
      <div class="form-grid-2">
        <div class="form-field"><label>Prezzo richiesto (€)</label><input type="number" id="np-price" class="input" min="0" step="any"></div>
        <div class="form-field"><label>Scadenza incarico</label><input type="date" id="np-mandate-end" class="input"></div>
      </div>
      <div class="form-field"><label>Assegnato a</label><input type="text" id="np-assigned" class="input" maxlength="200"></div>

      <div class="form-field">
        <label>Note interne</label>
        <textarea id="np-notes" class="input" rows="3"></textarea>
      </div>

      <div id="new-property-error" class="field-error"></div>

      <div class="modal-actions">
        <button type="button" id="new-property-cancel" class="btn ghost">Annulla</button>
        <button type="submit" id="new-property-submit" class="btn primary" disabled>Crea immobile</button>
      </div>
    </form>
  `;

  const form = dialogEl.querySelector('#new-property-form');
  const titleInput = dialogEl.querySelector('#np-title');
  const submitBtn = dialogEl.querySelector('#new-property-submit');
  const errorEl = dialogEl.querySelector('#new-property-error');
  const cancelBtn = dialogEl.querySelector('#new-property-cancel');

  function updateSubmitState() {
    submitBtn.disabled = submitting || !titleInput.value.trim();
  }
  titleInput.addEventListener('input', updateSubmitState);
  cancelBtn.onclick = () => dialogEl.close();

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const title = titleInput.value.trim();
    if (!title || submitting) return;

    submitting = true;
    updateSubmitState();
    errorEl.innerHTML = '';

    const payload = {
      title,
      property_type: dialogEl.querySelector('#np-type').value,
      commercial_status: dialogEl.querySelector('#np-status').value,
    };
    addTextIfPresent(payload, 'code', dialogEl.querySelector('#np-code').value);
    addTextIfPresent(payload, 'classification', dialogEl.querySelector('#np-class').value);
    addTextIfPresent(payload, 'city', dialogEl.querySelector('#np-city').value);
    addTextIfPresent(payload, 'province', dialogEl.querySelector('#np-province').value);
    addTextIfPresent(payload, 'address', dialogEl.querySelector('#np-address').value);
    addTextIfPresent(payload, 'microzone', dialogEl.querySelector('#np-microzone').value);
    addIntegerIfPresent(payload, 'rooms', dialogEl.querySelector('#np-rooms').value);
    addIntegerIfPresent(payload, 'bedrooms', dialogEl.querySelector('#np-bedrooms').value);
    addIntegerIfPresent(payload, 'bathrooms', dialogEl.querySelector('#np-bathrooms').value);
    addNumberIfPresent(payload, 'surface_sqm', dialogEl.querySelector('#np-surface').value);
    addNumberIfPresent(payload, 'commercial_surface_sqm', dialogEl.querySelector('#np-commercial-surface').value);
    addTextIfPresent(payload, 'condition', dialogEl.querySelector('#np-condition').value);
    addTextIfPresent(payload, 'energy_class', dialogEl.querySelector('#np-energy').value);
    const elevatorRaw = dialogEl.querySelector('#np-elevator').value;
    if (elevatorRaw !== '') payload.elevator = elevatorRaw === 'true';
    addNumberIfPresent(payload, 'asking_price', dialogEl.querySelector('#np-price').value);
    const mandateEnd = dialogEl.querySelector('#np-mandate-end').value;
    if (mandateEnd) payload.mandate_end = mandateEnd;
    addTextIfPresent(payload, 'assigned_to', dialogEl.querySelector('#np-assigned').value);
    addTextIfPresent(payload, 'internal_notes', dialogEl.querySelector('#np-notes').value);

    let created;
    try {
      created = await apiPost('/api/property/properties', payload);
    } catch (error) {
      errorEl.textContent = error.message || 'Errore nella creazione dell’immobile.';
      submitting = false;
      updateSubmitState();
      return;
    }

    // Creazione riuscita: chiude il dialog e apre subito la scheda del nuovo
    // immobile (stesso pattern gia' in produzione in acquirenti.js). La lista
    // Immobili, quando rivisitata, effettua sempre una nuova GET: nessun
    // refresh manuale necessario.
    dialogEl.close();
    navigate('immobili', [created.id]);
  });

  updateSubmitState();
  dialogEl.showModal();
}

function addTextIfPresent(payload, key, rawValue) {
  const v = String(rawValue ?? '').trim();
  if (v) payload[key] = v;
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

function formatPrice(value) {
  if (value === null || value === undefined) return '—';
  const n = Number(value);
  if (Number.isNaN(n)) return '—';
  return n.toLocaleString('it-IT', { style: 'currency', currency: 'EUR', maximumFractionDigits: 0 });
}
