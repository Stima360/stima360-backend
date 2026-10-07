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
//
// CESTINO-CONTATTI-1: lo STESSO foglio per i contatti (scheda contatto),
// con le stesse fasi; cambiano le rotte (core/router.py), i testi e il
// disegno dei blocchi, che per un contatto elencano i record che bloccano con
// il loro collegamento e, per l'accesso al portale proprietario, l'azione
// esistente «Disattiva accesso» (poi il controllo si ripete). Nessun blocco
// si scavalca da qui.
import { escapeHtml } from '../components/st-table.js';
import { showToast } from '../census/census-sheets.js';
import {
  buildingDeletionCheck, buyRequestDeletionCheck, contactDeletionCheck, deletionCheck, disableOwnerAccount,
  trashBuilding, trashBuyRequest, trashContact, trashProperty,
} from './trash-api.js';
import {
  BUILDING_TRASHED_TOAST, BUY_TRASHED_TOAST, CONTACT_TRASHED_TOAST, TRASHED_TOAST, TRASH_NOTE_MAX, TRASH_REASONS,
  blockerView, buildingLine, buildingName, buildingTrashErrorText, buyBlockerView, buyEffectsText, buyRequestLine,
  buyRequestName, buyTrashErrorText, contactBlockerView, contactEffectsText, contactLine, contactName,
  contactTrashErrorText, errorBlockers, propertyLine, trashErrorText,
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
    corpo = `${motiviHtml()}
      <p class="muted trash-hint">L'immobile esce da tutte le liste operative. Potrai ripristinarlo dal Cestino.</p>`;
  } else if (fase === 'error') {
    corpo = blockersHtml(state.blockers);
  }
  const riga = [property && property.code, propertyLine(property)].filter(Boolean).join(' · ');
  return formHtml('Elimina immobile', riga, corpo, state.error);
}

/** Motivo (obbligatorio) e nota (facoltativa): gli stessi per ogni foglio. */
function motiviHtml() {
  return `
      <fieldset class="trash-reasons" data-trash-reasons>
        <legend>Motivo</legend>
        ${TRASH_REASONS.map((r) => `<label class="trash-reason"><input type="radio" name="trash-reason" value="${r.value}"> ${escapeHtml(r.label)}</label>`).join('')}
      </fieldset>
      <div class="form-field">
        <label for="trash-note">Nota (facoltativa)</label>
        <textarea id="trash-note" class="input" data-trash-note rows="2" maxlength="${TRASH_NOTE_MAX}"></textarea>
      </div>`;
}

