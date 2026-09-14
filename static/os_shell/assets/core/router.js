// STIMA360 OS — router.js
// Hash routing minimale, senza libreria. Nessun reload di pagina tra le sezioni.

const routes = new Map();
let container = null;
let onNavigateCallback = null;
// P26-4: da chi dipende il "questa risposta appartiene ancora a qualcuno".
// Iniettato invece che importato, cosi' il router non conosce l'autenticazione
// e resta provabile da solo.
let epochFn = null;
// P28: la guardia. Vedi `renderCurrentRoute`.
let guardFn = null;

export function registerRoute(name, renderFn) {
  routes.set(name, renderFn);
}

export function initRouter(contentContainer, { onNavigate, epoch, guard } = {}) {
  container = contentContainer;
  onNavigateCallback = onNavigate || null;
  epochFn = typeof epoch === 'function' ? epoch : null;
  guardFn = typeof guard === 'function' ? guard : null;
  // Ogni cambio di hash ripassa da `renderCurrentRoute`, che e' anche dove
  // vive la guardia: un "indietro" del browser verso una rotta vietata viene
  // fermato esattamente come un accesso diretto.
  window.addEventListener('hashchange', renderCurrentRoute);
}

/** Svuota la superficie applicativa. Chiamata quando la sessione finisce. */
export function clearRoute() {
  if (container) container.innerHTML = '';
}

export function navigate(name, params = []) {
  const segments = [name, ...params].filter((s) => s !== undefined && s !== null && s !== '');
  const target = `#/${segments.join('/')}`;
  if (window.location.hash === target) {
    renderCurrentRoute();
  } else {
    window.location.hash = target;
  }
}

export function currentRouteName() {
  const raw = window.location.hash.replace(/^#\/?/, '');
  const name = raw.split('/')[0] || 'oggi';
  return name;
}

// Estensione minima P1: segmenti dopo il nome sezione (es. "#/contatti/42" -> ["42"]).
// Retrocompatibile: le viste P0 esistenti (renderOggi, i placeholder) non dichiarano
// un secondo parametro e continuano a funzionare invariate.
export function currentRouteParams() {
  const raw = window.location.hash.replace(/^#\/?/, '');
  const segments = raw.split('/').filter(Boolean);
  return segments.slice(1);
}

export async function renderCurrentRoute() {
  if (!container) return;
  const name = currentRouteName();

  // P28 - LA GUARDIA, PRIMA DI QUALUNQUE VISTA E QUINDI DI QUALUNQUE FETCH.
  //
  // E' l'unico punto del prodotto in cui si sceglie cosa disegnare, ed e'
  // percio' l'unico posto in cui questa domanda va posta. L'alternativa - un
  // controllo dentro ogni view - sarebbe venti posti da ricordare, con il
  // ventunesimo che arriva senza.
  //
  // Sta PRIMA di `routes.get`: una vista che non deve essere aperta non viene
  // nemmeno cercata, quindi la sua prima riga - che e' sempre una richiesta -
  // non parte. Il requisito non era "non mostrare l'errore": era "non fare la
  // chiamata".
  //
  // La guardia riceve il NOME della rotta e restituisce dove si deve andare,
  // oppure niente. Non sa nulla di sessioni: quella decisione la prende
  // `main.js` a partire da cio' che `/me` ha detto.
  const destinazione = guardFn ? guardFn(name) : null;
  if (destinazione && destinazione !== name) {
    const target = `#/${destinazione}`;
    if (window.location.hash !== target) {
      // L'URL deve dire la verita' su dove si e': un utente che ricarica, o
      // che condivide il link, non deve ritrovarsi di nuovo dove non puo'
      // stare.
      window.location.hash = target;
    }
    // Si disegna SUBITO, senza aspettare l'evento che l'assegnazione qui
    // sopra provoca. Al boot, o dopo un'uscita, non c'e' nessun evento in
    // arrivo: aspettarlo lascerebbe lo schermo sulla pagina di prima.
    //
    // In un browser quell'evento arriva comunque e la destinazione viene
    // ridisegnata una seconda volta. E' una lettura in piu' su una vista di
    // piattaforma, e si accetta: l'alternativa - ricordare che un
    // reindirizzamento e' in corso - e' uno stato che resta appeso quando
    // l'evento non arriva, e allora a essere ignorata e' una navigazione VERA.
    return renderCurrentRoute();
  }

  const renderFn = routes.get(name);
  const params = currentRouteParams();
  if (onNavigateCallback) onNavigateCallback(name, params);
  container.innerHTML = '';
  if (!renderFn) {
    container.textContent = 'Pagina non trovata. Seleziona una sezione dal menu.';
    return;
  }
  // P26-4. Le view sono asincrone: `container.innerHTML = ...` avviene dopo
  // l'await. Se nel frattempo la sessione e' cambiata, quella risposta
  // appartiene a una sessione che non esiste piu' - e con un login successivo
  // di un'altra agenzia finirebbe sullo schermo di un operatore che non ha
  // alcun diritto di vederla. Il numero viene annotato prima e riletto dopo:
  // se non coincide, il risultato viene buttato invece che dipinto.
  const started = epochFn ? epochFn() : null;
  const stale = () => started !== null && epochFn() !== started;

  try {
    await renderFn(container, params);
    if (stale()) container.innerHTML = '';
  } catch (error) {
    if (stale()) {
      container.innerHTML = '';
      return;
    }
    const message = (error && error.message) ? error.message : 'errore sconosciuto';
    container.innerHTML = `<div class="error-box">Errore nel caricamento della sezione: ${escapeHtml(message)}</div>`;
  }
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}
