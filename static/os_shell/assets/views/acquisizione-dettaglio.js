// STIMA360 OS — acquisizione-dettaglio.js
// CRM-OPS-3: la scheda di un'acquisizione (#/acquisizioni/{id}).
//
// Tutto viene da GET /api/acquisitions/{id}: immobile, proprietari REALI
// (`property_contacts`, il principale marcato), appuntamento (riletto
// dall'Agenda, che resta la sola fonte di data, ora, durata, agente e stato),
// dati commerciali, storico e `allowed_actions` decise dal server. Le
// etichette vengono da GET /api/acquisitions/options: nessuna copia qui.
//
// Scritture, ognuna una POST/PATCH del backend con `version`:
//   PATCH /api/acquisitions/{id}               dati commerciali, referente, agente
//   POST  /api/acquisitions/{id}/status        solo le transizioni ammesse
//   POST  /api/acquisitions/{id}/lost          motivo obbligatorio
//   POST  /api/acquisitions/{id}/appointment   nuovo appuntamento (se annullato/mancato)
//   POST  /api/acquisitions/{id}/mandate       "Genera incarico" (solo se ammesso)
// Le azioni sull'appuntamento (sposta, conferma, completa, assente, annulla)
// sono quelle dell'Agenda, con il SUO dialog e le SUE rotte.
import { apiGet, apiPatch, apiPost } from '../core/api-client.js';
import { renderBadge, escapeHtml, formatDate, formatDateTime } from '../components/st-table.js';
import { openActionDialog } from '../components/agenda/agenda-dialogs.js';
import { getAgents, getAppointment } from '../agenda/agenda-api.js';
import { ACTION_LABELS, formatDuration, durationMinutes, romeDateKey, statusLabel } from '../agenda/agenda-model.js';
import {
  acquisitionAppointmentPayload, formatPrice, loadAcquisitionOptions,
  openAcquisitionAppointmentDialog, statusTone,
} from './acquisizioni.js';

/** Le azioni dell'Agenda offerte dalla scheda, nell'ordine dell'Agenda. */
export const AGENDA_ACTIONS = Object.freeze(['confirm', 'reschedule', 'complete', 'no_show', 'cancel']);

/** "Apri in Agenda": il giorno dell'appuntamento nella vista Giorno. */
export function agendaDayHref(startAt) {
  const giorno = startAt ? romeDateKey(startAt) : null;
  return giorno ? `#/agenda/giorno/${giorno}` : '#/agenda';
}

/** Solo i campi cambiati, `version` sempre: il PATCH del server. */
export function acquisitionPatchPayload(acq, valori) {
  const corpo = { version: acq.version };
  const num = (v) => (v === '' || v === null || v === undefined ? null : Number(v));
  const testo = (v) => (v === undefined || v === null || String(v).trim() === '' ? null : String(v).trim());
  const confronti = {
    owner_contact_id: [num(valori.owner_contact_id), acq.owner_contact_id],
    asking_price: [num(valori.asking_price), acq.asking_price === null ? null : Number(acq.asking_price)],
    valuation_price: [num(valori.valuation_price), acq.valuation_price === null ? null : Number(acq.valuation_price)],
    sale_timing: [testo(valori.sale_timing), acq.sale_timing],
    source: [testo(valori.source), acq.source],
    notes: [testo(valori.notes), acq.notes],
  };
  if (valori.assigned_agent_id !== undefined) {
    confronti.assigned_agent_id = [num(valori.assigned_agent_id), acq.assigned_agent_id];
  }
  for (const [campo, [nuovo, vecchio]] of Object.entries(confronti)) {
    if (nuovo !== vecchio) {
      corpo[campo] = (campo.endsWith('_price') && nuovo !== null) ? String(nuovo) : nuovo;
    }
  }
  return corpo;
}

/** Il corpo di "Genera incarico". */
export function mandatePayload(acq, valori) {
  const corpo = { version: acq.version, mandate_type: String(valori.mandate_type || '').trim(), mandate_start: valori.mandate_start };
  if (valori.mandate_end) corpo.mandate_end = valori.mandate_end;
  if (valori.agreed_price !== '' && valori.agreed_price !== null && valori.agreed_price !== undefined) corpo.agreed_price = String(valori.agreed_price);
  return corpo;
}

function riga(etichetta, valore) {
  return `<div class="detail-item"><label>${escapeHtml(etichetta)}</label>${valore}</div>`;
}

