// STIMA360 OS — trash/trash-api.js
// DELETE-ARCH Fase 2B3: l'UNICO file della Shell che nomina le rotte del
// Cestino Immobili (2B1/2B3, property/router.py). Mai la DELETE legacy
// dell'immobile: si sposta nel Cestino e si ripristina, nient'altro.
// CESTINO-CONTATTI-1: anche le rotte del Cestino Contatti (core/router.py) e
// il «Disattiva» dell'accesso al portale proprietario che un blocco propone
// (owner/router_admin.py, la rotta esistente: nessuna nuova).
// CESTINO-EDIFICI-1: anche le rotte del Cestino Edifici (property/router.py).
import { apiGet, apiPost } from '../core/api-client.js';
import { buildingTrashListPath, contactTrashListPath, trashListPath } from './trash-model.js';

export function deletionCheck(propertyId) {
  return apiGet(`/api/property/properties/${propertyId}/deletion-check`);
}

export function trashProperty(propertyId, reasonCode, note) {
  const corpo = { reason_code: reasonCode };
  const testo = typeof note === 'string' ? note.trim() : '';
  if (testo) corpo.note = testo;
  return apiPost(`/api/property/properties/${propertyId}/trash`, corpo);
}

export function listTrash(offset = 0, limit = 50) {
  return apiGet(trashListPath(offset, limit));
}

export function restoreProperty(propertyId) {
  return apiPost(`/api/property/properties/${propertyId}/restore`);
}

// --- CESTINO-CONTATTI-1 ------------------------------------------------------

export function contactDeletionCheck(contactId) {
  return apiGet(`/api/core/contacts/${contactId}/deletion-check`);
}

export function trashContact(contactId, reasonCode, note) {
  const corpo = { reason_code: reasonCode };
  const testo = typeof note === 'string' ? note.trim() : '';
  if (testo) corpo.note = testo;
  return apiPost(`/api/core/contacts/${contactId}/trash`, corpo);
}

export function listContactTrash(offset = 0, limit = 50) {
  return apiGet(contactTrashListPath(offset, limit));
}

export function restoreContact(contactId) {
  return apiPost(`/api/core/contacts/${contactId}/restore`);
}

/** L'azione proposta dal blocco OWNER_PORTAL_ACTIVE: la rotta esistente. */
export function disableOwnerAccount(accountId) {
  return apiPost(`/api/owner/admin/accounts/${Number(accountId)}/disable`, {});
}

// --- CESTINO-EDIFICI-1 -------------------------------------------------------

export function buildingDeletionCheck(buildingId) {
  return apiGet(`/api/property/buildings/${buildingId}/deletion-check`);
}

export function trashBuilding(buildingId, reasonCode, note) {
  const corpo = { reason_code: reasonCode };
  const testo = typeof note === 'string' ? note.trim() : '';
  if (testo) corpo.note = testo;
  return apiPost(`/api/property/buildings/${buildingId}/trash`, corpo);
}

export function listBuildingTrash(offset = 0, limit = 50) {
  return apiGet(buildingTrashListPath(offset, limit));
}

export function restoreBuilding(buildingId) {
  return apiPost(`/api/property/buildings/${buildingId}/restore`);
}
