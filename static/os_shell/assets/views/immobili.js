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
// CRM-OPS-2: nessun campo obbligatorio per l'operatore - titolo (descrizione
// sintetica) e codice (IMM-<id>) li genera il backend. property_type e
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

// CRM-OPS-2: il form di creazione e' ora il componente condiviso con la
// modifica (components/property-form.js): niente titolo ne' codice da
// compilare (li genera il backend), territorio a menu Regione -> Provincia ->
// Comune -> Microzona dal catalogo del portale, classe energetica a menu,
// "Assegnato a" con gli agenti reali. La lista identifica l'immobile da
// indirizzo, comune e microzona, non dal titolo.
import { apiGet } from '../core/api-client.js';
import { navigate } from '../core/router.js';
import { renderTable, bindTableRowClicks, escapeHtml, formatDate } from '../components/st-table.js';
import { openPropertyDialog, propertyDisplayName } from '../components/property-form.js';

const PAGE_SIZE = 50;

// Valori reali del CHECK constraint properties_status_check (migrations/002_property_01.sql:43).
const STATUS_OPTIONS = ['draft', 'evaluation', 'mandate', 'active', 'reserved', 'under_offer', 'sold', 'withdrawn', 'archived'];

export async function renderImmobili(container) {
  container.innerHTML = `
    <div class="card panel">
      <div class="list-toolbar">
        <input id="immobili-search" class="input" type="search" placeholder="Cerca per codice, indirizzo o comune…">
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
        { label: 'Immobile', render: (p) => `<strong>${escapeHtml(propertyDisplayName(p))}</strong><br><small class="muted">${escapeHtml(p.code || '—')}</small>` },
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
  // Creazione riuscita: si apre subito la scheda del nuovo immobile (stesso
  // pattern di prima); la lista, rivisitata, rifa' sempre la GET.
  container.querySelector('#immobili-new').addEventListener('click', () => openPropertyDialog(dialogEl, {
    mode: 'create',
    onSaved: (created) => navigate('immobili', [created.id]),
  }));

  await load();
}

function formatPrice(value) {
  if (value === null || value === undefined) return '—';
  const n = Number(value);
  if (Number.isNaN(n)) return '—';
  return n.toLocaleString('it-IT', { style: 'currency', currency: 'EUR', maximumFractionDigits: 0 });
}
