// STIMA360 OS — contatto-dettaglio.js
// Scheda Contatto: riusa Contact 360 esistente (GET /api/crm/contacts/{id}/360,
// crm/router.py:10-15) come unica fonte per Panoramica, Richieste, Abbinamenti,
// Visite, Attivita, Task (tutti gia' presenti nella risposta, crm/service.py).
// Nessuna riaggregazione lato frontend di dati che il backend fornisce gia'.
//
// Due tab richiedono chiamate aggiuntive, SOLO verso endpoint reali ed esistenti,
// caricate on-demand (al primo click sulla tab, non al caricamento iniziale):
//  - Immobili / Documenti: GET /api/property/properties/{id} per ciascun immobile
//    in Contact360.properties (property/router.py:19-20), da cui si legge
//    p.contacts (ruolo reale in property_contacts) e p.documents (property/repository.py:62-96).
//  - Stime: GET /api/core/leads/{id} per ciascun lead in Contact360.leads
//    (core/router.py, get_lead -> core/repository.py:150-158), da cui si legge
//    lead.estimations (righe reali della tabella lead_stime). Non esiste un
//    endpoint per leggere una singola stima per id (verificato: main.py espone
//    solo GET /api/admin/stime, filtrato per data, e POST .../update, mai un
//    GET per id) quindi qui si mostra solo id/relazione/data del collegamento,
//    non i dettagli della stima (indirizzo, tipologia, ecc.).
//
// I ruoli mostrati in Panoramica vengono ESCLUSIVAMENTE da Contact360.roles
// (tabella contact_roles), mai dedotti dalla presenza di lead/buy_request/immobili.
// Le relazioni operative (numero di lead, richieste, immobili collegati) sono
// mostrate a parte, chiaramente etichettate come conteggi e non come ruoli.

import { apiGet } from '../core/api-client.js';
import { renderTable, renderBadge, escapeHtml, formatDate, formatDateTime } from '../components/st-table.js';
import {
  createBuyerPressureCache,
  hydrateBuyerPressure,
} from '../components/buyer-pressure.js';
import { loadSellerTimeline, renderSellerTimeline } from '../components/timeline.js';
import { mountInvisibleSale } from '../components/invisible-sale.js';
// P25.1: azioni rapide "Nuova attività"/"Nuovo task" con contact_id
// precompilato (e lead_id selezionabile tra i lead già caricati in
// data.leads, nessuna nuova chiamata). Stessi dialog condivisi già usati da
// attivita.js — vedi components/activity-task-dialogs.js.
import { openNewActivityDialog, openNewTaskDialog } from '../components/activity-task-dialogs.js';

const ROLE_LABELS = {
  owner: 'Proprietario', seller: 'Venditore', buyer: 'Acquirente', prospect: 'Potenziale cliente',
  referrer: 'Segnalatore', agency: 'Agenzia', professional: 'Professionista', other: 'Altro',
};

const PROPERTY_ROLE_LABELS = {
  owner: 'Proprietario', seller: 'Venditore', tenant: 'Inquilino', contact: 'Referente',
  professional: 'Professionista', other: 'Altro',
};

const SELLER_INTENT_BAND_LABELS = {
  freddo: 'Freddo',
  tiepido: 'Tiepido',
  caldo: 'Caldo',
  molto_caldo: 'Molto caldo',
};

const SELLER_STAGE_LABELS = {
  new: 'Nuovo',
  contacted: 'Contattato',
  qualified: 'Qualificato',
  appointment: 'Appuntamento',
  proposal: 'Proposta',
  won: 'Convertito',
  lost: 'Da recuperare',
};

const SELLER_INTENT_STATE_LABELS = {
  active: 'Attivo',
  paused: 'In pausa',
  converted: 'Convertito',
  da_recuperare: 'Da recuperare',
};

