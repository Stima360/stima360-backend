// STIMA360 OS — building-deletion/building-deletion.js
// ELIMINAZIONE-EDIFICIO-1: l'eliminazione CONTROLLATA di un edificio con tutte
// le sue unità. L'UNICO file della Shell che nomina queste rotte.
//
//   Operatore (qualunque ruolo, propria agenzia; property/router.py):
//     GET  /api/property/buildings/{id}/deletion-request        stato, unità, blocchi
//     POST /api/property/buildings/{id}/deletion-request        «Richiedi eliminazione»
//     POST /api/property/building-deletion-requests/{id}/withdraw   «Ritira» (solo chi l'ha chiesta)
//     GET  /api/property/trash/building-groups                  Cestino Edifici, unità raggruppate
//   Super Admin (amministratore di piattaforma; platform_admin/router.py):
//     GET  /api/platform/building-deletion-requests[?status=]    richieste da autorizzare
//     GET  /api/platform/building-deletion-requests/{id}         dettaglio completo
//     POST /api/platform/building-deletion-requests/{id}/approve «Approva» (impronta delle unità viste)
//     POST /api/platform/building-deletion-requests/{id}/reject  «Rifiuta» (motivo obbligatorio)
//     GET  /api/platform/building-deletions/trash                edifici nel Cestino, tutte le agenzie
//     POST /api/platform/building-deletions/{id}/restore      «Ripristina»
//     GET  /api/platform/building-deletions/{id}/purge-check  riepilogo e dipendenze
//     POST /api/platform/building-deletions/{id}/purge        «Elimina definitivamente»
//
// Cartella propria, fuori da trash/: il client del Cestino (trash/*.js e
// views/cestino.js) resta senza comandi del Super Admin, come prima.
//
// La UI NON decide nulla: permessi, blocchi, unità coinvolte e dipendenze
// arrivano dal backend, che resta l'autorità (403 per chi non è Super Admin).
// Qui solo rete, testi e HTML. Nessuna cancellazione automatica: la
// cancellazione definitiva chiede una seconda conferma esplicita con il testo
// esatto, e l'approvazione dello spostamento nel Cestino non la autorizza.
import { apiGet, apiPost } from '../core/api-client.js';
import { escapeHtml, formatDateTime, renderBadge } from '../components/st-table.js';
import { TRASH_REASONS, reasonLabel } from '../trash/trash-model.js';

const PLATFORM = '/api/platform';

// --- rete --------------------------------------------------------------------

export function getDeletionStatus(buildingId) {
  return apiGet(`/api/property/buildings/${Number(buildingId)}/deletion-request`);
}

export function requestDeletion(buildingId, reasonCode, note) {
  const corpo = { reason_code: reasonCode || 'created_by_mistake' };
  const testo = typeof note === 'string' ? note.trim() : '';
  if (testo) corpo.note = testo;
  return apiPost(`/api/property/buildings/${Number(buildingId)}/deletion-request`, corpo);
}

export function withdrawDeletion(requestId) {
  return apiPost(`/api/property/building-deletion-requests/${Number(requestId)}/withdraw`, {});
}

export function listBuildingTrashGroups(offset = 0, limit = 50) {
  return apiGet(`/api/property/trash/building-groups?limit=${Number(limit)}&offset=${Number(offset)}`);
}

export function listDeletionRequests(status = 'pending') {
  return apiGet(`${PLATFORM}/building-deletion-requests?status=${encodeURIComponent(status)}&limit=200`);
}

export function getDeletionRequest(requestId) {
  return apiGet(`${PLATFORM}/building-deletion-requests/${Number(requestId)}`);
}

export function approveDeletion(requestId, fingerprint, note) {
  const corpo = { fingerprint };
  const testo = typeof note === 'string' ? note.trim() : '';
  if (testo) corpo.note = testo;
  return apiPost(`${PLATFORM}/building-deletion-requests/${Number(requestId)}/approve`, corpo);
}

