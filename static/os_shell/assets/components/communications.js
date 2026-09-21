// P29-3D — la tab "Comunicazioni" del Contact 360.
//
// COSA MOSTRA, E PERCHE' IN QUEST'ORDINE
//
//   1. lo stato delle AUTOMAZIONI del contatto (attive o in pausa)
//   2. la card della JOURNEY aperta, se c'e': a che passo e', quando parte
//      il prossimo, e i pulsanti per intervenire
//   3. lo STORICO, una lista cronologica sola: la mail della stima, i passi
//      della sequenza e i messaggi scritti a mano, insieme
//   4. il pulsante per scrivere un messaggio
//
// Prima lo stato, poi la storia: chi apre questa tab quasi sempre vuole
// sapere "sta partendo qualcosa?" prima di "cos'e' partito".
//
// COSA NON MOSTRA. Il token di claim, la chiave di idempotenza, il testo
// grezzo di un errore del provider: non arrivano nemmeno dall'API (li toglie
// `communication/contact_view.py`), e questa pagina non li chiede.
//
// LE RISPOSTE IN ARRIVO NON SONO MODELLATE. La nota lo dice in una riga. Non
// si scrive "nessuna risposta", perche' il sistema non lo sa: scriverlo
// sarebbe un'informazione falsa travestita da interfaccia vuota.
//
// SENZA LA MIGRATION 071 la parte journey non esiste: l'API risponde
// `available: false`, e qui si mostra lo storico con una riga che spiega
// perche' l'automazione non e' disponibile. Nessun errore, nessuna pagina
// bianca.

import { apiGet, apiPost } from '../core/api-client.js';
import { escapeHtml, formatDateTime, renderBadge } from './st-table.js';

const TONI_STATO = {
  queued: 'blue', sending: 'blue', sent: 'green', failed: 'red',
  indeterminate: 'orange', suppressed: 'gray', cancelled: 'gray',
};

function badgeStato(messaggio) {
  return renderBadge(messaggio.status_label || messaggio.status,
                     TONI_STATO[messaggio.status] || 'gray');
}

function renderNotaInbound(nota) {
  return `<p class="muted" data-inbound-note style="margin-top:12px">${escapeHtml(nota)}</p>`;
}

function renderAutomazione(journey) {
  if (!journey || journey.available === false) {
    return `<div class="panel" data-automation-panel>
      <p class="muted">Automazioni non disponibili su questo ambiente.</p>
    </div>`;
  }
  const automazione = journey.automation || { paused: false };
  const stato = automazione.paused
    ? renderBadge('in pausa', 'orange')
    : renderBadge('attive', 'green');
  const quando = automazione.paused && automazione.paused_at
    ? ` <span class="muted">dal ${escapeHtml(formatDateTime(automazione.paused_at))}</span>` : '';
  const motivo = automazione.paused && automazione.pause_reason
    ? ` <span class="muted">— ${escapeHtml(automazione.pause_reason)}</span>` : '';
  const azione = automazione.paused
    ? '<button type="button" class="btn" data-action="automation-resume">Riprendi automazioni</button>'
    : '<button type="button" class="btn" data-action="automation-pause">Metti in pausa le automazioni</button>';
  return `<div class="panel" data-automation-panel>
    <div><strong>Automazioni contatto:</strong> ${stato}${quando}${motivo}</div>
    <p class="muted">La pausa ferma le sequenze automatiche. I messaggi scritti a mano partono comunque.</p>
    <div class="actions">${azione}</div>
  </div>`;
}

