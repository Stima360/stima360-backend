// STIMA360 OS — scrivania/viva-model.js
//
// SCRIVANIA VIVA (Fase A): le regole del benvenuto, del radar, del briefing e
// dei consigli. Funzioni pure sui dati GIA' restituiti dalle API esistenti e
// gia' filtrati dal server per ruolo: nessuna rete, nessun DOM, nessuna
// scrittura, nessun modello esterno. Ogni frase nasce da un conteggio o da un
// record visibile nella pagina; nessuna affermazione commerciale inventata.
//
// LE FONTI DEL RADAR (e perche' sono sicure)
//   * venditori con ricontatto scaduto e venditori senza prossima azione:
//     GET /api/crm/sellers?view=overdue|no_action (lead del venditore, ristretti
//     all'agente dal server; il titolare vede l'agenzia). Il punteggio di
//     intenzione di vendita, che la stessa risposta porta, NON si usa.
//   * acquisizioni da riprendere: GET /api/acquisitions (ristrette all'agente dal
//     server). «Da riprendere» = stato aperto, nessuna attivita' da almeno
//     ACQUISITION_IDLE_DAYS giorni, nessun appuntamento futuro gia' fissato.
//   * richieste di valutazione dal sito senza agente: SOLO per titolare e
//     amministratore, dai lead che il server gli mostra (a un agente i lead non
//     assegnati non arrivano mai). Nessuna assegnazione automatica.

import { FRASI } from './frasi.js';

export const TIMEZONE = 'Europe/Rome';
export const RADAR_MAX = 5;
/** Giorni senza attivita' perche' un'acquisizione aperta sia «da riprendere». */
export const ACQUISITION_IDLE_DAYS = 7;
/** Un appuntamento «a breve»: entro questi minuti. */
export const SOON_MINUTES = 120;

const DAY_MS = 86400000;

export const ACQUISITION_STATUS_LABELS = Object.freeze({
  appointment_set: 'Appuntamento fissato',
  inspection_done: 'Sopralluogo effettuato',
  valuation_presented: 'Valutazione presentata',
  mandate_negotiation: 'Trattativa per l’incarico',
});
export const OPEN_ACQUISITION_STATUSES = Object.freeze(Object.keys(ACQUISITION_STATUS_LABELS));

const ORA_ROMA = new Intl.DateTimeFormat('en-GB', { timeZone: TIMEZONE, hour: '2-digit', hourCycle: 'h23' });
const GIORNO_ROMA = new Intl.DateTimeFormat('en-CA', { timeZone: TIMEZONE, year: 'numeric', month: '2-digit', day: '2-digit' });

function ms(value) {
  if (value === null || value === undefined || value === '') return null;
  const t = value instanceof Date ? value.getTime() : (typeof value === 'number' ? value : Date.parse(value));
  return Number.isFinite(t) ? t : null;
}

export function romeHour(now = new Date()) {
  return Number(ORA_ROMA.format(now)) % 24;
}

/** 'mattina' fino alle 13, 'pomeriggio' fino alle 18, 'sera' poi (ora di Roma). */
export function dayPeriod(now = new Date()) {
  const h = romeHour(now);
  if (h < 13) return 'mattina';
  if (h < 18) return 'pomeriggio';
  return 'sera';
}

/** Buongiorno fino alle 13, buon pomeriggio fino alle 18, buonasera poi (ora di Roma). */
export function greeting(now = new Date()) {
  return { mattina: 'Buongiorno', pomeriggio: 'Buon pomeriggio', sera: 'Buonasera' }[dayPeriod(now)];
}

/** Il segno dell'ora: sole, tramonto, luna (nome e tracciati SVG, 24x24). */
export function timeSign(now = new Date()) {
  return {
    mattina: { name: 'sole', paths: ['M12 7a5 5 0 1 0 0 10 5 5 0 0 0 0-10', 'M12 2v2', 'M12 20v2', 'M4.9 4.9l1.4 1.4',
      'M17.7 17.7l1.4 1.4', 'M2 12h2', 'M20 12h2', 'M4.9 19.1l1.4-1.4', 'M17.7 6.3l1.4-1.4'] },
    pomeriggio: { name: 'tramonto', paths: ['M7 16a5 5 0 0 1 10 0', 'M12 4v3', 'M3 16h2', 'M19 16h2', 'M5.6 9.6l1.4 1.4',
      'M17 11l1.4-1.4', 'M2 20h20'] },
    sera: { name: 'luna', paths: ['M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5', 'M17 3l.6 1.4L19 5l-1.4.6L17 7l-.6-1.4L15 5l1.4-.6z'] },
  }[dayPeriod(now)];
}

