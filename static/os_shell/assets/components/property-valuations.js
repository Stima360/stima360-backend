// STIMA360 OS — components/property-valuations.js
// BLOCCO-2A: la scheda «Valutazioni» dell'immobile.
//
// Una «Valutazione dell'agente»: i valori li inserisce l'operatore con le sue
// fonti e i suoi comparabili. Non e' una stima automatica, non e' OMI, non e'
// una perizia, e la pagina lo dice. Nessun importo viene calcolato qui: il
// canone non deriva dal prezzo, e una sezione vuota e' «Non valutata».
//
// API: /api/property-valuations (agenzia dalla sessione). Permessi, stati e
// completezza li decide il server (`can_edit`, `can_approve`,
// `can_create_version`, `approval_issues`): la pagina non offre i comandi a
// chi riceverebbe 403. Tutto il testo passa da `textContent`.

import { apiGet, apiPost, apiPut } from '../core/api-client.js';

const API = '/api/property-valuations';

// Le stesse etichette del server (property_valuations/enums.py).
export const SURFACE_KINDS = {
  commerciale: 'Superficie commerciale', calpestabile: 'Superficie calpestabile (netta)',
  lorda: 'Superficie lorda', catastale: 'Superficie catastale', altro: 'Altro (specificare nella nota)',
};
export const SURFACE_SOURCES = {
  misurazione_agente: 'Misurazione dell’agente', planimetria: 'Planimetria', visura_catastale: 'Visura catastale',
  dichiarazione_proprietario: 'Dichiarazione del proprietario', scheda_immobile: 'Dati della scheda immobile',
  altro: 'Altro (specificare nella nota)',
};
export const RENT_SEGMENTS = {
  residenziale_ordinario: 'Residenziale ordinario (canone libero)', residenziale_concordato: 'Residenziale a canone concordato',
  residenziale_transitorio: 'Residenziale transitorio', studenti: 'Studenti universitari', uso_diverso: 'Uso diverso dall’abitativo',
};
export const RENT_FURNISHING = { arredato: 'Arredato', parzialmente_arredato: 'Parzialmente arredato', non_arredato: 'Non arredato' };
export const RENT_EXPENSES = { escluse: 'Spese escluse', incluse: 'Spese incluse', parzialmente_incluse: 'Spese parzialmente incluse' };
export const COMPARABLE_KINDS = { vendita: 'Vendita', locazione: 'Locazione' };
export const PRICE_TYPES = { richiesto: 'Prezzo richiesto', concluso: 'Prezzo concluso' };
export const RENT_PERIODS = { mensile: 'Mensile', annuale: 'Annuale' };
export const STATUS = { draft: 'Bozza', approved: 'Approvata', superseded: 'Superata' };
const STATUS_TONE = { draft: 'badge-warn', approved: 'badge-ok', superseded: 'badge-gray' };

export const METHOD_NOTICE = 'Valutazione dell’agente: valori inseriti dall’operatore sulla base delle fonti indicate. '
  + 'Non è una stima automatica, non è una quotazione OMI e non è una perizia.';

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function button(label, className, attr) {
  const b = el('button', className ? `btn ${className}` : 'btn', label);
  b.type = 'button';
  if (attr) b.setAttribute(attr, '');
  return b;
}

/** Un importo del server (stringa decimale) in euro, solo per mostrarlo. */
export function money(value, suffix = '') {
  if (value === null || value === undefined || value === '') return null;
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  return `${n.toLocaleString('it-IT', { style: 'currency', currency: 'EUR', maximumFractionDigits: 2 })}${suffix}`;
}

export function rangeText(section, suffix = '') {
  if (!section) return 'Non valutata';
  const parts = [];
  if (section.min || section.max) parts.push(`${money(section.min, suffix) || '—'} – ${money(section.max, suffix) || '—'}`);
  if (section.recommended) parts.push(`consigliato ${money(section.recommended, suffix)}`);
  return parts.length ? parts.join(' · ') : 'Dati incompleti';
}

