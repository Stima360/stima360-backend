// STIMA360 OS — acquisizioni.js
// CRM-OPS-3: elenco delle Acquisizioni e creazione (immobile -> proprietari
// reali -> dati commerciali -> appuntamento nell'Agenda -> UN salvataggio).
//
// Fonti, tutte del backend:
//   GET  /api/acquisitions/options   stati, etichette, motivi, tempistiche,
//                                    fonti, agenti (unica fonte: nessuna copia qui)
//   GET  /api/acquisitions           elenco con filtri (visibilita' decisa dal server:
//                                    un agente vede solo le proprie)
//   POST /api/acquisitions           acquisizione + appuntamento in UNA transazione
//   GET  /api/property/properties    ricerca immobile e i suoi proprietari reali
//                                    (`property_contacts`, ruoli owner/seller)
//
// L'appuntamento si compone con il dialog CONDIVISO dell'Agenda (tipo
// `seller_meeting` bloccato, agente obbligatorio, disponibilita'/conflitti):
// la sola scrittura e' la POST /api/acquisitions con lo stesso corpo e la
// stessa `client_request_id` del dialog (stesso schema di A31-4). Nessuna
// seconda Agenda, nessun proprietario duplicato.
import { apiGet, apiPost } from '../core/api-client.js';
import { navigate } from '../core/router.js';
import { renderTable, bindTableRowClicks, renderBadge, escapeHtml, formatDateTime } from '../components/st-table.js';
import { openCreateDialog } from '../components/agenda/agenda-dialogs.js';
import { getAgents } from '../agenda/agenda-api.js';
import { addDays, romeIso, statusLabel, todayKey } from '../agenda/agenda-model.js';
import { getSession } from '../core/auth.js';

const PAGE_SIZE = 50;
const OWNER_ROLES = ['owner', 'seller'];
const ROLE_LABELS = { owner: 'Proprietario', seller: 'Venditore' };

/** Il tono del badge di stato: chiuse in verde/rosso, il resto neutro. */
export function statusTone(status) {
  if (status === 'acquired') return 'ok';
  if (status === 'lost') return 'danger';
  if (status === 'mandate_negotiation' || status === 'valuation_presented') return 'warn';
  return 'gray';
}

export function formatPrice(value) {
  if (value === null || value === undefined || value === '') return '—';
  const n = Number(value);
  if (Number.isNaN(n)) return '—';
  return n.toLocaleString('it-IT', { style: 'currency', currency: 'EUR', maximumFractionDigits: 0 });
}

/** Le opzioni del server, una volta per vista. */
export async function loadAcquisitionOptions() {
  return apiGet('/api/acquisitions/options');
}

/**
 * I proprietari REALI dell'immobile (ruoli owner/seller di
 * `property_contacts`), uno per contatto, il principale per primo.
 */
export function ownersFromProperty(property) {
  const perContatto = new Map();
  for (const c of (property && property.contacts) || []) {
    if (!OWNER_ROLES.includes(c.role)) continue;
    const gia = perContatto.get(c.contact_id);
    if (gia) {
      gia.roles.push(c.role);
      gia.is_primary = gia.is_primary || Boolean(c.is_primary);
    } else {
      perContatto.set(c.contact_id, {
        contact_id: c.contact_id, display_name: c.display_name, phone: c.phone, email: c.email,
        roles: [c.role], is_primary: Boolean(c.is_primary),
      });
    }
  }
  return [...perContatto.values()].sort((a, b) => Number(b.is_primary) - Number(a.is_primary));
}

/** DELETE-ARCH Fase 1A: il filtro esplicito «Creati per errore». */
export const MISTAKES_FILTER = 'mistakes';

/** I parametri dell'elenco: solo i filtri valorizzati. */
export function acquisitionListParams(filters = {}, offset = 0) {
  const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
  if (filters.statuses === MISTAKES_FILTER) params.set('mistakes', 'true');
  else if (filters.statuses) params.set('statuses', filters.statuses);
  if (filters.agentId) params.set('agent_id', String(filters.agentId));
  // Giorni di Roma (stessa regola dell'Agenda): dalla mezzanotte del primo
  // alla mezzanotte del giorno dopo l'ultimo.
  if (filters.from) params.set('from', romeIso(filters.from, 0, 0));
  if (filters.to) params.set('to', romeIso(addDays(filters.to, 1), 0, 0));
  if (filters.city) params.set('city', filters.city);
  if (filters.search) params.set('search', filters.search);
  return params;
}

