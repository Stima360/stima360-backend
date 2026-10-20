// STIMA360 OS — incarico-dettaglio.js
// CRM-OPS-4: la scheda INCARICO, vista operativa dell'immobile in incarico.
//
//   GET   /api/property/mandates/{id}                 immobile, proprietari, agente,
//                                                     incarico, scadenza, prezzi,
//                                                     acquisizione d'origine
//   PATCH /api/property/properties/{id}               "Modifica incarico": lo STESSO
//                                                     contratto della scheda Immobile
//                                                     (tipo e date; origine permanente)
//   /api/property/properties/{id}/interactions        lo storico, lo STESSO della
//                                                     scheda Immobile (componente condiviso)
//
// Nessun "Crea incarico": nasce solo dall'acquisizione. Nessuna nota copiata:
// una riga di storico registrata qui la vede anche la scheda Immobile.
//
// SEMPLIFICAZIONE-CRM-1 - VISITE: la sezione «Visite» legge l'Agenda
// (GET /api/appointments?property_id=&types=buyer_visit, la fonte autorevole),
// con cliente, data, ora, agente e stato; nessuna copia. La visibilita' e'
// quella dell'Agenda: titolare e amministratore vedono tutta l'agenzia, un
// agente solo le visite assegnate a lui. «Programma visita» usa il dialog
// condiviso dell'Agenda come la scheda Immobile (stessa POST .../visits, stessa
// facade A31-3): cliente, agente e incarico sono obbligatori.
import { apiGet, apiPatch, apiPost } from '../core/api-client.js';
import { getAgents, getList, lookupMandates } from '../agenda/agenda-api.js';
import { MANDATE_REQUIRED_TYPES, formatTime, romeDateKey, statusLabel, todayKey } from '../agenda/agenda-model.js';
import { openCreateDialog } from '../components/agenda/agenda-dialogs.js';
import { getSession } from '../core/auth.js';
import { NO_MANDATE_FOR_VISIT, propertyVisitPayload } from './immobile-dettaglio.js';
import { renderBadge, renderTable, escapeHtml, formatDate, formatDateTime } from '../components/st-table.js';
import { mountPropertyInteractions, formatInteractionTime } from '../components/property-interactions.js';
import { COMMERCIAL_STATUS_LABELS, expiryText, expiryTone, formatPrice } from './incarichi.js';
import { propertyShortLabel } from '../components/property-label.js';

const ROLE_LABELS = { owner: 'Proprietario', seller: 'Venditore' };

function riga(etichetta, valore) {
  return `<div class="detail-item"><label>${escapeHtml(etichetta)}</label>${valore}</div>`;
}

/** Solo i campi davvero cambiati; '' = azzera (come la scheda Immobile). */
export function mandatePatchPayload(inc, valori) {
  const corpo = {};
  const tipo = String(valori.mandate_type || '').trim();
  if (tipo !== (inc.mandate_type || '')) corpo.mandate_type = tipo || null;
  for (const campo of ['mandate_start', 'mandate_end']) {
    const prima = inc[campo] ? String(inc[campo]).slice(0, 10) : '';
    if ((valori[campo] || '') !== prima) corpo[campo] = valori[campo] || null;
  }
  return corpo;
}

/** La tabella delle visite dell'incarico, dalle righe dell'Agenda. */
export function renderVisiteIncarico(righe, session) {
  const ordinate = [...(righe || [])].sort((a, b) => Date.parse(b.start_at) - Date.parse(a.start_at));
  const nota = session && session.role === 'agent'
    ? '<p class="muted" id="inc-visits-scope">Vedi le visite assegnate a te; le altre restano nell’agenda dei colleghi.</p>' : '';
  const table = renderTable(
    [
      { label: 'Data', render: (v) => escapeHtml(formatDate(v.start_at)) },
      { label: 'Ora', render: (v) => escapeHtml(`${formatTime(v.start_at)}–${formatTime(v.end_at)}`) },
      { label: 'Cliente', render: (v) => escapeHtml(v.contact_name || '—') },
      { label: 'Agente', render: (v) => escapeHtml(v.agent_name || '—') },
      { label: 'Stato', render: (v) => renderBadge(statusLabel(v.status) || v.status || '—', ['scheduled', 'confirmed', 'completed'].includes(v.status) ? 'ok' : 'gray') },
      { label: '', render: (v) => `<a class="btn ghost" href="#/agenda/giorno/${escapeHtml(romeDateKey(v.start_at))}" data-open-agenda="${escapeHtml(v.id)}">Apri in Agenda</a>` },
    ],
    ordinate,
    { emptyMessage: 'Nessuna visita programmata per questo incarico.' },
  );
  return nota + table;
}

