/**
 * P30 - la pagina pubblica di prenotazione (`/prenota/<token>`).
 *
 * PRIVACY (non negoziabile):
 *  - il token del link vive SOLO nella URL e in una variabile di questo modulo;
 *    non va in localStorage/sessionStorage/cookie, non nel DOM (attributi,
 *    dataset, testo), non in console, non a terzi. L'unico posto in cui esce e'
 *    la URL delle chiamate all'API A30-12, sulla stessa origine.
 *  - il `submission_token` vive SOLO in memoria e parte SOLO nel submit.
 *  - si mostrano soltanto i campi ammessi (agenzia, agente, tipo, durata,
 *    giorno e ora): mai un id interno, nemmeno se il server ne mandasse.
 *  - le chiamate non portano cookie (`credentials: 'omit'`): una sessione
 *    operatore aperta nello stesso browser non viaggia mai da qui.
 *
 * DISPONIBILITA': solo gli slot restituiti dal server. La pagina non calcola
 * orari e non sceglie agente, durata, buffer, tipo o agenzia.
 *
 * IDEMPOTENZA: un solo invio alla volta; dopo un errore di rete si ripete lo
 * STESSO corpo con lo STESSO submission_token (il server restituisce la stessa
 * prenotazione se la prima era andata a buon fine). Un nuovo submission_token
 * si chiede solo dopo un 409 (orario preso da altri), quando la richiesta
 * precedente e' certamente fallita.
 */
import {
  API_BASE, SETTIMANE_AUTOMATICHE, SETTIMANE_MASSIME, LIMITI,
  aggiungiGiorni, confermaPubblica, controllaCliente, corpoPrenotazione, dataRoma,
  durataTesto, finestra, giornoBreve, giornoLungo, giorniConSlot, infoPubblica, ora,
  slotDelServer, tokenDaPercorso,
} from './booking-model.js';

const TIMEOUT_MS = 15000;

const TESTI = {
  // 404 neutro: la stessa frase per link inesistente, scaduto, revocato,
  // disattivato o per troppi tentativi (il server non li distingue, la pagina nemmeno).
  nonDisponibile: 'Questo link non è disponibile.',
  nonDisponibileAiuto: 'Contatta l’agenzia per riceverne uno nuovo.',
  rete: 'Non riusciamo a collegarci. Controlla la connessione e riprova.',
  nessunoSlotSettimana: 'Nessun orario disponibile in questi giorni.',
  nessunoSlot: 'Al momento non ci sono orari disponibili nelle prossime settimane.',
  orarioPreso: 'Questo orario non è più disponibile. Scegline un altro.',
  datiNonValidi: 'Controlla i dati inseriti: nome e telefono sono obbligatori, l’email deve essere valida.',
  invioNonRiuscito: 'Non è stato possibile completare la prenotazione. Il link potrebbe non essere più '
    + 'disponibile oppure sono stati fatti troppi tentativi: riprova tra qualche minuto.',
  esitoIncerto: 'Non abbiamo ricevuto la conferma. Premi “Riprova”: la prenotazione non verrà duplicata.',
};

// ---------------------------------------------------------------------------
// Stato: in memoria, in questo modulo e basta.
// ---------------------------------------------------------------------------

const token = tokenDaPercorso(globalThis.window && window.location ? window.location.pathname : '');
let app = null;
let info = null;                 // { agenzia, agente, tipo, durata, submissionToken }
let oggi = null;                 // 'YYYY-MM-DD' di Roma all'apertura
let finestre = [];               // giorni di inizio delle finestre visitate
let indice = 0;                  // finestra corrente
let slotServer = [];             // gli slot dell'ULTIMA risposta del server
let giorni = [];                 // giorniConSlot(slotServer)
let giornoScelto = null;
let slotScelto = null;
let cliente = { nome: '', telefono: '', email: '' };
let invioBloccato = null;        // il corpo gia' inviato una volta (retry identico)
let inVolo = false;
let conferma = null;
let generazione = 0;             // scarta le risposte superate
let nessunoTrovato = false;      // la ricerca automatica non ha trovato orari

// ---------------------------------------------------------------------------
// Rete: solo l'API A30-12, stessa origine, niente cookie, niente cache.
// ---------------------------------------------------------------------------

