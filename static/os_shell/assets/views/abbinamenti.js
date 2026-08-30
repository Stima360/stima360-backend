// STIMA360 OS — abbinamenti.js
// Lista/graduatoria MATCH + centro di calcolo nuovi abbinamenti. MATCH resta
// proprietario di tutta la logica di scoring/readiness/freschezza: questo
// file usa esclusivamente le API MATCH gia' esistenti, nessun algoritmo o
// regola viene ricostruita lato browser.
//
// Endpoint reali verificati (match/router.py, prefix /api/match):
//   GET  /api/match/dashboard                                  (router.py:23)
//   GET  /api/match/matches?limit&offset&buy_request_id&property_id&
//        match_class&commercial_status&compatible_only&freshness_status&
//        review_required                                       (router.py:93-113)
//   GET  /api/match/readiness?buy_request_id&property_id        (router.py:41-46)
//   POST /api/match/calculate                    {buy_request_id,property_id} (router.py:49-51)
//   POST /api/match/buy-requests/{id}/calculate   {}             (router.py:53-55)
//   POST /api/match/properties/{id}/calculate     {}             (router.py:57-59)
// match/repository.py:list_matches/get_match fanno gia' JOIN su buy_requests/
// contacts/properties (buy_title, buyer_name, property_title, property_code,
// city, microzone, asking_price, classification, effective_score): NESSUNA
// chiamata N+1 per riga necessaria per popolare la graduatoria.
//
// Selettori Richiesta BUY / Immobile nel dialog "Calcola abbinamenti" riusano
// GET /api/buy/requests?search=... (buy/router.py:17, gia' verificato in P3)
// e GET /api/property/properties?search=... (property/router.py:18, gia'
// verificato in P2). Nessun ID numerico digitabile dall'operatore: l'ID viene
// solo conservato internamente dopo la selezione da lista.
//
// Readiness: eseguita SEMPRE prima di abilitare il pulsante di calcolo
// (GET /api/match/readiness), mai ricostruita lato frontend (match/readiness.py
// resta l'unica fonte di verita'). Se can_match e' false, il calcolo non parte
// e vengono mostrati i motivi reali restituiti dal backend.
//
// Nessun calcolo automatico: /api/match/calculate e le due varianti batch
// vengono chiamate SOLO al click esplicito del pulsante "Calcola" del dialog,
// mai all'apertura della pagina o della lista.
//
// Filtri di lista implementati senza richiedere ID: match_class, stato
// commerciale, freschezza, revisione richiesta, solo compatibili (tutti
// select/checkbox sui valori enum reali di match/enums.py). Filtrare la
// graduatoria per uno specifico Acquirente/Immobile tramite ricerca per nome
// non e' stato implementato in questa fase (gap, vedi report finale): i
// filtri buy_request_id/property_id restano disponibili lato API ma non
// sono esposti come picker nella lista (lo sono invece, come richiesto dal
// brief, nel dialog di calcolo).
//
// Dashboard e lista sono resilienti indipendentemente: un fallimento della
// dashboard non impedisce l'uso della lista e viceversa (nessun Promise.all
// che le lega).

import { apiGet, apiPost } from '../core/api-client.js';
import { navigate } from '../core/router.js';
import { renderTable, bindTableRowClicks, renderBadge, escapeHtml, formatDateTime } from '../components/st-table.js';

const PAGE_SIZE = 50;

// Valori reali (match/enums.py).
const MATCH_CLASSES = ['excellent', 'strong', 'good', 'possible', 'weak', 'poor', 'incompatible'];
const MATCH_CLASS_LABELS = { excellent: 'Eccellente', strong: 'Forte', good: 'Buono', possible: 'Possibile', weak: 'Debole', poor: 'Scarso', incompatible: 'Incompatibile' };
const FRESHNESS_STATUSES = ['fresh', 'stale', 'recalculating', 'failed', 'excluded'];
const FRESHNESS_LABELS = { fresh: 'Aggiornato', stale: 'Da ricalcolare', recalculating: 'Ricalcolo in corso', failed: 'Errore', excluded: 'Escluso' };
const COMMERCIAL_STATUSES = ['new', 'to_review', 'approved', 'rejected', 'suggested', 'interested', 'visit_requested', 'visit_scheduled', 'visited', 'offer_candidate', 'archived'];
const COMMERCIAL_STATUS_LABELS = { new: 'Nuovo', to_review: 'Da valutare', approved: 'Approvato', rejected: 'Rifiutato', suggested: 'Suggerito', interested: 'Interessato', visit_requested: 'Visita richiesta', visit_scheduled: 'Visita programmata', visited: 'Visitato', offer_candidate: 'Candidato offerta', archived: 'Archiviato' };

