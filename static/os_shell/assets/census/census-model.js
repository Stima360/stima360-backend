// STIMA360 OS — census/census-model.js (CENSIMENTO-1 Fase 4)
//
// Funzioni PURE del censimento: piani, contatori, etichette, payload e
// messaggi d'errore. Nessun DOM, nessuna rete: si provano da sole in node.
//
// Le liste che il backend VALIDA (tipologie, tipi di edificio, fonti, tipi di
// accessorio, categorie catastali) arrivano da GET /api/property/form-options e
// passano da qui solo come argomenti. Le uniche costanti locali sono
// presentazione pura: i chips dei piani (il campo `floor` e' testo libero,
// §2 S3) e le frasi della UI.

/** I chips del piano (S3): preselezione a un tocco, «Altro…» scrive libero. */
export const FLOOR_CHIPS = [
  { value: '-1', label: 'Int' },
  { value: 'S', label: 'Sem' },
  { value: 'T', label: 'T' },
  { value: 'R', label: 'R' },
  { value: '1', label: '1' },
  { value: '2', label: '2' },
  { value: '3', label: '3' },
  { value: '4', label: '4' },
  { value: '5', label: '5' },
  { value: '6', label: '6' },
];

const FLOOR_NAMES = {
  '-1': 'Interrato', '-2': 'Interrato', S: 'Seminterrato', T: 'Terra', '0': 'Terra', R: 'Rialzato',
};

/** L'intestazione del gruppo per piano (S2): «Terra», «2º», o il testo libero. */
export function floorLabel(floor) {
  const raw = floor === null || floor === undefined ? '' : String(floor).trim();
  if (!raw) return 'Piano non indicato';
  const chiave = raw.toUpperCase();
  if (FLOOR_NAMES[chiave]) return FLOOR_NAMES[chiave];
  if (/^-?\d+$/.test(raw)) return Number(raw) < 0 ? 'Interrato' : `${raw}º`;
  return raw;
}

/** Le unita' raggruppate per piano, nell'ORDINE del server (che ordina gia'
 *  per piano numerico, poi testuale, scala, interno). */
export function groupUnitsByFloor(units) {
  const gruppi = [];
  for (const u of units || []) {
    const chiave = u.floor === null || u.floor === undefined ? '' : String(u.floor).trim();
    const ultimo = gruppi[gruppi.length - 1];
    if (ultimo && ultimo.floor === chiave) ultimo.units.push(u);
    else gruppi.push({ floor: chiave, label: floorLabel(chiave), units: [u] });
  }
  return gruppi;
}

/** «Censite 5 di 6 dichiarate — 4 principali + 1 pertinenza» (decisione 5). */
export function countersText(counters, unitsDeclared) {
  const c = counters || {};
  const censite = Number(c.units_census || 0);
  const principali = Number(c.units_main || 0);
  const pertinenze = Number(c.units_pertinenze || 0);
  const testa = Number.isInteger(unitsDeclared) && unitsDeclared !== null
    ? `Censite ${censite} di ${unitsDeclared} dichiarate`
    : `Censite ${censite}`;
  const dettaglio = censite > 0
    ? ` — ${principali} ${principali === 1 ? 'principale' : 'principali'} + ${pertinenze} ${pertinenze === 1 ? 'pertinenza' : 'pertinenze'}`
    : '';
  return testa + dettaglio;
}

/** L'etichetta di un valore in una lista `{value, label}` di form-options. */
export function labelOf(list, value, fallback) {
  const voce = (list || []).find((x) => x && x.value === value);
  if (voce) return voce.label;
  return fallback === undefined ? (value || '—') : fallback;
}

function mq(valore) {
  if (valore === null || valore === undefined || valore === '') return '';
  const n = Number(valore);
  return Number.isNaN(n) ? '' : `${n.toLocaleString('it-IT', { maximumFractionDigits: 2 })} m²`;
}

/** La riga di un'unita' nella palazzina (S2): tipologia, interno, scala, mq. */
export function unitRowText(u, propertyTypes) {
  const parti = [labelOf(propertyTypes, u.property_type, u.property_type || 'Unità')];
  if (u.internal_number) parti.push(`int. ${u.internal_number}`);
  if (u.staircase) parti.push(`scala ${u.staircase}`);
  const superficie = mq(u.surface_sqm);
  if (superficie) parti.push(superficie);
  return parti.join(' · ');
}

