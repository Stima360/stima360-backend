// STIMA360 — pagina pubblica di firma del foglio visita (FIRMA OTP - FIRMA SEMPLIFICATA).
//
// Una sola pagina, tre momenti: leggi il documento -> «Accetto il documento e richiedo il codice» ->
// sei cifre e «Conferma firma». Poi l'esito, con il download quando il documento è completo.
//
// Il consenso resta esplicito e registrato: la dichiarazione è sempre in vista sopra il pulsante; il
// cliente la accetta con un'azione volontaria («Accetto il documento e richiedo il codice») e la
// conferma con il codice e «Conferma firma»; il server registra versione e impronta della
// dichiarazione insieme all'impronta del documento. La conferma OTP resta l'atto conclusivo.
// In pagina niente impronte, codici di procedimento, identificativi o dati tecnici.
//
// Il token sta nell'URL (`/firma/<token>`) e viene usato solo verso l'API pubblica. Nessun cookie,
// nessuno storage, nessuna libreria esterna. Tutto il testo dei dati passa da `textContent`. Lo stato
// lo decide il server a ogni chiamata.

const API = '/api/public/esign/sign';
const app = document.getElementById('sign-app');
const token = decodeURIComponent((location.pathname.split('/').filter(Boolean).pop() || ''));
let orologio = null;

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

function show(...nodes) {
  if (orologio) { clearInterval(orologio); orologio = null; }
  app.replaceChildren(...nodes);
}

function ora(iso) {
  return new Date(iso).toLocaleTimeString('it-IT', { timeZone: 'Europe/Rome', hour: '2-digit', minute: '2-digit' });
}

function pdfUrl(signed) {
  return `${API}/${encodeURIComponent(token)}/${signed ? 'signed.pdf' : 'pdf'}`;
}

function passi(attuale) {
  const ol = el('ol', 'sign-steps');
  ol.setAttribute('aria-label', 'Passaggi');
  ['Leggi', 'Ricevi il codice', 'Conferma'].forEach((t, i) => {
    const li = el('li', i < attuale ? 'done' : (i === attuale ? 'now' : null), t);
    if (i === attuale) li.setAttribute('aria-current', 'step');
    ol.appendChild(li);
  });
  return ol;
}

function docPanel(state, compatto = false) {
  const panel = el('section', `panel sign-doc${compatto ? ' sign-doc-compact' : ''}`);
  panel.appendChild(el('h2', null, 'Foglio visita'));
  if (compatto) {
    // Dopo l'invio del codice il documento resta a portata di dito, ma non spinge il codice fuori schermo.
    const dove = state.document.address || state.property.address || state.property.reference;
    if (dove) panel.appendChild(el('p', 'sign-hint', [dove, state.document.visit_text].filter(Boolean).join(' · ')));
    const links = el('div', 'sign-pdf-links');
    const open = el('a', null, 'Rileggi il documento');
    open.href = pdfUrl(false); open.target = '_blank'; open.rel = 'noopener noreferrer'; open.setAttribute('data-sign-open-pdf', '');
    links.appendChild(open);
    panel.appendChild(links);
    return panel;
  }
  const dl = el('dl');
  const immobile = state.document.address || state.property.address || state.property.reference;
  const rows = [['Immobile', immobile], ['Visita', state.document.visit_text || [state.visit.date_text, state.visit.time_text].filter(Boolean).join(' ')],
    ['Agenzia', state.agency_name], ['Firmatario', state.signer.name]];
  for (const [k, v] of rows) {
    if (!v) continue;
    dl.appendChild(el('dt', null, k));
    dl.appendChild(el('dd', null, v));
  }
  panel.appendChild(dl);
  const wide = window.matchMedia && window.matchMedia('(min-width: 760px)').matches;
  if (wide) {
    const frame = el('iframe', 'sign-pdf');
    frame.title = 'Documento da firmare';
    frame.src = pdfUrl(false);
    frame.setAttribute('data-sign-pdf', '');
    panel.appendChild(frame);
  }
  const links = el('div', 'sign-pdf-links');
  const open = el('a', 'primary-link', 'Leggi il documento');
  open.href = pdfUrl(false); open.target = '_blank'; open.rel = 'noopener noreferrer'; open.setAttribute('data-sign-open-pdf', '');
  const dl2 = el('a', null, 'Scarica il PDF');
  dl2.href = pdfUrl(false); dl2.download = 'foglio-visita.pdf'; dl2.setAttribute('data-sign-download-pdf', '');
  links.append(open, dl2);
  panel.appendChild(links);
  if (!wide) panel.appendChild(el('p', 'sign-hint', 'Il documento si apre nel lettore PDF del telefono: leggilo e torna qui per firmare.'));
  return panel;
}