export async function renderAbbinamenti(container) {
  container.innerHTML = `
    <div id="abbinamenti-dashboard"><p class="muted">Caricamento riepilogo…</p></div>
    <div class="card panel">
      <div class="list-toolbar">
        <select id="f-match-class" class="input">
          <option value="">Tutte le classi</option>
          ${MATCH_CLASSES.map((c) => `<option value="${c}">${escapeHtml(MATCH_CLASS_LABELS[c])}</option>`).join('')}
        </select>
        <select id="f-commercial-status" class="input">
          <option value="">Tutti gli stati commerciali</option>
          ${COMMERCIAL_STATUSES.map((s) => `<option value="${s}">${escapeHtml(COMMERCIAL_STATUS_LABELS[s])}</option>`).join('')}
        </select>
        <select id="f-freshness" class="input">
          <option value="">Ogni freschezza</option>
          ${FRESHNESS_STATUSES.map((s) => `<option value="${s}">${escapeHtml(FRESHNESS_LABELS[s])}</option>`).join('')}
        </select>
        <select id="f-review" class="input">
          <option value="">Revisione: tutte</option>
          <option value="true">Da revisionare</option>
          <option value="false">Revisionate</option>
        </select>
        <label class="checkbox-label">
          <input type="checkbox" id="f-compatible-only"> Solo compatibili
        </label>
        <button type="button" id="abbinamenti-calc" class="btn primary">Calcola abbinamenti</button>
      </div>
      <div id="abbinamenti-list-area"><p class="muted">Caricamento…</p></div>
      <div id="abbinamenti-pager" class="list-pager"></div>
    </div>
    <dialog id="calc-dialog" class="modal modal-wide"></dialog>
  `;

  const dashboardArea = container.querySelector('#abbinamenti-dashboard');
  const listArea = container.querySelector('#abbinamenti-list-area');
  const pagerArea = container.querySelector('#abbinamenti-pager');
  const classSelect = container.querySelector('#f-match-class');
  const statusSelect = container.querySelector('#f-commercial-status');
  const freshnessSelect = container.querySelector('#f-freshness');
  const reviewSelect = container.querySelector('#f-review');
  const compatibleOnly = container.querySelector('#f-compatible-only');
  const calcDialog = container.querySelector('#calc-dialog');

  let offset = 0;

  async function loadDashboard() {
    try {
      const d = await apiGet('/api/match/dashboard');
      dashboardArea.innerHTML = renderDashboard(d);
    } catch (error) {
      dashboardArea.innerHTML = `<div class="error-box">Riepilogo MATCH temporaneamente non disponibile: ${escapeHtml(error.message)}</div>`;
    }
  }

  async function loadList() {
    listArea.innerHTML = '<p class="muted">Caricamento…</p>';
    pagerArea.innerHTML = '';
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
    if (classSelect.value) params.set('match_class', classSelect.value);
    if (statusSelect.value) params.set('commercial_status', statusSelect.value);
    if (freshnessSelect.value) params.set('freshness_status', freshnessSelect.value);
    if (reviewSelect.value) params.set('review_required', reviewSelect.value);
    if (compatibleOnly.checked) params.set('compatible_only', 'true');

    let items = [];
    try {
      const data = await apiGet(`/api/match/matches?${params.toString()}`);
      items = Array.isArray(data?.items) ? data.items : [];
    } catch (error) {
      listArea.innerHTML = `<div class="error-box">Impossibile caricare la graduatoria: ${escapeHtml(error.message)}</div>`;
      return;
    }

    const anyFilter = classSelect.value || statusSelect.value || freshnessSelect.value || reviewSelect.value || compatibleOnly.checked;

    listArea.innerHTML = renderTable(
      [
        { label: 'Acquirente / Richiesta', render: (m) => `<strong>${escapeHtml(m.buyer_name || `Richiesta #${m.buy_request_id}`)}</strong><br><small class="muted">${escapeHtml(m.buy_title || '—')}</small>` },
        { label: 'Immobile', render: (m) => `<strong>${escapeHtml(m.property_title || m.property_code || `Immobile #${m.property_id}`)}</strong><br><small class="muted">${escapeHtml(m.city || '—')}</small>` },
        { label: 'Punteggio', render: (m) => renderScoreCell(m) },
        { label: 'Classe', render: (m) => renderBadge(MATCH_CLASS_LABELS[m.match_class] || m.match_class || '—', matchClassTone(m.match_class)) },
        { label: 'Freschezza', render: (m) => renderBadge(FRESHNESS_LABELS[m.freshness_status] || m.freshness_status || '—', freshnessTone(m.freshness_status)) },
        { label: 'Stato commerciale', render: (m) => escapeHtml(COMMERCIAL_STATUS_LABELS[m.commercial_status] || m.commercial_status || '—') },
        { label: 'Revisione', render: (m) => m.review_required ? renderBadge('Da revisionare', 'warn') : '' },
        { label: 'Ultimo calcolo', render: (m) => escapeHtml(formatDateTime(m.last_calculated_at)) },
      ],
      items,
      { emptyMessage: anyFilter ? 'Nessun abbinamento trovato per questi filtri.' : 'Nessun abbinamento presente. Usa "Calcola abbinamenti" per crearne.', onRowClick: true },
    );
    bindTableRowClicks(listArea, (id) => navigate('abbinamenti', [id]));

    pagerArea.innerHTML = `
      <button class="btn" id="abbinamenti-prev" ${offset === 0 ? 'disabled' : ''}>← Precedenti</button>
      <span class="muted">Risultati da ${items.length ? offset + 1 : 0} a ${offset + items.length}</span>
      <button class="btn" id="abbinamenti-next" ${items.length < PAGE_SIZE ? 'disabled' : ''}>Successivi →</button>
    `;
    const prevBtn = pagerArea.querySelector('#abbinamenti-prev');
    const nextBtn = pagerArea.querySelector('#abbinamenti-next');
    if (prevBtn) prevBtn.onclick = () => { offset = Math.max(0, offset - PAGE_SIZE); loadList(); };
    if (nextBtn) nextBtn.onclick = () => { offset += PAGE_SIZE; loadList(); };
  }

  [classSelect, statusSelect, freshnessSelect, reviewSelect].forEach((el) => {
    el.addEventListener('change', () => { offset = 0; loadList(); });
  });
  compatibleOnly.addEventListener('change', () => { offset = 0; loadList(); });

  container.querySelector('#abbinamenti-calc').addEventListener('click', () => {
    openCalcDialog(calcDialog, () => { loadDashboard(); offset = 0; loadList(); });
  });

  await Promise.all([loadDashboard(), loadList()]);
}

function renderDashboard(d) {
  const kpis = [
    ['Totali', d.total], ['Fresh', d.fresh], ['Stale', d.stale], ['Falliti', d.failed],
    ['Da revisionare', d.review_required], ['Strong/Excellent', d.strong],
    ['Override', d.overridden], ['Feedback negativi', d.negative_feedback],
  ];
  return `
    <div class="kpi-grid">
      ${kpis.map(([label, value]) => `
        <div class="card kpi">
          <span class="kpi-label">${escapeHtml(label)}</span>
          <strong class="kpi-value">${value ?? 0}</strong>
        </div>
      `).join('')}
    </div>
  `;
}

function renderScoreCell(m) {
  const effective = m.effective_score ?? m.score_total;
  const base = `<strong>${escapeHtml(effective ?? '—')}</strong>`;
  if (m.is_manual_override) return `${base}<br><small class="muted">manuale (tecnico ${escapeHtml(m.score_total ?? '—')})</small>`;
  return base;
}

function matchClassTone(matchClass) {
  if (['excellent', 'strong'].includes(matchClass)) return 'ok';
  if (['good', 'possible'].includes(matchClass)) return 'warn';
  if (['weak', 'poor', 'incompatible'].includes(matchClass)) return 'danger';
  return 'gray';
}

function freshnessTone(status) {
  if (status === 'fresh') return 'ok';
  if (status === 'stale' || status === 'recalculating') return 'warn';
  if (status === 'failed') return 'danger';
  return 'gray';
}

// --- Dialog "Calcola abbinamenti" ------------------------------------------

const CALC_MODES = [
  { key: 'pair', label: 'Confronta richiesta e immobile' },
  { key: 'buy', label: 'Trova immobili per un acquirente' },
  { key: 'property', label: 'Trova acquirenti per un immobile' },
];

function openCalcDialog(dialogEl, onDone) {
  let mode = 'pair';
  let selectedBuy = null;
  let selectedProperty = null;
  let readiness = null;
  let readinessBusy = false;
  let calcBusy = false;

  dialogEl.innerHTML = `
    <h2 style="margin-top:0">Calcola abbinamenti</h2>
    <div class="tabs" id="calc-modes"></div>
    <div id="calc-buy-picker"></div>
    <div id="calc-property-picker"></div>
    <h3 class="section-title">Readiness</h3>
    <div id="calc-readiness"><p class="muted">Seleziona i dati richiesti per verificare la readiness.</p></div>
    <div id="calc-error" class="field-error"></div>
    <div id="calc-result"></div>
    <div class="modal-actions">
      <button type="button" id="calc-cancel" class="btn ghost">Chiudi</button>
      <button type="button" id="calc-run" class="btn primary" disabled>Calcola</button>
    </div>
  `;

  const modesEl = dialogEl.querySelector('#calc-modes');
  const buyPickerEl = dialogEl.querySelector('#calc-buy-picker');
  const propertyPickerEl = dialogEl.querySelector('#calc-property-picker');
  const readinessEl = dialogEl.querySelector('#calc-readiness');
  const errorEl = dialogEl.querySelector('#calc-error');
  const resultEl = dialogEl.querySelector('#calc-result');
  const cancelBtn = dialogEl.querySelector('#calc-cancel');
  const runBtn = dialogEl.querySelector('#calc-run');

  modesEl.innerHTML = CALC_MODES.map((m, i) => `<button type="button" class="tab-btn ${i === 0 ? 'active' : ''}" data-mode="${m.key}">${escapeHtml(m.label)}</button>`).join('');

  createEntityPicker(buyPickerEl, {
    label: 'Richiesta BUY (acquirente)',
    placeholder: 'Cerca per nome contatto, email, telefono o titolo…',
    search: (term) => apiGet(`/api/buy/requests?search=${encodeURIComponent(term)}&limit=10`),
    itemLabel: (r) => r.contact_name || `Contatto #${r.contact_id}`,
    itemSub: (r) => `${r.title || `Richiesta #${r.id}`} · ${r.status || '—'}`,
    onChange: (value) => { selectedBuy = value; resetResult(); refreshReadiness(); },
  });

  createEntityPicker(propertyPickerEl, {
    label: 'Immobile',
    placeholder: 'Cerca per titolo, codice, indirizzo o comune…',
    search: (term) => apiGet(`/api/property/properties?search=${encodeURIComponent(term)}&limit=10`),
    itemLabel: (p) => p.title || p.code || `Immobile #${p.id}`,
    itemSub: (p) => `${p.city || '—'} · ${p.commercial_status || '—'}`,
    onChange: (value) => { selectedProperty = value; resetResult(); refreshReadiness(); },
  });

  function applyModeVisibility() {
    buyPickerEl.hidden = mode === 'property';
    propertyPickerEl.hidden = mode === 'buy';
  }

  function resetResult() {
    resultEl.innerHTML = '';
    errorEl.textContent = '';
  }

  function setRunLabel() {
    if (mode === 'pair') runBtn.textContent = 'Calcola';
    else if (mode === 'buy') runBtn.textContent = 'Calcola graduatoria';
    else runBtn.textContent = 'Trova acquirenti';
  }

  function updateRunState() {
    const ready = readiness && readiness.can_match === true;
    const selectionComplete = mode === 'pair' ? (selectedBuy && selectedProperty)
      : mode === 'buy' ? Boolean(selectedBuy)
      : Boolean(selectedProperty);
    runBtn.disabled = calcBusy || readinessBusy || !selectionComplete || !ready;
  }

  async function refreshReadiness() {
    readiness = null;
    const needBuy = mode !== 'property';
    const needProperty = mode !== 'buy';
    const haveBuy = !needBuy || Boolean(selectedBuy);
    const haveProperty = !needProperty || Boolean(selectedProperty);
    if (!haveBuy || !haveProperty) {
      readinessEl.innerHTML = '<p class="muted">Seleziona i dati richiesti per verificare la readiness.</p>';
      updateRunState();
      return;
    }
    readinessBusy = true;
    updateRunState();
    readinessEl.innerHTML = '<p class="muted">Verifica readiness…</p>';
    try {
      const query = new URLSearchParams();
      if (needBuy) query.set('buy_request_id', String(selectedBuy.id));
      if (needProperty) query.set('property_id', String(selectedProperty.id));
      readiness = await apiGet(`/api/match/readiness?${query.toString()}`);
      readinessEl.innerHTML = renderReadinessBlock(readiness);
    } catch (error) {
      readiness = null;
      readinessEl.innerHTML = `<div class="error-box">Impossibile verificare la readiness: ${escapeHtml(error.message)}</div>`;
    } finally {
      readinessBusy = false;
      updateRunState();
    }
  }

  modesEl.querySelectorAll('.tab-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      mode = btn.dataset.mode;
      modesEl.querySelectorAll('.tab-btn').forEach((b) => b.classList.toggle('active', b === btn));
      applyModeVisibility();
      setRunLabel();
      resetResult();
      refreshReadiness();
    });
  });

  cancelBtn.addEventListener('click', () => dialogEl.close());

  runBtn.addEventListener('click', async () => {
    if (runBtn.disabled) return;
    calcBusy = true;
    updateRunState();
    const previousLabel = runBtn.textContent;
    runBtn.textContent = 'Calcolo…';
    errorEl.textContent = '';
    resultEl.innerHTML = '';
    try {
      if (mode === 'pair') {
        const created = await apiPost('/api/match/calculate', { buy_request_id: selectedBuy.id, property_id: selectedProperty.id });
        resultEl.innerHTML = renderSingleResult(created);
        resultEl.querySelector('#calc-open-match').addEventListener('click', () => {
          dialogEl.close();
          navigate('abbinamenti', [created.id]);
        });
      } else if (mode === 'buy') {
        const data = await apiPost(`/api/match/buy-requests/${selectedBuy.id}/calculate`, {});
        resultEl.innerHTML = renderBatchResult(data, 'buy_request_id');
      } else {
        const data = await apiPost(`/api/match/properties/${selectedProperty.id}/calculate`, {});
        resultEl.innerHTML = renderBatchResult(data, 'property_id');
      }
      const closeBtn = resultEl.querySelector('#calc-close-refresh');
      if (closeBtn) closeBtn.addEventListener('click', () => { dialogEl.close(); onDone(); });
      if (onDone) onDone();
    } catch (error) {
      errorEl.textContent = error.message || 'Errore nel calcolo.';
    } finally {
      calcBusy = false;
      runBtn.textContent = previousLabel;
      updateRunState();
    }
  });

  applyModeVisibility();
  setRunLabel();
  updateRunState();
  dialogEl.showModal();
}