function renderCardJourney(journey) {
  if (!journey || journey.available === false) return '';
  const iscrizione = journey.enrollment;
  if (!iscrizione) {
    return `<div class="panel" data-journey-card>
      <p class="muted">Nessuna automazione in corso per questo contatto.</p>
    </div>`;
  }
  const passo = iscrizione.current_step;
  const azioni = [];
  if (iscrizione.can_send_current) {
    azioni.push('<button type="button" class="btn btn-primary" data-action="send-current">Approva e invia</button>');
    azioni.push('<button type="button" class="btn" data-action="skip-current">Salta questo passo</button>');
  }
  if (iscrizione.can_pause) {
    azioni.push('<button type="button" class="btn" data-action="enrollment-pause">Metti in pausa</button>');
  }
  if (iscrizione.can_resume) {
    azioni.push('<button type="button" class="btn" data-action="enrollment-resume">Riprendi</button>');
  }
  azioni.push('<button type="button" class="btn btn-danger" data-action="enrollment-stop">Interrompi</button>');

  return `<div class="panel" data-journey-card data-enrollment-id="${iscrizione.id}">
    <div><strong>${escapeHtml(iscrizione.journey_name || 'Automazione')} attiva</strong>
      ${renderBadge(iscrizione.status_label, iscrizione.awaiting_operator ? 'orange'
        : iscrizione.status === 'paused' ? 'gray' : 'green')}</div>
    <dl class="kv">
      <dt>Passo corrente</dt>
      <dd>${passo ? `${escapeHtml(passo.step_key)} — ${escapeHtml(passo.mode_label)}` : '—'}
        ${passo ? `<span class="muted">(${iscrizione.current_step.step_no} di ${iscrizione.total_steps})</span>` : ''}</dd>
      <dt>${iscrizione.awaiting_operator ? 'In attesa dal' : 'Prossimo invio previsto'}</dt>
      <dd>${escapeHtml(formatDateTime(iscrizione.awaiting_operator
        ? iscrizione.awaiting_since : iscrizione.next_action_at))}</dd>
    </dl>
    ${iscrizione.awaiting_operator
      ? '<p class="muted">Questo passo non parte da solo: lo manda una persona.</p>' : ''}
    <div class="actions">${azioni.join(' ')}</div>
  </div>`;
}

function renderStorico(messaggi) {
  if (!messaggi.length) {
    return '<p class="muted">Nessun messaggio inviato o programmato per questo contatto.</p>';
  }
  const righe = messaggi.map((m) => {
    const quando = m.sent_at || m.scheduled_at || m.created_at;
    const azioni = [];
    if (m.can_send_now) azioni.push(`<button type="button" class="btn btn-sm" data-action="send-now" data-message-id="${m.id}">Invia ora</button>`);
    if (m.can_cancel) azioni.push(`<button type="button" class="btn btn-sm" data-action="cancel" data-message-id="${m.id}">Annulla</button>`);
    return `<tr data-message-row="${m.id}">
      <td>${escapeHtml(formatDateTime(quando))}</td>
      <td>${escapeHtml(m.channel || '—')}</td>
      <td>${escapeHtml(m.reason_label || '—')}</td>
      <td>${escapeHtml(m.mode_label || '—')}</td>
      <td>${badgeStato(m)}${m.next_send_at
        ? `<div class="muted">invio previsto ${escapeHtml(formatDateTime(m.next_send_at))}</div>` : ''}</td>
      <td><div>${escapeHtml(m.subject || '—')}</div>
        <div class="muted">${escapeHtml(m.preview || '')}</div></td>
      <td>${azioni.join(' ')}</td>
    </tr>`;
  }).join('');
  return `<table class="st-table" data-messages-table>
    <thead><tr><th>Data</th><th>Canale</th><th>Motivo</th><th>Tipo</th>
      <th>Stato</th><th>Oggetto</th><th></th></tr></thead>
    <tbody>${righe}</tbody></table>`;
}

function renderNuovoMessaggio() {
  return `<details class="panel" data-new-message>
    <summary>Nuovo messaggio</summary>
    <form data-manual-form>
      <p class="muted">Il destinatario e' l'indirizzo del contatto. Un messaggio
        di marketing parte solo se il consenso c'e'.</p>
      <label>Oggetto <input type="text" name="subject" maxlength="200" required></label>
      <label>Testo <textarea name="body" rows="6" maxlength="20000" required></textarea></label>
      <label>Tipo
        <select name="communication_type">
          <option value="marketing">Marketing</option>
          <option value="service">Servizio</option>
        </select>
      </label>
      <div class="actions">
        <button type="submit" class="btn btn-primary">Metti in coda</button>
      </div>
      <p class="error-box" data-manual-error hidden></p>
    </form>
  </details>`;
}

