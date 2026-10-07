// STIMA360 OS — census/site-provenance.js (CATALOGO-CANONICO-1)
//
// Il pannello «Dal sito Stima360» nella tab Censimento/Pertinenze della scheda:
// da quali stime del sito viene la scheda, che cosa hanno dichiarato (valore
// tradotto e valore grezzo), i valori che il catalogo non riconosce («Da
// verificare», mai trasformati in "Altro"), le differenze con i dati corretti
// dall'agente («Applica» / «Ignora») e i possibili doppioni («Collega questa
// stima a IMM-x» / «Non è lo stesso»).
//
// Nessun catalogo qui: le etichette arrivano da form-options (stati, posizione
// e distanza dal mare, tipologie, accessori) e dalla risposta della provenienza
// (etichette dei campi). Se la provenienza non e' installata o la scheda non
// ne ha, il pannello non compare.

import { escapeHtml, renderBadge } from '../components/st-table.js';
import * as censusApi from './census-api.js';
import { errorMessage, labelOf } from './census-model.js';

const ORIGINI = { auto: 'Scheda nata dalla stima', retry: 'Stessa richiesta ripetuta', manual_link: 'Collegata a mano' };
const MOTIVI = { same_submission_data: 'stesso contatto e stessi dati (forse un nuovo invio della stessa stima)',
  same_contact: 'stesso contatto', same_address: 'stesso indirizzo' };

/** Il valore come lo legge l'operatore: Si'/No, etichette di catalogo, m². */
export function siteValueText(campo, valore, opzioni = {}) {
  if (valore === null || valore === undefined || valore === '') return '—';
  if (typeof valore === 'boolean') return valore ? 'Sì' : 'No';
  if (String(campo).startsWith('accessory:')) {
    if (valore && valore.present === false) return 'non più dichiarata';
    const parti = ['presente'];
    if (valore && valore.surface_sqm != null) parti.push(`${valore.surface_sqm} m²`);
    if (valore && valore.quantity != null) parti.push(`× ${valore.quantity}`);
    return parti.join(', ');
  }
  const liste = {
    condition: opzioni.conditions, sea_position: opzioni.sea_positions, sea_distance: opzioni.sea_distances,
    property_type: opzioni.property_types,
  };
  if (liste[campo]) return labelOf(liste[campo], valore, String(valore));
  if (campo === 'surface_sqm') return `${valore} m²`;
  if (campo === 'condo_fees') return `€ ${valore}`;
  return String(valore);
}

/** L'etichetta di un campo (o di una pertinenza, `accessory:<kind>`). */
export function siteFieldLabel(campo, etichette = {}, opzioni = {}) {
  if (String(campo).startsWith('accessory:')) {
    const kind = campo.slice('accessory:'.length);
    return `Pertinenza: ${labelOf(opzioni.accessory_kinds, kind, kind)}`;
  }
  return etichette[campo] || campo;
}

/** I valori dichiarati da mostrare: campi della scheda e pertinenze. */
export function declaredRows(dichiarato = {}, etichette = {}, opzioni = {}) {
  const righe = [];
  for (const [campo, voce] of Object.entries(dichiarato || {})) {
    if (!voce || typeof voce !== 'object' || !('value' in voce) || !etichette[campo]) continue;
    const testo = siteValueText(campo, voce.value, opzioni);
    const grezzo = voce.raw === null || voce.raw === undefined ? '' : String(voce.raw);
    righe.push({ label: etichette[campo], text: testo, raw: grezzo && grezzo !== String(voce.value) && grezzo !== testo ? grezzo : '' });
  }
  for (const [kind, voce] of Object.entries((dichiarato || {}).accessories || {})) {
    righe.push({ label: `Pertinenza: ${labelOf(opzioni.accessory_kinds, kind, kind)}`,
      text: siteValueText(`accessory:${kind}`, { present: true, ...voce }, opzioni),
      raw: voce && voce.raw ? String(voce.raw) : '' });
  }
  return righe;
}

function dataBreve(valore) {
  if (!valore) return '';
  const d = new Date(valore);
  return Number.isNaN(d.getTime()) ? String(valore) : d.toLocaleDateString('it-IT');
}

