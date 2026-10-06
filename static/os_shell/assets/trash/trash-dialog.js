// STIMA360 OS — trash/trash-dialog.js
// DELETE-ARCH Fase 2B3: «Elimina…» dalla scheda immobile.
//
//   1. GET  /api/property/properties/{id}/deletion-check  (sola lettura)
//   2. can_trash = false -> i blocchi ESATTAMENTE come arrivano, conferma
//      disabilitata. Lo storico protetto (HISTORY_REQUIRES_ADMIN) ha la sua
//      frase: serve un amministratore. Nessun bypass.
//      can_trash = true  -> motivo (obbligatorio) + nota facoltativa.
//   3. POST /api/property/properties/{id}/trash; un rifiuto arrivato nel
//      frattempo (409 TRASH_BLOCKED, 403 HISTORY_REQUIRES_ADMIN) si mostra
//      nello stesso foglio e la conferma resta disabilitata.
//
// Il foglio non decide chi puo' eliminare, ne' quali blocchi valgono: lo
// dice il backend. Su smartphone e' un bottom-sheet (app.css, «trash-sheet»).
import { escapeHtml } from '../components/st-table.js';
import { showToast } from '../census/census-sheets.js';
import { deletionCheck, trashProperty } from './trash-api.js';
import {
  TRASHED_TOAST, TRASH_NOTE_MAX, TRASH_REASONS, blockerView, errorBlockers, propertyLine, trashErrorText,
} from './trash-model.js';

/**
 * Il bottone «Elimina…» della scheda immobile e il suo foglio. `visibile` e'
 * la stessa regola di «Archivia» (canManagePropertyLifecycle): serve solo a
 * non offrire un 403 certo. Chi puo' davvero, i blocchi e lo storico
 * protetto li decide il backend (deletion-check, poi trash).
 */
export function trashButtonHtml(visibile) {
  if (!visibile) return '';
  return '<button type="button" id="property-trash-btn" class="btn ghost trash-open">Elimina…</button>'
    + '<dialog id="property-trash-dialog" class="modal trash-sheet"></dialog>';
}

/**
 * Collega «Elimina…» (se c'e'). Dopo il 200 di POST .../trash: toast
 * «Immobile spostato nel Cestino» (ospitato FUORI dalla vista, cosi' resta
 * visibile dopo la navigazione) e `dopo()` - la scheda torna alla lista
 * Immobili, che rifa' la sua GET normale. Nessuna rilettura forzata.
 */
export function bindTrashButton(container, property, dopo) {
  const bottone = container.querySelector('#property-trash-btn');
  if (!bottone) return;
  bottone.addEventListener('click', () => openTrashDialog(container.querySelector('#property-trash-dialog'), property, {
    onTrashed: () => {
      showToast(container.parentElement || container, { text: TRASHED_TOAST });
      if (dopo) dopo();
    },
  }));
}

function blockersHtml(blockers) {
  const voci = blockerView(blockers);
  if (!voci.length) return '';
  return `<ul class="trash-blockers" data-trash-blockers>${voci.map((v) => `
      <li data-blocker="${escapeHtml(v.code)}"><span class="trash-blocker-label">${escapeHtml(v.label)}</span>${v.count && !v.link ? ` <span class="muted">(${v.count})</span>` : ''}${v.link ? ` <a class="trash-blocker-link" data-blocker-link href="${escapeHtml(v.link.href)}">${escapeHtml(v.link.label)}</a>` : ''}${v.history && v.history.length ? `
        <ul class="trash-history">${v.history.map((h) => `<li>${escapeHtml(h.label)}${h.count ? ` <span class="muted">(${h.count})</span>` : ''}</li>`).join('')}</ul>` : ''}</li>`).join('')}
    </ul>`;
}

