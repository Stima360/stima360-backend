import { apiGet, apiPost } from '../core/api-client.js';
import { escapeHtml, formatDateTime } from './st-table.js';

const PARTIAL_LEAD_WARNING = 'Non è stato possibile verificare tutte le stime collegate.';
const UNVERIFIABLE_LEAD_WARNING = 'Non è stato possibile verificare le stime collegate.';
const NO_LINKED_STIME = 'Nessuna stima collegata per il calcolo della domanda buyer.';
const METRIC_LABELS = [
  ['Buyer valutati', 'evaluated_buyers', formatCount],
  ['Buyer compatibili', 'compatible_buyers', formatCount],
  ['Buyer altamente compatibili', 'highly_compatible_buyers', formatCount],
  ['Buyer compatibili recenti (30 giorni)', 'recent_compatible_buyers_30d', formatCount],
  ['Score medio MATCH', 'average_match_score', formatScore],
  ['Score massimo MATCH', 'maximum_match_score', formatScore],
  ['Budget medio', 'average_budget', formatCurrency],
];

export function createBuyerPressureCache() {
  return {
    buyerPressureResult: null,
    buyerPressurePromise: null,
    watchResults: new Map(),
    watchPromises: new Map(),
    refreshPromises: new Map(),
    requestSequence: 0,
  };
}

function normalizeStimaId(value) {
  const number = typeof value === 'string' && /^\d+$/.test(value)
    ? Number(value)
    : value;
  return Number.isSafeInteger(number) && number > 0 ? number : null;
}

function knownStimaIds(leads) {
  const ids = new Set();
  const items = Array.isArray(leads) ? leads : [];
  for (const lead of items) {
    const estimations = Array.isArray(lead && lead.estimations) ? lead.estimations : [];
    for (const estimation of estimations) {
      const stimaId = normalizeStimaId(estimation && estimation.stima_id);
      if (stimaId !== null) ids.add(stimaId);
    }
  }
  return [...ids].sort((left, right) => left - right);
}

function failureCount(value) {
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : 0;
}

async function loadWatch(stimaId, cache, getWatch) {
  if (cache.watchResults.has(stimaId)) {
    return cache.watchResults.get(stimaId);
  }
  if (cache.watchPromises.has(stimaId)) {
    return cache.watchPromises.get(stimaId);
  }

  const request = getWatch(`/api/property-watch/stime/${stimaId}`)
    .then((state) => {
      const card = { status: 'ready', state };
      cache.watchResults.set(stimaId, card);
      return card;
    })
    .finally(() => {
      cache.watchPromises.delete(stimaId);
    });
  cache.watchPromises.set(stimaId, request);
  return request;
}

export async function loadBuyerPressure(
  leadsFromContact360,
  cache,
  loadLeadDetails,
  getWatch = apiGet,
) {
  if (cache.buyerPressureResult) return cache.buyerPressureResult;
  if (cache.buyerPressurePromise) return cache.buyerPressurePromise;

  const contactLeads = Array.isArray(leadsFromContact360) ? leadsFromContact360 : [];
  const request = (async () => {
    let details;
    try {
      details = await loadLeadDetails();
    } catch (_error) {
      details = { leads: [], failedCount: Math.max(contactLeads.length, 1) };
    }
    const resolvedLeads = Array.isArray(details && details.leads) ? details.leads : [];
    const stimaIds = knownStimaIds(resolvedLeads);
    const failedDetails = failureCount(details && details.failedCount);
    const leadWarning = failedDetails
      ? (stimaIds.length ? PARTIAL_LEAD_WARNING : UNVERIFIABLE_LEAD_WARNING)
      : null;
    const cards = new Map();

    if (stimaIds.length) {
      const results = await Promise.allSettled(
        stimaIds.map((stimaId) => loadWatch(stimaId, cache, getWatch)),
      );
      results.forEach((result, index) => {
        const stimaId = stimaIds[index];
        const card = result.status === 'fulfilled'
          ? result.value
          : { status: 'unavailable' };
        cards.set(stimaId, card);
        if (result.status === 'rejected') {
          cache.watchResults.set(stimaId, card);
        }
      });
    }

    const state = { leadWarning, stimaIds, cards };
    cache.buyerPressureResult = state;
    return state;
  })().finally(() => {
    cache.buyerPressurePromise = null;
  });
  cache.buyerPressurePromise = request;
  return request;
}

