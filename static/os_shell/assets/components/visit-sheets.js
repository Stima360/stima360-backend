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
// FIRMA OTP STIMA360: il riquadro «Firma elettronica OTP» (FES). L'agente invia la richiesta
// (email con il collegamento personale al cliente), vede lo stato dal server, reinvia o copia
// il collegamento, revoca, firma come agente (password + dichiarazione), scarica documento e
// rapporto delle prove, verifica l'integrità. Il livello è sempre «firma elettronica semplice».
// Tutto via `textContent`.

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
  if (r.otp) {
    const parts = [r.state_label || r.status_label, `procedimento ${r.procedure || r.provider_reference || ''}`];
    if (r.status === 'signed') parts.push(r.signature_level_label || 'Firma elettronica semplice (FES)');
    return parts.join(' · ');
  }
  const parts = [r.status_label, `firmatario indicato: ${r.signer_name}`];
  if (r.simulation) parts.unshift('SIMULAZIONE — nessuna firma valida');
  else if (r.provider_environment === 'sandbox') parts.unshift('AMBIENTE DI PROVA — nessuna firma valida');
  if (r.status === 'signed') parts.push(r.valid_signature
    ? (r.signature_level_label || 'firmata')
    : 'firma di prova, non utilizzabile');
  return parts.join(' · ');
}

function signersBlock(r) {
  const list = el('ul', 'visit-sign-signers');
  for (const s of r.signers || []) {
    const parts = [`${s.role_label}: ${s.name} — ${s.status_label}`];
    if (s.signed_at) parts.push(when(s.signed_at));
    list.appendChild(el('li', null, parts.join(' · ')));
  }
  return list;
}

const EMAIL_STATUS = { queued: 'in coda', sending: 'in invio', sent: 'accettata dal server di posta', failed: 'invio fallito',
  indeterminate: 'esito incerto', suppressed: 'soppressa', cancelled: 'annullata' };

function linkBlock(r) {
  if (!r.link) return el('p', 'muted', 'Nessun collegamento ancora inviato al cliente.');
  const mail = r.link.email;
  const testo = [`Collegamento del ${when(r.link.issued_at)}`, r.link.valid ? 'valido' : 'non più valido',
    mail ? `email ${EMAIL_STATUS[mail.status] || mail.status}${mail.error_code ? ` (${mail.error_code})` : ''}` : 'nessuna email'];
  return el('p', 'muted', testo.join(' · '));
}

async function showEvents(r, holder) {
  holder.replaceChildren();
  let data;
  try { data = await apiGet(`${ESIGN}/${r.id}/events`); } catch (error) { holder.appendChild(el('div', 'error-box', errorText(error))); return; }
  const list = el('ol', 'visit-sign-events');
  for (const e of data.items) {
    const det = Object.entries(e.details || {}).filter(([k, v]) => v !== null && v !== '' && !/hash|sha256/.test(k)).map(([k, v]) => `${k}: ${v}`).join(', ');
    list.appendChild(el('li', null, `${when(e.occurred_at)} — ${e.event_type} (${e.actor_kind})${det ? ` — ${det}` : ''}`));
  }
  holder.appendChild(list);
  holder.appendChild(el('p', data.integrity.ok ? 'success-box' : 'error-box',
    data.integrity.ok ? `Catena delle prove integra: ${data.integrity.count} voci.` : `Catena delle prove NON integra: ${data.integrity.problems.join('; ')}`));
}

