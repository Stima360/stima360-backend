// STIMA360 OS — api-client.js
// Client fetch unico e condiviso. Chiama sempre path API completi e reali
// (verificati nei router: /api/core, /api/property, /api/buy, /api/match,
// /api/crm, /api/proposals, /api/sales, /api/flow, /api/property-watch,
// /api/next-best-action) — nessun alias backend introdotto.
//
// P26-3: NESSUN HEADER Authorization.
//
// Prima ogni richiesta ricostruiva `Authorization: Basic` dalle credenziali
// tenute in memoria. Adesso l'autenticazione e' il cookie di sessione emesso da
// /api/operator-auth/login, che il browser allega da solo grazie a
// `credentials: 'include'`. Il cookie e' HttpOnly: questo file non puo'
// leggerlo ne' copiarlo, ed e' esattamente il punto.
//
// Non c'e' nessun `agency_id` qui, in nessuna forma. L'agenzia la decide il
// server dalla sessione; il client non la conosce e non la puo' chiedere.

import { sessionExpired } from './auth.js';

async function request(path, options = {}) {
  const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };

  let response;
  try {
    response = await fetch(path, { ...options, headers, credentials: 'include' });
  } catch (networkError) {
    throw new Error('Impossibile contattare il server. Verifica la connessione.');
  }

  if (response.status === 401) {
    // Sessione assente, scaduta o revocata. `sessionExpired` azzera lo stato e
    // fa comparire la schermata di login; NON tenta un refresh ne' un secondo
    // login, che e' il modo in cui questi client finiscono in un ciclo.
    sessionExpired();
    const error = new Error('Sessione scaduta. Effettua di nuovo il login.');
    error.status = 401;
    throw error;
  }
  if (response.status === 204) {
    return null;
  }

  let data = null;
  try {
    data = await response.json();
  } catch (_parseError) {
    data = null;
  }

  if (!response.ok) {
    const detail = data && typeof data.detail === 'string' ? data.detail : `Errore ${response.status}`;
    const error = new Error(detail);
    error.status = response.status;
    throw error;
  }

  return data;
}

export function apiGet(path) {
  return request(path, { method: 'GET' });
}

export function apiPost(path, body) {
  return request(path, { method: 'POST', body: JSON.stringify(body) });
}

export function apiPatch(path, body) {
  return request(path, { method: 'PATCH', body: JSON.stringify(body) });
}

export function apiDelete(path) {
  return request(path, { method: 'DELETE' });
}