function formatCount(value) {
  const number = Number(value);
  return Number.isSafeInteger(number) && number >= 0
    ? new Intl.NumberFormat('it-IT', { maximumFractionDigits: 0 }).format(number)
    : '—';
}

function formatScore(value) {
  if (value === null || value === undefined) return '—';
  const number = Number(value);
  if (!Number.isFinite(number)) return '—';
  return `${new Intl.NumberFormat('it-IT', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(number)}/100`;
}

function formatCurrency(value) {
  if (value === null || value === undefined) return '—';
  const number = Number(value);
  if (!Number.isFinite(number)) return '—';
  return new Intl.NumberFormat('it-IT', {
    style: 'currency',
    currency: 'EUR',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(number);
}

function refreshButton(stimaId, disabled = false) {
  return `
    <button
      type="button"
      class="btn buyer-pressure-refresh"
      data-buyer-pressure-refresh="${escapeHtml(stimaId)}"
      ${disabled ? 'disabled' : ''}
    >Aggiorna domanda buyer</button>
  `;
}

function renderCardMessage(stimaId, message, { disabled = false } = {}) {
  return `
    <article class="buyer-pressure-card" data-buyer-pressure-card="${escapeHtml(stimaId)}">
      <strong>Stima #${escapeHtml(stimaId)}</strong>
      <p class="muted">${escapeHtml(message)}</p>
      ${refreshButton(stimaId, disabled)}
    </article>
  `;
}

function renderFactor(factor) {
  return `
    <li>
      <span>${escapeHtml(factor && factor.label ? factor.label : 'Fattore')}</span>
      <strong>${escapeHtml(factor && factor.points)}/${escapeHtml(factor && factor.max_points)}</strong>
    </li>
  `;
}

function renderMetric(label, value, formatter) {
  return `
    <div class="buyer-pressure-metric">
      <label>${escapeHtml(label)}</label>
      <span>${escapeHtml(formatter(value))}</span>
    </div>
  `;
}

function renderReadyCard(stimaId, state) {
  const metrics = state && state.buyer_pressure_metrics;
  const insight = state && state.buyer_pressure_insight;
  if (!metrics || !insight) {
    return renderCardMessage(stimaId, 'Domanda buyer non ancora calcolata.');
  }
  const factors = Array.isArray(insight.factors) ? insight.factors : [];

  return `
    <article class="buyer-pressure-card" data-buyer-pressure-card="${escapeHtml(stimaId)}">
      <strong>Stima #${escapeHtml(stimaId)}</strong>
      <div class="buyer-pressure-score-row">
        <strong class="buyer-pressure-headline">${escapeHtml(insight.headline)}</strong>
        <span>${escapeHtml(insight.score)}/100 · ${escapeHtml(insight.band_label)}</span>
      </div>
      <p>${escapeHtml(insight.message)}</p>
      <ul class="buyer-pressure-factors">
        ${factors.map((factor) => renderFactor(factor)).join('')}
      </ul>
      <div class="buyer-pressure-metrics">
        ${METRIC_LABELS.map(([label, key, formatter]) => renderMetric(
          label,
          metrics[key],
          formatter,
        )).join('')}
      </div>
      <p class="buyer-pressure-observed-at muted">Aggiornato: ${escapeHtml(formatDateTime(metrics.observed_at))}</p>
      <p class="buyer-pressure-disclaimer muted">${escapeHtml(insight.disclaimer)}</p>
      ${refreshButton(stimaId)}
    </article>
  `;
}

function renderBuyerPressureCard(stimaId, card) {
  if (!card || card.status === 'unavailable') {
    return renderCardMessage(
      stimaId,
      `Domanda buyer non disponibile per la stima #${stimaId}.`,
    );
  }
  if (card.status === 'refreshing') {
    return renderCardMessage(
      stimaId,
      'Calcolo domanda buyer in caricamento…',
      { disabled: true },
    );
  }
  if (card.status === 'baseline_unavailable') {
    return renderCardMessage(
      stimaId,
      'Dati della stima insufficienti per calcolare la domanda buyer.',
    );
  }
  if (card.status === 'failed') {
    return renderCardMessage(
      stimaId,
      'Impossibile aggiornare la domanda buyer. Riprova.',
    );
  }
  return renderReadyCard(stimaId, card.state);
}

export function renderBuyerPressureSection(state) {
  if (!state) {
    return '<p class="muted">Calcolo domanda buyer in caricamento…</p>';
  }
  if (!state.stimaIds.length) {
    return `<p class="muted">${escapeHtml(state.leadWarning || NO_LINKED_STIME)}</p>`;
  }
  const warning = state.leadWarning
    ? `<p class="buyer-pressure-warning">${escapeHtml(state.leadWarning)}</p>`
    : '';
  return `
    ${warning}
    <div class="buyer-pressure-grid">
      ${state.stimaIds.map((stimaId) => renderBuyerPressureCard(
        stimaId,
        state.cards.get(stimaId),
      )).join('')}
    </div>
  `;
}

export async function refreshBuyerPressure(stimaId, cache, post = apiPost, getWatch = apiGet) {
  if (cache.refreshPromises.has(stimaId)) {
    return cache.refreshPromises.get(stimaId);
  }

  const request = (async () => {
    let outcome;
    try {
      outcome = await post(
        `/api/property-watch/stime/${stimaId}/buyer-pressure/refresh`,
      );
    } catch (_error) {
      return { status: 'failed' };
    }
    if (!outcome || !["written", "unchanged", "superseded"].includes(outcome.status)) {
      return {
        status: outcome && outcome.status === 'baseline_unavailable'
          ? 'baseline_unavailable'
          : 'failed',
      };
    }

    cache.watchResults.delete(stimaId);
    cache.watchPromises.delete(stimaId);
    try {
      return await loadWatch(stimaId, cache, getWatch);
    } catch (_error) {
      const unavailable = { status: 'unavailable' };
      cache.watchResults.set(stimaId, unavailable);
      return unavailable;
    }
  })().finally(() => {
    cache.refreshPromises.delete(stimaId);
  });
  cache.refreshPromises.set(stimaId, request);
  return request;
}

function isCurrentMount(mount, requestId) {
  return mount.isConnected && mount.dataset.buyerPressureRequestId === requestId;
}

function replaceCard(mount, stimaId, card) {
  const currentCard = mount.querySelector(
    `[data-buyer-pressure-card="${stimaId}"]`,
  );
  if (!currentCard) return;
  currentCard.outerHTML = renderBuyerPressureCard(stimaId, card);
}

function bindRefreshButton(mount, button, cache, requestId) {
  if (button.dataset.buyerPressureBound) return;
  button.dataset.buyerPressureBound = 'true';
  button.addEventListener('click', async () => {
    const stimaId = normalizeStimaId(button.dataset.buyerPressureRefresh);
    if (stimaId === null || cache.refreshPromises.has(stimaId)) return;

    const result = cache.buyerPressureResult;
    if (!result || !result.cards.has(stimaId)) return;
    result.cards.set(stimaId, { status: 'refreshing' });
    replaceCard(mount, stimaId, result.cards.get(stimaId));
    bindRefreshButtons(mount, cache, requestId);

    const refreshed = await refreshBuyerPressure(stimaId, cache);
    if (!isCurrentMount(mount, requestId)) return;
    result.cards.set(stimaId, refreshed);
    replaceCard(mount, stimaId, refreshed);
    bindRefreshButtons(mount, cache, requestId);
  });
}

function bindRefreshButtons(mount, cache, requestId) {
  mount.querySelectorAll('[data-buyer-pressure-refresh]').forEach((button) => {
    bindRefreshButton(mount, button, cache, requestId);
  });
}

export async function hydrateBuyerPressure(
  mount,
  leadsFromContact360,
  cache,
  loadLeadDetails,
) {
  if (!mount) return;
  cache.requestSequence += 1;
  const requestId = `buyer-pressure-${cache.requestSequence}`;
  mount.dataset.buyerPressureRequestId = requestId;
  mount.innerHTML = '<p class="muted">Calcolo domanda buyer in caricamento…</p>';

  const state = await loadBuyerPressure(leadsFromContact360, cache, loadLeadDetails);
  if (!isCurrentMount(mount, requestId)) return;
  mount.innerHTML = renderBuyerPressureSection(state);
  bindRefreshButtons(mount, cache, requestId);
}
