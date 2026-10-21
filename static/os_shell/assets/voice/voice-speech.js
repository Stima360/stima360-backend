// STIMA360 OS — voice/voice-speech.js
// STIMA Voice Fase 6: la dettatura con il riconoscimento vocale DEL BROWSER
// (Web Speech API: `SpeechRecognition`, su Safari e Chrome `webkitSpeechRecognition`).
//
// Regole:
//   * nessuna richiesta del microfono prima di un'azione esplicita: il
//     riconoscitore nasce SOLO dentro `start()`, che la vista chiama dal tocco
//     sul pulsante;
//   * nessun audio passa da STIMA360: questo modulo non apre il microfono
//     (niente getUserMedia), non registra e non carica file. Riceve solo il
//     TESTO riconosciuto, che finisce nel campo modificabile; parte verso il
//     server solo con «Invia»;
//   * niente in sottofondo: la dettatura vive solo fra «Parla» e «Termina dettatura»
//     (vedi `createDictation`), e la vista la interrompe quando la pagina si nasconde;
//   * il supporto non si presume: senza API resta il campo di testo.
//
// ATTENZIONE: il riconoscimento del browser puo' elaborare l'audio sui server
// del fornitore del browser (Google per Chrome, Apple per Safari). Per questo
// il pulsante compare solo se il server lo consente (`speech_input`).

export function speechSupport(win = globalThis.window) {
  const ctor = win && (win.SpeechRecognition || win.webkitSpeechRecognition);
  return { supported: typeof ctor === 'function', ctor: typeof ctor === 'function' ? ctor : null };
}

const ERRORI = {
  'not-allowed': 'permission_denied',
  'service-not-allowed': 'permission_denied',
  'audio-capture': 'no_microphone',
  'no-speech': 'no_speech',
  network: 'network',
};

/**
 * Una dettatura. `onText(testo, finale)` a ogni risultato, `onState('listening'|'idle')`,
 * `onError(codice)` con i codici di SPEECH_ERRORS (voice-model.js).
 */
// ---------------------------------------------------------------------------
// Collaudo iPhone del 21/10: come si uniscono i pezzi riconosciuti.
//
// Chrome da computer apre ogni risultato con uno spazio; WebKit (Safari e
// Chrome su iPhone) no: concatenare senza spazio dava «EhiCrea». Su iPhone,
// inoltre, un risultato puo' RIPETERE i precedenti (trascrizione cumulativa) e
// una sessione nuova puo' riconsegnare l'ultima parola della precedente:
// «RuvoRuvo». Le regole, pure e provate dai test:
//   * i pezzi si uniscono SEMPRE con uno spazio;
//   * un pezzo che comincia con tutto il testo gia' detto lo SOSTITUISCE
//     (cumulativo); un pezzo gia' contenuto alla fine del testo si scarta;
//   * se la fine del testo e l'inizio del pezzo coincidono (parole intere, senza
//     maiuscole ne' accenti), la parte comune si scrive una volta sola;
//   * parole incollate da un motore che non mette spazi («rivenditaDomani»)
//     si separano al passaggio minuscola -> Maiuscola.
// ---------------------------------------------------------------------------

const piegaParola = (p) => String(p || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '')
  .toLowerCase().replace(/[^a-z0-9:]+/g, '');

/** «rivenditaDomani» -> «rivendita Domani»; «10:30Compratore» -> «10:30 Compratore». */
export function separaIncollate(testo) {
  return String(testo || '')
    .replace(/([a-zà-ÿ0-9])([A-ZÀ-Þ])/g, '$1 $2')
    .replace(/\s+/g, ' ')
    .trim();
}