function agentSignForm(r, box, load) {
  const form = el('div', 'visit-sign-form');
  form.setAttribute('data-agent-sign-form', '');
  form.appendChild(el('p', 'muted', 'Firmi come agente assegnato alla visita, con la tua sessione: conferma la password e accetta la dichiarazione. '
    + 'La tua firma è separata da quella del cliente e ha le sue prove.'));
  const consent = el('label', 'muted');
  const check = el('input'); check.type = 'checkbox'; check.setAttribute('data-agent-consent', '');
  consent.append(check, el('span', null, ' Dichiaro di aver letto il foglio visita in questa versione e di volerlo sottoscrivere con firma elettronica semplice.'));
  const pwd = el('input', 'input'); pwd.type = 'password'; pwd.placeholder = 'La tua password'; pwd.autocomplete = 'current-password';
  pwd.setAttribute('data-agent-password', '');
  const go = button('Firma come agente', 'primary', 'data-agent-sign');
  go.disabled = true;
  const update = () => { go.disabled = !(check.checked && pwd.value.length > 0); };
  check.addEventListener('change', update); pwd.addEventListener('input', update);
  go.addEventListener('click', async () => {
    go.disabled = true;
    try {
      await apiPost(`${ESIGN}/${r.id}/sign-as-agent`, { password: pwd.value, consent_accepted: check.checked === true, consent_version: 'dichiarazione_firma_otp_v1' });
      await load();
    } catch (error) { update(); form.appendChild(el('div', 'error-box', errorText(error))); }
  });
  form.append(consent, pwd, go);
  return form;
}

