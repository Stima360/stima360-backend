// STIMA360 OS — sellers/sellers-api.js (VENDITORI-1)
//
// Il client delle rotte Venditori (`crm/router.py`): GET /api/crm/sellers
// (worklist), POST /api/crm/sellers («Vende»), POST
// /api/crm/sellers/deactivate. Per tutto il resto RIUSA le rotte esistenti:
//
//   * storico: POST /api/property/properties/{id}/interactions con `lead_id` e
//     `context: 'seller'` - la STESSA riga di activities dello storico
//     immobile e del contatto (property/interactions.py);
//   * prossima azione: POST /api/core/tasks (lead + contatto + scadenza);
//   * fase: PATCH /api/core/leads/{id} {stage};
//   * presa in carico: `takeInCharge` di census/census-api.js (Fase 4).
//
// Un `request` proprio come census-api e il client dell'Agenda: gli errori di
// questo dominio portano `code` (e `candidates`) che il client condiviso non
// conserva. Stesso contratto: cookie di sessione (`credentials: 'include'`),
// 401 -> sessionExpired(), nessun agency_id dal browser.

import { sessionExpired } from '../core/auth.js';

async function request(method, url, body) {
  const opzioni = { method, headers: { 'Content-Type': 'application/json' }, credentials: 'include' };
  if (body !== undefined) opzioni.body = JSON.stringify(body);
  let risposta;
  try {
    risposta = await fetch(url, opzioni);
  } catch (_errore) {
    const errore = new Error('Impossibile contattare il server. Verifica la connessione.');
    errore.status = 0;
    errore.code = 'NETWORK';
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
  try { dati = await risposta.json(); } catch (_errore) { dati = null; }
  if (!risposta.ok) {
    const dettaglio = dati && typeof dati.detail === 'string' ? dati.detail : '';
    const errore = new Error(dettaglio || `Errore ${risposta.status}`);
    errore.status = risposta.status;
    errore.detail = dettaglio;
    errore.code = dati && typeof dati.code === 'string' ? dati.code : '';
    errore.candidates = dati && Array.isArray(dati.candidates) ? dati.candidates : [];
    throw errore;
  }
  return dati;
}

function id(valore) {
  const n = Number(valore);
  if (!Number.isInteger(n) || n <= 0) throw new Error('Identificativo non valido.');
  return n;
}

export function listSellers(query = '') {
  return request('GET', `/api/crm/sellers${query ? `?${query}` : ''}`);
}

/** «Vende»: idempotente sul server per (immobile, contatto). `leadId` per la
 *  scelta esplicita fra lead candidati, `newLead` per crearne comunque uno. */
export function activateSeller(propertyId, contactId, { leadId = null, newLead = false } = {}) {
  const corpo = { property_id: id(propertyId), contact_id: id(contactId) };
  if (leadId) corpo.lead_id = id(leadId);
  if (newLead) corpo.new_lead = true;
  return request('POST', '/api/crm/sellers', corpo);
}

/** outcome: 'paused' | 'not_selling' | 'mistake'. Non cancella nulla. */
export function deactivateSeller(propertyId, contactId, outcome, note = '') {
  const corpo = { property_id: id(propertyId), contact_id: id(contactId), outcome };
  if (note && note.trim()) corpo.note = note.trim();
  return request('POST', '/api/crm/sellers/deactivate', corpo);
}

/** Lo storico: la rotta delle interazioni dell'immobile, con il lead. */
export function logInteraction(propertyId, { type, note, contactId, leadId }) {
  return request('POST', `/api/property/properties/${id(propertyId)}/interactions`, {
    interaction_type: type, note, contact_id: id(contactId), lead_id: id(leadId), context: 'seller',
  });
}

/** La prossima azione: un task del CRM sul lead e sul contatto. */
export function createFollowUp({ leadId, contactId, title, dueAt }) {
  return request('POST', '/api/core/tasks', {
    lead_id: id(leadId), contact_id: id(contactId), title, due_at: dueAt, task_type: 'seller_followup',
  });
}

export function setStage(leadId, stage) {
  return request('PATCH', `/api/core/leads/${id(leadId)}`, { stage });
}
