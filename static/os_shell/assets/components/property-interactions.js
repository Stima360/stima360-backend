// STIMA360 OS — components/property-interactions.js
// CRM-OPS-4: lo STORICO INTERAZIONI di un immobile (telefonate, incontri,
// note con il proprietario). UN componente per DUE viste - la scheda Immobile
// (tab "Storico interazioni") e la scheda Incarico - sulla STESSA fonte:
//
//   GET  /api/property/properties/{id}/interactions   le righe di `activities`
//                                                     con quel property_id e il
//                                                     catalogo dei tipi (server)
//   POST /api/property/properties/{id}/interactions   una riga nuova: tipo, nota,
//                                                     referente facoltativo,
//                                                     contesto (property|mandate)
//
// Data, ora e autore NON si chiedono: li mette il server (NOW() del database,
// operatore della sessione). Qui si mostrano nel giorno/ora di Roma.
// Nessuna modifica e nessuna cancellazione da qui: il registro e' append-only.
import { apiGet, apiPost } from '../core/api-client.js';
import { escapeHtml } from './st-table.js';

export const INTERACTION_ICONS = { call: '☎', meeting: '👥', note: '📝', email: '✉', whatsapp: '💬' };
/** Le scorciatoie: i tre tipi di tutti i giorni, il resto dal menu del dialog. */
export const QUICK_TYPES = ['call', 'meeting', 'note'];

const GIORNO = new Intl.DateTimeFormat('it-IT', { timeZone: 'Europe/Rome', year: 'numeric', month: '2-digit', day: '2-digit' });
const ORA = new Intl.DateTimeFormat('it-IT', { timeZone: 'Europe/Rome', hour: '2-digit', minute: '2-digit' });
const DATA_LUNGA = new Intl.DateTimeFormat('it-IT', { timeZone: 'Europe/Rome', day: '2-digit', month: 'short', year: 'numeric' });

function chiaveGiorno(d) {
  const p = Object.fromEntries(GIORNO.formatToParts(d).map((x) => [x.type, x.value]));
  return `${p.year}-${p.month}-${p.day}`;
}

/**
 * "Oggi · 16:37", "Ieri · 09:05", altrimenti "02 OTT 2026 · 16:37" - sempre
 * nel giorno e nell'ora di Roma, mai in UTC.
 */
export function formatInteractionTime(value, now = new Date()) {
  if (!value) return '—';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return '—';
  const ora = ORA.format(d);
  const giorno = chiaveGiorno(d);
  if (giorno === chiaveGiorno(now)) return `Oggi · ${ora}`;
  if (giorno === chiaveGiorno(new Date(now.getTime() - 86400000))) return `Ieri · ${ora}`;
  const data = DATA_LUNGA.formatToParts(d)
    .map((p) => (p.type === 'month' ? p.value.replace('.', '').toUpperCase() : p.value)).join('');
  return `${data} · ${ora}`;
}

export function renderTimeline(items, now = new Date()) {
  if (!items || !items.length) {
    return '<p class="muted" id="int-empty">Nessuna interazione registrata. Annota qui telefonate, incontri e note con il proprietario.</p>';
  }
  return `<ol class="int-timeline">${items.map((v) => `
    <li class="int-item" data-interaction-id="${escapeHtml(String(v.id))}" data-type="${escapeHtml(v.interaction_type)}">
      <div class="int-when">${escapeHtml(formatInteractionTime(v.occurred_at, now))}</div>
      <div class="int-type"><span aria-hidden="true">${INTERACTION_ICONS[v.interaction_type] || '•'}</span> ${escapeHtml(v.type_label || v.interaction_type)}${v.context === 'mandate' ? ' <small class="muted">· da Incarico</small>' : ''}</div>
      ${v.contact_name ? `<div class="int-contact">${v.contact_id ? `<a href="#/contatti/${encodeURIComponent(v.contact_id)}">${escapeHtml(v.contact_name)}</a>` : escapeHtml(v.contact_name)}</div>` : ''}
      <p class="int-note">“${escapeHtml(v.note || '')}”</p>
      <div class="int-author muted">${escapeHtml(v.author_name || 'Operatore')}</div>
    </li>`).join('')}</ol>`;
}

/** Il corpo della POST: solo cio' che l'operatore sceglie. */
export function interactionPayload({ type, note, contactId, context }) {
  const corpo = { interaction_type: type, note: String(note || '').trim(), context: context || 'property' };
  if (contactId) corpo.contact_id = Number(contactId);
  return corpo;
}

