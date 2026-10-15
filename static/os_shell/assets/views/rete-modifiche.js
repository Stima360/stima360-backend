// STIMA360 OS — views/rete-modifiche.js
// AGENZIE-TERRITORI C2: la scheda «Modifiche territoriali» della Rete (Super Admin).
// Separata dalle eliminazioni degli edifici. Elenco per stato, dettaglio con
// Prima / Attuale / Proposto, blocchi calcolati dal backend, «Approva» con
// conferma esplicita, «Rifiuta» con motivo obbligatorio, storico dei passaggi.
// Il backend decide tutto (ruolo riletto nel database a ogni decisione).
import { escapeHtml, formatDateTime } from '../components/st-table.js';
import {
  approveRequest, errorText, fieldsListHtml, getRequest, listRequests, rejectRequest, requestSummaryHtml,
} from '../territorial-changes/territorial-changes.js';

const FILTRI = [
  { value: 'pending', label: 'In attesa' },
  { value: 'approved', label: 'Approvate' },
  { value: 'rejected', label: 'Rifiutate' },
  { value: 'all', label: 'Tutte' },
];
const EVENTI = { proposed: 'Proposta', approved: 'Approvata e applicata', rejected: 'Rifiutata', approval_blocked: 'Approvazione bloccata' };

/** Una riga dell'elenco (esportata per le prove). */
export function requestRowHtml(r) {
  return `
    <article class="trash-card" data-tc-admin-request="${Number(r.id)}">
      <div class="trash-card-head">
        <strong class="trash-code">${escapeHtml(r.property_label)}</strong>
        <span class="muted trash-type">Origine: ${escapeHtml(r.origin_agency_name || '')} · Competente: ${escapeHtml(r.competent_agency_name || '')} · ${escapeHtml(r.territory_label || '')}</span>
      </div>
      ${requestSummaryHtml(r)}
      <div data-tc-admin-detail></div>
      <div class="field-error trash-card-error" data-tc-admin-error role="alert"></div>
      <div class="trash-card-actions">
        <button type="button" class="btn ghost" data-tc-admin-open="${Number(r.id)}">Dettaglio</button>
        ${r.status === 'pending' ? '<span class="muted" data-tc-admin-hint>Apri il dettaglio per approvare o rifiutare.</span>' : ''}
      </div>
    </article>`;
}

/** Il dettaglio: confronto, blocchi, comandi, storico (esportato per le prove). */
export function requestDetailHtml(d) {
  const blocchi = d.blockers && d.blockers.length
    ? `<div class="field-error" data-tc-admin-blockers><strong>Non si può approvare:</strong><ul>${d.blockers.map((b) => `<li data-blocker-code="${escapeHtml(b.code)}">${escapeHtml(b.label)}</li>`).join('')}</ul></div>` : '';
  const comandi = d.status === 'pending' ? `
      <label class="field">Nota (obbligatoria per il rifiuto)
        <textarea data-tc-admin-note maxlength="500" rows="2"></textarea></label>
      <div class="action-bar">
        <button type="button" class="btn primary" data-tc-admin-approve${d.can_approve ? '' : ' disabled'}>Approva</button>
        <button type="button" class="btn danger" data-tc-admin-reject>Rifiuta</button>
      </div>
      <div class="deletion-approve-confirm" data-tc-admin-confirm hidden>
        <p><strong>Confermi l’approvazione?</strong> I campi proposti vengono applicati a ${escapeHtml(d.property_label)} (agenzia d’origine ${escapeHtml(d.origin_agency_name || '')}). Nessun altro dato cambia.</p>
        <div class="action-bar">
          <button type="button" class="btn primary" data-tc-admin-yes>Sì, approva</button>
          <button type="button" class="btn ghost" data-tc-admin-no>Annulla</button>
        </div>
      </div>` : '';
  const storico = (d.events || []).length
    ? `<h4>Storico</h4><ul class="tc-events" data-tc-admin-events>${d.events.map((e) => `<li>${escapeHtml(formatDateTime(e.occurred_at))} · ${escapeHtml(EVENTI[e.action] || e.action)}${e.actor_name ? ` · ${escapeHtml(e.actor_name)}` : ''}</li>`).join('')}</ul>` : '';
  return `<div class="deletion-detail" data-tc-admin-body>
      <p><a href="#/immobili/${Number(d.property_id)}">Immobile #${Number(d.property_id)}</a></p>
      ${fieldsListHtml(d.fields, { withCurrent: true })}${blocchi}${comandi}${storico}</div>`;
}