export function renderCommunications(stato) {
  if (stato.status === 'loading') return '<p class="muted">Caricamento…</p>';
  if (stato.status === 'error') {
    return `<div class="error-box">Errore nel caricamento delle comunicazioni: ${escapeHtml(stato.message || '')}</div>`;
  }
  return [
    renderAutomazione(stato.journey),
    renderCardJourney(stato.journey),
    '<h3>Storico comunicazioni</h3>',
    renderStorico(stato.messages || []),
    renderNotaInbound(stato.inboundNote),
    renderNuovoMessaggio(),
  ].join('\n');
}

export async function loadCommunications(contactId) {
  const [messaggi, journey] = await Promise.all([
    apiGet(`/api/communication/contacts/${contactId}/messages`),
    // La journey puo' non esserci (migration non applicata): non deve far
    // fallire lo storico, che e' la parte che esiste sempre.
    apiGet(`/api/communication/contacts/${contactId}/journey`).catch(() => ({ available: false })),
  ]);
  return {
    status: 'ready',
    messages: messaggi.messages || [],
    inboundNote: messaggi.inbound_note,
    journey,
  };
}

export function mountCommunications(mount, contactId) {
  if (!mount) return;

  async function ridisegna() {
    mount.innerHTML = renderCommunications({ status: 'loading' });
    try {
      mount.innerHTML = renderCommunications(await loadCommunications(contactId));
    } catch (errore) {
      mount.innerHTML = renderCommunications({ status: 'error', message: errore.message });
    }
  }

  const ROTTE = {
    'automation-pause': () => `/api/communication/contacts/${contactId}/automation/pause`,
    'automation-resume': () => `/api/communication/contacts/${contactId}/automation/resume`,
  };

  mount.addEventListener('click', async (evento) => {
    const bottone = evento.target.closest('button[data-action]');
    if (!bottone) return;
    const azione = bottone.dataset.action;
    const card = mount.querySelector('[data-journey-card]');
    const iscrizione = card && card.dataset.enrollmentId;
    let percorso = null;
    if (ROTTE[azione]) percorso = ROTTE[azione]();
    else if (azione === 'send-now' || azione === 'cancel') {
      percorso = `/api/communication/messages/${bottone.dataset.messageId}/`
        + (azione === 'send-now' ? 'send-now' : 'cancel');
    } else if (iscrizione) {
      const coda = {
        'send-current': 'send-current', 'skip-current': 'skip-current',
        'enrollment-pause': 'pause', 'enrollment-resume': 'resume',
        'enrollment-stop': 'stop',
      }[azione];
      if (coda) percorso = `/api/communication/journeys/enrollments/${iscrizione}/${coda}`;
    }
    if (!percorso) return;
    bottone.disabled = true;
    try {
      await apiPost(percorso, {});
      await ridisegna();
    } catch (errore) {
      bottone.disabled = false;
      const box = document.createElement('p');
      box.className = 'error-box';
      box.textContent = errore.message || 'Operazione non riuscita.';
      bottone.parentElement.appendChild(box);
    }
  });

  mount.addEventListener('submit', async (evento) => {
    const form = evento.target.closest('form[data-manual-form]');
    if (!form) return;
    evento.preventDefault();
    const errore = form.querySelector('[data-manual-error]');
    errore.hidden = true;
    const dati = new FormData(form);
    try {
      await apiPost(`/api/communication/contacts/${contactId}/messages`, {
        subject: (dati.get('subject') || '').toString(),
        body: (dati.get('body') || '').toString(),
        communication_type: (dati.get('communication_type') || 'marketing').toString(),
      });
      await ridisegna();
    } catch (guasto) {
      errore.hidden = false;
      errore.textContent = guasto.message || 'Il messaggio non e\u0027 stato messo in coda.';
    }
  });

  return ridisegna();
}