async function chiama(percorso, { metodo = 'GET', corpo } = {}) {
  const controllo = typeof AbortController === 'function' ? new AbortController() : null;
  const timer = controllo ? setTimeout(() => controllo.abort(), TIMEOUT_MS) : null;
  const opzioni = {
    method: metodo,
    headers: corpo === undefined ? { Accept: 'application/json' }
      : { Accept: 'application/json', 'Content-Type': 'application/json' },
    credentials: 'omit',
    cache: 'no-store',
    referrerPolicy: 'no-referrer',
  };
  if (corpo !== undefined) opzioni.body = JSON.stringify(corpo);
  if (controllo) opzioni.signal = controllo.signal;
  try {
    const r = await fetch(API_BASE + encodeURIComponent(token) + percorso, opzioni);
    let json = null;
    try { json = await r.json(); } catch (_e) { json = null; }
    return { stato: r.status, json };
  } catch (_e) {
    return { stato: 0, json: null };           // rete: nessun dettaglio tecnico
  } finally {
    if (timer) clearTimeout(timer);
  }
}

// ---------------------------------------------------------------------------
// DOM: solo createElement + textContent (mai innerHTML con dati).
// ---------------------------------------------------------------------------

function el(tag, classe, testo) {
  const nodo = document.createElement(tag);
  if (classe) nodo.className = classe;
  if (testo !== undefined && testo !== null) nodo.textContent = String(testo);
  return nodo;
}

function bottone(testo, classe, azione) {
  const b = el('button', classe, testo);
  b.setAttribute('type', 'button');
  b.addEventListener('click', azione);
  return b;
}

function titolo(testo) {
  const h = el('h2', 'step-title', testo);
  h.setAttribute('tabindex', '-1');
  return h;
}

function avviso(testo, tipo = 'error') {
  const p = el('p', `notice notice-${tipo}`, testo);
  p.setAttribute('role', tipo === 'error' ? 'alert' : 'status');
  return p;
}

function mostra(...sezioni) {
  app.replaceChildren(...sezioni);
  const h = app.querySelector('.step-title');
  if (h && typeof h.focus === 'function') h.focus();
}

function intestazione() {
  const box = el('section', 'panel summary-card');
  box.setAttribute('aria-label', 'Dettagli appuntamento');
  if (info.agenzia) box.append(el('p', 'eyebrow', info.agenzia));
  box.append(el('h1', 'booking-title', info.tipo));
  if (info.agente) box.append(el('p', 'agent-line', `con ${info.agente}`));
  const chips = el('ul', 'chips');
  chips.setAttribute('aria-label', 'Durata');
  chips.append(el('li', 'chip', `Durata: ${durataTesto(info.durata)}`));
  box.append(chips);
  return box;
}

// ---------------------------------------------------------------------------
// Stati
// ---------------------------------------------------------------------------

function mostraNonDisponibile() {
  const box = el('section', 'panel state-panel');
  box.append(titolo(TESTI.nonDisponibile), el('p', 'muted', TESTI.nonDisponibileAiuto));
  box.dataset.state = 'unavailable';
  mostra(box);
}

function mostraErroreRete(riprova) {
  const box = el('section', 'panel state-panel');
  box.dataset.state = 'network-error';
  box.append(titolo('Connessione non riuscita'), el('p', 'muted', TESTI.rete),
    bottone('Riprova', 'btn btn-primary', riprova));
  mostra(box);
}

function mostraCaricamento(testo = 'Caricamento…') {
  const box = el('section', 'panel state-panel');
  box.dataset.state = 'loading';
  box.setAttribute('role', 'status');
  const spin = el('div', 'spinner');
  spin.setAttribute('aria-hidden', 'true');
  box.append(spin, el('p', '', testo));
  app.replaceChildren(...(info ? [intestazione(), box] : [box]));
}