export async function renderIncaricoDettaglio(container, params = []) {
  const id = params[0];
  container.innerHTML = '<div class="card panel"><p class="muted">Caricamento…</p></div>';
  let inc;
  let feedback = '';

  async function carica() {
    inc = await apiGet(`/api/property/mandates/${encodeURIComponent(id)}`);
  }

  try {
    await carica();
  } catch (error) {
    container.innerHTML = `<div class="card panel"><p><a href="#/incarichi">← Incarichi</a></p><div class="error-box">Incarico non disponibile: ${escapeHtml(error.message)}</div></div>`;
    return;
  }

  function vistaUltima() {
    const u = inc.last_interaction;
    return u
      ? `${escapeHtml(u.type_label || u.type)} · ${escapeHtml(formatInteractionTime(u.occurred_at))}${u.author_name ? ` · ${escapeHtml(u.author_name)}` : ''}`
      : '<span class="muted">Nessuna interazione registrata</span>';
  }

  function disegna() {
    const acq = inc.acquisition || {};
    const indirizzo = [[inc.address, inc.civic_number].filter(Boolean).join(' '), inc.city].filter(Boolean).join(' · ');
    const principale = (inc.owners || []).find((o) => o.is_main);
    container.innerHTML = `
      <div class="card panel inc-detail">
        <p><a href="#/incarichi">← Incarichi</a></p>
        <div class="inc-head">
          <h2 class="section-title">Incarico · ${escapeHtml(propertyShortLabel(inc))}</h2>
          ${renderBadge(COMMERCIAL_STATUS_LABELS[inc.commercial_status] || inc.commercial_status || '—', 'gray')}
          ${renderBadge(expiryText(inc.days_to_expiry), expiryTone(inc.days_to_expiry))}
        </div>
        ${feedback}
        <div class="action-bar inc-actions">
          <a class="btn ghost" id="inc-open-property" href="#/immobili/${encodeURIComponent(inc.property_id)}">Apri immobile</a>
          ${acq.visible ? `<a class="btn ghost" id="inc-open-acquisition" href="#/acquisizioni/${encodeURIComponent(acq.id)}">Apri acquisizione</a>` : ''}
          ${principale ? `<a class="btn ghost" id="inc-open-owner" href="#/contatti/${encodeURIComponent(principale.contact_id)}">Apri proprietario</a>` : ''}
          <button type="button" class="btn ghost" id="inc-edit">Modifica incarico</button>
        </div>

        <h3 class="section-title">Immobile</h3>
        <div class="detail-grid">
          ${riga('Titolo', escapeHtml(inc.title || '—'))}
          ${riga('Indirizzo', escapeHtml(indirizzo || '—'))}
          ${riga('Stato commerciale', escapeHtml(COMMERCIAL_STATUS_LABELS[inc.commercial_status] || inc.commercial_status || '—'))}
        </div>

        <h3 class="section-title">Incarico</h3>
        <div class="detail-grid">
          ${riga('Tipo incarico', escapeHtml(inc.mandate_type || '—'))}
          ${riga('Data inizio', escapeHtml(formatDate(inc.mandate_start)))}
          ${riga('Data scadenza', escapeHtml(formatDate(inc.mandate_end)))}
          ${riga('Giorni residui', escapeHtml(expiryText(inc.days_to_expiry)))}
          ${riga('Agente', escapeHtml(inc.agent_name || '—'))}
          ${riga('Prezzo richiesto', escapeHtml(formatPrice(inc.asking_price)))}
          ${riga('Prezzo minimo', escapeHtml(formatPrice(inc.minimum_price)))}
          ${riga('Prezzo concordato all’incarico', escapeHtml(formatPrice(inc.agreed_price)))}
          ${riga('Valutazione', escapeHtml(formatPrice(inc.valuation_price)))}
          ${riga('Acquisizione di origine', acq.visible ? `<a href="#/acquisizioni/${encodeURIComponent(acq.id)}">#${escapeHtml(String(acq.id))}</a>` : `#${escapeHtml(String(acq.id))} <small class="muted">(di ${escapeHtml(acq.agent_name || 'un altro agente')})</small>`)}
          ${riga('Acquisita il', escapeHtml(formatDateTime(acq.acquired_at)))}
          <div class="detail-item" id="inc-last-interaction"><label>Ultima interazione</label>${vistaUltima()}</div>
        </div>

        <h3 class="section-title">Proprietari</h3>
        ${(inc.owners || []).length ? `<ul class="inc-owners">${inc.owners.map((o) => `
          <li><a href="#/contatti/${encodeURIComponent(o.contact_id)}"><strong>${escapeHtml(o.display_name || `Contatto #${o.contact_id}`)}</strong></a>
            ${o.is_main ? renderBadge('Principale', 'ok') : ''}
            <small class="muted">${escapeHtml((o.roles || []).map((r) => ROLE_LABELS[r] || r).join(', '))}${o.phone ? ` · ${escapeHtml(o.phone)}` : ''}${o.email ? ` · ${escapeHtml(o.email)}` : ''}</small></li>`).join('')}</ul>`
    : '<p class="muted">Nessun proprietario collegato all’immobile.</p>'}

        <h3 class="section-title">Visite</h3>
        <div class="action-bar"><button type="button" class="btn primary" id="inc-visit-schedule">Programma visita</button></div>
        <div id="inc-visits-feedback"></div>
        <div id="inc-visits"><p class="muted">Caricamento visite…</p></div>

        <h3 class="section-title">Storico interazioni</h3>
        <div id="inc-interactions"></div>
      </div>
      <dialog class="modal" id="inc-edit-dialog"></dialog>
      <dialog class="modal modal-wide agenda-dialog" id="inc-visit-dialog"></dialog>`;
    container.querySelector('#inc-edit').addEventListener('click', apriModifica);
    container.querySelector('#inc-visit-schedule').addEventListener('click', programmaVisita);
    caricaVisite();
    mountPropertyInteractions(container.querySelector('#inc-interactions'), {
      propertyId: inc.property_id,
      context: 'mandate',
      referents: (inc.owners || []).map((o) => ({ contact_id: o.contact_id, display_name: o.display_name })),
      // Dopo una nota: "Ultima interazione" si rilegge dal server.
      onChange: async () => {
        try {
          await carica();
          const campo = container.querySelector('#inc-last-interaction');
          if (campo) campo.innerHTML = `<label>Ultima interazione</label>${vistaUltima()}`;
        } catch (error) {
          // lo storico e' gia' aggiornato; il riepilogo si rilegge al prossimo caricamento
        }
      },
    });
  }

  // --- Visite (SEMPLIFICAZIONE-CRM-1) -------------------------------------
  async function caricaVisite() {
    const area = container.querySelector('#inc-visits');
    if (!area) return;
    let righe;
    try {
      const esito = await getList({ propertyId: inc.property_id, types: [...MANDATE_REQUIRED_TYPES], limit: 200 });
      righe = (esito && esito.items) || [];
    } catch (error) {
      area.innerHTML = `<div class="error-box">Impossibile caricare le visite: ${escapeHtml(error.message || 'errore')}</div>`;
      return;
    }
    area.innerHTML = renderVisiteIncarico(righe, getSession());
  }

  async function programmaVisita() {
    const dialogEl = container.querySelector('#inc-visit-dialog');
    const fb = container.querySelector('#inc-visits-feedback');
    if (!dialogEl) return;
    let incarichi;
    try {
      incarichi = await lookupMandates({ propertyId: inc.property_id });
    } catch (error) {
      if (fb) fb.innerHTML = `<div class="error-box">${escapeHtml(`Impossibile verificare l’incarico: ${error.message || 'errore sconosciuto'}`)}</div>`;
      return;
    }
    if (!(incarichi && Array.isArray(incarichi.items) && incarichi.items.length)) {
      if (fb) fb.innerHTML = `<div class="error-box" data-no-mandate>${escapeHtml(NO_MANDATE_FOR_VISIT)}</div>`;
      return;
    }
    let agents;
    try {
      const esito = await getAgents();
      agents = (esito && esito.items) || [];
    } catch (error) {
      if (fb) fb.innerHTML = `<div class="error-box">${escapeHtml(`Impossibile caricare gli agenti: ${error.message || 'errore sconosciuto'}`)}</div>`;
      return;
    }
    openCreateDialog(dialogEl, {
      agents,
      dateKey: todayKey(),
      session: getSession(),
      title: 'Programma visita',
      appointmentType: 'buyer_visit',
      lockAppointmentType: true,
      durationMinutes: 60,
      lockDuration: true,
      requireAgent: true,
      requireContact: true,
      crmMode: 'contact_lead',
      showLocation: false,
      showNotes: false,
      submitAppointment: (corpo) => {
        if (!(Date.parse(corpo.start_at) > Date.now())) {
          throw new Error('L’orario scelto è già passato: scegli un orario futuro.');
        }
        return apiPost(`/api/property/properties/${inc.property_id}/visits`, propertyVisitPayload(corpo));
      },
      onDone: async () => {
        if (fb) fb.innerHTML = '<div class="success-box">Visita programmata nell’Agenda.</div>';
        await caricaVisite();
      },
    });
  }

  function apriModifica() {
    const dialogEl = container.querySelector('#inc-edit-dialog');
    dialogEl.innerHTML = `
      <form novalidate>
        <h3 class="section-title">Modifica incarico</h3>
        <p class="muted">L’origine (acquisizione #${escapeHtml(String(inc.acquisition.id))}) resta collegata.</p>
        <div class="form-field"><label for="inc-edit-type">Tipo incarico</label><input id="inc-edit-type" class="input" type="text" maxlength="80" value="${escapeHtml(inc.mandate_type || '')}"></div>
        <div class="form-field"><label for="inc-edit-start">Data inizio</label><input id="inc-edit-start" class="input" type="date" value="${escapeHtml(inc.mandate_start ? String(inc.mandate_start).slice(0, 10) : '')}"></div>
        <div class="form-field"><label for="inc-edit-end">Data scadenza</label><input id="inc-edit-end" class="input" type="date" value="${escapeHtml(inc.mandate_end ? String(inc.mandate_end).slice(0, 10) : '')}"></div>
        <div class="field-error" id="inc-edit-error" role="alert"></div>
        <div class="modal-actions">
          <button type="button" class="btn ghost" id="inc-edit-cancel">Chiudi</button>
          <button type="submit" class="btn primary" id="inc-edit-save">Salva</button>
        </div>
      </form>`;
    dialogEl.querySelector('#inc-edit-cancel').addEventListener('click', () => dialogEl.close());
    dialogEl.querySelector('form').addEventListener('submit', async (evento) => {
      evento.preventDefault();
      const errore = dialogEl.querySelector('#inc-edit-error');
      errore.textContent = '';
      const valori = {
        mandate_type: dialogEl.querySelector('#inc-edit-type').value,
        mandate_start: dialogEl.querySelector('#inc-edit-start').value,
        mandate_end: dialogEl.querySelector('#inc-edit-end').value,
      };
      if (!valori.mandate_type.trim()) { errore.textContent = 'Indica il tipo di incarico.'; return; }
      if (!valori.mandate_start) { errore.textContent = 'Indica la data di inizio.'; return; }
      if (valori.mandate_end && valori.mandate_end < valori.mandate_start) {
        errore.textContent = 'La scadenza non può precedere l’inizio.'; return;
      }
      const corpo = mandatePatchPayload(inc, valori);
      if (!Object.keys(corpo).length) { dialogEl.close(); return; }
      try {
        await apiPatch(`/api/property/properties/${encodeURIComponent(inc.property_id)}`, corpo);
        await carica();
      } catch (error) {
        errore.textContent = error.message || 'Salvataggio non riuscito.';
        return;
      }
      dialogEl.close();
      feedback = '<div class="success-box">Incarico aggiornato.</div>';
      disegna();
    });
    dialogEl.showModal();
  }

  disegna();
}