const TABS = [
  { key: 'panoramica', label: 'Panoramica' },
  { key: 'timeline', label: 'Timeline' },
  { key: 'immobili', label: 'Immobili' },
  { key: 'richieste', label: 'Richieste' },
  { key: 'stime', label: 'Stime' },
  { key: 'abbinamenti', label: 'Abbinamenti' },
  { key: 'visite', label: 'Visite' },
  { key: 'attivita', label: 'Attività' },
  { key: 'task', label: 'Task' },
  { key: 'documenti', label: 'Documenti' },
];

export async function renderContattoDettaglio(container, params = []) {
  const contactId = params[0];
  if (!contactId || !/^\d+$/.test(String(contactId))) {
    container.innerHTML = '<div class="error-box">Identificativo contatto non valido.</div>';
    return;
  }

  container.innerHTML = '<p class="muted">Caricamento scheda contatto…</p>';

  let data;
  try {
    data = await apiGet(`/api/crm/contacts/${contactId}/360`);
  } catch (error) {
    const notFound = /non trovato|not found/i.test(error.message || '');
    container.innerHTML = `<div class="error-box">${notFound ? 'Contatto non trovato.' : `Errore nel caricamento del contatto: ${escapeHtml(error.message)}`}</div>`;
    return;
  }

  // Cache locale per le due tab a caricamento posticipato: evita di richiamare
  // le stesse API se l'operatore passa più volte da una tab all'altra.
  const lazyCache = {
    properties: null,
    leadsWithEstimations: null,
    sellerIntentByLead: null,
    sellerIntentPromise: null,
    timelineResult: null,
    timelinePromise: null,
    buyerPressure: createBuyerPressureCache(),
  };

  const contact = data.contact || {};
  const name = contact.display_name || fallbackName(contact);

  container.innerHTML = `
    <div class="contact-header card">
      <h2>${escapeHtml(name)}</h2>
      <div class="muted">Contatto #${escapeHtml(contact.id)} · ${escapeHtml(contact.contact_type === 'company' ? 'Azienda' : 'Persona')}</div>
      <div id="contact-role-badges" class="badge-row"></div>
      <div class="action-bar" style="margin-top:8px">
        <button type="button" id="contact-quick-activity" class="btn ghost">+ Nuova attività</button>
        <button type="button" id="contact-quick-task" class="btn ghost">+ Nuovo task</button>
      </div>
    </div>
    <div class="tabs" id="contact-tabs"></div>
    <div id="contact-tab-content" class="card panel"></div>
    <dialog id="contact-activity-dialog" class="modal"></dialog>
    <dialog id="contact-task-dialog" class="modal"></dialog>
  `;

  const badgeRow = container.querySelector('#contact-role-badges');
  const roles = Array.isArray(data.roles) ? data.roles : [];
  badgeRow.innerHTML = roles.length
    ? roles.map((r) => renderBadge(ROLE_LABELS[r.role] || r.role, 'role')).join('')
    : '<span class="muted">Nessun ruolo assegnato in anagrafica.</span>';

  // P25.1: azioni rapide, sempre visibili indipendentemente dalla tab attiva.
  // contact_id è sempre precompilato (mai richiesto), il lead è selezionabile
  // tra data.leads già caricato dal Contact 360 (nessuna nuova chiamata per
  // popolare il selettore).
  async function reloadTasksAndActivities() {
    try {
      const fresh = await apiGet(`/api/crm/contacts/${contact.id}/360`);
      data.activities = fresh.activities;
      data.tasks = fresh.tasks;
    } catch (_error) {
      // best-effort: la creazione è già avvenuta lato backend (il dialog ha
      // già mostrato l'esito), un errore di refresh qui non deve nascondere
      // il successo già ottenuto — stesso principio di reloadPropertyStatus
      // in immobile-dettaglio.js.
    }
    await showTab(activeTab);
  }

  container.querySelector('#contact-quick-activity').addEventListener('click', () => {
    openNewActivityDialog(container.querySelector('#contact-activity-dialog'), {
      presetContact: { id: contact.id, label: name },
      presetLeads: data.leads,
      onSuccess: reloadTasksAndActivities,
    });
  });
  container.querySelector('#contact-quick-task').addEventListener('click', () => {
    openNewTaskDialog(container.querySelector('#contact-task-dialog'), {
      presetContact: { id: contact.id, label: name },
      presetLeads: data.leads,
      onSuccess: reloadTasksAndActivities,
    });
  });

  const tabsEl = container.querySelector('#contact-tabs');
  tabsEl.innerHTML = TABS.map((t, i) => `<button type="button" class="tab-btn ${i === 0 ? 'active' : ''}" data-tab="${t.key}">${escapeHtml(t.label)}</button>`).join('');

  const contentEl = container.querySelector('#contact-tab-content');
  let activeTab = 'panoramica';

  async function showTab(key) {
    activeTab = key;
    tabsEl.querySelectorAll('.tab-btn').forEach((b) => b.classList.toggle('active', b.dataset.tab === key));
    contentEl.innerHTML = '<p class="muted">Caricamento…</p>';
    try {
      switch (key) {
        case 'panoramica':
          contentEl.innerHTML = renderPanoramica(contact, data);
          void hydrateSellerIntent(contentEl, data, lazyCache);
          void hydrateBuyerPressure(
            contentEl.querySelector('[data-buyer-pressure-mount]'),
            data.leads,
            lazyCache.buyerPressure,
            () => loadLeadEstimationsLazy(data.leads, lazyCache),
          );
          void hydrateInvisibleSale(
            contentEl.querySelector('[data-invisible-sale-mount]'),
            data.leads,
            lazyCache,
          );
          break;
        case 'timeline': {
          contentEl.innerHTML = renderSellerTimeline({ status: 'loading' });
          const timeline = await loadSellerTimeline(contact.id, lazyCache);
          if (activeTab === 'timeline') contentEl.innerHTML = renderSellerTimeline(timeline);
          break;
        }
        case 'richieste': contentEl.innerHTML = renderRichieste(data.buy_requests); break;
        case 'abbinamenti': contentEl.innerHTML = renderAbbinamenti(data.matches); break;
        case 'visite': contentEl.innerHTML = renderVisite(data.visits); break;
        case 'attivita': contentEl.innerHTML = renderAttivita(data.activities); break;
        case 'task': contentEl.innerHTML = renderTask(data.tasks); break;
        case 'immobili': {
          const properties = await loadPropertiesLazy(data.properties, contact.id, lazyCache);
          contentEl.innerHTML = renderImmobili(properties);
          break;
        }
        case 'documenti': {
          const properties = await loadPropertiesLazy(data.properties, contact.id, lazyCache);
          contentEl.innerHTML = renderDocumenti(properties);
          break;
        }
        case 'stime': {
          const leads = await loadLeadEstimationsLazy(data.leads, lazyCache);
          contentEl.innerHTML = renderStime(leads);
          break;
        }
        default: contentEl.innerHTML = '<p class="muted">Sezione non disponibile.</p>';
      }
    } catch (error) {
      contentEl.innerHTML = `<div class="error-box">Errore nel caricamento della sezione: ${escapeHtml(error.message)}</div>`;
    }
  }

  tabsEl.querySelectorAll('.tab-btn').forEach((btn) => {
    btn.addEventListener('click', () => showTab(btn.dataset.tab));
  });

  await showTab('panoramica');
}