function formHtml(titolo, riga, corpo, errore) {
  return `
    <form class="trash-form" data-trash-form>
      <h3 class="trash-title">${escapeHtml(titolo)}</h3>
      <p class="muted trash-subject">${escapeHtml(riga)}</p>
      <div data-trash-body>${corpo}</div>
      <div class="field-error" data-trash-error role="alert">${escapeHtml(errore || '')}</div>
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
  return apriFoglio(dialogEl, {
    html: (stato) => trashDialogHtml(property, stato),
    check: () => deletionCheck(property.id),
    trash: (motivo, nota) => trashProperty(property.id, motivo, nota),
    errorText: trashErrorText,
    onTrashed,
  });
}

/**
 * Il comportamento comune dei fogli «Elimina…»: controllo, blocchi o
 * conferma, spostamento. `cfg.onAction(bottone, ridisegna)` riceve i clic
 * sulle azioni che un blocco propone (solo per i contatti).
 */
async function apriFoglio(dialogEl, cfg) {
  let fase = { phase: 'loading' };

  function disegna(stato) {
    fase = stato;
    dialogEl.innerHTML = cfg.html(stato);
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
    if (cfg.onAction) {
      for (const bottone of Array.from(dialogEl.querySelectorAll('[data-trash-action]'))) {
        bottone.addEventListener('click', () => cfg.onAction(bottone, controlla));
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
        const riga = await cfg.trash(motivo, nota ? nota.value : '');
        dialogEl.close();
        if (cfg.onTrashed) await cfg.onTrashed(riga);
      } catch (error) {
        const blocchi = errorBlockers(error);
        if (blocchi.length) {
          // il backend ha cambiato idea fra il controllo e la conferma
          disegna({ phase: 'error', blockers: blocchi, error: cfg.errorText(error) });
        } else {
          dialogEl.querySelector('[data-trash-error]').textContent = cfg.errorText(error);
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

  async function controlla() {
    try {
      const esito = await cfg.check();
      if (esito && esito.can_trash === true) disegna({ phase: 'confirm', check: esito });
      else disegna({ phase: 'blocked', blockers: (esito && esito.blockers) || [] });
    } catch (error) {
      disegna({ phase: 'error', blockers: errorBlockers(error), error: cfg.errorText(error) });
    }
  }

  disegna(fase);
  dialogEl.showModal();
  await controlla();
}

// ---------------------------------------------------------------------------
// CESTINO-CONTATTI-1: «Elimina…» dalla scheda contatto
// ---------------------------------------------------------------------------

/** Il bottone «Elimina…» della scheda contatto e il suo foglio. Chi puo'
 *  davvero, i blocchi e lo storico li decide il backend: un contatto che la
 *  scheda mostra e' gia' nello scope di chi guarda. */
export function contactTrashButtonHtml() {
  return '<button type="button" id="contact-trash-btn" class="btn ghost trash-open">Elimina…</button>'
    + '<dialog id="contact-trash-dialog" class="modal trash-sheet"></dialog>';
}

/** Collega «Elimina…» della scheda contatto. Dopo il 200: toast e `dopo()`. */
export function bindContactTrashButton(container, contact, dopo) {
  const bottone = container.querySelector('#contact-trash-btn');
  if (!bottone) return;
  bottone.addEventListener('click', () => openContactTrashDialog(container.querySelector('#contact-trash-dialog'), contact, {
    onTrashed: () => {
      showToast(container.parentElement || container, { text: CONTACT_TRASHED_TOAST });
      if (dopo) dopo();
    },
  }));
}

function contactBlockersHtml(blockers, vista = contactBlockerView) {
  const voci = vista(blockers);
  if (!voci.length) return '';
  return `<ul class="trash-blockers" data-trash-blockers>${voci.map((v) => `
      <li data-blocker="${escapeHtml(v.code)}"><span class="trash-blocker-label">${escapeHtml(v.label)}</span>${v.link ? ` <a class="trash-blocker-link" data-blocker-link href="${escapeHtml(v.link.href)}">${escapeHtml(v.link.label)}</a>` : ''}${v.items.length ? `
        <ul class="trash-blocker-items" data-blocker-items>${v.items.map((i) => `<li>${i.link ? `<a href="${escapeHtml(i.link.href)}" data-blocker-item-link>${escapeHtml(i.label)}</a>` : escapeHtml(i.label)}</li>`).join('')}</ul>` : ''}${v.action && v.action.allowed ? `
        <div class="trash-blocker-action"><button type="button" class="btn ghost" data-trash-action="${escapeHtml(v.action.kind)}" data-account-id="${Number(v.action.accountId)}">${escapeHtml(v.action.label)}</button></div>` : ''}${v.history && v.history.length ? `
        <ul class="trash-history">${v.history.map((h) => `<li>${escapeHtml(h.label)}${h.count ? ` <span class="muted">(${h.count})</span>` : ''}</li>`).join('')}</ul>` : ''}</li>`).join('')}
    </ul>`;
}

/** Il corpo del foglio contatto per ogni stato: 'loading' | 'blocked' | 'confirm' | 'error'. */
export function contactTrashDialogHtml(contact, state = {}) {
  const fase = state.phase || 'loading';
  let corpo = '<p class="muted" data-trash-loading>Verifica in corso…</p>';
  if (fase === 'blocked') {
    const soloStorico = (state.blockers || []).every((b) => b.code === 'HISTORY_REQUIRES_ADMIN');
    corpo = `${soloStorico ? '' : '<p class="trash-lead">Non si può spostare nel Cestino:</p>'}${contactBlockersHtml(state.blockers)}`;
  } else if (fase === 'confirm') {
    const esito = state.check || {};
    const effetti = contactEffectsText(esito.effects);
    const storia = Array.isArray(esito.history) ? esito.history : [];
    corpo = `${motiviHtml()}
      ${storia.length ? `<div class="trash-kept" data-trash-history-kept><p class="muted">Lo storico resta consultabile dalla scheda:</p>
        <ul class="trash-history">${storia.map((h) => `<li>${escapeHtml(h.label || h.code)}${h.count ? ` <span class="muted">(${h.count})</span>` : ''}</li>`).join('')}</ul></div>` : ''}
      ${effetti.length ? `<ul class="trash-effects" data-trash-effects>${effetti.map((f) => `<li>${escapeHtml(f)}</li>`).join('')}</ul>` : ''}
      <p class="muted trash-hint">Il contatto esce da elenchi, ricerche e selettori. Potrai ripristinarlo dal Cestino; le automazioni restano sospese finché non le riattivi tu.</p>`;
  } else if (fase === 'error') {
    corpo = contactBlockersHtml(state.blockers);
  }
  const riga = [contactName(contact), contactLine(contact)].filter(Boolean).join(' · ');
  return formHtml('Elimina contatto', riga, corpo, state.error);
}

/** Apre il foglio «Elimina…» per `contact` (stesso comportamento degli immobili). */
export async function openContactTrashDialog(dialogEl, contact, { onTrashed } = {}) {
  return apriFoglio(dialogEl, {
    html: (stato) => contactTrashDialogHtml(contact, stato),
    check: () => contactDeletionCheck(contact.id),
    trash: (motivo, nota) => trashContact(contact.id, motivo, nota),
    errorText: contactTrashErrorText,
    onTrashed,
    // «Disattiva accesso» (portale proprietario): la rotta esistente, poi il
    // controllo si ripete e mostra cio' che il backend dice adesso.
    onAction: async (bottone, ricontrolla) => {
      if (bottone.dataset.trashAction !== 'owner_account_disable') return;
      bottone.disabled = true;
      bottone.textContent = 'Disattivazione…';
      try {
        await disableOwnerAccount(bottone.dataset.accountId);
        await ricontrolla();
      } catch (error) {
        bottone.disabled = false;
        bottone.textContent = 'Disattiva accesso';
        const errore = dialogEl.querySelector('[data-trash-error]');
        if (errore) errore.textContent = error.message || 'Disattivazione non riuscita.';
      }
    },
  });
}

// ---------------------------------------------------------------------------
// CESTINO-EDIFICI-1: «Elimina…» dalla scheda edificio (palazzina contenitore)
// ---------------------------------------------------------------------------

/** Il bottone «Elimina…» della scheda edificio e il suo foglio. Chi puo' e
 *  i blocchi (unita' collegate di ogni tipo) li decide il backend. */
export function buildingTrashButtonHtml() {
  return '<button type="button" id="building-trash-btn" class="btn ghost trash-open">Elimina…</button>'
    + '<dialog id="building-trash-dialog" class="modal trash-sheet"></dialog>';
}

/** Collega «Elimina…» della scheda edificio. Dopo il 200: toast e `dopo()`. */
export function bindBuildingTrashButton(container, building, dopo) {
  const bottone = container.querySelector('#building-trash-btn');
  if (!bottone) return;
  bottone.addEventListener('click', () => openBuildingTrashDialog(container.querySelector('#building-trash-dialog'), building, {
    onTrashed: () => {
      showToast(container.parentElement || container, { text: BUILDING_TRASHED_TOAST });
      if (dopo) dopo();
    },
  }));
}

/** Il corpo del foglio edificio per ogni stato: 'loading' | 'blocked' | 'confirm' | 'error'. */
export function buildingTrashDialogHtml(building, state = {}) {
  const fase = state.phase || 'loading';
  let corpo = '<p class="muted" data-trash-loading>Verifica in corso…</p>';
  if (fase === 'blocked') {
    corpo = `<p class="trash-lead">Non si può spostare nel Cestino:</p>${contactBlockersHtml(state.blockers)}`;
  } else if (fase === 'confirm') {
    corpo = `${motiviHtml()}
      <p class="muted trash-hint">L'edificio è vuoto: esce dalla lista Edifici e dalla creazione guidata. Potrai ripristinarlo dal Cestino, con i suoi dati.</p>`;
  } else if (fase === 'error') {
    corpo = contactBlockersHtml(state.blockers);
  }
  const riga = [buildingName(building), buildingLine(building)].filter((x, i, a) => x && a.indexOf(x) === i).join(' · ');
  return formHtml('Elimina edificio', riga, corpo, state.error);
}