/**
 * Dal corpo del dialog dell'Agenda al blocco `appointment` della API delle
 * Acquisizioni: tipo e stato li decide il server (`seller_meeting`,
 * `scheduled`), contatto e immobile sono quelli dell'acquisizione.
 */
export function acquisitionAppointmentPayload(corpo) {
  const appuntamento = {
    start_at: corpo.start_at,
    end_at: corpo.end_at,
    assigned_user_id: corpo.assigned_user_id,
    client_request_id: corpo.client_request_id,
  };
  if (corpo.location_text) appuntamento.location_text = corpo.location_text;
  if (corpo.notes) appuntamento.notes = corpo.notes;
  return appuntamento;
}

/** Il corpo della POST /api/acquisitions (senza appuntamento). */
export function acquisitionCreatePayload(dati) {
  const corpo = { property_id: Number(dati.propertyId), owner_contact_id: Number(dati.ownerContactId) };
  // VENDITORI-1: dall'area Venditori arriva anche il lead SELL dell'opportunita'.
  if (dati.leadId) corpo.lead_id = Number(dati.leadId);
  if (dati.askingPrice !== '' && dati.askingPrice !== null && dati.askingPrice !== undefined) corpo.asking_price = String(dati.askingPrice);
  if (dati.valuationPrice !== '' && dati.valuationPrice !== null && dati.valuationPrice !== undefined) corpo.valuation_price = String(dati.valuationPrice);
  if (dati.saleTiming) corpo.sale_timing = dati.saleTiming;
  if (dati.source) corpo.source = dati.source;
  if (dati.notes && dati.notes.trim()) corpo.notes = dati.notes.trim();
  return corpo;
}

function propertyLine(p) {
  const via = [p.address, p.civic_number].filter(Boolean).join(' ');
  return [p.code, p.title, via, p.city].filter(Boolean).join(' · ');
}

function optionsHtml(items, selected, placeholder) {
  const vuota = placeholder !== undefined ? `<option value="">${escapeHtml(placeholder)}</option>` : '';
  return vuota + (items || []).map((i) => `<option value="${escapeHtml(String(i.value))}"${String(i.value) === String(selected ?? '') ? ' selected' : ''}>${escapeHtml(i.label)}</option>`).join('');
}

