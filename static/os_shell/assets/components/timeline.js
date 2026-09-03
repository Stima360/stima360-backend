// STIMA360 OS — timeline.js
// Read-only Seller Intelligence timeline for a single contact.

import { apiGet } from '../core/api-client.js';
import { escapeHtml, formatDateTime } from './st-table.js';

const TIMELINE_PAGE_SIZE = 200;
const MAX_TIMELINE_PAGES = 25;

const EVENT_TYPE_LABELS = {
  stima_richiesta: 'Stima richiesta',
  stima_completata: 'Stima completata',
  email_stima_inviata: 'Email stima inviata',
};

const EVENT_SOURCE_LABELS = {
  stima360_it: 'Stima360.it',
};

const PAYLOAD_FIELDS_BY_EVENT_TYPE = {
  stima_richiesta: [
    { key: 'comune', label: 'Comune' },
    { key: 'tipologia', label: 'Tipologia' },
    { key: 'mq', label: 'Superficie' },
  ],
  stima_completata: [
    { key: 'price_exact', label: 'Prezzo' },
    { key: 'eur_mq_finale', label: 'EUR/mq finale' },
    { key: 'base_mq', label: 'Base EUR/mq' },
  ],
  email_stima_inviata: [
    { key: 'pdf_url', label: 'PDF' },
  ],
};

export function loadSellerTimeline(contactId, cache, getTimeline = apiGet) {
  if (cache.timelineResult) return Promise.resolve(cache.timelineResult);
  if (cache.timelinePromise) return cache.timelinePromise;

  const loadPromise = fetchTimelinePages(contactId, getTimeline)
    .then((state) => {
      cache.timelineResult = state;
      return state;
    })
    .catch((error) => ({
      status: 'error',
      message: error && error.message ? error.message : 'Errore sconosciuto.',
    }))
    .finally(() => {
      cache.timelinePromise = null;
    });

  cache.timelinePromise = loadPromise;
  return cache.timelinePromise;
}

async function fetchTimelinePages(contactId, getTimeline) {
  const items = [];
  let truncated = false;

  for (let page = 0; page < MAX_TIMELINE_PAGES; page += 1) {
    const offset = page * TIMELINE_PAGE_SIZE;
    const response = await getTimeline(
      `/api/seller-intelligence/timeline?contact_id=${encodeURIComponent(contactId)}&limit=${TIMELINE_PAGE_SIZE}&offset=${offset}`,
    );
    const pageItems = Array.isArray(response && response.items) ? response.items : [];
    items.push(...pageItems);

    if (pageItems.length < TIMELINE_PAGE_SIZE) {
      return {
        status: 'ready',
        items: sortTimelineEvents(items),
        truncated,
      };
    }
    if (page === MAX_TIMELINE_PAGES - 1) {
      truncated = true;
    }
  }

  return {
    status: 'ready',
    items: sortTimelineEvents(items),
    truncated,
  };
}

function sortTimelineEvents(items) {
  return [...items].sort(compareTimelineEvents);
}

function compareTimelineEvents(left, right) {
  const leftOccurredAt = sortableOccurredAt(left && left.occurred_at);
  const rightOccurredAt = sortableOccurredAt(right && right.occurred_at);
  const occurredAtDifference = rightOccurredAt - leftOccurredAt;
  if (Number.isFinite(occurredAtDifference) && occurredAtDifference !== 0) {
    return occurredAtDifference;
  }

  const leftId = Number(left && left.id);
  const rightId = Number(right && right.id);
  const idDifference = rightId - leftId;
  if (Number.isFinite(idDifference) && idDifference !== 0) return idDifference;

  return String(right && right.id ? right.id : '').localeCompare(String(left && left.id ? left.id : ''));
}

function sortableOccurredAt(value) {
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? timestamp : Number.NEGATIVE_INFINITY;
}

