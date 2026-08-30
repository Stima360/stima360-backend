// STIMA360 OS — env-badge.js
// Stessa identica logica di rilevamento ambiente gia' presente in
// static/core_admin/index.html (badge basato su window.location.hostname),
// riutilizzata qui senza modificarne il comportamento.

export function computeEnvLabel(hostname) {
  const host = hostname || '';
  if (host.indexOf('stima360-backend-test') !== -1) {
    return 'AMBIENTE TEST';
  }
  if (host.indexOf('stima360-backend.onrender.com') !== -1) {
    return 'AMBIENTE PROD';
  }
  if (host === 'localhost' || host === '127.0.0.1') {
    return 'AMBIENTE LOCALE';
  }
  return 'AMBIENTE LOCALE';
}

export function mountEnvBadge(element) {
  if (!element) return;
  element.textContent = computeEnvLabel(window.location.hostname);
}
