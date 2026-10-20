// STIMA360 OS — voice/voice-api.js
// STIMA Voice Fase 6: le chiamate a /api/voice (Fase 5). Solo il client
// condiviso (`core/api-client.js`): cookie di sessione, nessun header
// Authorization, nessun agency_id. Il server decide chi parla e cosa vede.
//
// La chiave del comando (`client_command_id`) nasce qui, in memoria, e vive
// quanto il testo che rappresenta: lo stesso testo reinviato dopo un errore di
// rete riusa la stessa chiave (il server non crea un secondo comando); un
// testo modificato e' un comando nuovo. Nessuno storage del browser.

import { apiGet, apiPost } from '../core/api-client.js';

const BASE = '/api/voice';

/**
 * Lo stato di STIMA Voice per l'operatore. Fail-closed: qualunque errore
 * (router non montato = 404, sessione, rete) vale «spento».
 */
let ultimoStato = { enabled: false, speechInput: false };

export async function voiceStatus(get = apiGet) {
  try {
    const s = await get(`${BASE}/status`);
    const enabled = Boolean(s && s.enabled === true);
    ultimoStato = { enabled, speechInput: enabled && s.speech_input === true };
  } catch (_error) {
    ultimoStato = { enabled: false, speechInput: false };
  }
  return ultimoStato;
}

/** V8-5: l'ultimo stato letto dal server in questa sessione (la shell lo
 *  chiede all'avvio); senza una lettura, spento. Nessuna richiesta. */
export function lastVoiceStatus() {
  return ultimoStato;
}

/** V8-5: solo interpretazione, per precompilare il dialog «Nuovo appuntamento». */
export const interpretText = (text, post = apiPost) => post(`${BASE}/interpret`, { text });

/** Un UUID v4 dalla crittografia del browser (mai Math.random). */
export function newCommandKey(cryptoImpl = globalThis.crypto) {
  if (cryptoImpl && typeof cryptoImpl.randomUUID === 'function') return cryptoImpl.randomUUID();
  const b = new Uint8Array(16);
  cryptoImpl.getRandomValues(b);
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = [...b].map((x) => x.toString(16).padStart(2, '0')).join('');
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

const percorso = (id, resto = '') => `${BASE}/commands/${Number(id)}${resto}`;

export const submitCommand = (key, text, post = apiPost) => post(`${BASE}/commands`, { client_command_id: key, text });
export const getCommand = (id, get = apiGet) => get(percorso(id));
/** Fase 7: risposta o correzione a un comando non ancora eseguito (nuova chiave). */
export const replyCommand = (id, key, text, post = apiPost) =>
  post(percorso(id, '/reply'), { client_command_id: key, text });
export const confirmSteps = (id, steps, post = apiPost) => post(percorso(id, '/confirm'), { all: false, steps });
export const executeCommand = (id, post = apiPost) => post(percorso(id, '/execute'), {});
export const cancelCommand = (id, post = apiPost) => post(percorso(id, '/cancel'), {});
export const retryStep = (id, n, post = apiPost) => post(percorso(id, `/steps/${Number(n)}/retry`), {});
export const undoStep = (id, n, post = apiPost) => post(percorso(id, `/steps/${Number(n)}/undo`), {});
export const reconcileInspect = (id, n, get = apiGet) => get(percorso(id, `/steps/${Number(n)}/reconcile`));
export const reconcileDecide = (id, n, outcome, recordId = null, post = apiPost) =>
  post(percorso(id, `/steps/${Number(n)}/reconcile`), { outcome, record_id: recordId });