export async function renderAcquisizioni(container, params = []) {
  container.innerHTML = `
    <div class="card panel">
      <div class="list-toolbar acq-toolbar">
        <input id="acq-search" class="input" type="search" placeholder="Cerca immobile, indirizzo o proprietario…">
        <select id="acq-status" class="input"><option value="">Tutti gli stati</option></select>
        <select id="acq-agent" class="input" hidden><option value="">Tutti gli agenti</option></select>
        <input id="acq-city" class="input" type="text" placeholder="Comune">
        <label class="acq-period">Appuntamento dal <input id="acq-from" class="input" type="date"></label>
        <label class="acq-period">al <input id="acq-to" class="input" type="date"></label>
        <button type="button" id="acq-new" class="btn primary">+ Nuova acquisizione</button>
      </div>
      <div id="acq-list-area"><p class="muted">Caricamento…</p></div>
      <div id="acq-pager" class="list-pager"></div>
    </div>
    <dialog id="acq-new-dialog" class="modal modal-wide"></dialog>
    <dialog id="acq-appointment-dialog" class="modal modal-wide agenda-dialog"></dialog>
  `;

  const $ = (sel) => container.querySelector(sel);
  const listArea = $('#acq-list-area');
  const pagerArea = $('#acq-pager');
  let opzioni;
  try {
    opzioni = await loadAcquisitionOptions();
  } catch (error) {
    listArea.innerHTML = `<div class="error-box">Impossibile caricare le acquisizioni: ${escapeHtml(error.message)}</div>`;
    return;
  }
  const etichette = Object.fromEntries((opzioni.statuses || []).map((s) => [s.value, s.label]));
  const aperte = (opzioni.statuses || []).filter((s) => !s.terminal).map((s) => s.value).join(',');
  $('#acq-status').innerHTML = `<option value="">Tutti gli stati</option><option value="${escapeHtml(aperte)}">Aperte</option>`
    + (opzioni.statuses || []).map((s) => `<option value="${escapeHtml(s.value)}">${escapeHtml(s.label)}</option>`).join('')
    + `<option value="${MISTAKES_FILTER}">Creati per errore</option>`;
  if (opzioni.can_assign) {
    const agenti = $('#acq-agent');
    agenti.hidden = false;
    agenti.innerHTML = '<option value="">Tutti gli agenti</option>'
      + (opzioni.agents || []).map((a) => `<option value="${escapeHtml(String(a.id))}">${escapeHtml(a.name || '')}</option>`).join('');
  }

  let offset = 0;
  let debounce = null;
  const filtri = () => ({
    statuses: $('#acq-status').value, agentId: $('#acq-agent').value, city: $('#acq-city').value.trim(),
    from: $('#acq-from').value, to: $('#acq-to').value, search: $('#acq-search').value.trim(),
  });

  async function load() {
    listArea.innerHTML = '<p class="muted">Caricamento…</p>';
    pagerArea.innerHTML = '';
    const f = filtri();
    let items = [];
    try {
      const data = await apiGet(`/api/acquisitions?${acquisitionListParams(f, offset).toString()}`);
      items = Array.isArray(data?.items) ? data.items : [];
    } catch (error) {
      listArea.innerHTML = `<div class="error-box">Impossibile caricare le acquisizioni: ${escapeHtml(error.message)}</div>`;
      return;
    }
    const filtrato = Object.values(f).some(Boolean);
    listArea.innerHTML = renderTable(
      [
        { label: 'Immobile', render: (r) => `<strong>${escapeHtml(r.property_code || `#${r.property_id}`)}</strong><br><small class="muted">${escapeHtml([r.property_title, [r.property_address, r.property_civic_number].filter(Boolean).join(' '), r.property_city].filter(Boolean).join(' · '))}</small>` },
        { label: 'Proprietario', render: (r) => escapeHtml(r.owner_name || '—') },
        { label: 'Agente', render: (r) => escapeHtml(r.agent_name || '—') },
        { label: 'Appuntamento', render: (r) => escapeHtml(formatDateTime(r.appointment_start_at)) },
        { label: 'Stato appuntamento', render: (r) => escapeHtml(statusLabel(r.appointment_status) || r.appointment_status || '—') },
        { label: 'Stato', render: (r) => renderBadge(r.lost_reason === 'created_by_mistake' ? r.status_label : (etichette[r.status] || r.status_label || r.status), statusTone(r.status)) },
        { label: 'Prezzo richiesto', render: (r) => formatPrice(r.asking_price) },
        { label: 'Ultima attività', render: (r) => escapeHtml(formatDateTime(r.last_activity_at)) },
      ],
      items,
      { emptyMessage: filtrato ? 'Nessuna acquisizione per questi filtri.' : 'Nessuna acquisizione.', onRowClick: true },
    );
    bindTableRowClicks(listArea, (id) => navigate('acquisizioni', [id]));
    pagerArea.innerHTML = `
      <button class="btn" id="acq-prev" ${offset === 0 ? 'disabled' : ''}>← Precedenti</button>
      <span class="muted">Risultati da ${items.length ? offset + 1 : 0} a ${offset + items.length}</span>
      <button class="btn" id="acq-next" ${items.length < PAGE_SIZE ? 'disabled' : ''}>Successivi →</button>`;
    pagerArea.querySelector('#acq-prev').addEventListener('click', () => { offset = Math.max(0, offset - PAGE_SIZE); load(); });
    pagerArea.querySelector('#acq-next').addEventListener('click', () => { offset += PAGE_SIZE; load(); });
  }

  const ricarica = () => { offset = 0; load(); };
  for (const sel of ['#acq-search', '#acq-city']) {
    $(sel).addEventListener('input', () => { clearTimeout(debounce); debounce = setTimeout(ricarica, 300); });
  }
  for (const sel of ['#acq-status', '#acq-agent', '#acq-from', '#acq-to']) $(sel).addEventListener('change', ricarica);
  $('#acq-new').addEventListener('click', () => openNewAcquisition(container, opzioni));

  await load();
  // `#/acquisizioni/nuova/<property_id>`: dalla scheda immobile ("Avvia acquisizione").
  // VENDITORI-1: `.../nuova/<property_id>/<owner_contact_id>/<lead_id>` dall'area
  // Venditori - stesso dialog, proprietario preselezionato e lead nel payload.
  if (params[0] === 'nuova' && params[1]) {
    openNewAcquisition(container, opzioni, { propertyId: params[1], ownerContactId: params[2] || null, leadId: params[3] || null });
  }
}

/**
 * Passo 1 (dialog della vista): immobile, proprietario principale fra quelli
 * REALI, dati commerciali. Passo 2: il dialog dell'Agenda, la cui conferma
 * e' l'UNICA scrittura.
 */
export async function openNewAcquisition(container, opzioni, { propertyId = null, ownerContactId = null, leadId = null } = {}) {
  const dialogEl = container.querySelector('#acq-new-dialog');
  if (!dialogEl) return;
  // LO STATO DELLA MODALE, esplicito. `property` e' l'immobile SCELTO (un
  // click su un risultato, mai il testo digitato); `owners` i suoi proprietari
  // reali; `caricamento` il numero d'ordine dell'ultima scelta, cosi' una
  // risposta arrivata in ritardo per una scelta precedente viene ignorata;
  // `prezzoProposto` il prezzo precompilato dall'immobile, da togliere se
  // l'immobile cambia e l'operatore non l'ha toccato.
  const stato = { property: null, owners: [], caricamento: 0, prezzoProposto: null, leadId: null };
  dialogEl.innerHTML = `
    <form class="acq-form" novalidate>
      <h3 class="section-title">Nuova acquisizione</h3>
      <div class="form-field"><label for="acq-property-search">Immobile *</label>
        <input id="acq-property-search" class="input" type="search" placeholder="Cerca per codice, indirizzo o comune…" autocomplete="off">
        <small class="muted" id="acq-property-hint">Cerca e poi scegli un risultato: il testo da solo non seleziona nessun immobile.</small>
        <div id="acq-property-results" class="acq-results" role="listbox"></div>
        <div id="acq-property-selected" class="acq-selected" hidden></div></div>
      <div class="form-field"><label>Proprietario principale *</label><div id="acq-owners" class="acq-owners"><p class="muted">Scegli prima l’immobile.</p></div></div>
      <div class="form-grid-2">
        <div class="form-field"><label for="acq-asking">Prezzo richiesto (€)</label><input id="acq-asking" class="input" type="number" min="0" step="0.01"></div>
        <div class="form-field"><label for="acq-valuation">Valutazione (€)</label><input id="acq-valuation" class="input" type="number" min="0" step="0.01"></div>
        <div class="form-field"><label for="acq-timing">Tempistica di vendita</label><select id="acq-timing" class="input">${optionsHtml(opzioni.sale_timings, '', 'Non indicata')}</select></div>
        <div class="form-field"><label for="acq-source">Fonte</label><select id="acq-source" class="input">${optionsHtml(opzioni.sources, '', 'Non indicata')}</select></div>
      </div>
      <div class="form-field"><label for="acq-notes">Note commerciali</label><textarea id="acq-notes" class="input" rows="3" maxlength="5000"></textarea></div>
      <div class="field-error" id="acq-new-error" role="alert"></div>
      <div class="modal-actions">
        <button type="button" class="btn ghost" id="acq-new-cancel">Chiudi</button>
        <button type="submit" class="btn primary" id="acq-new-next" disabled>Continua: fissa appuntamento</button>
      </div>
    </form>`;
  const $ = (sel) => dialogEl.querySelector(sel);
  const errore = $('#acq-new-error');
  $('#acq-new-cancel').addEventListener('click', () => dialogEl.close());

  /** L'UNICA fonte di `disabled` per «Continua»: attivo solo con un immobile
   *  scelto e caricato, almeno un proprietario owner|seller e, nel menu, un
   *  proprietario che appartiene alla lista corrente. Il submit ricontrolla
   *  tutto e il backend resta autoritativo. */
  function aggiornaContinua() {
    const scelto = $('#acq-owner');
    const valido = !!stato.property && stato.owners.length > 0 && !!scelto && !!scelto.value
      && stato.owners.some((o) => String(o.contact_id) === String(scelto.value));
    $('#acq-new-next').disabled = !valido;
  }

  /** Nessun immobile scelto: lo stato precedente non sopravvive. */
  function azzeraSelezione() {
    stato.property = null;
    stato.owners = [];
    stato.leadId = null;
    const selezionato = $('#acq-property-selected');
    selezionato.hidden = true;
    selezionato.innerHTML = '';
    delete selezionato.dataset.propertyId;
    $('#acq-owners').innerHTML = '<p class="muted">Scegli prima l’immobile.</p>';
    const prezzo = $('#acq-asking');
    if (stato.prezzoProposto !== null && prezzo.value === stato.prezzoProposto) prezzo.value = '';
    stato.prezzoProposto = null;
    aggiornaContinua();
  }

  async function scegliImmobile(id) {
    errore.textContent = '';
    azzeraSelezione();
    const mio = ++stato.caricamento;
    $('#acq-property-results').innerHTML = '';
    $('#acq-owners').innerHTML = '<p class="muted">Caricamento proprietari…</p>';
    let immobile;
    try {
      immobile = await apiGet(`/api/property/properties/${encodeURIComponent(id)}`);
    } catch (error) {
      if (mio !== stato.caricamento) return;          // nel frattempo si e' scelto altro
      azzeraSelezione();
      errore.textContent = error.message || 'Immobile non disponibile.';
      return;
    }
    if (mio !== stato.caricamento) return;            // risposta vecchia: non vale piu'
    stato.property = immobile;
    stato.owners = ownersFromProperty(immobile);
    // L'immobile SCELTO, in evidenza e con il suo id: e' lui che finisce nel payload.
    const selezionato = $('#acq-property-selected');
    selezionato.dataset.propertyId = String(immobile.id);
    selezionato.innerHTML = `
      <span class="acq-selected-label">Immobile selezionato</span>
      <strong>${escapeHtml(propertyLine(immobile))}</strong>
      <button type="button" class="btn ghost" id="acq-property-change">Cambia immobile</button>`;
    selezionato.hidden = false;
    $('#acq-property-change').addEventListener('click', () => {
      azzeraSelezione();
      const cerca = $('#acq-property-search');
      cerca.value = '';
      cerca.focus();
    });
    if (immobile.asking_price !== null && immobile.asking_price !== undefined) {
      stato.prezzoProposto = String(immobile.asking_price);
      $('#acq-asking').value = stato.prezzoProposto;
    }
    if (!stato.owners.length) {
      aggiornaContinua();
      // Stessa fonte della scheda Immobile (tab Proprietari = property_contacts).
      // Il ruolo «Proprietario» sulla scheda del CONTATTO (contact_roles) non
      // collega il contatto a nessun immobile: il collegamento si fa dalla
      // scheda immobile con «Collega contatto» (ruolo Proprietario o Venditore).
      $('#acq-owners').innerHTML = `<p class="field-error" id="acq-no-owner">L’immobile non ha proprietari collegati (scheda Immobile → Proprietari). Il ruolo «Proprietario» sulla scheda del contatto non basta: apri la <a href="#/immobili/${encodeURIComponent(stato.property.id)}">scheda immobile</a> e usa «Collega contatto» con ruolo Proprietario o Venditore.</p>`;
      return;
    }
    // Un menu, non dei radio: si legge e si sceglie allo stesso modo su
    // smartphone, e sotto restano visibili tutti i proprietari con i recapiti.
    $('#acq-owners').innerHTML = `
      <select id="acq-owner" class="input">${stato.owners.map((o) => `<option value="${escapeHtml(String(o.contact_id))}">${escapeHtml(o.display_name || `Contatto #${o.contact_id}`)} — ${escapeHtml(o.roles.map((r) => ROLE_LABELS[r] || r).join(', '))}</option>`).join('')}</select>
      <ul class="acq-owners-list">${stato.owners.map((o) => `<li class="acq-owner-option"><strong>${escapeHtml(o.display_name || `Contatto #${o.contact_id}`)}</strong>
        <small class="muted">${escapeHtml(o.roles.map((r) => ROLE_LABELS[r] || r).join(', '))}${o.phone ? ` · ${escapeHtml(o.phone)}` : ''}${o.email ? ` · ${escapeHtml(o.email)}` : ''}</small></li>`).join('')}</ul>`;
    $('#acq-owner').addEventListener('change', () => { stato.leadId = null; aggiornaContinua(); });
    aggiornaContinua();
  }

  let giro = 0;
  $('#acq-property-search').addEventListener('input', async (e) => {
    const testo = e.target.value.trim();
    const mio = ++giro;
    const risultati = $('#acq-property-results');
    // Cambiare il testo riapre la scelta: l'immobile scelto prima non vale
    // piu', e nemmeno un dettaglio ancora in arrivo per una scelta precedente.
    stato.caricamento += 1;
    azzeraSelezione();
    if (testo.length < 2) { risultati.innerHTML = ''; return; }
    try {
      const data = await apiGet(`/api/property/properties?${new URLSearchParams({ search: testo, limit: '10' }).toString()}`);
      if (mio !== giro) return;
      const items = Array.isArray(data?.items) ? data.items : [];
      risultati.innerHTML = items.length
        ? `<p class="muted acq-results-title">Risultati: scegli l’immobile</p>` + items.map((p) => `<button type="button" class="btn ghost acq-result" role="option" data-property-id="${escapeHtml(String(p.id))}">${escapeHtml(propertyLine(p))}</button>`).join('')
        : '<p class="muted">Nessun immobile trovato.</p>';
      for (const b of risultati.querySelectorAll('[data-property-id]')) {
        b.addEventListener('click', () => scegliImmobile(b.dataset.propertyId));
      }
    } catch (error) {
      if (mio === giro) risultati.innerHTML = `<p class="field-error">${escapeHtml(error.message)}</p>`;
    }
  });

  dialogEl.querySelector('form').addEventListener('submit', async (evento) => {
    evento.preventDefault();
    errore.textContent = '';
    if (!stato.property) { errore.textContent = 'Scegli l’immobile.'; return; }
    if (!stato.owners.length) { errore.textContent = 'L’immobile non ha proprietari collegati.'; return; }
    const scelto = $('#acq-owner');
    if (!scelto || !scelto.value || !stato.owners.some((o) => String(o.contact_id) === String(scelto.value))) {
      errore.textContent = 'Scegli il proprietario principale.'; aggiornaContinua(); return;
    }
    const dati = acquisitionCreatePayload({
      propertyId: stato.property.id, ownerContactId: scelto.value, leadId: stato.leadId,
      askingPrice: $('#acq-asking').value, valuationPrice: $('#acq-valuation').value,
      saleTiming: $('#acq-timing').value, source: $('#acq-source').value, notes: $('#acq-notes').value,
    });
    let agenti;
    try {
      const esito = await getAgents();
      agenti = (esito && esito.items) || [];
    } catch (error) {
      errore.textContent = `Impossibile caricare gli agenti: ${error.message || 'errore sconosciuto'}`;
      return;
    }
    dialogEl.close();
    openAcquisitionAppointmentDialog(container, opzioni, agenti, {
      title: 'Appuntamento di acquisizione',
      // REV 2 (R2): il client condiviso mette il `detail` del backend nel
      // messaggio; il dialog dell'Agenda lo legge da `detail`. Senza questo un
      // rifiuto 422 (es. SELLER_LEAD_MISMATCH) diventava «Dati non validi».
      submit: (corpo) => apiPost('/api/acquisitions', { ...dati, appointment: acquisitionAppointmentPayload(corpo) })
        .catch((error) => { if (error && error.detail === undefined) error.detail = error.message; throw error; }),
      onDone: (creata) => navigate('acquisizioni', [creata.id]),
    });
  });

  aggiornaContinua();
  dialogEl.showModal();
  if (propertyId) await scegliImmobile(propertyId);
  // VENDITORI-1: il proprietario dell'opportunita', SOLO se e' davvero fra i
  // proprietari reali dell'immobile appena caricati (mai inventato); il lead
  // viaggia solo insieme a lui.
  const menu = $('#acq-owner');
  if (ownerContactId && menu && stato.owners.some((o) => String(o.contact_id) === String(ownerContactId))) {
    menu.value = String(ownerContactId);
    stato.leadId = leadId;
    const fonte = $('#acq-source');
    if (fonte && [...fonte.querySelectorAll('option')].some((o) => o.getAttribute('value') === 'seller_lead')) fonte.value = 'seller_lead';
    aggiornaContinua();
  }
}

/** Il dialog CONDIVISO dell'Agenda, configurato per un appuntamento
 *  `seller_meeting` di un'acquisizione. `submit` e' l'unica scrittura. */
export function openAcquisitionAppointmentDialog(container, opzioni, agenti, { title, submit, onDone }) {
  const dialogEl = container.querySelector('#acq-appointment-dialog');
  if (!dialogEl) return;
  openCreateDialog(dialogEl, {
    agents: agenti,
    dateKey: todayKey(),
    session: getSession(),
    title,
    appointmentType: opzioni.appointment_type || 'seller_meeting',
    lockAppointmentType: true,
    durationMinutes: opzioni.default_duration_minutes || 60,
    requireAgent: true,
    crmMode: 'none',
    showLocation: true,
    showNotes: true,
    submitAppointment: submit,
    onDone,
  });
}
