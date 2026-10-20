// STIMA360 OS — components/property-label.js (SEMPLIFICAZIONE-CRM-1)
//
// COME SI CHIAMA UN IMMOBILE A SCHERMO, in un posto solo. Un agente riconosce
// un immobile da tipologia, indirizzo, comune e - quando servono - piano, scala
// e interno: «Appartamento — Via Roma 15 — Piano 2». Il codice tecnico
// (`properties.code`, IMM-<id>) resta nel database, nelle API e nella ricerca
// (il server cerca anche per codice), ma non si mostra nelle schermate
// ordinarie. Nessuna chiave tecnica cambia: qui c'e' solo la resa.
//
// Funzioni PURE: niente DOM, niente rete. La tipologia in italiano arriva dal
// catalogo di `GET /api/property/form-options` (`property_types`), quando il
// chiamante lo ha; senza catalogo si legge dalla descrizione generata dal
// backend (`property/catalog.py::generated_title`: «Appartamento · Comune
// (Microzona) · Via Civico»), che e' gia' in italiano; in ultimo il valore
// tecnico. Mai un codice.

const testo = (v) => (v === null || v === undefined ? '' : String(v).trim());

const PIANI = {
  '-2': 'Piano interrato', '-1': 'Piano interrato', S: 'Piano seminterrato',
  T: 'Piano terra', '0': 'Piano terra', R: 'Piano rialzato',
};

/** «Piano 2», «Piano terra», «Piano interrato» o '' se non indicato. */
export function floorText(floor) {
  const raw = testo(floor);
  if (!raw) return '';
  const chiave = raw.toUpperCase();
  if (PIANI[chiave]) return PIANI[chiave];
  if (/^-?\d+$/.test(raw)) return Number(raw) < 0 ? 'Piano interrato' : `Piano ${raw}`;
  return `Piano ${raw}`;
}

/** La tipologia leggibile (vedi intestazione). `accessoryKinds` per una
 *  pertinenza autonoma («Pertinenza · Posto auto»). */
export function propertyTypeText(p, types, accessoryKinds) {
  if (!p) return '';
  if (p.pertinenza_kind && Array.isArray(accessoryKinds)) {
    const tipo = accessoryKinds.find((k) => k && k.value === p.pertinenza_kind);
    if (tipo) return `Pertinenza · ${tipo.label}`;
  }
  const voce = (types || []).find((t) => t && t.value === p.property_type);
  if (voce) return voce.label;
  const titolo = testo(p.title);
  if (titolo.includes(' · ')) return titolo.split(' · ')[0];
  return testo(p.property_type_label) || '';
}

/** «Via Roma 15» o ''. */
export function propertyStreetText(p) {
  if (!p) return '';
  return [testo(p.address), testo(p.civic_number)].filter(Boolean).join(' ');
}

/** «Tortoreto (Lido Sud)», «Tortoreto» o ''. */
export function propertyPlaceText(p) {
  if (!p) return '';
  const comune = testo(p.city);
  if (!comune) return '';
  const zona = testo(p.microzone);
  return zona ? `${comune} (${zona})` : comune;
}

/** «Piano 2 · Scala B · Int. 3»: solo cio' che c'e'. */
export function propertyPositionText(p) {
  if (!p) return '';
  const parti = [];
  const piano = floorText(p.floor);
  if (piano) parti.push(piano);
  const scala = testo(p.staircase);
  if (scala) parti.push(`Scala ${scala}`);
  const interno = testo(p.internal_number);
  if (interno) parti.push(`Int. ${interno}`);
  return parti.join(' · ');
}

/**
 * Il nome breve: «Appartamento — Via Roma 15 — Piano 2». Senza indirizzo si
 * usa il comune; senza nulla la descrizione generata o «Immobile».
 */
export function propertyShortLabel(p, types, accessoryKinds) {
  if (!p) return '';
  const parti = [propertyTypeText(p, types, accessoryKinds), propertyStreetText(p) || propertyPlaceText(p),
    propertyPositionText(p)].filter(Boolean);
  if (parti.length) return parti.join(' — ');
  return testo(p.title) || 'Immobile';
}

/** Il nome completo: come il breve, piu' il comune quando l'indirizzo c'e'. */
export function propertyFullLabel(p, types, accessoryKinds) {
  if (!p) return '';
  const parti = [propertyTypeText(p, types, accessoryKinds), propertyStreetText(p), propertyPlaceText(p),
    propertyPositionText(p)].filter(Boolean);
  if (parti.length) return parti.join(' — ');
  return testo(p.title) || 'Immobile';
}

/** Titolo e riga secondaria per una ricerca con scelta (Agenda, selettori). */
export function propertyLookupLabel(p, types) {
  if (!p) return { title: '', detail: '' };
  const title = propertyShortLabel(p, types);
  const detail = [propertyStreetText(p) ? propertyPlaceText(p) : '', testo(p.owner_name)]
    .filter(Boolean).join(' · ');
  return { title, detail };
}

/** La riga di un'unita' DENTRO la sua palazzina (l'indirizzo e' quello della
 *  palazzina): «Appartamento — Piano 2 · Scala B · Int. 3». */
export function unitLabel(u, types, accessoryKinds) {
  if (!u) return '';
  const parti = [propertyTypeText(u, types, accessoryKinds) || 'Unità', propertyPositionText(u)].filter(Boolean);
  return parti.join(' — ');
}

/** Da una riga che porta solo i campi `property_*` (match, visite, acquisizioni):
 *  la descrizione generata, altrimenti indirizzo e comune, mai il codice. */
export function propertyRowLabel(r) {
  if (!r) return '';
  const titolo = testo(r.property_title);
  if (titolo) return titolo;
  const via = [testo(r.property_address), testo(r.property_civic_number)].filter(Boolean).join(' ');
  const dove = [via, testo(r.property_city) || testo(r.city)].filter(Boolean).join(', ');
  return dove || 'Immobile';
}
