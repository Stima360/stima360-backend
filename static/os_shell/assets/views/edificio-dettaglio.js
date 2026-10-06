// STIMA360 OS — views/edificio-dettaglio.js (CENSIMENTO-1 Fase 4, S2; EDIFICI-1)
//
// La scheda della palazzina: `#/edifici/{id}` (EDIFICI-1: sezione propria
// nella barra laterale; il vecchio `#/immobili/edifici/{id}` resta valido).
// Header con nome facoltativo, Comune, Microzona, Via, Civico, tipo, stato del
// censimento e scale indicate nelle unita'; i contatori del censimento
// (`census_summary`, regole SOLO nel server: dichiarate / censite / da
// completare, eccedenza segnalata, «non nota» distinta da 0); l'elenco delle
// unita' RAGGRUPPATO PER PIANO con codice, tipologia, scala, piano, interno,
// mq, stato e la relazione reale principale/pertinenza; tocco = scheda
// dell'unita'. Le archiviate restano censite e sono elencate a parte.
// Azioni gia' esistenti del censimento: «+ Appartamento», «Altro tipo…»,
// «Duplica», «Archivia» (solo a chi puo' gestire l'unita': stessa regola del
// backend, che resta l'autorita'), «Modifica palazzina».
//
// Contratti (property/router.py, Fase 3 + EDIFICI-1):
//   GET   /api/property/buildings/{id}        -> edificio + counters + census_summary
//                                                + units[] + archived_units[] + staircases[]
//   PATCH /api/property/buildings/{id}        -> propagated_units / custom_units
//   POST  /api/property/census/units          -> dal foglio «Nuova unita'»
//   POST  /api/property/properties/{id}/undo-create  (toast «Annulla»)
//   POST  /api/property/properties/{id}/archive      -> Archivia (DELETE-ARCH Fase 0)
// Nessun totale di superficie della palazzina (REV 3.1 §0 p.3).

import { apiPost } from '../core/api-client.js';
import { navigate } from '../core/router.js';
import { getSession } from '../core/auth.js';
import { escapeHtml, renderBadge } from '../components/st-table.js';
import { loadFormOptions } from '../components/property-form.js';
import * as api from '../census/census-api.js';
import { openBuildingSheet, openUnitSheet, showToast } from '../census/census-sheets.js';
import {
  buildingStreet, buildingTitle, createdToastText, duplicateSeed, errorMessage, groupUnitsByFloor, labelOf,
  summaryView, unitFacts, unitRelationText, unitRowBadges,
} from '../census/census-model.js';
import { STATUS_LABELS, canManagePropertyLifecycle } from './immobile-dettaglio.js';

