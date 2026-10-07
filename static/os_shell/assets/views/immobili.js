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
// CENSIMENTO-1 Fase 4 (S0): due tab in testa all'elenco, «Commerciale» (la
// lista di sempre) e «Censimento» (le palazzine censite, GET
// /api/property/buildings, e il «+ Nuovo» con «Immobile singolo» /
// «Palazzina con più unità»). Nessuna voce di menu nuova. Le etichette di
// tipologia arrivano da form-options (decisione 1: niente valore tecnico in
// colonna). CENSIMENTO-1 Fase 5: «Commerciale» legge l'elenco di default del
// server, che contiene solo le schede operative (`record_kind = 'crm'`);
// «Censimento» elenca le palazzine e le unita' censite
// (`record_kind=census`). Una scheda passa da una lista all'altra solo con
// «Prendi in carico» (stessa riga, stesso codice).
// DELETE-ARCH Fase 2B3: link discreto «Cestino» nella barra dell'elenco Commerciale.
import { apiGet } from '../core/api-client.js';
import { navigate } from '../core/router.js';
import { renderTable, bindTableRowClicks, escapeHtml, formatDate } from '../components/st-table.js';
import { propertyDisplayName, propertyTypeLabel, loadFormOptions } from '../components/property-form.js';
import * as census from '../census/census-api.js';
import { startCreation } from '../census/census-wizard.js';
import { errorMessage } from '../census/census-model.js';

const PAGE_SIZE = 50;

// Valori reali del CHECK constraint properties_status_check (migrations/002_property_01.sql:43).
const STATUS_OPTIONS = ['draft', 'evaluation', 'mandate', 'active', 'reserved', 'under_offer', 'sold', 'withdrawn', 'archived'];

