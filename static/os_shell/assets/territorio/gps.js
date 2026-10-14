// STIMA360 OS — territorio/gps.js (STIMA360 ZONA-4)
//
// «La mia posizione»: il pulsante sulla mappa di Zona che chiede al browser la
// posizione dello smartphone (Geolocation API nativa, `getCurrentPosition`,
// una richiesta FRESCA per ogni tocco: niente `watchPosition`, niente
// tracciamento, niente richiesta all'apertura della pagina). La posizione
// personale resta nello stato di questa pagina: non viene salvata, non viene
// inviata al backend, non diventa un marker persistente. Quello che si salva
// e' solo il punto che l'operatore trasferisce con «Inserisci qui», corregge
// e CONFERMA nel flusso «+ Inserisci dalla mappa» (ZONA-2/ZONA-3): la verifica
// resta la conferma T4/103, mai il GPS da solo.
//
//   1. tocco -> indicatore di attesa, pulsante bloccato (nessuna richiesta
//      doppia) -> il browser chiede il consenso se serve;
//   2. posizione ricevuta -> mappa centrata (zoom secondo la precisione),
//      cerchio indicativo della precisione + punto blu, «Precisione stimata:
//      circa N m», «Inserisci qui» e «Chiudi»;
//   3. permesso negato, posizione non disponibile, tempo scaduto, browser
//      senza posizione -> messaggio leggibile, si puo' riprovare;
//   4. si lascia la pagina durante la richiesta -> la risposta si butta.
//
// Nessun dato del CRM qui; solo `textContent`.

import {
  GPS_OPTIONS, accuracyText, gpsErrorText, gpsFeatureCollection, zoomForAccuracy,
} from './territorio-model.js';

const SORGENTE = 'stima-gps';
const LIVELLI = [
  { id: 'stima-gps-accuracy', type: 'fill', filter: ['==', ['geometry-type'], 'Polygon'],
    paint: { 'fill-color': '#1a56db', 'fill-opacity': 0.12 } },
  { id: 'stima-gps-accuracy-line', type: 'line', filter: ['==', ['geometry-type'], 'Polygon'],
    paint: { 'line-color': '#1a56db', 'line-opacity': 0.5, 'line-width': 1.5 } },
  { id: 'stima-gps-point', type: 'circle', filter: ['==', ['geometry-type'], 'Point'],
    paint: { 'circle-radius': 7, 'circle-color': '#1a56db', 'circle-stroke-color': '#ffffff', 'circle-stroke-width': 3 } },
];

const ICONA = 'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8zm9 3h-2.07A7.01 7.01 0 0 0 13 5.07V3h-2v2.07A7.01 7.01 0 0 0 5.07 11H3v2h2.07A7.01 7.01 0 0 0 11 18.93V21h2v-2.07A7.01 7.01 0 0 0 18.93 13H21v-2zm-9 8a5 5 0 1 1 0-10 5 5 0 0 1 0 10z';

function el(tag, { cls = null, text = null, attrs = null } = {}) {
  const nodo = document.createElement(tag);
  if (cls) nodo.className = cls;
  if (text !== null) nodo.textContent = text;
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k.startsWith('data-')) nodo.dataset[k.slice(5).replace(/-([a-z])/g, (_m, c) => c.toUpperCase())] = v;
    else nodo.setAttribute(k, v);
  }
  return nodo;
}

function icona() {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('width', '20');
  svg.setAttribute('height', '20');
  svg.setAttribute('aria-hidden', 'true');
  const p = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  p.setAttribute('d', ICONA);
  p.setAttribute('fill', 'currentColor');
  svg.appendChild(p);
  return svg;
}

/**
 * Monta il comando dentro `area` (il contenitore della mappa viva `map`).
 *   vivo()          -> la pagina e' ancora questa (risposte tardive si buttano)
 *   onInsertHere(p) -> «Inserisci qui»: p = { lng, lat, accuracy }
 *   geo             -> la Geolocation API (navigator.geolocation; per le prove una finta)
 * Restituisce { request() } solo per le prove.
 */
