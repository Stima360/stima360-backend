// STIMA360 OS — census/census-sheets.js (CENSIMENTO-1 Fase 4)
//
// I fogli (bottom-sheet su smartphone, dialog su desktop) del censimento,
// nel percorso dell'agente di CENSIMENTO-0 §2:
//
//   S1  openBuildingSheet      «Nuova palazzina» / modifica palazzina (PATCH
//                              con la conferma della propagazione, §6.4)
//   S3  openUnitSheet          «Nuova unita'» (in palazzina, singola, o
//   S4  (dentro S3)            pertinenza «Si'»), con il foglio della categoria
//   S6                         catastale e «Duplica» come precompilazione
//   S5  showToast              «IMM-412 · 2º piano aggiunta — Annulla» (undo-create)
//   S7  openPertinenzaSheet    «E' un'unita' catastale separata? Si' / No / Non lo so»
//       openLinkExistingSheet  «Collega esistente» (ricerca immobili + link)
//       openResolveSheet       «Chiarisci › E' separata (crea / collega) · E' compresa»
//       openAccessorySheet     modifica / elimina accessorio
//       openTakeInChargeDialog «Porti IMM-412 nel lavoro commerciale? ☑ pertinenze»
//
// Regole comuni:
//  * nessun campo obbligatorio oltre a quelli indispensabili; «Da verificare»
//    = categoria vuota, mai un codice inventato; la categoria non si deduce
//    mai dalla tipologia;
//  * ogni creazione porta `client_request_id` generato all'APERTURA del foglio
//    e riusato nei retry (`census-api.js`): un timeout seguito da «Riprova»
//    non crea una seconda riga; dopo un successo la chiave si rigenera;
//  * SIMILAR_FOUND -> banner nel foglio (non modale): «Apri / Salva comunque»;
//    CADASTRAL_DUPLICATE -> blocco con «Apri IMM-405», nessun salva-comunque;
//  * il bottone si disabilita durante il salvataggio (doppio invio) e il
//    foglio resta aperto con i dati in caso di errore («Riprova»);
//  * ESITO INCERTO (REV 2, R7/R8): rete caduta o risposta 2xx illeggibile.
//    Il foglio conserva chiave e corpo inviati; «Riprova» rimanda la stessa
//    chiave (anche dopo una modifica: se il server non ha scritto, crea i
//    dati nuovi; se ha scritto, risponde IDEMPOTENCY_KEY_REUSED e il foglio
//    RECUPERA la riga rimandando il primo corpo - una replica, mai una
//    creazione - e offre «Applica le modifiche» come PATCH o «Apri»). La
//    chiave non si rigenera mai da sola finche' l'esito resta incerto;
//  * la sezione catastale e' a tre stati (R1): non conosciuta / nessuna
//    (accertato) / con sezione, in creazione, modifica e «Chiarisci»;
//  * i cataloghi arrivano da form-options (`options`), mai scritti qui.

import { navigate } from '../core/router.js';
import { escapeHtml } from '../components/st-table.js';
import { provincesOf, municipalitiesOf, microzonesOf, cascadeLocation } from '../components/property-form.js';
import * as api from './census-api.js';
import {
  FLOOR_CHIPS, SECTION_MODES, buildBuildingPayload, buildUnitPayload, categoryGroups, errorMessage, filterCategories,
  labelOf, propagationConfirmText, recoveredText, sameUnitBody, sectionMode, similarText, suggestionsFor,
  uncertainText, unitPatchDiff,
} from './census-model.js';

const str = (v) => (v === null || v === undefined ? '' : String(v));

// --- Pezzi di markup -------------------------------------------------------------

function chipsHtml(name, voci, selected, { multiline = false } = {}) {
  return `<div class="census-chips${multiline ? ' census-chips-wrap' : ''}" data-chips="${escapeHtml(name)}">${voci.map((v) => `<button type="button" class="chip${v.value === selected ? ' active' : ''}" data-chip="${escapeHtml(name)}" data-value="${escapeHtml(v.value)}">${escapeHtml(v.label)}</button>`).join('')}</div>`;
}

function fieldHtml(id, label, inputHtml, hint) {
  return `<div class="form-field"><label for="${escapeHtml(id)}">${escapeHtml(label)}</label>${inputHtml}${hint ? `<small class="muted">${escapeHtml(hint)}</small>` : ''}</div>`;
}

function inputHtml(id, value, { type = 'text', maxlength, placeholder, inputmode, min, step } = {}) {
  return `<input type="${type}" id="${escapeHtml(id)}" class="input" value="${escapeHtml(str(value))}"${maxlength ? ` maxlength="${maxlength}"` : ''}${placeholder ? ` placeholder="${escapeHtml(placeholder)}"` : ''}${inputmode ? ` inputmode="${inputmode}"` : ''}${min !== undefined ? ` min="${min}"` : ''}${step ? ` step="${step}"` : ''}>`;
}

/** Un selettore di chips: tiene il valore corrente e ridisegna lo stato attivo. */
function bindChips(root, name, initial, onChange) {
  let valore = initial;
  const bottoni = root.querySelectorAll(`[data-chip="${name}"]`);
  const aggiorna = () => bottoni.forEach((b) => b.classList.toggle('active', b.dataset.value === valore));
  bottoni.forEach((b) => b.addEventListener('click', () => {
    valore = b.dataset.value === valore && name !== 'floor' ? valore : b.dataset.value;
    aggiorna();
    if (onChange) onChange(valore);
  }));
  aggiorna();
  return { get: () => valore, set: (v) => { valore = v; aggiorna(); } };
}

function setError(root, messaggio) {
  const el = root.querySelector('[data-error]');
  if (el) el.textContent = messaggio || '';
}

function setBusy(bottone, busy, testo) {
  if (!bottone) return;
  bottone.disabled = busy;
  if (busy) { bottone.dataset.label = bottone.dataset.label || bottone.textContent; bottone.textContent = 'Salvataggio…'; }
  else bottone.textContent = testo || bottone.dataset.label || bottone.textContent;
}

/** Sezione catastale a tre stati (R1): chips + campo testo visibile solo con
 *  «Con sezione». `initial` e' il valore salvato (null / '' / stringa). */
export function sectionFieldHtml(prefix, initial) {
  const mode = sectionMode(initial);
  return `<div class="form-field" data-section-field><label for="${escapeHtml(prefix)}-section">Sezione catastale</label>
      ${chipsHtml('cadastral_section_mode', SECTION_MODES, mode, { multiline: true })}
      ${inputHtml(`${prefix}-section`, mode === 'value' ? initial : '', { maxlength: 5, placeholder: 'es. A, B, URB' })}
      <small class="muted">In visura: «Sezione» vuota = nessuna; se non hai la visura lascia «Non conosciuta».</small></div>`;
}

export function bindSectionField(root, prefix, initial) {
  const input = root.querySelector(`#${prefix}-section`);
  const mostra = (mode) => { input.hidden = mode !== 'value'; input.disabled = mode !== 'value'; };
  const chips = bindChips(root, 'cadastral_section_mode', sectionMode(initial), mostra);
  mostra(chips.get());
  return {
    mode: () => chips.get(),
    text: () => str(input.value),
    /** null = non conosciuta, '' = nessuna, stringa = valorizzata; undefined = «con sezione» ma vuota (errore). */
    value: () => {
      const m = chips.get();
      if (m === 'unknown') return null;
      if (m === 'none') return '';
      return str(input.value).trim() || undefined;
    },
  };
}

