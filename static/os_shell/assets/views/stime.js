// Un solo archivio di stime: la vista dettagliata filtra gli stessi record.
import { apiGet } from '../core/api-client.js';
import { navigate } from '../core/router.js';
import { formatDateTime } from '../components/st-table.js';

const PAGE_SIZE = 30;

function node(tag, className, value) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (value !== undefined) element.textContent = value == null || value === '' ? '—' : String(value);
  return element;
}

function text(value) {
  return value == null || value === '' ? '—' : String(value);
}

function name(row) {
  return [row.nome, row.cognome].filter(Boolean).join(' ').trim() || 'Proprietario non indicato';
}

function address(row) {
  return [row.via, row.civico, row.comune].filter(Boolean).join(', ') || 'Indirizzo non indicato';
}

function validId(value) {
  const id = Number(value);
  return Number.isSafeInteger(id) && id > 0 ? id : null;
}

function link(label, href) {
  const anchor = node('a', null, label);
  anchor.href = href;
  return anchor;
}

function pdfAction(row) {
  const id = validId(row.id);
  if (id && row.pdf_status === 'ready') {
    const anchor = link('PDF originale', `/api/crm/stime/${id}/pdf`);
    anchor.target = '_blank';
    anchor.rel = 'noopener noreferrer';
    anchor.referrerPolicy = 'no-referrer';
    return anchor;
  }
  return node('span', 'muted', 'PDF non disponibile');
}

function estimatedValue(row) {
  if (row.price_exact === null || row.price_exact === undefined || row.price_exact === '') return null;
  const value = Number(row.price_exact);
  return Number.isFinite(value) && value >= 0
    ? new Intl.NumberFormat('it-IT', { style: 'currency', currency: 'EUR', maximumFractionDigits: 0 }).format(value)
    : null;
}

function detailBadge(row) {
  return node('span', `stime-status${row.detail_id ? ' is-detailed' : ''}`,
    row.detail_id ? 'Dettagliata' : 'Base');
}

function crmLinks(row, actions) {
  const contactId = validId(row.contact_id);
  const propertyId = validId(row.property_id);
  if (contactId) actions.appendChild(link('Contatto CRM', `#/contatti/${contactId}`));
  if (propertyId) actions.appendChild(link('Immobile CRM', `#/immobili/${propertyId}`));
  actions.appendChild(pdfAction(row));
}

function card(row, route) {
  const id = validId(row.id);
  const article = node('article', 'stime-card');
  const heading = node('div', 'stime-card-heading');
  const open = node('button', 'stime-name-link', name(row));
  open.type = 'button';
  open.addEventListener('click', () => navigate(route, [id]));
  heading.append(open, detailBadge(row));
  article.append(heading, node('p', 'stime-location', address(row)));
  const meta = node('div', 'stime-card-meta');
  meta.append(node('span', null, `${text(row.tipologia)} · ${text(row.mq)} mq`),
    node('span', null, formatDateTime(row.data)));
  const value = estimatedValue(row);
  if (value) meta.appendChild(node('strong', 'stime-estimate', value));
  article.appendChild(meta);
  const actions = node('div', 'stime-actions');
  const button = node('button', 'btn', `Apri stima #${id}`);
  button.type = 'button';
  button.addEventListener('click', () => navigate(route, [id]));
  actions.appendChild(button);
  crmLinks(row, actions);
  article.appendChild(actions);
  return article;
}

function fieldGroup(title, entries) {
  const section = node('section', 'card stime-detail-card');
  section.appendChild(node('h2', null, title));
  const list = node('dl', 'stime-fields');
  for (const [label, value] of entries) {
    const field = node('div', 'stime-field');
    field.append(node('dt', null, label), node('dd', null, text(value)));
    list.appendChild(field);
  }
  section.appendChild(list);
  return section;
}