function consentPanel(state) {
  const box = el('section', 'panel sign-consent');
  box.setAttribute('data-sign-declaration', '');
  box.appendChild(el('h2', null, 'Cosa stai firmando'));
  box.appendChild(el('p', null, state.consent.text));
  const privacy = el('details');
  privacy.appendChild(el('summary', null, 'Come trattiamo i tuoi dati'));
  privacy.appendChild(el('p', null, state.consent.privacy_note));
  box.appendChild(privacy);
  return box;
}

function errorBox(message) {
  const e = el('div', 'sign-error', message);
  e.setAttribute('role', 'alert');
  e.setAttribute('data-sign-error', '');
  return e;
}

function finalPanel(state) {
  const testi = {
    completed: ['ok', 'Documento firmato correttamente', 'Tutte le firme richieste sono state registrate. Puoi scaricare il documento firmato e conservarlo.'],
    signed: ['wait', 'La tua firma è stata registrata', 'In attesa del completamento dell’agente. Quando il documento sarà completo potrai scaricarlo da questa pagina.'],
    declined: ['stop', 'Hai scelto di non firmare', 'Nessuna firma è stata apposta. Per qualsiasi chiarimento contatta l’agenzia.'],
    expired: ['stop', 'Il tempo per firmare è terminato', 'Contatta l’agenzia per ricevere un nuovo invito.'],
    revoked: ['stop', 'Richiesta annullata', 'L’agenzia ha annullato questa richiesta di firma.'],
    link_expired: ['stop', 'Collegamento non più utilizzabile', 'Contatta l’agenzia per riceverne uno nuovo.'],
    link_revoked: ['stop', 'Collegamento sostituito', 'Usa il collegamento dell’ultima email ricevuta o contatta l’agenzia.'],
  }[state.state];
  const panel = el('section', `panel sign-final ${testi[0]}`);
  panel.setAttribute('data-sign-final', state.state);
  panel.appendChild(el('h2', null, testi[1]));
  panel.appendChild(el('p', null, testi[2]));
  const links = el('div', 'sign-pdf-links');
  if (state.can_download_signed) {
    const a = el('a', null, 'Scarica il documento firmato');
    a.href = pdfUrl(true);
    a.setAttribute('data-sign-download-signed', '');
    links.appendChild(a);
  } else if (state.state === 'signed') {
    const a = el('a', null, 'Rileggi il documento');
    a.href = pdfUrl(false); a.target = '_blank'; a.rel = 'noopener noreferrer';
    links.appendChild(a);
  }
  if (links.children.length) panel.appendChild(links);
  return panel;
}

function levelNote(state) {
  return el('p', 'sign-level', state.level_notice);
}