/** I badge di una riga unita': pertinenza, categoria da verificare, da chiarire. */
export function unitRowBadges(u) {
  const badge = [];
  if (u.parent_property_id) badge.push({ text: 'Pertinenza', tone: 'gray' });
  // PERTINENZE-1: pertinenza autonoma senza unita' principale
  else if (isUnitPertinenza(u)) badge.push({ text: 'Pertinenza da collegare', tone: 'warn' });
  if (!u.cadastral_category) badge.push({ text: 'Da verificare', tone: 'warn' });
  else badge.push({ text: u.cadastral_category, tone: 'ok' });
  if (Number(u.accessories_unknown || 0) > 0) badge.push({ text: `${u.accessories_unknown} da chiarire`, tone: 'warn' });
  if (u.record_kind && u.record_kind !== 'census') badge.push({ text: 'In carico', tone: 'role' });
  return badge;
}

/** «Duplica» (S6, decisione 4): copia tipologia, piano, scala, mq, locali e
 *  bagni; lascia VUOTI interno, categoria, catasto, proprietari, relazioni. */
export function duplicateSeed(u) {
  const seme = { property_type: u.property_type || 'apartment', floor: u.floor || '', staircase: u.staircase || '' };
  for (const k of ['surface_sqm', 'rooms', 'bathrooms']) {
    if (u[k] !== null && u[k] !== undefined && u[k] !== '') seme[k] = u[k];
  }
  return seme;
}

const str = (v) => (v === null || v === undefined ? '' : String(v).trim());

// --- Sezione catastale a tre stati (REV 2, R1) ---------------------------------
//
// Il backend (083 e `_identita_completa`) distingue: NULL = non conosciuta;
// '' = nessuna sezione, accertato (entra nella chiave di unicita');
// stringa = sezione valorizzata. La UI li tiene espliciti: un campo vuoto
// NON diventa mai «nessuna sezione» da solo.

export const SECTION_MODES = [
  { value: 'unknown', label: 'Non conosciuta' },
  { value: 'none', label: 'Nessuna (accertato)' },
  { value: 'value', label: 'Con sezione' },
];

/** Lo stato della sezione dal valore salvato: null → unknown, '' → none. */
export function sectionMode(valore) {
  if (valore === null || valore === undefined) return 'unknown';
  return String(valore) === '' ? 'none' : 'value';
}

/** Il valore da salvare (o `undefined` = non inviare) da stato + testo. */
export function sectionValue(mode, testo) {
  if (mode === 'none') return '';
  if (mode === 'value') return str(testo) || undefined;      // «con sezione» ma vuota: non si inventa «nessuna»
  return undefined;
}

export function sectionLabel(valore) {
  const mode = sectionMode(valore);
  return mode === 'value' ? String(valore) : (mode === 'none' ? 'Nessuna' : 'Non conosciuta');
}

/** Il corpo di POST /api/property/census/units dallo stato del foglio: solo
 *  i campi valorizzati (mai stringhe vuote o NaN); l'indirizzo proprio viaggia
 *  solo con «Ingresso diverso?» (altrimenti e' ereditato dalla palazzina).
 *  La sezione catastale segue `cadastral_section_mode` (R1); senza il
 *  tri-stato vale la regola generale (vuota = non inviata). */
