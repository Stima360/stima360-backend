// STIMA360 OS — views/venditori.js (VENDITORI-1): la worklist #/venditori.
//
// Una riga = un'opportunita' Venditore: PERSONA + IMMOBILE (non un contatto
// con un ruolo). Tutto arriva da UNA chiamata, GET /api/crm/sellers
// (crm/sellers.py: ultima interazione, prossima azione, agente, punteggio
// seller intent e acquisizione calcolati dal server per la pagina), quindi
// 100 venditori non sono 100 richieste.
//
// Le azioni riusano il CRM esistente, mai una copia:
//   Registra interazione -> POST /api/property/properties/{id}/interactions
//                           (lead + contatto, context 'seller'): la STESSA
//                           riga nello storico immobile e nella scheda contatto
//   Richiamo             -> POST /api/core/tasks (lead + contatto + scadenza)
//   Fase                 -> PATCH /api/core/leads/{id} {stage}
//   Smetti               -> POST /api/crm/sellers/deactivate (non cancella)
//   Avvia acquisizione   -> il modulo Acquisizioni, precompilato con immobile,
//                           proprietario e lead (#/acquisizioni/nuova/...)

import { escapeHtml, renderBadge, formatDateTime } from '../components/st-table.js';
import { loadFormOptions } from '../components/property-form.js';
import * as api from '../sellers/sellers-api.js';
import {
  acquisitionHref, dueText, interactionLabel, propertyLine, sellerErrorMessage, stageLabel, telHref, worklistParams,
} from '../sellers/seller-model.js';

const PAGE_SIZE = 30;
const STAGE_TONES = { new: 'warn', contacted: 'role', qualified: 'role', appointment: 'ok', proposal: 'ok', won: 'ok', lost: 'gray' };
// Le fasce di seller_intent/scoring.py (BANDS), non reinventate.
const BAND_TONES = { molto_caldo: 'ok', caldo: 'ok', tiepido: 'warn', freddo: 'gray' };
const BAND_LABELS = { molto_caldo: 'Molto caldo', caldo: 'Caldo', tiepido: 'Tiepido', freddo: 'Freddo' };

