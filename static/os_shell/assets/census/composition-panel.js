// STIMA360 OS — census/composition-panel.js (CENSIMENTO-2)
//
// Il pannello «Composizione e interni» della scheda edificio (#/edifici/{id}),
// dentro il censimento esistente:
//
//   * stato del completamento (calcolato dal server: Bozza -> Composizione
//     dichiarata -> Unità create -> Schede complete) e DUE indicatori separati:
//     unità create rispetto alle dichiarate, schede complete;
//   * composizione per classe (abitazioni, commerciali, altre, pertinenze):
//     dichiarate, presenti, da creare; «Dichiara / Modifica composizione»
//     (bozza sempre salvabile, totale = somma solo su scelta esplicita);
//   * «Crea le unità mancanti»: gruppi -> ANTEPRIMA (sola lettura) ->
//     conferma con i conteggi dell'anteprima, una sola chiave del lotto per
//     anteprima, conferma e ritentativi (nessun doppione); poi «Annulla» del
//     lotto (solo le schede ancora intatte);
//   * gli INTERNI: per ogni scheda attiva interno, scala, piano, scheda (link),
//     tipologia, superficie e categoria con la loro provenienza, pertinenze e
//     dati mancanti; filtro «Da completare»; selezione e «Modifica
//     selezionate» (elenco chiuso di campi, mai «Verificata» in gruppo).
//
// Ogni singola unità resta modificabile dalla propria scheda (tab
// Censimento). Le regole e i permessi li decide il backend
// (property/composition.py); qui solo la lettura e i controlli di forma.
// Nessuna riclassificazione e nessuna unità creata senza conferma.
//
// Contratti: GET/PUT /api/property/buildings/{id}/composition,
// POST .../units/generate, POST .../units/generate/{lotto}/undo,
// POST .../units/bulk-update (census-api.js).

import { escapeHtml, renderBadge } from '../components/st-table.js';
import * as api from './census-api.js';
import { openCategoryPicker, showToast } from './census-sheets.js';
import { errorMessage, floorLabel, labelOf } from './census-model.js';
import {
  bulkPayload, classLabel, compositionForm, compositionPayload, compositionView, defaultGroups, generatePayload,
  generatedText, hasOverDeclared, interiorRows, missingText, pertinenzeText, planRows, sourceLabel,
  sourceOptions, sourceTone, undoBatchText,
} from './composition-model.js';

const str = (v) => (v === null || v === undefined ? '' : String(v));

function mq(valore) {
  if (valore === null || valore === undefined || valore === '') return '';
  const n = Number(valore);
  return Number.isNaN(n) ? '' : `${n.toLocaleString('it-IT', { maximumFractionDigits: 2 })} m²`;
}

function sourceSelectHtml(id, catalog, selected, { bulk = false, verifiedAllowed = true, emptyLabel = 'Non indicata' } = {}) {
  const voci = sourceOptions(catalog, { bulk }).filter((x) => verifiedAllowed || x.value !== 'verified');
  return `<select id="${escapeHtml(id)}" class="input">
    <option value="">${escapeHtml(emptyLabel)}</option>
    ${voci.map((x) => `<option value="${escapeHtml(x.value)}"${x.value === selected ? ' selected' : ''}>${escapeHtml(x.label)}</option>`).join('')}
  </select>`;
}

function childDialog(dialogEl, suffix) {
  const id = `${dialogEl.id || 'composition'}-${suffix}`;
  let el = dialogEl.parentNode ? dialogEl.parentNode.querySelector(`#${id}`) : null;
  if (!el) {
    el = document.createElement('dialog');
    el.id = id;
    el.className = 'modal census-sheet';
    (dialogEl.parentNode || document.body).appendChild(el);
  }
  return el;
}

function apri(dialogEl) {
  dialogEl.className = 'modal census-sheet composition-sheet';
  if (!dialogEl._open && !dialogEl.open) dialogEl.showModal();
}

/**
 * @param {HTMLElement} el  il contenitore del pannello
 * @param {{buildingId: number, options: object, inTrash: boolean, host: HTMLElement,
 *          dialogEl: HTMLDialogElement, canEdit: Function, navigate: Function, onChanged: Function}} ctx
 *   canEdit(u) -> vero se l'operatore puo' modificare la scheda (census: agenzia; crm: gestione)
 *   onChanged() -> chi chiama ricarica la scheda edificio (contatori, elenco per piano)
 */
