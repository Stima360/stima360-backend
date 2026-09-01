// STIMA360 OS — abbinamento-dettaglio.js
// Scheda operativa di un singolo Match. MATCH resta proprietario di scoring/
// readiness/freschezza/stale-detection/override: questa vista legge solo le
// API MATCH gia' esistenti, nessuna logica viene ricostruita lato browser.
//
// Endpoint reali verificati (match/router.py, prefix /api/match):
//   GET   /api/match/matches/{id}                 (router.py:121-122, get_match)
//   PATCH /api/match/matches/{id}                  {commercial_status?|priority?|
//         assigned_to?|review_required?}           (router.py:125-127, MatchUpdate,
//         extra="forbid": SOLO questi 4 campi, mai l'intero oggetto Match)
//   POST  /api/match/matches/{id}/refresh          {trigger_reason?}
//         (router.py:69-71)
//   GET   /api/match/matches/{id}/refresh-history   (router.py:133-135)
//   GET   /api/match/matches/{id}/feedback          (router.py:145-147)
//   POST  /api/match/matches/{id}/feedback          {source,feedback_type,
//         reason_code?,notes?}                      (router.py:141-143, schema
//         FeedbackCreate: reason_code obbligatorio SOLO se feedback_type=='negative')
//
// P16 (patch MATCH -> VISITA): "Programma visita" chiama l'endpoint BUY gia'
// esistente POST /api/buy/requests/{request_id}/matches/{match_id}/decision
// (buy/router.py:31, schema MatchDecision) con action='visit_scheduled'.
// Verificato in buy/repository.py:schedule_match_visit che questo endpoint
// crea atomicamente property_visits + buy_request_interactions(match_id,
// buy_request_id, property_visit_id, interaction_type='visit_scheduled') e
// aggiorna matches.commercial_status='visit_scheduled' — e' l'UNICO percorso
// che produce una visita realmente collegata al match (property_visits non
// ha una colonna match_id: la CRUD standalone in immobile-dettaglio.js resta
// per le visite senza contesto MATCH). Nessun property_id/match_id/
// buy_request_id/contact_id/lead_id viene chiesto in questo dialog: sono
// tutti derivati server-side dal match/richiesta (schedule_match_visit
// legge match.property_id e buy_requests.contact_id/lead_id). Nessuna
// seconda POST verso /api/property/.../visits viene mai fatta da qui.
//
// GET .../matches/{id} e' indispensabile: se fallisce la scheda non apre.
// refresh-history e feedback sono secondari e caricati a tab (nessun
// Promise.all che li lega al GET principale ne' tra loro): un loro errore
// mostra "Temporaneamente non disponibile" solo nella propria tab.
//
// Timeline (GET .../matches/{id}/timeline, match/repository.py:timeline)
// verificata per intero: e' una UNION SQL di match_refresh_history e
// match_feedback ordinata per created_at, senza alcun campo aggiuntivo
// rispetto a quanto gia' mostrato nelle tab "Storico ricalcoli" e "Feedback".
// Per non introdurre una duplicazione inutile (esplicitamente sconsigliata
// dal brief P4), la tab Timeline non viene replicata separatamente: la
// struttura usa 4 tab (Panoramica, Analisi, Storico ricalcoli, Feedback)
// invece delle 5 suggerite. Riportato nel report finale.
//
// Override manuale: mostrato in sola lettura se presente (is_manual_override,
// manual_score, manual_reason) — NON e' editabile da questa vista (POST/DELETE
// /api/match/matches/{id}/override esistono ma sono esplicitamente esclusi da
// P4 dal brief, restano funzione della MATCH Admin legacy).
//
// Exclusions: non gestite qui. Se un match risulta escluso, freshness_status
// vale 'excluded' e viene mostrato correttamente come tale (nessuna azione
// di ricalcolo disponibile per un match escluso: coerente con
// match/repository.py:refresh_match, che rifiuta un match escluso).
//
// Nessun calcolo/refresh automatico: "Ricalcola abbinamento" parte solo al
// click esplicito. Nessuna chiamata viene fatta entrando nella pagina oltre
// al GET principale.

import { apiGet, apiPatch, apiPost } from '../core/api-client.js';
import { navigate } from '../core/router.js';
import { renderTable, renderBadge, escapeHtml, formatDateTime } from '../components/st-table.js';

