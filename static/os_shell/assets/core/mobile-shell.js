// STIMA360 OS — core/mobile-shell.js
//
// LA SHELL SU SMARTPHONE (restyling della Scrivania Viva). Solo presentazione
// e navigazione: nessuna rotta nuova, nessuna richiesta, nessuna memoria del
// browser. Su schermi stretti (lo stesso punto di rottura di app.css, 860px):
//
//   * un'intestazione compatta: logo, ambiente, ricerca a icona, profilo;
//   * una barra inferiore con Scrivania, Agenda, Zona, Contatti e «Altro»;
//   * «Altro» elenca TUTTE le altre voci che la barra laterale porta in quel
//     momento (quindi con gli stessi permessi: Rete solo al Superadmin, le
//     voci di tenant solo a chi ha un'agenzia), piu' «Attività».
//
// La barra laterale (`#nav`) resta la fonte di verita' e non viene toccata:
// la barra inferiore si ricostruisce leggendola a ogni cambio di sessione.
// La ricerca globale e' il componente di sempre: qui si apre e si chiude.
// Il profilo e' il componente di sempre: su smartphone il suo contenitore si
// sposta nell'intestazione compatta, su desktop torna nella barra del titolo.
//
// Tutto e' guardato per funzionare anche senza `matchMedia` (i test della
// Shell girano su un DOM simulato): senza, non si sposta nulla e la barra
// inferiore esiste ma la decide solo il CSS.

import { mountEnvBadge } from './env-badge.js';

export const PRIMARIE = Object.freeze(['oggi', 'agenda', 'zona', 'contatti']);
export const ETICHETTE_PRIMARIE = Object.freeze({ oggi: 'Scrivania', agenda: 'Agenda', zona: 'Zona', contatti: 'Contatti' });
const MOBILE_QUERY = '(max-width: 860px)';
const MASSIMO_SENZA_ALTRO = 5;

function el(tag, className, text) {
  const nodo = document.createElement(tag);
  if (className) nodo.className = className;
  if (text !== undefined && text !== null && text !== '') nodo.textContent = String(text);
  return nodo;
}

function icona(paths, className) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('aria-hidden', 'true');
  if (className) svg.classList.add(className);
  for (const d of paths || []) {
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', d);
    svg.appendChild(path);
  }
  return svg;
}

const ICONE = {
  cerca: ['M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14', 'M20 20l-3.5-3.5'],
  altro: ['M4 4h6v6H4z', 'M14 4h6v6h-6z', 'M4 14h6v6H4z', 'M14 14h6v6h-6z'],
  chiudi: ['M6 6l12 12', 'M18 6 6 18'],
};

/**
 * Monta l'intestazione compatta, la barra inferiore e il foglio «Altro».
 * `navEl` e' `#nav`; `sections` l'elenco SECTIONS (per i bersagli come Zona);
 * `icons` le icone della barra laterale; `searchWrap` il riquadro della
 * ricerca globale; `userMenuHost` il contenitore del profilo (`#user-menu`).
 */
