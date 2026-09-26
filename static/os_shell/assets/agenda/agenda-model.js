// STIMA360 OS — agenda/agenda-model.js (A30-4)
//
// FUNZIONI PURE: niente DOM, niente fetch. Date e fuso Europe/Rome, intervalli
// delle viste, posizione dei blocchi nella griglia, etichette leggibili degli
// stati e dei tipi, traduzione degli errori dell'API A30-2.
//
// Tutto e' calcolato nel fuso Europe/Rome con `Intl.DateTimeFormat`, qualunque
// sia il fuso del browser: un'agenzia italiana vede le ore italiane anche da un
// portatile rimasto in UTC. Nessuna libreria.
//
// Qui NON si decide cosa l'operatore puo' fare: le azioni ammesse arrivano dal
// server (`allowed_actions`). Qui si decide solo come mostrarle.

export const TIMEZONE = 'Europe/Rome';

/** Altezza di un'ora nella griglia (px). La griglia copre 00:00-24:00. */
export const HOUR_HEIGHT = 64;
/** La fascia 08:00-20:00 e' SOLO la posizione iniziale dello scroll: non e'
 *  una regola di disponibilita' (D7). */
export const VIEWPORT_START_HOUR = 8;
export const VIEWPORT_END_HOUR = 20;
/** Sotto-colonne massime per eventi sovrapposti nello stesso giorno. */
export const MAX_OVERLAP_COLUMNS = 4;

/** Le viste di A30-4. Nessun Mese: non e' in questa fase. */
export const VIEWS = Object.freeze(['week', 'day', 'list']);
/** Su smartphone solo Lista e Giorno: la settimana non si comprime. */
export const MOBILE_VIEWS = Object.freeze(['list', 'day']);
export const MOBILE_MAX_WIDTH = 767;
export const VIEW_LABELS = Object.freeze({ week: 'Settimana', day: 'Giorno', list: 'Lista' });
/** Il segmento di URL di ogni vista (`#/agenda/<vista>/<data>`). */
export const VIEW_SLUGS = Object.freeze({ week: 'settimana', day: 'giorno', list: 'lista' });

// Specchio di appointments/state_machine.py::_ETICHETTE (un test li
// confronta: se il backend cambia un'etichetta, il test lo dice).
export const STATUS_LABELS = Object.freeze({
  requested: 'Richiesta',
  scheduled: 'Fissato',
  confirmed: 'Confermato',
  completed: 'Completato',
  cancelled: 'Annullato',
  no_show: 'Assente',
  rescheduled: 'Spostato',
});

// Specchio di appointments/enums.py::APPOINTMENT_TYPE_LABELS_IT.
export const TYPE_LABELS = Object.freeze({
  call: 'Telefonata',
  video_call: 'Videochiamata',
  seller_meeting: 'Appuntamento proprietario',
  inspection: 'Sopralluogo',
  buyer_visit: 'Visita acquirente',
  valuation_presentation: 'Presentazione valutazione',
  mandate_signing: 'Firma incarico',
  proposal: 'Proposta',
  preliminary_contract: 'Preliminare',
  notary: 'Rogito',
  technical: 'Tecnico',
  other: 'Altro',
});

// Specchio di appointments/enums.py::DEFAULT_DURATION_MINUTES: e' solo il
// valore PROPOSTO nel form, non un vincolo.
export const DEFAULT_DURATION_MINUTES = Object.freeze({
  call: 15, video_call: 30, inspection: 60, buyer_visit: 60,
});
export const FALLBACK_DURATION_MINUTES = 60;

/** Le azioni di A30-2 (le chiavi di `allowed_actions`) e come si chiamano. */
export const ACTION_LABELS = Object.freeze({
  schedule: 'Pianifica',
  confirm: 'Conferma',
  reschedule: 'Sposta',
  reassign: 'Assegna / riassegna',
  complete: 'Completa',
  no_show: 'Assente (no-show)',
  cancel: 'Annulla appuntamento',
  patch: 'Modifica note e luogo',
});
/** Ordine dei pulsanti nel pannello. */
export const ACTION_ORDER = Object.freeze(
  ['schedule', 'confirm', 'reschedule', 'reassign', 'complete', 'no_show', 'cancel', 'patch']);
