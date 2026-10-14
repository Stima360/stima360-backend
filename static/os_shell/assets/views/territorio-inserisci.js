// STIMA360 OS — views/territorio-inserisci.js (STIMA360 TERRITORIO ZONA-2)
//
// Zona come punto d'ingresso operativo: dalla mappa generale (#/edifici/mappa)
// l'operatore sceglie un punto - con «Cerca indirizzo» (T5-bis, tramite il
// backend) oppure toccando la mappa - lo CONFERMA (stessa regola T4: zoom >= 17)
// e da li' apre i moduli ESISTENTI del CRM:
//
//   «Nuovo edificio» / «Nuovo immobile»  -> la procedura guidata di
//        CREAZIONE-GUIDATA-1 (census/census-wizard.js), con Comune, via e civico
//        gia' compilati quando vengono dalla ricerca. Quando la procedura SALVA
//        un edificio, il gancio `onBuildingReady` collega subito la posizione
//        scelta con la conferma T4 (POST /api/property/buildings/{id}/position:
//        operatore e data li registra il server). Se il collegamento fallisce
//        l'edificio NON viene duplicato e l'operazione NON e' dichiarata
//        conclusa: lo si dice, si riprova con lo stesso punto o si va alla
//        pagina di posizionamento, che riparte da quel punto.
//   «Collega un edificio esistente»       -> ricerca fra gli edifici dell'agenzia
//        e conferma T4 della posizione su questo punto.
//
// Il punto scelto resta in memoria per la sessione (`chosenPoint`), cosi' il
// passaggio Zona -> modulo -> Zona non lo perde; cambia sessione -> sparisce.
// Nessun localStorage. «Annulla» non scrive nulla.
//
// ZONA-3 - gli IMMOBILI INDIPENDENTI (ville, locali autonomi: senza edificio)
// hanno la loro verifica (migration 103, POST /api/property/properties/{id}/
// position): quando la scheda singola del percorso «Unita' autonoma» salva
// un immobile senza edificio, il gancio `onUnitSaved` collega subito il punto
// scelto; se il collegamento fallisce l'immobile NON viene duplicato e lo si
// dice («Immobile salvato, ma posizione non collegata»: «Riprova
// collegamento» con lo stesso id e lo stesso punto, oppure la scheda). Le
// unita' di una palazzina prendono la posizione dell'edificio (nessun marker
// doppio). «Collega un immobile esistente»: ricerca fra gli immobili
// dell'agenzia (solo quelli senza edificio) e conferma su questo punto, con
// lo stato della verifica in chiaro.
//
// ZONA-4 - «Inserisci qui» dal GPS (territorio/gps.js): il punto dello
// smartphone entra qui come PROVVISORIO (`placeProvisional`), con la precisione
// nel suggerimento; si corregge e si conferma come ogni altro punto. Nulla di
// automatico: senza «Conferma punto» non succede niente.
//
// I dati del CRM entrano nel DOM solo come `textContent`.

import { sessionEpoch } from '../core/auth.js';
import * as api from '../census/census-api.js';
import {
  buildingPlace, buildingStreet, buildingTitle, catalogMunicipalities, errorMessage, wizardInitialState,
} from '../census/census-model.js';
import { loadFormOptions } from '../components/property-form.js';
import { startCreation } from '../census/census-wizard.js';
import {
  INSERT_ACTIONS, MIN_CONFIRM_ZOOM, STATUS_LABELS, accuracyText, canConfirm, chosenPointText, pointForSave, positionStatus,
  propertyTitle, propertyWhere, wizardFieldsFromPlace,
} from '../territorio/territorio-model.js';

// ---------------------------------------------------------------------------
// Il punto scelto su Zona (in memoria, per la sessione)
// ---------------------------------------------------------------------------

let scelto = null;   // { lng, lat, luogo, epoch }

/** Memorizza il punto confermato su Zona (`luogo` = risultato della ricerca, se c'e'). */
export function setChosenPoint(punto, luogo = null) {
  if (!punto || !Number.isFinite(Number(punto.lat)) || !Number.isFinite(Number(punto.lng))) return;
  scelto = { lng: Number(punto.lng), lat: Number(punto.lat), luogo: luogo || null, epoch: sessionEpoch() };
}

/** Il punto scelto nella sessione corrente, o `null` (altra sessione = nessun punto). */
export function chosenPoint() {
  if (!scelto || scelto.epoch !== sessionEpoch()) {
    scelto = null;
    return null;
  }
  return { lng: scelto.lng, lat: scelto.lat, luogo: scelto.luogo };
}

export function clearChosenPoint() {
  scelto = null;
}

// ---------------------------------------------------------------------------
// «Cerca indirizzo» (T5-bis), condiviso con la pagina di posizionamento
// ---------------------------------------------------------------------------

