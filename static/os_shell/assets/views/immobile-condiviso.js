// STIMA360 OS — views/immobile-condiviso.js
// AGENZIE-TERRITORI C2: la scheda di un immobile di UN'ALTRA agenzia letto per
// competenza territoriale (C1, `access.mode === 'territorial'`). Sola lettura:
// nessun salvataggio diretto, nessuna sezione operativa dell'agenzia d'origine.
// Il Supervisore dell'agenzia competente (il backend lo dice con `can_propose`)
// puo' «Proporre una modifica» dei soli campi consentiti; l'immobile cambia
// solo quando il Super Admin approva.
import { escapeHtml, formatDateTime, renderBadge } from '../components/st-table.js';
import { propertyShortLabel } from '../components/property-label.js';
import {
  FIELD_TYPES, changesFrom, errorText, fieldsListHtml, getPropertyRequests, proposeChange, requestSummaryHtml,
  valueText,
} from '../territorial-changes/territorial-changes.js';

const DATI = [
  ['code', 'Codice'], ['property_type', 'Tipologia'], ['commercial_status', 'Stato'], ['asking_price', 'Prezzo richiesto'],
  ['rooms', 'Locali'], ['bedrooms', 'Camere'], ['bathrooms', 'Bagni'], ['floor', 'Piano'], ['total_floors', 'Piani dell’edificio'],
  ['elevator', 'Ascensore'], ['year_built', 'Anno di costruzione'], ['condition', 'Stato di conservazione'],
  ['energy_class', 'Classe energetica'], ['mandate_type', 'Incarico'],
];

/** La scheda in sola lettura (esportata per le prove). */
export function sharedCardHtml(p) {
  const a = p.access || {};
  const luogo = [p.address, p.civic_number].filter(Boolean).join(' ');
  const contatti = Array.isArray(p.contacts) ? p.contacts : [];
  const storico = Array.isArray(p.status_history) ? p.status_history.slice(0, 10) : [];
  return `
    <div class="card panel shared-property" data-shared-property="${Number(p.id)}">
      <a href="#/immobili" class="btn ghost">← Immobili</a>
      <h2>${escapeHtml(p.title || propertyShortLabel(p) || 'Immobile')}</h2>
      <p class="muted">${escapeHtml([luogo, p.city].filter(Boolean).join(', '))}</p>
      <p data-shared-badge>${renderBadge('Condiviso per competenza territoriale', 'info')}
        <span class="muted">Agenzia d’origine: <strong>${escapeHtml(a.origin_agency_name || '')}</strong>
        · Comune: ${escapeHtml((a.territory && a.territory.label) || p.city || '')} · Sola lettura</span></p>
      <dl class="detail-grid">${DATI.map(([k, l]) => `<div class="detail-item"><dt>${escapeHtml(l)}</dt><dd>${escapeHtml(valueText(p[k]))}</dd></div>`).join('')}</dl>
      ${p.public_notes ? `<h3>Descrizione</h3><p>${escapeHtml(p.public_notes)}</p>` : ''}
      <h3>Contatti collegati</h3>
      ${contatti.length ? `<ul class="shared-contacts" data-shared-contacts>${contatti.map((c) => `<li><strong>${escapeHtml(c.display_name || '')}</strong>
        <span class="muted">${escapeHtml(c.role || '')}</span>
        ${c.phone ? ` · <a href="tel:${escapeHtml(c.phone)}">${escapeHtml(c.phone)}</a>` : ''}
        ${c.email ? ` · <a href="mailto:${escapeHtml(c.email)}">${escapeHtml(c.email)}</a>` : ''}</li>`).join('')}</ul>`
        : '<p class="muted">Nessun contatto collegato.</p>'}
      ${storico.length ? `<h3>Cronologia dello stato</h3><ul>${storico.map((s) => `<li>${escapeHtml(formatDateTime(s.created_at))}: ${escapeHtml(s.field_name)} ${escapeHtml(valueText(s.old_value))} → ${escapeHtml(valueText(s.new_value))}</li>`).join('')}</ul>` : ''}
      <section class="tc-section" data-tc-section>
        <h3>Proposte di modifica</h3>
        <div data-tc-list><p class="muted">Caricamento…</p></div>
        <div data-tc-form-host></div>
      </section>
    </div>`;
}

