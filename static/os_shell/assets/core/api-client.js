// STIMA360 OS — api-client.js
// Client fetch unico e condiviso. Sostituisce, solo all'interno di questa nuova
// App Shell, le implementazioni api()/req()/request() duplicate nelle 6 admin
// legacy (che restano invariate). Chiama sempre path API completi e reali
// (verificati nei router: /api/core, /api/property, /api/buy, /api/match,
// /api/crm, /api/proposals, /api/owner/admin, /api/owner/portal, /api/flow) —
// nessun alias backend introdotto.

import { getCredentials, logout } from './auth.js';

function encodeBasic(username, password) {
  const bytes = new TextEncoder().encode(`${username}:${password}`);
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return `Basic ${btoa(binary)}`;
}

async function request(path, options = {}) {
  const creds = getCredentials();
  const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };
  if (creds) {
    headers.Authorization = encodeBasic(creds.username, creds.password);
  }

  let response;
  try {
    response = await fetch(path, { ...options, headers });
  } catch (networkError) {
    throw new Error('Impossibile contattare il server. Verifica la connessione.');
  }

  if (response.status === 401) {
    logout();
    throw new Error('Sessione non valida. Effettua di nuovo il login.');
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
    throw new Error(detail);
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