async function renderDetail(container, rawId, route) {
  const id = validId(rawId);
  if (!id) {
    container.replaceChildren(node('div', 'error-box', 'Identificativo non valido.'));
    return;
  }
  const root = node('div', 'stime-page');
  root.appendChild(node('p', 'muted', 'Caricamento valutazione…'));
  container.replaceChildren(root);
  try {
    const row = await apiGet(`/api/crm/stime/${id}`);
    if (!root.isConnected) return;
    const toolbar = node('div', 'stime-detail-toolbar');
    const back = node('button', 'btn', '← Torna alle stime');
    back.type = 'button';
    back.addEventListener('click', () => navigate(route));
    toolbar.append(back, detailBadge(row));
    const actions = node('div', 'stime-actions stime-detail-actions');
    crmLinks(row, actions);
    const grid = node('div', 'stime-detail-grid');
    grid.append(
      fieldGroup('Valore stimato', [['Valore del calcolo originale', estimatedValue(row)],
        ['PDF', row.pdf_status === 'ready' ? 'Disponibile' : 'Non disponibile']]),
      fieldGroup('Proprietario', [['Nome', name(row)], ['Telefono', row.telefono],
        ['Email', row.email], ['Lead CRM', row.lead_id ? `#${row.lead_id}` : null]]),
      fieldGroup('Immobile', [['Comune', row.comune], ['Microzona', row.microzona],
        ['Via', row.via], ['Civico', row.civico], ['Tipologia', row.tipologia],
        ['Superficie', row.mq == null ? null : `${row.mq} mq`], ['Piano', row.piano],
        ['Locali', row.locali], ['Bagni', row.bagni], ['Ascensore', row.ascensore],
        ['Anno', row.anno], ['Stato', row.stato]]),
      fieldGroup('Pertinenze', [['Descrizione', row.pertinenze], ['Garage mq', row.mqgarage],
        ['Posto auto mq', row.mqpostoauto], ['Cantina mq', row.mqcantina],
        ['Giardino mq', row.mqgiardino], ['Taverna mq', row.mqtaverna],
        ['Soffitta mq', row.mqsoffitta], ['Terrazzo mq', row.mqterrazzo],
        ['Balconi', row.numbalconi]]),
      fieldGroup('Posizione e mare', [['Posizione', row.posizionemare],
        ['Distanza mare', row.distanzamare], ['Barriera', row.barrieramare],
        ['Vista mare', row.vistamare], ['Altre informazioni', row.altrodescrizione]]),
    );
    if (row.detail_id) grid.appendChild(fieldGroup('Stima dettagliata', [
      ['ID dettaglio', row.detail_id], ['Compilata il', formatDateTime(row.detail_data)],
      ['Classe energetica', row.classe], ['Riscaldamento', row.riscaldamento],
      ['Condizionatore', row.condizionatore], ['Spese condominiali', row.spese_cond],
      ['Esposizione', row.esposizione], ['Arredo', row.arredo],
      ['Richiesta contatto', row.contatto],
      ['Sopralluogo', row.sopralluogo ? formatDateTime(row.sopralluogo) : null],
      ['Note', row.detail_note],
    ]));
    root.replaceChildren(toolbar, node('h2', null, `Stima #${id} — ${name(row)}`),
      node('p', 'muted', `${address(row)} · ${formatDateTime(row.data)}`), actions, grid,
      node('p', 'muted stime-value-note',
        'Valore recuperato dal calcolo originale conservato per il PDF. Il CRM non lo ricalcola.'));
  } catch (error) {
    if (root.isConnected) root.replaceChildren(node('div', 'error-box',
      error.message || 'Impossibile aprire la stima'));
  }
}

export async function renderStime(container, params = [], mode = 'all') {
  const route = mode === 'detailed' ? 'stime-dettagliate' : 'stime';
  if (params[0]) return renderDetail(container, params[0], route);

  const root = node('div', 'card stime-page');
  root.appendChild(node('p', 'muted',
    'Una scheda per valutazione. La richiesta dettagliata arricchisce la stessa stima.'));
  const stats = node('div', 'stime-stats');
  const toolbar = node('div', 'list-toolbar stime-toolbar');
  const searchInput = node('input', 'input');
  searchInput.type = 'search';
  searchInput.placeholder = 'Cerca proprietario, città, telefono, indirizzo…';
  searchInput.setAttribute('aria-label', 'Cerca stime');
  toolbar.appendChild(searchInput);
  let view = mode === 'detailed' ? 'detailed' : 'all';
  if (mode === 'all') {
    const select = node('select', 'input');
    select.setAttribute('aria-label', 'Tipo di stima');
    for (const [value, label] of [['all', 'Tutte'], ['base', 'Solo base'], ['detailed', 'Dettagliate']]) {
      const option = node('option', null, label);
      option.value = value;
      select.appendChild(option);
    }
    toolbar.appendChild(select);
    select.addEventListener('change', () => { view = select.value; offset = 0; void load(); });
  }
  const feedback = node('div');
  feedback.setAttribute('role', 'status');
  const list = node('div', 'stime-cards');
  const pager = node('div', 'list-pager');
  root.append(stats, toolbar, feedback, list, pager);
  container.replaceChildren(root);

  let offset = 0;
  let requestId = 0;
  let debounce;
  async function load() {
    const current = ++requestId;
    list.replaceChildren(node('p', 'muted', 'Caricamento stime…'));
    pager.replaceChildren();
    feedback.textContent = '';
    const query = new URLSearchParams({ view, search: searchInput.value.trim(),
      limit: String(PAGE_SIZE), offset: String(offset) });
    try {
      const data = await apiGet(`/api/crm/stime?${query}`);
      if (current !== requestId || !root.isConnected) return;
      stats.replaceChildren();
      const total = Number(data.stats?.total) || 0;
      const detailed = Number(data.stats?.detailed) || 0;
      for (const [label, value] of [['Totali', total], ['Base', total - detailed], ['Dettagliate', detailed]]) {
        const stat = node('div', 'stime-stat');
        stat.append(node('strong', null, value), node('span', null, label));
        stats.appendChild(stat);
      }
      list.replaceChildren();
      if (data.items?.length) {
        for (const row of data.items) list.appendChild(card(row, route));
      } else {
        list.appendChild(node('p', 'muted', 'Nessuna valutazione trovata con questi filtri.'));
      }
      if (offset || data.has_more) {
        const previous = node('button', 'btn', '← Precedenti');
        previous.type = 'button';
        previous.disabled = offset === 0;
        previous.addEventListener('click', () => { offset = Math.max(0, offset - PAGE_SIZE); void load(); });
        const next = node('button', 'btn', 'Successive →');
        next.type = 'button';
        next.disabled = !data.has_more;
        next.addEventListener('click', () => { offset += PAGE_SIZE; void load(); });
        pager.append(previous,
          node('span', 'muted', `${offset + (data.items?.length ? 1 : 0)}–${offset + (data.items?.length || 0)} di ${data.total}`),
          next);
      }
    } catch (error) {
      if (current !== requestId || !root.isConnected) return;
      list.replaceChildren(node('div', 'error-box', error.message || 'Errore nel caricamento'));
    }
  }
  searchInput.addEventListener('input', () => {
    offset = 0;
    clearTimeout(debounce);
    debounce = setTimeout(() => { void load(); }, 300);
  });
  await load();
}
