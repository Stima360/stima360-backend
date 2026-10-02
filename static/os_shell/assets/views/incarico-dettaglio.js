// STIMA360 OS — incarico-dettaglio.js
// CRM-OPS-4: la scheda INCARICO, vista operativa dell'immobile in incarico.
//
//   GET   /api/property/mandates/{id}                 immobile, proprietari, agente,
//                                                     incarico, scadenza, prezzi,
//                                                     acquisizione d'origine
//   PATCH /api/property/properties/{id}               "Modifica incarico": lo STESSO
//                                                     contratto della scheda Immobile
//                                                     (tipo e date; origine permanente)
//   /api/property/properties/{id}/interactions        lo storico, lo STESSO della
//                                                     scheda Immobile (componente condiviso)
//
// Nessun "Crea incarico": nasce solo dall'acquisizione. Nessuna nota copiata:
// una riga di storico registrata qui la vede anche la scheda Immobile.
import { apiGet, apiPatch } from '../core/api-client.js';
import { renderBadge, escapeHtml, formatDate, formatDateTime } from '../components/st-table.js';
import { mountPropertyInteractions, formatInteractionTime } from '../components/property-interactions.js';
import { COMMERCIAL_STATUS_LABELS, expiryText, expiryTone, formatPrice } from './incarichi.js';

const ROLE_LABELS = { owner: 'Proprietario', seller: 'Venditore' };

function riga(etichetta, valore) {
  return `<div class="detail-item"><label>${escapeHtml(etichetta)}</label>${valore}</div>`;
}

/** Solo i campi davvero cambiati; '' = azzera (come la scheda Immobile). */
export function mandatePatchPayload(inc, valori) {
  const corpo = {};
  const tipo = String(valori.mandate_type || '').trim();
  if (tipo !== (inc.mandate_type || '')) corpo.mandate_type = tipo || null;
  for (const campo of ['mandate_start', 'mandate_end']) {
    const prima = inc[campo] ? String(inc[campo]).slice(0, 10) : '';
    if ((valori[campo] || '') !== prima) corpo[campo] = valori[campo] || null;
  }
  return corpo;
}

