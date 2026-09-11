// STIMA360 OS — auth.js
//
// P26-3: la OS Shell autentica con la SESSIONE OPERATORE, non piu' con Basic.
//
// Cosa e' cambiato e perche'
// --------------------------
// Prima: username e password restavano in una variabile di modulo e ogni
// richiesta ricostruiva un header `Authorization: Basic`. Nessuna scrittura in
// localStorage - quello era gia' corretto - ma la password viveva comunque in
// memoria JS per tutta la sessione, e ogni richiesta la riesponeva.
//
// Adesso: la password viene inviata UNA volta, a /api/operator-auth/login, e
// non viene conservata da nessuna parte. Il server risponde 204 e mette il
// token in un cookie HttpOnly: il codice di pagina non puo' leggerlo, non puo'
// copiarlo e non puo' inoltrarlo altrove. Quello che questo modulo tiene in
// memoria e' soltanto la proiezione che /me restituisce - id operatore,
// agenzia, ruolo - che serve a disegnare la UI e non e' una credenziale.
//
// L'agenzia non viene mai decisa qui. Arriva da /me, che la legge dalla
// sessione lato server. Non esiste modo, in questo file, di chiederne un'altra.

let session = null;
const listeners = new Set();

// P26-4: quante volte la sessione e' cambiata da quando la pagina e' aperta.
//
// Serve a una cosa sola, e non e' un dettaglio: le view sono asincrone e
// scrivono nel DOM DOPO l'await. Se fra la richiesta e la risposta la sessione
// finisce - un 401, un logout, un altro operatore che entra - quella risposta
// appartiene a una sessione che non esiste piu' e non deve comparire sullo
// schermo. Chi ha iniziato un lavoro annota questo numero e, quando ha finito,
// controlla che sia ancora lo stesso.
//
// Un intero e non un booleano "autenticato": fra l'inizio e la fine ci puo'
// stare un logout E un login, e in quel caso `isAuthenticated()` risponderebbe
// di nuovo true mentre l'operatore, e l'agenzia, sono altri.
let epoch = 0;

export function sessionEpoch() {
  return epoch;
}

function notify() {
  epoch += 1;
  for (const fn of listeners) fn(session);
}

export function onAuthChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function getSession() {
  return session;
}

export function isAuthenticated() {
  return session !== null;
}

// `credentials: 'include'` su OGNI chiamata di questo modulo: senza, il browser
// non manda il cookie e non accetta il Set-Cookie della login.
const withCookie = (options = {}) => ({ ...options, credentials: 'include' });

async function call(path, options) {
  try {
    return await fetch(path, withCookie(options));
  } catch (networkError) {
    throw new Error('Impossibile contattare il server. Verifica la connessione.');
  }
}

/**
 * Restore the session from the cookie the browser already holds.
 *
 * Chiamata all'avvio. Un 401 qui e' normale - vuol dire che non c'e' sessione,
 * o che e' scaduta o revocata - e non e' un errore da mostrare: si finisce
 * semplicemente sulla schermata di login.
 */
export async function restore() {
  const response = await call('/api/operator-auth/me', { method: 'GET' });
  if (response.status === 401) {
    session = null;
    notify();
    return null;
  }
  if (!response.ok) {
    session = null;
    notify();
    throw new Error('Servizio di autenticazione non disponibile.');
  }
  session = await response.json();
  notify();
  return session;
}

/**
 * Log in with email and password.
 *
 * La password esiste solo dentro questa funzione: viene serializzata nel corpo
 * della richiesta e poi esce dallo scope. Non viene salvata, non viene passata
 * ad altri moduli e non compare in nessun header successivo.
 */
export async function login(email, password) {
  const response = await call('/api/operator-auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  });

  if (response.status === 401) {
    throw new Error('Credenziali non valide.');
  }
  if (!response.ok) {
    throw new Error('Servizio di autenticazione non disponibile.');
  }

  // La login risponde 204 e non dice CHI ha autenticato: il token sta nel
  // cookie e nient'altro. L'identita' si chiede a /me, che e' la sola fonte.
  return restore();
}

/**
 * Log out for real: the server revokes the session and clears the cookie.
 *
 * Lo stato locale viene azzerato comunque, anche se la chiamata fallisce: se
 * il server non e' raggiungibile la cosa giusta e' comunque riportare l'utente
 * alla schermata di login, non lasciarlo davanti a una UI che sembra attiva.
 */
export async function logout() {
  try {
    await call('/api/operator-auth/logout', { method: 'POST' });
  } catch (_ignored) {
    // vedi sopra
  }
  session = null;
  notify();
}

/**
 * Called by the API client when a request comes back 401.
 *
 * Azzera lo stato e notifica, senza rifare una chiamata di rete: e' gia' un
 * 401 ad averla provocata, e ritentare da qui e' il modo classico di costruire
 * un ciclo login/401/login. La schermata di login compare perche' i listener
 * reagiscono a `session === null`.
 */
export function sessionExpired() {
  if (session === null) return;
  session = null;
  notify();
}