/** Lo scheletro (statico) del modulo di ricerca. `fine` = come si usa il risultato. */
export function geocodeSearchHtml(fine = 'La posizione si salva con «Conferma posizione».') {
  return `
      <form class="territorio-search card" data-geocode-form novalidate>
        <label class="territorio-search-label" for="geocode-q">Cerca indirizzo</label>
        <div class="territorio-search-row">
          <input id="geocode-q" class="input" type="search" autocomplete="off" autocapitalize="sentences" enterkeyhint="search" placeholder="Es. Via Roma 25, Giulianova" maxlength="200" data-geocode-input>
          <button type="submit" class="btn primary" data-geocode-submit>Cerca</button>
        </div>
        <p class="muted territorio-search-note">L’indirizzo che cerchi viene inviato al servizio esterno OpenStreetMap (Nominatim) per trovare il punto sulla mappa. Il risultato è solo un suggerimento: ${fine}</p>
        <p class="territorio-search-status" data-geocode-status role="status" aria-live="polite" hidden></p>
        <ul class="territorio-search-results" data-geocode-results hidden></ul>
        <p class="muted territorio-search-attrib" data-geocode-attrib hidden>Risultati © OpenStreetMap contributors</p>
      </form>`;
}

/**
 * La ricerca SOLO dal pulsante (nessuna ricerca mentre si scrive): il testo va a
 * GET /api/property/geocode; fino a 5 risultati toccabili, `onPick(risultato)`
 * su quello scelto. `puoCercare()` = false blocca (es. salvataggio in corso).
 */
export function mountGeocodeSearch(form, { vivo = () => true, puoCercare = () => true, onPick } = {}) {
  const campo = form.querySelector('[data-geocode-input]');
  const invio = form.querySelector('[data-geocode-submit]');
  const statoEl = form.querySelector('[data-geocode-status]');
  const elenco = form.querySelector('[data-geocode-results]');
  const attrib = form.querySelector('[data-geocode-attrib]');
  let inCorso = false;
  const dici = (testo, { errore = false } = {}) => {
    statoEl.hidden = !testo;
    statoEl.classList.toggle('is-error', errore);
    statoEl.textContent = testo || '';
  };
  const mostra = (risultati) => {
    elenco.replaceChildren();
    if (!risultati.length) {
      elenco.hidden = true;
      attrib.hidden = true;
      dici('Nessun risultato: prova con via e comune, oppure tocca il punto sulla mappa.');
      return;
    }
    for (const r of risultati) {
      const voce = document.createElement('li');
      const bottone = document.createElement('button');
      bottone.type = 'button';
      bottone.className = 'territorio-search-pick';
      bottone.dataset.geocodePick = r.precision;
      const titolo = document.createElement('span');
      titolo.className = 'territorio-search-pick-title';
      titolo.textContent = r.label;
      const sotto = document.createElement('span');
      sotto.className = 'muted territorio-search-pick-sub';
      sotto.textContent = r.precision === 'house' && r.house_number_matches !== false
        ? 'Civico trovato'
        : (r.precision === 'house' ? `Civico ${r.house_number}: diverso da quello cercato` : (r.precision === 'street' ? 'Civico non trovato: punto sulla via' : 'Solo la zona'));
      bottone.append(titolo, sotto);
      bottone.addEventListener('click', () => {
        const lat = Number(r.latitude);
        const lng = Number(r.longitude);
        if (!Number.isFinite(lat) || !Number.isFinite(lng)) return;
        for (const b of elenco.querySelectorAll('[data-geocode-pick]')) b.classList.toggle('is-picked', b === bottone);
        onPick({ ...r, lat, lng, civicoOk: r.precision === 'house' && r.house_number_matches !== false });
      });
      voce.appendChild(bottone);
      elenco.appendChild(voce);
    }
    elenco.hidden = false;
    attrib.hidden = false;
    dici(risultati.length === 1 ? '1 risultato: toccalo per centrare la mappa.' : `${risultati.length} risultati: tocca quello giusto per centrare la mappa.`);
  };
  form.addEventListener('submit', async (event) => {
    if (event && typeof event.preventDefault === 'function') event.preventDefault();
    if (inCorso || !puoCercare()) return;
    const testo = String(campo.value || '').trim();
    if (testo.length < 3) {
      dici('Scrivi almeno via e comune.', { errore: true });
      return;
    }
    inCorso = true;
    invio.disabled = true;
    elenco.hidden = true;
    attrib.hidden = true;
    dici('Ricerca in corso…');
    let esito;
    try {
      esito = await api.geocode(testo);
    } catch (errore) {
      if (!vivo()) return;
      inCorso = false;
      invio.disabled = false;
      dici(`Ricerca non riuscita: ${errorMessage(errore)} Puoi comunque toccare il punto sulla mappa.`, { errore: true });
      return;
    }
    if (!vivo()) return;
    inCorso = false;
    invio.disabled = false;
    mostra(Array.isArray(esito && esito.results) ? esito.results : []);
  });
}

