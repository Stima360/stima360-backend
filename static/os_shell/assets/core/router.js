// STIMA360 OS — router.js
// Hash routing minimale, senza libreria. Nessun reload di pagina tra le sezioni.

const routes = new Map();
let container = null;
let onNavigateCallback = null;
// P26-4: da chi dipende il "questa risposta appartiene ancora a qualcuno".
// Iniettato invece che importato, cosi' il router non conosce l'autenticazione
// e resta provabile da solo.
let epochFn = null;

export function registerRoute(name, renderFn) {
  routes.set(name, renderFn);
}

export function initRouter(contentContainer, { onNavigate, epoch } = {}) {
  container = contentContainer;
  onNavigateCallback = onNavigate || null;
  epochFn = typeof epoch === 'function' ? epoch : null;
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
