// STIMA360 OS — agenda/calendar-sync-api.js (A30-9B)
//
// L'UNICO punto della OS Shell che chiama `/api/calendar/google`. Un file
// SEPARATO da `agenda-api.js` apposta: quel modulo dichiara di parlare solo
// con `/api/appointments` (A30-4), e questo non lo cambia di una riga - la
// riga d'apertura di quel file resta vera.
//
// Stesso contratto di rete di `agenda-api.js` (nessun header Authorization,
// il cookie di sessione basta con `credentials: 'include'`; un 401 chiama
// `sessionExpired()`), ripetuto qui invece di condiviso perche' nessuno dei
// due file tocca l'altro.
//
// L'URL di autorizzazione Google e' SEMPRE quello che il server restituisce
// da `POST /connect` (`authorization_url`): questo modulo non costruisce mai
// da solo un URL verso `accounts.google.com`, non conosce ne' client_id ne'
// client_secret, e non li chiede al server per nessuna ragione.

import { sessionExpired } from '../core/auth.js';

const BASE = '/api/calendar/google';

async function request(method, path, body) {
  const opzioni = {
    method,
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
  };
  if (body !== undefined) opzioni.body = JSON.stringify(body);

  let risposta;
  try {
    risposta = await fetch(`${BASE}${path}`, opzioni);
  } catch (_errore) {
    const errore = new Error('Impossibile contattare il server. Verifica la connessione.');
    errore.status = 0;
    throw errore;
  }

  if (risposta.status === 401) {
    sessionExpired();
    const errore = new Error('Sessione scaduta. Effettua di nuovo il login.');
    errore.status = 401;
    throw errore;
  }

  let dati = null;
  try {
    dati = await risposta.json();
  } catch (_errore) {
    dati = null;
  }

  if (!risposta.ok) {
    const dettaglio = dati && typeof dati.detail === 'string' ? dati.detail : '';
    const errore = new Error(dettaglio || `Errore ${risposta.status}`);
    errore.status = risposta.status;
    errore.detail = dettaglio;
    errore.code = dati && typeof dati.code === 'string' ? dati.code : '';
    throw errore;
  }
  return dati;
}

/** GET /status: mai un campo sensibile (token, provider_subject, segreti) -
 *  solo cio' che serve per disegnare il pannello. */
export function getGoogleCalendarStatus() {
  return request('GET', '/status');
}

/** POST /connect: il server genera lo state/PKCE e torna l'URL di Google.
 *  Il chiamante deve fare `window.location.assign(authorization_url)` - MAI
 *  costruire quell'URL da solo. */
export function connectGoogleCalendar() {
  return request('POST', '/connect');
}

export function disconnectGoogleCalendar() {
  return request('POST', '/disconnect');
}

export function resyncGoogleCalendar() {
  return request('POST', '/resync');
}