function render(state, messaggio) {
  const s = state.state;
  if (['completed', 'signed', 'declined', 'expired', 'revoked', 'link_expired', 'link_revoked'].includes(s)) {
    show(finalPanel(state), levelNote(state));
    return;
  }
  const codiceScaduto = s === 'otp_sent' && state.otp && Date.parse(state.otp.expires_at) <= Date.now();
  const inviato = s === 'otp_sent' && !codiceScaduto;
  const nodes = [passi(inviato ? 2 : 0), docPanel(state, inviato), consentPanel(state)];
  const actions = el('section', 'panel sign-otp');
  const holder = el('div');
  if (messaggio) holder.appendChild(errorBox(messaggio));
  const bar = el('div', 'sign-actions');
  const decline = button('Non voglio firmare', 'sign-link-button', 'data-sign-decline');
  let primary;
  let resend = null;
  let codeInput = null;

  if (inviato) {
    actions.appendChild(el('p', null, `Ti abbiamo inviato un codice di 6 cifre a ${state.signer.email_masked}. Vale fino alle ${ora(state.otp.expires_at)}.`));
    codeInput = el('input', 'sign-code-input');
    codeInput.inputMode = 'numeric'; codeInput.autocomplete = 'one-time-code'; codeInput.maxLength = 6;
    codeInput.pattern = '[0-9]*'; codeInput.placeholder = '••••••'; codeInput.setAttribute('aria-label', 'Codice di 6 cifre');
    codeInput.setAttribute('data-sign-code', '');
    actions.appendChild(codeInput);
    if (state.otp.attempts_left < 5) actions.appendChild(el('p', 'sign-hint', `Tentativi rimasti: ${state.otp.attempts_left}.`));
    actions.appendChild(el('p', 'sign-hint', 'Premendo «Conferma firma» accetti la dichiarazione qui sopra e firmi il documento.'));
    primary = button('Conferma firma', 'primary', 'data-sign-primary');
    primary.disabled = true;
    resend = button('Invia un nuovo codice', 'ghost', 'data-sign-resend');
    bar.append(primary, resend);
  } else {
    actions.appendChild(el('p', null, codiceScaduto
      ? 'Il codice è scaduto: richiedine uno nuovo.'
      : `Se il documento è corretto, accettalo: riceverai un codice a ${state.signer.email_masked} per confermare la firma.`));
    primary = button(codiceScaduto ? 'Ricevi un nuovo codice' : 'Accetto il documento e richiedo il codice', 'primary', 'data-sign-primary');
    bar.appendChild(primary);
  }
  actions.append(bar, holder, decline);
  nodes.push(actions, levelNote(state));

  const aggiorna = () => {
    if (codeInput) primary.disabled = codeInput.value.length !== 6;
  };
  // Il nuovo codice si chiede dopo l'attesa minima: il pulsante lo dice con un conto alla rovescia.
  const attesaReinvio = () => {
    if (!resend) return;
    const mancano = Math.ceil((Date.parse(state.resend_after || 0) - Date.now()) / 1000);
    if (mancano > 0) { resend.disabled = true; resend.textContent = `Invia un nuovo codice (tra ${mancano} s)`; }
    else { resend.disabled = false; resend.textContent = 'Invia un nuovo codice'; }
  };

  async function run(fn) {
    holder.replaceChildren();
    for (const b of [primary, resend, decline].filter(Boolean)) b.disabled = true;
    try {
      render(await fn());
    } catch (error) {
      if (['OTP_LOCKED', 'OTP_EXPIRED'].includes(error.code) || ([409, 404, 410].includes(error.status) && error.code && !/^OTP_/.test(error.code))) {
        try { render(await call('GET', ''), error.message); return; } catch (e) { /* resta l'errore */ }
      }
      holder.appendChild(errorBox(error.message));
      decline.disabled = false;
      attesaReinvio();
      if (codeInput) { aggiorna(); codeInput.focus(); } else primary.disabled = false;
    }
  }

  if (codeInput) {
    codeInput.addEventListener('input', () => { codeInput.value = codeInput.value.replace(/\D/g, '').slice(0, 6); aggiorna(); });
    codeInput.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !primary.disabled) primary.click(); });
  }
  primary.addEventListener('click', () => {
    if (inviato) {
      // La conferma finale: codice + dichiarazione in vista, accettata con questo gesto (registrata dal server).
      run(() => call('POST', '/confirm', { code: codeInput.value, consent_accepted: true, consent_version: state.consent.version }));
    } else {
      run(() => call('POST', '/otp'));
    }
  });
  if (resend) resend.addEventListener('click', () => run(() => call('POST', '/otp')));
  decline.addEventListener('click', () => {
    if (!window.confirm('Confermi di NON voler firmare questo documento?')) return;
    run(() => call('POST', '/decline', { reason: null }));
  });
  show(...nodes);
  aggiorna();
  attesaReinvio();
  if (resend) orologio = setInterval(attesaReinvio, 1000);
  if (codeInput) {
    codeInput.focus({ preventScroll: true });
    if (typeof codeInput.scrollIntoView === 'function') codeInput.scrollIntoView({ block: 'center' });
  }
}

async function start() {
  if (!token) { show(errorBox('Collegamento non valido.')); return; }
  try {
    render(await call('GET', ''));
  } catch (error) {
    const panel = el('section', 'panel sign-final stop');
    panel.appendChild(el('h2', null, 'Collegamento non valido'));
    panel.appendChild(el('p', null, error.status === 404
      ? 'Il collegamento non esiste o non è più valido. Controlla l’ultima email ricevuta o contatta l’agenzia.'
      : error.message));
    show(panel);
  }
}

start();