const SEZIONE_VUOTA = 'Hai scelto «Con sezione»: scrivi la sezione, oppure scegli «Non conosciuta» o «Nessuna».';

/** Il dialog figlio per i fogli annidati (categoria, ricerca), creato una
 *  volta accanto al dialog principale. */
function childDialog(dialogEl, suffix) {
  const id = `${dialogEl.id || 'census'}-${suffix}`;
  let el = dialogEl.parentNode ? dialogEl.parentNode.querySelector(`#${id}`) : null;
  if (!el) {
    el = document.createElement('dialog');
    el.id = id;
    el.className = 'modal census-sheet';
    (dialogEl.parentNode || document.body).appendChild(el);
  }
  return el;
}

// --- Territorio (Comune a tendina dal catalogo, §2 S1) ---------------------------

function territoryHtml(prefix, loc) {
  return `
    <div class="form-grid-2">
      ${fieldHtml(`${prefix}-region`, 'Regione', `<select id="${prefix}-region" class="input"></select>`)}
      ${fieldHtml(`${prefix}-province`, 'Provincia', `<select id="${prefix}-province" class="input"></select>`)}
      ${fieldHtml(`${prefix}-city`, 'Comune', `<select id="${prefix}-city" class="input"></select>`)}
      ${fieldHtml(`${prefix}-microzone`, 'Microzona', `<select id="${prefix}-microzone" class="input"></select>`)}
    </div>
    <div class="form-grid-2">
      ${fieldHtml(`${prefix}-address`, 'Via', inputHtml(`${prefix}-address`, loc.address, { maxlength: 250 }))}
      ${fieldHtml(`${prefix}-civic`, 'Civico', inputHtml(`${prefix}-civic`, loc.civic_number, { maxlength: 30 }))}
    </div>`;
}

function options(values, selected, placeholder) {
  const list = [`<option value="">${escapeHtml(placeholder)}</option>`];
  for (const v of values) {
    const o = typeof v === 'string' ? { value: v, label: v } : v;
    list.push(`<option value="${escapeHtml(o.value)}"${o.value === selected ? ' selected' : ''}>${escapeHtml(o.label)}</option>`);
  }
  return list.join('');
}

/** Regione -> Provincia -> Comune -> Microzona, come nel form Immobili
 *  (stesse funzioni pure di components/property-form.js). */
function bindTerritory(root, prefix, tree, initial) {
  let loc = { region: str(initial.region), province: str(initial.province), city: str(initial.city), microzone: str(initial.microzone) };
  const sel = {
    region: root.querySelector(`#${prefix}-region`), province: root.querySelector(`#${prefix}-province`),
    city: root.querySelector(`#${prefix}-city`), microzone: root.querySelector(`#${prefix}-microzone`),
  };
  function render() {
    sel.region.innerHTML = options(tree.map((r) => r.name), loc.region, 'Regione');
    sel.province.innerHTML = options(provincesOf(tree, loc.region).map((x) => ({ value: x.code, label: `${x.name} (${x.code})` })), loc.province, loc.region ? 'Provincia' : 'Prima la regione');
    sel.city.innerHTML = options(municipalitiesOf(tree, loc.region, loc.province).map((m) => m.name), loc.city, loc.province ? 'Comune' : 'Prima la provincia');
    sel.microzone.innerHTML = options(microzonesOf(tree, loc.region, loc.province, loc.city), loc.microzone, loc.city ? 'Microzona (facoltativa)' : 'Prima il comune');
    for (const k of Object.keys(sel)) sel[k].value = loc[k];
  }
  for (const level of Object.keys(sel)) {
    sel[level].addEventListener('change', () => {
      loc = cascadeLocation(tree, { ...loc, [level]: sel[level].value }, level);
      render();
    });
  }
  render();
  return {
    get: () => ({
      ...loc,
      address: str(root.querySelector(`#${prefix}-address`).value).trim(),
      civic_number: str(root.querySelector(`#${prefix}-civic`).value).trim(),
    }),
  };
}

// --- S8: banner simili / blocco duplicato -----------------------------------------

function similarBannerHtml(simili) {
  // «Apri» per ogni candidato con un id: le palazzine simili hanno id e nome,
  // non un codice immobile (R6); la destinazione la decide chi chiama.
  const righe = simili.slice(0, 5).map((s) => `<li>${escapeHtml(similarText(s))}${s.id ? ` <button type="button" class="btn ghost btn-small" data-open-similar="${escapeHtml(s.id)}">Apri</button>` : ''}</li>`).join('');
  return `<div class="census-banner" data-similar-banner>
      <strong>Possibile doppione</strong>
      <ul>${righe}</ul>
      <div class="action-bar"><button type="button" class="btn primary" data-save-anyway>Salva comunque</button></div>
    </div>`;
}

function bindSimilarBanner(root, onSaveAnyway, apri) {
  root.querySelectorAll('[data-open-similar]').forEach((b) => b.addEventListener('click', () => apri(b.dataset.openSimilar)));
  const salva = root.querySelector('[data-save-anyway]');
  if (salva) salva.addEventListener('click', onSaveAnyway);
}

function duplicateHtml(existing) {
  const code = existing && existing.code ? existing.code : (existing && existing.id ? `#${existing.id}` : '');
  return `<div class="census-banner census-banner-block" data-duplicate-banner>
      <strong>Questo subalterno è già censito${code ? ` come ${escapeHtml(code)}` : ''}</strong>
      ${existing && existing.id ? `<div class="action-bar"><button type="button" class="btn" data-open-duplicate="${escapeHtml(existing.id)}">Apri ${escapeHtml(code)}</button></div>` : ''}
    </div>`;
}

// --- S1: palazzina --------------------------------------------------------------------

/**
 * @param {HTMLDialogElement} dialogEl
 * @param {{options: object, building?: object, onSaved?: Function}} opts
 *   options  = GET /api/property/form-options (territorio, building_types, units_declared_sources)
 *   building = presente in modifica (PATCH); assente in creazione (POST)
 */