export function rejectDeletion(requestId, note) {
  return apiPost(`${PLATFORM}/building-deletion-requests/${Number(requestId)}/reject`, { note: String(note || '').trim() });
}

export function listPlatformTrash() {
  return apiGet(`${PLATFORM}/building-deletions/trash?limit=200`);
}

export function restoreTrashedBuilding(buildingId) {
  return apiPost(`${PLATFORM}/building-deletions/${Number(buildingId)}/restore`, {});
}

export function purgeCheck(buildingId) {
  return apiGet(`${PLATFORM}/building-deletions/${Number(buildingId)}/purge-check`);
}

export function purgeBuilding(buildingId, confirmText, fingerprint) {
  return apiPost(`${PLATFORM}/building-deletions/${Number(buildingId)}/purge`,
    { confirm_text: String(confirmText || ''), fingerprint });
}

// --- testi (funzioni pure) ------------------------------------------------------

export const REQUEST_STATUS_LABELS = Object.freeze({
  pending: 'In attesa di approvazione',
  approved: 'Approvata',
  rejected: 'Rifiutata',
  withdrawn: 'Ritirata',
});

export const REQUEST_STATUS_TONES = Object.freeze({
  pending: 'warn', approved: 'ok', rejected: 'danger', withdrawn: 'gray',
});

export const UNIT_STATE_LABELS = Object.freeze({
  active: 'attiva', archived: 'archiviata', trash: 'già nel Cestino',
});

export const PURGE_CONFIRM_TEXT = 'ELIMINA DEFINITIVAMENTE';

/** «Coinvolge 5 unità: 3 attive, 1 archiviata, 1 già nel Cestino». */
export function unitsSummaryText(units) {
  const u = units || {};
  const totale = Number(u.total || 0);
  if (!totale) return 'Nessuna unità collegata: si elimina solo l’edificio.';
  const parti = [];
  if (u.active) parti.push(`${u.active} ${u.active === 1 ? 'attiva' : 'attive'}`);
  if (u.archived) parti.push(`${u.archived} ${u.archived === 1 ? 'archiviata' : 'archiviate'}`);
  if (u.trash) parti.push(`${u.trash} già nel Cestino`);
  return `Coinvolge ${totale} unità${parti.length ? `: ${parti.join(', ')}` : ''}.`;
}

/** Chi, quando e com'è andata una richiesta, in frasi. */
export function requestStatusView(req) {
  if (!req) return null;
  const stato = REQUEST_STATUS_LABELS[req.status] || req.status;
  const chiesta = `Richiesta da ${req.requested_by_name || 'un operatore'} il ${formatDateTime(req.requested_at)}`;
  let esito = '';
  if (req.status === 'approved') {
    esito = `Approvata da ${req.decided_by_name || 'Super Admin'} il ${formatDateTime(req.decided_at)}`;
    if (req.restored_at) esito += ` · ripristinata il ${formatDateTime(req.restored_at)}`;
    if (req.purged_at) esito += ` · eliminata definitivamente il ${formatDateTime(req.purged_at)}`;
  } else if (req.status === 'rejected') {
    esito = `Rifiutata da ${req.decided_by_name || 'Super Admin'} il ${formatDateTime(req.decided_at)}`
      + (req.decision_note ? `: «${req.decision_note}»` : '');
  } else if (req.status === 'withdrawn') {
    esito = `Ritirata il ${formatDateTime(req.decided_at)}`;
  }
  return { status: req.status, label: stato, tone: REQUEST_STATUS_TONES[req.status] || 'gray', requested: chiesta,
           outcome: esito, stale: Boolean(req.stale), reason: reasonLabel(req.reason_code), note: req.request_note || '' };
}