export function buildUnitPayload(stato) {
  const s = stato || {};
  const corpo = { property_type: s.property_type || 'apartment', client_request_id: s.client_request_id };
  if (s.building_id) corpo.building_id = Number(s.building_id);
  if (s.parent_property_id) corpo.parent_property_id = Number(s.parent_property_id);
  if (s.whole_building === true) corpo.whole_building = true;
  for (const k of ['floor', 'staircase', 'internal_number', 'cadastral_category', 'cadastral_municipality_code',
    'cadastral_sheet', 'cadastral_parcel', 'cadastral_subunit', 'internal_notes', 'public_notes']) {
    if (str(s[k])) corpo[k] = str(s[k]);
  }
  if (s.cadastral_section_mode) {
    const sezione = sectionValue(s.cadastral_section_mode, s.cadastral_section);
    if (sezione !== undefined) corpo.cadastral_section = sezione;
  } else if (str(s.cadastral_section)) {
    corpo.cadastral_section = str(s.cadastral_section);
  }
  for (const k of ['surface_sqm', 'commercial_surface_sqm']) {
    const n = Number(s[k]);
    if (str(s[k]) && !Number.isNaN(n) && n >= 0) corpo[k] = n;
  }
  for (const k of ['rooms', 'bedrooms', 'bathrooms']) {
    const n = Number.parseInt(s[k], 10);
    if (str(s[k]) && !Number.isNaN(n) && n >= 0) corpo[k] = n;
  }
  if (s.own_address) {
    for (const k of ['region', 'province', 'city', 'microzone', 'address', 'civic_number', 'postal_code']) {
      if (str(s[k])) corpo[k] = str(s[k]);
    }
  }
  // CREAZIONE-GUIDATA-1: scheda commerciale (ingresso dall'elenco Commerciale)
  // e, solo per quella, l'agente scelto da chi puo' assegnare.
  if (s.record_kind === 'crm') {
    corpo.record_kind = 'crm';
    const agente = Number.parseInt(s.assigned_agent_id, 10);
    if (Number.isInteger(agente) && agente > 0) corpo.assigned_agent_id = agente;
  }
  if (s.confirm_similar === true) corpo.confirm_similar = true;
  // PERTINENZE-1: pertinenza autonoma (collegata o da collegare) e il suo tipo
  if (s.is_pertinenza === true) corpo.is_pertinenza = true;
  if (str(s.pertinenza_kind)) { corpo.pertinenza_kind = str(s.pertinenza_kind); corpo.is_pertinenza = true; }
  return corpo;
}

/** Le differenze fra due corpi di creazione, come PATCH generica (R7): solo
 *  i campi che la scheda puo' cambiare dopo la creazione. Palazzina, genitore
 *  e «Stabile intero» non si spostano con una PATCH e restano fuori. Un campo
 *  presente nel primo invio e assente nel secondo viene azzerato (null). */
export const PATCHABLE_UNIT_FIELDS = ['property_type', 'floor', 'staircase', 'internal_number', 'surface_sqm',
  'commercial_surface_sqm', 'rooms', 'bedrooms', 'bathrooms', 'cadastral_category', 'cadastral_municipality_code',
  'cadastral_section', 'cadastral_sheet', 'cadastral_parcel', 'cadastral_subunit', 'internal_notes', 'public_notes',
  'region', 'province', 'city', 'microzone', 'address', 'civic_number', 'postal_code'];

export function unitPatchDiff(inviato, attuale) {
  const a = inviato || {};
  const b = attuale || {};
  const patch = {};
  for (const k of PATCHABLE_UNIT_FIELDS) {
    const prima = a[k] === undefined ? null : a[k];
    const dopo = b[k] === undefined ? null : b[k];
    if (prima !== dopo) patch[k] = dopo;
  }
  return patch;
}

/** Vero se due corpi di creazione coincidono, chiave e conferma a parte. */
export function sameUnitBody(x, y) {
  const pulisci = (c) => Object.fromEntries(Object.entries(c || {}).filter(([k]) => k !== 'client_request_id' && k !== 'confirm_similar').sort());
  return JSON.stringify(pulisci(x)) === JSON.stringify(pulisci(y));
}

/** Il corpo di POST /api/property/buildings dal foglio «Nuova palazzina» (S1). */
export function buildBuildingPayload(stato) {
  const s = stato || {};
  const corpo = { building_type: s.building_type || 'condominio', client_request_id: s.client_request_id };
  for (const k of ['name', 'region', 'province', 'city', 'microzone', 'address', 'civic_number', 'postal_code', 'notes',
    'units_declared_source', 'cadastral_municipality_code', 'cadastral_section', 'cadastral_sheet', 'cadastral_parcel']) {
    if (str(s[k])) corpo[k] = str(s[k]);
  }
  const n = Number.parseInt(s.units_declared, 10);
  if (str(s.units_declared) && !Number.isNaN(n) && n >= 0) corpo.units_declared = n;
  if (s.confirm_similar === true) corpo.confirm_similar = true;
  return corpo;
}

