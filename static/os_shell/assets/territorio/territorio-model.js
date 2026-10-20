// STIMA360 OS — territorio/territorio-model.js (STIMA360 TERRITORIO T3)
//
// Funzioni PURE della mappa: nessun DOM, nessuna rete. Decidono quali edifici
// possono comparire come marker e come vanno contati gli altri.
//
// LA REGOLA DI VERIFICA DELLA POSIZIONE (la sola, in un posto solo)
//
// Ad oggi `buildings` ha `latitude`/`longitude` ma NESSUN campo che dica da
// dove vengono o chi le ha controllate: le accetta qualunque client della PATCH,
// senza provenienza. `census_status` ('verified' | 'partial' | 'estimated') parla
// del CENSIMENTO delle unita', non della posizione, e non si usa qui.
// Quindi una coordinata presente NON e' una posizione verificata.
//
// Un edificio e' «posizione verificata» solo se il server restituisce una prova
// esplicita: `position_verified_at` valorizzato con una data valida. Oggi il
// server non lo restituisce mai (nessuna colonna, nessuna scrittura): in dati
// reali nessun edificio risulta verificato e nessun marker compare, finche' T4
// non introduce il meccanismo di verifica. Il nome del campo e' il contratto
// proposto a T4; se T4 ne sceglie un altro cambia solo `isPositionVerified`.

export const POSITION_STATUSES = ['verified', 'unverified', 'invalid', 'missing'];

export const STATUS_LABELS = {
  verified: 'Posizione verificata',
  unverified: 'Coordinate non verificate',
  invalid: 'Coordinate non valide',
  missing: 'Senza coordinate',
};

function vuoto(v) {
  return v === null || v === undefined || (typeof v === 'string' && v.trim() === '');
}

/** Un numero decimale (numero o stringa decimale semplice, come arriva dal JSON di
 *  un NUMERIC). Tutto il resto - booleani, esadecimali, `1e3`, spazi interni - e' NaN. */
function decimale(v) {
  if (typeof v === 'number') return Number.isFinite(v) ? v : NaN;
  if (typeof v !== 'string') return NaN;
  const t = v.trim();
  return /^[-+]?\d{1,3}(\.\d+)?$/.test(t) ? Number(t) : NaN;
}

/**
 * Le coordinate di un edificio, validate. `null` = mancanti (entrambe vuote);
 * `{ valid: false }` = presenti ma inutilizzabili (una sola, non numeriche,
 * fuori intervallo, 0,0); `{ valid: true, lng, lat }` altrimenti.
 */
export function parseCoordinates(b) {
  const la = b ? b.latitude : null;
  const lo = b ? b.longitude : null;
  if (vuoto(la) && vuoto(lo)) return null;
  const lat = decimale(la);
  const lng = decimale(lo);
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return { valid: false };
  if (lat < -90 || lat > 90 || lng < -180 || lng > 180) return { valid: false };
  if (lat === 0 && lng === 0) return { valid: false };
  return { valid: true, lng, lat };
}

/** La prova di verifica. Vedi la regola in testa al file. */
export function isPositionVerified(b) {
  const quando = b ? b.position_verified_at : null;
  if (typeof quando !== 'string' || quando.trim() === '') return false;
  return Number.isFinite(Date.parse(quando));
}

export function positionStatus(b) {
  const c = parseCoordinates(b);
  if (c === null) return 'missing';
  if (!c.valid) return 'invalid';
  return isPositionVerified(b) ? 'verified' : 'unverified';
}

function idValido(b) {
  return b && (Number.isInteger(b.id) || (typeof b.id === 'string' && /^\d+$/.test(b.id)));
}

/**
 * Gli edifici divisi per stato della posizione, senza doppioni (stesso id = una
 * voce: la paginazione puo' ripetere una riga se nel frattempo l'ordine cambia).
 * `verified` porta anche `lng`/`lat` gia' validati.
 */