const MATCH_CLASS_LABELS = { excellent: 'Eccellente', strong: 'Forte', good: 'Buono', possible: 'Possibile', weak: 'Debole', poor: 'Scarso', incompatible: 'Incompatibile' };
const FRESHNESS_LABELS = { fresh: 'Aggiornato', stale: 'Da ricalcolare', recalculating: 'Ricalcolo in corso', failed: 'Errore', excluded: 'Escluso' };
const COMPATIBILITY_STATUS_LABELS = { compatible: 'Compatibile', exception: 'Compatibile con eccezioni', incompatible: 'Incompatibile' };
const COMMERCIAL_STATUSES = ['new', 'to_review', 'approved', 'rejected', 'suggested', 'interested', 'visit_requested', 'visit_scheduled', 'visited', 'offer_candidate', 'archived'];
const COMMERCIAL_STATUS_LABELS = { new: 'Nuovo', to_review: 'Da valutare', approved: 'Approvato', rejected: 'Rifiutato', suggested: 'Suggerito', interested: 'Interessato', visit_requested: 'Visita richiesta', visit_scheduled: 'Visita programmata', visited: 'Visitato', offer_candidate: 'Candidato offerta', archived: 'Archiviato' };
const PRIORITY_LABELS = { low: 'Bassa', normal: 'Normale', high: 'Alta', urgent: 'Urgente' };
const CRITERION_TYPE_LABELS = { hard: 'Obbligatorio', soft: 'Opzionale', preference: 'Preferenza', informational: 'Informativo' };
const RESULT_LABELS = { matched: 'Soddisfatto', partially_matched: 'Parzialmente soddisfatto', not_matched: 'Non soddisfatto', not_available: 'Non disponibile', not_applicable: 'Non applicabile' };
const TRIGGER_SOURCE_LABELS = { manual: 'Manuale', buy: 'Richiesta BUY', property: 'Immobile', system: 'Sistema' };
const FEEDBACK_SOURCE_LABELS = { agent: 'Agente', buyer: 'Acquirente' };
const FEEDBACK_TYPE_LABELS = { positive: 'Positivo', neutral: 'Neutro', negative: 'Negativo' };
const FEEDBACK_REASON_LABELS = { price: 'Prezzo', location: 'Zona', size: 'Dimensioni', condition: 'Condizioni', floor: 'Piano', elevator: 'Ascensore', parking: 'Parcheggio', outdoor_space: 'Spazio esterno', not_available: 'Non disponibile', other: 'Altro' };

const TABS = [
  { key: 'panoramica', label: 'Panoramica' },
  { key: 'analisi', label: 'Analisi' },
  { key: 'storico', label: 'Storico ricalcoli' },
  { key: 'feedback', label: 'Feedback' },
];

