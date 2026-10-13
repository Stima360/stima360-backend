// STIMA360 OS — views/cestino.js
// DELETE-ARCH Fase 2B3: la pagina «Cestino», nata con i soli immobili.
// CESTINO-CONTATTI-1: due schede, «Immobili» (predefinita, invariata) e
// «Contatti» (caricata al primo tocco, o subito con `#/cestino/contatti`);
// CESTINO-EDIFICI-1: la terza, «Edifici» (`#/cestino/edifici`);
// CESTINO-RICHIESTE-1: la quarta, «Richieste» acquirente (`#/cestino/richieste`),
// con le stesse card, lo stesso «Ripristina» e lo stesso «Carica altri».
// Il ripristino di una richiesta il cui contatto e' nel Cestino si ferma: la
// card mostra il motivo e il collegamento al contatto da ripristinare prima.
// Per i contatti il ripristino puo' segnalare POSSIBILI DOPPIONI attivi
// (stessa email o telefono): solo un avviso con i collegamenti, nessuna
// fusione e nessuna modifica dell'altro contatto.
//
//   GET  /api/property/trash?limit=&offset=          l'elenco (agency-scoped:
//        owner/admin vedono il Cestino dell'agenzia, un agent solo cio' che
//        ha spostato lui - lo decide il backend, la pagina non filtra nulla)
//   POST /api/property/properties/{id}/restore       «Ripristina»
//
// Ripristino riuscito: la card esce dalla lista e compare il toast, senza
// rileggere l'elenco. Rifiutato (409 RESTORE_CONFLICT, 403
// NOT_DELETED_BY_YOU, ...): la card resta com'e', con il messaggio del
// backend e gli immobili in conflitto; nessuna modifica locale inventata e
// nessuna correzione automatica di codice, dati catastali o richieste.
//
// Nessuna eliminazione definitiva da qui.
// ELIMINAZIONE-EDIFICIO-1: la scheda «Edifici» mostra ogni edificio con le sue
// unità raggruppate (GET /api/property/trash/building-groups), in sola
// lettura: ripristino ed eliminazione definitiva sono del Super Admin
// (Cestino › Richieste edifici). Nessun «Ripristina» per gli operatori.
//
// CESTINO-GENERALE-1 (CG-1): il Cestino diventa UNICO.
//   * «Tutti» (predefinita, `#/cestino` o `#/cestino/tutti`): l'elenco
//     unificato di tutte le categorie con ricerca, filtri e contatori
//     (GET /api/trash, /api/trash/counts), in SOLA LETTURA; «Gestisci» porta
//     alla scheda di categoria, che resta quella di prima (con «Ripristina»).
//   * Vista globale per il Super Admin generale designato (GET
//     /api/platform/trash), con selettore dell'agenzia: un amministratore di
//     piattaforma la sceglie; a chi non e' il designato il backend risponde 403
//     e qui compare solo l'avviso.
//   * «Richieste edifici» (`#/cestino/richieste-edifici`): per un
//     amministratore di piattaforma la gestione centralizzata gia' esistente
//     (renderEliminazioni, stesso codice e stesse rotte, prima in Rete); per
//     l'agenzia l'elenco delle sue richieste, in sola lettura. Una richiesta in
//     attesa NON e' un elemento eliminato.
//   * Se il server non ha ancora il Cestino unico (404), «Tutti» lo dice e si
//     apre la scheda Immobili come prima.
import { escapeHtml, formatDateTime } from '../components/st-table.js';
import { showToast } from '../census/census-sheets.js';
import { COMMERCIAL_STATUS_LABELS } from './incarichi.js';
import {
  listBuyRequestTrash, listContactTrash, listTrash, restoreBuyRequest,
  restoreContact, restoreProperty,
} from '../trash/trash-api.js';
import {
  BUY_RESTORED_TOAST, BUY_STATUS_LABELS, buyDuplicatesView, buyRequestLine, buyRequestName, buyRestoreErrorView,
} from '../trash/trash-model.js';
import { buyBlockersHtml } from '../trash/trash-dialog.js';
import {
  bindFinalDeletion, finalDeletionDoneText, listBuildingTrashGroups, trashGroupCardHtml,
} from '../building-deletion/building-deletion.js';
// CESTINO-GENERALE-1 (CG-1): il Cestino unico (scheda «Tutti», sola lettura) e
// le richieste di eliminazione degli edifici dentro il Cestino.
import {
  listAgencyBuildingRequests, listTrashAgencies, listUnifiedTrash, unifiedTrashCounts, unifiedTrashRelations,
} from '../trash/trash-api.js';
import {
  TRASH_KINDS, UPCOMING_KINDS, buildingRequestRowHtml, countsHtml, reasonOptionsHtml, relationsHtml,
  unifiedCardHtml, unifiedErrorText,
} from '../trash/trash-unified.js';
import { renderEliminazioni } from './rete-eliminazioni.js';
import {
  CONTACT_RESTORED_TOAST, CONTACT_STATUS_LABELS, RESTORED_TOAST,
  contactLine, contactName, duplicatesView, propertyLine, reasonLabel, restoreErrorView,
} from '../trash/trash-model.js';

