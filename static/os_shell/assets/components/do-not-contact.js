// STIMA360 OS — components/do-not-contact.js
// BLOCCO-6: il DIVIETO TOTALE DI CONTATTO nella scheda Comunicazioni del
// contatto (/api/communication/contacts/{id}/do-not-contact).
//
// Distinto dal consenso marketing: il divieto ferma OGNI comunicazione in
// uscita (servizio compreso, ogni canale), finché un operatore non lo revoca
// con una nota. L'operatore è quello della sessione. Tutto via `textContent`.

import { apiGet, apiPost } from '../core/api-client.js';

const base = (contactId) => `/api/communication/contacts/${encodeURIComponent(contactId)}/do-not-contact`;

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

function errorText(error) {
  const d = error && error.data && error.data.detail;
  if (typeof d === 'string') return d;
  return (error && error.message) || 'Operazione non riuscita.';
}

function when(iso) {
  try { return new Date(iso).toLocaleString('it-IT', { timeZone: 'Europe/Rome' }); } catch { return iso; }
}

export function renderDoNotContact(state, { readOnly = false, onSet, onLift } = {}) {
  const box = el('section', 'dnc-panel');
  box.style.marginBottom = '16px';
  box.setAttribute('data-dnc', state.active ? 'active' : 'inactive');
  if (!state.installed) return box;
  if (state.active) {
    const head = el('div', 'error-box');
    head.append(el('strong', null, 'Divieto di contatto attivo'),
      el('p', null, 'Nessuna comunicazione parte verso questo contatto, nemmeno di servizio, su nessun canale.'),
      el('p', 'muted', `${state.source_label} · dal ${when(state.set_at)} · Motivo: ${state.reason}`));
    box.appendChild(head);
    if (!readOnly) {
      const note = el('input', 'input'); note.name = 'dnc_lift_note'; note.maxLength = 1000;
      note.placeholder = 'Perché il divieto viene revocato (obbligatorio)';
      const lift = button('Revoca il divieto', 'ghost', 'data-dnc-lift');
      lift.addEventListener('click', () => onLift && onLift(note.value, lift));
      box.append(note, lift);
    }
  } else if (!readOnly) {
    const open = button('Registra divieto di contatto', 'ghost', 'data-dnc-open');
    open.addEventListener('click', () => {
      const form = el('div', 'dnc-form');
      const source = el('select', 'input'); source.name = 'dnc_source';
      for (const [v, label] of [['subject_request', 'Lo ha chiesto l’interessato'], ['agency_decision', 'Decisione dell’agenzia']]) {
        const o = el('option', null, label); o.value = v; source.appendChild(o);
      }
      const reason = el('input', 'input'); reason.name = 'dnc_reason'; reason.maxLength = 1000;
      reason.placeholder = 'Come e quando è stato chiesto, o perché (obbligatorio)';
      const save = button('Conferma il divieto', 'danger', 'data-dnc-save');
      save.addEventListener('click', () => onSet && onSet(source.value, reason.value, save));
      form.append(el('p', 'muted', 'Il divieto ferma tutte le comunicazioni, non solo il marketing. La rinuncia al marketing si registra nel consenso, sotto.'),
        source, reason, save);
      open.replaceWith(form);
    });
    box.appendChild(open);
  }
  return box;
}

export async function mountDoNotContact(container, contactId, { readOnly = false } = {}) {
  const holder = el('div');
  holder.setAttribute('data-dnc-holder', '');
  container.prepend(holder);
  async function draw(notice) {
    let state;
    try { state = await apiGet(base(contactId)); } catch { return; }
    const view = renderDoNotContact(state, {
      readOnly,
      onSet: async (source, reason, btn) => {
        btn.disabled = true;
        try { await apiPost(base(contactId), { source, reason }); await draw(); } catch (e) { btn.disabled = false; await draw(errorText(e)); }
      },
      onLift: async (note, btn) => {
        btn.disabled = true;
        try { await apiPost(`${base(contactId)}/lift`, { note }); await draw(); } catch (e) { btn.disabled = false; await draw(errorText(e)); }
      },
    });
    if (notice) view.appendChild(el('div', 'error-box', notice));
    holder.replaceChildren(view);
  }
  await draw();
}