export async function renderAbbinamentoDettaglio(container, params = []) {
  const matchId = params[0];
  if (!matchId || !/^\d+$/.test(String(matchId))) {
    container.innerHTML = '<div class="error-box">Identificativo abbinamento non valido.</div>';
    return;
  }

  container.innerHTML = '<p class="muted">Caricamento scheda abbinamento…</p>';

  let match;
  try {
    match = await apiGet(`/api/match/matches/${matchId}`);
  } catch (error) {
    const notFound = /non trovato|not found/i.test(error.message || '');
    container.innerHTML = `<div class="error-box">${notFound ? 'Abbinamento non trovato.' : `Errore nel caricamento dell'abbinamento: ${escapeHtml(error.message)}`}</div>`;
    return;
  }

  // Cache lazy per le tab secondarie: caricate al primo accesso, invalidate
  // dopo un ricalcolo riuscito (che genera una nuova riga di storico).
  const lazyCache = { history: null, feedback: null };

  render();

  function render() {
    container.innerHTML = `
      <div class="contact-header card">
        <h2>${renderPairHeading(match)}</h2>
        <div class="muted">Match #${escapeHtml(match.id)} · Richiesta: ${escapeHtml(match.buy_title || '—')} · Immobile: ${escapeHtml(match.property_code || match.property_title || '—')}${match.city ? ` · ${escapeHtml(match.city)}` : ''}</div>
        <div class="badge-row" id="match-badges"></div>
      </div>
      <div id="match-action-feedback"></div>
      <div class="tabs" id="match-tabs"></div>
      <div id="match-tab-content" class="card panel"></div>
      <dialog id="visit-schedule-dialog" class="modal"></dialog>
    `;

    container.querySelector('#match-badges').innerHTML = renderBadgeRow(match);

    const buyLink = container.querySelector('#match-buy-link');
    if (buyLink) buyLink.addEventListener('click', (event) => { event.preventDefault(); navigate('acquirenti', [match.buy_request_id]); });
    const propertyLink = container.querySelector('#match-property-link');
    if (propertyLink) propertyLink.addEventListener('click', (event) => { event.preventDefault(); navigate('immobili', [match.property_id]); });

    const tabsEl = container.querySelector('#match-tabs');
    tabsEl.innerHTML = TABS.map((t, i) => `<button type="button" class="tab-btn ${i === 0 ? 'active' : ''}" data-tab="${t.key}">${escapeHtml(t.label)}</button>`).join('');
    const contentEl = container.querySelector('#match-tab-content');

    async function showTab(key) {
      tabsEl.querySelectorAll('.tab-btn').forEach((b) => b.classList.toggle('active', b.dataset.tab === key));
      contentEl.innerHTML = '<p class="muted">Caricamento…</p>';
      try {
        if (key === 'panoramica') contentEl.innerHTML = renderPanoramica(match);
        else if (key === 'analisi') contentEl.innerHTML = renderAnalisi(match);
        else if (key === 'storico') contentEl.innerHTML = await renderStoricoLazy(matchId, lazyCache);
        else if (key === 'feedback') {
          contentEl.innerHTML = await renderFeedbackLazy(matchId, lazyCache);
          bindFeedbackForm(contentEl, matchId, () => { lazyCache.feedback = null; showTab('feedback'); });
        }
        else contentEl.innerHTML = '<p class="muted">Sezione non disponibile.</p>';
      } catch (error) {
        contentEl.innerHTML = `<div class="error-box">Errore nel caricamento della sezione: ${escapeHtml(error.message)}</div>`;
      }
      if (key === 'panoramica') bindActions();
    }

    tabsEl.querySelectorAll('.tab-btn').forEach((btn) => {
      btn.addEventListener('click', () => showTab(btn.dataset.tab));
    });

    showTab('panoramica');
  }

  function showFeedbackMessage(message, isError) {
    const el = container.querySelector('#match-action-feedback');
    if (!el) return;
    el.innerHTML = isError
      ? `<div class="error-box">${escapeHtml(message)}</div>`
      : `<div class="success-box">${escapeHtml(message)}</div>`;
  }

  async function reload({ invalidateHistory = false } = {}) {
    try {
      match = await apiGet(`/api/match/matches/${matchId}`);
    } catch (error) {
      showFeedbackMessage(`Impossibile aggiornare l'abbinamento: ${error.message || 'errore sconosciuto'}`, true);
      return;
    }
    if (invalidateHistory) lazyCache.history = null;
    render();
  }

  function bindActions() {
    const reviewBtn = container.querySelector('#match-mark-reviewed');
    if (reviewBtn) {
      reviewBtn.addEventListener('click', async () => {
        reviewBtn.disabled = true;
        reviewBtn.textContent = 'Salvataggio…';
        try {
          await apiPatch(`/api/match/matches/${matchId}`, { review_required: false });
          await reload();
          showFeedbackMessage('Abbinamento segnato come revisionato.', false);
        } catch (error) {
          reviewBtn.disabled = false;
          reviewBtn.textContent = 'Segna come revisionato';
          showFeedbackMessage(error.message || 'Errore nel salvataggio.', true);
        }
      });
    }

    const statusSelect = container.querySelector('#match-status-select');
    const statusSaveBtn = container.querySelector('#match-status-save');
    if (statusSelect && statusSaveBtn) {
      statusSaveBtn.addEventListener('click', async () => {
        statusSaveBtn.disabled = true;
        statusSaveBtn.textContent = 'Salvataggio…';
        try {
          await apiPatch(`/api/match/matches/${matchId}`, { commercial_status: statusSelect.value });
          await reload();
          showFeedbackMessage('Stato commerciale aggiornato.', false);
        } catch (error) {
          showFeedbackMessage(error.message || 'Errore nel salvataggio.', true);
        } finally {
          statusSaveBtn.disabled = false;
          statusSaveBtn.textContent = 'Salva stato';
        }
      });
    }

    const refreshBtn = container.querySelector('#match-refresh');
    if (refreshBtn) {
      refreshBtn.addEventListener('click', async () => {
        refreshBtn.disabled = true;
        refreshBtn.textContent = 'Ricalcolo…';
        try {
          const result = await apiPost(`/api/match/matches/${matchId}/refresh`, { trigger_reason: 'refresh manuale da STIMA360 OS' });
          await reload({ invalidateHistory: true });
          showFeedbackMessage(`Abbinamento ricalcolato. Punteggio: ${result.score_total} (Δ ${result.score_delta ?? '—'}).`, false);
        } catch (error) {
          refreshBtn.disabled = false;
          refreshBtn.textContent = 'Ricalcola abbinamento';
          showFeedbackMessage(error.message || 'Errore nel ricalcolo. La scheda resta consultabile.', true);
        }
      });
    }

    const scheduleVisitBtn = container.querySelector('#match-schedule-visit');
    if (scheduleVisitBtn) {
      scheduleVisitBtn.addEventListener('click', () => { openVisitScheduleDialog(); });
    }
  }

  // Patch MATCH -> VISITA: dialog "Programma visita". Unico dato realmente
  // richiesto all'operatore e' scheduled_at (obbligatorio lato MatchDecision,
  // buy/schemas.py: "scheduled_at is required when scheduling a visit"); le
  // note sono opzionali e vengono incluse nel payload solo se valorizzate.
  // Nessun campo property_id/match_id/buy_request_id/contact_id/lead_id e'
  // chiesto qui: schedule_match_visit li deriva da match_id (property_id) e
  // da buy_requests.contact_id/lead_id (buy/repository.py:152-176).
  function openVisitScheduleDialog() {
    const dialogEl = container.querySelector('#visit-schedule-dialog');
    if (!dialogEl) return;

    dialogEl.innerHTML = `
      <form id="visit-schedule-form">
        <h3 class="section-title">Programma visita</h3>
        <div class="form-field"><label>Data e ora *</label><input type="datetime-local" id="visit-schedule-at" class="input" required></div>
        <div class="form-field"><label>Note</label><textarea id="visit-schedule-notes" class="input"></textarea></div>
        <div id="visit-schedule-error" class="field-error"></div>
        <div class="modal-actions">
          <button type="button" id="visit-schedule-cancel" class="btn ghost">Annulla</button>
          <button type="submit" id="visit-schedule-submit" class="btn primary">Programma</button>
        </div>
      </form>
    `;

    dialogEl.querySelector('#visit-schedule-cancel').addEventListener('click', () => dialogEl.close());

    let submitting = false;
    dialogEl.querySelector('#visit-schedule-form').addEventListener('submit', async (event) => {
      event.preventDefault();
      if (submitting) return;
      const errorEl = dialogEl.querySelector('#visit-schedule-error');
      if (errorEl) errorEl.textContent = '';

      const raw = String(dialogEl.querySelector('#visit-schedule-at').value || '').trim();
      if (!raw) {
        if (errorEl) errorEl.textContent = 'Data e ora visita obbligatorie.';
        return;
      }
      const scheduledAt = new Date(raw);
      if (Number.isNaN(scheduledAt.getTime())) {
        if (errorEl) errorEl.textContent = 'Data e ora visita non valide.';
        return;
      }
      const payload = { action: 'visit_scheduled', scheduled_at: scheduledAt.toISOString() };
      const notes = dialogEl.querySelector('#visit-schedule-notes').value.trim();
      if (notes) payload.notes = notes;

      submitting = true;
      const submitBtn = dialogEl.querySelector('#visit-schedule-submit');
      const cancelBtn = dialogEl.querySelector('#visit-schedule-cancel');
      submitBtn.disabled = true;
      cancelBtn.disabled = true;
      submitBtn.textContent = 'Salvataggio…';
      try {
        await apiPost(`/api/buy/requests/${match.buy_request_id}/matches/${matchId}/decision`, payload);
        dialogEl.close();
        await reload({ invalidateHistory: true });
        showFeedbackMessage('Visita programmata sul match.', false);
      } catch (error) {
        submitting = false;
        submitBtn.disabled = false;
        cancelBtn.disabled = false;
        submitBtn.textContent = 'Programma';
        if (errorEl) errorEl.textContent = error.message || 'Errore nella programmazione della visita.';
      }
    });

    dialogEl.showModal();
  }
}

