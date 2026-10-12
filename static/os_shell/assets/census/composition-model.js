// STIMA360 OS — census/composition-model.js (CENSIMENTO-2)
//
// Funzioni PURE della composizione dell'edificio e delle schede delle unita':
// come si leggono i numeri del server, i corpi delle richieste e i controlli
// che si possono fare prima di inviarle. Nessun DOM, nessuna rete.
//
// Le regole stanno nel SERVER (property/composition.py, property/
// census_classes.py, migration 100): classi, presenti, da creare, stato di
// completamento, dati minimi, provenienze ammesse. Le liste arrivano nel
// `catalog` della risposta di GET .../composition: qui nessun elenco scritto
// a mano. Tre indicatori SEPARATI: quante unita' ci sono rispetto alle
// dichiarate, quanto sono complete le schede (il proprietario non e' fra i
// dati minimi) e quante sono verificate (Fase A, A1).

const str = (v) => (v === null || v === undefined ? '' : String(v).trim());
const plurale = (n, uno, molti) => `${n} ${n === 1 ? uno : molti}`;

export const UNKNOWN_SOURCE_LABEL = 'Non indicata';

/** Le provenienze per la scheda della singola unità, che non riceve il
 *  catalogo della composizione: solo presentazione (valori ed etichette), la
 *  validazione resta del server (schema e vincoli della migration 100). */
export const UNIT_SOURCES_CATALOG = {
  data_sources: [{ value: 'estimated', label: 'Stimata', bulk: true }, { value: 'declared', label: 'Dichiarata', bulk: true },
    { value: 'verified', label: 'Verificata', bulk: false }],
  cadastral_source_fields: ['cadastral_municipality_code', 'cadastral_sheet', 'cadastral_parcel', 'cadastral_subunit'],
  evidence_types: [{ value: 'visura', label: 'Visura catastale' }, { value: 'planimetria', label: 'Planimetria catastale' },
    { value: 'atto', label: 'Atto (rogito, notarile)' }, { value: 'altro', label: 'Altro documento' }],
};

/** La chiave dell'identità catastale, come `census_identity_key` della
 *  migration 100 (sezione facoltativa): null se incompleta. */
export function identityKey(f) {
  const v = (k) => str(f && f[k]);
  const parti = [v('cadastral_municipality_code').toUpperCase(), v('cadastral_section').toUpperCase(),
    v('cadastral_sheet').toUpperCase(), v('cadastral_parcel').toUpperCase(), v('cadastral_subunit').toUpperCase()];
  return [0, 2, 3, 4].every((i) => parti[i]) ? parti.join('|') : null;
}

/** CENSIMENTO-2 Fase A (A1): la verifica della scheda, separata dalla
 *  completezza. Tre condizioni, come il server (census_classes.py):
 *  'verified' (superficie E categoria verificate con la fonte), 'partial'
 *  (una sola delle due), 'to_verify' (nessuna; vedi `needsReconfirm`). */
export function verificationState(p) {
  if (!p) return 'to_verify';
  const n = ['surface_source', 'cadastral_category_source'].filter((k) => p[k] === 'verified').length;
  return ['to_verify', 'partial', 'verified'][n];
}

/** La verifica non vale piu': la fonte registrata e' di un'identita'
 *  catastale diversa da quella attuale (riferimenti cambiati dopo). */
export function needsReconfirm(p) {
  if (!p) return false;
  if (typeof p.verification_reconfirm === 'boolean') return p.verification_reconfirm;
  return Boolean(p.cadastral_evidence_identity) && p.cadastral_evidence_identity !== identityKey(p)
    && verificationState(p) === 'to_verify';
}

// D4: sempre «Verifica …», mai «Da verificare» (che nella scheda edificio era la categoria mancante)
export const VERIFICATION_LABELS = { verified: 'Verifica completa', partial: 'Verifica parziale', to_verify: 'Verifica da completare' };