function renderReadinessBlock(data) {
  const sides = [['Richiesta BUY', data.buy], ['Immobile', data.property]].filter(([, side]) => side);
  const overall = data.can_match
    ? renderBadge('READY', 'ok')
    : renderBadge('NON PRONTO', 'danger');
  return `
    <div>${overall}</div>
    ${sides.map(([label, side]) => {
      const reasons = !side.eligible ? (side.eligibility_reasons || []) : (side.reasons || []);
      return `
        <div class="detail-item readiness-side">
          <label>${escapeHtml(label)} #${escapeHtml(side.id)}</label>
          ${side.can_match ? renderBadge('OK', 'ok') : renderBadge(!side.eligible ? 'Non eligibile' : 'Non pronto', 'danger')}
          ${reasons.length ? `<ul>${reasons.map((r) => `<li>${escapeHtml(r)}</li>`).join('')}</ul>` : ''}
        </div>
      `;
    }).join('')}
  `;
}

function renderSingleResult(match) {
  return `
    <div class="success-box">
      Match creato/aggiornato: punteggio ${escapeHtml(match.score_total)}, classe ${escapeHtml(MATCH_CLASS_LABELS[match.match_class] || match.match_class)}.
    </div>
    <div class="action-bar">
      <button type="button" id="calc-open-match" class="btn primary">Apri abbinamento</button>
    </div>
  `;
}