function renderPairHeading(m) {
  const buyerLabel = escapeHtml(m.buyer_name || `Richiesta #${m.buy_request_id}`);
  const propertyLabel = escapeHtml(m.property_title || m.property_code || `Immobile #${m.property_id}`);
  return `<a href="#/acquirenti/${escapeHtml(m.buy_request_id)}" id="match-buy-link">${buyerLabel}</a> <span class="muted">↔</span> <a href="#/immobili/${escapeHtml(m.property_id)}" id="match-property-link">${propertyLabel}</a>`;
}

function renderBadgeRow(m) {
  const parts = [
    renderBadge(MATCH_CLASS_LABELS[m.match_class] || m.match_class || '—', matchClassTone(m.match_class)),
    renderBadge(COMMERCIAL_STATUS_LABELS[m.commercial_status] || m.commercial_status || '—', 'gray'),
    renderBadge(FRESHNESS_LABELS[m.freshness_status] || m.freshness_status || '—', freshnessTone(m.freshness_status)),
  ];
  if (m.review_required) parts.push(renderBadge('Da revisionare', 'warn'));
  if (m.priority) parts.push(renderBadge(PRIORITY_LABELS[m.priority] || m.priority, priorityTone(m.priority)));
  if (m.is_manual_override) parts.push(renderBadge('Override manuale', 'role'));
  return parts.join('');
}

