// STIMA360 OS — agenda/agenda-lookup.js (A30-5)
//
// Le SOLE letture fuori da `/api/appointments` che l'Agenda fa: servono al
// dialog "Nuovo appuntamento" per collegare l'appuntamento a entita' CRM che
// esistono gia', senza chiedere all'operatore un ID numerico.
//
//   * cliente  -> components/contact-picker.js (GET /api/core/contacts?search=),
//                 riusato cosi' com'e': non vive qui;
//   * lead     -> GET /api/core/leads?contact_id=  (solo i lead DEL cliente
//                 scelto: una combinazione lead/cliente incoerente non si puo'
//                 nemmeno comporre, e il backend la rifiuta comunque con
//                 LINK_MISMATCH);
//   * immobile -> GET /api/property/properties?search=.
//
// Tutte GET, tutte con la sessione cookie di `core/api-client.js` (nessun
// Authorization, 401 -> login). L'agenzia la decide il server: ogni endpoint
// e' gia' limitato all'agenzia della sessione. Nessuna copia locale delle
// entita': l'appuntamento porta solo gli ID autorevoli.
//
// La stima si cerca con `GET /api/appointments/lookups/stime` (agenda-api.js,
// perche' e' una rotta dell'Agenda); qui c'e' solo la sua etichetta.

import { apiGet } from '../core/api-client.js';

const LIMITE_LEAD = 20;
const LIMITE_IMMOBILI = 10;

function positivo(valore) {
  const n = Number(valore);
  return Number.isInteger(n) && n > 0 ? n : null;
}

/** I lead di un cliente, dal piu' recente. */
export async function leadsOfContact(contactId) {
  const id = positivo(contactId);
  if (id === null) return [];
  const dati = await apiGet(`/api/core/leads?contact_id=${id}&limit=${LIMITE_LEAD}`);
  return Array.isArray(dati && dati.items) ? dati.items : [];
}

/** Ricerca immobili per testo (titolo, codice, indirizzo). */
export async function searchProperties(term) {
  const testo = String(term || '').trim();
  if (!testo) return [];
  const dati = await apiGet(
    `/api/property/properties?search=${encodeURIComponent(testo)}&limit=${LIMITE_IMMOBILI}`);
  return Array.isArray(dati && dati.items) ? dati.items : [];
}

const DATA_BREVE = new Intl.DateTimeFormat('it-IT', {
  timeZone: 'Europe/Rome', day: '2-digit', month: '2-digit', year: 'numeric',
});

const STATI_LEAD = Object.freeze({ open: 'aperto', paused: 'in pausa', closed: 'chiuso' });

/**
 * L'etichetta di un lead: cio' che serve a distinguerne due dello stesso
 * cliente (pipeline, fase, stato, da quando). Nessun ID.
 */
export function leadLabel(lead) {
  if (!lead) return '';
  const quando = lead.created_at && !Number.isNaN(Date.parse(lead.created_at))
    ? `dal ${DATA_BREVE.format(new Date(lead.created_at))}` : '';
  const stato = STATI_LEAD[lead.status] || lead.status || '';
  const parti = [lead.pipeline, lead.stage, stato, quando].filter(Boolean);
  return parti.length ? parti.join(' · ') : 'Lead senza descrizione';
}

/** Titolo e riga secondaria di un immobile (codice, indirizzo, citta'). */
export function propertyLabel(property) {
  if (!property) return { title: '', detail: '' };
  return {
    title: property.title || property.code || 'Immobile senza titolo',
    detail: [property.code, property.address, property.city].filter(Boolean).join(' · '),
  };
}

/**
 * L'etichetta di una stima: chi, dove, cosa, quando. Nessun ID, nessun
 * recapito. Il valore stimato non c'e': `stime` non lo conserva.
 */
export function stimaLabel(stima) {
  if (!stima) return { title: '', detail: '' };
  const nominativo = [stima.nome, stima.cognome].filter(Boolean).join(' ');
  const indirizzo = [[stima.via, stima.civico].filter(Boolean).join(' '), stima.comune]
    .filter(Boolean).join(', ');
  const immobile = [stima.tipologia, stima.mq ? `${stima.mq} m²` : ''].filter(Boolean).join(' ');
  const quando = stima.data && !Number.isNaN(Date.parse(stima.data))
    ? `stima del ${DATA_BREVE.format(new Date(stima.data))}` : '';
  return {
    title: nominativo || indirizzo || 'Stima senza nominativo',
    detail: [nominativo ? indirizzo : '', immobile, quando].filter(Boolean).join(' · '),
  };
}
