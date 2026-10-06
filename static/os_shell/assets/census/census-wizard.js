// STIMA360 OS — census/census-wizard.js (CREAZIONE-GUIDATA-1)
//
// La procedura guidata per aggiungere o censire un immobile, partendo dal
// territorio e dall'edificio (Comune -> Microzona -> Edificio -> Unita'):
//
//   1  Dove: Comune (catalogo territoriale; regione e provincia dal catalogo),
//      Microzona del Comune, Via, Civico, quante unita' ha l'edificio (o «Non
//      so»: mai 0 o 1 per difetto), nome della palazzina facoltativo.
//   2  Cosa: «Unita' autonoma» (nessun edificio) · «Palazzina con piu' unita'»
//      · «Una sola unita' in una palazzina». Il percorso lo sceglie
//      l'operatore: «1» non vuol dire autonoma.
//   3  Edificio: candidati della stessa agenzia sulla stessa via (stesso civico
//      prima) -> «Usa questo edificio» (nessuna scrittura, nessuna modifica)
//      oppure «Crea un edificio nuovo/distinto».
//   4  Conferma: «Salva edificio» e' L'UNICO punto che salva l'edificio
//      (`client_request_id` riusato nei ritentativi: mai due edifici).
//   5  Pronto: «Inserisci le unita'» apre la scheda dell'edificio con il
//      foglio dell'unita' gia' aperto; «Fine per ora» lascia l'edificio vuoto.
//
// Nessuna scrittura navigando fra i passaggi; «Indietro» conserva tutto;
// «Annulla» chiude e non cancella nulla di gia' salvato. Il tipo di scheda
// lo decide l'ingresso (`recordKind`): dall'elenco Commerciale le unita'
// nascono commerciali, dal Censimento e da Edifici di censimento.
//
// Contratti (nessuna rotta nuova):
//   GET  /api/property/buildings?city=&search=&sort=address  (candidati)
//   POST /api/property/buildings                            (solo al passo 4)
//   la scheda singola: components/property-form.js (commerciale) oppure
//   census-sheets.js::openUnitSheet (censimento), con il territorio gia' scelto.

import { navigate } from '../core/router.js';
import { escapeHtml, renderBadge } from '../components/st-table.js';
import { openPropertyDialog } from '../components/property-form.js';
import * as api from './census-api.js';
import { openUnitSheet } from './census-sheets.js';
import {
  buildingPlace, buildingStreet, buildingTitle, candidateSearch, catalogMunicipalities, declaredMismatchText,
  errorMessage, rankCandidates, summaryView, wizardBuildingPayload, wizardDeclared, wizardInitialState,
  wizardLocation, wizardStep1Errors,
} from './census-model.js';

const str = (v) => (v === null || v === undefined ? '' : String(v));

export const DECLARED_HINT = 'Conta le unità principali e le pertinenze con subalterno proprio (box, cantine accatastate); non contare gli accessori senza sub. È il totale dell’edificio, non quante ne inserisci oggi.';

const PERCORSI = [
  { value: 'autonomous', title: 'Unità autonoma', text: 'Villa, casa indipendente, terreno, negozio o altra unità senza palazzina.' },
  { value: 'building', title: 'Palazzina con più unità', text: 'Scegli o crea l’edificio, poi inserisci le unità una dopo l’altra.' },
  { value: 'single', title: 'Una sola unità in una palazzina', text: 'Collegala a un edificio esistente o crea il contenitore, anche se non sai quante unità ha.' },
];

/**
 * @param {{wizardDialog: HTMLDialogElement, formDialog?: HTMLDialogElement, options: object,
 *          recordKind?: 'crm'|'census', state?: object}} opts
 *   options = GET /api/property/form-options (catalogo, etichette, agenti)
 *   formDialog = il dialog del form commerciale (scheda singola `crm`)
 */