/** L'etichetta e il tono del badge della verifica di una scheda. */
export function verificationBadge(p) {
  const stato = (p && p.verification) || verificationState(p);
  if (stato === 'verified') return { label: VERIFICATION_LABELS.verified, tone: 'ok' };
  if (stato === 'partial') return { label: VERIFICATION_LABELS.partial, tone: 'role' };
  if (needsReconfirm(p)) return { label: 'Verifica da riconfermare', tone: 'warn' };
  return { label: VERIFICATION_LABELS.to_verify, tone: 'gray' };
}

/** A2: l'interno e' provvisorio (generato in blocco e non ancora confermato). */
export function provisionalNumbering(p) {
  if (!p || !str(p.internal_number)) return false;
  if (typeof p.provisional_numbering === 'boolean') return p.provisional_numbering;
  const lotto = p.metadata && typeof p.metadata === 'object' ? p.metadata.census_batch : null;
  return Boolean(lotto && lotto.provisional_numbering);
}

/** «Visura catastale del 01/10/2026 · rif. prot. 123» (la fonte registrata). */
export function evidenceText(p, catalog = UNIT_SOURCES_CATALOG) {
  if (!p || !p.cadastral_evidence_type) return '';
  const voce = (catalog.evidence_types || []).find((x) => x.value === p.cadastral_evidence_type);
  const data = str(p.cadastral_evidence_date);
  const it = /^\d{4}-\d{2}-\d{2}/.test(data) ? `${data.slice(8, 10)}/${data.slice(5, 7)}/${data.slice(0, 4)}` : data;
  return [voce ? voce.label : p.cadastral_evidence_type, it ? `del ${it}` : '', str(p.cadastral_evidence_ref) ? `· rif. ${str(p.cadastral_evidence_ref)}` : '']
    .filter(Boolean).join(' ');
}

/** L'etichetta di una provenienza (null = «Non indicata»). */
export function sourceLabel(catalog, value) {
  if (value === null || value === undefined || value === '') return UNKNOWN_SOURCE_LABEL;
  const voce = ((catalog && catalog.data_sources) || []).find((x) => x.value === value);
  return voce ? voce.label : String(value);
}

/** Il tono del badge di una provenienza. */
export function sourceTone(value) {
  if (value === 'verified') return 'ok';
  if (value === 'declared') return 'role';
  if (value === 'estimated') return 'warn';
  return 'gray';
}

/** Le provenienze che si possono scegliere: in gruppo mai «Verificata». */
export function sourceOptions(catalog, { bulk = false } = {}) {
  return ((catalog && catalog.data_sources) || []).filter((x) => !bulk || x.bulk !== false);
}

export function classLabel(catalog, value) {
  const voce = ((catalog && catalog.classes) || []).find((x) => x.value === value);
  return voce ? voce.label : String(value || '');
}

/** Fonte catastale documentabile (D3): comune catastale, foglio, particella,
 *  subalterno; la sezione e' facoltativa. Stessa regola del vincolo della 100. */
export function hasCadastralSource(fields, catalog) {
  const campi = (catalog && catalog.cadastral_source_fields)
    || ['cadastral_municipality_code', 'cadastral_sheet', 'cadastral_parcel', 'cadastral_subunit'];
  return campi.every((k) => str(fields && fields[k]) !== '');
}

/** Come si legge la risposta di GET .../composition (stato, due indicatori,
 *  righe per classe). `null` se la risposta non e' una composizione. */