function mostraScelta(messaggio) {
  const box = el('section', 'panel');
  box.dataset.step = 'choose';
  box.append(titolo('Scegli giorno e orario'));
  if (messaggio) box.append(avviso(messaggio));

  const f = finestra(finestre[indice], Date.now());
  const nav = el('div', 'week-nav');
  const prec = bottone('‹ Giorni precedenti', 'btn btn-ghost', () => vaiA(indice - 1));
  prec.dataset.prev = '';
  prec.disabled = indice === 0;
  const succ = bottone('Giorni successivi ›', 'btn btn-ghost', () => vaiA(indice + 1));
  succ.dataset.next = '';
  succ.disabled = indice + 1 >= SETTIMANE_MASSIME;
  const periodo = el('p', 'week-range',
    f ? `${giornoLungo(f.inizio)} – ${giornoLungo(aggiungiGiorni(f.fine, -1))}` : '');
  nav.append(prec, periodo, succ);
  box.append(nav);

  if (!giorni.length) {
    const vuoto = el('div', 'empty-state');
    vuoto.dataset.empty = '';
    vuoto.append(el('p', '', nessunoTrovato || indice + 1 >= SETTIMANE_MASSIME
      ? TESTI.nessunoSlot : TESTI.nessunoSlotSettimana));
    box.append(vuoto);
    mostra(intestazione(), box);
    return;
  }

  const elencoGiorni = el('div', 'day-list');
  elencoGiorni.setAttribute('role', 'group');
  elencoGiorni.setAttribute('aria-label', 'Giorni disponibili');
  for (const g of giorni) {
    const b = giornoBreve(g.giorno);
    const btn = bottone('', 'day-btn', () => { giornoScelto = g.giorno; slotScelto = null; mostraScelta(); });
    btn.append(el('span', 'day-week', b.settimana), el('span', 'day-num', b.numero), el('span', 'day-month', b.mese));
    btn.setAttribute('aria-label', giornoLungo(g.giorno));
    btn.setAttribute('aria-pressed', g.giorno === giornoScelto ? 'true' : 'false');
    btn.dataset.day = '';
    elencoGiorni.append(btn);
  }
  box.append(el('h3', 'section-label', 'Giorno'), elencoGiorni);

  const scelto = giorni.find((g) => g.giorno === giornoScelto);
  if (scelto) {
    const griglia = el('div', 'slot-grid');
    griglia.setAttribute('role', 'group');
    griglia.setAttribute('aria-label', `Orari disponibili, ${giornoLungo(scelto.giorno)}`);
    for (const s of scelto.slot) {
      const btn = bottone(ora(s.inizioMs), 'slot-btn', () => { slotScelto = s; mostraDati(); });
      btn.setAttribute('aria-pressed', s === slotScelto ? 'true' : 'false');
      btn.dataset.slot = '';
      griglia.append(btn);
    }
    box.append(el('h3', 'section-label', `Orario · ${giornoLungo(scelto.giorno)}`), griglia);
  }
  mostra(intestazione(), box);
}

function campo(id, etichetta, valore, { tipo = 'text', obbligatorio = false, max, autocomplete, inputmode, errore }) {
  const w = el('div', 'field');
  const l = el('label', '', obbligatorio ? `${etichetta} *` : `${etichetta} (facoltativa)`);
  l.setAttribute('for', id);
  const i = el('input', 'input');
  i.id = id;
  i.setAttribute('type', tipo);
  i.setAttribute('name', id);
  i.setAttribute('maxlength', String(max));
  if (autocomplete) i.setAttribute('autocomplete', autocomplete);
  if (inputmode) i.setAttribute('inputmode', inputmode);
  if (obbligatorio) i.setAttribute('required', '');
  i.value = valore || '';
  w.append(l, i);
  if (errore) {
    const e = el('p', 'field-error', errore);
    e.id = `${id}-errore`;
    i.setAttribute('aria-invalid', 'true');
    i.setAttribute('aria-describedby', e.id);
    w.append(e);
  }
  return { w, i };
}