const PAGE_SIZE = 50;

/** Una card del Cestino (esportata per la prova di rendering mobile). */
export function trashCardHtml(item) {
  const stato = COMMERCIAL_STATUS_LABELS[item.commercial_status] || item.commercial_status || '—';
  const chi = item.deleted_by_name ? ` · da ${item.deleted_by_name}` : '';
  const motivo = reasonLabel(item.deleted_reason) + (item.deleted_note ? ` — ${item.deleted_note}` : '');
  return `
    <article class="trash-card" data-trash-item="${Number(item.id)}">
      <div class="trash-card-head">
        <strong class="trash-code">${escapeHtml(item.code || `#${item.id}`)}</strong>
        <span class="muted trash-type">${escapeHtml(item.title || '')}</span>
      </div>
      <div class="trash-line">${escapeHtml(propertyLine(item) || '—')}</div>
      <dl class="trash-facts">
        <dt>Eliminato</dt><dd data-trash-when>${escapeHtml(formatDateTime(item.deleted_at))}${escapeHtml(chi)}</dd>
        <dt>Motivo</dt><dd data-trash-reason>${escapeHtml(motivo)}</dd>
        <dt>Stato</dt><dd data-trash-status>${escapeHtml(stato)}${item.archived_at ? ' · archiviato' : ''}</dd>
      </dl>
      <div class="field-error trash-card-error" data-trash-card-error role="alert"></div>
      <div class="trash-card-actions">
        <button type="button" class="btn primary" data-trash-restore="${Number(item.id)}">Ripristina</button>
      </div>
    </article>`;
}

function conflittiHtml(view) {
  if (!view.conflicts.length) return escapeHtml(view.text);
  return `${escapeHtml(view.text)}<ul class="trash-conflicts" data-trash-conflicts>${view.conflicts.map((c) =>
    `<li><a href="#/immobili/${Number(c.id)}">${escapeHtml(c.code)}</a> — ${escapeHtml(c.label)}</li>`).join('')}</ul>`;
}

/** Una card del Cestino Contatti (esportata per le prove). */
export function contactTrashCardHtml(item) {
  const chi = item.deleted_by_name ? ` · da ${item.deleted_by_name}` : '';
  const motivo = reasonLabel(item.deleted_reason) + (item.deleted_note ? ` — ${item.deleted_note}` : '');
  const stato = CONTACT_STATUS_LABELS[item.status] || item.status || '—';
  const id = Number(item.id);
  return `
    <article class="trash-card" data-contact-trash-item="${id}">
      <div class="trash-card-head">
        <strong class="trash-code">${escapeHtml(contactName(item))}</strong>
        <span class="muted trash-type">${escapeHtml(item.contact_type === 'company' ? 'Azienda' : 'Persona')}</span>
      </div>
      <div class="trash-line">${escapeHtml(contactLine(item) || '—')}</div>
      <dl class="trash-facts">
        <dt>Eliminato</dt><dd data-trash-when>${escapeHtml(formatDateTime(item.deleted_at))}${escapeHtml(chi)}</dd>
        <dt>Motivo</dt><dd data-trash-reason>${escapeHtml(motivo)}</dd>
        <dt>Stato</dt><dd data-trash-status>${escapeHtml(stato)}</dd>
      </dl>
      <div class="field-error trash-card-error" data-trash-card-error role="alert"></div>
      <div class="trash-card-actions">
        <a class="btn ghost" href="#/contatti/${id}" data-contact-trash-open>Apri scheda</a>
        <button type="button" class="btn primary" data-contact-restore="${id}">Ripristina</button>
      </div>
    </article>`;
}

/**
 * L'avviso dopo un ripristino con possibili doppioni (esportato per le prove).
 * Contatti (predefinito) o, CESTINO-EDIFICI-1, edifici: cambiano solo il
 * collegamento e le parole.
 */
export function duplicatesNoticeHtml(nome, doppioni, { kind = 'contact' } = {}) {
  if (!doppioni.length) return '';
  // CESTINO-RICHIESTE-1: e le richieste acquirente (le altre aperte dello stesso contatto)
  const testi = {
    contact: { href: (id) => `#/contatti/${Number(id)}`, attr: 'data-contact-duplicates', dove: 'fra i contatti',
               nulla: 'Nessun contatto è stato unito o modificato.', lista: 'Possibili doppioni attivi, da controllare:' },
    building: { href: (id) => `#/edifici/${Number(id)}`, attr: 'data-building-duplicates', dove: 'fra gli edifici',
                nulla: 'Nessun edificio è stato unito o modificato.', lista: 'Possibili doppioni attivi, da controllare:' },
    buy: { href: (id) => `#/acquirenti/${Number(id)}`, attr: 'data-buy-duplicates', dove: 'fra le richieste',
           nulla: 'Nessuna richiesta è stata unita, chiusa o modificata.',
           lista: 'Lo stesso contatto ha altre richieste aperte, da controllare:' },
  };
  const t = testi[kind] || testi.contact;
  return `<div class="trash-notice" ${t.attr} role="status">
      <p><strong>${escapeHtml(nome)}</strong> è di nuovo ${t.dove}. ${t.lista}</p>
      <ul>${doppioni.map((d) => `<li><a href="${t.href(d.id)}">${escapeHtml(d.name)}</a>${d.reason ? ` — ${escapeHtml(d.reason)}` : ''}</li>`).join('')}</ul>
      <p class="muted">${t.nulla}</p>
    </div>`;
}

