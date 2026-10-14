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
//   CESTINO-GENERALE-3, Super Admin generale designato (trash/purge.py, motore condiviso):
//     GET  …/platform/trash/{kind}/{id}/purge-check   immobile, contatto, richiesta acquirente
//     POST …/platform/trash/{kind}/{id}/purge         «Elimina definitivamente» (testo + impronta)
//     (il percorso lo costruisce `unifiedTrashPath`, come per il resto del Cestino unico)
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
import { unifiedTrashPath } from '../trash/trash-unified.js';

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

/** Dove si decide una richiesta: la scheda «Richieste edifici» del Cestino. */
export const DECISION_HASH = '#/cestino/richieste-edifici';

/**
 * APPROVAZIONE-EDIFICI-1: nella scheda dell'edificio una richiesta in attesa
 * si vede soltanto; «Approva» / «Rifiuta» stanno nel Cestino › Richieste
 * edifici (pannello del Super Admin). Il collegamento lo dice: a chi usa la
 * superficie di piattaforma come comando, agli altri come informazione.
 * `platformAdmin` arriva dalla scheda (che legge la sessione): qui nessuna
 * regola di permesso, decide sempre il backend.
 */
export function decisionLinkHtml({ platformAdmin = false } = {}) {
  return platformAdmin
    ? `<p class="deletion-decide" data-deletion-decide="super-admin">Qui si vede solo lo stato. Per approvare o rifiutare: <a class="btn ghost" href="${DECISION_HASH}" data-deletion-decide-link>Apri Cestino › Richieste edifici</a></p>`
    : `<p class="muted deletion-decide" data-deletion-decide="operator">La decide il Super Admin della piattaforma da Cestino › Richieste edifici. <a href="${DECISION_HASH}" data-deletion-decide-link>Vedi le richieste della tua agenzia</a>.</p>`;
}

/**
 * Il pannello «Eliminazione edificio» della scheda (operatore). `stage`:
 * 'idle' (riepilogo e «Richiedi eliminazione…»), 'confirm' (motivo, nota,
 * «Conferma richiesta»). Lo stato della richiesta (in attesa / approvata /
 * rifiutata / ritirata) si vede sempre quando c'è.
 */
