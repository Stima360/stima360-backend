// STIMA360 OS — incarichi.js
// CRM-OPS-4: l'elenco INCARICHI. Una vista, non un archivio: compaiono da
// soli gli immobili il cui incarico e' nato da un'acquisizione
// (`properties.acquisition_id` con tipo e data di inizio). Nessun
// "Nuovo incarico" qui: l'incarico si genera SOLO dalla scheda Acquisizione.
//
// Fonti, tutte del backend:
//   GET /api/property/mandates       elenco con filtri e scadenze (giorno di Roma)
//   GET /api/property/form-options   agenti (solo a chi puo' assegnare)
import { apiGet } from '../core/api-client.js';
import { navigate } from '../core/router.js';
import { renderTable, bindTableRowClicks, renderBadge, escapeHtml, formatDate } from '../components/st-table.js';
import { formatInteractionTime } from '../components/property-interactions.js';

const PAGE_SIZE = 50;
export const EXPIRY_LABELS = {
  within_7: 'Scadenza entro 7 giorni', within_15: 'Entro 15 giorni',
  within_30: 'Entro 30 giorni', expired: 'Scaduti',
};
export const COMMERCIAL_STATUS_LABELS = {
  draft: 'Bozza', evaluation: 'In valutazione', mandate: 'Mandato', active: 'Attivo',
  reserved: 'Riservato', under_offer: 'Sotto offerta', sold: 'Venduto',
  withdrawn: 'Ritirato', archived: 'Archiviato',
};
export const SORT_LABELS = { expiry: 'Scadenza più vicina', start: 'Inizio più recente', last_interaction: 'Ultima interazione' };

export function formatPrice(value) {
  if (value === null || value === undefined || value === '') return '—';
  const n = Number(value);
  if (Number.isNaN(n)) return '—';
  return n.toLocaleString('it-IT', { style: 'currency', currency: 'EUR', maximumFractionDigits: 0 });
}

/** "Scade oggi", "3 giorni", "Scaduto da 5 giorni", "Senza scadenza". */
export function expiryText(days) {
  if (days === null || days === undefined) return 'Senza scadenza';
  if (days < 0) return `Scaduto da ${-days} ${-days === 1 ? 'giorno' : 'giorni'}`;
  if (days === 0) return 'Scade oggi';
  return `${days} ${days === 1 ? 'giorno' : 'giorni'}`;
}

export function expiryTone(days) {
  if (days === null || days === undefined) return 'gray';
  if (days < 0) return 'danger';
  if (days <= 7) return 'danger';
  if (days <= 30) return 'warn';
  return 'ok';
}

export function mandateListParams(f = {}, offset = 0) {
  const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
  for (const [chiave, valore] of [['search', f.search], ['agent_id', f.agentId], ['city', f.city],
    ['mandate_type', f.mandateType], ['commercial_status', f.status], ['expiry', f.expiry]]) {
    if (valore) params.set(chiave, String(valore));
  }
  if (f.sort && f.sort !== 'expiry') params.set('sort', f.sort);
  return params;
}

function opzioni(items, vuota) {
  return `<option value="">${escapeHtml(vuota)}</option>`
    + items.map(([v, l]) => `<option value="${escapeHtml(String(v))}">${escapeHtml(l)}</option>`).join('');
}

