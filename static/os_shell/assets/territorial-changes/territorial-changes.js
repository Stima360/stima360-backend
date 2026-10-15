// STIMA360 OS — territorial-changes/territorial-changes.js
// AGENZIE-TERRITORI C2: le proposte di modifica TERRITORIALI. L'UNICO file della
// Shell che nomina queste rotte.
//
//   Supervisore dell'agenzia competente (property/router.py):
//     GET  /api/property/properties/{id}/territorial-change-requests   proposte, campi proponibili, can_propose
//     POST /api/property/properties/{id}/territorial-change-requests   «Invia proposta»
//   Super Admin (platform_admin/router.py):
//     GET  /api/platform/territorial-change-requests[?status=]        elenco
//     GET  /api/platform/territorial-change-requests/{id}             dettaglio Prima / Attuale / Proposto, blocchi
//     POST /api/platform/territorial-change-requests/{id}/approve     «Approva» (dopo la conferma)
//     POST /api/platform/territorial-change-requests/{id}/reject      «Rifiuta» (motivo obbligatorio)
//
// La UI non decide nulla: chi puo' proporre, quali campi, se si puo' approvare e
// perche' no arrivano dal backend. Qui solo rete, testi e HTML (sempre con escapeHtml).
import { apiGet, apiPost } from '../core/api-client.js';
import { escapeHtml, formatDateTime, renderBadge } from '../components/st-table.js';

const PROPERTY = '/api/property/properties';
const PLATFORM = '/api/platform/territorial-change-requests';

// --- rete ---------------------------------------------------------------------

export function getPropertyRequests(propertyId) {
  return apiGet(`${PROPERTY}/${Number(propertyId)}/territorial-change-requests`);
}

export function proposeChange(propertyId, changes, note) {
  return apiPost(`${PROPERTY}/${Number(propertyId)}/territorial-change-requests`,
    { changes, note: note && String(note).trim() ? String(note).trim() : null });
}

export function listRequests(status = 'pending') {
  return apiGet(`${PLATFORM}?status=${encodeURIComponent(status)}&limit=100`);
}

export function getRequest(id) {
  return apiGet(`${PLATFORM}/${Number(id)}`);
}

export function approveRequest(id, note) {
  return apiPost(`${PLATFORM}/${Number(id)}/approve`, { note: note && String(note).trim() ? String(note).trim() : null });
}

export function rejectRequest(id, note) {
  return apiPost(`${PLATFORM}/${Number(id)}/reject`, { note: String(note || '') });
}

// --- testi (funzioni pure) ---------------------------------------------------------

export const STATUS_LABELS = Object.freeze({ pending: 'In attesa di approvazione', approved: 'Approvata', rejected: 'Rifiutata' });
export const STATUS_TONES = Object.freeze({ pending: 'warn', approved: 'ok', rejected: 'danger' });

/** Il campo numerico/booleano/testo come arriva dal modulo: tipi del backend. */
export const FIELD_TYPES = Object.freeze({
  title: 'text', rooms: 'int', bedrooms: 'int', bathrooms: 'int', floor: 'text', total_floors: 'int',
  elevator: 'bool', year_built: 'int', condition: 'text', energy_class: 'text', public_notes: 'textarea',
});

/** Un valore leggibile («—» se vuoto, «Sì/No» per i booleani). */
export function valueText(v) {
  if (v === null || v === undefined || v === '') return '—';
  if (v === true || v === 'true') return 'Sì';
  if (v === false || v === 'false') return 'No';
  return String(v);
}

/**
 * Dai valori del modulo alle sole MODIFICHE: campi della whitelist del backend,
 * diversi dal valore attuale, convertiti nel tipo giusto. Un campo svuotato
 * vale null. (Esportata per le prove.)
 */
export function changesFrom(form, current, allowed) {
  const out = {};
  for (const { field } of allowed || []) {
    if (!(field in form)) continue;
    const tipo = FIELD_TYPES[field] || 'text';
    const grezzo = form[field];
    let v;
    if (tipo === 'bool') v = grezzo === '' ? null : grezzo === 'true';
    else if (tipo === 'int') v = String(grezzo).trim() === '' ? null : Number.parseInt(grezzo, 10);
    else v = String(grezzo ?? '').trim() === '' ? null : String(grezzo).trim();
    if (tipo === 'int' && v !== null && !Number.isFinite(v)) continue;
    const prima = current ? current[field] : undefined;
    const uguale = (prima === undefined || prima === null || prima === '') ? v === null
      : String(prima) === String(v);
    if (!uguale) out[field] = v;
  }
  return out;
}

/** Una riga «campo: prima → dopo». */
export function fieldsListHtml(fields, { withCurrent = false } = {}) {
  const voci = Array.isArray(fields) ? fields : [];
  if (!voci.length) return '';
  return `<table class="data-table tc-fields" data-tc-fields>
      <thead><tr><th>Campo</th><th>Prima</th>${withCurrent ? '<th>Attuale</th>' : ''}<th>Proposto</th></tr></thead>
      <tbody>${voci.map((f) => `<tr data-tc-field="${escapeHtml(f.field)}">
        <td>${escapeHtml(f.label || f.field)}</td><td>${escapeHtml(valueText(f.old))}</td>
        ${withCurrent ? `<td>${escapeHtml(valueText(f.current))}</td>` : ''}<td><strong>${escapeHtml(valueText(f.new))}</strong></td></tr>`).join('')}
      </tbody></table>`;
}

/** Stato, chi e quando di una proposta. */
export function requestSummaryHtml(r) {
  const stato = renderBadge(STATUS_LABELS[r.status] || r.status, STATUS_TONES[r.status] || 'gray');
  const chi = `Proposta da ${escapeHtml(r.requested_by_name || 'Supervisore')} (${escapeHtml(r.competent_agency_name || '')}) il ${escapeHtml(formatDateTime(r.requested_at))}`;
  const esito = r.status === 'approved'
    ? `<p>Approvata da ${escapeHtml(r.decided_by_name || 'Super Admin')} il ${escapeHtml(formatDateTime(r.decided_at))}: modifiche applicate.</p>`
    : r.status === 'rejected'
      ? `<p>Rifiutata da ${escapeHtml(r.decided_by_name || 'Super Admin')} il ${escapeHtml(formatDateTime(r.decided_at))}${r.decision_note ? `: «${escapeHtml(r.decision_note)}»` : ''}</p>`
      : '<p class="muted">L’immobile non cambia finché il Super Admin non approva.</p>';
  return `<div class="tc-summary" data-tc-status="${escapeHtml(r.status)}">${stato} <span class="muted">${chi}</span>
      ${r.request_note ? `<p>Nota: ${escapeHtml(r.request_note)}</p>` : ''}${esito}</div>`;
}

export function errorText(error) {
  if (!error) return 'Operazione non riuscita.';
  if (error.status === 404) return 'Immobile o proposta non trovati.';
  if (error.status === 403) return error.message || 'Operazione non consentita.';
  return error.message || 'Operazione non riuscita.';
}
