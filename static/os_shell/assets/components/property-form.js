// STIMA360 OS — components/property-form.js (CRM-OPS-2)
//
// UN form per creare e modificare un immobile, usato da immobili.js
// ("+ Nuovo immobile") e da immobile-dettaglio.js ("Modifica immobile").
//
// Nessun catalogo vive qui. Territorio, classi energetiche, tipologie e agenti
// arrivano da GET /api/property/form-options (property/service.py::
// form_options), che e' anche la fonte con cui il backend valida cio' che
// riceve: una sola copia, nessuna lista scritta a mano nel browser.
//
//  * Regione -> Provincia -> Comune -> Microzona: ogni livello si filtra sul
//    precedente; cambiando un livello si azzerano SOLO i livelli sotto
//    diventati incompatibili. Indirizzo e civico restano liberi.
//  * Titolo e codice non si chiedono: li genera il backend.
//  * "Assegnato a": il menu c'e' solo se il server dice `can_assign` e
//    contiene solo gli agenti che il server ha restituito; si salva l'id.
//  * Valori storici (un comune fuori catalogo, una provincia scritta per
//    esteso, una classe "g"): si vedono come "(valore storico)", restano
//    selezionati e NON si inviano finche' l'operatore non li cambia.
//
// In modifica si invia solo cio' che e' cambiato (PropertyUpdate usa
// exclude_unset): un immobile si salva quante volte si vuole senza toccare i
// campi non modificati, i collegamenti, i documenti o le foto.

import { apiGet, apiPatch, apiPost } from '../core/api-client.js';
import { escapeHtml } from './st-table.js';

const STATUS_LABELS = {
  draft: 'Bozza', evaluation: 'In valutazione', mandate: 'Mandato', active: 'Attivo',
  reserved: 'Riservato', under_offer: 'Sotto offerta', sold: 'Venduto',
  withdrawn: 'Ritirato', archived: 'Archiviato',
};
// Stati offerti alla CREAZIONE (property/enums.py::PROPERTY_STATUSES meno
// 'sold' e 'archived', che si raggiungono solo con i loro flussi). In
// modifica lo stato resta nella sua sezione della scheda, con le sue conferme.
// CRM-OPS-3: niente 'mandate' - un incarico nasce solo da un'acquisizione
// (POST /api/acquisitions/{id}/mandate), mai creando l'immobile.
const CREATE_STATUSES = ['draft', 'evaluation', 'active', 'reserved', 'under_offer', 'withdrawn'];
const PROPERTY_CLASSES = ['A', 'B', 'C'];
const ROLE_LABELS = { agency_owner: 'Titolare', agency_admin: 'Amministratore', agent: 'Agente' };
const HISTORICAL = ' (valore storico)';

/** Come si riconosce un immobile senza il titolo manuale: indirizzo, comune
 *  (microzona). Il titolo, ora generato, resta solo come ultima risorsa. */
export function propertyDisplayName(p) {
  if (!p) return '';
  const street = [p.address, p.civic_number].filter((x) => x && String(x).trim()).join(' ');
  const place = p.city ? (p.microzone ? `${p.city} (${p.microzone})` : p.city) : '';
  const label = [street, place].filter(Boolean).join(', ');
  return label || p.title || p.code || `Immobile #${p.id}`;
}

let optionsPromise = null;
/** Le opzioni del form, una richiesta per pagina (si ricarica dopo un errore). */
export function loadFormOptions(get = apiGet) {
  if (!optionsPromise) {
    optionsPromise = get('/api/property/form-options').catch((error) => {
      optionsPromise = null;
      throw error;
    });
  }
  return optionsPromise;
}

// --- Territorio: funzioni pure ------------------------------------------------

export function provincesOf(tree, region) {
  const r = (tree || []).find((x) => x.name === region);
  return r ? r.provinces : [];
}

export function municipalitiesOf(tree, region, province) {
  const p = provincesOf(tree, region).find((x) => x.code === province);
  return p ? p.municipalities : [];
}

export function microzonesOf(tree, region, province, city) {
  const m = municipalitiesOf(tree, region, province).find((x) => x.name === city);
  return m ? m.microzones : [];
}

const LEVELS = ['region', 'province', 'city', 'microzone'];

