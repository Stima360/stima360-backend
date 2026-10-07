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
//
// PERTINENZE-1: «+ Pertinenza» censisce una pertinenza autonoma (subalterno
// proprio) collegandola subito a un'unita' o «da collegare dopo»; il pannello
// «Pertinenze» le mostra dalle relazioni reali, collegate (con la loro
// principale, anche in un altro edificio) e da collegare (con «Collega a…»).

import { apiPost } from '../core/api-client.js';
import { navigate } from '../core/router.js';
import { getSession } from '../core/auth.js';
import { escapeHtml, formatDateTime, renderBadge } from '../components/st-table.js';
import { loadFormOptions } from '../components/property-form.js';
import * as api from '../census/census-api.js';
import { openBuildingSheet, openLinkPrincipalSheet, openUnitSheet, showToast } from '../census/census-sheets.js';
import {
  buildingPertinenze, buildingStreet, buildingTitle, createdToastText, duplicateSeed, errorMessage, groupUnitsByFloor, labelOf,
  principalCandidates, summaryView, unitFacts, unitRelationText, unitRowBadges,
} from '../census/census-model.js';
import { STATUS_LABELS, canManagePropertyLifecycle } from './immobile-dettaglio.js';
// CESTINO-EDIFICI-1: «Elimina…» (foglio condiviso del Cestino) e, per un
// edificio nel Cestino, la scheda in sola lettura con «Ripristina». Le rotte
// le nomina solo trash/trash-api.js; blocchi e permessi li decide il backend.
import { bindBuildingTrashButton, buildingTrashButtonHtml } from '../trash/trash-dialog.js';
import { restoreBuilding } from '../trash/trash-api.js';
import {
  BUILDING_RESTORED_TOAST, buildingDuplicatesView, buildingName, reasonLabel, restoreErrorView,
} from '../trash/trash-model.js';
import { duplicatesNoticeHtml } from './cestino.js';

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
  // CESTINO-EDIFICI-1: nel Cestino l'edificio e' congelato (il backend
  // rifiuta modifica e unita' nuove): scheda consultabile, senza comandi.
  const inTrash = Boolean(edificio.deleted_at);
  // CREAZIONE-GUIDATA-1: arrivando dalla procedura guidata (#/edifici/{id}/
  // aggiungi/crm|censimento) il foglio dell'unita' si apre subito e le unita'
  // nascono del tipo deciso dall'ingresso; altrimenti, come prima, censimento.
  const dallaProcedura = params[1] === 'aggiungi' && !inTrash;
  const modo = dallaProcedura && params[2] === 'crm' ? 'crm' : 'census';
  let ultimaSalvata = null;

  container.innerHTML = `
    ${inTrash ? buildingTrashBannerHtml(edificio) : ''}
    <div class="contact-header card census-building-header">
      <a href="#/edifici" class="muted census-back" id="building-back">← Edifici</a>
      <h2 id="building-title"></h2>
      <div class="muted" id="building-subtitle"></div>
      <dl class="building-facts" id="building-facts"></dl>
      ${inTrash ? '' : `<div class="action-bar">
        <button type="button" class="btn ghost" id="building-edit">Modifica palazzina</button>
        ${buildingTrashButtonHtml()}
      </div>`}
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
      ${inTrash ? '' : `<div class="census-add-bar">
        <button type="button" class="btn primary" id="unit-add-apartment">+ Aggiungi unità</button>
        <button type="button" class="btn" id="unit-add-other">Altro tipo…</button>
        <button type="button" class="btn" id="unit-add-pertinenza">+ Pertinenza</button>
      </div>`}
      ${modo === 'crm' ? '<p class="muted census-kind-note" id="unit-add-mode" data-record-kind="crm">Le unità che aggiungi ora nascono come schede commerciali (procedura da Immobili).</p>' : ''}
      <div id="unit-saved-bar" class="census-banner census-saved-bar" hidden></div>
      <div id="unit-type-chips" class="census-chips census-chips-wrap" hidden></div>
      <div id="building-units"></div>
      <div id="building-archived"></div>
    </div>
    <div class="card panel census-pertinenze-panel" id="building-pertinenze"></div>
    <dialog id="building-sheet" class="modal census-sheet"></dialog>
    <dialog id="unit-sheet" class="modal census-sheet"></dialog>
  `;
  const buildingSheet = container.querySelector('#building-sheet');
  const unitSheet = container.querySelector('#unit-sheet');
  const unitsEl = container.querySelector('#building-units');
  const archivedEl = container.querySelector('#building-archived');
  const tipi = opzioni.property_types || [];
  const kinds = opzioni.accessory_kinds || [];
  const pertinenzeEl = container.querySelector('#building-pertinenze');
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
              <strong>${escapeHtml(u.code || `#${u.id}`)}</strong> <span>${escapeHtml(unitFacts(u, tipi, kinds))}</span>
              <span class="building-unit-meta muted">${escapeHtml([statoUnita(u), relazione].filter(Boolean).join(' · '))}</span>
              <span class="badge-row">${unitRowBadges(u).map((b) => renderBadge(b.text, b.tone)).join(' ')}${u.address_inherited === false ? renderBadge('Ingresso proprio', 'gray') : ''}${archiviata ? renderBadge('Archiviata', 'gray') : ''}</span>
            </a>${azioni}
          </div>`;
  }

  function renderUnits() {
    const gruppi = groupUnitsByFloor(edificio.units || []);
    if (!gruppi.length) {
      unitsEl.innerHTML = inTrash ? '<p class="muted">Nessuna unità: l’edificio è nel Cestino.</p>'
        : '<p class="muted">Nessuna unità censita: comincia con «+ Appartamento».</p>';
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

  // PERTINENZE-1: le pertinenze della palazzina, dalle relazioni reali
  function renderPertinenze() {
    const { linked, unlinked } = buildingPertinenze(edificio.units || []);
    if (!linked.length && !unlinked.length) {
      pertinenzeEl.innerHTML = '<h3 class="section-title">Pertinenze</h3><p class="muted">Nessuna pertinenza autonoma censita. Garage, cantine e posti auto con un loro subalterno si aggiungono con «+ Pertinenza»; quelli compresi restano accessori dell’unità.</p>';
      return;
    }
    const riga = (u, azione) => `
      <li class="census-list-item" data-pertinenza-row="${escapeHtml(u.id)}">
        <a href="#/immobili/${escapeHtml(u.id)}" data-open-unit="${escapeHtml(u.id)}"><strong>${escapeHtml(u.code || `#${u.id}`)}</strong></a>
        <span class="muted">${escapeHtml(unitFacts(u, tipi, kinds))}</span>
        ${azione}
      </li>`;
    pertinenzeEl.innerHTML = `
      <h3 class="section-title">Pertinenze</h3>
      <h4 class="census-subtitle">Collegate a un’unità (${linked.length})</h4>
      ${linked.length ? `<ul class="census-list" data-pertinenze-linked>${linked.map((u) => riga(u, u.parent
        ? `<span class="muted">→ <a href="#/immobili/${escapeHtml(u.parent.id)}" data-open-unit="${escapeHtml(u.parent.id)}">${escapeHtml(u.parent.code || `#${u.parent.id}`)}</a>${u.parent.same_building ? '' : ' (in un altro edificio)'}</span>`
        : '<span class="muted">→ unità principale non disponibile</span>')).join('')}</ul>` : '<p class="muted">Nessuna.</p>'}
      <h4 class="census-subtitle">Da collegare (${unlinked.length})</h4>
      ${unlinked.length ? `<ul class="census-list" data-pertinenze-unlinked>${unlinked.map((u) => riga(u, `<button type="button" class="btn btn-small" data-link-pertinenza="${escapeHtml(u.id)}">Collega a…</button>`)).join('')}</ul>` : '<p class="muted">Nessuna: tutte le pertinenze sono collegate.</p>'}`;
    pertinenzeEl.querySelectorAll('[data-open-unit]').forEach((a) => a.addEventListener('click', (ev) => {
      if (ev && typeof ev.preventDefault === 'function') ev.preventDefault();
      navigate('immobili', [a.dataset.openUnit]);
    }));
    pertinenzeEl.querySelectorAll('[data-link-pertinenza]').forEach((b) => b.addEventListener('click', () => {
      const u = unlinked.find((x) => String(x.id) === b.dataset.linkPertinenza);
      if (!u) return;
      openLinkPrincipalSheet(unitSheet, {
        options: opzioni, property: u, candidates: principalCandidates(edificio.units || [], u.id),
        onLinked: async () => {
          showToast(container, { text: `${u.code || 'Pertinenza'} collegata.` });
          await ricarica();
        },
      });
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
    renderPertinenze();
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

  function apriFoglio(seed, pertinenza = null) {
    openUnitSheet(unitSheet, {
      options: opzioni, building: edificio, seed, lastFloor, recordKind: modo,
      // PERTINENZE-1: pertinenza autonoma, collegata subito o da collegare
      pertinenza: pertinenza ? { kind: pertinenza.kind || '', principals: principalCandidates(edificio.units || []) } : null,
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
        if (another) {
          apriFoglio({ property_type: unita.property_type || seed.property_type || 'apartment', floor: unita.floor || lastFloor, staircase: unita.staircase || '' },
            pertinenza ? { kind: unita.pertinenza_kind || pertinenza.kind || '' } : null);
        }
      },
    });
  }

  container.querySelector('#unit-add-apartment')?.addEventListener('click', () => apriFoglio({ property_type: 'apartment' }));
  container.querySelector('#unit-add-pertinenza')?.addEventListener('click', () => apriFoglio({ property_type: 'garage', floor: lastFloor }, { kind: '' }));
  const chips = container.querySelector('#unit-type-chips');
  chips.innerHTML = tipi.filter((t) => t.value !== 'apartment').map((t) => `<button type="button" class="chip" data-add-type="${escapeHtml(t.value)}">${escapeHtml(t.label)}</button>`).join('');
  container.querySelector('#unit-add-other')?.addEventListener('click', () => { chips.hidden = !chips.hidden; });
  // R3: «Stabile intero» e' la tipologia `building` in palazzina; il flag lo
  // deriva il foglio dalla chip scelta, non il bottone di apertura
  chips.querySelectorAll('[data-add-type]').forEach((b) => b.addEventListener('click', () => { chips.hidden = true; apriFoglio({ property_type: b.dataset.addType }); }));

  if (inTrash) {
    bindBuildingRestore(container, edificio);
  } else {
    // CESTINO-EDIFICI-1: dopo lo spostamento si torna alla lista Edifici
    bindBuildingTrashButton(container, edificio, () => navigate('edifici'));
  }

  container.querySelector('#building-edit')?.addEventListener('click', () => openBuildingSheet(buildingSheet, {
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
  renderPertinenze();
  if (dallaProcedura) {
    // l'indirizzo torna quello della scheda: un ricaricamento non riapre il foglio
    if (window.history && typeof window.history.replaceState === 'function') {
      window.history.replaceState(null, '', `#/edifici/${edificio.id}`);
    }
    apriFoglio({ property_type: 'apartment' });
  }
}

