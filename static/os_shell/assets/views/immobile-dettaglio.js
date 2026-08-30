// STIMA360 OS — immobile-dettaglio.js
// Scheda Immobile: riusa GET /api/property/properties/{id} (property/router.py:19-20 ->
// property/repository.py:62-96, get_property) come fonte unica per Panoramica,
// Proprietari, Foto, Documenti, Visite (tutti gia' embedded nella risposta:
// p.contacts, p.photos, p.documents, p.visits). Nessuna riaggregazione lato
// frontend di dati che il backend fornisce gia'.
//
// Proposte: caricate al primo rendering insieme all'immobile, stesso pattern
// gia' usato da property_admin (static/property_admin/assets/app.js, openDetail:
// Promise.all([properties/{id}, /api/proposals?property_id=...])) — endpoint
// verificato in proposal/router.py:30-49 (property_id: int|None = Query(None,gt=0)).
//
// Abbinamenti: tab a caricamento posticipato (al primo click, non al caricamento
// iniziale) verso GET /api/match/matches?property_id={id} (match/router.py:94-104,
// property_id: int|None=None). Lettura pura (list_matches), nessun calcolo/refresh
// innescato: NON viene mai chiamato POST /api/match/properties/{id}/calculate.
//
// Acquirenti compatibili: NON esiste oggi un'API che risponda a "quali acquirenti
// sono compatibili con questo immobile" in modo pulito senza duplicare la logica
// di calcolo MATCH (che e' vietato reinvocare/reinventare qui). Si mostra un
// messaggio esplicito di rimando alla tab Abbinamenti, senza riusare gli stessi
// dati sotto un nome diverso.
//
// Attivita: core/repository.py:list_activities(limit,offset,contact_id,lead_id,
// stima_id) NON ha un parametro property_id — verificato per grep sulla firma
// reale. Nessuna relazione attivita<->immobile esiste oggi: si mostra stato
// neutro, nessuna chiamata viene fatta.
//
// Documenti: property_documents (SELECT * FROM property_documents WHERE
// property_id=%s, property/repository.py:68) non include alcun campo di
// collegamento a owner_shared_documents: lo stato di condivisione con il
// Proprietario (Owner Portal) richiederebbe una query aggiuntiva non esistente
// in questa risposta, quindi NON viene mostrato (nessun badge "condiviso"
// inventato). Nessuna azione di condivisione presente in questa vista.
//
// Vista interamente in sola lettura: nessuna scrittura, upload, eliminazione,
// riordino o azione di stato in questa tab.

import { apiGet } from '../core/api-client.js';
import { navigate } from '../core/router.js';
import { renderTable, renderBadge, escapeHtml, formatDate, formatDateTime } from '../components/st-table.js';

const STATUS_LABELS = {
  draft: 'Bozza', evaluation: 'In valutazione', mandate: 'Mandato', active: 'Attivo',
  reserved: 'Riservato', under_offer: 'Sotto offerta', sold: 'Venduto',
  withdrawn: 'Ritirato', archived: 'Archiviato',
};

const PROPERTY_ROLE_LABELS = {
  owner: 'Proprietario', seller: 'Venditore', tenant: 'Inquilino', contact: 'Referente',
  professional: 'Professionista', other: 'Altro',
};

const DOCUMENT_STATUS_LABELS = {
  missing: 'Mancante', requested: 'Richiesto', available: 'Disponibile',
  expired: 'Scaduto', rejected: 'Rifiutato', archived: 'Archiviato',
};

const TABS = [
  { key: 'panoramica', label: 'Panoramica' },
  { key: 'proprietari', label: 'Proprietari' },
  { key: 'foto', label: 'Foto' },
  { key: 'documenti', label: 'Documenti' },
  { key: 'visite', label: 'Visite' },
  { key: 'acquirenti', label: 'Acquirenti compatibili' },
  { key: 'abbinamenti', label: 'Abbinamenti' },
  { key: 'proposte', label: 'Proposte' },
  { key: 'attivita', label: 'Attività' },
];

