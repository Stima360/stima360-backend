// STIMA360 OS — views/caselle-whatsapp.js
// DUE CASELLE: Impostazioni → «Caselle WhatsApp». Le caselle sono i numeri
// WhatsApp che il server instrada a questa agenzia (WHATSAPP_INBOUND_ROUTES);
// qui si decidono solo etichetta, tipo, automazioni e accesso
// (GET/PUT /api/whatsapp-inboxes, PUT .../access). Configura il titolare: per
// gli altri la pagina è in sola lettura. Nessun invio parte da qui e nessun
// token passa dal browser. Tutto il testo passa da `textContent`.

import { apiGet, apiPut } from '../core/api-client.js';

const API = '/api/whatsapp-inboxes';

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
  if (Array.isArray(d)) return 'Alcuni dati non sono validi.';
  return (error && error.message) || 'Operazione non riuscita.';
}

export const KIND_LABELS = { cloud_api: 'Numero API (Cloud API)', coexistence: 'Numero personale (app + API)' };
export const ACCESS_LABELS = { agency: 'Tutta l’agenzia (come oggi)', restricted: 'Solo il titolare e gli operatori autorizzati' };

/** Una riga di stato leggibile per una casella. */
export function inboxSummary(ib) {
  const parts = [];
  parts.push(ib.automations_enabled ? 'automazioni attive' : 'senza automazioni');
  parts.push(ib.access_mode === 'restricted' ? 'accesso riservato' : 'accesso di agenzia');
  if (ib.status === 'disabled') parts.push('disattivata');
  if (!ib.routed) parts.push('non instradata dal server');
  if (!ib.configured) parts.push('impostazioni predefinite');
  return parts.join(' · ');
}

function select(name, options, value) {
  const s = el('select', 'input');
  s.name = name;
  for (const [k, label] of Object.entries(options)) {
    const o = el('option', null, label);
    o.value = k;
    if (k === value) o.selected = true;
    s.appendChild(o);
  }
  return s;
}

function field(label, control) {
  const wrap = el('label', 'form-field');
  wrap.appendChild(el('span', null, label));
  wrap.appendChild(control);
  return wrap;
}

