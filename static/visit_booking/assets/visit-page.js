// STIMA360 — /visita/{token} e /visita-gestione/{chiave} (BLOCCO-5A).
//
// La pagina non sa nulla: chiede tutto all'API pubblica
// `/api/public/visit-booking/...`, che decide validità, orari prenotabili ed
// esito. Mostra solo orari prenotabili, in ora di Roma; la conferma a schermo
// è quella restituita dal server (una richiesta in attesa dell'assenso del
// proprietario NON è presentata come confermata). Tutto via `textContent`.

const API = '/api/public/visit-booking';
const ZONE = 'Europe/Rome';
const app = document.getElementById('visit-app');
const path = window.location.pathname;
const managing = path.startsWith('/visita-gestione/');
const secret = decodeURIComponent(path.split('/')[2] || '');

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function button(label, className) {
  const b = el('button', `btn ${className || ''}`.trim(), label);
  b.type = 'button';
  return b;
}

function newKey() {
  if (crypto.randomUUID) return crypto.randomUUID();
  const b = new Uint8Array(16);
  crypto.getRandomValues(b);
  b[6] = (b[6] & 0x0f) | 0x40; b[8] = (b[8] & 0x3f) | 0x80;
  const h = [...b].map((x) => x.toString(16).padStart(2, '0')).join('');
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

export function dayLabel(iso) {
  return new Date(iso).toLocaleDateString('it-IT', { timeZone: ZONE, weekday: 'long', day: 'numeric', month: 'long' });
}

export function timeLabel(iso) {
  return new Date(iso).toLocaleTimeString('it-IT', { timeZone: ZONE, hour: '2-digit', minute: '2-digit' });
}

export function dayKey(iso) {
  return new Date(iso).toLocaleDateString('en-CA', { timeZone: ZONE });
}

async function call(method, url, body) {
  const response = await fetch(url, {
    method, headers: { 'Content-Type': 'application/json' }, credentials: 'omit',
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try { data = await response.json(); } catch (_e) { data = null; }
  if (!response.ok) {
    const d = data && data.detail;
    const error = new Error((d && d.message) || 'Operazione non riuscita. Riprova.');
    error.status = response.status;
    throw error;
  }
  return data;
}

function show(...nodes) { app.replaceChildren(...nodes); }

function notice(text, kind) {
  const n = el('p', `notice ${kind === 'error' ? 'notice-error' : 'notice-status'}`, text);
  n.setAttribute('role', kind === 'error' ? 'alert' : 'status');
  return n;
}

function slotPicker(fetchSlots, onPick) {
  const panel = el('section', 'panel');
  panel.append(el('h1', 'step-title', 'Scegli un orario'), el('p', 'hint', 'Orari in ora italiana. Sono mostrati solo gli orari prenotabili.'));
  const nav = el('div', 'week-nav');
  const prev = button('‹ Precedente', 'btn-ghost');
  const next = button('Successiva ›', 'btn-ghost');
  const range = el('span', 'week-range');
  nav.append(prev, range, next);
  const list = el('div', 'visit-days');
  panel.append(nav, list);
  let offset = 0;
  async function load() {
    list.replaceChildren(el('p', 'muted', 'Caricamento…'));
    const from = new Date(Date.now() + offset * 7 * 86400000);
    const to = new Date(from.getTime() + 7 * 86400000);
    range.textContent = `${from.toLocaleDateString('it-IT', { timeZone: ZONE })} – ${to.toLocaleDateString('it-IT', { timeZone: ZONE })}`;
    prev.disabled = offset === 0;
    let data;
    try {
      data = await fetchSlots(from.toISOString(), to.toISOString());
    } catch (error) {
      list.replaceChildren(notice(error.message, 'error'));
      return;
    }
    list.replaceChildren();
    if (!data.slots.length) {
      list.appendChild(el('p', 'empty-state', 'Nessun orario prenotabile in questa settimana.'));
      return;
    }
    const days = new Map();
    for (const s of data.slots) {
      const k = dayKey(s.start_at);
      if (!days.has(k)) days.set(k, []);
      days.get(k).push(s);
    }
    for (const slots of days.values()) {
      const day = el('div', 'day');
      day.appendChild(el('p', 'section-label', dayLabel(slots[0].start_at)));
      const grid = el('div', 'slot-grid');
      for (const s of slots) {
        const b = button(timeLabel(s.start_at), 'slot-btn');
        b.setAttribute('data-slot', s.start_at);
        b.addEventListener('click', () => onPick(s));
        grid.appendChild(b);
      }
      day.appendChild(grid);
      list.appendChild(day);
    }
  }
  prev.addEventListener('click', () => { offset = Math.max(0, offset - 1); load(); });
  next.addEventListener('click', () => { offset += 1; load(); });
  load();
  return panel;
}

function outcome(result, title) {
  const panel = el('section', 'panel success-panel');
  panel.setAttribute('data-visit-outcome', result.state);
  panel.append(el('h1', 'step-title', title || (result.state === 'confirmed' ? 'Visita confermata' : 'Esito della prenotazione')),
    el('p', null, result.message),
    el('p', 'visit-when', `${dayLabel(result.start_at)}, ore ${timeLabel(result.start_at)}–${timeLabel(result.end_at)} (ora italiana)`));
  if (result.manage_url) {
    panel.append(el('p', 'hint', 'Per annullare o spostare la visita conserva questo indirizzo:'));
    const a = el('a', null, window.location.origin + result.manage_url);
    a.href = result.manage_url;
    a.setAttribute('data-manage-link', '');
    panel.appendChild(a);
  }
  return panel;
}

async function bookingFlow() {
  let info;
  try {
    info = await call('GET', `${API}/${encodeURIComponent(secret)}`);
  } catch (error) {
    show(el('section', 'panel', null), notice(error.message, 'error'));
    return;
  }
  const head = el('section', 'panel');
  head.append(el('h1', 'booking-title', `Visita: ${info.property.type_label}${info.property.city ? ` a ${info.property.city}` : ''}`),
    el('p', 'agent-line', `${info.agency_name || 'Agenzia'} · durata ${info.duration_minutes} minuti`));
  if (info.owner_approval_required) {
    head.appendChild(notice('Questa visita richiede l’assenso del proprietario: la tua prenotazione sarà una richiesta, confermata dall’agenzia solo dopo il suo consenso.', 'status'));
  }
  const key = newKey();
  function form(slot) {
    const panel = el('section', 'panel');
    panel.append(el('h1', 'step-title', 'Conferma i tuoi dati'),
      el('p', 'visit-when', `${dayLabel(slot.start_at)}, ore ${timeLabel(slot.start_at)} (ora italiana)`));
    const f = el('form', 'form');
    const field = (label, input) => { const w = el('label', 'field'); w.append(el('span', null, label), input); return w; };
    const name = el('input', 'input'); name.name = 'name'; name.required = true; name.maxLength = 120; name.autocomplete = 'name';
    const phone = el('input', 'input'); phone.name = 'phone'; phone.type = 'tel'; phone.maxLength = 40; phone.autocomplete = 'tel';
    const email = el('input', 'input'); email.name = 'email'; email.type = 'email'; email.maxLength = 200; email.autocomplete = 'email';
    const privacy = el('input'); privacy.type = 'checkbox'; privacy.name = 'privacy';
    const privacyLabel = el('label', 'field'); privacyLabel.append(privacy, el('span', null, ' Acconsento al trattamento dei dati per organizzare la visita.'));
    const error = el('div');
    const submit = button('Prenota la visita', 'btn-primary'); submit.type = 'submit';
    const back = button('Scegli un altro orario', 'btn-ghost');
    const actions = el('div', 'actions'); actions.append(submit, back);
    f.append(field('Nome e cognome', name), field('Telefono', phone), field('Email', email), privacyLabel, error, actions);
    back.addEventListener('click', () => show(head, slotPicker(slotsFor, form)));
    f.addEventListener('submit', async (event) => {
      event.preventDefault();
      if (!privacy.checked) { error.replaceChildren(notice('Serve il consenso per organizzare la visita.', 'error')); return; }
      submit.disabled = true; submit.textContent = 'Invio in corso…';
      try {
        const result = await call('POST', `${API}/${encodeURIComponent(secret)}/book`, {
          name: name.value, phone: phone.value || null, email: email.value || null,
          start_at: slot.start_at, key, privacy_accepted: true,
        });
        show(head, outcome(result));
      } catch (e) {
        submit.disabled = false; submit.textContent = 'Prenota la visita';
        error.replaceChildren(notice(e.message, 'error'));
      }
    });
    panel.appendChild(f);
    show(head, panel);
  }
  const slotsFor = (from, to) => call('GET', `${API}/${encodeURIComponent(secret)}/slots?date_from=${encodeURIComponent(from)}&date_to=${encodeURIComponent(to)}`);
  show(head, slotPicker(slotsFor, form));
}

async function manageFlow() {
  const base = `${API}/manage/${encodeURIComponent(secret)}`;
  let state;
  try {
    state = await call('GET', base);
  } catch (error) {
    show(notice(error.message, 'error'));
    return;
  }
  const panel = outcome(state, 'La tua visita');
  const actions = el('div', 'actions');
  if (state.can_cancel) {
    const cancel = button('Annulla la visita', 'btn-ghost');
    cancel.addEventListener('click', async () => {
      if (!window.confirm('Vuoi annullare la visita?')) return;
      cancel.disabled = true;
      try { await call('POST', `${base}/cancel`); manageFlow(); } catch (e) { panel.appendChild(notice(e.message, 'error')); cancel.disabled = false; }
    });
    actions.appendChild(cancel);
  }
  if (state.can_reschedule) {
    const move = button('Sposta a un altro orario', 'btn-primary');
    move.addEventListener('click', () => {
      const key = newKey();
      show(panel, slotPicker((from, to) => call('GET', `${base}/slots?date_from=${encodeURIComponent(from)}&date_to=${encodeURIComponent(to)}`), async (slot) => {
        try {
          const result = await call('POST', `${base}/reschedule`, { start_at: slot.start_at, key });
          show(outcome(result, 'Visita spostata'));
        } catch (e) {
          app.appendChild(notice(e.message, 'error'));
        }
      }));
    });
    actions.appendChild(move);
  }
  panel.appendChild(actions);
  show(panel);
}

if (managing) manageFlow(); else bookingFlow();
