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

// ---------------------------------------------------------------------------
// CESTINO-CONTATTI-1: le stesse traduzioni per i CONTATTI (core/contact_lifecycle.py).
// Stessi motivi, stessa nota, stessa regola: blocchi, storico e possibili
// doppioni arrivano dal backend; qui diventano solo frasi e collegamenti.
// ---------------------------------------------------------------------------

export const CONTACT_HISTORY_REQUIRES_ADMIN_TEXT =
  'Questo contatto ha uno storico operativo. Serve un amministratore per spostarlo nel Cestino.';
export const CONTACT_TRASHED_TOAST = 'Contatto spostato nel Cestino';
export const CONTACT_RESTORED_TOAST = 'Contatto ripristinato';

export const CONTACT_STATUS_LABELS = Object.freeze({ active: 'Attivo', inactive: 'Inattivo', archived: 'Archiviato' });

/** Solo collegamenti interni della Shell («#/...»): mai un indirizzo esterno. */
function linkInterno(voce, etichetta) {
  return voce && typeof voce.href === 'string' && voce.href.startsWith('#/')
    ? { href: voce.href, label: voce.label || etichetta || 'Apri' } : null;
}

/**
 * I blocchi del Cestino Contatti come arrivano, con in piu' le VOCI (ogni
 * record che blocca, con il suo collegamento) e l'eventuale AZIONE proposta
 * dal backend (oggi: «Disattiva accesso» al portale proprietario).
 */
export function contactBlockerView(blockers) {
  return (Array.isArray(blockers) ? blockers : []).map((b) => {
    const items = Array.isArray(b.items) ? b.items : [];
    if (b.code === 'HISTORY_REQUIRES_ADMIN') {
      return {
        code: b.code, label: CONTACT_HISTORY_REQUIRES_ADMIN_TEXT, count: null, items: [], link: null, action: null,
        history: items.map((h) => ({ label: h.label || h.code, count: h.count ?? null })),
      };
    }
    const azione = b.action && b.action.kind === 'owner_account_disable'
      ? { kind: b.action.kind, accountId: Number(b.action.account_id), allowed: b.action.allowed === true,
          label: b.action.label || 'Disattiva accesso' }
      : null;
    return {
      code: b.code, label: b.label || b.code, count: items.length || null, history: null,
      link: b.link ? linkInterno(b.link, 'Apri') : null,
      items: items.filter((i) => i && i.label).map((i) => ({ label: i.label, link: linkInterno({ href: i.href, label: 'Apri' }) })),
      action: azione,
    };
  });
}

/** Il messaggio di un errore di deletion-check o di POST .../trash di un contatto. */
export function contactTrashErrorText(error) {
  if (!error) return 'Operazione non riuscita.';
  if (error.code === 'HISTORY_REQUIRES_ADMIN') return CONTACT_HISTORY_REQUIRES_ADMIN_TEXT;
  if (error.status === 404) return 'Contatto non trovato.';
  return error.message || 'Operazione non riuscita.';
}

/** Il nome di un contatto come lo mostra il resto del CRM. */
export function contactName(c) {
  if (!c) return '';
  const persona = [c.first_name, c.last_name].filter(Boolean).join(' ');
  return c.display_name || persona || c.company_name || `Contatto #${c.id}`;
}

/** Email e telefono, quando ci sono. */
export function contactLine(c) {
  if (!c) return '';
  return [c.email, c.phone].filter(Boolean).join(' · ');
}

export function contactTrashListPath(offset = 0, limit = 50) {
  return `/api/core/trash/contacts?limit=${Number(limit)}&offset=${Number(offset)}`;
}

/**
 * Cosa fara' lo spostamento sulle comunicazioni (deletion-check, `effects`):
 * detto prima della conferma, con i numeri del backend.
 */
export function contactEffectsText(effects) {
  const e = effects || {};
  const frasi = [];
  if (e.automations_to_pause) frasi.push('Le automazioni del contatto verranno sospese.');
  else if (e.automations_already_paused) frasi.push('Le automazioni del contatto sono già sospese.');
  const n = Number(e.queued_messages) || 0;
  if (n === 1) frasi.push('1 messaggio in coda verrà annullato.');
  else if (n > 1) frasi.push(`${n} messaggi in coda verranno annullati.`);
  return frasi;
}

