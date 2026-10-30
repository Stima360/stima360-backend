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

export function errorMessage(error) {
  const d = error && error.data && error.data.detail;
  if (d && typeof d === 'object' && !Array.isArray(d) && d.message) return d.message;
  if (Array.isArray(d)) return 'Il testo non è valido.';
  return (error && error.message) || 'Operazione non riuscita.';
}

function replyItem(r) {
  const item = el('li', 'wa-reply-item');
  item.setAttribute('data-wa-reply', String(r.id));
  const head = el('div', 'wa-reply-head');
  head.append(el('strong', null, r.operator || 'Operatore'), el('span', 'muted', dateTimeText(r.created_at)),
    el('span', `badge ${r.status === 'sent' ? 'badge-ok' : (r.status === 'failed' || r.status === 'indeterminate') ? 'badge-warn' : 'badge-gray'}`,
      r.delivery ? r.delivery.label : r.status_label));
  item.append(head, el('p', 'wa-reply-text', r.text));
  if (r.status === 'failed' || r.status === 'indeterminate' || r.status === 'suppressed') item.appendChild(el('p', 'wa-reply-error', failureText(r)));
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
      for (const r of st.replies) list.appendChild(replyItem(r));
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
          const reply = r.reply || {};
          let notice;
          if (reply.status === 'sent') notice = { kind: 'ok', text: 'Risposta accettata da WhatsApp.' };
          else if (['failed', 'indeterminate', 'suppressed'].includes(reply.status)) notice = { kind: 'error', text: failureText(reply) };
          else notice = { kind: 'ok', text: r.created === false ? 'Questa risposta era già stata registrata: non è stata inviata di nuovo.' : 'Risposta registrata.' };
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