function renderBatchResult(data, errorIdKey) {
  const errors = Array.isArray(data.errors) ? data.errors : [];
  return `
    <div class="success-box">
      ${escapeHtml(data.count ?? 0)} abbinamenti calcolati/aggiornati.
    </div>
    ${errors.length ? `
      <h3 class="section-title">Elementi non elaborati</h3>
      <ul>${errors.map((e) => `<li>${errorIdKey === 'buy_request_id' ? `Immobile #${escapeHtml(e.property_id)}` : `Richiesta #${escapeHtml(e.buy_request_id)}`}: ${escapeHtml(e.error)}</li>`).join('')}</ul>
    ` : ''}
    <div class="action-bar">
      <button type="button" id="calc-close-refresh" class="btn primary">Chiudi e aggiorna lista</button>
    </div>
  `;
}

// Picker generico di ricerca (Richiesta BUY / Immobile), stesso pattern del
// contact-picker gia' usato in acquirenti.js (nessun ID digitabile).
function createEntityPicker(rootEl, { label, placeholder, search, itemLabel, itemSub, onChange }) {
  let selected = null;
  let debounceHandle = null;

  rootEl.innerHTML = `
    <div class="form-field">
      <label>${escapeHtml(label)}</label>
      <input type="search" class="input picker-search" placeholder="${escapeHtml(placeholder)}" autocomplete="off">
      <div class="picker-results"></div>
      <div class="picker-selected" hidden></div>
    </div>
  `;

  const searchInput = rootEl.querySelector('.picker-search');
  const resultsEl = rootEl.querySelector('.picker-results');
  const selectedEl = rootEl.querySelector('.picker-selected');

  function select(item) {
    selected = item;
    resultsEl.innerHTML = '';
    searchInput.value = '';
    searchInput.hidden = true;
    selectedEl.hidden = false;
    selectedEl.innerHTML = `
      <div class="selected-contact-card">
        <div><strong>${escapeHtml(itemLabel(item))}</strong><br><small class="muted">${escapeHtml(itemSub(item))}</small></div>
        <button type="button" class="btn ghost picker-change">Cambia</button>
      </div>
    `;
    selectedEl.querySelector('.picker-change').addEventListener('click', () => {
      selected = null;
      selectedEl.hidden = true;
      selectedEl.innerHTML = '';
      searchInput.hidden = false;
      searchInput.value = '';
      onChange(null);
    });
    onChange(item);
  }

  searchInput.addEventListener('input', () => {
    clearTimeout(debounceHandle);
    const term = searchInput.value.trim();
    if (!term) { resultsEl.innerHTML = ''; return; }
    debounceHandle = setTimeout(async () => {
      resultsEl.innerHTML = '<p class="muted">Ricerca…</p>';
      try {
        const data = await search(term);
        const items = Array.isArray(data?.items) ? data.items : [];
        resultsEl.innerHTML = items.length
          ? `<div class="list">${items.map((it, i) => `<div class="list-item picker-result" data-index="${i}" style="cursor:pointer"><span><strong>${escapeHtml(itemLabel(it))}</strong><br><small class="muted">${escapeHtml(itemSub(it))}</small></span></div>`).join('')}</div>`
          : '<p class="muted">Nessun risultato.</p>';
        resultsEl.querySelectorAll('.picker-result').forEach((el) => {
          el.addEventListener('click', () => select(items[Number(el.dataset.index)]));
        });
      } catch (error) {
        resultsEl.innerHTML = `<div class="error-box">Errore nella ricerca: ${escapeHtml(error.message)}</div>`;
      }
    }, 300);
  });

  return { get selected() { return selected; } };
}
