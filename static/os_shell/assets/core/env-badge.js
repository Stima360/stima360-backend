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
  const label = computeEnvLabel(window.location.hostname);
  element.textContent = label;
  // Aurora Glass: solo un aggancio per il colore del badge, il testo resta
  // l'unica fonte di verita'.
  element.dataset.env = label === 'AMBIENTE TEST' ? 'test'
    : label === 'AMBIENTE PROD' ? 'prod' : 'local';
}