export function openBuildingSheet(dialogEl, { options: opzioni, building = null, onSaved } = {}) {
  const isEdit = !!building;
  const b = building || {};
  const tree = opzioni.territory || [];
  const tipi = opzioni.building_types || [];
  const fonti = opzioni.units_declared_sources || [];
  let clientRequestId = isEdit ? null : api.newClientRequestId();

  dialogEl.className = 'modal census-sheet';
  dialogEl.innerHTML = `
    <form data-building-form novalidate>
      <h2 class="census-sheet-title">${isEdit ? 'Modifica palazzina' : 'Nuova palazzina'}</h2>
      ${isEdit ? '' : '<p class="muted">Bastano Comune, via e civico. Il resto si completa dopo.</p>'}
      ${territoryHtml('bs', { address: b.address, civic_number: b.civic_number })}
      ${fieldHtml('bs-name', 'Nome (facoltativo)', inputHtml('bs-name', b.name, { maxlength: 120, placeholder: 'es. Palazzina via Roma 10' }))}
      <div class="form-field"><label>Tipo di edificio</label>${chipsHtml('building_type', tipi, str(b.building_type) || 'condominio', { multiline: true })}</div>
      <div class="form-grid-2">
        ${fieldHtml('bs-declared', 'Quante unità risultano?', inputHtml('bs-declared', b.units_declared, { type: 'number', inputmode: 'numeric', min: 0, step: '1' }), 'Principali + pertinenze autonome')}
        <div class="form-field"><label>Fonte</label>${chipsHtml('units_declared_source', fonti, str(b.units_declared_source), { multiline: true })}</div>
      </div>
      ${fieldHtml('bs-notes', 'Note', `<textarea id="bs-notes" class="input" rows="2">${escapeHtml(str(b.notes))}</textarea>`)}
      <div data-banner></div>
      <div class="field-error" data-error></div>
      <div class="modal-actions">
        <button type="button" class="btn ghost" data-cancel>Annulla</button>
        <button type="submit" class="btn primary" data-submit>${isEdit ? 'Salva' : 'Crea palazzina'}</button>
      </div>
    </form>`;
  if (!dialogEl._open && !dialogEl.open) dialogEl.showModal();

  const form = dialogEl.querySelector('[data-building-form]');
  const territorio = bindTerritory(dialogEl, 'bs', tree, b);
  const tipo = bindChips(dialogEl, 'building_type', str(b.building_type) || 'condominio');
  const fonte = bindChips(dialogEl, 'units_declared_source', str(b.units_declared_source));
  const submit = dialogEl.querySelector('[data-submit]');
  dialogEl.querySelector('[data-cancel]').addEventListener('click', () => dialogEl.close());

  function stato(confirmSimilar) {
    return {
      ...territorio.get(),
      name: dialogEl.querySelector('#bs-name').value, building_type: tipo.get(),
      units_declared: dialogEl.querySelector('#bs-declared').value, units_declared_source: fonte.get(),
      notes: dialogEl.querySelector('#bs-notes').value, client_request_id: clientRequestId, confirm_similar: confirmSimilar,
    };
  }

  function editPayload() {
    const s = stato(false);
    const corpo = {};
    const campi = ['name', 'building_type', 'region', 'province', 'city', 'microzone', 'address', 'civic_number', 'units_declared_source', 'notes'];
    for (const k of campi) {
      const nuovo = str(s[k]).trim();
      if (nuovo !== str(b[k]).trim()) corpo[k] = nuovo || null;
    }
    const dichiarate = str(s.units_declared).trim();
    if (dichiarate !== str(b.units_declared)) corpo.units_declared = dichiarate === '' ? null : Number.parseInt(dichiarate, 10);
    for (const k of ['building_type']) if (corpo[k] === null) delete corpo[k];     // NOT NULL: mai null
    return corpo;
  }

  let saving = false;
  async function salva(confirmSimilar) {
    if (saving) return;
    setError(dialogEl, '');
    dialogEl.querySelector('[data-banner]').innerHTML = '';
    const payload = isEdit ? editPayload() : buildBuildingPayload(stato(confirmSimilar));
    if (isEdit && !Object.keys(payload).length) { dialogEl.close(); return; }
    if (isEdit && ['address', 'civic_number', 'city', 'region', 'province', 'microzone'].some((k) => k in payload)
        && !confirmSimilar) {
      // §6.4: la propagazione va detta prima. Un secondo invio conferma.
      const testo = propagationConfirmText(b.counters);
      dialogEl.querySelector('[data-banner]').innerHTML = `<div class="census-banner" data-propagation-banner>${escapeHtml(testo)}
        <div class="action-bar"><button type="button" class="btn primary" data-confirm-propagation>Conferma</button></div></div>`;
      dialogEl.querySelector('[data-confirm-propagation]').addEventListener('click', () => salva(true));
      return;
    }
    saving = true;
    setBusy(submit, true);
    let esito;
    try {
      esito = isEdit ? await api.updateBuilding(b.id, payload) : await api.createBuilding(payload);
    } catch (error) {
      saving = false;
      setBusy(submit, false, isEdit ? 'Salva' : (error.code === 'NETWORK' ? 'Riprova' : 'Crea palazzina'));
      if (error.code === 'SIMILAR_FOUND' && error.similar.length) {
        dialogEl.querySelector('[data-banner]').innerHTML = similarBannerHtml(error.similar);
        bindSimilarBanner(dialogEl, () => salva(true), (id) => { dialogEl.close(); navigate('edifici', [id]); });
        return;
      }
      setError(dialogEl, errorMessage(error));
      return;
    }
    saving = false;
    clientRequestId = isEdit ? null : api.newClientRequestId();
    dialogEl.close();
    if (typeof onSaved === 'function') await onSaved(esito);
  }

  form.addEventListener('submit', (event) => { event.preventDefault(); salva(false); });
}

// --- S4: categoria catastale --------------------------------------------------------

/**
 * Il foglio di scelta: suggerimenti per tipologia, ricerca, tutte le
 * categorie a gruppi (storiche e stati particolari in fondo), «Lascia da
 * verificare». Nessuna assegnazione automatica.
 */
export function openCategoryPicker(dialogEl, { options: opzioni, propertyType, current, onPick } = {}) {
  const categorie = opzioni.cadastral_categories || [];
  const sugg = suggestionsFor(opzioni.cadastral_suggestions, categorie, propertyType);
  const voce = (c) => `<button type="button" class="chip${c.code === current ? ' active' : ''}" data-pick-category="${escapeHtml(c.code)}" title="${escapeHtml(c.label)}">${escapeHtml(c.code)}<small>${escapeHtml(c.label)}</small>${c.no_income ? '<em>senza rendita</em>' : ''}${c.historical ? '<em>storica</em>' : ''}</button>`;
  const gruppi = categoryGroups(categorie);
  dialogEl.className = 'modal census-sheet';
  dialogEl.innerHTML = `
    <div data-category-picker>
      <h2 class="census-sheet-title">Categoria catastale</h2>
      ${sugg.suggested.length ? `<div class="form-field"><label>Suggerite per ${escapeHtml(labelOf(opzioni.property_types, propertyType, propertyType))}</label><div class="census-chips census-chips-wrap census-category-list">${sugg.suggested.map(voce).join('')}</div></div>` : ''}
      ${sugg.secondary.length ? `<div class="form-field"><label>Altre possibili</label><div class="census-chips census-chips-wrap census-category-list">${sugg.secondary.map(voce).join('')}</div></div>` : ''}
      ${fieldHtml('cat-search', 'Cerca', inputHtml('cat-search', '', { type: 'search', placeholder: 'codice (c6) o parola (autorimessa)' }))}
      <div data-category-results class="census-chips census-chips-wrap census-category-list"></div>
      <details class="census-details"><summary>Tutte le categorie</summary>
        ${gruppi.map((g) => `<div class="form-field"><label>${escapeHtml(g.label)}</label><div class="census-chips census-chips-wrap census-category-list">${g.items.map(voce).join('')}</div></div>`).join('')}
      </details>
      <div class="modal-actions">
        <button type="button" class="btn ghost" data-cancel>Chiudi</button>
        <button type="button" class="btn" data-leave-unverified>Lascia da verificare</button>
      </div>
    </div>`;
  if (!dialogEl._open && !dialogEl.open) dialogEl.showModal();
  const scegli = (code) => { dialogEl.close(); if (onPick) onPick(code); };
  dialogEl.querySelectorAll('[data-pick-category]').forEach((b) => b.addEventListener('click', () => scegli(b.dataset.pickCategory)));
  dialogEl.querySelector('[data-cancel]').addEventListener('click', () => dialogEl.close());
  dialogEl.querySelector('[data-leave-unverified]').addEventListener('click', () => scegli(null));
  const ricerca = dialogEl.querySelector('#cat-search');
  const risultati = dialogEl.querySelector('[data-category-results]');
  ricerca.addEventListener('input', () => {
    const term = ricerca.value;
    const trovate = term.trim() ? filterCategories(categorie, term).slice(0, 12) : [];
    risultati.innerHTML = trovate.map(voce).join('');
    risultati.querySelectorAll('[data-pick-category]').forEach((b) => b.addEventListener('click', () => scegli(b.dataset.pickCategory)));
  });
}