function mostraDati(errori = {}, messaggio = null) {
  const box = el('section', 'panel');
  box.dataset.step = 'details';
  box.append(titolo('I tuoi dati'));
  box.append(el('p', 'muted', `${giornoLungo(slotScelto.inizioMs)}, ore ${ora(slotScelto.inizioMs)}`));
  if (messaggio) box.append(avviso(messaggio));
  const form = el('form', 'form');
  form.setAttribute('novalidate', '');
  const n = campo('nome', 'Nome e cognome', cliente.nome,
    { obbligatorio: true, max: LIMITI.nome, autocomplete: 'name', errore: errori.nome });
  const t = campo('telefono', 'Telefono', cliente.telefono,
    { tipo: 'tel', obbligatorio: true, max: LIMITI.telefono, autocomplete: 'tel', inputmode: 'tel', errore: errori.telefono });
  const e = campo('email', 'Email', cliente.email,
    { tipo: 'email', max: LIMITI.email, autocomplete: 'email', inputmode: 'email', errore: errori.email });
  const azioni = el('div', 'actions');
  const indietro = bottone('Cambia orario', 'btn btn-ghost', () => { leggi(); mostraScelta(); });
  const avanti = el('button', 'btn btn-primary', 'Continua');
  avanti.setAttribute('type', 'submit');
  avanti.dataset.continue = '';
  azioni.append(indietro, avanti);
  form.append(n.w, t.w, e.w, el('p', 'hint', '* campi obbligatori'), azioni);
  function leggi() { cliente = { nome: n.i.value, telefono: t.i.value, email: e.i.value }; }
  form.addEventListener('submit', (ev) => {
    if (ev && typeof ev.preventDefault === 'function') ev.preventDefault();
    leggi();
    const esito = controllaCliente(cliente);
    if (!esito.valido) { mostraDati(esito.errori); return; }
    cliente = esito.dati;
    mostraRiepilogo();
  });
  box.append(form);
  mostra(intestazione(), box);
}

function riga(dl, etichetta, valore) {
  if (!valore) return;
  dl.append(el('dt', '', etichetta), el('dd', '', valore));
}

function mostraRiepilogo(messaggio = null, { soloRiprova = false } = {}) {
  const box = el('section', 'panel');
  box.dataset.step = 'review';
  box.append(titolo(soloRiprova ? 'Conferma in sospeso' : 'Controlla e conferma'));
  if (messaggio) box.append(avviso(messaggio));
  const dl = el('dl', 'review');
  riga(dl, 'Data', giornoLungo(slotScelto.inizioMs));
  riga(dl, 'Ora', `${ora(slotScelto.inizioMs)} – ${ora(slotScelto.fineMs)}`);
  riga(dl, 'Durata', durataTesto(info.durata));
  riga(dl, 'Con', info.agente);
  riga(dl, 'Tipo', info.tipo);
  riga(dl, 'Nome', cliente.nome);
  riga(dl, 'Telefono', cliente.telefono);
  riga(dl, 'Email', cliente.email);
  box.append(dl);
  const azioni = el('div', 'actions');
  if (!soloRiprova) {
    azioni.append(bottone('Modifica', 'btn btn-ghost', () => mostraDati()));
  }
  const ok = bottone(soloRiprova ? 'Riprova' : 'Conferma prenotazione', 'btn btn-primary', invia);
  ok.dataset.confirm = '';
  azioni.append(ok);
  box.append(azioni);
  mostra(intestazione(), box);
}

function mostraConfermata() {
  const box = el('section', 'panel success-panel');
  box.dataset.step = 'done';
  const segno = el('div', 'success-mark', '✓');
  segno.setAttribute('aria-hidden', 'true');
  box.append(segno, titolo('Prenotazione confermata'));
  const dl = el('dl', 'review');
  riga(dl, 'Giorno', giornoLungo(conferma.inizioMs));
  riga(dl, 'Ora', conferma.fineMs ? `${ora(conferma.inizioMs)} – ${ora(conferma.fineMs)}` : ora(conferma.inizioMs));
  riga(dl, 'Con', info.agente);
  riga(dl, 'Tipo', info.tipo);
  box.append(dl);
  if (info.agenzia) box.append(el('p', 'muted', `Per modifiche o annullamenti contatta ${info.agenzia}.`));
  mostra(box);
}

// ---------------------------------------------------------------------------
// Flusso
// ---------------------------------------------------------------------------

async function caricaInfo() {
  const r = await chiama('');
  if (r.stato === 200) {
    const nuova = infoPubblica(r.json);
    if (nuova) return { esito: 'ok', info: nuova };
    return { esito: 'rete' };
  }
  if (r.stato === 404) return { esito: 'non_disponibile' };
  return { esito: 'rete' };
}

async function caricaFinestra(i) {
  const f = finestra(finestre[i], Date.now());
  if (!f) return { esito: 'ok', slot: [] };
  const r = await chiama(`/slots?from=${encodeURIComponent(f.da)}&to=${encodeURIComponent(f.a)}`);
  if (r.stato === 200) return { esito: 'ok', slot: slotDelServer(r.json) };
  if (r.stato === 404) return { esito: 'non_disponibile' };
  return { esito: 'rete' };
}

