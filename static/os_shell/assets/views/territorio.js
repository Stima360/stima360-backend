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
// ZONA-4 - la posizione dello smartphone (territorio/gps.js) col cerchio della
// precisione; passata al pannello come punto provvisorio. Nulla di salvato o
// inviato: la verifica resta la conferma.
//
// ZONA-6 - Zona alleggerita:
//   * posizione in tempo reale: all'apertura il punto blu, la mappa centrata
//     alla prima posizione (se l'operatore non l'ha gia' spostata), il punto
//     che si aggiorna; il GPS si ferma quando la mappa si chiude (`onMapClosed`);
//   * scheda rapida al tocco di un edificio: indirizzo, ultimo contatto e
//     prossima azione quando ci sono (GET /api/crm/sellers/zone, UNA lettura
//     per apertura), «Apri scheda completa»;
//   * un solo «+» sulla mappa: dalla mia posizione, da un punto scelto o da un
//     indirizzo, verso il pannello esistente (controllo e conferma del punto,
//     poi nuovo edificio / nuovo immobile / collega un esistente);
//   * tre filtri rapidi: Tutti, Da verificare (edifici con coordinate non
//     confermate, punti grigi), Da ricontattare (la vista «Da richiamare»
//     dei Venditori); cio' che non puo' comparire sulla mappa si dice.
//
// ZONA-3 - gli IMMOBILI INDIPENDENTI (ville, locali autonomi: `properties`
// senza edificio) con posizione VERIFICATA (migration 103) sono un secondo
// livello della stessa mappa, con un marker distinguibile. UNA lettura per
// apertura dalla lista LEGGERA del server (GET /api/property/properties/
// positions: solo verificati, solo colonne essenziali, 200 per pagina), stessa
// regola di verifica degli edifici applicata di nuovo qui. Clic: riquadro con
// le informazioni essenziali e «Apri scheda immobile» (#/immobili/{id}); il
// pannello sotto la mappa idem. Le unita' di palazzina restano sull'edificio
// (mai un marker doppio). Selezione in indirizzo: #/edifici/mappa/immobile/{id}.
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
  buildingPlace, buildingStreet, buildingTitle, errorMessage, groupUnitsByFloor, labelOf, unitFacts,
} from '../census/census-model.js';
import { loadFormOptions } from '../components/property-form.js';
import { loadMapLibre } from '../territorio/maplibre-loader.js';
import { renderPosiziona } from './territorio-posiziona.js';
import { chosenPoint, mountInserimento } from './territorio-inserisci.js';
import { mountGps } from '../territorio/gps.js';
import { zoneSummary } from '../sellers/sellers-api.js';
import {
  STATUS_LABELS, ZONE_FILTERS, classifyBuildings, filterLabel, propertyFeatureCollection, propertyTitle, propertyWhere, quickFacts,
  recontactIds, toPlaceText, unverifiedPoints, verifiedFeatureCollection, verifiedProperties, viewForAll, zoneIndex,
} from '../territorio/territorio-model.js';

export const MAP_STYLE_URL = 'https://tiles.openfreemap.org/styles/positron';
// Alba Adriatica (TE), centro: [longitudine, latitudine]
export const MAP_START = { center: [13.9258, 42.8337], zoom: 14 };
export const MAP_ROUTE_HASH = '#/edifici/mappa';
// Il massimo per richiesta del server (property/router.py::list_buildings, le=200).
export const BUILDINGS_PAGE_SIZE = 200;
const SORGENTE = 'stima-buildings';
const LIVELLO = 'stima-buildings-points';
// ZONA-3: gli immobili indipendenti verificati, sorgente e livello propri
const SORGENTE_IMMOBILI = 'stima-properties';
const LIVELLO_IMMOBILI = 'stima-properties-points';
// ZONA-6: gli edifici «Da verificare» (coordinate non confermate), visibili solo con quel filtro
const SORGENTE_DA_VERIFICARE = 'stima-buildings-unverified';
const LIVELLO_DA_VERIFICARE = 'stima-buildings-unverified-points';
export const PROPERTIES_PAGE_SIZE = 200;

let attiva = null;   // { map, stacca, pulizie }

function distruggi() {
  if (!attiva) return;
  const { map, stacca, pulizie } = attiva;
  attiva = null;
  stacca();
  // ZONA-6: chi vive con la mappa (il GPS) si ferma con lei
  for (const fn of pulizie) {
    try { fn(); } catch (_errore) { /* una pulizia rotta non blocca la navigazione */ }
  }
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
    pulizie: [],
  };
  return map;
}

/** ZONA-6: `fn` si esegue quando QUESTA mappa si chiude (altra rotta, sessione, ridisegno). */
export function onMapClosed(map, fn) {
  if (attiva && attiva.map === map) attiva.pulizie.push(fn);
  else fn();
}

// T4: dopo una conferma riuscita la mappa generale apre l'edificio appena posizionato.
let daEvidenziare = null;
export function highlightAfterConfirm(buildingId) {
  daEvidenziare = buildingId === null || buildingId === undefined ? null : Number(buildingId);
}

// ZONA-3: idem per un immobile indipendente appena verificato
let immobileDaEvidenziare = null;
export function highlightPropertyAfterConfirm(propertyId) {
  immobileDaEvidenziare = propertyId === null || propertyId === undefined ? null : Number(propertyId);
}

