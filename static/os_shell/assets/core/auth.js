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

// P28 - LE ALTRE SCHEDE DELLO STESSO BROWSER.
//
// Due schede aperte NON sono due sessioni: condividono il cookie, quindi
// condividono la stessa riga di `operator_sessions` e quindi lo stesso contesto
// di agenzia. Quando una entra in un'agenzia o ne esce, le altre stanno
// mostrando una barra sbagliata e - molto peggio - i dati dell'agenzia
// precedente, che il server non servirebbe piu'.
//
// Non e' una svista che si corregge da sola al prossimo clic: una scheda ferma
// su un elenco resta ferma, e quell'elenco e' esattamente la cosa che P26
// esiste per non far vedere a chi non deve.
//
// `BroadcastChannel` e' il minimo che risolve: nessuna libreria, nessun
// polling, nessuno stato condiviso da tenere allineato. Chi riceve il
// messaggio non si fida del suo contenuto - che infatti non porta dati - e
// RILEGGE `/me`. Il server resta l'unico a decidere in quale agenzia si sta;
// il messaggio dice soltanto "e' cambiato qualcosa, richiedilo".
//
// `try/catch` perche' l'API puo' mancare (browser vecchi, contesti non
// sicuri): senza canale la Shell funziona esattamente come prima di P28, e una
// scheda dimenticata si riallinea al primo caricamento. Degradare in silenzio
// e' giusto qui - non stiamo perdendo una difesa, stiamo perdendo una
// comodita' - ma la difesa vera resta il server, che a quella scheda non
// risponderebbe comunque con i dati dell'altra agenzia.
const CANALE_ACTING = 'stima360-acting';

let canale = null;
try {
  canale = new BroadcastChannel(CANALE_ACTING);
  // `unref()` NON esiste nel browser, ed e' proprio per questo che c'e'.
  //
  // Un canale aperto e' una risorsa attiva: in un browser non cambia niente -
  // la pagina vive finche' la scheda e' aperta - ma in node TIENE VIVO
  // L'EVENT LOOP, e un processo che importa questo modulo non termina mai.
  // Non e' un'ipotesi: e' come si e' fatto scoprire, appendendo a tempo
  // indeterminato un test che esegue `components/timeline.js`, che da
  // `api-client.js` arriva fin qui.
  //
  // Il canale resta pienamente funzionante: `unref` dice solo "non sei tu a
  // dover tenere in piedi il processo".
  if (typeof canale.unref === 'function') canale.unref();
  canale.onmessage = () => {
    // Nessun `annuncia()` qui: sarebbe un anello fra schede che si rimbalzano
    // lo stesso messaggio all'infinito. Chi riceve rilegge e basta.
    restore().catch(() => {
      // Un errore qui e' gia' stato tradotto in `session = null` da `restore`,
      // e i listener hanno gia' svuotato la superficie. Non c'e' nessuno a cui
      // mostrare un messaggio: questa non e' un'azione dell'utente.
    });
  };
} catch (_senzaCanale) {
  canale = null;
}

function annuncia() {
  if (canale === null) return;
  try {
    canale.postMessage({ tipo: 'acting-changed' });
  } catch (_ignorato) {
    // Un canale chiuso non deve far fallire l'operazione che lo ha usato:
    // l'ingresso o l'uscita sono gia' avvenuti sul server.
  }
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
 * P28 — entra nel CRM di un'agenzia come Superadmin.
 *
 * Non aggiorna lo stato locale e non prova a indovinare cosa sia cambiato:
 * chiama la route e poi RILEGGE /me. L'agenzia effettiva la decide il server
 * leggendo la riga di sessione, e questo file non deve poterla scrivere - se
 * potesse, esisterebbe un secondo posto che decide in quale agenzia si sta,
 * ed e' esattamente cio' che P26-1 ha eliminato.
 *
 * `restore()` fa scattare `notify()`, quindi l'epoch avanza e le view gia'
 * disegnate vengono buttate: i contatti dell'agenzia precedente non devono
 * restare nel documento mentre la barra dice un altro nome.
 */
export async function enterAgency(agencyId) {
  const response = await call(`/api/platform/agencies/${agencyId}/enter`, {
    method: 'POST',
  });
  if (!response.ok) {
    throw new Error(await describeActingError(response));
  }
  // Prima si rilegge, poi si avvisa: cosi' questa scheda e' gia' allineata
  // quando le altre cominciano a rileggere, e nessuna mostra un momento in cui
  // le due meta' dello schermo si contraddicono.
  const aggiornata = await restore();
  annuncia();
  return aggiornata;
}

/**
 * P28 — torna alla Platform.
 *
 * Rilegge /me anche quando la chiamata fallisce. E' deliberato: se il server
 * ha gia' tolto il contesto e la risposta si e' persa per strada, lo stato
 * locale deve comunque riallinearsi al server invece di restare a mostrare una
 * barra per un'agenzia da cui si e' gia' usciti.
 */
export async function exitAgency() {
  let errore = null;
  try {
    const response = await call('/api/platform/agency-context/exit', {
      method: 'POST',
    });
    if (!response.ok) errore = await describeActingError(response);
  } catch (networkError) {
    errore = networkError.message;
  }
  await restore();
  // Si avvisa ANCHE quando la chiamata e' fallita: se il server ha tolto il
  // contesto e la risposta si e' persa per strada, le altre schede devono
  // riallinearsi comunque. Il messaggio non afferma niente - dice solo
  // "richiedilo" - quindi avvisare di troppo non puo' mentire a nessuno.
  annuncia();
  if (errore) throw new Error(errore);
  return getSession();
}

/**
 * Il messaggio da mostrare, scelto per stato HTTP.
 *
 * Il `detail` del server non viene mai interpolato: e' scritto per chi legge i
 * log, puo' contenere nomi di vincoli, e non e' una frase che un operatore
 * debba trovarsi davanti. Stessa regola di `components/network.js`.
 */
async function describeActingError(response) {
  if (response.status === 401) return 'Sessione scaduta. Rifai l’accesso.';
  if (response.status === 403) return 'Operazione riservata all’amministrazione di piattaforma.';
  if (response.status === 404) return 'Agenzia non trovata.';
  if (response.status === 409) {
    return 'Operazione non possibile: esci dall’agenzia in cui sei, oppure l’agenzia non è attiva.';
  }
  if (response.status === 503) return 'Audit di piattaforma non disponibile: operazione non eseguita.';
  return 'Operazione non riuscita.';
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