/** I possibili doppioni attivi segnalati dal ripristino (nessuna fusione). */
export function duplicatesView(restored) {
  const lista = restored && Array.isArray(restored.possible_duplicates) ? restored.possible_duplicates : [];
  return lista.map((d) => ({
    id: Number(d.id),
    name: contactName(d),
    reason: [d.same_email ? 'stessa email' : null, d.same_phone ? 'stesso telefono' : null].filter(Boolean).join(' e '),
  }));
}

// ---------------------------------------------------------------------------
// CESTINO-EDIFICI-1: le traduzioni per gli EDIFICI (property/building_lifecycle.py).
// L'edificio e' la palazzina contenitore; l'immobile «intero stabile» resta un
// immobile (Cestino Immobili). Blocchi e possibili doppioni arrivano dal backend.
// ---------------------------------------------------------------------------

export const BUILDING_TRASHED_TOAST = 'Edificio spostato nel Cestino';
export const BUILDING_RESTORED_TOAST = 'Edificio ripristinato';

/** I blocchi dell'edificio: la stessa vista per voci dei contatti (unita' con link). */
export function buildingBlockerView(blockers) {
  return contactBlockerView(blockers);
}

/** Il messaggio di un errore di deletion-check o di POST .../trash di un edificio. */
export function buildingTrashErrorText(error) {
  if (!error) return 'Operazione non riuscita.';
  if (error.status === 404) return 'Edificio non trovato.';
  return error.message || 'Operazione non riuscita.';
}

/** Nome dell'edificio, o la sua via; mai vuoto. */
export function buildingName(b) {
  if (!b) return '';
  const via = [b.address, b.civic_number].filter(Boolean).join(' ');
  return b.name || via || `Edificio #${b.id}`;
}

/** Via, civico e Comune. */
export function buildingLine(b) {
  if (!b) return '';
  const via = [b.address, b.civic_number].filter(Boolean).join(' ');
  return [via, b.city].filter(Boolean).join(', ');
}

export function buildingTrashListPath(offset = 0, limit = 50) {
  return `/api/property/trash/buildings?limit=${Number(limit)}&offset=${Number(offset)}`;
}

function uguale(a, b) {
  return a != null && b != null && String(a).trim().toLowerCase() === String(b).trim().toLowerCase()
    && String(a).trim() !== '';
}

/** I possibili doppioni del ripristino (la regola «palazzina simile»), nessuna fusione. */
export function buildingDuplicatesView(restored) {
  const lista = restored && Array.isArray(restored.possible_duplicates) ? restored.possible_duplicates : [];
  return lista.map((d) => {
    const catasto = ['cadastral_municipality_code', 'cadastral_sheet', 'cadastral_parcel'].every((k) => uguale(d[k], restored[k]));
    const indirizzo = ['city', 'address'].every((k) => uguale(d[k], restored[k]));
    return {
      id: Number(d.id),
      name: [buildingName(d), buildingLine(d)].filter((x, i, a) => x && a.indexOf(x) === i).join(' · '),
      reason: [catasto ? 'stessa chiave catastale' : null, indirizzo ? 'stesso indirizzo' : null].filter(Boolean).join(' e '),
    };
  });
}

// ---------------------------------------------------------------------------
// CESTINO-RICHIESTE-1: le traduzioni per le RICHIESTE ACQUIRENTE
// (buy/lifecycle.py). Stessi motivi, stessa nota, stessa regola: blocchi,
// storico, effetti e possibili doppioni arrivano dal backend; qui diventano
// solo frasi e collegamenti. Le comunicazioni del contatto non cambiano.
// ---------------------------------------------------------------------------

export const BUY_HISTORY_REQUIRES_ADMIN_TEXT =
  'Questa richiesta ha uno storico operativo. Serve un amministratore per spostarla nel Cestino.';
export const BUY_TRASHED_TOAST = 'Richiesta spostata nel Cestino';
export const BUY_RESTORED_TOAST = 'Richiesta ripristinata';