/** Dopo il cambio di `changed`: i livelli SOTTO restano solo se ancora
 *  compatibili con quelli sopra, altrimenti si svuotano. I livelli sopra non
 *  si toccano mai, e nessuna scelta viene inventata. */
export function cascadeLocation(tree, loc, changed) {
  const out = { ...loc };
  const from = LEVELS.indexOf(changed);
  const valid = {
    province: () => provincesOf(tree, out.region).some((x) => x.code === out.province),
    city: () => municipalitiesOf(tree, out.region, out.province).some((m) => m.name === out.city),
    microzone: () => microzonesOf(tree, out.region, out.province, out.city).includes(out.microzone),
  };
  for (const level of LEVELS.slice(from + 1)) {
    if (out[level] && !valid[level]()) out[level] = '';
  }
  return out;
}

function optionsHtml(values, selected, { placeholder, historical } = {}) {
  const list = [`<option value="">${escapeHtml(placeholder || '—')}</option>`];
  const known = values.map((v) => (typeof v === 'string' ? { value: v, label: v } : v));
  if (historical && selected && !known.some((v) => v.value === selected)) {
    list.push(`<option value="${escapeHtml(selected)}" selected>${escapeHtml(selected + HISTORICAL)}</option>`);
  }
  for (const v of known) {
    list.push(`<option value="${escapeHtml(v.value)}"${v.value === selected ? ' selected' : ''}>${escapeHtml(v.label)}</option>`);
  }
  return list.join('');
}

const str = (v) => (v === null || v === undefined ? '' : String(v));

// --- Il dialog -----------------------------------------------------------------

/**
 * @param {HTMLDialogElement} dialogEl
 * @param {{mode?: 'create'|'edit', property?: object, onSaved?: Function, seed?: object, onBack?: Function}} opts
 *   seed   -> CREAZIONE-GUIDATA-1: in creazione, i dati gia' scelti nel primo
 *             passaggio (territorio, via, civico); restano modificabili
 *   onBack -> «← Indietro» torna al passaggio precedente senza salvare
 */