export function classifyBuildings(items) {
  const gruppi = { verified: [], unverified: [], invalid: [], missing: [] };
  const visti = new Set();
  for (const b of Array.isArray(items) ? items : []) {
    if (!idValido(b) || visti.has(String(b.id))) continue;
    visti.add(String(b.id));
    const stato = positionStatus(b);
    if (stato === 'verified') {
      const { lng, lat } = parseCoordinates(b);
      gruppi.verified.push({ building: b, lng, lat });
    } else {
      gruppi[stato].push({ building: b });
    }
  }
  const totale = visti.size;
  return { ...gruppi, total: totale, toPlace: totale - gruppi.verified.length };
}

/** GeoJSON dei SOLI edifici verificati (id numerico: serve allo stato «attivo»). */
export function verifiedFeatureCollection(verified) {
  return {
    type: 'FeatureCollection',
    features: verified.map(({ building, lng, lat }) => ({
      type: 'Feature',
      id: Number(building.id),
      properties: { building_id: Number(building.id) },
      geometry: { type: 'Point', coordinates: [lng, lat] },
    })),
  };
}

/** Il riquadro da inquadrare: `null` senza punti, un punto solo = centro + zoom. */
export function viewFor(verified) {
  if (!verified.length) return null;
  if (verified.length === 1) return { center: [verified[0].lng, verified[0].lat], zoom: 16 };
  let [w, s, e, n] = [Infinity, Infinity, -Infinity, -Infinity];
  for (const { lng, lat } of verified) {
    w = Math.min(w, lng); e = Math.max(e, lng); s = Math.min(s, lat); n = Math.max(n, lat);
  }
  return { bounds: [[w, s], [e, n]] };
}

/** Il testo del contatore. */
export function toPlaceText(n) {
  return `Edifici da posizionare: ${n}`;
}

// ---------------------------------------------------------------------------
// STIMA360 TERRITORIO T4 - posizionamento manuale e verifica
//
// `position_verified_at` e `position_verified_by_user_id` li scrive SOLO il
// server, alla conferma (migration 102 + property/building_position.py). Qui si
// decide soltanto cosa mostrare e quando la conferma e' ammessa.
// ---------------------------------------------------------------------------

/** Sotto questo zoom il punto e' troppo impreciso per essere confermato. */
export const MIN_CONFIRM_ZOOM = 17;

/** L'etichetta del comando nella scheda edificio. */
export function positionActionLabel(stato) {
  if (stato === 'verified') return 'Correggi posizione';
  if (stato === 'unverified') return 'Verifica posizione';
  return 'Posiziona sulla mappa';
}

function dataItaliana(iso) {
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return '';
  return new Intl.DateTimeFormat('it-IT', { timeZone: 'Europe/Rome', day: '2-digit', month: '2-digit', year: 'numeric' })
    .format(new Date(t));
}

/** La riga «Posizione» della scheda. `chi` = nome di chi ha verificato, se noto. */
export function positionFactText(b, chi = null) {
  const stato = positionStatus(b);
  if (stato === 'verified') {
    const quando = dataItaliana(b.position_verified_at);
    return `Verificata${quando ? ` il ${quando}` : ''}${chi ? ` da ${chi}` : ''}`;
  }
  if (stato === 'unverified') return 'Da verificare';
  if (stato === 'invalid') return 'Coordinate non valide: da posizionare';
  return 'Non indicata';
}

/** Il punto scelto, nel formato della colonna (NUMERIC(10,7)), o `null` se non valido. */
export function pointForSave(lng, lat) {
  const c = parseCoordinates({ latitude: Number(lat).toFixed(7), longitude: Number(lng).toFixed(7) });
  if (!c || !c.valid) return null;
  return { latitude: Number(lat).toFixed(7), longitude: Number(lng).toFixed(7) };
}

/** La conferma e' ammessa con un punto valido, a uno zoom sufficiente, senza invii in corso. */
export function canConfirm({ point, zoom, saving = false }) {
  return Boolean(point) && !saving && Number.isFinite(zoom) && zoom >= MIN_CONFIRM_ZOOM;
}