/** Il corpo del foglio per ogni stato: 'loading' | 'blocked' | 'confirm' | 'error'. */
export function trashDialogHtml(property, state = {}) {
  const fase = state.phase || 'loading';
  let corpo = '<p class="muted" data-trash-loading>Verifica in corso…</p>';
  if (fase === 'blocked') {
    const soloStorico = (state.blockers || []).every((b) => b.code === 'HISTORY_REQUIRES_ADMIN');
    corpo = `${soloStorico ? '' : '<p class="trash-lead">Non si può spostare nel Cestino:</p>'}${blockersHtml(state.blockers)}`;
  } else if (fase === 'confirm') {
    corpo = `
      <fieldset class="trash-reasons" data-trash-reasons>
        <legend>Motivo</legend>
        ${TRASH_REASONS.map((r) => `<label class="trash-reason"><input type="radio" name="trash-reason" value="${r.value}"> ${escapeHtml(r.label)}</label>`).join('')}
      </fieldset>
      <div class="form-field">
        <label for="trash-note">Nota (facoltativa)</label>
        <textarea id="trash-note" class="input" data-trash-note rows="2" maxlength="${TRASH_NOTE_MAX}"></textarea>
      </div>
      <p class="muted trash-hint">L'immobile esce da tutte le liste operative. Potrai ripristinarlo dal Cestino.</p>`;
  } else if (fase === 'error') {
    corpo = blockersHtml(state.blockers);
  }
  const riga = [property && property.code, propertyLine(property)].filter(Boolean).join(' · ');
  return `
    <form class="trash-form" data-trash-form>
      <h3 class="trash-title">Elimina immobile</h3>
      <p class="muted trash-subject">${escapeHtml(riga)}</p>
      <div data-trash-body>${corpo}</div>
      <div class="field-error" data-trash-error role="alert">${escapeHtml(state.error || '')}</div>
      <div class="modal-actions trash-actions">
        <button type="button" class="btn ghost" data-trash-close>Chiudi</button>
        <button type="submit" class="btn danger" data-trash-confirm>Sposta nel Cestino</button>
      </div>
    </form>`;
}

/**
 * Apre il foglio «Elimina…» per `property`. `onTrashed(riga)` arriva solo dopo
 * il 200 di POST .../trash; prima, nulla cambia sullo schermo.
 */
export async function openTrashDialog(dialogEl, property, { onTrashed } = {}) {
  let fase = { phase: 'loading' };

  function disegna(stato) {
    fase = stato;
    dialogEl.innerHTML = trashDialogHtml(property, stato);
    const form = dialogEl.querySelector('[data-trash-form]');
    const conferma = dialogEl.querySelector('[data-trash-confirm]');
    // la conferma esiste sempre, ma si abilita SOLO con un motivo scelto e
    // solo quando il backend ha detto can_trash = true
    conferma.disabled = true;
    dialogEl.querySelector('[data-trash-close]').addEventListener('click', () => dialogEl.close());
    if (stato.phase === 'confirm') {
      for (const radio of Array.from(dialogEl.querySelectorAll('input[name="trash-reason"]'))) {
        radio.addEventListener('change', () => { conferma.disabled = !motivoScelto(); });
      }
    }
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      if (fase.phase !== 'confirm') return;
      const motivo = motivoScelto();
      if (!motivo) {
        dialogEl.querySelector('[data-trash-error]').textContent = 'Scegli un motivo.';
        return;
      }
      const nota = dialogEl.querySelector('[data-trash-note]');
      conferma.disabled = true;
      conferma.textContent = 'Spostamento…';
      try {
        const riga = await trashProperty(property.id, motivo, nota ? nota.value : '');
        dialogEl.close();
        if (onTrashed) await onTrashed(riga);
      } catch (error) {
        const blocchi = errorBlockers(error);
        if (blocchi.length) {
          // il backend ha cambiato idea fra il controllo e la conferma
          disegna({ phase: 'error', blockers: blocchi, error: trashErrorText(error) });
        } else {
          dialogEl.querySelector('[data-trash-error]').textContent = trashErrorText(error);
          conferma.textContent = 'Sposta nel Cestino';
          conferma.disabled = !motivoScelto();
        }
      }
    });
  }

  function motivoScelto() {
    const scelto = Array.from(dialogEl.querySelectorAll('input[name="trash-reason"]')).find((r) => r.checked);
    return scelto ? scelto.value : null;
  }

  disegna(fase);
  dialogEl.showModal();
  try {
    const esito = await deletionCheck(property.id);
    if (esito && esito.can_trash === true) disegna({ phase: 'confirm' });
    else disegna({ phase: 'blocked', blockers: (esito && esito.blockers) || [] });
  } catch (error) {
    disegna({ phase: 'error', blockers: errorBlockers(error), error: trashErrorText(error) });
  }
}
