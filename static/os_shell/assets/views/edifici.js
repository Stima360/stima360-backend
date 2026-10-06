// STIMA360 OS — views/edifici.js (EDIFICI-1)
//
// La sezione «Edifici»: trovare una palazzina e aprirla. Voce propria nella
// barra laterale, subito sopra «Immobili» (#/edifici; la scheda e'
// #/edifici/{id}). L'edificio e' un CONTENITORE operativo (`buildings`), non
// un immobile: lo «Stabile intero» venduto come immobile resta la tipologia
// `building` in Immobili.
//
// Contratto (property/router.py, property/census.py::list_buildings):
//   GET /api/property/buildings?search=&city=&microzone=&sort=address&limit=&offset=
//   -> { items[], total, limit, offset }, ogni edificio con `census_summary`
//      (dichiarate, censite, da completare: regole SOLO nel server).
// Una richiesta per pagina, mai una per edificio. Comuni e microzone dal
// catalogo territoriale di form-options (niente elenchi scritti qui).
//
// Filtri e pagina restano IN MEMORIA per la sessione (stesso meccanismo della
// lista dell'Agenda, `sessionEpoch`): tornando dalla scheda la lista riparte
// da dove era. Nessun localStorage; un'altra sessione riparte da zero.

import { navigate } from '../core/router.js';
import { sessionEpoch } from '../core/auth.js';
import { escapeHtml, renderBadge } from '../components/st-table.js';
import { loadFormOptions } from '../components/property-form.js';
import * as api from '../census/census-api.js';
import { openBuildingSheet } from '../census/census-sheets.js';
import {
  buildingListQuery, buildingPlace, buildingStreet, buildingTitle, catalogMunicipalities, coherentFilters,
  errorMessage, labelOf, summaryView,
} from '../census/census-model.js';

export const PAGE_SIZE = 25;
const VUOTI = { search: '', city: '', microzone: '' };

let statoInMemoria = { epoch: null, filtri: { ...VUOTI }, offset: 0 };

function leggiStato() {
  return statoInMemoria.epoch === sessionEpoch()
    ? { filtri: { ...statoInMemoria.filtri }, offset: statoInMemoria.offset }
    : { filtri: { ...VUOTI }, offset: 0 };
}

function salvaStato(filtri, offset) {
  statoInMemoria = { epoch: sessionEpoch(), filtri: { ...filtri }, offset };
}

function opzioniHtml(valori, scelto, segnaposto) {
  return [`<option value="">${escapeHtml(segnaposto)}</option>`,
    ...valori.map((v) => `<option value="${escapeHtml(v)}"${v === scelto ? ' selected' : ''}>${escapeHtml(v)}</option>`)].join('');
}

/** La card di un edificio nella lista (stringa HTML, dati gia' escapati). */
export function buildingCardHtml(b, buildingTypes) {
  const v = summaryView(b.census_summary);
  const via = buildingStreet(b);
  const titolo = buildingTitle(b);
  const sotto = [b.name && via ? via : '', buildingPlace(b)].filter(Boolean).join(' · ');
  const tipo = labelOf(buildingTypes, b.building_type, '');
  const segnali = [];
  if (v.over) segnali.push(renderBadge('Censite oltre le dichiarate', 'warn'));
  if (v.unknownAccessories) segnali.push(renderBadge(v.unknownAccessories, 'warn'));
  return `
    <a class="building-card" href="#/edifici/${escapeHtml(b.id)}" data-building-id="${escapeHtml(b.id)}">
      <span class="building-card-head"><strong class="building-card-title">${escapeHtml(titolo)}</strong>${tipo ? ` <small class="muted">${escapeHtml(tipo)}</small>` : ''}</span>
      <span class="building-card-sub muted">${escapeHtml(sotto || 'Indirizzo non indicato')}</span>
      <span class="building-counts">
        <span class="building-count"><small>Dichiarate</small><b data-count="declared">${escapeHtml(v.declared)}</b></span>
        <span class="building-count"><small>Censite</small><b data-count="counted">${escapeHtml(v.counted)}</b>${v.countedNote ? `<em>${escapeHtml(v.countedNote)}</em>` : ''}</span>
        <span class="building-count"><small>Da completare</small><b data-count="to-complete">${escapeHtml(v.toComplete)}</b></span>
      </span>
      ${segnali.length ? `<span class="badge-row">${segnali.join(' ')}</span>` : ''}
    </a>`;
}

