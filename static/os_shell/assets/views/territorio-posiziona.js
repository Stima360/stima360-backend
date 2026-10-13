// STIMA360 OS — views/territorio-posiziona.js (STIMA360 TERRITORIO T4)
//
// `#/edifici/mappa/posiziona/{id}`: l'operatore posiziona A MANO un edificio e
// ne conferma la posizione. Stessa mappa di T2/T3 (stesso caricatore, stesso
// ciclo di vita: `openMap` di views/territorio.js), nessun geocoding.
//
//   1. in alto il nome dell'edificio; la mappa e' grande;
//   2. coordinate gia' presenti: un punto GRIGIO «da verificare» (non verificate)
//      o BLU «verificata» (posizione attuale), al centro, zoom stretto; nessuna
//      coordinata: la mappa parte da Alba Adriatica;
//   3. un tocco mette il punto PROVVISORIO (arancione); un altro tocco o il
//      trascinamento lo spostano. Niente si salva finche' non si conferma;
//   4. «Conferma posizione» e' attiva solo con un punto e a zoom >= 17
//      (precisione); invia SOLO le coordinate: operatore e data li registra il
//      server (POST /api/property/buildings/{id}/position);
//   5. solo con la risposta «verified» del server si apre la mappa generale con
//      l'edificio evidenziato; ogni errore resta qui, leggibile, e si riprova.
//
// T5-bis - «Cerca indirizzo»: SOLO su pressione di «Cerca» (nessuna ricerca
// mentre si scrive, nessuna all'apertura) il testo scritto dall'operatore va a
// GET /api/property/geocode (il backend lo inoltra a Nominatim/OpenStreetMap con
// il limite globale e la cache). Fino a 5 risultati; scegliendone uno la mappa
// si centra e compare il punto PROVVISORIO, correggibile come un tocco. Se il
// civico non e' stato trovato lo si dice: il punto e' sulla via, non sull'edificio.
// Niente viene salvato dalla ricerca: solo «Conferma posizione» (T4).
//
// ZONA-2 - il modulo di ricerca e' condiviso con Zona (views/territorio-inserisci.js:
// stesso scheletro, stessa regola «solo dal pulsante»). Se su Zona e' stato scelto
// un punto (`chosenPoint`), questa pagina RIPARTE da quel punto come provvisorio:
// e' la via di recupero quando il collegamento automatico non e' riuscito.
//
// I dati del CRM entrano nel DOM solo come `textContent`.

import { sessionEpoch } from '../core/auth.js';
import { navigate } from '../core/router.js';
import * as api from '../census/census-api.js';
import { buildingPlace, buildingStreet, buildingTitle, errorMessage } from '../census/census-model.js';
import { loadMapLibre } from '../territorio/maplibre-loader.js';
import {
  MIN_CONFIRM_ZOOM, canConfirm, parseCoordinates, pointForSave, positionStatus,
} from '../territorio/territorio-model.js';
import { MAP_START, highlightAfterConfirm, isActiveMap, openMap } from './territorio.js';
import { chosenPoint, clearChosenPoint, geocodeSearchHtml, mountGeocodeSearch } from './territorio-inserisci.js';

const TESTI = {
  tocca: 'Tocca il punto esatto dell’edificio sulla mappa.',
  correggi: 'Puoi toccare un altro punto o trascinare il segnaposto per correggerlo.',
  zoom: `Ingrandisci ancora la mappa per confermare con precisione (zoom ${MIN_CONFIRM_ZOOM} o più).`,
  pronto: 'Controlla il punto e premi «Conferma posizione».',
  salva: 'Salvataggio della posizione…',
  civicoNo: 'Civico non trovato: il punto è sulla via, non sull’edificio. Spostalo sul punto esatto.',
  risultato: 'Punto suggerito dalla ricerca: controllalo e correggilo se serve.',
  daZona: 'Punto scelto su Zona: controllalo e confermalo, oppure correggilo.',
};

function pin(classe, etichetta) {
  const el = document.createElement('div');
  el.className = `territorio-pin ${classe}`;
  el.setAttribute('aria-label', etichetta);
  el.setAttribute('role', 'img');
  return el;
}