// T5: la mappa generale e' anche `#/edifici/mappa/edificio/{id}` (edificio selezionato);
// ZONA-3: e `#/edifici/mappa/immobile/{id}` (immobile indipendente selezionato)
const ROTTA_GENERALE = /^#\/edifici\/mappa(\/(edificio|immobile)\/\d+)?$/;

function sullaRotta() {
  return ROTTA_GENERALE.test(window.location.hash.replace(/\/+$/, ''));
}

/** T5: l'edificio (ZONA-3: o l'immobile) selezionato entra nell'indirizzo senza ricaricare la vista. */
function scriviSelezione(id, tipo = 'edificio') {
  if (!window.history || typeof window.history.replaceState !== 'function' || !sullaRotta()) return;
  const voluto = id === null ? MAP_ROUTE_HASH : `${MAP_ROUTE_HASH}/${tipo}/${id}`;
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

/**
 * ZONA-3: gli immobili indipendenti verificati, pagina dopo pagina (stessa
 * strategia di `loadAllBuildings`: il server restituisce SOLO i verificati).
 */
export async function loadAllPropertyPositions(list = api.listPropertyPositions) {
  const tutti = [];
  const visti = new Set();
  let offset = 0;
  for (;;) {
    const pagina = await list({ limit: PROPERTIES_PAGE_SIZE, offset });
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
    if (!righe.length || !nuovi || righe.length < PROPERTIES_PAGE_SIZE || (totale !== null && offset >= totale)) break;
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

/**
 * ZONA-6: le righe «Ultimo contatto» / «Prossima azione» della scheda rapida, solo
 * quelle disponibili. `zona` = { stato: 'loading'|'ready'|'error', voce }.
 */
function fattiRapidi(box, zona) {
  if (!zona || zona.stato === 'loading') {
    box.appendChild(el('div', { cls: 'muted territorio-popup-note', text: 'Lettura di contatti e azioni…' }));
    return;
  }
  if (zona.stato === 'error') {
    box.appendChild(el('div', { cls: 'muted territorio-popup-note', text: 'Ultimo contatto e prossima azione non disponibili ora.' }));
    return;
  }
  const righe = quickFacts(zona.voce);
  if (!righe.length) return;
  const fatti = el('dl', { cls: 'territorio-popup-facts' });
  for (const r of righe) {
    const d = el('div', { attrs: { 'data-quick-fact': r.key } });
    if (r.overdue) d.classList.add('is-overdue');
    d.appendChild(el('dt', { text: r.label }));
    d.appendChild(el('dd', { text: r.text }));
    fatti.appendChild(d);
  }
  box.appendChild(fatti);
}

/** ZONA-6: la scheda rapida di un edificio. Solo DOM e `textContent`. */
function contenutoRiquadro(b, { zona = null, daVerificare = false } = {}) {
  const box = el('div', { cls: 'territorio-popup', attrs: { 'data-popup-building': String(b.id) } });
  box.appendChild(el('strong', { cls: 'territorio-popup-title', text: buildingTitle(b) }));
  const dove = [buildingStreet(b), buildingPlace(b)].filter(Boolean).join(' · ');
  if (dove && dove !== buildingTitle(b)) box.appendChild(el('div', { cls: 'territorio-popup-where', text: dove }));
  if (daVerificare) box.appendChild(el('div', { cls: 'territorio-popup-note', text: 'Posizione da verificare' }));
  fattiRapidi(box, zona);
  const azioni = el('div', { cls: 'territorio-popup-actions' });
  if (daVerificare) {
    const verifica = el('button', { cls: 'btn primary btn-small', text: 'Verifica posizione',
      attrs: { type: 'button', 'data-verify-building': String(b.id) } });
    verifica.addEventListener('click', () => navigate('edifici', ['mappa', 'posiziona', String(b.id)]));
    azioni.appendChild(verifica);
  }
  const apri = el('button', { cls: `btn ${daVerificare ? 'ghost' : 'primary'} btn-small`, text: 'Apri scheda completa',
    attrs: { type: 'button', 'data-open-building': String(b.id) } });
  apri.addEventListener('click', () => navigate('edifici', [String(b.id)]));
  azioni.appendChild(apri);
  box.appendChild(azioni);
  return box;
}

/** ZONA-3/ZONA-6: la scheda rapida di un immobile indipendente: solo DOM e `textContent`. */
function contenutoRiquadroImmobile(p, { tipi = [], zona = null } = {}) {
  const box = el('div', { cls: 'territorio-popup territorio-popup-property', attrs: { 'data-popup-property': String(p.id) } });
  box.appendChild(el('strong', { cls: 'territorio-popup-title', text: propertyTitle(p) }));
  const dove = propertyWhere(p);
  if (dove) box.appendChild(el('div', { cls: 'territorio-popup-where', text: dove }));
  box.appendChild(el('div', { cls: 'muted', text: labelOf(tipi, p.property_type, p.property_type || 'Immobile') }));
  fattiRapidi(box, zona);
  const azioni = el('div', { cls: 'territorio-popup-actions' });
  const apri = el('button', { cls: 'btn primary btn-small', text: 'Apri scheda completa',
    attrs: { type: 'button', 'data-open-property': String(p.id) } });
  apri.addEventListener('click', () => navigate('immobili', [String(p.id)]));
  azioni.appendChild(apri);
  box.appendChild(azioni);
  return box;
}

/** ZONA-3: il pannello «Immobile» sotto la mappa (informazioni essenziali, verso la scheda esistente). */
function disegnaPannelloImmobile(host, p, { tipi = [] } = {}) {
  host.replaceChildren();
  host.hidden = false;
  host.dataset.buildingPanel = '';            // il pannello resta raggiungibile come [data-building-panel]
  host.dataset.propertyPanel = String(p.id);
  host.appendChild(el('h3', { cls: 'territorio-building-title', text: propertyTitle(p) }));
  const dove = propertyWhere(p);
  if (dove) host.appendChild(el('p', { cls: 'muted territorio-building-where', text: dove }));
  const azioni = el('div', { cls: 'territorio-building-actions' });
  azioni.appendChild(el('a', { cls: 'btn primary', text: 'Apri scheda immobile',
    attrs: { href: `#/immobili/${encodeURIComponent(String(p.id))}`, 'data-panel-open-property': String(p.id) } }));
  host.appendChild(azioni);
  const fatti = el('dl', { cls: 'building-facts territorio-property-facts' });
  const riga = (k, v) => {
    if (!v) return;
    const d = el('div');
    d.appendChild(el('dt', { text: k }));
    d.appendChild(el('dd', { text: v }));
    fatti.appendChild(d);
  };
  riga('Tipologia', labelOf(tipi, p.property_type, p.property_type || ''));
  riga('Scheda', p.record_kind === 'census' ? 'Censimento' : 'Commerciale');
  riga('Posizione', 'Verificata su Zona (immobile senza edificio)');
  host.appendChild(fatti);
  host.appendChild(el('p', { cls: 'muted', text: 'Indirizzo, dati e foto si aggiornano dalla scheda immobile.' }));
}

/**
 * T5: il pannello «Edificio» sotto la mappa. Solo DOM e `textContent`; ogni
 * unita' e' un collegamento alla scheda immobile esistente, «+ Aggiungi unita'»
 * al foglio esistente del censimento. `scheda` = risposta della GET dell'edificio.
 */
function disegnaPannello(host, b, { scheda = null, stato = 'loading', opzioni = null } = {}) {
  host.replaceChildren();
  host.hidden = false;
  host.dataset.propertyPanel = '';
  host.dataset.buildingPanel = String(b.id);
  const fonte = scheda || b;
  host.appendChild(el('h3', { cls: 'territorio-building-title', text: buildingTitle(fonte) }));
  const dove = [buildingStreet(fonte), buildingPlace(fonte)].filter(Boolean).join(' · ');
  if (dove) host.appendChild(el('p', { cls: 'muted territorio-building-where', text: dove }));
  const azioni = el('div', { cls: 'territorio-building-actions' });
  // ZONA-6: un solo «+» su Zona (quello della mappa): qui l'azione si chiama per nome
  azioni.appendChild(el('a', { cls: 'btn primary', text: 'Aggiungi unità',
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
      const testo = unitFacts(u, tipi, accessori) || propertyTitle(u);
      voce.appendChild(el('a', { text: testo,
        attrs: { href: `#/immobili/${encodeURIComponent(String(u.id))}`, 'data-unit-link': String(u.id) } }));
      lista.appendChild(voce);
    }
    host.appendChild(lista);
  }
}

/** ZONA-6: la nota sotto i filtri (cio' che il filtro non puo' mostrare sulla mappa). */
function disegnaNotaFiltro(host, righe) {
  host.replaceChildren();
  host.hidden = !righe.length;
  for (const r of righe) {
    if (typeof r === 'string') {
      host.appendChild(el('p', { text: r }));
      continue;
    }
    host.appendChild(el('p', { text: r.text }));
    if (r.links && r.links.length) {
      const lista = el('ul', { cls: 'territorio-list' });
      for (const { href, text, attrs } of r.links) {
        const voce = el('li');
        voce.appendChild(el('a', { text, attrs: { href, ...(attrs || {}) } }));
        lista.appendChild(voce);
      }
      host.appendChild(lista);
    }
  }
}

export async function renderTerritorio(container) {
  // T4: `#/edifici/mappa/posiziona/{id}` e' la pagina di posizionamento
  const parametri = currentRouteParams();
  if (parametri[1] === 'posiziona') return renderPosiziona(container, parametri[2]);
  // T5: `#/edifici/mappa/edificio/{id}` riapre la mappa con quell'edificio selezionato;
  // ZONA-3: `#/edifici/mappa/immobile/{id}` con quell'immobile indipendente
  const idRotta = /^\d+$/.test(String(parametri[2] || '')) ? Number(parametri[2]) : null;
  const daRotta = parametri[1] === 'edificio' ? idRotta : null;
  const immobileDaRotta = parametri[1] === 'immobile' ? idRotta : null;
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
      <div class="territorio-filters" role="group" aria-label="Filtri della mappa" data-zone-filters></div>
      <div class="territorio-filter-note muted" data-zone-filter-note role="status" aria-live="polite" hidden></div>
      <div class="territorio-map card" data-map aria-label="Mappa stradale"></div>
      <details class="card territorio-insert" data-insert-panel></details>
      <ul class="territorio-legend territorio-map-legend" aria-label="Legenda" data-map-legend>
        <li><span class="territorio-dot is-building" aria-hidden="true"></span> Edifici con posizione verificata</li>
        <li><span class="territorio-dot is-property" aria-hidden="true"></span> Immobili indipendenti con posizione verificata</li>
        <li data-legend-unverified hidden><span class="territorio-dot is-unverified" aria-hidden="true"></span> Edifici con posizione da verificare</li>
        <li><span class="territorio-dot is-me" aria-hidden="true"></span> La tua posizione</li>
      </ul>
      <section class="card territorio-building" data-building-panel hidden aria-live="polite"></section>
      <details class="card territorio-pending" data-to-place hidden></details>
      <p class="muted territorio-independent" data-independent-note>Ville, case indipendenti e locali autonomi non appartengono a un edificio: compaiono qui con un marker proprio quando la loro posizione è verificata (con il «+» della mappa); si consultano in <a href="#/immobili">Immobili</a>.</p>
    </div>`;
  const stato = container.querySelector('[data-map-status]');
  const testo = container.querySelector('[data-map-status-text]');
  const riprova = container.querySelector('[data-map-retry]');
  const area = container.querySelector('[data-map]');
  const statoEdifici = container.querySelector('[data-buildings-status]');
  const testoEdifici = container.querySelector('[data-buildings-status-text]');
  const daPosizionare = container.querySelector('[data-to-place]');
  const pannello = container.querySelector('[data-building-panel]');
  const barraFiltri = container.querySelector('[data-zone-filters]');
  const notaFiltro = container.querySelector('[data-zone-filter-note]');
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

  // ZONA-6: i tre filtri rapidi (Tutti di partenza); `aggiornaFiltri` li riallinea ai dati
  let filtro = 'all';
  let applicaFiltro = () => {};    // sostituita quando la mappa c'e'
  const bottoniFiltro = new Map();
  for (const f of ZONE_FILTERS) {
    const b = el('button', { cls: 'btn territorio-filter', text: f.label,
      attrs: { type: 'button', 'data-zone-filter': f.key, 'aria-pressed': f.key === filtro ? 'true' : 'false' } });
    b.addEventListener('click', () => {
      if (filtro === f.key) return;
      filtro = f.key;
      if (riquadro) riquadro.remove();       // la scheda aperta appartiene al filtro di prima
      applicaFiltro();
    });
    bottoniFiltro.set(f.key, b);
    barraFiltri.appendChild(b);
  }

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
    applicaFiltro();
  }, (errore) => {
    if (!vivo()) return;
    statoEdifici.hidden = false;
    statoEdifici.classList.add('is-error');
    testoEdifici.textContent = `Impossibile leggere gli edifici: ${errorMessage(errore)}`;
    container.querySelector('[data-buildings-retry]').hidden = false;
  });
  // ZONA-3: gli immobili indipendenti verificati, in parallelo e indipendenti dagli
  // edifici: se la loro lettura fallisce (es. 103 non ancora installata) la mappa degli
  // edifici resta intera e lo si dice nella legenda.
  let immobili = null;             // [{ property, lng, lat }]
  let immobiliPronti = null;       // (immobili) => void, quando la mappa e' caricata
  const legenda = container.querySelector('[data-map-legend]');
  loadAllPropertyPositions().then((items) => {
    if (!vivo()) return;
    immobili = verifiedProperties(items);
    if (immobiliPronti) immobiliPronti(immobili);
    applicaFiltro();
  }, (errore) => {
    if (!vivo()) return;
    immobili = [];
    legenda.appendChild(el('li', { cls: 'territorio-legend-note', text: `Immobili indipendenti non disponibili ora: ${errorMessage(errore)}`,
      attrs: { 'data-properties-error': '1' } }));
    if (immobiliPronti) immobiliPronti(immobili);
    applicaFiltro();
  });
  // ZONA-6: ultimo contatto, prossima azione e «Da ricontattare», UNA lettura per apertura.
  // Se fallisce la mappa resta intera: lo dicono la scheda rapida e il filtro.
  const zona = { stato: 'loading', indice: zoneIndex(null), errore: '' };
  let zonaPronta = () => {};
  zoneSummary().then((risposta) => {
    if (!vivo()) return;
    zona.stato = 'ready';
    zona.indice = zoneIndex(risposta);
    zonaPronta();
    applicaFiltro();
  }, (errore) => {
    if (!vivo()) return;
    zona.stato = 'error';
    zona.errore = errorMessage(errore);
    zonaPronta();
    applicaFiltro();
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

  // ZONA-2: il pannello di inserimento, sulla stessa mappa viva. Dopo una posizione
  // verificata la mappa si riapre con quell'edificio evidenziato (rilettura).
  const inserimento = mountInserimento(container.querySelector('[data-insert-panel]'), {
    map, ml, vivo, area,
    onLinked: (id) => {
      highlightAfterConfirm(id);
      const voluto = `${MAP_ROUTE_HASH}/edificio/${id}`;
      if (window.location.hash === voluto) renderTerritorio(container);
      else navigate('edifici', ['mappa', 'edificio', String(id)]);
    },
    // ZONA-3: un immobile indipendente appena verificato
    onPropertyLinked: (id) => {
      highlightPropertyAfterConfirm(id);
      const voluto = `${MAP_ROUTE_HASH}/immobile/${id}`;
      if (window.location.hash === voluto) renderTerritorio(container);
      else navigate('edifici', ['mappa', 'immobile', String(id)]);
    },
  });

  // ZONA-6: un solo «+» sulla mappa. Tre partenze verso lo STESSO pannello di inserimento:
  // dalla mia posizione, da un punto scelto sulla mappa, da un indirizzo cercato.
  const piu = el('div', { cls: 'territorio-add', attrs: { 'data-zone-add-box': '1' } });
  const piuBottone = el('button', { cls: 'btn primary territorio-add-btn', text: '+',
    attrs: { type: 'button', 'data-zone-add': '1', 'aria-label': 'Aggiungi dalla mappa', 'aria-expanded': 'false', 'aria-haspopup': 'true' } });
  const menu = el('div', { cls: 'territorio-add-menu', attrs: { 'data-zone-add-menu': '1', role: 'menu' } });
  menu.hidden = true;
  const voceMenu = (chiave, titolo, nota) => {
    const b = el('button', { cls: 'territorio-add-item', attrs: { type: 'button', role: 'menuitem', [`data-zone-add-${chiave}`]: '1' } });
    b.appendChild(el('strong', { text: titolo }));
    const sotto = el('span', { cls: 'muted', text: nota });
    b.appendChild(sotto);
    menu.appendChild(b);
    return { b, sotto };
  };
  const daGps = voceMenu('gps', 'Dalla mia posizione', 'Ricerca della tua posizione…');
  const daPunto = voceMenu('pick', 'Scegli un punto sulla mappa', 'Tocca il punto esatto: lo controlli prima di salvare.');
  const daIndirizzo = voceMenu('search', 'Cerca un indirizzo', 'Il punto trovato si controlla e si conferma.');
  menu.appendChild(el('p', { cls: 'muted territorio-add-note',
    text: 'Poi scegli: nuovo edificio, nuovo immobile o collega un edificio esistente.' }));
  piu.append(menu, piuBottone);
  area.appendChild(piu);
  const apriMenu = (si) => {
    menu.hidden = !si;
    piuBottone.setAttribute('aria-expanded', si ? 'true' : 'false');
  };
  piuBottone.addEventListener('click', () => {
    if (menu.hidden) aggiornaGps(gps.last());
    apriMenu(menu.hidden);
  });
  daGps.b.addEventListener('click', () => {
    const p = gps.last();
    if (!p) return;
    apriMenu(false);
    inserimento.placeProvisional(p);
  });
  daPunto.b.addEventListener('click', () => { apriMenu(false); inserimento.startPick(); });
  map.on('click', () => apriMenu(false));      // un tocco sulla mappa chiude il menu
  daIndirizzo.b.addEventListener('click', () => { apriMenu(false); inserimento.startSearch(); });

  // ZONA-6: la posizione in tempo reale, dall'apertura; si ferma quando la mappa si chiude
  let gps = null;
  const aggiornaGps = (p) => {
    daGps.b.disabled = !p;
    daGps.sotto.textContent = p ? `${area.dataset.gpsAccuracy || 'Posizione ricevuta'}: lo controlli prima di salvare.`
      : (gps && gps.status()) || 'La tua posizione non è ancora disponibile.';
  };
  // la prima posizione centra la mappa solo se nessun edificio, immobile o punto e' gia' in primo piano
  const inPrimoPiano = daRotta !== null || immobileDaRotta !== null || daEvidenziare !== null || immobileDaEvidenziare !== null
    || chosenPoint() !== null;
  gps = mountGps(area, { map, vivo, onChange: aggiornaGps, centra: () => !inPrimoPiano && !riquadro && !inserimento.busy() });
  onMapClosed(map, () => gps.stop());
  daGps.b.disabled = true;

  // I marker: un livello della mappa sui SOLI edifici verificati. Lo stato
  // «attivo» e' uno stato della feature (colore e raggio), uno alla volta
  // (ZONA-3: fra edifici E immobili; ZONA-6: e fra gli edifici da verificare).
  const dettagli = new Map();       // id -> scheda letta (una volta per edificio, per questa apertura)
  let attivo = null;
  let attivoSorgente = SORGENTE;
  let attivoImmobile = null;
  let riquadro = null;
  const perId = new Map();
  const perIdDaVerificare = new Map();
  const immobiliPerId = new Map();
  let livelloImmobiliPronto = false;
  const seleziona = (id, sorgente = SORGENTE) => {
    if (attivo !== null) map.setFeatureState({ source: attivoSorgente, id: attivo }, { selected: false });
    if (attivoImmobile !== null && livelloImmobiliPronto) map.setFeatureState({ source: SORGENTE_IMMOBILI, id: attivoImmobile }, { selected: false });
    attivo = id;
    attivoSorgente = sorgente;
    attivoImmobile = null;
    if (id !== null) map.setFeatureState({ source: sorgente, id }, { selected: true });
    area.dataset.activeBuilding = id === null ? '' : String(id);
    area.dataset.activeProperty = '';
  };
  const selezionaImmobile = (id) => {
    if (attivo !== null) map.setFeatureState({ source: attivoSorgente, id: attivo }, { selected: false });
    if (attivoImmobile !== null && livelloImmobiliPronto) map.setFeatureState({ source: SORGENTE_IMMOBILI, id: attivoImmobile }, { selected: false });
    attivo = null;
    attivoImmobile = id;
    if (id !== null && livelloImmobiliPronto) map.setFeatureState({ source: SORGENTE_IMMOBILI, id }, { selected: true });
    area.dataset.activeBuilding = '';
    area.dataset.activeProperty = id === null ? '' : String(id);
  };
  // T5: le etichette delle tipologie per il pannello, lette una volta (cache di property-form)
  let opzioni = null;
  const leggiOpzioni = () => loadFormOptions().then((o) => { opzioni = o; return o; }, () => null);
  const zonaDi = (tipo, id) => ({ stato: zona.stato,
    voce: zona.stato === 'ready' ? (tipo === 'edificio' ? zona.indice.buildings : zona.indice.properties).get(Number(id)) || null : null });
  // la scheda rapida aperta si aggiorna quando arrivano contatti e azioni
  let ridisegnaRiquadro = () => {};
  zonaPronta = () => ridisegnaRiquadro();

  const apriRiquadro = (id, { daVerificare = false } = {}) => {
    const voce = (daVerificare ? perIdDaVerificare : perId).get(id);
    if (!voce) return;
    if (riquadro) riquadro.remove();
    seleziona(id, daVerificare ? SORGENTE_DA_VERIFICARE : SORGENTE);
    scriviSelezione(id);
    const b = voce.building;
    const nota = dettagli.has(id) ? 'ready' : 'loading';
    const contenuto = () => contenutoRiquadro(b, { zona: zonaDi('edificio', id), daVerificare });
    const popup = new ml.Popup({ closeButton: true, closeOnClick: true, offset: 12, maxWidth: '300px' })
      .setLngLat([voce.lng, voce.lat])
      .setDOMContent(contenuto())
      .addTo(map);
    riquadro = popup;
    ridisegnaRiquadro = () => { if (riquadro === popup && vivo()) popup.setDOMContent(contenuto()); };
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
    // T5: le unita' del pannello sotto la mappa (la scheda rapida non le aspetta)
    Promise.all([api.getBuilding(b.id), opzioni ? opzioni : leggiOpzioni()]).then(([scheda]) => {
      if (!vivo() || !attiva || attiva.map !== map) return;
      // la scheda deve essere proprio quella chiesta (stesso id): altrimenti si ignora
      if (!scheda || String(scheda.id) !== String(b.id)) throw new Error('scheda inattesa');
      dettagli.set(id, scheda);
      if (riquadro === popup) disegnaPannello(pannello, b, { scheda, stato: 'ready', opzioni });
    }).catch(() => {
      if (riquadro === popup && vivo()) disegnaPannello(pannello, b, { stato: 'error', opzioni });
    });
  };

  // ZONA-3: il riquadro e il pannello di un immobile indipendente (nessuna lettura in piu':
  // la riga leggera della lista basta; il resto e' nella scheda esistente)
  const apriRiquadroImmobile = (id) => {
    const voce = immobiliPerId.get(id);
    if (!voce) return;
    if (riquadro) riquadro.remove();
    selezionaImmobile(id);
    scriviSelezione(id, 'immobile');
    const p = voce.property;
    const contenuto = () => contenutoRiquadroImmobile(p, { tipi: opzioni ? opzioni.property_types || [] : [], zona: zonaDi('immobile', id) });
    const popup = new ml.Popup({ closeButton: true, closeOnClick: true, offset: 12, maxWidth: '300px' })
      .setLngLat([voce.lng, voce.lat])
      .setDOMContent(contenuto())
      .addTo(map);
    riquadro = popup;
    ridisegnaRiquadro = () => { if (riquadro === popup && vivo()) popup.setDOMContent(contenuto()); };
    disegnaPannelloImmobile(pannello, p, { tipi: opzioni ? opzioni.property_types || [] : [] });
    popup.on('close', () => {
      if (riquadro !== popup) return;
      riquadro = null;
      selezionaImmobile(null);
      if (isActiveMap(map) && sullaRotta()) {
        pannello.hidden = true;
        pannello.replaceChildren();
        scriviSelezione(null);
      }
    });
    if (!opzioni) {
      leggiOpzioni().then((o) => {
        if (!o || riquadro !== popup || !vivo()) return;
        popup.setDOMContent(contenuto());
        disegnaPannelloImmobile(pannello, p, { tipi: o.property_types || [] });
      });
    }
  };

  let caricata = false;
  let disegnati = false;
  let disegnatiImmobili = false;
  let disegnatiDaVerificare = false;
  // ZONA-3: il livello degli immobili indipendenti, quando mappa e lista sono pronte
  const disegnaImmobili = (lista) => {
    if (!caricata || disegnatiImmobili || !attiva || attiva.map !== map) return;
    disegnatiImmobili = true;
    for (const voce of lista) immobiliPerId.set(Number(voce.property.id), voce);
    map.addSource(SORGENTE_IMMOBILI, { type: 'geojson', data: propertyFeatureCollection(lista) });
    map.addLayer({
      id: LIVELLO_IMMOBILI,
      type: 'circle',
      source: SORGENTE_IMMOBILI,
      paint: {
        'circle-radius': ['case', ['boolean', ['feature-state', 'selected'], false], 10, 6],
        'circle-color': ['case', ['boolean', ['feature-state', 'selected'], false], '#c2410c', '#15803d'],
        'circle-stroke-color': '#ffffff',
        'circle-stroke-width': 2,
      },
    });
    livelloImmobiliPronto = true;
    map.on('click', LIVELLO_IMMOBILI, (e) => {
      const f = e && e.features && e.features[0];
      if (f && f.id !== undefined && f.id !== null) apriRiquadroImmobile(Number(f.id));
    });
    map.on('mouseenter', LIVELLO_IMMOBILI, () => { map.getCanvas().style.cursor = 'pointer'; });
    map.on('mouseleave', LIVELLO_IMMOBILI, () => { map.getCanvas().style.cursor = ''; });
    const evidenzia = immobileDaEvidenziare !== null ? immobileDaEvidenziare : immobileDaRotta;
    immobileDaEvidenziare = null;
    const appena = evidenzia !== null ? immobiliPerId.get(evidenzia) : null;
    if (appena) {
      map.jumpTo({ center: [appena.lng, appena.lat], zoom: 17 });
      apriRiquadroImmobile(evidenzia);
    } else if (disegnati && attivo === null && gruppi && !gruppi.verified.length && lista.length) {
      // nessun edificio da inquadrare: si inquadrano gli immobili
      const vista = viewForAll([], lista);
      if (vista && vista.bounds) map.fitBounds(vista.bounds, { padding: 48, maxZoom: 17, duration: 0 });
      else if (vista) map.jumpTo({ center: vista.center, zoom: vista.zoom });
    }
    applicaFiltro();
  };
  immobiliPronti = disegnaImmobili;

  // ZONA-6: gli edifici «Da verificare» (punti grigi): il livello nasce la prima volta
  // che si sceglie quel filtro (all'apertura la mappa e' quella di sempre)
  const disegnaDaVerificare = (g) => {
    if (!caricata || disegnatiDaVerificare || !attiva || attiva.map !== map) return;
    disegnatiDaVerificare = true;
    const punti = unverifiedPoints(g);
    for (const voce of punti) perIdDaVerificare.set(Number(voce.building.id), voce);
    map.addSource(SORGENTE_DA_VERIFICARE, { type: 'geojson', data: verifiedFeatureCollection(punti) });
    map.addLayer({
      id: LIVELLO_DA_VERIFICARE,
      type: 'circle',
      source: SORGENTE_DA_VERIFICARE,
      layout: { visibility: 'none' },
      paint: {
        'circle-radius': ['case', ['boolean', ['feature-state', 'selected'], false], 10, 7],
        'circle-color': ['case', ['boolean', ['feature-state', 'selected'], false], '#c2410c', '#6b7280'],
        'circle-stroke-color': '#ffffff',
        'circle-stroke-width': 2,
      },
    });
    map.on('click', LIVELLO_DA_VERIFICARE, (e) => {
      const f = e && e.features && e.features[0];
      if (f && f.id !== undefined && f.id !== null) apriRiquadro(Number(f.id), { daVerificare: true });
    });
    map.on('mouseenter', LIVELLO_DA_VERIFICARE, () => { map.getCanvas().style.cursor = 'pointer'; });
    map.on('mouseleave', LIVELLO_DA_VERIFICARE, () => { map.getCanvas().style.cursor = ''; });
  };

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
    applicaFiltro();
    const evidenzia = daEvidenziare !== null ? daEvidenziare : daRotta;
    daEvidenziare = null;
    const appena = evidenzia !== null ? perId.get(evidenzia) : null;
    if (appena) {
      // T4: l'edificio appena confermato, al centro e gia' aperto (marker attivo)
      map.jumpTo({ center: [appena.lng, appena.lat], zoom: 17 });
      apriRiquadro(evidenzia);
      return;
    }
    // ZONA-6: la posizione dell'operatore ha la precedenza sull'inquadratura degli edifici
    if (gps.last()) return;
    // ZONA-3: se e' un immobile a dover essere evidenziato, la vista la decide il suo livello
    if ((immobileDaEvidenziare !== null || immobileDaRotta !== null) && (!immobili || immobili.length)) return;
    const vista = viewForAll(g.verified, immobili || []);
    if (vista && vista.bounds) map.fitBounds(vista.bounds, { padding: 48, maxZoom: 17, duration: 0 });
    else if (vista) map.jumpTo({ center: vista.center, zoom: vista.zoom });
  };
  mappaPronta = disegnaPunti;

  // ZONA-6: il filtro scelto sulla mappa (livelli accesi/spenti, filtro per id) e la nota
  const visibile = (livello, si) => {
    if (typeof map.getLayer === 'function' && !map.getLayer(livello)) return;
    if (typeof map.setLayoutProperty === 'function') map.setLayoutProperty(livello, 'visibility', si ? 'visible' : 'none');
  };
  const filtraPer = (livello, chiave, ids) => {
    if (typeof map.getLayer === 'function' && !map.getLayer(livello)) return;
    if (typeof map.setFilter === 'function') map.setFilter(livello, ids === null ? null : ['in', ['get', chiave], ['literal', ids]]);
  };
  applicaFiltro = () => {
    if (!attiva || attiva.map !== map) return;
    for (const [chiave, b] of bottoniFiltro) {
      b.setAttribute('aria-pressed', chiave === filtro ? 'true' : 'false');
      b.classList.toggle('active', chiave === filtro);
    }
    area.dataset.activeFilter = filtro;
    if (filtro === 'verify' && gruppi) disegnaDaVerificare(gruppi);
    container.querySelector('[data-legend-unverified]').hidden = filtro !== 'verify';
    const daVerificare = gruppi ? unverifiedPoints(gruppi) : null;
    const ricontatto = zona.stato === 'ready' ? recontactIds(zona.indice) : null;
    const ricontattoSullaMappa = ricontatto ? {
      buildings: ricontatto.buildings.filter((id) => perId.has(id) || (gruppi && gruppi.verified.some((v) => Number(v.building.id) === id))),
      properties: ricontatto.properties.filter((id) => immobiliPerId.has(id) || (immobili && immobili.some((v) => Number(v.property.id) === id))),
    } : null;
    // i conteggi sui filtri: solo quando noti
    const tutti = gruppi && immobili ? gruppi.verified.length + immobili.length : null;
    bottoniFiltro.get('all').textContent = filterLabel('all', tutti);
    bottoniFiltro.get('verify').textContent = filterLabel('verify', daVerificare ? daVerificare.length : null);
    bottoniFiltro.get('recontact').textContent = filterLabel('recontact',
      ricontattoSullaMappa ? ricontattoSullaMappa.buildings.length + ricontattoSullaMappa.properties.length : null);
    visibile(LIVELLO, filtro !== 'verify');
    visibile(LIVELLO_IMMOBILI, filtro !== 'verify');
    visibile(LIVELLO_DA_VERIFICARE, filtro === 'verify');
    filtraPer(LIVELLO, 'building_id', filtro === 'recontact' ? (ricontattoSullaMappa ? ricontattoSullaMappa.buildings : []) : null);
    filtraPer(LIVELLO_IMMOBILI, 'property_id', filtro === 'recontact' ? (ricontattoSullaMappa ? ricontattoSullaMappa.properties : []) : null);
    // la nota: cio' che il filtro non puo' mostrare, detto in chiaro
    const righe = [];
    if (filtro === 'verify') {
      if (!gruppi) righe.push('Lettura degli edifici…');
      else {
        righe.push(daVerificare.length === 1 ? '1 edificio con la posizione da verificare: toccalo per verificarla.'
          : `${daVerificare.length} edifici con la posizione da verificare: tocca un punto grigio per verificarla.`);
        const fuori = gruppi.missing.length + gruppi.invalid.length;
        if (fuori) {
          righe.push(`Non possono comparire sulla mappa: ${gruppi.missing.length} senza coordinate, `
            + `${gruppi.invalid.length} con coordinate non valide (sono in «${toPlaceText(gruppi.toPlace)}»).`);
        }
        righe.push('Immobili indipendenti: la mappa riceve solo quelli già verificati, quindi qui non compaiono quelli da verificare.');
      }
    } else if (filtro === 'recontact') {
      if (zona.stato === 'loading') righe.push('Lettura dei venditori da ricontattare…');
      else if (zona.stato === 'error') righe.push(`«Da ricontattare» non è disponibile ora: ${zona.errore}`);
      else {
        const n = ricontattoSullaMappa.buildings.length + ricontattoSullaMappa.properties.length;
        righe.push(n === 1 ? '1 da ricontattare sulla mappa: un venditore ha la prossima azione scaduta.'
          : `${n} da ricontattare sulla mappa: almeno un venditore con la prossima azione scaduta.`);
        const edificiFuori = ricontatto.buildings.filter((id) => !ricontattoSullaMappa.buildings.includes(id));
        const immobiliFuori = ricontatto.properties.filter((id) => !ricontattoSullaMappa.properties.includes(id));
        if (edificiFuori.length) {
          const nomi = new Map();
          for (const stato of ['unverified', 'invalid', 'missing']) {
            for (const { building } of (gruppi && gruppi[stato]) || []) nomi.set(Number(building.id), building);
          }
          righe.push({ text: `${edificiFuori.length} ${edificiFuori.length === 1 ? 'edificio' : 'edifici'} da ricontattare senza posizione verificata (non compaiono sulla mappa):`,
            links: edificiFuori.map((id) => ({ href: `#/edifici/${id}`, text: nomi.has(id) ? rigaEdificio(nomi.get(id)) : `Edificio ${id}`,
              attrs: { 'data-recontact-off-map': String(id) } })) });
        }
        if (immobiliFuori.length) {
          righe.push({ text: `${immobiliFuori.length} ${immobiliFuori.length === 1 ? 'immobile indipendente' : 'immobili indipendenti'} da ricontattare senza posizione verificata:`,
            links: immobiliFuori.map((id) => ({ href: `#/immobili/${id}`, text: `Apri l’immobile ${id}`, attrs: { 'data-recontact-off-map-property': String(id) } })) });
        }
      }
    }
    disegnaNotaFiltro(notaFiltro, righe);
  };

  map.on('load', () => {
    caricata = true;
    stato.hidden = true;
    if (gruppi) disegnaPunti(gruppi);
    if (immobili) disegnaImmobili(immobili);
    applicaFiltro();
  });
  // Prima del primo disegno un errore e' la cartografia che non risponde (stile,
  // rete): lo si dice. Dopo, sono tessere singole che MapLibre riprova da solo.
  map.on('error', () => {
    if (!caricata && attiva && attiva.map === map) {
      mostra('La cartografia non risponde: controlla la connessione e riprova.', { errore: true });
    }
  });
  applicaFiltro();
}