// ---------------------------------------------------------------------------
// STIMA360 TERRITORIO ZONA-2 - inserimento geografico integrato
//
// Da Zona si sceglie un punto (ricerca indirizzo T5-bis o tocco) e da li' si
// apre la procedura guidata ESISTENTE (census/census-wizard.js). Qui solo le
// regole pure: cosa precompilare dal risultato della ricerca e come descrivere
// il punto. La posizione si registra SOLO con la conferma T4 dell'edificio.
// ---------------------------------------------------------------------------

const testo = (v) => (v === null || v === undefined ? '' : String(v).trim());

/**
 * I campi del passo 1 della procedura guidata ricavabili da un risultato della
 * ricerca indirizzo (`risultato` = una voce di GET /api/property/geocode) o da
 * nulla (tocco sulla mappa). Il Comune solo se e' nel catalogo territoriale
 * dell'agenzia (`municipalities` = catalogMunicipalities), confrontato senza
 * maiuscole/accenti: altrimenti resta da scegliere. Il civico solo se e'
 * quello cercato (mai un civico diverso spacciato per buono).
 */
export function wizardFieldsFromPlace(risultato, municipalities) {
  const campi = { city: '', address: '', civic_number: '' };
  if (!risultato || typeof risultato !== 'object') return campi;
  const piega = (s) => testo(s).normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
  const comune = (municipalities || []).find((m) => piega(m.name) === piega(risultato.town) && piega(m.name));
  if (comune) campi.city = comune.name;
  campi.address = testo(risultato.road).slice(0, 250);
  if (risultato.precision === 'house' && risultato.house_number_matches === true) {
    campi.civic_number = testo(risultato.house_number).toUpperCase().slice(0, 30);
  }
  return campi;
}

/** Il punto scelto, leggibile (5 decimali: ~1 m), per il pannello e i riepiloghi. */
export function chosenPointText(punto) {
  if (!punto || !Number.isFinite(Number(punto.lat)) || !Number.isFinite(Number(punto.lng))) return '';
  return `${Number(punto.lat).toFixed(5)}, ${Number(punto.lng).toFixed(5)}`;
}

/** Le azioni di Zona dopo la conferma del punto: solo cio' che il CRM sa gia' fare. */
// ZONA-3: anche l'immobile indipendente (villa, locale autonomo) si verifica al salvataggio
// o si collega se gia' in archivio.
export const INSERT_ACTIONS = [
  { key: 'building', label: 'Nuovo edificio', text: 'Palazzina o edificio con più unità: la posizione viene verificata al salvataggio.' },
  { key: 'unit', label: 'Nuovo immobile', text: 'Villa, casa indipendente o locale autonomo: la posizione viene verificata al salvataggio. In una palazzina la posizione va all’edificio.' },
  { key: 'link', label: 'Collega un edificio esistente', text: 'Verifica la posizione di un edificio già in archivio su questo punto.' },
  { key: 'link-property', label: 'Collega un immobile esistente', text: 'Verifica la posizione di una villa o di un locale autonomo già in archivio su questo punto.' },
];

// ---------------------------------------------------------------------------
// STIMA360 ZONA-3 - gli immobili INDIPENDENTI sulla mappa
//
// Stessa regola di verifica degli edifici (`position_verified_at` scritto dal
// server, migration 103): coordinate presenti ma non confermate NON sono un
// marker. Un immobile con edificio (`building_id`) non e' mai un marker
// proprio: la sua posizione e' quella dell'edificio (niente doppioni).
// ---------------------------------------------------------------------------

