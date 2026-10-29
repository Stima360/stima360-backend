// STIMA360 OS — components/owner-report.js
// REPORT-PROPRIETARIO-1: la scheda «Report proprietario» dell'immobile.
//
// Anteprima e PDF del report attivita' per il proprietario: richieste
// ricevute, visite effettuate, andamento mese per mese, prezzo richiesto,
// stato commerciale e indicazioni dell'agente. I dati li calcola il server
// (/api/owner-reports, agenzia dalla sessione); la pagina li mostra con
// `textContent`. Il PDF non viene inviato a nessuno: lo scarica il
// consulente, che lo condivide con il proprietario.

import { apiGet } from '../core/api-client.js';
import { sessionExpired } from '../core/auth.js';

const API = '/api/owner-reports';
const NOTES_MAX = 3000;

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

export function money(value) {
  if (value === null || value === undefined || value === '') return null;
  const n = Number(value);
  if (!Number.isFinite(n)) return null;
  return `${n.toLocaleString('it-IT', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €`;
}

export function dayText(value) {
  if (!value) return '—';
  const [y, m, d] = String(value).slice(0, 10).split('-');
  return `${d}/${m}/${y}`;
}

export function errorMessage(error) {
  const d = error && error.data && error.data.detail;
  if (d && typeof d === 'object' && !Array.isArray(d) && d.message) return d.message;
  if (Array.isArray(d)) return 'Alcuni dati non sono validi.';
  return (error && error.message) || 'Operazione non riuscita.';
}

function table(head, rows) {
  const t = el('table', 'st-table');
  const thead = el('thead');
  const tr = el('tr');
  for (const h of head) tr.appendChild(el('th', null, h));
  thead.appendChild(tr);
  const tbody = el('tbody');
  for (const r of rows) {
    const row = el('tr');
    for (const c of r) row.appendChild(el('td', null, c));
    tbody.appendChild(row);
  }
  t.append(thead, tbody);
  const wrap = el('div', 'table-wrap');
  wrap.appendChild(t);
  return wrap;
}

function facts(rows) {
  const dl = el('dl', 'owner-report-facts');
  for (const [k, v] of rows) {
    if (v === null || v === undefined || v === '') continue;
    const row = el('div', 'owner-report-fact');
    row.append(el('dt', null, k), el('dd', null, v));
    dl.appendChild(row);
  }
  return dl;
}

export function renderPreview(r) {
  const root = el('div', 'owner-report-preview');
  root.setAttribute('data-owner-report-preview', '');
  const p = r.property;
  const m = r.mandate;
  root.appendChild(el('h4', null, 'Immobile e incarico'));
  root.appendChild(facts([
    ['Immobile', p.title], ['Indirizzo', p.address], ['Stato commerciale', p.commercial_status_label],
    ['Prezzo richiesto', money(p.asking_price) || 'Non indicato'],
    ['Incarico', m.exists ? m.state_label : 'Nessun incarico registrato'],
    ['Inizio incarico', m.start ? dayText(m.start) : null], ['Scadenza incarico', m.end ? dayText(m.end) : null],
  ]));
  root.appendChild(el('h4', null, `Attività dal ${dayText(r.period.from)} al ${dayText(r.period.to)}`));
  const cards = el('div', 'owner-report-cards');
  for (const [label, value, attr] of [
    ['Richieste ricevute', r.inquiries.total, 'inquiries'], ['Visite effettuate', r.visits.completed, 'visits'],
    ['Visite in programma', r.visits.upcoming, 'upcoming'], ['Proposte di acquisto', r.proposals.total, 'proposals'],
  ]) {
    const card = el('div', 'owner-report-card');
    card.setAttribute('data-owner-report-count', attr);
    card.append(el('strong', null, String(value)), el('span', 'muted', label));
    cards.appendChild(card);
  }
  root.appendChild(cards);
  root.appendChild(el('h4', null, 'Andamento mese per mese'));
  root.appendChild(table(['Mese', 'Richieste', 'Visite'], r.monthly.map((x) => [x.label, String(x.inquiries), String(x.visits)])));
  root.appendChild(el('h4', null, 'Prezzo richiesto'));
  root.appendChild(r.price_history.length
    ? table(['Data', 'Prezzo precedente', 'Nuovo prezzo'], r.price_history.map((x) => [dayText(x.date), money(x.old_price) || '—', money(x.new_price) || '—']))
    : el('p', 'muted', 'Nessuna variazione del prezzo richiesto nel periodo.'));
  root.appendChild(el('h4', null, 'Stato commerciale'));
  root.appendChild(r.status_history.length
    ? table(['Data', 'Da', 'A'], r.status_history.map((x) => [dayText(x.date), x.old_label || '—', x.new_label || '—']))
    : el('p', 'muted', 'Nessun cambio di stato commerciale nel periodo.'));
  return root;
}