export function mountMobileShell({ appView, navEl, sections = [], icons = {}, navigate, searchWrap = null,
  userMenuHost = null, extraEntries = [] } = {}) {
  if (!appView || !navEl || typeof navigate !== 'function') return null;
  const body = document.body;

  // -- intestazione compatta ----------------------------------------------------
  // Porta anche la classe `topbar`: l'Agenda misura le barre fisse della Shell
  // cercando `.acting-bar`, `.sidebar` e `.topbar`, e su smartphone questa e' la
  // barra in alto che conta.
  const testata = el('header', 'mobile-head topbar');
  testata.setAttribute('data-mobile-head', '');
  const marchio = el('a', 'mobile-brand');
  marchio.setAttribute('href', '#/oggi');
  marchio.setAttribute('aria-label', 'STIMA360, Scrivania');
  const logo = el('img', 'mobile-brand-logo');
  logo.setAttribute('src', '/os/assets/brand/stima360-logo.png');
  logo.setAttribute('alt', 'STIMA360');
  logo.setAttribute('width', '1050');
  logo.setAttribute('height', '600');
  marchio.appendChild(logo);
  const ambiente = el('span', 'env-badge mobile-env');
  mountEnvBadge(ambiente);
  const azioni = el('div', 'mobile-head-actions');
  const cerca = el('button', 'mobile-icon-btn mobile-search-btn');
  cerca.type = 'button';
  cerca.setAttribute('aria-label', 'Cerca ovunque');
  cerca.setAttribute('aria-expanded', 'false');
  cerca.setAttribute('data-mobile-search', '');
  cerca.appendChild(icona(ICONE.cerca, 'mobile-icon'));
  const profilo = el('div', 'mobile-profile');
  profilo.setAttribute('data-mobile-profile', '');
  azioni.append(cerca, profilo);
  testata.append(marchio, ambiente, azioni);

  // -- barra inferiore --------------------------------------------------------------
  const barra = el('nav', 'mobile-nav');
  barra.setAttribute('aria-label', 'Navigazione principale');
  barra.setAttribute('data-mobile-nav', '');

  // -- «Altro» ----------------------------------------------------------------------
  const altro = el('div', 'mobile-more');
  altro.setAttribute('data-mobile-more', '');
  altro.hidden = true;
  const velo = el('div', 'mobile-more-veil');
  const foglio = el('div', 'mobile-more-sheet');
  foglio.setAttribute('role', 'dialog');
  foglio.setAttribute('aria-modal', 'true');
  foglio.setAttribute('aria-label', 'Tutte le sezioni');
  const testaFoglio = el('div', 'mobile-more-head');
  testaFoglio.appendChild(el('strong', '', 'Tutte le sezioni'));
  const chiudi = el('button', 'mobile-icon-btn mobile-more-close');
  chiudi.type = 'button';
  chiudi.setAttribute('aria-label', 'Chiudi');
  chiudi.appendChild(icona(ICONE.chiudi, 'mobile-icon'));
  testaFoglio.appendChild(chiudi);
  const elenco = el('div', 'mobile-more-list');
  foglio.append(testaFoglio, elenco);
  altro.append(velo, foglio);

  appView.prepend(testata);
  appView.append(barra, altro);

  let attiva = null;
  let aperto = false;

  function apriAltro(stato) {
    if (stato !== aperto) {
      if (stato) document.addEventListener('keydown', escape);
      else document.removeEventListener('keydown', escape);
    }
    aperto = stato;
    altro.hidden = !stato;
    const bottone = barra.querySelector('[data-mobile-route="altro"]');
    if (bottone) bottone.setAttribute('aria-expanded', String(stato));
    if (body && body.classList) body.classList.toggle('os-more-open', stato);
  }
  velo.addEventListener('click', () => apriAltro(false));
  chiudi.addEventListener('click', () => apriAltro(false));
  // Esc chiude il foglio: il listener esiste solo mentre il foglio e' aperto, cosi' a
  // riposo la Shell non aggiunge ascoltatori di tastiera (le prove dell'Agenda li contano).
  const escape = (e) => { if (e.key === 'Escape' && aperto) apriAltro(false); };

  function vai(route) {
    apriAltro(false);
    apriRicerca(false);
    const sezione = sections.find((s) => s.name === route);
    if (sezione && sezione.target) navigate(sezione.target[0], sezione.target.slice(1));
    else navigate(route);
  }

  function voce(route, label, className) {
    const b = el('button', className);
    b.type = 'button';
    b.dataset.mobileRoute = route;
    b.appendChild(icona(icons[route] || icons.oggi, 'mobile-icon'));
    b.appendChild(el('span', '', label));
    b.addEventListener('click', () => vai(route));
    return b;
  }

  /** Le voci della barra laterale in questo momento: [{ route, label }]. */
  function vociLaterali() {
    const out = [];
    for (const b of navEl.querySelectorAll('[data-route]')) {
      const route = b.dataset.route;
      const span = typeof b.querySelector === 'function' ? b.querySelector('span') : null;
      const label = (span && span.textContent) || b.textContent || route;
      if (route) out.push({ route, label: String(label).trim() });
    }
    return out;
  }

  /** Ricostruisce barra e foglio dalla barra laterale (stessi permessi). */
  function refresh() {
    const laterali = vociLaterali();
    const presenti = new Set(laterali.map((v) => v.route));
    const primarie = PRIMARIE.filter((r) => presenti.has(r));
    const altre = laterali.filter((v) => !PRIMARIE.includes(v.route));
    const extra = extraEntries.filter((v) => presenti.has(v.after));
    const vociAltro = [...extra.map((v) => ({ route: v.route, label: v.label })), ...altre];
    barra.replaceChildren();
    elenco.replaceChildren();
    if (!primarie.length && vociAltro.length <= MASSIMO_SENZA_ALTRO) {
      for (const v of vociAltro) barra.appendChild(voce(v.route, v.label, 'mobile-nav-item'));
    } else {
      for (const r of primarie) barra.appendChild(voce(r, ETICHETTE_PRIMARIE[r] || r, 'mobile-nav-item'));
      if (vociAltro.length) {
        const b = el('button', 'mobile-nav-item');
        b.type = 'button';
        b.dataset.mobileRoute = 'altro';
        b.setAttribute('aria-haspopup', 'dialog');
        b.setAttribute('aria-expanded', 'false');
        b.appendChild(icona(ICONE.altro, 'mobile-icon'));
        b.appendChild(el('span', '', 'Altro'));
        b.addEventListener('click', () => apriAltro(!aperto));
        barra.appendChild(b);
        for (const v of vociAltro) elenco.appendChild(voce(v.route, v.label, 'mobile-more-item'));
      }
    }
    setActive(attiva);
  }

  /** Evidenzia la voce della rotta corrente (o «Altro» se la rotta e' li'). */
  function setActive(nome) {
    attiva = nome;
    let trovata = false;
    for (const b of barra.querySelectorAll('[data-mobile-route]')) {
      const sua = b.dataset.mobileRoute === nome;
      b.classList.toggle('active', sua);
      if (sua) trovata = true;
    }
    const inAltro = !!nome && [...elenco.querySelectorAll('[data-mobile-route]')].some((b) => b.dataset.mobileRoute === nome);
    const bottoneAltro = barra.querySelector('[data-mobile-route="altro"]');
    if (bottoneAltro) bottoneAltro.classList.toggle('active', !trovata && inAltro);
    for (const b of elenco.querySelectorAll('[data-mobile-route]')) {
      b.classList.toggle('active', b.dataset.mobileRoute === nome);
    }
  }

  // -- ricerca a icona ------------------------------------------------------------------
  let ricercaAperta = false;
  function apriRicerca(stato) {
    ricercaAperta = stato;
    cerca.setAttribute('aria-expanded', String(stato));
    if (body && body.classList) body.classList.toggle('os-search-open', stato);
    if (stato && searchWrap && typeof searchWrap.querySelector === 'function') {
      const input = searchWrap.querySelector('input');
      if (input && typeof input.focus === 'function') input.focus();
    }
  }
  cerca.addEventListener('click', () => apriRicerca(!ricercaAperta));

  // -- il profilo: nell'intestazione su smartphone, nella barra del titolo su desktop ----
  const casaProfilo = userMenuHost ? userMenuHost.parentNode : null;
  const mq = typeof window !== 'undefined' && typeof window.matchMedia === 'function' ? window.matchMedia(MOBILE_QUERY) : null;
  function colloca() {
    if (!mq || !userMenuHost || !casaProfilo) return;
    if (mq.matches) {
      if (userMenuHost.parentNode !== profilo) profilo.appendChild(userMenuHost);
    } else if (userMenuHost.parentNode !== casaProfilo) {
      casaProfilo.appendChild(userMenuHost);
    }
    if (!mq.matches) { apriAltro(false); apriRicerca(false); }
  }
  // Si ascolta `resize` della finestra, non `change` della media query: le prove
  // responsive dell'Agenda contano gli ascoltatori sulla soglia, che restano i suoi.
  if (mq && typeof window.addEventListener === 'function') window.addEventListener('resize', colloca);
  colloca();

  // -- tastiera: con un campo di testo attivo la barra inferiore si ritira -------------
  const campo = (t) => !!t && /^(INPUT|TEXTAREA|SELECT)$/.test(String(t.tagName || '').toUpperCase())
    && !['checkbox', 'radio', 'button', 'submit', 'range'].includes(String(t.type || '').toLowerCase());
  document.addEventListener('focusin', (e) => { if (campo(e.target) && body && body.classList) body.classList.add('os-keyboard'); });
  document.addEventListener('focusout', () => { if (body && body.classList) body.classList.remove('os-keyboard'); });

  refresh();
  return { refresh, setActive, closeAll: () => { apriAltro(false); apriRicerca(false); }, elements: { testata, barra, altro } };
}