/** Una card del Cestino Richieste (esportata per le prove). */
export function buyTrashCardHtml(item) {
  const chi = item.deleted_by_name ? ` · da ${item.deleted_by_name}` : '';
  const motivo = reasonLabel(item.deleted_reason) + (item.deleted_note ? ` — ${item.deleted_note}` : '');
  const id = Number(item.id);
  const contatto = item.contact_in_trash
    ? `${escapeHtml(item.contact_name || `Contatto #${Number(item.contact_id)}`)} <span class="muted">(nel Cestino)</span>`
    : `<a href="#/contatti/${Number(item.contact_id)}">${escapeHtml(item.contact_name || `Contatto #${Number(item.contact_id)}`)}</a>`;
  return `
    <article class="trash-card" data-buy-trash-item="${id}">
      <div class="trash-card-head">
        <strong class="trash-code">${escapeHtml(buyRequestName(item))}</strong>
        <span class="muted trash-type">Richiesta acquirente</span>
      </div>
      <div class="trash-line">${escapeHtml(buyRequestLine({ ...item, contact_name: '' }) || '—')}</div>
      <dl class="trash-facts">
        <dt>Contatto</dt><dd data-trash-contact>${contatto}</dd>
        <dt>Eliminata</dt><dd data-trash-when>${escapeHtml(formatDateTime(item.deleted_at))}${escapeHtml(chi)}</dd>
        <dt>Motivo</dt><dd data-trash-reason>${escapeHtml(motivo)}</dd>
        <dt>Stato</dt><dd data-trash-status>${escapeHtml(BUY_STATUS_LABELS[item.status] || item.status || '—')}</dd>
      </dl>
      <div class="field-error trash-card-error" data-trash-card-error role="alert"></div>
      <div class="trash-card-actions">
        <a class="btn ghost" href="#/acquirenti/${id}" data-buy-trash-open>Apri scheda</a>
        <button type="button" class="btn primary" data-buy-restore="${id}">Ripristina</button>
      </div>
    </article>`;
}

/** Il rifiuto di un ripristino di richiesta: il testo e i collegamenti (contatto nel Cestino). */
export function buyRestoreErrorHtml(error) {
  const vista = buyRestoreErrorView(error);
  return `${escapeHtml(vista.text)}${vista.blockers.length ? buyBlockersHtml(error.data.blockers) : ''}`;
}

/**
 * Una lista del Cestino con «Ripristina» e «Carica altri»: la stessa per
 * immobili e contatti, cambiano solo le funzioni passate.
 */
