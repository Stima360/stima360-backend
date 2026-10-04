// STIMA360 OS — census/census-api.js (CENSIMENTO-1 Fase 4)
//
// L'UNICO punto della OS Shell che chiama le rotte del censimento di
// `property/router.py` (Fase 3): `/api/property/buildings…`,
// `/api/property/census/units` e `/api/property/properties/{id}/census|
// pertinenze|accessories|take-in-charge|undo-create`. Niente altro: la scheda
// generica dell'immobile continua a usare `core/api-client.js`.
//
// PERCHE' UN `request` PROPRIO E NON `core/api-client.js`
//
// Le rotte della Fase 3 rispondono agli errori con `{detail, code, ...extra}`
// e la UI deve leggere il codice e i dati che lo accompagnano: `similar` su
// SIMILAR_FOUND (il banner «Simile a IMM-402 · Apri / Salva comunque»),
// `existing` su CADASTRAL_DUPLICATE («gia' censito come IMM-405 · Apri»),
// `linked` su UNDO_NOT_POSSIBLE. Il client condiviso conserva solo `detail` e
// `status`, e `core/*.js` non si tocca (stessa scelta del client dell'Agenda).
// Qui si ripete il suo contratto, riga per riga:
//
//   * nessun header Authorization - la sessione e' il cookie HttpOnly, che il
//     browser allega grazie a `credentials: 'include'` (P26-3, P26-5);
//   * un 401 chiama `sessionExpired()` di core/auth.js, che riporta al login,
//     senza ritentare;
//   * nessun `agency_id`, nessun attore: li decide il server.
//
// IDEMPOTENZA (CENSIMENTO-0 §0 p.5): ogni creazione porta un
// `client_request_id` UUID generato dal client al PRIMO tentativo e riusato
// nei retry (`newClientRequestId`). Stessa chiave + stesso corpo = stessa riga
// (`replica: true`); stessa chiave + corpo diverso = 409 IDEMPOTENCY_KEY_REUSED.
//
// ESITO INCERTO (REV 2, R7/R8): un errore di rete PRIMA degli header
// (`NETWORK`) o un corpo illeggibile DOPO un HTTP 2xx (`RESPONSE_LOST`) non
// dicono se il server ha scritto. L'errore porta `uncertain: true`: chi
// chiama conserva chiave e corpo inviati e ritenta con la STESSA chiave (la
// replica restituisce la riga gia' scritta, mai un doppione). Un 204 e' un
// successo senza corpo e resta `null`.

import { sessionExpired } from '../core/auth.js';

const BASE = '/api/property';

async function request(method, path, body) {
  const opzioni = {
    method,
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
  };
  if (body !== undefined) opzioni.body = JSON.stringify(body);

  let risposta;
  try {
    risposta = await fetch(`${BASE}${path}`, opzioni);
  } catch (_errore) {
    const errore = new Error('Impossibile contattare il server. Verifica la connessione.');
    errore.status = 0;
    errore.code = 'NETWORK';
    errore.uncertain = true;          // la richiesta puo' essere arrivata: la risposta no
    throw errore;
  }

  if (risposta.status === 401) {
    sessionExpired();
    const errore = new Error('Sessione scaduta. Effettua di nuovo il login.');
    errore.status = 401;
    errore.code = 'UNAUTHENTICATED';
    throw errore;
  }
  if (risposta.status === 204) return null;

  let dati = null;
  let corpoIlleggibile = false;
  try {
    dati = await risposta.json();
  } catch (_errore) {
    dati = null;
    corpoIlleggibile = true;
  }

  if (risposta.ok && corpoIlleggibile) {
    // HTTP 2xx ma il corpo si e' interrotto: l'operazione e' probabilmente
    // riuscita e la riga creata non e' arrivata. NON e' un successo (R8).
    const errore = new Error('Risposta del server incompleta.');
    errore.status = risposta.status;
    errore.code = 'RESPONSE_LOST';
    errore.uncertain = true;
    throw errore;
  }

  if (!risposta.ok) {
    const dettaglio = dati && typeof dati.detail === 'string' ? dati.detail : '';
    const errore = new Error(dettaglio || `Errore ${risposta.status}`);
    errore.status = risposta.status;
    errore.detail = dettaglio;
    errore.code = dati && typeof dati.code === 'string' ? dati.code : '';
    errore.similar = dati && Array.isArray(dati.similar) ? dati.similar : [];
    errore.existing = dati && dati.existing && typeof dati.existing === 'object' ? dati.existing : null;
    errore.linked = dati && Array.isArray(dati.linked) ? dati.linked : [];
    throw errore;
  }
  return dati;
}