// ---------------------------------------------------------------------------
// Il pannello «Inserisci dalla mappa» di Zona
// ---------------------------------------------------------------------------

const TESTI = {
  tocca: 'Tocca il punto esatto sulla mappa (oppure cerca un indirizzo).',
  correggi: 'Puoi toccare un altro punto o trascinare il segnaposto per correggerlo.',
  zoom: `Ingrandisci ancora la mappa per confermare con precisione (zoom ${MIN_CONFIRM_ZOOM} o più).`,
  pronto: 'Controlla il punto e premi «Conferma punto».',
  civicoNo: 'Civico non trovato: il punto è sulla via, non sull’edificio. Spostalo sul punto esatto.',
  risultato: 'Punto suggerito dalla ricerca: controllalo e correggilo se serve.',
  attesa: 'Punto scelto in attesa: scegli cosa inserire, oppure cambialo o annullalo.',
  indipendenti: 'Ville, case indipendenti e locali autonomi (senza edificio): al salvataggio il punto scelto diventa la loro posizione verificata. Le unità di una palazzina prendono la posizione dell’edificio, senza un marker proprio.',
  salvatoNonCollegato: 'Immobile salvato, ma posizione non collegata',
};

function pin(classe, etichetta) {
  const el = document.createElement('div');
  el.className = `territorio-pin ${classe}`;
  el.setAttribute('aria-label', etichetta);
  el.setAttribute('role', 'img');
  return el;
}

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

const STATO_POSIZIONE = {
  verified: 'Posizione verificata: viene sostituita da questo punto',
  unverified: 'Coordinate da verificare: questo punto le sostituisce e le verifica',
  invalid: 'Coordinate non valide: questo punto le sostituisce',
  missing: 'Senza posizione',
};

/**
 * Monta il pannello dentro `host` (una `<details>` della pagina), sulla mappa
 * viva `map`. `onLinked(id)` = la posizione di un edificio e' stata verificata:
 * la pagina riapre la mappa con quell'edificio evidenziato.
 */
