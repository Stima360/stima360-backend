import { mountGlobalSearch } from './components/global-search.js';
// STIMA360 OS — main.js
// Bootstrap minimo dell'App Shell: collega login, sidebar, router e badge
// ambiente. Nessuna libreria, nessuna dipendenza esterna.

import { login, logout, isAuthenticated, onAuthChange } from './core/auth.js';
import { registerRoute, initRouter, navigate, renderCurrentRoute } from './core/router.js';
import { mountEnvBadge } from './core/env-badge.js';
import { renderOggi } from './views/oggi.js';
import { renderContatti } from './views/contatti.js';
import { renderContattoDettaglio } from './views/contatto-dettaglio.js';
import { renderImmobili } from './views/immobili.js';
import { renderImmobileDettaglio } from './views/immobile-dettaglio.js';
import { renderAcquirenti } from './views/acquirenti.js';
import { renderAcquirenteDettaglio } from './views/acquirente-dettaglio.js';
import { renderAbbinamenti } from './views/abbinamenti.js';
import { renderAbbinamentoDettaglio } from './views/abbinamento-dettaglio.js';
import { renderAttivita } from './views/attivita.js';
import { renderAutomazioni } from './views/automazioni.js';
import { renderAutomazioneDettaglio } from './views/automazione-dettaglio.js';

const SECTIONS = [
  { name: 'oggi', label: 'Oggi' },
  { name: 'contatti', label: 'Contatti' },
  { name: 'immobili', label: 'Immobili' },
  { name: 'acquirenti', label: 'Acquirenti' },
  { name: 'abbinamenti', label: 'Abbinamenti' },
  { name: 'attivita', label: 'Attività' },
  { name: 'automazioni', label: 'Automazioni' },
];

const loginView = document.getElementById('login-view');
const appView = document.getElementById('app-view');
const loginForm = document.getElementById('login-form');
const loginError = document.getElementById('login-error');
const logoutBtn = document.getElementById('logout-btn');
const pageTitle = document.getElementById('page-title');
const contentEl = document.getElementById('content');
const navEl = document.getElementById('nav');
const envBadgeEl = document.getElementById('env-badge');

registerRoute('oggi', renderOggi);
// "contatti" copre sia la lista (#/contatti) sia il dettaglio (#/contatti/{id}):
// il router passa i segmenti successivi al nome sezione come `params`.
registerRoute('contatti', (container, params = []) => {
  return params[0] ? renderContattoDettaglio(container, params) : renderContatti(container);
});
// "immobili" copre sia la lista (#/immobili) sia il dettaglio (#/immobili/{id}),
// stesso pattern dispatcher gia' usato per "contatti".
registerRoute('immobili', (container, params = []) => {
  return params[0] ? renderImmobileDettaglio(container, params) : renderImmobili(container);
});
// "acquirenti" copre sia la lista richieste BUY (#/acquirenti) sia la scheda
// (#/acquirenti/{buy_request_id}), stesso pattern dispatcher gia' usato per
// "contatti" e "immobili".
registerRoute('acquirenti', (container, params = []) => {
  return params[0] ? renderAcquirenteDettaglio(container, params) : renderAcquirenti(container);
});
// "abbinamenti" copre sia la graduatoria (#/abbinamenti) sia la scheda
// Match (#/abbinamenti/{match_id}), stesso pattern dispatcher gia' usato
// per "contatti", "immobili" e "acquirenti".
registerRoute('abbinamenti', (container, params = []) => {
  return params[0] ? renderAbbinamentoDettaglio(container, params) : renderAbbinamenti(container);
});
// "attivita" e' sola lista (nessun dettaglio: vedi commento in testa a
// attivita.js sul perche' non esiste attivita-dettaglio.js).
registerRoute('attivita', (container) => renderAttivita(container));
// "automazioni" copre sia l'elenco (#/automazioni) sia la scheda regola
// (#/automazioni/{code}), stesso pattern dispatcher gia' usato per le altre
// sezioni con lista+dettaglio.
registerRoute('automazioni', (container, params = []) => {
  return params[0] ? renderAutomazioneDettaglio(container, params) : renderAutomazioni(container);
});
// "impostazioni" (P25 pre-push compliance patch): la specifica P25.7
// approvata richiedeva di NON implementare Impostazioni e di
// rimuoverla/nasconderla dalla sidebar - una precedente implementazione
// (vista reale + route dedicata) deviava da questo requisito ed e' stata
// rimossa qui. "impostazioni" non compare piu' in SECTIONS (vedi sopra),
// quindi non ha ne' un bottone in sidebar ne' un registerRoute: e'
// interamente assente dall'app, non solo nascosta via CSS.

initRouter(contentEl, {
  onNavigate(name) {
    const active = SECTIONS.find((s) => s.name === name);
    pageTitle.textContent = active ? active.label : 'Pagina non trovata';
    for (const btn of navEl.querySelectorAll('[data-route]')) {
      btn.classList.toggle('active', btn.dataset.route === name);
    }
  },
});

mountEnvBadge(envBadgeEl);
mountGlobalSearch(contentEl.parentElement);

for (const section of SECTIONS) {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'nav-item';
  btn.dataset.route = section.name;
  btn.textContent = section.label;
  btn.addEventListener('click', () => navigate(section.name));
  navEl.appendChild(btn);
}

loginForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  loginError.textContent = '';
  const username = document.getElementById('login-username').value;
  const password = document.getElementById('login-password').value;
  const submitBtn = loginForm.querySelector('button[type="submit"]');
  submitBtn.disabled = true;
  try {
    await login(username, password);
  } catch (error) {
    loginError.textContent = error.message || 'Errore di accesso.';
  } finally {
    submitBtn.disabled = false;
  }
});

logoutBtn.addEventListener('click', () => {
  logout();
  window.location.hash = '';
});

onAuthChange((credentials) => {
  const authenticated = credentials !== null;
  loginView.hidden = authenticated;
  appView.hidden = !authenticated;
  if (authenticated) {
    loginForm.reset();
    loginError.textContent = '';
    renderCurrentRoute();
  }
});

// Stato iniziale: le credenziali non sono mai persistite, quindi ad ogni
// caricamento della pagina (incluso un refresh) si riparte da non autenticati
// e viene mostrato il login.
const authenticatedAtBoot = isAuthenticated();
loginView.hidden = authenticatedAtBoot;
appView.hidden = !authenticatedAtBoot;