/** Apre il foglio «Elimina…» per `building` (stesso comportamento degli altri). */
export async function openBuildingTrashDialog(dialogEl, building, { onTrashed } = {}) {
  return apriFoglio(dialogEl, {
    html: (stato) => buildingTrashDialogHtml(building, stato),
    check: () => buildingDeletionCheck(building.id),
    trash: (motivo, nota) => trashBuilding(building.id, motivo, nota),
    errorText: buildingTrashErrorText,
    onTrashed,
  });
}

// ---------------------------------------------------------------------------
// CESTINO-RICHIESTE-1: «Elimina…» dalla scheda richiesta acquirente
// ---------------------------------------------------------------------------

/** Il bottone «Elimina…» della scheda richiesta e il suo foglio. Blocchi,
 *  storico ed effetti li decide il backend. */
export function buyTrashButtonHtml() {
  return '<button type="button" id="buy-trash-btn" class="btn ghost trash-open">Elimina…</button>'
    + '<dialog id="buy-trash-dialog" class="modal trash-sheet"></dialog>';
}

/** Collega «Elimina…» della scheda richiesta. Dopo il 200: toast e `dopo()`. */
export function bindBuyTrashButton(container, request, dopo) {
  const bottone = container.querySelector('#buy-trash-btn');
  if (!bottone) return;
  bottone.addEventListener('click', () => openBuyTrashDialog(container.querySelector('#buy-trash-dialog'), request, {
    onTrashed: () => {
      showToast(container.parentElement || container, { text: BUY_TRASHED_TOAST });
      if (dopo) dopo();
    },
  }));
}

