// STIMA360 OS — views/territorio.js (STIMA360 TERRITORIO T2 + T3)
//
// La mappa stradale del CRM: `#/edifici/mappa`, dentro la sezione Edifici
// (nessuna voce nuova nella barra laterale; ci si arriva dal pulsante «Mappa»
// dell'elenco Edifici). MapLibre GL JS + OpenFreeMap (stile `positron`),
// centrata su Alba Adriatica, con zoom e spostamento.
//
// T3 - gli edifici dell'agenzia. UNA lettura per apertura: tutte le pagine di
// GET /api/property/buildings (200 per richiesta, finche' ce ne sono), con lo
// stesso scope d'agenzia e gli stessi filtri (Cestino, archiviati) del server.
// Sulla mappa SOLO gli edifici con posizione VERIFICATA (la regola e' una sola,
// in territorio/territorio-model.js: oggi nessun edificio la soddisfa, perche'
// il CRM non registra ancora la verifica - arriva con T4). Gli altri sono
// contati in «Edifici da posizionare: N» e raggiungibili da un elenco
// essenziale, divisi fra senza coordinate, coordinate non verificate e non
// valide. Clic su un marker: il marker diventa «attivo», un riquadro dice nome,
// indirizzo, comune e il riepilogo del censimento (dettaglio da GET
// /api/property/buildings/{id}, una volta per edificio), «Apri scheda edificio»
// porta a `#/edifici/{id}`. Nessun trascinamento, nessuna scrittura, nessun
// geocoding: i marker sono un livello `circle` della mappa, non elementi DOM.
// I dati del CRM entrano nel DOM solo come `textContent`.
//
// Ciclo di vita: al massimo UNA mappa viva. Si distrugge (`map.remove()`, che
// libera WebGL, worker, livelli, gestori e riquadri) quando si apre un'altra
// rotta, quando la sessione cambia (logout / altra agenzia) o quando la vista
// viene ridisegnata; una risposta (libreria o edifici) che arriva dopo la
// navigazione o dopo un cambio di sessione non disegna nulla.
//
// Attribuzione: la mostra il controllo di MapLibre, sempre esteso, con il testo
// che OpenFreeMap pubblica insieme ai dati (OpenFreeMap · © OpenMapTiles ·
// Data from OpenStreetMap).

import { onAuthChange, sessionEpoch } from '../core/auth.js';
import { navigate } from '../core/router.js';
import * as api from '../census/census-api.js';
import { buildingPlace, buildingStreet, buildingTitle, errorMessage, summaryView } from '../census/census-model.js';
import { loadMapLibre } from '../territorio/maplibre-loader.js';
import {
  STATUS_LABELS, classifyBuildings, toPlaceText, verifiedFeatureCollection, viewFor,
} from '../territorio/territorio-model.js';

export const MAP_STYLE_URL = 'https://tiles.openfreemap.org/styles/positron';
// Alba Adriatica (TE), centro: [longitudine, latitudine]
export const MAP_START = { center: [13.9258, 42.8337], zoom: 14 };
export const MAP_ROUTE_HASH = '#/edifici/mappa';
// Il massimo per richiesta del server (property/router.py::list_buildings, le=200).
export const BUILDINGS_PAGE_SIZE = 200;
const SORGENTE = 'stima-buildings';
const LIVELLO = 'stima-buildings-points';

let attiva = null;   // { map, stacca }

function distruggi() {
  if (!attiva) return;
  const { map, stacca } = attiva;
  attiva = null;
  stacca();
  try {
    map.remove();
  } catch (_errore) {
    // una mappa gia' rotta non deve bloccare la navigazione
  }
}

/** Solo per le prove: c'e' una mappa viva? */
export function hasActiveMap() {
  return attiva !== null;
}

function sullaRotta() {
  return window.location.hash.replace(/\/+$/, '') === MAP_ROUTE_HASH;
}

/**
 * Tutti gli edifici visibili all'operatore, pagina dopo pagina. Nessun tetto
 * imposto qui: si smette quando il server non ne ha altri (pagina vuota o
 * corta, oppure `total` raggiunto) o quando una pagina non porta nessun id
 * nuovo (un server che ignorasse `offset` non fa girare all'infinito).
 */