/** Il segmento di percorso di ogni azione (`POST /api/appointments/{id}/<...>`). */
export const ACTION_PATHS = Object.freeze({
  schedule: 'schedule', confirm: 'confirm', reschedule: 'reschedule', reassign: 'reassign',
  complete: 'complete', no_show: 'no-show', cancel: 'cancel',
});

// I tre tipi di `appointment_events` ammessi dal CHECK della 072.
export const EVENT_LABELS = Object.freeze({
  created: 'Creato',
  updated: 'Modificato',
  status_changed: 'Stato cambiato',
});

export function statusLabel(status) {
  return STATUS_LABELS[status] || '';
}

export function typeLabel(type) {
  return TYPE_LABELS[type] || '';
}

export function defaultDuration(type) {
  return DEFAULT_DURATION_MINUTES[type] || FALLBACK_DURATION_MINUTES;
}

// ---------------------------------------------------------------------------
// DATE E FUSO
// ---------------------------------------------------------------------------

const PARTI = new Intl.DateTimeFormat('en-GB', {
  timeZone: TIMEZONE, year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
});

function due(n) {
  return String(n).padStart(2, '0');
}

/** Le parti di un istante nel fuso di Roma. */
export function romeParts(value) {
  const ms = value instanceof Date ? value.getTime() : Date.parse(value);
  const parti = {};
  for (const p of PARTI.formatToParts(new Date(ms))) parti[p.type] = p.value;
  return {
    year: Number(parti.year), month: Number(parti.month), day: Number(parti.day),
    hour: Number(parti.hour) % 24, minute: Number(parti.minute),
  };
}

/** 'YYYY-MM-DD' del giorno di Roma che contiene l'istante. */
export function romeDateKey(value) {
  const p = romeParts(value);
  return `${p.year}-${due(p.month)}-${due(p.day)}`;
}

export function isDateKey(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const [y, m, d] = value.split('-').map(Number);
  const t = new Date(Date.UTC(y, m - 1, d));
  return t.getUTCFullYear() === y && t.getUTCMonth() === m - 1 && t.getUTCDate() === d;
}

export function todayKey(now = new Date()) {
  return romeDateKey(now);
}

/** Aritmetica di calendario pura su 'YYYY-MM-DD' (nessun fuso coinvolto). */
export function addDays(key, n) {
  const [y, m, d] = key.split('-').map(Number);
  const t = new Date(Date.UTC(y, m - 1, d + n));
  return `${t.getUTCFullYear()}-${due(t.getUTCMonth() + 1)}-${due(t.getUTCDate())}`;
}

/** 0 = lunedi' ... 6 = domenica. */
export function weekdayIndex(key) {
  const [y, m, d] = key.split('-').map(Number);
  return (new Date(Date.UTC(y, m - 1, d)).getUTCDay() + 6) % 7;
}

/** I sette giorni della settimana che contiene `key`, da lunedi' a DOMENICA. */
export function weekDays(key) {
  const lunedi = addDays(key, -weekdayIndex(key));
  return Array.from({ length: 7 }, (_, i) => addDays(lunedi, i));
}

/** Minuti di scarto di Roma da UTC in quell'istante (+60 inverno, +120 estate). */
export function offsetMinutes(utcMs) {
  const p = romeParts(new Date(utcMs));
  const comeUtc = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute);
  return Math.round((comeUtc - Math.floor(utcMs / 60000) * 60000) / 60000);
}

/** L'istante UTC (ms) dell'ora di Roma data. Due passaggi: il cambio d'ora. */
export function romeToUtcMs(key, hour = 0, minute = 0) {
  const [y, m, d] = key.split('-').map(Number);
  const come = Date.UTC(y, m - 1, d, hour, minute);
  const primo = offsetMinutes(come);
  let t = come - primo * 60000;
  const secondo = offsetMinutes(t);
  if (secondo !== primo) t = come - secondo * 60000;
  return t;
}

export const NONEXISTENT_TIME_MESSAGE =
  "Questo orario non esiste a causa del cambio dell'ora legale. Scegli un altro orario.";

/** Un orario da muro che a Roma non esiste (il salto in avanti dell'ora legale). */
export class NonexistentLocalTimeError extends Error {
  constructor() {
    super(NONEXISTENT_TIME_MESSAGE);
    this.name = 'NonexistentLocalTimeError';
  }
}