function fallbackName(contact) {
  if (contact.contact_type === 'company') return contact.company_name || `Contatto #${contact.id}`;
  const parts = [contact.first_name, contact.last_name].filter(Boolean);
  return parts.length ? parts.join(' ') : `Contatto #${contact.id}`;
}

// --- Panoramica -------------------------------------------------------

function renderPanoramica(contact, data) {
  const fields = [
    ['Email', contact.email], ['Telefono', contact.phone], ['Secondo telefono', contact.secondary_phone],
    ['Fonte', contact.source], ['Stato', contact.status],
    ['Consenso marketing', contact.marketing_consent ? 'Sì' : 'No'],
  ];
  const relCounts = [
    ['Lead', (data.leads || []).length],
    ['Richieste BUY', (data.buy_requests || []).length],
    ['Immobili collegati', (data.properties || []).length],
    ['Visite', (data.visits || []).length],
  ];
  return `
    <h3 class="section-title">Dati anagrafici</h3>
    <div class="detail-grid">
      ${fields.map(([label, value]) => `<div class="detail-item"><label>${escapeHtml(label)}</label>${escapeHtml(value || '—')}</div>`).join('')}
    </div>
    <h3 class="section-title">Note</h3>
    <p>${escapeHtml(contact.notes || 'Nessuna nota.')}</p>
    <h3 class="section-title">Seller Intelligence</h3>
    <div class="seller-intelligence-section" data-seller-intent-mount>
      <p class="muted">Calcolo Seller Intent…</p>
    </div>
    <h3 class="section-title">Domanda buyer</h3>
    <div class="buyer-pressure-section" data-buyer-pressure-mount>
      <p class="muted">Calcolo domanda buyer in caricamento…</p>
    </div>
    <h3 class="section-title">Potenziali acquirenti prima della pubblicazione</h3>
    <div class="invisible-sale-section" data-invisible-sale-mount>
      <p class="muted">Caricamento potenziali acquirenti…</p>
    </div>
    <h3 class="section-title">Relazioni operative</h3>
    <p class="muted">Conteggi informativi, non ruoli in anagrafica.</p>
    <div class="stat-chip-row">
      ${relCounts.map(([label, value]) => `<div class="stat-chip"><span>${value}</span><small>${escapeHtml(label)}</small></div>`).join('')}
    </div>
  `;
}

