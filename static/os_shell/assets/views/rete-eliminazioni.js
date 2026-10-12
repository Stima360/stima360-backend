// STIMA360 OS — views/rete-eliminazioni.js
// ELIMINAZIONE-EDIFICIO-1: la scheda «Eliminazioni edifici» della Rete, del
// Super Admin (amministratore di piattaforma). Due parti:
//
//   * RICHIESTE: quelle da autorizzare (e, a filtro, le decise), da tutte le
//     agenzie; il dettaglio completo (edificio, unità coinvolte, richiedente,
//     blocchi, unità cambiate) e «Approva» / «Rifiuta» (motivo obbligatorio).
//     L'approvazione porta l'impronta delle unità VISTE nel dettaglio: se sono
//     cambiate il backend si ferma (nuova verifica).
//   * CESTINO: gli edifici nel Cestino con le unità raggruppate, «Ripristina»
//     (solo le unità spostate da quell'approvazione) ed «Elimina
//     definitivamente…», che mostra prima il riepilogo e le dipendenze e chiede
//     la seconda conferma con il testo esatto. Nessuna cancellazione automatica.
//
// La difesa vera è nel backend (`require_platform_admin` su ogni rotta, l'account
// designato dalla configurazione del servizio e il ruolo riletto nel database);
// qui non si decide nulla.
import { escapeHtml, formatDateTime, renderBadge } from '../components/st-table.js';
import {
  PURGE_CONFIRM_TEXT, approveDeletion, blockersHtml, deletionErrorText, getDeletionRequest, listDeletionRequests,
  listPlatformTrash, purgeBuilding, purgeCheck, purgeSummaryHtml, rejectDeletion, requestStatusView,
  restoreTrashedBuilding, trashGroupCardHtml, unitsListHtml, unitsSummaryText,
} from '../building-deletion/building-deletion.js';

const FILTRI = [
  { value: 'pending', label: 'Da autorizzare' },
  { value: 'approved', label: 'Approvate' },
  { value: 'rejected', label: 'Rifiutate' },
  { value: 'withdrawn', label: 'Ritirate' },
  { value: 'all', label: 'Tutte' },
];

/** Una riga dell'elenco richieste (esportata per le prove). */
export function requestRowHtml(r) {
  const v = requestStatusView(r);
  const unita = `${Number(r.units_total || 0)} unità${r.units_already_trashed ? `, ${Number(r.units_already_trashed)} già nel Cestino` : ''}`;
  return `
    <article class="trash-card deletion-request" data-deletion-request="${Number(r.id)}">
      <div class="trash-card-head">
        <strong class="trash-code">${escapeHtml(r.building_label)}</strong>
        <span class="muted trash-type">${escapeHtml(r.agency_name || '')}</span>
      </div>
      <div class="badge-row">${renderBadge(v.label, v.tone)}${r.stale ? ` ${renderBadge('Unità cambiate: da riverificare', 'warn')}` : ''}${r.building_exists === false ? ` ${renderBadge('Edificio eliminato definitivamente', 'gray')}` : ''}</div>
      <dl class="trash-facts">
        <dt>Richiesta</dt><dd>${escapeHtml(v.requested)}</dd>
        <dt>Motivo</dt><dd>${escapeHtml(v.reason)}${v.note ? ` — ${escapeHtml(v.note)}` : ''}</dd>
        <dt>Unità</dt><dd>${escapeHtml(unita)}</dd>
        ${v.outcome ? `<dt>Esito</dt><dd>${escapeHtml(v.outcome)}</dd>` : ''}
      </dl>
      <div data-deletion-detail></div>
      <div class="field-error trash-card-error" data-trash-card-error role="alert"></div>
      <div class="trash-card-actions">
        <button type="button" class="btn ghost" data-deletion-detail-open="${Number(r.id)}">Dettaglio</button>
      </div>
    </article>`;
}

