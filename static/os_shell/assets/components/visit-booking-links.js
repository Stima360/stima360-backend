// STIMA360 OS — components/visit-booking-links.js
// BLOCCO-5A: «Prenotazione online delle visite» nella scheda Visite dell'immobile.
//
// Crea, elenca e disattiva i collegamenti pubblici di prenotazione di UNA
// visita per questo immobile (/api/visit-booking-links). Il server pretende un
// incarico in corso, un agente con orario configurato e l'autorizzazione del
// proprietario ESPLICITA. Il collegamento completo si vede una volta sola,
// alla creazione: poi resta solo la sua impronta. Tutto via `textContent`.

import { apiGet, apiPost } from '../core/api-client.js';

const API = '/api/visit-booking-links';

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
  if (d && typeof d === 'object' && !Array.isArray(d) && d.message) return d.message;
  return (error && error.message) || 'Operazione non riuscita.';
}

function field(label, input) {
  const wrap = el('label', 'form-field');
  wrap.append(el('span', null, label), input);
  return wrap;
}

function numberInput(name, value, min, max) {
  const input = el('input', 'input');
  input.type = 'number'; input.name = name; input.value = String(value); input.min = String(min); input.max = String(max);
  return input;
}

export async function mountVisitBookingLinks(container, { propertyId }) {
  const root = el('section', 'visit-booking-links');
  root.setAttribute('data-visit-booking-links', '');
  container.appendChild(root);

  async function load(notice) {
    root.replaceChildren(el('h3', null, 'Prenotazione online delle visite'));
    root.appendChild(el('p', 'muted', 'Un collegamento permette a un interessato di scegliere un orario libero dell’agente. '
      + 'Serve un incarico in corso e l’autorizzazione del proprietario.'));
    if (notice) root.appendChild(notice);
    let data;
    try {
      data = await apiGet(`${API}?property_id=${encodeURIComponent(String(propertyId))}`);
    } catch (error) {
      root.appendChild(el('div', 'error-box', errorText(error)));
      return;
    }
    const list = el('ul', 'visit-booking-list');
    for (const link of data.items) {
      const item = el('li', null);
      item.setAttribute('data-visit-booking-link', String(link.id));
      item.append(el('strong', null, link.status === 'active' ? 'Attivo' : 'Disattivato'),
        el('span', 'muted', ` · ${link.duration_minutes} min, orari ogni ${link.step_minutes} min, spostamento ${link.travel_minutes} min · ${link.owner_authorization_label}`));
      if (link.status !== 'active' && link.disabled_reason === 'owner_authorization_revoked') {
        item.appendChild(el('p', 'muted', `Autorizzazione revocata dal proprietario: ${link.disabled_note || ''}`));
      }
      if (link.status === 'active') {
        const off = button('Disattiva', 'ghost', 'data-visit-booking-disable');
        off.addEventListener('click', () => {
          // BLOCCO-6: il motivo si registra; la revoca del proprietario vuole una nota
          const form = el('div', 'visit-booking-disable-form');
          const reason = el('select', 'input'); reason.name = 'disabled_reason';
          for (const [v, label] of [['operator', 'Scelta dell’agenzia'], ['owner_authorization_revoked', 'Il proprietario ha revocato l’autorizzazione alle visite']]) {
            const o = el('option', null, label); o.value = v; reason.appendChild(o);
          }
          const note = el('input', 'input'); note.name = 'disabled_note'; note.maxLength = 1000;
          note.placeholder = 'Come e quando (obbligatoria se il proprietario ha revocato)';
          const confirm = button('Conferma disattivazione', 'danger', 'data-visit-booking-disable-confirm');
          confirm.addEventListener('click', async () => {
            confirm.disabled = true;
            try {
              const res = await apiPost(`${API}/${link.id}/disable`, { reason: reason.value, note: note.value || null });
              const msg = res && res.pending_owner_requests
                ? el('div', 'error-box', `Collegamento disattivato. Restano ${res.pending_owner_requests} visite richieste in attesa: gestiscile in Agenda.`)
                : null;
              await load(msg);
            } catch (error) { confirm.disabled = false; form.appendChild(el('div', 'error-box', errorText(error))); }
          });
          form.append(reason, note, confirm);
          off.replaceWith(form);
        });
        item.appendChild(off);
      }
      list.appendChild(item);
    }
    if (!data.items.length) root.appendChild(el('p', 'muted', 'Nessun collegamento creato.'));
    root.appendChild(list);
    const open = button('Nuovo collegamento', 'primary', 'data-visit-booking-new');
    open.addEventListener('click', () => { open.replaceWith(createForm()); });
    root.appendChild(open);
  }

  function createForm() {
    const form = el('div', 'visit-booking-form');
    // Solo gli agenti che il server accetta: membri attivi con orario
    // settimanale configurato. Nessuna scelta implicita se sono piu' d'uno.
    const agent = el('select', 'input'); agent.name = 'assigned_user_id';
    const agentHint = el('p', 'muted');
    agentHint.setAttribute('data-visit-booking-agent-hint', '');
    const agentsReady = apiGet(`${API}/agents?property_id=${encodeURIComponent(propertyId)}`).then((r) => {
      const items = (r && r.items) || [];
      if (items.length !== 1) {
        const pick = el('option', null, items.length ? '— scegli l’agente —' : 'Nessun agente disponibile');
        pick.value = ''; agent.appendChild(pick);
      }
      for (const a of items) {
        const o = el('option', null, a.is_me ? `${a.name} (tu)` : a.name); o.value = String(a.id);
        agent.appendChild(o);
      }
      if (items.length === 1) agent.value = String(items[0].id);
      agentHint.textContent = items.length
        ? 'Sono elencati solo gli agenti attivi con un orario settimanale configurato.'
        : 'Nessun agente ha un orario settimanale configurato: impostalo in Agenda per creare un collegamento.';
      return items.length;
    }).catch((e) => { agentHint.textContent = errorText(e); return 0; });
    const duration = numberInput('duration_minutes', 45, 15, 240);
    const step = el('select', 'input'); step.name = 'step_minutes';
    for (const v of [15, 30, 60]) { const o = el('option', null, `${v} minuti`); o.value = String(v); if (v === 30) o.selected = true; step.appendChild(o); }
    const travel = numberInput('travel_minutes', 15, 0, 120);
    const notice = numberInput('min_notice_hours', 12, 1, 168);
    const horizon = numberInput('horizon_days', 21, 1, 60);
    const owner = el('select', 'input'); owner.name = 'owner_authorization';
    const empty = el('option', null, '— scegli —'); empty.value = ''; owner.appendChild(empty);
    for (const [v, label] of [['preauthorized', 'Il proprietario ha autorizzato le visite negli orari dell’agente'],
      ['per_visit', 'Ogni visita richiede l’assenso del proprietario (resta una richiesta)']]) {
      const o = el('option', null, label); o.value = v; owner.appendChild(o);
    }
    const note = el('input', 'input'); note.name = 'owner_authorization_note'; note.maxLength = 500;
    note.placeholder = 'Come e quando il proprietario ha dato l’autorizzazione';
    const error = el('div');
    const save = button('Crea collegamento', 'primary', 'data-visit-booking-save');
    const cancel = button('Annulla', 'ghost');
    save.addEventListener('click', async () => {
      if (!(await agentsReady) || !agent.value) { error.replaceChildren(el('div', 'error-box', 'Scegli un agente con orario configurato.')); return; }
      if (!owner.value) { error.replaceChildren(el('div', 'error-box', 'Indica l’autorizzazione del proprietario.')); return; }
      save.disabled = true;
      try {
        const link = await apiPost(API, {
          property_id: Number(propertyId), assigned_user_id: Number(agent.value),
          duration_minutes: Number(duration.value), step_minutes: Number(step.value), travel_minutes: Number(travel.value),
          min_notice_minutes: Number(notice.value) * 60, horizon_days: Number(horizon.value),
          owner_authorization: owner.value, owner_authorization_note: note.value || null,
        });
        const box = el('div', 'success-box');
        box.setAttribute('data-visit-booking-created', '');
        const url = window.location.origin + link.url;
        box.append(el('p', null, 'Collegamento creato. Copialo adesso: non sarà più mostrato.'), el('code', null, url));
        await load(box);
      } catch (e) {
        save.disabled = false;
        error.replaceChildren(el('div', 'error-box', errorText(e)));
      }
    });
    cancel.addEventListener('click', () => load());
    const actions = el('div', 'agency-actions'); actions.append(save, cancel);
    form.append(field('Agente', agent), agentHint, field('Durata della visita (minuti)', duration), field('Orari ogni', step),
      field('Tempo di spostamento prima e dopo (minuti)', travel), field('Preavviso minimo (ore)', notice),
      field('Prenotabile fino a (giorni)', horizon), field('Autorizzazione del proprietario', owner),
      field('Nota sull’autorizzazione', note), error, actions);
    return form;
  }

  await load();
}
