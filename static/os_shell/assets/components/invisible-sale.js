import { apiGet, apiPost } from '../core/api-client.js';

const inFlight = new Map();

function actionKey(stimaId, decision, buyRequestId = '') {
  return `${stimaId}:${decision}:${buyRequestId}`;
}

function validId(value) {
  const id = Number(value);
  return Number.isSafeInteger(id) && id > 0 ? id : null;
}

export async function loadInvisibleSale(stimaId) {
  return apiGet(`/api/property-watch/stime/${stimaId}/invisible-sale`);
}

export async function refreshInvisibleSale(stimaId) {
  return apiPost(`/api/property-watch/stime/${stimaId}/invisible-sale/refresh`);
}

export async function reviewInvisibleSaleCandidate(stimaId, buyRequestId, decision) {
  if (!['approve', 'reject'].includes(decision)) throw new Error('Decisione non valida');
  return apiPost(`/api/property-watch/stime/${stimaId}/invisible-sale/candidates/${buyRequestId}/${decision}`);
}

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function formatMoney(value) {
  return value == null ? '—' : new Intl.NumberFormat('it-IT', {
    style: 'currency', currency: 'EUR', minimumFractionDigits: 2, maximumFractionDigits: 2,
  }).format(Number(value));
}

export function renderInvisibleSale(container, state, options = {}) {
  const { stimaId, onAction } = options;
  container.replaceChildren();
  const card = element('article', undefined, 'invisible-sale-card');
  card.append(element('strong', `Stima #${stimaId}`));
  if (!state || state.status === 'not_collected') {
    card.append(element('p', 'Potenziali acquirenti non ancora calcolati.', 'muted'));
  } else {
    card.append(element('p', `Stato: ${state.status} · ${state.current_candidate_count} candidati`, 'muted'));
    for (const candidate of Array.isArray(state.candidates) ? state.candidates : []) {
      const row = element('div', undefined, 'invisible-sale-candidate');
      row.append(element('strong', `BUY #${candidate.buy_request_id} · ${candidate.score_total}/100`));
      row.append(element('span', `${(candidate.reason_codes || []).join(', ') || '—'} · ${formatMoney(candidate.budget_reference)}`, 'muted'));
      // P25.7: link morto corretto - main.js registra la scheda Richiesta
      // BUY sotto la route 'acquirenti' (renderAcquirenteDettaglio), non
      // 'buy/richieste' (che non è mai stata una route registrata).
      const link = element('a', 'Apri richiesta BUY');
      link.href = `#/acquirenti/${candidate.buy_request_id}`;
      row.append(link);
      if (candidate.status !== 'stale' && state.status !== 'closed') {
        for (const [label, decision] of [['Approva', 'approve'], ['Rifiuta', 'reject']]) {
          const button = element('button', label, 'btn');
          button.type = 'button';
          button.addEventListener('click', () => onAction(decision, candidate.buy_request_id));
          row.append(button);
        }
      }
      card.append(row);
    }
  }
  const refresh = element('button', 'Aggiorna', 'btn invisible-sale-refresh');
  refresh.type = 'button';
  refresh.disabled = inFlight.has(actionKey(stimaId, 'refresh'));
  refresh.addEventListener('click', () => onAction('refresh'));
  card.append(refresh);
  container.append(card);
}

export function mountInvisibleSale(container, linkedStimaIds, mountToken) {
  const ids = [...new Set((Array.isArray(linkedStimaIds) ? linkedStimaIds : []).map(validId).filter(Boolean))].sort((a, b) => a - b);
  container.replaceChildren();
  const mounts = ids.map((stimaId) => {
    const mount = element('div', undefined, 'invisible-sale');
    container.append(mount);
    const refresh = async (decision, buyRequestId) => {
      const key = actionKey(stimaId, decision, buyRequestId || '');
      if (inFlight.has(key)) return;
      const request = (async () => {
        if (decision === 'refresh') await refreshInvisibleSale(stimaId);
        else await reviewInvisibleSaleCandidate(stimaId, buyRequestId, decision);
        const state = await loadInvisibleSale(stimaId);
        if (container.isConnected && container.dataset.invisibleSaleToken === mountToken) {
          renderInvisibleSale(mount, state, { stimaId, onAction: refresh });
        }
      })().catch(() => {
        if (container.isConnected && container.dataset.invisibleSaleToken === mountToken) {
          renderInvisibleSale(mount, null, { stimaId, onAction: refresh });
        }
      }).finally(() => inFlight.delete(key));
      inFlight.set(key, request);
      renderInvisibleSale(mount, null, { stimaId, onAction: refresh });
      return request;
    };
    return { stimaId, mount, refresh };
  });
  Promise.allSettled(mounts.map(({ stimaId }) => loadInvisibleSale(stimaId))).then((results) => {
    results.forEach((result, index) => {
      const { stimaId, mount, refresh } = mounts[index];
      if (!container.isConnected || container.dataset.invisibleSaleToken !== mountToken) return;
      renderInvisibleSale(
        mount,
        result.status === 'fulfilled' ? result.value : null,
        { stimaId, onAction: refresh },
      );
    });
  });
}