/** Il dettaglio completo di una richiesta, con «Approva» / «Rifiuta» se in attesa (esportato per le prove). */
export function requestDetailHtml(d) {
  const r = d.request;
  const b = d.building;
  const edificio = b
    ? `<p><a href="#/edifici/${Number(b.id)}">${escapeHtml(b.label)}</a> <span class="muted">· ${escapeHtml(r.agency_name || '')}${b.deleted_at ? ` · nel Cestino dal ${escapeHtml(formatDateTime(b.deleted_at))}` : ''}</span></p>`
    : '<p class="muted">L’edificio non esiste più (eliminato definitivamente).</p>';
  const unita = d.units ? `<p>${escapeHtml(unitsSummaryText(d.units))}</p>${unitsListHtml(d.units)}` : '';
  const cambiate = d.stale
    ? '<p class="field-error" data-deletion-stale>Le unità sono cambiate dalla richiesta: non si può approvare. Rifiutala, oppure il richiedente la ritira e la richiede di nuovo.</p>' : '';
  const blocchi = d.blockers && d.blockers.length
    ? `<p><strong>Blocchi:</strong> finché ci sono non si può approvare.</p>${blockersHtml(d.blockers)}` : '';
  const azioni = r.status === 'pending' ? `
      <label class="field">Nota (obbligatoria per il rifiuto)
        <textarea data-deletion-decision-note maxlength="500" rows="2"></textarea></label>
      <p class="muted">Approvando, l’edificio e le sue unità attive vanno insieme nel Cestino; quelle già nel Cestino restano com’erano. Nulla viene cancellato definitivamente.</p>
      <div class="action-bar">
        <button type="button" class="btn primary" data-deletion-approve${d.can_approve ? '' : ' disabled'}>Approva</button>
        <button type="button" class="btn danger" data-deletion-reject>Rifiuta</button>
      </div>` : '';
  return `<div class="deletion-detail" data-deletion-detail-body>${edificio}${unita}${cambiate}${blocchi}${azioni}</div>`;
}

