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
// T4 - `#/edifici/mappa/posiziona/{id}` (views/territorio-posiziona.js): stessa
// mappa e stesso ciclo di vita (`openMap`); dopo una conferma riuscita questa
// vista apre l'edificio appena verificato (`highlightAfterConfirm`).
//
// T5 - il pannello «Edificio» sotto la mappa: al clic su un marker mostra le
// unita' dell'edificio (dalla stessa GET della scheda, gia' letta per il
// riquadro), raggruppate per piano, ognuna verso la scheda immobile esistente
// (#/immobili/{id}); «+ Aggiungi unita'» apre il foglio esistente del
// censimento (#/edifici/{id}/aggiungi/censimento). Nessun salvataggio qui.
// L'edificio selezionato e' nell'indirizzo (#/edifici/mappa/edificio/{id},
// senza ricaricare): tornando indietro dalla scheda la mappa lo riapre.
//
// ZONA-2 - «Inserisci dalla mappa» (views/territorio-inserisci.js): dalla mappa
// generale si sceglie un punto (ricerca indirizzo o tocco) e si aprono i moduli
// ESISTENTI del CRM (procedura guidata, conferma T4 su un edificio esistente). La
// posizione scelta si collega all'edificio al suo salvataggio; questa vista non
// scrive nulla: il pannello e' montato qui e vive con la mappa.
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
import { currentRouteParams, navigate } from '../core/router.js';
import * as api from '../census/census-api.js';
import {
  buildingPlace, buildingStreet, buildingTitle, errorMessage, groupUnitsByFloor, summaryView, unitFacts,
} from '../census/census-model.js';
import { loadFormOptions } from '../components/property-form.js';
import { loadMapLibre } from '../territorio/maplibre-loader.js';
import { renderPosiziona } from './territorio-posiziona.js';
import { mountInserimento } from './territorio-inserisci.js';
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

/** La mappa viva e' ancora questa? (risposte tardive, gestori) */
export function isActiveMap(map) {
  return attiva !== null && attiva.map === map;
}

/** Chiude la mappa viva, se c'e' (T4: la pagina di posizionamento usa lo stesso ciclo). */
export function destroyActiveMap() {
  distruggi();
}

/**
 * STIMA360 TERRITORIO T4: lo stesso ciclo di vita di T2, condiviso. Crea la
 * mappa (stile, attribuzione estesa, zoom), la registra come UNICA mappa viva e
 * la distrugge quando l'indirizzo non e' piu' `restaSu()` o la sessione cambia.
 * Lancia se il browser non sa disegnare (WebGL): chi chiama lo dice.
 */
export function openMap(ml, area, { center = MAP_START.center, zoom = MAP_START.zoom, restaSu = sullaRotta } = {}) {
  distruggi();
  const map = new ml.Map({
    container: area,
    style: MAP_STYLE_URL,
    center,
    zoom,
    attributionControl: { compact: false },
  });
  map.addControl(new ml.NavigationControl({ showCompass: false }), 'top-right');
  // ZONA-2: il gestore di QUESTA mappa chiude solo lei, mai una mappa aperta dopo
  // (nello stesso cambio di indirizzo) da un'altra pagina.
  const suHash = () => { if (!restaSu() && attiva && attiva.map === map) distruggi(); };
  window.addEventListener('hashchange', suHash);
  const staccaAuth = onAuthChange(() => distruggi());
  attiva = {
    map,
    stacca: () => {
      window.removeEventListener('hashchange', suHash);
      staccaAuth();
    },
  };
  return map;
}

// T4: dopo una conferma riuscita la mappa generale apre l'edificio appena posizionato.
let daEvidenziare = null;
export function highlightAfterConfirm(buildingId) {
  daEvidenziare = buildingId === null || buildingId === undefined ? null : Number(buildingId);
}

// T5: la mappa generale e' anche `#/edifici/mappa/edificio/{id}` (edificio selezionato)
const ROTTA_GENERALE = /^#\/edifici\/mappa(\/edificio\/\d+)?$/;

function sullaRotta() {
  return ROTTA_GENERALE.test(window.location.hash.replace(/\/+$/, ''));
}