async function hydrateInvisibleSale(mount, leads, cache) {
  if (!mount) return;
  const token = `invisible-sale-${Date.now()}-${Math.random()}`;
  mount.dataset.invisibleSaleToken = token;
  try {
    const details = await loadLeadEstimationsLazy(leads, cache);
    const ids = new Set();
    for (const lead of details.leads || []) {
      for (const estimation of lead.estimations || []) {
        if (Number.isSafeInteger(Number(estimation.stima_id)) && Number(estimation.stima_id) > 0) ids.add(Number(estimation.stima_id));
      }
    }
    if (mount.isConnected && mount.dataset.invisibleSaleToken === token) mountInvisibleSale(mount, [...ids], token);
  } catch (_error) {
    if (mount.isConnected && mount.dataset.invisibleSaleToken === token) mount.textContent = 'Potenziali acquirenti non disponibili.';
  }
}

function sellerStageLabel(stage) {
  return SELLER_STAGE_LABELS[stage] || stage || '—';
}

function sellerBandLabel(band) {
  return SELLER_INTENT_BAND_LABELS[band] || band || '—';
}

function sellerStateLabel(state) {
  return SELLER_INTENT_STATE_LABELS[state] || state || '—';
}

function getSellLeads(leads) {
  const list = Array.isArray(leads) ? leads : [];
  return list.filter((lead) => lead && lead.pipeline === 'sell');
}

async function loadSellerIntentLazy(leadsFromContact360, cache) {
  if (cache.sellerIntentByLead) return cache.sellerIntentByLead;
  if (cache.sellerIntentPromise) return cache.sellerIntentPromise;

  const sellLeads = getSellLeads(leadsFromContact360);
  cache.sellerIntentPromise = Promise.allSettled(
    sellLeads.map((lead) => apiGet(`/api/seller-intent/leads/${lead.id}/score`)),
  ).then((results) => {
    const byLead = sellLeads.map((lead, idx) => {
      const result = results[idx];
      if (result && result.status === 'fulfilled') {
        return {
          lead,
          status: 'ok',
          scoreData: result.value,
        };
      }
      return {
        lead,
        status: 'error',
      };
    });
    cache.sellerIntentByLead = byLead;
    return byLead;
  }).finally(() => {
    cache.sellerIntentPromise = null;
  });

  return cache.sellerIntentPromise;
}

