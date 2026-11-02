// STIMA360 OS — views/sezioni.js
// SEMPLIFICAZIONE-NAV-1: i CONTENITORI della nuova barra laterale. Nessuna
// logica di dominio: ogni scheda qui dentro e' la vista di prima, montata
// com'e' in un riquadro proprio. Le vecchie rotte (#/attivita, #/abbinamenti,
// #/automazioni, #/cestino, #/agenzia, #/stime-dettagliate) restano registrate
// e valide: qui cambia solo dove si arriva dalla barra laterale.
//
//   Oggi          #/oggi            Oggi | Attività          (views/oggi.js, views/attivita.js)
//   Richieste     #/acquirenti      Richieste | Abbinamenti  (views/acquirenti.js, views/abbinamenti.js)
//   Impostazioni  #/impostazioni    Agenzia | Automazioni | Cestino
//                                   (views/agenzia-dati.js, views/automazioni.js, views/cestino.js)
//
// I permessi restano quelli delle viste e del server: Agenzia e Automazioni
// esistono solo con una superficie di tenant; il Cestino anche per un
// amministratore di piattaforma senza agenzia (vista globale), come prima.

import { navigate } from '../core/router.js';
import { renderOggi } from './oggi.js';
import { renderAttivita } from './attivita.js';
import { renderAcquirenti } from './acquirenti.js';
import { renderAbbinamenti } from './abbinamenti.js';
import { renderAgenziaDati } from './agenzia-dati.js';
import { renderAutomazioni } from './automazioni.js';
import { renderCestino } from './cestino.js';

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

/**
 * Un contenitore a schede: la barra delle schede e, sotto, la vista scelta.
 * `tabs`: [{ key, label, render(container) }]; `route`: la rotta del contenitore
 * (la scheda attiva finisce nell'indirizzo, cosi' si puo' tornarci e condividerla).
 */
async function renderTabbed(container, { route, tabs, active }) {
  const root = el('div', 'sezione');
  root.setAttribute('data-sezione', route);
  const bar = el('div', 'tabs sezione-tabs');
  bar.setAttribute('role', 'tablist');
  const content = el('div', 'sezione-content');
  content.setAttribute('data-sezione-content', '');
  const chosen = tabs.find((t) => t.key === active) || tabs[0];
  for (const t of tabs) {
    const b = el('button', `tab-btn${t === chosen ? ' active' : ''}`, t.label);
    b.type = 'button';
    b.setAttribute('role', 'tab');
    b.setAttribute('data-sezione-tab', t.key);
    b.setAttribute('aria-selected', String(t === chosen));
    // La prima scheda e' l'indirizzo "nudo" del contenitore; le altre lo estendono.
    b.addEventListener('click', () => navigate(route, t === tabs[0] ? [] : [t.key]));
    bar.appendChild(b);
  }
  root.append(bar, content);
  container.replaceChildren(root);
  await chosen.render(content);
}

/** Oggi: la pagina di prima piu' la scheda «Attività» (ex voce di menu). */
export async function renderOggiSezione(container, params = []) {
  return renderTabbed(container, {
    route: 'oggi',
    active: params[0],
    tabs: [
      { key: 'oggi', label: 'Oggi', render: (c) => renderOggi(c) },
      { key: 'attivita', label: 'Attività', render: (c) => renderAttivita(c) },
    ],
  });
}

/** Richieste: le richieste acquirenti con la scheda «Abbinamenti» (ex voce di menu). */
export async function renderRichieste(container, params = []) {
  return renderTabbed(container, {
    route: 'acquirenti',
    active: params[0],
    tabs: [
      { key: 'richieste', label: 'Richieste', render: (c) => renderAcquirenti(c) },
      { key: 'abbinamenti', label: 'Abbinamenti', render: (c) => renderAbbinamenti(c) },
    ],
  });
}

/**
 * Impostazioni: dati agenzia e logo, automazioni e Cestino. La superficie
 * (`tenant`, `platformAdmin`, `platformOnly`, `meId`) la decide main.js dalla
 * sessione del server: qui non si legge nulla della sessione.
 */
export async function renderImpostazioni(container, params = [], { tenant = false, platformAdmin = false, platformOnly = false, meId = null } = {}) {
  const tabs = [];
  if (tenant) {
    tabs.push({ key: 'agenzia', label: 'Dati agenzia e logo', render: (c) => renderAgenziaDati(c) });
    tabs.push({ key: 'automazioni', label: 'Automazioni', render: (c) => renderAutomazioni(c) });
  }
  tabs.push({ key: 'cestino', label: 'Cestino', render: (c) => renderCestino(c, { platformAdmin, platformOnly, meId }) });
  return renderTabbed(container, { route: 'impostazioni', active: params[0], tabs });
}