/** I gruppi del foglio categoria (S4): A–E ordinari, poi «Storiche» (A/5,
 *  A/6) e «Stati particolari» (gruppo F, senza rendita) in fondo. */
export function categoryGroups(categories) {
  const ordinari = new Map();
  const storiche = [];
  const particolari = [];
  for (const c of categories || []) {
    if (c.historical) storiche.push(c);
    else if (c.no_income || c.group === 'F') particolari.push(c);
    else {
      if (!ordinari.has(c.group)) ordinari.set(c.group, []);
      ordinari.get(c.group).push(c);
    }
  }
  const gruppi = [...ordinari.entries()].sort(([a], [b]) => a.localeCompare(b))
    .map(([group, voci]) => ({ key: group, label: `Gruppo ${group}`, items: voci }));
  if (storiche.length) gruppi.push({ key: 'storiche', label: 'Storiche', items: storiche });
  if (particolari.length) gruppi.push({ key: 'particolari', label: 'Stati particolari (senza rendita)', items: particolari });
  return gruppi;
}

/** Ricerca per codice («c6») o parola («autorimessa»), senza distinguere
 *  maiuscole e slash. */
export function filterCategories(categories, term) {
  const t = str(term).toLowerCase().replace(/\s+/g, '');
  if (!t) return categories || [];
  return (categories || []).filter((c) => c.code.toLowerCase().replace('/', '').includes(t.replace('/', ''))
    || (c.label || '').toLowerCase().includes(str(term).toLowerCase()));
}

/** I suggerimenti per la tipologia (S4, §5): sempre superabili. */
export function suggestionsFor(suggestions, categories, propertyType) {
  const s = (suggestions || {})[propertyType] || { suggested: [], secondary: [] };
  const trova = (code) => (categories || []).find((c) => c.code === code);
  return {
    suggested: (s.suggested || []).map(trova).filter(Boolean),
    secondary: (s.secondary || []).map(trova).filter(Boolean),
  };
}

/** Il testo «Simile a IMM-402 (2º piano, int. 2)» per un candidato. */
export function similarText(s) {
  const parti = [];
  if (s.floor) parti.push(`${floorLabel(s.floor)}${/^-?\d+$/.test(String(s.floor)) ? ' piano' : ''}`);
  if (s.internal_number) parti.push(`int. ${s.internal_number}`);
  if (s.staircase) parti.push(`scala ${s.staircase}`);
  if (!parti.length && (s.address || s.city)) parti.push([s.address, s.civic_number].filter(Boolean).join(' ') || s.city);
  const dove = parti.length ? ` (${parti.join(', ')})` : '';
  return `Simile a ${s.code || s.name || `#${s.id}`}${dove}`;
}

/** La frase per l'operatore, dal codice del backend. Mai un nome tecnico. */
export function errorMessage(error) {
  const code = error && error.code ? error.code : '';
  switch (code) {
    case 'NETWORK': return 'Connessione assente. Riprova.';
    case 'RESPONSE_LOST': return 'Risposta del server incompleta: il salvataggio potrebbe essere andato a buon fine. Riprova.';
    case 'IDEMPOTENCY_KEY_REUSED': return 'Questa richiesta risulta già inviata con dati diversi. Ricontrolla e conferma di nuovo.';
    case 'CADASTRAL_DUPLICATE': return `Questo subalterno è già censito${error.existing && error.existing.code ? ` come ${error.existing.code}` : ''}.`;
    case 'CENSUS_LOCKED': return 'Immobile in censimento: usa «Prendi in carico» prima di lavorarlo commercialmente.';
    case 'NOT_CENSUS': return 'L’immobile è già nel lavoro commerciale.';
    case 'UNDO_NOT_POSSIBLE': return 'Non più annullabile: apri la scheda.';
    case 'ALREADY_LINKED': return 'Questa pertinenza è già collegata a un’altra unità: scollegala prima.';
    case 'ALREADY_RESOLVED': return 'Questa voce è già stata chiarita in precedenza.';
    case 'LINK_INVALID': return error.detail || 'Collegamento non ammesso.';
    case 'CENSUS_NOT_INSTALLED': return 'Il modulo Censimento non è disponibile su questo ambiente.';
    case 'PERTINENZE_NOT_INSTALLED': return 'Le pertinenze autonome non sono ancora disponibili su questo ambiente (aggiornamento del database in corso).';
    case 'NOT_FOUND': return 'Non trovato: potrebbe essere stato archiviato o appartenere a un’altra agenzia.';
    default:
      if (error && error.status === 404) return 'Non trovato: potrebbe essere stato archiviato o appartenere a un’altra agenzia.';
      if (error && error.status === 422) return 'Alcuni valori non sono validi: controlla i campi.';
      return (error && (error.detail || error.message)) || 'Errore imprevisto.';
  }
}

