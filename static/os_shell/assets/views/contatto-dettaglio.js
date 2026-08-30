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

const ROLE_LABELS = {
  owner: 'Proprietario', seller: 'Venditore', buyer: 'Acquirente', prospect: 'Potenziale cliente',
  referrer: 'Segnalatore', agency: 'Agenzia', professional: 'Professionista', other: 'Altro',
};

const PROPERTY_ROLE_LABELS = {
  owner: 'Proprietario', seller: 'Venditore', tenant: 'Inquilino', contact: 'Referente',
  professional: 'Professionista', other: 'Altro',
};

const TABS = [
  { key: 'panoramica', label: 'Panoramica' },
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
  const lazyCache = { properties: null, leadsWithEstimations: null };

  const contact = data.contact || {};
  const name = contact.display_name || fallbackName(contact);

  container.innerHTML = `
    <div class="contact-header card">
      <h2>${escapeHtml(name)}</h2>
      <div class="muted">Contatto #${escapeHtml(contact.id)} · ${escapeHtml(contact.contact_type === 'company' ? 'Azienda' : 'Persona')}</div>
      <div id="contact-role-badges" class="badge-row"></div>
    </div>
    <div class="tabs" id="contact-tabs"></div>
    <div id="contact-tab-content" class="card panel"></div>
  `;

  const badgeRow = container.querySelector('#contact-role-badges');
  const roles = Array.isArray(data.roles) ? data.roles : [];
  badgeRow.innerHTML = roles.length
    ? roles.map((r) => renderBadge(ROLE_LABELS[r.role] || r.role, 'role')).join('')
    : '<span class="muted">Nessun ruolo assegnato in anagrafica.</span>';

  const tabsEl = container.querySelector('#contact-tabs');
  tabsEl.innerHTML = TABS.map((t, i) => `<button type="button" class="tab-btn ${i === 0 ? 'active' : ''}" data-tab="${t.key}">${escapeHtml(t.label)}</button>`).join('');

  const contentEl = container.querySelector('#contact-tab-content');

  async function showTab(key) {
    tabsEl.querySelectorAll('.tab-btn').forEach((b) => b.classList.toggle('active', b.dataset.tab === key));
    contentEl.innerHTML = '<p class="muted">Caricamento…</p>';
    try {
      switch (key) {
        case 'panoramica': contentEl.innerHTML = renderPanoramica(contact, data); break;
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
    <h3 class="section-title">Relazioni operative</h3>
    <p class="muted">Conteggi informativi, non ruoli in anagrafica.</p>
    <div class="stat-chip-row">
      ${relCounts.map(([label, value]) => `<div class="stat-chip"><span>${value}</span><small>${escapeHtml(label)}</small></div>`).join('')}
    </div>
  `;
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
