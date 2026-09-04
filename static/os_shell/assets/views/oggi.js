// STIMA360 OS — oggi.js
// Pagina "Oggi": sola lettura, dati reali dalle API esistenti.
// Endpoint usati (verificati nei router prima di scrivere questo file):
//   GET /api/core/tasks?limit=200          (core/router.py)
//   GET /api/core/activities?limit=20      (core/router.py)
//   GET /api/property/visits?limit=20      (property/router.py)
//   GET /api/next-best-action?limit=20     (next_best_action/router.py, P23)
// Nessuna nuova API oltre a P23. Nessun dato inventato: se una chiamata
// fallisce, la relativa sezione mostra un avviso e le altre restano
// utilizzabili. La sezione "Priorità di oggi" (P23) è additiva: le liste
// esistenti sopra non vengono toccate né sostituite.

import { apiGet } from '../core/api-client.js';

export async function renderOggi(container) {
  container.innerHTML = '<p class="muted">Caricamento…</p>';

  const [tasksResult, activitiesResult, visitsResult, nbaResult] = await Promise.allSettled([
    apiGet('/api/core/tasks?limit=200'),
    apiGet('/api/core/activities?limit=20'),
    apiGet('/api/property/visits?limit=20'),
    apiGet('/api/next-best-action?limit=20'),
  ]);

  const failedSections = [];

  const tasks = extractItems(tasksResult, failedSections, 'task');
  const activities = extractItems(activitiesResult, failedSections, 'attività');
  const visits = extractItems(visitsResult, failedSections, 'visite');
  const nextBestActions = extractItems(nbaResult, failedSections, 'priorità di oggi (P23)');

  const now = new Date();
  const openTasks = tasks.filter((t) => !['completed', 'cancelled'].includes(t.status));
  const overdueTasks = openTasks
    .filter((t) => t.due_at && new Date(t.due_at) < now)
    .sort((a, b) => new Date(a.due_at) - new Date(b.due_at));
  const dueTodayTasks = openTasks
    .filter((t) => t.due_at && sameDay(new Date(t.due_at), now))
    .sort((a, b) => new Date(a.due_at) - new Date(b.due_at));
  const highPriorityTasks = openTasks.filter((t) => ['high', 'urgent'].includes(t.priority));

  const kpis = [
    ['Task da fare oggi', dueTodayTasks.length],
    ['Task scaduti', overdueTasks.length],
    ['Task prioritari', highPriorityTasks.length],
    ['Attività recenti', activities.length],
    ['Visite in programma', visits.length],
  ];

  const errorBanner = failedSections.length
    ? `<div class="error-box">Alcuni dati non sono disponibili al momento (${escapeHtml(failedSections.join(', '))}). Le altre sezioni di questa pagina restano utilizzabili.</div>`
    : '';

  const focusTasks = [...overdueTasks, ...dueTodayTasks].slice(0, 10);

  container.innerHTML = `
    ${errorBanner}
    <div class="kpi-grid">
      ${kpis.map(([label, value]) => `
        <div class="card kpi">
          <span class="kpi-label">${escapeHtml(label)}</span>
          <strong class="kpi-value">${value}</strong>
        </div>
      `).join('')}
    </div>
    <div class="panel-grid">
      <div class="card panel">
        <h2>Priorità di oggi</h2>
        ${renderNbaList(nextBestActions)}
      </div>
      <div class="card panel">
        <h2>Task scaduti e di oggi</h2>
        ${renderTaskList(focusTasks)}
      </div>
      <div class="card panel">
        <h2>Attività recenti</h2>
        ${renderActivityList(activities.slice(0, 10))}
      </div>
      <div class="card panel">
        <h2>Prossime visite</h2>
        ${renderVisitList(visits.slice(0, 10))}
      </div>
    </div>
  `;
}

function extractItems(settledResult, failedSections, label) {
  if (settledResult.status === 'fulfilled') {
    const value = settledResult.value;
    return Array.isArray(value?.items) ? value.items : [];
  }
  failedSections.push(label);
  return [];
}

