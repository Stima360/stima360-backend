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
// P9: la tab Proposte diventa operativa (creazione, modifica in bozza,
// transizioni di stato) sul contratto gia' verificato in fase di audit P9
// (proposal/router.py, proposal/repository.py). Nessuna nuova entita': fonte
// unica property_proposals. La creazione e' sempre contestuale a un
// abbinamento (match) reale gia' esistente — mai un match_id digitato
// manualmente — riusando loadMatchesLazy, la stessa funzione di rete gia'
// usata dalla tab Abbinamenti (nessun nuovo endpoint, nessuna nuova
// chiamata oltre a quella gia' presente). Le transizioni di stato rispettano
// esattamente proposal/enums.py:PROPOSAL_TRANSITIONS: nessun selettore di
// stato generico. Accettare una proposta non tocca mai
// properties.commercial_status ne' buy_requests (verificato in repository.py:
// transition_proposal — unica UPDATE su property_proposals).
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

import { apiGet, apiPatch, apiPost } from '../core/api-client.js';
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

  // P9: ricarica proposte dopo creazione/modifica/transizione. Stessa fonte
  // gia' usata al caricamento iniziale (/api/proposals?property_id=...),
  // nessun nuovo endpoint.
  async function reloadProposals() {
    const data = await apiGet(`/api/proposals?property_id=${property.id}`);
    proposals = Array.isArray(data?.items) ? data.items : [];
  }

  // P8: stato locale di modifica per la sezione Incarico dentro Panoramica.
  // Nessuna nuova entita': mandate_type/mandate_start/mandate_end restano
  // colonne di properties, scritte tramite PATCH /api/property/properties/{id}
  // gia' esistente (property/schemas.py:PropertyUpdate).
  let incaricoEditMode = false;

  const title = property.title || property.code || `Immobile #${property.id}`;

  container.innerHTML = `
    <div class="contact-header card">
      <h2>${escapeHtml(title)}</h2>
      <div class="muted">Immobile #${escapeHtml(property.id)} · ${escapeHtml(property.code || '—')} · ${escapeHtml([property.address, property.city].filter(Boolean).join(', ') || '—')}</div>
      <div class="badge-row">${renderBadge(STATUS_LABELS[property.commercial_status] || property.commercial_status || '—', statusTone(property.commercial_status))}</div>
    </div>
    <div class="tabs" id="property-tabs"></div>
    <div id="property-tab-content" class="card panel"></div>
    <dialog id="proposal-dialog" class="modal"></dialog>
  `;

  const tabsEl = container.querySelector('#property-tabs');
  tabsEl.innerHTML = TABS.map((t, i) => `<button type="button" class="tab-btn ${i === 0 ? 'active' : ''}" data-tab="${t.key}">${escapeHtml(t.label)}</button>`).join('');

  const contentEl = container.querySelector('#property-tab-content');

  async function showTab(key) {
    tabsEl.querySelectorAll('.tab-btn').forEach((b) => b.classList.toggle('active', b.dataset.tab === key));
    contentEl.innerHTML = '<p class="muted">Caricamento…</p>';
    try {
      switch (key) {
        case 'panoramica': contentEl.innerHTML = renderPanoramica(property, incaricoEditMode); bindIncaricoSection(contentEl); break;
        case 'proprietari': contentEl.innerHTML = renderProprietari(property.contacts); break;
        case 'foto': contentEl.innerHTML = renderFoto(property.photos); break;
        case 'documenti': contentEl.innerHTML = renderDocumenti(property.documents); break;
        case 'visite': contentEl.innerHTML = renderVisite(property.visits); break;
        case 'proposte': contentEl.innerHTML = renderProposte(proposals); bindProposteSection(contentEl); break;
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

  // P8: azioni della sezione Incarico (Panoramica). Nessuna chiamata al
  // caricamento pagina: la PATCH parte solo al click esplicito su "Salva".
  function bindIncaricoSection(panelEl) {
    const editBtn = panelEl.querySelector('#incarico-edit-btn');
    if (editBtn) {
      editBtn.addEventListener('click', () => {
        incaricoEditMode = true;
        showTab('panoramica');
      });
    }

    const cancelBtn = panelEl.querySelector('#incarico-cancel-btn');
    if (cancelBtn) {
      cancelBtn.addEventListener('click', () => {
        incaricoEditMode = false;
        showTab('panoramica');
      });
    }

    const saveBtn = panelEl.querySelector('#incarico-save-btn');
    if (saveBtn) {
      saveBtn.addEventListener('click', async () => {
        const errorEl = panelEl.querySelector('#incarico-error');
        const typeInput = panelEl.querySelector('#incarico-type');
        const startInput = panelEl.querySelector('#incarico-start');
        const endInput = panelEl.querySelector('#incarico-end');
        if (errorEl) errorEl.textContent = '';

        const typeVal = typeInput.value.trim();
        const startVal = startInput.value;
        const endVal = endInput.value;

        // Validazione UX preventiva: il backend resta source of truth
        // (property/schemas.py:PropertyUpdate.validate_update applica la
        // stessa regola su cio' che viene effettivamente inviato).
        if (startVal && endVal && endVal < startVal) {
          if (errorEl) errorEl.textContent = 'La data di scadenza non pu\u00f2 precedere la data di inizio.';
          return;
        }

        // Solo i campi realmente modificati entrano nel payload: PropertyUpdate
        // usa exclude_unset, quindi un campo omesso resta invariato lato server,
        // mentre null lo azzera esplicitamente (verificato su property/schemas.py,
        // property/service.py:dump(p,True) e property/repository.py:update_property).
        const payload = {};
        if (typeVal !== (property.mandate_type || '')) payload.mandate_type = typeVal === '' ? null : typeVal;
        if (startVal !== toDateInputValue(property.mandate_start)) payload.mandate_start = startVal === '' ? null : startVal;
        if (endVal !== toDateInputValue(property.mandate_end)) payload.mandate_end = endVal === '' ? null : endVal;

        if (!Object.keys(payload).length) {
          incaricoEditMode = false;
          showTab('panoramica');
          return;
        }

        saveBtn.disabled = true;
        if (cancelBtn) cancelBtn.disabled = true;
        saveBtn.textContent = 'Salvataggio\u2026';
        try {
          const updated = await apiPatch(`/api/property/properties/${property.id}`, payload);
          property.mandate_type = updated.mandate_type;
          property.mandate_start = updated.mandate_start;
          property.mandate_end = updated.mandate_end;
          incaricoEditMode = false;
          showTab('panoramica');
        } catch (error) {
          saveBtn.disabled = false;
          if (cancelBtn) cancelBtn.disabled = false;
          saveBtn.textContent = 'Salva';
          if (errorEl) errorEl.textContent = error.message || 'Errore nel salvataggio.';
        }
      });
    }
  }

  // P9: sezione Proposte (Panoramica gestisce Incarico sopra, vedi
  // bindIncaricoSection). Creazione sempre contestuale a un abbinamento
  // reale gia' esistente (mai un match_id digitato), modifica diretta solo
  // in stato draft, transizioni secondo la macchina a stati reale.
  function bindProposteSection(panelEl) {
    const newBtn = panelEl.querySelector('#proposal-new-btn');
    if (newBtn) {
      newBtn.addEventListener('click', () => { openProposalCreateDialog(); });
    }
    panelEl.querySelectorAll('.proposal-action-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        const proposalId = Number(btn.dataset.proposalId);
        const action = btn.dataset.action;
        const proposal = proposals.find((p) => p.id === proposalId);
        if (!proposal) return;
        if (action === 'edit') {
          openProposalEditDialog(proposal);
        } else {
          runProposalTransition(btn, proposalId, action);
        }
      });
    });
  }

  async function openProposalCreateDialog() {
    const dialogEl = container.querySelector('#proposal-dialog');
    const feedbackEl = contentEl.querySelector('#proposal-feedback');
    if (!dialogEl) return;
    const newBtn = contentEl.querySelector('#proposal-new-btn');
    if (newBtn) { newBtn.disabled = true; newBtn.textContent = 'Caricamento\u2026'; }
    let matches;
    try {
      matches = await loadMatchesLazy(property.id, lazyCache);
    } catch (error) {
      matches = { error: error.message };
    }
    if (newBtn) { newBtn.disabled = false; newBtn.textContent = 'Nuova proposta'; }
    if (!Array.isArray(matches)) {
      if (feedbackEl) feedbackEl.innerHTML = `<div class="error-box">Impossibile caricare gli abbinamenti per la creazione della proposta: ${escapeHtml(matches?.error || 'errore sconosciuto')}</div>`;
      return;
    }
    const openMatchIds = new Set(proposals.filter((p) => ['draft', 'submitted'].includes(p.status)).map((p) => p.match_id));
    const eligible = matches.filter((m) => !openMatchIds.has(m.id));
    if (!eligible.length) {
      if (feedbackEl) feedbackEl.innerHTML = '<div class="error-box">Nessun abbinamento disponibile: tutti gli abbinamenti di questo immobile hanno gi\u00e0 una proposta aperta, oppure non esiste ancora alcun abbinamento (vedi tab Abbinamenti).</div>';
      return;
    }
    openProposalDialog(dialogEl, { mode: 'create', eligible });
  }

  function openProposalEditDialog(proposal) {
    const dialogEl = container.querySelector('#proposal-dialog');
    if (!dialogEl) return;
    openProposalDialog(dialogEl, { mode: 'edit', proposal });
  }

  function openProposalDialog(dialogEl, opts) {
    const isEdit = opts.mode === 'edit';
    const proposal = opts.proposal || null;
    // Chiave di idempotenza stabile per l'intera vita di QUESTA apertura del
    // dialog in modalita' create: generata una sola volta qui (mai dentro
    // buildProposalCreatePayload), cosi' un retry dopo un errore di rete sullo
    // stesso dialog riusa la stessa chiave invece di generarne una nuova.
    const createIdempotencyKey = isEdit ? null : crypto.randomUUID();
    dialogEl.innerHTML = `
      <form id="proposal-form">
        <h3 class="section-title">${isEdit ? 'Modifica proposta' : 'Nuova proposta'}</h3>
        ${isEdit ? '' : `
          <div class="form-field">
            <label>Abbinamento</label>
            <select id="proposal-match" class="input" required>
              ${opts.eligible.map((m) => `<option value="${escapeHtml(m.id)}">${escapeHtml(matchOptionLabel(m))}</option>`).join('')}
            </select>
          </div>
        `}
        <div class="form-grid-2">
          <div class="form-field"><label>Importo (\u20ac)</label><input type="number" id="proposal-amount" class="input" min="0.01" step="0.01" required value="${proposal ? escapeHtml(proposal.amount) : ''}"></div>
          <div class="form-field"><label>Scadenza</label><input type="datetime-local" id="proposal-expiry" class="input" required value="${proposal ? proposalDateTimeLocal(proposal.expires_at) : ''}"></div>
        </div>
        <div class="form-field"><label>Note</label><textarea id="proposal-notes" class="input">${proposal ? escapeHtml(proposal.notes || '') : ''}</textarea></div>
        <div id="proposal-form-error" class="field-error"></div>
        <div class="modal-actions">
          <button type="button" id="proposal-form-cancel" class="btn ghost">Annulla</button>
          <button type="submit" id="proposal-form-submit" class="btn primary">Salva</button>
        </div>
      </form>
    `;

    dialogEl.querySelector('#proposal-form-cancel').addEventListener('click', () => dialogEl.close());

    let submitting = false;
    dialogEl.querySelector('#proposal-form').addEventListener('submit', async (event) => {
      event.preventDefault();
      if (submitting) return;
      const errorEl = dialogEl.querySelector('#proposal-form-error');
      if (errorEl) errorEl.textContent = '';

      let payload;
      try {
        payload = isEdit ? buildProposalUpdatePayload(dialogEl) : buildProposalCreatePayload(dialogEl, createIdempotencyKey);
      } catch (error) {
        if (errorEl) errorEl.textContent = error.message || 'Dati non validi.';
        return;
      }

      submitting = true;
      const submitBtn = dialogEl.querySelector('#proposal-form-submit');
      const cancelBtn = dialogEl.querySelector('#proposal-form-cancel');
      submitBtn.disabled = true;
      cancelBtn.disabled = true;
      submitBtn.textContent = 'Salvataggio\u2026';
      try {
        if (isEdit) {
          await apiPatch(`/api/proposals/${proposal.id}`, payload);
        } else {
          await apiPost('/api/proposals', payload);
        }
        dialogEl.close();
        await reloadProposals();
        showTab('proposte');
        const fb = contentEl.querySelector('#proposal-feedback');
        if (fb) fb.innerHTML = `<div class="success-box">${isEdit ? 'Proposta aggiornata.' : 'Proposta creata.'}</div>`;
      } catch (error) {
        submitting = false;
        submitBtn.disabled = false;
        cancelBtn.disabled = false;
        submitBtn.textContent = 'Salva';
        if (errorEl) errorEl.textContent = error.message || 'Errore nel salvataggio.';
      }
    });

    dialogEl.showModal();
  }

  async function runProposalTransition(btn, proposalId, targetStatus) {
    const allButtons = Array.from(contentEl.querySelectorAll('.proposal-action-btn'));
    const originalText = btn.textContent;
    allButtons.forEach((b) => { b.disabled = true; });
    btn.textContent = 'Attendere\u2026';
    const feedbackEl = contentEl.querySelector('#proposal-feedback');
    if (feedbackEl) feedbackEl.innerHTML = '';
    try {
      await apiPost(`/api/proposals/${proposalId}/transition`, { target_status: targetStatus });
      await reloadProposals();
      showTab('proposte');
      const fb = contentEl.querySelector('#proposal-feedback');
      if (fb) fb.innerHTML = '<div class="success-box">Stato proposta aggiornato.</div>';
    } catch (error) {
      allButtons.forEach((b) => { b.disabled = false; });
      btn.textContent = originalText;
      const fb = contentEl.querySelector('#proposal-feedback');
      if (fb) fb.innerHTML = `<div class="error-box">${escapeHtml(error.message || 'Errore nella transizione di stato.')}</div>`;
    }
  }

  tabsEl.querySelectorAll('.tab-btn').forEach((btn) => {
    btn.addEventListener('click', () => showTab(btn.dataset.tab));
  });

  await showTab('panoramica');
}