function optionsHtml(items, selected, placeholder) {
  const vuota = placeholder !== undefined ? `<option value="">${escapeHtml(placeholder)}</option>` : '';
  return vuota + (items || []).map((i) => `<option value="${escapeHtml(String(i.value))}"${String(i.value) === String(selected ?? '') ? ' selected' : ''}>${escapeHtml(i.label)}</option>`).join('');
}

export async function renderAcquisizioneDettaglio(container, params = []) {
  const id = params[0];
  container.innerHTML = '<div class="card panel"><p class="muted">Caricamento…</p></div>';
  let acq;
  let opzioni;
  try {
    [acq, opzioni] = await Promise.all([apiGet(`/api/acquisitions/${encodeURIComponent(id)}`), loadAcquisitionOptions()]);
  } catch (error) {
    container.innerHTML = `<div class="card panel"><div class="error-box">${escapeHtml(error.status === 404 ? 'Acquisizione non trovata.' : `Impossibile caricare l’acquisizione: ${error.message}`)}</div>
      <p><a href="#/acquisizioni">← Torna alle acquisizioni</a></p></div>`;
    return;
  }
  const etichetteStato = Object.fromEntries((opzioni.statuses || []).map((s) => [s.value, s.label]));
  const etichetteEventi = opzioni.event_labels || {};
  let modifica = false;
  let feedback = '';

  function ridisegna() {
    const p = acq.property || {};
    const app = acq.appointment || {};
    const azioni = acq.allowed_actions || {};
    const via = [p.address, p.civic_number].filter(Boolean).join(' ');
    const proprietari = (acq.owners || []).map((o) => `
      <li class="acq-owner${o.is_main ? ' acq-owner-main' : ''}">
        <a href="#/contatti/${encodeURIComponent(o.contact_id)}"><strong>${escapeHtml(o.display_name || `Contatto #${o.contact_id}`)}</strong></a>
        ${o.is_main ? renderBadge('Principale', 'role') : ''}
        <small class="muted">${escapeHtml((o.roles || []).join(', '))}</small>
        <div class="muted">${o.phone ? `<a href="tel:${escapeHtml(o.phone)}">${escapeHtml(o.phone)}</a>` : ''}${o.phone && o.email ? ' · ' : ''}${o.email ? `<a href="mailto:${escapeHtml(o.email)}">${escapeHtml(o.email)}</a>` : ''}</div>
      </li>`).join('');
    const durata = app.start_at && app.end_at ? formatDuration(durationMinutes(app.start_at, app.end_at)) : '—';
    const transizioni = (azioni.transitions || []).map((s) => `<button type="button" class="btn" data-transition="${escapeHtml(s)}">${escapeHtml(etichetteStato[s] || s)}</button>`).join('');
    const eventi = (acq.events || []).slice().reverse().map((e) => `
      <li><strong>${escapeHtml(etichetteEventi[e.event_type] || e.event_type)}</strong>
        ${e.from_status !== e.to_status && e.to_status ? `<span class="muted">${escapeHtml(etichetteStato[e.from_status] || e.from_status || '—')} → ${escapeHtml(etichetteStato[e.to_status] || e.to_status)}</span>` : ''}
        <small class="muted">${escapeHtml(formatDateTime(e.occurred_at))}${e.actor_name ? ` · ${escapeHtml(e.actor_name)}` : ''}</small></li>`).join('');

    container.innerHTML = `
      <div class="card panel acq-detail">
        <p><a href="#/acquisizioni">← Acquisizioni</a></p>
        <div class="acq-header">
          <h2>Acquisizione #${escapeHtml(String(acq.id))}</h2>
          ${renderBadge(acq.created_by_mistake ? acq.status_label : (etichetteStato[acq.status] || acq.status_label || acq.status), statusTone(acq.status))}
          <span class="muted">Agente: ${escapeHtml(acq.agent_name || '—')}</span>
        </div>
        <div id="acq-feedback">${feedback}</div>

        <div class="acq-actions action-bar">
          ${transizioni}
          ${azioni.mandate ? '<button type="button" class="btn primary" id="acq-mandate-btn">Genera incarico</button>' : ''}
          ${azioni.lost ? '<button type="button" class="btn ghost" id="acq-lost-btn">Segna come persa</button>' : ''}
          ${azioni.mistake ? '<button type="button" class="btn ghost" id="acq-mistake-btn">Segna come creata per errore</button>' : ''}
        </div>

        <section class="acq-section"><h3 class="section-title">Immobile</h3>
          <div class="detail-grid">
            ${riga('Codice', escapeHtml(p.code || '—'))}
            ${riga('Titolo', escapeHtml(p.title || '—'))}
            ${riga('Indirizzo', escapeHtml(via || '—'))}
            ${riga('Comune', escapeHtml(p.city || '—'))}
            ${riga('Tipologia', escapeHtml(p.property_type || '—'))}
          </div>
          <div class="action-bar"><a class="btn ghost" href="#/immobili/${encodeURIComponent(acq.property_id)}">Apri immobile</a></div>
        </section>

        <section class="acq-section"><h3 class="section-title">Proprietari</h3>
          <ul class="acq-owners-list">${proprietari || '<li class="muted">Nessun proprietario collegato.</li>'}</ul>
        </section>

        <section class="acq-section"><h3 class="section-title">Appuntamento</h3>
          <div class="detail-grid">
            ${riga('Data e ora', escapeHtml(formatDateTime(app.start_at)))}
            ${riga('Durata', escapeHtml(durata))}
            ${riga('Agente', escapeHtml(app.agent_name || '—'))}
            ${riga('Stato', escapeHtml(statusLabel(app.status) || app.status || '—'))}
          </div>
          <p class="acq-notes-label">Note dell’appuntamento (Agenda)</p>
          <p class="muted" id="acq-appointment-notes">${escapeHtml(app.notes || '—')}</p>
          <div class="action-bar" id="acq-agenda-actions">
            <a class="btn ghost" id="acq-open-agenda" href="${escapeHtml(agendaDayHref(app.start_at))}">Apri in Agenda</a>
            ${azioni.new_appointment ? '<button type="button" class="btn primary" id="acq-new-appointment">Nuovo appuntamento</button>' : ''}
            <span class="acq-agenda-buttons" id="acq-agenda-buttons"></span>
          </div>
        </section>

        <section class="acq-section"><h3 class="section-title">Informazioni commerciali</h3>
          <div id="acq-commercial">${modifica ? formModifica() : vistaCommerciale()}</div>
        </section>

        <section class="acq-section"><h3 class="section-title">Storico</h3>
          <ul class="acq-events">${eventi || '<li class="muted">Nessun evento.</li>'}</ul>
        </section>
      </div>
      <dialog id="acq-dialog" class="modal"></dialog>
      <dialog id="acq-agenda-dialog" class="modal modal-wide agenda-dialog"></dialog>
      <dialog id="acq-appointment-dialog" class="modal modal-wide agenda-dialog"></dialog>`;
    collega();
  }

  function vistaCommerciale() {
    const perso = acq.status === 'lost'
      ? `${riga('Motivo', escapeHtml(acq.lost_reason_label || acq.lost_reason || '—'))}${riga('Note sulla perdita', escapeHtml(acq.lost_notes || '—'))}${riga('Persa il', escapeHtml(formatDateTime(acq.lost_at)))}`
      : '';
    const incarico = acq.status === 'acquired'
      ? `${riga('Incarico', `${escapeHtml((acq.property || {}).mandate_type || '—')} · dal ${escapeHtml(formatDate((acq.property || {}).mandate_start))}`)}${riga('Acquisita il', escapeHtml(formatDateTime(acq.acquired_at)))}`
      : '';
    return `
      <div class="detail-grid">
        ${riga('Prezzo richiesto', escapeHtml(formatPrice(acq.asking_price)))}
        ${riga('Valutazione', escapeHtml(formatPrice(acq.valuation_price)))}
        ${riga('Tempistica di vendita', escapeHtml(acq.sale_timing_label || '—'))}
        ${riga('Fonte', escapeHtml(acq.source_label || '—'))}
        ${perso}${incarico}
      </div>
      <p class="acq-notes-label">Note commerciali</p>
      <p class="muted" id="acq-commercial-notes">${escapeHtml(acq.notes || '—')}</p>
      ${(acq.allowed_actions || {}).edit ? '<div class="action-bar"><button type="button" class="btn ghost" id="acq-edit-btn">Modifica</button></div>' : ''}`;
  }

  function formModifica() {
    const proprietari = (acq.owners || []).map((o) => ({ value: o.contact_id, label: o.display_name || `Contatto #${o.contact_id}` }));
    const agenti = (opzioni.agents || []).map((a) => ({ value: a.id, label: a.name || `#${a.id}` }));
    return `
      <div class="form-grid-2">
        <div class="form-field"><label for="acq-edit-owner">Proprietario principale</label><select id="acq-edit-owner" class="input">${optionsHtml(proprietari, acq.owner_contact_id)}</select></div>
        ${(acq.allowed_actions || {}).reassign ? `<div class="form-field"><label for="acq-edit-agent">Agente dell’acquisizione</label><select id="acq-edit-agent" class="input">${optionsHtml(agenti, acq.assigned_agent_id)}</select></div>` : ''}
        <div class="form-field"><label for="acq-edit-asking">Prezzo richiesto (€)</label><input id="acq-edit-asking" class="input" type="number" min="0" step="0.01" value="${escapeHtml(acq.asking_price ?? '')}"></div>
        <div class="form-field"><label for="acq-edit-valuation">Valutazione (€)</label><input id="acq-edit-valuation" class="input" type="number" min="0" step="0.01" value="${escapeHtml(acq.valuation_price ?? '')}"></div>
        <div class="form-field"><label for="acq-edit-timing">Tempistica di vendita</label><select id="acq-edit-timing" class="input">${optionsHtml(opzioni.sale_timings, acq.sale_timing, 'Non indicata')}</select></div>
        <div class="form-field"><label for="acq-edit-source">Fonte</label><select id="acq-edit-source" class="input">${optionsHtml(opzioni.sources, acq.source, 'Non indicata')}</select></div>
      </div>
      <div class="form-field"><label for="acq-edit-notes">Note commerciali</label><textarea id="acq-edit-notes" class="input" rows="3" maxlength="5000">${escapeHtml(acq.notes || '')}</textarea></div>
      <div class="field-error" id="acq-edit-error" role="alert"></div>
      <div class="action-bar">
        <button type="button" class="btn ghost" id="acq-edit-cancel">Annulla</button>
        <button type="button" class="btn primary" id="acq-edit-save">Salva</button>
      </div>`;
  }

  async function ricarica(messaggio = '') {
    acq = await apiGet(`/api/acquisitions/${encodeURIComponent(acq.id)}`);
    feedback = messaggio ? `<div class="success-box">${escapeHtml(messaggio)}</div>` : '';
    ridisegna();
  }

  function errore(el, error) {
    if (el) el.textContent = (error && error.message) || 'Operazione non riuscita.';
  }

  function dialogo(titolo, corpoHtml, etichettaInvio, invio) {
    const dialogEl = container.querySelector('#acq-dialog');
    dialogEl.innerHTML = `
      <form novalidate>
        <h3 class="section-title">${escapeHtml(titolo)}</h3>
        ${corpoHtml}
        <div class="field-error" data-error role="alert"></div>
        <div class="modal-actions">
          <button type="button" class="btn ghost" data-cancel>Chiudi</button>
          <button type="submit" class="btn primary" data-submit>${escapeHtml(etichettaInvio)}</button>
        </div>
      </form>`;
    const form = dialogEl.querySelector('form');
    dialogEl.querySelector('[data-cancel]').addEventListener('click', () => dialogEl.close());
    form.addEventListener('submit', async (evento) => {
      evento.preventDefault();
      const box = form.querySelector('[data-error]');
      const bottone = form.querySelector('[data-submit]');
      box.textContent = '';
      bottone.disabled = true;
      try {
        const messaggio = await invio(form);
        if (messaggio === false) return;
        dialogEl.close();
        await ricarica(messaggio || '');
      } catch (error) {
        errore(box, error);
      } finally {
        bottone.disabled = false;
      }
    });
    dialogEl.showModal();
    return form;
  }

  // DELETE-ARCH Fase 1A: l'appuntamento ancora aperto puo' essere annullato
  // insieme (dal cliente / dall'agenzia): una perdita reale non e' un errore.
  const APPUNTAMENTO_APERTO = ['requested', 'scheduled', 'confirmed'];

  function apriPersa() {
    const aperto = Boolean(acq.appointment && APPUNTAMENTO_APERTO.includes(acq.appointment.status));
    dialogo('Segna come persa', `
      <div class="form-field"><label for="acq-lost-reason">Motivo *</label><select id="acq-lost-reason" class="input">${optionsHtml(opzioni.lost_reasons, '', 'Scegli il motivo')}</select></div>
      <div class="form-field"><label for="acq-lost-notes">Note</label><textarea id="acq-lost-notes" class="input" rows="3" maxlength="5000"></textarea></div>
      ${aperto ? `<fieldset class="form-field" id="acq-lost-appointment">
        <label><input type="checkbox" id="acq-lost-cancel" checked> Annulla anche l’appuntamento del ${escapeHtml(formatDateTime(acq.appointment.start_at))}</label>
        <select id="acq-lost-cancel-kind" class="input"><option value="agency">Annullato dall’agenzia</option><option value="client">Annullato dal cliente</option></select>
      </fieldset>` : ''}`,
    'Segna come persa', async (form) => {
      const motivo = form.querySelector('#acq-lost-reason').value;
      if (!motivo) throw new Error('Indica il motivo per cui l’acquisizione è persa.');
      const note = form.querySelector('#acq-lost-notes').value.trim();
      const corpo = { version: acq.version, lost_reason: motivo };
      if (note) corpo.lost_notes = note;
      const annulla = form.querySelector('#acq-lost-cancel');
      if (annulla && annulla.checked) {
        corpo.cancel_appointment = true;
        corpo.appointment_cancelled_kind = form.querySelector('#acq-lost-cancel-kind').value;
      }
      await apiPost(`/api/acquisitions/${acq.id}/lost`, corpo);
      return 'Acquisizione segnata come persa.';
    });
  }

  // DELETE-ARCH Fase 1A: «creata per errore» - l'acquisizione non e' mai
  // esistita. Terminale; l'appuntamento aperto si annulla come «Creato per
  // errore». Il server rifiuta (APPOINTMENT_ALREADY_HAPPENED) se l'incontro
  // e' avvenuto.
  function apriErrore() {
    dialogo('Segna come creata per errore', `
      <p>L’acquisizione non è mai esistita: sparisce dall’elenco e dalle «Perse» e non si può riaprire.
      ${acq.appointment && APPUNTAMENTO_APERTO.includes(acq.appointment.status) ? 'Anche il suo appuntamento viene tolto dall’Agenda.' : ''}</p>
      <div class="form-field"><label for="acq-mistake-notes">Note</label><textarea id="acq-mistake-notes" class="input" rows="3" maxlength="5000"></textarea></div>`,
    'Conferma: creata per errore', async (form) => {
      const note = form.querySelector('#acq-mistake-notes').value.trim();
      const corpo = { version: acq.version };
      if (note) corpo.notes = note;
      await apiPost(`/api/acquisitions/${acq.id}/mistake`, corpo);
      return 'Acquisizione segnata come creata per errore.';
    });
  }

  function apriIncarico() {
    // Il giorno di Roma, non quello UTC: fra mezzanotte e le 2 la data
    // proposta sarebbe altrimenti quella di ieri.
    const oggi = romeDateKey(new Date());
    dialogo('Genera incarico', `
      <p class="muted">L’incarico viene scritto sull’immobile e l’acquisizione diventa “Acquisita”.</p>
      <div class="form-grid-2">
        <div class="form-field"><label for="acq-mandate-type">Tipo incarico *</label><input id="acq-mandate-type" class="input" type="text" maxlength="80"></div>
        <div class="form-field"><label for="acq-mandate-price">Prezzo concordato (€)</label><input id="acq-mandate-price" class="input" type="number" min="0" step="0.01" value="${escapeHtml(acq.asking_price ?? '')}"></div>
        <div class="form-field"><label for="acq-mandate-start">Data inizio *</label><input id="acq-mandate-start" class="input" type="date" value="${oggi}"></div>
        <div class="form-field"><label for="acq-mandate-end">Data scadenza</label><input id="acq-mandate-end" class="input" type="date"></div>
      </div>`,
    'Genera incarico', async (form) => {
      const valori = {
        mandate_type: form.querySelector('#acq-mandate-type').value,
        mandate_start: form.querySelector('#acq-mandate-start').value,
        mandate_end: form.querySelector('#acq-mandate-end').value,
        agreed_price: form.querySelector('#acq-mandate-price').value,
      };
      if (!valori.mandate_type.trim()) throw new Error('Indica il tipo di incarico.');
      if (!valori.mandate_start) throw new Error('Indica la data di inizio.');
      if (valori.mandate_end && valori.mandate_end < valori.mandate_start) throw new Error('La scadenza non può precedere l’inizio.');
      await apiPost(`/api/acquisitions/${acq.id}/mandate`, mandatePayload(acq, valori));
      return 'Incarico generato.';
    });
  }

  async function agenti() {
    const esito = await getAgents();
    return (esito && esito.items) || [];
  }

  async function azioneAgenda(azione) {
    const box = container.querySelector('#acq-feedback');
    try {
      const [detail, listaAgenti] = await Promise.all([getAppointment(acq.appointment_id), agenti()]);
      if (!(detail.allowed_actions || []).includes(azione)) {
        box.innerHTML = '<div class="error-box">Azione non più disponibile: ricarica la scheda.</div>';
        return;
      }
      openActionDialog(container.querySelector('#acq-agenda-dialog'), {
        action: azione, detail, agents: listaAgenti,
        onDone: () => ricarica('Appuntamento aggiornato nell’Agenda.'),
      });
    } catch (error) {
      box.innerHTML = `<div class="error-box">${escapeHtml(error.message || 'Agenda non disponibile.')}</div>`;
    }
  }

  async function mostraAzioniAgenda() {
    const barra = container.querySelector('#acq-agenda-buttons');
    if (!barra || !acq.appointment) return;
    let detail;
    try {
      detail = await getAppointment(acq.appointment_id);
    } catch (_e) {
      return;   // un appuntamento non visibile nell'Agenda (altro agente): solo lettura
    }
    // Solo le azioni che l'Agenda stessa ammette per questo appuntamento.
    const ammesse = AGENDA_ACTIONS.filter((a) => (detail.allowed_actions || []).includes(a));
    barra.innerHTML = ammesse.map((a) => `<button type="button" class="btn" data-agenda-action="${escapeHtml(a)}">${escapeHtml(ACTION_LABELS[a] || a)}</button>`).join('');
    for (const b of barra.querySelectorAll('[data-agenda-action]')) {
      b.addEventListener('click', () => azioneAgenda(b.dataset.agendaAction));
    }
  }

  function collega() {
    const $ = (sel) => container.querySelector(sel);
    for (const b of container.querySelectorAll('[data-transition]')) {
      b.addEventListener('click', async () => {
        try {
          await apiPost(`/api/acquisitions/${acq.id}/status`, { version: acq.version, status: b.dataset.transition });
          await ricarica('Stato aggiornato.');
        } catch (error) {
          feedback = `<div class="error-box">${escapeHtml(error.message)}</div>`;
          ridisegna();
        }
      });
    }
    if ($('#acq-lost-btn')) $('#acq-lost-btn').addEventListener('click', apriPersa);
    if ($('#acq-mistake-btn')) $('#acq-mistake-btn').addEventListener('click', apriErrore);
    if ($('#acq-mandate-btn')) $('#acq-mandate-btn').addEventListener('click', apriIncarico);
    if ($('#acq-edit-btn')) $('#acq-edit-btn').addEventListener('click', () => { modifica = true; ridisegna(); });
    if ($('#acq-edit-cancel')) $('#acq-edit-cancel').addEventListener('click', () => { modifica = false; ridisegna(); });
    if ($('#acq-edit-save')) {
      $('#acq-edit-save').addEventListener('click', async () => {
        const valori = {
          owner_contact_id: $('#acq-edit-owner').value,
          asking_price: $('#acq-edit-asking').value,
          valuation_price: $('#acq-edit-valuation').value,
          sale_timing: $('#acq-edit-timing').value,
          source: $('#acq-edit-source').value,
          notes: $('#acq-edit-notes').value,
        };
        if ($('#acq-edit-agent')) valori.assigned_agent_id = $('#acq-edit-agent').value;
        const corpo = acquisitionPatchPayload(acq, valori);
        try {
          if (Object.keys(corpo).length > 1) await apiPatch(`/api/acquisitions/${acq.id}`, corpo);
          modifica = false;
          await ricarica(Object.keys(corpo).length > 1 ? 'Dati salvati.' : '');
        } catch (error) {
          errore($('#acq-edit-error'), error);
        }
      });
    }
    if ($('#acq-new-appointment')) {
      $('#acq-new-appointment').addEventListener('click', async () => {
        let lista;
        try {
          lista = await agenti();
        } catch (error) {
          feedback = `<div class="error-box">${escapeHtml(`Impossibile caricare gli agenti: ${error.message}`)}</div>`;
          ridisegna();
          return;
        }
        openAcquisitionAppointmentDialog(container, opzioni, lista, {
          title: 'Nuovo appuntamento di acquisizione',
          submit: (corpo) => apiPost(`/api/acquisitions/${acq.id}/appointment`, {
            version: acq.version, appointment: acquisitionAppointmentPayload(corpo),
          }),
          onDone: () => ricarica('Nuovo appuntamento fissato nell’Agenda.'),
        });
      });
    }
    mostraAzioniAgenda();
  }

  ridisegna();
}