export async function openPropertyDialog(dialogEl, { mode = 'create', property = null, onSaved, seed = null, onBack = null } = {}) {
  const isEdit = mode === 'edit' && property;
  const p = isEdit ? property : (seed || {});
  dialogEl.classList.add('property-form-dialog');
  dialogEl.innerHTML = '<p class="muted">Caricamento…</p>';
  if (!dialogEl.open) dialogEl.showModal();

  let options;
  try {
    options = await loadFormOptions();
  } catch (error) {
    dialogEl.innerHTML = `
      <div class="error-box">Impossibile caricare i dati del form: ${escapeHtml(error.message)}</div>
      <div class="modal-actions"><button type="button" class="btn ghost" data-close>Chiudi</button></div>`;
    dialogEl.querySelector('[data-close]').addEventListener('click', () => dialogEl.close());
    return;
  }

  const tree = options.territory || [];
  const types = options.property_types || [];
  const energy = options.energy_classes || [];
  const agents = Array.isArray(options.agents) ? options.agents : [];
  // CATALOGO-CANONICO-1: stato, posizione e distanza dal mare dal catalogo
  // canonico (property/site_catalog.py, via form-options).
  const conditions = options.conditions || [];
  const seaPositions = options.sea_positions || [];
  const seaDistances = options.sea_distances || [];
  const YES_NO = [{ value: 'true', label: 'Sì' }, { value: 'false', label: 'No' }];
  const canAssign = options.can_assign === true;

  // Stato del territorio: parte dai valori salvati, anche se storici.
  const initialLoc = { region: str(p.region), province: str(p.province), city: str(p.city), microzone: str(p.microzone) };
  let loc = { ...initialLoc };
  const initialAgent = str(p.assigned_agent_id);

  const agentOptions = agents.map((a) => ({
    value: String(a.id),
    label: `${a.name || `Operatore #${a.id}`}${ROLE_LABELS[a.role] ? ` · ${ROLE_LABELS[a.role]}` : ''}${a.is_me ? ' (tu)' : ''}`,
  }));

  function agentFieldHtml() {
    if (!canAssign) {
      return `<div class="form-field"><label>Assegnato a</label>
        <div class="input" data-agent-readonly aria-readonly="true">${escapeHtml(p.assigned_to || 'Nessuno')}</div>
        <small class="muted">Solo titolare o amministratore possono assegnare l'immobile.</small></div>`;
    }
    const opts = [`<option value=""${initialAgent === '' ? ' selected' : ''}>Nessuno</option>`];
    if (initialAgent && !agentOptions.some((a) => a.value === initialAgent)) {
      opts.push(`<option value="${escapeHtml(initialAgent)}" selected disabled>${escapeHtml(`${p.assigned_to || `Operatore #${initialAgent}`} (non attivo)`)}</option>`);
    }
    for (const a of agentOptions) opts.push(`<option value="${escapeHtml(a.value)}"${a.value === initialAgent ? ' selected' : ''}>${escapeHtml(a.label)}</option>`);
    const legacy = !initialAgent && p.assigned_to
      ? `<small class="muted">Indicazione precedente (testo libero): ${escapeHtml(p.assigned_to)}</small>` : '';
    const empty = agentOptions.length ? '' : '<small class="muted">Nessun agente attivo nell\'agenzia.</small>';
    return `<div class="form-field"><label for="pf-agent">Assegnato a</label>
      <select id="pf-agent" class="input">${opts.join('')}</select>${legacy}${empty}</div>`;
  }

  dialogEl.innerHTML = `
    <form id="property-form" novalidate>
      <h2 style="margin-top:0">${isEdit ? 'Modifica immobile' : 'Nuovo immobile'}</h2>
      ${isEdit ? `<p class="muted">Codice ${escapeHtml(p.code || 'assegnato al salvataggio')} · Immobile #${escapeHtml(p.id)}</p>` : '<p class="muted">Il codice viene assegnato automaticamente al salvataggio.</p>'}

      <div class="form-grid-2">
        <div class="form-field"><label for="pf-type">Tipologia</label>
          <select id="pf-type" class="input">${optionsHtml(types, str(p.property_type) || 'apartment', { placeholder: '—', historical: true }).replace('<option value="">—</option>', '')}</select></div>
        ${isEdit ? '' : `<div class="form-field"><label for="pf-status">Stato commerciale</label>
          <select id="pf-status" class="input">${CREATE_STATUSES.map((s) => `<option value="${s}"${s === 'draft' ? ' selected' : ''}>${escapeHtml(STATUS_LABELS[s])}</option>`).join('')}</select></div>`}
        <div class="form-field"><label for="pf-class">Classe</label>
          <select id="pf-class" class="input">${optionsHtml(PROPERTY_CLASSES, str(p.classification), { placeholder: '—', historical: true })}</select></div>
      </div>

      <h3 class="section-title">Ubicazione</h3>
      <div class="form-grid-2">
        <div class="form-field"><label for="pf-region">Regione</label><select id="pf-region" class="input"></select></div>
        <div class="form-field"><label for="pf-province">Provincia</label><select id="pf-province" class="input"></select></div>
        <div class="form-field"><label for="pf-city">Comune</label><select id="pf-city" class="input"></select></div>
        <div class="form-field"><label for="pf-microzone">Microzona</label><select id="pf-microzone" class="input"></select></div>
      </div>
      <div class="form-grid-2">
        <div class="form-field"><label for="pf-address">Indirizzo</label><input type="text" id="pf-address" class="input" maxlength="250" value="${escapeHtml(str(p.address))}"></div>
        <div class="form-field"><label for="pf-civic">Civico</label><input type="text" id="pf-civic" class="input" maxlength="30" value="${escapeHtml(str(p.civic_number))}"></div>
      </div>

      <h3 class="section-title">Caratteristiche</h3>
      <div class="form-grid-3">
        <div class="form-field"><label for="pf-rooms">Locali</label><input type="number" id="pf-rooms" class="input" min="0" step="1" value="${escapeHtml(str(p.rooms))}"></div>
        <div class="form-field"><label for="pf-bedrooms">Camere</label><input type="number" id="pf-bedrooms" class="input" min="0" step="1" value="${escapeHtml(str(p.bedrooms))}"></div>
        <div class="form-field"><label for="pf-bathrooms">Bagni</label><input type="number" id="pf-bathrooms" class="input" min="0" step="1" value="${escapeHtml(str(p.bathrooms))}"></div>
      </div>
      <div class="form-grid-2">
        <div class="form-field"><label for="pf-surface">Superficie (mq)</label><input type="number" id="pf-surface" class="input" min="0" step="any" value="${escapeHtml(str(p.surface_sqm))}"></div>
        <div class="form-field"><label for="pf-commercial-surface">Superficie commerciale (mq)</label><input type="number" id="pf-commercial-surface" class="input" min="0" step="any" value="${escapeHtml(str(p.commercial_surface_sqm))}"></div>
        <div class="form-field"><label for="pf-floor">Piano</label><input type="text" id="pf-floor" class="input" maxlength="50" placeholder="T, R, S, 1, 2… o Ultimo" value="${escapeHtml(str(p.floor))}"></div>
        <div class="form-field"><label for="pf-year">Anno di costruzione</label><input type="number" id="pf-year" class="input" min="1000" max="2200" step="1" value="${escapeHtml(str(p.year_built))}"></div>
        <div class="form-field"><label for="pf-condition">Stato</label>
          <select id="pf-condition" class="input">${optionsHtml(conditions, str(p.condition), { placeholder: 'Non indicato', historical: true })}</select></div>
        <div class="form-field"><label for="pf-energy">Classe energetica</label>
          <select id="pf-energy" class="input">${optionsHtml(energy, str(p.energy_class), { placeholder: 'Non indicata', historical: true })}</select></div>
        <div class="form-field"><label for="pf-elevator">Ascensore</label>
          <select id="pf-elevator" class="input">${optionsHtml(YES_NO, str(p.elevator), { placeholder: 'Non indicato' })}</select></div>
      </div>

      <h3 class="section-title">Mare</h3>
      <div class="form-grid-2">
        <div class="form-field"><label for="pf-sea-position">Posizione</label>
          <select id="pf-sea-position" class="input">${optionsHtml(seaPositions, str(p.sea_position), { placeholder: 'Non indicata', historical: true })}</select></div>
        <div class="form-field"><label for="pf-sea-distance">Distanza dal mare</label>
          <select id="pf-sea-distance" class="input">${optionsHtml(seaDistances, str(p.sea_distance), { placeholder: 'Non indicata', historical: true })}</select></div>
        <div class="form-field"><label for="pf-sea-barrier">Ferrovia o strada verso il mare</label>
          <select id="pf-sea-barrier" class="input">${optionsHtml(YES_NO, str(p.sea_barrier), { placeholder: 'Non indicato' })}</select></div>
        <div class="form-field"><label for="pf-sea-view">Vista mare</label>
          <select id="pf-sea-view" class="input">${optionsHtml(YES_NO, str(p.sea_view), { placeholder: 'Non indicato' })}</select></div>
        <div class="form-field"><label for="pf-sea-view-detail">Dettaglio vista mare</label><input type="text" id="pf-sea-view-detail" class="input" maxlength="200" value="${escapeHtml(str(p.sea_view_detail))}"></div>
        <div class="form-field"><label for="pf-sea-band">Fascia mare (dal sito)</label><input type="text" id="pf-sea-band" class="input" maxlength="32" value="${escapeHtml(str(p.sea_band))}"></div>
      </div>

      <h3 class="section-title">Impianti e dotazioni</h3>
      <div class="form-grid-2">
        <div class="form-field"><label for="pf-heating">Riscaldamento</label><input type="text" id="pf-heating" class="input" maxlength="120" value="${escapeHtml(str(p.heating))}"></div>
        <div class="form-field"><label for="pf-air">Climatizzazione</label><input type="text" id="pf-air" class="input" maxlength="120" value="${escapeHtml(str(p.air_conditioning))}"></div>
        <div class="form-field"><label for="pf-air-type">Tipo di climatizzazione</label><input type="text" id="pf-air-type" class="input" maxlength="120" value="${escapeHtml(str(p.air_conditioning_type))}"></div>
        <div class="form-field"><label for="pf-exposure">Esposizione</label><input type="text" id="pf-exposure" class="input" maxlength="120" value="${escapeHtml(str(p.exposure))}"></div>
        <div class="form-field"><label for="pf-furnishing">Arredamento</label><input type="text" id="pf-furnishing" class="input" maxlength="120" value="${escapeHtml(str(p.furnishing))}"></div>
        <div class="form-field"><label for="pf-condo-fees">Spese condominiali (€)</label><input type="number" id="pf-condo-fees" class="input" min="0" step="any" value="${escapeHtml(str(p.condo_fees))}"></div>
      </div>
      <div class="form-field"><label for="pf-other-features">Altre caratteristiche</label><textarea id="pf-other-features" class="input" rows="2">${escapeHtml(str(p.other_features))}</textarea></div>

      <h3 class="section-title">Commerciale</h3>
      <div class="form-grid-2">
        <div class="form-field"><label for="pf-price">Prezzo richiesto (€)</label><input type="number" id="pf-price" class="input" min="0" step="any" value="${escapeHtml(str(p.asking_price))}"></div>
      </div>
      ${agentFieldHtml()}

      <div class="form-field"><label for="pf-notes">Note interne</label><textarea id="pf-notes" class="input" rows="3">${escapeHtml(str(p.internal_notes))}</textarea></div>

      <div id="property-form-error" class="field-error"></div>
      <div class="modal-actions">
        ${!isEdit && onBack ? '<button type="button" id="property-form-back" class="btn ghost">← Indietro</button>' : ''}
        <button type="button" id="property-form-cancel" class="btn ghost">Annulla</button>
        <button type="submit" id="property-form-submit" class="btn primary">${isEdit ? 'Salva modifiche' : 'Crea immobile'}</button>
      </div>
    </form>`;

  const $ = (sel) => dialogEl.querySelector(sel);
  const form = $('#property-form');
  const submitBtn = $('#property-form-submit');
  const errorEl = $('#property-form-error');
  const sel = { region: $('#pf-region'), province: $('#pf-province'), city: $('#pf-city'), microzone: $('#pf-microzone') };

  // Un valore storico si mostra solo finche' coincide con quello salvato.
  const historicalOf = (level) => (loc[level] && loc[level] === initialLoc[level]);

  function renderLocation() {
    sel.region.innerHTML = optionsHtml(tree.map((r) => r.name), loc.region, { placeholder: 'Seleziona la regione', historical: historicalOf('region') });
    const provinces = provincesOf(tree, loc.region).map((x) => ({ value: x.code, label: `${x.name} (${x.code})` }));
    sel.province.innerHTML = optionsHtml(provinces, loc.province, { placeholder: loc.region ? 'Seleziona la provincia' : 'Seleziona prima la regione', historical: historicalOf('province') });
    sel.province.disabled = !loc.region && !loc.province;
    const cities = municipalitiesOf(tree, loc.region, loc.province).map((m) => m.name);
    sel.city.innerHTML = optionsHtml(cities, loc.city, { placeholder: loc.province ? 'Seleziona il comune' : 'Seleziona prima la provincia', historical: historicalOf('city') });
    sel.city.disabled = !loc.province && !loc.city;
    const zones = microzonesOf(tree, loc.region, loc.province, loc.city);
    sel.microzone.innerHTML = optionsHtml(zones, loc.microzone, { placeholder: loc.city ? 'Seleziona la microzona' : 'Seleziona prima il comune', historical: historicalOf('microzone') });
    sel.microzone.disabled = !loc.city && !loc.microzone;
    // Il valore segue lo stato, non l'ultima scelta fatta sul nodo: dopo una
    // cascata il livello azzerato mostra davvero il segnaposto.
    for (const level of LEVELS) sel[level].value = loc[level];
  }

  for (const level of LEVELS) {
    sel[level].addEventListener('change', () => {
      loc = cascadeLocation(tree, { ...loc, [level]: sel[level].value }, level);
      renderLocation();
    });
  }
  renderLocation();

  $('#property-form-cancel').addEventListener('click', () => dialogEl.close());
  if ($('#property-form-back')) $('#property-form-back').addEventListener('click', () => { if (!saving) { dialogEl.close(); onBack(); } });

  const fieldValue = (id) => ($(id) ? $(id).value : '');
  const textOrNull = (v) => (String(v ?? '').trim() === '' ? null : String(v).trim());
  const numberOrNull = (v) => { if (v === '' || v === null || v === undefined) return null; const n = Number(v); return Number.isNaN(n) ? null : n; };
  const intOrNull = (v) => { if (v === '' || v === null || v === undefined) return null; const n = Number.parseInt(v, 10); return Number.isNaN(n) ? null : n; };

  // Ogni campo: [chiave, selettore, conversione, valore iniziale come stringa].
  const FIELDS = [
    ['property_type', '#pf-type', textOrNull],
    ['classification', '#pf-class', textOrNull],
    ['address', '#pf-address', textOrNull],
    ['civic_number', '#pf-civic', textOrNull],
    ['rooms', '#pf-rooms', intOrNull],
    ['bedrooms', '#pf-bedrooms', intOrNull],
    ['bathrooms', '#pf-bathrooms', intOrNull],
    ['surface_sqm', '#pf-surface', numberOrNull],
    ['commercial_surface_sqm', '#pf-commercial-surface', numberOrNull],
    ['floor', '#pf-floor', textOrNull],
    ['year_built', '#pf-year', intOrNull],
    ['condition', '#pf-condition', textOrNull],
    ['energy_class', '#pf-energy', textOrNull],
    // CATALOGO-CANONICO-1 (migration 087): in creazione viaggiano solo se compilati
    ['sea_position', '#pf-sea-position', textOrNull],
    ['sea_distance', '#pf-sea-distance', textOrNull],
    ['sea_view_detail', '#pf-sea-view-detail', textOrNull],
    ['sea_band', '#pf-sea-band', textOrNull],
    ['heating', '#pf-heating', textOrNull],
    ['air_conditioning', '#pf-air', textOrNull],
    ['air_conditioning_type', '#pf-air-type', textOrNull],
    ['exposure', '#pf-exposure', textOrNull],
    ['furnishing', '#pf-furnishing', textOrNull],
    ['condo_fees', '#pf-condo-fees', numberOrNull],
    ['other_features', '#pf-other-features', textOrNull],
    ['asking_price', '#pf-price', numberOrNull],
    ['internal_notes', '#pf-notes', textOrNull],
  ];
  // Valori iniziali COME LI MOSTRA IL FORM, per confrontare senza falsi cambi.
  const initialField = Object.fromEntries(FIELDS.map(([key, id]) => [key, fieldValue(id)]));
  // Si' / No / Non indicato: tre stati, mai "no" per "non so".
  const BOOL_FIELDS = [['elevator', '#pf-elevator'], ['sea_barrier', '#pf-sea-barrier'], ['sea_view', '#pf-sea-view']];
  const initialBool = Object.fromEntries(BOOL_FIELDS.map(([key, id]) => [key, fieldValue(id)]));

  function buildPayload() {
    const payload = {};
    for (const [key, id, convert] of FIELDS) {
      const raw = fieldValue(id);
      if (isEdit) {
        if (raw !== initialField[key]) payload[key] = convert(raw);
      } else if (convert(raw) !== null) {
        payload[key] = convert(raw);
      }
    }
    for (const [key, id] of BOOL_FIELDS) {
      const value = fieldValue(id);
      if (isEdit ? value !== initialBool[key] : value !== '') payload[key] = value === '' ? null : value === 'true';
    }

    const locChanged = LEVELS.some((k) => loc[k] !== initialLoc[k]);
    if (isEdit ? locChanged : Object.values(loc).some(Boolean)) {
      // Il territorio viaggia sempre intero: e' una sola scelta a cascata.
      for (const k of LEVELS) payload[k] = loc[k] || null;
    }

    if (!isEdit) {
      payload.commercial_status = fieldValue('#pf-status') || 'draft';
      if (!payload.property_type) payload.property_type = 'apartment';
    }
    if (canAssign && $('#pf-agent')) {
      const agent = fieldValue('#pf-agent');
      if (isEdit ? agent !== initialAgent : agent !== '') payload.assigned_agent_id = agent === '' ? null : Number(agent);
    }
    return payload;
  }

  let saving = false;
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (saving) return;
    errorEl.textContent = '';
    const payload = buildPayload();
    if (isEdit && !Object.keys(payload).length) { dialogEl.close(); return; }

    saving = true;
    submitBtn.disabled = true;
    submitBtn.textContent = 'Salvataggio…';
    let saved;
    try {
      saved = isEdit
        ? await apiPatch(`/api/property/properties/${p.id}`, payload)
        : await apiPost('/api/property/properties', payload);
    } catch (error) {
      saving = false;
      submitBtn.disabled = false;
      submitBtn.textContent = isEdit ? 'Salva modifiche' : 'Crea immobile';
      errorEl.textContent = error.status === 422
        ? 'Alcuni valori non sono validi: controlla territorio, classe energetica e anno.'
        : (error.message || 'Errore nel salvataggio dell’immobile.');
      return;
    }
    dialogEl.close();
    if (typeof onSaved === 'function') await onSaved(saved);
  });
}