function eventTypeLabel(eventType) {
  return EVENT_TYPE_LABELS[eventType] || eventType || '—';
}

function eventSourceLabel(eventSource) {
  return EVENT_SOURCE_LABELS[eventSource] || eventSource || '—';
}

function formatTimelineDate(value) {
  const formatted = formatDateTime(value);
  return formatted === '—' && value ? String(value) : formatted;
}

function formatPayloadEntries(eventType, payload) {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return [];

  const configuredFields = PAYLOAD_FIELDS_BY_EVENT_TYPE[eventType] || [];
  const handledKeys = new Set();
  const entries = [];
  for (const field of configuredFields) {
    if (Object.prototype.hasOwnProperty.call(payload, field.key)) {
      handledKeys.add(field.key);
      entries.push({ label: field.label, value: payload[field.key] });
    }
  }

  Object.keys(payload)
    .filter((key) => key !== 'idempotency_key' && !handledKeys.has(key))
    .sort()
    .forEach((key) => {
      entries.push({ label: key.replace(/[_-]+/g, ' '), value: payload[key] });
    });

  return entries;
}

function formatPayloadValue(value) {
  if (value === null || value === undefined) return '—';
  if (Array.isArray(value)) return `[${value.map((item) => formatPayloadValue(item)).join(', ')}]`;
  if (typeof value === 'object') {
    return `{ ${Object.keys(value).sort().map((key) => `${key}: ${formatPayloadValue(value[key])}`).join(', ')} }`;
  }
  return String(value);
}

function eventReferences(event) {
  return [
    { label: 'Contatto', value: event.contact_id },
    { label: 'Lead', value: event.lead_id },
    { label: 'Stima', value: event.stima_id },
    { label: 'Immobile', value: event.property_id },
  ].filter((reference) => reference.value !== null && reference.value !== undefined && reference.value !== '');
}

function renderTimelineEvent(event) {
  const safeEvent = event && typeof event === 'object' ? event : {};
  const payloadEntries = formatPayloadEntries(safeEvent.event_type, safeEvent.payload);
  const references = eventReferences(safeEvent);
  const payloadHtml = payloadEntries.length
    ? `<div class="detail-grid">${payloadEntries.map(({ label, value }) => `<div class="detail-item"><label>${escapeHtml(label)}</label>${escapeHtml(formatPayloadValue(value))}</div>`).join('')}</div>`
    : '';
  const referencesHtml = references.length
    ? `<div class="muted">Riferimenti: ${references.map((reference) => `${escapeHtml(reference.label)} #${escapeHtml(reference.value)}`).join(' · ')}</div>`
    : '';

  return `
    <article class="list-item">
      <strong>${escapeHtml(eventTypeLabel(safeEvent.event_type))}</strong>
      <div class="muted">${escapeHtml(eventSourceLabel(safeEvent.event_source))} · ${escapeHtml(formatTimelineDate(safeEvent.occurred_at))}</div>
      ${referencesHtml}
      ${payloadHtml}
    </article>
  `;
}

export function renderSellerTimeline(state = { status: 'loading' }) {
  if (!state || state.status === 'loading') {
    return '<p class="muted">Caricamento timeline…</p>';
  }
  if (state.status === 'error') {
    return `<div class="error-box">Errore nel caricamento della timeline: ${escapeHtml(state.message || 'Errore sconosciuto.')}</div>`;
  }

  const items = Array.isArray(state.items) ? sortTimelineEvents(state.items) : [];
  const truncationWarning = state.truncated
    ? '<div class="error-box">La timeline potrebbe essere incompleta: raggiunto il limite di 25 pagine.</div>'
    : '';
  if (!items.length) {
    return `${truncationWarning}<p class="muted">Nessun evento Seller Intelligence disponibile per questo contatto.</p>`;
  }
  return `${truncationWarning}<div class="list">${items.map((event) => renderTimelineEvent(event)).join('')}</div>`;
}