/** I blocchi come arrivano (etichetta del backend), con le unità e il loro collegamento. */
export function blockersHtml(blockers) {
  const lista = Array.isArray(blockers) ? blockers : [];
  if (!lista.length) return '';
  return `<ul class="deletion-blockers" data-deletion-blockers>${lista.map((b) => {
    const chi = b.property_label ? `<a href="${escapeHtml(b.href || `#/immobili/${Number(b.property_id)}`)}">${escapeHtml(b.property_label)}</a>: ` : '';
    const voci = Array.isArray(b.items) && b.items.length && !b.property_label
      ? `<ul>${b.items.slice(0, 20).map((i) => `<li>${i.property_id ? `<a href="#/immobili/${Number(i.property_id)}">${escapeHtml(i.label || `#${i.property_id}`)}</a>` : escapeHtml(i.label || '')}</li>`).join('')}</ul>`
      : '';
    const conta = Number.isFinite(b.count) && b.count > 0 && !b.property_label ? ` <span class="muted">(${b.count})</span>` : '';
    return `<li data-blocker-code="${escapeHtml(b.code || '')}">${chi}${escapeHtml(b.label || b.code || '')}${conta}${voci}</li>`;
  }).join('')}</ul>`;
}

/** L'elenco delle unità coinvolte (dettaglio richiudibile). */
export function unitsListHtml(units) {
  const voci = units && Array.isArray(units.items) ? units.items : [];
  if (!voci.length) return '';
  return `<details class="deletion-units" data-deletion-units>
      <summary>Unità coinvolte (${voci.length})</summary>
      <ul>${voci.map((u) => `<li><a href="#/immobili/${Number(u.id)}">${escapeHtml(u.label || `#${u.id}`)}</a>
        <span class="muted">· ${escapeHtml(UNIT_STATE_LABELS[u.state] || u.state || '')}${u.moved_by_approval ? ' · spostata dall’approvazione' : ''}</span></li>`).join('')}</ul>
    </details>`;
}

function opzioniMotivo(scelto = 'created_by_mistake') {
  return TRASH_REASONS.map((r) => `<option value="${escapeHtml(r.value)}"${r.value === scelto ? ' selected' : ''}>${escapeHtml(r.label)}</option>`).join('');
}

/**
 * Il pannello «Eliminazione edificio» della scheda (operatore). `stage`:
 * 'idle' (riepilogo e «Richiedi eliminazione…»), 'confirm' (motivo, nota,
 * «Conferma richiesta»). Lo stato della richiesta (in attesa / approvata /
 * rifiutata / ritirata) si vede sempre quando c'è.
 */
export function deletionPanelHtml(stato, { stage = 'idle', error = '' } = {}) {
  if (!stato) return '';
  const req = requestStatusView(stato.request);
  const inAttesa = req && req.status === 'pending';
  const stato_html = req ? `
      <div class="deletion-request-status" data-deletion-status="${escapeHtml(req.status)}">
        ${renderBadge(req.label, req.tone)}
        <span class="muted">${escapeHtml(req.requested)}${req.reason ? ` · ${escapeHtml(req.reason)}` : ''}</span>
        ${req.outcome ? `<p>${escapeHtml(req.outcome)}</p>` : ''}
        ${inAttesa && req.stale ? '<p class="field-error" data-deletion-stale>Le unità sono cambiate dalla richiesta: il Super Admin non può approvarla così. Ritirala e richiedila di nuovo.</p>' : ''}
      </div>` : '';
  let azioni = '';
  if (inAttesa) {
    azioni = stato.can_withdraw
      ? '<button type="button" class="btn ghost" data-deletion-withdraw>Ritira la richiesta</button>'
      : '<span class="muted">Può ritirarla solo chi l’ha fatta.</span>';
  } else if (stato.can_request && stage === 'confirm') {
    azioni = `
      <label class="field">Motivo
        <select data-deletion-reason>${opzioniMotivo()}</select></label>
      <label class="field">Nota per il Super Admin (facoltativa)
        <textarea data-deletion-note maxlength="500" rows="2"></textarea></label>
      <p class="muted">L’edificio e tutte le sue unità resteranno come sono finché il Super Admin non approva. Con l’approvazione andranno insieme nel Cestino; nulla viene cancellato definitivamente.</p>
      <div class="action-bar">
        <button type="button" class="btn danger" data-deletion-confirm>Conferma richiesta</button>
        <button type="button" class="btn ghost" data-deletion-cancel>Annulla</button>
      </div>`;
  } else if (stato.can_request) {
    azioni = '<button type="button" class="btn ghost" data-deletion-open>Richiedi eliminazione…</button>';
  } else if (!stato.in_trash && Array.isArray(stato.blockers) && stato.blockers.length) {
    azioni = '<p class="muted">Per richiedere l’eliminazione risolvi prima questi punti:</p>' + blockersHtml(stato.blockers);
  }
  return `
    <div class="card panel deletion-panel" data-deletion-panel>
      <h3>Eliminazione edificio</h3>
      <p data-deletion-units-text>${escapeHtml(unitsSummaryText(stato.units))}</p>
      ${unitsListHtml(stato.units)}
      ${stato_html}
      <div class="field-error" data-deletion-error role="alert">${escapeHtml(error)}</div>
      <div class="deletion-actions">${azioni}</div>
      <p class="muted deletion-rule">Si richiede l’eliminazione dell’intero edificio con tutte le sue unità; la autorizza il Super Admin della piattaforma.</p>
    </div>`;
}

