// STIMA360 OS — sellers/seller-toggle.js (VENDITORI-1)
//
// L'interruttore «Vende» nella tab Proprietari della scheda immobile:
//
//   Mario Rossi — Proprietario — [ Vende ]
//
// Per ogni proprietario (owner|seller, una volta per contatto) una cella con lo
// stato letto da `property.leads` e l'azione giusta:
//   nessuna opportunita' -> «Vende?»       -> POST /api/crm/sellers
//   aperta               -> «Vende ✓»       -> «Apri in Venditori» · «Smetti…»
//   sospesa              -> «Sospesa»       -> «Riprendi»
//   chiusa               -> «Non vende»     -> «Riattiva»
//   inserita per errore  -> «Inserito per errore» -> «Vende?» (DELETE-ARCH 1B:
//                           mai mostrata come una chiusura «Non vende»)
//
// Casi espliciti (mai una scorciatoia):
//   * censimento: dialog «Mario vuole vendere questo immobile. Per lavorarlo
//     nel CRM dobbiamo prima prenderlo in carico.» -> «Prendi in carico e
//     continua» = DUE passi: la presa in carico ESISTENTE (census-api, stessa
//     riga, stesso codice), poi l'attivazione. Se il secondo passo non riesce
//     l'immobile resta preso in carico e il messaggio lo dice;
//   * lead ambigui: l'elenco dei candidati e «Crea un lead nuovo»: la scelta e'
//     dell'operatore, il server non indovina;
//   * Smetti: Sospesa / Non vende più / Inserito per errore, con nota: nulla si
//     cancella.

import { escapeHtml, renderBadge } from '../components/st-table.js';
import { navigate } from '../core/router.js';
import { takeInCharge } from '../census/census-api.js';
import * as api from './sellers-api.js';
import { sellableOwners, sellerErrorMessage, sellerState, stageLabel } from './seller-model.js';

/** La cella «Vendita» di una riga property_contacts (vuota per i ruoli che
 *  non vendono e per le righe ripetute dello stesso contatto). */
export function renderSellerCell(row, property) {
  const owners = sellableOwners(property.contacts);
  const primo = owners.find((o) => String(o.contact_id) === String(row.contact_id));
  if (!primo || primo !== row) return '';
  const s = sellerState(property.leads, row.contact_id);
  const cid = escapeHtml(String(row.contact_id));
  if (s.state === 'open') {
    return `<span class="seller-cell" data-seller-state="open">${renderBadge('Vende', 'ok')} <small class="muted">${escapeHtml(stageLabel(s.stage))}</small>
      <button type="button" class="btn ghost btn-small" data-seller-open="${cid}">Venditori</button>
      <button type="button" class="btn ghost btn-small" data-seller-stop="${cid}">Smetti…</button></span>`;
  }
  if (s.state === 'paused') {
    return `<span class="seller-cell" data-seller-state="paused">${renderBadge('Vendita sospesa', 'warn')}
      <button type="button" class="btn btn-small" data-seller-on="${cid}">Riprendi</button></span>`;
  }
  if (s.state === 'mistake') {
    return `<span class="seller-cell" data-seller-state="mistake">${renderBadge('Inserito per errore', 'gray')}
      <button type="button" class="btn ghost btn-small" data-seller-on="${cid}">Vende?</button></span>`;
  }
  if (s.state === 'closed') {
    return `<span class="seller-cell" data-seller-state="closed">${renderBadge('Non vende', 'gray')}
      <button type="button" class="btn ghost btn-small" data-seller-on="${cid}">Riattiva</button></span>`;
  }
  return `<span class="seller-cell" data-seller-state="none"><button type="button" class="btn btn-small seller-toggle" data-seller-on="${cid}" aria-pressed="false">Vende?</button></span>`;
}

function nomeDi(property, contactId) {
  const c = (property.contacts || []).find((x) => String(x.contact_id) === String(contactId));
  return (c && c.display_name) || `Contatto #${contactId}`;
}