export async function renderPosiziona(container, rawId) {
  const id = String(rawId || '');
  if (!/^\d+$/.test(id)) {
    container.innerHTML = '<div class="error-box">Identificativo edificio non valido.</div>';
    return;
  }
  const rotta = `#/edifici/mappa/posiziona/${id}`;
  const epoca = sessionEpoch();
  const vivo = () => container.isConnected && window.location.hash.replace(/\/+$/, '') === rotta
    && sessionEpoch() === epoca;

  container.innerHTML = `
    <div class="territorio-page territorio-place">
      <div class="territorio-head">
        <a class="btn ghost territorio-back" data-back-to-building>← Torna alla scheda</a>
        <h2 data-place-title>Posiziona edificio</h2>
        <div class="muted" data-place-subtitle></div>
      </div>
      ${geocodeSearchHtml('la posizione si salva con «Conferma posizione».')}
      <div class="territorio-status" data-map-status role="status" aria-live="polite">
        <span data-map-status-text>Caricamento…</span>
      </div>
      <div class="territorio-map territorio-map-place card" data-map aria-label="Mappa per posizionare l’edificio"></div>
      <ul class="territorio-legend" aria-label="Legenda">
        <li><span class="territorio-pin is-provisional" aria-hidden="true"></span> Punto provvisorio (non salvato)</li>
        <li><span class="territorio-pin is-verified" aria-hidden="true"></span> Posizione verificata</li>
        <li><span class="territorio-pin is-unverified" aria-hidden="true"></span> Posizione da verificare</li>
      </ul>
      <div class="territorio-place-bar" data-place-bar>
        <p class="territorio-place-hint" data-place-hint role="status" aria-live="polite"></p>
        <p class="territorio-place-error" data-place-error role="alert" hidden></p>
        <div class="territorio-place-actions">
          <a class="btn ghost" data-place-cancel>Annulla</a>
          <button type="button" class="btn primary" data-place-confirm disabled>Conferma posizione</button>
        </div>
      </div>
    </div>`;
  const q = (s) => container.querySelector(s);
  const scheda = `#/edifici/${id}`;
  q('[data-back-to-building]').setAttribute('href', scheda);
  q('[data-place-cancel]').setAttribute('href', scheda);
  const stato = q('[data-map-status]');
  const testoStato = q('[data-map-status-text]');
  const suggerimento = q('[data-place-hint]');
  const erroreEl = q('[data-place-error]');
  const conferma = q('[data-place-confirm]');
  const area = q('[data-map]');
  const barra = q('[data-place-bar]');

  const blocca = (messaggio) => {
    stato.hidden = false;
    stato.classList.add('is-error');
    testoStato.textContent = messaggio;
    barra.hidden = true;
  };

  let edificio;
  let ml;
  try {
    [edificio, ml] = await Promise.all([api.getBuilding(id), loadMapLibre()]);
  } catch (errore) {
    if (!vivo()) return;
    blocca(errore && errore.code !== undefined && errore.status
      ? `Impossibile aprire l’edificio: ${errorMessage(errore)}`
      : 'Impossibile caricare la mappa: controlla la connessione e riprova.');
    return;
  }
  if (!vivo()) return;
  if (!edificio || String(edificio.id) !== id) {
    blocca('Edificio non trovato.');
    return;
  }
  q('[data-place-title]').textContent = buildingTitle(edificio);
  q('[data-place-subtitle]').textContent = [buildingStreet(edificio), buildingPlace(edificio)].filter(Boolean).join(' · ');
  if (edificio.deleted_at) {
    blocca('L’edificio è nel Cestino: non si può posizionare.');
    return;
  }

  const esistente = parseCoordinates(edificio);
  const statoPosizione = positionStatus(edificio);
  const partenza = esistente && esistente.valid
    ? { center: [esistente.lng, esistente.lat], zoom: 18 }
    : { center: MAP_START.center, zoom: 15 };

  let map;
  try {
    map = openMap(ml, area, { ...partenza, restaSu: () => window.location.hash.replace(/\/+$/, '') === rotta });
  } catch (_errore) {
    blocca('Questo browser non riesce a disegnare la mappa (WebGL non disponibile).');
    return;
  }

  let punto = null;          // { lng, lat } scelto, NON salvato
  let provvisorio = null;    // il segnaposto arancione
  let salvataggio = false;

  const aggiorna = () => {
    const zoom = map.getZoom();
    conferma.disabled = !canConfirm({ point: punto, zoom, saving: salvataggio });
    if (salvataggio) suggerimento.textContent = TESTI.salva;
    else if (!punto) suggerimento.textContent = TESTI.tocca;
    else if (zoom < MIN_CONFIRM_ZOOM) suggerimento.textContent = `${notaRicerca ? `${notaRicerca} ` : ''}${TESTI.zoom}`;
    else suggerimento.textContent = `${notaRicerca ? `${notaRicerca} ` : ''}${TESTI.pronto} ${TESTI.correggi}`;
  };

  // --- T5-bis: la ricerca indirizzo (solo dal pulsante; modulo condiviso, ZONA-2) ---
  mountGeocodeSearch(q('[data-geocode-form]'), {
    vivo,
    puoCercare: () => !salvataggio,
    onPick: (r) => {
      map.jumpTo({ center: [r.lng, r.lat], zoom: r.civicoOk ? 18 : 16 });
      scegli({ lng: r.lng, lat: r.lat }, { nota: r.civicoOk ? TESTI.risultato : TESTI.civicoNo });
    },
  });

  let notaRicerca = null;    // T5-bis: avviso sul punto suggerito (civico trovato o no), finche' non lo si sposta

  const scegli = (lngLat, { nota = null } = {}) => {
    if (salvataggio || !lngLat) return;
    punto = { lng: lngLat.lng, lat: lngLat.lat };
    notaRicerca = nota;
    erroreEl.hidden = true;
    if (!provvisorio) {
      provvisorio = new ml.Marker({ element: pin('is-provisional', 'Punto provvisorio'), draggable: true })
        .setLngLat([punto.lng, punto.lat])
        .addTo(map);
      provvisorio.on('dragend', () => {
        const p = provvisorio.getLngLat();
        punto = { lng: p.lng, lat: p.lat };
        notaRicerca = null;
        aggiorna();
      });
    } else {
      provvisorio.setLngLat([punto.lng, punto.lat]);
    }
    aggiorna();
  };

  // ZONA-2: il punto scelto su Zona diventa il provvisorio di partenza (recupero)
  const daZona = chosenPoint();
  if (daZona) {
    map.jumpTo({ center: [daZona.lng, daZona.lat], zoom: 18 });
    scegli({ lng: daZona.lng, lat: daZona.lat }, { nota: TESTI.daZona });
  }

  map.on('load', () => {
    if (!isActiveMap(map)) return;
    stato.hidden = true;
    if (esistente && esistente.valid) {
      // il punto attuale, NON trascinabile e trasparente ai tocchi (si tocca la mappa)
      const verificato = statoPosizione === 'verified';
      new ml.Marker({ element: pin(verificato ? 'is-verified is-current' : 'is-unverified is-current',
        verificato ? 'Posizione verificata attuale' : 'Posizione da verificare'), draggable: false })
        .setLngLat([esistente.lng, esistente.lat])
        .addTo(map);
    }
    aggiorna();
  });
  map.on('error', () => {
    if (isActiveMap(map) && !map.loaded()) {
      stato.hidden = false;
      stato.classList.add('is-error');
      testoStato.textContent = 'La cartografia non risponde: controlla la connessione e riprova.';
    }
  });
  map.on('click', (e) => scegli(e && e.lngLat));
  map.on('zoomend', aggiorna);
  aggiorna();

  conferma.addEventListener('click', async () => {
    const corpo = punto ? pointForSave(punto.lng, punto.lat) : null;
    if (!corpo || !canConfirm({ point: punto, zoom: map.getZoom(), saving: salvataggio })) {
      aggiorna();
      return;
    }
    salvataggio = true;
    erroreEl.hidden = true;
    aggiorna();
    let esito = null;
    try {
      esito = await api.confirmBuildingPosition(id, corpo);
    } catch (errore) {
      if (!vivo()) return;
      salvataggio = false;
      erroreEl.hidden = false;
      erroreEl.textContent = errore && errore.code === 'BUILDING_IN_TRASH'
        ? 'L’edificio è nel Cestino: non si può posizionare.'
        : `Posizione non salvata. ${errorMessage(errore)}`;
      aggiorna();
      return;
    }
    if (!vivo()) return;
    // riuscita SOLO se il server dice «verified» per QUESTO edificio
    if (!esito || String(esito.building_id) !== id || esito.status !== 'verified') {
      salvataggio = false;
      erroreEl.hidden = false;
      erroreEl.textContent = 'Posizione non salvata: risposta inattesa del server. Riprova.';
      aggiorna();
      return;
    }
    if (daZona) clearChosenPoint();          // ZONA-2: il punto di Zona e' stato usato
    highlightAfterConfirm(Number(id));
    navigate('edifici', ['mappa']);
  });
}