/** La frase del giorno: cambia ogni giorno di Roma, resta la stessa per tutta la giornata. */
export function quoteOfDay(now = new Date(), frasi = FRASI) {
  if (!frasi.length) return '';
  const p = {};
  for (const x of GIORNO_ROMA.formatToParts(now)) p[x.type] = x.value;
  const giorno = Math.floor(Date.UTC(Number(p.year), Number(p.month) - 1, Number(p.day)) / DAY_MS);
  return frasi[((giorno % frasi.length) + frasi.length) % frasi.length];
}

/**
 * Il nome da salutare, solo se affidabile. Il server compone «Nome Cognome» dai
 * campi dell'operatore e, se mancano, ripiega sulla parte locale dell'email: un
 * valore con cifre, punti, chiocciole o trattini bassi, o tutto minuscolo, non
 * e' un nome di persona e non si mostra. Con nome e cognome si saluta per nome.
 */
export function reliableName(name) {
  const testo = String(name || '').trim().replace(/\s+/g, ' ');
  if (!testo || /[\d@._]/.test(testo)) return '';
  const parole = testo.split(' ');
  if (!parole.every((w) => /^[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’-]*$/.test(w))) return '';
  return parole.length === 2 ? parole[0] : testo;
}

function giorni(dal, now) {
  const t = ms(dal);
  if (t === null) return null;
  return Math.max(0, Math.floor((now.getTime() - t) / DAY_MS));
}

function quandoFa(n) {
  if (n === null) return '';
  if (n === 0) return 'oggi';
  if (n === 1) return 'ieri';
  return `${n} giorni fa`;
}

export function sellerPlace(property) {
  if (!property) return '';
  const via = [property.address, property.civic_number].filter(Boolean).join(' ').trim();
  return [via, property.city].filter(Boolean).join(', ') || String(property.title || '').trim();
}

// ---------------------------------------------------------------------------
// radar
// ---------------------------------------------------------------------------

/** Venditore con ricontatto scaduto (vista «overdue» del server). */
export function overdueSellerItem(s, now = new Date()) {
  const na = s && s.next_action;
  if (!na || !na.overdue) return null;
  const n = giorni(na.due_at, now);
  return {
    kind: 'seller_overdue', key: `seller:${s.lead_id}`, agentId: s.assigned_agent_id ?? null,
    title: (s.contact && s.contact.name) || sellerPlace(s.property) || 'Venditore',
    place: sellerPlace(s.property),
    reason: na.source === 'task' && na.title ? `Ricontatto previsto: «${na.title}»` : 'Ricontatto previsto e non ancora fatto',
    since: n === null ? '' : (n === 0 ? 'scaduto oggi' : `in ritardo di ${n} ${n === 1 ? 'giorno' : 'giorni'}`),
    sortDays: n ?? 0,
    phone: (s.contact && s.contact.phone) || '',
    href: s.contact && s.contact.id ? `#/contatti/${encodeURIComponent(s.contact.id)}` : '#/venditori',
    hrefLabel: s.contact && s.contact.id ? 'Apri contatto' : 'Apri Venditori',
    propertyHref: s.property && s.property.id ? `#/immobili/${encodeURIComponent(s.property.id)}` : '',
  };
}

/** Venditore attivo senza prossima azione (vista «no_action» del server). */
export function noActionSellerItem(s, now = new Date()) {
  if (!s || s.next_action) return null;
  const ultimo = s.last_interaction && s.last_interaction.occurred_at;
  const n = giorni(ultimo, now);
  return {
    kind: 'seller_no_action', key: `seller:${s.lead_id}`, agentId: s.assigned_agent_id ?? null,
    title: (s.contact && s.contact.name) || sellerPlace(s.property) || 'Venditore',
    place: sellerPlace(s.property),
    reason: 'Nessuna prossima azione definita',
    since: n === null ? 'nessun contatto registrato' : `ultimo contatto ${quandoFa(n)}`,
    sortDays: n === null ? 100000 : n,
    phone: (s.contact && s.contact.phone) || '',
    href: s.contact && s.contact.id ? `#/contatti/${encodeURIComponent(s.contact.id)}` : '#/venditori',
    hrefLabel: s.contact && s.contact.id ? 'Apri contatto' : 'Apri Venditori',
    propertyHref: s.property && s.property.id ? `#/immobili/${encodeURIComponent(s.property.id)}` : '',
  };
}