export async function renderImmobili(container, params = []) {
  const modoIniziale = params[0] === 'censimento' ? 'census' : 'crm';
  container.innerHTML = `
    <div class="tabs census-mode-tabs" id="immobili-mode-tabs">
      <button type="button" class="tab-btn${modoIniziale === 'crm' ? ' active' : ''}" data-mode="crm">Commerciale</button>
      <button type="button" class="tab-btn${modoIniziale === 'census' ? ' active' : ''}" data-mode="census">Censimento</button>
    </div>
    <div class="card panel" id="immobili-crm-panel" ${modoIniziale === 'census' ? 'hidden' : ''}>
      <div class="list-toolbar">
        <input id="immobili-search" class="input" type="search" placeholder="Cerca per codice, indirizzo o comune…">
        <select id="immobili-status" class="input">
          <option value="">Tutti gli stati</option>
          ${STATUS_OPTIONS.map((s) => `<option value="${s}">${escapeHtml(s)}</option>`).join('')}
        </select>
        <button type="button" id="immobili-new" class="btn primary">+ Nuovo immobile</button>
        <a href="#/cestino" class="btn ghost trash-link" id="immobili-trash-link">Cestino</a>
      </div>
      <div id="immobili-list-area"><p class="muted">Caricamento…</p></div>
      <div id="immobili-pager" class="list-pager"></div>
    </div>
    <div class="card panel" id="immobili-census-panel" ${modoIniziale === 'crm' ? 'hidden' : ''}>
      <div class="list-toolbar">
        <input id="census-search" class="input" type="search" placeholder="Cerca palazzina o unità per nome, via o comune…">
        <button type="button" id="census-new" class="btn primary">+ Nuovo</button>
      </div>
      <h3 class="census-section-title">Palazzine</h3>
      <div id="census-list-area"><p class="muted">Caricamento…</p></div>
      <h3 class="census-section-title">Unità censite</h3>
      <div id="census-units-area"><p class="muted">Caricamento…</p></div>
      <p class="muted census-note">Le unità censite non sono immobili operativi: entrano nell'elenco «Commerciale» con «Prendi in carico».</p>
    </div>
    <dialog id="new-property-dialog" class="modal modal-wide"></dialog>
    <dialog id="census-sheet" class="modal census-sheet"></dialog>
  `;

  const searchInput = container.querySelector('#immobili-search');
  const statusSelect = container.querySelector('#immobili-status');
  const listArea = container.querySelector('#immobili-list-area');
  const pagerArea = container.querySelector('#immobili-pager');
  const dialogEl = container.querySelector('#new-property-dialog');

  let offset = 0;
  let debounceHandle = null;
  let tipologie = [];
  let tipiPertinenza = [];    // PERTINENZE-1: Posto auto, Cantina… per le pertinenze autonome
  try {
    const opzioniForm = await loadFormOptions();
    tipologie = opzioniForm.property_types || [];
    tipiPertinenza = opzioniForm.accessory_kinds || [];
  } catch (_error) {
    tipologie = [];           // etichette non disponibili: resta il valore tecnico
  }

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
        { label: 'Tipologia', render: (p) => escapeHtml(propertyTypeLabel(p, tipologie, tipiPertinenza)) },
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
  // CREAZIONE-GUIDATA-1: «+ Nuovo immobile» parte dalla procedura guidata
  // (territorio -> percorso -> edificio). Da qui le schede nascono COMMERCIALI:
  // l'unita' autonoma apre il form di sempre (POST /properties) con il
  // territorio gia' compilato; in palazzina le unita' nascono `crm`.
  // Creazione riuscita: si apre la scheda del nuovo immobile, come prima.
  container.querySelector('#immobili-new').addEventListener('click', async () => {
    const opzioni = await opzioniCreazione(listArea);
    if (opzioni) startCreation({ wizardDialog: container.querySelector('#census-sheet'), formDialog: dialogEl, options: opzioni, recordKind: 'crm' });
  });

  async function opzioniCreazione(area) {
    try {
      return await loadFormOptions();
    } catch (error) {
      area.insertAdjacentHTML('afterbegin', `<div class="error-box">Impossibile caricare i dati del form: ${escapeHtml(error.message)}</div>`);
      return null;
    }
  }

  // --- Censimento (S0 -> S1/S3) ------------------------------------------------
  const crmPanel = container.querySelector('#immobili-crm-panel');
  const censusPanel = container.querySelector('#immobili-census-panel');
  const censusSearch = container.querySelector('#census-search');
  const censusListArea = container.querySelector('#census-list-area');
  const censusUnitsArea = container.querySelector('#census-units-area');
  const censusSheet = container.querySelector('#census-sheet');
  let censusDebounce = null;
  let censusLoaded = false;

  async function loadCensus() {
    censusLoaded = true;
    loadCensusUnits();
    censusListArea.innerHTML = '<p class="muted">Caricamento…</p>';
    let items = [];
    try {
      const data = await census.listBuildings({ search: censusSearch.value.trim() || undefined });
      items = Array.isArray(data?.items) ? data.items : [];
    } catch (error) {
      censusListArea.innerHTML = `<div class="error-box">${escapeHtml(errorMessage(error))}</div>`;
      return;
    }
    if (!items.length) {
      censusListArea.innerHTML = `<p class="muted">${censusSearch.value.trim() ? 'Nessuna palazzina trovata.' : 'Nessuna palazzina censita: comincia da «+ Nuovo».'}</p>`;
      return;
    }
    censusListArea.innerHTML = renderTable(
      [
        { label: 'Palazzina', render: (b) => `<strong>${escapeHtml(b.name || [b.address, b.civic_number].filter(Boolean).join(' ') || `Palazzina #${b.id}`)}</strong><br><small class="muted">${escapeHtml([[b.address, b.civic_number].filter(Boolean).join(' '), b.city].filter(Boolean).join(', ') || '—')}</small>` },
        // EDIFICI-1: le censite del riepilogo condiviso con la sezione Edifici
        // (archiviate comprese), se il server lo manda.
        { label: 'Censite', render: (b) => `${escapeHtml((b.census_summary && Number.isInteger(b.census_summary.units_counted)) ? b.census_summary.units_counted : (b.units_census ?? 0))}${Number.isInteger(b.units_declared) ? ` di ${escapeHtml(b.units_declared)}` : ''}` },
        { label: 'Da chiarire', render: (b) => escapeHtml(b.accessories_unknown || 0) },
        { label: 'Aggiornata il', render: (b) => escapeHtml(formatDate(b.updated_at)) },
      ],
      items,
      { onRowClick: true },
    );
    bindTableRowClicks(censusListArea, (id) => navigate('edifici', [id]));
  }

  // Fase 5: le unita' censite (in palazzina o singole), dal server con
  // `record_kind=census` - mai filtrate nel browser da un elenco misto.
  async function loadCensusUnits() {
    censusUnitsArea.innerHTML = '<p class="muted">Caricamento…</p>';
    let unita = [];
    try {
      const data = await census.listCensusUnits({ search: censusSearch.value.trim() || undefined, limit: PAGE_SIZE });
      unita = Array.isArray(data?.items) ? data.items : [];
    } catch (error) {
      censusUnitsArea.innerHTML = `<div class="error-box">${escapeHtml(errorMessage(error))}</div>`;
      return;
    }
    if (!unita.length) {
      censusUnitsArea.innerHTML = `<p class="muted">${censusSearch.value.trim() ? 'Nessuna unità censita trovata.' : 'Nessuna unità censita.'}</p>`;
      return;
    }
    censusUnitsArea.innerHTML = renderTable(
      [
        { label: 'Unità', render: (p) => `<strong>${escapeHtml(propertyDisplayName(p))}</strong><br><small class="muted">${escapeHtml(p.code || '—')}</small>` },
        { label: 'Tipologia', render: (p) => escapeHtml(propertyTypeLabel(p, tipologie, tipiPertinenza)) },
        { label: 'Comune', render: (p) => escapeHtml(p.city || '—') },
        { label: 'Collocazione', render: (p) => escapeHtml(p.building_id ? 'In palazzina' : 'Singola') },
        { label: 'Aggiornata il', render: (p) => escapeHtml(formatDate(p.updated_at)) },
      ],
      unita,
      { onRowClick: true },
    );
    if (unita.length === PAGE_SIZE) censusUnitsArea.insertAdjacentHTML('beforeend', `<p class="muted">Prime ${PAGE_SIZE}: affina la ricerca per vedere le altre.</p>`);
    bindTableRowClicks(censusUnitsArea, (id) => navigate('immobili', [id]));
  }

  function mostraModo(modo) {
    container.querySelector('#immobili-mode-tabs').querySelectorAll('.tab-btn').forEach((b) => b.classList.toggle('active', b.dataset.mode === modo));
    crmPanel.hidden = modo !== 'crm';
    censusPanel.hidden = modo !== 'census';
    if (modo === 'census' && !censusLoaded) loadCensus();
  }
  container.querySelector('#immobili-mode-tabs').querySelectorAll('.tab-btn').forEach((b) => b.addEventListener('click', () => mostraModo(b.dataset.mode)));
  censusSearch.addEventListener('input', () => { clearTimeout(censusDebounce); censusDebounce = setTimeout(loadCensus, 300); });
  // CREAZIONE-GUIDATA-1: «+ Nuovo» del Censimento apre la stessa procedura
  // guidata; da qui le schede nascono di censimento.
  container.querySelector('#census-new').addEventListener('click', async () => {
    const opzioni = await opzioniCreazione(censusListArea);
    if (opzioni) startCreation({ wizardDialog: censusSheet, options: opzioni, recordKind: 'census' });
  });

  if (modoIniziale === 'census') loadCensus();
  await load();
}

function formatPrice(value) {
  if (value === null || value === undefined) return '—';
  const n = Number(value);
  if (Number.isNaN(n)) return '—';
  return n.toLocaleString('it-IT', { style: 'currency', currency: 'EUR', maximumFractionDigits: 0 });
}