export async function renderIncarichi(container) {
  container.innerHTML = `
    <div class="card panel">
      <p class="muted">Gli incarichi nascono solo da un’acquisizione («Genera incarico»): qui compaiono da soli.</p>
      <div class="list-toolbar inc-toolbar">
        <input id="inc-search" class="input" type="search" placeholder="Cerca immobile, codice, indirizzo o proprietario…">
        <select id="inc-expiry" class="input">${opzioni(Object.entries(EXPIRY_LABELS), 'Tutte le scadenze')}</select>
        <select id="inc-status" class="input">${opzioni(Object.entries(COMMERCIAL_STATUS_LABELS), 'Stato commerciale')}</select>
        <select id="inc-type" class="input"><option value="">Tipo incarico</option></select>
        <select id="inc-agent" class="input" hidden><option value="">Tutti gli agenti</option></select>
        <input id="inc-city" class="input" type="text" placeholder="Comune">
        <select id="inc-sort" class="input">${Object.entries(SORT_LABELS).map(([v, l]) => `<option value="${v}">${escapeHtml(l)}</option>`).join('')}</select>
      </div>
      <div id="inc-list-area"><p class="muted">Caricamento…</p></div>
      <div id="inc-pager" class="list-pager"></div>
    </div>`;
  const $ = (sel) => container.querySelector(sel);
  const listArea = $('#inc-list-area');
  const pagerArea = $('#inc-pager');
  let offset = 0;
  let debounce = null;
  let tipiCaricati = false;

  // Gli agenti: solo per chi puo' assegnare (stessa regola di CRM-OPS-2);
  // per gli altri il filtro non c'e', e la richiesta non parte.
  apiGet('/api/property/form-options').then((o) => {
    if (o && o.can_assign && Array.isArray(o.agents) && o.agents.length) {
      const sel = $('#inc-agent');
      if (!sel) return;
      sel.hidden = false;
      sel.innerHTML = opzioni(o.agents.map((a) => [a.id, a.name || `Agente #${a.id}`]), 'Tutti gli agenti');
    }
  }).catch(() => {});

  const filtri = () => ({
    search: $('#inc-search').value.trim(), expiry: $('#inc-expiry').value, status: $('#inc-status').value,
    mandateType: $('#inc-type').value, agentId: $('#inc-agent').value, city: $('#inc-city').value.trim(),
    sort: $('#inc-sort').value,
  });

  async function load() {
    listArea.innerHTML = '<p class="muted">Caricamento…</p>';
    pagerArea.innerHTML = '';
    const f = filtri();
    let data;
    try {
      data = await apiGet(`/api/property/mandates?${mandateListParams(f, offset).toString()}`);
    } catch (error) {
      listArea.innerHTML = `<div class="error-box">Impossibile caricare gli incarichi: ${escapeHtml(error.message)}</div>`;
      return;
    }
    const items = Array.isArray(data?.items) ? data.items : [];
    if (!tipiCaricati && Array.isArray(data?.mandate_types)) {
      tipiCaricati = true;
      $('#inc-type').innerHTML = opzioni(data.mandate_types.map((t) => [t, t]), 'Tipo incarico');
    }
    const filtrato = Object.entries(f).some(([k, v]) => k !== 'sort' && v);
    listArea.innerHTML = renderTable(
      [
        { label: 'Immobile', render: (r) => `<strong>${escapeHtml(r.code || `#${r.property_id}`)}</strong><br><small class="muted">${escapeHtml([r.title, [r.address, r.civic_number].filter(Boolean).join(' '), r.city].filter(Boolean).join(' · '))}</small>` },
        { label: 'Proprietario', render: (r) => `${escapeHtml(r.main_owner_name || '—')}${r.other_owners && r.other_owners.length ? `<br><small class="muted">+ ${escapeHtml(r.other_owners.map((o) => o.display_name).join(', '))}</small>` : ''}` },
        { label: 'Agente', render: (r) => escapeHtml(r.agent_name || '—') },
        { label: 'Tipo', render: (r) => escapeHtml(r.mandate_type || '—') },
        { label: 'Inizio', render: (r) => escapeHtml(formatDate(r.mandate_start)) },
        { label: 'Scadenza', render: (r) => `${escapeHtml(formatDate(r.mandate_end))}<br>${renderBadge(expiryText(r.days_to_expiry), expiryTone(r.days_to_expiry))}` },
        { label: 'Prezzo', render: (r) => `${formatPrice(r.asking_price)}${r.minimum_price ? `<br><small class="muted">min ${formatPrice(r.minimum_price)}</small>` : ''}` },
        { label: 'Stato', render: (r) => renderBadge(COMMERCIAL_STATUS_LABELS[r.commercial_status] || r.commercial_status || '—', 'gray') },
        { label: 'Acquisizione', render: (r) => `#${escapeHtml(String(r.acquisition.id))}` },
        { label: 'Ultima interazione', render: (r) => (r.last_interaction ? `${escapeHtml(r.last_interaction.type_label || '')}<br><small class="muted">${escapeHtml(formatInteractionTime(r.last_interaction.occurred_at))}</small>` : '<span class="muted">Nessuna</span>') },
      ],
      items.map((r) => ({ ...r, id: r.property_id })),
      { emptyMessage: filtrato ? 'Nessun incarico per questi filtri.' : 'Nessun incarico: si generano dalla scheda di un’acquisizione.', onRowClick: true },
    );
    bindTableRowClicks(listArea, (id) => navigate('incarichi', [id]));
    pagerArea.innerHTML = `
      <button class="btn" id="inc-prev" ${offset === 0 ? 'disabled' : ''}>← Precedenti</button>
      <span class="muted">Risultati da ${items.length ? offset + 1 : 0} a ${offset + items.length}</span>
      <button class="btn" id="inc-next" ${items.length < PAGE_SIZE ? 'disabled' : ''}>Successivi →</button>`;
    pagerArea.querySelector('#inc-prev').addEventListener('click', () => { offset = Math.max(0, offset - PAGE_SIZE); load(); });
    pagerArea.querySelector('#inc-next').addEventListener('click', () => { offset += PAGE_SIZE; load(); });
  }

  const ricarica = () => { offset = 0; load(); };
  for (const sel of ['#inc-search', '#inc-city']) {
    $(sel).addEventListener('input', () => { clearTimeout(debounce); debounce = setTimeout(ricarica, 300); });
  }
  for (const sel of ['#inc-expiry', '#inc-status', '#inc-type', '#inc-agent', '#inc-sort']) $(sel).addEventListener('change', ricarica);
  await load();
}
