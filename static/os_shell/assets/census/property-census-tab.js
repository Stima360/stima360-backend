// STIMA360 OS — census/property-census-tab.js (CENSIMENTO-1 Fase 4)
//
// La tab «Censimento» (o «Pertinenze», su una scheda gia' commerciale) della
// scheda Immobile: collocazione (palazzina, unita' principale, indirizzo
// ereditato/proprio), dati catastali (PATCH generica, categoria dal foglio di
// scelta), pertinenze collegate (+ Aggiungi «Si'/No/Non lo so», Collega
// esistente, Scollega), accessori («Chiarisci», modifica/elimina) e - solo in
// censimento - «Prendi in carico» e «Annulla creazione».
//
// Contratti (property/router.py, Fase 3): GET .../census, POST .../pertinenze/link,
// POST .../pertinenze/{pid}/unlink, POST/PATCH/DELETE .../accessories[/{aid}],
// POST .../accessories/{aid}/resolve, POST .../take-in-charge, POST .../undo-create;
// i dati catastali con la PATCH generica /api/property/properties/{id}.
// Vive in un file proprio perche' la scheda Immobile (immobile-dettaglio.js)
// e' gia' lunga e i suoi test la caricano per intero.

import { apiGet } from '../core/api-client.js';
import { escapeHtml, renderBadge } from '../components/st-table.js';
import { loadFormOptions } from '../components/property-form.js';
import * as censusApi from './census-api.js';
import {
  bindSectionField, openAccessorySheet, openCategoryPicker, openLinkExistingSheet, openPertinenzaSheet, openResolveSheet,
  openTakeInChargeDialog, sectionFieldHtml, showToast,
} from './census-sheets.js';
import { errorMessage as censusErrorMessage, labelOf, sectionLabel } from './census-model.js';

/**
 * @param {HTMLElement} el   il contenitore della tab
 * @param {{property: object, container: HTMLElement, dialogEl: HTMLDialogElement,
 *          linkConfirm: Set, isCensus: boolean, showTab: Function, navigate: Function}} ctx
 */