// --- Panoramica -------------------------------------------------------

function renderPanoramica(p, editMode) {
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
    ['Assegnato a', p.assigned_to], ['Fonte', p.source],
    ['Punteggio completezza', p.readiness_score != null ? `${p.readiness_score}%` : null],
  ];
  return `
    <h3 class="section-title">Dati immobile</h3>
    <div class="detail-grid">
      ${fields.map(([label, value]) => `<div class="detail-item"><label>${escapeHtml(label)}</label>${escapeHtml(value === null || value === undefined || value === '' ? '—' : value)}</div>`).join('')}
    </div>
    ${renderIncaricoSection(p, editMode)}
    <h3 class="section-title">Note</h3>
    <p>${escapeHtml(p.public_notes || p.internal_notes || 'Nessuna nota.')}</p>
  `;
}

// --- Incarico (mandate_type/mandate_start/mandate_end su properties; P8) ---
// Source of truth: properties.mandate_type/mandate_start/mandate_end (nessuna
// nuova entita' introdotta). Scrittura tramite il contratto generico gia'
// esistente PATCH /api/property/properties/{id} (PropertyUpdate) — nessun
// endpoint dedicato. commercial_status non viene mai incluso nel payload di
// questa sezione: resta indipendente (property/schemas.py non impone alcun
// accoppiamento tra i campi mandate_* e commercial_status).
function renderIncaricoSection(p, editMode) {
  if (!editMode) {
    return `
      <h3 class="section-title">Incarico</h3>
      <div class="detail-grid">
        <div class="detail-item"><label>Tipo incarico</label>${escapeHtml(p.mandate_type || '—')}</div>
        <div class="detail-item"><label>Data inizio</label>${escapeHtml(formatDate(p.mandate_start))}</div>
        <div class="detail-item"><label>Data scadenza</label>${escapeHtml(formatDate(p.mandate_end))}</div>
      </div>
      <div class="action-bar" style="margin-top:12px">
        <button type="button" id="incarico-edit-btn" class="btn ghost">Modifica</button>
      </div>
    `;
  }
  return `
    <h3 class="section-title">Incarico</h3>
    <div class="form-grid-3">
      <div class="form-field"><label>Tipo incarico</label><input type="text" id="incarico-type" class="input" maxlength="80" value="${escapeHtml(p.mandate_type || '')}"></div>
      <div class="form-field"><label>Data inizio</label><input type="date" id="incarico-start" class="input" value="${toDateInputValue(p.mandate_start)}"></div>
      <div class="form-field"><label>Data scadenza</label><input type="date" id="incarico-end" class="input" value="${toDateInputValue(p.mandate_end)}"></div>
    </div>
    <div id="incarico-error" class="field-error"></div>
    <div class="action-bar" style="margin-top:4px">
      <button type="button" id="incarico-cancel-btn" class="btn ghost">Annulla</button>
      <button type="button" id="incarico-save-btn" class="btn primary">Salva</button>
    </div>
  `;
}

