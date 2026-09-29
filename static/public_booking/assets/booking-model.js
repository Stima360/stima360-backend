/**
 * P30 - la pagina pubblica di prenotazione: le regole PURE (nessun DOM, nessuna
 * rete, nessuno storage). Tutto cio' che qui si decide e' presentazione:
 * la disponibilita' la decide SOLO il server (`/api/public/booking/{token}/slots`),
 * agente, durata, buffer, tipo e agenzia li decide SOLO il link lato server.
 */

export const ROMA = 'Europe/Rome';

/** Il percorso della pagina: /prenota/<token>. Stessa forma di `public_booking/page.py`. */
const PERCORSO_PAGINA = /^\/prenota\/([A-Za-z0-9_-]{16,200})\/?$/;

/** L'API A30-12, l'unica con cui la pagina parla. */
export const API_BASE = '/api/public/booking/';

/** Quante settimane in avanti si possono sfogliare, e quante se ne cercano da sole all'apertura. */
export const SETTIMANE_MASSIME = 12;
export const SETTIMANE_AUTOMATICHE = 4;

const GIORNO_MS = 24 * 60 * 60 * 1000;
/** Il massimo che l'API accetta per una finestra di slot (7 giorni esatti). */
export const FINESTRA_MASSIMA_MS = 7 * GIORNO_MS;

export const LIMITI = { nome: 120, telefono: 32, email: 254 };

/** Il token dalla URL, oppure null se il percorso non ha la forma attesa. */
export function tokenDaPercorso(pathname) {
  const esito = PERCORSO_PAGINA.exec(String(pathname || ''));
  return esito ? esito[1] : null;
}

// ---------------------------------------------------------------------------
// Ora di Roma, senza librerie: `Intl` e' la sola fonte del fuso.
// ---------------------------------------------------------------------------

const formatoParti = new Intl.DateTimeFormat('en-CA', {
  timeZone: ROMA, year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
});

function partiRoma(ms) {
  const p = {};
  for (const x of formatoParti.formatToParts(new Date(ms))) p[x.type] = x.value;
  return { y: +p.year, m: +p.month, d: +p.day, h: +p.hour, mi: +p.minute, s: +p.second };
}

function scartoRoma(ms) {
  const p = partiRoma(ms);
  return Date.UTC(p.y, p.m - 1, p.d, p.h, p.mi, p.s) - Math.floor(ms / 1000) * 1000;
}

/** 'YYYY-MM-DD' del giorno di Roma che contiene l'istante `ms`. */
export function dataRoma(ms) {
  const p = partiRoma(ms);
  return `${p.y}-${String(p.m).padStart(2, '0')}-${String(p.d).padStart(2, '0')}`;
}

/** L'istante (ms) della mezzanotte di Roma del giorno 'YYYY-MM-DD'. */
export function mezzanotteRoma(ymd) {
  const [y, m, d] = ymd.split('-').map(Number);
  const t0 = Date.UTC(y, m - 1, d);
  let t = t0 - scartoRoma(t0);
  const secondo = scartoRoma(t);
  if (t0 - secondo !== t) t = t0 - secondo;
  return t;
}

/** 'YYYY-MM-DD' spostato di `n` giorni di calendario. */
export function aggiungiGiorni(ymd, n) {
  const [y, m, d] = ymd.split('-').map(Number);
  const t = new Date(Date.UTC(y, m - 1, d + n));
  return t.toISOString().slice(0, 10);
}

/**
 * La finestra di slot che comincia il giorno `inizioYmd` (di Roma): fino a
 * 7 giorni di calendario, ma MAI oltre i 7x24 ore che l'API accetta - la
 * settimana del cambio d'ora d'ottobre dura 169 ore, e allora se ne chiedono 6.
 * Non comincia mai prima di `adessoMs`: il passato non si chiede.
 * Restituisce `null` se la finestra e' interamente passata.
 */
export function finestra(inizioYmd, adessoMs) {
  const inizioGiorno = mezzanotteRoma(inizioYmd);
  const da = Math.max(inizioGiorno, adessoMs);
  for (const giorni of [7, 6, 5]) {
    const fineYmd = aggiungiGiorni(inizioYmd, giorni);
    const a = mezzanotteRoma(fineYmd);
    if (a - da <= FINESTRA_MASSIMA_MS) {
      if (a <= da) return null;
      return { da: new Date(da).toISOString(), a: new Date(a).toISOString(), inizio: inizioYmd, fine: fineYmd };
    }
  }
  return null;
}

// ---------------------------------------------------------------------------
// Risposte del server: SOLO i campi ammessi, mai un oggetto grezzo.
// ---------------------------------------------------------------------------

/** Metadata del link: i soli campi che la pagina mostra o usa. */
export function infoPubblica(json) {
  if (!json || typeof json !== 'object') return null;
  const testo = (v) => (typeof v === 'string' && v.trim() ? v.trim() : null);
  const durata = Number.isInteger(json.duration_minutes) && json.duration_minutes > 0
    ? json.duration_minutes : null;
  const submission = typeof json.submission_token === 'string' && json.submission_token
    ? json.submission_token : null;
  if (!submission || durata === null) return null;
  return {
    agenzia: testo(json.agency_name),
    agente: testo(json.agent_name),
    tipo: testo(json.appointment_type_label) || 'Appuntamento',
    durata,
    submissionToken: submission,
  };
}