/** L'avviso sotto il foglio dopo un invio dall'esito incerto (R7/R8). */
export function uncertainText() {
  return 'L’esito dell’ultimo invio non è noto: il server potrebbe aver già salvato. «Riprova» ripete la stessa richiesta con la stessa chiave: nessun doppione.';
}

/** Il banner del recupero (R7): la riga era gia' salvata con i dati del primo invio. */
export function recoveredText(unit, modifiche) {
  const codice = (unit && unit.code) || 'La scheda';
  return `${codice} era già stata salvata con i dati del primo invio. ${modifiche ? 'Le modifiche fatte dopo non sono ancora applicate: puoi applicarle ora, oppure aprire la scheda e lasciarla com’è.' : 'Nessuna differenza rispetto a quanto inviato.'}`;
}

/** Il testo del toast S5: «IMM-412 · 2º piano aggiunta — Annulla». */
export function createdToastText(unit) {
  const dove = unit && unit.floor ? ` · ${floorLabel(unit.floor)}${/^-?\d+$/.test(String(unit.floor)) ? ' piano' : ''}` : '';
  return `${(unit && unit.code) || 'Unità'}${dove} aggiunta`;
}

/** Il testo della conferma prima di cambiare l'indirizzo della palazzina
 *  (§6.4): «Aggiorno 4 unità; 2 con ingresso personalizzato restano come sono». */
export function propagationConfirmText(counters) {
  const c = counters || {};
  const ereditate = Number(c.units_address_inherited || 0);
  const proprie = Number(c.units_address_custom || 0);
  const prima = ereditate === 1 ? 'Aggiorno 1 unità' : `Aggiorno ${ereditate} unità`;
  const poi = proprie ? `; ${proprie} con ingresso personalizzato ${proprie === 1 ? 'resta' : 'restano'} come ${proprie === 1 ? 'è' : 'sono'}` : '';
  return `${prima}${poi}.`;
}

// --- EDIFICI-1: la sezione Edifici (lista e scheda) ---------------------------------
//
// Le regole dei contatori le decide il SERVER (`census_summary`,
// property/census.py): qui solo come si leggono. Dichiarate NULL = «non
// nota», mai 0; da completare NULL = non calcolabile; mai numeri negativi.

/** «Via Roma 10» (via e civico), stringa vuota se mancano. */
export function buildingStreet(b) {
  return [b && b.address, b && b.civic_number].filter((x) => x && String(x).trim()).join(' ');
}

/** Il nome mostrato: nome facoltativo, altrimenti la via, altrimenti #id. */
export function buildingTitle(b) {
  if (!b) return '';
  return (b.name && String(b.name).trim()) || buildingStreet(b) || `Palazzina #${b.id}`;
}

/** «Tortoreto · Alto» (comune e microzona del catalogo). */
export function buildingPlace(b) {
  return [b && b.city, b && b.microzone].filter((x) => x && String(x).trim()).join(' · ');
}

const plurale = (n, uno, molti) => `${n} ${n === 1 ? uno : molti}`;