/** Unisce il testo gia' riconosciuto e un pezzo nuovo, senza incollare e senza ripetere. */
export function unisciPezzi(gia, pezzo) {
  const a = separaIncollate(gia);
  const b = separaIncollate(pezzo);
  if (!a) return b;
  if (!b) return a;
  const pa = a.split(' ');
  const pb = b.split(' ');
  const fa = pa.map(piegaParola);
  const fb = pb.map(piegaParola);
  // cumulativo: il pezzo ripete tutto il testo gia' detto e aggiunge
  if (fb.length >= fa.length && fa.every((p, i) => p === fb[i])) return b;
  // gia' detto: il pezzo e' la coda del testo
  if (fb.length <= fa.length && fb.every((p, i) => p === fa[fa.length - fb.length + i])) return a;
  // sovrapposizione: la fine del testo e' l'inizio del pezzo
  for (let k = Math.min(fa.length, fb.length); k >= 1; k -= 1) {
    let uguali = true;
    for (let i = 0; i < k; i += 1) if (fa[fa.length - k + i] !== fb[i]) { uguali = false; break; }
    if (uguali) return [...pa, ...pb.slice(k)].join(' ');
  }
  return `${a} ${b}`;
}

/** Il testo di una sessione dai risultati del browser, in ordine, senza ripetizioni. */
export function testoDeiRisultati(results) {
  let finale = '';
  let provvisorio = '';
  for (let i = 0; i < (results ? results.length : 0); i += 1) {
    const r = results[i];
    const t = r && r[0] ? r[0].transcript : '';
    if (r && r.isFinal) finale = unisciPezzi(finale, t); else provvisorio = unisciPezzi(provvisorio, t);
  }
  return { finale, provvisorio, testo: unisciPezzi(finale, provvisorio) };
}

/** iPhone e iPad (anche iPadOS che si presenta come Mac): Safari e Chrome usano WebKit. */
export function isAppleMobile(win = globalThis.window) {
  const nav = win && win.navigator;
  if (!nav) return false;
  const ua = String(nav.userAgent || '');
  return /iPhone|iPad|iPod/.test(ua) || (nav.platform === 'MacIntel' && Number(nav.maxTouchPoints) > 1);
}

/**
 * STIMA VOICE - la dettatura a CONTROLLO MANUALE (collaudo reale del 21/10).
 *
 *   * «Parla» avvia l'ascolto; finche' l'agente non preme «Termina dettatura» la
 *     dettatura resta aperta: le pause non la chiudono e il testo riconosciuto
 *     non si perde;
 *   * Chrome (anche su Mac) chiude da solo il riconoscimento dopo un silenzio o
 *     dopo un po' di tempo: qui si riapre, con il testo gia' detto conservato;
 *   * MAI un riavvio dopo uno stop volontario, un diniego del microfono, un
 *     microfono assente o un errore di rete; al massimo `maxSilentRestarts`
 *     riaperture di fila senza parlato e `maxRestarts` in tutto, e non oltre
 *     `maxMs` di dettatura: poi si chiude e lo si dice;
 *   * il testo e' «completo» solo dopo «Termina dettatura» (`onEnd` con
 *     `reason: 'user'`): chi chiama decide cosa farne; qui non si invia nulla;
 *   * `start(prefisso)`: il testo gia' nel campo resta in testa (si puo'
 *     correggere a mano e poi continuare a dettare).
 */