// --- Panoramica: dati principali + azioni deliberate -----------------------

function renderPanoramica(m) {
  const scoreFields = [
    ['Score tecnico', m.score_total],
    ['Override manuale', m.is_manual_override ? m.manual_score : null],
    ['Score effettivo', m.effective_score ?? m.score_total],
  ];
  const breakdown = [
    ['Zona', m.score_location], ['Budget', m.score_budget], ['Tipologia', m.score_typology],
    ['Dimensioni', m.score_dimensions], ['Locali', m.score_rooms], ['Caratteristiche', m.score_features],
    ['Condizione', m.score_condition],
  ];
  const infoFields = [
    ['Priorità', PRIORITY_LABELS[m.priority] || m.priority],
    ['Assegnato a', m.assigned_to],
    ['Revisione necessaria', m.review_required ? 'Sì' : 'No'],
    ['Compatibilità', COMPATIBILITY_STATUS_LABELS[m.compatibility_status] || m.compatibility_status],
    ['Versione algoritmo', m.algorithm_version],
    ['Ultimo calcolo', formatDateTime(m.last_calculated_at)],
    ['Primo abbinamento', formatDateTime(m.first_matched_at)],
    ['Ultima revisione', formatDateTime(m.last_reviewed_at)],
  ];
  if (m.freshness_status === 'stale') {
    infoFields.push(['Motivo aggiornamento richiesto', m.stale_reason], ['Da quando', formatDateTime(m.stale_since)]);
  }

  const overrideNote = m.is_manual_override
    ? `<p class="muted">Motivo override: ${escapeHtml(m.manual_reason || '—')}. Modifica non disponibile in questa fase: usare la MATCH Admin legacy.</p>`
    : '';

  const failedNote = m.freshness_status === 'failed' && m.recalculation_error
    ? `<div class="error-box">Errore tecnico ultimo ricalcolo: ${escapeHtml(m.recalculation_error)}</div>`
    : '';

  const canRefresh = m.freshness_status !== 'excluded';

  return `
    ${failedNote}
    <h3 class="section-title">Punteggio</h3>
    <div class="detail-grid">
      ${scoreFields.map(([label, value]) => `<div class="detail-item"><label>${escapeHtml(label)}</label>${value === null || value === undefined ? '—' : escapeHtml(value)}</div>`).join('')}
    </div>
    <h3 class="section-title">Dettaglio per area</h3>
    <div class="stat-chip-row">
      ${breakdown.map(([label, value]) => `<div class="stat-chip"><span>${value === null || value === undefined ? '—' : escapeHtml(value)}</span><small>${escapeHtml(label)}</small></div>`).join('')}
    </div>
    ${overrideNote}
    <h3 class="section-title">Informazioni</h3>
    <div class="detail-grid">
      ${infoFields.map(([label, value]) => `<div class="detail-item"><label>${escapeHtml(label)}</label>${value === null || value === undefined || value === '' ? '—' : escapeHtml(value)}</div>`).join('')}
    </div>
    <h3 class="section-title">Azioni</h3>
    <div class="action-bar">
      <select id="match-status-select" class="input">
        ${COMMERCIAL_STATUSES.map((s) => `<option value="${s}" ${s === m.commercial_status ? 'selected' : ''}>${escapeHtml(COMMERCIAL_STATUS_LABELS[s])}</option>`).join('')}
      </select>
      <button type="button" id="match-status-save" class="btn">Salva stato</button>
      ${m.review_required ? '<button type="button" id="match-mark-reviewed" class="btn">Segna come revisionato</button>' : ''}
      <button type="button" id="match-schedule-visit" class="btn">Programma visita</button>
      <button type="button" id="match-refresh" class="btn primary" ${canRefresh ? '' : 'disabled'}>Ricalcola abbinamento</button>
      ${canRefresh ? '' : '<span class="muted">Un abbinamento escluso non può essere ricalcolato.</span>'}
    </div>
  `;
}