function renderSellerIntentFactor(factor) {
  const points = Number(factor && factor.points);
  const pointsLabel = Number.isFinite(points) ? (points > 0 ? `+${points}` : `${points}`) : '—';
  return `<li><span>${escapeHtml(factor && factor.label ? factor.label : 'Fattore')}</span><strong>${escapeHtml(pointsLabel)}</strong></li>`;
}

function renderSellerIntentCard({ lead, scoreData }) {
  const factors = Array.isArray(scoreData.factors) ? scoreData.factors : [];
  const operationalFlags = Array.isArray(scoreData.operational_flags) ? scoreData.operational_flags : [];
  const stateHtml = scoreData.state && scoreData.state !== 'active'
    ? `<div class="seller-intent-meta muted">Stato: ${escapeHtml(sellerStateLabel(scoreData.state))}</div>`
    : '';
  const factorsHtml = factors.length
    ? `
      <details class="seller-intent-factors">
        <summary>Perché questo punteggio?</summary>
        <ul>
          ${factors.map((factor) => renderSellerIntentFactor(factor)).join('')}
        </ul>
      </details>
    `
    : '<p class="muted">Nessun dettaglio fattori disponibile.</p>';
  const flagsHtml = operationalFlags.length
    ? `
      <div class="seller-intent-flags">
        ${operationalFlags.map((flag) => `<div class="seller-intent-flag">⚠ ${escapeHtml(flag.label || flag.code || 'Segnalazione operativa')}</div>`).join('')}
      </div>
    `
    : '';

  return `
    <article class="seller-intent-card">
      <div class="seller-intent-score-row">
        <div class="seller-intent-score">${escapeHtml(scoreData.score)}/100</div>
        <div>${escapeHtml(sellerBandLabel(scoreData.band))}</div>
      </div>
      <div class="seller-intent-meta">Lead #${escapeHtml(lead.id)} · ${escapeHtml(sellerStageLabel(lead.stage))}</div>
      ${stateHtml}
      ${flagsHtml}
      ${factorsHtml}
    </article>
  `;
}

function renderSellerIntentUnavailableCard({ lead }) {
  return `
    <article class="seller-intent-card">
      <div class="seller-intent-meta">Lead #${escapeHtml(lead.id)} · ${escapeHtml(sellerStageLabel(lead.stage))}</div>
      <p class="muted">Seller Intent non disponibile.</p>
    </article>
  `;
}

function renderSellerIntentSection(items) {
  if (!items.length) {
    return '<p class="muted">Nessuna opportunità venditore collegata.</p>';
  }
  return `
    <div class="seller-intent-grid">
      ${items.map((item) => (item.status === 'ok' ? renderSellerIntentCard(item) : renderSellerIntentUnavailableCard(item))).join('')}
    </div>
  `;
}

async function hydrateSellerIntent(contentEl, data, cache) {
  const mount = contentEl.querySelector('[data-seller-intent-mount]');
  if (!mount) return;
  const requestId = `seller-intent-${Date.now()}-${Math.random()}`;
  mount.dataset.requestId = requestId;

  try {
    const items = await loadSellerIntentLazy(data.leads, cache);
    if (!mount.isConnected || mount.dataset.requestId !== requestId) return;
    mount.innerHTML = renderSellerIntentSection(items);
  } catch (_error) {
    if (!mount.isConnected || mount.dataset.requestId !== requestId) return;
    mount.innerHTML = '<p class="muted">Seller Intent non disponibile.</p>';
  }
}

// --- Richieste (BUY) ----------------------------------------------------

function renderRichieste(items) {
  return renderTable(
    [
      { label: 'Titolo', render: (r) => escapeHtml(r.title || `Richiesta #${r.id}`) },
      { label: 'Stato', render: (r) => renderBadge(r.status || '—', statusTone(r.status)) },
      { label: 'Budget target', render: (r) => r.budget_target != null ? escapeHtml(r.budget_target) : '—' },
      { label: 'Creata il', render: (r) => escapeHtml(formatDate(r.created_at)) },
    ],
    items,
    { emptyMessage: 'Nessuna richiesta BUY collegata a questo contatto.' },
  );
}

