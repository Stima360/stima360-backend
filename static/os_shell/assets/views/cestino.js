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
// Nessuna eliminazione definitiva: non esiste, in questa fase, da nessuna parte.
import { escapeHtml, formatDateTime } from '../components/st-table.js';
import { showToast } from '../census/census-sheets.js';
import { COMMERCIAL_STATUS_LABELS } from './incarichi.js';
import {
  listBuildingTrash, listBuyRequestTrash, listContactTrash, listTrash, restoreBuilding, restoreBuyRequest,
  restoreContact, restoreProperty,
} from '../trash/trash-api.js';
import {
  BUY_RESTORED_TOAST, BUY_STATUS_LABELS, buyDuplicatesView, buyRequestLine, buyRequestName, buyRestoreErrorView,
} from '../trash/trash-model.js';
import { buyBlockersHtml } from '../trash/trash-dialog.js';
import {
  BUILDING_RESTORED_TOAST, CONTACT_RESTORED_TOAST, CONTACT_STATUS_LABELS, RESTORED_TOAST, buildingDuplicatesView,
  buildingLine, buildingName, contactLine, contactName, duplicatesView, propertyLine, reasonLabel, restoreErrorView,
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

const BUILDING_TYPE_LABELS = {
  condominio: 'Condominio', villa: 'Villa', rustico: 'Rustico', capannone: 'Capannone',
  commerciale: 'Commerciale', misto: 'Misto', altro: 'Altro',
};

/** Una card del Cestino Edifici (esportata per le prove). */
export function buildingTrashCardHtml(item) {
  const chi = item.deleted_by_name ? ` · da ${item.deleted_by_name}` : '';
  const motivo = reasonLabel(item.deleted_reason) + (item.deleted_note ? ` — ${item.deleted_note}` : '');
  const id = Number(item.id);
  const dichiarate = item.units_declared === null || item.units_declared === undefined ? 'non note' : String(item.units_declared);
  return `
    <article class="trash-card" data-building-trash-item="${id}">
      <div class="trash-card-head">
        <strong class="trash-code">${escapeHtml(buildingName(item))}</strong>
        <span class="muted trash-type">${escapeHtml(BUILDING_TYPE_LABELS[item.building_type] || 'Edificio')}</span>
      </div>
      <div class="trash-line">${escapeHtml(buildingLine(item) || '—')}</div>
      <dl class="trash-facts">
        <dt>Eliminato</dt><dd data-trash-when>${escapeHtml(formatDateTime(item.deleted_at))}${escapeHtml(chi)}</dd>
        <dt>Motivo</dt><dd data-trash-reason>${escapeHtml(motivo)}</dd>
        <dt>Unità dichiarate</dt><dd data-trash-status>${escapeHtml(dichiarate)}</dd>
      </dl>
      <div class="field-error trash-card-error" data-trash-card-error role="alert"></div>
      <div class="trash-card-actions">
        <a class="btn ghost" href="#/edifici/${id}" data-building-trash-open>Apri scheda</a>
        <button type="button" class="btn primary" data-building-restore="${id}">Ripristina</button>
      </div>
    </article>`;
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

export async function renderCestino(container) {
  const hash = window.location.hash || '';
  // CESTINO-EDIFICI-1 / CESTINO-RICHIESTE-1: `#/cestino/contatti`,
  // `#/cestino/edifici` e `#/cestino/richieste` aprono la loro scheda
  const scheda = (hash.match(/^#\/cestino\/(contatti|edifici|richieste)\b/) || [])[1];
  const iniziale = scheda || 'immobili';
  const contatti = iniziale === 'contatti';
  const edifici = iniziale === 'edifici';
  const richieste = iniziale === 'richieste';
  container.innerHTML = `
    <div class="card panel trash-page">
      <div class="tabs trash-tabs" data-trash-tabs>
        <button type="button" class="tab-btn${iniziale === 'immobili' ? ' active' : ''}" data-trash-tab="immobili">Immobili</button>
        <button type="button" class="tab-btn${contatti ? ' active' : ''}" data-trash-tab="contatti">Contatti</button>
        <button type="button" class="tab-btn${edifici ? ' active' : ''}" data-trash-tab="edifici">Edifici</button>
        <button type="button" class="tab-btn${richieste ? ' active' : ''}" data-trash-tab="richieste">Richieste</button>
      </div>
      <section class="trash-panel" data-trash-panel="immobili"${iniziale === 'immobili' ? '' : ' hidden'}>
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
          <p class="muted trash-intro">Edifici (palazzine) spostati nel Cestino: vuoti, non compaiono nella lista Edifici né nella creazione guidata finché non li ripristini.</p>
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
    </div>
  `;
  const toastHost = container.parentElement || container;
  const pannelli = {
    immobili: container.querySelector('[data-trash-panel="immobili"]'),
    contatti: container.querySelector('[data-trash-panel="contatti"]'),
    edifici: container.querySelector('[data-trash-panel="edifici"]'),
    richieste: container.querySelector('[data-trash-panel="richieste"]'),
  };
  const buyNoticeEl = container.querySelector('[data-buy-trash-notice]');
  const buildingNoticeEl = container.querySelector('[data-building-trash-notice]');
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
  const caricaEdifici = listaCestino(container.querySelector('[data-building-trash-list]'),
    container.querySelector('[data-building-trash-pager]'), toastHost, {
      itemAttr: 'data-building-trash-item', restoreAttr: 'data-building-restore', restoreKey: 'buildingRestore',
      card: buildingTrashCardHtml, list: listBuildingTrash, restore: restoreBuilding, toast: BUILDING_RESTORED_TOAST,
      errorHtml: (error) => escapeHtml(restoreErrorView(error).text),
      moreError: 'Impossibile caricare altri edifici.',
      afterRestore: (riga) => {
        buildingNoticeEl.innerHTML = duplicatesNoticeHtml(buildingName(riga), buildingDuplicatesView(riga), { kind: 'building' });
      },
    });
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
  const caricati = { immobili: false, contatti: false, edifici: false, richieste: false };

  async function mostra(scheda) {
    for (const [nome, el] of Object.entries(pannelli)) el.hidden = nome !== scheda;
    for (const b of Array.from(container.querySelectorAll('[data-trash-tab]'))) {
      b.classList.toggle('active', b.dataset.trashTab === scheda);
    }
    if (caricati[scheda]) return;
    caricati[scheda] = true;
    const carica = { immobili: caricaImmobili, contatti: caricaContatti, edifici: caricaEdifici,
                     richieste: caricaRichieste }[scheda];
    await carica();
  }

  for (const b of Array.from(container.querySelectorAll('[data-trash-tab]'))) {
    b.addEventListener('click', () => { mostra(b.dataset.trashTab); });
  }
  await mostra(iniziale);
}