export function mountGps(area, { map, vivo = () => true, onInsertHere = () => {},
  geo = (typeof navigator !== 'undefined' && navigator ? navigator.geolocation : null) } = {}) {
  const box = el('div', { cls: 'territorio-gps', attrs: { 'data-gps': '1' } });
  const bottone = el('button', { cls: 'btn territorio-gps-btn', attrs: { type: 'button', 'data-gps-button': '1', 'aria-label': 'La mia posizione', 'aria-busy': 'false' } });
  bottone.appendChild(icona());
  bottone.appendChild(el('span', { cls: 'territorio-gps-label', text: 'La mia posizione' }));
  const stato = el('div', { cls: 'territorio-gps-status', attrs: { 'data-gps-status': '1', role: 'status', 'aria-live': 'polite', hidden: '' } });
  stato.hidden = true;
  const testo = el('p', { cls: 'territorio-gps-text', attrs: { 'data-gps-text': '1' } });
  const azioni = el('div', { cls: 'territorio-gps-actions' });
  const inserisci = el('button', { cls: 'btn primary btn-small', text: 'Inserisci qui', attrs: { type: 'button', 'data-gps-insert': '1' } });
  const chiudi = el('button', { cls: 'btn ghost btn-small', text: 'Chiudi', attrs: { type: 'button', 'data-gps-close': '1' } });
  azioni.append(inserisci, chiudi);
  stato.append(testo, azioni);
  box.append(bottone, stato);
  area.appendChild(box);

  let inCorso = false;
  let ultima = null;          // { lng, lat, accuracy }: SOLO in memoria, SOLO in questa pagina
  let disegnato = false;
  let caricata = typeof map.loaded === 'function' ? !!map.loaded() : false;
  let daDisegnare = null;
  map.on('load', () => { caricata = true; if (daDisegnare) { disegna(daDisegnare); daDisegnare = null; } });

  const mostra = (messaggio, { errore = false, pronto = false } = {}) => {
    stato.hidden = false;
    stato.classList.toggle('is-error', errore);
    stato.dataset.gpsState = errore ? 'error' : (pronto ? 'ready' : 'busy');
    testo.textContent = messaggio;
    inserisci.hidden = !pronto;
  };

  function disegna(fc) {
    if (!caricata) { daDisegnare = fc; return; }
    const sorgente = typeof map.getSource === 'function' ? map.getSource(SORGENTE) : null;
    if (sorgente && typeof sorgente.setData === 'function') {
      sorgente.setData(fc);
      return;
    }
    if (disegnato) return;
    disegnato = true;
    map.addSource(SORGENTE, { type: 'geojson', data: fc });
    for (const livello of LIVELLI) map.addLayer({ ...livello, source: SORGENTE });
  }

  function richiesta() {
    if (inCorso) return;                     // un tocco ripetuto non chiede due volte
    if (!geo || typeof geo.getCurrentPosition !== 'function') {
      mostra(gpsErrorText('unsupported'), { errore: true });
      return;
    }
    inCorso = true;
    bottone.disabled = true;
    bottone.setAttribute('aria-busy', 'true');
    box.classList.add('is-busy');
    mostra('Ricerca della posizione…');
    const fine = () => {
      inCorso = false;
      bottone.disabled = false;
      bottone.setAttribute('aria-busy', 'false');
      box.classList.remove('is-busy');
    };
    geo.getCurrentPosition((posizione) => {
      fine();
      if (!vivo()) return;                   // si e' andati altrove: nulla da mostrare
      const c = posizione && posizione.coords ? posizione.coords : null;
      const lat = c ? Number(c.latitude) : NaN;
      const lng = c ? Number(c.longitude) : NaN;
      if (!Number.isFinite(lat) || !Number.isFinite(lng)) {
        mostra(gpsErrorText(2), { errore: true });
        return;
      }
      const accuracy = c && Number.isFinite(Number(c.accuracy)) ? Number(c.accuracy) : NaN;
      ultima = { lng, lat, accuracy };
      map.jumpTo({ center: [lng, lat], zoom: zoomForAccuracy(accuracy) });
      disegna(gpsFeatureCollection(lng, lat, Number.isFinite(accuracy) ? accuracy : 0));
      mostra(`${accuracyText(accuracy)}. Il punto è indicativo: con «Inserisci qui» lo controlli, lo correggi e lo confermi tu.`, { pronto: true });
    }, (errore) => {
      fine();
      if (!vivo()) return;
      mostra(gpsErrorText(errore), { errore: true });
    }, { ...GPS_OPTIONS });
  }

  bottone.addEventListener('click', richiesta);
  inserisci.addEventListener('click', () => {
    if (!ultima) return;
    onInsertHere({ ...ultima });
    stato.hidden = true;
  });
  chiudi.addEventListener('click', () => { stato.hidden = true; });
  return { request: richiesta };
}