export async function loadAllBuildings(list = api.listBuildings) {
  const tutti = [];
  const visti = new Set();
  let offset = 0;
  for (;;) {
    const pagina = await list({ sort: 'address', limit: BUILDINGS_PAGE_SIZE, offset });
    const righe = Array.isArray(pagina && pagina.items) ? pagina.items : [];
    let nuovi = 0;
    for (const r of righe) {
      const chiave = r && r.id !== undefined && r.id !== null ? String(r.id) : null;
      if (chiave === null || visti.has(chiave)) continue;
      visti.add(chiave);
      tutti.push(r);
      nuovi += 1;
    }
    offset += righe.length;
    const totale = pagina && Number.isInteger(pagina.total) ? pagina.total : null;
    if (!righe.length || !nuovi || righe.length < BUILDINGS_PAGE_SIZE || (totale !== null && offset >= totale)) break;
  }
  return tutti;
}

function el(tag, { cls = null, text = null, attrs = null } = {}) {
  const nodo = document.createElement(tag);
  if (cls) nodo.className = cls;
  if (text !== null) nodo.textContent = text;
  for (const [k, v] of Object.entries(attrs || {})) {
    // gli attributi data-* passano da `dataset` (nel browser e' lo stesso attributo)
    if (k.startsWith('data-')) nodo.dataset[k.slice(5).replace(/-([a-z])/g, (_m, c) => c.toUpperCase())] = v;
    else nodo.setAttribute(k, v);
  }
  return nodo;
}

function rigaEdificio(b) {
  const dove = [buildingStreet(b), buildingPlace(b)].filter(Boolean).join(' · ');
  const titolo = buildingTitle(b);
  return dove && dove !== titolo ? `${titolo} — ${dove}` : titolo;
}

/** L'elenco essenziale degli edifici che non stanno sulla mappa. */
function disegnaDaPosizionare(host, gruppi) {
  host.replaceChildren();
  host.hidden = false;
  const sommario = el('summary', { text: toPlaceText(gruppi.toPlace) });
  sommario.dataset.toPlaceCount = String(gruppi.toPlace);
  host.appendChild(sommario);
  host.appendChild(el('p', { cls: 'muted territorio-note',
    text: `Sulla mappa: ${gruppi.verified.length} con posizione verificata su ${gruppi.total}. `
      + 'Le coordinate presenti nel CRM non sono ancora verificate: compaiono sulla mappa solo dopo la verifica.' }));
  for (const stato of ['missing', 'unverified', 'invalid']) {
    const voci = gruppi[stato];
    if (!voci.length) continue;
    const blocco = el('div', { cls: 'territorio-group', attrs: { 'data-position-group': stato } });
    blocco.appendChild(el('h3', { text: `${STATUS_LABELS[stato]}: ${voci.length}` }));
    const lista = el('ul', { cls: 'territorio-list' });
    for (const { building } of voci) {
      const voce = el('li');
      const link = el('a', { text: rigaEdificio(building),
        attrs: { href: `#/edifici/${encodeURIComponent(String(building.id))}`, 'data-building-link': String(building.id) } });
      voce.appendChild(link);
      lista.appendChild(voce);
    }
    blocco.appendChild(lista);
    host.appendChild(blocco);
  }
}

/** Il riquadro di un marker: solo DOM e `textContent`. */
function contenutoRiquadro(b, { dettaglio = null, stato = 'loading' } = {}) {
  const fonte = dettaglio || b;
  const box = el('div', { cls: 'territorio-popup', attrs: { 'data-popup-building': String(b.id) } });
  box.appendChild(el('strong', { cls: 'territorio-popup-title', text: buildingTitle(fonte) }));
  const via = buildingStreet(fonte);
  if (via) box.appendChild(el('div', { text: via }));
  const luogo = buildingPlace(fonte);
  if (luogo) box.appendChild(el('div', { cls: 'muted', text: luogo }));
  const v = summaryView(fonte.census_summary);
  box.appendChild(el('div', { cls: 'territorio-popup-facts',
    text: `Unità dichiarate: ${v.declared} · censite: ${v.counted} · da completare: ${v.toComplete}` }));
  if (stato === 'loading') box.appendChild(el('div', { cls: 'muted', text: 'Aggiornamento del dettaglio…' }));
  if (stato === 'error') box.appendChild(el('div', { cls: 'muted', text: 'Dettaglio non disponibile ora.' }));
  const apri = el('button', { cls: 'btn primary', text: 'Apri scheda edificio', attrs: { type: 'button', 'data-open-building': String(b.id) } });
  apri.addEventListener('click', () => navigate('edifici', [String(b.id)]));
  box.appendChild(apri);
  return box;
}