/** Come si leggono i contatori di un edificio (lista e scheda). */
export function summaryView(summary) {
  const s = summary || {};
  const num = (v) => (Number.isInteger(v) && v >= 0 ? v : null);
  const dichiarate = num(s.units_declared);
  const censite = num(s.units_counted) ?? 0;
  const archiviate = num(s.units_archived) ?? 0;
  const daCompletare = num(s.units_to_complete);
  const oltre = num(s.units_over_declared) ?? 0;
  return {
    declared: dichiarate === null ? 'Non note' : String(dichiarate),
    declaredKnown: dichiarate !== null,
    counted: String(censite),
    countedNote: archiviate > 0 ? `di cui ${plurale(archiviate, 'archiviata', 'archiviate')}` : '',
    toComplete: daCompletare === null ? '—' : String(daCompletare),
    toCompleteNote: daCompletare === null ? 'Indica quante unità risultano per calcolarle' : '',
    over: oltre > 0 ? `${plurale(oltre, 'unità censita', 'unità censite')} oltre le dichiarate: verifica il totale dichiarato` : '',
    split: `${plurale(num(s.units_main) ?? 0, 'principale', 'principali')} + ${plurale(num(s.units_pertinenze) ?? 0, 'pertinenza', 'pertinenze')}${num(s.units_pertinenze_unlinked) ? ` (${num(s.units_pertinenze_unlinked)} da collegare)` : ''}`,
    unknownAccessories: num(s.accessories_unknown) ? plurale(s.accessories_unknown, 'accessorio da chiarire', 'accessori da chiarire') : '',
    categoryToVerify: num(s.category_to_verify) ? plurale(s.category_to_verify, 'categoria da verificare', 'categorie da verificare') : '',
  };
}

/** Comuni del catalogo territoriale (form-options `territory`), in ordine
 *  alfabetico, ciascuno con le sue microzone. Nessun elenco scritto qui. */
export function catalogMunicipalities(tree) {
  const out = [];
  for (const regione of tree || []) {
    for (const provincia of regione.provinces || []) {
      for (const comune of provincia.municipalities || []) {
        // CREAZIONE-GUIDATA-1: regione e provincia del comune, dal catalogo
        out.push({ name: comune.name, microzones: [...(comune.microzones || [])], region: regione.name, province: provincia.code });
      }
    }
  }
  return out.sort((a, b) => a.name.localeCompare(b.name, 'it', { sensitivity: 'base' }));
}

/** I filtri coerenti: una microzona vale solo se appartiene al comune scelto. */
export function coherentFilters(filters, municipalities) {
  const f = { search: str(filters && filters.search), city: str(filters && filters.city), microzone: str(filters && filters.microzone) };
  const comune = (municipalities || []).find((m) => m.name === f.city);
  if (!f.city || !comune || !comune.microzones.includes(f.microzone)) f.microzone = '';
  return f;
}

/** I parametri di GET /api/property/buildings dalla lista Edifici. */
export function buildingListQuery(filters, offset = 0, limit = 25) {
  const q = { sort: 'address', limit, offset };
  if (filters && str(filters.search)) q.search = str(filters.search);
  if (filters && str(filters.city)) q.city = str(filters.city);
  if (filters && str(filters.city) && str(filters.microzone)) q.microzone = str(filters.microzone);
  return q;
}

/** La riga di un'unita' nella scheda edificio: codice, tipologia, scala,
 *  piano, interno, mq (solo cio' che c'e'). */
export function unitFacts(u, propertyTypes, accessoryKinds) {
  // PERTINENZE-1: una pertinenza si legge per il suo tipo (Posto auto, non «Garage»)
  const parti = [pertinenzaKindLabel(u, accessoryKinds) || labelOf(propertyTypes, u.property_type, u.property_type || 'Unità')];
  if (u.staircase) parti.push(`scala ${u.staircase}`);
  if (u.floor !== null && u.floor !== undefined && String(u.floor).trim() !== '') parti.push(`piano ${floorLabel(u.floor)}`);
  if (u.internal_number) parti.push(`int. ${u.internal_number}`);
  const superficie = mq(u.surface_sqm);
  if (superficie) parti.push(superficie);
  return parti.join(' · ');
}