function toDateInputValue(value) {
  if (!value) return '';
  return String(value).slice(0, 10);
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

// --- Proposte (P9: operative — creazione, modifica in bozza, transizioni) --

const PROPOSAL_STATUS_LABELS = {
  draft: 'Bozza', submitted: 'Inviata', accepted: 'Accettata',
  rejected: 'Rifiutata', expired: 'Scaduta', withdrawn: 'Ritirata',
};

const PROPOSAL_ACTION_LABELS = {
  edit: 'Modifica', submitted: 'Invia', accepted: 'Accetta',
  rejected: 'Rifiuta', withdrawn: 'Ritira', expired: 'Segna scaduta',
};

// Stessa logica di static/buy_admin/assets/app.js:proposalActions (riferimento
// comportamentale P9), riletta sullo stato reale della proposta. "expired" e'
// solo un suggerimento client-side: il backend ricontrolla sempre database_now.
function proposalActions(proposal) {
  if (proposal.status === 'draft') return ['edit', 'submitted', 'withdrawn'];
  if (proposal.status === 'submitted') {
    const actions = ['accepted', 'rejected', 'withdrawn'];
    const expiresAt = new Date(proposal.expires_at);
    if (!Number.isNaN(expiresAt.getTime()) && expiresAt.getTime() <= Date.now()) actions.push('expired');
    return actions;
  }
  return [];
}

function renderProposalActionButtons(pr) {
  const actions = proposalActions(pr);
  if (!actions.length) return '';
  return `<div class="action-bar">${actions.map((a) => `<button type="button" class="btn ghost proposal-action-btn" data-proposal-id="${escapeHtml(pr.id)}" data-action="${escapeHtml(a)}">${escapeHtml(PROPOSAL_ACTION_LABELS[a] || a)}</button>`).join('')}</div>`;
}

function renderProposte(items) {
  const table = renderTable(
    [
      { label: 'Proposta', render: (pr) => `#${escapeHtml(pr.id)}` },
      { label: 'Acquirente', render: (pr) => escapeHtml(pr.contact_name || `Contatto #${pr.contact_id}`) },
      { label: 'Importo', render: (pr) => formatPrice(pr.amount) },
      { label: 'Stato', render: (pr) => renderBadge(PROPOSAL_STATUS_LABELS[pr.status] || pr.status || '—', statusTone(pr.status)) },
      { label: 'Scadenza', render: (pr) => escapeHtml(formatDateTime(pr.expires_at)) },
      { label: '', render: (pr) => renderProposalActionButtons(pr) },
    ],
    items,
    { emptyMessage: 'Nessuna proposta presente per questo immobile.' },
  );
  return `
    <div class="action-bar" style="margin-bottom:12px">
      <button type="button" id="proposal-new-btn" class="btn primary">Nuova proposta</button>
    </div>
    <div id="proposal-feedback"></div>
    ${table}
  `;
}

// Etichetta di un abbinamento eleggibile nel selettore "Nuova proposta": mai
// un match_id digitato manualmente, solo scelta da un elenco di abbinamenti
// reali gia' presenti (stesso principio gia' applicato in P3 per il Contatto).
function matchOptionLabel(m) {
  const buyer = m.buyer_name || `Richiesta #${m.buy_request_id}`;
  const scoreText = m.effective_score ?? m.score_total;
  return `${buyer}${scoreText != null ? ` \u2014 punteggio ${scoreText}` : ''}${m.match_class ? ` (${m.match_class})` : ''}`;
}

function proposalDateTimeLocal(value) {
  if (!value) return '';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '';
  return new Date(parsed.getTime() - parsed.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

// Converte il valore di un input datetime-local (orario locale del browser,
// senza timezone) in un ISO string con offset esplicito, come richiesto da
// proposal/schemas.py (expiry_requires_timezone). Stesso pattern di
// static/buy_admin/assets/app.js:proposalExpiry.
function proposalExpiryToIso(raw) {
  const trimmed = String(raw || '').trim();
  if (!trimmed) throw new Error('Data di scadenza non valida.');
  const parsed = new Date(trimmed);
  if (Number.isNaN(parsed.getTime())) throw new Error('Data di scadenza non valida.');
  return parsed.toISOString();
}

function proposalAmountValue(raw) {
  const value = Number(raw);
  if (!Number.isFinite(value) || value <= 0) throw new Error('Importo deve essere maggiore di zero.');
  return value;
}

function buildProposalCreatePayload(dialogEl, idempotencyKey) {
  const matchSelect = dialogEl.querySelector('#proposal-match');
  const matchId = matchSelect ? Number(matchSelect.value) : NaN;
  if (!Number.isInteger(matchId) || matchId <= 0) throw new Error('Seleziona un abbinamento valido.');
  const notes = dialogEl.querySelector('#proposal-notes').value.trim();
  return {
    match_id: matchId,
    amount: proposalAmountValue(dialogEl.querySelector('#proposal-amount').value),
    expires_at: proposalExpiryToIso(dialogEl.querySelector('#proposal-expiry').value),
    notes: notes || null,
    idempotency_key: idempotencyKey,
  };
}

function buildProposalUpdatePayload(dialogEl) {
  const notes = dialogEl.querySelector('#proposal-notes').value.trim();
  return {
    amount: proposalAmountValue(dialogEl.querySelector('#proposal-amount').value),
    expires_at: proposalExpiryToIso(dialogEl.querySelector('#proposal-expiry').value),
    notes: notes || null,
  };
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