/** Acquisizione aperta, ferma da almeno ACQUISITION_IDLE_DAYS, senza un appuntamento futuro. */
export function idleAcquisitionItem(a, now = new Date()) {
  if (!a || !OPEN_ACQUISITION_STATUSES.includes(a.status)) return null;
  const inizio = ms(a.appointment_start_at);
  const aperto = ['requested', 'scheduled', 'confirmed'].includes(a.appointment_status);
  if (aperto && inizio !== null && inizio > now.getTime()) return null;   // c'e' gia' un appuntamento in arrivo
  const n = giorni(a.last_activity_at, now);
  if (n === null || n < ACQUISITION_IDLE_DAYS) return null;
  const via = [a.property_address, a.property_civic_number].filter(Boolean).join(' ').trim();
  return {
    kind: 'acquisition_idle', key: `acquisition:${a.id}`, agentId: a.assigned_agent_id ?? null,
    title: a.owner_name || a.property_title || 'Acquisizione',
    place: [via, a.property_city].filter(Boolean).join(', ') || a.property_title || '',
    reason: `${ACQUISITION_STATUS_LABELS[a.status]}: nessun aggiornamento`,
    since: `fermo da ${n} giorni`,
    sortDays: n,
    phone: '',
    href: `#/acquisizioni/${encodeURIComponent(a.id)}`,
    hrefLabel: 'Apri acquisizione',
    propertyHref: a.property_id ? `#/immobili/${encodeURIComponent(a.property_id)}` : '',
  };
}

/** Richiesta di valutazione dal sito senza agente: SOLO per chi vede l'agenzia. */
export function unassignedValuationItem(lead, contact, now = new Date()) {
  if (!lead || lead.status !== 'open' || lead.source !== 'public_stima' || lead.assigned_agent_id) return null;
  const n = giorni(lead.created_at, now);
  const nome = contact ? String(contact.display_name || [contact.first_name, contact.last_name].filter(Boolean).join(' ')
    || contact.company_name || '').trim() : '';
  return {
    kind: 'valuation_unassigned', key: `lead:${lead.id}`, agentId: null,
    title: nome || 'Nuova richiesta di valutazione',
    place: '',
    reason: 'Richiesta di valutazione dal sito, senza agente assegnato',
    since: n === null ? '' : `arrivata ${quandoFa(n)}`,
    sortDays: n ?? 0,
    phone: (contact && contact.phone) || '',
    href: lead.contact_id ? `#/contatti/${encodeURIComponent(lead.contact_id)}` : '#/contatti',
    hrefLabel: 'Apri contatto',
    propertyHref: '',
  };
}

/**
 * Il radar: al massimo RADAR_MAX voci, a turno fra le famiglie nell'ordine di
 * importanza (ricontatti scaduti, valutazioni da assegnare, acquisizioni ferme,
 * venditori senza prossima azione); dentro una famiglia, la situazione piu'
 * vecchia prima. `total` e' il numero di tutte le voci trovate.
 */
export function buildRadar({ overdue = [], valuations = [], acquisitions = [], noAction = [] } = {}, max = RADAR_MAX) {
  const famiglie = [overdue, valuations, acquisitions, noAction]
    .map((f) => f.filter(Boolean).slice().sort((a, b) => b.sortDays - a.sortDays));
  const visti = new Set();
  const scelte = [];
  let giro = 0;
  while (scelte.length < max && famiglie.some((f) => f.length > giro)) {
    for (const f of famiglie) {
      const voce = f[giro];
      if (voce && !visti.has(voce.key) && scelte.length < max) {
        visti.add(voce.key);
        scelte.push(voce);
      }
    }
    giro += 1;
  }
  const total = new Set(famiglie.flat().map((v) => v.key)).size;
  return { items: scelte, total, counts: {
    overdue: famiglie[0].length, valuations: famiglie[1].length, acquisitions: famiglie[2].length,
    noAction: famiglie[3].length } };
}

// ---------------------------------------------------------------------------
// briefing e consigli
// ---------------------------------------------------------------------------