/** La relazione principale/pertinenza dell'unita', dai dati reali. */
export function unitRelationText(u) {
  if (u && u.parent) {
    const di = u.parent.code || `#${u.parent.id}`;
    return u.parent.same_building ? `Pertinenza di ${di}` : `Pertinenza di ${di} (in un altro edificio)`;
  }
  if (u && u.parent_property_id) return 'Pertinenza (unità principale non disponibile)';
  if (isUnitPertinenza(u)) return 'Pertinenza da collegare a un’unità';
  const n = Number(u && u.pertinenze_count) || 0;
  return n > 0 ? `Con ${plurale(n, 'pertinenza', 'pertinenze')}` : '';
}


// --- CREAZIONE-GUIDATA-1: la procedura guidata (territorio -> percorso -> edificio) ------
//
// Funzioni pure: lo stato della procedura e' un oggetto semplice che resta
// in memoria fra i passaggi («Indietro» non perde nulla); niente rete qui.

/** Lo stato iniziale della procedura. */
export function wizardInitialState() {
  return { city: '', microzone: '', address: '', civic_number: '', units_declared: '', units_unknown: false,
    building_name: '', path: '', search: null, building: null, building_saved: false };
}

/** Il totale dichiarato: intero >= 0, oppure null con «Non so» (mai 0 o 1 per difetto). */
export function wizardDeclared(stato) {
  if (stato.units_unknown) return { value: null, error: '' };
  const t = str(stato.units_declared);
  if (!t) return { value: null, error: 'Indica quante unità ci sono nell’edificio, oppure «Non so».' };
  if (!/^\d+$/.test(t)) return { value: null, error: 'Il numero di unità deve essere un intero (0 o più).' };
  return { value: Number.parseInt(t, 10), error: '' };
}

/** Gli errori del primo passaggio (nessun valore inventato per riempire un campo). */
export function wizardStep1Errors(stato, municipalities) {
  const errori = [];
  const comune = (municipalities || []).find((m) => m.name === stato.city);
  if (!comune) errori.push('Scegli il Comune.');
  else if (stato.microzone && !comune.microzones.includes(stato.microzone)) errori.push('La microzona non appartiene al Comune scelto.');
  const d = wizardDeclared(stato);
  if (d.error) errori.push(d.error);
  return errori;
}

/** Territorio completo per il backend: regione e provincia dal catalogo del Comune. */
export function wizardLocation(stato, municipalities) {
  const comune = (municipalities || []).find((m) => m.name === stato.city);
  const loc = { region: comune ? comune.region : '', province: comune ? comune.province : '', city: comune ? comune.name : '' };
  if (comune && str(stato.microzone) && comune.microzones.includes(stato.microzone)) loc.microzone = stato.microzone;
  if (str(stato.address)) loc.address = str(stato.address);
  if (str(stato.civic_number)) loc.civic_number = str(stato.civic_number);
  return loc;
}

const PAROLE_GENERICHE = new Set(['via', 'viale', 'v.', 'piazza', 'p.zza', 'piazzale', 'corso', 'c.so', 'contrada', 'c.da',
  'vicolo', 'largo', 'strada', 'localita', 'località', 'loc.', 'lungomare', 'traversa', 'del', 'della', 'dei', 'delle', 'di', 'da']);

/** Le parole utili per cercare edifici sulla stessa via (senza «via», «piazza»…). */
export function candidateSearch(address) {
  return str(address).split(/\s+/).filter((w) => w.length >= 2 && !PAROLE_GENERICHE.has(w.toLowerCase())).join(' ');
}

/** Stesso civico, normalizzato («10», «10 », «10/B» vs «10b»). */
export function sameCivic(a, b) {
  const n = (x) => str(x).toLowerCase().replace(/[\s/]+/g, '');
  return Boolean(n(a)) && n(a) === n(b);
}

/** I candidati in ordine: stesso civico prima, poi gli altri della via. */
export function rankCandidates(items, civic) {
  const lista = Array.isArray(items) ? items : [];
  return [...lista.filter((b) => sameCivic(b.civic_number, civic)), ...lista.filter((b) => !sameCivic(b.civic_number, civic))]
    .map((b) => ({ ...b, same_civic: sameCivic(b.civic_number, civic) }));
}

