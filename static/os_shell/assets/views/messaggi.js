// STIMA360 OS — views/messaggi.js
// «WhatsApp» (`#/messaggi[/<telefono>[/<casella>]]`): una chat semplice, come WhatsApp Web.
//
// Sinistra: le conversazioni (nome del contatto CRM, anteprima, ora, non letti),
// con ricerca. Destra: la chat con il cliente, fumetti ricevuti a sinistra e
// inviati a destra, e in basso il campo per scrivere (components/whatsapp-reply.js,
// /api/message-replies: conferma esplicita, chiave di richiesta contro il doppio
// invio, finestra di 24 ore e destinatari autorizzati decisi dal server).
//
// DUE CASELLE: una conversazione è (numero ricevente, cliente). Con più caselle
// visibili l'elenco mostra su quale numero è arrivata ogni chat e si può
// filtrare per casella; la risposta parte dallo stesso numero (`inbox` =
// phone_number_id, deciso dal server). Lo stesso cliente su due numeri ha due
// chat e una sola scheda contatto. Chi vede quali caselle lo decide il server.
//
// Ciò che non serve tutti i giorni sta nel menu «⋯»: cambiare o collegare il
// contatto (BLOCCO-4A, /api/inbound-messages, `can_link` deciso dal server),
// collegare un immobile a un messaggio, le stime suggerite, il suggerimento di
// risposta (BLOCCO-4C) e le risposte automatiche (BLOCCO-4D, solo simulazione).
// Il riconoscimento del contatto è automatico, lato server. Tutto il testo
// passa da `textContent`; allegati e link non vengono mai aperti.

import { apiGet, apiPost } from '../core/api-client.js';
import { navigate } from '../core/router.js';
import { mountWhatsappReply } from '../components/whatsapp-reply.js';
import { mountReplySuggestion } from '../components/suggested-reply.js';
import { fetchAutoState, renderAutoPanel, renderDecision } from '../components/auto-reply.js';
import { openCreateContactDialog } from './contatti.js';

const REPLIES_API = '/api/message-replies/conversations';
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

/** Solo l'ora se è oggi, altrimenti la data: come in una chat. */
export function chatTimeText(value) {
  if (!value) return '';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return '';
  const opts = { timeZone: 'Europe/Rome' };
  const today = new Date().toLocaleDateString('it-IT', opts) === d.toLocaleDateString('it-IT', opts);
  return today ? d.toLocaleTimeString('it-IT', { ...opts, hour: '2-digit', minute: '2-digit' })
    : d.toLocaleDateString('it-IT', { ...opts, day: '2-digit', month: '2-digit', year: 'numeric' });
}

