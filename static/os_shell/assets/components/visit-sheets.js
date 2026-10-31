// STIMA360 OS — components/visit-sheets.js
// BLOCCO-6A: «Fogli visita» nella scheda Visite dell'immobile.
//
// Elenca le visite dell'immobile (stessa visibilità dell'Agenda) con i fogli
// già generati; «Genera foglio visita» crea la versione successiva solo se i
// dati sono cambiati. Il foglio distingue visita prenotata e visita svolta
// (esito registrato) e non è mai un documento firmato. Il download passa dal
// client autenticato. Tutto via `textContent`.
//
// BLOCCO-6B: per ogni foglio, le richieste di firma. Oggi l'unico fornitore è
// l'adattatore SIMULATO: ogni richiesta e ogni esito portano la scritta
// «SIMULAZIONE — nessuna firma valida» e la visita non risulta mai firmata.

import { apiGet, apiPost } from '../core/api-client.js';

const API = '/api/visit-sheets';
const STATUS = { scheduled: 'Fissata', confirmed: 'Confermata', completed: 'Svolta', requested: 'Richiesta',
  cancelled: 'Annullata', no_show: 'Non avvenuta', rescheduled: 'Riprogrammata' };

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

function when(iso) {
  return new Date(iso).toLocaleString('it-IT', { timeZone: 'Europe/Rome', day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function errorText(error) {
  const d = error && error.data && error.data.detail;
  if (d && typeof d === 'object' && !Array.isArray(d) && d.message) return d.message;
  return (error && error.message) || 'Operazione non riuscita.';
}

const ESIGN = '/api/signature-requests';

function newKey() {
  if (crypto.randomUUID) return crypto.randomUUID();
  const b = new Uint8Array(16);
  crypto.getRandomValues(b);
  b[6] = (b[6] & 0x0f) | 0x40; b[8] = (b[8] & 0x3f) | 0x80;
  const h = [...b].map((x) => x.toString(16).padStart(2, '0')).join('');
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

async function downloadFrom(url, filename, holder) {
  try {
    const response = await fetch(url, { credentials: 'include' });
    if (!response.ok) throw new Error('Download non riuscito.');
    const blob = await response.blob();
    const href = URL.createObjectURL(blob);
    const a = el('a');
    a.href = href; a.download = filename;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(href), 1000);
  } catch (error) {
    holder.appendChild(el('div', 'error-box', errorText(error)));
  }
}

export function signatureLine(r) {
  const parts = [r.status_label, `firmatario indicato: ${r.signer_name}`];
  if (r.simulation) parts.unshift('SIMULAZIONE — nessuna firma valida');
  return parts.join(' · ');
}

async function mountSignatures(holder, sheet) {
  const box = el('div', 'visit-sign');
  box.setAttribute('data-signatures', String(sheet.id));
  holder.appendChild(box);
  async function load() {
    box.replaceChildren();
    let data;
    try { data = await apiGet(`${ESIGN}?visit_sheet_id=${sheet.id}`); } catch (error) {
      if (error && error.status === 503) return;
      box.appendChild(el('div', 'error-box', errorText(error)));
      return;
    }
    for (const r of data.items) {
      const line = el('div', 'visit-sheet-line');
      line.setAttribute('data-signature', String(r.id));
      line.appendChild(el('span', r.simulation ? 'badge badge-gray' : null, signatureLine(r)));
      if (r.status === 'sent') {
        for (const [label, action, body] of [['Annulla', 'cancel', {}],
          ...(r.simulation ? [['Simula completata', 'simulate', { outcome: 'completed' }], ['Simula rifiutata', 'simulate', { outcome: 'declined' }]] : [])]) {
          const b = button(label, 'ghost', `data-signature-${action}`);
          b.addEventListener('click', async () => {
            b.disabled = true;
            try { await apiPost(`${ESIGN}/${r.id}/${action}`, body); await load(); } catch (error) { box.appendChild(el('div', 'error-box', errorText(error))); }
          });
          line.appendChild(b);
        }
      }
      if (r.has_document) {
        const d = button(r.simulation ? 'Scarica ricevuta di simulazione' : 'Scarica documento', 'ghost', 'data-signature-document');
        d.addEventListener('click', () => downloadFrom(`${ESIGN}/${r.id}/document`,
          `${r.simulation ? 'SIMULAZIONE-non-firmato' : 'documento'}-richiesta-${r.id}.pdf`, box));
        line.appendChild(d);
      }
      box.appendChild(line);
    }
    const ask = button('Richiedi firma (simulazione)', 'ghost', 'data-signature-new');
    ask.addEventListener('click', () => {
      const form = el('div', 'visit-sign-form');
      const name = el('input', 'input'); name.placeholder = 'Nome del firmatario'; name.maxLength = 120;
      const contact = el('input', 'input'); contact.placeholder = 'Email o telefono'; contact.maxLength = 200;
      const send = button('Crea richiesta simulata', 'primary', 'data-signature-create');
      const key = newKey();
      send.addEventListener('click', async () => {
        send.disabled = true;
        try {
          await apiPost(ESIGN, { visit_sheet_id: sheet.id, signer_name: name.value, signer_contact: contact.value,
            expires_in_days: 7, idempotency_key: key });
          await load();
        } catch (error) { send.disabled = false; form.appendChild(el('div', 'error-box', errorText(error))); }
      });
      form.append(el('p', 'muted', 'Simulazione: nessun messaggio viene inviato e nessuna firma valida viene prodotta.'), name, contact, send);
      ask.replaceWith(form);
    });
    box.appendChild(ask);
  }
  await load();
}

async function downloadPdf(sheet, holder) {
  try {
    const response = await fetch(`${API}/${sheet.id}/pdf`, { credentials: 'include' });
    if (!response.ok) throw new Error('Download non riuscito.');
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const a = el('a');
    a.href = url;
    a.download = `foglio-visita-${sheet.appointment_id}-v${sheet.version}.pdf`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) {
    holder.appendChild(el('div', 'error-box', errorText(error)));
  }
}

export async function mountVisitSheets(container, { propertyId }) {
  const root = el('section', 'visit-sheets');
  root.setAttribute('data-visit-sheets', '');
  container.appendChild(root);

  async function load(notice) {
    root.replaceChildren(el('h3', null, 'Fogli visita'));
    root.appendChild(el('p', 'muted', 'Bozza di modello da revisionare. Una visita risulta svolta solo dopo l’esito registrato in agenda; il foglio non è un documento firmato.'));
    if (notice) root.appendChild(notice);
    let data;
    try {
      data = await apiGet(`${API}?property_id=${encodeURIComponent(String(propertyId))}`);
    } catch (error) {
      root.appendChild(el('div', 'error-box', errorText(error)));
      return;
    }
    if (!data.items.length) {
      root.appendChild(el('p', 'muted', 'Nessuna visita in agenda per questo immobile.'));
      return;
    }
    const list = el('ul', 'visit-sheets-list');
    for (const v of data.items) {
      const item = el('li');
      item.setAttribute('data-visit-sheet-visit', String(v.appointment_id));
      item.append(el('strong', null, when(v.start_at)), el('span', 'muted', ` · ${STATUS[v.status] || v.status}`));
      for (const s of v.sheets) {
        const line = el('div', 'visit-sheet-line');
        line.append(el('span', null, `Versione ${s.version} · ${s.visit_state_label} · ${when(s.generated_at)}`));
        const dl = button('Scarica PDF', 'ghost', 'data-visit-sheet-download');
        dl.addEventListener('click', () => downloadPdf(s, item));
        line.appendChild(dl);
        item.appendChild(line);
        mountSignatures(item, s).catch(() => {});
      }
      if (v.can_generate) {
        const gen = button('Genera foglio visita', 'ghost', 'data-visit-sheet-generate');
        gen.addEventListener('click', async () => {
          gen.disabled = true;
          try {
            const out = await apiPost(`${API}/appointments/${v.appointment_id}`, {});
            await load(el('div', 'success-box', out.created
              ? `Foglio generato (versione ${out.sheet.version}).`
              : `Nessun dato cambiato: resta la versione ${out.sheet.version}.`));
          } catch (error) {
            gen.disabled = false;
            item.appendChild(el('div', 'error-box', errorText(error)));
          }
        });
        item.appendChild(gen);
      }
      list.appendChild(item);
    }
    root.appendChild(list);
  }

  await load();
}
