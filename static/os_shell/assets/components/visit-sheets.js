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
// FIRMA OTP STIMA360 - FIRMA SEMPLIFICATA: il riquadro «Firma del foglio visita» (FES), uguale
// in Agenda, nella scheda dell'immobile e nella sezione «Fogli visita». Un pulsante principale
// coerente con lo stato («Invia per firma», «Firma come agente», «Scarica PDF firmato»), le altre
// azioni nel menu «⋯» e solo quando sono possibili. Cliente precompilato dal CRM, agente dalla
// visita; il foglio si genera o si riusa sul server. Lo stato lo dice il server (sette parole,
// `presentation.py`) e si aggiorna da solo mentre si attende qualcuno. Nessun identificativo
// tecnico in vista: procedimento e registro delle prove stanno in «Dettagli tecnici».
// Il livello è sempre «firma elettronica semplice». Tutto via `textContent`.

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

// Il riquadro di prima, usato SOLO con l'adattatore SIMULATO (BLOCCO-6B): la firma OTP usa
// `mountSignaturePanel` qui sotto.
async function mountLegacySignatures(holder, sheet, badge) {
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
          + 'agente dal CRM.'
        : 'Simulazione: nessun messaggio viene inviato e nessuna firma valida viene prodotta.'), name, contact, send);
      ask.replaceWith(form);
    });
    box.appendChild(ask);
  }
  await load();
}

async function downloadPdf(sheet, holder) {
  return downloadFrom(`${API}/${sheet.id}/pdf`, `foglio-visita-${sheet.appointment_id}-v${sheet.version}.pdf`, holder);
}

// Apre un PDF del CRM in una scheda nuova. La scheda si apre SUBITO (gesto dell'utente: niente
// blocco dei popup su iPhone) e riceve il documento appena arriva; se il browser non la concede,
// il PDF si scarica.
async function openPdf(url, filename, holder) {
  let finestra = null;
  try { finestra = window.open('', '_blank'); } catch (e) { finestra = null; }
  try {
    const response = await fetch(url, { credentials: 'include' });
    if (!response.ok) throw new Error('Documento non disponibile.');
    const blob = await response.blob();
    const href = URL.createObjectURL(blob);
    if (finestra) {
      finestra.location.href = href;
    } else {
      const a = el('a');
      a.href = href; a.download = filename;
      document.body.appendChild(a); a.click(); a.remove();
    }
    setTimeout(() => URL.revokeObjectURL(href), 60000);
  } catch (error) {
    if (finestra) finestra.close();
    holder.appendChild(el('div', 'error-box', errorText(error)));
  }
}

const EMAIL_OK = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

// Le voci del registro delle prove in parole (Dettagli tecnici). Gli user-agent e le impronte
// restano nel rapporto delle prove, non qui.
const EVENT_LABELS = {
  request_created: 'Richiesta creata', link_issued: 'Collegamento personale creato', link_email_queued: 'Email del collegamento affidata all’invio',
  link_opened: 'Collegamento aperto dal cliente', otp_requested: 'Codice richiesto dal cliente', otp_email_queued: 'Email del codice affidata all’invio',
  otp_failed: 'Codice errato', otp_verified: 'Codice verificato', otp_invalidated: 'Codice annullato', consent_recorded: 'Dichiarazione accettata',
  signer_signed: 'Firma registrata', signer_declined: 'Firma rifiutata', agent_authenticated: 'Agente autenticato con password',
  agent_auth_failed: 'Password dell’agente errata', signature_completed: 'Firma completata', documents_archived: 'Documento firmato e prove archiviati',
  request_expired: 'Richiesta scaduta', request_cancelled: 'Richiesta annullata', link_invalidated: 'Collegamento sostituito',
};
const ACTOR_LABELS = { operator: 'agenzia', signer: 'firmatario', system: 'sistema' };

async function technicalDetails(r, holder) {
  holder.replaceChildren();
  let data;
  try { data = await apiGet(`${ESIGN}/${r.id}/events`); } catch (error) { holder.appendChild(el('div', 'error-box', errorText(error))); return; }
  holder.appendChild(el('p', 'muted', `Procedimento ${r.procedure || r.provider_reference || r.id} · ${r.signature_level_label || 'Firma elettronica semplice (FES)'}`));
  const list = el('ol', 'visit-sign-events');
  for (const e of data.items) {
    list.appendChild(el('li', null, `${when(e.occurred_at)} — ${EVENT_LABELS[e.event_type] || 'Evento registrato'} (${ACTOR_LABELS[e.actor_kind] || e.actor_kind})`));
  }
  holder.appendChild(list);
  holder.appendChild(el('p', data.integrity.ok ? 'success-box' : 'error-box',
    data.integrity.ok ? `Registro delle prove integro (${data.integrity.count} voci).` : 'Registro delle prove NON integro: controlla «Verifica integrità».'));
}