/** T5: l'edificio selezionato entra nell'indirizzo senza ricaricare la vista. */
function scriviSelezione(id) {
  if (!window.history || typeof window.history.replaceState !== 'function' || !sullaRotta()) return;
  const voluto = id === null ? MAP_ROUTE_HASH : `${MAP_ROUTE_HASH}/edificio/${id}`;
  if (window.location.hash !== voluto) window.history.replaceState(null, '', voluto);
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
function contenutoRiquadro(b, { dettaglio = null, stato = 'loading', vediUnita = null } = {}) {
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
  if (vediUnita) {
    const unita = el('button', { cls: 'btn ghost', text: 'Vedi unità ↓', attrs: { type: 'button', 'data-show-units': String(b.id) } });
    unita.addEventListener('click', vediUnita);
    box.appendChild(unita);
  }
  return box;
}

/**
 * T5: il pannello «Edificio» sotto la mappa. Solo DOM e `textContent`; ogni
 * unita' e' un collegamento alla scheda immobile esistente, «+ Aggiungi unita'»
 * al foglio esistente del censimento. `scheda` = risposta della GET dell'edificio.
 */
function disegnaPannello(host, b, { scheda = null, stato = 'loading', opzioni = null } = {}) {
  host.replaceChildren();
  host.hidden = false;
  host.dataset.buildingPanel = String(b.id);
  const fonte = scheda || b;
  host.appendChild(el('h3', { cls: 'territorio-building-title', text: buildingTitle(fonte) }));
  const dove = [buildingStreet(fonte), buildingPlace(fonte)].filter(Boolean).join(' · ');
  if (dove) host.appendChild(el('p', { cls: 'muted territorio-building-where', text: dove }));
  const azioni = el('div', { cls: 'territorio-building-actions' });
  azioni.appendChild(el('a', { cls: 'btn primary', text: '+ Aggiungi unità',
    attrs: { href: `#/edifici/${encodeURIComponent(String(b.id))}/aggiungi/censimento`, 'data-add-unit': String(b.id) } }));
  azioni.appendChild(el('a', { cls: 'btn ghost', text: 'Apri scheda edificio',
    attrs: { href: `#/edifici/${encodeURIComponent(String(b.id))}`, 'data-panel-open-building': String(b.id) } }));
  host.appendChild(azioni);
  if (stato === 'loading') {
    host.appendChild(el('p', { cls: 'muted', text: 'Caricamento delle unità…' }));
    return;
  }
  if (stato === 'error' || !scheda) {
    host.appendChild(el('p', { cls: 'muted', text: 'Unità non disponibili ora: apri la scheda edificio.' }));
    return;
  }
  const unita = Array.isArray(scheda.units) ? scheda.units : [];
  host.appendChild(el('p', { cls: 'territorio-building-count', text: `Unità immobiliari: ${unita.length}`,
    attrs: { 'data-units-count': String(unita.length) } }));
  if (!unita.length) {
    host.appendChild(el('p', { cls: 'muted', text: 'Nessuna unità censita in questo edificio.' }));
    return;
  }
  const tipi = opzioni ? opzioni.property_types || [] : [];
  const accessori = opzioni ? opzioni.accessory_kinds || [] : [];
  for (const gruppo of groupUnitsByFloor(unita)) {
    host.appendChild(el('h4', { cls: 'territorio-floor', text: gruppo.label }));
    const lista = el('ul', { cls: 'territorio-units' });
    for (const u of gruppo.units) {
      const voce = el('li');
      const testo = [u.code, unitFacts(u, tipi, accessori)].filter(Boolean).join(' · ');
      voce.appendChild(el('a', { text: testo,
        attrs: { href: `#/immobili/${encodeURIComponent(String(u.id))}`, 'data-unit-link': String(u.id) } }));
      lista.appendChild(voce);
    }
    host.appendChild(lista);
  }
}

export async function renderTerritorio(container) {
  // T4: `#/edifici/mappa/posiziona/{id}` e' la pagina di posizionamento
  const parametri = currentRouteParams();
  if (parametri[1] === 'posiziona') return renderPosiziona(container, parametri[2]);
  // T5: `#/edifici/mappa/edificio/{id}` riapre la mappa con quell'edificio selezionato
  const daRotta = parametri[1] === 'edificio' && /^\d+$/.test(String(parametri[2] || '')) ? Number(parametri[2]) : null;
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
      <details class="card territorio-insert" data-insert-panel></details>
      <div class="territorio-map card" data-map aria-label="Mappa stradale"></div>
      <section class="card territorio-building" data-building-panel hidden aria-live="polite"></section>
      <details class="card territorio-pending" data-to-place hidden></details>
      <p class="muted territorio-independent" data-independent-note>Ville, case indipendenti e locali autonomi non appartengono a un edificio: si consultano in <a href="#/immobili">Immobili</a>.</p>
    </div>`;
  const stato = container.querySelector('[data-map-status]');
  const testo = container.querySelector('[data-map-status-text]');
  const riprova = container.querySelector('[data-map-retry]');
  const area = container.querySelector('[data-map]');
  const statoEdifici = container.querySelector('[data-buildings-status]');
  const testoEdifici = container.querySelector('[data-buildings-status-text]');
  const daPosizionare = container.querySelector('[data-to-place]');
  const pannello = container.querySelector('[data-building-panel]');
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
    map = openMap(ml, area);
  } catch (_errore) {
    mostra('Questo browser non riesce a disegnare la mappa (WebGL non disponibile).', { errore: true });
    return;
  }

  // ZONA-2: il pannello «Inserisci dalla mappa», sulla stessa mappa viva. Dopo una
  // posizione verificata la mappa si riapre con quell'edificio evidenziato (rilettura).
  mountInserimento(container.querySelector('[data-insert-panel]'), {
    map, ml, vivo,
    onLinked: (id) => {
      highlightAfterConfirm(id);
      const voluto = `${MAP_ROUTE_HASH}/edificio/${id}`;
      if (window.location.hash === voluto) renderTerritorio(container);
      else navigate('edifici', ['mappa', 'edificio', String(id)]);
    },
  });

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
  // T5: le etichette delle tipologie per il pannello, lette una volta (cache di property-form)
  let opzioni = null;
  const leggiOpzioni = () => loadFormOptions().then((o) => { opzioni = o; return o; }, () => null);
  const vediUnita = () => {
    if (typeof pannello.scrollIntoView === 'function') pannello.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };
  const apriRiquadro = (id) => {
    const voce = perId.get(id);
    if (!voce) return;
    if (riquadro) riquadro.remove();
    seleziona(id);
    scriviSelezione(id);
    const b = voce.building;
    const nota = dettagli.has(id) ? 'ready' : 'loading';
    const popup = new ml.Popup({ closeButton: true, closeOnClick: true, offset: 12, maxWidth: '300px' })
      .setLngLat([voce.lng, voce.lat])
      .setDOMContent(contenutoRiquadro(b, { dettaglio: dettagli.get(id) || null, stato: nota, vediUnita }))
      .addTo(map);
    riquadro = popup;
    disegnaPannello(pannello, b, { scheda: dettagli.get(id) || null, stato: nota, opzioni });
    popup.on('close', () => {
      if (riquadro !== popup) return;
      riquadro = null;
      seleziona(null);
      // chiusura dell'operatore (non la navigazione verso un'altra pagina): pannello chiuso
      if (isActiveMap(map) && sullaRotta()) {
        pannello.hidden = true;
        pannello.replaceChildren();
        scriviSelezione(null);
      }
    });
    if (nota === 'ready') return;
    Promise.all([api.getBuilding(b.id), opzioni ? opzioni : leggiOpzioni()]).then(([scheda]) => {
      if (!vivo() || !attiva || attiva.map !== map) return;
      // la scheda deve essere proprio quella chiesta (stesso id): altrimenti si ignora
      if (!scheda || String(scheda.id) !== String(b.id)) throw new Error('scheda inattesa');
      dettagli.set(id, scheda);
      if (riquadro === popup) {
        popup.setDOMContent(contenutoRiquadro(b, { dettaglio: scheda, stato: 'ready', vediUnita }));
        disegnaPannello(pannello, b, { scheda, stato: 'ready', opzioni });
      }
    }).catch(() => {
      if (riquadro === popup && vivo()) {
        popup.setDOMContent(contenutoRiquadro(b, { stato: 'error', vediUnita }));
        disegnaPannello(pannello, b, { stato: 'error', opzioni });
      }
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
    const evidenzia = daEvidenziare !== null ? daEvidenziare : daRotta;
    daEvidenziare = null;
    const appena = evidenzia !== null ? perId.get(evidenzia) : null;
    if (appena) {
      // T4: l'edificio appena confermato, al centro e gia' aperto (marker attivo)
      map.jumpTo({ center: [appena.lng, appena.lat], zoom: 17 });
      apriRiquadro(evidenzia);
      return;
    }
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
