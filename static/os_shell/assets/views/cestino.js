// STIMA360 OS — views/cestino.js
// DELETE-ARCH Fase 2B3: la pagina «Cestino». In questa fase contiene SOLO
// immobili (contatti, palazzine e richieste d'acquisto sono fuori scope).
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
import { listTrash, restoreProperty } from '../trash/trash-api.js';
import { RESTORED_TOAST, propertyLine, reasonLabel, restoreErrorView } from '../trash/trash-model.js';

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

export async function renderCestino(container) {
  container.innerHTML = `
    <div class="card panel trash-page">
      <div class="trash-page-head">
        <p class="muted trash-intro">Immobili spostati nel Cestino: non compaiono nelle liste operative finché non li ripristini.</p>
        <a href="#/immobili" class="btn ghost" data-trash-back>← Immobili</a>
      </div>
      <div class="trash-cards" data-trash-list><p class="muted">Caricamento…</p></div>
      <div class="list-pager" data-trash-pager></div>
    </div>
  `;
  const listEl = container.querySelector('[data-trash-list]');
  const pagerEl = container.querySelector('[data-trash-pager]');
  const toastHost = container.parentElement || container;
  let offset = 0;

  function vuoto() {
    if (!listEl.querySelector('[data-trash-item]')) {
      listEl.innerHTML = '<p class="muted" data-trash-empty>Il Cestino è vuoto.</p>';
    }
  }

  function collega(card) {
    const bottone = card.querySelector('[data-trash-restore]');
    const erroreEl = card.querySelector('[data-trash-card-error]');
    bottone.addEventListener('click', async () => {
      const id = Number(bottone.dataset.trashRestore);
      bottone.disabled = true;
      bottone.textContent = 'Ripristino…';
      erroreEl.innerHTML = '';
      try {
        await restoreProperty(id);
        card.remove();
        showToast(toastHost, { text: RESTORED_TOAST });
        vuoto();
      } catch (error) {
        // la card resta com'era: nessuna modifica locale finta
        erroreEl.innerHTML = conflittiHtml(restoreErrorView(error));
        bottone.textContent = 'Ripristina';
        bottone.disabled = false;
      }
    });
  }

  async function carica() {
    pagerEl.innerHTML = '';
    let pagina;
    try {
      pagina = await listTrash(offset, PAGE_SIZE);
    } catch (error) {
      if (offset === 0) listEl.innerHTML = `<div class="error-box">${escapeHtml(error.message || 'Impossibile caricare il Cestino.')}</div>`;
      else pagerEl.innerHTML = `<div class="field-error">${escapeHtml(error.message || 'Impossibile caricare altri immobili.')}</div>`;
      return;
    }
    const voci = (pagina && pagina.items) || [];
    if (offset === 0) listEl.innerHTML = '';
    for (const item of voci) {
      listEl.insertAdjacentHTML('beforeend', trashCardHtml(item));
      const card = listEl.querySelector(`[data-trash-item="${Number(item.id)}"]`);
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

  await carica();
}
