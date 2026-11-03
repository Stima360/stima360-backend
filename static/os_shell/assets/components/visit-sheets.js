// STIMA360 OS — components/visit-sheets.js
// BLOCCO-6A: «Fogli visita» nella scheda Visite dell'immobile.
//
// Elenca le visite dell'immobile (stessa visibilità dell'Agenda) con i fogli
// già generati; «Genera foglio visita» crea la versione successiva solo se i
// dati sono cambiati. Il foglio distingue visita prenotata e visita svolta
// (esito registrato) e non è mai un documento firmato. Il download passa dal
// client autenticato. Tutto via `textContent`.
//
// BLOCCO-6B: per ogni foglio, le richieste di firma. Con l'adattatore SIMULATO ogni
// richiesta e ogni esito portano la scritta «SIMULAZIONE — nessuna firma valida».
//
// DOCUMENSO: con il servizio reale configurato la richiesta ha DUE firmatari (cliente e
// agente). Il servizio NON scrive a nessuno: l'operatore apre i collegamenti personali o
// mostra il QR al cliente, che firma dal telefono. Il livello è sempre «firma elettronica
// semplice» e in ambiente di prova la firma non è utilizzabile. Tutto via `textContent`.

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
  // BLOCCO-5: una firma dell'ambiente di prova del fornitore non e' valida
  else if (r.provider_environment === 'sandbox') parts.unshift('AMBIENTE DI PROVA DEL FORNITORE — nessuna firma valida');
  // condizione tecnica, non giudizio legale
  if (r.status === 'signed') parts.push(r.valid_signature
    ? `firmata nell'ambiente reale del fornitore (${r.signature_level_label || `livello dichiarato: ${(r.signature_level || '').toUpperCase()}`})`
    : 'firma di prova, non utilizzabile');
  return parts.join(' · ');
}

function safeHref(url) {
  try {
    const u = new URL(url);
    return (u.protocol === 'https:' || u.protocol === 'http:') ? u.href : null;
  } catch (e) { return null; }
}

function signersBlock(r) {
  const list = el('ul', 'visit-sign-signers');
  for (const s of r.signers || []) {
    list.appendChild(el('li', null, `${s.role_label}: ${s.name} — ${s.status_label}`));
  }
  return list;
}

/** I collegamenti personali: segreti, caricati solo su richiesta e mai tenuti nella pagina oltre a questa vista. */
async function showLinks(r, holder) {
  holder.replaceChildren();
  let data;
  try { data = await apiGet(`${ESIGN}/${r.id}/signing-links`); } catch (error) {
    holder.appendChild(el('div', 'error-box', errorText(error)));
    return;
  }
  holder.appendChild(el('p', 'muted', 'Il servizio di firma non invia nulla. Apri il collegamento o fai inquadrare il QR al cliente: '
    + 'chi lo apre firma al posto di quella persona, quindi non condividerlo.'));
  for (const item of data.items) {
    const row = el('div', 'visit-sign-link');
    row.appendChild(el('strong', null, `${item.role_label}: ${item.name}`));
    row.appendChild(el('span', 'badge badge-gray', item.status_label));
    const href = item.url ? safeHref(item.url) : null;
    if (href) {
      const open = el('a', 'btn ghost', 'Apri per firmare');
      open.href = href; open.target = '_blank'; open.rel = 'noopener noreferrer';
      open.setAttribute('data-signature-open', item.role);
      const copy = button('Copia collegamento', 'ghost', 'data-signature-copy');
      copy.addEventListener('click', async () => {
        try { await navigator.clipboard.writeText(href); copy.textContent = 'Copiato'; } catch (e) { copy.textContent = 'Copia non riuscita'; }
      });
      row.append(open, copy);
      if (item.role === 'visitor') {
        const qr = el('img', 'visit-sign-qr');
        qr.alt = 'QR del collegamento di firma del cliente';
        qr.width = 180; qr.height = 180;
        qr.src = `${ESIGN}/${r.id}/signing-links/visitor/qr.svg`;
        qr.setAttribute('data-signature-qr', '');
        row.appendChild(qr);
      }
    }
    holder.appendChild(row);
  }
}