// --- Abbinamenti (MATCH) -------------------------------------------------

function renderAbbinamenti(items) {
  return renderTable(
    [
      { label: 'Match', render: (m) => `#${escapeHtml(m.id)}` },
      { label: 'Immobile', render: (m) => escapeHtml(m.property_title || `Immobile #${m.property_id}`) },
      { label: 'Punteggio', render: (m) => escapeHtml(m.score_total ?? '—') },
      { label: 'Classe', render: (m) => renderBadge(m.match_class || '—', 'gray') },
      { label: 'Stato commerciale', render: (m) => escapeHtml(m.commercial_status || '—') },
    ],
    items,
    { emptyMessage: 'Nessun abbinamento per le richieste di questo contatto.' },
  );
}

// --- Visite ---------------------------------------------------------------

function renderVisite(items) {
  return renderTable(
    [
      { label: 'Immobile', render: (v) => escapeHtml(v.property_title || `Immobile #${v.property_id}`) },
      { label: 'Data', render: (v) => escapeHtml(formatDateTime(v.scheduled_at)) },
      { label: 'Stato', render: (v) => renderBadge(v.status || '—', statusTone(v.status)) },
      { label: 'Esito', render: (v) => escapeHtml(v.outcome || '—') },
    ],
    items,
    { emptyMessage: 'Nessuna visita registrata per questo contatto.' },
  );
}

// --- Attività ---------------------------------------------------------------

function renderAttivita(items) {
  return renderTable(
    [
      { label: 'Tipo', render: (a) => escapeHtml(a.activity_type || '—') },
      { label: 'Descrizione', render: (a) => escapeHtml(a.description || '—') },
      { label: 'Quando', render: (a) => escapeHtml(formatDateTime(a.occurred_at)) },
    ],
    items,
    { emptyMessage: 'Nessuna attività registrata per questo contatto.' },
  );
}

// --- Task (con origine BUY se presente in metadata.buy_request_id) --------

function renderTask(items) {
  return renderTable(
    [
      { label: 'Titolo', render: (t) => escapeHtml(t.title || `Task #${t.id}`) },
      { label: 'Origine', render: (t) => {
        const buyId = t.metadata && t.metadata.buy_request_id;
        return buyId ? renderBadge(`Da richiesta BUY #${buyId}`, 'buy') : '<span class="muted">CORE</span>';
      } },
      { label: 'Stato', render: (t) => renderBadge(t.status || '—', statusTone(t.status)) },
      { label: 'Priorità', render: (t) => escapeHtml(t.priority || '—') },
      { label: 'Scadenza', render: (t) => escapeHtml(formatDateTime(t.due_at)) },
    ],
    items,
    { emptyMessage: 'Nessun task collegato a questo contatto.' },
  );
}

// --- Immobili (lazy: GET /api/property/properties/{id} per ciascuno) ------

async function loadPropertiesLazy(propertiesFromContact360, contactId, cache) {
  if (cache.properties) return cache.properties;
  const list = Array.isArray(propertiesFromContact360) ? propertiesFromContact360 : [];
  const results = await Promise.allSettled(
    list.map((p) => apiGet(`/api/property/properties/${p.id}`)),
  );
  const properties = results
    .filter((r) => r.status === 'fulfilled')
    .map((r) => r.value);
  const failedCount = results.length - properties.length;
  cache.properties = { properties, contactId: String(contactId), failedCount };
  return cache.properties;
}