export function deletionPanelHtml(stato, { stage = 'idle', error = '', platformAdmin = false } = {}) {
  if (!stato) return '';
  const req = requestStatusView(stato.request);
  const inAttesa = req && req.status === 'pending';
  const stato_html = req ? `
      <div class="deletion-request-status" data-deletion-status="${escapeHtml(req.status)}">
        ${renderBadge(req.label, req.tone)}
        <span class="muted">${escapeHtml(req.requested)}${req.reason ? ` · ${escapeHtml(req.reason)}` : ''}</span>
        ${req.outcome ? `<p>${escapeHtml(req.outcome)}</p>` : ''}
        ${inAttesa && req.stale ? '<p class="field-error" data-deletion-stale>Le unità sono cambiate dalla richiesta: il Super Admin non può approvarla così. Ritirala e richiedila di nuovo.</p>' : ''}
        ${inAttesa ? decisionLinkHtml({ platformAdmin }) : ''}
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
  if (error.status === 403) return error.message || 'Operazione riservata al Super Admin generale designato.';
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

/**
 * ELIMINAZIONE-EDIFICIO-2: i riferimenti storici che RESTANO dopo la
 * cancellazione definitiva (comandi e «Annulla» di STIMA Voice, storico degli
 * stati e collegamenti ai proprietari copiati nel registro).
 */
export function preservedHtml(preserved) {
  const lista = Array.isArray(preserved) ? preserved : [];
  if (!lista.length) return '';
  return `<div class="deletion-preserved" data-purge-preserved>
      <p><strong>Riferimenti storici conservati</strong> (non vengono cancellati):</p>
      <ul>${lista.map((p) => {
        const voci = Array.isArray(p.items) && p.items.length
          ? `<ul>${p.items.slice(0, 20).map((i) => `<li>${escapeHtml(i.label || '')}</li>`).join('')}${p.items.length > 20 ? `<li class="muted">… e altri ${p.items.length - 20}</li>` : ''}</ul>`
          : '';
        const conta = Number.isFinite(p.count) && p.count > 0 ? ` <span class="muted">(${p.count})</span>` : '';
        return `<li data-preserved-code="${escapeHtml(p.code || '')}">${escapeHtml(p.label || p.code || '')}${conta}${voci}</li>`;
      }).join('')}</ul>
    </div>`;
}

/** Il riepilogo della cancellazione definitiva e la seconda conferma. */
export function purgeSummaryHtml(check) {
  if (!check) return '';
  const propri = Array.isArray(check.will_delete) ? check.will_delete : [];
  if (!check.eligible) {
    return `<div class="deletion-purge" data-purge-blocked>
        <p><strong>Non si può eliminare definitivamente.</strong> Ci sono dati da conservare; nulla viene cancellato:</p>
        ${blockersHtml(check.blockers)}
        ${preservedHtml(check.preserved)}
      </div>`;
  }
  return `<div class="deletion-purge" data-purge-ready>
      <p><strong>Eliminazione definitiva di «${escapeHtml(check.building ? check.building.label : '')}»</strong>: non si può annullare.</p>
      <p>${escapeHtml(unitsSummaryText(check.units))} Verranno cancellati l’edificio e tutte queste unità${propri.length ? ', con:' : '.'}</p>
      ${propri.length ? `<ul>${propri.map((p) => `<li>${escapeHtml(p.label)} <span class="muted">(${Number(p.count)})</span></li>`).join('')}</ul>` : ''}
      <p class="muted">Contatti, proprietari, stime e storico del registro restano.</p>
      ${preservedHtml(check.preserved)}
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
export async function mountDeletionPanel(host, buildingId, { onChange = null, initialStage = 'idle', platformAdmin = false } = {}) {
  if (!host) return;
  let stato;
  // CESTINO-GENERALE-2: da un elenco il pannello si apre gia' sul motivo
  // (`confirm`), solo se il backend dice che la richiesta si puo' fare
  let stage = initialStage === 'confirm' ? 'confirm' : 'idle';
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
    host.innerHTML = deletionPanelHtml(stato, { stage, error: errore, platformAdmin });
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


// --- CESTINO-GENERALE-3: cancellazione definitiva di un elemento del Cestino ----------
//
// Stesso flusso degli edifici (riepilogo -> testo esatto -> impronta), dalle card
// del Cestino unico nella vista globale, che legge SOLO il Super Admin generale
// designato (agli altri il backend risponde 403 e non arriva nessuna card). Il
// backend ricontrolla tutto a ogni passo: qui nessuna regola di permesso.

/** Le categorie del motore condiviso (gli edifici restano sul loro flusso). */
export const ITEM_PURGE_KINDS = Object.freeze(['property', 'contact', 'buy_request']);

const ITEM_PURGE_NAMES = Object.freeze({ property: 'l’immobile', contact: 'il contatto', buy_request: 'la richiesta acquirente' });
const ITEM_PURGE_OF = Object.freeze({ property: 'dell’immobile', contact: 'del contatto', buy_request: 'della richiesta acquirente' });

function itemPurgePath(kind, id, coda) {
  if (!ITEM_PURGE_KINDS.includes(kind)) throw new Error('Categoria non gestita');
  return unifiedTrashPath(`/${encodeURIComponent(kind)}/${Number(id)}/${coda}`, {}, { scope: 'platform' });
}

export function itemPurgeCheck(kind, id) {
  return apiGet(itemPurgePath(kind, id, 'purge-check'));
}

export function purgeItem(kind, id, confirmText, fingerprint) {
  return apiPost(itemPurgePath(kind, id, 'purge'), { confirm_text: String(confirmText || ''), fingerprint });
}

/** I blocchi di un elemento: motivo, quanti, «come risolvere» e collegamento (dal backend). */
export function itemBlockersHtml(blockers) {
  const lista = Array.isArray(blockers) ? blockers : [];
  if (!lista.length) return '';
  return `<ul class="deletion-blockers" data-item-blockers>${lista.map((b) => {
    const conta = Number(b.count) > 0 ? ` <span class="muted">(${Number(b.count)})</span>` : '';
    const link = b.href && String(b.href).startsWith('#/') ? ` <a href="${escapeHtml(b.href)}" data-item-blocker-link>Apri</a>` : '';
    const aiuto = b.help ? `<span class="deletion-help" data-item-blocker-help>Come risolvere: ${escapeHtml(b.help)}</span>` : '';
    return `<li data-blocker-code="${escapeHtml(b.code || '')}">${escapeHtml(b.label || b.code || '')}${conta}${link}${aiuto}</li>`;
  }).join('')}</ul>`;
}

/** Il riepilogo della cancellazione definitiva di un elemento e la conferma forte. */
export function itemPurgeSummaryHtml(check) {
  if (!check) return '';
  const nome = ITEM_PURGE_NAMES[check.kind] || 'l’elemento';
  const etichetta = check.label ? ` «${escapeHtml(check.label)}»` : '';
  if (!check.eligible) {
    return `<div class="deletion-purge" data-item-purge-blocked>
        <p><strong>Non si può eliminare definitivamente${etichetta}.</strong> Nulla viene cancellato. Motivi:</p>
        ${itemBlockersHtml(check.blockers)}
        ${preservedHtml(check.preserved)}
        <div class="action-bar"><button type="button" class="btn ghost" data-item-purge-close>Chiudi</button></div>
      </div>`;
  }
  const propri = Array.isArray(check.will_delete) ? check.will_delete : [];
  const resta = check.kind === 'contact'
    ? 'Restano gli altri dati del CRM e il registro del Cestino, senza dati personali.'
    : 'Restano gli altri dati del CRM (contatti, immobili, richieste) e il registro del Cestino.';
  return `<div class="deletion-purge" data-item-purge-ready>
      <p><strong>Eliminazione definitiva ${escapeHtml(ITEM_PURGE_OF[check.kind] || 'dell’elemento')}${etichetta}</strong>: non si può annullare.</p>
      <p>Si cancella ${escapeHtml(nome)}${propri.length ? ', con i suoi dati:' : ', senza altri dati collegati.'}</p>
      ${propri.length ? `<ul data-item-will-delete>${propri.map((p) => `<li data-will-delete="${escapeHtml(p.table || '')}">${escapeHtml(p.label || p.table || '')} <span class="muted">(${Number(p.count)})</span></li>`).join('')}</ul>` : ''}
      <p class="muted">${escapeHtml(resta)}</p>
      ${preservedHtml(check.preserved)}
      <label class="field">Per confermare scrivi <strong>${escapeHtml(PURGE_CONFIRM_TEXT)}</strong>
        <input type="text" data-item-purge-text autocomplete="off" autocapitalize="characters" spellcheck="false"></label>
      <div class="action-bar">
        <button type="button" class="btn danger" data-item-purge-confirm disabled>Elimina definitivamente</button>
        <button type="button" class="btn ghost" data-item-purge-close>Annulla</button>
      </div>
    </div>`;
}

/** Il testo di un errore della cancellazione definitiva di un elemento. */
export function itemPurgeErrorText(error) {
  if (!error) return 'Operazione non riuscita. Nessun dato è stato cancellato.';
  if (error.status === 404) return 'Elemento non trovato: forse è già stato eliminato definitivamente.';
  if (error.status === 403) return error.message || 'Operazione riservata al Super Admin generale designato.';
  return error.message || 'Operazione non riuscita. Nessun dato è stato cancellato.';
}

/** Il comando nella card del Cestino unico: separato dagli altri, in una zona propria. */
export function itemPurgeZoneHtml(kind, id) {
  if (kind === 'building') {
    return `<div class="trash-final" data-item-purge-zone="building:${Number(id)}">
        <a class="btn ghost" href="#/cestino/richieste-edifici" data-item-purge-building>Eliminazione definitiva: Richieste edifici</a>
      </div>`;
  }
  if (!ITEM_PURGE_KINDS.includes(kind)) return '';
  return `<div class="trash-final" data-item-purge-zone="${escapeHtml(kind)}:${Number(id)}">
      <p class="muted trash-final-note">Solo Super Admin · irreversibile, diverso da «Ripristina»</p>
      <button type="button" class="btn danger" data-item-purge>Elimina definitivamente…</button>
      <div data-item-purge-host></div>
      <div class="field-error" data-item-purge-error role="alert"></div>
    </div>`;
}

/**
 * Aggiunge alla card la zona della cancellazione definitiva e ne gestisce il
 * flusso: controllo -> riepilogo -> testo esatto -> POST con l'impronta.
 * Riepilogo superato (PURGE_STALE): si ricarica e si chiede di nuovo. Dopo
 * l'esito `onDone({ kind, id, label })`.
 */
export function bindFinalDeletion(card, item, { onDone = null } = {}) {
  if (!card || !item) return;
  const kind = String(item.kind || '');
  const id = Number(item.id);
  const zona = itemPurgeZoneHtml(kind, id);
  if (!zona) return;
  card.insertAdjacentHTML('beforeend', zona);                 // in fondo, dopo le azioni della card
  if (!ITEM_PURGE_KINDS.includes(kind)) return;
  const bottone = card.querySelector('[data-item-purge]');
  const host = card.querySelector('[data-item-purge-host]');
  const erroreEl = card.querySelector('[data-item-purge-error]');

  function chiudi() { host.innerHTML = ''; bottone.hidden = false; }

  function disegna(check, avviso = '') {
    host.innerHTML = itemPurgeSummaryHtml(check);
    bottone.hidden = true;
    erroreEl.textContent = avviso;
    host.querySelector('[data-item-purge-close]')?.addEventListener('click', () => { erroreEl.textContent = ''; chiudi(); });
    const testo = host.querySelector('[data-item-purge-text]');
    const conferma = host.querySelector('[data-item-purge-confirm]');
    if (!testo || !conferma) return;
    testo.addEventListener('input', () => { conferma.disabled = testo.value.trim() !== PURGE_CONFIRM_TEXT; });
    conferma.addEventListener('click', async () => {
      if (testo.value.trim() !== PURGE_CONFIRM_TEXT) return;
      conferma.disabled = true;
      erroreEl.textContent = '';
      try {
        await purgeItem(kind, id, testo.value, check.fingerprint);
      } catch (error) {
        if (error && error.code === 'PURGE_STALE') { await apri(itemPurgeErrorText(error)); return; }
        if (error && error.data && Array.isArray(error.data.blockers) && error.data.blockers.length) {
          disegna({ ...check, eligible: false, blockers: error.data.blockers }, itemPurgeErrorText(error));
          return;
        }
        erroreEl.textContent = itemPurgeErrorText(error);
        conferma.disabled = testo.value.trim() !== PURGE_CONFIRM_TEXT;
        return;
      }
      host.innerHTML = '';
      card.setAttribute('data-item-purged', '');
      if (onDone) onDone({ kind, id, label: check.label || item.label || '' });
    });
  }

  async function apri(avviso = '') {
    bottone.disabled = true;
    erroreEl.textContent = '';
    try {
      disegna(await itemPurgeCheck(kind, id), avviso);
    } catch (error) {
      erroreEl.textContent = itemPurgeErrorText(error);
    } finally {
      bottone.disabled = false;
    }
  }

  bottone.addEventListener('click', () => { apri(); });
}

/** Il messaggio dopo l'esito. */
export function finalDeletionDoneText(esito) {
  const label = esito && esito.label ? ` «${esito.label}»` : '';
  return `Eliminazione definitiva completata${label}. Il registro del Cestino conserva l’operazione.`;
}