const CHIUSI = ['completed', 'cancelled', 'no_show', 'rescheduled'];

function plurale(n, uno, molti) {
  return `${n} ${n === 1 ? uno : molti}`;
}

/** Il prossimo appuntamento di oggi ancora da fare (ordine per orario). */
export function nextAppointment(agendaToday, now = new Date()) {
  return (agendaToday || []).find((a) => !CHIUSI.includes(a.status) && (ms(a.start_at) ?? 0) > now.getTime()) || null;
}

/**
 * Due o tre frasi sulla giornata, in tono naturale e secondo l'ora, ma SOLO da
 * conteggi e record della pagina. Mai «tutto concluso» se restano appuntamenti
 * o attivita' aperte. `agency` = il titolare guarda l'agenzia.
 */
export function briefing({ agendaToday = [], agendaTomorrow = [], radar = { items: [], total: 0 }, overdue = 0, today = 0,
  toVerify = 0, agency = false, now = new Date(), typeLabel = () => '', timeLabel = () => '' } = {}) {
  const periodo = dayPeriod(now);
  const frasi = [];
  const visite = agendaToday.filter((a) => a.type === 'buyer_visit').length;
  const prossimo = nextAppointment(agendaToday, now);
  const restanti = agendaToday.filter((a) => !CHIUSI.includes(a.status) && (ms(a.start_at) ?? 0) > now.getTime()).length;
  const soggetto = agency ? 'in agenzia' : '';
  const hai = agency ? (agendaToday.length === 1 ? 'in agenzia c’è' : 'in agenzia ci sono') : 'hai';
  const domani = agendaTomorrow.length
    ? ` Domani ${agency ? (agendaTomorrow.length === 1 ? 'è già in agenda' : 'sono già in agenda') : 'hai già'} ${plurale(agendaTomorrow.length, 'appuntamento', 'appuntamenti')}.`
    : '';

  // 1. l'agenda, secondo l'ora
  if (!agendaToday.length) {
    if (periodo === 'sera') {
      frasi.push(`La giornata è finita${soggetto ? ' ' + soggetto : ''}, senza appuntamenti in agenda.${domani || ' Domani ci aspettano nuove opportunità.'}`);
    } else if (periodo === 'mattina') {
      frasi.push(`Oggi l’agenda ${agency ? 'dell’agenzia ' : ''}è libera: una buona occasione per sentire qualche cliente.${domani}`);
    } else {
      frasi.push(`Pomeriggio senza appuntamenti${soggetto ? ' ' + soggetto : ''}: c’è spazio per qualche telefonata.${domani}`);
    }
  } else if (prossimo) {
    const chi = prossimo.contact_name ? ` con ${prossimo.contact_name}` : '';
    const tipo = (typeLabel(prossimo.type) || 'appuntamento').toLowerCase();
    const quanti = `${plurale(agendaToday.length, 'appuntamento', 'appuntamenti')}${visite ? `, di cui ${plurale(visite, 'visita', 'visite')}` : ''}`;
    if (periodo === 'mattina') {
      frasi.push(`Oggi ${hai} ${quanti}: il prossimo è alle ${timeLabel(prossimo.start_at)}, ${tipo}${chi}.${domani}`);
    } else if (periodo === 'pomeriggio') {
      const resta = restanti === 1 ? (agency ? 'In agenzia resta' : 'Ti resta') : (agency ? 'In agenzia restano' : 'Ti restano');
      frasi.push(`${restanti === agendaToday.length ? `Oggi ${hai} ${quanti}` : `${resta} ${plurale(restanti, 'appuntamento', 'appuntamenti')} su ${agendaToday.length}`}: il prossimo è alle ${timeLabel(prossimo.start_at)}, ${tipo}${chi}.${domani}`);
    } else {
      frasi.push(`${agency ? 'In agenzia c’è' : 'Hai'} ancora ${plurale(restanti, 'appuntamento', 'appuntamenti')} stasera: alle ${timeLabel(prossimo.start_at)}, ${tipo}${chi}.${domani}`);
    }
  } else {
    const quanti = `${plurale(agendaToday.length, 'appuntamento', 'appuntamenti')}${visite ? `, di cui ${plurale(visite, 'visita', 'visite')}` : ''}`;
    const uno = agendaToday.length === 1;
    if (periodo === 'sera') {
      frasi.push(`La giornata è finita: ${agency ? (uno ? 'in agenzia c’è stato' : 'in agenzia ci sono stati') : 'hai avuto'} ${quanti}, ${uno ? 'ormai' : 'tutti'} alle spalle.${domani || ' Domani ci aspettano nuove opportunità.'}`);
    } else if (uno) {
      frasi.push(`${agency ? 'L’appuntamento di oggi in agenzia' : 'Il tuo appuntamento di oggi'} è già alle spalle: il resto della giornata è libero.${domani}`);
    } else {
      frasi.push(`${agency ? 'I' : 'I tuoi'} ${quanti.replace(', di cui', ' di oggi' + (agency ? ' in agenzia' : '') + ', di cui')}${visite ? ',' : (agency ? ' di oggi in agenzia' : ' di oggi')} sono già alle spalle: il resto della giornata è libero.${domani}`);
    }
  }

  // 2. il radar
  if (radar.total) {
    const primo = radar.items[0];
    const motivo = primo ? ` La prima è ${primo.title} (${primo.reason.charAt(0).toLowerCase()}${primo.reason.slice(1)}).` : '';
    frasi.push(`Intanto il Radar ha trovato ${plurale(radar.total, 'situazione interessante', 'situazioni interessanti')} da esplorare.${motivo}`);
  } else {
    frasi.push('Il Radar non ha trovato clienti in attesa: è un buon momento per coltivare nuovi contatti.');
  }

  // 3. le attivita', senza nasconderle
  if (overdue || today || toVerify) {
    const parti = [];
    if (overdue) parti.push(plurale(overdue, 'attività scaduta', 'attività scadute'));
    if (today) parti.push(plurale(today, 'attività di oggi', 'attività di oggi'));
    if (toVerify) parti.push(plurale(toVerify, 'situazione da verificare', 'situazioni da verificare'));
    const elenco = parti.length > 1 ? `${parti.slice(0, -1).join(', ')} e ${parti[parti.length - 1]}` : parti[0];
    frasi.push(`${periodo === 'sera' ? 'Restano' : 'Più in basso trovi'} ${elenco}${periodo === 'sera' ? ', più in basso' : ''}.`);
  } else {
    frasi.push('Nessuna attività scaduta e niente da verificare: tutto in ordine.');
  }
  return frasi;
}