function id(valore) {
  const n = Number(valore);
  if (!Number.isInteger(n) || n <= 0) throw new Error('Identificativo non valido.');
  return n;
}

function query(parametri) {
  const q = new URLSearchParams();
  for (const [chiave, valore] of Object.entries(parametri)) {
    if (valore === undefined || valore === null || valore === '') continue;
    q.set(chiave, String(valore));
  }
  const s = q.toString();
  return s ? `?${s}` : '';
}

/** UUID v4 per `client_request_id`: generato una volta per tentativo logico e
 *  riusato nei retry. Stesso ripiego dell'Agenda per browser senza randomUUID. */
export function newClientRequestId() {
  if (globalThis.crypto && typeof globalThis.crypto.randomUUID === 'function') {
    return globalThis.crypto.randomUUID();
  }
  const b = globalThis.crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = [...b].map((x) => x.toString(16).padStart(2, '0')).join('');
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

// --- Edifici ---------------------------------------------------------------------

export function listBuildings({ search, city, limit = 50, offset = 0 } = {}) {
  return request('GET', `/buildings${query({ search, city, limit, offset })}`);
}

export function getBuilding(buildingId) {
  return request('GET', `/buildings/${id(buildingId)}`);
}

export function createBuilding(payload) {
  return request('POST', '/buildings', payload);
}

export function updateBuilding(buildingId, payload) {
  return request('PATCH', `/buildings/${id(buildingId)}`, payload);
}

// --- Unita' di censimento -------------------------------------------------------

export function createUnit(payload) {
  return request('POST', '/census/units', payload);
}

export function getCensus(propertyId) {
  return request('GET', `/properties/${id(propertyId)}/census`);
}

export function takeInCharge(propertyId, includePertinenze) {
  return request('POST', `/properties/${id(propertyId)}/take-in-charge`, { include_pertinenze: includePertinenze === true });
}

export function undoCreate(propertyId) {
  return request('POST', `/properties/${id(propertyId)}/undo-create`);
}

/** La PATCH generica dell'immobile (`PropertyUpdate`, stesse colonne della
 *  083): usata dal recupero di un invio dall'esito incerto per applicare a
 *  una riga GIA' creata le modifiche fatte dopo (R7). Nessuna rotta nuova. */
export function updateProperty(propertyId, payload) {
  return request('PATCH', `/properties/${id(propertyId)}`, payload);
}

// --- Pertinenze ------------------------------------------------------------------

export function linkPertinenza(propertyId, pertinenzaId) {
  return request('POST', `/properties/${id(propertyId)}/pertinenze/link`, { pertinenza_id: id(pertinenzaId) });
}

export function unlinkPertinenza(propertyId, pertinenzaId) {
  return request('POST', `/properties/${id(propertyId)}/pertinenze/${id(pertinenzaId)}/unlink`);
}

// --- Accessori -------------------------------------------------------------------

export function createAccessory(propertyId, payload) {
  return request('POST', `/properties/${id(propertyId)}/accessories`, payload);
}

export function updateAccessory(propertyId, accessoryId, payload) {
  return request('PATCH', `/properties/${id(propertyId)}/accessories/${id(accessoryId)}`, payload);
}

export function deleteAccessory(propertyId, accessoryId) {
  return request('DELETE', `/properties/${id(propertyId)}/accessories/${id(accessoryId)}`);
}

export function resolveAccessory(propertyId, accessoryId, payload) {
  return request('POST', `/properties/${id(propertyId)}/accessories/${id(accessoryId)}/resolve`, payload);
}

// --- «Collega esistente»: la ricerca immobili gia' esistente ----------------------
//
// Nessuna rotta nuova: `GET /api/property/properties?search=` (la stessa
// dell'elenco Immobili). Il server verifica comunque agenzia, profondita' e
// stato al momento del collegamento.
export function searchProperties(term, limit = 10) {
  return request('GET', `/properties${query({ search: term, limit })}`);
}
