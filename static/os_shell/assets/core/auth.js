// STIMA360 OS — auth.js
// Le credenziali restano ESCLUSIVAMENTE in una variabile di modulo, in memoria JS.
// Nessuna scrittura in localStorage, sessionStorage o cookie da parte del frontend.
// Riusa lo stesso endpoint di verifica già usato dalle admin legacy (/api/admin/check)
// e le stesse credenziali ADMIN_USER/ADMIN_PASS gia' verificate da require_admin e
// require_owner_admin lato backend (nessuna modifica ai due file, nessun nuovo
// meccanismo di sessione server-side).

let credentials = null;
const listeners = new Set();

function notify() {
  for (const fn of listeners) fn(credentials);
}

export function onAuthChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function getCredentials() {
  return credentials;
}

export function isAuthenticated() {
  return credentials !== null;
}

export async function login(username, password) {
  let response;
  try {
    response = await fetch('/api/admin/check', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user: username, password }),
    });
  } catch (networkError) {
    throw new Error('Impossibile contattare il server. Verifica la connessione.');
  }
  if (!response.ok) {
    if (response.status === 401) {
      throw new Error('Credenziali non valide.');
    }
    throw new Error('Servizio amministrativo non disponibile.');
  }
  credentials = { username, password };
  notify();
}

export function logout() {
  credentials = null;
  notify();
}