export async function renderEdificioDettaglio(container, params = []) {
  const buildingId = params[0];
  if (!buildingId || !/^\d+$/.test(String(buildingId))) {
    container.innerHTML = '<div class="error-box">Identificativo palazzina non valido.</div>';
    return;
  }
  container.innerHTML = '<p class="muted">Caricamento palazzina…</p>';

  let opzioni;
  let edificio;
  try {
    [opzioni, edificio] = await Promise.all([loadFormOptions(), api.getBuilding(buildingId)]);
  } catch (error) {
    container.innerHTML = `<div class="error-box">${escapeHtml(errorMessage(error))}</div>`;
    return;
  }

  let lastFloor = '';
  let archiveConfirm = null;
  // CREAZIONE-GUIDATA-1: arrivando dalla procedura guidata (#/edifici/{id}/
  // aggiungi/crm|censimento) il foglio dell'unita' si apre subito e le unita'
  // nascono del tipo deciso dall'ingresso; altrimenti, come prima, censimento.
  const dallaProcedura = params[1] === 'aggiungi';
  const modo = dallaProcedura && params[2] === 'crm' ? 'crm' : 'census';
  let ultimaSalvata = null;

  container.innerHTML = `
    <div class="contact-header card census-building-header">
      <a href="#/edifici" class="muted census-back" id="building-back">← Edifici</a>
      <h2 id="building-title"></h2>
      <div class="muted" id="building-subtitle"></div>
      <dl class="building-facts" id="building-facts"></dl>
      <div class="action-bar">
        <button type="button" class="btn ghost" id="building-edit">Modifica palazzina</button>
      </div>
    </div>
    <div class="card panel building-summary-panel">
      <div class="building-counts building-counts-large" id="building-counters"></div>
      <p class="muted building-split" id="building-split"></p>
      <div class="badge-row" id="building-signals"></div>
      <details class="census-details building-rules">
        <summary>Come si contano</summary>
        <p class="muted">Dichiarate: le unità catastali che risultano (principali più pertinenze con subalterno proprio); «Non note» se non sono state indicate. Censite: le schede di questo edificio, comprese le archiviate; non contano gli accessori senza subalterno, le unità nel Cestino e quelle annullate subito dopo la creazione. Da completare: dichiarate meno censite, mai sotto zero.</p>
      </details>
    </div>
    <div class="card panel census-units-panel">
      <div class="census-add-bar">
        <button type="button" class="btn primary" id="unit-add-apartment">+ Aggiungi unità</button>
        <button type="button" class="btn" id="unit-add-other">Altro tipo…</button>
      </div>
      ${modo === 'crm' ? '<p class="muted census-kind-note" id="unit-add-mode" data-record-kind="crm">Le unità che aggiungi ora nascono come schede commerciali (procedura da Immobili).</p>' : ''}
      <div id="unit-saved-bar" class="census-banner census-saved-bar" hidden></div>
      <div id="unit-type-chips" class="census-chips census-chips-wrap" hidden></div>
      <div id="building-units"></div>
      <div id="building-archived"></div>
    </div>
    <dialog id="building-sheet" class="modal census-sheet"></dialog>
    <dialog id="unit-sheet" class="modal census-sheet"></dialog>
  `;
  const buildingSheet = container.querySelector('#building-sheet');
  const unitSheet = container.querySelector('#unit-sheet');
  const unitsEl = container.querySelector('#building-units');
  const archivedEl = container.querySelector('#building-archived');
  const tipi = opzioni.property_types || [];
  const sessione = getSession();

  function renderHeader() {
    container.querySelector('#building-title').textContent = buildingTitle(edificio);
    container.querySelector('#building-subtitle').textContent = [
      labelOf(opzioni.building_types, edificio.building_type, edificio.building_type),
      edificio.name ? buildingStreet(edificio) : '',
    ].filter(Boolean).join(' · ');
    const voci = [
      ['Comune', edificio.city], ['Microzona', edificio.microzone], ['Via', edificio.address],
      ['Civico', edificio.civic_number], ['Nome', edificio.name],
      ['Stato censimento', labelOf(opzioni.building_census_statuses, edificio.census_status, edificio.census_status)],
      ['Scale', (edificio.staircases || []).join(', ')],
    ];
    container.querySelector('#building-facts').innerHTML = voci.map(([k, v]) => `<div><dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v && String(v).trim() ? v : '—')}</dd></div>`).join('');
    const v = summaryView(edificio.census_summary);
    const fonte = labelOf(opzioni.units_declared_sources, edificio.census_summary && edificio.census_summary.units_declared_source, '');
    container.querySelector('#building-counters').innerHTML = `
      <span class="building-count"><small>Dichiarate</small><b data-count="declared">${escapeHtml(v.declared)}</b>${fonte && v.declaredKnown ? `<em>${escapeHtml(fonte)}</em>` : ''}</span>
      <span class="building-count"><small>Censite</small><b data-count="counted">${escapeHtml(v.counted)}</b>${v.countedNote ? `<em>${escapeHtml(v.countedNote)}</em>` : ''}</span>
      <span class="building-count"><small>Da completare</small><b data-count="to-complete">${escapeHtml(v.toComplete)}</b>${v.toCompleteNote ? `<em>${escapeHtml(v.toCompleteNote)}</em>` : ''}</span>`;
    container.querySelector('#building-split').textContent = `Attive: ${v.split}`;
    const c = edificio.counters || {};
    const segnali = [];
    if (v.over) segnali.push(renderBadge(v.over, 'warn'));
    if (v.unknownAccessories) segnali.push(renderBadge(v.unknownAccessories, 'warn'));
    if (v.categoryToVerify) segnali.push(renderBadge(v.categoryToVerify, 'gray'));
    if (Number(c.units_address_custom || 0) > 0) segnali.push(renderBadge(`${c.units_address_custom} con ingresso proprio`, 'gray'));
    container.querySelector('#building-signals').innerHTML = segnali.join(' ');
  }

  function statoUnita(u) {
    const testo = STATUS_LABELS[u.commercial_status] || u.commercial_status || '';
    return u.record_kind === 'census' ? `Censimento · ${testo}` : testo;
  }

  function rigaUnita(u, { archiviata = false } = {}) {
    const relazione = unitRelationText(u);
    const puoGestire = canManagePropertyLifecycle(u, sessione);
    const azioni = archiviata ? '' : `
            <div class="census-unit-menu">
              <button type="button" class="btn ghost btn-small" data-duplicate-unit="${escapeHtml(u.id)}">Duplica</button>
              ${puoGestire ? `<button type="button" class="btn ghost btn-small${archiveConfirm === u.id ? ' danger' : ''}" data-archive-unit="${escapeHtml(u.id)}">${archiveConfirm === u.id ? 'Confermi l\'archiviazione?' : 'Archivia'}</button>` : ''}
            </div>`;
    return `
          <div class="census-unit-row" data-unit-id="${escapeHtml(u.id)}">
            <a class="census-unit-main" href="#/immobili/${escapeHtml(u.id)}" data-open-unit="${escapeHtml(u.id)}">
              <strong>${escapeHtml(u.code || `#${u.id}`)}</strong> <span>${escapeHtml(unitFacts(u, tipi))}</span>
              <span class="building-unit-meta muted">${escapeHtml([statoUnita(u), relazione].filter(Boolean).join(' · '))}</span>
              <span class="badge-row">${unitRowBadges(u).map((b) => renderBadge(b.text, b.tone)).join(' ')}${u.address_inherited === false ? renderBadge('Ingresso proprio', 'gray') : ''}${archiviata ? renderBadge('Archiviata', 'gray') : ''}</span>
            </a>${azioni}
          </div>`;
  }

  function renderUnits() {
    const gruppi = groupUnitsByFloor(edificio.units || []);
    if (!gruppi.length) {
      unitsEl.innerHTML = '<p class="muted">Nessuna unità censita: comincia con «+ Appartamento».</p>';
    } else {
      unitsEl.innerHTML = gruppi.map((g) => `
      <div class="census-floor" data-floor="${escapeHtml(g.floor)}">
        <div class="census-floor-head">${escapeHtml(g.label)} <small class="muted">${g.units.length}</small></div>
        ${g.units.map((u) => rigaUnita(u)).join('')}
      </div>`).join('');
    }
    const archiviate = edificio.archived_units || [];
    archivedEl.innerHTML = archiviate.length ? `
      <details class="census-details building-archived">
        <summary>Archiviate (${archiviate.length}) — restano censite</summary>
        ${archiviate.map((u) => rigaUnita(u, { archiviata: true })).join('')}
      </details>` : '';
    // il link apre la scheda dell'unita' (anche in una nuova scheda del
    // browser); il click resta sul router, come prima
    container.querySelectorAll('[data-open-unit]').forEach((a) => a.addEventListener('click', (ev) => {
      if (ev && typeof ev.preventDefault === 'function') ev.preventDefault();
      navigate('immobili', [a.dataset.openUnit]);
    }));
    unitsEl.querySelectorAll('[data-duplicate-unit]').forEach((b) => b.addEventListener('click', () => {
      const u = (edificio.units || []).find((x) => String(x.id) === b.dataset.duplicateUnit);
      if (u) apriFoglio({ ...duplicateSeed(u), duplicate_of: u.code || `#${u.id}` });
    }));
    unitsEl.querySelectorAll('[data-archive-unit]').forEach((b) => b.addEventListener('click', async () => {
      const id = Number(b.dataset.archiveUnit);
      if (archiveConfirm !== id) { archiveConfirm = id; renderUnits(); return; }
      archiveConfirm = null;
      try {
        // DELETE-ARCH Fase 0: azione esplicita di archivio (409 ARCHIVE_BLOCKED
        // con i blocchi; 403 se l'unita' non e' assegnata all'agente).
        await apiPost(`/api/property/properties/${id}/archive`);
      } catch (error) {
        showToast(container, { text: errorMessage(error) });
      }
      await ricarica();
    }));
  }

  async function ricarica() {
    try {
      edificio = await api.getBuilding(edificio.id);
    } catch (error) {
      showToast(container, { text: errorMessage(error) });
      return;
    }
    renderHeader();
    renderUnits();
  }

  // S3 -> S5: il foglio, poi il toast con «Annulla» (undo-create) e la
  // ricarica della palazzina; «Salva e aggiungine un'altra» riapre il foglio
  // con edificio, piano e tipologia gia' impostati.
  // CREAZIONE-GUIDATA-1: dopo il salvataggio, sempre visibili «Aggiungi
  // un'altra unita'» e «Apri scheda» (il toast con «Annulla» resta com'era).
  const barra = container.querySelector('#unit-saved-bar');
  function mostraSalvata() {
    if (!ultimaSalvata) { barra.hidden = true; barra.innerHTML = ''; return; }
    const u = ultimaSalvata;
    barra.innerHTML = `<span data-saved-text>${escapeHtml(`${u.code || `Unità #${u.id}`} salvata${u.record_kind === 'crm' ? ' (scheda commerciale)' : ''}.`)}</span>
      <span class="action-bar"><button type="button" class="btn primary btn-small" data-add-another>Aggiungi un’altra unità</button>
      <a class="btn btn-small" href="#/immobili/${escapeHtml(u.id)}" data-open-saved>Apri scheda</a></span>`;
    barra.hidden = false;
    barra.querySelector('[data-add-another]').addEventListener('click', () => apriFoglio({ property_type: u.property_type || 'apartment', floor: u.floor || lastFloor, staircase: u.staircase || '' }));
    barra.querySelector('[data-open-saved]').addEventListener('click', (ev) => {
      if (ev && typeof ev.preventDefault === 'function') ev.preventDefault();
      navigate('immobili', [u.id]);
    });
  }

  function apriFoglio(seed) {
    openUnitSheet(unitSheet, {
      options: opzioni, building: edificio, seed, lastFloor, recordKind: modo,
      onSaved: async (unita, { another, floor }) => {
        lastFloor = floor || lastFloor;
        ultimaSalvata = unita;
        await ricarica();
        mostraSalvata();
        showToast(container, {
          text: createdToastText(unita), actionLabel: 'Annulla',
          onAction: async () => {
            try {
              await api.undoCreate(unita.id);
              showToast(container, { text: `${unita.code || 'Unità'} annullata.` });
              if (ultimaSalvata && ultimaSalvata.id === unita.id) { ultimaSalvata = null; mostraSalvata(); }
            } catch (error) {
              showToast(container, { text: errorMessage(error), actionLabel: 'Apri la scheda', onAction: () => navigate('immobili', [unita.id]) });
              return;
            }
            await ricarica();
          },
        });
        // R4: la prossima riparte da cio' che e' stato DAVVERO salvato
        // (tipologia, piano, scala della riga creata), non dal seme iniziale
        if (another) apriFoglio({ property_type: unita.property_type || seed.property_type || 'apartment', floor: unita.floor || lastFloor, staircase: unita.staircase || '' });
      },
    });
  }

  container.querySelector('#unit-add-apartment').addEventListener('click', () => apriFoglio({ property_type: 'apartment' }));
  const chips = container.querySelector('#unit-type-chips');
  chips.innerHTML = tipi.filter((t) => t.value !== 'apartment').map((t) => `<button type="button" class="chip" data-add-type="${escapeHtml(t.value)}">${escapeHtml(t.label)}</button>`).join('');
  container.querySelector('#unit-add-other').addEventListener('click', () => { chips.hidden = !chips.hidden; });
  // R3: «Stabile intero» e' la tipologia `building` in palazzina; il flag lo
  // deriva il foglio dalla chip scelta, non il bottone di apertura
  chips.querySelectorAll('[data-add-type]').forEach((b) => b.addEventListener('click', () => { chips.hidden = true; apriFoglio({ property_type: b.dataset.addType }); }));

  container.querySelector('#building-edit').addEventListener('click', () => openBuildingSheet(buildingSheet, {
    options: opzioni, building: edificio,
    onSaved: async (aggiornato) => {
      if (aggiornato && typeof aggiornato.propagated_units === 'number') {
        showToast(container, { text: `Palazzina aggiornata: ${aggiornato.propagated_units} unità con indirizzo ereditato aggiornate, ${aggiornato.custom_units || 0} con ingresso proprio invariate.` });
      }
      await ricarica();
    },
  }));

  renderHeader();
  renderUnits();
  if (dallaProcedura) {
    // l'indirizzo torna quello della scheda: un ricaricamento non riapre il foglio
    if (window.history && typeof window.history.replaceState === 'function') {
      window.history.replaceState(null, '', `#/edifici/${edificio.id}`);
    }
    apriFoglio({ property_type: 'apartment' });
  }
}
