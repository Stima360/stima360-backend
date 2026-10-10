// STIMA360 OS — components/user-menu.js
//
// Il profilo dell'operatore in alto a destra: avatar, nome, ruolo, agenzia e
// un menu con le informazioni dell'account e il Logout. Solo presentazione.
//
// Dati, tutti reali e gia' esistenti:
//   * ruolo, agenzia, scadenza della sessione: la sessione di `core/auth.js`
//     (GET /api/operator-auth/me), nessuna richiesta in piu';
//   * nome e cognome: `getAgents()`, l'elenco dei membri attivi dell'agenzia
//     (riga con `is_me`). Se l'operatore non e' membro dell'agenzia in cui
//     opera (Superadmin) il nome non c'e' e non si inventa.
// Il backend non espone una foto profilo: l'avatar mostra le iniziali, o
// un'icona neutra quando il nome non e' disponibile.
//
// Il Logout resta il bottone `#logout-btn` di sempre, con il suo gestore in
// main.js: qui viene solo spostato dentro il menu.
//
// Se il documento non porta `#user-menu` il componente non fa nulla.

import { getAgents } from '../agenda/agenda-api.js';
import { canUseTenantSurface, onAuthChange, sessionEpoch } from '../core/auth.js';

const RUOLI = { agency_owner: 'Owner', agency_admin: 'Admin', agent: 'Agente' };

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function ruolo(session) {
  if (session.is_platform_admin === true) return 'Superadmin';
  return RUOLI[session.role] || 'Operatore';
}

function agenzia(session) {
  if (session.acting) return session.acting.agency_name || `Agenzia ${session.acting.agency_id}`;
  return session.agency_name || 'Piattaforma';
}

function iniziali(nome) {
  const parti = String(nome || '').trim().split(/\s+/).filter(Boolean);
  if (!parti.length) return '';
  const prime = parti.length > 1 ? [parti[0], parti[parti.length - 1]] : [parti[0]];
  return prime.map((p) => p[0].toUpperCase()).join('');
}

function scadenza(valore) {
  if (!valore) return null;
  const d = new Date(valore);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleString('it-IT', {
    day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit',
    timeZone: 'Europe/Rome',
  });
}

function iconaPersona() {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('aria-hidden', 'true');
  svg.classList.add('user-avatar-icon');
  for (const d of ['M20 21v-1a6 6 0 0 0-6-6h-4a6 6 0 0 0-6 6v1', 'M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8']) {
    const path = document.createElementNS(ns, 'path');
    path.setAttribute('d', d);
    svg.appendChild(path);
  }
  return svg;
}

function riga(etichetta, valore) {
  const r = el('div', 'user-menu-row');
  r.append(el('dt', null, etichetta), el('dd', null, valore));
  return r;
}

export function mountUserMenu() {
  const host = document.getElementById('user-menu');
  if (!host) return;
  const logoutBtn = document.getElementById('logout-btn');

  const trigger = el('button', 'user-trigger');
  trigger.type = 'button';
  trigger.setAttribute('aria-haspopup', 'true');
  trigger.setAttribute('aria-expanded', 'false');
  trigger.setAttribute('aria-controls', 'user-menu-panel');
  const avatar = el('span', 'user-avatar');
  const testi = el('span', 'user-text');
  const nomeEl = el('span', 'user-name');
  const metaEl = el('span', 'user-meta');
  testi.append(nomeEl, metaEl);
  const freccia = el('span', 'user-caret');
  freccia.setAttribute('aria-hidden', 'true');
  trigger.append(avatar, testi, freccia);

  const panel = el('div', 'user-menu-panel');
  panel.id = 'user-menu-panel';
  panel.hidden = true;
  const testa = el('div', 'user-menu-head');
  const avatarGrande = el('span', 'user-avatar user-avatar-lg');
  const testaTesti = el('div', 'user-menu-head-text');
  const nomeGrande = el('strong', 'user-menu-name');
  const ruoloGrande = el('span', 'user-menu-role');
  testaTesti.append(nomeGrande, ruoloGrande);
  testa.append(avatarGrande, testaTesti);
  const info = el('dl', 'user-menu-info');
  const azioni = el('div', 'user-menu-actions');
  panel.append(testa, info, azioni);
  if (logoutBtn) azioni.appendChild(logoutBtn);

  host.replaceChildren(trigger, panel);

  function apri(aperto) {
    panel.hidden = !aperto;
    trigger.setAttribute('aria-expanded', String(aperto));
    host.classList.toggle('open', aperto);
  }
  trigger.addEventListener('click', (e) => {
    e.stopPropagation();
    apri(panel.hidden);
  });
  document.addEventListener('click', (e) => {
    if (!panel.hidden && !host.contains(e.target)) apri(false);
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !panel.hidden) {
      apri(false);
      trigger.focus();
    }
  });
  if (logoutBtn) logoutBtn.addEventListener('click', () => apri(false));

  function disegna(session, nome) {
    const r = ruolo(session);
    const a = agenzia(session);
    nomeEl.textContent = nome || r;
    metaEl.textContent = nome ? `${r} · ${a}` : a;
    nomeGrande.textContent = nome || 'Nome non disponibile';
    ruoloGrande.textContent = r;
    for (const target of [avatar, avatarGrande]) {
      const sigla = iniziali(nome);
      target.replaceChildren(sigla ? document.createTextNode(sigla) : iconaPersona());
    }
    trigger.setAttribute('aria-label', `Account: ${nome || r}, ${r}, ${a}`);
    const righe = [riga('Ruolo', r), riga('Agenzia in uso', a)];
    if (session.acting && session.home_agency_name) {
      righe.push(riga('Agenzia di appartenenza', session.home_agency_name));
    }
    const fine = scadenza(session.expires_at);
    if (fine) righe.push(riga('Sessione valida fino al', fine));
    info.replaceChildren(...righe);
  }

  onAuthChange(async (session) => {
    apri(false);
    if (!session) {
      host.hidden = true;
      return;
    }
    host.hidden = false;
    disegna(session, null);
    if (!canUseTenantSurface(session)) return;
    const epoca = sessionEpoch();
    try {
      const dati = await getAgents();
      if (epoca !== sessionEpoch()) return;
      const io = (dati && Array.isArray(dati.items) ? dati.items : []).find((a) => a.is_me);
      if (io && io.name) disegna(session, io.name);
    } catch (_errore) {
      // Nessun nome: restano ruolo e agenzia, gia' disegnati.
    }
  });
}