function listaCestino(listEl, pagerEl, toastHost, cfg) {
  let offset = 0;

  function vuoto() {
    if (!listEl.querySelector(`[${cfg.itemAttr}]`)) {
      listEl.innerHTML = '<p class="muted" data-trash-empty>Il Cestino è vuoto.</p>';
    }
  }

  function collega(card) {
    const bottone = card.querySelector(`[${cfg.restoreAttr}]`);
    const erroreEl = card.querySelector('[data-trash-card-error]');
    bottone.addEventListener('click', async () => {
      const id = Number(bottone.dataset[cfg.restoreKey]);
      bottone.disabled = true;
      bottone.textContent = 'Ripristino…';
      erroreEl.innerHTML = '';
      try {
        const riga = await cfg.restore(id);
        card.remove();
        showToast(toastHost, { text: cfg.toast });
        if (cfg.afterRestore) cfg.afterRestore(riga);
        vuoto();
      } catch (error) {
        // la card resta com'era: nessuna modifica locale finta
        erroreEl.innerHTML = cfg.errorHtml(error);
        bottone.textContent = 'Ripristina';
        bottone.disabled = false;
      }
    });
  }

  async function carica() {
    pagerEl.innerHTML = '';
    let pagina;
    try {
      pagina = await cfg.list(offset, PAGE_SIZE);
    } catch (error) {
      if (offset === 0) listEl.innerHTML = `<div class="error-box">${escapeHtml(error.message || 'Impossibile caricare il Cestino.')}</div>`;
      else pagerEl.innerHTML = `<div class="field-error">${escapeHtml(error.message || cfg.moreError)}</div>`;
      return;
    }
    const voci = (pagina && pagina.items) || [];
    if (offset === 0) listEl.innerHTML = '';
    for (const item of voci) {
      listEl.insertAdjacentHTML('beforeend', cfg.card(item));
      const card = listEl.querySelector(`[${cfg.itemAttr}="${Number(item.id)}"]`);
      if (card) collega(card);
    }
    vuoto();
    if (pagina && pagina.has_more) {
      pagerEl.innerHTML = '<button type="button" class="btn ghost" data-trash-more>Carica altri</button>';
      pagerEl.querySelector('[data-trash-more]').addEventListener('click', () => {
        offset += PAGE_SIZE;
        carica();
      });
    }
  }

  return carica;
}

/**
 * ELIMINAZIONE-EDIFICIO-1: il Cestino Edifici, un edificio per card con le sue
 * unità raggruppate; sola lettura per gli operatori.
 */
function gruppiEdifici(listEl, pagerEl) {
  let offset = 0;
  return async function carica() {
    pagerEl.innerHTML = '';
    let pagina;
    try {
      pagina = await listBuildingTrashGroups(offset, PAGE_SIZE);
    } catch (error) {
      if (offset === 0) listEl.innerHTML = `<div class="error-box">${escapeHtml(error.message || 'Impossibile caricare il Cestino.')}</div>`;
      else pagerEl.innerHTML = `<div class="field-error">${escapeHtml(error.message || 'Impossibile caricare altri edifici.')}</div>`;
      return;
    }
    const voci = (pagina && pagina.items) || [];
    if (offset === 0) listEl.innerHTML = '';
    for (const item of voci) listEl.insertAdjacentHTML('beforeend', trashGroupCardHtml(item));
    if (!listEl.querySelector('[data-building-group]')) {
      listEl.innerHTML = '<p class="muted" data-trash-empty>Il Cestino è vuoto.</p>';
    }
    if (pagina && pagina.has_more) {
      pagerEl.innerHTML = '<button type="button" class="btn ghost" data-trash-more>Carica altri</button>';
      pagerEl.querySelector('[data-trash-more]').addEventListener('click', () => {
        offset += PAGE_SIZE;
        carica();
      });
    }
  };
}

const SCHEDE = ['tutti', 'immobili', 'contatti', 'edifici', 'richieste', 'richieste-edifici'];

