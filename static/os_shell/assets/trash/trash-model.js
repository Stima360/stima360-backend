// STIMA360 OS — trash/trash-model.js
// DELETE-ARCH Fase 2B3: le funzioni pure del Cestino Immobili (nessuna rete,
// nessun DOM). La UI NON decide nulla: chi puo' eliminare, i blocchi, lo
// storico protetto e i conflitti del ripristino arrivano dal backend
// (property/lifecycle.py). Qui si traducono soltanto status/code/detail in
// frasi leggibili, senza ricostruire nessuna regola.
//
// Lessico: «Elimina…» = sposta nel Cestino; «Ripristina» lo riporta fra gli
// immobili operativi. «Archivia» e' un'altra cosa e resta separata.

/** I motivi del backend (property/lifecycle.py::TRASH_REASONS), stesso ordine. */
export const TRASH_REASONS = Object.freeze([
  { value: 'created_by_mistake', label: 'Creato per errore' },
  { value: 'duplicate', label: 'Duplicato' },
  { value: 'invalid_data', label: 'Dati non validi' },
  { value: 'test_record', label: 'Record di prova' },
  { value: 'other', label: 'Altro' },
]);

export const TRASH_NOTE_MAX = 500;

export const HISTORY_REQUIRES_ADMIN_TEXT =
  'Questo immobile ha uno storico operativo. Serve un amministratore per spostarlo nel Cestino.';

export const TRASHED_TOAST = 'Immobile spostato nel Cestino';
export const RESTORED_TOAST = 'Immobile ripristinato';

const CONFLICT_LABELS = {
  cadastral_identity: 'stessa identità catastale',
  client_request: 'stessa richiesta di creazione',
};

export function reasonLabel(code) {
  const voce = TRASH_REASONS.find((r) => r.value === code);
  return voce ? voce.label : (code || '—');
}

/**
 * I blocchi di deletion-check (o di un 409 TRASH_BLOCKED) ESATTAMENTE come
 * arrivano: etichetta del backend, quante voci. Per lo storico protetto
 * (HISTORY_REQUIRES_ADMIN) le sue voci sono gia' righe di storico
 * ({code, label, count}) e si mostrano come tali.
 */
export function blockerView(blockers) {
  return (Array.isArray(blockers) ? blockers : []).map((b) => {
    const items = Array.isArray(b.items) ? b.items : [];
    if (b.code === 'HISTORY_REQUIRES_ADMIN') {
      return {
        code: b.code, label: HISTORY_REQUIRES_ADMIN_TEXT, count: null,
        history: items.map((h) => ({ label: h.label || h.code, count: h.count ?? null })),
      };
    }
    // FIX-MANDATE-1: il backend puo' indicare dove trovare l'oggetto del
    // blocco (es. «Apri incarico»): solo collegamenti interni della Shell.
    const link = b.link && typeof b.link.href === 'string' && b.link.href.startsWith('#/')
      ? { href: b.link.href, label: b.link.label || 'Apri' } : null;
    return { code: b.code, label: b.label || b.code, count: items.length || null, history: null, link };
  });
}

/** Il messaggio di un errore di deletion-check o di POST .../trash. */
export function trashErrorText(error) {
  if (!error) return 'Operazione non riuscita.';
  if (error.code === 'HISTORY_REQUIRES_ADMIN') return HISTORY_REQUIRES_ADMIN_TEXT;
  if (error.status === 404) return 'Immobile non trovato.';
  return error.message || 'Operazione non riuscita.';
}

/** I blocchi portati da un errore (409 TRASH_BLOCKED) o dallo storico (403). */
export function errorBlockers(error) {
  const data = error && error.data ? error.data : null;
  if (!data) return [];
  if (Array.isArray(data.blockers)) return data.blockers;
  if (error.code === 'HISTORY_REQUIRES_ADMIN' && Array.isArray(data.history)) {
    return [{ code: 'HISTORY_REQUIRES_ADMIN', items: data.history }];
  }
  return [];
}

/**
 * Il messaggio di un ripristino rifiutato. Per RESTORE_CONFLICT si elencano
 * gli immobili in conflitto restituiti dal backend: nessuna correzione
 * automatica (codice, dati catastali, client_request_id restano come sono).
 */
export function restoreErrorView(error) {
  if (!error) return { text: 'Ripristino non riuscito.', conflicts: [] };
  const conflitti = error.code === 'RESTORE_CONFLICT' && error.data && Array.isArray(error.data.conflicts)
    ? error.data.conflicts : [];
  const text = error.status === 404 ? 'Immobile non trovato.' : (error.message || 'Ripristino non riuscito.');
  return {
    text,
    conflicts: conflitti.map((c) => ({
      id: c.id, code: c.code || `#${c.id}`, label: CONFLICT_LABELS[c.index] || 'conflitto',
    })),
  };
}

/** Indirizzo leggibile; il titolo se l'indirizzo manca. */
export function propertyLine(p) {
  if (!p) return '';
  const via = [p.address, p.civic_number].filter(Boolean).join(' ');
  const luogo = [via, p.city].filter(Boolean).join(', ');
  return luogo || p.title || '';
}

export function trashListPath(offset = 0, limit = 50) {
  return `/api/property/trash?limit=${Number(limit)}&offset=${Number(offset)}`;
}