/** Gli slot del server, cosi' come sono: solo `start_at`/`end_at` leggibili. */
export function slotDelServer(json) {
  const elenco = json && Array.isArray(json.slots) ? json.slots : [];
  const esito = [];
  for (const s of elenco) {
    if (!s || typeof s.start_at !== 'string' || typeof s.end_at !== 'string') continue;
    const inizio = Date.parse(s.start_at);
    const fine = Date.parse(s.end_at);
    if (!Number.isFinite(inizio) || !Number.isFinite(fine) || fine <= inizio) continue;
    esito.push({ start_at: s.start_at, end_at: s.end_at, inizioMs: inizio, fineMs: fine });
  }
  return esito.sort((x, y) => x.inizioMs - y.inizioMs);
}

/** Gli slot raggruppati per giorno di Roma: solo i giorni che ne hanno. */
export function giorniConSlot(slot) {
  const mappa = new Map();
  for (const s of slot) {
    const giorno = dataRoma(s.inizioMs);
    if (!mappa.has(giorno)) mappa.set(giorno, []);
    mappa.get(giorno).push(s);
  }
  return [...mappa.entries()].sort(([a], [b]) => (a < b ? -1 : 1))
    .map(([giorno, lista]) => ({ giorno, slot: lista }));
}

// ---------------------------------------------------------------------------
// Testi in italiano, sempre nel fuso di Roma.
// ---------------------------------------------------------------------------

const fmtGiornoLungo = new Intl.DateTimeFormat('it-IT', {
  timeZone: ROMA, weekday: 'long', day: 'numeric', month: 'long', year: 'numeric',
});
const fmtGiornoBreve = new Intl.DateTimeFormat('it-IT', { timeZone: ROMA, weekday: 'short' });
const fmtNumero = new Intl.DateTimeFormat('it-IT', { timeZone: ROMA, day: 'numeric' });
const fmtMese = new Intl.DateTimeFormat('it-IT', { timeZone: ROMA, month: 'short' });
const fmtOra = new Intl.DateTimeFormat('it-IT', {
  timeZone: ROMA, hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
});

const maiuscola = (t) => (t ? t.charAt(0).toUpperCase() + t.slice(1) : t);
const mezzogiorno = (ymd) => mezzanotteRoma(ymd) + 12 * 60 * 60 * 1000;

export function giornoLungo(ymdOMs) {
  const ms = typeof ymdOMs === 'string' ? mezzogiorno(ymdOMs) : ymdOMs;
  return maiuscola(fmtGiornoLungo.format(new Date(ms)));
}

export function giornoBreve(ymd) {
  const ms = mezzogiorno(ymd);
  return {
    settimana: maiuscola(fmtGiornoBreve.format(new Date(ms)).replace('.', '')),
    numero: fmtNumero.format(new Date(ms)),
    mese: fmtMese.format(new Date(ms)).replace('.', ''),
  };
}

export function ora(ms) {
  return fmtOra.format(new Date(ms));
}

export function durataTesto(minuti) {
  if (minuti < 60) return `${minuti} minuti`;
  const h = Math.floor(minuti / 60);
  const m = minuti % 60;
  const ore = h === 1 ? '1 ora' : `${h} ore`;
  return m ? `${ore} e ${m} minuti` : ore;
}

// ---------------------------------------------------------------------------
// Dati del cliente: controlli di cortesia. Il server ricontrolla tutto.
// ---------------------------------------------------------------------------

const TELEFONO = /^[0-9+().\/\s-]+$/;
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function controllaCliente({ nome, telefono, email }) {
  const n = String(nome || '').trim();
  const t = String(telefono || '').trim();
  const e = String(email || '').trim();
  const errori = {};
  if (!n) errori.nome = 'Inserisci il tuo nome.';
  else if (n.length > LIMITI.nome) errori.nome = `Il nome può avere al massimo ${LIMITI.nome} caratteri.`;
  const cifre = t.replace(/\D/g, '').length;
  if (!t) errori.telefono = 'Inserisci un numero di telefono.';
  else if (t.length > LIMITI.telefono || !TELEFONO.test(t) || cifre < 6 || cifre > 15) {
    errori.telefono = 'Inserisci un numero di telefono valido.';
  }
  if (e && (e.length > LIMITI.email || !EMAIL.test(e))) errori.email = 'Inserisci un indirizzo email valido, oppure lascia vuoto.';
  return { valido: Object.keys(errori).length === 0, errori, dati: { nome: n, telefono: t, email: e } };
}

/**
 * Il corpo del submit. `slot` deve essere UNO degli slot dell'ultima risposta
 * del server (`elencoServer`): la pagina non inventa orari. Nessun campo oltre
 * a quelli dell'API (agente, durata, buffer, tipo e agenzia vengono dal link).
 */
export function corpoPrenotazione(submissionToken, slot, elencoServer, dati) {
  if (!submissionToken) throw new Error('submission token mancante');
  if (!slot || !elencoServer.includes(slot)) throw new Error('orario non proposto dal server');
  const corpo = {
    submission_token: submissionToken,
    start_at: slot.start_at,
    name: dati.nome,
    phone: dati.telefono,
  };
  if (dati.email) corpo.email = dati.email;
  return corpo;
}

/** La conferma del server: solo cio' che serve per dire "confermata". */
export function confermaPubblica(json) {
  if (!json || typeof json.start_at !== 'string') return null;
  const inizio = Date.parse(json.start_at);
  const fine = typeof json.end_at === 'string' ? Date.parse(json.end_at) : NaN;
  if (!Number.isFinite(inizio)) return null;
  return { inizioMs: inizio, fineMs: Number.isFinite(fine) ? fine : null };
}
