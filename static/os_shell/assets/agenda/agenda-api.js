// STIMA360 OS — agenda/agenda-api.js (A30-4)
//
// L'UNICO punto della OS Shell che chiama `/api/appointments`. Nessun altro
// endpoint: l'Agenda non legge stime, visite acquirente o altro.
//
// PERCHE' UN `request` PROPRIO E NON `core/api-client.js`
//
// L'API A30-2 risponde agli errori con `{"detail", "code", ...}` e alcuni
// codici portano dati che la UI deve mostrare: `conflicts` e `alternatives`
// su APPOINTMENT_CONFLICT, `current_version` su VERSION_CONFLICT. Il client
// condiviso conserva solo `detail` e `status`, e `core/*.js` non si tocca in
// A30-4. Qui si ripete il suo contratto, riga per riga:
//
//   * nessun header Authorization - la sessione e' il cookie HttpOnly, che il
//     browser allega grazie a `credentials: 'include'` (P26-3, P26-5);
//   * un 401 chiama `sessionExpired()` di core/auth.js, che riporta al login,
//     senza ritentare;
//   * nessun `agency_id`, nessun attore: li decide il server.

import { sessionExpired } from '../core/auth.js';

const BASE = '/api/appointments';

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
    throw errore;
  }

  if (risposta.status === 401) {
    sessionExpired();
    const errore = new Error('Sessione scaduta. Effettua di nuovo il login.');
    errore.status = 401;
    throw errore;
  }

  let dati = null;
  try {
    dati = await risposta.json();
  } catch (_errore) {
    dati = null;
  }

  if (!risposta.ok) {
    const dettaglio = dati && typeof dati.detail === 'string' ? dati.detail : '';
    const errore = new Error(dettaglio || `Errore ${risposta.status}`);
    errore.status = risposta.status;
    errore.detail = dettaglio;
    errore.code = dati && typeof dati.code === 'string' ? dati.code : '';
    errore.conflicts = dati && Array.isArray(dati.conflicts) ? dati.conflicts : [];
    errore.alternatives = dati && Array.isArray(dati.alternatives) ? dati.alternatives : [];
    errore.currentVersion = dati && Number.isInteger(dati.current_version)
      ? dati.current_version : null;
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
    q.set(chiave, Array.isArray(valore) ? valore.join(',') : String(valore));
  }
  const testo = q.toString();
  return testo ? `?${testo}` : '';
}

// -- letture ----------------------------------------------------------------

/** GET /calendar: una chiamata aggregata per intervallo. */
export function getCalendar({ from, to, agents, types, statuses, showColleagues } = {}) {
  return request('GET', `/calendar${query({
    from, to, agents, types, statuses,
    show_colleagues: showColleagues === undefined ? undefined : String(Boolean(showColleagues)),
  })}`);
}

/** GET lista (la vista Lista). */
export function getList({ from, to, statuses, types, limit = 200, offset = 0 } = {}) {
  return request('GET', query({ from, to, statuses, types, limit, offset }));
}

export function getAgents() {
  return request('GET', '/agents');
}

export function getAvailability({ userId, from, to, duration, step = 30, excludeAppointmentId }) {
  return request('GET', `/availability${query({
    user_id: id(userId), from, to, duration, step,
    exclude_appointment_id: excludeAppointmentId ? id(excludeAppointmentId) : undefined,
  })}`);
}

export function checkAvailability(body) {
  return request('POST', '/availability/check', body);
}

export function getAppointment(appointmentId) {
  return request('GET', `/${id(appointmentId)}`);
}

export function getEvents(appointmentId) {
  return request('GET', `/${id(appointmentId)}/events`);
}

// -- scritture --------------------------------------------------------------

/** POST: `client_request_id` resta lo stesso nei tentativi (idempotenza A30-2 §6). */
export function createAppointment(body) {
  return request('POST', '', body);
}

export function patchAppointment(appointmentId, body) {
  return request('PATCH', `/${id(appointmentId)}`, body);
}

const PERCORSI_AZIONE = Object.freeze({
  schedule: 'schedule', confirm: 'confirm', reschedule: 'reschedule', reassign: 'reassign',
  cancel: 'cancel', complete: 'complete', no_show: 'no-show',
});

/** POST /{id}/<azione> per schedule, confirm, reschedule, reassign, cancel,
 *  complete, no_show. Ogni corpo porta `version` (lock ottimistico). */
export function runAction(appointmentId, action, body) {
  const percorso = PERCORSI_AZIONE[action];
  if (!percorso) throw new Error(`Azione non supportata: ${action}`);
  return request('POST', `/${id(appointmentId)}/${percorso}`, body);
}
