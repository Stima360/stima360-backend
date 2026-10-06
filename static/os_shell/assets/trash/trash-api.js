// STIMA360 OS — trash/trash-api.js
// DELETE-ARCH Fase 2B3: l'UNICO file della Shell che nomina le rotte del
// Cestino Immobili (2B1/2B3, property/router.py). Mai la DELETE legacy
// dell'immobile: si sposta nel Cestino e si ripristina, nient'altro.
import { apiGet, apiPost } from '../core/api-client.js';
import { trashListPath } from './trash-model.js';

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