async function mountSignatures(holder, sheet, badge) {
  const box = el('div', 'visit-sign');
  box.setAttribute('data-signatures', String(sheet.id));
  holder.appendChild(box);
  let config = { otp: false, simulation: true };
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
        badge.textContent = 'Sottoscritto (firma elettronica semplice)';
        badge.setAttribute('data-visit-sheet-signature-state', 'signed');
      } else if (waiting) {
        badge.textContent = waiting.state_label || 'In attesa di firma';
        badge.setAttribute('data-visit-sheet-signature-state', 'pending');
      }
    }
    if (config.otp) box.appendChild(el('h4', 'visit-sign-title', 'Firma elettronica OTP'));
    for (const r of data.items) {
      const line = el('div', 'visit-sheet-line');
      line.setAttribute('data-signature', String(r.id));
      line.setAttribute('data-signature-state', r.state || r.status);
      line.appendChild(el('span', r.simulation ? 'badge badge-gray' : 'badge', signatureLine(r)));
      if (r.signers && r.signers.length) line.appendChild(signersBlock(r));
      if (r.otp) line.appendChild(linkBlock(r));
      const extra = el('div', 'visit-sign-extra');
      if (r.status === 'sent') {
        if (r.otp) {
          if (r.can_resend) {
            const resend = button('Reinvia link', 'ghost', 'data-signature-resend');
            resend.addEventListener('click', async () => {
              resend.disabled = true;
              try { await apiPost(`${ESIGN}/${r.id}/links`, { send: true }); await load(); } catch (error) { resend.disabled = false; box.appendChild(el('div', 'error-box', errorText(error))); }
            });
            const copy = button('Copia link', 'ghost', 'data-signature-copy');
            copy.addEventListener('click', async () => {
              copy.disabled = true;
              try {
                const out = await apiPost(`${ESIGN}/${r.id}/links`, { send: false });
                try { await navigator.clipboard.writeText(out.url); copy.textContent = 'Link copiato (il precedente non vale più)'; }
                catch (e) { extra.appendChild(el('p', 'muted', `Collegamento: ${out.url}`)); copy.textContent = 'Link generato'; }
                await showStateOnly();
              } catch (error) { copy.disabled = false; box.appendChild(el('div', 'error-box', errorText(error))); }
            });
            line.append(resend, copy);
          }
          if (r.can_agent_sign) {
            const agent = button('Firma come agente', 'primary', 'data-signature-agent');
            agent.addEventListener('click', () => { extra.replaceChildren(agentSignForm(r, box, load)); });
            line.appendChild(agent);
          }
          const refresh = button('Verifica stato', 'ghost', 'data-signature-refresh');
          refresh.addEventListener('click', load);
          line.appendChild(refresh);
        }
        const cancel = button(r.otp ? 'Revoca richiesta' : 'Annulla', 'ghost', 'data-signature-cancel');
        cancel.addEventListener('click', async () => {
          cancel.disabled = true;
          try { await apiPost(`${ESIGN}/${r.id}/cancel`, {}); await load(); } catch (error) { box.appendChild(el('div', 'error-box', errorText(error))); }
        });
        line.appendChild(cancel);
        if (r.simulation) {
          for (const [label, body] of [['Simula completata', { outcome: 'completed' }], ['Simula rifiutata', { outcome: 'declined' }]]) {
            const b = button(label, 'ghost', 'data-signature-simulate');
            b.addEventListener('click', async () => {
              b.disabled = true;
              try { await apiPost(`${ESIGN}/${r.id}/simulate`, body); await load(); } catch (error) { box.appendChild(el('div', 'error-box', errorText(error))); }
            });
            line.appendChild(b);
          }
        }
      }
      if (r.has_document) {
        const d = button(r.simulation ? 'Scarica ricevuta di simulazione' : 'Scarica documento sottoscritto', 'ghost', 'data-signature-document');
        d.addEventListener('click', () => downloadFrom(`${ESIGN}/${r.id}/document`,
          r.simulation ? `SIMULAZIONE-non-firmato-richiesta-${r.id}.pdf` : `foglio-visita-sottoscritto-${r.procedure || r.id}.pdf`, box));
        line.appendChild(d);
      }
      if (r.has_audit_trail) {
        const t = button('Scarica rapporto delle prove', 'ghost', 'data-signature-audit');
        t.addEventListener('click', () => downloadFrom(`${ESIGN}/${r.id}/audit-trail`, `rapporto-prove-${r.procedure || r.id}.pdf`, box));
        line.appendChild(t);
      }
      if (r.otp) {
        const ev = button('Prove', 'ghost', 'data-signature-events');
        const evHolder = el('div', 'visit-sign-events-holder');
        ev.addEventListener('click', () => showEvents(r, evHolder));
        const verify = button('Verifica integrità', 'ghost', 'data-signature-verify');
        verify.addEventListener('click', async () => {
          verify.disabled = true;
          try {
            const out = await apiGet(`${ESIGN}/${r.id}/verify`);
            evHolder.replaceChildren(el('p', out.ok ? 'success-box' : 'error-box',
              out.checks.map((c) => `${c.name}: ${c.ok ? 'OK' : 'NON VALIDO'}`).join(' · ')));
          } catch (error) { evHolder.replaceChildren(el('div', 'error-box', errorText(error))); }
          verify.disabled = false;
        });
        line.append(ev, verify, evHolder);
      }
      line.appendChild(extra);
      box.appendChild(line);
    }
    async function showStateOnly() { /* il link copiato resta visibile: non si ridisegna subito */ }
    const open = (data.items || []).some((r) => r.status === 'sent');
    if (config.otp && open) return;                             // una sola richiesta aperta per foglio
    if (config.otp && config.otp_ready === false) {
      box.appendChild(el('div', 'error-box', `Firma OTP non configurata su questo servizio: ${config.otp_problem || ''}`));
      return;
    }
    if (config.configured === false) return;
    const ask = button(config.otp ? 'Invia per firma OTP' : 'Richiedi firma (simulazione)', config.otp ? 'primary' : 'ghost', 'data-signature-new');
    ask.addEventListener('click', () => {
      const form = el('div', 'visit-sign-form');
      const name = el('input', 'input'); name.placeholder = 'Nome e cognome del cliente'; name.maxLength = 120; name.setAttribute('data-signature-name', '');
      const contact = el('input', 'input'); contact.placeholder = config.otp ? 'Email del cliente' : 'Email o telefono'; contact.maxLength = 200;
      contact.setAttribute('data-signature-contact', '');
      if (config.otp) contact.type = 'email';
      const send = button(config.otp ? 'Invia la richiesta di firma' : 'Crea richiesta simulata', 'primary', 'data-signature-create');
      const key = newKey();
      send.addEventListener('click', async () => {
        send.disabled = true;
        try {
          await apiPost(ESIGN, { visit_sheet_id: sheet.id, signer_name: name.value, signer_contact: contact.value,
            expires_in_days: 7, idempotency_key: key, include_agent: true });
          await load();
        } catch (error) { send.disabled = false; form.appendChild(el('div', 'error-box', errorText(error))); }
      });
      form.append(el('p', 'muted', config.otp
        ? 'Firma elettronica SEMPLICE (FES). Il documento viene congelato in questa versione; il cliente riceve per email un '
          + 'collegamento personale, legge il PDF, accetta la dichiarazione e conferma con un codice OTP via email. Tu firmi come '
          + 'agente dal CRM. ' + (config.recipients_restricted ? 'Su questo ambiente le email partono solo verso i destinatari autorizzati.' : '')
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