export function createDictation({
  win = globalThis.window, lang = 'it-IT', onText, onState, onError, onEnd,
  maxSilentRestarts = 6, maxRestarts = 60, maxMs = 5 * 60 * 1000,
  // Collaudo iPhone: su iOS ogni nuova sessione di riconoscimento puo' chiedere di
  // nuovo il permesso del microfono (limite di WebKit, non aggirabile). Li' NON si
  // riapre da soli: alla pausa la dettatura si ferma con il testo, e l'agente
  // sceglie se continuare (`onEnd(testo, 'paused')`).
  autoRestart = !isAppleMobile(win),
} = {}) {
  const { ctor } = speechSupport(win);
  let rec = null;
  let attivo = false;          // l'agente vuole ascoltare
  let chiusura = null;         // perche' sta finendo: 'user' | 'aborted' | 'error' | 'limit'
  let base = '';               // il testo gia' nel campo all'avvio
  let detto = '';              // il testo delle sessioni gia' chiuse
  let sessione = '';           // il testo della sessione aperta (finale + provvisorio)
  let silenzi = 0;
  let riavvii = 0;
  let inizio = 0;
  let timer = null;

  const stato = (s) => { if (typeof onState === 'function') onState(s); };
  const unisci = (...parti) => parti.reduce((acc, x) => unisciPezzi(acc, x), '');
  const testo = () => unisci(base, detto, sessione);

  function chiudi(motivo) {
    attivo = false;
    rec = null;
    clearTimeout(timer);
    timer = null;
    stato('idle');
    if (typeof onEnd === 'function') onEnd(testo(), motivo);
  }

  function apri() {
    rec = new ctor();
    rec.lang = lang;
    rec.continuous = true;
    rec.interimResults = true;
    rec.maxAlternatives = 1;
    const questa = rec;
    sessione = '';
    rec.onresult = (event) => {
      if (rec !== questa) return;
      const { provvisorio, testo: diSessione } = testoDeiRisultati(event.results);
      sessione = diSessione;
      if (sessione) silenzi = 0;
      if (typeof onText === 'function') onText(testo(), provvisorio === '');
    };
    rec.onerror = (event) => {
      if (rec !== questa) return;
      const codice = event && event.error;
      if (codice === 'aborted') return;
      if (codice === 'no-speech') {             // una pausa: non e' un errore, salvo che duri troppo
        silenzi += 1;
        if (silenzi > maxSilentRestarts) {
          chiusura = 'limit';
          if (typeof onError === 'function') onError('no_speech');
        }
        return;
      }
      chiusura = 'error';                      // diniego, microfono, rete...: mai un riavvio
      if (typeof onError === 'function') onError(ERRORI[codice] || 'error');
    };
    rec.onend = () => {
      if (rec !== questa) return;
      detto = unisci(detto, sessione);         // il testo della sessione chiusa resta
      sessione = '';
      const scaduto = Date.now() - inizio >= maxMs;
      if (attivo && !chiusura && !autoRestart) {
        chiudi('paused');                       // iPhone: nessuna riapertura automatica
        return;
      }
      if (attivo && !chiusura && !scaduto && riavvii < maxRestarts) {
        riavvii += 1;
        try {
          apri();
          return;
        } catch (_e) {
          chiusura = 'error';
          if (typeof onError === 'function') onError('error');
        }
      } else if (attivo && !chiusura) {
        chiusura = 'limit';
      }
      chiudi(chiusura || 'user');
    };
    rec.start();
  }

  function start(prefisso = '') {
    if (!ctor) { if (onError) onError('error'); return false; }
    if (attivo) return false;
    attivo = true;
    chiusura = null;
    base = String(prefisso || '');
    detto = '';
    sessione = '';
    silenzi = 0;
    riavvii = 0;
    inizio = Date.now();
    try {
      apri();
    } catch (_e) {
      rec = null;
      attivo = false;
      if (typeof onError === 'function') onError('error');
      stato('idle');
      return false;
    }
    timer = setTimeout(() => { if (attivo) { chiusura = 'limit'; if (rec) rec.stop(); } }, maxMs);
    if (timer && typeof timer.unref === 'function') timer.unref();   // fuori dal browser non trattiene il processo
    stato('listening');
    return true;
  }

  /** «Termina dettatura»: il browser consegna l'ultimo risultato, poi `onEnd(testo, 'user')`. */
  function stop() {
    if (!attivo) return;
    chiusura = chiusura || 'user';
    if (rec) rec.stop(); else chiudi(chiusura);
  }

  /** Interruzione senza riavvio (pagina nascosta, uscita): il testo gia' scritto resta nel campo. */
  function abort() {
    if (!attivo) return;
    chiusura = 'aborted';
    const r = rec;
    rec = null;
    attivo = false;
    clearTimeout(timer);
    try { if (r) r.abort(); } catch (_e) { /* gia' chiuso */ }
    stato('idle');
  }

  return { start, stop, abort, get listening() { return attivo; } };
}