/** Il modulo della proposta: SOLO i campi consentiti dal backend (esportato per le prove). */
export function proposalFormHtml(p, allowed) {
  const campo = ({ field, label }) => {
    const tipo = FIELD_TYPES[field] || 'text';
    const v = p[field];
    const id = `tc-${field}`;
    if (tipo === 'bool') {
      const s = v === true ? 'true' : v === false ? 'false' : '';
      return `<label class="field" for="${id}">${escapeHtml(label)}<select id="${id}" class="input" data-tc-input="${escapeHtml(field)}">
        <option value=""${s === '' ? ' selected' : ''}>Non indicato</option><option value="true"${s === 'true' ? ' selected' : ''}>Sì</option>
        <option value="false"${s === 'false' ? ' selected' : ''}>No</option></select></label>`;
    }
    if (tipo === 'textarea') {
      return `<label class="field" for="${id}">${escapeHtml(label)}<textarea id="${id}" class="input" rows="3" maxlength="5000" data-tc-input="${escapeHtml(field)}">${escapeHtml(v ?? '')}</textarea></label>`;
    }
    const attr = tipo === 'int' ? 'type="number" inputmode="numeric" min="0" step="1"' : 'type="text" maxlength="200"';
    return `<label class="field" for="${id}">${escapeHtml(label)}<input id="${id}" class="input" ${attr} value="${escapeHtml(v ?? '')}" data-tc-input="${escapeHtml(field)}"></label>`;
  };
  return `<form class="tc-form" data-tc-form novalidate>
      <p class="muted">Modifica i campi da correggere. La proposta va al Super Admin: l’immobile resta com’è finché non la approva.</p>
      <div class="form-grid">${(allowed || []).map(campo).join('')}</div>
      <label class="field" for="tc-note">Nota per il Super Admin (facoltativa)<textarea id="tc-note" class="input" rows="2" maxlength="500" data-tc-note></textarea></label>
      <div class="field-error" data-tc-error role="alert"></div>
      <div class="action-bar">
        <button type="submit" class="btn primary" data-tc-submit>Invia proposta</button>
        <button type="button" class="btn ghost" data-tc-cancel>Annulla</button>
      </div>
    </form>`;
}

export async function renderImmobileCondiviso(container, property) {
  container.innerHTML = sharedCardHtml(property);
  const listaEl = container.querySelector('[data-tc-list]');
  const formHost = container.querySelector('[data-tc-form-host]');

  async function carica() {
    let dati;
    try {
      dati = await getPropertyRequests(property.id);
    } catch (error) {
      listaEl.innerHTML = `<div class="error-box">${escapeHtml(errorText(error))}</div>`;
      return;
    }
    const voci = (dati && dati.items) || [];
    listaEl.innerHTML = (voci.length
      ? voci.map((r) => `<article class="trash-card" data-tc-request="${Number(r.id)}">${requestSummaryHtml(r)}${fieldsListHtml(r.fields)}</article>`).join('')
      : '<p class="muted" data-tc-empty>Nessuna proposta.</p>')
      + (dati && dati.can_propose ? '<button type="button" class="btn primary" data-tc-open>Proponi modifica</button>' : '');
    listaEl.querySelector('[data-tc-open]')?.addEventListener('click', () => apriModulo(dati.allowed_fields || []));
  }

  function apriModulo(allowed) {
    const apri = listaEl.querySelector('[data-tc-open]');
    if (apri) apri.hidden = true;
    formHost.innerHTML = proposalFormHtml(property, allowed);
    const form = formHost.querySelector('[data-tc-form]');
    const errore = form.querySelector('[data-tc-error]');
    form.querySelector('[data-tc-cancel]').addEventListener('click', () => { formHost.innerHTML = ''; if (apri) apri.hidden = false; });
    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const valori = {};
      for (const el of Array.from(form.querySelectorAll('[data-tc-input]'))) valori[el.dataset.tcInput] = el.value;
      const changes = changesFrom(valori, property, allowed);
      if (!Object.keys(changes).length) {
        errore.textContent = 'Nessuna modifica rispetto ai dati attuali.';
        return;
      }
      const invia = form.querySelector('[data-tc-submit]');
      invia.disabled = true;
      try {
        await proposeChange(property.id, changes, form.querySelector('[data-tc-note]').value);
        formHost.innerHTML = '';
        await carica();
      } catch (error) {
        errore.textContent = errorText(error);
        invia.disabled = false;
      }
    });
  }

  await carica();
}