export function startCreation({ wizardDialog, formDialog = null, options: opzioni, recordKind = 'census', state = null } = {}) {
  const comuni = catalogMunicipalities(opzioni.territory || []);
  const stato = state || wizardInitialState();
  const commerciale = recordKind === 'crm';
  let occupato = false;
  let richiestaEdificio = null;            // chiave del POST dell'edificio, riusata nei ritentativi
  let candidatiMostrati = 0;

  function apri() {
    wizardDialog.className = 'modal census-sheet census-wizard';
    if (!wizardDialog._open && !wizardDialog.open) wizardDialog.showModal();
  }

  function testata(passo, titolo) {
    return `<p class="census-wizard-step muted">Passo ${passo} di 4 · ${commerciale ? 'scheda commerciale' : 'censimento'}</p>
      <h2 class="census-sheet-title">${escapeHtml(titolo)}</h2>`;
  }

  function riepilogoLuogo() {
    const loc = wizardLocation(stato, comuni);
    const via = [loc.address, loc.civic_number].filter(Boolean).join(' ');
    return [via, [loc.city, loc.microzone].filter(Boolean).join(' · ')].filter(Boolean).join(', ');
  }

  // --- 1. Dove ---------------------------------------------------------------------
  function passo1() {
    apri();
    const zone = (comuni.find((c) => c.name === stato.city) || { microzones: [] }).microzones;
    wizardDialog.innerHTML = `
      <form data-wizard-step="1" novalidate>
        ${testata(1, 'Dove si trova?')}
        <div class="form-grid-2">
          <div class="form-field"><label for="wz-city">Comune</label><select id="wz-city" class="input">
            <option value="">Scegli il Comune</option>
            ${comuni.map((c) => `<option value="${escapeHtml(c.name)}"${c.name === stato.city ? ' selected' : ''}>${escapeHtml(`${c.name} (${c.province})`)}</option>`).join('')}
          </select></div>
          <div class="form-field"><label for="wz-microzone">Microzona</label><select id="wz-microzone" class="input"${stato.city ? '' : ' disabled'}>
            <option value="">${stato.city ? 'Microzona (facoltativa)' : 'Scegli prima il Comune'}</option>
            ${zone.map((z) => `<option value="${escapeHtml(z)}"${z === stato.microzone ? ' selected' : ''}>${escapeHtml(z)}</option>`).join('')}
          </select></div>
        </div>
        <div class="form-grid-2">
          <div class="form-field"><label for="wz-address">Via</label><input id="wz-address" class="input" maxlength="250" value="${escapeHtml(stato.address)}" autocomplete="off"></div>
          <div class="form-field"><label for="wz-civic">Civico</label><input id="wz-civic" class="input" maxlength="30" value="${escapeHtml(stato.civic_number)}" autocomplete="off"></div>
        </div>
        <div class="form-field"><label for="wz-declared">Quante unità immobiliari ci sono nell’edificio?</label>
          <div class="census-inline"><input id="wz-declared" class="input" type="number" inputmode="numeric" min="0" step="1" value="${escapeHtml(stato.units_declared)}"${stato.units_unknown ? ' disabled' : ''}>
          <label class="census-check"><input type="checkbox" id="wz-unknown"${stato.units_unknown ? ' checked' : ''}> Non so</label></div>
          <small class="muted">${escapeHtml(DECLARED_HINT)}</small></div>
        <div class="form-field"><label for="wz-name">Nome della palazzina (facoltativo)</label><input id="wz-name" class="input" maxlength="120" value="${escapeHtml(stato.building_name)}" placeholder="es. Residenza Gabbiano"></div>
        <p class="muted census-wizard-note">Nulla viene salvato finché non lo confermi.</p>
        <div class="field-error" data-error></div>
        <div class="modal-actions">
          <button type="button" class="btn ghost" data-cancel>Annulla</button>
          <button type="submit" class="btn primary" data-next>Avanti</button>
        </div>
      </form>`;
    const $ = (sel) => wizardDialog.querySelector(sel);
    const leggi = () => {
      stato.city = $('#wz-city').value;
      stato.microzone = $('#wz-microzone').value;
      stato.address = str($('#wz-address').value).trim();
      stato.civic_number = str($('#wz-civic').value).trim();
      stato.units_declared = str($('#wz-declared').value).trim();
      stato.units_unknown = $('#wz-unknown').checked;
      stato.building_name = str($('#wz-name').value).trim();
    };
    $('#wz-city').addEventListener('change', () => { leggi(); stato.microzone = ''; passo1(); });
    $('#wz-unknown').addEventListener('change', () => { leggi(); $('#wz-declared').disabled = stato.units_unknown; });
    $('[data-cancel]').addEventListener('click', () => wizardDialog.close());
    $('[data-wizard-step]').addEventListener('submit', (ev) => {
      if (ev && typeof ev.preventDefault === 'function') ev.preventDefault();
      leggi();
      const errori = wizardStep1Errors(stato, comuni);
      if (errori.length) { $('[data-error]').textContent = errori.join(' '); return; }
      passo2();
    });
  }

  // --- 2. Cosa ---------------------------------------------------------------------
  function passo2() {
    apri();
    wizardDialog.innerHTML = `
      <div data-wizard-step="2">
        ${testata(2, 'Che cosa stai inserendo?')}
        <p class="muted">${escapeHtml(riepilogoLuogo())}${stato.units_unknown ? ' · unità dell’edificio: non note' : ` · ${escapeHtml(stato.units_declared)} unità nell’edificio`}</p>
        <div class="census-new-cards census-wizard-paths">
          ${PERCORSI.map((p) => `<button type="button" class="census-card${stato.path === p.value ? ' active' : ''}" data-path="${p.value}"><strong>${escapeHtml(p.title)}</strong><span class="muted">${escapeHtml(p.text)}</span></button>`).join('')}
        </div>
        <div class="modal-actions">
          <button type="button" class="btn ghost" data-back>← Indietro</button>
          <button type="button" class="btn ghost" data-cancel>Annulla</button>
        </div>
      </div>`;
    wizardDialog.querySelector('[data-back]').addEventListener('click', passo1);
    wizardDialog.querySelector('[data-cancel]').addEventListener('click', () => wizardDialog.close());
    wizardDialog.querySelectorAll('[data-path]').forEach((b) => b.addEventListener('click', () => {
      stato.path = b.dataset.path;
      if (stato.path === 'autonomous') schedaAutonoma();
      else passo3();
    }));
  }

  // --- 2a. Unita' autonoma: la scheda singola, territorio gia' compilato -----------------
  function schedaAutonoma() {
    const seme = wizardLocation(stato, comuni);
    const torna = () => passo2();
    if (commerciale && formDialog) {
      wizardDialog.close();
      openPropertyDialog(formDialog, {
        mode: 'create', seed: seme, onBack: torna,
        onSaved: (creato) => navigate('immobili', [creato.id]),
      });
      return;
    }
    openUnitSheet(wizardDialog, {
      options: opzioni, seed: seme, recordKind: commerciale ? 'crm' : 'census', onBack: torna,
      onSaved: (unita) => navigate('immobili', [unita.id]),
    });
  }

  // --- 3. Edificio: candidati ------------------------------------------------------------
  function passo3() {
    apri();
    if (stato.building) { passo5(); return; }
    if (stato.search === null) stato.search = candidateSearch(stato.address);
    wizardDialog.innerHTML = `
      <div data-wizard-step="3">
        ${testata(3, 'In quale edificio?')}
        <p class="muted">Edifici già presenti in ${escapeHtml(stato.city)}: se è uno di questi usalo, altrimenti crealo. La stessa via è solo un suggerimento: nulla viene collegato da solo.</p>
        <div class="form-field"><label for="wz-building-search">Cerca per via, civico o nome</label>
          <input id="wz-building-search" class="input" type="search" value="${escapeHtml(stato.search)}" autocomplete="off"></div>
        <div data-candidates aria-live="polite"><p class="muted">Ricerca…</p></div>
        <div class="modal-actions">
          <button type="button" class="btn ghost" data-back>← Indietro</button>
          <button type="button" class="btn" data-create-building>Crea un edificio nuovo</button>
        </div>
      </div>`;
    const area = wizardDialog.querySelector('[data-candidates]');
    const crea = wizardDialog.querySelector('[data-create-building]');
    wizardDialog.querySelector('[data-back]').addEventListener('click', passo2);
    crea.addEventListener('click', passo4);
    let attesa = null;
    let giro = 0;
    async function cerca() {
      const mio = ++giro;
      area.innerHTML = '<p class="muted">Ricerca…</p>';
      let corpo;
      try {
        corpo = await api.listBuildings({ city: stato.city, search: stato.search || undefined, sort: 'address', limit: 10 });
      } catch (error) {
        if (mio !== giro) return;
        area.innerHTML = `<div class="error-box">${escapeHtml(errorMessage(error))} <button type="button" class="btn ghost btn-small" data-retry>Riprova</button></div>`;
        area.querySelector('[data-retry]').addEventListener('click', cerca);
        return;
      }
      if (mio !== giro) return;
      const candidati = rankCandidates(corpo && corpo.items, stato.civic_number);
      candidatiMostrati = candidati.length;
      crea.textContent = candidati.length ? 'Nessuno di questi: crea un edificio distinto' : 'Crea un edificio nuovo';
      if (!candidati.length) {
        area.innerHTML = '<p class="muted">Nessun edificio trovato con questa ricerca in questo Comune.</p>';
        return;
      }
      area.innerHTML = `<ul class="census-list census-candidates">${candidati.map((b) => {
        const v = summaryView(b.census_summary);
        return `<li class="census-list-item" data-candidate="${escapeHtml(b.id)}">
          <div class="census-candidate-text"><strong>${escapeHtml(buildingTitle(b))}</strong>
            <small class="muted">${escapeHtml([b.name ? buildingStreet(b) : '', buildingPlace(b)].filter(Boolean).join(' · '))}</small>
            <small class="muted">Dichiarate ${escapeHtml(v.declared)} · Censite ${escapeHtml(v.counted)}</small>
            ${b.same_civic ? renderBadge('Stesso civico', 'warn') : ''}</div>
          <button type="button" class="btn primary btn-small" data-use-building="${escapeHtml(b.id)}">Usa questo edificio</button></li>`;
      }).join('')}</ul>`;
      area.querySelectorAll('[data-use-building]').forEach((btn) => btn.addEventListener('click', () => {
        const scelto = candidati.find((b) => String(b.id) === btn.dataset.useBuilding);
        stato.building = scelto;
        stato.building_saved = false;
        passo5();
      }));
    }
    wizardDialog.querySelector('#wz-building-search').addEventListener('input', (ev) => {
      stato.search = str(ev && ev.target ? ev.target.value : wizardDialog.querySelector('#wz-building-search').value).trim();
      clearTimeout(attesa);
      attesa = setTimeout(cerca, 300);
    });
    cerca();
  }

  // --- 4. Conferma e salvataggio dell'edificio -------------------------------------------
  function passo4() {
    apri();
    if (!richiestaEdificio) richiestaEdificio = api.newClientRequestId();
    const loc = wizardLocation(stato, comuni);
    const d = wizardDeclared(stato);
    const righe = [['Comune', loc.city], ['Microzona', loc.microzone], ['Via', loc.address], ['Civico', loc.civic_number],
      ['Nome', stato.building_name], ['Unità dichiarate', d.value === null ? 'Non note' : String(d.value)]];
    wizardDialog.innerHTML = `
      <div data-wizard-step="4">
        ${testata(4, 'Salvo il nuovo edificio?')}
        <dl class="building-facts">${righe.map(([k, val]) => `<div><dt>${escapeHtml(k)}</dt><dd>${escapeHtml(val || '—')}</dd></div>`).join('')}</dl>
        <p class="muted">Viene salvato solo l’edificio. Le unità le inserisci subito dopo, una alla volta; un edificio può anche restare vuoto.</p>
        <div data-banner></div>
        <div class="field-error" data-error></div>
        <div class="modal-actions">
          <button type="button" class="btn ghost" data-back>← Indietro</button>
          <button type="button" class="btn primary" data-save-building>Salva edificio</button>
        </div>
      </div>`;
    const salvaBtn = wizardDialog.querySelector('[data-save-building]');
    wizardDialog.querySelector('[data-back]').addEventListener('click', () => { if (!occupato) passo3(); });
    async function salva(confermaSimili) {
      if (occupato) return;                       // doppio clic: una sola richiesta
      occupato = true;
      salvaBtn.disabled = true;
      salvaBtn.textContent = 'Salvataggio…';
      wizardDialog.querySelector('[data-error]').textContent = '';
      let esito;
      try {
        esito = await api.createBuilding(wizardBuildingPayload(stato, comuni, {
          clientRequestId: richiestaEdificio, confirmSimilar: confermaSimili || candidatiMostrati > 0,
        }));
      } catch (error) {
        occupato = false;
        salvaBtn.disabled = false;
        salvaBtn.textContent = error.uncertain ? 'Riprova' : 'Salva edificio';
        if (error.code === 'SIMILAR_FOUND' && error.similar.length) {
          const banner = wizardDialog.querySelector('[data-banner]');
          banner.innerHTML = `<div class="census-banner" data-similar-banner><strong>Esiste già un edificio con questi dati</strong>
            <ul>${error.similar.slice(0, 5).map((s) => `<li>${escapeHtml(buildingTitle(s))} <small class="muted">${escapeHtml(buildingStreet(s))}</small> <button type="button" class="btn ghost btn-small" data-use-similar="${escapeHtml(s.id)}">Usa questo</button></li>`).join('')}</ul>
            <div class="action-bar"><button type="button" class="btn" data-save-anyway>Crea comunque un edificio distinto</button></div></div>`;
          banner.querySelectorAll('[data-use-similar]').forEach((b) => b.addEventListener('click', () => {
            stato.building = error.similar.find((s) => String(s.id) === b.dataset.useSimilar);
            stato.building_saved = false;
            passo5();
          }));
          banner.querySelector('[data-save-anyway]').addEventListener('click', () => salva(true));
          return;
        }
        wizardDialog.querySelector('[data-error]').textContent = error.uncertain
          ? 'Esito non noto: l’edificio potrebbe essere già salvato. «Riprova» non ne crea un secondo.'
          : errorMessage(error);
        return;
      }
      occupato = false;
      stato.building = esito;
      stato.building_saved = true;
      passo5();
    }
    salvaBtn.addEventListener('click', () => salva(false));
  }

  // --- 5. Edificio pronto -----------------------------------------------------------------
  function passo5() {
    apri();
    const b = stato.building;
    const v = summaryView(b.census_summary);
    const differenza = stato.building_saved ? '' : declaredMismatchText(stato, b);
    wizardDialog.innerHTML = `
      <div data-wizard-step="5">
        ${testata(4, stato.building_saved ? 'Edificio salvato' : 'Edificio scelto')}
        <div class="census-banner" data-building-ready><strong>${escapeHtml(buildingTitle(b))}</strong>
          <div class="muted">${escapeHtml([b.name ? buildingStreet(b) : '', buildingPlace(b)].filter(Boolean).join(' · '))}</div>
          <div class="muted">Dichiarate ${escapeHtml(v.declared)} · Censite ${escapeHtml(v.counted)}</div>
          <div>${stato.building_saved ? 'Salvato ora, ancora senza unità.' : 'Edificio esistente: nessun dato modificato.'}</div></div>
        ${differenza ? `<p class="muted" data-declared-mismatch>${escapeHtml(differenza)}</p>` : ''}
        <p class="muted">Ora le unità: ${commerciale ? 'nasceranno come schede commerciali' : 'nasceranno come schede di censimento'}, con l’indirizzo dell’edificio.</p>
        <div class="modal-actions">
          ${stato.building_saved ? '' : '<button type="button" class="btn ghost" data-change-building>← Scegli un altro edificio</button>'}
          <button type="button" class="btn ghost" data-finish>Fine per ora</button>
          <button type="button" class="btn primary" data-add-units>${stato.path === 'single' ? 'Inserisci l’unità' : 'Inserisci le unità'}</button>
        </div>
      </div>`;
    const cambia = wizardDialog.querySelector('[data-change-building]');
    if (cambia) cambia.addEventListener('click', () => { stato.building = null; passo3(); });
    wizardDialog.querySelector('[data-finish]').addEventListener('click', () => { wizardDialog.close(); navigate('edifici', [b.id]); });
    wizardDialog.querySelector('[data-add-units]').addEventListener('click', () => {
      wizardDialog.close();
      navigate('edifici', [b.id, 'aggiungi', commerciale ? 'crm' : 'censimento']);
    });
  }

  passo1();
  return stato;
}