/** Gli immobili indipendenti verificati, senza doppioni, con `lng`/`lat` validati. */
export function verifiedProperties(items) {
  const esito = [];
  const visti = new Set();
  for (const p of Array.isArray(items) ? items : []) {
    if (!idValido(p) || visti.has(String(p.id))) continue;
    visti.add(String(p.id));
    if (p.building_id !== null && p.building_id !== undefined) continue;   // mai un marker proprio
    if (positionStatus(p) !== 'verified') continue;
    const { lng, lat } = parseCoordinates(p);
    esito.push({ property: p, lng, lat });
  }
  return esito;
}

/** GeoJSON dei SOLI immobili indipendenti verificati (id numerico = immobile). */
export function propertyFeatureCollection(verified) {
  return {
    type: 'FeatureCollection',
    features: verified.map(({ property, lng, lat }) => ({
      type: 'Feature',
      id: Number(property.id),
      properties: { property_id: Number(property.id) },
      geometry: { type: 'Point', coordinates: [lng, lat] },
    })),
  };
}

const testoBreve = (v) => (v === null || v === undefined ? '' : String(v).trim());

/** Il titolo dell'immobile per riquadro e pannello: titolo, altrimenti via
 *  (SEMPLIFICAZIONE-CRM-1: mai il codice tecnico). */
export function propertyTitle(p) {
  const t = testoBreve(p && p.title);
  if (t) return t;
  const via = [testoBreve(p && p.address), testoBreve(p && p.civic_number)].filter(Boolean).join(' ');
  return via || testoBreve(p && p.city) || 'Immobile';
}

/** «Via Roma 25 · Alba Adriatica (Lido)» */
export function propertyWhere(p) {
  const via = [testoBreve(p && p.address), testoBreve(p && p.civic_number)].filter(Boolean).join(' ');
  const comune = testoBreve(p && p.city);
  const zona = testoBreve(p && p.microzone);
  const luogo = comune ? (zona ? `${comune} (${zona})` : comune) : zona;
  return [via, luogo].filter(Boolean).join(' · ');
}

/** Il riquadro da inquadrare per edifici E immobili insieme (stessa regola di `viewFor`). */
export function viewForAll(buildings, properties) {
  return viewFor([...(buildings || []), ...(properties || [])]);
}

// ---------------------------------------------------------------------------
// STIMA360 ZONA-4 - «La mia posizione» (GPS dello smartphone)
//
// Solo regole pure: le opzioni della richiesta (fresca, precisa, con un tempo
// massimo), il testo della precisione, i messaggi d'errore, lo zoom adatto
// alla precisione e il cerchio indicativo (poligono in metri). La posizione
// personale non viene mai salvata ne' inviata: resta nello stato della pagina
// finche' l'operatore non la trasferisce al flusso di inserimento e la
// conferma lui stesso.
// ---------------------------------------------------------------------------

/** Una posizione FRESCA (maximumAge 0), precisa, al massimo 15 s di attesa. */
export const GPS_OPTIONS = Object.freeze({ enableHighAccuracy: true, timeout: 15000, maximumAge: 0 });

/** «Precisione stimata: circa 12 m» (sotto i 5 m si dice «pochi metri», oltre il km in km). */
export function accuracyText(metri) {
  const m = Number(metri);
  if (!Number.isFinite(m) || m < 0) return 'Precisione non nota';
  if (m < 5) return 'Precisione stimata: pochi metri';
  if (m >= 1000) return `Precisione stimata: circa ${(m / 1000).toFixed(1).replace('.', ',')} km`;
  return `Precisione stimata: circa ${Math.round(m)} m`;
}

/** Lo zoom a cui mostrare il punto: piu' stretto quanto piu' la posizione e' precisa. */
export function zoomForAccuracy(metri) {
  const m = Number(metri);
  if (!Number.isFinite(m) || m < 0) return 16;
  if (m <= 25) return 18;
  if (m <= 60) return 17;
  if (m <= 150) return 16;
  return 15;
}

