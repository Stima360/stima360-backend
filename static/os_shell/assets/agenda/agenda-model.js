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

/** Le viste di A30-4 (piano §3/§4.3): il Mese e' solo desktop/tablet (G3). */
export const VIEWS = Object.freeze(['week', 'day', 'month', 'list']);
/** Su smartphone solo Lista e Giorno: settimana e mese non si comprimono (G3). */
export const MOBILE_VIEWS = Object.freeze(['list', 'day']);
export const MOBILE_MAX_WIDTH = 767;
export const VIEW_LABELS = Object.freeze({ week: 'Settimana', day: 'Giorno', month: 'Mese', list: 'Lista' });
/** Il segmento di URL di ogni vista (`#/agenda/<vista>/<data>`). */
export const VIEW_SLUGS = Object.freeze({ week: 'settimana', day: 'giorno', month: 'mese', list: 'lista' });
/** Righe al massimo per cella del Mese; oltre, "+N altri" (piano §4.3). */
export const MONTH_CELL_ROWS = 3;

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

// DELETE-ARCH Fase 1A: specchio di appointments/enums.py::CANCELLED_KIND_LABELS_IT.
// La qualifica di un annullamento; `mistake` = creato per errore (fuori
// dall'Agenda normale, visibile solo col filtro «Creati per errore»).
export const CANCELLED_KIND_LABELS = Object.freeze({
  client: 'Annullato dal cliente',
  agency: "Annullato dall'agenzia",
  mistake: 'Creato per errore',
});

/** L'etichetta di una qualifica di annullamento, o '' (storico: nessuna). */
export function cancelledKindLabel(kind) {
  return CANCELLED_KIND_LABELS[kind] || '';
}

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
  if (view === 'month') {
    // Il mese CIVILE (<= 31 giorni): l'API accetta al massimo 42 giorni di
    // tempo trascorso, e una griglia di 6 settimane attraverso il cambio
    // d'ora d'ottobre sarebbe 42 giorni + 1 ora. I giorni dei mesi vicini
    // nella griglia sono solo contorno (`monthGrid`), senza dati.
    const primo = firstOfMonth(key);
    const giorni = [];
    for (let g = primo; g.slice(0, 7) === primo.slice(0, 7); g = addDays(g, 1)) giorni.push(g);
    return { from: romeIso(primo), to: romeIso(addDays(giorni[giorni.length - 1], 1)), days: giorni };
  }
  // lista: il giorno scelto e i sei successivi
  const giorni = Array.from({ length: 7 }, (_, i) => addDays(key, i));
  return { from: romeIso(key), to: romeIso(addDays(key, 7)), days: giorni };
}

/** Il passo di navigazione (◀ ▶) in giorni (Settimana, Giorno, Lista). */
export function stepDays(view) {
  return view === 'day' ? 1 : 7;
}

/** 'YYYY-MM-01' del mese di `key`. */
export function firstOfMonth(key) {
  return `${key.slice(0, 7)}-01`;
}

/** Il primo giorno del mese precedente (dir -1) o successivo (+1). */
export function addMonths(key, dir) {
  const [y, m] = key.split('-').map(Number);
  const t = new Date(Date.UTC(y, m - 1 + dir, 1));
  return `${t.getUTCFullYear()}-${due(t.getUTCMonth() + 1)}-01`;
}

/** ◀ ▶ (e ← →): il Mese va di mese in mese, le altre viste di `stepDays`. */
export function shiftKey(view, key, dir) {
  return view === 'month' ? addMonths(key, dir) : addDays(key, dir * stepDays(view));
}

/**
 * La griglia del Mese: settimane da LUNEDI' a DOMENICA (come `weekDays`),
 * dalla settimana del 1 a quella dell'ultimo giorno - 4, 5 o 6 righe.
 * Ogni cella: `{ key, inMonth }`; fuori mese = giorni dei mesi vicini.
 */