async function mountSignatures(holder, sheet, badge) {
  const box = el('div', 'visit-sign');
  box.setAttribute('data-signatures', String(sheet.id));
  holder.appendChild(box);
  let config = { signers: false };
  try { config = await apiGet(`${ESIGN}/config/provider`); } catch (error) { /* senza configurazione resta la simulazione */ }
  async function load() {
    box.replaceChildren();
    let data;
    try { data = await apiGet(`${ESIGN}?visit_sheet_id=${sheet.id}`); } catch (error) {
      if (error && error.status === 503) return;
      box.appendChild(el('div', 'error-box', errorText(error)));
      return;
    }
    if (badge) {                                                 // lo stato del foglio segue la richiesta REALE (mai la simulazione)
      const real = data.items.filter((x) => !x.simulation);
      const signed = real.find((x) => x.status === 'signed');
      const waiting = real.find((x) => x.status === 'sent');
      if (signed) {
        badge.textContent = signed.valid_signature ? 'Firmato (firma elettronica semplice)' : 'Firmato in prova — non utilizzabile';
        badge.setAttribute('data-visit-sheet-signature-state', signed.valid_signature ? 'signed' : 'signed_test');
      } else if (waiting) {
        badge.textContent = 'In attesa di firma';
        badge.setAttribute('data-visit-sheet-signature-state', 'pending');
      }
    }
    for (const r of data.items) {
      const line = el('div', 'visit-sheet-line');
      line.setAttribute('data-signature', String(r.id));
      line.appendChild(el('span', r.simulation ? 'badge badge-gray' : null, signatureLine(r)));
      if (r.signers && r.signers.length) line.appendChild(signersBlock(r));
      const linksHolder = el('div', 'visit-sign-links');
      if (r.status === 'sent') {
        if (!r.simulation && r.signers && r.signers.length) {
          const links = button('Collegamenti di firma', 'primary', 'data-signature-links');
          links.addEventListener('click', () => showLinks(r, linksHolder));
          const refresh = button('Aggiorna stato', 'ghost', 'data-signature-refresh');
          refresh.addEventListener('click', async () => {
            refresh.disabled = true;
            try { await apiPost(`${ESIGN}/${r.id}/refresh`, {}); await load(); } catch (error) { refresh.disabled = false; box.appendChild(el('div', 'error-box', errorText(error))); }
          });
          line.append(links, refresh);
        }
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
      if (r.audit_trail_sha256) {
        const t = button('Scarica rapporto del fornitore', 'ghost', 'data-signature-audit');
        t.addEventListener('click', () => downloadFrom(`${ESIGN}/${r.id}/audit-trail`, `rapporto-firma-richiesta-${r.id}.pdf`, box));
        line.appendChild(t);
      }
      if (r.has_document) {
        const d = button(r.simulation ? 'Scarica ricevuta di simulazione' : 'Scarica documento', 'ghost', 'data-signature-document');
        d.addEventListener('click', () => downloadFrom(`${ESIGN}/${r.id}/document`,
          `${r.simulation ? 'SIMULAZIONE-non-firmato' : 'documento'}-richiesta-${r.id}.pdf`, box));
        line.appendChild(d);
      }
      line.appendChild(linksHolder);
      box.appendChild(line);
    }
    const real = Boolean(config && config.signers);
    const open = (data.items || []).some((r) => r.status === 'sent');
    if (real && open) return;                                   // una sola richiesta aperta per foglio
    if (!real && config && config.configured && !config.simulation) return;   // servizio reale senza firmatari multipli: non gestito qui
    const ask = button(real ? 'Richiedi firma elettronica' : 'Richiedi firma (simulazione)', real ? 'primary' : 'ghost', 'data-signature-new');
    ask.addEventListener('click', () => {
      const form = el('div', 'visit-sign-form');
      const name = el('input', 'input'); name.placeholder = 'Nome del firmatario'; name.maxLength = 120;
      const contact = el('input', 'input'); contact.placeholder = real ? 'Email del cliente' : 'Email o telefono'; contact.maxLength = 200;
      if (real) contact.type = 'email';
      const send = button(real ? 'Crea richiesta di firma' : 'Crea richiesta simulata', 'primary', 'data-signature-create');
      const key = newKey();
      send.addEventListener('click', async () => {
        send.disabled = true;
        try {
          await apiPost(ESIGN, { visit_sheet_id: sheet.id, signer_name: name.value, signer_contact: contact.value,
            expires_in_days: 7, idempotency_key: key });
          await load();
        } catch (error) { send.disabled = false; form.appendChild(el('div', 'error-box', errorText(error))); }
      });
      form.append(el('p', 'muted', real
        ? 'Firma elettronica SEMPLICE, con il cliente e l\u2019agente della visita. L\u2019email serve solo a identificare il cliente presso il '
          + 'servizio di firma, che non gli scrive: nessun messaggio viene inviato. ' + (config.environment === 'production'
          ? 'Ambiente reale.' : 'AMBIENTE DI PROVA: la firma non sar\u00e0 utilizzabile.')
        : 'Simulazione: nessun messaggio viene inviato e nessuna firma valida viene prodotta.'), name, contact, send);
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

/**
 * Il blocco di UNA visita: i suoi fogli (versione, stato, firma, scarico, richieste di
 * firma) e «Genera foglio visita». Lo usano la scheda dell'immobile e la scheda
 * dell'appuntamento in Agenda: un solo componente, nessuna copia. `reload(notice)`
 * ridisegna il contenitore dopo una generazione. Con `compact` non ripete la data.
 */
export function buildVisitItem(v, reload, { compact = false } = {}) {
  const item = el('li');
  item.setAttribute('data-visit-sheet-visit', String(v.appointment_id));
  if (!compact) item.append(el('strong', null, when(v.start_at)), el('span', 'muted', ` · ${STATUS[v.status] || v.status}`));
  for (const s of v.sheets) {
    const line = el('div', 'visit-sheet-line');
    // ROADMAP VISITE (fase C): lo stato della firma in chiaro, mai una firma simulata come valida.
    line.append(el('span', null, `Versione ${s.version} · ${s.visit_state_label} · ${when(s.generated_at)}`));
    const firma = el('span', 'badge badge-gray', s.signature_label || 'Da firmare');
    firma.setAttribute('data-visit-sheet-signature-state', s.signature_state || 'unsigned');
    line.appendChild(firma);
    const dl = button('Scarica PDF', 'ghost', 'data-visit-sheet-download');
    dl.addEventListener('click', () => downloadPdf(s, item));
    line.appendChild(dl);
    item.appendChild(line);
    mountSignatures(item, s, firma).catch(() => {});
  }
  if (v.can_generate) {
    const gen = button('Genera foglio visita', 'ghost', 'data-visit-sheet-generate');
    gen.addEventListener('click', async () => {
      gen.disabled = true;
      try {
        const out = await apiPost(`${API}/appointments/${v.appointment_id}`, {});
        await reload(el('div', 'success-box', out.created
          ? `Foglio generato (versione ${out.sheet.version}).`
          : `Nessun dato cambiato: resta la versione ${out.sheet.version}.`));
      } catch (error) {
        gen.disabled = false;
        item.appendChild(el('div', 'error-box', errorText(error)));
      }
    });
    item.appendChild(gen);
  }
  return item;
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
    for (const v of data.items) list.appendChild(buildVisitItem(v, load));
    root.appendChild(list);
  }

  await load();
}

/**
 * ROADMAP VISITE (fase C): il foglio visita DALLA SCHEDA DELL'APPUNTAMENTO in Agenda.
 * Legge i fogli della sola visita (`GET /api/visit-sheets/appointments/{id}`), li
 * elenca con versione, stato e «Da firmare», e permette di generare o scaricare il
 * PDF. Stessa API, stessi permessi e stesso componente della scheda dell'immobile.
 */
export async function mountAppointmentSheet(container, { appointmentId }) {
  const root = el('section', 'visit-sheets visit-sheets-appointment');
  root.setAttribute('data-appointment-sheet', String(appointmentId));
  container.appendChild(root);

  async function load(notice) {
    root.replaceChildren(el('h3', 'section-title', 'Foglio visita'));
    root.appendChild(el('p', 'muted', 'Bozza di modello da revisionare: il foglio non è un documento firmato. '
      + 'La firma elettronica reale è una fase separata: finché non è operativa lo stato è «Da firmare».'));
    if (notice) root.appendChild(notice);
    let data;
    try {
      data = await apiGet(`${API}/appointments/${encodeURIComponent(String(appointmentId))}`);
    } catch (error) {
      if (error && (error.status === 404 || error.status === 503)) {
        root.replaceChildren();
        return;
      }
      root.appendChild(el('div', 'error-box', errorText(error)));
      return;
    }
    const list = el('ul', 'visit-sheets-list');
    list.appendChild(buildVisitItem(data, load, { compact: true }));
    root.appendChild(list);
    if (!data.can_generate && !data.sheets.length) {
      root.appendChild(el('p', 'muted', 'Il foglio si genera per una visita fissata, confermata o svolta.'));
    }
  }

  await load();
  return root;
}