// --- S3 / S6: unita' -------------------------------------------------------------------

/**
 * @param {HTMLDialogElement} dialogEl
 * @param {{options: object, building?: object, parent?: object, seed?: object,
 *          lastFloor?: string, onSaved: Function, onOpen?: Function}} opts
 *   building  -> unita' in palazzina (indirizzo ereditato salvo «Ingresso diverso?»)
 *   parent    -> pertinenza «Si'» di un'unita' (parent_property_id)
 *   seed      -> «Duplica» (S6) o la tipologia scelta dal bottone
 *   onSaved(unit, {another}) -> chi chiama mostra il toast con «Annulla»
 */
export function openUnitSheet(dialogEl, { options: opzioni, building = null, parent = null, seed = {}, lastFloor = '', onSaved } = {}) {
  const tree = opzioni.territory || [];
  const tipi = opzioni.property_types || [];
  const inPalazzina = !!building;
  const isPertinenza = !!parent;
  const s = seed || {};
  const titolo = s.duplicate_of ? `Copia di ${s.duplicate_of} — completa interno e categoria`
    : (isPertinenza ? `Nuova pertinenza di ${parent.code || `#${parent.id}`}` : (inPalazzina ? 'Nuova unità' : 'Nuovo immobile singolo'));
  let clientRequestId = api.newClientRequestId();
  let categoria = str(s.cadastral_category) || '';
  let ownAddress = !inPalazzina;       // fuori palazzina l'indirizzo e' sempre proprio
  // R7/R8: gli invii dall'esito incerto {chiave, corpi: [...]} (piu' corpi se
  // l'operatore ha modificato fra un esito incerto e l'altro); finche' c'e',
  // la chiave non cambia e il retry riusa la stessa.
  let invioIncerto = null;

  function categoriaHtml() {
    const c = (opzioni.cadastral_categories || []).find((x) => x.code === categoria);
    return categoria
      ? `${escapeHtml(categoria)} <small class="muted">${escapeHtml(c ? c.label : '')}</small>${c && c.no_income ? ' <small class="muted">· unità senza rendita</small>' : ''}`
      : 'Da verificare ›';
  }

  dialogEl.className = 'modal census-sheet';
  dialogEl.innerHTML = `
    <form data-unit-form novalidate>
      <h2 class="census-sheet-title">${escapeHtml(titolo)}</h2>
      ${inPalazzina ? `<p class="muted">${escapeHtml([building.name, [building.address, building.civic_number].filter(Boolean).join(' ')].filter(Boolean).join(' · '))}</p>` : ''}
      <div class="form-field"><label>Tipologia</label>${chipsHtml('property_type', tipi, str(s.property_type) || 'apartment', { multiline: true })}</div>
      <div class="form-field"><label>Piano</label>${chipsHtml('floor', FLOOR_CHIPS, str(s.floor) || str(lastFloor))}
        <div class="census-inline"><button type="button" class="chip" data-floor-other>Altro…</button>${inputHtml('us-floor-other', (s.floor && !FLOOR_CHIPS.some((f) => f.value === String(s.floor))) ? s.floor : '', { maxlength: 50, placeholder: 'es. T+1 duplex' })}</div></div>
      <div class="form-grid-3">
        ${fieldHtml('us-staircase', 'Scala', inputHtml('us-staircase', s.staircase, { maxlength: 10 }))}
        ${fieldHtml('us-internal', 'Interno', inputHtml('us-internal', '', { maxlength: 10 }))}
        ${fieldHtml('us-surface', 'mq', inputHtml('us-surface', s.surface_sqm, { type: 'number', inputmode: 'decimal', min: 0, step: 'any' }))}
      </div>
      <div class="form-grid-3">
        ${fieldHtml('us-rooms', 'Locali', inputHtml('us-rooms', s.rooms, { type: 'number', inputmode: 'numeric', min: 0, step: '1' }))}
        ${fieldHtml('us-bedrooms', 'Camere', inputHtml('us-bedrooms', '', { type: 'number', inputmode: 'numeric', min: 0, step: '1' }))}
        ${fieldHtml('us-bathrooms', 'Bagni', inputHtml('us-bathrooms', s.bathrooms, { type: 'number', inputmode: 'numeric', min: 0, step: '1' }))}
      </div>
      <div class="form-field"><label>Categoria catastale</label>
        <button type="button" class="btn census-category-btn" data-category-btn>${categoriaHtml()}</button></div>
      <details class="census-details" data-cadastral-details><summary>Dettagli catastali (dalla visura)</summary>
        <div class="form-grid-2">
          ${fieldHtml('us-belfiore', 'Codice comune (Belfiore)', inputHtml('us-belfiore', '', { maxlength: 4 }))}
          ${fieldHtml('us-sheet', 'Foglio', inputHtml('us-sheet', '', { maxlength: 10 }))}
          ${fieldHtml('us-parcel', 'Particella', inputHtml('us-parcel', '', { maxlength: 10 }))}
          ${fieldHtml('us-subunit', 'Subalterno', inputHtml('us-subunit', '', { maxlength: 10 }))}
        </div>
        ${sectionFieldHtml('us', null)}
      </details>
      ${inPalazzina ? '<div class="form-field"><label class="census-check"><input type="checkbox" data-own-address> Ingresso diverso? (via o civico propri)</label></div>' : ''}
      <div data-address-block ${inPalazzina ? 'hidden' : ''}>${territoryHtml('us', { address: '', civic_number: '' })}</div>
      ${fieldHtml('us-notes', 'Note', `<textarea id="us-notes" class="input" rows="2"></textarea>`)}
      <div data-banner></div>
      <div class="field-error" data-error></div>
      <div class="modal-actions census-sheet-actions">
        <button type="button" class="btn ghost" data-cancel>Annulla</button>
        <button type="submit" class="btn" data-submit>Salva</button>
        ${inPalazzina ? '<button type="button" class="btn primary" data-submit-another>Salva e aggiungine un\'altra</button>' : ''}
      </div>
    </form>`;
  if (!dialogEl._open && !dialogEl.open) dialogEl.showModal();

  const form = dialogEl.querySelector('[data-unit-form]');
  const tipo = bindChips(dialogEl, 'property_type', str(s.property_type) || 'apartment');
  const altroPiano = dialogEl.querySelector('#us-floor-other');
  const piano = bindChips(dialogEl, 'floor', str(s.floor) || str(lastFloor), () => { altroPiano.value = ''; });
  dialogEl.querySelector('[data-floor-other]').addEventListener('click', () => { piano.set(''); altroPiano.focus(); });
  altroPiano.addEventListener('input', () => { if (altroPiano.value.trim()) piano.set(''); });
  const territorio = bindTerritory(dialogEl, 'us', tree, inPalazzina ? {} : { region: s.region, province: s.province, city: s.city, microzone: s.microzone });
  const blocco = dialogEl.querySelector('[data-address-block]');
  const spunta = dialogEl.querySelector('[data-own-address]');
  if (spunta) spunta.addEventListener('change', () => { ownAddress = !!spunta.checked; blocco.hidden = !ownAddress; });
  const sezione = bindSectionField(dialogEl, 'us', null);
  dialogEl.querySelector('[data-cancel]').addEventListener('click', () => dialogEl.close());
  const bottoneCategoria = dialogEl.querySelector('[data-category-btn]');
  bottoneCategoria.addEventListener('click', () => openCategoryPicker(childDialog(dialogEl, 'categoria'), {
    options: opzioni, propertyType: tipo.get(), current: categoria,
    onPick: (code) => { categoria = code || ''; bottoneCategoria.innerHTML = categoriaHtml(); },
  }));

  const v = (id) => str(dialogEl.querySelector(id).value);
  function stato(confirmSimilar) {
    return {
      client_request_id: clientRequestId, building_id: inPalazzina ? building.id : null,
      parent_property_id: isPertinenza ? parent.id : null,
      // R3: «Stabile intero» = tipologia `building` dentro una palazzina (CHECK
      // della 083); segue la chip scelta NEL foglio, non il bottone di apertura
      whole_building: inPalazzina && tipo.get() === 'building',
      property_type: tipo.get(), floor: piano.get() || altroPiano.value, staircase: v('#us-staircase'),
      internal_number: v('#us-internal'), surface_sqm: v('#us-surface'), rooms: v('#us-rooms'),
      bedrooms: v('#us-bedrooms'), bathrooms: v('#us-bathrooms'), cadastral_category: categoria,
      cadastral_municipality_code: v('#us-belfiore'), cadastral_section_mode: sezione.mode(), cadastral_section: sezione.text(),
      cadastral_sheet: v('#us-sheet'), cadastral_parcel: v('#us-parcel'), cadastral_subunit: v('#us-subunit'), internal_notes: v('#us-notes'),
      own_address: ownAddress, ...territorio.get(), confirm_similar: confirmSimilar,
    };
  }

  function mostraIncerto() {
    dialogEl.querySelector('[data-banner]').innerHTML = `<div class="census-banner" data-uncertain-banner>${escapeHtml(uncertainText())}</div>`;
    dialogEl.querySelector('[data-submit]').textContent = 'Riprova';
  }

  async function chiudiConEsito(unita, another, payload) {
    invioIncerto = null;
    clientRequestId = api.newClientRequestId();       // la prossima unita' e' un'altra richiesta
    const pianoUsato = (payload && payload.floor) || '';
    dialogEl.close();
    if (typeof onSaved === 'function') await onSaved(unita, { another: another === true, floor: pianoUsato });
  }

  /** R7: la chiave risulta gia' usata con un corpo diverso DOPO un invio
   *  dall'esito incerto: il server ha scritto il PRIMO corpo. Lo si rimanda
   *  tale e quale (replica: nessuna scrittura) per recuperare la riga, poi
   *  l'operatore sceglie: applicare le modifiche (PATCH) o aprire la scheda. */
  async function recupera(payloadAttuale, another) {
    let salvata = null;
    let corpoSalvato = null;
    for (const corpo of invioIncerto.corpi) {
      try {
        salvata = await api.createUnit(corpo);
        corpoSalvato = corpo;
        break;
      } catch (error) {
        if (error.code === 'IDEMPOTENCY_KEY_REUSED') continue;      // non era questo corpo: prova il successivo
        if (error.uncertain) mostraIncerto();
        setError(dialogEl, errorMessage(error));
        return;
      }
    }
    if (!salvata) { setError(dialogEl, errorMessage({ code: 'IDEMPOTENCY_KEY_REUSED' })); return; }
    const modifiche = unitPatchDiff(corpoSalvato, payloadAttuale);
    const codice = salvata.code || `#${salvata.id}`;
    const conModifiche = Object.keys(modifiche).length > 0;
    dialogEl.querySelector('[data-banner]').innerHTML = `<div class="census-banner" data-recovered-banner>${escapeHtml(recoveredText(salvata, conModifiche))}
        <div class="action-bar">${conModifiche ? `<button type="button" class="btn primary" data-apply-changes>Applica le modifiche a ${escapeHtml(codice)}</button>` : ''}
        <button type="button" class="btn" data-open-recovered>Apri ${escapeHtml(codice)}</button></div></div>`;
    dialogEl.querySelector('[data-open-recovered]').addEventListener('click', () => { invioIncerto = null; dialogEl.close(); navigate('immobili', [salvata.id]); });
    const applica = dialogEl.querySelector('[data-apply-changes]');
    if (applica) applica.addEventListener('click', async () => {
      setBusy(applica, true);
      let aggiornata;
      try {
        aggiornata = await api.updateProperty(salvata.id, modifiche);
      } catch (error) {
        setBusy(applica, false, `Applica le modifiche a ${codice}`);
        setError(dialogEl, errorMessage(error));
        return;
      }
      await chiudiConEsito({ ...salvata, ...aggiornata, replica: true }, another, corpoSalvato);
    });
    if (!conModifiche) {
      // stesso corpo: e' la replica di un invio riuscito, come un retry invariato
      await chiudiConEsito({ ...salvata, replica: true }, another, corpoSalvato);
    }
  }

  let saving = false;
  async function salva(confirmSimilar, another) {
    if (saving) return;
    setError(dialogEl, '');
    dialogEl.querySelector('[data-banner]').innerHTML = '';
    if (sezione.value() === undefined) { setError(dialogEl, SEZIONE_VUOTA); return; }
    const payload = buildUnitPayload(stato(confirmSimilar));
    saving = true;
    const bottoni = [dialogEl.querySelector('[data-submit]'), dialogEl.querySelector('[data-submit-another]')].filter(Boolean);
    bottoni.forEach((btn) => setBusy(btn, true));
    let unita;
    try {
      unita = await api.createUnit(payload);
    } catch (error) {
      saving = false;
      bottoni.forEach((btn) => setBusy(btn, false));
      if (error.uncertain) {
        // R7/R8: chiave e corpo restano quelli; «Riprova» li rimanda
        if (!invioIncerto) invioIncerto = { chiave: clientRequestId, corpi: [] };
        if (!invioIncerto.corpi.some((c) => sameUnitBody(c, payload))) invioIncerto.corpi.push(payload);
        mostraIncerto();
        setError(dialogEl, errorMessage(error));
        return;
      }
      if (error.code === 'IDEMPOTENCY_KEY_REUSED' && invioIncerto && !invioIncerto.corpi.every((c) => sameUnitBody(c, payload))) {
        await recupera(payload, another);
        return;
      }
      if (error.code === 'SIMILAR_FOUND' && error.similar.length) {
        dialogEl.querySelector('[data-banner]').innerHTML = similarBannerHtml(error.similar);
        bindSimilarBanner(dialogEl, () => salva(true, another), (id) => { dialogEl.close(); navigate('immobili', [id]); });
        return;
      }
      if (error.code === 'CADASTRAL_DUPLICATE') {
        dialogEl.querySelector('[data-banner]').innerHTML = duplicateHtml(error.existing);
        const apri = dialogEl.querySelector('[data-open-duplicate]');
        if (apri) apri.addEventListener('click', () => { dialogEl.close(); navigate('immobili', [apri.dataset.openDuplicate]); });
        return;
      }
      setError(dialogEl, errorMessage(error));
      return;
    }
    saving = false;
    await chiudiConEsito(unita, another, payload);
  }

  form.addEventListener('submit', (event) => { event.preventDefault(); salva(false, false); });
  const ancora = dialogEl.querySelector('[data-submit-another]');
  if (ancora) ancora.addEventListener('click', () => salva(false, true));
}

// --- S5: toast con «Annulla» -------------------------------------------------------------

const TOAST_MS = 10000;

/**
 * Un toast in fondo alla pagina con un'azione (S5). `onAction` chiama
 * `undo-create`; chi chiama ridisegna. Si chiude da solo dopo 10 s.
 */
export function showToast(host, { text, actionLabel = null, onAction = null, ms = TOAST_MS } = {}) {
  let el = host.querySelector('[data-census-toast]');
  if (!el) {
    el = document.createElement('div');
    el.className = 'census-toast';
    el.setAttribute('data-census-toast', '');
    el.dataset.censusToast = '';
    host.appendChild(el);
  }
  // R5: il timer del toast precedente si annulla, e un suo eventuale scatto
  // tardivo non puo' chiudere questo toast (il gettone lo identifica)
  if (el._toastTimer) clearTimeout(el._toastTimer);
  const gettone = {};
  el._toastToken = gettone;
  el.innerHTML = `<span data-toast-text>${escapeHtml(text)}</span>${actionLabel ? `<button type="button" class="btn ghost" data-toast-action>${escapeHtml(actionLabel)}</button>` : ''}`;
  el.hidden = false;
  const chiudi = () => { if (el._toastToken !== gettone) return; el.hidden = true; el.innerHTML = ''; el._toastTimer = null; };
  const azione = el.querySelector('[data-toast-action]');
  if (azione && onAction) {
    azione.addEventListener('click', async () => {
      azione.disabled = true;
      try { await onAction(); } finally {
        // si chiude solo se nel frattempo non e' comparso un altro toast
        if (el.querySelector('[data-toast-action]') === azione) chiudi();
      }
    });
  }
  const timer = setTimeout(chiudi, ms);
  if (timer && typeof timer.unref === 'function') timer.unref();
  el._toastTimer = timer;
  return { close: chiudi };
}

// --- S7: pertinenza «Si' / No / Non lo so» -------------------------------------------------

/**
 * «E' un'unita' catastale separata?»  Si' -> foglio unita' con
 * parent_property_id (riga `properties`); No -> accessorio `included`;
 * Non lo so -> accessorio `unknown` (badge «Da chiarire»). Nessuna unita'
 * inventata, mai.
 */
export function openPertinenzaSheet(dialogEl, { options: opzioni, property, building = null, onSaved } = {}) {
  const kinds = opzioni.accessory_kinds || [];
  let clientRequestId = api.newClientRequestId();
  dialogEl.className = 'modal census-sheet';
  dialogEl.innerHTML = `
    <form data-pertinenza-form novalidate>
      <h2 class="census-sheet-title">Aggiungi pertinenza a ${escapeHtml(property.code || `#${property.id}`)}</h2>
      <div class="form-field"><label>È un'unità catastale separata?</label>
        ${chipsHtml('answer', [{ value: 'yes', label: 'Sì' }, { value: 'no', label: 'No' }, { value: 'unknown', label: 'Non lo so' }], '')}
        <small class="muted">Lo leggi in visura: se ha un suo subalterno, è separata.</small></div>
      <div data-accessory-fields hidden>
        <div class="form-field"><label>Che cos'è?</label>${chipsHtml('kind', kinds, '', { multiline: true })}</div>
        <div class="form-grid-2">
          ${fieldHtml('pa-surface', 'mq (facoltativi)', inputHtml('pa-surface', '', { type: 'number', inputmode: 'decimal', min: 0, step: 'any' }))}
          ${fieldHtml('pa-notes', 'Note', inputHtml('pa-notes', '', { maxlength: 250 }))}
        </div>
        <p class="muted" data-accessory-hint></p>
      </div>
      <div class="field-error" data-error></div>
      <div class="modal-actions">
        <button type="button" class="btn ghost" data-cancel>Annulla</button>
        <button type="submit" class="btn primary" data-submit disabled>Continua</button>
      </div>
    </form>`;
  if (!dialogEl._open && !dialogEl.open) dialogEl.showModal();
  const submit = dialogEl.querySelector('[data-submit]');
  const campi = dialogEl.querySelector('[data-accessory-fields]');
  const hint = dialogEl.querySelector('[data-accessory-hint]');
  const kind = bindChips(dialogEl, 'kind', '');
  const risposta = bindChips(dialogEl, 'answer', '', (v) => {
    campi.hidden = v === 'yes' || v === '';
    submit.disabled = v === '';
    submit.textContent = v === 'yes' ? 'Continua: crea l\'unità collegata' : 'Salva';
    hint.textContent = v === 'no' ? 'Resta un accessorio compreso nell\'unità: non conta come unità.'
      : (v === 'unknown' ? 'Resta in sospeso con il badge «Da chiarire»: nessuna unità inventata.' : '');
  });
  dialogEl.querySelector('[data-cancel]').addEventListener('click', () => dialogEl.close());

  let saving = false;
  dialogEl.querySelector('[data-pertinenza-form]').addEventListener('submit', async (event) => {
    event.preventDefault();
    const v = risposta.get();
    if (!v || saving) return;
    if (v === 'yes') {
      dialogEl.close();
      // R2: la pertinenza nasce nella STESSA palazzina della principale
      // (building_id nel corpo, indirizzo ereditato, «Ingresso diverso?»
      // disponibile); senza palazzina resta a indirizzo proprio.
      openUnitSheet(dialogEl, { options: opzioni, building, parent: property, seed: { property_type: 'garage' }, onSaved: (unita, extra) => onSaved({ kind: 'pertinenza', unit: unita, ...extra }) });
      return;
    }
    if (!kind.get()) { setError(dialogEl, 'Indica che cos\'è (cantina, box, posto auto…).'); return; }
    setError(dialogEl, '');
    saving = true;
    setBusy(submit, true);
    const payload = { kind: kind.get(), cadastral_status: v === 'no' ? 'included' : 'unknown', client_request_id: clientRequestId };
    const mq = Number(dialogEl.querySelector('#pa-surface').value);
    if (dialogEl.querySelector('#pa-surface').value.trim() && !Number.isNaN(mq)) payload.surface_sqm = mq;
    const note = dialogEl.querySelector('#pa-notes').value.trim();
    if (note) payload.notes = note;
    let accessorio;
    try {
      accessorio = await api.createAccessory(property.id, payload);
    } catch (error) {
      saving = false;
      setBusy(submit, false, error.code === 'NETWORK' ? 'Riprova' : 'Salva');
      setError(dialogEl, errorMessage(error));
      return;
    }
    saving = false;
    clientRequestId = api.newClientRequestId();
    dialogEl.close();
    if (typeof onSaved === 'function') await onSaved({ kind: 'accessory', accessory: accessorio });
  });
}

// --- «Collega esistente» ------------------------------------------------------------------------

export function openLinkExistingSheet(dialogEl, { property, onLinked, title = 'Collega una pertinenza già censita', onPick = null } = {}) {
  dialogEl.className = 'modal census-sheet';
  dialogEl.innerHTML = `
    <div data-link-sheet>
      <h2 class="census-sheet-title">${escapeHtml(title)}</h2>
      ${fieldHtml('lk-search', 'Cerca per codice, indirizzo o comune', inputHtml('lk-search', '', { type: 'search', placeholder: 'IMM-414, via Roma…' }))}
      <div data-link-results class="census-results"></div>
      <div class="field-error" data-error></div>
      <div class="modal-actions"><button type="button" class="btn ghost" data-cancel>Chiudi</button></div>
    </div>`;
  if (!dialogEl._open && !dialogEl.open) dialogEl.showModal();
  dialogEl.querySelector('[data-cancel]').addEventListener('click', () => dialogEl.close());
  const ricerca = dialogEl.querySelector('#lk-search');
  const risultati = dialogEl.querySelector('[data-link-results]');
  let debounce = null;
  let busy = false;
  async function cerca() {
    const term = ricerca.value.trim();
    if (term.length < 2) { risultati.innerHTML = ''; return; }
    let dati;
    try { dati = await api.searchProperties(term); } catch (error) { setError(dialogEl, errorMessage(error)); return; }
    const voci = (dati && Array.isArray(dati.items) ? dati.items : []).filter((p) => p.id !== property.id);
    risultati.innerHTML = voci.length ? voci.map((p) => `<button type="button" class="census-result" data-link-id="${escapeHtml(p.id)}"><strong>${escapeHtml(p.code || `#${p.id}`)}</strong> <span class="muted">${escapeHtml([p.property_type, [p.address, p.civic_number].filter(Boolean).join(' '), p.city].filter(Boolean).join(' · '))}</span></button>`).join('')
      : '<p class="muted">Nessun immobile trovato.</p>';
    risultati.querySelectorAll('[data-link-id]').forEach((b) => b.addEventListener('click', async () => {
      if (busy) return;
      busy = true;
      setError(dialogEl, '');
      try {
        if (onPick) { await onPick(Number(b.dataset.linkId)); dialogEl.close(); return; }
        const esito = await api.linkPertinenza(property.id, b.dataset.linkId);
        dialogEl.close();
        if (onLinked) await onLinked(esito);
      } catch (error) {
        setError(dialogEl, errorMessage(error));
      } finally { busy = false; }
    }));
  }
  ricerca.addEventListener('input', () => { clearTimeout(debounce); debounce = setTimeout(cerca, 300); });
}

// --- «Chiarisci» -------------------------------------------------------------------------------

/**
 * «E' compresa» -> resta accessorio, badge via. «E' separata» -> nella stessa
 * transazione nasce la pertinenza collegata (tipo, mq e note travasati) oppure
 * si collega un immobile gia' censito; l'accessorio viene rimosso.
 */
export function openResolveSheet(dialogEl, { options: opzioni, property, accessory, onResolved } = {}) {
  const tipi = opzioni.property_types || [];
  const kinds = opzioni.accessory_kinds || [];
  let clientRequestId = api.newClientRequestId();
  let categoria = '';
  dialogEl.className = 'modal census-sheet';
  dialogEl.innerHTML = `
    <form data-resolve-form novalidate>
      <h2 class="census-sheet-title">Chiarisci: ${escapeHtml(labelOf(kinds, accessory.kind, accessory.kind))}${accessory.surface_sqm ? ` · ${escapeHtml(String(accessory.surface_sqm))} m²` : ''}</h2>
      <p class="muted">Lo leggi in visura: con un suo subalterno è un'unità separata.</p>
      <div class="form-field">${chipsHtml('outcome', [{ value: 'separate', label: 'È separata' }, { value: 'included', label: 'È compresa' }], '')}</div>
      <div data-separate-fields hidden>
        <div class="form-field">${chipsHtml('how', [{ value: 'create', label: 'Crea l\'unità collegata' }, { value: 'link', label: 'Collega un immobile già censito' }], 'create')}</div>
        <div data-create-fields>
          <div class="form-field"><label>Tipologia</label>${chipsHtml('property_type', tipi, '', { multiline: true })}<small class="muted">Vuota = dedotta dal tipo di accessorio (cantina → Cantina / Deposito, box → Garage).</small></div>
          <div class="form-field"><label>Categoria catastale</label><button type="button" class="btn census-category-btn" data-category-btn>Da verificare ›</button></div>
          <div class="form-grid-2">
            ${fieldHtml('rs-sheet', 'Foglio', inputHtml('rs-sheet', '', { maxlength: 10 }))}
            ${fieldHtml('rs-parcel', 'Particella', inputHtml('rs-parcel', '', { maxlength: 10 }))}
            ${fieldHtml('rs-subunit', 'Subalterno', inputHtml('rs-subunit', '', { maxlength: 10 }))}
            ${fieldHtml('rs-belfiore', 'Codice comune', inputHtml('rs-belfiore', '', { maxlength: 4 }))}
          </div>
          ${sectionFieldHtml('rs', null)}
        </div>
      </div>
      <div class="field-error" data-error></div>
      <div class="modal-actions">
        <button type="button" class="btn ghost" data-cancel>Annulla</button>
        <button type="submit" class="btn primary" data-submit disabled>Conferma</button>
      </div>
    </form>`;
  if (!dialogEl._open && !dialogEl.open) dialogEl.showModal();
  const submit = dialogEl.querySelector('[data-submit]');
  const separati = dialogEl.querySelector('[data-separate-fields]');
  const creazione = dialogEl.querySelector('[data-create-fields]');
  const tipo = bindChips(dialogEl, 'property_type', '');
  const come = bindChips(dialogEl, 'how', 'create', (v) => { creazione.hidden = v !== 'create'; submit.textContent = v === 'link' ? 'Scegli l\'immobile…' : 'Conferma'; });
  const esito = bindChips(dialogEl, 'outcome', '', (v) => {
    separati.hidden = v !== 'separate';
    submit.disabled = v === '';
    submit.textContent = v === 'separate' && come.get() === 'link' ? 'Scegli l\'immobile…' : 'Conferma';
  });
  const sezione = bindSectionField(dialogEl, 'rs', null);
  const bottoneCategoria = dialogEl.querySelector('[data-category-btn]');
  bottoneCategoria.addEventListener('click', () => openCategoryPicker(childDialog(dialogEl, 'categoria'), {
    options: opzioni, propertyType: tipo.get() || 'storage', current: categoria,
    onPick: (code) => { categoria = code || ''; bottoneCategoria.textContent = categoria ? categoria : 'Da verificare ›'; },
  }));
  dialogEl.querySelector('[data-cancel]').addEventListener('click', () => dialogEl.close());

  let saving = false;
  async function invia(payload) {
    saving = true;
    setBusy(submit, true);
    let risultato;
    try {
      risultato = await api.resolveAccessory(property.id, accessory.id, payload);
    } catch (error) {
      saving = false;
      setBusy(submit, false, error.code === 'NETWORK' ? 'Riprova' : 'Conferma');
      setError(dialogEl, errorMessage(error));
      return null;
    }
    saving = false;
    return risultato;
  }

  dialogEl.querySelector('[data-resolve-form]').addEventListener('submit', async (event) => {
    event.preventDefault();
    if (saving) return;
    const v = esito.get();
    if (!v) return;
    setError(dialogEl, '');
    if (v === 'included') {
      const r = await invia({ outcome: 'included' });
      if (r) { dialogEl.close(); if (onResolved) await onResolved(r); }
      return;
    }
    if (come.get() === 'link') {
      // La scelta dell'immobile avviene nel foglio di ricerca; la chiave di
      // idempotenza NON si manda (il backend la rifiuta con existing_property_id).
      dialogEl.close();
      openLinkExistingSheet(dialogEl, {
        property, title: `Collega l'immobile che era «${labelOf(kinds, accessory.kind, accessory.kind)}»`,
        onPick: async (pertinenzaId) => {
          const r = await api.resolveAccessory(property.id, accessory.id, { outcome: 'separate', existing_property_id: pertinenzaId });
          if (onResolved) await onResolved(r);
        },
      });
      return;
    }
    if (sezione.value() === undefined) { setError(dialogEl, SEZIONE_VUOTA); return; }
    const payload = { outcome: 'separate', client_request_id: clientRequestId };
    if (tipo.get()) payload.property_type = tipo.get();
    if (categoria) payload.cadastral_category = categoria;
    for (const [k, id] of [['cadastral_sheet', '#rs-sheet'], ['cadastral_parcel', '#rs-parcel'], ['cadastral_subunit', '#rs-subunit'], ['cadastral_municipality_code', '#rs-belfiore']]) {
      const val = dialogEl.querySelector(id).value.trim();
      if (val) payload[k] = val;
    }
    if (sezione.value() !== null) payload.cadastral_section = sezione.value();     // '' = nessuna, accertato (R1)
    const r = await invia(payload);
    if (r) { clientRequestId = api.newClientRequestId(); dialogEl.close(); if (onResolved) await onResolved(r); }
  });
}