/**
 * L'orario da muro `key hour:minute` esiste a Roma? Nessuna tabella di date:
 * si converte in un istante e si rilegge quell'istante nel fuso Europe/Rome
 * (dati del fuso del motore JS, `Intl`). Se la rilettura non restituisce lo
 * stesso giorno, ora e minuto, quell'orario e' stato saltato dal cambio d'ora.
 * Un orario ambiguo (ripetuto al ritorno dell'ora solare) esiste: vale.
 */
export function romeWallTimeExists(key, hour = 0, minute = 0) {
  const [y, m, d] = key.split('-').map(Number);
  const p = romeParts(new Date(romeToUtcMs(key, hour, minute)));
  return p.year === y && p.month === m && p.day === d && p.hour === hour && p.minute === minute;
}

/**
 * ISO 8601 CON il fuso di Roma: l'API rifiuta un orario senza fuso.
 *
 * Un orario che a Roma non esiste NON viene spostato in silenzio a un altro
 * orario: si solleva `NonexistentLocalTimeError`, e il dialog mostra il
 * messaggio senza inviare nulla. Un orario ambiguo resta com'e' oggi (la
 * seconda occorrenza, in ora solare).
 */
export function romeIso(key, hour = 0, minute = 0) {
  if (!romeWallTimeExists(key, hour, minute)) throw new NonexistentLocalTimeError();
  const ms = romeToUtcMs(key, hour, minute);
  const off = offsetMinutes(ms);
  const segno = off >= 0 ? '+' : '-';
  const a = Math.abs(off);
  const p = romeParts(new Date(ms));
  return `${p.year}-${due(p.month)}-${due(p.day)}T${due(p.hour)}:${due(p.minute)}:00`
    + `${segno}${due(Math.floor(a / 60))}:${due(a % 60)}`;
}

/** 'HH:MM' -> [ore, minuti], oppure null. */
export function parseTime(value) {
  const trovato = /^(\d{1,2}):(\d{2})$/.exec(String(value || '').trim());
  if (!trovato) return null;
  const h = Number(trovato[1]);
  const m = Number(trovato[2]);
  if (h > 23 || m > 59) return null;
  return [h, m];
}

export function formatTime(value) {
  const p = romeParts(value);
  return `${due(p.hour)}:${due(p.minute)}`;
}

const GIORNO_LUNGO = new Intl.DateTimeFormat('it-IT', {
  timeZone: TIMEZONE, weekday: 'long', day: 'numeric', month: 'long', year: 'numeric',
});
const GIORNO_BREVE = new Intl.DateTimeFormat('it-IT', {
  timeZone: TIMEZONE, weekday: 'short', day: 'numeric',
});
const DATA_ORA = new Intl.DateTimeFormat('it-IT', {
  timeZone: TIMEZONE, day: '2-digit', month: '2-digit', year: 'numeric',
  hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
});

/** Mezzogiorno di Roma di quel giorno: un istante sicuramente dentro il giorno. */
function mezzogiorno(key) {
  return new Date(romeToUtcMs(key, 12, 0));
}

export function formatDayLong(key) {
  return GIORNO_LUNGO.format(mezzogiorno(key));
}

export function formatDayShort(key) {
  return GIORNO_BREVE.format(mezzogiorno(key));
}

const GIORNO_MESE_ANNO = new Intl.DateTimeFormat('it-IT', {
  timeZone: TIMEZONE, day: 'numeric', month: 'long', year: 'numeric',
});
const GIORNO_MESE = new Intl.DateTimeFormat('it-IT', {
  timeZone: TIMEZONE, day: 'numeric', month: 'long',
});

/** "21 – 27 settembre 2026", "28 settembre – 4 ottobre 2026". */
export function formatRange(primo, ultimo) {
  const a = romeParts(mezzogiorno(primo));
  const b = romeParts(mezzogiorno(ultimo));
  const fine = GIORNO_MESE_ANNO.format(mezzogiorno(ultimo));
  if (a.year !== b.year) return `${GIORNO_MESE_ANNO.format(mezzogiorno(primo))} – ${fine}`;
  if (a.month !== b.month) return `${GIORNO_MESE.format(mezzogiorno(primo))} – ${fine}`;
  return `${a.day} – ${fine}`;
}