/**
 * «Cosa mi consigli di fare oggi?»: fino a tre consigli, con il motivo e il
 * collegamento. Ordine: un appuntamento a breve; la prima opportunita' del radar;
 * l'attivita' scaduta piu' urgente; un appuntamento passato da chiudere.
 */
export function advice({ agendaToday = [], radar = { items: [] }, urgentTask = null, verify = null,
  now = new Date(), typeLabel = () => '', timeLabel = () => '', agendaHref = () => '#/agenda' } = {}) {
  const out = [];
  const prossimo = nextAppointment(agendaToday, now);
  if (prossimo && (ms(prossimo.start_at) - now.getTime()) <= SOON_MINUTES * 60000) {
    out.push({
      kind: 'appointment',
      title: `Preparati per l’appuntamento delle ${timeLabel(prossimo.start_at)}`,
      reason: [typeLabel(prossimo.type) || 'Appuntamento', prossimo.contact_name, prossimo.place].filter(Boolean).join(' · '),
      href: agendaHref(prossimo), label: 'Apri in Agenda',
    });
  }
  const primo = radar.items && radar.items[0];
  if (primo) {
    out.push({
      kind: 'opportunity',
      title: primo.kind === 'valuation_unassigned' ? `Assegna la richiesta di ${primo.title}` : `Ricontatta ${primo.title}`,
      reason: [primo.reason, primo.since].filter(Boolean).join(' · '),
      href: primo.href, label: primo.hrefLabel,
    });
  }
  if (urgentTask) {
    out.push({
      kind: 'task', title: `Chiudi «${urgentTask.title}»`, reason: urgentTask.reason,
      href: urgentTask.href || '', label: urgentTask.href ? 'Apri contatto' : 'Mostra le attività',
    });
  }
  if (out.length < 3 && verify) {
    out.push({
      kind: 'verify', title: `Registra l’esito: ${verify.title}`, reason: verify.reason,
      href: verify.href, label: 'Apri in Agenda',
    });
  }
  return out.slice(0, 3);
}