export async function renderIncaricoDettaglio(container, params = []) {
  const id = params[0];
  container.innerHTML = '<div class="card panel"><p class="muted">Caricamento…</p></div>';
  let inc;
  let feedback = '';

  async function carica() {
    inc = await apiGet(`/api/property/mandates/${encodeURIComponent(id)}`);
  }

  try {
    await carica();
  } catch (error) {
    container.innerHTML = `<div class="card panel"><p><a href="#/incarichi">← Incarichi</a></p><div class="error-box">Incarico non disponibile: ${escapeHtml(error.message)}</div></div>`;
    return;
  }

  function vistaUltima() {
    const u = inc.last_interaction;
    return u
      ? `${escapeHtml(u.type_label || u.type)} · ${escapeHtml(formatInteractionTime(u.occurred_at))}${u.author_name ? ` · ${escapeHtml(u.author_name)}` : ''}`
      : '<span class="muted">Nessuna interazione registrata</span>';
  }

  function disegna() {
    const acq = inc.acquisition || {};
    const indirizzo = [[inc.address, inc.civic_number].filter(Boolean).join(' '), inc.city].filter(Boolean).join(' · ');
    const principale = (inc.owners || []).find((o) => o.is_main);
    container.innerHTML = `
      <div class="card panel inc-detail">
        <p><a href="#/incarichi">← Incarichi</a></p>
        <div class="inc-head">
          <h2 class="section-title">Incarico · ${escapeHtml(inc.code || `#${inc.property_id}`)}</h2>
          ${renderBadge(COMMERCIAL_STATUS_LABELS[inc.commercial_status] || inc.commercial_status || '—', 'gray')}
          ${renderBadge(expiryText(inc.days_to_expiry), expiryTone(inc.days_to_expiry))}
        </div>
        ${feedback}
        <div class="action-bar inc-actions">
          <a class="btn ghost" id="inc-open-property" href="#/immobili/${encodeURIComponent(inc.property_id)}">Apri immobile</a>
          ${acq.visible ? `<a class="btn ghost" id="inc-open-acquisition" href="#/acquisizioni/${encodeURIComponent(acq.id)}">Apri acquisizione</a>` : ''}
          ${principale ? `<a class="btn ghost" id="inc-open-owner" href="#/contatti/${encodeURIComponent(principale.contact_id)}">Apri proprietario</a>` : ''}
          <button type="button" class="btn ghost" id="inc-edit">Modifica incarico</button>
        </div>

        <h3 class="section-title">Immobile</h3>
        <div class="detail-grid">
          ${riga('Titolo', escapeHtml(inc.title || '—'))}
          ${riga('Indirizzo', escapeHtml(indirizzo || '—'))}
          ${riga('Stato commerciale', escapeHtml(COMMERCIAL_STATUS_LABELS[inc.commercial_status] || inc.commercial_status || '—'))}
        </div>

        <h3 class="section-title">Incarico</h3>
        <div class="detail-grid">
          ${riga('Tipo incarico', escapeHtml(inc.mandate_type || '—'))}
          ${riga('Data inizio', escapeHtml(formatDate(inc.mandate_start)))}
          ${riga('Data scadenza', escapeHtml(formatDate(inc.mandate_end)))}
          ${riga('Giorni residui', escapeHtml(expiryText(inc.days_to_expiry)))}
          ${riga('Agente', escapeHtml(inc.agent_name || '—'))}
          ${riga('Prezzo richiesto', escapeHtml(formatPrice(inc.asking_price)))}
          ${riga('Prezzo minimo', escapeHtml(formatPrice(inc.minimum_price)))}
          ${riga('Prezzo concordato all’incarico', escapeHtml(formatPrice(inc.agreed_price)))}
          ${riga('Valutazione', escapeHtml(formatPrice(inc.valuation_price)))}
          ${riga('Acquisizione di origine', acq.visible ? `<a href="#/acquisizioni/${encodeURIComponent(acq.id)}">#${escapeHtml(String(acq.id))}</a>` : `#${escapeHtml(String(acq.id))} <small class="muted">(di ${escapeHtml(acq.agent_name || 'un altro agente')})</small>`)}
          ${riga('Acquisita il', escapeHtml(formatDateTime(acq.acquired_at)))}
          <div class="detail-item" id="inc-last-interaction"><label>Ultima interazione</label>${vistaUltima()}</div>
        </div>

        <h3 class="section-title">Proprietari</h3>
        ${(inc.owners || []).length ? `<ul class="inc-owners">${inc.owners.map((o) => `
          <li><a href="#/contatti/${encodeURIComponent(o.contact_id)}"><strong>${escapeHtml(o.display_name || `Contatto #${o.contact_id}`)}</strong></a>
            ${o.is_main ? renderBadge('Principale', 'ok') : ''}
            <small class="muted">${escapeHtml((o.roles || []).map((r) => ROLE_LABELS[r] || r).join(', '))}${o.phone ? ` · ${escapeHtml(o.phone)}` : ''}${o.email ? ` · ${escapeHtml(o.email)}` : ''}</small></li>`).join('')}</ul>`
    : '<p class="muted">Nessun proprietario collegato all’immobile.</p>'}

        <h3 class="section-title">Storico interazioni</h3>
        <div id="inc-interactions"></div>
      </div>
      <dialog class="modal" id="inc-edit-dialog"></dialog>`;
    container.querySelector('#inc-edit').addEventListener('click', apriModifica);
    mountPropertyInteractions(container.querySelector('#inc-interactions'), {
      propertyId: inc.property_id,
      context: 'mandate',
      referents: (inc.owners || []).map((o) => ({ contact_id: o.contact_id, display_name: o.display_name })),
      // Dopo una nota: "Ultima interazione" si rilegge dal server.
      onChange: async () => {
        try {
          await carica();
          const campo = container.querySelector('#inc-last-interaction');
          if (campo) campo.innerHTML = `<label>Ultima interazione</label>${vistaUltima()}`;
        } catch (error) {
          // lo storico e' gia' aggiornato; il riepilogo si rilegge al prossimo caricamento
        }
      },
    });
  }

  function apriModifica() {
    const dialogEl = container.querySelector('#inc-edit-dialog');
    dialogEl.innerHTML = `
      <form novalidate>
        <h3 class="section-title">Modifica incarico</h3>
        <p class="muted">L’origine (acquisizione #${escapeHtml(String(inc.acquisition.id))}) resta collegata.</p>
        <div class="form-field"><label for="inc-edit-type">Tipo incarico</label><input id="inc-edit-type" class="input" type="text" maxlength="80" value="${escapeHtml(inc.mandate_type || '')}"></div>
        <div class="form-field"><label for="inc-edit-start">Data inizio</label><input id="inc-edit-start" class="input" type="date" value="${escapeHtml(inc.mandate_start ? String(inc.mandate_start).slice(0, 10) : '')}"></div>
        <div class="form-field"><label for="inc-edit-end">Data scadenza</label><input id="inc-edit-end" class="input" type="date" value="${escapeHtml(inc.mandate_end ? String(inc.mandate_end).slice(0, 10) : '')}"></div>
        <div class="field-error" id="inc-edit-error" role="alert"></div>
        <div class="modal-actions">
          <button type="button" class="btn ghost" id="inc-edit-cancel">Chiudi</button>
          <button type="submit" class="btn primary" id="inc-edit-save">Salva</button>
        </div>
      </form>`;
    dialogEl.querySelector('#inc-edit-cancel').addEventListener('click', () => dialogEl.close());
    dialogEl.querySelector('form').addEventListener('submit', async (evento) => {
      evento.preventDefault();
      const errore = dialogEl.querySelector('#inc-edit-error');
      errore.textContent = '';
      const valori = {
        mandate_type: dialogEl.querySelector('#inc-edit-type').value,
        mandate_start: dialogEl.querySelector('#inc-edit-start').value,
        mandate_end: dialogEl.querySelector('#inc-edit-end').value,
      };
      if (!valori.mandate_type.trim()) { errore.textContent = 'Indica il tipo di incarico.'; return; }
      if (!valori.mandate_start) { errore.textContent = 'Indica la data di inizio.'; return; }
      if (valori.mandate_end && valori.mandate_end < valori.mandate_start) {
        errore.textContent = 'La scadenza non può precedere l’inizio.'; return;
      }
      const corpo = mandatePatchPayload(inc, valori);
      if (!Object.keys(corpo).length) { dialogEl.close(); return; }
      try {
        await apiPatch(`/api/property/properties/${encodeURIComponent(inc.property_id)}`, corpo);
        await carica();
      } catch (error) {
        errore.textContent = error.message || 'Salvataggio non riuscito.';
        return;
      }
      dialogEl.close();
      feedback = '<div class="success-box">Incarico aggiornato.</div>';
      disegna();
    });
    dialogEl.showModal();
  }

  disegna();
}