function applicaSlot(slot) {
  slotServer = slot;
  giorni = giorniConSlot(slotServer);
  if (!giorni.some((g) => g.giorno === giornoScelto)) giornoScelto = giorni.length ? giorni[0].giorno : null;
  slotScelto = null;
}

function assicuraFinestra(i) {
  while (finestre.length <= i) {
    const ultima = finestre[finestre.length - 1];
    const f = finestra(ultima, Date.now());
    finestre.push(f ? f.fine : aggiungiGiorni(ultima, 7));
  }
}

async function vaiA(i, { automatico = false, messaggio = null } = {}) {
  if (i < 0 || i >= SETTIMANE_MASSIME) return;
  const mia = ++generazione;
  assicuraFinestra(i);
  indice = i;
  mostraCaricamento('Cerco gli orari disponibili…');
  const r = await caricaFinestra(i);
  if (mia !== generazione) return;
  if (r.esito === 'non_disponibile') { mostraNonDisponibile(); return; }
  if (r.esito === 'rete') { mostraErroreRete(() => vaiA(i, { automatico, messaggio })); return; }
  applicaSlot(r.slot);
  if (!giorni.length && automatico && i + 1 < SETTIMANE_AUTOMATICHE) {
    await vaiA(i + 1, { automatico, messaggio });
    return;
  }
  // nessun orario nelle prime settimane cercate da sole: lo si dice chiaramente
  // (si puo' comunque continuare a sfogliare con "Giorni successivi").
  nessunoTrovato = !giorni.length && automatico;
  mostraScelta(messaggio);
}

async function avvia() {
  if (!token) { mostraNonDisponibile(); return; }
  mostraCaricamento();
  const r = await caricaInfo();
  if (r.esito === 'non_disponibile') { mostraNonDisponibile(); return; }
  if (r.esito === 'rete') { mostraErroreRete(avvia); return; }
  info = r.info;
  oggi = dataRoma(Date.now());
  finestre = [oggi];
  await vaiA(0, { automatico: true });
}

/** Dopo un 409: un nuovo submission_token (il precedente e' fallito) e gli slot aggiornati. */
async function ricaricaDopoConflitto() {
  const r = await caricaInfo();
  if (r.esito === 'non_disponibile') { mostraNonDisponibile(); return; }
  if (r.esito === 'rete') { mostraErroreRete(ricaricaDopoConflitto); return; }
  info = r.info;
  await vaiA(indice, { messaggio: TESTI.orarioPreso });
}

async function invia() {
  if (inVolo) return;                           // doppio click: un solo invio
  if (!invioBloccato) {
    invioBloccato = corpoPrenotazione(info.submissionToken, slotScelto, slotServer, cliente);
  }
  inVolo = true;
  const b = app.querySelector('[data-confirm]');
  if (b) { b.disabled = true; b.textContent = 'Invio in corso…'; }
  const r = await chiama('/submit', { metodo: 'POST', corpo: invioBloccato });
  inVolo = false;
  if (r.stato === 201 || r.stato === 200) {
    const c = confermaPubblica(r.json);
    if (c) {
      conferma = c;
      info = { ...info, submissionToken: null };
      invioBloccato = null;
      mostraConfermata();
      return;
    }
    mostraRiepilogo(TESTI.esitoIncerto, { soloRiprova: true });
    return;
  }
  if (r.stato === 409) {
    invioBloccato = null;                       // tentativo fallito: si puo' ricominciare
    info = { ...info, submissionToken: null };
    await ricaricaDopoConflitto();
    return;
  }
  if (r.stato === 422) {
    invioBloccato = null;                       // il server non ha registrato nulla
    mostraDati({}, TESTI.datiNonValidi);
    return;
  }
  if (r.stato === 404) {
    mostraRiepilogo(TESTI.invioNonRiuscito, { soloRiprova: true });
    return;
  }
  mostraRiepilogo(TESTI.esitoIncerto, { soloRiprova: true });
}

function monta() {
  if (typeof document === 'undefined') return;
  app = document.getElementById('booking-app');
  if (!app) return;
  avvia();
}

monta();
