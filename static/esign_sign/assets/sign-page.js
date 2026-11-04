// STIMA360 — pagina pubblica di firma del foglio visita (FIRMA OTP).
//
// Il token sta nell'URL (`/firma/<token>`) e viene usato solo verso l'API pubblica. Nessun cookie, nessuna
// registrazione, nessuna libreria esterna. Tutto il testo dei dati passa da `textContent`. Lo stato lo decide il
// server a ogni chiamata: la pagina non tiene stati propri oltre al codice digitato.

const API = '/api/public/esign/sign';
const app = document.getElementById('sign-app');
const token = decodeURIComponent((location.pathname.split('/').filter(Boolean).pop() || ''));

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function button(label, className, attr) {
  const b = el('button', className, label);
  b.type = 'button';
  if (attr) b.setAttribute(attr, '');
  return b;
}

async function call(method, path, body) {
  const response = await fetch(`${API}/${encodeURIComponent(token)}${path}`, {
    method, headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined,
    credentials: 'omit', cache: 'no-store',
  });
  let data = null;
  try { data = await response.json(); } catch (e) { data = null; }
  if (!response.ok) {
    const d = data && data.detail;
    const message = (d && typeof d === 'object' && d.message) || (typeof d === 'string' ? d : null) || 'Operazione non riuscita.';
    const error = new Error(message);
    error.code = d && typeof d === 'object' ? d.code : null;
    error.status = response.status;
    throw error;
  }
  return data;
}

function show(...nodes) { app.replaceChildren(...nodes); }