export function compositionView(c) {
  if (!c || !c.composition || !c.by_class) return null;
  const catalog = c.catalog || {};
  const comp = c.composition;
  const stati = Object.fromEntries(((catalog.completion) || []).map((x) => [x.value, x.label]));
  const create = c.units_created || {};
  const dati = c.data_completeness || {};
  const num = (v) => (Number.isInteger(v) && v >= 0 ? v : null);
  const righe = ((catalog.classes) || []).map((k) => {
    const r = c.by_class[k.value] || {};
    return {
      value: k.value, label: k.label,
      declared: num(r.declared) === null ? 'Non so' : String(r.declared),
      present: String(num(r.present) ?? 0),
      toCreate: num(r.to_create) === null ? '—' : String(r.to_create),
      over: num(r.over_declared) ? plurale(r.over_declared, 'in più', 'in più') : '',
    };
  });
  // indicatore 3 (A1): attendibilita' delle schede, separato da quantita' e completezza
  const ver = c.verification || {};
  const verificate = num(ver.verified) ?? 0;
  const parziali = num(ver.partial) ?? 0;
  const daVerificare = num(ver.to_verify) ?? 0;
  const daRiconfermare = num(ver.to_reconfirm) ?? 0;
  const presenti = num(create.present) ?? 0;
  const dichiarate = num(create.declared);
  const daCreare = num(create.to_create);
  const schede = num(dati.units) ?? 0;
  const complete = num(dati.complete) ?? 0;
  return {
    state: c.completion,
    stateLabel: stati[c.completion] || c.completion || '',
    draft: c.completion === 'draft',
    complete: comp.complete === true,
    unitsIndicator: dichiarate === null
      ? `${plurale(presenti, 'unità presente', 'unità presenti')} · composizione da dichiarare`
      : `${presenti} di ${dichiarate} dichiarate${daCreare ? ` · ${plurale(daCreare, 'da creare', 'da creare')}` : ''}`,
    unitsDone: create.complete === true,
    dataIndicator: schede ? `${complete} di ${plurale(schede, 'scheda completa', 'schede complete')}` : 'Nessuna scheda',
    dataDone: schede > 0 && complete === schede,
    verificationIndicator: schede ? `${verificate} di ${plurale(schede, 'scheda verificata', 'schede verificate')}` : 'Nessuna scheda',
    verificationNote: schede ? [parziali ? plurale(parziali, 'parziale', 'parziali') : '',
      daVerificare ? `${daVerificare} da completare${daRiconfermare ? ` (${daRiconfermare} da riconfermare)` : ''}` : '']
      .filter(Boolean).join(' · ') : '',
    verificationDone: schede > 0 && verificate === schede,
    toCreate: daCreare || 0,
    canGenerate: comp.complete === true && (daCreare || 0) > 0,
    totalNote: comp.total_matches === false
      ? `La somma delle classi (${comp.sum}) non corrisponde al totale dichiarato (${comp.units_declared}).` : '',
    rows: righe,
  };
}

/** I dati minimi mancanti di una scheda, in parole. */
export function missingText(unit, catalog) {
  const etichette = Object.fromEntries(((catalog && catalog.minimum_data) || []).map((x) => [x.value, x.label]));
  const mancanti = (unit && unit.missing) || [];
  // senza il valore manca anche la sua provenienza: si dice una volta sola
  const coppie = { surface_source: 'surface_sqm', cadastral_category_source: 'cadastral_category' };
  const voci = mancanti.filter((k) => !(coppie[k] && mancanti.includes(coppie[k])));
  return voci.length ? `Mancano: ${voci.map((k) => etichette[k] || k).join(', ')}` : 'Completa';
}

const FLOOR_ORDER = { S: -0.5, T: 0, PT: 0, R: 0.5 };

function floorKey(floor) {
  const f = str(floor).toUpperCase();
  if (/^-?\d+$/.test(f)) return Number(f);
  if (f in FLOOR_ORDER) return FLOOR_ORDER[f];
  return f ? 1000 : 2000;
}

function internalKey(v) {
  const s = str(v);
  return /^\d+$/.test(s) ? [0, Number(s), ''] : [1, 0, s.toUpperCase()];
}

/** Gli interni nell'ordine della palazzina: scala, piano, interno, poi id.
 *  Con il filtro «da completare» solo le schede con dati mancanti. */
export function interiorRows(units, { onlyIncomplete = false } = {}) {
  const lista = (units || []).filter((u) => !onlyIncomplete || ((u.missing || []).length > 0));
  return [...lista].sort((a, b) => {
    const sa = str(a.staircase).toUpperCase();
    const sb = str(b.staircase).toUpperCase();
    if (sa !== sb) return sa < sb ? -1 : 1;
    const fa = floorKey(a.floor);
    const fb = floorKey(b.floor);
    if (fa !== fb) return fa - fb;
    const ia = internalKey(a.internal_number);
    const ib = internalKey(b.internal_number);
    for (let i = 0; i < 3; i += 1) if (ia[i] !== ib[i]) return ia[i] < ib[i] ? -1 : 1;
    return Number(a.id) - Number(b.id);
  });
}