export async function renderEdifici(container) {
  container.innerHTML = `
    <div class="card panel buildings-panel">
      <p class="muted buildings-intro">Palazzine ed edifici censiti: aprine uno per vedere tutte le sue unità. Ville, case indipendenti e locali autonomi restano in Immobili, senza edificio.</p>
      <div class="list-toolbar buildings-toolbar">
        <input id="buildings-search" class="input" type="search" placeholder="Cerca per via, civico o nome…" autocomplete="off">
        <select id="buildings-city" class="input" aria-label="Comune"></select>
        <select id="buildings-microzone" class="input" aria-label="Microzona"></select>
        <button type="button" id="buildings-reset" class="btn ghost" hidden>Azzera filtri</button>
        <button type="button" id="buildings-new" class="btn primary">+ Nuova palazzina</button>
      </div>
      <div id="buildings-area" aria-live="polite"><p class="muted">Caricamento…</p></div>
      <div id="buildings-pager" class="list-pager"></div>
    </div>
    <dialog id="buildings-sheet" class="modal census-sheet"></dialog>
  `;
  const $ = (sel) => container.querySelector(sel);
  const area = $('#buildings-area');
  const pager = $('#buildings-pager');
  const cerca = $('#buildings-search');
  const comuneSel = $('#buildings-city');
  const zonaSel = $('#buildings-microzone');
  const azzera = $('#buildings-reset');

  // Il catalogo serve per i filtri e per le etichette: se manca la lista
  // funziona lo stesso (ricerca), i menu restano vuoti e lo dicono.
  let opzioni = null;
  try {
    opzioni = await loadFormOptions();
  } catch (_error) {
    opzioni = null;
  }
  const comuni = catalogMunicipalities(opzioni ? opzioni.territory : []);
  const tipi = opzioni ? opzioni.building_types : [];

  let { filtri, offset } = leggiStato();
  filtri = coherentFilters(filtri, comuni);
  let richiesta = 0;
  let attesa = null;

  function disegnaFiltri() {
    comuneSel.innerHTML = opzioniHtml(comuni.map((c) => c.name), filtri.city, comuni.length ? 'Tutti i comuni' : 'Comuni non disponibili');
    const zone = (comuni.find((c) => c.name === filtri.city) || { microzones: [] }).microzones;
    zonaSel.innerHTML = opzioniHtml(zone, filtri.microzone, filtri.city ? 'Tutte le microzone' : 'Scegli prima il comune');
    zonaSel.disabled = !filtri.city;
    azzera.hidden = !(filtri.search || filtri.city || filtri.microzone);
  }

  async function carica() {
    salvaStato(filtri, offset);
    const mia = ++richiesta;
    area.innerHTML = '<p class="muted">Caricamento…</p>';
    pager.innerHTML = '';
    let corpo;
    try {
      corpo = await api.listBuildings(buildingListQuery(filtri, offset, PAGE_SIZE));
    } catch (error) {
      if (mia !== richiesta) return;
      area.innerHTML = `<div class="error-box">Impossibile caricare gli edifici: ${escapeHtml(errorMessage(error))} <button type="button" class="btn ghost btn-small" id="buildings-retry">Riprova</button></div>`;
      area.querySelector('#buildings-retry').addEventListener('click', carica);
      return;
    }
    if (mia !== richiesta) return;                 // una risposta vecchia non copre la nuova
    const items = Array.isArray(corpo && corpo.items) ? corpo.items : [];
    const totale = Number.isInteger(corpo && corpo.total) ? corpo.total : items.length;
    if (!items.length && offset > 0 && totale > 0) {  // la pagina non esiste piu': si torna alla prima
      offset = 0;
      carica();
      return;
    }
    const filtrato = Boolean(filtri.search || filtri.city || filtri.microzone);
    if (!items.length) {
      area.innerHTML = filtrato
        ? '<p class="muted">Nessun edificio per questi filtri. Prova ad azzerarli.</p>'
        : '<p class="muted">Nessun edificio censito. Comincia da «+ Nuova palazzina».</p>';
      return;
    }
    area.innerHTML = `<div class="building-cards">${items.map((b) => buildingCardHtml(b, tipi)).join('')}</div>`;
    const ultimo = offset + items.length;
    pager.innerHTML = `
      <button class="btn" id="buildings-prev" ${offset === 0 ? 'disabled' : ''}>← Precedenti</button>
      <span class="muted" id="buildings-range">${escapeHtml(`${offset + 1}–${ultimo} di ${totale}`)}</span>
      <button class="btn" id="buildings-next" ${ultimo >= totale ? 'disabled' : ''}>Successivi →</button>`;
    pager.querySelector('#buildings-prev').addEventListener('click', () => { offset = Math.max(0, offset - PAGE_SIZE); carica(); });
    pager.querySelector('#buildings-next').addEventListener('click', () => { offset += PAGE_SIZE; carica(); });
  }

  function cambia(nuovi) {
    filtri = coherentFilters({ ...filtri, ...nuovi }, comuni);
    offset = 0;
    disegnaFiltri();
    carica();
  }

  cerca.addEventListener('input', () => {
    clearTimeout(attesa);
    attesa = setTimeout(() => cambia({ search: cerca.value }), 300);
  });
  comuneSel.addEventListener('change', () => cambia({ city: comuneSel.value, microzone: '' }));
  zonaSel.addEventListener('change', () => cambia({ microzone: zonaSel.value }));
  azzera.addEventListener('click', () => { cerca.value = ''; cambia({ ...VUOTI }); });
  $('#buildings-new').addEventListener('click', () => {
    if (!opzioni) {
      area.insertAdjacentHTML('afterbegin', '<div class="error-box">Impossibile caricare i dati del form: riprova tra poco.</div>');
      return;
    }
    openBuildingSheet($('#buildings-sheet'), { options: opzioni, onSaved: (creato) => navigate('edifici', [creato.id]) });
  });

  cerca.value = filtri.search;
  disegnaFiltri();
  await carica();
}
