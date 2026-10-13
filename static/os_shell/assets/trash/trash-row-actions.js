// STIMA360 OS — trash/trash-row-actions.js
// CESTINO-GENERALE-2 (CG-2): «Elimina…» direttamente dagli elenchi di
// Immobili, Contatti, Richieste acquirente ed Edifici.
//
// NESSUN motore nuovo: ogni riga apre lo STESSO foglio della scheda di
// dettaglio (trash-dialog.js: controllo, blocchi con i loro collegamenti,
// motivo, conferma) o, per un edificio, lo STESSO pannello della richiesta di
// eliminazione (building-deletion.js: la richiesta va al Super Admin
// designato, che la approva o la rifiuta; nulla va nel Cestino prima).
// Rotte, permessi e blocchi restano quelli del backend.
//
// Chi vede il comando lo decide la vista con la regola che usa gia' la scheda
// (`canDelete`): qui non c'e' nessuna regola di permesso.
import { escapeHtml } from '../components/st-table.js';
import { showToast } from '../census/census-sheets.js';
import { mountDeletionPanel } from '../building-deletion/building-deletion.js';
import { openBuyTrashDialog, openContactTrashDialog, openTrashDialog } from './trash-dialog.js';
import { BUY_TRASHED_TOAST, CONTACT_TRASHED_TOAST, TRASHED_TOAST } from './trash-model.js';

/** Le categorie servite dagli elenchi, con il foglio della scheda. */
const CATEGORIE = Object.freeze({
  property: { open: openTrashDialog, toast: TRASHED_TOAST, nome: 'immobile' },
  contact: { open: openContactTrashDialog, toast: CONTACT_TRASHED_TOAST, nome: 'contatto' },
  buy_request: { open: openBuyTrashDialog, toast: BUY_TRASHED_TOAST, nome: 'richiesta' },
  building: { open: null, toast: null, nome: 'edificio' },
});

export const ROW_DELETE_KINDS = Object.freeze(Object.keys(CATEGORIE));

/** Il comando di una riga (o di una card). */
export function rowDeleteButtonHtml(kind, id, etichetta = '') {
  if (!CATEGORIE[kind]) return '';
  const chi = etichetta ? ` ${etichetta}` : '';
  return `<button type="button" class="btn ghost trash-row-delete" data-row-delete="${escapeHtml(kind)}:${Number(id)}"
      aria-label="${escapeHtml(`Elimina ${CATEGORIE[kind].nome}${chi}`)}">Elimina…</button>`;
}

/** La colonna «Azioni» di un elenco a tabella (st-table): vuota dove il comando non serve. */
export function rowDeleteColumn(kind, canDelete = () => true) {
  return {
    label: 'Azioni',
    render: (riga) => (canDelete(riga) ? `<span class="trash-row-cell">${rowDeleteButtonHtml(kind, riga.id)}</span>` : ''),
  };
}

/** Il foglio condiviso da tutte le righe di un elenco. */
export function rowDeleteDialogHtml() {
  return '<dialog class="modal trash-sheet" data-row-delete-dialog></dialog>';
}

/** Il foglio della richiesta di eliminazione di un edificio: lo stesso pannello della scheda. */
export function buildingRequestSheetHtml(building) {
  const titolo = (building && (building.name || [building.address, building.civic_number].filter(Boolean).join(' '))) || '';
  return `
    <div class="trash-form trash-building-request" data-row-building-request>
      <h3 class="trash-title">Elimina edificio</h3>
      ${titolo ? `<p class="muted trash-subject">${escapeHtml(titolo)}</p>` : ''}
      <div data-row-building-panel><p class="muted" data-trash-loading>Verifica in corso…</p></div>
      <div class="modal-actions trash-actions">
        <a class="btn ghost" href="#/edifici/${Number(building && building.id)}" data-row-building-open>Apri la scheda</a>
        <button type="button" class="btn ghost" data-trash-close>Chiudi</button>
      </div>
    </div>`;
}

/**
 * Collega i comandi «Elimina…» di un elenco gia' disegnato.
 *   area      l'elemento che contiene le righe (e il foglio)
 *   rows      le righe dell'elenco, per ritrovare quella cliccata
 *   onDone    dopo uno spostamento nel Cestino: l'elenco si ricarica
 * Il clic sul comando non apre la scheda della riga.
 */
export function bindRowDeletes(area, { rows = [], onDone = null, dialog = null } = {}) {
  const foglio = dialog || area.querySelector('[data-row-delete-dialog]');
  const host = area.parentElement || area;
  for (const bottone of Array.from(area.querySelectorAll('[data-row-delete]'))) {
    bottone.addEventListener('click', async (evento) => {
      if (evento) {
        if (typeof evento.stopPropagation === 'function') evento.stopPropagation();
        if (typeof evento.preventDefault === 'function') evento.preventDefault();
      }
      const [kind, id] = String(bottone.dataset.rowDelete || '').split(':');
      const riga = rows.find((r) => String(r.id) === String(id));
      const cat = CATEGORIE[kind];
      if (!riga || !cat || !foglio) return;
      if (kind === 'building') {
        foglio.innerHTML = buildingRequestSheetHtml(riga);
        foglio.querySelector('[data-trash-close]').addEventListener('click', () => foglio.close());
        foglio.showModal();
        // stessa richiesta della scheda: si apre gia' sul motivo; nulla va nel Cestino senza il Super Admin
        await mountDeletionPanel(foglio.querySelector('[data-row-building-panel]'), riga.id, { initialStage: 'confirm' });
        return;
      }
      await cat.open(foglio, riga, {
        onTrashed: async () => {
          showToast(host.parentElement || host, { text: cat.toast });
          if (onDone) await onDone(riga);
        },
      });
    });
  }
}