/** La scheda iniziale dall'indirizzo (esportata per le prove). */
export function initialTrashTab(hash, { platformOnly = false } = {}) {
  const scheda = (String(hash || '').match(/^#\/cestino\/(tutti|immobili|contatti|edifici|richieste-edifici|richieste)\b/) || [])[1];
  if (platformOnly) return scheda === 'richieste-edifici' ? scheda : 'tutti';
  return scheda || 'tutti';
}

/** Il Cestino unico: filtri, contatori, elenco, collegamenti (sola lettura). */
function cestinoUnico(panel, { platformOnly, meId, onMissing }) {
  const stato = { scope: platformOnly ? 'platform' : 'agency', filtri: { type: 'all' }, offset: 0 };
  const listEl = panel.querySelector('[data-unified-list]');
  const pagerEl = panel.querySelector('[data-unified-pager]');
  const countsEl = panel.querySelector('[data-unified-counts]');
  const scopeEl = panel.querySelector('[data-unified-scope]');
  const agencyEl = panel.querySelector('[data-unified-agency]');
  const form = panel.querySelector('[data-unified-filters]');
  const campo = (nome) => form.querySelector(`[name="${nome}"]`);
  let richiesta = 0;
  let agenzieCaricate = false;

  function leggiFiltri() {
    const mie = campo('mine') && campo('mine').checked && meId;
    stato.filtri = {
      type: campo('type').value || 'all',
      q: campo('q').value,
      reason: campo('reason').value,
      date_from: campo('date_from').value,
      date_to: campo('date_to').value,
      deleted_by: mie ? meId : '',
      agency: stato.scope === 'platform' && agencyEl ? agencyEl.value : '',
    };
  }

  function collega(card) {
    const bottone = card.querySelector('[data-trash-relations]');
    const host = card.querySelector('[data-trash-relations-host]');
    if (!bottone) return;
    bottone.addEventListener('click', async () => {
      if (host.innerHTML) { host.innerHTML = ''; return; }
      const [kind, id] = bottone.dataset.trashRelations.split(':');
      host.innerHTML = '<p class="muted">Caricamento…</p>';
      try {
        host.innerHTML = relationsHtml(await unifiedTrashRelations(kind, Number(id), { scope: stato.scope }));
      } catch (error) {
        host.innerHTML = `<p class="field-error">${escapeHtml(unifiedErrorText(error))}</p>`;
      }
    });
  }

  async function caricaAgenzie() {
    if (agenzieCaricate || !agencyEl) return;
    agenzieCaricate = true;
    try {
      const agenzie = await listTrashAgencies();
      const voci = Array.isArray(agenzie) ? agenzie : ((agenzie && agenzie.items) || []);
      agencyEl.innerHTML = `<option value="">Tutte le agenzie</option>${voci.map((a) =>
        `<option value="${Number(a.id)}">${escapeHtml(a.name || `Agenzia #${a.id}`)}</option>`).join('')}`;
    } catch (_error) {
      agencyEl.innerHTML = '<option value="">Tutte le agenzie</option>';
    }
  }

  async function carica({ reset = true } = {}) {
    const mia = ++richiesta;
    if (reset) { stato.offset = 0; listEl.innerHTML = '<p class="muted">Caricamento…</p>'; }
    pagerEl.innerHTML = '';
    if (agencyEl) agencyEl.hidden = stato.scope !== 'platform';
    let pagina;
    let conti = null;
    try {
      [pagina, conti] = await Promise.all([
        listUnifiedTrash(stato.filtri, { scope: stato.scope, offset: stato.offset, limit: PAGE_SIZE }),
        reset ? unifiedTrashCounts(stato.filtri, { scope: stato.scope }) : Promise.resolve(null),
      ]);
    } catch (error) {
      if (mia !== richiesta) return;
      if (error && error.status === 404 && stato.scope === 'agency' && onMissing) { onMissing(); return; }
      listEl.innerHTML = `<div class="error-box" data-unified-error>${escapeHtml(unifiedErrorText(error))}</div>`;
      if (reset) countsEl.innerHTML = '';
      return;
    }
    if (mia !== richiesta) return;
    if (conti) countsEl.innerHTML = countsHtml(conti, stato.filtri.type || 'all');
    for (const chip of Array.from(countsEl.querySelectorAll('[data-trash-count]'))) {
      chip.addEventListener('click', () => {
        campo('type').value = chip.dataset.trashCount;
        leggiFiltri();
        carica();
      });
    }
    const voci = (pagina && pagina.items) || [];
    if (reset) listEl.innerHTML = '';
    for (const item of voci) {
      listEl.insertAdjacentHTML('beforeend', unifiedCardHtml(item, { scope: stato.scope }));
      const card = listEl.querySelector(`[data-unified-item="${String(item.kind)}:${Number(item.id)}"]`);
      if (card) collega(card);
      // CESTINO-GENERALE-3: la vista globale la legge solo il Super Admin generale designato
      // (agli altri 403, nessuna card); il comando e il suo flusso stanno fuori da questo file
      if (card && stato.scope === 'platform') {
        bindFinalDeletion(card, item, {
          onDone: (esito) => { showToast(panel, { text: finalDeletionDoneText(esito) }); carica(); },
        });
      }
    }
    if (!listEl.querySelector('[data-unified-item]')) {
      const filtrato = Object.entries(stato.filtri).some(([k, v]) => v && !(k === 'type' && v === 'all'));
      listEl.innerHTML = `<p class="muted" data-trash-empty>${filtrato ? 'Nessun elemento corrisponde ai filtri.' : 'Il Cestino è vuoto.'}</p>`;
    }
    if (pagina && pagina.has_more) {
      pagerEl.innerHTML = '<button type="button" class="btn ghost" data-trash-more>Carica altri</button>';
      pagerEl.querySelector('[data-trash-more]').addEventListener('click', () => {
        stato.offset += PAGE_SIZE;
        carica({ reset: false });
      });
    }
  }

  form.addEventListener('submit', (event) => { if (event && event.preventDefault) event.preventDefault(); leggiFiltri(); carica(); });
  for (const nome of ['type', 'reason', 'date_from', 'date_to', 'mine']) {
    const el = campo(nome);
    if (el) el.addEventListener('change', () => { leggiFiltri(); carica(); });
  }
  if (agencyEl) agencyEl.addEventListener('change', () => { leggiFiltri(); carica(); });
  if (scopeEl) {
    for (const b of Array.from(scopeEl.querySelectorAll('[data-unified-scope-choice]'))) {
      b.addEventListener('click', async () => {
        stato.scope = b.dataset.unifiedScopeChoice;
        for (const x of Array.from(scopeEl.querySelectorAll('[data-unified-scope-choice]'))) {
          x.classList.toggle('active', x.dataset.unifiedScopeChoice === stato.scope);
        }
        if (stato.scope === 'platform') await caricaAgenzie();
        leggiFiltri();
        carica();
      });
    }
  }
  return async () => {
    if (stato.scope === 'platform') await caricaAgenzie();
    leggiFiltri();
    await carica();
  };
}

/** Le richieste di eliminazione degli edifici dell'agenzia (sola lettura). */
function richiesteEdificiAgenzia(host) {
  host.innerHTML = `
    <div class="trash-page-head">
      <p class="muted trash-intro">Le richieste di eliminazione degli edifici della tua agenzia. Una richiesta in attesa non è ancora un elemento eliminato: decide il Super Admin. Si chiede dalla scheda dell’edificio.</p>
      <select data-building-requests-status aria-label="Stato delle richieste">
        <option value="pending">In attesa</option><option value="all">Tutte</option>
      </select>
    </div>
    <div class="trash-cards" data-building-requests-list><p class="muted">Caricamento…</p></div>
    <div class="list-pager" data-building-requests-pager></div>`;
  const listEl = host.querySelector('[data-building-requests-list]');
  const pagerEl = host.querySelector('[data-building-requests-pager]');
  const statoEl = host.querySelector('[data-building-requests-status]');
  let offset = 0;
  async function carica({ reset = true } = {}) {
    if (reset) { offset = 0; listEl.innerHTML = '<p class="muted">Caricamento…</p>'; }
    pagerEl.innerHTML = '';
    let pagina;
    try {
      pagina = await listAgencyBuildingRequests(statoEl.value || 'pending', offset, PAGE_SIZE);
    } catch (error) {
      listEl.innerHTML = `<div class="error-box">${escapeHtml(unifiedErrorText(error))}</div>`;
      return;
    }
    const voci = (pagina && pagina.items) || [];
    if (reset) listEl.innerHTML = '';
    for (const r of voci) listEl.insertAdjacentHTML('beforeend', buildingRequestRowHtml(r));
    if (!listEl.querySelector('[data-building-request]')) {
      listEl.innerHTML = '<p class="muted" data-trash-empty>Nessuna richiesta.</p>';
    }
    if (pagina && pagina.has_more) {
      pagerEl.innerHTML = '<button type="button" class="btn ghost" data-trash-more>Carica altri</button>';
      pagerEl.querySelector('[data-trash-more]').addEventListener('click', () => { offset += PAGE_SIZE; carica({ reset: false }); });
    }
  }
  statoEl.addEventListener('change', () => carica());
  return carica;
}

/**
 * `platformAdmin`, `platformOnly` e `meId` arrivano da main.js (la sessione la
 * legge solo lui): servono a MOSTRARE la vista globale e il filtro «Eliminati
 * da me»; chi vede cosa lo decide sempre il backend.
 */
export async function renderCestino(container, { platformAdmin = false, platformOnly = false, meId = null } = {}) {
  const hash = window.location.hash || '';
  const iniziale = initialTrashTab(hash, { platformOnly });
  const contatti = iniziale === 'contatti';
  const edifici = iniziale === 'edifici';
  const richieste = iniziale === 'richieste';
  const nascosta = (nome) => (iniziale === nome ? '' : ' hidden');
  const tabTenant = (nome, label) => (platformOnly ? '' : `<button type="button" class="tab-btn${iniziale === nome ? ' active' : ''}" data-trash-tab="${nome}">${label}</button>`);
  container.innerHTML = `
    <div class="card panel trash-page">
      <div class="tabs trash-tabs" data-trash-tabs>
        <button type="button" class="tab-btn${iniziale === 'tutti' ? ' active' : ''}" data-trash-tab="tutti">Tutti</button>
        ${tabTenant('immobili', 'Immobili')}
        ${tabTenant('contatti', 'Contatti')}
        ${tabTenant('edifici', 'Edifici')}
        ${tabTenant('richieste', 'Richieste')}
        <button type="button" class="tab-btn${iniziale === 'richieste-edifici' ? ' active' : ''}" data-trash-tab="richieste-edifici">Richieste edifici</button>
      </div>
      <section class="trash-panel" data-trash-panel="tutti"${nascosta('tutti')}>
        <div class="trash-page-head">
          <p class="muted trash-intro">Tutto ciò che è stato eliminato nel CRM, in un posto solo: gli elementi nel Cestino non compaiono negli elenchi, nelle ricerche e nelle automazioni finché non vengono ripristinati. «Gestisci» apre la scheda della categoria con «Ripristina».</p>
          ${platformAdmin ? `<div class="chips trash-scope" data-unified-scope role="group" aria-label="Vista del Cestino">
              ${platformOnly ? '' : '<button type="button" class="chip active" data-unified-scope-choice="agency">Questa agenzia</button>'}
              <button type="button" class="chip${platformOnly ? ' active' : ''}" data-unified-scope-choice="platform">Tutte le agenzie (Super Admin)</button>
            </div>` : ''}
        </div>
        <form class="trash-filters" data-unified-filters role="search">
          <input type="search" name="q" maxlength="120" placeholder="Cerca per nome, indirizzo, codice, email, telefono…" aria-label="Cerca nel Cestino">
          <select name="type" aria-label="Categoria"><option value="all">Tutte le categorie</option>${TRASH_KINDS.map((k) => `<option value="${k.value}">${escapeHtml(k.label)}</option>`).join('')}</select>
          <select name="reason" aria-label="Motivo">${reasonOptionsHtml()}</select>
          <label class="trash-date">Dal <input type="date" name="date_from"></label>
          <label class="trash-date">Al <input type="date" name="date_to"></label>
          <label class="trash-mine"><input type="checkbox" name="mine"> Eliminati da me</label>
          ${platformAdmin ? `<select data-unified-agency aria-label="Agenzia"${platformOnly ? '' : ' hidden'}><option value="">Tutte le agenzie</option></select>` : ''}
          <button type="submit" class="btn primary">Cerca</button>
        </form>
        <div data-unified-counts></div>
        <div class="trash-cards" data-unified-list>${iniziale === 'tutti' ? '<p class="muted">Caricamento…</p>' : ''}</div>
        <div class="list-pager" data-unified-pager></div>
        <p class="muted trash-upcoming" data-trash-upcoming>In arrivo nel Cestino generale: ${escapeHtml(UPCOMING_KINDS.join(', '))}.</p>
      </section>
      <section class="trash-panel" data-trash-panel="immobili"${nascosta('immobili')}>
        <div class="trash-page-head">
          <p class="muted trash-intro">Immobili spostati nel Cestino: non compaiono nelle liste operative finché non li ripristini.</p>
          <a href="#/immobili" class="btn ghost" data-trash-back>← Immobili</a>
        </div>
        <div class="trash-cards" data-trash-list>${iniziale === 'immobili' ? '<p class="muted">Caricamento…</p>' : ''}</div>
        <div class="list-pager" data-trash-pager></div>
      </section>
      <section class="trash-panel" data-trash-panel="contatti"${contatti ? '' : ' hidden'}>
        <div class="trash-page-head">
          <p class="muted trash-intro">Contatti spostati nel Cestino: non compaiono in elenchi, ricerche e selettori finché non li ripristini. Il ripristino non riattiva automazioni né messaggi.</p>
          <a href="#/contatti" class="btn ghost" data-contact-trash-back>← Contatti</a>
        </div>
        <div data-contact-trash-notice></div>
        <div class="trash-cards" data-contact-trash-list>${contatti ? '<p class="muted">Caricamento…</p>' : ''}</div>
        <div class="list-pager" data-contact-trash-pager></div>
      </section>
      <section class="trash-panel" data-trash-panel="edifici"${edifici ? '' : ' hidden'}>
        <div class="trash-page-head">
          <p class="muted trash-intro">Edifici (palazzine) spostati nel Cestino con le loro unità: non compaiono nella lista Edifici né nella creazione guidata. Il ripristino e l’eliminazione definitiva li decide il Super Admin della piattaforma.</p>
          <a href="#/edifici" class="btn ghost" data-building-trash-back>← Edifici</a>
        </div>
        <div data-building-trash-notice></div>
        <div class="trash-cards" data-building-trash-list>${edifici ? '<p class="muted">Caricamento…</p>' : ''}</div>
        <div class="list-pager" data-building-trash-pager></div>
      </section>
      <section class="trash-panel" data-trash-panel="richieste"${richieste ? '' : ' hidden'}>
        <div class="trash-page-head">
          <p class="muted trash-intro">Richieste acquirente spostate nel Cestino: non compaiono in Acquirenti, negli abbinamenti né nei suggerimenti finché non le ripristini. Il ripristino non invia messaggi e non riapre nulla.</p>
          <a href="#/acquirenti" class="btn ghost" data-buy-trash-back>← Acquirenti</a>
        </div>
        <div data-buy-trash-notice></div>
        <div class="trash-cards" data-buy-trash-list>${richieste ? '<p class="muted">Caricamento…</p>' : ''}</div>
        <div class="list-pager" data-buy-trash-pager></div>
      </section>
      <section class="trash-panel" data-trash-panel="richieste-edifici"${nascosta('richieste-edifici')}>
        <div data-building-requests-host></div>
      </section>
    </div>
  `;
  const toastHost = container.parentElement || container;
  const pannelli = {};
  for (const nome of SCHEDE) pannelli[nome] = container.querySelector(`[data-trash-panel="${nome}"]`);
  const buyNoticeEl = container.querySelector('[data-buy-trash-notice]');
  const noticeEl = container.querySelector('[data-contact-trash-notice]');
  const caricaImmobili = listaCestino(container.querySelector('[data-trash-list]'),
    container.querySelector('[data-trash-pager]'), toastHost, {
      itemAttr: 'data-trash-item', restoreAttr: 'data-trash-restore', restoreKey: 'trashRestore', card: trashCardHtml,
      list: listTrash, restore: restoreProperty, toast: RESTORED_TOAST,
      errorHtml: (error) => conflittiHtml(restoreErrorView(error)),
      moreError: 'Impossibile caricare altri immobili.',
    });
  const caricaContatti = listaCestino(container.querySelector('[data-contact-trash-list]'),
    container.querySelector('[data-contact-trash-pager]'), toastHost, {
      itemAttr: 'data-contact-trash-item', restoreAttr: 'data-contact-restore', restoreKey: 'contactRestore', card: contactTrashCardHtml,
      list: listContactTrash, restore: restoreContact, toast: CONTACT_RESTORED_TOAST,
      errorHtml: (error) => escapeHtml(restoreErrorView(error).text),
      moreError: 'Impossibile caricare altri contatti.',
      afterRestore: (riga) => { noticeEl.innerHTML = duplicatesNoticeHtml(contactName(riga), duplicatesView(riga)); },
    });
  const caricaEdifici = gruppiEdifici(container.querySelector('[data-building-trash-list]'),
    container.querySelector('[data-building-trash-pager]'));
  const caricaRichieste = listaCestino(container.querySelector('[data-buy-trash-list]'),
    container.querySelector('[data-buy-trash-pager]'), toastHost, {
      itemAttr: 'data-buy-trash-item', restoreAttr: 'data-buy-restore', restoreKey: 'buyRestore',
      card: buyTrashCardHtml, list: listBuyRequestTrash, restore: restoreBuyRequest, toast: BUY_RESTORED_TOAST,
      errorHtml: buyRestoreErrorHtml,
      moreError: 'Impossibile caricare altre richieste.',
      afterRestore: (riga) => {
        buyNoticeEl.innerHTML = duplicatesNoticeHtml(buyRequestName(riga), buyDuplicatesView(riga), { kind: 'buy' });
      },
    });
  const hostRichiesteEdifici = container.querySelector('[data-building-requests-host]');
  const caricaRichiesteEdifici = platformAdmin
    ? () => renderEliminazioni(hostRichiesteEdifici)          // la gestione centralizzata, invariata
    : richiesteEdificiAgenzia(hostRichiesteEdifici);
  const caricati = Object.fromEntries(SCHEDE.map((n) => [n, false]));

  async function mostra(scheda) {
    for (const [nome, el] of Object.entries(pannelli)) el.hidden = nome !== scheda;
    for (const b of Array.from(container.querySelectorAll('[data-trash-tab]'))) {
      b.classList.toggle('active', b.dataset.trashTab === scheda);
    }
    if (caricati[scheda]) return;
    caricati[scheda] = true;
    const carica = { tutti: caricaTutti, immobili: caricaImmobili, contatti: caricaContatti, edifici: caricaEdifici,
                     richieste: caricaRichieste, 'richieste-edifici': caricaRichiesteEdifici }[scheda];
    await carica();
  }

  // il server senza il Cestino unico (404): lo si dice e si apre la scheda Immobili come prima
  const caricaTutti = cestinoUnico(pannelli.tutti, {
    platformOnly, meId,
    onMissing: () => {
      pannelli.tutti.querySelector('[data-unified-list]').innerHTML =
        '<p class="muted" data-unified-missing>La vista unificata non è ancora attiva su questo server: usa le schede di categoria.</p>';
      mostra('immobili');
    },
  });

  for (const b of Array.from(container.querySelectorAll('[data-trash-tab]'))) {
    b.addEventListener('click', () => { mostra(b.dataset.trashTab); });
  }
  await mostra(iniziale);
}