export async function renderPropertyCensusTab(el, ctx) {
  const { property, container, dialogEl, linkConfirm, showTab, navigate } = ctx;
  const isCensus = () => ctx.isCensus;
  let censimento;
  let opzioni;
  try {
    [censimento, opzioni] = await Promise.all([censusApi.getCensus(property.id), loadFormOptions()]);
  } catch (error) {
    el.innerHTML = `<div class="error-box">${escapeHtml(censusErrorMessage(error))}</div>`;
    return;
  }
    const tipi = opzioni.property_types || [];
  const kinds = opzioni.accessory_kinds || [];
  const ricarica = async () => {
    try {
      const aggiornato = await apiGet(`/api/property/properties/${property.id}`);
      Object.assign(property, aggiornato);
    } catch (_error) { /* la tab si ridisegna comunque dalla lettura del censimento */ }
    await showTab('censimento');
  };
  const dopoPresa = async (esito) => {
    Object.assign(property, esito);
    showToast(container, { text: `${property.code || 'Immobile'} preso in carico${esito.pertinenze_taken && esito.pertinenze_taken.length ? ` con ${esito.pertinenze_taken.length} pertinenze` : ''}.` });
    navigate('immobili', [property.id]);
  };
  const catasto = [
    ['Categoria catastale', property.cadastral_category || 'Da verificare'],
    ['Comune catastale', property.cadastral_municipality_code], ['Sezione', sectionLabel(property.cadastral_section)],
    ['Foglio', property.cadastral_sheet], ['Particella', property.cadastral_parcel], ['Subalterno', property.cadastral_subunit],
  ];
  const edificio = censimento.building;
  const genitore = censimento.parent;
  el.innerHTML = `
    <div class="census-tab">
      ${isCensus() ? `<div class="census-banner" data-census-state><strong>Scheda in censimento</strong> — senza proprietario, incarico o stato commerciale. Per lavorarla commercialmente usa «Prendi in carico» (stessa scheda, stesso codice).
        <div class="action-bar"><button type="button" class="btn primary" id="census-take">Prendi in carico</button><button type="button" class="btn ghost" id="census-undo">Annulla creazione</button></div></div>` : ''}
      <h3 class="section-title">Collocazione</h3>
      <div class="detail-grid">
        <div class="detail-item"><label>Palazzina</label>${edificio ? `<a href="#/edifici/${escapeHtml(edificio.id)}" id="census-open-building">${escapeHtml(edificio.name || [edificio.address, edificio.civic_number].filter(Boolean).join(' ') || `#${edificio.id}`)}</a>` : '—'}</div>
        <div class="detail-item"><label>Indirizzo</label>${escapeHtml(censimento.address_inherited ? 'Ereditato dalla palazzina' : 'Proprio')}</div>
        <div class="detail-item"><label>Unità principale</label>${genitore ? `<a href="#/immobili/${escapeHtml(genitore.id)}" id="census-open-parent">${escapeHtml(genitore.code || `#${genitore.id}`)}</a>${genitore.archived_at ? ' <small class="muted">(archiviata)</small>' : ''}` : '—'}</div>
        <div class="detail-item"><label>Piano · scala · interno</label>${escapeHtml([property.floor, property.staircase ? `scala ${property.staircase}` : null, property.internal_number ? `int. ${property.internal_number}` : null].filter(Boolean).join(' · ') || '—')}</div>
      </div>
      <h3 class="section-title">Dati catastali</h3>
      <div class="detail-grid">${catasto.map(([l, v]) => `<div class="detail-item"><label>${escapeHtml(l)}</label>${escapeHtml(v === null || v === undefined || v === '' ? '—' : v)}</div>`).join('')}</div>
      <div class="action-bar"><button type="button" class="btn ghost" id="census-edit-cadastral">Modifica dati catastali</button></div>
      <div id="census-cadastral-form" hidden></div>
      <h3 class="section-title">Pertinenze collegate</h3>
      ${censimento.pertinenze.length ? `<ul class="census-list">${censimento.pertinenze.map((x) => `<li class="census-list-item" data-pertinenza-id="${escapeHtml(x.id)}"><a href="#/immobili/${escapeHtml(x.id)}"><strong>${escapeHtml(x.code || `#${x.id}`)}</strong></a> <span class="muted">${escapeHtml([labelOf(tipi, x.property_type, x.property_type), x.surface_sqm ? `${x.surface_sqm} m²` : null, x.cadastral_category || 'Da verificare'].filter(Boolean).join(' · '))}</span> <button type="button" class="btn ghost btn-small${linkConfirm.has(x.id) ? ' danger' : ''}" data-unlink="${escapeHtml(x.id)}">${linkConfirm.has(x.id) ? 'Confermi lo scollegamento?' : 'Scollega'}</button></li>`).join('')}</ul>` : '<p class="muted">Nessuna pertinenza collegata.</p>'}
      <div class="action-bar">
        <button type="button" class="btn primary" id="census-add-pertinenza">+ Aggiungi pertinenza</button>
        <button type="button" class="btn ghost" id="census-link-existing">Collega esistente</button>
      </div>
      <h3 class="section-title">Accessori${censimento.accessories_unknown ? ` · ${escapeHtml(String(censimento.accessories_unknown))} da chiarire` : ''}</h3>
      ${censimento.accessories.length ? `<ul class="census-list">${censimento.accessories.map((a) => `<li class="census-list-item" data-accessory-id="${escapeHtml(a.id)}"><strong>${escapeHtml(labelOf(kinds, a.kind, a.kind))}</strong> <span class="muted">${escapeHtml([a.surface_sqm ? `${a.surface_sqm} m²` : null, a.notes].filter(Boolean).join(' · '))}</span> ${a.cadastral_status === 'unknown' ? renderBadge('Da chiarire', 'warn') : renderBadge('Compreso', 'gray')} ${a.cadastral_status === 'unknown' ? `<button type="button" class="btn btn-small" data-resolve="${escapeHtml(a.id)}">Chiarisci</button>` : ''}<button type="button" class="btn ghost btn-small" data-edit-accessory="${escapeHtml(a.id)}">Modifica</button></li>`).join('')}</ul>` : '<p class="muted">Nessun accessorio. Gli accessori compresi non contano mai come unità.</p>'}
    </div>`;

  const take = el.querySelector('#census-take');
  if (take) take.addEventListener('click', () => openTakeInChargeDialog(dialogEl, { property, pertinenze: censimento.pertinenze, onDone: dopoPresa }));
  const undo = el.querySelector('#census-undo');
  if (undo) undo.addEventListener('click', async () => {
    if (undo.dataset.confirm !== '1') { undo.dataset.confirm = '1'; undo.textContent = 'Confermi? La scheda viene archiviata'; return; }
    try {
      await censusApi.undoCreate(property.id);
      showToast(container, { text: `${property.code || 'Scheda'} annullata (archiviata).` });
      await ricarica();
    } catch (error) {
      undo.dataset.confirm = '0';
      undo.textContent = 'Annulla creazione';
      showToast(container, { text: censusErrorMessage(error) });
    }
  });
  el.querySelector('#census-add-pertinenza').addEventListener('click', () => openPertinenzaSheet(dialogEl, {
    options: opzioni, property, building: censimento.building,          // R2: la pertinenza resta nella palazzina
    onSaved: async (esito) => {
      showToast(container, { text: esito.kind === 'pertinenza' ? `${esito.unit.code || 'Pertinenza'} collegata a ${property.code || 'questa scheda'}.` : (esito.accessory.cadastral_status === 'unknown' ? 'Accessorio salvato: da chiarire.' : 'Accessorio compreso salvato.') });
      await ricarica();
    },
  }));
  el.querySelector('#census-link-existing').addEventListener('click', () => openLinkExistingSheet(dialogEl, { property, onLinked: ricarica }));
  el.querySelectorAll('[data-unlink]').forEach((b) => b.addEventListener('click', async () => {
    const id = Number(b.dataset.unlink);
    if (!linkConfirm.has(id)) { linkConfirm.add(id); await showTab('censimento'); return; }
    linkConfirm.delete(id);
    try { await censusApi.unlinkPertinenza(property.id, id); } catch (error) { showToast(container, { text: censusErrorMessage(error) }); }
    await ricarica();
  }));
  el.querySelectorAll('[data-resolve]').forEach((b) => b.addEventListener('click', () => {
    const accessorio = censimento.accessories.find((a) => String(a.id) === b.dataset.resolve);
    openResolveSheet(dialogEl, { options: opzioni, property, accessory: accessorio, onResolved: async (r) => {
      showToast(container, { text: r.pertinenza ? `Pertinenza ${r.pertinenza.code || ''} collegata, accessorio rimosso.` : 'Segnato come compreso.' });
      await ricarica();
    } });
  }));
  el.querySelectorAll('[data-edit-accessory]').forEach((b) => b.addEventListener('click', () => {
    const accessorio = censimento.accessories.find((a) => String(a.id) === b.dataset.editAccessory);
    openAccessorySheet(dialogEl, { options: opzioni, property, accessory: accessorio, onSaved: ricarica });
  }));

  // Dati catastali: la PATCH generica (PropertyUpdate porta i campi della
  // 083); la categoria dal foglio di scelta, mai dedotta. La sezione e' a
  // tre stati (R1): null = non conosciuta, '' = nessuna, stringa = valore;
  // viaggia solo se cambia davvero, con il valore esatto (null o '').
  const formEl = el.querySelector('#census-cadastral-form');
  el.querySelector('#census-edit-cadastral').addEventListener('click', () => {
    if (!formEl.hidden) { formEl.hidden = true; return; }
    let categoria = property.cadastral_category || '';
    formEl.hidden = false;
    formEl.innerHTML = `
      <div class="form-field"><label>Categoria catastale</label><button type="button" class="btn census-category-btn" id="census-cat-btn">${escapeHtml(categoria || 'Da verificare ›')}</button></div>
      <div class="form-grid-3">
        <div class="form-field"><label for="cc-belfiore">Comune catastale</label><input id="cc-belfiore" class="input" maxlength="4" value="${escapeHtml(property.cadastral_municipality_code || '')}"></div>
        <div class="form-field"><label for="cc-sheet">Foglio</label><input id="cc-sheet" class="input" maxlength="10" value="${escapeHtml(property.cadastral_sheet || '')}"></div>
        <div class="form-field"><label for="cc-parcel">Particella</label><input id="cc-parcel" class="input" maxlength="10" value="${escapeHtml(property.cadastral_parcel || '')}"></div>
        <div class="form-field"><label for="cc-subunit">Subalterno</label><input id="cc-subunit" class="input" maxlength="10" value="${escapeHtml(property.cadastral_subunit || '')}"></div>
      </div>
      ${sectionFieldHtml('cc', property.cadastral_section)}
      <div class="field-error" id="cc-error"></div>
      <div class="action-bar"><button type="button" class="btn ghost" id="cc-cancel">Annulla</button><button type="button" class="btn primary" id="cc-save">Salva</button></div>`;
    formEl.querySelector('#census-cat-btn').addEventListener('click', () => openCategoryPicker(dialogEl, {
      options: opzioni, propertyType: property.property_type, current: categoria,
      onPick: (code) => { categoria = code || ''; formEl.querySelector('#census-cat-btn').textContent = categoria || 'Da verificare ›'; },
    }));
    const sezione = bindSectionField(formEl, 'cc', property.cadastral_section);
    formEl.querySelector('#cc-cancel').addEventListener('click', () => { formEl.hidden = true; });
    formEl.querySelector('#cc-save').addEventListener('click', async () => {
      const campi = { cadastral_category: categoria, cadastral_municipality_code: formEl.querySelector('#cc-belfiore').value,
        cadastral_sheet: formEl.querySelector('#cc-sheet').value,
        cadastral_parcel: formEl.querySelector('#cc-parcel').value, cadastral_subunit: formEl.querySelector('#cc-subunit').value };
      const payload = {};
      for (const [k, v] of Object.entries(campi)) {
        const nuovo = String(v ?? '').trim();
        if (nuovo !== String(property[k] ?? '').trim()) payload[k] = nuovo === '' ? null : nuovo;
      }
      const nuovaSezione = sezione.value();
      if (nuovaSezione === undefined) { formEl.querySelector('#cc-error').textContent = 'Hai scelto «Con sezione»: scrivi la sezione, oppure scegli «Non conosciuta» o «Nessuna».'; return; }
      const sezioneSalvata = property.cadastral_section === undefined ? null : property.cadastral_section;
      if (nuovaSezione !== sezioneSalvata) payload.cadastral_section = nuovaSezione;
      if (!Object.keys(payload).length) { formEl.hidden = true; return; }
      formEl.querySelector('#cc-save').disabled = true;
      try {
        // il client del censimento conserva `code` (CADASTRAL_DUPLICATE,
        // CENSUS_LOCKED...): il messaggio viene dal codice, non dal solo 409
        const aggiornato = await censusApi.updateProperty(property.id, payload);
        Object.assign(property, aggiornato);
        await showTab('censimento');
      } catch (error) {
        formEl.querySelector('#cc-save').disabled = false;
        formEl.querySelector('#cc-error').textContent = censusErrorMessage(error);
      }
    });
  });
}

// EDIFICI-1: il collegamento evidente all'edificio di appartenenza, dalla
// relazione reale (`building_id` -> `building` nella risposta del dettaglio).
// Nessun collegamento per un immobile autonomo.
export function buildingLinkHtml(p) {
  const b = p && p.building;
  if (!b || !b.id) return '';
  const via = [b.address, b.civic_number].filter((x) => x && String(x).trim()).join(' ');
  const nome = (b.name && String(b.name).trim()) || via || `Palazzina #${b.id}`;
  const dove = [b.name ? via : '', b.city].filter(Boolean).join(', ');
  return `<a class="property-building-link" id="property-building-link" href="#/edifici/${escapeHtml(b.id)}">`
    + `<span class="muted">Nell'edificio</span> <strong>${escapeHtml(nome)}</strong>${dove ? ` <span class="muted">${escapeHtml(dove)}</span>` : ''} ›</a>`;
}
