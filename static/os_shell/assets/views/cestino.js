// STIMA360 OS — views/cestino.js
// DELETE-ARCH Fase 2B3: la pagina «Cestino», nata con i soli immobili.
// CESTINO-CONTATTI-1: due schede, «Immobili» (predefinita, invariata) e
// «Contatti» (caricata al primo tocco, o subito con `#/cestino/contatti`),
// con le stesse card, lo stesso «Ripristina» e lo stesso «Carica altri».
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
import { listContactTrash, listTrash, restoreContact, restoreProperty } from '../trash/trash-api.js';
import {
  CONTACT_RESTORED_TOAST, CONTACT_STATUS_LABELS, RESTORED_TOAST, contactLine, contactName, duplicatesView,
  propertyLine, reasonLabel, restoreErrorView,
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

/** L'avviso dopo un ripristino con possibili doppioni (esportato per le prove). */
export function duplicatesNoticeHtml(nome, doppioni) {
  if (!doppioni.length) return '';
  return `<div class="trash-notice" data-contact-duplicates role="status">
      <p><strong>${escapeHtml(nome)}</strong> è di nuovo fra i contatti. Possibili doppioni attivi, da controllare:</p>
      <ul>${doppioni.map((d) => `<li><a href="#/contatti/${d.id}">${escapeHtml(d.name)}</a>${d.reason ? ` — ${escapeHtml(d.reason)}` : ''}</li>`).join('')}</ul>
      <p class="muted">Nessun contatto è stato unito o modificato.</p>
    </div>`;
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
  const contatti = /^#\/cestino\/contatti\b/.test(window.location.hash || '');
  container.innerHTML = `
    <div class="card panel trash-page">
      <div class="tabs trash-tabs" data-trash-tabs>
        <button type="button" class="tab-btn${contatti ? '' : ' active'}" data-trash-tab="immobili">Immobili</button>
        <button type="button" class="tab-btn${contatti ? ' active' : ''}" data-trash-tab="contatti">Contatti</button>
      </div>
      <section class="trash-panel" data-trash-panel="immobili"${contatti ? ' hidden' : ''}>
        <div class="trash-page-head">
          <p class="muted trash-intro">Immobili spostati nel Cestino: non compaiono nelle liste operative finché non li ripristini.</p>
          <a href="#/immobili" class="btn ghost" data-trash-back>← Immobili</a>
        </div>
        <div class="trash-cards" data-trash-list>${contatti ? '' : '<p class="muted">Caricamento…</p>'}</div>
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
    </div>
  `;
  const toastHost = container.parentElement || container;
  const pannelli = {
    immobili: container.querySelector('[data-trash-panel="immobili"]'),
    contatti: container.querySelector('[data-trash-panel="contatti"]'),
  };
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
  const caricati = { immobili: false, contatti: false };

  async function mostra(scheda) {
    for (const [nome, el] of Object.entries(pannelli)) el.hidden = nome !== scheda;
    for (const b of Array.from(container.querySelectorAll('[data-trash-tab]'))) {
      b.classList.toggle('active', b.dataset.trashTab === scheda);
    }
    if (caricati[scheda]) return;
    caricati[scheda] = true;
    await (scheda === 'contatti' ? caricaContatti() : caricaImmobili());
  }

  for (const b of Array.from(container.querySelectorAll('[data-trash-tab]'))) {
    b.addEventListener('click', () => { mostra(b.dataset.trashTab); });
  }
  await mostra(contatti ? 'contatti' : 'immobili');
}