function signerOf(r, role) {
  return (r && r.signers || []).find((s) => s.role === role) || null;
}

// Il riquadro della firma della visita `appointmentId`. `onChanged` (facoltativo) viene chiamato
// dopo ogni cambio di stato, per chi mostra lo stesso foglio altrove (l'elenco Fogli visita).
export async function mountSignaturePanel(holder, { appointmentId, onChanged = null } = {}) {
  const box = el('section', 'sign-panel');
  box.setAttribute('data-sign-panel', String(appointmentId));
  holder.appendChild(box);
  let config = { otp: false, simulation: true };
  try { config = await apiGet(`${ESIGN}/config/provider`); } catch (error) { /* senza configurazione: niente firma */ }
  let timer = null;
  let occupato = false;              // un modulo in compilazione non viene ridisegnato dall'aggiornamento automatico
  let ultimoStato = null;

  const notifica = () => { if (onChanged) { try { onChanged(); } catch (e) { /* chi ascolta non blocca il riquadro */ } } };

  function pianifica(stato) {
    if (timer) clearTimeout(timer);
    timer = null;
    if (!['awaiting_client', 'awaiting_agent'].includes(stato)) return;
    if (typeof box.isConnected !== 'boolean') return;          // fuori da un documento vero: niente orologio
    timer = setTimeout(() => {
      if (!box.isConnected) return;
      if (occupato) { pianifica(stato); return; }
      load().catch(() => {});
    }, 15000);
  }

  async function load(notice) {
    let data;
    try {
      data = await apiGet(`${API}/appointments/${encodeURIComponent(String(appointmentId))}`);
    } catch (error) {
      box.replaceChildren(el('div', 'error-box', errorText(error)));
      return;
    }
    const latest = data.sheets[0] || null;
    if (!config.otp) {
      // Firma OTP non attiva (nessun fornitore, o l'adattatore simulato): il foglio si genera, si scarica e
      // (in simulazione) si «firma» come prima. Il riquadro semplificato vale solo con la firma OTP.
      box.replaceChildren();
      if (notice) box.appendChild(notice);
      const lista = el('ul', 'visit-sheets-list');
      lista.appendChild(buildLegacyItem(data, (avviso) => load(avviso), { compact: true }));
      box.appendChild(lista);
      return;
    }
    let requests = [];
    if (latest) {
      try { requests = (await apiGet(`${ESIGN}?visit_sheet_id=${latest.id}`)).items || []; } catch (error) {
        if (!(error && error.status === 503)) { box.replaceChildren(el('div', 'error-box', errorText(error))); return; }
      }
    }
    const r = requests.find((x) => x.otp) || null;
    render(data, latest, r, notice);
    const stato = r && r.ui ? r.ui.state : 'to_send';
    if (ultimoStato !== null && ultimoStato !== stato) notifica();
    ultimoStato = stato;
    pianifica(stato);
  }

  function render(data, latest, r, notice) {
    occupato = false;
    box.replaceChildren();
    const ui = r && r.ui ? r.ui : { state: 'to_send', label: 'Da inviare', note: null };
    const testa = el('div', 'sign-panel-head');
    testa.appendChild(el('h4', 'sign-panel-title', 'Firma del foglio visita'));
    const stato = el('span', `sign-state sign-state-${ui.state}`, ui.label);
    stato.setAttribute('data-sign-state', ui.state);
    testa.appendChild(stato);
    box.appendChild(testa);
    if (notice) box.appendChild(notice);
    if (ui.note) box.appendChild(el('p', 'sign-note', ui.note));
    if (config.otp_ready === false) {
      box.appendChild(el('div', 'error-box', 'La firma elettronica non è ancora configurata su questo servizio.'));
      return;
    }
    box.appendChild(el('p', 'muted sign-doc-line', latest
      ? `Foglio visita, versione ${latest.version} · ${latest.visit_state_label} · preparato il ${when(latest.generated_at)}`
      : 'Il foglio visita verrà preparato con i dati della visita al primo invio.'));

    const azioni = el('div', 'sign-actions');
    const area = el('div', 'sign-area');
    const menu = buildMenu(data, latest, r, area);
    const aperta = r && r.status === 'sent';
    const agente = signerOf(r, 'agent');
    const cliente = signerOf(r, 'visitor');

    if (['to_send', 'expired', 'declined'].includes(ui.state)) {
      area.appendChild(sendForm(data, latest, ui.state === 'to_send' ? 'Invia per firma' : 'Invia di nuovo'));
    } else if (ui.state === 'completed') {
      const scarica = button('Scarica PDF firmato', 'primary', 'data-sign-download-final');
      scarica.addEventListener('click', () => downloadFrom(`${ESIGN}/${r.id}/document`, `foglio-visita-firmato-${data.appointment_id}.pdf`, box));
      azioni.appendChild(scarica);
      area.appendChild(el('p', 'sign-done', 'Documento completato · firma elettronica semplice (FES).'));
    } else if (aperta) {
      if (cliente) area.appendChild(el('p', 'muted', `Cliente: ${cliente.name} · ${cliente.email}`));
      const puoFirmare = r.can_agent_sign && data.agent && data.agent.is_me && agente && agente.status === 'pending';
      if (puoFirmare) {
        if (ui.state === 'awaiting_agent') {
          area.appendChild(agentForm(r));
        } else {
          const firma = button('Firma come agente', 'primary', 'data-signature-agent');
          firma.addEventListener('click', () => { firma.remove(); area.appendChild(agentForm(r)); });
          azioni.appendChild(firma);
        }
      } else if (ui.state === 'send_failed' && r.can_resend) {
        const reinvia = button('Reinvia email', 'primary', 'data-sign-resend-main');
        reinvia.addEventListener('click', () => resend(r, reinvia));
        azioni.appendChild(reinvia);
      } else if (agente && agente.status === 'pending' && ui.state === 'awaiting_agent') {
        area.appendChild(el('p', 'muted', `Manca la firma di ${agente.name}: può firmare dal suo accesso al CRM.`));
      }
    }
    azioni.appendChild(menu.toggle);
    box.append(azioni, menu.list, area);
    if (data.sheets.length > 1) box.appendChild(history(data));
  }

  function sendForm(data, latest, etichetta) {
    const form = el('form', 'sign-form');
    form.setAttribute('data-sign-form', '');
    form.noValidate = true;
    const proposta = data.signer_suggestion || {};
    const campo = (testo, input) => { const l = el('label', 'sign-field'); l.append(el('span', null, testo), input); return l; };
    const nome = el('input', 'input'); nome.value = proposta.name || ''; nome.maxLength = 120; nome.autocomplete = 'off';
    nome.setAttribute('data-signature-name', '');
    const email = el('input', 'input'); email.type = 'email'; email.value = proposta.email || ''; email.maxLength = 200;
    email.autocomplete = 'off'; email.setAttribute('data-signature-contact', '');
    form.append(campo('Nome e cognome del cliente', nome), campo('Email del cliente', email));
    if (data.agent && data.agent.name) form.appendChild(el('p', 'muted', `Agente: ${data.agent.name}${data.agent.is_me ? ' (tu)' : ''}.`));
    let spunta = null; let password = null;
    if (data.agent && data.agent.is_me) {
      const riga = el('label', 'sign-agent-now');
      spunta = el('input'); spunta.type = 'checkbox'; spunta.setAttribute('data-sign-agent-now', '');
      riga.append(spunta, el('span', null, 'Firmo anche io adesso: dichiaro di aver letto il foglio visita in questa versione e di volerlo firmare con firma elettronica semplice.'));
      password = el('input', 'input'); password.type = 'password'; password.autocomplete = 'current-password';
      password.placeholder = 'La tua password del CRM'; password.hidden = true; password.setAttribute('data-sign-agent-password', '');
      spunta.addEventListener('change', () => { password.hidden = !spunta.checked; if (spunta.checked) password.focus(); });
      form.append(riga, password);
    }
    const errore = el('div', 'sign-form-error');
    errore.setAttribute('role', 'alert');
    const barra = el('div', 'sign-actions');
    const vedi = button('Visualizza PDF', 'ghost', 'data-sign-preview');
    const invia = el('button', 'btn primary', etichetta);
    invia.type = 'submit'; invia.setAttribute('data-sign-send', '');
    barra.append(invia, vedi);
    form.append(errore, barra);
    const chiave = newKey();
    for (const n of [nome, email, password].filter(Boolean)) n.addEventListener('input', () => { occupato = true; });
    vedi.addEventListener('click', async () => {
      if (latest) { openPdf(`${API}/${latest.id}/pdf`, `foglio-visita-${data.appointment_id}.pdf`, box); return; }
      vedi.disabled = true;
      try {
        const out = await apiPost(`${API}/appointments/${data.appointment_id}`, {});
        await openPdf(`${API}/${out.sheet.id}/pdf`, `foglio-visita-${data.appointment_id}.pdf`, box);
        await load();
      } catch (error) { vedi.disabled = false; errore.textContent = errorText(error); }
    });
    form.addEventListener('submit', async (evento) => {
      if (evento && evento.preventDefault) evento.preventDefault();
      errore.textContent = '';
      const n = nome.value.trim(); const m = email.value.trim();
      if (!n) { errore.textContent = 'Indica il nome del cliente.'; nome.focus(); return; }
      if (!EMAIL_OK.test(m)) { errore.textContent = 'Indica un indirizzo email valido del cliente.'; email.focus(); return; }
      const firmoAnche = !!(spunta && spunta.checked);
      if (firmoAnche && !password.value) { errore.textContent = 'Per firmare adesso inserisci la tua password.'; password.focus(); return; }
      invia.disabled = true; vedi.disabled = true;
      invia.textContent = 'Invio in corso…';
      const corpo = { appointment_id: data.appointment_id, known_sheet_id: latest ? latest.id : null,
        signer_name: n, signer_contact: m, idempotency_key: chiave };
      if (firmoAnche) corpo.agent_sign = { password: password.value, consent_accepted: true, consent_version: config.consent_version };
      try {
        const out = await apiPost(`${ESIGN}/send`, corpo);
        const testo = out.agent_error
          ? `Richiesta inviata a ${m}. La tua firma non è stata registrata: ${out.agent_error}`
          : `Richiesta inviata a ${m}${out.agent_signed ? ' e firma dell’agente registrata' : ''}.`;
        await load(el('div', out.agent_error ? 'error-box' : 'success-box', testo));
        notifica();
      } catch (error) {
        const codice = error && error.data && error.data.detail && error.data.detail.code;
        if (codice === 'SHEET_UPDATED' || codice === 'REQUEST_OPEN') {
          await load(el('div', 'error-box', errorText(error)));
          return;
        }
        invia.disabled = false; vedi.disabled = false; invia.textContent = etichetta;
        errore.textContent = errorText(error);
      }
    });
    return form;
  }

  function agentForm(r) {
    occupato = true;
    const form = el('div', 'sign-form');
    form.setAttribute('data-agent-sign-form', '');
    const riga = el('label', 'sign-agent-now');
    const check = el('input'); check.type = 'checkbox'; check.setAttribute('data-agent-consent', '');
    riga.append(check, el('span', null, 'Dichiaro di aver letto il foglio visita in questa versione e di volerlo firmare con firma elettronica semplice.'));
    const pwd = el('input', 'input'); pwd.type = 'password'; pwd.placeholder = 'La tua password del CRM'; pwd.autocomplete = 'current-password';
    pwd.setAttribute('data-agent-password', '');
    const go = button('Firma come agente', 'primary', 'data-agent-sign');
    go.disabled = true;
    const errore = el('div', 'sign-form-error');
    const update = () => { go.disabled = !(check.checked && pwd.value.length > 0); };
    check.addEventListener('change', update); pwd.addEventListener('input', update);
    go.addEventListener('click', async () => {
      go.disabled = true;
      try {
        await apiPost(`${ESIGN}/${r.id}/sign-as-agent`, { password: pwd.value, consent_accepted: check.checked === true, consent_version: r.consent_version || config.consent_version });
        await load(el('div', 'success-box', 'La tua firma è registrata.'));
        notifica();
      } catch (error) { update(); errore.textContent = errorText(error); }
    });
    form.append(riga, pwd, go, errore);
    return form;
  }

  async function resend(r, pulsante) {
    pulsante.disabled = true;
    try {
      await apiPost(`${ESIGN}/${r.id}/links`, { send: true });
      await load(el('div', 'success-box', 'Nuovo collegamento inviato: il precedente non vale più.'));
    } catch (error) { pulsante.disabled = false; box.appendChild(el('div', 'error-box', errorText(error))); }
  }

  function buildMenu(data, latest, r, area) {
    const toggle = button('⋯', 'ghost sign-menu-toggle', 'data-sign-menu');
    toggle.setAttribute('aria-label', 'Altre azioni');
    toggle.setAttribute('aria-expanded', 'false');
    const list = el('div', 'sign-menu');
    list.setAttribute('data-sign-menu-list', '');
    list.hidden = true;
    toggle.addEventListener('click', () => {
      list.hidden = !list.hidden;
      toggle.setAttribute('aria-expanded', list.hidden ? 'false' : 'true');
    });
    const voce = (testo, attr, fn) => {
      const b = button(testo, 'ghost', attr);
      b.addEventListener('click', async () => { list.hidden = true; toggle.setAttribute('aria-expanded', 'false'); await fn(b); });
      list.appendChild(b);
    };
    const aperta = r && r.status === 'sent';
    if (aperta && r.can_resend) {
      voce('Reinvia', 'data-signature-resend', (b) => resend(r, b));
      voce('Copia link', 'data-signature-copy', async () => {
        try {
          const out = await apiPost(`${ESIGN}/${r.id}/links`, { send: false });
          try { await navigator.clipboard.writeText(out.url); area.appendChild(el('p', 'success-box', 'Collegamento copiato: il precedente non vale più. È personale, mandalo solo al cliente.')); }
          catch (e) { area.appendChild(el('p', 'muted', `Collegamento personale del cliente: ${out.url}`)); }
        } catch (error) { area.appendChild(el('div', 'error-box', errorText(error))); }
      });
    }
    if (aperta) {
      voce('Annulla richiesta', 'data-signature-cancel', async () => {
        if (!window.confirm('Annullare la richiesta di firma? Il collegamento del cliente smetterà di funzionare.')) return;
        try { await apiPost(`${ESIGN}/${r.id}/cancel`, {}); await load(el('div', 'success-box', 'Richiesta annullata.')); notifica(); }
        catch (error) { area.appendChild(el('div', 'error-box', errorText(error))); }
      });
    }
    if (latest) voce('Visualizza documento', 'data-sign-view', () => openPdf(`${API}/${latest.id}/pdf`, `foglio-visita-${data.appointment_id}.pdf`, box));
    if (r && r.has_document) {
      voce('Scarica PDF finale', 'data-signature-document', () => downloadFrom(`${ESIGN}/${r.id}/document`, `foglio-visita-firmato-${data.appointment_id}.pdf`, box));
    }
    if (r && r.has_audit_trail) {
      voce('Scarica rapporto delle prove', 'data-signature-audit', () => downloadFrom(`${ESIGN}/${r.id}/audit-trail`, `rapporto-prove-foglio-${data.appointment_id}.pdf`, box));
    }
    if (r) {
      voce('Verifica integrità', 'data-signature-verify', async () => {
        try {
          const out = await apiGet(`${ESIGN}/${r.id}/verify`);
          area.appendChild(el('p', out.ok ? 'success-box' : 'error-box', out.ok
            ? 'Integrità verificata: documento, firme e registro delle prove corrispondono alle impronte.'
            : `Integrità NON verificata: ${out.checks.filter((c) => !c.ok).map((c) => c.name).join(', ')}.`));
        } catch (error) { area.appendChild(el('div', 'error-box', errorText(error))); }
      });
      const dettagli = el('div', 'sign-technical');
      voce('Dettagli tecnici', 'data-signature-events', () => technicalDetails(r, dettagli));
      area.appendChild(dettagli);
    }
    if (!list.children.length) toggle.hidden = true;
    return { toggle, list };
  }

  function history(data) {
    const box2 = el('details', 'sign-history');
    box2.appendChild(el('summary', null, `Versioni precedenti (${data.sheets.length - 1})`));
    const ul = el('ul');
    for (const s of data.sheets.slice(1)) {
      const li = el('li');
      const firma = s.signature;
      li.appendChild(el('span', null, `Versione ${s.version} · ${when(s.generated_at)} · ${firma ? firma.state_label : 'Non inviata'}`));
      const vedi = button('Visualizza', 'ghost btn-small', 'data-sign-history-view');
      vedi.addEventListener('click', () => openPdf(`${API}/${s.id}/pdf`, `foglio-visita-${s.appointment_id}-v${s.version}.pdf`, li));
      li.appendChild(vedi);
      if (firma && firma.has_document && firma.request_id) {
        const d = button('Scarica PDF firmato', 'ghost btn-small', 'data-sign-history-signed');
        d.addEventListener('click', () => downloadFrom(`${ESIGN}/${firma.request_id}/document`, `foglio-visita-firmato-${s.appointment_id}-v${s.version}.pdf`, li));
        li.appendChild(d);
      }
      ul.appendChild(li);
    }
    box2.appendChild(ul);
    return box2;
  }

  await load();
  return box;
}