/** POST /api/property/buildings dalla procedura: solo i dati del primo passaggio. */
export function wizardBuildingPayload(stato, municipalities, { clientRequestId, confirmSimilar = false } = {}) {
  const corpo = { building_type: stato.building_type || 'condominio', client_request_id: clientRequestId, ...wizardLocation(stato, municipalities) };
  if (str(stato.building_name)) corpo.name = str(stato.building_name);
  const d = wizardDeclared(stato);
  if (d.value !== null) corpo.units_declared = d.value;
  if (confirmSimilar) corpo.confirm_similar = true;
  return corpo;
}

/** Il totale indicato ora, confrontato con quello gia' salvato sull'edificio scelto. */
export function declaredMismatchText(stato, building) {
  const d = wizardDeclared(stato);
  const salvato = building ? building.units_declared : null;
  if (d.value === null || salvato === null || salvato === undefined || Number(salvato) === d.value) return '';
  return `Hai indicato ${d.value} unità, l’edificio ne ha già ${salvato} dichiarate: il dato dell’edificio resta invariato (si cambia da «Modifica palazzina»).`;
}


// --- PERTINENZE-1: la natura di pertinenza, indipendente dal collegamento ----------------
//
// Una scheda e' pertinenza se e' collegata a un'unita' principale
// (`parent_property_id`) o se e' marcata tale (`is_pertinenza`, 089): censita
// ma non ancora collegata, o scollegata. Il tipo (`pertinenza_kind`) usa il
// catalogo degli accessori (form-options `accessory_kinds`): box e posto auto
// restano distinti anche se la tipologia e' `garage` per entrambi.

export function isUnitPertinenza(u) {
  return !!(u && (u.parent_property_id || u.is_pertinenza === true || u.pertinenza === true));
}

export function pertinenzaKindLabel(u, accessoryKinds) {
  if (!u || !u.pertinenza_kind) return '';
  return labelOf(accessoryKinds, u.pertinenza_kind, u.pertinenza_kind);
}

/** La tipologia di partenza di una pertinenza dal suo tipo (form-options
 *  `pertinenza_property_types`); `other` se il catalogo non la indica. */
export function pertinenzaPropertyType(options, kind) {
  const voce = ((options && options.pertinenza_property_types) || []).find((x) => x && x.value === kind);
  return voce ? voce.property_type : 'other';
}

/** Le pertinenze di una palazzina, dalle relazioni reali: collegate (con la
 *  loro principale, anche in un altro edificio) e da collegare. */
export function buildingPertinenze(units) {
  const linked = [];
  const unlinked = [];
  for (const u of units || []) {
    if (!isUnitPertinenza(u)) continue;
    if (u.parent_property_id) linked.push(u); else unlinked.push(u);
  }
  return { linked, unlinked };
}

/** Le unita' a cui si puo' collegare una pertinenza: principali (non
 *  pertinenze), non «stabile intero», non archiviate, diverse da lei. */
export function principalCandidates(units, excludeId) {
  return (units || []).filter((u) => u && u.id !== excludeId && !isUnitPertinenza(u) && !u.whole_building
    && u.commercial_status !== 'archived' && !u.archived_at);
}

/** «Nata dall'accessorio …»: la provenienza conservata nella conversione. */
export function fromAccessoryText(origine, accessoryKinds) {
  if (!origine || !origine.kind) return '';
  const testa = `Nata dall’accessorio «${labelOf(accessoryKinds, origine.kind, origine.kind)}»`;
  const dettagli = [];
  if (origine.source === 'stima360') dettagli.push('dal sito Stima360');
  if (origine.cadastral_status === 'unknown') dettagli.push('era «Da chiarire»');
  else if (origine.cadastral_status === 'included') dettagli.push('era «Compreso»');
  if (origine.surface_sqm !== null && origine.surface_sqm !== undefined && origine.surface_sqm !== '') dettagli.push(`${origine.surface_sqm} m²`);
  if (origine.quantity) dettagli.push(`× ${origine.quantity}`);
  return dettagli.length ? `${testa} (${dettagli.join(', ')})` : testa;
}