// --- Accessorio: modifica / elimina -----------------------------------------------------------

export function openAccessorySheet(dialogEl, { options: opzioni, property, accessory, onSaved } = {}) {
  const kinds = opzioni.accessory_kinds || [];
  dialogEl.className = 'modal census-sheet';
  dialogEl.innerHTML = `
    <form data-accessory-form novalidate>
      <h2 class="census-sheet-title">Accessorio</h2>
      <div class="form-field"><label>Che cos'è?</label>${chipsHtml('kind', kinds, str(accessory.kind), { multiline: true })}</div>
      <div class="form-grid-2">
        ${fieldHtml('ae-surface', 'mq', inputHtml('ae-surface', accessory.surface_sqm, { type: 'number', inputmode: 'decimal', min: 0, step: 'any' }))}
        ${fieldHtml('ae-notes', 'Note', inputHtml('ae-notes', accessory.notes, { maxlength: 250 }))}
      </div>
      <p class="muted">${accessory.cadastral_status === 'unknown' ? 'Da chiarire: usa «Chiarisci» per dire se è separata o compresa.' : 'Accessorio compreso nell\'unità.'}</p>
      <div class="field-error" data-error></div>
      <div class="modal-actions">
        <button type="button" class="btn ghost" data-cancel>Annulla</button>
        <button type="button" class="btn danger" data-delete>Elimina</button>
        <button type="submit" class="btn primary" data-submit>Salva</button>
      </div>
    </form>`;
  if (!dialogEl._open && !dialogEl.open) dialogEl.showModal();
  const kind = bindChips(dialogEl, 'kind', str(accessory.kind));
  dialogEl.querySelector('[data-cancel]').addEventListener('click', () => dialogEl.close());
  let busy = false;
  dialogEl.querySelector('[data-delete]').addEventListener('click', async () => {
    const b = dialogEl.querySelector('[data-delete]');
    if (b.dataset.confirm !== '1') { b.dataset.confirm = '1'; b.textContent = 'Confermi l\'eliminazione?'; return; }
    if (busy) return;
    busy = true;
    try { await api.deleteAccessory(property.id, accessory.id); dialogEl.close(); if (onSaved) await onSaved(null); } catch (error) { setError(dialogEl, errorMessage(error)); } finally { busy = false; }
  });
  dialogEl.querySelector('[data-accessory-form]').addEventListener('submit', async (event) => {
    event.preventDefault();
    if (busy) return;
    const payload = {};
    if (kind.get() && kind.get() !== accessory.kind) payload.kind = kind.get();
    const mq = dialogEl.querySelector('#ae-surface').value.trim();
    if (mq !== str(accessory.surface_sqm)) payload.surface_sqm = mq === '' ? null : Number(mq);
    const note = dialogEl.querySelector('#ae-notes').value.trim();
    if (note !== str(accessory.notes)) payload.notes = note || null;
    if (!Object.keys(payload).length) { dialogEl.close(); return; }
    busy = true;
    setBusy(dialogEl.querySelector('[data-submit]'), true);
    try {
      const esito = await api.updateAccessory(property.id, accessory.id, payload);
      dialogEl.close();
      if (onSaved) await onSaved(esito);
    } catch (error) {
      setBusy(dialogEl.querySelector('[data-submit]'), false, 'Salva');
      setError(dialogEl, errorMessage(error));
    } finally { busy = false; }
  });
}

