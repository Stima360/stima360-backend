// STIMA360 OS — impostazioni.js
// Vista "Impostazioni" minimale (P25.7): sostituisce il placeholder
// "Sezione in preparazione" con contenuto reale ma volutamente ridotto —
// informazioni di sessione già disponibili nel frontend (utente
// autenticato via core/auth.js, ambiente via core/env-badge.js), nessun
// nuovo endpoint backend, nessuna nuova funzionalità di configurazione.
// Coerente con "ADD, DON'T REPLACE" / no scope creep: niente dashboard,
// niente redesign, solo la rimozione del placeholder residuo.

import { getCredentials, logout } from '../core/auth.js';
import { computeEnvLabel } from '../core/env-badge.js';

export async function renderImpostazioni(container) {
  const credentials = getCredentials();
  const envLabel = computeEnvLabel(window.location.hostname);

  container.innerHTML = `
    <div class="card panel">
      <h2>Account</h2>
      <p>Utente: <strong>${escapeHtml(credentials?.username || '—')}</strong></p>
      <p>Ambiente: <strong>${escapeHtml(envLabel)}</strong></p>
      <button type="button" class="btn" id="impostazioni-logout-btn">Esci</button>
    </div>
  `;

  const logoutBtn = document.getElementById('impostazioni-logout-btn');
  logoutBtn.addEventListener('click', () => {
    logout();
    window.location.hash = '';
  });
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}
