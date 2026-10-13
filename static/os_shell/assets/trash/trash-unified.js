// STIMA360 OS — trash/trash-unified.js
// CESTINO-GENERALE-1 (CG-1): le funzioni PURE del Cestino unico (percorsi,
// testi, card, contatori, collegamenti, richieste edifici). Nessuna rete,
// nessun DOM: le prove le chiamano da node.
//
// Il Cestino unico e' in SOLA LETTURA in questa fase: le azioni (ripristino,
// richieste, decisioni del Super Admin) restano quelle gia' esistenti di ogni
// categoria, raggiunte dalle schede e dalle schede di categoria del Cestino.
import { escapeHtml, formatDateTime } from '../components/st-table.js';
import { reasonLabel, TRASH_REASONS } from './trash-model.js';

/** Le categorie del Cestino unico (stesso ordine del backend). */
export const TRASH_KINDS = Object.freeze([
  { value: 'property', label: 'Immobili', tab: 'immobili' },
  { value: 'contact', label: 'Contatti', tab: 'contatti' },
  { value: 'building', label: 'Edifici', tab: 'edifici' },
  { value: 'buy_request', label: 'Richieste acquirente', tab: 'richieste' },
]);

const PER_KIND = Object.fromEntries(TRASH_KINDS.map((k) => [k.value, k]));

/** Le categorie previste nelle fasi successive del Cestino generale (solo testo). */
export const UPCOMING_KINDS = Object.freeze([
  'Attività e note', 'Task', 'Lead', 'Appuntamenti', 'Acquisizioni', 'Vendite e proposte', 'Stime',
]);

export function kindLabel(kind) {
  return (PER_KIND[kind] && PER_KIND[kind].label) || kind || '—';
}

/**
 * Il percorso dell'elenco (o dei contatori, `suffix='/counts'`). Solo i
 * filtri valorizzati; l'agenzia (`agency`) SOLO nella vista globale (Super
 * Admin): sulla superficie tenant l'agenzia e' quella della sessione.
 */
export function unifiedTrashPath(suffix, filters = {}, { scope = 'agency', offset, limit } = {}) {
  const base = scope === 'platform' ? '/api/platform/trash' : '/api/trash';
  const params = [];
  const metti = (k, v) => { if (v !== undefined && v !== null && String(v).trim() !== '') params.push(`${k}=${encodeURIComponent(String(v).trim())}`); };
  metti('type', filters.type && filters.type !== 'all' ? filters.type : '');
  metti('q', filters.q);
  metti('reason', filters.reason);
  metti('deleted_by', filters.deleted_by);
  metti('date_from', filters.date_from);
  metti('date_to', filters.date_to);
  if (scope === 'platform') metti('agency', filters.agency);
  if (limit !== undefined) metti('limit', limit);
  if (offset !== undefined) metti('offset', offset);
  return `${base}${suffix || ''}${params.length ? `?${params.join('&')}` : ''}`;
}

/** I filtri (motivi) per il menu. */
export function reasonOptionsHtml(scelto = '') {
  return `<option value="">Tutti i motivi</option>${TRASH_REASONS.map((r) =>
    `<option value="${escapeHtml(r.value)}"${r.value === scelto ? ' selected' : ''}>${escapeHtml(r.label)}</option>`).join('')}`;
}

/** I contatori: «Tutti» + una pastiglia per categoria; quella scelta e' attiva. */
export function countsHtml(counts, scelta = 'all') {
  if (!counts) return '';
  const categorie = Array.isArray(counts.categories) ? counts.categories : [];
  const pillola = (value, label, n, spenta) => `<button type="button" class="chip trash-chip${scelta === value ? ' active' : ''}"
      data-trash-count="${escapeHtml(value)}"${spenta ? ' disabled title="Non ancora disponibile su questo database"' : ''}>
      ${escapeHtml(label)} <span class="trash-chip-n" data-trash-count-n>${Number(n) || 0}</span></button>`;
  return `<div class="trash-counts" data-trash-counts>
      ${pillola('all', 'Tutti', counts.total)}
      ${categorie.map((c) => pillola(c.kind, c.label || kindLabel(c.kind), c.count, c.installed === false)).join('')}
    </div>`;
}

