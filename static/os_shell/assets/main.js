import { mountGlobalSearch } from './components/global-search.js';
// STIMA360 OS — main.js
// Bootstrap minimo dell'App Shell: collega login, sidebar, router e badge
// ambiente. Nessuna libreria, nessuna dipendenza esterna.

import { login, logout, onAuthChange, restore, sessionEpoch } from './core/auth.js';
import { registerRoute, initRouter, navigate, renderCurrentRoute, clearRoute } from './core/router.js';
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
import { renderRete } from './views/rete.js';
import { renderReteAgenzia } from './views/rete-agenzia.js';
import { renderReteTerritorio } from './views/rete-territorio.js';

const SECTIONS = [
  { name: 'oggi', label: 'Oggi' },
  { name: 'contatti', label: 'Contatti' },
  { name: 'immobili', label: 'Immobili' },
  { name: 'acquirenti', label: 'Acquirenti' },
  { name: 'abbinamenti', label: 'Abbinamenti' },
  { name: 'attivita', label: 'Attività' },
  { name: 'automazioni', label: 'Automazioni' },
];

// P27-7 - la sezione RETE, che NON sta in SECTIONS.
//
// SECTIONS e' la sidebar di ogni operatore: quelle voci le vede chiunque abbia
// una sessione. "Rete" amministra la piattaforma - agenzie, operatori,
// territori - e deve comparire solo a chi e' `is_platform_admin`, quindi il
// suo bottone viene aggiunto e RIMOSSO al cambio di sessione (vedi
// `aggiornaVoceRete` in fondo) invece di essere disegnato una volta all'avvio.
//
// La rotta, invece, e' registrata sempre: nasconderla non e' una difesa -
// `#/rete` si scrive a mano - e la difesa vera e' altrove, in due posti veri.
// La view chiede `GET /api/platform/me` prima di ogni altra cosa e si ferma
// sul 403; e ogni route di `/api/platform` e' protetta da
// `require_platform_admin` (P27-1), che e' l'unica autorita' in materia. Un
// tenant normale che arrivi qui non vede dati: vede un avviso.
const SEZIONE_RETE = { name: 'rete', label: 'Rete' };

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

// "rete" copre l'elenco (#/rete), la scheda agenzia (#/rete/agenzie/{id}) e la
// scheda territorio (#/rete/territori/{id}): stesso pattern dispatcher gia'
// usato dalle altre sezioni con lista+dettaglio.
registerRoute('rete', (container, params = []) => {
  if (params[0] === 'agenzie' && params[1]) return renderReteAgenzia(container, params[1]);
  if (params[0] === 'territori' && params[1]) return renderReteTerritorio(container, params[1]);
  return renderRete(container);
});

initRouter(contentEl, {
  // P26-4: il router butta un risultato che arriva dopo un cambio di sessione.
  // Vedi il commento su `sessionEpoch` in core/auth.js.
  epoch: sessionEpoch,
  onNavigate(name) {
    const active = [...SECTIONS, SEZIONE_RETE].find((s) => s.name === name);
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

// P27-7. Il bottone "Rete" esiste nel DOM solo mentre la sessione corrente e'
// di un amministratore di piattaforma. Non `hidden`: RIMOSSO - e' la stessa
// lezione di P26-4, dove nascondere invece di togliere lasciava la superficie
// della sessione precedente a un `hidden = false` di distanza.
function aggiornaVoceRete(session) {
  const esistente = navEl.querySelector('[data-route="rete"]');
  if (!session || session.is_platform_admin !== true) {
    if (esistente) esistente.remove();
    return;
  }
  if (esistente) return;
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'nav-item';
  btn.dataset.route = SEZIONE_RETE.name;
  btn.textContent = SEZIONE_RETE.label;
  btn.addEventListener('click', () => navigate(SEZIONE_RETE.name));
  navEl.appendChild(btn);
}

loginForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  loginError.textContent = '';
  const email = document.getElementById('login-email').value;
  const password = document.getElementById('login-password').value;
  const submitBtn = loginForm.querySelector('button[type="submit"]');
  submitBtn.disabled = true;
  try {
    await login(email, password);
  } catch (error) {
    loginError.textContent = error.message || 'Errore di accesso.';
  } finally {
    submitBtn.disabled = false;
  }
});

// P26-3: logout vero. La sessione viene revocata sul server e il cookie
// cancellato; azzerare solo lo stato locale lascerebbe un cookie valido.
logoutBtn.addEventListener('click', async () => {
  logoutBtn.disabled = true;
  try {
    await logout();
  } finally {
    logoutBtn.disabled = false;
    window.location.hash = '';
  }
});

// P26-4: quando la sessione finisce, la superficie applicativa va SVUOTATA,
// non nascosta.
//
// Prima qui si commutava `hidden` e nient'altro. I contatti, gli immobili e
// gli abbinamenti dell'agenzia appena uscita restavano nel documento: a un
// `hidden = false` di distanza, o a un devtools aperto. Su una postazione
// condivisa e' gia' sbagliato; quando il prossimo a entrare appartiene a
// un'altra agenzia diventa esattamente cio' che P26 esiste per impedire.
//
// La ricerca globale non compare qui: vive fuori dal container delle view e si
// ripulisce da sola su `onAuthChange`, come faceva gia' prima di P26-4. Una
// seconda chiamata da questo punto sarebbe codice che non fa niente, e i test
// di P26-4 la coprono dove sta.
function clearApplicationSurface() {
  clearRoute();
  pageTitle.textContent = '';
  for (const btn of navEl.querySelectorAll('[data-route]')) {
    btn.classList.remove('active');
  }
}

onAuthChange((session) => {
  const authenticated = session !== null;
  aggiornaVoceRete(session);
  loginView.hidden = authenticated;
  appView.hidden = !authenticated;
  // Il form viene svuotato appena la sessione esiste: la password non deve
  // restare nel DOM piu' del necessario.
  loginForm.reset();
  if (authenticated) {
    loginError.textContent = '';
    renderCurrentRoute();
  } else {
    clearApplicationSurface();
  }
});

// P26-3 - stato iniziale.
//
// Il cookie di sessione e' HttpOnly, quindi questo codice non puo' vederlo: la
// sola cosa che sa dire se c'e' una sessione viva e' il server. Al boot si
// parte percio' dalla schermata di login e si chiede /me; se risponde, la UI
// passa allo stato autenticato senza che l'utente rifaccia il login dopo un
// refresh - cosa che con Basic in memoria era impossibile.
//
// Un 401 qui e' l'esito normale di "non c'e' sessione" e non produce un
// messaggio di errore.
loginView.hidden = false;
appView.hidden = true;
restore().catch((error) => {
  loginError.textContent = error.message || '';
});