/** «Pertinenza di IMM-412» / «Da collegare» / «Con 2 pertinenze» / ''. */
export function pertinenzeText(u) {
  if (u && u.parent) return `Pertinenza di ${u.parent.code || `#${u.parent.id}`}${u.parent.same_building === false ? ' (altro edificio)' : ''}`;
  if (u && u.parent_property_id) return 'Pertinenza';
  if (u && u.unit_class === 'pertinenze') return 'Pertinenza da collegare';
  const n = Number(u && u.pertinenze_count) || 0;
  return n ? `Con ${plurale(n, 'pertinenza', 'pertinenze')}` : '';
}

// --- Composizione (PUT) -------------------------------------------------------------------

const CLASSI = ['residential', 'commercial', 'other', 'pertinenze'];

/** Il corpo di PUT .../composition dal foglio. Campo vuoto = «Non so» (null),
 *  mai 0 per difetto. `setTotal` solo con le quattro classi note. */
export function compositionPayload(form, { setTotal = false, source = '' } = {}) {
  const errori = [];
  const corpo = {};
  for (const k of CLASSI) {
    const t = str(form && form[k]);
    if (!t) { corpo[k] = null; continue; }
    if (!/^\d+$/.test(t)) { errori.push('I numeri della composizione sono interi, 0 o più.'); corpo[k] = null; continue; }
    corpo[k] = Number.parseInt(t, 10);
  }
  const complete = CLASSI.every((k) => corpo[k] !== null);
  corpo.set_total = Boolean(setTotal && complete);
  if (corpo.set_total && str(source)) corpo.units_declared_source = str(source);
  return { body: corpo, complete, sum: complete ? CLASSI.reduce((s, k) => s + corpo[k], 0) : null,
    errors: [...new Set(errori)] };
}

/** Il foglio parte dai valori salvati (null = vuoto, «Non so»). */
export function compositionForm(c) {
  const comp = (c && c.composition) || {};
  return Object.fromEntries(CLASSI.map((k) => [k, comp[k] === null || comp[k] === undefined ? '' : String(comp[k])]));
}

// --- Generazione delle unità mancanti -----------------------------------------------------

/** I gruppi proposti: uno per classe con unità da creare, della prima
 *  tipologia ammessa; quantità = da creare (modificabile). */
export function defaultGroups(c) {
  const catalog = (c && c.catalog) || {};
  return ((catalog.classes) || []).filter((k) => ((c.by_class || {})[k.value] || {}).to_create > 0).map((k) => ({
    unit_class: k.value, count: String(c.by_class[k.value].to_create), property_type: k.property_types[0],
    surface_sqm: '', surface_source: '', cadastral_category: '', cadastral_category_source: '',
    floor: '', staircase: '', pertinenza_kind: '', numbering: false, numbering_start: '1',
  }));
}

/** Il corpo di POST .../units/generate dai gruppi del foglio, con gli errori
 *  da mostrare prima di inviare. Superficie e categoria solo con la loro
 *  provenienza (e viceversa); mai «Verificata» in gruppo. */