// --- Presa in carico -------------------------------------------------------------------------

export function openTakeInChargeDialog(dialogEl, { property, pertinenze = [], onDone } = {}) {
  const n = pertinenze.length;
  dialogEl.className = 'modal census-sheet';
  dialogEl.innerHTML = `
    <form data-take-form novalidate>
      <h2 class="census-sheet-title">Porti ${escapeHtml(property.code || `#${property.id}`)} nel lavoro commerciale?</h2>
      <p class="muted">Stessa scheda, stesso codice: da qui valgono i flussi commerciali (proprietario, acquisizione, incarico).</p>
      ${n ? `<div class="form-field"><label class="census-check"><input type="checkbox" data-include-pertinenze checked> Includi ${n === 1 ? 'la pertinenza collegata' : `le ${n} pertinenze collegate`} (${escapeHtml(pertinenze.map((p) => p.code || `#${p.id}`).join(', '))})</label></div>` : ''}
      <div class="field-error" data-error></div>
      <div class="modal-actions">
        <button type="button" class="btn ghost" data-cancel>Annulla</button>
        <button type="submit" class="btn primary" data-submit>Prendi in carico</button>
      </div>
    </form>`;
  if (!dialogEl._open && !dialogEl.open) dialogEl.showModal();
  dialogEl.querySelector('[data-cancel]').addEventListener('click', () => dialogEl.close());
  let busy = false;
  dialogEl.querySelector('[data-take-form]').addEventListener('submit', async (event) => {
    event.preventDefault();
    if (busy) return;
    busy = true;
    const spunta = dialogEl.querySelector('[data-include-pertinenze]');
    const include = spunta ? !!spunta.checked : false;
    setBusy(dialogEl.querySelector('[data-submit]'), true);
    try {
      const esito = await api.takeInCharge(property.id, include);
      dialogEl.close();
      if (onDone) await onDone(esito);
    } catch (error) {
      setBusy(dialogEl.querySelector('[data-submit]'), false, 'Prendi in carico');
      setError(dialogEl, errorMessage(error));
    } finally { busy = false; }
  });
}