function dateText(value) {
  if (!value) return '—';
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleString('it-IT', {
    day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Rome' });
}

/** Il messaggio di un errore del server, leggibile, e i problemi da elencare. */
export function errorDetails(error) {
  const d = error && error.data && error.data.detail;
  if (d && typeof d === 'object' && !Array.isArray(d)) {
    return { message: d.message || error.message, issues: Array.isArray(d.issues) ? d.issues : [] };
  }
  if (Array.isArray(d)) {
    return {
      message: 'Alcuni dati non sono validi.',
      issues: d.map((i) => `${(i.loc || []).filter((p) => p !== 'body').join(' › ')}: ${String(i.msg || '').replace(/^Value error, /, '')}`),
    };
  }
  return { message: (error && error.message) || 'Operazione non riuscita.', issues: [] };
}

// ---------------------------------------------------------------------------
// lettura di una versione
// ---------------------------------------------------------------------------

function dl(rows) {
  const list = el('dl', 'valuation-facts');
  for (const [k, v] of rows) {
    if (v === null || v === undefined || v === '') continue;
    const row = el('div', 'valuation-fact');
    row.append(el('dt', null, k), el('dd', null, v));
    list.appendChild(row);
  }
  return list;
}

function sectionBlock(title, rows, absent) {
  const box = el('section', 'valuation-section');
  box.appendChild(el('h4', null, title));
  if (absent) {
    box.appendChild(el('p', 'muted', 'Non valutata'));
    return box;
  }
  box.appendChild(dl(rows));
  return box;
}

export function renderValuationView(v, names = {}, versions = {}) {
  const c = v.content || {};
  const root = el('div', 'valuation-view');
  root.setAttribute('data-valuation-view', String(v.id));
  const head = el('div', 'valuation-view-head');
  head.append(el('strong', null, `Versione ${v.version}`), el('span', `badge ${STATUS_TONE[v.status] || ''}`, STATUS[v.status] || v.status));
  root.appendChild(head);
  root.appendChild(dl([
    ['Metodo', v.method_label],
    ['Creata', `${dateText(v.created_at)}${names[v.created_by_user_id] ? ` da ${names[v.created_by_user_id]}` : ''}`],
    ['Approvata', v.approved_at ? `${dateText(v.approved_at)}${names[v.approved_by_user_id] ? ` da ${names[v.approved_by_user_id]}` : ''}` : null],
    ['Superata', v.superseded_at ? dateText(v.superseded_at) : null],
    ['Versione precedente', v.previous_valuation_id ? (versions[v.previous_valuation_id] ? `v${versions[v.previous_valuation_id]}` : 'versione precedente') : null],
  ]));
  const s = c.surface || {};
  root.appendChild(sectionBlock('Immobile considerato', [
    ['Caratteristiche rilevanti', c.characteristics],
    ['Superficie utilizzata', s.value ? `${Number(s.value).toLocaleString('it-IT')} m²` : null],
    ['Tipo di superficie', SURFACE_KINDS[s.kind]],
    ['Provenienza del dato', SURFACE_SOURCES[s.source]],
    ['Nota sulla superficie', s.note],
  ], false));
  const sale = c.sale;
  root.appendChild(sectionBlock('Vendita', sale && [
    ['Intervallo', sale.min || sale.max ? `${money(sale.min) || '—'} – ${money(sale.max) || '—'}` : null],
    ['Prezzo di proposta consigliato', money(sale.recommended)],
    ['Motivo del consigliato fuori intervallo', sale.outside_range_reason],
  ], !sale));
  const rent = c.rent;
  root.appendChild(sectionBlock('Locazione (canone mensile)', rent && [
    ['Segmento', RENT_SEGMENTS[rent.segment]],
    ['Arredo', RENT_FURNISHING[rent.furnishing]],
    ['Spese', RENT_EXPENSES[rent.expenses]],
    ['Intervallo mensile', rent.min || rent.max ? `${money(rent.min, '/mese') || '—'} – ${money(rent.max, '/mese') || '—'}` : null],
    ['Canone mensile consigliato', money(rent.recommended, '/mese')],
    ['Motivo del consigliato fuori intervallo', rent.outside_range_reason],
    ['Note', rent.notes],
  ], !rent));
  root.appendChild(sectionBlock('Motivazione e fonti', [
    ['Motivazione', c.rationale], ['Fonti e base informativa', c.sources],
  ], false));
  const comps = el('section', 'valuation-section');
  comps.appendChild(el('h4', null, 'Comparabili'));
  if (!c.comparables || !c.comparables.length) {
    comps.appendChild(el('p', 'muted', c.comparables_note
      ? `Nessun comparabile. Motivo dichiarato: ${c.comparables_note}` : 'Nessun comparabile inserito.'));
  } else {
    const list = el('ul', 'valuation-comparables');
    for (const cp of c.comparables) {
      const item = el('li');
      const amount = money(cp.amount, cp.kind === 'locazione' ? (cp.rent_period === 'annuale' ? '/anno' : '/mese') : '');
      item.appendChild(el('strong', null, `${COMPARABLE_KINDS[cp.kind] || cp.kind} · ${PRICE_TYPES[cp.price_type] || cp.price_type} · ${amount}`));
      const dettagli = [
        cp.surface ? `${Number(cp.surface).toLocaleString('it-IT')} m²${cp.surface_kind ? ` (${SURFACE_KINDS[cp.surface_kind] || cp.surface_kind})` : ''}` : null,
        cp.zone, cp.features, `Fonte: ${cp.source}${cp.data_date ? `, ${cp.data_date}` : ''}`,
        cp.differences ? `Differenze: ${cp.differences}` : null,
      ].filter(Boolean);
      for (const d of dettagli) item.appendChild(el('div', 'muted', d));
      if (cp.url) {
        const a = el('a', null, cp.url);
        a.href = cp.url;
        a.target = '_blank';
        a.rel = 'noopener noreferrer';
        item.appendChild(a);
      }
      list.appendChild(item);
    }
    comps.appendChild(list);
    if (c.comparables_note) comps.appendChild(el('p', 'muted', c.comparables_note));
  }
  root.appendChild(comps);
  if (v.status === 'draft' && v.approval_issues && v.approval_issues.length) {
    const box = el('div', 'valuation-issues');
    box.setAttribute('data-approval-issues', '');
    box.appendChild(el('strong', null, 'Da completare prima dell’approvazione'));
    const ul = el('ul');
    for (const i of v.approval_issues) ul.appendChild(el('li', null, i));
    box.appendChild(ul);
    root.appendChild(box);
  }
  return root;
}

// ---------------------------------------------------------------------------
// modulo di modifica della bozza
// ---------------------------------------------------------------------------

function field(label, input, hint) {
  const wrap = el('div', 'form-field');
  const lab = el('label', null, label);
  if (!input.id) input.id = `val-${Math.random().toString(36).slice(2, 9)}`;
  lab.htmlFor = input.id;
  wrap.append(lab, input);
  if (hint) wrap.appendChild(el('p', 'muted valuation-hint', hint));
  return wrap;
}

function input(name, { type = 'text', value = '', placeholder = '', inputmode } = {}) {
  const i = el('input', 'input');
  i.name = name;
  i.type = type;
  i.value = value ?? '';
  if (placeholder) i.placeholder = placeholder;
  if (inputmode) i.inputMode = inputmode;
  return i;
}

function textarea(name, value = '') {
  const t = el('textarea', 'input');
  t.name = name;
  t.rows = 3;
  t.value = value ?? '';
  return t;
}

function select(name, choices, value, empty = '— scegli —') {
  const s = el('select', 'input');
  s.name = name;
  s.appendChild(new Option(empty, ''));
  for (const [k, label] of Object.entries(choices)) s.appendChild(new Option(label, k));
  s.value = value || '';
  return s;
}

/** Un importo dal modulo, come testo decimale per il server (mai un float).
 *  Convenzione italiana: la virgola e' il decimale, il punto separa le migliaia.
 *    «195.000,50» -> «195000.50»   «195.000» -> «195000»   «1.500» -> «1500»
 *    «195000.50» (formato del server, due decimali) resta com'e'.
 *  Cio' che non rientra in questi casi passa invariato: lo valida il server. */
export function amountValue(raw) {
  const v = String(raw ?? '').trim().replace(/[\s€]/g, '');
  if (!v) return null;
  if (v.includes(',')) return v.replace(/\./g, '').replace(',', '.');
  if (/^\d{1,3}(\.\d{3})+$/.test(v)) return v.replace(/\./g, '');
  return v;
}

function text(raw) {
  const v = String(raw ?? '').trim();
  return v || null;
}

export function buildForm(content, { onCancel }) {
  const c = content || {};
  const form = el('form', 'valuation-form');
  form.noValidate = true;
  form.setAttribute('data-valuation-form', '');
  form.appendChild(el('p', 'muted valuation-notice', METHOD_NOTICE));

  const s = c.surface || {};
  const base = el('fieldset', 'valuation-fieldset');
  base.appendChild(el('legend', null, 'Immobile considerato'));
  const fCharacteristics = textarea('characteristics', c.characteristics);
  const fSurface = input('surface_value', { value: s.value, inputmode: 'decimal', placeholder: 'm²' });
  const fSurfaceKind = select('surface_kind', SURFACE_KINDS, s.kind);
  const fSurfaceSource = select('surface_source', SURFACE_SOURCES, s.source);
  const fSurfaceNote = input('surface_note', { value: s.note });
  base.append(field('Caratteristiche rilevanti', fCharacteristics),
    field('Superficie utilizzata (m²)', fSurface), field('Tipo di superficie', fSurfaceKind),
    field('Provenienza del dato', fSurfaceSource), field('Nota sulla superficie', fSurfaceNote));
  form.appendChild(base);

  const sale = c.sale || {};
  const saleBox = el('fieldset', 'valuation-fieldset');
  saleBox.appendChild(el('legend', null, 'Vendita'));
  const saleOn = input('sale_on', { type: 'checkbox' });
  saleOn.checked = Boolean(c.sale);
  const saleFields = el('div', 'valuation-section-fields');
  const fSaleMin = input('sale_min', { value: sale.min, inputmode: 'decimal', placeholder: '€' });
  const fSaleMax = input('sale_max', { value: sale.max, inputmode: 'decimal', placeholder: '€' });
  const fSaleRec = input('sale_recommended', { value: sale.recommended, inputmode: 'decimal', placeholder: '€' });
  const fSaleWhy = textarea('sale_outside_range_reason', sale.outside_range_reason);
  saleFields.append(field('Valore minimo', fSaleMin), field('Valore massimo', fSaleMax),
    field('Prezzo di proposta consigliato', fSaleRec),
    field('Motivo se il consigliato è fuori intervallo', fSaleWhy));
  saleBox.append(field('Valuta la vendita', saleOn, 'Se non selezionata, la vendita risulta «Non valutata».'), saleFields);
  form.appendChild(saleBox);

  const rent = c.rent || {};
  const rentBox = el('fieldset', 'valuation-fieldset');
  rentBox.appendChild(el('legend', null, 'Locazione (canone mensile)'));
  const rentOn = input('rent_on', { type: 'checkbox' });
  rentOn.checked = Boolean(c.rent);
  const rentFields = el('div', 'valuation-section-fields');
  const fSegment = select('rent_segment', RENT_SEGMENTS, rent.segment);
  const fFurn = select('rent_furnishing', RENT_FURNISHING, rent.furnishing);
  const fExp = select('rent_expenses', RENT_EXPENSES, rent.expenses);
  const fRentMin = input('rent_min', { value: rent.min, inputmode: 'decimal', placeholder: '€/mese' });
  const fRentMax = input('rent_max', { value: rent.max, inputmode: 'decimal', placeholder: '€/mese' });
  const fRentRec = input('rent_recommended', { value: rent.recommended, inputmode: 'decimal', placeholder: '€/mese' });
  const fRentWhy = textarea('rent_outside_range_reason', rent.outside_range_reason);
  const fRentNotes = textarea('rent_notes', rent.notes);
  rentFields.append(field('Segmento', fSegment, 'Canoni mensili: le tariffe turistiche giornaliere non sono ammesse qui.'),
    field('Arredo', fFurn), field('Spese', fExp),
    field('Canone mensile minimo', fRentMin), field('Canone mensile massimo', fRentMax),
    field('Canone mensile consigliato', fRentRec),
    field('Motivo se il consigliato è fuori intervallo', fRentWhy), field('Note', fRentNotes));
  rentBox.append(field('Valuta la locazione', rentOn, 'Se non selezionata, la locazione risulta «Non valutata».'), rentFields);
  form.appendChild(rentBox);

  const why = el('fieldset', 'valuation-fieldset');
  why.appendChild(el('legend', null, 'Motivazione e fonti'));
  const fRationale = textarea('rationale', c.rationale);
  const fSources = textarea('sources', c.sources);
  why.append(field('Motivazione della valutazione', fRationale),
    field('Fonti e base informativa', fSources, 'Ad esempio: sopralluogo, annunci di zona consultati, atti noti all’agente.'));
  form.appendChild(why);

  const compBox = el('fieldset', 'valuation-fieldset');
  compBox.appendChild(el('legend', null, 'Comparabili (facoltativi)'));
  compBox.appendChild(el('p', 'muted valuation-hint', 'Inseriti a mano: nessun dato viene recuperato dagli indirizzi web.'));
  const compList = el('div', 'valuation-comparable-list');
  compList.setAttribute('data-comparables', '');
  const addComp = button('+ Aggiungi comparabile', 'ghost', 'data-add-comparable');
  const fCompNote = textarea('comparables_note', c.comparables_note);
  compBox.append(compList, addComp, field('Se non ci sono comparabili, spiega perché', fCompNote));
  form.appendChild(compBox);

  function compRow(cp = {}) {
    const row = el('div', 'valuation-comparable card');
    row.setAttribute('data-comparable', '');
    const f = {
      kind: select('kind', COMPARABLE_KINDS, cp.kind),
      price_type: select('price_type', PRICE_TYPES, cp.price_type),
      amount: input('amount', { value: cp.amount, inputmode: 'decimal', placeholder: '€' }),
      rent_period: select('rent_period', RENT_PERIODS, cp.rent_period, '— solo locazione —'),
      surface: input('surface', { value: cp.surface, inputmode: 'decimal', placeholder: 'm²' }),
      surface_kind: select('surface_kind', SURFACE_KINDS, cp.surface_kind),
      zone: input('zone', { value: cp.zone }),
      features: textarea('features', cp.features),
      source: input('source', { value: cp.source }),
      data_date: input('data_date', { type: 'date', value: cp.data_date }),
      url: input('url', { type: 'url', value: cp.url, placeholder: 'https://' }),
      differences: textarea('differences', cp.differences),
    };
    row.append(field('Tipo', f.kind), field('Prezzo', f.price_type), field('Importo', f.amount),
      field('Periodo del canone', f.rent_period), field('Superficie (m²)', f.surface), field('Tipo di superficie', f.surface_kind),
      field('Zona', f.zone), field('Caratteristiche essenziali', f.features), field('Fonte', f.source),
      field('Data del dato', f.data_date), field('Indirizzo web (facoltativo)', f.url),
      field('Differenze rispetto all’immobile', f.differences));
    const remove = button('Rimuovi comparabile', 'danger btn-small', 'data-remove-comparable');
    remove.addEventListener('click', () => row.remove());
    row.appendChild(remove);
    row._fields = f;
    compList.appendChild(row);
  }
  for (const cp of c.comparables || []) compRow(cp);
  addComp.addEventListener('click', () => compRow());

  function toggle() {
    saleFields.hidden = !saleOn.checked;
    rentFields.hidden = !rentOn.checked;
  }
  saleOn.addEventListener('change', toggle);
  rentOn.addEventListener('change', toggle);
  toggle();

  const actions = el('div', 'agency-actions');
  const save = button('Salva bozza', 'primary', 'data-valuation-save');
  save.type = 'submit';
  const cancel = button('Annulla', 'ghost', 'data-valuation-cancel');
  cancel.addEventListener('click', () => onCancel());
  actions.append(save, cancel);
  form.appendChild(actions);

  form.collect = () => ({
    characteristics: text(fCharacteristics.value),
    surface: { value: amountValue(fSurface.value), kind: fSurfaceKind.value || null, source: fSurfaceSource.value || null, note: text(fSurfaceNote.value) },
    sale: saleOn.checked ? {
      min: amountValue(fSaleMin.value), max: amountValue(fSaleMax.value), recommended: amountValue(fSaleRec.value),
      outside_range_reason: text(fSaleWhy.value),
    } : null,
    rent: rentOn.checked ? {
      segment: fSegment.value || null, furnishing: fFurn.value || null, expenses: fExp.value || null,
      min: amountValue(fRentMin.value), max: amountValue(fRentMax.value), recommended: amountValue(fRentRec.value),
      outside_range_reason: text(fRentWhy.value), notes: text(fRentNotes.value),
    } : null,
    rationale: text(fRationale.value),
    sources: text(fSources.value),
    comparables: [...compList.querySelectorAll('[data-comparable]')].map((row) => {
      const f = row._fields;
      return {
        kind: f.kind.value || null, price_type: f.price_type.value || null, amount: amountValue(f.amount.value),
        rent_period: f.rent_period.value || null, surface: amountValue(f.surface.value), surface_kind: f.surface_kind.value || null,
        zone: text(f.zone.value), features: text(f.features.value), source: text(f.source.value),
        data_date: f.data_date.value || null, url: text(f.url.value), differences: text(f.differences.value),
      };
    }),
    comparables_note: text(fCompNote.value),
  });
  return form;
}

// ---------------------------------------------------------------------------
// la scheda
// ---------------------------------------------------------------------------

// `loadAgents` (facoltativo) restituisce i membri dell'agenzia per mostrare i
// nomi di autore e approvatore; lo passa la scheda immobile, che gia' lo usa.
export async function mountPropertyValuations(container, { propertyId, loadAgents = null }) {
  const root = el('div', 'valuations-tab');
  root.setAttribute('data-valuations', '');
  const notice = el('div');
  notice.setAttribute('role', 'status');
  notice.setAttribute('data-valuation-notice', '');
  const intro = el('p', 'muted valuation-notice', METHOD_NOTICE);
  const toolbar = el('div', 'agency-actions');
  const listBox = el('div', 'valuation-list');
  const detail = el('div', 'valuation-detail');
  detail.setAttribute('data-valuation-detail', '');
  root.append(el('h3', 'section-title', 'Valutazioni dell’agente'), intro, notice, toolbar, listBox, detail);
  container.replaceChildren(root);

  let names = {};
  try {
    const agents = loadAgents ? await loadAgents() : null;
    for (const a of (agents && agents.items) || []) names[a.id] = a.name;
  } catch (_e) { names = {}; }

  let state = null;
  let selected = null;

  function say(message, kind, issues = []) {
    notice.replaceChildren();
    if (!message) return;
    const box = el('div', kind === 'ok' ? 'success-box' : 'error-box', message);
    if (issues.length) {
      const ul = el('ul');
      for (const i of issues) ul.appendChild(el('li', null, i));
      box.appendChild(ul);
    }
    notice.appendChild(box);
  }

  async function load(selectId) {
    try {
      state = await apiGet(`${API}?property_id=${encodeURIComponent(propertyId)}`);
    } catch (error) {
      const d = errorDetails(error);
      listBox.replaceChildren(el('div', 'error-box', d.message));
      toolbar.replaceChildren();
      detail.replaceChildren();
      return;
    }
    drawToolbar();
    drawList();
    const target = selectId ? state.items.find((v) => v.id === selectId) : state.items[0];
    if (target) showDetail(target); else detail.replaceChildren();
  }

  function drawToolbar() {
    toolbar.replaceChildren();
    if (state.can_create) {
      const b = button('Nuova valutazione', 'primary', 'data-valuation-new');
      b.addEventListener('click', () => openEditor(null));
      toolbar.appendChild(b);
    } else if (state.has_draft) {
      toolbar.appendChild(el('p', 'muted', 'C’è già una bozza in corso: modificala o approvala.'));
    }
  }

  function drawList() {
    listBox.replaceChildren();
    if (!state.items.length) {
      listBox.appendChild(el('p', 'muted', 'Nessuna valutazione per questo immobile.'));
      return;
    }
    const table = el('table', 'st-table');
    const head = el('tr');
    for (const h of ['Versione', 'Stato', 'Data', 'Vendita', 'Locazione']) head.appendChild(el('th', null, h));
    const thead = el('thead');
    thead.appendChild(head);
    const tbody = el('tbody');
    for (const v of state.items) {
      const tr = el('tr', 'row-clickable');
      tr.setAttribute('data-valuation-row', String(v.id));
      const badge = el('span', `badge ${STATUS_TONE[v.status] || ''}`, STATUS[v.status] || v.status);
      const tdStato = el('td');
      tdStato.appendChild(badge);
      tr.append(el('td', null, `v${v.version}`), tdStato, el('td', null, dateText(v.approved_at || v.updated_at)),
        el('td', null, rangeText(v.content && v.content.sale)), el('td', null, rangeText(v.content && v.content.rent, '/mese')));
      tr.addEventListener('click', () => showDetail(v));
      tbody.appendChild(tr);
    }
    table.append(thead, tbody);
    const wrap = el('div', 'table-wrap');
    wrap.appendChild(table);
    listBox.appendChild(wrap);
  }

  function showDetail(v) {
    selected = v;
    const versions = Object.fromEntries(state.items.map((x) => [x.id, x.version]));
    detail.replaceChildren(renderValuationView(v, names, versions));
    const actions = el('div', 'agency-actions');
    if (v.can_edit) {
      const edit = button('Modifica bozza', 'ghost', 'data-valuation-edit');
      edit.addEventListener('click', () => openEditor(v));
      actions.appendChild(edit);
    }
    if (v.can_approve) {
      const ok = button('Approva valutazione', 'primary', 'data-valuation-approve');
      ok.addEventListener('click', () => approve(v, ok));
      actions.appendChild(ok);
    }
    if (v.can_create_version && !state.has_draft) {
      const nv = button('Crea nuova versione', 'ghost', 'data-valuation-new-version');
      nv.addEventListener('click', () => newVersion(v, nv));
      actions.appendChild(nv);
    }
    if (actions.childNodes.length) detail.appendChild(actions);
  }

  function openEditor(v) {
    say('');
    const form = buildForm(v ? v.content : null, { onCancel: () => (selected ? showDetail(selected) : detail.replaceChildren()) });
    detail.replaceChildren(el('h4', null, v ? `Modifica bozza · versione ${v.version}` : 'Nuova valutazione'), form);
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const saveBtn = form.querySelector('[data-valuation-save]');
      saveBtn.disabled = true;
      try {
        const saved = v
          ? await apiPut(`${API}/${v.id}`, { row_version: v.row_version, content: form.collect() })
          : await apiPost(API, { property_id: Number(propertyId), content: form.collect() });
        say('Bozza salvata.', 'ok');
        await load(saved.id);
      } catch (error) {
        const d = errorDetails(error);
        say(`Bozza non salvata. ${d.message}`, 'error', d.issues);
      } finally {
        saveBtn.disabled = false;
      }
    });
  }

  async function approve(v, btn) {
    btn.disabled = true;
    try {
      const done = await apiPost(`${API}/${v.id}/approve`, { row_version: v.row_version });
      say('Valutazione approvata.', 'ok');
      await load(done.id);
    } catch (error) {
      const d = errorDetails(error);
      say(`Valutazione non approvata. ${d.message}`, 'error', d.issues);
      btn.disabled = false;
    }
  }

  async function newVersion(v, btn) {
    btn.disabled = true;
    try {
      const draft = await apiPost(`${API}/${v.id}/new-version`, {});
      say(`Creata la bozza della versione ${draft.version}: la versione approvata resta valida finché la nuova non viene approvata.`, 'ok');
      await load(draft.id);
    } catch (error) {
      say(`Nuova versione non creata. ${errorDetails(error).message}`, 'error');
      btn.disabled = false;
    }
  }

  await load();
}