export async function renderCaselleWhatsapp(container) {
  const root = el('div', 'card panel wa-inboxes');
  root.setAttribute('data-wa-inboxes', '');
  container.replaceChildren(root);
  root.appendChild(el('h3', 'section-title', 'Caselle WhatsApp'));
  root.appendChild(el('p', 'muted', 'Una casella è un numero WhatsApp dell’agenzia. Le conversazioni restano separate per numero e la risposta parte dallo stesso numero che ha ricevuto. Le automazioni (risposte automatiche, prenotazione visita) si attivano casella per casella.'));
  const status = el('div');
  status.setAttribute('role', 'status');
  status.setAttribute('data-wa-inboxes-status', '');
  const list = el('div', 'wa-inboxes');
  list.setAttribute('data-wa-inboxes-list', '');
  root.append(status, list);

  let members = null;

  async function loadMembers() {
    if (members) return members;
    try {
      members = (await apiGet(`${API}/members`)).items || [];
    } catch (error) {
      members = [];
    }
    return members;
  }

  function card(ib, data) {
    const c = el('div', 'card wa-inbox-card');
    c.setAttribute('data-wa-inbox', ib.phone_number_id);
    const head = el('div', 'wa-inbox-head');
    const shown = ib.display_phone_number ? `+${ib.display_phone_number}` : null;
    const title = shown && shown !== ib.label ? `${ib.label} · ${shown}` : ib.label;
    head.append(el('strong', null, title), el('span', 'muted', inboxSummary(ib)));
    c.appendChild(head);
    if (!data.can_configure) {
      c.appendChild(el('p', 'muted', ib.kind === 'coexistence' ? KIND_LABELS.coexistence : KIND_LABELS.cloud_api));
      return c;
    }
    const form = el('div', 'wa-inbox-form');
    const label = el('input', 'input');
    label.name = 'label'; label.maxLength = 60; label.value = ib.label || '';
    const kind = select('kind', KIND_LABELS, ib.kind || 'cloud_api');
    const access = select('access_mode', ACCESS_LABELS, ib.access_mode || 'agency');
    const auto = el('input'); auto.type = 'checkbox'; auto.name = 'automations_enabled'; auto.checked = !!ib.automations_enabled;
    const autoLabel = el('label', 'checkbox-label');
    autoLabel.append(auto, document.createTextNode(' Automazioni attive su questo numero'));
    const active = el('input'); active.type = 'checkbox'; active.name = 'active'; active.checked = ib.status !== 'disabled';
    const activeLabel = el('label', 'checkbox-label');
    activeLabel.append(active, document.createTextNode(' Casella attiva'));
    form.append(field('Etichetta', label), field('Tipo', kind), field('Accesso', access), autoLabel, activeLabel);
    c.appendChild(form);
    if (!ib.routed) c.appendChild(el('p', 'wa-reply-error', 'Questo numero non è più instradato dal server: le impostazioni restano ma non si possono salvare.'));

    // Operatori autorizzati: solo con accesso riservato (il titolare vede sempre).
    const membersBox = el('div', 'wa-inbox-members');
    membersBox.setAttribute('data-wa-inbox-members', '');
    membersBox.hidden = access.value !== 'restricted';
    const granted = new Set((ib.access || []).map((a) => a.user_id));
    const checks = [];
    const fillMembers = async () => {
      membersBox.replaceChildren(el('span', 'muted', 'Operatori autorizzati (oltre al titolare): '));
      const items = await loadMembers();
      if (!items.length) membersBox.appendChild(el('span', 'muted', 'nessun operatore da autorizzare.'));
      for (const m of items) {
        if (m.role === 'agency_owner') continue;
        const cb = el('input'); cb.type = 'checkbox'; cb.value = String(m.id); cb.checked = granted.has(m.id);
        cb.setAttribute('data-wa-inbox-member', String(m.id));
        const l = el('label', 'checkbox-label');
        l.append(cb, document.createTextNode(` ${m.name}`));
        membersBox.appendChild(l);
        checks.push(cb);
      }
    };
    if (!membersBox.hidden) fillMembers();
    access.addEventListener('change', () => { membersBox.hidden = access.value !== 'restricted'; if (!membersBox.hidden && !checks.length) fillMembers(); });
    c.appendChild(membersBox);

    const msg = el('p', 'muted');
    msg.setAttribute('role', 'status');
    const save = button('Salva casella', 'primary', 'data-wa-inbox-save');
    save.disabled = !ib.routed;
    save.addEventListener('click', async () => {
      save.disabled = true;
      msg.textContent = 'Salvataggio…'; msg.className = 'muted';
      try {
        const body = { label: label.value.trim(), kind: kind.value, access_mode: access.value,
          automations_enabled: auto.checked, status: active.checked ? 'active' : 'disabled' };
        let out = await apiPut(`${API}/${encodeURIComponent(ib.phone_number_id)}`, body);
        if (access.value === 'restricted') {
          const ids = checks.filter((x) => x.checked).map((x) => Number(x.value));
          out = await apiPut(`${API}/${encodeURIComponent(ib.phone_number_id)}/access`, { user_ids: ids });
        }
        render(out, 'Casella salvata.');
      } catch (error) {
        msg.textContent = errorMessage(error); msg.className = 'error-box';
        save.disabled = false;
      }
    });
    c.append(save, msg);
    return c;
  }

  function render(data, notice) {
    status.replaceChildren();
    if (notice) status.appendChild(el('div', 'success-box', notice));
    if (!data.installed) status.appendChild(el('div', 'error-box', 'Le caselle WhatsApp non sono ancora attive su questo ambiente (migrazione 130).'));
    else if (!data.can_configure) status.appendChild(el('p', 'muted', 'Solo il titolare dell’agenzia può modificare le caselle.'));
    list.replaceChildren();
    if (!data.items.length) {
      list.appendChild(el('p', 'muted', 'Nessun numero WhatsApp instradato a questa agenzia: la configurazione del server (WHATSAPP_INBOUND_ROUTES) non ne elenca.'));
      return;
    }
    for (const ib of data.items) list.appendChild(card(ib, data));
  }

  try {
    render(await apiGet(API));
  } catch (error) {
    status.replaceChildren(el('div', 'error-box', errorMessage(error)));
  }
}