export function mountInserimento(host, { map, ml, vivo = () => true, onLinked = () => {}, onPropertyLinked = () => {} } = {}) {
  host.innerHTML = `
      <summary class="territorio-insert-summary">+ Inserisci dalla mappa</summary>
      <div class="territorio-insert-body">
        <p class="muted territorio-insert-intro">Cerca un indirizzo o tocca il punto sulla mappa, poi scegli che cosa inserire: la posizione resta collegata all’edificio quando lo salvi.</p>
        ${geocodeSearchHtml('il punto si usa solo quando salvi o colleghi un edificio.')}
        <div class="territorio-insert-pick">
          <button type="button" class="btn ghost" data-insert-pick aria-pressed="false">📍 Scegli il punto toccando la mappa</button>
        </div>
        <div class="territorio-place-bar territorio-insert-point" data-insert-point hidden>
          <p class="territorio-insert-coords" data-insert-point-text></p>
          <p class="territorio-place-hint" data-insert-hint role="status" aria-live="polite"></p>
          <div class="territorio-place-actions">
            <button type="button" class="btn ghost" data-insert-cancel>Annulla</button>
            <button type="button" class="btn primary" data-insert-confirm disabled>Conferma punto</button>
          </div>
        </div>
        <div class="territorio-insert-actions" data-insert-actions hidden>
          <p class="territorio-insert-coords" data-insert-chosen></p>
          <p class="muted" data-insert-chosen-note></p>
          <div class="census-new-cards territorio-insert-cards" data-insert-cards></div>
          <p class="muted territorio-insert-independent" data-insert-independent-note></p>
          <div class="territorio-place-actions">
            <button type="button" class="btn ghost" data-insert-change>Cambia punto</button>
            <button type="button" class="btn ghost" data-insert-cancel>Annulla</button>
          </div>
        </div>
        <form class="territorio-insert-link" data-insert-link hidden novalidate>
          <label class="territorio-search-label" for="insert-link-q" data-insert-link-label>Quale edificio?</label>
          <div class="territorio-search-row">
            <input id="insert-link-q" class="input" type="search" autocomplete="off" placeholder="Cerca per via, civico o nome" maxlength="120" data-insert-link-input>
            <button type="submit" class="btn primary" data-insert-link-submit>Cerca</button>
          </div>
          <p class="territorio-search-status" data-insert-link-status role="status" aria-live="polite" hidden></p>
          <p class="territorio-place-error" data-insert-link-error role="alert" hidden></p>
          <ul class="census-list territorio-insert-candidates" data-insert-link-results hidden></ul>
        </form>
        <dialog class="modal census-sheet" data-insert-sheet></dialog>
      </div>`;
  const q = (s) => host.querySelector(s);
  const puntoEl = q('[data-insert-point]');
  const puntoTesto = q('[data-insert-point-text]');
  const suggerimento = q('[data-insert-hint]');
  const conferma = q('[data-insert-confirm]');
  const sceltaBtn = q('[data-insert-pick]');
  const azioniEl = q('[data-insert-actions]');
  const sceltoTesto = q('[data-insert-chosen]');
  const sceltoNota = q('[data-insert-chosen-note]');
  const carte = q('[data-insert-cards]');
  const notaIndipendenti = q('[data-insert-independent-note]');
  const linkForm = q('[data-insert-link]');
  const sheet = q('[data-insert-sheet]');
  notaIndipendenti.textContent = TESTI.indipendenti;

  let punto = null;          // { lng, lat } provvisorio (non confermato)
  let luogo = null;          // il risultato della ricerca da cui viene, se c'e'
  let notaRicerca = null;
  let provvisorio = null;    // il segnaposto arancione
  let modoTocco = false;
  let confermato = false;

  const trascinabile = (si) => {
    if (provvisorio && typeof provvisorio.setDraggable === 'function') provvisorio.setDraggable(si);
  };
  const impostaTocco = (si) => {
    modoTocco = si;
    sceltaBtn.setAttribute('aria-pressed', si ? 'true' : 'false');
    sceltaBtn.classList.toggle('active', si);
    try { map.getCanvas().style.cursor = si ? 'crosshair' : ''; } catch (_e) { /* mappa finta */ }
  };
  const aggiorna = () => {
    if (confermato) return;
    const zoom = map.getZoom();
    conferma.disabled = !canConfirm({ point: punto, zoom });
    puntoTesto.textContent = punto ? `Punto provvisorio: ${chosenPointText(punto)}` : '';
    if (!punto) suggerimento.textContent = TESTI.tocca;
    else if (zoom < MIN_CONFIRM_ZOOM) suggerimento.textContent = `${notaRicerca ? `${notaRicerca} ` : ''}${TESTI.zoom}`;
    else suggerimento.textContent = `${notaRicerca ? `${notaRicerca} ` : ''}${TESTI.pronto} ${TESTI.correggi}`;
  };
  const mostraPunto = () => {
    confermato = false;
    puntoEl.hidden = !punto;
    azioniEl.hidden = true;
    linkForm.hidden = true;
    trascinabile(true);
    aggiorna();
  };
  const scegli = (lngLat, { nota = null, da = null } = {}) => {
    if (!lngLat || confermato) return;
    punto = { lng: Number(lngLat.lng), lat: Number(lngLat.lat) };
    luogo = da;
    notaRicerca = nota;
    if (!provvisorio) {
      provvisorio = new ml.Marker({ element: pin('is-provisional', 'Punto provvisorio'), draggable: true })
        .setLngLat([punto.lng, punto.lat])
        .addTo(map);
      provvisorio.on('dragend', () => {
        if (confermato) return;
        const p = provvisorio.getLngLat();
        punto = { lng: p.lng, lat: p.lat };
        notaRicerca = null;
        luogo = null;
        aggiorna();
      });
    } else {
      provvisorio.setLngLat([punto.lng, punto.lat]);
    }
    host.open = true;
    mostraPunto();
  };
  const azzera = () => {
    if (provvisorio) provvisorio.remove();
    provvisorio = null;
    punto = null;
    luogo = null;
    notaRicerca = null;
    confermato = false;
    clearChosenPoint();
    impostaTocco(false);
    puntoEl.hidden = true;
    azioniEl.hidden = true;
    linkForm.hidden = true;
    aggiorna();
  };

  // --- il punto confermato e le azioni ------------------------------------------------
  const mostraAzioni = (nota) => {
    confermato = true;
    trascinabile(false);
    impostaTocco(false);
    puntoEl.hidden = true;
    linkForm.hidden = true;
    azioniEl.hidden = false;
    host.open = true;
    sceltoTesto.textContent = `Punto scelto: ${chosenPointText(punto)}`;
    sceltoNota.textContent = nota;
    carte.replaceChildren();
    for (const a of INSERT_ACTIONS) {
      const b = el('button', { cls: 'census-card', attrs: { type: 'button', 'data-insert-action': a.key } });
      b.appendChild(el('strong', { text: a.label }));
      b.appendChild(el('span', { cls: 'muted', text: a.text }));
      b.addEventListener('click', () => azione(a.key));
      carte.appendChild(b);
    }
  };
  conferma.addEventListener('click', () => {
    if (!canConfirm({ point: punto, zoom: map.getZoom() })) { aggiorna(); return; }
    setChosenPoint(punto, luogo);
    mostraAzioni(luogo && luogo.label ? `Dalla ricerca: ${luogo.label}` : 'Scelto sulla mappa.');
  });
  q('[data-insert-change]').addEventListener('click', () => {
    clearChosenPoint();
    mostraPunto();
  });
  for (const b of host.querySelectorAll('[data-insert-cancel]')) b.addEventListener('click', azzera);
  sceltaBtn.addEventListener('click', () => { impostaTocco(!modoTocco); host.open = true; });
  map.on('click', (e) => { if (modoTocco) scegli(e && e.lngLat); });
  map.on('zoomend', aggiorna);

  mountGeocodeSearch(q('[data-geocode-form]'), {
    vivo,
    puoCercare: () => !confermato,
    onPick: (r) => {
      map.jumpTo({ center: [r.lng, r.lat], zoom: r.civicoOk ? 18 : 16 });
      scegli({ lng: r.lng, lat: r.lat }, { nota: r.civicoOk ? TESTI.risultato : TESTI.civicoNo, da: r });
    },
  });

  // --- la conferma T4 della posizione su un edificio ------------------------------------
  // Riuscita SOLO se il server dice «verified» per QUELL'edificio. Nessuna scrittura
  // qui oltre a questa; un errore si legge e si riprova con lo stesso punto.
  async function collega(buildingId) {
    const p = chosenPoint();
    const corpo = p ? pointForSave(p.lng, p.lat) : null;
    if (!corpo) throw Object.assign(new Error('Nessun punto scelto su Zona.'), { code: 'NO_POINT' });
    const esito = await api.confirmBuildingPosition(String(buildingId), corpo);
    if (!esito || String(esito.building_id) !== String(buildingId) || esito.status !== 'verified') {
      throw Object.assign(new Error('risposta inattesa del server'), { code: 'UNEXPECTED' });
    }
    return esito;
  }
  const testoErrore = (errore) => (errore && errore.code === 'NO_POINT'
    ? 'Nessun punto scelto su Zona: scegline uno e riprova.'
    : (errore && errore.code === 'BUILDING_IN_TRASH' ? 'L’edificio è nel Cestino: non si può posizionare.'
      : (errore && errore.code === 'PROPERTY_IN_TRASH' ? 'L’immobile è nel Cestino: non si può posizionare.'
        : `${errorMessage(errore)}`)));
  const vaiAllaMappa = (id) => {
    azzera();
    onLinked(Number(id));
  };

  // ZONA-3: la conferma della posizione di un IMMOBILE INDIPENDENTE (stessa regola: riuscita
  // SOLO se il server dice «verified» per QUELL'immobile)
  async function collegaImmobile(propertyId) {
    const p = chosenPoint();
    const corpo = p ? pointForSave(p.lng, p.lat) : null;
    if (!corpo) throw Object.assign(new Error('Nessun punto scelto su Zona.'), { code: 'NO_POINT' });
    const esito = await api.confirmPropertyPosition(String(propertyId), corpo);
    if (!esito || String(esito.property_id) !== String(propertyId) || esito.status !== 'verified') {
      throw Object.assign(new Error('risposta inattesa del server'), { code: 'UNEXPECTED' });
    }
    return esito;
  }
  const vaiAllaMappaImmobile = (id) => {
    azzera();
    onPropertyLinked(Number(id));
  };

  /** ZONA-3: dopo il salvataggio di un'unita' dal percorso «Unita' autonoma» (gancio del wizard).
   *  Immobile senza edificio -> collega il punto; con edificio -> la scheda come prima. */
  async function unitaSalvata(unita) {
    if (!unita || unita.building_id !== null && unita.building_id !== undefined) return false;
    try {
      await collegaImmobile(unita.id);
    } catch (errore) {
      if (!vivo()) return true;
      mostraRecuperoImmobile(unita, errore);
      return true;
    }
    if (!vivo()) return true;
    vaiAllaMappaImmobile(unita.id);
    return true;
  }

  /** «Immobile salvato, ma posizione non collegata»: stesso id, stesso punto, nessun doppione. */
  function mostraRecuperoImmobile(unita, errore) {
    sheet.className = 'modal census-sheet';
    sheet.replaceChildren();
    const box = el('div', { attrs: { 'data-unit-recovery': String(unita.id) } });
    box.appendChild(el('h2', { cls: 'census-sheet-title', text: TESTI.salvatoNonCollegato }));
    const banner = el('div', { cls: 'census-banner census-banner-block' });
    banner.appendChild(el('strong', { text: `${propertyTitle(unita)}${unita.code ? ` (${unita.code})` : ''} è salvato` }));
    banner.appendChild(el('div', { cls: 'territorio-recovery-error', text: testoErrore(errore), attrs: { 'data-unit-recovery-error': '1' } }));
    banner.appendChild(el('div', { text: 'L’immobile non viene duplicato: riprova il collegamento con lo stesso punto, oppure apri la scheda (il punto resta qui su Zona).' }));
    box.appendChild(banner);
    const azioni = el('div', { cls: 'modal-actions' });
    const chiudi = el('button', { cls: 'btn ghost', text: 'Chiudi', attrs: { type: 'button', 'data-unit-recovery-close': '1' } });
    chiudi.addEventListener('click', () => sheet.close());
    const scheda = el('a', { cls: 'btn ghost', text: 'Apri la scheda immobile',
      attrs: { href: `#/immobili/${encodeURIComponent(String(unita.id))}`, 'data-unit-recovery-open': String(unita.id) } });
    scheda.addEventListener('click', () => sheet.close());
    const riprova = el('button', { cls: 'btn primary', text: 'Riprova collegamento', attrs: { type: 'button', 'data-unit-recovery-retry': String(unita.id) } });
    riprova.addEventListener('click', async () => {
      riprova.disabled = true;
      riprova.textContent = 'Collegamento…';
      try {
        await collegaImmobile(unita.id);
      } catch (e) {
        if (!vivo()) return;
        riprova.disabled = false;
        riprova.textContent = 'Riprova collegamento';
        banner.querySelector('[data-unit-recovery-error]').textContent = testoErrore(e);
        return;
      }
      if (!vivo()) return;
      sheet.close();
      vaiAllaMappaImmobile(unita.id);
    });
    azioni.append(chiudi, scheda, riprova);
    box.appendChild(azioni);
    sheet.appendChild(box);
    if (!(sheet.open || sheet._open)) sheet.showModal();
  }

  // --- «Nuovo edificio» / «Nuovo immobile»: la procedura guidata esistente -----------------
  let opzioniPromessa = null;
  const leggiOpzioni = () => {
    if (!opzioniPromessa) opzioniPromessa = loadFormOptions().catch((e) => { opzioniPromessa = null; throw e; });
    return opzioniPromessa;
  };

  /** Il nodo che la procedura mostra nel passo «Edificio pronto» (gancio `onBuildingReady`). */
  function pannelloPronto(edificio, { created }) {
    const box = el('div', { cls: 'census-banner territorio-ready', attrs: { 'data-ready-building': String(edificio.id) } });
    let stato = created ? 'saving' : 'offer';   // saving | ok | failed | offer
    let errore = null;
    const disegna = () => {
      box.replaceChildren();
      box.classList.toggle('census-banner-block', stato === 'failed');
      box.dataset.readyState = stato;
      if (stato === 'saving') {
        box.appendChild(el('strong', { text: 'Collegamento della posizione scelta su Zona…' }));
        return;
      }
      if (stato === 'ok') {
        box.appendChild(el('strong', { text: 'Posizione verificata sulla mappa' }));
        box.appendChild(el('div', { text: `Il punto scelto su Zona (${chosenPointText(punto)}) è ora la posizione verificata di questo edificio.` }));
        const vedi = el('button', { cls: 'btn primary btn-small', text: 'Vedi sulla mappa', attrs: { type: 'button', 'data-ready-map': String(edificio.id) } });
        vedi.addEventListener('click', () => { if (sheet.open || sheet._open) sheet.close(); vaiAllaMappa(edificio.id); });
        const barra = el('div', { cls: 'action-bar' });
        barra.appendChild(vedi);
        box.appendChild(barra);
        return;
      }
      if (stato === 'failed') {
        box.appendChild(el('strong', { text: 'Edificio salvato, ma posizione NON collegata' }));
        box.appendChild(el('div', { text: `${testoErrore(errore)} L’edificio non viene duplicato: riprova con lo stesso punto, oppure posizionalo dalla sua pagina (riparte da questo punto).` }));
        const barra = el('div', { cls: 'action-bar' });
        const riprova = el('button', { cls: 'btn btn-small', text: 'Riprova il collegamento', attrs: { type: 'button', 'data-ready-retry': String(edificio.id) } });
        riprova.addEventListener('click', () => avvia());
        barra.appendChild(riprova);
        barra.appendChild(el('a', { cls: 'btn ghost btn-small', text: 'Posiziona dalla pagina dell’edificio',
          attrs: { href: `#/edifici/mappa/posiziona/${encodeURIComponent(String(edificio.id))}`, 'data-ready-place': String(edificio.id) } }));
        box.appendChild(barra);
        return;
      }
      // offer: edificio esistente, collegare e' una scelta esplicita
      box.appendChild(el('strong', { text: 'Collegare la posizione scelta su Zona a questo edificio?' }));
      box.appendChild(el('div', { text: `${STATO_POSIZIONE[positionStatus(edificio)]}. Nulla cambia finché non confermi.` }));
      const lega = el('button', { cls: 'btn primary btn-small', text: 'Collega la posizione', attrs: { type: 'button', 'data-ready-link': String(edificio.id) } });
      lega.addEventListener('click', () => avvia());
      const barra = el('div', { cls: 'action-bar' });
      barra.appendChild(lega);
      box.appendChild(barra);
    };
    async function avvia() {
      stato = 'saving';
      errore = null;
      disegna();
      try {
        await collega(edificio.id);
        stato = 'ok';
      } catch (e) {
        stato = 'failed';
        errore = e;
      }
      disegna();
    }
    disegna();
    if (created) return avvia().then(() => box);
    return box;
  }

  let linkTipo = 'building';       // ZONA-3: 'building' | 'property' (che cosa cerca «Collega…»)
  async function azione(chiave) {
    if (!chosenPoint()) { mostraPunto(); return; }
    if (chiave === 'link' || chiave === 'link-property') {
      linkTipo = chiave === 'link' ? 'building' : 'property';
      linkForm.dataset.insertLinkKind = linkTipo;
      q('[data-insert-link-label]').textContent = linkTipo === 'building' ? 'Quale edificio?' : 'Quale immobile indipendente?';
      q('[data-insert-link-input]').setAttribute('placeholder', linkTipo === 'building' ? 'Cerca per via, civico o nome' : 'Cerca per titolo, codice, via o comune');
      linkElenco.replaceChildren();
      linkElenco.hidden = true;
      linkStato.hidden = true;
      linkErrore.hidden = true;
      linkForm.hidden = false;
      q('[data-insert-link-input]').focus && q('[data-insert-link-input]').focus();
      return;
    }
    let opzioni;
    try {
      opzioni = await leggiOpzioni();
    } catch (errore) {
      if (!vivo()) return;
      sceltoNota.textContent = `Impossibile aprire la procedura: ${errorMessage(errore)}`;
      return;
    }
    if (!vivo()) return;
    const comuni = catalogMunicipalities(opzioni.territory || []);
    const state = { ...wizardInitialState(), ...wizardFieldsFromPlace(luogo, comuni), path: chiave === 'building' ? 'building' : '' };
    startCreation({
      wizardDialog: sheet, options: opzioni, recordKind: 'census', state,
      onBuildingReady: (edificio, info) => pannelloPronto(edificio, info),
      onUnitSaved: (unita) => unitaSalvata(unita),
    });
  }

  // --- «Collega un edificio esistente» ----------------------------------------------------
  const linkStato = q('[data-insert-link-status]');
  const linkErrore = q('[data-insert-link-error]');
  const linkElenco = q('[data-insert-link-results]');
  const linkInvio = q('[data-insert-link-submit]');
  let giro = 0;
  let collegamento = false;
  linkForm.addEventListener('submit', async (event) => {
    if (event && typeof event.preventDefault === 'function') event.preventDefault();
    const testo = String(q('[data-insert-link-input]').value || '').trim();
    const mio = ++giro;
    linkInvio.disabled = true;
    linkErrore.hidden = true;
    linkElenco.hidden = true;
    linkStato.hidden = false;
    linkStato.classList.remove('is-error');
    const immobili = linkTipo === 'property';
    linkStato.textContent = immobili ? 'Ricerca fra gli immobili…' : 'Ricerca fra gli edifici…';
    let corpo;
    try {
      corpo = immobili
        ? await api.searchProperties(testo, 20)
        : await api.listBuildings({ search: testo || undefined, sort: 'address', limit: 10 });
    } catch (errore) {
      if (!vivo() || mio !== giro) return;
      linkInvio.disabled = false;
      linkStato.classList.add('is-error');
      linkStato.textContent = `Ricerca non riuscita: ${errorMessage(errore)}`;
      return;
    }
    if (!vivo() || mio !== giro) return;
    linkInvio.disabled = false;
    const trovati = Array.isArray(corpo && corpo.items) ? corpo.items : [];
    linkElenco.replaceChildren();
    if (immobili) {
      mostraImmobili(trovati);
      return;
    }
    const voci = trovati;
    if (!voci.length) {
      linkStato.textContent = 'Nessun edificio trovato: prova con via, civico o nome, oppure crealo con «Nuovo edificio».';
      return;
    }
    linkStato.textContent = voci.length === 1 ? '1 edificio trovato.' : `${voci.length} edifici trovati: «Collega qui» ne verifica la posizione su questo punto.`;
    for (const b of voci) {
      const voce = el('li', { cls: 'census-list-item', attrs: { 'data-insert-candidate': String(b.id) } });
      const testoVoce = el('div', { cls: 'census-candidate-text' });
      testoVoce.appendChild(el('strong', { text: buildingTitle(b) }));
      testoVoce.appendChild(el('small', { cls: 'muted', text: [b.name ? buildingStreet(b) : '', buildingPlace(b)].filter(Boolean).join(' · ') }));
      const s = positionStatus(b);
      testoVoce.appendChild(el('small', { cls: 'muted', text: s === 'missing' ? STATUS_LABELS.missing : STATO_POSIZIONE[s] }));
      voce.appendChild(testoVoce);
      const lega = el('button', { cls: 'btn primary btn-small', text: 'Collega qui', attrs: { type: 'button', 'data-insert-link-building': String(b.id) } });
      lega.addEventListener('click', async () => {
        if (collegamento) return;
        collegamento = true;
        lega.disabled = true;
        lega.textContent = 'Collegamento…';
        linkErrore.hidden = true;
        try {
          await collega(b.id);
        } catch (errore) {
          if (!vivo()) return;
          collegamento = false;
          lega.disabled = false;
          lega.textContent = 'Collega qui';
          linkErrore.hidden = false;
          linkErrore.textContent = `Posizione non collegata. ${testoErrore(errore)} Riprova.`;
          return;
        }
        if (!vivo()) return;
        collegamento = false;
        vaiAllaMappa(b.id);
      });
      voce.appendChild(lega);
      linkElenco.appendChild(voce);
    }
    linkElenco.hidden = false;
  });

  // ZONA-3: gli immobili trovati: SOLO quelli senza edificio si collegano qui (le unita' di
  // palazzina si posizionano con l'edificio); lo stato della verifica in chiaro.
  function mostraImmobili(trovati) {
    const indipendenti = trovati.filter((p) => p && (p.building_id === null || p.building_id === undefined));
    const inPalazzina = trovati.length - indipendenti.length;
    if (!indipendenti.length) {
      linkStato.textContent = inPalazzina
        ? `Trovate solo unità di palazzina (${inPalazzina}): la loro posizione è quella dell’edificio («Collega un edificio esistente»).`
        : 'Nessun immobile trovato: prova con titolo, codice, via o comune, oppure crealo con «Nuovo immobile».';
      return;
    }
    linkStato.textContent = `${indipendenti.length === 1 ? '1 immobile indipendente trovato' : `${indipendenti.length} immobili indipendenti trovati`}`
      + (inPalazzina ? ` (${inPalazzina} unità di palazzina escluse: la posizione è dell’edificio)` : '')
      + ': «Collega qui» ne verifica la posizione su questo punto.';
    for (const p of indipendenti) {
      const voce = el('li', { cls: 'census-list-item', attrs: { 'data-insert-candidate-property': String(p.id) } });
      const testoVoce = el('div', { cls: 'census-candidate-text' });
      testoVoce.appendChild(el('strong', { text: propertyTitle(p) }));
      testoVoce.appendChild(el('small', { cls: 'muted', text: [p.code, propertyWhere(p)].filter(Boolean).join(' · ') }));
      const s = positionStatus(p);
      const statoEl = el('small', { cls: s === 'verified' ? 'territorio-verified-note' : 'muted',
        text: s === 'missing' ? STATUS_LABELS.missing : STATO_POSIZIONE[s], attrs: { 'data-property-position-status': s } });
      testoVoce.appendChild(statoEl);
      voce.appendChild(testoVoce);
      const lega = el('button', { cls: 'btn primary btn-small', text: 'Collega qui', attrs: { type: 'button', 'data-insert-link-property': String(p.id) } });
      lega.addEventListener('click', async () => {
        if (collegamento) return;
        collegamento = true;
        lega.disabled = true;
        lega.textContent = 'Collegamento…';
        linkErrore.hidden = true;
        try {
          await collegaImmobile(p.id);
        } catch (errore) {
          if (!vivo()) return;
          collegamento = false;
          lega.disabled = false;
          lega.textContent = 'Collega qui';
          linkErrore.hidden = false;
          linkErrore.textContent = `Posizione non collegata. ${testoErrore(errore)} Riprova.`;
          return;
        }
        if (!vivo()) return;
        collegamento = false;
        vaiAllaMappaImmobile(p.id);
      });
      voce.appendChild(lega);
      linkElenco.appendChild(voce);
    }
    linkElenco.hidden = false;
  }

  // --- un punto scelto prima (si torna da un modulo): si riparte da li' --------------------
  const prima = chosenPoint();
  if (prima) {
    scegli({ lng: prima.lng, lat: prima.lat }, { da: prima.luogo });
    setChosenPoint(punto, luogo);
    mostraAzioni(TESTI.attesa);
    map.jumpTo({ center: [prima.lng, prima.lat], zoom: 18 });
  } else {
    aggiorna();
  }

  // ZONA-4: il punto dal GPS diventa il provvisorio (anche al posto di uno gia' confermato:
  // il confermato si scarta, nulla era stato salvato). Poi correzione e conferma come sempre.
  function placeProvisional(p) {
    if (!p || !Number.isFinite(Number(p.lng)) || !Number.isFinite(Number(p.lat))) return;
    if (confermato) {
      clearChosenPoint();
      confermato = false;
    }
    host.open = true;
    scegli({ lng: Number(p.lng), lat: Number(p.lat) }, { nota: `Punto dal GPS (${accuracyText(p.accuracy).toLowerCase()}): controllalo e correggilo se serve.` });
    if (typeof host.scrollIntoView === 'function') host.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
  return { placeProvisional };
}