export function formatDateTime(value) {
  return DATA_ORA.format(new Date(Date.parse(value)));
}

export function durationMinutes(startIso, endIso) {
  return Math.round((Date.parse(endIso) - Date.parse(startIso)) / 60000);
}

export function formatDuration(minutes) {
  if (!Number.isFinite(minutes) || minutes <= 0) return '';
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (!h) return `${m} min`;
  return m ? `${h} h ${m} min` : `${h} h`;
}

// ---------------------------------------------------------------------------
// VISTE E INTERVALLI
// ---------------------------------------------------------------------------

/** La vista effettiva: su smartphone la settimana non esiste (diventa Lista). */
export function effectiveView(view, isMobile) {
  const v = VIEWS.includes(view) ? view : (isMobile ? 'list' : 'week');
  if (isMobile && !MOBILE_VIEWS.includes(v)) return 'list';
  return v;
}

export function viewFromSlug(slug) {
  return Object.keys(VIEW_SLUGS).find((v) => VIEW_SLUGS[v] === slug) || null;
}

/** L'intervallo [from, to) da chiedere all'API per una vista. */
export function rangeFor(view, key) {
  if (view === 'week') {
    const giorni = weekDays(key);
    return { from: romeIso(giorni[0]), to: romeIso(addDays(giorni[0], 7)), days: giorni };
  }
  if (view === 'day') {
    return { from: romeIso(key), to: romeIso(addDays(key, 1)), days: [key] };
  }
  // lista: il giorno scelto e i sei successivi
  const giorni = Array.from({ length: 7 }, (_, i) => addDays(key, i));
  return { from: romeIso(key), to: romeIso(addDays(key, 7)), days: giorni };
}

/** Il passo di navigazione (◀ ▶) in giorni. */
export function stepDays(view) {
  return view === 'day' ? 1 : 7;
}

// ---------------------------------------------------------------------------
// GRIGLIA
// ---------------------------------------------------------------------------

/** Gli elementi che toccano il giorno di Roma `key`. */
export function itemsForDay(items, key) {
  const inizio = romeToUtcMs(key);
  const fine = romeToUtcMs(addDays(key, 1));
  return (items || []).filter((it) => {
    const s = Date.parse(it.start_at);
    const e = Date.parse(it.end_at);
    return Number.isFinite(s) && Number.isFinite(e) && s < fine && e > inizio;
  });
}

/** Minuti dall'inizio del giorno di Roma (orologio a muro), tagliati al giorno. */
function minutiNelGiorno(iso, key, fineGiorno) {
  if (romeDateKey(iso) !== key) return fineGiorno ? 24 * 60 : 0;
  const p = romeParts(iso);
  return p.hour * 60 + p.minute;
}

/** top/height (px) di un blocco nella colonna del giorno `key`. */
export function blockGeometry(item, key, hourHeight = HOUR_HEIGHT) {
  const inizio = Date.parse(item.start_at) < romeToUtcMs(key) ? 0
    : minutiNelGiorno(item.start_at, key, false);
  const fineMs = Date.parse(item.end_at);
  const fine = fineMs >= romeToUtcMs(addDays(key, 1)) ? 24 * 60
    : minutiNelGiorno(item.end_at, key, true);
  const top = (inizio / 60) * hourHeight;
  const height = Math.max(((Math.max(fine, inizio) - inizio) / 60) * hourHeight, 20);
  return { top, height };
}

/**
 * Sotto-colonne per gli eventi sovrapposti di UN giorno. Restituisce, per
 * indice dell'elemento, `{ column, columns }`. Oltre MAX_OVERLAP_COLUMNS gli
 * eventi finiscono nell'ultima colonna (restano cliccabili).
 */