export const BUY_STATUS_LABELS = Object.freeze({
  draft: 'Bozza', active: 'Attiva', paused: 'In pausa', satisfied: 'Soddisfatta', closed: 'Chiusa', archived: 'Archiviata',
});

/** I blocchi della richiesta: la stessa vista per voci dei contatti, con il
 *  testo dello storico della richiesta. */
export function buyBlockerView(blockers) {
  return contactBlockerView(blockers).map((v) => (v.code === 'HISTORY_REQUIRES_ADMIN'
    ? { ...v, label: BUY_HISTORY_REQUIRES_ADMIN_TEXT } : v));
}

/** Il messaggio di un errore di deletion-check o di POST .../trash di una richiesta. */
export function buyTrashErrorText(error) {
  if (!error) return 'Operazione non riuscita.';
  if (error.code === 'HISTORY_REQUIRES_ADMIN') return BUY_HISTORY_REQUIRES_ADMIN_TEXT;
  if (error.status === 404) return 'Richiesta non trovata.';
  return error.message || 'Operazione non riuscita.';
}

/** Il titolo della richiesta; mai vuoto. */
export function buyRequestName(r) {
  if (!r) return '';
  return r.title || `Richiesta #${r.id}`;
}

function euro(valore) {
  const n = Number(valore);
  if (valore === null || valore === undefined || valore === '' || !Number.isFinite(n)) return '';
  return `${n.toLocaleString('it-IT', { maximumFractionDigits: 0 })} €`;
}

/** Contatto e budget, quando ci sono. */
export function buyRequestLine(r) {
  if (!r) return '';
  const budget = euro(r.budget_target ?? r.budget_max);
  return [r.contact_name, budget ? `budget ${budget}` : ''].filter(Boolean).join(' · ');
}

export function buyTrashListPath(offset = 0, limit = 50) {
  return `/api/buy/trash/requests?limit=${Number(limit)}&offset=${Number(offset)}`;
}

/** Cosa fara' lo spostamento (deletion-check, `effects`): detto prima della conferma. */
export function buyEffectsText(effects) {
  const e = effects || {};
  const frasi = [];
  const stato = BUY_STATUS_LABELS[e.status] || e.status_label || '';
  if (stato) frasi.push(`Lo stato resta «${stato}»: la richiesta non viene chiusa né sospesa.`);
  const n = Number(e.matches) || 0;
  if (n === 1) frasi.push('1 abbinamento esce dalle liste e dai calcoli (resta nella scheda).');
  else if (n > 1) frasi.push(`${n} abbinamenti escono dalle liste e dai calcoli (restano nella scheda).`);
  const altre = Number(e.other_open_requests) || 0;
  const chi = e.contact_name ? `${e.contact_name}` : 'Il contatto';
  if (altre === 1) frasi.push(`${chi} resta attivo, con la sua altra richiesta aperta.`);
  else if (altre > 1) frasi.push(`${chi} resta attivo, con le sue altre ${altre} richieste aperte.`);
  else frasi.push(`${chi} resta attivo.`);
  if (e.communications_unchanged) frasi.push('Le comunicazioni del contatto non vengono sospese né annullate.');
  return frasi;
}

/** Le altre richieste aperte dello stesso contatto, segnalate dal ripristino. */
export function buyDuplicatesView(restored) {
  const lista = restored && Array.isArray(restored.possible_duplicates) ? restored.possible_duplicates : [];
  return lista.map((d) => ({
    id: Number(d.id),
    name: buyRequestName(d),
    reason: ['stesso contatto', (BUY_STATUS_LABELS[d.status] || '').toLowerCase()].filter(Boolean).join(', '),
  }));
}

/** Il rifiuto di un ripristino: il testo e, se il contatto e' nel Cestino, i suoi collegamenti. */
export function buyRestoreErrorView(error) {
  if (!error) return { text: 'Ripristino non riuscito.', blockers: [] };
  const blocchi = error.code === 'RESTORE_BLOCKED' ? buyBlockerView(errorBlockers(error)) : [];
  const text = error.status === 404 ? 'Richiesta non trovata.' : (error.message || 'Ripristino non riuscito.');
  return { text, blockers: blocchi };
}
