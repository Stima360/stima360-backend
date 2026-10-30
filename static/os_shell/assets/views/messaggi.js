// STIMA360 OS — views/messaggi.js
// BLOCCO-4A: «Messaggi WhatsApp» (`#/messaggi`). Solo ricezione e consultazione.
//
// Le conversazioni WhatsApp ricevute dall'agenzia della sessione
// (/api/inbound-messages): mittente, data, testo o tipo del messaggio,
// contatto e immobile collegati, messaggi ancora da associare. L'associazione
// (contatto e immobile) la offre il server con `can_link` (titolare e
// amministratori); un agente vede solo i messaggi dei suoi contatti. Nessun
// pulsante di invio: questa pagina non scrive a nessuno. Tutto il testo passa
// da `textContent`; allegati e link non vengono aperti.

import { apiGet, apiPost } from '../core/api-client.js';

const API = '/api/inbound-messages';

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

export function dateTimeText(value) {
  if (!value) return '—';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleString('it-IT', { timeZone: 'Europe/Rome', day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

export function phoneText(digits) {
  return digits ? `+${digits}` : '—';
}

/** Il contenuto di un messaggio: il testo se e' testuale, altrimenti il TIPO (mai un testo finto). */
export function messageSummary(m) {
  if (!m) return '—';
  if (m.is_text) return m.text || '(messaggio vuoto)';
  const meta = m.metadata || {};
  const extra = meta.caption || meta.label || meta.filename || meta.name || meta.emoji || '';
  return extra ? `[${m.message_type_label}] ${extra}` : `[${m.message_type_label}]`;
}

export function errorMessage(error) {
  const d = error && error.data && error.data.detail;
  if (d && typeof d === 'object' && !Array.isArray(d) && d.message) return d.message;
  if (Array.isArray(d)) return 'Alcuni dati non sono validi.';
  return (error && error.message) || 'Operazione non riuscita.';
}

function linkState(m) {
  const parts = [];
  parts.push(m.contact ? `Contatto: ${m.contact.name || `n. ${m.contact.id}`}` : (m.contact_match === 'ambiguous' ? 'Contatto da scegliere (più contatti con questo numero)' : 'Nessun contatto collegato'));
  parts.push(m.property ? `Immobile: ${m.property.title || m.property.code || `n. ${m.property.id}`}` : 'Nessun immobile collegato');
  return parts.join(' · ');
}

async function searchOptions(select, url, label) {
  select.replaceChildren(el('option', null, 'Ricerca in corso…'));
  try {
    const r = await apiGet(url);
    select.replaceChildren(el('option', null, '— nessuno —'));
    select.firstChild.value = '';
    for (const item of r.items || []) {
      const o = el('option', null, label(item));
      o.value = String(item.id);
      select.appendChild(o);
    }
  } catch (error) {
    select.replaceChildren(el('option', null, errorMessage(error)));
  }
}

function linkForm(m, candidates, onDone) {
  const form = el('div', 'messages-link-form');
  form.setAttribute('data-message-link-form', String(m.id));
  const contactSearch = el('input'); contactSearch.type = 'search'; contactSearch.placeholder = 'Cerca contatto (nome, telefono)';
  const contactSelect = el('select'); contactSelect.name = 'contact_id';
  const propertySearch = el('input'); propertySearch.type = 'search'; propertySearch.placeholder = 'Cerca immobile (titolo, indirizzo, codice)';
  const propertySelect = el('select'); propertySelect.name = 'property_id';
  const keep = (select, current, text) => {
    select.replaceChildren(el('option', null, '— nessuno —'));
    select.firstChild.value = '';
    if (current) {
      const o = el('option', null, text);
      o.value = String(current);
      o.selected = true;
      select.appendChild(o);
    }
  };
  keep(contactSelect, m.contact && m.contact.id, m.contact && (m.contact.name || `Contatto n. ${m.contact.id}`));
  keep(propertySelect, m.property && m.property.id, m.property && (m.property.title || m.property.code || `Immobile n. ${m.property.id}`));
  for (const c of candidates || []) {
    if (m.contact && m.contact.id === c.id) continue;
    const o = el('option', null, `${c.name || `Contatto n. ${c.id}`} (stesso numero)`);
    o.value = String(c.id);
    contactSelect.appendChild(o);
  }
  contactSearch.addEventListener('change', () => searchOptions(contactSelect, `/api/core/contacts?limit=20&search=${encodeURIComponent(contactSearch.value)}`,
    (c) => c.display_name || [c.first_name, c.last_name].filter(Boolean).join(' ') || `Contatto n. ${c.id}`));
  propertySearch.addEventListener('change', () => searchOptions(propertySelect, `/api/property/properties?limit=20&search=${encodeURIComponent(propertySearch.value)}`,
    (p) => [p.code, p.title, p.city].filter(Boolean).join(' · ') || `Immobile n. ${p.id}`));
  const status = el('p', 'muted');
  status.setAttribute('role', 'status');
  const save = button('Salva associazione', 'primary', 'data-message-link-save');
  save.addEventListener('click', async () => {
    save.disabled = true;
    status.textContent = '';
    try {
      const r = await apiPost(`${API}/${m.id}/link`, {
        contact_id: contactSelect.value ? Number(contactSelect.value) : null,
        property_id: propertySelect.value ? Number(propertySelect.value) : null,
      });
      onDone(r.changed ? 'Associazione salvata.' : 'Nessuna modifica.');
    } catch (error) {
      status.textContent = errorMessage(error);
      status.className = 'error-box';
      save.disabled = false;
    }
  });
  const field = (label, ...nodes) => {
    const wrap = el('label', 'messages-field');
    wrap.appendChild(el('span', null, label));
    for (const n of nodes) wrap.appendChild(n);
    return wrap;
  };
  form.append(field('Contatto', contactSearch, contactSelect), field('Immobile', propertySearch, propertySelect),
    el('p', 'muted messages-hint', 'Associare un messaggio non crea contatti, richieste o attività e non conta come «richiesta ricevuta» nel report proprietario.'),
    save, status);
  return form;
}

export async function renderMessaggi(container, params = []) {
  const root = el('div', 'card panel messages-page');
  root.setAttribute('data-messages', '');
  root.appendChild(el('h2', 'section-title', 'Messaggi WhatsApp'));
  root.appendChild(el('p', 'muted messages-notice',
    'Messaggi WhatsApp ricevuti dai numeri dell’agenzia. Solo consultazione: da qui non si inviano risposte. Allegati e link non vengono aperti.'));
  const notice = el('div');
  notice.setAttribute('role', 'status');
  notice.setAttribute('data-messages-notice', '');
  const filters = el('div', 'chips');
  const allBtn = button('Tutte', 'chip active', 'data-messages-filter-all');
  const unlinkedBtn = button('Da associare', 'chip', 'data-messages-filter-unlinked');
  filters.append(allBtn, unlinkedBtn);
  const layout = el('div', 'messages-layout');
  const list = el('div', 'messages-list');
  list.setAttribute('data-messages-list', '');
  const detail = el('div', 'messages-detail');
  detail.setAttribute('data-messages-detail', '');
  layout.append(list, detail);
  root.append(notice, filters, layout);
  container.replaceChildren(root);

  let onlyUnlinked = false;
  let selected = params[0] && /^\d{6,15}$/.test(params[0]) ? params[0] : null;

  function say(message, kind) {
    notice.replaceChildren();
    if (message) notice.appendChild(el('div', kind === 'ok' ? 'success-box' : 'error-box', message));
  }

  async function loadList() {
    list.replaceChildren(el('p', 'muted', 'Caricamento…'));
    let data;
    try {
      data = await apiGet(`${API}/conversations${onlyUnlinked ? '?only_unlinked=true' : ''}`);
    } catch (error) {
      list.replaceChildren(el('div', 'error-box', errorMessage(error)));
      detail.replaceChildren();
      return;
    }
    list.replaceChildren();
    if (!data.items.length) {
      list.appendChild(el('p', 'muted', onlyUnlinked ? 'Nessun messaggio da associare.' : 'Nessun messaggio ricevuto.'));
      detail.replaceChildren();
      return;
    }
    for (const c of data.items) {
      const item = button('', 'messages-conversation', 'data-messages-conversation');
      item.setAttribute('data-sender', c.sender_phone);
      if (c.sender_phone === selected) item.classList.add('active');
      const last = c.last_message;
      const who = (last && last.contact && last.contact.name) || (last && last.sender_profile_name) || phoneText(c.sender_phone);
      item.append(el('strong', null, who), el('span', 'muted', `${phoneText(c.sender_phone)} · ${dateTimeText(c.last_received_at)}`),
        el('span', 'messages-preview', messageSummary(last)));
      if (c.without_contact || c.without_property) {
        item.appendChild(el('span', 'badge badge-warn', `Da associare: ${Math.max(c.without_contact, c.without_property)}`));
      }
      item.addEventListener('click', () => { selected = c.sender_phone; loadList(); });
      list.appendChild(item);
    }
    if (selected && data.items.some((c) => c.sender_phone === selected)) await loadConversation(selected);
    else detail.replaceChildren(el('p', 'muted', 'Seleziona una conversazione.'));
  }

  async function loadConversation(sender) {
    detail.replaceChildren(el('p', 'muted', 'Caricamento…'));
    let data;
    try {
      data = await apiGet(`${API}/conversations/${encodeURIComponent(sender)}`);
    } catch (error) {
      detail.replaceChildren(el('div', 'error-box', errorMessage(error)));
      return;
    }
    detail.replaceChildren(el('h3', null, `Conversazione con ${phoneText(sender)}`));
    for (const m of data.messages) {
      const card = el('div', 'messages-message');
      card.setAttribute('data-message', String(m.id));
      const head = el('div', 'messages-message-head');
      head.append(el('strong', null, m.sender_profile_name || phoneText(m.sender_phone)),
        el('span', 'muted', `Ricevuto ${dateTimeText(m.received_at)}${m.provider_timestamp ? ` · inviato ${dateTimeText(m.provider_timestamp)}` : ''}`));
      if (!m.is_text) head.appendChild(el('span', 'badge badge-gray', m.message_type_label));
      card.append(head, el('p', m.is_text ? 'messages-text' : 'messages-text muted', messageSummary(m)),
        el('p', 'muted messages-links', linkState(m)));
      if (data.can_link) {
        const toggle = button('Associa', 'ghost', 'data-message-link-open');
        toggle.addEventListener('click', () => {
          toggle.replaceWith(linkForm(m, data.contact_candidates, async (msg) => {
            say(msg, 'ok');
            await loadList();
          }));
        });
        card.appendChild(toggle);
      }
      detail.appendChild(card);
    }
  }

  allBtn.addEventListener('click', () => { onlyUnlinked = false; allBtn.classList.add('active'); unlinkedBtn.classList.remove('active'); loadList(); });
  unlinkedBtn.addEventListener('click', () => { onlyUnlinked = true; unlinkedBtn.classList.add('active'); allBtn.classList.remove('active'); loadList(); });
  await loadList();
}
