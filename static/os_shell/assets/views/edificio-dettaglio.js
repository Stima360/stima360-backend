// STIMA360 OS — views/edificio-dettaglio.js (CENSIMENTO-1 Fase 4, S2)
//
// La scheda della palazzina: `#/immobili/edifici/{id}` (dentro la rotta
// «immobili», nessuna voce di menu nuova). Header con nome/indirizzo e il
// contatore «Censite N di M dichiarate — X principali + Y pertinenze»; elenco
// unita' RAGGRUPPATO PER PIANO (intestazioni adesive); un solo bottone primario
// «+ Appartamento» e «Altro tipo…» a chips; su ogni riga: tocco = apri la
// scheda immobile, «⋯» = Duplica · Archivia.
//
// Contratti (property/router.py, Fase 3):
//   GET   /api/property/buildings/{id}        -> edificio + counters + units[]
//   PATCH /api/property/buildings/{id}        -> propagated_units / custom_units
//   POST  /api/property/census/units          -> dal foglio «Nuova unita'»
//   POST  /api/property/properties/{id}/undo-create  (toast «Annulla»)
//   DELETE /api/property/properties/{id}      -> Archivia (archiviazione generica)
// Nessun totale di superficie della palazzina (REV 3.1 §0 p.3).

import { apiPost } from '../core/api-client.js';
import { navigate } from '../core/router.js';
import { escapeHtml, renderBadge } from '../components/st-table.js';
import { loadFormOptions } from '../components/property-form.js';
import * as api from '../census/census-api.js';
import { openBuildingSheet, openUnitSheet, showToast } from '../census/census-sheets.js';
import {
  countersText, createdToastText, duplicateSeed, errorMessage, groupUnitsByFloor, labelOf, unitRowBadges, unitRowText,
} from '../census/census-model.js';

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

  container.innerHTML = `
    <div class="contact-header card census-building-header">
      <a href="#/immobili/censimento" class="muted census-back">← Immobili · Censimento</a>
      <h2 id="building-title"></h2>
      <div class="muted" id="building-subtitle"></div>
      <div class="badge-row" id="building-counters"></div>
      <div class="action-bar">
        <button type="button" class="btn ghost" id="building-edit">Modifica palazzina</button>
      </div>
    </div>
    <div class="card panel census-units-panel">
      <div class="census-add-bar">
        <button type="button" class="btn primary" id="unit-add-apartment">+ Appartamento</button>
        <button type="button" class="btn" id="unit-add-other">Altro tipo…</button>
      </div>
      <div id="unit-type-chips" class="census-chips census-chips-wrap" hidden></div>
      <div id="building-units"></div>
    </div>
    <dialog id="building-sheet" class="modal census-sheet"></dialog>
    <dialog id="unit-sheet" class="modal census-sheet"></dialog>
  `;
  const buildingSheet = container.querySelector('#building-sheet');
  const unitSheet = container.querySelector('#unit-sheet');
  const unitsEl = container.querySelector('#building-units');
  const tipi = opzioni.property_types || [];

  function indirizzo(e) {
    const via = [e.address, e.civic_number].filter((x) => x && String(x).trim()).join(' ');
    return [via, e.city].filter(Boolean).join(', ');
  }

  function renderHeader() {
    container.querySelector('#building-title').textContent = edificio.name || indirizzo(edificio) || `Palazzina #${edificio.id}`;
    container.querySelector('#building-subtitle').textContent = [indirizzo(edificio), labelOf(opzioni.building_types, edificio.building_type, edificio.building_type)].filter(Boolean).join(' · ');
    const c = edificio.counters || {};
    const badge = [renderBadge(countersText(c, edificio.units_declared), 'role')];
    if (Number(c.accessories_unknown || 0) > 0) badge.push(renderBadge(`${c.accessories_unknown} da chiarire`, 'warn'));
    if (Number(c.units_address_custom || 0) > 0) badge.push(renderBadge(`${c.units_address_custom} con ingresso proprio`, 'gray'));
    container.querySelector('#building-counters').innerHTML = badge.join(' ');
  }

  function renderUnits() {
    const gruppi = groupUnitsByFloor(edificio.units || []);
    if (!gruppi.length) {
      unitsEl.innerHTML = '<p class="muted">Nessuna unità censita: comincia con «+ Appartamento».</p>';
      return;
    }
    unitsEl.innerHTML = gruppi.map((g) => `
      <div class="census-floor" data-floor="${escapeHtml(g.floor)}">
        <div class="census-floor-head">${escapeHtml(g.label)} <small class="muted">${g.units.length}</small></div>
        ${g.units.map((u) => `
          <div class="census-unit-row" data-unit-id="${escapeHtml(u.id)}">
            <button type="button" class="census-unit-main" data-open-unit="${escapeHtml(u.id)}">
              <strong>${escapeHtml(u.code || `#${u.id}`)}</strong> <span>${escapeHtml(unitRowText(u, tipi))}</span>
              <span class="badge-row">${unitRowBadges(u).map((b) => renderBadge(b.text, b.tone)).join(' ')}${u.address_inherited === false ? renderBadge('Ingresso proprio', 'gray') : ''}</span>
            </button>
            <div class="census-unit-menu">
              <button type="button" class="btn ghost btn-small" data-duplicate-unit="${escapeHtml(u.id)}">Duplica</button>
              <button type="button" class="btn ghost btn-small${archiveConfirm === u.id ? ' danger' : ''}" data-archive-unit="${escapeHtml(u.id)}">${archiveConfirm === u.id ? 'Confermi l\'archiviazione?' : 'Archivia'}</button>
            </div>
          </div>`).join('')}
      </div>`).join('');
    unitsEl.querySelectorAll('[data-open-unit]').forEach((b) => b.addEventListener('click', () => navigate('immobili', [b.dataset.openUnit])));
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
  function apriFoglio(seed) {
    openUnitSheet(unitSheet, {
      options: opzioni, building: edificio, seed, lastFloor,
      onSaved: async (unita, { another, floor }) => {
        lastFloor = floor || lastFloor;
        await ricarica();
        showToast(container, {
          text: createdToastText(unita), actionLabel: 'Annulla',
          onAction: async () => {
            try {
              await api.undoCreate(unita.id);
              showToast(container, { text: `${unita.code || 'Unità'} annullata.` });
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
}