export async function renderImmobileDettaglio(container, params = []) {
  const propertyId = params[0];
  if (!propertyId || !/^\d+$/.test(String(propertyId))) {
    container.innerHTML = '<div class="error-box">Identificativo immobile non valido.</div>';
    return;
  }

  container.innerHTML = '<p class="muted">Caricamento scheda immobile…</p>';

  let property;
  let proposals = [];
  try {
    const [propertyData, proposalsData] = await Promise.all([
      apiGet(`/api/property/properties/${propertyId}`),
      apiGet(`/api/proposals?property_id=${propertyId}`),
    ]);
    property = propertyData;
    proposals = Array.isArray(proposalsData?.items) ? proposalsData.items : [];
  } catch (error) {
    const notFound = /non trovato|not found/i.test(error.message || '');
    container.innerHTML = `<div class="error-box">${notFound ? 'Immobile non trovato.' : `Errore nel caricamento dell'immobile: ${escapeHtml(error.message)}`}</div>`;
    return;
  }

  // Cache locale per la tab Abbinamenti (caricamento posticipato al primo click).
  const lazyCache = { matches: null };

  const title = property.title || property.code || `Immobile #${property.id}`;

  container.innerHTML = `
    <div class="contact-header card">
      <h2>${escapeHtml(title)}</h2>
      <div class="muted">Immobile #${escapeHtml(property.id)} · ${escapeHtml(property.code || '—')} · ${escapeHtml([property.address, property.city].filter(Boolean).join(', ') || '—')}</div>
      <div class="badge-row">${renderBadge(STATUS_LABELS[property.commercial_status] || property.commercial_status || '—', statusTone(property.commercial_status))}</div>
    </div>
    <div class="tabs" id="property-tabs"></div>
    <div id="property-tab-content" class="card panel"></div>
  `;

  const tabsEl = container.querySelector('#property-tabs');
  tabsEl.innerHTML = TABS.map((t, i) => `<button type="button" class="tab-btn ${i === 0 ? 'active' : ''}" data-tab="${t.key}">${escapeHtml(t.label)}</button>`).join('');

  const contentEl = container.querySelector('#property-tab-content');

  async function showTab(key) {
    tabsEl.querySelectorAll('.tab-btn').forEach((b) => b.classList.toggle('active', b.dataset.tab === key));
    contentEl.innerHTML = '<p class="muted">Caricamento…</p>';
    try {
      switch (key) {
        case 'panoramica': contentEl.innerHTML = renderPanoramica(property); break;
        case 'proprietari': contentEl.innerHTML = renderProprietari(property.contacts); break;
        case 'foto': contentEl.innerHTML = renderFoto(property.photos); break;
        case 'documenti': contentEl.innerHTML = renderDocumenti(property.documents); break;
        case 'visite': contentEl.innerHTML = renderVisite(property.visits); break;
        case 'proposte': contentEl.innerHTML = renderProposte(proposals); break;
        case 'acquirenti': contentEl.innerHTML = renderAcquirentiCompatibili(); break;
        case 'attivita': contentEl.innerHTML = renderAttivita(); break;
        case 'abbinamenti': {
          const matches = await loadMatchesLazy(property.id, lazyCache);
          contentEl.innerHTML = renderAbbinamenti(matches);
          bindOpenMatchLinks(contentEl);
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

// --- Panoramica -------------------------------------------------------

function renderPanoramica(p) {
  const fields = [
    ['Tipologia', p.property_type], ['Classificazione', p.classification],
    ['Indirizzo', [p.address, p.civic_number].filter(Boolean).join(' ')],
    ['Comune', p.city], ['Provincia', p.province], ['CAP', p.postal_code], ['Microzona', p.microzone],
    ['Superficie (mq)', p.surface_sqm], ['Superficie commerciale (mq)', p.commercial_surface_sqm],
    ['Locali', p.rooms], ['Camere', p.bedrooms], ['Bagni', p.bathrooms],
    ['Piano', p.floor], ['Piani totali', p.total_floors],
    ['Ascensore', p.elevator === null || p.elevator === undefined ? null : (p.elevator ? 'Sì' : 'No')],
    ['Anno costruzione', p.year_built], ['Condizione', p.condition], ['Classe energetica', p.energy_class],
    ['Prezzo richiesto', formatPrice(p.asking_price)], ['Prezzo minimo', formatPrice(p.minimum_price)],
    ['Tipo mandato', p.mandate_type], ['Inizio mandato', formatDate(p.mandate_start)], ['Fine mandato', formatDate(p.mandate_end)],
    ['Assegnato a', p.assigned_to], ['Fonte', p.source],
    ['Punteggio completezza', p.readiness_score != null ? `${p.readiness_score}%` : null],
  ];
  return `
    <h3 class="section-title">Dati immobile</h3>
    <div class="detail-grid">
      ${fields.map(([label, value]) => `<div class="detail-item"><label>${escapeHtml(label)}</label>${escapeHtml(value === null || value === undefined || value === '' ? '—' : value)}</div>`).join('')}
    </div>
    <h3 class="section-title">Note</h3>
    <p>${escapeHtml(p.public_notes || p.internal_notes || 'Nessuna nota.')}</p>
  `;
}

// --- Proprietari (property_contacts, ruolo reale, non owner_accounts) ------

function renderProprietari(items) {
  const note = '<p class="muted">Elenco dai referenti collegati all\'immobile (property_contacts). Non riflette gli account di accesso all\'Owner Portal.</p>';
  const table = renderTable(
    [
      { label: 'Nominativo', render: (c) => escapeHtml(c.display_name || `Contatto #${c.contact_id}`) },
      { label: 'Ruolo', render: (c) => renderBadge(PROPERTY_ROLE_LABELS[c.role] || c.role || '—', c.role === 'owner' ? 'role' : 'gray') },
      { label: 'Principale', render: (c) => c.is_primary ? renderBadge('Principale', 'ok') : '' },
      { label: 'Quota (%)', render: (c) => c.ownership_share != null ? escapeHtml(c.ownership_share) : '—' },
      { label: 'Email', render: (c) => escapeHtml(c.email || '—') },
      { label: 'Telefono', render: (c) => escapeHtml(c.phone || '—') },
    ],
    items,
    { emptyMessage: 'Nessun referente collegato a questo immobile.' },
  );
  return note + table;
}

// --- Foto (sola lettura: nessun upload/elimina/riordina) -------------------

function renderFoto(items) {
  const list = Array.isArray(items) ? items : [];
  if (!list.length) return '<p class="muted">Nessuna foto disponibile per questo immobile.</p>';
  const sorted = [...list].sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0));
  return `<div class="photo-grid">${sorted.map((photo) => `
    <figure class="photo-item">
      <img src="${escapeHtml(photo.url)}" alt="${escapeHtml(photo.title || 'Foto immobile')}" loading="lazy">
      <figcaption>${photo.is_cover ? renderBadge('Copertina', 'role') + ' ' : ''}${escapeHtml(photo.title || '—')}</figcaption>
    </figure>
  `).join('')}</div>`;
}

// --- Documenti (property_documents; nessuno stato di condivisione OWNER) ---

function renderDocumenti(items) {
  const note = '<p class="muted">Documenti dell\'immobile (sistema PROPERTY). Lo stato di condivisione con il Proprietario (Owner Portal) non è incluso in questa risposta e non è mostrato qui.</p>';
  const table = renderTable(
    [
      { label: 'Documento', render: (d) => escapeHtml(d.title || `Documento #${d.id}`) },
      { label: 'Tipo', render: (d) => escapeHtml(d.document_type || '—') },
      { label: 'Stato', render: (d) => renderBadge(DOCUMENT_STATUS_LABELS[d.status] || d.status || '—', documentStatusTone(d.status)) },
      { label: 'Scadenza', render: (d) => escapeHtml(formatDate(d.expires_at)) },
      { label: 'Note', render: (d) => escapeHtml(d.notes || '—') },
    ],
    items,
    { emptyMessage: 'Nessun documento presente per questo immobile.' },
  );
  return note + table;
}

// --- Visite (con contesto BUY/MATCH gia' incluso da get_property, solo testo) ---

function renderVisite(items) {
  const table = renderTable(
    [
      { label: 'Data', render: (v) => escapeHtml(formatDateTime(v.scheduled_at)) },
      { label: 'Stato', render: (v) => renderBadge(v.status || '—', statusTone(v.status)) },
      { label: 'Esito', render: (v) => escapeHtml(v.outcome || '—') },
      { label: 'Valutazione', render: (v) => v.rating != null ? `${escapeHtml(v.rating)}/5` : '—' },
      { label: 'Assegnata a', render: (v) => escapeHtml(v.assigned_to || '—') },
      { label: 'Origine', render: (v) => v.buy_request_id ? renderBadge(`Richiesta BUY #${v.buy_request_id}`, 'buy') : '<span class="muted">—</span>' },
    ],
    items,
    { emptyMessage: 'Nessuna visita registrata per questo immobile.' },
  );
  return table;
}

// --- Acquirenti compatibili: gap esplicito, nessun riuso dati Abbinamenti ---

function renderAcquirentiCompatibili() {
  return '<p class="muted">Funzione disponibile nella fase Abbinamenti.</p>';
}

// --- Abbinamenti (lazy: GET /api/match/matches?property_id={id}) -----------

async function loadMatchesLazy(propertyId, cache) {
  if (cache.matches) return cache.matches;
  try {
    const data = await apiGet(`/api/match/matches?property_id=${propertyId}`);
    cache.matches = Array.isArray(data?.items) ? data.items : [];
  } catch (error) {
    cache.matches = { error: error.message };
  }
  return cache.matches;
}

function renderAbbinamenti(matches) {
  if (matches && matches.error) {
    return `<div class="error-box">Impossibile caricare gli abbinamenti: ${escapeHtml(matches.error)}</div>`;
  }
  return renderTable(
    [
      { label: 'Match', render: (m) => `#${escapeHtml(m.id)}` },
      { label: 'Acquirente', render: (m) => escapeHtml(m.buyer_name || `Richiesta #${m.buy_request_id}`) },
      { label: 'Punteggio', render: (m) => escapeHtml(m.effective_score ?? m.score_total ?? '—') },
      { label: 'Classe', render: (m) => renderBadge(m.match_class || '—', 'gray') },
      { label: 'Stato commerciale', render: (m) => escapeHtml(m.commercial_status || '—') },
      { label: 'Compatibilità', render: (m) => renderBadge(m.compatibility_status || '—', m.compatibility_status === 'incompatible' ? 'danger' : 'ok') },
      { label: '', render: (m) => `<button type="button" class="btn ghost open-match-btn" data-match-id="${escapeHtml(m.id)}">Apri match</button>` },
    ],
    matches,
    { emptyMessage: 'Nessun abbinamento presente per questo immobile.' },
  );
}

// P4: colonna "Apri match" -> #/abbinamenti/{match_id} (scheda MATCH nella
// nuova App Shell, mai match-admin legacy). Questa tabella non ha oggi altra
// navigazione di riga da preservare.
function bindOpenMatchLinks(contentEl) {
  contentEl.querySelectorAll('.open-match-btn').forEach((btn) => {
    btn.addEventListener('click', () => navigate('abbinamenti', [btn.dataset.matchId]));
  });
}

// --- Proposte (gia' caricate all'apertura scheda, come property_admin) -----

function renderProposte(items) {
  return renderTable(
    [
      { label: 'Proposta', render: (pr) => `#${escapeHtml(pr.id)}` },
      { label: 'Acquirente', render: (pr) => escapeHtml(pr.contact_name || `Contatto #${pr.contact_id}`) },
      { label: 'Importo', render: (pr) => formatPrice(pr.amount) },
      { label: 'Stato', render: (pr) => renderBadge(pr.status || '—', statusTone(pr.status)) },
      { label: 'Scadenza', render: (pr) => escapeHtml(formatDateTime(pr.expires_at)) },
    ],
    items,
    { emptyMessage: 'Nessuna proposta presente per questo immobile.' },
  );
}

// --- Attività: nessuna relazione attivita<->immobile nelle API esistenti ---

function renderAttivita() {
  return '<p class="muted">Non è disponibile oggi un collegamento tra Attività e Immobile nelle API esistenti (core/repository.py: list_activities non filtra per immobile).</p>';
}

// --- utility ---------------------------------------------------------------

function formatPrice(value) {
  if (value === null || value === undefined) return '—';
  const n = Number(value);
  if (Number.isNaN(n)) return '—';
  return n.toLocaleString('it-IT', { style: 'currency', currency: 'EUR', maximumFractionDigits: 0 });
}

function statusTone(status) {
  if (['active', 'sold', 'mandate', 'confirmed', 'completed', 'accepted'].includes(status)) return 'ok';
  if (['withdrawn', 'archived', 'cancelled', 'rejected', 'expired', 'no_show'].includes(status)) return 'danger';
  if (['reserved', 'under_offer', 'evaluation', 'submitted'].includes(status)) return 'warn';
  return 'gray';
}

function documentStatusTone(status) {
  if (status === 'available') return 'ok';
  if (['missing', 'expired', 'rejected'].includes(status)) return 'danger';
  if (status === 'requested') return 'warn';
  return 'gray';
}
