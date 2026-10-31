// STIMA360 OS — components/auto-reply.js
// BLOCCO-4D: il motore delle risposte automatiche nella conversazione, SOLO
// SIMULAZIONE. Mostra lo stato (arresto generale, modo dell'agenzia), la pausa
// della conversazione e, sotto ogni messaggio, la decisione registrata con
// l'etichetta «Simulazione — non inviato». Nessun invio parte da qui: le sole
// scritture sono pausa/ripresa e il modo dell'agenzia (titolare/amministratore).
// Tutto il testo passa da `textContent`.

import { apiGet, apiPost, apiPut } from '../core/api-client.js';

const API = '/api/auto-replies';

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function button(label, className, attr) {
  const b = el('button', className ? `btn ${className}` : 'btn', label);
  b.type = 'button';
  if (attr) b.setAttribute(attr, '');
  return b;
}

function errorMessage(error) {
  const d = error && error.data && error.data.detail;
  if (d && typeof d === 'object' && !Array.isArray(d) && d.message) return d.message;
  return (error && error.message) || 'Operazione non riuscita.';
}

/** Lo stato della conversazione, o null se il motore non è installato qui. */
export async function fetchAutoState(senderPhone) {
  try {
    return await apiGet(`${API}/conversations/${encodeURIComponent(senderPhone)}`);
  } catch (error) {
    if (error && (error.status === 503 || error.status === 404)) return null;
    throw error;
  }
}

export function statusText(settings) {
  if (!settings.global_enabled) return 'Risposte automatiche: arresto generale attivo (nessuna decisione).';
  if (settings.effective === 'send' && settings.sending === 'enabled') {
    return 'Risposte automatiche: INVIO REALE attivo — le risposte partono su WhatsApp dopo i controlli.';
  }
  if (settings.effective === 'send') {
    return 'Risposte automatiche: modo d’invio, ma l’invio WhatsApp dell’ambiente è spento — ogni risposta viene bloccata prima di partire.';
  }
  if (settings.effective === 'simulate') return 'Risposte automatiche: simulazione attiva — le decisioni si registrano, nessun messaggio parte.';
  return 'Risposte automatiche: disattivate per questa agenzia.';
}

/** La decisione simulata sotto un messaggio. */
export function renderDecision(d) {
  const box = el('div', 'wa-auto-decision');
  box.setAttribute('data-auto-decision', String(d.message_id));
  const head = el('div', 'wa-auto-head');
  const badge = el('span', 'badge badge-gray', d.label);
  badge.setAttribute('data-auto-simulation', '');
  head.append(badge, el('strong', null, d.decision_label));
  box.appendChild(head);
  if (d.reason) box.appendChild(el('p', 'muted', `Motivo: ${d.reason}`));
  const sendMode = d.mode === 'send';
  if (d.proposed_text) {
    box.appendChild(el('p', null, sendMode ? 'Testo della risposta (come registrato):' : 'Testo che verrebbe proposto (non inviato):'));
    const quote = el('blockquote', 'wa-auto-text', d.proposed_text);
    quote.setAttribute('data-auto-text', '');
    box.appendChild(quote);
  }
  if (d.handoff && d.handoff.task === 'planned') {
    box.appendChild(el('p', 'muted', 'Intervento dell’agente: attività solo pianificata, non creata (simulazione).'));
  }
  if (d.handoff && d.handoff.task === 'created') {
    box.appendChild(el('p', 'muted', d.handoff.assigned
      ? 'Intervento dell’agente: attività creata e assegnata all’agente del contatto.'
      : 'Intervento dell’agente: attività creata, NON assegnata (il contatto non ha un agente attivo).'));
  }
  if (d.data_used && d.data_used.booking_link_id) {
    box.appendChild(el('p', 'muted', `Collegamento di prenotazione: n. ${d.data_used.booking_link_id}`
      + (d.data_used.owner_approval_required ? ' (serve l’assenso del proprietario)' : '')
      + (sendMode
        ? '. Al momento dell’invio si emette un collegamento dedicato, con scadenza: l’indirizzo non viene conservato.'
        : '. Nel testo resta un segnaposto: l’indirizzo non viene conservato.')));
  }
  if (sendMode && d.delivery && d.delivery.suppressed_reason) {
    box.appendChild(el('p', 'muted', `Bloccato prima dell’invio: ${d.delivery.suppressed_reason}`));
  }
  const fields = (d.data_used && d.data_used.fields) || [];
  if (fields.length) {
    box.appendChild(el('p', 'muted', `Dati usati: ${fields.map((f) => f.value).join(' · ')}`));
  }
  box.appendChild(el('p', 'muted', `Regole ${d.rules_version}`));
  return box;
}

/** Il pannello in cima alla conversazione: stato, pausa, modo dell'agenzia. */
export function renderAutoPanel(state, senderPhone, onChange) {
  const panel = el('div', 'wa-auto-panel');
  panel.setAttribute('data-auto-panel', '');
  const status = el('p', null, statusText(state.settings));
  status.setAttribute('data-auto-status', state.settings.effective);
  panel.appendChild(status);
  const actions = el('div', 'agency-actions');
  const pause = button(state.paused ? 'Riprendi automazione' : 'Metti in pausa', 'ghost', 'data-auto-pause');
  pause.addEventListener('click', async () => {
    pause.disabled = true;
    try {
      await apiPost(`${API}/conversations/${encodeURIComponent(senderPhone)}/${state.paused ? 'resume' : 'pause'}`, {});
      await onChange();
    } catch (error) {
      pause.disabled = false;
      panel.appendChild(el('div', 'error-box', errorMessage(error)));
    }
  });
  actions.appendChild(pause);
  if (state.settings.can_configure) {
    const next = state.settings.mode === 'off' ? 'simulate' : 'off';
    const mode = button(next === 'simulate' ? 'Attiva simulazione per l’agenzia' : 'Disattiva per l’agenzia', 'ghost', 'data-auto-mode');
    mode.addEventListener('click', async () => {
      mode.disabled = true;
      try {
        await apiPut(`${API}/settings`, { mode: next });
        await onChange();
      } catch (error) {
        mode.disabled = false;
        panel.appendChild(el('div', 'error-box', errorMessage(error)));
      }
    });
    actions.appendChild(mode);
    // Il modo d'invio si offre SOLO se l'ambiente lo consente (arresto generale
    // su `send`, migrazione 123 applicata), e chiede una conferma esplicita.
    if (state.settings.global_mode === 'send' && state.settings.send_available) {
      const sending = state.settings.mode === 'send';
      const send = button(sending ? 'Torna alla simulazione' : 'Attiva INVIO REALE per l’agenzia', sending ? 'ghost' : 'danger', 'data-auto-send');
      send.addEventListener('click', async () => {
        if (!sending && !window.confirm('Le risposte automatiche partiranno davvero su WhatsApp verso i clienti. Confermi?')) return;
        send.disabled = true;
        try {
          await apiPut(`${API}/settings`, { mode: sending ? 'simulate' : 'send' });
          await onChange();
        } catch (error) {
          send.disabled = false;
          panel.appendChild(el('div', 'error-box', errorMessage(error)));
        }
      });
      actions.appendChild(send);
    }
  }
  panel.appendChild(actions);
  if (state.paused) panel.appendChild(el('p', 'muted', 'Conversazione in pausa: il motore non decide nulla finché non la riprendi.'));
  return panel;
}