function voceHtml(v, ctx) {
  const { etichette, opzioni, canManage } = ctx;
  const attiva = v.status === 'active';
  const aperti = (v.conflicts || []).filter((c) => c.status === 'open');
  const doppioni = (v.duplicates || []).filter((d) => !d.dismissed);
  const righe = declaredRows(v.declared, etichette, opzioni);
  const precompilati = ((v.declared || {}).prefilled_unchanged || []).map((c) => siteFieldLabel(c, etichette, opzioni));
  return `
    <div class="site-source${attiva ? '' : ' site-source-closed'}" data-site-source="${escapeHtml(v.id)}">
      <div class="site-source-head"><strong>Stima n. ${escapeHtml(v.stima_id)}</strong>
        <span class="muted">${escapeHtml([dataBreve(v.stima_at || v.created_at), ORIGINI[v.origin] || v.origin,
          (v.detail_ids || []).length ? `+ stima dettagliata${v.detail_ids.length > 1 ? ` (${v.detail_ids.length})` : ''}` : ''].filter(Boolean).join(' · '))}</span>
        ${attiva ? '' : renderBadge('Spostata', 'gray')}
        ${(v.unmapped || []).length ? renderBadge(`${v.unmapped.length} da verificare`, 'warn') : ''}
        ${aperti.length ? renderBadge(`${aperti.length} differenz${aperti.length === 1 ? 'a' : 'e'}`, 'warn') : ''}</div>
      ${!attiva && v.relinked_to ? `<p class="muted">Collegata a <a href="#/immobili/${escapeHtml(v.relinked_to.id)}">${escapeHtml(v.relinked_to.code || `#${v.relinked_to.id}`)}</a>: questa scheda non riceve più i dati di questa stima. Se non serve, puoi spostarla nel Cestino.</p>` : ''}
      ${aperti.length ? `<div class="site-conflicts"><p class="muted">Il sito dice altro rispetto a un dato corretto a mano: decidi tu.</p><ul class="census-list">${aperti.map((c) => `
        <li class="census-list-item" data-conflict="${escapeHtml(c.id)}"><strong>${escapeHtml(siteFieldLabel(c.field, etichette, opzioni))}</strong>
          <span class="muted">sito: ${escapeHtml(siteValueText(c.field, c.site_value, opzioni))} · scheda: ${escapeHtml(siteValueText(c.field, c.current_value, opzioni))}</span>
          ${canManage ? `<button type="button" class="btn btn-small" data-conflict-apply="${escapeHtml(c.id)}">Applica</button><button type="button" class="btn ghost btn-small" data-conflict-ignore="${escapeHtml(c.id)}">Ignora</button>` : ''}</li>`).join('')}</ul></div>` : ''}
      ${(v.unmapped || []).length ? `<div class="site-unmapped"><strong>Da verificare</strong> <span class="muted">(valori del sito che il catalogo non riconosce: conservati, nessun dato inventato)</span><ul class="census-list">${v.unmapped.map((u) => `
        <li class="census-list-item">${renderBadge('Da verificare', 'warn')} <strong>${escapeHtml(u.site_field)}</strong> <span>${escapeHtml(u.raw === null || u.raw === undefined ? '(non indicato)' : String(u.raw))}</span> <span class="muted">${escapeHtml(u.reason || '')}</span></li>`).join('')}</ul></div>` : ''}
      ${attiva && doppioni.length ? `<div class="site-duplicates"><strong>Possibile doppione</strong><ul class="census-list">${doppioni.map((d) => `
        <li class="census-list-item" data-duplicate="${escapeHtml(d.property_id)}"><a href="#/immobili/${escapeHtml(d.property_id)}">${escapeHtml(d.code || `#${d.property_id}`)}</a>
          <span class="muted">${escapeHtml((d.reasons || []).map((r) => MOTIVI[r] || r).join(', '))}</span>
          ${canManage ? `<button type="button" class="btn btn-small" data-relink-to="${escapeHtml(d.property_id)}">Collega questa stima a ${escapeHtml(d.code || `#${d.property_id}`)}</button><button type="button" class="btn ghost btn-small" data-dismiss="${escapeHtml(d.property_id)}">Non è lo stesso</button>` : ''}</li>`).join('')}</ul></div>` : ''}
      ${precompilati.length ? `<p class="muted site-prefilled">Lasciati come il sito li aveva precompilati (non sono una nuova dichiarazione del cliente): ${escapeHtml(precompilati.join(', '))}.</p>` : ''}
      ${righe.length ? `<details class="site-declared"><summary>Valori dichiarati (${righe.length})</summary><div class="detail-grid">${righe.map((r) => `
        <div class="detail-item"><label>${escapeHtml(r.label)}</label>${escapeHtml(r.text)}${r.raw ? ` <small class="muted">sito: «${escapeHtml(r.raw)}»</small>` : ''}</div>`).join('')}</div></details>` : ''}
      ${attiva && canManage ? `<div class="site-relink"><label for="site-relink-${escapeHtml(v.id)}" class="muted">Collega questa stima a un altro immobile</label>
        <div class="action-bar"><input id="site-relink-${escapeHtml(v.id)}" class="input" placeholder="Codice, es. IMM-12" maxlength="50" data-relink-code>
        <button type="button" class="btn ghost btn-small" data-relink-code-btn>Collega</button></div></div>` : ''}
      <div class="field-error" data-site-error></div>
    </div>`;
}

/**
 * @param {HTMLElement} el     il contenitore del pannello
 * @param {{property: object, options: object, onChanged?: Function, navigate?: Function}} ctx
 */
export async function renderSiteProvenance(el, { property, options = {}, onChanged, navigate } = {}) {
  if (!el) return;
  let dati;
  try {
    dati = await censusApi.getSiteSources(property.id);
  } catch (_error) {
    el.innerHTML = '';                // provenienza non disponibile: la tab resta com'era
    return;
  }
  const voci = (dati && dati.items) || [];
  if (!dati || dati.installed === false || !voci.length) { el.innerHTML = ''; return; }
  const ctx = { etichette: dati.labels || {}, opzioni: options, canManage: dati.can_manage === true };
  el.innerHTML = `
    <section class="site-provenance" id="site-provenance">
      <h3 class="section-title">Dal sito Stima360</h3>
      <p class="muted">I valori arrivano da ciò che il cliente ha dichiarato sul sito. Un dato corretto a mano non viene mai sovrascritto.</p>
      ${voci.map((v) => voceHtml(v, ctx)).join('')}
    </section>`;

  const ridisegna = async (cambiata) => {
    if (cambiata && typeof onChanged === 'function') { await onChanged(); return; }
    await renderSiteProvenance(el, { property, options, onChanged, navigate });
  };
  const errore = (box, error) => {
    const f = box && box.querySelector('[data-site-error]');
    if (f) f.textContent = siteErrorMessage(error);
  };
  el.querySelectorAll('[data-site-source]').forEach((box) => {
    const sourceId = Number(box.dataset.siteSource);
    let busy = false;
    const esegui = async (azione, cambiaScheda) => {
      if (busy) return;
      busy = true;
      try { await azione(); await ridisegna(cambiaScheda); } catch (error) { errore(box, error); } finally { busy = false; }
    };
    box.querySelectorAll('[data-conflict-apply]').forEach((b) => b.addEventListener('click', () => esegui(
      () => censusApi.resolveSiteConflict(property.id, sourceId, b.dataset.conflictApply, 'apply'), true)));
    box.querySelectorAll('[data-conflict-ignore]').forEach((b) => b.addEventListener('click', () => esegui(
      () => censusApi.resolveSiteConflict(property.id, sourceId, b.dataset.conflictIgnore, 'ignore'), false)));
    box.querySelectorAll('[data-dismiss]').forEach((b) => b.addEventListener('click', () => esegui(
      () => censusApi.dismissSiteDuplicate(property.id, sourceId, Number(b.dataset.dismiss)), false)));
    const collega = (target, bottone, testo) => {
      // due clic: lo spostamento cambia la scheda che riceve i dati della stima
      if (bottone.dataset.confirm !== '1') { bottone.dataset.confirm = '1'; bottone.textContent = testo; return; }
      esegui(async () => {
        const esito = await censusApi.relinkSiteSource(property.id, sourceId, target);
        if (typeof navigate === 'function' && esito && esito.property_id) navigate('immobili', [esito.property_id, 'censimento']);
      }, true);
    };
    box.querySelectorAll('[data-relink-to]').forEach((b) => b.addEventListener('click', () => collega(
      { target_property_id: Number(b.dataset.relinkTo) }, b, 'Confermi? La stima passa all\'altra scheda')));
    const codice = box.querySelector('[data-relink-code]');
    const bottone = box.querySelector('[data-relink-code-btn]');
    if (codice && bottone) bottone.addEventListener('click', () => {
      const valore = codice.value.trim();
      if (!valore) { errore(box, { detail: 'Scrivi il codice dell\'immobile (es. IMM-12).' }); return; }
      collega({ target_code: valore }, bottone, `Confermi il collegamento a ${valore}?`);
    });
  });
}

export function siteErrorMessage(error) {
  const code = error && error.code ? error.code : '';
  if (code === 'SELLER_LINK_ACTIVE') return 'La stima è già un’opportunità Venditore su questa scheda: chiudila o sospendila prima.';
  if (code === 'SOURCE_ALREADY_RELINKED') return 'Questa stima è già collegata a un altro immobile.';
  if (code === 'CONFLICT_ALREADY_RESOLVED') return 'Questa differenza è già stata gestita.';
  if (code === 'SITE_SYNC_NOT_INSTALLED') return 'La provenienza dal sito non è disponibile su questo ambiente.';
  if (code === 'FORBIDDEN') return 'Solo chi gestisce l’immobile (titolare, amministratore o agente assegnato) può farlo.';
  return errorMessage(error);
}