export async function renderCompositionPanel(el, ctx) {
  const stato = { data: null, filter: 'all', selected: new Set() };

  async function carica() {
    el.innerHTML = '<p class="muted">Caricamento della composizione…</p>';
    try {
      stato.data = await api.getComposition(ctx.buildingId);
    } catch (error) {
      if (error.code === 'COMPOSITION_NOT_INSTALLED') {
        el.innerHTML = `<h3 class="section-title">Composizione e interni</h3><p class="muted" data-composition-unavailable>${escapeHtml(errorMessage(error))}</p>`;
        return;
      }
      el.innerHTML = `<div class="error-box">${escapeHtml(errorMessage(error))} <button type="button" class="btn ghost btn-small" data-composition-retry>Riprova</button></div>`;
      el.querySelector('[data-composition-retry]').addEventListener('click', carica);
      return;
    }
    if (!compositionView(stato.data)) { el.hidden = true; el.innerHTML = ''; return; }
    el.hidden = false;
    const vive = new Set((stato.data.units || []).map((u) => String(u.id)));
    stato.selected = new Set([...stato.selected].filter((x) => vive.has(x)));
    disegna();
  }

  async function aggiornaTutto() {
    if (ctx.onChanged) await ctx.onChanged();
    await carica();
  }

  function disegna() {
    const d = stato.data;
    const v = compositionView(d);
    const catalog = d.catalog || {};
    const tipi = ctx.options.property_types || [];
    const kinds = ctx.options.accessory_kinds || [];
    const modificabile = (u) => !ctx.inTrash && ctx.canEdit(u);
    const tutte = interiorRows(d.units);
    const righe = interiorRows(d.units, { onlyIncomplete: stato.filter === 'incomplete' });
    const incomplete = tutte.filter((u) => (u.missing || []).length).length;
    const toneStato = v.state === 'data_complete' ? 'ok' : (v.state === 'draft' ? 'warn' : 'role');
    el.innerHTML = `
      <div class="composition-head">
        <h3 class="section-title">Composizione e interni</h3>
        <span data-completion-state="${escapeHtml(v.state)}">${renderBadge(v.stateLabel, toneStato)}</span>
      </div>
      ${v.draft ? `<div class="census-banner" data-composition-draft><strong>Bozza</strong> — la composizione non è ancora dichiarata del tutto. L’edificio resta salvato e si completa quando vuoi: indica quante abitazioni, commerciali, altre unità e pertinenze ha (anche «Non so» per ora).</div>` : ''}
      <div class="building-counts composition-indicators">
        <span class="building-count" data-indicator="units"><small>Unità create</small><b>${escapeHtml(v.unitsIndicator)}</b>${v.unitsDone ? '<em>tutte presenti</em>' : ''}</span>
        <span class="building-count" data-indicator="data"><small>Schede complete</small><b>${escapeHtml(v.dataIndicator)}</b><em>dati minimi: ${escapeHtml(((catalog.minimum_data) || []).map((x) => x.label).join(', '))}</em></span>
      </div>
      ${v.totalNote ? `<p class="muted" data-total-note>${escapeHtml(v.totalNote)}</p>` : ''}
      <div class="composition-classes" data-composition-classes>
        ${v.rows.map((r) => `<div class="composition-class" data-class="${escapeHtml(r.value)}">
          <strong>${escapeHtml(r.label)}</strong>
          <span><small>Dichiarate</small>${escapeHtml(r.declared)}</span>
          <span><small>Presenti</small>${escapeHtml(r.present)}</span>
          <span><small>Da creare</small>${escapeHtml(r.toCreate)}${r.over ? ` ${renderBadge(r.over, 'warn')}` : ''}</span>
        </div>`).join('')}
      </div>
      ${ctx.inTrash ? '' : `<div class="action-bar composition-actions">
        <button type="button" class="btn${v.complete ? ' ghost' : ' primary'}" data-composition-edit>${v.complete ? 'Modifica composizione' : 'Dichiara composizione'}</button>
        <button type="button" class="btn${v.canGenerate ? ' primary' : ''}" data-generate${v.canGenerate ? '' : ' disabled'}>Crea le unità mancanti${v.toCreate ? ` (${escapeHtml(v.toCreate)})` : ''}</button>
      </div>
      ${v.complete ? '' : '<p class="muted composition-hint">Per creare le unità mancanti serve la composizione completa: le unità già presenti non vengono mai ricreate.</p>'}`}
      <h4 class="census-subtitle">Interni</h4>
      <div class="census-chips composition-filter" data-interior-filter>
        <button type="button" class="chip${stato.filter === 'all' ? ' active' : ''}" data-filter="all">Tutti (${tutte.length})</button>
        <button type="button" class="chip${stato.filter === 'incomplete' ? ' active' : ''}" data-filter="incomplete">Da completare (${incomplete})</button>
      </div>
      <div class="composition-bulk-bar" data-bulk-bar${ctx.inTrash ? ' hidden' : ''}>
        <span class="muted" data-selected-count>${escapeHtml(stato.selected.size ? `${stato.selected.size} selezionate` : 'Seleziona gli interni da modificare insieme')}</span>
        <span class="action-bar">
          <button type="button" class="btn ghost btn-small" data-select-all>Seleziona visibili</button>
          ${stato.selected.size ? '<button type="button" class="btn ghost btn-small" data-select-none>Deseleziona</button>' : ''}
          <button type="button" class="btn btn-small${stato.selected.size ? ' primary' : ''}" data-bulk-edit${stato.selected.size ? '' : ' disabled'}>Modifica selezionate</button>
        </span>
      </div>
      ${righe.length ? `<ul class="composition-interiors" data-interiors>${righe.map((u) => {
        const dove = [u.internal_number ? `int. ${u.internal_number}` : 'int. —', u.staircase ? `scala ${u.staircase}` : null,
          u.floor !== null && u.floor !== undefined && str(u.floor).trim() ? `piano ${floorLabel(u.floor)}` : null].filter(Boolean).join(' · ');
        const tipo = u.pertinenza_kind ? labelOf(kinds, u.pertinenza_kind, u.pertinenza_kind) : labelOf(tipi, u.property_type, u.property_type);
        const pert = pertinenzeText(u);
        const editabile = modificabile(u);
        return `<li class="interior-row${(u.missing || []).length ? '' : ' interior-complete'}" data-interior="${escapeHtml(u.id)}">
          ${editabile ? `<label class="interior-check"><input type="checkbox" data-select-unit="${escapeHtml(u.id)}" aria-label="Seleziona ${escapeHtml(u.code || `#${u.id}`)}"${stato.selected.has(String(u.id)) ? ' checked' : ''}></label>` : '<span class="interior-check"></span>'}
          <div class="interior-body">
            <a href="#/immobili/${escapeHtml(u.id)}" data-open-interior="${escapeHtml(u.id)}" class="interior-title"><strong>${escapeHtml(dove)}</strong> <span class="muted">${escapeHtml(u.code || `#${u.id}`)}</span></a>
            <div class="interior-facts">
              <span data-fact="type">${escapeHtml(tipo)} <small class="muted">${escapeHtml(classLabel(catalog, u.unit_class))}</small></span>
              <span data-fact="surface">${escapeHtml(mq(u.surface_sqm) || 'm² —')} ${renderBadge(sourceLabel(catalog, u.surface_source), sourceTone(u.surface_source))}</span>
              <span data-fact="category">${escapeHtml(u.cadastral_category || 'Categoria —')} ${renderBadge(sourceLabel(catalog, u.cadastral_category_source), sourceTone(u.cadastral_category_source))}</span>
              ${pert ? `<span data-fact="pertinenze">${escapeHtml(pert)}</span>` : ''}
            </div>
            <div class="interior-state" data-fact="state">${(u.missing || []).length ? renderBadge(missingText(u, catalog), 'warn') : renderBadge('Completa', 'ok')}${u.record_kind === 'crm' ? ` ${renderBadge('In carico', 'role')}` : ''}${u.provisional_numbering ? ` ${renderBadge('Interno provvisorio', 'gray')}` : ''}${u.verification === 'verified' ? ` ${renderBadge('Verifica catastale', 'ok')}` : ''}${u.verification === 'to_reconfirm' ? ` ${renderBadge('Verifica da riconfermare', 'warn')}` : ''}</div>
          </div>
        </li>`;
      }).join('')}</ul>` : `<p class="muted" data-interiors-empty>${stato.filter === 'incomplete' ? 'Nessuna scheda da completare.' : 'Nessuna unità attiva in questo edificio.'}</p>`}`;
    collega();
  }

  function collega() {
    el.querySelectorAll('[data-open-interior]').forEach((a) => a.addEventListener('click', (ev) => {
      if (ev && typeof ev.preventDefault === 'function') ev.preventDefault();
      ctx.navigate('immobili', [a.dataset.openInterior]);
    }));
    el.querySelectorAll('[data-filter]').forEach((b) => b.addEventListener('click', () => { stato.filter = b.dataset.filter; disegna(); }));
    el.querySelectorAll('[data-select-unit]').forEach((c) => c.addEventListener('change', () => {
      if (c.checked) stato.selected.add(c.dataset.selectUnit); else stato.selected.delete(c.dataset.selectUnit);
      disegna();
    }));
    el.querySelector('[data-select-all]')?.addEventListener('click', () => {
      el.querySelectorAll('[data-select-unit]').forEach((c) => stato.selected.add(c.dataset.selectUnit));
      disegna();
    });
    el.querySelector('[data-select-none]')?.addEventListener('click', () => { stato.selected.clear(); disegna(); });
    el.querySelector('[data-composition-edit]')?.addEventListener('click', () => openCompositionSheet(ctx.dialogEl, {
      data: stato.data, options: ctx.options, buildingId: ctx.buildingId,
      onSaved: async () => { showToast(ctx.host, { text: 'Composizione salvata.' }); await aggiornaTutto(); },
    }));
    el.querySelector('[data-generate]')?.addEventListener('click', () => openGenerateSheet(ctx.dialogEl, {
      data: stato.data, options: ctx.options, buildingId: ctx.buildingId,
      onDone: async (esito) => {
        await aggiornaTutto();
        if (esito.replica || !esito.batch_key) { showToast(ctx.host, { text: generatedText(esito) }); return; }
        showToast(ctx.host, {
          text: generatedText(esito), actionLabel: 'Annulla',
          onAction: async () => {
            try {
              const r = await api.undoUnitsBatch(ctx.buildingId, esito.batch_key);
              showToast(ctx.host, { text: undoBatchText(r) });
            } catch (error) {
              showToast(ctx.host, { text: errorMessage(error) });
            }
            await aggiornaTutto();
          },
        });
      },
    }));
    el.querySelector('[data-bulk-edit]')?.addEventListener('click', () => {
      const scelte = (stato.data.units || []).filter((u) => stato.selected.has(String(u.id)));
      if (!scelte.length) return;
      openBulkSheet(ctx.dialogEl, {
        units: scelte, data: stato.data, options: ctx.options, buildingId: ctx.buildingId,
        onDone: async (esito) => {
          stato.selected.clear();
          showToast(ctx.host, { text: `${esito.updated} ${esito.updated === 1 ? 'unità aggiornata' : 'unità aggiornate'}.` });
          await aggiornaTutto();
        },
      });
    });
  }

  await carica();
  return { reload: carica };
}

// --- «Dichiara / Modifica composizione» -------------------------------------------------

export function openCompositionSheet(dialogEl, { data, options: opzioni, buildingId, onSaved } = {}) {
  apri(dialogEl);
  const catalog = data.catalog || {};
  const form = compositionForm(data);
  const totale = data.composition ? data.composition.units_declared : null;
  const fonti = opzioni.units_declared_sources || [];
  dialogEl.innerHTML = `
    <form data-composition-form novalidate>
      <h2 class="census-sheet-title">Composizione dell’edificio</h2>
      <p class="muted">Quante unità ha l’edificio, per classe. Lascia vuoto se non lo sai: la bozza si salva comunque. Box e cantine con un loro subalterno vanno fra le pertinenze solo se lo sono davvero; altrimenti sono «altre unità».</p>
      <div class="form-grid-2">
        ${((catalog.classes) || []).map((k) => `<div class="form-field"><label for="cmp-${escapeHtml(k.value)}">${escapeHtml(k.label)}</label>
          <input id="cmp-${escapeHtml(k.value)}" class="input" type="number" inputmode="numeric" min="0" step="1" placeholder="Non so" value="${escapeHtml(form[k.value])}"></div>`).join('')}
      </div>
      <p class="muted" data-composition-sum></p>
      <label class="census-check" data-set-total-row><input type="checkbox" id="cmp-set-total"${totale === null || totale === undefined ? ' checked' : ''}> <span data-set-total-label>Usa la somma come totale dichiarato</span></label>
      <div class="form-field" data-total-source-row><label for="cmp-source">Fonte del totale</label>
        <select id="cmp-source" class="input"><option value="">Invariata</option>${fonti.map((f) => `<option value="${escapeHtml(f.value)}">${escapeHtml(f.label)}</option>`).join('')}</select></div>
      <div class="field-error" data-error role="alert"></div>
      <div class="modal-actions">
        <button type="button" class="btn ghost" data-cancel>Annulla</button>
        <button type="submit" class="btn primary" data-submit>Salva composizione</button>
      </div>
    </form>`;
  const $ = (s) => dialogEl.querySelector(s);
  const leggi = () => Object.fromEntries(((catalog.classes) || []).map((k) => [k.value, $(`#cmp-${k.value}`).value]));
  const aggiorna = () => {
    const p = compositionPayload(leggi());
    const riga = $('[data-composition-sum]');
    if (!p.complete) riga.textContent = `Totale dichiarato attuale: ${totale === null || totale === undefined ? 'non noto' : totale}. Con una classe «Non so» il totale non cambia.`;
    else if (totale === p.sum) riga.textContent = `Somma: ${p.sum} — coincide con il totale dichiarato.`;
    else riga.textContent = `Somma: ${p.sum} — totale dichiarato attuale: ${totale === null || totale === undefined ? 'non noto' : totale}.`;
    $('[data-set-total-row]').hidden = !p.complete || totale === p.sum;
    $('[data-total-source-row]').hidden = !p.complete || totale === p.sum || !$('#cmp-set-total').checked;
  };
  const cambia = () => { $('[data-error]').textContent = ''; aggiorna(); };
  ((catalog.classes) || []).forEach((k) => $(`#cmp-${k.value}`).addEventListener('input', cambia));
  $('#cmp-set-total').addEventListener('change', cambia);
  aggiorna();
  $('[data-cancel]').addEventListener('click', () => dialogEl.close());
  let occupato = false;
  $('[data-composition-form]').addEventListener('submit', async (ev) => {
    if (ev && typeof ev.preventDefault === 'function') ev.preventDefault();
    if (occupato) return;
    const visibile = !$('[data-set-total-row]').hidden;
    const p = compositionPayload(leggi(), { setTotal: visibile && $('#cmp-set-total').checked, source: $('#cmp-source').value });
    if (p.errors.length) { $('[data-error]').textContent = p.errors.join(' '); return; }
    occupato = true;
    $('[data-submit]').disabled = true;
    $('[data-error]').textContent = '';
    try {
      const esito = await api.setComposition(buildingId, p.body);
      dialogEl.close();
      if (onSaved) await onSaved(esito);
    } catch (error) {
      $('[data-error]').textContent = errorMessage(error);
    } finally {
      occupato = false;
      $('[data-submit]').disabled = false;
    }
  });
}

// --- «Crea le unità mancanti» ----------------------------------------------------------

export function openGenerateSheet(dialogEl, { data, options: opzioni, buildingId, onDone } = {}) {
  apri(dialogEl);
  const catalog = data.catalog || {};
  const tipi = opzioni.property_types || [];
  const kinds = opzioni.accessory_kinds || [];
  const mappa = Object.fromEntries((opzioni.pertinenza_property_types || []).map((x) => [x.value, x.property_type]));
  const gruppi = defaultGroups(data);
  const chiave = api.newClientRequestId();       // una sola chiave: anteprima, conferma, ritentativi
  let anteprima = null;
  let occupato = false;
  let confermaEccedenza = false;
  let confermaSimili = false;
  let incerto = false;

  function nuovoGruppo(classe) {
    const k = ((catalog.classes) || []).find((x) => x.value === classe) || ((catalog.classes) || [])[0];
    return { unit_class: k.value, count: '1', property_type: k.property_types[0], surface_sqm: '', surface_source: '',
      cadastral_category: '', cadastral_category_source: '', floor: '', staircase: '', pertinenza_kind: '',
      numbering: false, numbering_start: '1' };
  }

  function gruppoHtml(g, i) {
    const classe = ((catalog.classes) || []).find((x) => x.value === g.unit_class) || { property_types: [] };
    const tipiAmmessi = classe.property_types.map((t) => ({ value: t, label: labelOf(tipi, t, t) }));
    const kindsAmmessi = kinds.filter((k) => !mappa[k.value] || mappa[k.value] === g.property_type);
    return `<fieldset class="composition-group" data-group="${i}">
      <legend>${escapeHtml(classLabel(catalog, g.unit_class))}</legend>
      <div class="form-grid-2">
        <div class="form-field"><label for="g${i}-count">Quante</label><input id="g${i}-count" class="input" type="number" inputmode="numeric" min="1" step="1" value="${escapeHtml(g.count)}"></div>
        <div class="form-field"><label for="g${i}-type">Tipologia</label><select id="g${i}-type" class="input">${tipiAmmessi.map((t) => `<option value="${escapeHtml(t.value)}"${t.value === g.property_type ? ' selected' : ''}>${escapeHtml(t.label)}</option>`).join('')}</select></div>
      </div>
      ${g.unit_class === 'pertinenze' ? `<div class="form-field"><label for="g${i}-kind">Tipo di pertinenza</label><select id="g${i}-kind" class="input"><option value="">Non indicato</option>${kindsAmmessi.map((k) => `<option value="${escapeHtml(k.value)}"${k.value === g.pertinenza_kind ? ' selected' : ''}>${escapeHtml(k.label)}</option>`).join('')}</select><small class="muted">Nascono «da collegare»: l’unità principale si sceglie dopo.</small></div>` : ''}
      ${g.unit_class === 'residential' && g.property_type === 'rustic' ? '<p class="muted" data-rustic-note>Creati qui, i rustici sono classificati esplicitamente come abitazioni.</p>' : ''}
      <details class="census-details composition-group-details" data-group-details="${i}"${g.floor || g.staircase || g.surface_sqm || g.cadastral_category || g.numbering ? ' open' : ''}>
      <summary>Dati iniziali (facoltativi): piano, scala, superficie, categoria, interni</summary>
      <div class="form-grid-2">
        <div class="form-field"><label for="g${i}-floor">Piano (facoltativo)</label><input id="g${i}-floor" class="input" maxlength="50" value="${escapeHtml(g.floor)}"></div>
        <div class="form-field"><label for="g${i}-stair">Scala (facoltativa)</label><input id="g${i}-stair" class="input" maxlength="10" value="${escapeHtml(g.staircase)}"></div>
      </div>
      <div class="form-grid-2">
        <div class="form-field"><label for="g${i}-surface">Superficie iniziale m² (facoltativa)</label><input id="g${i}-surface" class="input" inputmode="decimal" value="${escapeHtml(g.surface_sqm)}"></div>
        <div class="form-field"><label for="g${i}-surface-source">Provenienza della superficie</label>${sourceSelectHtml(`g${i}-surface-source`, catalog, g.surface_source, { bulk: true, emptyLabel: 'Scegli…' })}</div>
      </div>
      <div class="form-grid-2">
        <div class="form-field"><label>Categoria (facoltativa, mai dedotta)</label><button type="button" class="btn census-category-btn" data-group-category="${i}">${escapeHtml(g.cadastral_category || 'Nessuna ›')}</button></div>
        <div class="form-field"><label for="g${i}-cat-source">Provenienza della categoria</label>${sourceSelectHtml(`g${i}-cat-source`, catalog, g.cadastral_category_source, { bulk: true, emptyLabel: 'Scegli…' })}</div>
      </div>
      <label class="census-check"><input type="checkbox" id="g${i}-numbering"${g.numbering ? ' checked' : ''}> Numera gli interni (provvisorio) a partire da</label>
      <input id="g${i}-start" class="input composition-start" type="number" inputmode="numeric" min="0" step="1" value="${escapeHtml(g.numbering_start)}"${g.numbering ? '' : ' hidden'}>
      </details>
      <div class="action-bar"><button type="button" class="btn ghost btn-small" data-remove-group="${i}">Togli questo gruppo</button></div>
    </fieldset>`;
  }

  function leggi() {
    gruppi.forEach((g, i) => {
      const $ = (s) => dialogEl.querySelector(s);
      if (!$(`#g${i}-count`)) return;
      g.count = $(`#g${i}-count`).value;
      g.property_type = $(`#g${i}-type`).value;
      g.floor = $(`#g${i}-floor`).value;
      g.staircase = $(`#g${i}-stair`).value;
      g.surface_sqm = $(`#g${i}-surface`).value;
      g.surface_source = $(`#g${i}-surface-source`).value;
      g.cadastral_category_source = $(`#g${i}-cat-source`).value;
      g.numbering = $(`#g${i}-numbering`).checked;
      g.numbering_start = $(`#g${i}-start`).value;
      if ($(`#g${i}-kind`)) g.pertinenza_kind = $(`#g${i}-kind`).value;
    });
  }

  function disegna(errore = '') {
    const righe = anteprima ? planRows(anteprima, catalog) : [];
    const eccedenza = anteprima && hasOverDeclared(anteprima);
    const conflitti = (anteprima && anteprima.numbering_conflicts) || [];
    dialogEl.innerHTML = `
      <div data-generate-sheet>
        <h2 class="census-sheet-title">Crea le unità mancanti</h2>
        <p class="muted">Nascono come schede di censimento in bozza, con l’indirizzo dell’edificio. Le unità già presenti non vengono toccate né ricreate; le pertinenze restano «da collegare». Ogni scheda resta modificabile singolarmente.</p>
        <div data-groups>${gruppi.map(gruppoHtml).join('')}</div>
        <div class="action-bar">
          <select id="gen-add-class" class="input composition-add-class">${((catalog.classes) || []).map((k) => `<option value="${escapeHtml(k.value)}">${escapeHtml(k.label)}</option>`).join('')}</select>
          <button type="button" class="btn ghost btn-small" data-add-group>+ Aggiungi gruppo</button>
        </div>
        ${anteprima ? `<div class="composition-preview" data-preview>
          <h3 class="census-subtitle">Anteprima: ${escapeHtml(anteprima.total)} unità</h3>
          <div class="composition-classes">${righe.map((r) => `<div class="composition-class" data-plan="${escapeHtml(r.value)}"><strong>${escapeHtml(r.label)}</strong>
            <span><small>Dichiarate</small>${escapeHtml(r.declared)}</span><span><small>Presenti</small>${escapeHtml(r.present)}</span>
            <span><small>Da creare</small>${escapeHtml(r.requested)}${r.over ? ` ${renderBadge(`${r.over} oltre le dichiarate`, 'warn')}` : ''}</span></div>`).join('')}</div>
          ${eccedenza ? `<label class="census-check" data-over-confirm><input type="checkbox" id="gen-over"${confermaEccedenza ? ' checked' : ''}> Confermo: creo più unità di quelle dichiarate</label>` : ''}
          ${conflitti.length ? `<div class="census-banner" data-numbering-conflicts><strong>Interni già usati nella stessa posizione</strong><ul>${conflitti.slice(0, 8).map((c) => `<li>${escapeHtml([c.code, c.staircase ? `scala ${c.staircase}` : '', c.floor ? `piano ${c.floor}` : '', `int. ${c.internal_number}`].filter(Boolean).join(' · '))}</li>`).join('')}</ul>
            <label class="census-check"><input type="checkbox" id="gen-similar"${confermaSimili ? ' checked' : ''}> Crea comunque (numerazione provvisoria)</label></div>` : ''}
        </div>` : ''}
        <div class="field-error" data-error role="alert">${escapeHtml(errore)}</div>
        <div class="modal-actions census-sheet-actions">
          <button type="button" class="btn ghost" data-cancel>Annulla</button>
          <button type="button" class="btn${anteprima ? '' : ' primary'}" data-preview-btn>${anteprima ? 'Aggiorna anteprima' : 'Anteprima'}</button>
          ${anteprima ? `<button type="button" class="btn primary" data-confirm-generate>${incerto ? 'Riprova' : `Crea ${escapeHtml(anteprima.total)} unità`}</button>` : ''}
        </div>
      </div>`;
    const $ = (s) => dialogEl.querySelector(s);
    const invalida = () => { if (anteprima && !incerto) { anteprima = null; leggi(); disegna(); } };
    gruppi.forEach((g, i) => {
      ['count', 'floor', 'stair', 'surface', 'start'].forEach((s) => $(`#g${i}-${s}`).addEventListener('change', invalida));
      ['type', 'surface-source', 'cat-source', 'kind'].forEach((s) => $(`#g${i}-${s}`)?.addEventListener('change', () => { leggi(); anteprima = incerto ? anteprima : null; disegna(); }));
      $(`#g${i}-numbering`).addEventListener('change', () => { leggi(); anteprima = incerto ? anteprima : null; disegna(); });
    });
    dialogEl.querySelectorAll('[data-group-category]').forEach((b) => b.addEventListener('click', () => {
      leggi();
      const i = Number(b.dataset.groupCategory);
      openCategoryPicker(childDialog(dialogEl, 'category'), {
        options: opzioni, propertyType: gruppi[i].property_type, current: gruppi[i].cadastral_category,
        onPick: (code) => { gruppi[i].cadastral_category = code || ''; if (!code) gruppi[i].cadastral_category_source = ''; if (!incerto) anteprima = null; disegna(); },
      });
    }));
    dialogEl.querySelectorAll('[data-remove-group]').forEach((b) => b.addEventListener('click', () => {
      leggi(); gruppi.splice(Number(b.dataset.removeGroup), 1); if (!incerto) anteprima = null; disegna();
    }));
    $('[data-add-group]').addEventListener('click', () => { leggi(); gruppi.push(nuovoGruppo($('#gen-add-class').value)); if (!incerto) anteprima = null; disegna(); });
    $('[data-cancel]').addEventListener('click', () => dialogEl.close());
    $('#gen-over')?.addEventListener('change', (ev) => { confermaEccedenza = Boolean(ev && ev.target ? ev.target.checked : $('#gen-over').checked); });
    $('#gen-similar')?.addEventListener('change', (ev) => { confermaSimili = Boolean(ev && ev.target ? ev.target.checked : $('#gen-similar').checked); });
    $('[data-preview-btn]').addEventListener('click', () => { leggi(); prova(true); });
    $('[data-confirm-generate]')?.addEventListener('click', () => {
      if ($('#gen-over')) confermaEccedenza = $('#gen-over').checked;
      if ($('#gen-similar')) confermaSimili = $('#gen-similar').checked;
      prova(false);
    });
  }

  async function prova(soloAnteprima) {
    if (occupato) return;
    const p = generatePayload(gruppi, {
      clientRequestId: chiave, dryRun: soloAnteprima, expected: anteprima ? anteprima.existing : null,
      confirmOver: confermaEccedenza, confirmSimilar: confermaSimili, maxUnits: catalog.max_batch_units || 300,
    });
    if (p.errors.length) { disegna(p.errors.join(' ')); return; }
    occupato = true;
    const bottone = dialogEl.querySelector(soloAnteprima ? '[data-preview-btn]' : '[data-confirm-generate]');
    if (bottone) { bottone.disabled = true; bottone.textContent = soloAnteprima ? 'Anteprima…' : 'Creazione…'; }
    try {
      const esito = await api.generateUnits(buildingId, p.body);
      occupato = false;
      if (soloAnteprima && esito.dry_run) {
        anteprima = esito; incerto = false; disegna();
        const blocco = dialogEl.querySelector('[data-preview]');
        if (blocco && typeof blocco.scrollIntoView === 'function') blocco.scrollIntoView({ block: 'nearest' });
        return;
      }
      dialogEl.close();
      if (onDone) await onDone(esito);
    } catch (error) {
      occupato = false;
      if (error.uncertain) { incerto = true; disegna('Esito non noto: le unità potrebbero essere già state create. «Riprova» non crea doppioni.'); return; }
      incerto = false;
      if (error.code === 'UNITS_CHANGED') { anteprima = null; disegna(errorMessage(error)); return; }
      disegna(errorMessage(error));
    }
  }

  if (!gruppi.length) gruppi.push(nuovoGruppo('residential'));
  disegna();
}

// --- «Modifica selezionate» ------------------------------------------------------------

export function openBulkSheet(dialogEl, { units, data, options: opzioni, buildingId, onDone } = {}) {
  apri(dialogEl);
  const catalog = data.catalog || {};
  const singola = units.length === 1;
  const tuttiRustici = units.every((u) => u.property_type === 'rustic' && u.unit_class !== 'pertinenze');
  let categoria = '';
  dialogEl.innerHTML = `
    <form data-bulk-form novalidate>
      <h2 class="census-sheet-title">Modifica ${escapeHtml(singola ? (units[0].code || 'l’unità') : `${units.length} unità`)}</h2>
      <p class="muted">Solo i campi che compili cambiano; gli altri restano come sono. Proprietari, collegamenti e stato non si toccano da qui.</p>
      <div class="form-grid-2">
        <div class="form-field"><label for="bk-surface">Superficie m²</label><input id="bk-surface" class="input" inputmode="decimal" placeholder="invariata"></div>
        <div class="form-field"><label for="bk-surface-source">Provenienza della superficie</label>${sourceSelectHtml('bk-surface-source', catalog, '', { bulk: true, emptyLabel: 'Invariata' })}</div>
      </div>
      <div class="form-grid-2">
        <div class="form-field"><label>Categoria catastale</label><button type="button" class="btn census-category-btn" data-bulk-category>Invariata ›</button></div>
        <div class="form-field"><label for="bk-cat-source">Provenienza della categoria</label>${sourceSelectHtml('bk-cat-source', catalog, '', { bulk: true, emptyLabel: 'Invariata' })}</div>
      </div>
      <p class="muted" data-verified-hint>«Verificata» si registra dalla scheda della singola unità, con la fonte effettivamente consultata (tipo di documento e data).</p>
      <div class="form-grid-2">
        <div class="form-field"><label for="bk-floor">Piano</label><input id="bk-floor" class="input" maxlength="50" placeholder="invariato"></div>
        <div class="form-field"><label for="bk-stair">Scala</label><input id="bk-stair" class="input" maxlength="10" placeholder="invariata"></div>
      </div>
      ${tuttiRustici ? '<label class="census-check" data-rustic-row><input type="checkbox" id="bk-rustic"> Classifica come abitazione (scelta esplicita)</label>' : ''}
      <p class="muted" data-bulk-warnings></p>
      <div class="field-error" data-error role="alert"></div>
      <div class="modal-actions">
        <button type="button" class="btn ghost" data-cancel>Annulla</button>
        <button type="submit" class="btn primary" data-submit>Applica</button>
      </div>
    </form>`;
  const $ = (s) => dialogEl.querySelector(s);
  const leggi = () => bulkPayload({
    surface_sqm: $('#bk-surface').value, surface_source: $('#bk-surface-source').value,
    cadastral_category: categoria, cadastral_category_source: $('#bk-cat-source').value,
    floor: $('#bk-floor').value, staircase: $('#bk-stair').value,
  }, units.map((u) => u.id), { residentialRustic: Boolean($('#bk-rustic') && $('#bk-rustic').checked) });
  const avvisa = () => { $('[data-bulk-warnings]').textContent = leggi().warnings.join(' '); };
  ['#bk-surface', '#bk-surface-source', '#bk-cat-source'].forEach((s) => $(s).addEventListener('change', avvisa));
  $('#bk-surface').addEventListener('input', avvisa);
  $('[data-bulk-category]').addEventListener('click', () => openCategoryPicker(childDialog(dialogEl, 'category'), {
    options: opzioni, propertyType: units[0].property_type, current: categoria,
    onPick: (code) => { categoria = code || ''; $('[data-bulk-category]').textContent = categoria || 'Invariata ›'; avvisa(); },
  }));
  $('[data-cancel]').addEventListener('click', () => dialogEl.close());
  let occupato = false;
  $('[data-bulk-form]').addEventListener('submit', async (ev) => {
    if (ev && typeof ev.preventDefault === 'function') ev.preventDefault();
    if (occupato) return;
    const p = leggi();

    if (p.errors.length) { $('[data-error]').textContent = p.errors.join(' '); return; }
    occupato = true;
    $('[data-submit]').disabled = true;
    $('[data-error]').textContent = '';
    try {
      const esito = await api.bulkUpdateUnits(buildingId, p.body);
      dialogEl.close();
      if (onDone) await onDone(esito);
    } catch (error) {
      $('[data-error]').textContent = errorMessage(error);
    } finally {
      occupato = false;
      $('[data-submit]').disabled = false;
    }
  });
  avvisa();
}