export async function renderModificheTerritoriali(contentEl) {
  const stato = { filtro: 'pending' };
  contentEl.innerHTML = `
    <section class="tc-admin" data-tc-admin>
      <h3>Modifiche territoriali</h3>
      <p class="muted">Proposte dei Supervisori territoriali su immobili di altre agenzie. Nulla cambia sull’immobile finché non approvi.</p>
      <div class="list-toolbar"><select data-tc-admin-filter aria-label="Stato delle proposte">${FILTRI.map((f) => `<option value="${f.value}">${escapeHtml(f.label)}</option>`).join('')}</select></div>
      <div class="trash-cards" data-tc-admin-list><p class="muted">Caricamento…</p></div>
    </section>`;
  const listaEl = contentEl.querySelector('[data-tc-admin-list]');
  const filtroEl = contentEl.querySelector('[data-tc-admin-filter]');
  filtroEl.addEventListener('change', () => { stato.filtro = filtroEl.value || 'pending'; carica(); });

  async function carica() {
    listaEl.innerHTML = '<p class="muted">Caricamento…</p>';
    let pagina;
    try {
      pagina = await listRequests(stato.filtro);
    } catch (error) {
      listaEl.innerHTML = `<div class="error-box" data-tc-admin-forbidden>${escapeHtml(errorText(error))}</div>`;
      return;
    }
    const voci = (pagina && pagina.items) || [];
    listaEl.innerHTML = voci.length ? voci.map(requestRowHtml).join('') : '<p class="muted" data-tc-admin-empty>Nessuna proposta.</p>';
    for (const card of Array.from(listaEl.querySelectorAll('[data-tc-admin-request]'))) collega(card);
  }

  function collega(card) {
    const id = Number(card.dataset.tcAdminRequest);
    const erroreEl = card.querySelector('[data-tc-admin-error]');
    const dettaglioEl = card.querySelector('[data-tc-admin-detail]');
    card.querySelector('[data-tc-admin-open]').addEventListener('click', async () => {
      erroreEl.textContent = '';
      let d;
      try {
        d = await getRequest(id);
      } catch (error) {
        erroreEl.textContent = errorText(error);
        return;
      }
      dettaglioEl.innerHTML = requestDetailHtml(d);
      const nota = () => (dettaglioEl.querySelector('[data-tc-admin-note]') || {}).value || '';
      const approva = dettaglioEl.querySelector('[data-tc-admin-approve]');
      const conferma = dettaglioEl.querySelector('[data-tc-admin-confirm]');
      approva?.addEventListener('click', () => { approva.disabled = true; if (conferma) conferma.hidden = false; });
      dettaglioEl.querySelector('[data-tc-admin-no]')?.addEventListener('click', () => {
        if (conferma) conferma.hidden = true;
        if (approva) approva.disabled = !d.can_approve;
      });
      const si = dettaglioEl.querySelector('[data-tc-admin-yes]');
      si?.addEventListener('click', async () => {
        si.disabled = true;
        try {
          await approveRequest(id, nota());
          await carica();
        } catch (error) {
          const b = error && error.data && Array.isArray(error.data.blockers) ? error.data.blockers : [];
          erroreEl.innerHTML = escapeHtml(errorText(error)) + (b.length ? `<ul>${b.map((x) => `<li>${escapeHtml(x.label)}</li>`).join('')}</ul>` : '');
          si.disabled = false;
        }
      });
      const rifiuta = dettaglioEl.querySelector('[data-tc-admin-reject]');
      rifiuta?.addEventListener('click', async () => {
        if (!nota().trim()) { erroreEl.textContent = 'Scrivi il motivo del rifiuto.'; return; }
        rifiuta.disabled = true;
        try {
          await rejectRequest(id, nota());
          await carica();
        } catch (error) {
          erroreEl.textContent = errorText(error);
          rifiuta.disabled = false;
        }
      });
    });
  }

  await carica();
}
