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
//   * niente in sottofondo: `continuous = false` (una frase), stop volontario,
//     e la vista interrompe la dettatura quando la pagina si nasconde;
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
export function createDictation({ win = globalThis.window, lang = 'it-IT', onText, onState, onError } = {}) {
  const { ctor } = speechSupport(win);
  let rec = null;
  let finale = '';

  const stato = (s) => { if (typeof onState === 'function') onState(s); };

  function start() {
    if (!ctor) { if (onError) onError('error'); return false; }
    if (rec) return false;
    finale = '';
    rec = new ctor();
    rec.lang = lang;
    rec.continuous = false;
    rec.interimResults = true;
    rec.maxAlternatives = 1;
    rec.onresult = (event) => {
      let provvisorio = '';
      for (let i = event.resultIndex || 0; i < event.results.length; i += 1) {
        const risultato = event.results[i];
        const testo = risultato[0] ? risultato[0].transcript : '';
        if (risultato.isFinal) finale += testo; else provvisorio += testo;
      }
      if (typeof onText === 'function') onText((finale + provvisorio).trim(), provvisorio === '');
    };
    rec.onerror = (event) => {
      const codice = event && event.error;
      if (codice === 'aborted') return;
      if (typeof onError === 'function') onError(ERRORI[codice] || 'error');
    };
    rec.onend = () => { rec = null; stato('idle'); };
    try {
      rec.start();
    } catch (_e) {
      rec = null;
      if (typeof onError === 'function') onError('error');
      stato('idle');
      return false;
    }
    stato('listening');
    return true;
  }

  /** Stop volontario: il browser consegna l'ultimo risultato e chiude. */
  function stop() { if (rec) rec.stop(); }

  /** Interruzione senza risultato (pagina nascosta, uscita dalla vista). */
  function abort() {
    if (!rec) return;
    const r = rec;
    rec = null;
    try { r.abort(); } catch (_e) { /* gia' chiuso */ }
    stato('idle');
  }

  return { start, stop, abort, get listening() { return rec !== null; } };
}