/** Il testo di un errore delle rotte di eliminazione. */
export function deletionErrorText(error) {
  if (!error) return 'Operazione non riuscita.';
  if (error.status === 404) return 'Edificio o richiesta non trovati.';
  if (error.status === 503) return error.message || 'Funzione non ancora disponibile su questo database.';
  return error.message || 'Operazione non riuscita.';
}

/**
 * Una card del Cestino Edifici con le sue unità raggruppate (operatore: in
 * sola lettura). `superAdmin` mostra anche i comandi del Super Admin.
 */
export function trashGroupCardHtml(item, { superAdmin = false } = {}) {
  const id = Number(item.id);
  const req = requestStatusView(item.request);
  const chi = item.deleted_by_name ? ` · da ${item.deleted_by_name}` : '';
  const spostate = Number(item.units_moved_by_approval || 0);
  const totale = Number(item.units_total || 0);
  const gia = totale - spostate;
  const dettagli = [spostate ? `${spostate} spostate dall’approvazione` : null,
    gia ? `${gia} già nel Cestino prima` : null].filter(Boolean).join(', ');
  const unita = totale ? `${totale} unità nel Cestino con l’edificio${dettagli ? ` (${dettagli})` : ''}` : 'Nessuna unità';
  const azioni = superAdmin
    ? `<button type="button" class="btn primary" data-group-restore="${id}">Ripristina</button>
       <button type="button" class="btn danger" data-group-purge="${id}">Elimina definitivamente…</button>`
    : '<span class="muted">Ripristino ed eliminazione definitiva: solo il Super Admin.</span>';
  return `
    <article class="trash-card trash-group" data-building-group="${id}">
      <div class="trash-card-head">
        <strong class="trash-code">${escapeHtml(item.label || item.name || `Edificio #${id}`)}</strong>
        <span class="muted trash-type">${escapeHtml(item.agency_name || 'Edificio')}</span>
      </div>
      <dl class="trash-facts">
        <dt>Eliminato</dt><dd data-trash-when>${escapeHtml(formatDateTime(item.deleted_at))}${escapeHtml(chi)}</dd>
        <dt>Motivo</dt><dd data-trash-reason>${escapeHtml(reasonLabel(item.deleted_reason))}</dd>
        <dt>Unità</dt><dd data-group-units>${escapeHtml(unita)}</dd>
        ${req ? `<dt>Richiesta</dt><dd data-group-request>${escapeHtml(req.requested)}${req.outcome ? ` · ${escapeHtml(req.outcome)}` : ''}</dd>` : ''}
      </dl>
      ${unitsListHtml({ items: item.units || [] })}
      <div class="field-error trash-card-error" data-trash-card-error role="alert"></div>
      <div data-group-purge-host></div>
      <div class="trash-card-actions">
        <a class="btn ghost" href="#/edifici/${id}" data-building-trash-open>Apri scheda</a>
        ${azioni}
      </div>
    </article>`;
}

/** Il riepilogo della cancellazione definitiva e la seconda conferma. */
export function purgeSummaryHtml(check) {
  if (!check) return '';
  const propri = Array.isArray(check.will_delete) ? check.will_delete : [];
  if (!check.eligible) {
    return `<div class="deletion-purge" data-purge-blocked>
        <p><strong>Non si può eliminare definitivamente.</strong> Ci sono dati da conservare; nulla viene cancellato:</p>
        ${blockersHtml(check.blockers)}
      </div>`;
  }
  return `<div class="deletion-purge" data-purge-ready>
      <p><strong>Eliminazione definitiva di «${escapeHtml(check.building ? check.building.label : '')}»</strong>: non si può annullare.</p>
      <p>${escapeHtml(unitsSummaryText(check.units))} Verranno cancellati l’edificio e tutte queste unità${propri.length ? ', con:' : '.'}</p>
      ${propri.length ? `<ul>${propri.map((p) => `<li>${escapeHtml(p.label)} <span class="muted">(${Number(p.count)})</span></li>`).join('')}</ul>` : ''}
      <p class="muted">Contatti, proprietari, stime e storico del registro restano.</p>
      <label class="field">Per confermare scrivi <strong>${escapeHtml(PURGE_CONFIRM_TEXT)}</strong>
        <input type="text" data-purge-text autocomplete="off"></label>
      <div class="action-bar">
        <button type="button" class="btn danger" data-purge-confirm disabled>Elimina definitivamente</button>
        <button type="button" class="btn ghost" data-purge-cancel>Annulla</button>
      </div>
    </div>`;
}

// --- comportamento ---------------------------------------------------------------

/**
 * Il pannello dell'operatore nella scheda edificio: carica lo stato, gestisce
 * richiesta (con conferma) e ritiro. `onChange` dopo ogni esito.
 */
export async function mountDeletionPanel(host, buildingId, { onChange = null } = {}) {
  if (!host) return;
  let stato;
  let stage = 'idle';
  let errore = '';
  async function carica() {
    try {
      stato = await getDeletionStatus(buildingId);
    } catch (error) {
      host.innerHTML = `<div class="card panel deletion-panel" data-deletion-panel><p class="muted" data-deletion-unavailable>${escapeHtml(deletionErrorText(error))}</p></div>`;
      return false;
    }
    return true;
  }
  function disegna() {
    host.innerHTML = deletionPanelHtml(stato, { stage, error: errore });
    host.querySelector('[data-deletion-open]')?.addEventListener('click', () => { stage = 'confirm'; errore = ''; disegna(); });
    host.querySelector('[data-deletion-cancel]')?.addEventListener('click', () => { stage = 'idle'; errore = ''; disegna(); });
    const conferma = host.querySelector('[data-deletion-confirm]');
    conferma?.addEventListener('click', async () => {
      conferma.disabled = true;
      try {
        await requestDeletion(buildingId, host.querySelector('[data-deletion-reason]')?.value,
          host.querySelector('[data-deletion-note]')?.value);
        stage = 'idle';
        errore = '';
      } catch (error) {
        errore = deletionErrorText(error);
        if (error && error.data && Array.isArray(error.data.blockers)) stato = { ...stato, blockers: error.data.blockers, can_request: false };
      }
      if (!errore) await carica();
      disegna();
      if (onChange) onChange(stato);
    });
    const ritira = host.querySelector('[data-deletion-withdraw]');
    ritira?.addEventListener('click', async () => {
      ritira.disabled = true;
      try {
        await withdrawDeletion(stato.request.id);
        errore = '';
      } catch (error) {
        errore = deletionErrorText(error);
      }
      await carica();
      disegna();
      if (onChange) onChange(stato);
    });
  }
  if (await carica()) disegna();
}