// --- CESTINO-EDIFICI-1: edificio nel Cestino ---------------------------------

function buildingTrashBannerHtml(edificio) {
  const info = edificio.trash || {};
  const chi = info.deleted_by_name ? ` da ${info.deleted_by_name}` : '';
  const data = formatDateTime(info.deleted_at || edificio.deleted_at);
  const motivo = reasonLabel(info.deleted_reason || edificio.deleted_reason) + (info.deleted_note ? ` — ${info.deleted_note}` : '');
  return `
    <div class="card trash-banner" data-building-in-trash role="status">
      <p><strong>Nel Cestino</strong> dal ${escapeHtml(data)}${escapeHtml(chi)}. Motivo: ${escapeHtml(motivo)}.</p>
      <p class="muted">L'edificio non compare nella lista Edifici né nella creazione guidata, non si modifica e non riceve unità. I suoi dati restano.</p>
      <div class="field-error" data-building-restore-error role="alert"></div>
      <div class="trash-banner-actions">
        ${info.can_restore ? '<button type="button" class="btn primary" data-building-restore-btn>Ripristina</button>' : '<span class="muted">Può ripristinarlo chi lo ha spostato o un amministratore.</span>'}
        <a class="btn ghost" href="#/cestino/edifici">Apri il Cestino</a>
      </div>
    </div>`;
}

/** «Ripristina» dalla scheda: poi la scheda si ridisegna (stesso id) e i
 *  possibili doppioni attivi si mostrano con il loro collegamento; nulla si unisce. */
function bindBuildingRestore(container, edificio) {
  const bottone = container.querySelector('[data-building-restore-btn]');
  if (!bottone) return;
  bottone.addEventListener('click', async () => {
    const errore = container.querySelector('[data-building-restore-error]');
    bottone.disabled = true;
    bottone.textContent = 'Ripristino…';
    errore.textContent = '';
    try {
      const riga = await restoreBuilding(edificio.id);
      showToast(container.parentElement || container, { text: BUILDING_RESTORED_TOAST });
      await renderEdificioDettaglio(container, [String(edificio.id)]);
      const avviso = duplicatesNoticeHtml(buildingName(riga), buildingDuplicatesView(riga), { kind: 'building' });
      if (avviso) container.insertAdjacentHTML('afterbegin', avviso);
    } catch (error) {
      errore.textContent = restoreErrorView(error).text;
      bottone.textContent = 'Ripristina';
      bottone.disabled = false;
    }
  });
}