export function generatePayload(groups, { clientRequestId, dryRun = true, expected = null, confirmOver = false,
  confirmSimilar = false, maxUnits = 300 } = {}) {
  const errori = [];
  const gruppi = [];
  let totale = 0;
  (groups || []).forEach((g, i) => {
    const n = `Gruppo ${i + 1}`;
    const count = str(g.count);
    if (!/^\d+$/.test(count) || Number(count) < 1) { errori.push(`${n}: indica quante unità (almeno 1).`); return; }
    const corpo = { unit_class: g.unit_class, count: Number(count), property_type: g.property_type };
    totale += corpo.count;
    for (const [valore, fonte, nome] of [['surface_sqm', 'surface_source', 'superficie'], ['cadastral_category', 'cadastral_category_source', 'categoria']]) {
      const v = str(g[valore]);
      const f = str(g[fonte]);
      if (v && !f) errori.push(`${n}: indica la provenienza della ${nome}.`);
      if (!v && f) errori.push(`${n}: la provenienza della ${nome} richiede il valore.`);
      if (f === 'verified') errori.push(`${n}: «Verificata» si indica unità per unità, non in gruppo.`);
      if (v && f) {
        if (valore === 'surface_sqm') {
          const num = Number(v.replace(',', '.'));
          if (Number.isNaN(num) || num < 0) errori.push(`${n}: superficie non valida.`);
          else corpo.surface_sqm = num;
        } else corpo.cadastral_category = v;
        corpo[fonte] = f;
      }
    }
    for (const k of ['floor', 'staircase']) if (str(g[k])) corpo[k] = str(g[k]);
    if (g.unit_class === 'pertinenze' && str(g.pertinenza_kind)) corpo.pertinenza_kind = str(g.pertinenza_kind);
    if (g.numbering) {
      const inizio = str(g.numbering_start);
      if (!/^\d+$/.test(inizio)) errori.push(`${n}: il primo interno è un numero intero.`);
      else corpo.numbering_start = Number(inizio);
    }
    gruppi.push(corpo);
  });
  if (!gruppi.length && !errori.length) errori.push('Aggiungi almeno un gruppo di unità.');
  if (totale > maxUnits) errori.push(`Al massimo ${maxUnits} unità per volta.`);
  const body = { client_request_id: clientRequestId, dry_run: dryRun, groups: gruppi };
  if (!dryRun) body.expected_existing = expected;
  if (confirmOver) body.confirm_over_declared = true;
  if (confirmSimilar) body.confirm_similar = true;
  return { body, total: totale, errors: [...new Set(errori)] };
}

/** Le righe dell'anteprima: per classe dichiarate, presenti, mancanti,
 *  richieste ed eventuale eccedenza. */
export function planRows(preview, catalog) {
  const piano = (preview && preview.plan) || {};
  return ((catalog && catalog.classes) || []).filter((k) => piano[k.value]).map((k) => {
    const p = piano[k.value];
    return { value: k.value, label: k.label, declared: p.declared, present: p.present, missing: p.missing,
      requested: p.requested, over: p.over_declared };
  });
}

export function hasOverDeclared(preview) {
  return Object.values((preview && preview.plan) || {}).some((p) => Number(p.over_declared) > 0);
}

/** Il testo dopo la conferma: «12 unità create» (o la replica). */
export function generatedText(esito) {
  const n = Number(esito && esito.created) || 0;
  return esito && esito.replica ? `Questo lotto era già stato creato: ${plurale(n, 'unità', 'unità')}, nessun doppione.`
    : `${plurale(n, 'unità creata', 'unità create')} come schede di censimento in bozza.`;
}

export function undoBatchText(esito) {
  const fatte = ((esito && esito.undone) || []).length;
  const rimaste = ((esito && esito.not_undone) || []).length;
  return `${plurale(fatte, 'unità annullata', 'unità annullate')}${rimaste ? `; ${plurale(rimaste, 'resta', 'restano')} perché già modificate o collegate` : ''}.`;
}

// --- Modifica di gruppo -------------------------------------------------------------------

/** Il corpo di POST .../units/bulk-update: solo i campi compilati o scelti.
 *  Un valore nuovo senza la sua provenienza la riporta a «Non indicata»
 *  (lo fa il server): il foglio lo dice prima di salvare. */
