// STIMA360 OS — router.js
// Hash routing minimale, senza libreria. Nessun reload di pagina tra le sezioni.

const routes = new Map();
let container = null;
let onNavigateCallback = null;

export function registerRoute(name, renderFn) {
  routes.set(name, renderFn);
}

export function initRouter(contentContainer, { onNavigate } = {}) {
  container = contentContainer;
  onNavigateCallback = onNavigate || null;
  window.addEventListener('hashchange', renderCurrentRoute);
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
  try {
    await renderFn(container, params);
  } catch (error) {
    const message = (error && error.message) ? error.message : 'errore sconosciuto';
    container.innerHTML = `<div class="error-box">Errore nel caricamento della sezione: ${escapeHtml(message)}</div>`;
  }
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}