function dayText(value) {
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleDateString('it-IT', { timeZone: 'Europe/Rome', weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
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

function initials(name) {
  const parts = String(name || '').replace(/[^\p{L}\p{N} ]/gu, ' ').trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return '…';
  return (parts[0][0] + (parts[1] ? parts[1][0] : '')).toUpperCase();
}

/** Apre una scheda del CRM (mai un indirizzo esterno o ricevuto nel messaggio). */
function openButton(label, route, id, className = 'ghost') {
  const b = button(label, className);
  b.setAttribute('data-open', `${route}/${id}`);
  b.addEventListener('click', () => navigate(route, [String(id)]));
  return b;
}

// ---------------------------------------------------------------------------
// Pannelli del menu «⋯» (la logica è quella di prima, solo spostata)
// ---------------------------------------------------------------------------

function stimeSection(stime) {
  const section = el('div', 'wa-sheet-section');
  section.setAttribute('data-messages-stime', '');
  section.appendChild(el('h4', null, 'Stime suggerite'));
  if (!stime || !stime.length) {
    section.appendChild(el('p', 'muted', 'Nessuna stima dal contatto collegato o con questo numero.'));
    return section;
  }
  section.appendChild(el('p', 'muted', 'Solo suggerimenti: una stima con lo stesso numero non è collegata alla conversazione e può riguardare un’altra persona.'));
  const list = el('ul', 'wa-sheet-list');
  for (const s of stime) {
    const li = el('li');
    const where = [s.via, s.civico].filter(Boolean).join(' ');
    const label = [where, s.comune, s.tipologia].filter(Boolean).join(' · ') || `Stima n. ${s.id}`;
    li.append(openButton(label, 'stime', s.id),
      el('span', 'muted', ` ${dateTimeText(s.data)} · ${s.match === 'contact' ? 'dal contatto collegato' : 'stesso numero, non collegata'}`));
    li.setAttribute('data-stima', String(s.id));
    list.appendChild(li);
  }
  section.appendChild(list);
  return section;
}

/**
 * Contatto della conversazione: cambiarlo fra i candidati con lo stesso
 * numero, collegarne uno, o crearlo con il telefono già compilato. Vale per
 * tutta la conversazione (POST .../link). La scelta manuale prevale sempre.
 */
function contactSection(data, sender, onDone) {
  const section = el('div', 'wa-sheet-section');
  section.setAttribute('data-messages-contact', data.contact ? String(data.contact.id) : 'none');
  section.appendChild(el('h4', null, data.contact ? 'Contatto collegato' : 'Collega un contatto'));
  const status = el('p', 'muted');
  status.setAttribute('role', 'status');
  const linkTo = async (contactId) => {
    try {
      const r = await apiPost(`${API}/conversations/${encodeURIComponent(sender)}/link${inboxQuery(data.phone_number_id)}`, { contact_id: contactId });
      onDone(r.changed ? 'Conversazione collegata al contatto.' : 'Nessuna modifica.');
    } catch (error) {
      status.textContent = errorMessage(error);
      status.className = 'error-box';
    }
  };
  const others = (data.contact_candidates || []).filter((c) => !data.contact || c.id !== data.contact.id);
  if (data.contact && data.contact.private) {
    // Contatto riservato di un collega (rubrica importata): si sa che c'è, non chi è.
    section.appendChild(el('p', null, 'Contatto riservato di un altro operatore: la scheda non è visibile a te.'));
    if (!data.can_link) return section;
  } else if (data.contact) {
    const how = data.contact.match === 'manual' ? 'scelto manualmente'
      : (others.length ? `riconosciuto automaticamente: il contatto attivo creato per primo fra ${others.length + 1} con questo numero` : 'riconosciuto automaticamente dalla rubrica');
    section.append(el('p', null, `${data.contact.name || `Contatto n. ${data.contact.id}`} · ${how}.`),
      openButton('Apri scheda contatto', 'contatti', data.contact.id));
    if (!data.can_link) return section;
  } else {
    section.appendChild(el('p', 'muted', data.sender_profile_name
      ? `Nessun contatto in rubrica con questo numero. Nome del profilo WhatsApp: ${data.sender_profile_name}.`
      : 'Nessun contatto in rubrica con questo numero.'));
    if (!data.can_link) return section;
  }
  if (others.length) {
    section.appendChild(el('p', 'muted', data.contact ? 'Altri contatti hanno questo numero: puoi cambiare l’associazione.' : 'Più contatti hanno questo numero: scegli quello giusto.'));
    const select = el('select');
    select.setAttribute('data-messages-contact-candidates', '');
    select.appendChild(el('option', null, '— scegli il contatto —')).value = '';
    for (const c of others) {
      const note = [c.on_phone ? '' : 'secondo telefono', c.status && c.status !== 'active' ? `stato: ${c.status}` : ''].filter(Boolean).join(', ');
      const o = el('option', null, `${c.name || `Contatto n. ${c.id}`}${note ? ` (${note})` : ''}`);
      o.value = String(c.id);
      select.appendChild(o);
    }
    const link = button(data.contact ? 'Cambia contatto' : 'Collega conversazione', 'primary', 'data-messages-contact-link');
    link.addEventListener('click', () => { if (select.value) linkTo(Number(select.value)); });
    section.append(select, link);
  }
  if (!data.contact || !others.length) {
    const create = button('Nuovo contatto con questo numero', data.contact ? 'ghost' : 'primary', 'data-messages-contact-new');
    create.addEventListener('click', () => {
      const dialog = document.querySelector('[data-messages-contact-dialog]');
      openCreateContactDialog(dialog, async (created) => linkTo(created.id), { phone: `+${sender}`, source: 'whatsapp' });
    });
    section.appendChild(create);
  }
  section.appendChild(status);
  return section;
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

/** Collega un immobile (e, se serve, un altro contatto) a UN messaggio: /api/inbound-messages/{id}/link. */
function propertySection(m, candidates, onDone) {
  const section = el('div', 'wa-sheet-section');
  section.setAttribute('data-message-link-form', String(m.id));
  section.appendChild(el('h4', null, 'Immobile della richiesta'));
  section.appendChild(el('p', 'muted', m.property
    ? `Ultimo messaggio collegato a: ${m.property.title || m.property.code || `immobile n. ${m.property.id}`}.`
    : 'L’immobile non si deduce mai dal numero: collegalo qui, se la richiesta ne riguarda uno.'));
  const propertySearch = el('input'); propertySearch.type = 'search'; propertySearch.placeholder = 'Cerca immobile (titolo, indirizzo, codice)';
  const propertySelect = el('select'); propertySelect.name = 'property_id';
  propertySelect.appendChild(el('option', null, '— nessuno —')).value = '';
  if (m.property) {
    const o = el('option', null, m.property.title || m.property.code || `Immobile n. ${m.property.id}`);
    o.value = String(m.property.id); o.selected = true; propertySelect.appendChild(o);
  }
  propertySearch.addEventListener('change', () => searchOptions(propertySelect, `/api/property/properties?limit=20&search=${encodeURIComponent(propertySearch.value)}`,
    (p) => [p.code, p.title, p.city].filter(Boolean).join(' · ') || `Immobile n. ${p.id}`));
  const status = el('p', 'muted');
  status.setAttribute('role', 'status');
  const save = button('Salva', 'primary', 'data-message-link-save');
  save.addEventListener('click', async () => {
    save.disabled = true;
    try {
      const r = await apiPost(`${API}/${m.id}/link`, {
        contact_id: m.contact ? m.contact.id : null,
        property_id: propertySelect.value ? Number(propertySelect.value) : null,
      });
      onDone(r.changed ? 'Immobile collegato al messaggio.' : 'Nessuna modifica.');
    } catch (error) {
      status.textContent = errorMessage(error);
      status.className = 'error-box';
      save.disabled = false;
    }
  });
  if (m.property) section.appendChild(openButton('Apri immobile', 'immobili', m.property.id));
  section.append(propertySearch, propertySelect, save, status);
  return section;
}

/** `?inbox=<phone_number_id>`: la casella della conversazione, quando è nota. */
export function inboxQuery(phoneNumberId) {
  return phoneNumberId ? `?inbox=${encodeURIComponent(phoneNumberId)}` : '';
}

/** Il nome della casella da mostrare: etichetta, o il numero visibile. */
export function inboxLabel(inbox) {
  if (!inbox) return '';
  return inbox.label || (inbox.display_phone_number ? `+${inbox.display_phone_number}` : `Numero …${String(inbox.phone_number_id || '').slice(-4)}`);
}

/** Il nome del contatto della conversazione: mai quello di un contatto riservato di un collega. */
export function contactDisplayName(contact) {
  if (!contact) return null;
  if (contact.private) return 'Contatto riservato';
  return contact.name || `Contatto n. ${contact.id}`;
}

// ---------------------------------------------------------------------------
// Fumetti
// ---------------------------------------------------------------------------

function tick(r) {
  const s = r.delivery ? r.delivery.status : r.status;
  if (s === 'read') return { mark: '✓✓', cls: 'is-read', title: 'Letto' };
  if (s === 'delivered') return { mark: '✓✓', cls: '', title: 'Consegnato' };
  if (s === 'sent' && r.delivery) return { mark: '✓', cls: '', title: 'Inviato' };
  if (r.status === 'sent') return { mark: '✓', cls: '', title: 'Accettato da WhatsApp' };
  if (['failed', 'suppressed', 'indeterminate'].includes(r.status) || s === 'failed') return { mark: '!', cls: 'is-failed', title: r.status_label };
  return { mark: '🕓', cls: 'is-pending', title: r.status_label };
}

function bubbleIn(m, who) {
  const b = el('div', 'wa-bubble wa-bubble-in');
  b.setAttribute('data-message', String(m.id));
  if (!m.is_text) b.appendChild(el('span', 'wa-bubble-kind', m.message_type_label));
  b.appendChild(el('p', 'wa-bubble-text', messageSummary(m)));
  const meta = el('span', 'wa-bubble-meta', chatTimeText(m.provider_timestamp || m.received_at));
  meta.title = `${who} · ricevuto ${dateTimeText(m.received_at)}`;
  b.appendChild(meta);
  return b;
}

function bubbleOut(r) {
  const b = el('div', 'wa-bubble wa-bubble-out');
  b.setAttribute('data-reply', String(r.id));
  b.appendChild(el('p', 'wa-bubble-text', r.text));
  const t = tick(r);
  const meta = el('span', 'wa-bubble-meta');
  meta.append(el('span', null, chatTimeText(r.created_at)), el('span', `wa-tick ${t.cls}`, t.mark));
  meta.title = `${r.operator || 'Operatore'} · ${t.title}${r.delivery && r.delivery.at ? ` ${dateTimeText(r.delivery.at)}` : ''}`;
  b.appendChild(meta);
  if (['failed', 'suppressed', 'indeterminate'].includes(r.status)) {
    const why = el('span', 'wa-bubble-error', r.status === 'indeterminate'
      ? 'Esito incerto: verifica con il cliente prima di riscrivere.'
      : 'Non inviato.');
    b.appendChild(why);
  }
  return b;
}

// ---------------------------------------------------------------------------
// Vista
// ---------------------------------------------------------------------------

export async function renderMessaggi(container, params = []) {
  const root = el('div', 'wa-chat');
  root.setAttribute('data-messages', '');
  const dialog = el('dialog', 'modal');
  dialog.setAttribute('data-messages-contact-dialog', '');

  // --- colonna sinistra
  const side = el('aside', 'wa-side');
  const sideHead = el('div', 'wa-side-head');
  sideHead.appendChild(el('h2', 'wa-title', 'WhatsApp'));
  const search = el('input', 'wa-search');
  search.type = 'search';
  search.placeholder = 'Cerca chat';
  search.setAttribute('aria-label', 'Cerca chat');
  search.setAttribute('data-messages-search', '');
  const filters = el('div', 'chips wa-filters');
  const allBtn = button('Tutte', 'chip active', 'data-messages-filter-all');
  const unlinkedBtn = button('Da associare', 'chip', 'data-messages-filter-unlinked');
  filters.append(allBtn, unlinkedBtn);
  filters.hidden = true;
  // DUE CASELLE: il filtro per numero ricevente compare solo con più caselle visibili.
  const inboxFilters = el('div', 'chips wa-filters wa-inbox-filters');
  inboxFilters.setAttribute('data-messages-inboxes', '');
  inboxFilters.hidden = true;
  const list = el('div', 'wa-list');
  list.setAttribute('data-messages-list', '');
  sideHead.append(search, inboxFilters, filters);
  side.append(sideHead, list);

  // --- colonna destra
  const detail = el('section', 'wa-main');
  detail.setAttribute('data-messages-detail', '');
  root.append(side, detail, dialog);
  container.replaceChildren(root);
  // La chat occupa lo spazio fino al fondo della finestra: l'elenco e la
  // cronologia scorrono al loro interno, il campo di scrittura resta in basso.
  // Su smartphone la barra di navigazione in basso copre la pagina: si toglie la
  // sua altezza, cosi' il campo di scrittura resta sopra la barra.
  const bottomBar = () => {
    const nav = document.querySelector('.mobile-nav');
    if (!nav || typeof window.getComputedStyle !== 'function' || window.getComputedStyle(nav).display === 'none') return 0;
    return nav.getBoundingClientRect().height || 0;
  };
  const fit = () => {
    const top = root.getBoundingClientRect().top + window.scrollY;
    root.style.height = `${Math.max(420, window.innerHeight - top - 16 - bottomBar())}px`;
  };
  fit();
  window.addEventListener('resize', fit);

  let onlyUnlinked = false;
  let inboxFilter = null;          // phone_number_id della casella scelta, o null = tutte
  let inboxes = [];                // le caselle visibili (dal server)
  let items = [];
  let selected = params[0] && /^\d{6,15}$/.test(params[0]) ? params[0] : null;
  let selectedInbox = selected && params[1] && /^\d{5,20}$/.test(params[1]) ? params[1] : null;

  function nameOf(c) {
    const last = c.last_message;
    return (last && last.contact && contactDisplayName(last.contact)) || (last && last.sender_profile_name) || phoneText(c.sender_phone);
  }

  const isSelected = (c) => c.sender_phone === selected && (!selectedInbox || c.phone_number_id === selectedInbox);

  function renderInboxFilters() {
    inboxFilters.replaceChildren();
    if (inboxes.length < 2) { inboxFilters.hidden = true; return; }
    inboxFilters.hidden = false;
    const all = button('Tutte le caselle', `chip${inboxFilter ? '' : ' active'}`, 'data-messages-inbox-all');
    all.addEventListener('click', () => { inboxFilter = null; loadList(); });
    inboxFilters.appendChild(all);
    for (const ib of inboxes) {
      const b = button(inboxLabel(ib), `chip${inboxFilter === ib.phone_number_id ? ' active' : ''}`, 'data-messages-inbox');
      b.setAttribute('data-inbox', ib.phone_number_id);
      b.addEventListener('click', () => { inboxFilter = ib.phone_number_id; loadList(); });
      inboxFilters.appendChild(b);
    }
  }

  function renderList() {
    list.replaceChildren();
    const q = search.value.trim().toLowerCase();
    const digits = q.replace(/\D/g, '');
    const shown = items.filter((c) => !q || nameOf(c).toLowerCase().includes(q) || (digits && c.sender_phone.includes(digits))
      || messageSummary(c.last_message).toLowerCase().includes(q));
    if (!shown.length) {
      list.appendChild(el('p', 'muted wa-empty', q ? 'Nessuna chat trovata.' : (onlyUnlinked ? 'Nessuna chat da associare.' : 'Nessun messaggio ricevuto.')));
      return;
    }
    for (const c of shown) {
      const item = button('', 'wa-item', 'data-messages-conversation');
      item.setAttribute('data-sender', c.sender_phone);
      if (c.phone_number_id) item.setAttribute('data-inbox', c.phone_number_id);
      if (isSelected(c)) item.classList.add('active');
      if (c.unread) item.classList.add('unread');
      const who = nameOf(c);
      const avatar = el('span', 'wa-avatar', initials(who));
      const body = el('span', 'wa-item-body');
      const top = el('span', 'wa-item-top');
      top.append(el('strong', 'wa-item-name', who), el('span', 'wa-item-time', chatTimeText(c.last_received_at)));
      const bottom = el('span', 'wa-item-bottom');
      bottom.appendChild(el('span', 'wa-item-preview', messageSummary(c.last_message)));
      if (inboxes.length > 1 && c.inbox) {
        // Su quale numero è arrivata: solo quando l'agenzia ha più caselle visibili.
        const chip = el('span', 'wa-inbox-chip', inboxLabel(c.inbox));
        chip.setAttribute('data-messages-item-inbox', c.phone_number_id);
        bottom.appendChild(chip);
      }
      if (c.unread) {
        const badge = el('span', 'wa-unread', String(c.unread));
        badge.setAttribute('data-messages-unread', String(c.unread));
        bottom.appendChild(badge);
      } else if (c.without_contact) {
        bottom.appendChild(el('span', 'wa-dot', '').appendChild(document.createTextNode('')).parentNode);
        bottom.lastChild.title = 'Nessun contatto collegato';
      }
      body.append(top, bottom);
      item.append(avatar, body);
      item.addEventListener('click', () => {
        selected = c.sender_phone; selectedInbox = c.phone_number_id || null;
        renderList(); loadConversation(c.sender_phone, c.phone_number_id || null);
      });
      list.appendChild(item);
    }
  }

  async function loadList() {
    let data;
    const q = [];
    if (onlyUnlinked) q.push('only_unlinked=true');
    if (inboxFilter) q.push(`inbox=${encodeURIComponent(inboxFilter)}`);
    try {
      data = await apiGet(`${API}/conversations${q.length ? `?${q.join('&')}` : ''}`);
    } catch (error) {
      list.replaceChildren(el('div', 'error-box', errorMessage(error)));
      return;
    }
    items = data.items;
    inboxes = Array.isArray(data.inboxes) ? data.inboxes : [];
    renderInboxFilters();
    // «Da associare» compare solo se esiste davvero una chat senza contatto.
    if (!onlyUnlinked) filters.hidden = !items.some((c) => c.without_contact);
    renderList();
    const current = selected ? items.find(isSelected) : null;
    if (current) { selectedInbox = current.phone_number_id || selectedInbox; await loadConversation(selected, selectedInbox); }
    else {
      selected = null; selectedInbox = null;
      root.classList.remove('is-chat-open');
      detail.replaceChildren(el('div', 'wa-placeholder', 'Seleziona una chat per leggere e rispondere.'));
    }
  }

  async function loadConversation(sender, inbox, notice) {
    root.classList.add('is-chat-open');
    detail.replaceChildren(el('p', 'muted wa-loading', 'Caricamento…'));
    let data;
    try {
      data = await apiGet(`${API}/conversations/${encodeURIComponent(sender)}${inboxQuery(inbox)}`);
    } catch (error) {
      detail.replaceChildren(el('div', 'error-box', errorMessage(error)));
      return;
    }
    inbox = data.phone_number_id || inbox || null;    // la casella decisa dal server
    selectedInbox = inbox;
    // Letti per chi apre la chat: un errore (sola lettura, migrazione assente) non blocca la vista.
    apiPost(`${API}/conversations/${encodeURIComponent(sender)}/read${inboxQuery(inbox)}`, {}).then(() => {
      const item = list.querySelector(`[data-sender="${sender}"]${inbox ? `[data-inbox="${inbox}"]` : ''}`);
      if (item) {
        item.classList.remove('unread');
        const badge = item.querySelector('[data-messages-unread]');
        if (badge) badge.remove();
      }
      const c = items.find((x) => x.sender_phone === sender && (!inbox || x.phone_number_id === inbox));
      if (c) c.unread = 0;
    }).catch(() => {});

    let replies = [];
    try {
      replies = (await apiGet(`${REPLIES_API}/${encodeURIComponent(sender)}${inboxQuery(inbox)}`)).replies || [];
    } catch (error) {
      replies = [];
    }
    let auto = null;
    try { auto = await fetchAutoState(sender, inbox); } catch (error) { auto = null; }
    const decisions = new Map(((auto && auto.decisions) || []).map((d) => [d.message_id, d]));

    const who = contactDisplayName(data.contact) || data.sender_profile_name || phoneText(sender);
    detail.replaceChildren();

    // --- intestazione
    const head = el('header', 'wa-head');
    const back = button('‹', 'ghost wa-back', 'data-messages-back');
    back.setAttribute('aria-label', 'Torna alle chat');
    back.addEventListener('click', () => { selected = null; root.classList.remove('is-chat-open'); renderList(); });
    const titleBox = el('div', 'wa-head-title');
    titleBox.append(el('strong', 'wa-head-name', who));
    const subParts = [phoneText(sender)];
    if (!data.contact) subParts.push('nessun contatto collegato');
    // DUE CASELLE: su quale numero è arrivata la chat (e da quale parte la risposta).
    if (data.inbox && (inboxes.length > 1 || (data.other_inboxes || []).length)) subParts.push(`ricevuto su ${inboxLabel(data.inbox)}`);
    const sub = el('span', 'wa-head-sub', subParts.join(' · '));
    if (data.phone_number_id) sub.setAttribute('data-messages-head-inbox', data.phone_number_id);
    titleBox.appendChild(sub);
    titleBox.setAttribute('data-messages-head-contact', data.contact ? String(data.contact.id) : 'none');
    const actions = el('div', 'wa-head-actions');
    if (data.contact && !data.contact.private) actions.appendChild(openButton('Scheda', 'contatti', data.contact.id, 'ghost wa-head-btn'));
    else if (data.can_link) {
      const link = button('Collega contatto', 'ghost wa-head-btn', 'data-messages-contact-open');
      link.addEventListener('click', () => openSheet('contact'));
      actions.appendChild(link);
    }
    const more = button('⋯', 'ghost wa-head-btn wa-more', 'data-messages-menu');
    more.setAttribute('aria-label', 'Altre azioni');
    more.setAttribute('aria-haspopup', 'menu');
    const menu = el('div', 'wa-menu');
    menu.hidden = true;
    menu.setAttribute('role', 'menu');
    more.addEventListener('click', () => { menu.hidden = !menu.hidden; });
    actions.append(more, menu);
    head.append(back, el('span', 'wa-avatar wa-avatar-head', initials(who)), titleBox, actions);
    detail.appendChild(head);

    // --- avviso (esito di un'azione) e foglio delle azioni avanzate
    const noticeBox = el('div', 'wa-notice');
    noticeBox.setAttribute('role', 'status');
    noticeBox.setAttribute('data-messages-notice', '');
    if (notice) noticeBox.appendChild(el('div', notice.kind === 'ok' ? 'success-box' : 'error-box', notice.text));
    // Lo stesso cliente ha scritto anche a un altro numero: la chat è separata, il contatto è lo stesso.
    for (const other of data.other_inboxes || []) {
      const line = el('div', 'wa-other-inbox');
      line.setAttribute('data-messages-other-inbox', other.phone_number_id);
      const open = button(`Apri la chat su ${inboxLabel(other)}`, 'ghost', 'data-messages-open-other');
      open.addEventListener('click', () => { selected = sender; selectedInbox = other.phone_number_id; renderList(); loadConversation(sender, other.phone_number_id); });
      line.append(el('span', null, `Questo cliente ha scritto anche a ${inboxLabel(other)}.`), open);
      noticeBox.appendChild(line);
    }
    const sheet = el('div', 'wa-sheet');
    sheet.hidden = true;
    sheet.setAttribute('data-messages-sheet', '');
    detail.append(noticeBox, sheet);
    const refresh = async (msg) => { await loadList(); await loadConversation(sender, inbox, msg ? { kind: 'ok', text: msg } : undefined); };
    const lastIn = data.messages[0];     // il server li dà dal più recente
    const sections = {
      contact: () => contactSection(data, sender, (msg) => refresh(msg)),
      property: () => propertySection(lastIn, data.contact_candidates, (msg) => refresh(msg)),
      stime: () => stimeSection(data.stime),
      suggestion: () => {
        const card = el('div', 'wa-sheet-section');
        card.appendChild(el('h4', null, 'Suggerimento di risposta'));
        card.appendChild(el('p', 'muted', 'Testo proposto dai dati del CRM: lo rivedi tu e lo invii solo con la conferma.'));
        const m = lastIn;
        if (m && m.is_text) mountReplySuggestion(card, { messageId: m.id, getComposer: () => composer });
        return card;
      },
      auto: () => {
        const card = el('div', 'wa-sheet-section');
        card.setAttribute('data-messages-auto', '');
        card.appendChild(el('h4', null, 'Risposte automatiche'));
        if (!auto) { card.appendChild(el('p', 'muted', 'Non disponibili su questo ambiente.')); return card; }
        card.appendChild(renderAutoPanel(auto, sender, () => refresh(), inbox));
        for (const m of data.messages) {
          if (decisions.has(m.id)) card.appendChild(renderDecision(decisions.get(m.id)));
        }
        return card;
      },
    };
    function openSheet(key) {
      menu.hidden = true;
      const close = button('Chiudi', 'ghost wa-sheet-close', 'data-messages-sheet-close');
      close.addEventListener('click', () => { sheet.hidden = true; sheet.replaceChildren(); });
      sheet.replaceChildren(sections[key](), close);
      sheet.hidden = false;
      sheet.setAttribute('data-messages-sheet', key);
    }
    const menuItems = [];
    if (data.can_link) menuItems.push(['contact', data.contact ? 'Cambia contatto' : 'Collega contatto']);
    if (data.contact && !data.contact.private) menuItems.push(['card', 'Apri scheda contatto']);
    if (data.can_link && lastIn) menuItems.push(['property', 'Collega immobile']);
    menuItems.push(['stime', 'Stime suggerite']);
    if (lastIn && lastIn.is_text) menuItems.push(['suggestion', 'Suggerimento di risposta']);
    if (auto) menuItems.push(['auto', 'Risposte automatiche']);
    for (const [key, label] of menuItems) {
      const it = button(label, 'wa-menu-item');
      it.setAttribute('role', 'menuitem');
      it.setAttribute('data-messages-menu-item', key);
      it.addEventListener('click', () => (key === 'card' ? navigate('contatti', [String(data.contact.id)]) : openSheet(key)));
      menu.appendChild(it);
    }

    // --- cronologia: dal più vecchio al più recente, con il giorno come separatore
    const scroller = el('div', 'wa-messages');
    scroller.setAttribute('data-messages-timeline', '');
    const timeline = [
      ...data.messages.map((m) => ({ kind: 'in', at: m.provider_timestamp || m.received_at, m })),
      ...replies.map((r) => ({ kind: 'out', at: r.created_at, r })),
    ].sort((a, b) => String(a.at || '').localeCompare(String(b.at || '')));
    let lastDay = null;
    for (const entry of timeline) {
      const day = dayText(entry.at);
      if (day && day !== lastDay) { scroller.appendChild(el('div', 'wa-day', day)); lastDay = day; }
      scroller.appendChild(entry.kind === 'in' ? bubbleIn(entry.m, who) : bubbleOut(entry.r));
    }
    detail.appendChild(scroller);

    // --- compositore: sempre in basso. Le risposte partono solo dopo la tua conferma.
    let composer = null;
    composer = await mountWhatsappReply(detail, {
      senderPhone: sender, inbox, inboxName: data.inbox && (inboxes.length > 1 || (data.other_inboxes || []).length) ? inboxLabel(data.inbox) : null,
      history: 'pending', compact: true, recipientName: who,
      onChange: (n) => loadConversation(sender, inbox, n),
    });
    scroller.scrollTop = scroller.scrollHeight;
  }

  search.addEventListener('input', renderList);
  allBtn.addEventListener('click', () => { onlyUnlinked = false; allBtn.classList.add('active'); unlinkedBtn.classList.remove('active'); loadList(); });
  unlinkedBtn.addEventListener('click', () => { onlyUnlinked = true; unlinkedBtn.classList.add('active'); allBtn.classList.remove('active'); loadList(); });
  await loadList();
}