// --- Analisi: spiegazione del match + criteri dettagliati -------------------

function renderAnalisi(m) {
  const list = (items) => {
    const arr = Array.isArray(items) ? items : [];
    if (!arr.length) return '<p class="muted">Nessun elemento.</p>';
    return `<ul>${arr.map((item) => `<li>${escapeHtml(formatListItem(item))}</li>`).join('')}</ul>`;
  };

  const criteriaTable = renderTable(
    [
      { label: 'Gruppo', render: (c) => escapeHtml(c.criterion_group || '—') },
      { label: 'Criterio', render: (c) => escapeHtml(c.criterion_code || '—') },
      { label: 'Tipo', render: (c) => escapeHtml(CRITERION_TYPE_LABELS[c.criterion_type] || c.criterion_type || '—') },
      { label: 'Richiesto', render: (c) => escapeHtml(formatCriterionValue(c.requested_value)) },
      { label: 'Immobile', render: (c) => escapeHtml(formatCriterionValue(c.property_value)) },
      { label: 'Risultato', render: (c) => renderBadge(RESULT_LABELS[c.result] || c.result || '—', resultTone(c.result)) },
      { label: 'Punteggio', render: (c) => escapeHtml(c.score ?? '—') },
      { label: 'Bloccante', render: (c) => c.is_blocking ? renderBadge('Sì', 'danger') : '' },
      { label: 'Spiegazione', render: (c) => escapeHtml(c.explanation || '—') },
    ],
    m.criteria,
    { emptyMessage: 'Nessun dettaglio criteri disponibile per questo calcolo.' },
  );

  return `
    <h3 class="section-title">Punti forti</h3>
    ${list(m.strengths)}
    <h3 class="section-title">Avvisi</h3>
    ${list(m.warnings)}
    <h3 class="section-title">Motivi bloccanti</h3>
    ${list(m.blocking_reasons)}
    <h3 class="section-title">Criteri (dettaglio calcolo)</h3>
    ${criteriaTable}
  `;
}

function formatListItem(item) {
  if (item === null || item === undefined) return '—';
  if (typeof item === 'string' || typeof item === 'number' || typeof item === 'boolean') return String(item);
  if (typeof item === 'object') {
    if (item.label || item.message || item.text) return String(item.label || item.message || item.text);
    return Object.entries(item).map(([k, v]) => `${k}: ${formatScalar(v)}`).join(', ');
  }
  return String(item);
}

function formatCriterionValue(value) {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (Array.isArray(value)) return value.length ? value.map(formatScalar).join(', ') : '—';
  if (typeof value === 'object') {
    // Un criterio del gruppo "features" porta come requested_value l'intera
    // riga buy_request_features (id, created_at, buy_request_id, feature_code,
    // value_type, weight_override, ecc.): riconoscibile dalla presenza di
    // value_type (marcatore strutturale univoco di quella tabella). In quel
    // caso NON si fa il dump generico Object.entries: si riusa la stessa
    // logica gia' collaudata in acquirente-dettaglio.js (formatFeatureValue),
    // mostrando solo il valore umano effettivo in base al value_type.
    if ('value_type' in value) return formatFeatureValue(value);
    const entries = Object.entries(value);
    return entries.length ? entries.map(([k, v]) => `${k}: ${formatScalar(v)}`).join(', ') : '—';
  }
  return String(value);
}