export async function renderEliminazioni(contentEl) {
  const stato = { filtro: 'pending' };
  contentEl.innerHTML = `
    <section class="deletion-admin" data-deletion-admin>
      <h3>Richieste di eliminazione degli edifici</h3>
      <div class="list-toolbar">
        <select data-deletion-filter>${FILTRI.map((f) => `<option value="${f.value}">${escapeHtml(f.label)}</option>`).join('')}</select>
      </div>
      <div class="trash-cards" data-deletion-requests><p class="muted">Caricamento…</p></div>
      <h3>Edifici nel Cestino</h3>
      <p class="muted">Ogni edificio con le sue unità. «Ripristina» riporta l’edificio e le unità spostate dalla sua approvazione; «Elimina definitivamente» chiede il riepilogo e una seconda conferma.</p>
      <div class="trash-cards" data-deletion-trash><p class="muted">Caricamento…</p></div>
    </section>`;
  const richiesteEl = contentEl.querySelector('[data-deletion-requests]');
  const cestinoEl = contentEl.querySelector('[data-deletion-trash]');
  const filtroEl = contentEl.querySelector('[data-deletion-filter]');
  filtroEl.addEventListener('change', () => {
    stato.filtro = filtroEl.value || 'pending';
    caricaRichieste();
  });

  // ELIMINAZIONE-EDIFICIO-2: un amministratore di piattaforma che non è il Super
  // Admin generale designato (o un servizio senza configurazione) riceve 403:
  // un solo avviso al posto della scheda, nessun comando.
  function vietato(error) {
    if (!error || error.status !== 403) return false;
    const sezione = contentEl.querySelector('[data-deletion-admin]');
    if (sezione) {
      sezione.innerHTML = `<div class="error-box" data-deletion-forbidden>${escapeHtml(deletionErrorText(error))}</div>`;
    }
    return true;
  }

  async function caricaRichieste() {
    richiesteEl.innerHTML = '<p class="muted">Caricamento…</p>';
    let pagina;
    try {
      pagina = await listDeletionRequests(stato.filtro);
    } catch (error) {
      if (vietato(error)) return;
      richiesteEl.innerHTML = `<div class="error-box">${escapeHtml(deletionErrorText(error))}</div>`;
      return;
    }
    const voci = (pagina && pagina.items) || [];
    richiesteEl.innerHTML = voci.length ? voci.map(requestRowHtml).join('')
      : '<p class="muted" data-deletion-empty>Nessuna richiesta.</p>';
    for (const card of Array.from(richiesteEl.querySelectorAll('[data-deletion-request]'))) collegaRichiesta(card);
  }

  function collegaRichiesta(card) {
    const id = Number(card.dataset.deletionRequest);
    const erroreEl = card.querySelector('[data-trash-card-error]');
    const dettaglioEl = card.querySelector('[data-deletion-detail]');
    card.querySelector('[data-deletion-detail-open]').addEventListener('click', async () => {
      erroreEl.textContent = '';
      let d;
      try {
        d = await getDeletionRequest(id);
      } catch (error) {
        erroreEl.textContent = deletionErrorText(error);
        return;
      }
      dettaglioEl.innerHTML = requestDetailHtml(d);
      const nota = () => (dettaglioEl.querySelector('[data-deletion-decision-note]') || {}).value || '';
      const approva = dettaglioEl.querySelector('[data-deletion-approve]');
      const rifiuta = dettaglioEl.querySelector('[data-deletion-reject]');
      approva?.addEventListener('click', async () => {
        approva.disabled = true;
        try {
          await approveDeletion(id, d.fingerprint, nota());
          await Promise.all([caricaRichieste(), caricaCestino()]);
        } catch (error) {
          erroreEl.innerHTML = escapeHtml(deletionErrorText(error))
            + (error && error.data && Array.isArray(error.data.blockers) ? blockersHtml(error.data.blockers) : '');
          approva.disabled = false;
        }
      });
      rifiuta?.addEventListener('click', async () => {
        if (!nota().trim()) {
          erroreEl.textContent = 'Scrivi il motivo del rifiuto.';
          return;
        }
        rifiuta.disabled = true;
        try {
          await rejectDeletion(id, nota());
          await caricaRichieste();
        } catch (error) {
          erroreEl.textContent = deletionErrorText(error);
          rifiuta.disabled = false;
        }
      });
    });
  }

  async function caricaCestino() {
    let pagina;
    try {
      pagina = await listPlatformTrash();
    } catch (error) {
      if (vietato(error)) return;
      cestinoEl.innerHTML = `<div class="error-box">${escapeHtml(deletionErrorText(error))}</div>`;
      return;
    }
    const voci = (pagina && pagina.items) || [];
    cestinoEl.innerHTML = voci.length ? voci.map((x) => trashGroupCardHtml(x, { superAdmin: true })).join('')
      : '<p class="muted" data-trash-empty>Nessun edificio nel Cestino.</p>';
    for (const card of Array.from(cestinoEl.querySelectorAll('[data-building-group]'))) collegaGruppo(card);
  }

  function collegaGruppo(card) {
    const id = Number(card.dataset.buildingGroup);
    const erroreEl = card.querySelector('[data-trash-card-error]');
    const purgeEl = card.querySelector('[data-group-purge-host]');
    const ripristina = card.querySelector('[data-group-restore]');
    ripristina?.addEventListener('click', async () => {
      ripristina.disabled = true;
      erroreEl.textContent = '';
      try {
        await restoreTrashedBuilding(id);
        await Promise.all([caricaCestino(), caricaRichieste()]);
      } catch (error) {
        erroreEl.textContent = deletionErrorText(error);
        ripristina.disabled = false;
      }
    });
    card.querySelector('[data-group-purge]')?.addEventListener('click', async () => {
      erroreEl.textContent = '';
      let check;
      try {
        check = await purgeCheck(id);
      } catch (error) {
        erroreEl.textContent = deletionErrorText(error);
        return;
      }
      purgeEl.innerHTML = purgeSummaryHtml(check);
      const testo = purgeEl.querySelector('[data-purge-text]');
      const conferma = purgeEl.querySelector('[data-purge-confirm]');
      purgeEl.querySelector('[data-purge-cancel]')?.addEventListener('click', () => { purgeEl.innerHTML = ''; });
      testo?.addEventListener('input', () => { conferma.disabled = testo.value.trim() !== PURGE_CONFIRM_TEXT; });
      conferma?.addEventListener('click', async () => {
        conferma.disabled = true;
        try {
          await purgeBuilding(id, testo.value, check.fingerprint);
          await Promise.all([caricaCestino(), caricaRichieste()]);
        } catch (error) {
          erroreEl.innerHTML = escapeHtml(deletionErrorText(error))
            + (error && error.data && Array.isArray(error.data.blockers) ? blockersHtml(error.data.blockers) : '');
          conferma.disabled = false;
        }
      });
    });
  }

  await Promise.all([caricaRichieste(), caricaCestino()]);
}