/**
 * Monta lo storico in `host`. `referents` = i contatti dell'immobile
 * proponibili come referente ({contact_id, display_name}); `context` =
 * 'property' | 'mandate'; `onChange` dopo ogni salvataggio riuscito.
 */
export async function mountPropertyInteractions(host, { propertyId, context = 'property', referents = [], onChange } = {}) {
  host.innerHTML = `
    <div class="int-panel">
      <div class="action-bar int-actions" id="int-actions"></div>
      <div id="int-feedback"></div>
      <div id="int-list"><p class="muted">Caricamento…</p></div>
      <dialog class="modal" id="int-dialog"></dialog>
    </div>`;
  const $ = (sel) => host.querySelector(sel);
  let tipi = [];

  async function carica() {
    try {
      const data = await apiGet(`/api/property/properties/${encodeURIComponent(propertyId)}/interactions?limit=100`);
      tipi = Array.isArray(data?.types) ? data.types : [];
      $('#int-list').innerHTML = renderTimeline(Array.isArray(data?.items) ? data.items : []);
      disegnaAzioni();
      return data;
    } catch (error) {
      $('#int-list').innerHTML = `<div class="error-box">Impossibile caricare lo storico: ${escapeHtml(error.message)}</div>`;
      return null;
    }
  }

  function disegnaAzioni() {
    const etichetta = (v) => (tipi.find((t) => t.value === v) || {}).label || v;
    $('#int-actions').innerHTML = QUICK_TYPES.filter((v) => tipi.some((t) => t.value === v))
      .map((v) => `<button type="button" class="btn ghost" data-quick-type="${escapeHtml(v)}">+ ${escapeHtml(etichetta(v))}</button>`).join('')
      + '<button type="button" class="btn primary" data-quick-type="">+ Registra interazione</button>';
    for (const b of $('#int-actions').querySelectorAll('[data-quick-type]')) {
      b.addEventListener('click', () => apriDialog(b.dataset.quickType || QUICK_TYPES[0]));
    }
  }

  function apriDialog(tipo) {
    const dialogEl = $('#int-dialog');
    dialogEl.innerHTML = `
      <form novalidate class="int-form">
        <h3 class="section-title">Registra interazione</h3>
        <div class="form-field"><label for="int-type">Tipo</label>
          <select id="int-type" class="input">${tipi.map((t) => `<option value="${escapeHtml(t.value)}"${t.value === tipo ? ' selected' : ''}>${escapeHtml(t.label)}</option>`).join('')}</select></div>
        <div class="form-field"><label for="int-contact">Referente</label>
          <select id="int-contact" class="input"><option value="">Nessuno</option>${referents.map((r) => `<option value="${escapeHtml(String(r.contact_id))}">${escapeHtml(r.display_name || `Contatto #${r.contact_id}`)}</option>`).join('')}</select></div>
        <div class="form-field"><label for="int-note">Nota *</label>
          <textarea id="int-note" class="input" rows="4" maxlength="5000" placeholder="Cosa è successo? Es. «Ho chiamato il proprietario: richiamare lunedì»"></textarea></div>
        <p class="muted">Data, ora e autore vengono registrati automaticamente.</p>
        <div class="field-error" id="int-error" role="alert"></div>
        <div class="modal-actions">
          <button type="button" class="btn ghost" id="int-cancel">Chiudi</button>
          <button type="submit" class="btn primary" id="int-save">Salva</button>
        </div>
      </form>`;
    dialogEl.querySelector('#int-cancel').addEventListener('click', () => dialogEl.close());
    dialogEl.querySelector('form').addEventListener('submit', async (evento) => {
      evento.preventDefault();
      const errore = dialogEl.querySelector('#int-error');
      const salva = dialogEl.querySelector('#int-save');
      errore.textContent = '';
      const corpo = interactionPayload({
        type: dialogEl.querySelector('#int-type').value,
        note: dialogEl.querySelector('#int-note').value,
        contactId: dialogEl.querySelector('#int-contact').value,
        context,
      });
      if (!corpo.note) { errore.textContent = 'Scrivi la nota.'; return; }
      salva.disabled = true;
      try {
        await apiPost(`/api/property/properties/${encodeURIComponent(propertyId)}/interactions`, corpo);
      } catch (error) {
        errore.textContent = error.message || 'Salvataggio non riuscito.';
        salva.disabled = false;
        return;
      }
      dialogEl.close();
      $('#int-feedback').innerHTML = '<div class="success-box">Interazione registrata.</div>';
      await carica();
      if (onChange) await onChange();
    });
    dialogEl.showModal();
    dialogEl.querySelector('#int-note').focus();
  }

  return carica();
}
