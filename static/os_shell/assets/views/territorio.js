// STIMA360 OS — views/territorio.js (STIMA360 TERRITORIO T2)
//
// La mappa stradale del CRM: `#/edifici/mappa`, dentro la sezione Edifici
// (nessuna voce nuova nella barra laterale). In T2 SOLO la cartografia:
// MapLibre GL JS + OpenFreeMap (stile `positron`), centrata su Alba Adriatica,
// con zoom e spostamento. Nessun marker, nessun dato del CRM, nessuna chiamata
// alle API, nessuna geolocalizzazione.
//
// Ciclo di vita: al massimo UNA mappa viva. Si distrugge (`map.remove()`, che
// libera WebGL e worker) quando si apre un'altra rotta, quando la sessione
// cambia (logout / altra agenzia) o quando la vista viene ridisegnata; un
// caricamento lento che arriva dopo la navigazione non crea nulla.
//
// Attribuzione: la mostra il controllo di MapLibre, sempre esteso, con il testo
// che OpenFreeMap pubblica insieme ai dati (OpenFreeMap · © OpenMapTiles ·
// Data from OpenStreetMap).

import { onAuthChange, sessionEpoch } from '../core/auth.js';
import { loadMapLibre } from '../territorio/maplibre-loader.js';

export const MAP_STYLE_URL = 'https://tiles.openfreemap.org/styles/positron';
// Alba Adriatica (TE), centro: [longitudine, latitudine]
export const MAP_START = { center: [13.9258, 42.8337], zoom: 14 };
export const MAP_ROUTE_HASH = '#/edifici/mappa';

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

export async function renderTerritorio(container) {
  distruggi();
  container.innerHTML = `
    <div class="territorio-page">
      <div class="territorio-head">
        <a href="#/edifici" class="muted census-back">← Edifici</a>
        <h2>Mappa</h2>
      </div>
      <div class="territorio-status" data-map-status role="status" aria-live="polite">
        <span data-map-status-text>Caricamento della mappa…</span>
        <button type="button" class="btn ghost" data-map-retry hidden>Riprova</button>
      </div>
      <div class="territorio-map card" data-map aria-label="Mappa stradale"></div>
    </div>`;
  const stato = container.querySelector('[data-map-status]');
  const testo = container.querySelector('[data-map-status-text]');
  const riprova = container.querySelector('[data-map-retry]');
  const area = container.querySelector('[data-map]');
  riprova.addEventListener('click', () => renderTerritorio(container));

  const mostra = (messaggio, { errore = false } = {}) => {
    stato.hidden = false;
    stato.classList.toggle('is-error', errore);
    testo.textContent = messaggio;
    riprova.hidden = !errore;
  };

  const epoca = sessionEpoch();
  let ml;
  try {
    ml = await loadMapLibre();
  } catch (_errore) {
    if (container.isConnected && sullaRotta() && sessionEpoch() === epoca) {
      mostra('Impossibile caricare la mappa: controlla la connessione e riprova.', { errore: true });
    }
    return;
  }
  // nel frattempo si e' andati altrove, o la sessione e' cambiata: niente mappa
  if (!container.isConnected || !sullaRotta() || sessionEpoch() !== epoca) return;
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

  let caricata = false;
  map.on('load', () => {
    caricata = true;
    stato.hidden = true;
  });
  // Prima del primo disegno un errore e' la cartografia che non risponde (stile,
  // rete): lo si dice. Dopo, sono tessere singole che MapLibre riprova da solo.
  map.on('error', () => {
    if (!caricata && attiva && attiva.map === map) {
      mostra('La cartografia non risponde: controlla la connessione e riprova.', { errore: true });
    }
  });
}