export function bulkPayload(form, ids, { residentialRustic = false } = {}) {
  const errori = [];
  const changes = {};
  const f = form || {};
  const sup = str(f.surface_sqm);
  if (sup) {
    const n = Number(sup.replace(',', '.'));
    if (Number.isNaN(n) || n < 0) errori.push('Superficie non valida.');
    else changes.surface_sqm = n;
  }
  if (str(f.surface_source)) changes.surface_source = str(f.surface_source);
  if (str(f.cadastral_category)) changes.cadastral_category = str(f.cadastral_category);
  if (str(f.cadastral_category_source)) changes.cadastral_category_source = str(f.cadastral_category_source);
  if (str(f.floor)) changes.floor = str(f.floor);
  if (str(f.staircase)) changes.staircase = str(f.staircase);
  if (residentialRustic) changes.census_class = 'residential';
  const lista = [...new Set((ids || []).map(Number).filter((x) => Number.isInteger(x) && x > 0))];
  if (!lista.length) errori.push('Seleziona almeno un’unità.');
  if (!Object.keys(changes).length) errori.push('Indica almeno un dato da modificare.');
  if (changes.surface_source === 'verified' || changes.cadastral_category_source === 'verified') {
    errori.push('«Verificata» si registra dalla scheda della singola unità, con la fonte consultata.');
  }
  const avvisi = [];
  if ('surface_sqm' in changes && !('surface_source' in changes)) avvisi.push('La provenienza della superficie tornerà «Non indicata».');
  if ('cadastral_category' in changes && !('cadastral_category_source' in changes)) avvisi.push('La provenienza della categoria tornerà «Non indicata».');
  return { body: { property_ids: lista, changes }, errors: errori, warnings: avvisi };
}

// --- Scheda della singola unità: provenienza dei dati -------------------------------------

/** La PATCH della scheda dell'unità per superficie, categoria e loro
 *  provenienza: solo cio' che cambia. Un valore cambiato porta SEMPRE la sua
 *  provenienza (anche «Non indicata» = null): la conferma e' esplicita. */
export function unitSourcesPatch(property, form, { today = null, identityChanged = false } = {}) {
  const p = property || {};
  const f = form || {};
  const patch = {};
  const errori = [];
  const supOld = p.surface_sqm === null || p.surface_sqm === undefined || p.surface_sqm === '' ? null : Number(p.surface_sqm);
  const supText = str(f.surface_sqm);
  let supNew = null;
  if (supText) {
    supNew = Number(supText.replace(',', '.'));
    if (Number.isNaN(supNew) || supNew < 0) { errori.push('Superficie non valida.'); supNew = supOld; }
  }
  const fonteSup = str(f.surface_source) || null;
  // con i riferimenti catastali cambiati, una «Verificata» lasciata scelta e' una RICONFERMA esplicita
  const riconferma = (v) => identityChanged && v === 'verified';
  if (supNew !== supOld) { patch.surface_sqm = supNew; patch.surface_source = supNew === null ? null : fonteSup; }
  else if (fonteSup !== (p.surface_source || null) || riconferma(fonteSup)) patch.surface_source = fonteSup;
  const fonteCat = str(f.cadastral_category_source) || null;
  const catNew = str(f.cadastral_category) || null;
  if (catNew !== (p.cadastral_category || null)) { patch.cadastral_category = catNew; patch.cadastral_category_source = catNew === null ? null : fonteCat; }
  else if (fonteCat !== (p.cadastral_category_source || null) || riconferma(fonteCat)) patch.cadastral_category_source = fonteCat;
  const finale = { ...p, ...patch };
  if (finale.surface_source && (finale.surface_sqm === null || finale.surface_sqm === undefined || finale.surface_sqm === '')) {
    errori.push('Una provenienza della superficie senza la superficie non si salva.');
  }
  if (finale.cadastral_category_source && !finale.cadastral_category) {
    errori.push('Una provenienza della categoria senza la categoria non si salva.');
  }
  // «Verificata»: ogni volta (anche una riconferma) con la fonte consultata
  // e registrata nella stessa richiesta; mai dalla sola compilazione dei campi
  if (patch.surface_source === 'verified' || patch.cadastral_category_source === 'verified') {
    const tipo = str(f.cadastral_evidence_type);
    const data = str(f.cadastral_evidence_date);
    if (!tipo || !data) errori.push('«Verificata» richiede la fonte consultata: tipo di documento e data.');
    else if (!/^\d{4}-\d{2}-\d{2}$/.test(data) || (today && data > today)) errori.push('La data della consultazione non è valida.');
    else {
      patch.cadastral_evidence_type = tipo;
      patch.cadastral_evidence_date = data;
      patch.cadastral_evidence_ref = str(f.cadastral_evidence_ref) || null;
    }
  }
  return { patch, errors: errori };
}