// Stessa logica di acquirente-dettaglio.js:formatFeatureValue (riusata, non
// reinventata): boolean -> Si/No, text -> value_text, number/range -> min-max.
function formatFeatureValue(f) {
  if (f.value_type === 'boolean') return f.value_boolean === true ? 'Sì' : f.value_boolean === false ? 'No' : '—';
  if (f.value_type === 'text') return f.value_text || '—';
  const min = f.value_min ?? '—'; const max = f.value_max ?? '—';
  return `${min} – ${max}`;
}

function formatScalar(v) {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'object') return Array.isArray(v) ? v.map(formatScalar).join('/') : '(dettaglio)';
  return String(v);
}

// --- Storico ricalcoli (lazy, GET .../refresh-history) ---------------------

async function renderStoricoLazy(matchId, cache) {
  if (cache.history === null) {
    try {
      const data = await apiGet(`/api/match/matches/${matchId}/refresh-history`);
      cache.history = Array.isArray(data?.items) ? data.items : [];
    } catch (error) {
      cache.history = { error: error.message || 'errore sconosciuto' };
    }
  }
  if (cache.history && cache.history.error) {
    return `<div class="error-box">Storico ricalcoli temporaneamente non disponibile: ${escapeHtml(cache.history.error)}</div>`;
  }
  return renderTable(
    [
      { label: 'Quando', render: (h) => escapeHtml(formatDateTime(h.created_at)) },
      { label: 'Origine', render: (h) => escapeHtml(TRIGGER_SOURCE_LABELS[h.trigger_source] || h.trigger_source || '—') },
      { label: 'Motivo', render: (h) => escapeHtml(h.trigger_reason || '—') },
      { label: 'Punteggio', render: (h) => `${escapeHtml(h.previous_score ?? '—')} → ${escapeHtml(h.new_score ?? '—')}` },
      { label: 'Classe', render: (h) => `${escapeHtml(MATCH_CLASS_LABELS[h.previous_class] || h.previous_class || '—')} → ${escapeHtml(MATCH_CLASS_LABELS[h.new_class] || h.new_class || '—')}` },
      { label: 'Compatibilità', render: (h) => `${escapeHtml(COMPATIBILITY_STATUS_LABELS[h.previous_compatibility_status] || h.previous_compatibility_status || '—')} → ${escapeHtml(COMPATIBILITY_STATUS_LABELS[h.new_compatibility_status] || h.new_compatibility_status || '—')}` },
      { label: 'Altri campi cambiati', render: (h) => formatChangedFieldsExtra(h.changed_fields) },
    ],
    cache.history,
    { emptyMessage: 'Nessun ricalcolo registrato per questo abbinamento.' },
  );
}

// changed_fields (match/refresh.py:changed_fields) e' un oggetto piatto
// {campo: {before, after}}; score_total/match_class/compatibility_status sono
// gia' mostrati nelle colonne dedicate qui sopra, quindi qui si mostra solo
// l'eventuale delta su hard_fail_count/warning_count per evitare di duplicare
// le stesse informazioni due volte nella stessa riga.
function formatChangedFieldsExtra(changedFields) {
  const labels = { hard_fail_count: 'Blocchi', warning_count: 'Avvisi' };
  const obj = changedFields && typeof changedFields === 'object' ? changedFields : {};
  const keys = Object.keys(obj).filter((k) => labels[k]);
  if (!keys.length) return '—';
  return escapeHtml(keys.map((k) => `${labels[k]}: ${obj[k].before ?? '—'} → ${obj[k].after ?? '—'}`).join(' · '));
}

// --- Feedback (lazy, GET/POST .../feedback) ---------------------------------