export async function renderTerritorio(container) {
  distruggi();
  container.innerHTML = `
    <div class="territorio-page">
      <div class="territorio-head">
        <a href="#/edifici" class="btn ghost territorio-back" data-back-to-buildings>← Torna agli edifici</a>
        <h2>Mappa</h2>
      </div>
      <div class="territorio-status" data-map-status role="status" aria-live="polite">
        <span data-map-status-text>Caricamento della mappa…</span>
        <button type="button" class="btn ghost" data-map-retry hidden>Riprova</button>
      </div>
      <div class="territorio-status" data-buildings-status role="status" aria-live="polite">
        <span data-buildings-status-text>Caricamento degli edifici…</span>
        <button type="button" class="btn ghost" data-buildings-retry hidden>Riprova</button>
      </div>
      <div class="territorio-map card" data-map aria-label="Mappa stradale"></div>
      <details class="card territorio-pending" data-to-place hidden></details>
    </div>`;
  const stato = container.querySelector('[data-map-status]');
  const testo = container.querySelector('[data-map-status-text]');
  const riprova = container.querySelector('[data-map-retry]');
  const area = container.querySelector('[data-map]');
  const statoEdifici = container.querySelector('[data-buildings-status]');
  const testoEdifici = container.querySelector('[data-buildings-status-text]');
  const daPosizionare = container.querySelector('[data-to-place]');
  riprova.addEventListener('click', () => renderTerritorio(container));
  container.querySelector('[data-buildings-retry]').addEventListener('click', () => renderTerritorio(container));

  const mostra = (messaggio, { errore = false } = {}) => {
    stato.hidden = false;
    stato.classList.toggle('is-error', errore);
    testo.textContent = messaggio;
    riprova.hidden = !errore;
  };

  const epoca = sessionEpoch();
  const vivo = () => container.isConnected && sullaRotta() && sessionEpoch() === epoca;

  // Gli edifici si leggono UNA volta, in parallelo alla libreria. Una risposta
  // arrivata dopo la navigazione o dopo un cambio di sessione si butta.
  let gruppi = null;
  let mappaPronta = null;          // (gruppi) => void, quando la mappa e' caricata
  loadAllBuildings().then((items) => {
    if (!vivo()) return;
    gruppi = classifyBuildings(items);
    statoEdifici.hidden = true;
    disegnaDaPosizionare(daPosizionare, gruppi);
    if (mappaPronta) mappaPronta(gruppi);
  }, (errore) => {
    if (!vivo()) return;
    statoEdifici.hidden = false;
    statoEdifici.classList.add('is-error');
    testoEdifici.textContent = `Impossibile leggere gli edifici: ${errorMessage(errore)}`;
    container.querySelector('[data-buildings-retry]').hidden = false;
  });

  let ml;
  try {
    ml = await loadMapLibre();
  } catch (_errore) {
    if (vivo()) {
      mostra('Impossibile caricare la mappa: controlla la connessione e riprova.', { errore: true });
    }
    return;
  }
  // nel frattempo si e' andati altrove, o la sessione e' cambiata: niente mappa
  if (!vivo()) return;
  distruggi();

  let map;
  try {
    map = new ml.Map({
      container: area,
      style: MAP_STYLE_URL,
      center: MAP_START.center,
      zoom: MAP_START.zoom,
      attributionControl: { compact: false },
    });
  } catch (_errore) {
    mostra('Questo browser non riesce a disegnare la mappa (WebGL non disponibile).', { errore: true });
    return;
  }
  map.addControl(new ml.NavigationControl({ showCompass: false }), 'top-right');

  const suHash = () => { if (!sullaRotta()) distruggi(); };
  window.addEventListener('hashchange', suHash);
  const staccaAuth = onAuthChange(() => distruggi());
  attiva = {
    map,
    stacca: () => {
      window.removeEventListener('hashchange', suHash);
      staccaAuth();
    },
  };

  // I marker: un livello della mappa sui SOLI edifici verificati. Lo stato
  // «attivo» e' uno stato della feature (colore e raggio), uno alla volta.
  const dettagli = new Map();       // id -> scheda letta (una volta per edificio, per questa apertura)
  let attivo = null;
  let riquadro = null;
  const perId = new Map();
  const seleziona = (id) => {
    if (attivo !== null) map.setFeatureState({ source: SORGENTE, id: attivo }, { selected: false });
    attivo = id;
    if (id !== null) map.setFeatureState({ source: SORGENTE, id }, { selected: true });
    area.dataset.activeBuilding = id === null ? '' : String(id);
  };
  const apriRiquadro = (id) => {
    const voce = perId.get(id);
    if (!voce) return;
    if (riquadro) riquadro.remove();
    seleziona(id);
    const b = voce.building;
    const nota = dettagli.has(id) ? 'ready' : 'loading';
    const popup = new ml.Popup({ closeButton: true, closeOnClick: true, offset: 12, maxWidth: '300px' })
      .setLngLat([voce.lng, voce.lat])
      .setDOMContent(contenutoRiquadro(b, { dettaglio: dettagli.get(id) || null, stato: nota }))
      .addTo(map);
    riquadro = popup;
    popup.on('close', () => {
      if (riquadro === popup) { riquadro = null; seleziona(null); }
    });
    if (nota === 'ready') return;
    api.getBuilding(b.id).then((scheda) => {
      if (!vivo() || !attiva || attiva.map !== map) return;
      // la scheda deve essere proprio quella chiesta (stesso id): altrimenti si ignora
      if (!scheda || String(scheda.id) !== String(b.id)) throw new Error('scheda inattesa');
      dettagli.set(id, scheda);
      if (riquadro === popup) popup.setDOMContent(contenutoRiquadro(b, { dettaglio: scheda, stato: 'ready' }));
    }).catch(() => {
      if (riquadro === popup && vivo()) popup.setDOMContent(contenutoRiquadro(b, { stato: 'error' }));
    });
  };

  let caricata = false;
  let disegnati = false;
  const disegnaPunti = (g) => {
    if (!caricata || disegnati || !attiva || attiva.map !== map) return;
    disegnati = true;
    for (const voce of g.verified) perId.set(Number(voce.building.id), voce);
    map.addSource(SORGENTE, { type: 'geojson', data: verifiedFeatureCollection(g.verified) });
    map.addLayer({
      id: LIVELLO,
      type: 'circle',
      source: SORGENTE,
      paint: {
        'circle-radius': ['case', ['boolean', ['feature-state', 'selected'], false], 11, 7],
        'circle-color': ['case', ['boolean', ['feature-state', 'selected'], false], '#c2410c', '#1a56db'],
        'circle-stroke-color': '#ffffff',
        'circle-stroke-width': 2,
      },
    });
    map.on('click', LIVELLO, (e) => {
      const f = e && e.features && e.features[0];
      if (f && f.id !== undefined && f.id !== null) apriRiquadro(Number(f.id));
    });
    map.on('mouseenter', LIVELLO, () => { map.getCanvas().style.cursor = 'pointer'; });
    map.on('mouseleave', LIVELLO, () => { map.getCanvas().style.cursor = ''; });
    const vista = viewFor(g.verified);
    if (vista && vista.bounds) map.fitBounds(vista.bounds, { padding: 48, maxZoom: 17, duration: 0 });
    else if (vista) map.jumpTo({ center: vista.center, zoom: vista.zoom });
  };
  mappaPronta = disegnaPunti;

  map.on('load', () => {
    caricata = true;
    stato.hidden = true;
    if (gruppi) disegnaPunti(gruppi);
  });
  // Prima del primo disegno un errore e' la cartografia che non risponde (stile,
  // rete): lo si dice. Dopo, sono tessere singole che MapLibre riprova da solo.
  map.on('error', () => {
    if (!caricata && attiva && attiva.map === map) {
      mostra('La cartografia non risponde: controlla la connessione e riprova.', { errore: true });
    }
  });
}