export function monthGrid(key) {
  const primo = firstOfMonth(key);
  const ultimo = addDays(addMonths(primo, 1), -1);
  const settimane = [];
  for (let lun = addDays(primo, -weekdayIndex(primo)); lun <= ultimo; lun = addDays(lun, 7)) {
    settimane.push(Array.from({ length: 7 }, (_, i) => {
      const k = addDays(lun, i);
      return { key: k, inMonth: k.slice(0, 7) === primo.slice(0, 7) };
    }));
  }
  return settimane;
}

const MESE_ANNO = new Intl.DateTimeFormat('it-IT', { timeZone: TIMEZONE, month: 'long', year: 'numeric' });

/** "ottobre 2026". */
export function formatMonth(key) {
  return MESE_ANNO.format(new Date(romeToUtcMs(firstOfMonth(key), 12, 0)));
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
 * I gruppi di eventi sovrapposti di UN giorno, con la colonna "grezza" di
 * ciascuno (assegnazione greedy, SENZA limite). Base comune di
 * `overlapLayout` e `overflowLayout`: una sola regola di impaginazione.
 */
function gruppiSovrapposti(items) {
  const ordinati = (items || []).map((it, i) => ({
    i, s: Date.parse(it.start_at), e: Date.parse(it.end_at),
  })).sort((a, b) => a.s - b.s || b.e - a.e || a.i - b.i);
  const gruppi = [];
  let gruppo = [];
  let fineGruppo = -Infinity;
  let fineColonne = [];
  for (const it of ordinati) {
    if (gruppo.length && it.s >= fineGruppo) {
      gruppi.push(gruppo);
      gruppo = [];
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
  if (gruppo.length) gruppi.push(gruppo);
  return gruppi;
}

/**
 * Sotto-colonne per gli eventi sovrapposti di UN giorno. Restituisce, per
 * indice dell'elemento, `{ column, columns }`. Oltre MAX_OVERLAP_COLUMNS gli
 * eventi finiscono nell'ultima colonna: per la griglia si usa
 * `overflowLayout`, che invece li raccoglie dietro "+N" (A30-13C).
 */
export function overlapLayout(items) {
  const esito = new Array((items || []).length);
  for (const gruppo of gruppiSovrapposti(items)) {
    const colonne = Math.min(Math.max(1, ...gruppo.map((g) => g.column + 1)), MAX_OVERLAP_COLUMNS);
    for (const g of gruppo) {
      esito[g.i] = { column: Math.min(g.column, colonne - 1), columns: colonne };
    }
  }
  return esito;
}

/**
 * A30-13C: come `overlapLayout`, ma nessun evento resta irraggiungibile.
 *
 * Un gruppo che sta in MAX_OVERLAP_COLUMNS colonne si impagina come sempre.
 * Uno che ne chiederebbe di piu' mostra i suoi eventi nelle prime
 * MAX_OVERLAP_COLUMNS - 1 colonne e usa l'ultima per un indicatore "+N":
 * `overflow` elenca, per ogni gruppo, gli indici NASCOSTI (quelli che
 * sarebbero finiti dalla colonna MAX_OVERLAP_COLUMNS - 1 in poi) e
 * l'intervallo che coprono, dove l'indicatore si disegna.
 *
 * Restituisce `{ placement, overflow }`: `placement[i]` e' `{ column,
 * columns }` per un evento visibile e `null` per uno nascosto. Ogni indice e'
 * visibile oppure in UN solo gruppo di `overflow`, mai perso.
 */
export function overflowLayout(items) {
  const lista = items || [];
  const placement = new Array(lista.length).fill(null);
  const overflow = [];
  for (const gruppo of gruppiSovrapposti(lista)) {
    const richieste = Math.max(1, ...gruppo.map((g) => g.column + 1));
    if (richieste <= MAX_OVERLAP_COLUMNS) {
      for (const g of gruppo) placement[g.i] = { column: g.column, columns: richieste };
      continue;
    }
    const corsia = MAX_OVERLAP_COLUMNS - 1;
    const nascosti = [];
    for (const g of gruppo) {
      if (g.column < corsia) placement[g.i] = { column: g.column, columns: MAX_OVERLAP_COLUMNS };
      else nascosti.push(g.i);
    }
    nascosti.sort((a, b) => Date.parse(lista[a].start_at) - Date.parse(lista[b].start_at) || a - b);
    const inizio = Math.min(...nascosti.map((i) => Date.parse(lista[i].start_at)));
    const fine = Math.max(...nascosti.map((i) => Date.parse(lista[i].end_at)));
    overflow.push({
      indices: nascosti,
      column: corsia,
      columns: MAX_OVERLAP_COLUMNS,
      start_at: new Date(inizio).toISOString(),
      end_at: new Date(fine).toISOString(),
    });
  }
  return { placement, overflow };
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
  // A30-8 D6: le guardie temporali e il follow-up.
  COMPLETE_TOO_EARLY: "L'appuntamento non è ancora iniziato: potrai completarlo dall'orario di inizio.",
  NO_SHOW_TOO_EARLY: "Potrai segnare il cliente come non presentato solo dopo la fine dell'appuntamento.",
  RESCHEDULE_IN_PAST: 'Il nuovo orario è già passato: scegli un orario futuro.',
  FOLLOW_UP_IN_PAST: 'La scadenza del follow-up deve essere nel futuro.',
  FOLLOW_UP_REQUIRES_LINK: 'Il follow-up richiede un cliente, un lead o una stima collegati.',
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

// ---------------------------------------------------------------------------
// A30-8 - esito e follow-up. Funzioni pure: il backend resta autorevole.
// ---------------------------------------------------------------------------

/** Il messaggio mostrato DOPO il 2xx di un esito (mai prima). */
export const OUTCOME_SUCCESS = Object.freeze({
  complete: 'Appuntamento completato.',
  no_show: 'Cliente segnato come non presentato.',
  cancel: 'Appuntamento annullato.',
});

/** Il messaggio di successo di un'azione, o null se non ne ha uno proprio. */
export function actionSuccessMessage(action) {
  return OUTCOME_SUCCESS[action] || null;
}

// A30-13B.1: specchio puro di `operator_auth.permissions.may_assign_records`
// (titolare, amministratore o platform admin dentro un'agenzia "acting").
// Usato sia per mostrare "Aggiorna richieste dal sito" (A30-7) sia per
// decidere se il campo Agente del form di creazione e' un selettore libero o
// bloccato su se stessi (A30-13B.1): UNA sola funzione, non due copie della
// stessa regola. Solo per NON mostrare un controllo che il server
// rifiuterebbe: l'autorita' resta sempre il server (403/ForbiddenRole).
export function canAssignRecords(session) {
  if (!session) return false;
  if (session.is_platform_admin === true) {
    return session.acting !== null && session.acting !== undefined;
  }
  return session.role === 'agency_owner' || session.role === 'agency_admin';
}

// ---------------------------------------------------------------------------
// FILTRI della vista (piano A30-4 congelato, §3 AgendaFilters, §4.1, §7, §11).
//
// Traducono una scelta dell'operatore nei parametri che `GET /calendar` e la
// lista ACCETTANO GIA' (appointments/router.py): nessuna regola nuova, nessun
// parametro inventato. Con i valori di partenza non si manda NIENTE, cosi' la
// richiesta resta identica a quella di prima e ogni vista tiene il default
// del server (il calendario nasconde annullati e spostati, la lista no).
//
// La visibilita' NON si decide qui: un `agent` vede comunque solo i propri
// appuntamenti (server, `_solo_agente`). Il filtro agente si offre solo a chi
// assegna, e solo dove l'API lo accetta (calendario, non lista).
// ---------------------------------------------------------------------------

/** "Tutti gli stati", anche annullati e spostati. */
export const ALL_STATUSES_FILTER = 'all';

/** DELETE-ARCH Fase 1A: «Creati per errore» - il solo modo di vederli. */
export const MISTAKES_FILTER = 'mistakes';

export const DEFAULT_FILTERS = Object.freeze({
  agent: '', type: '', status: '', colleagues: true,
});

/** Un filtro qualunque (anche arrivato da un vecchio stato) ridotto a valori
 *  che il server accetterebbe; tutto il resto torna al valore di partenza. */
export function normalizeFilters(filters) {
  const f = filters || {};
  const agente = Number(f.agent);
  return {
    agent: Number.isInteger(agente) && agente > 0 ? String(agente) : '',
    type: Object.prototype.hasOwnProperty.call(TYPE_LABELS, f.type) ? f.type : '',
    status: f.status === ALL_STATUSES_FILTER || f.status === MISTAKES_FILTER
      || Object.prototype.hasOwnProperty.call(STATUS_LABELS, f.status) ? f.status : '',
    colleagues: f.colleagues !== false,
  };
}

function statiDelFiltro(status) {
  if (status === ALL_STATUSES_FILTER) return Object.keys(STATUS_LABELS);
  if (status === MISTAKES_FILTER) return ['cancelled'];
  return status ? [status] : undefined;
}

/** I parametri di `getCalendar` (Settimana, Giorno). */
export function calendarFilterParams(filters, { canAssign = false } = {}) {
  const f = normalizeFilters(filters);
  const parametri = {};
  if (canAssign && f.agent) parametri.agents = [Number(f.agent)];
  if (f.type) parametri.types = [f.type];
  const stati = statiDelFiltro(f.status);
  if (stati) parametri.statuses = stati;
  if (f.status === MISTAKES_FILTER) parametri.mistakes = true;
  // Solo per chi vede i colleghi come "Occupato", e solo se li spegne: il
  // valore di partenza del server e' "si'".
  if (!canAssign && !f.colleagues) parametri.showColleagues = false;
  return parametri;
}

/** I parametri di `getList` (Lista): la lista non ha un filtro agente. */
export function listFilterParams(filters) {
  const f = normalizeFilters(filters);
  const parametri = {};
  if (f.type) parametri.types = [f.type];
  const stati = statiDelFiltro(f.status);
  if (stati) parametri.statuses = stati;
  if (f.status === MISTAKES_FILTER) parametri.mistakes = true;
  return parametri;
}

/** Quanti filtri stanno cambiando cio' che si vede in questa vista. */
export function activeFilterCount(filters, { canAssign = false, view = 'week' } = {}) {
  const f = normalizeFilters(filters);
  const griglia = view !== 'list';
  return [
    Boolean(f.type),
    Boolean(f.status),
    griglia && canAssign && Boolean(f.agent),
    griglia && !canAssign && !f.colleagues,
  ].filter(Boolean).length;
}

// ---------------------------------------------------------------------------
// A30-11 in UI: orari di lavoro, eccezioni, chiusure. Solo traduzioni fra i
// minuti del contratto ([start, end), 0..1440, dalla mezzanotte di Roma) e
// l'ora che l'operatore legge e scrive. Nessuna regola nuova: le fasce
// sovrapposte e i limiti li rifiuta il server (EXCLUDE della 076, schemi).
// ---------------------------------------------------------------------------

/** ISO: 1 = lunedi' ... 7 = domenica, come `day_of_week` della 076. */
export const WEEKDAY_LABELS = Object.freeze({
  1: 'Lunedì', 2: 'Martedì', 3: 'Mercoledì', 4: 'Giovedì', 5: 'Venerdì', 6: 'Sabato', 7: 'Domenica',
});

/** 540 -> "09:00"; 1440 -> "24:00" (fine giornata). */
export function minutesToTime(minuti) {
  const m = Number(minuti);
  if (!Number.isInteger(m) || m < 0 || m > 1440) return '';
  return `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`;
}

/** "09:00" -> 540; "24:00" -> 1440; altro -> null. */
export function timeToMinutes(testo) {
  if (String(testo || '').trim() === '24:00') return 1440;
  const t = parseTime(testo);
  return t ? t[0] * 60 + t[1] : null;
}

/** "Tutto il giorno" per [0, 1440), altrimenti "09:00–13:00". */
export function intervalLabel(start, end) {
  if (Number(start) === 0 && Number(end) === 1440) return 'Tutto il giorno';
  return `${minutesToTime(start)}–${minutesToTime(end)}`;
}

/** Le righe di GET working-hours raggruppate per giorno 1..7, in ordine. */
export function groupWeeklyHours(items) {
  const giorni = {};
  for (let d = 1; d <= 7; d += 1) giorni[d] = [];
  for (const r of items || []) {
    const d = Number(r.day_of_week);
    if (giorni[d]) giorni[d].push({ start: Number(r.start_minute), end: Number(r.end_minute) });
  }
  for (let d = 1; d <= 7; d += 1) giorni[d].sort((a, b) => a.start - b.start);
  return giorni;
}

/**
 * Dai giorni della UI al corpo del PUT: `{ slots, error }`. Si rifiuta qui
 * solo cio' che non e' un orario leggibile o una fascia vuota/rovesciata
 * (lo stesso controllo dello schema); tutto il resto lo decide il server.
 */
export function weeklySlotsFromDays(giorni) {
  const slots = [];
  for (let d = 1; d <= 7; d += 1) {
    const giorno = (giorni && giorni[d]) || { active: false, intervals: [] };
    if (!giorno.active) continue;
    for (const f of giorno.intervals || []) {
      const start = timeToMinutes(f.start);
      const end = timeToMinutes(f.end);
      if (start === null || end === null) {
        return { slots: null, error: `${WEEKDAY_LABELS[d]}: indica ora di inizio e di fine.` };
      }
      if (end <= start) {
        return { slots: null, error: `${WEEKDAY_LABELS[d]}: la fine deve essere dopo l'inizio (nessuna fascia attraversa la mezzanotte).` };
      }
      slots.push({ day_of_week: d, start_minute: start, end_minute: end });
    }
  }
  return { slots, error: null };
}

// ---------------------------------------------------------------------------
// A30-12 in UI: link di prenotazione pubblica.
// ---------------------------------------------------------------------------

/** P30: la PAGINA pubblica per il cliente (`public_booking/page.py`,
 *  `static/public_booking/`). E' l'indirizzo da condividere: la pagina parla
 *  da se' con l'API A30-12; l'Agenda non la chiama mai. */
export const PUBLIC_BOOKING_PATH = '/prenota/';

/** L'URL cliente completa di un token appena creato o ruotato. */
export function publicBookingUrl(origin, token) {
  if (!token) return '';
  return `${String(origin || '').replace(/\/+$/, '')}${PUBLIC_BOOKING_PATH}${encodeURIComponent(token)}`;
}

/** Lo stato leggibile di un link: disattivato, scaduto o attivo. */
export function bookingLinkStatus(link, now = new Date()) {
  if (!link || link.status !== 'active' || link.revoked_at) return 'Disattivato';
  if (link.expires_at && Date.parse(link.expires_at) <= now.getTime()) return 'Scaduto';
  return 'Attivo';
}

/** Vero se l'appuntamento ha un contatto, un lead o una stima: senza, il
 *  follow-up (un task CORE) non si puo' creare e il blocco non si mostra. */
export function canFollowUp(row) {
  if (!row) return false;
  return ['contact_id', 'lead_id', 'stima_id'].some((c) => row[c] !== null && row[c] !== undefined);
}

/**
 * Da quando un esito sara' registrabile: `start_at` per "Completa", `end_at`
 * per "Non presentato". null se l'azione non ha guardia, se lo stato non e'
 * aperto-fissato o se l'orario e' gia' passato. Solo un'indicazione: il
 * server ricontrolla sul proprio orologio.
 */
export function availableFrom(row, action, now = new Date()) {
  if (!row || !['scheduled', 'confirmed'].includes(row.status)) return null;
  const campo = action === 'complete' ? 'start_at' : action === 'no_show' ? 'end_at' : null;
  if (!campo || !row[campo]) return null;
  const quando = new Date(row[campo]);
  if (Number.isNaN(quando.getTime())) return null;
  return quando.getTime() > now.getTime() ? row[campo] : null;
}

/** La nota di esito registrata: dall'ultimo evento `status_changed` che la
 *  porta, tra quelli che il pannello ha GIA' caricato (`/events`). */
export function outcomeNote(events) {
  const lista = Array.isArray(events) ? events : [];
  for (let i = lista.length - 1; i >= 0; i -= 1) {
    const e = lista[i];
    if (e && e.event_type === 'status_changed' && e.changes
        && typeof e.changes.outcome_note === 'string' && e.changes.outcome_note.trim()) {
      return e.changes.outcome_note.trim();
    }
  }
  return null;
}

// ---------------------------------------------------------------------------
// Rifiniture UX del piano A30-4 (§3 Legenda, §10-§11 rete, §12 tastiera e
// fuso). Funzioni pure: nessun DOM, nessuna rete.
// ---------------------------------------------------------------------------

/** §12: date e orari dell'Agenda sono SEMPRE nel fuso di Roma (`TIMEZONE`). */
export const TIMEZONE_LABEL = 'Orario di Roma';

/**
 * §3/§12 "Legenda sempre visibile": gli stati VERI (`STATUS_LABELS`, specchio
 * del backend), con le stesse classi delle card; piu' "Occupato" solo dove un
 * impegno di un collega puo' davvero comparire (chi non assegna, in una vista
 * a griglia). Il TIPO non e' un codice visivo (§8): e' scritto su ogni card.
 */
export function legendEntries({ withBusy = false } = {}) {
  const voci = Object.entries(STATUS_LABELS).map(([stato, etichetta]) => ({
    key: stato, label: etichetta, className: `agenda-badge agenda-badge-${stato}`,
  }));
  if (withBusy) {
    voci.push({ key: 'busy', label: 'Occupato (collega)', className: 'agenda-badge agenda-badge-busy' });
  }
  voci.push({ key: 'online', label: 'Online: prenotato dal link', className: 'agenda-badge agenda-badge-online' });
  return voci;
}

/** A30-12: la `source` con cui nasce un appuntamento prenotato dal link pubblico. */
export const ONLINE_SOURCE = 'booking_link';

/** Vero SOLO per un appuntamento (mai un "Occupato" di collega, che non porta
 *  `source`) nato da una prenotazione online. */
export function isOnlineAppointment(item) {
  return !!item && item.kind !== 'busy' && item.source === ONLINE_SOURCE;
}

/**
 * §12 tastiera: ← periodo precedente, → successivo, T oggi. `null` quando il
 * tasto non va interpretato: combinazioni con Ctrl/Alt/Meta, composizione
 * (IME), un campo in scrittura (input, textarea, select, contenteditable),
 * un dialog o il pannello aperti, oppure il fuoco fuori dalla barra
 * dell'Agenda (§12: "quando il focus e' sulla toolbar"; nessun fuoco = il
 * documento, vale come barra).
 */
export function shortcutAction(event, { editing = false, dialogOpen = false, inToolbar = false } = {}) {
  if (!event || event.defaultPrevented || event.isComposing) return null;
  if (event.ctrlKey || event.altKey || event.metaKey) return null;
  if (editing || dialogOpen || !inToolbar) return null;
  if (event.key === 'ArrowLeft') return 'prev';
  if (event.key === 'ArrowRight') return 'next';
  if (event.key === 't' || event.key === 'T') return 'today';
  return null;
}

/** §10: "dati del 10:42" - l'ora (di Roma) dell'ultimo caricamento riuscito. */
export function loadedAtLabel(date) {
  if (!(date instanceof Date) || Number.isNaN(date.getTime())) return '';
  return formatTime(date.toISOString());
}
