// STIMA360 OS — components/whatsapp-reply.js
// BLOCCO-4B: risposta WhatsApp manuale, in fondo a una conversazione di «Messaggi WhatsApp».
//
// Stato, blocchi e storico delle risposte li decide il server
// (/api/message-replies/conversations/<telefono>): finestra di 24 ore, STOP,
// contatto collegato, numero mittente configurato, invio attivo. Il browser
// invia SOLO testo, conferma e una chiave di richiesta: destinatario e numero
// mittente non escono da qui. La stessa chiave resta finché il testo non
// cambia, così un doppio clic o un nuovo tentativo dopo un errore di rete non
// mandano due volte la stessa risposta. Tutto il testo passa da `textContent`.
//
// Tre stati non finali, distinti: «in attesa» (in coda, mai partita: chi l'ha
// scritta può riprenderla), «in invio» ed «esito incerto» (il processo può
// essersi fermato dopo la chiamata a WhatsApp: mai ritentate).

import { apiGet, apiPost } from '../core/api-client.js';

const API = '/api/message-replies/conversations';

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

function dateTimeText(value) {
  if (!value) return '—';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleString('it-IT', { timeZone: 'Europe/Rome', day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

export function newRequestId() {
  if (globalThis.crypto && typeof globalThis.crypto.randomUUID === 'function') return globalThis.crypto.randomUUID();
  const b = new Uint8Array(16);
  globalThis.crypto.getRandomValues(b);
  b[6] = (b[6] & 0x0f) | 0x40; b[8] = (b[8] & 0x3f) | 0x80;
  const h = [...b].map((x) => x.toString(16).padStart(2, '0')).join('');
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

/** Il motivo di un invio non riuscito, in parole comprensibili. */
export function failureText(reply) {
  if (reply.status === 'indeterminate') {
    return 'Esito incerto: la risposta potrebbe essere partita. Non è stata ritentata: verifica con il cliente prima di riscrivere.';
  }
  if (reply.status === 'suppressed') {
    const why = {
      window_closed: 'la finestra di 24 ore si è chiusa prima dell’invio',
      sender_not_configured: 'il numero WhatsApp non è più configurato per l’agenzia',
      outbound_disabled: 'l’invio è stato disattivato',
      credentials_missing: 'mancano le credenziali d’invio',
      graph_version_missing: 'manca la versione dell’API configurata',
      conversation_not_visible: 'non hai più accesso alla conversazione',
      contact_changed: 'il contatto della conversazione è cambiato',
      sender_changed: 'il numero della conversazione è cambiato',
      no_contact: 'la conversazione non ha più un contatto',
      operator_not_authorized: 'il tuo accesso all’agenzia non è più attivo',
    }[reply.suppressed_reason];
    return `Non inviata e non ritentata: ${why || 'un controllo prima dell’invio l’ha bloccata'}. WhatsApp non è stato chiamato.`;
  }
  const detail = reply.error_detail || '';
  if (detail.includes('131047')) return 'Non inviata: la finestra di 24 ore era chiusa.';
  if (reply.error_code === 'invalid_destination') return 'Non inviata: il numero non può ricevere messaggi WhatsApp.';
  if (reply.error_code === 'missing_credentials') return 'Non inviata: credenziali WhatsApp non valide.';
  if (reply.error_code === 'provider_unavailable') return 'Non inviata: WhatsApp non era raggiungibile. Puoi riprovare con un nuovo invio.';
  return 'Non inviata: WhatsApp ha rifiutato il messaggio.';
}

/** L'avviso dopo un invio o una ripresa: mai un esito più certo di quello registrato. */
export function outcomeNotice(r) {
  const reply = (r && r.reply) || {};
  if (reply.status === 'sent') return { kind: 'ok', text: 'Risposta accettata da WhatsApp.' };
  if (['failed', 'indeterminate', 'suppressed'].includes(reply.status)) return { kind: 'error', text: failureText(reply) };
  if (reply.status === 'queued' || reply.status === 'sending') return { kind: 'error', text: pendingText(reply) };
  return { kind: 'ok', text: r && r.created === false ? 'Questa risposta era già stata registrata: non è stata inviata di nuovo.' : 'Risposta registrata.' };
}

export function errorMessage(error) {
  const d = error && error.data && error.data.detail;
  if (d && typeof d === 'object' && !Array.isArray(d) && d.message) return d.message;
  if (Array.isArray(d)) return 'Il testo non è valido.';
  return (error && error.message) || 'Operazione non riuscita.';
}

export function pendingText(reply) {
  if (reply.status === 'queued') {
    return reply.can_resume
      ? 'In attesa: non è ancora partita. Puoi riprendere l’invio: i controlli vengono ripetuti prima di WhatsApp.'
      : 'In attesa: non è ancora partita.';
  }
  if (reply.status === 'sending') {
    return 'In invio: l’esito non è ancora registrato. Se resta così, l’invio può essersi interrotto dopo la chiamata a WhatsApp: non verrà ritentato. Verifica con il cliente prima di riscrivere.';
  }
  return null;
}

function replyItem(r, onResume) {
  const item = el('li', 'wa-reply-item');
  item.setAttribute('data-wa-reply', String(r.id));
  const head = el('div', 'wa-reply-head');
  head.append(el('strong', null, r.operator || 'Operatore'), el('span', 'muted', dateTimeText(r.created_at)),
    el('span', `badge ${r.status === 'sent' ? 'badge-ok' : ['failed', 'indeterminate', 'sending'].includes(r.status) ? 'badge-warn' : 'badge-gray'}`,
      r.delivery ? r.delivery.label : r.status_label));
  item.append(head, el('p', 'wa-reply-text', r.text));
  if (r.status === 'failed' || r.status === 'indeterminate' || r.status === 'suppressed') item.appendChild(el('p', 'wa-reply-error', failureText(r)));
  const pending = pendingText(r);
  if (pending) {
    const note = el('p', 'wa-reply-error', pending);
    note.setAttribute('data-wa-reply-pending', r.status);
    item.appendChild(note);
  }
  if (r.status === 'queued' && r.can_resume) {
    const resume = button('Riprendi invio', 'ghost', 'data-wa-reply-resume');
    resume.addEventListener('click', () => onResume(r, item, resume));
    item.appendChild(resume);
  }
  return item;
}

export async function mountWhatsappReply(container, { senderPhone }) {
  const root = el('div', 'wa-reply');
  root.setAttribute('data-wa-reply-panel', '');
  container.appendChild(root);
  let requestId = null;
  let lastText = '';

  async function load(notice) {
    let st;
    try {
      st = await apiGet(`${API}/${encodeURIComponent(senderPhone)}`);
    } catch (error) {
      root.replaceChildren(el('h4', null, 'Rispondi su WhatsApp'), el('div', 'error-box', errorMessage(error)));
      return;
    }
    root.replaceChildren(el('h4', null, 'Rispondi su WhatsApp'));
    function resumeReply(r, item, resume) {
      resume.hidden = true;
      const box = el('div', 'wa-reply-confirm');
      box.appendChild(el('p', null, `Confermi l'invio di questa risposta, rimasta in attesa, a +${senderPhone}?`));
      const send = button('Conferma invio', 'primary', 'data-wa-reply-resume-confirm');
      const cancel = button('Annulla', 'ghost');
      const actions = el('div', 'agency-actions');
      actions.append(send, cancel);
      box.appendChild(actions);
      item.appendChild(box);
      cancel.addEventListener('click', () => { box.remove(); resume.hidden = false; });
      send.addEventListener('click', async () => {
        send.disabled = true; cancel.disabled = true;
        send.textContent = 'Invio in corso…';
        try {
          const out = await apiPost(`${API}/${encodeURIComponent(senderPhone)}/replies/${encodeURIComponent(String(r.id))}/resume`,
            { confirm: true });
          await load(outcomeNotice(out));
        } catch (error) {
          await load({ kind: 'error', text: `Risposta non inviata. ${errorMessage(error)}` });
        }
      });
    }
    if (notice) {
      const box = el('div', notice.kind === 'ok' ? 'success-box' : 'error-box', notice.text);
      box.setAttribute('data-wa-reply-notice', '');
      root.appendChild(box);
    }
    const win = el('p', 'muted wa-reply-window', st.window.open
      ? `Finestra di risposta aperta fino al ${dateTimeText(st.window.closes_at)} (24 ore dall'ultimo messaggio del cliente).`
      : `Finestra di risposta chiusa il ${dateTimeText(st.window.closes_at)}.`);
    win.setAttribute('data-wa-reply-window', st.window.open ? 'open' : 'closed');
    root.appendChild(win);
    if (st.stop.requested) {
      const stop = el('div', 'wa-reply-stop', st.stop.notice);
      stop.setAttribute('data-wa-reply-stop', '');
      root.appendChild(stop);
    }
    if (st.has_uncertain_reply) {
      root.appendChild(el('div', 'wa-reply-stop', 'Una risposta precedente ha esito incerto e non è stata ritentata: verifica con il cliente prima di riscrivere.'));
    }
    if (st.replies.length) {
      const list = el('ul', 'wa-reply-list');
      for (const r of st.replies) list.appendChild(replyItem(r, resumeReply));
      root.appendChild(list);
    }
    if (!st.can_reply) {
      const blocks = el('div', 'wa-reply-blocks');
      blocks.setAttribute('data-wa-reply-blocks', '');
      for (const b of st.blocks) blocks.appendChild(el('p', 'wa-reply-error', b.message));
      root.appendChild(blocks);
      return;
    }
    const text = el('textarea');
    text.name = 'wa_reply_text';
    text.rows = 3;
    text.maxLength = st.text_max;
    text.value = lastText;
    const counter = el('span', 'muted wa-reply-counter', `${text.value.length}/${st.text_max}`);
    const prepare = button('Prepara invio', 'primary', 'data-wa-reply-prepare');
    const confirmBox = el('div', 'wa-reply-confirm');
    confirmBox.hidden = true;
    text.addEventListener('input', () => {
      counter.textContent = `${text.value.length}/${st.text_max}`;
      if (text.value !== lastText) { requestId = null; confirmBox.hidden = true; prepare.hidden = false; }
      lastText = text.value;
    });
    prepare.addEventListener('click', () => {
      if (!text.value.trim()) { text.focus(); return; }
      if (!requestId) requestId = newRequestId();
      confirmBox.replaceChildren(
        el('p', null, `Confermi l'invio di questo testo a +${senderPhone}? Partirà dal numero WhatsApp dell'agenzia e resterà registrato con il tuo nome.`),
      );
      const send = button('Conferma invio', 'primary', 'data-wa-reply-confirm');
      const cancel = button('Annulla', 'ghost', 'data-wa-reply-cancel');
      const actions = el('div', 'agency-actions');
      actions.append(send, cancel);
      confirmBox.appendChild(actions);
      confirmBox.hidden = false;
      prepare.hidden = true;
      cancel.addEventListener('click', () => { confirmBox.hidden = true; prepare.hidden = false; });
      send.addEventListener('click', async () => {
        send.disabled = true; cancel.disabled = true; text.disabled = true;
        send.textContent = 'Invio in corso…';
        try {
          const r = await apiPost(`${API}/${encodeURIComponent(senderPhone)}`,
            { text: text.value, client_request_id: requestId, confirm: true });
          const notice = outcomeNotice(r);
          requestId = null; lastText = '';
          await load(notice);
        } catch (error) {
          // Stessa chiave: riprovare non manda due volte la stessa risposta.
          await load({ kind: 'error', text: `Risposta non inviata. ${errorMessage(error)}` });
        }
      });
    });
    const field = el('label', 'wa-reply-field');
    field.append(el('span', null, 'Testo della risposta (solo testo)'), text, counter);
    root.append(field, prepare, confirmBox);
  }

  await load();
}