// Il blocco di prima (versioni, «Genera foglio visita», scarico, richieste simulate), solo senza firma OTP.
function buildLegacyItem(v, reload, { compact = false } = {}) {
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
    mountLegacySignatures(item, s, firma).catch(() => {});
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

/**
 * Una visita nell'elenco dell'immobile: data, stato della visita, stato della firma del suo
 * foglio e il collegamento al foglio nella sezione «Fogli visita» (dove si invia e si firma).
 */
export function buildVisitItem(v, reload, { compact = false } = {}) {
  const item = el('li');
  item.setAttribute('data-visit-sheet-visit', String(v.appointment_id));
  if (!compact) item.append(el('strong', null, when(v.start_at)), el('span', 'muted', ` · ${STATUS[v.status] || v.status}`));
  const ultimo = v.sheets[0] || null;
  const line = el('div', 'visit-sheet-line');
  const firma = ultimo && ultimo.signature;
  const badge = el('span', 'badge badge-gray', firma ? firma.list_label : (ultimo ? 'Da firmare' : 'Foglio non ancora preparato'));
  badge.setAttribute('data-visit-sheet-signature-state', firma ? firma.state : (ultimo ? 'unsigned' : 'none'));
  line.appendChild(badge);
  if (ultimo) {
    line.appendChild(el('span', 'muted', ` Versione ${ultimo.version} · ${ultimo.visit_state_label}`));
    const dl = button('Scarica PDF', 'ghost', 'data-visit-sheet-download');
    dl.addEventListener('click', () => downloadPdf(ultimo, item));
    line.appendChild(dl);
  }
  const apri = el('a', 'btn ghost', ultimo ? 'Apri foglio visita' : 'Prepara e invia');
  apri.href = `#/fogli-visita/${v.appointment_id}`;
  apri.setAttribute('data-visit-sheet-open', String(v.appointment_id));
  line.appendChild(apri);
  item.appendChild(line);
  return item;
}

export async function mountVisitSheets(container, { propertyId }) {
  const root = el('section', 'visit-sheets');
  root.setAttribute('data-visit-sheets', '');
  container.appendChild(root);

  async function load(notice) {
    root.replaceChildren(el('h3', null, 'Fogli visita'));
    root.appendChild(el('p', 'muted', 'Una visita risulta svolta solo dopo l’esito registrato in agenda. Il foglio non è un documento firmato finché la firma non è completata.'));
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
 * Il foglio visita DALLA SCHEDA DELL'APPUNTAMENTO in Agenda: lo stesso riquadro della sezione
 * «Fogli visita» (stessa API, stessi permessi, stessi dati), piu' il collegamento alla sezione.
 */
export async function mountAppointmentSheet(container, { appointmentId }) {
  const root = el('section', 'visit-sheets visit-sheets-appointment');
  root.setAttribute('data-appointment-sheet', String(appointmentId));
  container.appendChild(root);
  root.appendChild(el('h3', 'section-title', 'Foglio visita'));
  let data;
  try {
    data = await apiGet(`${API}/appointments/${encodeURIComponent(String(appointmentId))}`);
  } catch (error) {
    if (error && (error.status === 404 || error.status === 503)) {
      root.replaceChildren();
      return root;
    }
    root.appendChild(el('div', 'error-box', errorText(error)));
    return root;
  }
  if (!data.can_generate && !data.sheets.length) {
    root.appendChild(el('p', 'muted', 'Il foglio si prepara per una visita fissata, confermata o svolta.'));
    return root;
  }
  await mountSignaturePanel(root, { appointmentId });
  const link = el('a', 'sign-section-link', 'Apri in Fogli visita');
  link.href = `#/fogli-visita/${appointmentId}`;
  root.appendChild(link);
  return root;
}