export async function mountOwnerReport(container, { propertyId }) {
  const root = el('div', 'owner-report-tab');
  root.setAttribute('data-owner-report', '');
  root.appendChild(el('h3', 'section-title', 'Report attività per il proprietario'));
  root.appendChild(el('p', 'muted owner-report-notice',
    'Il report riassume richieste, visite, prezzo e stato dell’immobile. Il PDF non viene inviato: scaricalo e condividilo tu con il proprietario. Note interne, dati degli acquirenti e importi delle proposte non vengono mai inclusi.'));
  const notice = el('div');
  notice.setAttribute('role', 'status');
  notice.setAttribute('data-owner-report-notice', '');

  const form = el('div', 'owner-report-form');
  const from = el('input'); from.type = 'date'; from.name = 'date_from';
  const to = el('input'); to.type = 'date'; to.name = 'date_to';
  const notes = el('textarea'); notes.name = 'agent_notes'; notes.rows = 5; notes.maxLength = NOTES_MAX;
  const counter = el('span', 'muted owner-report-counter', `0/${NOTES_MAX}`);
  notes.addEventListener('input', () => { counter.textContent = `${notes.value.length}/${NOTES_MAX}`; });
  const field = (label, input, extra, wide) => {
    const wrap = el('label', wide ? 'owner-report-field owner-report-wide' : 'owner-report-field');
    wrap.appendChild(el('span', null, label));
    wrap.appendChild(input);
    if (extra) wrap.appendChild(extra);
    return wrap;
  };
  const refresh = button('Aggiorna anteprima', 'ghost', 'data-owner-report-refresh');
  const download = button('Scarica PDF', 'primary', 'data-owner-report-download');
  const actions = el('div', 'agency-actions owner-report-wide');
  actions.append(refresh, download);
  form.append(field('Dal', from), field('Al', to),
    field('Indicazioni dell’agente per il proprietario (facoltative)', notes, counter, true), actions);
  const previewBox = el('div', 'owner-report-preview-box');
  root.append(notice, form, previewBox);
  container.replaceChildren(root);

  function say(message, kind) {
    notice.replaceChildren();
    if (message) notice.appendChild(el('div', kind === 'ok' ? 'success-box' : 'error-box', message));
  }

  async function load() {
    refresh.disabled = true;
    const q = new URLSearchParams();
    if (from.value) q.set('date_from', from.value);
    if (to.value) q.set('date_to', to.value);
    try {
      const r = await apiGet(`${API}/${encodeURIComponent(propertyId)}${q.toString() ? `?${q}` : ''}`);
      from.value = r.period.from;
      to.value = r.period.to;
      previewBox.replaceChildren(renderPreview(r));
      say('');
      return true;
    } catch (error) {
      previewBox.replaceChildren();
      say(errorMessage(error), 'error');
      return false;
    } finally {
      refresh.disabled = false;
    }
  }

  async function downloadPdf() {
    download.disabled = true;
    download.textContent = 'Preparazione PDF…';
    try {
      let response;
      try {
        response = await fetch(`${API}/${encodeURIComponent(propertyId)}/pdf`, {
          method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ date_from: from.value || null, date_to: to.value || null, agent_notes: notes.value || null }),
        });
      } catch (_networkError) {
        throw new Error('Impossibile contattare il server. Verifica la connessione.');
      }
      if (response.status === 401) {
        sessionExpired();
        throw new Error('Sessione scaduta. Effettua di nuovo il login.');
      }
      if (!response.ok) {
        let data = null;
        try { data = await response.json(); } catch (_e) { data = null; }
        const error = new Error(`Errore ${response.status}`);
        error.data = data;
        throw error;
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `report-proprietario-immobile-${propertyId}-${from.value}-${to.value}.pdf`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
      say('PDF del report scaricato: puoi condividerlo con il proprietario.', 'ok');
    } catch (error) {
      say(`PDF non generato. ${errorMessage(error)}`, 'error');
    } finally {
      download.disabled = false;
      download.textContent = 'Scarica PDF';
    }
  }

  refresh.addEventListener('click', load);
  download.addEventListener('click', downloadPdf);
  await load();
}