export async function renderVenditori(container) {
  // Le opzioni (filtro agente) PRIMA di disegnare: lo scheletro e i suoi
  // riferimenti nascono insieme, senza un'attesa in mezzo.
  let opzioni = { can_assign: false, agents: [] };
  try { opzioni = await loadFormOptions(); } catch (_e) { /* filtro agente non disponibile */ }
  container.innerHTML = `
    <div class="card panel sellers-page">
      <div class="list-toolbar sellers-toolbar">
        <input id="sellers-search" class="input" type="search" placeholder="Cerca venditore, telefono, immobile…">
        <input id="sellers-city" class="input" type="text" placeholder="Comune">
        <select id="sellers-status" class="input"></select>
        <select id="sellers-agent" class="input" hidden></select>
      </div>
      <div id="sellers-views" class="sellers-views" role="tablist"></div>
      <div id="sellers-feedback"></div>
      <div id="sellers-list"><p class="muted">Caricamento…</p></div>
      <div id="sellers-pager" class="list-pager"></div>
    </div>
    <dialog id="sellers-dialog" class="modal seller-sheet"></dialog>`;
  const $ = (sel) => container.querySelector(sel);
  const dialogEl = $('#sellers-dialog');
  const filtri = { view: 'all', status: 'active', agentId: '', city: '', search: '' };
  let offset = 0;
  let cataloghi = null;
  let items = [];
  let debounce = null;

  if (opzioni.can_assign && (opzioni.agents || []).length) {
    const sel = $('#sellers-agent');
    sel.innerHTML = `<option value="">Tutti gli agenti</option><option value="0">Non assegnati</option>${opzioni.agents.map((a) => `<option value="${escapeHtml(String(a.id))}">${escapeHtml(a.label || a.name || `#${a.id}`)}</option>`).join('')}`;
    sel.hidden = false;
  }

  const feedback = (testo, tono = 'ok') => {
    $('#sellers-feedback').innerHTML = testo ? `<div class="${tono === 'ok' ? 'success-box' : 'error-box'}">${escapeHtml(testo)}</div>` : '';
  };

  function disegnaFiltri() {
    $('#sellers-views').innerHTML = cataloghi.views.map((v) => `<button type="button" class="chip${v.value === filtri.view ? ' active' : ''}" data-view="${escapeHtml(v.value)}" role="tab" aria-selected="${v.value === filtri.view}">${escapeHtml(v.label)}</button>`).join('');
    $('#sellers-views').querySelectorAll('[data-view]').forEach((b) => b.addEventListener('click', () => { filtri.view = b.dataset.view; offset = 0; carica(); }));
    const st = $('#sellers-status');
    if (!st.options || !st.innerHTML) st.innerHTML = cataloghi.statuses.map((s) => `<option value="${escapeHtml(s.value)}">${escapeHtml(s.label)}</option>`).join('');
    st.value = filtri.status;
  }

  async function carica() {
    $('#sellers-list').innerHTML = '<p class="muted">Caricamento…</p>';
    let dati;
    try {
      dati = await api.listSellers(worklistParams(filtri, offset, PAGE_SIZE));
    } catch (error) {
      $('#sellers-list').innerHTML = `<div class="error-box">${escapeHtml(sellerErrorMessage(error))}</div>`;
      return;
    }
    cataloghi = dati;
    items = dati.items || [];
    disegnaFiltri();
    $('#sellers-list').innerHTML = items.length
      ? `<div class="seller-cards">${items.map(card).join('')}</div>`
      : `<p class="muted">${filtri.view === 'all' && filtri.status === 'active' && !filtri.search && !filtri.city
        ? 'Nessun venditore attivo. Dalla scheda di un immobile, tab Proprietari, usa «Vende?» sul proprietario che vuole vendere.'
        : 'Nessun venditore con questi filtri.'}</p>`;
    $('#sellers-pager').innerHTML = (offset > 0 || dati.has_more) ? `
      <button class="btn" id="sellers-prev" ${offset === 0 ? 'disabled' : ''}>← Precedenti</button>
      <span class="muted">${items.length ? offset + 1 : 0}–${offset + items.length}</span>
      <button class="btn" id="sellers-next" ${dati.has_more ? '' : 'disabled'}>Successivi →</button>` : '';
    if ($('#sellers-prev')) $('#sellers-prev').addEventListener('click', () => { offset = Math.max(0, offset - PAGE_SIZE); carica(); });
    if ($('#sellers-next')) $('#sellers-next').addEventListener('click', () => { offset += PAGE_SIZE; carica(); });
    collega();
  }

  function card(it) {
    const tel = telHref(it.contact.phone);
    const ultima = it.last_interaction
      ? `${escapeHtml(interactionLabel(it.last_interaction.interaction_type))} · ${escapeHtml(formatDateTime(it.last_interaction.occurred_at))}${it.last_interaction.note ? ` — <span class="seller-note">${escapeHtml(it.last_interaction.note)}</span>` : ''}`
      : '<span class="muted">Nessuna interazione</span>';
    const prossima = it.next_action
      ? `${escapeHtml(it.next_action.title)} · <span class="${it.next_action.overdue ? 'seller-overdue' : ''}">${escapeHtml(dueText(it.next_action.due_at))}</span>`
      : '<span class="muted">Nessuna prossima azione</span>';
    const intent = it.intent ? renderBadge(`${BAND_LABELS[it.intent.band] || it.intent.band} ${it.intent.score}`, BAND_TONES[it.intent.band] || 'gray') : '';
    const acq = it.acquisition
      ? (it.acquisition.visible ? `<a class="btn ghost btn-small" href="#/acquisizioni/${escapeHtml(String(it.acquisition.id))}">Acquisizione aperta</a>` : renderBadge('Acquisizione in corso', 'role'))
      : '';
    const attiva = it.status === 'open';
    // REV 2 (R3): il contatto non e' piu' proprietario dell'immobile. Niente
    // azioni che il backend rifiuterebbe (interazione nel contesto seller,
    // acquisizione, riattivazione richiedono il collegamento); restano aprire
    // contatto e immobile, il richiamo e chiudere/sospendere. Il collegamento
    // non si ricrea da qui.
    const proprietario = it.still_owner !== false;
    const id = escapeHtml(String(it.lead_id));
    return `<article class="seller-card" data-lead-id="${id}">
      <header class="seller-card-head">
        <a class="seller-name" href="#/contatti/${escapeHtml(String(it.contact.id))}">${escapeHtml(it.contact.name || `Contatto #${it.contact.id}`)}</a>
        ${renderBadge(it.stage_label || stageLabel(it.stage), STAGE_TONES[it.stage] || 'gray')} ${intent}
        ${it.status === 'paused' ? renderBadge('Sospesa', 'warn') : ''}${it.status === 'closed' ? renderBadge(it.lost_reason || 'Chiusa', 'gray') : ''}
      </header>
      <div class="seller-line"><a href="#/immobili/${escapeHtml(String(it.property.id))}">${escapeHtml([it.property.code, propertyLine(it.property)].filter(Boolean).join(' · '))}</a>
        ${proprietario ? '' : ` ${renderBadge('Non più collegato come proprietario', 'warn')}`}</div>
      ${proprietario ? '' : `<div class="seller-line muted" data-not-owner="${id}">Per lavorare questa vendita ricollega ${escapeHtml(it.contact.name || 'il contatto')} come proprietario dalla <a href="#/immobili/${escapeHtml(String(it.property.id))}/proprietari">scheda immobile</a>, oppure chiudi l’opportunità.</div>`}
      <div class="seller-line muted">${it.contact.phone ? escapeHtml(it.contact.phone) : 'Nessun telefono'}${it.contact.email ? ` · ${escapeHtml(it.contact.email)}` : ''} · Agente: ${escapeHtml(it.agent_name || 'non assegnato')}</div>
      <dl class="seller-facts"><dt>Ultima</dt><dd>${ultima}</dd><dt>Prossima</dt><dd>${prossima}</dd></dl>
      ${acq ? `<div class="seller-line">${acq}</div>` : ''}
      <div class="seller-actions">
        ${tel ? `<a class="btn primary" href="${escapeHtml(tel)}" data-call="${id}">Chiama</a>` : ''}
        ${attiva ? `${proprietario ? `<button type="button" class="btn" data-log="${id}">Registra interazione</button>` : ''}
        <button type="button" class="btn ghost" data-followup="${id}">Richiamo</button>
        ${proprietario ? `<button type="button" class="btn ghost" data-stage="${id}">Fase</button>` : ''}
        ${it.acquisition || !proprietario ? '' : `<a class="btn ghost" href="${escapeHtml(acquisitionHref(it))}" data-acquire="${id}">Avvia acquisizione</a>`}
        <button type="button" class="btn ghost" data-stop="${id}">Smetti…</button>`
    : (proprietario ? `<button type="button" class="btn" data-resume="${id}">${it.status === 'paused' ? 'Riprendi' : 'Riattiva'}</button>`
      : (it.status === 'paused' ? `<button type="button" class="btn ghost" data-stop="${id}">Smetti…</button>` : ''))}
      </div>
    </article>`;
  }

  const trova = (leadId) => items.find((x) => String(x.lead_id) === String(leadId));

  function collega() {
    const lista = $('#sellers-list');
    lista.querySelectorAll('[data-log]').forEach((b) => b.addEventListener('click', () => apriInterazione(trova(b.dataset.log))));
    lista.querySelectorAll('[data-followup]').forEach((b) => b.addEventListener('click', () => apriRichiamo(trova(b.dataset.followup))));
    lista.querySelectorAll('[data-stage]').forEach((b) => b.addEventListener('click', () => apriFase(trova(b.dataset.stage))));
    lista.querySelectorAll('[data-stop]').forEach((b) => b.addEventListener('click', () => apriSmetti(trova(b.dataset.stop))));
    lista.querySelectorAll('[data-resume]').forEach((b) => b.addEventListener('click', async () => {
      const it = trova(b.dataset.resume);
      b.disabled = true;
      try {
        await api.activateSeller(it.property.id, it.contact.id);
        feedback(`${it.contact.name}: vendita riattivata.`);
        await carica();
      } catch (error) {
        b.disabled = false;
        feedback(sellerErrorMessage(error), 'error');
      }
    }));
  }

  function apri(html, onSubmit) {
    dialogEl.innerHTML = html;
    if (!dialogEl.open) dialogEl.showModal();
    dialogEl.querySelector('[data-cancel]').addEventListener('click', () => dialogEl.close());
    const form = dialogEl.querySelector('form');
    form.addEventListener('submit', async (evento) => {
      evento.preventDefault();
      const bottone = dialogEl.querySelector('[data-submit]');
      const errore = dialogEl.querySelector('[data-error]');
      errore.textContent = '';
      bottone.disabled = true;
      try {
        const testo = await onSubmit();
        dialogEl.close();
        feedback(testo);
        await carica();
      } catch (error) {
        bottone.disabled = false;
        errore.textContent = error.userMessage || sellerErrorMessage(error);
      }
    });
  }

  const azioni = (submit) => `<div class="field-error" data-error role="alert"></div>
    <div class="modal-actions"><button type="button" class="btn ghost" data-cancel>Annulla</button>
    <button type="submit" class="btn primary" data-submit>${submit}</button></div>`;

  function apriInterazione(it) {
    const tipi = [['call', 'Telefonata'], ['meeting', 'Incontro'], ['whatsapp', 'WhatsApp'], ['email', 'Email'], ['note', 'Nota']];
    apri(`<form novalidate data-log-form>
        <h3 class="section-title">${escapeHtml(it.contact.name)} · ${escapeHtml(propertyLine(it.property))}</h3>
        <div class="census-chips census-chips-wrap">${tipi.map(([v, l], i) => `<button type="button" class="chip${i === 0 ? ' active' : ''}" data-type="${v}">${l}</button>`).join('')}</div>
        <div class="form-field"><label for="seller-note">Cosa vi siete detti</label><textarea id="seller-note" class="input" rows="3" maxlength="5000" placeholder="Es. Chiamato Mario, vuole 220.000 €, lo richiamo venerdì"></textarea></div>
        <div class="form-field"><label for="seller-recall">Richiamo il (facoltativo)</label><input id="seller-recall" class="input" type="datetime-local"></div>
        <p class="muted">Finisce nello storico dell’immobile e nella scheda del contatto: una sola registrazione.</p>
        ${azioni('Registra')}</form>`, async () => {
      const tipo = dialogEl.querySelector('[data-type].active').dataset.type;
      const nota = dialogEl.querySelector('#seller-note').value.trim();
      if (!nota) { const e = new Error('Scrivi cosa vi siete detti.'); e.userMessage = e.message; throw e; }
      await api.logInteraction(it.property.id, { type: tipo, note: nota, contactId: it.contact.id, leadId: it.lead_id });
      const quando = dialogEl.querySelector('#seller-recall').value;
      if (quando) {
        await api.createFollowUp({ leadId: it.lead_id, contactId: it.contact.id, title: `Richiamare ${it.contact.name}`, dueAt: new Date(quando).toISOString() });
        return 'Interazione registrata e richiamo fissato.';
      }
      return 'Interazione registrata.';
    });
    dialogEl.querySelectorAll('[data-type]').forEach((b) => b.addEventListener('click', () => {
      dialogEl.querySelectorAll('[data-type]').forEach((x) => x.classList.toggle('active', x === b));
    }));
  }

  function apriRichiamo(it) {
    apri(`<form novalidate data-followup-form>
        <h3 class="section-title">Prossima azione · ${escapeHtml(it.contact.name)}</h3>
        <div class="form-field"><label for="seller-task-title">Cosa fare</label><input id="seller-task-title" class="input" maxlength="200" value="${escapeHtml(`Richiamare ${it.contact.name}`)}"></div>
        <div class="form-field"><label for="seller-task-due">Quando</label><input id="seller-task-due" class="input" type="datetime-local"></div>
        ${azioni('Salva')}</form>`, async () => {
      const titolo = dialogEl.querySelector('#seller-task-title').value.trim();
      const quando = dialogEl.querySelector('#seller-task-due').value;
      if (!titolo || !quando) { const e = new Error('Indica cosa fare e quando.'); e.userMessage = e.message; throw e; }
      await api.createFollowUp({ leadId: it.lead_id, contactId: it.contact.id, title: titolo, dueAt: new Date(quando).toISOString() });
      return 'Prossima azione salvata.';
    });
  }

  function apriFase(it) {
    const fasi = (cataloghi.stages || []).filter((s) => s.value !== 'lost' && s.value !== 'won');
    apri(`<form novalidate data-stage-form>
        <h3 class="section-title">Fase · ${escapeHtml(it.contact.name)}</h3>
        <div class="form-field"><label for="seller-stage">Fase della vendita</label><select id="seller-stage" class="input">${fasi.map((s) => `<option value="${escapeHtml(s.value)}" ${s.value === it.stage ? 'selected' : ''}>${escapeHtml(s.label)}</option>`).join('')}</select></div>
        ${azioni('Salva')}</form>`, async () => {
      await api.setStage(it.lead_id, dialogEl.querySelector('#seller-stage').value);
      return 'Fase aggiornata.';
    });
  }

  function apriSmetti(it) {
    apri(`<form novalidate data-stop-form>
        <h3 class="section-title">${escapeHtml(it.contact.name)} non vende più?</h3>
        <p class="muted">Lo storico resta: cambia solo lo stato dell’opportunità.</p>
        ${(cataloghi.outcomes || []).map((o, i) => `<label class="seller-choice"><input type="radio" name="seller-outcome" value="${escapeHtml(o.value)}" ${i === 0 ? 'checked' : ''}><span>${escapeHtml(o.label)}</span></label>`).join('')}
        <div class="form-field"><label for="seller-stop-note">Nota (facoltativa)</label><input id="seller-stop-note" class="input" maxlength="500"></div>
        ${azioni('Conferma')}</form>`, async () => {
      const radio = [...dialogEl.querySelectorAll('input[name="seller-outcome"]')].find((r) => r.checked);
      await api.deactivateSeller(it.property.id, it.contact.id, radio ? radio.value : 'paused', dialogEl.querySelector('#seller-stop-note').value);
      return 'Stato aggiornato: lo storico resta.';
    });
  }

  const ricarica = () => { offset = 0; carica(); };
  for (const [sel, chiave] of [['#sellers-search', 'search'], ['#sellers-city', 'city']]) {
    $(sel).addEventListener('input', (e) => { filtri[chiave] = e.target.value; clearTimeout(debounce); debounce = setTimeout(ricarica, 300); });
  }
  $('#sellers-status').addEventListener('change', (e) => { filtri.status = e.target.value; ricarica(); });
  $('#sellers-agent').addEventListener('change', (e) => { filtri.agentId = e.target.value; ricarica(); });
  await carica();
}