function renderImmobili(loaded) {
  const { properties, contactId, failedCount } = loaded;
  const warning = failedCount
    ? `<div class="error-box">${failedCount} immobile/i non è stato possibile caricarli in dettaglio.</div>`
    : '';
  const table = renderTable(
    [
      { label: 'Immobile', render: (p) => escapeHtml(p.title || p.code || `Immobile #${p.id}`) },
      { label: 'Città', render: (p) => escapeHtml(p.city || '—') },
      { label: 'Ruolo del contatto', render: (p) => {
        const link = (p.contacts || []).find((c) => String(c.contact_id) === String(contactId));
        if (!link) return '<span class="muted">Non specificato</span>';
        return renderBadge(PROPERTY_ROLE_LABELS[link.role] || link.role, link.role === 'owner' ? 'role' : 'gray');
      } },
    ],
    properties,
    { emptyMessage: 'Nessun immobile collegato a questo contatto (relazione property_contacts).' },
  );
  return warning + table;
}

// --- Documenti (riusa lo stesso fetch di Immobili: Contatto → Immobile → property_documents) ---

function renderDocumenti(loaded) {
  const { properties } = loaded;
  const rows = [];
  for (const p of properties) {
    for (const doc of (p.documents || [])) {
      rows.push({ ...doc, property_title: p.title || p.code || `Immobile #${p.id}` });
    }
  }
  const note = '<p class="muted">Documenti collegati tramite Contatto → Immobile → documenti immobile (property_documents). Non esiste una relazione diretta Contatto → documento.</p>';
  const table = renderTable(
    [
      { label: 'Immobile', render: (d) => escapeHtml(d.property_title) },
      { label: 'Documento', render: (d) => escapeHtml(d.title || d.document_type || `Documento #${d.id}`) },
      { label: 'Tipo', render: (d) => escapeHtml(d.document_type || '—') },
      { label: 'Stato', render: (d) => renderBadge(d.status || '—', statusTone(d.status)) },
    ],
    rows,
    { emptyMessage: 'Nessun documento disponibile sugli immobili collegati a questo contatto.' },
  );
  return note + table;
}

// --- Stime (lazy: GET /api/core/leads/{id} per ciascun lead, campo estimations) ---

async function loadLeadEstimationsLazy(leadsFromContact360, cache) {
  if (cache.leadsWithEstimations) return cache.leadsWithEstimations;
  const list = Array.isArray(leadsFromContact360) ? leadsFromContact360 : [];
  const results = await Promise.allSettled(
    list.map((l) => apiGet(`/api/core/leads/${l.id}`)),
  );
  const leads = results.filter((r) => r.status === 'fulfilled').map((r) => r.value);
  const failedCount = results.length - leads.length;
  cache.leadsWithEstimations = { leads, failedCount };
  return cache.leadsWithEstimations;
}

function renderStime(loaded) {
  const { leads, failedCount } = loaded;
  const rows = [];
  for (const lead of leads) {
    for (const est of (lead.estimations || [])) {
      rows.push({ ...est, lead_id: lead.id });
    }
  }
  const warning = failedCount
    ? `<div class="error-box">${failedCount} lead non è stato possibile caricarli in dettaglio.</div>`
    : '';
  const note = '<p class="muted">Collegamento verificato tramite Contatto → Lead → lead_stime. Non esiste oggi un endpoint per leggere i dettagli (indirizzo, tipologia) di una singola stima per id: qui è mostrato solo il collegamento.</p>';
  const table = renderTable(
    [
      { label: 'Stima', render: (e) => `#${escapeHtml(e.stima_id)}` },
      { label: 'Lead collegato', render: (e) => `#${escapeHtml(e.lead_id)}` },
      { label: 'Relazione', render: (e) => renderBadge(e.relation_type || '—', 'gray') },
      { label: 'Collegata il', render: (e) => escapeHtml(formatDate(e.created_at)) },
    ],
    rows,
    { emptyMessage: 'Nessuna stima collegata ai lead di questo contatto.' },
  );
  return warning + note + table;
}

// --- utility ---------------------------------------------------------------

function statusTone(status) {
  if (['completed', 'won', 'active', 'confirmed'].includes(status)) return 'ok';
  if (['cancelled', 'lost', 'failed'].includes(status)) return 'danger';
  if (['urgent', 'high'].includes(status)) return 'warn';
  return 'gray';
}
