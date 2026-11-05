// STIMA360 OS — agenda/agenda-prefs.js (AGENDA MOBILE)
//
// L'UNICO punto della OS Shell che usa uno storage del browser, per una sola
// preferenza di visualizzazione: l'ultima vista dell'Agenda (Giorno,
// Settimana, Mese, Lista), cosi' chi rientra la ritrova.
//
// Cosa NON finisce mai qui: credenziali, token, sessioni, identificativi,
// nomi, filtri, date, contenuti degli appuntamenti. Si scrive e si legge SOLO
// uno dei quattro nomi di vista ammessi; qualunque altro valore letto viene
// ignorato. Ogni accesso e' protetto: con lo storage bloccato (navigazione
// privata, dati del sito cancellati) l'Agenda funziona lo stesso e parte
// dalla vista iniziale di sempre.

import { VIEWS } from './agenda-model.js';

const CHIAVE = 'stima360.agenda.vista';

function archivio() {
  try {
    const s = globalThis.localStorage;
    return s && typeof s.getItem === 'function' ? s : null;
  } catch (_e) {
    return null;
  }
}

/** L'ultima vista scelta, o null. */
export function readPreferredView() {
  try {
    const s = archivio();
    const valore = s ? s.getItem(CHIAVE) : null;
    return VIEWS.includes(valore) ? valore : null;
  } catch (_e) {
    return null;
  }
}

/** Ricorda la vista scelta (solo uno dei quattro nomi ammessi). */
export function savePreferredView(view) {
  if (!VIEWS.includes(view)) return;
  try {
    const s = archivio();
    if (s) s.setItem(CHIAVE, view);
  } catch (_e) {
    // storage pieno o bloccato: la preferenza semplicemente non resta.
  }
}

export const PREFERENCE_KEY = CHIAVE;