/** I messaggi, uno per esito della Geolocation API (1 negato, 2 non disponibile, 3 tempo scaduto). */
export function gpsErrorText(codice) {
  const c = codice && typeof codice === 'object' ? codice.code : codice;
  if (c === 'unsupported') return 'Questo browser non offre la posizione: cerca l’indirizzo o tocca il punto sulla mappa.';
  if (c === 1) return 'Permesso negato: consenti al browser di usare la posizione per «La mia posizione», oppure cerca l’indirizzo.';
  if (c === 2) return 'Posizione non disponibile ora: riprova all’aperto, oppure cerca l’indirizzo.';
  if (c === 3) return 'Tempo scaduto: nessuna posizione ricevuta. Riprova, oppure cerca l’indirizzo.';
  return 'Posizione non ricevuta: riprova, oppure cerca l’indirizzo.';
}

/** Il cerchio indicativo della precisione: un poligono GeoJSON di raggio `metri` attorno al punto. */
export function accuracyCircle(lng, lat, metri, punti = 48) {
  const r = Math.max(0, Number(metri) || 0);
  const dLat = r / 111320;
  const dLng = r / (111320 * Math.max(Math.cos((Number(lat) * Math.PI) / 180), 0.01));
  const anello = [];
  for (let i = 0; i <= punti; i += 1) {
    const a = (i / punti) * 2 * Math.PI;
    anello.push([Number(lng) + dLng * Math.cos(a), Number(lat) + dLat * Math.sin(a)]);
  }
  return { type: 'Feature', properties: { accuracy: r }, geometry: { type: 'Polygon', coordinates: [anello] } };
}

/** Le due feature dell'indicatore: il cerchio e il punto. */
export function gpsFeatureCollection(lng, lat, metri) {
  return {
    type: 'FeatureCollection',
    features: [accuracyCircle(lng, lat, metri),
      { type: 'Feature', properties: { kind: 'gps' }, geometry: { type: 'Point', coordinates: [Number(lng), Number(lat)] } }],
  };
}

// ---------------------------------------------------------------------------
// STIMA360 ZONA-5 - il pannello «Indirizzo individuato»
//
// Dal risultato della ricerca inversa (o da quello della ricerca diretta) ai
// quattro campi modificabili; dai campi confermati dall'operatore al «luogo»
// che precompila la procedura guidata (wizardFieldsFromPlace). La conferma e'
// una dichiarazione dell'operatore sui dati dell'indirizzo, non una certezza
// catastale; un civico assente resta vuoto.
// ---------------------------------------------------------------------------

/** I campi del pannello da un risultato (inverso o diretto): mai un civico inventato. */
export function addressFieldsFrom(risultato) {
  const r = risultato && typeof risultato === 'object' ? risultato : {};
  const civico = r.precision === 'house' && r.house_number_matches === false ? '' : testo(r.house_number);
  return { road: testo(r.road).slice(0, 250), house_number: civico.slice(0, 30), town: testo(r.town).slice(0, 120), postcode: testo(r.postcode).slice(0, 20) };
}

/** Il «luogo» dichiarato dall'operatore, nella forma che la procedura guidata gia' capisce. */
export function placeFromAddressFields(campi) {
  const f = addressFieldsFrom(campi);
  const pezzi = [[f.house_number, f.road].filter(Boolean).join(', ') || f.road, [f.postcode, f.town].filter(Boolean).join(' ')].filter(Boolean);
  return {
    road: f.road || null, house_number: f.house_number || null, town: f.town || null, postcode: f.postcode || null,
    precision: f.house_number ? 'house' : (f.road ? 'street' : 'area'), house_number_matches: Boolean(f.house_number),
    label: pezzi.join(', '), declared: true,
  };
}

/** «Via Roma 25, 64011 Alba Adriatica» (o «Indirizzo non indicato»). */
export function addressSummary(campi) {
  const f = addressFieldsFrom(campi);
  const via = [f.road, f.house_number].filter(Boolean).join(' ');
  const luogo = [f.postcode, f.town].filter(Boolean).join(' ');
  return [via, luogo].filter(Boolean).join(', ') || 'Indirizzo non indicato';
}
