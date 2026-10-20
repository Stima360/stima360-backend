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
import { unitLabel } from '../components/property-label.js';
import { loadFormOptions } from '../components/property-form.js';
import * as censusApi from './census-api.js';
import {
  bindSectionField, openAccessorySheet, openCategoryPicker, openLinkExistingSheet, openLinkPrincipalSheet, openPertinenzaSheet,
  openResolveSheet, openTakeInChargeDialog, sectionFieldHtml, showToast,
} from './census-sheets.js';
import {
  errorMessage as censusErrorMessage, fromAccessoryText, labelOf, pertinenzaKindLabel, sectionLabel,
} from './census-model.js';
import { renderSiteProvenance } from './site-provenance.js';
// CENSIMENTO-2: provenienza di superficie e categoria, modificabile da qui
import {
  UNIT_SOURCES_CATALOG, evidenceText, hasCadastralSource, identityKey, needsReconfirm, provisionalNumbering, sourceLabel,
  unitSourcesPatch, verificationState,
} from './composition-model.js';

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
  // Fase A: lo stato letto per ultimo; il server rifiuta (STALE_DATA) se nel frattempo e' cambiato
  const atteso = () => (property.updated_at ? { expected_updated_at: property.updated_at } : {});
  const dopoPresa = async (esito) => {
    Object.assign(property, esito);
    showToast(container, { text: `${unitLabel(property, tipi, kinds) || 'Immobile'} preso in carico${esito.pertinenze_taken && esito.pertinenze_taken.length ? ` con ${esito.pertinenze_taken.length} pertinenze` : ''}.` });
    navigate('immobili', [property.id]);
  };
  // CENSIMENTO-2: le colonne della 100 ci sono solo dopo la migrazione; senza,
  // la tab resta com'era (nessun campo che il database non saprebbe salvare)
  const conProvenienza = Object.prototype.hasOwnProperty.call(property, 'surface_source');
  const fonte = (v) => sourceLabel(UNIT_SOURCES_CATALOG, v);
  const mqTesto = property.surface_sqm === null || property.surface_sqm === undefined || property.surface_sqm === ''
    ? '' : `${Number(property.surface_sqm).toLocaleString('it-IT', { maximumFractionDigits: 2 })} m²`;
  const catasto = [
    // D4: «Mancante» (il dato non c'e'), distinto dalla verifica catastale qui sotto
    ['Categoria catastale', `${property.cadastral_category || 'Mancante'}${conProvenienza && property.cadastral_category ? ` · ${fonte(property.cadastral_category_source)}` : ''}`],
    ...(conProvenienza ? [['Superficie', mqTesto ? `${mqTesto} · ${fonte(property.surface_source)}` : '']] : []),
    // la verifica catastale: indicatore separato dalla completezza della scheda
    // Fase A (A1): tre condizioni - verificata (superficie e categoria), parziale, da verificare/riconfermare
    ...(conProvenienza ? [['Verifica catastale', (() => {
      const statoVerifica = verificationState(property);
      if (statoVerifica === 'verified') return `Verifica completa: superficie e categoria · ${evidenceText(property)}`;
      if (statoVerifica === 'partial') return `Verifica parziale: ${property.surface_source === 'verified' ? 'superficie sì, categoria no' : 'categoria sì, superficie no'} · ${evidenceText(property)}`;
      if (needsReconfirm(property)) return `Verifica da riconfermare: i riferimenti catastali sono cambiati dopo la verifica (${evidenceText(property)})`;
      return 'Verifica da completare';
    })()]] : []),
    ['Comune catastale', property.cadastral_municipality_code], ['Sezione', sectionLabel(property.cadastral_section)],
    ['Foglio', property.cadastral_sheet], ['Particella', property.cadastral_parcel], ['Subalterno', property.cadastral_subunit],
  ];
  const edificio = censimento.building;
  const genitore = censimento.parent;
  // PERTINENZE-1: la natura della scheda, indipendente dal collegamento
  const pertinenza = censimento.is_pertinenza === true || !!genitore;
  const daCollegare = censimento.pertinenza_unlinked === true;
  const tipoPertinenza = pertinenzaKindLabel(censimento, kinds);
  const provenienza = fromAccessoryText(censimento.from_accessory, kinds);
  const confermaScollega = genitore && linkConfirm.has(`parent:${genitore.id}`);
  // Fase A (A2): interno generato in blocco e non ancora confermato
  const internoProvvisorio = conProvenienza && provisionalNumbering(property);
  el.innerHTML = `
    <div class="census-tab">
      ${isCensus() ? `<div class="census-banner" data-census-state><strong>Scheda in censimento</strong> — senza proprietario, incarico o stato commerciale. Per lavorarla commercialmente usa «Prendi in carico» (stessa scheda, stesso codice).
        <div class="action-bar"><button type="button" class="btn primary" id="census-take">Prendi in carico</button><button type="button" class="btn ghost" id="census-undo">Annulla creazione</button></div></div>` : ''}
      <div id="site-provenance-slot"></div>
      <h3 class="section-title">Collocazione</h3>
      <div class="detail-grid">
        <div class="detail-item"><label>Palazzina</label>${edificio ? `<a href="#/edifici/${escapeHtml(edificio.id)}" id="census-open-building">${escapeHtml(edificio.name || [edificio.address, edificio.civic_number].filter(Boolean).join(' ') || `#${edificio.id}`)}</a>` : '—'}</div>
        <div class="detail-item"><label>Indirizzo</label>${escapeHtml(censimento.address_inherited ? 'Ereditato dalla palazzina' : 'Proprio')}</div>
        <div class="detail-item"><label>Natura</label><span id="census-nature">${escapeHtml(pertinenza ? `Pertinenza autonoma${tipoPertinenza ? ` · ${tipoPertinenza}` : ''}` : 'Unità principale')}</span></div>
        <div class="detail-item"><label>Unità principale</label>${genitore ? `<a href="#/immobili/${escapeHtml(genitore.id)}" id="census-open-parent">${escapeHtml(genitore.code || `#${genitore.id}`)}</a>${genitore.archived_at ? ' <small class="muted">(archiviata)</small>' : ''}${genitore.same_building === false ? ' <small class="muted">(in un altro edificio)</small>' : ''}` : (daCollegare ? `${renderBadge('Da collegare', 'warn')}` : '—')}</div>
        <div class="detail-item"><label>Piano · scala · interno</label>${escapeHtml([property.floor, property.staircase ? `scala ${property.staircase}` : null, property.internal_number ? `int. ${property.internal_number}` : null].filter(Boolean).join(' · ') || '—')}${internoProvvisorio ? ` ${renderBadge('Interno provvisorio', 'gray')}` : ''}</div>
      </div>
      ${internoProvvisorio ? `<div class="action-bar" data-internal-provisional><span class="muted">L’interno è stato numerato in blocco: confermalo quando è quello reale (oppure correggilo da «Modifica dati catastali e superficie»).</span><button type="button" class="btn ghost btn-small" id="census-confirm-internal">Conferma interno</button></div>` : ''}
      ${provenienza ? `<p class="muted census-provenance" id="census-from-accessory">${escapeHtml(provenienza)}</p>` : ''}
      ${daCollegare ? '<div class="action-bar"><button type="button" class="btn primary" id="census-link-principal">Collega a un’unità principale</button></div>' : ''}
      ${genitore ? `<div class="action-bar"><button type="button" class="btn ghost btn-small${confermaScollega ? ' danger' : ''}" id="census-unlink-self">${confermaScollega ? 'Confermi lo scollegamento? Resta una pertinenza da collegare' : `Scollega da ${escapeHtml(unitLabel(genitore, tipi, kinds) || 'unità principale')}`}</button></div>` : ''}
      <h3 class="section-title">Dati catastali</h3>
      <div class="detail-grid">${catasto.map(([l, v]) => `<div class="detail-item"><label>${escapeHtml(l)}</label>${escapeHtml(v === null || v === undefined || v === '' ? '—' : v)}</div>`).join('')}</div>
      <div class="action-bar"><button type="button" class="btn ghost" id="census-edit-cadastral">${conProvenienza ? 'Modifica dati catastali e superficie' : 'Modifica dati catastali'}</button></div>
      <div id="census-cadastral-form" hidden></div>
      ${pertinenza ? '' : `<h3 class="section-title">Pertinenze collegate</h3>
      ${censimento.pertinenze.length ? `<ul class="census-list">${censimento.pertinenze.map((x) => `<li class="census-list-item" data-pertinenza-id="${escapeHtml(x.id)}"><a href="#/immobili/${escapeHtml(x.id)}"><strong>${escapeHtml(x.code || `#${x.id}`)}</strong></a> <span class="muted">${escapeHtml([pertinenzaKindLabel(x, kinds) || labelOf(tipi, x.property_type, x.property_type), x.surface_sqm ? `${x.surface_sqm} m²` : null, x.cadastral_subunit ? `sub ${x.cadastral_subunit}` : null, x.cadastral_category || 'Categoria mancante', x.same_building === false ? 'in un altro edificio' : null].filter(Boolean).join(' · '))}</span> <button type="button" class="btn ghost btn-small${linkConfirm.has(x.id) ? ' danger' : ''}" data-unlink="${escapeHtml(x.id)}">${linkConfirm.has(x.id) ? 'Confermi lo scollegamento?' : 'Scollega'}</button></li>`).join('')}</ul>` : '<p class="muted">Nessuna pertinenza collegata.</p>'}
      <div class="action-bar">
        <button type="button" class="btn primary" id="census-add-pertinenza">+ Aggiungi pertinenza</button>
        <button type="button" class="btn ghost" id="census-link-existing">Collega esistente</button>
      </div>`}
      <h3 class="section-title">Accessori${censimento.accessories_unknown ? ` · ${escapeHtml(String(censimento.accessories_unknown))} da chiarire` : ''}</h3>
      ${censimento.accessories.length ? `<ul class="census-list">${censimento.accessories.map((a) => `<li class="census-list-item" data-accessory-id="${escapeHtml(a.id)}"><strong>${escapeHtml(labelOf(kinds, a.kind, a.kind))}</strong> <span class="muted">${escapeHtml([a.quantity ? `× ${a.quantity}` : null, a.surface_sqm ? `${a.surface_sqm} m²` : null, a.notes].filter(Boolean).join(' · '))}</span> ${a.cadastral_status === 'unknown' ? renderBadge('Da chiarire', 'warn') : renderBadge('Compreso', 'gray')}${a.source === 'stima360' ? ` ${renderBadge('Dal sito', 'gray')}` : ''} ${a.cadastral_status === 'unknown' ? `<button type="button" class="btn btn-small" data-resolve="${escapeHtml(a.id)}">Chiarisci</button>` : (pertinenza ? '' : `<button type="button" class="btn ghost btn-small" data-resolve="${escapeHtml(a.id)}" data-review>Ha un suo sub?</button>`)}<button type="button" class="btn ghost btn-small" data-edit-accessory="${escapeHtml(a.id)}">Modifica</button></li>`).join('')}</ul>` : '<p class="muted">Nessun accessorio. Gli accessori compresi non contano mai come unità.</p>'}
    </div>`;

  // CATALOGO-CANONICO-1: «Dal sito Stima360» (stime, valori dichiarati, da
  // verificare, differenze, possibili doppioni). Indipendente: se non c'e',
  // la tab resta com'era.
  renderSiteProvenance(el.querySelector('#site-provenance-slot'), { property, options: opzioni, onChanged: ricarica, navigate });

  const take = el.querySelector('#census-take');
  if (take) take.addEventListener('click', () => openTakeInChargeDialog(dialogEl, { property, pertinenze: censimento.pertinenze, onDone: dopoPresa }));
  const undo = el.querySelector('#census-undo');
  if (undo) undo.addEventListener('click', async () => {
    if (undo.dataset.confirm !== '1') { undo.dataset.confirm = '1'; undo.textContent = 'Confermi? La scheda viene archiviata'; return; }
    try {
      await censusApi.undoCreate(property.id);
      showToast(container, { text: `${unitLabel(property, tipi, kinds) || 'Scheda'} annullata (archiviata).` });
      await ricarica();
    } catch (error) {
      undo.dataset.confirm = '0';
      undo.textContent = 'Annulla creazione';
      showToast(container, { text: censusErrorMessage(error) });
    }
  });
  // PERTINENZE-1: collegamento e scollegamento anche dal lato della pertinenza
  const collegaPrincipale = el.querySelector('#census-link-principal');
  if (collegaPrincipale) collegaPrincipale.addEventListener('click', () => openLinkPrincipalSheet(dialogEl, {
    options: opzioni, property, candidates: censimento.link_candidates || [],
    onLinked: async (esito) => {
      showToast(container, { text: `${unitLabel(property, tipi, kinds) || 'Pertinenza'} collegata a un’unità principale.` });
      await ricarica();
    },
  }));
  const scollegaSe = el.querySelector('#census-unlink-self');
  if (scollegaSe) scollegaSe.addEventListener('click', async () => {
    const chiave = `parent:${genitore.id}`;
    if (!linkConfirm.has(chiave)) { linkConfirm.add(chiave); await showTab('censimento'); return; }
    linkConfirm.delete(chiave);
    try { await censusApi.unlinkPertinenza(genitore.id, property.id); } catch (error) { showToast(container, { text: censusErrorMessage(error) }); }
    await ricarica();
  });
  const aggiungi = el.querySelector('#census-add-pertinenza');
  if (aggiungi) aggiungi.addEventListener('click', () => openPertinenzaSheet(dialogEl, {
    options: opzioni, property, building: censimento.building,          // R2: la pertinenza resta nella palazzina
    onSaved: async (esito) => {
      showToast(container, { text: esito.kind === 'pertinenza' ? `${esito.unit.code || 'Pertinenza'} collegata a ${property.code || 'questa scheda'}.` : (esito.accessory.cadastral_status === 'unknown' ? 'Accessorio salvato: da chiarire.' : 'Accessorio compreso salvato.') });
      await ricarica();
    },
  }));
  const collegaEsistente = el.querySelector('#census-link-existing');
  if (collegaEsistente) collegaEsistente.addEventListener('click', () => openLinkExistingSheet(dialogEl, { property, onLinked: ricarica }));
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

  // Fase A (A2): «Conferma interno» - stessa scheda, cade solo «provvisorio».
  // Esito incerto: si rilegge la scheda, mai un successo dichiarato a priori.
  const confermaInterno = el.querySelector('#census-confirm-internal');
  if (confermaInterno) confermaInterno.addEventListener('click', async () => {
    confermaInterno.disabled = true;
    try {
      await censusApi.updateCensusData(property.id, { confirm_internal_number: true, ...atteso() });
      showToast(container, { text: `Interno ${property.internal_number} confermato.` });
      await ricarica();
    } catch (error) {
      confermaInterno.disabled = false;
      if (error && (error.uncertain || error.code === 'STALE_DATA')) {
        await ricarica();
        showToast(container, { text: 'Esito non noto: la scheda è stata riletta dal server. Controlla l’interno prima di riprovare.' });
        return;
      }
      showToast(container, { text: error && error.code === 'UNIT_ARCHIVED' && error.detail ? error.detail : censusErrorMessage(error) });
    }
  });

  // Dati catastali. Con la migration 100 («Dati catastali e superficie»): UNA
  // chiamata a PATCH .../census-data (Fase A, A3), una transazione con lo
  // storico; senza la 100 la PATCH generica di prima (solo i campi della 083).
  // La categoria dal foglio di scelta, mai dedotta. La sezione e' a tre stati
  // (R1): null = non conosciuta, '' = nessuna, stringa = valore; viaggia solo
  // se cambia davvero, con il valore esatto (null o '').
  const formEl = el.querySelector('#census-cadastral-form');
  el.querySelector('#census-edit-cadastral').addEventListener('click', () => {
    if (!formEl.hidden) { formEl.hidden = true; return; }
    let categoria = property.cadastral_category || '';
    // la fonte registrata si propone solo se vale ancora per l'identità attuale
    const valida = conProvenienza && property.cadastral_evidence_identity && property.cadastral_evidence_identity === identityKey(property);
    const evidenzaIniziale = valida ? { type: property.cadastral_evidence_type || '', date: String(property.cadastral_evidence_date || '').slice(0, 10), ref: property.cadastral_evidence_ref || '' }
      : { type: '', date: '', ref: '' };
    const oggi = new Date().toISOString().slice(0, 10);
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
      ${conProvenienza ? `<div class="form-grid-2 census-sources" data-unit-sources>
        <div class="form-field"><label for="cc-category-source">Provenienza della categoria</label>${fonteSelect('cc-category-source', property.cadastral_category_source)}</div>
        <div class="form-field"><label for="cc-surface">Superficie m²</label><input id="cc-surface" class="input" inputmode="decimal" value="${escapeHtml(property.surface_sqm === null || property.surface_sqm === undefined ? '' : property.surface_sqm)}"></div>
        <div class="form-field"><label for="cc-surface-source">Provenienza della superficie</label>${fonteSelect('cc-surface-source', property.surface_source)}</div>
      </div>
      <div class="census-evidence" data-evidence-fields hidden>
        <p class="muted"><strong>Fonte consultata</strong> — «Verificata» si registra solo con il documento effettivamente consultato (l’allegato non è obbligatorio).</p>
        <div class="form-grid-2">
          <div class="form-field"><label for="cc-evidence-type">Tipo di documento</label><select id="cc-evidence-type" class="input"><option value="">Scegli…</option>${UNIT_SOURCES_CATALOG.evidence_types.map((x) => `<option value="${escapeHtml(x.value)}"${x.value === evidenzaIniziale.type ? ' selected' : ''}>${escapeHtml(x.label)}</option>`).join('')}</select></div>
          <div class="form-field"><label for="cc-evidence-date">Data della consultazione</label><input id="cc-evidence-date" class="input" type="date" max="${escapeHtml(oggi)}" value="${escapeHtml(evidenzaIniziale.date)}"></div>
        </div>
        <div class="form-field"><label for="cc-evidence-ref">Riferimento (facoltativo)</label><input id="cc-evidence-ref" class="input" maxlength="120" placeholder="es. protocollo della visura" value="${escapeHtml(evidenzaIniziale.ref)}"></div>
      </div>
      <div class="form-grid-2 census-internal" data-internal-fields>
        <div class="form-field"><label for="cc-internal">Interno</label><input id="cc-internal" class="input" maxlength="10" value="${escapeHtml(property.internal_number || '')}"></div>
        ${internoProvvisorio ? `<label class="form-field census-confirm-internal"><span><input type="checkbox" id="cc-confirm-internal"> Conferma l’interno (non più provvisorio)</span><small class="muted">Correggerlo senza confermarlo lo lascia provvisorio.</small></label>` : ''}
      </div>
      <p class="muted" id="cc-sources-hint">«Verificata» richiede l’identità catastale (comune catastale, foglio, particella, subalterno; la sezione solo se prevista) e la fonte consultata. Se cambi un valore, indica di nuovo la sua provenienza: altrimenti torna «Non indicata». Se cambi i riferimenti catastali, una verifica va riconfermata.</p>` : ''}
      <div class="field-error" id="cc-error" role="alert"></div>
      <div class="census-reconcile" id="cc-reconcile" hidden></div>
      <div class="action-bar"><button type="button" class="btn ghost" id="cc-cancel">Annulla</button><button type="button" class="btn" id="cc-save-anyway" hidden>Salva comunque</button><button type="button" class="btn primary" id="cc-save">Salva</button></div>`;
    formEl.querySelector('#census-cat-btn').addEventListener('click', () => openCategoryPicker(dialogEl, {
      options: opzioni, propertyType: property.property_type, current: categoria,
      onPick: (code) => {
        const cambiata = (code || '') !== categoria;
        categoria = code || '';
        formEl.querySelector('#census-cat-btn').textContent = categoria || 'Da verificare ›';
        // CENSIMENTO-2: categoria nuova = provenienza da confermare di nuovo
        const sel = formEl.querySelector('#cc-category-source');
        if (sel && cambiata) { sel.value = ''; aggiornaVerificata(); }
      },
    }));
    const identita = () => ({ cadastral_municipality_code: formEl.querySelector('#cc-belfiore').value,
      cadastral_sheet: formEl.querySelector('#cc-sheet').value, cadastral_parcel: formEl.querySelector('#cc-parcel').value,
      cadastral_subunit: formEl.querySelector('#cc-subunit').value });
    // «Verificata» si offre solo con la fonte catastale compilata (il server la richiede comunque)
    function aggiornaVerificata() {
      if (!conProvenienza) return;
      const ok = hasCadastralSource(identita(), UNIT_SOURCES_CATALOG);
      for (const id of ['#cc-category-source', '#cc-surface-source']) {
        const sel = formEl.querySelector(id);
        const voce = sel && sel.querySelector('option[value="verified"]');
        if (voce) voce.disabled = !ok;
        if (sel && !ok && sel.value === 'verified') sel.value = '';
      }
      const scelta = ['#cc-category-source', '#cc-surface-source'].some((id) => formEl.querySelector(id).value === 'verified');
      formEl.querySelector('[data-evidence-fields]').hidden = !scelta;
    }
    if (conProvenienza) {
      // riferimenti catastali cambiati: una verifica precedente non vale piu' da sola.
      // «Verificata» torna da scegliere e la fonte da indicare di nuovo (riconferma esplicita)
      const riferimentiCambiati = () => {
        if (identityKey({ ...property, ...identita() }) !== identityKey(property)) {
          for (const id of ['#cc-category-source', '#cc-surface-source']) {
            const sel = formEl.querySelector(id);
            if (sel.value === 'verified') sel.value = '';
          }
          ['#cc-evidence-type', '#cc-evidence-date', '#cc-evidence-ref'].forEach((s) => { formEl.querySelector(s).value = ''; });
        }
        aggiornaVerificata();
      };
      ['#cc-belfiore', '#cc-sheet', '#cc-parcel', '#cc-subunit'].forEach((s) => formEl.querySelector(s).addEventListener('input', riferimentiCambiati));
      ['#cc-category-source', '#cc-surface-source'].forEach((s) => formEl.querySelector(s).addEventListener('change', aggiornaVerificata));
      const sup = formEl.querySelector('#cc-surface');
      sup.addEventListener('input', () => {
        const prima = property.surface_sqm === null || property.surface_sqm === undefined ? '' : String(Number(property.surface_sqm));
        const ora = String(sup.value || '').trim() === '' ? '' : String(Number(String(sup.value).replace(',', '.')));
        if (ora !== prima) formEl.querySelector('#cc-surface-source').value = '';
      });
      aggiornaVerificata();
    }
    const sezione = bindSectionField(formEl, 'cc', property.cadastral_section);
    formEl.querySelector('#cc-cancel').addEventListener('click', () => { formEl.hidden = true; });
    const errore = (testo) => { formEl.querySelector('#cc-error').textContent = testo; };
    const riconcilia = formEl.querySelector('#cc-reconcile');
    const salvaComunque = formEl.querySelector('#cc-save-anyway');
    // il corpo della richiesta dal modulo, rispetto all'ultimo stato letto dal server (`property`)
    function corpoDelModulo() {
      const campi = { ...(conProvenienza ? {} : { cadastral_category: categoria }), cadastral_municipality_code: formEl.querySelector('#cc-belfiore').value,
        cadastral_sheet: formEl.querySelector('#cc-sheet').value,
        cadastral_parcel: formEl.querySelector('#cc-parcel').value, cadastral_subunit: formEl.querySelector('#cc-subunit').value,
        ...(conProvenienza ? { internal_number: formEl.querySelector('#cc-internal').value } : {}) };
      const payload = {};
      for (const [k, v] of Object.entries(campi)) {
        const nuovo = String(v ?? '').trim();
        if (nuovo !== String(property[k] ?? '').trim()) payload[k] = nuovo === '' ? null : nuovo;
      }
      const nuovaSezione = sezione.value();
      if (nuovaSezione === undefined) return { errors: ['Hai scelto «Con sezione»: scrivi la sezione, oppure scegli «Non conosciuta» o «Nessuna».'] };
      const sezioneSalvata = property.cadastral_section === undefined ? null : property.cadastral_section;
      if (nuovaSezione !== sezioneSalvata) payload.cadastral_section = nuovaSezione;
      if (conProvenienza) {
        // CENSIMENTO-2: superficie, categoria e provenienza; un valore cambiato
        // porta sempre la sua provenienza (anche «Non indicata»)
        const identitaModulo = { ...property, ...identita() };
        identitaModulo.cadastral_section = nuovaSezione;
        const fonti = unitSourcesPatch(property, {
          surface_sqm: formEl.querySelector('#cc-surface').value, surface_source: formEl.querySelector('#cc-surface-source').value,
          cadastral_category: categoria, cadastral_category_source: formEl.querySelector('#cc-category-source').value,
          cadastral_evidence_type: formEl.querySelector('#cc-evidence-type').value,
          cadastral_evidence_date: formEl.querySelector('#cc-evidence-date').value,
          cadastral_evidence_ref: formEl.querySelector('#cc-evidence-ref').value,
        }, { today: oggi, identityChanged: identityKey(identitaModulo) !== identityKey(property) });
        const verificata = fonti.patch.surface_source === 'verified' || fonti.patch.cadastral_category_source === 'verified';
        if (verificata && !hasCadastralSource(identitaModulo, UNIT_SOURCES_CATALOG)) {
          fonti.errors.push('«Verificata» richiede comune catastale, foglio, particella e subalterno.');
        }
        if (fonti.errors.length) return { errors: fonti.errors };
        Object.assign(payload, fonti.patch);
        const conferma = formEl.querySelector('#cc-confirm-internal');
        if (conferma && conferma.checked && provisionalNumbering(property)) payload.confirm_internal_number = true;
      }
      return { payload, errors: [] };
    }
    // esito incerto o scheda cambiata nel frattempo: si rilegge dal server, si
    // mostrano le differenze con i valori inseriti (che restano nel modulo) e
    // si riprova solo su scelta esplicita, rispetto allo stato appena letto
    async function rileggiEConfronta(inviato, titolo) {
      let letto = null;
      try { letto = await apiGet(`/api/property/properties/${property.id}`); } catch (_e) { letto = null; }
      if (!letto) {
        errore(`${titolo} Il server ora non risponde: i valori inseriti restano qui, riprova quando la connessione torna.`);
        return;
      }
      Object.assign(property, letto);
      const etichette = { cadastral_municipality_code: 'Comune catastale', cadastral_section: 'Sezione', cadastral_sheet: 'Foglio',
        cadastral_parcel: 'Particella', cadastral_subunit: 'Subalterno', surface_sqm: 'Superficie', surface_source: 'Provenienza della superficie',
        cadastral_category: 'Categoria', cadastral_category_source: 'Provenienza della categoria', internal_number: 'Interno',
        cadastral_evidence_type: 'Fonte consultata', cadastral_evidence_date: 'Data della fonte', cadastral_evidence_ref: 'Riferimento della fonte' };
      const testo = (v) => (v === null || v === undefined || v === '' ? '—' : String(v));
      const uguali = (k, a, b) => (k === 'surface_sqm' && a !== null && a !== undefined && b !== null && b !== undefined
        ? Number(a) === Number(b) : String(a ?? '').trim().toUpperCase() === String(b ?? '').trim().toUpperCase());
      const diverse = Object.keys(inviato).filter((k) => etichette[k] && !uguali(k, letto[k], inviato[k]));
      riconcilia.hidden = false;
      riconcilia.innerHTML = `<p><strong>${escapeHtml(titolo)}</strong></p>${diverse.length
        ? `<p class="muted">Sul server ora risulta diverso da quanto hai inserito:</p><ul class="census-list">${diverse.map((k) => `<li data-reconcile-field="${escapeHtml(k)}">${escapeHtml(etichette[k])}: salvato <strong>${escapeHtml(testo(letto[k]))}</strong> · inserito <strong>${escapeHtml(testo(inviato[k]))}</strong></li>`).join('')}</ul><p class="muted">I valori inseriti restano nel modulo: «Salva» li applica ai dati appena riletti.</p>`
        : '<p class="muted" data-reconcile-same>I dati salvati sul server coincidono con quelli inseriti: puoi chiudere il modulo.</p>'}`;
      errore('');
    }
    async function salva({ simili = false } = {}) {
      errore('');
      salvaComunque.hidden = true;
      const { payload, errors } = corpoDelModulo();
      if (errors.length) { errore(errors.join(' ')); return; }
      if (!Object.keys(payload).length) { formEl.hidden = true; return; }
      const bottone = formEl.querySelector('#cc-save');
      bottone.disabled = true;
      try {
        // il client del censimento conserva `code` (CADASTRAL_DUPLICATE,
        // CENSUS_LOCKED...): il messaggio viene dal codice, non dal solo 409
        if (conProvenienza) {
          await censusApi.updateCensusData(property.id, { ...payload, ...(simili ? { confirm_similar: true } : {}), ...atteso() });
          // la tab si ridisegna dai dati riletti dal server (il modulo si chiude con essa)
          await ricarica();
          showToast(container, { text: 'Dati catastali e superficie salvati.' });
        } else {
          const aggiornato = await censusApi.updateProperty(property.id, payload);
          Object.assign(property, aggiornato);
          await showTab('censimento');
        }
      } catch (error) {
        // il modulo resta aperto con i valori inseriti; nessun messaggio di successo
        bottone.disabled = false;
        if (conProvenienza && error && (error.uncertain || error.code === 'STALE_DATA')) {
          await rileggiEConfronta(payload, error.uncertain
            ? 'Esito non noto: la scheda è stata riletta dal server. Il salvataggio non è dato per riuscito.'
            : censusErrorMessage(error));
          return;
        }
        if (error && error.code === 'SIMILAR_FOUND') {
          errore(`Nessuna modifica salvata: ${error.detail || 'esiste già un interno uguale in questa posizione.'}${(error.similar || []).length ? ` (${error.similar.map((x) => x.code || `#${x.id}`).join(', ')})` : ''}`);
          salvaComunque.hidden = false;
          return;
        }
        const motivo = error && error.code === 'UNIT_ARCHIVED' && error.detail ? error.detail : censusErrorMessage(error);
        errore(conProvenienza ? `Nessuna modifica salvata: ${motivo}` : motivo);   // senza la 100: come prima
      }
    }
    formEl.querySelector('#cc-save').addEventListener('click', () => salva());
    salvaComunque.addEventListener('click', () => salva({ simili: true }));
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

// CENSIMENTO-2: il selettore della provenienza (null = «Non indicata»)
function fonteSelect(id, selected) {
  return `<select id="${escapeHtml(id)}" class="input"><option value="">Non indicata</option>${UNIT_SOURCES_CATALOG.data_sources.map((x) => `<option value="${escapeHtml(x.value)}"${x.value === selected ? ' selected' : ''}>${escapeHtml(x.label)}</option>`).join('')}</select>`;
}