/** Una voce dell'elenco unificato (sola lettura). */
export function unifiedCardHtml(item, { scope = 'agency' } = {}) {
  const id = Number(item.id);
  const kind = String(item.kind || '');
  const chi = item.deleted_by_name ? ` · da ${item.deleted_by_name}` : '';
  const motivo = reasonLabel(item.deleted_reason) + (item.deleted_note ? ` — ${item.deleted_note}` : '');
  const extra = item.extra || {};
  const dettagli = [];
  if (kind === 'building' && Number(extra.units_total)) dettagli.push(`${Number(extra.units_total)} unità con l’edificio`);
  if (kind === 'buy_request' && extra.contact_in_trash) dettagli.push('anche il contatto è nel Cestino');
  if (extra.archived) dettagli.push('era archiviato');
  const tab = PER_KIND[kind] ? PER_KIND[kind].tab : null;
  const azioni = [];
  // le schede si aprono solo nella propria agenzia: la vista globale (Super Admin senza agenzia) non ha «Apri scheda»
  if (item.href && scope === 'agency') azioni.push(`<a class="btn ghost" href="${escapeHtml(item.href)}" data-trash-open>Apri scheda</a>`);
  if (scope === 'agency' && tab) azioni.push(`<a class="btn ghost" href="#/cestino/${tab}" data-trash-manage>Gestisci</a>`);
  azioni.push(`<button type="button" class="btn ghost" data-trash-relations="${escapeHtml(kind)}:${id}">Collegamenti</button>`);
  return `
    <article class="trash-card" data-unified-item="${escapeHtml(kind)}:${id}">
      <div class="trash-card-head">
        <strong class="trash-code">${escapeHtml(item.label || `#${id}`)}</strong>
        <span class="badge trash-kind" data-trash-kind="${escapeHtml(kind)}">${escapeHtml(item.kind_label || kindLabel(kind))}</span>
      </div>
      ${item.line ? `<div class="trash-line">${escapeHtml(item.line)}</div>` : ''}
      <dl class="trash-facts">
        ${scope === 'platform' ? `<dt>Agenzia</dt><dd data-trash-agency>${escapeHtml(item.agency_name || '')}</dd>` : ''}
        <dt>Eliminato</dt><dd data-trash-when>${escapeHtml(formatDateTime(item.deleted_at))}${escapeHtml(chi)}</dd>
        <dt>Motivo</dt><dd data-trash-reason>${escapeHtml(motivo)}</dd>
        ${dettagli.length ? `<dt>Note</dt><dd data-trash-details>${escapeHtml(dettagli.join(' · '))}</dd>` : ''}
      </dl>
      <div class="trash-relations" data-trash-relations-host></div>
      <div class="trash-card-actions">${azioni.join('')}</div>
    </article>`;
}

/** L'anteprima dei collegamenti (conteggi letti dal backend). */
export function relationsHtml(rel) {
  const voci = rel && Array.isArray(rel.relations) ? rel.relations : [];
  if (!voci.length) return '<p class="muted" data-trash-relations-empty>Nessun collegamento.</p>';
  return `<ul class="trash-relations-list" data-trash-relations-list>${voci.map((v) =>
    `<li data-relation-table="${escapeHtml(v.table)}">${escapeHtml(v.label || v.table)} <span class="muted">(${Number(v.count)})</span></li>`).join('')}</ul>`;
}

const STATI_RICHIESTA = { pending: 'In attesa', approved: 'Approvata', rejected: 'Rifiutata', withdrawn: 'Ritirata' };

/** Una richiesta di eliminazione edificio vista dall'agenzia (sola lettura). */
export function buildingRequestRowHtml(r) {
  const id = Number(r.id);
  const stato = STATI_RICHIESTA[r.status] || r.status || '—';
  const chi = r.requested_by_name ? ` da ${r.requested_by_name}` : '';
  const decisione = r.status !== 'pending' && r.decided_at
    ? `${stato}${r.decided_by_name ? ` da ${r.decided_by_name}` : ''} il ${formatDateTime(r.decided_at)}${r.decision_note ? ` — «${r.decision_note}»` : ''}` : '';
  const unita = `${Number(r.units_total || 0)} unità`;
  const link = r.building_exists === false ? '<span class="muted">eliminato definitivamente</span>'
    : `<a href="#/edifici/${Number(r.building_id)}">Apri l’edificio</a>`;
  return `
    <article class="trash-card" data-building-request="${id}">
      <div class="trash-card-head">
        <strong class="trash-code">${escapeHtml(r.building_label || `Edificio #${Number(r.building_id)}`)}</strong>
        <span class="badge" data-building-request-status="${escapeHtml(r.status || '')}">${escapeHtml(stato)}</span>
      </div>
      <dl class="trash-facts">
        <dt>Richiesta</dt><dd>${escapeHtml(formatDateTime(r.requested_at))}${escapeHtml(chi)}</dd>
        <dt>Motivo</dt><dd>${escapeHtml(reasonLabel(r.reason_code))}${r.request_note ? ` — ${escapeHtml(r.request_note)}` : ''}</dd>
        <dt>Unità</dt><dd>${escapeHtml(unita)}</dd>
        ${decisione ? `<dt>Esito</dt><dd>${escapeHtml(decisione)}</dd>` : ''}
      </dl>
      <div class="trash-card-actions">${link}</div>
    </article>`;
}

/** Il messaggio d'errore di una lettura del Cestino unico. */
export function unifiedErrorText(error) {
  if (!error) return 'Impossibile caricare il Cestino.';
  if (error.status === 403) return error.message || 'Non hai accesso a questa vista del Cestino.';
  return error.message || 'Impossibile caricare il Cestino.';
}