/** I blocchi di una richiesta, con le loro voci e i collegamenti (esportato per la scheda). */
export function buyBlockersHtml(blockers) {
  return contactBlockersHtml(blockers, buyBlockerView);
}

/** Il corpo del foglio richiesta per ogni stato: 'loading' | 'blocked' | 'confirm' | 'error'. */
export function buyTrashDialogHtml(request, state = {}) {
  const fase = state.phase || 'loading';
  let corpo = '<p class="muted" data-trash-loading>Verifica in corso…</p>';
  if (fase === 'blocked') {
    const soloStorico = (state.blockers || []).every((b) => b.code === 'HISTORY_REQUIRES_ADMIN');
    corpo = `${soloStorico ? '' : '<p class="trash-lead">Non si può spostare nel Cestino:</p>'}${buyBlockersHtml(state.blockers)}`;
  } else if (fase === 'confirm') {
    const esito = state.check || {};
    const effetti = buyEffectsText(esito.effects);
    const storia = Array.isArray(esito.history) ? esito.history : [];
    corpo = `${motiviHtml()}
      ${storia.length ? `<div class="trash-kept" data-trash-history-kept><p class="muted">Lo storico resta consultabile dalla scheda:</p>
        <ul class="trash-history">${storia.map((h) => `<li>${escapeHtml(h.label || h.code)}${h.count ? ` <span class="muted">(${h.count})</span>` : ''}</li>`).join('')}</ul></div>` : ''}
      ${effetti.length ? `<ul class="trash-effects" data-trash-effects>${effetti.map((f) => `<li>${escapeHtml(f)}</li>`).join('')}</ul>` : ''}
      <p class="muted trash-hint">La richiesta esce da Acquirenti, ricerche, selettori, abbinamenti e suggerimenti. Potrai ripristinarla dal Cestino: il ripristino non invia messaggi e non riapre nulla.</p>`;
  } else if (fase === 'error') {
    corpo = buyBlockersHtml(state.blockers);
  }
  const riga = [buyRequestName(request), buyRequestLine(request)].filter(Boolean).join(' · ');
  return formHtml('Elimina richiesta', riga, corpo, state.error);
}

/** Apre il foglio «Elimina…» per `request` (stesso comportamento degli altri). */
export async function openBuyTrashDialog(dialogEl, request, { onTrashed } = {}) {
  return apriFoglio(dialogEl, {
    html: (stato) => buyTrashDialogHtml(request, stato),
    check: () => buyRequestDeletionCheck(request.id),
    trash: (motivo, nota) => trashBuyRequest(request.id, motivo, nota),
    errorText: buyTrashErrorText,
    onTrashed,
  });
}
