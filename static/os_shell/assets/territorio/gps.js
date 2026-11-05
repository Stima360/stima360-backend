// STIMA360 OS — territorio/gps.js (STIMA360 ZONA-4, ZONA-6)
//
// ZONA-6 - la posizione in tempo reale: appena si apre Zona la mappa chiede
// al browser la posizione dello smartphone (Geolocation API nativa,
// `watchPosition`) e la mostra con un punto blu e il cerchio della
// precisione. Alla PRIMA posizione la mappa si centra su di essa, a meno che
// l'operatore non l'abbia gia' spostata; poi il punto si aggiorna mentre ci
// si muove e la vista resta dove l'operatore la lascia (nessun «Seguimi»).
// Il GPS si ferma (`clearWatch`) quando si esce da Zona (`stop()`, chiamato
// dalla mappa quando si chiude) e mentre la pagina e' nascosta.
//
// La posizione personale resta nello stato di questa pagina: non viene
// salvata, non viene inviata al backend. Quello che si salva e' solo il punto
// che l'operatore sceglie con «+ → Dalla mia posizione», controlla e CONFERMA
// nel flusso di inserimento (ZONA-2/ZONA-3/ZONA-5): la verifica resta la
// conferma T4/103, mai il GPS da solo.
//
// Errori (permesso negato, posizione non disponibile, tempo scaduto, browser
// senza posizione): un messaggio leggibile sulla mappa finche' non arriva una
// posizione; il permesso negato ferma la richiesta. Nessun dato del CRM qui;
// solo `textContent`.

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

/**
 * Avvia la posizione in tempo reale sulla mappa viva `map` (contenitore `area`).
 *   vivo()      -> la pagina e' ancora questa (risposte tardive si buttano)
 *   onChange(p) -> ogni nuova posizione: p = { lng, lat, accuracy }
 *   centra()    -> alla prima posizione: si puo' centrare la mappa? (es. no se un
 *                  edificio o un punto scelto e' gia' in primo piano)
 *   geo         -> la Geolocation API (navigator.geolocation; per le prove una finta)
 *   doc         -> il documento (visibilita' della pagina)
 * Restituisce { stop(), last(), status() }: `stop()` ferma il GPS (idempotente),
 * `status()` il messaggio mostrato (attesa o errore), '' quando la posizione c'e'.
 */
export function mountGps(area, { map, vivo = () => true, onChange = () => {}, centra = () => true,
  geo = (typeof navigator !== 'undefined' && navigator ? navigator.geolocation : null),
  doc = (typeof document !== 'undefined' ? document : null) } = {}) {
  const stato = el('div', { cls: 'territorio-gps-status', attrs: { 'data-gps-status': '1', role: 'status', 'aria-live': 'polite' } });
  stato.hidden = true;
  area.appendChild(stato);

  let ultima = null;          // { lng, lat, accuracy }: SOLO in memoria, SOLO in questa pagina
  let guardia = null;         // l'id di watchPosition
  let fermo = false;
  let negato = false;         // permesso negato: non si richiede piu' (nemmeno tornando visibili)
  let centrata = false;       // la mappa si centra UNA volta, alla prima posizione
  let spostata = false;       // l'operatore ha gia' spostato la mappa: la vista resta sua
  let disegnato = false;
  let caricata = typeof map.loaded === 'function' ? !!map.loaded() : false;
  let daDisegnare = null;
  map.on('load', () => { caricata = true; if (daDisegnare) { disegna(daDisegnare); daDisegnare = null; } });
  // un gesto dell'operatore (non uno spostamento del programma) porta `originalEvent`
  for (const evento of ['dragstart', 'zoomstart', 'rotatestart', 'pitchstart']) {
    map.on(evento, (e) => { if (e && e.originalEvent) spostata = true; });
  }

  const mostra = (messaggio, statoGps) => {
    stato.hidden = !messaggio;
    stato.dataset.gpsState = statoGps;
    stato.classList.toggle('is-error', statoGps === 'error');
    stato.textContent = messaggio || '';
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

  const ricevuta = (posizione) => {
    if (fermo || !vivo()) return;            // si e' andati altrove: nulla da mostrare
    const c = posizione && posizione.coords ? posizione.coords : null;
    const lat = c ? Number(c.latitude) : NaN;
    const lng = c ? Number(c.longitude) : NaN;
    if (!Number.isFinite(lat) || !Number.isFinite(lng)) {
      if (!ultima) mostra(gpsErrorText(2), 'error');
      return;
    }
    const accuracy = c && Number.isFinite(Number(c.accuracy)) ? Number(c.accuracy) : NaN;
    ultima = { lng, lat, accuracy };
    if (!centrata) {
      centrata = true;
      if (!spostata && centra()) map.jumpTo({ center: [lng, lat], zoom: zoomForAccuracy(accuracy) });
    }
    disegna(gpsFeatureCollection(lng, lat, Number.isFinite(accuracy) ? accuracy : 0));
    area.dataset.gpsAccuracy = accuracyText(accuracy);
    mostra('', 'live');
    onChange({ ...ultima });
  };
  const errore = (e) => {
    if (fermo || !vivo()) return;
    const codice = e && typeof e === 'object' ? e.code : e;
    if (codice === 1) { negato = true; ferma(); }   // permesso negato: non si insiste
    if (!ultima || codice === 1) mostra(gpsErrorText(e), 'error');
  };

  function avvia() {
    if (fermo || negato || guardia !== null) return;
    if (!geo || typeof geo.watchPosition !== 'function') {
      mostra(gpsErrorText('unsupported'), 'error');
      return;
    }
    if (!ultima) mostra('Ricerca della tua posizione…', 'busy');
    guardia = geo.watchPosition(ricevuta, errore, { ...GPS_OPTIONS });
  }
  function ferma() {
    if (guardia !== null && geo && typeof geo.clearWatch === 'function') geo.clearWatch(guardia);
    guardia = null;
  }
  // pagina nascosta (altra app, schermo spento): GPS fermo; di nuovo visibile: riparte
  const visibilita = () => {
    if (!doc || fermo) return;
    if (doc.hidden) ferma();
    else if (vivo()) avvia();
  };
  if (doc && typeof doc.addEventListener === 'function') doc.addEventListener('visibilitychange', visibilita);

  avvia();
  return {
    stop() {
      if (fermo) return;
      fermo = true;
      ferma();
      if (doc && typeof doc.removeEventListener === 'function') doc.removeEventListener('visibilitychange', visibilita);
    },
    last: () => (ultima ? { ...ultima } : null),
    status: () => (stato.hidden ? '' : stato.textContent),
  };
}