function dialogo(host) {
  let el = host.querySelector('#seller-dialog');
  if (!el) {
    el = document.createElement('dialog');
    el.id = 'seller-dialog';
    el.className = 'modal seller-sheet';
    host.appendChild(el);
  }
  return el;
}

/**
 * @param {HTMLElement} panelEl  la tab Proprietari gia' disegnata
 * @param {{property: object, host: HTMLElement, onChanged: Function, onRerender: Function}} ctx
 *   onChanged(messaggio)  ricarica contatti+lead e ridisegna la tab
 *   onRerender()          ridisegna la scheda intera (dopo la presa in carico)
 */
export function bindSellerToggles(panelEl, { property, host, onChanged, onRerender }) {
  const el = dialogo(host);
  const feedback = (testo, tono = 'error') => {
    const fb = panelEl.querySelector('#proprietari-feedback');
    if (fb) fb.innerHTML = `<div class="${tono === 'ok' ? 'success-box' : 'error-box'}">${escapeHtml(testo)}</div>`;
  };

  async function attiva(contactId, scelta = {}) {
    try {
      const esito = await api.activateSeller(property.id, contactId, scelta);
      await onChanged(esito.reopened ? 'Vendita riattivata.' : `${nomeDi(property, contactId)} ora è tra i Venditori.`);
      return esito;
    } catch (error) {
      if (error.code === 'PROPERTY_IN_CENSUS') return apriCensimento(contactId);
      if (error.code === 'SELLER_LEAD_AMBIGUOUS') return apriScelta(contactId, error.candidates);
      feedback(sellerErrorMessage(error));
      return null;
    }
  }

  function apriCensimento(contactId) {
    const nome = nomeDi(property, contactId);
    el.innerHTML = `<form data-seller-census novalidate>
        <h3 class="section-title">Prendi in carico per vendere</h3>
        <p>${escapeHtml(nome)} vuole vendere questo immobile. Per lavorarlo nel CRM dobbiamo prima prenderlo in carico.</p>
        <p class="muted">Stessa scheda, stesso codice: l’immobile passa dal censimento al lavoro commerciale (con le sue pertinenze collegate), poi ${escapeHtml(nome)} entra tra i Venditori.</p>
        <div class="field-error" data-error role="alert"></div>
        <div class="modal-actions"><button type="button" class="btn ghost" data-cancel>Annulla</button>
        <button type="submit" class="btn primary" data-submit>Prendi in carico e continua</button></div></form>`;
    if (!el.open) el.showModal();
    el.querySelector('[data-cancel]').addEventListener('click', () => el.close());
    el.querySelector('[data-seller-census]').addEventListener('submit', async (evento) => {
      evento.preventDefault();
      const bottone = el.querySelector('[data-submit]');
      bottone.disabled = true;
      bottone.textContent = 'Presa in carico…';
      try {
        await takeInCharge(property.id, true);                     // passo 1: il flusso esistente
      } catch (error) {
        bottone.disabled = false;
        bottone.textContent = 'Prendi in carico e continua';
        el.querySelector('[data-error]').textContent = sellerErrorMessage(error);
        return;
      }
      property.record_kind = 'crm';
      try {
        await api.activateSeller(property.id, contactId);         // passo 2: Vende
        el.close();
        await onRerender(`Immobile preso in carico: ${nome} ora è tra i Venditori.`);
      } catch (error) {
        el.close();
        await onRerender(`Immobile preso in carico. Vende non attivato: ${sellerErrorMessage(error)}`);
      }
    });
  }

  function apriScelta(contactId, candidati) {
    const righe = (candidati || []).map((c, i) => `<label class="seller-choice"><input type="radio" name="seller-lead" value="${escapeHtml(String(c.id))}" ${i === 0 ? 'checked' : ''}>
        <span><strong>Lead #${escapeHtml(String(c.id))}</strong> <small class="muted">${escapeHtml([c.source === 'public_stima' ? 'Stima dal sito' : (c.source || ''), stageLabel(c.stage), c.created_at ? new Date(c.created_at).toLocaleDateString('it-IT') : ''].filter(Boolean).join(' · '))}</small></span></label>`).join('');
    el.innerHTML = `<form data-seller-choice novalidate>
        <h3 class="section-title">Quale lead collegare?</h3>
        <p class="muted">${escapeHtml(nomeDi(property, contactId))} ha più lead venditore senza immobile. Scegli quello che riguarda questo immobile, oppure creane uno nuovo.</p>
        ${righe}
        <label class="seller-choice"><input type="radio" name="seller-lead" value="new"><span><strong>Crea un lead nuovo</strong></span></label>
        <div class="modal-actions"><button type="button" class="btn ghost" data-cancel>Annulla</button>
        <button type="submit" class="btn primary">Collega</button></div></form>`;
    if (!el.open) el.showModal();
    el.querySelector('[data-cancel]').addEventListener('click', () => el.close());
    el.querySelector('[data-seller-choice]').addEventListener('submit', async (evento) => {
      evento.preventDefault();
      const scelto = [...el.querySelectorAll('input[name="seller-lead"]')].find((r) => r.checked);
      if (!scelto) return;
      el.close();
      await attiva(contactId, scelto.value === 'new' ? { newLead: true } : { leadId: Number(scelto.value) });
    });
  }

  function apriSmetti(contactId) {
    el.innerHTML = `<form data-seller-stop-form novalidate>
        <h3 class="section-title">${escapeHtml(nomeDi(property, contactId))} non vende più?</h3>
        <p class="muted">Lo storico (telefonate, note, richiami) resta: cambia solo lo stato dell’opportunità.</p>
        <label class="seller-choice"><input type="radio" name="seller-outcome" value="paused" checked><span><strong>Vendita sospesa</strong> <small class="muted">ne riparliamo più avanti</small></span></label>
        <label class="seller-choice"><input type="radio" name="seller-outcome" value="not_selling"><span><strong>Non vende più</strong></span></label>
        <label class="seller-choice"><input type="radio" name="seller-outcome" value="mistake"><span><strong>Inserito per errore</strong></span></label>
        <div class="form-field"><label for="seller-stop-note">Nota (facoltativa)</label><input id="seller-stop-note" class="input" maxlength="500"></div>
        <div class="field-error" data-error role="alert"></div>
        <div class="modal-actions"><button type="button" class="btn ghost" data-cancel>Annulla</button>
        <button type="submit" class="btn primary">Conferma</button></div></form>`;
    if (!el.open) el.showModal();
    el.querySelector('[data-cancel]').addEventListener('click', () => el.close());
    el.querySelector('[data-seller-stop-form]').addEventListener('submit', async (evento) => {
      evento.preventDefault();
      const radio = [...el.querySelectorAll('input[name="seller-outcome"]')].find((r) => r.checked);
      try {
        await api.deactivateSeller(property.id, contactId, radio ? radio.value : 'paused', el.querySelector('#seller-stop-note').value);
        el.close();
        await onChanged('Stato aggiornato: lo storico resta.');
      } catch (error) {
        el.querySelector('[data-error]').textContent = sellerErrorMessage(error);
      }
    });
  }

  panelEl.querySelectorAll('[data-seller-on]').forEach((b) => b.addEventListener('click', async () => {
    b.disabled = true;
    await attiva(b.dataset.sellerOn);
    b.disabled = false;
  }));
  panelEl.querySelectorAll('[data-seller-stop]').forEach((b) => b.addEventListener('click', () => apriSmetti(b.dataset.sellerStop)));
  panelEl.querySelectorAll('[data-seller-open]').forEach((b) => b.addEventListener('click', () => navigate('venditori', [])));
}