async function renderFeedbackLazy(matchId, cache) {
  if (cache.feedback === null) {
    try {
      const data = await apiGet(`/api/match/matches/${matchId}/feedback`);
      cache.feedback = Array.isArray(data?.items) ? data.items : [];
    } catch (error) {
      cache.feedback = { error: error.message || 'errore sconosciuto' };
    }
  }

  const listHtml = (cache.feedback && cache.feedback.error)
    ? `<div class="error-box">Feedback temporaneamente non disponibile: ${escapeHtml(cache.feedback.error)}</div>`
    : renderTable(
      [
        { label: 'Tipo', render: (f) => renderBadge(FEEDBACK_TYPE_LABELS[f.feedback_type] || f.feedback_type || '—', feedbackTone(f.feedback_type)) },
        { label: 'Fonte', render: (f) => escapeHtml(FEEDBACK_SOURCE_LABELS[f.source] || f.source || '—') },
        { label: 'Motivo', render: (f) => escapeHtml(FEEDBACK_REASON_LABELS[f.reason_code] || f.reason_code || '—') },
        { label: 'Note', render: (f) => escapeHtml(f.notes || '—') },
        { label: 'Quando', render: (f) => escapeHtml(formatDateTime(f.created_at)) },
      ],
      cache.feedback,
      { emptyMessage: 'Nessun feedback registrato per questo abbinamento.' },
    );

  return `
    ${listHtml}
    <h3 class="section-title">Aggiungi feedback</h3>
    <form id="feedback-form">
      <div class="form-grid-2">
        <div class="form-field">
          <label>Fonte</label>
          <select id="feedback-source" class="input">
            ${Object.keys(FEEDBACK_SOURCE_LABELS).map((s) => `<option value="${s}">${escapeHtml(FEEDBACK_SOURCE_LABELS[s])}</option>`).join('')}
          </select>
        </div>
        <div class="form-field">
          <label>Tipo</label>
          <select id="feedback-type" class="input">
            ${Object.keys(FEEDBACK_TYPE_LABELS).map((t) => `<option value="${t}">${escapeHtml(FEEDBACK_TYPE_LABELS[t])}</option>`).join('')}
          </select>
        </div>
      </div>
      <div class="form-field" id="feedback-reason-field" hidden>
        <label>Motivo (obbligatorio per feedback negativo)</label>
        <select id="feedback-reason" class="input">
          <option value="">Seleziona un motivo…</option>
          ${Object.keys(FEEDBACK_REASON_LABELS).map((r) => `<option value="${r}">${escapeHtml(FEEDBACK_REASON_LABELS[r])}</option>`).join('')}
        </select>
      </div>
      <div class="form-field">
        <label>Note</label>
        <textarea id="feedback-notes" class="input" rows="3"></textarea>
      </div>
      <div id="feedback-error" class="field-error"></div>
      <div class="action-bar">
        <button type="submit" id="feedback-submit" class="btn primary">Aggiungi feedback</button>
      </div>
    </form>
  `;
}

function bindFeedbackForm(contentEl, matchId, onSuccess) {
  const form = contentEl.querySelector('#feedback-form');
  if (!form) return;
  const typeSelect = contentEl.querySelector('#feedback-type');
  const reasonField = contentEl.querySelector('#feedback-reason-field');
  const reasonSelect = contentEl.querySelector('#feedback-reason');
  const errorEl = contentEl.querySelector('#feedback-error');
  const submitBtn = contentEl.querySelector('#feedback-submit');

  function updateReasonVisibility() {
    reasonField.hidden = typeSelect.value !== 'negative';
  }
  typeSelect.addEventListener('change', updateReasonVisibility);
  updateReasonVisibility();

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    errorEl.textContent = '';
    const feedbackType = typeSelect.value;
    const reasonCode = reasonSelect.value;
    if (feedbackType === 'negative' && !reasonCode) {
      errorEl.textContent = 'Il motivo è obbligatorio per un feedback negativo.';
      return;
    }
    const payload = {
      source: contentEl.querySelector('#feedback-source').value,
      feedback_type: feedbackType,
    };
    if (reasonCode) payload.reason_code = reasonCode;
    const notes = contentEl.querySelector('#feedback-notes').value.trim();
    if (notes) payload.notes = notes;

    submitBtn.disabled = true;
    submitBtn.textContent = 'Salvataggio…';
    try {
      await apiPost(`/api/match/matches/${matchId}/feedback`, payload);
      if (onSuccess) onSuccess();
    } catch (error) {
      errorEl.textContent = error.message || 'Errore nel salvataggio del feedback.';
      submitBtn.disabled = false;
      submitBtn.textContent = 'Aggiungi feedback';
    }
  });
}

// --- utility -----------------------------------------------------------

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

function priorityTone(priority) {
  if (priority === 'urgent') return 'danger';
  if (priority === 'high') return 'warn';
  return 'gray';
}

function resultTone(result) {
  if (result === 'matched') return 'ok';
  if (result === 'partially_matched') return 'warn';
  if (result === 'not_matched') return 'danger';
  return 'gray';
}

function feedbackTone(type) {
  if (type === 'positive') return 'ok';
  if (type === 'negative') return 'danger';
  return 'gray';
}