function quando(iso) {
  return new Date(iso).toLocaleString('it-IT', { timeZone: 'Europe/Rome', day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function docPanel(state) {
  const panel = el('section', 'panel sign-doc');
  panel.appendChild(el('h2', null, state.document.title));
  const dl = el('dl');
  const rows = [['Agenzia', state.agency_name], ['Immobile', [state.property.reference, state.property.address].filter(Boolean).join(' · ')],
    ['Visita', [state.visit.date_text, state.visit.time_text].filter(Boolean).join(' ')], ['Firmatario', `${state.signer.name} (${state.signer.email_masked})`],
    ['Versione', `n. ${state.document.version}`], ['Impronta SHA-256', state.document.sha256]];
  for (const [k, v] of rows) {
    if (!v) continue;
    dl.appendChild(el('dt', null, k));
    dl.appendChild(el('dd', k === 'Impronta SHA-256' ? 'sign-code' : null, v));
  }
  panel.appendChild(dl);
  const pdfUrl = `${API}/${encodeURIComponent(token)}/pdf`;
  // Su schermo largo il PDF è incorporato; sul telefono (Safari mostra un PDF in un riquadro senza scorrimento) si apre a
  // tutto schermo con il lettore del dispositivo: è il modo in cui il cliente lo legge davvero.
  const wide = window.matchMedia && window.matchMedia('(min-width: 760px)').matches;
  if (wide) {
    const frame = el('iframe', 'sign-pdf');
    frame.title = 'Documento da firmare';
    frame.src = pdfUrl;
    frame.setAttribute('data-sign-pdf', '');
    panel.appendChild(frame);
  }
  const links = el('div', 'sign-pdf-links');
  const open = el('a', wide ? null : 'primary-link', wide ? 'Apri il PDF a tutto schermo' : 'Apri e leggi il documento (PDF)');
  open.href = pdfUrl; open.target = '_blank'; open.rel = 'noopener noreferrer'; open.setAttribute('data-sign-open-pdf', '');
  const dl2 = el('a', null, 'Scarica il PDF');
  dl2.href = pdfUrl; dl2.download = `${state.document.title}.pdf`; dl2.setAttribute('data-sign-download-pdf', '');
  links.append(open, dl2);
  panel.appendChild(links);
  if (!wide) panel.appendChild(el('p', 'sign-hint', 'Leggi tutto il documento prima di firmare: si apre nel lettore PDF del telefono.'));
  return panel;
}

function levelNote(state) {
  return el('p', 'sign-level', state.level_notice);
}

function consentPanel(state, onChange) {
  const box = el('section', 'panel sign-consent');
  box.appendChild(el('h3', null, 'Dichiarazione di sottoscrizione'));
  box.appendChild(el('p', null, state.consent.text));
  box.appendChild(el('p', 'privacy', state.consent.privacy_note));
  const label = el('label');
  const check = el('input'); check.type = 'checkbox'; check.setAttribute('data-sign-consent', '');
  check.addEventListener('change', () => onChange(check.checked));
  label.append(check, el('span', null, 'Ho letto il documento e dichiaro di volerlo sottoscrivere con firma elettronica semplice.'));
  box.appendChild(label);
  return { box, check };
}

function errorBox(message) {
  const e = el('div', 'sign-error', message);
  e.setAttribute('role', 'alert');
  e.setAttribute('data-sign-error', '');
  return e;
}

function finalPanel(state, extra) {
  const panel = el('section', 'panel sign-notice');
  panel.setAttribute('data-sign-final', state.state);
  panel.appendChild(el('h2', null, state.state_label));
  if (extra) panel.appendChild(el('p', null, extra));
  if (state.can_download_signed) {
    const a = el('a', null, 'Scarica il documento sottoscritto');
    a.href = `${API}/${encodeURIComponent(token)}/signed.pdf`;
    a.setAttribute('data-sign-download-signed', '');
    const wrap = el('div', 'sign-pdf-links'); wrap.appendChild(a);
    panel.appendChild(wrap);
  }
  panel.appendChild(el('p', 'sign-hint', `Procedimento ${state.procedure}.`));
  return panel;
}

function render(state) {
  const s = state.state;
  if (['completed', 'signed', 'declined', 'expired', 'revoked', 'link_expired', 'link_revoked'].includes(s)) {
    const extra = {
      signed: 'La tua firma è stata registrata. Il documento sarà completo quando avranno firmato tutte le parti.',
      completed: 'Tutte le parti hanno firmato. Puoi scaricare il documento sottoscritto.',
      declined: 'Hai rifiutato di firmare: nessuna sottoscrizione è stata apposta.',
      expired: 'Il tempo per firmare è terminato: contatta l’agenzia per un nuovo invio.',
      revoked: 'L’agenzia ha revocato la richiesta.',
      link_expired: 'Questo collegamento non è più utilizzabile: contatta l’agenzia per riceverne uno nuovo.',
      link_revoked: 'Questo collegamento è stato sostituito: usa l’ultimo messaggio ricevuto o contatta l’agenzia.',
    }[s];
    show(finalPanel(state, extra), ...(s === 'completed' || s === 'signed' ? [docPanel(state)] : []), levelNote(state));
    return;
  }
  const nodes = [docPanel(state)];
  let consented = false;
  const { box: consentBox, check } = consentPanel(state, (v) => { consented = v; update(); });
  nodes.push(consentBox);
  const actions = el('section', 'panel sign-otp');
  const holder = el('div');
  let codeInput = null;
  if (s === 'otp_sent') {
    actions.appendChild(el('p', null, `Ti abbiamo inviato un codice di 6 cifre a ${state.signer.email_masked}. Vale fino alle ${quando(state.otp.expires_at)}.`));
    codeInput = el('input'); codeInput.inputMode = 'numeric'; codeInput.autocomplete = 'one-time-code'; codeInput.maxLength = 6;
    codeInput.pattern = '[0-9]{6}'; codeInput.placeholder = '______'; codeInput.setAttribute('aria-label', 'Codice OTP');
    codeInput.setAttribute('data-sign-code', '');
    codeInput.addEventListener('input', () => { codeInput.value = codeInput.value.replace(/\D/g, '').slice(0, 6); update(); });
    actions.appendChild(codeInput);
    actions.appendChild(el('p', 'sign-hint', `Tentativi disponibili: ${state.otp.attempts_left}.`));
  } else {
    actions.appendChild(el('p', null, 'Per firmare accetta la dichiarazione qui sopra: ti invieremo un codice OTP all’indirizzo email con cui hai ricevuto questo collegamento.'));
  }
  const bar = el('div', 'sign-actions');
  const primary = button(s === 'otp_sent' ? 'Conferma la firma con il codice' : 'Conferma e richiedi codice OTP', 'primary', 'data-sign-primary');
  primary.disabled = true;
  const resend = button('Invia un nuovo codice', 'ghost', 'data-sign-resend');
  const decline = button('Non voglio firmare', 'danger', 'data-sign-decline');
  bar.appendChild(primary);
  if (s === 'otp_sent') bar.appendChild(resend);
  bar.appendChild(decline);
  actions.append(bar, holder);
  nodes.push(actions, levelNote(state));

  function update() {
    primary.disabled = !consented || (s === 'otp_sent' && (!codeInput || codeInput.value.length !== 6));
  }
  async function run(fn, busy) {
    holder.replaceChildren();
    for (const b of [primary, resend, decline]) b.disabled = true;
    try { render(await fn()); } catch (error) {
      holder.appendChild(errorBox(error.message));
      if ([409, 404, 410].includes(error.status) && error.code && !/^OTP_/.test(error.code)) { try { render(await call('GET', '')); } catch (e) { /* resta l'errore */ } return; }
      update(); resend.disabled = false; decline.disabled = false;
      if (error.code === 'OTP_LOCKED' || error.code === 'OTP_EXPIRED') { try { render(await call('GET', '')); } catch (e) { /* resta l'errore */ } }
    }
  }
  primary.addEventListener('click', () => {
    if (s === 'otp_sent') {
      run(() => call('POST', '/confirm', { code: codeInput.value, consent_accepted: check.checked === true, consent_version: state.consent.version }));
    } else {
      run(() => call('POST', '/otp'));
    }
  });
  resend.addEventListener('click', () => run(() => call('POST', '/otp')));
  decline.addEventListener('click', () => {
    if (!window.confirm('Confermi di NON voler firmare questo documento?')) return;
    run(() => call('POST', '/decline', { reason: null }));
  });
  show(...nodes);
  update();
}

async function start() {
  if (!token) { show(errorBox('Collegamento non valido.')); return; }
  try {
    render(await call('GET', ''));
  } catch (error) {
    const panel = el('section', 'panel');
    panel.appendChild(el('h2', null, 'Collegamento non valido'));
    panel.appendChild(el('p', null, error.status === 404
      ? 'Il collegamento non esiste o non è più valido. Controlla l’ultimo messaggio ricevuto o contatta l’agenzia.'
      : error.message));
    show(panel);
  }
}

start();