export function overlapLayout(items) {
  const ordinati = (items || []).map((it, i) => ({
    i, s: Date.parse(it.start_at), e: Date.parse(it.end_at),
  })).sort((a, b) => a.s - b.s || b.e - a.e || a.i - b.i);
  const esito = new Array((items || []).length);
  let gruppo = [];
  let fineGruppo = -Infinity;
  const chiudi = () => {
    const colonne = Math.min(Math.max(1, ...gruppo.map((g) => g.column + 1)), MAX_OVERLAP_COLUMNS);
    for (const g of gruppo) {
      esito[g.i] = { column: Math.min(g.column, colonne - 1), columns: colonne };
    }
    gruppo = [];
  };
  let fineColonne = [];
  for (const it of ordinati) {
    if (gruppo.length && it.s >= fineGruppo) {
      chiudi();
      fineColonne = [];
    }
    let colonna = fineColonne.findIndex((fine) => fine <= it.s);
    if (colonna === -1) {
      colonna = fineColonne.length;
      fineColonne.push(it.e);
    } else {
      fineColonne[colonna] = it.e;
    }
    gruppo.push({ i: it.i, column: colonna });
    fineGruppo = Math.max(fineGruppo === -Infinity ? it.e : fineGruppo, it.e);
  }
  if (gruppo.length) chiudi();
  return esito;
}

/** Lo scroll iniziale della griglia: 08:00. */
export function initialScrollTop(hourHeight = HOUR_HEIGHT) {
  return VIEWPORT_START_HOUR * hourHeight;
}

/** La riga principale di un elemento: SOLO dati arrivati dal server. */
export function itemTitle(item) {
  if (!item) return '';
  if (item.kind === 'busy') return item.label || 'Occupato';
  return item.contact_name || item.place || item.location_text || '';
}

// ---------------------------------------------------------------------------
// ERRORI
// ---------------------------------------------------------------------------

const MESSAGGI_CODICE = Object.freeze({
  VERSION_CONFLICT: 'Questo appuntamento è stato modificato da un altro operatore. Ricarica e riprova.',
  APPOINTMENT_CONFLICT: "Orario non disponibile per l'agente.",
  IDEMPOTENCY_KEY_REUSED: 'Questa richiesta risulta già inviata con dati diversi. Ricontrolla e conferma di nuovo.',
  AGENT_NOT_ACTIVE: "L'agente selezionato non è un membro attivo dell'agenzia.",
  PLATFORM_ADMIN_AGENCY_REQUIRED: "Scegli un'agenzia per usare l'Agenda.",
  PROJECTION_CONFLICT: 'Il sopralluogo è già stato aggiornato altrove. Ricarica.',
});

const MESSAGGI_STATO = Object.freeze({
  401: 'Sessione scaduta. Effettua di nuovo il login.',
  403: 'Non hai i permessi per questa azione.',
  404: 'Appuntamento non trovato o non più disponibile.',
  409: "L'operazione è in conflitto con lo stato attuale. Ricarica e riprova.",
  422: 'Dati non validi. Controlla i campi e riprova.',
});

/**
 * Il messaggio leggibile per un errore dell'API. Usa il `detail` del backend
 * quando e' un testo (i messaggi A30-2 sono scritti per l'utente); mai uno
 * stack, mai un oggetto grezzo. Per i codici noti usa un testo stabile.
 */
export function errorMessage(error) {
  if (!error) return 'Errore sconosciuto.';
  const stato = Number(error.status) || 0;
  const codice = typeof error.code === 'string' ? error.code : '';
  const dettaglio = typeof error.detail === 'string' ? error.detail.trim() : '';
  if (stato === 401) return MESSAGGI_STATO[401];
  if (codice && MESSAGGI_CODICE[codice]) {
    return codice === 'APPOINTMENT_CONFLICT' && dettaglio
      ? `${dettaglio}.`.replace(/\.\.$/, '.') : MESSAGGI_CODICE[codice];
  }
  if (stato === 404) return MESSAGGI_STATO[404];
  if ([403, 409, 422].includes(stato)) return dettaglio || MESSAGGI_STATO[stato];
  if (!stato) return dettaglio || 'Impossibile contattare il server. Verifica la connessione.';
  return dettaglio && stato < 500 ? dettaglio : `Errore del server (${stato}). Riprova.`;
}

/** Vero quando l'errore chiede di ricaricare i dati (versione vecchia, riga sparita). */
export function errorNeedsReload(error) {
  if (!error) return false;
  return error.status === 404 || error.code === 'VERSION_CONFLICT'
    || error.code === 'INVALID_TRANSITION' || error.code === 'PROJECTION_CONFLICT';
}