function sameDay(a, b) {
  return a.getFullYear() === b.getFullYear()
    && a.getMonth() === b.getMonth()
    && a.getDate() === b.getDate();
}

function formatDateTime(value) {
  if (!value) return '—';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleString('it-IT', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
}

// P23 — Next Best Action: già ordinate dall'API (/api/next-best-action),
// non riordiniamo qui. Ogni item è già la singola azione vincente per il
// suo soggetto (calcolata da next_best_action/engine.py), questa funzione
// si limita a visualizzarla; non decide nulla.
const NBA_PRIORITY_LABELS = { urgent: 'Urgente', high: 'Alta', normal: 'Normale', low: 'Bassa' };

function renderNbaList(items) {
  if (!items.length) return '<p class="muted">Nessuna priorità da segnalare oggi.</p>';
  return `<div class="list">${items.map((n) => {
    const priorityLabel = NBA_PRIORITY_LABELS[n.priority] || escapeHtml(n.priority || '—');
    const subjectLabel = escapeHtml(nbaSubjectLabel(n));
    const cta = nbaCtaHref(n);
    return `
    <div class="list-item">
      <div>
        <strong>${subjectLabel}</strong>
        <div class="muted">${escapeHtml(n.reason || '')}</div>
      </div>
      <div class="muted">
        ${escapeHtml(priorityLabel)}
        ${cta ? ` · <a href="${cta}">Apri</a>` : ''}
      </div>
    </div>
  `;
  }).join('')}</div>`;
}

// P25.7 (Gap C fix): il backend ora fornisce subject_label (additivo,
// next_best_action/repository.py::_row - dynamic LEFT JOIN su contacts,
// nessuna migration), calcolato dal nome reale del contatto collegato
// quando disponibile. Se assente (contact_id nullo, nessun match, o
// contatto senza nome utilizzabile) resta il fallback preesistente
// "{Tipo} #{id}" — comportamento identico a prima di questo fix.
function nbaSubjectLabel(n) {
  if (n.subject_label) return n.subject_label;
  const labels = { lead: 'Venditore', buy_request: 'Acquirente', stima: 'Stima', match: 'Match' };
  const label = labels[n.subject_type] || n.subject_type || 'Soggetto';
  return `${label} #${n.subject_id}`;
}

function nbaCtaHref(n) {
  if (!n.cta_route || !Array.isArray(n.cta_params) || !n.cta_params.length) return null;
  const params = n.cta_params.filter((p) => p !== null && p !== undefined);
  if (!params.length) return null;
  return `#/${n.cta_route}/${params.map((p) => encodeURIComponent(p)).join('/')}`;
}

function renderTaskList(items) {
  if (!items.length) return '<p class="muted">Nessun task da segnalare.</p>';
  return `<div class="list">${items.map((t) => `
    <div class="list-item">
      <div>
        <strong>${escapeHtml(t.title || `Task #${t.id}`)}</strong>
        <div class="muted">${escapeHtml(t.description || '')}</div>
      </div>
      <div class="muted">${escapeHtml(formatDateTime(t.due_at))}</div>
    </div>
  `).join('')}</div>`;
}

function renderActivityList(items) {
  if (!items.length) return '<p class="muted">Nessuna attività recente.</p>';
  return `<div class="list">${items.map((a) => `
    <div class="list-item">
      <div>
        <strong>${escapeHtml(a.activity_type || 'Attività')}</strong>
        <div class="muted">${escapeHtml(a.description || '')}</div>
      </div>
      <div class="muted">${escapeHtml(formatDateTime(a.occurred_at))}</div>
    </div>
  `).join('')}</div>`;
}

function renderVisitList(items) {
  if (!items.length) return '<p class="muted">Nessuna visita in programma.</p>';
  return `<div class="list">${items.map((v) => `
    <div class="list-item">
      <div>
        <strong>Visita #${v.id}</strong>
        <div class="muted">${escapeHtml(v.status || '')}</div>
      </div>
      <div class="muted">${escapeHtml(formatDateTime(v.scheduled_at))}</div>
    </div>
  `).join('')}</div>`;
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}
