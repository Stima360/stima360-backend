// STIMA360 OS — components/suggested-reply.js
// BLOCCO-4C: «Prepara risposta» sotto un messaggio di «Messaggi WhatsApp».
//
// Il server calcola il suggerimento dai dati attuali e dai soli campi
// comunicabili della scheda immobile (/api/reply-suggestions/messages/<id>):
// testo, dati e fonti usati, motivo dell'eventuale passaggio all'agente.
// Qui si mostra e si modifica; «Usa nel compositore» ricalcola prima di
// trasferire e, se i dati sono cambiati, lo dice invece di trasferire. Il
// trasferimento mette il testo nel compositore del 4B: nessun invio parte da
// qui, e il compositore chiede comunque la conferma. Tutto via `textContent`.

import { apiGet } from '../core/api-client.js';

const API = '/api/reply-suggestions/messages';

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

export function errorMessage(error) {
  const d = error && error.data && error.data.detail;
  if (d && typeof d === 'object' && !Array.isArray(d) && d.message) return d.message;
  return (error && error.message) || 'Suggerimento non disponibile.';
}

/** Il dettaglio dell'esito, in righe leggibili. */
export function explanation(s) {
  const lines = [];
  if (s.handoff) lines.push(`Motivo: ${s.handoff.reason}`);
  if (s.topics && s.topics.length) lines.push(`Domanda riconosciuta: ${s.topics.join(', ')}`);
  if (s.missing && s.missing.length) lines.push(`Non registrato nella scheda: ${s.missing.join(', ')}`);
  if (s.cited_properties && s.cited_properties.length) {
    lines.push(`Immobili citati nel testo: ${s.cited_properties.map((p) => p.code).join(', ')}`);
  }
  return lines;
}

export function mountReplySuggestion(card, { messageId, getComposer }) {
  const open = button('Prepara risposta', 'ghost', 'data-suggestion-open');
  card.appendChild(open);
  let panel = null;

  async function fetchSuggestion() {
    return apiGet(`${API}/${encodeURIComponent(String(messageId))}`);
  }

  function render(s, notice) {
    const box = el('div', 'wa-suggestion');
    box.setAttribute('data-suggestion', String(messageId));
    box.setAttribute('data-suggestion-outcome', s.outcome);
    box.appendChild(el('h4', null, `Suggerimento: ${s.outcome_label}`));
    if (notice) {
      const n = el('div', 'error-box', notice);
      n.setAttribute('data-suggestion-changed', '');
      box.appendChild(n);
    }
    for (const line of explanation(s)) box.appendChild(el('p', 'muted', line));
    if (s.property) {
      box.appendChild(el('p', 'muted', `Immobile del messaggio: ${s.property.code || s.property.title || `n. ${s.property.id}`}`));
    }
    if (s.sources.length) {
      const list = el('ul', 'wa-suggestion-sources');
      list.setAttribute('data-suggestion-sources', '');
      for (const src of s.sources) list.appendChild(el('li', null, `${src.label}: ${src.value} — ${src.source}`));
      box.append(el('p', null, 'Dati del CRM usati:'), list);
    }
    const actions = el('div', 'agency-actions');
    if (s.text) {
      const text = el('textarea');
      text.name = 'wa_suggestion_text';
      text.rows = 4;
      text.value = s.text;
      text.setAttribute('data-suggestion-text', '');
      const use = button('Usa nel compositore', 'primary', 'data-suggestion-use');
      use.addEventListener('click', async () => {
        use.disabled = true;
        let fresh;
        try {
          fresh = await fetchSuggestion();
        } catch (error) {
          use.disabled = false;
          box.appendChild(el('div', 'error-box', errorMessage(error)));
          return;
        }
        if (fresh.fingerprint !== s.fingerprint) {
          show(fresh, 'I dati sono cambiati rispetto al suggerimento: controlla il nuovo testo prima di usarlo.');
          return;
        }
        const composer = getComposer();
        if (!composer || !composer.setDraft(text.value)) {
          use.disabled = false;
          box.appendChild(el('div', 'error-box', 'Il compositore non è disponibile ora: vedi i blocchi della risposta.'));
          return;
        }
        use.disabled = false;
        box.appendChild(el('div', 'success-box', 'Testo nel compositore: controllalo e conferma l’invio da lì. Non è partito nulla.'));
      });
      const field = el('label', 'wa-reply-field');
      field.append(el('span', null, 'Testo suggerito (puoi modificarlo)'), text);
      box.appendChild(field);
      actions.appendChild(use);
    } else {
      box.appendChild(el('p', 'muted', 'Nessun testo proposto per questo messaggio.'));
    }
    const close = button('Chiudi', 'ghost', 'data-suggestion-close');
    close.addEventListener('click', () => { box.remove(); panel = null; open.hidden = false; });
    actions.appendChild(close);
    box.appendChild(actions);
    box.appendChild(el('p', 'muted', 'Solo un suggerimento: nessun messaggio viene inviato automaticamente.'));
    return box;
  }

  function show(s, notice) {
    const next = render(s, notice);
    if (panel) panel.replaceWith(next); else card.appendChild(next);
    panel = next;
  }

  open.addEventListener('click', async () => {
    open.disabled = true;
    try {
      const s = await fetchSuggestion();
      open.hidden = true;
      show(s);
    } catch (error) {
      card.appendChild(el('div', 'error-box', errorMessage(error)));
    } finally {
      open.disabled = false;
    }
  });
}
