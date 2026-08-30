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

export function navigate(name) {
  const target = `#/${name}`;
  if (window.location.hash === target) {
    renderCurrentRoute();
  } else {
    window.location.hash = target;
  }
}

export function currentRouteName() {
  const raw = window.location.hash.replace(/^#\/?/, '');
  const name = raw.split('/')[0] || 'oggi';
  return routes.has(name) ? name : 'oggi';
}

export async function renderCurrentRoute() {
  if (!container) return;
  const name = currentRouteName();
  const renderFn = routes.get(name);
  if (onNavigateCallback) onNavigateCallback(name);
  container.innerHTML = '';
  if (!renderFn) {
    container.textContent = 'Sezione non trovata.';
    return;
  }
  try {
    await renderFn(container);
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
